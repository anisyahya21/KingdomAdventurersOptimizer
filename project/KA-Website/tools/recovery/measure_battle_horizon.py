"""Part A: measure the 7,000-tick unresolved tail and pick a safety horizon from data.

The experiment is deliberately *not* a fresh random sample. It takes the exact runs the stored
library recorded as unresolved/censored at the current 7,000-tick horizon -- same candidate, same
math/lib seeds -- and continues each of them at larger horizons until it resolves:

    pypy3.exe measure_battle_horizon.py --horizons 8000 10000 12000 18000

Resolution is read from the engine that produced the stored runs: the native kernel when it accepts
the scenario (parity-verified, ~0.1 s per run), otherwise the canonical Python engine. `--backend
python` forces the canonical engine so a sample can be re-measured independently.

Writes `RE-evidence/20260922-battle-horizon/horizon-measurement.json` unless --out is given.
"""
import argparse
import collections
import ctypes
import json
import os
import sqlite3
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

TICKS_PER_SECOND = 20
DEFAULT_LIBRARY = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / \
    'KingdomAdventurersOptimizer' / 'strategies.sqlite'
DEFAULT_OUT = HERE.parents[2] / 'RE-evidence/20260922-battle-horizon/horizon-measurement.json'


def clock(ticks):
    """`M:SS` nominal game time for a tick count (20 ticks = 1 s)."""
    seconds = ticks / TICKS_PER_SECOND
    return f'{int(seconds) // 60}:{int(seconds) % 60:02d}'


