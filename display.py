"""Crisp native Tk text at the system's display scale, without bitmap stretching.

Tk font sizes are points and already scale. Only pixel-based layout distances
are scaled here, once. Character widths and text line counts stay unchanged.
"""
import ctypes
import tkinter as tk
from tkinter import ttk

SCALE = 1.0


def enable_dpi_awareness():
    global SCALE
    try:
        user = ctypes.WinDLL('user32',use_last_error=True)
        user.SetProcessDpiAwarenessContext.argtypes=[ctypes.c_void_p]
        user.SetProcessDpiAwarenessContext.restype=ctypes.c_bool
        # System awareness matches Tk 8.6's point-font model. Do not claim
        # dynamic mixed-DPI monitor support without a complete relayout.
        user.SetProcessDpiAwarenessContext(ctypes.c_void_p(-2))
        user.GetDpiForSystem.restype=ctypes.c_uint
        SCALE = max(1.0,user.GetDpiForSystem()/96)
    except (AttributeError,OSError):
        SCALE=1.0
    return SCALE


def px(value):
    if isinstance(value,(tuple,list)):
        return tuple(px(v) for v in value)
    return round(float(value)*SCALE)


def scale_layout(parent):
    if SCALE == 1:
        return
    def distances(value):
        parts=(value,) if isinstance(value,(int,float)) else value if isinstance(value,(tuple,list)) else parent.tk.splitlist(value)
        return tuple(px(v) for v in parts)
    def walk(widget):
        keys=set(widget.keys())
        options={}
        for key in ('padx','pady','borderwidth','highlightthickness','insertwidth','spacing1','spacing2','spacing3','wraplength'):
            if key in keys:
                try:options[key]=px(widget.cget(key))
                except (ValueError,TypeError):pass
        if isinstance(widget,(tk.Frame,tk.Canvas)):
            for key in ('width','height'):
                options[key]=px(widget.cget(key))
        if options:widget.configure(**options)
        manager=widget.winfo_manager()
        if manager in ('pack','grid'):
            info=widget.pack_info() if manager=='pack' else widget.grid_info()
            values={key:distances(info[key]) for key in ('padx','pady','ipadx','ipady') if key in info}
            (widget.pack_configure if manager=='pack' else widget.grid_configure)(**values)
        if isinstance(widget,ttk.Treeview):
            for col in widget['columns']:
                widget.column(col,width=px(widget.column(col,'width')),minwidth=px(widget.column(col,'minwidth')))
        if isinstance(widget,tk.Canvas):
            for item in widget.find_all():
                if widget.type(item) not in ('window','image'):
                    widget.coords(item,*[px(c) for c in widget.coords(item)])
                    if 'width' in widget.itemconfigure(item):
                        widget.itemconfigure(item,width=px(widget.itemcget(item,'width')))
        for child in widget.winfo_children():walk(child)
    walk(parent)


def scale_styles(root):
    if SCALE == 1:return
    style=ttk.Style(root)
    names=('.', 'TButton','Primary.TButton','Quiet.TButton','Danger.TButton','Book.TButton','Nav.TButton',
           'TCheckbutton','TEntry','TCombobox','Vertical.TScrollbar','Treeview','Treeview.Heading','TNotebook','TNotebook.Tab')
    for name in names:
        config=style.configure(name) or {}
        for key in ('padding','tabmargins','rowheight','arrowsize','borderwidth','focusthickness'):
            if key not in config:continue
            values=(config[key],) if isinstance(config[key],(int,float)) else root.tk.splitlist(config[key])
            style.configure(name,**{key:tuple(px(v) for v in values)})
