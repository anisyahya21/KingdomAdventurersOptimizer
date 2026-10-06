"""DIAGNOSTIC ONLY - 3x3x3 ATK x Luck x Speed alignment surface on the jackpot builds. Read-only.

Extends §39.10.2's ATK x Speed grid with a mathematically derived Luck axis (crit-rate classes from
`combat_resolution.critical_rate`, anchored so that the stored build's class is preserved and the
neighbours change boss hits-to-kill by a material amount). Speed classes are the established three
(AGI 314/365/400). Only the DPS's ATK (pid 13), Luck (pid 16) and Speed (pid 15) change; every other
effective parameter is asserted identical across all 27 cells.

    python test_atk_luck_speed_grid.py --encounter 18 --candidate a3d527c888ca7d \
        --atk 79 200 350 --luck 20 123 700 --pairs 16
    python test_atk_luck_speed_grid.py ... --row atk1 --json partial.json      # split the grid
    python test_atk_luck_speed_grid.py ... --seed-pair 1149347225 1159301657 --diagnostic-only
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

import combat_setup                                                # noqa: E402
import strategy_optimizer                                          # noqa: E402
from combat_resolution import attack_interval, critical_rate, hit_rate, skill_invocation_rate  # noqa: E402
from combat_runtime_data import load_data                          # noqa: E402
from combat_skill_selection import active_skill_infos              # noqa: E402
from derive_atk_arms import (ATK_PID, LUCK_PID, SPD_PID, base_damage_pmf,  # noqa: E402
                             expected_hits_per_action, fallback_fraction, hits_to_kill, mix,
                             summarise)
from strategy_optimizer_adapter import stats                       # noqa: E402
from test_counter_level_arms import FAMILY                         # noqa: E402
from test_speed_arms import (DEFAULT_LIBRARY, dps_unit, load_candidate,  # noqa: E402
                             neighbour_agi, param_entry, replay)

SKILLS = {r['id']: r for r in load_data('weapon-skill-profiles.json')['skills']}
ATK_LABELS = ('atk1', 'atk2', 'atk3')
LUCK_LABELS = ('luck1', 'luck2', 'luck3')
SPD_LABELS = ('slower', 'current', 'faster')
THRESHOLDS = (10, 25, 50, 100)


def set_effective(scenario, name, pid, target):
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
    slower, faster = neighbour_agi(current, base + 1), neighbour_agi(current, base - 1)
    if slower is not None:
        out['slower'] = (slower, base + 1)
    if faster is not None:
        out['faster'] = (faster, base - 1)
    assert out['slower'][1] == base + 1 and out['faster'][1] == base - 1, 'speed classes changed'
    return out


def roster(scenario):
    prepared = combat_setup.prepare_setup(json.loads(json.dumps(scenario)))
    out = []
    for f in prepared['encounter']['fighters']:
        p = f['parameters']
        out.append(dict(name=f['name'], boss=bool(f['leaderIdentity']),
                        hp=p['10']['rawValue'], defense=f['effectiveDefense'],
                        dex=p['19']['rawValue'], agility=p['15']['rawValue'],
                        luck=p['16']['rawValue']))
    return out


def damage_state(atk, luck, boss, follower, per_action, enemy_agi):
    crit = critical_rate(luck)
    weight = crit / 100.0
    normal = base_damage_pmf(atk, boss['defense'], False)
    critical = base_damage_pmf(atk, boss['defense'], True)
    combined = mix((1 - weight, normal), (weight, critical))
    follower_combined = mix((1 - weight, base_damage_pmf(atk, follower['defense'], False)),
                            (weight, base_damage_pmf(atk, follower['defense'], True)))
    boss_ttk = summarise(hits_to_kill(combined, boss['hp']))
    return dict(atk=atk, luck=luck, crit=crit,
                normal=dict(min=min(normal), max=max(normal),
                            mean=round(sum(d * p for d, p in normal.items()), 2)),
                critDamage=dict(min=min(critical), max=max(critical),
                                mean=round(sum(d * p for d, p in critical.items()), 2)),
                expectedDamage=round(sum(d * p for d, p in combined.items()), 3),
                fallbackShare=round((1 - weight) * fallback_fraction(atk, boss['defense'], False)
                                    + weight * fallback_fraction(atk, boss['defense'], True), 4),
                bossTtkHits=boss_ttk,
                bossTtkActions=dict(p10=round(boss_ttk['p10'] / per_action, 1),
                                    median=round(boss_ttk['median'] / per_action, 1),
                                    p90=round(boss_ttk['p90'] / per_action, 1)),
                followerTtk=summarise(hits_to_kill(follower_combined, follower['hp'])),
                enemyHitRate=hit_rate(enemy_agi, 365, luck))


def reliability(values):
    if not values:
        return dict(n=0)
    ordered = sorted(values)
    def q(frac):
        return ordered[min(len(ordered) - 1, int(frac * len(ordered)))]
    return dict(n=len(values), mean=round(statistics.fmean(values), 2),
                median=round(statistics.median(values), 2),
                sd=round(statistics.pstdev(values), 2),
                se=round(statistics.pstdev(values) / (len(values) ** 0.5), 2),
                p10=q(0.1), p25=q(0.25), p50=q(0.5), p75=q(0.75), p90=q(0.9),
                **{'ge%d' % t: round(sum(1 for v in values if v >= t) / len(values), 3)
                   for t in THRESHOLDS},
                zeroShare=round(sum(1 for v in values if v <= 0) / len(values), 3))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', default=DEFAULT_LIBRARY)
    parser.add_argument('--encounter', type=int, required=True)
    parser.add_argument('--candidate', required=True)
    parser.add_argument('--atk', type=int, nargs=3, required=True)
    parser.add_argument('--luck', type=int, nargs=3, required=True)
    parser.add_argument('--row', action='append', choices=ATK_LABELS, help='restrict to one ATK row')
    parser.add_argument('--luck-row', action='append', choices=LUCK_LABELS,
                        help='restrict to one Luck column')
    parser.add_argument('--cell', action='append',
                        help='restrict to one ATK:Luck cell, e.g. atk2:luck1 (all speeds)')
    parser.add_argument('--seed-pair', type=int, nargs=2)
    parser.add_argument('--diagnostic-only', action='store_true')
    parser.add_argument('--pairs', type=int, default=16)
    parser.add_argument('--seed-base', type=int, default=2000000)
    parser.add_argument('--json')
    args = parser.parse_args(argv)

    db = sqlite3.connect('file:%s?mode=ro' % args.library, uri=True)
    db.row_factory = sqlite3.Row
    candidate, scenario, label = load_candidate(db, args.candidate)
    name = dps_unit(scenario)['name']
    enemies = roster(scenario)
    boss = next(e for e in enemies if e['boss'])
    follower = max((e for e in enemies if not e['boss']), key=lambda e: e['hp'])
    stored = stats(scenario)[name]['parameters']
    per_action = expected_hits_per_action(scenario)
    unit = next(u for u in scenario['ownUnits'] if u['name'] == name)
    infos = active_skill_infos([SKILLS[s] for s in unit['skills']], unit['invocationLevels'],
                               10 ** 6, lambda s: 0)
    survival, selection = 1.0, []
    for skill, level in infos:
        rate = skill_invocation_rate(skill['type'], level) / 100.0
        selection.append(round(survival * rate, 6))
        survival *= 1 - rate

    print('encounter %d  candidate %s  %s' % (args.encounter, candidate[:12], label[:46]))
    print('  DPS %s  stored ATK %d Luck %d Speed %d | boss %s hp %d def %d'
          % (name, stored[ATK_PID]['value'], stored[LUCK_PID]['value'], stored[SPD_PID]['value'],
             boss['name'], boss['hp'], boss['defense']))
    print('  expected hits/action %.5f  selection probabilities %s' % (per_action, selection))
    speeds = speed_classes(scenario, name)
    print('  Speed classes asserted through attack_interval: %s'
          % {k: (v[0], v[1]) for k, v in speeds.items()})

    states, cell_meta = {}, {}
    for atk_label, atk in zip(ATK_LABELS, args.atk):
        for luck_label, luck in zip(LUCK_LABELS, args.luck):
            states[(atk_label, luck_label)] = damage_state(
                atk, luck, boss, follower, per_action, enemies[0]['dex'])
    print('  static ATK x Luck damage/TTK table (boss %s)' % boss['name'])
    print('    %-8s %-8s %6s %6s %6s %9s %8s %8s %8s %10s'
          % ('atk', 'luck', 'ATK', 'Luck', 'crit%', 'dmg/hit', 'TTKp10', 'TTKmed', 'TTKp90',
             'enemyHit%'))
    for atk_label in ATK_LABELS:
        for luck_label in LUCK_LABELS:
            row = states[(atk_label, luck_label)]
            print('    %-8s %-8s %6d %6d %6d %9.2f %8d %8d %8d %10d'
                  % (atk_label, luck_label, row['atk'], row['luck'], row['crit'],
                     row['expectedDamage'], row['bossTtkHits']['p10'], row['bossTtkHits']['median'],
                     row['bossTtkHits']['p90'], row['enemyHitRate']))
    print('    (TTK in hits; normal DMG %d..%d, crit DMG %d..%d at the stored ATK/Luck)'
          % (states[(ATK_LABELS[0], LUCK_LABELS[0])]['normal']['min'],
             states[(ATK_LABELS[0], LUCK_LABELS[0])]['normal']['max'],
             states[(ATK_LABELS[0], LUCK_LABELS[0])]['critDamage']['min'],
             states[(ATK_LABELS[0], LUCK_LABELS[0])]['critDamage']['max']))
    print('    enemy accuracy vs the DPS at each Luck arm:')
    for luck in args.luck:
        profile = collections.OrderedDict()
        for row in enemies:
            profile.setdefault(row['name'], hit_rate(row['dex'], stored[SPD_PID]['value'], luck))
        print('      Luck %-5d crit %2d%% -> %s' % (luck, critical_rate(luck), dict(profile)))

    rows_wanted = args.row or list(ATK_LABELS)
    luck_wanted = args.luck_row or list(LUCK_LABELS)
    if args.cell:
        wanted = {tuple(part.split(':')) for part in args.cell}
        rows_wanted = [r for r in rows_wanted if any(r == a for a, _ in wanted)]
        luck_wanted = [l for l in luck_wanted if any(l == b for _, b in wanted)]
    cells, meta = {}, {}
    for atk_label in rows_wanted:
        atk = args.atk[ATK_LABELS.index(atk_label)]
        for luck_label in luck_wanted:
            if args.cell and (atk_label, luck_label) not in {tuple(p.split(':'))
                                                             for p in args.cell}:
                continue
            luck = args.luck[LUCK_LABELS.index(luck_label)]
            for spd_label in SPD_LABELS:
                agi, interval = speeds[spd_label]
                variant = set_effective(scenario, name, ATK_PID, atk)
                variant = set_effective(variant, name, LUCK_PID, luck)
                variant = set_effective(variant, name, SPD_PID, agi)
                cells[(atk_label, luck_label, spd_label)] = variant
                meta[(atk_label, luck_label, spd_label)] = dict(atk=atk, luck=luck, agi=agi,
                                                                interval=interval)
    reference = stats(scenario)
    changed = []
    for key, variant in cells.items():
        table = stats(variant)
        for fighter, values in table.items():
            for pid, block in values['parameters'].items():
                if fighter == name and pid in (ATK_PID, LUCK_PID, SPD_PID):
                    continue
                if block != reference[fighter]['parameters'].get(pid):
                    changed.append((key, fighter, pid))
            for field in ('effectiveDefense', 'averageTrainingLevel', 'weaponId', 'weaponRange',
                          'skillCosts', 'monster'):
                if values.get(field) != reference[fighter].get(field):
                    changed.append((key, fighter, field))
    print('  non-ATK/Luck/Speed parity: %s'
          % ('all other values identical' if not changed else 'CHANGED %s' % changed[:4]))

    output = []
    if args.seed_pair:
        seeds = tuple(args.seed_pair)
        print('  historical jackpot-seed diagnostic, seeds %s (diagnostic only)' % (seeds,))
        keys = ('firstBossTargetTick', 'death', 'bossQueueAtDeath', 'bossHitMassAtDeath',
                'bossHitsAfterDeath', 'reEntries', 'leavings', 'chests')
        for atk_label in rows_wanted:
            for luck_label in luck_wanted:
                for spd_label in SPD_LABELS:
                    if (atk_label, luck_label, spd_label) not in cells:
                        continue
                    metrics = replay(cells[(atk_label, luck_label, spd_label)], seeds, name)
                    output.append(dict(diagnostic=True, seed=list(seeds), encounter=args.encounter,
                                       candidate=candidate, atk=atk_label, luck=luck_label,
                                       speed=spd_label, **metrics))
                    print('    %-8s %-8s %-8s %s' % (atk_label, luck_label, spd_label,
                                                      '  '.join('%8s' % metrics.get(k) for k in keys)))
        print('    (columns: %s)' % ', '.join(keys))
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
                               atk=key[0], luck=key[1], speed=key[2], **metrics))
    mass = collections.defaultdict(list)
    chests = collections.defaultdict(list)
    for cells_by_seed in per_seed.values():
        for key, metrics in cells_by_seed.items():
            if isinstance(metrics.get('bossHitMassAtDeath'), (int, float)):
                mass[key].append(metrics['bossHitMassAtDeath'])
            if isinstance(metrics.get('chests'), (int, float)):
                chests[key].append(metrics['chests'])

    print('  fresh %d-pair 3x3 ATK x Luck planes: mean mass at death / mean chests (n)'
          % args.pairs)
    for spd_label in SPD_LABELS:
        print('    --- Speed %s ---' % spd_label)
        print('      %-8s %s' % ('ATK', '  '.join('%20s' % lbl for lbl in LUCK_LABELS)))
        for atk_label in rows_wanted:
            text = []
            for luck_label in luck_wanted:
                key = (atk_label, luck_label, spd_label)
                values, chest_values = mass.get(key) or [], chests.get(key) or []
                text.append('%20s' % ('%.1f / %.1f (%d)' % (
                    statistics.fmean(values) if values else float('nan'),
                    statistics.fmean(chest_values) if chest_values else float('nan'),
                    len(values))))
            print('      %-8s %s' % (atk_label, '  '.join(text)))

    print('  reliability of certified chests per cell (threshold fractions)')
    print('    %-8s %-8s %-8s %8s %8s %8s %8s %8s %8s'
          % ('atk', 'luck', 'speed', 'mean', 'median', '>=10', '>=25', '>=50', 'zero'))
    for key in sorted(mass):
        values = chests.get(key) or []
        blob = reliability(values)
        if not blob.get('n'):
            continue
        print('    %-8s %-8s %-8s %8.2f %8.1f %8.3f %8.3f %8.3f %8.3f'
              % (key[0], key[1], key[2], blob['mean'], blob['median'], blob['ge10'], blob['ge25'],
                 blob['ge50'], blob['zeroShare']))

    print('  best Speed class per ATK/Luck cell, and best cell per Speed class')
    for atk_label in rows_wanted:
        for luck_label in luck_wanted:
            means = {spd: (statistics.fmean(mass[(atk_label, luck_label, spd)])
                           if mass.get((atk_label, luck_label, spd)) else None)
                     for spd in SPD_LABELS}
            best = max((s for s in means if means[s] is not None), key=lambda s: means[s],
                       default=None)
            print('    %-8s %-8s %s -> %s'
                  % (atk_label, luck_label, {k: round(v, 1) if v is not None else None
                                             for k, v in means.items()}, best))
    reference_key = None
    stored_atk_label = next((lbl for lbl, v in zip(ATK_LABELS, args.atk)
                             if v == stored[ATK_PID]['value']), None)
    stored_luck_label = next((lbl for lbl, v in zip(LUCK_LABELS, args.luck)
                              if v == stored[LUCK_PID]['value']), None)
    if stored_atk_label and stored_luck_label:
        reference_key = (stored_atk_label, stored_luck_label, 'current')
    if reference_key and reference_key in cells:
        print('  paired mass deltas against the stored cell %s' % (reference_key,))
        for key in sorted(cells):
            deltas = [by_seed[key]['bossHitMassAtDeath']
                      - by_seed[reference_key]['bossHitMassAtDeath']
                      for by_seed in per_seed.values()
                      if isinstance(by_seed[key].get('bossHitMassAtDeath'), (int, float))
                      and isinstance(by_seed[reference_key].get('bossHitMassAtDeath'),
                                     (int, float))]
            if not deltas:
                continue
            ci = 1.96 * statistics.pstdev(deltas) / (len(deltas) ** 0.5)
            print('    %-8s %-8s %-8s %+8.2f [%+.2f,%+.2f] median %+8.2f %d/%d/%d'
                  % (key[0], key[1], key[2], statistics.fmean(deltas),
                     statistics.fmean(deltas) - ci, statistics.fmean(deltas) + ci,
                     statistics.median(deltas), sum(1 for v in deltas if v > 0),
                     sum(1 for v in deltas if v < 0),
                     sum(1 for v in deltas if v == 0)))
    if args.json:
        Path(args.json).write_text(json.dumps(output, indent=1, default=str), encoding='utf-8')
        print('wrote', args.json)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
