"""DIAGNOSTIC ONLY - derive the ATK compensation curve for raised Luck under matched TTK. Read-only.

For each Luck arm, scans legal ATK values and picks the one whose boss/follower TTK vector best
matches a reference cell's vector (boss p10/p50/p90 in hits plus each distinct follower's median),
using only the authoritative recovered damage PMF and hits-to-kill convolution. Also reports the
incoming enemy accuracy profile per Luck arm from `hit_rate`, so the accuracy cliff is measured.

    python derive_atk_compensation.py --encounter 18 --candidate a3d527c888ca7d \
        --reference 200:40 --luck 40 123 299 364 700
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

from combat_initial_state import i32, trunc_div                       # noqa: E402
from combat_resolution import critical_rate, hit_rate                # noqa: E402
from derive_atk_arms import (LUCK_PID, ATK_PID, base_damage_pmf, mix, summarise)  # noqa: E402
from test_atk_luck_speed_grid import roster                          # noqa: E402
from test_counter_level_arms import FAMILY                           # noqa: E402
from test_speed_arms import DEFAULT_LIBRARY, dps_unit, load_candidate  # noqa: E402
from strategy_optimizer_adapter import stats                         # noqa: E402

SPD_PID = 15
ATK_MIN, ATK_MAX = 6, 900
FALLBACK_MEAN = 5.5


def mean_damage(attack, defense, critical=False):
    """Exact expected damage per landed hit, including the fallback branch's uniform 1..10 mean."""
    armor = trunc_div(i32(defense), 8) if critical else i32(defense)
    total = 0.0
    for attack_roll in range(80, 121):
        scaled = max(1, trunc_div(i32(i32(attack) * attack_roll), 100))
        for defense_roll in range(70, 91):
            damage = i32(scaled - trunc_div(i32(armor * defense_roll), 100))
            total += FALLBACK_MEAN if damage <= 5 else damage
    return total / (41 * 21)


def expected_damage(atk, luck, defense):
    weight = critical_rate(luck) / 100.0
    return (1 - weight) * mean_damage(atk, defense, False) \
        + weight * mean_damage(atk, defense, True)


def solve_atk(target, luck, defense):
    """Smallest legal ATK whose expected damage per hit reaches `target` (monotone in ATK)."""
    lo, hi = ATK_MIN, ATK_MAX
    if expected_damage(hi, luck, defense) < target:
        return hi
    while lo < hi:
        mid = (lo + hi) // 2
        if expected_damage(mid, luck, defense) >= target:
            hi = mid
        else:
            lo = mid + 1
    best = min(range(max(ATK_MIN, lo - 4), min(ATK_MAX, lo + 4) + 1),
               key=lambda a: abs(expected_damage(a, luck, defense) - target))
    return best


def hits_to_kill_fast(pmf, hp, cutoff=1e-11):
    """Hits-to-kill distribution via an array DP over remaining HP, pruning negligible states."""
    items = sorted(pmf.items())
    alive = [0.0] * (hp + 1)
    alive[hp] = 1.0
    out = {}
    hits = 0
    while True:
        hits += 1
        nxt = [0.0] * (hp + 1)
        dead = 0.0
        for remaining in range(1, hp + 1):
            probability = alive[remaining]
            if probability < cutoff:
                continue
            for damage, weight in items:
                left = remaining - damage
                if left <= 0:
                    dead += probability * weight
                else:
                    nxt[left] += probability * weight
        if dead > 0:
            out[hits] = dead
        if not any(value >= cutoff for value in nxt):
            break
        alive = nxt
        if hits > hp + 8:
            break
    return out


def ttk_vector(atk, luck, targets, per_action):
    weight = critical_rate(luck) / 100.0
    out = {}
    for tag, target, kind in targets:
        pmf = mix((1 - weight, base_damage_pmf(atk, target['defense'], False)),
                  (weight, base_damage_pmf(atk, target['defense'], True)))
        out[tag] = summarise(hits_to_kill_fast(pmf, target['hp']))
    boss = out['boss']
    return out, dict(p10=boss['p10'], median=boss['median'], p90=boss['p90'],
                     actions=dict(p10=round(boss['p10'] / per_action, 1),
                                  median=round(boss['median'] / per_action, 1),
                                  p90=round(boss['p90'] / per_action, 1)))


