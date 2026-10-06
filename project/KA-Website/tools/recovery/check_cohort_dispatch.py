"""Focused checks for the bounded parallel Community development cohort (mock evaluator only).

Run:  <venv>/python -B -X utf8 tools/recovery/check_cohort_dispatch.py

The cohort freezes up to ``ceil(workers / IMPROVEMENT_RUNS)`` INDEPENDENT paired challengers from ONE
ranked proposal pool against one shared reference, so a finite 8-battle experiment no longer leaves
worker slots idle. Every check uses a deterministic mock pool; no real battle is launched.
"""
from __future__ import annotations

import sys
import uuid
from concurrent.futures import Future
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import check_encounter_search as base                    # noqa: E402
import strategy_encounter_search as search                # noqa: E402
import strategy_experiment_store as ledger                # noqa: E402
import strategy_mechanics as mechanics                    # noqa: E402
import strategy_search                                    # noqa: E402
from strategy_optimizer import Store, baseline_scenarios  # noqa: E402
from strategy_optimizer_adapter import (default_scenario, provenance,   # noqa: E402
                                        stats)

RESULTS = []


def check(name, condition, detail=''):
    RESULTS.append((name, bool(condition)))
    print(('PASS ' if condition else 'FAIL ') + name + ((' :: ' + str(detail)) if detail else ''))


def new_dir(tag):
    path = base.TMP / ('ka-cohort-%s-%s' % (tag, uuid.uuid4().hex[:8]))
    path.mkdir(parents=True, exist_ok=True)
    return path


def prep(path, encounter=19):
    store = Store(path, provenance())
    seed = default_scenario()
    seed['tickLimit'] = strategy_search.DEFAULT_TICK_LIMIT
    scenario = next(sc for sc, _label in baseline_scenarios(seed)
                    if int(sc['encounterId']) == encounter)
    with store.db:
        candidate_id = store.add(scenario, 'baseline', 'supplied', stats(scenario))
    store.close()
    return int(scenario['encounterId']), candidate_id, scenario


def build_coordinator(path, config, encounter, reference, pool, workers, maximum=12):
    search.EVALUATOR_FACTORY = base.mock_factory(pool)
    store = Store(path, provenance())
    store.set(search.MODE_KEY, dict(enabled=True, mode='community-first', config=config,
                                    encounter=encounter, reference=reference, previous={}))
    coordinator = search.Coordinator(path=path, revision='cohort', workers=workers, telemetry=True,
                                     maximum=maximum, reference=reference, encounter=encounter,
                                     timeout=10.0)
    coordinator.load(store)
    return store, coordinator


def purposes(improvement, comparison=0):
    return search.default_config('community-first', purposes={
        'improvement': improvement, 'boundary': 0, 'support': 0, 'comparison': comparison,
        'exploration': 0})


def budget_row(db, session_id, purpose):
    for row in ledger.session_status(db, session_id)['budgets']:
        if row['purpose'] == purpose:
            return row
    return {}


class GatedPool:
    """Deterministic pool whose futures are released by the test, so drainage is controlled exactly.

    Any build that differs from the supplied baseline earns 100 and the baseline earns 0, so a measured
    challenger always out-earns its reference and the confirmation nominates it (never the incumbent).
    """

    def __init__(self, baseline_own_units):
        self.baseline = baseline_own_units
        self.pending = []

    def submit(self, fn, scenario, seeds):
        future = Future()
        self.pending.append([future, scenario, list(seeds)])
        return future

    def release(self, count):
        released = 0
        for entry in list(self.pending):
            if released >= count:
                break
            future, scenario, seeds = entry
            units = mechanics.normalize_scenario(scenario).get('ownUnits')
            reward = 0 if units == self.baseline else 100
            future.set_result(dict(seeds=list(seeds), verdict=1, resultBackend='native',
                                   rewardOutcome=dict(pendingChests=reward),
                                   elapsedSeconds=0.01, cpuSeconds=0.01))
            self.pending.remove(entry)
            released += 1
        return released

    def shutdown(self, wait=True):
        pass

    def terminate_workers(self):
        pass


def member_pairs(member):
    candidate, reference = set(), set()
    for job in member.get('jobs') or []:
        pair = (int(job['seedPair'][0]), int(job['seedPair'][1]))
        (candidate if job['candidateId'] == member['candidateId'] else reference).add(pair)
    return candidate, reference


