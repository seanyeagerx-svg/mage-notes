"""Local, per-person vocabulary. Only human edits can authorize replacements.

Automatic results are never training labels. Usage counts are hints only.
The database holds short phrases/context, not recordings or full documents.
"""
from dataclasses import dataclass
from difflib import SequenceMatcher
import json
from pathlib import Path
import re
import sqlite3
import time
import uuid

MAX_PHRASE = 48
AUTO_VOTES = 2
PUNCTUATION = re.compile(r'[\s，。！？；：、,.!?;:]+')
PROTECTED = re.compile(r'[0-9零一二三四五六七八九十百千万两]+|不要|不对|不是|不想|不用|不能|不许|没有|不|没|无|别|勿|未|否|发送|取消发送')
QUOTED = re.compile(r'“[^”]*”|「[^」]*」|『[^』]*』|"[^"]*"|`[^`]*`')


def content(text):
    return PUNCTUATION.sub('', text)


def _phonetic(text):
    from pypinyin import lazy_pinyin
    return lazy_pinyin(text.lower())


def _same_protected(before, after):
    return PROTECTED.findall(before) == PROTECTED.findall(after)


def _context(text, start, end):
    # Never allow context to cross a sentence boundary.
    left = re.split(r'[，。！？；：、,.!?;:\n]', text[:start])[-1][-4:]
    right = re.split(r'[，。！？；：、,.!?;:\n]', text[end:])[0][:4]
    return left, right


def edit_pairs(before, after):
    """Small same-sounding user replacements; rewrites/deletions aren't spelling.

    Single-character differences borrow an unchanged adjacent character, so a
    correction such as 电图 -> 垫图 never becomes a global 电 -> 垫 rule.
    """
    if len(before) > 64000 or len(after) > 64000 or not before or not after:
        return []
    # Bound diff work before using SequenceMatcher: long repeated documents
    # must not stall the recording UI. A broad rewrite is not a word lesson.
    quoted = [(m.start(),m.end()) for m in QUOTED.finditer(before)]
    first = 0
    while first < min(len(before),len(after)) and before[first] == after[first]:
        first += 1
    tail = 0
    while tail < min(len(before),len(after))-first and before[-tail-1] == after[-tail-1]:
        tail += 1
    if max(len(before)-first-tail,len(after)-first-tail)>512:
        return []
    offset = max(0,first-8)
    before = before[offset:min(len(before),len(before)-tail+8)]
    after = after[offset:min(len(after),len(after)-tail+8)]
    pairs = []
    # English corrections must teach whole words, not a changed suffix such as
    # "you -> ill" from SKYOU -> Skill. Spaces in spoken names are meaningful.
    begin=first-offset
    old_end=len(before)-min(8,tail)
    new_end=len(after)-min(8,tail)
    left_index=begin
    while left_index>0 and re.match('[A-Za-z]',before[left_index-1]):left_index-=1
    while old_end<len(before) and re.match('[A-Za-z]',before[old_end]):old_end+=1
    while new_end<len(after) and re.match('[A-Za-z]',after[new_end]):new_end+=1
    source,target=before[left_index:old_end].strip(),after[left_index:new_end].strip()
    if (re.search('[A-Za-z]{2}',target) and 2<=len(source)<=32 and 2<=len(target)<=32
            and re.fullmatch(r'[\u3400-\u9fffA-Za-z -]+',source+target)
            and _same_protected(source,target) and not any(a<old_end+offset and b>left_index+offset for a,b in quoted)):
        left,right=_context(before,left_index,old_end)
        return [(source,target,left,right)]
    matcher = SequenceMatcher(None, before, after, autojunk=False)
    for kind, i, j, a, b in matcher.get_opcodes():
        if kind != 'replace' or j-i > 12 or b-a > 12:
            continue
        source, target = before[i:j], after[a:b]
        if len(source) == 1 or len(target) == 1:
            if j < len(before) and b < len(after) and before[j] == after[b] and before[j].isalnum():
                j += 1; b += 1
            elif i and a and before[i-1] == after[a-1] and before[i-1].isalnum():
                i -= 1; a -= 1
            source, target = before[i:j], after[a:b]
        if not (2 <= len(source) <= 16 and 2 <= len(target) <= 16):
            continue
        if not re.fullmatch(r'[\u3400-\u9fffA-Za-z]+', source + target):
            continue
        if not _same_protected(source, target):
            continue
        if any(a <= i+offset < b for a,b in quoted):
            continue
        left, right = _context(before, i, j)
        pairs.append((source, target, left, right))
    return list(dict.fromkeys(pairs))


