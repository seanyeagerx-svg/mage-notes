"""Local short audio/edit pairs; automatic hypotheses are never training truth.

Only explicitly started dictation is staged in RAM. A verified human edit in
that same owned range may save its utterance. No audio or dataset is uploaded.
"""
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
import io
from pathlib import Path
import re
import sqlite3
import time
import uuid
import wave


@dataclass(frozen=True)
class Clip:
    id: str
    profile: str
    start: int
    end: int
    text: str
    original: str
    wav: bytes
    created: float


def short_english_edit(source,target,wav):
    """Allow a human's short cross-script word edit, still only partial truth.

    Chinese ASR renderings and English names can have no common characters.
    This exception stores evidence; it never authorizes a text replacement.
    """
    from personalization import PROTECTED
    if (not re.fullmatch(r'(?:[\u3400-\u9fff]{1,8}|[A-Za-z]{1,24})[。.!?？！]?',source)
            or not re.fullmatch(r'[A-Za-z]{2,24}[。.!?？！]?',target)
            or PROTECTED.search(source)):
        return False
    try:
        with wave.open(io.BytesIO(wav),'rb') as audio:
            return audio.getnframes()/audio.getframerate()<=8
    except (wave.Error,EOFError,ZeroDivisionError):
        return False


def map_clips(clips, before, after):
    """Keep a clip only when all edits touching it stay within its boundaries."""
    if len(before)>64000 or len(after)>64000:
        return ()
    # Bound the diff to the changed region, avoiding quadratic repeated prose.
    first=0
    while first<min(len(before),len(after)) and before[first]==after[first]:first+=1
    tail=0
    while tail<min(len(before),len(after))-first and before[-tail-1]==after[-tail-1]:tail+=1
    old_end,new_end=len(before)-tail,len(after)-tail
    if max(old_end-first,new_end-first)>1024:
        return ()
    edits=[(op,i+first,j+first,a+first,b+first) for op,i,j,a,b in
        SequenceMatcher(None,before[first:old_end],after[first:new_end],autojunk=False).get_opcodes() if op!='equal']
    updated=[]
    for clip in clips:
        if before[clip.start:clip.end]!=clip.text:
            continue
        shift,size=0,0
        valid=True
        for _,i,j,a,b in edits:
            delta=(b-a)-(j-i)
            if j<=clip.start and i<clip.start:shift+=delta
            elif i>=clip.end:continue
            elif i>=clip.start and j<=clip.end:size+=delta
            else:valid=False;break
        if valid:
            start,end=clip.start+shift,clip.end+shift+size
            updated.append(replace(clip,start=start,end=end,text=after[start:end]))
    return tuple(updated)


