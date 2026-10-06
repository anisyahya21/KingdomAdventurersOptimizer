"""Cell target snapshot survives nested damage and occupancy changes."""
import json
from itertools import product
from combat_initial_state import EVIDENCE
from combat_entities import CombatEntities
from combat_targeting import can_damage_shared, damage_cell
from combat_shared_resolution import dispatch_shared_attack

checks=0
for initially_eligible,remove_occupant,nested_lethal in product((False,True),(False,True),(False,True)):
    world=CombatEntities(100,[]);caster,a,b=[world.allocate() for _ in range(3)]
    world.add_component(caster,49,{})
    for identity in (a,b):
        world.add_component(identity,33,dict(parameters={10:dict(rawValue=10 if initially_eligible else 0,rawMax=10,extraValue=0)},extras=[]))
    occupants=[a,b];trace=[]
    def hp(identity):return world.fighter(identity)['parameters'][10]['rawValue']
    def eligible(identity):
        trace.append(('eligible',identity,hp(identity)))
        return can_damage_shared(world,caster,identity,{},lambda i:False,lambda i:False,lambda i:None)
    def change(unit,state):trace.append(('state',unit['id'],hp(unit['id'])))
    def attack(identity):
        trace.append(('attack',identity,hp(a),hp(b)))
        if identity==a:
            if nested_lethal:dispatch_shared_attack(world,[dict(target=b,hit=True,damage=20)],lambda r:None,change)
            if remove_occupant:occupants.remove(b)
        return dict(target=identity,hit=True,damage=3)
    def send(event,results):
        trace.append(('send',event))
        dispatch_shared_attack(world,results,lambda r:trace.append(('effect',r['target'],hp(a),hp(b))),change)
    damage_cell(occupants,eligible,attack,send)
    if initially_eligible:
        assert [e[1] for e in trace if e[0]=='eligible']==[a,a,b]
        assert [e[1] for e in trace if e[0]=='attack']==[a,b]
        assert (hp(a),hp(b))==(7,0 if nested_lethal else 7)
        sends=[index for index,e in enumerate(trace) if e[0]=='send']
        assert len(sends)==1 and all(index<sends[0] for index,e in enumerate(trace) if e[0]=='attack')
        # A subsequent cell pass sees new HP/occupancy, not the old snapshot.
        previous=len(trace);damage_cell([b],eligible,attack,send)
        assert any(e[0]=='send' for e in trace[previous:])==(not nested_lethal)
    else:
        assert trace==[('eligible',a,0),('eligible',b,0)]
    checks+=1
report=dict(sharedCellDamageCases=checks,
    scope='Any/filter rescan, stable target snapshot, nested HP changes, occupancy removal and later-cell eligibility.',
    limitations=['Portable composition check; native DamageEntitiesOnCell call sequence statically traced, LINQ bodies not executed here.',
                'Attack returns controlled damage; native Attack and HP/event ordering checked separately.'])
(EVIDENCE/'shared-cell-damage-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
print(json.dumps(report))
