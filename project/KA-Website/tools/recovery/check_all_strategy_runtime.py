"""All-strategy runtime integration checks (mock evaluator only, no real battle).

Run:  <venv>/python -B -X utf8 tools/recovery/check_all_strategy_runtime.py

Drives the real `Coordinator` + `strategy_experiment_store` ledger directly (never the Optimizer
bridge, whose activation gate belongs to another owner) on temporary libraries. Every evaluator is a
deterministic mock, so `KA_SKIP_NATIVE=1` semantics hold: zero actual battles.

The file asserts the all-strategy contract that the shared generator report left to the coordinator:
owner budgets derived from allocations (integers, exact totals, zero owners stay zero), fair
owner rotation with real `strategy_stream_proposals.proposal_pool` dispatch for every active
registered owner, per-owner lineage/scope archiving, Community-only isolation, retired-average
rejection in every mode, session/owner persistence, recovery isolation, and the canonical report.
"""
from __future__ import annotations

import json
import sys
import time
import uuid
from concurrent.futures import Future
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import check_encounter_search as base                       # noqa: E402
import strategy_encounter_search as search                  # noqa: E402
import strategy_experiment_store as ledger                  # noqa: E402
import strategy_search_mode as modes                        # noqa: E402
import strategy_stream_proposals as stream_proposals        # noqa: E402
import strategy_students as students                        # noqa: E402
from strategy_optimizer import Store, baseline_scenarios    # noqa: E402
from strategy_optimizer_adapter import (default_scenario, provenance,   # noqa: E402
                                        stats)

RESULTS = []
TMP = base.TMP


def check(name, condition, detail=''):
    RESULTS.append((name, bool(condition)))
    print(('PASS ' if condition else 'FAIL ') + name + ((' :: ' + str(detail)) if detail else ''))


def new_dir(tag):
    path = TMP / ('ka-allstrat-%s-%s' % (tag, uuid.uuid4().hex[:8]))
    path.mkdir(parents=True, exist_ok=True)
    return path


class ConstantPool:
    """Deterministic positive reward for every sample, so a paired experiment resolves."""

    def __init__(self, reward=50):
        self.reward = reward
        self.calls = 0

    def submit(self, fn, scenario, seeds):
        self.calls += 1
        future = Future()
        future.set_result(dict(seeds=list(seeds), verdict=1, resultBackend='native',
                               rewardOutcome=dict(pendingChests=self.reward),
                               elapsedSeconds=0.01, cpuSeconds=0.01))
        return future

    def shutdown(self, wait=True):
        pass

    def terminate_workers(self):
        pass


def prep(path, encounter=19):
    store = Store(path, provenance())
    seed = default_scenario()
    import strategy_search
    seed['tickLimit'] = strategy_search.DEFAULT_TICK_LIMIT
    scenario = next(sc for sc, _label in baseline_scenarios(seed)
                    if int(sc['encounterId']) == encounter)
    with store.db:
        candidate_id = store.add(scenario, 'baseline', 'supplied', stats(scenario))
    store.close()
    return int(scenario['encounterId']), candidate_id


def all_strategy_config(allocations, purposes):
    """A validated all-strategy config with explicit allocations and a derived owner matrix."""
    full = {name: 0.0 for name in modes.share_streams()}
    for name, value in (allocations or {}).items():
        full[name] = float(value)
    full[students.STUDENT_AVERAGE] = 0.0
    config = {'version': modes.CONFIG_VERSION, 'mode': 'all-strategy',
              'allocations': full, 'purposes': dict(purposes)}
    search.apply_derived_budgets(config)
    modes.validate_config(config)
    return config


def build(path, config, encounter, reference, pool, workers=2, maximum=6):
    search.EVALUATOR_FACTORY = base.mock_factory(pool)
    store = Store(path, provenance())
    store.set(search.MODE_KEY, dict(enabled=True, mode=config['mode'], config=config,
                                    encounter=encounter, reference=reference, previous={}))
    coordinator = search.Coordinator(path=path, revision='allstrat', workers=workers, telemetry=True,
                                     maximum=maximum, reference=reference, encounter=encounter,
                                     timeout=5.0)
    coordinator.load(store)
    return store, coordinator


def run_until(coordinator, store, predicate, passes=12):
    for _ in range(passes):
        coordinator.run_pass(store, running=True)
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def session_rows(db, session_id):
    return {(row['owner'], row['purpose']): row
            for row in ledger.session_status(db, session_id)['budgets']}


