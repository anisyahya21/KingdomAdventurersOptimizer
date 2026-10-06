"""Part M: what the lane objective changes about a controlled search, measured on one fixture.

Two searches are run back to back on identical fresh libraries, identical seeds and identical worker
settings, and only the objective differs:

    legacy  the previous single score: Wilson >= 0.80 archive admission, archive-only parents
    lanes   the elite-lane objective: no reliability threshold, four lanes, lane-rotating parents

`strategy_optimizer.OBJECTIVE_MODE` selects the mode for the measurement only; the shipped default is
`lanes`. Reported for each run: unique promising parents retained, best earned, best potential, best
stored-attack setup, family diversity, candidate churn, utilisation, empty plans and which lane
nominated each parent. Plus a static comparison on a real stored library: how many candidates the old
parent source could draw from versus how many the lanes keep.

    python check_optimizer_objective_search.py [--seconds 60] [--workers 24] [--json OUT]
"""
import argparse
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import strategy_optimizer as optimizer  # noqa: E402
from strategy_optimizer_adapter import default_scenario, provenance, stats  # noqa: E402

LIVE_LIBRARY = Path(r'C:\Users\anisb\OneDrive\Desktop\replit kingdom adventures - Copy\KA-Website'
                    r'\strategiespostrust1000tickslimit.sqlite')


def fresh_library(path, ticks):
    """The same starting point for both modes: one supplied baseline, one encounter, one horizon."""
    scenario = dict(default_scenario(), tickLimit=ticks, encounterId=19)
    store = optimizer.Store(path, provenance())
    with store.db:
        store.set('scope', optimizer.scope(scenario))
        store.add(scenario, 'Objective fixture', 'supplied', stats(scenario))
    store.close()


def run_search(path, mode, seconds, workers):
    optimizer.OBJECTIVE_MODE = mode
    live = optimizer.Optimizer(path)
    try:
        deadline = time.monotonic()+60
        while live.status()['state'] == 'Opening library' and time.monotonic() < deadline:
            time.sleep(.05)
        live.command('start', dict(workers=workers, duty=1), wait=True)
        started = time.monotonic()
        while time.monotonic()-started < seconds:
            time.sleep(.5)
        status = live.status()
        wall = time.monotonic()-started
        live.command('pause', {}, wait=True)
        time.sleep(1.0)
        status = live.status()
    finally:
        live.command('close')
        live.thread.join(180)
        optimizer.OBJECTIVE_MODE = 'lanes'
    return status, wall


def describe(path, status, wall, mode):
    """Everything the task asks a controlled search to report, from the store that just ran."""
    store = optimizer.Store(path, provenance())
    try:
        aggregates = {row[0]: json.loads(row[1]) for row in store.db.execute(
            "SELECT key, value FROM meta WHERE key LIKE 'aggregate:%'")}
        rows = list(store.db.execute('SELECT id, label, source, scenario FROM candidate'))
        records = [optimizer.lane_record(row, optimizer.merge_aggregates(
            aggregates.get(f'aggregate:{row["id"]}:discovery'),
            aggregates.get(f'aggregate:{row["id"]}:validation'))) for row in rows]
        lanes = optimizer.elite_lanes(records)
        lane_members = sum(len(pool) for groups in lanes.values() for pool in groups.values())
        archive_ids = {row['candidate'] for row in store.archive()}
        families = len({optimizer.encounter_family(json.loads(row['scenario']),
                                                   store.rows(row['id'], 'validation')[:optimizer.VALIDATION_RUNS])
                        for row in rows if store.rows(row['id'], 'validation')})
        best_setup = max((record['progress'].get('postDeathPrizes', 0) for record in records),
                         default=0)
        best_setup_mass = max((record['progress'].get('storedCommandsTargetingBossAtDeath', 0)
                               for record in records), default=0)
        created = len(rows)
        return dict(
            mode=mode, seconds=round(wall, 1), candidatesAfter=created,
            runs=status.get('totalRuns'),
            battlesPerSecond=round((status.get('totalRuns') or 0)/max(1e-9, wall), 2),
            # "Unique promising parents retained" is exactly what each objective kept as a parent
            # source: the lane pools, or the archive the old rule admitted to.
            promisingParentsRetained=(lane_members if mode == 'lanes' else len(archive_ids)),
            archiveCells=len(archive_ids), laneMembers=lane_members,
            bestEarned=max((record['earnedMax'] or 0 for record in records), default=0),
            bestPotential=max((record['potentialMax'] or 0 for record in records), default=0),
            bestStoredAttackSetup=best_setup, bestStoredAttackMass=best_setup_mass,
            candidatesWithProgress=sum(1 for record in records if record['progressRuns'] > 0),
            completeBanks=sum(1 for record in records
                              if record['n'] >= optimizer.VALIDATION_RUNS
                              and record['censored'] == 0),
            behaviourFamilies=families,
            proposals=store.get('proposals'), improvements=store.get('improvements'),
            proposalAxes=store.get('proposalAxes'), proposalLanes=store.get('proposalLanes'),
            utilisation=status.get('scheduler', {}).get('idleWorkerSeconds'),
            emptyPlans=status.get('scheduler', {}).get('emptyPlans'),
            scheduler=status.get('scheduler'),
            throughput=status.get('throughput'),
        )
    finally:
        store.close()


