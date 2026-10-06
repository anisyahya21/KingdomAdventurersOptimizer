"""Independent acceptance-path check for the Community-first encounter runtime.

Run:  <venv>/python -B -X utf8 tools/recovery/check_encounter_acceptance_path.py

This test owns NO production code. It drives the REAL coordinator
(`strategy_encounter_search.Coordinator`), the REAL shared proposal service
(`strategy_joint_proposals.pool`, reached through the coordinator), the REAL ledger
(`strategy_experiment_store`) and the REAL `Evaluator`, and mocks ONLY the battle pool behind
the documented `search.EVALUATOR_FACTORY` seam. It never patches the proposal generator, the
confirmation rule, the adviser, or the selected nominee to force a pass.

Two independent, PREDECLARED synthetic oracles (never tuned after seeing the nominee):

* joint oracle - relative to the frozen baseline effective DPS ATK/DEX, the baseline earns 10,
  any isolated one-axis move earns 0, and a compensated joint ATK/DEX move earns 20 only when
  the two axes move in OPPOSITE directions (every other joint move earns 0).
* boundary oracle - a fixed-build DPS DEX question with acceptable low/high islands and a
  degraded middle, so the tested set is reported per point with real gaps left unknown.

These rewards are invented test values, NOT game facts. Nothing here validates real combat,
the live library, settings, a desktop restart, or a whole-battle run.
"""
from __future__ import annotations

import json
import sys
import time
import traceback
import uuid
from concurrent.futures import Future
from copy import deepcopy
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
WORKSPACE = HERE.parents[1]
TMP = WORKSPACE / 'tmp/encounter-redesign-20260928'
REPORT_PATH = TMP / 'acceptance-path-report.md'

import strategy_encounter_search as search          # noqa: E402
import strategy_experiment_store as ledger          # noqa: E402
import strategy_mechanics as mechanics              # noqa: E402
import strategy_students as students                # noqa: E402
import search_contract as contract                  # noqa: E402
from strategy_optimizer import Store, baseline_scenarios    # noqa: E402
from strategy_optimizer_adapter import default_scenario, provenance, stats   # noqa: E402
import strategy_search                              # noqa: E402

RESULTS = []
TRACE = {'oracle': {}, 'joint': {}, 'boundary': {}, 'failures': []}
LIMITATIONS = []

ATK_PID = 13
DEX_PID = 19
#: PREDECLARED oracle constants, frozen before any scheduler run and never adjusted afterwards.
JOINT_BASELINE_REWARD = 10
JOINT_ISOLATED_REWARD = 0
JOINT_COMPENSATED_REWARD = 20


def check(name, condition, detail=''):
    RESULTS.append((name, bool(condition)))
    print(('PASS ' if condition else 'FAIL ') + name + ((' :: ' + str(detail)) if detail else ''))


def note(message):
    if message not in LIMITATIONS:
        LIMITATIONS.append(message)


def new_dir(tag):
    path = TMP / ('ka-accept-%s-%s' % (tag, uuid.uuid4().hex[:8]))
    path.mkdir(parents=True, exist_ok=True)
    return path


def joint_oracle_reward(delta_atk, delta_dex):
    """PREDECLARED synthetic interaction law, relative to the frozen baseline ATK/DEX.

    Defined before the run and never changed after seeing the selected nominee. It is an invented
    test law, not a recovered game rule.
    """
    if delta_atk == 0 and delta_dex == 0:
        return JOINT_BASELINE_REWARD
    if delta_atk == 0 or delta_dex == 0:
        return JOINT_ISOLATED_REWARD
    return JOINT_COMPENSATED_REWARD if (delta_atk > 0) != (delta_dex > 0) else 0


