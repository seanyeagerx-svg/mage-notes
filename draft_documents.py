"""Readable local copies; structured recovery remains in JSON/SQLite."""
from pathlib import Path
import hashlib
import os
import tempfile
from draft_store import DraftStore, validate_draft


def profile_folder(root, profile):
    key = hashlib.sha256(str(profile).encode('utf-8')).hexdigest()[:12]
    return Path(root) / ('词库-' + key)


def document(data, title='语音草稿', saved_at=''):
    draft = validate_draft(data)
    pieces = ['# ' + title, saved_at, '## 转写', draft['dictation']]
    if draft['workflow']:
        pieces.extend(['## 工作流', draft['workflow']])
    if draft['refs']:
        pieces.extend(['## 素材', '\n'.join(draft['refs'])])
    return '\n\n'.join(pieces) + '\n'


def write_current(root, profile, data, saved_at):
    path = profile_folder(root, profile) / '当前草稿.md'
    path.parent.mkdir(parents=True, exist_ok=True)
    DraftStore(path)._atomic(path, document(data, saved_at=saved_at))
    return path


def write_revision(root, profile, key, created, data):
    folder = profile_folder(root, profile)
    folder.mkdir(parents=True, exist_ok=True)
    filename = created[:19].replace(':', '-').replace('T', '_') + f'_{int(key):06d}.md'
    path = folder / filename
    if not path.exists():
        temporary=None
        try:
            with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=folder,
                    prefix='.revision-',suffix='.tmp',delete=False) as stream:
                temporary=Path(stream.name)
                stream.write(document(data, title='语音草稿 · ' + str(key), saved_at=created))
                stream.flush();os.fsync(stream.fileno())
            try:os.link(temporary,path)
            except FileExistsError:pass
        finally:
            if temporary is not None and temporary.exists():temporary.unlink()
    return path
