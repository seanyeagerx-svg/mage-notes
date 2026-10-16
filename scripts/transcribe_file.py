"""Decode an explicitly selected mono PCM WAV locally, without opening a microphone."""
import argparse
import json
from pathlib import Path
import sys
import wave

ROOT=Path(__file__).resolve().parents[1]

def transcribe(file,model_dir=None):
    import numpy as np
    if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
    import engine
    if model_dir is not None:engine.MODEL=Path(model_dir).expanduser().resolve()
    recognizer=engine.create_recognizer('zh')
    with wave.open(str(file),'rb') as audio:
        if audio.getnchannels()!=1 or audio.getsampwidth()!=2 or audio.getframerate()!=16000:
            raise ValueError('请使用 16 kHz、单声道、16-bit PCM WAV。')
        if audio.getnframes()>16000*600:raise ValueError('本入口一次最多处理10分钟。')
        samples=np.frombuffer(audio.readframes(audio.getnframes()),dtype=np.int16).astype(np.float32)/32768
    stream=recognizer.create_stream()
    for start in range(0,len(samples),1280):
        stream.accept_waveform(16000,samples[start:start+1280])
        while recognizer.is_ready(stream):recognizer.decode_stream(stream)
    stream.accept_waveform(16000,np.zeros(8000,dtype=np.float32))
    stream.input_finished()
    while recognizer.is_ready(stream):recognizer.decode_stream(stream)
    return {'text':recognizer.get_result(stream),'seconds':round(len(samples)/16000,3),
            'source':'selected_file','microphone_opened':False,'cloud_used':False}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('wav',type=Path)
    p.add_argument('--model-dir',type=Path)
    args=p.parse_args()
    print(json.dumps(transcribe(args.wav,args.model_dir),ensure_ascii=False))

if __name__=='__main__':main()
