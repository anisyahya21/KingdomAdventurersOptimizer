"""Generated farmer actions joined to boss re-entry in a fixed-position slice.

Original selection, invocation, cost, direct damage and command rules are used.
Incoming enemy events are supplied; this is not the full encounter simulator.
"""
import json
from combat_runtime_data import load_data
from combat_entities import CombatEntities
from combat_commands import enqueue_skill_command
from combat_shared_resolution import (SharedAttackMath, dispatch_shared_attack,
    execute_shared_skill_commands, shared_skill_cost, target_exists)
from combat_resolution import (SystemRandomState, random_below, resolve_attack,
    decide_skill_invocation, defender_attack_phase, attack_interval)
from combat_skill_selection import active_skill_infos, attack_skill_candidates
from combat_skill_use import use_fighter_skill
from combat_skills import can_use_skill, play_skill_sound
from combat_targeting import nearest_skill_target
from combat_states import (change_fighter_state, exit_using_skill, enter_using_skill,
    enter_damaging, exit_damaging, update_damaging, tick_charging, tick_using_skill,
    update_attacking, is_normal_attack_target, enter_leaving_special, update_leaving)
from combat_tick import update_fighters
from combat_parameters import HUMAN_TRAINING_PARAMETERS

ROWS={s['id']:s for s in load_data('weapon-skill-profiles.json')['skills']}
RECIPE=(26,110,25,24,23,22)


