"""Local streaming speech recognizer; optionally stages short audio for human edits."""
from pathlib import Path
from dataclasses import dataclass
import queue
import re
import threading
import time
import numpy as np
import sherpa_onnx
import sounddevice as sd
from audio_backpressure import BackpressureQueue
from core import WordStableEmitter

MODEL = Path(__file__).resolve().parent / "models" / "streaming-zh"
BILINGUAL = Path(__file__).resolve().parent / 'models' / 'streaming-bilingual'


@dataclass
class AudioPacket:
    samples: np.ndarray
    sample_rate: int
    ended_at: float
    capture_id: object = None

    def after(self, boundary):
        started = self.ended_at - len(self.samples) / self.sample_rate
        if boundary <= started:
            return self.samples
        if boundary >= self.ended_at:
            return self.samples[:0]
        return self.samples[min(len(self.samples), max(0, round((boundary - started) * self.sample_rate))):]

    def between(self,start,end=None):
        began=self.ended_at-len(self.samples)/self.sample_rate
        first=max(0,min(len(self.samples),round((start-began)*self.sample_rate)))
        last=len(self.samples) if end is None else max(0,min(len(self.samples),round((end-began)*self.sample_rate)))
        return self.samples[first:max(first,last)]


def create_recognizer(language='auto',endpoint_silence=1.3):
    model = MODEL
    names = ("tokens.txt", "encoder.int8.onnx", "decoder.onnx", "joiner.int8.onnx")
    bilingual_names = ('tokens.txt', 'encoder-epoch-99-avg-1.int8.onnx',
                       'decoder-epoch-99-avg-1.onnx', 'joiner-epoch-99-avg-1.int8.onnx')
    bilingual_available = all((BILINGUAL / name).is_file() for name in bilingual_names)
    if language == 'auto' and bilingual_available:
        return LanguageRecognizer(create_recognizer('zh',endpoint_silence),create_recognizer('bilingual',endpoint_silence))
    if language != 'zh' and bilingual_available:
        model, names = BILINGUAL, bilingual_names
    for name in names:
        if not (model / name).is_file():
            raise FileNotFoundError("基础识别模型缺失：" + str(model / name)
                + "。请先按 README 运行 download_model.py，或放入完整的基础中文模型；可选增强模型不影响基础听写。")
    return sherpa_onnx.OnlineRecognizer.from_transducer(
        tokens=str(model / names[0]), encoder=str(model / names[1]),
        decoder=str(model / names[2]), joiner=str(model / names[3]),
        num_threads=2, sample_rate=16000, feature_dim=80,
        decoding_method="modified_beam_search" if model==BILINGUAL else "greedy_search",
        modeling_unit='cjkchar+bpe' if model==BILINGUAL else 'cjkchar',
        bpe_vocab=str(model/'bpe.vocab') if model==BILINGUAL else '',
        hotwords_score=1.5,max_active_paths=4,provider="cpu", enable_endpoint_detection=True,
        rule1_min_trailing_silence=2.0, rule2_min_trailing_silence=endpoint_silence,
        rule3_min_utterance_length=25.0)


class LanguageStream:
    def __init__(self,chinese,bilingual):
        self.chinese,self.bilingual=chinese,bilingual
        self.english=False
        from streaming_gate import InitialLanguageGate
        self.commit_gate=InitialLanguageGate()
        self.chinese_text=self.bilingual_text=''

    def accept_waveform(self,rate,samples):
        self.chinese.accept_waveform(rate,samples)
        self.bilingual.accept_waveform(rate,samples)

    def accept_original_waveform(self,rate,samples,original):
        # Keep the existing Chinese acoustic scale. Quiet compensation helps
        # the bilingual model, but can degrade otherwise clear Chinese words.
        # Both paths share the speech gate; raw rejected noise never leaks.
        self.chinese.accept_waveform(rate,np.where(samples!=0,original,0).astype(np.float32))
        self.bilingual.accept_waveform(rate,samples)

    def input_finished(self):
        self.chinese.input_finished()
        self.bilingual.input_finished()


