"""Independent decode-only verification of a saved complete SQLite artifact."""
import hashlib,json,sqlite3,zlib,time,sys
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor
import strategy_battle_binary as binary
from verify_battle_binary_packets import semantic_bytes
from measure_battle_binary_complete_files import atomic_json
DECODER=None
def init(definitions):
    global DECODER
    DECODER=binary.RecordDecoder(definitions)
def decode_chunk(rows):
    result=[]
    for packet,metadata in rows:
        value=semantic_bytes({'decoded':DECODER.decode(packet),'metadata':json.loads(metadata)})
        result.append(len(value).to_bytes(4,'little')+value)
    return b''.join(result),len(rows)
def chunks(cursor):
    while batch:=cursor.fetchmany(250):yield batch
def run():
    root=Path(__file__).resolve().parent/'studies/battle_binary_corpus';out=root/'complete-file-audit';path=out/'binary-after.sqlite'
    db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True);db.execute('PRAGMA query_only=ON')
    stored,expected_definition=db.execute('SELECT zlib,raw_sha256 FROM definitions').fetchone(); raw=zlib.decompress(stored)
    assert hashlib.sha256(raw).hexdigest()==expected_definition
    total=db.execute('SELECT count(*) FROM records').fetchone()[0];start=time.monotonic();done=0;digest=hashlib.sha256()
    def status(error=None):
        elapsed=time.monotonic()-start;rate=done/elapsed if done else None
        atomic_json(out/'status.json',dict(stage='Independent decoder: saved SQLite packets and embedded definitions only',completed=done,total=total,elapsed=elapsed,rate=rate,eta=(total-done)/rate if rate else None,error=error))
    status()
    try:
        cursor=db.execute('SELECT payload,metadata FROM records ORDER BY table_name,rowid')
        with ProcessPoolExecutor(max_workers=4,initializer=init,initargs=(json.loads(raw),)) as pool:
            for data,count in pool.map(decode_chunk,chunks(cursor),buffersize=8):
                digest.update(data);done+=count;status()
        # The reference is a saved source semantic digest, not a decoder input.
        expected=json.loads((root/'report.json').read_text())['sourceHashVerification']['expectedSemanticSha256']
        result=dict(rows=done,semanticSha256=digest.hexdigest(),expectedSourceSemanticSha256=expected,exactMatch=digest.hexdigest()==expected,definitionSha256=expected_definition,seconds=time.monotonic()-start,workers=4,decoderInputs='Saved binary-after.sqlite packets, metadata and embedded compressed definitions; no source records or source database')
        atomic_json(out/'independent-decoder.json',result)
        assert done==total==50000 and result['exactMatch'];print(json.dumps(result))
    except Exception as exc:status(str(exc));raise
    finally:db.close()
if __name__=='__main__':run()
