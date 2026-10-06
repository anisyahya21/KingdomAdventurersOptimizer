"""Bounded actual store-reader output/performance comparison; no initialization/writes."""
import json,pathlib,time
import packed_library_trial as trial
import strategy_experiment_store as store

support=pathlib.Path('A:/KingdomAdventurersOptimizer/Community-Knowledge-20260928.packed-trial.sqlite.support')
info=json.loads((support/'report.json').read_text());s=trial.ro(info['source']);d=trial.ro(info['destination'])
high=s.execute('SELECT max(id) FROM ea_experiment').fetchone()[0]
ids=[s.execute('SELECT id FROM ea_experiment WHERE id>=? ORDER BY id LIMIT 1',(max(1,int(high*f)),)).fetchone()[0] for f in (0,.25,.5,.75,1)]
ids += [r[0] for r in s.execute('SELECT experiment_id FROM ea_sample_link GROUP BY experiment_id HAVING count(*) BETWEEN 256 AND 4096 ORDER BY count(*) DESC LIMIT 5')]
ids=list(dict.fromkeys(ids))
confirmation_high=s.execute('SELECT max(id) FROM ea_confirmation').fetchone()[0]
confirmation_ids=[s.execute('SELECT experiment_id FROM ea_confirmation WHERE id>=? ORDER BY id LIMIT 1',(max(1,int(confirmation_high*f)),)).fetchone()[0] for f in (0,.25,.5,.75,1)]
report={'state':'PASS','readers':{},'cache':'not controlled; sequential isolated calls after full validation'}
for name,function in (('outcomes',store.outcomes),('confirmation_report',store.confirmation_report)):
    selected=ids if name=='outcomes' else confirmation_ids
    times={};values=[]
    for label,db in (('source',s),('destination',d)):
        started=time.monotonic();results=[function(db,i) for i in selected];times[label+'Seconds']=time.monotonic()-started;values.append(results)
    trial.binary.exact(values[0],values[1])
    times['experimentIds']=selected;times['parity']='PASS';times['outputRowsOrReports']=sum(len(v) if isinstance(v,list) else 1 for v in values[0])
    report['readers'][name]=times
trial.atomic(support/'reader-api-check.json',report);print(json.dumps(report));s.close();d.close()
