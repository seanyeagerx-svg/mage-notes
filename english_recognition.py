"""An independent local English acoustic reader, never a translator.

It is consulted only after existing bilingual models already indicate English.
Its result has a separate evidence field so it cannot silently outvote a correct
baseline through unrelated correction rules.
"""
from pathlib import Path
import re
import numpy as np
import sherpa_onnx
from audio_gain import normalize_utterance

MODEL=Path(__file__).resolve().parent/'models/parakeet-v2'


def english_reading(text):
    return (isinstance(text,str) and bool(re.search('[A-Za-z]',text))
        and not re.search('[\u3400-\u9fff]',text))


def needs_english_reading(online,primary,support,alternatives=()):
    from acoustic_context import spelling_intent
    from recognition import _agreement_tokens
    if spelling_intent(online or ''):return False
    existing=[online,primary,support]+list(alternatives or ())
    if sum(english_reading(r) for r in existing)<2:return False
    if any(isinstance(r,str) and re.search('[\u3400-\u9fff]',r) for r in [primary,support]):return False
    source=_agreement_tokens(online)
    if source and source==_agreement_tokens(primary)==_agreement_tokens(support):return False
    return True


class EnglishRefiner:
    backend='Parakeet TDT 0.6B v2'

    def __init__(self,model=MODEL):
        model=Path(model)
        for name in ('encoder.int8.onnx','decoder.int8.onnx','joiner.int8.onnx','tokens.txt'):
            if not (model/name).is_file():raise FileNotFoundError('缺少本地英文复核模型：'+name)
        self.recognizer=sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=str(model/'encoder.int8.onnx'),decoder=str(model/'decoder.int8.onnx'),
            joiner=str(model/'joiner.int8.onnx'),tokens=str(model/'tokens.txt'),
            num_threads=2,sample_rate=16000,feature_dim=128,model_type='nemo_transducer',provider='cpu')

    def transcribe(self,audio,rate=16000):
        if rate<=0 or not .2<=len(audio)/rate<=32:return None
        if rate!=16000:
            import soxr
            audio=soxr.resample(audio,rate,16000)
        stream=self.recognizer.create_stream()
        stream.accept_waveform(16000,normalize_utterance(np.asarray(audio,np.float32)))
        self.recognizer.decode_stream(stream)
        result=stream.result.text.strip()
        if (not result or len(result)>min(640,max(40,int(len(audio)/16000*24)))
            or re.search('[\u3400-\u9fff\ufffd<>`\n]',result)):return None
        return result
