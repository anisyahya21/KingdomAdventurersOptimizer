"""DIAGNOSTIC ONLY - derive mechanically distinct DPS Attack regions from the recovered damage model.

Read-only. Uses the authoritative recovered damage path (`combat_resolution.base_damage` /
`damage_from_parameters`): attack roll uniform 80..120, defense roll uniform 70..90, and the
fallback branch (raw damage <= 5 -> uniform 1..10). Builds the exact per-hit damage PMF, convolves it
into a hits-to-kill distribution for the boss and the dominant follower, and finds the ATK values
where the structure actually changes (fallback exit, half-real, all-real). One real battle is run to
check the observed DPS damage values against the analytic PMF.

    python derive_atk_arms.py --encounter 18 --candidate a3d527c888ca7d
"""
from __future__ import annotations

import argparse
import collections
import json
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import combat_sandbox                                              # noqa: E402
import combat_setup                                                # noqa: E402
import combat_shared_controllers as csc                            # noqa: E402
import strategy_optimizer                                          # noqa: E402
from combat_initial_state import i32, trunc_div                    # noqa: E402
from combat_resolution import critical_rate, skill_invocation_rate  # noqa: E402
from combat_runtime_data import load_data                          # noqa: E402
from combat_skill_selection import active_skill_infos              # noqa: E402
from strategy_optimizer_adapter import validate_scenario, stats     # noqa: E402
from test_counter_level_arms import FAMILY                         # noqa: E402
from test_speed_arms import load_candidate                         # noqa: E402

DEFAULT_LIBRARY = 'A:/KingdomAdventurersOptimizer/strategiesv.student1only.sqlite'
ATK_PID, DEF_PID, SPD_PID, LUCK_PID, DEX_PID = 13, 14, 15, 16, 19
ATK_DOMAIN = (6, 5766)              # derive_search_bounds.SUPPLIED['atk'] minimum/ceiling
SKILLS = {r['id']: r for r in load_data('weapon-skill-profiles.json')['skills']}


def base_damage_pmf(attack, defense, critical=False):
    """Exact per-hit damage PMF from the recovered formula, over both uniform rolls."""
    armor = trunc_div(i32(defense), 8) if critical else i32(defense)
    pmf = collections.defaultdict(float)
    for attack_roll in range(80, 121):
        scaled = max(1, trunc_div(i32(i32(attack) * attack_roll), 100))
        for defense_roll in range(70, 91):
            damage = i32(scaled - trunc_div(i32(armor * defense_roll), 100))
            if damage <= 5:
                for value in range(1, 11):
                    pmf[value] += 0.1
            else:
                pmf[damage] += 1.0
    total = 41 * 21
    return {value: weight / total for value, weight in pmf.items()}


def mix(*weighted):
    out = collections.defaultdict(float)
    for weight, table in weighted:
        for value, probability in table.items():
            out[value] += weight * probability
    return dict(out)


def fallback_share(pmf):
    return sum(p for value, p in pmf.items() if value <= 10)


def fallback_fraction(attack, defense, critical=False):
    """Exact fraction of the (attack_roll, defense_roll) grid that lands in the fallback branch."""
    armor = trunc_div(i32(defense), 8) if critical else i32(defense)
    count = 0
    for attack_roll in range(80, 121):
        scaled = max(1, trunc_div(i32(i32(attack) * attack_roll), 100))
        for defense_roll in range(70, 91):
            if i32(scaled - trunc_div(i32(armor * defense_roll), 100)) <= 5:
                count += 1
    return count / (41 * 21)


def hits_to_kill(pmf, hp):
    """Distribution of the number of hits needed to remove `hp`."""
    alive = {hp: 1.0}
    out = {}
    hits = 0
    while alive and hits < 100000:
        hits += 1
        nxt = collections.defaultdict(float)
        for remaining, probability in alive.items():
            for damage, weight in pmf.items():
                left = remaining - damage
                if left <= 0:
                    out[hits] = out.get(hits, 0.0) + probability * weight
                else:
                    nxt[left] += probability * weight
        alive = nxt
    return out


def quantile(distribution, q):
    total, running = sum(distribution.values()), 0.0
    for value in sorted(distribution):
        running += distribution[value]
        if running >= q * total:
            return value
    return max(distribution)


def summarise(distribution):
    total = sum(distribution.values())
    mean = sum(value * weight for value, weight in distribution.items()) / total
    return dict(mean=round(mean, 2), median=quantile(distribution, 0.5),
                p10=quantile(distribution, 0.1), p90=quantile(distribution, 0.9))


