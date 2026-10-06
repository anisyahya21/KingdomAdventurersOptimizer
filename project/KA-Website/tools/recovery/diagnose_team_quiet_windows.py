"""DIAGNOSTIC ONLY - not part of the optimiser and not imported by it.

Replays exact historical library runs (candidate + stored seed pair) and measures whether the ENEMY
TEAM as a whole stopped producing offense, and how that lines up with the player's stored-command
unload.

The library's `evidence` table stores the exact `[mathSeed, libSeed]` pair beside each result, and
the candidate row stores the scenario, so a run replays deterministically:

    python diagnose_team_quiet_windows.py --candidate a3d527c888ca7d --phase discovery --ordinal 174
    python diagnose_team_quiet_windows.py --candidate f7e6911be2a49c --auto 5      # outlier + controls

Team definitions used here:

  living          hp > 0 and state not in (7, 8)
  frontline       living and grid 0..4 (the only grids `is_most_front` accepts)
  capable         living and (frontline OR has already produced at least one offensive event)
  offensive event that enemy emitted an `enqueue` or an `attack` on this tick
  quiet window    a maximal stretch with no offensive event from ALL living / ALL frontline /
                  >=80% of capable enemies, longer than `--factor` times the encounter's longest
                  enemy period

The already-diagnosed back-row stall (bare-handed range 1 + unaffordable skill + blocked advance)
is reported separately and is NOT what this script calls a team-wide pause.
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
import combat_shared_controllers as csc                            # noqa: E402
from combat_resolution import attack_interval                      # noqa: E402
from strategy_optimizer_adapter import validate_scenario           # noqa: E402

DEFAULT_LIBRARY = 'A:/KingdomAdventurersOptimizer/strategiesv.student1only.sqlite'
STATE_NAMES = {0: 'None', 1: 'Waiting', 2: 'Moving', 3: 'Charging', 4: 'Attacking',
               5: 'UsingSkill', 6: 'Damaging', 7: 'KnockDown', 8: 'Leaving'}


def replay(scenario, seeds, library=None):
    """One exact replay with a per-tick team sampler installed. Returns (report, sample, meta)."""
    sample = {}
    meta = {}
    original = csc.update_fighters

    def wrapped(teams, battle_state, update_state, execute_commands, after_fighter=None):
        if after_fighter is None:
            original(teams, battle_state, update_state, execute_commands)
        else:
            original(teams, battle_state, update_state, execute_commands, after_fighter)
        engine = update_state.__self__
        if not meta:
            meta['identity_of'] = {value: key for key, value in engine.names.items()}
            meta['enemy_grid'] = {}
            meta['enemy_period'] = {}
            meta['own_grid'] = {}
            for identity, spec in engine.specs.items():
                if spec['human']:
                    meta['own_grid'][spec['name']] = spec['grid']
                else:
                    meta['enemy_grid'][spec['name']] = spec['grid']
                    meta['enemy_period'][spec['name']] = attack_interval(
                        engine.value(identity, 15)) + 21
        row = {}
        for identity, components in engine.units.items():
            board = components['board']
            row[meta['identity_of'].get(identity, identity)] = (
                board[5], board.get(8, 0), board[4], engine.value(identity, 10),
                len(components.get('commands') or ()), engine.value(identity, 11))
        sample[engine.tick] = row

    math_seed, lib_seed = int(seeds[0]), int(seeds[1])
    scenario = dict(validate_scenario(scenario), mathSeed=math_seed, libSeed=lib_seed)
    csc.update_fighters = wrapped
    try:
        report = combat_sandbox.run_scenario(scenario, include_trace=True)
    finally:
        csc.update_fighters = original
    return report, sample, meta


def team_analysis(report, sample, meta, own_names, factor=2.0):
    """Per-tick team state plus the three quiet-window measurements."""
    enemy_of = meta['identity_of']          # identity -> name, the direction the trace needs
    enemies = sorted(meta['enemy_grid'])
    grid = meta['enemy_grid']
    period = meta['enemy_period']
    longest_period = max(period.values()) if period else 60

    events = collections.defaultdict(set)          # tick -> enemies with an offensive event
    hits = collections.Counter()                   # tick -> hits landed on enemies
    releases = collections.Counter()               # tick -> player command releases
    counters = []
    for event in report['trace']:
        kind = event.get('kind')
        if kind in ('enqueue', 'attack'):
            unit = enemy_of.get(event.get('caster', event.get('attacker')))
            if unit in grid:
                events[event['tick']].add(unit)
            # hits are counted by TARGET: an `attack` event's attacker is the player, its target is
            # the enemy that was struck.
            if (kind == 'attack' and event.get('hit')
                    and enemy_of.get(event.get('target')) in grid):
                hits[event['tick']] += 1
            if kind == 'enqueue' and event.get('source') == 'counter':
                counters.append((event['tick'], unit, event.get('target')))
        if kind == 'release':
            releases[event['tick']] += 1

    rows = []
    ever_acted = set()
    own_state_time = collections.defaultdict(collections.Counter)
    for tick in sorted(sample):
        row = sample[tick]
        living = [name for name in enemies if row.get(name, (8, 0, 0, 0, 0, 0))[3] > 0
                  and row.get(name, (8, 0, 0, 0, 0, 0))[0] not in (7, 8)]
        frontline = [name for name in living if 0 <= grid[name] <= 4]
        # `combat_navigation.front_opponent`: a normal-attack target must be alive, in state
        # 1/3/4/6, and itself most-front. A player chaining skills sits in state 5 -> untargetable.
        targetable = [name for name in own_names
                      if row.get(name, (8, 0, 0, 0, 0, 0))[3] > 0
                      and row.get(name, (8, 0, 0, 0, 0, 0))[0] in (1, 3, 4, 6)
                      and 0 <= meta['own_grid'].get(name, 9) <= 4]
        for name in own_names:
            own_state_time[name][STATE_NAMES.get(row.get(name, (8,))[0], '?')] += 1
        acted = events.get(tick, set())
        capable = set(frontline) | {name for name in living if name in ever_acted}
        player_queue = sum(row.get(name, (0, 0, 0, 0, 0, 0))[4] for name in own_names)
        rows.append(dict(tick=tick, living=len(living), frontline=len(frontline),
                         capable=len(capable), acted=len(acted & set(living)),
                         targetable=len(targetable),
                         damaging=sum(1 for name in living if row[name][0] == 6),
                         waiting=sum(1 for name in living if row[name][0] == 1),
                         playerQueue=player_queue, hits=hits.get(tick, 0),
                         released=releases.get(tick, 0)))
        ever_acted |= acted

    def windows(key, share=1.0):
        out = []
        start = None
        for row in rows:
            base = row['capable'] if key == 'capable' else row[key]
            silent = row['acted'] <= base * (1.0 - share)
            if base and silent:
                start = row['tick'] if start is None else start
                last = row
            else:
                if start is not None and last['tick'] - start > factor * longest_period:
                    out.append((start, last['tick'], last['tick'] - start))
                start = None
        if start is not None and rows and rows[-1]['tick'] - start > factor * longest_period:
            out.append((start, rows[-1]['tick'], rows[-1]['tick'] - start))
        return out

    return dict(rows=rows, events=events, hits=hits, counters=counters,
                enemies=enemies, grid=grid, period=period, longestPeriod=longest_period,
                ownGrid=meta['own_grid'], ownStateTime=own_state_time,
                windowsAll=windows('living'), windowsFront=windows('frontline'),
                windowsShare80=windows('capable', 0.8), windowsShare90=windows('capable', 0.9))


def summarise(label, analysis, report):
    rows = analysis['rows']
    span = max(1, len(rows))
    quiet_all = sum(1 for row in rows if row['living'] and row['acted'] == 0)
    quiet_front = sum(1 for row in rows if row['frontline'] and row['acted'] == 0)
    peak_damaging = max((row['damaging'] for row in rows), default=0)
    peak_queue = max((row['playerQueue'] for row in rows), default=0)
    no_target = sum(1 for row in rows if row['targetable'] == 0)
    enemy_actions = sum(row['acted'] for row in rows)
    entitlement = report['result'].get('rewardEntitlement') or {}
    print('%-28s ticks=%-6d earned=%-5s certFrame=%-6s | quietAll %5.1f%% quietFront %5.1f%% | '
          'longest all=%s front=%s 80%%=%s | peakDamaging=%d peakPlayerQueue=%d counters=%d' % (
              label, len(rows), entitlement.get('awardedChestCount'),
              (entitlement.get('certificate') or {}).get('frame'),
              100.0 * quiet_all / span, 100.0 * quiet_front / span,
              max((w[2] for w in analysis['windowsAll']), default=0),
              max((w[2] for w in analysis['windowsFront']), default=0),
              max((w[2] for w in analysis['windowsShare80']), default=0),
              peak_damaging, peak_queue, len(analysis['counters'])))
    dps = sorted(analysis['ownStateTime'],
                 key=lambda name: -sum(analysis['ownStateTime'][name].values()))
    focus = dps[0] if dps else None
    states = analysis['ownStateTime'].get(focus, {})
    total = sum(states.values()) or 1
    print('    own-unit targetability: ticks with NO legal enemy target %d of %d (%.0f%%) | '
          'enemy offensive events %d | %s states: %s' % (
              no_target, span, 100.0 * no_target / span, enemy_actions, focus,
              ', '.join('%s %.0f%%' % (name, 100.0 * count / total)
                        for name, count in states.most_common(4))))


def describe_windows(analysis, sample, limit=3):
    """Print the longest ALL-living-quiet windows that end in a resumption, with the state mix."""
    rows = analysis['rows']
    by_tick = {row['tick']: row for row in rows}
    ordered = [row['tick'] for row in rows]
    events = analysis['events']
    out = []
    for start, end, length in sorted(analysis['windowsAll'], key=lambda item: -item[2]):
        resumed = next((tick for tick in ordered if tick > end and events.get(tick)), None)
        if resumed is None:
            continue                      # the battle simply ended still quiet
        states = collections.Counter()
        for tick in range(start, end + 1):
            row = sample.get(tick) or {}
            for name in analysis['enemies']:
                entry = row.get(name)
                if entry and entry[3] > 0 and entry[0] not in (7, 8):
                    states[STATE_NAMES.get(entry[0], entry[0])] += 1
        total = sum(states.values()) or 1
        window = dict(start=start, end=end, length=length, resumed=resumed,
                      resumedAfter=resumed - end,
                      states='%s' % ', '.join('%s %.0f%%' % (name, 100.0 * count / total)
                                              for name, count in states.most_common(4)),
                      hits=sum(by_tick[t]['hits'] for t in range(start, end + 1) if t in by_tick),
                      releases=sum(by_tick[t]['released'] for t in range(start, end + 1) if t in by_tick),
                      queueStart=by_tick.get(start, {}).get('playerQueue'),
                      queuePeak=max((by_tick[t]['playerQueue'] for t in range(start, end + 1)
                                     if t in by_tick), default=0),
                      living=by_tick.get(start, {}).get('living'))
        out.append(window)
        if len(out) >= limit:
            break
    return out


def load_candidate(db, prefix):
    row = db.execute('SELECT id, scenario, label FROM candidate WHERE id LIKE ?', (prefix + '%',)).fetchone()
    return row


def runs_for(db, candidate, limit=400):
    return [dict(row) for row in db.execute(
        'SELECT phase, ordinal, seeds, awardedChests, pendingChests, prizeCallbacks FROM evidence '
        'WHERE candidate=? AND awardedChests IS NOT NULL ORDER BY ordinal DESC LIMIT ?',
        (candidate, limit))]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', default=DEFAULT_LIBRARY)
    parser.add_argument('--candidate')
    parser.add_argument('--phase')
    parser.add_argument('--ordinal', type=int)
    parser.add_argument('--auto', type=int, default=0,
                        help='with --candidate: replay the N highest-earned runs plus controls')
    parser.add_argument('--factor', type=float, default=2.0)
    args = parser.parse_args(argv)

    db = sqlite3.connect('file:%s?mode=ro' % args.library, uri=True)
    db.row_factory = sqlite3.Row
    row = load_candidate(db, args.candidate)
    if row is None:
        print('candidate %s is not resident' % args.candidate)
        return 1
    scenario = json.loads(row['scenario'])
    encounter = db.execute('SELECT encounter FROM candidate_meta WHERE id=?', (row['id'],)).fetchone()
    print('candidate %s  encounter %s  %s' % (
        row['id'][:14], encounter[0] if encounter else '?', row['label'][:70]))

    selected = []
    if args.auto:
        history = runs_for(db, row['id'])
        with_seeds = [entry for entry in history if entry['seeds']]
        with_seeds.sort(key=lambda entry: -(entry['awardedChests'] or 0))
        if with_seeds:
            selected.append(('outlier', with_seeds[0]))
            middle = with_seeds[len(with_seeds) // 2]
            selected.append(('median', middle))
            selected.append(('lowest', with_seeds[-1]))
        lowest_stored = sorted(with_seeds, key=lambda entry: (entry['prizeCallbacks'] or 0))[:1]
        for entry in lowest_stored:
            if entry not in [item[1] for item in selected]:
                selected.append(('lowPrize', entry))
    else:
        entry = next((item for item in runs_for(db, row['id'])
                      if item['phase'] == args.phase and item['ordinal'] == args.ordinal), None)
        if entry is None:
            print('no stored evidence row for that phase/ordinal in the window')
            return 1
        selected.append(('requested', entry))

    for label, entry in selected:
        seeds = json.loads(entry['seeds'])
        report, sample, meta = replay(scenario, seeds, args.library)
        own_names = [name for name in sample[max(sample)] if name not in meta['enemy_grid']]
        analysis = team_analysis(report, sample, meta, own_names, args.factor)
        summarise('%s %s#%s stored=%s' % (label, entry['phase'], entry['ordinal'],
                                          entry['awardedChests']), analysis, report)
        for window in describe_windows(analysis, sample):
            print('    quiet t=%-6d..%-6d  len=%-6d enemies at start=%-2s states[%s] '
                  'playerQueue %s->peak %s  hits=%-4d releases=%-4d  resumed after %d ticks' % (
                      window['start'], window['end'], window['length'], window['living'],
                      window['states'], window['queueStart'], window['queuePeak'],
                      window['hits'], window['releases'], window['resumedAfter']))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
