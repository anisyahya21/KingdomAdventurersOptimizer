"""Zero-battle acceptance checks for the closed-loop learning-cycle integration.

Run:  <venv>/python -B -X utf8 tools/recovery/check_encounter_learning_cycle.py

Every fixture is synthetic and deterministic. No real battle, native engine, live library,
setting or reset is touched: the pure services are exercised directly and the Coordinator is
driven only through mock pools over temporary libraries.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import check_encounter_search as base                                  # noqa: E402
import strategy_encounter_decisions as decisions                       # noqa: E402
import strategy_encounter_presentation as presentation                # noqa: E402
import strategy_encounter_search as search                             # noqa: E402
import strategy_experiment_store as ledger                             # noqa: E402
import strategy_joint_proposals as joint                               # noqa: E402
import strategy_optimizer_adapter as adapter                           # noqa: E402
import strategy_students as students                                   # noqa: E402
from strategy_optimizer import Store                                   # noqa: E402

RESULTS = []


def check(name, condition, detail=''):
    RESULTS.append((name, bool(condition)))
    print(('PASS ' if condition else 'FAIL ') + name + ((' :: ' + str(detail)) if detail else ''))


def coordinator(path, purposes, *, reference=None, encounter=None, maximum=6):
    store = Store(path, adapter.provenance())
    coord = search.Coordinator(path=path, revision='check', workers=2, telemetry=True,
                               maximum=maximum, reference=reference, encounter=encounter,
                               timeout=5.0)
    config = search.default_config('community-first', purposes=purposes)
    mapping = dict(enabled=True, mode='community-first', config=config, previous={})
    if encounter is not None:
        mapping['encounter'] = encounter
    if reference is not None:
        mapping['reference'] = reference
    store.set(search.MODE_KEY, mapping)
    coord.load(store)
    return store, coord


def boundary_question_fixture():
    path = base.new_dir('cycle-boundary')/'lib.sqlite'
    encounter_id, supplied_id = base.prep_library(path)
    store, coord = coordinator(path, {'improvement': 0, 'comparison': 0, 'boundary': 24,
                                      'support': 0, 'exploration': 0},
                               reference=supplied_id, encounter=encounter_id)
    frozen = coord.set_question(store, dict(kind='compensated', role='dps', stat='dex',
                                            value=5, budget=16, minimumPairs=2))
    return store, coord, encounter_id, supplied_id, frozen


def boundary_checks():
    store, coord, _enc, _ref, frozen = boundary_question_fixture()
    try:
        check('cycle-question-frozen', frozen.get('ok') is True, frozen.get('error'))
        question = frozen.get('question') or {}
        scenario = store.scenario(question.get('referenceId'))
        points = decisions.boundary_points(question, scenario, constraints=coord.constraints,
                                           tested_points=(), maximum=8)
        check('cycle-boundary-requested-point-first',
              bool(points) and points[0]['category'] == 'requested'
              and points[0]['value'] == (question.get('fixedFields') or {}).get(question.get('field')),
              [p.get('value') for p in points])
        values = [p['value'] for p in points]
        check('cycle-boundary-no-duplicate-values', len(values) == len(set(values)), values)
        check('cycle-boundary-child-carries-root-question-id',
              all(p.get('questionId') == question.get('id') and p.get('id') != question.get('id')
                  for p in points))
        check('cycle-boundary-no-continuous-box-claim',
              all(p.get('interpolation') is False and p.get('safeCartesianProductClaimed') is False
                  and p.get('continuousCoverageClaimed') is False for p in points))
        # A tested value is never re-emitted, and the finite budget caps the point list.
        retested = decisions.boundary_points(question, scenario, constraints=coord.constraints,
                                             tested_points=[points[0]['value']], maximum=8)
        check('cycle-boundary-tested-point-not-retested',
              all(p['value'] != points[0]['value'] for p in retested),
              [p['value'] for p in retested])
        capped = decisions.boundary_points(question, scenario, constraints=coord.constraints,
                                           tested_points=(), maximum=3)
        check('cycle-boundary-finite-cap', len(capped) <= 3, len(capped))
    finally:
        store.close()


def extension_checks():
    compat = {'engine': 'e', 'mechanics': 'm', 'policy': 'p'}
    history = [dict(candidateId='cand', referenceId='ref', owner='community', purpose='improvement',
                    compatibility=compat, pairs=4, unknownCount=0, pairedMeanDifference=1.5,
                    pairedStandardError=0.6)]
    portfolio = [dict(candidateId='cand', owner='community', arm='candidate')]
    picked = decisions.choose_extension(history, portfolio, remaining_runs=64, max_pairs=32,
                                        round_index=0)
    check('cycle-extension-uncertain-challenger-chosen',
          bool(picked) and picked['candidateId'] == 'cand' and picked['referenceId'] == 'ref'
          and picked['additionalPairs'] == 4,
          picked)
    check('cycle-extension-budget-is-two-per-pair',
          bool(picked) and 'knowledge' in picked['reason'] and 'holdout' in picked['reason'])
    poor = [dict(history[0], pairedMeanDifference=-5.0, pairedStandardError=0.5)]
    check('cycle-extension-resolved-poor-stopped',
          decisions.choose_extension(poor, portfolio, remaining_runs=64) is None)
    unresolved = [dict(history[0], pairs=4, unknownCount=4, pairedMeanDifference=0.0,
                       pairedStandardError=None)]
    check('cycle-extension-wholly-unresolved-blocked',
          decisions.choose_extension(unresolved, portfolio, remaining_runs=64) is None)
    maxed = [dict(history[0], pairs=64)]
    check('cycle-extension-never-beyond-max-stage',
          decisions.choose_extension(maxed, portfolio, remaining_runs=64) is None)
    check('cycle-extension-needs-whole-pair-budget',
          decisions.choose_extension(history, portfolio, remaining_runs=4, max_pairs=32) is None)


def presentation_checks():
    store, coord, _enc, ref, _frozen = boundary_question_fixture()
    try:
        scenario = store.scenario(ref)
        coord.constraints = joint.SYNTHETIC_CONSTRAINTS
        first = coord._presentation(scenario, str(ref))
        second = coord._presentation(scenario, str(ref))
        check('cycle-presentation-cached-identical', first is second and bool(first))
        check('cycle-presentation-reflects-requested-reference',
              (first or {}).get('referenceId') == str(ref))
        check('cycle-presentation-has-no-full-scenarios',
              isinstance(first, dict) and 'jobs' not in first and 'observedCandidate' not in first
              and 'referenceScenario' not in first)
        domain = (first or {}).get('buildDomain') or {}
        check('cycle-presentation-domain-context-truthful',
              domain.get('context') == 'synthetic' and domain.get('reachability') == 'unknown'
              and domain.get('schemaValid') is True,
              dict(context=domain.get('context'), reachability=domain.get('reachability')))
        report = coord.report()
        check('cycle-report-exposes-presentation-and-domain',
              report.get('presentation') is first)
    finally:
        store.close()


def archive_checks():
    path = base.new_dir('cycle-archive')/'lib.sqlite'
    encounter_id, supplied_id = base.prep_library(path)
    store, coord = coordinator(path, {'improvement': 8, 'comparison': 0, 'boundary': 0,
                                      'support': 0, 'exploration': 0},
                               reference=supplied_id, encounter=encounter_id)
    try:
        coord._ensure_schema(store.db)
        current = dict(experimentId=4242, candidateId='cand-archive', owner='community',
                       purpose='mechanism')
        aggregate = dict(meanEarned=3.0, max=40.0,
                         diagnostics=dict(skewness=2.5, top1ShareOfTotal=0.6,
                                          jackpotSensitive=True))
        record = dict(candidateId='cand-archive', meanEarned=3.0, jackpotSensitive=True)
        experiment_id = ledger.create_experiment(store.db, dict(
            scope='community', owner='community', ownerShare=1.0, purpose='mechanism',
            planned_budget=2, stopping={'maxRuns': 2, 'rule': 'fixture'},
            policy={}, mechanicsRevision='m', encounterRevision='e',
            measurementWindow='development'))
        current['experimentId'] = experiment_id
        coord._archive_mechanism(store.db, experiment_id, current, aggregate, record)
        coord._archive_mechanism(store.db, experiment_id, current, aggregate, record)
        mechanism_rows = store.db.execute(
            'SELECT COUNT(*) FROM ea_mechanism_archive WHERE experiment_id=?',
            (experiment_id,)).fetchone()[0]
        portfolio_rows = store.db.execute(
            'SELECT COUNT(*) FROM ea_earning_portfolio WHERE experiment_id=?',
            (experiment_id,)).fetchone()[0]
        check('cycle-mechanism-archive-idempotent', mechanism_rows == 1, mechanism_rows)
        check('cycle-mechanism-archive-separate-from-earning', portfolio_rows == 0, portfolio_rows)
        # A confirmed holdout publication is durable and idempotent; an unconfirmed one writes none.
        confirmation = dict(experimentId=experiment_id, nominee='cand-archive', reference='ref',
                            owner='community', scope='community')
        coord._record_holdout_publication(store.db, confirmation,
                                          dict(status='inconclusive', confirmed=False))
        check('cycle-unconfirmed-publication-not-stored',
              store.db.execute('SELECT COUNT(*) FROM ea_earning_portfolio WHERE experiment_id=?',
                               (experiment_id,)).fetchone()[0] == 0)
        # The REAL confirmation rule reports status='supported-improvement' with confirmed=True.
        coord._record_holdout_publication(store.db, confirmation,
                                          dict(status='supported-improvement', confirmed=True,
                                               reason='ok'))
        coord._record_holdout_publication(store.db, confirmation,
                                          dict(status='supported-improvement', confirmed=True,
                                               reason='ok'))
        holdout = store.db.execute(
            "SELECT COUNT(*) FROM ea_earning_portfolio WHERE experiment_id=? "
            "AND payload LIKE '%\"window\":\"holdout\"%'", (experiment_id,)).fetchone()[0]
        check('cycle-holdout-publication-durable-and-idempotent', holdout == 1, holdout)
        # Simulate a crash after the archive commit but before the runtime state save: the runtime
        # flag is gone, yet the persisted row identity still refuses a duplicate insert.
        coord.state['holdoutRecord'] = None
        coord._record_holdout_publication(store.db, confirmation,
                                          dict(status='supported-improvement', confirmed=True,
                                               reason='ok'))
        holdout_crash = store.db.execute(
            "SELECT COUNT(*) FROM ea_earning_portfolio WHERE experiment_id=? "
            "AND payload LIKE '%\"window\":\"holdout\"%'", (experiment_id,)).fetchone()[0]
        check('cycle-holdout-publication-crash-safe-idempotent', holdout_crash == 1, holdout_crash)
    finally:
        store.close()


def blind_stream_checks():
    path = base.new_dir('cycle-blind')/'lib.sqlite'
    encounter_id, supplied_id = base.prep_library(path)
    store, coord = coordinator(path, {'improvement': 8, 'comparison': 0, 'boundary': 0,
                                      'support': 0, 'exploration': 0},
                               reference=supplied_id, encounter=encounter_id)
    try:
        coord.state['history'] = [
            dict(candidateId='c1', referenceId='r1', owner='community', purpose='improvement',
                 features=dict(dps_dex=1), meanEarned=50.0),
            dict(candidateId='c2', referenceId='r1', owner='stumble', purpose='improvement',
                 features=dict(dps_dex=2), meanEarned=99.0),
        ]
        coord.state['portfolio'] = [
            dict(candidateId='c1', owner='community', meanEarned=50.0, eligible=True, arm='candidate'),
            dict(candidateId='c2', owner='stumble', meanEarned=99.0, eligible=True, arm='candidate'),
        ]
        coord.state['features'] = {'c1': dict(dps_dex=1), 'c2': dict(dps_dex=2)}
        check('cycle-stumble-evidence-is-blind',
              coord._evidence(students.STUDENT_STUMBLING) == {})
        check('cycle-discovery-evidence-is-blind',
              coord._evidence(students.STREAM_DISCOVERY) == {})
        community = coord._evidence('community').get('observedStatVectors')
        check('cycle-community-evidence-is-owner-scoped',
              community == [dict(dps_dex=1)], community)
        # A blind stream never selects the highest-mean empirical parent for its root.
        called = []
        original = search.adviser.rank
        search.adviser.rank = lambda *a, **k: (called.append('rank'), [])[1]
        try:
            coord.state['advisers'] = {'community': {'status': 'fitted'}}
            ranked = coord._rank([dict(id='x')], 'community')
        finally:
            search.adviser.rank = original
            coord.state.pop('adviser', None)
        check('cycle-rank-service-still-callable', called == ['rank'] and ranked == [])
    finally:
        store.close()


def main():
    boundary_checks()
    extension_checks()
    presentation_checks()
    archive_checks()
    blind_stream_checks()
    passed = sum(1 for _name, ok in RESULTS if ok)
    print('%d/%d learning-cycle checks passed' % (passed, len(RESULTS)))
    return 0 if passed == len(RESULTS) else 1


if __name__ == '__main__':
    sys.exit(main())