def unresolved_runs(library):
    """Every stored run with no verdict, exactly as the optimiser recorded it."""
    from strategy_optimizer_adapter import validate_scenario

    db = sqlite3.connect(f'file:{library}?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    # A stored scenario is the JSON round trip of a validated one: `load_scenario` restores the
    # integer parameter keys and the other normalisations the engine expects.
    scenarios = {row['id']: validate_scenario(json.loads(row['scenario']))
                 for row in db.execute('select id, scenario from candidate')}
    cases = []
    for row in db.execute('select candidate, phase, ordinal, result from run'):
        result = json.loads(row['result'])
        if result.get('verdict') is not None or not result.get('censored'):
            continue
        scenario = scenarios[row['candidate']]
        math_seed, lib_seed = (int(v) for v in result['seeds'])
        cases.append(dict(candidate=row['candidate'], phase=row['phase'],
                          ordinal=int(row['ordinal']), encounterId=scenario['encounterId'],
                          defeatCount=scenario.get('defeatCount'), horizon=scenario['tickLimit'],
                          mathSeed=math_seed, libSeed=lib_seed, stored=result,
                          scenario=dict(scenario, mathSeed=math_seed, libSeed=lib_seed)))
    db.close()
    return cases


class NativeRunner:
    """The production native path, driven directly so the exact verdict tick is visible."""

    def __init__(self):
        import strategy_optimizer_native as native
        self.native = native
        self.library = native.library()
        self.templates = {}

    def template(self, scenario):
        key = self.native.scenario_key(scenario)
        entry = self.templates.get(key)
        if entry is None:
            entry = self.native._template(scenario, key)
            self.templates[key] = entry
        return entry

    def run(self, scenario, horizon):
        """`(resolved, record)`; `record` is None when the kernel refuses the scenario."""
        entry = self.template(scenario)
        clone = self.library.ka_battle_clone(entry['handle'])
        if not clone:
            return None
        try:
            self.library.ka_battle_seed_rng(clone, scenario['mathSeed'], scenario['libSeed'])
            if entry['follower_draws']:
                self.library.ka_battle_skip_lib_draws(clone, entry['follower_draws'])
            report = __import__('ka_abi').KaBattleReport()
            status = self.library.ka_run_battle(clone, horizon, 0, ctypes.byref(report))
            if status != 0:
                return None
            return dict(verdict=report.verdict, verdictTick=report.verdict_tick,
                        ticks=report.ticks, prizeCallbacks=report.prize_callbacks,
                        preVerdictPrizeCallbacks=report.pre_verdict_prize_callbacks,
                        survivors=report.survivors, backend='native')
        finally:
            self.library.ka_battle_free(clone)


def python_run(scenario, horizon):
    """The canonical Python engine, so a sample can always be re-measured independently."""
    from combat_sandbox import run_scenario
    report = run_scenario(dict(scenario, tickLimit=horizon), include_trace=False)
    result = report['result']
    return dict(verdict=result.get('verdict'), verdictTick=result.get('verdictTick'),
                ticks=result.get('ticks'),
                prizeCallbacks=(result.get('preVerdictPrizeCallbacks')
                                if result.get('verdict') is not None else None),
                preVerdictPrizeCallbacks=result.get('preVerdictPrizeCallbacks'),
                survivors=None, backend='python',
                rewardOutcome=(report.get('rewardEntitlement') or {}).get('awardedChestCount'))


def measure(cases, horizons, backend):
    native = None if backend == 'python' else NativeRunner()
    rows = []
    for index, case in enumerate(cases, 1):
        record = dict(case)
        record.pop('stored', None)
        record['resolvedAt'] = None
        record['observations'] = []
        for horizon in horizons:
            started = time.perf_counter()
            result = None
            if native is not None:
                result = native.run(case['scenario'], horizon)
            if result is None:
                result = python_run(case['scenario'], horizon)
            result['horizon'] = horizon
            result['seconds'] = round(time.perf_counter() - started, 3)
            record['observations'].append(result)
            if result.get('verdict') not in (None, 0):
                record['resolvedAt'] = horizon
                break
        rows.append(record)
        verdict = next((o for o in record['observations'] if o.get('verdict') not in (None, 0)), None)
        print(f"  [{index}/{len(cases)}] enc{case['encounterId']} seed "
              f"{case['mathSeed']}/{case['libSeed']}: " +
              (f"verdict {verdict['verdict']} at tick {verdict['verdictTick']} "
               f"({clock(verdict['verdictTick'])}) reached by {verdict['horizon']}"
               if verdict else 'still unresolved at every horizon'), flush=True)
    return rows


def summarise(rows, horizons):
    resolved = [row for row in rows if row['resolvedAt'] is not None]
    ticks = sorted(o['verdictTick'] for row in resolved
                   for o in row['observations'] if o.get('verdict') not in (None, 0))
    table = []
    for horizon in horizons:
        by = [row for row in rows
              if row['resolvedAt'] is not None
              and next(o['verdictTick'] for o in row['observations']
                       if o.get('verdict') not in (None, 0)) <= horizon]
        table.append(dict(horizon=horizon, resolved=len(by),
                          fraction=len(by) / len(rows) if rows else 0.0))
    summary = dict(
        tested=len(rows), resolved=len(resolved), stillUnresolved=len(rows) - len(resolved),
        resolutionByHorizon=table,
        verdictTicks=dict(
            minimum=min(ticks) if ticks else None,
            median=statistics.median(ticks) if ticks else None,
            p90=(sorted(ticks)[int(.9 * (len(ticks) - 1))] if ticks else None),
            maximum=max(ticks) if ticks else None,
            over7000=sum(1 for tick in ticks if tick > 7000)),
        # `enter_ending` returns 1 when the enemy team is annihilated (own-team win) and 2 when the
        # own team is, which is the same polarity `strategy_optimizer.summarize` uses.
        wins=sum(1 for row in resolved
                 for o in row['observations'] if o.get('verdict') == 1),
        losses=sum(1 for row in resolved
                   for o in row['observations'] if o.get('verdict') == 2))
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', type=Path, default=DEFAULT_LIBRARY)
    parser.add_argument('--horizons', type=int, nargs='+',
                        default=[8000, 10000, 12000, 18000])
    parser.add_argument('--backend', choices=('native', 'python'), default='native')
    parser.add_argument('--limit', type=int, default=0)
    parser.add_argument('--sample', type=int, default=0,
                        help='spread N unresolved runs across the whole stored tail instead of the '
                             'first N in row order')
    parser.add_argument('--out', type=Path, default=DEFAULT_OUT)
    parser.add_argument('--safety-sample', type=int, default=0,
                        help='candidates to re-sample on the production seed banks as a breadth check')
    parser.add_argument('--encounter-scan', action='store_true',
                        help='run every encounter/difficulty on the production seed banks at one horizon')
    parser.add_argument('--performance', type=int, default=0,
                        help='candidates to time at 7,000 then at the production horizon')
    args = parser.parse_args()

    if args.safety_sample:
        return safety_sample(args)
    if args.encounter_scan:
        return encounter_scan(args)
    if args.performance:
        return performance(args)

    cases = unresolved_runs(args.library)
    if args.sample and len(cases) > args.sample:
        stride = len(cases)/args.sample
        cases = [cases[int(index*stride)] for index in range(args.sample)]
    if args.limit:
        cases = cases[:args.limit]
    print(f'{len(cases)} stored unresolved runs at the current horizon; '
          f'horizons={args.horizons} backend={args.backend}')
    started = time.perf_counter()
    rows = measure(cases, sorted(args.horizons), args.backend)
    summary = summarise(rows, sorted(args.horizons))
    summary['backend'] = args.backend
    summary['seconds'] = round(time.perf_counter() - started, 2)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(dict(summary=summary, rows=rows), indent=2), encoding='utf-8')
    print('\nresolution of the stored 7,000-tick unresolved set:')
    for entry in summary['resolutionByHorizon']:
        print(f"  by {entry['horizon']:>6} ticks ({clock(entry['horizon']):>5}): "
              f"{entry['resolved']}/{summary['tested']} = {entry['fraction']:.0%}")
    print(f"  still unresolved: {summary['stillUnresolved']}/{summary['tested']} "
          f"({summary['stillUnresolved'] / max(1, summary['tested']):.0%})")
    print(f"  verdict ticks: {summary['verdictTicks']}")
    print(f"  wins={summary['wins']} losses={summary['losses']}")
    print(f"  evidence: {args.out}  ({summary['seconds']}s)")
    return 0