def section_parallel_fill():
    path = new_dir('fill') / 'lib.sqlite'
    encounter, supplied, scenario = prep(path)
    baseline = mechanics.normalize_scenario(scenario).get('ownUnits')
    pool = GatedPool(baseline)
    store, coordinator = build_coordinator(path, purposes(128), encounter, supplied, pool, workers=24)
    coordinator.run_pass(store, running=True)
    cohort = coordinator.state.get('cohort') or []
    pending = len(coordinator.evaluator.pending)
    check('cohort-bound-is-three-waves-for-large-pools',
          search.cohort_bound(24) == 9 and search.cohort_bound(8) == 1, search.cohort_bound(24))
    check('more-than-eight-jobs-active-at-24-workers',
          pending == min(24, sum(m['planned'] for m in cohort)) and pending > 8,
          'pending=%d cohort=%d' % (pending, len(cohort)))
    check('cohort-freezes-multiple-distinct-children', len(cohort) >= 2, len(cohort))
    cids = [member['candidateId'] for member in cohort]
    check('no-duplicated-candidate', len(set(cids)) == len(cids), cids)
    candidate_sets = []
    paired_ok = True
    for member in cohort:
        candidate, reference = member_pairs(member)
        paired_ok = paired_ok and candidate == reference and len(candidate) == member['plannedPairs']
        candidate_sets.append(candidate)
    check('pair-integrity-both-arms-share-ordered-pairs', paired_ok)
    disjoint = all(not (candidate_sets[i] & candidate_sets[j])
                   for i in range(len(candidate_sets)) for j in range(i + 1, len(candidate_sets)))
    check('cohort-members-share-no-ordered-pair', disjoint)
    intent = ledger.experiment_intent(store.db, cohort[0]['experimentId'])
    check('selection-batch-recorded-in-intent',
          (intent.get('selectionBatch') or {}).get('policy') == 'bounded-cohort',
          intent.get('selectionBatch'))
    progress = coordinator.report()['progress']
    check('report-shows-cohort-work-as-current',
          progress.get('current') is not None and progress.get('cohortPending') == len(cohort)
          and coordinator.busy is True, dict(cohortPending=progress.get('cohortPending')))
    owners = {row[0] for row in store.db.execute('SELECT DISTINCT owner FROM ea_experiment')}
    budget_owners = {row[0] for row in store.db.execute('SELECT DISTINCT owner FROM ea_session_budget')}
    check('exactly-one-community-stream', owners <= {'community'} and budget_owners == {'*'},
          'experiment=%r budget=%r' % (owners, budget_owners))
    check('cohort-within-bound', len(cohort) <= search.cohort_bound(24))
    coordinator.close()
    store.close()


def section_small_budget():
    path = new_dir('small') / 'lib.sqlite'
    encounter, supplied, scenario = prep(path)
    baseline = mechanics.normalize_scenario(scenario).get('ownUnits')
    pool = GatedPool(baseline)
    store, coordinator = build_coordinator(path, purposes(10), encounter, supplied, pool, workers=24)
    coordinator.run_pass(store, running=True)
    cohort = coordinator.state.get('cohort') or []
    row = budget_row(store.db, coordinator.session_id, 'improvement')
    planned = sum(member['planned'] for member in cohort)
    check('small-budget-never-overspent',
          row.get('reserved') == 10 and row.get('total') == 10 and row.get('conserved') is True,
          row)
    check('cohort-planned-window-respects-remaining-budget', planned == 10 and len(cohort) >= 2,
          'planned=%d cohort=%d' % (planned, len(cohort)))
    check('no-battle-reserved-beyond-the-finite-plan',
          len(coordinator.evaluator.pending) <= 10, len(coordinator.evaluator.pending))
    coordinator.close()
    store.close()


