"""Seven-tick command/reflection composition with original combat skill rows.

This fixture joins independently native-checked helpers. It does not execute
the complete native world or claim that these initial units were captured.
"""
import json
from itertools import product
from combat_initial_state import EVIDENCE
from combat_entities import CombatEntities
from combat_shared_resolution import dispatch_shared_attack, execute_shared_skill_commands, target_exists, SharedAttackMath, shared_skill_cost
from combat_parameters import fighter_parameter, HUMAN_TRAINING_PARAMETERS
from combat_resolution import (resolve_attack, defender_attack_phase, attacker_invocation_phase,
                               reflected_attack_result, subtract_raw_parameter, decide_skill_invocation, random_below, SystemRandomState)
from combat_skill_use import use_fighter_skill
from combat_skills import can_use_skill, play_skill_sound
from combat_states import change_fighter_state, exit_using_skill, enter_damaging, update_damaging, exit_damaging
from combat_tick import update_fighters

rows={s['id']:s for s in json.loads((EVIDENCE/'weapon-skill-profiles.json').read_text(encoding='utf-8'))['skills']}
examples=[]
for skill_id,reflect_id,initial_hp,roll_mode,invoke in product((22,23,24,25,109,110),(20,21),(5,100),('fixed','hit_then_miss','critical_then_miss'),(False,True)):
    world=CombatEntities(100,[]);a,b=world.allocate(),world.allocate()
    attack_skill,reflection=rows[skill_id],rows[reflect_id]
    for team,identity in enumerate((a,b)):
        world.add_component(identity,'AI',dict(board={4:0,5:5 if team==0 else 3,6:team,7:0,8:1,17:skill_id},long_board={16:b},commands=[],path=[]))
        world.add_component(identity,'Position',[0.,0.,96. if team==0 else 72.,0.,0.,0.,None])
        world.add_component(identity,'Parameter',dict(parameters={10:dict(rawValue=initial_hp if team==0 else 100,rawMax=100),11:dict(rawValue=1000,rawMax=1000)}))
        world.add_component(identity,'Skill',dict(dataIds=[skill_id if team==0 else reflect_id],invocationLevels=[1],invokingSkills=[(999,2)] if team==0 and roll_mode=='fixed' else [],maxSlotNum=1))
        world.add_component(identity,'Animation',[1,-1]);world.add_component(identity,'Seb',[0,0,0,-1])
    attacker,defender=world.fighter(a),world.fighter(b)
    for identity in (a,b):
        for key in HUMAN_TRAINING_PARAMETERS:
            parameter=world.fighter(identity)['parameters'].setdefault(key,dict(rawValue=0,rawMax=2147483647))
            parameter.update(extraValue=0,extraMax=0,trainingLevel=123)
        for key,value in {13:103,14:8,15:0,16:0,19:100}.items():
            world.fighter(identity)['parameters'][key]['rawValue']=value
    inv_roll=0 if invoke else 99
    samples=[inv_roll,inv_roll] if roll_mode=='fixed' else [99,0,20,10,inv_roll,99,99,inv_roll] if roll_mode=='hit_then_miss' else [0,20,10,inv_roll,99,99,inv_roll]
    draws=[];stream=iter(samples)
    def draw():
        value=next(stream);draws.append(value);return value
    def get_value(identity,key,default):
        return fighter_parameter(key,world.fighter(identity)['parameters'].get(key),[],lambda *args:0,default=default)
    math=SharedAttackMath(world,rows,get_value,lambda identity:False,draw)
    lib=SystemRandomState(123);lib_draws=[]
    def lib_hits(rate):
        value=lib.next_int();lib_draws.append(value);return random_below(value,100)<rate
    def sound(identity,skill):
        play_skill_sound(skill,100,lib_hits,lambda sound_id:events.append(('sound',tick,identity,sound_id)))
    def cost(identity,skill):return shared_skill_cost(world,identity,skill,lambda identity:False,lambda identity:True)
    command=dict(opcode=29,target=b,skill=skill_id,tick=0,duration=60,use_index=0)
    attacker['commands'].append(command);events=[]
    def hp(identity):return world.fighter(identity)['parameters'][10]['rawValue']
    def animate(unit,behavior):events.append(('animation',tick,unit['id'],behavior))
    def change(unit,state):
        change_fighter_state(unit,state,{3:lambda u:None,5:lambda u:exit_using_skill(u,animate),6:lambda u:exit_damaging(u,3)},
                             {6:lambda u:enter_damaging(u,animate)})
        events.append(('state',tick,unit['id'],state,hp(unit['id'])))
    def send(event,results):
        assert event==26
        dispatch_shared_attack(world,results,lambda r:events.append(('effect',tick,r['attacker'],r['target'],hp(a),hp(b))),change)
    def pay(identity,skill):
        parameter=world.fighter(identity)['parameters'][11]
        parameter['rawValue']=subtract_raw_parameter(parameter['rawValue'],parameter['rawMax'],cost(identity,skill))[0]
        events.append(('pay',tick,identity,skill['id']))
    def eligible(identity,target,skill,check_mp):
        return can_use_skill(skill,check_mp=check_mp,mp=get_value(identity,11,0),cost=cost(identity,skill),
                             target_exists=target_exists(world,target),weapon_type=0,target_hp=hp(target),target_hp_rate=hp(target),
                             invoking=False,battle_world=True,most_front=True,opponent_in_range=True)
    def reflect(skill,damage):
        use_fighter_skill(skill,0,dict(can_use=lambda check:eligible(b,a,skill,check),pay_mp=lambda:pay(b,skill),
            balloon=lambda:None,reflect=lambda:reflected_attack_result(b,a,skill,damage),send=send,sound=lambda:sound(b,skill)))
    def attack():
        def defense(h,c,d):
            return defender_attack_phase(h,c,d,defender['board'],[reflection],lambda s:eligible(b,a,s,True),
                lambda s:decide_skill_invocation(s['type'],defender['invocation_levels'][0],s['flags'],lambda limit:random_below(draw(),limit)),
                reflect,lambda s:None,lambda s:pay(b,s),lambda s:None,lambda:lib_hits(100))
        def invocation():
            attacker_invocation_phase(attacker['invoking'],[],lambda s:False,lambda s:False,lambda s:None,lambda s:None)
            events.append(('invocation',tick,hp(a)))
        return resolve_attack(a,b,attack_skill,lambda identity:target_exists(world,identity),
            (lambda u:False) if roll_mode=='fixed' else math.critical,
            (lambda u,t:True) if roll_mode=='fixed' else math.hit,
            (lambda c,u,t,m:20) if roll_mode=='fixed' else math.damage,lambda u:False,defense,invocation)
    def use(unit,stored,index):
        assert stored['target']==b
        events.append(('use',tick,index,hp(a)))
        use_fighter_skill(attack_skill,index,dict(can_use=lambda check:eligible(a,stored['target'],attack_skill,check),
            pay_mp=lambda:pay(a,attack_skill),balloon=lambda:None,attack=attack,send=send,sound=lambda:sound(a,attack_skill)))
    def state_update(unit,state):
        if state==6:
            update_damaging(unit,lambda u:hp(u['id']),lambda u:True,lambda u:False,lambda u:False,lambda:False,
                            lambda u:3,lambda u:None,change)
    for tick in range(7):
        update_fighters([[attacker],[defender]],2,state_update,
                        lambda unit:execute_shared_skill_commands(world,unit['id'],lambda cmd:rows[cmd['skill']],animate,use))
    total_reflection=(2*(20*reflection['value']//100) if roll_mode=='fixed' else
                      (97 if roll_mode=='hit_then_miss' else 103)*reflection['value']//100+1)
    if not invoke:total_reflection=0
    expected_defender=60 if roll_mode=='fixed' else 3 if roll_mode=='hit_then_miss' else 0
    assert (hp(a),hp(b))==(max(0,initial_hp-total_reflection),expected_defender)
    assert draws==samples
    assert len(lib_draws)==(6 if invoke else 2)
    assert [(e[1],e[2]) for e in events if e[0]=='use']==[(1,0),(6,1)]
    assert [e for e in events if e[0]=='pay' and e[2]==a]==[('pay',1,a,skill_id)]
    assert attacker['parameters'][11]['rawValue']==1000-cost(a,attack_skill)
    assert defender['parameters'][11]['rawValue']==1000-(2*cost(b,reflection) if invoke else 0)
    assert attacker['invoking']==[] and command['tick']==7 and command['use_index']==2
    assert attacker['commands'][0] is command and attacker['long_board'][16]==(-1 if invoke else b)
    assert attacker['board'][5]==(6 if invoke else 5) and defender['board'][5]==6
    for release in (1,6):
        subset=[e for e in events if e[1]==release]
        invoking=next(i for i,e in enumerate(subset) if e[0]=='invocation')
        outer_effect=next(i for i,e in enumerate(subset) if e[0]=='effect' and e[2]==a)
        assert invoking<outer_effect
        if invoke:
            reflected_effect=next(i for i,e in enumerate(subset) if e[0]=='effect' and e[2]==b)
            reflected_state=next(i for i,e in enumerate(subset) if e[0]=='state' and e[2]==a)
            assert reflected_effect<reflected_state<invoking
        else:
            assert not any(e[0]=='effect' and e[2]==b for e in subset)
    examples.append(dict(skillId=skill_id,reflectionId=reflect_id,initialHP=initial_hp,rollMode=roll_mode,reflectionInvokes=invoke,mathDraws=draws,libDraws=lib_draws,finalHP=[hp(a),hp(b)],events=events))
report=dict(sharedExchangeCases=len(examples),logicalTicks=len(examples)*7,examples=examples,
            limits=['Joined portable fixture using separately native-checked phases; no full native world replay.',
                    'Original skill rows; synthetic initial participants, positions, command duration and MP.',
                    'Synthetic human training levels123, empty equipment and no independent defender AI.',
                    'Fixed mode uses damage20; two other modes use recovered effective stats, hit/critical and damage with explicit raw RNG samples.',
                    'Reflection invocation uses supplied success/failure raw draws; Lib sound stream explicitly selects System.Random seed123. No live RNG equivalence.',
                    'Seven ticks end before knockdown/leaving; effects/animation/sounds recorded, no spatial or projectile phase.'])
(EVIDENCE/'shared-exchange-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
print(json.dumps({k:v for k,v in report.items() if k!='examples'}))
