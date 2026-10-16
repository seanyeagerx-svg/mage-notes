"""A small, paged material library; saving requires an explicit user selection."""
import tkinter as tk
from tkinter import ttk
from datetime import datetime
from reuse_library import store

def collect(app,text=None,source_draft=None,source_date=''):
    if app.state.mode!='idle' or app.stopping:
        app.status.set('结束录音后再收为素材，当前文字保留。');return
    if text is None:
        widget=app.dict_text
        text=widget.get('sel.first','sel.last') if widget.tag_ranges('sel') else widget.get('1.0','end-1c')
        if not app.archive_draft('manual'):return
        if app.history is not None:
            source_draft=app.history.archive(app.draft_data(),app.personal.active,'manual')
        source_date=datetime.now().astimezone().isoformat(timespec='seconds')
    if not text.strip():app.status.set('先选择或输入要留作素材的文字。');return
    profile=app.personal.active
    from draft_ui import fit_dialog
    from ui import wrapped
    window=tk.Toplevel(app.root);window.title('收为复用素材')
    body=ttk.Frame(window,padding=18);body.pack(fill='both',expand=True)
    title=tk.StringVar(value=text.strip().splitlines()[0][:36]);purpose=tk.StringVar()
    ttk.Label(body,text='标题').pack(anchor='w');ttk.Entry(body,textvariable=title).pack(fill='x',pady=(4,12))
    ttk.Label(body,text='准备在哪些任务中使用').pack(anchor='w');ttk.Entry(body,textvariable=purpose).pack(fill='x',pady=(4,12))
    preview=tk.Text(body,height=10,wrap='word',font=('Microsoft YaHei UI',11));preview.pack(fill='both',expand=True)
    preview.insert('1.0',text);preview.configure(state='disabled')
    notice=tk.StringVar(value='保存原文和来源，标为待核对；不会自动改写或加入纠错规则。')
    ttk.Label(body,textvariable=notice,wraplength=580).pack(fill='x',pady=10)
    def save():
        if profile!=app.personal.active:notice.set('词库已切换，请重新收集这份素材。');return
        try:
            key,created=store(app).collect(profile,title.get(),purpose.get(),text,source_draft,source_date)
            app.status.set('已收为素材 · 可在“素材库”复制或导出' if created else '这份文字已在素材库中，已避免重复保存')
            window.destroy()
            if hasattr(app,'library_page'):app.library_page.refresh()
        except Exception as exc:notice.set('保存未完成，原文保留：'+str(exc))
    ttk.Button(body,text='保存素材',command=save).pack(anchor='e');fit_dialog(window,650,510)

class LibraryPage:
    def __init__(self,app,parent):
        from ui import card,WHITE,wrapped
        self.app=app;self.offset=0;self.profile=None
        self.page=tk.Frame(parent,bg=WHITE)
        app.tabs.add(self.page,'素材库','把有用的话留作素材，带着来源复制到下一个任务。')
        body=card(self.page,fill='both',expand=True)
        inner=tk.Frame(body,bg=WHITE,padx=18,pady=16);inner.pack(fill='both',expand=True)
        searchrow=ttk.Frame(inner);searchrow.pack(fill='x')
        self.search=tk.StringVar()
        entry=ttk.Entry(searchrow,textvariable=self.search);entry.pack(side='left',fill='x',expand=True)
        entry.bind('<Return>',lambda _:self.refresh(reset=True))
        ttk.Button(searchrow,text='搜索素材',command=lambda:self.refresh(reset=True)).pack(side='left',padx=6)
        table_frame=ttk.Frame(inner);table_frame.pack(fill='x',pady=12)
        self.table=ttk.Treeview(table_frame,columns=('title','purpose','state'),show='headings',height=4,selectmode='browse')
        for col,title,width in [('title','素材',230),('purpose','用途',190),('state','状态',75)]:
            self.table.heading(col,text=title);self.table.column(col,width=width,minwidth=65)
        bar=ttk.Scrollbar(table_frame,command=self.table.yview);bar.pack(side='right',fill='y')
        self.table.configure(yscrollcommand=bar.set)
        self.table.pack(side='left',fill='x',expand=True);self.table.bind('<<TreeviewSelect>>',self.display)
        self.preview=tk.Text(inner,height=7,wrap='word',state='disabled',font=('Microsoft YaHei UI',11),relief='flat',bd=0,padx=6,pady=8,bg=WHITE,fg='#243237')
        self.preview.pack(fill='both',expand=True)
        controls=ttk.Frame(inner);controls.pack(fill='x',pady=(12,0))
        self.prev=ttk.Button(controls,text='上一页',command=lambda:self.change(-50));self.prev.pack(side='left')
        self.next=ttk.Button(controls,text='下一页',command=lambda:self.change(50));self.next.pack(side='left',padx=6)
        ttk.Button(controls,text='导出 Markdown',command=self.export).pack(side='right')
        ttk.Button(controls,text='复制素材',command=self.copy).pack(side='right',padx=6)
        self.notice=tk.StringVar(value='在听写草稿或草稿历史中，点“收为素材”。原稿全部保留。')
        wrapped(inner,variable=self.notice,fill='x',pady=(10,0))
        self.page.on_show=lambda:self.refresh()

    def refresh(self,reset=False):
        if reset or self.profile!=self.app.personal.active:self.offset=0
        self.profile=self.app.personal.active
        try:
            rows=store(self.app).rows(self.profile,self.offset,50,self.search.get())
            self.table.delete(*self.table.get_children())
            for row in rows:self.table.insert('','end',iid=str(row['id']),values=(row['title'],row['purpose'],row['status']))
            self.prev.state(['disabled'] if self.offset==0 else ['!disabled'])
            self.next.state(['disabled'] if len(rows)<50 else ['!disabled'])
            self.preview.configure(state='normal');self.preview.delete('1.0','end');self.preview.configure(state='disabled')
            self.notice.set(f'第 {self.offset//50+1} 页 · {len(rows)} 份素材 · 原文与来源已保留，使用前核对' if rows else '还没有素材。在听写草稿或草稿历史中，点“收为素材”。')
        except Exception:self.notice.set('素材库暂不可读；原草稿保留。')

    def change(self,step):self.offset=max(0,self.offset+step);self.refresh()
    def selected(self):
        selection=self.table.selection()
        if self.profile!=self.app.personal.active:raise ValueError('词库已切换，请刷新素材库')
        return int(selection[0]) if selection else None
    def display(self,event=None):
        try:
            key=self.selected()
            if key is None:return
            text=store(self.app).document(key,self.profile)
            self.preview.configure(state='normal');self.preview.delete('1.0','end');self.preview.insert('1.0',text);self.preview.configure(state='disabled')
        except Exception:self.notice.set('暂时无法显示素材；原文保留。')
    def copy(self):
        try:
            key=self.selected()
            if key is None:self.notice.set('先选择一份素材。');return
            text=store(self.app).document(key,self.profile)
            self.app.root.clipboard_clear();self.app.root.clipboard_append(text)
            self.notice.set('已复制原文和来源，可贴到下一个任务。')
        except Exception:self.notice.set('复制未完成；素材保留。')
    def export(self):
        try:
            key=self.selected()
            if key is None:self.notice.set('先选择一份素材。');return
            path=store(self.app).export(key,self.profile);self.notice.set('已导出：'+str(path))
        except Exception:self.notice.set('导出未完成；素材保留。')
