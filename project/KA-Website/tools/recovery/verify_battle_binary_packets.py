"""Decode-only process: inputs are packed packets and definitions, never source corpus."""
import argparse
import hashlib
import json
from pathlib import Path
import pickle
import strategy_battle_binary as binary

def semantic_bytes(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()

def verify(definitions,directory):
    decoder=binary.RecordEncoder(json.loads(Path(definitions).read_text()))
    digest=hashlib.sha256(); count=0
    for path in sorted(Path(directory).glob('*.pickle.packed')):
        for packet,metadata in pickle.loads(path.read_bytes()):
            value=semantic_bytes({'decoded':decoder.decode(packet),'metadata':decoder.decode(metadata)})
            digest.update(len(value).to_bytes(4,'little')); digest.update(value); count+=1
    return dict(rows=count,semanticSha256=digest.hexdigest(),inputScope='Only packets and immutable definitions; no source JSON or database opened')

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('definitions'); parser.add_argument('packets'); parser.add_argument('output')
    args=parser.parse_args()
    Path(args.output).write_text(json.dumps(verify(args.definitions,args.packets),indent=2))
