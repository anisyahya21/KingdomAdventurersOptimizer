"""Read-only, resumable real corpus extraction and bounded parallel codec evidence."""
import argparse
from concurrent.futures import ProcessPoolExecutor
import hashlib
import json
import math
import os
from pathlib import Path
import pickle
import sqlite3
import statistics
import time
import zlib
import strategy_battle_binary as binary
import strategy_payload_codec as legacy

ROOT=Path(__file__).resolve().parent
SOURCE=Path('A:/KingdomAdventurersOptimizer/Community-Knowledge-20260928.schema5-compact.sqlite')
OUT=ROOT/'studies/battle_binary_corpus'
START=time.perf_counter()
EVIDENCE=OUT
DEFINITION_HASH=None
STAGE=None
STAGE_START=START
STAGE_BASE=0

def atomic_json(path,value):
    tmp=path.with_suffix('.tmp'); tmp.write_text(json.dumps(value,indent=2),encoding='utf-8')
    for attempt in range(5):
        try: tmp.replace(path); return
        except PermissionError:
            if attempt==4: raise
            time.sleep(.03)

def status(stage,done,total,error=None):
    global STAGE,STAGE_START,STAGE_BASE
    if stage!=STAGE:
        STAGE=stage; STAGE_START=time.perf_counter(); STAGE_BASE=done
    elapsed=time.perf_counter()-START
    duration=time.perf_counter()-STAGE_START
    rate=(done-STAGE_BASE)/duration if duration>.25 and done>STAGE_BASE else None
    atomic_json(OUT/'status.json',dict(stage=stage,completed=done,total=total,
        elapsed=elapsed,error=error,rate=rate,eta=(total-done)/rate if rate and total else None,output=str(OUT)))

def unpack(record):
    row=record['columns']; texts={k:legacy.decode_text(row[k]) for k in ('result','outcome')}
    value={k:json.loads(v) if v is not None else None for k,v in texts.items()}
    meta={'columns':{k:v for k,v in row.items() if k not in ('result','outcome')},'links':record['links']}
    return value,meta,texts

def extract(limit):
    manifest_path=OUT/'manifest.json'
    if manifest_path.exists():
        manifest=json.loads(manifest_path.read_text())
        if manifest['requestedRows']!=limit or manifest['source']!=str(SOURCE): raise ValueError('different corpus arguments; use separate output')
        for shard in manifest['shards']:
            if hashlib.sha256((OUT/shard['file']).read_bytes()).hexdigest()!=shard['sha256']: raise ValueError('corpus shard changed')
        return manifest
    db=sqlite3.connect(SOURCE.as_uri()+'?mode=ro',uri=True); db.row_factory=sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    manifest=dict(source=str(SOURCE),sourceBytes=SOURCE.stat().st_size,requestedRows=limit,
        method='256 deterministic spread rowid windows per table; equal table strata; local stratum quotas; no filtering by outcome or codec size',
        shards=[],tables={},sourceOpenedReadOnly=True)
    count=0; t0=time.perf_counter()
    for table in ('ea_sample','ea_holdout'):
        maximum=db.execute('SELECT max(rowid) FROM '+table).fetchone()[0]
        population=db.execute('SELECT count(*) FROM '+table).fetchone()[0]
        manifest['tables'][table]=dict(population=population,maxRowid=maximum,selected=0)
        wanted=limit//2+(limit%2 if table=='ea_sample' else 0)
        for window in range(256):
            name=f'{table}-{window:03d}.pickle'; checkpoint=OUT/name
            quota=wanted//256+(1 if window<wanted%256 else 0)
            if quota==0: continue
            if checkpoint.exists(): records=pickle.loads(checkpoint.read_bytes())
            else:
                low=1+window*maximum//256; high=(window+1)*maximum//256
                rows=[dict(r) for r in db.execute('SELECT rowid,* FROM '+table+' WHERE rowid BETWEEN ? AND ? ORDER BY rowid LIMIT ?', (low,high,quota))]
                links={}
                if table=='ea_sample' and rows:
                    keys=[r['sample_key'] for r in rows]
                    for r in db.execute('SELECT * FROM ea_sample_link WHERE sample_key IN ('+','.join('?'*len(keys))+') ORDER BY experiment_id',keys):
                        links.setdefault(r['sample_key'],[]).append(dict(r))
                records=[dict(table=table,columns=r,links=links.get(r.get('sample_key'),[])) for r in rows]
                checkpoint.write_bytes(pickle.dumps(records,protocol=5))
            digest=hashlib.sha256(checkpoint.read_bytes()).hexdigest()
            manifest['shards'].append(dict(file=name,rows=len(records),sha256=digest))
            count+=len(records); manifest['tables'][table]['selected']+=len(records)
            status('Extracting read-only corpus',count,limit)
    db.close(); manifest['rows']=count; manifest['extractWallSeconds']=time.perf_counter()-t0
    atomic_json(manifest_path,manifest)
    return manifest

