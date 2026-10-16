"""Local project conversations and durable, idempotent browser turns; no inference."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
import uuid
from urllib.parse import urlsplit


def conversation_url(value):
    u = urlsplit(value or '')
    if (u.scheme != 'https' or u.netloc != 'chatgpt.com'
            or not re.fullmatch(r'/c/[a-zA-Z0-9-]+/?', u.path)
            or u.query or u.fragment):
        raise ValueError('不是本项目可绑定的 GPT 对话。')
    return 'https://chatgpt.com' + u.path.rstrip('/')


class ProjectStore:
    def __init__(self, root):
        self.root = Path(root)
        self.path = self.root / 'data/projects.sqlite3'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS projects(
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, stage TEXT NOT NULL,
                    refs TEXT NOT NULL, conversation TEXT NOT NULL DEFAULT '',
                    reply_draft TEXT NOT NULL DEFAULT '', created REAL, updated REAL);
                CREATE TABLE IF NOT EXISTS project_meta(key TEXT PRIMARY KEY,value TEXT);
                CREATE TABLE IF NOT EXISTS turns(
                    id TEXT PRIMARY KEY, project TEXT NOT NULL, packet TEXT NOT NULL,
                    prompt_hash TEXT NOT NULL, status TEXT NOT NULL, lease TEXT,
                    lease_until REAL NOT NULL DEFAULT 0, result TEXT, error TEXT,
                    created REAL, updated REAL);
                CREATE TABLE IF NOT EXISTS messages(
                    id INTEGER PRIMARY KEY, project TEXT NOT NULL, role TEXT NOT NULL,
                    text TEXT NOT NULL, turn TEXT NOT NULL, created REAL,
                    UNIQUE(project,role,turn));
            ''')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def active(self):
        with self.db() as db:
            row = db.execute("SELECT value FROM project_meta WHERE key='active'").fetchone()
            return self.get(row['value']) if row else None

    def get(self, project_id):
        with self.db() as db:
            row = db.execute('SELECT * FROM projects WHERE id=?', (project_id,)).fetchone()
            if row is None:
                raise ValueError('项目不存在。')
            item = dict(row)
            item['refs'] = json.loads(item['refs'])
            return item

    def start(self, refs, name=None, force=False):
        refs = list(dict.fromkeys(str(Path(p).resolve()) for p in refs))
        old = self.active()
        if not force:
            if old and refs and old['refs'] == refs:
                return old
            found = self._matching(refs)
            if found:
                self.activate(found)
                return self.get(found)
        project_id = str(uuid.uuid4())
        name = name or next((Path(p).stem for p in refs if Path(p).suffix.lower() in ('.md', '.txt')), '视频项目')
        now = time.time()
        with self.db() as db:
            db.execute('INSERT INTO projects(id,name,stage,refs,created,updated) VALUES(?,?,?,?,?,?)',
                       (project_id, name[:120], 'story_discussion', json.dumps(refs, ensure_ascii=False), now, now))
            db.execute("INSERT OR REPLACE INTO project_meta VALUES('active',?)", (project_id,))
        return self.get(project_id)

    def _matching(self, refs):
        # 空材料不匹配任何项目，避免误恢复其他作品。
        if not refs:
            return None
        with self.db() as db:
            rows = db.execute('SELECT id,refs FROM projects ORDER BY updated DESC, created DESC, id').fetchall()
        for row in rows:
            if json.loads(row['refs']) == refs:
                return row['id']
        return None

    def list_projects(self):
        with self.db() as db:
            return [dict(row) for row in db.execute(
                'SELECT id,name,stage,updated FROM projects ORDER BY updated DESC, created DESC, id')]

    def activate(self, project_id):
        self.get(project_id)
        with self.db() as db:
            db.execute("INSERT OR REPLACE INTO project_meta VALUES('active',?)", (project_id,))
            db.execute('UPDATE projects SET updated=? WHERE id=?', (time.time(), project_id))
        return self.get(project_id)

    def messages(self, project_id):
        with self.db() as db:
            return [dict(row) for row in db.execute('SELECT * FROM messages WHERE project=? ORDER BY id', (project_id,))]

    def draft(self, project_id, text):
        if not isinstance(text, str) or len(text) > 16000:
            raise ValueError('回复过长，原文保留。')
        with self.db() as db:
            db.execute('UPDATE projects SET reply_draft=?,updated=? WHERE id=?', (text, time.time(), project_id))

    def phase(self, project_id, stage):
        if stage not in ('story_discussion', 'storyboard'):
            raise ValueError('本版尚未开放这个制作步骤。')
        with self.db() as db:
            db.execute('UPDATE projects SET stage=?,updated=? WHERE id=?', (stage, time.time(), project_id))

    def pending(self, project_id):
        with self.db() as db:
            row = db.execute("SELECT * FROM turns WHERE project=? AND status IN ('queued','working','attention') ORDER BY created DESC LIMIT 1", (project_id,)).fetchone()
            return dict(row) if row else None

    def queue(self, project_id, packet, visible, turn_id=None, clear_draft=False):
        if not isinstance(visible, str) or not visible.strip() or len(visible) > 16000:
            raise ValueError('请先说出或写下本次回复。')
        project = self.get(project_id)
        turn_id = turn_id or 'native-' + uuid.uuid4().hex
        packet = json.loads(json.dumps(packet, ensure_ascii=False))
        # A unique turn marker prevents identical replies matching an older turn.
        packet['prompt'] = f'【手札本轮标记 {turn_id}，只用于对应本次回复，请勿在回答中输出】\n' + packet['prompt']
        if len(packet['prompt']) > 64000:
            raise ValueError('本次材料过长，原稿保留，未提交。')
        digest = hashlib.sha256(packet['prompt'].encode('utf-8')).hexdigest()
        packet['nativeSource'] = dict(projectId=project_id, turnId=turn_id,
                                      conversationUrl=project['conversation'] if packet['mode'] == 'video_sop' else '',
                                      promptHash=digest)
        now = time.time()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM turns WHERE project=? AND status IN ('queued','working','attention')", (project_id,)).fetchone():
                raise ValueError('上一条任务仍在处理，请等待回复或重新检查。')
            db.execute('INSERT INTO turns(id,project,packet,prompt_hash,status,created,updated) VALUES(?,?,?,?,?,?,?)',
                       (turn_id, project_id, json.dumps(packet, ensure_ascii=False), digest, 'queued', now, now))
            db.execute('INSERT INTO messages(project,role,text,turn,created) VALUES(?,?,?,?,?)',
                       (project_id, 'user', visible, turn_id, now))
            if clear_draft:
                db.execute("UPDATE projects SET reply_draft='',updated=? WHERE id=?", (now, project_id))
        return turn_id

    def claim(self, now=None):
        now = time.time() if now is None else now
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT * FROM turns WHERE status='queued' OR (status='working' AND lease_until<?) ORDER BY created LIMIT 1", (now,)).fetchone()
            if row is None:
                return None
            lease = uuid.uuid4().hex
            db.execute("UPDATE turns SET status='working',lease=?,lease_until=?,updated=? WHERE id=?", (lease, now + 120, now, row['id']))
            return dict(id=row['id'], lease=lease, packet=json.loads(row['packet']))

    def update(self, turn_id, lease, event):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM turns WHERE id=?', (turn_id,)).fetchone()
            if row is None or row['lease'] != lease:
                raise ValueError('本次回复与已领取的任务不一致。')
            kind = event.get('kind')
            if row['status'] == 'done':
                if kind == 'reply' and json.loads(row['result']) == event:
                    return  # Idempotent acknowledgement after a lost response.
                raise ValueError('本次任务已完成，未覆盖结果。')
            if kind == 'reply':
                if event.get('promptHash') != row['prompt_hash']:
                    raise ValueError('回复对应的提示词不一致，未采用。')
                url = conversation_url(event.get('conversationUrl'))
                text = event.get('text')
                if not isinstance(text, str) or not 1 <= len(text.strip()) <= 32000:
                    raise ValueError('未取得完整有效的 GPT 回复。')
                db.execute('INSERT OR IGNORE INTO messages(project,role,text,turn,created) VALUES(?,?,?,?,?)',
                           (row['project'], 'assistant', text, turn_id, time.time()))
                packet = json.loads(row['packet'])
                if packet['mode'] == 'video_sop':
                    old_url = db.execute('SELECT conversation FROM projects WHERE id=?', (row['project'],)).fetchone()[0]
                    if old_url and old_url != url:
                        raise ValueError('项目讨论对话发生变化，未采用其他对话的回复。')
                    db.execute('UPDATE projects SET conversation=?,updated=? WHERE id=?', (url, time.time(), row['project']))
                db.execute("UPDATE turns SET status='done',result=?,error=NULL,updated=? WHERE id=?", (json.dumps(event, ensure_ascii=False), time.time(), turn_id))
            elif kind == 'error':
                message = str(event.get('message') or '浏览器任务未完成。')[:600]
                db.execute("UPDATE turns SET status='attention',error=?,updated=? WHERE id=?", (message, time.time(), turn_id))
            elif kind == 'progress':
                db.execute('UPDATE turns SET lease_until=?,updated=? WHERE id=?', (time.time()+120, time.time(), turn_id))
            else:
                raise ValueError('不支持的任务回执。')

    def retry(self, turn_id):
        with self.db() as db:
            db.execute("UPDATE turns SET status='queued',lease=NULL,lease_until=0,error=NULL,updated=? WHERE id=? AND status='attention'", (time.time(), turn_id))