class LanguageRecognizer:
    """Retain the stronger Chinese model; route evident English to bilingual.

    The models see the same audio. A later change of hypothesis is handled by
    the existing append-only emitter and verified final correction, never by
    deleting a user's partial text from the recognizer thread.
    """
    def __init__(self,chinese,bilingual):
        self.chinese,self.bilingual=chinese,bilingual
        self.hotwords=()

    def create_stream(self):
        return LanguageStream(self.chinese.create_stream(),self.bilingual.create_stream(
            '/'.join(term.upper() for term in self.hotwords) if self.hotwords else None))

    def set_context(self,context):
        from acoustic_context import select_terms,spelling_intent
        self.hotwords=() if spelling_intent(context.get('previous','')) else select_terms(context.get('terms',()))

    def is_ready(self,stream):
        return self.chinese.is_ready(stream.chinese) or self.bilingual.is_ready(stream.bilingual)

    def decode_stream(self,stream):
        for model,part in ((self.chinese,stream.chinese),(self.bilingual,stream.bilingual)):
            if model.is_ready(part):model.decode_stream(part)

    def get_result(self,stream):
        zh=self.chinese.get_result(stream.chinese)
        en=self.bilingual.get_result(stream.bilingual)
        stream.chinese_text,stream.bilingual_text=zh,en
        letters=len(re.findall('[A-Za-z]',en))
        han=len(re.findall(r'[\u3400-\u9fff]',en))
        short_acronym = han==0 and re.sub(r'\s+','',en).upper() in {'GPT','SOP','API','CPU','GPU','USB'}
        from language_routing import mixed_language_candidate
        stream.english=bool(en and ((letters>=4 or short_acronym) and letters>=han*2
            or mixed_language_candidate(zh,en)))
        return en if stream.english or not zh else zh

    def ready_to_commit(self,stream,elapsed,emitted):
        # Early English syllables often look like Chinese to the first model.
        # Give the other stream one chunk before committing a disagreeing
        # Chinese opening. Hypotheses and the final command evidence stay raw.
        return stream.commit_gate.allowed(stream.chinese_text,stream.bilingual_text,
            stream.english,elapsed,emitted)

    def is_endpoint(self,stream):
        # A Chinese-only model can treat an English word as trailing silence.
        # Do not split while either decoder still hears this utterance. Both
        # retain the same maximum utterance bound and explicit stop still flushes.
        return (self.chinese.is_endpoint(stream.chinese)
                and self.bilingual.is_endpoint(stream.bilingual))


