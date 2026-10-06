"""Focused deterministic checks for the asynchronous planning pool.

Run:  python -B -X utf8 tools/recovery/check_optimizer_planning_pool.py

Read-only, no DB/GUI, no live battles, no benchmark. The pool is driven with the tiny module-level
targets defined below (this file is imported by the workers under its module name). Non-blocking
submission is proven with a flag file the worker waits on, so no check depends on a sleep race.

Covered here:
  * the host hard worker maximum is enforced and one shared pool is reused;
  * submit never blocks and never runs the target inline (no silent synchronous fallback);
  * harvest_ready returns nothing until a task truly finishes, then a detached record;
  * records carry deterministic ids, ordered timestamps, PID, CPU seconds and byte metrics;
  * empty results are valid, worker exceptions come back compact with a traceback;
  * bounded pending count and bounded queued payload bytes reject excess work;
  * cancel / cancel_unstarted drop only unstarted work;
  * close is bounded, reports surviving PIDs, and the legacy plan/iter_build_chunks API still works.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import strategy_parallel_proposals as parallel  # noqa: E402

MODULE = 'check_optimizer_planning_pool'
RESULTS = []
POLL_TIMEOUT = 20.0


# -- module-level picklable targets executed inside the pool workers ------------------------------

def _probe_echo(value, tag=None):
    return {'value': value, 'tag': tag}


def _probe_noop():
    return None


def _probe_boom():
    raise ValueError('boom')


def _probe_wait(flag_path, payload):
    while not os.path.exists(flag_path):
        time.sleep(0.005)
    return payload


def _probe_spin(iterations):
    total = 0
    for index in range(iterations):
        total += index * index
    return total


def _probe_text(size):
    return 'x' * size


def _probe_unpicklable():
    return lambda: None


def _probe_context_build(context, spec):
    if spec.get('none'):
        return None
    return {'i': spec['i'], 'scaled': spec['i'] * context['scale']}


# -- helpers --------------------------------------------------------------------------------------

def check(name, condition, detail=''):
    RESULTS.append((name, bool(condition)))
    print(('PASS ' if condition else 'FAIL ') + name + ((' :: ' + str(detail)) if detail else ''))


def _wait_running(pool, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pool.status()['running'] >= 1:
            return True
        time.sleep(0.01)
    return False


def _harvest_ids(pool, ids, timeout=POLL_TIMEOUT):
    wanted = set(ids)
    found = {}
    deadline = time.monotonic() + timeout
    while wanted and time.monotonic() < deadline:
        for record in pool.harvest_ready():
            found[record['requestId']] = record
            wanted.discard(record['requestId'])
        if wanted:
            time.sleep(0.01)
    return found


def _watchdog(seconds):
    time.sleep(seconds)
    sys.stderr.write('planning-pool check watchdog fired; a submit likely blocked\n')
    os._exit(3)


# -- checks ---------------------------------------------------------------------------------------

def main():
    threading.Thread(target=_watchdog, args=(180,), daemon=True).start()
    tmp = Path(tempfile.mkdtemp(prefix='ka-planning-check-'))
    try:
        # 1. Host-supplied hard worker maximum is enforced.
        raised = False
        try:
            parallel.PlanningPool(workers=parallel.MAX_PLANNING_WORKERS + 1)
        except ValueError:
            raised = True
        check('hard-worker-maximum-enforced', raised)

        # 2. The application owns exactly one persistent shared pool.
        pool = parallel.get_planning_pool(workers=2, max_pending=8)
        check('shared-pool-is-singleton', parallel.get_planning_pool() is pool)
        check('shared-pool-status-alive', parallel.planning_pool_status()['alive'] is True)

        # 3. Non-blocking submission and the harvest lifecycle on a flag-gated task.
        flag = tmp / 'go.flag'
        submitted = {}
        done = threading.Event()

        def _do_submit():
            submitted['ok'] = pool.submit('wait-1', MODULE, '_probe_wait', (str(flag), 'waited'))
            done.set()

        threading.Thread(target=_do_submit, daemon=True).start()
        check('submit-returns-without-task-completion', done.wait(timeout=10.0))
        check('submit-returned-true', submitted.get('ok') is True)
        check('harvest-empty-while-task-blocked', pool.harvest_ready() == [])
        check('status-shows-running-task', _wait_running(pool), pool.status())
        flag.write_text('go', encoding='utf-8')
        record = _harvest_ids(pool, ['wait-1']).get('wait-1')
        check('completed-task-harvested', record is not None)
        if record is not None:
            check('record-request-id-deterministic', record['requestId'] == 'wait-1')
            check('record-result-detached', record['result'] == 'waited' and record['error'] is None)
            check('record-has-worker-pid', isinstance(record['workerPid'], int)
                  and record['workerPid'] > 0, record['workerPid'])
            check('record-timestamps-ordered',
                  record['submittedAt'] <= record['startedAt'] + 1e-3
                  <= record['finishedAt'] + 2e-3 <= record['harvestedAt'] + 3e-3,
                  (record['submittedAt'], record['startedAt'], record['finishedAt'],
                   record['harvestedAt']))
            check('record-request-bytes-positive', record['requestBytes'] > 0,
                  record['requestBytes'])
            check('record-result-bytes-present', isinstance(record['resultBytes'], int))
            check('record-cpu-seconds-present', record['workerCpuSeconds'] >= 0)

        # 4. Empty result is valid, and an empty harvest is not an error.
        pool.submit('noop-1', MODULE, '_probe_noop')
        noop = _harvest_ids(pool, ['noop-1']).get('noop-1')
        check('empty-result-valid', noop is not None and noop['result'] is None
              and noop['error'] is None)

        # 5. Byte metrics scale with the serialized request and result sizes.
        pool.submit('text-small', MODULE, '_probe_text', (256,))
        pool.submit('text-large', MODULE, '_probe_text', (65536,))
        sizes = _harvest_ids(pool, ['text-small', 'text-large'])
        small, large = sizes.get('text-small'), sizes.get('text-large')
        check('result-bytes-scale-with-result',
              small is not None and large is not None
              and large['resultBytes'] > small['resultBytes'] > 0,
              (small and small['resultBytes'], large and large['resultBytes']))
        check('request-bytes-scale-with-args',
              small is not None and large is not None
              and large['requestBytes'] > small['requestBytes'] > 0,
              (small and small['requestBytes'], large and large['requestBytes']))

        # 6. A worker exception surfaces compactly with a traceback; the result stays empty.
        pool.submit('boom-1', MODULE, '_probe_boom')
        boom = _harvest_ids(pool, ['boom-1']).get('boom-1')
        check('worker-exception-compact-and-traced',
              boom is not None and boom['result'] is None
              and (boom['error'] or '').startswith('ValueError: boom')
              and 'ValueError' in (boom['traceback'] or ''), boom and boom['error'])

        # 7. An unpicklable result is reported instead of silently dropped.
        pool.submit('unpicklable-1', MODULE, '_probe_unpicklable')
        bad = _harvest_ids(pool, ['unpicklable-1']).get('unpicklable-1')
        check('unpicklable-result-reported',
              bad is not None and bad['result'] is None and bad['error'] is not None
              and bad['resultBytes'] == 0, bad and bad['error'])

        # 8. CPU seconds are captured for real compute.
        pool.submit('spin-1', MODULE, '_probe_spin', (2_000_000,))
        spin = _harvest_ids(pool, ['spin-1']).get('spin-1')
        check('worker-cpu-seconds-captured', spin is not None and spin['workerCpuSeconds'] > 0,
              spin and spin['workerCpuSeconds'])

        # 9. Bounded pending count is enforced; excess work is rejected, never run inline.
        cap = parallel.PlanningPool(workers=2, max_pending=2)
        cap_flag = str(tmp / 'cap.flag')  # never created, so blocked tasks cannot finish
        check('capacity-accepts-first', cap.submit('c1', MODULE, '_probe_wait', (cap_flag, 1)))
        check('capacity-accepts-second', cap.submit('c2', MODULE, '_probe_wait', (cap_flag, 2)))
        check('capacity-rejects-third', cap.submit('c3', MODULE, '_probe_wait', (cap_flag, 3))
              is False)
        check('capacity-honest-outstanding', cap.status()['outstanding'] == 2,
              cap.status()['outstanding'])
        started = time.monotonic()
        closed = cap.close(timeout=1.0)
        check('close-is-bounded', time.monotonic() - started < 8.0)
        check('close-report-shape', closed['closed'] is True
              and isinstance(closed['remainingPids'], list), closed)
        check('close-left-no-owning-pids', closed['remainingPids'] == [], closed['remainingPids'])

        # 10. Bounded queued payload bytes are enforced.
        bytes_pool = parallel.PlanningPool(workers=1, max_pending=8, max_pending_bytes=4096)
        check('bytes-limit-rejects-huge-request',
              bytes_pool.submit('huge', MODULE, '_probe_echo', ('x' * 200000,)) is False)
        check('bytes-limit-accepts-small-request',
              bytes_pool.submit('tiny', MODULE, '_probe_echo', ('x' * 16,)) is True)
        bytes_pool.close(timeout=2.0)

        # 11. Cancel drops only unstarted work.
        canc = parallel.PlanningPool(workers=1, max_pending=4)
        cancel_flag = str(tmp / 'cancel.flag')  # never created
        canc.submit('run-1', MODULE, '_probe_wait', (cancel_flag, 'r'))
        check('cancel-worker-started', _wait_running(canc))
        canc.submit('pend-2', MODULE, '_probe_wait', (cancel_flag, 'p2'))
        canc.submit('pend-3', MODULE, '_probe_wait', (cancel_flag, 'p3'))
        check('cancel-running-returns-false', canc.cancel('run-1') is False)
        check('cancel-pending-returns-true', canc.cancel('pend-2') is True)
        check('cancel-unstarted-counts-remaining', canc.cancel_unstarted() == 1)
        state = canc.status()
        check('cancel-status-consistent',
              state['running'] == 1 and state['pending'] == 0 and state['cancelled'] == 2, state)
        check('cancelled-work-never-harvested',
              all(record['requestId'] != 'pend-2' for record in canc.harvest_ready()))
        cancelled_close = canc.close(timeout=1.0)
        check('cancel-pool-close-clean', cancelled_close['remainingPids'] == [],
              cancelled_close['remainingPids'])

        # 12. Legacy plan / iter_build_chunks still work through the same transport.
        planned, reason = parallel.plan(64)
        check('legacy-plan-bounded',
              2 <= planned <= parallel.MAX_PREP_WORKERS and reason == 'bounded-parallel',
              'workers=%d reason=%s' % (planned, reason))
        tiny, tiny_reason = parallel.plan(parallel.MIN_SPECS_FOR_PARALLEL - 1)
        check('legacy-plan-tiny-serial',
              tiny == 0 and tiny_reason == 'tiny-workload-serial', tiny_reason)
        specs = [dict(i=index, none=(index % 3 == 2)) for index in range(6)]
        expected = [None if spec['none'] else {'i': spec['i'], 'scaled': spec['i'] * 4}
                    for spec in specs]
        got = []
        for _chunk, results in parallel.iter_build_chunks(MODULE, '_probe_context_build',
                                                          {'scale': 4}, specs, workers=2):
            got.extend(results)
        check('legacy-chunks-preserve-order', got == expected, got[:3])
        check('legacy-pool-alive', parallel.diagnostics()['poolAlive'] is True)
        parallel.shutdown()
        check('legacy-shutdown-closes-pool', parallel.diagnostics()['poolAlive'] is False)

        # 13. The shared pool closes and can be recreated.
        parallel.close_planning_pool()
        check('shared-pool-closed', parallel.planning_pool_status()['alive'] is False)
        revived = parallel.get_planning_pool()
        check('shared-pool-recreated', revived is not pool and revived.closed is False)
        parallel.close_planning_pool()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    failed = [name for name, ok in RESULTS if not ok]
    print('\n%d/%d checks passed' % (len(RESULTS) - len(failed), len(RESULTS)))
    if failed:
        print('FAILED: ' + ', '.join(failed))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
