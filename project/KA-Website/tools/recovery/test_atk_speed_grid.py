"""DIAGNOSTIC ONLY - 3x3 ATK x Speed alignment grid on the jackpot-capable builds. Read-only.

Rows are three structurally distinct DPS Attack regions derived from the recovered damage model
(`derive_atk_arms.py`): the fallback-dominated floor, the current stored ATK, and a real-damage
region past the fallback boundaries. Columns are the three established Speed classes (adjacent
`attack_interval` classes). Only the DPS's ATK (pid 13) and Speed (pid 15) change; every other
effective parameter is asserted identical across all nine cells.

    python test_atk_speed_grid.py --encounter 18 --candidate a3d527c888ca7d \
        --atk 79 200 350 --pairs 16
    python test_atk_speed_grid.py --encounter 18 --candidate a3d527c888ca7d \
        --atk 79 200 350 --seed-pair 1149347225 1159301657 --diagnostic-only
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
from combat_resolution import attack_interval, critical_rate, skill_invocation_rate  # noqa: E402
from combat_runtime_data import load_data                          # noqa: E402
from combat_skill_selection import active_skill_infos              # noqa: E402
from derive_atk_arms import (ATK_PID, SPD_PID, LUCK_PID, enemies_of,  # noqa: E402
                             expected_hits_per_action, fallback_fraction, hits_to_kill, mix,
                             base_damage_pmf, summarise)
from strategy_optimizer_adapter import stats                       # noqa: E402
from test_counter_level_arms import FAMILY                         # noqa: E402
from test_speed_arms import (DEFAULT_LIBRARY, dps_unit, load_candidate,  # noqa: E402
                             neighbour_agi, param_entry, replay)

SKILLS = {r['id']: r for r in load_data('weapon-skill-profiles.json')['skills']}
ATK_LABELS = ('atk1', 'atk2', 'atk3')
SPD_LABELS = ('slower', 'current', 'faster')


def set_effective(scenario, name, pid, target):
    """A copy whose named unit's effective `pid` value is `target`, everything else untouched."""
    out = json.loads(json.dumps(scenario))
    unit = next(u for u in out['ownUnits'] if u['name'] == name)
    current = stats(scenario)[name]['parameters'][pid]['value']
    entry = param_entry(unit, pid)
    entry['rawValue'] = int(entry['rawValue']) + (int(target) - int(current))
    return out


def speed_classes(scenario, name):
    current = stats(scenario)[name]['parameters'][SPD_PID]['value']
    base = attack_interval(current)
    out = {'current': (current, base)}
    slower = neighbour_agi(current, base + 1)
    faster = neighbour_agi(current, base - 1)
    if slower is not None:
        out['slower'] = (slower, base + 1)
    if faster is not None:
        out['faster'] = (faster, base - 1)
    return out


def grid(scenario, name, atk_values):
    speeds = speed_classes(scenario, name)
    cells, meta = {}, {}
    for atk_label, atk in zip(ATK_LABELS, atk_values):
        for spd_label in SPD_LABELS:
            if spd_label not in speeds:
                continue
            agi, interval = speeds[spd_label]
            variant = set_effective(scenario, name, ATK_PID, atk)
            variant = set_effective(variant, name, SPD_PID, agi)
            key = (atk_label, spd_label)
            cells[key] = variant
            meta[key] = dict(atk=atk, agi=agi, interval=interval)
    return cells, meta


def parity(scenario, name, cells):
    """Every effective parameter except the DPS ATK and Speed must be identical across cells."""
    reference = stats(scenario)
    changed = []
    for key, variant in cells.items():
        table = stats(variant)
        for fighter, values in table.items():
            ref = reference[fighter]
            for pid, block in values['parameters'].items():
                if fighter == name and pid in (ATK_PID, SPD_PID):
                    continue
                if block != ref['parameters'].get(pid):
                    changed.append((key, fighter, pid))
            for field in ('effectiveDefense', 'averageTrainingLevel', 'weaponId', 'weaponRange',
                          'skillCosts', 'monster'):
                if values.get(field) != ref.get(field):
                    changed.append((key, fighter, field))
    return changed


