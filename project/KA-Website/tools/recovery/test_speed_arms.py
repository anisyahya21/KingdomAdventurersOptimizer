"""DIAGNOSTIC ONLY - Speed-class (attack_interval) arms vs boss-death alignment. Read-only.

Question: can DPS Speed be used deliberately to increase the boss-directed useful work retained
at boss death? Speed is treated as an attack-interval CLASS, never as an arbitrary percentage:
each arm sits one `attack_interval(AGI)` step away from the current class, computed with the
authoritative recovered function and kept inside the legal spd search domain [5, 4780].

Everything else is preserved: ATK / Luck / Dexterity / Intelligence / HP / MP / DEF, skill set,
skill order, invocation levels, Counter level, equipment, placement, support and Holy Herb policy.
Only the DPS's Speed parameter (pid 15) changes, by exactly the raw delta needed to land on the
target effective AGI. The script prints the pre-simulation parity check (effective parameters minus
Speed, crit rate, hit rate) and then the paired mediator results.

    python test_speed_arms.py --encounters 16 18 19 --pairs 32 --json out.json
    python test_speed_arms.py --encounters 16 --probe
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
from combat_resolution import attack_interval, critical_rate, hit_rate  # noqa: E402
from combat_runtime_data import load_data                          # noqa: E402
from strategy_optimizer_adapter import validate_scenario, stats     # noqa: E402
from test_counter_level_arms import pick_candidate, FAMILY, ATTACKABLE   # noqa: E402

DEFAULT_LIBRARY = 'A:/KingdomAdventurersOptimizer/strategiesv.student1only.sqlite'
SPD_PID = 15                        # parameter id for 'spd' (strategy_naming.STAT_PARAMETER_IDS)
DEX_PID = 19
LUCK_PID = 16
SPD_DOMAIN = (5, 4780)             # derive_search_bounds.SUPPLIED['spd'] minimum/ceiling
SKILLS = {r['id']: r for r in load_data('weapon-skill-profiles.json')['skills']}


def param_entry(unit, pid):
    """The scenario's raw parameter block for `pid`, tolerating str or int keys."""
    block = unit['parameters']
    if str(pid) in block:
        return block[str(pid)]
    return block.get(pid)


def dps_unit(scenario):
    for unit in scenario['ownUnits']:
        if set(unit['skills']) == set(FAMILY):
            return unit
    return None


def load_candidate(db, candidate_id):
    """One named resident candidate's stored scenario, by id (prefix match)."""
    row = db.execute('SELECT c.id AS id, c.scenario AS scenario, c.label AS label FROM candidate c '
                     'WHERE c.id LIKE ?', (candidate_id + '%',)).fetchone()
    if row is None:
        return None
    return row['id'], json.loads(row['scenario']), row['label']


def neighbour_agi(agi0, target, domain=SPD_DOMAIN):
    """The nearest effective AGI with `attack_interval == target`, or None if unreachable here.

    `attack_interval` is non-increasing in AGI, so walking down from the current value finds the
    largest AGI for a slower class and walking up finds the smallest AGI for a faster class.
    """
    lo, hi = domain
    base = attack_interval(agi0)
    if target > base:
        agi = agi0
        while agi > lo:
            agi -= 1
            value = attack_interval(agi)
            if value == target:
                return agi
            if value > target:
                return None
    elif target < base:
        agi = agi0
        while agi < hi:
            agi += 1
            value = attack_interval(agi)
            if value == target:
                return agi
            if value < target:
                return None
    return None


def build_arm(scenario, dps_name, target_agi):
    """A copy whose DPS effective Speed is `target_agi` and whose every other value is untouched."""
    out = json.loads(json.dumps(scenario))
    unit = next(u for u in out['ownUnits'] if u['name'] == dps_name)
    current = stats(scenario)[dps_name]['parameters'][SPD_PID]['value']
    entry = param_entry(unit, SPD_PID)
    entry['rawValue'] = int(entry['rawValue']) + (int(target_agi) - int(current))
    return out