def section_budget_matrix():
    allocations = {students.STUDENT_COMMUNITY: 0.5, students.STUDENT_REBEL: 0.3,
                   students.STREAM_DISCOVERY: 0.2}
    purposes = {'improvement': 64, 'boundary': 33, 'support': 16, 'comparison': 64,
                'exploration': 7}
    config = all_strategy_config(allocations, purposes)
    derived = modes.derive_budget_matrix({key: value for key, value in config.items()
                                          if key != 'budgets'})
    matrix = derived['matrix']
    check('derived-matrix-preserves-every-purpose-total',
          all(sum(matrix[owner][purpose] for owner in matrix) == total
              for purpose, total in purposes.items()), derived['totals'])
    check('derived-matrix-is-integers',
          all(isinstance(value, int) and value >= 0 for row in matrix.values()
              for value in row.values()))
    check('derived-matrix-zero-owners-are-zero',
          all(matrix[owner][purpose] == 0 for owner in matrix
              for purpose in modes.PURPOSES
              if config['allocations'].get(owner, 0.0) <= 0)
          and students.STUDENT_STUMBLING not in matrix)
    check('derived-matrix-excludes-retired-and-freewill',
          students.STUDENT_AVERAGE not in matrix and modes.FREE_WILL not in matrix)
    check('derived-matrix-labelles-odd-remainder',
          bool(derived['remainders']) and all(row['units'] == 1 for row in derived['remainders']),
          derived['remainders'])
    check('derived-budget-share-effect',
          matrix[students.STUDENT_COMMUNITY]['improvement']
          > matrix[students.STREAM_DISCOVERY]['improvement'] > 0,
          {o: matrix[o]['improvement'] for o in matrix})
    check('derived-matrix-visible-before-activation',
          config.get('budgets') == matrix)


def section_owner_dispatch():
    for owner in (students.STUDENT_COMMUNITY, students.STREAM_DISCOVERY,
                  students.STUDENT_REBEL, students.STUDENT_STUMBLING,
                  students.STUDENT_MECHANISM):
        path = new_dir('owner-' + owner) / 'lib.sqlite'
        encounter, supplied = prep(path)
        config = all_strategy_config({owner: 1.0},
                                     {'improvement': 8, 'boundary': 0, 'support': 0,
                                      'comparison': 0, 'exploration': 0})
        store, coordinator = build(path, config, encounter, supplied, ConstantPool())
        run_until(coordinator, store, lambda: coordinator.state.get('current') is not None)
        current = coordinator.state.get('current')
        ok = current is not None and current.get('owner') == owner
        exp = (current or {}).get('experimentId')
        intent = ledger.experiment_intent(store.db, exp) if exp is not None else {}
        lane = store.db.execute('SELECT source FROM lineage WHERE candidate=?',
                                (str((current or {}).get('candidateId')),)).fetchone()
        check('all-mode-dispatches-%s' % owner, ok,
              (current or {}).get('blockedReason') or (current or {}).get('owner'))
        check('all-mode-scope-and-lineage-owner-%s' % owner,
              intent.get('owner') == owner and intent.get('scope') == owner
              and lane is not None and lane[0] == owner,
              {'intentOwner': intent.get('owner'), 'scope': intent.get('scope'),
               'lane': lane[0] if lane else None})
        if owner == students.STUDENT_MECHANISM:
            basis = intent.get('strategyBasis') or {}
            check('mechanism-question-and-arm-preserved',
                  bool(basis.get('diagnosticQuestion')) and bool(basis.get('counterfactualArm')),
                  {k: basis.get(k) for k in ('arm', 'counterfactualArm', 'diagnosticQuestion')})
        if owner == students.STUDENT_REBEL:
            basis = intent.get('strategyBasis') or {}
            check('rebel-tier-and-breaks-attributed',
                  bool(basis.get('tier')) and 'brokenRules' in basis,
                  {k: basis.get(k) for k in ('tier', 'brokenRules')})
        coordinator.close()
        store.close()
    search.EVALUATOR_FACTORY = None


