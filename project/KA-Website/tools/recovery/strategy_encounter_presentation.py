"""Pure presentation helper for the encounter-aware optimiser UI.

`describe(reference_scenario, constraints=None, reference_id=None)` returns the
payload the host publishes as `snapshot.presentation` (or
`migrationPreview.presentation`). It reads only the already-recovered services -
the encounter compiler, the shared mechanics profile, the prepared roles and the
build-domain constraint schema - and it never runs a battle, opens a database,
consumes a seed or asks for a reward. Nothing here is a second copy of a private
formula: every number is a value those services already return, and everything it
cannot read is reported as `unavailable`/`unknown` with the reason.

Returned shape:

    {
      "version": "strategy-encounter-presentation-1",
      "referenceId": <reference_id or None>,
      "encounterDetails": {encounterId, title, defeatCount, level, enemyCount,
                           bossName, bossOrder, revision, referenceId, note},
      "questionFields": [{role, stat, label, field, currentValue, unitIndex,
                          parameterId, status}, ...],
      "buildDomain": {context, provenanceStatus, reachability, schemaValid, valid,
                      violations, fixed, bounds, syntheticBounds, adjustableFields,
                      constraintSchema, roleIndices, formationOrder, rolesStatus, note},
      "mechanicsSummary": {reducedModel, revisions, quantities[], wholeBattleReward,
                           approximations, note},
    }

`questionFields[].field` is the canonical `ownUnits.<index>.parameters.<id>` path
that `strategy_build_domain` documents, using the ACTUAL source unit index the
recovered role resolved to and the actual effective prepared value.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import strategy_build_domain as domain          # noqa: E402
import strategy_encounter_compiler as compiler  # noqa: E402
import strategy_mechanics as mechanics          # noqa: E402
import strategy_students as students            # noqa: E402

PRESENTATION_VERSION = 'strategy-encounter-presentation-1'

ROLE_ORDER = ('dps', 'healer', 'fodder')
ROLE_LABELS = {'dps': 'DPS', 'healer': 'Healer', 'fodder': 'Fodder'}
STAT_ORDER = ('atk', 'lck', 'spd', 'dex', 'hp', 'mp', 'def')
STAT_PARAMETER_IDS = {'atk': 13, 'lck': 16, 'spd': 15, 'dex': 19, 'hp': 10, 'mp': 11, 'def': 14}
STAT_LABELS = {'atk': 'ATK', 'lck': 'LCK', 'spd': 'SPD', 'dex': 'DEX',
               'hp': 'HP', 'mp': 'MP', 'def': 'DEF'}

WHOLE_BATTLE_REWARD_UNKNOWN = 'unknown'


def _role_indices(normalized):
    """role -> source unit index, from the actual derived placement (never a name guess)."""
    roles = {role: None for role in ROLE_ORDER}
    source = {unit['name']: index for index, unit in enumerate(normalized['ownUnits'])}
    try:
        placed = {int(row['grid']): row['unit'] for row in students._placed_rows(normalized)}
    except Exception:  # noqa: BLE001 - an unresolved placement is reported, never guessed
        return roles, 'unresolved'
    for grid in sorted(placed):
        role = students.role(placed[grid])
        if roles.get(role) is None:
            roles[role] = source.get(placed[grid]['name'])
    return roles, 'resolved'


def _effective_by_name(prepared):
    return {member['name']: member['effectiveParameters'] for member in prepared['ownUnits']}


def question_fields(normalized, prepared, roles):
    """The canonical stat paths for the roles that actually resolved."""
    effective = _effective_by_name(prepared)
    units = normalized['ownUnits']
    fields = []
    for role in ROLE_ORDER:
        index = roles.get(role)
        if index is None or not 0 <= index < len(units):
            continue
        name = units[index]['name']
        parameters = effective.get(name) or {}
        for stat in STAT_ORDER:
            pid = STAT_PARAMETER_IDS[stat]
            entry = parameters.get(pid)
            value = None if entry is None else entry.get('value')
            fields.append(dict(
                role=role, stat=stat, label='%s %s' % (ROLE_LABELS[role], STAT_LABELS[stat]),
                field='ownUnits.%d.parameters.%d' % (index, pid),
                currentValue=value, unitIndex=index, parameterId=pid,
                status='resolved' if value is not None else 'unavailable'))
    return fields


def build_domain(normalized, prepared, roles, constraints):
    """Constraint-document status straight from `strategy_build_domain` (exact schema)."""
    schema = domain.constraint_schema(constraints)
    validation = domain.validate_constraints(normalized, constraints)
    return dict(
        context=validation['context'], provenanceStatus=validation['provenanceStatus'],
        reachability=validation['reachability'], schemaValid=schema['valid'],
        valid=validation['valid'], violations=validation['violations'],
        fixed=validation['normalizedConstraints']['fixed'],
        bounds=validation['normalizedConstraints']['bounds'],
        syntheticBounds=validation['syntheticBounds'],
        adjustableFields=(constraints or {}).get('adjustableFields'),
        constraintSchema=dict(context=schema['context'], provenanceStatus=schema['provenanceStatus'],
                              valid=schema['valid'], violations=schema['violations'],
                              fixed=schema['fixed'], bounds=schema['bounds']),
        roleIndices={role: roles.get(role) for role in ROLE_ORDER},
        formationOrder=list(prepared.get('ownFormationOrder') or []),
        rolesStatus='resolved' if any(value is not None for value in roles.values()) else 'unresolved',
        note='Read-only document check: joint reachability stays unknown and no equipment or '
             'rank availability is claimed.')


def _quantity(key, label, value, unit, fidelity, scope=None, reason=None, denominator=None):
    entry = dict(key=key, label=label, unit=unit, fidelity=fidelity)
    if value is None:
        entry['value'] = None
        entry['fidelity'] = 'unavailable'
        if reason is None:
            reason = 'the recovered service returned no value for this quantity'
    else:
        entry['value'] = value
    if scope is not None:
        entry['scope'] = scope
    if reason is not None:
        entry['reason'] = reason
    if denominator is not None:
        entry['denominator'] = denominator
    return entry


def _summary_name(normalized, roles, role):
    index = roles.get(role)
    if index is None or not 0 <= index < len(normalized['ownUnits']):
        return None
    return normalized['ownUnits'][index]['name']


def mechanics_summary(normalized, prepared, roles):
    """Small set of human-meaningful quantities, each with its own fidelity label."""
    profile = mechanics.profile(normalized)
    compiled = compiler.compile_encounter(normalized)
    quantities = []
    boss = next((row for row in compiled['roster'] if row.get('boss')), None)
    if boss is not None:
        quantities.append(_quantity('bossHp', 'Boss HP', boss['parameters'].get('hp'), 'hp', 'exact',
                                    scope='compiled roster: %s' % boss['name']))
        quantities.append(_quantity('bossInterval', 'Boss action interval', boss.get('interval'),
                                    'frames', 'reduced', scope='compiled roster: %s' % boss['name'],
                                    reason='interval + 21 is the uninterrupted normal-attack reduced '
                                           'path, not the actual live cadence'))
    else:
        quantities.append(_quantity('bossHp', 'Boss HP', None, 'hp', 'unavailable',
                                    reason='the compiled roster carries no leader/boss row'))
    followers = [row for row in compiled['roster'] if not row.get('boss')]
    follower_hps = [row['parameters'].get('hp') for row in followers
                    if row['parameters'].get('hp') is not None]
    if follower_hps:
        quantities.append(_quantity('followerHpMax', 'Toughest follower HP', max(follower_hps), 'hp',
                                    'exact', scope='compiled roster, %d followers' % len(followers)))
        mean_hp = round(sum(follower_hps) / float(len(follower_hps)), 3)
        quantities.append(_quantity('followerHpMean', 'Mean follower HP', mean_hp, 'hp', 'exact',
                                    scope='compiled roster, %d followers' % len(followers)))
    else:
        quantities.append(_quantity('followerHpMax', 'Toughest follower HP', None, 'hp', 'unavailable',
                                    reason='the compiled roster carries no follower HP'))

    dps_name = _summary_name(normalized, roles, 'dps')
    dps_unit = next((unit for unit in profile['ownUnits'] if unit['name'] == dps_name), None)
    if dps_unit is None:
        for key, label, unit in (('incomingContact', 'DPS contact rate vs boss', 'probability'),
                                 ('critRate', 'DPS critical rate', 'probability'),
                                 ('actionInterval', 'DPS action interval', 'frames'),
                                 ('mpEligibility', 'DPS skills affordable at battle MP', 'skills')):
            quantities.append(_quantity(key, label, None, unit, 'unavailable',
                                        reason='no DPS role resolved in this reference build'))
        return _mechanics(profile, compiled, quantities)

    boss_order = boss.get('order') if boss is not None else None
    incoming = next((row for row in dps_unit['incomingAccuracy']['perEnemy']
                     if row.get('enemyIndex') == boss_order), None)
    quantities.append(_quantity(
        'incomingContact', 'DPS contact rate vs boss',
        None if incoming is None else incoming.get('hitRate'), '%', 'reduced',
        scope='static hit_rate against the compiled boss',
        reason='static hit_rate; live invoking type-22 windows are not applied'))
    quantities.append(_quantity(
        'critRate', 'DPS critical rate', dps_unit['critProfile'].get('rate'), '%', 'reduced',
        reason='recovered critical law only; invoking/affinity modifiers are not applied'))
    quantities.append(_quantity(
        'actionInterval', 'DPS action interval', dps_unit['speed'].get('interval'), 'frames', 'reduced',
        reason='interval + 21 is the uninterrupted normal-attack reduced path, not the actual cadence'))
    eligible = dps_unit['skills'].get('eligible') or []
    routed = len(eligible) + len(dps_unit['skills'].get('unavailableRoutes') or [])
    quantities.append(_quantity(
        'mpEligibility', 'DPS skills affordable at battle MP', len(eligible), 'skills', 'reduced',
        scope='expected MP per action %s' % (dps_unit['mpDependencies'].get('expectedMpPerAction'),),
        denominator=routed,
        reason='skill affordability is the authoritative MP filter on the battle MP pool, but the '
               'surrounding skill-selection model is reduced'))
    return _mechanics(profile, compiled, quantities)


def _mechanics(profile, compiled, quantities):
    return dict(
        reducedModel=bool(profile.get('reducedModel')),
        encounterRevision=(compiled.get('revision') or {}).get('digest'),
        mechanicsRevision=profile.get('mechanicsRevision'),
        compilerRevision=compiled.get('compilerRevision'),
        quantities=quantities,
        wholeBattleReward=WHOLE_BATTLE_REWARD_UNKNOWN,
        approximations=list(profile.get('approximations') or []),
        note='Reduced mechanical model. These quantities are not a whole-fight prediction and '
             'wholeBattleReward is unknown here: only a simulation over the frozen policy can '
             'produce a reward. Survival and target selection are likewise not predicted.')


def encounter_details(normalized, reference_id):
    compiled = compiler.compile_encounter(normalized)
    encounter = compiled['encounter']
    return dict(
        encounterId=encounter.get('encounterId'), title=encounter.get('title'),
        defeatCount=encounter.get('defeatCount'), level=encounter.get('level'),
        enemyCount=encounter.get('enemyCount'), bossName=encounter.get('bossName'),
        bossOrder=encounter.get('bossOrder'),
        revision=(compiled.get('revision') or {}).get('digest'), referenceId=reference_id,
        note='Compiled from the same roster revision the coordinator uses; no battle is run.')


def describe(reference_scenario, constraints=None, reference_id=None):
    """Pure presentation payload for one reference scenario. Deterministic, no battle, no seed."""
    normalized = mechanics.normalize_scenario(reference_scenario)
    prepared = mechanics.prepared_setup(normalized)
    roles, _roles_status = _role_indices(normalized)
    return dict(
        version=PRESENTATION_VERSION,
        referenceId=reference_id,
        encounterDetails=encounter_details(normalized, reference_id),
        questionFields=question_fields(normalized, prepared, roles),
        buildDomain=build_domain(normalized, prepared, roles, constraints),
        mechanicsSummary=mechanics_summary(normalized, prepared, roles))


def cache_info():
    return dict(mechanics=mechanics.cache_info(), compiler=compiler.cache_info())


def invalidate_caches():
    compiler.invalidate_caches()


__all__ = ['PRESENTATION_VERSION', 'ROLE_ORDER', 'STAT_ORDER', 'STAT_PARAMETER_IDS',
           'WHOLE_BATTLE_REWARD_UNKNOWN', 'describe', 'question_fields', 'build_domain',
           'mechanics_summary', 'encounter_details', 'cache_info', 'invalidate_caches']


if __name__ == '__main__':  # pragma: no cover - manual inspection only
    import argparse
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('scenario')
    parser.add_argument('--reference-id', default=None)
    args = parser.parse_args()
    with open(args.scenario, encoding='utf-8') as handle:
        payload = describe(json.load(handle), reference_id=args.reference_id)
    print(json.dumps(payload, indent=2, sort_keys=True))
