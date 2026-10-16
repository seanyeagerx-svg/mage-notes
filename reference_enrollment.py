"""Build a voice reference asynchronously from edited clips that contain one voice.

Human edits supply the provenance; the separator rejects overlapping references.
The loudest source is never selected for enrollment. Ambiguity leaves focus off.
"""
from collections import OrderedDict
import hashlib
import threading
import time
import numpy as np
from separation_client import SeparationClient
from voice_identity import VoiceReference,wav_audio


def single_voice(outputs):
    if not isinstance(outputs,(list,tuple)) or len(outputs)!=2:return False
    values=[np.asarray(value,dtype=np.float32) for value in outputs]
    if (any(value.ndim!=1 or not len(value) or not np.isfinite(value).all() for value in values)
            or values[0].shape!=values[1].shape):return False
    power=[float(np.dot(value.astype(np.float64),value.astype(np.float64))) for value in values]
    return max(power)>1e-10 and min(power)/max(power)<=.01


class ReferenceEnrollment:
    def __init__(self,client_factory=SeparationClient,reference_factory=VoiceReference):
        self.client_factory=client_factory
        self.reference_factory=reference_factory
        self.lock=threading.RLock()
        self.closed=False
        self.serial=0
        self.signature=None
        self.reference=VoiceReference()
        self.client=None
        self.building=None
        self.thread=None
        self.error=None
        self.purity=OrderedDict()

    def snapshot(self):
        with self.lock:return self.reference,self.client

    def configure(self,profile,samples):
        pack=[];keys=set();events=set();hashes=set()
        if isinstance(profile,str) and profile and isinstance(samples,(list,tuple)):
            for sample in samples[:8]:
                if (not isinstance(sample,dict) or sample.get('profile')!=profile
                        or sample.get('status')!='human_edit_partial'):
                    continue
                key,event,blob=sample.get('id'),sample.get('event'),sample.get('wav')
                if not isinstance(key,str) or not isinstance(event,str) or not key or not event or not isinstance(blob,bytes):continue
                digest=hashlib.sha256(blob).hexdigest()
                audio=wav_audio(blob)
                if audio is None or key in keys or event in events or digest in hashes:continue
                keys.add(key);events.add(event);hashes.add(digest)
                pack.append((dict(sample),audio,digest))
        signature=(profile,tuple(sorted(hashes)))
        with self.lock:
            if self.closed or signature==self.signature:return
            previous_profile=self.reference.profile
            self.signature=signature;self.serial+=1;serial=self.serial
            reuse=(self.client if previous_profile==profile and len(pack)>=3
                and self.client is not None and self.client.ready.is_set() else None)
            old=[value for value in (self.client,self.building) if value is not None]
            self.client=self.building=None
            self.reference=VoiceReference();self.reference.profile=profile
            self.reference.status='not_enough_distinct_human_clips'
            self.error=None
            if previous_profile!=profile:self.purity.clear()
        for value in set(old):
            if value is not reuse:value.close()
        if len(pack)<3:return
        with self.lock:
            if self.closed or serial!=self.serial:return
            client=reuse or self.client_factory();self.building=client
            self.reference.status='checking_human_clips'
            self.thread=threading.Thread(target=self._build,args=(serial,profile,pack,client),
                name='voice-reference',daemon=True)
            self.thread.start()

    def _current(self,serial):
        with self.lock:return not self.closed and serial==self.serial

    def _build(self,serial,profile,pack,client):
        try:
            client.warmup();deadline=time.monotonic()+42
            while not client.ready.is_set():
                if not self._current(serial):return
                if client.error:raise RuntimeError(client.error)
                if time.monotonic()>=deadline:raise TimeoutError('声音参考检查超时')
                client.ready.wait(.05)
            clean=[]
            for sample,audio,digest in pack:
                if not self._current(serial):return
                with self.lock:pure=self.purity.get(digest)
                if pure is None:
                    outputs=client.separate(audio,16000)
                    if outputs is None:raise RuntimeError(client.error or 'reference_separator_unavailable')
                    pure=single_voice(outputs)
                    with self.lock:
                        if not self._current(serial):return
                        self.purity[digest]=pure
                        while len(self.purity)>64:self.purity.popitem(last=False)
                if pure:clean.append(sample)
            reference=self.reference_factory()
            reference.configure(profile,clean)
            if reference.reference is None and len(clean)<3:
                reference.status='not_enough_single_voice_clips'
            with self.lock:
                if not self._current(serial):return
                self.reference=reference;self.building=None
                if reference.reference is not None:self.client=client
        except Exception as exc:
            with self.lock:
                if self._current(serial):
                    self.error=type(exc).__name__;self.building=None
                    self.reference=VoiceReference();self.reference.profile=profile
                    self.reference.status='reference_check_unavailable'
        finally:
            with self.lock:keep=self._current(serial) and self.client is client
            if not keep:client.close()

    def close(self):
        with self.lock:
            self.closed=True;self.serial+=1
            old=[value for value in (self.client,self.building) if value is not None]
            self.client=self.building=None
            self.reference=VoiceReference();self.purity.clear()
        for value in set(old):value.close()
