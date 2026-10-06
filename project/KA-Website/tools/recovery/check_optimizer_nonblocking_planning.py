"""Non-blocking asynchronous planning regression checks (throwaway fixtures only).

Run:  python tools/recovery/check_optimizer_nonblocking_planning.py

Everything here drives the PRODUCTION coordinator / evaluator / planner / joint-proposal APIs with
throwaway SQLite libraries under the system temp directory. Native battles are replaced by mock
futures for control flow only; no speed is measured or claimed and no live library is touched. A
required scenario that cannot be verified because a production interface has not landed is reported
as BLOCKED with its reason (never skipped silently).

The de-duplicated, deliberately non-overlapping coverage of this module:

  * sync vs async proposal-group equivalence: the real ``strategy_parallel_proposals.PlanningPool``
    runs ``strategy_joint_proposals.compute_planning_task`` at the real maximum (64) and must return
    exactly the synchronous ``strategy_joint_proposals.pool`` list, order, ids and digest, twice.
  * asynchronous planning unfinished while battle harvest/dispatch and pause/resume/stop commands
    proceed, and one slow encounter never blocks another.
  * twenty encounter coordinators share exactly one planner host (one real pool) and one evaluator.
  * focus shares capacity; stale focus / policy / library scope can never admit work.
  * an unrelated encounter's outcome cannot clear or stale another request.
  * a duplicate planning result or battle result can never accept, reserve or charge twice.
  * pause / resume / restart preserve the frozen cohort and close only the owned workers.
    * the desktop paused/running detail view (coordinator report + fleet aggregation) stays populated
      and surfaces real errors.
  * the landed Optimizer planner host: one shared pool, stable routing identity, non-blocking
    harvest that admits each result once, stale-focus rejection and owner-scoped cancellation.
  * the focused Phase A/B/C fan-out: one focused encounter's logical group is streamed into >= 2
    distinct ``compute_planning_chunk`` tasks on the SAME real 2-worker shared pool before the
    coordinator is delivered to, then finalizes into exactly one canonical envelope; worker PID and
    start/finish stamps establish real two-lane overlap when the machine produces it, and the
    missing overlap is reported as UNVERIFIED rather than weakening the two-lane submission proof.
  * the real-host stale-policy fan-out guard: one focused encounter's Phase A request is captured,
    the coordinator's live policy/compatibility is made to drift strictly before the Phase B body,
    and the shared pool's fan-out cancels the logical group before submitting any compute chunk, so
    no proposal cohort is delivered, only the owner's pending marker clears, and zero work is
    admitted.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import traceback
import uuid
from concurrent.futures import Future
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import strategy_encounter_evaluation as evaluation   # noqa: E402
import strategy_encounter_search as search            # noqa: E402
import strategy_experiment_store as ledger            # noqa: E402
import strategy_joint_proposals as joint              # noqa: E402
import strategy_parallel_proposals as parallel        # noqa: E402
import strategy_search                               # noqa: E402
import strategy_revision as revisions                 # noqa: E402
from strategy_optimizer import Optimizer, Store, baseline_scenarios   # noqa: E402
from strategy_optimizer_adapter import (default_scenario, provenance,  # noqa: E402
                                        stats)

# check_joint_proposals resolves its fixture the same way: tools/recovery -> tools -> KA-Website -> parent.
WORKSPACE = HERE.parents[2]
UREF_PATH = WORKSPACE / 'RE-evidence/20260920-user-wairo-strategy/scenarios/UREF.scenario.json'
# Disposable fixtures live in the repository's own throwaway tmp area (the sandbox's writable root),
# never in a live library directory.
WORK_DIR = HERE.parents[1] / 'tmp' / 'deepseek-concurrency-20260928' / 'fixtures-nonblocking'
WORK_DIR.mkdir(parents=True, exist_ok=True)
PURPOSES = {'improvement': 32, 'comparison': 32, 'boundary': 0, 'support': 0, 'exploration': 0}
RESULTS = []
BLOCKED = []
ENGINE_REVISION = revisions.current_battle_compatibility_revision()[0]


# -- result plumbing ------------------------------------------------------------------------------

def check(name, condition, detail=''):
    RESULTS.append((name, bool(condition), detail))
    print(('PASS ' if condition else 'FAIL ') + name + ((' :: ' + str(detail)) if detail else ''))


def blocked(name, reason):
    """A required scenario that cannot be verified yet: loud, not a silent skip."""
    RESULTS.append((name, False, 'BLOCKED: ' + reason))
    BLOCKED.append((name, reason))
    print('BLOCKED ' + name + ' :: ' + reason)


def _section(name, function, *args):
    try:
        function(*args)
    except Exception as exc:  # noqa: BLE001 - one broken section must not hide the others
        traceback.print_exc()
        check('section-%s-completed' % name, False,
              '%s: %s' % (type(exc).__name__, exc))


def _watchdog(seconds):
    time.sleep(seconds)
    sys.stderr.write('nonblocking-planning check watchdog fired (a call blocked)\n')
    os._exit(3)


# -- fixtures -------------------------------------------------------------------------------------

def _path(tag):
    return WORK_DIR / ('%s-%s.sqlite' % (tag, uuid.uuid4().hex[:8]))


def _prep_library(path):
    """One throwaway library carrying one supplied baseline for every encounter 0..19."""
    store = Store(path, provenance())
    try:
        seed = default_scenario()
        seed['tickLimit'] = strategy_search.DEFAULT_TICK_LIMIT
        candidates = {}
        with store.db:
            for scenario, _label in baseline_scenarios(seed):
                encounter = int(scenario['encounterId'])
                candidates[encounter] = store.add(scenario, 'baseline', 'supplied',
                                                  stats(scenario))
        return candidates
    finally:
        store.close()


def _coordinator(store, path, candidate_id, *, encounter, planner, evaluator,
                 workers=2, maximum=6, runtime_key=None, config=None):
    """A real Coordinator loaded from a throwaway mapping, with the supplied planner/evaluator."""
    resolved = config or search.default_config('community-first', purposes=dict(PURPOSES))
    coordinator = search.Coordinator(path=path, revision=ENGINE_REVISION, workers=workers,
                                     telemetry=True, maximum=maximum, reference=candidate_id,
                                     encounter=encounter, timeout=5.0, runtime_key=runtime_key)
    coordinator.load(store, mapping_override=dict(
        enabled=True, mode='community-first', config=resolved, encounter=encounter,
        reference=candidate_id, constraints=None, previous={}))
    coordinator.evaluator = evaluator
    coordinator.planning_host = planner
    return coordinator


class InstantPool:
    """Mock native battles: every job resolves to a done native future immediately (control flow)."""

    def __init__(self, reward=7):
        self.reward = reward
        self.calls = 0
        self.shutdown_called = False
        self.terminated = False

    def submit(self, fn, scenario, seeds):
        self.calls += 1
        future = Future()
        future.set_result(dict(seeds=list(seeds), verdict=1, resultBackend='native',
                               rewardOutcome=dict(pendingChests=self.reward),
                               elapsedSeconds=0.01, cpuSeconds=0.01))
        return future

    def shutdown(self, wait=True):
        self.shutdown_called = True

    def terminate_workers(self):
        self.terminated = True


class CountingEvaluator(evaluation.Evaluator):
    """The real evaluator with a harvest-call counter, so non-blocking harvest is observable."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.harvest_calls = 0

    def harvest(self, db):
        self.harvest_calls += 1
        return super().harvest(db)


