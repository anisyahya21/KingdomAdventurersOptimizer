"""No-battle mechanical checks for benchmark_encounter_redesign.py.

Every check is deterministic and opens no live library. The plan/budget/contract/transport logic is
exercised against a synthetic fixture and mocked pool futures, and the only real-file checks confirm
the frozen baseline is not written and the CLI refuses to start without authorisation. Run:

    python -B -X utf8 tools/recovery/check_benchmark_encounter_redesign.py
"""
from __future__ import annotations

import inspect
import contextlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import benchmark_encounter_redesign as harness  # noqa: E402

PYTHON = harness.default_python()


def _fake_pool_module():
    class Pool:
        def __init__(self):
            self.calls = []

        def submit(self, function, scenario, seeds):
            self.calls.append(('submit', [list(seeds)]))
            return dict(seeds=list(seeds))

        def submit_batch(self, function, scenario, seed_pairs):
            self.calls.append(('batch', [list(pair) for pair in seed_pairs]))
            return [dict(seeds=list(pair)) for pair in seed_pairs]

    class Module:
        HeadlessPool = Pool

    return Module


def main():
    checks = []

    def check(name, condition, detail=''):
        checks.append(dict(check=name, ok=bool(condition), detail=str(detail)))

    def refuse(call):
        try:
            call()
        except Exception as exc:  # noqa: BLE001
            return exc
        return None

    work = HERE.parents[1] / 'tmp' / 'encounter-redesign-20260928' / 'check-work'
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True, exist_ok=True)
    baseline, evidence = harness._synthetic_fixture(work)

    budget = harness.build_budget(4, 3)
    check('budget-planned-4944', budget['plannedBattles'] == 4944)
    check('budget-ceiling-4976', budget['hardCeiling'] == 4976)
    check('budget-global-cap', budget['globalCap'] == 4976)
    check('budget-setup-only-if-used', budget.get('setupOnlyIfUsed') is True)
    check('budget-setup-within-cap', budget['setupBattles'] <= budget['setupCap'])
    check('budget-items-sum', budget['setupBattles'] == sum(budget['setup'].values()))
    check('budget-over-cap-refused',
          isinstance(refuse(lambda: harness.build_budget(4, 3, setup_cap=4)), harness.HarnessError))

    ledger_path = work / 'ledger.json'
    ledger = harness.BudgetLedger(ledger_path, 30, setup_cap=4)
    reservation = ledger.reserve('search', 20)
    check('ledger-global-reserve', ledger.spent == 20 and ledger.remaining == 10)
    check('ledger-refuses-overrun',
          isinstance(refuse(lambda: ledger.reserve('x', 11)), harness.Blocked))
    ledger.settle(reservation, 18)
    ledger.charge_setup('calibration', 4)
    check('ledger-settles-and-setup', ledger.spent == 18 and ledger.setup_spent == 4)
    check('ledger-refuses-setup-overrun',
          isinstance(refuse(lambda: ledger.charge_setup('x', 1)), harness.Blocked))
    check('ledger-persists-across-resume',
          harness.BudgetLedger(ledger_path, 30, setup_cap=4).spent == 18)

    module = _fake_pool_module()
    meter = harness.ExecutionMeter(128, stage='transport')
    pause = []
    uninstall = harness.install_bounded_dispatch(meter, pool_module=module,
                                                 pause_enqueue=lambda: pause.append('pause'))
    pool = module.HeadlessPool()
    submitted, denied = 0, False
    try:
        while True:
            pool.submit_batch(None, {}, [[1, index] for index in range(8)])
            submitted += 8
    except harness.BudgetExhausted:
        denied = True
    check('transport-exact-cap', meter.charged == 128 and submitted == 128, meter.snapshot())
    check('transport-deny-at-zero', denied and meter.denied >= 1)
    check('transport-pause-at-last-grant', len(pause) == 1)
    uninstall()
    module2 = _fake_pool_module()
    meter2 = harness.ExecutionMeter(5, stage='clamp')
    harness.install_bounded_dispatch(meter2, pool_module=module2)
    module2.HeadlessPool().submit_batch(None, {}, [[1, index] for index in range(8)])
    check('transport-clamps-batch', meter2.charged == 5)

    # --- pre-charge scenario guard: the ACTUAL dispatched scenario is refused before any charge ----
    guard_module = _fake_pool_module()
    guard_meter = harness.ExecutionMeter(4, stage='guard-transport')
    guard_seen = []

    def _mock_guard(scenario):
        guard_seen.append(scenario)
        if scenario.get('encounterId') != 19:
            raise harness.Blocked('bad scope')
        if not scenario.get('community'):
            raise harness.Blocked('bad owner')

    harness.install_bounded_dispatch(guard_meter, pool_module=guard_module,
                                     scenario_validator=_mock_guard)
    guard_pool = guard_module.HeadlessPool()
    bad_scope = refuse(lambda: guard_pool.submit(None, dict(encounterId=3, community=True), [1, 2]))
    check('guard-refuses-bad-scope-before-charge',
          isinstance(bad_scope, harness.Blocked) and guard_meter.charged == 0
          and guard_meter.denied == 0)
    bad_owner = refuse(lambda: guard_pool.submit_batch(
        None, dict(encounterId=19, community=False), [[1, 2], [3, 4]]))
    check('guard-refuses-bad-owner-before-charge-batch',
          isinstance(bad_owner, harness.Blocked) and guard_meter.charged == 0)
    guard_pool.submit(None, dict(encounterId=19, community=True), [5, 6])
    check('guard-valid-passes-and-charges-one',
          guard_meter.charged == 1 and len(guard_seen) == 3)
    empty_module = _fake_pool_module()
    empty_meter = harness.ExecutionMeter(0, stage='guard-empty')
    harness.install_bounded_dispatch(empty_meter, pool_module=empty_module,
                                     scenario_validator=_mock_guard)
    empty_raised = refuse(lambda: empty_module.HeadlessPool().submit(
        None, dict(encounterId=3, community=True), [1, 2]))
    check('guard-runs-before-meter-would-deny',
          isinstance(empty_raised, harness.Blocked) and empty_meter.denied == 0)

    # --- the real guard builder: frozen STORED reference policy, never adapter.default_scenario -----
    def _stored_reference(tick=30000):
        """A legal stored supplied scenario whose policy is the frozen 30000 horizon."""
        return dict(schema='ka-special-combat-research-1', encounterId=19, defeatCount=0, mathSeed=7,
                    libSeed=8, tickLimit=tick, finishPolicy='on-verdict', holyHerbStock=0, inputs=[],
                    startProfile=dict(kind='isolated-scene0', enemySpawnCell=[0, 0], bossCell=None,
                                      startingStatus={}),
                    ownUnits=[dict(name='probe fighter', human=True, monsterId=None, weaponId=0,
                                   equipment=[], visitor=False, leaderIdentity=False, skills=[26, 25],
                                   invocationLevels=[1, 1],
                                   parameters={p: dict(rawValue=1,
                                                       rawMax=1 if p in (10, 11) else 2147483647,
                                                       extraValue=0, extraMax=0, trainingLevel=123)
                                               for p in range(10, 41)})])

    class _GuardAdapter:
        """The arm's real normalization path; its default is deliberately the WRONG (3000) policy."""

        def __init__(self, *, default_tick=3000, normalize_tick=30000):
            self._default_tick = default_tick
            self._normalize_tick = normalize_tick

        def default_scenario(self):
            return _stored_reference(self._default_tick)

        def validate_scenario(self, scenario):
            return dict(scenario, tickLimit=self._normalize_tick)

    class _GuardStudents:
        """Mimics the real contract: community membership plus C7 relative to the pinned parent."""

        def community_admits(self, scenario, parent=None):
            if not scenario.get('community', True):
                return False, [('C2', 'not the community placement')]
            if parent is not None and scenario.get('skills') != parent.get('skills'):
                return False, [('C7', 'the community skill-set was changed')]
            return True, []

    def _guard_plan(tick=30000, reference=True):
        fields = dict(finishPolicy='on-verdict', tickLimit=tick, holyHerbStock=0, inputs=[],
                      defeatCount=0,
                      startProfile=dict(kind='isolated-scene0', enemySpawnCell=[0, 0], bossCell=None,
                                        startingStatus={}))
        record = {'fields': fields}
        if reference:
            record['referencesByEncounter'] = {
                '19': {'stored': _stored_reference(tick), 'encounterId': 19, 'candidateId': 'ref'}}
        return {'arms': {'probe': {'observedPolicy': record}}}

    guard_adapter = _GuardAdapter()
    guard_students = _GuardStudents()
    bound_guard = harness.build_scenario_guard(_guard_plan(), 'probe', 19, adapter=guard_adapter,
                                               students=guard_students)
    valid = harness.apply_frozen_policy(dict(encounterId=19, community=True), bound_guard)
    bound_guard(valid)
    check('guard-builder-accepts-exact-valid-and-records-hash',
          sorted(bound_guard.hashes) == [bound_guard.frozenHash])
    check('guard-uses-frozen-stored-policy-not-adapter-default',
          bound_guard.policyFields['tickLimit'] == 30000
          and guard_adapter.default_scenario()['tickLimit'] == 3000)
    check('guard-builder-refuses-changed-horizon',
          isinstance(refuse(lambda: bound_guard(dict(valid, tickLimit=999))), harness.Blocked))
    check('guard-builder-refuses-adapter-default-horizon',
          isinstance(refuse(lambda: bound_guard(dict(valid, tickLimit=3000))), harness.Blocked))
    check('guard-builder-refuses-changed-defeat-count',
          isinstance(refuse(lambda: bound_guard(dict(valid, defeatCount=1))), harness.Blocked))
    check('guard-builder-refuses-changed-start-profile',
          isinstance(refuse(lambda: bound_guard(dict(valid, startProfile={'kind': 'other'}))),
                     harness.Blocked))
    check('guard-builder-refuses-non-community',
          isinstance(refuse(lambda: bound_guard(dict(valid, community=False))), harness.Blocked))
    check('guard-builder-refuses-wrong-encounter',
          isinstance(refuse(lambda: bound_guard(dict(valid, encounterId=5))), harness.Blocked))
    check('guard-builder-fails-closed-without-frozen-study',
          isinstance(refuse(lambda: harness.build_scenario_guard(
              {'arms': {'probe': {}}}, 'probe', 19, adapter=guard_adapter,
              students=guard_students)), harness.Blocked))
    check('guard-builder-fails-closed-without-stored-reference',
          isinstance(refuse(lambda: harness.build_scenario_guard(
              _guard_plan(reference=False), 'probe', 19, adapter=guard_adapter,
              students=guard_students)), harness.Blocked))
    check('guard-builder-fails-closed-when-arm-normalization-changed',
          isinstance(refuse(lambda: harness.build_scenario_guard(
              _guard_plan(), 'probe', 19, adapter=_GuardAdapter(normalize_tick=3000),
              students=guard_students)), harness.Blocked))
    check('guard-frozen-policy-application',
          harness.apply_frozen_policy(dict(encounterId=19), bound_guard)['tickLimit'] == 30000)
    # A stored 30000 dispatch passes; the stale adapter default 3000 is refused BEFORE any charge.
    changed_meter = harness.ExecutionMeter(4, stage='guard-changed-3000')
    changed_module = _fake_pool_module()
    harness.install_bounded_dispatch(changed_meter, pool_module=changed_module,
                                     scenario_validator=bound_guard)
    changed_scenario = harness.apply_frozen_policy(dict(encounterId=19, community=True), bound_guard)
    changed_scenario['tickLimit'] = 3000
    changed_error = refuse(lambda: changed_module.HeadlessPool().submit(None, changed_scenario, [1, 2]))
    check('guard-refuses-changed-3000-before-meter-charge',
          isinstance(changed_error, harness.Blocked) and changed_meter.charged == 0
          and changed_meter.denied == 0)
    # A C7 break (the community skill-set changed relative to the encounter's pinned reference) is
    # refused BEFORE any meter charge, exactly like the policy break above.
    c7_meter = harness.ExecutionMeter(4, stage='guard-c7')
    c7_module = _fake_pool_module()
    harness.install_bounded_dispatch(c7_meter, pool_module=c7_module, scenario_validator=bound_guard)
    c7_error = refuse(lambda: c7_module.HeadlessPool().submit(
        None, harness.apply_frozen_policy(dict(encounterId=19, community=True, skills=[99]),
                                          bound_guard), [1, 2]))
    check('guard-refuses-c7-mutation-before-meter-charge',
          isinstance(c7_error, harness.Blocked) and c7_meter.charged == 0 and c7_meter.denied == 0)
    check('guard-guards-community-relative-to-pinned-reference',
          'parent=reference_scenario' in inspect.getsource(harness.build_scenario_guard)
          and 'referencesByEncounter' in inspect.getsource(harness.pinned_reference))
    check('search-and-holdout-install-precharge-guard',
          'scenario_validator=guard' in inspect.getsource(harness.search_stage)
          and 'scenario_validator=guard' in inspect.getsource(harness.holdout_stage))
    check('smoke-is-not-guarded',
          'scenario_validator' not in inspect.getsource(harness.arm_smoke)
          and 'install_bounded_dispatch' not in inspect.getsource(harness.arm_smoke))

    registry_path = work / 'registry.json'
    registry = harness.HoldoutRegistry(registry_path, 'pid', 'nonce')
    first = registry.issue('holdout|0|1', 6, forbidden={(1, 2)})
    second = registry.issue('holdout|0|2', 6, forbidden={(1, 2)})
    check('registry-rejects-forbidden', (1, 2) not in first)
    check('registry-global-distinct', not (set(first) & set(second)))
    check('registry-refuses-reissue',
          isinstance(refuse(lambda: harness.HoldoutRegistry(registry_path, 'pid', 'nonce')
                            .issue('holdout|0|1', 6, forbidden=set())), harness.Blocked))
    fresh = harness.HoldoutRegistry(work / 'registry-fresh.json', 'pid', 'nonce')
    check('registry-deterministic', fresh.issue('holdout|0|1', 6, forbidden={(1, 2)}) == first)

    plan = harness.build_plan(harness._Args(baseline=str(baseline), evidence=str(work),
                                            encounters='0,15,18,19'), python=PYTHON)
    errors = [e for e in harness.validate_plan(plan) if 'open items' not in e]
    check('plan-valid', not errors, '; '.join(errors))
    check('plan-scope-record',
          isinstance(plan.get('scope'), dict) and 'finishPolicy' in plan['scope'])
    check('plan-digest-stable', plan['digest'] == harness.digest(
        {k: v for k, v in plan.items() if k != 'digest'}))
    check('plan-arms-separated', plan['arms']['legacy']['source'] != plan['arms']['new']['source'])
    check('plan-union-across-whole-library',
          plan['holdout'].get('unionAcrossWholeLibrary') is True
          and 'unionOfBothNominees' not in plan['holdout'])
    check('plan-holdout-after-both-freezes',
          not harness.holdout_ordering_errors(harness.plan_steps(plan)))
    bad_steps = [dict(op='holdout', arm='legacy', encounterId=0, replicate=1),
                 dict(op='freeze', arm='legacy', encounterId=0, replicate=1)]
    check('ordering-detects-early-holdout', bool(harness.holdout_ordering_errors(bad_steps)))
    tampered = json.loads(json.dumps(plan))
    tampered['holdout']['freezeBothArmsBeforeHoldout'] = False
    check('plan-tamper-detected', bool(harness.validate_plan(tampered)))
    check('incomplete-budget-not-success',
          harness.cross_arm_summary([], plan, incomplete=True)['decisive'] == 0)
    harness.write_json(work / 'plan.json', plan)

    nominee, detail = harness.select_nominee([dict(candidateId='sha-a', meanEarned=None, jackpot=9),
                                              dict(candidateId='sha-b', meanEarned=1.5)])
    check('nominee-best-mean-not-jackpot', nominee == 'sha-b' and not detail['noEligible'])
    check('nominee-explicit-no-eligible',
          harness.select_nominee([dict(candidateId='sha-a', meanEarned=None)])[1]['noEligible'])
    check('nominee-respects-explicit-ineligibility',
          harness.select_nominee([dict(candidateId='a', meanEarned=9.0, eligible=False),
                                  dict(candidateId='b', meanEarned=1.0)])[0] == 'b'
          and harness.select_nominee([dict(candidateId='a', meanEarned=9.0,
                                           eligibleForRecommendation=False),
                                      dict(candidateId='b', meanEarned=1.0)])[0] == 'b')

    # --- the nominee scope is the ACTUAL stored candidates admitted to the Community -------------
    scope_db = work / 'scope.sqlite'
    if scope_db.exists():
        scope_db.unlink()
    scope_connection = sqlite3.connect(str(scope_db))
    scope_connection.executescript(
        'CREATE TABLE candidate(id TEXT PRIMARY KEY, scenario TEXT NOT NULL);')
    for cid, admit in (('high-invalid', False), ('valid-second', True), ('low-valid', True)):
        scope_connection.execute('INSERT INTO candidate VALUES (?,?)',
                                 (cid, json.dumps(dict(encounterId=19, admit=admit))))
    scope_connection.commit()
    scope_connection.close()

    class _ScopeStudents:
        def community_admits(self, scenario, parent=None):
            return bool(scenario.get('admit')), []

    scope_rows = [dict(candidateId='high-invalid', meanEarned=99.0),
                  dict(candidateId='valid-second', meanEarned=5.0),
                  dict(candidateId='low-valid', meanEarned=1.0),
                  dict(candidateId='foreign-family', meanEarned=1000.0)]
    scope_admitted = harness.admitted_candidate_ids(
        scope_db, [row['candidateId'] for row in scope_rows], 19,
        dict(encounterId=19), _ScopeStudents())
    scoped = harness._scope_measured(scope_rows, scope_admitted, 19)
    scoped_nominee, scoped_detail = harness.select_nominee(scoped)
    check('scope-excludes-unadmitted-and-unstored-highest-mean',
          scope_admitted == {'valid-second', 'low-valid'} and scoped_nominee == 'valid-second'
          and scoped_detail['eligibleCount'] == 2)
    none_scope, none_scope_detail = harness.select_nominee(
        harness._scope_measured(scope_rows, set(), 19))
    check('scope-no-eligible-candidate-is-explicit-none',
          none_scope is None and none_scope_detail['noEligible'])

    # --- the measured fallback reads the arm's own durable store (bounded), never a status field --
    measured_db = work / 'measured.sqlite'
    if measured_db.exists():
        measured_db.unlink()
    shutil.copyfile(baseline, measured_db)
    measured_connection = sqlite3.connect(str(measured_db))
    for cid, mean in (('c0', 1.0), ('c1', 3.5)):
        measured_connection.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',
                                    (f'aggregate:{cid}:validation',
                                     json.dumps(dict(chestSum=mean * 4, chestCount=4, n=6,
                                                     chestSum2=mean * mean * 4 + 0.5))))
    measured_connection.commit()
    measured_connection.close()
    legacy_rows = harness.stored_measured_snapshot(measured_db, 19)
    check('stored-measured-snapshot-reads-legacy-aggregate',
          {(row['candidateId'], row['meanEarned']) for row in legacy_rows}
          == {('c0', 1.0), ('c1', 3.5)}
          and all(row['measuredSource'] == 'legacy-validation-aggregate'
                  and row['evidenceClass'] == 'legacy-validation-aggregate' for row in legacy_rows))
    # A durable ea_earning_portfolio row is NEVER used for the new arm: it aggregates every past
    # window/experiment/revision and is a confirmation leak, so the new arm has NO durable fallback.
    measured_connection = sqlite3.connect(str(measured_db))
    measured_connection.execute('CREATE TABLE IF NOT EXISTS ea_earning_portfolio('
                                'id INTEGER PRIMARY KEY AUTOINCREMENT, experiment_id INTEGER, '
                                'candidate_id TEXT, payload TEXT NOT NULL, created_at REAL NOT NULL)')
    measured_connection.execute(
        'INSERT INTO ea_earning_portfolio(experiment_id,candidate_id,payload,created_at) '
        'VALUES (?,?,?,?)',
        (7, 'c0', json.dumps(dict(candidateId='c0', meanEarned=8.0, total=10, resolved=9,
                                  eligible=True)), 0.0))
    measured_connection.commit()
    measured_connection.close()
    after_ea = harness.stored_measured_snapshot(measured_db, 19)
    check('stored-measured-snapshot-ignores-new-ea-portfolio',
          {row['candidateId']: row['meanEarned'] for row in after_ea} == {'c0': 1.0, 'c1': 3.5}
          and all(row['measuredSource'] == 'legacy-validation-aggregate' for row in after_ea))
    check('stored-measured-snapshot-new-arm-has-no-durable-fallback',
          harness.stored_measured_snapshot(measured_db, 19, arm=harness.ARM_NEW) == [])
    check('stored-measured-snapshot-empty-encounter-is-empty',
          harness.stored_measured_snapshot(measured_db, 12345) == [])
    # A missing or non-finite chest counter yields NO row (no mean), never a fabricated zero.
    bad_db = work / 'bad-measured.sqlite'
    if bad_db.exists():
        bad_db.unlink()
    shutil.copyfile(baseline, bad_db)
    bad_connection = sqlite3.connect(str(bad_db))
    bad_connection.execute("INSERT OR REPLACE INTO meta VALUES ('aggregate:c0:validation',?)",
                           (json.dumps(dict(chestSum=None, chestCount=4, n=1)),))
    bad_connection.execute("INSERT OR REPLACE INTO meta VALUES ('aggregate:c1:validation',?)",
                           (json.dumps(dict(chestSum=float('nan'), chestCount=4, n=1)),))
    bad_connection.commit()
    bad_connection.close()
    check('legacy-aggregate-missing-or-nonfinite-counter-no-mean',
          harness.stored_measured_snapshot(bad_db, 19) == [])

    # freeze_nomination falls back to the durable snapshot when the live view is empty, keeping the
    # SAME community/policy scope filter and best-mean selection (never a fabricated baseline).
    freeze_plan = {'arms': {'legacy': {'observedPolicy': {'referencesByEncounter': {
        '19': dict(encounterId=19, candidateId='c0', stored=harness._fixture_scenario(19))}}}},
        'scope': {'finishPolicy': 'on-verdict'}}
    freeze_out = work / 'freeze-db'
    freeze_out.mkdir(parents=True, exist_ok=True)
    frozen = harness.freeze_nomination({}, harness.ARM_LEGACY, freeze_out, freeze_plan, 19, 1,
                                       copy_path=measured_db, students=guard_students)
    check('freeze-nomination-falls-back-to-durable-snapshot',
          frozen['nominee'] == 'c1' and frozen['selection']['noEligible'] is False
          and frozen['candidateScope']['measuredSource'] == 'stored-measurement'
          and len(frozen['measured']) == 2)
    empty_out = work / 'freeze-empty'
    empty_out.mkdir(parents=True, exist_ok=True)
    empty_measured = work / 'empty-measured.sqlite'
    shutil.copyfile(baseline, empty_measured)
    empty_frozen = harness.freeze_nomination({}, harness.ARM_LEGACY, empty_out, freeze_plan, 19, 1,
                                             copy_path=empty_measured,
                                             students=guard_students)
    check('freeze-nomination-genuine-no-measurement-is-no-eligible',
          empty_frozen['nominee'] is None and empty_frozen['selection']['noEligible'] is True)

    samples = [
        dict(seeds=[1, 2], verdict=2, censored=False,
             rewardOutcome=dict(pendingChests=0, awardedChests=0, awardedBasis='native-win-loss-gate')),
        dict(seeds=[3, 4], verdict=1, censored=False,
             rewardOutcome=dict(pendingChests=3, awardedChests=3,
                                awardedBasis='reward-entitlement-certificate')),
        dict(seeds=[5, 6], verdict=1, censored=False,
             rewardOutcome=dict(pendingChests=1, awardedChests=None,
                                awardedBasis='unknown-win-without-certificate')),
        dict(seeds=[7, 8], verdict=1, error='boom', censored=False, rewardOutcome={}),
    ]
    rows = [dict(candidateId='c0', encounterId=19, seeds=s['seeds'], raw=s) for s in samples]
    applied, counts = harness.apply_canonical(rows, dict(finishPolicy=None))
    check('canonical-counts-kept', counts['total'] == 4 and counts['error'] == 1
          and counts['unresolved'] >= 2 and counts['loss'] == 1 and counts['certified'] == 1)
    check('canonical-loss-zero', applied[0]['outcome']['finalEarned'] == 0)
    check('canonical-unknown-none', applied[2]['outcome']['finalEarned'] is None)
    check('canonical-cells-unknown-none', harness.earned_cells(applied)[('c0', (5, 6))] is None)
    check('bootstrap-fraction',
          harness.paired_bootstrap([1, 1, 1], resamples=200)['fractionPositive'] == 1.0)

    # --- native-backend fail-closed gate ---------------------------------------------------------
    check('native-accepts-native',
          harness.assert_native_backend([dict(raw=dict(resultBackend='native'))] * 3,
                                        stage='mock')['native'] == 3)
    check('native-refuses-python-fallback',
          isinstance(refuse(lambda: harness.assert_native_backend(
              [dict(raw=dict(resultBackend='python', nativeFallbackReason='x'))], stage='mock')),
              harness.Blocked))
    check('native-refuses-unknown-backend',
          isinstance(refuse(lambda: harness.assert_native_backend(
              [dict(raw={})], stage='mock')), harness.Blocked))

    # --- reviewed observer migration gate (no certificate is ever fabricated) --------------------
    check('plan-parity-demands-migration-not-manifest',
          'manifest' not in plan['parity'] and 'strategy_encounter_migration' in plan['parity']['rule']
          and 'manifest' not in plan['parity']['rule'].split('no arbitrary')[0])
    stub = types.SimpleNamespace(CERTIFICATE='<pinned>',
                                 preview=lambda old, new: dict(eligible=True, planId='plan-abc',
                                                               proof={'certificate': 'h'}))
    real_loader = harness.load_migration_module
    harness.load_migration_module = lambda: stub
    try:
        eligible = harness.migration_gate({'files': {'a': '1'}}, {'files': {'a': '2'}})
    finally:
        harness.load_migration_module = real_loader
    check('migration-gate-honours-preview',
          eligible.get('eligible') is True and eligible.get('planId') == 'plan-abc'
          and eligible.get('certificate') == '<pinned>')
    absent = harness.migration_gate({'files': {'a': '1'}}, {'files': {'a': '2'}})
    check('migration-gate-fails-closed-without-certificate',
          absent.get('eligible') is False and bool(absent.get('certificate'))
          and bool(absent.get('reason')), str(absent.get('reason'))[:80])

    # --- explicit-path canonical observer --------------------------------------------------------
    harness.apply_canonical([], dict(finishPolicy=None))
    check('canonical-observer-loaded-by-explicit-path',
          Path(harness._CANONICAL_OUTCOMES.__file__).resolve()
          == (HERE / 'strategy_outcomes.py').resolve())
    check('explicit-loader-refuses-missing',
          isinstance(refuse(lambda: harness.load_explicit_module('nope', work / 'nope.py')),
                     harness.HarnessError))

    # --- failed-run budget accounting (actual dispatch, never a silent zero) ----------------------
    fake_meter = types.SimpleNamespace(stage='search:legacy:19', cap=10, charged=7, denied=0,
                                       usedPairs=[[1, 2], [3, 4]])
    harness._write_meter_snapshot(work, 'search-e19', 'legacy', fake_meter)
    snapshot_path = work / 'meter-search-e19-legacy.json'
    check('meter-snapshot-persisted',
          snapshot_path.is_file() and harness.read_json(snapshot_path)['admitted'] == 7)
    check('recovered-admitted-conservative-max-across-sources',
          harness._recovered_admitted(snapshot_path, dict(admitted=3), 10) == 7)
    check('recovered-admitted-zero-payload-never-hides-durable-cost',
          harness._recovered_admitted(snapshot_path, dict(admitted=0), 10) == 7)
    check('recovered-admitted-uses-snapshot-on-failure',
          harness._recovered_admitted(snapshot_path, None, 10) == 7)
    check('recovered-admitted-fails-closed-to-cap',
          harness._recovered_admitted(work / 'missing.json', None, 10) == 10)
    check('recovered-admitted-refuses-over-cap-report',
          isinstance(refuse(lambda: harness._recovered_admitted(None, dict(admitted=99), 10)),
                     harness.Blocked))
    stale_zero = work / 'meter-stale-zero.json'
    harness.write_json(stale_zero, dict(admitted=0, denied=0))
    report_four = work / 'report-four.json'
    harness.write_json(report_four, dict(admitted=4))
    check('recovered-admitted-max-across-stale-meter-report-and-ea-session',
          harness._recovered_admitted(stale_zero, dict(admitted=0), 128, report_path=report_four,
                                      extra_counts=(4, 4)) == 4
          and harness._recovered_admitted(stale_zero, None, 128, report_path=report_four) == 4)

    # --- new arm counts its ea_* ledger, finite purposes sum to the transport cap -----------------
    check('purposes-finite-total',
          harness._purposes_for(128) == dict(improvement=128, boundary=0, support=0, comparison=0,
                                             exploration=0)
          and sum(harness._purposes_for(128).values()) == 128)
    check('ea-ledger-missing-is-none', harness._ea_ledger_counts(work / 'no.sqlite') is None)
    ea_path = work / 'ea.sqlite'
    ea = sqlite3.connect(str(ea_path))
    ea.executescript(
        "CREATE TABLE ea_experiment_budget(experiment_id TEXT, total INT, reserved INT, "
        "completed INT);"
        "CREATE TABLE ea_sample_link(seed_a INT, seed_b INT, experiment_id TEXT);"
        "INSERT INTO ea_experiment_budget VALUES('x', 128, 4, 120);"
        "INSERT INTO ea_sample_link VALUES(1, 2, 'x');")
    ea.commit()
    ea.close()
    ea_counts = harness._ea_ledger_counts(ea_path)
    check('ea-ledger-reads-budget-and-samples',
          ea_counts['ea_experiment_budget']['completed'] == 120
          and ea_counts['ea_experiment_budget']['reserved'] == 4
          and ea_counts['ea_sample_link'] == 1)

    # --- holdout rows must also be native before the parent scores them --------------------------
    check('holdout-eval-refuses-fallback',
          isinstance(refuse(lambda: harness.evaluate_holdout(
              plan, 'legacy', work, 19, 1, [(1, 2)], 'n',
              [dict(candidateId='n', encounterId=19, seeds=[1, 2],
                    raw=dict(resultBackend='python'))])), harness.Blocked))

    # --- throughput: matched fixed workload through the real persistence paths -------------------
    import strategy_optimizer as _so  # noqa: E402
    import strategy_optimizer_adapter as _sa  # noqa: E402
    import strategy_experiment_store as _ledger  # noqa: E402
    from strategy_encounter_evaluation import Evaluator as _Evaluator  # noqa: E402

    def _raw(pair):
        return dict(seeds=list(pair), resultBackend='native', cpuSeconds=0.25, elapsedSeconds=0.5,
                    verdict=1, censored=False, resourceUses=0, survivors=6, ticks=100,
                    prizeCallbacks=2,
                    rewardOutcome=dict(pendingChests=2, awardedChests=2,
                                       awardedBasis='reward-entitlement-certificate'))

    class _Future:
        def __init__(self, value):
            self._value = value

        def done(self):
            return True

        def result(self):
            return self._value

    class _RecordingPool:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs
            self.processes = []
            self.submitted = []

        def submit(self, function, scenario, seeds):
            self.submitted.append(list(seeds))
            return _Future(_raw(seeds))

        def submit_batch(self, function, scenario, seed_pairs):
            return _Future([self.submit(function, scenario, pair).result() for pair in seed_pairs])

        def shutdown(self, wait=True, cancel_futures=False):
            pass

        def terminate_workers(self):
            pass

    def _legacy_pool(max_workers, **kwargs):
        if kwargs:
            raise TypeError('the frozen legacy HeadlessPool takes no telemetry kwarg')
        return _RecordingPool(max_workers)

    copy_a = work / 'workload-legacy.sqlite'
    copy_b = work / 'workload-new.sqlite'
    shutil.copyfile(baseline, copy_a)
    shutil.copyfile(baseline, copy_b)
    runtime_store = dict(optimizer=_so, adapter=_sa,
                         fast=types.SimpleNamespace(HeadlessPool=_legacy_pool))
    runtime_ledger = dict(optimizer=_so, adapter=_sa,
                          fast=types.SimpleNamespace(HeadlessPool=_RecordingPool),
                          ledger=_ledger, evaluator=_Evaluator)
    store_payload = harness._matched_dispatch(
        plan, harness.ARM_LEGACY, str(HERE), copy_a, work, harness.ExecutionMeter(8, stage='mock'),
        2, 8, encounter_id=19, mode='legacy-dispatch', runtime=runtime_store)
    ledger_payload = harness._matched_dispatch(
        plan, harness.ARM_NEW, str(HERE), copy_b, work, harness.ExecutionMeter(8, stage='mock'),
        2, 8, encounter_id=19, mode='new-dispatch', runtime=runtime_ledger)
    check('throughput-matched-same-workload',
          store_payload['pairsDigest'] == ledger_payload['pairsDigest']
          and store_payload['scenarioDigest'] == ledger_payload['scenarioDigest']
          and store_payload['nextOrdinal'] == ledger_payload['nextOrdinal']
          and store_payload['battles'] == ledger_payload['battles'] == 8
          and store_payload['workerConcurrency'] == ledger_payload['workerConcurrency'] == 2)
    check('throughput-matched-counts-exact',
          store_payload['admitted'] == 8 and ledger_payload['admitted'] == 8)
    _store = _so.Store(copy_a, _sa.provenance())
    try:
        stored_runs = _store.db.execute(
            "SELECT COUNT(*) FROM run WHERE candidate='c0' AND phase='validation'").fetchone()[0]
    finally:
        _store.close()
    check('throughput-store-records-real-ordinals', stored_runs == 8)
    _db = sqlite3.connect(str(copy_b))
    try:
        links = _db.execute('SELECT COUNT(*) FROM ea_sample_link').fetchone()[0]
        samples = _db.execute('SELECT COUNT(*) FROM ea_sample').fetchone()[0]
    finally:
        _db.close()
    check('throughput-ledger-persists-ea-samples', links == 8 and samples == 8)
    check('throughput-stage-timings-separated',
          set(store_payload['stages']) == {'preparation', 'dispatch', 'persistence', 'analysis'}
          and store_payload['stages']['persistence']['source'] == 'legacy Store.record'
          and ledger_payload['stages']['persistence']['source'] == 'current Evaluator + ea ledger')
    check('throughput-legacy-pool-refuses-telemetry-kwarg',
          'telemetry=False' not in json.dumps(store_payload.get('counts', {}))
          and store_payload['admitted'] == 8)

    check('worker-cpu-refuses-elapsed-fallback',
          harness._worker_cpu_reading([dict(raw=dict(elapsedSeconds=9.0))])['seconds'] is None
          and harness._worker_cpu_reading([dict(raw=dict(elapsedSeconds=9.0))])['missing'] == 1)
    check('worker-cpu-sums-actual-seconds',
          harness._worker_cpu_reading([dict(raw=dict(cpuSeconds=0.5)),
                                       dict(raw=dict(cpuSeconds=0.25))])['seconds'] == 0.75)

    # --- observer-only cost: wall/parent CPU + real worker CPU only, missing counted never zeroed --
    observer = harness._observer_cost(
        wall_seconds=3.5, parent_cpu_seconds=1.25,
        raw_rows=[dict(raw=dict(cpuSeconds=0.5)), dict(raw=dict(cpuSeconds=0.25))],
        stages=dict(preparation=dict(wall=0.4, parentCpu=0.1)),
        runtime_counters=harness._runtime_phase_counters(
            dict(scheduler=dict(proposalSeconds=0.0, planSeconds=0.2))))
    check('observer-cost-scope-and-shape',
          observer['schema'] == harness.OBSERVER_COST_SCHEMA
          and observer['wallSeconds'] == 3.5 and observer['parentCpuSeconds'] == 1.25
          and observer['workerCpuSeconds'] == 0.75
          and observer['workerCpuSource'] == 'raw-cpuSeconds'
          and observer['workerCpuMissing'] == 0
          and 'never a learning feature' in observer['scope']
          and observer['stages']['preparation']['wall'] == 0.4)
    missing_worker = harness._observer_cost(wall_seconds=1.0, parent_cpu_seconds=0.5,
                                            raw_rows=[dict(raw=dict(elapsedSeconds=9.0))])
    check('observer-cost-missing-worker-never-zero',
          missing_worker['workerCpuSeconds'] is None and missing_worker['workerCpuMissing'] == 1
          and missing_worker['workerCpuSource'] == 'unavailable')
    check('observer-cost-owned-process-fallback-and-unavailable',
          harness._observer_cost(wall_seconds=1.0, parent_cpu_seconds=0.5, raw_rows=[],
                                 process_cpu=dict(seconds=0.9, readings=2, processes=2)
                                 )['workerCpuSource'] == 'owned-process-GetProcessTimes'
          and harness._observer_cost(wall_seconds=1.0, parent_cpu_seconds=0.5,
                                     raw_rows=[])['workerCpuSeconds'] is None
          and harness._observer_cost(wall_seconds=1.0, parent_cpu_seconds=0.5,
                                     raw_rows=[])['workerCpuSource'] == 'unavailable')
    check('observer-cost-retains-published-counters-never-fabricates',
          harness._runtime_phase_counters(dict(scheduler=dict(proposalSeconds=0.0,
                                                              coordinatorBusySeconds=0.3)))
          == dict(proposalSeconds=0.0, coordinatorBusySeconds=0.3)
          and harness._runtime_phase_counters(dict(status='Running')) is None
          and 'publishSeconds' not in (harness._runtime_phase_counters(
              dict(scheduler=dict(proposalSeconds=0.0))) or {}))
    check('search-and-holdout-carry-observer-cost',
          "report['observerCost'] = _observer_cost(" in inspect.getsource(harness.search_stage)
          and "report['observerCost'] = _observer_cost(" in inspect.getsource(harness.holdout_stage)
          and 'never a learning feature' in inspect.getsource(harness._observer_cost))

    telemetry_pools = []

    def _telemetry_pool(*args, **kwargs):
        pool = _RecordingPool(*args, **kwargs)
        telemetry_pools.append(pool)
        return pool

    runtime_telemetry = dict(
        optimizer=types.SimpleNamespace(
            baseline_scenarios=lambda base: [(dict(encounterId=19), 'a'),
                                             (dict(encounterId=18), 'b')]),
        adapter=types.SimpleNamespace(default_scenario=lambda: dict(finishPolicy='on-verdict')),
        fast=types.SimpleNamespace(HeadlessPool=_telemetry_pool))
    off = harness._telemetry_throughput(plan, harness.ARM_NEW, str(HERE), work,
                                        harness.ExecutionMeter(4, stage='mock'), 2,
                                        'telemetry-off', 4, runtime=runtime_telemetry)
    on = harness._telemetry_throughput(plan, harness.ARM_NEW, str(HERE), work,
                                       harness.ExecutionMeter(4, stage='mock'), 2,
                                       'telemetry-on', 4, runtime=runtime_telemetry)
    check('throughput-telemetry-toggle-is-pool-flag',
          telemetry_pools[0].kwargs.get('telemetry') is False
          and telemetry_pools[1].kwargs.get('telemetry') is True
          and off['admitted'] == on['admitted'] == 4)
    check('throughput-arm-selection',
          harness._throughput_arm('telemetry-off') == harness.ARM_NEW
          and harness._throughput_arm('telemetry-on') == harness.ARM_NEW
          and harness._throughput_arm('legacy-dispatch') == harness.ARM_LEGACY
          and harness._throughput_arm('new-dispatch') == harness.ARM_NEW)
    check('throughput-modes-declared',
          plan['throughput']['modes'] == list(harness.THROUGHPUT_MODES)
          and plan['throughput']['matchedModes'] == list(harness.MATCHED_MODES))
    check('throughput-matched-requires-copy',
          isinstance(refuse(lambda: harness.throughput_stage(
              plan, harness.ARM_LEGACY, str(HERE), work, harness.ExecutionMeter(2, stage='mock'),
              2, 'legacy-dispatch', 2, copy_path=None)), harness.Blocked))
    matched = harness.matched_infrastructure_comparison(
        [dict(throughput=store_payload), dict(throughput=ledger_payload)])
    mismatched = harness.matched_infrastructure_comparison(
        [dict(throughput=store_payload),
         dict(throughput=dict(ledger_payload, pairsDigest='different'))])
    check('infrastructure-comparison-refuses-different-work',
          matched['compared'] is True and matched['matched'] is True
          and mismatched['compared'] is False and 'not the same work' in mismatched['reason'])
    check('infrastructure-comparison-labels-wall-metric',
          matched.get('metric') == 'wall' and 'wallRatio' in matched
          and matched.get('cpuOverhead') is None
          and 'legacyParentCpuSeconds' in matched and 'parent CPU' in matched.get('metricNote', ''))
    check('matched-comparison-worker-cpu-unknown-or-real-never-zero',
          harness.matched_infrastructure_comparison(
              [dict(throughput=dict(store_payload, workerCpuSeconds=None)),
               dict(throughput=dict(ledger_payload, workerCpuSeconds=None))]
          ).get('legacyWorkerCpuSeconds') is None
          and harness.matched_infrastructure_comparison(
              [dict(throughput=dict(store_payload, workerCpuSeconds=0.8)),
               dict(throughput=dict(ledger_payload, workerCpuSeconds=0.4))]
          ).get('legacyWorkerCpuSeconds') == 0.8)

    # --- holdout freshness is fail-closed and covers EVERY candidate on both copies --------------
    fresh_a = work / 'fresh-a.sqlite'
    fresh_b = work / 'fresh-b.sqlite'
    shutil.copyfile(baseline, fresh_a)
    shutil.copyfile(baseline, fresh_b)
    _db = sqlite3.connect(str(fresh_b))
    _db.executescript(
        "CREATE TABLE ea_sample_link(candidate_id TEXT, seed_a INT, seed_b INT, experiment_id TEXT);"
        "CREATE TABLE ea_holdout(candidate_id TEXT, seed_a INT, seed_b INT, experiment_id TEXT);"
        "INSERT INTO ea_sample_link VALUES('c0', 7, 7, 'x');"
        "INSERT INTO ea_holdout VALUES('c1', 6, 6, 'x');"
        # An ELIMINATED candidate that is not a nominee must still reserve its tuning pair.
        "INSERT INTO ea_sample_link VALUES('eliminated', 9, 9, 'x');")
    _db.commit()
    _db.close()
    # A high deterministic ordinal for a NON-nominee candidate must extend the global bank too.
    _db = sqlite3.connect(str(fresh_a))
    _db.execute("INSERT INTO run VALUES('eliminated','validation',100000,'{}')")
    _db.commit()
    _db.close()
    reserved = harness._union_reserved({'legacy': fresh_a, 'new': fresh_b}, [(5, 5)],
                                       issued_pairs=[(4, 4)])
    bank = tuple(_so.seed_pair('validation', 0))
    high = tuple(_so.seed_pair('validation', 100000))
    check('union-reserved-includes-transport-issued-and-ea',
          (5, 5) in reserved and (4, 4) in reserved and (7, 7) in reserved and (6, 6) in reserved)
    check('union-reserved-includes-eliminated-candidate-ea', (9, 9) in reserved)
    check('union-reserved-global-bank-covers-any-candidate', high in reserved and bank in reserved)
    check('union-reserved-fails-closed-on-missing-copy',
          isinstance(refuse(lambda: harness._union_reserved(
              {'legacy': work / 'nope.sqlite', 'new': fresh_b}, [])), Exception))
    # Malformed compact seed evidence must ABORT, never yield a partial reserved set.
    bad_evidence = work / 'bad-evidence.sqlite'
    shutil.copyfile(baseline, bad_evidence)
    _db = sqlite3.connect(str(bad_evidence))
    _db.execute("INSERT INTO evidence(candidate,phase,ordinal,seeds) "
                "VALUES('c0','validation',0,'not json')")
    _db.commit()
    _db.close()
    check('union-reserved-fails-closed-on-malformed-evidence',
          isinstance(refuse(lambda: harness._union_reserved(
              {'legacy': bad_evidence, 'new': fresh_b}, [])), harness.Blocked))
    # The deterministic bank is cached by (phase, limit), so it is built once, not per copy.
    harness._DETERMINISTIC_BANK_CACHE.clear()
    harness._FORBIDDEN_BANK_CACHE.clear()
    harness._union_reserved({'legacy': fresh_a, 'new': fresh_b}, [])
    builds = len(harness._DETERMINISTIC_BANK_CACHE)
    harness._union_reserved({'legacy': fresh_a, 'new': fresh_b}, [])
    check('union-reserved-caches-deterministic-bank',
          builds >= 1 and len(harness._DETERMINISTIC_BANK_CACHE) == builds)

    # --- per-unit copies are unique and cleanup is ownership-scoped (never the baseline) ----------
    unit_root = work / 'unit-copies'
    unit_root.mkdir(parents=True, exist_ok=True)
    dir_a = harness.unit_copy_dir(unit_root, 'legacy', 19, 1)
    dir_b = harness.unit_copy_dir(unit_root, 'legacy', 18, 1)
    check('unit-copy-dirs-are-per-encounter',
          dir_a != dir_b and dir_a.name == 'legacy-e19-r1' and dir_b.name == 'legacy-e18-r1')
    owned, made = [], []
    for index, directory in enumerate((dir_a, dir_b)):
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / harness.UNIT_COPY_NAME
        path.write_bytes(('copy%d' % index).encode())
        harness.register_copy(owned, path, root=unit_root)
        made.append(path)
    check('register-refuses-foreign-name',
          isinstance(refuse(lambda: harness.register_copy(owned, unit_root / 'baseline.sqlite',
                                                          root=unit_root)), harness.HarnessError))
    check('register-refuses-outside-root',
          isinstance(refuse(lambda: harness.register_copy(
              owned, work / 'outside' / harness.UNIT_COPY_NAME, root=unit_root)),
              harness.HarnessError))
    outside = work / 'not-owned.sqlite'
    outside.write_bytes(b'leave me')
    harness.cleanup_copies(owned, [outside], root=unit_root)
    check('cleanup-leaves-unowned-files', outside.is_file() and len(owned) == 2)
    harness.cleanup_copies(owned, [made[0]], root=unit_root)
    check('cleanup-deletes-owned-copy', not made[0].exists() and made[1].exists() and len(owned) == 1)
    # holdout-only must refuse loudly without a persisted manifest, never emit an empty comparison
    check('holdout-only-without-manifest-refuses',
          isinstance(refuse(lambda: harness._persisted_search(
              unit_root / 'empty', 'legacy', 19, 1,
              copy_path=unit_root / 'empty' / harness.UNIT_COPY_NAME)), harness.Blocked))
    persisted_dir = unit_root / 'persisted'
    persisted_dir.mkdir(parents=True, exist_ok=True)
    harness.write_json(persisted_dir / 'search-legacy-e19-r1.json',
                       dict(nomination=dict(nominee='sha-x'), usedPairs=[[1, 2]]))
    check('holdout-only-resumes-persisted-manifest',
          harness._persisted_search(persisted_dir, 'legacy', 19, 1)['nomination']['nominee']
          == 'sha-x')
    check('holdout-only-refuses-missing-copy',
          isinstance(refuse(lambda: harness._persisted_search(
              persisted_dir, 'legacy', 19, 1,
              copy_path=persisted_dir / harness.UNIT_COPY_NAME)), harness.Blocked))
    harness.cleanup_copies(owned, list(owned), root=unit_root)
    check('cleanup-removes-all-owned-copies', not made[1].exists() and not owned)

    # --- holdout is nominee-only; reference is never re-simulated inside an arm -------------------
    check('holdout-evaluates-only-nominee',
          'reference' not in inspect.signature(harness.holdout_stage).parameters
          and 'reference' not in inspect.signature(harness.evaluate_holdout).parameters
          and plan['holdout'].get('nomineeOnly') is True
          and 'pairCandidates' not in plan['holdout'])
    holdout_budget = harness.build_budget(4, 3)['stages']['holdout']
    check('holdout-budget-nominee-only',
          holdout_budget['battlesPerArmPerEncounterPerReplicate'] == 64
          and holdout_budget['nomineeOnly'] is True and holdout_budget['battles'] == 1536)

    # --- the canonical comparison consumes the holdout units run_units actually appends ----------
    def _holdout_rows(candidate_id, earned):
        rows = []
        for index in range(4):
            pair = [11 + index, 12 + index]
            raw = dict(_raw(pair), rewardOutcome=dict(
                pendingChests=earned, awardedChests=earned,
                awardedBasis='reward-entitlement-certificate'))
            rows.append(dict(candidateId=candidate_id, encounterId=19, seeds=pair, raw=raw))
        return rows

    pairs4 = [(11 + index, 12 + index) for index in range(4)]
    holdout_units = [
        harness.evaluate_holdout(plan, harness.ARM_LEGACY, work, 19, 1, pairs4,
                                 harness.ARM_LEGACY, _holdout_rows(harness.ARM_LEGACY, 2)),
        harness.evaluate_holdout(plan, harness.ARM_NEW, work, 19, 1, pairs4,
                                 harness.ARM_NEW, _holdout_rows(harness.ARM_NEW, 3)),
    ]
    comparison = harness.cross_arm_summary(holdout_units, plan)
    check('canonical-comparison-consumes-holdout-units',
          comparison['comparisons'] == 1
          and comparison['diagnostics'][0]['verdict'] == 'new-favoured'
          and comparison['diagnostics'][0]['newNominee'] == harness.ARM_NEW
          and comparison['diagnostics'][0]['legacyNominee'] == harness.ARM_LEGACY
          and comparison['diagnostics'][0]['identicalNominee'] is False
          and comparison['newFavoured'] == 1 and comparison['legacyFavoured'] == 0)
    shared_units = [
        harness.evaluate_holdout(plan, harness.ARM_LEGACY, work, 19, 2, pairs4, 'shared',
                                 _holdout_rows('shared', 2)),
        harness.evaluate_holdout(plan, harness.ARM_NEW, work, 19, 2, pairs4, 'shared',
                                 _holdout_rows('shared', 2)),
    ]
    check('canonical-comparison-flags-identical-nominee',
          harness.cross_arm_summary(shared_units, plan)['identicalNomineeBuilds'] == 1)
    check('canonical-comparison-direction-is-new-minus-legacy',
          comparison['diagnostics'][0]['paired']['mean'] == 1.0)
    check('canonical-comparison-ignores-non-holdout-units',
          harness.cross_arm_summary([dict(arm='x', stage='smoke'),
                                     dict(arm='x', stage='search', search={})],
                                    plan)['comparisons'] == 0)
    boundary_findings = harness._boundaries([
        dict(arm='x', stage='search',
             search=dict(incumbentTimeline=dict(
                 boundaries=[dict(testedPoints=[1, 2], unresolvedGaps=[])])))])
    check('boundaries-read-from-search-unit',
          boundary_findings['findings'] == [dict(arm='x', testedPoints=[1, 2], unresolvedGaps=[])])

    # --- a shared raw-Optimizer stand-in that publishes a preview like the live coordinator ------
    def _fake_preview(config=None, plan_id=None):
        cfg = dict(version=1, mode='community-first', allocations={'community': 1.0},
                   purposes={'improvement': 0, 'boundary': 0, 'support': 0, 'comparison': 0,
                             'exploration': 0})
        if config:
            cfg.update(config)
        migration = dict(eligible=True, required=plan_id is not None)
        if plan_id is not None:
            migration['planId'] = plan_id
        return dict(config=cfg, purposes=dict(cfg['purposes']), simulatorMigration=migration)

    class FakeEngine:
        NAMES = dict(community='community', rebel='rebel', stumble='stumble', average='average',
                     mechanism='mechanism', discovery='discovery')

        def __init__(self, path, **overrides):
            self.path = path
            self.commands = []
            self._state = overrides.get('state', 'Paused')
            self._enabled = overrides.get('enabled', True)
            self._scheduler = overrides.get('scheduler', {})
            self._pending = overrides.get('pending')
            self._total = overrides.get('total_runs', 0)
            self._activation_error = overrides.get('activation_error')
            self._status_error = overrides.get('status_error')
            self._names = overrides.get('names', dict(self.NAMES))
            self._shares = overrides.get('shares', {name: 0.3 for name in self.NAMES.values()})
            self._publish_preview = overrides.get('publish_preview', True)
            self._preview = overrides.get('preview') or _fake_preview()
            # The coordinator's own encounter-aware progress view. Default: explicitly idle with an
            # unspendable budget, so the explicit-idle path is exercised by the existing tests; a
            # search that is still analysing publishes idle=False (see SlowAnalysisEngine below).
            self._progress = overrides.get('progress', dict(idle=True))
            self.preview = self._preview

        def command(self, name, value=None, wait=True):
            self.commands.append((name, value))
            if name == 'encounter_activate' and self._activation_error:
                return dict(ok=True, error=self._activation_error)
            if name == 'students' and isinstance(value, dict) and isinstance(value.get('shares'), dict):
                requested = dict(value['shares'])
                total = sum(float(v) for v in requested.values())
                self._shares = ({k: float(v) / total for k, v in requested.items()}
                                if total > 0 else dict(self._shares))
            return dict(ok=True)

        def status(self):
            aware = dict(enabled=self._enabled, progress=dict(self._progress))
            if self._publish_preview and any(name == 'encounter_preview'
                                             for name, _ in self.commands):
                aware['migrationPreview'] = self._preview
            return dict(state=self._state, error=self._status_error, totalRuns=self._total,
                        scheduler=dict(self._scheduler), pending=self._pending,
                        encounterAware=aware, focusPortfolio=None, rankOne=None,
                        students=dict(names=dict(self._names), shares=dict(self._shares)))

    class SlowAnalysisEngine(FakeEngine):
        """A new-arm search whose analysis is still running, then dispatches its admitted work.

        For the first ``charge_after`` polls the coordinator publishes ``idle=False`` with no current
        experiment and no pending work - exactly the live slow-startup shape - and only then charges
        the shared meter. It must never be declared idle during that window.
        """

        def __init__(self, path, meter, *, charge_after=4, amount=2):
            super().__init__(path, state='Running',
                             progress=dict(idle=False, current=None, queueLength=0,
                                           unspendable=[], unspendableTasks=[], experiments=0))
            self._meter = meter
            self._charge_after = charge_after
            self._amount = amount
            self._polls = 0

        def status(self):
            self._polls += 1
            if self._polls == self._charge_after:
                for _ in range(self._amount):
                    self._meter.admit(1)
            return super().status()

    class CleanupDispatchEngine:
        """An engine whose close() flushes late dispatches through the still-installed wrapper."""

        def __init__(self, path, dispatch):
            self.path = path
            self._dispatch = dispatch
            self.commands = []
            self.thread = types.SimpleNamespace(is_alive=lambda: False)

        def command(self, name, value=None, wait=True):
            self.commands.append(name)
            if name == 'close':
                self._dispatch()
            return dict(ok=True)

        def status(self):
            return dict(state='Stopped', error=None, totalRuns=0, scheduler={})

    def _issued(engine, name):
        for command, value in engine.commands:
            if command == name:
                return value
        return None

    # --- activation error is fatal even when the coordinator mode is already enabled -------------
    check('new-arm-activation-error-fatal-when-enabled',
          isinstance(refuse(lambda: harness._run_encounter(
              FakeEngine(baseline, activation_error='activation refused'), harness.ARM_NEW, 19,
              harness.ExecutionMeter(0, stage='mock'), 1, dict(encounters=[], errors=[]),
              time.monotonic() + 5, [])), harness.Blocked))

    # --- an explicitly-idle, unspendable budget is INCOMPLETE, not a three-hour loop -------------
    idle_report = dict(encounters=[], errors=[])
    harness._run_encounter(FakeEngine(baseline, state='Running'), harness.ARM_NEW, 19,
                           harness.ExecutionMeter(2, stage='mock'), 1, idle_report,
                           time.monotonic() + 30, [], poll_interval=0.0, idle_polls=3,
                           idle_grace_seconds=0.0)
    check('run-encounter-idle-budget-is-incomplete',
          bool(idle_report['encounters'])
          and idle_report['encounters'][0]['incomplete'] is True
          and idle_report['encounters'][0]['admitted'] == 0
          and 'coordinator' in (idle_report['encounters'][0]['reason'] or ''))

    # --- a slow first analysis is NOT a stall: explicit idle is required AND only after the grace --
    slow_meter = harness.ExecutionMeter(2, stage='slow')
    slow_report = dict(encounters=[], errors=[])
    slow_engine = SlowAnalysisEngine(baseline, slow_meter, charge_after=5, amount=2)
    harness._run_encounter(slow_engine, harness.ARM_NEW, 19, slow_meter, 1, slow_report,
                           time.monotonic() + 30, [], poll_interval=0.0, idle_polls=2,
                           idle_grace_seconds=0.0)
    check('run-encounter-slow-analysis-is-not-idle',
          slow_engine._polls >= 5
          and slow_report['errors'] == []
          and slow_report['encounters'][0]['incomplete'] is False
          and slow_report['encounters'][0]['admitted'] == 2)

    # During the grace window even an explicit idle flag is ignored; only the absolute deadline ends it.
    grace_report = dict(encounters=[], errors=[])
    harness._run_encounter(FakeEngine(baseline, state='Running'), harness.ARM_NEW, 19,
                           harness.ExecutionMeter(2, stage='mock'), 1, grace_report,
                           time.monotonic() + 0.3, [], poll_interval=0.0, idle_polls=1,
                           idle_grace_seconds=30.0)
    check('run-encounter-idle-grace-defers-stall',
          grace_report['errors'] == []
          and grace_report['encounters'][0]['incomplete'] is True
          and 'deadline' in (grace_report['encounters'][0]['reason'] or ''))

    # A coordinator that reports idle with an empty budget but no explicit signal is not a stall.
    nosignal_report = dict(encounters=[], errors=[])
    harness._run_encounter(FakeEngine(baseline, state='Running', progress={}), harness.ARM_NEW, 19,
                           harness.ExecutionMeter(2, stage='mock'), 1, nosignal_report,
                           time.monotonic() + 0.3, [], poll_interval=0.0, idle_polls=1,
                           idle_grace_seconds=0.0)
    check('run-encounter-absence-of-progress-is-not-a-stall',
          nosignal_report['errors'] == []
          and 'deadline' in (nosignal_report['encounters'][0]['reason'] or ''))

    check('pending-count-reads-scheduler-view',
          harness._pending_count(dict(state='Running', scheduler=dict(pending=2))) == 2
          and harness._pending_count(dict(state='Running', pending=0, scheduler=dict(pending=2))) == 0)

    busy_report = dict(encounters=[], errors=[])
    harness._run_encounter(FakeEngine(baseline, state='Running', scheduler=dict(pending=1, busy=1)),
                           harness.ARM_NEW, 19, harness.ExecutionMeter(2, stage='mock'), 1,
                           busy_report, time.monotonic() + 0.25, [], poll_interval=0.0, idle_polls=3)
    check('run-encounter-busy-pool-is-not-called-idle',
          busy_report['encounters'][0]['incomplete'] is True and busy_report['errors'] == [])

    class DrainedEngine:
        def __init__(self, path):
            self.path = path

        def status(self):
            return dict(state='Paused', error=None, totalRuns=5, scheduler=dict(pending=0))

    check('drain-returns-when-scheduler-pending-is-zero',
          harness._drain(DrainedEngine(baseline), time.monotonic() + 5) is True)

    # --- the real horizon/policy comes from the frozen meta, not a censored default --------------
    check('coerce-mapping-handles-double-encoded',
          harness._coerce_mapping('{"a": 1}') == dict(a=1)
          and harness._coerce_mapping(json.dumps(json.dumps(dict(a=1)))) == dict(a=1)
          and harness._coerce_mapping('not json') is None)

    # --- real driver callpath: new arm PREVIEWS then activates; legacy arm only focuses ----------
    new_engine = FakeEngine(baseline, preview=_fake_preview(plan_id='plan-abc'))
    harness._run_encounter(new_engine, harness.ARM_NEW, 19,
                           harness.ExecutionMeter(0, stage='mock'), 1, dict(encounters=[],
                           errors=[]), time.monotonic() + 5, [], migration_plan_id='plan-abc')
    names = [name for name, _ in new_engine.commands]
    activation = _issued(new_engine, 'encounter_activate') or {}
    check('new-arm-preview-before-activate',
          'encounter_preview' in names and 'encounter_activate' in names
          and names.index('encounter_preview') < names.index('encounter_activate'))
    check('new-arm-activates-not-focuses',
          'encounter_activate' in names and 'focus_encounter' not in names)
    check('new-arm-activation-uses-previewed-config-and-scope',
          activation.get('migrationPlanId') == 'plan-abc'
          and activation.get('config') == new_engine.preview['config']
          and activation.get('mode') == 'community-first'
          and activation.get('encounter') == 19 and 'reference' in activation
          and activation.get('purposes', {}).get('improvement') == 0)
    preview_request = _issued(new_engine, 'encounter_preview') or {}
    check('preview-requests-exact-scope',
          preview_request.get('mode') == 'community-first'
          and preview_request.get('encounter') == 19
          and 'reference' in preview_request
          and preview_request.get('purposes') == activation.get('purposes'))
    check('preview-without-config-fails-closed',
          isinstance(refuse(lambda: harness._run_encounter(
              FakeEngine(baseline, publish_preview=False), harness.ARM_NEW, 19,
              harness.ExecutionMeter(0, stage='mock'), 1, dict(encounters=[], errors=[]),
              time.monotonic() + 5, [])), harness.Blocked))
    check('preview-plan-mismatch-fails-closed',
          isinstance(refuse(lambda: harness._run_encounter(
              FakeEngine(baseline, preview=_fake_preview(plan_id='other')), harness.ARM_NEW, 19,
              harness.ExecutionMeter(0, stage='mock'), 1, dict(encounters=[], errors=[]),
              time.monotonic() + 5, [], migration_plan_id='plan-abc')), harness.Blocked))
    legacy_engine = FakeEngine(baseline)
    harness._run_encounter(legacy_engine, harness.ARM_LEGACY, 19,
                           harness.ExecutionMeter(0, stage='mock'), 1, dict(encounters=[],
                           errors=[]), time.monotonic() + 5, [])
    check('legacy-arm-uses-focus-not-activate',
          'focus_encounter' in [n for n, _ in legacy_engine.commands]
          and 'encounter_activate' not in [n for n, _ in legacy_engine.commands])

    # --- the legacy arm is pinned to the SAME allowed space as community-first (Community 1) -----
    students_cmd = _issued(legacy_engine, 'students') or {}
    shares = students_cmd.get('shares') or {}
    check('legacy-arm-configures-community-only-students',
          shares.get('community') == 1.0
          and all(value == 0.0 for name, value in shares.items() if name != 'community')
          and legacy_engine._shares.get('community') == 1.0
          and all(legacy_engine._shares.get(name, 0.0) == 0.0
                  for name in FakeEngine.NAMES.values() if name != 'community'))
    check('legacy-arm-refuses-without-registered-names',
          isinstance(refuse(lambda: harness._run_encounter(
              FakeEngine(baseline, names={}), harness.ARM_LEGACY, 19,
              harness.ExecutionMeter(0, stage='mock'), 1, dict(encounters=[], errors=[]),
              time.monotonic() + 5, [])), harness.Blocked))

    # --- a failed or underexecuted encounter is marked, never a silent complete ------------------
    failed_report = dict(encounters=[], errors=[])
    harness._run_encounter(FakeEngine(baseline, state='Running', status_error='boom'),
                           harness.ARM_LEGACY, 19, harness.ExecutionMeter(2, stage='mock'), 1,
                           failed_report, time.monotonic() + 5, [], poll_interval=0.0, idle_polls=3)
    check('run-encounter-error-marks-failed',
          failed_report['encounters'][0]['failed'] is True
          and failed_report['encounters'][0]['incomplete'] is True)
    stopped_report = dict(encounters=[], errors=[])
    harness._run_encounter(FakeEngine(baseline, state='Stopped'), harness.ARM_LEGACY, 19,
                           harness.ExecutionMeter(2, stage='mock'), 1, stopped_report,
                           time.monotonic() + 5, [], poll_interval=0.0, idle_polls=3)
    check('run-encounter-underexecution-is-incomplete',
          stopped_report['encounters'][0]['incomplete'] is True
          and stopped_report['encounters'][0]['underexecuted'] is True
          and stopped_report['encounters'][0]['admitted'] == 0)

    # --- matched search equality: any underexecution/inequality is a problem ---------------------
    equal = {arm: dict(declared=128, admitted=128) for arm in harness.ARMS}
    check('search-unit-equal-is-clean', harness._search_unit_problem(equal) is None)
    check('search-unit-flags-underexecution',
          'underexecution' in (harness._search_unit_problem(
              {harness.ARM_LEGACY: dict(declared=128, admitted=128),
               harness.ARM_NEW: dict(declared=128, admitted=100)}) or ''))
    check('search-unit-flags-unequal',
          harness._search_unit_problem(
              {harness.ARM_LEGACY: dict(declared=128, admitted=128),
               harness.ARM_NEW: dict(declared=128, admitted=120)}) is not None)
    check('search-unit-flags-failed',
          harness._search_unit_problem(
              {harness.ARM_LEGACY: dict(declared=128, admitted=128, failed=True),
               harness.ARM_NEW: dict(declared=128, admitted=128)}) is not None)

    # --- per-arm policy + frozen source inventory are recorded, and tamper is rejected -----------
    for arm in harness.ARMS:
        inv = plan['arms'][arm]['sourceInventory']
        pol = plan['arms'][arm]['policy']
        check(f'plan-freezes-source-inventory-{arm}',
              bool(inv.get('digest')) and bool(inv.get('files')))
        check(f'plan-records-per-arm-policy-{arm}',
              pol.get('finishPolicy') == 'on-verdict' and bool(pol.get('source')))
    pol_tamper = json.loads(json.dumps(plan))
    pol_tamper['arms'][harness.ARM_LEGACY]['policy']['finishPolicy'] = 'not-the-declared-policy'
    check('plan-rejects-policy-mismatch', bool(harness.validate_plan(pol_tamper)))
    src_tamper = json.loads(json.dumps(plan))
    src_tamper['arms'][harness.ARM_NEW]['sourceInventory']['digest'] = 'changed-after-plan'
    check('plan-rejects-unfrozen-source-change', bool(harness.validate_plan(src_tamper)))
    scope_tamper = json.loads(json.dumps(plan))
    scope_tamper['arms'][harness.ARM_NEW]['policy']['scenarioScope']['holyHerbStock'] = 5
    check('plan-rejects-cross-arm-resource-mismatch', bool(harness.validate_plan(scope_tamper)))

    # --- independent implementation inventory + observed-policy study are frozen per arm ---------
    for arm in harness.ARMS:
        impl = plan['arms'][arm]['implementationInventory']
        observed = plan['arms'][arm]['observedPolicy']
        fields = observed.get('fields') or {}
        check(f'plan-freezes-implementation-inventory-{arm}',
              bool(impl.get('digest')) and bool(impl.get('files'))
              and bool(impl.get('observerRevision'))
              and any(name.startswith('native/') for name in impl.get('files') or {}))
        check(f'plan-freezes-observed-policy-{arm}',
              fields.get('finishPolicy') == 'on-verdict'
              and fields.get('tickLimit') == 30000
              and 'startProfile' in fields and fields.get('inputs') == []
              and ((observed.get('referencesByEncounter') or {}).get('19') or {}).get('stored')
              and set(observed.get('referencesByEncounter') or {}) == {'0', '15', '18', '19'}
              and observed.get('searchHorizon') == 30000)
        check(f'plan-observed-policy-from-stored-reference-{arm}',
              'stored' in (observed.get('source') or '')
              and not observed.get('conflicts') and not observed.get('missingEncounters'))
    check('plan-source-inventory-note-corrected',
          'adapter.provenance' in plan['arms'][harness.ARM_NEW]['sourceInventory']['note']
          and 'implementationInventory' in plan['arms'][harness.ARM_NEW]['sourceInventory']['note'])
    impl_tamper = json.loads(json.dumps(plan))
    impl_tamper['arms'][harness.ARM_NEW]['implementationInventory']['digest'] = 'changed-after-plan'
    check('plan-rejects-unfrozen-implementation-change', bool(harness.validate_plan(impl_tamper)))
    obs_tamper = json.loads(json.dumps(plan))
    obs_tamper['arms'][harness.ARM_NEW]['observedPolicy']['fields']['startProfile'] = {'kind': 'x'}
    check('plan-rejects-observed-policy-tamper', bool(harness.validate_plan(obs_tamper)))

    legacy_probe = harness.probe_provenance(PYTHON, str(HERE))
    check('provenance-probe-runs', legacy_probe.get('ok') is True, legacy_probe.get('error', ''))

    # --- the independent inventory resolves the ACTUAL native DLLs under the arm tree -------------
    local_files = harness.implementation_inventory_files(HERE)
    remote_probe = harness.probe_implementation_inventory(PYTHON, str(HERE))
    check('implementation-inventory-probe-runs', remote_probe.get('ok') is True,
          remote_probe.get('error', ''))
    check('implementation-inventory-local-matches-probe',
          remote_probe.get('ok') and local_files.get('files') == remote_probe.get('files'))
    check('implementation-inventory-hashes-native-dlls',
          bool(local_files.get('files', {}).get('native/kernel'))
          and bool(local_files.get('files', {}).get('native/encounter')))
    check('implementation-inventory-covers-coordinator-and-observer',
          bool(local_files['files'].get('impl/strategy_encounter_search.py'))
          and bool(local_files['files'].get('impl/strategy_joint_proposals.py'))
          and bool(local_files['files'].get('impl/strategy_encounter_adviser.py'))
          and bool(harness.observer_inventory().get('digest')))
    # The newly shipped history-summary module is inventoried CONDITIONALLY: the current (new) arm
    # ships it and must record its hash, while a tree without the file records NEITHER a label NOR a
    # spurious missing entry, so the frozen legacy inventory stays byte-identical to before the file
    # existed. Both the in-process inventory and the independent subprocess probe must agree.
    bare_root = work / 'impl-bare'
    bare_root.mkdir(parents=True, exist_ok=True)
    bare_inventory = harness.implementation_inventory_files(bare_root)
    check('implementation-inventory-history-summary-conditional-only',
          'strategy_history_summary.py' not in harness.IMPLEMENTATION_SOURCES
          and harness.CONDITIONAL_IMPLEMENTATION_SOURCES == ('strategy_history_summary.py',))
    check('implementation-inventory-covers-history-summary-when-present',
          local_files['files'].get('impl/strategy_history_summary.py')
          == harness._file_sha256(HERE / 'strategy_history_summary.py')
          and 'impl/strategy_history_summary.py' not in local_files['missing'])
    check('implementation-inventory-omits-absent-history-summary-without-missing',
          'impl/strategy_history_summary.py' not in bare_inventory['files']
          and 'impl/strategy_history_summary.py' not in bare_inventory['missing'])
    check('implementation-inventory-probe-covers-history-summary',
          remote_probe.get('ok') is True
          and remote_probe.get('files', {}).get('impl/strategy_history_summary.py')
          == local_files['files']['impl/strategy_history_summary.py'])
    for arm in harness.ARMS:
        arm_root = plan['arms'][arm]['source']
        arm_policy_probe = harness.probe_arm_policy(
            PYTHON, arm_root, plan['baseline']['path'],
            [row['id'] for row in plan['encounters'] if row.get('id') is not None])
        arm_live = harness._implementation_inventory_record(
            harness.implementation_inventory_files(arm_root),
            canonical_files=(arm_policy_probe.get('files') or {}),
            observer=harness.observer_inventory())
        check(f'implementation-inventory-live-matches-frozen-{arm}',
              not harness.validate_implementation_inventory(
                  plan['arms'][arm]['implementationInventory'], arm_live, arm=arm))

    temp_arm = work / 'impl-arm'
    temp_release = temp_arm / 'native' / 'ka_kernel' / 'target' / 'release'
    temp_release.mkdir(parents=True, exist_ok=True)
    for name in ('ka_abi.py', 'ka_encounter_abi.py'):
        shutil.copyfile(HERE / name, temp_arm / name)
    for name in ('ka_kernel_v9.dll', 'ka_kernel_v9_large.dll', 'ka_kernel.dll',
                 'ka_kernel_encounter_v3.dll', 'ka_kernel_encounter_v3_large.dll'):
        source = HERE / 'native' / 'ka_kernel' / 'target' / 'release' / name
        if source.is_file():
            shutil.copyfile(source, temp_release / name)
    coordinator = temp_arm / 'strategy_encounter_search.py'
    coordinator.write_text('COORDINATOR = 1\n', encoding='utf-8')

    def _temp_inventory():
        return harness._implementation_inventory_record(
            harness.probe_implementation_inventory(PYTHON, str(temp_arm)),
            observer=harness.observer_inventory())

    temp_frozen = _temp_inventory()
    check('implementation-inventory-temp-stable',
          not harness.validate_implementation_inventory(temp_frozen, _temp_inventory(), arm='temp'))
    coordinator.write_text('COORDINATOR = 2\n', encoding='utf-8')
    coordinator_problems = harness.validate_implementation_inventory(
        temp_frozen, _temp_inventory(), arm='temp')
    check('implementation-inventory-detects-coordinator-change',
          any('strategy_encounter_search' in problem for problem in coordinator_problems),
          '; '.join(coordinator_problems))
    temp_frozen_native = _temp_inventory()
    with open(temp_release / 'ka_kernel_v9.dll', 'ab') as handle:
        handle.write(b'x')
    native_problems = harness.validate_implementation_inventory(
        temp_frozen_native, _temp_inventory(), arm='temp')
    check('implementation-inventory-detects-native-asset-change',
          any('native/' in problem for problem in native_problems), '; '.join(native_problems))

    frozen = Path(harness._Args().baseline)
    if frozen.is_file():
        before = (frozen.stat().st_size, frozen.stat().st_mtime_ns)
        real_plan = harness.build_plan(harness._Args(encounters='0,15,18,19'), python=PYTHON)
        harness.write_json(work / 'baseline-integrity-plan.json', real_plan)
        after = (frozen.stat().st_size, frozen.stat().st_mtime_ns)
        check('baseline-untouched-by-write-plan', before == after, f'{before} -> {after}')
        binding = harness.read_baseline_binding(str(frozen), counts=False)
        check('baseline-stored-digest-present',
              binding.get('storedProvenanceDigest') ==
              '8a24c862670c7c143a90e762d25f7020ef921c3ed78fcfafb3e1121fecbfb630',
              str(binding.get('storedProvenanceDigest')))
        scope_rec = harness.read_scope_record(binding)
        check('scope-read-from-frozen-meta-not-default',
              scope_rec['tickLimit']['value'] == 30000
              and scope_rec['tickLimit']['source'] == 'meta:scope'
              and scope_rec['finishPolicy']['value'] == 'on-verdict'
              and scope_rec['holyHerbStock']['value'] == 0,
              str(scope_rec['tickLimit']))
        # BOTH arm subprocesses read the SAME frozen supplied references read-only and observe the
        # stored 30000 policy the search actually dispatches; the adapter's 3000 default is never it.
        real_observed = {}
        for arm in harness.ARMS:
            probe = harness.probe_arm_policy(PYTHON, plan['arms'][arm]['source'], str(frozen),
                                             [0, 15, 18, 19])
            observed = probe.get('observed') or {}
            real_observed[arm] = observed
            check(f'real-baseline-arm-observes-stored-30000-{arm}',
                  probe.get('ok') is True and observed.get('tickLimit') == 30000
                  and observed.get('finishPolicy') == 'on-verdict'
                  and probe.get('searchHorizon') == 30000
                  and not probe.get('conflicts') and not probe.get('missingEncounters'),
                  'ok=%s observed=%s horizon=%s conflicts=%s missing=%s err=%s'
                  % (probe.get('ok'), observed, probe.get('searchHorizon'), probe.get('conflicts'),
                     probe.get('missingEncounters'), probe.get('error')))
        check('real-baseline-both-arms-agree-stored-30000',
              real_observed[harness.ARM_NEW].get('tickLimit') == 30000
              and real_observed[harness.ARM_LEGACY].get('tickLimit') == 30000)
        # The REAL stored reference re-normalized in-process passes the frozen 30000 guard; a 3000
        # dispatch is refused before any meter charge.
        import strategy_optimizer_adapter as _real_adapter
        _cid = harness._reference_candidate(frozen, 19)
        _stored = harness._read_scenarios(frozen, [_cid])[_cid]
        _normalized = _real_adapter.validate_scenario(_stored)
        _plan = {'arms': {'new': {'observedPolicy': {
            'fields': {key: _normalized.get(key) for key in harness.GUARD_POLICY_KEYS},
            'referencesByEncounter': {
                '19': {'stored': _stored, 'encounterId': 19, 'candidateId': _cid}}}}}}
        _bound = harness.build_scenario_guard(_plan, 'new', 19, adapter=_real_adapter,
                                              students=_GuardStudents())
        _good_meter = harness.ExecutionMeter(4, stage='real-guard-30000')
        _good_module = _fake_pool_module()
        harness.install_bounded_dispatch(_good_meter, pool_module=_good_module,
                                         scenario_validator=_bound)
        _good_module.HeadlessPool().submit(
            None, harness.apply_frozen_policy(dict(encounterId=19, community=True), _bound), [1, 2])
        check('real-baseline-stored-guard-passes-30000-and-charges',
              _good_meter.charged == 1 and _real_adapter.default_scenario()['tickLimit'] == 3000)
        _bad = harness.apply_frozen_policy(dict(encounterId=19, community=True), _bound)
        _bad['tickLimit'] = 3000
        _bad_meter = harness.ExecutionMeter(4, stage='real-guard-3000')
        _bad_module = _fake_pool_module()
        harness.install_bounded_dispatch(_bad_meter, pool_module=_bad_module,
                                         scenario_validator=_bound)
        _bad_err = refuse(lambda: _bad_module.HeadlessPool().submit(None, _bad, [1, 2]))
        check('real-baseline-changed-3000-refused-before-charge',
              isinstance(_bad_err, harness.Blocked) and _bad_meter.charged == 0)
        # The pinned reference resolves PER encounter from the frozen baseline (never one
        # first-reference reused across encounters), and each encounter's own supplied reference is
        # admitted relative to itself (C1-C8 hold), so the representative parent needs no handicap.
        import strategy_students as _real_students
        _pinned = {}
        for encounter in (0, 15, 18, 19):
            entry = harness.pinned_reference(real_plan, harness.ARM_NEW, encounter)
            _pinned[encounter] = entry['candidateId']
            reference_scenario = harness._read_scenarios(frozen, [entry['candidateId']])[
                entry['candidateId']]
            check(f'real-baseline-pinned-reference-{encounter}',
                  entry['candidateId'] == harness._reference_candidate(frozen, encounter)
                  and _real_students.community_admits(reference_scenario,
                                                      parent=reference_scenario)[0])
        check('real-baseline-per-encounter-references-distinct-or-owned',
              len({_pinned[e] for e in _pinned}) >= 1
              and all(_pinned[e] for e in (0, 15, 18, 19)))
    else:
        check('frozen-baseline-present', False, f'missing {frozen}')

    refusal = _cli(['arm', '--plan', str(work / 'plan.json'), '--arm', 'legacy', '--stage', 'smoke',
                    '--source-root', str(HERE), '--out', str(work / 'arm')])
    check('arm-refuses-without-authorisation',
          refusal.returncode == 2 and 'authorised' in (refusal.stdout + refusal.stderr))
    run_refusal = _cli(['run-plan', '--plan', str(work / 'plan.json'), '--out', str(work / 'run')])
    check('run-plan-refuses-without-authorisation',
          run_refusal.returncode == 2 and 'authorised' in (run_refusal.stdout + run_refusal.stderr))

    # --- search transport: every ACTUAL completion must be native (fail closed) ------------------
    tc = harness.TransportCompletions()
    tc.observe(dict(resultBackend='native'))
    tc.observe([dict(resultBackend='native'), dict(resultBackend='native')])
    tc_summary = tc.summary()
    check('transport-completions-native-counts',
          tc_summary['completed'] == 3 and tc_summary['native'] == 3
          and tc_summary['fallback'] == 0 and tc_summary['unknown'] == 0
          and harness.assert_native_completions(tc_summary, stage='mock')['native'] == 3)
    tc_fallback = harness.TransportCompletions()
    tc_fallback.observe(dict(resultBackend='python', nativeFallbackReason='unvalidated-combo'))
    fallback_summary = tc_fallback.summary()
    check('transport-completions-record-fallback-reason',
          fallback_summary['fallback'] == 1
          and fallback_summary['reasons'].get('unvalidated-combo') == 1
          and isinstance(refuse(lambda: harness.assert_native_completions(fallback_summary,
                                                                          stage='mock')),
                         harness.Blocked))
    tc_unknown = harness.TransportCompletions()
    tc_unknown.observe(dict(seeds=[1, 2]))
    check('transport-completions-unknown-fails-closed',
          tc_unknown.summary()['unknown'] == 1
          and isinstance(refuse(lambda: harness.assert_native_completions(tc_unknown.summary(),
                                                                          stage='mock')),
                         harness.Blocked))
    check('transport-completions-empty-fails-closed',
          isinstance(refuse(lambda: harness.assert_native_completions(
              dict(completed=0, native=0, fallback=0, unknown=0, reasons={}), stage='mock')),
              harness.Blocked))
    from concurrent.futures import Future as _Future  # noqa: E402
    fut_completions = harness.TransportCompletions()
    future = _Future()
    fut_completions.observe(future)
    future.set_result(dict(resultBackend='native'))
    check('transport-completions-observes-future', fut_completions.summary()['native'] == 1)
    completed_module = types.SimpleNamespace(**{'HeadlessPool': type('P', (), {
        'submit': lambda self, *a, **k: dict(resultBackend='native'),
        'submit_batch': lambda self, *a, **k: [dict(resultBackend='native')]})})
    completion_meter = harness.ExecutionMeter(2, stage='completions')
    completion_log = harness.TransportCompletions()
    harness.install_bounded_dispatch(completion_meter, pool_module=completed_module,
                                     completions=completion_log)
    completed_pool = completed_module.HeadlessPool()
    completed_pool.submit(None, {}, [1, 2])
    completed_pool.submit_batch(None, {}, [[3, 4]])
    check('transport-completions-charged-and-native',
          completion_meter.charged == 2 and completion_log.summary()['native'] == 2)
    harness._write_transport_snapshot(work, 'unit', 'x',
                                      dict(completed=1, native=1, fallback=0, unknown=0, reasons={}))
    check('transport-snapshot-persisted', (work / 'transport-unit-x.json').is_file())
    search_source = inspect.getsource(harness.search_stage)
    shutdown_source = inspect.getsource(harness._shutdown_search)
    check('search-stage-enforces-native-completions',
          'assert_native_completions' in search_source
          and 'completions=completions' in search_source
          and '_shutdown_search' in search_source
          and 'transportBackends' in shutdown_source)

    # --- FINAL snapshots are taken AFTER the owned thread finished and BEFORE the wrapper is removed
    native_pool_module = types.SimpleNamespace(**{'HeadlessPool': type('P', (), {
        'submit': lambda self, *a, **k: dict(resultBackend='native')})})
    cleanup_completions = harness.TransportCompletions()
    cleanup_meter = harness.ExecutionMeter(128, stage='cleanup')
    state = dict(uninstalled=False)
    real_uninstall = harness.install_bounded_dispatch(
        cleanup_meter, pool_module=native_pool_module, completions=cleanup_completions)

    def _wrapped_uninstall():
        state['uninstalled'] = True
        real_uninstall()

    def _close_dispatch():
        # Four late dispatches the coordinator flushes while it stops, through the still-installed
        # wrapper; the zero-battle meter snapshot must not be the last word on them.
        for index in range(4):
            native_pool_module.HeadlessPool().submit(None, {}, [index, index + 1])

    cleanup_engine = CleanupDispatchEngine(baseline, _close_dispatch)
    cleanup_report = dict(errors=[])
    seen = {}

    def _cleanup_finalise(status):
        seen['status'] = status
        seen['uninstalledAtFinalise'] = state['uninstalled']

    cleanup_error, finalise_error = harness._shutdown_search(
        cleanup_engine, cleanup_meter, cleanup_completions, work, 'search-e19', 'new',
        cleanup_report, _wrapped_uninstall, finalise=_cleanup_finalise)
    cleanup_snapshot = harness.read_json(work / 'meter-search-e19-new.json')
    cleanup_transport = harness.read_json(work / 'transport-search-e19-new.json')
    check('shutdown-charges-cleanup-dispatches-after-join',
          cleanup_error is None and finalise_error is None
          and cleanup_meter.charged == 4
          and cleanup_snapshot['admitted'] == 4
          and cleanup_transport['native'] == 4)
    check('shutdown-finalises-before-uninstall',
          seen.get('uninstalledAtFinalise') is False and state['uninstalled'] is True
          and seen.get('status', {}).get('state') == 'Stopped')
    check('recovered-admitted-never-hidden-by-stale-zero',
          harness._recovered_admitted(work / 'meter-search-e19-new.json', None, 128,
                                      report_path=None) == 4)
    stale = work / 'meter-stale.json'
    harness.write_json(stale, dict(admitted=0, denied=0))
    larger = work / 'search-stale-e19-r1.json'
    harness.write_json(larger, dict(admitted=4))
    check('recovered-admitted-prefers-larger-durable-cost',
          harness._recovered_admitted(stale, None, 128, report_path=larger) == 4
          and harness._recovered_admitted(stale, None, 128) == 0)

    # --- if the owned thread is STILL alive, the wrapper stays installed (no unmetered work) ------
    class _StuckWorker:
        pid = 8888

        def __init__(self):
            self.killed = 0

        def poll(self):
            return None

        def kill(self):
            self.killed += 1

    class _StuckPool:
        def __init__(self):
            self.processes = [_StuckWorker()]

    class _StuckEngine:
        def __init__(self):
            self.commands = []
            self.thread = types.SimpleNamespace(is_alive=lambda: True,
                                                join=lambda timeout=None: None)

        def command(self, name, value=None, wait=True):
            self.commands.append(name)
            return dict(ok=True)

        def status(self):
            return dict(state='Running', error=None, totalRuns=0, scheduler={})

    stuck_pool = _StuckPool()
    stuck_meter = harness.ExecutionMeter(8, stage='stuck')
    stuck_flag = dict(uninstalled=False)
    real_stuck_uninstall = harness.install_bounded_dispatch(
        stuck_meter, pool_module=native_pool_module)

    def _stuck_uninstall():
        stuck_flag['uninstalled'] = True
        real_stuck_uninstall()

    taskkill_calls_stuck = []
    real_run_stuck = subprocess.run
    subprocess.run = lambda command, **kwargs: (
        taskkill_calls_stuck.append(tuple(command)) or types.SimpleNamespace(returncode=0))
    original_join = harness.SHUTDOWN_JOIN_SECONDS
    harness.SHUTDOWN_JOIN_SECONDS = 0.01
    stuck_report = dict(errors=[])
    stuck_cost = {}
    try:
        stuck_error, _stuck_finalise = harness._shutdown_search(
            _StuckEngine(), stuck_meter, harness.TransportCompletions(), work, 'search-stuck', 'new',
            stuck_report, _stuck_uninstall, finalise=None, pool_registry=[stuck_pool],
            cost=stuck_cost)
    finally:
        subprocess.run = real_run_stuck
        harness.SHUTDOWN_JOIN_SECONDS = original_join
    check('shutdown-stuck-thread-keeps-guard-installed',
          stuck_error is not None and stuck_flag['uninstalled'] is False
          and stuck_report.get('guardRetained') is True
          and stuck_report.get('ownedTerminations') == 1
          and stuck_pool.processes[0].killed == (1 if os.name != 'nt' else 0)
          and 'processCpu' in stuck_cost,
          str(stuck_error))

    # --- throughput gate: fail closed when unavailable, over threshold or unbound ----------------
    def _matched_payload(mode, infra, battles=8):
        return dict(throughput=dict(mode=mode, pairsDigest='pairs', scenarioDigest='scenario',
                                    battles=battles, workerConcurrency=4,
                                    stages={name: dict(wall=infra)
                                            for name in ('dispatch', 'persistence', 'analysis')}))

    gate_dir = work / 'gate'
    gate_dir.mkdir(parents=True, exist_ok=True)
    within = harness._throughput_gate(
        [_matched_payload('legacy-dispatch', 1.0), _matched_payload('new-dispatch', 1.05)],
        out_root=gate_dir, plan=plan)
    check('throughput-gate-compares-matched-work',
          within['compared'] is True and within['withinThreshold'] is True)
    persisted_gate = harness.read_json(gate_dir / 'throughput-gate.json')
    check('throughput-gate-persisted-and-bound',
          persisted_gate['planId'] == plan['planId']
          and persisted_gate['planDigest'] == plan['digest'])
    check('throughput-gate-over-threshold-fails-closed',
          isinstance(refuse(lambda: harness._throughput_gate(
              [_matched_payload('legacy-dispatch', 1.0), _matched_payload('new-dispatch', 5.0)],
              out_root=gate_dir, plan=plan)), harness.Blocked))
    check('throughput-gate-unavailable-fails-closed',
          isinstance(refuse(lambda: harness._throughput_gate(
              [_matched_payload('legacy-dispatch', 1.0)], out_root=gate_dir, plan=plan)),
              harness.Blocked))
    stale_gate_dir = work / 'gate-stale'
    stale_gate_dir.mkdir(parents=True, exist_ok=True)
    harness.write_json(stale_gate_dir / 'throughput-gate.json',
                       dict(compared=True, withinThreshold=True, planId='other', planDigest='x'))
    check('persisted-gate-refuses-unbound-plan',
          isinstance(refuse(lambda: harness._persisted_throughput_gate(stale_gate_dir, plan)),
                     harness.Blocked))
    check('search-cannot-bypass-gate',
          harness._persisted_throughput_gate(work / 'gate-none', plan) is None
          and "'throughput' not in stages" in inspect.getsource(harness.run_units))

    # --- the gate consumes the child's VALIDATED full report, never the compact stdout summary -----
    full_dir = work / 'throughput-full'
    full_dir.mkdir(parents=True, exist_ok=True)
    full_report = dict(arm='legacy', stage='throughput', mode='legacy-dispatch', battles=8,
                       admitted=8, workerConcurrency=4, pairsDigest='pairs',
                       scenarioDigest='scenario',
                       stages={name: dict(wall=1.0, parentCpu=0.1)
                               for name in ('dispatch', 'persistence', 'analysis')})
    harness.write_json(full_dir / 'throughput-legacy-legacy-dispatch.json', full_report)
    check('throughput-gate-loads-validated-full-report',
          harness._validated_throughput_report(full_dir, 'legacy', 'legacy-dispatch', 8)['admitted'] == 8)
    check('throughput-gate-refuses-missing-full-report',
          isinstance(refuse(lambda: harness._validated_throughput_report(
              full_dir, 'legacy', 'new-dispatch', 8)), harness.Blocked))
    truncated = json.loads(json.dumps(full_report))
    truncated['stages'] = dict(dispatch=dict(wall=1.0))
    harness.write_json(full_dir / 'throughput-legacy-legacy-dispatch.json', truncated)
    check('throughput-gate-refuses-truncated-report',
          isinstance(refuse(lambda: harness._validated_throughput_report(
              full_dir, 'legacy', 'legacy-dispatch', 8)), harness.Blocked))
    incomplete = json.loads(json.dumps(full_report))
    incomplete['admitted'] = 4
    harness.write_json(full_dir / 'throughput-legacy-legacy-dispatch.json', incomplete)
    check('throughput-gate-refuses-underexecuted-report',
          isinstance(refuse(lambda: harness._validated_throughput_report(
              full_dir, 'legacy', 'legacy-dispatch', 8)), harness.Blocked))
    check('run-units-uses-validated-full-report',
          '_validated_throughput_report' in inspect.getsource(harness.run_units)
          and 'throughput=report' in inspect.getsource(harness.run_units))

    # --- copy-only storage ceiling: declared, applied identically, never lowered ------------------
    storage = plan['storage']
    check('plan-declares-copy-storage-8192',
          storage['copyMiB'] == 8192 and storage['baselineMiB'] == 4096
          and storage['peakCopies'] == 2
          and storage['requiredFreeMiB'] == 2 * 8192 + storage['walMiB']
          and 'copies only' in storage['appliesTo'])
    storage_tamper = json.loads(json.dumps(plan))
    storage_tamper['storage']['copyMiB'] = 4096
    storage_tamper['digest'] = harness.digest(
        {k: v for k, v in storage_tamper.items() if k != 'digest'})
    check('plan-rejects-non-declared-copy-limit', bool(harness.validate_plan(storage_tamper)))

    class _FakeOptimizer:
        MAX_DB_MB = 4096

    fake_optimizer = _FakeOptimizer()
    check('copy-storage-applied-identically',
          harness.apply_copy_storage_limit(plan, fake_optimizer) == 8192
          and fake_optimizer.MAX_DB_MB == 8192)
    check('copy-storage-refuses-lowering',
          isinstance(refuse(lambda: harness.apply_copy_storage_limit(
              {'storage': {'copyMiB': 2048}}, _FakeOptimizer())), harness.Blocked))
    headroom = harness.copy_filesystem_headroom(plan, work)
    check('copy-storage-headroom-reported',
          headroom['requiredFreeMiB'] == 2 * 8192 + storage['walMiB']
          and headroom['checked'] is not None and headroom['ok'] is True, str(headroom))
    real_disk_usage = harness.shutil.disk_usage
    harness.shutil.disk_usage = lambda _path: types.SimpleNamespace(
        total=1, used=1, free=64 * 1024 * 1024)
    try:
        tight = harness.copy_filesystem_headroom(plan, work)
    finally:
        harness.shutil.disk_usage = real_disk_usage
    check('copy-storage-headroom-fails-closed-when-tight',
          tight['ok'] is False and tight['freeMiB'] == 64)
    check('run-units-requires-declared-copy-storage',
          'does not declare the copy-only storage limit' in inspect.getsource(harness.run_units))

    # --- close safety: no leaked handle after a copy; failed flush still closes the connection -----
    closed_copy = work / 'closure-copy.sqlite'
    harness.copy_baseline(baseline, closed_copy)
    # On Windows a leaked destination handle makes this unlink fail with WinError 32.
    closed_copy.unlink()
    check('copy-baseline-releases-its-handle', not closed_copy.exists())

    class _FailingStore:
        class _Db:
            def __init__(self):
                self.closed = False

            def close(self):
                self.closed = True

        def __init__(self):
            self.db = _FailingStore._Db()
            self.raised = harness.HarnessError('flush failed')

        def close(self):
            raise self.raised

    failing_store = _FailingStore()
    error = refuse(lambda: harness._close_store_safely(failing_store))
    check('close-store-releases-handle-after-failed-flush',
          error is failing_store.raised and failing_store.db.closed is True)
    check('close-store-noop-when-absent', harness._close_store_safely(None) is None)

    # --- early-recovery carryforward: adopt completed smoke/telemetry, charge once, fail closed ----
    def _write_native_rows(path, count, extra=None):
        rows = [dict(arm='new', encounterId=0, seeds=[index, index + 1],
                     raw=dict(resultBackend='native'), **(extra or {})) for index in range(count)]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('\n'.join(json.dumps(row) for row in rows) + '\n', encoding='utf-8')

    def _build_stopped_run(root):
        stopped = root / 'stopped'
        stopped.mkdir(parents=True, exist_ok=True)
        # The stopped plan differs from the new plan only in fields that are legal to differ.
        stopped_plan = json.loads(json.dumps(plan))
        stopped_plan.pop('storage', None)
        stopped_plan['digest'] = harness.digest(
            {k: v for k, v in stopped_plan.items() if k != 'digest'})
        harness.write_json(stopped / 'plan.json', stopped_plan)
        ledger = harness.BudgetLedger(stopped / 'battle-ledger.json',
                                      plan['budget']['hardCeiling'],
                                      setup_cap=plan['budget']['setupCap'])
        for stage, cap, actual in (('smoke:legacy', 40, 40), ('smoke:new', 40, 40),
                                   ('throughput:telemetry-off', 64, 64),
                                   ('throughput:telemetry-on', 64, 64),
                                   ('throughput:legacy-dispatch', 64, 13)):
            ledger.settle(ledger.reserve(stage, cap), actual)
        harness.write_json(stopped / 'authorisation-receipt.json', dict(
            kind='encounter-redesign-benchmark-authorisation-1', planId=stopped_plan['planId'],
            planDigest=stopped_plan['digest'], authorisedBy='root:--authorised', scope='run-plan'))
        _write_native_rows(stopped / 'smoke-legacy' / 'raw-smoke-legacy.jsonl', 40)
        _write_native_rows(stopped / 'smoke-new' / 'raw-smoke-new.jsonl', 40)
        for label, upper in (('off', 'telemetry-off'), ('on', 'telemetry-on')):
            unit = stopped / f'throughput-{upper}'
            _write_native_rows(unit / f'raw-throughput-new-telemetry-{label}.jsonl', 64,
                               extra=dict(mode=upper))
            harness.write_json(unit / f'throughput-new-telemetry-{label}.json',
                               dict(armed=True, stage='throughput', arm='new', mode=upper,
                                    battles=64, admitted=64))
        return stopped

    stopped_dir = _build_stopped_run(work / 'carryforward')
    out_dir = work / 'carryforward-out'
    out_dir.mkdir(parents=True, exist_ok=True)
    receipt, index = harness.prepare_carryforward(plan, stopped_dir, out_dir)
    check('carryforward-prior-total-221',
          receipt['priorTotal'] == 221 and receipt['adoptedBattles'] == 208
          and receipt['setupCharge']['battles'] == 13
          and receipt['priorLedger']['spent'] == 221
          and receipt['priorLedger']['reserved'] == {})
    check('carryforward-total-within-ceiling',
          receipt['expectedTotal'] == 221 + 128 + 3072 + 1536
          and receipt['expectedTotal'] == 4957 <= receipt['hardCeiling'] == 4976)
    check('carryforward-adopts-all-four-artifacts',
          len(receipt['adopted']) == 4
          and {entry['stage'] for entry in receipt['adopted']} == {'smoke', 'throughput'}
          and all(len(entry['sourceSha256']) == 64 for entry in receipt['adopted']))
    check('carryforward-receipt-binds-both-plans-and-receipt',
          receipt['priorPlan']['digest'] == harness.read_json(stopped_dir / 'plan.json')['digest']
          and receipt['newPlan']['digest'] == plan['digest']
          and receipt['priorAuthorisation']['authorisedBy'] == 'root:--authorised'
          and (out_dir / 'carryforward-receipt.json').is_file())
    check('carryforward-index-keys',
          set(index) == {('smoke', 'legacy'), ('smoke', 'new'),
                         ('throughput', 'telemetry-off'), ('throughput', 'telemetry-on')})
    adopted_ledger = harness.BudgetLedger(out_dir / 'carryforward-charge.json',
                                          plan['budget']['hardCeiling'],
                                          setup_cap=plan['budget']['setupCap'])
    for entry in receipt['adopted']:
        adopted_ledger.settle(adopted_ledger.reserve('carryforward', entry['cap']),
                              entry['admitted'])
    adopted_ledger.charge_setup('carryforward:throughput:legacy-dispatch', 13)
    check('carryforward-new-ledger-reaches-221',
          adopted_ledger.spent == 208 and adopted_ledger.setup_spent == 13
          and adopted_ledger.committed == 221)

    # Fail-closed guards: a wrong prior total, an outstanding reservation, prior search/holdout, a
    # non-declared plan change, and a wrong artifact count each refuse without adopting.
    bad_ledger = harness.read_json(stopped_dir / 'battle-ledger.json')
    bad_ledger['spent'] = 220
    harness.write_json(stopped_dir / 'battle-ledger-bad.json', bad_ledger)
    check('carryforward-refuses-wrong-prior-total',
          bool(harness.check_carryforward_ledger(plan, bad_ledger)))
    outstanding = harness.read_json(stopped_dir / 'battle-ledger.json')
    outstanding['reserved'] = {'9': dict(stage='x', cap=1, actual=None)}
    check('carryforward-refuses-outstanding-reservation',
          bool(harness.check_carryforward_ledger(plan, outstanding)))
    search_claim = harness.read_json(stopped_dir / 'battle-ledger.json')
    search_claim['claims'] = search_claim['claims'] + [
        dict(op='reserve', stage='search:legacy:0:r1', cap=1, spent=222)]
    check('carryforward-refuses-prior-search',
          bool(harness.check_carryforward_ledger(plan, search_claim)))
    changed = json.loads(json.dumps(plan))
    changed['budget']['stages']['search']['battles'] = 1
    check('carryforward-refuses-non-declared-plan-change',
          any('budget.stages.search.battles' in path
              for path in harness.disallowed_plan_differences(plan, changed)))

    broken_dir = _build_stopped_run(work / 'carryforward-broken')
    (broken_dir / 'smoke-new' / 'raw-smoke-new.jsonl').write_text('', encoding='utf-8')
    check('carryforward-refuses-incomplete-artifact',
          isinstance(refuse(lambda: harness.prepare_carryforward(
              plan, broken_dir, work / 'carryforward-broken-out')), harness.Blocked))

    # --- v4 continuation: content-detected by the prior ledger, adopts 6 stages, skips the failed --
    def _write_v4_rows(path, count, arm, mode=None):
        rows = []
        for index in range(count):
            row = dict(arm=arm, encounterId=0, seeds=[index, index + 1],
                       raw=dict(resultBackend='native'))
            if mode is not None:
                row['mode'] = mode
            rows.append(row)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('\n'.join(json.dumps(item) for item in rows) + '\n', encoding='utf-8')

    def _dispatch_payload(mode, arm, persistence, dispatch):
        return dict(armed=True, stage='throughput', arm=arm, mode=mode, battles=64, admitted=64,
                    pairsDigest='pairs', scenarioDigest='scenario', workerConcurrency=1,
                    persistence=persistence,
                    stages=dict(preparation=dict(wall=0.5, parentCpu=0.1),
                                dispatch=dict(wall=dispatch, parentCpu=0.1),
                                persistence=dict(wall=dispatch, parentCpu=0.1),
                                analysis=dict(wall=dispatch, parentCpu=0.1)))

    def _build_v4_stopped(root):
        stopped = root / 'stopped-v4'
        stopped.mkdir(parents=True, exist_ok=True)
        # The stopped plan is byte-identical except the harness-observer metadata and the fields
        # DERIVED from the re-written planId; storage (the 8 GiB copy limit) is deliberately identical.
        stopped_plan = json.loads(json.dumps(plan))
        stopped_plan['planId'] = 'v4-stopped-plan'
        stopped_plan['createdAt'] = '1970-01-01T00:00:00+00:00'
        stopped_plan['status'] = 'authorised'
        stopped_plan['smoke'] = dict(stopped_plan['smoke'], fixedPairs=[[1, 2], [3, 4]])
        stopped_plan['statistics'] = dict(
            stopped_plan['statistics'],
            bootstrap=dict(stopped_plan['statistics']['bootstrap'], seed=99))
        for arm in harness.ARMS:
            impl = stopped_plan['arms'][arm]['implementationInventory']
            impl['observerRevision'] = 'stopped-observer-rev'
            impl['observerFiles'] = dict(impl['observerFiles'], extra_observer='deadbeef')
            impl['digest'] = harness.digest({k: v for k, v in impl.items() if k != 'digest'})
        stopped_plan['digest'] = harness.digest(
            {k: v for k, v in stopped_plan.items() if k != 'digest'})
        harness.write_json(stopped / 'plan.json', stopped_plan)
        ledger = harness.BudgetLedger(stopped / 'battle-ledger.json',
                                      plan['budget']['hardCeiling'],
                                      setup_cap=plan['budget']['setupCap'])
        for stage, cap, actual in (('carryforward:smoke:legacy', 40, 40),
                                   ('carryforward:smoke:new', 40, 40),
                                   ('carryforward:throughput:telemetry-off', 64, 64),
                                   ('carryforward:throughput:telemetry-on', 64, 64),
                                   ('throughput:legacy-dispatch', 64, 64),
                                   ('throughput:new-dispatch', 64, 64),
                                   ('search:legacy:0:r1', 128, 128),
                                   ('search:new:0:r1', 128, 0)):
            ledger.settle(ledger.reserve(stage, cap), actual)
        ledger.charge_setup('carryforward:throughput:legacy-dispatch', 13)
        harness.write_json(stopped / 'authorisation-receipt.json', dict(
            kind='encounter-redesign-benchmark-authorisation-1', planId=stopped_plan['planId'],
            planDigest=stopped_plan['digest'], authorisedBy='root:--authorised', scope='run-plan'))
        _write_v4_rows(stopped / 'smoke-legacy' / 'raw-smoke-legacy.jsonl', 40, 'legacy')
        _write_v4_rows(stopped / 'smoke-new' / 'raw-smoke-new.jsonl', 40, 'new')
        for label, mode in (('off', 'telemetry-off'), ('on', 'telemetry-on')):
            unit = stopped / f'throughput-{mode}'
            _write_v4_rows(unit / f'raw-throughput-new-telemetry-{label}.jsonl', 64, 'new', mode)
            harness.write_json(unit / f'throughput-new-telemetry-{label}.json', dict(
                armed=True, stage='throughput', arm='new', mode=mode, battles=64, admitted=64,
                persistence='current-pool'))
        legacy_unit = stopped / 'throughput-legacy-dispatch'
        _write_v4_rows(legacy_unit / 'raw-throughput-legacy-legacy-dispatch.jsonl', 64, 'legacy',
                       'legacy-dispatch')
        harness.write_json(legacy_unit / 'throughput-legacy-legacy-dispatch.json',
                           _dispatch_payload('legacy-dispatch', 'legacy', 'store', 1.0))
        new_unit = stopped / 'throughput-new-dispatch'
        _write_v4_rows(new_unit / 'raw-throughput-new-new-dispatch.jsonl', 64, 'new', 'new-dispatch')
        harness.write_json(new_unit / 'throughput-new-new-dispatch.json',
                           _dispatch_payload('new-dispatch', 'new', 'ledger', 1.05))
        legacy_pair, new_pair = stopped / 'legacy-e0-r1', stopped / 'new-e0-r1'
        harness.write_json(legacy_pair / 'meter-search-e0-legacy.json',
                           dict(stage='search-e0', arm='legacy', admitted=128, denied=0, cap=128))
        harness.write_json(legacy_pair / 'search-legacy-e0-r1.json',
                           dict(arm='legacy', stage='search', admitted=128, declared=128))
        harness.write_json(new_pair / 'meter-search-e0-new.json',
                           dict(stage='search-e0', arm='new', admitted=0, denied=0, cap=128))
        harness.write_json(new_pair / 'search-new-e0-r1.json', dict(
            arm='new', stage='search', admitted=4, declared=128, incomplete=True,
            nomination=dict(nominee=None),
            eaLedger=dict(ea_session_budget=dict(completed=4),
                          ea_experiment_budget=dict(completed=4))))
        return stopped, stopped_plan

    v4_stopped, v4_stopped_plan = _build_v4_stopped(work / 'carryforward-v4')
    check('carryforward-detects-v4-by-ledger-content-not-name',
          harness._prior_has_search_claims(
              harness.read_json(v4_stopped / 'battle-ledger.json')) is True
          and harness._prior_has_search_claims(
              harness.read_json(stopped_dir / 'battle-ledger.json')) is False)
    named = work / 'carryforward-named'
    shutil.copytree(stopped_dir, named / 'comparison-v4')
    named_receipt, _named_index = harness.prepare_carryforward(
        plan, named / 'comparison-v4', work / 'carryforward-named-out')
    check('carryforward-name-alone-does-not-select-v4',
          named_receipt['schema'] == harness.CARRYFORWARD_SCHEMA
          and named_receipt['schema'] != harness.CARRYFORWARD_V4_SCHEMA)
    check('carryforward-v4-legal-plan-differences-only',
          not harness.disallowed_plan_differences_v4(v4_stopped_plan, plan)
          and v4_stopped_plan['planId'] != plan['planId']
          and v4_stopped_plan['storage'] == plan['storage'])

    v4_out = work / 'carryforward-v4-out'
    v4_out.mkdir(parents=True, exist_ok=True)
    v4_receipt, v4_index = harness.prepare_carryforward(plan, v4_stopped, v4_out)
    check('carryforward-v4-reconciles-prior-cost',
          v4_receipt['priorRegular'] == 468 and v4_receipt['priorTotal'] == 481
          and v4_receipt['adoptedBattles'] == 336
          and v4_receipt['priorLedger']['spent'] == 464
          and v4_receipt['priorLedger']['setupSpent'] == 13)
    check('carryforward-v4-total-and-unused-budget',
          v4_receipt['expectedTotal'] == 4705 and v4_receipt['hardCeiling'] == 4976
          and v4_receipt['unusedBudget'] == 271
          and v4_receipt['rerun']['searchBattles'] == 2816
          and v4_receipt['rerun']['holdoutBattles'] == 1408)
    check('carryforward-v4-adopts-six-and-indexes',
          len(v4_receipt['adopted']) == 6
          and set(v4_index) == {('smoke', 'legacy'), ('smoke', 'new'),
                                ('throughput', 'telemetry-off'), ('throughput', 'telemetry-on'),
                                ('throughput', 'legacy-dispatch'), ('throughput', 'new-dispatch')}
          and all(len(entry['sourceSha256']) == 64 for entry in v4_receipt['adopted']))
    check('carryforward-v4-failed-pair-permanent-no-reconstruction',
          v4_receipt['failedPair']['status'] == 'permanently-failed-inconclusive'
          and v4_receipt['failedPair']['legacyAdmitted'] == 128
          and v4_receipt['failedPair']['newAdmitted'] == 4
          and v4_receipt['failedPair']['holdoutBattles'] == 0
          and v4_receipt['failedPair']['replacement'] is False
          and v4_receipt['failedPair']['nominationReconstructed'] is False
          and [entry['admitted'] for entry in v4_receipt['failedSearches']] == [128, 4]
          and len(v4_receipt['untouchedPairs']) == 11
          and all(pair != dict(encounter=0, replicate=1)
                  for pair in v4_receipt['untouchedPairs']))
    check('carryforward-v4-gate-and-native-claim',
          v4_receipt['throughputGate']['compared'] is True
          and v4_receipt['throughputGate']['withinThreshold'] is True
          and 'NOT claimed native' in v4_receipt['nativeClaim'])
    v4_charge = harness.BudgetLedger(work / 'carryforward-v4-charge.json',
                                     plan['budget']['hardCeiling'],
                                     setup_cap=plan['budget']['setupCap'])
    for entry in v4_receipt['adopted']:
        v4_charge.settle(v4_charge.reserve('carryforward', entry['cap']), entry['admitted'])
    for entry in v4_receipt['failedSearches']:
        v4_charge.settle(v4_charge.reserve('carryforward-failed', int(entry['admitted'])),
                         int(entry['admitted']))
    v4_charge.charge_setup('carryforward:throughput:legacy-dispatch', 13)
    check('carryforward-v4-prior-charged-once-no-double-count',
          v4_charge.spent == 468 and v4_charge.setup_spent == 13 and v4_charge.committed == 481)

    # Fail-closed: every stale/unexpected/unreconstructable variant refuses without adopting.
    def _v4_variant(name, mutate):
        variant = work / f'carryforward-v4-{name}'
        if variant.exists():
            shutil.rmtree(variant)
        shutil.copytree(v4_stopped, variant)
        mutate(variant)
        return variant

    def _v4_refuses(name, mutate):
        variant = _v4_variant(name, mutate)
        return isinstance(refuse(lambda: harness.prepare_carryforward(
            plan, variant, work / f'carryforward-v4-{name}-out')), harness.Blocked)

    def _mutate_plan(path, mutate):
        doc = harness.read_json(path)
        mutate(doc)
        doc['digest'] = harness.digest({k: v for k, v in doc.items() if k != 'digest'})
        harness.write_json(path, doc)

    def _mutate_json(path, mutate):
        doc = harness.read_json(path)
        mutate(doc)
        harness.write_json(path, doc)

    check('carryforward-v4-refuses-results-json',
          _v4_refuses('results', lambda v: (v / 'results.json').write_text('{}', encoding='utf-8')))
    check('carryforward-v4-refuses-storage-change',
          _v4_refuses('storage', lambda v: _mutate_plan(
              v / 'plan.json', lambda d: d['storage'].__setitem__('copyMiB', 4096))))
    check('carryforward-v4-refuses-budget-change',
          _v4_refuses('budget', lambda v: _mutate_plan(
              v / 'plan.json',
              lambda d: d['budget']['stages']['search'].__setitem__('battles', 1))))
    check('carryforward-v4-refuses-encounter-change',
          _v4_refuses('encounters', lambda v: _mutate_plan(
              v / 'plan.json', lambda d: d.__setitem__('encounters', d['encounters'][:3]))))
    check('carryforward-v4-refuses-stale-ledger-spent',
          _v4_refuses('spent', lambda v: _mutate_json(
              v / 'battle-ledger.json', lambda d: d.__setitem__('spent', 100))))
    check('carryforward-v4-refuses-unexpected-claim',
          _v4_refuses('claim', lambda v: _mutate_json(
              v / 'battle-ledger.json', lambda d: d['claims'].append(
                  dict(op='settle', stage='search:legacy:15:r1', cap=64, actual=64)))))
    check('carryforward-v4-refuses-outstanding-reservation',
          _v4_refuses('reserved', lambda v: _mutate_json(
              v / 'battle-ledger.json', lambda d: d.__setitem__(
                  'reserved', {'9': dict(stage='x', cap=1, actual=None)}))))
    check('carryforward-v4-refuses-wrong-setup-charge',
          _v4_refuses('setup', lambda v: _mutate_json(
              v / 'battle-ledger.json', lambda d: [claim.__setitem__('count', 12)
                                                   for claim in d['claims']
                                                   if claim.get('op') == 'setup'])))
    check('carryforward-v4-refuses-nominee-reconstruction',
          _v4_refuses('nominee', lambda v: _mutate_json(
              v / 'new-e0-r1' / 'search-new-e0-r1.json',
              lambda d: d.__setitem__('nomination', dict(nominee='c1')))))
    check('carryforward-v4-refuses-holdout-artifact',
          _v4_refuses('holdout', lambda v: (v / 'new-e0-r1' / 'holdout-new-e0-r1.json').write_text(
              '{}', encoding='utf-8')))
    check('carryforward-v4-refuses-wrong-failed-count',
          _v4_refuses('count', lambda v: _mutate_json(
              v / 'new-e0-r1' / 'search-new-e0-r1.json', lambda d: d.__setitem__('admitted', 5))))
    check('carryforward-v4-refuses-short-legacy-declaration',
          _v4_refuses('declared', lambda v: _mutate_json(
              v / 'legacy-e0-r1' / 'search-legacy-e0-r1.json',
              lambda d: d.__setitem__('declared', 100))))
    check('carryforward-v4-refuses-not-incomplete',
          _v4_refuses('complete', lambda v: _mutate_json(
              v / 'new-e0-r1' / 'search-new-e0-r1.json',
              lambda d: d.__setitem__('incomplete', False))))
    check('carryforward-v4-refuses-short-adopted-artifact',
          _v4_refuses('short', lambda v: (v / 'smoke-legacy' / 'raw-smoke-legacy.jsonl').write_text(
              '\n'.join(json.dumps(dict(arm='legacy', encounterId=0, seeds=[i, i + 1],
                                        raw=dict(resultBackend='native')))
                        for i in range(10)) + '\n', encoding='utf-8')))

    # No-battle orchestration: run_units with --resume-from charges the prior cost once, retains the
    # failed pair and the adopted timing costs, and never spawns a battle for an adopted stage.
    real_preflight = harness.preflight
    harness.preflight = lambda plan, python=None, out_root=None, timeout=900: dict(
        gates={}, blocked=[], notes=['stubbed no-battle preflight'])
    try:
        v4_run = harness.run_units(plan, PYTHON, work / 'carryforward-v4-run', stages=('smoke',),
                                   authorise=True, workers=1, resume_from=v4_stopped)
    finally:
        harness.preflight = real_preflight
    check('run-units-resume-from-v4-charges-prior-once',
          v4_run['ledger']['spent'] == 468 and v4_run['ledger']['setupSpent'] == 13
          and v4_run['ledger']['committed'] == 481
          and v4_run['computeAccounting']['prior'] == dict(adoptedBattles=336,
                                                           failedPairBattles=132, chargedTotal=481)
          and v4_run['computeAccounting']['new']['battles'] == 0
          and all(unit.get('compute') == 'prior' for unit in v4_run['units']))
    check('run-units-resume-from-v4-retains-failed-pair-and-costs',
          bool(v4_run['failedUnits'])
          and v4_run['failedUnits'][0]['status'] == 'permanently-failed-inconclusive'
          and v4_run['failedUnits'][0]['compute'] == 'prior'
          and v4_run['carryforward']['priorTotal'] == 481
          and (work / 'carryforward-v4-run' / 'results.json').is_file()
          and v4_run['units'][0]['carryforward']['sourceSha256'])

    spawn_calls = []

    def _search_stub(python, plan_path, arm, stage, source_root, copy_path, unit_dir, cap=0,
                     extra=None, seconds=0, workers=1):
        spawn_calls.append((arm, stage))
        return dict(admitted=cap, declared=cap, incomplete=False, failed=False, observedRuns=cap,
                    nomination=dict(nominee='c1'), usedPairs=[[1, 2], [3, 4]])

    real_spawn, real_preflight = harness.spawn_arm, harness.preflight
    harness.spawn_arm = _search_stub
    harness.preflight = lambda plan, python=None, out_root=None, timeout=900: dict(
        gates={}, blocked=[], notes=['stubbed no-battle preflight'])
    try:
        v4_search = harness.run_units(plan, PYTHON, work / 'carryforward-v4-search',
                                      stages=('throughput', 'search'), authorise=True, workers=1,
                                      resume_from=v4_stopped)
    finally:
        harness.spawn_arm, harness.preflight = real_spawn, real_preflight
    check('run-units-v4-skips-only-failed-pair-and-runs-eleven',
          len(spawn_calls) == 22 and all(stage == 'search' for _arm, stage in spawn_calls)
          and any(step.get('op') == 'failed-unit' and step.get('encounterId') == 0
                  and step.get('replicate') == 1 and step.get('compute') == 'prior'
                  for step in v4_search['steps'])
          and v4_search['computeAccounting']['new']['battles'] == 2816
          and v4_search['ledger']['committed'] == 3297
          and not v4_search['incomplete'])
    check('run-plan-cli-wires-resume-from-to-prepare-carryforward',
          'resume_from=getattr(args, \'resume_from\', None)' in inspect.getsource(harness.cmd_run_plan)
          and 'manifest_path = getattr(args, \'reviewed_source_manifest\', None)' in inspect.getsource(
              harness.cmd_run_plan)
          and 'reviewed_source_manifest = read_json(manifest_path)' in inspect.getsource(
              harness.cmd_run_plan)
          and 'prepare_carryforward(' in inspect.getsource(harness.run_units)
          and 'resume_from' in inspect.getsource(harness.run_units)
          and 'prepare_carryforward_v4' in inspect.getsource(harness.prepare_carryforward)
          and 'prepare_carryforward_v5' in inspect.getsource(harness.prepare_carryforward))

    # --- v5 continuation: TWO permanently failed pairs, 10 untouched pairs, reviewed source change --
    def _rewrite_plan(base, plan_id, mutate=None):
        doc = json.loads(json.dumps(base))
        doc['planId'] = plan_id
        doc['smoke']['fixedPairs'] = [list(pair) for pair in
                                      harness.derive_pairs(plan_id, 'smoke-pairs', 2)]
        doc['statistics']['bootstrap']['seed'] = int(plan_id[:8], 16)
        doc['createdAt'] = '2026-09-28T09:00:00Z'
        if mutate:
            mutate(doc)
        doc['digest'] = harness.digest({k: v for k, v in doc.items() if k != 'digest'})
        return doc

    def _change(rel, old_hash, new_hash, rationale='reviewed seed/readiness fix'):
        return dict(path=rel, oldSha256=old_hash, newSha256=new_hash, rationale=rationale)

    def _manifest(prior_digest, changes):
        return dict(schema=harness.REVIEWED_SOURCE_MANIFEST_SCHEMA, arm='new',
                    priorPlanDigest=prior_digest, changes=changes)

    def _set_new_arm_file(doc, rel, value):
        doc['arms']['new']['implementationInventory']['files']['impl/' + rel] = value

    def _build_v5_stopped(root, prior_plan):
        stopped = root / 'stopped-v5'
        stopped.mkdir(parents=True, exist_ok=True)
        harness.write_json(stopped / 'plan.json', prior_plan)
        ledger = harness.BudgetLedger(stopped / 'battle-ledger.json', plan['budget']['hardCeiling'],
                                      setup_cap=plan['budget']['setupCap'])
        for stage, cap, actual in (
                ('carryforward:smoke:legacy', 40, 40), ('carryforward:smoke:new', 40, 40),
                ('carryforward:throughput:telemetry-off', 64, 64),
                ('carryforward:throughput:telemetry-on', 64, 64),
                ('carryforward:throughput:legacy-dispatch', 64, 64),
                ('carryforward:throughput:new-dispatch', 64, 64),
                ('carryforward-failed:search:legacy:0:r1', 128, 128),
                ('carryforward-failed:search:new:0:r1', 4, 4),
                ('search:legacy:15:r1', 128, 128), ('search:new:15:r1', 128, 0)):
            ledger.settle(ledger.reserve(stage, cap), actual)
        ledger.charge_setup('carryforward:throughput:legacy-dispatch', 13)
        harness.write_json(stopped / 'authorisation-receipt.json', dict(
            kind='encounter-redesign-benchmark-authorisation-1', planId=prior_plan['planId'],
            planDigest=prior_plan['digest'], authorisedBy='root:--authorised', scope='run-plan'))
        _write_v4_rows(stopped / 'smoke-legacy' / 'raw-smoke-legacy.jsonl', 40, 'legacy')
        _write_v4_rows(stopped / 'smoke-new' / 'raw-smoke-new.jsonl', 40, 'new')
        for label, mode in (('off', 'telemetry-off'), ('on', 'telemetry-on')):
            unit = stopped / f'throughput-{mode}'
            _write_v4_rows(unit / f'raw-throughput-new-telemetry-{label}.jsonl', 64, 'new', mode)
            harness.write_json(unit / f'throughput-new-telemetry-{label}.json', dict(
                armed=True, stage='throughput', arm='new', mode=mode, battles=64, admitted=64,
                persistence='current-pool'))
        legacy_unit = stopped / 'throughput-legacy-dispatch'
        _write_v4_rows(legacy_unit / 'raw-throughput-legacy-legacy-dispatch.jsonl', 64, 'legacy',
                       'legacy-dispatch')
        harness.write_json(legacy_unit / 'throughput-legacy-legacy-dispatch.json',
                           _dispatch_payload('legacy-dispatch', 'legacy', 'store', 1.0))
        new_unit = stopped / 'throughput-new-dispatch'
        _write_v4_rows(new_unit / 'raw-throughput-new-new-dispatch.jsonl', 64, 'new', 'new-dispatch')
        harness.write_json(new_unit / 'throughput-new-new-dispatch.json',
                           _dispatch_payload('new-dispatch', 'new', 'ledger', 1.05))
        carried = root / 'v5-prior-v4'
        carried_reports = {}
        for arm in harness.ARMS:
            path = carried / f'{arm}-e0-r1' / f'search-{arm}-e0-r1.json'
            harness.write_json(path, dict(arm=arm, stage='search',
                                          admitted=128 if arm == 'legacy' else 4, declared=128,
                                          incomplete=(arm == 'new'), nomination=dict(nominee=None)))
            carried_reports[arm] = path
        for arm, admitted, native in (('legacy', 128, 128), ('new', 0, 0)):
            unit = stopped / f'{arm}-e15-r1'
            harness.write_json(unit / f'meter-search-e15-{arm}.json',
                               dict(stage='search-e15', arm=arm, admitted=admitted, denied=0, cap=128,
                                    usedPairs=admitted))
            harness.write_json(unit / f'transport-search-e15-{arm}.json',
                               dict(completed=native, native=native, fallback=0, unknown=0, reasons={}))
            harness.write_json(unit / f'search-{arm}-e15-r1.json', dict(
                arm=arm, stage='search', encounterId=15, replicate=1, admitted=admitted, declared=128,
                incomplete=(arm == 'new'), failed=False,
                usedPairs=[[1, 2]] * admitted if arm == 'legacy' else [],
                observerCost=dict(
                    parentCpuSeconds=(105.6719 if arm == 'new' else 48.8125),
                    wallSeconds=(147.8641 if arm == 'new' else 55.9225),
                    workerCpuSeconds=(None if arm == 'new' else 0.0), workerCpuNote='mock',
                    stages=dict(dispatch=dict(wall=1.0, parentCpu=1.0))),
                nomination=dict(nominee=(
                    '51563448cf2104674d818bc21e5f9ee10ab8337e2c72ae2d6d420e3e877017cb'
                    if arm == 'new' else 'c1'))))
        harness.write_json(stopped / 'new-e15-r1' / 'child-search-new.json',
                           dict(returncode=2, seconds=149.29, timedOut=False))
        harness.write_json(stopped / 'carryforward-receipt.json', dict(
            schema=harness.CARRYFORWARD_V4_SCHEMA, detectedBy='content',
            failedPair=dict(encounter=0, replicate=1, status='permanently-failed-inconclusive',
                            legacyAdmitted=128, newAdmitted=4, holdoutBattles=0, replacement=False,
                            nominationReconstructed=False,
                            legacyReport=dict(path=str(carried_reports['legacy']),
                                              sha256=harness._file_sha256(carried_reports['legacy'])),
                            newReport=dict(path=str(carried_reports['new']),
                                           sha256=harness._file_sha256(carried_reports['new'])))))
        return stopped

    v5_prior = _rewrite_plan(plan, 'a' * 32)
    v5_new = _rewrite_plan(plan, 'b' * 32)
    v5_stopped = _build_v5_stopped(work / 'carryforward-v5', v5_prior)
    base_manifest = _manifest(v5_prior['digest'], [])
    v5_out = work / 'carryforward-v5-out'
    v5_out.mkdir(parents=True, exist_ok=True)
    v5_receipt, v5_index = harness.prepare_carryforward(
        v5_new, v5_stopped, v5_out, reviewed_source_manifest=base_manifest)
    check('carryforward-v5-detected-by-ledger-content-not-name',
          harness._prior_is_v5_continuation(
              harness.read_json(v5_stopped / 'battle-ledger.json')) is True
          and harness._prior_is_v5_continuation(
              harness.read_json(v4_stopped / 'battle-ledger.json')) is False)
    check('carryforward-v5-reconciles-609-exactly-once',
          v5_receipt['priorRegular'] == 596 and v5_receipt['priorTotal'] == 609
          and v5_receipt['priorLedger']['spent'] == 596
          and v5_receipt['priorLedger']['setupSpent'] == 13
          and v5_receipt['priorLedger']['reserved'] == {})
    check('carryforward-v5-total-and-unused-budget',
          v5_receipt['expectedTotal'] == 4449 and v5_receipt['hardCeiling'] == 4976
          and v5_receipt['unusedBudget'] == 527
          and v5_receipt['rerun']['pairs'] == 10
          and v5_receipt['rerun']['searchBattles'] == 2560
          and v5_receipt['rerun']['holdoutBattles'] == 1280
          and v5_receipt['rerun']['failedPairsSkipped'] == 2)
    check('carryforward-v5-adopts-six-and-indexes',
          len(v5_receipt['adopted']) == 6
          and set(v5_index) == {('smoke', 'legacy'), ('smoke', 'new'),
                                ('throughput', 'telemetry-off'), ('throughput', 'telemetry-on'),
                                ('throughput', 'legacy-dispatch'), ('throughput', 'new-dispatch')})
    check('carryforward-v5-two-failed-pairs-never-replaced',
          len(v5_receipt['failedPairs']) == 2
          and [p['encounter'] for p in v5_receipt['failedPairs']] == [0, 15]
          and {p['replicate'] for p in v5_receipt['failedPairs']} == {1}
          and all(p['status'] == 'permanently-failed-inconclusive' and p['replacement'] is False
                  and p['nominationReconstructed'] is False for p in v5_receipt['failedPairs'])
          and [p['newAdmitted'] for p in v5_receipt['failedPairs']] == [4, 0]
          and len(v5_receipt['untouchedPairs']) == 10
          and all(pair not in ({'encounter': 0, 'replicate': 1}, {'encounter': 15, 'replicate': 1})
                  for pair in v5_receipt['untouchedPairs']))
    check('carryforward-v5-failed-searches-four-exact',
          [entry['admitted'] for entry in v5_receipt['failedSearches']] == [128, 4, 128, 0])
    v5_charge = harness.BudgetLedger(work / 'v5-charge.json', 4976, setup_cap=32)
    for entry in v5_receipt['adopted']:
        v5_charge.settle(v5_charge.reserve('carryforward', entry['cap']), entry['admitted'])
    for entry in v5_receipt['failedSearches']:
        v5_charge.settle(v5_charge.reserve('carryforward-failed', int(entry['admitted'])),
                         int(entry['admitted']))
    v5_charge.charge_setup('carryforward:throughput:legacy-dispatch', 13)
    check('carryforward-v5-prior-charged-once-no-double-count',
          v5_charge.spent == 596 and v5_charge.setup_spent == 13 and v5_charge.committed == 609
          and v5_receipt['throughputGate']['withinThreshold'] is True
          and 'ZERO transport completions' in v5_receipt['nativeClaim'])

    def _v5_refuses(name, mutate=None, new_plan=None, manifest=None):
        variant = work / f'carryforward-v5-r-{name}'
        if variant.exists():
            shutil.rmtree(variant)
        shutil.copytree(v5_stopped, variant)
        if mutate:
            mutate(variant)
        return isinstance(refuse(lambda: harness.prepare_carryforward(
            new_plan or v5_new, variant, work / f'carryforward-v5-r-{name}-out',
            reviewed_source_manifest=base_manifest if manifest is None else manifest)),
            harness.Blocked)

    file_under_test = 'strategy_seed_freshness.py'
    file_old_hash = plan['arms']['new']['implementationInventory']['files'][
        'impl/' + file_under_test]
    changed_plan = _rewrite_plan(plan, 'c' * 32,
                                 mutate=lambda d: _set_new_arm_file(d, file_under_test, 'c' * 64))
    changed_manifest = _manifest(v5_prior['digest'],
                                 [_change(file_under_test, file_old_hash, 'c' * 64)])
    v5_changed_out = work / 'carryforward-v5-changed-out'
    v5_changed_out.mkdir(parents=True, exist_ok=True)
    changed_receipt, _ = harness.prepare_carryforward(
        changed_plan, v5_stopped, v5_changed_out, reviewed_source_manifest=changed_manifest)
    check('carryforward-v5-accepts-reviewed-allowed-source-change',
          changed_receipt['reviewedSourceChanges'] == changed_manifest['changes']
          and changed_receipt['priorTotal'] == 609)
    check('carryforward-v5-refuses-missing-manifest',
          isinstance(refuse(lambda: harness.prepare_carryforward(
              v5_new, v5_stopped, work / 'carryforward-v5-nomanifest-out')), harness.Blocked))
    check('carryforward-v5-refuses-unsigned-source-hash',
          _v5_refuses('badhash', new_plan=changed_plan,
                      manifest=_manifest(v5_prior['digest'],
                                         [_change(file_under_test, file_old_hash, 'd' * 64)])))
    combat_plan = _rewrite_plan(plan, 'e' * 32,
                                mutate=lambda d: _set_new_arm_file(d, 'strategy_mechanics.py',
                                                                   'e' * 64))
    check('carryforward-v5-refuses-changed-combat-file',
          _v5_refuses('combat', new_plan=combat_plan,
                      manifest=_manifest(v5_prior['digest'],
                                         [_change('strategy_mechanics.py', 'f' * 64, 'e' * 64)])))
    legacy_plan = _rewrite_plan(plan, '2' * 32, mutate=lambda d: d['arms']['legacy'][
        'implementationInventory']['files'].__setitem__('impl/' + file_under_test, '9' * 64))
    check('carryforward-v5-refuses-changed-legacy-file',
          _v5_refuses('legacy', new_plan=legacy_plan))
    policy_plan = _rewrite_plan(plan, '3' * 32, mutate=lambda d: d['arms']['new'][
        'observedPolicy']['fields'].__setitem__('finishPolicy', 'changed'))
    check('carryforward-v5-refuses-changed-policy-field',
          _v5_refuses('policy', new_plan=policy_plan))
    budget_plan = _rewrite_plan(plan, '1' * 32,
                                mutate=lambda d: d['budget']['stages']['search'].__setitem__(
                                    'battles', 1))
    check('carryforward-v5-refuses-changed-plan-field',
          _v5_refuses('planfield', new_plan=budget_plan))
    check('carryforward-v5-refuses-unmatched-source-change',
          _v5_refuses('unmatched', new_plan=changed_plan))
    check('carryforward-v5-refuses-wrong-prior-total',
          _v5_refuses('spent', mutate=lambda v: _mutate_json(
              v / 'battle-ledger.json', lambda d: d.__setitem__('spent', 590))))
    check('carryforward-v5-refuses-outstanding-reservation',
          _v5_refuses('reserved', mutate=lambda v: _mutate_json(
              v / 'battle-ledger.json', lambda d: d.__setitem__(
                  'reserved', {'99': dict(stage='x', cap=1, actual=None)}))))
    check('carryforward-v5-refuses-replayed-failed-pair',
          _v5_refuses('replay', mutate=lambda v: _mutate_json(
              v / 'battle-ledger.json', lambda d: d['claims'].append(
                  dict(op='settle', stage='search:legacy:15:r1', cap=128, actual=128)))))
    check('carryforward-v5-refuses-wrong-failed-count',
          _v5_refuses('count', mutate=lambda v: _mutate_json(
              v / 'new-e15-r1' / 'search-new-e15-r1.json',
              lambda d: d.__setitem__('admitted', 1))))
    check('carryforward-v5-refuses-holdout-artifact',
          _v5_refuses('holdout', mutate=lambda v: (
              v / 'new-e15-r1' / 'holdout-new-e15-r1.json').write_text('{}', encoding='utf-8')))

    # --- run-plan CLI parses the reviewed-source manifest PATH into the DICT run_units expects -------
    # The real argparse entry is used and only ``run_units`` is stubbed, so the wiring is verified end
    # to end without any battle: a v5 run-plan must hand the parsed manifest dict (not the path string)
    # to run_units, or the continuation would fail on the argument type BEFORE dispatch.
    cli_plan_path = work / 'run-plan-cli-plan.json'
    harness.write_json(cli_plan_path, plan)
    cli_manifest = _manifest(plan['digest'], [])
    cli_manifest_path = work / 'run-plan-cli-manifest.json'
    harness.write_json(cli_manifest_path, cli_manifest)
    captured = {}

    def _stub_run_units(plan_arg, python, out_root, **kwargs):
        captured['plan'] = plan_arg
        captured['kwargs'] = kwargs
        return dict(units=[], summary=dict(decisive=0, unresolved=0, incomplete=False))

    real_run_units = harness.run_units
    harness.run_units = _stub_run_units
    try:
        cli_args = harness.build_parser().parse_args(
            ['run-plan', '--plan', str(cli_plan_path), '--out', str(work / 'run-plan-cli-out'),
             '--authorised', '--resume-from', str(work / 'run-plan-cli-resume'),
             '--reviewed-source-manifest', str(cli_manifest_path)])
        # cmd_run_plan prints its JSON receipt; keep it out of this checker's own stdout.
        with contextlib.redirect_stdout(io.StringIO()):
            cli_rc = cli_args.func(cli_args)
    finally:
        harness.run_units = real_run_units
    check('run-plan-cli-parses-reviewed-source-manifest-before-dispatch',
          cli_rc == 0
          and isinstance(captured.get('kwargs', {}).get('reviewed_source_manifest'), dict)
          and captured['kwargs']['reviewed_source_manifest'] == cli_manifest
          and captured['kwargs'].get('resume_from') == str(work / 'run-plan-cli-resume'))

    # --- reviewed-source glue: strategy_optimizer.py mirror + derived-digest binding (synthetic) ------
    # ``strategy_optimizer.py`` is signed as glue because adapter.provenance also hashes it: its hash is
    # the same in impl/, canonical/tools/recovery/ AND sourceInventory.files, and the provenance digest
    # (mirrored into policy.revision) is derived from it. A valid derived inventory is accepted; a wrong
    # digest, a mismatched mirror, an unmanifested extra optimizer/canonical change, a smuggled combat
    # file or a changed policy field is refused.
    def _glue_provenance(files):
        return {mode: harness.digest(
            dict(files=dict(files), count=len(files), missing=[], mode=mode))
            for mode in harness.PROVENANCE_DIGEST_MODES}

    def _glue_arm(opt_hash, provenance_files, fields=None):
        digest_value = _glue_provenance(provenance_files)['recovery-workspace']
        policy = dict(ok=True, finishPolicy='on-verdict', scenarioScope={'tickLimit': 30000},
                      source='per-arm-subprocess-probe', error=None, revision=digest_value)
        policy.update(fields or {})
        impl_files = {'impl/strategy_optimizer.py': opt_hash,
                      'impl/strategy_legacy_observations.py': 'aa' * 32}
        impl_files.update({'canonical/' + label: value
                           for label, value in provenance_files.items()})
        return dict(implementationInventory=dict(files=impl_files, digest='impl-digest'),
                    sourceInventory=dict(ok=True, digest=digest_value, count=len(provenance_files),
                                         files=dict(provenance_files), missing=[], error=None, note='n'),
                    policy=policy, observedPolicy=dict(ok=True, fields={'tickLimit': 30000}))

    def _glue_plan(plan_id, opt_hash, provenance_files, fields=None):
        return dict(schema=harness.PLAN_SCHEMA, planId=plan_id, digest='d' * 64, budget={},
                    createdAt='2026-09-28T09:00:00Z', status='proposed',
                    arms={'legacy': dict(label='frozen'),
                          'new': _glue_arm(opt_hash, provenance_files, fields)})

    glue_files_old = {'tools/recovery/strategy_optimizer.py': '1' * 64,
                      'tools/recovery/strategy_search.py': 'b' * 64,
                      'tools/recovery/combat_kernel.py': 'c' * 64}
    glue_files_new = dict(glue_files_old, **{'tools/recovery/strategy_optimizer.py': '2' * 64})
    glue_old = _glue_plan('a' * 32, '1' * 64, glue_files_old)
    glue_new = _glue_plan('b' * 32, '2' * 64, glue_files_new)
    glue_manifest = _manifest(glue_old['digest'],
                              [_change('strategy_optimizer.py', '1' * 64, '2' * 64)])
    glue_problems, glue_changes = harness.check_reviewed_source_changes(glue_old, glue_new,
                                                                        glue_manifest)
    check('reviewed-glue-optimizer-accepts-bound-derived-mirrors',
          not glue_problems and [c['path'] for c in glue_changes] == ['strategy_optimizer.py'],
          '; '.join(glue_problems))

    def _glue_refuses(mutate=None, manifest=None):
        variant = json.loads(json.dumps(glue_new))
        if mutate:
            mutate(variant)
        return bool(harness.check_reviewed_source_changes(
            glue_old, variant, glue_manifest if manifest is None else manifest)[0])

    check('reviewed-glue-optimizer-rejects-wrong-digest', _glue_refuses(
        lambda d: d['arms']['new']['sourceInventory'].__setitem__('digest', 'ff' * 32)))
    check('reviewed-glue-optimizer-rejects-wrong-canonical-mirror', _glue_refuses(
        lambda d: d['arms']['new']['implementationInventory']['files'].__setitem__(
            'canonical/tools/recovery/strategy_optimizer.py', '9' * 64)))
    check('reviewed-glue-optimizer-rejects-wrong-source-inventory-mirror', _glue_refuses(
        lambda d: d['arms']['new']['sourceInventory']['files'].__setitem__(
            'tools/recovery/strategy_optimizer.py', '9' * 64)))
    check('reviewed-glue-optimizer-rejects-extra-canonical-change', _glue_refuses(
        lambda d: d['arms']['new']['sourceInventory']['files'].__setitem__(
            'tools/recovery/strategy_search.py', '9' * 64)))
    check('reviewed-glue-optimizer-rejects-extra-impl-change-unmanifested', _glue_refuses(
        lambda d: d['arms']['new']['implementationInventory']['files'].__setitem__(
            'impl/strategy_optimizer_limits.py', '9' * 64)))
    check('reviewed-glue-optimizer-rejects-changed-policy-field', _glue_refuses(
        lambda d: d['arms']['new']['policy'].__setitem__('finishPolicy', 'changed')))
    check('reviewed-glue-optimizer-rejects-unmanifested',
          _glue_refuses(manifest=_manifest(glue_old['digest'], [])))

    actual_v5 = work.parent / 'comparison-v5'
    if (actual_v5 / 'plan.json').is_file():
        real_prior = harness.read_json(actual_v5 / 'plan.json')
        real_old = real_prior['arms']['new']['implementationInventory']['files'][
            'impl/' + file_under_test]
        dry_plan = _rewrite_plan(real_prior, 'cafe' * 8,
                                 mutate=lambda d: _set_new_arm_file(d, file_under_test, 'ab' * 32))
        dry_manifest = _manifest(real_prior['digest'],
                                 [_change(file_under_test, real_old, 'ab' * 32, 'drycheck mock')])
        dry_out = work / 'carryforward-v5-actual-out'
        dry_out.mkdir(parents=True, exist_ok=True)
        dry_receipt, dry_index = harness.prepare_carryforward(
            dry_plan, actual_v5, dry_out, reviewed_source_manifest=dry_manifest)
        e15 = [p for p in dry_receipt['failedPairs'] if p['encounter'] == 15]
        check('carryforward-v5-actual-drycheck-609-and-10-pairs',
              dry_receipt['priorTotal'] == 609 and dry_receipt['expectedTotal'] == 4449
              and dry_receipt['hardCeiling'] == 4976
              and len(dry_receipt['untouchedPairs']) == 10
              and len(dry_receipt['failedPairs']) == 2 and len(dry_index) == 6)
        check('carryforward-v5-actual-new-e15-inconclusive-and-costs',
              len(e15) == 1 and e15[0]['legacyAdmitted'] == 128 and e15[0]['newAdmitted'] == 0
              and e15[0]['nominee'] == '51563448cf2104674d818bc21e5f9ee10ab8337e2c72ae2d6d420e3e877017cb'
              and e15[0]['observerCost']['new']['parentCpuSeconds'] == 105.6719
              and e15[0]['observerCost']['legacy']['parentCpuSeconds'] == 48.8125
              and e15[0]['observerCost']['new']['workerCpuSeconds'] is None
              and e15[0]['transport']['new']['completed'] == 0)
    else:
        check('carryforward-v5-actual-drycheck-skipped', True, 'comparison-v5 artifacts absent')

    v5_spawn_calls = []

    def _v5_search_stub(python, plan_path, arm, stage, source_root, copy_path, unit_dir, cap=0,
                        extra=None, seconds=0, workers=1):
        v5_spawn_calls.append((arm, stage))
        return dict(admitted=cap, declared=cap, incomplete=False, failed=False, observedRuns=cap,
                    nomination=dict(nominee='c1'), usedPairs=[[1, 2], [3, 4]])

    real_spawn, real_preflight = harness.spawn_arm, harness.preflight
    harness.spawn_arm = _v5_search_stub
    harness.preflight = lambda plan, python=None, out_root=None, timeout=900: dict(
        gates={}, blocked=[], notes=['stubbed no-battle preflight'])
    try:
        v5_run = harness.run_units(v5_new, PYTHON, work / 'carryforward-v5-run',
                                   stages=('throughput', 'search'), authorise=True, workers=1,
                                   resume_from=v5_stopped, reviewed_source_manifest=base_manifest)
    finally:
        harness.spawn_arm, harness.preflight = real_spawn, real_preflight
    check('run-units-v5-skips-two-failed-pairs-and-runs-ten',
          len(v5_spawn_calls) == 20 and all(stage == 'search' for _arm, stage in v5_spawn_calls)
          and sum(1 for step in v5_run['steps'] if step.get('op') == 'failed-unit') == 2
          and v5_run['computeAccounting']['new']['battles'] == 2560
          and v5_run['ledger']['committed'] == 3169
          and not v5_run['incomplete'])
    check('run-units-v5-reports-both-failed-units-and-total-cost',
          len(v5_run['failedUnits']) == 2
          and {unit['encounter'] for unit in v5_run['failedUnits']} == {0, 15}
          and v5_run['computeAccounting']['prior'] == dict(adoptedBattles=336,
                                                           failedPairBattles=260, chargedTotal=609)
          and v5_run['ledger']['spent'] == 3156 and v5_run['ledger']['setupSpent'] == 13)

    # --- root authorisation: explicit flag accepted, receipt bound to planId+digest --------------
    auth_dir = work / 'auth'
    auth_dir.mkdir(parents=True, exist_ok=True)
    check('authorisation-refuses-without-flag',
          isinstance(refuse(lambda: harness._authorise_plan(plan, auth_dir, authorise=False)),
                     harness.Blocked))
    receipt = harness._authorise_plan(plan, auth_dir, authorise=True)
    check('authorisation-receipt-bound-to-plan',
          receipt['planId'] == plan['planId'] and receipt['planDigest'] == plan['digest']
          and plan['status'] == 'proposed' and plan.get('authorisation') is None
          and (auth_dir / 'authorisation-receipt.json').is_file())
    stale_auth = work / 'auth-stale'
    stale_auth.mkdir(parents=True, exist_ok=True)
    harness.write_json(stale_auth / 'authorisation-receipt.json',
                       dict(planId='other', planDigest=plan['digest']))
    check('authorisation-refuses-stale-receipt',
          isinstance(refuse(lambda: harness._authorise_plan(plan, stale_auth, authorise=True)),
                     harness.Blocked))
    run_source = inspect.getsource(harness.run_units)
    check('run-units-accepts-flag-not-plan-status',
          '_authorise_plan' in run_source and 'status must be' not in run_source)
    check('run-units-refuses-without-flag',
          isinstance(refuse(lambda: harness.run_units(plan, PYTHON, work / 'run-refuse',
                                                      stages=(), authorise=False, workers=1)),
                     harness.Blocked))

    # --- owned process-tree termination and shutdown lifecycle -----------------------------------
    spawn_source = inspect.getsource(harness.spawn_arm)
    check('spawn-arm-owns-its-process-tree',
          'Popen' in spawn_source and '_terminate_owned_process_tree' in spawn_source
          and 'TimeoutExpired' in spawn_source)
    kill_source = inspect.getsource(harness._terminate_owned_process_tree)
    check('owned-tree-kill-is-pid-scoped',
          'taskkill' in kill_source and "'/PID'" in kill_source and "'/T'" in kill_source
          and "'/F'" in kill_source and 'killpg' in kill_source)

    class _Exited:
        pid = 4242

        def poll(self):
            return 0

        def kill(self):
            raise AssertionError('must not kill an already-exited process')

    harness._terminate_owned_process_tree(_Exited())
    check('owned-tree-kill-noop-when-exited', True)

    class _Running:
        pid = 7777

        def __init__(self):
            self.killed = 0

        def poll(self):
            return None

        def kill(self):
            self.killed += 1

    taskkill_calls = []

    def _fake_taskkill(command, **_kwargs):
        taskkill_calls.append(tuple(command))
        return types.SimpleNamespace(returncode=0)

    real_run = subprocess.run
    running = _Running()
    subprocess.run = _fake_taskkill
    try:
        harness._terminate_owned_process_tree(running)
    finally:
        subprocess.run = real_run
    check('owned-tree-kill-uses-exact-taskkill',
          (taskkill_calls == [('taskkill', '/PID', '7777', '/T', '/F')] if os.name == 'nt' else True),
          str(taskkill_calls))
    if os.name == 'nt':
        refused = _Running()
        subprocess.run = lambda command, **kwargs: types.SimpleNamespace(returncode=1)
        try:
            harness._terminate_owned_process_tree(refused)
        finally:
            subprocess.run = real_run
        check('owned-tree-kill-falls-back-on-refusal', refused.killed == 1)
    check('search-keeps-guard-through-shutdown',
          shutdown_source.index("for name in ('stop', 'close')")
          < shutdown_source.index('thread.join')
          < shutdown_source.index('finalise(')
          < shutdown_source.index('_write_meter_snapshot')
          < shutdown_source.index('uninstall()')
          and shutdown_source.index('_write_transport_snapshot') < shutdown_source.index('uninstall()')
          and 'except BaseException as exc' in search_source
          and 'except BaseException as exc' in shutdown_source)
    injected_module = types.SimpleNamespace(**{'HeadlessPool': type('P', (), {
        'submit': lambda self, *a, **k: dict(resultBackend='native')})})
    injected_meter = harness.ExecutionMeter(4, stage='injected')

    def _injected_guard(scenario):
        if scenario.get('encounterId') != 19:
            raise harness.Blocked('escaped dispatch')

    harness.install_bounded_dispatch(injected_meter, pool_module=injected_module,
                                     scenario_validator=_injected_guard)
    escaped = refuse(lambda: injected_module.HeadlessPool().submit(None, dict(encounterId=3), [1, 2]))
    check('failure-injection-no-escaped-dispatch',
          isinstance(escaped, harness.Blocked) and injected_meter.charged == 0)

    # --- resource scope resolves to the ACTUAL frozen stored-reference policy, never a default -----
    new_observed = plan['arms'][harness.ARM_NEW]['observedPolicy']['fields']
    legacy_observed = plan['arms'][harness.ARM_LEGACY]['observedPolicy']['fields']
    check('scope-declares-frozen-stored-arm-policy',
          plan['scope']['tickLimit']['value'] == 30000
          and plan['scope']['tickLimit']['value'] == new_observed['tickLimit']
          and plan['scope']['tickLimit']['value'] == legacy_observed['tickLimit']
          and plan['scope']['finishPolicy']['value'] == new_observed['finishPolicy'])
    check('scope-agrees-with-frozen-meta-no-silent-overlay',
          plan['scope']['tickLimit'].get('metaValue') == 30000
          and not any(row['kind'] == 'meta-vs-observed' for row in plan.get('scopeConflicts') or []))
    drift_plan = json.loads(json.dumps(plan))
    drift_plan['scope']['tickLimit']['value'] = 3000
    drift_plan['digest'] = harness.digest({k: v for k, v in drift_plan.items() if k != 'digest'})
    check('plan-rejects-declared-scope-drift',
          any('disagrees with the declared scope' in error
              for error in harness.validate_plan(drift_plan)))
    guarded = harness.apply_frozen_policy(dict(encounterId=19, tickLimit=1), bound_guard)
    check('holdout-overlay-is-explicit-and-recorded',
          harness.digest(guarded) != harness.digest(dict(encounterId=19, tickLimit=1))
          and guarded['tickLimit'] == bound_guard.policyFields['tickLimit'] == 30000
          and 'dispatchedScenarioDigest' in inspect.getsource(harness.holdout_stage)
          and 'storedScenarioDigest' in inspect.getsource(harness.holdout_stage))

    # --- zero-stock / nonzero-max: the holdout aligns dependent fields like the arm runtime ---------
    stale_nominee = dict(encounterId=19, community=True, holyHerbStock=10, holyHerbMaxUses=2,
                         holyHerbTriggerUnits=['probe fighter'], mpWatchUnits=['probe fighter'])
    normalized = harness.apply_frozen_policy(stale_nominee, bound_guard)
    check('holdout-drops-stale-herb-cap-when-policy-is-stock-zero',
          normalized['holyHerbStock'] == 0 and 'holyHerbMaxUses' not in normalized
          and 'holyHerbTriggerUnits' not in normalized and 'mpWatchUnits' not in normalized
          and stale_nominee['holyHerbMaxUses'] == 2 and stale_nominee['holyHerbStock'] == 10,
          'the frozen policy declares the herb unavailable with no use cap')
    guard_error = refuse(lambda: bound_guard(normalized))
    check('holdout-normalized-scenario-passes-guard', guard_error is None)
    check('guard-alignment-declares-no-herb-cap',
          'holyHerbMaxUses' not in bound_guard.alignmentFields
          and bound_guard.alignmentFields['holyHerbStock'] == 0)
    # The alignment copies a declared dependent field, drops an undeclared one, and never invents or
    # mutates: the combat source/policy (stock and the six policy dimensions) is left untouched.
    aligned = harness.align_scenario_policy(
        dict(holyHerbStock=10, holyHerbMaxUses=2, holyHerbTriggerUnits=['x'], untouched=1),
        dict(holyHerbStock=3, holyHerbMaxUses=1))
    check('align-copies-declared-drops-undeclared-keeps-other-fields',
          aligned['holyHerbStock'] == 3 and aligned['holyHerbMaxUses'] == 1
          and 'holyHerbTriggerUnits' not in aligned and aligned['untouched'] == 1)
    check('align-idempotent-and-noninventive',
          harness.align_scenario_policy(aligned, dict(holyHerbStock=3, holyHerbMaxUses=1)) == aligned)

    # --- same-plan ledger adoption: a settled search stage is reused, never re-charged -------------
    resume_ledger_path = work / 'resume-ledger.json'
    resume_ledger = harness.BudgetLedger(resume_ledger_path, 4976, setup_cap=32)
    for stage in ('search:legacy:18:r1', 'search:new:18:r1'):
        reservation = resume_ledger.reserve(stage, 128)
        resume_ledger.settle(reservation, 128)
    resume_ledger.charge_setup('carryforward:throughput:legacy-dispatch', 13)
    check('resume-ledger-adopts-settled-search-stages',
          {'search:legacy:18:r1', 'search:new:18:r1'} <= resume_ledger.settled_stages()
          and resume_ledger.spent == 256 and resume_ledger.setup_spent == 13
          and not resume_ledger.reserved)
    check('resume-ledger-settled-stages-not-reissuable-unknown',
          'holdout:legacy:18:r1' not in resume_ledger.settled_stages())

    # --- no duplicate search/holdout or seed reissue: an issued label is adopted, not reissued ------
    adopt_path = work / 'registry-adopt.json'
    issued = harness.HoldoutRegistry(adopt_path, 'pid', 'nonce').issue('holdout|18|1', 6,
                                                                       forbidden={(1, 2)})
    again = harness.HoldoutRegistry(adopt_path, 'pid', 'nonce').issue_or_adopt(
        'holdout|18|1', 6, forbidden={(1, 2)}, used_seeds=[])
    check('registry-adopts-issued-label-without-reissue', again == issued)
    check('registry-refuses-adopted-label-colliding-with-library-history',
          isinstance(refuse(lambda: harness.HoldoutRegistry(adopt_path, 'pid', 'nonce')
                            .issue_or_adopt('holdout|18|1', 6,
                                            forbidden={issued[0]})), harness.Blocked))
    check('registry-refuses-adopted-label-when-a-seed-was-used',
          isinstance(refuse(lambda: harness.HoldoutRegistry(adopt_path, 'pid', 'nonce')
                            .issue_or_adopt('holdout|18|1', 6, forbidden=set(),
                                            used_seeds=[list(issued[0])])), harness.Blocked))
    check('registry-refuses-adopted-label-with-wrong-count',
          isinstance(refuse(lambda: harness.HoldoutRegistry(adopt_path, 'pid', 'nonce')
                            .issue_or_adopt('holdout|18|1', 5, forbidden=set())), harness.Blocked))
    tampered = json.loads(adopt_path.read_text(encoding='utf-8'))
    tampered['planId'] = 'a-different-plan'
    tamper_path = work / 'registry-tampered.json'
    tamper_path.write_text(json.dumps(tampered), encoding='utf-8')
    check('registry-tamper-fails-closed',
          isinstance(refuse(lambda: harness.HoldoutRegistry(tamper_path, 'pid', 'nonce')),
                     harness.Blocked))

    # --- same-plan resume state is validated fail-closed; an admitted holdout aborts ----------------
    resume_root = work / 'resume-root'
    resume_root.mkdir(parents=True, exist_ok=True)
    harness.write_json(resume_root / 'plan.json', plan)
    harness.write_json(resume_root / 'authorisation-receipt.json',
                       dict(kind='encounter-redesign-benchmark-authorisation-1',
                            planId=plan['planId'], planDigest=plan['digest'],
                            authorisedBy='root:--authorised', scope='run-plan'))
    resume_ledger2 = harness.BudgetLedger(resume_root / 'battle-ledger.json',
                                          plan['budget']['hardCeiling'],
                                          setup_cap=plan['budget']['setupCap'])
    reservation = resume_ledger2.reserve('search:legacy:18:r1', 128)
    resume_ledger2.settle(reservation, 128)
    reservation = resume_ledger2.reserve('search:new:18:r1', 128)
    resume_ledger2.settle(reservation, 128)
    resume_ledger2.charge_setup('prior', 13)
    # Re-create the prior ledger as the stopped run would have left it (852 regular + 13 setup).
    resume_ledger2.reserve('prior-fill', 596)
    prior = harness.BudgetLedger(resume_root / 'battle-ledger.json', plan['budget']['hardCeiling'],
                                 setup_cap=plan['budget']['setupCap'])
    prior.spent = 852
    prior.setup_spent = 13
    prior.reserved = {}
    prior._persist()
    prior = harness.BudgetLedger(resume_root / 'battle-ledger.json', plan['budget']['hardCeiling'],
                                 setup_cap=plan['budget']['setupCap'])
    prior_receipt = dict(schema=harness.CARRYFORWARD_V5_SCHEMA, priorTotal=609,
                         newPlan=dict(planId=plan['planId'], digest=plan['digest']),
                         failedPairs=[dict(encounter=0, replicate=1),
                                      dict(encounter=15, replicate=1)])
    check('resume-state-accepts-the-stopped-shape',
          harness.check_current_resume_state(plan, resume_root, prior, prior_receipt) == [])
    admitted_dir = resume_root / f'{harness.ARM_LEGACY}-e18-r1'
    admitted_dir.mkdir(parents=True, exist_ok=True)
    harness.write_json(admitted_dir / 'meter-holdout-e18-legacy.json',
                       dict(admitted=5, usedPairs=5))
    check('resume-state-aborts-on-admitted-partial-holdout',
          any('admitted holdout' in problem
              for problem in harness.check_current_resume_state(plan, resume_root, prior,
                                                                prior_receipt)))
    harness.write_json(admitted_dir / 'holdout-legacy-e18-r1.json', dict(stage='holdout'))
    check('resume-state-aborts-on-existing-holdout-outcome',
          any('holdout artifact already exists' in problem
              for problem in harness.check_current_resume_state(plan, resume_root, prior,
                                                                prior_receipt)))
    prior.reserved[99] = dict(stage='partial', cap=1, actual=None)
    check('resume-state-aborts-on-outstanding-reservation',
          any('outstanding reservation' in problem
              for problem in harness.check_current_resume_state(plan, resume_root, prior,
                                                                prior_receipt)))

    # --- observer rebind for a same-plan resume: only the repaired runner file may move ------------
    live_obs = harness.observer_inventory()
    frozen_obs_files = dict(live_obs['files'])
    frozen_obs_files['benchmark_encounter_redesign.py'] = 'old-harness-hash'
    inv = dict(digest='stale', files={'impl/strategy_optimizer.py': 'same'}, missing=[],
               nativePaths={}, ok=True, observerFiles=frozen_obs_files, observerRevision='old-rev')
    synth_plan = {'arms': {arm: {'implementationInventory': dict(inv)} for arm in harness.ARMS}}
    _rebind_path, deviation = harness.prepare_current_resume_observer(synth_plan, work)
    check('observer-rebind-accepts-only-the-harness-file',
          deviation['changedObserverFiles'] == ['benchmark_encounter_redesign.py']
          and harness._CURRENT_RESUME_OBSERVER_REBIND is not None)
    tampered_obs = dict(frozen_obs_files)
    tampered_obs['strategy_outcomes.py'] = 'different-module'
    synth_tampered = {'arms': {arm: {'implementationInventory': dict(inv, observerFiles=tampered_obs)}
                               for arm in harness.ARMS}}
    check('observer-rebind-refuses-a-second-source-change',
          isinstance(refuse(lambda: harness.prepare_current_resume_observer(synth_tampered, work)),
                     harness.Blocked))
    live_full = harness._implementation_inventory_record(
        dict(ok=True, files={'impl/strategy_optimizer.py': 'same'}, missing=[], nativePaths={}),
        canonical_files={}, observer=live_obs)
    frozen_full = dict(live_full, observerFiles=frozen_obs_files, observerRevision='old-rev')
    frozen_full['digest'] = harness.digest(
        {k: v for k, v in frozen_full.items() if k not in ('digest', 'ok')})
    check('observer-rebind-validates-only-the-observer-moved',
          harness.validate_implementation_inventory(frozen_full, live_full, arm='legacy') == [])
    changed_file = dict(frozen_full, files={'impl/strategy_optimizer.py': 'DIFFERENT'})
    check('observer-rebind-still-flags-a-real-file-change',
          any('file changed after the plan' in problem
              for problem in harness.validate_implementation_inventory(changed_file, live_full,
                                                                        arm='legacy')))
    harness._set_current_resume_observer(None)

    selftest = harness.selftest(work / 'selftest')
    check('harness-selftest-ok', selftest['ok'], str(selftest.get('checks')))

    checks.extend(run_detached_checks(work / 'detached'))

    ok = all(row['ok'] for row in checks)
    passed = sum(1 for row in checks if row['ok'])
    print(json.dumps(dict(ok=ok, passed=passed, total=len(checks), checks=checks), indent=2))
    return 0 if ok else 1


