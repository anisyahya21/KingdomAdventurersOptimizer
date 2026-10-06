"""Focused checks for the bounded encounter evaluator (development + frozen holdout jobs).

Run:  <venv>/python -B -X utf8 tools/recovery/check_encounter_evaluation.py
Deterministic, temporary in-memory ledger, no native battles (the pool is a stub). Prints PASS/FAIL.
"""
from concurrent.futures import Future
import sqlite3
import time

import strategy_experiment_store as ledger
import strategy_encounter_evaluation as evaluation


class Pool:
    def __init__(self, hang=False):
        self.calls = []
        self.hang = hang

    def submit(self, fn, scenario, seeds):
        future = Future()
        self.calls.append((future, scenario, seeds))
        if not self.hang:
            future.set_result({'seeds': list(seeds), 'verdict': 1, 'resultBackend': 'native',
                               'rewardOutcome': {'pendingChests': 5}})
        return future

    def shutdown(self, wait=True):
        pass

    def terminate_workers(self):
        pass


class TimedPool(Pool):
    """A stub worker that reports a fixed 10-second battle, for duty-cycle checks."""

    def submit(self, fn, scenario, seeds):
        future = Future()
        future.set_result({'seeds': list(seeds), 'verdict': 1, 'resultBackend': 'native',
                           'rewardOutcome': {'pendingChests': 5}, 'elapsedSeconds': 10.0})
        return future


class FakeClock:
    def __init__(self, start=1000.0):
        self.now = float(start)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += float(seconds)


def new_ledger():
    db = sqlite3.connect(':memory:')
    ledger.initialize(db)
    return db


def intent(purpose='improvement', budget=2):
    return {'scope': 'community', 'purpose': purpose, 'planned_budget': budget,
            'stopping': 'finite', 'ownerShare': 1, 'policy': {'finishPolicy': 'on-verdict'},
            'expectedMechanicalDifferences': {'test': 1}}


def check(name, condition, detail=''):
    print(('PASS ' if condition else 'FAIL ') + name + ((' :: ' + str(detail)) if detail else ''))
    return bool(condition)


