"""DIAGNOSTIC ONLY - Counter level (High/Medium/Low/Off) A/B on short encounters. Read-only.

Encoding: the candidate's skills are re-declared with Counter LAST (`[7,5,4,3,2-hit, Counter]`) so
Counter's invocation level is read from its own slot while the five active multi-hit skills keep
slots 0..4. The library's Counter-FIRST encoding makes the two collide -- raising Counter's level in
place also raises the 7-hit's level -- which `check_combat_counter_invocation.py` proves. Arms differ
only in Counter's level (0 = 36%, 1 = 18%, 2 = 9%) or its absence; nothing fills the freed slot.

Measurements follow the mediator hierarchy: boss-directed future hit mass retained at the death,
then boss-directed commands, then boss-triggered Counter commands, then raw Counter count.

    python test_counter_level_arms.py --encounters 0 4 8 12 16 --pairs 16
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
from combat_runtime_data import load_data                          # noqa: E402
from strategy_optimizer_adapter import validate_scenario           # noqa: E402

DEFAULT_LIBRARY = 'A:/KingdomAdventurersOptimizer/strategiesv.student1only.sqlite'
ATTACKABLE = (1, 3, 4, 6)
FAMILY = [26, 110, 25, 24, 23, 22]
ACTIVE_ORDER = [110, 25, 24, 23, 22]
SKILLS = {r['id']: r for r in load_data('weapon-skill-profiles.json')['skills']}
ARM_LEVELS = (('high(36%)', 0), ('medium(18%)', 1), ('low(9%)', 2))


def arms(scenario):
    """{arm: scenario} - identical except Counter's level, or Counter removed."""
    out = {}
    for label, level in ARM_LEVELS:
        variant = json.loads(json.dumps(scenario))
        for unit in variant['ownUnits']:
            if set(unit['skills']) == set(FAMILY):
                unit['skills'] = ACTIVE_ORDER + [26]
                unit['invocationLevels'] = [0, 0, 0, 0, 0, level]
        out[label] = variant
    variant = json.loads(json.dumps(scenario))
    for unit in variant['ownUnits']:
        if set(unit['skills']) == set(FAMILY):
            unit['skills'] = list(ACTIVE_ORDER)
            unit['invocationLevels'] = [0, 0, 0, 0, 0]
        out['off'] = variant
    return out


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
            identity_of = {value: key for key, value in engine.names.items()}
            meta['identity_of'] = identity_of
            meta['boss'] = next(identity_of[i] for i, spec in engine.specs.items()
                                if not spec['human'] and spec.get('boss'))
            meta['own_grid'] = {spec['name']: spec['grid'] for spec in engine.specs.values()
                                if spec['human']}
        row = {}
        for identity, components in engine.units.items():
            row[meta['identity_of'][identity]] = (
                components['board'][5], engine.value(identity, 10), engine.value(identity, 11),
                tuple((command.get('target'), command.get('skill'), command.get('use_index'))
                      for command in components.get('commands') or ()))
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
    boss_identity = next(identity for identity, name in identity_of.items() if name == meta['boss'])
    dps = max(own, key=lambda name: sum(len(sample[tick].get(name, (8, 0, 0, ()))[3])
                                        for tick in sample))
    dps_identity = next(identity for identity, name in identity_of.items() if name == dps)
    trace = report['trace']
    progress = report['result'].get('progressMetrics') or {}
    entitlement = report['result'].get('rewardEntitlement') or {}
    death = progress.get('bossDeathTick')
    death = None if (death is None or death < 0) else death
    ticks = sorted(sample)

    checks = [event['tick'] for event in trace
              if event.get('kind') == 'attack' and event.get('target') == dps_identity]
    enqueues = [(event['tick'], event.get('target')) for event in trace
                if event.get('kind') == 'enqueue' and event.get('caster') == dps_identity
                and event.get('source') == 'counter']
    counter_mp = sum(event.get('amount') or 0 for event in trace
                     if event.get('kind') == 'mp' and event.get('caster') == dps_identity
                     and (SKILLS.get(event.get('skill')) or {}).get('type') == 20)

    def queue_at(tick):
        row = sample.get(tick) or sample.get(min(ticks, key=lambda t: abs(t - tick)))
        commands = [c for name in own for c in row.get(name, (8, 0, 0, ()))[3]]
        boss_commands = [c for c in commands if c[0] == boss_identity]
        mass = sum(max(0, int(SKILLS[c[1]]['count']) - int(c[2] or 0)) for c in boss_commands)
        return (len(commands), len(boss_commands), mass,
                sum(1 for c in boss_commands if c[1] == 26))

    if death is not None:
        total, boss_total, mass, counter_boss = queue_at(death)
    else:
        total = boss_total = mass = counter_boss = None
    hits_after = [event['tick'] for event in trace
                  if event.get('kind') == 'attack' and event.get('hit')
                  and event.get('target') == boss_identity
                  and death is not None and event['tick'] >= death]
    releases_after = [event['tick'] for event in trace
                      if event.get('kind') == 'release' and event.get('target') == boss_identity
                      and death is not None and event['tick'] >= death]
    return dict(
        chests=entitlement.get('awardedChestCount'), ticks=len(ticks), death=death,
        certificate=(entitlement.get('certificate') or {}).get('frame'),
        counterChecks=len(checks), counterEnqueues=len(enqueues),
        bossCounterEnqueues=sum(1 for _tick, target in enqueues if target == boss_identity),
        followerCounterEnqueues=sum(1 for _tick, target in enqueues if target != boss_identity),
        counterMp=counter_mp,
        totalQueueAtDeath=total, bossQueueAtDeath=boss_total, bossHitMassAtDeath=mass,
        bossCounterQueuedAtDeath=counter_boss,
        bossReleasesAfterDeath=len(releases_after), bossHitsAfterDeath=len(hits_after),
        reEntries=progress.get('postDeathBossReentries'),
        leavings=progress.get('postDeathBossLeavings'),
        postDeathPrizes=progress.get('postDeathPrizes'),
        usingSkillTicks=sum(1 for tick in ticks if sample[tick].get(dps, (8, 0, 0, ()))[0] == 5),
        noTargetTicks=sum(1 for tick in ticks
                          if not [name for name in own
                                  if sample[tick].get(name, (8, 0, 0, ()))[1] > 0
                                  and sample[tick].get(name, (8, 0, 0, ()))[0] in ATTACKABLE
                                  and 0 <= meta['own_grid'].get(name, 9) <= 4]))


