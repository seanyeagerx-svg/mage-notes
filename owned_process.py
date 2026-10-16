"""Tie a private Windows helper to this process, including abnormal exit.

An unnamed, non-inheritable Job Object controls only explicitly attached child
process handles. It never enumerates or terminates another application's tasks.
"""
import ctypes as C
from ctypes import wintypes as W
import threading


class _Basic(C.Structure):
    _fields_=[('process_time',C.c_longlong),('job_time',C.c_longlong),('flags',W.DWORD),
        ('min_working',C.c_size_t),('max_working',C.c_size_t),('active_limit',W.DWORD),
        ('affinity',C.c_size_t),('priority',W.DWORD),('scheduling',W.DWORD)]


class _IO(C.Structure):
    _fields_=[(name,C.c_ulonglong) for name in ('reads','writes','other','read_bytes','write_bytes','other_bytes')]


class _Extended(C.Structure):
    _fields_=[('basic',_Basic),('io',_IO),('process_memory',C.c_size_t),('job_memory',C.c_size_t),
        ('peak_process_memory',C.c_size_t),('peak_job_memory',C.c_size_t)]


class ChildLifetime:
    def __init__(self):
        self.lock=threading.Lock()
        self.kernel=C.WinDLL('kernel32',use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes=[C.c_void_p,W.LPCWSTR]
        self.kernel.CreateJobObjectW.restype=W.HANDLE
        self.kernel.SetInformationJobObject.argtypes=[W.HANDLE,C.c_int,C.c_void_p,W.DWORD]
        self.kernel.SetInformationJobObject.restype=W.BOOL
        self.kernel.AssignProcessToJobObject.argtypes=[W.HANDLE,W.HANDLE]
        self.kernel.AssignProcessToJobObject.restype=W.BOOL
        self.kernel.CloseHandle.argtypes=[W.HANDLE];self.kernel.CloseHandle.restype=W.BOOL
        self.handle=self.kernel.CreateJobObjectW(None,None)
        if not self.handle:raise C.WinError(C.get_last_error())
        info=_Extended();info.basic.flags=0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.kernel.SetInformationJobObject(self.handle,9,C.byref(info),C.sizeof(info)):
            error=C.get_last_error();self.close();raise C.WinError(error)

    def attach(self,process):
        with self.lock:
            if not self.handle:raise RuntimeError('后台进程所有者已关闭')
            if not self.kernel.AssignProcessToJobObject(self.handle,W.HANDLE(int(process._handle))):
                raise C.WinError(C.get_last_error())

    def close(self):
        with self.lock:
            handle,self.handle=self.handle,None
            if handle:self.kernel.CloseHandle(handle)

    def __del__(self):
        if getattr(self,'handle',None):self.close()
