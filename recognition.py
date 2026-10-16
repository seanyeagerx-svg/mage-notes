"""Acoustic second pass. Uses only the current utterance in RAM, never a cloud API."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import re
import time
import sherpa_onnx

MODEL = Path(__file__).resolve().parent / "models" / "sensevoice"
QWEN = Path(__file__).resolve().parent / 'models' / 'qwen3-asr'
QWEN_LARGE = QWEN.with_name('qwen3-asr-1.7b')


def qwen_text(raw):
    raw=re.sub(r'<\|[^|]*\|>','',raw).strip()
    if '<asr_text>' not in raw:
        return raw
    metadata,text=raw.split('<asr_text>',1)
    if (re.fullmatch(r'language\s+None',metadata.strip(),re.I)
            or not re.fullmatch(r'language\s+[A-Za-z -]+',metadata.strip())):
        return ''
    return text.strip()


def _agreement_tokens(value):
    """Keep word boundaries, signs, decimal points and literal punctuation."""
    if (not isinstance(value,str) or not value.strip() or len(value)>320
            or re.search(r'[\n\r\ufffd<>`]',value)):
        return None
    found=re.findall(r"[\u3400-\u9fff]|[A-Za-z]+(?:['’][A-Za-z]+)?|[+-]?\d+(?:[.:/-]\d+)*|[^\s]",
                     value.casefold().replace('’',"'"))
    # Ordinary sentence endings may differ; internal symbols and questions may not.
    while found and found[-1] in '.,，。!！;；:：、':found.pop()
    return found


def recover_missing_draft(primary,support,alternatives=()):
    target=_agreement_tokens(primary)
    if not target or sum(c.isalnum() for c in primary)<2:return None
    return primary if any(_agreement_tokens(r)==target for r in [support]+list(alternatives or ())) else None


def agreeing_backup_reading(first,second,audio_seconds):
    """Two real families may recover a missing primary, never duplicate a vote.

    Compare whole English words and decimal/sign punctuation rather than
    flattening all characters. A question and a statement do not agree here.
    """
    limit=min(320,max(40,int(audio_seconds*24)))
    if any(not isinstance(value,str) or len(value)>limit for value in (first,second)):
        return None
    a,b=_agreement_tokens(first),_agreement_tokens(second)
    # A trailing question mark is punctuation, not a second spoken word.
    return first if a and sum(any(c.isalnum() for c in token) for token in a)>=2 and a==b else None


def preserve_unprompted_reading(original,hinted,independent):
    """A hint cannot displace this family's reading corroborated before hints."""
    target=_agreement_tokens(original)
    if target and target!=_agreement_tokens(hinted) and any(_agreement_tokens(other)==target for other in independent):
        return original
    return hinted


