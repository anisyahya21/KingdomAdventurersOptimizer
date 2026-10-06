"""Active-session accounting: elapsed time, session run count, and the worker ceiling.

This drives the real `Optimizer` coordinator (the object the desktop bridge exposes) through
Start -> Pause -> Resume -> Stop -> Start and asserts the published status. It needs no browser, so
the semantics are checked deterministically:

  * Start resets elapsed time to zero and begins counting;
  * Pause stops accumulating, and polling does not move the frozen reading;
  * Resume continues from the accumulated value instead of restarting;
  * Stop freezes the value until the next Start;
  * a new Start resets elapsed time and the session run count;
  * the session run count is a delta, so it is strictly below the library-wide count;
  * a Start at 16 and at 24 workers launches exactly that many when the machine allows it.

    pypy3.exe check_strategy_optimizer_session.py
"""
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from strategy_optimizer import Optimizer, Store, scope  # noqa: E402
from strategy_optimizer_adapter import default_scenario, provenance, stats  # noqa: E402
from strategy_optimizer_limits import clamp_workers, default_workers, worker_ceiling  # noqa: E402

#: Slower than the poll interval, so "paused adds nothing" is unambiguous rather than a rounding
#: artefact. The whole check still finishes in a few seconds.
SETTLE_SECONDS = 1.3


def wait(predicate, label, seconds=120):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(.05)
    raise AssertionError(f'timed out: {label}')


def sample(optimizer, seconds, step=.25):
    """Poll `status()` for `seconds` and return the elapsed readings, as the desktop would."""
    readings = []
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        readings.append(optimizer.status()['sessionElapsedSeconds'])
        time.sleep(step)
    return readings