def arms(scenario, dps_name):
    """{label: (scenario, effective AGI, interval)} - current class plus its adjacent classes."""
    current = stats(scenario)[dps_name]['parameters'][SPD_PID]['value']
    base = attack_interval(current)
    plan = [('slower', base + 1), ('current', base), ('faster', base - 1), ('faster2', base - 2)]
    out = {}
    for label, target in plan:
        if label == 'current':
            out[label] = (json.loads(json.dumps(scenario)), current, base)
            continue
        agi = neighbour_agi(current, target)
        if agi is None:
            continue
        out[label] = (build_arm(scenario, dps_name, agi), agi, target)
    return out


def parity(scenario, name, variants):
    """Pre-simulation comparison: everything except the DPS Speed parameter must be identical."""
    prepared = {label: stats(arm) for label, (arm, _agi, _interval) in variants.items()}
    reference = prepared['current']
    changed = []
    for label, table in prepared.items():
        if label == 'current':
            continue
        for fighter, values in table.items():
            ref = reference[fighter]
            for pid, block in values['parameters'].items():
                if fighter == name and pid == SPD_PID:
                    continue
                if block != ref['parameters'].get(pid):
                    changed.append(((label, fighter, pid), ref['parameters'].get(pid), block))
            for key in ('effectiveDefense', 'averageTrainingLevel', 'weaponId', 'weaponRange',
                        'skillCosts', 'monster'):
                if values.get(key) != ref.get(key):
                    changed.append(((label, fighter, key), ref.get(key), values.get(key)))
    table = {}
    for label, (_arm, agi, interval) in variants.items():
        row = reference[name]['parameters']
        table[label] = dict(
            agi=agi, interval=interval, uninterruptedPeriod=interval + 21,
            critRate=critical_rate(row[LUCK_PID]['value']),
            hitRate=hit_rate(row[DEX_PID]['value'], agi, row[LUCK_PID]['value']),
        )
    return dict(changed=changed, table=table)


def replay(scenario, seeds, dps_name):
    sample, meta = {}, {}
    original = csc.update_fighters

    def wrapped(teams, battle_state, update_state, execute_commands, after_fighter=None):
        if after_fighter is None:
            original(teams, battle_state, update_state, execute_commands)
        else:
            original(teams, battle_state, update_state, execute_commands, after_fighter)
        engine = update_state.__self__
        if not meta:
            identity_of = {value: key for key, value in engine.names.items()}
            meta['identity_of'] = identity_of
            meta['boss'] = next(i for i, spec in engine.specs.items() if not spec['human']
                                and spec.get('boss'))
            meta['dps'] = next(i for i, name in identity_of.items() if name == dps_name)
            meta['own_grid'] = {spec['name']: spec['grid'] for spec in engine.specs.values()
                                if spec['human']}
        row = {}
        for identity, components in engine.units.items():
            row[meta['identity_of'][identity]] = (
                components['board'][5], engine.value(identity, 10), engine.value(identity, 11),
                tuple((c.get('target'), c.get('skill'), c.get('use_index'))
                      for c in components.get('commands') or ()))
        sample[engine.tick] = row

    scenario = dict(validate_scenario(scenario), mathSeed=int(seeds[0]), libSeed=int(seeds[1]))
    csc.update_fighters = wrapped
    try:
        report = combat_sandbox.run_scenario(scenario, include_trace=True)
    finally:
        csc.update_fighters = original
    return measure(report, sample, meta)


