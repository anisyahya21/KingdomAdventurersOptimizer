"""Player operating-region read model (bounded, read-only, presentation only).

This module answers the player's question - "which measured build alternatives operate at the
reward I care about, and what is still unknown?" - without inventing facts. It is a *pure*
synthesis over the coordinator's already-published encounter snapshot
(``status()['encounterAware']``) plus, optionally, the exact scenario of each snapshot candidate
read through a bounded read-only library connection.

Hard rules, enforced here rather than left to a caller:

* No battle, no seed, no reward prediction and no cross-candidate reward pooling. A point's
  ``meanEarned`` is the value the snapshot already measured for *that exact candidate*; a missing
  or censored mean stays ``None`` and is never rewritten to zero or to a favourable subset.
* Structure is identity. Two candidates share an archetype only when their canonical normalized
  scenario is identical after removing *only* the seven adjustable stat values (HP, MP, ATK, DEF,
  SPD, LCK, DEX). Formation, skills and their order, invocation, weapon/equipment behaviour, the
  finish policy, the resource policy, the encounter and every known revision stay in the key, so
  nearby vectors are never merged and a role, policy or horizon difference splits the group.
* Every measurement binds to the candidate that actually produced it. A frozen boundary question
  is never copied onto its reference, and a boundary-only child gets its OWN identity/stats
  (its loaded scenario) rather than the reference's or the parent candidate's.
* Portfolio evidence and development boundary evidence stay separate windows: they are never
  pooled, and a boundary mean is reported only when the paired assessment resolved every sample.
* Gaps, unbracketed ends and failed tolerance tests stay explicit. There is no interpolation, no
  safe Cartesian box and no monotonic extrapolation. ``lowImpact`` stays empty and ``support`` is
  only filled from a *controlled* boundary assessment that actually accepted the tested point;
  otherwise the answer is ``unknown``.

The pure entry point ``build_summary`` has no database dependency. ``build_summary_from_library``
adds the bounded read-only candidate load and delegates straight back to it, so a future website or
another read-only surface can reuse the same synthesis without importing the desktop bridge.
"""
from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy

import strategy_encounter_presentation as presentation
import strategy_mechanics as mechanics
import strategy_students as students

READ_MODEL_VERSION = 'strategy-player-regions-1'
SYNTHESIS_REVISION = 'player-region-synthesis-1'
SOURCE_REVISION = 'player-region-source-1'

#: Hard cap on how many distinct candidate scenarios one summary reads/represents. Bounds the
#: read-only SQL batch and the per-refresh synthesis cost; ``coverage.truncated`` says so when hit.
CANDIDATE_LIMIT = 256

#: Candidate-scenario rows fetched per ``IN (...)`` statement so one query never grows unbounded.
SCENARIO_BATCH = 200

#: The seven effective stat values the search is free to adjust. They are the only values stripped
#: from the structural key; every other behavioural input is preserved.
ADJUSTABLE_PARAMETER_IDS = tuple(sorted(set(presentation.STAT_PARAMETER_IDS.values())))

REGION_KINDS = ('tested-alternatives', 'fixed-build', 'compensated', 'support')

LIMITATIONS = [
    'Read-only presentation over the coordinator snapshot: no battle is run, no seed is consumed '
    'and no whole-battle reward is predicted.',
    'Every point is an exact tested candidate. Gaps are never interpolated and no safe box or '
    'monotonic extrapolation is claimed; untested values above and below every listed point stay '
    'unknown.',
    'Archetypes share a structural identity that removes only the seven adjustable stat values; '
    'nearby vectors are never merged and a policy, skill, horizon or context difference splits the '
    'group.',
    'Evidence is never pooled across candidates, across windows, across skills, across policy or '
    'across context. A censored/unresolved sample leaves the full mean unknown even when a partial '
    'mean exists.',
    'Boundary/compensated evidence is development-only diagnostic evidence; it is never a holdout '
    'confirmation unless the point itself was explicitly confirmed.',
    'Job/equipment conversion is not implemented; unknown provenance and unknown reachability stay '
    'unknown.',
]


# --------------------------------------------------------------------------- #
# Snapshot enumeration
# --------------------------------------------------------------------------- #
def _cid(row):
    if not isinstance(row, dict):
        return None
    value = row.get('candidateId')
    if value is None:
        value = row.get('key')
    return None if value is None else str(value)


