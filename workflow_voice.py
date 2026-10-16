"""Literal workflow controls. Acoustic agreement can veto, never invent actions."""
from voice_send import CommandFilter, command_text, previous_veto
from video_entry import START_PHRASES

COMMANDS={'整理任务':'prepare','复制提示词':'copy','交给接力站':'relay','添加参考图':'references'}
COMMANDS.update({phrase: 'video_start' for phrase in START_PHRASES})


class WorkflowVoice:
    def __init__(self,commands=None):
        self.commands=COMMANDS if commands is None else commands
        self.reset()

    def reset(self):
        self.filter=CommandFilter(self.commands);self.previous=''

    def feed(self,text):return self.filter.feed(text)

    def finish(self,raw,verified=None,speaker_allowed=True):
        held=self.filter.raw if self.filter.holding else ''
        released,action=self.filter.finish(raw)
        if action and (not speaker_allowed or verified is None
                       or command_text(raw)!=command_text(verified) or previous_veto(self.previous)):
            released,action=held,None
        if not action:self.previous=raw
        return released,action
