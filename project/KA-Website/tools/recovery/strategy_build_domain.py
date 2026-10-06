"""Build-domain constraints and conservative mechanical fingerprinting.

Two services:

* `validate_constraints` validates a JSON constraint document against one scenario
  using the documented field path `ownUnits.<index>.parameters.<parameterId>` in
  `fixed` and `bounds` maps. `constraint_schema` validates only the document;
  `validate_constraints` adds candidate acceptance: every fixed/bounds value must
  match the candidate's *effective* prepared parameter, and a `synthetic` value
  must also lie inside `search_contract.stat_bounds`, never signed-32 alone.
  Provenance is explicit: `synthetic`, `player` or `unknown` (a player value stays
  `unknown` unless verified). Joint reachability is always `unknown`.
* `fingerprint` returns a conservative identity key plus derived mechanical
  features. `identity` is *exactly* `strategy_optimizer.identity` (the raw input
  minus `mathSeed`/`libSeed`, no normalization); `normalizedFingerprint` is the
  separate effective-value key. `exactKey` hashes the complete input plus prepared
  effective values, encounter revision and source digest, so it keys *calculations*
  - it is never a licence to pool whole-battle outcome histories, which still needs
  explicit full-behaviour validation.
"""
from __future__ import annotations

import hashlib
from copy import deepcopy

import strategy_encounter_compiler as compiler
import strategy_mechanics as mechanics

DOMAIN_REVISION = 'strategy-build-domain-2'
_PARAMETER_IDS = (10, 11, 12, 13, 14, 15, 16, 18, 19, 20, 21, 22)
_INT32_MIN, _INT32_MAX = -(2 ** 31), 2 ** 31 - 1
_FIELD_PREFIX = 'ownUnits'
_CONTEXTS = ('synthetic', 'player', 'unrestricted')
#: A synthetic point is the search contract's declared domain; a player/unrestricted
#: value is unknown provenance until separately verified.
_CONTEXT_PROVENANCE = {'synthetic': 'declared', 'player': 'unknown', 'unrestricted': 'unknown'}


def canonical(value):
    return mechanics.canonical(value)


def identity(scenario):
    """Exactly `strategy_optimizer.identity`: raw input minus seeds, no normalization.

    The byte-for-byte hash must match the optimiser's canonical candidate identity,
    so it deliberately does *not* validate, expand household pets or coerce keys.
    Use `normalizedFingerprint`/`preparedDigest` for the effective-value view.
    """
    body = deepcopy(scenario)
    body.pop('mathSeed', None)
    body.pop('libSeed', None)
    return hashlib.sha256(canonical(body).encode('utf-8')).hexdigest()


def _parse_field(field):
    """Return ((index, parameterId), None) or (None, reason) for a documented path."""
    if not isinstance(field, str):
        return None, 'field must be a string'
    parts = field.split('.')
    if len(parts) != 4 or parts[0] != _FIELD_PREFIX or parts[2] != 'parameters':
        return None, 'field must match ownUnits.<index>.parameters.<parameterId>'
    try:
        index = int(parts[1])
        parameter_id = int(parts[3])
    except ValueError:
        return None, 'index and parameterId must be integers'
    return (index, parameter_id), None


def constraint_schema(constraints=None):
    """Document-only validation: context, shapes, paths, integer and signed-32 range.

    Reads no candidate, so a caller can check a constraint document on its own.
    Returns parsed `fixed`/`bounds` entries keyed by the original field path.
    """
    constraints = constraints or {}
    context = constraints.get('context')
    if context is None:
        provenance_status = 'unknown'
    elif context in _CONTEXTS:
        provenance_status = _CONTEXT_PROVENANCE[context]
    else:
        provenance_status = 'invalid'
    violations = []
    if provenance_status == 'invalid':
        violations.append(dict(field='context', reason='context must be one of %s' % (_CONTEXTS,)))
    fixed = constraints.get('fixed', {}) or {}
    bounds = constraints.get('bounds', {}) or {}
    if not isinstance(fixed, dict):
        violations.append(dict(field='fixed', reason='fixed must be a mapping'))
        fixed = {}
    if not isinstance(bounds, dict):
        violations.append(dict(field='bounds', reason='bounds must be a mapping'))
        bounds = {}
    parsed_fixed = {}
    for field, value in fixed.items():
        parsed, reason = _parse_field(field)
        if reason:
            violations.append(dict(field=str(field), reason=reason))
            continue
        if not isinstance(value, int) or isinstance(value, bool) or not _INT32_MIN <= value <= _INT32_MAX:
            violations.append(dict(field=str(field), reason='fixed value must be a signed-32 integer'))
            continue
        parsed_fixed[field] = dict(index=parsed[0], parameterId=parsed[1], value=value)
    parsed_bounds = {}
    for field, span in bounds.items():
        parsed, reason = _parse_field(field)
        if reason:
            violations.append(dict(field=str(field), reason=reason))
            continue
        if (not isinstance(span, (list, tuple)) or len(span) != 2
                or any(not isinstance(v, int) or isinstance(v, bool) for v in span)):
            violations.append(dict(field=str(field), reason='bounds must be a [low, high] integer pair'))
            continue
        low, high = span
        if low > high:
            violations.append(dict(field=str(field), reason='bounds low must not exceed high'))
            continue
        if not _INT32_MIN <= low <= high <= _INT32_MAX:
            violations.append(dict(field=str(field), reason='bounds exceed signed-32 storage'))
            continue
        if field in parsed_fixed and not low <= parsed_fixed[field]['value'] <= high:
            violations.append(dict(field=str(field), reason='fixed value lies outside bounds'))
            continue
        parsed_bounds[field] = dict(index=parsed[0], parameterId=parsed[1], low=int(low), high=int(high))
    return dict(context=context, provenanceStatus=provenance_status, valid=not violations,
                violations=violations, fixed=parsed_fixed, bounds=parsed_bounds)


