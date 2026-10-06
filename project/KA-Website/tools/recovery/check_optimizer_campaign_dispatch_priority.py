from __future__ import annotations

import argparse
import json
import math
import shutil
import statistics
import sys
import tempfile
import time
from concurrent.futures import Future
from pathlib import Path

SOURCE = Path(r'C:\Users\anisb\OneDrive\Desktop\replit kingdom adventures - Copy\KA-Website\tools\recovery')
sys.path.insert(0, str(SOURCE))

import check_optimizer_evaluator_slots as base
import strategy_experiment_store as ledger
import strategy_optimizer as so

ENCOUNTERS = list(range(20))
REQUESTED_WORKERS = 12
PLANNERS = 2
BATTLE_SLOTS = 10


class TracePool:
    """Only the battle worker is controlled; the real Evaluator and Coordinator remain in use."""

    supports_job_trace = False

    def __init__(self, workers):
        self.workers = workers
        self.jobs = []
        self.submit_events = []
        self.events = []
        self.evaluator = None
        self.coordinators = {}
        self.current_iteration = 0
        self.available_at = None
        self.available_free = 0
        self.available_authorized = 0
        self.delays = []
        self.delays_by_iteration = {}
        self.harvest_events = []
        self.interval_rows = []
        self.identity_by_pair = {}
        self._sequence = 0

    def submit(self, _function, scenario, seeds):
        started = time.perf_counter()
        future = Future()
        candidate_id = self.identity_by_pair.get(tuple(seeds))
        record = dict(future=future, encounter=int(scenario.get('encounterId', -1)),
                      candidateId=candidate_id, seedPair=tuple(int(v) for v in seeds),
                      scenario=scenario, submittedAt=started)
        self.jobs.append(record)
        self._sequence += 1
        live = sum(not job['future'].done() for job in self.jobs)
        event = dict(kind='pool_submit', at=started, iteration=self.current_iteration,
                     encounter=record['encounter'], freeSlots=max(0, self.workers-live+1),
                     authorizedUnsent=max(0, self.available_authorized),
                     evaluatorPending=(len(self.evaluator.pending) if self.evaluator else None),
                     poolExecuting=live, stage='pool_submit', sequence=self._sequence,
                     candidateId=record['candidateId'], seedPair=list(record['seedPair']))
        self.submit_events.append(event)
        self.events.append(event)
        if self.available_at is not None and self.available_authorized > 0:
            delay = max(0.0, started-self.available_at)
            self.delays.append(delay)
            self.delays_by_iteration.setdefault(self.current_iteration, []).append(delay)
            self.available_authorized = max(0, self.available_authorized-1)
            self.available_free = max(0, self.available_free-1)
        return future

    def set_evaluator(self, evaluator):
        self.evaluator = evaluator

    def set_coordinators(self, coordinators):
        self.coordinators = coordinators

    def register_frozen(self, candidate_id, job_list):
        for job in job_list:
            self.identity_by_pair[tuple(job['seedPair'])] = str(candidate_id)

    def _authorized_count(self, db):
        return sum(coord._authorized_dispatch_count(db) for coord in self.coordinators.values())

    def begin_iteration(self, db, harvested_count):
        self.current_iteration += 1
        now = time.perf_counter()
        pending = len(self.evaluator.pending)
        free = max(0, self.workers-pending)
        authorized = self._authorized_count(db)
        self.available_at = now if free > 0 and authorized > 0 else None
        self.available_free = free
        self.available_authorized = authorized
        row = dict(kind='authorized_available', at=now, iteration=self.current_iteration,
                   freeSlots=free, authorizedUnsent=authorized, evaluatorPending=pending,
                   poolExecuting=self.snapshot()['executing'], coordinatorStage='after_harvest',
                   harvestedResults=int(harvested_count))
        self.events.append(row)
        self._iteration_start = row
        self._iteration_submit_start = len(self.submit_events)

    def end_iteration(self):
        now = time.perf_counter()
        start = getattr(self, '_iteration_start', None)
        if start is None:
            return
        made = len(self.submit_events)-self._iteration_submit_start
        end_row = dict(kind='iteration_end', at=now, iteration=self.current_iteration,
                       freeSlots=max(0, self.workers-len(self.evaluator.pending)),
                       authorizedUnsent=self._authorized_count(self._db),
                       evaluatorPending=len(self.evaluator.pending),
                       poolExecuting=self.snapshot()['executing'], submissionsMade=made)
        self.events.append(end_row)
        for row in self.events:
            if row.get('kind') == 'coordinator_stage' and row.get('iteration') == self.current_iteration:
                self.mark_stage_delayed_submissions(row)
        free = int(start['freeSlots'])
        authorized = int(start['authorizedUnsent'])
        cursor = float(start['at'])
        overlap = 0.0
        for event in self.submit_events[self._iteration_submit_start:]:
            if event['iteration'] != self.current_iteration:
                continue
            at = float(event['at'])
            if free > 0 and authorized > 0:
                overlap += max(0.0, at-cursor)
            cursor = at
            free = max(0, free-1)
            authorized = max(0, authorized-1)
        if free > 0 and authorized > 0:
            overlap += max(0.0, now-cursor)
        self.interval_rows.append(dict(iteration=self.current_iteration,
            wallSeconds=max(0.0, now-float(start['at'])), freeAndAuthorizedSeconds=overlap,
            freeAtHarvest=int(start['freeSlots']), authorizedAtHarvest=int(start['authorizedUnsent']),
            evaluatorPendingAtHarvest=int(start['evaluatorPending']),
            poolExecutingAtHarvest=int(start['poolExecuting']),
            submissionsMade=made,
            coordinatorStagesEntered=[row['stage'] for row in self.events
                if row.get('kind') == 'coordinator_stage'
                and row.get('iteration') == self.current_iteration],
            coordinatorStageDurationsSeconds=[dict(stage=row['stage'],
                durationSeconds=row['durationSeconds'],
                freeSlotsAtStart=row['freeBattleSlotsAtStart'],
                authorizedCompatibleUnsentAtStart=row['authorizedCompatibleUnsentAtStart'],
                evaluatorPendingAtStart=row['evaluatorPendingAtStart'],
                poolExecutingAtStart=row['poolExecutingAtStart'],
                submissionsDelayedByStage=row['submissionsDelayedByStage'])
                for row in self.events if row.get('kind') == 'coordinator_stage'
                and row.get('iteration') == self.current_iteration],
            freeAtIterationEnd=int(end_row['freeSlots']),
            authorizedAtIterationEnd=int(end_row['authorizedUnsent']),
            evaluatorPendingAtIterationEnd=int(end_row['evaluatorPending']),
            poolExecutingAtIterationEnd=int(end_row['poolExecuting']),
            harvestedResults=int(start['harvestedResults'])))

    def record_stage(self, name, encounter, started, ended, before, after):
        row = dict(kind='coordinator_stage', iteration=self.current_iteration,
                   stage=name, encounter=int(encounter),
                   startAt=started, durationSeconds=max(0.0, ended-started),
                   freeBattleSlotsAtStart=int(before['freeSlots']),
                   authorizedCompatibleUnsentAtStart=int(before['authorizedUnsent']),
                   evaluatorPendingAtStart=int(before['evaluatorPending']),
                   poolExecutingAtStart=int(before['poolExecuting']),
                   submissionsBefore=int(before['submissionsBefore']),
                   submissionsAfter=int(after['submissionsAfter']),
                   submissionsDelayedByStage=0)
        self.events.append(row)
        return row

    def mark_stage_delayed_submissions(self, row):
        row['submissionsDelayedByStage'] = sum(
            1 for event in self.submit_events
            if event['at'] > row['startAt']+row['durationSeconds']
            and event['iteration'] == self.current_iteration)

    def snapshot(self):
        executing = sum(not job['future'].done() for job in self.jobs)
        return dict(queued=0, executing=executing, available=max(0, self.workers-executing))

    def stage_state(self):
        return dict(freeSlots=max(0, self.workers-len(self.evaluator.pending)),
                    authorizedUnsent=max(0, self.available_authorized),
                    evaluatorPending=len(self.evaluator.pending),
                    poolExecuting=self.snapshot()['executing'],
                    submissionsBefore=len(self.submit_events),
                    submissionsAfter=len(self.submit_events))

    def complete_one(self):
        for job in self.jobs:
            if not job['future'].done():
                job['completedAt'] = time.perf_counter()
                job['future'].set_result(dict(
                    seeds=list(job['seedPair']), verdict=1, resultBackend='native',
                    rewardOutcome=dict(pendingChests=1), elapsedSeconds=0.01,
                    cpuSeconds=0.0, digest='dispatch-priority-fixture'))
                return 1
        return 0

    def complete_all(self):
        return sum(self.complete_one() for _ in range(
            sum(not job['future'].done() for job in self.jobs)))

    def shutdown(self, wait=True, cancel_futures=False):
        return None

    def terminate_workers(self):
        return None


