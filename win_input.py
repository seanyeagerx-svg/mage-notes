"""Small Windows input adapter. No clipboard reads, text logging, or focus stealing."""
import ctypes as C
from ctypes import wintypes as W
import queue
import threading
import time
import unicodedata
from hotkeys import BindingCapture, MODIFIER_VKS, key_vk, base_vks

user32 = C.WinDLL("user32", use_last_error=True)
kernel32 = C.WinDLL("kernel32", use_last_error=True)
ULONG_PTR = C.c_size_t
LRESULT = C.c_ssize_t
TAG = 0x564F4943


class KEYBDINPUT(C.Structure):
    _fields_ = [("wVk", W.WORD), ("wScan", W.WORD), ("dwFlags", W.DWORD),
                ("time", W.DWORD), ("dwExtraInfo", ULONG_PTR)]


class MOUSEINPUT(C.Structure):
    _fields_ = [("dx", W.LONG), ("dy", W.LONG), ("mouseData", W.DWORD),
                ("dwFlags", W.DWORD), ("time", W.DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(C.Structure):
    _fields_ = [("uMsg", W.DWORD), ("wParamL", W.WORD), ("wParamH", W.WORD)]


class INPUTUNION(C.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(C.Structure):
    _anonymous_ = ("value",)
    _fields_ = [("type", W.DWORD), ("value", INPUTUNION)]


class KBDHOOK(C.Structure):
    _fields_ = [("vkCode", W.DWORD), ("scanCode", W.DWORD), ("flags", W.DWORD),
                ("time", W.DWORD), ("dwExtraInfo", ULONG_PTR)]


class MOUSEHOOK(C.Structure):
    _fields_ = [("pt", W.POINT), ("mouseData", W.DWORD), ("flags", W.DWORD),
                ("time", W.DWORD), ("dwExtraInfo", ULONG_PTR)]


class GUITHREADINFO(C.Structure):
    _fields_ = [("cbSize", W.DWORD), ("flags", W.DWORD), ("hwndActive", W.HWND),
                ("hwndFocus", W.HWND), ("hwndCapture", W.HWND), ("hwndMenuOwner", W.HWND),
                ("hwndMoveSize", W.HWND), ("hwndCaret", W.HWND), ("rcCaret", W.RECT)]


HOOKPROC = C.WINFUNCTYPE(LRESULT, C.c_int, W.WPARAM, W.LPARAM)
user32.GetForegroundWindow.restype = W.HWND
user32.FindWindowW.argtypes = [W.LPCWSTR, W.LPCWSTR]
user32.FindWindowW.restype = W.HWND
user32.SendInput.argtypes = [W.UINT, C.POINTER(INPUT), C.c_int]
user32.SendInput.restype = W.UINT
user32.SetWindowsHookExW.argtypes = [C.c_int, HOOKPROC, W.HINSTANCE, W.DWORD]
user32.SetWindowsHookExW.restype = W.HHOOK
user32.CallNextHookEx.argtypes = [W.HHOOK, C.c_int, W.WPARAM, W.LPARAM]
user32.CallNextHookEx.restype = LRESULT
user32.UnhookWindowsHookEx.argtypes = [W.HHOOK]
user32.PostThreadMessageW.argtypes = [W.DWORD, W.UINT, W.WPARAM, W.LPARAM]
kernel32.GetModuleHandleW.argtypes = [W.LPCWSTR]
kernel32.GetModuleHandleW.restype = W.HMODULE
user32.GetAsyncKeyState.argtypes = [C.c_int]
user32.GetAsyncKeyState.restype = C.c_short
user32.GetAncestor.argtypes = [W.HWND, W.UINT]
user32.GetAncestor.restype = W.HWND
user32.SetForegroundWindow.argtypes = [W.HWND]
user32.SetForegroundWindow.restype = W.BOOL
user32.ShowWindow.argtypes = [W.HWND, C.c_int]
user32.ShowWindow.restype = W.BOOL
user32.IsWindowVisible.argtypes = [W.HWND]
user32.IsWindowVisible.restype = W.BOOL
user32.IsWindow.argtypes = [W.HWND]
user32.IsWindow.restype = W.BOOL
user32.GetWindowThreadProcessId.argtypes = [W.HWND, C.POINTER(W.DWORD)]
user32.GetWindowThreadProcessId.restype = W.DWORD
user32.GetGUIThreadInfo.argtypes = [W.DWORD, C.POINTER(GUITHREADINFO)]
user32.GetGUIThreadInfo.restype = W.BOOL
user32.AttachThreadInput.argtypes = [W.DWORD, W.DWORD, W.BOOL]
user32.AttachThreadInput.restype = W.BOOL
user32.SetFocus.argtypes = [W.HWND]
user32.SetFocus.restype = W.HWND
user32.GetWindowRect.argtypes = [W.HWND,C.POINTER(W.RECT)]
user32.GetWindowLongPtrW.argtypes = [W.HWND, C.c_int]
user32.GetWindowLongPtrW.restype = C.c_ssize_t
user32.SetWindowLongPtrW.argtypes = [W.HWND, C.c_int, C.c_ssize_t]
user32.SetWindowLongPtrW.restype = C.c_ssize_t


def make_nonactivating(widget):
    widget.update_idletasks()
    hwnd = user32.GetAncestor(widget.winfo_id(), 2)
    style = user32.GetWindowLongPtrW(hwnd, -20)
    user32.SetWindowLongPtrW(hwnd, -20, style | 0x08000000 | 0x00000080)


def top_handle(widget):
    widget.update_idletasks()
    return user32.GetAncestor(widget.winfo_id(), 2)


def foreground():
    return user32.GetForegroundWindow()


def show_existing_app():
    hwnd = (user32.FindWindowW(None, "魔法师手札 · 开口成文，一语施法")
            or user32.FindWindowW(None, "魔法师手札")
            or user32.FindWindowW(None, "声笺 · 本地实时语音"))
    if hwnd:
        user32.ShowWindow(hwnd, 9)
        user32.SetForegroundWindow(hwnd)
        return True
    return False


def window_process(hwnd):
    process = W.DWORD()
    if hwnd and user32.IsWindow(hwnd):
        user32.GetWindowThreadProcessId(hwnd, C.byref(process))
    return process.value


def focused_control(window):
    info = GUITHREADINFO()
    info.cbSize = C.sizeof(info)
    thread = user32.GetWindowThreadProcessId(window, None)
    if thread and user32.GetGUIThreadInfo(thread, C.byref(info)):
        if info.hwndFocus and user32.GetAncestor(info.hwndFocus, 2) == window:
            return info.hwndFocus
    return None


def restore_input_focus(window, control):
    if not window or not control or not user32.IsWindow(control):
        return False
    if user32.GetAncestor(control, 2) != window:
        return False
    user32.SetForegroundWindow(window)
    if foreground() != window:
        return False
    current = kernel32.GetCurrentThreadId()
    target = user32.GetWindowThreadProcessId(control, None)
    attached = current != target and bool(user32.AttachThreadInput(current, target, True))
    try:
        user32.SetFocus(control)
    finally:
        if attached:
            user32.AttachThreadInput(current, target, False)
    return focused_control(window) == control


def modifiers_down():
    return any(user32.GetAsyncKeyState(vk) & 0x8000 for vk in (0x10, 0x11, 0x12, 0x5B, 0x5C))


def type_unicode(text: str, target=None, move_to_end=False):
    if not text:
        return True
    if target and foreground() != target:
        return False
    if modifiers_down():
        return False
    units = text.encode("utf-16-le")
    events = []
    if move_to_end:
        # Keep the resume shortcut and new text in one atomic SendInput batch.
        events.extend([
            INPUT(type=1, ki=KEYBDINPUT(0x11, 0, 0, 0, TAG)),
            INPUT(type=1, ki=KEYBDINPUT(0x23, 0, 0, 0, TAG)),
            INPUT(type=1, ki=KEYBDINPUT(0x23, 0, 2, 0, TAG)),
            INPUT(type=1, ki=KEYBDINPUT(0x11, 0, 2, 0, TAG)),
        ])
    for i in range(0, len(units), 2):
        code = int.from_bytes(units[i:i + 2], "little")
        events.append(INPUT(type=1, ki=KEYBDINPUT(0, code, 0x0004, 0, TAG)))
        events.append(INPUT(type=1, ki=KEYBDINPUT(0, code, 0x0004 | 0x0002, 0, TAG)))
    array = (INPUT * len(events))(*events)
    count = user32.SendInput(len(events), array, C.sizeof(INPUT))
    if count != len(events):
        raise OSError("文字输入被 Windows 拒绝；请确认目标软件没有以管理员身份运行。")
    return True


def paste_shortcut(target):
    """Paste a prepared workflow prompt, preserving line breaks without Enter."""
    if not target or foreground() != target or modifiers_down():
        return False
    values = (INPUT * 4)(
        INPUT(type=1, ki=KEYBDINPUT(0x11, 0, 0, 0, TAG)),
        INPUT(type=1, ki=KEYBDINPUT(0x56, 0, 0, 0, TAG)),
        INPUT(type=1, ki=KEYBDINPUT(0x56, 0, 2, 0, TAG)),
        INPUT(type=1, ki=KEYBDINPUT(0x11, 0, 2, 0, TAG)))
    if user32.SendInput(4, values, C.sizeof(INPUT)) != 4:
        raise OSError("Windows 未能粘贴到原输入框，完整提示词仍在本次任务中。")
    return True


def send_shortcut(target, control, key="enter"):
    """Send only after the caller verifies the field identity and countdown."""
    if not target or foreground()!=target or focused_control(target)!=control or modifiers_down():return False
    if key not in ("enter","ctrl+enter"):return False
    events=[]
    if key=="ctrl+enter":events.append(INPUT(type=1,ki=KEYBDINPUT(0x11,0,0,0,TAG)))
    events.extend([INPUT(type=1,ki=KEYBDINPUT(0x0D,0,0,0,TAG)),INPUT(type=1,ki=KEYBDINPUT(0x0D,0,2,0,TAG))])
    if key=="ctrl+enter":events.append(INPUT(type=1,ki=KEYBDINPUT(0x11,0,2,0,TAG)))
    batch=(INPUT*len(events))(*events)
    if user32.SendInput(len(events),batch,C.sizeof(INPUT))!=len(events):
        raise OSError("发送按键未完整输入；不会自动重试，以免重复发送。")
    return True


def replace_owned_tail(original, replacement, target, control):
    """Replace only the just-typed untouched tail, never select the whole field.

    Caller must also check its physical-input epoch and generation. Multi-codepoint
    graphemes and line breaks are rejected because arrow-key units vary by editor.
    """
    if not original or original == replacement:
        return original == replacement
    prefix = 0
    while prefix < min(len(original), len(replacement)) and original[prefix] == replacement[prefix]:
        prefix += 1
    original, replacement = original[prefix:], replacement[prefix:]
    if len(original) > 512 or len(replacement) > 1024:
        return False
    if any(ord(char) > 0xFFFF or unicodedata.combining(char) or unicodedata.category(char).startswith("M")
           or char in "\r\n\t" for char in original):
        return False
    if foreground() != target or focused_control(target) != control or modifiers_down():
        return False
    # Backspace has no modifier state to get lost through IMEs/accessibility
    # providers. Only our untouched BMP tail is eligible; never select-all.
    events = []
    for _ in original:
        events += [INPUT(type=1, ki=KEYBDINPUT(0x08, 0, 0, 0, TAG)),
                   INPUT(type=1, ki=KEYBDINPUT(0x08, 0, 2, 0, TAG))]
    if replacement:
        units = replacement.encode("utf-16-le")
        for offset in range(0, len(units), 2):
            value = int.from_bytes(units[offset:offset+2], "little")
            events += [INPUT(type=1, ki=KEYBDINPUT(0, value, 4, 0, TAG)),
                       INPUT(type=1, ki=KEYBDINPUT(0, value, 6, 0, TAG))]
    batch = (INPUT*len(events))(*events)
    if user32.SendInput(len(events), batch, C.sizeof(INPUT)) != len(events):
        raise OSError("自动修正输入未完成，已停止继续改写。")
    return True


def parse_binding(value):
    parts = value.split("+")
    key = parts[-1]
    vk = key_vk(key)
    return key, vk, frozenset(parts[:-1])


class GlobalHooks:
    def __init__(self, events: queue.Queue, bindings):
        self.events = events
        self.bindings = [parse_binding(v) for v in bindings]
        self.held = set()
        self.suppressed_vks = {}
        self.thread = None
        self.thread_id = None
        self.ready = threading.Event()
        self.error = None
        self.enabled = True
        # No key contents are stored. The counter closes the gap between a real
        # edit reaching the hook and the Tk queue processing its notification.
        self.input_epoch = 0
        self.alt_key = None
        self.alt_forwarded = False
        self.tap_index = None
        self.tap_consumed = False
        self.passthrough_sidekeys = set()
        self.mouse_held = {}
        self.wheel_last = {}
        self.ui_windows=set()
        self.capture = None
        self.keyboard_cb = HOOKPROC(self._keyboard)
        self.mouse_cb = HOOKPROC(self._mouse)

    def configure(self, bindings):
        self.bindings = [parse_binding(v) for v in bindings]
        self.held.clear()
        self.suppressed_vks.clear()
        self.alt_key = None
        self.alt_forwarded = False
        self.tap_index = None
        self.tap_consumed = False
        self.mouse_held.clear()

    def begin_capture(self,window,token,cancel_rect):
        self.configure(["+".join([*sorted(mods),key]) for key,_,mods in self.bindings])
        self.capture={"window":window,"token":token,"recorder":BindingCapture(),"cancel_rect":cancel_rect}

    def cancel_capture(self):
        capture=self.capture
        self.capture=None
        if capture:self.events.put(("binding_cancelled",capture["token"]))

    def _capture_event(self,capture,result):
        if result is not None:
            self.capture=None
            self.events.put(("binding_captured",(capture["token"],*result)))
        else:
            self.events.put(("binding_preview",(capture["token"],capture["recorder"].preview())))

    def _mods(self):
        actual={name for name,key in (("ctrl",0x11),("alt",0x12),("shift",0x10),("win",0x5B))
                if user32.GetAsyncKeyState(key)&0x8000}
        if user32.GetAsyncKeyState(0x5C)&0x8000:actual.add("win")
        if self.alt_key is not None:actual.add(MODIFIER_VKS[self.alt_key])
        return frozenset(actual)

    def _match_key(self,vk,extended,mods):
        for i,(key,wanted,wanted_mods) in enumerate(self.bindings):
            if wanted is None or mods!=wanted_mods:continue
            if vk not in base_vks(key):continue
            if vk==13 and bool(extended)!=(key=="numpad_enter"):continue
            return i
        return None

    def _forward_alt_chord(self, following):
        key = self.alt_key
        extended = 1 if key in (0xA5,0x5B,0x5C,0xA3) else 0
        events = [INPUT(type=1, ki=KEYBDINPUT(key, 0, extended, 0, TAG)), following]
        batch = (INPUT*len(events))(*events)
        self.alt_forwarded = True
        user32.SendInput(len(events), batch, C.sizeof(INPUT))
        self.input_epoch += 1
        self.events.put(("manual_edit", None))

    @property
    def enabled(self):
        return self._enabled

    @enabled.setter
    def enabled(self, value):
        self._enabled = bool(value)
        if not value:
            self.held.clear()
            self.suppressed_vks.clear()
            if hasattr(self,"mouse_held"):self.mouse_held.clear()

    def _next(self, code, wparam, lparam):
        return user32.CallNextHookEx(None, code, wparam, lparam)

    def _keyboard(self, code, wparam, lparam):
        if code < 0:
            return self._next(code, wparam, lparam)
        data = C.cast(lparam, C.POINTER(KBDHOOK)).contents
        if data.dwExtraInfo == TAG:
            return self._next(code, wparam, lparam)
        down, up = wparam in (0x100, 0x104), wparam in (0x101, 0x105)
        vk = data.vkCode
        capture=self.capture
        if capture:
            if foreground()!=capture["window"]:self.cancel_capture()
            elif down or up:
                result=capture["recorder"].keyboard(vk,down,bool(data.flags&1))
                self._capture_event(capture,result)
                return 1
        if not self.enabled:return self._next(code,wparam,lparam)
        if up and vk in self.suppressed_vks:
            self.events.put(("hotkey_up", self.suppressed_vks.pop(vk)))
            return 1
        if self.alt_key is not None and vk==self.alt_key:
            if down:return self._next(code,wparam,lparam) if self.alt_forwarded else 1
            if up:
                forwarded,consumed,index=self.alt_forwarded,self.tap_consumed,self.tap_index
                self.alt_key=None;self.alt_forwarded=False;self.tap_consumed=False;self.tap_index=None
                if not forwarded:
                    if not consumed:
                        self.events.put(("hotkey",index));self.events.put(("hotkey_up",index))
                    return 1
                return self._next(code,wparam,lparam)
        if down:
            actual=self._mods()
            index=self._match_key(vk,bool(data.flags&1),actual)
            if self.alt_key is None and vk in MODIFIER_VKS and index is not None:
                self.alt_key=vk;self.tap_index=index;self.alt_forwarded=False;self.tap_consumed=False
                return 1
            if index is not None:
                if self.alt_key is not None and not self.alt_forwarded:self.tap_consumed=True
                if vk not in self.suppressed_vks:
                    self.suppressed_vks[vk]=index;self.events.put(("hotkey",index))
                return 1
            if self.alt_key is not None and not self.alt_forwarded:
                self._forward_alt_chord(INPUT(type=1,ki=KEYBDINPUT(vk,data.scanCode,data.flags&1,0,TAG)))
                return 1
            if vk not in (0x10, 0x11, 0x12, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0x5B, 0x5C):
                self.input_epoch += 1
                self.events.put(("manual_edit", None))
        return self._next(code, wparam, lparam)

    def _mouse(self, code, wparam, lparam):
        if code < 0:
            return self._next(code, wparam, lparam)
        data = C.cast(lparam, C.POINTER(MOUSEHOOK)).contents
        if data.dwExtraInfo == TAG:
            return self._next(code, wparam, lparam)
        buttons={0x201:("mouse_left",True),0x202:("mouse_left",False),0x204:("mouse_right",True),0x205:("mouse_right",False),
                 0x207:("mouse_middle",True),0x208:("mouse_middle",False)}
        if wparam in (0x20B,0x20C):
            buttons[wparam]=("x1" if ((data.mouseData>>16)&0xFFFF)==1 else "x2",wparam==0x20B)
        wheel=None
        if wparam in (0x20A,0x20E):
            positive=not ((data.mouseData>>16)&0x8000)
            wheel=("wheel_up" if positive else "wheel_down") if wparam==0x20A else ("wheel_right" if positive else "wheel_left")
        capture=self.capture
        if capture:
            bounds=W.RECT()
            inside=bool(user32.GetWindowRect(capture["window"],C.byref(bounds))) and bounds.left<=data.pt.x<bounds.right and bounds.top<=data.pt.y<bounds.bottom
            if foreground()!=capture["window"] or ((wparam in buttons or wheel) and not inside):self.cancel_capture()
            elif wparam in buttons or wheel:
                rect=capture["cancel_rect"]
                if wparam==0x201 and rect[0]<=data.pt.x<rect[2] and rect[1]<=data.pt.y<rect[3]:
                    self.cancel_capture();return 1
                recorder=capture["recorder"]
                if wheel:
                    recorder.mouse(wheel,True);result=recorder.mouse(wheel,False)
                else:
                    key,down=buttons[wparam];result=recorder.mouse(key,down)
                self._capture_event(capture,result)
                return 1
        if not self.enabled:return self._next(code,wparam,lparam)
        if wheel or wparam in buttons:
            key,down=(wheel,True) if wheel else buttons[wparam]
            if not down and key in self.mouse_held:
                index,passthrough=self.mouse_held.pop(key)
                if passthrough:self.events.put(("hotkey",index))
                self.events.put(("hotkey_up",index))
                return self._next(code,wparam,lparam) if passthrough else 1
            # Keep the program's own controls reachable even when ordinary
            # mouse buttons or the wheel are assigned as global triggers.
            own_ui=foreground() in self.ui_windows and key not in ("x1","x2")
            if down and not own_ui:
                actual=self._mods()
                for i,(wanted,_,mods) in enumerate(self.bindings):
                    if key!=wanted or actual!=mods:continue
                    if self.alt_key is not None and not self.alt_forwarded:self.tap_consumed=True
                    if wheel:
                        now=time.monotonic()
                        if now-self.wheel_last.get(i,0)>.45:
                            self.wheel_last[i]=now;self.events.put(("hotkey",i));self.events.put(("hotkey_up",i))
                        return 1
                    if key not in self.mouse_held:
                        passthrough=key=="mouse_left"
                        self.mouse_held[key]=(i,passthrough)
                        if not passthrough:self.events.put(("hotkey",i))
                    return self._next(code,wparam,lparam) if key=="mouse_left" else 1
        # A mouse chord also cancels an Alt tap; forward its original action.
        mouse_flags = {0x201:2,0x204:8,0x207:0x20,0x20B:0x80,0x20A:0x800,0x20E:0x1000}
        if self.alt_key is not None and not self.alt_forwarded and wparam in mouse_flags:
            extra = (data.mouseData>>16)&0xFFFF if wparam in (0x20B,0x20A,0x20E) else 0
            if wparam in (0x20A,0x20E) and extra&0x8000:extra |= 0xFFFF0000
            if wparam == 0x20B:self.passthrough_sidekeys.add(extra)
            self._forward_alt_chord(INPUT(type=0, mi=MOUSEINPUT(0,0,extra,mouse_flags[wparam],0,TAG)))
            return 1
        if wparam == 0x20C and ((data.mouseData>>16)&0xFFFF) in self.passthrough_sidekeys:
            self.passthrough_sidekeys.discard((data.mouseData>>16)&0xFFFF)
            return self._next(code,wparam,lparam)
        if wparam in (0x201, 0x204):
            self.input_epoch += 1
            self.events.put(("manual_edit", (data.pt.x, data.pt.y)))
        return self._next(code, wparam, lparam)

    def start(self):
        self.thread = threading.Thread(target=self._run, name="input-hooks", daemon=True)
        self.thread.start()
        self.ready.wait(3)
        if self.error:
            raise self.error
        if not self.ready.is_set():
            raise RuntimeError("快捷键监听启动超时")

    def _run(self):
        self.thread_id = kernel32.GetCurrentThreadId()
        module = kernel32.GetModuleHandleW(None)
        mouse = user32.SetWindowsHookExW(14, self.mouse_cb, module, 0)
        keyboard = user32.SetWindowsHookExW(13, self.keyboard_cb, module, 0)
        if not mouse or not keyboard:
            self.error = C.WinError(C.get_last_error())
        self.ready.set()
        try:
            if self.error:
                return
            msg = W.MSG()
            while user32.GetMessageW(C.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(C.byref(msg))
                user32.DispatchMessageW(C.byref(msg))
        finally:
            if mouse:
                user32.UnhookWindowsHookEx(mouse)
            if keyboard:
                user32.UnhookWindowsHookEx(keyboard)

    def stop(self):
        if self.thread_id:
            user32.PostThreadMessageW(self.thread_id, 0x12, 0, 0)
        if self.thread:
            self.thread.join(2)
