"""Campaign-order regression for ready planner results on the production host path.

Run: python tools/recovery/check_optimizer_ready_planner_results.py

The fixture uses the real Optimizer campaign fleet, all twenty real Coordinators, the production
PlanningPool/lane workers, real proposal preparation/finalization, experiment freezing, compatibility
checks and Coordinator dispatch. Battle futures and selected host callbacks are controlled.
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import check_optimizer_campaign_dispatch_priority as dispatch
import strategy_encounter_search as search
import strategy_experiment_store as ledger
import strategy_joint_proposals as joint
import strategy_parallel_proposals as parallel

RESULTS = []


def check(name, passed, detail=None):
    RESULTS.append(dict(name=name, passed=bool(passed), detail=detail))
    print(('PASS ' if passed else 'FAIL ') + name +
          ((' :: ' + json.dumps(detail, sort_keys=True, default=str)) if detail is not None else ''))


class GatedPlanningPool(parallel.PlanningPool):
    """Real planner lane pool; hold completed Phase B replies until the fixture releases them."""

    def __init__(self):
        self.release_computed_chunks = threading.Event()
        self.computed_chunks = 0
        self._computed_cond = threading.Condition()
        super().__init__(workers=2, max_pending=32)

    def _execute(self, index, request):
        reply = super()._execute(index, request)
        # The real request has three ordered chunks for this fixture. Let the first two return so
        # the host can harvest/fan them out normally; hold only the final chunk at the lane boundary.
        if str(request.get('requestId')).endswith('#c2'):
            with self._computed_cond:
                self.computed_chunks += 1
                self._computed_cond.notify_all()
            if not self.release_computed_chunks.wait(90):
                return dict(ok=False, error='test gate timed out', startedAt=time.time(),
                            finishedAt=time.time(), resultBytes=0, cpuSeconds=0.0)
        return reply

    def wait_computed(self, count, timeout=90):
        deadline = time.monotonic() + timeout
        with self._computed_cond:
            while self.computed_chunks < count:
                remaining = deadline-time.monotonic()
                if remaining <= 0:
                    return False
                self._computed_cond.wait(min(.05, remaining))
        return True

    def wait_ready(self, count, timeout=90):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.status().get('completedUnharvested', 0) >= count:
                return True
            time.sleep(.002)
        return False

    def ready_records(self):
        with self._cond:
            return [dict(record) for record in self._results]


def make_rig(tag):
    opt, store, battle_pool = dispatch.make_rig('ready-planner-' + tag)
    planner = GatedPlanningPool()
    opt._planning_pool = planner
    opt._planner_routes = {}
    opt._planner_chunk_tasks = {}
    opt._planner_task_phase = {}
    opt._planner_phase_counts = {}
    opt._planner_accepted = 0
    opt._planner_rejected = 0
    return opt, store, battle_pool, planner


def start_planners(opt, store, encounter_ids):
    coordinators = []
    for encounter_id in encounter_ids:
        coord = opt._campaign_coordinators[encounter_id]
        coord.maximum = 2
        # This fixture is about when an already-valid planner result is harvested. Make the
        # question-dependent streams explicitly unavailable so the real coordinator reaches its
        # ordinary improvement request without spending passes marking empty streams unspendable.
        coord.state['unspendable'] = ['boundary', 'support', 'comparison', 'exploration']
        coord.state['idle'] = False
        coord._planning_dirty = True
        coord.run_pass(store, running=True, harvested_entries=[], recovery_snapshot=None)
        if coord._planner_pending is None:
            raise RuntimeError('campaign Coordinator did not queue its real planning request: '
                               + json.dumps(dict(encounter=coord.encounter,
                                   unspendable=coord.state.get('unspendable'),
                                   limitations=coord.limitations,
                                   nextTask=coord._next_task(store.db),
                                   requestClass=search.planning_request_class(),
                                   taskFunction=search.planning_task_function(),
                                   plannerHost=coord.planning_host is opt,
                                   hostError=getattr(opt, '_planner_last_error', None),
                                   budgets=coord._budget_remaining(store.db)), default=str))
        coordinators.append(coord)
    print('queued real planners: '+','.join(str(coord.encounter) for coord in coordinators), flush=True)
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        opt._harvest_planning(store, eligible_coordinators=coordinators)
        routes = [route for route in getattr(opt, '_planner_routes', {}).values()
                  if route.get('coord') in coordinators]
        if (len(routes) == len(coordinators)
                and all(route.get('phase') == 'compute'
                        and route.get('chunkSubmitted') == route.get('chunkTotal')
                        for route in routes)):
            print('real planner Phase B queued: '+json.dumps([
                dict(encounter=route.get('encounter'), chunks=route.get('chunkTotal'))
                for route in routes]), flush=True)
            return routes
        if not routes and not any(coord._planner_pending for coord in coordinators):
            diagnostics = [{'encounter': coord.encounter, 'enabled': coord.enabled,
                'mode': coord.mode, 'idle': coord.state.get('idle'),
                'planningDirty': coord._planning_dirty, 'sessionId': coord.session_id,
                'hasWork': coord._has_work(), 'pendingBattles': len(coord.evaluator.pending),
                'battleWorkers': coord.workers, 'cohort': coord._cohort(),
                'nextTask': coord._next_task(store.db),
                'remaining': coord._budget_remaining(store.db),
                'plannerEnabled': coord._planning_enabled(),
                'pending': bool(coord._planner_pending),
                'pendingRequestId': (coord._planner_pending or {}).get('requestId'),
                'notes': coord.state.get('limitations')} for coord in coordinators]
            raise RuntimeError('campaign Coordinator did not create a real planner request: '
                               + json.dumps(diagnostics, default=str))
        time.sleep(.002)
    diagnostics = [{
        'encounter': coord.encounter, 'enabled': coord.enabled,
        'mode': coord.mode, 'idle': coord.state.get('idle'),
        'planningDirty': coord._planning_dirty,
        'sessionId': coord.session_id,
        'remaining': coord._budget_remaining(store.db),
        'plannerEnabled': coord._planning_enabled(),
        'pending': bool(coord._planner_pending),
        'pendingRequestId': (coord._planner_pending or {}).get('requestId'),
        'notes': coord.state.get('limitations'),
    } for coord in coordinators]
    raise TimeoutError('real proposal preparation did not fan out all Phase B chunks: '
                       + json.dumps(diagnostics, default=str))


def slow_checkpoint(coord, planner, *, release_at=None, ready_count=0, accepted_at_entry=None):
    original = coord._persist
    state = dict(startedAt=None, endedAt=None, sawPlannerReady=False,
                 acceptedAtEntry=None)

    def persist(store):
        original(store)
        started = time.monotonic()
        state['startedAt'] = started
        state['acceptedAtEntry'] = (accepted_at_entry() if accepted_at_entry else None)
        if release_at is not None:
            if not planner.wait_computed(max(1, min(2, ready_count)), timeout=90):
                raise TimeoutError('planner worker did not finish computation before checkpoint gate')
            while time.monotonic()-started < release_at:
                time.sleep(.001)
            planner.release_computed_chunks.set()
            if not planner.wait_ready(ready_count, timeout=90):
                raise TimeoutError('completed planner records were not ready during checkpoint')
            state['sawPlannerReady'] = True
            while time.monotonic()-started < .250:
                time.sleep(.001)
        else:
            time.sleep(.250)
        state['endedAt'] = time.monotonic()

    coord._persist = persist
    return state


def slow_campaign_reporting(opt, accepted_at_entry):
    original = opt._campaign_fleet_status
    state = dict(startedAt=None, endedAt=None, acceptedAtEntry=None)

    def report():
        state['startedAt'] = time.monotonic()
        state['acceptedAtEntry'] = accepted_at_entry()
        time.sleep(.250)
        result = original()
        state['endedAt'] = time.monotonic()
        return result

    opt._campaign_fleet_status = report
    return state


def run_ready_during_slow_portfolio(report):
    opt, store, battle_pool, planner = make_rig('fixed-ready-during-portfolio')
    try:
        routes = start_planners(opt, store, [0, 1])
        expected = len(routes)
        check('portfolio-case-planner-computation-finishes-before-dispatch',
              planner.wait_computed(expected), dict(gatedChunks=expected))

        # Encounter 2 has one real frozen paired job. Completing it through the real Evaluator
        # makes Coordinator.run_pass enter its actual evidence-driven portfolio refresh.
        for encounter_id, coord in opt._campaign_coordinators.items():
            if encounter_id not in (0, 1, 2):
                coord.state['idle'] = True
                coord._planning_dirty = False
        coord2 = opt._campaign_coordinators[2]
        dispatch.attach_frozen(coord2, store, jobs=2, budget=2)
        coord2._submission_quota = 2
        coord2._submission_started = 0
        submitted = coord2.dispatch_authorized_ready(store)
        battle_pool.complete_all()
        check('portfolio-case-real-battles-complete-through-evaluator',
              submitted == 2 and len(opt._community_evaluator.pending) == 2,
              dict(submitted=submitted, pending=len(opt._community_evaluator.pending)))

        portfolio = coord2._refresh_portfolio
        state = dict(startedAt=None, endedAt=None, resultReadyDuring=False,
                     acceptedAtEntry=None)

        def slow_portfolio(db):
            state['startedAt'] = time.monotonic()
            state['acceptedAtEntry'] = all(
                opt._campaign_coordinators[e]._planner_pending is None
                and bool(opt._campaign_coordinators[e]._cohort()) for e in (0, 1))
            if not planner.wait_computed(expected, timeout=90):
                raise TimeoutError('planner computation did not finish before portfolio callback')
            while time.monotonic()-state['startedAt'] < .240:
                time.sleep(.001)
            planner.release_computed_chunks.set()
            if not planner.wait_ready(expected, timeout=90):
                raise TimeoutError('planner results did not become ready during portfolio callback')
            state['resultReadyDuring'] = True
            while time.monotonic()-state['startedAt'] < .250:
                time.sleep(.001)
            result = portfolio(db)
            state['endedAt'] = time.monotonic()
            return result

        coord2._refresh_portfolio = slow_portfolio
        next_encounter = {}
        coordinator3 = opt._campaign_coordinators[3]
        original_run_pass = coordinator3.run_pass

        def record_next(*args, **kwargs):
            next_encounter['readyAccepted'] = all(
                opt._campaign_coordinators[e]._planner_pending is None
                and bool(opt._campaign_coordinators[e]._cohort()) for e in (0, 1))
            return original_run_pass(*args, **kwargs)

        coordinator3.run_pass = record_next
        opt._campaign_fleet_pass(store, running=True)
        events = [event for event in opt.__dict__.get('_planner_lifecycle_events', [])
                  if event.get('encounter') in (0, 1)]
        report['readyDuringSlowPortfolio'] = dict(
            portfolioMs=((state['endedAt']-state['startedAt'])*1000
                         if state['endedAt'] is not None else None),
            futureReadyDuringPortfolio=state['resultReadyDuring'],
            plannerResultsConsumed=len(events),
            acceptedBeforePortfolio=bool(state['acceptedAtEntry']),
            acceptedBeforeNextEncounter=bool(next_encounter.get('readyAccepted')))
        check('planner-results-ready-during-portfolio-consumed-before-next-encounter',
              state['resultReadyDuring'] and len(events) == 2
              and bool(next_encounter.get('readyAccepted')),
              report['readyDuringSlowPortfolio'])
    finally:
        planner.close(timeout=10)
        dispatch.base._close(opt, store)


def run_old_order_control(report):
    opt, store, _battle_pool, planner = make_rig('old-order')
    try:
        routes = start_planners(opt, store, [0])
        route = routes[0]
        expected = len(routes)
        check('old-order-control-gated-final-real-planner-chunk',
              planner.wait_computed(expected), dict(gatedChunks=expected,
                  totalChunks=int(route['chunkTotal'])))
        planner.release_computed_chunks.set()
        print('released old-order planner results', flush=True)
        check('old-order-control-results-ready-before-slow-stage', planner.wait_ready(expected),
              planner.status())

        seen = slow_checkpoint(opt._campaign_coordinators[1], planner)
        original = opt._harvest_planning
        # This is the former ordering: only the main-loop harvest after the whole fleet pass.
        opt._harvest_planning = lambda *_args, **_kwargs: 0
        started = time.monotonic()
        opt._campaign_fleet_pass(store, running=True)
        delayed = (opt._campaign_coordinators[0]._planner_pending is not None
                   and opt._campaign_coordinators[0].state.get('cohort') == [])
        opt._harvest_planning = original
        original(store)
        events = opt.__dict__.get('_planner_lifecycle_events') or []
        event = next((row for row in events if row.get('requestId') == route['requestId']), {})
        delay_ms = ((event.get('hostHarvestStartAt') or 0)
                    - (event.get('futureReadyAt') or 0))*1000
        report['oldOrderControl'] = dict(
            fleetPassMs=(time.monotonic()-started)*1000,
            slowCheckpointMs=(seen['endedAt']-seen['startedAt'])*1000,
            remainedPendingThroughFleet=delayed,
            futureReadyToHarvestMs=delay_ms)
        check('old-order-control-reproduces-result-waiting-through-fleet',
              delayed and delay_ms >= 200, report['oldOrderControl'])
    finally:
        planner.close(timeout=10)
        dispatch.base._close(opt, store)


def run_fixed_order_ready_before_slow_stage(report):
    opt, store, battle_pool, planner = make_rig('fixed-ready-first')
    try:
        routes = start_planners(opt, store, [0, 1])
        expected = len(routes)
        total_worker_tasks = sum(1+int(route['chunkTotal']) for route in routes)
        check('fixed-order-two-real-planner-groups-fan-out',
              planner.wait_computed(expected), dict(gatedChunks=expected,
                  totalWorkerTasks=total_worker_tasks))
        planner.release_computed_chunks.set()
        print('released fixed-order planner results', flush=True)
        check('fixed-order-multiple-results-become-ready-together', planner.wait_ready(expected),
              planner.status())

        slow = slow_campaign_reporting(opt, lambda: all(
            opt._campaign_coordinators[e]._planner_pending is None
            and bool(opt._campaign_coordinators[e]._cohort()) for e in (0, 1)))
        opt._campaign_fleet_pass(store, running=True)
        events = [event for event in opt.__dict__.get('_planner_lifecycle_events', [])
                  if event.get('encounter') in (0, 1)]
        samples = [row for row in opt.__dict__.get('_planner_lifecycle_samples', [])
                   if row.get('encounter') in (0, 1)]
        delays_ms = [((event.get('hostHarvestStartAt') or 0)
                      - (event.get('futureReadyAt') or 0))*1000
                     for event in events if event.get('hostHarvestStartAt') is not None
                     and event.get('futureReadyAt') is not None]
        normal_max = max(delays_ms, default=0.0)
        bound_ms = max(1.0, 2.0*normal_max)
        exp_map = {coord.encounter: [member['experimentId'] for member in coord._cohort()]
                   for coord in opt._campaign_coordinators.values() if coord.encounter in (0, 1)}
        counts = {}
        for key in opt._community_evaluator.pending:
            encounter_id = next((enc for enc, experiment_ids in exp_map.items()
                                 if key[0] in experiment_ids), None)
            counts[encounter_id] = counts.get(encounter_id, 0)+1
        experiment_ids = [experiment_id for ids in exp_map.values()
                          for experiment_id in ids]
        reservation_rows = list(store.db.execute(
            'SELECT experiment_id, candidate_id, seed_a, seed_b FROM ea_sample_link '
            'WHERE experiment_id IN (%s)' % ','.join('?' for _ in experiment_ids),
            experiment_ids)) if experiment_ids else []
        reservation_keys = [tuple(row) for row in reservation_rows]
        unique_reservations = (len(reservation_keys) == len(set(reservation_keys))
                               and len(opt._community_evaluator.pending) ==
                               len(set(opt._community_evaluator.pending)))
        unique_cohort_experiments = all(len(ids) == len(set(ids))
                                        for ids in exp_map.values())
        report['readyBeforeSlowStage'] = dict(
            plannerResults=len(events), workerTasks=len(samples), fastPathMaxMs=normal_max,
            derivedBoundMs=bound_ms, maxReadyToHarvestMs=max(delays_ms, default=None),
            slowReportingMs=(slow['endedAt']-slow['startedAt'])*1000,
            planAvailableBeforeSlowReporting=bool(slow['acceptedAtEntry']),
            submissionsByEncounter=counts,
            poolPending=len(opt._community_evaluator.pending),
            reservedRows=len(reservation_rows),
            uniqueReservations=unique_reservations,
            uniqueCohortExperiments=unique_cohort_experiments)
        check('ready-planners-consumed-before-slow-reporting',
              report['readyBeforeSlowStage']['planAvailableBeforeSlowReporting'],
              report['readyBeforeSlowStage'])
        check('planner-ready-to-harvest-meets-derived-normal-fast-path-bound',
              bool(delays_ms) and max(delays_ms) <= bound_ms,
              dict(maxReadyToHarvestMs=max(delays_ms, default=None), boundMs=bound_ms,
                   samples=len(delays_ms)))
        check('simultaneous-planner-results-preserve-five-five-fairness',
              counts == {0: 5, 1: 5} and len(opt._community_evaluator.pending) == 10,
              counts)
        check('planner-freeze-and-dispatch-create-no-duplicate-reservations',
              unique_reservations and unique_cohort_experiments,
              exp_map)

        # Revisit the real fleet once. The active frozen cohorts must not be recreated and every
        # battle seed already submitted remains a unique reservation.
        prior_experiments = sum(int(store.db.execute(
            'SELECT COUNT(*) FROM ea_experiment WHERE session_id=?',
            (coord.session_id,)).fetchone()[0]) for coord in opt._campaign_coordinators.values())
        prior_submits = len(battle_pool.jobs)
        opt._campaign_fleet_pass(store, running=True)
        after_experiments = sum(int(store.db.execute(
            'SELECT COUNT(*) FROM ea_experiment WHERE session_id=?',
            (coord.session_id,)).fetchone()[0]) for coord in opt._campaign_coordinators.values())
        check('repeat-fleet-pass-creates-no-duplicate-plans-or-battle-submits',
              after_experiments == prior_experiments and
              len(battle_pool.jobs) == prior_submits,
              dict(experimentsBefore=prior_experiments, experimentsAfter=after_experiments,
                   submitsBefore=prior_submits, submitsAfter=len(battle_pool.jobs)))
    finally:
        planner.close(timeout=10)
        dispatch.base._close(opt, store)


def run_ready_during_slow_encounter(report):
    opt, store, battle_pool, planner = make_rig('fixed-ready-during-stage')
    try:
        routes = start_planners(opt, store, [0, 1])
        expected = len(routes)
        total_worker_tasks = sum(1+int(route['chunkTotal']) for route in routes)
        check('during-stage-planner-worker-computation-finishes',
              planner.wait_computed(expected), dict(gatedChunks=expected,
                  totalWorkerTasks=total_worker_tasks))
        print('starting slow coordinator release case', flush=True)
        slow = slow_checkpoint(opt._campaign_coordinators[2], planner,
                               release_at=.240, ready_count=expected,
                               accepted_at_entry=lambda: all(
                                   opt._campaign_coordinators[e]._planner_pending is None
                                   for e in (0, 1)))
        reached_next = {}
        coordinator3 = opt._campaign_coordinators[3]
        original_run_pass = coordinator3.run_pass

        def record_next(*args, **kwargs):
            reached_next['readyAccepted'] = all(
                opt._campaign_coordinators[e]._planner_pending is None
                and bool(opt._campaign_coordinators[e]._cohort()) for e in (0, 1))
            return original_run_pass(*args, **kwargs)

        coordinator3.run_pass = record_next
        opt._campaign_fleet_pass(store, running=True)
        events = [event for event in opt.__dict__.get('_planner_lifecycle_events', [])
                  if event.get('encounter') in (0, 1)]
        samples = [row for row in opt.__dict__.get('_planner_lifecycle_samples', [])
                   if row.get('encounter') in (0, 1)]
        delays_ms = [((event.get('hostHarvestStartAt') or 0)
                      - (event.get('futureReadyAt') or 0))*1000
                     for event in events if event.get('hostHarvestStartAt') is not None
                     and event.get('futureReadyAt') is not None]
        report['readyDuringSlowEncounter'] = dict(
            slowCheckpointMs=(slow['endedAt']-slow['startedAt'])*1000,
            resultBecameReadyDuringCheckpoint=slow['sawPlannerReady'],
            plannerResultsConsumed=len(events),
            readyToHarvestMaxMs=max(delays_ms, default=None),
            acceptedBeforeNextEncounter=bool(reached_next.get('readyAccepted')),
            submissionsByEncounter={enc: sum(1 for job in battle_pool.jobs
                if job.get('encounter') == enc)
                for enc in (0, 1)})
        check('planner-results-becoming-ready-during-coordinator-work-consumed-at-next-safe-boundary',
              slow['sawPlannerReady'] and len(events) == 2
              and bool(reached_next.get('readyAccepted')),
              report['readyDuringSlowEncounter'])
        check('slow-coordinator-does-not-block-already-ready-work-for-later-encounters',
              bool(reached_next.get('readyAccepted')), reached_next)
        check('planner-lifecycle-splits-worker-and-host-ready-intervals',
              len(samples) == total_worker_tasks and all(
                  row.get('queuedAt') is not None and row.get('workerStartAt') is not None
                  and row.get('workerEndAt') is not None and row.get('futureReadyAt') is not None
                  and row.get('hostHarvestStartAt') is not None for row in samples),
              dict(tasks=len(samples), expected=total_worker_tasks))
    finally:
        planner.close(timeout=10)
        dispatch.base._close(opt, store)


def frozen_search_signature(opt):
    signature = {}
    for encounter_id in (0, 1):
        coord = opt._campaign_coordinators[encounter_id]
        signature[encounter_id] = [dict(
            proposalId=member.get('proposalId'),
            candidateId=member.get('candidateId'),
            referenceId=member.get('referenceId'),
            planned=member.get('planned'),
            planDigest=member.get('planDigest'),
            jobs=[(job.get('candidateId'), tuple(job.get('seedPair') or ()), job.get('arm'))
                  for job in member.get('jobs') or ()])
            for member in coord._cohort()]
    return signature


def run_search_semantics_parity(report):
    outcomes = {}
    for mode in ('old-order', 'ready-first'):
        opt, store, _battle_pool, planner = make_rig('semantics-' + mode)
        try:
            routes = start_planners(opt, store, [0, 1])
            expected = len(routes)
            if not planner.wait_computed(expected):
                raise TimeoutError('planner chunks did not compute for ' + mode)
            planner.release_computed_chunks.set()
            if not planner.wait_ready(expected):
                raise TimeoutError('planner results did not become ready for ' + mode)
            if mode == 'old-order':
                original_harvest = opt._harvest_planning
                opt._harvest_planning = lambda *_args, **_kwargs: 0
                try:
                    opt._campaign_fleet_pass(store, running=True)
                finally:
                    opt._harvest_planning = original_harvest
                original_harvest(store, eligible_coordinators=[
                    opt._campaign_coordinators[0], opt._campaign_coordinators[1]])
            else:
                opt._campaign_fleet_pass(store, running=True)
            outcomes[mode] = frozen_search_signature(opt)
        finally:
            planner.close(timeout=10)
            dispatch.base._close(opt, store)
    same = outcomes['old-order'] == outcomes['ready-first']
    digests = {mode: hashlib.sha256(json.dumps(value, sort_keys=True,
        separators=(',', ':')).encode('utf-8')).hexdigest() for mode, value in outcomes.items()}
    report['searchSemanticsParity'] = dict(
        exactFrozenCandidatesPlansSeedsAndBudgetsPreserved=same,
        digests=digests, oldOrder=outcomes['old-order'], readyFirst=outcomes['ready-first'])
    check('earlier-planner-consumption-preserves-exact-candidates-plans-seeds-and-budgets',
          same, dict(digests=digests,
                     plansByEncounter={encounter: len(plans)
                                        for encounter, plans in outcomes['ready-first'].items()}))


def main():
    root = Path(tempfile.gettempdir())/'ka-optimizer-ready-planner-results'
    root.mkdir(parents=True, exist_ok=True)
    dispatch.base.WORK_DIR = root
    dispatch.base.CREATED_DIRS.clear()
    report = dict(schema='ka-optimizer-ready-planner-results-1', checks=[])
    started = time.perf_counter()
    try:
        run_old_order_control(report)
        run_fixed_order_ready_before_slow_stage(report)
        run_ready_during_slow_encounter(report)
        run_ready_during_slow_portfolio(report)
        run_search_semantics_parity(report)
    finally:
        report['elapsedSeconds'] = time.perf_counter()-started
        for temp_path in dispatch.base.CREATED_DIRS:
            import shutil
            shutil.rmtree(temp_path, ignore_errors=True)
    failures = [row['name'] for row in RESULTS if not row['passed']]
    report['checks'] = RESULTS
    report['failedChecks'] = failures
    report['passed'] = not failures
    output = Path(tempfile.gettempdir())/'ka-optimizer-ready-planner-results-report.json'
    output.write_text(json.dumps(report, indent=2, sort_keys=True)+'\n', encoding='utf-8')
    print('\nSUMMARY '+json.dumps(dict(passed=report['passed'], checks=len(RESULTS),
        failures=failures, elapsedSeconds=round(report['elapsedSeconds'], 2)), sort_keys=True))
    print('REPORT '+str(output))
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
