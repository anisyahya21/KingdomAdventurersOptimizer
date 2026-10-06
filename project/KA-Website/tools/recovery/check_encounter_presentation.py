"""Deterministic checks for the encounter presentation helper.

Run:  <venv>/python -B -X utf8 tools/recovery/check_encounter_presentation.py
Read-only, no battles, no SQLite, no seeds. Exercises every one of the twenty
recovered encounters' compiled rosters through the pure helper and prints
PASS/FAIL per check, exiting non-zero on any failure.
"""
from __future__ import annotations

import copy
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import strategy_build_domain as domain          # noqa: E402
import strategy_encounter_compiler as compiler  # noqa: E402
import strategy_joint_proposals as proposals    # noqa: E402
import strategy_mechanics as mechanics          # noqa: E402
import strategy_encounter_presentation as presentation  # noqa: E402

WORKSPACE = HERE.parents[2]
SCENARIO_PATH = WORKSPACE / 'RE-evidence/20260920-user-wairo-strategy/scenarios/UREF.scenario.json'
FIELD_RE = re.compile(r'^ownUnits\.(\d+)\.parameters\.(\d+)$')
FORBIDDEN_KEY_WORDS = ('seed', 'verdict', 'telemetry', 'certificate', 'holdout', 'earned', 'chest',
                       'reward', 'jackpot', 'pending')
FORBIDDEN_KEY_ALLOW = {'wholeBattleReward'}
RESULTS = []


def check(name, condition, detail=''):
    RESULTS.append(dict(name=name, ok=bool(condition), detail=detail))
    print(('PASS' if condition else 'FAIL') + ' ' + name + ((' :: ' + str(detail)) if detail else ''))


def load_scenario():
    return json.loads(SCENARIO_PATH.read_text(encoding='utf-8'))


def scenario_for(encounter_id):
    scenario = copy.deepcopy(load_scenario())
    scenario['encounterId'] = encounter_id
    scenario['defeatCount'] = 0
    return scenario


def iter_keys(value, prefix=''):
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key)
            yield from iter_keys(child, prefix + '.' + str(key))
    elif isinstance(value, list):
        for child in value:
            yield from iter_keys(child, prefix + '[]')


def effective_value(scenario, index, pid):
    prepared = mechanics.prepared_setup(mechanics.normalize_scenario(scenario))
    member = prepared['ownUnits']
    name = mechanics.normalize_scenario(scenario)['ownUnits'][index]['name']
    for row in member:
        if row['name'] == name:
            entry = row['effectiveParameters'].get(pid)
            return None if entry is None else entry['value']
    return None


def check_shape(payload, encounter_id):
    details = payload.get('encounterDetails') or {}
    fields = payload.get('questionFields') or []
    domain_block = payload.get('buildDomain') or {}
    summary = payload.get('mechanicsSummary') or {}
    ok = (payload.get('version') == presentation.PRESENTATION_VERSION
          and details.get('encounterId') == encounter_id
          and isinstance(fields, list) and fields
          and isinstance(domain_block, dict) and domain_block.get('reachability') == 'unknown'
          and isinstance(summary.get('quantities'), list) and summary['quantities']
          and summary.get('wholeBattleReward') == 'unknown'
          and summary.get('reducedModel') is True)
    check('shape-encounter-%d' % encounter_id, ok,
          'fields=%d quantities=%d' % (len(fields), len(summary.get('quantities') or [])))