class PronunciationMemory:
    MAX_BYTES=100*1024*1024
    MAX_SAMPLES=2000
    def __init__(self,path):
        self.path=Path(path)
        self.error=None
        self.capacity_reached=False
        self.clips=()
        self.revision=0
        self.db=None
        schema='''CREATE TABLE IF NOT EXISTS samples(
            id TEXT PRIMARY KEY, profile TEXT NOT NULL, created REAL NOT NULL,
            original TEXT NOT NULL, displayed TEXT NOT NULL, edited TEXT NOT NULL,
            wav BLOB NOT NULL, event TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'human_edit_partial')'''
        blocks='''CREATE TABLE IF NOT EXISTS word_blocks(
            profile TEXT NOT NULL,term TEXT NOT NULL,created REAL NOT NULL,
            PRIMARY KEY(profile,term))'''
        try:
            self.path.parent.mkdir(parents=True,exist_ok=True)
            self.db=sqlite3.connect(self.path,timeout=2)
            self.db.execute(schema)
            self.db.execute(blocks)
            self.db.commit()
        except (OSError,sqlite3.Error) as exc:
            self.error=type(exc).__name__
            if self.db is not None:self.db.close()
            # Keep the application usable without overwriting a damaged file.
            # Collection stays disabled: memory-only saves must not claim success.
            self.db=sqlite3.connect(':memory:')
            self.db.execute(schema)
            self.db.execute(blocks)
            self.db.commit()

    @property
    def notice(self):
        if self.error:return '录音库暂不可用，已暂停保存；听写继续'
        if self.capacity_reached:return '录音库已满，停止新增；已有样本保留'
        return ''

    def _failed(self,exc):
        if self.error!=type(exc).__name__:
            self.error=type(exc).__name__;self.revision+=1
        try:self.db.rollback()
        except sqlite3.Error:pass
        self.discard()

    def discard(self):
        self.clips=()

    def stage(self,profile,start,text,original,audio,rate):
        if self.error or audio is None or not text or len(text)>320 or rate<=0 or not .2<=len(audio)/rate<=32:
            return
        import numpy as np
        if rate!=16000:
            import soxr
            audio=soxr.resample(audio,rate,16000)
        pcm=(np.clip(np.asarray(audio),-1,1)*32767).astype('<i2').tobytes()
        buffer=io.BytesIO()
        with wave.open(buffer,'wb') as wav:
            wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000);wav.writeframes(pcm)
        clip=Clip(uuid.uuid4().hex,profile,start,start+len(text),text,original,buffer.getvalue(),time.monotonic())
        self.clips=tuple(c for c in self.clips[-11:] if time.monotonic()-c.created<=300)+(clip,)
        return True

    def patch(self,before,after):
        self.clips=map_clips(self.clips,before,after)

    def snapshot(self,profile,text):
        return tuple(c for c in self.clips if c.profile==profile and time.monotonic()-c.created<=300
                     and text[c.start:c.end]==c.text)

    def learn(self,clips,before,after,event,profile):
        updated=map_clips(clips,before,after)
        if self.error:return updated,0
        try:
            return updated,self._save(clips,updated,event,profile)
        except sqlite3.Error as exc:
            self._failed(exc)
            return updated,0

    def _save(self,clips,updated,event,profile):
        old={c.id:c for c in clips}
        saved=0
        blocked=0
        for clip in updated:
            previous=old[clip.id]
            if (clip.profile!=profile or time.monotonic()-clip.created>300 or previous.text==clip.text
                    or not 2<=len(clip.text)<=320):
                continue
            # A clear/delete, wholesale rewrite or pure layout change is not
            # reliable audio supervision. Save small edits only, labeled partial.
            source,target=previous.text,clip.text
            fold=lambda s:''.join(c for c in s.casefold() if c.isalnum())
            if fold(source)==fold(target):
                continue
            # A person explicitly replacing a short English output vetoes its
            # older acoustic references, even if the new whole-word edit has
            # too little overlap to save as partial transcription supervision.
            # Two later distinct human confirmations can qualify it again.
            rejected=re.fullmatch(r'([A-Za-z]{2,24})[。.!?？！]?',source.strip())
            if rejected and 2<=len(target)<=48:
                self.db.execute('''INSERT INTO word_blocks(profile,term,created) VALUES(?,?,?)
                    ON CONFLICT(profile,term) DO UPDATE SET created=excluded.created''',
                    (profile,rejected[1].casefold(),time.time()))
                blocked+=1
            matcher=SequenceMatcher(None,source,target,autojunk=False)
            changed=sum(max(j-i,b-a) for op,i,j,a,b in matcher.get_opcodes() if op!='equal')
            if changed>48 or (matcher.ratio()<.35 and not short_english_edit(source,target,clip.wav)):
                continue
            existing=self.db.execute('SELECT 1 FROM samples WHERE id=?',(clip.id,)).fetchone()
            if not existing:
                count,size=self.db.execute('SELECT COUNT(*),COALESCE(SUM(LENGTH(wav)),0) FROM samples').fetchone()
                if count>=self.MAX_SAMPLES or size+len(clip.wav)>self.MAX_BYTES:
                    if not self.capacity_reached:
                        self.capacity_reached=True;self.revision+=1
                    continue
            self.db.execute('''INSERT INTO samples(id,profile,created,original,displayed,edited,wav,event)
                VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET edited=excluded.edited,
                status='human_edit_partial' ''',
                (clip.id,profile,time.time(),clip.original,source,target,clip.wav,str(event)))
            saved+=1
        self.db.commit()
        if saved or blocked:self.revision+=1
        return saved

    def rows(self,profile):
        self.db.row_factory=sqlite3.Row
        try:
            return [dict(r) for r in self.db.execute('SELECT id,created,original,displayed,edited,status FROM samples WHERE profile=? ORDER BY created DESC LIMIT 2000',(profile,))]
        except sqlite3.Error as exc:
            self._failed(exc);return []

    def audio(self,profile,key):
        try:
            row=self.db.execute('SELECT wav FROM samples WHERE profile=? AND id=?',(profile,key)).fetchone()
            return row[0] if row else None
        except sqlite3.Error as exc:
            self._failed(exc);return None

    def delete(self,profile,key):
        try:
            self.db.execute('DELETE FROM samples WHERE profile=? AND id=?',(profile,key));self.db.commit();self.revision+=1
            self.capacity_reached=False
            return True
        except sqlite3.Error as exc:
            self._failed(exc);return False

    def close(self):
        self.discard();self.db.close()
