"""Controlled zero-HP boss re-entry schedules; not a gear/setup optimizer."""
import json
from itertools import product
from combat_initial_state import EVIDENCE
from combat_entities import CombatEntities
from combat_collections import ComponentSubset
from combat_shared_resolution import dispatch_shared_attack
from combat_states import change_fighter_state, enter_damaging, update_damaging, exit_damaging, enter_leaving_special, update_leaving
from combat_projectiles import fire_shared_projectile, tick_projectiles
from combat_spatial import update_positions
from combat_tick import update_fighters

examples=[]
for spacing,before,miss_alternates in product(range(1,17),(False,True),(False,True)):
    projectiles=ComponentSubset((39,0,1));moving=ComponentSubset((0,1))
    world=CombatEntities(100,[projectiles,moving]);boss=world.allocate()
    world.add_component(boss,'AI',dict(board={4:0,5:3,6:1,7:0},long_board={},commands=[],path=[]))
    world.add_component(boss,'Position',[0.,0.,72.,0.,0.,0.,None]);world.add_component(boss,'Speed',[0.,0.,0.])
    world.add_component(boss,'Direction',[2]);world.add_component(boss,'Parameter',dict(parameters={10:dict(rawValue=1,rawMax=1)}))
    unit=world.fighter(boss);rewards=[];transitions=[];impacts=[];tick=-1
    def fire(fighter,speed,start,end):fire_shared_projectile(world,boss,start,end,speed,boss)
    def leave(fighter):
        enter_leaving_special(fighter,3,lambda u:True,lambda u:True,fire,lambda u:None,
                              lambda:rewards.append(tick),lambda sound:None)
    def change(fighter,state):
        old=fighter['board'][5]
        change_fighter_state(fighter,state,{3:lambda u:None,6:lambda u:exit_damaging(u,3),8:lambda u:None},
                            {6:lambda u:enter_damaging(u,lambda *args:None),8:leave})
        transitions.append((tick,old,state))
    def update(fighter,state):
        if state==6:
            update_damaging(fighter,lambda u:u['parameters'][10]['rawValue'],lambda u:False,lambda u:True,
                lambda u:True,lambda:False,lambda u:3,lambda u:None,change)
        elif state==8:update_leaving(fighter)
    attempted=list(range(0,81,spacing))
    landed=[t for i,t in enumerate(attempted) if not miss_alternates or i%2==0]
    def deliver():
        if tick in attempted:
            dispatch_shared_attack(world,[dict(target=boss,hit=tick in landed,damage=1)],lambda result:None,change)
    for tick in range(120):
        if before:deliver()
        update_fighters([[unit]],2,update,lambda u:None)
        if not before:deliver()
        tick_projectiles(projectiles.members,world,lambda *args:None,lambda *args:None,lambda identity:impacts.append(tick))
        update_positions(moving.members,world)
    expected=[]
    for i,t in enumerate(landed):
        due=t+(6 if before else 7)
        following=landed[i+1] if i+1<len(landed) else 10000
        if (due<following if before else due<=following):expected.append(due)
    assert rewards==expected,(spacing,before,miss_alternates,rewards,expected)
    assert unit['parameters'][10]['rawValue']==0 and not world.is_destroyed(boss)
    assert len([e for e in transitions if e[2]==8])==len(rewards)
    examples.append(dict(attemptSpacing=spacing,delivery='before boss update' if before else 'after boss update',
        alternatingMisses=miss_alternates,landedHitTicks=landed,chestCallbackTicks=rewards,chestCallbacks=len(rewards),
        zeroHPLeavingReentries=len([e for e in transitions if e[1:]==(8,6)]),impactTicks=impacts))
report=dict(controlledChestWindowCases=len(examples),logicalTicks=len(examples)*120,examples=examples,
    findings=['A landed result can return the zero-HP boss from Leaving8 to Damaging6.',
              'Each subsequent Leaving entry requests a prize again; hits during Damaging reset its frame counter.',
              'Under this controlled schedule, fewer landed hits can yield more prize callbacks by allowing frame7 to be reached.'],
    limits=['Portable composition of native-checked pieces, not one joined original-world execution.',
            'Encounter held active; prescribed hits bypass target selection, actual command backlog, DEX/LUK rolls and healer AI.',
            'Reward callback count only; chest settlement/caps and inventory delivery not modeled.',
            'Smoke, animation, bookkeeping and sound/reward RNG omitted. Tick gaps are not yet recommended stat thresholds.'])
(EVIDENCE/'chest-window-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
print(json.dumps({k:v for k,v in report.items() if k!='examples'}))
