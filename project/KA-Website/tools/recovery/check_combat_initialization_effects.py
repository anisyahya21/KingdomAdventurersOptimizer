"""Joined pre-placement sensitivity, attack effect ordering and lifetimes."""
from pathlib import Path
exec(Path(__file__).with_name('check_combat_shared_controllers.py').read_text(encoding='utf-8').split('cases=[]')[0])

specs=fixture(1000,False,False)[:3]
for s in specs:
    s['prePlacement']=dict(cell=[100,100],position=[2400.,0.,2400.],offset=[1.,2.,3.],
        board={4:99,5:0,6:99,7:99,8:99,88:123},longBoard={90:456})
class Observed(SharedControllers):
    def decide(self,u):
        self.emit('initial_observe',target=u['id'],cells=[list(t['cell']) for t in self.units.values()])
        return super().decide(u)
lab=Observed(specs,1,2,initialization_orders=[[1,0],[0]])
observations=[e for e in lab.trace if e['kind']=='initial_observe']
assert observations[0]['cells']==[[0,4],[0,5],[100,100]]
assert observations[1]['cells']==[[0,4],[0,5],[100,100]]
assert observations[2]['cells']==[[0,4],[0,5],[0,3]]
assert [u['id'] for u in lab.teams[0]]==[lab.names['farmer'],lab.names['healer']]
assert all(u['board'][88]==123 and u['long_board'][90]==456 and list(u['offset'])==[1.,2.,3.] for u in lab.units.values())

# A rear attacker can see a skill target before placement, or see none.
outcomes=[]
for cell in ([0,4],[100,100]):
    scenario=deepcopy(specs)
    scenario[1]['skills']=[22];scenario[1]['levels']=[1]
    scenario[2]['prePlacement']['cell']=cell
    scenario[2]['prePlacement']['position']=[cell[0]*24.,0.,cell[1]*24.]
    engine=SharedControllers(scenario,1,2)
    outcomes.append(engine.units[engine.names['healer']]['board'][5])
assert outcomes==[3,1],outcomes

effects=[]
for hit,critical in ((False,False),(True,False),(True,True)):
    lab=SharedControllers(fixture(1000,False,False)[:3],1,2)
    lab.update=lambda *a:None
    i,t=lab.names['farmer'],lab.names['knight']
    lab.math.critical=lambda *a:critical;lab.math.hit=lambda *a:hit;lab.math.damage=lambda *a:10
    lab.attack(i,t,None)
    births=[e for e in lab.trace if e['kind']=='effect_birth']
    assert [e['lifetime'] for e in births]==([30] if not hit else [9,30,30] if critical else [9,30])
    if hit:
        state=next(e['id'] for e in lab.trace if e['kind']=='state' and e['id']>births[0]['id'])
        assert all(e['id']<state for e in births)
    for tick in range(30):
        lab.run(1)
        for birth in births:assert lab.world.is_destroyed(birth['effect'])==(tick>=birth['lifetime']-1)
    assert not list(lab.effects.members) and not list(lab.garbage.members)
    effects.append(dict(hit=hit,critical=critical,effects=len(births)))
report=dict(sequentialTeamObservation=True,retainedCloneFields=True,initialStateSensitivity=outcomes,effectCases=effects,
    limits=['Explicit synthetic pre-placement state; no save extraction or whole native encounter replay.',
            'Controlled single attacks; native dispatch/resource/lifetime comparisons are separate checks.'])
(EVIDENCE/'initialization-effects-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');print(json.dumps(report))