def expected_hits_per_action(scenario):
    unit = next(u for u in scenario['ownUnits'] if set(u['skills']) == set(FAMILY))
    infos = active_skill_infos([SKILLS[s] for s in unit['skills']], unit['invocationLevels'],
                               10 ** 6, lambda s: 0)
    survival, total = 1.0, 0.0
    for skill, level in infos:
        rate = skill_invocation_rate(skill['type'], level) / 100.0
        total += survival * rate * skill['count']
        survival *= 1 - rate
    return total + survival


def enemies_of(scenario):
    prepared = combat_setup.prepare_setup(json.loads(json.dumps(scenario)))
    out = []
    for fighter in prepared['encounter']['fighters']:
        out.append(dict(name=fighter['name'], boss=bool(fighter['leaderIdentity']),
                        hp=fighter['parameters']['10']['rawValue'],
                        defense=fighter['effectiveDefense']))
    return out


def observed_damage(scenario, seeds, dps_name):
    """Histogram of the DPS's landed damage values in one real battle, for PMF validation."""
    capture = {}
    original = csc.update_fighters

    def wrapped(teams, battle_state, update_state, execute_commands, after_fighter=None):
        if after_fighter is None:
            original(teams, battle_state, update_state, execute_commands)
        else:
            original(teams, battle_state, update_state, execute_commands, after_fighter)
        capture['engine'] = update_state.__self__

    scenario = dict(validate_scenario(scenario), mathSeed=int(seeds[0]), libSeed=int(seeds[1]))
    csc.update_fighters = wrapped
    try:
        combat_sandbox.run_scenario(scenario, include_trace=True)
    finally:
        csc.update_fighters = original
    engine = capture.get('engine')
    names = getattr(engine, 'names', {}) or {}
    identity = next((i for i, name in names.items() if name == dps_name), None)
    if identity is None:
        identity = names.get(dps_name)
    boss_identity = next((i for i, spec in engine.specs.items() if spec.get('boss')), None)
    histogram = collections.Counter()
    boss_histogram = collections.Counter()
    attempts = boss_attempts = 0
    for event in (engine.trace if engine else []):
        if event.get('kind') == 'attack' and event.get('attacker') == identity:
            attempts += 1
            if event.get('target') == boss_identity:
                boss_attempts += 1
            if not event.get('hit'):
                continue
            histogram[int(event.get('damage') or 0)] += 1
            if event.get('target') == boss_identity:
                boss_histogram[int(event.get('damage') or 0)] += 1
    return dict(all=histogram, boss=boss_histogram, attempts=attempts, bossAttempts=boss_attempts)


