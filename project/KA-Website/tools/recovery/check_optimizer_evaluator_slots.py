"""Focused regression: the campaign evaluator must use every configured battle slot.

Run:  python tools/recovery/check_optimizer_evaluator_slots.py

The production desktop campaign starts N encounters that share ONE evaluator whose worker count is
the EFFECTIVE battle-worker count (requested total minus the two shared planner workers).
``Optimizer._campaign_fleet_pass`` hands each encounter a per-pass submission quota.  The earlier
quota divided the free pool by EVERY encounter in the campaign, so with at least ``battle_workers``
encounters each encounter was capped at one slot per pass; when every other encounter was idle or had
nothing ready, the configured battle workers sat idle (the measured desktop state: one executing,
nine idle).

This check drives the REAL production objects: ``Optimizer._campaign_start`` (one shared evaluator,
per-encounter coordinators, real sessions/ledger), the real
``strategy_encounter_evaluation.Evaluator`` (real slot allocation, reservation and budget accounting)
against a deterministic in-flight pool injected through the documented ``EVALUATOR_FACTORY`` seam, and
the real ``Optimizer._campaign_fleet_pass`` dispatch loop.  Native battles are replaced by controlled
futures; no live library, no desktop run and no configured library is touched here.

It also carries one DIRECT ``HeadlessPool`` control with a frozen scenario and ten independent jobs:
ten accepted futures, ten concurrently occupied worker interpreters (their own reported leaf /
interpreter PIDs, never the launchers), and no executor serialisation.  When the headless runtime is
absent the control is reported UNVERIFIED rather than silently passing.
"""
from __future__ import annotations

import os
import shutil
import sys
import time
import uuid
from concurrent.futures import Future
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# No battle tracing: this check must never write a trace file.
os.environ.pop('KA_OPTIMIZER_BATTLE_TRACE', None)
os.environ.pop('KA_OPTIMIZER_BATTLE_TRACE_SAMPLE', None)

import strategy_community_campaign as cc            # noqa: E402
import strategy_encounter_evaluation as evaluation  # noqa: E402
import strategy_encounter_search as search          # noqa: E402
import strategy_experiment_store as ledger          # noqa: E402
import strategy_optimizer as so                     # noqa: E402
import strategy_optimizer_adapter as adapter        # noqa: E402
import strategy_students as students                # noqa: E402

RESULTS = []
BLOCKED = []
CREATED_DIRS = []


def check(name, condition, detail=''):
    RESULTS.append((name, bool(condition), detail))
    print(('PASS ' if condition else 'FAIL ') + name + ((' :: ' + str(detail)) if detail else ''))


def unverified(name, reason):
    BLOCKED.append((name, reason))
    print('UNVERIFIED ' + name + ' :: ' + reason)


WORK_DIR = HERE.parents[1] / 'tmp' / 'evaluator-slots-20260929'


def _new_dir(tag):
    path = WORK_DIR / ('%s-%s' % (tag, uuid.uuid4().hex[:8]))
    path.mkdir(parents=True, exist_ok=True)
    CREATED_DIRS.append(path)
    return path


# -- deterministic in-flight pool injected through the real EVALUATOR_FACTORY seam ------------------

class SlotPool:
    """Accepted futures stay pending until the test releases them, so slots stay occupied.

    This stands in for the native ``HeadlessPool`` only: the real ``Evaluator`` above it performs the
    real slot acquisition, ledger reservation, harvest/release and budget accounting under test.
    """
    supports_job_trace = False

    def __init__(self):
        self._jobs = []
        self.shutdown_called = False

    def submit(self, function, scenario, seeds, trace=False):
        future = Future()
        self._jobs.append((future, list(seeds)))
        return future

    def release_all(self):
        released = 0
        for future, seeds in self._jobs:
            if not future.done():
                future.set_result(dict(seeds=list(seeds), verdict=1, resultBackend='native',
                                       rewardOutcome=dict(pendingChests=1), elapsedSeconds=0.01,
                                       cpuSeconds=0.0, digest='slot-fixture'))
                released += 1
        return released

    def shutdown(self, wait=True, cancel_futures=False):
        self.shutdown_called = True

    def terminate_workers(self):
        self.shutdown_called = True


CURRENT_POOL = [None]


def evaluator_factory(*, workers, telemetry, timeout):
    return evaluation.Evaluator(workers=workers, telemetry=telemetry, pool=CURRENT_POOL[0],
                                timeout=timeout or 60.0)


