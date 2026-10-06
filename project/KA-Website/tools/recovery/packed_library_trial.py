"""Offline representation-only copy. Never opens a source with write permissions.

The final filename exists only after independent full verification. Durable external
state plus destination rowids make committed copying resumable without runtime tables.
The standalone deployed verifier imports only the deployed frozen payload readers.
"""
from __future__ import annotations
import argparse, collections, concurrent.futures as futures, hashlib, json, math
import multiprocessing, os, pathlib, shutil, sqlite3, struct, subprocess, sys, time, traceback

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import strategy_payload_codec as payload
import strategy_battle_binary as binary

PAYLOAD_TABLES = ('ea_sample', 'ea_holdout')

class PortableProgress:
    """Small verifier-only status writer, independent of all runtime storage modules."""
    def __init__(self,status,log,report):
        self.status=pathlib.Path(status); self.log=pathlib.Path(log)
        self.started=time.monotonic(); self.last=0
        try: self.base_elapsed=json.loads(self.status.read_text()).get('elapsedSeconds',0)
        except (OSError,ValueError): self.base_elapsed=0
        self.data={'state':'running','warning':None,'error':None}
    def update(self,**fields):
        previous=self.data.get('stage'); self.data.update(fields)
        elapsed=time.monotonic()-self.started
        self.data.update(elapsedSeconds=self.base_elapsed+elapsed,verificationElapsedSeconds=elapsed,
                         outputLocation=str(self.status.parent/'report.json'))
        if 'rowsPerSecond' in fields:
            self.data.update(processingRate=fields['rowsPerSecond'],processingRateUnit='rows/s')
        n=self.data.get('completed'); total=self.data.get('total')
        self.data['percent']=100*n/total if total and n is not None else None
        now=time.monotonic()
        if now-self.last>=1 or previous!=self.data.get('stage') or fields.get('state') in ('complete','failed'):
            atomic(self.status,self.data)
            with self.log.open('a',encoding='utf-8') as f: f.write(json.dumps(self.data)+'\n')
            self.last=now

def q(name):
    return '"' + name.replace('"', '""') + '"'

def row_keys(db,table):
    sql=db.execute('SELECT sql FROM sqlite_master WHERE type=\'table\' AND name=?',(table,)).fetchone()[0]
    if 'WITHOUT ROWID' not in sql.upper(): return []
    return [r[1] for r in sorted(db.execute('PRAGMA table_info('+q(table)+')'),key=lambda r:r[5]) if r[5]]

def ro(path):
    db = sqlite3.connect(pathlib.Path(path).resolve().as_uri()+'?mode=ro', uri=True)
    db.execute('PRAGMA query_only=ON')
    db.execute('PRAGMA cache_size=-32768')
    return db

def atomic(path, data):
    path = pathlib.Path(path)
    tmp = path.with_name(path.name+'.tmp')
    with tmp.open('w', encoding='utf-8') as f:
        json.dump(data, f, separators=(',', ':'), sort_keys=True, allow_nan=False)
        f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)

def fingerprint(path, progress=None):
    path = pathlib.Path(path)
    result = {}
    for suffix in ('', '-wal'):
        p = pathlib.Path(str(path)+suffix)
        if not p.exists():
            result[suffix] = None; continue
        st = p.stat(); h = hashlib.sha256(); count = 0; started=time.monotonic()
        with p.open('rb') as f:
            while block := f.read(8*1024*1024):
                h.update(block); count += len(block)
                if progress:
                    rate=count/max(.001,time.monotonic()-started)
                    progress.update(stage='source SHA256 safety check', completed=count,
                                    total=st.st_size, etaSeconds=(st.st_size-count)/rate,
                                    detail=str(p), processingRateUnit='bytes/s', processingRate=rate)
        after=p.stat()
        if (st.st_size, st.st_mtime_ns) != (after.st_size,after.st_mtime_ns):
            raise RuntimeError('Source changed while hashing; stop and preserve diagnostics')
        result[suffix]={'bytes':st.st_size, 'mtimeNs':st.st_mtime_ns, 'sha256':h.hexdigest()}
    return result

