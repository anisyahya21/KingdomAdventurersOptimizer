"""Exercise the opt-in sidecar on committed REAL rows, opening the library read-only."""
import json
import os
from pathlib import Path
import pickle
import sqlite3
import time
import strategy_battle_binary as binary
import strategy_battle_binary_shadow as shadow
import benchmark_battle_binary as bench

def run():
    root=bench.OUT/'real-shadow-pilot'; os.environ[shadow.ENV_NAME]=str(root)
    manifest=json.loads((bench.OUT/'manifest.json').read_text())
    selected=[]
    for table in ('ea_sample','ea_holdout'):
        files=[s['file'] for s in manifest['shards'] if s['file'].startswith(table)]
        records=[]
        for filename in files[::64]: records.extend(pickle.loads((bench.OUT/filename).read_bytes()))
        selected.extend(records[:256])
    db=sqlite3.connect(bench.SOURCE.as_uri()+'?mode=ro',uri=True)
    db.execute('PRAGMA query_only=ON'); expected={}; start=time.perf_counter()
    for record in selected:
        columns=record['columns']; value,_,_=bench.unpack(record)
        value['timing']=json.loads(columns['timing']) if columns['timing'] is not None else None
        table=record['table']; kind='sample' if table=='ea_sample' else 'holdout'
        where=({'sample_key':columns['sample_key']} if kind=='sample' else
            {k:columns[k] for k in ('experiment_id','candidate_id','seed_a','seed_b')})
        key=(columns['sample_key'] if kind=='sample' else binary.dumps(where))
        context=dict(sourceTable=table,sourceRowid=columns['rowid'],candidateId=columns['candidate_id'],
                     seedPair=[columns['seed_a'],columns['seed_b']])
        accepted=shadow.record_primary_write(db,kind=kind,record_key=key,context=context,value=value,primary_where=where)
        if not accepted: raise AssertionError('real shadow copy failed')
        expected[(kind,key)]=value
    write_wall=time.perf_counter()-start; db.close()
    start=time.perf_counter(); saved=shadow.read_records(root)
    for row in saved: binary.exact(expected[(row['kind'],row['recordKey'])],row['value'])
    assert len(saved)==len(expected)==512
    sidecar=sqlite3.connect(root/shadow.DB_NAME)
    payload_bytes=sidecar.execute('SELECT sum(length(packet)) FROM shadow_record').fetchone()[0]; sidecar.close()
    definitions=sum(p.stat().st_size for p in (root/'definitions').glob('*'))
    report=dict(realSourceRows=512,sourceOpenedReadOnly=True,sourceWrites=0,exactPersistedParity=True,
                writeSecondsIncludingPrimaryVerificationDefinitionsAndDurability=write_wall,
                writesPerSecond=512/write_wall,readAndExactParitySeconds=time.perf_counter()-start,
                packetBytes=payload_bytes,definitionFileBytes=definitions,
                actualSidecarDatabaseBytes=(root/shadow.DB_NAME).stat().st_size,
                totalDirectoryBytes=sum(p.stat().st_size for p in root.rglob('*') if p.is_file()),
                notes='Sidecar database bytes include indexes, context JSON and SQLite overhead. This is additional shadow storage; primary library is unchanged.')
    (bench.OUT/'real-shadow-pilot.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))

if __name__=='__main__': run()
