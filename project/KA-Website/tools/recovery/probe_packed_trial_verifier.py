"""Bounded parallel decode/parity throughput measurement, independent intervals."""
import concurrent.futures as futures,json,pathlib,time
import packed_library_trial as trial

def main():
    db=trial.ro('A:/KingdomAdventurersOptimizer/Community-Knowledge-20260928.sqlite')
    chunks=[]
    for table in trial.PAYLOAD_TABLES:
        cols=[r[1] for r in db.execute('PRAGMA table_info('+trial.q(table)+')')]
        high=db.execute('SELECT max(rowid) FROM '+trial.q(table)).fetchone()[0]
        for frac in (0,.25,.5,.75):
            rows=db.execute('SELECT rowid,'+','.join(map(trial.q,cols))+' FROM '+trial.q(table)+' WHERE rowid>=? ORDER BY rowid LIMIT 512',(int(high*frac),)).fetchall()
            for i in range(0,len(rows),64): chunks.append((table,cols,[list(r) for r in rows[i:i+64]]))
    prep=time.monotonic()
    with futures.ProcessPoolExecutor(max_workers=8,initializer=trial.init_worker) as pool:
        packed=list(pool.map(trial.encode_chunk,[(c,[list(r) for r in rows]) for t,c,rows in chunks]))
    prep_seconds=time.monotonic()-prep
    tasks=[(t,c,rows,new) for (t,c,rows),(new,_) in zip(chunks,packed)]
    n=sum(len(x[2]) for x in tasks); records=[]
    for workers in (4,8,12,16,20,22):
        started=time.monotonic()
        with futures.ProcessPoolExecutor(max_workers=workers,initializer=trial.init_worker) as pool:
            outputs=list(pool.map(trial.verify_chunk,tasks))
        elapsed=time.monotonic()-started
        assert sum(x['rows'] for x in outputs)==n
        records.append({'workers':workers,'seconds':elapsed,'rows':n,'rowsPerSecond':n/elapsed})
    fastest=max(r['rowsPerSecond'] for r in records)
    selected=next(r['workers'] for r in records if r['rowsPerSecond']>=fastest*.95)
    result={'trials':records,'selectedWorkers':selected,'preparationSecondsSeparate':prep_seconds,
            'includes':'fresh-process startup, definitions load, IPC, decode, full typed comparison, actual escape reads and numeric input summaries',
            'chunks':len(chunks),'maxLogicalCpuUsageTarget':'22 of 28; at least 6 logical CPUs remain'}
    path=trial.HERE/'studies/battle_binary_corpus/full-trial-verifier-throughput.json'
    trial.atomic(path,result);print(json.dumps(result))

if __name__=='__main__': main()
