"""DIAGNOSTIC ONLY - not part of the optimiser and not imported by it.

Answers: does a living enemy in our authoritative simulator ever stop producing offensive actions
for substantially longer than its own action period, and if so, which state is it sitting in?

Method. The engine's own trace already carries the state machine (`state`, `attack`, `enqueue`,
`release`). One extra read-only hook samples, after each fighters phase, the exact per-unit state,
gauge, state-frame, HP and command-queue depth - the same seam the native kernel's own observer
uses. Nothing is written anywhere; no library is opened.

    python diagnose_enemy_quiet_windows.py --encounter 4 --seeds 40
    python diagnose_enemy_quiet_windows.py --encounter 4 --seeds 40 --pinned          # hostile test

Definitions used for a "quiet window" (per enemy, per run):

  eligible   hp > 0, state not in (7, 8), and at least one opposing unit alive and not leaving
  offensive  the enemy emitted an `enqueue` (it decided to attack) or an `attack` (it struck)
  quiet      a gap between consecutive offensive ticks longer than K x the enemy's own
             uninterrupted period (`attack_interval(AGI) + 21`)

Legitimate sleep, knockdown, leaving, death and no-target stretches are excluded from `eligible`
rather than being counted as mysterious.
"""
from __future__ import annotations

import argparse
import collections
import copy
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import combat_sandbox                                             # noqa: E402
import combat_shared_controllers as csc                           # noqa: E402
from combat_resolution import attack_interval                     # noqa: E402
from combat_runtime_data import data_path                         # noqa: E402

STATE_NAMES = {0: 'None', 1: 'Waiting', 2: 'Moving', 3: 'Charging', 4: 'Attacking',
               5: 'UsingSkill', 6: 'Damaging', 7: 'KnockDown', 8: 'Leaving'}


def base_scenario():
    """The synthetic scenario the sandbox checks use; its enemy side comes from the encounter."""
    return json.loads(data_path('sandbox-synthetic-scenario.json').read_text(encoding='utf-8'))


def build(encounter, pinned):
    """One scenario. `pinned` makes the player fast and multi-hit and the enemy unkillable-ish, to
    push the suspected mechanism as hard as the recovered mechanics allow."""
    scenario = base_scenario()
    scenario['encounterId'] = int(encounter)
    scenario['tickLimit'] = 4000
    for unit in scenario['ownUnits']:
        unit['parameters']['13']['rawValue'] = 1              # ATK 1: nothing dies quickly
        if pinned:
            unit['parameters']['15']['rawValue'] = 900        # interval 8 for every own unit
            unit['skills'] = [110, 25, 24, 23, 22]            # multi-hit only, no counter
            unit['invocationLevels'] = [2, 2, 2, 2, 2]        # high invocation level
        else:
            unit['parameters']['15']['rawValue'] = 79
    return scenario


def run(scenario, seed_pair, max_ticks):
    """Run one battle with the trace on and a read-only per-tick sampler installed."""
    sample = {}
    identity_of = {}
    original = csc.update_fighters

    def wrapped(teams, battle_state, update_state, execute_commands, after_fighter=None):
        if after_fighter is None:
            original(teams, battle_state, update_state, execute_commands)
        else:
            original(teams, battle_state, update_state, execute_commands, after_fighter)
        engine = update_state.__self__
        names = {value: key for key, value in engine.names.items()}
        identity_of.update(names)          # identity -> name, the direction the trace needs
        row = {}
        for identity, components in engine.units.items():
            board = components['board']
            row[names.get(identity, identity)] = (
                board[5], board.get(8, 0), board[4], engine.value(identity, 10),
                len(components.get('commands') or ()),
                board.get(62))
        sample[engine.tick] = row

    scenario = dict(scenario, mathSeed=int(seed_pair[0]), libSeed=int(seed_pair[1]))
    csc.update_fighters = wrapped
    try:
        report = combat_sandbox.run_scenario(scenario, include_trace=True, stop_tick=max_ticks)
    finally:
        csc.update_fighters = original
    return report, sample, identity_of


