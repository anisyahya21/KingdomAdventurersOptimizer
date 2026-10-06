"""DIAGNOSTIC ONLY - DEX x Luck/ATK grid on the two jackpot-capable builds. Read-only.

Rows are mechanically distinct player-DEX classes (below the follower cap / mid / full-roster cap
edge), columns are the productive ATK/Luck ridge points from §39.10.4, at the stored Speed class.
Only the DPS's DEX (pid 19), Luck (pid 16) and ATK (pid 13) change; every other effective parameter is
asserted identical. Measures the DPS's own attack decomposition (crit / non-crit land / non-crit miss),
the boss-directed attempted-versus-landed post-death stream, queue mass at death and the reward
distribution.

    python test_dex_luck_grid.py --encounter 18 --candidate a3d527c888ca7d \
        --dexs 2 10 19 --arms 40:200,123:210,299:205 --pairs 16
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
from combat_resolution import hit_rate                              # noqa: E402
from strategy_optimizer_adapter import validate_scenario, stats     # noqa: E402
from test_atk_luck_speed_grid import ATK_PID, LUCK_PID, set_effective, roster  # noqa: E402
from test_speed_arms import DEFAULT_LIBRARY, dps_unit, load_candidate, replay    # noqa: E402

DEX_PID = 19
THRESHOLDS = (10, 25, 50, 100)


def capture(report, sample, meta):
    """DPS-attack decomposition plus the boss-directed attempted/landed post-death stream."""
    dps = meta['dps']
    boss = meta['boss']
    trace = report['trace']
    progress = report['result'].get('progressMetrics') or {}
    entitlement = report['result'].get('rewardEntitlement') or {}
    death = progress.get('bossDeathTick')
    death = None if (death is None or death < 0) else death
    ticks = sorted(sample)
    identity_of = meta['identity_of']
    own = meta['own']

    attempts = crits = noncrit_hits = noncrit_misses = 0
    for event in trace:
        if event.get('kind') != 'attack' or event.get('attacker') != dps:
            continue
        attempts += 1
        if event.get('critical'):
            crits += 1
        elif event.get('hit'):
            noncrit_hits += 1
        else:
            noncrit_misses += 1
    post_attempt = post_land = post_miss = 0
    for event in trace:
        if event.get('kind') != 'attack' or event.get('target') != boss:
            continue
        if death is None or event['tick'] < death:
            continue
        post_attempt += 1
        if event.get('hit'):
            post_land += 1
        else:
            post_miss += 1

    def queue_row(tick):
        row = sample.get(tick)
        if row is None:
            return (0, 0, 0)
        commands = [c for name in own for c in row.get(identity_of[name], (8, 0, 0, ()))[3]]
        boss_commands = [c for c in commands if c[0] == boss]
        from test_atk_luck_speed_grid import SKILLS
        mass = sum(max(0, int(SKILLS[c[1]]['count']) - int(c[2] or 0)) for c in boss_commands)
        return (len(commands), len(boss_commands), mass)

    peak_mass = 0
    for tick in ticks:
        peak_mass = max(peak_mass, queue_row(tick)[2])
    if death is not None:
        total_at_death, boss_at_death, mass_at_death = queue_row(death)
    else:
        total_at_death = boss_at_death = mass_at_death = None
    return dict(
        ticks=len(ticks), death=death,
        bossAccess=next((event['tick'] for event in trace
                         if event.get('kind') == 'enqueue' and event.get('target') == boss), None),
        dpsAttempts=attempts, dpsCrits=crits, dpsNonCritHits=noncrit_hits,
        dpsNonCritMisses=noncrit_misses,
        landRate=(crits + noncrit_hits) / attempts if attempts else None,
        postAttempt=post_attempt, postLand=post_land, postMiss=post_miss,
        bossQueueAtDeath=boss_at_death, bossMassAtDeath=mass_at_death,
        totalQueueAtDeath=total_at_death, peakBossMass=peak_mass,
        retention=(mass_at_death / peak_mass) if (mass_at_death is not None and peak_mass) else None,
        chests=entitlement.get('awardedChestCount'),
        reEntries=progress.get('postDeathBossReentries'),
        leavings=progress.get('postDeathBossLeavings'),
    )


def run(scenario, seeds, dps_name):
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
            meta['dps'] = next(i for i, name in meta['identity_of'].items() if name == dps_name)
            meta['boss'] = next(i for i, spec in engine.specs.items()
                                if not spec['human'] and spec.get('boss'))
            meta['own'] = [i for i, spec in engine.specs.items() if spec['human']]
        row = {}
        for identity, components in engine.units.items():
            row[meta['identity_of'][identity]] = (
                components['board'][5], engine.value(identity, 10),
                engine.value(identity, 11),
                tuple((c.get('target'), c.get('skill'), c.get('use_index'))
                      for c in components.get('commands') or ()))
        sample[engine.tick] = row

    scenario = dict(validate_scenario(scenario), mathSeed=int(seeds[0]), libSeed=int(seeds[1]))
    csc.update_fighters = wrapped
    try:
        report = combat_sandbox.run_scenario(scenario, include_trace=True)
    finally:
        csc.update_fighters = original
    return capture(report, sample, meta)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', default=DEFAULT_LIBRARY)
    parser.add_argument('--encounter', type=int, required=True)
    parser.add_argument('--candidate', required=True)
    parser.add_argument('--dexs', type=int, nargs='+', required=True)
    parser.add_argument('--arms', required=True, help='comma list of luck:atk ridge points')
    parser.add_argument('--pairs', type=int, default=16)
    parser.add_argument('--seed-base', type=int, default=2500000)
    parser.add_argument('--json')
    args = parser.parse_args(argv)

    db = sqlite3.connect('file:%s?mode=ro' % args.library, uri=True)
    db.row_factory = sqlite3.Row
    candidate, scenario, label = load_candidate(db, args.candidate)
    name = dps_unit(scenario)['name']
    stored = stats(scenario)[name]['parameters']
    enemies = roster(scenario)
    boss = next(e for e in enemies if e['boss'])
    arms = [tuple(int(part) for part in arm.split(':')) for arm in args.arms.split(',')]

    print('encounter %d  candidate %s  %s' % (args.encounter, candidate[:12], label[:44]))
    print('  DPS %s  stored DEX %d ATK %d Luck %d'
          % (name, stored[DEX_PID]['value'], stored[ATK_PID]['value'], stored[LUCK_PID]['value']))
    print('  static hit profile (boss %s agi %d luck %d)' % (boss['name'], boss['agility'],
                                                             boss['luck']))
    print('    %-6s %10s %10s %10s %10s' % ('DEX', 'hitBoss%', 'hitFoll%', 'needBossDEX', 'Pland@c'))
    for dex in args.dexs:
        follower_rates = [hit_rate(dex, e['agility'], e['luck']) for e in enemies if not e['boss']]
        need = max(97 - 150 + e['agility'] // 5 + e['luck'] // 5 for e in enemies)
        print('    %-6d %10d %10d %10d %10s'
              % (dex, hit_rate(dex, boss['agility'], boss['luck']), min(follower_rates), need,
                 'see arms below'))
    print('    arms and their P(land) = crit + (1-crit) * noncrit-hit:')
    for luck, atk in arms:
        from combat_resolution import critical_rate
        crit = critical_rate(luck) / 100.0
        for dex in args.dexs:
            rate = hit_rate(dex, boss['agility'], boss['luck']) / 100.0
            print('      Luck %-4d ATK %-4d DEX %-3d crit %.2f boss hit %.2f -> P(land) %.3f'
                  % (luck, atk, dex, crit, rate, crit + (1 - crit) * rate))

    output = []
    per_seed = collections.defaultdict(dict)
    for luck, atk in arms:
        for dex in args.dexs:
            for index in range(args.pairs):
                seeds = strategy_optimizer.seed_pair('validation', args.seed_base + index)
                variant = set_effective(scenario, name, ATK_PID, atk)
                variant = set_effective(variant, name, LUCK_PID, luck)
                variant = set_effective(variant, name, DEX_PID, dex)
                metrics = run(variant, seeds, name)
                per_seed[tuple(seeds)][(luck, atk, dex)] = metrics
                output.append(dict(seed=list(seeds), encounter=args.encounter, candidate=candidate,
                                   luck=luck, atk=atk, dex=dex, **metrics))

    print('  fresh %d-pair DEX x (Luck/ATK) surface' % args.pairs)
    print('    %-14s %s' % ('ridge point', '  '.join('%24s' % ('DEX %d' % d) for d in args.dexs)))
    for luck, atk in arms:
        text = []
        for dex in args.dexs:
            rows = [by_seed[(luck, atk, dex)] for by_seed in per_seed.values()]
            mass = [r['bossMassAtDeath'] for r in rows if isinstance(r['bossMassAtDeath'], (int, float))]
            land = [r['postLand'] for r in rows if isinstance(r['postLand'], (int, float))]
            chests = [r['chests'] for r in rows if isinstance(r['chests'], (int, float))]
            text.append('%24s' % ('%.1f / %.1f / %.1f' % (
                statistics.fmean(mass) if mass else float('nan'),
                statistics.fmean(land) if land else float('nan'),
                statistics.fmean(chests) if chests else float('nan'))))
        print('    %-14s %s' % ('%d/%d' % (luck, atk), '  '.join(text)))
    print('    (cell = mean boss-directed mass at death / mean post-death landed boss hits / mean chests)')
    print('  offence and conversion per cell')
    print('    %-14s %-6s %8s %7s %7s %7s %8s %8s %8s %8s'
          % ('ridge', 'DEX', 'attempts', 'crits', 'nc hit', 'nc miss', 'land%', 'postAtt', 'postLand',
             'postMiss'))
    for luck, atk in arms:
        for dex in args.dexs:
            rows = [by_seed[(luck, atk, dex)] for by_seed in per_seed.values()]
            def mean(key):
                values = [r[key] for r in rows if isinstance(r.get(key), (int, float))]
                return statistics.fmean(values) if values else float('nan')
            print('    %-14s %-6d %8.0f %7.0f %7.0f %7.0f %8.3f %8.1f %8.1f %8.1f'
                  % ('%d/%d' % (luck, atk), dex, mean('dpsAttempts'), mean('dpsCrits'),
                     mean('dpsNonCritHits'), mean('dpsNonCritMisses'), mean('landRate'),
                     mean('postAttempt'), mean('postLand'), mean('postMiss')))
    print('  reward distribution per cell')
    print('    %-14s %-6s %8s %8s %8s %6s %6s %6s %6s'
          % ('ridge', 'DEX', 'mean', 'median', 'se', '>=10', '>=25', '>=50', '>=100'))
    for luck, atk in arms:
        for dex in args.dexs:
            values = [by_seed[(luck, atk, dex)]['chests'] for by_seed in per_seed.values()
                      if isinstance(by_seed[(luck, atk, dex)]['chests'], (int, float))]
            if not values:
                continue
            print('    %-14s %-6d %8.2f %8.1f %8.2f %6.3f %6.3f %6.3f %6.3f'
                  % ('%d/%d' % (luck, atk), dex, statistics.fmean(values),
                     statistics.median(values), statistics.pstdev(values) / (len(values) ** 0.5),
                     *[sum(1 for v in values if v >= t) / len(values) for t in THRESHOLDS]))
    print('  progression per cell')
    for luck, atk in arms:
        for dex in args.dexs:
            rows = [by_seed[(luck, atk, dex)] for by_seed in per_seed.values()]
            def med(key):
                values = [r[key] for r in rows if isinstance(r.get(key), (int, float))]
                return statistics.median(values) if values else float('nan')
            print('    %-14s DEX %-3d access %7.0f death %7.0f ticks %7.0f bossQ@death %6.1f retention %.2f'
                  % ('%d/%d' % (luck, atk), dex, med('bossAccess'), med('death'), med('ticks'),
                     statistics.fmean([r['bossQueueAtDeath'] for r in rows
                                       if isinstance(r['bossQueueAtDeath'], (int, float))] or [float('nan')]),
                     statistics.fmean([r['retention'] for r in rows
                                       if isinstance(r['retention'], (int, float))] or [float('nan')])))
    if args.json:
        Path(args.json).write_text(json.dumps(output, indent=1, default=str), encoding='utf-8')
        print('wrote', args.json)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