def pre_simulation(scenario, name, meta, boss):
    """The static 3x3 table: ATK, speed, timing, boss TTK, crit, selection, expected hits/MP."""
    unit = next(u for u in scenario['ownUnits'] if u['name'] == name)
    luck = stats(scenario)[name]['parameters'][LUCK_PID]['value']
    crit = critical_rate(luck) / 100.0
    per_action = expected_hits_per_action(scenario)
    infos = active_skill_infos([SKILLS[s] for s in unit['skills']], unit['invocationLevels'],
                               10 ** 6, lambda s: 0)
    survival, probabilities = 1.0, []
    for skill, level in infos:
        rate = skill_invocation_rate(skill['type'], level) / 100.0
        probabilities.append((skill['id'], round(survival * rate, 6)))
        survival *= 1 - rate
    rows = {}
    for key, values in meta.items():
        blob = mix((1 - crit, base_damage_pmf(values['atk'], boss['defense'], False)),
                   (crit, base_damage_pmf(values['atk'], boss['defense'], True)))
        rows[key] = dict(
            **values,
            period=values['interval'] + 21,
            fallbackShare=round((1 - crit) * fallback_fraction(values['atk'], boss['defense'], False)
                                + crit * fallback_fraction(values['atk'], boss['defense'], True), 4),
            bossTtk=summarise(hits_to_kill(blob, boss['hp'])),
            critRate=critical_rate(luck),
            selectionProbabilities=[row[1] for row in probabilities],
            expectedHitsPerAction=round(per_action, 5),
        )
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', default=DEFAULT_LIBRARY)
    parser.add_argument('--encounter', type=int, required=True)
    parser.add_argument('--candidate', required=True)
    parser.add_argument('--atk', type=int, nargs=3, required=True,
                        help='low/current/high DPS ATK regions')
    parser.add_argument('--seed-pair', type=int, nargs=2)
    parser.add_argument('--diagnostic-only', action='store_true')
    parser.add_argument('--row', choices=ATK_LABELS, help='restrict the fresh run to one ATK row')
    parser.add_argument('--pairs', type=int, default=16)
    parser.add_argument('--seed-base', type=int, default=1900000)
    parser.add_argument('--json')
    args = parser.parse_args(argv)

    db = sqlite3.connect('file:%s?mode=ro' % args.library, uri=True)
    db.row_factory = sqlite3.Row
    candidate, scenario, label = load_candidate(db, args.candidate)
    name = dps_unit(scenario)['name']
    boss = next(e for e in enemies_of(scenario) if e['boss'])
    current_atk = stats(scenario)[name]['parameters'][ATK_PID]['value']
    cells, meta = grid(scenario, name, args.atk)
    if args.row:
        cells = {key: value for key, value in cells.items() if key[0] == args.row}
        meta = {key: value for key, value in meta.items() if key[0] == args.row}
    changed = parity(scenario, name, cells)
    table = pre_simulation(scenario, name, meta, boss)

    print('encounter %d  candidate %s  %s' % (args.encounter, candidate[:12], label[:46]))
    print('  DPS %s  stored ATK %d  boss %s hp %d def %d'
          % (name, current_atk, boss['name'], boss['hp'], boss['defense']))
    stored = [label for label, value in zip(ATK_LABELS, args.atk) if value == current_atk]
    print('  ATK regions %s (stored ATK is region %s)'
          % (dict(zip(ATK_LABELS, args.atk)), stored[0] if stored else 'not among the arms'))
    print('  non-ATK/non-Speed parity: %s' % ('all other values identical' if not changed
                                              else 'CHANGED %s' % changed[:4]))
    print('  %-8s %-8s %6s %5s %6s %7s %8s %10s %10s' % (
        'atk', 'speed', 'ATK', 'AGI', 'intvl', 'period', 'fallback', 'bossTTKmed', 'bossTTKmean'))
    for atk_label in ATK_LABELS:
        for spd_label in SPD_LABELS:
            row = table.get((atk_label, spd_label))
            if row is None:
                continue
            print('  %-8s %-8s %6d %5d %6d %7d %8.3f %10d %10.2f' % (
                atk_label, spd_label, row['atk'], row['agi'], row['interval'], row['period'],
                row['fallbackShare'], row['bossTtk']['median'], row['bossTtk']['mean']))
    sample = table.get(('atk1', 'current')) or table[next(iter(table))]
    print('  crit %d%%  expected hits/action %.5f  selection probabilities %s'
          % (sample['critRate'], sample['expectedHitsPerAction'], sample['selectionProbabilities']))

    output = []
    if args.seed_pair:
        seeds = tuple(args.seed_pair)
        print('  historical jackpot-seed diagnostic, seeds %s (diagnostic only)' % (seeds,))
        keys = ('firstBossTargetTick', 'death', 'peakBossQueue', 'bossQueueAtDeath',
                'bossHitMassAtDeath', 'bossHitsAfterDeath', 'reEntries', 'leavings', 'chests')
        print('    %-10s %-8s %s' % ('atk', 'speed', '  '.join('%10s' % k for k in keys)))
        for atk_label in ATK_LABELS:
            for spd_label in SPD_LABELS:
                if (atk_label, spd_label) not in cells:
                    continue
                metrics = replay(cells[(atk_label, spd_label)], seeds, name)
                output.append(dict(diagnostic=True, seed=list(seeds), encounter=args.encounter,
                                   candidate=candidate, atk=atk_label, speed=spd_label, **metrics))
                print('    %-10s %-8s %s' % (atk_label, spd_label,
                                             '  '.join('%10s' % metrics.get(k) for k in keys)))
    if args.diagnostic_only:
        if args.json:
            Path(args.json).write_text(json.dumps(output, indent=1, default=str), encoding='utf-8')
            print('wrote', args.json)
        return 0

    per_seed = collections.defaultdict(dict)
    for index in range(args.pairs):
        seeds = strategy_optimizer.seed_pair('validation', args.seed_base + index)
        for key, variant in cells.items():
            metrics = replay(variant, seeds, name)
            per_seed[tuple(seeds)][key] = metrics
            output.append(dict(seed=list(seeds), encounter=args.encounter, candidate=candidate,
                               atk=key[0], speed=key[1], **metrics))
    primary = 'bossHitMassAtDeath'
    print('  fresh paired surface, %d pairs (primary = boss-directed mass at death)' % args.pairs)
    surface = collections.defaultdict(lambda: collections.defaultdict(list))
    chests = collections.defaultdict(lambda: collections.defaultdict(list))
    for metrics_by_cell in per_seed.values():
        for key, metrics in metrics_by_cell.items():
            if isinstance(metrics.get(primary), (int, float)):
                surface[key[0]][key[1]].append(metrics[primary])
            if isinstance(metrics.get('chests'), (int, float)):
                chests[key[0]][key[1]].append(metrics['chests'])
    print('    %-8s %s' % ('ATK', '  '.join('%18s' % lbl for lbl in SPD_LABELS)))
    for atk_label in ATK_LABELS:
        cells_text = []
        for spd_label in SPD_LABELS:
            values = surface[atk_label].get(spd_label) or []
            chest_values = chests[atk_label].get(spd_label) or []
            cells_text.append('%18s' % ('%.2f / %.2f (%d)' % (
                statistics.fmean(values) if values else float('nan'),
                statistics.fmean(chest_values) if chest_values else float('nan'), len(values))))
        print('    %-8s %s' % (atk_label, '  '.join(cells_text)))
    print('    (cell = mean mass at death / mean chests / n)')

    print('    paired deltas vs the current Speed class, per ATK row (+/-/= and CI)')
    for atk_label in ATK_LABELS:
        for spd_label in ('slower', 'faster'):
            deltas, chest_deltas = [], []
            for metrics_by_cell in per_seed.values():
                a = metrics_by_cell.get((atk_label, spd_label), {}).get(primary)
                b = metrics_by_cell.get((atk_label, 'current'), {}).get(primary)
                if isinstance(a, (int, float)) and isinstance(b, (int, float)):
                    deltas.append(a - b)
                ca = metrics_by_cell.get((atk_label, spd_label), {}).get('chests')
                cb = metrics_by_cell.get((atk_label, 'current'), {}).get('chests')
                if isinstance(ca, (int, float)) and isinstance(cb, (int, float)):
                    chest_deltas.append(ca - cb)
            if not deltas:
                continue
            pos = sum(1 for v in deltas if v > 0)
            neg = sum(1 for v in deltas if v < 0)
            tie = len(deltas) - pos - neg
            ci = 1.96 * statistics.pstdev(deltas) / (len(deltas) ** 0.5)
            print('      %-8s %-8s mass %+8.2f [%+.2f,%+.2f] median %+7.2f %d/%d/%d | chests %+6.2f'
                  % (atk_label, spd_label, statistics.fmean(deltas),
                     statistics.fmean(deltas) - ci, statistics.fmean(deltas) + ci,
                     statistics.median(deltas), pos, neg, tie,
                     statistics.fmean(chest_deltas) if chest_deltas else float('nan')))

    best = {}
    for atk_label in ATK_LABELS:
        scores = {spd: (statistics.fmean(surface[atk_label][spd])
                        if surface[atk_label].get(spd) else float('-inf'))
                  for spd in SPD_LABELS}
        best[atk_label] = max(scores, key=scores.get)
    print('    best Speed class per ATK row: %s' % best)
    if args.json:
        Path(args.json).write_text(json.dumps(output, indent=1, default=str), encoding='utf-8')
        print('wrote', args.json)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