def vector_error(candidate, reference):
    """Normalised L1 distance over the TTK vector (boss p10/p50/p90 + follower medians)."""
    total, parts = 0.0, {}
    for tag, values in reference.items():
        keys = ('p10', 'median', 'p90') if tag == 'boss' else ('median',)
        for key in keys:
            ref = reference[tag][key]
            got = candidate[tag][key]
            value = abs(got - ref) / max(1.0, float(ref))
            parts['%s.%s' % (tag, key)] = round(value, 4)
            total += value
    return total, parts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', default=DEFAULT_LIBRARY)
    parser.add_argument('--encounter', type=int, required=True)
    parser.add_argument('--candidate', required=True)
    parser.add_argument('--reference', required=True, help='ATK:Luck of the reference cell')
    parser.add_argument('--luck', type=int, nargs='+', required=True)
    parser.add_argument('--speed', type=int, help='DPS Speed to use; defaults to stored')
    parser.add_argument('--per-action', type=float, default=4.77311)
    parser.add_argument('--json')
    args = parser.parse_args(argv)

    db = sqlite3.connect('file:%s?mode=ro' % args.library, uri=True)
    db.row_factory = sqlite3.Row
    candidate, scenario, label = load_candidate(db, args.candidate)
    name = dps_unit(scenario)['name']
    stored = stats(scenario)[name]['parameters']
    speed = args.speed if args.speed is not None else stored[SPD_PID]['value']
    ref_atk, ref_luck = (int(part) for part in args.reference.split(':'))
    enemies = roster(scenario)
    boss = next(e for e in enemies if e['boss'])
    counts = collections.Counter((e['name'], e['hp'], e['defense']) for e in enemies)
    boss_key = (boss['name'], boss['hp'], boss['defense'])
    followers = [dict(name=n, hp=h, defense=d, count=c, boss=False)
                 for (n, h, d), c in counts.items() if (n, h, d) != boss_key]
    followers.sort(key=lambda f: (-f['count'], -f['hp']))
    targets = [('boss', boss, True)] + [('f%d' % i, f, False) for i, f in enumerate(followers[:3])]

    print('encounter %d  candidate %s  %s' % (args.encounter, candidate[:12], label[:46]))
    print('  DPS %s  Speed %d  reference cell ATK %d / Luck %d (crit %d%%)'
          % (name, speed, ref_atk, ref_luck, critical_rate(ref_luck)))
    print('  targets: %s' % [(tag, t['name'], t['hp'], t['defense'], t.get('count', 1))
                             for tag, t, _ in targets])
    reference_vector, reference_boss = ttk_vector(ref_atk, ref_luck, targets, args.per_action)
    print('  reference TTK vector: boss p10/p50/p90 %d/%d/%d hits (~%.1f/%.1f/%.1f actions)'
          % (reference_boss['p10'], reference_boss['median'], reference_boss['p90'],
             reference_boss['actions']['p10'], reference_boss['actions']['median'],
             reference_boss['actions']['p90']))
    for tag, target, _ in targets[1:]:
        print('    follower %s (%s hp %d def %d x%d) median %d hits'
              % (tag, target['name'], target['hp'], target['defense'], target['count'],
                 reference_vector[tag]['median']))

    report = dict(encounter=args.encounter, candidate=candidate, dps=name, speed=speed,
                  reference=dict(atk=ref_atk, luck=ref_luck, vector=reference_vector,
                                 boss=reference_boss), arms={})
    print('  compensation curve')
    print('    %-6s %-6s %-8s %-10s %-14s %-16s %-10s'
          % ('Luck', 'crit', 'enemyHit', 'ATK', 'bossTTKp50', 'followerMedians', 'error'))
    for luck in args.luck:
        accuracy = {row['name']: hit_rate(row['dex'], speed, luck) for row in enemies}
        unique = {}
        for enemy in enemies:
            unique.setdefault(enemy['name'], hit_rate(enemy['dex'], speed, luck))
        target_damage = expected_damage(ref_atk, ref_luck, boss['defense'])
        candidates = sorted({solve_atk(target_damage * scale, luck, boss['defense'])
                             for scale in (0.85, 1.0, 1.15)})
        best = None
        for atk in candidates:
            vector, boss_summary = ttk_vector(atk, luck, targets, args.per_action)
            error, parts = vector_error(vector, reference_vector)
            if best is None or error < best[0]:
                best = (error, atk, vector, boss_summary, parts)
        error, atk, vector, boss_summary, parts = best
        report['arms'][luck] = dict(
            crit=critical_rate(luck), accuracy=unique, accuracyMin=min(unique.values()),
            compensatedAtk=atk, error=round(error, 4), parts=parts,
            bracket=candidates,
            expectedDamage=round(expected_damage(atk, luck, boss['defense']), 3),
            bossTtk=vector['boss'], bossBoss=boss_summary,
            followers={tag: vector[tag]['median'] for tag, _t, _b in targets[1:]})
        print('    %-6d %-6d %-8s %-10d %-14s %-16s %-10.4f'
              % (luck, critical_rate(luck), '%d-%d' % (min(unique.values()), max(unique.values())),
                 atk, '%d/%d/%d' % (vector['boss']['p10'], vector['boss']['median'],
                                    vector['boss']['p90']),
                 {tag: vector[tag]['median'] for tag, _t, _b in targets[1:]},
                 error))
        print('        accuracy profile %s' % accuracy)
        print('        bracket %s  expected damage/hit %.2f (reference %.2f)'
              % (candidates, expected_damage(atk, luck, boss['defense']), target_damage))
    keys = sorted(set().union(*[set(arm['followers']) for arm in report['arms'].values()])
                  if report['arms'] else [])
    print('  follower medians per arm: %s'
          % {luck: report['arms'][luck]['followers'] for luck in report['arms']})
    print('  reference follower medians: %s'
          % {tag: reference_vector[tag]['median'] for tag in keys})
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=1, default=str), encoding='utf-8')
        print('wrote', args.json)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