def safety_sample(args):
    """Breadth check over candidates already in the library (see `encounter_scan` for all encounters)."""
    return _banked_run(args, label='safety-sample')


def encounter_scan(args):
    """Every encounter/difficulty the UI shows, on the production seed banks.

    The library only holds candidates for one encounter, so this derives the other combinations from
    the same base scenario the desktop's "expand encounter set" command uses. It answers two
    questions the horizon decision needs: does any other encounter have a longer resolution tail, and
    does a genuine stalemate (no verdict even at a long horizon) exist anywhere in the set.
    """
    from strategy_optimizer_adapter import validate_scenario

    db = sqlite3.connect(f'file:{args.library}?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    base = validate_scenario(json.loads(db.execute(
        'select scenario from candidate order by created limit 1').fetchone()['scenario']))
    db.close()
    limit = args.horizons[0]
    scenarios = []
    for encounter_id in range(20):
        scenario = dict(base, encounterId=encounter_id)
        if scenario['tickLimit'] < limit:
            scenario['tickLimit'] = 7000          # the template does not use the horizon
        scenarios.append((encounter_id, validate_scenario(scenario)))
    return _banked_run(args, label='encounter-scan', scenarios=scenarios)


def performance(args):
    """Cost of the horizon itself, measured on the same seed bank at both values.

    The production finish policy is `at-horizon`, so the engine cannot stop at the verdict: it is
    asked to run every tick it is given. That makes the interesting number not "what does a battle
    cost" but "how much of the horizon is actually executed", which is measured here rather than
    assumed -- along with the ticks a battle *would* have needed, from the same runs.
    """
    import strategy_optimizer as optimizer_mod
    import strategy_optimizer_adapter as adapter

    db = sqlite3.connect(f'file:{args.library}?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    scenarios = [(row['id'], adapter.validate_scenario(json.loads(row['scenario'])))
                 for row in db.execute('select id, scenario from candidate order by created')]
    db.close()
    scenarios = scenarios[:args.performance]
    production = max(args.horizons)
    for horizon in (7000, production):
        runner = NativeRunner()
        executed, needed, seconds = [], [], []
        for cid, scenario in scenarios:
            for phase, count in (('discovery', 8), ('validation', 8)):
                for ordinal in range(count):
                    math_seed, lib_seed = optimizer_mod.seed_pair(phase, ordinal)
                    case = dict(scenario, mathSeed=math_seed, libSeed=lib_seed)
                    started = time.perf_counter()
                    result = runner.run(case, horizon) or python_run(case, horizon)
                    seconds.append(time.perf_counter() - started)
                    executed.append(result['ticks'])
                    # An unresolved run reports verdict_tick -1, which is truthy; only a real verdict
                    # counts as "the tick this battle needed".
                    needed.append(result['verdictTick']
                                  if result.get('verdict') not in (None, 0) else horizon)
        cases = len(seconds)
        total = sum(seconds)
        print(f'horizon {horizon:>6} ({clock(horizon):>5}): {cases} runs, '
              f'{total:.2f}s total, {cases / total:.2f} battles/s single-process, '
              f'mean run {statistics.mean(seconds) * 1e3:.1f}ms')
        print(f'            executed ticks: mean {statistics.mean(executed):.0f}, '
              f'reaching the maximum {sum(1 for t in executed if t >= horizon) / cases:.1%}')
        print(f'            ticks a verdict needed: mean {statistics.mean(needed):.0f}, '
              f'max {max(needed)}, unresolved {sum(1 for n in needed if n >= horizon)}')
    return 0


