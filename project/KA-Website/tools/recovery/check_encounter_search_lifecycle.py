"""Lifecycle/acceptance checks for the Community-first encounter runtime (mock evaluator only).

Run:  <venv>/python -B -X utf8 tools/recovery/check_encounter_search_lifecycle.py

This file adds the deeper lifecycle coverage that `check_encounter_search.py` does not assert:
event/evidence-driven `run_pass` caching, a full >=64-pair confirmation through to publication,
partial holdouts, restart durability without double charge or seed reuse, a real support question,
and fail-closed policy/revision/recovery handling. Every evaluator is a deterministic mock; no real
battle is launched.
"""
from __future__ import annotations

import copy
import json
import sys
import time
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
    path = base.TMP / ('ka-life-%s-%s' % (tag, uuid.uuid4().hex[:8]))
    path.mkdir(parents=True, exist_ok=True)
    return path


def prep(path, encounter=19):
    """One supplied Community baseline. Returns (encounterId, candidateId, rawScenario)."""
    store = Store(path, provenance())
    seed = default_scenario()
    seed['tickLimit'] = strategy_search.DEFAULT_TICK_LIMIT
    scenario = next(sc for sc, _label in baseline_scenarios(seed)
                    if int(sc['encounterId']) == encounter)
    with store.db:
        candidate_id = store.add(scenario, 'baseline', 'supplied', stats(scenario))
    store.close()
    return int(scenario['encounterId']), candidate_id, scenario


def build_coordinator(path, config, encounter, reference, pool, workers=2, maximum=6):
    search.EVALUATOR_FACTORY = base.mock_factory(pool)
    store = Store(path, provenance())
    store.set(search.MODE_KEY, dict(enabled=True, mode='community-first', config=config,
                                    encounter=encounter, reference=reference, previous={}))
    coordinator = search.Coordinator(path=path, revision='lifecycle', workers=workers, telemetry=True,
                                     maximum=maximum, reference=reference, encounter=encounter,
                                     timeout=5.0)
    coordinator.load(store)
    return store, coordinator


def budget_row(db, session_id, purpose):
    for row in ledger.session_status(db, session_id)['budgets']:
        if row['purpose'] == purpose:
            return row
    return {}


class PositivePool:
    """Deterministic law: any build that differs from the supplied baseline earns 100, else 0.

    The reward depends only on the scenario, so every matched pair carries the same difference and a
    full confirmation is decided without randomness.
    """

    def __init__(self, baseline_own_units):
        self.baseline = baseline_own_units
        self.calls = 0

    def submit(self, fn, scenario, seeds):
        self.calls += 1
        future = Future()
        units = mechanics.normalize_scenario(scenario).get('ownUnits')
        reward = 0 if units == self.baseline else 100
        future.set_result(dict(seeds=list(seeds), verdict=1, resultBackend='native',
                               rewardOutcome=dict(pendingChests=reward),
                               elapsedSeconds=0.01, cpuSeconds=0.01))
        return future

    def shutdown(self, wait=True):
        pass

    def terminate_workers(self):
        pass


class SupportPool:
    """Constant positive reward, so a paired support assessment resolves rather than being censored."""

    def __init__(self):
        self.calls = 0

    def submit(self, fn, scenario, seeds):
        self.calls += 1
        future = Future()
        future.set_result(dict(seeds=list(seeds), verdict=1, resultBackend='native',
                               rewardOutcome=dict(pendingChests=50),
                               elapsedSeconds=0.01, cpuSeconds=0.01))
        return future

    def shutdown(self, wait=True):
        pass

    def terminate_workers(self):
        pass


