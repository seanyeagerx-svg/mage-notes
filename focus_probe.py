"""Read only the focused editable field for dictation/send safety.

UI Automation runs on its own COM thread. No document text, browser history,
URLs, passwords, or other controls are logged. Send checks return only a digest.
Personal learning can explicitly request this same editable field's text in RAM;
the caller keeps only a small human correction, never the whole field on disk.
"""
from dataclasses import dataclass
import hashlib
import queue
import threading
import time
import ctypes as C
from ctypes import wintypes as W
from win_input import foreground, focused_control, window_process, user32

user32.GetClassNameW.argtypes=[W.HWND,W.LPWSTR,C.c_int]
user32.IsWindowEnabled.argtypes=[W.HWND]
user32.SendMessageTimeoutW.argtypes=[W.HWND,W.UINT,W.WPARAM,W.LPARAM,W.UINT,W.UINT,C.POINTER(C.c_size_t)]
user32.SendMessageTimeoutW.restype=C.c_ssize_t

def native_field(window,control,process,include_text=False):
    name=C.create_unicode_buffer(128)
    user32.GetClassNameW(control,name,len(name))
    if name.value.lower() not in ("edit","richedit20w","richedit50w"):return None
    style=user32.GetWindowLongPtrW(control,-16)
    if style & (0x20|0x800) or not user32.IsWindowEnabled(control):return None
    def message(kind,wparam=0,lparam=0):
        result=C.c_size_t()
        if not user32.SendMessageTimeoutW(control,kind,wparam,lparam,2,300,C.byref(result)):
            raise TimeoutError("Focused field did not respond")
        return result.value
    length=message(0xE)
    if length>64000:return None
    buffer=C.create_unicode_buffer(length+1)
    message(0xD,len(buffer),C.addressof(buffer))
    text=buffer.value
    selection=message(0xB0)
    if foreground()!=window or focused_control(window)!=control:return None
    identity=(window,control,process,"native-edit")
    digest=hashlib.sha256((text+"\0"+str(selection)).encode("utf-8")).hexdigest()
    return FocusSnapshot(identity,digest,bool(text.strip()),time.monotonic(),text.replace('\r\n','\n') if include_text else None)

@dataclass(frozen=True)
class FocusSnapshot:
    identity:tuple
    digest:str
    nonempty:bool
    captured_at:float
    text:str|None=None

class FocusReader:
    def __init__(self):
        import comtypes
        import comtypes.client
        comtypes.CoInitializeEx(0)
        self.comtypes=comtypes
        self.api=comtypes.client.GetModule("UIAutomationCore.dll")
        self.automation=comtypes.client.CreateObject(self.api.CUIAutomation8,interface=self.api.IUIAutomation2)
        self.automation.ConnectionTimeout=500
        self.automation.TransactionTimeout=500

    def snapshot(self,window,control,process,include_text=False):
        self.reason="window_mismatch"
        if foreground()!=window or focused_control(window)!=control or window_process(window)!=process:
            return None
        native=native_field(window,control,process,include_text=include_text)
        if native:
            self.reason="native_edit_verified"
            return native
        a=self.api
        field=self.automation.GetFocusedElement()
        self.reason="not_focused_enabled_edit"
        if not field or not field.CurrentHasKeyboardFocus or not field.CurrentIsEnabled or field.CurrentIsPassword:
            return None
        # UIA can briefly return a stale focused element from a different
        # window. Its provider tree must also belong to our original window.
        ancestor=field
        belongs=False
        for _ in range(24):
            native_window=ancestor.CurrentNativeWindowHandle
            if native_window and user32.GetAncestor(native_window,2)==window:
                belongs=True;break
            ancestor=self.automation.ControlViewWalker.GetParentElement(ancestor)
            if not ancestor:break
        if not belongs:
            self.reason="provider_window_mismatch";return None
        # Modern rich editors (including Windows Notepad) expose Document,
        # not Edit. A focused Document is eligible only with an explicitly
        # writable TextPattern and a real selection. Read-only page roots and
        # unsupported attributes still fail closed; never infer writability.
        document = field.CurrentControlType == a.UIA_DocumentControlTypeId
        if field.CurrentControlType!=a.UIA_EditControlTypeId and not document:
            self.reason=f"control_type_{field.CurrentControlType}"
            return None
        try:
            if document:raise ValueError('Document requires a writable text range')
            value=field.GetCurrentPattern(a.UIA_ValuePatternId).QueryInterface(a.IUIAutomationValuePattern)
            if value.CurrentIsReadOnly:
                self.reason="readonly_value";return None
            text=value.CurrentValue
        except (self.comtypes.COMError,ValueError):
            text_pattern=field.GetCurrentPattern(a.UIA_TextPatternId).QueryInterface(a.IUIAutomationTextPattern)
            # A TextPattern must explicitly expose writability as a text attribute.
            readonly=text_pattern.DocumentRange.GetAttributeValue(a.UIA_IsReadOnlyAttributeId)
            if readonly is not False and readonly!=0:
                self.reason="readonly_text";return None
            if document:
                selection=text_pattern.GetSelection()
                if not field.CurrentIsKeyboardFocusable or selection is None or selection.Length!=1:
                    self.reason="document_without_edit_selection";return None
            text=text_pattern.DocumentRange.GetText(64001)
        if not isinstance(text,str) or len(text)>64000:return None
        identity=(window,control,process,field.CurrentProcessId,tuple(field.GetRuntimeId()))
        if foreground()!=window or focused_control(window)!=control or not field.CurrentHasKeyboardFocus:
            return None
        self.reason='writable_document_verified' if document else 'editable_field_verified'
        return FocusSnapshot(identity,hashlib.sha256(text.encode("utf-8")).hexdigest(),bool(text.strip()),time.monotonic(),text.replace('\r\n','\n') if include_text else None)

    def close(self):
        self.automation=None
        self.comtypes.CoUninitialize()

class FocusProbe:
    def __init__(self,events):
        self.events=events
        self.requests=queue.Queue()
        self.closed=threading.Event()
        # Import on the GUI thread so comtypes' one-time STA initialization does
        # not collide with this worker's explicit MTA apartment.
        try:import comtypes.client
        except ImportError:pass
        self.thread=threading.Thread(target=self._run,name="focused-field-check",daemon=True)
        self.thread.start()
    def request(self,token,window,control,process,include_text=False):
        self.requests.put((token,window,control,process,include_text))
    def _run(self):
        reader=None
        try:
            reader=FocusReader()
            while not self.closed.is_set():
                try: token,window,control,process,include_text=self.requests.get(timeout=.1)
                except queue.Empty:continue
                try:
                    snapshot=reader.snapshot(window,control,process,include_text=include_text)
                    self.last_reason=reader.reason
                except Exception as exc:
                    self.last_error=repr(exc)
                    snapshot=None
                if not self.closed.is_set():self.events.put(("learning_snapshot" if include_text else "focus_snapshot",(token,snapshot)))
        except Exception as exc:
            # Missing/broken accessibility may disable voice send, never dictation.
            self.error=type(exc).__name__
        finally:
            if reader:reader.close()
    def close(self):
        self.closed.set()
        self.thread.join(.6)