def _banked_run(args, label, scenarios=None):
    """Breadth check: the optimiser's own seed banks, run to one long horizon.

    The primary experiment only proves what the *stored* unresolved runs do. This measures a wider
    slice of the same distribution -- the candidates already in the library, on the exact discovery
    and validation seed banks the search draws from -- so a horizon can be chosen for the tail rather
    than for 34 cases, and so a genuine stalemate can be found if one exists.
    """
    from strategy_optimizer import seed_pair
    from strategy_optimizer_adapter import validate_scenario

    if scenarios is None:
        db = sqlite3.connect(f'file:{args.library}?mode=ro', uri=True)
        db.row_factory = sqlite3.Row
        scenarios = [(row['id'], validate_scenario(json.loads(row['scenario'])))
                     for row in db.execute('select id, scenario from candidate order by created')]
        db.close()
        scenarios = scenarios[:args.safety_sample]
    cases = []
    for cid, scenario in scenarios:
        for phase, count in (('discovery', 8), ('validation', 16)):
            for ordinal in range(count):
                math_seed, lib_seed = seed_pair(phase, ordinal)
                cases.append(dict(candidate=cid, phase=phase, ordinal=ordinal,
                                  encounterId=scenario['encounterId'],
                                  defeatCount=scenario.get('defeatCount'), horizon=scenario['tickLimit'],
                                  mathSeed=math_seed, libSeed=lib_seed,
                                  scenario=dict(scenario, mathSeed=math_seed, libSeed=lib_seed)))
    horizon = max(args.horizons)
    print(f'{len(cases)} sampled runs from {len(scenarios)} scenarios at {horizon} ticks '
          f'({clock(horizon)})')
    rows = measure(cases, [horizon], args.backend)
    summary = summarise(rows, [horizon])
    summary['mode'] = label
    summary['horizon'] = horizon
    summary['scenarios'] = len(scenarios)
    # The breadth samples do not need each run's full scenario: they are keyed by candidate id, and
    # the candidate's scenario is in the library. Keeping it would duplicate megabytes of identical
    # setup JSON per row.
    for row in rows:
        row.pop('scenario', None)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(dict(summary=summary, rows=rows), indent=2), encoding='utf-8')
    print(f"  resolved: {summary['resolved']}/{summary['tested']} "
          f"({summary['resolved'] / max(1, summary['tested']):.1%})")
    print(f"  unresolved at {horizon}: {summary['stillUnresolved']}")
    print(f"  verdict ticks: {summary['verdictTicks']}")
    per_scenario = []
    for cid, scenario in scenarios[:24]:
        mine = [row for row in rows if row['candidate'] == cid]
        ticks = [o['verdictTick'] for row in mine for o in row['observations']
                 if o.get('verdict') not in (None, 0)]
        per_scenario.append(
            f"enc{scenario['encounterId']}: n={len(mine)} "
            f"unresolved={sum(1 for row in mine if row['resolvedAt'] is None)} "
            f"maxTick={max(ticks) if ticks else None}")
    print('  per scenario: ' + ' | '.join(per_scenario))
    print(f"  evidence: {args.out}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