def _reference_id(value):
    """Normalise a frozen reference that may be a str, a JSON-encoded str or a dict."""
    if value is None:
        return None
    if isinstance(value, dict):
        for key in ('referenceId', 'candidateId', 'id', 'frozenReference', 'reference', 'key'):
            inner = value.get(key)
            if inner is not None:
                found = _reference_id(inner)
                if found:
                    return found
        return None
    if isinstance(value, (list, tuple)):
        for item in value:
            found = _reference_id(item)
            if found:
                return found
        return None
    text = str(value).strip()
    if not text:
        return None
    if text[0] in '"{[':
        try:
            return _reference_id(json.loads(text))
        except ValueError:
            return text
    return text


def _boundary_reference(row):
    return _reference_id(row.get('frozenReference')) if isinstance(row, dict) else None


def _boundary_cids(row):
    """Every candidate a boundary row references, for enumeration/loading only."""
    out = []

    def add(value):
        cid = _reference_id(value)
        if cid and cid not in out:
            out.append(cid)

    if not isinstance(row, dict):
        return out
    add(row.get('frozenReference'))
    for field in ('testedPoints', 'unresolvedGaps'):
        for pack in row.get(field) or []:
            if isinstance(pack, dict):
                add(pack.get('candidateId'))
    return out


def _boundary_child_packs(row):
    """The ``(field, child_id, pack)`` measurements a boundary row actually tested.

    The frozen reference is not a tested child and is never returned, so a row's measurement can
    never be mirrored onto the reference's archetype.
    """
    out = []
    if not isinstance(row, dict):
        return out
    reference = _boundary_reference(row)
    for field in ('testedPoints', 'unresolvedGaps'):
        for pack in row.get(field) or []:
            if not isinstance(pack, dict):
                continue
            raw = pack.get('candidateId')
            cid = None if raw is None else str(raw)
            if cid is None or cid == reference:
                continue
            out.append((field, cid, pack))
    return out


def _diverse_sample(ids, count):
    """Deterministic spread over a ranked list that keeps both ends (best AND lowest/failed)."""
    n = len(ids)
    if count <= 0 or n == 0:
        return []
    if count >= n:
        return list(ids)
    if count == 1:
        return [ids[0]]
    picks = []
    for index in range(count):
        position = int(round(index * (n - 1) / float(count - 1)))
        cid = ids[position]
        if cid not in picks:
            picks.append(cid)
    return picks


def collect_candidate_ids(snapshot, *, limit=CANDIDATE_LIMIT):
    """``(ordered capped ids, available total)`` from the snapshot's own candidate references."""
    portfolio_ids = []
    boundary_ids = []
    seen = set()

    def add(target, value):
        cid = _reference_id(value)
        if cid is None or cid in seen:
            return
        seen.add(cid)
        target.append(cid)

    if isinstance(snapshot, dict):
        for row in snapshot.get('portfolio') or []:
            if isinstance(row, dict):
                add(portfolio_ids, _cid(row))
        for row in snapshot.get('boundaries') or []:
            if isinstance(row, dict):
                for cid in _boundary_cids(row):
                    add(boundary_ids, cid)
        block = snapshot.get('presentation')
        if isinstance(block, dict):
            add(boundary_ids, block.get('referenceId'))
    available = len(seen)

    priority = []
    for cid in boundary_ids:
        if cid not in priority:
            priority.append(cid)
    if not isinstance(limit, int) or limit <= 0:
        return priority + [cid for cid in portfolio_ids if cid not in priority], available
    ordered = list(priority[:limit])
    remaining = limit - len(ordered)
    if remaining > 0:
        chosen = set(ordered)
        rest = [cid for cid in portfolio_ids if cid not in chosen]
        ordered += rest if remaining >= len(rest) else _diverse_sample(rest, remaining)
    return ordered, available


# --------------------------------------------------------------------------- #
# Bounded read-only candidate load
# --------------------------------------------------------------------------- #
def _row_pair(row):
    try:
        keys = row.keys()
    except AttributeError:
        keys = None
    if keys is not None and 'id' in keys and 'scenario' in keys:
        return row['id'], row['scenario']
    if isinstance(row, (tuple, list)) and len(row) >= 2:
        return row[0], row[1]
    return None, None


def _decode_scenario(raw):
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except ValueError:
            return None
    return None


def load_candidate_scenarios(readonly_db, library, candidate_ids, *, batch=SCENARIO_BATCH):
    """Read the stored scenario of each requested candidate through a read-only connection."""
    out = {}
    ids = [str(cid) for cid in candidate_ids if cid is not None]
    if not ids:
        return out
    db = readonly_db(library)
    try:
        for start in range(0, len(ids), batch):
            chunk = ids[start:start + batch]
            marks = ','.join('?' * len(chunk))
            rows = db.execute('SELECT id, scenario FROM candidate WHERE id IN (%s)' % marks,
                              chunk).fetchall()
            for row in rows:
                cid, raw = _row_pair(row)
                scenario = _decode_scenario(raw)
                if cid is not None and scenario is not None:
                    out[str(cid)] = scenario
    finally:
        db.close()
    return out


