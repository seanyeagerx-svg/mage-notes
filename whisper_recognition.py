"""Optional local multilingual second pass; never downloads during dictation."""
import os
from pathlib import Path
import re
import sys


MODEL = Path(__file__).resolve().parent / 'models' / 'whisper-large-v3'


class WhisperRefiner:
    def __init__(self):
        if not all((MODEL / name).is_file() for name in ('model.bin','config.json','tokenizer.json')):
            raise FileNotFoundError('本地多语言复听模型尚未安装')
        self.dll_handles = []
        if os.name == 'nt':
            folders = list((Path(sys.prefix) / 'Lib/site-packages/nvidia').glob('*/bin'))
            # CTranslate2 dynamically loads CUDA through the legacy PATH lookup.
            # This changes only this process, never Windows' global environment.
            os.environ['PATH'] = os.pathsep.join(map(str,folders)) + os.pathsep + os.environ.get('PATH','')
            for folder in folders:
                self.dll_handles.append(os.add_dll_directory(str(folder)))
        import ctranslate2
        from faster_whisper import WhisperModel
        if not ctranslate2.get_cuda_device_count():
            raise RuntimeError('多语言复听需要可用的本地 NVIDIA 显卡')
        self.model = WhisperModel(str(MODEL), device='cuda', compute_type='int8_float16',
                                  cpu_threads=2, num_workers=1, local_files_only=True)
        self.backend = 'Whisper large-v3 · GPU'
        self.last_info = {}

    def warmup(self):
        import numpy as np
        self.transcribe(np.zeros(16000,dtype=np.float32),16000)

    def transcribe(self, audio, sample_rate, online='', *, hotwords=(), previous='', language=None):
        if language not in (None,'en'):
            raise ValueError('Unsupported explicit acoustic language')
        duration=len(audio)/sample_rate
        if not .2 <= duration <= 32:
            return None
        if sample_rate != 16000:
            import soxr
            audio = soxr.resample(audio, sample_rate, 16000)
        segments, info = self.model.transcribe(audio, beam_size=5, temperature=0,
            condition_on_previous_text=False, vad_filter=False, task='transcribe',
            language=language, no_speech_threshold=.6, log_prob_threshold=-1.0,
            hotwords=', '.join(hotwords) or None, initial_prompt=previous[-160:] or None)
        segments = list(segments)
        self.last_info = dict(language=info.language, language_probability=info.language_probability,
            avg_logprob=[s.avg_logprob for s in segments], no_speech_prob=[s.no_speech_prob for s in segments])
        text = ''.join(s.text for s in segments).strip()
        # The live draft can be much shorter when English was heard as Chinese.
        # Bound expansion by the recorded duration as well as draft length.
        limit=min(640,max(60,len(online)*2+12,min(320,int(duration*24))))
        if (not text or '\ufffd' in text or len(text) > limit
                or any(s.no_speech_prob > .8 and s.avg_logprob < -.6 for s in segments)):
            return None
        from chinese_script import acoustic_simplified
        return acoustic_simplified(re.sub(r'<\|[^|]*\|>', '', text).strip())