class Decoder:
    def __init__(self, recognizer, emit, refiner=None,emitter_factory=WordStableEmitter):
        self.recognizer = recognizer
        self.emit = emit
        self.refiner = refiner
        self.retain_audio = False
        self.emitter_factory=emitter_factory
        self.backlog_seconds = lambda: 0.0
        self.refinement_in_progress = False
        self.refinement_deferred = False
        self.last_refinement_seconds = 0.0
        self.reset()

    def reset(self):
        self.stream = self.recognizer.create_stream()
        self.emitter = self.emitter_factory()
        self.last_text = ""
        self.finished = False
        self.input_rate = None
        self.audio_parts = []
        self.audio_frames = 0
        self.audio_over_limit = False
        self.audible_frames = 0
        self.silent_frames = 0
        self.total_frames = 0

    def feed(self, samples, sample_rate, original=None, speech_active=None, detected_silence=None):
        if self.input_rate is not None and self.input_rate != sample_rate:
            raise ValueError("同一段录音的采样率发生变化，请重新开始录音")
        self.input_rate = sample_rate
        self.total_frames += len(samples)
        if speech_active is not None:
            self.silent_frames = 0 if speech_active else self.silent_frames+len(samples)
        if np.any(np.abs(samples)>.00001):
            self.audible_frames += len(samples)
        if self.refiner is not None and not self.audio_over_limit:
            self.audio_frames += len(samples)
            if self.audio_frames <= sample_rate * 32:
                if original is not None and len(original) != len(samples):
                    raise ValueError('原音与实时音频长度不一致')
                self.audio_parts.append((samples if original is None else original).copy())
            else:
                self.audio_parts.clear()
                self.audio_over_limit = True
        if original is not None and hasattr(self.stream,'accept_original_waveform'):
            self.stream.accept_original_waveform(sample_rate,samples,original)
        else:
            self.stream.accept_waveform(sample_rate, samples)
        while self.recognizer.is_ready(self.stream):
            self.recognizer.decode_stream(self.stream)
        result = self.recognizer.get_result(self.stream)
        gate=getattr(self.recognizer,'ready_to_commit',None)
        can_commit=gate is None or gate(self.stream,self.total_frames/sample_rate,self.emitter.emitted)
        delta = self.emitter.accept(result) if can_commit else ''
        if delta:
            self.emit("delta", delta)
        if result != self.last_text:
            self.last_text = result
            self.emit("partial", result)
        if self.emitter.conflict and self.refiner is None:
            self.emit("revision", result)
        # An out-of-vocabulary English word may look like silence to both fast
        # decoders. Actual VAD activity vetoes ending in the middle of that word.
        endpoint_allowed = (speech_active is None or self.silent_frames>=sample_rate*.45
                            or self.total_frames>=sample_rate*25)
        model_endpoint=self.recognizer.is_endpoint(self.stream) and endpoint_allowed
        # The two language decoders can disagree forever about a completed
        # mixed-language sentence. A real neural pause is an independent end
        # signal, measured before the frontend's 640-ms look-back delay. The
        # frontend retains the original consonant tail; finish/pad it normally.
        pause_endpoint=(detected_silence is not None and detected_silence>=1.5
                        and self.audible_frames>=sample_rate*.2)
        if model_endpoint or pause_endpoint:
            self.finalize(pad=not model_endpoint)
            self.reset()

    def finalize(self, pad=True):
        if self.finished:
            return
        if pad:
            # OnlineStream fixes its input resampler at the first waveform's rate.
            # Mixing 16 kHz padding with a 44.1/48 kHz microphone fatally exits C++.
            rate = self.input_rate or 16000
            self.stream.accept_waveform(rate, np.zeros(round(rate * 0.5), dtype=np.float32))
            self.stream.input_finished()
            self.finished = True
            while self.recognizer.is_ready(self.stream):
                self.recognizer.decode_stream(self.stream)
        result = self.recognizer.get_result(self.stream)
        delta = self.emitter.accept(result, final=True)
        if delta:
            self.emit("delta", delta)
        if self.refiner is not None and (result or self.audible_frames >= (self.input_rate or 16000)*.2):
            corrected = None
            error = None
            audio = (np.concatenate(self.audio_parts)
                     if self.audio_parts and not self.audio_over_limit else None)
            audio_staged = bool(self.retain_audio and audio is not None and result and self.emitter.emitted)
            if audio_staged:
                # A person may start correcting the visible draft while the
                # slow acoustic pass runs. Deliver RAM audio first; the UI's
                # existing owned-range/profile checks decide whether to keep it.
                self.emit('audio_segment', dict(online=result, emitted=self.emitter.emitted,
                    _audio=audio, _sample_rate=self.input_rate))
            started = time.monotonic()
            command_online = result
            focus_info = None
            deferred = self.backlog_seconds() > 2.0
            if deferred and not self.refinement_deferred:
                self.emit('backlog', self.backlog_seconds())
            self.refinement_deferred = deferred
            self.refinement_in_progress = audio is not None and not deferred
            try:
                if audio is not None and not deferred:
                    prepared=audio
                    if hasattr(self.refiner,'prepare_audio'):
                        try:
                            prepared=self.refiner.prepare_audio(audio,self.input_rate)
                            focus_info=getattr(self.refiner,'last_focus',None)
                            if focus_info and focus_info.get('action')=='separated':
                                from focused_draft import read_selected_voice
                                selected_draft=read_selected_voice(self.recognizer,prepared,self.input_rate)
                                if selected_draft:
                                    result=selected_draft
                                else:
                                    prepared=audio
                                    focus_info=dict(focus_info,action='original',owner_confirmed=False,
                                        reason='selected_draft_unavailable')
                        except Exception as exc:
                            prepared=audio;result=command_online
                            self.refiner.focus_error=type(exc).__name__
                            focus_info=dict(action='original',reason='focus_prepare_error',
                                reference_available=True,owner_confirmed=False,error=type(exc).__name__)
                    corrected = self.refiner.transcribe(prepared, self.input_rate, result)
            except Exception as exc:
                error = str(exc)
            finally:
                self.refinement_in_progress = False
                self.last_refinement_seconds = time.monotonic() - started
            if not result and corrected:
                from recognition import recover_missing_draft
                recovered=recover_missing_draft(corrected,getattr(self.refiner,'last_support',None),
                                                 getattr(self.refiner,'last_alternatives',[]))
                if recovered:
                    result=recovered
                    delta=self.emitter.accept(recovered,final=True)
                    if delta:self.emit('delta',delta)
            if not result:
                self.audio_parts.clear()
                self.finished=True
                return
            # A deferred pass has no evidence of its own. In particular, the
            # refiner still holds fields from the previous utterance in RAM.
            # Those must never authorize a repair or a spoken send command.
            evidence = None if deferred else self.refiner
            payload = {"online": result, "emitted": self.emitter.emitted,
                "command_online": command_online,
                "text": corrected or result, "refined": corrected is not None,
                "primary_backend": getattr(evidence,'last_primary_backend',getattr(evidence,'backend',None)),
                "support": getattr(evidence,'last_support',None),
                "alternatives": getattr(evidence,'last_alternatives',[]),
                "english_reading": getattr(evidence,'last_english_reading',None),
                "personal_word": getattr(evidence,'last_personal_word',None),
                "short_english": getattr(evidence,'last_short_english',None),
                "empty_english": getattr(evidence,'last_empty_english',None),
                "unprompted_readings": {
                    "primary": getattr(evidence,'last_unprompted_primary',None),
                    "support": getattr(evidence,'last_unprompted_support',None),
                    "whisper": getattr(evidence,'last_unprompted_multilingual',None)},
                "speaker_focus": focus_info,
                "seconds": time.monotonic() - started, "error": error,
                "refinement_deferred": deferred}
            if self.retain_audio and self.audio_parts and not self.audio_over_limit:
                payload['_audio'] = audio
                payload['_sample_rate'] = self.input_rate
                payload['_audio_staged'] = audio_staged
            self.emit("utterance", payload)
            self.audio_parts.clear()
        elif result:
            self.emit("sentence", result)
        if self.emitter.conflict and self.refiner is None:
            self.emit("revision", result)
        self.finished = True


