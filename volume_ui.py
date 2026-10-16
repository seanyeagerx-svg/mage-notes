"""Two draggable limits; changes apply during recording and save locally."""
import tkinter as tk
from tkinter import ttk


def build(app, parent):
    from ui import wrapped
    app.volume_enabled = tk.BooleanVar(value=app.config.get('volume_filter',False))
    app.volume_low = tk.DoubleVar(value=(app.config.get('volume_min_db',-65)+80)/.8)
    app.volume_high = tk.DoubleVar(value=(app.config.get('volume_max_db',-4)+80)/.8)
    app.volume_range_hint = tk.StringVar()
    app.volume_meter = tk.DoubleVar(value=0)
    app.volume_meter_hint = tk.StringVar(value='未收音')
    ttk.Checkbutton(parent,text='只收录音量区间内的声音',variable=app.volume_enabled,
                    command=lambda:changed(app,None)).pack(anchor='w',pady=(10,3))
    for title,variable,which in (('下限',app.volume_low,'low'),('上限',app.volume_high,'high')):
        row=ttk.Frame(parent);row.pack(fill='x',pady=3)
        ttk.Label(row,text=title,width=5).pack(side='left')
        ttk.Scale(row,from_=0,to=100,variable=variable,
                  command=lambda value,w=which:changed(app,w)).pack(side='left',fill='x',expand=True)
    wrapped(parent,variable=app.volume_range_hint,fill='x',pady=(2,4))
    ttk.Progressbar(parent,maximum=100,variable=app.volume_meter).pack(fill='x')
    wrapped(parent,variable=app.volume_meter_hint,fill='x',pady=(3,4))
    wrapped(parent,'刻度越大，声音越响。上下限按原始收音判断，区间外暂停采入。',fill='x',pady=(2,8))
    update_range(app)


def update_range(app):
    app.volume_range_hint.set(f'收音区间：{app.volume_low.get():.0f} — {app.volume_high.get():.0f}')


def changed(app, which):
    low,high=app.volume_low.get(),app.volume_high.get()
    if high-low < 1:
        if which == 'low':
            low=min(low,99);high=low+1
        else:
            high=max(high,1);low=high-1
        app.volume_low.set(low);app.volume_high.set(high)
    update_range(app)
    app.config.update(volume_filter=app.volume_enabled.get(),volume_min_db=low*.8-80,volume_max_db=high*.8-80)
    if hasattr(app,'engine'):
        app.engine.command('volume_window',enabled=app.config['volume_filter'],
                           minimum=app.config['volume_min_db'],maximum=app.config['volume_max_db'])
    timer=getattr(app,'volume_save_timer',None)
    if timer:
        app.root.after_cancel(timer)
    def save():
        from settings import save_settings
        from app import ROOT
        app.volume_save_timer=None
        try:save_settings(ROOT/'settings.json',app.config)
        except OSError:app.volume_meter_hint.set('音量范围尚未保存，当前调节仍生效。')
    app.volume_save_timer=app.root.after(350,save)


def refresh(app):
    if not hasattr(app,'volume_meter'):
        return
    engine=app.engine
    if getattr(engine,'recording',None) is None:
        value,text=0,'未收音'
    else:
        db=getattr(engine,'raw_input_db',-80)
        value=max(0,min(100,round((db+80)/.8)))
        state=('正在采入' if getattr(engine,'volume_accepted',True) else '区间外 · 暂停采入') if app.config.get('volume_filter') else '区间筛选关闭'
        text=f'当前音量：{value} · {state}'
    if app.volume_meter.get()!=value:app.volume_meter.set(value)
    if app.volume_meter_hint.get()!=text:app.volume_meter_hint.set(text)