@dataclass(frozen=True)
class Correction:
    text: str
    rule_ids: tuple = ()


class PersonalStore:
    def __init__(self, path):
        self.path = Path(path)
        self.error = None
        self.revision = 0
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.db = sqlite3.connect(self.path, timeout=2)
            self._schema()
        except (OSError, sqlite3.Error) as exc:
            if getattr(self, 'db', None):
                self.db.close()
            self.error = str(exc)
            # Leave an unreadable file untouched; never destroy old vocabulary.
            self.db = sqlite3.connect(':memory:')
            self._schema()
        self.db.row_factory = sqlite3.Row
        if not self.profiles():
            self.create_profile('我的用语')
        row = self.db.execute("SELECT value FROM meta WHERE key='active'").fetchone()
        self.active = row[0] if row and any(p['id'] == row[0] for p in self.profiles()) else self.profiles()[0]['id']
        self.select_profile(self.active)

    def _schema(self):
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS profiles(id TEXT PRIMARY KEY, name TEXT NOT NULL, learning INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS rules(
          id INTEGER PRIMARY KEY, profile TEXT NOT NULL, source TEXT NOT NULL, target TEXT NOT NULL,
          left_ctx TEXT NOT NULL DEFAULT '', right_ctx TEXT NOT NULL DEFAULT '',
          scope TEXT NOT NULL DEFAULT 'context', status TEXT NOT NULL DEFAULT 'pending',
          origin TEXT NOT NULL DEFAULT 'manual_edit', count INTEGER NOT NULL DEFAULT 0,
          updated REAL NOT NULL, UNIQUE(profile,source,target,left_ctx,right_ctx,scope));
        CREATE TABLE IF NOT EXISTS votes(rule INTEGER NOT NULL, event TEXT NOT NULL, PRIMARY KEY(rule,event));
        CREATE TABLE IF NOT EXISTS phrases(
          profile TEXT NOT NULL, text TEXT NOT NULL, count INTEGER NOT NULL DEFAULT 0,
          protected INTEGER NOT NULL DEFAULT 0, updated REAL NOT NULL, PRIMARY KEY(profile,text));
        CREATE TABLE IF NOT EXISTS learning_events(
          profile TEXT NOT NULL, code TEXT NOT NULL, count INTEGER NOT NULL, updated REAL NOT NULL,
          PRIMARY KEY(profile,code));
        CREATE TABLE IF NOT EXISTS rule_uses(
          profile TEXT NOT NULL, rule INTEGER NOT NULL, event TEXT NOT NULL, updated REAL NOT NULL,
          PRIMARY KEY(profile,rule,event));
        ''')
        self.db.commit()

    def _commit(self):
        self.db.commit()
        self.revision += 1

    def profiles(self):
        return [dict(zip(('id','name','learning'), row)) for row in self.db.execute('SELECT id,name,learning FROM profiles ORDER BY rowid')]

    def create_profile(self, name):
        name = str(name).strip()
        if not 1 <= len(name) <= 24:
            raise ValueError('名称请用 1～24 个字')
        if any(p['name'] == name for p in self.profiles()):
            raise ValueError('这个名称已存在')
        key = uuid.uuid4().hex
        self.db.execute('INSERT INTO profiles(id,name) VALUES(?,?)', (key,name))
        self._commit()
        return key

    def select_profile(self, key):
        if not any(p['id'] == key for p in self.profiles()):
            raise ValueError('找不到这份个人词库')
        self.active = key
        self.db.execute("INSERT OR REPLACE INTO meta VALUES('active',?)", (key,))
        self._commit()

    def rename(self, name):
        name = name.strip()
        if not 1 <= len(name) <= 24 or any(p['name'] == name and p['id'] != self.active for p in self.profiles()):
            raise ValueError('请使用不同的名称，长度 1～24 个字')
        self.db.execute('UPDATE profiles SET name=? WHERE id=?', (name,self.active))
        self._commit()

    @property
    def learning(self):
        return bool(self.db.execute('SELECT learning FROM profiles WHERE id=?',(self.active,)).fetchone()[0])

    def set_learning(self, enabled):
        self.db.execute('UPDATE profiles SET learning=? WHERE id=?',(int(enabled),self.active))
        self._commit()

    def rules(self):
        return [dict(row) for row in self.db.execute('SELECT * FROM rules WHERE profile=? ORDER BY updated DESC LIMIT 1000',(self.active,))]

    def record_applied(self, rule_ids, event):
        # Called only after a verified editor write succeeds, not during proposals.
        if not rule_ids:return
        changed=False
        for key in set(rule_ids):
            cursor=self.db.execute('''INSERT OR IGNORE INTO rule_uses(profile,rule,event,updated)
                SELECT profile,id,?,? FROM rules WHERE id=? AND profile=? AND status='active' ''',
                (str(event),time.time(),key,self.active))
            changed=changed or bool(cursor.rowcount)
        self.db.commit()
        if changed:self.revision+=1

    def usage_count(self, rule_id=None):
        query='SELECT COUNT(*) FROM rule_uses WHERE profile=?'
        params=[self.active]
        if rule_id is not None:query+=' AND rule=?';params.append(rule_id)
        return self.db.execute(query,params).fetchone()[0]

    def phrases(self):
        return [dict(row) for row in self.db.execute('SELECT * FROM phrases WHERE profile=? ORDER BY protected DESC,count DESC,updated DESC LIMIT 500',(self.active,))]

    def decode_terms(self):
        """Only human vocabulary, never the recognizer's own usage counts.

        A pending English correction can provide spelling evidence to the audio
        decoder without granting permission for a global text replacement.
        Conflicting, disabled and unconfirmed Chinese alternatives are omitted.
        """
        rows = self.rules()
        terms = []
        for row in rows:
            if row['status'] == 'disabled':
                continue
            if any(other['source'] == row['source'] and other['target'] != row['target']
                   and other['status'] != 'disabled' for other in rows):
                continue
            human = row['origin'] == 'explicit' or row['origin'] == 'manual_edit' and row['count'] > 0 or row['origin']=='imported' and row['status']=='active'
            if human and (row['status'] == 'active' or re.search('[A-Za-z]{2}', row['target'])):
                terms.append(row['target'])
        terms.extend(row['text'] for row in self.phrases() if row['protected'])
        return list(dict.fromkeys(terms))

    def remember(self, text, protected=False):
        text = text.strip()
        if not 2 <= len(text) <= MAX_PHRASE:
            return
        self.db.execute('''INSERT INTO phrases VALUES(?,?,1,?,?) ON CONFLICT(profile,text)
             DO UPDATE SET count=MIN(count+1,999999),protected=MAX(protected,excluded.protected),updated=excluded.updated''',
             (self.active,text,int(protected),time.time()))

    def observe_usage(self, text):
        if not self.learning:
            return
        # Whole short phrases, not sliding fragments or full conversation logs.
        for phrase in set(PUNCTUATION.split(text)):
            if 2 <= len(phrase) <= 24 and not re.search(r'\d|https?://|@', phrase):
                self.remember(phrase)
        self.db.execute('''DELETE FROM phrases WHERE profile=? AND protected=0 AND rowid NOT IN
              (SELECT rowid FROM phrases WHERE profile=? ORDER BY protected DESC,updated DESC LIMIT 500)''',(self.active,self.active))
        self._commit()

    def add_rule(self, source, target, *, left='', right='', scope='literal', active=True, origin='explicit'):
        source, target = source.strip(), target.strip()
        if not 2 <= len(source) <= MAX_PHRASE or not 1 <= len(target) <= MAX_PHRASE or source == target:
            raise ValueError('请填写不同的原词和正确说法，原词至少 2 个字')
        if '\n' in source+target or not _same_protected(source, target):
            raise ValueError('不能把数字、否定或发送口令设成自动替换')
        if len(self.rules()) >= 1000:
            raise ValueError('这份词库已满，请先删除不用的记录')
        self.db.execute('''INSERT INTO rules(profile,source,target,left_ctx,right_ctx,scope,status,origin,updated)
              VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(profile,source,target,left_ctx,right_ctx,scope)
              DO UPDATE SET status=excluded.status,updated=excluded.updated''',
              (self.active,source,target,left,right,scope,'active' if active else 'pending',origin,time.time()))
        row = self.db.execute('SELECT id FROM rules WHERE profile=? AND source=? AND target=? AND left_ctx=? AND right_ctx=? AND scope=?',
              (self.active,source,target,left,right,scope)).fetchone()
        if active and origin == 'explicit':
            self.db.execute('UPDATE phrases SET protected=0 WHERE profile=? AND text=?',(self.active,source))
        self._commit()
        return row[0]

    def set_status(self, rule_id, status):
        if status not in ('active','pending','disabled'):
            raise ValueError('无效状态')
        self.db.execute('UPDATE rules SET status=?,updated=? WHERE id=? AND profile=?',(status,time.time(),rule_id,self.active))
        if status == 'active':
            self.db.execute('UPDATE phrases SET protected=0 WHERE profile=? AND text IN (SELECT source FROM rules WHERE id=? AND profile=?)',
                  (self.active,rule_id,self.active))
        self._commit()

    def delete_rule(self, rule_id):
        self.db.execute('DELETE FROM rule_uses WHERE profile=? AND rule=?',(self.active,rule_id))
        self.db.execute('DELETE FROM votes WHERE rule IN (SELECT id FROM rules WHERE id=? AND profile=?)',(rule_id,self.active))
        self.db.execute('DELETE FROM rules WHERE id=? AND profile=?',(rule_id,self.active))
        self._commit()

    def protect_phrase(self, text):
        self.remember(text, protected=True)
        self._commit()

    def delete_phrase(self, text):
        self.db.execute('DELETE FROM phrases WHERE profile=? AND text=?',(self.active,text))
        self._commit()

    def clear(self):
        self.db.execute('DELETE FROM rule_uses WHERE profile=?',(self.active,))
        self.db.execute('DELETE FROM learning_events WHERE profile=?',(self.active,))
        self.db.execute('DELETE FROM votes WHERE rule IN (SELECT id FROM rules WHERE profile=?)',(self.active,))
        self.db.execute('DELETE FROM rules WHERE profile=?',(self.active,))
        self.db.execute('DELETE FROM phrases WHERE profile=?',(self.active,))
        self._commit()

    def observe_edit(self, before, after, event_id):
        if not self.learning or before == after:
            return []
        learned = []
        for source, target, left, right in edit_pairs(before, after):
            # One explicit reversal is enough to stop a previously applied rule.
            inverse = [r for r in self.rules() if r['source'] == target and r['target'] == source and r['status'] == 'active'
                       and (r['scope'] == 'literal' or (r['left_ctx'] == left and r['right_ctx'] == right))]
            if inverse:
                for rule in inverse:
                    self.set_status(rule['id'], 'disabled')
                self.remember(target, protected=True)
                learned.append(('reverted',source,target))
                continue
            row = self.db.execute('SELECT * FROM rules WHERE profile=? AND source=? AND target=? AND left_ctx=? AND right_ctx=? AND scope=?',
                  (self.active,source,target,left,right,'context')).fetchone()
            if row is None:
                rule_id = self.add_rule(source,target,left=left,right=right,scope='context',active=False,origin='manual_edit')
            else:
                rule_id = row['id']
                if row['status'] == 'disabled':
                    continue
            self.db.execute('INSERT OR IGNORE INTO votes VALUES(?,?)',(rule_id,str(event_id)))
            count = self.db.execute('SELECT COUNT(*) FROM votes WHERE rule=?',(rule_id,)).fetchone()[0]
            # Conflicting choices for the same context require explicit review.
            conflicts = self.db.execute('SELECT id FROM rules WHERE profile=? AND source=? AND target<>? AND left_ctx=? AND right_ctx=? AND scope=? AND status<>?',
                  (self.active,source,target,left,right,'context','disabled')).fetchall()
            # A change of destination/meaning (上海 -> 北京) may be an ordinary
            # edit, not an ASR error. Keep those for explicit review even when
            # repeated; only a same-sounding spelling can activate by itself.
            status = 'active' if count >= AUTO_VOTES and not conflicts and _phonetic(source)==_phonetic(target) else 'pending'
            self.db.execute('UPDATE rules SET count=?,status=?,updated=? WHERE id=?',(count,status,time.time(),rule_id))
            for conflict in conflicts:
                self.db.execute("UPDATE rules SET status='pending' WHERE id=?",(conflict[0],))
            self.remember(target)
            learned.append((status,source,target))
        self._commit()
        return learned

    def apply(self, text):
        proposals = []
        protected = [(m.start(),m.end()) for m in QUOTED.finditer(text)]
        from literal_spelling import spelling_spans
        protected.extend(spelling_spans(text))
        for phrase in self.phrases():
            if phrase['protected']:
                protected.extend((m.start(),m.end()) for m in re.finditer(re.escape(phrase['text']),text))
        for rule in self.rules():
            if rule['status'] != 'active':
                continue
            for match in re.finditer(re.escape(rule['source']),text):
                start,end = match.span()
                if any(start < b and end > a for a,b in protected):
                    continue
                if rule['scope'] == 'context':
                    left,right = _context(text,start,end)
                    if left != rule['left_ctx'] or right != rule['right_ctx']:
                        continue
                proposals.append((start,end,rule['target'],rule['id']))
        accepted = []
        for proposal in proposals:
            start,end,target,key = proposal
            overlaps = [p for p in proposals if start < p[1] and end > p[0]]
            if all(p[:3] == proposal[:3] for p in overlaps) and not any(p[:3] == proposal[:3] for p in accepted):
                accepted.append(proposal)
        result = text
        for start,end,target,key in sorted(accepted,reverse=True):
            result = result[:start]+target+result[end:]
        return Correction(result,tuple(p[3] for p in accepted))

    def acoustic(self, online, second_pass, enabled=True):
        """Only agreement may add punctuation; disagreement keeps first reading."""
        selected = second_pass if enabled and content(online) == content(second_pass) else online
        return self.apply(selected) if enabled else Correction(selected)

    def migrate_explicit_legacy(self, path):
        if self.db.execute("SELECT 1 FROM meta WHERE key='legacy_imported'").fetchone():
            return
        # The one supplied before/after example belongs only to the first owner.
        # Import its actual whole phrase, not a generic filler/prefix rewrite.
        try:
            data = json.loads(Path(path).read_text('utf-8-sig'))
            if any(item.get('source') == '眼看一下' and item.get('target') == '你先看一下' for item in data.get('utterance_prefix',[])):
                self.add_rule('眼看一下嗯哦我们的那个自动化生图有没有那个方案可以用',
                              '你先看一下我们的那个自动化生图有没有那个方案可以用',origin='user_supplied_example')
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        self.db.execute("INSERT INTO meta VALUES('legacy_imported','1')")
        self._commit()

    def close(self):
        self.db.close()
