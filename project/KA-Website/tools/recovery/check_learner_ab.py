"""Controlled before/after of the learner: the previous v3 scheduler versus the branching learner.

Both arms run on a fresh Search-Space-v3 library with the same encounter set, the same horizon, the
same worker count, the same shared seed banks and the same wall-clock budget. Only `LEARNER_MODE`
differs (`legacy` reproduces the previous one-child-at-a-time scheduler with the two-loss deferral;
`branching` is the reservoir learner). Search Space v3 generation is identical in both arms, so the
comparison measures the *learner*, not the generator.

    python check_learner_ab.py [--seconds 90] [--workers 24] [--trials 1] [--ticks 10000] [--json OUT]
"""
import argparse
import json
import shutil
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import strategy_learner as learner  # noqa: E402
import strategy_optimizer as optimizer  # noqa: E402
from strategy_optimizer_adapter import default_scenario, provenance, stats  # noqa: E402


def new_library(path, ticks):
    """The production fixture: the default scenario at the declared horizon, one baseline to start."""
    scenario = dict(default_scenario(), tickLimit=ticks, encounterId=19)
    store = optimizer.Store(path, provenance())
    with store.db:
        store.set('scope', optimizer.scope(scenario))
        store.add(scenario, 'Learner fixture', 'supplied', stats(scenario))
    store.close()


def run_arm(path, mode, seconds, workers):
    original_mode = optimizer.LEARNER_MODE
    optimizer.LEARNER_MODE = mode
    live = optimizer.Optimizer(path)
    try:
        deadline = time.monotonic()+60
        while live.status()['state'] == 'Opening library' and time.monotonic() < deadline:
            time.sleep(.05)
        live.command('start', dict(workers=workers, duty=1), wait=True)
        started = time.monotonic()
        busy, samples = [], 0
        while time.monotonic()-started < seconds:
            status = live.status()
            busy.append((status.get('scheduler') or {}).get('busy', 0))
            if samples % 4 == 0:
                pass
            samples += 1
            time.sleep(.5)
        live.command('pause', {}, wait=True)
        time.sleep(1.0)
        status = live.status()
        wall = time.monotonic()-started
    finally:
        live.command('close')
        live.thread.join(300)
        optimizer.LEARNER_MODE = original_mode
    return status, wall, busy


