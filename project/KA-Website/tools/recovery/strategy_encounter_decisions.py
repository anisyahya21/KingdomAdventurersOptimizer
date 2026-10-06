"""Reusable, pure, finite decision services for the encounter-aware redesign.

Two read-only, deterministic helpers shared by the scheduler:

* ``boundary_points`` - turn ONE frozen question into a bounded, ordered list of
  distinct child points (the exact requested value, a bracket, discrete mechanical
  neighbours, interiors strictly between already-measured points and the capped
  domain endpoints). It never assumes monotonicity, never fills a raw Cartesian grid,
  never builds a safe continuous box and never changes the frozen reference/policy.
* ``choose_extension`` - choose at most one under-sampled, upside-plausible challenger
  to re-sample on a finite stage, or refuse. It pools paired evidence only inside one
  candidate/reference/compatibility group, never across different references, and it
  treats extra samples as explicit knowledge charged to the improvement budget rather
  than as an earning gain.

Neither service simulates a battle, opens a database, imports the optimiser or keeps
mutable global state. A tested point is a tested point: gaps stay unknown.
"""
from __future__ import annotations

import hashlib
import math
from copy import deepcopy

import strategy_build_domain as domain
import strategy_encounter_compiler as compiler
import strategy_joint_proposals as joint
import strategy_mechanics as mechanics
import strategy_students as students

VERSION = 'strategy-encounter-decisions-1'

#: Question kinds this service understands (mirrors ``strategy_operating_regions.KINDS``).
KINDS = ('fixed-build', 'compensated', 'support')
#: Hard cap on how many child points one call may return.
MAXIMUM_CAP = 64
#: Finite paired-sample stages: an extension moves a challenger up exactly one rung.
EXTENSION_STAGES = (4, 8, 16, 32, 64)
MINIMUM_PAIRS = EXTENSION_STAGES[0]
_RUNS_PER_PAIR = 2
_JOINT_PIDS = frozenset(joint.JOINT_STATS.values())
_SUPPORT_PIDS = frozenset(joint.SUPPORT_STATS.values())


# --------------------------------------------------------------------------- #
# Boundary points
# --------------------------------------------------------------------------- #
def _stat_name(pid):
    for name, value in joint.JOINT_STATS.items():
        if value == pid:
            return name
    for name, value in joint.SUPPORT_STATS.items():
        if value == pid:
            return name
    return 'parameter %d' % pid


def _tested_values(tested_points):
    """Accept plain ints or stored tested-point records and return the measured values."""
    out = set()
    for item in tested_points or ():
        if isinstance(item, bool):
            continue
        if isinstance(item, int):
            out.add(int(item))
            continue
        if isinstance(item, dict):
            value = item.get('value')
            if isinstance(value, dict):
                value = value.get('value')
            if isinstance(value, int) and not isinstance(value, bool):
                out.add(int(value))
    return out


def _enemy_attacks(compiled):
    pressure = ((compiled.get('demands') or {}).get('survivalPressure') or {})
    rows = pressure.get('perEnemy') or []
    out = []
    for row in rows:
        value = row.get('effectiveAttack')
        if isinstance(value, int) and not isinstance(value, bool):
            out.append(int(value))
    return out


def _fallback_defense(compiled):
    return (((compiled.get('demands') or {}).get('survivalPressure') or {})
            .get('fallbackDefenseForEncounter'))


def _support_landmarks(pid, index, prepared, compiled, profile):
    """Discrete encounter-mechanical support landmarks for ONE unit's HP/MP/DEF."""
    values = []
    attacks = _enemy_attacks(compiled)
    fallback = _fallback_defense(compiled)
    if pid == joint.SUPPORT_STATS['def'] and fallback is not None:
        values.append(int(fallback))
    if pid == joint.SUPPORT_STATS['hp']:
        if attacks:
            values.append(int(max(attacks)))
        if fallback is not None:
            values.append(int(fallback))
    if pid == joint.SUPPORT_STATS['mp']:
        unit = profile['ownUnits'][index]
        kit = sum(int(entry['cost']) for entry in unit['mpDependencies']['skillCosts'])
        values.extend([int(kit), 0])
    return values


def _joint_landmarks(pid, index, prepared, compiled):
    """Discrete mechanical landmarks on a joint DPS axis (Luck crit class, DEX
    accuracy, AGI timing/contact). Never a percentage ladder."""
    current = joint._effective(prepared, index, pid)
    if current is None:
        return []
    if pid == joint.JOINT_STATS['lck']:
        return joint._luck_landmarks(current)
    if pid == joint.JOINT_STATS['dex']:
        return joint._dex_landmarks(current, compiled['roster'])
    if pid == joint.JOINT_STATS['spd']:
        luck = joint._effective(prepared, index, joint.JOINT_STATS['lck']) or 0
        return joint._speed_landmarks(current, luck, compiled['roster'])
    return []


