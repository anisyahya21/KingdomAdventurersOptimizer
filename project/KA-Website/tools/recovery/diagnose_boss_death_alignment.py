"""DIAGNOSTIC ONLY - not part of the optimiser and not imported by it.

Replays exact historical library runs and measures the stored-command queue relative to boss death:
queue depth at death, boss-targeting mass at death, pre/post-death releases, boss-directed landed-hit
spacing after death, the target-denial span either side of death, and the boss re-entry/leaving and
prize movement. Answers whether high reward tracks total stored work or *boss-directed* stored work
aligned with the death.

    python diagnose_boss_death_alignment.py --candidate a3d527c888ca7d --auto
    python diagnose_boss_death_alignment.py --run a3d527c888ca7d discovery 174

Read-only: opens the library read-only, writes nothing, and the engine is unmodified.
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
from strategy_optimizer_adapter import validate_scenario           # noqa: E402

DEFAULT_LIBRARY = 'A:/KingdomAdventurersOptimizer/strategiesv.student1only.sqlite'
ATTACKABLE = (1, 3, 4, 6)


def replay(scenario, seeds):
    """Exact replay with a per-tick sampler: own state/queue/targets and boss state/HP."""
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
            meta['boss'] = next(name for identity, spec in engine.specs.items()
                                if not spec['human'] and spec.get('boss')
                                for name in [identity_of[identity]])
            meta['enemy_grid'] = {spec['name']: spec['grid'] for spec in engine.specs.values()
                                  if not spec['human']}
            meta['own_grid'] = {spec['name']: spec['grid'] for spec in engine.specs.values()
                                if spec['human']}
        row = {}
        for identity, components in engine.units.items():
            board = components['board']
            row[meta['identity_of'][identity]] = (
                board[5], board.get(8, 0), engine.value(identity, 10),
                tuple(command['target'] for command in components.get('commands') or ()),
                engine.value(identity, 11))
        sample[engine.tick] = row

    scenario = dict(validate_scenario(scenario), mathSeed=int(seeds[0]), libSeed=int(seeds[1]))
    csc.update_fighters = wrapped
    try:
        report = combat_sandbox.run_scenario(scenario, include_trace=True)
    finally:
        csc.update_fighters = original
    return report, sample, meta


def analyse(report, sample, meta):
    identity_of = meta['identity_of']
    boss, boss_identity = meta['boss'], None
    for identity, name in identity_of.items():
        if name == boss:
            boss_identity = identity
    own = sorted(meta['own_grid'])
    enemies = sorted(meta['enemy_grid'])
    trace = report['trace']
    progress = report['result'].get('progressMetrics') or {}
    entitlement = report['result'].get('rewardEntitlement') or {}
    death = progress.get('bossDeathTick')
    if death is None or death < 0:
        death = None

    releases = [(event['tick'], identity_of.get(event.get('caster')), event.get('target'))
                for event in trace if event.get('kind') == 'release']
    hits = [(event['tick'], identity_of.get(event.get('target')))
            for event in trace if event.get('kind') == 'attack' and event.get('hit')]
    enqueues = [(event['tick'], identity_of.get(event.get('caster')), event.get('target'),
                 event.get('source'))
                for event in trace if event.get('kind') == 'enqueue']
    boss_states = [(event['tick'], event.get('old'), event.get('new'))
                   for event in trace if event.get('kind') == 'state'
                   and identity_of.get(event.get('target')) == boss]
    prizes = [(event['tick'],) for event in trace if event.get('kind') == 'prize']

    ticks = sorted(sample)
    peak_queue, peak_tick = 0, None
    no_target_ticks = []
    queue_series = {}
    for tick in ticks:
        row = sample[tick]
        total = sum(len(row.get(name, (8, 0, 0, (), 0))[3]) for name in own)
        queue_series[tick] = total
        if total > peak_queue:
            peak_queue, peak_tick = total, tick
        targetable = [name for name in own
                      if row.get(name, (8, 0, 0, (), 0))[2] > 0
                      and row.get(name, (8, 0, 0, (), 0))[0] in ATTACKABLE
                      and 0 <= meta['own_grid'].get(name, 9) <= 4]
        if not targetable:
            no_target_ticks.append(tick)

    def queue_at(tick):
        row = sample.get(tick) or sample.get(min(ticks, key=lambda t: abs(t - tick)))
        return sum(len(row.get(name, (8, 0, 0, (), 0))[3]) for name in own)

    def boss_targeting_at(tick):
        row = sample.get(tick) or sample.get(min(ticks, key=lambda t: abs(t - tick)))
        return sum(1 for name in own for target in row.get(name, (8, 0, 0, (), 0))[3]
                   if target == boss_identity)

    def spans(tickset):
        """(longest, total) of consecutive runs inside a tick set."""
        longest = total = 0
        previous = None
        for tick in tickset:
            streak = tick - previous if previous is not None else 1
            streak = streak if streak <= 1 else 1
            total += 1
            previous = tick
        return total

    def runs(tickset):
        ordered = sorted(tickset)
        longest = count = 0
        previous = None
        for tick in ordered:
            if previous is not None and tick == previous + 1:
                count += 1
            else:
                count = 1
            longest = max(longest, count)
            previous = tick
        return longest, len(ordered)

    before = [tick for tick in no_target_ticks if death is not None and tick < death]
    after = [tick for tick in no_target_ticks if death is not None and tick >= death]
    longest_before, total_before = runs(before)
    longest_after, total_after = runs(after)

    boss_hits_after = [tick for tick, target in hits
                       if target == boss and death is not None and tick >= death]
    spacing = [b - a for a, b in zip(boss_hits_after, boss_hits_after[1:])]
    boss_releases_after = [tick for tick, _caster, target in releases
                           if target == boss_identity and death is not None and tick >= death]
    re_entries = [tick for tick, old, new in boss_states
                  if death is not None and tick > death and new == 6 and old != 6]
    leavings = [tick for tick, old, new in boss_states
                if death is not None and tick > death and new == 8 and old != 8]

    return dict(
        ticks=len(ticks), death=death, certificate=(entitlement.get('certificate') or {}).get('frame'),
        awarded=entitlement.get('awardedChestCount'), pending=entitlement.get('pendingChestCount'),
        peakQueue=peak_queue, peakTick=peak_tick,
        queueAtDeath=(queue_at(death) if death is not None else None),
        bossTargetedAtDeath=(boss_targeting_at(death) if death is not None else None),
        preDeathReleases=(sum(1 for tick, _c, _t in releases if death is None or tick < death)
                          if death is not None else None),
        postDeathReleases=(sum(1 for tick, _c, _t in releases if death is not None and tick >= death)
                           if death is not None else None),
        postDeathBossReleases=len(boss_releases_after) if death is not None else None,
        postDeathBossHits=len(boss_hits_after) if death is not None else None,
        hitSpacing=spacing, longestNoTargetBefore=longest_before, longestNoTargetAfter=longest_after,
        noTargetTotal=total_before + total_after,
        spansDeath=bool(before and after), reEntries=len(re_entries), leavings=len(leavings),
        postDeathPrizes=progress.get('postDeathPrizes'),
        storedAtDeath=progress.get('storedCommandsAtDeath'),
        storedBossAtDeath=progress.get('storedCommandsTargetingBossAtDeath'),
        maxStored=progress.get('maxSimultaneousStoredCommands'),
        progressReleasesAfterDeath=progress.get('commandsReleasedAfterDeath'),
        bossDeathTick=progress.get('bossDeathTick'), bossLeavingTick=progress.get('bossLeavingTick'),
        prizesAtDeath=progress.get('prizesAtDeath') if isinstance(progress, dict) else None,
    )


def report_row(label, metrics):
    spacing = metrics['hitSpacing']
    detail = ('min %s / median %s / max %s' % (min(spacing), int(statistics.median(spacing)),
                                               max(spacing))) if spacing else 'none'
    retention = (metrics['queueAtDeath'] / metrics['peakQueue']) if metrics['peakQueue'] else None
    print('%-34s chests=%-5s ticks=%-6s | peakQ=%-4s Q@death=%-4s ret=%-5s bossQ@death=%-4s | '
          'pre/post rel %-5s/%-5s (boss post %-4s) | boss post-hits %-4s spacing[%s] | '
          'no-target %-6s (before %-5s after %-5s, spans %s) | reentry %-3s leaving %-3s prizes %s' % (
              label, metrics['awarded'], metrics['ticks'], metrics['peakQueue'],
              metrics['queueAtDeath'], ('%.2f' % retention) if retention is not None else 'n/a',
              metrics['bossTargetedAtDeath'], metrics['preDeathReleases'],
              metrics['postDeathReleases'], metrics['postDeathBossReleases'],
              metrics['postDeathBossHits'], detail, metrics['noTargetTotal'],
              metrics['longestNoTargetBefore'], metrics['longestNoTargetAfter'],
              metrics['spansDeath'], metrics['reEntries'], metrics['leavings'],
              metrics['postDeathPrizes']))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', default=DEFAULT_LIBRARY)
    parser.add_argument('--candidate')
    parser.add_argument('--auto', action='store_true',
                        help='replay the highest-earned runs of this candidate plus controls')
    parser.add_argument('--json')
    args = parser.parse_args(argv)

    db = sqlite3.connect('file:%s?mode=ro' % args.library, uri=True)
    db.row_factory = sqlite3.Row
    row = db.execute('SELECT id, scenario, label FROM candidate WHERE id LIKE ?',
                     (args.candidate + '%',)).fetchone()
    encounter = db.execute('SELECT encounter FROM candidate_meta WHERE id=?', (row['id'],)).fetchone()
    print('candidate %s  %s  (encounter %s)' % (row['id'][:14], row['label'][:56], encounter[0]))
    scenario = json.loads(row['scenario'])
    history = db.execute(
        'SELECT phase, ordinal, seeds, awardedChests FROM evidence WHERE candidate=? '
        'AND awardedChests IS NOT NULL ORDER BY awardedChests DESC LIMIT 400', (row['id'],)).fetchall()
    with_seeds = [dict(entry) for entry in history if entry['seeds']]
    chosen = []
    if args.auto:
        chosen.append(('highest', with_seeds[0]))
        for entry in with_seeds:
            if entry['awardedChests'] == 1:
                chosen.append(('counterexample(1)', entry))
                break
        for entry in with_seeds:
            if entry not in [item[1] for item in chosen]:
                chosen.append(('control', entry))
            if len(chosen) >= 4:
                break
        chosen.append(('lowest', with_seeds[-1]))
    outputs = []
    for label, entry in chosen:
        report, sample, meta = replay(scenario, json.loads(entry['seeds']))
        metrics = analyse(report, sample, meta)
        metrics.update(phase=entry['phase'], ordinal=entry['ordinal'], seeds=json.loads(entry['seeds']))
        report_row('%s %s#%s' % (label, entry['phase'], entry['ordinal']), metrics)
        outputs.append(dict(label=label, **metrics))
    if args.json:
        Path(args.json).write_text(json.dumps(outputs, indent=1), encoding='utf-8')
        print('wrote', args.json)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
