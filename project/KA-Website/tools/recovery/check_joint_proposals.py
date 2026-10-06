"""Focused checks for the joint proposal service.

Run:  <venv>/python -B -X utf8 tools/recovery/check_joint_proposals.py
Deterministic, read-only, no battles, no SQLite. Prints PASS/FAIL per check.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import strategy_build_domain as domain        # noqa: E402
import strategy_encounter_adviser as adviser  # noqa: E402
import strategy_joint_proposals as proposals  # noqa: E402
import strategy_mechanics as mechanics        # noqa: E402
import strategy_students as students          # noqa: E402

WORKSPACE = HERE.parents[2]
SCENARIO_PATH = WORKSPACE / 'RE-evidence/20260920-user-wairo-strategy/scenarios/UREF.scenario.json'
FORBIDDEN = ('reward', 'earned', 'certificate', 'queue', 'reentry', 're-entry', 'verdict',
             'telemetry', 'holdout')
RESULTS = []


def check(name, condition, detail=''):
    RESULTS.append(dict(name=name, ok=bool(condition), detail=detail))
    print(('PASS' if condition else 'FAIL') + ' ' + name + ((' :: ' + str(detail)) if detail else ''))


def load_scenario():
    return json.loads(SCENARIO_PATH.read_text(encoding='utf-8'))


def feature_dict(proposal):
    return proposal['expectedMechanicalDifferences']['dpsEffectiveChanges']


def dps_change(proposal, name):
    return proposal['expectedMechanicalDifferences']['dpsEffectiveChanges'].get(name)


def main():
    scenario = load_scenario()
    parent = mechanics.normalize_scenario(scenario)
    parent_id = domain.identity(parent)
    started = time.time()
    base = proposals.pool(scenario, maximum=64, round_index=0)
    elapsed = time.time() - started

    check('fixture-parent-is-community-admitted', students.community_admits(parent)[0] is True)
    check('pool-produces-candidates', len(base) > 0, 'n=%d in %.2fs' % (len(base), elapsed))
    check('no-battle-or-optimiser-imports',
          'combat_sandbox' not in sys.modules
          and 'strategy_optimizer_adapter' not in sys.modules
          and not any(name.startswith('strategy_optimizer') for name in sys.modules))
    check('maximum-is-finite-and-capped',
          len(proposals.pool(scenario, maximum=64)) <= 64
          and len(proposals.pool(scenario, maximum=3)) <= 3)
    check('no-filler-repeated-parent',
          all(row['childIdentity'] != parent_id for row in base)
          and len({row['childIdentity'] for row in base}) == len(base))
    check('child-identity-is-domains-exact-identity',
          all(row['childIdentity'] == domain.identity(row['scenario']) for row in base))
    check('parent-id-recorded',
          all(row['parentId'] == domain.identity(scenario) for row in base))
    check('zero-budget-produces-no-proposals', proposals.pool(scenario, maximum=0) == [])
    check('deliberate-progression-targets-present',
          any(row['expectedMechanicalDifferences']['proposalKind'] == 'progression' for row in base))
    check('interval-is-mechanical-not-raw-speed', all(
        row['expectedMechanicalDifferences']['intervalChange']['after'] ==
        mechanics.profile(row['scenario'])['ownUnits'][0]['speed']['interval'] for row in base))

    # Frozen template: skills, order, triggers, weapon, fodder and formation never move.
    preserved = True
    for row in base:
        child = row['scenario']
        for before, after in zip(parent['ownUnits'], child['ownUnits']):
            if (before['skills'] != after['skills']
                    or before['invocationLevels'] != after['invocationLevels']
                    or before['weaponId'] != after['weaponId']):
                preserved = False
        if mechanics.prepared_setup(child)['ownFormationOrder'] != mechanics.prepared_setup(parent)['ownFormationOrder']:
            preserved = False
    check('frozen-template-preserved', preserved)
    check('proposal-schema-fields',
          all(set(('id', 'scenario', 'parentId', 'purpose', 'changedFields', 'fixedFields',
                   'expectedMechanicalDifferences', 'approximatelyPreserved', 'residualMismatch',
                   'whySimulation', 'plannedBudget', 'stopping', 'features', 'novelty')) <= set(row)
              for row in base))
    check('planned-budget-and-stopping-finite',
          all(row['plannedBudget']['finite'] and row['stopping']['maxRuns'] > 0
              and row['stopping']['maxRuns'] < 10 ** 6 for row in base))

    # Features are flat numeric, telemetry-free, and consumable by the adviser.
    names = {name for row in base for name in row['features']}
    flat_numeric = all(isinstance(value, float) for row in base for value in row['features'].values())
    clean = all(not any(word in name.lower() for word in FORBIDDEN) for name in names)
    equal_keys = len({tuple(sorted(row['features'])) for row in base}) == 1
    check('features-flat-numeric-no-telemetry', flat_numeric and clean and equal_keys and len(names) > 10)
    observations = [dict(candidateId=row['id'], compatibility='fixture', features=row['features'],
                         meanEarned=float(index), resolved=4, total=4, window='development')
                    for index, row in enumerate(base)]
    model = adviser.fit(observations, compatibility='fixture')
    predicted = adviser.predict(model, base[0]['features']) if base else {}
    check('features-fit-predict-adviser',
          model['status'] == 'fitted' and predicted.get('status') == 'predicted'
          and predicted.get('expectedEarned') is not None,
          model.get('status'))

    # High and low DEX both searchable; no learned cap prohibition.
    dex_before = mechanics.profile(parent)['ownUnits'][0]['effectiveStats']['dexterity']
    dex_values = [dps_change(row, 'dex')['after'] for row in base if dps_change(row, 'dex')]
    check('high-and-low-dex-searchable',
          any(value < dex_before for value in dex_values) and any(value > dex_before for value in dex_values),
          'before=%d values=%s' % (dex_before, sorted(set(dex_values))))
    check('negative-one-axis-evidence-does-not-veto-joint-move',
          any(row['expectedMechanicalDifferences']['dpsEffectiveChanges'].get('lck')
              and row['expectedMechanicalDifferences']['dpsEffectiveChanges'].get('atk')
              for row in proposals.pool(scenario, evidence={'negativeAxes': ['lck']}, maximum=64)))

    # Speed: a meaningful interval/contact change with joint compensation present.
    speed_rows = [row for row in base if dps_change(row, 'spd') and dps_change(row, 'atk')]
    check('speed-interval-contact-change-with-joint-compensation',
          bool(speed_rows)
          and any(row['expectedMechanicalDifferences']['incomingContact']['before']
                  != row['expectedMechanicalDifferences']['incomingContact']['after']
                  or row['expectedMechanicalDifferences']['intervalChange']['before']
                  != row['expectedMechanicalDifferences']['intervalChange']['after']
                  for row in speed_rows),
          'rows=%d' % len(speed_rows))
    check('nominal-and-actual-effective-changes-recorded',
          all(set(('nominal', 'before', 'after'))
              <= set(change)
              for row in base
              for change in row['expectedMechanicalDifferences']['dpsEffectiveChanges'].values())
          and all('trainingSideEffects' in row['expectedMechanicalDifferences']
                  and 'mpSideEffects' in row['expectedMechanicalDifferences']
                  and row['expectedMechanicalDifferences']['formationOrderPreserved'] is True for row in base))

    # Fixed DEX honoured.
    dex_field = 'ownUnits.0.parameters.19'
    fixed = proposals.pool(scenario, constraints={'context': 'synthetic', 'fixed': {dex_field: 17}},
                           maximum=64)
    check('fixed-dex-honored',
          bool(fixed) and all(dps_change(row, 'dex') and dps_change(row, 'dex')['after'] == 17 for row in fixed),
          'n=%d' % len(fixed))

    # Boundary (fixed-build) and compensated questions.
    question = dict(kind='fixed-build', field=dex_field, fixedFields={dex_field: 300},
                    adjustableFields=[dex_field])
    boundary = proposals.pool(scenario, purpose='boundary', question=question, maximum=8)
    check('boundary-frozen-field-value',
          len(boundary) == 1 and dps_change(boundary[0], 'dex')['after'] == 300
          and boundary[0]['purpose'] == 'boundary'
          and boundary[0]['fixedFields'].get(dex_field) == 300)
    compensated_question = dict(kind='compensated', field=dex_field, fixedFields={dex_field: 40},
                                adjustableFields=[dex_field, 'ownUnits.0.parameters.13'])
    compensated = proposals.pool(scenario, purpose='compensated', question=compensated_question, maximum=8)
    check('compensated-fixes-target-and-adjusts-permitted',
          len(compensated) == 1 and dps_change(compensated[0], 'dex')['after'] == 40
          and dps_change(compensated[0], 'atk') and compensated[0]['purpose'] == 'compensated')

    # Support-boundary (lowering on a clone) and improvement repair (upward, named role) are separate.
    support = proposals.pool(scenario, purpose='support', maximum=16)
    repair = [row for row in proposals.pool(
        scenario, evidence={'limitingRole': 'healer', 'limitingStats': ['hp', 'def']}, maximum=64)
        if row['purpose'] == 'improvement' and any(
            field.split('.')[-1] in ('10', '11', '14') for field in row['changedFields'])]
    check('support-boundary-vs-repair-separate',
          bool(support) and bool(repair) and all(row['purpose'] == 'support'
                                and all(change['after'] < change['before']
                                        for change in row['expectedMechanicalDifferences']['supportEffectiveChanges'].values())
                                for row in support)
          and all(row['purpose'] == 'improvement'
                  and all(change['after'] > change['before']
                          for change in row['expectedMechanicalDifferences']['supportEffectiveChanges'].values())
                  for row in repair),
          'support=%d repair=%d' % (len(support), len(repair)))

    # An MP-only limit must never buy an unrelated DEF/HP repair, and an un-evidenced role/stat list
    # must buy nothing at all: the improvement budget stays unspent without support.
    mp_only = [row for row in proposals.pool(
        scenario, evidence={'limitingRole': 'healer', 'limitingStats': ['mp']}, maximum=64)
        if row['purpose'] == 'improvement' and any(
            field.split('.')[-1] in ('10', '11', '14') for field in row['changedFields'])]
    mp_fields = {field for row in mp_only
                 for field in row['expectedMechanicalDifferences']['supportEffectiveChanges']}
    check('repair-honours-limiting-stats-mp-only',
          all(field.split('.')[-1] not in ('10', '14') for field in mp_fields),
          'mp_repair=%d fields=%s' % (len(mp_only), sorted(mp_fields)))
    no_stats = [row for row in proposals.pool(
        scenario, evidence={'limitingRole': 'healer'}, maximum=64)
        if row['purpose'] == 'improvement' and any(
            field.split('.')[-1] in ('10', '11', '14') for field in row['changedFields'])]
    check('repair-refused-without-limiting-stats', no_stats == [], len(no_stats))

    # No useful work returns [].
    check('no-question-boundary-and-compensated-return-empty',
          proposals.pool(scenario, purpose='boundary') == []
          and proposals.pool(scenario, purpose='compensated') == [])

    # A non-community parent is refused.
    broken = json.loads(json.dumps(scenario))
    broken['ownUnits'][0]['skills'] = broken['ownUnits'][0]['skills'] + [37]
    check('non-community-parent-refused', proposals.pool(broken) == [])

    # Injected synthetic mechanics stub: a joint compensation must still be produced.
    class StubSolver:
        calls = 0

        def __call__(self, targets, luck, dex, roster, bounds):
            StubSolver.calls += 1
            return {'results': [{'best': {'attack': 42, 'residual': 0.5},
                                 'candidates': [{'attack': 42, 'residual': 0.5}]}]}

    original = proposals.COMPENSATION_SOLVER
    proposals.COMPENSATION_SOLVER = StubSolver()
    try:
        stub = proposals.pool(scenario, maximum=3)
    finally:
        proposals.COMPENSATION_SOLVER = original
    check('synthetic-law-joint-compensation-stub',
          StubSolver.calls > 0 and bool(stub)
          and all(dps_change(row, 'atk') and dps_change(row, 'atk')['after'] == 42 for row in stub),
          'calls=%d n=%d' % (StubSolver.calls, len(stub)))
    check('real-solver-restored', proposals.COMPENSATION_SOLVER is mechanics.solve_attack_profile)

    # Repeated pool calls with several productive parents.
    first = base[0]
    child_parent = proposals.pool(first['scenario'], maximum=4, round_index=1)
    check('repeated-pool-calls-per-parent',
          bool(child_parent) and all(row['parentId'] == first['childIdentity'] for row in child_parent)
          and all(row['childIdentity'] != first['childIdentity'] for row in child_parent))

    failed = [row['name'] for row in RESULTS if not row['ok']]
    print('\n%d/%d checks passed' % (len(RESULTS) - len(failed), len(RESULTS)))
    if failed:
        print('FAILED: ' + ', '.join(failed))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
