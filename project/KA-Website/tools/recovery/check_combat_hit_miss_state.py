"""DIAGNOSTIC CHECK - does a HIT move a UsingSkill(5) human into Damaging(6), and a MISS not?

Read-only. Drives the authoritative handlers directly on a minimal fighter fixture that mirrors the
shared controller's `change` table, so no RNG and no unrelated state can contaminate the comparison:

  * `apply_fighter_attack_results` (the hit/miss gate that requests the state change),
  * `combat_states.update_damaging` (how long state 6 lasts before `decide` is consulted),
  * `combat_states.is_normal_attack_target` (the targetability predicate),
  * `combat_resolution.defender_attack_phase` (is Counter still checked on a miss?),
  * the queue rule in `combat_states.tick_using_skill`.

    python check_combat_hit_miss_state.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from combat_resolution import apply_fighter_attack_results, defender_attack_phase  # noqa: E402
from combat_states import (change_fighter_state, enter_damaging, exit_damaging,  # noqa: E402
                           enter_using_skill, exit_using_skill, is_normal_attack_target,
                           tick_using_skill, update_damaging)

USING_SKILL, DAMAGING = 5, 6


def fresh_fighter(queue=True):
    return dict(
        id=1, board={5: USING_SKILL, 4: 0, 6: 0, 7: 0, 12: 0, 8: 0, 17: 110},
        long_board={16: -1},
        position=[0.0, 0.0, 0.0], offset=[0.0, 0.0, 0.0], direction=0,
        commands=[dict(opcode=29, target=2, skill=110, tick=3, duration=54, use_index=0)]
        if queue else [],
    )


def make_change(log):
    empty = lambda u: None
    anim = lambda u, index=None: None

    def change(unit, state):
        log.append(dict(old=unit['board'][5], new=state))
        change_fighter_state(
            unit, state,
            {0: empty, 1: empty, 2: empty, 3: empty, 4: empty,
             5: lambda u: exit_using_skill(u, anim), 6: lambda u: exit_damaging(u, 0),
             7: empty, 8: empty},
            {0: empty, 1: empty, 2: empty, 3: empty, 4: empty,
             5: lambda u: enter_using_skill(u, lambda s: dict(motion=0), anim, lambda u: 3, change),
             6: lambda u: enter_damaging(u, anim), 7: empty, 8: empty})
    return change


def resolve(hit, queue=True, damage=7):
    unit = fresh_fighter(queue)
    log = []
    hp = [100]
    inner = make_change(log)
    apply_fighter_attack_results(
        [dict(attacker=2, target=1, hit=hit, critical=False, damage=damage, skill=None)],
        lambda identity: True, lambda identity, amount: hp.__setitem__(0, hp[0] - amount),
        lambda identity, state: inner(unit, state))
    return unit, log, hp[0]


def main():
    report = {}
    failures = []

    def check(condition, message):
        if not condition:
            failures.append(message)

    for hit in (True, False):
        unit, log, hp = resolve(hit)
        report['hit' if hit else 'miss'] = dict(
            requested=log, finalState=unit['board'][5], hp=hp,
            targetable=is_normal_attack_target(True, hp, unit['board'][5]),
            queue=len(unit['commands']))
    check(report['hit']['finalState'] == DAMAGING, 'a hit did not move the DPS to Damaging(6)')
    check(report['hit']['requested'] == [dict(old=USING_SKILL, new=DAMAGING)],
          'hit did not request exactly UsingSkill -> Damaging')
    check(report['miss']['finalState'] == USING_SKILL, 'a miss moved the DPS out of UsingSkill(5)')
    check(report['miss']['requested'] == [], 'a miss requested a state change')
    check(report['hit']['targetable'] and not report['miss']['targetable'],
          'targetability did not differ between hit and miss')
    check(report['miss']['hp'] == 100, 'a miss changed HP')

    # how long does state 6 last, and where does it go?
    unit = fresh_fighter()
    log = []
    change = make_change(log)
    change(unit, DAMAGING)
    frames = 0
    while unit['board'][5] == DAMAGING and frames < 40:
        unit['board'][4] += 1
        frames += 1
        update_damaging(unit, lambda u: 100, lambda u: True, lambda u: False,
                        lambda u: False, lambda: False, lambda u: 3, lambda u: None, change)
    report['damagingWindow'] = dict(ticksUntilExit=frames, nextState=unit['board'][5],
                                    exitRequests=log)
    check(unit['board'][5] != DAMAGING, 'state 6 never exited within 40 ticks')
    check(unit['board'][5] == 3, 'state 6 did not return via decide -> Charging(3)')

    # queue rule: does state 5 hold while an opcode-29 command is queued?
    unit = fresh_fighter(queue=True)
    log = []
    tick_using_skill(unit, lambda u: 3, make_change(log))
    held = unit['board'][5] == USING_SKILL and not log
    unit = fresh_fighter(queue=False)
    log = []
    tick_using_skill(unit, lambda u: 3, make_change(log))
    released = unit['board'][5] == 3
    report['queueRule'] = dict(heldWhileQueued=held, releasedWhenEmpty=released)
    check(held and released, 'the queue rule for state 5 did not behave as recovered')

    # Counter on a miss, at the handler level
    seen = []
    candidate = dict(id=26, type=20, flags=70458, value=0, count=1, minMp=10)
    for hit in (True, False):
        enqueued = []
        defender_attack_phase(
            hit, False, 0, {5: USING_SKILL}, [candidate],
            lambda s: True, lambda s: True, lambda s, d: seen.append('reflect'),
            lambda s: enqueued.append(s['id']), lambda s: seen.append('pay'),
            lambda s: seen.append('balloon'), lambda: False)
        report['counterOn%s' % ('Hit' if hit else 'Miss')] = dict(enqueued=enqueued)
    check(report['counterOnMiss']['enqueued'] == [26], 'Counter was not enqueued on a miss')
    check(report['counterOnHit']['enqueued'] == [26], 'Counter was not enqueued on a hit')

    # damage does not matter: hit=True with the smallest possible damage still requests state 6
    unit, log, _hp = resolve(True, damage=1)
    report['hitWithDamageOne'] = dict(requested=log, finalState=unit['board'][5])
    check(unit['board'][5] == DAMAGING, 'a hit with damage 1 did not enter Damaging')

    print(json.dumps(report, indent=1))
    if failures:
        print('FAILURES: %s' % failures)
        return 1
    print('hit/miss state-transition check: %d assertions passed' % 10)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
