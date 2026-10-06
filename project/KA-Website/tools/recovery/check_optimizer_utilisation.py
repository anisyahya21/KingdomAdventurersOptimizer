"""Why are the workers idle? (Part B)

Runs a real search on a fresh library with the real PyPy worker pool and reports the coordinator's
own scheduler counters, so utilisation is explained by measurements rather than guessed at:

    utilisation      = 1 - idleWorkerSeconds / (workers * wall)
    runnable work    = discovery / validation seeds the chooser could see when it last planned
    empty plans      = choose_plan() found nothing runnable and fell through to a proposal
    coordinator time = plan / proposal / record / publish seconds

    python check_optimizer_utilisation.py --workers 24 --seconds 45
    python check_optimizer_utilisation.py --library path.sqlite --label live-copy

Without `--library` the fixture is a fresh single-encounter library. With `--library` the named
library is *copied* first and the copy is what runs, so the same measurement can be taken on the
saturated 256-candidate shape the desktop is actually running without writing to the user's file.
Either way the working library is temporary and removed afterwards.
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

import strategy_optimizer as kernel  # noqa: E402
from strategy_optimizer import Optimizer, Store, scope  # noqa: E402
from strategy_optimizer_adapter import default_scenario, provenance, stats  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workers', type=int, default=24)
    parser.add_argument('--seconds', type=float, default=45)
    parser.add_argument('--sample-seconds', type=float, default=.25,
                        help='status polling interval; use 2 to match the desktop')
    parser.add_argument('--ticks', type=int, default=10000)
    parser.add_argument('--label', default='run')
    parser.add_argument('--library', type=Path,
                        help='copy this library first and measure the copy (saturated shape)')
    parser.add_argument('--naive-publish', action='store_true',
                        help='publish with the original full rebuild, for a same-fixture before/after')
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix='ka-utilisation-') as root:
        path = Path(root) / 'utilisation.sqlite'
        if args.library:
            # A byte copy: the live library's WAL sidecars may be locked while the desktop is
            # running, and only the main file is needed for a readable snapshot.
            shutil.copyfile(args.library, path)
            # The copy is a measurement fixture, not the user's library: it is stamped with the current
            # search-space version so the scheduler will actually start on it. The library it was copied
            # from is never touched, and its own version stays whatever it is.
            import search_contract
            store = Store(path, provenance())
            with store.db:
                store.set('searchSpaceVersion', search_contract.SEARCH_SPACE_VERSION)
            store.close()
        else:
            scenario = dict(default_scenario(), tickLimit=args.ticks)
            store = Store(path, provenance())
            with store.db:
                store.set('scope', scope(scenario))
                store.add(scenario, 'Utilisation fixture', 'supplied', stats(scenario))
            store.close()
        if args.naive_publish:
            # The pre-fix `_publish`: reread and reparse every candidate on every publish. Kept as
            # `full_candidate_payloads` precisely so before/after can be measured on one fixture
            # instead of comparing two different libraries under two different machine loads.
            Optimizer._published_candidates = (
                lambda self, store: kernel.full_candidate_payloads(store))
        optimizer = Optimizer(path)
        try:
            deadline = time.monotonic() + 60
            while optimizer.status()['state'] == 'Opening library' and time.monotonic() < deadline:
                time.sleep(.05)
            # The library-wide counter is persistent, so the fights-per-second reading is the delta
            # this window actually completed; a copy of a live library starts at tens of thousands.
            base_runs = optimizer.status().get('totalRuns') or 0
            optimizer.command('start', dict(workers=args.workers, duty=1), wait=True)
            started = time.monotonic()
            peak = 0
            busy_samples = []
            while time.monotonic() - started < args.seconds:
                status = optimizer.status()
                busy = (status.get('scheduler') or {}).get('busy', 0)
                peak = max(peak, busy)
                busy_samples.append(busy)
                time.sleep(args.sample_seconds)
            status = optimizer.status()
            wall = time.monotonic() - started
            optimizer.command('pause', {}, wait=True)
            time.sleep(1.0)
            status = optimizer.status()
        finally:
            optimizer.command('close')
            optimizer.thread.join(180)

    scheduler = status.get('scheduler') or {}
    rows = (status.get('totalRuns') or 0) - base_runs
    idle = scheduler.get('idleWorkerSeconds', 0.)
    utilisation = 1 - idle / max(1e-9, args.workers * wall)
    throughput = status.get('throughput') or {}
    # The busy histogram is the utilisation reading's shape: how many of the configured workers were
    # mid-battle at each 0.25 s sample, so a mean cannot hide a pool that alternates between full and
    # empty. It is reported next to the counters that explain the idle samples.
    histogram = {str(count): sum(1 for sample in busy_samples if sample == count)
                 for count in sorted(set(busy_samples))}
    order = sorted(busy_samples)
    report_histogram = dict(busyHistogram=histogram, busySamples=len(busy_samples),
                            busyMedian=order[len(order) // 2] if order else None)
    report = dict(label=args.label, workers=args.workers, seconds=round(wall, 1), ticks=args.ticks,
                  library=args.library.name if args.library else 'fresh fixture',
                  candidates=len(status.get('candidates') or []),
                  runs=rows, libraryRunsBefore=base_runs, battlesPerSecond=round(rows / wall, 2),
                  engineTimePerBattle=round(wall / rows, 4) if rows else None,
                  utilisation=round(utilisation, 4), peakBusy=peak,
                  **report_histogram,
                  idleWorkerSeconds=round(idle, 2), scheduler=scheduler,
                  measuredRate=throughput.get('battlesPerSecond'),
                  capacity=throughput.get('capacityBattlesPerSecond'))
    print(json.dumps(report, indent=1, default=str))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=1, default=str), encoding='utf-8')
    return 0


if __name__ == '__main__':
    sys.exit(main())
