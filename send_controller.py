"""Voice-send state machine. The only side effect is one verified send shortcut."""
import math
import time
from focus_probe import FocusProbe
from voice_send import CommandFilter, SendIntent, previous_veto, command_text
from win_input import foreground, focused_control, window_process, modifiers_down, send_shortcut

class VoiceSendController:
    def __init__(self,app):
        self.app=app
        self.filter=CommandFilter()
        self.probe=FocusProbe(app.events)
        self.pending=None
        self.anchor=None
        self.previous=""
        self.written=False
        self.serial=0
        self.anchor_token=None
        self.next_check=0.0

    def active(self):
        return bool(self.app.config.get("voice_send",True) and not self.app.internal
                    and self.app.recording_mode=="dictation")

    def start(self):
        self.cancel("")
        self.filter.reset()
        self.previous="";self.written=False;self.anchor=None
        self.anchor_token=None
        if self.active():
            self.serial+=1
            self.anchor_token=("origin",self.serial,self.app.state.generation)
            self.probe.request(self.anchor_token,self.app.target,self.app.target_control,self.app.target_process)

    def reset_segment(self):
        self.filter.reset()

    def delta(self,text):
        if not self.active():return text
        if self.pending:self.cancel("已取消发送：你继续说话了。")
        return self.filter.feed(text)

    def sentence(self,raw,verified=None,speaker_allowed=True):
        if not self.active():return "",False
        held = self.filter.raw if self.filter.holding else ""
        text,action=self.filter.finish(raw)
        if action=='send' and not speaker_allowed:
            text,action=held,None
        if action and verified is not None and command_text(raw) != command_text(verified):
            # A second acoustic reading may veto a command, never create one.
            text,action=held,None
        if action=="send":
            if previous_veto(self.previous):
                self.app.status.set("前一句含有阻止发送的意思，已保留文字，未发送。")
                text="发送"
            else:self.request()
        elif action=="cancel":self.cancel("已取消发送，文字保留。")
        if action not in ("send","cancel"):self.previous=raw
        return text,action in ("send","cancel")

    def request(self):
        a=self.app
        if (not self.active() or a.state.mode!="dictation" or a.state.paused or a.stopping):return
        if not self.written or not self.anchor:
            a.status.set("尚未确认本次输入框和正文，已保留文字，未发送。")
            return
        self.pending=SendIntent(a.state.generation,a._input_epoch(),time.monotonic(),a.engine.audio_serial)
        a.status.set("收到独立的“发送” · 等最后文字整理完成；继续说或按 Esc 可取消")

    def cancel(self,reason="已取消发送，文字保留。"):
        had=self.pending is not None
        self.pending=None
        if had and reason:self.app.status.set(reason)

    def activity(self):
        if self.pending:self.cancel("已取消发送：检测到新的声音，文字保留。")

    def partial(self,text):
        if text and self.pending:self.cancel("已取消发送：你继续说话了。")

    def _valid(self):
        a=self.app;p=self.pending
        return bool(p and self.active() and a.state.generation==p.generation
            and a._input_epoch()==p.epoch and a.engine.audio_serial==p.audio_serial
            and foreground()==a.target and focused_control(a.target)==a.target_control
            and window_process(a.target)==a.target_process and not modifiers_down())

    def _query(self,phase):
        p=self.pending
        self.serial+=1
        p.probe_token=(phase,self.serial,p.generation)
        self.probe.request(p.probe_token,self.app.target,self.app.target_control,self.app.target_process)
        self.next_check=time.monotonic()+.25

    def _audio_ready(self):
        engine=self.app.engine
        audio=getattr(engine,'audio',None)
        return ((audio is None or audio.empty()) and
                not getattr(getattr(engine,'decoder',None),'refinement_in_progress',False))

    def on_snapshot(self,token,snapshot):
        a=self.app
        if token==self.anchor_token:
            if snapshot and token[2]==a.state.generation:self.anchor=snapshot.identity
            self.anchor_token=None
            return
        p=self.pending
        if not p or token!=p.probe_token:return
        p.probe_token=None
        if (not self._valid() or not snapshot or snapshot.identity!=self.anchor or not snapshot.nonempty
                or time.monotonic()-snapshot.captured_at>.4):
            self.cancel("输入框或正文已变化，已取消发送。")
            return
        if p.snapshot and p.snapshot.digest!=snapshot.digest:
            self.cancel("正文已经变化，已取消发送。")
            return
        phase=token[0]
        if phase=="arm":
            p.snapshot=snapshot;p.phase="countdown";p.deadline=time.monotonic()+2.0
        elif phase=="final":
            if not self._audio_ready():
                # Even a quiet tail may not have reached VAD/audio_serial yet.
                # Keep capturing, then re-arm with a fresh field snapshot.
                p.phase="waiting"
                return
            p.phase="closing"
            # Stop and drain microphone/ASR before the last identity check. Any
            # trailing utterance, edit, focus change or new sound cancels this.
            a.finish(voice_send=True)
        elif phase=="dispatch":
            self.pending=None  # Never retry even if the target rejects the key.
            try:
                sent=send_shortcut(a.target,a.target_control,a.config.get("send_key","enter"))
                a.status.set("已按发送键 · 本次会话已结束" if sent else "输入位置变化，已保留文字，未发送。")
            except OSError as exc:a.status.set(str(exc))

    def tick(self):
        p=self.pending
        if not p:return
        a=self.app;now=time.monotonic()
        if not self._valid():
            self.cancel("已取消发送：有新声音、手动操作或输入位置变化。")
            return
        if now-p.requested_at>20:
            self.cancel("整理或输入框检查超时，文字已保留，未发送。")
            return
        if p.phase=="waiting":
            if not a.pending and not a.cleanup_waiting and not a.cleanup_boundary and self._audio_ready():
                p.phase="arming";self._query("arm")
        elif p.phase=="countdown":
            a.status.set(f"{max(1,math.ceil(p.deadline-now))} 秒后发送 · 继续说、改字或按 Esc 可取消")
            if not p.probe_token:
                if now>=p.deadline:self._query("final")
                elif now>=self.next_check:self._query("check")
        elif p.phase=="closing" and a.state.mode=="idle" and not a.stopping and not a.pending:
            p.phase="dispatching";self._query("dispatch")

    def close(self):
        self.cancel("");self.probe.close()