def dps_view(scenario):
    """Resolve the DPS role by identity (grid -> unit name), never by an assumed prepared index."""
    normalized = mechanics.normalize_scenario(scenario)
    placed = {int(row['grid']): row['unit'] for row in students._placed_rows(normalized)}
    grid = next((g for g in sorted(placed) if students.role(placed[g]) == students.ROLE_DPS), None)
    if grid is None:
        return None
    name = placed[grid]['name']
    index = next((i for i, unit in enumerate(normalized['ownUnits']) if unit['name'] == name), None)
    if index is None:
        return None
    unit = normalized['ownUnits'][index]
    prepared = mechanics.prepared_setup(normalized)
    prepared_index = next((i for i, member in enumerate(prepared['ownUnits'])
                           if member.get('name') == name), None)
    return dict(name=name, sourceIndex=index, preparedIndex=prepared_index,
                atk=int(contract.effective_parameter(normalized, unit, ATK_PID)),
                dex=int(contract.effective_parameter(normalized, unit, DEX_PID)))


def edit_dps(scenario, *, atk=None, dex=None):
    """A valid scenario edit: write an effective DPS stat through the engine's own writer."""
    edited = mechanics.normalize_scenario(deepcopy(scenario))
    view = dps_view(edited)
    unit = edited['ownUnits'][view['sourceIndex']]
    if atk is not None:
        contract.set_effective_parameter(edited, unit, ATK_PID, int(atk))
    if dex is not None:
        contract.set_effective_parameter(edited, unit, DEX_PID, int(dex))
    return edited


def prep_library(path, encounter=19):
    """One supplied Community baseline, so the library is a normal (non-fresh) library."""
    store = Store(path, provenance())
    base = default_scenario()
    base['tickLimit'] = strategy_search.DEFAULT_TICK_LIMIT
    scenario = next(sc for sc, _label in baseline_scenarios(base)
                    if int(sc['encounterId']) == encounter)
    with store.db:
        candidate_id = store.add(scenario, 'baseline', 'supplied', stats(scenario))
    store.close()
    return int(scenario['encounterId']), candidate_id, scenario


class JointOraclePool:
    """Deterministic mock battle pool for the joint-compensation oracle. Synthetic, not a game fact."""

    def __init__(self, baseline_atk, baseline_dex):
        self.baseline_atk = int(baseline_atk)
        self.baseline_dex = int(baseline_dex)
        self.calls = 0
        self.observed = []

    def submit(self, fn, scenario, seeds):
        self.calls += 1
        view = dps_view(scenario) or {}
        d_atk = int(view.get('atk', self.baseline_atk)) - self.baseline_atk
        d_dex = int(view.get('dex', self.baseline_dex)) - self.baseline_dex
        reward = joint_oracle_reward(d_atk, d_dex)
        self.observed.append((d_atk, d_dex, reward))
        future = Future()
        future.set_result(dict(seeds=list(seeds), verdict=1, resultBackend='native',
                               rewardOutcome=dict(pendingChests=reward),
                               elapsedSeconds=0.001, cpuSeconds=0.001))
        return future

    def shutdown(self, wait=True):
        pass

    def terminate_workers(self):
        pass


class BoundaryOraclePool:
    """Deterministic mock pool: DPS DEX acceptable low/high islands, degraded middle."""

    def __init__(self, low_max, high_min):
        self.low_max = int(low_max)
        self.high_min = int(high_min)
        self.calls = 0

    def submit(self, fn, scenario, seeds):
        self.calls += 1
        view = dps_view(scenario) or {}
        dex = int(view.get('dex', self.low_max))
        acceptable = dex <= self.low_max or dex >= self.high_min
        reward = JOINT_COMPENSATED_REWARD if acceptable else JOINT_ISOLATED_REWARD
        future = Future()
        future.set_result(dict(seeds=list(seeds), verdict=1, resultBackend='native',
                               rewardOutcome=dict(pendingChests=reward),
                               elapsedSeconds=0.001, cpuSeconds=0.001))
        return future

    def shutdown(self, wait=True):
        pass

    def terminate_workers(self):
        pass


