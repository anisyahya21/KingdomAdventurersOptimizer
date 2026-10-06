"""Defeat state/command/projectile composition for the special-fight branch."""
import json
from itertools import product
from combat_initial_state import EVIDENCE
from combat_entities import CombatEntities
from combat_collections import ComponentSubset
from combat_shared_resolution import dispatch_shared_attack, execute_shared_skill_commands
from combat_states import (change_fighter_state, enter_damaging, update_damaging, exit_damaging,
    enter_knocking_down, update_knocking_down, enter_leaving_special, update_leaving)
from combat_projectiles import fire_shared_projectile, tick_projectiles, process_projectile_impact
from combat_spatial import update_positions, update_cells
from combat_tick import update_fighters

skill=next(row for row in json.loads((EVIDENCE/'weapon-skill-profiles.json').read_text(encoding='utf-8'))['skills'] if row['id']==23)
examples=[]
for human,team,start_tick in product((False,True),(0,1),(0,4,9)):
    projectiles=ComponentSubset((39,0,1));moves=ComponentSubset((0,1));cells=ComponentSubset((5,0,1))
    world=CombatEntities(100,[projectiles,moves,cells]);identity=world.allocate()
    world.add_component(identity,'AI',dict(board={4:0,5:3,6:team,7:0},long_board={},commands=[],path=[]))
    world.add_component(identity,'Position',[0.,0.,96. if team==0 else 72.,0.,0.,0.,None])
    world.add_component(identity,'Speed',[0.,0.,0.]);world.add_component(identity,'Cell',[0,4 if team==0 else 3])
    world.add_component(identity,'Direction',[0 if team==0 else 2]);world.add_component(identity,'Seb',[0,0,0,-1])
    world.add_component(identity,'Animation',[1,-1]);world.add_component(identity,'Parameter',dict(parameters={10:dict(rawValue=5,rawMax=100)}))
    fighter=world.fighter(identity);command=dict(opcode=29,target=999,skill=23,tick=start_tick,duration=34,use_index=0)
    fighter['commands'].append(command);events=[];tick=-1;defeat_seen=None
    def animate(unit,behavior):events.append(('animation',tick,behavior))
    def fire(unit,speed,start,end):
        events.append(('launch',tick,speed,list(start),list(end)))
        fire_shared_projectile(world,unit['id'],start,end,speed,unit['id'])
    def smoke(unit):events.append(('smoke',tick))
    def sound(sound_id):events.append(('sound',tick,sound_id))
    def enter_knock(unit):
        enter_knocking_down(unit,[fighter],3,lambda u:u['commands'].clear(),fire,animate,smoke,sound)
    def enter_leave(unit):
        enter_leaving_special(unit,3,lambda u:not human,lambda u:True,fire,smoke,
                              lambda:events.append(('prize',tick)),sound)
    def change(unit,state):
        change_fighter_state(unit,state,{3:lambda u:None,6:lambda u:exit_damaging(u,3),7:lambda u:None},
                             {6:lambda u:enter_damaging(u,animate),7:enter_knock,8:enter_leave})
        events.append(('state',tick,state))
    dispatch_shared_attack(world,[dict(target=identity,hit=True,damage=10)],lambda r:None,change)
    def state_update(unit,state):
        if state==6:
            update_damaging(unit,lambda u:u['parameters'][10]['rawValue'],lambda u:human,lambda u:not human,
                            lambda u:team==1,lambda:False,lambda u:3,
                            lambda u:events.append(('monster_defeated',tick)),change)
        elif state==7:update_knocking_down(unit,animate,change)
        elif state==8:update_leaving(unit)
        else:raise AssertionError(state)
    def impact(unit_id):
        events.append(('impact',tick,list(fighter['position'])))
        process_projectile_impact(unit_id,dict(destroyed=world.is_destroyed,has_cell=lambda i:True,
            owner=lambda i:world.objects[i]['components'][39]['owner'],alive=lambda i:not world.is_destroyed(i),
            fighter=lambda i:True,has_attack=lambda i:False,terrain_at_cell=lambda i:None))
    for tick in range(180):
        # Battle's all-state8 predicate precedes Fighter.Update in system order.
        if defeat_seen is None and fighter['board'][5]==8:defeat_seen=tick
        update_fighters([[fighter]],2,state_update,
                        lambda unit:execute_shared_skill_commands(world,identity,lambda cmd:skill,animate,
                            lambda u,cmd,index:events.append(('use',tick,index,cmd['target']))))
        tick_projectiles(projectiles.members,world,lambda i,dy:None,
                         lambda *args:(_ for _ in ()).throw(AssertionError('Departure has no Attack component')),impact)
        update_positions(moves.members,world)
        update_cells(cells.members,world,64,24,24,lambda *args:None)
    assert fighter['parameters'][10]['rawValue']==0 and not world.is_destroyed(identity)
    assert fighter['board'][5]==8 and not fighter['commands']
    assert defeat_seen==(108 if human else 7)
    released=[event[1] for event in events if event[0]=='use']
    expected=[t for t in range(35) if (start_tick+t)%5==1][:3]
    if human:expected=[t for t in expected if t<6]
    assert released==expected,(human,team,start_tick,released,expected)
    assert [(e[1],e[2]) for e in events if e[0]=='state']==([(-1,6),(6,7),(107,8)] if human else [(-1,6),(6,8)])
    assert len([e for e in events if e[0]=='launch'])==(2 if human else 1)
    assert len([e for e in events if e[0]=='impact'])==(2 if human else 1)
    assert len([e for e in events if e[0]=='monster_defeated'])==int(not human and team==1)
    assert len([e for e in events if e[0]=='prize'])==int(not human and team==1)
    assert world.objects[identity]['components'][39] is None
    examples.append(dict(human=human,team=team,initialCommandTick=start_tick,defeatObservedTick=defeat_seen,events=events))
report=dict(sharedDepartureCases=len(examples),logicalTicks=len(examples)*180,examples=examples,
    limits=['Portable composition of separately native-checked phases; not a full original-world replay.',
            'Lethal input injected before tick0; stored skill releases recorded without UseSkill/target effects.',
            'Projectile rotation, smoke, sound playback/RNG and prize selection are supplied; absent terrain.',
            'All-state8 observation is recorded; full Battle ending/rewards/teardown not executed.'])
(EVIDENCE/'shared-departure-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
print(json.dumps({k:v for k,v in report.items() if k!='examples'}))
