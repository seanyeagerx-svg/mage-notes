"""VoiceInput: local streaming dictation with two customizable global triggers."""
import argparse
import ctypes
from collections import deque
from datetime import datetime
import json
import logging
from pathlib import Path
import queue
import sqlite3
import time
import traceback
import os
import webbrowser
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from core import SessionState, route_workflow, validate_bindings, LABELS
from engine import SpeechEngine
from settings import load_settings, save_settings as write_settings
from workflows import prepare_workflow, save_bundle
from win_input import GlobalHooks, foreground, type_unicode, modifiers_down, make_nonactivating, top_handle, window_process, focused_control, restore_input_focus, paste_shortcut, show_existing_app, replace_owned_tail

ROOT = Path(__file__).resolve().parent


class App:
    def __init__(self, root, no_hooks=False, draft_path=None):
        self.root = root
        self.no_hooks=no_hooks
        self.root.title("魔法师手札")
        self.root.geometry("850x720")
        self.root.minsize(760, 640)
        self.root.configure(bg="#f4f6f9")
        self.events = queue.Queue()
        self.state = SessionState()
        self.ready = False
        self.model_error = None
        self.stopping = False
        self.recording_mode = "dictation"
        self.target = None
        self.target_control = None
        self.target_process = None
        self.internal = False
        self.focus_blocked = False
        self.last_edit = 0.0
        self.edit_until = 0.0
        self.resume_position_pending = False
        self.route_on_drain = False
        self.pending = deque()
        self.refs = []
        self.origin_window = None
        self.origin_process = None
        self.origin_control = None
        self.workflow_plan = None
        self.last_bundle = None
        self.bundle_signature = None
        self.workflow_completed = False
        from workflow_voice import WorkflowVoice
        self.workflow_voice = WorkflowVoice()
        self.workflow_voice_action = None
        from video_entry import START_PHRASES
        self.project_voice = WorkflowVoice({'发送':'project_send','确认剧情进入分镜':'project_confirm',
            **{phrase:'video_start' for phrase in START_PHRASES}})
        self.project_voice_action = None
        self.project_on_drain = False
        self.cleaner = None
        self.cleanup_serial = 0
        # 显式识别方式切换的未决状态：pending 表示最近一次 refinement 请求
        # 还没有以“携带同一请求身份的 ready”收口。请求身份把首次加载的
        # ready、较早请求的完成与最新目标区分开；失败后界面不得假就绪，
        # 重复保存同一选择必须重发并真实重试。
        self.mode_switch_pending = False
        self.mode_switch_serial = 0
        self.mode_switch_request = None
        self.cleanup_waiting = None
        self.cleanup_boundary = False
        self.owned_text = ""
        self.owned_epoch = None
        self.write_settle_until = 0
        self.acoustic_edits = []
        self.light_jobs = {}
        self.local_input_epoch = 0
        self.last_cleanup_original = ""
        self.raw_transcript = ""
        self.raw_mode = None
        # 识别/录音失败后的会话边界：本轮已收到的正文要可见、可保存，
        # 迟到事件只按普通正文留存，不再触发口令、工作流或发送。
        self.session_failed = False
        self.hooks = None
        self.hook_error = None
        self.closing = False
        # 本次会话的真实结束原因；仅元数据（原因、时间、积压秒数、版本），
        # 不含任何转写正文。UI 在 stopped 后持续显示，直到下一次会话开始。
        self.stop_reason = None
        from session_diagnostics import SessionLog
        self.session_log = SessionLog(ROOT / 'data')
        # 保存失败后窗口保留等待重试；这个过渡期仍属关窗边界：
        # poll 只继续收文字，不恢复普通事件处理，避免迟到的语音口令被执行。
        self.close_boundary = False
        self.capture_index=None
        self.capture_serial=0
        self.config = load_settings(ROOT / "settings.json")
        from project_store import ProjectStore
        self.projects = ProjectStore(ROOT)
        from skill_commands import SkillCommands
        self.skills = SkillCommands(ROOT / 'skill_commands.json')
        from personalization import PersonalStore
        from personal_learning import PersonalLearning
        self.personal = PersonalStore(ROOT / "data" / "personalization.sqlite3")
        from pronunciation_memory import PronunciationMemory
        self.pronunciation = PronunciationMemory(ROOT / 'data' / 'pronunciation.sqlite3')
        from proofreading_history import ProofreadingHistory
        self.proofreading = ProofreadingHistory(ROOT / 'data' / 'proofreading.sqlite3')
        self.personal.migrate_explicit_legacy(ROOT / "text_corrections.json")
        self.learning = PersonalLearning(self, self.personal)
        self.learning_fault = False
        self.history = None
        self.reuse_library = None
        self.history_error = None
        self.archive_pending = False
        try:
            from draft_history import DraftHistory
            self.history = DraftHistory(ROOT / 'data' / 'draft-history.sqlite3')
        except Exception as exc:
            self.history_error = type(exc).__name__
        from send_controller import VoiceSendController
        self.sender = VoiceSendController(self)
        self._build()
        self._build_hud()
        self.drafts=None
        self.draft_writer=None
        self.draft_dirty=True
        self.draft_refs=()
        self.draft_fault=False
        if draft_path is not None:
            from draft_store import DraftStore
            self.drafts=DraftStore(draft_path)
            recovered=self.drafts.load()
            if recovered is not None:
                self.restore_draft(recovered)
                self.archive_draft('startup')
            from draft_writer import DraftWriter
            self.draft_writer=DraftWriter(self.drafts,ROOT/'data'/'草稿留存')
            for widget in (self.dict_text,self.flow_text):
                widget.edit_modified(False)
                widget.bind('<<Modified>>',self._draft_changed,add='+')
            self.root.after(2000,self._autosave_draft)
        self.engine = SpeechEngine(self.events, enhanced=self.config['correction_mode'] == 'light')
        self.engine.command('volume_window',enabled=self.config.get('volume_filter',False),
                            minimum=self.config.get('volume_min_db',-65),maximum=self.config.get('volume_max_db',-4))
        self._sync_recognition_context()
        self.runtime_started_at=datetime.now().astimezone().isoformat()
        self._runtime_status('starting')
        if self.config.get("auto_correct",True) and self.config.get("correction_mode") == "light":
            self._light_worker()
        if not no_hooks:
            try:
                self.hooks = GlobalHooks(self.events, (self.config["dictation_key"], self.config["workflow_key"]))
                self.hooks.enabled = self.config["enabled"]
                self.hooks.ui_windows={top_handle(self.root),top_handle(self.hud)}
                self.hooks.start()
            except Exception as exc:
                self.hook_error = str(exc)
                self.status.set("快捷键不可用，可用试写按钮：" + str(exc))
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(25, self.poll)
        if not no_hooks:
            self.project_page.start_bridge()

    def _build(self):
        from ui import build
        build(self)
        for widget in (self.dict_text, self.flow_text):
            widget.bind("<KeyPress>", self._local_edit, add="+")
            widget.bind("<ButtonPress-1>", self._local_edit, add="+")

    def _runtime_status(self,state):
        if self.drafts is not None:
            try:
                from runtime_status import write_status
                write_status(self.drafts.path.parent,self.runtime_started_at,state,self.engine)
            except (OSError,ValueError):
                pass

    def restore_draft(self,data):
        from draft_store import validate_draft
        draft=validate_draft(data)
        for name,widget in (('dictation',self.dict_text),('workflow',self.flow_text)):
            widget.delete('1.0','end')
            widget.insert('1.0',draft[name])
            widget.edit_reset()
        self.refs=draft['refs']
        self.ref_hint.set(f'已选 {len(self.refs)} 项素材' if self.refs else '未添加图片')
        self.status.set('上次草稿已恢复，尚未开始录音')

    def draft_data(self):
        return dict(dictation=self.dict_text.get('1.0','end-1c'),
                    workflow=self.flow_text.get('1.0','end-1c'),refs=list(self.refs))

    def _draft_changed(self,event):
        if event.widget.edit_modified():
            self.draft_dirty=True
            event.widget.edit_modified(False)

    def archive_draft(self, reason):
        try:
            if self.history is None:
                raise OSError('历史库不可用')
            self.history.archive(self.draft_data(),self.personal.active,reason)
            self.history_error=None
            return True
        except Exception as exc:
            self.history_error=type(exc).__name__
            self.status.set('草稿历史暂不可写；当前内容保留，未清空或替换。')
            return False

    def _save_draft(self,wait=False,only_if_changed=False):
        if hasattr(self,'project_page'):
            try:self.project_page.save_draft()
            except (OSError,ValueError,sqlite3.Error):
                self.project_page.notice.set('项目回复暂未存盘；文字仍在当前回复框。')
        if self.drafts is None:
            return True
        try:
            writer=getattr(self,'draft_writer',None)
            previous_write_fault = bool(writer is not None and (writer.error or self.draft_fault))
            refs=tuple(self.refs)
            changed=(not only_if_changed or getattr(self,'draft_dirty',True)
                     or refs!=getattr(self,'draft_refs',()) or writer and (writer.error or writer.document_error))
            if changed:
                data=self.draft_data()
                if writer is not None:
                    writer.submit(data,self.personal.active)
                else:self.drafts.save(data)
                self.draft_dirty=False
                self.draft_refs=refs
            if writer is not None and wait and not writer.flush():
                raise OSError('草稿尚未完成存盘')
            if writer is not None and writer.error:
                raise OSError(writer.error)
            # submit() clears the writer's error while retrying. Keep the UI
            # fault until that retry actually reaches disk, instead of showing
            # success for another outstanding background write.
            if previous_write_fault and writer.saved < writer.requested:
                raise OSError('草稿重试尚未完成存盘')
            if writer is not None and writer.document_error:
                self.status.set('恢复草稿已保存，但留存文档暂未保存；文字保留，稍后重试。')
            elif self.draft_fault:
                self.status.set('草稿已恢复保存。')
            self.draft_fault=False
            return True
        except (OSError,ValueError,tk.TclError):
            if not self.draft_fault:
                self.status.set('草稿暂未存盘，文字仍在当前窗口')
            self.draft_fault=True
            return False

    def _autosave_draft(self):
        if self.closing:
            return
        self._save_draft(only_if_changed=True)
        self.root.after(2000,self._autosave_draft)

    def _input_epoch(self):
        return (self.hooks.input_epoch if self.hooks else 0, self.local_input_epoch)

    def _local_edit(self, event):
        if not self.hooks or not self.hooks.enabled:
            self.local_input_epoch += 1
            self._edit()

    def _forget_owned(self):
        if hasattr(self,'pronunciation'):
            self.pronunciation.discard()
        if hasattr(self,'sender'):
            self.sender.cancel('输入内容或位置已变化，已取消发送。')
        self.owned_text = ""
        self.owned_epoch = None
        self.cleanup_waiting = None
        self.cleanup_boundary = False
        self.cleanup_serial += 1
        self.acoustic_edits.clear()
        self.light_jobs.clear()
        if self.cleaner:
            self.cleaner.cancel()

    def _light_worker(self):
        if self.cleaner is None:
            from light_correction import LightWorker
            if self.config.get('ai_assist'):
                if self.config.get('ai_provider','codex')=='glm':
                    from cloud_proofreader import GLMProofreader
                    self.cleaner = LightWorker(self.events, lambda:GLMProofreader(ROOT))
                else:
                    from ai_proofreader import CodexProofreader
                    self.cleaner = LightWorker(self.events, CodexProofreader)
            else:
                self.cleaner = LightWorker(self.events)
        return self.cleaner

    def _sync_recognition_context(self, previous=None):
        from acoustic_context import context_snapshot
        if not hasattr(self,'engine'):
            return
        if previous is None:
            previous = self.owned_text[-160:]
            if self.internal:
                # The user may dictate in the middle of an existing draft.
                # Context follows the insertion point, never the document tail.
                widget = self.active_text()
                previous = widget.get('insert-160c', 'insert')
        context=context_snapshot(self.personal,previous,enabled=self.config.get('correction_mode')=='light')
        context['profile']=self.personal.active
        context['voice_samples']=()
        context['word_samples']=()
        if (self.config.get('audio_learning',False) and self.personal.learning
                and self.config.get('auto_correct',True) and self.config.get('correction_mode')=='light'):
            try:
                from personal_word_memory import samples_from_memory as word_samples
                context['word_samples']=word_samples(self.pronunciation,self.personal.active)
            except Exception:
                self.correction_hint.set('个人发音参考暂不可用，听写继续')
        if (self.config.get('audio_learning',False) and self.personal.learning
                and self.config.get('noise_filter',True) and self.config.get('correction_mode')=='light'):
            try:
                from voice_identity import samples_from_memory
                context['voice_samples']=samples_from_memory(self.pronunciation,self.personal.active)
            except Exception:
                self.correction_hint.set('个人声音参考暂不可用，听写继续')
        self.engine.command('context', context=context,
            retain_audio=self.config.get('audio_learning',False) and self.personal.learning)

    def _begin_owned(self, widget=None):
        epoch = self._input_epoch()
        if self.owned_epoch is not None and self.owned_epoch != epoch:
            boundary = self.cleanup_boundary
            self._forget_owned()
            self.cleanup_boundary = boundary
        if not self.owned_text and widget is not None:
            widget.mark_set("voice_owned_start", "insert")
            widget.mark_gravity("voice_owned_start", "left")
        self.owned_epoch = epoch
        return epoch

    def _append_owned(self, text, epoch, widget=None):
        if epoch != self._input_epoch():
            self._forget_owned()
            return
        previous = self.owned_text
        self.owned_text += text
        if not self.internal:
            self.write_settle_until = time.monotonic()+.10
        if widget is not None:
            widget.mark_set("voice_owned_end", "insert")
            widget.mark_gravity("voice_owned_end", "right")
        self.learning.written(previous)

    def _request_cleanup(self):
        self.cleanup_boundary = False
        if not self.config.get("auto_correct", True) or len(self.owned_text) < 2:
            return
        if self.owned_epoch != self._input_epoch():
            self._forget_owned()
            return
        self.cleanup_serial += 1
        token = (self.state.generation, self.cleanup_serial, self.owned_epoch)
        self.cleanup_waiting = token
        source = self.owned_text
        # No language model can invent a personal preference. Only local rules
        # approved by human edits (or explicitly entered) may change words.
        try:
            result = self.personal.apply(source)
            self.events.put(("cleanup", {"token":token,"source":source,"text":result.text,"rule_ids":result.rule_ids}))
        except Exception as exc:
            self.cleanup_waiting = None
            self.correction_hint.set("个人词库暂不可用，已保留原话")

    def _cleanup_result(self, result):
        if result["token"] != self.cleanup_waiting:
            return
        self.cleanup_waiting = None
        source, corrected = result["source"], result["text"]
        if (not self.config.get("auto_correct", True) or result["token"][0] != self.state.generation
                or source != self.owned_text or self.pending or self.owned_epoch != self._input_epoch()):
            self.correction_hint.set("已保留你当前的文字；过期的自动修正不会覆盖修改。")
            return
        changed = corrected != source
        try:
            if changed and not self._replace_owned(source, corrected):
                self.correction_hint.set("原输入位置已变化，已保留原文，没有自动回改。")
                self._forget_owned()
                return
            self.last_cleanup_original = source
            self.owned_text = corrected
            if changed:
                self.personal.record_applied(result.get('rule_ids',()),
                    (self.learning.session,'cleanup',result['token']))
            self.correction_hint.set("已按你的词库修正" if changed else "已保留原话 · 只用你确认过的改法")
            if result.get("error"):
                self.correction_hint.set("本次仅做基础修正；实时听写继续正常工作。")
        except (OSError, tk.TclError):
            self._forget_owned()
            self.correction_hint.set("本次自动回改未完成，已停止继续改写。")

    def _replace_owned(self, source, corrected):
        if source != self.owned_text or self.owned_epoch != self._input_epoch():
            return False
        if source == corrected:
            return True
        if self.internal:
            widget = self.active_text()
            if widget.get("voice_owned_start", "voice_owned_end") != source:
                return False
            widget.edit_separator()
            widget.delete("voice_owned_start", "voice_owned_end")
            widget.insert("voice_owned_start", corrected)
            widget.mark_set("insert", "voice_owned_end")
            widget.edit_separator()
        else:
            if (window_process(self.target) != self.target_process
                    or not replace_owned_tail(source, corrected, self.target, self.target_control)):
                return False
            mirror = self.dict_text.get("1.0", "end-1c")
            if mirror.endswith(source):
                self.dict_text.delete(f"end-1c-{len(source)}c", "end-1c")
                self.dict_text.insert("end-1c", corrected)
        self.pronunciation.patch(source,corrected)
        self.owned_text = corrected
        self._sync_recognition_context()
        if not self.internal:
            self.write_settle_until = time.monotonic()+.10
        self.learning.written(source)
        return True

    def _stage_edit_audio(self, utterance):
        if (not self.config.get('audio_learning',False) or not self.personal.learning
                or utterance.get('_audio_staged')):
            return
        source = utterance.get('emitted','')
        from learning_status import record
        if not source or (self.owned_epoch is not None and self.owned_epoch != self._input_epoch()):
            record(self.personal,'audio_range')
            return
        draft = self.owned_text + "".join(self.pending)
        if not draft.endswith(source):
            record(self.personal,'audio_range')
            return
        try:
            staged=self.pronunciation.stage(self.personal.active,len(draft)-len(source),source,
                utterance.get('online',source),utterance.get('_audio'),utterance.get('_sample_rate',16000))
            record(self.personal,'audio_staged' if staged else ('audio_missing' if utterance.get('_audio') is None else 'audio_rejected'))
            self.learning.audio_staged(draft)
        except Exception:
            self.correction_hint.set('这段发音样本未能暂存，听写继续')

    def _queue_acoustic(self, utterance):
        personal_word=utterance.get('personal_word')
        if (not isinstance(personal_word,dict) or personal_word.get('profile')!=self.personal.active
                or not self.config.get('audio_learning',False) or not self.personal.learning):
            personal_word=None
        source = utterance["emitted"]
        online = utterance.get("online",source)
        result = self.personal.acoustic(online,utterance["text"],self.config.get("auto_correct",True))
        corrected = result.text
        if not source:
            return
        epoch = self._input_epoch()
        if self.owned_epoch is not None and self.owned_epoch != epoch:
            return
        draft = self.owned_text + "".join(self.pending)
        if not draft.endswith(source):
            return
        self._stage_edit_audio(utterance)
        protected = [p['text'] for p in self.personal.phrases() if p['protected']]
        protected.extend(r['target'] for r in self.personal.rules() if r['status'] != 'disabled')
        light_mode = self.config.get('auto_correct',True) and self.config.get('correction_mode') == 'light'
        if light_mode and not result.rule_ids:
            from dictation_policy import final_base
            base=final_base(online,utterance,protected)
            if base:
                result=self.personal.apply(base)
                corrected=result.text
        expanded = self.skills.expand(online,self.config.get('skill_syntax','dollar')) if self.config.get('skill_commands',True) else None
        if expanded is not None:
            corrected = expanded
        elif light_mode and self.config.get('clean_fillers',True):
            from speech_text import trim_fillers
            corrected = trim_fillers(corrected,protected)
        light = (self.config.get('auto_correct',True) and self.config.get('correction_mode') == 'light'
                 and not result.rule_ids and expanded is None and len(corrected) <= 256
                 and (len(corrected)>=4 or (len(corrected)>=1 and (bool(personal_word) or bool(utterance.get('short_english'))
                      or any(c.isascii() and c.isalpha() for c in utterance['text'])))))
        self.acoustic_edits.append(dict(start=len(draft)-len(source),before=source,after=corrected,
            rule_ids=result.rule_ids if expanded is None else (),
            epoch=epoch,generation=self.state.generation,light=light,acoustic=utterance['text'],protected=protected,
            pause_before=utterance.get('pause_before',0),support=utterance.get('support'),alternatives=utterance.get('alternatives'),
            english_reading=utterance.get('english_reading'),personal_word=personal_word,
            short_english=utterance.get('short_english'),
            unprompted_readings=utterance.get('unprompted_readings'),
            word_revision=getattr(getattr(self,'pronunciation',None),'revision',0)))

    def _patch_span(self, start, before, after):
        source = self.owned_text
        if source[start:start+len(before)] != before:
            return False
        if not self._replace_owned(source,source[:start]+after+source[start+len(before):]):
            return False
        shift = len(after)-len(before)
        for job in self.light_jobs.values():
            if job['start'] >= start+len(before):
                job['start'] += shift
        return True

    def _apply_acoustic(self):
        if self.pending or not self.acoustic_edits:
            return
        # 解码积压未追平时暂缓昂贵的额外校对（LightWorker/AI 请求）：
        # 在线逐字文字已保留，追平后本方法自动恢复提交。
        if getattr(self.engine, 'backlog_seconds', lambda: 0.0)() > 2.0:
            return
        edits, self.acoustic_edits = self.acoustic_edits, []
        shift = 0
        for edit in edits:
            if edit['epoch'] != self._input_epoch() or edit['generation'] != self.state.generation:
                continue
            start = edit['start']+shift
            try:
                if not self._patch_span(start,edit['before'],edit['after']):
                    self._forget_owned()
                    return
                shift += len(edit['after'])-len(edit['before'])
                if edit['before']!=edit['after']:
                    self.personal.record_applied(edit.get('rule_ids',()),
                        (self.learning.session,'acoustic',edit['generation'],edit['epoch'],edit['start']))
                if edit['light']:
                    personal_word=edit.get('personal_word')
                    if (not self.config.get('audio_learning',False) or not self.personal.learning
                            or edit.get('word_revision')!=getattr(getattr(self,'pronunciation',None),'revision',0)):
                        personal_word=None
                    self.cleanup_serial += 1
                    token = (self.state.generation,self.cleanup_serial,edit['epoch'])
                    options=dict(context=self.owned_text[max(0,start-160):start], acoustic_support=True,
                                 punctuation=self.config.get('punctuation',True),fillers=self.config.get('clean_fillers',True),support=edit.get('support'),
                                 pause_before=edit.get('pause_before',0),alternatives=edit.get('alternatives'),
                                 english_reading=edit.get('english_reading'),personal_word=personal_word,
                                 short_english=edit.get('short_english'),
                                 unprompted_readings=edit.get('unprompted_readings'),deadline=time.monotonic()+16)
                    if self._light_worker().submit(token,edit['after'],edit['acoustic'],edit['protected'],options):
                        self.light_jobs[token] = dict(start=start,before=edit['after'],epoch=edit['epoch'],
                            generation=edit['generation'],deadline=options['deadline'],pause_before=edit.get('pause_before',0),
                            profile=self.personal.active,context=options['context'],personal_word_requested=bool(personal_word),
                            word_revision=edit.get('word_revision'))
                elif edit['before'] != edit['after']:
                    self.correction_hint.set('已按原音和个人用语核对')
            except (OSError, tk.TclError):
                self._forget_owned()
                self.correction_hint.set('已保留当前文字')
                return
        self.cleanup_waiting = next(iter(self.light_jobs),None)

    def _apply_light(self):
        if self.pending or self.acoustic_edits:
            return
        if not self.internal and time.monotonic()<self.write_settle_until:
            return
        while self.light_jobs:
            token = next(iter(self.light_jobs))
            job = self.light_jobs[token]
            if time.monotonic() >= job['deadline']:
                if job.get('phase')=='committing':
                    self._forget_owned()
                    self.correction_hint.set('未能确认回写结果，已取消发送')
                    return
                self.light_jobs.pop(token)
                self.correction_hint.set('本次校对超时，保留原话')
                continue
            if 'result' not in job:
                break
            if job.get('phase')=='committing':
                if not job.get('probe') and time.monotonic()>=job.get('next_probe',0):
                    job['probe']=('light_commit',token)
                    job['snapshot_expected']=job['field_prefix']+self.owned_text
                    self.sender.probe.request(job['probe'],self.target,self.target_control,self.target_process,include_text=True)
                break
            self.light_jobs.pop(token)
            if (job['epoch'] != self._input_epoch() or job['generation'] != self.state.generation
                    or not self.config.get('auto_correct',True) or self.config.get('correction_mode') != 'light'):
                continue
            if job.get('personal_word_requested') and (not self.config.get('audio_learning',False)
                    or not self.personal.learning or job.get('profile')!=self.personal.active
                    or job.get('word_revision')!=getattr(getattr(self,'pronunciation',None),'revision',0)):
                continue
            result = job.get('result')
            if result is None:
                self.correction_hint.set('本次校对超时，保留原话')
                continue
            if not job.get('layout_done'):
                if self.config.get('skill_commands',True):
                    expanded=self.skills.expand_corrected(job['before'],result['text'],self.config.get('skill_syntax','dollar'))
                    if expanded is not None:
                        result['text']=expanded
                        result['boundary']=None
                from speech_text import paragraph_prefix, boundary_patch
                cut, laid_out = boundary_patch(self.owned_text[:job['start']], result['text'], result.get('boundary'))
                if cut or laid_out != result['text']:
                    job['before'] = self.owned_text[job['start']-cut:job['start']] + job['before']
                    job['start'] -= cut
                    result['text'] = laid_out
                prefix=paragraph_prefix(result['text'],self.owned_text[:job['start']],job.get('pause_before',0),self.config.get('paragraphs',True))
                result['text']=prefix+result['text']
                job['layout_done']=True
            if not self.internal and job.get('verified') and job['snapshot_owned'] != self.owned_text:
                job['verified']=False
                job['probe']=None
            if not self.internal and not job.get('verified'):
                # A webpage may change its field without generating keyboard
                # hooks. Read the exact owned tail before any delayed rewrite.
                self.light_jobs = {token:job,**self.light_jobs}
                if not job.get('probe'):
                    if time.monotonic()<job.get('next_probe',0):
                        break
                    job['probe'] = ('light',token)
                    job['snapshot_owned'] = self.owned_text
                    self.sender.probe.request(job['probe'],self.target,self.target_control,self.target_process,include_text=True)
                break
            try:
                if not self._patch_span(job['start'],job['before'],result['text']):
                    self._forget_owned()
                    return
                changed = result['text'] != job['before']
                self.correction_hint.set('已轻度纠错 · 可查看原文' if changed else '已核对 · 保留原话')
                if result.get('backend') in ('codex','glm'):
                    self.correction_hint.set('已用 AI 辅助核对 · 你的手动改法单独学习')
                if self.internal and job.get('sample_id'):
                    if not self.proofreading.applied(job['sample_id']):
                        self.correction_hint.set('文字已处理，校对记录暂未保存')
                if result.get('error'):
                    self.correction_hint.set('本次云端校对未完成，保留本地结果；可结束后手动校对' if self.config.get('ai_assist') else '本次自动校对未完成，保留本地结果')
                if changed and not self.internal:
                    # SendInput delivery is asynchronous. Keep the send gate
                    # closed until the field contains the completed rewrite.
                    job['phase']='committing'
                    job['probe']=None
                    job['next_probe']=time.monotonic()+.04
                    job['deadline']=min(job['deadline'],time.monotonic()+1.2)
                    self.light_jobs={token:job,**self.light_jobs}
                    break
            except (OSError,tk.TclError):
                self._forget_owned()
                return
        self.cleanup_waiting = next(iter(self.light_jobs),None)

    def _light_snapshot(self, token, snapshot):
        job=self.light_jobs.get(token[1])
        if not job or job.get('probe')!=token:return
        if (job['epoch']!=self._input_epoch() or job['generation']!=self.state.generation
                or not snapshot or snapshot.text is None or time.monotonic()-snapshot.captured_at>.4):
            self._forget_owned()
            return
        if token[0]=='light_commit':
            job['probe']=None
            if (job['snapshot_expected'] != job['field_prefix']+self.owned_text
                    or self.pending or snapshot.text != job['snapshot_expected']):
                job['next_probe']=time.monotonic()+.08
                return
            self.light_jobs.pop(token[1])
            if job.get('sample_id'):
                if not self.proofreading.applied(job['sample_id']):
                    self.correction_hint.set('文字已处理，校对记录暂未保存')
            self.cleanup_waiting=next(iter(self.light_jobs),None)
            return
        if job['snapshot_owned'] != self.owned_text or self.pending:
            job['probe']=None
            return
        if not snapshot.text.endswith(self.owned_text):
            # Accessibility providers can return the state from just before our
            # preceding SendInput batch. Retry reads only; never write unless an
            # exact current tail is observed. Real edits still invalidate epoch.
            until=job.setdefault('read_retry_until',time.monotonic()+.8)
            if time.monotonic()<until:
                job['probe']=None
                job['next_probe']=time.monotonic()+.08
                return
            self._forget_owned()
            self.correction_hint.set('输入框内容已变化，保留当前文字')
            return
        job['verified']=True
        job['field_prefix']=snapshot.text[:-len(self.owned_text)] if self.owned_text else snapshot.text

    def _build_hud(self):
        from display import px, scale_layout
        self.hud = tk.Toplevel(self.root)
        self.hud.withdraw()
        self.hud.overrideredirect(True)
        self.hud.attributes("-topmost", True)
        self.hud.configure(bg="#182C30")
        self.hud_text = tk.StringVar(value="正在听写")
        status_label = tk.Label(self.hud, textvariable=self.hud_text, bg="#182C30", fg="white",
                 font=("Microsoft YaHei UI", 11), wraplength=265, justify="left",
                 padx=15, pady=6)
        status_label.pack(fill="x")
        hint_label = tk.Label(self.hud, textvariable=self.key_hint, bg="#182C30", fg="#9AD7C7",
                 font=("Microsoft YaHei UI", 9), wraplength=265, justify="left",
                 padx=15, pady=3)
        hint_label.pack(fill="x")
        tk.Button(self.hud, text="结束会话", command=self.finish, bd=0, bg="#2A4548", fg="white",
                  activebackground="#315052", activeforeground="white", font=("Microsoft YaHei UI", 10)).pack(fill="x")
        scale_layout(self.hud)
        self.hud.minsize(px(300), 0)
        self.hud.maxsize(px(300), self.root.winfo_screenheight())
        from floating_hud import FloatingHud
        self.hud_mover = FloatingHud(self.hud, (status_label, hint_label),
                                    self.config.get('hud_position'), self._save_hud_position,
                                    width=px(300), margin=px(30))
        make_nonactivating(self.hud)

    def _save_hud_position(self, position):
        self.config['hud_position'] = position
        try:
            write_settings(ROOT / 'settings.json', self.config)
        except OSError:
            self.status.set('浮窗位置已调整，本次未能保存')

    def _update_hint(self):
        from hotkeys import display_binding
        if hasattr(self,'guide_keys'):
            self.guide_keys.set('听写键：'+display_binding(self.config['dictation_key'])+'  工作流键：'+display_binding(self.config['workflow_key']))
        hint = ""
        if self.state.mode != "idle" and not self.stopping:
            setting = "workflow_key" if self.state.mode == "workflow" else "dictation_key"
            action = "继续" if self.state.paused else "暂停" if self.config["activation"] == "continuous" else "结束"
            hint = f"再按一次 {display_binding(self.config[setting])} {action}"
        if self.key_hint.get() != hint:
            self.key_hint.set(hint)
        if hint:
            self.key_hint_label.pack(fill="x", pady=(5, 0))
        else:
            self.key_hint_label.pack_forget()

    def _refresh_key_buttons(self):
        from hotkeys import display_binding
        for i,value in enumerate((self.key1.get(),self.key2.get())):
            self.key_labels[i].set("请按下新按键…" if self.capture_index==i else display_binding(value)+"    点击更改")

    def cancel_key_capture(self,message="已取消，原来的按键保持不变。"):
        if self.hooks:self.hooks.cancel_capture()
        self.capture_index=None
        self.capture_serial+=1
        self.capture_cancel.state(["disabled"])
        self._refresh_key_buttons()
        self.capture_hint.set(message)

    def begin_key_capture(self,index):
        if self.state.mode!="idle" or self.stopping:
            self.capture_hint.set("结束当前录音后，就可以更改按键。")
            return
        if not self.hooks or self.hook_error:
            self.capture_hint.set("按键监听未启动，暂时不能录入快捷键。")
            return
        if modifiers_down():
            self.capture_hint.set("请先松开正在按的键，再点击更改。")
            return
        if self.capture_index is not None:self.cancel_key_capture("")
        self.capture_index=index;self.capture_serial+=1
        token=self.capture_serial
        self.capture_cancel.state(["!disabled"])
        self._refresh_key_buttons()
        self.capture_hint.set("按下想用的键盘键、组合键或鼠标键，松开后自动保存。")
        self.root.update_idletasks()
        button=self.capture_cancel
        x,y=button.winfo_rootx(),button.winfo_rooty()
        self.hooks.begin_capture(top_handle(self.root),token,(x,y,x+button.winfo_width(),y+button.winfo_height()))
        def timeout():
            if self.capture_index is not None and self.capture_serial==token and not self.closing:
                self.cancel_key_capture("本次未录入按键，原设置已保留；点击即可重新录入。")
        self.root.after(20000,timeout)

    def accept_key_capture(self,token,value,error):
        if token!=self.capture_serial or self.capture_index is None:return
        index=self.capture_index
        try:
            if error:raise ValueError(error)
            pair=[self.key1.get(),self.key2.get()];pair[index]=value
            first,second=validate_bindings(*pair)
            config=dict(self.config,dictation_key=first,workflow_key=second)
            write_settings(ROOT/"settings.json",config)
        except (ValueError,OSError) as exc:
            self.cancel_key_capture(str(exc)+" 原按键没有改变。")
            return
        self.config=config
        self.key1.set(first);self.key2.set(second)
        self.hooks.configure((first,second))
        from hotkeys import display_binding
        name="听写键" if index==0 else "工作流键"
        notice=f"{name}已改为 {display_binding(pair[index])}，已自动保存。"
        if pair[index]=="mouse_left":notice+=" 普通左键点击也会触发；本程序按钮仍可正常点击。"
        self.cancel_key_capture(notice)
        self._update_hint()
        self.status.set("快捷键已生效并保存")

    def enable_hooks(self):
        if self.state.mode != "idle":
            self.finish()
        if self.hooks:
            self.hooks.enabled = self.enabled.get()
        self.config["enabled"] = self.enabled.get()
        try:
            write_settings(ROOT / "settings.json", self.config)
        except OSError as exc:
            self.status.set("快捷键开关已生效，但未能保存：" + str(exc))

    def save_settings(self):
        if self.capture_index is not None:
            self.capture_hint.set("请先完成按键录入，或点击取消。")
            return
        if self.state.mode != "idle" or self.stopping:
            messagebox.showinfo("请先结束会话", "结束本次会话后可以修改按键和麦克风。")
            return
        try:
            was_enhanced = self.config['correction_mode'] == 'light'
            was_ai = (self.config.get('ai_assist',False),self.config.get('ai_provider','codex'))
            first, second = validate_bindings(self.key1.get(), self.key2.get())
            self.config.update(dictation_key=first, workflow_key=second,
                               activation="continuous" if self.activation.get().startswith("持续") else "toggle",
                               device=self.devices[self.device.get()], resume_at_end=self.resume_at_end.get(),
                               auto_correct=self.correction_mode.get() != '原样输出',
                               correction_mode={'轻度纠错':'light','只用个人词库':'personal','原样输出':'raw'}[self.correction_mode.get()],
                               ai_assist=self.ai_assist.get(),
                               ai_provider='glm' if self.ai_provider.get().startswith('GLM') else 'codex',
                               voice_send=self.voice_send.get(), send_key="ctrl+enter" if self.send_key.get().startswith("Ctrl") else "enter",
                               device_signature=self.device_signatures[self.device.get()],
                               noise_filter=self.noise_filter.get(),clean_fillers=self.clean_fillers.get(),
                               punctuation=self.punctuation.get(),paragraphs=self.paragraphs.get(),
                               skill_commands=self.skill_commands.get(),
                               skill_syntax={'$技能名（Codex）':'dollar','/技能名':'slash','自然语言':'natural'}[self.skill_syntax.get()],
                               enabled=self.enabled.get(), workflow_action={"填回原输入框（不发送）":"paste_origin",
                                   "复制提示词":"copy", "仅整理任务":"prepare"}[self.workflow_action.get()])
            write_settings(ROOT / "settings.json", self.config)
            if self.hooks:
                self.hooks.configure((first, second))
            self.auto_correct.set(self.config['auto_correct'])
            self._forget_owned()
            if was_ai != (self.config.get('ai_assist'),self.config.get('ai_provider')) and self.cleaner:
                self.cleaner.close()
                self.cleaner = None
            if self.config['correction_mode'] != 'light':
                if self.cleaner:
                    self.cleaner.close()
                    self.cleaner = None
            else:
                self._light_worker()
            self.correction_hint.set({'light':'轻度纠错 · 保留原意','personal':'只用你确认过的改法','raw':'保留原始转写'}[self.config['correction_mode']])
            if self.config.get('ai_assist') and self.config['correction_mode']=='light':
                self.correction_hint.set('AI 辅助校对 · 仅发送当前转写与短上下文')
            self.processing_location.set('本地识别 · AI 校对' if self.config.get('ai_assist') and self.config['correction_mode']=='light' else '●  全程本地')
            self._update_hint()
            self.status.set("设置已保存")
            if was_enhanced != (self.config['correction_mode'] == 'light') or self.mode_switch_pending:
                self.ready = False
                self.mode_switch_pending = True
                self.mode_switch_serial += 1
                self.mode_switch_request = self.mode_switch_serial
                self.engine.command('refinement',enhanced=self.config['correction_mode'] == 'light',
                                    request_id=self.mode_switch_request)
                self.status.set('正在切换本地识别方式…')
        except (ValueError, OSError) as exc:
            messagebox.showerror("设置未保存", str(exc))

    def trigger(self, index, internal=False):
        if self.close_boundary:
            self.status.set('草稿尚未存盘，请先重试保存或关闭窗口；识别暂不重启')
            return
        if self.capture_index is not None:return
        self.sender.cancel()
        if not self.ready:
            self.status.set("模型还在加载，请稍候")
            return
        if self.stopping:
            return
        requested = "dictation" if index == 0 else "workflow"
        if index == 0 and (self.state.mode == 'project' or
                (self.tabs.select() == self.project_tab and (internal or foreground() == top_handle(self.root)))):
            requested = 'project'
        action = self.state.press(requested, self.config["activation"] == "continuous")
        if action == "busy":
            self.status.set("另一种录音正在进行，请先结束当前会话")
            return
        if action == "stop":
            self.stopping = True
            self._record_stop('button_stop' if internal else 'hotkey_stop')
            self.status.set("正在收好最后一句…")
            self.engine.command("stop")
            self._runtime_status('finishing')
            return
        if action == "pause":
            self.stopping = True
            self._record_stop('pause')
            self.engine.command("pause")
            self._runtime_status('pausing')
            self.status.set("正在暂停…")
            return
        self.recording_mode = requested
        if action == "resume":
            self._forget_owned()
            self.sender.reset_segment()
        if action == "start":
            self.stop_reason = None
            self.workflow_voice.reset()
            self.workflow_voice_action = None
            self.project_voice.reset()
            self.project_voice_action = None
            self.project_on_drain = False
            self.learning.reset()
            self._forget_owned()
            self.raw_transcript = ""
            self.raw_mode = requested
            self.session_failed = False
            self.internal = internal or requested in ("workflow", "project") or foreground() == top_handle(self.root)
            self.target = foreground()
            self.target_control = focused_control(self.target)
            self.target_process = window_process(self.target)
            if requested == "workflow":
                if self.workflow_completed:
                    self.flow_text.delete("1.0", "end")
                self.workflow_completed = False
                self.origin_window = self.target
                self.origin_process = window_process(self.target)
                self.origin_control = focused_control(self.target)
                self.workflow_plan = None
                self.bundle_signature = None
                self.last_bundle = None
            self.pending.clear()
            self.edit_until = 0
            self.last_edit = 0
            self.resume_position_pending = False
            self.route_on_drain = False
            self.focus_blocked = False
            if self.internal:
                self.root.deiconify()
                self.tabs.select(self.flow_tab if requested == "workflow" else self.project_tab if requested == 'project' else self.dict_tab)
                self.root.lift()
                self.root.focus_force()
                self.active_text().focus_set()
                self.root.update_idletasks()
                self.target = top_handle(self.root)
                self.target_control = focused_control(self.target)
                self.target_process = window_process(self.target)
            elif requested == "dictation":
                # Each external session has its own shadow. Save the previous
                # document first; never clear or alter the external target.
                if self.dict_text.get('1.0','end-1c') and self.archive_draft('before_external_session'):
                    self.dict_text.delete('1.0','end')
                    self.dict_text.edit_reset()
                self.text_hint.set("这里只显示本次外部输入的副本；之前的内容在草稿历史和自动留存中。")
            self.sender.start()
        self.status.set("正在启动麦克风…")
        self.hud_mover.place()
        self.hud.deiconify()
        self._sync_recognition_context()
        self.engine.command("resume" if action == "resume" else "start",
                            generation=self.state.generation, device=self.config["device"],noise_filter=self.config.get('noise_filter',True))

    def active_text(self):
        if self.recording_mode == 'project':return self.project_page.reply_text
        return self.flow_text if self.recording_mode == "workflow" else self.dict_text

    def _record_stop(self, reason):
        # 记录真实结束原因：诊断文件只写元数据；原因在 UI 持续可见。
        if getattr(self, 'stop_reason', None) == reason:
            return
        self.stop_reason = reason
        try:
            self.session_log.record(reason, getattr(self.engine, 'backlog_seconds', lambda: 0.0)())
        except Exception:
            pass

    def finish(self, voice_send=False, reason=None):
        if not voice_send:self.sender.cancel()
        if self.state.mode == "idle" or self.stopping:
            return
        self._record_stop(reason if reason is not None
                          else ('voice_send' if voice_send else 'button_stop'))
        self.state.finish()
        self.stopping = True
        if not self.internal and foreground() == top_handle(self.root):
            restore_input_focus(self.target, self.target_control)
        self.engine.command("stop")
        self._runtime_status('finishing')
        self.status.set("正在收好最后一句…")

    def _edit(self, mouse_point=None):
        if self.recording_mode == 'project':
            self.project_voice.reset()
            self.project_voice_action = None
        if self.recording_mode == 'workflow':
            self.workflow_voice.reset()
            if self.workflow_voice_action:
                self.workflow_voice_action = 'prepare'
        self.learning.manual_edit()
        self.sender.cancel("已取消发送：检测到手动操作，文字保留。")
        self.sender.reset_segment()
        if self.owned_epoch is not None and self.owned_epoch != self._input_epoch():
            self._forget_owned()
        if self.state.mode == "idle" or self.stopping:
            return
        if foreground() != self.target:
            return
        if self.internal and mouse_point is None and self.root.focus_get() is not self.active_text():
            return
        if self.state.paused:
            self.resume_position_pending = bool(self.config["resume_at_end"])
            return
        if mouse_point is not None:
            x, y = mouse_point
            def inside(widget):
                return (widget.winfo_ismapped() and widget.winfo_rootx() <= x < widget.winfo_rootx() + widget.winfo_width()
                        and widget.winfo_rooty() <= y < widget.winfo_rooty() + widget.winfo_height())
            # Clicking Stop is not an edit. Preserve pending tail audio until it is drained.
            if inside(self.hud) or (inside(self.root) and (not self.internal or not inside(self.active_text()))):
                return
        now = time.monotonic()
        self._forget_owned()
        new_edit_block = now - self.last_edit > 0.7
        self.last_edit = now
        self.edit_until = now + 0.35
        self.resume_position_pending = bool(self.config["resume_at_end"])
        if new_edit_block:
            self.pending.clear()
            self.state.generation += 1
            self.engine.command("cut", generation=self.state.generation, delay=0.0, at=now)
        self.status.set("正在改字")

    def handle_engine(self, generation, kind, value):
        if generation != self.state.generation:
            return
        if self.session_failed and kind != "error":
            self._retain_late(generation, kind, value)
            return
        if kind == "delta":
            self.raw_transcript = (self.raw_transcript + value)[-64000:]
            if self.recording_mode == 'workflow':
                if self.workflow_voice_action:self.workflow_voice_action='prepare'
                value=self.workflow_voice.feed(value)
            elif self.recording_mode == 'project':
                self.project_voice_action=None
                value=self.project_voice.feed(value)
            else:value=self.sender.delta(value)
            if not value:return
            if not self.internal:
                self.dict_text.insert("end", value)
                self.dict_text.see("end")
            self.pending.append(value)
        elif kind == 'audio_segment':
            self._stage_edit_audio(value)
        elif kind == 'backlog':
            self.correction_hint.set('录音继续 · 正在追赶积压，暂缓精细复核，保留实时原话')
        elif kind == "listening":
            self._runtime_status('listening')
            self.status.set("正在录入工作流" if self.recording_mode == "workflow" else "正在语音回复" if self.recording_mode=='project' else "正在听写")
        elif kind in ("sentence", "utterance"):
            self._runtime_status('listening' if self.engine.recording is not None else 'finishing')
            utterance = value if kind == "utterance" else None
            from voice_send import speaker_command_allowed
            speaker_options=({'speaker_allowed':False} if utterance and not speaker_command_allowed(utterance) else {})
            raw=utterance.get('command_online',utterance['online']) if utterance else value
            verified=(utterance['text'] if utterance['refined'] else '') if utterance else None
            if self.recording_mode == 'workflow':
                text,action=self.workflow_voice.finish(raw,verified=verified,**speaker_options)
                command=bool(action)
                if action:
                    if action == 'video_start' or self.flow_text.get('1.0','end-1c').strip() or ''.join(self.pending).strip():
                        self.workflow_voice_action=action
                        self.finish(reason='workflow_command')
                    else:
                        text=raw;command=False
                        self.status.set('先说具体任务，再单独说操作口令')
            elif self.recording_mode == 'project':
                text,action=self.project_voice.finish(raw,verified=verified,**speaker_options)
                command=bool(action)
                if action:
                    self.project_voice_action=action
                    self.finish(reason='workflow_command')
            else:
                text,command=self.sender.sentence(raw,verified=verified,**speaker_options)
            if text:
                if not self.internal:self.dict_text.insert("end",text)
                self.pending.append(text)
            if not command:
                self._queue_acoustic(utterance or {"online":value,"emitted":value,"text":value,"refined":False})
            if not command:
                try:
                    self.learning.observe_usage(utterance["online"] if utterance else value,generation)
                except Exception:
                    self.correction_hint.set("本次用语未能保存；听写继续")
            # Each utterance gets one vocabulary pass. Reprocessing the whole
            # session would cascade A -> B -> C rules across later pauses.
            if not command:self.cleanup_boundary = False
        elif kind == "partial":
            self.sender.partial(value)
        elif kind == "level":
            self.meter["value"] = min(100, value * 800)
            if value >= .012:self.sender.activity()
        elif kind == "revision":
            self.status.set("有一句识别发生回改；已输入文字保持不动，请核对：" + value[:45])
        elif kind in ("stopped", "paused"):
            self._runtime_status('paused' if kind=='paused' else 'ready')
            self.stopping = False
            self.meter["value"] = 0
            if kind == "paused":
                self.status.set("会话已暂停")
            else:
                self.state.finish()
                self.hud.withdraw()
                # 真实结束原因持续可见：不被泛泛的“已结束”覆盖。
                reason_text = self.session_log.describe(self.stop_reason) if self.stop_reason else ""
                self.status.set("本次听写已结束" + ("（原因：" + reason_text + "）" if reason_text else ""))
                self.archive_pending = True
                if self.recording_mode == "workflow":
                    self.route_on_drain = True
                elif self.recording_mode == 'project' and self.project_voice_action:
                    self.project_on_drain=True
        elif kind == "error":
            self._record_stop('device_error')
            self.sender.cancel()
            self.stopping = False
            self.state.finish()
            self._retain_on_error()
            self.hud.withdraw()
            self.status.set("录音未完成：" + value)

    def _retain_on_error(self):
        # 录音/识别失败：本轮已收到但尚未显示的正文仍要可见、可保存。
        # 文字按普通正文写入当前会话控件（内部不替换已有内容或手改，
        # 外部只补写本地副本，绝不向别的窗口打字）；被语音过滤器暂扣的
        # 文字一并按原文留存，不解释成口令，也不再触发工作流或自动改写。
        self.session_failed = True
        self.route_on_drain = False
        self.workflow_voice_action = None
        self.project_voice_action = None
        self.project_on_drain = False
        self.resume_position_pending = False
        self.acoustic_edits.clear()
        self.light_jobs.clear()
        self.cleanup_waiting = None
        self.cleanup_boundary = False
        held = ""
        if self.recording_mode == 'workflow':
            if self.workflow_voice.filter.holding:
                held = self.workflow_voice.filter.raw
            self.workflow_voice.reset()
        elif self.recording_mode == 'project':
            if self.project_voice.filter.holding:
                held = self.project_voice.filter.raw
            self.project_voice.reset()
        else:
            if self.sender.filter.holding:
                held = self.sender.filter.raw
            self.sender.reset_segment()
        # 外部模式下 delta 已逐字写入本地副本，待显示队列不能再重复写入；
        # 只有仍被过滤器暂扣、从未显示过的文字需要补写进副本。
        text = "".join(self.pending) + held if self.internal else held
        self.pending.clear()
        if not text:
            return
        if self.internal:
            widget = self.active_text()
            widget.insert("insert", text)
            widget.see("insert")
        else:
            self.dict_text.insert("end", text)
            self.dict_text.see("end")

    def _retain_late(self, generation, kind, value):
        # 失败后同一代迟到的识别结果：不执行口令、不触发工作流/发送/自动
        # 改写，也不把失败改报成功；新会话由用户显式开始后正常恢复。
        # 实际 Decoder 协议里，finalize（及空稿复原）会先用 emitter.accept
        # 发出真正新增的 delta，随后才发 utterance/sentence 收尾事件。因此
        # 新增正文只以确实收到的 delta 为来源；同段收尾信息（utterance 的
        # emitted/online/text 与 sentence）不是额外的新原话，不再生成第二
        # 份正文——冲突的修订候选既没有对应 delta 也绝不能整句追加，正常
        # 收尾的新字已由它的 delta 先行保留。这既保住真实晚到新字且只写
        # 一次，也不依赖 reset 后可变的 decoder 状态或对未来队列的猜测。
        if kind in ("stopped", "paused"):
            self.stopping = False
            return
        if kind != "delta":
            return
        if not value:
            return
        self.raw_transcript = (self.raw_transcript + value)[-64000:]
        if self.internal:
            widget = self.active_text()
            widget.insert("insert", value)
            widget.see("insert")
        else:
            self.dict_text.insert("end", value)
            self.dict_text.see("end")

    def poll(self):
        if self.closing:
            return
        if self.close_boundary:
            self._close_collect()
            self.root.after(25, self.poll)
            return
        for _ in range(200):
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "ready":
                if isinstance(value, dict) and 'refinement' in value:
                    if value['refinement'] != self.mode_switch_request:
                        # 较早请求的完成事件：不得让快速连续选择时较早的
                        # 完成冒充较新目标已经完成。
                        pass
                    else:
                        self.mode_switch_pending = False
                        self.ready = True
                        self._runtime_status('ready')
                        self.status.set("识别已就绪；全局按键不可用，可用试写按钮：" + self.hook_error if self.hook_error
                                        else self.device_notice if self.device_notice
                                        else "点击目标输入框，再轻点听写键" if self.enabled.get()
                                        else "全局快捷键已关闭，可在设置中开启")
                elif self.mode_switch_pending:
                    # 初始加载完成的 ready 不携带请求身份：显式切换仍未收口，
                    # 不能清掉挂起，也不能假报目标模式就绪。
                    self.status.set("模型已加载；正在完成识别方式切换…")
                else:
                    self.ready = True
                    self._runtime_status('ready')
                    self.status.set("识别已就绪；全局按键不可用，可用试写按钮：" + self.hook_error if self.hook_error
                                    else self.device_notice if self.device_notice
                                    else "点击目标输入框，再轻点听写键" if self.enabled.get()
                                    else "全局快捷键已关闭，可在设置中开启")
            elif kind == "refinement_failed":
                if isinstance(value, dict) and value.get('request_id') == self.mode_switch_request:
                    # 本次显式切换失败：保持挂起等待用户再次保存以显式重试；
                    # 界面不得假就绪，也不与首次加载或普通录音错误混淆。
                    self.ready = False
                    self.status.set("识别方式切换未完成：" + str(value.get('error', ''))
                                    + "；请再次保存设置以重试")
            elif kind == "fatal":
                self.model_error = value
                self._runtime_status('model_error')
                self.status.set("模型加载失败：" + value)
            elif kind == "hotkey":
                self.trigger(value)
            elif kind=="binding_preview":
                token,binding=value
                if token==self.capture_serial and self.capture_index is not None:
                    from hotkeys import display_binding
                    self.key_labels[self.capture_index].set(display_binding(binding)+" …" if binding else "请按下新按键…")
            elif kind=="binding_captured":
                self.accept_key_capture(*value)
            elif kind=="binding_cancelled":
                if value==self.capture_serial:self.cancel_key_capture()
            elif kind == "manual_edit":
                self._edit(value)
            elif kind == "engine":
                self.handle_engine(*value)
            elif kind == "cleanup":
                self._cleanup_result(value)
            elif kind == 'v1_result':
                from v1_ui import result
                result(self,value)
            elif kind == 'light_result':
                if value['token'] in self.light_jobs:
                    job = self.light_jobs[value['token']]
                    job['result'] = value
                    if value.get('backend') in ('codex','glm') and value.get('proposal') is not None:
                        try:
                            job['sample_id']=self.proofreading.proposal(job.get('profile',self.personal.active),
                                job['before'],value['proposal'],value['text'],job.get('context',''),value['backend'])
                        except Exception:
                            self.correction_hint.set('本次校对记录未保存，当前文字保留')
            elif kind == "recognition_notice":
                self.correction_hint.set(value)
            elif kind == "focus_snapshot":
                self.sender.on_snapshot(*value)
            elif kind == "learning_snapshot":
                try:
                    if value[0][0] in ('light','light_commit'):self._light_snapshot(*value)
                    else:self.learning.on_snapshot(*value)
                except Exception:
                    self.learning.reset()
                    self.correction_hint.set("本次改词未能保存；听写继续")
            elif kind == "audio_overflow":
                # 缓冲真实耗尽：如实记录原因（含积压秒数），不假报正常。
                self.status.set("识别复核长时间阻塞，录音缓冲已耗尽；正在保存已录内容后停止")
                self.finish(reason='buffer_exhausted')
            elif kind == "audio_warning":
                self.status.set("麦克风出现丢帧，请检查设备：" + value)
        if self.capture_index is not None and (foreground()!=top_handle(self.root) or self.tabs.select()!=self.settings_tab):
            self.cancel_key_capture()
        if self.state.mode != "idle" and not self.state.paused and not self.stopping:
            blocked = not self.internal and (foreground() != self.target or (self.target_control and focused_control(self.target) != self.target_control))
            if blocked != self.focus_blocked:
                self.sender.cancel("已切换输入位置，已取消发送。")
                self.sender.reset_segment()
                self._forget_owned()
                self.focus_blocked = blocked
                self.pending.clear()
                self.state.generation += 1
                self.engine.command("gate", blocked=blocked, generation=self.state.generation, at=time.monotonic())
                self.status.set("已离开原输入框，语音写入暂停；返回后继续" if blocked else "已返回原输入框，可以继续说")
        if self.pending and not modifiers_down() and time.monotonic() >= self.edit_until:
            if self.internal:
                widget = self.active_text()
                if self.resume_position_pending:
                    widget.mark_set("insert", "end-1c")
                    widget.tag_remove("sel", "1.0", "end")
                text = "".join(self.pending)
                epoch = self._begin_owned(widget)
                widget.insert("insert", text)
                self._append_owned(text, epoch, widget)
                self.pending.clear()
                self.resume_position_pending = False
                widget.see("insert")
            elif foreground() != self.target or (self.target_control and focused_control(self.target) != self.target_control):
                self.pending.clear()
                self.status.set("目标窗口已改变，未自动输入的文字保留在转写副本中")
            else:
                try:
                    text = "".join(self.pending)
                    epoch = self._begin_owned()
                    if type_unicode(text, self.target, move_to_end=self.resume_position_pending):
                        self.sender.written = self.sender.written or bool(text.strip())
                        self._append_owned(text, epoch)
                        self.pending.clear()
                        self.resume_position_pending = False
                except OSError as exc:
                    self.pending.clear()
                    self.status.set(str(exc) + " 转写副本仍保留。")
                    self.finish(reason='input_error')
        self._apply_acoustic()
        self._apply_light()
        if self.cleanup_boundary and not self.pending:
            self._request_cleanup()
        self.sender.tick()
        if not self.learning_fault:
            try:
                self.learning.tick()
            except Exception:
                self.learning_fault = True
                self.learning.reset()
                from learning_status import record
                record(self.personal,'learning_error')
                self.correction_hint.set("自动学习暂不可用；听写和手动词库仍可使用")
        if hasattr(self,"personal_page"):
            self.personal_page.refresh()
        if self.archive_pending and not self.pending and not self.acoustic_edits and not self.light_jobs and not self.cleanup_waiting:
            self.archive_pending=False
            self.archive_draft('session')
            self._save_draft()
        if self.route_on_drain and not self.pending and not self.cleanup_waiting:
            self.route_on_drain = False
            action=self.workflow_voice_action
            self.workflow_voice_action=None
            if action == 'video_start':
                self.start_video_discussion()
            else:
                self.classify()
                plan=self.prepare_flow(auto=action is None)
                if action=='copy' and plan:self.copy_prompt(plan)
                elif action=='relay' and plan:self.relay_flow()
                elif action=='references':
                    self.add_refs()
                    self.prepare_flow()
            self.workflow_completed = True
        if self.project_on_drain and not self.pending and not self.cleanup_waiting:
            self.project_on_drain=False
            action=self.project_voice_action
            self.project_voice_action=None
            if action=='project_send':self.project_page.send()
            elif action=='project_confirm':self.project_page.confirm()
            elif action=='video_start':self.start_video_discussion()
        self.project_page.refresh()
        from ui import refresh
        refresh(self)
        self.hud_text.set(self.status.get()[:48])
        self.root.after(25, self.poll)

    def classify(self):
        result = route_workflow(self.flow_text.get("1.0", "end-1c"))
        self.route_choice.set(result.label if result.mode not in ("unmatched", "ambiguous") else "待选择工作流")
        self.route_hint.set(result.reason)

    def add_refs(self):
        self.refs.extend(filedialog.askopenfilenames(title="选择本次参考图", filetypes=[("图片", "*.png *.jpg *.jpeg *.webp *.bmp"), ("所有文件", "*.*")]))
        self.refs = list(dict.fromkeys(self.refs))
        self.ref_hint.set(f"已选 {len(self.refs)} 项素材")

    def add_materials(self):
        self.refs.extend(filedialog.askopenfilenames(title="选择当前作品状态、分镜或提示词", filetypes=[("作品文字材料", "*.md *.txt")]))
        self.refs = list(dict.fromkeys(self.refs))
        self.ref_hint.set(f"已选 {len(self.refs)} 项素材")
        self._save_draft()

    def clear_refs(self):
        self.refs.clear()
        self.ref_hint.set("未添加图片")

    def prepare_flow(self, auto=False):
        if self.state.mode != "idle" or self.stopping:
            self.route_hint.set("结束本次录音后会自动整理任务。")
            return None
        text = self.flow_text.get("1.0", "end-1c").strip()
        mode = next((key for key, value in LABELS.items() if value == self.route_choice.get()), "unmatched")
        if mode in ("unmatched", "ambiguous") and text:
            inferred = route_workflow(text)
            if inferred.mode in ("image", "palette", "reference"):
                mode = inferred.mode
                self.route_choice.set(inferred.label)
        if not text or mode in ("unmatched", "ambiguous"):
            self.workflow_plan = None
            self.route_hint.set("原话已保留；选择一个工作流后可整理任务。")
            return None
        try:
            plan = prepare_workflow(text, self.refs, mode)
            reference_states = []
            for path in plan.reference_files:
                file = Path(path)
                stat = file.stat() if file.is_file() else None
                reference_states.append((path, stat.st_size if stat else None, stat.st_mtime_ns if stat else None))
            original = self.raw_transcript if self.raw_mode == "workflow" else ""
            signature = (mode, plan.prompt, tuple(reference_states), original)
            if signature != self.bundle_signature:
                self.last_bundle = save_bundle(plan, ROOT / "handoffs", self.origin_window)
                if original:
                    (self.last_bundle / "raw-transcript.txt").write_text(original, "utf-8")
                self.bundle_signature = signature
            self.workflow_plan = plan
            label = plan.label
            self.route_hint.set(f"{label}任务已整理 · " + ("；".join(plan.missing_inputs) if plan.missing_inputs else "提示词与参考图已就绪"))
            if auto and not plan.missing_inputs:
                action = self.config["workflow_action"]
                if action == "paste_origin": self.fill_origin(plan)
                elif action == "copy": self.copy_prompt(plan)
            return plan
        except (OSError, ValueError) as exc:
            self.route_hint.set("任务未能保存：" + str(exc))
            return None

    def export(self):
        self.prepare_flow()

    def copy_prompt(self, plan=None):
        plan = plan or self.prepare_flow()
        if plan:
            self.root.clipboard_clear()
            self.root.clipboard_append(plan.prompt)
            self.status.set("完整提示词已复制；参考图保存在本次任务目录")

    def start_video_discussion(self):
        self.route_hint.set("本公开版未包含视频制作流程。")

    def relay_flow(self, plan=None, open_fallback=False):
        plan=plan or self.prepare_flow()
        if not plan:return
        try:
            from relay_handoff import build_packet,copy_packet
            packet=build_packet(self.last_bundle,start=True)
            if (not self.origin_window or self.origin_window == top_handle(self.root)
                    or window_process(self.origin_window) != self.origin_process):
                copy_packet(top_handle(self.root),packet)
                if open_fallback:
                    opened = webbrowser.open('https://chatgpt.com/')
                    self.route_hint.set(('已打开 GPT，' if opened else '') + '创作任务已复制；粘贴一次后由接力站开始，尚未确认网页接收。')
                else:
                    self.route_hint.set(f"已复制{plan.label}任务；粘贴到原 GPT 对话后，接力站收到后直接开始，结果回填原对话。")
                return
            self.fill_origin(plan,relay_packet=packet)
        except (OSError,ValueError,KeyError) as exc:
            self.route_hint.set("未交接："+str(exc))

    def fill_origin(self, plan=None, relay_packet=None):
        plan = plan or self.prepare_flow()
        if not plan: return
        origin = self.origin_window
        if not origin or origin == top_handle(self.root) or window_process(origin) != self.origin_process:
            from hotkeys import display_binding
            self.route_hint.set(f"任务已整理；从目标输入框按 {display_binding(self.config['workflow_key'])} 开始，才可填回原处。")
            return
        if not restore_input_focus(origin, self.origin_control):
            self.route_hint.set("原输入框未能恢复，任务已保留，可复制提示词。")
            return
        generation = self.state.generation
        bundle = self.last_bundle
        def deliver():
            if self.closing: return
            if self.state.mode != "idle" or self.stopping or self.state.generation != generation:
                self.route_hint.set("新录音已开始，上一个任务保留在本地，未填入。")
                return
            if (window_process(origin) != self.origin_process or foreground() != origin
                    or focused_control(origin) != self.origin_control):
                self.route_hint.set("原窗口未能恢复，提示词已保留在本次任务中。")
                return
            try:
                if relay_packet:
                    from relay_handoff import copy_packet
                    copy_packet(top_handle(self.root),relay_packet)
                else:
                    self.root.clipboard_clear()
                    self.root.clipboard_append(plan.prompt)
                self.root.update_idletasks()
                if paste_shortcut(origin):
                    self.status.set("已交给浏览器入口 · 等待接收回执" if relay_packet else "完整提示词已填回原输入框 · 未按发送")
                    self.route_hint.set("新版接力站接收后开始；若未出现任务页，本地任务仍保留，尚未确认网页执行。" if relay_packet else "提示词已填回原处；图片附件和网页生成尚未自动执行。")
                    if bundle:
                        path = bundle / "task.json"
                        record = json.loads(path.read_text("utf-8"))
                        record["dispatch_status"] = "browser_handoff_submitted_unconfirmed" if relay_packet else "filled_origin_input_not_sent"
                        if relay_packet:record['relay_request_id']=relay_packet['requestId']
                        path.write_text(json.dumps(record, ensure_ascii=False, indent=2), "utf-8")
                else:
                    self.route_hint.set("有组合键未松开，任务已保留，可用“填回原处”重试。")
            except OSError as exc:
                self.route_hint.set("未能填入原处：" + str(exc))
        self.root.after(150, deliver)

    def open_task(self):
        if self.prepare_flow() and self.last_bundle:
            os.startfile(self.last_bundle)

    def open_gpt(self):
        if self.prepare_flow():
            self.copy_prompt(self.workflow_plan)
            webbrowser.open("https://chatgpt.com/")
            self.route_hint.set("GPT 网页已打开，提示词已复制；尚未自动粘贴、上传或发送。")

    def copy(self, widget):
        self.root.clipboard_clear()
        self.root.clipboard_append(widget.get("1.0", "end-1c"))
        self.status.set("文字已复制")

    def show_original(self):
        window = tk.Toplevel(self.root)
        window.title("本次转写原文 · 尚未自动修正")
        window.geometry("700x430")
        text = tk.Text(window, wrap="word", font=("Microsoft YaHei UI", 12), padx=16, pady=16)
        text.pack(fill="both", expand=True)
        text.insert("1.0", self.raw_transcript or "本次还没有语音转写。")
        text.configure(state="disabled")

    def clear(self, widget):
        if self.state.mode != "idle" or self.stopping:
            self.status.set("请先结束会话再清空")
            return
        if not self.archive_draft('before_clear'):
            return
        self.learning.reset()
        self._forget_owned()
        widget.delete("1.0", "end")
        self._save_draft()
        self.status.set('已新建草稿；原内容在草稿历史中。')

    def close(self):
        if self.closing:
            return
        if getattr(getattr(self, 'state', None), 'mode', 'idle') != 'idle':
            self._record_stop('quit')
        self.closing = True
        self.close_boundary = False
        self.status.set("正在收好最后一句…")
        thread = getattr(self.engine, "thread", None)
        if thread is not None and thread.is_alive():
            # 立即停止新的收音；识别线程的慢收尾（含最后一句复核）在它自己的
            # 线程上进行。这里不 join、不猜时间，只排空已到事件并存一次盘，
            # 然后用 Tk 调度等引擎退出，期间窗口保持可响应。
            try:
                self.engine.command("quit")
            except Exception:
                pass
            self._runtime_status('stopping')
            self._close_collect()
            if not self._close_save():
                return
            self.root.after(25, self._close_step)
            return
        self._close_finish()

    def _close_step(self):
        if not self.closing:
            return
        self._close_collect()
        thread = getattr(self.engine, "thread", None)
        if thread is not None and thread.is_alive():
            self.root.after(25, self._close_step)
            return
        self._close_finish()

    def _close_collect(self):
        # 关窗只收当前 generation 的转写文字；其余事件（发送、工作流路由、
        # 纠错结果、剪贴板、快捷键）一律不再处理，也不会触发外部打字。
        while True:
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if (kind == "engine" and isinstance(value, tuple)
                    and value[0] == self.state.generation
                    and value[1] in ("delta", "sentence", "utterance", "stopped", "paused")):
                self.handle_engine(*value)
        text = "".join(self.pending)
        self.pending.clear()
        self.acoustic_edits.clear()
        # Late engine completion must not hide an unresolved disk failure.
        if self.close_boundary and self.draft_fault:
            self.status.set('草稿尚未存盘，窗口保持打开；保存恢复后再关闭。')
        if not text:
            return
        if self.internal:
            widget = self.active_text()
            if self.resume_position_pending:
                widget.mark_set("insert", "end-1c")
                widget.tag_remove("sel", "1.0", "end")
            widget.insert("insert", text)
            widget.see("insert")
        # 外部听写不向别的窗口补打字；转写副本已在 dict_text 中随本次保存留存。
        self.resume_position_pending = False

    def _close_save(self):
        if self._save_draft(wait=True):
            return True
        # 首次写盘失败：窗口保留等待重试，但不得退回普通 poll——识别线程
        # 稍后交付的尾句仍处在关窗边界内，只收文字，不执行其中的语音口令。
        self.closing = False
        self.close_boundary = True
        self.status.set('草稿尚未存盘，窗口保持打开；保存恢复后再关闭。')
        self.root.after(2000, self._autosave_draft)
        return False

    def _close_finish(self):
        if not self.closing:
            return
        try:
            self.engine.close()
        except Exception:
            pass
        self._close_collect()
        if not self._close_save():
            return
        self.close_boundary = False
        faults = []
        def step(action):
            # 一个非关键资源释放失败不得截断其余清理，更不得把窗口
            # 永久留在 closing 状态；草稿保存失败仍由 _close_save 处理。
            try:
                action()
            except Exception as exc:
                faults.append(type(exc).__name__)
        if self.draft_writer is not None:
            step(self.draft_writer.close)
        def shutdown_ui():
            from v1_ui import shutdown
            shutdown(self)
        step(shutdown_ui)
        step(self.project_page.close)
        step(lambda: self.archive_draft('close'))
        step(lambda: self._runtime_status('stopping'))
        step(self.learning.reset)
        step(self.sender.close)
        if self.hooks:
            step(self.hooks.stop)
        waiting = False
        if self.cleaner is not None:
            step(self.cleaner.close)
            if self.cleaner.thread.is_alive():
                # 校对线程还在外部模型调用里：等它自己退出并恰好释放一次
                # 模型，期间 Tk 事件循环保持可响应，正文不再接受校对结果。
                self._close_faults = faults
                self.root.after(25, self._close_wait_cleaner)
                waiting = True
        if not waiting:
            step(self.personal.close)
            step(self.pronunciation.close)
            step(self.proofreading.close)
            if self.history is not None:
                step(self.history.close)
            if getattr(self,'reuse_library',None) is not None:
                step(self.reuse_library.close)
            step(lambda: self._runtime_status('stopped'))
            if faults:
                logging.getLogger(__name__).warning(
                    '关窗收尾部分资源释放失败：%s', ','.join(faults))
            self._close_destroy()

    def _close_wait_cleaner(self):
        if not self.closing:
            return
        if self.cleaner is not None and self.cleaner.thread.is_alive():
            self.root.after(25, self._close_wait_cleaner)
            return
        faults = getattr(self, '_close_faults', [])
        for owner in (self.personal, self.pronunciation, self.proofreading):
            try:
                owner.close()
            except Exception as exc:
                faults.append(type(exc).__name__)
        if self.history is not None:
            try:
                self.history.close()
            except Exception as exc:
                faults.append(type(exc).__name__)
        if getattr(self,'reuse_library',None) is not None:
            try:self.reuse_library.close()
            except Exception as exc:faults.append(type(exc).__name__)
        try:
            self._runtime_status('stopped')
        except Exception as exc:
            faults.append(type(exc).__name__)
        if faults:
            logging.getLogger(__name__).warning(
                '关窗收尾部分资源释放失败：%s', ','.join(faults))
        self._close_destroy()

    def _close_destroy(self):
        if not self.closing:
            return
        # Cancel scheduled callbacks before Tcl deletes their Python commands.
        # This also makes closing and reopening the UI safe in one process.
        for callback in self.root.tk.splitlist(self.root.tk.call('after', 'info')):
            self.root.after_cancel(callback)
        self.root.destroy()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-hooks", action="store_true")
    parser.add_argument("--smoke-seconds", type=float, default=0)
    parser.add_argument("--restore-draft",type=Path)
    parser.add_argument("--show-settings",action="store_true")
    args = parser.parse_args()
    mutex = None
    if not args.smoke_seconds:
        native = ctypes.WinDLL("kernel32", use_last_error=True)
        native.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        native.CreateMutexW.restype = ctypes.c_void_p
        native.CloseHandle.argtypes = [ctypes.c_void_p]
        mutex = native.CreateMutexW(None, False, "Local\\VoiceInput-LocalStreaming-20260926")
        if not mutex:
            raise ctypes.WinError(ctypes.get_last_error())
        if ctypes.get_last_error() == 183:
            native.CloseHandle(mutex)
            for _ in range(20):
                if show_existing_app(): break
                time.sleep(.05)
            return
    from display import enable_dpi_awareness
    enable_dpi_awareness()
    root = tk.Tk()
    try:
        app = App(root, no_hooks=args.no_hooks,
                  draft_path=None if args.smoke_seconds else ROOT/'data'/'drafts.json')
        if args.restore_draft:
            try:
                from draft_store import DraftStore
                app.restore_draft(DraftStore.read(args.restore_draft))
                app._save_draft()
            except (OSError,ValueError,AttributeError):
                app.status.set("草稿仍在备份文件中，未自动载入")
        if args.show_settings:app.tabs.select(app.settings_tab)
        if args.smoke_seconds:
            root.after(int(args.smoke_seconds * 1000), app.close)
        root.mainloop()
    finally:
        if mutex:
            native.CloseHandle(mutex)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        error = traceback.format_exc()
        (ROOT / "startup-error.log").write_text(error, encoding="utf-8")
        messagebox.showerror("语音输入启动失败", "请把项目目录中的 startup-error.log 交给我排查。\n\n" + error.splitlines()[-1])
        raise