def factory(pool):
    def make(*, workers, telemetry, timeout):
        import strategy_encounter_evaluation as evaluation
        return evaluation.Evaluator(workers=workers, telemetry=telemetry, pool=pool,
                                    timeout=timeout or 5.0)
    return make


def build(path, config, encounter, reference, pool, maximum=12):
    search.EVALUATOR_FACTORY = factory(pool)
    store = Store(path, provenance())
    store.set(search.MODE_KEY, dict(enabled=True, mode='community-first', config=config,
                                    encounter=encounter, reference=reference, previous={}))
    store.db.commit()
    coordinator = search.Coordinator(path=path, revision='acceptance', workers=2, telemetry=True,
                                     maximum=maximum, reference=reference, encounter=encounter,
                                     timeout=5.0)
    coordinator.load(store)
    return store, coordinator


def snapshot(coordinator):
    report = coordinator.report()
    progress = report['progress']
    measured = [row for row in report.get('portfolio') or [] if row.get('meanEarned') is not None]
    tested = set()
    for entry in report.get('boundaries') or []:
        for bucket in ('testedPoints', 'unresolvedGaps'):
            for item in entry.get(bucket) or []:
                value = (item.get('value') or {}).get('value') if isinstance(item, dict) else None
                if isinstance(value, int) and not isinstance(value, bool):
                    tested.add(int(value))
    return dict(round=progress.get('roundIndex'), experiments=progress.get('experiments'),
                currentPurpose=(progress.get('current') or {}).get('purpose'),
                currentProposal=(progress.get('current') or {}).get('proposalId'),
                measured=len(measured),
                bestMean=max((row['meanEarned'] for row in measured), default=None),
                confirmationPairs=len((progress.get('confirmation') or {}).get('pairs') or []),
                holdoutCompleted=(progress.get('confirmation') or {}).get('completed'),
                publication=(progress.get('publication') or {}).get('status'),
                boundaries=len(report.get('boundaries') or []), distinctTested=len(tested),
                idle=bool(progress.get('idle')))


def drive(coordinator, store, *, max_passes, seconds, stop=None):
    trace = []
    passes = 0
    began = time.time()
    deadline = began + seconds
    while passes < max_passes and time.time() < deadline:
        coordinator.run_pass(store, running=True)
        passes += 1
        snap = snapshot(coordinator)
        trace.append(snap)
        if stop and stop(snap):
            break
        if (snap['idle'] and not coordinator.busy and coordinator.state.get('current') is None
                and not coordinator._confirmation_pending()):
            break
        time.sleep(0.001)
    return trace, passes, round(time.time() - began, 3)


def budget_row(db, session_id, purpose):
    for row in ledger.session_status(db, session_id)['budgets']:
        if row['purpose'] == purpose:
            return row
    return {}


def safe_close(obj):
    try:
        if obj is not None:
            obj.close()
    except Exception:  # noqa: BLE001 - cleanup must never mask the real failure
        pass


