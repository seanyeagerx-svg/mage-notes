"""Bounded microphone-level compensation, not denoising or speaker separation."""
from collections import deque
import numpy as np


class QuietGain:
    """Raise quiet input using only the recent signal, without clipping peaks.

    A 640-ms rolling envelope avoids independently stretching each phoneme.
    Normal/loud frames remain unchanged; digital silence is never filled.
    The source recording remains separate and can be retained unchanged.
    """
    def __init__(self,rate=16000):
        self.rate=rate;self.reset()

    def reset(self):
        self.frames=deque();self.count=0;self.energy=0.;self.gain=1.

    def process(self,samples):
        data=np.asarray(samples,dtype=np.float32)
        if not len(data):return data
        energy=float(np.dot(data,data));peak=float(np.max(np.abs(data)))
        self.frames.append((len(data),energy,peak));self.count+=len(data);self.energy+=energy
        while len(self.frames)>1 and self.count-self.frames[0][0]>=self.rate*.64:
            n,e,_=self.frames.popleft();self.count-=n;self.energy-=e
        rms=float(np.sqrt(max(0.,self.energy)/max(self.count,1)))
        current_rms=float(np.sqrt(energy/len(data)))
        recent_peak=max(frame[2] for frame in self.frames)
        if rms<.00005 or rms>=.015 or recent_peak>=.16 or current_rms>=.03:
            desired=1.
        else:
            desired=max(1.,min(24.,.06/rms,.94/max(recent_peak,1e-8)))
        # React quickly to speech onset and immediately to loud peaks, gently
        # release afterwards. This changes the level, never the speech spectrum.
        weight=.8 if desired>self.gain else .25
        self.gain=self.gain+(desired-self.gain)*weight
        if current_rms>=.03:self.gain=1.
        self.gain=min(self.gain,.94/max(peak,1e-8))
        return (data*self.gain).astype(np.float32)


def normalize_utterance(samples):
    """An utterance-sized constant gain for an already bounded second pass."""
    data=np.asarray(samples,dtype=np.float32)
    if not len(data):return data
    rms=float(np.sqrt(np.mean(data.astype(np.float64)**2)))
    if not .00005<=rms<.03:return data
    peak=float(np.max(np.abs(data)))
    gain=max(1.,min(24.,.06/rms,.94/max(peak,1e-8)))
    return (data*gain).astype(np.float32)