def section_evidence_scoping():
    path = new_dir('evidence') / 'lib.sqlite'
    encounter, supplied = prep(path)
    config = all_strategy_config({students.STUDENT_REBEL: 1.0},
                                 {'improvement': 4, 'boundary': 0, 'support': 0,
                                  'comparison': 0, 'exploration': 0})
    store, coordinator = build(path, config, encounter, supplied, ConstantPool())
    coordinator.state['history'] = [
        dict(candidateId='x', owner='rebel', purpose='improvement'),
        dict(candidateId='y', owner='community', purpose='improvement')]
    coordinator.state['features'] = {'x': {'dps_dex': 1}, 'y': {'dps_dex': 2}}
    check('stumble-evidence-is-blind', coordinator._evidence(students.STUDENT_STUMBLING) == {})
    check('discovery-evidence-not-community-vetoed',
          coordinator._evidence(students.STREAM_DISCOVERY) == {})
    rebel_vectors = coordinator._evidence(students.STUDENT_REBEL)['observedStatVectors']
    check('owner-evidence-is-family-scoped', rebel_vectors == [{'dps_dex': 1}], rebel_vectors)
    coordinator.close()
    store.close()
    search.EVALUATOR_FACTORY = None


def section_zero_owner_and_recovery():
    path = new_dir('zero') / 'lib.sqlite'
    encounter, supplied = prep(path)
    purposes = {'improvement': 4, 'boundary': 0, 'support': 0, 'comparison': 0, 'exploration': 0}
    store, coordinator = build(path, all_strategy_config({students.STUDENT_COMMUNITY: 1.0}, purposes),
                               encounter, supplied, ConstantPool())
    db = store.db
    ledger.initialize(db)
    old_config = all_strategy_config({students.STUDENT_REBEL: 1.0}, purposes)
    old_sid = ledger.configure_session(db, old_config)
    experiment = ledger.create_experiment(db, dict(
        scope='rebel', owner='rebel', ownerShare=1.0, purpose='improvement', planned_budget=1,
        stopping='one sample', sessionId=old_sid, policy={}, mechanicsRevision='m',
        encounterRevision='e'))
    check('zero-owner-old-reservation-fixture',
          ledger.reserve(db, experiment, 'rebel-build', [11, 22]))
    ledger.deactivate_session(db, old_sid)
    session_id = ledger.configure_session(db, coordinator.config)
    ledger.activate_exclusive_session(db, session_id)
    rows = session_rows(db, session_id)
    check('zero-owner-holds-no-budget',
          rows.get(('rebel', 'improvement'), {}).get('total', 0) == 0, rows)
    recovered = ledger.recover(db, session_id=session_id)
    check('zero-owner-recovery-blocked',
          any(row['chargedExperimentId'] == experiment for row in recovered['blocked'])
          and not any(row['chargedExperimentId'] == experiment for row in recovered['outstanding']))
    check('zero-owner-no-new-reservation',
          not ledger.reserve(db, experiment, 'rebel-build-2', [33, 44]))
    coordinator.close()
    store.close()
    search.EVALUATOR_FACTORY = None


def section_community_only():
    path = new_dir('community-only') / 'lib.sqlite'
    encounter, supplied = prep(path)
    config = search.default_config('community-first', purposes={
        'improvement': 8, 'boundary': 0, 'support': 0, 'comparison': 0, 'exploration': 0})
    store, coordinator = build(path, config, encounter, supplied, ConstantPool())
    calls = []
    original_generator = stream_proposals.stream_generator
    original_pool = stream_proposals.proposal_pool

    def spy_generator(owner):
        calls.append(owner)
        return original_generator(owner)

    def spy_pool(*args, **kwargs):
        calls.append(kwargs.get('owner', args[0] if args else None))
        return original_pool(*args, **kwargs)

    stream_proposals.stream_generator = spy_generator
    stream_proposals.proposal_pool = spy_pool
    try:
        run_until(coordinator, store, lambda: coordinator.state.get('current') is not None)
    finally:
        stream_proposals.stream_generator = original_generator
        stream_proposals.proposal_pool = original_pool
    check('community-first-makes-no-foreign-generator-calls', calls == [], calls)
    owners = {row[0] for row in store.db.execute(
        'SELECT DISTINCT owner FROM ea_experiment')} if coordinator.state.get('current') else set()
    check('community-first-experiments-are-community-only', owners <= {'community'}, owners)
    coordinator.close()
    store.close()
    search.EVALUATOR_FACTORY = None