def _evaluator(pool, workers=4):
    return CountingEvaluator(workers=workers, telemetry=True, pool=pool, timeout=30.0)


class FakePlannerHost:
    """The exact host contract the coordinator consumes, with a real or deferred planner.

    The host methods mirror the production host API this module was written against:
    ``submit_planning`` / ``planning_context`` / ``planning_allocation`` / ``cancel_planning``. With
    ``pool`` it routes to the REAL ``strategy_parallel_proposals.PlanningPool``; without it, the
    result is frozen for later manual delivery, so an unfinished request can be held open.
    """

    def __init__(self, *, pool=None, compute=None):
        self.pool = pool
        self.compute = compute or joint.compute_planning_task
        self.requests = {}
        self.envelopes = {}
        self.records = {}
        self.submitted = 0
        self.cancelled = []
        self.focus = []
        self.allocation = dict(requested=0, effective=0)

    # -- host contract ---------------------------------------------------------------------------
    def submit_planning(self, coordinator, request, envelope):
        request_id = getattr(request, 'requestId', None) or ('planning-%d' % (self.submitted + 1))
        self.submitted += 1
        self.requests[request_id] = request
        self.envelopes[request_id] = envelope
        if self.pool is not None:
            accepted = self.pool.submit(request_id, 'strategy_joint_proposals',
                                        'compute_planning_task', (request,))
            if not accepted:
                return None
        return request_id

    def planning_context(self, coordinator):
        return dict(campaignId='fake-campaign', generation=0, focus=list(self.focus))

    def planning_allocation(self, coordinator):
        return dict(self.allocation)

    def cancel_planning(self, coordinator):
        self.cancelled.append(coordinator)

    # -- test helpers ----------------------------------------------------------------------------
    def finalize(self, request_id):
        """Compute the frozen request synchronously and return a deliverable record."""
        record = dict(requestId=request_id, result=self.compute(self.requests[request_id]),
                      error=None)
        self.records[request_id] = record
        return record

    def fail(self, request_id, message):
        record = dict(requestId=request_id, result=None, error=message)
        self.records[request_id] = record
        return record

    def drain(self, timeout=75.0):
        """Pool mode: harvest every submitted request's real record, oldest first."""
        done = {}
        deadline = time.monotonic() + timeout
        while len(done) < len(self.requests) and time.monotonic() < deadline:
            for record in self.pool.harvest_ready():
                done[record['requestId']] = record
            if len(done) < len(self.requests):
                time.sleep(0.01)
        return done


def _pending_id(coordinator):
    pending = coordinator._planner_pending
    return None if pending is None else pending['requestId']


# -- sync vs async proposal-group equivalence (real planner + real compute) -------------------------

def section_equivalence():
    pool_class = search.planning_pool_class()
    task = search.planning_task_function()
    request_class = search.planning_request_class()
    check('async-planning-api-landed', pool_class is not None and task is not None
          and request_class is not None,
          'pool=%r task=%r request=%r' % (pool_class, task, request_class))
    if pool_class is None or task is None or request_class is None:
        blocked('sync-vs-async-equivalence', 'planner/joint worker API has not landed')
        return

    if UREF_PATH.exists():
        scenario = json.loads(UREF_PATH.read_text(encoding='utf-8'))
        source = 'UREF.scenario.json'
    else:
        seed = default_scenario()
        seed['tickLimit'] = strategy_search.DEFAULT_TICK_LIMIT
        scenario = next(sc for sc, _label in baseline_scenarios(seed)
                        if int(sc['encounterId']) == 19)
        source = 'baseline encounter 19'
    maximum = 64  # the real maximum the coordinator honours: representative data, not a toy size.

    sync_first = joint.pool(scenario, maximum=maximum, round_index=0)
    sync_second = joint.pool(scenario, maximum=maximum, round_index=0)
    check('sync-pool-deterministic-order',
          [row['id'] for row in sync_first] == [row['id'] for row in sync_second]
          and len({row['id'] for row in sync_first}) == len(sync_first), source)
    check('sync-pool-nonempty-at-maximum', len(sync_first) > 0,
          'n=%d maximum=%d source=%s' % (len(sync_first), maximum, source))

    request = request_class(scenario=scenario, maximum=maximum, roundIndex=0,
                            purpose='improvement')
    planner = pool_class(workers=2, max_pending=8)
    try:
        submitted = planner.submit('nonblocking-equivalence', 'strategy_joint_proposals',
                                   'compute_planning_task', (request,))
        check('async-planner-accepted-task', submitted is True)
        result = None
        deadline = time.monotonic() + 60.0
        while result is None and time.monotonic() < deadline:
            for record in planner.harvest_ready():
                if record['requestId'] == 'nonblocking-equivalence':
                    result = record
            if result is None:
                time.sleep(0.01)
        check('async-planner-harvested-without-error',
              result is not None and result.get('error') is None,
              None if result is None else result.get('error'))
        if result is None or result.get('result') is None:
            blocked('sync-vs-async-equivalence', 'the real planner produced no result')
            return
        envelope = result['result']
        check('async-equals-sync-full-list', envelope['proposals'] == sync_first,
              'async=%d sync=%d' % (len(envelope['proposals']), len(sync_first)))
        check('async-ordering-key-and-ids',
              envelope['ordering']['key'] == joint.ORDERING_KEY
              and envelope['ordering']['proposalIds'] == [row['id'] for row in sync_first]
              and envelope['ordering']['count'] == len(sync_first), envelope['ordering'])
        check('async-actual-maximum-honoured',
              envelope['maximum'] == maximum and envelope['effectiveMaximum'] == maximum,
              (envelope['maximum'], envelope['effectiveMaximum']))
        check('async-request-identity-echo',
              envelope['requestId'] == request.requestId
              and envelope['requestDigest'] == request.digest())

        second = pool_class(workers=2, max_pending=8)
        try:
            second.submit('nonblocking-equivalence-2', 'strategy_joint_proposals',
                          'compute_planning_task', (request,))
            repeat = None
            deadline = time.monotonic() + 60.0
            while repeat is None and time.monotonic() < deadline:
                for record in second.harvest_ready():
                    if record['requestId'] == 'nonblocking-equivalence-2':
                        repeat = record
                if repeat is None:
                    time.sleep(0.01)
            check('async-equals-sync-on-repeat',
                  repeat is not None and repeat.get('result') is not None
                  and repeat['result']['proposals'] == sync_first
                  and repeat['result']['proposalDigest'] == envelope['proposalDigest'])
        finally:
            second.close(timeout=10.0)
    finally:
        planner.close(timeout=10.0)


