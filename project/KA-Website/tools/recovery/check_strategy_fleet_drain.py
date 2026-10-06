"""Synthetic regression for safe explicit Community worker resizing."""
from __future__ import annotations

import json

from strategy_optimizer import Optimizer


class FakeStore:
    def __init__(self):
        self.values = {'communityCampaignFleet': {'status': 'active', 'campaignId': 'synthetic'}}
        self.db = self
        self.commits = 0

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value):
        self.values[key] = value

    def commit(self):
        self.commits += 1


class FakeEvaluator:
    def __init__(self, workers, *, busy=False):
        self.workers = workers
        self.closed = False
        self.pending = {'battle': object()} if busy else {}
        self._busy = busy
        self.close_calls = 0
        self.duty = 0.9

    def busy(self):
        return bool(self.pending) or self._busy

    def close(self):
        if self.busy():
            raise AssertionError('resize tried to close a busy evaluator')
        self.close_calls += 1
        self.closed = True

    def configure_duty(self, duty):
        self.duty = duty

    def status(self):
        return dict(workers=self.workers, inflight=len(self.pending), gatedWorkers=0)


class FakePlannerPool:
    def __init__(self, workers, **status):
        self.workers = workers
        self.closed = False
        self.snapshot = dict(closed=False, workers=workers, pending=0, running=0,
                             outstanding=0, completedUnharvested=0)
        self.snapshot.update(status)

    def status(self):
        return dict(self.snapshot)


class FakeCoordinator:
    def __init__(self, workers, evaluator):
        self.workers = workers
        self.duty = 0.9
        self.evaluator = evaluator
        self.configure_calls = []

    def configure_duty(self, duty):
        self.duty = duty
        if self.evaluator is not None:
            self.evaluator.configure_duty(duty)

    def configure_workers(self, workers):
        self.configure_calls.append(workers)
        self.workers = workers

    def _make_evaluator(self):
        return FakeEvaluator(self.workers)

    def allocation_status(self):
        return dict(requested=self.workers, effective=self.workers)


def make_optimizer(*, workers=2, planner_status=None, battle_busy=False):
    optimizer = Optimizer.__new__(Optimizer)
    evaluator = FakeEvaluator(max(1, workers - 1), busy=battle_busy)
    coordinator = FakeCoordinator(evaluator.workers, evaluator)
    pool = FakePlannerPool(1, **(planner_status or {}))
    optimizer._encounter = object()
    optimizer._encounter_report = None
    optimizer._campaign_report = {'campaignId': 'synthetic'}
    optimizer._campaign_started = True
    optimizer._campaign_resize_pending = None
    optimizer._worker_resize_status = dict(state='idle')
    optimizer.encounters = {11: {}}
    optimizer._campaign_coordinators = {11: coordinator}
    optimizer._community_evaluator = evaluator
    optimizer._community_fleet_state = {'status': 'active', 'campaignId': 'synthetic'}
    optimizer._campaign_fair_cursor = 0
    optimizer._workers = workers
    optimizer._duty = 0.9
    optimizer._planning_focus = []
    optimizer._planning_pool = pool
    optimizer._planner_routes = {}
    optimizer._planner_deferred_records = []
    optimizer._planner_chunk_map = lambda: optimizer.__dict__.setdefault('_planner_chunk_map_data', {})
    optimizer._planner_task_phase_map = lambda: optimizer.__dict__.setdefault('_planner_task_phase_data', {})
    optimizer._scheduler = {}
    optimizer._active_window = []
    optimizer._work_started = None
    optimizer._campaign_fleet_status = lambda reports=None: {
        'status': 'active', 'campaignId': 'synthetic'}
    optimizer._community_focus = lambda store: []
    optimizer._cancel_all_planning = lambda store: (_ for _ in ()).throw(
        AssertionError('role resize must not cancel planning routes'))

    close_calls = []

    def close_planning_pool(timeout=5.0):
        close_calls.append(timeout)
        optimizer._planning_pool.closed = True
        optimizer._planning_pool = None
        return dict(closed=True, cancelledPending=0, unharvestedResults=0, remainingPids=[])

    optimizer._close_planning_pool = close_planning_pool
    return optimizer, coordinator, evaluator, pool, close_calls


