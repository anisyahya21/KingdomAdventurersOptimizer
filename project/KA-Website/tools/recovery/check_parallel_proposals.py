"""Targeted checks for the bounded parallel pre-dispatch proposal pool.

Run:  <venv>/python -B -X utf8 tools/recovery/check_parallel_proposals.py

Deterministic, read-only, no battles, no live library. The parallel path is exercised with a small
bounded set of BELOW_NORMAL child processes; the SAME checks also pass with ``KA_PREP_WORKERS=0``
(the digest comparison then compares serial against serial, which is still a valid invariant).

Covered here:
  * the plan is bounded and tiny/unsupported workloads stay honestly serial;
  * serial and parallel preparation return byte-identical candidates in identical order;
  * a custom admission callback or a non-recovered solver never silently drops to a worker;
  * chunk results keep strict original order (including ``None`` skips);
  * a worker failure is fail-closed and discards the pool (no silent partial result);
  * the pool is reused across rounds and closed cleanly;
  * dispatch fills every free worker (past the old fixed cap) and reports the real limit.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
WORKSPACE = HERE.parents[2]

import strategy_build_domain as domain                 # noqa: E402
import strategy_encounter_search as search             # noqa: E402
import strategy_joint_proposals as proposals           # noqa: E402
import strategy_parallel_proposals as parallel         # noqa: E402
import strategy_mechanics as mechanics                 # noqa: E402
import strategy_students as students                   # noqa: E402

SCENARIO_PATH = (WORKSPACE / 'RE-evidence/20260920-user-wairo-strategy/scenarios/'
                 'UREF.scenario.json')
RESULTS = []


def check(name, condition, detail=''):
    RESULTS.append((name, bool(condition)))
    print(('PASS ' if condition else 'FAIL ') + name + ((' :: ' + str(detail)) if detail else ''))


def _probe_build(context, spec):
    """A tiny picklable builder used by the order/None checks (imported by the worker)."""
    if spec.get('none'):
        return None
    return {'i': spec['i'], 'scaled': spec['i'] * context['scale'], 'tag': context['tag']}


def digest(rows):
    return hashlib.sha256(domain.canonical(rows).encode('utf-8')).hexdigest()


class FakeEvaluator:
    """A stub battle pool that accepts every submission and never completes it."""

    def __init__(self, workers):
        self.workers = workers
        self.pending = []
        self.submitted = []

    def submit(self, db, experiment_id, candidate_id, scenario, pair):
        self.pending.append((candidate_id, pair))
        self.submitted.append((candidate_id, pair))
        return True

    def submit_holdout(self, db, experiment_id, candidate_id, scenario, pair):
        return self.submit(db, experiment_id, candidate_id, scenario, pair)


class WindowFakeEvaluator(FakeEvaluator):
    """A ready-ahead evaluator stub whose bounded capacity exceeds physical workers."""

    def __init__(self, workers, capacity, *, duty=1.0):
        super().__init__(workers)
        self.max_pending = capacity
        self.duty = duty

    def admission_capacity(self):
        return max(0, self.max_pending - len(self.pending))

    def submit_recovered(self, *args, **kwargs):
        raise AssertionError('duty-limited submissions must use the established submit path')


def _frozen_jobs(count, scenario):
    jobs = [dict(candidateId='cand' if index % 2 == 0 else 'ref',
                 seedPair=[index, index + 100]) for index in range(count)]
    return dict(experimentId='exp', candidateId='cand', blocked=False, jobs=jobs,
                observedCandidate=scenario, observedReference=scenario,
                engineRevision='check', mechanicsRevision=None, encounterRevision=None,
                compatibility=None, planned=count)


def main():
    scenario = json.loads(SCENARIO_PATH.read_text(encoding='utf-8'))

    # 1. The plan is bounded, and tiny/disabled workloads stay honestly serial.
    workers, reason = parallel.plan(64)
    check('plan-is-bounded', 2 <= workers <= parallel.MAX_PREP_WORKERS and reason == 'bounded-parallel',
          'workers=%d reason=%s' % (workers, reason))
    tiny, tiny_reason = parallel.plan(parallel.MIN_SPECS_FOR_PARALLEL - 1)
    check('tiny-workload-stays-serial', tiny == 0 and tiny_reason == 'tiny-workload-serial',
          '%d/%s' % (tiny, tiny_reason))

    # 2. Serial and parallel preparation are byte-identical, in identical order, per round.
    previous = os.environ.get('KA_PREP_WORKERS')
    try:
        os.environ['KA_PREP_WORKERS'] = '0'
        serial = [proposals.pool(scenario, maximum=12, round_index=r) for r in range(3)]
        os.environ['KA_PREP_WORKERS'] = str(workers)
        parallel_rows = []
        parallel_reasons = []
        for r in range(3):
            parallel_rows.append(proposals.pool(scenario, maximum=12, round_index=r))
            parallel_reasons.append(proposals.LAST_PREPARATION['reason'])
        digests_match = all(digest(a) == digest(b) for a, b in zip(serial, parallel_rows))
        check('serial-and-parallel-identical-order-and-digest', digests_match,
              'serial=%s parallel=%s' % (digest(serial[0])[:12], digest(parallel_rows[0])[:12]))
        check('parallel-path-was-actually-used', parallel_reasons == ['bounded-parallel'] * 3
              and proposals.LAST_PREPARATION['workers'] >= 2, parallel_reasons)
    finally:
        if previous is None:
            os.environ.pop('KA_PREP_WORKERS', None)
        else:
            os.environ['KA_PREP_WORKERS'] = previous

    # 3. Unsupported configurations stay serial - never a silent false parallel claim.
    def custom_admit(child, parent):
        return students.community_admits(child, parent)

    proposals.pool(scenario, maximum=12, admit=custom_admit)
    check('custom-admission-stays-serial',
          proposals.LAST_PREPARATION['reason'] == 'custom-admission-serial'
          and proposals.LAST_PREPARATION['workers'] == 0, proposals.LAST_PREPARATION['reason'])
    original_solver = proposals.COMPENSATION_SOLVER
    proposals.COMPENSATION_SOLVER = lambda *a, **k: {
        'results': [{'best': {'attack': 42, 'residual': 0.5},
                     'candidates': [{'attack': 42, 'residual': 0.5}]}]}
    try:
        proposals.pool(scenario, maximum=12)
    finally:
        proposals.COMPENSATION_SOLVER = original_solver
    check('custom-solver-stays-serial',
          proposals.LAST_PREPARATION['reason'] == 'custom-solver-serial'
          and proposals.LAST_PREPARATION['workers'] == 0, proposals.LAST_PREPARATION['reason'])

    # 4. Chunk results keep strict original order, including None skips.
    specs = [dict(i=index, none=(index % 3 == 2)) for index in range(8)]
    expected = [None if spec['none'] else {'i': spec['i'], 'scaled': spec['i'] * 5, 'tag': 'x'}
                for spec in specs]
    got = []
    for _chunk, results in parallel.iter_build_chunks('check_parallel_proposals', '_probe_build',
                                                      {'scale': 5, 'tag': 'x'}, specs, workers=3):
        got.extend(results)
    check('chunk-results-preserve-original-order', got == expected,
          'got=%r' % (got[:3],))
    check('pool-is-live-and-reused', parallel.diagnostics()['poolAlive'] is True
          and parallel.diagnostics()['poolWorkers'] == 3, parallel.diagnostics())

    # 5. A worker failure is fail-closed and discards the pool (no silent partial result).
    raised = False
    try:
        list(parallel.iter_build_chunks('strategy_parallel_proposals', '_no_such_builder', {},
                                        [dict(i=index) for index in range(4)], workers=2))
    except RuntimeError as exc:
        raised = 'no_such_builder' in str(exc) or 'attribute' in str(exc).lower()
    check('worker-failure-is-fail-closed', raised, parallel.diagnostics().get('lastError'))
    check('failed-pool-was-discarded', parallel.diagnostics()['poolAlive'] is False)
    check('failure-never-consumed-a-battle-budget',
          parallel.diagnostics()['lastError'] is not None and search.EVALUATOR_FACTORY is None)

    # 6. Clean shutdown, and the pool comes back when work resumes.
    parallel.shutdown()
    check('clean-shutdown-closes-the-pool', parallel.diagnostics()['poolAlive'] is False)
    revived = []
    for _chunk, chunk_results in parallel.iter_build_chunks('check_parallel_proposals',
                                                            '_probe_build',
                                                            {'scale': 2, 'tag': 'y'},
                                                            [dict(i=index) for index in range(6)],
                                                            workers=2):
        revived.extend(chunk_results)
    check('pool-recreated-after-shutdown',
          parallel.diagnostics()['poolAlive'] is True and revived[0]['scaled'] == 0)
    parallel.shutdown()

    # 7. Dispatch fills every free worker and reports the real limit (past the historic cap).
    check('historic-pass-cap-retained', search.MAX_SUBMISSIONS_PER_PASS == 4,
          search.MAX_SUBMISSIONS_PER_PASS)
    for worker_count, expected in ((8, 8), (4, 4)):
        coordinator = search.Coordinator(path='unused.sqlite', revision='check', workers=worker_count)
        coordinator.evaluator = FakeEvaluator(worker_count)
        coordinator._experiment_state = lambda db, exp: dict(complete=False, remaining=8, planned=8)
        coordinator._reserved_jobs = lambda db, exp: set()
        coordinator._current_compatible = lambda current: (True, None)
        coordinator.state['current'] = _frozen_jobs(8, scenario)
        coordinator._dispatch(None, None)
        limit = coordinator.last_submission_limit or {}
        check('dispatch-fills-free-workers-%d' % worker_count,
              len(coordinator.evaluator.submitted) == expected
              and limit.get('capacity') == expected and limit.get('historicalCap') == 4,
              'submitted=%d limit=%r' % (len(coordinator.evaluator.submitted), limit))
        check('eight-battle-experiment-capped-at-worker-limit-%d' % worker_count,
              len(coordinator.evaluator.submitted) <= min(worker_count, 8),
              len(coordinator.evaluator.submitted))

    # 7a. A ready window is filled beyond physical worker count, but still obeys the
    # reservation-batch bound, per-coordinator quota, zero-quota and duty fallback paths.
    def dispatch_member(evaluator, *, count=72, quota=None, canonical=False):
        coordinator = search.Coordinator(path='unused.sqlite', revision='check', workers=5)
        coordinator.evaluator = evaluator
        coordinator._experiment_state = lambda db, exp: dict(complete=False,
                                                               remaining=count, planned=count)
        coordinator._reserved_jobs = lambda db, exp: set()
        coordinator._current_compatible = lambda current: (True, None)
        coordinator._is_canonical_evaluator = lambda: canonical
        coordinator._prepare_durable_dispatch = lambda db: None
        coordinator._submission_quota = quota
        current = _frozen_jobs(count, scenario)
        coordinator._dispatch_member(None, current)
        return coordinator

    ready_eval = WindowFakeEvaluator(5, 80)
    dispatch_member(ready_eval)
    check('ready-window-admits-more-than-physical-workers',
          len(ready_eval.submitted) == search.RESERVATION_BATCH_MAX == 64,
          'submitted=%d limit=%d' % (len(ready_eval.submitted), search.RESERVATION_BATCH_MAX))
    quota_eval = WindowFakeEvaluator(5, 80)
    dispatch_member(quota_eval, quota=13)
    check('ready-window-respects-fair-quota', len(quota_eval.submitted) == 13,
          len(quota_eval.submitted))
    zero_quota_eval = WindowFakeEvaluator(5, 80)
    dispatch_member(zero_quota_eval, quota=0)
    check('ready-window-zero-quota-submits-nothing', not zero_quota_eval.submitted,
          len(zero_quota_eval.submitted))
    duty_eval = WindowFakeEvaluator(5, 80, duty=0.9)
    dispatch_member(duty_eval, canonical=True)
    check('duty-limited-ready-window-uses-per-seed-submit',
          len(duty_eval.submitted) == search.RESERVATION_BATCH_MAX,
          len(duty_eval.submitted))
    legacy_eval = FakeEvaluator(5)
    dispatch_member(legacy_eval)
    check('legacy-evaluator-retains-worker-width-admission', len(legacy_eval.submitted) == 5,
          len(legacy_eval.submitted))

    # 8. The holdout path fills the same way without touching the paired plan.
    coordinator = search.Coordinator(path='unused.sqlite', revision='check', workers=8)
    coordinator.evaluator = FakeEvaluator(8)
    coordinator.state['confirmation'] = {'experimentId': 'confirm'}
    coordinator._intent = lambda db, exp: dict(engineRevision='check',
                                               observedScenarios={'cand': scenario})
    coordinator._mechanics_revision = lambda: None
    coordinator._compatibility = lambda: None
    confirmation_jobs = [('cand', (index, index + 100)) for index in range(8)]
    coordinator._dispatch_confirmation(None, None, confirmation_jobs)
    limit = coordinator.last_submission_limit or {}
    check('holdout-dispatch-fills-free-workers',
          len(coordinator.evaluator.submitted) == 8 and limit.get('capacity') == 8, limit)

    failed = [name for name, ok in RESULTS if not ok]
    print('\n%d/%d checks passed' % (len(RESULTS) - len(failed), len(RESULTS)))
    if failed:
        print('FAILED: ' + ', '.join(failed))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