def analyse(report, sample, identity_of, enemies, own, intervals, thresholds):
    """Per enemy: action rate against its own period, state time, and the quiet windows."""
    trace = report['trace']
    offensive = collections.defaultdict(list)
    for event in trace:
        if event.get('kind') == 'enqueue':
            unit = identity_of.get(event.get('caster'))
        elif event.get('kind') == 'attack':
            unit = identity_of.get(event.get('attacker'))
        else:
            continue
        if unit in enemies or str(unit).startswith('enemy:'):
            offensive[unit].append(event['tick'])
    for ticks in offensive.values():
        ticks.sort()
    offensive_sets = {unit: set(ticks) for unit, ticks in offensive.items()}

    ticks = sorted(sample)
    windows = []
    summary = {}
    for enemy in enemies:
        period = intervals.get(enemy, 1) + 21
        state_time = collections.Counter()
        eligible_ticks = 0
        actions = 0
        points = []                      # every tick from which a gap is measured
        eligible_run = None
        for tick in ticks:
            row = sample[tick]
            state, gauge, frame, hp, queued, status = row.get(enemy, (8, 0, 0, 0, 0, None))
            alive = hp > 0 and state not in (7, 8)
            target_exists = any(row.get(name, (8, 0, 0, 0, 0, None))[3] > 0
                                and row.get(name, (8, 0, 0, 0, 0, None))[0] not in (7, 8)
                                for name in own)
            eligible = alive and target_exists
            acted = tick in offensive_sets.get(enemy, ())
            if not eligible:
                if eligible_run is not None and points:
                    found = _window(enemy, tick - 1, eligible_run, points, period, thresholds)
                    if found is not None:
                        windows.append(found)
                eligible_run = None
                points = []
                continue
            if eligible_run is None:
                eligible_run = tick
                points = [tick]
            state_time[STATE_NAMES.get(state, str(state))] += 1
            eligible_ticks += 1
            if acted:
                actions += 1
                points.append(tick)
        if eligible_run is not None and points:
            found = _window(enemy, ticks[-1], eligible_run, points, period, thresholds)
            if found is not None:
                windows.append(found)
        summary[enemy] = dict(period=period, eligibleTicks=eligible_ticks, actions=actions,
                              expected=eligible_ticks / period if period else None,
                              stateTime=state_time.most_common())

    # state composition inside each window, and the player's queue depth during it
    for window in windows:
        composition = collections.Counter()
        peak_queue = 0
        peak_player = 0
        for tick in range(window['start'], window['end'] + 1):
            row = sample.get(tick)
            if row is None:
                continue
            state = row.get(window['enemy'], (None,))[0]
            composition[STATE_NAMES.get(state, str(state))] += 1
            total = sum(entry[4] for entry in row.values())
            player = sum(row.get(name, (0, 0, 0, 0, 0, 0))[4] for name in own)
            peak_queue = max(peak_queue, total)
            peak_player = max(peak_player, player)
        window['states'] = composition.most_common()
        window['peakTotalQueue'] = peak_queue
        window['peakPlayerQueue'] = peak_player
    return windows, summary