def section_idle_caching():
    path = new_dir('idle') / 'lib.sqlite'
    encounter, supplied, _scenario = prep(path)
    # At least one purpose budget must stay positive; `comparison: 2` cannot buy a pair (<4) and
    # cannot nominate without evidence, so the session settles straight into a genuinely idle state.
    config = search.default_config('community-first', purposes={
        'improvement': 0, 'boundary': 0, 'support': 0, 'comparison': 2, 'exploration': 0})
    store, coordinator = build_coordinator(path, config, encounter, supplied, base.LearnablePool())
    coordinator.run_pass(store, running=True)
    check('idle-pass-settles', coordinator.state.get('idle') is True)

    counts = {'outcomes': 0, 'digest': 0, 'init': 0, 'set': 0}
    real_outcomes, real_digest = ledger.outcomes, mechanics.dependency_digest
    real_init, real_set = ledger.initialize, store.set

    def wrap_outcomes(*args, **kwargs):
        counts['outcomes'] += 1
        return real_outcomes(*args, **kwargs)

    def wrap_digest(*args, **kwargs):
        counts['digest'] += 1
        return real_digest(*args, **kwargs)

    def wrap_init(*args, **kwargs):
        counts['init'] += 1
        return real_init(*args, **kwargs)

    def wrap_set(key, value, *args, **kwargs):
        if key == search.RUNTIME_KEY:
            counts['set'] += 1
        return real_set(key, value, *args, **kwargs)

    ledger.outcomes, mechanics.dependency_digest = wrap_outcomes, wrap_digest
    ledger.initialize, store.set = wrap_init, wrap_set
    try:
        for _ in range(3):
            coordinator.run_pass(store, running=True)
    finally:
        ledger.outcomes, mechanics.dependency_digest = real_outcomes, real_digest
        ledger.initialize, store.set = real_init, real_set
    check('idle-passes-skip-outcome-rescan', counts['outcomes'] == 0, counts)
    check('idle-passes-skip-digest-reread', counts['digest'] == 0, counts)
    check('idle-passes-skip-schema-reinit', counts['init'] == 0, counts)
    check('idle-passes-skip-fullstate-rewrite', counts['set'] == 0, counts)

    # A config change re-enables planning, re-scans evidence and rewrites the state.
    resized = coordinator.set_budget(store, {'purposes': {'improvement': 8}})
    check('budget-change-reactivates-idle', resized.get('ok') is True
          and coordinator.state.get('idle') is False, resized.get('error'))
    counts2 = {'outcomes': 0, 'set': 0}

    def wrap_outcomes2(*args, **kwargs):
        counts2['outcomes'] += 1
        return real_outcomes(*args, **kwargs)

    def wrap_set2(key, value, *args, **kwargs):
        if key == search.RUNTIME_KEY:
            counts2['set'] += 1
        return real_set(key, value, *args, **kwargs)

    ledger.outcomes, store.set = wrap_outcomes2, wrap_set2
    try:
        coordinator.run_pass(store, running=True)
    finally:
        ledger.outcomes, store.set = real_outcomes, real_set
    check('new-budget-plans-an-experiment', coordinator.state.get('current') is not None)
    # A purpose-budget change does not modify the development evidence. Reuse the valid evidence
    # snapshot instead of rescanning every completed experiment on this planning-only pass.
    check('new-budget-reuses-unchanged-outcome-cache', counts2['outcomes'] == 0, counts2)
    check('new-budget-persists-state', counts2['set'] >= 1, counts2)

    # A database-object replacement invalidates the per-db caches.
    coordinator.close()
    store.close()
    store2 = Store(path, provenance())
    counts3 = {'outcomes': 0, 'init': 0}

    def wrap_outcomes3(*args, **kwargs):
        counts3['outcomes'] += 1
        return real_outcomes(*args, **kwargs)

    def wrap_init3(*args, **kwargs):
        counts3['init'] += 1
        return real_init(*args, **kwargs)

    ledger.outcomes, ledger.initialize = wrap_outcomes3, wrap_init3
    try:
        # The first pass notices the replacement and rebuilds its per-DB caches, but all evaluator
        # slots are still occupied. It must leave those battles untouched and defer search work.
        coordinator.run_pass(store2, running=True)
        # The ready futures are persisted and harvested at the next safe boundary. Only then does
        # the coordinator need to read evidence from the replacement connection.
        coordinator.run_pass(store2, running=True)
    finally:
        ledger.outcomes, ledger.initialize = real_outcomes, real_init
    check('db-replacement-reinitializes-schema', counts3['init'] >= 1, counts3)
    check('db-replacement-rescans-outcomes-at-next-safe-harvest', counts3['outcomes'] >= 1,
          counts3)

    report = coordinator.report()
    public_current = (report.get('progress') or {}).get('current') or {}
    durable_jobs = (coordinator.state.get('current') or {}).get('jobs') or []
    persisted = store2.get(search.RUNTIME_KEY) or {}
    persisted_jobs = ((persisted.get('current') or {}).get('jobs')
                      if isinstance(persisted.get('current'), dict) else None) or []
    check('report-omits-current-jobs', 'jobs' not in public_current
          and public_current.get('planJobs') == len(durable_jobs), public_current.get('planJobs'))
    check('report-omits-observed-scenarios',
          'observedCandidate' not in public_current and 'observedReference' not in public_current)
    check('durable-state-retains-jobs', len(persisted_jobs) == len(durable_jobs) > 0,
          'durable=%d live=%d' % (len(persisted_jobs), len(durable_jobs)))
    coordinator.close()
    store2.close()


