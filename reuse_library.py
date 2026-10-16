"""Explicitly selected local source material, with provenance and no AI rewriting."""
from pathlib import Path
from datetime import datetime
import hashlib, os, sqlite3, tempfile

class ReuseLibrary:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(self.path,timeout=2);self.db.row_factory=sqlite3.Row
        self.db.execute('''CREATE TABLE IF NOT EXISTS material(
            id INTEGER PRIMARY KEY,profile TEXT NOT NULL,title TEXT NOT NULL,purpose TEXT NOT NULL,
            content TEXT NOT NULL,digest TEXT NOT NULL,created TEXT NOT NULL,
            source_draft INTEGER,source_date TEXT NOT NULL,status TEXT NOT NULL DEFAULT '待核对',
            UNIQUE(profile,digest))''')
        self.db.execute('CREATE INDEX IF NOT EXISTS material_profile_id ON material(profile,id DESC)')
        self.db.commit()

    def collect(self,profile,title,purpose,content,source_draft=None,source_date=''):
        content=content.strip()
        if not content or len(content)>64000:raise ValueError('请选择不超过 64000 字的文字素材')
        title=title.strip()[:120] or content.splitlines()[0][:36]
        digest=hashlib.sha256(content.encode('utf-8')).hexdigest()
        with self.db:
            cursor=self.db.execute('''INSERT OR IGNORE INTO material(profile,title,purpose,content,digest,created,source_draft,source_date)
                VALUES(?,?,?,?,?,?,?,?)''',(profile,title,purpose.strip()[:400],content,digest,datetime.now().astimezone().isoformat(timespec='seconds'),source_draft,source_date[:40]))
        key=self.db.execute('SELECT id FROM material WHERE profile=? AND digest=?',(profile,digest)).fetchone()[0]
        return key,cursor.rowcount>0

    def rows(self,profile,offset=0,limit=50,search=''):
        query='profile=?';args=[profile]
        if search.strip():query+=' AND (instr(title,?)>0 OR instr(purpose,?)>0 OR instr(content,?)>0)';args.extend([search.strip()[:200]]*3)
        return [dict(r) for r in self.db.execute('SELECT id,title,purpose,created,source_draft,source_date,status FROM material WHERE '+query+' ORDER BY id DESC LIMIT ? OFFSET ?',(*args,min(100,max(1,limit)),max(0,offset)))]

    def get(self,key,profile):
        row=self.db.execute('SELECT * FROM material WHERE id=? AND profile=?',(key,profile)).fetchone()
        if row is None:raise ValueError('找不到当前词库的这份素材')
        return dict(row)

    def document(self,key,profile):
        row=self.get(key,profile)
        source='草稿 '+str(row['source_draft']) if row['source_draft'] else '主动选中的文字'
        return f"# {row['title']}\n\n用途：{row['purpose'] or '未填写'}\n\n来源：{source} · {row['source_date'] or row['created']}\n\n状态：{row['status']}（原文素材，使用前核对）\n\n{row['content']}\n"

    def export(self,key,profile):
        from draft_documents import profile_folder
        folder=profile_folder(self.path.parent/'复用素材',profile);folder.mkdir(parents=True,exist_ok=True)
        path=folder/f'素材_{int(key):06d}.md'
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=folder,prefix='.material-',suffix='.tmp',delete=False) as stream:
            temp=Path(stream.name);stream.write(self.document(key,profile));stream.flush();os.fsync(stream.fileno())
        try:os.replace(temp,path)
        finally:
            if temp.exists():temp.unlink()
        return path

    def close(self):self.db.close()

def store(app):
    library=getattr(app,'reuse_library',None)
    if library is None:
        library=ReuseLibrary(app.skills.path.parent/'data/reuse-library.sqlite3');app.reuse_library=library
    return library