def main():
    ok = True
    db = new_ledger()
    experiment = ledger.create_experiment(db, intent())
    assert ledger.experiment_intent(db, experiment) == intent()
    pool = Pool()
    runner = evaluation.Evaluator(workers=2, pool=pool)
    ok &= check('development-one-pair-one-job',
                runner.submit(db, experiment, 'candidate', {}, [1, 2])
                and not runner.submit(db, experiment, 'candidate', {}, [1, 2])
                and runner.submit(db, experiment, 'candidate', {}, [1, 3])
                and len(pool.calls) == 2)
    experiment_reads = []
    db.set_trace_callback(experiment_reads.append)
    complete = runner.harvest(db)
    db.set_trace_callback(None)
    ok &= check('harvest-completes-and-persists', len(complete) == 2)
    read_count = sum('select owner_share, planned_budget' in sql.lower()
                     and 'from ea_experiment where id=' in sql.lower()
                     for sql in experiment_reads)
    ok &= check('harvest-caches-immutable-experiment-metadata', read_count == 1, read_count)
    later_reads = []
    db.set_trace_callback(later_reads.append)
    ledger._experiment(db, experiment)
    db.set_trace_callback(None)
    ok &= check('harvest-cache-is-released',
                sum('select owner_share, planned_budget' in sql.lower()
                    and 'from ea_experiment where id=' in sql.lower()
                    for sql in later_reads) == 1)
    ok &= check('no-duplicate-after-completion',
                not runner.submit(db, experiment, 'candidate', {}, [1, 2]))
    summary = ledger.summary(db)
    ok &= check('budget-conserved', summary['totalCompleted'] == 2 and summary['conserved'])
    ok &= check('outcomes-are-canonical',
                all(row['outcome']['finalEarned'] == 5 for row in ledger.outcomes(db, experiment)))
    runner.close()

    # Frozen holdout: not a development reservation; recorded only through the holdout store.
    db2 = new_ledger()
    config = {'version': 1, 'mode': 'community-first',
              'allocations': {'community': 1.0, 'discovery': 0.0, 'rebel': 0.0, 'stumble': 0.0,
                              'average': 0.0, 'mechanism': 0.0},
              'purposes': {'improvement': 1, 'boundary': 0, 'support': 0, 'comparison': 4,
                           'exploration': 0}}
    session = ledger.configure_session(db2, config)
    comparison = ledger.create_experiment(db2, dict(intent('comparison', 4), sessionId=session,
                                                    owner='community'))
    plan = [dict(candidateId='nominee', seeds=[[7, 8], [9, 10]]),
            dict(candidateId='reference', seeds=[[7, 8], [9, 10]])]
    ledger.freeze_confirmation(db2, comparison, 'nominee', 'reference',
                               {'finishPolicy': 'on-verdict'}, 'finalEarned', plan)
    holdout = evaluation.Evaluator(workers=4, pool=Pool())
    ok &= check('holdout-dispatch-only-frozen-pairs',
                holdout.submit_holdout(db2, comparison, 'nominee', {}, (7, 8))
                and not holdout.submit_holdout(db2, comparison, 'nominee', {}, (11, 12))
                and not holdout.submit_holdout(db2, comparison, 'unknown', {}, (7, 8)))
    holdout.harvest(db2)
    for candidate in ('nominee', 'reference'):
        for pair in ((7, 8), (9, 10)):
            holdout.submit_holdout(db2, comparison, candidate, {}, pair)
    holdout.harvest(db2)
    report = ledger.confirmation_report(db2, comparison)
    ok &= check('confirmation-ready-only-when-complete', report['ready'] is True
                and report['runsCompleted'] == 4)
    ok &= check('holdout-is-not-development',
                not ledger.development_reservations(db2, comparison))
    holdout.close()

    # Bounded timeout: a hung job is recorded as an error outcome, not dropped or zeroed.
    db3 = new_ledger()
    hung_experiment = ledger.create_experiment(db3, intent('improvement', 1))
    hung = evaluation.Evaluator(workers=1, pool=Pool(hang=True), timeout=0.05)
    hung.submit(db3, hung_experiment, 'candidate', {}, [3, 4])
    time.sleep(0.1)
    harvested = hung.harvest(db3)
    ok &= check('timeout-records-error-outcome',
                len(harvested) == 1 and harvested[0]['timedOut'] is True
                and hung.status()['timeouts'] == 1)
    ok &= check('timeout-outcome-blocks-a-claim',
                not ledger.outcomes(db3)[0]['outcome']['resolved'])
    hung.close()

    # Missing telemetry is an absent reading, never a zero.
    db4 = new_ledger()
    plain = ledger.create_experiment(db4, intent('improvement', 1))
    telemetry = evaluation.Evaluator(workers=1, pool=Pool())
    telemetry.submit(db4, plain, 'candidate', {}, [5, 6])
    telemetry.harvest(db4)
    ok &= check('missing-telemetry-is-absent-not-zero',
                telemetry.status()['telemetryMissing'] == 1
                and telemetry.status()['workerCpuReadings'] == 0)
    telemetry.close()

    # Per-worker duty gating: a completed battle of `elapsed` seconds holds that worker for
    # `elapsed*(1/duty-1)` seconds, and a blocked submit must not reserve (never double-charge).
    db5 = new_ledger()
    duty_experiment = ledger.create_experiment(db5, intent('improvement', 4))
    clock = FakeClock()
    gated = evaluation.Evaluator(workers=1, pool=TimedPool(), duty=0.5, clock=clock)
    ok &= check('duty-hold-math', abs(gated.duty_hold(10.0) - 10.0) < 1e-9, gated.duty_hold(10.0))
    ok &= check('duty-dispatches-first-job', gated.submit(db5, duty_experiment, 'candidate', {}, [1, 2]))
    gated.harvest(db5)
    ok &= check('duty-hold-blocks-next-job',
                not gated.submit(db5, duty_experiment, 'candidate', {}, [1, 3]))
    clock.advance(9.9)
    ok &= check('duty-hold-blocks-until-expiry',
                not gated.submit(db5, duty_experiment, 'candidate', {}, [1, 3]))
    ok &= check('duty-blocked-submit-did-not-reserve',
                len(ledger.development_reservations(db5, duty_experiment)) == 1)
    clock.advance(0.2)
    ok &= check('duty-releases-after-hold',
                gated.submit(db5, duty_experiment, 'candidate', {}, [1, 3]))
    gated.harvest(db5)
    ok &= check('duty-configure-clamps', gated.configure_duty(0.0) == 0.05)
    gated.close()

    for connection in (db, db2, db3, db4, db5):
        connection.close()
    print('ALL PASS' if ok else 'FAILURES PRESENT')
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