def section_mechanics_fail_closed():
    path = new_dir('mech') / 'lib.sqlite'
    encounter, supplied, _scenario = prep(path)
    config = search.default_config('community-first', purposes={
        'improvement': 8, 'boundary': 0, 'support': 0, 'comparison': 0, 'exploration': 0})
    store, coordinator = build_coordinator(path, config, encounter, supplied, base.LearnablePool())
    revision = {'value': 'mech-1'}
    coordinator._mechanics_revision = lambda: revision['value']
    coordinator.run_pass(store, running=True)
    current = coordinator.state.get('current') or {}
    reserved_before = len(ledger.development_reservations(store.db, current.get('experimentId')))
    check('mechanics-frozen-into-plan',
          current.get('mechanicsRevision') == 'mech-1' and reserved_before > 0,
          'frozen=%s reserved=%d' % (current.get('mechanicsRevision'), reserved_before))
    revision['value'] = 'mech-2'
    coordinator.run_pass(store, running=True)
    current = coordinator.state.get('current') or {}
    reserved_after = len(ledger.development_reservations(store.db, current.get('experimentId')))
    check('changed-mechanics-blocks-dispatch',
          current.get('blocked') is True and 'mechanics' in (current.get('blockedReason') or ''),
          current.get('blockedReason'))
    check('changed-mechanics-spends-nothing-new', reserved_after == reserved_before,
          '%d -> %d' % (reserved_before, reserved_after))
    coordinator.close()
    store.close()


