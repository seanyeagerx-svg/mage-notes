"""Compact desktop presentation. Native input and recording live in app.py."""
import tkinter as tk
from tkinter import ttk
from display import px, scale_layout, scale_styles

BG = '#F4F5F2'
WHITE = '#FFFFFF'
INK = '#243237'
MUTED = '#66757A'
BLUE = '#187B70'
LINE = '#E6EBEC'
FONT = 'Microsoft YaHei UI'
NAV = '#182C30'
SOFT = '#EAF4F0'


class LevelMeter(tk.Canvas):
    def __init__(self, parent):
        super().__init__(parent,height=2,bg=LINE,highlightthickness=0,bd=0)
        self.level=0
        self.bar=self.create_rectangle(0,0,0,2,fill=BLUE,outline='')
        self.bind('<Configure>',lambda _:self.coords(self.bar,0,0,self.winfo_width()*self.level/100,px(2)))
    def __setitem__(self,key,value):
        if key=='value':
            self.level=min(100,max(0,float(value)))
            self.coords(self.bar,0,0,self.winfo_width()*self.level/100,px(2))
        else:super().__setitem__(key,value)


class ScrollPane(tk.Frame):
    def __init__(self,parent):
        super().__init__(parent,bg=WHITE)
        self.canvas=tk.Canvas(self,bg=WHITE,highlightthickness=0,bd=0)
        bar=ttk.Scrollbar(self,command=self.canvas.yview)
        bar.pack(side='right',fill='y')
        self.canvas.pack(side='left',fill='both',expand=True)
        self.canvas.configure(yscrollcommand=bar.set)
        self.content=tk.Frame(self.canvas,bg=WHITE)
        item=self.canvas.create_window(0,0,window=self.content,anchor='nw')
        self.canvas.bind('<Configure>',lambda e:self.canvas.itemconfigure(item,width=e.width))
        self.content.bind('<Configure>',lambda _:self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        def wheel(event):
            if (self.winfo_ismapped() and self.winfo_rootx()<=event.x_root<self.winfo_rootx()+self.winfo_width()
                    and self.winfo_rooty()<=event.y_root<self.winfo_rooty()+self.winfo_height()):
                self.canvas.yview_scroll(-int(event.delta/120),'units')
        self.bind_all('<MouseWheel>',wheel,add='+')


class PageDeck:
    def __init__(self,parent,nav,title=None,description=None):
        self.parent,self.nav=parent,nav
        self.pages,self.buttons,self.titles,self.descriptions=[],[],[],[]
        self.current=None
        self.title,self.description=title,description
    def add(self,page,text,description=''):
        self.pages.append(page)
        self.titles.append(text)
        self.descriptions.append(description)
        button=ttk.Button(self.nav,text=text,style='Nav.TButton',command=lambda:self.select(page))
        button.pack(fill='x',pady=3)
        self.buttons.append(button)
        if self.current is None:self.select(page)
    def select(self,page=None):
        if page is None:return self.current
        for candidate,button in zip(self.pages,self.buttons):
            candidate.pack_forget()
            button.configure(style='Selected.Nav.TButton' if candidate is page else 'Nav.TButton')
        page.pack(fill='both',expand=True)
        self.current=page
        index=self.pages.index(page)
        if self.title is not None:self.title.set(self.titles[index])
        if self.description is not None:self.description.set(self.descriptions[index])
        if hasattr(page,'on_show'):page.on_show()


def label(parent,text=None,variable=None,size=10,color=INK,bold=False,bg=WHITE,**pack):
    widget=tk.Label(parent,text=text,textvariable=variable,bg=bg,fg=color,
                    font=(FONT,size,'bold' if bold else 'normal'),anchor='w',justify='left',bd=0)
    widget.pack(**pack)
    return widget


def card(parent,**pack):
    frame=tk.Frame(parent,bg=WHITE,highlightbackground=LINE,highlightthickness=1)
    frame.pack(**pack)
    return frame


def rule(parent,pady=16):
    tk.Frame(parent,bg=LINE,height=1).pack(fill='x',pady=pady)


def wrapped(parent,text=None,variable=None,width=550,**pack):
    widget=label(parent,text,variable,size=9,color=MUTED,**pack)
    widget.configure(wraplength=width)
    widget.bind('<Configure>',lambda e:widget.configure(wraplength=max(100,e.width-2)))
    return widget


def text_field(parent):
    frame=tk.Frame(parent,bg=WHITE)
    frame.pack(fill='both',expand=True,pady=(15,8))
    scrollbar=ttk.Scrollbar(frame)
    scrollbar.pack(side='right',fill='y')
    text=tk.Text(frame,wrap='word',font=(FONT,13),height=6,bg=WHITE,fg=INK,
                 insertbackground=BLUE,insertwidth=2,relief='flat',bd=0,padx=1,pady=5,
                 spacing1=5,spacing3=6,undo=True,maxundo=100,
                 selectbackground='#CFE9E2',selectforeground=INK,yscrollcommand=scrollbar.set)
    text.pack(side='left',fill='both',expand=True)
    scrollbar.configure(command=text.yview)
    return text


def styles(root):
    style=ttk.Style(root)
    style.theme_use('clam')
    root.option_add('*TCombobox*Listbox.font',(FONT,10))
    style.configure('.',font=(FONT,10))
    style.configure('TButton',font=(FONT,10),padding=(14,9),borderwidth=0,relief='flat',
                    background='#EDF1F2',foreground=INK,focusthickness=0)
    style.map('TButton',background=[('disabled','#F0F3F3'),('pressed','#DDE5E6'),('active','#E3E9EA')],
              foreground=[('disabled','#A5AFB2')])
    style.configure('Primary.TButton',background=BLUE,foreground=WHITE,padding=(20,11))
    style.map('Primary.TButton',background=[('disabled','#E0ECE8'),('pressed','#105C54'),('active','#136B61')],
              foreground=[('disabled','#7DABA3'),('!disabled',WHITE)])
    style.configure('Quiet.TButton',background=WHITE,foreground=MUTED,padding=(10,6))
    style.map('Quiet.TButton',background=[('active','#F1F5F4')],foreground=[('active',BLUE)])
    style.configure('Danger.TButton',background=WHITE,foreground='#A56662',padding=(8,6))
    style.configure('Book.TButton',background=WHITE,foreground=MUTED,padding=(16,9))
    style.map('Book.TButton',background=[('active','#F3F7F5')])
    style.configure('Selected.Book.TButton',background=SOFT,foreground=BLUE)
    style.map('Selected.Book.TButton',background=[('active','#DDEFE8')],foreground=[('active',BLUE)])
    style.configure('Nav.TButton',anchor='w',background=NAV,foreground='#9CB1B5',padding=(17,13),font=(FONT,10))
    style.map('Nav.TButton',background=[('active','#203B3E')],foreground=[('active',WHITE)])
    style.configure('Selected.Nav.TButton',background='#2A4548',foreground=WHITE)
    style.map('Selected.Nav.TButton',background=[('active','#315052')],foreground=[('active',WHITE)])
    style.configure('TCheckbutton',background=WHITE,foreground=INK,padding=3)
    style.map('TCheckbutton',background=[('active',WHITE)])
    style.configure('TEntry',padding=8,fieldbackground='#F7F9F9',bordercolor=LINE,lightcolor=LINE,darkcolor=LINE)
    style.map('TEntry',bordercolor=[('focus',BLUE)])
    style.configure('TCombobox',padding=8,fieldbackground='#F7F9F9',background='#F7F9F9',foreground=INK,
                    bordercolor=LINE,lightcolor=LINE,darkcolor=LINE,arrowsize=12)
    style.map('TCombobox',fieldbackground=[('readonly','#F7F9F9')],selectbackground=[('readonly','#F7F9F9')],
              selectforeground=[('readonly',INK)],bordercolor=[('focus',BLUE)])
    style.configure('Vertical.TScrollbar',background='#D5DEDE',troughcolor=WHITE,borderwidth=0,arrowsize=10)
    style.layout('Vertical.TScrollbar',[('Vertical.Scrollbar.trough',{'sticky':'ns','children':[
        ('Vertical.Scrollbar.thumb',{'expand':'1','sticky':'nswe'})]})])
    style.configure('Treeview',background=WHITE,fieldbackground=WHITE,foreground=INK,borderwidth=0,
                    relief='flat',rowheight=40,font=(FONT,10))
    style.configure('Treeview.Heading',background='#F3F6F6',foreground=MUTED,font=(FONT,9),padding=(10,9),relief='flat',borderwidth=0)
    style.map('Treeview',background=[('selected',SOFT)],foreground=[('selected',BLUE)])
    style.map('Treeview.Heading',background=[('active','#EDF2F1')])
    style.layout('Treeview',[('Treeview.treearea',{'sticky':'nswe'})])
    style.configure('TNotebook',background=WHITE,borderwidth=0,tabmargins=(0,0,0,12))
    style.configure('TNotebook.Tab',background=WHITE,foreground=MUTED,padding=(16,9),borderwidth=0)
    style.map('TNotebook.Tab',background=[('selected',SOFT)],foreground=[('selected',BLUE)])


def build(app):
    root=app.root
    root.title('魔法师手札 · 开口成文，一语施法')
    root.geometry(f'{px(1020)}x{min(px(820),max(px(720),root.winfo_screenheight()-px(100)))}')
    root.minsize(px(900),px(720))
    root.configure(bg=BG)
    styles(root)
    app.app_icon=tk.PhotoImage(width=32,height=32)
    app.app_icon.put(BLUE,to=(0,0,32,32))
    for x,height in ((7,10),(14,22),(21,16)):
        app.app_icon.put(WHITE,to=(x,(32-height)//2,x+4,(32+height)//2))
    root.iconphoto(True,app.app_icon)

    sidebar=tk.Frame(root,bg=NAV,width=156)
    sidebar.pack(side='left',fill='y')
    sidebar.pack_propagate(False)
    brand=tk.Frame(sidebar,bg=NAV,padx=20,pady=22)
    brand.pack(fill='x')
    mark=tk.Canvas(brand,width=31,height=28,bg=NAV,highlightthickness=0)
    mark.pack(anchor='w',pady=(0,10))
    for x,h in ((3,10),(11,24),(19,18),(27,7)):
        mark.create_line(x,14-h/2,x,14+h/2,fill='#9AD7C7',width=4,capstyle='round')
    label(brand,'声笺',size=23,bold=True,bg=NAV,color=WHITE,anchor='w')
    label(brand,'魔法师手札',size=10,color='#A3BBBE',bg=NAV,anchor='w',pady=(4,0))
    nav=tk.Frame(sidebar,bg=NAV,padx=10)
    nav.pack(fill='x',pady=(14,0))
    sidefoot=tk.Frame(sidebar,bg=NAV,padx=23,pady=25)
    sidefoot.pack(side='bottom',fill='x')
    app.processing_location=tk.StringVar(value='本地识别 · AI 校对' if app.config.get('ai_assist') and app.config.get('correction_mode')=='light' else '●  全程本地')
    location=label(sidefoot,'',size=9,color='#9AD7C7',bg=NAV,anchor='w')
    location.configure(textvariable=app.processing_location)
    from runtime_status import VERSION
    label(sidefoot,VERSION,size=9,color='#849FA3',bg=NAV,anchor='w',pady=(7,0))

    main=tk.Frame(root,bg=BG,padx=22,pady=20)
    main.pack(side='left',fill='both',expand=True)
    header=tk.Frame(main,bg=BG)
    header.pack(fill='x',pady=(0,14))
    app.page_title=tk.StringVar()
    app.page_description=tk.StringVar()
    label(header,variable=app.page_title,size=20,bold=True,bg=BG,anchor='w')
    wrapped(header,variable=app.page_description,fill='x',bg=BG,pady=(5,0))

    statuscard=card(main,side='bottom',fill='x',pady=(16,0))
    app.meter=LevelMeter(statuscard)
    app.meter.pack(fill='x')
    statusinner=tk.Frame(statuscard,bg=WHITE,padx=15,pady=10)
    statusinner.pack(fill='x')
    app.stop_button=ttk.Button(statusinner,text='结束会话',command=app.finish)
    statebody=tk.Frame(statusinner,bg=WHITE)
    app.state_body=statebody
    statebody.pack(side='left',fill='x',expand=True)
    app.state_label=tk.StringVar(value='准备中')
    label(statebody,variable=app.state_label,bold=True,color=BLUE,anchor='w')
    app.status=tk.StringVar(value='正在加载本地模型…')
    app.status_line=wrapped(statebody,variable=app.status,fill='x',pady=(3,0))
    app.key_hint=tk.StringVar()
    app.key_hint_label=label(statebody,variable=app.key_hint,size=9,color=BLUE,fill='x')
    app._update_hint()

    body=tk.Frame(main,bg=BG)
    body.pack(fill='both',expand=True)
    app.tabs=PageDeck(body,nav,app.page_title,app.page_description)
    app.dict_tab=tk.Frame(body,bg=BG)
    app.flow_tab=tk.Frame(body,bg=BG)
    app.personal_tab=tk.Frame(body,bg=BG)
    app.settings_tab=tk.Frame(body,bg=BG)
    app.tabs.add(app.dict_tab,'语音听写','边说边出字，保留原话；也能输入到你当前的聊天框。')
    app.tabs.add(app.flow_tab,'工作流','识别意图 → 整理任务 → 复制或交给创作接力站。')

    editor=card(app.dict_tab,fill='both',expand=True)
    inside=tk.Frame(editor,bg=WHITE,padx=18,pady=15)
    inside.pack(fill='both',expand=True)
    toolbar=tk.Frame(inside,bg=WHITE)
    toolbar.pack(fill='x')
    label(toolbar,'转写草稿',size=11,bold=True,side='left')
    from draft_ui import show_history, review, open_documents
    from library_ui import collect
    for title,command in [('新建',lambda:app.clear(app.dict_text)),('历史',lambda:show_history(app)),
                          ('收为素材',lambda:collect(app)),('复制',lambda:app.copy(app.dict_text))]:
        ttk.Button(toolbar,text=title,style='Quiet.TButton',command=command).pack(side='right',padx=(5,0))
    more=tk.Menubutton(toolbar,text='更多 ▾',bg=WHITE,fg=MUTED,font=(FONT,10),relief='flat')
    menu=tk.Menu(more,tearoff=False,font=(FONT,10))
    menu.add_command(label='查看原始转写',command=app.show_original)
    menu.add_command(label='亲手改词入库',command=lambda:review(app))
    menu.add_command(label='打开自动留存文件夹',command=lambda:open_documents(app))
    more.configure(menu=menu);more.pack(side='right',padx=(4,0))
    app.text_hint=tk.StringVar(value='外部输入时，这里保留副本。也可以在这里试写。')
    wrapped(inside,variable=app.text_hint,fill='x',pady=(8,0))
    app.dict_text=text_field(inside)
    app.correction_hint=tk.StringVar(value={'light':'轻度纠错 · 保留原意','personal':'只用你确认过的改法','raw':'保留原始转写'}[app.config.get('correction_mode','light')])
    if app.config.get('ai_assist') and app.config.get('correction_mode')=='light':
        app.correction_hint.set('AI 辅助校对 · 仅发送当前转写与短上下文')
    bottom=tk.Frame(inside,bg=WHITE)
    bottom.pack(fill='x',pady=(4,0))
    app.correction_stage=tk.StringVar(value='原话先出字 · 复核结果随后到达')
    wrapped(bottom,variable=app.correction_stage,fill='x')
    wrapped(bottom,variable=app.correction_hint,fill='x',pady=(3,0))
    from v1_ui import build_proofread
    build_proofread(app,inside)
    app.learning_hint=tk.StringVar(value='待命 · 只学习你亲手改的词' if app.personal.learning else '自动学习已关闭 · 词库仍可使用')
    rule(inside,pady=8)
    wrapped(inside,variable=app.learning_hint,fill='x')
    controls=tk.Frame(app.dict_tab,bg=BG)
    controls.pack(before=editor,fill='x',pady=(0,12))
    app.dict_button=ttk.Button(controls,text='开始录音',style='Primary.TButton',command=lambda:app.trigger(0,internal=True))
    app.dict_button.pack(side='left')
    from hotkeys import display_binding
    app.shortcut_badge=tk.StringVar(value=display_binding(app.config['dictation_key']))
    label(controls,variable=app.shortcut_badge,color=BLUE,bg=SOFT,size=9,side='left',padx=(12,8),ipadx=9,ipady=5)
    label(controls,'再次按键结束 · 停顿不会结束录音',size=9,color=MUTED,bg=BG,side='left')

    build_flow(app)
    from project_ui import ProjectPage
    app.project_page=ProjectPage(app,body)
    from library_ui import LibraryPage
    app.library_page=LibraryPage(app,body)
    app.tabs.add(app.personal_tab,'我的用语','改词记录与生效规则分开看，亲手修改逐渐积累。')
    app.tabs.add(app.settings_tab,'设置','按你的习惯，调整输入方式。')
    build_settings(app)
    from personal_ui import PersonalPage
    app.personal_page=PersonalPage(app,app.personal_tab)
    from v1_ui import build_pages
    build_pages(app,body)
    if not app.config.get('onboarding_done') and not app.no_hooks:
        app.tabs.select(app.help_tab)
    scale_layout(root)
    scale_styles(root)


def build_flow(app):
    flowcard=card(app.flow_tab,fill='both',expand=True)
    inner=tk.Frame(flowcard,bg=WHITE,padx=18,pady=15)
    inner.pack(fill='both',expand=True)
    head=tk.Frame(inner,bg=WHITE)
    head.pack(fill='x')
    label(head,'任务草稿',size=11,bold=True,side='left')
    app.route_choice=tk.StringVar(value='待选择工作流')
    ttk.Combobox(head,textvariable=app.route_choice,state='readonly',width=14,
                 values=['待选择工作流','生成图片','制作色卡','参考图素材']).pack(side='right')
    # Reserve the action/material area before the expanding editor is packed.
    bottom=tk.Frame(inner,bg=WHITE);bottom.pack(side='bottom',fill='x')
    app.route_hint=tk.StringVar(value='结束录音后自动识别流程。')
    wrapped(bottom,variable=app.route_hint,fill='x',pady=(6,0))
    from v1_ui import semantic
    ttk.Button(bottom,text='识别意图（复杂表达可用 AI）',command=lambda:semantic(app)).pack(anchor='w',pady=5)
    rule(bottom,pady=8)
    refrow=tk.Frame(bottom,bg=WHITE)
    refrow.pack(fill='x')
    ttk.Button(refrow,text='＋ 参考图',command=app.add_refs).pack(side='left')
    ttk.Button(refrow,text='移除',style='Quiet.TButton',command=app.clear_refs).pack(side='left',padx=5)
    ttk.Button(refrow,text='＋ 作品材料',command=app.add_materials).pack(side='left',padx=5)
    app.ref_hint=tk.StringVar(value='未添加图片')
    ttk.Button(refrow,text='查看任务',style='Quiet.TButton',command=app.open_task).pack(side='right')
    wrapped(bottom,variable=app.ref_hint,fill='x',pady=(6,0))
    app.relay_hint=tk.StringVar(value='接力站连接尚未确认 · 准备任务不代表已经生成')
    wrapped(bottom,variable=app.relay_hint,fill='x',pady=(5,0))
    wrapped(inner,'“生成图片…”、“调用视频工作流…”；Skill 指令可在听写中说“调用领导这个技能…”。',fill='x',pady=(8,0))
    app.flow_text=text_field(inner)
    controls=tk.Frame(app.flow_tab,bg=BG)
    controls.pack(before=flowcard,fill='x',pady=(0,10))
    app.flow_button=ttk.Button(controls,text='录入任务',style='Primary.TButton',command=lambda:app.trigger(1,internal=True))
    app.flow_button.pack(side='left')
    ttk.Button(controls,text='整理任务',command=app.prepare_flow).pack(side='left',padx=8)
    ttk.Button(controls,text='复制提示词',command=app.copy_prompt).pack(side='left')
    ttk.Button(controls,text='填回原处',command=app.fill_origin).pack(side='right')
    ttk.Button(controls,text='交给接力站',command=app.relay_flow).pack(side='right',padx=6)
    foot=tk.Frame(app.flow_tab,bg=BG)
    foot.pack(side='bottom',before=controls,fill='x',pady=(12,0))
    wrapped(foot,'语音和草稿保存在本机；网页交接另需安装创作接力站。',bg=BG,side='left',fill='x',expand=True)
    ttk.Button(foot,text='打开 GPT ↗',command=app.open_gpt).pack(side='right')


def build_settings(app):
    outer=card(app.settings_tab,fill='both',expand=True)
    fixed=tk.Frame(outer,bg=WHITE,padx=22,pady=12)
    fixed.pack(side='bottom',fill='x')
    ttk.Button(fixed,text='保存设置',style='Primary.TButton',command=app.save_settings).pack(side='right')
    label(fixed,'快捷键录入后自动保存',size=9,color=MUTED,side='left')
    tk.Frame(outer,bg=LINE,height=1).pack(side='bottom',fill='x')
    scroll=ScrollPane(outer)
    scroll.pack(fill='both',expand=True)
    settings=scroll.content
    settings.configure(padx=22,pady=18)
    label(settings,'快捷键',size=12,bold=True,anchor='w',pady=(0,10))
    grid=tk.Frame(settings,bg=WHITE)
    grid.pack(fill='x')
    grid.columnconfigure(1,weight=1)
    app.key1=tk.StringVar(value=app.config['dictation_key'])
    app.key2=tk.StringVar(value=app.config['workflow_key'])
    app.key_labels=[tk.StringVar(),tk.StringVar()]
    app.key_buttons=[]
    for row,title in enumerate(('语音听写','工作流录音')):
        tk.Label(grid,text=title,bg=WHITE,fg=INK,font=(FONT,10)).grid(row=row,column=0,sticky='w',padx=(0,26),pady=5)
        button=ttk.Button(grid,textvariable=app.key_labels[row],command=lambda i=row:app.begin_key_capture(i))
        button.grid(row=row,column=1,sticky='ew',pady=5)
        app.key_buttons.append(button)
    app._refresh_key_buttons()
    capture=tk.Frame(settings,bg=WHITE)
    capture.pack(fill='x',pady=(6,10))
    app.capture_cancel=ttk.Button(capture,text='取消录入',style='Quiet.TButton',command=app.cancel_key_capture)
    app.capture_cancel.pack(side='right',padx=(8,0))
    app.capture_cancel.state(['disabled'])
    app.capture_hint=tk.StringVar(value='点击按键，再按键盘或鼠标；松开即保存。')
    wrapped(capture,variable=app.capture_hint,side='left',fill='x',expand=True)
    app.activation=tk.StringVar(value='持续：点一下暂停 / 恢复' if app.config['activation']=='continuous' else '点按：点一下开始，再点结束（不用按住）')
    ttk.Combobox(settings,textvariable=app.activation,state='readonly',values=[
        '点按：点一下开始，再点结束（不用按住）','持续：点一下暂停 / 恢复']).pack(fill='x',pady=(0,8))
    app.enabled=tk.BooleanVar(value=app.config.get('enabled',True))
    ttk.Checkbutton(settings,text='启用全局快捷键',variable=app.enabled,command=app.enable_hooks).pack(anchor='w')

    rule(settings)
    label(settings,'文字修正',size=12,bold=True,anchor='w',pady=(0,10))
    app.auto_correct=tk.BooleanVar(value=app.config.get('auto_correct',True))
    app.correction_mode=tk.StringVar(value={'light':'轻度纠错','personal':'只用个人词库','raw':'原样输出'}[app.config.get('correction_mode','light')])
    ttk.Combobox(settings,textvariable=app.correction_mode,state='readonly',
                 values=['轻度纠错','只用个人词库','原样输出']).pack(fill='x')
    wrapped(settings,'轻度纠错结合原音和上下文；你确认过的用语优先。',fill='x',pady=(7,9))
    app.ai_assist=tk.BooleanVar(value=app.config.get('ai_assist',False))
    ttk.Checkbutton(settings,text='启用云端 AI 辅助校对',variable=app.ai_assist).pack(anchor='w')
    from v1_ui import build_provider
    build_provider(app,settings)
    app.clean_fillers=tk.BooleanVar(value=app.config.get('clean_fillers',True))
    app.punctuation=tk.BooleanVar(value=app.config.get('punctuation',True))
    app.paragraphs=tk.BooleanVar(value=app.config.get('paragraphs',True))
    for title,variable in [('整理犹豫音、卡顿重复和明确改口',app.clean_fillers),
                           ('按语意补标点',app.punctuation),('完整句后切换话题，另起一段',app.paragraphs)]:
        ttk.Checkbutton(settings,text=title,variable=variable).pack(anchor='w',pady=2)
    wrapped(settings,'以上整理随轻度纠错生效；引用原话和有意义的语气保留。',fill='x',pady=(4,8))
    app.resume_at_end=tk.BooleanVar(value=app.config['resume_at_end'])
    ttk.Checkbutton(settings,text='改字后强制接到末尾（默认关闭，从当前光标输入）',variable=app.resume_at_end).pack(anchor='w')
    rule(settings)
    label(settings,'语音发送',size=12,bold=True,anchor='w',pady=(0,8))
    app.voice_send=tk.BooleanVar(value=app.config.get('voice_send',True))
    ttk.Checkbutton(settings,text='单独说“发送”，等待 2 秒后发送',variable=app.voice_send).pack(anchor='w')
    wrapped(settings,'继续说、改字或按 Esc 可取消。说“输入发送”可写出这两个字。',fill='x',pady=(4,10))
    app.send_key=tk.StringVar(value='Ctrl+Enter 发送' if app.config.get('send_key')=='ctrl+enter' else 'Enter 发送')
    ttk.Combobox(settings,textvariable=app.send_key,state='readonly',values=['Enter 发送','Ctrl+Enter 发送']).pack(fill='x')
    rule(settings)
    label(settings,'麦克风与工作流',size=12,bold=True,anchor='w',pady=(0,10))
    app.devices={'系统默认':None}
    app.device_signatures={'系统默认':None}
    app.device_notice=''
    try:
        import sounddevice as sd
        for i,device in enumerate(sd.query_devices()):
            api=sd.query_hostapis(device['hostapi'])['name']
            if device['max_input_channels'] and api in ('MME','Windows WASAPI'):
                name=f"{i} · {device['name']}"
                app.devices[name]=i
                app.device_signatures[name]={'name':device['name'],'hostapi':api}
    except Exception:
        app.status.set('麦克风列表暂不可用')
    signature=app.config.get('device_signature')
    if signature:
        selected=next((name for name,value in app.device_signatures.items() if value==signature),'系统默认')
        if selected=='系统默认':app.device_notice='已选麦克风未找到，暂用系统默认设备'
    else:
        selected=next((name for name,idx in app.devices.items() if idx==app.config['device']),'系统默认')
    app.config['device']=app.devices[selected]
    app.device=tk.StringVar(value=selected)
    ttk.Combobox(settings,textvariable=app.device,values=list(app.devices),state='readonly').pack(fill='x')
    from volume_ui import build as build_volume
    build_volume(app,settings)
    app.noise_filter=tk.BooleanVar(value=app.config.get('noise_filter',True))
    ttk.Checkbutton(settings,text='过滤环境杂音，保留轻声',variable=app.noise_filter).pack(anchor='w',pady=(10,3))
    wrapped(settings,'不调低系统麦克风音量。多人同时说话或电视对白，仍可能混入。',fill='x',pady=(2,8))
    rule(settings)
    label(settings,'口述调用技能',size=12,bold=True,anchor='w',pady=(0,8))
    app.skill_commands=tk.BooleanVar(value=app.config.get('skill_commands',True))
    ttk.Checkbutton(settings,text='识别开头的“调用 + 技能名”',variable=app.skill_commands).pack(anchor='w')
    app.skill_syntax=tk.StringVar(value={'dollar':'$技能名（Codex）','slash':'/技能名','natural':'自然语言'}[app.config.get('skill_syntax','dollar')])
    ttk.Combobox(settings,textvariable=app.skill_syntax,state='readonly',values=['$技能名（Codex）','/技能名','自然语言']).pack(fill='x',pady=8)
    wrapped(settings,'比如“调用领导，帮我拆一下目标” → $leader 帮我拆一下目标。只填文字，不自动发送。',fill='x',pady=(0,10))
    aliasrow=tk.Frame(settings,bg=WHITE)
    aliasrow.pack(fill='x')
    app.skill_name=tk.StringVar(value='leader' if 'leader' in app.skills.skills else next(iter(app.skills.skills),''))
    app.skill_alias=tk.StringVar()
    app.skill_combo=ttk.Combobox(aliasrow,textvariable=app.skill_name,state='readonly',values=sorted(app.skills.skills),width=18)
    app.skill_combo.pack(side='left')
    ttk.Entry(aliasrow,textvariable=app.skill_alias,width=14).pack(side='left',fill='x',expand=True,padx=8)
    app.skill_notice=tk.StringVar(value='可以给已安装技能起一个好念的名字。')
    def add_alias():
        try:
            app.skills.add(app.skill_name.get(),app.skill_alias.get())
            app.skill_notice.set('已记住：调用'+app.skill_alias.get()+' → '+app.skill_name.get())
        except (OSError,ValueError) as exc:app.skill_notice.set(str(exc))
    ttk.Button(aliasrow,text='记住',command=add_alias).pack(side='right')
    wrapped(settings,variable=app.skill_notice,fill='x',pady=(7,4))
    rule(settings)
    label(settings,'工作流结束后',size=9,color=MUTED,anchor='w',pady=(12,6))
    app.workflow_action=tk.StringVar(value={'paste_origin':'填回原输入框（不发送）','copy':'复制提示词','prepare':'仅整理任务'}[app.config['workflow_action']])
    ttk.Combobox(settings,textvariable=app.workflow_action,state='readonly',
                 values=['填回原输入框（不发送）','复制提示词','仅整理任务']).pack(fill='x',pady=(0,6))


def refresh(app):
    from volume_ui import refresh as refresh_volume
    refresh_volume(app)
    active=app.state.mode!='idle'
    decoder=getattr(app.engine,'decoder',None)
    if getattr(app.engine,'backlog_seconds',lambda:0.0)()>2:
        stage='正在追赶录音 · 暂缓精细复核，保留原话'
    elif getattr(decoder,'refinement_in_progress',False):
        stage='正在本地复核原音 · 录音继续，原话先保留'
    elif app.light_jobs:
        import time
        elapsed=max(0,int(time.monotonic()-(next(iter(app.light_jobs.values()))['deadline']-16)))
        stage=f'辅助校对 {elapsed} 秒 · 实时文字已保留，超时保留原话'
    else:
        stage='本地转写 · 原话先出字' if app.config.get('correction_mode')=='raw' else '本地转写 → 原音复核 → 按需校对'
    if app.correction_stage.get()!=stage:app.correction_stage.set(stage)
    bridge=getattr(getattr(app,'project_page',None),'bridge',None)
    relay='接力站已连接 · 可交接已整理任务，结果以实际回执为准' if bridge and bridge.connected else '可先复制任务；网页交接需另行连接创作接力站'
    if app.relay_hint.get()!=relay:app.relay_hint.set(relay)
    app._update_hint()
    app.state_label.set('收尾中' if app.stopping else '已暂停' if app.state.paused else '暂停写入' if active and app.focus_blocked else '正在录入工作流' if active and app.state.mode=='workflow'
                        else '正在听写' if active and app.engine.recording is not None else '启动中' if active
                        else '已就绪' if app.ready else '加载失败' if app.model_error else '准备中')
    if active:
        app.stop_button.pack(side='right',before=app.state_body,padx=(12,0))
    else:
        app.stop_button.pack_forget()
    if active and app.status.get() in ('正在听写','正在录入工作流','会话已暂停'):
        app.status_line.pack_forget()
    elif app.key_hint_label.winfo_manager():
        app.status_line.pack(fill='x',before=app.key_hint_label,pady=(3,0))
    else:
        app.status_line.pack(fill='x',pady=(3,0))
    app.stop_button.state(['!disabled'] if active and not app.stopping else ['disabled'])
    from hotkeys import display_binding
    app.shortcut_badge.set(display_binding(app.config['dictation_key']))
    for mode,button,initial in [('dictation',app.dict_button,'开始录音'),('workflow',app.flow_button,'录入任务')]:
        own=app.state.mode==mode
        action='继续录音' if app.state.paused else '暂停录音' if app.config['activation']=='continuous' else '结束录音'
        button.configure(text=action if own else initial)
        button.state(['disabled'] if app.stopping or not app.ready or (active and not own) else ['!disabled'])