def load_training(manifest):
    values=[]; rows=0
    for shard in manifest['shards']:
        records=pickle.loads((OUT/shard['file']).read_bytes())
        for index,record in enumerate(records):
            if is_training(record):
                value,meta,_=unpack(record); values.extend((value,meta)); rows+=1
    return values,rows

def candidate_bucket(candidate):
    return int(hashlib.sha256(str(candidate).encode()).hexdigest()[:8],16)%5

def is_training(record):
    return record['table']=='ea_sample' and candidate_bucket(record['columns'].get('candidate_id'))==0

WORKER=None
def worker_init(definitions,evidence):
    global WORKER,EVIDENCE,DEFINITION_HASH
    EVIDENCE=Path(evidence)
    WORKER=binary.RecordEncoder(definitions)
    DEFINITION_HASH=hashlib.sha256(binary.dumps(definitions).encode()).hexdigest()

def process_shard(filename,save=True):
    rows=pickle.loads((OUT/filename).read_bytes()); measurements=[]; packets=[]
    timings=dict(legacyDecode=0.,encode=0.,decode=0.,metadataEncode=0.,metadataDecode=0.,parity=0.)
    for index,record in enumerate(rows):
        t=time.perf_counter(); value,meta,texts=unpack(record); timings['legacyDecode']+=time.perf_counter()-t
        t=time.perf_counter(); packet=WORKER.encode(value); timings['encode']+=time.perf_counter()-t
        t=time.perf_counter(); decoded=WORKER.decode(packet); timings['decode']+=time.perf_counter()-t
        t=time.perf_counter(); binary.exact(value,decoded); timings['parity']+=time.perf_counter()-t
        t=time.perf_counter(); meta_packet=WORKER.encode(meta); timings['metadataEncode']+=time.perf_counter()-t
        t=time.perf_counter(); binary.exact(meta,WORKER.decode(meta_packet)); timings['metadataDecode']+=time.perf_counter()-t
        result=value.get('result') or {}; outcome=value.get('outcome') or {}
        raw=sum(len(v.encode()) if v is not None else 0 for v in texts.values())
        stored=sum(len(record['columns'][k].encode()) if isinstance(record['columns'][k],str) else len(record['columns'][k] or b'') for k in ('result','outcome'))
        measurements.append(dict(table=record['table'],rowid=record['columns']['rowid'],raw=raw,current=stored,
            binary=len(packet),metadata=len(meta_packet),metadataRaw=len(binary.dumps(meta).encode()),
            holdout=not is_training(record),unseenCandidate=candidate_bucket(record['columns'].get('candidate_id'))!=0,
            rich='encounterTelemetry' in result,
            status=outcome.get('status','NULL'),censored=result.get('censored'),ticks=result.get('ticks'),
            pending=record['columns']['result'] is None,escape=packet[12:14]==b'\xff\xff',
            candidates=record['columns'].get('candidate_id'),experiment=record['columns'].get('experiment_id'),
            links=len(record['links']),reused=any(r.get('reused') for r in record['links'])))
        packets.append((packet,meta_packet))
    if save:
        target=EVIDENCE/(filename+'.packed'); packed=pickle.dumps(packets,protocol=5)
        tmp=target.with_suffix('.tmp'); tmp.write_bytes(packed); tmp.replace(target)
        atomic_json(EVIDENCE/(filename+'.metrics.json'),dict(rows=measurements,timings=timings,
             definitionHash=DEFINITION_HASH,packedSha256=hashlib.sha256(packed).hexdigest()))
    return dict(rows=len(rows),timings=timings)

def summary(numbers):
    ordered=sorted(numbers)
    if not ordered: return None
    def percentile(p): return ordered[min(len(ordered)-1,int((len(ordered)-1)*p))]
    return dict(count=len(ordered),total=sum(ordered),mean=statistics.mean(ordered),min=ordered[0],
                p50=percentile(.5),p90=percentile(.9),p95=percentile(.95),p99=percentile(.99),max=ordered[-1])

