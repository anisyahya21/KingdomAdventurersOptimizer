"""DIAGNOSTIC ONLY - compensated ATK/Luck arms vs a reference cell, with progression matching.

Arms are explicit `luck:atk` pairs. Speed is given as integer interval offsets from the stored class
(0 = current, -1 = one class faster, +1 = one class slower, ...), resolved through the authoritative
`attack_interval`. Reports progression timing (for calibration), the enemy-interaction decomposition
(attempts / hits / misses / Counter checks / Counter enqueues / conditional success rate), the queue
and post-death chain, and the certified-chest distribution with threshold frequencies.

    python test_compensation_arms.py --encounter 18 --candidate a3d527c888ca7d \
        --arms 40:200,123:184,299:187,364:185,700:171 --speed-offsets 0 --pairs 8
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

import strategy_optimizer                                          # noqa: E402
from combat_resolution import attack_interval, critical_rate, hit_rate  # noqa: E402
from derive_atk_compensation import expected_damage                 # noqa: E402
from derive_atk_arms import base_damage_pmf, fallback_fraction, summarise  # noqa: E402
from strategy_optimizer_adapter import stats                        # noqa: E402
from test_atk_luck_speed_grid import ATK_PID, LUCK_PID, SPD_PID, set_effective, roster  # noqa: E402
from test_speed_arms import (DEFAULT_LIBRARY, dps_unit, load_candidate,  # noqa: E402
                             neighbour_agi, replay)

PER_ACTION = 4.77311
THRESHOLDS = (10, 25, 50, 100)


def quantile(values, frac):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(frac * len(ordered)))]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', default=DEFAULT_LIBRARY)
    parser.add_argument('--encounter', type=int, required=True)
    parser.add_argument('--candidate', required=True)
    parser.add_argument('--arms', required=True, help='comma list of luck:atk pairs')
    parser.add_argument('--speed-offsets', type=int, nargs='+', default=[0])
    parser.add_argument('--pairs', type=int, default=8)
    parser.add_argument('--seed-base', type=int, default=2100000)
    parser.add_argument('--reference', help='luck:atk of the reference arm for paired deltas')
    parser.add_argument('--json')
    args = parser.parse_args(argv)

    db = sqlite3.connect('file:%s?mode=ro' % args.library, uri=True)
    db.row_factory = sqlite3.Row
    candidate, scenario, label = load_candidate(db, args.candidate)
    name = dps_unit(scenario)['name']
    stored = stats(scenario)[name]['parameters']
    base_agi, base_interval = stored[SPD_PID]['value'], attack_interval(stored[SPD_PID]['value'])
    enemies = roster(scenario)
    boss = next(e for e in enemies if e['boss'])
    pairs = [tuple(int(part) for part in arm.split(':')) for arm in args.arms.split(',')]

    print('encounter %d  candidate %s  %s' % (args.encounter, candidate[:12], label[:46]))
    print('  reference speed AGI %d interval %d' % (base_agi, base_interval))
    print('  static profile per arm (boss %s hp %d def %d)' % (boss['name'], boss['hp'],
                                                               boss['defense']))
    print('    %-6s %-6s %-5s %-9s %-9s %-8s %-9s %-12s'
          % ('Luck', 'ATK', 'crit', 'dmg/hit', 'fallback', 'accMin', 'bossTTKp50', 'followerMed'))
    cells, meta = {}, {}
    for luck, atk in pairs:
        crit = critical_rate(luck)
        accuracy = {enemy['name']: hit_rate(enemy['dex'], base_agi, luck) for enemy in enemies}
        followers = [e for e in enemies if not e['boss']]
        print('    %-6d %-6d %-5d %-9.2f %-9.3f %-8d %-9s %-12s'
              % (luck, atk, crit, expected_damage(atk, luck, boss['defense']),
                 fallback_fraction(atk, boss['defense'], False),
                 min(accuracy.values()), 'see json',
                 statistics.median([expected_damage(atk, luck, e['defense']) for e in followers])))
        for offset in args.speed_offsets:
            target_interval = base_interval + offset
            agi = base_agi if offset == 0 else neighbour_agi(base_agi, target_interval)
            if agi is None:
                print('      speed offset %+d unreachable - skipped' % offset)
                continue
            variant = set_effective(scenario, name, ATK_PID, atk)
            variant = set_effective(variant, name, LUCK_PID, luck)
            variant = set_effective(variant, name, SPD_PID, agi)
            cells[(luck, atk, offset)] = variant
            meta[(luck, atk, offset)] = dict(agi=agi, interval=target_interval,
                                             accuracyMin=min(accuracy.values()))
    changed = []
    reference_stats = stats(scenario)
    for key, variant in cells.items():
        table = stats(variant)
        for fighter, values in table.items():
            for pid, block in values['parameters'].items():
                if fighter == name and pid in (ATK_PID, LUCK_PID, SPD_PID):
                    continue
                if block != reference_stats[fighter]['parameters'].get(pid):
                    changed.append((key, fighter, pid))
    print('  non-ATK/Luck/Speed parity: %s'
          % ('all other values identical' if not changed else 'CHANGED %s' % changed[:4]))
    print('  speed classes used: %s'
          % {key[2]: meta[key]['agi'] for key in meta})

    per_seed = collections.defaultdict(dict)
    output = []
    for index in range(args.pairs):
        seeds = strategy_optimizer.seed_pair('validation', args.seed_base + index)
        for key, variant in cells.items():
            metrics = replay(variant, seeds, name)
            per_seed[tuple(seeds)][key] = metrics
            output.append(dict(seed=list(seeds), encounter=args.encounter, candidate=candidate,
                               luck=key[0], atk=key[1], speedOffset=key[2], **metrics))

    def column(key, field):
        return [cells_by_seed[key][field] for cells_by_seed in per_seed.values()
                if isinstance(cells_by_seed[key].get(field), (int, float))]

    print('  progression + enemy interaction + reward per arm (%d pairs)' % args.pairs)
    print('    %-14s %-6s %8s %8s %8s %8s %8s %7s %7s %8s %8s'
          % ('arm', 'offset', 'access', 'death', 'ticks', 'attempts', 'hits', 'misses', 'checks',
             'enqueues', 'succ%'))
    for key in sorted(cells, key=lambda k: (k[2], k[0])):
        access = column(key, 'firstBossTargetTick')
        death = column(key, 'death')
        ticks = column(key, 'ticks')
        attempts = column(key, 'incomingAttackAttempts')
        hits = column(key, 'incomingAttackHits')
        checks = column(key, 'counterChecks')
        enqueues = column(key, 'counterEnqueues')
        misses = [a - h for a, h in zip(attempts, hits)]
        success = (sum(enqueues) / sum(checks)) if sum(checks) else float('nan')
        print('    %-14s %-6d %8.0f %8.0f %8.0f %8.1f %8.1f %7.1f %7.1f %8.1f %8.3f'
              % ('%d/%d' % (key[0], key[1]), key[2],
                 statistics.median(access) if access else float('nan'),
                 statistics.median(death) if death else float('nan'),
                 statistics.median(ticks) if ticks else float('nan'),
                 statistics.fmean(attempts) if attempts else float('nan'),
                 statistics.fmean(hits) if hits else float('nan'),
                 statistics.fmean(misses) if misses else float('nan'),
                 statistics.fmean(checks) if checks else float('nan'),
                 statistics.fmean(enqueues) if enqueues else float('nan'), success))
    print('    %-14s %-6s %8s %8s %8s %8s %8s %7s %7s %8s %8s'
          % ('arm', 'offset', 'mass', 'massPeak', 'retain', 'usingSk', 'suppress',
             'afterHits', 'reentry', 'leave', 'chests'))
    for key in sorted(cells, key=lambda k: (k[2], k[0])):
        mass = column(key, 'bossHitMassAtDeath')
        peak = column(key, 'peakBossMass')
        retention = column(key, 'retentionFraction')
        using = column(key, 'usingSkillTicks')
        suppress = column(key, 'suppressionTicks')
        after = column(key, 'bossHitsAfterDeath')
        reentry = column(key, 'reEntries')
        leave = column(key, 'leavings')
        chests = column(key, 'chests')
        print('    %-14s %-6d %8.1f %8.1f %8.2f %8.0f %8.0f %7.1f %7.1f %8.1f %8.1f'
              % ('%d/%d' % (key[0], key[1]), key[2],
                 statistics.fmean(mass) if mass else float('nan'),
                 statistics.fmean(peak) if peak else float('nan'),
                 statistics.fmean(retention) if retention else float('nan'),
                 statistics.fmean(using) if using else float('nan'),
                 statistics.fmean(suppress) if suppress else float('nan'),
                 statistics.fmean(after) if after else float('nan'),
                 statistics.fmean(reentry) if reentry else float('nan'),
                 statistics.fmean(leave) if leave else float('nan'),
                 statistics.fmean(chests) if chests else float('nan')))
    print('  chest distribution per arm')
    print('    %-14s %-6s %8s %8s %8s %8s %8s %6s %6s %6s %6s'
          % ('arm', 'offset', 'mean', 'median', 'se', 'p25', 'p90', '>=10', '>=25', '>=50', '>=100'))
    for key in sorted(cells, key=lambda k: (k[2], k[0])):
        values = column(key, 'chests')
        if not values:
            continue
        print('    %-14s %-6d %8.2f %8.1f %8.2f %8.1f %8.1f %6.3f %6.3f %6.3f %6.3f'
              % ('%d/%d' % (key[0], key[1]), key[2], statistics.fmean(values),
                 statistics.median(values),
                 statistics.pstdev(values) / (len(values) ** 0.5), quantile(values, 0.25),
                 quantile(values, 0.9),
                 sum(1 for v in values if v >= 10) / len(values),
                 sum(1 for v in values if v >= 25) / len(values),
                 sum(1 for v in values if v >= 50) / len(values),
                 sum(1 for v in values if v >= 100) / len(values)))
    if args.reference:
        ref_luck, ref_atk = (int(part) for part in args.reference.split(':'))
        reference_key = next((k for k in cells if k[0] == ref_luck and k[1] == ref_atk), None)
        if reference_key:
            print('  paired deltas against reference %s' % (reference_key,))
            for key in sorted(cells, key=lambda k: (k[2], k[0])):
                for field in ('bossHitMassAtDeath', 'chests', 'death', 'counterChecks'):
                    deltas = [cells_by_seed[key][field] - cells_by_seed[reference_key][field]
                              for cells_by_seed in per_seed.values()
                              if isinstance(cells_by_seed[key].get(field), (int, float))
                              and isinstance(cells_by_seed[reference_key].get(field), (int, float))]
                    if not deltas:
                        continue
                    ci = 1.96 * statistics.pstdev(deltas) / (len(deltas) ** 0.5)
                    print('    %-14s %+8.2f [%+.2f,%+.2f] median %+8.2f %d/%d/%d  %s'
                          % ('%d/%d' % (key[0], key[1]), statistics.fmean(deltas),
                             statistics.fmean(deltas) - ci, statistics.fmean(deltas) + ci,
                             statistics.median(deltas), sum(1 for v in deltas if v > 0),
                             sum(1 for v in deltas if v < 0), sum(1 for v in deltas if v == 0),
                             field))
    if args.json:
        Path(args.json).write_text(json.dumps(output, indent=1, default=str), encoding='utf-8')
        print('wrote', args.json)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
