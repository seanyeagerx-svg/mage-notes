"""Reviewable local vocabulary; switching profiles never copies another's words."""
import tkinter as tk
from tkinter import ttk, simpledialog, messagebox, filedialog
from ui import ScrollPane, card, label, wrapped, rule, WHITE, MUTED, INK, BLUE, FONT


class PersonalPage:
    def __init__(self, app, parent):
        self.app, self.store = app, app.personal
        self.seen_revision = -1
        frame = card(parent,fill='both',expand=True)
        scroll = ScrollPane(frame)
        scroll.pack(fill='both',expand=True)
        body = scroll.content
        body.configure(padx=22,pady=18)
        self.summary = tk.StringVar()
        profile_row = tk.Frame(body,bg=WHITE)
        profile_row.pack(fill='x')
        self.profile = tk.StringVar()
        self.profile_box = ttk.Combobox(profile_row,textvariable=self.profile,state='readonly',width=19)
        self.profile_box.pack(side='left',fill='x',expand=True)
        self.profile_box.bind('<<ComboboxSelected>>',self.switch)
        ttk.Button(profile_row,text='新建词库',command=self.create).pack(side='left',padx=(10,5))
        ttk.Button(profile_row,text='改名',style='Quiet.TButton',command=self.rename).pack(side='left')
        transfer=tk.Frame(body,bg=WHITE);transfer.pack(fill='x',pady=(8,0))
        ttk.Button(transfer,text='导出词库',command=self.export_book).pack(side='left')
        ttk.Button(transfer,text='导入词库',command=self.import_book).pack(side='left',padx=6)
        ttk.Button(transfer,text='学习状态',command=self.show_learning).pack(side='right')
        label(body,variable=self.summary,size=9,color=MUTED,anchor='w',pady=(10,6))
        self.learning = tk.BooleanVar()
        ttk.Checkbutton(body,text='学习我亲手改过的词',variable=self.learning,command=self.toggle_learning).pack(anchor='w',pady=(3,2))
        wrapped(body,'同音错字在同一语境改两次才启用；改回原词就停用。',fill='x',pady=(2,14))
        self.audio_learning=tk.BooleanVar(value=self.app.config.get('audio_learning',False))
        ttk.Checkbutton(body,text='改词时保存对应短录音，仅存本机',variable=self.audio_learning,
                        command=self.toggle_audio_learning).pack(anchor='w')
        sample_row=tk.Frame(body,bg=WHITE);sample_row.pack(fill='x',pady=(5,12))
        self.audio_summary=tk.StringVar()
        label(sample_row,variable=self.audio_summary,size=9,color=MUTED,side='left')
        ttk.Button(sample_row,text='查看发音样本',command=self.show_samples).pack(side='right')

        bookbar=tk.Frame(body,bg=WHITE)
        bookbar.pack(fill='x',pady=(0,12))
        books = tk.Frame(body,bg=WHITE)
        books.pack(fill='both',expand=True)
        rules = tk.Frame(books,bg=WHITE,padx=0,pady=0)
        phrases = tk.Frame(books,bg=WHITE,padx=0,pady=0)
        self.book_pages=[]
        for page,title in ((rules,'改词记录'),(phrases,'常用表达')):
            button=ttk.Button(bookbar,text=title,style='Book.TButton',command=lambda p=page:self.select_book(p))
            button.pack(side='left',padx=(0,6))
            self.book_pages.append((page,button))
        self.select_book(rules)
        rule_list = tk.Frame(rules,bg=WHITE)
        rule_list.pack(fill='x')
        self.rules = ttk.Treeview(rule_list,columns=('source','target','status'),show='headings',height=4,selectmode='browse')
        for col,title,width in [('source','识别成',150),('target','我想说',150),('status','状态',145)]:
            self.rules.heading(col,text=title,anchor='w');self.rules.column(col,width=width,minwidth=65,stretch=True,anchor='w')
        rule_scroll = ttk.Scrollbar(rule_list,command=self.rules.yview)
        rule_scroll.pack(side='right',fill='y')
        self.rules.configure(yscrollcommand=rule_scroll.set)
        self.rules.pack(fill='both',expand=True)
        self.rules.bind('<<TreeviewSelect>>',self.detail)
        self.rule_detail = tk.StringVar(value='选择一条，查看完整改法。')
        wrapped(rules,variable=self.rule_detail,fill='x',pady=12)
        actions = tk.Frame(rules,bg=WHITE)
        actions.pack(fill='x')
        ttk.Button(actions,text='启用',command=lambda:self.status('active')).pack(side='left')
        ttk.Button(actions,text='停用',command=lambda:self.status('disabled')).pack(side='left',padx=5)
        ttk.Button(actions,text='删除',style='Danger.TButton',command=self.delete_rule).pack(side='right')

        phrase_list = tk.Frame(phrases,bg=WHITE)
        phrase_list.pack(fill='x')
        self.phrases = ttk.Treeview(phrase_list,columns=('text','count','protect'),show='headings',height=4,selectmode='browse')
        for col,title,width in [('text','短语',295),('count','次数',60),('protect','保留原词',85)]:
            self.phrases.heading(col,text=title,anchor='w');self.phrases.column(col,width=width,minwidth=50,stretch=True)
        phrase_scroll = ttk.Scrollbar(phrase_list,command=self.phrases.yview)
        phrase_scroll.pack(side='right',fill='y')
        self.phrases.configure(yscrollcommand=phrase_scroll.set)
        self.phrases.pack(fill='both',expand=True)
        phrase_actions = tk.Frame(phrases,bg=WHITE)
        phrase_actions.pack(fill='x',pady=8)
        ttk.Button(phrase_actions,text='保留原词',command=self.protect).pack(side='left')
        ttk.Button(phrase_actions,text='删除',style='Danger.TButton',command=self.delete_phrase).pack(side='right')
        wrapped(phrases,'常用表达只记录次数，不会自动改成别的词。',fill='x',pady=(0,8))

        rule(body,pady=16)
        label(body,'教它一个词',size=11,bold=True,anchor='w',pady=(0,8))
        add = tk.Frame(body,bg=WHITE)
        add.pack(fill='x')
        add.columnconfigure(0,weight=1);add.columnconfigure(2,weight=1)
        self.source,self.target = tk.StringVar(),tk.StringVar()
        ttk.Entry(add,textvariable=self.source,width=15).grid(row=0,column=0,sticky='ew')
        tk.Label(add,text=' → ',bg=WHITE,fg=MUTED,font=(FONT,11)).grid(row=0,column=1)
        ttk.Entry(add,textvariable=self.target,width=15).grid(row=0,column=2,sticky='ew')
        ttk.Button(add,text='记住',command=self.add).grid(row=0,column=3,padx=(6,0))
        label(body,'识别出的词 → 正确说法',size=9,color=MUTED,anchor='w',pady=(7,14))
        footer = tk.Frame(body,bg=WHITE)
        footer.pack(fill='x')
        ttk.Button(footer,text='清空这份词库',style='Danger.TButton',command=self.clear).pack(side='right')
        self.notice = tk.StringVar(value='词库文件暂不可写，本次只在内存中。' if self.store.error else '仅保存在本机；不同人的词库互不混用。')
        wrapped(footer,variable=self.notice,side='left',fill='x',expand=True)
        parent.on_show=lambda:self.refresh()
        self.refresh(force=True)

    def select_book(self, selected):
        for page,button in self.book_pages:
            page.pack_forget()
            button.configure(style='Selected.Book.TButton' if page is selected else 'Book.TButton')
        selected.pack(fill='both',expand=True)

    def idle(self):
        if self.app.state.mode != 'idle' or self.app.stopping:
            self.notice.set('结束当前会话后再管理词库。')
            return False
        return True

    def run(self, action):
        if not self.idle():
            self.refresh(force=True)
            return
        try:
            action()
            self.app.learning.reset()
            self.app._forget_owned()
            self.app.learning_fault=False
            self.app._sync_recognition_context('')
            self.notice.set('已保存在本机。' if not self.store.error else '本地词库文件暂不可写；本次改动只在内存中。')
        except Exception as exc:
            self.notice.set(str(exc))
        self.refresh(force=True)

    def refresh(self, force=False):
        if not force and getattr(self.app,'tabs',None) is not None and self.app.tabs.select() is not self.app.personal_tab:
            return
        version=(self.store.revision,self.app.pronunciation.revision)
        if not force and self.seen_revision==version:
            return
        self.seen_revision=version
        profiles=self.store.profiles()
        self.profile_box.configure(values=[p['name'] for p in profiles])
        self.profile.set(next(p['name'] for p in profiles if p['id']==self.store.active))
        self.learning.set(self.store.learning)
        self.audio_learning.set(self.app.config.get('audio_learning',False))
        self.audio_summary.set(f"{len(self.store.decode_terms())} 个个人识别词 · {len(self.app.pronunciation.rows(self.store.active))} 段改词录音")
        if self.app.pronunciation.notice:self.notice.set(self.app.pronunciation.notice)
        selected=self.rules.selection()
        self.rules.delete(*self.rules.get_children())
        data=self.store.rules()
        names={'active':'已启用','pending':'待确认','disabled':'已停用'}
        for row in data:
            count=f" · {row['count']} 次" if row['count'] else ' · 手动添加'
            self.rules.insert('', 'end', iid=str(row['id']),values=(row['source'],row['target'],names[row['status']]+count))
        if selected and self.rules.exists(selected[0]):
            self.rules.selection_set(selected[0])
            self.detail()
        else:
            self.rule_detail.set('选择一条，查看完整改法。' if data else '还没有改词记录。你亲手改过的词会出现在这里。')
        self.phrases.delete(*self.phrases.get_children())
        self.phrase_rows=self.store.phrases()
        for index,row in enumerate(self.phrase_rows):
            self.phrases.insert('','end',iid=str(index),values=(row['text'],row['count'],'是' if row['protected'] else ''))
        self.summary.set(f"{sum(r['status']=='active' for r in data)} 条改法生效 · {sum(r['status']=='pending' for r in data)} 条待确认 · 实际自动改词 {self.store.usage_count()} 次")

    def export_book(self):
        if not self.idle():return
        path=filedialog.asksaveasfilename(parent=self.app.root,title='导出当前人的词库',
            defaultextension='.json',initialfile='我的用语.json',filetypes=[('个人词库','*.json')])
        if not path:return
        try:
            import json
            from pathlib import Path
            from draft_store import DraftStore
            from personal_exchange import export_data
            target=Path(path)
            DraftStore(target)._atomic(target,json.dumps(export_data(self.store),ensure_ascii=False,indent=2))
            self.notice.set('已导出改词记录与保留原词；不含草稿、录音或 Key。')
        except Exception:self.notice.set('未能导出，原词库保留。')

    def import_book(self):
        if not self.idle():return
        path=filedialog.askopenfilename(parent=self.app.root,title='导入为一份独立词库',filetypes=[('个人词库','*.json')])
        if not path:return
        try:
            from personal_exchange import read_file,import_data
            data=read_file(path)
            key=import_data(self.store,data)
            self.run(lambda:self.store.select_profile(key))
            self.notice.set('已导入为独立词库；改法待确认，选择后点“启用”即可。')
        except Exception as exc:self.notice.set('未导入：'+str(exc))

    def show_learning(self):
        from learning_status import MESSAGES,rows
        from datetime import datetime
        window=tk.Toplevel(self.app.root);window.title('本地学习状态')
        text=tk.Text(window,wrap='word',font=(FONT,11),padx=18,pady=18);text.pack(fill='both',expand=True)
        state='开启' if self.store.learning else '关闭'
        audio='开启' if self.app.config.get('audio_learning') else '关闭'
        text.insert('end',f'改词学习：{state}　短录音：{audio}\n实际自动改词：{self.store.usage_count()} 次\n\n')
        events=rows(self.store)
        if not events:text.insert('end','升级后还没有学习事件。旧记录没有失败原因，不能据此判断此前为什么未采到录音。\n')
        for row in events:
            stamp=datetime.fromtimestamp(row['updated']).strftime('%m-%d %H:%M')
            text.insert('end',f"{stamp} · {row['count']} 次\n{MESSAGES.get(row['code'],row['code'])}\n\n")
        text.insert('end','统计只表示发生过哪些事件，不是识别准确率。旧草稿可选中一句后点“改词入库”；没有原音的草稿只学习文字。')
        text.configure(state='disabled')
        from draft_ui import fit_dialog
        fit_dialog(window,760,490)

    def switch(self, event=None):
        chosen=next((p['id'] for p in self.store.profiles() if p['name']==self.profile.get()),None)
        if chosen:self.run(lambda:self.store.select_profile(chosen))

    def create(self):
        if not self.idle():return
        name=simpledialog.askstring('新建个人词库','给这份词库起个名字：',parent=self.app.root)
        if name:self.run(lambda:self.store.select_profile(self.store.create_profile(name)))

    def rename(self):
        if not self.idle():return
        name=simpledialog.askstring('词库改名','新的名字：',initialvalue=self.profile.get(),parent=self.app.root)
        if name:self.run(lambda:self.store.rename(name))

    def toggle_learning(self):
        enabled=self.learning.get()
        self.run(lambda:self.store.set_learning(enabled))

    def toggle_audio_learning(self):
        if not self.idle():
            self.audio_learning.set(self.app.config.get('audio_learning',False));return
        from settings import save_settings
        from pathlib import Path
        self.app.config['audio_learning']=self.audio_learning.get()
        save_settings(self.store.path.parent.parent/'settings.json',self.app.config)
        self.app.learning.reset();self.app.pronunciation.discard()
        self.app._sync_recognition_context('')
        self.notice.set('只在你启动听写后暂存；亲手改词才保存对应短录音。' if self.audio_learning.get() else '已停止采集新录音；已有样本保留。')

    def show_samples(self):
        if not self.idle():return
        window=tk.Toplevel(self.app.root);window.title('本地发音样本');window.geometry('840x480')
        body=tk.Frame(window,padx=16,pady=14);body.pack(fill='both',expand=True)
        tk.Label(body,text='人工局部改词样本；整句尚未审核，未用于训练个人声音模型。',anchor='w').pack(fill='x')
        table=ttk.Treeview(body,columns=('before','after'),show='headings',selectmode='browse')
        table.heading('before',text='当时显示');table.heading('after',text='亲手改后')
        table.pack(fill='both',expand=True,pady=10)
        profile=self.store.active
        for row in self.app.pronunciation.rows(profile):table.insert('','end',iid=row['id'],values=(row['displayed'],row['edited']))
        controls=tk.Frame(body);controls.pack(fill='x')
        def play():
            selected=table.selection()
            if not selected:return
            audio=self.app.pronunciation.audio(profile,selected[0])
            if audio:
                import threading,winsound
                threading.Thread(target=lambda:winsound.PlaySound(audio,winsound.SND_MEMORY|winsound.SND_NODEFAULT),daemon=True).start()
        def delete():
            selected=table.selection()
            if selected:
                if self.app.pronunciation.delete(profile,selected[0]):table.delete(selected[0])
                self.refresh(force=True)
        ttk.Button(controls,text='播放原音',command=play).pack(side='left')
        ttk.Button(controls,text='删除所选样本',command=delete).pack(side='right')

    def detail(self, event=None):
        selected=self.rules.selection()
        row=next((r for r in self.store.rules() if selected and str(r['id'])==selected[0]),None)
        if row:
            context='语境：'+row['left_ctx']+'【原词】'+row['right_ctx'] if row['scope']=='context' else '字面匹配 · 引号内保留原文'
            self.rule_detail.set('识别：'+row['source']+'\n改为：'+row['target']+'\n'+context+
                f"\n实际自动使用：{self.store.usage_count(row['id'])} 次")

    def status(self, value):
        selected=self.rules.selection()
        if selected:self.run(lambda:self.store.set_status(int(selected[0]),value))

    def delete_rule(self):
        selected=self.rules.selection()
        if selected:self.run(lambda:self.store.delete_rule(int(selected[0])))

    def add(self):
        self.run(lambda:self.store.add_rule(self.source.get(),self.target.get()))

    def protect(self):
        selected=self.phrases.selection()
        if selected:self.run(lambda:self.store.protect_phrase(self.phrase_rows[int(selected[0])]['text']))

    def delete_phrase(self):
        selected=self.phrases.selection()
        if selected:self.run(lambda:self.store.delete_phrase(self.phrase_rows[int(selected[0])]['text']))

    def clear(self):
        if self.idle() and messagebox.askyesno('清空这份词库','只清空当前人的改词记录和常用表达？',parent=self.app.root):
            self.run(self.store.clear)