def pick_candidate(db, encounter, wanted=FAMILY):
    """The best-measured resident candidate on this encounter carrying the exact skill family."""
    rows = db.execute('SELECT c.id AS id, c.scenario AS scenario, c.label AS label FROM candidate c '
                      'JOIN candidate_meta m ON m.id=c.id WHERE m.encounter=?', (encounter,)).fetchall()
    best = None
    for row in rows:
        scenario = json.loads(row['scenario'])
        if set(scenario['ownUnits'][0]['skills']) != set(wanted):
            continue
        total = db.execute('SELECT value FROM meta WHERE key=?',
                           ('aggregate:%s:validation' % row['id'],)).fetchone()
        total = json.loads(total[0]) if total else {}
        count = int(total.get('chestCount') or 0)
        mean = (float(total.get('chestSum') or 0) / count) if count else -1
        if count >= 32 and (best is None or mean > best[0]):
            best = (mean, count, row['id'], scenario, row['label'])
    return best


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', default=DEFAULT_LIBRARY)
    parser.add_argument('--encounters', type=int, nargs='+', default=[0, 4, 8, 12, 16])
    parser.add_argument('--pairs', type=int, default=16)
    parser.add_argument('--seed-base', type=int, default=1600000)
    parser.add_argument('--json')
    args = parser.parse_args(argv)

    db = sqlite3.connect('file:%s?mode=ro' % args.library, uri=True)
    db.row_factory = sqlite3.Row
    arms_order = [label for label, _level in ARM_LEVELS] + ['off']
    output = []
    for encounter in args.encounters:
        chosen = pick_candidate(db, encounter)
        if chosen is None:
            print('encounter %d: no resident candidate carries %s - skipped' % (encounter, FAMILY))
            continue
        mean, count, candidate, scenario, label = chosen
        print()
        print('=== encounter %-2d  candidate %s  %s  (measured mean %.3f over %d)' % (
            encounter, candidate[:12], label[:44], mean, count))
        variants = arms(scenario)
        means = collections.defaultdict(lambda: collections.defaultdict(list))
        paired = collections.defaultdict(lambda: collections.defaultdict(list))
        for index in range(args.pairs):
            seeds = strategy_optimizer.seed_pair('validation', args.seed_base + index)
            per_arm = {}
            for arm, variant in variants.items():
                metrics = replay(variant, seeds)
                per_arm[arm] = metrics
                output.append(dict(seed=seeds, encounter=encounter, candidate=candidate, arm=arm,
                                   **metrics))
                for key, value in metrics.items():
                    if isinstance(value, (int, float)):
                        means[arm][key].append(value)
            for arm, metrics in per_arm.items():
                for key, value in metrics.items():
                    reference = per_arm['high(36%)'].get(key)
                    if isinstance(value, (int, float)) and isinstance(reference, (int, float)):
                        paired[arm][key].append(value - reference)
        keys = ('counterChecks', 'counterEnqueues', 'bossCounterEnqueues', 'followerCounterEnqueues',
                'counterMp', 'totalQueueAtDeath', 'bossQueueAtDeath', 'bossHitMassAtDeath',
                'bossCounterQueuedAtDeath', 'bossHitsAfterDeath', 'reEntries', 'leavings', 'chests',
                'noTargetTicks', 'usingSkillTicks', 'ticks')
        print('    %-26s %s' % ('metric', '  '.join('%14s' % arm for arm in arms_order)))
        for key in keys:
            cells = []
            for arm in arms_order:
                values = means[arm].get(key) or []
                cells.append('%14s' % ('%.2f' % statistics.fmean(values) if values else 'n/a'))
            print('    %-26s %s' % (key, '  '.join(cells)))
        for key in ('chests', 'bossHitMassAtDeath', 'bossQueueAtDeath'):
            print('    %-26s %s' % ('paired delta ' + key,
                                    '  '.join('%14s' % ('%+.2f' % statistics.fmean(paired[arm][key])
                                                        if paired[arm].get(key) else 'n/a')
                                              for arm in arms_order)))
    if args.json:
        Path(args.json).write_text(json.dumps(output, indent=1, default=str), encoding='utf-8')
        print('wrote', args.json)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