# --------------------------------------------------------------------------- #
# Pure synthesis
# --------------------------------------------------------------------------- #
def structural_key(normalized, *, revisions, context, extra=None):
    """Canonical structural identity: raw behaviour minus ONLY the seven adjustable stat values."""
    body = deepcopy(normalized)
    body.pop('mathSeed', None)
    body.pop('libSeed', None)
    for unit in body.get('ownUnits') or []:
        if not isinstance(unit, dict):
            continue
        parameters = unit.get('parameters')
        if isinstance(parameters, dict):
            for pid in ADJUSTABLE_PARAMETER_IDS:
                parameters.pop(pid, None)
                parameters.pop(str(pid), None)
    payload = dict(scenario=body, revisions=revisions or {}, context=context or 'unknown',
                   extra=extra or {})
    return hashlib.sha256(mechanics.canonical(payload).encode('utf-8')).hexdigest()


def _stable_id(text):
    return hashlib.sha256(str(text).encode('utf-8')).hexdigest()[:16]


def _revisions(block):
    """The actual ``presentation.describe`` revision shape; never a guessed key."""
    details = block.get('encounterDetails') if isinstance(block.get('encounterDetails'), dict) else {}
    summary = block.get('mechanicsSummary') if isinstance(block.get('mechanicsSummary'), dict) else {}
    encounter_revision = summary.get('encounterRevision')
    if encounter_revision is None:
        encounter_revision = details.get('revision')
    return dict(encounterRevision=encounter_revision,
                mechanicsRevision=summary.get('mechanicsRevision'),
                compilerRevision=summary.get('compilerRevision'))


def _source_revision(snapshot, block):
    """The loaded backend revision when the snapshot exposes it, else a digest of known provenance."""
    runtime = snapshot.get('runtimeRevision') if isinstance(snapshot, dict) else None
    if runtime:
        return str(runtime)
    payload = dict(_revisions(block), presentationVersion=block.get('version'), source=SOURCE_REVISION)
    return hashlib.sha256(mechanics.canonical(payload).encode('utf-8')).hexdigest()


def _scope(snapshot, block):
    details = block.get('encounterDetails') if isinstance(block.get('encounterDetails'), dict) else {}
    raw = snapshot.get('encounter') if isinstance(snapshot, dict) else None
    if isinstance(raw, dict):
        encounter_id = details.get('encounterId', raw.get('encounterId'))
    elif raw is not None:
        encounter_id = details.get('encounterId', raw)
    else:
        encounter_id = details.get('encounterId')
    return dict(encounterId=encounter_id, referenceId=_reference_id(block.get('referenceId')),
                mode=snapshot.get('mode') if isinstance(snapshot, dict) else None)


def _normalize_scenarios(candidate_scenarios):
    result = {}
    if not isinstance(candidate_scenarios, dict):
        return result
    for cid, scenario in candidate_scenarios.items():
        if cid is None:
            continue
        decoded = _decode_scenario(scenario)
        if decoded is not None:
            result[str(cid)] = decoded
    return result


def _context(row):
    """The candidate's declared context/provenance from the ACTUAL public row fields."""
    if not isinstance(row, dict):
        return 'unknown'
    declared = row.get('context')
    if declared is None:
        declared = row.get('domainContext')
    if declared:
        return str(declared)
    if row.get('synthetic') is True:
        return 'synthetic'
    if row.get('playerUnknown') is True:
        return 'player'
    if row.get('provenanceStatus') == 'declared':
        return 'synthetic'
    return 'unknown'


def _boundary_context(row):
    if not isinstance(row, dict):
        return 'unknown'
    for source in (row, row.get('study'), row.get('assessment')):
        found = _context(source)
        if found != 'unknown':
            return found
    return 'unknown'


def _effective_stats(normalized, prepared):
    roles, role_status = presentation._role_indices(normalized)
    stats = {}
    for entry in presentation.question_fields(normalized, prepared, roles):
        value = entry.get('currentValue')
        if value is None:
            continue
        stats['%s.%s' % (entry['role'], entry['stat'])] = value
    return stats, roles, role_status


def _resource_policy(normalized):
    """The exact known finish/resource policy of a scenario, or ``None`` when unknown."""
    if not isinstance(normalized, dict):
        return None
    policy = {}
    if normalized.get('finishPolicy') is not None:
        policy['finishPolicy'] = normalized.get('finishPolicy')
    try:
        consumables = mechanics.profile(normalized).get('consumables')
    except Exception:  # noqa: BLE001 - a missing policy stays unknown, never guessed
        consumables = None
    if isinstance(consumables, dict):
        for key, value in consumables.items():
            if value is not None:
                policy[key] = value
    return policy or None