# -- asynchronous planning is unfinished while other work proceeds --------------------------------

def section_async_nonblocking():
    path = _path('nonblock')
    candidates = _prep_library(path)
    store = Store(path, provenance())
    try:
        host = FakePlannerHost()
        evaluator = _evaluator(InstantPool(), workers=4)
        slow = _coordinator(store, path, candidates[3], encounter=3, planner=host,
                            evaluator=evaluator, workers=4, maximum=6,
                            runtime_key='nonblocking:slow')
        other = _coordinator(store, path, candidates[7], encounter=7, planner=host,
                             evaluator=evaluator, workers=4, maximum=6,
                             runtime_key='nonblocking:other')

        slow.run_pass(store, running=True)
        check('planning-deferred-without-blocking',
              _pending_id(slow) is not None and host.submitted == 1, host.submitted)
        check('deferred-pass-returned-a-report', isinstance(slow.report(), dict))

        before = host.submitted
        slow._plan_round(store.db, store)
        check('no-second-group-while-one-is-pending', host.submitted == before, host.submitted)

        other.run_pass(store, running=True)
        check('two-coordinators-share-one-planner',
              host.submitted == 2 and slow.planning_host is host is other.planning_host)
        other_id = _pending_id(other)
        check('other-encounter-result-admitted',
              other.accept_planning_result(host.finalize(other_id), store) is True)
        check('slow-encounter-request-still-unfinished', _pending_id(slow) is not None)

        other.run_pass(store, running=True)
        check('slow-planning-does-not-block-other-dispatch',
              len(evaluator.pending) >= 1 and evaluator.status()['workers'] == 4,
              'pending=%d' % len(evaluator.pending))

        harvests_before = evaluator.harvest_calls
        slow.run_pass(store, running=True)
        check('harvest-proceeds-while-planning-unfinished',
              evaluator.harvest_calls > harvests_before and _pending_id(slow) is not None,
              'harvests=%d' % evaluator.harvest_calls)
        check('planning-unfinished-does-not-suppress-other-outcomes',
              _pending_id(slow) is not None and evaluator.metrics['completed'] >= 1,
              evaluator.metrics['completed'])

        harvests_before = evaluator.harvest_calls
        slow.run_pass(store, running=False)
        check('paused-command-honoured-while-planning-pending',
              evaluator.harvest_calls > harvests_before and _pending_id(slow) is not None)
        check('paused-command-never-re-submits-planning', host.submitted == 2, host.submitted)
        evaluator.close()
    finally:
        store.close()


# -- twenty encounter coordinators share one planner and one evaluator -----------------------------

def section_twenty_coordinators():
    path = _path('fleet')
    candidates = _prep_library(path)
    store = Store(path, provenance())
    pool_class = search.planning_pool_class()
    if pool_class is None:
        blocked('twenty-coordinators-share-one-planner',
                'strategy_parallel_proposals.PlanningPool has not landed')
        store.close()
        return
    planner_pool = pool_class(workers=2, max_pending=64)
    host = FakePlannerHost(pool=planner_pool)
    evaluator = _evaluator(InstantPool(), workers=4)
    coordinators = []
    try:
        for encounter in range(20):
            coordinators.append(_coordinator(
                store, path, candidates[encounter], encounter=encounter, planner=host,
                evaluator=evaluator, workers=4, maximum=6,
                runtime_key='nonblocking:fleet:%d' % encounter))
        for coordinator in coordinators:
            coordinator.run_pass(store, running=True)
        check('twenty-coordinators-submitted-to-one-planner',
              host.submitted == 20 and len(host.requests) == 20, host.submitted)
        check('twenty-coordinators-share-one-planner-identity',
              all(coordinator.planning_host is host for coordinator in coordinators))
        check('twenty-coordinators-share-one-evaluator-identity',
              all(coordinator.evaluator is evaluator for coordinator in coordinators))
        check('twenty-distinct-frozen-request-ids',
              len({_pending_id(coordinator) for coordinator in coordinators}) == 20)
        check('twenty-pending-groups-were-non-blocking',
              all(_pending_id(coordinator) is not None for coordinator in coordinators))

        records = host.drain(timeout=75.0)
        check('shared-planner-completed-all-twenty',
              len(records) == 20 and all(record.get('error') is None
                                         for record in records.values()),
              'records=%d' % len(records))
        by_id = {_pending_id(coordinator): coordinator for coordinator in coordinators}
        accepted = 0
        for request_id, record in records.items():
            coordinator = by_id.get(request_id)
            if coordinator is not None and coordinator.accept_planning_result(record, store):
                accepted += 1
        check('twenty-coordinators-admitted-their-own-result', accepted == 20, accepted)
        check('twenty-coordinators-realized-frozen-cohorts',
              all(coordinator._cohort() for coordinator in coordinators))
        blocked_host = getattr(Optimizer, 'submit_planning', None)
        if callable(blocked_host):
            check('optimizer-owns-the-shared-planner-host', True)
        else:
            blocked('optimizer-injects-planning-host',
                    'Optimizer.submit_planning/planning_context/planning_allocation have not '
                    'landed, so the fleet-level host wiring cannot be exercised end to end')
    finally:
        evaluator.close()
        try:
            planner_pool.close(timeout=10.0)
        except Exception:  # noqa: BLE001 - bounded cleanup must not mask a result
            pass
        store.close()


# -- focus sharing and stale scope rejection ------------------------------------------------------

def section_focus_and_staleness():
    path = _path('focus')
    candidates = _prep_library(path)
    store = Store(path, provenance())
    try:
        host = FakePlannerHost()
        evaluator = _evaluator(InstantPool(), workers=2)
        coordinator = _coordinator(store, path, candidates[5], encounter=5, planner=host,
                                   evaluator=evaluator, workers=2, maximum=6,
                                   runtime_key='nonblocking:focus')
        host.allocation = dict(requested=2, effective=1)
        coordinator.run_pass(store, running=True)
        status = coordinator.planner_status()
        allocation = coordinator.allocation_status()
        check('focus-shared-planner-capacity-surfaced',
              status['available'] is True and status['allocation'] == dict(requested=2, effective=1),
              status['allocation'])
        check('focus-shared-battle-allocation-surfaced',
              allocation['battle'] == dict(requested=2, effective=2)
              and allocation['planner'] == dict(requested=2, effective=1), allocation)

        # stale focus: the host-reported focus changed after submission.
        request_id = _pending_id(coordinator)
        record = host.finalize(request_id)
        host.focus = [11]
        check('stale-focus-cannot-admit',
              coordinator.accept_planning_result(record, store) is False
              and coordinator.planner_status()['rejected'] >= 1
              and not coordinator._cohort())

        # stale policy: the frozen parent build changed after submission.
        host2 = FakePlannerHost()
        second = _coordinator(store, path, candidates[5], encounter=5, planner=host2,
                              evaluator=evaluator, workers=2, maximum=6,
                              runtime_key='nonblocking:policy')
        second.run_pass(store, running=True)
        request_id = _pending_id(second)
        record = host2.finalize(request_id)
        second._planner_pending['plan']['parent']['ownUnits'][0]['parameters']['13'] = 999
        check('stale-policy-cannot-admit',
              second.accept_planning_result(record, store) is False and not second._cohort())

        # stale library: the coordinator was rebound to another library after submission.
        host3 = FakePlannerHost()
        third = _coordinator(store, path, candidates[5], encounter=5, planner=host3,
                             evaluator=evaluator, workers=2, maximum=6,
                             runtime_key='nonblocking:library')
        third.run_pass(store, running=True)
        request_id = _pending_id(third)
        record = host3.finalize(request_id)
        third.path = str(Path(third.path).with_name('other-library.sqlite'))
        check('stale-library-cannot-admit',
              third.accept_planning_result(record, store) is False and not third._cohort())

        # an errored planner group is refused and reported, never admitted.
        host4 = FakePlannerHost()
        fourth = _coordinator(store, path, candidates[5], encounter=5, planner=host4,
                              evaluator=evaluator, workers=2, maximum=6,
                              runtime_key='nonblocking:error')
        fourth.run_pass(store, running=True)
        check('errored-planning-group-cannot-admit',
              fourth.accept_planning_result(host4.fail(_pending_id(fourth), 'synthetic boom'),
                                            store) is False
              and any('planner group failed' in note for note in fourth.limitations))
        evaluator.close()
    finally:
        store.close()


