"""Queued Heal Maddy, incoming damage and Holy Herb timing in shared fighters."""
import json
from itertools import product
from combat_initial_state import EVIDENCE
from combat_entities import CombatEntities
from combat_commands import enqueue_skill_command
from combat_shared_resolution import dispatch_shared_attack,execute_shared_skill_commands,shared_skill_cost
from combat_parameters import HUMAN_TRAINING_PARAMETERS
from combat_states import (change_fighter_state,exit_using_skill,enter_damaging,exit_damaging,
    update_damaging,enter_knocking_down,update_knocking_down,tick_using_skill)
from combat_tick import update_fighters
from combat_skills import can_use_skill
from combat_skill_use import use_fighter_skill
from combat_consumables import use_battle_holy_herb
from combat_resolution import cure_result,apply_fighter_cure_results,add_raw_parameter,subtract_raw_parameter

skill=next(s for s in json.loads((EVIDENCE/'weapon-skill-profiles.json').read_text(encoding='utf-8'))['skills'] if s['id']==37)
examples=[]
for injury,initial_mp,herb_tick,before,farmer_hp in product(('none','nonlethal5','lethal5','lethal6'),(0,100),(-1,10,11,12),(False,True),(0,1,500,1000)):
    world=CombatEntities(100,[]);farmer=world.allocate();healer=world.allocate();ids=[farmer,healer]
    for index,identity in enumerate(ids):
        world.add_component(identity,'AI',dict(board={4:0,5:5 if identity==healer else 3,6:0,7:index*5,8:0},long_board={},commands=[],path=[]))
        world.add_component(identity,'Position',[0.,0.,96.+index*24,0.,0.,0.,None]);world.add_component(identity,'Speed',[0.,0.,0.])
        world.add_component(identity,'Direction',[0]);world.add_component(identity,'Seb',[0,0,0,-1]);world.add_component(identity,'Animation',[1,-1])
        world.add_component(identity,'Parameter',dict(parameters={10:dict(rawValue=farmer_hp if identity==farmer else 100,rawMax=1000 if identity==farmer else 100),11:dict(rawValue=initial_mp,rawMax=100)}))
    for identity in ids:
        for key in HUMAN_TRAINING_PARAMETERS:
            p=world.fighter(identity)['parameters'].setdefault(key,dict(rawValue=0,rawMax=2147483647))
            p['trainingLevel']=123
    cost=shared_skill_cost(world,healer,skill,lambda i:False,lambda i:True)
    assert cost==28
    units=[world.fighter(i) for i in ids];events=[];tick=-1
    def param(i,p):return world.fighter(i)['parameters'][p]
    def rate(i,p):
        v=param(i,p);return max(1,v['rawValue']*100//v['rawMax']) if v['rawValue']>0 else 0
    def add(i,p,value):
        v=param(i,p);v['rawValue']=add_raw_parameter(v['rawValue'],value,v['rawMax'])[0]
    def animate(u,motion):pass
    def knock(u):enter_knocking_down(u,units,3,lambda a:a['commands'].clear(),lambda *a:None,animate,lambda u:None,lambda s:None)
    def change(u,state):
        change_fighter_state(u,state,{3:lambda u:None,5:lambda u:exit_using_skill(u,animate),6:lambda u:exit_damaging(u,3),7:lambda u:None},
                            {3:lambda u:None,6:lambda u:enter_damaging(u,animate),7:knock})
        events.append(('state',tick,u['id'],state))
    def update(u,state):
        if state==5:tick_using_skill(u,lambda u:3,change)
        elif state==6:update_damaging(u,lambda u:u['parameters'][10]['rawValue'],lambda u:True,lambda u:False,lambda u:False,lambda:False,lambda u:3,lambda u:None,change)
        elif state==7:update_knocking_down(u,animate,change)
    def use(u,command,index):
        target=command['target'];events.append(('attempt',tick,param(healer,10)['rawValue']))
        def pay():
            v=param(healer,11);v['rawValue']=subtract_raw_parameter(v['rawValue'],v['rawMax'],cost)[0];events.append(('pay',tick))
        def send(event,results):
            assert event==27
            apply_fighter_cure_results(results,lambda i:True,lambda i,n:add(i,10,n),lambda i:None,lambda *a:None,lambda *a:None)
            events.append(('heal',tick,param(target,10)['rawValue']))
        use_fighter_skill(skill,index,dict(can_use=lambda check:can_use_skill(skill,check_mp=check,mp=param(healer,11)['rawValue'],cost=cost,
            target_exists=True,weapon_type=0,target_hp=param(target,10)['rawValue'],target_hp_rate=rate(target,10),invoking=False,
            battle_world=True,most_front=False,opponent_in_range=False),pay_mp=pay,balloon=lambda:None,
            cure=lambda:cure_result(healer,target,skill,param(target,10)['rawMax']),send=send,sound=lambda:None))
    enqueue_skill_command(world.fighter(healer)['commands'],farmer,skill,target_exists=True)
    def herb():
        if tick==herb_tick:
            use_battle_holy_herb(ids,lambda i:world.fighter(i)['board'][5],lambda:True,rate,
                lambda i,p:param(i,p)['rawMax'],lambda i:True,add,lambda:events.append(('herb',tick)))
    for tick in range(24):
        if (injury.endswith('5') and tick==5) or (injury=='lethal6' and tick==6):
            dispatch_shared_attack(world,[dict(target=healer,hit=True,damage=1 if injury=='nonlethal5' else 100)],lambda r:None,change)
        if before:herb()
        update_fighters([units],2,update,lambda u:execute_shared_skill_commands(world,u['id'],lambda c:skill,animate,use))
        if not before:herb()
    expected=injury!='lethal5' and 0<farmer_hp<1000 and (initial_mp>=cost or herb_tick==10 or (herb_tick==11 and before))
    heals=[e for e in events if e[0]=='heal']
    assert bool(heals)==expected,(injury,initial_mp,herb_tick,before,farmer_hp,events)
    assert all(e[1]==11 for e in heals)
    assert param(farmer,10)['rawValue']==(min(1000,farmer_hp+150) if expected else farmer_hp)
    assert not world.fighter(healer)['commands']
    examples.append(dict(injury=injury,initialMP=initial_mp,healMPCost=cost,herbTick=herb_tick,herbBeforeFighters=before,farmerHP=farmer_hp,healSucceeded=expected,events=events))
report=dict(controlledHealWindowCases=len(examples),logicalTicks=len(examples)*24,examples=examples,
    findings=['Nonlethal interruption preserves the queued heal clock; this command still attempts release at tick11.',
              'MP is checked on release: a herb before that attempt can rescue the heal, one after cannot.',
              'Knockdown clears the queue; zero HP still in Damaging does not itself prevent a queued caster from healing.',
              'A stored heal whose target has zero/full HP fails eligibility at release without MP spending.'],
    limits=['Portable composition of native-checked pieces; one preselected queued Heal Maddy, no autonomous targeting/invocation or enemy AI.',
            'All12 human training levels supplied as123; original Heal Maddy min/max MP and recovered average-level formula give cost28. No-equipment stats supplied, not a player setup.',
            'Holy Herb stock available and input phase prescribed; no live touch event timing, effects/RNG, flight or reward settlement.'])
(EVIDENCE/'heal-window-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
print(json.dumps({k:v for k,v in report.items() if k!='examples'}))
