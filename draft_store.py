"""Atomic local recovery for the app's two draft fields and chosen references."""
from datetime import datetime
import json
import os
from pathlib import Path
import tempfile


def validate_draft(data):
    if not isinstance(data,dict) or data.get('schema_version',1)!=1:
        raise ValueError('不支持的草稿格式')
    result={}
    for key in ('dictation','workflow'):
        value=data.get(key,'')
        if not isinstance(value,str) or len(value)>2_000_000:
            raise ValueError('草稿内容无效')
        result[key]=value
    refs=data.get('refs',[])
    if (not isinstance(refs,list) or len(refs)>1000
            or any(not isinstance(p,str) or len(p)>32767 for p in refs)
            or sum(map(len,refs))>1_000_000):
        raise ValueError('参考图记录无效')
    result['refs']=list(dict.fromkeys(refs))
    return result


class DraftStore:
    def __init__(self,path):
        self.path=Path(path)
        self.backup=self.path.with_suffix('.previous.json')
        self.last=None
        self.error=None

    @staticmethod
    def read(path):
        path=Path(path)
        if path.stat().st_size>20_000_000:
            raise ValueError('草稿文件过大')
        return validate_draft(json.loads(path.read_text('utf-8-sig')))

    def load(self):
        for path in (self.path,self.backup):
            try:
                draft=self.read(path)
                # A recovered backup must be written to the main file again.
                self.last=draft if path==self.path else None
                return draft
            except FileNotFoundError:
                continue
            except (OSError,ValueError,UnicodeError) as exc:
                self.error=type(exc).__name__
        return None

    def _atomic(self,path,payload):
        temporary=None
        try:
            with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,
                                             prefix=path.stem+'-',suffix='.tmp',delete=False) as stream:
                temporary=Path(stream.name)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary,path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()

    def save(self,data):
        draft=validate_draft(data)
        if draft==self.last:
            return False
        self.path.parent.mkdir(parents=True,exist_ok=True)
        previous=None
        try:
            previous=self.read(self.path)
        except FileNotFoundError:
            pass
        except (OSError,ValueError,UnicodeError):
            # Retain damaged input before replacing it; never rotate it over
            # the last valid backup.
            if self.path.exists():
                damaged=self.path.with_name(self.path.stem+'-unreadable-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f')+'.json')
                with damaged.open('xb') as stream:
                    stream.write(self.path.read_bytes())
        if previous is not None:
            self._atomic(self.backup,json.dumps(previous,ensure_ascii=False,indent=2))
        payload=dict(schema_version=1,saved_at=datetime.now().astimezone().isoformat(),**draft)
        self._atomic(self.path,json.dumps(payload,ensure_ascii=False,indent=2))
        self.last=draft
        self.error=None
        return True
