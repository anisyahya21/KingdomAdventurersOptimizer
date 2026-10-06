"""Deterministic checks for bounded ready-ahead encounter admission.

Run: python tools/recovery/check_encounter_ready_queue.py
Uses a temporary in-memory ledger and synthetic Futures only; it launches no native battles.
"""
from concurrent.futures import Future
import json
import queue
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import strategy_experiment_store as ledger
from strategy_encounter_ready_queue import ReadyQueueEvaluator
from strategy_optimizer_fast import HeadlessPool


class DeferredPool:
    supports_ready_window = True
    supports_job_trace = False

    def __init__(self):
        self.calls = []
        self.duty = 1.0

    def configure_duty(self, duty):
        self.duty = float(duty)

    def submit(self, _function, scenario, seeds):
        future = Future()
        future.ka_dispatch_state = {'startedAt': None}
        self.calls.append((future, scenario, list(seeds)))
        return future

    def snapshot(self):
        return {'queued': len(self.calls), 'executing': 0, 'cooling': 0, 'available': 0}

    def shutdown(self, wait=True, cancel_futures=False):
        pass

    def terminate_workers(self):
        pass


def main():
    db = sqlite3.connect(':memory:')
    ledger.initialize(db)
    intent = {'scope': 'queue-check', 'purpose': 'improvement', 'planned_budget': 5,
              'stopping': 'finite', 'ownerShare': 1,
              'policy': {'finishPolicy': 'on-verdict'},
              'expectedMechanicalDifferences': {'fixture': 1}}
    experiment = ledger.create_experiment(db, intent)
    pool = DeferredPool()
    evaluator = ReadyQueueEvaluator(workers=2, pool=pool, ready_window=2, timeout=0.01)

    accepted = [evaluator.submit(db, experiment, 'candidate', {'frozen': True}, [1, n])
                for n in range(1, 6)]
    assert accepted == [True, True, True, True, False], accepted
    assert len(evaluator.pending) == evaluator.max_pending == 4
    assert len(ledger.development_reservations(db, experiment)) == 4
    assert evaluator.admission_capacity() == 0
    time.sleep(0.02)
    assert evaluator._expired() == []  # queued work has no battle timeout until a process starts
    status = evaluator.status()
    assert status['inflight'] == 4 and status['windowCapacity'] == 4
    assert status['readyQueueDepth'] == 4 and status['completedUnharvested'] == 0

    for future, _scenario, seeds in pool.calls:
        future.set_result({'seeds': seeds, 'verdict': 1, 'resultBackend': 'native',
                           'rewardOutcome': {'pendingChests': 3}, 'elapsedSeconds': 0.01})
    harvested = evaluator.harvest(db)
    assert len(harvested) == 4 and all(row['accepted'] for row in harvested)
    assert not evaluator.pending and evaluator.admission_capacity() == 4
    summary = ledger.summary(db)
    assert summary['totalCompleted'] == 4 and summary['totalReserved'] == 4
    assert summary['conserved']
    evaluator.close()
    db.close()

    # Exercise the real pool scheduler with deterministic pipe-shaped fake interpreters. This proves
    # queued requests run while the coordinator deliberately delays harvest, and that per-process
    # duty holds do not block Future completion or get bypassed by the ready-ahead queue.
    pool = HeadlessPool.__new__(HeadlessPool)
    pool.telemetry = False
    pool.trace = False
    pool.digest = 'ready-queue-fixture'
    pool.processes = []
    pool.available = queue.Queue()
    pool._counters = {'submitted': 0, 'started': 0, 'finished': 0,
                      'executing': 0, 'cooling': 0}
    pool._counter_lock = threading.Lock()
    pool._duty = 0.5
    pool._cooldown_condition = threading.Condition()
    pool._cooldown_heap = []
    pool._cooldown_sequence = 0
    pool._cooldown_stopping = False
    pool._cooldown_thread = threading.Thread(
        target=pool._cooldown_loop, name='ready-queue-check-reaper', daemon=True)
    pool._cooldown_thread.start()
    pool.executor = ThreadPoolExecutor(max_workers=2)

    class FakeOutput:
        def __init__(self):
            self.lines = queue.Queue()

        def readline(self, _limit):
            return self.lines.get(timeout=2)

        def close(self):
            pass

    class FakeInput:
        def __init__(self, process):
            self.process = process
            self.buffer = ''

        def write(self, value):
            self.buffer += value

        def flush(self):
            request = json.loads(self.buffer)
            self.buffer = ''
            seeds = request['seeds']
            self.process.started_at.append(time.perf_counter())
            result = {'seeds': seeds, 'verdict': 1, 'resultBackend': 'native',
                      'rewardOutcome': {'pendingChests': 2}, 'elapsedSeconds': 0.04}
            self.process.stdout.lines.put(json.dumps({'result': result}) + '\n')

        def close(self):
            pass

    class FakeInterpreter:
        def __init__(self, pid):
            self.pid = pid
            self.started_at = []
            self.stdout = FakeOutput()
            self.stdin = FakeInput(self)

        def wait(self, timeout=None):
            return 0

    workers = [FakeInterpreter(1001), FakeInterpreter(1002)]
    pool.processes = workers
    for worker in workers:
        pool.available.put(worker)
    db2 = sqlite3.connect(':memory:')
    ledger.initialize(db2)
    experiment2 = ledger.create_experiment(db2, intent)
    live = ReadyQueueEvaluator(workers=2, pool=pool, ready_window=2, timeout=5)
    live.configure_duty(0.5)
    assert all(live.submit(db2, experiment2, 'candidate', {}, [2, n]) for n in range(1, 5))
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and not all(
            job['future'].done() for job in live.pending.values()):
        time.sleep(0.005)
    assert all(job['future'].done() for job in live.pending.values()), live.status()
    queued_status = live.status()
    assert queued_status['completedUnharvested'] == 4, queued_status
    assert queued_status['inflight'] == 4 and queued_status['executingWorkers'] == 0
    for worker in workers:
        assert len(worker.started_at) == 2, [row.started_at for row in workers]
        assert worker.started_at[1] - worker.started_at[0] >= 0.035, worker.started_at
    assert len(live.harvest(db2)) == 4
    live.close()
    db2.close()
    print('PASS bounded queue runs ahead of harvest, respects per-process duty, and persists once')


if __name__ == '__main__':
    main()