search.EVALUATOR_FACTORY = evaluator_factory


# -- runtime rig (throwaway library + real Optimizer campaign start) --------------------------------

def make_runtime(ids, tag):
    """A throwaway library plus the real campaign start path (mirrors the integration check)."""
    path = _new_dir(tag) / 'lib.sqlite'
    provenance = adapter.provenance()
    store = so.Store(str(path), provenance)
    opt = so.Optimizer.__new__(so.Optimizer)
    opt.path = str(path)
    opt.provenance = provenance
    opt.battleCompatibilityRevision = 'check'
    opt.runtimeRevision = 'check'
    opt.encounters = {str(value): {} for value in ids}
    opt._encounter = search.Coordinator(path=str(path), revision='check', workers=1,
                                        telemetry=False, maximum=2)
    opt._encounter.load(store)
    opt._encounter_snapshot = lambda _store: dict(opt._encounter.__dict__)
    opt._encounter_report = opt._encounter.report()
    opt._student_shares = students.shares()
    opt._track_shares = {track.name: 0.0 for track in students.tracks()}
    opt._encounter_previous_shares = None
    opt._encounter_previous_tracks = None
    opt._encounter_preview_token = None
    opt._encounter_ledger = None
    opt._encounter_session_base = None
    opt._planner_dirty = True
    opt._campaign_started = False
    opt._campaign_report = None
    opt._session_since = None
    opt._session_active = 0.0
    opt._session_live = True

    def preview(value):
        return opt._preview_encounter(store, dict(value))

    def activate(value):
        return opt._activate_encounter(store, dict(value))

    opt._campaign = cc.CommunityCampaign(encounter_ids=list(ids), preview=preview,
                                         activate=activate, commit=store.db.commit, focus=[])
    return opt, store


def _close(opt, store):
    evaluator = getattr(opt, '_community_evaluator', None)
    if evaluator is not None:
        try:
            evaluator.close()
        except Exception:  # noqa: BLE001 - teardown must not mask a result
            pass
    try:
        store.close()
    except Exception:  # noqa: BLE001
        pass


def _force_idle(opt, store, *, keep=()):
    """White-box: park every coordinator except ``keep`` so ``_has_work()`` is False for them."""
    for encounter_id, coord in opt._campaign_coordinators.items():
        if encounter_id in keep:
            continue
        coord.state['cohort'] = []
        coord.state['current'] = None
        coord.state['confirmation'] = None
        coord.state['idle'] = True
        coord._planning_dirty = False
        # Mark the db already synced so the next pass does not re-raise _planning_dirty and re-plan.
        coord._db_ref = store.db
        coord._initialized_db = store.db


def _reserved_count(store, experiment_id):
    return int(store.db.execute('SELECT COUNT(*) FROM ea_sample_link WHERE experiment_id=?',
                                (experiment_id,)).fetchone()[0])


def _experiment_budget(store, experiment_id):
    row = store.db.execute('SELECT total, reserved, completed FROM ea_experiment_budget '
                           'WHERE experiment_id=?', (experiment_id,)).fetchone()
    return dict(total=int(row[0]), reserved=int(row[1]), completed=int(row[2]))


def _fixture_scenario(encounter):
    return {'encounterId': int(encounter), 'finishPolicy': 'on-verdict', 'horizon': 1,
            'consumables': {}, 'ownUnits': [], 'foeUnits': []}


