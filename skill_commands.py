"""Explicit spoken skill names expand to text, never execute or send anything."""
import json
import os
from pathlib import Path
import re

DEFAULT_ALIASES = {
    'leader':['leader','领导','任务拆解','里德','利德'],
    'human-writing':['human writing','中文写作','写作'],
    'prompt-delivery':['prompt delivery','提示词交付'],
    'rework-diagnosis':['rework diagnosis','返工诊断'],
    'project-handoff':['project handoff','项目交接'],
}
INVOCATION = r'^\s*(?:[嗯呃啊][，, ]*){0,3}(?:请帮我|请|帮我)?(?:调用|使用|启用|启动|用)(?:一下)?\s*(?:技能|skill)?\s*'


class SkillCommands:
    def __init__(self, path, roots=None):
        self.path = Path(path)
        self.root_file=self.path.with_name('skill_roots.json')
        self.roots=roots if roots is not None else [Path(os.environ.get('CODEX_HOME',Path.home()/'.codex'))/'skills',Path.home()/'.agents/skills']
        self.refresh()

    def refresh(self):
        self.skills = {}
        self.descriptions={};self.duplicates=set();self.issues=[]
        roots=list(self.roots)
        try:
            extra=json.loads(self.root_file.read_text('utf-8'))
            if isinstance(extra,list):roots.extend(Path(p) for p in extra[:64] if isinstance(p,str))
        except (OSError,ValueError):pass
        for root in roots:
            root=Path(root)
            files=([root/'SKILL.md'] if (root/'SKILL.md').is_file() else [])+list(root.glob('*/SKILL.md'))+list(root.glob('skills/*/SKILL.md'))
            for file in files[:1000]:
                try:
                    if file.stat().st_size>1_000_000:continue
                    header = file.read_text('utf-8-sig')[:12000]
                    if not header.startswith('---\n') and not header.startswith('---\r\n'):
                        continue
                    header = header.split('---',2)[1]
                    match = re.search(r'^name:\s*[\'"]?([a-z][a-z0-9_-]{0,63})[\'"]?\s*$',header,re.M)
                    if match:
                        name=match[1];resolved=str(file.resolve())
                        if name in self.skills and self.skills[name]!=resolved:self.duplicates.add(name)
                        self.skills.setdefault(name,resolved)
                        description=re.search(r'^description:\s*(.+)$',header,re.M)
                        self.descriptions.setdefault(name,description[1].strip(' \"\'')[:600] if description else '未提供用途说明')
                except (OSError,UnicodeError):
                    continue
        self.aliases = {name:list(DEFAULT_ALIASES.get(name,[name,name.replace('-',' ')])) for name in self.skills}
        try:
            data=json.loads(self.path.read_text('utf-8'))
            if isinstance(data,dict):
                for name,terms in data.items():
                    if name in self.skills and isinstance(terms,list):
                        self.aliases[name]=list(dict.fromkeys([name]+[t for t in terms if self.valid(t)]))
        except (OSError,ValueError):
            pass

    def add_root(self,folder):
        root=Path(folder).resolve()
        if not root.is_dir():raise ValueError('请选择包含 SKILL.md 的技能文件夹或它的上级目录')
        files=([root/'SKILL.md'] if (root/'SKILL.md').is_file() else [])+list(root.glob('*/SKILL.md'))+list(root.glob('skills/*/SKILL.md'))
        candidates=[]
        for file in files[:1000]:
            try:
                header=file.read_text('utf-8-sig')[:12000]
                if header.startswith(('---\n','---\r\n')) and re.search(r'^name:\s*[\'\"]?[a-z][a-z0-9_-]{0,63}[\'\"]?\s*$',header.split('---',2)[1],re.M):candidates.append(file)
            except (OSError,UnicodeError):pass
        if not candidates:raise ValueError('目录中未找到有效 SKILL.md；不会安装或运行任何脚本')
        try:roots=json.loads(self.root_file.read_text('utf-8'))
        except (OSError,ValueError):roots=[]
        if not isinstance(roots,list):roots=[]
        roots=list(dict.fromkeys([p for p in roots if isinstance(p,str)]+[str(root)]))
        if len(roots)>64:raise ValueError('已达到 64 个技能目录上限')
        tmp=self.root_file.with_suffix('.tmp');tmp.write_text(json.dumps(roots,ensure_ascii=False,indent=2),'utf-8');tmp.replace(self.root_file)
        self.refresh()

    def remove_root(self,folder):
        try:roots=json.loads(self.root_file.read_text('utf-8'))
        except (OSError,ValueError):return
        roots=[p for p in roots if p!=folder] if isinstance(roots,list) else []
        tmp=self.root_file.with_suffix('.tmp');tmp.write_text(json.dumps(roots,ensure_ascii=False,indent=2),'utf-8');tmp.replace(self.root_file)
        self.refresh()

    @staticmethod
    def valid(term):
        return isinstance(term,str) and 2<=len(term)<=48 and bool(re.fullmatch(r'[\w -]+',term))

    def add(self,name,alias):
        alias=alias.strip()
        if name not in self.skills or not self.valid(alias):
            raise ValueError('请选择已安装的技能，口令用 2—48 个字母或汉字。')
        norm=lambda s:re.sub(r'[\s_-]','',s.casefold())
        if any(norm(alias)==norm(a) for n,terms in self.aliases.items() if n!=name for a in terms):
            raise ValueError('这个口令已经对应另一项技能。')
        self.aliases[name]=list(dict.fromkeys(self.aliases[name]+[alias]))
        temporary=self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.aliases,ensure_ascii=False,indent=2),'utf-8')
        temporary.replace(self.path)

    def expand(self,text,syntax='dollar'):
        # Requiring an opening invocation keeps discussion, quotes and negation
        # in ordinary dictation. A model cannot invent a new executable command.
        match=re.match(INVOCATION,text,re.I)
        if not match:
            return None
        rest=text[match.end():]
        found=[]
        for name,terms in self.aliases.items():
            for term in terms:
                pattern=re.escape(term).replace(r'\ ',r'[\s_-]*').replace(r'\-',r'[\s_-]*')
                pattern=r'(?:'+pattern+'|“'+pattern+'”|"'+pattern+'")'
                m=re.match(pattern+r'(?=这个技能|技能|skill\b|[\s，,。:：]|帮我|来|$)',rest,re.I)
                if m:
                    found.append((m.end(),name))
        if not found:
            return None
        length=max(n for n,name in found)
        names={name for n,name in found if n==length}
        if len(names)!=1:
            return None
        name=names.pop()
        if name in getattr(self,'duplicates',()):return None
        body=re.sub(r'^\s*(?:(?:这个)?技能|skill\b)?[\s，,。:：]*','',rest[length:],flags=re.I)
        if re.match(r'^(?:是什么意思|什么意思|怎么用|是什么|有啥用|有什么用)',body):
            return None
        prefix = f'/{name}' if syntax=='slash' else f'请使用 {name} 技能。' if syntax=='natural' else f'${name}'
        return prefix + (' '+body if body else ' ')

    def expand_corrected(self,original,corrected,syntax='dollar'):
        # Correction can repair the name, but cannot invent an opening command.
        if not re.match(INVOCATION,original,re.I):
            return None
        return self.expand(corrected,syntax)
