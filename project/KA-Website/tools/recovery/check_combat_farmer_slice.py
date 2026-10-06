"""Integration invariants for generated direct farmer actions, not yield fitting."""
import json
from itertools import product
from combat_farmer_slice import run_farmer_slice
from combat_initial_state import EVIDENCE

cases=[]
for seed,mp,dex,gap in product((1,7,31),(0,37,1000),(0,500),(0,3,12,40)):
    result=run_farmer_slice(seed=seed,mp=mp,dex=dex,incoming_gap=gap)
    events=result['trace']
    payments=[e for e in events if e['kind']=='mp']
    assert 0<=result['finalMP']<=mp
    assert mp-sum(e['before']-e['after'] for e in payments)==result['finalMP']
    assert all(e['before']>=e['amount'] and e['after']==e['before']-e['amount'] for e in payments)
    commands={e['commandId']:e for e in events if e['kind']=='enqueue'}
    for event in events:
        if event['kind']=='release':
            assert event['target']==commands[event['commandId']]['target']
        if event['kind']=='attack':
            assert not event['critical'] or event['hit']
            if not event['hit']:assert event['hpAfter']==event['hpBefore']
            if event['commandId'] is not None:
                assert event['target']==commands[event['commandId']]['target']
        if event['kind']=='prize':
            cause=events[event['causeAttack']]
            assert cause['kind']=='attack' and cause['hit'] and cause['target']==event['target']
            assert event['hp']==0
    if mp==0:assert not commands and not payments
    if gap==0:assert all(e['source']=='charge' for e in commands.values())
    result['failedReleases']=sum(e['kind']=='release' and not e['used'] for e in events)
    cases.append({k:v for k,v in result.items() if k not in ('trace','limits')})

# Exact replay of this portable slice includes its event ordering and RNG calls.
example=run_farmer_slice(seed=7,mp=37,incoming_gap=12)
assert example==run_farmer_slice(seed=7,mp=37,incoming_gap=12)
assert any(c['failedReleases'] for c in cases)
report=dict(cases=len(cases),ticks=sum(c['ticks'] for c in cases),results=cases,
    example=example,limits=example['limits']+[
        'These are composition invariants and deterministic repeatability, not a joined native differential replay.',
        'No stat threshold or setup recommendation follows from synthetic slice prize counts.'])
(EVIDENCE/'farmer-slice-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
print(json.dumps(dict(cases=len(cases),ticks=report['ticks'],
    casesWithFailedReleases=sum(c['failedReleases']>0 for c in cases))))