def section_confirmation_64():
    path = new_dir('confirm') / 'lib.sqlite'
    encounter, supplied, baseline = prep(path)
    baseline_units = mechanics.normalize_scenario(baseline).get('ownUnits')
    pool = PositivePool(baseline_units)
    config = search.default_config('community-first', purposes={
        'improvement': 8, 'comparison': 128, 'boundary': 0, 'support': 0, 'exploration': 0})
    store, coordinator = build_coordinator(path, config, encounter, supplied, pool)
    deadline = time.time() + 90
    while time.time() < deadline:
        if (coordinator.state.get('confirmation') or {}).get('frozen'):
            break
        coordinator.run_pass(store, running=True)
        time.sleep(0.01)
    confirmation = coordinator.state.get('confirmation') or {}
    check('confirmation-frozen-with-64-pairs',
          confirmation.get('frozen') is True and len(confirmation.get('pairs') or []) == 64,
          'pairs=%d' % len(confirmation.get('pairs') or []))
    check('confirmation-plans-128-runs', confirmation.get('planned') == 128,
          confirmation.get('planned'))

    for _ in range(6):
        if coordinator.state.get('publication'):
            break
        coordinator.run_pass(store, running=True)
        time.sleep(0.01)
    confirmation = coordinator.state.get('confirmation') or {}
    check('partial-holdout-does-not-publish',
          coordinator.state.get('publication') is None and not confirmation.get('ready')
          and 0 < confirmation.get('completed', 0) < confirmation.get('planned', 0),
          'completed=%s ready=%s' % (confirmation.get('completed'), confirmation.get('ready')))

    pairs_before = sorted({(int(a), int(b)) for a, b in store.db.execute(
        'SELECT seed_a, seed_b FROM ea_holdout')})
    rows_before = store.db.execute('SELECT COUNT(*) FROM ea_holdout').fetchone()[0]
    reserved_before = budget_row(store.db, coordinator.session_id, 'comparison').get('reserved')

    coordinator.close()
    store.close()
    store2 = Store(path, provenance())
    coordinator2 = search.Coordinator(path=path, revision='lifecycle', workers=2, telemetry=True,
                                      maximum=6, reference=supplied, encounter=encounter, timeout=5.0)
    coordinator2.load(store2)
    deadline = time.time() + 180
    while time.time() < deadline and not coordinator2.state.get('publication'):
        coordinator2.run_pass(store2, running=True)
        time.sleep(0.005)

    pairs_after = sorted({(int(a), int(b)) for a, b in store2.db.execute(
        'SELECT seed_a, seed_b FROM ea_holdout')})
    rows_after = store2.db.execute('SELECT COUNT(*) FROM ea_holdout').fetchone()[0]
    distinct = store2.db.execute(
        'SELECT COUNT(*) FROM (SELECT DISTINCT candidate_id, seed_a, seed_b FROM ea_holdout)'
    ).fetchone()[0]
    publication = coordinator2.state.get('publication') or {}
    comparison = budget_row(store2.db, coordinator2.session_id, 'comparison')
    check('restart-reuses-frozen-holdout-seeds',
          pairs_after == pairs_before and rows_after == rows_before == 128,
          'pairs %d/%d rows %d' % (len(pairs_before), len(pairs_after), rows_after))
    check('no-duplicate-holdout-rows',
          distinct == rows_after == 128, 'distinct=%d rows=%d' % (distinct, rows_after))
    check('full-positive-confirmation-published',
          publication.get('status') == 'published'
          and (publication.get('comparison') or {}).get('confirmed') is True,
          json.dumps(publication)[:220])
    check('confirmation-not-double-charged',
          comparison.get('reserved') == 128 and comparison.get('completed') == 128
          and reserved_before == 128, comparison)
    coordinator2.close()
    store2.close()


