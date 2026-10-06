"""Projectile retarget-at-impact probe and all original roster smoke runs."""
from pathlib import Path
exec(Path(__file__).with_name('check_combat_shared_controllers.py').read_text(encoding='utf-8').split('cases=[]')[0])
from combat_farmer_slice import ROWS
from combat_projectiles import tick_projectiles, skill_projectile_destinations
from combat_spatial import update_positions, update_cells, cell_key

specs=fixture(1000,False,False)[:3]
specs[2]['skills']=[17];specs[2]['levels']=[1]
lab=SharedControllers(specs,1,2,front_targets)
caster=lab.names['knight'];target=lab.names['farmer'];replacement=lab.names['healer']
lab.math.critical=lambda i:False;lab.math.hit=lambda i,t:True;lab.math.damage=lambda *a:10
lab.enqueue(caster,17,target,'controlled_projectile_probe')
command=lab.units[caster]['commands'][0]
lab.use(lab.units[caster],command,0)
launches=[e for e in lab.trace if e['kind']=='projectile_launch']
assert len(launches)==5
assert [tuple(e['end']) for e in launches]==skill_projectile_destinations((0.,0.,96.),2)
# Move the original target away, put another fighter in its launch-time cell,
# and set owner HP0 without destroying the owner entity.
old=cell_key(*lab.units[target]['cell'],lab.map_width)
lab.units[target]['position'][0]=2400.;lab.units[target]['cell'][0]=100
lab.occupancy.changed(target,old)
old=cell_key(*lab.units[replacement]['cell'],lab.map_width)
lab.units[replacement]['position'][2]=96.;lab.units[replacement]['cell'][1]=4
lab.occupancy.changed(replacement,old)
lab.param(caster,10)['rawValue']=0
for tick in range(60):
    lab.tick=tick;lab.phase='projectiles'
    tick_projectiles(lab.projectiles.members,lab.world,lambda *a:None,lambda *a:None,lab.impact_projectile)
    update_positions(lab.moving.members,lab.world)
    update_cells(lab.cells.members,lab.world,lab.map_width,24,24,lab.occupancy.changed)
assert lab.value(target,10)==5000 and lab.value(replacement,10)==4990
assert not list(lab.projectiles.members)
assert len([e for e in lab.trace if e['kind']=='projectile_impact'])==5

cases=[];projectile_count=0
for encounter in range(20):
    baseline=special_enemy_baseline(encounter,0,lambda n:0)
    offset=max(3,len(baseline['fighters'])//5+1)
    roster=fixture(1000,True,False)[:2]
    for own in roster:own['cell'][1]+=offset-3
    for original in baseline['fighters']:
        roster.append(dict(name=f"enemy{original['incomingIndex']}",human=False,team=1,
            grid=original['grid'],cell=original['cell'],boss=original['leaderIdentity'],monsterType=0,
            skills=original['skills']['dataIds'],levels=original['skills']['invocationLevels'],
            parameters={int(k):v for k,v in deepcopy(original['parameters']).items()}))
    engine=SharedControllers(roster,7,8,front_targets,row_offset=offset)
    # All original follower acceptance rates are100; account for their supplied
    # successful constructor draws on the Lib stream before combat starts.
    for _ in range(baseline['followerSelectionDraws']):engine.next_lib()
    result=engine.run(1000)
    shots=sum(e['kind']=='projectile_launch' for e in result['trace']);projectile_count+=shots
    assert len(result['units'])==len(baseline['fighters'])+2
    for event in result['trace']:
        if event['kind']=='mp':assert event['before']>=event['amount'] and event['after']>=0
        elif event['kind']=='attack':assert event['hpAfter']>=0
    cases.append(dict(encounterId=encounter,enemies=len(baseline['fighters']),projectiles=shots,
        impacts=sum(e['kind']=='projectile_impact' for e in result['trace']),
        attacks=sum(e['kind']=='attack' for e in result['trace']),verdict=result['verdict']))
assert sum(c['enemies'] for c in cases)==270 and projectile_count>0
report=dict(fullRosterSmokeCases=len(cases),ticks=20000,enemies=270,projectiles=projectile_count,cases=cases,
    controlledReplacementProbe=True,probeTrace=lab.trace,
    limits=['All original roster members/parameters/skills are loaded, but movement decisions and full initialization are not simulated.',
            'Two synthetic own fighters, fixed normal-front geometry, explicit monster type0 for special-combat eligibility.',
            'No departure flight/effect allocation/RNG/garbage disposal/source-world receipt/teardown; no faithful full-fight yield claim.',
            'Replacement probe supplies damage10 and explicit position/HP mutations; generated roster runs use recovered damage RNG.'])
(EVIDENCE/'projectile-controller-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
print(json.dumps({k:v for k,v in report.items() if k not in ('cases','probeTrace','limits')}))
