"""Provider setup, finite semantic suggestions, skill library and first-run help."""
from pathlib import Path
import threading
import time
import tkinter as tk
from tkinter import ttk,filedialog,messagebox
from provider_secret import KeyStore

PROVIDERS=('GLM-5.3-Flash（云端，按量计费）','Codex（已登录的命令行）')
EXAMPLES=(('生成图片','生成图片，一只戴蓝色帽子的猫，水彩风格'),
          ('制作色卡','制作色卡，提取参考图里的蓝灰色，六个色块'),
          ('查找参考资料','查找参考资料，木屋外观设计，保留来源链接'),
          )


def root_path(app):return app.skills.path.parent


def build_provider(app,parent):
    from ui import wrapped
    app.ai_provider=tk.StringVar(value=PROVIDERS[0] if app.config.get('ai_provider','codex')=='glm' else PROVIDERS[1])
    ttk.Combobox(parent,textvariable=app.ai_provider,values=PROVIDERS,state='readonly').pack(fill='x',pady=5)
    wrapped(parent,'启用后仅上传本句、短上下文与识别候选，不上传录音。GLM-5.3-Flash 按服务商规则计费；失败保留本地结果。',fill='x',pady=4)
    ttk.Label(parent,text='GLM API Key（本机加密保存）').pack(anchor='w',pady=(5,0))
    row=ttk.Frame(parent);row.pack(fill='x',pady=5)
    app.api_key_entry=ttk.Entry(row,show='●');app.api_key_entry.pack(side='left',fill='x',expand=True)
    ttk.Button(row,text='保存 Key',command=lambda:save_key(app)).pack(side='left',padx=4)
    ttk.Button(row,text='移除 Key',command=lambda:clear_key(app)).pack(side='left')
    ttk.Button(parent,text='测试 GLM 连接（只发送测试句）',command=lambda:connection_test(app)).pack(anchor='w')
    app.api_notice=tk.StringVar(value='已保存本机加密密钥' if KeyStore(root_path(app)).exists() else '尚未配置 Key；本地听写可直接使用')
    wrapped(parent,variable=app.api_notice,fill='x',pady=(5,8))
    app.v1_jobs={};app.v1_serial=0


def invalidate(app,tag):
    old=app.v1_jobs.pop(tag,None)
    if old:old[1].close()


def save_key(app):
    try:
        KeyStore(root_path(app)).save(app.api_key_entry.get())
        app.api_key_entry.delete(0,'end');invalidate(app,'connection');invalidate(app,'semantic')
        if app.config.get('ai_provider')=='glm' and app.cleaner:
            app.cleaner.close();app.cleaner=None
        app.api_notice.set('已加密保存在本机。选择 GLM 并保存设置后生效；可以先测试连接。')
    except (ValueError,OSError):app.api_notice.set('保存失败，请检查 Key 是否完整及目录是否可写')


def clear_key(app):
    try:
        KeyStore(root_path(app)).clear();app.api_key_entry.delete(0,'end')
        invalidate(app,'connection');invalidate(app,'semantic')
        if app.config.get('ai_provider')=='glm' and app.cleaner:app.cleaner.close();app.cleaner=None
        app.api_notice.set('已移除 Key，GLM 不再接收新请求；本地听写仍可使用')
    except OSError:app.api_notice.set('未能移除 Key，请检查目录权限')


def launch(app,tag,operation,snapshot=None):
    if tag in app.v1_jobs:return False
    from cloud_proofreader import GLMProofreader
    model=GLMProofreader(root_path(app),timeout=90 if tag=='proofread' else 60);app.v1_serial+=1;serial=app.v1_serial
    app.v1_jobs[tag]=(serial,model)
    def run():
        try:result=operation(model);error=None
        except Exception as exc:
            from cloud_proofreader import ProviderError
            result=None;error=str(exc) if isinstance(exc,(ProviderError,ValueError)) else '服务暂不可用，内容保留'
        finally:model.close()
        app.events.put(('v1_result',(tag,serial,snapshot,result,error)))
    def expire():
        pending=app.v1_jobs.get(tag)
        if pending and pending[0]==serial:
            model.close()
            result(app,(tag,serial,snapshot,None,'等待已到上限，内容保留；稍后可重试'))
    app.root.after(int(model.timeout*1000),expire)
    threading.Thread(target=run,name='optional-'+tag,daemon=True).start();return True


def connection_test(app):
    def check(model):
        started=time.monotonic()
        result=model.request_json('只返回 JSON {"ok":true}。',{'connection_test':True},max_tokens=32)
        if result!={'ok':True}:raise ValueError('连接有响应，但返回格式不兼容')
        return {'seconds':round(time.monotonic()-started,1)}
    if launch(app,'connection',check):app.api_notice.set('正在测试 GLM 连接，最多等待 60 秒；听写可以继续…')


