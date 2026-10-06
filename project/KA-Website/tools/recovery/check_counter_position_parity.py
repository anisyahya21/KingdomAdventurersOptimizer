"""DIAGNOSTIC ONLY - is Counter's declared POSITION gameplay-neutral when every level is equal?

Two isolated variants of one candidate, differing only in where Counter sits in `skills`, with all
six invocation levels at the same numeric level:

    A. COUNTER FIRST   [Counter, 7, 5, 4, 3, 2-hit]
    B. COUNTER LAST    [7, 5, 4, 3, 2-hit, Counter]

Prints the pre-simulation resolution of both (Counter's level and rate, the active order, each
active's level, the exact selection probabilities, expected hits and MP per action), then runs both on
identical fresh seed pairs and compares the canonical compact result, the full event trace and a
per-tick unit sample. Any divergence is located to its first tick.

    python check_counter_position_parity.py --candidate f934c862c945 --pairs 8
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import combat_sandbox                                              # noqa: E402
import combat_shared_controllers as csc                            # noqa: E402
import strategy_optimizer                                          # noqa: E402
from combat_resolution import skill_invocation_rate, skill_mp_cost  # noqa: E402
from combat_runtime_data import load_data                          # noqa: E402
from combat_skill_selection import active_skill_infos              # noqa: E402
from strategy_optimizer_adapter import validate_scenario, stats, _compact   # noqa: E402

DEFAULT_LIBRARY = 'A:/KingdomAdventurersOptimizer/strategiesv.student1only.sqlite'
COUNTER, FAMILY = 26, [110, 25, 24, 23, 22]
SKILLS = {r['id']: r for r in load_data('weapon-skill-profiles.json')['skills']}


def variant(scenario, counter_last, level):
    out = json.loads(json.dumps(scenario))
    order = (FAMILY + [COUNTER]) if counter_last else ([COUNTER] + FAMILY)
    for unit in out['ownUnits']:
        if set(unit['skills']) == set(FAMILY + [COUNTER]) and unit['skills'][0] in (COUNTER, FAMILY[0]):
            names = [sid for sid in unit['skills']]
            if set(names) != set(order):
                continue
            unit['skills'] = list(order)
            unit['invocationLevels'] = [level] * 6
    return out


def resolution(scenario, level):
    """Everything the prompt asks for before any battle runs."""
    prepared = stats(scenario)
    unit = scenario['ownUnits'][0]
    name = unit['name']
    index = unit['skills'].index(COUNTER)
    counter_level = unit['invocationLevels'][index]
    counter_rate = skill_invocation_rate(SKILLS[COUNTER]['type'], counter_level)
    infos = active_skill_infos([SKILLS[s] for s in unit['skills']], unit['invocationLevels'],
                              10 ** 6, lambda s: 0)
    training = prepared[name]['averageTrainingLevel']
    costs = {row['skillId']: row['cost'] for row in prepared[name]['skillCosts']}
    survival, rows = 1.0, []
    for skill, slot_level in infos:
        rate = skill_invocation_rate(skill['type'], slot_level)
        p = survival * rate / 100.0
        rows.append(dict(skill=skill['id'], count=skill['count'], level=slot_level, rate=rate,
                         probability=round(p, 6), cost=costs.get(skill['id'])))
        survival *= (1 - rate / 100.0)
    return dict(counterIndex=index, counterLevel=counter_level, counterRate=counter_rate,
                activeOrder=[skill['id'] for skill, _ in infos], active=rows,
                normalProbability=round(survival, 6),
                expectedHitsPerAction=round(sum(r['probability'] * r['count'] for r in rows)
                                            + survival, 6),
                expectedMpPerAction=round(sum(r['probability'] * (r['cost'] or 0) for r in rows), 4))


def replay(scenario, seeds):
    sample, meta = {}, {}
    original = csc.update_fighters

    def wrapped(teams, battle_state, update_state, execute_commands, after_fighter=None):
        if after_fighter is None:
            original(teams, battle_state, update_state, execute_commands)
        else:
            original(teams, battle_state, update_state, execute_commands, after_fighter)
        engine = update_state.__self__
        if not meta:
            meta['identity_of'] = {value: key for key, value in engine.names.items()}
        row = {}
        for identity, components in engine.units.items():
            board = components['board']
            row[meta['identity_of'][identity]] = (
                board[5], board.get(8, 0), board[4], engine.value(identity, 10),
                engine.value(identity, 11),
                tuple((c.get('target'), c.get('skill'), c.get('tick'), c.get('use_index'))
                      for c in components.get('commands') or ()))
        sample[engine.tick] = row

    scenario = dict(validate_scenario(scenario), mathSeed=int(seeds[0]), libSeed=int(seeds[1]))
    csc.update_fighters = wrapped
    try:
        report = combat_sandbox.run_scenario(scenario, include_trace=True)
    finally:
        csc.update_fighters = original
    return report, sample


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()[:16]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', default=DEFAULT_LIBRARY)
    parser.add_argument('--candidate', default='f934c862c945')
    parser.add_argument('--pairs', type=int, default=8)
    parser.add_argument('--seed-base', type=int, default=1700000)
    parser.add_argument('--level', type=int, default=0)
    args = parser.parse_args(argv)

    db = sqlite3.connect('file:%s?mode=ro' % args.library, uri=True)
    db.row_factory = sqlite3.Row
    row = db.execute('SELECT id, scenario, label FROM candidate WHERE id LIKE ?',
                     (args.candidate + '%',)).fetchone()
    base = json.loads(row['scenario'])
    print('candidate %s  %s  (all six levels = %d)' % (row['id'][:12], row['label'][:50], args.level))
    first = variant(base, False, args.level)
    last = variant(base, True, args.level)
    print()
    print('pre-simulation resolution')
    for label, scenario in (('A COUNTER FIRST', first), ('B COUNTER LAST', last)):
        print('  %s -> %s' % (label, json.dumps(resolution(scenario, args.level), sort_keys=True)))
    same = resolution(first, args.level) == resolution(last, args.level)
    print('  resolution identical:', same)

    print()
    print('paired parity, %d seeds' % args.pairs)
    identical = 0
    for index in range(args.pairs):
        seeds = strategy_optimizer.seed_pair('validation', args.seed_base + index)
        report_a, sample_a = replay(first, seeds)
        report_b, sample_b = replay(last, seeds)
        compact_a, compact_b = _compact(report_a, tuple(seeds)), _compact(report_b, tuple(seeds))
        trace_a = digest(report_a['trace'])
        trace_b = digest(report_b['trace'])
        sample_diff = None
        for tick in sorted(set(sample_a) | set(sample_b)):
            if sample_a.get(tick) != sample_b.get(tick):
                sample_diff = tick
                break
        same_run = (compact_a == compact_b and trace_a == trace_b and sample_diff is None)
        identical += same_run
        print('  seed %-2d %s | compact %s trace %s/%s firstSampleDiff %s | chests %s/%s ticks %s/%s' % (
            index, 'IDENTICAL' if same_run else 'DIVERGED',
            digest(compact_a) == digest(compact_b), trace_a, trace_b, sample_diff,
            compact_a.get('rewardOutcome', {}).get('awardedChests'),
            compact_b.get('rewardOutcome', {}).get('awardedChests'),
            compact_a.get('ticks'), compact_b.get('ticks')))
        if not same_run:
            keys = sorted(set(compact_a) | set(compact_b))
            for key in keys:
                if compact_a.get(key) != compact_b.get(key):
                    print('      compact difference at %s: %s vs %s' % (
                        key, compact_a.get(key), compact_b.get(key)))
    print()
    print('identical on %d of %d paired seeds' % (identical, args.pairs))
    return 0 if identical == args.pairs else 1


if __name__ == '__main__':
    raise SystemExit(main())
