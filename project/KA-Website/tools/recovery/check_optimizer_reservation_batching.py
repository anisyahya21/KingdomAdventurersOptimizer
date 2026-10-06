"""Deterministic reservation-batch correctness checks using temporary SQLite ledgers only."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import strategy_encounter_search as search
import strategy_encounter_evaluation as evaluation
import strategy_encounter_ready_queue as ready_queue
import strategy_experiment_store as ledger
import strategy_optimizer_fast as optimizer_fast


def _intent(scope, budget):
    return dict(scope=scope, purpose='improvement', planned_budget=budget,
                stopping={'maxRuns': budget}, owner='community', ownerShare=1.0,
                policy={'finishPolicy': 'on-verdict'}, mechanicsRevision='mechanics-v1',
                encounterRevision='encounter-v1', measurementWindow='development',
                fixedFields={'enemy': 'fixture'}, changedFields={'atk': 1})


def _db(path):
    db = sqlite3.connect(path)
    ledger.initialize(db)
    db.commit()
    return db


def _coordinator(evaluator=None):
    coordinator = object.__new__(search.Coordinator)
    coordinator.evaluator = evaluator or type('IdleEvaluator', (), {'pending': {}})()
    coordinator._budget_dirty = False
    return coordinator


def _ready(candidate, pairs):
    scenario = {'candidate': candidate, 'fixture': True}
    return [(candidate, pair, scenario) for pair in pairs]


def _reserved_count(db, experiment_id):
    return int(db.execute('SELECT reserved FROM ea_experiment_budget WHERE experiment_id=?',
                          (experiment_id,)).fetchone()[0])


def main():
    checks = {}
    with tempfile.TemporaryDirectory(prefix='ka-reservation-batch-') as temp:
        root = Path(temp)

        # A full 64-row bank is committed once, visible to another SQLite connection, and keeps
        # the exact input order. This is a transaction ceiling; the real coordinator further limits
        # the bank to free slots and finite ready jobs.
        path = root / 'full-bank.sqlite'
        db = _db(path)
        experiment = ledger.create_experiment(db, _intent('full-bank', 64))
        pairs = [(index, index + 1000) for index in range(64)]
        coordinator = _coordinator()
        rows = coordinator._reserve_dispatch_batch(
            db, experiment, _ready('candidate', pairs), capacity=128)
        reader = sqlite3.connect(path)
        visible = [(int(row[0]), int(row[1])) for row in reader.execute(
            'SELECT seed_a,seed_b FROM ea_sample_link WHERE experiment_id=? ORDER BY rowid',
            (experiment,))]
        assert len(rows) == len(visible) == 64
        assert visible == pairs
        assert not db.in_transaction and _reserved_count(reader, experiment) == 64
        assert coordinator._budget_dirty is True
        checks['64SeedCommitAndCrossConnectionVisibility'] = len(rows)
        reader.close()
        db.close()

        # The 64 limit counts reserve calls, even when early ordered pairs are denied.
        db = _db(root / 'reserve-call-cap.sqlite')
        experiment = ledger.create_experiment(db, _intent('reserve-call-cap', 100))
        pairs = [(500 + index, 1500 + index) for index in range(70)]
        db.executemany('INSERT INTO ea_holdout(experiment_id,candidate_id,seed_a,seed_b) '
                       'VALUES(?,?,?,?)',
                       [(experiment, 'candidate', *pair) for pair in pairs])
        db.commit()
        calls = 0
        original_reserve = ledger.reserve

        def count_reserve(*args):
            nonlocal calls
            calls += 1
            return original_reserve(*args)

        coordinator = _coordinator()
        with patch.object(search.ledger, 'reserve', count_reserve):
            assert coordinator._reserve_dispatch_batch(
                db, experiment, _ready('candidate', pairs), capacity=70) == []
        assert calls == 64 and _reserved_count(db, experiment) == 0
        checks['MaximumReserveCallsPerTransaction'] = calls
        db.close()

        # Smaller purpose grants remain partial and ordered; later pairs cannot exceed the
        # immutable experiment budget.
        db = _db(root / 'partial-budget.sqlite')
        experiment = ledger.create_experiment(db, _intent('partial-budget', 2))
        pairs = [(10, 110), (20, 120), (30, 130)]
        coordinator = _coordinator()
        rows = coordinator._reserve_dispatch_batch(
            db, experiment, _ready('candidate', pairs), capacity=3)
        assert [tuple(entry['seedPair']) for entry, _ in rows] == pairs[:2]
        assert _reserved_count(db, experiment) == 2
        checks['PartialBudgetAdmissionPreservesOrder'] = 2
        db.close()

        # An injected mid-bank failure rolls back prior ledger writes and does not dirty coordinator
        # counters. The caller-owned transaction remains intact when the guard rejects it.
        db = _db(root / 'rollback.sqlite')
        experiment = ledger.create_experiment(db, _intent('rollback', 3))
        coordinator = _coordinator()
        original_reserve = ledger.reserve
        calls = 0

        def fail_second(db_arg, exp_arg, candidate_arg, pair_arg):
            nonlocal calls
            calls += 1
            result = original_reserve(db_arg, exp_arg, candidate_arg, pair_arg)
            if calls == 2:
                raise RuntimeError('injected reservation failure')
            return result

        try:
            with patch.object(search.ledger, 'reserve', fail_second):
                coordinator._reserve_dispatch_batch(
                    db, experiment, _ready('candidate', [(1, 2), (3, 4)]), capacity=2)
            raise AssertionError('injected failure did not escape')
        except RuntimeError as exc:
            assert 'injected reservation failure' in str(exc)
        assert not db.in_transaction and _reserved_count(db, experiment) == 0
        assert db.execute('SELECT COUNT(*) FROM ea_sample_link WHERE experiment_id=?',
                          (experiment,)).fetchone()[0] == 0
        assert coordinator._budget_dirty is False

        db.execute('BEGIN')
        calls_before = calls
        try:
            coordinator._reserve_dispatch_batch(
                db, experiment, _ready('candidate', [(5, 6)]), capacity=1)
            raise AssertionError('caller transaction was not rejected')
        except RuntimeError as exc:
            assert 'caller-owned SQLite transaction' in str(exc)
        assert calls == calls_before and db.in_transaction
        db.rollback()
        assert _reserved_count(db, experiment) == 0
        checks['AtomicRollbackAndCallerTransactionGuard'] = True
        db.close()

        # Setup/checkpoint writes are committed at their point of ownership, so a transaction the
        # caller opens later on the same connection cannot be mistaken for stale coordinator work.
        db = sqlite3.connect(root / 'transaction-ownership.sqlite')
        coordinator = search.Coordinator(path='fixture', revision='fixture')
        coordinator._ensure_schema(db)
        assert not db.in_transaction
        db.commit()  # a caller's explicit commit must not leave an ownership marker behind
        db.execute('BEGIN')
        db.execute('INSERT INTO ea_meta(key,value) VALUES(?,?)', ('caller-write', 'pending'))
        try:
            coordinator._prepare_durable_dispatch(db)
            raise AssertionError('new caller transaction was mistaken for coordinator-owned work')
        except RuntimeError as exc:
            assert 'caller-owned SQLite transaction' in str(exc)
        assert db.in_transaction
        db.rollback()

        checkpoint_db = _db(root / 'checkpoint-ownership.sqlite')
        checkpoint_db.execute('CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
        checkpoint_db.commit()

        class CheckpointStore:
            def __init__(self, connection):
                self.db = connection

            def set(self, key, value):
                self.db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',
                                (key, json.dumps(value, sort_keys=True)))

        checkpoint = search.Coordinator(path='fixture', revision='fixture')
        checkpoint.report = lambda: {}
        checkpoint._persist(CheckpointStore(checkpoint_db))
        assert not checkpoint_db.in_transaction
        checkpoint_db.execute('BEGIN')
        checkpoint_db.execute('INSERT INTO meta VALUES (?,?)', ('caller-write', 'pending'))
        try:
            checkpoint._prepare_durable_dispatch(checkpoint_db)
            raise AssertionError('checkpoint ownership leaked into a later caller transaction')
        except RuntimeError as exc:
            assert 'caller-owned SQLite transaction' in str(exc)
        assert checkpoint_db.in_transaction
        checkpoint_db.rollback()
        checks['SetupAndCheckpointDoNotClaimLaterCallerTransactions'] = True
        checkpoint_db.close()
        db.close()

        # Crash after commit and after one of two pool submissions: canonical recovery sees one row
        # per sample, keeps the charge owner, and an idempotent reserve does not charge twice.
        path = root / 'recovery.sqlite'
        db = _db(path)
        experiment = ledger.create_experiment(db, _intent('recovery-owner', 2))
        pairs = [(51, 61), (52, 62)]
        coordinator = _coordinator()
        rows = coordinator._reserve_dispatch_batch(
            db, experiment, _ready('candidate', pairs), capacity=2)
        assert len(rows) == 2 and not db.in_transaction
        # Simulate one accepted submission, then a process boundary before either result is saved.
        class PartialDispatch:
            def __init__(self):
                self.pending = {}

            def submit_recovered(self, db_arg, entry, scenario):
                assert not db_arg.in_transaction
                key = (entry['chargedExperimentId'], entry['candidateId'],
                       tuple(entry['seedPair']))
                if key in self.pending:
                    return False
                self.pending[key] = {'scenario': scenario}
                return True

        partial = PartialDispatch()
        assert partial.submit_recovered(db, *rows[0]) is True
        assert len(partial.pending) == 1
        db.close()
        db = sqlite3.connect(path)
        recovered = ledger.recover(db)['outstanding']
        assert {tuple(row['seedPair']) for row in recovered} == set(pairs)
        assert all(row['chargedExperimentId'] == experiment for row in recovered)
        assert ledger.reserve(db, experiment, 'candidate', pairs[0]) is True
        assert _reserved_count(db, experiment) == 2
        checks['CommitBeforeSubmitRecoveryAndChargeOnce'] = len(recovered)
        db.close()

        # Real subprocess exits exercise SQLite's recovery at both transaction boundaries: an
        # uncommitted SAVEPOINT bank vanishes, while the committed bank survives an exit before
        # the first worker submit and is available to canonical recovery.
        crash_path = root / 'process-crash.sqlite'
        db = _db(crash_path)
        crash_experiment = ledger.create_experiment(db, _intent('process-crash', 2))
        db.close()
        recovery_dir = str(Path(__file__).resolve().parent)
        before_commit = (
            'import os,sqlite3,sys; '
            'sys.path.insert(0,sys.argv[3]); '
            'import strategy_experiment_store as ledger; '
            'db=sqlite3.connect(sys.argv[1]); db.execute("BEGIN"); '
            'ledger.reserve(db,int(sys.argv[2]),"candidate",[111,211]); os._exit(0)')
        subprocess.run([sys.executable, '-c', before_commit, str(crash_path),
                        str(crash_experiment), recovery_dir], check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        db = sqlite3.connect(crash_path)
        assert ledger.recover(db)['outstandingCount'] == 0
        assert _reserved_count(db, crash_experiment) == 0
        db.close()
        after_commit = (
            'import os,sqlite3,sys; '
            'sys.path.insert(0,sys.argv[3]); '
            'import strategy_encounter_search as search; '
            'db=sqlite3.connect(sys.argv[1]); '
            'c=object.__new__(search.Coordinator); '
            'c.evaluator=type("E",(),{"pending":{}})(); c._budget_dirty=False; '
            'ready=[("candidate",(121,221),{}),("candidate",(122,222),{})]; '
            'c._reserve_dispatch_batch(db,int(sys.argv[2]),ready,capacity=2); os._exit(0)')
        subprocess.run([sys.executable, '-c', after_commit, str(crash_path),
                        str(crash_experiment), recovery_dir], check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        db = sqlite3.connect(crash_path)
        recovered = ledger.recover(db)['outstanding']
        assert {tuple(row['seedPair']) for row in recovered} == {(121, 221), (122, 222)}
        assert all(row['chargedExperimentId'] == crash_experiment for row in recovered)
        assert _reserved_count(db, crash_experiment) == 2
        checks['SubprocessExitBeforeAndAfterCommit'] = {'beforeCommit': 0, 'afterCommit': 2}
        db.close()

        # Identical work requested under another experiment never steals the canonical charge owner.
        # Pending work stays with its original owner; completed work becomes evidence without a job.
        db = _db(root / 'coalesced.sqlite')
        owner = ledger.create_experiment(db, _intent('coalesced-owner', 2))
        follower = ledger.create_experiment(db, _intent('coalesced-follower', 2))
        pair = (71, 81)
        owner_rows = _coordinator()._reserve_dispatch_batch(
            db, owner, _ready('candidate', [pair]), capacity=1)
        assert len(owner_rows) == 1
        follower_coordinator = _coordinator()
        follower_rows = follower_coordinator._reserve_dispatch_batch(
            db, follower, _ready('candidate', [pair]), capacity=1)
        assert follower_rows == [] and follower_coordinator._budget_dirty is True
        recovery = ledger.recover(db)['outstanding']
        assert len(recovery) == 1 and recovery[0]['chargedExperimentId'] == owner
        assert db.execute('SELECT charged_experiment_id,reused FROM ea_sample_link '
                          'WHERE experiment_id=?', (follower,)).fetchone() == (owner, 1)

        second_follower = ledger.create_experiment(db, _intent('completed-follower', 2))
        assert ledger.complete(db, owner, 'candidate', pair, {
            'seeds': list(pair), 'verdict': 1, 'finishPolicy': 'on-verdict',
            'rewardOutcome': {'awardedChests': 1, 'awardedBasis': 'fixture'},
        }) is True
        completed_coordinator = _coordinator()
        completed_rows = completed_coordinator._reserve_dispatch_batch(
            db, second_follower, _ready('candidate', [pair]), capacity=1)
        assert completed_rows == [] and completed_coordinator._budget_dirty is True
        assert db.execute('SELECT COUNT(*) FROM ea_sample WHERE candidate_id=?',
                          ('candidate',)).fetchone()[0] == 1
        assert db.execute('SELECT charged_experiment_id,reused FROM ea_sample_link '
                          'WHERE experiment_id=?', (second_follower,)).fetchone() == (owner, 1)
        checks['CoalescedPendingAndCompletedChargeOwner'] = True
        db.close()

        # A frozen holdout pair remains disjoint from development reservation; the following
        # authorized pairs retain their relative order and budget is used only for development.
        db = _db(root / 'holdout.sqlite')
        # The frozen holdout already consumes one planned run, leaving two development admissions.
        experiment = ledger.create_experiment(db, _intent('holdout-disjoint', 3))
        db.execute('INSERT INTO ea_holdout(experiment_id,candidate_id,seed_a,seed_b) '
                   'VALUES(?,?,?,?)', (experiment, 'candidate', 91, 101))
        db.commit()
        pairs = [(91, 101), (92, 102), (93, 103)]
        rows = _coordinator()._reserve_dispatch_batch(
            db, experiment, _ready('candidate', pairs), capacity=2)
        assert [tuple(entry['seedPair']) for entry, _ in rows] == pairs[1:]
        assert db.execute('SELECT COUNT(*) FROM ea_sample_link WHERE experiment_id=?',
                          (experiment,)).fetchone()[0] == 2
        checks['HoldoutDisjointAndSeedOrder'] = 2
        db.close()

        # Exercise the actual coordinator branch with only currently free capacity. Its
        # submit_recovered seam opens a second connection to prove each reservation was committed
        # before submission; the third authorized job is left unreserved for a later pass.
        path = root / 'dispatch.sqlite'
        db = _db(path)
        experiment = ledger.create_experiment(db, _intent('dispatch-capacity', 4))

        class CommitCheckingPool:
            def __init__(self):
                self.visible = []

            def submit(self, _function, scenario, pair):
                other = sqlite3.connect(path)
                row = other.execute(
                    'SELECT result FROM ea_sample WHERE candidate_id=? AND seed_a=? AND seed_b=?',
                    ('candidate', pair[0], pair[1])).fetchone()
                assert row is not None
                other.close()
                self.visible.append(tuple(pair))
                return Future()

            def terminate_workers(self):
                pass

            def shutdown(self, wait=True):
                pass

        pool = CommitCheckingPool()
        with patch.dict(os.environ, {optimizer_fast.TRACE_ENV: ''}):
            evaluator = evaluation.Evaluator(workers=2, pool=pool, telemetry=False,
                                             require_native=False)
        coordinator = search.Coordinator(path=str(path), revision='fixture', workers=2,
                                          telemetry=False)
        coordinator.enabled = True
        coordinator.session_id = 1
        coordinator.evaluator = evaluator
        coordinator._current_compatible = lambda current: (True, None)
        coordinator._runnable_kwargs = lambda: {}
        current = {
            'experimentId': experiment, 'candidateId': 'candidate',
            'observedCandidate': {'candidate': 'candidate'},
            'observedReference': None,
            'jobs': [{'candidateId': 'candidate', 'seedPair': [101 + n, 201 + n]}
                     for n in range(3)],
        }
        coordinator.state['cohort'] = [current]
        assert coordinator.dispatch_authorized_ready(
            type('StoreView', (), {'db': db})()) == 2
        assert pool.visible == [(101, 201), (102, 202)]
        assert db.execute('SELECT COUNT(*) FROM ea_sample_link WHERE experiment_id=?',
                          (experiment,)).fetchone()[0] == 2
        assert coordinator._submission_started == 2 and coordinator._budget_dirty is True
        checks['RealCoordinatorUsesCommittedFreeSlotBatch'] = 2
        evaluator.close()
        db.close()

        # Duty-held and closed evaluator slots must retain the old submit-before-reserve gate.
        for name, evaluator, should_raise in (
                ('duty', type('DutyHeld', (), {
                    'pending': {}, 'duty': 0.5, 'closed': False,
                    'submit_recovered': lambda *args, **kwargs: (_ for _ in ()).throw(
                        AssertionError('batch path must not run while duty-held')),
                    'submit': lambda self, *args, **kwargs: False,
                })(), False),
                ('closed', type('ClosedEvaluator', (), {
                    'pending': {}, 'duty': 1.0, 'closed': True,
                    'submit_recovered': lambda *args, **kwargs: (_ for _ in ()).throw(
                        AssertionError('closed evaluator must not batch-reserve')),
                    'submit': lambda self, *args, **kwargs: (_ for _ in ()).throw(
                        RuntimeError('Evaluator is closed')),
                })(), True)):
            db = _db(root / f'{name}-gate.sqlite')
            experiment = ledger.create_experiment(db, _intent(f'{name}-gate', 1))
            coordinator = _coordinator(evaluator)
            coordinator.workers = 1
            coordinator._submission_started = 0
            coordinator.state = {'timings': {}}
            coordinator._hydrate_current = lambda db_arg, current: True
            coordinator._experiment_state = lambda db_arg, exp_arg: {'complete': False, 'remaining': 1}
            coordinator._submission_capacity = lambda: 1
            coordinator._reserved_jobs = lambda db_arg, exp_arg: set()
            coordinator._current_compatible = lambda current: (True, None)
            coordinator._runnable_kwargs = lambda: {}
            current = {
                'experimentId': experiment, 'candidateId': 'candidate',
                'observedCandidate': {'candidate': 'candidate'}, 'observedReference': None,
                'jobs': [{'candidateId': 'candidate', 'seedPair': [301, 401]}],
            }
            if should_raise:
                try:
                    coordinator._dispatch_member(db, current)
                    raise AssertionError('closed evaluator did not fail closed')
                except RuntimeError as exc:
                    assert 'Evaluator is closed' in str(exc)
            else:
                assert coordinator._dispatch_member(db, current) is False
                assert evaluator.pending == {}
            assert not db.in_transaction
            assert _reserved_count(db, experiment) == 0
            db.close()
        checks['DutyAndClosedEvaluatorDoNotPreReserve'] = True

        # ReadyQueueEvaluator's process-owned duty gate permits an atomic 64-row reservation bank
        # even at reduced duty. Every queued worker request observes the complete committed bank
        # from another SQLite connection; the finite budget and charged owner remain exact, and
        # queued jobs do not age into battle timeouts before a process starts.
        path = root / 'ready-queue-batch.sqlite'
        db = _db(path)
        experiment = ledger.create_experiment(db, _intent('ready-queue-batch', 64))

        class CommitObservingPool:
            supports_ready_window = True
            supports_job_trace = False

            def __init__(self):
                self.visible_counts = []
                self.charged_owners = []
                self.duty = 1.0

            def configure_duty(self, duty):
                self.duty = float(duty)

            def submit(self, _function, _scenario, pair):
                other = sqlite3.connect(path)
                count = int(other.execute(
                    'SELECT COUNT(*) FROM ea_sample_link WHERE experiment_id=?',
                    (experiment,)).fetchone()[0])
                row = other.execute(
                    'SELECT charged_experiment_id FROM ea_sample_link '
                    'WHERE experiment_id=? AND seed_a=? AND seed_b=?',
                    (experiment, pair[0], pair[1])).fetchone()
                other.close()
                assert count == 64, count
                assert row is not None and int(row[0]) == experiment, row
                self.visible_counts.append(count)
                self.charged_owners.append(int(row[0]))
                future = Future()
                future.ka_dispatch_state = {'startedAt': None}
                return future

            def shutdown(self, wait=True, cancel_futures=False):
                pass

            def terminate_workers(self):
                pass

        pool = CommitObservingPool()
        evaluator = ready_queue.ReadyQueueEvaluator(workers=5, pool=pool, ready_window=16,
                                                    timeout=0.001)
        evaluator.configure_duty(0.9)
        coordinator = search.Coordinator(path=str(path), revision='fixture', workers=5)
        coordinator.evaluator = evaluator
        coordinator._current_compatible = lambda current: (True, None)
        coordinator._runnable_kwargs = lambda: {}
        current = {
            'experimentId': experiment, 'candidateId': 'candidate',
            'observedCandidate': {'candidate': 'candidate'}, 'observedReference': None,
            'jobs': [{'candidateId': 'candidate', 'seedPair': [501 + n, 1501 + n]}
                     for n in range(64)],
        }
        assert coordinator._dispatch_member(db, current) is True
        assert len(pool.visible_counts) == 64 and set(pool.visible_counts) == {64}
        assert len(pool.charged_owners) == 64 and set(pool.charged_owners) == {experiment}
        assert len(evaluator.pending) == 64 and evaluator.admission_capacity() == 16
        assert not db.in_transaction and _reserved_count(db, experiment) == 64
        assert int(db.execute(
            'SELECT COUNT(*) FROM ea_sample_link WHERE experiment_id=? AND charged_experiment_id=?',
            (experiment, experiment)).fetchone()[0]) == 64
        assert all(job['future'].ka_dispatch_state['startedAt'] is None
                   for job in evaluator.pending.values())
        import time
        time.sleep(0.005)
        assert evaluator._expired() == []
        checks['ReadyQueueDutyBatchCommitOwnerBudgetAndQueuedTimeout'] = {
            'reserved': 64, 'enqueuedAfterCommit': len(pool.visible_counts),
            'admissionRemaining': evaluator.admission_capacity(), 'expiredBeforeStart': 0,
        }
        evaluator.close()
        db.close()

    print(json.dumps({'status': 'ok', 'checks': checks}, sort_keys=True))


if __name__ == '__main__':
    main()
