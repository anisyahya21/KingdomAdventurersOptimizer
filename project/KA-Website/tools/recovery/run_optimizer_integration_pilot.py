"""One bounded, real integration pilot on an *isolated copy* of a live library.

Two stages, deliberately separate so a changed item policy can never silently confound a
speed/damage comparison:

  ``--stage mp``           ordinary runs carry MP telemetry -> a watched unit crosses `<=3%` of its own
                           maximum during a battle -> the optimiser creates an herb-supported child
                           under the TEST FIXTURE allowance -> the child's own bank is run by the real
                           scheduler -> its own results and the herb's own telemetry are reported.
  ``--stage breakthrough`` real evidence -> a machine-readable hypothesis -> legal intervention arms ->
                           real scheduler requests -> real native results -> a persisted decision.

The source library is opened read-only and copied through SQLite's own backup API, so the live
coordinator is never touched, no share is changed and no host is restarted. The pilot stops at the
budget it is given and reports what it actually completed.

    python run_optimizer_integration_pilot.py --stage mp --src A:/.../live.sqlite --budget 900
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sqlite3
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import strategy_optimizer as optimizer  # noqa: E402

#: TEST FIXTURE, not a production value: the user has not chosen a Holy Herb quantity, so the pilot
#: declares the smallest one that can prove the path end to end.
TEST_FIXTURE_ALLOWANCE = dict(enabled=True, stock=1, maxUses=1, bank=16, maxChildren=2)


def isolate(source, target):
    """A consistent copy through SQLite's backup API, including the source's WAL state."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()
    started = time.time()
    with sqlite3.connect(f'file:{source}?mode=ro', uri=True) as origin:
        with sqlite3.connect(str(target)) as copy:
            origin.backup(copy)
    return dict(target=str(target), seconds=round(time.time()-started, 2),
                bytes=target.stat().st_size)


def wait_open(engine, seconds=600):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if engine.status().get('state') != 'Opening library':
            return True
        time.sleep(.25)
    return False


def evidence_count(path):
    db = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    try:
        return int(db.execute('SELECT COUNT(*) FROM evidence').fetchone()[0])
    finally:
        db.close()


def run_stage(engine, *, workers, duty, budget, stop_when, seconds, poll=0.5):
    """Start the real search and stop it at the first stopping condition, the budget, or the deadline."""
    engine.command('start', dict(workers=workers, duty=duty), wait=True)
    started = time.time()
    baseline = int(engine.status().get('totalRuns') or 0)
    timeline = []
    stopped_for = None
    first_error = None
    while time.time() - started < seconds:
        # A short poll keeps the budget honest: the pool refills between polls, so a two-second poll
        # overshot a 400-battle budget by more than half on the fast fight.
        time.sleep(max(0.2, poll))
        status = engine.status()
        done = int(status.get('totalRuns') or 0) - baseline
        # A refused `start` is published as an error and leaves the host paused: without this the pilot
        # would simply report "zero battles" and hide the reason.
        if first_error is None and status.get('error'):
            first_error = str(status['error'])
            stopped_for = f'the coordinator reported: {first_error}'
            break
        timeline.append(dict(seconds=round(time.time()-started, 1), runs=done,
                             state=status.get('state'),
                             battlesPerSecond=(status.get('throughput') or {}).get('battlesPerSecond')))
        reason = stop_when(status, done)
        if reason:
            stopped_for = reason
            break
        if done >= budget:
            stopped_for = f'the pilot budget of {budget} newly executed battles is spent'
            break
    # A stop request can land while the coordinator is inside a long evidence rebuild; the command
    # queue is still drained, so a timed-out acknowledgement is not a failure. What matters is that
    # the run is stopped before the report is read.
    try:
        engine.command('stop', {}, wait=True)
    except Exception:  # noqa: BLE001 - the stop is queued even when its acknowledgement is late
        pass
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline and engine.status().get('state') in ('Running', 'Saving'):
        time.sleep(.5)
    final = engine.status()
    return dict(baseline=baseline, executed=int(final.get('totalRuns') or 0) - baseline,
                stoppedFor=stopped_for, error=first_error, seconds=round(time.time()-started, 1),
                secondsPerBattle=(final.get('throughput') or {}).get('secondsPerRun'),
                battlesPerSecond=(final.get('throughput') or {}).get('battlesPerSecond'),
                scheduler={
                    'evidenceSeconds': (final.get('scheduler') or {}).get('evidenceSeconds'),
                    'dispatchStalls': (final.get('scheduler') or {}).get('starvationActive'),
            }, timeline=timeline[-12:])