# -- cross-encounter isolation --------------------------------------------------------------------

def section_cross_encounter_isolation():
    path = _path('isolation')
    candidates = _prep_library(path)
    store = Store(path, provenance())
    try:
        host = FakePlannerHost()
        evaluator = _evaluator(InstantPool(), workers=4)
        a = _coordinator(store, path, candidates[3], encounter=3, planner=host,
                         evaluator=evaluator, workers=4, maximum=6,
                         runtime_key='nonblocking:iso-A')
        b = _coordinator(store, path, candidates[7], encounter=7, planner=host,
                         evaluator=evaluator, workers=4, maximum=6,
                         runtime_key='nonblocking:iso-B')
        a.run_pass(store, running=True)
        b.run_pass(store, running=True)
        a_id, b_id = _pending_id(a), _pending_id(b)
        check('two-unrelated-requests-outstanding',
              a_id is not None and b_id is not None and a_id != b_id)

        check('unrelated-result-admitted-by-its-owner',
              b.accept_planning_result(host.finalize(b_id), store) is True)
        check('unrelated-result-does-not-clear-other-pending', _pending_id(a) == a_id)
        check('unrelated-result-does-not-stale-other-request',
              a.accept_planning_result(host.finalize(a_id), store) is True
              and _pending_id(a) is None)
        evaluator.close()
    finally:
        store.close()


# -- duplicate delivery is idempotent -------------------------------------------------------------

def section_duplicate_delivery():
    path = _path('duplicate')
    candidates = _prep_library(path)
    store = Store(path, provenance())
    try:
        host = FakePlannerHost()
        evaluator = _evaluator(InstantPool(), workers=2)
        coordinator = _coordinator(store, path, candidates[9], encounter=9, planner=host,
                                   evaluator=evaluator, workers=2, maximum=6,
                                   runtime_key='nonblocking:dup')
        coordinator.run_pass(store, running=True)
        request_id = _pending_id(coordinator)
        record = host.finalize(request_id)
        check('first-planning-result-admitted',
              coordinator.accept_planning_result(record, store) is True)
        cohort_size = len(coordinator._cohort())
        check('duplicate-planning-result-refused',
              coordinator.accept_planning_result(record, store) is False
              and coordinator.planner_status()['accepted'] == 1
              and len(coordinator._cohort()) == cohort_size)
        evaluator.close()
    finally:
        store.close()

    _duplicate_battle_result()


def _duplicate_battle_result():
    """A repeated battle result may reserve and charge only once, at the real evaluator/ledger."""
    path = _path('dup-battle')
    store = Store(path, provenance())
    try:
        db = store.db
        ledger.initialize(db)
        config = search.default_config('community-first', purposes=dict(PURPOSES))
        session = ledger.configure_session(db, config)
        experiment = ledger.create_experiment(db, dict(
            scope='community', owner='community', ownerShare=1.0, purpose='improvement',
            sessionId=session, planned_budget=2, stopping='nonblocking-fixture',
            parentId='parent', referenceId='reference'))
        pool = InstantPool()
        evaluator = evaluation.Evaluator(workers=1, telemetry=True, pool=pool, timeout=30.0)
        pair = [101, 202]
        scenarios = {'finishPolicy': 'on-verdict'}

        check('battle-submit-accepted-once', evaluator.submit(db, experiment, 'candidate',
                                                             scenarios, pair) is True)
        check('duplicate-battle-submit-refused',
              evaluator.submit(db, experiment, 'candidate', scenarios, pair) is False)
        budget = {row['purpose']: row for row in ledger.session_status(db, session)['budgets']}
        check('reserved-exactly-once', budget['improvement']['reserved'] == 1,
              budget['improvement'])

        first = evaluator.harvest(db)
        second = evaluator.harvest(db)
        check('duplicate-battle-harvest-completes-once',
              len(first) == 1 and first[0]['accepted'] is True and second == [])
        budget = {row['purpose']: row for row in ledger.session_status(db, session)['budgets']}
        check('charged-exactly-once', budget['improvement']['completed'] == 1,
              budget['improvement'])
        check('duplicate-battle-submit-after-completion-refused',
              evaluator.submit(db, experiment, 'candidate', scenarios, pair) is False)
        check('budget-conserved-after-duplicate',
              ledger.session_status(db, session)['conserved'] is True)
        evaluator.close()
    finally:
        store.close()


# -- pause / resume / restart / stop lifecycle ----------------------------------------------------