def _detached_pair_bank(start, count):
    return [(start + index, start + 100000 + index) for index in range(count)]


def _detached_raw(pair, encounter_id, nominee):
    return dict(candidateId=nominee, encounterId=encounter_id, seeds=list(pair),
                raw=dict(seeds=list(pair), resultBackend='native', cpuSeconds=0.25,
                         elapsedSeconds=0.5, verdict=1, censored=False, resourceUses=0,
                         survivors=6, ticks=100, prizeCallbacks=2,
                         rewardOutcome=dict(pendingChests=2, awardedChests=2,
                                            awardedBasis='reward-entitlement-certificate')))


def _detached_completed_unit(root, plan, arm, encounter_id, pairs, search_pairs, nominee):
    unit = root / f'{arm}-e{encounter_id}-r1'
    unit.mkdir(parents=True, exist_ok=True)
    harness.write_json(unit / f'search-{arm}-e{encounter_id}-r1.json', dict(
        arm=arm, stage='search', encounterId=encounter_id, replicate=1, failed=False,
        incomplete=False, admitted=128, declared=128, nomination=dict(nominee=nominee),
        usedPairs=[list(pair) for pair in search_pairs]))
    harness.write_json(unit / f'nomination-{arm}-e{encounter_id}-r1.json', dict(
        arm=arm, encounterId=encounter_id, replicate=1, nominee=nominee))
    harness.write_json(unit / f'holdout-{arm}-e{encounter_id}-r1.json', dict(
        arm=arm, stage='holdout', encounterId=encounter_id, replicate=1, admitted=64, declared=64,
        incomplete=False, nominee=nominee, nomineeOnly=True, pairs=64, rawRows=64))
    harness.write_json(unit / f'meter-search-e{encounter_id}-{arm}.json',
                       dict(arm=arm, stage=f'search-e{encounter_id}', admitted=128, cap=128))
    harness.write_json(unit / f'meter-holdout-e{encounter_id}-{arm}.json',
                       dict(arm=arm, stage=f'holdout-e{encounter_id}', admitted=64, cap=64))
    raw_rows = [_detached_raw(pair, encounter_id, nominee) for pair in pairs]
    (unit / f'raw-holdout-{arm}-e{encounter_id}-r1.jsonl').write_text(
        '\n'.join(json.dumps(row) for row in raw_rows) + '\n', encoding='utf-8')
    # evaluate_holdout writes the canonical outcome jsonl the adoption later reproduces and checks.
    harness.evaluate_holdout(plan, arm, unit, encounter_id, 1, pairs, nominee, raw_rows)
    return unit