def snapshot(app):
    return (app.flow_text.get('1.0','end-1c'),tuple(app.refs),app.state.generation,app.local_input_epoch,app.route_choice.get())


def semantic(app):
    if app.state.mode!='idle' or app.stopping:
        app.route_hint.set('请结束录音后再理解任务');return
    text=app.flow_text.get('1.0','end-1c').strip()
    if not text:app.route_hint.set('请先说出或输入具体任务');return
    from core import route_workflow
    route=route_workflow(text)
    if route.mode in ('image','palette','reference'):
        app.route_choice.set(route.label);app.route_hint.set('已按常见说法选中；点击“整理任务”继续，尚未执行。');return
    if len(text)>2000:
        app.route_hint.set('任务过长，请先缩短到 2000 字以内或手动选择工作流');return
    from semantic_route import propose
    frozen=snapshot(app)
    if launch(app,'semantic',lambda model:propose(model,text,bool(app.refs)),frozen):
        app.route_hint.set('正在用 GLM 理解这句话；只提供流程建议，不自动执行…')


def result(app,value):
    tag,serial,frozen,data,error=value
    pending=app.v1_jobs.get(tag)
    if not pending or pending[0]!=serial:return
    app.v1_jobs.pop(tag,None)
    if tag=='connection':
        seconds=data.get('seconds',0) if isinstance(data,dict) else 0
        app.api_notice.set(error or f'GLM 连接成功 · {seconds} 秒'+('；速度较慢，建议结束后使用手动校对' if seconds>10 else '；实际效果取决于输入'))
        return
    if tag=='proofread':
        proofread_result(app,frozen,data,error);return
    if frozen!=snapshot(app) or app.state.mode!='idle' or app.stopping:
        app.route_hint.set('任务已变化，已忽略旧的语义建议');return
    if error:app.route_hint.set(error);return
    from semantic_route import explanation
    from core import LABELS
    app.route_choice.set(LABELS[data[0]] if len(data)==1 else '待选择工作流')
    app.route_hint.set(explanation(data))


def use_example(app,text):
    if app.state.mode!='idle' or app.stopping:
        app.status.set('结束当前会话后可使用示例');return
    if app.flow_text.get('1.0','end-1c').strip():
        app.tabs.select(app.flow_tab);app.status.set('当前任务已保留；清空或保存后可填入示例');return
    app.flow_text.insert('1.0',text);app.tabs.select(app.flow_tab);app.classify()
    app.status.set('示例已填入，可编辑后整理任务；尚未执行')


def build_pages(app,body):
    from ui import ScrollPane,WHITE,wrapped,label,rule
    app.spells_tab=tk.Frame(body,bg=WHITE)
    app.help_tab=tk.Frame(body,bg=WHITE)
    app.tabs.add(app.spells_tab,'咒语库','常见说法与已安装技能。先看用途，再调用。')
    app.tabs.add(app.help_tab,'开始使用','开口成文，一语施法。无需 Key 也能听写。')
    scroll=ScrollPane(app.spells_tab);scroll.pack(fill='both',expand=True)
    parent=scroll.content
    label(parent,'常见说法',size=13,bold=True,anchor='w',pady=8)
    for name,text in EXAMPLES:
        ttk.Button(parent,text=name+' · 填入示例',command=lambda t=text:use_example(app,t)).pack(anchor='w',pady=4)
        wrapped(parent,text,fill='x',pady=(0,5))
    rule(parent)
    label(parent,'本地 Skill',size=13,bold=True,anchor='w')
    wrapped(parent,'扫描只读取名称与说明，不安装依赖、不运行脚本。GitHub 下载的技能也可选择文件夹导入。',fill='x',pady=5)
    controls=ttk.Frame(parent);controls.pack(fill='x',pady=6)
    ttk.Button(controls,text='刷新检测',command=lambda:refresh_skills(app)).pack(side='left')
    ttk.Button(controls,text='导入技能文件夹',command=lambda:import_skills(app)).pack(side='left',padx=6)
    ttk.Button(controls,text='管理导入目录',command=lambda:manage_roots(app)).pack(side='left')
    app.skill_list=tk.Listbox(parent,height=7,exportselection=False)
    app.skill_list.pack(fill='x');app.skill_list.bind('<<ListboxSelect>>',lambda _:show_skill(app))
    app.skill_detail=tk.StringVar();wrapped(parent,variable=app.skill_detail,fill='x',pady=8)
    ttk.Button(parent,text='复制调用指令',command=lambda:copy_skill(app)).pack(anchor='w')
    ttk.Button(parent,text='复制技能说明',command=lambda:copy_skill(app,full=True)).pack(anchor='w',pady=4)
    wrapped(parent,'自定义叫法：在“设置 → 口述调用技能”中添加。调用指令需要目标 AI 支持对应 Skill；复制并不代表技能已经执行。',fill='x',pady=8)
    refresh_skills(app)
    guide=ScrollPane(app.help_tab);guide.pack(fill='both',expand=True);p=guide.content
    label(p,'魔法师手札',size=20,bold=True,anchor='w',pady=10)
    wrapped(p,'1. 点击下面的“去试写”，轻按你的听写键，说一句话。再次轻按同一个键结束，不用一直按住。',fill='x',pady=9)
    wrapped(p,'2. 停顿时改几个字，再继续说。之后在你平时使用的聊天框里点击光标，用同一个快捷键输入。',fill='x',pady=9)
    wrapped(p,'3. 在“咒语库”选择例子。任务需要的参考图会单独提示；整理完成不等于已经生成。',fill='x',pady=9)
    wrapped(p,'普通听写全程本地；GLM 校对需要在设置里填 Key 并启用。网页生图需要自己的 GPT 账号与接力扩展，使用平台额度。',fill='x',pady=9)
    wrapped(p,'“调用领导”可转换为 $leader；发送请在熟悉输入后使用：单独说“发送”，等待期间改字、继续说或按 Esc 可以取消。',fill='x',pady=9)
    from hotkeys import display_binding
    app.guide_keys=tk.StringVar()
    app.guide_keys.set('听写键：'+display_binding(app.config['dictation_key'])+'  工作流键：'+display_binding(app.config['workflow_key']))
    wrapped(p,variable=app.guide_keys,fill='x',pady=9)
    def begin():
        from settings import save_settings
        app.config['onboarding_done']=True
        try:save_settings(root_path(app)/'settings.json',app.config)
        except OSError:app.status.set('引导状态未保存，仍可使用')
        app.tabs.select(app.dict_tab);app.dict_text.focus_set()
    ttk.Button(p,text='去试写',command=begin).pack(anchor='w',pady=8)


