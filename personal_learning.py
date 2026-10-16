"""Learn edits only inside the last dictated range, in the same verified field.

The watcher expires or clears on focus/profile change. No unrelated keyboard
contents, fields, passwords or clipboard are persisted. With local audio
learning enabled, verified small edits can retain the matching short utterance.
"""
from dataclasses import dataclass, replace
import time
import uuid
from learning_status import record
from win_input import foreground, focused_control, window_process


@dataclass
class Watch:
    text: str
    start: int
    end: int
    identity: tuple
    event_id: str
    captured_at: float
    audio_samples: tuple = ()
    profile: str = ''
    input_epoch: tuple = None


class PersonalLearning:
    def __init__(self, app, store):
        self.app, self.store = app, store
        self.watch = None
        self.dirty = False
        self.manual_at = None
        self.next_arm = 0.0
        self.token = None
        self.serial = 0
        self.session = uuid.uuid4().hex
        self.last_usage = None
        self.last_status = '待命 · 只学习你亲手改的词'

    def hint(self,text):
        self.last_status=text
        variable=getattr(self.app,'learning_hint',None)
        if variable is not None and variable.get()!=text:variable.set(text)

    def reset(self):
        self.watch = None
        self.manual_at = None
        self.dirty = False
        self.token = None
        self.serial += 1
        self.session = uuid.uuid4().hex
        self.last_usage = None
        self.hint('待命 · 只学习你亲手改的词' if self.store.learning else '自动学习已关闭 · 已确认词库仍可使用')

    def valid_target(self):
        a = self.app
        if not self.store.learning:
            return False
        if a.internal:
            return a.root.focus_get() is a.active_text() and foreground() == a.target
        return (foreground() == a.target and focused_control(a.target) == a.target_control
                and window_process(a.target) == a.target_process)

    def written(self, previous=None):
        if not self.store.learning:
            return
        # The next human key can arrive before the delayed field snapshot.
        # Advance only this verified program-owned span immediately, so an
        # automatic correction cannot be counted as a human preference.
        watch = self.watch
        if watch is not None and self.manual_at is None:
            a = self.app
            if (previous is not None and watch.text[watch.start:watch.end] == previous
                    and watch.profile == self.store.active
                    and watch.input_epoch == a.owned_epoch == a._input_epoch()
                    and time.monotonic() - watch.captured_at <= 300
                    and self.valid_target()):
                owned = a.owned_text
                text = watch.text[:watch.start] + owned + watch.text[watch.end:]
                samples = (a.pronunciation.snapshot(self.store.active, owned)
                    if hasattr(a, 'pronunciation') and a.config.get('audio_learning', False) else ())
                self.watch = (replace(watch, text=text, end=watch.start + len(owned),
                    audio_samples=samples, captured_at=time.monotonic()) if len(text) <= 64000 else None)
            else:
                self.watch = None
        self.dirty = True
        self.hint('等文字落入输入框，再观察你的手改')
        self.next_arm = time.monotonic() + .18
        # Program output isn't a human edit. Keep a pending human comparison
        # until it has been read, even when the next utterance already arrives.
        if self.manual_at is None:
            self.token = None

    def audio_staged(self, owned):
        """Join a late clip to an unchanged watch without rearming an edit.

        Streaming text can settle before final audio arrives. If the acoustic
        result leaves that text unchanged, no write event refreshes the watch.
        Only attach to the exact same owned range and person; never follow a
        pending human edit or try to relocate an old range by matching text.
        """
        watch = self.watch
        if (not watch or self.manual_at is not None or not self.store.learning
                or not self.app.config.get('audio_learning', False)
                or watch.profile != self.store.active
                or time.monotonic() - watch.captured_at > 300
                or watch.text[watch.start:watch.end] != owned
                or self.app.owned_epoch != self.app._input_epoch()):
            return
        watch.audio_samples = self.app.pronunciation.snapshot(self.store.active, owned)

    def manual_edit(self):
        if self.watch and self.valid_target():
            self.manual_at = time.monotonic()
            self.token = None

    def observe_usage(self, text, generation):
        signature = (generation, text, self.app.raw_transcript)
        if signature != self.last_usage:
            self.store.observe_usage(text)
            self.last_usage = signature

    def _internal_snapshot(self, phase):
        a = self.app
        widget = a.active_text()
        text = widget.get('1.0','end-1c')
        identity = ('internal',str(widget))
        if phase == 'edit':
            self._edited(text, identity)
        elif a.owned_text and a.owned_epoch == a._input_epoch():
            start = len(widget.get('1.0','voice_owned_start'))
            end = len(widget.get('1.0','voice_owned_end'))
            if text[start:end] == a.owned_text:
                self._armed(text,start,end,identity)

    def _armed(self, text, start, end, identity):
        if self.manual_at is not None:
            return
        self.serial += 1
        self.watch = Watch(text,start,end,identity,self.session + ':' + str(self.serial),time.monotonic(),
                           profile=self.store.active,input_epoch=self.app._input_epoch())
        if hasattr(self.app,'pronunciation'):
            self.watch.audio_samples=self.app.pronunciation.snapshot(self.store.active,text[start:end])
        self.dirty = False
        self.hint('正在观察这次输入 · 只记录同一段文字的亲手改词')

    def _edited(self, text, identity):
        watch = self.watch
        self.manual_at = None
        if (not watch or identity != watch.identity or watch.profile != self.store.active
                or not isinstance(text,str) or len(text)>64000):
            self.watch = None
            return
        prefix, suffix = watch.text[:watch.start], watch.text[watch.end:]
        if not text.startswith(prefix) or (suffix and not text.endswith(suffix)):
            # Editing other text or clearing/submitting a field is not training.
            self.watch = None
            return
        end = len(text)-len(suffix) if suffix else len(text)
        before, after = watch.text[watch.start:watch.end], text[watch.start:end]
        learned = self.store.observe_edit(before,after,watch.event_id)
        if before!=after:record(self.store,'human_edit')
        if before!=after:
            self.hint('已记录你的改词 · 待重复确认后启用' if learned else '已记录文字修改 · 未形成可用短词规则')
        samples,saved=(),0
        if (hasattr(self.app,'pronunciation') and self.app.config.get('audio_learning',False)
                and self.store.learning):
            try:
                samples,saved=self.app.pronunciation.learn(watch.audio_samples,before,after,watch.event_id,self.store.active)
            except Exception:
                self.app.correction_hint.set('改词已保留；这次短录音暂未入库')
        if hasattr(self.app,'_sync_recognition_context'):
            self.app._sync_recognition_context(after[-160:])
        if hasattr(self.app,'proofreading'):
            try: self.app.proofreading.human_edit(self.store.active,before,after)
            except Exception: self.app.correction_hint.set('这次修改未能记入校对记录，当前文字保留')
        if learned:
            if any(row[0]=='reverted' for row in learned):
                hint='已停用被你改回的规则'
            elif any(row[0]=='active' for row in learned):
                hint='已记住你的改法 · 只用于相同语境'
            else:
                hint='已记下这次改词 · 重复确认后再自动使用'
            self.app.correction_hint.set(hint)
            self.hint(hint)
        if saved:self.app.correction_hint.set('已保存改词和对应短录音 · 仅在本机')
        elif hasattr(self.app,'pronunciation') and self.app.pronunciation.notice:
            self.app.correction_hint.set(self.app.pronunciation.notice)
        if before!=after and self.app.config.get('audio_learning',False) and self.store.learning:
            record(self.store,'audio_saved' if saved else ('edit_without_audio' if not watch.audio_samples else 'audio_not_word_edit'))
        self.watch = Watch(text,watch.start,end,identity,watch.event_id,time.monotonic(),samples,watch.profile,
                           self.app._input_epoch())

    def tick(self):
        a = self.app
        if not self.store.learning or (self.watch and not self.valid_target()):
            self.reset()
            return
        now = time.monotonic()
        if self.watch and now-self.watch.captured_at>300:
            self.reset()
            return
        if self.token:
            if now-self.token[-1] > 1.5:
                self.token = None
                self.next_arm = now + 1
            return
        phase = None
        if self.manual_at is not None:
            if now-self.manual_at >= 1.1:
                phase = 'edit'
        # Observe text as soon as it has reached the editor. A slow acoustic or
        # text correction must not leave the first human edit unobserved.
        # written() refreshes this baseline for verified program changes, so
        # arming now does not turn later automatic corrections into training.
        elif self.dirty and now>=self.next_arm and a.owned_text and not a.pending:
            if a.owned_epoch == a._input_epoch() and self.valid_target():
                phase = 'arm'
        if not phase:
            return
        if a.internal:
            self._internal_snapshot(phase)
        else:
            self.serial += 1
            self.token = ('learning',phase,self.serial,a._input_epoch(),self.store.active,now)
            a.sender.probe.request(self.token,a.target,a.target_control,a.target_process,include_text=True)

    def on_snapshot(self, token, snapshot):
        if token != self.token:
            return
        self.token = None
        a = self.app
        if token[4] != self.store.active or token[3] != a._input_epoch() or not self.valid_target():
            return
        if not snapshot or snapshot.text is None or time.monotonic()-snapshot.captured_at>.6:
            self.next_arm = time.monotonic()+2
            if token[1] == 'edit':
                self.watch = None
                self.manual_at = None
            self.hint('当前输入框无法自动学习 · 可在草稿中选中文字，点“改词入库”')
            record(self.store,'field_stale' if snapshot and snapshot.text is not None else 'field_unreadable')
            return
        if token[1] == 'edit':
            self._edited(snapshot.text,snapshot.identity)
        else:
            text, owned = snapshot.text, a.owned_text
            if not owned or a.owned_epoch != a._input_epoch():
                return
            # Do not guess among duplicated text spans in an external editor.
            if text.count(owned) != 1:
                self.hint('无法确认这段文字的位置 · 可选中文字“改词入库”')
                record(self.store,'field_ambiguous')
                self.next_arm = time.monotonic()+1
                return
            start = text.index(owned)
            self._armed(text,start,start+len(owned),snapshot.identity)