def _build_detached_stopped(root, plan):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    harness.write_json(root / 'plan.json', plan)
    harness.write_json(root / 'authorisation-receipt.json', dict(
        kind='encounter-redesign-benchmark-authorisation-1', planId=plan['planId'],
        planDigest=plan['digest'], authorisedBy='root:--authorised', scope='run-plan'))
    carry = dict(schema=harness.CARRYFORWARD_V5_SCHEMA, priorTotal=609,
                 newPlan=dict(planId=plan['planId'], digest=plan['digest']),
                 failedPairs=[dict(encounter=0, replicate=1), dict(encounter=15, replicate=1)])
    harness.write_json(root / 'carryforward-receipt.json', carry)
    claims = [dict(op='settle', stage=stage, actual=0, spent=0) for stage in (
        'search:legacy:18:r1', 'search:new:18:r1', 'search:legacy:19:r1', 'search:new:19:r1',
        'holdout:legacy:18:r1', 'holdout:new:18:r1', 'holdout:legacy:19:r1', 'holdout:new:19:r1')]
    claims.append(dict(op='reserve', id=20, stage='search:legacy:0:r2', cap=128, spent=1492))
    harness.write_json(root / 'battle-ledger.json', dict(
        schema=harness.LEDGER_SCHEMA, ceiling=plan['budget']['hardCeiling'],
        setupCap=plan['budget']['setupCap'], spent=1492, setupSpent=13,
        reserved={'20': dict(stage='search:legacy:0:r2', cap=128, actual=None)},
        claims=claims, nextReservation=21))
    registry = harness.HoldoutRegistry(root / 'holdout-registry.json', plan['planId'],
                                       plan['holdout']['globalNonce'])
    banks = {18: registry.issue('holdout|18|1', 64, forbidden=set()),
             19: registry.issue('holdout|19|1', 64, forbidden=set())}
    for encounter_id, pairs in banks.items():
        for arm in harness.ARMS:
            _detached_completed_unit(root, plan, arm, encounter_id, pairs,
                                     _detached_pair_bank(700000 + (1000 if arm == 'new' else 0), 128),
                                     f'nominee-{arm}-{encounter_id}')
    return root, carry