def refresh_skills(app):
    app.skills.refresh();app.skill_list.delete(0,'end')
    app.skill_names=sorted(app.skills.skills)
    for name in app.skill_names:app.skill_list.insert('end',name+(' · 重名，请检查目录' if name in app.skills.duplicates else ''))
    if hasattr(app,'skill_combo'):app.skill_combo.configure(values=app.skill_names)
    if app.skill_names:app.skill_list.selection_set(0);show_skill(app)
    else:app.skill_detail.set('未发现技能。可导入包含 SKILL.md 的文件夹；内置创作流程不受影响。')


def selected_skill(app):
    selected=app.skill_list.curselection()
    return app.skill_names[selected[0]] if selected else None


def show_skill(app):
    name=selected_skill(app)
    if name:
        app.skill_detail.set(name+'\n'+app.skills.descriptions.get(name,'')+'\n'+
            ('同名技能来自多个目录，已暂停口述调用。' if name in app.skills.duplicates else '状态：已发现；由目标 AI 执行，未验证其依赖。'))


def import_skills(app):
    folder=filedialog.askdirectory(title='选择技能文件夹或上级目录')
    if not folder:return
    try:app.skills.add_root(folder);refresh_skills(app);app.status.set('已添加技能目录；没有执行其中的脚本')
    except (ValueError,OSError) as exc:app.skill_detail.set(str(exc))


def copy_skill(app,full=False):
    name=selected_skill(app)
    if not name:return
    if name in app.skills.duplicates:app.skill_detail.set('请先移除重名目录，再选择要调用的技能');return
    syntax=app.config.get('skill_syntax','dollar')
    text='/'+name if syntax=='slash' else '请使用 '+name+' 技能。' if syntax=='natural' else '$'+name
    if full:
        try:
            file=Path(app.skills.skills[name])
            if file.stat().st_size>65536:raise ValueError()
            text=file.read_text('utf-8-sig')
        except (OSError,UnicodeError,ValueError):
            app.skill_detail.set('说明无法读取或超过 64 KB，请使用原技能文件');return
    app.root.clipboard_clear();app.root.clipboard_append(text)
    app.status.set('技能说明已复制；不包含引用文件或脚本，依赖仍需目标 AI 支持' if full else '调用指令已复制；请粘贴到支持该技能的 AI，尚未执行')


def manage_roots(app):
    import json
    try:roots=json.loads(app.skills.root_file.read_text('utf-8'))
    except (OSError,ValueError):roots=[]
    if not isinstance(roots,list):roots=[]
    window=tk.Toplevel(app.root);window.title('导入目录');window.geometry('640x260')
    ttk.Label(window,text='移除只取消检测此目录，不删除文件。默认安装目录由程序自动检测。').pack(pady=10)
    listing=tk.Listbox(window,exportselection=False);listing.pack(fill='both',expand=True,padx=12)
    for path in roots:listing.insert('end',path)
    def remove():
        selected=listing.curselection()
        if not selected:return
        try:
            app.skills.remove_root(listing.get(selected[0]));listing.delete(selected[0]);refresh_skills(app)
        except OSError:app.status.set('目录记录未能保存')
    ttk.Button(window,text='移除所选目录记录',command=remove).pack(pady=8)