class SpeechEngine:
    def __init__(self, events: queue.Queue, enhanced=True):
        self.events = events
        self.commands = queue.Queue()
        # A slow multi-model recheck inside one finalize blocks this single
        # consumer thread. The bounded backlog (audio_backpressure) rides out
        # such a recheck instead of overflowing after 6.4 s and stopping a
        # recording the user never asked to stop.
        self.audio = BackpressureQueue()
        self.recording = None
        self.capture_lock=threading.RLock()
        self.capture_requested=False
        self.capture_cutoff=None
        self.capture_id=None
        self.decoder = None
        self.generation = 0
        self.hold_until = 0.0
        self.drop_before = 0.0
        self.gated = False
        self.closed = False
        self.audio_serial = 0
        self.last_voice_at = 0.0
        self.frontend = None
        self.filter_enabled = True
        self.silence_seconds = 0.0
        self.pause_before = 0.0
        self.enhanced = enhanced
        # 真实停止原因（供 UI 与诊断使用）；普通收尾保持 None。
        self.stop_note = None
        # 切换尚未以一次成功的 Decoder.reset 收口（reset 会向外部识别器
        # 真实请求新流）；置位期间同目标重试必须真正完成 reset，不得凭
        # 目标布尔值假报 ready。
        self.refinement_pending = False
        from volume_window import limits
        self.volume_window=limits()
        self.raw_input_db=-80.0
        self.volume_accepted=True
        self.thread = threading.Thread(target=self._run, name="speech-engine", daemon=True)
        self.thread.start()

    def backlog_seconds(self):
        # 旧式普通 Queue（外部适配/旧测试）没有积压统计时按 0 秒报告。
        return getattr(self.audio, 'queued_seconds', lambda: 0.0)()

    def command(self, name, **kwargs):
        if name == 'volume_window':
            from volume_window import limits
            self.volume_window=limits(kwargs.get('enabled',False),kwargs.get('minimum',-65),kwargs.get('maximum',-4))
            return
        if name in ('start','resume'):
            with self.capture_lock:self.capture_requested=True
        elif name in ('stop','pause','quit'):
            cutoff=time.monotonic()
            with self.capture_lock:
                self.capture_requested=False
                self.capture_cutoff=cutoff
            # Model inference must not keep the microphone recording after the
            # user pressed stop. Closing capture is separate from draining and
            # recognizing the audio that was already captured before this key.
            try:self._close_mic()
            except Exception:
                self.events.put(('recognition_notice','收音关闭时出现异常；已停止接收新的录音'))
        self.commands.put((name, kwargs))

    def event(self, kind, value):
        if kind == 'utterance':
            value['pause_before'] = self.pause_before
            self.pause_before = 0.0
        self.events.put(("engine", (self.generation, kind, value)))

    def _reset_frontend(self):
        if self.frontend is not None:
            self.frontend.reset()
        self.silence_seconds = self.pause_before = 0.0

    def _feed_audio(self, samples, final=False):
        original = None
        if self.frontend is not None and self.filter_enabled:
            samples = self.frontend.flush() if final else self.frontend.feed(samples, self.rate)
            rate = self.frontend.rate
            original = self.frontend.last_original
            if self.frontend.new_speech_gap is not None and not self.decoder.last_text:
                self.pause_before = self.frontend.new_speech_gap
        else:
            if final:
                return
            rate = self.rate
        if not len(samples):
            return
        active = bool(np.any(np.abs(samples) > .00001))
        if active:
            if self.silence_seconds >= .2 and not self.decoder.last_text and not (self.frontend is not None and self.filter_enabled):
                self.pause_before = self.silence_seconds
            self.silence_seconds = 0.0
            # Quiet detected speech must cancel a pending send, too. The raw
            # callback remains a conservative immediate cancellation guard.
            self.audio_serial += 1
            self.last_voice_at = time.monotonic()
        else:
            self.silence_seconds += len(samples) / rate
        self.decoder.feed(samples, rate, original=original, speech_active=active if original is not None else None,
            detected_silence=(self.frontend.silence_frames/self.frontend.rate if original is not None else None))

    def _clear_audio(self):
        while True:
            try:
                self.audio.get_nowait()
            except queue.Empty:
                break

    def observe_audio(self, samples, ended_at=None):
        level=float(np.sqrt(np.mean(samples ** 2))) if len(samples) else 0.0
        # This runs on the capture callback too: queued ASR work cannot hide new
        # speech from the send guard. Noise may cancel a send, never authorize one.
        if level >= .012:
            self.audio_serial += 1
            self.last_voice_at = ended_at if ended_at is not None else time.monotonic()
        return level

    def _close_mic(self):
        with self.capture_lock:
            stream = self.recording
            self.recording = None
        if stream is not None:
            try:
                stream.stop()
            finally:
                stream.close()

    def _open_mic(self, device=None):
        if not self.capture_requested:return
        # At most one live input stream: a repeat start/resume must retire the
        # previous capture instead of overwriting and leaking it.
        self._close_mic()
        info = sd.query_devices(device, "input")
        rate = int(info["default_samplerate"])
        self.rate = rate
        # A callback can outlive its capture. Invalidate the previous capture
        # before clearing its queue, and tag packets to cover delayed enqueue.
        capture_id = self.capture_id = object()
        self._clear_audio()

        def callback(samples, frames, timing, status):
            if self.capture_id is not capture_id:
                return
            if status:
                self.events.put(("audio_warning", str(status)))
            try:
                now = time.monotonic()
                end_offset = timing.inputBufferAdcTime + frames / rate - timing.currentTime
                ended_at = now + end_offset if abs(end_offset) < 2.0 else now
                packet=AudioPacket(samples[:,0],rate,ended_at,capture_id)
                cutoff=self.capture_cutoff
                if cutoff is not None:
                    began=ended_at-frames/rate
                    kept=packet.between(began,cutoff)
                    if not len(kept):return
                    packet=AudioPacket(kept,rate,began+len(kept)/rate,capture_id)
                self.observe_audio(packet.samples,packet.ended_at)
                from volume_window import filter_packet
                accepted,self.raw_input_db,self.volume_accepted=filter_packet(
                    packet.samples,getattr(self,'volume_window',(False,-65,-4)))
                if self.capture_id is not capture_id:
                    return
                self.audio.put_nowait(AudioPacket(accepted.copy(),rate,packet.ended_at,capture_id))
            except queue.Full:
                # Bounded backlog genuinely exhausted: report the real reason
                # with the real backlog size. Never drop packets silently.
                # (Plain Queue adapters without backlog stats report None.)
                measure = getattr(self.audio, 'queued_seconds', None)
                seconds = measure() if callable(measure) else None
                self.stop_note = ('buffer_exhausted', seconds)
                self.events.put(("audio_overflow", seconds))
        stream=sd.InputStream(device=device,channels=1,samplerate=rate,
                              dtype="float32",blocksize=int(rate*.08),callback=callback)
        with self.capture_lock:
            if not self.capture_requested:
                stream.close();return
            self.capture_cutoff=None
            self.recording=stream
            try:
                stream.start()
            except Exception:
                # The device never started; retire the half-open stream so the
                # error handler finds no live capture left behind.
                self.recording=None
                try:
                    stream.stop()
                finally:
                    stream.close()
                raise
        self.event("listening", {"device": info["name"], "sample_rate": rate})

    def _drain(self):
        while not self.audio.empty():
            data = self._packet_samples(self.audio.get_nowait())
            if not self.gated and time.monotonic() >= self.hold_until:
                if len(data):
                    self._feed_audio(data)

    def _packet_samples(self, packet):
        if isinstance(packet, AudioPacket):
            if packet.capture_id is not None and packet.capture_id is not getattr(self,'capture_id',None):
                return packet.samples[:0]
            return packet.between(self.drop_before,self.capture_cutoff)
        # Public-file adapters without a capture clock are still supported.
        return packet

    def _load_refiner(self):
        try:
            from recognition import AcousticRefiner
            # Raw/personal modes do not use the larger model's word repairs.
            # Keep the small acoustic pass: it also vetoes false send commands.
            refiner = AcousticRefiner(model='auto' if self.enhanced else 'sensevoice',
                                      quiet_gain=True,domain_verifier=self.enhanced,english_verifier=self.enhanced,
                                      speaker_focus=self.enhanced,accuracy='high' if self.enhanced else 'balanced')
            if refiner.fallback_reason:
                self.events.put(('recognition_notice','加强识别暂不可用，已用原有本地模型复核'))
            return refiner
        except Exception as exc:
            self.events.put(("recognition_notice", "原音复核暂不可用，仍可实时听写：" + str(exc)))
            return None

    def _run(self):
        refiner = None
        try:
            recognizer = create_recognizer()
            refiner = self._load_refiner()
            self.decoder = Decoder(recognizer, self.event, refiner=refiner)
            self.decoder.backlog_seconds = self.backlog_seconds
            try:
                from audio_frontend import AudioFrontend
                self.frontend = AudioFrontend(quiet_gain=True,continuation_threshold=.18)
            except Exception as exc:
                self.events.put(('recognition_notice', '环境过滤暂不可用，仍可听写：' + str(exc)))
            self.events.put(("ready", None))
        except Exception as exc:
            self.events.put(("fatal", str(exc)))
            # 初始化失败退出也要归还已创建的 refiner：由本工作线程在退出前
            # 恰好释放一次；refiner 已交给可用 Decoder 时交给循环出口统一收
            # 口，这里不重复释放。释放异常不得覆盖上面的 fatal。
            decoder = self.decoder
            handed_over = decoder is not None and getattr(decoder, 'refiner', None) is refiner and refiner is not None
            if not handed_over and refiner is not None and hasattr(refiner, 'close'):
                try:
                    refiner.close()
                except Exception as release_exc:
                    self.events.put(("recognition_notice",
                                     "复核资源释放时出现异常：" + str(release_exc)))
            return
        while not self.closed:
            try:
                # Pending capture packets already provide work. Do not add a
                # 10-ms idle wait to every packet while catching up after ASR.
                name, values = self.commands.get(timeout=0 if not self.audio.empty() else 0.01)
                if name == "start":
                    self.generation = values["generation"]
                    self.decoder.reset()
                    self.filter_enabled = values.get('noise_filter', True)
                    self._reset_frontend()
                    self.gated = False
                    self.hold_until = 0
                    self.drop_before = 0
                    self.stop_note = None
                    self._open_mic(values.get("device"))
                elif name == 'context':
                    self.decoder.retain_audio=bool(values.get('retain_audio',False))
                    if hasattr(recognizer,'set_context'):
                        recognizer.set_context(values.get('context',{}))
                    if self.decoder.refiner is not None and hasattr(self.decoder.refiner,'set_context'):
                        self.decoder.refiner.set_context(values.get('context',{}))
                elif name == 'refinement':
                    request = values.get('request_id')
                    try:
                        if self.recording is not None:
                            raise ValueError('结束录音后才能切换识别方式')
                        target = values['enhanced']
                        if self.enhanced != target or self.refinement_pending:
                            if self.enhanced != target:
                                old = self.decoder.refiner
                                if old is not None and hasattr(old,'close'):
                                    try:
                                        old.close()
                                    except Exception as close_exc:
                                        # 释放失败只降级为提示；切换继续完成，
                                        # 不让半关闭的旧实例冒充目标模式继续被使用。
                                        self.events.put(("recognition_notice",
                                                         "复核资源释放时出现异常：" + str(close_exc)))
                                self.decoder.refiner = None
                                self.enhanced = target
                                self.decoder.refiner = self._load_refiner()
                            # 只有 reset 真实成功（外部识别器给出新流）才算完成；
                            # 失败时 refinement_pending 保持置位，等显式重试收口。
                            self.refinement_pending = True
                            self.decoder.reset()
                            self.refinement_pending = False
                        # 完成事件携带本次请求身份：界面凭它区分首次加载的
                        # ready、较早请求的完成与本次显式切换的收口。
                        self.events.put(('ready', {'refinement': request}))
                    except Exception as exc:
                        # 与外层通用错误路径相同的设备/队列收口：被拒绝或
                        # 失败的切换不得让已报错的输入流继续保持采集。随后
                        # 先通知通用错误，再以同一请求身份显式上报切换失败，
                        # 让界面最终以切换专属文案收尾，不与首次加载或普通
                        # 录音错误混淆。
                        try:
                            self._close_mic()
                        except Exception:
                            pass
                        self._clear_audio()
                        self.event("error", str(exc))
                        self.events.put(('refinement_failed',
                                         {'request_id': request, 'error': str(exc)}))
                elif name == "cut":
                    self.generation = values["generation"]
                    self.decoder.reset()
                    self._reset_frontend()
                    self.drop_before = values.get("at", time.monotonic())
                    self.hold_until = time.monotonic() + values.get("delay", 0.0)
                elif name == "gate":
                    self.gated = values["blocked"]
                    self.generation = values["generation"]
                    self.decoder.reset()
                    self._reset_frontend()
                    self.drop_before = values.get("at", time.monotonic())
                elif name in ("stop", "pause", "quit"):
                    try:
                        self._close_mic()
                        self._drain()
                        if not self.gated:
                            self._feed_audio(None, final=True)
                        self.decoder.finalize()
                        self.event("stopped" if name != "pause" else "paused", None)
                    finally:
                        if name == "quit":
                            self.closed = True
                elif name == "resume":
                    self.generation = values["generation"]
                    self.decoder.reset()
                    self.filter_enabled = values.get('noise_filter', True)
                    self._reset_frontend()
                    self.gated = False
                    self.hold_until = 0
                    self.drop_before = 0
                    self.stop_note = None
                    self._open_mic(values.get("device"))
            except queue.Empty:
                pass
            except Exception as exc:
                try:
                    self._close_mic()
                except Exception:
                    pass
                self._clear_audio()
                self.capture_requested = False
                self.stop_note = ('device_error', type(exc).__name__)
                self.event("error", str(exc))
            if self.recording is not None:
                try:
                    samples = self._packet_samples(self.audio.get_nowait())
                    if not len(samples):
                        continue
                    self.event("level", self.observe_audio(samples))
                    if not self.gated and time.monotonic() >= self.hold_until:
                        self._feed_audio(samples)
                except queue.Empty:
                    pass
                except Exception as exc:
                    error = str(exc)
                    try:
                        self._close_mic()
                    except Exception as close_error:
                        error += "；关闭设备时也发生错误：" + str(close_error)
                    self._clear_audio()
                    self.capture_requested = False
                    self.stop_note = ('device_error', type(exc).__name__)
                    self.event("error", error)
        # The loop has exited after quit, so the worker is past its last
        # refiner use (including a slow transcribe still finishing). Release
        # here, not in the caller's thread, and never let a release error
        # skip the caller's join.
        refiner = getattr(getattr(self, 'decoder', None), 'refiner', None)
        if refiner is not None and hasattr(refiner, 'close'):
            try:
                refiner.close()
            except Exception as exc:
                self.events.put(("recognition_notice", "复核资源释放时出现异常：" + str(exc)))
            self.decoder.refiner = None

    def close(self):
        # Quit 只在调用线程入队并立即停麦；refiner 由工作线程在最后一次
        # 使用之后释放，调用线程只负责 join。释放抛错也不能跳过 join。
        self.command("quit")
        self.thread.join(4)
