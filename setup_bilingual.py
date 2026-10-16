"""Pinned bilingual streaming candidate; adoption requires measured comparison."""
import hashlib
import json
from pathlib import Path
import urllib.request

ROOT=Path(__file__).resolve().parent
REPO='csukuangfj/sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20'
REVISION='98590b7ed6443e77b714204da2757d75e1a642f4'
FILES={'encoder-epoch-99-avg-1.int8.onnx':'8fa764187a261844f859d7143ebaa563af5d10adfece4c18a8f414c88cba2a9b',
       'decoder-epoch-99-avg-1.onnx':'2e3b5ec371f8899ee6acd829fd753ba45772df57a91bdf37cde3136354e7db7d',
       'joiner-epoch-99-avg-1.int8.onnx':'1ed689c5ed19dbaa725d9d191bb4822b5f4855a39e1ffd28cbc1f340d25b2ee0',
       'tokens.txt':None,'README.md':None,'bpe.vocab':None}


def main():
    folder=ROOT/'models'/'streaming-bilingual';folder.mkdir(exist_ok=True)
    records=[]
    for name,expected in FILES.items():
        p=folder/name
        if not p.is_file():
            print('Downloading '+name,flush=True)
            part=p.with_suffix(p.suffix+'.part')
            with urllib.request.urlopen(f'https://huggingface.co/{REPO}/resolve/{REVISION}/{name}',timeout=60) as source,part.open('wb') as target:
                while chunk:=source.read(1024*1024):target.write(chunk)
            actual=hashlib.file_digest(part.open('rb'),'sha256').hexdigest()
            if expected and actual!=expected:raise ValueError('Checksum mismatch '+name)
            part.replace(p)
        actual=hashlib.file_digest(p.open('rb'),'sha256').hexdigest()
        if expected and expected!=actual:raise ValueError('Existing checksum mismatch '+name)
        records.append({'file':name,'bytes':p.stat().st_size,'sha256':actual})
        print('Verified '+name,flush=True)
    (folder/'manifest.json').write_text(json.dumps({'repository':REPO,'revision':REVISION,'files':records},indent=2),'utf-8')


if __name__=='__main__':main()