def section_compat_and_recovery():
    path = new_dir('compat') / 'lib.sqlite'
    encounter, supplied, _scenario = prep(path)
    # A positive improvement budget lets the hand-written fixture experiments reserve samples; no pass
    # is run in this section, so it cannot start a real search.
    config = search.default_config('community-first', purposes={
        'improvement': 64, 'boundary': 0, 'support': 0, 'comparison': 0, 'exploration': 0})
    store, coordinator = build_coordinator(path, config, encounter, supplied, base.LearnablePool())
    coordinator._ensure_schema(store.db)
    coordinator._ensure_session(store.db)
    coordinator._reference_candidate(store)
    db = store.db
    live_compat = coordinator._compatibility()
    live_mechanics = coordinator._mechanics_revision()
    live_policy = coordinator.state['policy']
    observed = store.observed_scenario(store.scenario(supplied), candidate=supplied)

    def intent(extra):
        fields = dict(scope='community', owner='community', ownerShare=1.0, purpose='improvement',
                      sessionId=coordinator.session_id, planned_budget=1, stopping='fixture',
                      policy=live_policy, mechanicsRevision=live_mechanics,
                      encounterRevision='enc-rev', engineRevision=coordinator.revision,
                      compatibility=live_compat, measurementWindow='development')
        fields.update(extra)
        return fields

    good = ledger.create_experiment(db, intent({'observedScenarios': {'good-cand': observed}}))
    stale_policy = ledger.create_experiment(db, intent({
        'policy': {'finishPolicy': 'on-verdict', 'tickLimit': 123456},
        'stopping': 'stale-policy-fixture',
        'observedScenarios': {'stale-policy-cand': observed}}))
    stale_mechanics = ledger.create_experiment(db, intent({
        'mechanicsRevision': 'other-mechanics', 'stopping': 'stale-mechanics-fixture',
        'observedScenarios': {'stale-mechanics-cand': observed}}))
    for experiment, candidate, pair in ((good, 'good-cand', [31, 41]),
                                        (stale_policy, 'stale-policy-cand', [32, 42]),
                                        (stale_mechanics, 'stale-mechanics-cand', [33, 43])):
        ledger.reserve(db, experiment, candidate, pair)
        ledger.complete(db, experiment, candidate, pair,
                        {'seeds': pair, 'verdict': 1, 'resultBackend': 'native',
                         'rewardOutcome': {'pendingChests': 77}})
    compatible = coordinator._compatible_experiment_ids(db)
    check('live-intent-admitted', good in compatible, sorted(compatible))
    check('digest-matching-stale-policy-excluded', stale_policy not in compatible)
    check('digest-matching-stale-mechanics-excluded', stale_mechanics not in compatible)
    deduped = coordinator._deduped_outcomes(db, experiments=compatible)
    check('stale-policy-row-not-in-learner-view', 'stale-policy-cand' not in deduped)
    check('stale-mechanics-row-not-in-learner-view', 'stale-mechanics-cand' not in deduped)
    check('live-row-retained', 'good-cand' in deduped)

    # Recovery: a legacy intent with no frozen scenario must not be recomputed; a policy mismatch
    # must not be recovered; a fully frozen matching intent must be recovered.
    legacy = ledger.create_experiment(db, intent({'stopping': 'legacy-fixture',
                                                  'observedScenarios': None}))
    mismatch = ledger.create_experiment(db, intent({
        'policy': {'finishPolicy': 'on-verdict', 'tickLimit': 222222},
        'stopping': 'mismatch-fixture', 'observedScenarios': {'b-mismatch': observed}}))
    recoverable = ledger.create_experiment(db, intent({
        'stopping': 'recoverable-fixture', 'observedScenarios': {'c-recoverable': observed}}))
    ledger.reserve(db, legacy, 'a-legacy', [51, 61])
    ledger.reserve(db, mismatch, 'b-mismatch', [52, 62])
    ledger.reserve(db, recoverable, 'c-recoverable', [53, 63])
    # Recovery dispatch models rows left durable by an interrupted prior process. Keep the
    # fixture's reservation writes outside an open caller transaction before starting workers.
    db.commit()

    seen = {'observed': 0}
    real_observed = store.observed_scenario

    def spy_observed(*args, **kwargs):
        seen['observed'] += 1
        return real_observed(*args, **kwargs)

    store.observed_scenario = spy_observed
    coordinator._make_evaluator()
    try:
        coordinator._recover(db, store)
    finally:
        store.observed_scenario = real_observed
    submitted = {str(key[1]) for key in coordinator.evaluator.pending}
    check('unfrozen-scenario-not-recomputed',
          'a-legacy' not in submitted and seen['observed'] == 0,
          'submitted=%s observed_calls=%d' % (submitted, seen['observed']))
    check('policy-mismatch-not-recovered', 'b-mismatch' not in submitted, submitted)
    check('frozen-matching-scenario-recovered', 'c-recoverable' in submitted, submitted)
    check('unfrozen-scenario-noted',
          any('no frozen scenario' in note for note in coordinator.limitations),
          coordinator.limitations[-3:])
    check('policy-mismatch-noted',
          any('scope/revision/policy' in note for note in coordinator.limitations),
          coordinator.limitations[-3:])
    coordinator.close()
    store.close()