def static_comparison(library):
    """On an existing library: the old parent source versus the lanes, from the same stored evidence."""
    with tempfile.TemporaryDirectory(prefix='ka-static-') as root:
        path = Path(root)/'static.sqlite'
        shutil.copyfile(library, path)
        store = optimizer.Store(path, provenance())
        try:
            rows = list(store.db.execute('SELECT id, label, source, scenario FROM candidate'))

            def view(label):
                aggregates = {row[0]: json.loads(row[1]) for row in store.db.execute(
                    "SELECT key, value FROM meta WHERE key LIKE 'aggregate:%'")}
                records = [optimizer.lane_record(row, optimizer.merge_aggregates(
                    aggregates.get(f'aggregate:{row["id"]}:discovery'),
                    aggregates.get(f'aggregate:{row["id"]}:validation'))) for row in rows]
                lanes = optimizer.elite_lanes(records)
                return dict(
                    phase=label, candidates=len(rows),
                    archiveMembers=len({row['candidate'] for row in store.archive()}),
                    laneMembers=sum(len(pool) for groups in lanes.values()
                                    for pool in groups.values()),
                    laneMembersByLane={lane: sum(len(groups.get(lane, []))
                                                 for groups in lanes.values())
                                       for lane in optimizer.LANE_NAMES},
                    withProgress=sum(1 for record in records if record['progressRuns'] > 0),
                    earnedOrPotential=sum(1 for record in records
                                          if record['earnedMax'] is not None
                                          or record['potentialMax'] is not None))

            before = view('stored (old aggregates)')
            migration = optimizer.migrate_objective(store)
            after = view('after migrate_objective')
            return dict(library=library.name, before=before, after=after,
                        migration=dict(runsWithProgressMetrics=migration['runsWithProgressMetrics'],
                                       runsWithoutProgressMetrics=migration['runsWithoutProgressMetrics']))
        finally:
            store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=float, default=60)
    parser.add_argument('--workers', type=int, default=24)
    parser.add_argument('--ticks', type=int, default=10000)
    parser.add_argument('--library', type=Path, default=LIVE_LIBRARY)
    parser.add_argument('--static-only', action='store_true',
                        help='skip the two controlled searches and only compare stored evidence')
    parser.add_argument('--json', type=Path)
    args = parser.parse_args()

    report = dict(seconds=args.seconds, workers=args.workers, ticks=args.ticks, runs={})
    if args.static_only:
        report['runs'] = json.loads(args.json.read_text(encoding='utf-8'))['runs'] \
            if args.json and args.json.is_file() else {}
    else:
        with tempfile.TemporaryDirectory(prefix='ka-objective-') as root:
            for mode in ('legacy', 'lanes'):
                path = Path(root)/f'{mode}.sqlite'
                fresh_library(path, args.ticks)
                status, wall = run_search(path, mode, args.seconds, args.workers)
                report['runs'][mode] = describe(path, status, wall, mode)
                row = report['runs'][mode]
                print(f"  {mode:<7} parents {row['promisingParentsRetained']:>3} "
                      f"(archive {row['archiveCells']}, lanes {row['laneMembers']}) · "
                      f"earned {row['bestEarned']} · potential {row['bestPotential']} · "
                      f"setup {row['bestStoredAttackSetup']} · "
                      f"families {row['behaviourFamilies']} · runs {row['runs']} · "
                      f"completeBanks {row['completeBanks']} · "
                      f"emptyPlans {row['emptyPlans']}", flush=True)
    if args.library.is_file():
        report['static'] = static_comparison(args.library)
        static = report['static']
        print(f"  stored library {static['library']}:")
        for phase in ('before', 'after'):
            view = static[phase]
            print(f"    {phase:<24} archive {view['archiveMembers']} members · lanes "
                  f"{view['laneMembers']} {view['laneMembersByLane']} · "
                  f"candidates with earned/potential evidence {view['earnedOrPotential']} · "
                  f"with stored-attack metrics {view['withProgress']}", flush=True)
        print(f"    migration reused {static['migration']['runsWithoutProgressMetrics']} runs "
              f"({static['migration']['runsWithProgressMetrics']} carried progress metrics)",
              flush=True)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, indent=1, default=str), encoding='utf-8')
    return 0


if __name__ == '__main__':
    sys.exit(main())
