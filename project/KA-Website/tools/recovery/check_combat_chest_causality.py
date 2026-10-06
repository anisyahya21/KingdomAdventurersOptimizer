"""Paired controlled attacks separate mob damage from boss prize re-entry.

Actual damage amounts/eligibility and encounter lifetime are supplied. This
joins checked portable mechanics; it does not run a complete native encounter.
"""
import json
from itertools import product
from combat_initial_state import EVIDENCE
from combat_entities import CombatEntities
from combat_commands import enqueue_skill_command, execute_skill_queue
from combat_shared_resolution import dispatch_shared_attack
from combat_states import (change_fighter_state, enter_damaging, update_damaging,
                           exit_damaging, enter_leaving_special, update_leaving)
from combat_tick import update_fighters
from combat_ending import is_annihilated, enter_ending


ROWS = {s['id']: s for s in json.loads(
    (EVIDENCE/'weapon-skill-profiles.json').read_text(encoding='utf-8'))['skills']}


def run_case(skill_id, before, alternate_misses, mob_mode, boss_releases, observe_ending=False):
    world = CombatEntities(100, [])
    boss, mob = world.allocate(), world.allocate()
    fighters = {}
    for identity in (boss, mob):
        world.add_component(identity, 'AI', dict(board={4:0,5:3,6:1,7:0},
                            long_board={},commands=[],path=[]))
        world.add_component(identity, 'Position', [0.,0.,72.,0.,0.,0.,None])
        world.add_component(identity, 'Speed', [0.,0.,0.])
        world.add_component(identity, 'Direction', [2])
        hp = 100 if identity == mob and mob_mode == 'survive' else 1
        world.add_component(identity, 'Parameter', dict(parameters={10:dict(rawValue=hp,rawMax=hp)}))
        fighters[identity] = world.fighter(identity)
    queue = []
    command_ids = {}
    for target, sid in ((boss,skill_id),(mob,26),(mob,26),(boss,skill_id)):
        command = enqueue_skill_command(queue,target,ROWS[sid],target_exists=True)
        command_ids[id(command)] = len(command_ids)
    animation = dict(rate=1,frame=0)
    trace, prizes, boss_hits = [], [], []
    latest_hit = {}
    tick, phase = -1, 'initial'
    def identity_of(fighter):
        return next(k for k,v in fighters.items() if v is fighter)
    def snapshot(identity):
        u = fighters[identity]
        return dict(hp=u['parameters'][10]['rawValue'],state=u['board'][5],frame=u['board'][4])
    def emit(kind, **fields):
        event = dict(eventId=len(trace),tick=tick,phase=phase,kind=kind,**fields)
        trace.append(event)
        return event['eventId']
    def leave(fighter):
        identity = identity_of(fighter)
        def award():
            event = emit('prize',boss=identity,causeAttack=latest_hit.get(identity),
                         state=snapshot(identity),prizeCount=len(prizes)+1)
            prizes.append(event)
        enter_leaving_special(fighter,3,lambda u:True,lambda u:identity==boss,
                              lambda *a:None,lambda u:None,award,lambda sound:None)
    def change(fighter,state):
        identity = identity_of(fighter)
        emit('state_transition',target=identity,before=snapshot(identity),to=state,
             causeAttack=latest_hit.get(identity))
        change_fighter_state(fighter,state,
            {0:lambda u:None,3:lambda u:None,6:lambda u:exit_damaging(u,3),8:lambda u:None},
            {0:lambda u:None,3:lambda u:None,6:lambda u:enter_damaging(u,lambda *a:None),8:leave})
    def update(fighter,state):
        if state==6:
            update_damaging(fighter,lambda u:u['parameters'][10]['rawValue'],
                lambda u:False,lambda u:True,lambda u:True,lambda:False,
                lambda u:3,lambda u:None,change)
        elif state==8:
            update_leaving(fighter)
    boss_attempts = 0
    def use(command,index):
        nonlocal boss_attempts
        target = command['target']
        enabled = boss_releases if target==boss else mob_mode!='disabled'
        hit = True
        if target==boss:
            hit = not alternate_misses or boss_attempts%2==0
            boss_attempts += 1
        event_id = emit('attack',attacker='controlled_farmer',commandId=command_ids[id(command)],
            storedTarget=target,deliveredTarget=target if enabled else None,
            skill=command['skill'],hitIndex=index,hit=hit if enabled else None,
            critical=False,before=snapshot(target))
        if enabled:
            if hit:
                latest_hit[target] = event_id
                if target==boss: boss_hits.append(tick)
            dispatch_shared_attack(world,[dict(target=target,hit=hit,damage=1)],lambda r:None,change)
        trace[event_id]['after'] = snapshot(target)
    # The prescribed caster is external to this target-state fixture. A living
    # own-team sentinel makes the verdict meaningful without inventing its AI.
    sentinel = dict(board={5:3})
    teams = [[sentinel],list(fighters.values())]
    battle_state, verdict_tick = 2, None
    for tick in range(250):
        phase = 'battle'
        if observe_ending and battle_state==2 and is_annihilated(teams[1]):
            def end_change(u,state):
                if u is sentinel: u['board'][5]=state
                else: change(u,state)
            verdict = enter_ending(teams,end_change)
            battle_state, verdict_tick = 3, tick
            emit('verdict',result=verdict)
        phase = 'before_fighters'
        if before: execute_skill_queue(queue,lambda c:ROWS[c['skill']],animation,lambda a:None,use)
        phase = 'fighters'
        update_fighters([list(fighters.values())],battle_state,update,lambda u:None)
        phase = 'after_fighters'
        if not before: execute_skill_queue(queue,lambda c:ROWS[c['skill']],animation,lambda a:None,use)
    assert not queue
    for p in prizes:
        award = trace[p]
        cause = trace[award['causeAttack']]
        assert cause['kind']=='attack' and cause['deliveredTarget']==boss and cause['hit']
        assert award['state']['hp']==0 and award['state']['state']==8
    expected=[]
    for i,t in enumerate(boss_hits):
        due=t+(6 if before else 7)
        following=boss_hits[i+1] if i+1<len(boss_hits) else 10000
        if (due<following if before else due<=following): expected.append(due)
    prize_ticks=[trace[p]['tick'] for p in prizes]
    assert prize_ticks==expected
    return dict(skillId=skill_id,releaseBeforeFighters=before,alternatingMisses=alternate_misses,
                mobMode=mob_mode,bossReleases=boss_releases,observeEnding=observe_ending,
                verdictTick=verdict_tick,prizeTicks=prize_ticks,trace=trace)