def section_support_question():
    path = new_dir('support') / 'lib.sqlite'
    encounter, supplied, _scenario = prep(path)
    pool = SupportPool()
    config = search.default_config('community-first', purposes={
        'improvement': 0, 'boundary': 0, 'support': 32, 'comparison': 0, 'exploration': 0})
    store, coordinator = build_coordinator(path, config, encounter, supplied, pool)
    frozen = coordinator.set_question(store, dict(kind='support', role='dps', stat='mp', value=0,
                                                  budget=32, minimumPairs=2))
    check('support-question-frozen', frozen.get('ok') is True, frozen.get('error'))

    deadline = time.time() + 60
    while time.time() < deadline:
        coordinator.run_pass(store, running=True)
        if coordinator.state.get('boundaries'):
            break
        if (not coordinator.busy and coordinator.state.get('current') is None
                and not coordinator._confirmation_pending()
                and coordinator._next_purpose(store.db) is None):
            break
        time.sleep(0.01)

    boundaries = coordinator.state.get('boundaries') or []
    entry = boundaries[-1] if boundaries else {}
    assessment = entry.get('assessment') or {}
    check('support-question-produces-assessment',
          bool(entry) and assessment.get('tested') is True
          and assessment.get('pairedCount', 0) >= 2,
          json.dumps(assessment)[:220] if assessment else 'no assessment')
    check('support-assessment-kind-is-support', entry.get('kind') == 'support')
    support_row = budget_row(store.db, coordinator.session_id, 'support')
    improvement_row = budget_row(store.db, coordinator.session_id, 'improvement')
    boundary_row = budget_row(store.db, coordinator.session_id, 'boundary')
    check('support-budget-only-spent',
          support_row.get('reserved', 0) >= 4 and support_row.get('completed', 0) >= 4,
          support_row)
    check('other-budgets-untouched',
          improvement_row.get('reserved', 0) == 0 and boundary_row.get('reserved', 0) == 0,
          {'improvement': improvement_row, 'boundary': boundary_row})
    persisted = [row[0] for row in store.db.execute('SELECT kind FROM ea_boundary')]
    check('support-boundary-persisted', 'support' in persisted, persisted)
    coordinator.close()
    store.close()


def _challenger_fixture(tag, *, round_index, improvement=32):
    path = new_dir(tag) / 'lib.sqlite'
    encounter, reference, baseline = prep(path)
    store = Store(path, provenance())
    child = copy.deepcopy(baseline)
    param = child['ownUnits'][0]['parameters'][13]
    param['rawValue'] = int(param['rawValue']) + 11
    with store.db:
        challenger = store.add(child, 'challenger', 'mutation', stats(child))
    store.close()
    config = search.default_config('community-first', purposes={
        'improvement': improvement, 'comparison': 8, 'boundary': 0, 'support': 0, 'exploration': 0})
    store, coordinator = build_coordinator(path, config, encounter, reference, SupportPool(),
                                           workers=1, maximum=6)
    coordinator._ensure_schema(store.db)
    coordinator._ensure_session(store.db)
    coordinator.state['roundIndex'] = round_index
    compatibility = coordinator._compatibility()
    coordinator.state['history'] = [dict(
        candidateId=challenger, referenceId=str(reference), owner='community',
        purpose='improvement', compatibility=compatibility, pairs=8, unknownCount=0,
        pairedMeanDifference=2.0, pairedStandardError=3.0)]
    coordinator.state['portfolio'] = [dict(candidateId=challenger, owner='community',
                                           meanEarned=2.0, eligible=True, arm='candidate')]
    return store, coordinator, challenger, reference


def section_support_requires_question():
    # A support budget with NO frozen support question must stay unspent with a reason; it may not
    # buy questionless landmark filler.
    path = new_dir('support-nq') / 'lib.sqlite'
    encounter, supplied, _scenario = prep(path)
    config = search.default_config('community-first', purposes={
        'improvement': 0, 'boundary': 0, 'support': 32, 'comparison': 0, 'exploration': 0})
    store, coordinator = build_coordinator(path, config, encounter, supplied, SupportPool())
    try:
        for _ in range(3):
            coordinator.run_pass(store, running=True)
        support_row = budget_row(store.db, coordinator.session_id, 'support')
        check('support-without-question-stays-unspent',
              support_row.get('reserved', 0) == 0
              and any('no frozen support question' in note for note in coordinator.limitations),
              {'support': support_row, 'limitations': coordinator.limitations[-2:]})
    finally:
        coordinator.close()
        store.close()


