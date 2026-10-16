"""User-facing local history and explicit human review of an old draft."""
import tkinter as tk
from tkinter import ttk
import hashlib
import json
from learning_status import record
from display import px,scale_layout


def open_documents(app):
    import os
    try:
        if not app.archive_draft('manual'):
            return
        folder=app.history.folder(app.personal.active)
        folder.mkdir(parents=True,exist_ok=True)
        os.startfile(folder)
        app.status.set('自动留存已打开：可以直接复制文字，或把文档当作素材使用。')
    except (OSError,ValueError):
        app.status.set('留存文件暂不可打开；当前草稿仍可复制，原历史保留。')


def fit_dialog(window, width, height):
    window.geometry(f'{px(width)}x{px(height)}')
    window.minsize(px(600),px(420))
    scale_layout(window)


def idle(app):
    if app.state.mode!='idle' or app.stopping:
        app.status.set('结束当前会话后再整理草稿。')
        return False
    return True


def restore(app, data):
    if not idle(app) or not app.archive_draft('before_restore'):
        return False
    app.learning.reset();app._forget_owned()
    app.restore_draft(data);app._save_draft()
    app.status.set('草稿已恢复；替换前的内容也保存在历史中。')
    return True


def show_history(app):
    if not idle(app):return
    if not app.archive_draft('manual'):return
    profile=app.personal.active
    window=tk.Toplevel(app.root);window.title('草稿历史 · 仅存本机')
    body=ttk.Frame(window,padding=16);body.pack(fill='both',expand=True)
    body.columnconfigure(0,weight=1);body.rowconfigure(2,weight=1)
    searchrow=ttk.Frame(body);searchrow.grid(row=0,column=0,sticky='ew',pady=(0,10))
    search=tk.StringVar();search_input=ttk.Entry(searchrow,textvariable=search)
    search_input.pack(side='left',fill='x',expand=True)
    table=ttk.Treeview(body,columns=('time','reason','preview'),show='headings',height=5,selectmode='browse')
    for col,title,width in [('time','保存时间',175),('reason','来源',100),('preview','内容',400)]:
        table.heading(col,text=title);table.column(col,width=width)
    table.grid(row=1,column=0,sticky='ew')
    reasons={'startup':'此前草稿','session':'会话结束','close':'关闭程序','before_clear':'新建前',
             'before_restore':'恢复前','manual':'手动归档','human_review':'亲手校订','before_external_session':'上次外部输入'}
    offset=0
    page_size=50
    page_label=tk.StringVar()
    notice=tk.StringVar(value='按会话自动留存；可恢复编辑、复制使用。只有亲手确认的改词才会进入词库。')
    preview=tk.Text(body,wrap='word',font=('Microsoft YaHei UI',11),height=13)
    preview.grid(row=2,column=0,sticky='nsew',pady=10)
    preview.configure(state='disabled')
    def load(step=0,reset=False):
        nonlocal offset
        try:
            if profile!=app.personal.active:
                notice.set('词库已切换，请重新打开历史。');return
            offset=0 if reset else max(0,offset+step)
            rows=app.history.rows(profile,limit=page_size,offset=offset,search=search.get())
            table.delete(*table.get_children())
            for r in rows:
                table.insert('','end',iid=str(r['id']),values=(r['created'][:19].replace('T',' '),reasons.get(r['reason'],'归档'),r['preview']))
            total=app.history.count(profile,search.get())
            page_label.set(f'第 {offset//page_size+1} 页 · 共 {total} 份')
            prev_button.state(['disabled'] if offset==0 else ['!disabled'])
            next_button.state(['disabled'] if offset+len(rows)>=total else ['!disabled'])
            preview.configure(state='normal');preview.delete('1.0','end');preview.configure(state='disabled')
        except Exception:notice.set('暂时无法读取草稿历史；原文件保留。')
    ttk.Button(searchrow,text='搜索草稿',command=lambda:load(reset=True)).pack(side='left',padx=6)
    search_input.bind('<Return>',lambda _:load(reset=True))
    def selected():
        ids=table.selection()
        if profile!=app.personal.active:return None
        return app.history.get(int(ids[0]),profile) if ids else None
    def display(event=None):
        data=selected()
        if data is None:return
        preview.configure(state='normal');preview.delete('1.0','end')
        preview.insert('1.0',data['dictation']+'\n\n—— 工作流 ——\n'+data['workflow']+
            ('\n\n参考图：\n'+'\n'.join(data['refs']) if data['refs'] else ''))
        preview.configure(state='disabled')
    table.bind('<<TreeviewSelect>>',display)
    def reuse():
        if profile!=app.personal.active:
            notice.set('个人词库已切换，请重新打开历史。');return
        data=selected()
        if data is not None and restore(app,data):window.destroy()
    pager=ttk.Frame(body);pager.grid(row=3,column=0,sticky='ew',pady=(0,8))
    prev_button=ttk.Button(pager,text='上一页',command=lambda:load(-page_size));prev_button.pack(side='left')
    next_button=ttk.Button(pager,text='下一页',command=lambda:load(page_size));next_button.pack(side='left',padx=6)
    ttk.Label(pager,textvariable=page_label).pack(side='left',padx=8)
    controls=ttk.Frame(body);controls.grid(row=4,column=0,sticky='ew')
    ttk.Button(controls,text='留存文件夹',command=lambda:open_documents(app)).pack(side='left')
    def export():
        ids=table.selection()
        if not ids or profile!=app.personal.active:notice.set('先选择一份草稿。');return
        try:notice.set('已导出这份草稿：'+str(app.history.export(int(ids[0]),profile)))
        except Exception:notice.set('导出未完成，原历史保留。')
    ttk.Button(controls,text='导出选中稿',command=export).pack(side='left',padx=6)
    def collect_selected():
        data=selected()
        if data is None:notice.set('先选择一份草稿。');return
        from library_ui import collect
        key=int(table.selection()[0]);values=table.item(str(key),'values')
        text=data['dictation']+('\n\n—— 工作流 ——\n'+data['workflow'] if data['workflow'] else '')
        collect(app,text,source_draft=key,source_date=values[0])
    ttk.Button(controls,text='收为素材',command=collect_selected).pack(side='left')
    ttk.Button(controls,text='恢复这份草稿',command=reuse).pack(side='right')
    ttk.Label(body,textvariable=notice,wraplength=760).grid(row=5,column=0,sticky='ew',pady=(10,0))
    # Inspection handles for isolated native UI checks; no retained page data.
    window.history_table=table;window.history_load=load;window.history_search=search
    fit_dialog(window,840,620)
    load()