def _landmarks(pid, index, prepared, compiled, profile):
    if pid in _SUPPORT_PIDS:
        return _support_landmarks(pid, index, prepared, compiled, profile)
    if pid in _JOINT_PIDS:
        return _joint_landmarks(pid, index, prepared, compiled)
    return []


def _point(question, field, value, reason, category, context, provenance,
           reference_value, tested, support_role):
    qid = question.get('id')
    fixed_fields = dict(question.get('fixedFields') or {})
    fixed_fields[field] = int(value)
    return dict(
        id=hashlib.sha256(domain.canonical(
            dict(questionId=qid, value=int(value), category=category)).encode('utf-8')).hexdigest(),
        kind=question.get('kind'),
        field=field,
        value=int(value),
        fixedFields=fixed_fields,
        adjustableFields=list(question.get('adjustableFields') or []),
        referenceId=question.get('referenceId'),
        frozenReference=question.get('referenceId'),
        referenceRevision=question.get('referenceRevision'),
        encounterRevision=question.get('encounterRevision'),
        mechanicsRevision=question.get('mechanicsRevision'),
        policy=deepcopy(question.get('policy')),
        reliability=deepcopy(question.get('reliability')),
        tolerance=question.get('tolerance'),
        # The frozen ``id`` keeps describing the overall question; the child point records its own value.
        questionId=qid,
        interventionReason=reason,
        category=category,
        referenceIdentical=(reference_value is not None and int(value) == int(reference_value)),
        tested=bool(tested),
        interpolation=False,
        safeCartesianProductClaimed=False,
        continuousCoverageClaimed=False,
        domainContext=context,
        constraintProvenance=provenance,
        referenceValue=None if reference_value is None else int(reference_value),
        supportRole=support_role,
    )


def boundary_points(question, reference_scenario, *, constraints=None, tested_points=(),
                    maximum=8):
    """Bounded, ordered child points for ONE frozen boundary/compensated/support question.

    ``question`` is the frozen question document (``kind``, ``field``, ``fixedFields``,
    ``adjustableFields``, ``referenceId`` and ``policy`` are carried onto every child
    unchanged). ``reference_scenario`` is the raw reference build the question is frozen
    against. ``tested_points`` are the already-measured values that must not be re-tested.
    ``maximum`` caps the returned list; ``0`` returns ``[]``.
    """
    if not isinstance(question, dict):
        return []
    if question.get('kind') not in KINDS:
        return []
    maximum = max(0, min(int(maximum), MAXIMUM_CAP))
    if not maximum:
        return []
    field = question.get('field')
    parsed, _reason = domain._parse_field(field or '')
    if not parsed:
        return []
    index, pid = parsed

    effective_constraints = constraints
    if effective_constraints is None:
        effective_constraints = question.get('constraints')
    if not effective_constraints:
        effective_constraints = {'context': 'synthetic'}

    try:
        reference = mechanics.normalize_scenario(reference_scenario)
        prepared = mechanics.prepared_setup(reference)
        compiled = compiler.compile_encounter(reference)
        profile = mechanics.profile(reference)
    except Exception:  # noqa: BLE001 - an unusable fixture yields no points, never a guess
        return []

    table, context, provenance = joint._domain_table(prepared, effective_constraints)
    span = table.get((index, pid))
    if span is None:
        return []
    fixed = span.get('fixed')
    if fixed is not None:
        legal_low = legal_high = int(fixed)
    else:
        legal_low, legal_high = int(span['low']), int(span['high'])
    reference_value = joint._effective(prepared, index, pid)
    if reference_value is None:
        return []

    support_role = None
    if question.get('kind') == 'support':
        unit = reference['ownUnits'][index] if 0 <= index < len(reference['ownUnits']) else None
        support_role = students.role(unit) if unit is not None else None
        # "Support role preserved": never a fodder durability repair, never another unit.
        if support_role not in (students.ROLE_DPS, students.ROLE_HEALER):
            return []

    requested = (question.get('fixedFields') or {}).get(field)
    if requested is None:
        requested = question.get('value')
    if not isinstance(requested, int) or isinstance(requested, bool):
        requested = None

    tested = _tested_values(tested_points)
    landmarks = sorted(set(_landmarks(pid, index, prepared, compiled, profile)))
    support_lowering = question.get('kind') == 'support'

    def legal(value):
        if not isinstance(value, int) or isinstance(value, bool):
            return False
        if value < legal_low or value > legal_high:
            return False
        if fixed is not None and value != fixed:
            return False
        return True

    placed = set()
    points = []

    def add(value, reason, category, *, generated):
        if not legal(value):
            return
        value = int(value)
        if value in placed or value in tested:
            return
        if generated and value == int(reference_value):
            return  # a reference-identical child is a no-op, not a boundary probe
        if support_lowering and generated and value > int(reference_value):
            return  # support boundaries never repair
        placed.add(value)
        points.append(_point(question, field, value, reason, category, context, provenance,
                             reference_value, False, support_role))

    # 1. The exact requested value goes first, if it is legal and untested.
    if requested is not None:
        add(requested, 'exact value requested by the frozen question', 'requested',
            generated=False)
    # 2. A fixed user constraint admits exactly one value.
    if fixed is not None:
        add(fixed, 'value fixed by the supplied build constraint', 'fixed', generated=False)

    # 3. An adjacent bracket around the requested (or reference) value, both directions.
    anchor = requested if requested is not None else int(reference_value)
    anchor_name = 'requested' if requested is not None else 'reference'
    add(anchor - 1, 'bracket one step below the %s value %d' % (anchor_name, anchor), 'bracket',
        generated=True)
    add(anchor + 1, 'bracket one step above the %s value %d' % (anchor_name, anchor), 'bracket',
        generated=True)

    # 4. Interior mechanical landmarks strictly between measured/known points only.
    anchors = sorted({int(reference_value)} | tested
                     | ({int(requested)} if requested is not None else set()))
    for low, high in zip(anchors, anchors[1:]):
        if high - low <= 1:
            continue
        for value in landmarks:
            if low < value < high:
                add(value, 'interior mechanical landmark strictly between measured points '
                           '%d and %d' % (low, high), 'interior', generated=True)

    # 5. Remaining discrete mechanical neighbours, nearest the requested value first.
    ordered = sorted(landmarks, key=lambda value: (abs(value - anchor), value))
    for value in ordered:
        add(value, 'discrete mechanical %s landmark neighbour' % _stat_name(pid),
            'neighbor', generated=True)

    # 6. Capped domain endpoints, named as the user bound or the verified search contract.
    question_bounds = (question.get('constraints') or {}).get('bounds')
    source = ('supplied user bound' if constraints is not None or question_bounds
              else 'the verified search-contract interval')
    add(legal_low, 'capped domain %s endpoint low (%s)' % (_stat_name(pid), source),
        'capped', generated=True)
    add(legal_high, 'capped domain %s endpoint high (%s)' % (_stat_name(pid), source),
        'capped', generated=True)

    return points[:maximum]