def attach_cohort(coord, store, *, jobs=12, budget=64):
    """Freeze one immutable cohort member with ``jobs`` ready (unreserved) frozen jobs."""
    encounter = int(coord.encounter)
    scenario = _fixture_scenario(encounter)
    candidate_id = 'slot-%d-c0' % encounter
    base = 900_000_000 + encounter * 10_000
    job_list = []
    for index in range(jobs):
        seed_a = base + index * 2
        job_list.append({'candidateId': candidate_id, 'seedPair': [seed_a, seed_a + 1],
                         'arm': 'candidate'})
    engine_revision = coord.revision
    mechanics_revision = coord._mechanics_revision()
    encounter_revision = coord._encounter_revision(scenario)
    compatibility = coord._compatibility()
    policy = dict(coord.state.get('policy') or {})
    intent = dict(scope='community', owner='community', ownerShare=1.0, parentId=None,
                  referenceId=None, purpose='improvement', sessionId=coord.session_id,
                  planned_budget=int(budget),
                  stopping={'rule': 'slot-fixture', 'maxRuns': int(budget)},
                  policy=policy, mechanicsRevision=mechanics_revision,
                  encounterRevision=encounter_revision, engineRevision=engine_revision,
                  compatibility=compatibility, measurementWindow='development', jobs=[],
                  observedScenarios={candidate_id: scenario})
    experiment_id = ledger.create_experiment(store.db, intent)
    member = dict(experimentId=experiment_id, candidateId=candidate_id, referenceId=None,
                  purpose='improvement', owner='community', ownerShare=1.0, planned=int(jobs),
                  plannedPairs=max(1, int(jobs) // 2), reserved=0, completed=0,
                  proposalId='slot-fixture', parentId=None, jobs=job_list,
                  observedCandidate=scenario, observedReference=None, policy=policy,
                  blocked=False, compatibility=compatibility,
                  mechanicsRevision=mechanics_revision, engineRevision=engine_revision,
                  encounterRevision=encounter_revision)
    coord.state['cohort'] = [member]
    coord.state['current'] = member
    coord.state['idle'] = False
    coord._planning_dirty = False
    coord._db_ref = store.db
    coord._initialized_db = store.db
    return experiment_id, candidate_id, scenario


# -- bounded 20-encounter / 10-battle-slot fixture + in-harness old-quota reference -----------------

CAMPAIGN_IDS_20 = list(range(20))
REQUESTED_WORKERS_20 = 12   # -> 2 shared planners + 10 battle slots (the fixed effective split)
BATTLE_SLOTS_20 = 10


def build_twenty_campaign(tag, pool):
    """A real temporary campaign: EXACTLY 20 encounter coordinators sharing 10 battle slots."""
    CURRENT_POOL[0] = pool
    opt, store = make_runtime(CAMPAIGN_IDS_20, tag)
    opt._campaign_start(store, {'workers': REQUESTED_WORKERS_20, 'duty': 1})
    _force_idle(opt, store)
    return opt, store


def legacy_fleet_pass(opt, store, *, running=True):
    """The PRE-FIX dispatch loop, re-implemented here in the harness only.

    The old quota divided the free pool by EVERY encounter in the campaign, so a campaign with at
    least ``battle_workers`` encounters capped every encounter at one slot per pass. This is written
    from scratch inside the test (it never reads or writes the production module on disk) purely so
    the fixture can demonstrate the capacity the fixed quota now recovers.
    """
    import math as _math
    coordinators = getattr(opt, '_campaign_coordinators', None) or {}
    if not coordinators:
        return None
    evaluator = opt._community_evaluator
    harvested = evaluator.harvest(store.db)
    by_session = {}
    for entry in harvested:
        row = store.db.execute('SELECT session_id FROM ea_experiment WHERE id=?',
                               (entry.get('experimentId'),)).fetchone()
        if row is not None:
            by_session.setdefault(row[0], []).append(entry)
    ids = sorted(coordinators)
    battle_workers = opt._battle_worker_count(opt._workers)
    capacity_left = max(0, battle_workers - len(evaluator.pending))
    recovery_snapshot = None
    if running and capacity_left > 0:
        recovery_snapshot = ledger.RecoverySnapshot(store.db)
    for encounter_id in ids:
        coord = coordinators[encounter_id]
        # OLD policy: split the free pool across EVERY encounter, not just the runnable ones.
        coord._submission_quota = (max(1, _math.ceil(capacity_left / len(ids)))
                                   if capacity_left else 0)
        pending_before = len(evaluator.pending)
        entries = by_session.get(coord.session_id, [])
        coord.run_pass(store, running=running, harvested_entries=entries,
                       recovery_snapshot=recovery_snapshot)
        used = max(0, len(evaluator.pending) - pending_before)
        capacity_left = max(0, capacity_left - used)
    return None


def _counts_by_encounter(evaluator, exp_to_enc):
    """Map the evaluator's pending jobs back to the encounter that owns each experiment."""
    counts = {}
    for key in evaluator.pending:
        encounter_id = exp_to_enc.get(key[0])
        counts[encounter_id] = counts.get(encounter_id, 0) + 1
    return counts


# -- sections --------------------------------------------------------------------------------------

def section_worker_split():
    """The requested total is split by formula into planner + battle workers (never hardcoded)."""
    inst = so.Optimizer.__new__(so.Optimizer)
    inst._workers = 12
    cases = {12: (2, 10), 8: (2, 6), 24: (2, 22), 5: (2, 3), 3: (2, 1), 2: (1, 1), 1: (0, 1)}
    for total, (planners, battles) in sorted(cases.items()):
        got_p = inst._planner_worker_count(total)
        got_b = inst._battle_worker_count(total)
        check('worker-split-%d-is-planner-%d-battle-%d' % (total, planners, battles),
              (got_p, got_b) == (planners, battles)
              and got_p + got_b == total and got_b >= 1,
              {'planner': got_p, 'battle': got_b})
    check('worker-split-is-not-a-stored-constant',
          inst._battle_worker_count(12) == 10 and inst._battle_worker_count(8) == 6
          and inst._battle_worker_count(5) == 3)


def section_shared_evaluator():
    """A campaign start binds every coordinator to ONE evaluator sized to the battle workers."""
    ids = [11, 12, 13]
    CURRENT_POOL[0] = SlotPool()
    opt, store = make_runtime(ids, 'shared')
    try:
        opt._campaign_start(store, {'workers': 12, 'duty': 1})
        evaluator = opt._community_evaluator
        check('campaign-12-shares-one-10-worker-evaluator',
              evaluator is not None and evaluator.workers == 10
              and len(evaluator._slot_ready) == 10,
              {'workers': getattr(evaluator, 'workers', None),
               'slots': len(getattr(evaluator, '_slot_ready', []))})
        check('every-encounter-shares-the-one-evaluator',
              all(coord.evaluator is evaluator
                  for coord in opt._campaign_coordinators.values()))
        check('every-encounter-has-effective-battle-workers',
              all(coord.workers == 10 for coord in opt._campaign_coordinators.values()),
              [coord.workers for coord in opt._campaign_coordinators.values()])
        opt._campaign_start(store, {'workers': 12, 'duty': 1})
        check('restart-same-request-reuses-evaluator',
              opt._community_evaluator is evaluator)
    finally:
        _close(opt, store)


def section_slot_occupancy():
    """One runnable encounter may occupy all ten slots even with twelve campaign encounters."""
    ids = list(range(12))
    pool = SlotPool()
    CURRENT_POOL[0] = pool
    opt, store = make_runtime(ids, 'occupancy')
    try:
        opt._campaign_start(store, {'workers': 12, 'duty': 1})
        evaluator = opt._community_evaluator
        _force_idle(opt, store)
        target = sorted(opt._campaign_coordinators)[0]
        coord = opt._campaign_coordinators[target]
        experiment_id, candidate, scenario = attach_cohort(coord, store, jobs=12, budget=64)
        opt._campaign_fleet_pass(store, running=True)
        slots = sorted(job.get('slot') for job in evaluator.pending.values())
        check('single-runnable-encounter-occupies-ten-distinct-slots',
              slots == list(range(10)), slots)
        check('occupancy-is-ten-not-twelve', len(evaluator.pending) == 10, len(evaluator.pending))
        check('occupancy-reserves-exactly-ten',
              _reserved_count(store, experiment_id) == 10,
              _reserved_count(store, experiment_id))
        before = _reserved_count(store, experiment_id)
        accepted = evaluator.submit(store.db, experiment_id, candidate, scenario, [2_000_000_000, 2_000_000_001])
        check('eleventh-submit-refused-on-a-full-pool', accepted is False)
        check('refused-submit-reserved-nothing',
              _reserved_count(store, experiment_id) == before, _reserved_count(store, experiment_id))
        occupied = set(evaluator.pending)
        released = pool.release_all()
        check('release-provided-ten-results', released == 10, released)
        opt._campaign_fleet_pass(store, running=True)
        still = sorted(occupied & set(evaluator.pending))
        check('harvest-releases-every-occupied-slot', not still, still)
        check('harvest-completes-ten-samples',
              _experiment_budget(store, experiment_id)['completed'] == 10)
        slots_after = sorted(job.get('slot') for job in evaluator.pending.values())
        check('second-pass-refills-only-the-unreserved-jobs', slots_after == [0, 1], slots_after)
        check('no-duplicate-reservation-after-harvest',
              _reserved_count(store, experiment_id) == 12,
              _reserved_count(store, experiment_id))
        budget = _experiment_budget(store, experiment_id)
        check('experiment-budget-accounting-is-exact',
              budget == dict(total=64, reserved=12, completed=10), budget)
        pool.release_all()
        opt._campaign_fleet_pass(store, running=True)
        budget = _experiment_budget(store, experiment_id)
        check('budget-completes-without-duplicates',
              not evaluator.pending and budget == dict(total=64, reserved=12, completed=12), budget)
    finally:
        _close(opt, store)


def section_fairness():
    """Three runnable encounters share the ten slots fairly; an idle encounter holds none."""
    ids = list(range(8))
    pool = SlotPool()
    CURRENT_POOL[0] = pool
    opt, store = make_runtime(ids, 'fairness')
    try:
        opt._campaign_start(store, {'workers': 12, 'duty': 1})
        evaluator = opt._community_evaluator
        _force_idle(opt, store)
        runnable = sorted(opt._campaign_coordinators)[:3]
        for encounter_id in runnable:
            attach_cohort(opt._campaign_coordinators[encounter_id], store, jobs=12, budget=64)
        opt._campaign_fleet_pass(store, running=True)
        counts = {}
        for key in evaluator.pending:
            counts[key[0]] = counts.get(key[0], 0) + 1
        check('fairness-fills-every-slot', len(evaluator.pending) == 10, len(evaluator.pending))
        check('fairness-serves-all-three-runnable-encounters',
              len(counts) == 3 and all(count >= 1 for count in counts.values()), counts)
        check('fairness-no-encounter-hogs-more-than-its-share',
              all(count <= 4 for count in counts.values()), counts)
        idle_quota = [coord._submission_quota for encounter_id, coord
                      in opt._campaign_coordinators.items() if encounter_id not in runnable]
        check('idle-encounters-hold-no-quota', all(quota == 0 for quota in idle_quota), idle_quota)
    finally:
        _close(opt, store)


def section_rollover_and_recovery():
    """A worker-count change rebuilds once; a rollover adopts the shared evaluator unchanged."""
    ids = [3, 7, 11]
    CURRENT_POOL[0] = SlotPool()
    opt, store = make_runtime(ids, 'rollover')
    try:
        opt._campaign_start(store, {'workers': 8, 'duty': 1})
        ev6 = opt._community_evaluator
        check('start-8-shares-one-6-worker-evaluator',
              ev6 is not None and ev6.workers == 6 and len(ev6._slot_ready) == 6,
              {'workers': getattr(ev6, 'workers', None),
               'slots': len(getattr(ev6, '_slot_ready', []))})
        check('start-8-coordinators-have-6-workers',
              all(coord.workers == 6 for coord in opt._campaign_coordinators.values()))
        opt._campaign_start(store, {'workers': 8, 'duty': 1})
        check('restart-8-keeps-the-same-evaluator', opt._community_evaluator is ev6)
        opt._campaign_start(store, {'workers': 12, 'duty': 1})
        ev10 = opt._community_evaluator
        check('restart-12-rebuilds-one-10-worker-evaluator',
              ev10 is not ev6 and ev10.workers == 10 and ev6.closed is True,
              {'workers': ev10.workers, 'oldClosed': ev6.closed})

        # Force a drained-encounter rollover and confirm it adopts the shared 10-worker evaluator.
        victim = sorted(opt._campaign_coordinators)[0]
        _force_idle(opt, store)
        coord = opt._campaign_coordinators[victim]
        coord.state['completedExperiments'] = ['rollover-proof']
        opt._campaign_fleet_pass(store, running=True)
        replacement = opt._campaign_coordinators[victim]
        check('rollover-replacement-keeps-shared-evaluator',
              replacement is not coord and replacement.evaluator is ev10,
              {'replaced': replacement is not coord,
               'shared': replacement.evaluator is ev10})
        check('rollover-does-not-downgrade-to-one-worker',
              replacement.workers == 10 and ev10.workers == 10
              and len(ev10._slot_ready) == 10,
              {'workers': replacement.workers, 'sharedWorkers': ev10.workers})
        opt._campaign_fleet_pass(store, running=True)
        check('recovery-pass-keeps-the-shared-evaluator',
              opt._community_evaluator is ev10 and ev10.workers == 10)
    finally:
        _close(opt, store)


def section_old_quota_reference_vs_fixed():
    """20 coordinators, 10 battle slots, only encounter 0 holds work: the fix frees the pool.

    The old quota (free pool divided by ALL 20 encounters) admits a single job and strands nine
    slots. The real fixed ``_campaign_fleet_pass`` then admits ten distinct evaluator slots.
    """
    pool = SlotPool()
    opt, store = build_twenty_campaign('old-vs-fixed', pool)
    try:
        evaluator = opt._community_evaluator
        check('fixture-has-exactly-twenty-coordinators',
              len(opt._campaign_coordinators) == 20, len(opt._campaign_coordinators))
        check('fixture-has-exactly-ten-battle-slots',
              evaluator.workers == BATTLE_SLOTS_20
              and len(evaluator._slot_ready) == BATTLE_SLOTS_20,
              {'workers': evaluator.workers, 'slots': len(evaluator._slot_ready)})
        target = sorted(opt._campaign_coordinators)[0]
        experiment_id, _candidate, _scenario = attach_cohort(
            opt._campaign_coordinators[target], store, jobs=20, budget=64)
        legacy_fleet_pass(opt, store, running=True)
        admitted = len(evaluator.pending)
        check('old-quota-reference-admits-exactly-one', admitted == 1, admitted)
        check('old-quota-reference-reserves-exactly-one',
              _reserved_count(store, experiment_id) == 1, _reserved_count(store, experiment_id))
        check('old-quota-reference-leaves-nine-slots-unused',
              BATTLE_SLOTS_20 - admitted == 9,
              {'admitted': admitted, 'unused': BATTLE_SLOTS_20 - admitted})
        pool.release_all()
        opt._campaign_fleet_pass(store, running=True)
        slots = sorted(job.get('slot') for job in evaluator.pending.values())
        check('fixed-quota-admits-ten-distinct-slots', slots == list(range(BATTLE_SLOTS_20)), slots)
        check('fixed-quota-occupies-all-ten-battle-slots',
              len(evaluator.pending) == BATTLE_SLOTS_20, len(evaluator.pending))
    finally:
        _close(opt, store)


def section_ten_runnable_encounters():
    """20 coordinators, exactly 10 runnable: one authorised job each, ten total."""
    pool = SlotPool()
    opt, store = build_twenty_campaign('ten-runnable', pool)
    try:
        evaluator = opt._community_evaluator
        runnable = sorted(opt._campaign_coordinators)[:10]
        exp_to_enc = {}
        for encounter_id in runnable:
            experiment_id, _candidate, _scenario = attach_cohort(
                opt._campaign_coordinators[encounter_id], store, jobs=4, budget=64)
            exp_to_enc[experiment_id] = encounter_id
        opt._campaign_fleet_pass(store, running=True)
        counts = _counts_by_encounter(evaluator, exp_to_enc)
        check('ten-runnable-total-is-ten', len(evaluator.pending) == 10, len(evaluator.pending))
        check('ten-runnable-one-job-per-active-encounter',
              sorted(counts) == sorted(runnable) and all(count == 1 for count in counts.values()),
              counts)
        check('ten-runnable-no-inactive-encounter-served',
              set(counts) <= set(runnable), sorted(counts))
    finally:
        _close(opt, store)


def section_two_runnable_encounters_split_evenly():
    """20 coordinators, exactly 2 runnable with abundant work: ten total, fair 5/5."""
    pool = SlotPool()
    opt, store = build_twenty_campaign('two-runnable', pool)
    try:
        evaluator = opt._community_evaluator
        runnable = sorted(opt._campaign_coordinators)[:2]
        exp_to_enc = {}
        for encounter_id in runnable:
            experiment_id, _candidate, _scenario = attach_cohort(
                opt._campaign_coordinators[encounter_id], store, jobs=12, budget=64)
            exp_to_enc[experiment_id] = encounter_id
        opt._campaign_fleet_pass(store, running=True)
        counts = _counts_by_encounter(evaluator, exp_to_enc)
        check('two-runnable-total-is-ten', len(evaluator.pending) == 10, len(evaluator.pending))
        check('two-runnable-both-encounters-served', sorted(counts) == sorted(runnable), counts)
        check('two-runnable-split-is-five-five', sorted(counts.values()) == [5, 5], counts)
    finally:
        _close(opt, store)


def section_cursor_rotation_no_starvation():
    """20 runnable encounters: the rotating cursor admits later encounters without starvation."""
    pool = SlotPool()
    opt, store = build_twenty_campaign('cursor', pool)
    try:
        evaluator = opt._community_evaluator
        exp_to_enc = {}
        for encounter_id in sorted(opt._campaign_coordinators):
            experiment_id, _candidate, _scenario = attach_cohort(
                opt._campaign_coordinators[encounter_id], store, jobs=40, budget=96)
            exp_to_enc[experiment_id] = encounter_id
        served_per_pass = []
        served = set()
        for _ in range(24):
            opt._campaign_fleet_pass(store, running=True)
            this_pass = set(_counts_by_encounter(evaluator, exp_to_enc))
            served_per_pass.append(this_pass)
            served |= this_pass
            pool.release_all()
        check('cursor-first-pass-serves-the-first-ten',
              served_per_pass[0] == set(CAMPAIGN_IDS_20[:10]), sorted(served_per_pass[0]))
        check('cursor-second-pass-admits-a-later-encounter',
              CAMPAIGN_IDS_20[10] in served_per_pass[1], sorted(served_per_pass[1]))
        check('cursor-every-pass-uses-all-ten-slots',
              all(len(group) == 10 for group in served_per_pass),
              [len(group) for group in served_per_pass])
        check('cursor-encounters-ten-to-nineteen-eventually-served',
              set(CAMPAIGN_IDS_20[10:]) <= served,
              sorted(set(CAMPAIGN_IDS_20[10:]) - served))
        check('cursor-no-encounter-starves',
              served == set(CAMPAIGN_IDS_20), sorted(set(CAMPAIGN_IDS_20) - served))
    finally:
        _close(opt, store)


def section_no_work_fabricates_nothing():
    """All 20 coordinators idle: no pending job, reservation or quota is fabricated."""
    pool = SlotPool()
    opt, store = build_twenty_campaign('no-work', pool)
    try:
        evaluator = opt._community_evaluator
        opt._campaign_fleet_pass(store, running=True)
        check('idle-campaign-fabricates-no-pending-jobs', not evaluator.pending,
              dict(evaluator.pending))
        samples = store.db.execute('SELECT COUNT(*) FROM ea_sample_link').fetchone()[0]
        check('idle-campaign-fabricates-no-reservations', int(samples) == 0, samples)
        quotas = [coord._submission_quota for coord in opt._campaign_coordinators.values()]
        check('idle-campaign-holds-no-quota', all(quota == 0 for quota in quotas), quotas)
    finally:
        _close(opt, store)


def section_authorization_boundary_four_jobs():
    """A frozen plan with only four authorised jobs can reserve at most four of ten free slots."""
    pool = SlotPool()
    opt, store = build_twenty_campaign('authorization', pool)
    try:
        evaluator = opt._community_evaluator
        target = sorted(opt._campaign_coordinators)[0]
        experiment_id, _candidate, _scenario = attach_cohort(
            opt._campaign_coordinators[target], store, jobs=4, budget=64)
        opt._campaign_fleet_pass(store, running=True)
        check('four-job-plan-reserves-at-most-four',
              len(evaluator.pending) == 4, len(evaluator.pending))
        check('four-job-plan-reserves-exactly-the-frozen-four',
              _reserved_count(store, experiment_id) == 4, _reserved_count(store, experiment_id))
        check('four-job-plan-does-not-fill-idle-slots',
              BATTLE_SLOTS_20 - len(evaluator.pending) == 6,
              {'pending': len(evaluator.pending),
               'free': BATTLE_SLOTS_20 - len(evaluator.pending)})
        opt._campaign_fleet_pass(store, running=True)
        check('four-job-plan-cannot-fabricate-a-fifth',
              len(evaluator.pending) == 4 and _reserved_count(store, experiment_id) == 4,
              {'pending': len(evaluator.pending),
               'reserved': _reserved_count(store, experiment_id)})
    finally:
        _close(opt, store)


def section_pause_resume_boundary():
    """A paused pass submits nothing; resuming dispatches the same still-frozen work."""
    pool = SlotPool()
    opt, store = build_twenty_campaign('pause', pool)
    try:
        evaluator = opt._community_evaluator
        target = sorted(opt._campaign_coordinators)[0]
        experiment_id, _candidate, _scenario = attach_cohort(
            opt._campaign_coordinators[target], store, jobs=12, budget=64)
        opt._campaign_fleet_pass(store, running=False)
        check('paused-pass-submits-nothing', not evaluator.pending, dict(evaluator.pending))
        check('paused-pass-reserves-nothing',
              _reserved_count(store, experiment_id) == 0, _reserved_count(store, experiment_id))
        opt._campaign_fleet_pass(store, running=True)
        check('resumed-pass-dispatches-the-frozen-work',
              len(evaluator.pending) == BATTLE_SLOTS_20, len(evaluator.pending))
        check('resumed-pass-reserves-the-frozen-work',
              _reserved_count(store, experiment_id) == BATTLE_SLOTS_20,
              _reserved_count(store, experiment_id))
    finally:
        _close(opt, store)


def section_headless_pool_control():
    """Direct HeadlessPool capacity control: ten independent jobs, ten occupied interpreters."""
    import strategy_optimizer_fast as fast
    from strategy_optimizer import worker

    if not fast.runtime_path().is_file():
        unverified('headless-pool-ten-independent-jobs',
                   'headless runtime not installed (runtime_path=%s)' % fast.runtime_path())
        return
    scenario = dict(adapter.default_scenario(), tickLimit=20000)
    pool = fast.HeadlessPool(10, telemetry=False, trace=True)
    try:
        futures = [pool.submit(worker, scenario, [1000 + index, 2000 + index], trace=True)
                   for index in range(10)]
        check('headless-pool-accepted-ten-futures', len(futures) == 10
              and all(not future.cancelled() for future in futures), len(futures))
        max_executing = 0
        min_available = 10
        deadline = time.monotonic() + 120.0
        while time.monotonic() < deadline and not all(future.done() for future in futures):
            snapshot = pool.snapshot()
            max_executing = max(max_executing, int(snapshot.get('executing') or 0))
            min_available = min(min_available, int(snapshot.get('available') or 0))
            time.sleep(0.002)
        done = [future.done() for future in futures]
        check('headless-pool-completed-ten-futures', all(done), done)
        pids = []
        intervals = []
        for future in futures:
            try:
                result = future.result(timeout=30)
            except Exception as exc:  # noqa: BLE001 - a failed job must be surfaced, not hidden
                check('headless-pool-job-completed', False, '%s: %s' % (type(exc).__name__, exc))
                continue
            trace = getattr(result, 'katrace', None) or {}
            pids.append(trace.get('workerPid'))
            intervals.append((trace.get('pipeWriteAt'), trace.get('pipeReceiveAt'),
                              trace.get('workerLauncherPid')))
        check('headless-pool-reports-ten-distinct-worker-interpreters',
              len(pids) == 10 and len(set(pids)) == 10 and all(pid is not None for pid in pids),
              dict(distinct=len(set(pids)), pids=sorted(p for p in pids if p is not None)))
        check('headless-pool-occupied-all-ten-workers-at-once',
              max_executing == 10 or min_available == 0,
              {'maxExecuting': max_executing, 'minAvailable': min_available})
        starts = [interval[0] for interval in intervals if interval[0] is not None]
        ends = [interval[1] for interval in intervals if interval[1] is not None]
        overlap = bool(starts and ends and max(starts) < min(ends))
        check('headless-pool-no-executor-serialisation', overlap,
              {'maxStart': max(starts) if starts else None,
               'minEnd': min(ends) if ends else None})
        launchers = sorted({interval[2] for interval in intervals if interval[2] is not None})
        print('  headless-pool worker interpreter PIDs: %s' % sorted(set(pids)))
        print('  headless-pool launcher PIDs: %s' % launchers)
    finally:
        pool.shutdown(wait=True, cancel_futures=True)


def main():
    with_time = time.monotonic()
    section_worker_split()
    section_shared_evaluator()
    section_slot_occupancy()
    section_fairness()
    section_rollover_and_recovery()
    section_old_quota_reference_vs_fixed()
    section_ten_runnable_encounters()
    section_two_runnable_encounters_split_evenly()
    section_cursor_rotation_no_starvation()
    section_no_work_fabricates_nothing()
    section_authorization_boundary_four_jobs()
    section_pause_resume_boundary()
    section_headless_pool_control()
    for root in CREATED_DIRS:
        shutil.rmtree(root, ignore_errors=True)
    failures = [name for name, ok, _ in RESULTS if not ok]
    print('\n%d checks, %d failed, %d unverified, %.1fs'
          % (len(RESULTS), len(failures), len(BLOCKED), time.monotonic() - with_time))
    if failures:
        print('FAILED: ' + ', '.join(failures))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
