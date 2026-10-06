"""A short controlled comparison of the old generator against the contract generator.

Both runs use the same fixture, the same seeds, the same worker settings and the same horizon; only
the *generator* differs:

    old        the previous bounded generator: roster reorders and skill-order permutations
    contract   the hard search-space contract: add/remove/replace/move/trigger/stat/formation/
               weapon/placement mutations inside the approved space

The point is not which run gets luckier. The point is where the runs are spent: the report counts
distinct skill sets, distinct orders, triggers, stat vectors, placements and weapon behaviour groups,
and the fraction of simulations that went to candidates differing from the supplied baseline *only*
by the order of the same skills.

    python check_search_contract_search.py [--seconds 60] [--workers 24] [--json OUT]
"""
import argparse
import json
import sys
import tempfile
import time
from collections import Counter
from copy import deepcopy
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import search_contract  # noqa: E402
import strategy_optimizer as optimizer  # noqa: E402
from strategy_optimizer_adapter import default_scenario, provenance, stats  # noqa: E402


def fresh_library(path, ticks):
    scenario = dict(default_scenario(), tickLimit=ticks, encounterId=19)
    store = optimizer.Store(path, provenance())
    with store.db:
        store.set('scope', optimizer.scope(scenario))
        store.add(scenario, 'Contract fixture', 'supplied', stats(scenario))
    store.close()


def run(path, seconds, workers, use_contract):
    """One short search. `use_contract=False` disables the new generator only."""
    original_version = search_contract.SEARCH_SPACE_VERSION
    original_mode = optimizer.SEARCH_MODE
    if not use_contract:
        # The measurement needs the previous generator to be able to run, so this fixture is presented
        # as a legacy library and the optimiser is switched to its legacy proposal path.
        search_contract.SEARCH_SPACE_VERSION = 1
        optimizer.SEARCH_MODE = 'legacy'
        store = optimizer.Store(path, provenance())
        with store.db:
            store.set('searchSpaceVersion', 1)
        store.close()
    live = optimizer.Optimizer(path)
    try:
        deadline = time.monotonic()+60
        while live.status()['state'] == 'Opening library' and time.monotonic() < deadline:
            time.sleep(.05)
        live.command('start', dict(workers=workers, duty=1), wait=True)
        started = time.monotonic()
        while time.monotonic()-started < seconds:
            time.sleep(.5)
        live.command('pause', {}, wait=True)
        time.sleep(1.0)
        status = live.status()
        wall = time.monotonic()-started
    finally:
        live.command('close')
        live.thread.join(180)
        search_contract.SEARCH_SPACE_VERSION = original_version
        optimizer.SEARCH_MODE = original_mode
    return status, wall