def save_review(app, frozen, before, after, event):
    """A human review teaches only selected replacements and never creates audio."""
    profile, draft, start, end=frozen
    if not idle(app) or profile!=app.personal.active or draft!=app.draft_data():
        raise ValueError('当前草稿或词库已改变，请重新选中文字。')
    if not isinstance(start,int) or not isinstance(end,int) or not 0<=start<end<=len(draft['dictation']) or draft['dictation'][start:end]!=before:
        raise ValueError('所选文字已变化，请重新选取。')
    if before==after:raise ValueError('还没有修改文字。')
    if not after.strip() or len(after)>64000:raise ValueError('请保留需要校订的文字；清空不作为改词样本。')
    if not app.archive_draft('manual'):raise ValueError('原稿未能归档，未替换当前文字。')
    data=dict(draft)
    data['dictation']=draft['dictation'][:start]+after+draft['dictation'][end:]
    # Persist both texts before replacing the editor. No inferred/AI correction is used.
    app.history.archive(data,profile,'human_review')
    # A selected word still needs its real neighbouring words for context rules.
    # Keep at most 16 characters on each side; the full draft is never a label.
    left=draft['dictation'][max(0,start-16):start]
    right=draft['dictation'][end:end+16]
    learned=app.personal.observe_edit(left+before+right,left+after+right,event)
    app.proofreading.human_edit(profile,left+before+right,left+after+right)
    record(app.personal,'human_edit')
    record(app.personal,'edit_without_audio')
    app.learning.reset();app._forget_owned();app.restore_draft(data);app._save_draft()
    if hasattr(app.learning,'hint'):
        app.learning.hint('已记录你的亲手改词 · 记录与启用状态可在“我的用语”查看')
    app._sync_recognition_context(after[-160:])
    return learned


def review(app):
    if not idle(app):return
    widget=app.dict_text;draft=app.draft_data()
    if widget.tag_ranges('sel'):
        start=len(widget.get('1.0','sel.first'));end=len(widget.get('1.0','sel.last'))
    else:start,end=0,len(draft['dictation'])
    before=draft['dictation'][start:end]
    if not before.strip():app.status.set('先在草稿中选择需要改词的那句话。');return
    if len(before)>2000:
        app.status.set('先选中需要改词的一句话或一小段，再点“改词入库”。');return
    frozen=(app.personal.active,draft,start,end)
    event='draft-review:'+hashlib.sha256(json.dumps((frozen[0],draft['dictation'],start,end),ensure_ascii=False).encode()).hexdigest()
    window=tk.Toplevel(app.root);window.title('亲手改词入库')
    body=ttk.Frame(window,padding=16);body.pack(fill='both',expand=True)
    ttk.Label(body,text='把这里的错词改对，再保存。同一句反复保存只算一次；原稿会保留。',wraplength=690).pack(fill='x')
    original=tk.Text(body,height=5,wrap='word',font=('Microsoft YaHei UI',11));original.pack(fill='x',pady=8)
    original.insert('1.0',before);original.configure(state='disabled')
    edited=tk.Text(body,height=8,wrap='word',font=('Microsoft YaHei UI',11),undo=True);edited.pack(fill='both',expand=True)
    edited.insert('1.0',before)
    notice=tk.StringVar(value='只记录你在此处亲手修改的词；旧草稿不会生成录音样本。')
    ttk.Label(body,textvariable=notice,wraplength=690).pack(fill='x',pady=8)
    def save():
        try:
            learned=save_review(app,frozen,before,edited.get('1.0','end-1c'),event)
            app.status.set(f'校订已保存，记录了 {len(learned)} 处改词；可在“我的用语”查看。' if learned else
                '校订已保存；未形成可学习的短词替换。大段重写、排版和标点不会自动成为改词规则。')
            window.destroy()
        except Exception as exc:notice.set(str(exc))
    ttk.Button(body,text='保存校订并记录改词',command=save).pack(anchor='e')
    fit_dialog(window,760,570)