def measure(report, sample, meta):
    identity_of = meta['identity_of']
    own = sorted(meta['own_grid'])
    boss = meta['boss']
    dps = meta['dps']
    trace = report['trace']
    progress = report['result'].get('progressMetrics') or {}
    entitlement = report['result'].get('rewardEntitlement') or {}
    death = progress.get('bossDeathTick')
    death = None if (death is None or death < 0) else death
    ticks = sorted(sample)

    def queue_row(tick):
        row = sample.get(tick)
        if row is None:
            return (0, 0, 0)
        commands = [c for name in own for c in row.get(name, (8, 0, 0, ()))[3]]
        boss_commands = [c for c in commands if c[0] == boss]
        mass = sum(max(0, int(SKILLS[c[1]]['count']) - int(c[2] or 0)) for c in boss_commands)
        return (len(commands), len(boss_commands), mass)

    peak_total = peak_boss = peak_mass = 0
    for tick in ticks:
        total, boss_count, mass = queue_row(tick)
        peak_total = max(peak_total, total)
        peak_boss = max(peak_boss, boss_count)
        peak_mass = max(peak_mass, mass)
    if death is not None:
        total_at_death, boss_at_death, mass_at_death = queue_row(death)
    else:
        total_at_death = boss_at_death = mass_at_death = None
    retention = (mass_at_death / peak_mass) if (mass_at_death is not None and peak_mass) else None

    dps_checks = [event['tick'] for event in trace
                  if event.get('kind') == 'attack' and event.get('target') == dps]
    dps_incoming_hits = [event['tick'] for event in trace
                         if event.get('kind') == 'attack' and event.get('target') == dps
                         and event.get('hit')]
    dps_outgoing = [event['tick'] for event in trace
                    if event.get('kind') == 'attack' and event.get('attacker') == dps]
    dps_outgoing_hits = [event['tick'] for event in trace
                         if event.get('kind') == 'attack' and event.get('attacker') == dps
                         and event.get('hit')]
    counter_enqueues = [(event['tick'], event.get('target')) for event in trace
                        if event.get('kind') == 'enqueue' and event.get('caster') == dps
                        and event.get('source') == 'counter']
    active_enqueues = [event['tick'] for event in trace
                       if event.get('kind') == 'enqueue' and event.get('caster') == dps
                       and event.get('source') == 'charge']
    boss_hits = [event['tick'] for event in trace
                 if event.get('kind') == 'attack' and event.get('hit')
                 and event.get('target') == boss]
    pre_death_hits = [tick for tick in boss_hits if death is None or tick < death]
    post_death_hits = [tick for tick in boss_hits if death is not None and tick >= death]
    releases_after = [event['tick'] for event in trace
                      if event.get('kind') == 'release' and event.get('target') == boss
                      and death is not None and event['tick'] >= death]
    gaps = [b - a for a, b in zip(post_death_hits, post_death_hits[1:])]
    first_boss_target = next((event['tick'] for event in trace
                              if event.get('kind') == 'enqueue' and event.get('target') == boss), None)

    def suppressed_at(tick):
        row = sample.get(tick) or {}
        return not [name for name in own
                    if row.get(name, (8, 0, 0, ()))[1] > 0
                    and row.get(name, (8, 0, 0, ()))[0] in ATTACKABLE
                    and 0 <= meta['own_grid'].get(name, 9) <= 4]

    suppressed = [tick for tick in ticks if suppressed_at(tick)]
    dps_name = identity_of[dps]
    using = [tick for tick in ticks if sample[tick].get(dps_name, (8, 0, 0, ()))[0] == 5]
    longest = window = 0
    using_set = set(using)
    for tick in ticks:
        if tick in using_set:
            window += 1
            longest = max(longest, window)
        else:
            window = 0
    mp_rows = [row for row in (report['result'].get('mpMetrics') or [])
               if row.get('identity') == dps]
    mp_min = mp_rows[0].get('minimumMp') if mp_rows else None
    mp_zero = bool(mp_rows[0].get('reachedZero')) if mp_rows else None
    herb_uses = sum(1 for row in (report.get('holyHerbUses') or []) if row.get('used'))
    return dict(
        chests=entitlement.get('awardedChestCount'), ticks=len(ticks),
        certificate=(entitlement.get('certificate') or {}).get('frame'),
        death=death, firstBossTargetTick=first_boss_target,
        peakTotalQueue=peak_total, peakBossQueue=peak_boss, peakBossMass=peak_mass,
        totalQueueAtDeath=total_at_death, bossQueueAtDeath=boss_at_death,
        bossHitMassAtDeath=mass_at_death, retentionFraction=retention,
        bossHitsBeforeDeath=len(pre_death_hits), bossHitsAfterDeath=len(post_death_hits),
        postDeathReleaseCount=len(releases_after),
        postDeathSpacingMin=(min(gaps) if gaps else None),
        postDeathSpacingMax=(max(gaps) if gaps else None),
        activeSkillEnqueues=len(active_enqueues),
        counterChecks=len(dps_checks), counterEnqueues=len(counter_enqueues),
        incomingAttackAttempts=len(dps_checks), incomingAttackHits=len(dps_incoming_hits),
        outgoingAttackAttempts=len(dps_outgoing), outgoingAttackHits=len(dps_outgoing_hits),
        bossCounterEnqueues=sum(1 for _t, target in counter_enqueues if target == boss),
        followerCounterEnqueues=sum(1 for _t, target in counter_enqueues if target != boss),
        usingSkillTicks=len(using), longestUsingSkillWindow=longest,
        suppressionTicks=len(suppressed),
        suppressionBeforeDeath=sum(1 for tick in suppressed if death is None or tick < death),
        suppressionAfterDeath=sum(1 for tick in suppressed if death is not None and tick >= death),
        mpMinimum=mp_min, reachedZeroMp=mp_zero, holyHerbUses=herb_uses,
        reEntries=progress.get('postDeathBossReentries'),
        leavings=progress.get('postDeathBossLeavings'),
        postDeathPrizes=progress.get('postDeathPrizes'),
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', default=DEFAULT_LIBRARY)
    parser.add_argument('--encounters', type=int, nargs='+', default=[16, 18, 19])
    parser.add_argument('--candidate', nargs='+',
                        help='exact resident candidate id per encounter, positionally')
    parser.add_argument('--seed-pair', type=int, nargs=2,
                        help='one historical seed pair: run it as a diagnostic before the fresh loop')
    parser.add_argument('--diagnostic-only', action='store_true')
    parser.add_argument('--pairs', type=int, default=32)
    parser.add_argument('--seed-base', type=int, default=1800000)
    parser.add_argument('--probe', action='store_true')
    parser.add_argument('--faster2', action='store_true',
                        help='also run the second-faster interval class (extra, confounded by hit rate)')
    parser.add_argument('--json')
    args = parser.parse_args(argv)

    db = sqlite3.connect('file:%s?mode=ro' % args.library, uri=True)
    db.row_factory = sqlite3.Row
    output = []
    for position, encounter in enumerate(args.encounters):
        if args.candidate:
            named = load_candidate(db, args.candidate[position])
            if named is None:
                print('encounter %d: candidate %s not found - skipped' % (
                    encounter, args.candidate[position]))
                continue
            candidate, scenario, label = named
            count = None
            mean = float('nan')
        else:
            chosen = pick_candidate(db, encounter)
            if chosen is None:
                print('encounter %d: no resident candidate carries %s - skipped' % (
                    encounter, FAMILY))
                continue
            mean, count, candidate, scenario, label = chosen
        unit = dps_unit(scenario)
        if unit is None:
            print('encounter %d candidate %s: no DPS carrying %s - skipped' % (
                encounter, candidate, FAMILY))
            continue
        name = unit['name']
        print()
        print('=== encounter %-2d  candidate %s  %s  (stored mean %s over %s)' % (
            encounter, candidate[:12], label[:44],
            'n/a' if mean != mean else '%.3f' % mean, count if count else 'n/a'))
        variants = arms(scenario, name)
        check = parity(scenario, name, variants)
        print('  DPS %s  non-speed parity: %s' % (
            name, 'all other values identical' if not check['changed']
            else 'CHANGED %s' % check['changed'][:4]))
        wanted = ('slower', 'current', 'faster', 'faster2') if args.faster2 \
            else ('slower', 'current', 'faster')
        order = [lbl for lbl in wanted if lbl in variants]
        print('    %-10s %10s %8s %8s %8s %8s' % ('arm', 'AGI', 'interval', 'period',
                                                   'crit%', 'hit%'))
        for lbl in order:
            row = check['table'][lbl]
            print('    %-10s %10d %8d %8d %8.2f %8.2f' % (
                lbl, row['agi'], row['interval'], row['uninterruptedPeriod'],
                row['critRate'], row['hitRate']))
        if args.seed_pair:
            seeds = tuple(args.seed_pair)
            cols = [lbl for lbl in ('slower', 'current', 'faster') if lbl in variants]
            print('    historical jackpot-seed diagnostic, seeds %s (diagnostic only)' % (seeds,))
            single = {lbl: replay(variants[lbl][0], seeds, name) for lbl in cols}
            diag = ('death', 'peakBossQueue', 'bossQueueAtDeath', 'bossHitMassAtDeath',
                    'peakBossMass', 'bossHitsAfterDeath', 'reEntries', 'leavings', 'chests',
                    'activeSkillEnqueues', 'counterChecks', 'bossCounterEnqueues',
                    'incomingAttackAttempts', 'incomingAttackHits', 'ticks', 'certificate')
            print('    %-24s %s' % ('diagnostic metric', '  '.join('%13s' % lbl for lbl in cols)))
            for key in diag:
                print('    %-24s %s' % (key, '  '.join('%13s' % single[lbl].get(key)
                                                        for lbl in cols)))
                output.append(dict(seed=list(seeds), encounter=encounter, candidate=candidate,
                                   arm=key, agi=None, interval=None, diagnostic=True,
                                   **{lbl: single[lbl].get(key) for lbl in cols}))
        if args.diagnostic_only:
            continue
        if args.probe:
            continue
        means = collections.defaultdict(lambda: collections.defaultdict(list))
        paired = collections.defaultdict(lambda: collections.defaultdict(list))
        for index in range(args.pairs):
            seeds = strategy_optimizer.seed_pair('validation', args.seed_base + index)
            per_arm = {}
            for lbl in order:
                metrics = replay(variants[lbl][0], seeds, name)
                per_arm[lbl] = metrics
                output.append(dict(seed=seeds, encounter=encounter, candidate=candidate, arm=lbl,
                                   agi=variants[lbl][1], interval=variants[lbl][2], **metrics))
                for key, value in metrics.items():
                    if isinstance(value, (int, float)):
                        means[lbl][key].append(value)
            for lbl, metrics in per_arm.items():
                for key, value in metrics.items():
                    reference = per_arm['current'].get(key)
                    if isinstance(value, (int, float)) and isinstance(reference, (int, float)):
                        paired[lbl][key].append(value - reference)
        keys = ('bossHitMassAtDeath', 'bossQueueAtDeath', 'totalQueueAtDeath', 'retentionFraction',
                'peakBossMass', 'death', 'ticks', 'bossHitsAfterDeath', 'postDeathReleaseCount',
                'activeSkillEnqueues', 'counterChecks', 'counterEnqueues', 'bossCounterEnqueues',
                'followerCounterEnqueues', 'incomingAttackAttempts', 'incomingAttackHits',
                'outgoingAttackAttempts', 'outgoingAttackHits', 'usingSkillTicks',
                'suppressionTicks', 'mpMinimum',
                'holyHerbUses', 'reEntries', 'leavings', 'chests', 'certificate')
        print('    %-24s %s' % ('metric', '  '.join('%13s' % lbl for lbl in order)))
        for key in keys:
            cells = []
            for lbl in order:
                values = means[lbl].get(key) or []
                cells.append('%13s' % ('%.2f' % statistics.fmean(values) if values else 'n/a'))
            print('    %-24s %s' % (key, '  '.join(cells)))
        for key in ('bossHitMassAtDeath', 'bossQueueAtDeath', 'death', 'bossHitsAfterDeath',
                    'chests', 'counterChecks'):
            cells = []
            for lbl in order:
                values = paired[lbl].get(key) or []
                if not values:
                    cells.append('%13s' % 'n/a')
                    continue
                pos = sum(1 for v in values if v > 0)
                neg = sum(1 for v in values if v < 0)
                tie = len(values) - pos - neg
                cells.append('%13s' % ('%+.2f %d/%d/%d' % (statistics.fmean(values), pos, neg, tie)))
            print('    %-24s %s' % ('paired ' + key + ' (+/-/=)', '  '.join(cells)))
    if args.json:
        Path(args.json).write_text(json.dumps(output, indent=1, default=str), encoding='utf-8')
        print('wrote', args.json)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
