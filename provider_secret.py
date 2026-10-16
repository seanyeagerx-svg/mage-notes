"""User-scoped Windows DPAPI storage. Never put a provider key in preferences/logs."""
import ctypes
from ctypes import wintypes
from pathlib import Path


class Blob(ctypes.Structure):
    _fields_=[('length',wintypes.DWORD),('data',ctypes.POINTER(ctypes.c_ubyte))]


def crypt(data,decrypt=False):
    buffer=ctypes.create_string_buffer(data)
    source=Blob(len(data),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte)))
    output=Blob()
    library=ctypes.WinDLL('crypt32',use_last_error=True)
    name='CryptUnprotectData' if decrypt else 'CryptProtectData'
    fn=getattr(library,name)
    fn.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
    fn.restype=wintypes.BOOL
    if not fn(ctypes.byref(source),None,None,None,None,1,ctypes.byref(output)):
        raise OSError('本机密钥无法解密，请重新填写')
    free=ctypes.WinDLL('kernel32',use_last_error=True).LocalFree
    free.argtypes=[ctypes.c_void_p];free.restype=ctypes.c_void_p
    try:return ctypes.string_at(output.data,output.length)
    finally:free(output.data)


class KeyStore:
    def __init__(self,root):self.path=Path(root)/'data'/'glm-key.dpapi'
    def exists(self):return self.path.is_file()
    def save(self,key):
        key=key.strip()
        if not 8<=len(key)<=2048 or any(c.isspace() or ord(c)<32 for c in key):
            raise ValueError('密钥格式不正确，请完整粘贴 API Key')
        encrypted=crypt(key.encode('utf-8'))
        self.path.parent.mkdir(parents=True,exist_ok=True)
        temporary=self.path.with_suffix('.tmp');temporary.write_bytes(encrypted);temporary.replace(self.path)
    def load(self):
        if not self.exists():raise ValueError('尚未填写 GLM API Key；本地听写仍可使用')
        return crypt(self.path.read_bytes(),decrypt=True).decode('utf-8')
    def clear(self):self.path.unlink(missing_ok=True)