# ---------------------------------------------------------------------------- sections
def section_oracle_selftest(base_scenario):
    view = dps_view(base_scenario)
    base_atk, base_dex = view['atk'], view['dex']
    TRACE['oracle'] = dict(baselineAtk=base_atk, baselineDex=base_dex,
                           sourceIndex=view['sourceIndex'], preparedIndex=view['preparedIndex'],
                           roleIndexMismatch=view['sourceIndex'] != view['preparedIndex'],
                           baselineReward=joint_oracle_reward(0, 0),
                           isolatedAtkReward=joint_oracle_reward(5, 0),
                           isolatedDexReward=joint_oracle_reward(0, 3),
                           oppositeUpDown=joint_oracle_reward(5, -2),
                           oppositeDownUp=joint_oracle_reward(-5, 2),
                           sameDirection=joint_oracle_reward(5, 2))
    check('oracle-baseline-earns-10', joint_oracle_reward(0, 0) == 10)
    check('oracle-isolated-atk-loses', joint_oracle_reward(5, 0) == 0)
    check('oracle-isolated-dex-loses', joint_oracle_reward(0, 3) == 0)
    check('oracle-opposite-joint-earns-20',
          joint_oracle_reward(5, -2) == 20 and joint_oracle_reward(-5, 2) == 20)
    check('oracle-same-direction-joint-earns-0', joint_oracle_reward(5, 2) == 0)
    # The oracle must react to REAL scenario edits, not just to input arguments.
    up = dps_view(edit_dps(base_scenario, atk=base_atk + 7))
    down = dps_view(edit_dps(base_scenario, dex=base_dex - 4))
    joint = dps_view(edit_dps(base_scenario, atk=base_atk + 7, dex=base_dex - 4))
    check('oracle-edit-isolated-atk', up['atk'] - base_atk == 7 and joint_oracle_reward(7, 0) == 0)
    check('oracle-edit-isolated-dex', down['dex'] - base_dex == -4 and joint_oracle_reward(0, -4) == 0)
    check('oracle-edit-opposite-joint-beats-baseline',
          joint['atk'] - base_atk == 7 and joint['dex'] - base_dex == -4
          and joint_oracle_reward(7, -4) == 20 > joint_oracle_reward(0, 0))
    # Identity, not a positional prepared index: the grid-resolved DPS unit and the prepared member
    # located by NAME must describe the same engine-effective ATK/DEX.
    normalized = mechanics.normalize_scenario(base_scenario)
    prepared_member = mechanics.prepared_setup(normalized)['ownUnits'][view['preparedIndex']]
    check('oracle-role-identity-is-the-dps-unit',
          students.role(normalized['ownUnits'][view['sourceIndex']]) == students.ROLE_DPS)
    check('oracle-identity-matches-prepared-by-name',
          view['atk'] == int(prepared_member['effectiveParameters'][ATK_PID]['value'])
          and view['dex'] == int(prepared_member['effectiveParameters'][DEX_PID]['value']),
          'atk=%s dex=%s' % (view['atk'], view['dex']))


