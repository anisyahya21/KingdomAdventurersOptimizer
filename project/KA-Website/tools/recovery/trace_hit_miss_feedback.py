"""DIAGNOSTIC ONLY - hit/miss -> DPS state -> future targetability feedback, on the compensated arms.

For every enemy attack resolved against the DPS it records the DPS state immediately before and after
resolution, the hit/miss outcome, whether Counter was checked/enqueued, the targetable window that
follows, and the time to the next enemy attack. It also finds the first sustained target-denial
window (T_lock) directly from state/targetability and inspects the attack that preceded it.

An optional intervention forces the hit outcome for attacks against a UsingSkill(5) DPS while still
consuming the same accuracy draw, so the RNG stream is untouched:

    --intervention force-hit | force-miss | none

    python trace_hit_miss_feedback.py --encounter 18 --candidate a3d527c888ca7d \
        --arms 299:205,364:185 --pairs 32 --seed-base 2400000
"""
from __future__ import annotations

import argparse
import collections
import json
import sqlite3
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import combat_sandbox                                              # noqa: E402
import combat_shared_controllers as csc                            # noqa: E402
import strategy_optimizer                                          # noqa: E402
from combat_resolution import attack_interval                      # noqa: E402
from strategy_optimizer_adapter import validate_scenario, stats     # noqa: E402
from test_atk_luck_speed_grid import ATK_PID, LUCK_PID, SPD_PID, set_effective  # noqa: E402
from test_speed_arms import DEFAULT_LIBRARY, dps_unit, load_candidate  # noqa: E402

ATTACKABLE = (1, 3, 4, 6)
USING_SKILL = 5
LOCK_WINDOW = 30               # ticks of sustained no-legal-target used to mark a lock onset


def run(scenario, seeds, dps_name, intervention='none'):
    sample, meta = {}, {}
    original = csc.update_fighters
    forced = {}

    def wrapped(teams, battle_state, update_state, execute_commands, after_fighter=None):
        if after_fighter is None:
            original(teams, battle_state, update_state, execute_commands)
        else:
            original(teams, battle_state, update_state, execute_commands, after_fighter)
        engine = update_state.__self__
        if not meta:
            meta['identity_of'] = {value: key for key, value in engine.names.items()}
            meta['dps'] = next(i for i, name in meta['identity_of'].items() if name == dps_name)
            meta['own'] = [i for i, spec in engine.specs.items() if spec['human']]
            if intervention != 'none':
                # Consume the real accuracy draw, then override only the outcome for the DPS.
                base_hit = engine.math.hit

                def forced_hit(attacker, target, _base=base_hit, _dps=meta['dps']):
                    value = _base(attacker, target)
                    if target == _dps and engine.units[_dps]['board'][5] == USING_SKILL:
                        forced['count'] = forced.get('count', 0) + 1
                        return intervention == 'force-hit'
                    return value
                engine.math.hit = forced_hit
        row = {}
        for identity, components in engine.units.items():
            commands = components.get('commands') or ()
            row[meta['identity_of'][identity]] = (
                components['board'][5], engine.value(identity, 10),
                tuple((c.get('target'), c.get('skill'), c.get('use_index')) for c in commands))
        sample[engine.tick] = row

    scenario = dict(validate_scenario(scenario), mathSeed=int(seeds[0]), libSeed=int(seeds[1]))
    csc.update_fighters = wrapped
    try:
        report = combat_sandbox.run_scenario(scenario, include_trace=True)
    finally:
        csc.update_fighters = original
    return analyse(report, sample, meta, combat_sandbox)