def make_pool():
    pool = TracePool(BATTLE_SLOTS)
    base.CURRENT_POOL[0] = pool
    return pool


def make_rig(tag):
    pool = make_pool()
    opt, store = base.build_twenty_campaign(tag, pool)
    pool.set_evaluator(opt._community_evaluator)
    pool.set_coordinators(opt._campaign_coordinators)
    trace_evaluator(opt, store, pool)
    trace_coordinator_stages(opt, pool)
    return opt, store, pool


def trace_evaluator(opt, store, pool):
    evaluator = opt._community_evaluator
    original_harvest = evaluator.harvest

    def harvest(db):
        entries = original_harvest(db)
        harvested_at = time.perf_counter()
        for entry in entries:
            pair = tuple(entry.get('seeds') or ())
            job = next((row for row in pool.jobs
                        if row.get('candidateId') == entry.get('candidateId')
                        and tuple(row.get('seedPair') or ()) == pair), None)
            if job is not None:
                job['harvestedAt'] = harvested_at
            pool.harvest_events.append(dict(experimentId=entry.get('experimentId'),
                candidateId=entry.get('candidateId'), seedPair=list(pair), at=harvested_at,
                accepted=bool(entry.get('accepted'))))
        pool._db = db
        pool.begin_iteration(db, len(entries))
        return entries

    evaluator.harvest = harvest


def trace_coordinator_stages(opt, pool):
    for coord in opt._campaign_coordinators.values():
        original = coord._measure_stage
        def measured(name, function, *args, _coord=coord, _original=original):
            started = time.perf_counter()
            before = pool.stage_state()
            result = _original(name, function, *args)
            ended = time.perf_counter()
            after = pool.stage_state()
            row = pool.record_stage(str(name), _coord.encounter,
                                    started, ended, before, after)
            pool.mark_stage_delayed_submissions(row)
            return result
        coord._measure_stage = measured