def check_session(failures):
    with tempfile.TemporaryDirectory(prefix='ka-session-check-') as root:
        path = Path(root) / 'session.sqlite'
        scenario = dict(default_scenario(), tickLimit=40, mathSeed=11, libSeed=12)
        store = Store(path, provenance())
        with store.db:
            store.set('scope', scope(scenario))
            store.add(scenario, 'Session accounting check', 'supplied', stats(scenario))
        store.close()
        optimizer = Optimizer(path)
        try:
            wait(lambda: optimizer.status()['state'] != 'Opening library', 'library opened')
            idle = optimizer.status()
            if idle['sessionElapsedSeconds'] != 0:
                failures.append(f'before any Start, elapsed should be 0, got '
                                f"{idle['sessionElapsedSeconds']}")
            if idle.get('sessionRuns') is not None:
                failures.append(f'before any Start, sessionRuns should be null, got '
                                f"{idle.get('sessionRuns')}")

            # ---- Start: reset and count ----
            optimizer.command('start', dict(workers=1, duty=1), wait=True)
            wait(lambda: optimizer.status()['state'] == 'Running', 'running')
            wait(lambda: (optimizer.status()['totalRuns'] or 0) >= 1, 'first run recorded')
            running = sample(optimizer, SETTLE_SECONDS)
            if running[-1] - running[0] < SETTLE_SECONDS * .5:
                failures.append(f'running clock did not advance: {running[0]:.2f} -> '
                                f'{running[-1]:.2f}')

            # ---- Pause: stop accumulating, and do not drift while polled ----
            optimizer.command('pause', {}, wait=True)
            wait(lambda: optimizer.status()['state'] == 'Paused', 'paused')
            paused = sample(optimizer, SETTLE_SECONDS)
            if max(paused) - min(paused) > .2:
                failures.append(f'paused reading drifted: {min(paused):.2f} .. {max(paused):.2f}')
            frozen = paused[-1]

            # ---- Resume: continue from the frozen value ----
            optimizer.command('start', dict(workers=1, duty=1), wait=True)
            wait(lambda: optimizer.status()['state'] == 'Running', 'resumed')
            resumed = sample(optimizer, SETTLE_SECONDS)
            if resumed[-1] <= frozen + SETTLE_SECONDS * .5:
                failures.append(f'resume did not continue from {frozen:.2f}: '
                                f'{resumed[0]:.2f} -> {resumed[-1]:.2f}')
            if resumed[0] < frozen - .2:
                failures.append(f'resume restarted instead of continuing: {resumed[0]:.2f} '
                                f'< {frozen:.2f}')

            # ---- Stop: freeze until the next Start ----
            optimizer.command('stop', {}, wait=True)
            wait(lambda: optimizer.status()['state'] != 'Running', 'stopped')
            stopped = sample(optimizer, SETTLE_SECONDS)
            if max(stopped) - min(stopped) > .2:
                failures.append(f'stopped reading drifted: {min(stopped):.2f} .. {max(stopped):.2f}')
            if stopped[-1] + .2 < resumed[-1]:
                failures.append(f'stop lost accumulated time: {stopped[-1]:.2f} < {resumed[-1]:.2f}')
            held = optimizer.status()
            library_runs = held['totalRuns'] or 0
            session_runs = held.get('sessionRuns')
            if session_runs is None:
                failures.append('sessionRuns missing after a real session')
            elif session_runs > library_runs:
                failures.append(f'sessionRuns {session_runs} exceeds library runs {library_runs}')

            # ---- A new Start resets both readings ----
            optimizer.command('start', dict(workers=1, duty=1), wait=True)
            fresh = optimizer.status()
            if fresh['sessionElapsedSeconds'] > .5:
                failures.append(f'new Start did not reset elapsed: '
                                f"{fresh['sessionElapsedSeconds']:.2f} (held {frozen:.2f})")
            if (fresh.get('sessionRuns') or 0) > 1:
                failures.append(f'new Start did not reset the session count: '
                                f"{fresh.get('sessionRuns')} of {fresh['totalRuns']} library runs")
            wait(lambda: (optimizer.status()['totalRuns'] or 0) > library_runs,
                 'first run of the new session')
            after = optimizer.status()
            expected = after['totalRuns'] - library_runs
            if after['sessionRuns'] != expected:
                failures.append(f'sessionRuns {after["sessionRuns"]} != runs since Start '
                                f'{expected}')
            if after['sessionRuns'] >= after['totalRuns']:
                failures.append('sessionRuns is not a delta against the library count')
            optimizer.command('stop', {}, wait=True)
            wait(lambda: optimizer.status()['state'] != 'Running', 'stopped again')
        finally:
            optimizer.command('close')
            optimizer.thread.join(180)
    return failures