def section_retired_average():
    for mode in modes.MODES:
        allocations = {name: 0.0 for name in modes.share_streams()}
        allocations[students.STUDENT_COMMUNITY] = 0.5
        allocations[students.STUDENT_AVERAGE] = 0.5
        try:
            modes.validate_config({'version': 1, 'mode': mode, 'allocations': allocations,
                                   'purposes': {'improvement': 8}})
        except ValueError:
            check('retired-average-refused-%s' % mode, True)
        else:
            check('retired-average-refused-%s' % mode, False, 'accepted average allocation')
    check('retired-average-zero-in-default',
          search.default_config('all-strategy')['allocations'].get(students.STUDENT_AVERAGE) == 0.0)
    path = new_dir('retired') / 'lib.sqlite'
    encounter, supplied = prep(path)
    store = Store(path, provenance())
    parent = store.scenario(supplied)
    diagnostics = {}
    rows = stream_proposals.proposal_pool(students.STUDENT_AVERAGE, parent, allocation=0.5,
                                          diagnostics=diagnostics)
    check('retired-average-generator-refuses',
          rows == [] and diagnostics.get('code') == 'retired-owner', diagnostics)
    store.close()


def section_persist_restart():
    path = new_dir('restart') / 'lib.sqlite'
    encounter, supplied = prep(path)
    config = all_strategy_config({students.STUDENT_REBEL: 1.0},
                                 {'improvement': 8, 'boundary': 0, 'support': 0,
                                  'comparison': 0, 'exploration': 0})
    store, coordinator = build(path, config, encounter, supplied, ConstantPool())
    run_until(coordinator, store, lambda: coordinator.state.get('current') is not None)
    before = coordinator.state.get('current')
    coordinator.close()
    restarted = search.Coordinator(path=path, revision='allstrat', workers=2, telemetry=True,
                                   maximum=6, reference=supplied, encounter=encounter, timeout=5.0)
    restarted.load(store)
    current = restarted.state.get('current') or {}
    check('restart-preserves-owner',
          current.get('owner') == students.STUDENT_REBEL
          and current.get('experimentId') == (before or {}).get('experimentId'),
          {'before': (before or {}).get('owner'), 'after': current.get('owner')})
    check('restart-preserves-session-allocations',
          restarted.config['allocations'].get(students.STUDENT_REBEL) == 1.0
          and restarted.config.get('budgets'))
    restarted.close()
    store.close()
    search.EVALUATOR_FACTORY = None


def section_canonical_report():
    path = new_dir('report') / 'lib.sqlite'
    encounter, supplied = prep(path)
    config = all_strategy_config({students.STUDENT_COMMUNITY: 1.0},
                                 {'improvement': 8, 'boundary': 0, 'support': 0,
                                  'comparison': 0, 'exploration': 0})
    store, coordinator = build(path, config, encounter, supplied, ConstantPool())
    run_until(coordinator, store,
              lambda: bool(coordinator.state.get('portfolio')) or bool(coordinator.state.get('idle')))
    report = coordinator.report()
    check('report-advertises-supported-modes',
          set(report.get('supportedModes') or []) == set(modes.MODES)
          and 'all-strategy' in (report.get('supportedModes') or []),
          report.get('supportedModes'))
    check('report-exposes-derived-owner-matrix',
          bool((report.get('budgets') or {}).get('matrix'))
          and (report.get('budgets') or {}).get('source') in ('derived', 'explicit'),
          (report.get('budgets') or {}).get('source'))
    check('report-exposes-eligible-owners',
          report.get('eligibleOwners') == [students.STUDENT_COMMUNITY], report.get('eligibleOwners'))
    rows = report.get('portfolio') or []
    row = rows[0] if rows else {}
    check('portfolio-row-carries-truthful-domain-labels',
          bool(row) and row.get('owner') == students.STUDENT_COMMUNITY
          and isinstance(row.get('synthetic'), bool) and isinstance(row.get('playerUnknown'), bool)
          and 'unknownCount' in row and row.get('domainContext') in ('synthetic', 'player',
                                                                     'unrestricted', None),
          {k: row.get(k) for k in ('owner', 'synthetic', 'playerUnknown', 'domainContext',
                                   'unknownCount')})
    coordinator.close()
    store.close()
    search.EVALUATOR_FACTORY = None


def main():
    section_budget_matrix()
    section_owner_dispatch()
    section_evidence_scoping()
    section_zero_owner_and_recovery()
    section_community_only()
    section_retired_average()
    section_persist_restart()
    section_canonical_report()
    search.EVALUATOR_FACTORY = None
    passed = sum(1 for _name, ok in RESULTS if ok)
    print('%d/%d all-strategy runtime checks passed' % (passed, len(RESULTS)))
    return 0 if passed == len(RESULTS) else 1


if __name__ == '__main__':
    sys.exit(main())