def inventory(db, path):
    objects = list(db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type,name"))
    tables={}
    for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall():
        cols=list(db.execute('PRAGMA table_info('+q(name)+')'))
        # Hidden/generated columns need explicit handling; stable composite keys are supported.
        xcols=list(db.execute('PRAGMA table_xinfo('+q(name)+')'))
        if len(cols)!=len(xcols) or any(r[-1] for r in xcols):
            raise RuntimeError('Generated/hidden columns require explicit handling: '+name)
        tables[name]={'rows':db.execute('SELECT count(*) FROM '+q(name)).fetchone()[0],
                      'columns':[r[1] for r in cols], 'columnInfo':cols,'rowKeyColumns':row_keys(db,name)}
    return json.loads(json.dumps({'source':str(path),'sqliteBytes':pathlib.Path(path).stat().st_size,
            'sidecars':{s:pathlib.Path(str(path)+s).stat().st_size if pathlib.Path(str(path)+s).exists() else 0 for s in ('-wal','-shm')},
            'objects':objects,'tables':tables,
            'pragmas':{k:db.execute('PRAGMA '+k).fetchone()[0] for k in ('user_version','application_id','page_size','page_count','freelist_count')},
            'schemaVersion':next(iter(db.execute("SELECT value FROM ea_meta WHERE key='schema_version'")),[None])[0]}))

def init_worker():
    # Instrument only this offline worker's reader; no runtime files are edited.
    payload._packed_codec(payload._registry_manifest()['current'][:16])

def encode_chunk(task):
    columns, rows = task
    started=time.monotonic()
    for row in rows:
        for kind in ('result','outcome'):
            i=columns.index(kind)+1
            if row[i] is not None:
                row[i]=payload.encode_packed_text(payload.decode_text(row[i]),kind)
    return rows, time.monotonic()-started

def text_equal(a,b):
    if type(a) is not type(b): return False
    if type(a) is float: return struct.pack('<d',a)==struct.pack('<d',b)
    return a==b

def digest_value(h,value):
    if isinstance(value,(tuple,list)):
        h.update(b'K'+struct.pack('<Q',len(value)))
        for v in value: digest_value(h,v)
        return
    if value is None: data=b'N'
    elif type(value) is int: data=b'I'+str(value).encode()
    elif type(value) is float: data=b'F'+struct.pack('<d',value)
    elif type(value) is str: data=b'T'+value.encode('utf-8',errors='surrogatepass')
    elif isinstance(value,bytes): data=b'B'+value
    else: raise TypeError(type(value))
    h.update(struct.pack('<Q',len(data))); h.update(data)

def verify_chunk(task):
    table,columns,source_rows,dest_rows=task
    if len(source_rows)!=len(dest_rows): raise RuntimeError('Batch population mismatch '+table)
    h=hashlib.sha256(); hist=collections.Counter(); counts=collections.Counter()
    layouts=collections.Counter(); field_shapes=collections.Counter(); schemas=collections.Counter()
    numeric={}; candidates=collections.Counter(); pairs=hashlib.sha256()
    # Count actual literal escape reads, including extension/reference fallback paths.
    escape_events=[]
    original=binary.Codec.read_escape
    def counted(codec,bits):
        value=original(codec,bits); escape_events.append(binary.shape_key(value)); return value
    binary.Codec.read_escape=counted
    try:
        for left,right in zip(source_rows,dest_rows):
            a=list(left); b=list(right)
            counts['rows']+=1; row_fallback=False; row_escape=False
            for kind in ('result','outcome') if table in PAYLOAD_TABLES else ():
                i=columns.index(kind)+1
                if a[i] is None:
                    counts['nullCells']+=1
                    if b[i] is not None: raise RuntimeError('NULL changed')
                    continue
                raw=b[i]
                a[i]=payload.decode_text(a[i]); escape_events.clear(); b[i]=payload.decode_text(b[i])
                counts['payloadCells']+=1; size=len(raw.encode()) if isinstance(raw,str) else len(raw)
                hist[size]+=1
                if isinstance(raw,bytes) and raw[:4]==payload.PACKED_MAGIC:
                    sid=struct.unpack('<H',raw[16:18])[0]; schemas[str(sid)]+=1
                    counts['packedCells']+=1
                    if sid==65535:
                        counts['wholeLayoutFallbackCells']+=1; row_fallback=True
                        layouts[binary.shape_key({kind:json.loads(a[i])})]+=1
                    else:
                        counts['normalPackedCells']+=1
                        if escape_events:
                            counts['fieldEscapeCells']+=1; row_escape=True
                            counts['fieldEscapeEvents']+=len(escape_events)
                            field_shapes.update(escape_events)
                else:
                    counts['exactTextFallbackCells']+=1; row_fallback=True
                    layouts[binary.shape_key({kind:json.loads(a[i])})]+=1
                if kind=='outcome':
                    value=json.loads(a[i])
                    def walk(v,path=''):
                        if isinstance(v,dict):
                            for k,x in v.items(): walk(x,path+'/'+k)
                        elif type(v) in (int,float) and math.isfinite(v):
                            n,s,sq=numeric.get(path,(0,0,0)); numeric[path]=(n+1,s+v,sq+v*v)
                    walk(value)
            if row_fallback: counts['fallbackRows']+=1
            if row_escape: counts['fieldEscapeRows']+=1
            if len(a)!=len(b) or any(not text_equal(x,y) for x,y in zip(a,b)):
                diffs=[i for i,(x,y) in enumerate(zip(a,b)) if not text_equal(x,y)]
                raise RuntimeError('Exact typed parity failed '+table+' rowid='+str(a[0])+' columns='+str(diffs))
            for v in a: digest_value(h,v)
            if table in PAYLOAD_TABLES:
                candidate=a[columns.index('candidate_id')+1]; candidates[candidate]+=1
                for name in ('candidate_id','seed_a','seed_b'):
                    digest_value(pairs,a[columns.index(name)+1])
        return {'rows':len(source_rows),'digest':h.hexdigest(),'counts':dict(counts),
                'histogram':dict(hist),'layouts':dict(layouts),'fieldShapes':dict(field_shapes),
                'schemas':dict(schemas),'numeric':numeric,'candidates':dict(candidates),'seedDigest':pairs.hexdigest()}
    finally:
        binary.Codec.read_escape=original

def batches(db,table,columns,after=None,batch=256):
    keys=row_keys(db,table); order=','.join(map(q,keys)) if keys else 'rowid'
    sql='SELECT '+('NULL' if keys else 'rowid')+','+','.join(map(q,columns))+' FROM '+q(table)
    args=()
    if after is not None:
        if keys:
            sql+=' WHERE ('+order+')>('+','.join('?' for _ in keys)+')'; args=tuple(after)
        else: sql+=' WHERE rowid>?'; args=(after,)
    cur=db.execute(sql+' ORDER BY '+order,args)
    while rows:=cur.fetchmany(batch):
        rows=[list(r) for r in rows]
        if keys:
            for row in rows: row[0]=tuple(row[columns.index(k)+1] for k in keys)
        yield rows

def fresh_verify(source,dest,info,workers,status,report):
    # This entry point runs from the deployed directory in a fresh interpreter.
    progress=PortableProgress(status,str(status)+'.jsonl',report)
    s=ro(source); d=ro(dest); s.execute('BEGIN'); d.execute('BEGIN')
    actual=inventory(d,dest)
    for key in ('objects','tables','schemaVersion'):
        # SQLite may reorder sqlite_sequence insertion, but its table schema is identical.
        if actual[key]!=info[key]: raise RuntimeError('Schema/count parity failed: '+key)
    for k in ('user_version','application_id','page_size'):
        if actual['pragmas'][k]!=info['pragmas'][k]: raise RuntimeError('Pragma changed '+k)
    total=sum(t['rows'] for t in info['tables'].values()); done=0
    result={'state':'verifying','tables':{},'exactTypedParity':'PASS','optimizerStatistics':{},
            'sourceFingerprintExpected':None}
    start=time.monotonic(); hist=collections.Counter(); counts=collections.Counter()
    layouts=collections.Counter(); fields=collections.Counter(); schemas=collections.Counter()
    with futures.ProcessPoolExecutor(max_workers=workers,initializer=init_worker) as pool:
        for table,tableinfo in info['tables'].items():
            cols=tableinfo['columns']; left=iter(batches(s,table,cols)); right=iter(batches(d,table,cols))
            pending=collections.deque(); digest=hashlib.sha256(); seed_digest=hashlib.sha256()
            numeric={}; candidates=collections.Counter(); checked=0
            def receive():
                nonlocal done,checked
                part=pending.popleft().result(); done+=part['rows']; checked+=part['rows']
                digest.update(bytes.fromhex(part['digest'])); seed_digest.update(bytes.fromhex(part['seedDigest']))
                hist.update({int(k):v for k,v in part['histogram'].items()}); counts.update(part['counts'])
                layouts.update(part['layouts']); fields.update(part['fieldShapes']); schemas.update(part['schemas'])
                candidates.update(part['candidates'])
                for key,(n,sm,sq) in part['numeric'].items():
                    pn,ps,pq=numeric.get(key,(0,0,0)); numeric[key]=(pn+n,ps+sm,pq+sq)
                elapsed=time.monotonic()-start; rate=done/max(.001,elapsed)
                progress.update(stage='independent exact parity: '+table,completed=done,total=total,
                                rowsPerSecond=rate,etaSeconds=(total-done)/rate,
                                detail='Every rowid, payload, seed order, metadata and association compared')
            while True:
                a=next(left,None); b=next(right,None)
                if a is None or b is None:
                    if a!=b: raise RuntimeError('Population differs in '+table)
                    break
                if table in PAYLOAD_TABLES:
                    pending.append(pool.submit(verify_chunk,(table,cols,a,b)))
                    if len(pending)>=workers*2: receive()
                else:
                    # Other tables need no CPU-heavy payload codec; avoid IPC for large intents.
                    part=verify_chunk((table,cols,a,b)); f=futures.Future(); f.set_result(part)
                    pending.append(f); receive()
            while pending: receive()
            result['tables'][table]={'rowsCompared':checked,'semanticDigest':digest.hexdigest()}
            if table in PAYLOAD_TABLES:
                result['optimizerStatistics'][table]={
                    'numericOutcomeInputs':{k:{'count':n,'sum':sm,'sumSquares':sq,'mean':sm/n,
                        'populationVariance':max(0,sq/n-(sm/n)**2)} for k,(n,sm,sq) in numeric.items()},
                    'candidateCount':len(candidates),'candidateSupportDigest':hashlib.sha256(json.dumps(sorted(candidates.items())).encode()).hexdigest(),
                    'orderedCandidateSeedEvidenceDigest':seed_digest.hexdigest(),
                    'parity':'PASS: complete outcome inputs and candidate/ordered seed values compared'}
    result['verificationSeconds']=time.monotonic()-start
    # Full optimizer association/budget queries; stable output hashes avoid giant reports.
    queries={
      'experimentBudgets':'SELECT sum(total),sum(reserved),sum(completed),count(*) FROM ea_experiment_budget',
      'reuseCharges':'SELECT reused,charged_experiment_id,count(*) FROM ea_sample_link GROUP BY reused,charged_experiment_id ORDER BY reused,charged_experiment_id',
      'candidateSupport':'SELECT candidate_id,count(*) FROM ea_sample GROUP BY candidate_id ORDER BY candidate_id',
      'holdoutSupport':'SELECT candidate_id,count(*),sum(result IS NOT NULL) FROM ea_holdout GROUP BY candidate_id ORDER BY candidate_id',
      'linkedExperimentCandidateSupport':'SELECT l.experiment_id,l.candidate_id,count(*),sum(s.result IS NOT NULL) FROM ea_sample_link l JOIN ea_sample s ON s.sample_key=l.sample_key GROUP BY l.experiment_id,l.candidate_id ORDER BY l.experiment_id,l.candidate_id',
      'developmentReservations':'SELECT l.experiment_id,l.candidate_id,l.seed_a,l.seed_b,(s.result IS NOT NULL) FROM ea_sample_link l JOIN ea_sample s ON s.sample_key=l.sample_key WHERE NOT EXISTS (SELECT 1 FROM ea_holdout h WHERE h.experiment_id=l.experiment_id AND h.candidate_id=l.candidate_id AND h.seed_a=l.seed_a AND h.seed_b=l.seed_b) ORDER BY l.experiment_id,l.candidate_id,l.seed_a,l.seed_b'}
    timings={}
    for name,sql in queries.items():
        outputs=[]; timings[name]={}
        progress.update(stage='optimizer query parity: '+name,completed=0,total=None,etaSeconds=None)
        for label,db in (('source',s),('destination',d)):
            t=time.monotonic(); h=hashlib.sha256(); n=0
            for row in db.execute(sql):
                for v in row: digest_value(h,v)
                n+=1
            timings[name][label+'Seconds']=time.monotonic()-t; outputs.append((n,h.hexdigest()))
        if outputs[0]!=outputs[1]: raise RuntimeError('Optimizer query differs '+name)
        timings[name].update(rows=outputs[0][0],digest=outputs[0][1],parity='PASS')
    result['queryPerformance']=timings
    # Payload fetch+decode cost measured separately on identical ordered representative rows.
    result['payloadReadPerformance']={}
    for table in PAYLOAD_TABLES:
        times={}; digests=[]
        for label,db in (('source',s),('destination',d)):
            t=time.monotonic(); h=hashlib.sha256(); n=0
            for row in db.execute('SELECT result,outcome FROM '+q(table)+' ORDER BY rowid LIMIT 4096'):
                for v in row: digest_value(h,None if v is None else payload.decode_text(v))
                n+=1
            times[label+'Seconds']=time.monotonic()-t; digests.append(h.hexdigest())
        if digests[0]!=digests[1]: raise RuntimeError('Representative read differs')
        times['rows']=n; times['parity']='PASS'; times['cache']='warm after full validation; sequential isolated timings'
        result['payloadReadPerformance'][table]=times
    progress.update(stage='destination SQLite integrity',completed=0,total=None,etaSeconds=None)
    t=time.monotonic(); callbacks=[0]
    def tick():
        callbacks[0]+=1
        if callbacks[0]%500==0: progress.update(completed=callbacks[0],total=None)
        return 0
    d.set_progress_handler(tick,100000)
    integrity=d.execute('PRAGMA integrity_check').fetchall(); d.set_progress_handler(None,0)
    if integrity!=[('ok',)]: raise RuntimeError('Destination integrity failed '+str(integrity))
    result['integritySeconds']=time.monotonic()-t; result['integrity']='PASS'
    population=sum(info['tables'][t]['rows'] for t in PAYLOAD_TABLES)
    payload_cells=counts['payloadCells']; all_hist=sorted(hist.items())
    def quantile(f):
        target=max(1,math.ceil(payload_cells*f)); n=0
        for size,count in all_hist:
            n+=count
            if n>=target: return size
    result['population']={'battleRows':population,'counts':dict(counts),'schemaUse':dict(schemas),
         'distinctWholeFallbackLayouts':len(layouts),'wholeFallbackLayouts':dict(layouts),
         'distinctFieldEscapeShapes':len(fields),'fieldEscapeShapes':dict(fields),
         'payloadBytes':{'mean':sum(k*v for k,v in hist.items())/max(1,payload_cells),
                         'median':quantile(.5),'p95':quantile(.95),'p99':quantile(.99),'max':max(hist,default=0)},
         'fallbackRowPercent':100*counts['fallbackRows']/max(1,population)}
    result['destinationInventory']=actual; result['rowsCompared']=done
    result['state']='verified'; atomic(report,result)
    progress.update(state='complete',stage='independent validation passed',completed=done,total=total,
                    etaSeconds=0,detail=str(report))
    s.close();d.close()
    return result

def probe(source,out,workers=(1,2,4,8,12,16)):
    db=ro(source); examples=[]
    for table in PAYLOAD_TABLES:
        cols=[r[1] for r in db.execute('PRAGMA table_info('+q(table)+')')]
        # Spread windows; bounded and measured inclusive of pool startup/loading/IPC.
        high=db.execute('SELECT max(rowid) FROM '+q(table)).fetchone()[0] or 0
        for frac in (0,.25,.5,.75):
            rows=db.execute('SELECT rowid,'+','.join(map(q,cols))+' FROM '+q(table)+' WHERE rowid>=? ORDER BY rowid LIMIT 256',(int(high*frac),)).fetchall()
            examples.append((cols,[list(r) for r in rows]))
    n=sum(len(r) for c,r in examples); trials=[]
    for w in workers:
        t=time.monotonic()
        with futures.ProcessPoolExecutor(max_workers=w,initializer=init_worker) as pool:
            outputs=list(pool.map(encode_chunk,examples))
        elapsed=time.monotonic()-t
        trials.append({'workers':w,'rows':n,'elapsedSeconds':elapsed,'rowsPerSecond':n/elapsed,
                       'encodeCpuSeconds':sum(x[1] for x in outputs)})
    fastest=max(r['rowsPerSecond'] for r in trials)
    best=next(r for r in trials if r['rowsPerSecond']>=fastest*.95)
    report={'trials':trials,'selectedWorkers':best['workers'],'includes':'fresh process startup, definitions load, encoding and IPC',
            'notes':'8 tasks cap useful probe concurrency; production uses smaller queued chunks. GPU unsuitable for branching JSON codec.'}
    atomic(out,report); db.close(); return report

def run(args):
    from migrate_strategy_payload_storage import Progress, _set_background_priority, _resource_snapshot
    source=pathlib.Path(args.source).resolve(); dest=pathlib.Path(args.destination).resolve()
    if source==dest or dest.parent!=source.parent or not dest.name.endswith('.packed-trial.sqlite'):
        raise RuntimeError('Destination must be an unmistakable separate trial alongside source')
    stage=dest.with_name(dest.stem+'.incomplete.sqlite')
    support=dest.with_name(dest.name+'.support'); statepath=support/'state.json'
    if dest.exists(): raise RuntimeError('Final trial already exists; refuse overwrite')
    if not args.resume and (stage.exists() or support.exists()): raise RuntimeError('Existing trial state requires explicit --resume')
    support.mkdir(exist_ok=args.resume)
    progress=Progress(support/'status.json',support/'log.jsonl',support/'report.json')
    _set_background_priority(); resources=_resource_snapshot({'source':source,'stage':stage,'output':dest})
    progress.update(resources=resources,state='running',detail='Source remains read-only; final filename is absent until verified')
    started=time.monotonic()
    try:
        fp=fingerprint(source,progress)
        s=ro(source); s.execute('BEGIN'); version=s.execute('PRAGMA data_version').fetchone()[0]
        if args.resume:
            state=json.loads(statepath.read_text())
            if state['source']!=str(source) or state['destination']!=str(dest) or state['fingerprint']!=fp: raise RuntimeError('Source/destination identity changed; cannot resume same trial')
            info=state['inventory']
        else:
            progress.update(stage='complete source inventory',completed=0,total=None,etaSeconds=None)
            info=inventory(s,source)
            state={'state':'incomplete','source':str(source),'destination':str(dest),'fingerprint':fp,
                   'inventory':info,'tables':{},'timings':{},'resources':resources,'resumedRuns':0,'workers':args.workers}
            atomic(statepath,state); atomic(support/'source-inventory.json',info)
        # Frozen standalone reader deployment. No runtime storage design changes.
        for name in ('strategy_payload_codec.py','strategy_battle_binary.py','packed_library_trial.py'):
            target=support/name
            if not target.exists(): shutil.copy2(HERE/name,target)
        shutil.copytree(HERE/'battle_binary_registry',support/'battle_binary_registry',dirs_exist_ok=True)
        d=sqlite3.connect(stage); d.execute('PRAGMA journal_mode=DELETE'); d.execute('PRAGMA synchronous=FULL')
        d.execute('PRAGMA cache_size=-65536'); d.execute('PRAGMA temp_store=FILE')
        if not args.resume:
            d.execute('PRAGMA page_size='+str(info['pragmas']['page_size']))
            for typ,name,table,sql in info['objects']:
                if typ=='table' and not name.startswith('sqlite_'): d.execute(sql)
            for pragma in ('user_version','application_id'): d.execute('PRAGMA '+pragma+'='+str(info['pragmas'][pragma]))
            d.commit()
        else:
            current=inventory(d,stage)
            if set(current['tables'])!=set(info['tables']): raise RuntimeError('Resume table set differs')
            for table in info['tables']:
                if current['tables'][table]['columnInfo']!=info['tables'][table]['columnInfo']: raise RuntimeError('Resume columns differ '+table)
            if current['schemaVersion']!=info['schemaVersion'] and d.execute('SELECT count(*) FROM ea_meta').fetchone()[0]:
                raise RuntimeError('Resume schema marker changed')
            state['resumedRuns']+=1; atomic(statepath,state)
        total=sum(t['rows'] for t in info['tables'].values()); done=sum(d.execute('SELECT count(*) FROM '+q(t)).fetchone()[0] for t in info['tables'] if t!='sqlite_sequence')
        copy_start=time.monotonic(); initial_done=done
        old=state['timings']
        encode_cpu=old.get('encodeWorkerSeconds',0); read_seconds=old.get('sourceReadSeconds',0)
        read_bytes=old.get('sourceReadBytes',0); write_seconds=old.get('databaseWriteSeconds',0); batches_done=0
        with futures.ProcessPoolExecutor(max_workers=args.workers,initializer=init_worker) as pool:
            for table,tableinfo in info['tables'].items():
                if table=='sqlite_sequence': continue
                cols=tableinfo['columns']; count=d.execute('SELECT count(*) FROM '+q(table)).fetchone()[0]
                if count>tableinfo['rows']: raise RuntimeError('Destination has extra rows '+table)
                keys=tableinfo['rowKeyColumns']
                if count:
                    if keys:
                        after=d.execute('SELECT '+','.join(map(q,keys))+' FROM '+q(table)+' ORDER BY '+','.join(q(k)+' DESC' for k in keys)+' LIMIT 1').fetchone()
                    else: after=d.execute('SELECT max(rowid) FROM '+q(table)).fetchone()[0]
                else: after=None
                it=iter(batches(s,table,cols,after,args.batch)); pending=collections.deque()
                insert='INSERT INTO '+q(table)+'('+('' if keys else 'rowid,')+','.join(map(q,cols))+') VALUES('+','.join('?' for _ in range(len(cols)+(0 if keys else 1)))+')'
                def write_next():
                    nonlocal done,encode_cpu,write_seconds,batches_done,count
                    rows,enc=pending.popleft().result(); encode_cpu+=enc
                    t=time.monotonic(); d.executemany(insert,[r[1:] for r in rows] if keys else rows); d.commit(); write_seconds+=time.monotonic()-t
                    done+=len(rows); count+=len(rows); batches_done+=1
                    state['tables'][table]={'rowsCommitted':count,'lastRowid':rows[-1][0]}
                    state['timings'].update(encodeWorkerSeconds=encode_cpu,databaseWriteSeconds=write_seconds,sourceReadSeconds=read_seconds,sourceReadBytes=read_bytes)
                    atomic(statepath,state)
                    elapsed=time.monotonic()-copy_start; rate=(done-initial_done)/max(.001,elapsed)
                    progress.update(stage='copy and pack: '+table,completed=done,total=total,
                                    rowsPerSecond=rate,etaSeconds=(total-done)/max(.001,rate),
                                    detail=str(stage)+' | committed batches; workers '+str(args.workers))
                    if args.stop_after_batches and batches_done>=args.stop_after_batches:
                        raise InterruptedError('Deliberate interruption after committed batch for resume evidence')
                while True:
                    t=time.monotonic(); rows=next(it,None); read_seconds+=time.monotonic()-t
                    if rows is None: break
                    read_bytes+=sum(len(v.encode()) if isinstance(v,str) else len(v) if isinstance(v,bytes) else 8 for r in rows for v in r if v is not None)
                    if table in PAYLOAD_TABLES: pending.append(pool.submit(encode_chunk,(cols,rows)))
                    else:
                        f=futures.Future(); f.set_result((rows,0)); pending.append(f)
                    if len(pending)>=args.workers*2 or table not in PAYLOAD_TABLES: write_next()
                while pending: write_next()
                if count!=tableinfo['rows']: raise RuntimeError('Copy count mismatch '+table)
        # Restore deleted AUTOINCREMENT high-water marks exactly.
        if 'sqlite_sequence' in info['tables']:
            d.execute('DELETE FROM sqlite_sequence')
            d.executemany('INSERT INTO sqlite_sequence(rowid,name,seq) VALUES(?,?,?)',s.execute('SELECT rowid,name,seq FROM sqlite_sequence'))
            d.commit()
        state['timings']['copyWallSeconds']=old.get('copyWallSeconds',0)+time.monotonic()-copy_start
        progress.update(stage='rebuild preserved reader indexes',completed=0,total=None,etaSeconds=None)
        t=time.monotonic(); index_count=0
        for typ,name,table,sql in info['objects']:
            if typ in ('index','view','trigger') and not d.execute('SELECT 1 FROM sqlite_master WHERE name=?',(name,)).fetchone():
                progress.update(detail=name,completed=index_count,total=None)
                d.execute(sql); d.commit(); index_count+=1
        state['timings']['indexSeconds']=old.get('indexSeconds',0)+time.monotonic()-t
        d.close()
        if s.execute('PRAGMA data_version').fetchone()[0]!=version: raise RuntimeError('Source changed during trial')
        s.close()
        if fingerprint(source,progress)!=fp: raise RuntimeError('Source bytes changed; trial remains incomplete')
        state['state']='copied-unverified'; atomic(statepath,state)
        progress.update(stage='fresh independent verifier',completed=0,total=None,etaSeconds=None,
                        detail='Independent process uses deployed reader files and definitions')
        cmd=[sys.executable,str(support/'packed_library_trial.py'),'--verify','--source',str(source),'--destination',str(stage),
             '--workers',str(args.workers),'--inventory',str(support/'source-inventory.json'),'--status',str(support/'status.json'),'--report',str(support/'verification.json')]
        with (support/'verification-console.log').open('w') as log:
            code=subprocess.call(cmd,stdout=log,stderr=subprocess.STDOUT,cwd=support)
        if code: raise RuntimeError('Independent verifier failed; see verification-console.log')
        verification=json.loads((support/'verification.json').read_text())
        if fingerprint(source,progress)!=fp: raise RuntimeError('Source changed during verification')
        # Only now make a final trial visible. No producer/reader config is switched.
        if dest.exists(): raise RuntimeError('Unexpected final destination exists')
        atomic(support/'VERIFIED-COMPLETE.json',{'state':'verified-complete','destination':str(dest),
                'sqliteBytes':stage.stat().st_size,'sourceFingerprint':fp,
                'verificationSha256':hashlib.sha256((support/'verification.json').read_bytes()).hexdigest()})
        os.rename(stage,dest)
        state['state']='verified-complete'; state['timings']['thisRunWallSeconds']=time.monotonic()-started
        state['sourceUnchanged']='PASS: main file and WAL SHA256, sizes and timestamps unchanged'
        state['timings']['verificationSeconds']=verification['verificationSeconds']
        state['verification']=verification; atomic(statepath,state)
        registry_bytes=sum(p.stat().st_size for p in (support/'battle_binary_registry').rglob('*') if p.is_file())
        definitions=payload._packed_codec(payload._registry_manifest()['current'][:16]).definitions
        raw_definitions=binary.dumps(definitions).encode()
        # Full footprint includes durable support evidence and standalone reader code.
        sqlite_bytes=dest.stat().st_size
        support_bytes=sum(p.stat().st_size for p in support.rglob('*') if p.is_file())
        footprint=sqlite_bytes+support_bytes
        source_bytes=sum(x['bytes'] for x in fp.values() if x)+info['sidecars']['-shm']
        saved=source_bytes-footprint
        report={'state':'verified-complete','source':str(source),'destination':str(dest),'sourceBytes':source_bytes,
                'sourceGiB':source_bytes/2**30,'destinationSQLiteBytes':sqlite_bytes,'deployedDefinitionsBytes':registry_bytes,
                'supportBytesAtMeasurement':support_bytes,'completeFootprintBytesAtMeasurement':footprint,
                'packedGiB':footprint/2**30,'savedBytes':saved,'savedGB':saved/1e9,'savedGiB':saved/2**30,
                'percentSmaller':100*saved/source_bytes,'sourceDestinationRatio':source_bytes/footprint,
                'bytesPerRetainedBattle':footprint/verification['population']['battleRows'],
                'migratedBattles':verification['population']['battleRows'],'sourceBattles':sum(info['tables'][t]['rows'] for t in PAYLOAD_TABLES),
                'exactParity':'PASS','sourceUnchanged':state['sourceUnchanged'],'sourceFingerprint':fp,
                'schemaVersionPreserved':info['schemaVersion'],'timings':state['timings'],
                'population':verification['population'],'sourceInventory':info,'destinationInventory':verification['destinationInventory'],
                'optimizerStatistics':verification['optimizerStatistics'],'queryPerformance':verification['queryPerformance'],
                'payloadReadPerformance':verification['payloadReadPerformance'],'resources':resources,'workers':args.workers,
                'definitions':{'schemas':len(definitions['schemas']),'dictionaryEntries':len(definitions['dictionary']),
                    'currentRawBytes':len(raw_definitions),'dictionaryRawBytes':len(binary.dumps(definitions['dictionary']).encode()),
                    'allDeployedBytes':registry_bytes,'amortizedDeployedBytesPerBattle':registry_bytes/verification['population']['battleRows']},
                'noCutover':True,'unknownMetrics':['runtime optimizer search rankings not re-executed; identical complete statistical inputs verified'],
                'footprintMeasurementNote':'Final footprint includes deployed readers/definitions, all durable reports/logs/checkpoints and SQLite sidecars; every byte counted after final status write',
                'timingNote':'encodeWorkerSeconds is aggregate process CPU stage wall time, not elapsed conversion time; sourceReadBytes is logical row-value volume, not physical disk bytes'}
        atomic(support/'report.json',report)
        progress.update(state='complete',stage='verified packed trial complete',completed=report['migratedBattles'],total=report['sourceBattles'],etaSeconds=0,detail=str(dest))
        report['timings']['totalWallSeconds']=time.monotonic()-started
        report['timings']['conversionRowsPerSecond']=sum(info['tables'][t]['rows'] for t in info['tables'])/max(.001,state['timings']['copyWallSeconds'])
        report['timings']['logicalSourceReadBytesPerSecond']=read_bytes/max(.001,read_seconds)
        # Iterate the small self-describing report until its own byte length stabilizes.
        for _ in range(8):
            external={str(p.relative_to(support)):p.stat().st_size for p in support.rglob('*') if p.is_file()}
            sidecars={s:pathlib.Path(str(dest)+s).stat().st_size if pathlib.Path(str(dest)+s).exists() else 0 for s in ('-wal','-shm','-journal')}
            support_bytes=sum(external.values()); footprint=sqlite_bytes+support_bytes+sum(sidecars.values()); saved=source_bytes-footprint
            report.update(supportBytesAtMeasurement=support_bytes,completeFootprintBytesAtMeasurement=footprint,
                packedGiB=footprint/2**30,savedBytes=saved,savedGB=saved/1e9,savedGiB=saved/2**30,
                percentSmaller=100*saved/source_bytes,sourceDestinationRatio=source_bytes/footprint,
                bytesPerRetainedBattle=footprint/report['migratedBattles'],externalFiles=external,destinationSidecars=sidecars)
            before=(support/'report.json').stat().st_size; atomic(support/'report.json',report)
            if before==(support/'report.json').stat().st_size: break
        else: raise RuntimeError('Final report byte accounting did not stabilize')
        return report
    except BaseException as e:
        progress.update(state='interrupted' if isinstance(e,InterruptedError) else 'failed',error=str(e),detail=traceback.format_exc())
        raise

def main():
    p=argparse.ArgumentParser(); p.add_argument('--source',required=True); p.add_argument('--destination')
    p.add_argument('--workers',type=int,default=8); p.add_argument('--batch',type=int,default=256)
    p.add_argument('--resume',action='store_true'); p.add_argument('--stop-after-batches',type=int)
    p.add_argument('--verify',action='store_true');p.add_argument('--probe',action='store_true')
    p.add_argument('--headless',action='store_true');p.add_argument('--inventory');p.add_argument('--status');p.add_argument('--report')
    a=p.parse_args()
    if a.verify: fresh_verify(a.source,a.destination,json.loads(pathlib.Path(a.inventory).read_text()),a.workers,a.status,a.report)
    elif a.probe: print(json.dumps(probe(a.source,a.report)))
    elif a.headless: run(a)
    else:
        from migrate_strategy_payload_storage import _run_with_tk
        support=pathlib.Path(a.destination).with_name(pathlib.Path(a.destination).name+'.support')
        _run_with_tk(lambda:run(a),support/'status.json')

if __name__=='__main__':
    multiprocessing.freeze_support()
    main()