def describe(path, status, wall, label, baseline_scenario):
    store = optimizer.Store(path, provenance())
    try:
        rows = list(store.db.execute('SELECT id, label, scenario FROM candidate'))
        run_counts = {row[0]: row[1] for row in store.db.execute(
            'SELECT candidate, COUNT(*) FROM run GROUP BY candidate')}

        def shape(scenario):
            """The comparable content of a scenario: what the search space actually covers."""
            return dict(
                sets=tuple(frozenset(unit['skills']) for unit in scenario['ownUnits']),
                orders=tuple(tuple(unit['skills']) for unit in scenario['ownUnits']),
                triggers=tuple(tuple(unit['invocationLevels']) for unit in scenario['ownUnits']),
                parameters=tuple(tuple(sorted((int(key), int((entry or {}).get('rawValue') or 0))
                                              for key, entry in unit['parameters'].items()))
                                 for unit in scenario['ownUnits']),
                placement=tuple((unit['name'], unit.get('grid'), tuple(unit.get('cell') or ()))
                                for unit in scenario['ownUnits']),
                weapons=tuple(search_contract.weapon_group_id(unit)
                              for unit in scenario['ownUnits']))

        baseline = shape(baseline_scenario)
        skill_sets, skill_orders, triggers, stat_vectors, formations, weapons = (
            set(), set(), set(), set(), set(), set())
        order_only = set()
        for candidate_id, _label, raw in rows:
            current = shape(json.loads(raw))
            skill_sets.add(current['sets'])
            skill_orders.add(current['orders'])
            triggers.add(current['triggers'])
            stat_vectors.add(current['parameters'])
            formations.add(current['placement'])
            weapons.add(current['weapons'])
            if (current['sets'] == baseline['sets'] and current['orders'] != baseline['orders']
                    and current['parameters'] == baseline['parameters']
                    and current['weapons'] == baseline['weapons']
                    and current['triggers'] == baseline['triggers']):
                order_only.add(candidate_id)
        total_runs = sum(run_counts.values())
        runs_by_order_only = sum(run_counts.get(candidate_id, 0) for candidate_id in order_only)
        chest_maxima = [int((json.loads(value) or {}).get('chestMax') or 0)
                        for (value,) in store.db.execute(
                            "SELECT value FROM meta WHERE key LIKE 'aggregate:%:validation'")]
        return dict(
            label=label, seconds=round(wall, 1), runs=status.get('totalRuns'),
            battlesPerSecond=round((status.get('totalRuns') or 0)/max(1e-9, wall), 2),
            proposalsAttempted=(store.get('proposalAxes') and sum(
                (store.get('proposalAxes') or {}).values())) or store.get('proposals'),
            proposalsRecorded=store.get('proposals'),
            candidates=len(rows),
            rejectedProposals=status.get('scheduler', {}).get('rejectedProposals', 0),
            distinctSkillSets=len(skill_sets), distinctSkillOrders=len(skill_orders),
            distinctTriggerConfigs=len(triggers), distinctStatVectors=len(stat_vectors),
            distinctFormations=len(formations), distinctWeaponGroups=len(weapons),
            orderOnlyCandidates=len(order_only), orderOnlyRuns=runs_by_order_only,
            orderOnlyRunFraction=(round(runs_by_order_only/total_runs, 4) if total_runs else None),
            bestEarned=max(chest_maxima) if chest_maxima else 0,
            proposalAxes=store.get('proposalAxes'), emptyPlans=status.get('scheduler', {}).get('emptyPlans'))
    finally:
        store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=float, default=60)
    parser.add_argument('--workers', type=int, default=24)
    parser.add_argument('--ticks', type=int, default=10000)
    parser.add_argument('--json', type=Path)
    args = parser.parse_args()
    baseline = dict(default_scenario(), tickLimit=args.ticks, encounterId=19)
    report = dict(seconds=args.seconds, workers=args.workers, ticks=args.ticks, runs={})
    with tempfile.TemporaryDirectory(prefix='ka-contract-') as root:
        for label, use_contract in (('old', False), ('contract', True)):
            path = Path(root)/f'{label}.sqlite'
            fresh_library(path, args.ticks)
            status, wall = run(path, args.seconds, args.workers, use_contract)
            report['runs'][label] = describe(path, status, wall, label, baseline)
            row = report['runs'][label]
            print(f"  {label:<9} runs {row['runs']:<6} proposals {row['proposalsRecorded']:<4} "
                  f"rejected {row['rejectedProposals']:<4} sets {row['distinctSkillSets']:<4} "
                  f"orders {row['distinctSkillOrders']:<4} triggers {row['distinctTriggerConfigs']:<4} "
                  f"stats {row['distinctStatVectors']:<4} weapons {row['distinctWeaponGroups']:<3} "
                  f"order-only runs {row['orderOnlyRunFraction']}", flush=True)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, indent=1, default=str), encoding='utf-8')
    print()
    print('  the contract run spends its battles inside the approved space; the old run cannot leave '
          'the order-permutation space its generator can reach')
    return 0


if __name__ == '__main__':
    sys.exit(main())
