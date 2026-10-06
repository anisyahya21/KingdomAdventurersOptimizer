import json,pickle,collections,hashlib
from pathlib import Path
import benchmark_battle_binary as bench
import strategy_battle_binary as binary
out=bench.OUT; report=json.loads((out/'report.json').read_text()); evidence=Path(report['evidenceDirectory'])
groups={}; before=[]
for shard in report['corpus']['shards']:
    metrics=json.loads((evidence/(shard['file']+'.metrics.json')).read_text())['rows']; before.extend(m['binary'] for m in metrics)
    selected={m['rowid']:m for m in metrics if m['escape']}
    if not selected: continue
    records=pickle.loads((out/shard['file']).read_bytes())
    for record in records:
        rowid=record['columns']['rowid']
        if rowid not in selected: continue
        value,_,_=bench.unpack(record); key=binary.shape_key(value)
        group=groups.setdefault(key,dict(count=0,tables=collections.Counter(),representative=value,rows=[]))
        group['count']+=1; group['tables'][record['table']]+=1; group['rows'].append((shard['file'],rowid))
ranked=sorted(groups.items(),key=lambda x:(-x[1]['count'],x[0])); cumulative=0; layouts=[]
for key,g in ranked:
    cumulative+=g['count']; layouts.append(dict(fingerprint=key,count=g['count'],tables=dict(g['tables']),cumulativeCount=cumulative,cumulativePercent=100*cumulative/681,shape=binary.shape(g['representative'])))
result=dict(escapes=sum(g['count'] for g in groups.values()),distinctLayouts=len(groups),layouts=layouts,before=bench.summary(before),baselineEvidence=str(evidence))
(out/'escape-audit.json').write_text(json.dumps(result,indent=2))
(out/'escape-audit-representatives.pickle').write_bytes(pickle.dumps(ranked))
print(json.dumps({**{k:v for k,v in result.items() if k!='layouts'},'layouts':[{k:v for k,v in x.items() if k!='shape'} for x in layouts[:3]]},indent=2))