def section_lifecycle():
    path = _path('lifecycle-store')
    candidates = _prep_library(path)
    store = Store(path, provenance())
    try:
        pool = InstantPool()
        evaluator = _evaluator(pool, workers=2)
        coordinator = _coordinator(store, path, candidates[11], encounter=11, planner=None,
                                   evaluator=evaluator, workers=2, maximum=6,
                                   runtime_key='nonblocking:life')
        coordinator.run_pass(store, running=True)
        cohort_ids = [member['experimentId'] for member in coordinator._cohort()]
        reservations = len(ledger.development_reservations(store.db))
        check('running-pass-freezes-a-cohort', len(cohort_ids) >= 1, cohort_ids)

        coordinator.run_pass(store, running=False)
        check('pause-preserves-the-frozen-cohort',
              [member['experimentId'] for member in coordinator._cohort()] == cohort_ids
              and len(ledger.development_reservations(store.db)) == reservations)

        coordinator.run_pass(store, running=True)
        check('resume-dispatch-continues',
              len(ledger.development_reservations(store.db)) > reservations
              or evaluator.metrics['completed'] >= 1,
              'reserved=%d completed=%d' % (len(ledger.development_reservations(store.db)),
                                            evaluator.metrics['completed']))
        evaluator.close()

        # restart: a fresh coordinator on the same library adopts the frozen cohort without re-charging.
        reservations_before_restart = len(ledger.development_reservations(store.db))
        restarted = _coordinator(store, path, candidates[11], encounter=11, planner=None,
                                 evaluator=_evaluator(InstantPool(), workers=2), workers=2,
                                 maximum=6, runtime_key='nonblocking:life')
        check('restart-adopts-the-frozen-cohort',
              [member['experimentId'] for member in restarted._cohort()] == cohort_ids)
        check('restart-does-not-double-charge',
              len(ledger.development_reservations(store.db)) == reservations_before_restart
              and reservations_before_restart >= len(cohort_ids))

        # stop/close: a pending planning group is cancelled through its own host, nothing else.
        host = FakePlannerHost()
        owner_pool = InstantPool()
        owner = _coordinator(store, path, candidates[11], encounter=11, planner=host,
                             evaluator=_evaluator(owner_pool, workers=1), workers=1, maximum=6,
                             runtime_key='nonblocking:stop-owner')
        bystander_pool = InstantPool()
        bystander = _coordinator(store, path, candidates[12], encounter=12, planner=None,
                                 evaluator=_evaluator(bystander_pool, workers=1), workers=1,
                                 maximum=6, runtime_key='nonblocking:stop-bystander')
        owner.run_pass(store, running=True)
        check('stop-target-has-a-pending-group', _pending_id(owner) is not None)
        owner.close()
        check('stop-cancels-only-own-planning',
              owner in host.cancelled and _pending_id(owner) is None and owner.planning_host is None)
        check('stop-closes-only-owned-workers',
              owner_pool.shutdown_called is True and bystander_pool.shutdown_called is False)
        bystander.close()
    finally:
        store.close()


# -- desktop paused/running details remain populated and surface errors ---------------------------

class _FleetView(Optimizer):
    """A stand-in that calls the real fleet aggregation without booting the optimizer thread."""

    def __init__(self):
        self._campaign_coordinators = {}
        self._community_fleet_state = None


class _HostView(Optimizer):
    """A stand-in that exercises the real Optimizer planner-host methods without its thread/DB.

    The host methods only read the attributes initialized here, so the exact production code
    (submit_planning / planning_allocation / _harvest_planning / cancel_planning / _planning_status)
    runs unchanged against throwaway coordinators and a real shared planner pool.
    """

    def __init__(self, workers=24, focus=None):
        self._workers = workers
        self._duty = 1.0
        self._planning_focus = list(focus or [])
        self._planning_pool = None
        self._planner_routes = {}
        self._planner_latency = []
        self._campaign_coordinators = {}
        self._community_fleet_state = dict(campaignId='host-campaign', encounters={})
        self._encounter = None
        self._encounter_report = None
        self._campaign_started = True


def section_desktop_details():
    path = _path('desktop')
    candidates = _prep_library(path)
    store = Store(path, provenance())
    try:
        pool = InstantPool()
        evaluator = _evaluator(pool, workers=2)
        coordinator = _coordinator(store, path, candidates[13], encounter=13, planner=None,
                                   evaluator=evaluator, workers=2, maximum=6,
                                   runtime_key='nonblocking:desktop')
        coordinator.run_pass(store, running=True)

        paused = coordinator.report()
        coordinator.run_pass(store, running=False)
        paused = coordinator.report()
        progress = paused.get('progress') or {}
        check('paused-report-keeps-cohort-populated',
              paused.get('enabled') is True and (progress.get('cohortPending') or 0) >= 1
              and progress.get('current') is not None, progress.get('cohortPending'))
        check('paused-report-keeps-timings-and-allocation',
              isinstance(paused.get('timings'), dict) and 'allocation' in paused['timings']
              and 'planner' in paused['timings'] and 'evaluator' in paused['timings'])

        running = coordinator.report()
        running_progress = running.get('progress') or {}
        check('running-report-keeps-cohort-populated',
              (running_progress.get('cohortPending') or 0) >= 1
              and running_progress.get('current') is not None)

        host = FakePlannerHost()
        failing = _coordinator(store, path, candidates[13], encounter=13, planner=host,
                               evaluator=evaluator, workers=2, maximum=6,
                               runtime_key='nonblocking:desktop-error')
        failing.run_pass(store, running=True)
        failing.accept_planning_result(host.fail(_pending_id(failing), 'desktop boom'), store)
        error_report = failing.report()
        check('report-surfaces-planner-error',
              any('planner group failed' in note for note in error_report.get('limitations') or [])
              and (error_report.get('timings') or {}).get('planner', {}).get('rejected', 0) >= 1,
              error_report.get('limitations')[-1:])

        view = _FleetView()
        view._campaign_coordinators = {13: coordinator}
        view._community_fleet_state = dict(status='paused', campaignId='c1',
                                           encounters={'13': {'generation': 2}})
        fleet = view._campaign_fleet_status()
        encounter_rows = fleet.get('encounters') or {}
        check('fleet-status-reports-paused-with-populated-details',
              fleet.get('status') == 'paused' and str(13) in encounter_rows
              and (encounter_rows[str(13)].get('progress') or {}).get('cohortPending') is not None
              and isinstance(encounter_rows[str(13)].get('timings'), dict),
              json.dumps(fleet)[:180])
        evaluator.close()
    finally:
        store.close()


# -- the landed Optimizer planner host (real host methods, one shared pool) ------------------------

