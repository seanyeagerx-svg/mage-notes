"""Literal voice-command filtering and conservative send eligibility; no I/O."""
from dataclasses import dataclass
import re


def speaker_command_allowed(utterance):
    """Voice focus may veto a command; a selected voice cannot create one."""
    focus=utterance.get('speaker_focus') or {}
    if not focus.get('reference_available'):
        return True
    if not focus.get('owner_confirmed'):
        return False
    if focus.get('action')=='separated':
        return command_text(utterance.get('online',''))==command_text(utterance.get('command_online',''))
    return True

COMMANDS={"发送":"send", "输入发送":"literal", "取消发送":"cancel"}

def command_text(text):
    # Do not normalize homophones, negation, quotes, questions, or filler words.
    return re.sub(r"\s+", "", text).rstrip("。.!！")

class CommandFilter:
    def __init__(self, commands=None):
        self.commands=COMMANDS if commands is None else commands
        self.reset()
    def reset(self):
        self.raw=""
        self.holding=True
    def feed(self,delta):
        self.raw+=delta
        if not self.holding:return delta
        compact=command_text(self.raw)
        if any(word.startswith(compact) or word==compact for word in self.commands):return ""
        self.holding=False
        return self.raw
    def finish(self,final):
        action=None; text=""
        if self.holding:
            if command_text(self.raw)==command_text(final):
                action=self.commands.get(command_text(final))
            if action=="literal":text="发送"
            elif action is None:text=self.raw
        self.reset()
        return text,action

def previous_veto(text):
    """Avoid joining '不要 [pause] 发送' into a send instruction."""
    text=command_text(text)
    return bool(re.search(r"(?:不要|不用|别|不需要|不想|不能|不许|暂不|先不|不要再|别再|等一下|先等等)$",text)
                or re.search(r"(?:不要|不用|别|不需要|不能|先不).{0,6}发送",text))

@dataclass
class SendIntent:
    generation:int
    epoch:tuple
    requested_at:float
    audio_serial:int
    phase:str="waiting"
    deadline:float=0.0
    snapshot:object=None
    probe_token:object=None
