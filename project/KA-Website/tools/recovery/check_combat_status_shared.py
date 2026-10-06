"""Joined status phase: expiry after charging, queue continuity and Skill subset gates."""
from pathlib import Path
exec(Path(__file__).with_name('check_combat_shared_controllers.py').read_text().split('cases=[]')[0])
from combat_farmer_slice import ROWS
sleep=next(s['id'] for s in ROWS.values() if s['type']==67)
lab=SharedControllers(fixture(1000,False,False)[:3],1,2,front_targets)
farmer=lab.units[lab.names['farmer']]
farmer['board'].update({5:3,8:0,62:sleep,63:1,64:19})
original_update=lab.update
lab.update=lambda u,s:original_update(u,s) if u['id']==farmer['id'] else None
lab.decide=lambda u:3
lab.run(1)
assert farmer['board'][8]==0 and all(k not in farmer['board'] for k in (62,63,64))
lab.run(1)
assert farmer['board'][8]==1
# Sleep does not suspend a retained multi-hit command's private clock.
farmer['board'].update({62:sleep,63:5,64:0})
lab.enqueue(farmer['id'],22,lab.names['knight'],'controlled_probe')
command=farmer['commands'][0]
lab.run(3)
assert command['tick']==3 and farmer['board'][64]==3
assert farmer['board'][8]==1
assert any(e['kind']=='animation_request' for e in lab.trace)
# Native Skill subset update has no HP/Leaving-state gate.
lab.update=lambda *a:None
farmer['commands'].clear();farmer['board'].update({5:8,62:sleep,63:1,64:19})
lab.param(farmer['id'],10)['rawValue']=0
lab.run(1)
assert 62 not in farmer['board']
# An AI without Skill component is outside this system's subset.
farmer['board'].update({62:sleep,63:1,64:19})
lab.world.remove_component(farmer['id'],51)
lab.run(1)
assert farmer['board'][64]==19
from check_combat_sandbox import example
from combat_sandbox import run_scenario
data=example();data['tickLimit']=2
base=run_scenario(data)
data['prePlacement']={name:dict(cell=[100,100],position=[2400.,0.,2400.],offset=[0.,0.,0.],
    board={4:0,5:1,6:0,7:0,8:0},longBoard={}) for name in base['result']['names']}
data['prePlacement']['synthetic farmer']['board'].update({62:sleep,63:1,64:19})
result=run_scenario(data,True)
assert any(e['kind']=='status_tick' and e['target']==result['result']['names']['synthetic farmer']
           and e['tick']==0 and e['phase']=='skills' and e['after']=={} for e in result['trace'])
report=dict(joinedStatusCases=5,scope='Controlled shared loop and explicit source status input; native status arithmetic and charging are compared separately')
(EVIDENCE/'status-shared-checks.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report))