def herb_evidence(path, child, limit=6):
    """The child's own retained runs: the herb block the observer published, run by run."""
    db = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    try:
        rows = []
        for phase, ordinal, blob in db.execute(
                'SELECT phase,ordinal,result FROM run WHERE candidate=? ORDER BY ordinal DESC LIMIT ?',
                (child, int(limit))):
            result = json.loads(blob)
            herb = result.get('herbMetrics') or {}
            rows.append(dict(phase=phase, ordinal=int(ordinal), verdict=result.get('verdict'),
                             ticks=result.get('ticks'), useCount=int(herb.get('useCount') or 0),
                             startingStock=herb.get('startingStock'),
                             remainingStock=herb.get('remainingStock'),
                             uses=herb.get('uses') or [],
                             mp=result.get('mpMetrics') or []))
        return rows
    finally:
        db.close()


def mp_stage(engine, path, encounter, budget, seconds, workers=6):
    """Observation -> eligibility -> child -> its own bank, all through the real coordinator."""
    engine.command('mp_recovery', dict(TEST_FIXTURE_ALLOWANCE), wait=True)
    if encounter is not None:
        engine.command('focus_encounter', dict(encounterId=int(encounter)), wait=True)
    allowance = engine.status().get('mpRecovery', {}).get('setting')

    def stop_when(status, done):
        requests = (status.get('mpRecovery') or {}).get('requests') or []
        if not requests:
            return None
        request = requests[0]
        if int(request.get('bank') or 0) and int(request.get('observed') or 0) >= int(request['bank']):
            return 'the herb-supported child was measured over its whole declared bank'
        return None

    run = run_stage(engine, workers=workers, duty=1.0, budget=budget, stop_when=stop_when,
                    seconds=seconds)
    status = engine.status()
    payload = status.get('mpRecovery') or {}
    requests = payload.get('requests') or []
    report = dict(stage='mp', encounter=encounter, allowance=allowance, run=run,
                  mpRecovery=payload, executed=run['executed'])
    if requests:
        request = requests[0]
        report['request'] = request
        child = request.get('childId')
        report['childRuns'] = herb_evidence(path, child) if child else []
        report['herbUses'] = sum(int(row['useCount']) for row in report['childRuns'])
    return report


def breakthrough_stage(engine, path, encounter, budget, seconds, workers=3):
    """Real evidence -> hypothesis -> arms -> native runs -> a persisted next decision."""
    engine.command('students', dict(shares={'community': 0.05, 'rebel': 0.05, 'stumble': 0.05,
                                            'average': 0.0, 'mechanism': 0.6, 'discovery': 0.25}),
                   wait=True)
    if encounter is not None:
        engine.command('focus_encounter', dict(encounterId=int(encounter)), wait=True)

    def stop_when(status, done):
        breakthrough = status.get('breakthrough') or {}
        for entry in (breakthrough.get('encounters') or {}).values():
            plan = entry.get('plan') or {}
            if plan.get('status') in ('evaluated', 'scored', 'not_promoted', 'confirmed'):
                return f"the screening plan reached status {plan.get('status')!r}"
            if entry.get('status') in ('no_improvement_in_tested_region', 'promising_unconfirmed',
                                       'confirmed_improvement'):
                return f"the stream reached status {entry.get('status')!r}"
        return None

    run = run_stage(engine, workers=workers, duty=1.0, budget=budget, stop_when=stop_when,
                    seconds=seconds)
    status = engine.status()
    return dict(stage='breakthrough', encounter=encounter, run=run,
                breakthrough=status.get('breakthrough'), executed=run['executed'])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', choices=('mp', 'breakthrough'), required=True)
    parser.add_argument('--src', required=True)
    parser.add_argument('--work', default=str(HERE / '.tmp-integration'))
    parser.add_argument('--encounter', type=int)
    parser.add_argument('--budget', type=int, default=900)
    parser.add_argument('--seconds', type=float, default=900)
    parser.add_argument('--workers', type=int, default=0,
                        help='worker count for the pilot pool; 0 uses the stage default')
    parser.add_argument('--json')
    args = parser.parse_args(argv)
    work = pathlib.Path(args.work)
    target = work / f'{args.stage}-pilot.sqlite'
    copy = isolate(args.src, target)
    engine = optimizer.Optimizer(target)
    try:
        if not wait_open(engine):
            raise SystemExit('the isolated copy did not finish opening')
        if args.stage == 'mp':
            report = mp_stage(engine, target, args.encounter, args.budget, args.seconds,
                              workers=args.workers or 6)
        else:
            report = breakthrough_stage(engine, target, args.encounter, args.budget, args.seconds,
                                        workers=args.workers or 3)
    finally:
        try:
            engine.command('close', {}, wait=True)
        except Exception:  # noqa: BLE001 - the report matters more than a tidy shutdown
            pass
    report['copy'] = copy
    report['evidenceRows'] = evidence_count(target)
    text = json.dumps(report, indent=1, default=str)
    if args.json:
        pathlib.Path(args.json).write_text(text + '\n', encoding='utf-8')
    print(text)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
