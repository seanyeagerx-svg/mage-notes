"""Conservative, local reference derived from distinct human-edited clips.

No enrollment is inferred from ordinary ASR output or the loudest voice. An
ambiguous profile produces no reference. Embeddings stay in RAM and are not
shared between personal profiles.
"""
import hashlib
import io
import itertools
from pathlib import Path
import wave
import numpy as np

MODEL = Path(__file__).resolve().parent/'models/speaker-focus/campplus.onnx'


def samples_from_memory(memory,profile):
    """Return bounded evidence already saved by actual human corrections."""
    if getattr(memory,'error',None) or not isinstance(profile,str): return ()
    rows = memory.db.execute('''SELECT id,profile,event,status,wav FROM samples
        WHERE profile=? AND status='human_edit_partial' AND edited<>displayed
        AND LENGTH(wav) BETWEEN 64000 AND 480512 ORDER BY created DESC LIMIT 24''',(profile,)).fetchall()
    result=[];events=set();hashes=set()
    for key,person,event,status,blob in rows:
        digest=hashlib.sha256(blob).hexdigest()
        if event in events or digest in hashes: continue
        events.add(event);hashes.add(digest)
        result.append(dict(id=key,profile=person,event=event,status=status,wav=blob))
        if len(result)==8:break
    return tuple(result)


def normalize(vector):
    value = np.asarray(vector,dtype=np.float32)
    if value.ndim != 1 or not len(value) or not np.isfinite(value).all(): return None
    length = float(np.linalg.norm(value))
    return value/length if length > 1e-8 else None


def consensus(vectors, minimum=.75):
    """Reject competing clusters; a repeated clip cannot supply separate votes."""
    if not 3 <= len(vectors) <= 8: return None
    values = [normalize(v) for v in vectors]
    if any(v is None for v in values) or len({v.shape for v in values}) != 1: return None
    matches = np.asarray(values) @ np.asarray(values).T
    candidates = []
    for count in range(len(values),2,-1):
        for group in itertools.combinations(range(len(values)),count):
            if all(matches[a,b] >= minimum for a,b in itertools.combinations(group,2)):
                candidates.append(group)
        if candidates: break
    if len(candidates) != 1: return None
    group = candidates[0]
    if len(group)*3 < len(values)*2: return None
    other = [i for i in range(len(values)) if i not in group]
    if any(matches[a,b] >= minimum for a,b in itertools.combinations(other,2)):
        return None
    return normalize(np.mean([values[i] for i in group],axis=0))


class SpeakerEmbedding:
    def __init__(self,model=MODEL):
        import sherpa_onnx
        config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(model),num_threads=2,provider='cpu')
        self.extractor = sherpa_onnx.SpeakerEmbeddingExtractor(config)

    def __call__(self,audio):
        values = np.asarray(audio,dtype=np.float32)
        if values.ndim!=1 or not 16000 <= len(values) <= 16000*32 or not np.isfinite(values).all():
            return None
        rms = float(np.sqrt(np.mean(values.astype(np.float64)**2)))
        peak = float(np.max(np.abs(values)))
        if rms < 1e-5 or peak < 1e-5: return None
        values = values * min(.08/rms,.9/peak)
        stream = self.extractor.create_stream()
        stream.accept_waveform(16000,values)
        stream.input_finished()
        if not self.extractor.is_ready(stream): return None
        return normalize(self.extractor.compute(stream))


def wav_audio(blob):
    if not isinstance(blob,bytes) or not 44 < len(blob) <= 16000*2*15+512: return None
    try:
        with wave.open(io.BytesIO(blob),'rb') as handle:
            if (handle.getnchannels()!=1 or handle.getsampwidth()!=2 or handle.getframerate()!=16000
                    or not 16000*2 <= handle.getnframes() <= 16000*15): return None
            audio = np.frombuffer(handle.readframes(handle.getnframes()),'<i2').astype(np.float32)/32768
        if len(audio)<32000 or np.mean(np.abs(audio)>=.995)>.001: return None
        return audio
    except (wave.Error,EOFError,ValueError): return None


class VoiceReference:
    def __init__(self,embed=None):
        self.embed = embed
        self.profile = None
        self.reference = None
        self.signature = None
        self.status = 'no_human_reference'
        self.supporting_clips = 0

    def clear(self):
        self.profile = self.reference = self.signature = None
        self.supporting_clips = 0
        self.status = 'no_human_reference'

    def configure(self,profile,samples):
        if not isinstance(profile,str) or not profile or not isinstance(samples,(list,tuple)):
            self.clear(); return
        # Never retain the former person's identity when the new pack is empty.
        if profile != self.profile:
            self.clear(); self.profile = profile
        selected = []
        ids, events, hashes = set(),set(),set()
        for sample in samples[:8]:
            if (not isinstance(sample,dict) or sample.get('profile')!=profile
                    or sample.get('status')!='human_edit_partial'):
                continue
            key,event,blob = sample.get('id'),sample.get('event'),sample.get('wav')
            if not isinstance(key,str) or not isinstance(event,str) or not key or not event or not isinstance(blob,bytes):
                continue
            digest = hashlib.sha256(blob).hexdigest()
            if key in ids or event in events or digest in hashes: continue
            values = wav_audio(blob)
            if values is None: continue
            ids.add(key); events.add(event); hashes.add(digest)
            selected.append((digest,values))
        signature = (profile,tuple(item[0] for item in selected))
        if signature == self.signature: return
        self.signature = signature
        self.reference = None
        self.supporting_clips = 0
        self.status = 'not_enough_distinct_human_clips'
        if len(selected)<3: return
        if self.embed is None: self.embed = SpeakerEmbedding()
        vectors = []
        for _,audio in selected:
            full = self.embed(audio)
            half = len(audio)//2
            first,second = self.embed(audio[:half]),self.embed(audio[half:])
            # A change of voice inside a reference is not reliable enrollment.
            if (full is None or first is None or second is None
                    or float(np.dot(first,second)) < .55):
                continue
            vectors.append(full)
        self.reference = consensus(vectors)
        self.supporting_clips = len(vectors) if self.reference is not None else 0
        self.status = 'ready' if self.reference is not None else 'uncertain_human_reference'

    def score(self,audio):
        if self.reference is None: return None
        vector = self.embed(audio)
        return float(np.dot(self.reference,vector)) if vector is not None else None