# --------------------------------------------------------------------------- #
# Adaptive extension
# --------------------------------------------------------------------------- #
def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    if not math.isfinite(value):
        return None
    return value


def _integer(value):
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _group_history(history):
    """Group paired history by (candidate, reference, compatibility) - never pool across refs."""
    groups = {}
    order = []
    for position, row in enumerate(history or []):
        if not isinstance(row, dict):
            continue
        candidate = row.get('candidateId')
        reference = row.get('referenceId')
        if candidate is None or reference is None:
            continue
        candidate, reference = str(candidate), str(reference)
        if candidate == reference:
            continue  # the reference arm is a comparator, never an extension target
        key = (candidate, reference, domain.canonical(row.get('compatibility')))
        entry = groups.get(key)
        if entry is None:
            entry = dict(candidateId=candidate, referenceId=reference,
                         compatibility=row.get('compatibility'), purpose=row.get('purpose'),
                         pairs=0, unknown=0, moments=[], experiments=0, last=position)
            groups[key] = entry
            order.append(key)
        pairs = _integer(row.get('pairs')) or 0
        unknown = _integer(row.get('unknownCount')) or 0
        mean = _number(row.get('pairedMeanDifference'))
        error = _number(row.get('pairedStandardError'))
        entry['pairs'] += pairs
        entry['unknown'] += unknown
        entry['experiments'] += 1
        entry['last'] = position
        entry['purpose'] = row.get('purpose') or entry['purpose']
        if mean is not None:
            entry['moments'].append((pairs, mean, error))
    return groups, order


def _aggregate(entry):
    """Sample-weighted mean and a conservative standard error inside one reference group.

    A KNOWN zero standard error stays ``0.0`` and an UNKNOWN one stays ``None``. A fully measured,
    zero-variance group is a determinate result, not a missing one: collapsing a known ``0.0`` onto
    ``None`` would relabel an already-exact decision as an open question and let the scheduler spend
    budget on samples that can add no knowledge.
    """
    moments = entry['moments']
    weight = sum(pairs for pairs, _mean, _error in moments)
    if weight <= 0:
        return None, None
    mean = sum(pairs * value for pairs, value, _error in moments) / weight
    if not any(error is not None for _pairs, _value, error in moments):
        return mean, None  # every moment's SE is unknown: the interval stays genuinely unknown
    variance = 0.0
    for pairs, value, error in moments:
        if error is not None:
            variance += (pairs * error) ** 2
    return mean, math.sqrt(variance) / weight