def validate_constraints(scenario, constraints=None):
    """Schema validation plus candidate acceptance against effective prepared values.

    Every supplied fixed/bounds value must equal/contain the candidate's effective
    parameter (raw + extra + equipment for unbounded stats), and the supplied fixed
    value is preserved in the echo rather than replaced. A `synthetic` value must
    also lie inside `search_contract.stat_bounds`; signed-32 is only a storage floor.
    Player provenance stays `unknown`; reachability is never claimed.
    """
    schema = constraint_schema(constraints)
    violations = list(schema['violations'])
    normalized = mechanics.normalize_scenario(scenario)
    units = normalized['ownUnits']
    synthetic_bounds = dict(mechanics.synthetic_stat_bounds() or ())
    prepared = None
    if schema['fixed'] or schema['bounds']:
        prepared = mechanics.prepared_setup(normalized)
    effective_by_name = {member['name']: member['effectiveParameters']
                         for member in (prepared or {}).get('ownUnits', [])}

    def effective(index, parameter_id):
        if not 0 <= index < len(units):
            return None, 'unit index %d out of range' % index
        unit = units[index]
        parameters = {int(k) for k in unit['parameters']}
        if parameter_id not in parameters or parameter_id not in _PARAMETER_IDS:
            return None, 'unit %d has no parameter %d' % (index, parameter_id)
        entry = effective_by_name.get(unit['name'], {}).get(parameter_id)
        if entry is None:
            return None, 'unit %d reports no effective parameter %d' % (index, parameter_id)
        return entry['value'], None

    def synthetic_span(field, index, parameter_id):
        if schema['context'] != 'synthetic':
            return None
        span = synthetic_bounds.get(parameter_id)
        if span is None:
            violations.append(dict(field=field, reason='parameter %d is outside the permitted synthetic '
                                                       'search domain' % parameter_id))
        return span

    for field, spec in schema['fixed'].items():
        span = synthetic_span(field, spec['index'], spec['parameterId'])
        if span is not None and not span[0] <= spec['value'] <= span[1]:
            violations.append(dict(field=field, reason='fixed %d outside permitted synthetic interval [%d, %d]'
                                   % (spec['value'], span[0], span[1])))
        value, reason = effective(spec['index'], spec['parameterId'])
        if reason:
            violations.append(dict(field=field, reason=reason))
        elif value != spec['value']:
            violations.append(dict(field=field, reason='effective parameter %d of unit %d is %s, not supplied %s'
                                   % (spec['parameterId'], spec['index'], value, spec['value'])))
    for field, spec in schema['bounds'].items():
        span = synthetic_span(field, spec['index'], spec['parameterId'])
        if span is not None and (spec['low'] < span[0] or spec['high'] > span[1]):
            violations.append(dict(field=field, reason='bounds [%d, %d] exceed permitted synthetic interval [%d, %d]'
                                   % (spec['low'], spec['high'], span[0], span[1])))
        value, reason = effective(spec['index'], spec['parameterId'])
        if reason:
            violations.append(dict(field=field, reason=reason))
        elif not spec['low'] <= value <= spec['high']:
            violations.append(dict(field=field, reason='effective parameter %d of unit %d is %s outside bounds [%d, %d]'
                                   % (spec['parameterId'], spec['index'], value, spec['low'], spec['high'])))
    return dict(
        schema='ka-strategy-build-constraints-1',
        valid=not violations,
        schemaValid=schema['valid'],
        context=schema['context'],
        provenanceStatus=schema['provenanceStatus'],
        violations=violations,
        reachability='unknown',
        reachableClaimed=False,
        syntheticBounds={str(k): list(v) for k, v in sorted(synthetic_bounds.items())}
        if schema['context'] == 'synthetic' else None,
        normalizedConstraints=dict(
            context=schema['context'],
            fixed={field: spec['value'] for field, spec in schema['fixed'].items()},
            bounds={field: [spec['low'], spec['high']] for field, spec in schema['bounds'].items()}),
        note='Joint reachability is unknown; declared points are never claimed reachable. '
             'Acceptance compares effective values (raw + extra + equipment).',
    )