def _identity(cid, scenario, block, context, *, engine_revision=None, compatibility=None):
    revisions = _revisions(block)
    extra = dict(engineRevision=engine_revision, compatibility=compatibility)

    def unresolved(reason):
        key = 'unknown:%s' % cid
        return dict(structuralKey=key, context=context, identity=_stable_id(key), stats={},
                    roles={}, roleStatus='unknown', normalized=None, revisions=revisions,
                    resourcePolicy=None, reasons=[reason])

    if scenario is None:
        return unresolved('scenario unavailable for this candidate; its own stats, structure and '
                          'reachability are unknown')
    try:
        normalized = mechanics.normalize_scenario(scenario)
    except Exception as exc:  # noqa: BLE001 - an unreadable scenario is reported, never guessed
        return unresolved('scenario could not be normalized: %s' % (exc,))
    key = structural_key(normalized, revisions=revisions, context=context, extra=extra)
    reasons = []
    try:
        prepared = mechanics.prepared_setup(normalized)
        stats, roles, role_status = _effective_stats(normalized, prepared)
    except Exception as exc:  # noqa: BLE001 - missing effective stats stay unknown, not zero
        stats, roles, role_status = {}, {}, 'unknown'
        reasons.append('effective stats unavailable: %s' % (exc,))
    return dict(structuralKey=key, context=context, identity=_stable_id(key), stats=stats, roles=roles,
                roleStatus=role_status, normalized=normalized, revisions=revisions,
                resourcePolicy=_resource_policy(normalized), reasons=reasons)


def _blank_identity(cid):
    key = 'unknown:%s' % cid
    return dict(structuralKey=key, context='unknown', identity=_stable_id(key), stats={}, roles={},
                roleStatus='unknown', normalized=None, revisions={}, resourcePolicy=None, reasons=[])


