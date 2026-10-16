"""Drag only our floating window, keeping the active dictation field focused."""
import ctypes as C
from ctypes import wintypes as W


def clamp_position(position, size, area):
    x, y = position
    width, height = size
    left, top, right, bottom = area
    return (max(left, min(x, max(left, right - width))),
            max(top, min(y, max(top, bottom - height))))


def work_area(widget, position=None, size=(1, 1)):
    """Nearest monitor's usable area, including monitors left of the primary."""
    try:
        api = C.WinDLL('user32', use_last_error=True)
        if position is None:
            rect = W.RECT()
            if api.SystemParametersInfoW(0x30, 0, C.byref(rect), 0):
                return rect.left, rect.top, rect.right, rect.bottom
        else:
            class MonitorInfo(C.Structure):
                _fields_ = [('cbSize', W.DWORD), ('rcMonitor', W.RECT),
                            ('rcWork', W.RECT), ('dwFlags', W.DWORD)]
            api.MonitorFromRect.argtypes = [C.POINTER(W.RECT), W.DWORD]
            api.MonitorFromRect.restype = W.HANDLE
            api.GetMonitorInfoW.argtypes = [W.HANDLE, C.POINTER(MonitorInfo)]
            rect = W.RECT(position[0], position[1], position[0] + size[0], position[1] + size[1])
            monitor = api.MonitorFromRect(C.byref(rect), 2)
            info = MonitorInfo()
            info.cbSize = C.sizeof(info)
            if monitor and api.GetMonitorInfoW(monitor, C.byref(info)):
                rect = info.rcWork
                return rect.left, rect.top, rect.right, rect.bottom
    except (OSError, AttributeError):
        pass
    return 0, 0, widget.winfo_screenwidth(), widget.winfo_screenheight()


class FloatingHud:
    def __init__(self, widget, handles, position, save, width, margin):
        self.widget = widget
        self.save = save
        self.width = width
        self.margin = margin
        self.position = position
        self.placed_position = None
        self.anchor = None
        self.reflow_pending = False
        for handle in handles:
            handle.configure(cursor='fleur')
            handle.bind('<ButtonPress-1>', self.start)
            handle.bind('<B1-Motion>', self.drag)
            handle.bind('<ButtonRelease-1>', self.end)
            handle.bind('<Double-Button-1>', self.reset)
        widget.bind('<Configure>', self.reflow, add='+')
        self.place()

    def place(self):
        self.widget.update_idletasks()
        size = (self.width, self.widget.winfo_reqheight())
        area = work_area(self.widget, self.position, size)
        position = self.position
        if position is None:
            position = (area[2] - size[0] - self.margin,
                        area[3] - size[1] - self.margin)
        self.position = clamp_position(position, size, area)
        # A literal + before each coordinate makes negative coordinates absolute
        # rather than Tk's distance-from-right/bottom geometry notation.
        if self.position != self.placed_position:
            self.placed_position = self.position
            self.widget.geometry(f'+{self.position[0]}+{self.position[1]}')

    def start(self, event):
        self.anchor = (event.x_root, event.y_root,
                       self.widget.winfo_x(), self.widget.winfo_y())
        return 'break'

    def drag(self, event):
        if self.anchor is not None:
            sx, sy, wx, wy = self.anchor
            self.position = (wx + event.x_root - sx, wy + event.y_root - sy)
            self.place()
        return 'break'

    def end(self, event):
        if self.anchor is not None:
            moved = self.position != self.anchor[2:]
            self.anchor = None
            if moved:
                self.save(list(self.position))
        return 'break'

    def reset(self, event=None):
        self.anchor = None
        self.position = None
        self.place()
        self.save(None)
        return 'break'

    def reflow(self, event):
        if event.widget is not self.widget or self.reflow_pending:
            return
        self.reflow_pending = True
        def adjust():
            self.reflow_pending = False
            if self.widget.winfo_exists():
                self.place()
        self.widget.after_idle(adjust)