def analyse(report, sample, meta, _module):
    dps = meta['dps']
    own = meta['own']
    dps_name = meta['identity_of'][dps]
    identity_of = meta['identity_of']
    trace = report['trace']
    ticks = sorted(sample)

    def state_at(tick, who):
        row = sample.get(tick)
        if row is None:
            return None
        entry = row.get(identity_of[who])
        return None if entry is None else entry[0]

    def queue_count(tick, who):
        row = sample.get(tick)
        entry = row.get(identity_of[who]) if row else None
        return 0 if entry is None else len(entry[2])

    def team_targetable(tick):
        row = sample.get(tick) or {}
        for who in own:
            entry = row.get(identity_of[who])
            if entry and entry[1] > 0 and entry[0] in ATTACKABLE:
                return True
        return False

    attacks = [event for event in trace
               if event.get('kind') == 'attack' and event.get('target') == dps]
    counter_checks = len(attacks)
    counter_enqueues = [(event['tick'], event.get('target')) for event in trace
                        if event.get('kind') == 'enqueue' and event.get('caster') == dps
                        and event.get('source') == 'counter']

    events = []
    for event in attacks:
        tick = event['tick']
        before = state_at(tick - 1, dps)
        after = state_at(tick, dps)
        events.append(dict(tick=tick, before=before, after=after,
                           hit=bool(event.get('hit')), damage=event.get('damage')))
    for index, item in enumerate(events):
        item['nextAttack'] = (events[index + 1]['tick'] - item['tick']
                              if index + 1 < len(events) else None)

    def window_after(tick):
        length = 0
        for probe in range(tick + 1, tick + 400):
            if probe not in sample:
                break
            entry = sample[probe].get(dps_name)
            if entry and entry[1] > 0 and entry[0] in ATTACKABLE:
                length += 1
                continue
            break
        return length

    conditioned = [item for item in events if item['before'] == USING_SKILL and item['after'] is not None]
    for item in conditioned:
        item['window'] = window_after(item['tick'])
    hit_group = [item for item in conditioned if item['hit']]
    miss_group = [item for item in conditioned if not item['hit']]

    def share(group, predicate):
        return (sum(1 for item in group if predicate(item)) / len(group)) if group else None

    def median(values):
        values = [value for value in values if value is not None]
        return statistics.median(values) if values else None

    lock = None
    death = (report['result'].get('progressMetrics') or {}).get('bossDeathTick')
    death = None if (death is None or death < 0) else death
    for tick in ticks:
        if death is not None and tick >= death:
            break
        if death is not None and tick + LOCK_WINDOW > death:
            break
        if all(not team_targetable(probe) for probe in range(tick, tick + LOCK_WINDOW)
               if probe in sample):
            lock = tick
            break
    lock_context = None
    if lock is not None:
        before_lock = [item for item in events if item['tick'] < lock]
        last = before_lock[-1] if before_lock else None
        lock_context = dict(
            tick=lock, lastAttack=(None if last is None else dict(
                tick=last['tick'], hit=last['hit'], before=last['before'], after=last['after'],
                queue=queue_count(last['tick'], dps), gapToLock=lock - last['tick'])),
            missesInLastTen=[item['hit'] for item in before_lock[-10:]])
    return dict(
        ticks=len(ticks), death=death,
        chests=(report['result'].get('rewardEntitlement') or {}).get('awardedChestCount'),
        counterChecks=counter_checks, counterEnqueues=len(counter_enqueues),
        attacks=len(events),
        pState6AfterHit=share(hit_group, lambda item: item['after'] == 6),
        pState6AfterMiss=share(miss_group, lambda item: item['after'] == 6),
        pState5AfterMiss=share(miss_group, lambda item: item['after'] == USING_SKILL),
        hitsWhileUsingSkill=len(hit_group), missesWhileUsingSkill=len(miss_group),
        medianWindowAfterHit=median([item['window'] for item in hit_group]),
        medianWindowAfterMiss=median([item['window'] for item in miss_group]),
        medianNextAttackAfterHit=median([item['nextAttack'] for item in hit_group]),
        medianNextAttackAfterMiss=median([item['nextAttack'] for item in miss_group]),
        usingSkillAttacks=len(conditioned), lock=lock, lockContext=lock_context,
        lockAfterMiss=(None if lock_context is None or lock_context['lastAttack'] is None
                       else not lock_context['lastAttack']['hit']),
        queueAtLock=(None if lock_context is None or lock_context['lastAttack'] is None
                     else lock_context['lastAttack']['queue']),
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', default=DEFAULT_LIBRARY)
    parser.add_argument('--encounter', type=int, required=True)
    parser.add_argument('--candidate', required=True)
    parser.add_argument('--arms', required=True, help='comma list of luck:atk pairs')
    parser.add_argument('--pairs', type=int, default=32)
    parser.add_argument('--seed-base', type=int, default=2400000)
    parser.add_argument('--intervention', choices=('none', 'force-hit', 'force-miss'), default='none')
    parser.add_argument('--json')
    args = parser.parse_args(argv)

    db = sqlite3.connect('file:%s?mode=ro' % args.library, uri=True)
    db.row_factory = sqlite3.Row
    candidate, scenario, label = load_candidate(db, args.candidate)
    name = dps_unit(scenario)['name']
    stored = stats(scenario)[name]['parameters']
    arms = [tuple(int(part) for part in arm.split(':')) for arm in args.arms.split(',')]
    print('encounter %d  candidate %s  %s  intervention=%s'
          % (args.encounter, candidate[:12], label[:44], args.intervention))
    print('  reference enemy interval for encounter 18/19 enemies is ~10-13 ticks')
    output = []
    aggregates = collections.defaultdict(lambda: collections.defaultdict(list))
    for luck, atk in arms:
        for index in range(args.pairs):
            seeds = strategy_optimizer.seed_pair('validation', args.seed_base + index)
            variant = set_effective(scenario, name, ATK_PID, atk)
            variant = set_effective(variant, name, LUCK_PID, luck)
            metrics = run(variant, seeds, name, args.intervention)
            output.append(dict(seed=list(seeds), encounter=args.encounter, candidate=candidate,
                               luck=luck, atk=atk, intervention=args.intervention, **metrics))
            for key, value in metrics.items():
                if isinstance(value, (int, float)) and value is not None:
                    aggregates[(luck, atk)][key].append(value)
    print('    %-10s %-8s %-6s %10s %10s %10s %10s %10s %10s %8s %8s'
          % ('arm', 'ticks', 'death', 'attacks', 'hit@state5', 'miss@state5', 'P6|hit',
             'P6|miss', 'win|hit', 'win|miss', 'lock'))
    for (luck, atk), values in sorted(aggregates.items()):
        def mean(key):
            data = values.get(key) or []
            return statistics.fmean(data) if data else float('nan')
        print('    %-10s %-8.0f %-6.0f %10.1f %10.1f %10.1f %10.3f %10.3f %10.1f %8.1f %8s'
              % ('%d/%d' % (luck, atk), mean('ticks'), mean('death'), mean('attacks'),
                 mean('hitsWhileUsingSkill'), mean('missesWhileUsingSkill'),
                 mean('pState6AfterHit'), mean('pState6AfterMiss'),
                 mean('medianWindowAfterHit'), mean('medianWindowAfterMiss'),
                 mean('lock')))
    print('    %-10s %-8s %10s %10s %10s %10s %10s'
          % ('arm', 'chests', 'checks/1k', 'nextAtk|hit', 'nextAtk|miss', 'lockAfterMiss',
             'queue@lock'))
    for (luck, atk), values in sorted(aggregates.items()):
        checks = values.get('counterChecks') or []
        ticks = values.get('ticks') or []
        rate = (statistics.fmean(checks) / (statistics.fmean(ticks) / 1000)) if ticks else float('nan')
        after_miss = [1.0 if flag else 0.0 for flag in values.get('lockAfterMiss') or []
                      if flag is not None]
        print('    %-10s %-8.1f %10.1f %10.1f %10.1f %10.3f %10.0f'
              % ('%d/%d' % (luck, atk),
                 statistics.fmean(values.get('chests') or [float('nan')]),
                 rate, statistics.fmean(values.get('medianNextAttackAfterHit') or [float('nan')]),
                 statistics.fmean(values.get('medianNextAttackAfterMiss') or [float('nan')]),
                 statistics.fmean(after_miss) if after_miss else float('nan'),
                 statistics.fmean(values.get('queueAtLock') or [float('nan')])))
    if args.json:
        Path(args.json).write_text(json.dumps(output, indent=1, default=str), encoding='utf-8')
        print('wrote', args.json)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
