"""Per-person text evidence; an AI suggestion is never a training label."""
import sqlite3
import time
from personalization import edit_pairs


class ProofreadingHistory:
    def __init__(self,path):
        self.error=None
        schema='''CREATE TABLE IF NOT EXISTS samples(
            id INTEGER PRIMARY KEY, profile TEXT NOT NULL, kind TEXT NOT NULL,
            original TEXT NOT NULL, proposal TEXT NOT NULL, output TEXT NOT NULL,
            context TEXT NOT NULL, backend TEXT NOT NULL, state TEXT NOT NULL,
            created REAL NOT NULL)'''
        self.db=None
        try:
            path.parent.mkdir(parents=True,exist_ok=True)
            self.db=sqlite3.connect(path,timeout=2)
            self.db.execute(schema)
            self.db.commit()
        except (OSError,sqlite3.Error) as exc:
            self.error=type(exc).__name__
            if self.db is not None:self.db.close()
            self.db=sqlite3.connect(':memory:')
            self.db.execute(schema)
            self.db.commit()

    def proposal(self,profile,original,proposal,output,context,backend):
        if not original or max(map(len,(original,proposal,output)))>600:
            return None
        row=self.db.execute('INSERT INTO samples VALUES(NULL,?,?,?,?,?,?,?,?,?)',
            (profile,'ai_suggestion',original,proposal,output,context[-160:],backend,'not_applied',time.time()))
        self.db.commit()
        return row.lastrowid

    def applied(self,identity):
        if identity is not None:
            try:
                self.db.execute("UPDATE samples SET state='applied' WHERE id=? AND kind='ai_suggestion'",(identity,))
                self.db.commit()
            except sqlite3.Error as exc:
                self.error=type(exc).__name__
                return False
        return self.error is None

    def human_edit(self,profile,before,after):
        # Only short actual edits and their immediate context, never the field.
        for source,target,left,right in edit_pairs(before,after):
            self.db.execute('INSERT INTO samples VALUES(NULL,?,?,?,?,?,?,?,?,?)',
                (profile,'human_edit',source,'',target,left+'|'+right,'human','confirmed_edit',time.time()))
        self.db.commit()

    def close(self):
        self.db.close()