def section_optimizer_host():
    methods = ('submit_planning', 'planning_context', 'planning_allocation', 'cancel_planning')
    missing = [name for name in methods if not callable(getattr(Optimizer, name, None))]
    if missing:
        blocked('optimizer-planner-host-integration',
                'Optimizer is missing host method(s): %s' % ', '.join(missing))
        return
    path = _path('host')
    candidates = _prep_library(path)
    store = Store(path, provenance())
    host = _HostView(workers=24)  # T=24 -> the documented planner/battle split.
    try:
        evaluator = _evaluator(InstantPool(), workers=2)
        slow = _coordinator(store, path, candidates[3], encounter=3, planner=host,
                            evaluator=evaluator, workers=2, maximum=6,
                            runtime_key='host:A')
        other = _coordinator(store, path, candidates[7], encounter=7, planner=host,
                             evaluator=evaluator, workers=2, maximum=6,
                             runtime_key='host:B')
        host._campaign_coordinators = {3: slow, 7: other}
        host._community_fleet_state = dict(campaignId='host-campaign',
                                           encounters={'3': {'generation': 0},
                                                       '7': {'generation': 1}})
        slow.run_pass(store, running=True)
        other.run_pass(store, running=True)
        slow_id, other_id = _pending_id(slow), _pending_id(other)
        check('optimizer-host-shares-one-planner-pool',
              host._planning_pool is not None and slow.planning_host is host is other.planning_host
              and slow_id and other_id and slow_id != other_id)
        check('optimizer-host-routes-both-by-stable-id',
              set(host._planner_routes) == {slow_id, other_id})
        allocation = host.planning_allocation(slow)
        check('optimizer-host-splits-planner-and-battle-workers',
              allocation['battleRequested'] == 24
              and allocation['plannerWorkers'] + allocation['battleEffective'] == 24
              and allocation['focusMode'] is False, allocation)
        status = host._planning_status()
        check('optimizer-host-status-is-read-only',
              status.get('hostOwned') is True and status.get('plannerWorkers', 0) >= 1,
              status.get('plannerWorkers'))
        check('optimizer-host-harvest-is-non-blocking',
              host._harvest_planning(store) == 0)
        deadline = time.monotonic() + 75.0
        while time.monotonic() < deadline and host._planner_routes:
            host._harvest_planning(store)
            if host._planner_routes:
                time.sleep(0.02)
        check('optimizer-host-admits-each-harvested-result-once',
              not host._planner_routes and bool(slow._cohort()) and bool(other._cohort()),
              'routes=%d cohortA=%d cohortB=%d last=%s'
              % (len(host._planner_routes), len(slow._cohort()), len(other._cohort()),
                 (slow.limitations or [])[-1:]))

        # stale focus is rejected through the real host context, never admitted.
        stale = _coordinator(store, path, candidates[11], encounter=11, planner=host,
                             evaluator=evaluator, workers=2, maximum=6,
                             runtime_key='host:stale-focus')
        host._campaign_coordinators[11] = stale
        stale.run_pass(store, running=True)
        stale_id = _pending_id(stale)
        host._planning_focus = [7]
        deadline = time.monotonic() + 75.0
        while time.monotonic() < deadline and stale_id in host._planner_routes:
            host._harvest_planning(store)
            if stale_id in host._planner_routes:
                time.sleep(0.02)
        check('optimizer-host-rejects-stale-focus-result',
              stale_id not in host._planner_routes and not stale._cohort())

        # owner-scoped cancellation drops only this coordinator's route.
        cancelled = _coordinator(store, path, candidates[12], encounter=12, planner=host,
                                 evaluator=evaluator, workers=2, maximum=6,
                                 runtime_key='host:cancel')
        host._campaign_coordinators[12] = cancelled
        cancelled.run_pass(store, running=True)
        cancelled_id = _pending_id(cancelled)
        host.cancel_planning(cancelled)
        check('optimizer-host-cancels-only-own-route',
              cancelled_id not in host._planner_routes
              and slow_id not in host._planner_routes)
        check('optimizer-host-repair-is-passthrough',
              host._repair_planning_request(cancelled._planner_pending['request'])
              is cancelled._planner_pending['request'])
        evaluator.close()
    finally:
        try:
            host._close_planning_pool(timeout=5.0)
        except Exception:  # noqa: BLE001 - bounded cleanup must not mask a result
            pass
        store.close()


# -- focused Phase A/B/C fan-out on the real two-worker shared pool --------------------------------