def run_farmer_slice(seed=1, mp=1000, dex=0, luck=110, speed=220,
                     incoming_gap=12, ticks=1000):
    if min(mp,dex,luck,speed,incoming_gap)<0 or ticks<1:
        raise ValueError('Nonnegative explicit slice inputs required')
    world=CombatEntities(100,[])
    farmer,boss,mob=[world.allocate() for _ in range(3)]
    units={}
    for identity,hp,pos,team in ((farmer,100000,[0.,0.,96.],0),
                                (boss,200,[0.,0.,72.],1),(mob,1000000,[24.,0.,96.],1)):
        world.add_component(identity,'AI',dict(board={4:0,5:3,6:team,7:0,8:0},long_board={},commands=[],path=[]))
        world.add_component(identity,'Position',pos+[0.,0.,0.,None])
        world.add_component(identity,'Speed',[0.,0.,0.])
        world.add_component(identity,'Direction',[0 if team==0 else 2])
        world.add_component(identity,'Animation',[1,-1])
        world.add_component(identity,'Seb',[0,0,0,-1])
        parameters={p:dict(rawValue=0,rawMax=2147483647,extraValue=0,extraMax=0,trainingLevel=123)
                    for p in HUMAN_TRAINING_PARAMETERS}
        parameters[10].update(rawValue=hp,rawMax=hp)
        parameters[11].update(rawValue=mp if identity==farmer else 0,rawMax=mp if identity==farmer else 1)
        world.add_component(identity,'Parameter',dict(parameters=parameters,extras=[]))
        units[identity]=world.fighter(identity)
    f=units[farmer]
    stats={farmer:{13:300,14:3000,15:speed,16:luck,19:dex},
           boss:{13:300,14:100,15:220,16:110,19:0},mob:{13:300,14:100,15:220,16:110,19:0}}
    math_rng,lib_rng=SystemRandomState(seed),SystemRandomState(seed+1)
    trace=[]; tick=-1;phase='initial';draws=0;command_counter=0;last_hit={};prizes=[]
    def emit(kind,**fields):
        event=dict(id=len(trace),tick=tick,phase=phase,kind=kind,**fields)
        trace.append(event);return event['id']
    def next_math():
        nonlocal draws
        draws+=1;return math_rng.next_int()
    def hp(identity):return units[identity]['parameters'][10]['rawValue']
    def current_mp():return f['parameters'][11]['rawValue']
    def cost(skill):return shared_skill_cost(world,farmer,skill,lambda i:False,lambda i:True)
    def get_value(identity,p,default):return stats[identity].get(p,default)
    attack_math=SharedAttackMath(world,ROWS,get_value,lambda i:i!=farmer,next_math)
    def animate(u,m):pass
    def identity_of(u):return next(i for i,v in units.items() if u is v)
    def leave(u):
        identity=identity_of(u)
        def award():
            cause=last_hit[identity]
            prizes.append(emit('prize',target=identity,causeAttack=cause,hp=hp(identity)))
        enter_leaving_special(u,3,lambda u:True,lambda u:identity==boss,
                              lambda *a:None,lambda u:None,award,lambda s:None)
    def change(u,state):
        identity=identity_of(u)
        emit('state',target=identity,old=u['board'][5],new=state,hp=hp(identity))
        change_fighter_state(u,state,
            {3:lambda u:None,4:lambda u:None,5:lambda u:exit_using_skill(u,animate),
             6:lambda u:exit_damaging(u,3),8:lambda u:None},
            {3:lambda u:None,4:lambda u:animate(u,4),
             5:lambda u:enter_using_skill(u,lambda s:ROWS[s],animate,lambda u:3,change),
             6:lambda u:enter_damaging(u,animate),8:leave})
    def eligible(skill,target,check_mp=True):
        return can_use_skill(skill,check_mp=check_mp,mp=current_mp(),cost=cost(skill),
            target_exists=target_exists(world,target),weapon_type=0,target_hp=hp(target) if target in units else 0,
            target_hp_rate=0,invoking=False,battle_world=True,most_front=True,opponent_in_range=True)
    def invoke(skill,level):
        passed=decide_skill_invocation(skill['type'],level,skill['flags'],lambda n:random_below(next_math(),n))
        emit('invocation',skill=skill['id'],level=level,passed=passed,mp=current_mp())
        return passed
    def nearest(skill):
        return nearest_skill_target(farmer,[boss,mob],skill['shootingRange'],
            lambda a,b:sum(abs(x-y) for x,y in zip(units[a]['position'][::2],units[b]['position'][::2]))/24,
            lambda i:hp(i)>0)
    def choose(u):
        infos=active_skill_infos([ROWS[s] for s in RECIPE],[1]*len(RECIPE),current_mp(),cost)
        for skill,level,target in attack_skill_candidates(infos,nearest,eligible):
            if invoke(skill,level):return skill['id'],target
        return None
    def enqueue(skill,target,source):
        nonlocal command_counter
        command=enqueue_skill_command(f['commands'],target,ROWS[skill],target_exists=target_exists(world,target))
        command['traceId']=command_counter;command_counter+=1
        emit('enqueue',commandId=command['traceId'],skill=skill,target=target,source=source,mp=current_mp())
    def pay(skill):
        amount=cost(skill);before=current_mp()
        f['parameters'][11]['rawValue']=max(0,before-amount)
        emit('mp',skill=skill['id'],before=before,after=current_mp(),amount=amount)
    def sound(skill):
        play_skill_sound(skill,100,lambda n:random_below(lib_rng.next_int(),100)<n,lambda s:None)
    def attack(target,skill,command=None,index=None):
        result=resolve_attack(farmer,target,skill,lambda i:target_exists(world,i),
            attack_math.critical,attack_math.hit,attack_math.damage,lambda i:False)
        event=emit('attack',attacker=farmer,target=target,skill=skill['id'] if skill else None,
            commandId=command['traceId'] if command else None,hitIndex=index,
            hit=result['hit'],critical=result['critical'],damage=result['damage'],hpBefore=hp(target))
        if result['hit']:last_hit[target]=event
        dispatch_shared_attack(world,[result],lambda r:None,change)
        trace[event]['hpAfter']=hp(target)
        return result
    def use(u,command,index):
        skill=ROWS[command['skill']];target=command['target']
        # Direct farmer skills only; resolve+dispatch synchronously inside attack.
        used=use_fighter_skill(skill,index,dict(can_use=lambda first:eligible(skill,target,first),
            pay_mp=lambda:pay(skill),balloon=lambda:None,
            attack=lambda:attack(target,skill,command,index),send=lambda *a:None,sound=lambda:sound(skill)))
        emit('release',commandId=command['traceId'],skill=skill['id'],target=target,index=index,used=used,mp=current_mp())
    def normal_target(u):
        return next((i for i in (boss,mob) if is_normal_attack_target(True,hp(i),units[i]['board'][5])),None)
    def update(u,state):
        identity=identity_of(u)
        if state==6:
            update_damaging(u,lambda u:hp(identity),lambda u:identity==farmer,
                lambda u:identity!=farmer,lambda u:identity!=farmer,lambda:False,
                lambda u:3,lambda u:None,change)
        elif state==8:update_leaving(u)
        elif identity==farmer and state==5:tick_using_skill(u,lambda u:3,change)
        elif identity==farmer and state==3:
            tick_charging(u,False,dict(interval=lambda u:attack_interval(speed),choose_skill=choose,
                add_command=lambda u,s,t:enqueue(s,t,'charge'),change_state=change,long_range=lambda u:False,
                most_front=lambda u:True,front_target=normal_target,decide=lambda u:3))
        elif identity==farmer and state==4:
            gauge,state=update_attacking(u['board'][4],u['board'][8],lambda:u['long_board'].get(16),
                False,0,lambda t:attack(t,None),lambda *a:None,lambda:lib_rng.next_int(),lambda:3)
            u['board'][8]=gauge
            if state is not None:change(u,state)
    for tick in range(ticks):
        phase='incoming'
        if incoming_gap and tick%incoming_gap==0:
            attacker=boss if (tick//incoming_gap)%2==0 else mob
            defender_attack_phase(True,False,1,f['board'],[ROWS[26]],
                lambda s:eligible(s,attacker),lambda s:invoke(s,1),lambda *a:None,
                lambda s:enqueue(s['id'],attacker,'counter'),lambda s:pay(s),lambda s:None,
                lambda:sound(ROWS[26]))
            dispatch_shared_attack(world,[dict(target=farmer,hit=True,damage=1)],lambda r:None,change)
        phase='fighters'
        update_fighters([[f],[units[boss],units[mob]]],2,update,
            lambda u:execute_shared_skill_commands(world,farmer,lambda c:ROWS[c['skill']],animate,use) if u is f else None)
    return dict(seed=seed,initialMP=mp,finalMP=current_mp(),dex=dex,luck=luck,speed=speed,
        incomingGap=incoming_gap,ticks=ticks,mathDraws=draws,commandsCreated=command_counter,
        commandsPending=len(f['commands']),prizeCallbacks=len(prizes),trace=trace,
        limits=['Fixed-position farmer slice; enemy incoming events are prescribed, including after their death.',
                'No enemy skills/healing/AI, movement, departure flight, effects RNG, ending or inventory receipt.',
                'Synthetic raw HP/training/effective stats; not a user build or Wairo/Kairo yield prediction.'])
