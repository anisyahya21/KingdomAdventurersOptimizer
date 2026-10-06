"""DIAGNOSTIC ONLY - pool the ATK x Luck x Speed grid runs written by test_atk_luck_speed_grid.py.

Reads the saved per-row JSON runs plus the cell extensions and reports the pooled surface, the
per-cell chest reliability, the best Speed class per ATK/Luck cell, and paired deltas against the
stored cell. Read-only; nothing here feeds the optimiser.
"""
from __future__ import annotations

import collections
import json
import statistics
import sys
from pathlib import Path

PILOTS = Path(r'C:\Users\anisb\KaOptimizerPilots')
FILES = {
    18: ['atk-luck-speed-18-atk1.json', 'atk-luck-speed-18-atk2.json',
         'atk-luck-speed-18-atk3.json', 'atk-luck-speed-18-ext.json'],
    19: ['atk-luck-speed-19-atk1.json', 'atk-luck-speed-19-atk2.json',
         'atk-luck-speed-19-atk3.json', 'atk-luck-speed-19-ext.json'],
}
ATK_VALUE = {18: {'atk1': 79, 'atk2': 200, 'atk3': 350},
             19: {'atk1': 200, 'atk2': 265, 'atk3': 478}}
LUCK_VALUE = {18: {'luck1': 40, 'luck2': 123, 'luck3': 700},
              19: {'luck1': 40, 'luck2': 414, 'luck3': 900}}
CRIT = {18: {'luck1': 14, 'luck2': 21, 'luck3': 30}, 19: {'luck1': 14, 'luck2': 25, 'luck3': 33}}
STORED = {18: ('atk1', 'luck2'), 19: ('atk2', 'luck2')}
SPEEDS = ('slower', 'current', 'faster')
THRESHOLDS = (10, 25, 50)


def load(encounter):
    per_seed = collections.defaultdict(dict)
    for name in FILES[encounter]:
        path = PILOTS / name
        if not path.exists():
            continue
        for row in json.loads(path.read_text(encoding='utf-8')):
            if row.get('diagnostic'):
                continue
            per_seed[tuple(row['seed'])][(row['atk'], row['luck'], row['speed'])] = row
    return per_seed


def q(values, frac):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(frac * len(ordered)))]


def main():
    for encounter in (18, 19):
        per_seed = load(encounter)
        mass = collections.defaultdict(list)
        chests = collections.defaultdict(list)
        for cells in per_seed.values():
            for key, row in cells.items():
                if isinstance(row.get('bossHitMassAtDeath'), (int, float)):
                    mass[key].append(row['bossHitMassAtDeath'])
                if isinstance(row.get('chests'), (int, float)):
                    chests[key].append(row['chests'])
        print('=== encounter %d  (%d pooled seed pairs)  ATK %s  Luck %s  crit %s'
              % (encounter, len(per_seed), ATK_VALUE[encounter], LUCK_VALUE[encounter],
                 CRIT[encounter]))
        for speed in SPEEDS:
            print('  --- Speed %s: mean mass / mean chests / median chests (n) ---' % speed)
            print('    %-8s %s' % ('ATK', '  '.join('%26s' % l for l in ('luck1', 'luck2', 'luck3'))))
            for atk in ('atk1', 'atk2', 'atk3'):
                text = []
                for luck in ('luck1', 'luck2', 'luck3'):
                    key = (atk, luck, speed)
                    m, c = mass.get(key) or [], chests.get(key) or []
                    text.append('%26s' % ('%.1f / %.1f / %.1f (%d)' % (
                        statistics.fmean(m) if m else float('nan'),
                        statistics.fmean(c) if c else float('nan'),
                        statistics.median(c) if c else float('nan'), len(m))))
                print('    %-8s %s' % ('%s=%d' % (atk, ATK_VALUE[encounter][atk]), '  '.join(text)))
        print('  chest reliability (pooled; fractions of seeds at or above the threshold)')
        print('    %-14s %-8s %6s %7s %7s %6s %6s %6s %6s'
              % ('cell', 'speed', 'mean', 'median', 'sd', '>=10', '>=25', '>=50', 'zero'))
        for atk in ('atk1', 'atk2', 'atk3'):
            for luck in ('luck1', 'luck2', 'luck3'):
                for speed in SPEEDS:
                    values = chests.get((atk, luck, speed)) or []
                    if not values:
                        continue
                    print('    %-14s %-8s %6.2f %7.1f %7.2f %6.3f %6.3f %6.3f %6.3f'
                          % ('%d/%d' % (ATK_VALUE[encounter][atk], LUCK_VALUE[encounter][luck]),
                             speed, statistics.fmean(values), statistics.median(values),
                             statistics.pstdev(values),
                             sum(1 for v in values if v >= 10) / len(values),
                             sum(1 for v in values if v >= 25) / len(values),
                             sum(1 for v in values if v >= 50) / len(values),
                             sum(1 for v in values if v <= 0) / len(values)))
        print('  best Speed class per ATK/Luck cell (mean mass), and best cell per Speed class')
        overall = []
        for atk in ('atk1', 'atk2', 'atk3'):
            for luck in ('luck1', 'luck2', 'luck3'):
                means = {s: (statistics.fmean(mass[(atk, luck, s)])
                             if mass.get((atk, luck, s)) else None) for s in SPEEDS}
                best = max((s for s in means if means[s] is not None), key=lambda s: means[s],
                           default=None)
                overall.append((statistics.fmean(mass[(atk, luck, best)]) if best else -1,
                                atk, luck, best))
                print('    %-14s %s -> %s'
                      % ('%d/%d' % (ATK_VALUE[encounter][atk], LUCK_VALUE[encounter][luck]),
                         {k: round(v, 1) if v is not None else None for k, v in means.items()}, best))
        overall.sort(reverse=True)
        print('  top cells by mean mass: %s'
              % ['%d/%d/%s=%.1f' % (ATK_VALUE[encounter][a], LUCK_VALUE[encounter][l], s, v)
                 for v, a, l, s in overall[:5]])
        reference = (STORED[encounter][0], STORED[encounter][1], 'current')
        print('  paired mass deltas against the stored cell %s' % (reference,))
        for atk in ('atk1', 'atk2', 'atk3'):
            for luck in ('luck1', 'luck2', 'luck3'):
                for speed in SPEEDS:
                    key = (atk, luck, speed)
                    deltas = []
                    for cells in per_seed.values():
                        if key not in cells or reference not in cells:
                            continue
                        left = cells[key].get('bossHitMassAtDeath')
                        right = cells[reference].get('bossHitMassAtDeath')
                        if isinstance(left, (int, float)) and isinstance(right, (int, float)):
                            deltas.append(left - right)
                    if not deltas:
                        continue
                    ci = 1.96 * statistics.pstdev(deltas) / (len(deltas) ** 0.5)
                    print('    %-14s %-8s %+8.2f [%+.2f,%+.2f] median %+8.2f %d/%d/%d'
                          % ('%d/%d' % (ATK_VALUE[encounter][atk], LUCK_VALUE[encounter][luck]),
                             speed, statistics.fmean(deltas), statistics.fmean(deltas) - ci,
                             statistics.fmean(deltas) + ci, statistics.median(deltas),
                             sum(1 for v in deltas if v > 0), sum(1 for v in deltas if v < 0),
                             sum(1 for v in deltas if v == 0)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