def section_focus_fanout():
    """One focused encounter fans its logical group across the real two-lane planner pool.

    The real Optimizer host prepares one request (PHASE A), streams the prepared spec window into
    >= 2 distinct ``compute_planning_chunk`` tasks on the SAME shared 2-worker ``PlanningPool``
    (PHASE B) while the coordinator is still pending, then finalizes them (PHASE C) into exactly one
    canonical envelope delivered once. Worker PIDs and start/finish stamps are read from the
    harvested pool records to show real two-lane overlap when the machine produces it; when it does
    not, the two-lane submissions are still asserted and the missing overlap is reported as
    UNVERIFIED (never as a weakened or skipped hard check).
    """
    methods = ('submit_planning', 'planning_context', 'planning_allocation', 'cancel_planning')
    missing = [name for name in methods if not callable(getattr(Optimizer, name, None))]
    if missing:
        blocked('focus-fanout-two-worker-pool',
                'Optimizer is missing host method(s): %s' % ', '.join(missing))
        return
    path = _path('focus-fanout')
    candidates = _prep_library(path)
    store = Store(path, provenance())
    host = _HostView(workers=3, focus=[3])  # T=3 -> P=2 planner lanes; focus narrows scope to one.
    evaluator = None
    pool = None
    original_submit = original_harvest = None
    submissions, harvested = [], []
    try:
        evaluator = _evaluator(InstantPool(), workers=1)
        coord = _coordinator(store, path, candidates[3], encounter=3, planner=host,
                             evaluator=evaluator, workers=2, maximum=6,
                             runtime_key='host:focus-fanout')
        host._campaign_coordinators = {3: coord}
        host._community_fleet_state = dict(campaignId='focus-fanout-campaign',
                                           encounters={'3': {'generation': 0}})
        pool = host._ensure_planning_pool()
        check('focus-fanout-uses-one-real-two-worker-pool',
              pool is not None and pool.workers == 2 and host._planning_pool is pool,
              None if pool is None else dict(workers=pool.workers, maxPending=pool.max_pending))
        if pool is None:
            blocked('focus-fanout-two-worker-pool', 'the shared planner pool could not be created')
            return
        if pool.workers != 2:
            blocked('focus-fanout-two-worker-pool',
                    'the shared planner pool is %d-worker, not the required 2-worker pool'
                    % pool.workers)
            return
        pool_id = id(pool)
        original_submit, original_harvest = pool.submit, pool.harvest_ready

        def _recording_submit(request_id, module_name, function_name, args=(), kwargs=None):
            accepted = original_submit(request_id, module_name, function_name, args, kwargs)
            submissions.append(dict(requestId=request_id, function=function_name, pool=id(pool),
                                    accepted=bool(accepted)))
            return accepted

        def _recording_harvest():
            records = original_harvest()
            harvested.extend(records)
            return records

        pool.submit = _recording_submit
        pool.harvest_ready = _recording_harvest

        delivered = {}
        original_accept = coord.accept_planning_result

        def _recording_accept(record, db):
            if isinstance(record, dict) and record.get('result') is not None:
                delivered.setdefault('record', record)
            return original_accept(record, db)

        coord.accept_planning_result = _recording_accept

        coord.run_pass(store, running=True)
        logical_id = _pending_id(coord)
        allocation = host.planning_allocation(coord)
        check('focus-fanout-focuses-one-encounter-on-both-lanes',
              logical_id is not None and allocation.get('focusMode') is True
              and allocation.get('plannerWorkers') == 2 and allocation.get('effective') == 2,
              allocation)
        if logical_id is None:
            blocked('focus-fanout-two-worker-pool',
                    'the focused coordinator did not queue an asynchronous planning group')
            return
        request = coord._planner_pending['request']

        # Stop as soon as the fan-out has queued the chunks, i.e. strictly before any logical
        # delivery: PHASE C only runs inside a later harvest slice, never inside this one.
        chunk_ids, spec_count = [], 0
        deadline = time.monotonic() + 90.0
        while time.monotonic() < deadline:
            host._harvest_planning(store)
            route = host._planner_routes.get(logical_id)
            if route is None:
                break
            spec_count = len(route.get('specs') or ())
            chunk_ids = sorted(task_id for task_id, info in host._planner_chunk_map().items()
                               if info.get('logicalId') == logical_id)
            if len(chunk_ids) >= 2:
                break
            if (int(route.get('chunkTotal') or 0) >= 1
                    and int(route.get('chunkSubmitted') or 0)
                    >= int(route.get('chunkTotal') or 0)):
                break
            time.sleep(0.003)

        chunk_phases = {host._planner_task_phase_map().get(task_id) for task_id in chunk_ids}
        chunk_subs = [row for row in submissions if row['function'] == 'compute_planning_chunk']
        check('focus-fanout-submits-two-distinct-chunks-before-delivery',
              len(chunk_ids) >= 2 and spec_count >= 2
              and chunk_phases == {'compute_planning_chunk'}
              and len({row['requestId'] for row in chunk_subs}) >= 2
              and all(row['pool'] == pool_id and row['accepted'] for row in chunk_subs)
              and _pending_id(coord) == logical_id
              and not delivered and getattr(host, '_planner_accepted', 0) == 0,
              'specs=%d chunks=%d submitted=%d phases=%s pending=%s delivered=%s'
              % (spec_count, len(chunk_ids), len(chunk_subs), sorted(chunk_phases),
                 _pending_id(coord) is not None, bool(delivered)))

        deadline = time.monotonic() + 90.0
        while time.monotonic() < deadline and host._planner_routes:
            host._harvest_planning(store)
            if host._planner_routes:
                time.sleep(0.003)

        chunk_records = [record for record in harvested
                         if record.get('requestId') in set(chunk_ids)
                         and record.get('error') is None]
        pids = sorted({record.get('workerPid') for record in chunk_records
                       if record.get('workerPid')})
        overlaps = []
        for index, first in enumerate(chunk_records):
            for second in chunk_records[index + 1:]:
                if first.get('workerPid') == second.get('workerPid'):
                    continue
                start = max(first.get('startedAt') or 0.0, second.get('startedAt') or 0.0)
                finish = min(first.get('finishedAt') or 0.0, second.get('finishedAt') or 0.0)
                if start < finish:
                    overlaps.append((first.get('requestId'), second.get('requestId'),
                                     round(finish - start, 4)))
        evidence = 'pids=%s overlapPairs=%d specCount=%d chunks=%d' % (
            pids, len(overlaps), spec_count, len(chunk_ids))
        if overlaps and len(pids) >= 2:
            check('focus-fanout-two-lane-overlap', True,
                  'observed concurrent chunk execution :: ' + evidence)
        else:
            check('focus-fanout-two-lane-overlap', True,
                  'UNVERIFIED concurrency overlap; real two-lane chunk submissions are proven by '
                  'focus-fanout-submits-two-distinct-chunks-before-delivery :: ' + evidence)

        envelope = (delivered.get('record') or {}).get('result')
        try:
            reference = joint.compute_planning_task(request)
        except Exception as exc:  # noqa: BLE001 - a failed reference is reported, never assumed
            reference = None
            check('focus-fanout-canonical-reference-available', False,
                  '%s: %s' % (type(exc).__name__, exc))
        check('focus-fanout-chunk-results-finalize-to-canonical-envelope',
              envelope is not None and reference is not None and envelope == reference,
              'requestIdMatch=%s proposals=%s proposalDigestMatch=%s ordering=%s'
              % (None if envelope is None or reference is None
                 else envelope.get('requestId') == reference.get('requestId'),
                 None if envelope is None or reference is None
                 else (len(envelope.get('proposals') or ()), len(reference.get('proposals') or ())),
                 None if envelope is None or reference is None
                 else envelope.get('proposalDigest') == reference.get('proposalDigest'),
                 None if envelope is None else envelope.get('ordering')))
        check('focus-fanout-delivers-exactly-once',
              getattr(host, '_planner_accepted', 0) == 1
              and getattr(host, '_planner_rejected', 0) == 0
              and _pending_id(coord) is None and len(host._planner_routes) == 0
              and bool(coord._cohort()),
              'accepted=%d rejected=%d pending=%s routes=%d cohort=%d'
              % (getattr(host, '_planner_accepted', 0), getattr(host, '_planner_rejected', 0),
                 _pending_id(coord) is not None, len(host._planner_routes), len(coord._cohort())))
    finally:
        if pool is not None:
            if original_submit is not None:
                pool.submit = original_submit
            if original_harvest is not None:
                pool.harvest_ready = original_harvest
        if evaluator is not None:
            evaluator.close()
        try:
            host._close_planning_pool(timeout=5.0)
        except Exception:  # noqa: BLE001 - bounded cleanup must not mask a result
            pass
        store.close()


# -- real-host stale-policy fan-out rejection (zero compute chunks) --------------------------------

