"""Focused checks for the all-strategy proposal service.

Run:  <venv>/python -B -X utf8 tools/recovery/check_stream_proposals.py
Deterministic, read-only, no battles, no SQLite. Prints PASS/FAIL per check and exits non-zero on
failure. The checks exercise the REAL registered generators (synthetic_root, rebel_child,
adapter.propose, plan_arms) and the shared joint mechanics - not a mock label mapper.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import search_contract as contract            # noqa: E402
import strategy_build_domain as domain        # noqa: E402
import strategy_joint_proposals as joint      # noqa: E402
import strategy_mechanics as mechanics        # noqa: E402
import strategy_students as students          # noqa: E402
import strategy_stream_proposals as service   # noqa: E402

WORKSPACE = HERE.parents[2]
SCENARIO_PATH = WORKSPACE / 'RE-evidence/20260920-user-wairo-strategy/scenarios/UREF.scenario.json'
FORBIDDEN = ('reward', 'earned', 'certificate', 'queue', 'reentry', 're-entry', 'verdict',
             'telemetry', 'holdout')
REQUIRED = frozenset(('owner', 'parentId', 'childIdentity', 'strategyBasis', 'changedFields',
                      'features', 'counterfactual', 'plannedBudget', 'stopping'))
RESULTS = []


def check(name, condition, detail=''):
    RESULTS.append(dict(name=name, ok=bool(condition), detail=detail))
    print(('PASS' if condition else 'FAIL') + ' ' + name + ((' :: ' + str(detail)) if detail else ''))


def load_scenario():
    return json.loads(SCENARIO_PATH.read_text(encoding='utf-8'))


def main():
    scenario = load_scenario()
    parent = mechanics.normalize_scenario(scenario)
    parent_id = domain.identity(parent)
    started = time.time()

    # --- registry / module guard ---------------------------------------------------------------
    check('imports-no-battle-or-optimiser-modules',
          'combat_sandbox' not in sys.modules and 'strategy_optimizer' not in sys.modules)
    check('registry-read-from-students',
          service.REGISTERED_OWNERS == tuple(students.SHARE_STREAMS)
          and service.DECLARED_OWNERS == tuple(students.STUDENT_NAMES),
          str(service.REGISTERED_OWNERS))
    check('supported-owners-exclude-retired-and-inactive',
          all(name in service.supported_owners() for name in
              ('community', 'discovery', 'rebel', 'stumble', 'mechanism'))
          and students.STUDENT_AVERAGE not in service.supported_owners()
          and students.STUDENT_FREEWILL not in service.supported_owners(),
          str(service.supported_owners()))

    # --- community: unchanged strict pool ------------------------------------------------------
    diag = {}
    community = service.proposal_pool('community', scenario, maximum=6, diagnostics=diag)
    strict = joint.pool(scenario, maximum=6)
    check('community-delegates-to-strict-pool-unchanged',
          bool(community) and {row['id'] for row in community} == {row['id'] for row in strict}
          and {row['childIdentity'] for row in community} == {row['childIdentity'] for row in strict}
          and all(row['owner'] == 'community' for row in community),
          'n=%d strict=%d' % (len(community), len(strict)))
    check('community-rows-declare-required-fields',
          all(REQUIRED <= set(row) and row['parentId'] == parent_id for row in community)
          and all(row['strategyBasis']['generator'] == 'strategy_joint_proposals.pool'
                  for row in community))
    check('community-path-no-battle-or-optimiser-imports',
          'combat_sandbox' not in sys.modules and 'strategy_optimizer' not in sys.modules)

    # --- guards: average, freewill, unknown, allocation ----------------------------------------
    average_codes = set()
    for config in (None, {'mode': 'community-first'}, {'mode': 'all-strategy'}):
        diag = {}
        rows = service.proposal_pool(students.STUDENT_AVERAGE, scenario, diagnostics=diag,
                                     config=config)
        average_codes.add(diag.get('code'))
        if not (rows == [] and diag.get('reason')):
            average_codes.add('BAD')
    check('average-rejected-in-every-mode', average_codes == {'retired-owner'}, str(average_codes))
    diag = {}
    check('freewill-rejected-inactive',
          service.proposal_pool(students.STUDENT_FREEWILL, scenario, diagnostics=diag) == []
          and diag.get('code') == 'inactive-owner', diag.get('code'))
    diag = {}
    check('unknown-owner-rejected',
          service.proposal_pool('not-a-stream', scenario, diagnostics=diag) == []
          and diag.get('code') == 'unknown-owner', diag.get('code'))
    guarded = True
    for name in set(students.SHARE_STREAMS) | set(students.STUDENT_NAMES):
        d = {}
        rows = service.proposal_pool(name, scenario, maximum=2, diagnostics=d)
        if name in (students.STUDENT_AVERAGE, students.STUDENT_FREEWILL):
            guarded = guarded and rows == [] and d.get('code') in ('retired-owner', 'inactive-owner')
        else:
            guarded = guarded and (bool(rows) or bool(d.get('reason')))
    check('every-registered-name-guarded', guarded)
    zero_ok = True
    for owner in ('community', 'discovery', 'rebel'):
        d = {}
        rows = service.proposal_pool(owner, scenario, allocation=0, diagnostics=d)
        zero_ok = zero_ok and rows == [] and d.get('code') == 'zero-allocation'
    d = {}
    positive = service.proposal_pool('community', scenario, allocation=1.0, maximum=2,
                                     diagnostics=d)
    check('zero-allocation-refused-and-positive-checked',
          zero_ok and bool(positive) and d.get('code') == 'ok')

    # --- community-first calls no other generator ----------------------------------------------
    calls = []
    original = service.stream_generator
    service.stream_generator = lambda owner: (calls.append(owner), original(owner))[1]
    try:
        blocked = []
        for owner in ('discovery', 'rebel', 'stumble', 'mechanism'):
            d = {}
            rows = service.proposal_pool(owner, scenario, config={'mode': 'community-first'},
                                         maximum=2, diagnostics=d)
            blocked.append(rows == [] and d.get('code') == 'community-only-mode')
        still_community = service.proposal_pool('community', scenario, maximum=2,
                                                config={'mode': 'community-first'})
    finally:
        service.stream_generator = original
    check('community-first-calls-no-other-generator',
          all(blocked) and calls == [] and bool(still_community), 'generator calls=%r' % (calls,))

    # --- discovery: real synthetic root --------------------------------------------------------
    import strategy_synthetic_roots as roots
    check('discovery-resolves-actual-synthetic-root',
          service.stream_generator('discovery') is roots.synthetic_root
          and service.generator_name('discovery') == 'strategy_synthetic_roots.synthetic_root')
    discovery = service.proposal_pool('discovery', scenario, maximum=4, round_index=0)
    expected_root = roots.synthetic_root(scenario, 0, contract.enemy_count(parent['encounterId']))[0]
    check('discovery-refines-a-real-synthetic-root-seed',
          bool(discovery)
          and discovery[0]['strategyBasis']['seedId'] == domain.identity(expected_root)
          and discovery[0]['strategyBasis']['generator'] == 'strategy_synthetic_roots.synthetic_root'
          and 'seedRoles' in discovery[0]['strategyBasis']
          and all(row['seedId'] != row['childIdentity'] for row in discovery),
          'n=%d roles=%s' % (len(discovery), discovery[0]['strategyBasis'].get('seedRoles')))

    # --- stumble: real blind mutation ----------------------------------------------------------
    import strategy_optimizer_adapter as adapter
    check('stumble-resolves-actual-blind-mutation',
          service.stream_generator('stumble') is adapter.propose
          and service.generator_name('stumble') == 'strategy_optimizer_adapter.propose')
    stumble = service.proposal_pool('stumble', scenario, maximum=3, round_index=0)
    expected_mutation = adapter.propose(scenario, 1)
    check('stumble-refines-a-real-mutation-seed',
          bool(stumble)
          and stumble[0]['strategyBasis']['seedId'] == domain.identity(expected_mutation)
          and all(row['seedId'] != row['childIdentity'] for row in stumble), 'n=%d' % len(stumble))

    # --- rebel: real tier + actual broken-rule ledger ------------------------------------------
    check('rebel-resolves-actual-rebel-child',
          service.stream_generator('rebel') is students.rebel_child)
    tier_names = {tier.name: tier for tier in students.tiers()}
    rebel_ok, seen_tiers = True, set()
    for round_index in range(4):
        rows = service.proposal_pool('rebel', scenario, maximum=3, round_index=round_index)
        if not rows:
            rebel_ok = False
            continue
        for row in rows:
            basis = row['strategyBasis']
            seen_tiers.add(basis.get('tier'))
            if basis.get('tier') not in tier_names or not basis.get('brokenRules'):
                rebel_ok = False
                continue
            tier = tier_names[basis['tier']]
            broken = set(students.rule_breaks(row['scenario'], parent))
            if broken != set(basis['brokenRules']) or not tier.takes(broken):
                rebel_ok = False
    check('rebel-records-and-respects-actual-tier-and-rules',
          rebel_ok and len(seen_tiers) == 4, 'tiers=%s' % sorted(seen_tiers))

    # --- mechanism: real plan_arms control -----------------------------------------------------
    import strategy_breakthrough as breakthrough
    check('mechanism-resolves-actual-plan-arms',
          service.stream_generator('mechanism') is breakthrough.plan_arms)
    mechanism = service.proposal_pool('mechanism', scenario, maximum=3, round_index=0)
    basis = mechanism[0]['strategyBasis'] if mechanism else {}
    check('mechanism-records-diagnostic-question-and-control',
          bool(mechanism) and bool(basis.get('diagnosticQuestion'))
          and basis.get('counterfactualArm') == 'A'
          and basis.get('counterfactualId') == parent_id
          and 'deltas' in mechanism[0]['counterfactual'], str(basis.get('arm')))

    # --- shared row contract for every non-community owner -------------------------------------
    rows = community + discovery + stumble + mechanism
    for round_index in range(4):
        rows += service.proposal_pool('rebel', scenario, maximum=3, round_index=round_index)
    shape_ok = all(REQUIRED <= set(row) for row in rows)
    exact = all(row['childIdentity'] == domain.identity(row['scenario'])
                and row['parentId'] == parent_id for row in rows)
    flat = all(isinstance(value, float) for row in rows for value in row['features'].values())
    finite = all(row['plannedBudget']['finite'] and 0 < row['stopping']['maxRuns'] < 10 ** 6
                 for row in rows)
    clean = all(not any(word in name.lower() for word in FORBIDDEN)
                for row in rows for name in row['features'])
    check('noncommunity-rows-declare-required-fields', shape_ok, 'n=%d' % len(rows))
    check('child-and-parent-ids-are-exact', exact)
    check('features-flat-numeric-telemetry-free', flat and clean)
    check('planned-budget-and-stopping-finite', finite)
    mechanical = all(row['expectedMechanicalDifferences'].get('critRateChange') is not None
                     and row['expectedMechanicalDifferences'].get('intervalChange') is not None
                     for row in discovery + stumble + mechanism)
    check('shared-mechanics-refinement-ran', mechanical)

    # --- bounded, deterministic, diagnostics ---------------------------------------------------
    check('maximum-is-finite-and-capped',
          len(service.proposal_pool('discovery', scenario, maximum=2)) <= 2
          and len(service.proposal_pool('community', scenario, maximum=3)) <= 3)
    stable = True
    for owner in ('discovery', 'stumble', 'rebel', 'mechanism'):
        first = service.proposal_pool(owner, scenario, maximum=3, round_index=0)
        second = service.proposal_pool(owner, scenario, maximum=3, round_index=0)
        stable = stable and bool(first) and [r['id'] for r in second] == [r['id'] for r in first]
    check('deterministic-repeat', stable)
    d = {}
    check('zero-budget-returns-explicit-reason',
          service.proposal_pool('discovery', scenario, maximum=0, diagnostics=d) == []
          and d.get('code') == 'zero-budget' and bool(d.get('reason')), d.get('reason'))
    broken = json.loads(json.dumps(scenario))
    broken['ownUnits'][0]['skills'] = broken['ownUnits'][0]['skills'] + [37]
    d = {}
    check('no-fallback-returns-explicit-reason',
          service.proposal_pool('community', broken, diagnostics=d) == []
          and bool(d.get('reason')), d.get('code'))

    failed = [row['name'] for row in RESULTS if not row['ok']]
    print('\n%d/%d checks passed (%.1fs)' % (len(RESULTS) - len(failed), len(RESULTS),
                                             time.time() - started))
    if failed:
        print('FAILED: ' + ', '.join(failed))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