class AcousticRefiner:
    def __init__(self, model='auto',quiet_gain=False,domain_verifier=False,parallel_refinement=True,english_verifier=False,speaker_focus=False,accuracy='balanced'):
        self.quiet_gain=quiet_gain
        self.parallel_refinement=parallel_refinement
        self.backend = 'SenseVoice'
        self.fallback_reason = None
        self.last_support = None
        self.last_alternatives = []
        self.multilingual = None
        self.multilingual_error = None
        self.support = None
        self.context = {}
        self.last_context_terms = ()
        self.word_memory = None
        self.last_personal_word = None
        self.personal_word_error = None
        self.last_short_english = None
        self.short_english_error = None
        self.last_unprompted_multilingual = None
        self.last_unprompted_multilingual_info = {}
        self.last_unprompted_primary = None
        self.last_unprompted_support=None
        self.last_empty_english=None
        self.empty_english_error=None
        self.domain=None
        self.domain_error=None
        self.last_domain_used=False
        self.english=None
        self.english_error=None
        self.last_english_reading=None
        self.last_multilingual_info={}
        self.last_language_retry=False
        self.language_retry_error=None
        self.target=None
        self.last_focus=None
        self.focus_error=None
        if speaker_focus:
            try:
                from speaker_target import SpeakerTarget
                self.target=SpeakerTarget()
            except Exception as exc:self.focus_error=type(exc).__name__
        if model != 'sensevoice':
            try:
                files=('conv_frontend.onnx','encoder.int8.onnx','decoder.int8.onnx','tokenizer/vocab.json','tokenizer/merges.txt')
                qwen=QWEN_LARGE if accuracy=='high' and all((QWEN_LARGE/name).is_file() for name in files) else QWEN
                for name in ('conv_frontend.onnx','encoder.int8.onnx','decoder.int8.onnx','tokenizer/vocab.json','tokenizer/merges.txt'):
                    if not (qwen/name).is_file():
                        raise FileNotFoundError(name)
                self.recognizer = sherpa_onnx.OfflineRecognizer.from_qwen3_asr(
                    conv_frontend=str(qwen/'conv_frontend.onnx'),encoder=str(qwen/'encoder.int8.onnx'),
                    decoder=str(qwen/'decoder.int8.onnx'),tokenizer=str(qwen/'tokenizer'),
                    num_threads=4,max_total_len=1024,max_new_tokens=256,provider='cpu')
                self.backend = 'Qwen3-ASR 1.7B' if qwen==QWEN_LARGE else 'Qwen3-ASR 0.6B'
                self.support = AcousticRefiner(model='sensevoice')
                if model == 'auto':
                    try:
                        from whisper_recognition import WhisperRefiner
                        self.multilingual = WhisperRefiner()
                        self.multilingual.warmup()
                    except Exception as exc:
                        self.multilingual = None
                        self.multilingual_error = type(exc).__name__
                if domain_verifier:
                    try:
                        from firered_recognition import DomainRefiner
                        self.domain=DomainRefiner()
                    except Exception as exc:self.domain_error=type(exc).__name__
                if english_verifier:
                    try:
                        from english_recognition import EnglishRefiner
                        self.english=EnglishRefiner()
                    except Exception as exc:self.english_error=type(exc).__name__
                return
            except Exception as exc:
                self.fallback_reason = type(exc).__name__
                if model == 'qwen':raise
        for filename in ("model.int8.onnx", "tokens.txt"):
            if not (MODEL / filename).is_file():
                raise FileNotFoundError("缺少本地原音复核模型：" + filename)
        self.recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=str(MODEL / "model.int8.onnx"), tokens=str(MODEL / "tokens.txt"),
            language="", use_itn=True, num_threads=4, provider="cpu")

    def set_context(self, context):
        from acoustic_context import clean_terms
        self.context = dict(terms=clean_terms(context.get('terms', ())), previous=str(context.get('previous',''))[-160:])
        from personal_word_memory import PersonalWordMemory
        if getattr(self,'word_memory',None) is None:self.word_memory=PersonalWordMemory()
        try:
            self.word_memory.configure(context.get('profile'),context.get('word_samples',()))
            self.personal_word_error=None
        except Exception as exc:
            self.word_memory.configure(None,())
            self.personal_word_error=type(exc).__name__
        if getattr(self,'target',None) is not None:
            self.target.configure(context.get('profile'),context.get('voice_samples',()))

    def prepare_audio(self,audio,rate):
        self.last_focus=None
        target=getattr(self,'target',None)
        if target is None:return audio
        selected=target.process(audio,rate)
        self.last_focus=dict(target.last_decision)
        self.focus_error=target.error
        return selected

    def close(self):
        if getattr(self,'target',None) is not None:self.target.close()

    def _primary_reading(self,audio,sample_rate,online,context):
        stream = self.recognizer.create_stream()
        from acoustic_context import retry_terms, spelling_intent, contextual_reading
        stream.accept_waveform(sample_rate, audio)
        self.recognizer.decode_stream(stream)
        result = qwen_text(stream.result.text) if self.backend.startswith('Qwen3') else re.sub(r"<\|[^|]*\|>", "", stream.result.text).strip()
        from chinese_script import acoustic_simplified
        result=acoustic_simplified(result)
        # Empty/noise results and pathological expansions cannot replace speech.
        # A Chinese-looking live error can stand for a much longer English
        # sentence. Bound by available audio too, without trusting raw length.
        limit=min(640,max(40,len(online)*2+12,min(320,int(len(audio)/sample_rate*24))))
        self.last_primary_backend=self.backend
        if (self.backend=='Qwen3-ASR 1.7B'
                and (not result or len(result)>limit or '\ufffd' in result)):
            # Some int8 1.7B clips return the model's language=None marker.
            # Re-read with the installed small model rather than losing all
            # useful independent evidence. Both sizes remain one Qwen family.
            fallback=getattr(self,'fallback_qwen',None)
            if fallback is None:
                fallback=sherpa_onnx.OfflineRecognizer.from_qwen3_asr(
                    conv_frontend=str(QWEN/'conv_frontend.onnx'),encoder=str(QWEN/'encoder.int8.onnx'),
                    decoder=str(QWEN/'decoder.int8.onnx'),tokenizer=str(QWEN/'tokenizer'),
                    num_threads=2,max_total_len=1024,max_new_tokens=256,provider='cpu')
                self.fallback_qwen=fallback
            recovered=fallback.create_stream();recovered.accept_waveform(sample_rate,audio)
            fallback.decode_stream(recovered)
            result=acoustic_simplified(qwen_text(recovered.result.text))
            self.last_primary_backend='Qwen3-ASR 0.6B · 大模型空结果回退'
        if not result or len(result) > limit or "\ufffd" in result:
            return None
        self.last_unprompted_primary=result
        # Keep an unprompted acoustic reading. Hints must not distort ordinary
        # Chinese or turn a spoken spelling into a dictionary word.
        terms = retry_terms(context.get('terms', ()), result, context.get('previous',''))
        if not re.search('[A-Za-z]',result+online) or spelling_intent(online):
            terms = ()
        self.last_context_terms = terms
        if terms and self.backend.startswith('Qwen3') and self.last_primary_backend==self.backend:
            hinted = self.recognizer.create_stream()
            hinted.set_option('hotwords', ','.join(terms))
            hinted.accept_waveform(sample_rate,audio)
            self.recognizer.decode_stream(hinted)
            result = contextual_reading(result,acoustic_simplified(qwen_text(hinted.result.text)),terms)
        return result

    def _multilingual_reading(self,audio,sample_rate,online,context):
        from acoustic_context import retry_terms,spelling_intent,contextual_reading
        alternative=self.multilingual.transcribe(audio,sample_rate,online)
        self.last_unprompted_multilingual=alternative
        self.last_unprompted_multilingual_info=dict(getattr(self.multilingual,'last_info',{}))
        terms=retry_terms(context.get('terms',()),alternative or '',context.get('previous',''))
        if terms and not spelling_intent(online):
            hinted=self.multilingual.transcribe(audio,sample_rate,online,hotwords=terms,
                previous=context.get('previous',''))
            if alternative:alternative=contextual_reading(alternative,hinted,terms)
        return alternative

    def _personal_word_reading(self,audio,rate,online):
        """Separate evidence, not another acoustic family or automatic alias."""
        memory=getattr(self,'word_memory',None)
        if memory is None or not self.backend.startswith('Qwen3'):return
        from acoustic_context import spelling_intent
        if spelling_intent(online or ''):return
        match=memory.match(audio,rate)
        if not match:return
        # Only the language is supplied. Never prompt with the remembered word
        # and then count that prompted output as independent word evidence.
        stream=self.recognizer.create_stream();stream.set_option('language','English')
        stream.accept_waveform(rate,audio);self.recognizer.decode_stream(stream)
        reading=re.sub(r'<\|[^|]*\|>','',stream.result.text).strip()
        from personal_word_memory import word
        target=word(reading)
        if target and target.casefold()==match['term'].casefold():
            self.last_personal_word=dict(match,reading=reading)

    def _short_english_reading(self,audio,rate,online,primary):
        """Resolve language ambiguity separately; retries cannot add votes."""
        if getattr(self,'english',None) is None or not self.backend.startswith('Qwen3'):return False
        from english_short_word import needs_check,evidence
        support=self.last_support;whisper=self.last_unprompted_multilingual
        seconds=len(audio)/rate
        if not needs_check(online,primary,support,whisper,seconds):return False
        stream=self.recognizer.create_stream();stream.set_option('language','English')
        stream.accept_waveform(rate,audio);self.recognizer.decode_stream(stream)
        qwen=re.sub(r'<\|[^|]*\|>','',stream.result.text).strip()
        english=self.last_english_reading or self.english.transcribe(audio,rate)
        # Keep original family fields unchanged. The extra reading is not
        # evidence for unrelated repair paths or for command recognition.
        self.last_short_english=evidence(online,primary,support,whisper,qwen,english,seconds)
        return True

    def _finish_empty_english(self,audio,rate,online,primary):
        """Fill an absent live draft; a language hint is not another ASR vote."""
        if (online or getattr(self,'english',None) is None or not getattr(self,'backend','').startswith('Qwen3')
                or getattr(self,'last_personal_word',None) or not .2<=len(audio)/rate<=6):return primary
        from empty_english import word,evidence,eligible_primary
        support=self.last_unprompted_support;whisper=self.last_unprompted_multilingual
        if (not word(whisper) or not eligible_primary(primary,support,whisper,len(audio)/rate,
                                                     self.context.get('terms',()))):return primary
        started=time.monotonic()
        try:
            english=self.last_english_reading or self.english.transcribe(audio,rate)
            if word(english)!=word(whisper):return primary
            stream=self.recognizer.create_stream();stream.set_option('language','English')
            stream.accept_waveform(rate,audio);self.recognizer.decode_stream(stream)
            reading=re.sub(r'<\|[^|]*\|>','',stream.result.text).strip()
            proof=evidence(reading,support,whisper,english)
            if proof is None:return primary
            self.last_empty_english=dict(proof,primary=primary)
            self.last_support=support
            self.last_alternatives=[whisper]
            self.last_multilingual_info=dict(self.last_unprompted_multilingual_info)
            self.last_english_reading=english
            self.last_short_english=None
            self.last_primary_recovered=True
            return reading
        except Exception as exc:
            self.empty_english_error=type(exc).__name__
            return primary
        finally:self.last_timings['empty_english']=time.monotonic()-started

    def transcribe(self,audio,sample_rate,online):
        self.last_personal_word=None
        self.last_short_english=None
        self.short_english_error=None
        self.last_unprompted_multilingual=None
        self.last_unprompted_multilingual_info={}
        self.last_unprompted_primary=None
        self.last_unprompted_support=None
        self.last_empty_english=None
        self.empty_english_error=None
        self.last_support=None
        self.last_alternatives=[]
        self.last_context_terms=()
        self.last_domain_used=False
        self.last_timings={}
        self.last_primary_error=None
        self.last_primary_recovered=False
        self.last_english_reading=None
        self.last_multilingual_info={}
        self.last_language_retry=False
        self.language_retry_error=None
        multilingual_reading=None
        if not .2<=len(audio)/sample_rate<=32:return None
        if self.quiet_gain:
            from audio_gain import normalize_utterance
            audio=normalize_utterance(audio)
        context=dict(self.context)
        def timed(function,*args,**kwargs):
            start=time.monotonic();value=function(*args,**kwargs)
            return value,time.monotonic()-start
        # Each independent model owns its own recognizer. Only readings of this
        # single utterance overlap; emitted events and utterances remain ordered.
        # Exiting the executor waits even on failure, so an old GPU job cannot
        # race the next utterance or a change in recognition mode.
        with ThreadPoolExecutor(max_workers=2,thread_name_prefix='acoustic-reading') as pool:
            futures={}
            if self.parallel_refinement:
                if self.support is not None:
                    futures['support']=pool.submit(timed,self.support.transcribe,audio,sample_rate,online)
                if self.multilingual is not None:
                    futures['multilingual']=pool.submit(timed,self._multilingual_reading,audio,sample_rate,online,context)
            primary_error=None
            primary_started=time.monotonic()
            try:
                result,seconds=timed(self._primary_reading,audio,sample_rate,online,context)
            except Exception as exc:
                result=None;primary_error=exc
                self.last_primary_error=type(exc).__name__
            self.last_timings['primary']=time.monotonic()-primary_started
            if self.support is not None:
                try:
                    self.last_support,seconds=(futures['support'].result() if 'support' in futures else
                        timed(self.support.transcribe,audio,sample_rate,online))
                    self.last_timings['support']=seconds
                    self.last_unprompted_support=self.last_support
                except Exception:pass
            if self.multilingual is not None:
                try:
                    alternative,seconds=(futures['multilingual'].result() if 'multilingual' in futures else
                        timed(self._multilingual_reading,audio,sample_rate,online,context))
                    self.last_timings['multilingual']=seconds
                    self.last_multilingual_info=dict(getattr(self.multilingual,'last_info',{}))
                    if alternative:
                        self.last_alternatives.append(alternative)
                        multilingual_reading=alternative
                except Exception as exc:
                    self.multilingual_error=type(exc).__name__;self.multilingual=None
            # Compare only the original reads. Hotword retries from two models
            # are still prompted evidence, not two independent confirmations.
            raw_primary=self.last_unprompted_primary
            raw_whisper=self.last_unprompted_multilingual
            result=preserve_unprompted_reading(raw_primary,result,[self.last_support,raw_whisper])
            if self.last_alternatives:
                hinted_whisper=self.last_alternatives[0]
                self.last_alternatives[0]=preserve_unprompted_reading(
                    raw_whisper,hinted_whisper,[raw_primary,self.last_support])
                if self.last_alternatives[0]!=hinted_whisper:
                    self.last_multilingual_info=dict(self.last_unprompted_multilingual_info)
                multilingual_reading=self.last_alternatives[0]
            if not result:
                # The primary can be empty/failed while its independent jobs
                # succeed. Promote one only when the other agrees completely.
                # They remain two families, not a fabricated three-way vote.
                other=self.last_alternatives[0] if self.last_alternatives else None
                result=agreeing_backup_reading(self.last_support,other,len(audio)/sample_rate)
                self.last_alternatives=[]
                self.last_support=other if result else None
                self.last_primary_recovered=bool(result)
                result=self._finish_empty_english(audio,sample_rate,online,result)
                if not result and primary_error is not None:raise primary_error
                return result
            if self.domain is not None:
                from firered_recognition import needs_domain_check
                if needs_domain_check(result,[self.last_support]+self.last_alternatives,
                                      context.get('terms',()),len(audio)/sample_rate):
                    try:
                        extra,seconds=timed(self.domain.transcribe,audio,sample_rate,online)
                        self.last_timings['domain']=seconds;self.last_domain_used=True
                        if extra:self.last_alternatives.append(extra)
                    except Exception as exc:
                        self.domain_error=type(exc).__name__;self.domain=None
            if getattr(self,'english',None) is not None:
                from english_recognition import needs_english_reading
                if needs_english_reading(online,result,self.last_support,self.last_alternatives):
                    try:
                        self.last_english_reading,seconds=timed(self.english.transcribe,audio,sample_rate)
                        self.last_timings['english']=seconds
                    except Exception as exc:
                        self.english_error=type(exc).__name__;self.english=None
            from english_language import needs_english_retry
            if (self.multilingual is not None and multilingual_reading is not None
                    and self.last_alternatives and self.last_alternatives[0]==multilingual_reading
                    and needs_english_retry(online,result,self.last_support,self.last_multilingual_info.get('language'))):
                try:
                    retry,seconds=timed(self.multilingual.transcribe,audio,sample_rate,online,language='en')
                    self.last_timings['language_retry']=seconds
                    if retry:
                        # This replaces the old Whisper reading. Both passes
                        # belong to one family and cannot vote twice.
                        self.last_alternatives[0]=retry
                        self.last_language_retry=True
                except Exception as exc:
                    self.language_retry_error=type(exc).__name__
        try:
            checked,seconds=timed(self._short_english_reading,audio,sample_rate,online,result)
            if checked:self.last_timings['short_english']=seconds
        except Exception as exc:self.short_english_error=type(exc).__name__
        try:
            _,seconds=timed(self._personal_word_reading,audio,sample_rate,online)
            if self.last_personal_word:self.last_timings['personal_word']=seconds
        except Exception as exc:self.personal_word_error=type(exc).__name__
        return self._finish_empty_english(audio,sample_rate,online,result)
