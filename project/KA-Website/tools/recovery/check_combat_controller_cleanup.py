"""Joined controller cleanup distinguishes arrows, other shots and fighter bodies."""
from pathlib import Path
exec(Path(__file__).with_name('check_combat_shared_controllers.py').read_text(encoding='utf-8').split('cases=[]')[0])
from combat_farmer_slice import ROWS
cases=[]
for sid in (1,2,17):
    lab=SharedControllers(fixture(1000,False,False)[:3],1,2,front_targets)
    caster=lab.names['knight'];target=lab.names['farmer']
    # Only the projectile lifecycle runs; no generated fighter decisions.
    lab.update=lambda *a:None
    lab.fire_skill_projectiles(caster,target,ROWS[sid],dict(traceId=None),0)
    shots=[e['projectile'] for e in lab.trace if e['kind']=='projectile_launch']
    impact_ticks={};destroy_ticks={};trail_births={};trail_deaths={}
    for tick in range(100):
        lab.run(1)
        for event in lab.trace:
            if event['kind']=='projectile_impact':impact_ticks.setdefault(event['projectile'],event['tick'])
            if event['kind']=='effect_birth' and event['type']==17:
                trail_births.setdefault(event['effect'],event['tick'])
                assert event['phase']=='projectiles' and event['lifetime']==10
        for trail,born in trail_births.items():
            if lab.world.is_destroyed(trail):trail_deaths.setdefault(trail,lab.tick)
            else:
                components=lab.world.objects[trail]['components']
                assert components[5] is None and components[4] is None
                assert components[32][0]==9-(lab.tick-born)
        for shot in shots:
            if lab.world.is_destroyed(shot):destroy_ticks.setdefault(shot,lab.tick)
    assert set(impact_ticks)==set(destroy_ticks)==set(shots)
    delay=20 if sid==1 else 0
    assert all(destroy_ticks[i]-impact_ticks[i]==delay for i in shots)
    assert all(i not in bucket for i in shots for bucket in lab.occupancy.buckets.values())
    assert all(lab.world.objects[i]['components']==[None]*52 for i in shots)
    assert trail_births and trail_births.keys()==trail_deaths.keys()
    assert all(trail_deaths[i]-born==9 for i,born in trail_births.items())
    cases.append(dict(skill=sid,shots=len(shots),destroyDelay=delay,trails=len(trail_births)))
lab=SharedControllers(fixture(1000,False,False)[:3],1,2,front_targets)
lab.update=lambda *a:None
body=lab.units[lab.names['knight']]
lab.body_flight(body,10,tuple(body['position']),tuple(body['position']))
lab.run(30)
assert not lab.world.is_destroyed(body['id']) and body['components'][39] is None
assert body['id'] in lab.occupancy.buckets[lab.units[body['id']]['cell'][0]+16*lab.units[body['id']]['cell'][1]]
report=dict(projectileCases=cases,bodySurvives=True,scope='Controlled composed lifecycle; no full native-world replay')
(EVIDENCE/'controller-cleanup-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
print(json.dumps(report))