def section_joint():
    path = new_dir('joint') / 'lib.sqlite'
    encounter, supplied, baseline = prep_library(path)
    view = dps_view(baseline)
    pool = JointOraclePool(view['atk'], view['dex'])
    config = search.default_config('community-first', purposes={
        'improvement': 128, 'comparison': 128, 'boundary': 0, 'support': 0, 'exploration': 0})
    store, coordinator = build(path, config, encounter, supplied, pool, maximum=12)
    store2 = coordinator2 = None
    try:
        trace, passes, seconds = drive(coordinator, store, max_passes=500, seconds=30.0,
                                       stop=lambda snap: snap['publication'] is not None)
        report = coordinator.report()
        publication = coordinator.state.get('publication') or {}
        confirmation = coordinator.state.get('confirmation') or {}
        proposals = [snap['currentProposal'] for snap in trace if snap.get('currentProposal')]
        distinct_proposals = sorted(set(proposals))
        first = proposals[0] if proposals else None
        second_after_evidence = any(
            snap.get('currentProposal') and snap['currentProposal'] != first
            and snap['experiments'] >= 1 and snap['measured'] >= 1 for snap in trace)
        nominee_id = publication.get('candidateId') or confirmation.get('nominee')
        nominee_scenario = store.scenario(nominee_id) if nominee_id is not None else None
        nominee = dps_view(nominee_scenario) if nominee_scenario is not None else None
        d_atk = (nominee['atk'] - view['atk']) if nominee else None
        d_dex = (nominee['dex'] - view['dex']) if nominee else None
        nominee_reward = joint_oracle_reward(d_atk, d_dex) if nominee else None
        pairs_distinct = store.db.execute(
            'SELECT COUNT(*) FROM (SELECT DISTINCT seed_a, seed_b FROM ea_holdout)').fetchone()[0]
        holdout_rows = store.db.execute('SELECT COUNT(*) FROM ea_holdout').fetchone()[0]
        status = ledger.session_status(store.db, coordinator.session_id)
        improvement = budget_row(store.db, coordinator.session_id, 'improvement')
        comparison_row = budget_row(store.db, coordinator.session_id, 'comparison')
        owners = sorted({row[0] for row in store.db.execute('SELECT DISTINCT owner FROM ea_experiment')})
        session_owners = sorted({row[0] for row in store.db.execute(
            'SELECT DISTINCT owner FROM ea_session_budget')})
        portfolio_rows = store.db.execute('SELECT COUNT(*) FROM ea_earning_portfolio').fetchone()[0]
        confirmed_archive = [json.loads(row[0]) for row in store.db.execute(
            'SELECT payload FROM ea_earning_portfolio WHERE experiment_id=? AND candidate_id=?',
            (confirmation.get('experimentId'), nominee_id))]
        confirmed_archive = [row for row in confirmed_archive
                             if row.get('window') == 'holdout' and row.get('confirmed') is True]
        comparison = publication.get('comparison') or {}
        TRACE['joint'] = dict(baselineAtk=view['atk'], baselineDex=view['dex'], proposalMaximum=12,
                              passes=passes, seconds=seconds,
                              experiments=report['progress']['experiments'],
                              distinctProposals=len(distinct_proposals),
                              secondProposalAfterEvidence=second_after_evidence,
                              poolCalls=pool.calls, published=publication.get('status'),
                              nominee=nominee_id, nomineeDeltaAtk=d_atk, nomineeDeltaDex=d_dex,
                              nomineeReward=nominee_reward, holdoutPairs=pairs_distinct,
                              holdoutRuns=holdout_rows, improvementBudget=improvement,
                              comparisonBudget=comparison_row, experimentOwners=owners,
                              sessionOwners=session_owners)
        check('joint-proposal-maximum-12', coordinator.maximum == 12, coordinator.maximum)
        check('joint-at-least-two-development-experiments',
              report['progress']['experiments'] >= 2, report['progress']['experiments'])
        check('joint-distinct-next-proposal-chosen-after-evidence',
              len(distinct_proposals) >= 2 and second_after_evidence,
              'distinct=%d afterEvidence=%s' % (len(distinct_proposals), second_after_evidence))
        check('joint-valid-joint-nominee', nominee_reward == JOINT_COMPENSATED_REWARD,
              'delta=(%s,%s) reward=%s' % (d_atk, d_dex, nominee_reward))
        check('joint-fresh-holdout-64-pairs',
              pairs_distinct == 64 and holdout_rows == 128
              and len(confirmation.get('pairs') or []) == 64,
              'pairs=%d rows=%d planned=%d' % (pairs_distinct, holdout_rows,
                                               len(confirmation.get('pairs') or [])))
        check('joint-canonical-confirmed-publication',
              publication.get('status') == 'published' and comparison.get('confirmed') is True
              and comparison.get('status') == 'supported-improvement',
              json.dumps(publication)[:180])
        check('joint-durable-earning-archive', portfolio_rows > 0, 'rows=%d' % portfolio_rows)
        check('joint-durable-confirmed-holdout-archive', len(confirmed_archive) == 1,
              'confirmed holdout rows=%d' % len(confirmed_archive))
        check('joint-budgets-finite-and-conserved',
              status['conserved'] and all(row['conserved'] for row in status['budgets'])
              and improvement.get('reserved') == 128 and improvement.get('completed') == 128
              and comparison_row.get('reserved') == 128 and comparison_row.get('completed') == 128,
              'improvement=%s comparison=%s' % (improvement, comparison_row))
        check('joint-non-community-zero',
              owners == ['community'] and session_owners == ['*'],
              'experiment owners=%s session owners=%s' % (owners, session_owners))
        # Restart durability: reload the SAME library and prove nothing is re-charged.
        rows_before = holdout_rows
        reserved_before = comparison_row.get('reserved')
        samples_before = store.db.execute('SELECT COUNT(*) FROM ea_sample').fetchone()[0]
        safe_close(coordinator)
        safe_close(store)
        coordinator = store = None
        store2 = Store(path, provenance())
        coordinator2 = search.Coordinator(path=path, revision='acceptance', workers=2, telemetry=True,
                                          maximum=12, reference=supplied, encounter=encounter, timeout=5.0)
        coordinator2.load(store2)
        drive(coordinator2, store2, max_passes=40, seconds=10.0)
        rows_after = store2.db.execute('SELECT COUNT(*) FROM ea_holdout').fetchone()[0]
        samples_after = store2.db.execute('SELECT COUNT(*) FROM ea_sample').fetchone()[0]
        comparison_after = budget_row(store2.db, coordinator2.session_id, 'comparison')
        archived_after = [json.loads(row[0]) for row in store2.db.execute(
            'SELECT payload FROM ea_earning_portfolio WHERE experiment_id=? AND candidate_id=?',
            (confirmation.get('experimentId'), nominee_id))]
        check('joint-confirmed-archive-survives-restart-once', sum(
            row.get('window') == 'holdout' and row.get('confirmed') is True
            for row in archived_after) == 1)
        TRACE['joint'].update(restartHoldoutRows=rows_after, restartSamples=samples_after,
                              restartComparisonReserved=comparison_after.get('reserved'),
                              restartPublication=(coordinator2.state.get('publication') or {}).get('status'))
        check('joint-restart-no-double-charge',
              rows_after == rows_before == 128 and samples_after == samples_before
              and comparison_after.get('reserved') == reserved_before == 128
              and (coordinator2.state.get('publication') or {}).get('status') == 'published',
              'rows %d->%d samples %d->%d reserved %s' % (rows_before, rows_after, samples_before,
                                                          samples_after, comparison_after.get('reserved')))
    finally:
        safe_close(coordinator)
        safe_close(store)
        safe_close(coordinator2)
        safe_close(store2)