if __name__=='__main__':
    cases=[]
    for skill,before,misses in product((22,25,110),(False,True),(False,True)):
        paired=[run_case(skill,before,misses,mode,True) for mode in ('disabled','survive','die')]
        assert all(c['prizeTicks']==paired[0]['prizeTicks'] for c in paired)
        negative=run_case(skill,before,misses,'die',False)
        assert not negative['prizeTicks']
        cases.extend(paired+[negative])
        for mode in ('survive','die'):
            ending=run_case(skill,before,misses,mode,True,True)
            baseline=next(c for c in paired if c['mobMode']==mode)
            assert ending['prizeTicks']==baseline['prizeTicks']
            if mode=='survive': assert ending['verdictTick'] is None
            else: assert ending['verdictTick'] is not None
            cases.append(ending)
    report=dict(controlledCases=len(cases),ticks=len(cases)*250,cases=cases,
        findings=['With identical boss releases and fixed encounter lifetime, disabling mob damage or changing its survival does not change prize timing.',
                  'Mob-only releases produce no boss prize in these controlled cases.',
                  'Every prize is linked to a landed boss attack and a zero-HP Leaving entry; no healing or resurrection was supplied.',
                  'Observing the verdict with retained prescribed commands does not itself suppress subsequent prize callbacks; teardown is a distinct boundary.'],
        limits=['Portable composition of checked rules, not full native execution.',
                'Prescribed queues, damage and misses; no autonomous acquisition, invocation, MP or incoming attacks.',
                'Ending variants use a living own-team sentinel and prescribed external caster releases; no autonomous post-verdict caster behavior.',
                'World remains active without confirmation/teardown; mob survival can alter real future attack schedules.',
                'No departure flight, downstream bookkeeping, reward selection RNG or inventory settlement.'])
    (EVIDENCE/'chest-causality-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='cases'}))
