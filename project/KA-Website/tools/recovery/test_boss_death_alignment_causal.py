"""DIAGNOSTIC ONLY - causal A/B for the boss-death alignment mechanism. Read-only.

Intervention: **purge at death**. On the tick the boss's HP first reaches zero, drop a controlled
fraction of the commands that are *already sitting in the player's queue*, either the boss-directed
ones (treatment) or an equal number of the others (placebo). The dropped commands never fire: they
are simply consumed before the post-death window, which is the literal form of "consume the existing
boss-directed queue before boss death".

Why this and not a clock-haste: a haste cascades. An earlier version advanced every boss-directed
command's clock from tick 6000 and the whole fight changed -- the queue grew, the boss never died and
the run censored. Purging at the death tick leaves everything up to and including the death
**bit-identical** to the baseline, including the shared RNG stream and the death tick itself, and
changes only the mediator: the queued mass the post-death window has to work with.

The placebo drops the same *number* of non-boss commands, so total queue size falls identically while
the boss-directed mass is preserved. Comparing treatment with placebo separates

    total queue            (both fall by the same amount)
    from
    useful boss-directed queue   (only the treatment falls)

Honest limitation, recorded rather than hidden: a dropped command would also have dealt its damage
earlier, so a purge is a *reduction in work*, not a pure re-timing. That is why the placebo matters:
it removes the same amount of queued work from the other bucket.

    python test_boss_death_alignment_causal.py --phase exact      # baseline + levels + placebo
    python test_boss_death_alignment_causal.py --phase paired --pairs 16
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
from strategy_optimizer_adapter import validate_scenario           # noqa: E402

DEFAULT_LIBRARY = 'A:/KingdomAdventurersOptimizer/strategiesv.student1only.sqlite'
ATTACKABLE = (1, 3, 4, 6)
CANDIDATE = 'a3d527c888ca7d'
SEEDS = [1149347225, 1159301657]


def load(db, candidate=CANDIDATE):
    row = db.execute('SELECT id, scenario FROM candidate WHERE id LIKE ?',
                     (candidate + '%',)).fetchone()
    return row['id'], json.loads(row['scenario'])


class Intervention:
    """Purge `share` of one bucket of queued commands on the tick the boss dies."""

    def __init__(self, mode='none', share=0.5):
        self.mode, self.share = mode, share
        self.boss = None
        self.dropped = 0
        self.droppedBoss = 0
        self.triggerTick = None
        self.available = None

    def install(self):
        self.original = csc.update_fighters
        self.sample = {}
        self.meta = {}
        original, outer = self.original, self

        def wrapped(teams, battle_state, update_state, execute_commands, after_fighter=None):
            engine = update_state.__self__
            if outer.boss is None:
                identity_of = {value: key for key, value in engine.names.items()}
                outer.meta['identity_of'] = identity_of
                outer.boss = next(identity for identity, spec in engine.specs.items()
                                  if not spec['human'] and spec.get('boss'))
                outer.meta['boss'] = identity_of[outer.boss]
                outer.meta['own_grid'] = {spec['name']: spec['grid']
                                          for spec in engine.specs.values() if spec['human']}
            if after_fighter is None:
                original(teams, battle_state, update_state, execute_commands)
            else:
                original(teams, battle_state, update_state, execute_commands, after_fighter)
            if outer.mode != 'none' and outer.triggerTick is None:
                boss_hp = engine.value(outer.boss, 10)
                if boss_hp <= 0:
                    outer.triggerTick = engine.tick
                    own = [identity for identity, spec in engine.specs.items() if spec['human']]
                    boss_commands = [(identity, command) for identity in own
                                     for command in engine.units[identity].get('commands') or ()
                                     if command.get('target') == outer.boss]
                    other_commands = [(identity, command) for identity in own
                                      for command in engine.units[identity].get('commands') or ()
                                      if command.get('target') != outer.boss]
                    bucket = boss_commands if outer.mode == 'boss' else other_commands
                    outer.available = len(bucket)
                    drop = set(id(command) for _identity, command in
                               bucket[:int(round(len(bucket) * outer.share))])
                    for identity in own:
                        components = engine.units[identity]
                        if not components.get('commands'):
                            continue
                        keep = [command for command in components['commands']
                                if id(command) not in drop]
                        outer.dropped += len(components['commands']) - len(keep)
                        components['commands'][:] = keep
                    outer.droppedBoss = sum(1 for _identity, command in boss_commands
                                            if id(command) in drop)
            row = {}
            for identity, components in engine.units.items():
                row[outer.meta['identity_of'][identity]] = (
                    components['board'][5], engine.value(identity, 10),
                    tuple(command['target'] for command in components.get('commands') or ()))
            outer.sample[engine.tick] = row

        csc.update_fighters = wrapped

    def uninstall(self):
        csc.update_fighters = self.original


def measure(report, sample, meta):
    identity_of = meta['identity_of']
    own = sorted(meta['own_grid'])
    boss_identity = next(identity for identity, name in identity_of.items()
                         if name == meta['boss'])
    trace = report['trace']
    progress = report['result'].get('progressMetrics') or {}
    entitlement = report['result'].get('rewardEntitlement') or {}
    death = progress.get('bossDeathTick')
    death = None if (death is None or death < 0) else death
    ticks = sorted(sample)

    queue = {tick: sum(len(sample[tick].get(name, (8, 0, ()))[2]) for name in own) for tick in ticks}
    bossq = {tick: sum(1 for name in own for target in sample[tick].get(name, (8, 0, ()))[2]
                       if target == boss_identity) for tick in ticks}
    peak_queue = max(queue.values()) if queue else 0
    peak_bossq = max(bossq.values()) if bossq else 0

    no_target = []
    using_skill = 0
    for tick in ticks:
        row = sample[tick]
        if any(row.get(name, (8, 0, ()))[0] == 5 for name in own):
            using_skill += 1
        if not [name for name in own if row.get(name, (8, 0, ()))[1] > 0
                and row.get(name, (8, 0, ()))[0] in ATTACKABLE
                and 0 <= meta['own_grid'].get(name, 9) <= 4]:
            no_target.append(tick)

    hits_after = [event['tick'] for event in trace
                  if event.get('kind') == 'attack' and event.get('hit')
                  and identity_of.get(event.get('target')) == meta['boss']
                  and death is not None and event['tick'] >= death]
    releases_after = [event['tick'] for event in trace
                      if event.get('kind') == 'release' and event.get('target') == boss_identity
                      and death is not None and event['tick'] >= death]
    spacing = [b - a for a, b in zip(hits_after, hits_after[1:])]
    return dict(
        chests=entitlement.get('awardedChestCount'), ticks=len(ticks), death=death,
        certificate=(entitlement.get('certificate') or {}).get('frame'),
        peakQueue=peak_queue, peakBossQueue=peak_bossq,
        queueAtDeath=queue.get(death), bossQueueAtDeath=bossq.get(death),
        retention=((bossq.get(death) / peak_bossq) if (death in bossq and peak_bossq) else None),
        bossReleasesAfterDeath=len(releases_after), bossHitsAfterDeath=len(hits_after),
        spacing=spacing, usingSkillTicks=using_skill, noTargetTicks=len(no_target),
        reEntries=progress.get('postDeathBossReentries'), leavings=progress.get('postDeathBossLeavings'),
        postDeathPrizes=progress.get('postDeathPrizes'),
        storedAtDeath=progress.get('storedCommandsAtDeath'),
        storedBossAtDeath=progress.get('storedCommandsTargetingBossAtDeath'))


def run_one(scenario, seeds, mode='none', share=0.5):
    intervention = Intervention(mode, share)
    intervention.install()
    try:
        report = combat_sandbox.run_scenario(
            dict(validate_scenario(scenario), mathSeed=int(seeds[0]), libSeed=int(seeds[1])),
            include_trace=True)
    finally:
        intervention.uninstall()
    metrics = measure(report, intervention.sample, intervention.meta)
    metrics['purged'] = intervention.dropped
    metrics['purgedBoss'] = intervention.droppedBoss
    metrics['purgeTick'] = intervention.triggerTick
    metrics['availableAtPurge'] = intervention.available
    return metrics


def summarise(label, metrics):
    spacing = metrics['spacing']
    detail = ('min %s / med %s / max %s' % (min(spacing), int(statistics.median(spacing)),
                                            max(spacing))) if spacing else 'none'
    print('%-26s chests=%-4s death=%-6s cert=%-6s | peakQ=%-4s peakBossQ=%-4s Q@death=%-4s '
          'bossQ@death=%-4s ret=%-5s | boss post rel=%-4s hits=%-4s [%s] | reentry=%-4s leaving=%-4s '
          'prizes=%-4s | usingSkill=%-6s noTarget=%-6s (purged %s of %s at t=%s, boss-bucket %s)' % (
              label, metrics['chests'], metrics['death'], metrics['certificate'],
              metrics['peakQueue'], metrics['peakBossQueue'], metrics['queueAtDeath'],
              metrics['bossQueueAtDeath'],
              ('%.3f' % metrics['retention']) if metrics['retention'] is not None else 'n/a',
              metrics['bossReleasesAfterDeath'], metrics['bossHitsAfterDeath'], detail,
              metrics['reEntries'], metrics['leavings'], metrics['postDeathPrizes'],
              metrics['usingSkillTicks'], metrics['noTargetTicks'], metrics['purged'],
              metrics['availableAtPurge'], metrics['purgeTick'], metrics['purgedBoss']))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', default=DEFAULT_LIBRARY)
    parser.add_argument('--phase', choices=('exact', 'paired'), default='exact')
    parser.add_argument('--pairs', type=int, default=16)
    parser.add_argument('--seed-base', type=int, default=1500000)
    parser.add_argument('--json')
    args = parser.parse_args(argv)

    db = sqlite3.connect('file:%s?mode=ro' % args.library, uri=True)
    db.row_factory = sqlite3.Row
    candidate, scenario = load(db)
    print('candidate %s (encounter %s)' % (candidate[:14],
          db.execute('SELECT encounter FROM candidate_meta WHERE id=?', (candidate,)).fetchone()[0]))
    results = []

    if args.phase == 'exact':
        print('--- exact replay, seeds %s' % SEEDS)
        variants = [
            ('baseline', 'none', 0.0),
            ('purge 25% of boss queue', 'boss', 0.25),
            ('purge 50% of boss queue', 'boss', 0.50),
            ('purge 75% of boss queue', 'boss', 0.75),
            ('purge 100% of boss queue', 'boss', 1.00),
            ('PLACEBO purge 50% of other queue', 'other', 0.50),
            ('PLACEBO purge 100% of other queue', 'other', 1.00),
        ]
        for label, mode, share in variants:
            metrics = run_one(scenario, SEEDS, mode, share)
            summarise(label, metrics)
            results.append(dict(label=label, mode=mode, share=share, **metrics))
    else:
        print('--- %d paired seeds (baseline vs purge 50%% of the boss-directed queue at death), '
              'same build, identical seed pairs' % args.pairs)
        deltas = collections.defaultdict(list)
        for index in range(args.pairs):
            seeds = strategy_optimizer.seed_pair('validation', args.seed_base + index)
            base = run_one(scenario, seeds)
            treat = run_one(scenario, seeds, 'boss', 0.5)
            row = dict(seed=seeds, base=base, treat=treat)
            results.append(row)
            for key in ('chests', 'bossQueueAtDeath', 'bossHitsAfterDeath', 'reEntries', 'leavings',
                        'queueAtDeath', 'noTargetTicks'):
                deltas[key].append((treat.get(key) or 0) - (base.get(key) or 0))
            print('  seed %-2d baseline chests=%-4s bossQ@d=%-4s postHits=%-4s leaves=%-4s | '
                  'haste chests=%-4s bossQ@d=%-4s postHits=%-4s leaves=%-4s' % (
                      index, base['chests'], base['bossQueueAtDeath'], base['bossHitsAfterDeath'],
                      base['leavings'], treat['chests'], treat['bossQueueAtDeath'],
                      treat['bossHitsAfterDeath'], treat['leavings']))
        print()
        for key, values in deltas.items():
            mean = statistics.fmean(values)
            negative = sum(1 for value in values if value < 0)
            positive = sum(1 for value in values if value > 0)
            print('  paired delta %-20s mean %+8.2f   (treat<base %d, >base %d, equal %d)' % (
                key, mean, negative, positive, len(values) - negative - positive))

    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=1, default=str), encoding='utf-8')
        print('wrote', args.json)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