def section_extension_ladder():
    # Round 3: the interleave is due, so an UNCERTAIN challenger buys ONE declared stage even though
    # a fresh distinct child is available. The frozen plan must dispatch the WHOLE stage (8 -> 16
    # TOTAL pairs = 16 runs), never a truncated 4-pair sample, and must not borrow holdout budget.
    store, coordinator, challenger, reference = _challenger_fixture('ext-buy', round_index=3)
    try:
        coordinator._plan_round(store.db, store)
        current = coordinator.state.get('current') or {}
        comparison_row = budget_row(store.db, coordinator.session_id, 'comparison')
        holdout = store.db.execute('SELECT COUNT(*) FROM ea_holdout').fetchone()[0]
        experiment = coordinator._experiment_state(store.db, current['experimentId'])
        check('extension-stage-beyond-8-bought-through-the-real-scheduler',
              str(current.get('proposalId') or '').startswith('knowledge-extension')
              and current.get('plannedPairs') == 8 and current.get('planned') == 16
              and current.get('candidateId') == challenger,
              {k: current.get(k) for k in ('proposalId', 'plannedPairs', 'planned', 'candidateId')})
        check('extension-plan-is-charged-to-improvement-and-not-holdout',
              current.get('purpose') == 'improvement' and experiment.get('planned') == 16
              and comparison_row.get('reserved') == 0 and holdout == 0,
              {'purpose': current.get('purpose'), 'experiment': experiment,
               'comparison': comparison_row, 'holdout': holdout})
    finally:
        coordinator.close()
        store.close()


def section_mode_rollback_chain():
    path = new_dir('mode-rollback-chain') / 'lib.sqlite'
    store = Store(path, provenance())
    config = search.default_config('community-first')
    coordinator = search.Coordinator(path=str(path), revision='lifecycle', config=config,
                                     workers=1, telemetry=False, maximum=1)
    try:
        previous = dict(enabled=False, mode='community-first', config=config, previous={'old': True})
        for _ in range(8):
            coordinator.activate(store, dict(config=config, previousMapping=previous))
            raw = store.db.execute('SELECT value FROM meta WHERE key=?',
                                   (search.MODE_KEY,)).fetchone()[0]
            mapping = json.loads(raw)
            snapshot = mapping['previous']['mapping']
            check('activation-stores-one-rollback-snapshot', 'previous' not in snapshot,
                  list(snapshot)[:5])
            previous = mapping

        nested = '{}'
        for _ in range(1_100):
            nested = '{"previous":{"mapping":' + nested + '}}'
        deep_raw = ('{"enabled":true,"mode":"community-first","config":'
                    + json.dumps(config, separators=(',', ':'))
                    + ',"previous":{"mapping":' + nested + '}}')
        store.db.execute('INSERT OR REPLACE INTO meta(key,value) VALUES (?,?)',
                         (search.MODE_KEY, deep_raw))
        shallow = search._read_json(store.db, search.MODE_KEY)
        check('read-only-mode-report-projection-survives-legacy-depth',
              isinstance(shallow, dict) and shallow.get('enabled') is True,
              isinstance(shallow, dict))
        coordinator.rollback(store)
        rolled_back = json.loads(store.db.execute('SELECT value FROM meta WHERE key=?',
                                                   (search.MODE_KEY,)).fetchone()[0])
        check('rollback-reads-through-store-shallow-mode-get',
              rolled_back.get('enabled') is False,
              {'enabled': rolled_back.get('enabled'),
               'previousKeys': list((rolled_back.get('previous') or {}).keys())})
    finally:
        coordinator.close()
        store.close()

    # Round 1: the interleave is NOT due, so a fresh distinct child is chosen and the uncertain
    # challenger is left alone - diverse coverage is preserved.
    store, coordinator, challenger, reference = _challenger_fixture('ext-cover', round_index=1)
    try:
        coordinator._plan_round(store.db, store)
        current = coordinator.state.get('current') or {}
        check('extension-does-not-preempt-coverage-before-interleave',
              current.get('proposalId') is not None
              and not str(current.get('proposalId')).startswith('knowledge-extension')
              and current.get('candidateId') not in (None, challenger),
              {k: current.get(k) for k in ('proposalId', 'plannedPairs', 'candidateId')})
    finally:
        coordinator.close()
        store.close()


def main():
    section_mode_rollback_chain()
    section_idle_caching()
    section_mechanics_fail_closed()
    section_confirmation_64()
    section_compat_and_recovery()
    section_support_question()
    section_support_requires_question()
    section_extension_ladder()
    search.EVALUATOR_FACTORY = None
    passed = sum(1 for _name, ok in RESULTS if ok)
    print('%d/%d lifecycle checks passed' % (passed, len(RESULTS)))
    return 0 if passed == len(RESULTS) else 1


if __name__ == '__main__':
    sys.exit(main())
