"""Controlled incoming attacks feeding Counter and charge/skill queue generation."""
import json
from collections import Counter
from itertools import product
from combat_initial_state import EVIDENCE
from combat_entities import CombatEntities
from combat_commands import enqueue_skill_command
from combat_shared_resolution import dispatch_shared_attack,execute_shared_skill_commands
from combat_resolution import defender_attack_phase,attack_interval
from combat_states import (change_fighter_state,exit_using_skill,enter_using_skill,enter_damaging,
    exit_damaging,update_damaging,tick_charging,tick_using_skill)
from combat_tick import update_fighters

rows={s['id']:s for s in json.loads((EVIDENCE/'weapon-skill-profiles.json').read_text(encoding='utf-8'))['skills']}
examples=[]
for selected_skill,speed,gap,counter_enabled,alternate_misses in product((22,25,110),(220,717),(0,3,6,7,12,20,40),(False,True),(False,True)):
    world=CombatEntities(100,[]);farmer=world.allocate();boss=world.allocate();other=world.allocate()
    world.add_component(farmer,'AI',dict(board={4:0,5:5,6:0,7:0,8:0,17:selected_skill},long_board={},commands=[],path=[]))
    world.add_component(farmer,'Position',[0.,0.,96.,0.,0.,0.,None]);world.add_component(farmer,'Animation',[1,-1]);world.add_component(farmer,'Seb',[0,0,0,-1])
    world.add_component(farmer,'Parameter',dict(parameters={10:dict(rawValue=100000,rawMax=100000)}))
    unit=world.fighter(farmer);created=[];released=[];histogram=Counter();peak=0;incoming=0;landed=0;tick=-1
    interrupted_skills=0;overlapping_charges=0
    def enqueue(skill,target,source):
        global overlapping_charges
        if source=='charging' and unit['commands']:overlapping_charges+=1
        cmd=enqueue_skill_command(unit['commands'],target,rows[skill],target_exists=True)
        created.append((tick,source,cmd))
    enqueue(selected_skill,boss,'initial')
    def animate(u,m):pass
    def change(u,state):
        change_fighter_state(u,state,{3:lambda u:None,5:lambda u:exit_using_skill(u,animate),6:lambda u:exit_damaging(u,3)},
            {3:lambda u:None,5:lambda u:enter_using_skill(u,lambda i:rows[i],animate,lambda u:3,change),6:lambda u:enter_damaging(u,animate)})
    def update(u,state):
        if state==6:update_damaging(u,lambda u:u['parameters'][10]['rawValue'],lambda u:True,lambda u:False,lambda u:False,lambda:False,lambda u:3,lambda u:None,change)
        elif state==5:tick_using_skill(u,lambda u:3,change)
        elif state==3:
            tick_charging(u,False,dict(interval=lambda u:attack_interval(speed),choose_skill=lambda u:(selected_skill,boss),
                add_command=lambda u,s,t:enqueue(s,t,'charging'),change_state=change))
        else:raise AssertionError(state)
    for tick in range(500):
        if gap and tick%gap==0:
            attacker=boss if incoming%2==0 else other
            hit=not alternate_misses or incoming%2==0;incoming+=1;landed+=int(hit)
            interrupted_skills+=int(hit and unit['board'][5]==5)
            hit,critical,damage=defender_attack_phase(hit,False,int(hit),unit['board'],[rows[26]],
                lambda s:counter_enabled,lambda s:True,lambda *a:None,lambda s:enqueue(s['id'],attacker,'counter'),
                lambda s:None,lambda s:None,lambda:None)
            dispatch_shared_attack(world,[dict(target=farmer,hit=hit,damage=damage)],lambda r:None,change)
        peak=max(peak,len(unit['commands']))
        update_fighters([[unit]],2,update,lambda u:execute_shared_skill_commands(world,farmer,lambda c:rows[c['skill']],animate,
            lambda u,c,index:released.append((tick,c['skill'],c['target'],index))))
        peak=max(peak,len(unit['commands']));histogram[unit['board'][5]]+=1
    counter_created=sum(source=='counter' for _,source,c in created)
    assert counter_created==(incoming if counter_enabled else 0)
    assert len(released)==sum(c['use_index'] for _,_,c in created)
    assert unit['parameters'][10]['rawValue']==100000-landed
    assert all(target in (boss,other) for _,_,target,_ in released)
    if not gap:assert peak==1 and not counter_created
    if gap==3 and not alternate_misses:
        assert sum(source=='charging' for _,source,c in created)==0
        assert [r[3] for r in released if r[1]==selected_skill]==list(range(rows[selected_skill]['count']))
    examples.append(dict(selectedSkill=selected_skill,hitCount=rows[selected_skill]['count'],commandDuration=19+5*rows[selected_skill]['count'],
        interruptedUsingSkill=interrupted_skills,chargesWithOlderCommands=overlapping_charges,
        speed=speed,interval=attack_interval(speed),incomingGap=gap,counterEnabled=counter_enabled,
        alternatingIncomingMisses=alternate_misses,incomingAttempts=incoming,landedIncoming=landed,
        chargedCommands=sum(source=='charging' for _,source,c in created),counterCommands=counter_created,
        peakCommands=peak,pendingCommands=len(unit['commands']),bossReleaseAttempts=sum(t==boss for _,_,t,_ in released),
        teammateReleaseAttempts=sum(t==other for _,_,t,_ in released),stateTicks=dict(histogram),releases=released))
report=dict(controlledBacklogCases=len(examples),logicalTicks=len(examples)*500,examples=examples,
    findings=['Counter arrivals and persistent command execution can grow a queue while repeated damage keeps the farmer out of Charging.',
              'Continuous interruption does not freeze an existing multi-hit command; its attempts still execute.',
              'Returning to Charging after interruption can append another skill while older commands remain.',
              'Incoming misses can trigger Counter without entering Damaging; attacker identity remains attached to each Counter.'],
    limits=['Portable composition, with periodic incoming attacks, supplied misses, guaranteed eligible Counter and guaranteed selected2/5/7-hit skill.',
            'Damage1, large HP, no MP cost/healing/death, no outgoing hit resolution, reward callbacks or enemy AI.',
            'These queue counts are not achievable farming yields or recommended SPD values.'])
(EVIDENCE/'backlog-generation-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
print(json.dumps({k:v for k,v in report.items() if k!='examples'}))