def _prepared_effective(prepared):
    """Effective parameters in a stable unit-index shape (never a name-only mapping)."""
    return [dict(unitIndex=index, name=member['name'],
                 effective={str(pid): dict(entry) for pid, entry in member['effectiveParameters'].items()})
            for index, member in enumerate(prepared['ownUnits'])]


def _features(profile):
    """Interpretation-only feature shape; never a pooling key or equivalence relation."""
    units = []
    for unit in profile['ownUnits']:
        units.append(dict(
            unitIndex=unit['unitIndex'], name=unit['name'], role=unit['role'],
            mp=unit['effectiveStats']['mp'],
            interval=unit['speed']['interval'], period=unit['speed']['period'],
            periodSemantics=unit['speed']['periodSemantics'],
            critRate=unit['critProfile']['rate'],
            averageTrainingLevel=unit['mpDependencies']['averageTrainingLevel'],
            skills=dict(eligibleSkillIds=[entry['skillId'] for entry in unit['skills']['eligible']],
                        normalAttackProbability=unit['skills']['normalAttackProbability'],
                        expectedCommandHitsPerAction=unit['skills']['expectedCommandHitsPerAction'],
                        expectedMpPerAction=unit['mpDependencies']['expectedMpPerAction'],
                        unavailableSkillIds=[route['skillId'] for route in unit['skills']['unavailableRoutes']]),
            outgoing=[dict(enemyIndex=entry['enemyIndex'], enemy=entry['enemy'],
                           hitRate=entry['probabilities']['hit']) for entry in unit['outgoing']]))
    return dict(
        units=units,
        formation=profile['formation'],
        consumables=profile['consumables'],
        dependencyDigest=profile.get('dependencyDigest'),
        mechanicsRevision=profile.get('mechanicsRevision'),
        note='Derived in a stable index shape (unitIndex/fighterIndex) covering MP, skills, '
             'formation, consumables and dependency digest. For interpretation only; it never '
             'keys outcome-history pooling and is not an equivalence relation. Use exactKey to '
             'key calculations.',
    )


def fingerprint(scenario, constraints=None):
    """Conservative calculation key plus derived mechanical features."""
    normalized = mechanics.normalize_scenario(scenario)
    constraints_result = validate_constraints(normalized, constraints)
    prepared = mechanics.prepared_setup(normalized)
    mechanics_profile = mechanics.profile(normalized)
    encounter = compiler.compile_encounter(normalized)
    prepared_effective = _prepared_effective(prepared)
    seedless = deepcopy(normalized)
    seedless.pop('mathSeed', None)
    seedless.pop('libSeed', None)
    prepared_digest = hashlib.sha256(canonical(prepared_effective).encode('utf-8')).hexdigest()
    dependency_digest = mechanics.dependency_digest()
    versions = dict(mechanicsRevision=mechanics.MECHANICS_REVISION,
                    compilerRevision=compiler.COMPILER_REVISION,
                    cacheKeyRevision=mechanics.CACHE_KEY_REVISION,
                    dependencyDigest=dependency_digest)
    normalized_fingerprint = hashlib.sha256(
        canonical(dict(scenario=seedless, preparedEffective=prepared_effective, **versions)).encode('utf-8')
    ).hexdigest()
    exact_payload = dict(
        scenario=seedless,
        constraintContext=constraints_result['context'],
        normalizedConstraints=constraints_result['normalizedConstraints'],
        encounterRevision=encounter['revision']['digest'],
        preparedDigest=prepared_digest,
        **versions,
    )
    exact_key = hashlib.sha256(canonical(exact_payload).encode('utf-8')).hexdigest()
    return dict(
        schema='ka-strategy-build-fingerprint-1',
        identity=identity(scenario),
        normalizedFingerprint=normalized_fingerprint,
        **versions,
        encounterRevision=encounter['revision'],
        exactKey=exact_key,
        preparedDigest=prepared_digest,
        features=_features(mechanics_profile),
        domain=dict(context=constraints_result['context'],
                    provenanceStatus=constraints_result['provenanceStatus'],
                    valid=constraints_result['valid'],
                    schemaValid=constraints_result['schemaValid'],
                    reachability=constraints_result['reachability'],
                    fixed=constraints_result['normalizedConstraints']['fixed'],
                    bounds=constraints_result['normalizedConstraints']['bounds']),
        limits=['exactKey is a complete calculation key (input + prepared effective values + encounter '
                'revision + source digest); it does not by itself licence pooling whole-battle histories.',
                'History reuse still requires explicit full-behaviour validation.',
                'features are interpretation only and are not an equivalence relation.',
                'No full equipment solver and no whole-battle prediction are performed here.',
                'Seed values do not change identity or exactKey; import-time source digests require a '
                'process restart to pick up edited files.'],
    )


__all__ = ['DOMAIN_REVISION', 'canonical', 'identity', 'constraint_schema',
           'validate_constraints', 'fingerprint']
