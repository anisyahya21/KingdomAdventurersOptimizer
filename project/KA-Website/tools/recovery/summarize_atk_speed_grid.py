"""DIAGNOSTIC ONLY - pool the ATK x Speed grid evidence written by test_atk_speed_grid.py.

Reads the saved JSON runs (16-pair surface, 16-pair row extensions, and the earlier 32-pair Speed
runs that sit on the same cells) and reports, per encounter and ATK row: the surface means, the
paired deltas against the current Speed class with median/sign counts/CI, and the best Speed class.
"""
from __future__ import annotations

import collections
import json
import statistics
import sys
from pathlib import Path

PILOTS = Path(r'C:\Users\anisb\KaOptimizerPilots')
FILES = {
    18: [('surface16', PILOTS / 'atk-speed-18.json', None),
         ('row16', PILOTS / 'atk-speed-18-row2.json', None),
         # the earlier 32-pair Speed run sits on the same ATK-79 cells as the atk1 row
         ('stored32@79', PILOTS / 'speed-jackpot-18.json', 'atk1')],
    19: [('surface16', PILOTS / 'atk-speed-19.json', None),
         ('row16', PILOTS / 'atk-speed-19-row2.json', None),
         # the earlier 32-pair Speed run sits on the same ATK-265 cells as the atk2 row
         ('stored32@265', PILOTS / 'speed-jackpot-19.json', 'atk2')],
}
ATK_OF = {18: {'atk1': 79, 'atk2': 200, 'atk3': 350}, 19: {'atk1': 200, 'atk2': 265, 'atk3': 478}}
PRIMARY = 'bossHitMassAtDeath'


def load(path, forced_atk=None):
    rows = json.loads(Path(path).read_text(encoding='utf-8'))
    paired = collections.defaultdict(dict)
    for row in rows:
        if row.get('diagnostic'):
            continue
        arm = row.get('arm') or row.get('speed')
        atk = row.get('atk') or forced_atk
        if arm is None or atk is None:
            continue
        paired[tuple(row['seed'])][(atk, arm)] = row
    return paired


def stats_of(values):
    return dict(mean=statistics.fmean(values), median=statistics.median(values),
                pos=sum(1 for v in values if v > 0), neg=sum(1 for v in values if v < 0),
                n=len(values))


def ci(values):
    return 1.96 * statistics.pstdev(values) / (len(values) ** 0.5)


def main():
    for encounter, sources in FILES.items():
        pooled = collections.defaultdict(dict)
        by_source = {}
        for tag, path, forced_atk in sources:
            if not Path(path).exists():
                continue
            data = load(path, forced_atk)
            by_source[tag] = data
            for seed, cells in data.items():
                for key, row in cells.items():
                    pooled[(tag, seed)][key] = row
        print('=== encounter %d  (ATK regions %s)' % (encounter, ATK_OF[encounter]))
        print('  surface: mean %s / mean chests' % PRIMARY)
        print('    %-8s %-10s %s' % ('row', 'source', '  '.join('%22s' % a for a in
                                                                ('slower', 'current', 'faster'))))
        for tag, data in by_source.items():
            cells = collections.defaultdict(list)
            chests = collections.defaultdict(list)
            for seed, cell_rows in data.items():
                for key, row in cell_rows.items():
                    if isinstance(row.get(PRIMARY), (int, float)):
                        cells[key].append(row[PRIMARY])
                    if isinstance(row.get('chests'), (int, float)):
                        chests[key].append(row['chests'])
            rows_present = sorted({key[0] for key in cells})
            for atk in rows_present:
                text = []
                for arm in ('slower', 'current', 'faster'):
                    values = cells.get((atk, arm)) or []
                    chest_values = chests.get((atk, arm)) or []
                    text.append('%22s' % ('%.1f / %.1f (%d)' % (
                        statistics.fmean(values) if values else float('nan'),
                        statistics.fmean(chest_values) if chest_values else float('nan'),
                        len(values))))
                print('    %-8s %-10s %s' % (
                    '%s=ATK%d' % (atk, ATK_OF[encounter].get(atk, -1)), tag, '  '.join(text)))
        print('  paired vs the current Speed class')
        for atk in sorted(ATK_OF[encounter]):
            for arm in ('slower', 'faster'):
                deltas = []
                for (_tag, _seed), cells in pooled.items():
                    a = cells.get((atk, arm), {}).get(PRIMARY)
                    b = cells.get((atk, 'current'), {}).get(PRIMARY)
                    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
                        deltas.append(a - b)
                if not deltas:
                    continue
                blob = stats_of(deltas)
                print('    %-10s %-7s mean %+8.2f [%+.2f,%+.2f] median %+8.2f  %d/%d/%d  n=%d'
                      % ('%s=ATK%d' % (atk, ATK_OF[encounter].get(atk, -1)), arm, blob['mean'],
                         blob['mean'] - ci(deltas), blob['mean'] + ci(deltas), blob['median'],
                         blob['pos'], blob['neg'], blob['n'] - blob['pos'] - blob['neg'], blob['n']))
        print('  best Speed class per ATK row (pooled):')
        for atk in sorted(ATK_OF[encounter]):
            means = {}
            for arm in ('slower', 'current', 'faster'):
                values = [cells.get((atk, arm), {}).get(PRIMARY) for cells in pooled.values()]
                values = [v for v in values if isinstance(v, (int, float))]
                means[arm] = statistics.fmean(values) if values else None
            best = max((a for a in means if means[a] is not None), key=lambda a: means[a],
                       default=None)
            print('    %-10s %s  ->  %s' % ('%s=ATK%d' % (atk, ATK_OF[encounter].get(atk, -1)),
                                            {a: round(v, 1) if v is not None else None
                                             for a, v in means.items()}, best))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