def run_detached_checks(work=None):
    """Targeted synthetic checks for the bounded ``resume-detached`` checkpoint. No spawn, no copy,
    no battle: every artifact is a synthetic fixture and every assertion is deterministic."""
    checks = []

    def check(name, condition, detail=''):
        checks.append(dict(check=name, ok=bool(condition), detail=str(detail)))

    def refuse(call):
        try:
            call()
        except Exception as exc:  # noqa: BLE001
            return exc
        return None

    work = Path(work or (HERE.parents[1] / 'tmp' / 'encounter-redesign-20260928'
                         / 'check-detached-work'))
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True, exist_ok=True)
    baseline, evidence = harness._synthetic_fixture(work / 'fixture')
    plan = harness.build_plan(harness._Args(baseline=str(baseline), evidence=str(work / 'fixture'),
                                            encounters='0,15,18,19'), python=PYTHON)
    root, carry = _build_detached_stopped(work / 'stopped', plan)
    ceiling, setup_cap = plan['budget']['hardCeiling'], plan['budget']['setupCap']

    ledger = harness.BudgetLedger(root / 'battle-ledger.json', ceiling, setup_cap=setup_cap)
    registry = harness.HoldoutRegistry(root / 'holdout-registry.json', plan['planId'],
                                       plan['holdout']['globalNonce'])
    auth = harness.read_json(root / 'authorisation-receipt.json')
    ctx = harness.prepare_detached_checkpoint(plan, root, ledger, registry, carry, auth)
    check('detached-validates-and-snapshots-four-artifact-sets',
          len(ctx['completed']) == 4 and (root / 'detached-recovery-receipt.json').is_file())
    check('detached-completed-payloads-match-128-64',
          all(entry['admitted'] == dict(search=128, holdout=64) for entry in ctx['completed'])
          and all(entry['nominee'] for entry in ctx['completed'])
          and {entry['arm'] for entry in ctx['completed']} == set(harness.ARMS)
          and {entry['encounter'] for entry in ctx['completed']} == {18, 19})
    check('detached-recovery-receipt-hashes-artifacts',
          all(entry[name]['sha256'] and len(entry[name]['sha256']) == 64 for entry in ctx['completed']
              for name in ('search-{}-e{}-r1.json'.format(entry['arm'], entry['encounter']),
                           'holdout-{}-e{}-r1.json'.format(entry['arm'], entry['encounter']),
                           'outcome-holdout-{}-e{}-r1.jsonl'.format(entry['arm'], entry['encounter']),
                           'nomination-{}-e{}-r1.json'.format(entry['arm'], entry['encounter']))))
    check('detached-seed-pairs-bound-to-issued-registry',
          sorted(len(entry['registryPairs']) for entry in ctx['completed']) == [64, 64, 64, 64]
          and all(len(entry['registryPairsDigest']) == 64 for entry in ctx['completed']))
    check('detached-untouched-and-abandoned-pairs',
          len(ctx['untouchedPairs']) == 7 and ctx['abandonedPairs'] == [[0, 2]]
          and ctx['expectedFinal'] == 4193)
    check('detached-abandon-unknown-no-refund-no-actual',
          ledger.spent == 1492 and ledger.setup_spent == 13 and ledger.reserved == {}
          and ledger.committed == 1505)
    abandon = [claim for claim in ledger.claims if claim.get('op') == 'abandon-unknown']
    check('detached-abandon-record-shape',
          len(abandon) == 1 and abandon[0]['actual'] is None
          and abandon[0]['chargedUpperBound'] == 128 and abandon[0]['unknownAdmission'] is True
          and abandon[0]['stage'] == 'search:legacy:0:r2' and abandon[0]['spent'] == 1492)
    check('detached-adoption-creates-no-unit-copy',
          not (root / 'legacy-e18-r1' / harness.UNIT_COPY_NAME).exists()
          and not (root / 'new-e19-r1' / harness.UNIT_COPY_NAME).exists())

    # Restart receipt: re-validating the unchanged stopped run never double charges.
    ledger2 = harness.BudgetLedger(root / 'battle-ledger.json', ceiling, setup_cap=setup_cap)
    registry2 = harness.HoldoutRegistry(root / 'holdout-registry.json', plan['planId'],
                                        plan['holdout']['globalNonce'])
    ctx2 = harness.prepare_detached_checkpoint(plan, root, ledger2, registry2, carry, auth)
    check('detached-restart-no-double-charge',
          ctx2['abandonAlreadyApplied'] is True and ctx2['abandonAppliedNow'] is False
          and ledger2.spent == 1492 and ledger2.reserved == {}
          and len([claim for claim in ledger2.claims
                   if claim.get('op') == 'abandon-unknown']) == 1)

    # Completed pairs are adopted into the result units BEFORE the copy/work loop, and skipped.
    results = dict(units=[], steps=[])
    search_reports = {}
    completed, abandoned = harness.adopt_detached_completed(plan, root, results, search_reports,
                                                            ctx2, ledger2)
    check('detached-completed-adopted-before-loop',
          completed == {(18, 1), (19, 1)} and abandoned == {(0, 2)})
    check('detached-adoption-units-and-prior-compute',
          len([unit for unit in results['units'] if unit.get('stage') == 'search']) == 4
          and len([unit for unit in results['units'] if unit.get('stage') == 'holdout']) == 4
          and all(unit.get('compute') == 'prior' for unit in results['units']))
    check('detached-adoption-skipped-interrupted-pair',
          not any(int(step.get('encounterId', -1)) == 0 and int(step.get('replicate', -1)) == 2
                  for step in results['steps']))

    # Full 4193 accounting after the seven untouched pairs run in full.
    ledger3 = harness.BudgetLedger(root / 'battle-ledger.json', ceiling, setup_cap=setup_cap)
    registry3 = harness.HoldoutRegistry(root / 'holdout-registry.json', plan['planId'],
                                        plan['holdout']['globalNonce'])
    harness.prepare_detached_checkpoint(plan, root, ledger3, registry3, carry, auth)
    for item in ctx['untouchedPairs']:
        encounter_id, replicate = int(item['encounter']), int(item['replicate'])
        for arm in harness.ARMS:
            ledger3.settle(ledger3.reserve(f'search:{arm}:{encounter_id}:r{replicate}', 128), 128)
            ledger3.settle(ledger3.reserve(f'holdout:{arm}:{encounter_id}:r{replicate}', 64), 64)
    check('detached-final-charged-4193',
          ledger3.spent + ledger3.setup_spent == 4193 == harness.DETACHED_FINAL_CHARGED)

    # A tampered artifact changes the snapshot and is refused without a refund.
    tampered_root = work / 'tampered'
    shutil.copytree(root, tampered_root)
    target = tampered_root / 'legacy-e18-r1' / 'search-legacy-e18-r1.json'
    target.write_text(target.read_text(encoding='utf-8') + ' \n', encoding='utf-8')
    ledger4 = harness.BudgetLedger(tampered_root / 'battle-ledger.json', ceiling, setup_cap=setup_cap)
    registry4 = harness.HoldoutRegistry(tampered_root / 'holdout-registry.json', plan['planId'],
                                        plan['holdout']['globalNonce'])
    check('detached-refuses-tampered-artifact',
          isinstance(refuse(lambda: harness.prepare_detached_checkpoint(
              plan, tampered_root, ledger4, registry4, carry, auth)), harness.Blocked))
    other_root = work / 'other-plan'
    shutil.copytree(root, other_root)
    bad_plan = json.loads(json.dumps(plan))
    bad_plan['planId'] = 'a-different-plan'
    harness.write_json(other_root / 'plan.json', bad_plan)
    ledger5 = harness.BudgetLedger(other_root / 'battle-ledger.json', ceiling, setup_cap=setup_cap)
    registry5 = harness.HoldoutRegistry(other_root / 'holdout-registry.json', plan['planId'],
                                        plan['holdout']['globalNonce'])
    check('detached-refuses-a-different-plan',
          isinstance(refuse(lambda: harness.prepare_detached_checkpoint(
              plan, other_root, ledger5, registry5, carry, auth)), harness.Blocked))
    return checks


def _cli(args):
    return subprocess.run([PYTHON, '-B', '-X', 'utf8', str(HERE / 'benchmark_encounter_redesign.py')]
                          + args, capture_output=True, text=True)


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] in ('detached', '--detached'):
        _payload = run_detached_checks()
        _ok = all(row['ok'] for row in _payload)
        print(json.dumps(dict(ok=_ok, passed=sum(1 for row in _payload if row['ok']),
                              total=len(_payload), checks=_payload), indent=2))
        raise SystemExit(0 if _ok else 1)
    raise SystemExit(main())