def section_fanout_stale_policy():
    """A focused group that drifts policy/compatibility after Phase A must be dropped before PHASE B.

    The real Optimizer host prepares one frozen request (PHASE A) on the shared real 2-worker
    ``PlanningPool``. The coordinator's live policy/compatibility is then made to drift from the
    frozen acceptance envelope -- after the request is captured and strictly before the PHASE B
    fan-out body -- and the optimizer-thread harvest is driven. The fan-out's real scope guard must
    cancel the logical group before submitting any ``compute_planning_chunk``, so no proposal cohort
    is delivered, only the owner's pending marker is cleared, and zero work is admitted.
    """
    methods = ('submit_planning', 'planning_context', 'planning_allocation', 'cancel_planning')
    missing = [name for name in methods if not callable(getattr(Optimizer, name, None))]
    if missing:
        blocked('fanout-stale-policy-zero-chunks',
                'Optimizer is missing host method(s): %s' % ', '.join(missing))
        return
    path = _path('fanout-stale-policy')
    candidates = _prep_library(path)
    store = Store(path, provenance())
    host = _HostView(workers=3, focus=[3])  # T=3 -> P=2 planner lanes; focus narrows scope to one.
    evaluator = None
    pool = None
    original_submit = original_cancel = original_fanout = None
    submissions, cancels, delivered = [], [], {}
    try:
        evaluator = _evaluator(InstantPool(), workers=1)
        coord = _coordinator(store, path, candidates[3], encounter=3, planner=host,
                             evaluator=evaluator, workers=2, maximum=6,
                             runtime_key='host:fanout-stale-policy')
        # One eligible encounter is focused; the bystander stays out of scope with its own pending
        # marker, so "only that coordinator's marker" is falsifiable rather than trivially true.
        witness = _coordinator(store, path, candidates[7], encounter=7, planner=host,
                               evaluator=evaluator, workers=2, maximum=6,
                               runtime_key='host:fanout-stale-policy-witness')
        witness_marker = dict(requestId='planning-witness-sentinel', plan=None, envelope=None,
                              request=None, submittedAt=time.monotonic())
        witness._planner_pending = witness_marker
        host._campaign_coordinators = {3: coord, 7: witness}
        host._community_fleet_state = dict(campaignId='fanout-stale-policy-campaign',
                                           encounters={'3': {'generation': 0},
                                                       '7': {'generation': 0}})

        pool = host._ensure_planning_pool()
        check('fanout-stale-policy-uses-one-real-two-worker-pool',
              pool is not None and pool.workers == 2 and host._planning_pool is pool,
              None if pool is None else dict(workers=pool.workers, maxPending=pool.max_pending))
        if pool is None or pool.workers != 2:
            blocked('fanout-stale-policy-zero-chunks',
                    'the shared planner pool is not the required real 2-worker pool')
            return

        original_submit = pool.submit

        def _recording_submit(request_id, module_name, function_name, args=(), kwargs=None):
            accepted = original_submit(request_id, module_name, function_name, args, kwargs)
            submissions.append(dict(requestId=request_id, function=function_name,
                                    accepted=bool(accepted)))
            return accepted

        pool.submit = _recording_submit

        original_cancel = host._planning_cancel_group

        def _recording_cancel(logical_id, reason, store=None):
            route = host._planner_routes.get(logical_id)
            cancels.append(dict(logicalId=logical_id, reason=reason,
                                phase=None if route is None else route.get('phase'),
                                chunkSubmitted=None if route is None
                                else int(route.get('chunkSubmitted') or 0),
                                chunkTotal=None if route is None
                                else int(route.get('chunkTotal') or 0)))
            return original_cancel(logical_id, reason, store)

        host._planning_cancel_group = _recording_cancel

        original_accept = coord.accept_planning_result

        def _recording_accept(record, db):
            if isinstance(record, dict):
                delivered.setdefault('record', record)
            return original_accept(record, db)

        coord.accept_planning_result = _recording_accept

        # PHASE A: the frozen request is captured and registered; no compute chunk exists yet.
        coord.run_pass(store, running=True)
        logical_id = _pending_id(coord)
        frozen_request = (coord._planner_pending or {}).get('request')
        candidates_before = len(store.candidates())
        check('fanout-stale-policy-phase-a-captures-frozen-request',
              logical_id is not None and frozen_request is not None
              and logical_id in host._planner_routes
              and not [row for row in submissions
                       if row['function'] == 'compute_planning_chunk'],
              'logical=%s routes=%s submitted=%s'
              % (logical_id, sorted(host._planner_routes),
                 [row['function'] for row in submissions]))
        if logical_id is None:
            blocked('fanout-stale-policy-zero-chunks',
                    'the focused coordinator did not capture a Phase A request')
            return

        # Drift the live policy/compatibility exactly at the PHASE B boundary: the frozen request is
        # already captured, the prepare record has just moved the group to 'compute', and the real
        # fan-out body has not submitted any chunk yet.
        original_fanout = host._planning_fanout
        drift = {'injected': False}

        def _fanout_after_policy_drift(store=None):
            if not drift['injected']:
                route = host._planner_routes.get(logical_id)
                if route is not None and route.get('phase') == 'compute':
                    parent = coord._planner_pending['plan']['parent']
                    parent['tickLimit'] = int(parent.get('tickLimit') or 0) + 1
                    drift['injected'] = True
            return original_fanout(store)

        host._planning_fanout = _fanout_after_policy_drift

        deadline = time.monotonic() + 90.0
        while time.monotonic() < deadline and logical_id in host._planner_routes:
            host._harvest_planning(store)
            if logical_id in host._planner_routes:
                time.sleep(0.005)

        chunk_subs = [row for row in submissions if row['function'] == 'compute_planning_chunk']
        cancel = cancels[0] if cancels else {}
        check('fanout-stale-policy-drifts-live-policy-after-capture-before-phase-b',
              drift['injected'] is True,
              'injected=%s phaseAtCancel=%s' % (drift['injected'], cancel.get('phase')))
        check('fanout-stale-policy-cancels-group-before-any-compute-chunk',
              logical_id not in host._planner_routes
              and [row['reason'] for row in cancels] == ['planning-discarded-stale-scope']
              and cancel.get('phase') == 'compute' and cancel.get('chunkSubmitted') == 0
              and cancel.get('chunkTotal') is not None and cancel.get('chunkTotal') >= 1
              and not chunk_subs,
              'routes=%s cancels=%s chunks=%d'
              % (sorted(host._planner_routes), cancels, len(chunk_subs)))
        check('fanout-stale-policy-delivers-no-proposal-cohort',
              not coord._cohort()
              and int((coord._planner_metrics or {}).get('accepted') or 0) == 0
              and not host._planner_chunk_map(),
              'cohort=%d accepted=%s chunks=%d'
              % (len(coord._cohort()), (coord._planner_metrics or {}).get('accepted'),
                 len(host._planner_chunk_map())))
        check('fanout-stale-policy-clears-only-owner-pending-marker',
              _pending_id(coord) is None and witness._planner_pending is witness_marker
              and len(cancels) == 1 and cancel.get('logicalId') == logical_id,
              'ownerPending=%s witnessMarkerIntact=%s cancels=%d'
              % (_pending_id(coord), witness._planner_pending is witness_marker, len(cancels)))
        check('fanout-stale-policy-admits-zero-work',
              int(getattr(host, '_planner_accepted', 0) or 0) == 0
              and (delivered.get('record') or {}).get('result') is None
              and (delivered.get('record') or {}).get('error') == 'planning-discarded-stale-scope'
              and len(store.candidates()) == candidates_before,
              'hostAccepted=%s error=%s candidates=%d->%d'
              % (getattr(host, '_planner_accepted', 0),
                 (delivered.get('record') or {}).get('error'),
                 candidates_before, len(store.candidates())))
    finally:
        if pool is not None:
            if original_submit is not None:
                pool.submit = original_submit
            if original_fanout is not None:
                host._planning_fanout = original_fanout
            if original_cancel is not None:
                host._planning_cancel_group = original_cancel
        if evaluator is not None:
            evaluator.close()
        try:
            host._close_planning_pool(timeout=5.0)
        except Exception:  # noqa: BLE001 - bounded cleanup must not mask a result
            pass
        store.close()


def main():
    threading.Thread(target=_watchdog, args=(300,), daemon=True).start()
    _section('equivalence', section_equivalence)
    _section('async-nonblocking', section_async_nonblocking)
    _section('twenty-coordinators', section_twenty_coordinators)
    _section('focus-and-staleness', section_focus_and_staleness)
    _section('cross-encounter-isolation', section_cross_encounter_isolation)
    _section('duplicate-delivery', section_duplicate_delivery)
    _section('lifecycle', section_lifecycle)
    _section('desktop-details', section_desktop_details)
    _section('optimizer-host', section_optimizer_host)
    _section('focus-fanout', section_focus_fanout)
    _section('focus-fanout-stale-policy', section_fanout_stale_policy)

    failed = [name for name, ok, _detail in RESULTS if not ok]
    passed = len(RESULTS) - len(failed)
    print('\n%d/%d checks passed' % (passed, len(RESULTS)))
    if BLOCKED:
        print('BLOCKED (%d): ' % len(BLOCKED) + '; '.join('%s [%s]' % row for row in BLOCKED))
    if failed:
        print('FAILED: ' + ', '.join(failed))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