def main():
    scenario = load_scenario()
    reference = presentation.describe(scenario, reference_id='community')
    again = presentation.describe(scenario, reference_id='community')
    check('deterministic', reference == again)
    check('json-round-trip', json.loads(json.dumps(reference)) == reference)
    check('reference-id-echoed', reference['referenceId'] == 'community')
    check('no-battle-or-optimiser-imports',
          'combat_sandbox' not in sys.modules
          and 'strategy_optimizer_adapter' not in sys.modules
          and not any(name.startswith('strategy_optimizer') for name in sys.modules))

    keys = list(iter_keys(reference))
    offenders = [key for key in keys
                 if key not in FORBIDDEN_KEY_ALLOW
                 and any(word in key.lower() for word in FORBIDDEN_KEY_WORDS)]
    check('no-reward-or-telemetry-keys', not offenders, offenders[:6])

    fields = reference['questionFields']
    shaped = all(set(('role', 'stat', 'label', 'field', 'currentValue', 'unitIndex', 'parameterId',
                      'status')) <= set(row) for row in fields)
    check('question-field-shape', shaped)
    paths_ok = all(FIELD_RE.match(str(row['field'] or '')) for row in fields)
    check('question-field-paths-canonical', paths_ok)
    index_ok = all(int(FIELD_RE.match(row['field']).group(1)) == row['unitIndex']
                   and int(FIELD_RE.match(row['field']).group(2)) == row['parameterId'] for row in fields)
    check('question-field-index-and-parameter', index_ok)
    effective_ok = all(
        row['currentValue'] is None
        or row['currentValue'] == effective_value(scenario, row['unitIndex'], row['parameterId'])
        for row in fields)
    check('question-field-current-values-effective', effective_ok)

    # Every resolved field is a valid one-value constraint at its own current value, and an off-by-one
    # value for a present field is refused: the paths are the coordinator's own canonical paths.
    accepted = 0
    refused = 0
    for row in fields:
        if row['currentValue'] is None:
            continue
        doc = {'context': 'synthetic', 'fixed': {row['field']: row['currentValue']}}
        if domain.validate_constraints(mechanics.normalize_scenario(scenario), doc)['schemaValid']:
            if domain.validate_constraints(mechanics.normalize_scenario(scenario), doc)['valid']:
                accepted += 1
            else:
                refused += 1
    check('current-values-are-valid-fixed-points', accepted == len([r for r in fields
                                                                   if r['currentValue'] is not None]),
          'accepted=%d refused=%d' % (accepted, refused))

    # The helper's role indices must equal the joint service's own resolution, never a fallback.
    normalized = mechanics.normalize_scenario(scenario)
    expected_roles = proposals._role_indices(normalized)
    check('role-indices-match-joint-service',
          reference['buildDomain']['roleIndices'] == {role: expected_roles.get(role)
                                                      for role in presentation.ROLE_ORDER},
          reference['buildDomain']['roleIndices'])

    # Build-domain schema echo is the EXACT constraint_schema output for the supplied document.
    lck_span = mechanics.synthetic_stat_bounds(presentation.STAT_PARAMETER_IDS['lck'])
    document = {'context': 'synthetic',
                'fixed': {'ownUnits.0.parameters.13': effective_value(scenario, 0, 13)},
                'bounds': {'ownUnits.0.parameters.16': [lck_span[0], lck_span[1]]}}
    built = presentation.describe(scenario, constraints=document)
    schema = domain.constraint_schema(document)
    check('constraint-schema-echo-exact',
          built['buildDomain']['constraintSchema'] == dict(
              context=schema['context'], provenanceStatus=schema['provenanceStatus'],
              valid=schema['valid'], violations=schema['violations'],
              fixed=schema['fixed'], bounds=schema['bounds']))
    check('constraint-document-valid', built['buildDomain']['valid'] is True)

    fixed_dex = effective_value(scenario, expected_roles['dps'], presentation.STAT_PARAMETER_IDS['dex'])
    good = {'context': 'synthetic', 'fixed': {'ownUnits.%d.parameters.19' % expected_roles['dps']: fixed_dex}}
    bad = {'context': 'synthetic', 'fixed': {'ownUnits.%d.parameters.19' % expected_roles['dps']: fixed_dex + 1}}
    good_domain = presentation.describe(scenario, constraints=good)['buildDomain']
    bad_domain = presentation.describe(scenario, constraints=bad)['buildDomain']
    check('fixed-dex-at-effective-valid', good_domain['valid'] is True)
    check('fixed-dex-off-by-one-invalid', bad_domain['valid'] is False and bad_domain['violations'])
    unknown_context = presentation.describe(scenario, constraints={'fixed': {}})['buildDomain']
    check('absent-context-is-unknown-not-player',
          unknown_context['context'] is None and unknown_context['provenanceStatus'] == 'unknown')
    for context in ('synthetic', 'player', 'unrestricted'):
        block = presentation.describe(scenario, constraints={'context': context})['buildDomain']
        if block['context'] != context:
            check('context-%s-echoed' % context, False, block['context'])
            break
    else:
        check('context-echoed', True)

    summary = reference['mechanicsSummary']
    quantities = summary['quantities']
    allowed = {'exact', 'reduced', 'approx', 'unavailable'}
    check('quantity-fidelity-labels', all(q['fidelity'] in allowed for q in quantities),
          sorted({q['fidelity'] for q in quantities}))
    check('reduced-quantities-name-their-limit',
          all(q.get('reason') for q in quantities if q['fidelity'] in ('reduced', 'approx', 'unavailable')))
    compiled = compiler.compile_encounter(mechanics.normalize_scenario(scenario))
    boss_hp = next(row['parameters']['hp'] for row in compiled['roster'] if row['boss'])
    boss_quantity = next(q for q in quantities if q['key'] == 'bossHp')
    check('boss-hp-is-compiled-roster-value',
          boss_quantity['value'] == boss_hp and boss_quantity['fidelity'] == 'exact', boss_hp)
    check('whole-battle-reward-unknown', summary['wholeBattleReward'] == 'unknown'
          and 'wholeBatchReward' not in summary)

    # All twenty recovered encounters compile and describe without a battle.
    for encounter_id in range(20):
        payload = presentation.describe(scenario_for(encounter_id), reference_id='community')
        check_shape(payload, encounter_id)
    check('all-twenty-describe', True)

    failed = [row for row in RESULTS if not row['ok']]
    print('\n%d/%d checks passed' % (len(RESULTS) - len(failed), len(RESULTS)))
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
