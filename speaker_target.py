"""Optional second-pass focus; ambiguity or failure always preserves original audio."""
from dataclasses import asdict
import time
import numpy as np
from separation_client import SeparationClient
from separation_protocol import MAX_FRAMES
from speaker_focus import apply
from voice_identity import VoiceReference


class SpeakerTarget:
    def __init__(self,reference=None,client_factory=SeparationClient):
        self.enrollment=None
        self._reference = reference or VoiceReference()
        self.client_factory = client_factory
        self._client = None
        self.error = None
        self.last_decision = dict(action='original',reason='no_human_reference')

    @property
    def reference(self):
        return self.enrollment.snapshot()[0] if self.enrollment else self._reference

    @property
    def client(self):
        return self.enrollment.snapshot()[1] if self.enrollment else self._client

    @client.setter
    def client(self,value):
        self._client=value

    def configure(self,profile,samples):
        try:
            if self.enrollment is None:
                from reference_enrollment import ReferenceEnrollment
                self.enrollment=ReferenceEnrollment(client_factory=self.client_factory)
            self.enrollment.configure(profile,samples)
        except Exception as exc:
            self.error = type(exc).__name__
            if self.enrollment:self.enrollment.close();self.enrollment=None
            self._reference.clear()
            if self._client:self._client.close();self._client=None

    def process(self,audio,rate):
        start = time.monotonic()
        reference,client=(self.enrollment.snapshot() if self.enrollment else (self._reference,self._client))
        self.last_decision = dict(action='original',reason=reference.status,
            reference_available=reference.reference is not None,owner_confirmed=False)
        if self.enrollment:self.error=self.enrollment.error
        if reference.reference is None or client is None: return audio
        if rate!=16000 or not 16000<=len(audio)<=MAX_FRAMES:
            self.last_decision['reason']='unsupported_audio'; return audio
        if not client.ready.is_set():
            self.last_decision['reason']='helper_not_ready'
            self.error = client.error
            return audio
        try:
            outputs = client.separate(audio,rate)
            if outputs is None:
                self.error = client.error
                self.last_decision['reason']='helper_unavailable'; return audio
            scores = [reference.score(value) for value in outputs]
            raw_score = reference.score(audio)
            chosen,decision = apply(audio,outputs,scores,raw_score)
            confirmed=(decision.action=='separated' or (decision.reason=='single_source'
                and raw_score is not None and raw_score>=.65))
            self.last_decision = dict(asdict(decision),seconds=time.monotonic()-start,
                reference_available=True,owner_confirmed=bool(confirmed))
            return chosen
        except Exception as exc:
            self.error = type(exc).__name__
            self.last_decision = dict(action='original',reason='focus_error',seconds=time.monotonic()-start,
                reference_available=True,owner_confirmed=False)
            return audio

    def close(self):
        if self.enrollment:self.enrollment.close();self.enrollment=None
        self._reference.clear()
        if self._client:self._client.close();self._client=None