def _window(enemy, end, run_start, points, period, thresholds):
    """The single largest gap between consecutive action points inside one eligible stretch."""
    worst = None
    for before, after in zip(points, points[1:] + [end + 1]):
        gap = after - before
        if gap > thresholds['factor_min'] * period and (worst is None or gap > worst['gap']):
            worst = dict(enemy=enemy, start=before, end=after, gap=gap, period=period,
                         runStart=run_start, states=[], peakTotalQueue=0, peakPlayerQueue=0)
    return worst


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--encounter', type=int, default=4)
    parser.add_argument('--seeds', type=int, default=40, help='number of seed pairs')
    parser.add_argument('--ticks', type=int, default=4000, help='per-battle tick horizon')
    parser.add_argument('--budget-seconds', type=float, default=180.0)
    parser.add_argument('--pinned', action='store_true',
                        help='hostile configuration: fast multi-hit players, ATK 1')
    parser.add_argument('--top', type=int, default=5, help='how many anomalous runs to detail')
    parser.add_argument('--json', help='write the aggregate result as JSON here')
    args = parser.parse_args(argv)

    scenario = build(args.encounter, args.pinned)
    report, sample, identity_of = run(scenario, (1, 1), 60)          # probe run: identity names and periods
    names = report['result']['names'] if isinstance(report['result'].get('names'), list) else []
    sample_row = sample[max(sample)]
    enemy_names = [name for name in sample_row if str(name).startswith('enemy:')]
    own_names = [name for name in sample_row if not str(name).startswith('enemy:')]
    intervals = {}
    for name in enemy_names:
        incoming = int(str(name).split(':')[1])
        fighters = [f for f in report['setup']['encounter']['fighters']
                    if f['incomingIndex'] == incoming]
        if fighters:
            agility = int(fighters[0]['parameters']['15']['rawValue'])
            intervals[name] = attack_interval(agility)
    if not enemy_names:
        # the probe horizon may not have built the roster; fall back to the setup directly
        for fighter in report['setup']['encounter']['fighters']:
            enemy_names.append('enemy:%d:%s' % (fighter['incomingIndex'], fighter['monsterId']))
            intervals[enemy_names[-1]] = attack_interval(
                int(fighter['parameters']['15']['rawValue']))

    thresholds = dict(factor_min=2.0)
    started = time.monotonic()
    all_windows = []
    all_summaries = []
    runs = 0
    per_run = []
    for index in range(args.seeds):
        if time.monotonic() - started > args.budget_seconds:
            break
        seed_pair = (1000 + index, 2000 + index)
        report, sample, identity_of = run(scenario, seed_pair, args.ticks)
        windows, summary = analyse(report, sample, identity_of, enemy_names, own_names, intervals, thresholds)
        runs += 1
        all_windows.extend(windows)
        all_summaries.append(summary)
        longest = max((window['gap'] for window in windows), default=0)
        per_run.append(dict(seed=index, ticks=report['result'].get('ticks'),
                            windows=len(windows), longest=longest,
                            verdict=report['result'].get('verdict')))

    print('encounter %d, %d battle(s), tick horizon %d%s' % (
        args.encounter, runs, args.ticks, ' [PINNED]' if args.pinned else ''))
    print('enemy units: %s' % ', '.join('%s period=%d' % (name, intervals[name] + 21)
                                        for name in enemy_names[:6]))
    print('quiet windows (gap > 2 x period, eligible throughout): %d' % len(all_windows))
    by_state = collections.Counter()
    for window in all_windows:
        by_state[window['states'][0][0] if window['states'] else 'unknown'] += 1
    print('dominant state inside those windows:', by_state.most_common())
    print()
    print('per enemy, averaged over %d battle(s):' % runs)
    for enemy in enemy_names:
        eligible = sum(s[enemy]['eligibleTicks'] for s in all_summaries)
        actions = sum(s[enemy]['actions'] for s in all_summaries)
        expected = sum(s[enemy]['expected'] or 0 for s in all_summaries)
        states = collections.Counter()
        for entry in all_summaries:
            states.update(dict(entry[enemy]['stateTime']))
        total = sum(states.values()) or 1
        share = ', '.join('%s %.0f%%' % (name, 100.0 * count / total)
                          for name, count in states.most_common(4))
        print('  %-22s period %-3d eligible %-6d actions %-5.1f expected %-6.1f  ratio %.2f  | %s' % (
            enemy, intervals[enemy] + 21, eligible, actions / runs, expected / runs,
            (actions / expected) if expected else 0, share))
    print()
    longest = sorted(all_windows, key=lambda window: -window['gap'])[:args.top]
    for window in longest:
        print('  %-22s gap %-6d ticks (period %d)  states=%s  peakPlayerQueue=%d' % (
            window['enemy'], window['gap'], window['period'],
            window['states'][:3], window['peakPlayerQueue']))
    if args.json:
        Path(args.json).write_text(json.dumps(dict(
            encounter=args.encounter, pinned=bool(args.pinned), runs=runs, ticks=args.ticks,
            thresholds=thresholds, windows=all_windows, perRun=per_run), indent=1),
            encoding='utf-8')
        print('wrote %s' % args.json)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
