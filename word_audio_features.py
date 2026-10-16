"""Bounded short-word acoustic features. A match is a hint, never text truth."""
from functools import lru_cache
import numpy as np


@lru_cache(maxsize=1)
def matrices():
    mel=lambda hz:2595*np.log10(1+hz/700)
    hz=lambda m:700*(10**(m/2595)-1)
    edges=hz(np.linspace(mel(80),mel(7600),42));frequencies=np.fft.rfftfreq(512,1/16000)
    bank=np.asarray([np.maximum(0,np.minimum((frequencies-a)/(b-a),(c-frequencies)/(c-b)))
                     for a,b,c in zip(edges,edges[1:],edges[2:])],dtype=np.float32)
    dct=np.cos(np.pi*np.arange(1,14)[:,None]*(np.arange(40)+.5)/40).astype(np.float32)
    return bank,dct


def features(audio,rate=16000):
    values=np.asarray(audio,dtype=np.float32).reshape(-1)
    if rate!=16000 or not .18<=len(values)/rate<=3 or not np.isfinite(values).all():return None
    values=values-np.mean(values)
    rms=float(np.sqrt(np.mean(values*values)))
    if rms<1e-5:return None
    values=values/rms
    frames=np.lib.stride_tricks.sliding_window_view(values,400)[::160].copy()
    energies=np.sqrt(np.mean(frames*frames,axis=1))
    active=np.flatnonzero(energies>max(.15,float(np.max(energies))*.09))
    if len(active)<8:return None
    start,end=max(0,int(active[0])-2),min(len(frames),int(active[-1])+3)
    frames=frames[start:end]
    frames[:,1:]-=.97*frames[:,:-1].copy()
    power=np.abs(np.fft.rfft(frames*np.hamming(400),512))**2
    bank,dct=matrices();logs=np.log(np.maximum(power@bank.T,1e-5))
    cepstra=logs@dct.T
    cepstra=cepstra/(np.linalg.norm(cepstra,axis=1,keepdims=True)+1e-8)
    return np.asarray(cepstra,dtype=np.float32)


def distance(a,b):
    if a is None or b is None or max(len(a),len(b))/min(len(a),len(b))>2:return float('inf')
    costs=np.maximum(0,1-a@b.T)
    n,m=costs.shape;dp=np.full((n+1,m+1),np.inf);steps=np.zeros((n+1,m+1),np.int16);dp[0,0]=0
    for i in range(1,n+1):
        center=i*m/n
        for j in range(max(1,int(center-m*.3)),min(m+1,int(center+m*.3)+1)):
            candidates=(dp[i-1,j-1],dp[i-1,j]+.03,dp[i,j-1]+.03)
            choice=int(np.argmin(candidates));prev=((i-1,j-1),(i-1,j),(i,j-1))[choice]
            dp[i,j]=candidates[choice]+costs[i-1,j-1];steps[i,j]=steps[prev]+1
    return float(dp[n,m]/max(1,steps[n,m]))
