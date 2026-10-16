"""Neural speech detection, independent of a fixed loudness threshold.

Silero classifies the original speech. A short look-back preserves quiet
consonants; rejected frames become silence so ASR endpoint timing survives.
Destructive denoising was rejected by measured ASR regression. No audio leaves
RAM and no speaker identity is inferred.
"""
from collections import deque
from pathlib import Path
import numpy as np
import sherpa_onnx
import soxr

MODEL = Path(__file__).resolve().parent / 'models' / 'audio'


class AudioFrontend:
    rate = 16000
    def __init__(self,quiet_gain=False,threshold=.42,continuation_threshold=None):
        from audio_gain import QuietGain
        self.quiet_gain=QuietGain(self.rate) if quiet_gain else None
        paths = [MODEL / 'silero_vad.onnx']
        for p in paths:
            if not p.is_file():
                raise FileNotFoundError('缺少环境过滤模型：' + p.name)
        config = sherpa_onnx.VadModelConfig()
        config.silero_vad.model = str(paths[0])
        config.silero_vad.threshold = threshold
        config.silero_vad.min_speech_duration = .064
        config.silero_vad.min_silence_duration = .24
        config.sample_rate = self.rate
        config.num_threads = 1
        self.vad = sherpa_onnx.VadModel.create(config)
        self.continuation=None
        if continuation_threshold is not None:
            config.silero_vad.threshold=continuation_threshold
            self.continuation=sherpa_onnx.VadModel.create(config)
        self.window = self.vad.window_size()
        self.lookback = round(.64 * self.rate / self.window)
        self.reset()

    def reset(self):
        self.vad.reset()
        if self.continuation is not None:self.continuation.reset()
        if self.quiet_gain is not None:self.quiet_gain.reset()
        self.pending = np.empty(0, dtype=np.float32)
        self.detect_pending = np.empty(0, dtype=np.float32)
        self.resampler = None
        self.input_rate = None
        self.finished = False
        self.history = deque()
        self.speaking = False
        self.seen_speech = False
        self.continuing = False
        self.silence_frames = 0
        self.new_speech_gap = None
        self.input_frames = self.output_frames = self.speech_frames = 0
        self.last_original = np.empty(0,dtype=np.float32)

    def _classify(self, samples, detector_samples, final=False):
        self.pending = np.concatenate((self.pending, np.asarray(samples, dtype=np.float32)))
        self.detect_pending = np.concatenate((self.detect_pending, np.asarray(detector_samples, dtype=np.float32)))
        output = []
        originals = []
        while len(self.pending) >= self.window or (final and len(self.pending)):
            frame, self.pending = self.pending[:self.window], self.pending[self.window:]
            detect, self.detect_pending = self.detect_pending[:self.window], self.detect_pending[self.window:]
            valid = len(frame)
            processed=self.quiet_gain.process(frame) if self.quiet_gain is not None else frame
            if self.quiet_gain is not None:detect=processed
            padded = np.pad(detect, (0, self.window-valid)) if valid < self.window else detect
            speech = self.vad.is_speech(padded)
            if self.continuation is not None:
                continuing=self.continuation.is_speech(padded)
                # The sensitive detector may preserve a weak continuation, but
                # can never open a segment without the normal detector first.
                speech=speech or (self.continuing and continuing)
                self.continuing=speech
            if speech:
                if not self.speaking and self.seen_speech:
                    self.new_speech_gap = self.silence_frames / self.rate
                self.seen_speech = True
                self.silence_frames = 0
            else:
                self.silence_frames += valid
            # Keep a short original tail after detected speech. Short English
            # words/unvoiced consonants must not turn into an artificial pause.
            keep_tail = self.seen_speech and self.silence_frames <= self.rate * .8
            self.history.append([processed, speech or keep_tail, frame])
            if speech:
                # Start detection is delayed by a few frames. Recover their
                # original samples instead of clipping the first word.
                for item in self.history:
                    item[1] = True
            self.speaking = speech
            while self.history and (speech or len(self.history) > self.lookback):
                item, keep, original = self.history.popleft()
                self.speech_frames += len(item) if keep else 0
                output.append(item if keep else np.zeros_like(item))
                originals.append(original)
        if final:
            while self.history:
                item, keep, original = self.history.popleft()
                self.speech_frames += len(item) if keep else 0
                output.append(item if keep else np.zeros_like(item))
                originals.append(original)
        result = np.concatenate(output) if output else np.empty(0, dtype=np.float32)
        self.last_original = np.concatenate(originals) if originals else np.empty(0,dtype=np.float32)
        self.output_frames += len(result)
        return result

    def feed(self, samples, rate):
        if self.finished:
            raise ValueError('这段录音已经结束')
        self.new_speech_gap = None
        self.input_frames += len(samples)
        if self.input_rate is None:
            self.input_rate = rate
            if rate != self.rate:
                self.resampler = soxr.ResampleStream(rate,self.rate,1,dtype='float32',quality='HQ')
        elif rate != self.input_rate:
            raise ValueError('收音采样率变化，请重新开始')
        raw = self.resampler.resample_chunk(samples) if self.resampler else samples
        return self._classify(raw,raw)

    def flush(self):
        if self.finished:
            return np.empty(0,dtype=np.float32)
        self.finished = True
        self.new_speech_gap = None
        tail = self.resampler.resample_chunk(np.empty(0,dtype=np.float32),last=True) if self.resampler else np.empty(0,dtype=np.float32)
        return self._classify(tail,tail,final=True)
