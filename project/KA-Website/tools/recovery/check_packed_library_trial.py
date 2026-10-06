"""Real-schema bounded interruption/resume + typed parity/fallback safety check."""
import hashlib,json,pathlib,sqlite3,subprocess,sys,tempfile
import packed_library_trial as trial

def main():
    source=pathlib.Path('A:/KingdomAdventurersOptimizer/Community-Knowledge-20260928.sqlite')
    work=pathlib.Path(tempfile.mkdtemp(prefix='packed-trial-check-',dir=trial.HERE/'studies/battle_binary_corpus'))
    fixture=work/'fixture.sqlite'; dest=work/'fixture.packed-trial.sqlite'
    s=trial.ro(source); d=sqlite3.connect(fixture)
    objects=s.execute('SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type,name').fetchall()
    for kind,name,sql in objects:
        if kind=='table' and not name.startswith('sqlite_'): d.execute(sql)
    for (table,) in s.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall():
        columns=[r[1] for r in s.execute('PRAGMA table_info('+trial.q(table)+')')]
        keys=trial.row_keys(s,table)
        rows=s.execute('SELECT '+('' if keys else 'rowid,')+','.join(map(trial.q,columns))+' FROM '+trial.q(table)+' ORDER BY '+(','.join(map(trial.q,keys)) if keys else 'rowid')+' LIMIT 16').fetchall()
        sql='INSERT INTO '+trial.q(table)+'('+('' if keys else 'rowid,')+','.join(map(trial.q,columns))+') VALUES('+','.join('?' for _ in range(len(columns)+(0 if keys else 1)))+')'
        d.executemany(sql,rows)
    # Preserve the actual registry/schema plus explicit lossless edge values.
    values=[{'unknown':[None,0,False,True,-0.0,1.125,2**200],'historicalOutcome':'accepted'},
            {'status':'future-state','earned':0,'censored':None}]
    for table in trial.PAYLOAD_TABLES:
        ids=[r[0] for r in d.execute('SELECT rowid FROM '+trial.q(table)+' ORDER BY rowid LIMIT 3')]
        d.execute('UPDATE '+trial.q(table)+' SET result=?,outcome=? WHERE rowid=?',
                  (json.dumps(values[0],sort_keys=True,separators=(',',':')),json.dumps(values[1],sort_keys=True,separators=(',',':')),ids[0]))
        d.execute('UPDATE '+trial.q(table)+' SET result=?,outcome=? WHERE rowid=?',
                  ('{"a":1, "a":2}', '{"earned":0.00}',ids[1]))
        d.execute('UPDATE '+trial.q(table)+' SET result=NULL,outcome=NULL WHERE rowid=?',(ids[2],))
    d.execute('DELETE FROM sqlite_sequence')
    d.executemany('INSERT INTO sqlite_sequence(rowid,name,seq) VALUES(?,?,?)',s.execute('SELECT rowid,name,seq FROM sqlite_sequence'))
    for kind,name,sql in objects:
        if kind in ('index','trigger','view'): d.execute(sql)
    d.commit(); d.close();s.close()
    before=hashlib.sha256(fixture.read_bytes()).hexdigest()
    cmd=[sys.executable,str(trial.HERE/'packed_library_trial.py'),'--source',str(fixture),'--destination',str(dest),'--workers','2','--batch','8','--headless']
    interrupted=subprocess.run(cmd+['--stop-after-batches','1'],capture_output=True,text=True)
    assert interrupted.returncode!=0 and not dest.exists(),interrupted.stdout+interrupted.stderr
    assert dest.with_name(dest.stem+'.incomplete.sqlite').exists()
    support=dest.with_name(dest.name+'.support')
    assert not (support/'VERIFIED-COMPLETE.json').exists()
    resumed=subprocess.run(cmd+['--resume'],capture_output=True,text=True)
    assert resumed.returncode==0,resumed.stdout+resumed.stderr
    assert hashlib.sha256(fixture.read_bytes()).hexdigest()==before
    report=json.loads((support/'report.json').read_text())
    assert report['exactParity']=='PASS' and report['migratedBattles']==32
    assert report['population']['counts']['wholeLayoutFallbackCells']>=4
    assert report['population']['counts']['exactTextFallbackCells']>=4
    assert (support/'VERIFIED-COMPLETE.json').exists()
    measured=dest.stat().st_size+sum(p.stat().st_size for p in support.rglob('*') if p.is_file())
    assert measured==report['completeFootprintBytesAtMeasurement'],(measured,report['completeFootprintBytesAtMeasurement'])
    summary={'state':'PASS','work':str(work),'resume':'interrupted after real committed batch; final absent; resumed exact complete comparison passed',
             'typedPayloads':'unknown layouts, negative zero, null/zero/bools, big int, duplicate key spelling, noncanonical floats',
             'sourceUnchanged':True,'allTables':len(report['sourceInventory']['tables']),'battleRows':32,'footprintExact':True}
    trial.atomic(trial.HERE/'studies/battle_binary_corpus/full-trial-safety-check.json',summary)
    print(json.dumps(summary))

if __name__=='__main__': main()