def boundaries(attack_range, defense, critical):
    """First ATK where the fallback share leaves 1.0, crosses 0.5, and reaches 0.0."""
    exit_atk = half_atk = real_atk = None
    for attack in attack_range:
        share = fallback_fraction(attack, defense, critical)
        if exit_atk is None and share < 1.0:
            exit_atk = attack
        if half_atk is None and share < 0.5:
            half_atk = attack
        if share == 0.0:
            real_atk = attack
            break
    return dict(fallbackExit=exit_atk, halfReal=half_atk, allReal=real_atk)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', default=DEFAULT_LIBRARY)
    parser.add_argument('--encounter', type=int, required=True)
    parser.add_argument('--candidate', required=True)
    parser.add_argument('--validate-seed', type=int, nargs=2, default=[1900000, 1900001])
    parser.add_argument('--atk', type=int, nargs='+', help='ATK arms to summarise')
    parser.add_argument('--json')
    args = parser.parse_args(argv)

    db = sqlite3.connect('file:%s?mode=ro' % args.library, uri=True)
    db.row_factory = sqlite3.Row
    candidate, scenario, label = load_candidate(db, args.candidate)
    unit = next(u for u in scenario['ownUnits'] if set(u['skills']) == set(FAMILY))
    name = unit['name']
    current = stats(scenario)[name]['parameters']
    attack, luck = current[ATK_PID]['value'], current[LUCK_PID]['value']
    crit = critical_rate(luck)
    per_action = expected_hits_per_action(scenario)
    enemies = enemies_of(scenario)
    boss = next(e for e in enemies if e['boss'])
    follower = max((e for e in enemies if not e['boss']), key=lambda e: e['hp'])
    print('encounter %d  candidate %s  %s' % (args.encounter, candidate[:12], label[:46]))
    print('  DPS %s  ATK %d  Luck %d (crit %d%% = %.4f)  expected hits/action %.4f'
          % (name, attack, luck, crit, crit / 100.0, per_action))
    print('  boss %s hp %d def %d | dominant follower %s hp %d def %d'
          % (boss['name'], boss['hp'], boss['defense'], follower['name'], follower['hp'],
             follower['defense']))
    report = dict(candidate=candidate, encounter=args.encounter, label=label, dps=name,
                  currentAttack=attack, luck=luck, critRate=crit,
                  expectedHitsPerAction=per_action, boss=boss, follower=follower)

    edges = {}
    for tag, critical in (('normal', False), ('crit', True)):
        edges[tag] = dict(boss=boundaries(range(ATK_DOMAIN[0], ATK_DOMAIN[1] + 1),
                                          boss['defense'], critical),
                          follower=boundaries(range(ATK_DOMAIN[0], ATK_DOMAIN[1] + 1),
                                              follower['defense'], critical))
    report['boundaries'] = edges
    for tag in ('normal', 'crit'):
        print('  %-6s fallback exit/half/all-real: boss %s  follower %s'
              % (tag, edges[tag]['boss'], edges[tag]['follower']))

    arms = args.atk or sorted({attack, edges['normal']['follower']['halfReal'] or attack,
                               edges['normal']['boss']['halfReal'] or attack,
                               edges['normal']['boss']['allReal'] or attack})
    report['arms'] = {}
    for value in arms:
        weight = crit / 100.0
        blob = mix((1 - weight, base_damage_pmf(value, boss['defense'], False)),
                   (weight, base_damage_pmf(value, boss['defense'], True)))
        follower_blob = mix((1 - weight, base_damage_pmf(value, follower['defense'], False)),
                            (weight, base_damage_pmf(value, follower['defense'], True)))
        boss_ttk = summarise(hits_to_kill(blob, boss['hp']))
        follower_ttk = summarise(hits_to_kill(follower_blob, follower['hp']))
        report['arms'][value] = dict(
            fallbackShareBoss=round((1 - weight) * fallback_fraction(value, boss['defense'], False)
                                    + weight * fallback_fraction(value, boss['defense'], True), 4),
            normalDamage=dict(min=min(base_damage_pmf(value, boss['defense'])),
                              max=max(base_damage_pmf(value, boss['defense'])),
                              mean=round(sum(d * p for d, p in
                                             base_damage_pmf(value, boss['defense']).items()), 2)),
            critDamage=dict(min=min(base_damage_pmf(value, boss['defense'], True)),
                            max=max(base_damage_pmf(value, boss['defense'], True)),
                            mean=round(sum(d * p for d, p in
                                           base_damage_pmf(value, boss['defense'], True).items()), 2)),
            bossTtkHits=boss_ttk,
            bossTtkActions=round(boss_ttk['mean'] / per_action, 1),
            followerTtkHits=follower_ttk)
        arm = report['arms'][value]
        print('    ATK %-5d fallback %.3f | normal %d..%d (mean %.1f) crit %d..%d (mean %.1f)'
              % (value, arm['fallbackShareBoss'], arm['normalDamage']['min'],
                 arm['normalDamage']['max'], arm['normalDamage']['mean'], arm['critDamage']['min'],
                 arm['critDamage']['max'], arm['critDamage']['mean']))
        print('              boss TTK hits %s (~%.1f actions) | follower TTK %s'
              % (boss_ttk, arm['bossTtkActions'], follower_ttk))

    histograms = observed_damage(scenario, args.validate_seed, name)
    histogram = histograms['all']
    boss_histogram = histograms['boss']
    analytic = mix((1 - crit / 100.0, base_damage_pmf(attack, boss['defense'], False)),
                   (crit / 100.0, base_damage_pmf(attack, boss['defense'], True)))
    observed_mean = (sum(d * n for d, n in histogram.items()) / sum(histogram.values())
                     if histogram else float('nan'))
    boss_mean = (sum(d * n for d, n in boss_histogram.items()) / sum(boss_histogram.values())
                 if boss_histogram else float('nan'))
    analytic_mean = sum(d * p for d, p in analytic.items())
    report['validation'] = dict(observedHits=sum(histogram.values()),
                               observedMean=round(observed_mean, 2),
                               observedBossHits=sum(boss_histogram.values()),
                               observedBossMean=round(boss_mean, 2),
                               attempts=histograms['attempts'],
                               bossAttempts=histograms['bossAttempts'],
                               analyticMean=round(analytic_mean, 2),
                               observedMin=min(histogram) if histogram else None,
                               observedMax=max(histogram) if histogram else None,
                               observedBossMin=min(boss_histogram) if boss_histogram else None,
                               observedBossMax=max(boss_histogram) if boss_histogram else None,
                               analyticMin=min(analytic), analyticMax=max(analytic))
    print('  PMF validation at ATK %d (boss DEF %d), one real battle: %s'
          % (attack, boss['defense'], report['validation']))
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=1, default=str), encoding='utf-8')
        print('wrote', args.json)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