def check_workers(failures):
    """A Start at 16 and at 24 workers must launch exactly that many, when the machine allows."""
    import strategy_optimizer_fast as fast

    # The dropdown is filtered by the same ceiling, so the ceiling must react to the machine: a
    # 12-logical-CPU box allows 8 and is never offered 16 or 24.
    for cpus, expected in ((8, 4), (12, 8), (20, 16), (28, 24), (64, 24)):
        actual = worker_ceiling(cpus)
        if actual != expected:
            failures.append(f'worker_ceiling({cpus} CPUs) = {actual}, expected {expected}')
    if clamp_workers(24, 8) != 4:
        failures.append(f'clamp_workers(24, 8 CPUs) = {clamp_workers(24, 8)}, expected 4')
    ceiling = worker_ceiling()
    print(f'  worker ceiling on this machine: {ceiling} (default {default_workers()})')
    totals = []
    for count in (1, 2, 4, 8, 12, 16, 24):
        if count > ceiling:
            print(f'  {count} workers: above this machine\'s ceiling {ceiling}, skipped')
            continue
        with tempfile.TemporaryDirectory(prefix='ka-workers-check-') as root:
            path = Path(root) / 'workers.sqlite'
            scenario = dict(default_scenario(), tickLimit=40, mathSeed=21 + count, libSeed=22 + count)
            store = Store(path, provenance())
            with store.db:
                store.set('scope', scope(scenario))
                store.add(scenario, 'Worker count check', 'supplied', stats(scenario))
            store.close()
            optimizer = Optimizer(path)
            try:
                wait(lambda: optimizer.status()['state'] != 'Opening library', 'library opened')
                # The pool launches one persistent interpreter per worker through
                # `subprocess.Popen`; counting the spawns is direct evidence of how many processes
                # a Start actually created, rather than trusting the number the host reports back.
                spawned = []
                original = fast.subprocess.Popen
                fast.subprocess.Popen = lambda *a, **k: spawned.append(original(*a, **k)) or spawned[-1]
                try:
                    optimizer.command('start', dict(workers=count, duty=1), wait=True)
                finally:
                    fast.subprocess.Popen = original
                wait(lambda: optimizer.status()['state'] == 'Running', 'running')
                status = wait(lambda: (optimizer.status()['totalRuns'] or 0) >= 1
                              and optimizer.status(), 'first run recorded')
                reported = status['throughput']['workers']
                if reported != count:
                    failures.append(f'Start at {count} workers reported {reported}')
                live = sum(1 for process in spawned if process.poll() is None)
                if len(spawned) != count or live != count:
                    failures.append(f'Start at {count} workers spawned {len(spawned)} '
                                    f'({live} still alive)')
                optimizer.command('stop', {}, wait=True)
                wait(lambda: optimizer.status()['state'] != 'Running', 'stopped')
                totals.append((count, reported, live))
            finally:
                optimizer.command('close')
                optimizer.thread.join(120)
    print('  worker selections: ' +
          ', '.join(f'{count}->{reported} (launched {live})' for count, reported, live in totals))
    return failures


def main():
    failures = []
    print('session accounting:')
    check_session(failures)
    print('worker selections:')
    check_workers(failures)
    print('default horizon:')
    check_default_horizon(failures)
    if failures:
        print('FAILURES:')
        for row in failures:
            print('  ' + row)
        return 1
    print('  elapsed time resets on Start, holds through Pause/Stop, resumes without restarting, '
          'and the session run count is a delta against the library count')
    return 0


def check_default_horizon(failures):
    """A brand-new library is scoped to the production safety limit, and ships the card payload.

    This is the Part A wiring check: bulk discovery and validation run whatever the candidate's
    scenario says, so the one place the horizon has to be right is the baseline the coordinator
    creates for an empty library.
    """
    import strategy_search

    with tempfile.TemporaryDirectory(prefix='ka-horizon-check-') as root:
        optimizer = Optimizer(Path(root) / 'horizon.sqlite')
        try:
            wait(lambda: optimizer.status()['state'] != 'Opening library', 'library opened')
            status = optimizer.status()
            horizon = status.get('horizonTicks')
            expected = strategy_search.DEFAULT_TICK_LIMIT
            if horizon != expected:
                failures.append(f'new library horizon {horizon} != production {expected}')
            limits = sorted({int(c['scenario'].get('tickLimit')) for c in status['candidates']})
            if limits != [expected]:
                failures.append(f'new library candidates use {limits}, expected [{expected}]')
            campaigns = status.get('campaigns') or []
            if len(campaigns) != 5 or any(len(c['difficulties']) != 4 for c in campaigns):
                failures.append(f'encounter cards are not 5x4: '
                                f'{[(c["title"], len(c["difficulties"])) for c in campaigns]}')
            if status.get('encounterStats') is None:
                failures.append('the per-encounter aggregates are missing from the status')
            print(f'  new library horizon {horizon} ticks '
                  f'({horizon // 20 // 60}:{horizon // 20 % 60:02d}), '
                  f'{len(campaigns)} encounter cards x 4 difficulties')
        finally:
            optimizer.command('close')
            optimizer.thread.join(120)
    return failures


if __name__ == '__main__':
    sys.exit(main())
