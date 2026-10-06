"""Focused checks for the pure finite decision services and the proposal refinements.

Run:  <venv>/python -B -X utf8 tools/recovery/check_encounter_decisions.py
Deterministic, read-only, no battles, no SQLite. Real mechanics fixtures only.

Covers ``strategy_encounter_decisions.boundary_points`` /
``strategy_encounter_decisions.choose_extension`` and the ``strategy_joint_proposals``
refinements (always-present domain result, support HP/DEF joint compensation, and the
exact/reduced/approx compensation explanation). The existing joint-proposal and stream
checks are preserved separately; this file adds the new decision-service surface.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import strategy_build_domain as domain          # noqa: E402
import strategy_encounter_compiler as compiler  # noqa: E402
import strategy_encounter_decisions as decisions  # noqa: E402
import strategy_joint_proposals as proposals    # noqa: E402
import strategy_mechanics as mechanics          # noqa: E402

WORKSPACE = HERE.parents[2]
SCENARIO_PATH = WORKSPACE / 'RE-evidence/20260920-user-wairo-strategy/scenarios/UREF.scenario.json'
LOW_DEF_PATH = WORKSPACE / 'RE-evidence/20260920-user-wairo-strategy/scenarios/UREF-OLDRAWMX.scenario.json'
RESULTS = []

DEX_FIELD = 'ownUnits.0.parameters.19'
HP_FIELD = 'ownUnits.0.parameters.10'
DEF_FIELD = 'ownUnits.0.parameters.14'


def check(name, condition, detail=''):
    RESULTS.append(dict(name=name, ok=bool(condition), detail=detail))
    print(('PASS' if condition else 'FAIL') + ' ' + name + ((' :: ' + str(detail)) if detail else ''))


def load_scenario():
    return json.loads(SCENARIO_PATH.read_text(encoding='utf-8'))


def boundary_question(**overrides):
    base = dict(kind='fixed-build', field=DEX_FIELD, fixedFields={DEX_FIELD: 12},
                adjustableFields=[DEX_FIELD], referenceId='ref-1', referenceRevision=1,
                encounterRevision='enc-r1', mechanicsRevision='mech-r1', tolerance=0.9,
                reliability=None, policy={'resource': 'frozen', 'finish': 'on-verdict'}, id='Q-1')
    base.update(overrides)
    return base


def history_row(candidate, reference, mean, error, pairs, unknown=0, compatibility='cc',
                purpose='improvement'):
    return dict(candidateId=candidate, referenceId=reference, pairedMeanDifference=mean,
                pairedStandardError=error, pairs=pairs, unknownCount=unknown,
                experimentId='%s-exp' % candidate, compatibility=compatibility, purpose=purpose)


def extension_case(*rows, remaining_runs=64, max_pairs=64, portfolio=None):
    return decisions.choose_extension(list(rows), portfolio if portfolio is not None else [],
                                      remaining_runs=remaining_runs, max_pairs=max_pairs)


def main():
    scenario = load_scenario()
    parent = mechanics.normalize_scenario(scenario)
    prepared = mechanics.prepared_setup(parent)
    roles = proposals._role_indices(parent)
    ref_dex = proposals._effective(prepared, 0, 19)
    ref_def = proposals._effective(prepared, 0, 14)

    check('decisions-no-battle-or-optimiser-imports',
          'combat_sandbox' not in sys.modules
          and 'strategy_optimizer_adapter' not in sys.modules
          and not any(name.startswith('strategy_optimizer') for name in sys.modules))

    # ---- boundary_points -------------------------------------------------- #
    check('boundary-requires-a-known-kind',
          decisions.boundary_points({'kind': 'nope'}, scenario) == []
          and decisions.boundary_points(None, scenario) == [])
    check('boundary-zero-maximum-is-empty',
          decisions.boundary_points(boundary_question(), scenario, maximum=0) == [])

    base_points = decisions.boundary_points(boundary_question(), scenario, maximum=8)
    check('exact-requested-value-is-first-and-untested',
          bool(base_points) and base_points[0]['value'] == 12
          and base_points[0]['category'] == 'requested',
          base_points[0]['value'] if base_points else None)
    check('child-records-own-value-and-frozen-question-id',
          all(point['questionId'] == 'Q-1' and point['value'] == point['fixedFields'][DEX_FIELD]
              for point in base_points))
    check('reference-and-policy-frozen-on-every-child',
          all(point['frozenReference'] == 'ref-1' and point['referenceId'] == 'ref-1'
              and point['policy'] == {'resource': 'frozen', 'finish': 'on-verdict'}
              and point['interpolation'] is False
              and point['safeCartesianProductClaimed'] is False
              and point['continuousCoverageClaimed'] is False
              for point in base_points))

    tested_once = decisions.boundary_points(boundary_question(), scenario, tested_points=[12],
                                            maximum=8)
    check('requested-value-not-retested-once-measured',
          tested_once and all(point['value'] != 12 for point in tested_once))

    no_op = decisions.boundary_points(boundary_question(fixedFields={DEX_FIELD: ref_dex + 1}),
                                      scenario, maximum=8)
    check('no-generated-reference-identical-no-op',
          all(point['value'] != ref_dex for point in no_op if point['category'] != 'requested'),
          'ref=%d values=%s' % (ref_dex, [p['value'] for p in no_op]))

    spread = decisions.boundary_points(boundary_question(fixedFields={DEX_FIELD: 20}), scenario,
                                       tested_points=[5, 30], maximum=12)
    categories = {point['category'] for point in spread}
    values = [point['value'] for point in spread]
    check('distinct-bracket-interior-and-capped-points',
          {'requested', 'bracket', 'interior', 'capped'} <= categories
          and len(values) == len(set(values)),
          sorted(categories))
    check('interiors-only-strictly-between-measured-points',
          all(5 < point['value'] < 30 for point in spread
              if point['category'] == 'interior')
          and all(point['category'] != 'interior'
                  for point in decisions.boundary_points(
                      boundary_question(fixedFields={DEX_FIELD: ref_dex}), scenario, maximum=8)))
    check('boundary-maximum-is-bounded',
          all(len(decisions.boundary_points(boundary_question(), scenario, maximum=cap)) <= cap
              for cap in (1, 2, 3)))

    fixed = decisions.boundary_points(
        boundary_question(), scenario,
        constraints={'context': 'synthetic', 'fixed': {DEX_FIELD: 15}}, maximum=8)
    check('fixed-constraint-never-violated',
          len(fixed) == 1 and fixed[0]['value'] == 15 and fixed[0]['category'] == 'fixed',
          [point['value'] for point in fixed])

    bounded = decisions.boundary_points(
        boundary_question(), scenario,
        constraints={'context': 'synthetic', 'bounds': {DEX_FIELD: [10, 20]}}, maximum=12)
    check('user-bounds-clamp-and-supply-a-capped-endpoint',
          bounded and all(10 <= point['value'] <= 20 for point in bounded)
          and any(point['category'] == 'capped' and point['value'] == 20 for point in bounded)
          and any('user bound' in point['interventionReason'] for point in bounded
                  if point['category'] == 'capped'),
          [point['value'] for point in bounded])

    fodder_index = roles.get('fodder')
    fodder_question = boundary_question(
        kind='support', field='ownUnits.%d.parameters.14' % fodder_index,
        fixedFields={'ownUnits.%d.parameters.14' % fodder_index: 5}, adjustableFields=[])
    support_question = boundary_question(
        kind='support', field=DEF_FIELD, fixedFields={DEF_FIELD: 900},
        adjustableFields=[HP_FIELD])
    support_points = decisions.boundary_points(support_question, scenario, maximum=12)
    check('support-role-preserved-and-fodder-refused',
          decisions.boundary_points(fodder_question, scenario, maximum=8) == []
          and bool(support_points)
          and all(point['supportRole'] in ('dps', 'healer') for point in support_points))
    check('support-points-never-repair',
          support_points and all(point['value'] <= ref_def for point in support_points),
          'ref_def=%d values=%s' % (ref_def, [p['value'] for p in support_points]))

    islands = decisions.boundary_points(
        boundary_question(fixedFields={DEX_FIELD: 20}), scenario,
        tested_points=[dict(value=5, classification='supported-acceptable'),
                       dict(value=20, classification='supported-degraded'),
                       dict(value=30, classification='supported-acceptable')], maximum=12)
    check('nonmonotone-accepted-islands-not-joined',
          islands and all(point['interpolation'] is False
                          and point['safeCartesianProductClaimed'] is False
                          and 'classification' not in point and 'region' not in point
                          for point in islands))

    # ---- choose_extension ------------------------------------------------- #
    check('extension-empty-history-refuses', extension_case(remaining_runs=64) is None)
    check('extension-incomplete-stage-refused-within-budget',
          extension_case(history_row('c1', 'r1', 5, 2, 4), remaining_runs=6) is None)

    basic = extension_case(history_row('c1', 'r1', 5, 2, 4), remaining_runs=32,
                           portfolio=[{'candidateId': 'c1'}])
    check('extension-advances-one-finite-stage',
          basic is not None and basic['candidateId'] == 'c1' and basic['referenceId'] == 'r1'
          and basic['additionalPairs'] == 4 and basic['target'] == 8
          and set(basic) == {'candidateId', 'referenceId', 'additionalPairs', 'target',
                             'undecided', 'reason'},
          basic and basic['additionalPairs'])
    check('extension-reason-is-knowledge-not-gain',
          basic and 'knowledge' in basic['reason'] and 'holdout' in basic['reason']
          and 'improvement' in basic['reason'])

    check('extension-resolved-poor-refused',
          extension_case(history_row('c1', 'r1', -10, 1, 4), remaining_runs=64) is None)
    check('extension-wholly-unresolved-blocked',
          extension_case(history_row('c1', 'r1', 5, 2, 4, unknown=4), remaining_runs=64) is None)
    check('extension-largest-stage-refused',
          extension_case(history_row('c1', 'r1', 5, 2, 64), remaining_runs=64) is None)

    capped = extension_case(history_row('c1', 'r1', 5, 2, 4), remaining_runs=10, max_pairs=64)
    check('extension-respects-peak-pair-and-budget-caps',
          capped is not None and capped['additionalPairs'] * 2 <= 10
          and capped['additionalPairs'] <= 5)

    uncertain = extension_case(history_row('c1', 'r1', 8, 0.5, 4),
                               history_row('c2', 'r1', 3, 3, 4), remaining_runs=64)
    check('extension-prefers-decision-uncertainty',
          uncertain is not None and uncertain['candidateId'] == 'c2',
          uncertain and uncertain['candidateId'])

    no_pool = extension_case(history_row('c1', 'r1', 2, 2, 4),
                             history_row('c1', 'r2', -5, 0.5, 4), remaining_runs=64)
    check('extension-does-not-pool-across-references',
          no_pool is not None and no_pool['referenceId'] == 'r1'
          and no_pool['additionalPairs'] == 4,
          no_pool)

    check('extension-never-extends-the-reference-arm',
          extension_case(history_row('c1', 'c1', 5, 2, 4), remaining_runs=64) is None)
    check('extension-portfolio-scope-restricts-candidates',
          extension_case(history_row('c1', 'r1', 5, 2, 4), remaining_runs=64,
                         portfolio=[{'candidateId': 'c2'}]) is None)
    check('extension-is-pure-and-deterministic',
          extension_case(history_row('c1', 'r1', 5, 2, 4), remaining_runs=32)
          == extension_case(history_row('c1', 'r1', 5, 2, 4), remaining_runs=32))

    # A KNOWN zero standard error is a determinate result: it must be preserved for independent
    # confirmation, never re-sampled as if its interval were merely unknown.
    zero_se = extension_case(history_row('c1', 'r1', 5, 0.0, 4), remaining_runs=64)
    check('extension-known-zero-se-is-determinate-not-extended', zero_se is None, zero_se)
    check('aggregate-known-zero-se-stays-zero',
          decisions._aggregate(dict(moments=[(4, 5.0, 0.0)]))[1] == 0.0,
          decisions._aggregate(dict(moments=[(4, 5.0, 0.0)])))
    check('aggregate-unknown-se-stays-unknown',
          decisions._aggregate(dict(moments=[(4, 5.0, None)]))[1] is None,
          decisions._aggregate(dict(moments=[(4, 5.0, None)])))
    # The declared finite ladder advances ONE total stage at a time and is capped at 64 TOTAL pairs.
    rung8 = extension_case(history_row('c1', 'r1', 3, 3, 8), remaining_runs=64, max_pairs=64)
    rung16 = extension_case(history_row('c1', 'r1', 3, 3, 16), remaining_runs=64, max_pairs=64)
    rung32 = extension_case(history_row('c1', 'r1', 3, 3, 32), remaining_runs=64, max_pairs=64)
    check('extension-ladder-advances-to-each-declared-stage',
          rung8 and rung8['additionalPairs'] == 8 and rung8['target'] == 16
          and rung16 and rung16['additionalPairs'] == 16 and rung16['target'] == 32
          and rung32 and rung32['additionalPairs'] == 32 and rung32['target'] == 64,
          [rung8 and rung8['target'], rung16 and rung16['target'], rung32 and rung32['target']])
    check('extension-total-cap-is-64-not-additional',
          extension_case(history_row('c1', 'r1', 3, 3, 8), remaining_runs=64,
                         max_pairs=8) is None,
          'a total cap of 8 must refuse the 8 -> 16 stage')
    check('extension-stage-needs-the-complete-paired-budget',
          extension_case(history_row('c1', 'r1', 3, 3, 8), remaining_runs=14) is None)

    # ---- proposal refinements -------------------------------------------- #
    proposals_default = proposals.pool(scenario, maximum=4)
    check('proposal-always-carries-a-domain-result',
          proposals_default and all(
              point['domain']['context'] == 'synthetic'
              and point['domain']['provenanceStatus'] == 'declared'
              and point['domain']['valid'] is True
              and point['domain']['reachability'] == 'unknown'
              and isinstance(point['approximatelyPreserved']['constraints'], bool)
              for point in proposals_default))
    proposals_player = proposals.pool(scenario, constraints={'context': 'player'}, maximum=4)
    check('proposal-domain-never-false-player-label',
          proposals_default and proposals_player
          and all(point['domain']['context'] == 'synthetic' for point in proposals_default)
          and all(point['domain']['context'] == 'player'
                  and point['domain']['provenanceStatus'] == 'unknown'
                  for point in proposals_player))

    support_q = dict(kind='support', field=DEF_FIELD, fixedFields={DEF_FIELD: 700},
                     adjustableFields=[HP_FIELD], referenceId='ref-1', policy={'resource': 'frozen'},
                     id='SQ-1')
    joint = proposals.pool(scenario, purpose='support', question=support_q, maximum=4)
    check('support-question-joint-hp-def-changes',
          len(joint) == 1
          and set(joint[0]['changedFields']) == {HP_FIELD, DEF_FIELD}
          and joint[0]['expectedMechanicalDifferences']['supportEffectiveChanges']['def']['after'] == 700
          and (joint[0]['expectedMechanicalDifferences']['supportEffectiveChanges']['hp']['after']
               > joint[0]['expectedMechanicalDifferences']['supportEffectiveChanges']['hp']['before']),
          joint[0]['changedFields'] if joint else None)
    check('support-question-never-fodder',
          proposals.pool(scenario, purpose='support', question=fodder_question, maximum=4) == [])

    reduced = proposals.pool(scenario, purpose='support', question=support_q, maximum=4)[0]
    approx_q = dict(support_q, fixedFields={DEF_FIELD: 500})
    approx = proposals.pool(scenario, purpose='support', question=approx_q, maximum=4)[0]
    check('support-compensation-fidelity-and-residual',
          reduced['expectedMechanicalDifferences']['compensationFidelity'] == 'reduced'
          and reduced['expectedMechanicalDifferences']['compensationResidual'] == 0.0
          and approx['expectedMechanicalDifferences']['compensationFidelity'] == 'approx'
          and approx['expectedMechanicalDifferences']['compensationResidual'] > 0,
          (reduced['expectedMechanicalDifferences']['compensationFidelity'],
           approx['expectedMechanicalDifferences']['compensationFidelity']))

    # A low-DEF real fixture that is not community-admitted: exercise the pure HP-axis helper
    # directly so the DEF-raising compensation direction is covered on real mechanics.
    low_raw = mechanics.normalize_scenario(json.loads(LOW_DEF_PATH.read_text(encoding='utf-8')))
    low_prepared = mechanics.prepared_setup(low_raw)
    low_table, _context, _provenance = proposals._domain_table(low_prepared,
                                                               proposals.SYNTHETIC_CONSTRAINTS)
    low_hp = proposals._effective(low_prepared, 0, 10)
    low_def = proposals._effective(low_prepared, 0, 14)
    extra, detail = proposals._support_compensation(
        low_prepared, compiler.compile_encounter(low_raw), 0, 10, int(low_hp * 0.8),
        {'ownUnits.0.parameters.14'}, low_table)
    check('support-hp-axis-raises-def-compensation',
          extra == [(0, 14, detail['value'])] and detail['value'] > low_def
          and detail['partnerStat'] == 'DEF' and detail['fidelity'] in ('reduced', 'approx')
          and detail['itemPath'] is None and detail['consumablePolicy'] == 'frozen',
          detail)
    check('compensation-explanation-reaches-why-simulation',
          all('simulation' in point['whySimulation'].lower()
              and any(label in point['whySimulation']
                      for label in ('reduced', 'approx', 'exact'))
              for point in (reduced, approx)))

    failed = [row['name'] for row in RESULTS if not row['ok']]
    print('\n%d/%d checks passed' % (len(RESULTS) - len(failed), len(RESULTS)))
    if failed:
        print('FAILED: ' + ', '.join(failed))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
