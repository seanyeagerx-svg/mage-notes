"""Use repeated human short-word edits to request a language-only re-listen.

The saved word is never supplied to the acoustic decoder. A match alone cannot
change text. This does not train a general ASR model or establish voice identity.
"""
import hashlib
import io
import re
import wave
import numpy as np
from word_audio_features import features,distance
from english_orthography import QUANTITY

WORD=re.compile(r'[A-Za-z]{2,24}[。.!?？！]?')
SENSITIVE=set(('no not never without cannot yes send cancel ignore execute buy sell open close start stop enable disable delete keep '
    'up down left right forward backward zero one two three four five six seven eight nine ten hundred thousand').split())


def word(value):
    if not isinstance(value,str) or not WORD.fullmatch(value.strip()):return None
    term=value.strip().rstrip('。.!?？！')
    return term if term.casefold() not in SENSITIVE and not QUANTITY.fullmatch(term) else None


def wav_audio(blob):
    if not isinstance(blob,bytes) or not 6400<=len(blob)<=96512:return None
    try:
        with wave.open(io.BytesIO(blob),'rb') as wav:
            if (wav.getframerate(),wav.getnchannels(),wav.getsampwidth())!=(16000,1,2):return None
            if not 3200<=wav.getnframes()<=48000:return None
            values=np.frombuffer(wav.readframes(wav.getnframes()),'<i2').astype(np.float32)/32768
        if len(values)<3200 or np.mean(np.abs(values)>=.995)>.001:return None
        return values
    except (wave.Error,EOFError,ValueError):return None


def samples_from_memory(memory,profile):
    if getattr(memory,'error',None) or not isinstance(profile,str):return ()
    rows=memory.db.execute('''SELECT id,profile,event,status,wav,edited,created FROM samples
        WHERE profile=? AND status='human_edit_partial' AND edited<>displayed
        AND LENGTH(wav) BETWEEN 6400 AND 96512 ORDER BY created DESC LIMIT 192''',(profile,)).fetchall()
    blocks=dict(memory.db.execute('SELECT term,created FROM word_blocks WHERE profile=?',(profile,)).fetchall())
    result=[];events=set();hashes=set();counts={}
    for key,person,event,status,blob,edited,created in rows:
        term=word(edited)
        if not term or created<=blocks.get(term.casefold(),0):continue
        folded=term.casefold();digest=hashlib.sha256(blob).hexdigest()
        if event in events or digest in hashes or counts.get(folded,0)>=2:continue
        if folded not in counts and len(counts)>=24:continue
        events.add(event);hashes.add(digest);counts[folded]=counts.get(folded,0)+1
        result.append(dict(id=key,profile=person,event=event,status=status,wav=blob,term=term))
    return tuple(s for s in result if counts[s['term'].casefold()]==2)


class PersonalWordMemory:
    def __init__(self):
        self.profile=None;self.signature=None;self.references={};self.status='no_human_word_pairs'

    def configure(self,profile,samples):
        self.profile=profile if isinstance(profile,str) and profile else None
        if not self.profile or not isinstance(samples,(tuple,list)):
            self.references={};self.signature=None;self.status='no_human_word_pairs';return
        selected=[];ids=set();events=set();hashes=set()
        for sample in samples[:48]:
            if not isinstance(sample,dict) or sample.get('profile')!=profile or sample.get('status')!='human_edit_partial':continue
            key,event,blob,term=sample.get('id'),sample.get('event'),sample.get('wav'),word(sample.get('term'))
            if not isinstance(key,str) or not key or not isinstance(event,str) or not event or not term or not isinstance(blob,bytes):continue
            digest=hashlib.sha256(blob).hexdigest()
            if key in ids or event in events or digest in hashes:continue
            ids.add(key);events.add(event);hashes.add(digest);selected.append((digest,blob,term))
        signature=(profile,tuple((h,t) for h,_,t in selected))
        if signature==self.signature:return
        self.signature=signature;self.references={};grouped={}
        for digest,blob,term in selected:
            raw=wav_audio(blob)
            feat=features(raw) if raw is not None else None
            if feat is not None:grouped.setdefault(term.casefold(),[]).append((term,digest,feat))
        for folded,items in grouped.items():
            if len(items)!=2:continue
            spread=distance(items[0][2],items[1][2])
            if np.isfinite(spread) and spread<=.30:
                self.references[folded]=dict(term=items[0][0],hashes=tuple(x[1] for x in items),
                    features=tuple(x[2] for x in items),spread=spread)
        self.status='ready' if self.references else 'no_consistent_human_word_pairs'

    def match(self,audio,rate=16000):
        if not self.references or rate<=0 or not .2<=len(audio)/rate<=3:return None
        if rate!=16000:
            import soxr
            audio=soxr.resample(audio,rate,16000)
        feat=features(audio);scores=[]
        if feat is None:return None
        for key,ref in self.references.items():
            ds=[distance(feat,f) for f in ref['features']]
            scores.append((float(np.mean(ds)),max(ds),key))
        scores.sort();score,maximum,key=scores[0];ref=self.references[key]
        if (not np.isfinite(score) or score>min(.30,max(.10,ref['spread']*1.10))
                or maximum>min(.38,max(.14,ref['spread']*1.35))
                or (len(scores)>1 and scores[1][0]-score<.12)):return None
        return dict(profile=self.profile,term=ref['term'],reference_hashes=ref['hashes'],
            score=score,maximum=maximum,spread=ref['spread'],method='two_human_clips_and_language_only')


def repair(source,evidence,protected=()):
    """Whole short-word repair only; every output still uses edit ownership."""
    from acoustic_context import spelling_intent
    from personalization import PROTECTED,QUOTED
    if not isinstance(evidence,dict) or evidence.get('method')!='two_human_clips_and_language_only':return None
    target=word(evidence.get('term'));reading=word(evidence.get('reading'))
    hashes=evidence.get('reference_hashes',())
    if (not target or not reading or target.casefold()!=reading.casefold()
            or not isinstance(hashes,(list,tuple)) or len(hashes)!=2 or hashes[0]==hashes[1]
            or any(not isinstance(h,str) or not re.fullmatch('[0-9a-f]{64}',h) for h in hashes)
            or not isinstance(evidence.get('profile'),str) or not evidence['profile']):return None
    if (not isinstance(source,str) or spelling_intent(source) or PROTECTED.search(source)
            or QUANTITY.search(source) or re.fullmatch(r'[嗯呃啊哦噢唉哎]+[。.!?？！]?',source.strip())
            or QUOTED.search(source) or not re.fullmatch(r'(?:[A-Za-z]{2,24}|[\u3400-\u9fff]{1,8})[。.!?？！]?',source.strip())):return None
    plain=source.strip().rstrip('。.!?？！')
    if plain.casefold() in SENSITIVE or plain.casefold()==target.casefold():return None
    if any(isinstance(t,str) and t and t in source and t not in target for t in protected):return None
    return target