def choose_extension(history, portfolio, *, remaining_runs, max_pairs=64, round_index=0):
    """One under-sampled, upside-plausible challenger to extend, or ``None``.

    The returned mapping is ``{candidateId, referenceId, additionalPairs, undecided, reason}``.
    ``additionalPairs`` moves the challenger to the next finite stage (4 -> 8 -> 16 -> 32 -> 64);
    ``max_pairs`` is a TOTAL target cap, so a stage is only chosen when its target is within it and
    the whole paired stage fits the remaining budget. A resolved-poor challenger (mean + 2*SE < 0) is
    refused, a challenger with a KNOWN zero standard error is refused (no sample can add knowledge to
    a determinate result - it is preserved for independent confirmation), and a wholly unresolved or
    already-maxed group is never extended. Extra samples are explicit knowledge charged to the
    improvement budget, so the frozen comparison holdout is preserved. ``portfolio`` restricts which
    candidates are in scope.
    """
    remaining_runs = int(remaining_runs or 0)
    max_pairs = max(0, min(int(max_pairs), EXTENSION_STAGES[-1]))
    if remaining_runs // _RUNS_PER_PAIR < 1:
        return None  # too little remains to buy even one complete pair

    groups, order = _group_history(history)
    if not groups:
        return None

    allowed = None
    if portfolio:
        allowed = set()
        for row in portfolio:
            if not isinstance(row, dict):
                continue
            if row.get('arm') == 'reference':
                continue
            identifier = row.get('candidateId') or row.get('key')
            if identifier is not None:
                allowed.add(str(identifier))
    if allowed:
        order = [key for key in order if key[0] in allowed]
    if not order:
        return None

    best = None
    for key in order:
        entry = groups[key]
        mean, error = _aggregate(entry)
        if mean is None:
            continue  # no usable paired mean yet
        pairs = entry['pairs']
        unknown = entry['unknown']
        if pairs > 0 and unknown >= pairs:
            continue  # wholly unresolved: block the claim, do not retry endlessly
        stages = [stage for stage in EXTENSION_STAGES if stage > pairs]
        if not stages:
            continue  # already at the largest finite stage
        target = stages[0]
        if target > max_pairs:
            continue  # the next finite stage would exceed the declared TOTAL pair cap
        additional = target - pairs
        if additional > remaining_runs // _RUNS_PER_PAIR:
            continue  # the remaining budget cannot fund the COMPLETE paired stage
        if error is not None and error <= 1e-12:
            continue  # a known, determinate result: no sample can add knowledge to it
        lower = mean - 2 * error if error is not None else None
        upper = mean + 2 * error if error is not None else None
        if lower is not None and upper is not None and upper < 0:
            continue  # resolved poor: a harmful challenger is not extended
        if mean <= 0 and (upper is None or upper <= 0):
            continue  # not upside-plausible
        if abs(mean) < 1e-12 and (error is None or error < 1e-12):
            continue  # repeated empty ticks carry no signal to resolve
        undecided = error is None or (lower <= 0 <= upper)
        upside = upper if upper is not None else mean
        rank = (0 if undecided else 1, 0 if mean > 0 else 1, -upside, pairs, entry['last'])
        row = dict(candidateId=entry['candidateId'], referenceId=entry['referenceId'],
                   additionalPairs=int(additional), pairs=int(pairs), target=int(target),
                   mean=mean, error=error, undecided=bool(undecided),
                   rank=rank, purpose=entry['purpose'])
        if best is None or rank < best['rank']:
            best = row
    if best is None:
        return None
    interval = ('SE unknown' if best['error'] is None
                else 'SE %.6g' % best['error'])
    reason = ('under-sampled upside-plausible challenger: %d pairs, mean difference %.6g (%s) '
              'leave the decision %s; add %d pairs to reach the finite stage of %d under the '
              'improvement/development budget. Extra samples are explicit knowledge, not earning '
              'gain, and the frozen comparison holdout is untouched.'
              % (best['pairs'], best['mean'], interval,
                 'undecided' if best['undecided'] else 'not yet resolved',
                 best['additionalPairs'], best['target']))
    return dict(candidateId=best['candidateId'], referenceId=best['referenceId'],
                additionalPairs=int(best['additionalPairs']), target=int(best['target']),
                undecided=bool(best['undecided']), reason=reason)


__all__ = ['VERSION', 'KINDS', 'MAXIMUM_CAP', 'EXTENSION_STAGES', 'MINIMUM_PAIRS',
           'boundary_points', 'choose_extension']
