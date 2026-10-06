"""Area/reflect ordering and generated Wairo subset combat, without projectiles."""
from pathlib import Path
from combat_farmer_slice import ROWS
exec(Path(__file__).with_name('check_combat_shared_controllers.py').read_text(encoding='utf-8').split('cases=[]')[0])

# Same-cell targets force the native per-cell snapshot/batch boundary to matter.
specs=fixture(1000,False,False)[:2]
specs[0]['skills']=[20];specs[0]['levels']=[1]
specs[1]['grid']=0;specs[1]['cell']=[0,4]
source=fixture(1000,False,False)[2]
source['name']='area_source';source['skills']=[35];source['levels']=[1]
source['parameters'][10].update(rawValue=1,rawMax=1)
source['parameters'][11].update(rawValue=1000,rawMax=1000)
engine=SharedControllers(specs+[source],1,2,front_targets)
caster=engine.names['area_source'];first=engine.names['farmer'];second=engine.names['healer']
engine.math.critical=lambda i:False;engine.math.hit=lambda i,t:True;engine.math.damage=lambda *a:10
engine.invoke=lambda *a:True
engine.enqueue(caster,35,None,'controlled_order_probe')
command=engine.units[caster]['commands'][0]
engine.use(engine.units[caster],command,0)
assert engine.value(caster,10)==0
assert engine.value(first,10)==4990 and engine.value(second,10)==4990
batch=next(e for e in engine.trace if e['kind']=='attack_batch' and len(e['attacks'])==2)
outer=[engine.trace[i] for i in batch['attacks']]
assert [e['target'] for e in outer]==[first,second]
reflected=next(e for e in engine.trace if e['kind']=='attack' and e.get('route')=='reflection')
assert reflected['damage']==5 and reflected['hpAfter']==0
assert reflected['id']<outer[0]['id']<outer[1]['id']<batch['id']
# Zero HP in Damaging still satisfies area availability, but cannot receive area damage.
for i in (first,second):
    engine.param(i,10)['rawValue']=0;engine.units[i]['board'][5]=6
assert engine.opponent_in_range(caster,ROWS[35])
before=sum(e['kind']=='attack' for e in engine.trace)
engine.hit_cell(caster,ROWS[35],(0,4),command,0)
assert sum(e['kind']=='attack' for e in engine.trace)==before
for i in (first,second):engine.units[i]['board'][5]=8
assert not engine.opponent_in_range(caster,ROWS[35])
# Departure loses the caster front-row gate even with a living opponent nearby.
engine.param(first,10)['rawValue']=1;engine.units[first]['board'][5]=3
engine.units[caster]['board'][7]=100
assert not engine.eligible(caster,ROWS[35],None)

examples=[]
baseline=special_enemy_baseline(19,0,lambda n:0)
for seed in (1,7,31,93):
    roster=fixture(1000,True,False)[:2]
    for index,monster in enumerate((142,116,121,114)):
        original=next(f for f in baseline['fighters'] if f['monsterId']==monster)
        roster.append(dict(name=f'monster{monster}',human=False,team=1,grid=index,cell=[index,3],
            boss=monster==142,skills=original['skills']['dataIds'],levels=original['skills']['invocationLevels'],
            parameters={int(k):v for k,v in deepcopy(original['parameters']).items()}))
    lab=SharedControllers(roster,seed,seed+1,front_targets)
    result=lab.run(3000)
    assert any(e['kind']=='area_cell' for e in result['trace'])
    for event in result['trace']:
        if event['kind']=='attack_batch':assert all(i<event['id'] for i in event['attacks'])
        elif event['kind']=='mp':assert event['before']>=event['amount'] and event['after']>=0
    examples.append(dict(seed=seed,prizeCallbacks=result['prizeCallbacks'],
        areaCells=sum(e['kind']=='area_cell' for e in result['trace']),
        reflections=sum(e['kind']=='attack' and e.get('route')=='reflection' for e in result['trace'])))
report=dict(controlledOrderingProbe=True,generatedWairoSubsetCases=len(examples),ticks=12000,
    examples=examples,orderingTrace=engine.trace,
    findings=['A lethal nested reflection is delivered before the outer area batch, without canceling already-snapshotted targets.',
              'Zero-HP Damaging opponents can permit area use but fail its separate damage filter.',
              'Wairo Tank loses area eligibility after its grid moves out of the front row.'],
    limits=['Controlled ordering probe forces damage10 and reflection invocation; it is not a statistical result.',
            'Generated subset uses original Wairo19 defeatCount0 parameters for four selected monsters; remaining followers omitted and formation supplied.',
            'No movement/projectiles/effect RNG/source-world receipt; prize counts are not encounter predictions.'])
(EVIDENCE/'area-reflection-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
print(json.dumps({k:v for k,v in report.items() if k not in ('orderingTrace','examples','limits')}))