def speed_pass(files,definitions,workers):
    start=time.perf_counter(); count=0; cpu={}
    with ProcessPoolExecutor(max_workers=workers,initializer=worker_init,initargs=(definitions,str(EVIDENCE))) as pool:
        for result in pool.map(process_shard,files,[False]*len(files),chunksize=1):
            count+=result['rows']
            for k,v in result['timings'].items(): cpu[k]=cpu.get(k,0)+v
    wall=time.perf_counter()-start
    return dict(workers=workers,rows=count,wallSecondsIncludingStartupLoadingAndParity=wall,rowsPerSecond=count/wall,
                summedIsolatedOperationSeconds=cpu)

def fresh_verify(files,definitions):
    decoder=binary.RecordEncoder(definitions); count=0; started=time.perf_counter()
    total=sum(s['rows'] for s in json.loads((OUT/'manifest.json').read_text())['shards'])
    for filename in files:
        records=pickle.loads((OUT/filename).read_bytes()); packets=pickle.loads((EVIDENCE/(filename+'.packed')).read_bytes())
        if len(records)!=len(packets): raise AssertionError('missing packet')
        for record,(packet,meta_packet) in zip(records,packets):
            value,meta,_=unpack(record); binary.exact(value,decoder.decode(packet)); binary.exact(meta,decoder.decode(meta_packet)); count+=1
        status('Fresh decoder verifying saved corpus packets',count,total)
    return dict(rows=count,exact=True,wallSeconds=time.perf_counter()-started)