def main():
    results = []
    store = FakeStore()

    optimizer, coordinator, evaluator, pool, close_calls = make_optimizer(
        workers=2, planner_status=dict(pending=1, running=1, outstanding=1), battle_busy=True)
    report = optimizer._campaign_start(store, {'workers': 4, 'duty': 0.8})
    assert report['status'] == 'active'
    assert optimizer._workers == 2 and optimizer._duty == 0.9
    assert optimizer._campaign_resize_pending['workers'] == 4
    assert optimizer._campaign_resize_pending['duty'] == 0.8
    assert not evaluator.closed and not close_calls
    assert optimizer._cancel_all_planning.__name__ == '<lambda>'
    allocation = optimizer._worker_allocation_status()
    assert allocation['requestedWorkers'] == 4 and allocation['effectiveWorkers'] == 2
    assert allocation['requestedPlannerWorkers'] == 2 and allocation['effectivePlannerWorkers'] == 1
    results.append('same-scope resize stages without closing pools or cancelling routes')

    waiting = optimizer._campaign_resize_drain_status()
    assert not waiting['ready'] and waiting['waitingFor']['battlePending'] == 1
    assert waiting['waitingFor']['plannerOutstanding'] == 1
    assert not optimizer._apply_campaign_resize_if_drained(store)
    assert not evaluator.closed and not close_calls
    results.append('battle and phase-A planner work block replacement')

    # Phase A is harvested into a fan-out: pool requests now represent chunk work.
    evaluator.pending.clear()
    evaluator._busy = False
    pool.snapshot.update(pending=2, running=1, outstanding=2, completedUnharvested=0)
    optimizer._planner_routes['logical'] = {'phase': 'compute'}
    optimizer._planner_chunk_map()['chunk-1'] = {'logicalId': 'logical'}
    optimizer._planner_task_phase_map()['chunk-1'] = 'compute_planning_chunk'
    assert not optimizer._apply_campaign_resize_if_drained(store)
    assert not evaluator.closed and not close_calls

    # Completed chunks may already be in host memory while their owner is not yet eligible.
    pool.snapshot.update(pending=0, running=0, outstanding=0, completedUnharvested=0)
    optimizer._planner_chunk_map().clear()
    optimizer._planner_task_phase_map().clear()
    optimizer._planner_deferred_records.append({'requestId': 'deferred'})
    assert not optimizer._apply_campaign_resize_if_drained(store)
    assert not close_calls
    results.append('chunk fan-out and deferred owner results keep resize draining')

    # Only after all worker tasks are harvested, routed, and checkpointed may the pools swap.
    optimizer._planner_deferred_records.clear()
    optimizer._planner_routes.clear()
    assert optimizer._apply_campaign_resize_if_drained(store)
    assert optimizer._workers == 4 and optimizer._duty == 0.8
    assert optimizer._campaign_resize_pending is None
    assert evaluator.closed and evaluator.close_calls == 1
    assert len(close_calls) == 1
    assert optimizer._community_evaluator.workers == 2
    assert coordinator.evaluator is optimizer._community_evaluator
    assert coordinator.workers == 2 and coordinator.duty == 0.8
    assert optimizer._worker_allocation_status()['resizeState'] == 'applied'
    results.append('drained resize swaps pools while retaining the existing coordinator/session')

    # A changed focus cannot use the busy-resize path or reach its cancellation/rebuild code.
    optimizer, coordinator, evaluator, pool, close_calls = make_optimizer(
        workers=2, planner_status=dict(pending=1, running=1, outstanding=1), battle_busy=True)
    optimizer.encounters = {11: {}, 12: {}}
    optimizer._community_focus = lambda store: [12]
    assert not optimizer._campaign_resize_scope_matches(store)
    try:
        optimizer._campaign_start(store, {'workers': 4, 'duty': 0.8})
    except ValueError:
        pass
    else:
        raise AssertionError('changed-focus worker request should be refused while active')
    assert optimizer._workers == 2 and evaluator.pending
    assert not evaluator.closed and not close_calls
    assert optimizer._campaign_resize_pending is None
    results.append('busy changed-focus resize is refused before cancellation or pool close')

    print(json.dumps({'ok': True, 'checks': results}, separators=(',', ':')))


if __name__ == '__main__':
    main()
