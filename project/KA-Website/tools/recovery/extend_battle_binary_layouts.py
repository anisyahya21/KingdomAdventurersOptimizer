import json,pickle,copy,hashlib,zlib
from pathlib import Path
import strategy_battle_binary as b
out=Path('studies/battle_binary_corpus'); original=json.loads((out/'b9f86ce61fd7c3cf'/'definitions.json').read_text()); raw=b.dumps(original).encode()
assert hashlib.sha256(raw).hexdigest()=='b9f86ce61fd7c3cf26dcc45a9e8ab8c0a5b0cf71e14bd4ce73a9566566fbe7f0'
(out/'b9f86ce61fd7c3cf'/'definitions.json').write_bytes(raw)
ranked=pickle.loads((out/'escape-audit-representatives.pickle').read_bytes()); new=copy.deepcopy(original)
for key,g in ranked[:3]:
    assert key not in new['shapes']; codec=b.Codec(g['representative']); new['shapes'].append(key); new['schemas'].append(codec.schema)
# Preserve dictionary IDs and width: new text stays in counted inline escapes.
assert new['dictionary']==original['dictionary']
raw_new=b.dumps(new).encode(); target=out/'escape-audit-candidate-definitions.json'; target.write_bytes(raw_new)
encoder=b.RecordEncoder(new)
for key,g in ranked[:3]:
    packet=encoder.encode(g['representative']); b.exact(g['representative'],encoder.decode(packet)); print(key[:12],g['count'],len(packet))
print(json.dumps(dict(oldSchemas=len(original['schemas']),newSchemas=len(new['schemas']),oldRaw=len(raw),newRaw=len(raw_new),oldZlib9=len(zlib.compress(raw,9)),newZlib9=len(zlib.compress(raw_new,9)),newHash=hashlib.sha256(raw_new).hexdigest())))

if '--apply' in __import__('sys').argv:
    assert hashlib.sha256(b.DEFAULT_DEFINITIONS.read_bytes()).hexdigest() in (hashlib.sha256(raw).hexdigest(),hashlib.sha256(raw_new).hexdigest())
    b.DEFAULT_DEFINITIONS.write_bytes(raw_new)