def section_boundary():
    path = new_dir('boundary') / 'lib.sqlite'
    encounter, supplied, baseline = prep_library(path)
    view = dps_view(baseline)
    d0 = view['dex']
    lo, hi = contract.stat_bounds()[DEX_PID]
    gap = max(2, (hi - d0) // 4)
    low_max = d0
    high_min = d0 + gap
    requested = max(int(lo), d0 - 1)
    pool = BoundaryOraclePool(low_max, high_min)
    config = search.default_config('community-first', purposes={
        'improvement': 0, 'comparison': 0, 'boundary': 64, 'support': 0, 'exploration': 0})
    store, coordinator = build(path, config, encounter, supplied, pool, maximum=12)
    try:
        frozen = coordinator.set_question(store, dict(kind='fixed-build', role='dps', stat='dex',
                                                      value=requested, budget=64, minimumPairs=2,
                                                      tolerance=0.9))
        check('boundary-fixed-question-frozen', frozen.get('ok') is True, frozen.get('error'))
        question = frozen.get('question') or {}
        trace, passes, seconds = drive(coordinator, store, max_passes=200, seconds=20.0)
        entries = coordinator.report().get('boundaries') or []
        tested = {}
        for entry in entries:
            for bucket in ('testedPoints', 'unresolvedGaps'):
                for item in entry.get(bucket) or []:
                    value = (item.get('value') or {}).get('value')
                    if isinstance(value, int) and not isinstance(value, bool) and int(value) not in tested:
                        tested[int(value)] = item.get('classification')
        classes = sorted({str(c) for c in tested.values() if c})
        interpolation = any((entry.get('assessment') or {}).get('interpolation') for entry in entries)
        payloads = [json.loads(row[0]) for row in store.db.execute('SELECT payload FROM ea_boundary')]
        safe_box = any(bool(payload.get('safeCartesianProductClaimed')) for payload in payloads)
        untested = len(set(range(int(lo), int(hi) + 1)) - set(tested))
        boundary_rows = store.db.execute('SELECT COUNT(*) FROM ea_boundary').fetchone()[0]
        TRACE['boundary'] = dict(field=question.get('field'), referenceValue=d0, requested=requested,
                                 legalLow=int(lo), legalHigh=int(hi),
                                 islands=dict(lowMax=low_max, highMin=high_min),
                                 testedValues=sorted(tested), classes=classes, passes=passes,
                                 seconds=seconds, distinct=len(tested), untestedGaps=untested,
                                 interpolationClaimed=bool(interpolation),
                                 safeCartesianClaimed=bool(safe_box), boundaryRows=boundary_rows)
        check('boundary-at-least-three-distinct-tested-values', len(tested) >= 3, sorted(tested))
        check('boundary-classes-conditional-and-mixed',
              'supported-acceptable' in classes and 'supported-degraded' in classes, classes)
        check('boundary-no-interpolation-or-safe-cartesian',
              not interpolation and not safe_box,
              'interp=%s safeBox=%s' % (interpolation, safe_box))
        check('boundary-untested-gaps-remain-unknown', untested > 0, 'untested=%d' % untested)
        check('boundary-durable-archive', boundary_rows > 0, 'rows=%d' % boundary_rows)
    finally:
        safe_close(coordinator)
        safe_close(store)


def write_report():
    passed = sum(1 for _name, ok in RESULTS if ok)
    lines = [
        '# Encounter acceptance path - report',
        'Generated: %s' % time.strftime('%Y-%m-%d %H:%M:%S'),
        'Runner: tools/recovery/check_encounter_acceptance_path.py (independent deterministic oracle).',
        'SCOPE: synthetic scheduler/oracle checks only; rewards are invented test values, NOT game facts.',
        'Mocked: battle pool only, via search.EVALUATOR_FACTORY. Real: coordinator, proposal service, ledger, Evaluator.',
        'Result: %d/%d checks passed' % (passed, len(RESULTS)),
        '',
        '## Checks',
    ]
    for name, ok in RESULTS:
        lines.append(('PASS ' if ok else 'FAIL ') + name)
    lines.append('')
    lines.append('## Compact JSON trace')
    for key in ('oracle', 'joint', 'boundary'):
        if TRACE.get(key):
            lines.append('%s=%s' % (key, json.dumps(TRACE[key], sort_keys=True, separators=(',', ':'))))
    if TRACE['failures']:
        lines.append('failures=%s' % json.dumps(TRACE['failures'], sort_keys=True, separators=(',', ':')))
    lines.append('')
    lines.append('## Honesty notes')
    for item in LIMITATIONS:
        lines.append('- ' + item)
    REPORT_PATH.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return passed


def main():
    TMP.mkdir(parents=True, exist_ok=True)
    try:
        _encounter, _cid, base = prep_library(TMP / ('ka-accept-oracle-%s.sqlite' % uuid.uuid4().hex[:8]))
        section_oracle_selftest(base)
    except Exception:  # noqa: BLE001 - a broken oracle is a failing test, reported not hidden
        TRACE['failures'].append({'section': 'oracle',
                                  'trace': traceback.format_exc().strip().splitlines()[-3:]})
        check('oracle-selftest-ran', False, 'exception; see trace')
    for name, section in (('joint', section_joint), ('boundary', section_boundary)):
        try:
            section()
        except Exception:  # noqa: BLE001 - a broken section is a failing test, reported not hidden
            TRACE['failures'].append({'section': name,
                                      'trace': traceback.format_exc().strip().splitlines()[-3:]})
            check('%s-section-ran' % name, False, 'exception; see trace')
    search.EVALUATOR_FACTORY = None
    note('Synthetic rewards are invented test values, not game facts.')
    note('Proposals, confirmation, adviser and nominee selection were never patched; only the mock pool was installed.')
    note('No live library, settings, desktop restart, install or real battle was used.')
    passed = write_report()
    print('%d/%d acceptance checks passed' % (passed, len(RESULTS)))
    return 0 if passed == len(RESULTS) else 1


if __name__ == '__main__':
    sys.exit(main())
