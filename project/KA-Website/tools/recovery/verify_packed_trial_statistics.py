"""Independent destination calculation against saved full-source statistical inputs.

Reuses the source aggregation from exact parity; reads only destination outcomes.
The same 256-row boundaries and order retain exact floating-point summation order.
"""
import collections,concurrent.futures as futures,json,math,pathlib,sys,time

def summarize(rows):
    import strategy_payload_codec as payload
    numeric={}; completed=0
    for raw in rows:
        if raw is None: continue
        completed+=1;value=json.loads(payload.decode_text(raw))
        def walk(v,path=''):
            if isinstance(v,dict):
                for k,x in v.items(): walk(x,path+'/'+k)
            elif type(v) in (int,float) and math.isfinite(v):
                n,s,sq=numeric.get(path,(0,0,0)); numeric[path]=(n+1,s+v,sq+v*v)
        walk(value)
    return numeric,completed

def main():
    support=pathlib.Path(sys.argv[1]);sys.path.insert(0,str(support))
    import packed_library_trial as trial
    started=time.monotonic()
    while not (support/'verification.json').exists():
        status=json.loads((support/'status.json').read_text())
        if status.get('state') in ('failed','interrupted'): raise RuntimeError('Primary job failed')
        time.sleep(1)
    source=json.loads((support/'verification.json').read_text())
    dest=support.with_name(support.name.removesuffix('.support'))
    # Parent publishes the final trial after hashing source again.
    while not dest.exists(): time.sleep(1)
    actual_start=time.monotonic();db=trial.ro(dest);out={'state':'PASS','tables':{}}
    with futures.ProcessPoolExecutor(max_workers=8) as pool:
        for table in trial.PAYLOAD_TABLES:
            numeric={};accepted=0;rows=0;pending=collections.deque()
            def receive():
                nonlocal accepted
                part,count=pending.popleft().result();accepted+=count
                for key,(n,s,sq) in part.items():
                    pn,ps,pq=numeric.get(key,(0,0,0));numeric[key]=(pn+n,ps+s,pq+sq)
            cur=db.execute('SELECT outcome FROM '+trial.q(table)+' ORDER BY rowid')
            while batch:=cur.fetchmany(256):
                rows+=len(batch);pending.append(pool.submit(summarize,[r[0] for r in batch]))
                if len(pending)>=16: receive()
            while pending: receive()
            calculated={k:{'count':n,'sum':s,'sumSquares':sq,'mean':s/n,'populationVariance':max(0,sq/n-(s/n)**2)} for k,(n,s,sq) in numeric.items()}
            expected=source['optimizerStatistics'][table]['numericOutcomeInputs']
            if calculated!=expected: raise RuntimeError('Destination statistical output mismatch '+table)
            out['tables'][table]={'retainedRows':rows,'acceptedOutcomes':accepted,'statisticalFields':len(numeric),
                                  'sourceAndDestinationStatistics':calculated,'parity':'PASS'}
    out['calculationSeconds']=time.monotonic()-actual_start
    out['waitSecondsSeparate']=actual_start-started
    out['method']='Fresh process with deployed reader only; independent complete destination totals, counts, means and variances compared to previously saved full-source calculation; source pass reused'
    trial.atomic(support/'statistical-output-parity.json',out)
    print(json.dumps({'state':'PASS','calculationSeconds':out['calculationSeconds'],'tables':{k:{x:y for x,y in v.items() if x!='sourceAndDestinationStatistics'} for k,v in out['tables'].items()}}))

if __name__=='__main__':main()
