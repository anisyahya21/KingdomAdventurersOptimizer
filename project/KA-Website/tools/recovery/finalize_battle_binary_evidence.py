"""Compact evidence review: independent decoder and stratum reweighting, no DB writes."""
import hashlib
import json
from pathlib import Path
import pickle
import subprocess
import sys
import benchmark_battle_binary as bench
from verify_battle_binary_packets import semantic_bytes

def run():
    out=bench.OUT; path=out/'report.json'; report=json.loads(path.read_text())
    evidence=Path(report['evidenceDirectory'])
    child=subprocess.Popen([sys.executable,str(Path(__file__).with_name('verify_battle_binary_packets.py')),
        str(out/'definitions.json'),str(evidence),str(out/'separate-decoder.json')])
    expected=hashlib.sha256(); count=0; rows=[]
    for shard in sorted(report['corpus']['shards'],key=lambda s:s['file']):
        for record in pickle.loads((out/shard['file']).read_bytes()):
            value,meta,_=bench.unpack(record)
            raw=semantic_bytes({'decoded':value,'metadata':meta})
            expected.update(len(raw).to_bytes(4,'little')); expected.update(raw); count+=1
        rows.extend(json.loads((evidence/(shard['file']+'.metrics.json')).read_text())['rows'])
    if child.wait()!=0: raise RuntimeError('independent decoder failed')
    independent=json.loads((out/'separate-decoder.json').read_text())
    assert independent['rows']==count==50000 and independent['semanticSha256']==expected.hexdigest()
    report['parity']['separateProcessWithoutSource']=independent
    populations={table:r['population'] for table,r in report['corpus']['tables'].items()}
    selections={table:r['selected'] for table,r in report['corpus']['tables'].items()}
    weighted={}
    for key in ('raw','current','binary','metadata'):
        pairs=sorted((row[key],populations[row['table']]/selections[row['table']]) for row in rows)
        total_weight=sum(w for _,w in pairs); cumulative=0; quantiles={}; targets=iter((.5,.9,.95,.99)); target=next(targets,None)
        for value,weight in pairs:
            cumulative+=weight
            while target is not None and cumulative>=target*total_weight:
                quantiles[f'p{int(target*100)}']=value; target=next(targets,None)
        weighted[key]=dict(mean=sum(value*weight for value,weight in pairs)/total_weight,**quantiles)
    report['populationReweightedSampleEstimates']=dict(method='Source table-count weights applied to equal table samples; estimates only, not an unbiased random census.',distributions=weighted)
    report['sourceHashVerification']=dict(expectedSemanticSha256=expected.hexdigest(),separateDecodedSemanticSha256=independent['semanticSha256'],exactMatch=True)
    path.write_text(json.dumps(report,indent=2))
    print(json.dumps(dict(separateProcess=independent,weighted=weighted),indent=2))

if __name__=='__main__': run()
