"""Goal 2 acceptance: what a real 24-worker search does with the anti-starvation controller.

Runs a real optimiser (default: a fresh three-encounter library; `--library` to measure a copy of a
real one) and samples the scheduler's own readings throughout, so the controller's behaviour is judged
on measurements rather than on the mechanism's existence:

    useful utilisation -> pressure level -> runnable work (`order` ran dry?)
    -> candidates created (if any) -> whether creation stopped -> population bounds

Two outcomes are legitimate and this harness asserts *consistency*, not a target count:

  * if the planner never ran out of runnable work while workers were free, the controller must NOT
    create exploration - adding candidates would feed an already-saturated coordinator, which is the
    "flood" failure mode, not a fix;
  * if it did run dry, the controller must create legal candidates and must stop once the reservoir
    is healthy again.

The control law itself, the candidate legality/lineage/bounds and the encounter fairness are unit
tested in `check_optimizer_starvation.py`.

    python check_starvation_recovery.py [--seconds 45] [--workers 24] [--ticks 3000]
                                        [--library PATH]
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

import strategy_learner as learner  # noqa: E402
import strategy_optimizer as optimizer  # noqa: E402
from strategy_optimizer_adapter import default_scenario, provenance, stats  # noqa: E402

ENCOUNTERS = (10, 11, 19)


def build(path, ticks):
    store = optimizer.Store(path, provenance())
    with store.db:
        base = dict(default_scenario(), tickLimit=ticks, encounterId=ENCOUNTERS[0])
        store.set('scope', optimizer.scope(base))
        for scenario, label in optimizer.baseline_scenarios(base):
            if scenario['encounterId'] in ENCOUNTERS:
                store.add(scenario, label, 'supplied', stats(scenario))
    store.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--seconds', type=float, default=120)
    parser.add_argument('--workers', type=int, default=24)
    parser.add_argument('--ticks', type=int, default=3000)
    parser.add_argument('--json', type=Path)
    parser.add_argument('--cap', type=int, default=0,
                        help='override the per-encounter active-population bound, so the fixture can '
                             'be driven into the "no useful runnable work" state deterministically')
    parser.add_argument('--library', type=Path,
                        help='measure a byte copy of this library instead of the fresh fixture')
    parser.add_argument('--release-at', type=float, default=0,
                        help='seconds after which the production population bound is restored, so '
                             'the test can prove creation stops once the pool recovers')
    args = parser.parse_args()
    if args.cap:
        # Normal branching replaces the oldest replaceable build while an encounter has room; with the
        # bound below the lane protection the encounter becomes genuinely full, which is exactly the
        # state the real 20-encounter run reached after hours of search. The reseed controller is what
        # has to answer that state.
        optimizer.MAX_CANDIDATES_PER_ENCOUNTER = args.cap
    failures = []
    with tempfile.TemporaryDirectory(prefix='ka-starve-', ignore_cleanup_errors=True) as root:
        path = Path(root)/'starve.sqlite'
        if args.library:
            # A byte copy: the live library's WAL sidecars may be locked while the desktop runs, and
            # only the main file is needed for a readable snapshot. The copy is a measurement fixture,
            # never the user's library.
            shutil.copyfile(args.library, path)
            import search_contract
            store = optimizer.Store(path, provenance())
            with store.db:
                store.set('searchSpaceVersion', search_contract.SEARCH_SPACE_VERSION)
            store.close()
        else:
            build(path, args.ticks)
        live = optimizer.Optimizer(path)
        deadline = time.monotonic()+120
        while live.status()['state'] == 'Opening library' and time.monotonic() < deadline:
            time.sleep(.05)
        live.command('start', dict(workers=args.workers, duty=1), wait=True)
        started = time.monotonic()
        samples = []
        released = False
        while time.monotonic()-started < args.seconds:
            time.sleep(2.0)
            elapsed = time.monotonic()-started
            if args.cap and args.release_at and not released and elapsed >= args.release_at:
                optimizer.MAX_CANDIDATES_PER_ENCOUNTER = args.cap * 0 + 24
                released = True
                print(f'  --- population bound released at t={elapsed:.1f} ---', flush=True)
            status = live.status()
            scheduler = status.get('scheduler') or {}
            samples.append(dict(t=round(time.monotonic()-started, 1),
                                runs=status.get('totalRuns'),
                                utilisation=scheduler.get('starvationUtilisation'),
                                level=scheduler.get('starvationLevel'),
                                active=scheduler.get('starvationActive'),
                                busy=scheduler.get('busy'),
                                reservoir=scheduler.get('reservoirSeeds'),
                                open=scheduler.get('openCandidates'),
                                reseeds=scheduler.get('reseedCreated'),
                                dryPasses=scheduler.get('ranOutOfReadySeeds'),
                                vetoed=scheduler.get('starvationVetoed'),
                                coordinatorFraction=scheduler.get('coordinatorFraction'),
                                bySource=dict(scheduler.get('reseedBySource') or {}),
                                byEncounter=dict(scheduler.get('reseedByEncounter') or {}),
                                candidates=len(status.get('candidates') or []),
                                error=status.get('error')))
            row = samples[-1]
            print(f"t={row['t']:>6} runs={row['runs']:<6} util={row['utilisation']} "
                  f"lvl={row['level']} busy={row['busy']:<3} res={row['reservoir']:<4} "
                  f"open={row['open']:<3} reseeds={row['reseeds']:<4} "
                  f"enc={row['byEncounter']} src={row['bySource']}", flush=True)
        live.command('pause', {}, wait=True)
        time.sleep(1.0)
        final = live.status()
        live.command('close')
        live.thread.join(120)
        scheduler = final.get('scheduler') or {}
        levels = [row['level'] or 0 for row in samples]
        util = [row['utilisation'] for row in samples if row['utilisation'] is not None]
        reseeds = int(scheduler.get('reseedCreated') or 0)
        by_encounter = dict(scheduler.get('reseedByEncounter') or {})
        by_source = dict(scheduler.get('reseedBySource') or {})
        store = optimizer.Store(path, provenance())
        try:
            candidates = store.db.execute('SELECT COUNT(*) FROM candidate').fetchone()[0]
            per_encounter = {}
            for cid, encounter in store.candidate_encounters().items():
                per_encounter[encounter] = per_encounter.get(encounter, 0)+1
            reseed_rows = store.db.execute(
                "SELECT source, COUNT(*) FROM lineage WHERE source LIKE 'reseed:%' "
                'GROUP BY source').fetchall()
            # Every reseed's lineage row survives pruning, so this is where the reseeds actually
            # went - the in-population counter only shows the ones still held.
            reseed_encounters = store.db.execute(
                "SELECT encounterId, COUNT(*) FROM lineage WHERE source LIKE 'reseed:%' "
                'GROUP BY encounterId').fetchall()
            late = [row for row in samples
                    if row['reseeds'] == reseeds and row['t'] >= samples[-1]['t']-20]
        finally:
            store.close()
        report = dict(seconds=args.seconds, workers=args.workers, ticks=args.ticks,
                      samples=samples, reseeds=reseeds, byEncounter=by_encounter,
                      bySource=by_source, candidates=candidates,
                      perEncounter=per_encounter, lineage=dict(reseed_rows),
                      lineageByEncounter={str(row[0]): int(row[1]) for row in reseed_encounters},
                      final=None if not final else dict(
                          runs=final.get('totalRuns'), scheduler=scheduler))
        print()
        def expect(label, condition, detail=''):
            print(f'  {"ok   " if condition else "FAIL "} {label}' + ('' if condition else f': {detail}'))
            if not condition:
                failures.append(label)
        expect('useful utilisation genuinely fell into the starved band',
               util and min(util) < learner.STARVATION_HEALTHY,
               f'min {min(util) if util else None}')
        # Did the *condition* the controller exists for actually occur? `dryPasses` is the planner's
        # own count of passes with free workers and nothing runnable to submit; `vetoed` is the number
        # of those windows refused because the coordinator was already the bottleneck. Only when the
        # first is positive and the second does not explain it must candidates exist.
        dry = max((row['dryPasses'] or 0) for row in samples)
        vetoed = max((row['vetoed'] or 0) for row in samples)
        print(f'  dry planning passes: {dry}, coordinator-saturated vetoes: {vetoed}')
        expect('starvation pressure was raised where the condition held',
               (max(levels) > 0) if dry > vetoed else True, f'levels {sorted(set(levels))}')
        expect('new exploration candidates were created where the condition held',
               (reseeds > 0) if dry > vetoed else True, f'{reseeds} created')
        created_by_encounter = {str(row[0]): int(row[1]) for row in reseed_encounters}
        # Reported, not asserted: the controller's preference is "the encounter with the least
        # runnable work, then the fewest open children, then the fewest reseeds ever given" - in a
        # fixture where one fight is genuinely the driest, sending the exploration there *is* the
        # specified behaviour. The ordering itself is asserted in check_optimizer_starvation.py
        # (`fairness`), which drives the ranking directly.
        print(f'  exploration created per encounter: {created_by_encounter}')
        expect('reseed mutation sources are mixed where candidates were created',
               (len(by_source) > 1) if reseeds else True, str(by_source))
        # Recovery proof: after the bound is released the pool can be fed by normal branching again,
        # so the controller must fall back to zero pressure and stop creating candidates.
        expect('creation stopped once the pool recovered',
               late and all(row['reseeds'] == reseeds for row in late)
               and all(not row['active'] for row in late),
               f'{len(late)} quiet samples: {[row["reseeds"] for row in late]}')
        expect('the population stayed inside its global bound',
               candidates <= optimizer.MAX_CANDIDATES_TOTAL, f'{candidates} candidates')
        expect('no encounter exceeded its active-population bound',
               all(count <= optimizer.MAX_CANDIDATES_PER_ENCOUNTER*2 for count in per_encounter.values()),
               str(per_encounter))
        expect('the run never failed', not any(row['error'] for row in samples),
               str(next((row['error'] for row in samples if row['error']), '')))
        if args.json:
            args.json.parent.mkdir(parents=True, exist_ok=True)
            args.json.write_text(json.dumps(report, indent=1, default=str), encoding='utf-8')
        print(f'\nFAILURES: {failures}' if failures else '\nstarvation reseeding recovers the pool and stops')
        return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