def describe(path, status, wall, busy, mode):
    store = optimizer.Store(path, provenance())
    try:
        keys = optimizer.candidate_keys(store)
        aggregates = {row[0]: json.loads(row[1]) for row in store.db.execute(
            "SELECT key, value FROM meta WHERE key LIKE 'aggregate:%'")}
        records = optimizer.population_records(store, keys, aggregates)
        lineage = store.lineage()
        improvements = store.get('childImprovements') or {}
        regions = store.get('strategyRegions') or {}
        operators = store.get('operatorStats') or {}
        scheduler = status.get('scheduler') or {}
        idle = scheduler.get('idleWorkerSeconds', 0.)
        simulated = sum(1 for cid in keys if int((aggregates.get(
            f'aggregate:{cid}:discovery') or {}).get('n', 0) or 0) > 0)
        depths = Counter(row['depth'] for row in lineage.values())
        attempts = sum(entry.get('attempts', 0) for group in operators.values()
                       for entry in group.values())
        return dict(
            mode=mode, seconds=round(wall, 1), runs=status.get('totalRuns'),
            battlesPerSecond=round((status.get('totalRuns') or 0)/max(1e-9, wall), 2),
            capacity=(status.get('throughput') or {}).get('capacityBattlesPerSecond'),
            utilisation=round(1-idle/max(1e-9, len(busy[0:1])*0+status.get('throughput', {}).get('workers', 1)*wall), 4),
            idleWorkerSeconds=round(idle, 2), busySamples=dict(Counter(busy)),
            busyMedian=sorted(b for b in busy if isinstance(b, int))[len(busy)//2] if busy else None,
            proposals=store.get('proposals'), rejected=scheduler.get('rejectedProposals', 0),
            emptyPlans=scheduler.get('emptyPlans'), reservoirFill=scheduler.get('reservoirFill'),
            planSeconds=round(scheduler.get('planSeconds', 0.), 2),
            proposalSeconds=round(scheduler.get('proposalSeconds', 0.), 2),
            recordSeconds=round(scheduler.get('recordSeconds', 0.), 2),
            publishSeconds=round(scheduler.get('publishSeconds', 0.), 2),
            candidates=store.db.execute('SELECT COUNT(*) FROM candidate').fetchone()[0],
            candidatesCreated=len(keys), candidatesSimulated=simulated,
            lineageRows=len(lineage), lineages=len({row['root'] for row in lineage.values()}),
            deepestGeneration=max((row['depth'] for row in lineage.values()), default=0),
            generationHistogram=dict(sorted(depths.items())),
            childrenImproved=len(improvements),
            operatorAttempts=attempts,
            operatorImproved=sum(entry.get('improved', 0) for group in operators.values()
                                 for entry in group.values()),
            regions=len(regions),
            distinctSkillSets=len({tuple(frozenset(unit['skills']) for unit in
                                         json.loads(row['scenario'])['ownUnits']) for row in
                                   store.db.execute('SELECT scenario FROM candidate')}),
            distinctOrders=len({tuple(tuple(unit['skills']) for unit in
                                      json.loads(row['scenario'])['ownUnits']) for row in
                                store.db.execute('SELECT scenario FROM candidate')}),
            distinctTriggers=len({tuple(tuple(unit['invocationLevels']) for unit in
                                        json.loads(row['scenario'])['ownUnits']) for row in
                                  store.db.execute('SELECT scenario FROM candidate')}),
            distinctStatVectors=len({tuple(tuple(sorted((int(k), int((v or {}).get('rawValue') or 0))
                                                        for k, v in unit['parameters'].items()))
                                           for unit in json.loads(row['scenario'])['ownUnits'])
                                     for row in store.db.execute('SELECT scenario FROM candidate')}),
            distinctWeapons=len({tuple(optimizer.search_contract.weapon_group_id(unit)
                                       for unit in json.loads(row['scenario'])['ownUnits'])
                                 for row in store.db.execute('SELECT scenario FROM candidate')}),
            bestEarned=max([record.get('earnedMax') or 0 for record in records], default=0),
            bestPotential=max([record.get('potentialMax') or 0 for record in records], default=0),
            bestSetup=max([(record.get('progress') or {}).get('postDeathPrizes', 0)
                           for record in records], default=0),
            diskBytes=sum(p.stat().st_size for p in path.parent.glob(path.name+'*') if p.is_file()),
        )
    finally:
        store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=float, default=90)
    parser.add_argument('--workers', type=int, default=24)
    parser.add_argument('--trials', type=int, default=1)
    parser.add_argument('--ticks', type=int, default=10000)
    parser.add_argument('--json', type=Path)
    args = parser.parse_args()
    report = dict(seconds=args.seconds, workers=args.workers, ticks=args.ticks, trials=[])
    # Windows keeps a worker's sqlite handle briefly after the pool is torn down; the fixture files are
    # temporary either way, so a failed directory cleanup must never discard the measurement.
    with tempfile.TemporaryDirectory(prefix='ka-learner-ab-', ignore_cleanup_errors=True) as root:
        for trial in range(args.trials):
            for mode in ('legacy', 'branching'):
                path = Path(root)/f'{mode}-{trial}.sqlite'
                new_library(path, args.ticks)
                status, wall, busy = run_arm(path, mode, args.seconds, args.workers)
                row = describe(path, status, wall, busy, mode)
                row['trial'] = trial
                report['trials'].append(row)
                print(f"  trial {trial} {mode:<9} runs {row['runs']:<6} "
                      f"{row['battlesPerSecond']:>6} battles/s  util {row['utilisation']:.3f}  "
                      f"candidates {row['candidates']:<3} lineages {row['lineages']:<3} "
                      f"depth {row['deepestGeneration']:<2} improvements {row['childrenImproved']:<3} "
                      f"sets {row['distinctSkillSets']} orders {row['distinctOrders']} "
                      f"triggers {row['distinctTriggers']} stats {row['distinctStatVectors']} "
                      f"weapons {row['distinctWeapons']}", flush=True)
        if args.json:
            args.json.parent.mkdir(parents=True, exist_ok=True)
            args.json.write_text(json.dumps(report, indent=1, default=str), encoding='utf-8')
    print()
    print('  the branching learner keeps a reservoir of independent siblings from evidence-bearing '
          'parents; the legacy arm reproduces the one-child-at-a-time scheduler')
    return 0


if __name__ == '__main__':
    sys.exit(main())