def section_restart_and_pause():
    path = new_dir('restart') / 'lib.sqlite'
    encounter, supplied, scenario = prep(path)
    baseline = mechanics.normalize_scenario(scenario).get('ownUnits')
    pool = GatedPool(baseline)
    store, coordinator = build_coordinator(path, purposes(24), encounter, supplied, pool, workers=24)
    coordinator.run_pass(store, running=True)
    cohort_size = len(coordinator.state.get('cohort') or [])
    reserved_before = budget_row(store.db, coordinator.session_id, 'improvement').get('reserved')
    # A paused pass must retain the frozen cohort and reserve nothing new.
    coordinator.run_pass(store, running=False)
    reserved_paused = budget_row(store.db, coordinator.session_id, 'improvement').get('reserved')
    check('paused-pass-retains-cohort-and-charges',
          reserved_paused == reserved_before
          and len(coordinator.state.get('cohort') or []) == cohort_size and cohort_size >= 2,
          'before=%s paused=%s cohort=%d' % (reserved_before, reserved_paused, cohort_size))
    store.close()
    coordinator.close()
    # Restart: the persisted cohort is recovered, never re-planned or double-charged.
    pool2 = GatedPool(baseline)
    store2, coordinator2 = build_coordinator(path, purposes(24), encounter, supplied, pool2, workers=24)
    for _ in range(60):
        coordinator2.run_pass(store2, running=True)
        if pool2.pending:
            pool2.release(len(pool2.pending))
        if coordinator2.state.get('idle') and not coordinator2.busy:
            break
    row = budget_row(store2.db, coordinator2.session_id, 'improvement')
    check('restart-no-double-charge',
          row.get('reserved') == 24 and row.get('completed') == 24 and row.get('conserved') is True,
          row)
    done = coordinator2.state.get('completedExperiments') or []
    history = coordinator2.state.get('history') or []
    check('restart-completes-each-member-exactly-once',
          len(done) == cohort_size == len(history) == 3,
          'done=%d history=%d cohort=%d' % (len(done), len(history), cohort_size))
    check('restart-no-duplicate-candidate',
          len({row['candidateId'] for row in history}) == len(history))
    check('restart-cohort-fully-drained', not (coordinator2.state.get('cohort') or []))
    coordinator2.close()
    store2.close()


def section_confirmation_waits():
    path = new_dir('confirm') / 'lib.sqlite'
    encounter, supplied, scenario = prep(path)
    baseline = mechanics.normalize_scenario(scenario).get('ownUnits')
    pool = GatedPool(baseline)
    store, coordinator = build_coordinator(path, purposes(16, comparison=128), encounter, supplied,
                                           pool, workers=16)
    coordinator.run_pass(store, running=True)
    cohort_size = len(coordinator.state.get('cohort') or [])
    check('confirmation-cohort-planned', cohort_size == 2, cohort_size)
    # Drain exactly ONE member; the holdout must NOT freeze while a development member remains.
    pool.release(coordinator.state['cohort'][0]['planned'])
    coordinator.run_pass(store, running=True)
    remaining = coordinator.state.get('cohort') or []
    check('confirmation-waits-for-unfinished-cohort',
          coordinator.state.get('confirmation') is None and len(remaining) == cohort_size - 1,
          'confirmation=%r cohort=%d' % (coordinator.state.get('confirmation'), len(remaining)))
    pool.release(sum(member['planned'] for member in remaining))
    coordinator.run_pass(store, running=True)
    confirmation = coordinator.state.get('confirmation') or {}
    check('confirmation-freezes-only-after-cohort-drained',
          not (coordinator.state.get('cohort') or []) and confirmation.get('frozen') is True,
          'frozen=%r' % (confirmation.get('frozen'),))
    check('holdout-plan-is-frozen-fresh',
          int(confirmation.get('planned') or 0) >= 4 and confirmation.get('nominee') is not None,
          dict(planned=confirmation.get('planned'), nominee=confirmation.get('nominee')))
    coordinator.close()
    store.close()


def section_legacy_single_current():
    coordinator = search.Coordinator(path='unused.sqlite', revision='cohort', workers=24)
    legacy = dict(experimentId='legacy-1', candidateId='c', referenceId='r', planned=8, jobs=[])
    coordinator.state['cohort'] = []
    coordinator.state['current'] = legacy
    check('legacy-single-current-resumes-as-a-cohort', coordinator._cohort() == [legacy])
    coordinator._set_cohort([])
    check('empty-cohort-clears-current',
          coordinator._cohort() == [] and coordinator.state.get('current') is None)


def main():
    section_parallel_fill()
    section_small_budget()
    section_restart_and_pause()
    section_confirmation_waits()
    section_legacy_single_current()
    failed = [name for name, ok in RESULTS if not ok]
    print('\n%d/%d checks passed' % (len(RESULTS) - len(failed), len(RESULTS)))
    if failed:
        print('FAILED: ' + ', '.join(failed))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
