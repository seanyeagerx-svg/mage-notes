"""Read the selected voice with the existing fast model, keeping emitted text owned.

This is a replacement reading from the same model family, never an extra vote.
The caller retains the original emitted span and original command hypothesis.
"""
import numpy as np
from audio_gain import normalize_utterance


def read_selected_voice(recognizer,audio,rate):
    values=np.asarray(audio,dtype=np.float32)
    if (rate!=16000 or values.ndim!=1 or not 16000<=len(values)<=16000*15
            or not np.isfinite(values).all()):return None
    normalized=normalize_utterance(values)
    stream=recognizer.create_stream()
    for start in range(0,len(values),1280):
        part=normalized[start:start+1280]
        if hasattr(stream,'accept_original_waveform'):
            stream.accept_original_waveform(rate,part,values[start:start+1280])
        else:stream.accept_waveform(rate,part)
        while recognizer.is_ready(stream):recognizer.decode_stream(stream)
    stream.accept_waveform(rate,np.zeros(8000,np.float32))
    stream.input_finished()
    while recognizer.is_ready(stream):recognizer.decode_stream(stream)
    text=recognizer.get_result(stream)
    if not text or len(text)>min(320,max(40,int(len(values)/rate*24))):return None
    return text
