"""Optional domain-word cross-check; no prompts, downloads, or text rewrites."""
from pathlib import Path
import re
import sherpa_onnx

MODEL=Path(__file__).resolve().parent/'models/firered-asr2-candidate'


def needs_domain_check(primary,others,terms,duration):
    from acoustic_context import clean_terms,spelling_intent
    if not .3<=duration<=12 or spelling_intent(primary):return False
    def heard(text,term):
        # A phrase split as Mid Journey is still the same spoken domain word.
        compact=lambda value:re.sub('[^a-z0-9]','',value.casefold())
        wanted=compact(term)
        if not wanted:return False
        words=re.findall('[A-Za-z0-9]+',text)
        return any(compact(''.join(words[i:j]))==wanted for i in range(len(words))
                   for j in range(i+1,min(len(words),i+3)+1))
    return any(re.search('[A-Za-z]',term) and heard(primary,term)
        and not any(heard(r,term) for r in others if isinstance(r,str)) for term in clean_terms(terms))


class DomainRefiner:
    def __init__(self):
        for name in ('encoder.int8.onnx','decoder.int8.onnx','tokens.txt'):
            if not (MODEL/name).is_file():raise FileNotFoundError('本地专词复听模型不可用')
        self.recognizer=sherpa_onnx.OfflineRecognizer.from_fire_red_asr(
            encoder=str(MODEL/'encoder.int8.onnx'),decoder=str(MODEL/'decoder.int8.onnx'),
            tokens=str(MODEL/'tokens.txt'),num_threads=4,provider='cpu')
        self.backend='FireRedASR2 AED · 专词复听'

    def transcribe(self,audio,rate,online=''):
        if not .3<=len(audio)/rate<=12:return None
        stream=self.recognizer.create_stream();stream.accept_waveform(rate,audio)
        self.recognizer.decode_stream(stream)
        from chinese_script import acoustic_simplified
        result=acoustic_simplified(stream.result.text.strip())
        if not result or len(result)>min(320,max(40,len(online)*2+12)):return None
        return result