def shutdown(app):
    for _,model in getattr(app,'v1_jobs',{}).values():model.close()
    if hasattr(app,'v1_jobs'):app.v1_jobs.clear()


def proof_snapshot(app):
    return (app.dict_text.get('1.0','end-1c'),app.state.generation,
            app.local_input_epoch,app.personal.active)


def build_proofread(app,parent):
    from ui import wrapped
    app.proof_candidate=None
    app.proof_hint=tk.StringVar(value='可选一段手动校对；建议先预览，再由你采用。')
    row=ttk.Frame(parent);row.pack(fill='x',pady=(4,0))
    ttk.Button(row,text='校对这一段',command=lambda:manual_proofread(app)).pack(side='left')
    ttk.Button(row,text='取消校对',command=lambda:cancel_proofread(app)).pack(side='left',padx=6)
    app.proof_apply=ttk.Button(row,text='采用建议',state='disabled',command=lambda:apply_proofread(app))
    app.proof_apply.pack(side='right')
    wrapped(parent,variable=app.proof_hint,fill='x',pady=4)
    app.proof_preview=tk.Text(parent,height=3,wrap='word',state='disabled',font=('Microsoft YaHei UI',10))
    # Keep the writing area large until there is an actual suggestion.


def preview(app,text):
    if text:app.proof_preview.pack(fill='x',pady=(4,0))
    else:app.proof_preview.pack_forget()
    app.proof_preview.configure(state='normal');app.proof_preview.delete('1.0','end')
    app.proof_preview.insert('1.0',text);app.proof_preview.configure(state='disabled')


def cancel_proofread(app):
    invalidate(app,'proofread');app.proof_candidate=None
    app.proof_apply.configure(state='disabled');preview(app,'')
    app.proof_hint.set('已取消采用本次结果 · 草稿保留，已发出的远端请求可能仍在处理')


def manual_proofread(app):
    if app.state.mode!='idle' or app.stopping:
        app.proof_hint.set('结束听写后再校对，当前文字保持');return
    if 'proofread' in app.v1_jobs:
        app.proof_hint.set('正在校对，最多等待 90 秒；可继续改字或取消');return
    widget=app.dict_text
    try:start,end=widget.index('sel.first'),widget.index('sel.last')
    except tk.TclError:start,end='1.0',widget.index('end-1c')
    text=widget.get(start,end)
    if not text.strip():app.proof_hint.set('先说出或输入一段文字');return
    if len(text)>300:app.proof_hint.set('请选中不超过 300 字的一段文字再校对');return
    protected=tuple(p['text'] for p in app.personal.phrases() if p['protected'])
    frozen=(proof_snapshot(app),start,end,text,protected)
    app.proof_candidate=None;app.proof_apply.configure(state='disabled');preview(app,'')
    def request(model):
        from light_correction import supported_parts
        proposal=model.propose_with_evidence(text,protected=protected)
        safe=supported_parts(text,proposal,protected)
        return safe if safe is not None else text
    if launch(app,'proofread',request,frozen):
        app.proof_hint.set('GLM 后台校对中，最多等待 90 秒；结果先预览，不自动改字')


def proofread_result(app,frozen,data,error):
    if frozen[0]!=proof_snapshot(app) or app.state.mode!='idle' or app.stopping:
        app.proof_hint.set('草稿已变化，已忽略旧建议；可以重新校对');return
    if error:app.proof_hint.set(error);return
    if data==frozen[3]:
        app.proof_hint.set('已核对，未发现足够可靠的修改，保留原话');return
    app.proof_candidate=(frozen,data);preview(app,data)
    app.proof_apply.configure(state='normal')
    app.proof_hint.set('下方为校对建议；采用后只更新本页草稿，不改外部聊天框、不发送')


def apply_proofread(app):
    if not app.proof_candidate:return
    frozen,text=app.proof_candidate
    if frozen[0]!=proof_snapshot(app) or app.state.mode!='idle' or app.stopping:
        cancel_proofread(app);app.proof_hint.set('草稿已变化，建议未采用，请重新校对');return
    app.learning.reset();app._forget_owned()
    app.dict_text.edit_separator()
    app.dict_text.delete(frozen[1],frozen[2]);app.dict_text.insert(frozen[1],text)
    app.dict_text.edit_separator();app.local_input_epoch+=1
    app.learning.reset();app._save_draft()
    app.proof_candidate=None;app.proof_apply.configure(state='disabled')
    app.proof_hint.set('已更新本页草稿，可撤销或复制；没有发送，也没有写入个人纠错规则')