def attach_frozen(coord, store, *, jobs, budget=None):
    """Create an immutable intent and runtime cohort containing the exact authorized job list."""
    jobs = int(jobs)
    budget = jobs if budget is None else int(budget)
    encounter = int(coord.encounter)
    scenario = base._fixture_scenario(encounter)
    candidate_id = 'dispatch-%d-c0' % encounter
    seed_base = 1_300_000_000 + encounter*100_000
    job_list = []
    for index in range(jobs):
        seed_a = seed_base + index*2
        job_list.append(dict(candidateId=candidate_id, seedPair=[seed_a, seed_a+1],
                             arm='candidate'))
    mechanics_revision = coord._mechanics_revision()
    encounter_revision = coord._encounter_revision(scenario)
    compatibility = coord._compatibility()
    policy = dict(coord.state.get('policy') or {})
    intent = dict(scope='community', owner='community', ownerShare=1.0,
        parentId=None, referenceId=None, purpose='improvement', sessionId=coord.session_id,
        planned_budget=budget, stopping=dict(rule='dispatch-priority-fixture', maxRuns=budget),
        policy=policy, mechanicsRevision=mechanics_revision,
        encounterRevision=encounter_revision, engineRevision=coord.revision,
        compatibility=compatibility, measurementWindow='development',
        jobs=list(job_list), observedScenarios={candidate_id: scenario})
    experiment_id = ledger.create_experiment(store.db, intent)
    member = dict(experimentId=experiment_id, candidateId=candidate_id, referenceId=None,
        purpose='improvement', owner='community', ownerShare=1.0, planned=jobs,
        plannedPairs=max(1, jobs//2), reserved=0, completed=0,
        proposalId='dispatch-priority-fixture', parentId=None, jobs=list(job_list),
        observedCandidate=scenario, observedReference=None, policy=policy, blocked=False,
        compatibility=compatibility, mechanicsRevision=mechanics_revision,
        engineRevision=coord.revision, encounterRevision=encounter_revision)
    coord.state['cohort'] = [member]
    coord.state['current'] = member
    coord.state['idle'] = False
    coord._planning_dirty = False
    coord._db_ref = store.db
    coord._initialized_db = store.db
    coord.evaluator.pool.register_frozen(candidate_id, job_list)
    return experiment_id, candidate_id, scenario, job_list


def event_state(opt, store, pool, coord):
    evaluator = opt._community_evaluator
    return dict(freeSlots=max(0, evaluator.workers-len(evaluator.pending)),
        authorizedUnsent=coord._authorized_dispatch_count(store.db),
        evaluatorPending=len(evaluator.pending), poolExecuting=pool.snapshot()['executing'],
        submissionsBefore=len(pool.submit_events),
        submissionsAfter=len(pool.submit_events))


def install_slow_stage(opt, store, pool, coord, name, delay=0.250):
    if name == 'planning':
        target = coord
        attr = '_advance'
        original = getattr(coord, attr)
        def callback(db, local_store):
            started = time.perf_counter()
            before = event_state(opt, store, pool, coord)
            time.sleep(delay)
            ended = time.perf_counter()
            after = event_state(opt, store, pool, coord)
            row = pool.record_stage('planning', coord.encounter, started, ended, before, after)
            pool.mark_stage_delayed_submissions(row)
            return None
        setattr(target, attr, callback)
    elif name == 'portfolio':
        target = coord
        attr = '_refresh_portfolio'
        original = getattr(coord, attr)
        def callback(db):
            started = time.perf_counter()
            before = event_state(opt, store, pool, coord)
            time.sleep(delay)
            ended = time.perf_counter()
            after = event_state(opt, store, pool, coord)
            row = pool.record_stage('portfolio', coord.encounter, started, ended, before, after)
            pool.mark_stage_delayed_submissions(row)
            return None
        setattr(target, attr, callback)
    elif name == 'status':
        target = opt
        attr = '_campaign_fleet_status'
        original = getattr(opt, attr)
        def callback(*args, **kwargs):
            started = time.perf_counter()
            before = event_state(opt, store, pool, coord)
            time.sleep(delay)
            result = original(*args, **kwargs)
            ended = time.perf_counter()
            after = event_state(opt, store, pool, coord)
            row = pool.record_stage('status', coord.encounter, started, ended, before, after)
            pool.mark_stage_delayed_submissions(row)
            return result
        setattr(target, attr, callback)
    else:
        raise ValueError(name)


def check(report, name, condition, detail=None):
    passed = bool(condition)
    row = dict(name=name, passed=passed)
    if detail is not None:
        row['detail'] = detail
    report['checks'].append(row)
    print(('PASS ' if passed else 'FAIL ')+name+((' :: '+json.dumps(detail, sort_keys=True)) if detail is not None else ''))
    return passed


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    index = max(0, min(len(ordered)-1, math.ceil(fraction*len(ordered))-1))
    return ordered[index]


def run_worker_split(report):
    opt = so.Optimizer.__new__(so.Optimizer)
    opt._workers = REQUESTED_WORKERS
    planners = opt._planner_worker_count(REQUESTED_WORKERS)
    battles = opt._battle_worker_count(REQUESTED_WORKERS)
    check(report, 'requested-12-splits-to-2-planners-and-10-battle-slots',
          (planners, battles) == (PLANNERS, BATTLE_SLOTS),
          dict(requested=REQUESTED_WORKERS, planners=planners, battleWorkers=battles))


def run_fast_path_benchmark(report):
    opt, store, pool = make_rig('dispatch-fast-path')
    try:
        target = opt._campaign_coordinators[0]
        experiment, candidate, _scenario, jobs = attach_frozen(target, store, jobs=120, budget=120)
        frozen_pairs = [(candidate, tuple(job['seedPair'])) for job in jobs]
        began = time.perf_counter()
        active_worker_seconds = 0.0
        active_wall_seconds = 0.0
        for cycle in range(12):
            if cycle:
                pool.complete_all()
            pass_started = time.perf_counter()
            opt._campaign_fleet_pass(store, running=True)
            pool.end_iteration()
            if cycle == 0:
                first_pass_ms = (time.perf_counter()-pass_started)*1000
            hold_started = time.perf_counter()
            executing = pool.snapshot()['executing']
            time.sleep(0.002)
            held = time.perf_counter()-hold_started
            active_worker_seconds += executing*held
            active_wall_seconds += held
        elapsed = time.perf_counter()-began
        submitted_pairs = [(job['candidateId'], job['seedPair'])
                           for job in pool.jobs]
        expected_pairs = [(cid, pair) for cid, pair in frozen_pairs]
        exact_work = (len(pool.jobs) == 120 and
                      submitted_pairs == expected_pairs and
                      len(set((row[0], tuple(row[1])) for row in submitted_pairs)) == 120)
        delays_ms = [value*1000 for value in pool.delays]
        fast_p95 = percentile(delays_ms, .95) or 0.0
        per_iteration_p95 = [percentile([value*1000 for value in values], .95)
                             for values in pool.delays_by_iteration.values() if values]
        normal_pass_p95_max = max(per_iteration_p95, default=fast_p95)
        bound_ms = max(1.0, normal_pass_p95_max*2.0)
        overlap_seconds = sum(row['freeAndAuthorizedSeconds'] for row in pool.interval_rows)
        interval_seconds = sum(row['wallSeconds'] for row in pool.interval_rows)
        overlap_pct = 100.0*overlap_seconds/interval_seconds if interval_seconds else 0.0
        result = dict(submitted=len(pool.jobs), frozenJobs=120, firstPassSubmissions=10,
            firstPassSeconds=first_pass_ms/1000, authorizedToSubmitP50Ms=percentile(delays_ms, .50),
            authorizedToSubmitP95Ms=fast_p95, normalPassP95MaxMs=normal_pass_p95_max,
            regressionBoundMs=bound_ms,
            regressionBoundRationale='Twice the worst measured per-pass p95 from the normal fast path; a 1 ms floor covers timer/scheduler granularity.',
            meanActiveBattleWorkers=(active_worker_seconds/active_wall_seconds
                                     if active_wall_seconds else 0.0),
            percentTimeFreeSlotsCoexistWithAuthorizedQueue=overlap_pct,
            harnessDispatchJobsPerSecond=len(pool.jobs)/elapsed,
            harnessElapsedSeconds=elapsed, uniqueFrozenSubmissions=exact_work)
        report['performance'] = result
        report['iterationRecords'] = list(pool.interval_rows)
        check(report, 'one-encounter-has-120-frozen-authorized-jobs',
              len(ledger.experiment_intent(store.db, experiment).get('jobs') or []) == 120)
        check(report, 'deep-queue-fills-all-ten-slots-before-host-stages',
              len(pool.submit_events[:10]) == 10 and
              pool.submit_events[0]['at'] <= pool.submit_events[9]['at'] and
              len(opt._community_evaluator.pending) == 10,
              dict(firstBatch=len(pool.submit_events[:10]), pending=len(opt._community_evaluator.pending)))
        check(report, 'all-120-submissions-match-frozen-candidates-and-seeds', exact_work,
              dict(submitted=len(pool.jobs), unique=len(set(
                  (row[0], tuple(row[1])) for row in submitted_pairs))))
        check(report, 'fast-path-p95-is-the-derived-regression-reference',
              fast_p95 > 0 and bound_ms == max(1.0, normal_pass_p95_max*2.0)
              and max(delays_ms) <= bound_ms,
              dict(p95Ms=fast_p95, worstPassP95Ms=normal_pass_p95_max,
                   maxObservedMs=max(delays_ms), boundMs=bound_ms))
        check(report, 'deep-queue-throughput-is-positive-and-pool-stays-supplied',
              result['meanActiveBattleWorkers'] >= 9.9
              and result['harnessDispatchJobsPerSecond'] > 0,
              dict(meanActiveWorkers=result['meanActiveBattleWorkers'],
                   jobsPerSecond=result['harnessDispatchJobsPerSecond']))
        return result
    finally:
        base._close(opt, store)


def run_legacy_order_control(report):
    """Prove the real serial Coordinator.run_pass order still exhibits the measured bad sequence."""
    opt, store, pool = make_rig('dispatch-legacy-order-control')
    try:
        coord = opt._campaign_coordinators[0]
        _experiment, _candidate, _scenario, _jobs = attach_frozen(coord, store, jobs=120, budget=120)
        evaluator = opt._community_evaluator
        pool._db = store.db
        coord._submission_quota = None
        coord._submission_started = 0
        pool.begin_iteration(store.db, 0)
        slow_called = []
        def slow_advance(db, local_store):
            before = event_state(opt, store, pool, coord)
            started = time.perf_counter()
            time.sleep(.250)
            ended = time.perf_counter()
            after = event_state(opt, store, pool, coord)
            row = pool.record_stage('serial_planning_control', coord.encounter,
                                    started, ended, before, after)
            pool.mark_stage_delayed_submissions(row)
            slow_called.append(row)
            return None
        coord._advance = slow_advance
        started = time.perf_counter()
        coord.run_pass(store, running=True, harvested_entries=[],
                       recovery_snapshot=None)
        if slow_called:
            pool.mark_stage_delayed_submissions(slow_called[0])
        pool.end_iteration()
        elapsed = time.perf_counter()-started
        row = slow_called[0] if slow_called else {}
        reproduced = (row.get('freeBattleSlotsAtStart') == 10
                      and row.get('authorizedCompatibleUnsentAtStart') == 120
                      and row.get('submissionsDelayedByStage') == 10
                      and len(evaluator.pending) == 10
                      and bool(pool.delays)
                      and pool.delays[0]*1000 > report['performance']['regressionBoundMs'])
        report['defectControl'] = dict(stage='planning', injectedDelayMs=250,
            freeSlotsAtStageStart=row.get('freeBattleSlotsAtStart'),
            authorizedJobsAtStageStart=row.get('authorizedCompatibleUnsentAtStart'),
            submissionsDelayedByStage=row.get('submissionsDelayedByStage'),
            firstDispatchAfterMs=pool.delays[0]*1000 if pool.delays else None,
            regressionBoundMs=report['performance']['regressionBoundMs'])
        check(report, 'production-coordinator-control-reproduces-stage-before-dispatch',
              reproduced, report['defectControl'])
    finally:
        base._close(opt, store)


def run_deep_queue_status_order(report):
    opt, store, pool = make_rig('dispatch-deep-status-order')
    try:
        coord = opt._campaign_coordinators[0]
        _experiment, _candidate, _scenario, _jobs = attach_frozen(
            coord, store, jobs=120, budget=120)
        install_slow_stage(opt, store, pool, coord, 'status', delay=.250)
        opt._campaign_fleet_pass(store, running=True)
        event = next((row for row in pool.events
                      if row.get('kind') == 'coordinator_stage' and row.get('stage') == 'status'), None)
        dispatch_events = pool.submit_events[:10]
        passed = (len(dispatch_events) == BATTLE_SLOTS and event is not None
                  and max(row['at'] for row in dispatch_events) <= event['startAt']
                  and event['freeBattleSlotsAtStart'] == 0
                  and event['authorizedCompatibleUnsentAtStart'] == 110
                  and event['submissionsDelayedByStage'] == 0
                  and len(opt._community_evaluator.pending) == BATTLE_SLOTS
                  and pool.delays[-1]*1000 <= report['performance']['regressionBoundMs'])
        report['deepQueueOrdering'] = dict(initialAuthorized=120,
            firstBatchSubmissions=len(dispatch_events), statusDurationMs=(event or {}).get(
                'durationSeconds', 0)*1000,
            freeSlotsAtStatusStart=(event or {}).get('freeBattleSlotsAtStart'),
            authorizedAtStatusStart=(event or {}).get('authorizedCompatibleUnsentAtStart'),
            delayedByStatus=(event or {}).get('submissionsDelayedByStage'),
            lastFastDispatchDelayMs=(pool.delays[-1]*1000 if pool.delays else None),
            regressionBoundMs=report['performance']['regressionBoundMs'])
        check(report, '120-job-deep-queue-is-dispatched-before-slow-status', passed,
              report['deepQueueOrdering'])
    finally:
        base._close(opt, store)


def run_fairness_cases(report):
    two, store2, pool2 = make_rig('dispatch-two-encounters')
    try:
        active = [0, 1]
        exp_map = {}
        for encounter in active:
            exp_map[encounter] = attach_frozen(
                two._campaign_coordinators[encounter], store2, jobs=20, budget=20)[0]
        two._campaign_fleet_pass(store2, running=True)
        counts = {}
        for job in two._community_evaluator.pending:
            experiment = job[0]
            encounter = next(cid for cid, exp in exp_map.items() if exp == experiment)
            counts[encounter] = counts.get(encounter, 0)+1
        check(report, 'two-encounters-share-ten-slots-five-each',
              counts == {0: 5, 1: 5}, counts)
    finally:
        base._close(two, store2)

    ten, store10, pool10 = make_rig('dispatch-ten-encounters')
    try:
        exp_map = {}
        for encounter in range(10):
            exp_map[encounter] = attach_frozen(
                ten._campaign_coordinators[encounter], store10, jobs=10, budget=10)[0]
        ten._campaign_fleet_pass(store10, running=True)
        counts = {}
        for job in ten._community_evaluator.pending:
            experiment = job[0]
            encounter = next(cid for cid, exp in exp_map.items() if exp == experiment)
            counts[encounter] = counts.get(encounter, 0)+1
        check(report, 'ten-encounters-each-receive-one-of-ten-slots',
              counts == {encounter: 1 for encounter in range(10)}, counts)
    finally:
        base._close(ten, store10)


def run_battle_completion_midpass_harvest(report):
    """Complete a real evaluator future during one encounter and require refill before the next."""
    opt, store, pool = make_rig('dispatch-midpass-battle-harvest')
    try:
        owner = opt._campaign_coordinators[0]
        experiment, candidate, _scenario, jobs = attach_frozen(
            owner, store, jobs=120, budget=120)
        opt._campaign_fleet_pass(store, running=True)
        if len(owner.evaluator.pending) != BATTLE_SLOTS or len(pool.jobs) != BATTLE_SLOTS:
            raise AssertionError('fixture did not fill all ten production evaluator slots')

        # Derive the no-host-stall turnover bound from this same real Evaluator/Coordinator path.
        pool.complete_one()
        fast_job = pool.jobs[0]
        opt._campaign_fleet_pass(store, running=True)
        fast_replacement = pool.jobs[-1]
        fast_harvest_ms = (fast_job['harvestedAt']-fast_job['completedAt'])*1000
        fast_submit_ms = (fast_replacement['submittedAt']-fast_job['completedAt'])*1000
        fast_turnover_ms = max(fast_harvest_ms, fast_submit_ms)
        fast_bound_ms = max(1.0, fast_turnover_ms*2.0)
        # Keep the two controlled coordinator callbacks adjacent in the known encounter order.
        opt._campaign_fair_cursor = 0

        slow_encounter = opt._campaign_coordinators[1]
        original_slow = slow_encounter.run_pass
        completed = {}

        def complete_during_stage(*args, **kwargs):
            if not completed:
                stage_started = time.perf_counter()
                time.sleep(.015)
                pool.complete_one()
                completed['job'] = next(row for row in pool.jobs
                                         if row.get('completedAt') is not None
                                         and row.get('harvestedAt') is None)
                completed['at'] = completed['job']['completedAt']
                time.sleep(.235)
                completed['stageEndedAt'] = time.perf_counter()
                completed['stageDurationMs'] = (completed['stageEndedAt']-stage_started)*1000
            return original_slow(*args, **kwargs)

        slow_encounter.run_pass = complete_during_stage
        next_encounter = opt._campaign_coordinators[2]
        original_next = next_encounter.run_pass
        observed = {}

        def inspect_before_next(*args, **kwargs):
            sample = completed['job']
            owner_jobs = [row for row in pool.jobs if row.get('encounter') == owner.encounter]
            link = store.db.execute(
                'SELECT s.result FROM ea_sample_link l JOIN ea_sample s '
                'ON s.sample_key=l.sample_key WHERE l.experiment_id=? AND l.candidate_id=? '
                'AND l.seed_a=? AND l.seed_b=?',
                (experiment, candidate, sample['seedPair'][0], sample['seedPair'][1])).fetchone()
            observed.update(resultPersisted=bool(link and link[0] is not None),
                replacementSubmitted=(len(owner_jobs) == BATTLE_SLOTS+2),
                pending=len(owner.evaluator.pending), submissions=len(pool.jobs),
                completedToHarvestMs=((sample.get('harvestedAt')-sample.get('completedAt'))*1000
                                      if sample.get('harvestedAt') else None),
                completedToReplacementMs=((owner_jobs[-1].get('submittedAt')-sample.get(
                    'completedAt'))*1000 if sample.get('completedAt') else None),
                ownerSubmissions=len(owner_jobs),
                expectedNextSeed=jobs[BATTLE_SLOTS+1]['seedPair'],
                actualNextSeed=list(owner_jobs[-1]['seedPair'])
                if len(owner_jobs) > BATTLE_SLOTS+1 else None)
            return original_next(*args, **kwargs)

        next_encounter.run_pass = inspect_before_next
        opt._campaign_fleet_pass(store, running=True)
        report['midpassBattleHarvest'] = dict(
            injectedCoordinatorDelayMs=250,
            measuredFastHarvestMs=fast_harvest_ms,
            measuredFastSubmitMs=fast_submit_ms,
            derivedFastTurnoverBoundMs=fast_bound_ms,
            completedDuringEncounter=slow_encounter.encounter,
            requiredPersistenceBeforeNextEncounter=True,
            **observed)
        persisted_refill = (bool(observed.get('resultPersisted'))
                            and bool(observed.get('replacementSubmitted'))
                            and observed.get('pending') == BATTLE_SLOTS
                            and observed.get('actualNextSeed') == observed.get('expectedNextSeed'))
        check(report, 'battle-result-persists-and-refills-before-next-coordinator-work',
              persisted_refill,
              report['midpassBattleHarvest'])
        owner_pairs = [row['seedPair'] for row in pool.jobs
                       if row.get('encounter') == owner.encounter]
        expected_pairs = [job['seedPair'] for job in jobs[:BATTLE_SLOTS+2]]
        unique_pairs = len({tuple(row['seedPair']) for row in pool.jobs}) == len(pool.jobs)
        exact_seed_sequence = [list(pair) for pair in owner_pairs] == expected_pairs
        check(report, 'midpass-refill-keeps-frozen-seed-order-and-no-duplicate-reservation',
              exact_seed_sequence and unique_pairs,
              dict(submissions=len(pool.jobs), uniqueSeeds=len(
                  {tuple(row['seedPair']) for row in pool.jobs}),
                  exactOwnerSeedSequence=exact_seed_sequence,
                  uniquePairs=unique_pairs, ownerSeeds=owner_pairs,
                  expectedSeeds=expected_pairs))
        check(report, 'battle-completion-harvest-latency-is-bounded-by-current-stage-only',
              observed.get('completedToHarvestMs') is not None
              and observed['completedToHarvestMs'] <=
                  (completed['stageEndedAt']-completed['at'])*1000 + fast_bound_ms
              and observed.get('completedToReplacementMs') is not None
              and observed['completedToReplacementMs'] <=
                  (completed['stageEndedAt']-completed['at'])*1000 + fast_bound_ms,
              dict(completedToHarvestMs=observed.get('completedToHarvestMs'),
                   completedToReplacementMs=observed.get('completedToReplacementMs'),
                   remainingCurrentStageMs=(completed['stageEndedAt']-completed['at'])*1000,
                   derivedFastTurnoverBoundMs=fast_bound_ms))
    finally:
        base._close(opt, store)


def run_portfolio_scan_reuse(report):
    """Portfolio and incumbent evidence must share one compatible-outcome aggregation."""
    opt, store, _pool = make_rig('portfolio-scan-reuse')
    try:
        coord = opt._campaign_coordinators[0]
        coord.reference = 'scan-reuse-reference'
        original = coord._deduped_outcomes
        calls = []

        def counted(db, *, experiments=None):
            calls.append(tuple(experiments or ()))
            return original(db, experiments=experiments)

        coord._deduped_outcomes = counted
        first_incumbent = coord._refresh_portfolio(store.db)
        first_portfolio = json.loads(json.dumps(coord.state.get('portfolio') or [],
                                                sort_keys=True, default=str))
        first_calls = len(calls)
        second_incumbent = coord._refresh_portfolio(store.db)
        second_portfolio = json.loads(json.dumps(coord.state.get('portfolio') or [],
                                                 sort_keys=True, default=str))
        report['portfolioScanReuse'] = dict(
            scansPerRefresh=[first_calls, len(calls)-first_calls],
            outputStable=(first_portfolio == second_portfolio
                          and first_incumbent == second_incumbent),
            reference='scan-reuse-reference')
        check(report, 'portfolio-refresh-reuses-one-outcome-scan-for-incumbent',
              first_calls == 1 and len(calls)-first_calls == 1,
              report['portfolioScanReuse'])
        check(report, 'portfolio-scan-reuse-preserves-exact-visible-portfolio',
              report['portfolioScanReuse']['outputStable'],
              dict(entries=len(first_portfolio)))
    finally:
        base._close(opt, store)


def run_midpass_refill_fairness(report):
    opt, store, pool = make_rig('midpass-refill-fairness')
    try:
        experiments = {}
        frozen = {}
        for encounter in (0, 1):
            experiments[encounter], _candidate, _scenario, frozen[encounter] = attach_frozen(
                opt._campaign_coordinators[encounter], store, jobs=20, budget=20)
        opt._campaign_fleet_pass(store, running=True)
        before = {encounter: sum(job.get('encounter') == encounter for job in pool.jobs)
                  for encounter in (0, 1)}
        if before != {0: 5, 1: 5}:
            raise AssertionError('two-encounter setup did not begin at five submissions each: '
                                 + json.dumps(before))
        opt._campaign_fair_cursor = 0
        slow = opt._campaign_coordinators[2]
        original_slow = slow.run_pass
        completed_at = {}

        def complete_during_slow(*args, **kwargs):
            if not completed_at:
                time.sleep(.015)
                pool.complete_one()
                completed_at['job'] = next(row for row in pool.jobs
                    if row.get('completedAt') is not None and row.get('harvestedAt') is None)
                time.sleep(.235)
                completed_at['stageEnd'] = time.perf_counter()
            return original_slow(*args, **kwargs)

        slow.run_pass = complete_during_slow
        next_coord = opt._campaign_coordinators[3]
        original_next = next_coord.run_pass
        observed = {}

        def inspect(*args, **kwargs):
            counts = {encounter: sum(job.get('encounter') == encounter for job in pool.jobs)
                      for encounter in (0, 1)}
            observed.update(counts=counts,
                completedPersisted=completed_at['job'].get('harvestedAt') is not None,
                pending=len(opt._community_evaluator.pending))
            return original_next(*args, **kwargs)

        next_coord.run_pass = inspect
        opt._campaign_fleet_pass(store, running=True)
        report['midpassRefillFairness'] = dict(before=before, **observed)
        check(report, 'midpass-refill-keeps-multiple-encounters-fair',
              observed.get('counts') == {0: 6, 1: 5}
              and observed.get('pending') == BATTLE_SLOTS
              and observed.get('completedPersisted'),
              report['midpassRefillFairness'])
        exact_by_encounter = all(
            [list(row['seedPair']) for row in pool.jobs if row.get('encounter') == encounter]
            == [job['seedPair'] for job in frozen[encounter][:
                sum(row.get('encounter') == encounter for row in pool.jobs)] ]
            for encounter in (0, 1))
        check(report, 'midpass-fair-refill-keeps-each-encounters-frozen-seed-order',
              exact_by_encounter,
              dict(submitted={enc: [list(row['seedPair']) for row in pool.jobs
                                    if row.get('encounter') == enc] for enc in (0, 1)}))
    finally:
        base._close(opt, store)


def run_slow_stage_cases(report):
    results = {}
    for stage in ('planning', 'portfolio', 'status'):
        opt, store, pool = make_rig('dispatch-slow-'+stage)
        try:
            coord = opt._campaign_coordinators[0]
            experiment, _candidate, _scenario, _jobs = attach_frozen(
                coord, store, jobs=12, budget=12)
            if stage != 'planning':
                coord._advance = lambda _db, _store: None
            if stage != 'portfolio':
                coord._refresh_portfolio = lambda _db: None
            opt._campaign_fleet_pass(store, running=True)
            if len(opt._community_evaluator.pending) != 10:
                raise AssertionError('initial campaign pass did not fill ten real evaluator slots')
            pool.complete_one()
            install_slow_stage(opt, store, pool, coord, stage, delay=.250)
            before_submissions = len(pool.submit_events)
            opt._campaign_fleet_pass(store, running=True)
            event = next((row for row in pool.events
                          if row.get('kind') == 'coordinator_stage'
                          and row.get('stage') == stage), None)
            delayed = (event or {}).get('submissionsDelayedByStage')
            checks = (event is not None
                      and event['durationSeconds'] >= .245
                      and event['submissionsBefore'] > 0
                      and len(pool.submit_events)-before_submissions == 1
                      and delayed == 0
                      and pool.delays[-1]*1000 <= report['performance']['regressionBoundMs'])
            results[stage] = dict(durationMs=(event or {}).get('durationSeconds', 0)*1000,
                freeSlotsAtStart=(event or {}).get('freeBattleSlotsAtStart'),
                authorizedAtStart=(event or {}).get('authorizedCompatibleUnsentAtStart'),
                pendingAtStart=(event or {}).get('evaluatorPendingAtStart'),
                delayedSubmissions=delayed)
            check(report, 'slow-'+stage+'-runs-after-authorized-refill', checks, results[stage])
            check(report, 'slow-'+stage+'-preserves-exact-frozen-budget',
                  base._reserved_count(store, experiment) == 11
                  and len(opt._community_evaluator.pending) == 10,
                  dict(reserved=base._reserved_count(store, experiment),
                       pending=len(opt._community_evaluator.pending)))
        finally:
            base._close(opt, store)
    report['slowHostStages'] = results


def run_compatibility_and_boundaries(report):
    opt, store, pool = make_rig('dispatch-compatibility')
    try:
        bad = opt._campaign_coordinators[0]
        good = opt._campaign_coordinators[1]
        bad_experiment, _bad_candidate, _scenario, _bad_jobs = attach_frozen(
            bad, store, jobs=20, budget=20)
        good_experiment, _good_candidate, _scenario, _good_jobs = attach_frozen(
            good, store, jobs=20, budget=20)
        bad.constraints = {'scope-change': 'rejected'}
        opt._campaign_fleet_pass(store, running=True)
        bad_reserved = base._reserved_count(store, bad_experiment)
        good_reserved = base._reserved_count(store, good_experiment)
        check(report, 'compatibility-rejected-frozen-jobs-are-not-dispatched',
              bad_reserved == 0 and bool(bad.state['cohort'][0].get('blocked'))
              and good_reserved == 10,
              dict(rejectedReserved=bad_reserved, rejectedBlocked=bad.state['cohort'][0].get('blocked'),
                   compatibleReserved=good_reserved))
    finally:
        base._close(opt, store)

    opt, store, pool = make_rig('dispatch-frozen-boundary')
    try:
        coord = opt._campaign_coordinators[0]
        experiment, _candidate, _scenario, jobs = attach_frozen(coord, store, jobs=4, budget=4)
        frozen_pairs = {tuple(job['seedPair']) for job in jobs}
        opt._campaign_fleet_pass(store, running=True)
        first = [tuple(job['seedPair']) for job in pool.jobs]
        opt._campaign_fleet_pass(store, running=True)
        reserved = base._reserved_count(store, experiment)
        exact_boundary = len(pool.jobs) == 4 and set(first) == frozen_pairs and reserved == 4
        check(report, 'frozen-plan-boundary-never-fabricates-a-fifth-job',
              exact_boundary,
              dict(frozen=4, submitted=len(pool.jobs), reserved=reserved))
        check(report, 'no-duplicate-reservations-across-repeated-pass',
              len(set(first)) == len(first) and reserved == len(first), first)
    finally:
        base._close(opt, store)


def run_pause_resume(report):
    opt, store, pool = make_rig('dispatch-pause-resume')
    try:
        coord = opt._campaign_coordinators[0]
        experiment, _candidate, _scenario, jobs = attach_frozen(coord, store, jobs=12, budget=12)
        opt._campaign_fleet_pass(store, running=False)
        paused_ok = (len(opt._community_evaluator.pending) == 0
                     and base._reserved_count(store, experiment) == 0)
        paused_pending = len(opt._community_evaluator.pending)
        paused_reserved = base._reserved_count(store, experiment)
        opt._campaign_fleet_pass(store, running=True)
        resumed_ok = (len(opt._community_evaluator.pending) == 10
                      and base._reserved_count(store, experiment) == 10
                      and {tuple(job['seedPair']) for job in jobs[:10]}
                      == {tuple(job['seedPair']) for job in pool.jobs})
        check(report, 'pause-does-not-reserve-frozen-work', paused_ok,
              dict(pending=paused_pending, reserved=paused_reserved))
        check(report, 'resume-dispatches-exactly-the-same-frozen-work', resumed_ok,
              dict(pending=len(opt._community_evaluator.pending),
                   reserved=base._reserved_count(store, experiment)))
    finally:
        base._close(opt, store)


def run_confirmation_authorization_snapshot(report):
    """Blocked frozen confirmations must not trigger repeated holdout ledger scans."""
    opt, store, pool = make_rig('dispatch-confirmation-authorization-snapshot')
    try:
        confirmation_ids = set(range(17))
        expected_live_ids = {17, 18, 19}
        count_by_encounter = {}
        scan_seconds_by_encounter = {}
        for encounter_id, coord in opt._campaign_coordinators.items():
            experiment_id, candidate_id, _scenario, _jobs = attach_frozen(
                coord, store, jobs=1, budget=1)
            if encounter_id in confirmation_ids:
                planned = 64
                seeds = [[1_800_000_000 + encounter_id*1000 + index*2,
                          1_800_000_001 + encounter_id*1000 + index*2]
                         for index in range(planned)]
                confirmation_payload = dict(metric='finalEarned', runsPlanned=planned,
                    samplePlan=[dict(candidateId=candidate_id, seeds=seeds)])
                store.db.execute(
                    'INSERT INTO ea_confirmation(experiment_id,plan_key,payload,created_at) '
                    'VALUES(?,?,?,?)',
                    (experiment_id, 'stale-confirmation-%d' % encounter_id,
                     json.dumps(confirmation_payload, sort_keys=True), time.time()))
                store.db.executemany(
                    'INSERT INTO ea_holdout(experiment_id,candidate_id,seed_a,seed_b) '
                    'VALUES(?,?,?,?)',
                    [(experiment_id, candidate_id, pair[0], pair[1]) for pair in seeds])
                coord.state['cohort'] = []
                coord.state['current'] = None
                coord.state['confirmation'] = dict(experimentId=experiment_id, frozen=True,
                    ready=False, owner='improvement', nominee=candidate_id, planned=planned,
                    completed=0)
                # The frozen intent still carries the original engine revision. This reproduces
                # a stale confirmation that has reportable pending jobs but fails closed at submit.
                coord.revision = 'stale-' + str(coord.revision)

            original_count = coord._authorized_dispatch_count

            def counted(db, *, _id=encounter_id, _original=original_count):
                started = time.perf_counter()
                try:
                    return _original(db)
                finally:
                    scan_seconds_by_encounter[_id] = (
                        scan_seconds_by_encounter.get(_id, 0.0)
                        + time.perf_counter()-started)
                    count_by_encounter[_id] = count_by_encounter.get(_id, 0) + 1

            coord._authorized_dispatch_count = counted

        # The immutable confirmation/holdout fixtures above are written directly through SQLite.
        # Make that caller-owned setup durable before the real dispatcher can submit reserved work.
        store.db.commit()

        # Measure the legacy sweep's expected repeated snapshots against the exact same pending
        # holdout rows. These calls are read-only; reset instrumentation before the real pass.
        legacy_started = time.perf_counter()
        for encounter_id in range(17):
            coord = opt._campaign_coordinators[encounter_id]
            for _ in range(3):
                coord._authorized_dispatch_count(store.db)
        for encounter_id in expected_live_ids:
            coord = opt._campaign_coordinators[encounter_id]
            for _ in range(2):
                coord._authorized_dispatch_count(store.db)
        legacy_scan_seconds = time.perf_counter()-legacy_started
        count_by_encounter.clear()
        scan_seconds_by_encounter.clear()

        # Keep this case to one unchanged refill boundary. A real newly harvested planner result
        # intentionally starts a fresh authorization snapshot because it can create new work.
        opt._harvest_planning = lambda _store, **_kwargs: None
        # The shared fixture's trace wrapper starts an iteration by taking its own authorization
        # snapshot. Disable only that observer here so the assertion counts scheduler scans.
        pool.begin_iteration = lambda _db, _harvested: None
        priority_before = {
            encounter_id: float((coord.state.get('timings') or {}).get(
                'priorityDispatchSeconds') or 0.0)
            for encounter_id, coord in opt._campaign_coordinators.items()
        }
        opt._campaign_fleet_pass(store, running=True)
        current_priority_seconds = sum(
            float((coord.state.get('timings') or {}).get('priorityDispatchSeconds') or 0.0)
            - priority_before[encounter_id]
            for encounter_id, coord in opt._campaign_coordinators.items())

        # The old sweep re-dispatched each still-authorized stale confirmation once more after
        # seeing the three runnable encounters submit. Measure that exact extra stage cost now that
        # the first attempt has left their immutable holdout state unchanged.
        retry_before = {
            encounter_id: float((opt._campaign_coordinators[encounter_id].state.get(
                'timings') or {}).get('priorityDispatchSeconds') or 0.0)
            for encounter_id in confirmation_ids
        }
        legacy_retry_started = time.perf_counter()
        for encounter_id in confirmation_ids:
            coord = opt._campaign_coordinators[encounter_id]
            coord._submission_quota = 1
            coord._submission_started = 0
            coord.dispatch_authorized_ready(store, invalidate=False)
        legacy_retry_wall_seconds = time.perf_counter()-legacy_retry_started
        legacy_retry_stage_seconds = sum(
            float((opt._campaign_coordinators[encounter_id].state.get('timings') or {}).get(
                'priorityDispatchSeconds') or 0.0)-retry_before[encounter_id]
            for encounter_id in confirmation_ids)
        submitted_by_encounter = {
            encounter_id: sum(job.get('encounter') == encounter_id for job in pool.jobs)
            for encounter_id in range(20)
        }
        current_scan_seconds = sum(scan_seconds_by_encounter.values())
        expected_old_calls = sum(3 if encounter_id in confirmation_ids else 2
                                 for encounter_id in range(20))
        checks = dict(
            each_encounter_authorized_once=len(count_by_encounter) == 20
                and all(count == 1 for count in count_by_encounter.values()),
            blocked_confirmations_not_submitted=all(
                submitted_by_encounter[encounter_id] == 0
                for encounter_id in confirmation_ids),
            three_runnable_encounters_each_receive_one_job=all(
                submitted_by_encounter[encounter_id] == 1
                for encounter_id in expected_live_ids),
            only_three_evaluator_slots_reserved=(len(opt._community_evaluator.pending) == 3),
            no_holdout_results_fabricated=(store.db.execute(
                'SELECT COUNT(*) FROM ea_holdout WHERE result IS NOT NULL').fetchone()[0] == 0),
        )
        report['confirmationAuthorizationSnapshot'] = dict(
            blockedConfirmations=17, runnableEncounters=3, pendingHoldoutRowsPerBlocked=64,
            currentAuthorizationCalls=sum(count_by_encounter.values()),
            legacySweepAuthorizationCalls=expected_old_calls,
            currentAuthorizationSeconds=current_scan_seconds,
            legacyEquivalentAuthorizationSeconds=legacy_scan_seconds,
            currentPriorityDispatchSeconds=current_priority_seconds,
            legacyRepeatPriorityDispatchSeconds=legacy_retry_stage_seconds,
            legacyRepeatPriorityDispatchWallSeconds=legacy_retry_wall_seconds,
            currentMeasuredBoundarySeconds=(current_scan_seconds+current_priority_seconds),
            legacyEquivalentBoundarySeconds=(legacy_scan_seconds+current_priority_seconds
                                             +legacy_retry_stage_seconds),
            approximateAuthorizationScanReduction=(
                round(1.0-current_scan_seconds/legacy_scan_seconds, 3)
                if legacy_scan_seconds > 0 else None),
            submittedByEncounter=submitted_by_encounter,
            checks=checks)
        for name, passed in checks.items():
            check(report, 'confirmation-authorization-'+name, passed,
                  report['confirmationAuthorizationSnapshot'])
    finally:
        base._close(opt, store)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--report', type=Path)
    parser.add_argument('--work-dir', type=Path,
        default=Path(tempfile.gettempdir())/'ka-optimizer-dispatch-priority')
    args = parser.parse_args()
    temp_root = args.work_dir.resolve()
    temp_root.mkdir(parents=True, exist_ok=True)
    base.WORK_DIR = temp_root
    base.CREATED_DIRS.clear()
    report = dict(schema='ka-optimizer-campaign-dispatch-priority-1',
        fixture=dict(encounters=20, requestedWorkers=12, plannerWorkers=2,
                     battleWorkers=10, targetFrozenQueue=120,
                     workerPool='controlled futures; production Evaluator and Coordinator'),
        checks=[])
    started = time.perf_counter()
    try:
        run_worker_split(report)
        run_fast_path_benchmark(report)
        run_legacy_order_control(report)
        run_deep_queue_status_order(report)
        run_fairness_cases(report)
        run_battle_completion_midpass_harvest(report)
        run_portfolio_scan_reuse(report)
        run_midpass_refill_fairness(report)
        run_slow_stage_cases(report)
        run_compatibility_and_boundaries(report)
        run_pause_resume(report)
        run_confirmation_authorization_snapshot(report)
    finally:
        report['elapsedSeconds'] = time.perf_counter()-started
        for root in base.CREATED_DIRS:
            shutil.rmtree(root, ignore_errors=True)
    failures = [item['name'] for item in report['checks'] if not item['passed']]
    report['failedChecks'] = failures
    report['passed'] = not failures
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True)+'\n', encoding='utf-8')
    print('\nSUMMARY '+json.dumps(dict(passed=report['passed'],
        checks=len(report['checks']), failures=len(failures),
        elapsedSeconds=round(report['elapsedSeconds'], 2),
        performance=report.get('performance')), sort_keys=True))
    if args.report:
        print('REPORT '+str(args.report.resolve()))
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())