def _num_or_none(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return value if math.isfinite(value) else None
        except TypeError:
            return None
    return None


def _int_or_none(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _evidence_status(row):
    """Evidence window/class. A confirmation label needs an EXPLICIT confirmed window."""
    if not isinstance(row, dict):
        return 'unknown'
    window = row.get('window')
    confirmed = (row.get('confirmation') is True
                 or row.get('confirmationStatus') in ('confirmed', 'holdout', 'confirmed-holdout')
                 or row.get('independentConfirmation') is True)
    if window == 'holdout' and confirmed:
        return 'confirmed-holdout'
    if window == 'development' or row.get('development') is True:
        return 'development'
    if row.get('evidenceClass'):
        return str(row['evidenceClass'])
    if window:
        return str(window)
    return 'unknown'


def _classification(cid, row, scope):
    if scope.get('referenceId') is not None and str(scope['referenceId']) == cid:
        return 'reference'
    return 'measured'


def _portfolio_full_mean(row):
    """The row's full mean, kept ONLY when completeness is provable."""
    if not isinstance(row, dict):
        return None
    raw = row.get('meanEarned') if 'meanEarned' in row else row.get('fullMean')
    mean = _num_or_none(raw)
    if mean is None:
        return None
    unknown = _int_or_none(row.get('unknownCount'))
    censored = _int_or_none(row.get('censoredCount'))
    errors = _int_or_none(row.get('errorCount'))
    resolved = row.get('resolved')
    if resolved is None:
        resolved = row.get('samples')
    resolved = _int_or_none(resolved)
    total = _int_or_none(row.get('total'))
    if unknown is None or total is None or resolved is None:
        return None
    if unknown or (censored or 0) or (errors or 0) or resolved != total:
        return None
    return mean


def _portfolio_point(cid, row, identity, scope):
    point = dict(
        candidateId=cid,
        label=str(row.get('label') or cid),
        stats=dict(identity.get('stats') or {}),
        meanEarned=_portfolio_full_mean(row),
        partialMean=_num_or_none(row.get('partialMean')),
        samples=_int_or_none(row.get('samples', row.get('resolved'))),
        total=_int_or_none(row.get('total')),
        unknownCount=_int_or_none(row.get('unknownCount')),
        uncertainty=row.get('uncertainty') if isinstance(row.get('uncertainty'), dict) else None,
        median=_num_or_none(row.get('median')),
        classification=_classification(cid, row, scope),
        evidenceStatus=_evidence_status(row),
        context=identity['context'],
        reachability='unknown',
        resourcePolicy=identity.get('resourcePolicy'))
    if row.get('quantiles') is not None:
        point['quantiles'] = row['quantiles']
    thresholds = row.get('thresholds')
    if thresholds is None:
        thresholds = row.get('thresholdProbabilities')
    if thresholds is not None:
        point['thresholds'] = thresholds
    reference = _reference_id(row.get('referenceId'))
    if reference is None:
        reference = scope.get('referenceId')
    if reference is not None:
        point['referenceId'] = reference
    if row.get('changedFields') is not None:
        point['changedFields'] = list(row['changedFields'])
    if row.get('fixedFields') is not None:
        point['fixedFields'] = row['fixedFields']
    return point


def _boundary_assessment_mean(assessment):
    """The child's own known mean from a paired assessment, or ``None`` when it is not proven."""
    if not isinstance(assessment, dict):
        return None
    mean = _num_or_none(assessment.get('meanEarned'))
    if mean is None:
        return None
    unresolved_reference = _int_or_none(assessment.get('unresolvedReference'))
    unresolved_candidate = _int_or_none(assessment.get('unresolvedCandidate'))
    total_reference = _int_or_none(assessment.get('totalReference'))
    total_candidate = _int_or_none(assessment.get('totalCandidate'))
    paired = _int_or_none(assessment.get('pairedCount'))
    if unresolved_reference != 0 or unresolved_candidate != 0:
        return None
    if total_reference is None or total_candidate is None or paired is None:
        return None
    if paired <= 0 or paired != total_reference or paired != total_candidate:
        return None
    return mean


def _boundary_uncertainty(assessment, pack):
    method = assessment.get('uncertaintyMethod') or pack.get('uncertaintyMethod')
    entry = dict(kind=method or 'paired percentile bootstrap (diagnostic)')
    for key, value in (('pairedCount', assessment.get('pairedCount', pack.get('pairedCount'))),
                       ('diagnosticInterval',
                        assessment.get('diagnosticInterval', pack.get('diagnosticInterval'))),
                       ('alpha', assessment.get('alpha')),
                       ('resamples', assessment.get('resamples'))):
        if value is not None:
            entry[key] = value
    return entry


def _boundary_evidence_status(assessment):
    """Boundary evidence is development unless the point itself was explicitly confirmed."""
    if not isinstance(assessment, dict):
        return 'unknown'
    window = assessment.get('window')
    confirmed = (assessment.get('confirmation') is True
                 or assessment.get('independentConfirmation') is True
                 or assessment.get('confirmationStatus') in ('confirmed', 'confirmed-holdout'))
    if window == 'holdout' and confirmed:
        return 'confirmed-holdout'
    return str(window or 'development')


def _boundary_point(child_cid, pack, identity, row, scope, frozen_question=None):
    assessment = row.get('assessment') if isinstance(row.get('assessment'), dict) else {}
    value = pack.get('value') if isinstance(pack.get('value'), dict) else {}
    reference = (_reference_id(assessment.get('referenceId')) or _boundary_reference(row)
                 or scope.get('referenceId'))
    policy = identity.get('resourcePolicy')
    if isinstance(frozen_question, dict) and frozen_question.get('policy') is not None:
        policy = frozen_question['policy']
    point = dict(
        candidateId=str(pack.get('candidateId', child_cid)),
        label=str(child_cid),
        stats=dict(identity.get('stats') or {}),
        meanEarned=_boundary_assessment_mean(assessment),
        partialMean=_num_or_none(assessment['partialMean']) if 'partialMean' in assessment else None,
        samples=_int_or_none(assessment.get('pairedCount', pack.get('pairedCount'))),
        total=_int_or_none(assessment.get('totalCandidate')),
        unknownCount=_int_or_none(assessment.get('unresolvedCandidate')),
        uncertainty=_boundary_uncertainty(assessment, pack),
        classification=str(pack.get('classification') or assessment.get('classification')
                           or 'unresolved'),
        evidenceStatus=_boundary_evidence_status(assessment),
        context=identity['context'],
        reachability='unknown',
        resourcePolicy=policy,
        referenceId=reference)
    reference_mean = _num_or_none(assessment.get('referenceMeanEarned'))
    if reference_mean is not None:
        point['referenceMeanEarned'] = reference_mean
    tolerance = _num_or_none(assessment.get('tolerance'))
    if tolerance is not None:
        point['tolerance'] = tolerance
    if value.get('field') is not None:
        point['changedFields'] = [str(value['field'])]
    if value:
        point['fixedFields'] = value
    if isinstance(frozen_question, dict) and frozen_question.get('fixedFields') is not None:
        point['fixedFields'] = frozen_question['fixedFields']
    return point


def _rid(key, suffix):
    payload = dict(structuralKey=key[0], context=key[1], kind=suffix)
    return hashlib.sha256(mechanics.canonical(payload).encode('utf-8')).hexdigest()[:16]


def _fixed_conditions(identity):
    normalized = identity.get('normalized')
    if not isinstance(normalized, dict):
        return ['structural requirements are unknown for this candidate']
    conditions = []
    roles = identity.get('roles') or {}
    units = normalized.get('ownUnits') or []
    parts = []
    for role in presentation.ROLE_ORDER:
        index = roles.get(role)
        if isinstance(index, int) and 0 <= index < len(units):
            parts.append('%s=%s' % (role.upper(), units[index].get('name')))
    if parts:
        conditions.append('formation: ' + ', '.join(parts))
    skills = []
    for unit in units:
        ids = list(unit.get('skills') or [])
        if ids:
            skills.append('%s=[%s]' % (unit.get('name'), ', '.join(str(x) for x in ids)))
    if skills:
        conditions.append('skills: ' + '; '.join(skills))
    if normalized.get('finishPolicy') is not None:
        conditions.append('finish policy: %s' % normalized.get('finishPolicy'))
    if normalized.get('encounterId') is not None:
        conditions.append('encounter %s (defeat count %s)'
                          % (normalized.get('encounterId'), normalized.get('defeatCount')))
    if normalized.get('tickLimit') is not None:
        conditions.append('tick limit: %s' % normalized.get('tickLimit'))
    conditions.append('context: %s' % identity['context'])
    return conditions


def _tested_region(key, entry, scope):
    conditions = _fixed_conditions(entry['identity'])
    conditions.append('alternatives share these fixed conditions and differ only in the adjustable '
                      'effective stats (HP, MP, ATK, DEF, SPD, LCK, DEX); each point lists its own '
                      'exact stats and fields it changed')
    gaps = []
    for cid, row in entry['boundaryRows']:
        for field, child, pack in _boundary_child_packs(row):
            if field == 'unresolvedGaps' and child in entry['members'] and pack.get('reason'):
                gaps.append('candidate %s: %s' % (child, pack['reason']))
    gaps.append('only the listed tested points are measured; untested stat combinations and the '
                'ends of every axis stay unknown (no interpolation or safe box)')
    return dict(id=_rid(key, 'tested-alternatives'), label='Tested alternatives',
                kind='tested-alternatives', conditions=conditions, points=list(entry['points']),
                gaps=gaps, lowImpact=[], support=[], continuousCoverage=False,
                safeCartesianProduct=False)


def _boundary_region(key, identifier, row, members, identities, scope, frozen_question, identity):
    kind = row.get('kind') if row.get('kind') in REGION_KINDS else 'support'
    packs = [(field, cid, pack) for field, cid, pack in _boundary_child_packs(row) if cid in members]
    tested = [(cid, pack) for field, cid, pack in packs if field == 'testedPoints']
    gaps = [str(pack['reason']) for field, cid, pack in packs
            if field == 'unresolvedGaps' and pack.get('reason')]
    points = [_boundary_point(cid, pack, identities.get(cid) or _blank_identity(cid), row, scope,
                              frozen_question) for cid, pack in tested]
    values = []
    for _child, pack in tested:
        value = pack.get('value') if isinstance(pack.get('value'), dict) else {}
        number = value.get('value')
        if isinstance(number, int) and not isinstance(number, bool):
            values.append(number)
    if values:
        gaps.append('tested values span %s..%s only; smaller and larger values are untested and '
                    'unknown' % (min(values), max(values)))
    if not tested:
        gaps.append('this boundary recorded a failure/gap rather than an accepted tested point; no '
                    'coverage is claimed')
    else:
        gaps.append('values between and outside the listed tested points are unknown; no continuous '
                    'coverage is inferred')
    conditions = _fixed_conditions(identity)
    changed = row.get('childValue') if isinstance(row.get('childValue'), dict) else {}
    if changed.get('field') is not None:
        conditions.append('tested %s' % changed.get('field'))
    if isinstance(frozen_question, dict) and frozen_question.get('id') is not None:
        conditions.append('frozen question %s' % frozen_question.get('id'))
    assessment = row.get('assessment') if isinstance(row.get('assessment'), dict) else {}
    support = []
    if assessment.get('classification') == 'supported-acceptable':
        support.append('controlled boundary assessment accepted this tested point (paired n=%s)'
                       % assessment.get('pairedCount'))
    child_key = ','.join(sorted({cid for _field, cid, _pack in packs}))
    return dict(id=_rid(key, 'boundary-%s-%s' % (identifier, child_key)),
                label='%s region' % kind, kind=kind, conditions=conditions, points=points,
                gaps=gaps, lowImpact=[], support=support, continuousCoverage=False,
                safeCartesianProduct=False)


def _match_frozen_question(question, row):
    if not isinstance(question, dict) or not isinstance(row, dict):
        return None
    qid = row.get('questionId')
    if qid is not None and question.get('id') is not None and str(qid) == str(question['id']):
        return question
    return None


def _attach_reference_deltas(entry, scope):
    reference_id = scope.get('referenceId')
    reference = None
    for point in entry['points']:
        if reference_id is not None and point['candidateId'] == str(reference_id):
            reference = point
            break
    if reference is None or not reference.get('stats'):
        return
    keys = set(reference['stats'])
    for point in entry['points']:
        keys |= set(point.get('stats') or {})
    for point in entry['points']:
        if point is reference:
            continue
        stats = point.get('stats') or {}
        changed = [key for key in sorted(keys) if stats.get(key) != reference['stats'].get(key)]
        if changed:
            existing = list(point.get('changedFields') or [])
            point['changedFields'] = existing + [key for key in changed if key not in existing]


def _fixed_requirements(identity, scope):
    normalized = identity.get('normalized')
    revisions = identity.get('revisions') or {}
    context = identity['context']
    structural = identity['structuralKey']
    if normalized is None:
        return dict(formation=None, skills=None, policy=None,
                    other=dict(context=context, structuralKey=structural, revisions=revisions,
                               status='unknown',
                               reason='scenario unavailable; structural requirements are unknown'))
    units = normalized.get('ownUnits') or []
    roster = [unit.get('name') for unit in units]
    try:
        placed = [dict(grid=int(row['grid']), name=row['unit'].get('name'))
                  for row in students._placed_rows(normalized)]
        placement_status = 'resolved'
    except Exception:  # noqa: BLE001 - an unresolved placement is reported, never guessed
        placed = None
        placement_status = 'unknown'
    skills = [dict(unit=unit.get('name'), skills=list(unit.get('skills') or []),
                   invocationLevels=list(unit.get('invocationLevels') or []),
                   weaponId=unit.get('weaponId')) for unit in units]
    return dict(formation=dict(roster=roster, placed=placed, placementStatus=placement_status),
                skills=skills, policy=normalized.get('finishPolicy'),
                other=dict(context=context, structuralKey=structural,
                           encounterId=normalized.get('encounterId'),
                           defeatCount=normalized.get('defeatCount'),
                           tickLimit=normalized.get('tickLimit'),
                           startProfile=normalized.get('startProfile'),
                           revisions=revisions))


def _archetype_label(identity):
    normalized = identity.get('normalized')
    if normalized is None:
        return 'Archetype %s (structure unknown)' % identity['identity'][:8]
    units = normalized.get('ownUnits') or []
    roles = identity.get('roles') or {}
    parts = []
    for role in presentation.ROLE_ORDER:
        index = roles.get(role)
        if isinstance(index, int) and 0 <= index < len(units):
            parts.append('%s=%s' % (role.upper(), units[index].get('name')))
    if normalized.get('finishPolicy'):
        parts.append('policy=%s' % normalized['finishPolicy'])
    return ' · '.join(parts) if parts else 'Archetype %s' % identity['identity'][:8]


def _archetype(entry, regions, scope):
    identity = entry['identity']
    context = identity['context']
    unknowns = list(identity.get('reasons') or [])
    if context == 'unknown':
        unknowns.append('candidate provenance/context is unknown; synthetic and player domains are '
                        'never assumed to be equivalent')
    if identity.get('resourcePolicy') is None:
        unknowns.append('resource/finish policy is unknown for this candidate; no consumption '
                        'policy is assumed')
    unknowns.append('support conditions and low-impact claims are unknown unless a controlled '
                    'boundary assessment establishes them')
    boundary_rows = {id(row) for _, row in entry['boundaryRows']}
    if not boundary_rows:
        unknowns.append('no frozen boundary question is attached, so unbracketed ends and gaps are '
                        'unknown')
    return dict(id=identity['identity'], label=_archetype_label(identity),
                description=('%d measured candidate point(s) and %d boundary row(s). Conditional '
                             'alternatives share this structural identity and context; they differ '
                             'only in the adjustable effective stats.'
                             % (len(entry['points']), len(boundary_rows))),
                fixedRequirements=_fixed_requirements(identity, scope), context=context,
                reachability='unknown', regions=regions, unknowns=unknowns)


def build_summary(snapshot, candidate_scenarios=None, *, candidate_limit=CANDIDATE_LIMIT):
    """Pure synthesis of the player region read model. No database, no battle, no prediction."""
    scenarios = _normalize_scenarios(candidate_scenarios)
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    block = snapshot.get('presentation') if isinstance(snapshot.get('presentation'), dict) else {}
    scope = _scope(snapshot, block)
    ids, available = collect_candidate_ids(snapshot, limit=candidate_limit)
    coverage = dict(candidateLimit=int(candidate_limit), availableCandidates=int(available),
                    includedCandidates=0, truncated=bool(available > len(ids)))
    base = dict(version=READ_MODEL_VERSION, synthesisRevision=SYNTHESIS_REVISION,
                sourceRevision=_source_revision(snapshot, block), status='unavailable', scope=scope,
                archetypes=[], coverage=coverage, limitations=list(LIMITATIONS))
    if not snapshot:
        base['reason'] = 'encounter-aware snapshot unavailable'
        return base
    portfolio = [row for row in snapshot.get('portfolio') or [] if isinstance(row, dict)]
    boundaries = [row for row in snapshot.get('boundaries') or [] if isinstance(row, dict)]
    if not portfolio and not boundaries:
        base['reason'] = 'encounter snapshot contains no candidate observations'
        return base

    id_set = set(ids)
    portfolio_by_cid = {}
    for row in portfolio:
        cid = _cid(row)
        if cid in id_set and cid not in portfolio_by_cid:
            portfolio_by_cid[cid] = row

    rows_by_child = {}
    for row in boundaries:
        for _field, cid, _pack in _boundary_child_packs(row):
            if cid not in id_set:
                continue
            bucket = rows_by_child.setdefault(cid, [])
            if not any(existing is row for existing in bucket):
                bucket.append(row)

    frozen_question = snapshot.get('question') if isinstance(snapshot.get('question'), dict) else None
    engine_revision = snapshot.get('runtimeRevision')

    entries = {}
    identities = {}
    represented = []
    for cid in ids:
        row = portfolio_by_cid.get(cid)
        rows = rows_by_child.get(cid) or []
        if row is None and not rows:
            continue
        context = _context(row) if row is not None else _boundary_context(rows[0])
        compatibility = row.get('compatibility') if isinstance(row, dict) else None
        identity = _identity(cid, scenarios.get(cid), block, context,
                             engine_revision=engine_revision, compatibility=compatibility)
        identities[cid] = identity
        key = (identity['structuralKey'], identity['context'])
        entry = entries.get(key)
        if entry is None:
            entry = dict(identity=identity, points=[], boundaryRows=[], members=[])
            entries[key] = entry
        if cid not in represented:
            represented.append(cid)
        entry['members'].append(cid)
        if row is not None:
            entry['points'].append(_portfolio_point(cid, row, identity, scope))
        for boundary_row in rows:
            entry['boundaryRows'].append((cid, boundary_row))

    archetypes = []
    for key in sorted(entries):
        entry = entries[key]
        _attach_reference_deltas(entry, scope)
        regions = []
        if entry['points']:
            regions.append(_tested_region(key, entry, scope))
        seen = []
        for _child, boundary_row in entry['boundaryRows']:
            if any(boundary_row is existing for existing in seen):
                continue
            seen.append(boundary_row)
            identifier = (boundary_row.get('questionId') or boundary_row.get('childId')
                          or boundary_row.get('experimentId') or len(seen))
            regions.append(_boundary_region(key, identifier, boundary_row, set(entry['members']),
                                            identities, scope,
                                            _match_frozen_question(frozen_question, boundary_row),
                                            entry['identity']))
        archetypes.append(_archetype(entry, regions, scope))

    if not archetypes:
        base['reason'] = 'no candidate observations could be represented from this snapshot'
        return base
    coverage['includedCandidates'] = len(represented)
    base.update(status='available', archetypes=archetypes)
    return base


def build_summary_from_library(snapshot, library, *, readonly_db, candidate_limit=CANDIDATE_LIMIT):
    """Bounded read-only load of the snapshot's candidate scenarios, then pure synthesis."""
    ids, _available = collect_candidate_ids(snapshot, limit=candidate_limit)
    scenarios = {}
    load_error = None
    if ids:
        try:
            scenarios = load_candidate_scenarios(readonly_db, library, ids)
        except Exception as exc:  # noqa: BLE001 - a failed read must not fabricate candidate data
            load_error = str(exc)
    summary = build_summary(snapshot, scenarios, candidate_limit=candidate_limit)
    if load_error:
        summary['limitations'] = list(summary.get('limitations') or []) + [
            'Candidate scenario load failed: %s' % load_error]
    return summary


__all__ = ['READ_MODEL_VERSION', 'SYNTHESIS_REVISION', 'SOURCE_REVISION', 'CANDIDATE_LIMIT',
           'SCENARIO_BATCH', 'ADJUSTABLE_PARAMETER_IDS', 'REGION_KINDS', 'LIMITATIONS',
           'collect_candidate_ids', 'load_candidate_scenarios', 'structural_key', 'build_summary',
           'build_summary_from_library']
