import json,pickle,hashlib
from pathlib import Path
import benchmark_battle_binary as bench
import strategy_battle_binary as b
out=bench.OUT; audit=json.loads((out/'escape-audit.json').read_text()); original=json.loads((Path(audit['baselineEvidence'])/'definitions.json').read_text()); candidate=json.loads((out/'escape-audit-candidate-definitions.json').read_text()); enc=b.RecordEncoder(candidate)
ranked=pickle.loads((out/'escape-audit-representatives.pickle').read_bytes()); refs={(file,rowid):key for key,g in ranked for file,rowid in g['rows']}; sizes=[]; affected=0; escapes=0
for shard in json.loads((out/'manifest.json').read_text())['shards']:
    file=shard['file']; metrics=json.loads((Path(audit['baselineEvidence'])/(file+'.metrics.json')).read_text())['rows']; selected={m['rowid']:m for m in metrics if m['escape']}
    replacements={}
    if selected:
        for record in pickle.loads((out/file).read_bytes()):
            rowid=record['columns']['rowid']
            if rowid in selected:
                value,_,_=bench.unpack(record); packet=enc.encode(value); b.exact(value,enc.decode(packet)); replacements[rowid]=len(packet); escapes+=packet[12:14]==b'\xff\xff'; affected+=1
    sizes.extend(replacements.get(m['rowid'],m['binary']) for m in metrics)
audit.update(after=bench.summary(sizes),afterEscapes=escapes,affectedExactTypedChecks=affected,addedLayouts=[key for key,_ in ranked[:3]],definitions=dict(beforeHash=hashlib.sha256(b.dumps(original).encode()).hexdigest(),afterHash=hashlib.sha256(b.dumps(candidate).encode()).hexdigest(),beforeRaw=len(b.dumps(original).encode()),afterRaw=len(b.dumps(candidate).encode()),dictionaryEntries=len(candidate['dictionary']),dictionaryUnchanged=candidate['dictionary']==original['dictionary']),scope='Optimization on inspected stratified corpus; this corpus is no longer untouched holdout evidence. Existing fallback retained.')
(out/'escape-audit.json').write_text(json.dumps(audit,indent=2))
print(json.dumps({k:audit[k] for k in ('before','after','afterEscapes','affectedExactTypedChecks','definitions')},indent=2))