def run(args):
    global EVIDENCE,DEFINITION_HASH
    manifest=extract(args.rows)
    status('Reading training rows; excluding holdout table',0,None)
    values,training_rows=load_training(manifest)
    status('Learning frozen definitions',0,None)
    encoder=binary.RecordEncoder.fit(values); del values
    definitions=encoder.definitions; raw_def=binary.dumps(definitions).encode(); compressed_def=zlib.compress(raw_def,9)
    DEFINITION_HASH=hashlib.sha256(raw_def).hexdigest()
    EVIDENCE=OUT/DEFINITION_HASH[:16]; EVIDENCE.mkdir(exist_ok=True)
    (OUT/'definitions.json').write_bytes(raw_def); (OUT/'definitions.zlib').write_bytes(compressed_def)
    files=[s['file'] for s in manifest['shards']]
    pilot_files=files[::max(1,len(files)//16)][:16]
    pilots=json.loads((EVIDENCE/'pilot.json').read_text()) if (EVIDENCE/'pilot.json').exists() else [speed_pass(pilot_files,definitions,w) for w in (1,4,12)]
    workers=max(pilots,key=lambda p:p['rowsPerSecond'])['workers']
    atomic_json(EVIDENCE/'pilot.json',pilots)
    completed=0; start=time.perf_counter()
    missing=[]
    for f in files:
        try:
            m=json.loads((EVIDENCE/(f+'.metrics.json')).read_text())
            valid=m['definitionHash']==DEFINITION_HASH and hashlib.sha256((EVIDENCE/(f+'.packed')).read_bytes()).hexdigest()==m['packedSha256']
        except (OSError,KeyError,ValueError): valid=False
        if not valid: missing.append(f)
    with ProcessPoolExecutor(max_workers=workers,initializer=worker_init,initargs=(definitions,str(EVIDENCE))) as pool:
        for result in pool.map(process_shard,missing,chunksize=1):
            completed+=result['rows']; status('Packing and proving exact parity',completed,manifest['rows'])
    wall=time.perf_counter()-start
    atomic_json(OUT/'encode-checkpoint.json',dict(rows=completed,wallSeconds=wall,workers=workers))
    rows=[]; timings={}
    for filename in files:
        m=json.loads((EVIDENCE/(filename+'.metrics.json')).read_text()); rows.extend(m['rows'])
        for k,v in m['timings'].items(): timings[k]=timings.get(k,0)+v
    fresh=fresh_verify(files,json.loads((OUT/'definitions.json').read_text()))
    status('Measuring complete corpus throughput',0,manifest['rows'])
    full_speed=speed_pass(files,definitions,workers)
    distributions={k:summary([r[k] for r in rows]) for k in ('raw','current','binary','metadata','metadataRaw')}
    groups={}
    for label,predicate in [('training',lambda r:not r['holdout']),('unseen-test',lambda r:r['unseenCandidate']),('nontraining-rows',lambda r:r['holdout']),('rich',lambda r:r['rich']),
                            ('development',lambda r:r['table']=='ea_sample'),('holdout-table',lambda r:r['table']=='ea_holdout'),
                            ('censored',lambda r:r['censored']),('pending',lambda r:r['pending'])]:
        selected=[r for r in rows if predicate(r)]
        groups[label]=dict(rows=len(selected),binary=summary([r['binary'] for r in selected]),
                          raw=summary([r['raw'] for r in selected]),current=summary([r['current'] for r in selected]),
                          escapes=sum(r['escape'] for r in selected))
    status_counts={}
    for r in rows: status_counts[str(r['status'])]=status_counts.get(str(r['status']),0)+1
    # Operation timers surround only the named operation, do not overlap, and
    # are summed across workers. The wall timing includes loading and parity.
    train_candidates={r['candidates'] for r in rows if not r['holdout']}
    test_candidates={r['candidates'] for r in rows if r['unseenCandidate']}
    report=dict(corpus=manifest,evidenceDirectory=str(EVIDENCE),trainingRows=training_rows,testRows=len(rows)-training_rows,
        trainingPolicy='Only ea_sample rows with candidate SHA256 bucket 0/5; no ea_holdout rows in training. Unseen candidate test uses the other four buckets, strictly candidate-disjoint.',
        candidateOverlapBetweenTrainingAndUnseenTest=len(train_candidates & test_candidates),
        parity=dict(allResultOutcomeFieldsExact=True,allRowAndLinkMetadataExact=True,
                    includesFloatBitsNullZeroAndSeedOrder=True,freshSavedPacketDecoder=fresh),
        frozenDefinitions=dict(rawBytes=len(raw_def),zlibBytes=len(compressed_def),schemas=len(definitions['schemas']),
            dictionaryEntries=len(definitions['dictionary']),amortizedBytesPerBattle=len(compressed_def)/len(rows),
            sha256=hashlib.sha256(raw_def).hexdigest()),
        distributions=distributions,groups=groups,statusCounts=status_counts,
        uniqueCandidates=len({r['candidates'] for r in rows}),
        payloadRatioToCurrent=distributions['binary']['total']/distributions['current']['total'],
        includingDefinitionsRatioToCurrent=(distributions['binary']['total']+len(compressed_def))/distributions['current']['total'],
        completeIncludingMetadataAndDefinitionsBytes=distributions['binary']['total']+distributions['metadata']['total']+len(compressed_def),
        completeRatioToCurrentPayloadPlusLogicalMetadata=(distributions['binary']['total']+distributions['metadata']['total']+len(compressed_def))/(distributions['current']['total']+distributions['metadataRaw']['total']),
        metadataBaselineNote='Exact retained row/link metadata compared as decoded compact JSON bytes, not SQLite physical pages or indexes. Payload-only ratio includes all definitions (including metadata definitions) conservatively.',
        worstExamples=sorted(rows,key=lambda r:r['binary'],reverse=True)[:8],
        fullRecordEscapes=sum(r['escape'] for r in rows),performancePilots=pilots,fullCorpusThroughput=full_speed,
        performance=dict(workers=workers,wallSeconds=wall,rowsCompletedThisRun=completed,
             rowsPerSecondIncludingLoadAndParity=completed/wall if wall else None,
             isolatedOperationSeconds=timings,
             isolatedOperationMicrosecondsPerBattle={k:v*1e6/len(rows) for k,v in timings.items()}),
        scope='Empirical deterministic stratified sample distributions, not a census or an unbiased random population estimate. Original JSON lexical spelling is not the parity target; every decoded field and type is.',
        unchangedSource=True)
    report['gatePassed']=fresh['rows']==manifest['rows'] and report['includingDefinitionsRatioToCurrent']<1 and report['completeRatioToCurrentPayloadPlusLogicalMetadata']<1 and groups['unseen-test']['rows']>10000 and report['candidateOverlapBetweenTrainingAndUnseenTest']==0
    atomic_json(OUT/'report.json',report)
    status('Complete: corpus evidence saved; shadow gate '+str(report['gatePassed']),len(rows),len(rows))
    print(json.dumps(dict(rows=len(rows),gate=report['gatePassed'],binary=distributions['binary'],ratio=report['includingDefinitionsRatioToCurrent'],definitions=report['frozenDefinitions'],speed=report['performance']),indent=2))

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--rows',type=int,default=50000)
    args=parser.parse_args(); OUT.mkdir(parents=True,exist_ok=True)
    try: run(args)
    except Exception as exc:
        status('Failed; checkpoints retained',0,None,str(exc)); raise
