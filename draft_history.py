"""Local, append-only draft revisions. Recovery never destroys the current draft."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3
from draft_store import validate_draft


class DraftHistory:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=2)
        self.db.row_factory = sqlite3.Row
        self.db.execute('''CREATE TABLE IF NOT EXISTS drafts(
            id INTEGER PRIMARY KEY, profile TEXT NOT NULL, created TEXT NOT NULL,
            reason TEXT NOT NULL, digest TEXT NOT NULL, payload TEXT NOT NULL,
            parent INTEGER, UNIQUE(profile,digest))''')
        columns = {row[1] for row in self.db.execute('PRAGMA table_info(drafts)')}
        for name, kind in (('preview','TEXT'),('characters','INTEGER'),('refs','INTEGER')):
            if name not in columns:
                self.db.execute(f'ALTER TABLE drafts ADD COLUMN {name} {kind}')
        self.db.execute('CREATE INDEX IF NOT EXISTS draft_profile_id ON drafts(profile,id DESC)')
        # One-time migration. Subsequent history lists read only small metadata.
        for row in self.db.execute('SELECT id,payload FROM drafts WHERE preview IS NULL'):
            data = validate_draft(json.loads(row['payload']))
            self.db.execute('UPDATE drafts SET preview=?,characters=?,refs=? WHERE id=?',
                            (*self._metadata(data),row['id']))
        self.db.commit()
        self.documents = self.path.parent / '草稿留存'
        self.document_error = None

    @staticmethod
    def _metadata(data):
        text = data['dictation'] or data['workflow']
        return text[:200].replace('\n',' ')[:100], len(text), len(data['refs'])

    def archive(self, data, profile, reason='manual', parent=None):
        draft = validate_draft(data)
        if not any(draft.values()):
            return None
        payload = json.dumps(draft, ensure_ascii=False, sort_keys=True)
        digest = hashlib.sha256(payload.encode('utf-8')).hexdigest()
        with self.db:
            self.db.execute('''INSERT OR IGNORE INTO drafts(profile,created,reason,digest,payload,parent,preview,characters,refs)
                VALUES(?,?,?,?,?,?,?,?,?)''', (profile, datetime.now().astimezone().isoformat(timespec='seconds'),
                                        reason, digest, payload, parent,*self._metadata(draft)))
        key = self.db.execute('SELECT id FROM drafts WHERE profile=? AND digest=?', (profile,digest)).fetchone()[0]
        try:
            self.export(key, profile)
            self.document_error = None
        except OSError as exc:
            # The authoritative archive is already durable. A failed readable
            # copy must not masquerade as lost recovery or block dictation.
            self.document_error = type(exc).__name__
        return key

    def rows(self, profile, limit=100, offset=0, search=''):
        result = []
        where='profile=?';args=[profile]
        if search.strip():
            where+=' AND instr(payload,?)>0';args.append(search.strip()[:200])
        for row in self.db.execute('''SELECT id,created,reason,preview,characters,refs,parent FROM drafts
                WHERE '''+where+''' ORDER BY id DESC LIMIT ? OFFSET ?''', (*args,min(100,max(1,limit)),max(0,offset))):
            item = dict(row)
            result.append(item)
        return result

    def count(self,profile,search=''):
        if search.strip():
            return self.db.execute('SELECT count(*) FROM drafts WHERE profile=? AND instr(payload,?)>0',(profile,search.strip()[:200])).fetchone()[0]
        return self.db.execute('SELECT count(*) FROM drafts WHERE profile=?',(profile,)).fetchone()[0]

    def export(self, key, profile):
        from draft_documents import write_revision
        row = self.db.execute('SELECT created,payload FROM drafts WHERE id=? AND profile=?', (key,profile)).fetchone()
        if row is None:
            raise ValueError('找不到当前词库的这份草稿')
        return write_revision(self.documents, profile, key, row['created'], json.loads(row['payload']))

    def folder(self, profile):
        from draft_documents import profile_folder
        return profile_folder(self.documents, profile)

    def export_existing(self, profile):
        # Normally ten small legacy records; invoked once when opening their
        # folder, never on the 25-ms UI polling path.
        for row in self.db.execute('SELECT id FROM drafts WHERE profile=?', (profile,)).fetchall():
            self.export(row['id'], profile)
        return self.folder(profile)

    def get(self, key, profile):
        row = self.db.execute('SELECT payload FROM drafts WHERE id=? AND profile=?', (key,profile)).fetchone()
        if row is None:
            raise ValueError('找不到当前词库的这份草稿')
        return validate_draft(json.loads(row[0]))

    def close(self):
        self.db.close()
