"""Bounded, read-only support diagnosis from persisted development evidence.

The encounter-aware search persists every development battle in ``ea_sample`` (raw compact result)
linked to the immutable ``ea_experiment`` intent that requested it, and persists every assessed
operating-region point in ``ea_boundary``. ``strategy_encounter_search`` already builds
``observedStatVectors`` evidence for the improvement/repair generator but never names a
``limitingRole``, so ``strategy_joint_proposals._repair_specs`` can never fire (support-path-review
finding 1). This module is the missing reader: it turns a candidate's OWN persisted evidence into an
explicit, evidence-referenced explanation of *why* a non-fodder role needs upward support, or an
explicit "no supported trigger".

Guarantees:

  * read-only. Only ``SELECT`` statements; nothing writes a row, run, policy or candidate, and no
    battle or simulator is imported.
  * bounded. At most ``maximum_rows`` rows are examined in each source, newest-first.
  * candidate-specific. Sample rows must match ``ea_sample.candidate_id`` AND
    ``ea_sample_link.candidate_id``; a support-boundary record must name THIS candidate as the
    DEGRADED tested child (``payload.assessment.candidateId``), never as its distinct reference.
  * same observation. A row counts only when its experiment's frozen ``compatibility`` digest equals
    the caller's current digest, the sample's own mechanic/encounter revisions agree with the
    experiment that charged it, and a boundary assessment was stored under a matching digest.
  * development only. Frozen holdout pairs are excluded and a non-``development`` window is rejected.
  * honest about absence. Missing telemetry is unknown, never zero: a result with no ``mpMetrics``
    contributes no trigger and is reported under ``unavailable``.

Exactly two triggers are emitted, each able to stand on its own:

  * MP crossing -> MP repair. A watched DPS/healer reached <=3% of its own MP at a real tick that is
    strictly before an observed run end (``ticks``). This mirrors ``strategy_mp_recovery.crossing_run``
    (``firstLowMpTick < ticks``); that function reads the legacy ``run`` table and so cannot see
    encounter samples, so the rule is reused, not the function. A crossing with no observed run end
    cannot be established and contributes no trigger.
  * Controlled support-boundary degradation -> HP or DEF repair. A persisted ``ea_boundary`` support
    question lowered exactly one survival axis (HP or DEF) on a clone and the frozen, paired, complete
    ``strategy_operating_regions`` assessment classified the point ``supported-degraded``: the tested
    reduction measurably lost earnings, so that axis is load-bearing on the DEGRADED TESTED CHILD and
    an upward repair of the tested axis is supported. The candidate is the child
    (``assessment.candidateId``) and its reference is a DISTINCT build: a degraded child never raises
    its own sufficient reference, and a sibling's loss never raises any other build. The intervention
    must be verifiably single-axis from the persisted experiment intent (``observedScenarios``, then
    ``changedFields``); an axis merely held constant in ``fixedFields`` is not a change, and an
    unverifiable intervention stays unknown and never triggers. A death, a boss death or a reward
    certificate is NOT used to infer HP/DEF: none of them proves that this candidate's HP/DEF was the
    limiting thing, so a lifecycle death alone stays a reportable hypothesis under ``unavailable`` and
    never fires the HP/DEF trigger. An unresolved, foreign, multi-axis or fodder boundary does not
    trigger either.

Roles and source indices come from the shared helpers ``strategy_mp_recovery.watch_units`` (derived
placement, DPS then healer, fodder excluded) and ``strategy_students.role`` (the authoritative
skill-based classifier); a boundary axis resolves its role from the canonical
``ownUnits.<index>.parameters.<id>`` field through the SAME source index. No unit is re-classified and
no label is invented; a reward amount, mean, chest count or verdict is never read as a predictor.
"""
from __future__ import annotations

import json
import sqlite3

import strategy_experiment_store as ledger
import strategy_mp_recovery as mp_recovery
import strategy_payload_codec as payload_codec
import strategy_students as students

#: Development is the only measurement window a trigger may be read from.
DEVELOPMENT_WINDOW = 'development'
#: The two non-fodder roles support repair may name. A fodder is never watched and never named.
SUPPORT_ROLES = (students.ROLE_DPS, students.ROLE_HEALER)
#: The MP crossing supports MP repair.
MP_STATS = ('mp',)
#: Canonical parameter ids: HP 10, MP 11, DEF 14 (see strategy_encounter_search.STAT_PARAMETER_IDS).
HP_PARAMETER_ID = 10
MP_PARAMETER_ID = 11
DEF_PARAMETER_ID = 14
#: The two survival parameters a support-boundary point may name, by canonical id.
SURVIVAL_AXIS = {HP_PARAMETER_ID: 'hp', DEF_PARAMETER_ID: 'def'}
#: Every axis a frozen support question may move; a second one makes a point multi-axis.
SUPPORT_PARAMETER_IDS = (HP_PARAMETER_ID, MP_PARAMETER_ID, DEF_PARAMETER_ID)
#: The field prefix of a canonical build path (``strategy_build_domain``'s own contract).
_FIELD_PREFIX = 'ownUnits'


def _is_tick(value):
    """A real observed tick: a non-bool int >= 0. ``-1``/``None``/absent are not ticks."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_count(value):
    """A real count: a non-bool int >= 0."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _watched_roles(scenario):
    """``({unit name: role}, detail)`` for the DPS and healer resolved from the scenario.

    ``mp_recovery.watch_units`` owns the watch list (derived placement, DPS first then healer,
    fodder deliberately excluded); ``students.role`` owns the classification.
    """
    names, detail = mp_recovery.watch_units(scenario)
    by_name = {}
    for unit in (scenario or {}).get('ownUnits') or []:
        name, role = unit.get('name'), students.role(unit)
        if name in names and role in SUPPORT_ROLES:
            by_name[name] = role
    return by_name, detail


def _owner_compatible(experiment_owner, owner):
    """Owner/session compatibility: the requested owner, or the community-first default.

    ``owner=None`` is the community-first view, whose experiments are owned by ``community`` (or the
    session-wide ``*`` row), so those are admitted. Any other owner sees only its own rows.
    """
    if owner is None:
        return experiment_owner in (None, 'community', ledger.ANY_OWNER)
    return experiment_owner == owner


def _parse_support_field(field):
    """``(index, parameter_id)`` for a canonical build path, else ``None``.

    Mirrors ``strategy_build_domain._parse_field``'s contract (``ownUnits.<i>.parameters.<id>``) so a
    boundary axis and a role are read through the SAME source index. Intentionally local: importing
    the build-domain service would pull the optimiser into this read-only reader.
    """
    if not isinstance(field, str):
        return None
    parts = field.split('.')
    if len(parts) != 4 or parts[0] != _FIELD_PREFIX or parts[2] != 'parameters':
        return None
    try:
        index, parameter_id = int(parts[1]), int(parts[3])
    except (TypeError, ValueError):
        return None
    if index < 0 or parameter_id < 0:
        return None
    return index, parameter_id


def _parameter_value(unit, parameter_id):
    """The build's own recorded value for one canonical parameter, else ``None``.

    Handles a parameter block keyed by int (a live scenario) or by str (a stored library), and a
    ``{'rawValue': ...}`` entry or a bare integer. This reader never recomputes an effective value; it
    only compares what the persisted build actually records.
    """
    if not isinstance(unit, dict):
        return None
    parameters = unit.get('parameters')
    if not isinstance(parameters, dict):
        return None
    for key in (str(parameter_id), parameter_id):
        if key in parameters:
            entry = parameters[key]
            value = entry.get('rawValue') if isinstance(entry, dict) else entry
            return value if isinstance(value, int) and not isinstance(value, bool) else None
    return None


def _load_intent_value(text):
    """A persisted intent sub-document parsed from its stored JSON text, else ``None``."""
    if text is None:
        return None
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


def _support_axis_changes(reference, candidate):
    """The set of support parameter ids that ACTUALLY differ between two observed builds.

    ``None`` when the two builds cannot be compared parameter-by-parameter (a structural difference
    or a missing support block), which is ``unknown``, never a claim that nothing changed.
    """
    if not isinstance(reference, dict) or not isinstance(candidate, dict):
        return None
    left, right = reference.get('ownUnits'), candidate.get('ownUnits')
    if not isinstance(left, list) or not isinstance(right, list) or len(left) != len(right):
        return None
    changed = set()
    for unit_left, unit_right in zip(left, right):
        for parameter_id in SUPPORT_PARAMETER_IDS:
            before, after = (_parameter_value(unit_left, parameter_id),
                             _parameter_value(unit_right, parameter_id))
            if before is None or after is None:
                return None
            if before != after:
                changed.add(parameter_id)
    return changed


def _declared_support_changes(changed_fields):
    """The support parameter ids named by an intent's ``changedFields``, or ``None`` when unreadable.

    ``changedFields`` records the fields the experiment actually moved between its two arms; a field
    merely held constant is never listed. An empty usable list is a definite "no support axis moved",
    distinct from ``None`` (unknown).
    """
    if isinstance(changed_fields, dict):
        fields = list(changed_fields)
    elif isinstance(changed_fields, (list, tuple, set)):
        fields = list(changed_fields)
    else:
        return None
    changed = set()
    for field in fields:
        parsed = _parse_support_field(field)
        if parsed is not None and parsed[1] in SUPPORT_PARAMETER_IDS:
            changed.add(parsed[1])
    return changed


def _support_axis_intervention(changed_fields, observed_scenarios, candidate, reference_id,
                               parameter_id):
    """``(verdict, changed)`` for the actual support-axis intervention behind a boundary point.

    ``verdict`` is ``'single'`` when the persisted intent shows EXACTLY the tested support axis moved,
    ``'multi'`` when it shows a different support-axis change set, and ``'unknown'`` when neither the
    frozen OBSERVED scenarios nor the recorded ``changedFields`` can be read. The observed scenarios
    are preferred because they diff the DISTINCT reference build against the candidate build, so an
    axis merely held constant (present in ``fixedFields``) is not counted as a change. Nothing is
    inferred when both sources are absent.
    """
    if isinstance(observed_scenarios, dict) and isinstance(candidate, dict):
        changed = _support_axis_changes(observed_scenarios.get(reference_id), candidate)
        if changed is not None:
            return ('single' if changed == {parameter_id} else 'multi'), changed
    declared = _declared_support_changes(changed_fields)
    if declared is None:
        return 'unknown', None
    return ('single' if declared == {parameter_id} else 'multi'), declared


def _mp_crossing(result, watched):
    """One MP crossing strictly before the run ended, or ``None``.

    ``None`` means no crossing was observed, the result carries no usable MP block, or the run end was
    not observed. The caller distinguishes the cases by whether ``mpMetrics`` was a list and whether
    ``ticks`` is a tick. ``strategy_mp_recovery.crossing_run``'s rule is ``firstLowMpTick < ticks``, so
    without an observed run end the crossing is unknown, never asserted.
    """
    metrics = result.get('mpMetrics')
    if not isinstance(metrics, list):
        return None
    end = result.get('ticks')
    if not _is_tick(end):
        return None
    for entry in metrics:
        if not isinstance(entry, dict):
            continue
        name = entry.get('name')
        role = watched.get(name)
        if role is None:  # fodder, an unknown unit, or an unclassified extra entry
            continue
        if not entry.get('reachedLowMp'):
            continue
        tick = entry.get('firstLowMpTick')
        if not _is_tick(tick):
            continue
        if tick >= end:
            continue  # sampled low after the battle had effectively finished
        return dict(kind='mp-crossing', role=role, unit=name, tick=tick,
                    phase=entry.get('firstLowMpPhase'), runEnd=end,
                    minimumMp=entry.get('minimumMp'),
                    minimumMpPercent=entry.get('minimumMpPercent'))
    return None


def _death_hypotheses(result, watched):
    """Watched non-fodder deaths seen in a result, as an unproven hypothesis only.

    A death is reported for context and never as an HP/DEF trigger: the persisted telemetry does not
    prove that the dead unit's own HP/DEF was the limiting thing, that any useful work was left, or
    that a boss death marks the end of the objective. No boss/certificate/remaining-work conclusion is
    drawn here.
    """
    telemetry = result.get('encounterTelemetry')
    if not isinstance(telemetry, dict):
        return []
    deaths, roles = telemetry.get('ownDeaths'), telemetry.get('ownRoles')
    if not isinstance(deaths, dict) or not isinstance(roles, dict):
        return []
    out = []
    for identity, tick in deaths.items():
        role = roles.get(identity)
        if role not in SUPPORT_ROLES:  # never fodder, never an unclassified unit
            continue
        if not _is_tick(tick):
            continue
        out.append(dict(role=role, identity=identity, tick=tick, runEnd=result.get('ticks')))
    return out


def _sample_rows(db, candidate_id, maximum_rows):
    """Bounded, candidate-specific development links, newest first, with their experiment intent.

    The frozen holdout pairs are flagged (not filtered) so the caller can count them as rejected; no
    holdout result is ever selected. Compatibility and the measurement window are extracted from the
    stored immutable intent without parsing the (large) raw scenarios it also holds.
    """
    rows = []
    sql = (
        'SELECT s.result, s.sample_key, s.seed_a, s.seed_b, l.experiment_id, '
        'e.owner, e.owner_share, e.measurement_window, e.policy, e.mechanics_revision, '
        'e.encounter_revision, s.policy, s.mechanics_revision, s.encounter_revision, s.created_at, '
        "json_extract(e.intent_json, '$.compatibility'), "
        "json_extract(e.intent_json, '$.measurementWindow'), "
        '(SELECT COUNT(*) FROM ea_holdout h WHERE h.experiment_id = l.experiment_id '
        ' AND h.candidate_id = l.candidate_id AND h.seed_a = l.seed_a AND h.seed_b = l.seed_b) '
        'FROM ea_sample s JOIN ea_sample_link l ON l.sample_key = s.sample_key '
        'JOIN ea_experiment e ON e.id = l.experiment_id '
        'WHERE s.candidate_id = ? AND l.candidate_id = ? AND s.result IS NOT NULL '
        'ORDER BY s.created_at DESC, s.sample_key LIMIT ?')
    for row in db.execute(sql, (candidate_id, candidate_id, int(maximum_rows))).fetchall():
        rows.append(dict(result=payload_codec.decode_codec_if_magic(row[0]), sampleKey=row[1],
                         seedA=int(row[2]), seedB=int(row[3]),
                         experimentId=int(row[4]), owner=row[5], ownerShare=row[6],
                         windowColumn=row[7], policy=row[8], mechanicsRevision=row[9],
                         encounterRevision=row[10], samplePolicy=row[11],
                         sampleMechanics=row[12], sampleEncounter=row[13], createdAt=row[14],
                         compatibility=row[15], windowIntent=row[16], holdoutPairs=int(row[17])))
    return rows


def _support_boundary_rows(db, candidate_id, maximum_rows):
    """Bounded support-boundary records whose DEGRADED TESTED CHILD is this candidate, newest first.

    ``ea_boundary.payload.assessment.candidateId`` is the canonical candidate id of the build whose
    axis the reduced support point tested, so a record is candidate-specific by construction: the
    reference (``study.referenceId``) is a DISTINCT build and a record about any other child is never
    selected. The experiment's thin ``changedFields`` projection is read alongside the payload. Its
    full frozen intent is decoded only after the bounded row passes the cheap evidence gates, and
    SQLite JSON1 extracts ``observedScenarios`` from that exact text to retain first-duplicate rules.
    """
    rows = []
    sql = (
        "SELECT b.id, b.experiment_id, b.kind, b.frozen_reference, b.payload, b.created_at, "
        "e.owner, e.measurement_window, json_extract(e.intent_json, '$.compatibility'), "
        "json_extract(e.intent_json, '$.measurementWindow'), "
        "json_extract(e.intent_json, '$.changedFields'), "
        "(SELECT COUNT(*) FROM ea_holdout h WHERE h.experiment_id = b.experiment_id "
        " AND h.candidate_id = ?) "
        "FROM ea_boundary b JOIN ea_experiment e ON e.id = b.experiment_id "
        "WHERE b.kind = 'support' "
        "AND json_extract(b.payload, '$.assessment.candidateId') = ? "
        "AND json_extract(b.payload, '$.study.referenceId') <> ? "
        'ORDER BY b.created_at DESC, b.id DESC LIMIT ?')
    for row in db.execute(sql, (candidate_id, candidate_id, candidate_id,
                                int(maximum_rows))).fetchall():
        rows.append(dict(boundaryId=int(row[0]), experimentId=int(row[1]), kind=row[2],
                         frozenReference=row[3], payload=row[4], createdAt=row[5], owner=row[6],
                         windowColumn=row[7], compatibility=row[8], windowIntent=row[9],
                         changedFields=row[10], holdoutPairs=int(row[11])))
    return rows


def _controlled_support_boundary(db, row, scenario, owner, compatibility, candidate_id):
    """``(reading, None)`` for a controlled, resolved, single-axis supported-degraded support point
    whose DEGRADED TESTED CHILD is this candidate, else ``(None, rejection_key)``.

    Reads the REAL ``ea_boundary`` payload and the frozen ``strategy_operating_regions`` question
    contract: ``study`` (the frozen question, whose ``referenceId`` is a DISTINCT build), ``assessment``
    (the paired comparison, whose ``candidateId`` is the tested child), ``testedValue`` (the
    per-experiment child point) and ``published``. The candidate's own observed build must actually
    carry the tested value, and the persisted experiment intent must verify that ONLY the tested
    support axis changed - a held-constant ``fixedFields`` axis is not a change. Every gate fails
    closed to an explicit rejection; an unknown classification or an unverifiable intervention never
    triggers.
    """
    if row['holdoutPairs'] > 0:
        return None, 'holdout'
    window = row['windowIntent'] or row['windowColumn']
    if window != DEVELOPMENT_WINDOW:
        return None, 'notDevelopment'
    if not _owner_compatible(row['owner'], owner):
        return None, 'foreignOwner'
    if row['compatibility'] != compatibility:
        return None, 'incompatible'
    try:
        payload = json.loads(row['payload'])
    except (TypeError, ValueError):
        return None, 'malformed'
    if not isinstance(payload, dict):
        return None, 'malformed'
    study, assessment, tested = payload.get('study'), payload.get('assessment'), payload.get('testedValue')
    if not isinstance(study, dict) or not isinstance(assessment, dict) or not isinstance(tested, dict):
        return None, 'malformed'
    if study.get('kind') != 'support' or assessment.get('kind') != 'support':
        return None, 'malformed'
    reference, child = study.get('referenceId'), assessment.get('candidateId')
    # The diagnosed candidate is the DEGRADED TESTED CHILD; its reference is a distinct build.
    if not isinstance(child, str) or child != candidate_id:
        return None, 'malformed'
    if not isinstance(reference, str) or reference == candidate_id:
        return None, 'malformed'
    if payload.get('frozenReference') not in (None, reference):
        return None, 'malformed'
    if any(study.get(key) is None for key in
           ('encounterRevision', 'mechanicsRevision', 'policy', 'fixedFields')):
        return None, 'malformed'
    field = study.get('field')
    parsed = _parse_support_field(field)
    fixed = study.get('fixedFields')
    if parsed is None or not isinstance(fixed, dict) or field not in fixed:
        return None, 'malformed'
    if (tested.get('field') != field or tested.get('kind') != 'support'
            or tested.get('value') != fixed.get(field)):
        return None, 'malformed'
    # The classification decides: only a resolved harmful loss is a trigger. An unresolved or unknown
    # classification is NOT evidence of a limit.
    if assessment.get('classification') != 'supported-degraded':
        return None, 'notDegraded'
    if payload.get('published') is not True:
        return None, 'unresolved'
    if assessment.get('tested') is not True or assessment.get('interpolation') is True:
        return None, 'notDegraded'
    paired = assessment.get('pairedCount')
    if not _is_count(paired) or paired < 2:
        return None, 'unresolved'
    if (assessment.get('totalReference') != paired or assessment.get('totalCandidate') != paired
            or assessment.get('unresolvedReference') != 0
            or assessment.get('unresolvedCandidate') != 0):
        return None, 'unresolved'
    index, parameter_id = parsed
    if parameter_id not in SURVIVAL_AXIS:
        return None, 'notSurvivalAxis'
    units = (scenario or {}).get('ownUnits') or []
    if index >= len(units):
        return None, 'malformed'
    # The candidate's OWN observed build must actually carry the tested value on the tested axis.
    if _parameter_value(units[index], parameter_id) != tested.get('value'):
        return None, 'malformed'
    role = students.role(units[index])
    if role not in SUPPORT_ROLES:  # never fodder, never an unclassified unit
        return None, 'fodder'
    # Verify from the persisted experiment intent that the ACTUAL intervention moved exactly the tested
    # support axis; a compensation or an unverifiable intervention makes the causal axis ambiguous.
    # This is deliberately after every cheap gate above. JSON1 runs over the exact decompressed raw
    # intent, preserving SQLite's first-duplicate behavior independently from Python full-reader use.
    try:
        observed_raw = ledger.experiment_intent_json_extract(
            db, row['experimentId'], '$.observedScenarios')
    except (sqlite3.Error, TypeError, ValueError):
        return None, 'unresolved'
    verdict, _changed = _support_axis_intervention(
        _load_intent_value(row.get('changedFields')),
        _load_intent_value(observed_raw), scenario, reference, parameter_id)
    if verdict == 'unknown':
        return None, 'unresolved'
    if verdict != 'single':
        return None, 'multiAxis'
    return dict(kind='support-boundary-degraded', role=role, axis=SURVIVAL_AXIS[parameter_id],
                field=field, value=fixed.get(field), unitIndex=index, referenceId=reference,
                candidateId=child, classification='supported-degraded',
                pairedCount=paired, tolerance=assessment.get('tolerance'),
                diagnosticInterval=assessment.get('diagnosticInterval'),
                referenceMeanEarned=assessment.get('referenceMeanEarned'),
                meanEarned=assessment.get('meanEarned'),
                questionId=study.get('id') or assessment.get('questionId'),
                experimentId=row['experimentId'], boundaryId=row['boundaryId']), None


def _evidence_ref(row, reading):
    """The MP-crossing evidence reference for one admitted sample (shape unchanged)."""
    return dict(experimentId=row['experimentId'], sampleKey=row['sampleKey'],
                seedPair=[row['seedA'], row['seedB']],
                measurementWindow=DEVELOPMENT_WINDOW, createdAt=row['createdAt'],
                reading=dict(reading))


def _boundary_evidence_ref(row, reading):
    """The controlled support-comparison evidence reference for one degraded boundary point.

    This is the causal comparison the API reports, kept distinct from an MP crossing: it names the
    frozen reference, the tested canonical field and value, the resolved classification and the
    complete matched-pair count. It never carries a reward, mean or certificate as a predictor.
    """
    return dict(kind=reading['kind'], experimentId=row['experimentId'],
                boundaryId=row['boundaryId'], questionId=reading.get('questionId'),
                referenceId=reading.get('referenceId'), testedCandidateId=reading.get('candidateId'),
                testedField=reading['field'], testedValue=reading['value'],
                testedUnitIndex=reading['unitIndex'], classification='supported-degraded',
                pairedCount=reading['pairedCount'], tolerance=reading.get('tolerance'),
                diagnosticInterval=reading.get('diagnosticInterval'),
                measurementWindow=DEVELOPMENT_WINDOW, createdAt=row['createdAt'],
                reading=dict(reading))


def diagnose(db, *, candidate_id, scenario, owner, compatibility, maximum_rows=256):
    """Bounded, read-only support diagnosis for one candidate's own persisted evidence.

    Returns a dict with ``limitingRole`` / ``limitingStats`` / ``parentTelemetry`` / ``counts`` /
    ``reasons`` / ``evidence`` / ``hypotheses`` / ``unavailable``, and a ``supported`` truth value.
    ``supported`` is ``False`` with ``limitingRole`` ``None`` when no evidence-backed trigger exists; it
    is never a guess. ``compatibility`` must be the caller's current frozen digest (``owner=None`` is
    the community-first view). No reward amount, mean, chest count, verdict, boss death or certificate
    is read as a predictor.
    """
    counts = dict(examined=0, admitted=0, mpCrossings=0, deathsObserved=0,
                  boundaryExamined=0, boundaryAdmitted=0, supportBoundaryDegraded=0,
                  rejections=dict(holdout=0, notDevelopment=0, foreignOwner=0, incompatible=0,
                                  missingTelemetry=0),
                  boundaryRejections=dict(holdout=0, notDevelopment=0, foreignOwner=0,
                                          incompatible=0, malformed=0, notDegraded=0, unresolved=0,
                                          notSurvivalAxis=0, multiAxis=0, fodder=0))
    reasons = []
    evidence = []
    hypotheses = []
    unavailable = []
    result = dict(supported=False, limitingRole=None, limitingStats=[], parentTelemetry=None,
                  counts=counts, reasons=reasons, evidence=evidence, hypotheses=hypotheses,
                  unavailable=unavailable)
    candidate_id = str(candidate_id)

    if compatibility is None:
        reasons.append('no compatibility digest was supplied: the same-observation check cannot be '
                       'made, so no persisted row may contribute')
        return result
    if db is None:
        reasons.append('no encounter ledger is reachable')
        return result
    try:
        rows = _sample_rows(db, candidate_id, maximum_rows)
    except sqlite3.OperationalError as exc:
        reasons.append('the encounter ledger is not readable: %s' % (exc,))
        return result

    try:
        watched, detail = _watched_roles(scenario)
    except Exception as exc:  # noqa: BLE001 - an unresolvable roster must not crash the coordinator
        reasons.append('the candidate roster could not be resolved for role/source indices: %s'
                       % (exc,))
        return result
    if not watched:
        reasons.append('no non-fodder DPS or healer could be watched for this candidate: %s'
                       % (detail or 'roles unresolved'))
        return result

    best = None  # (0 = MP crossing preferred, 1 = controlled support boundary) -> the reading
    for row in rows:
        counts['examined'] += 1
        if row['holdoutPairs'] > 0:
            counts['rejections']['holdout'] += 1
            continue
        window = row['windowIntent'] or row['windowColumn']
        if window != DEVELOPMENT_WINDOW:
            counts['rejections']['notDevelopment'] += 1
            continue
        if not _owner_compatible(row['owner'], owner):
            counts['rejections']['foreignOwner'] += 1
            continue
        if row['compatibility'] != compatibility:
            counts['rejections']['incompatible'] += 1
            continue
        if (row['samplePolicy'] != row['policy']
                or row['sampleMechanics'] != row['mechanicsRevision']
                or row['sampleEncounter'] != row['encounterRevision']):
            counts['rejections']['incompatible'] += 1
            continue
        counts['admitted'] += 1
        try:
            payload = json.loads(row['result'])
        except (TypeError, ValueError):
            counts['rejections']['missingTelemetry'] += 1
            continue
        if not isinstance(payload, dict):
            counts['rejections']['missingTelemetry'] += 1
            continue
        crossing = _mp_crossing(payload, watched)
        if crossing is not None:
            counts['mpCrossings'] += 1
            evidence.append(_evidence_ref(row, crossing))
            if best is None:
                best = (0, crossing, row)
            continue
        if not isinstance(payload.get('mpMetrics'), list):
            unavailable.append('sample %s carries no mpMetrics block; MP was not observed, not zero'
                               % row['sampleKey'][:12])
        elif not _is_tick(payload.get('ticks')):
            unavailable.append('sample %s carries an MP block but no observed run end (ticks); an MP '
                               'crossing before the end cannot be established' % row['sampleKey'][:12])
        for hypothesis in _death_hypotheses(payload, watched):
            counts['deathsObserved'] += 1
            hypotheses.append(hypothesis)

    try:
        boundary_rows = _support_boundary_rows(db, candidate_id, maximum_rows)
    except sqlite3.OperationalError as exc:
        # A missing operating-region ledger makes the HP/DEF source unknown; it must not discard a
        # genuine MP crossing already found in the development telemetry.
        boundary_rows = []
        unavailable.append('the operating-region ledger is not readable; no support-boundary evidence '
                           'could be read: %s' % (exc,))
    for row in boundary_rows:
        counts['boundaryExamined'] += 1
        reading, rejection = _controlled_support_boundary(db, row, scenario, owner, compatibility,
                                                          candidate_id)
        if reading is None:
            counts['boundaryRejections'][rejection] += 1
            continue
        counts['boundaryAdmitted'] += 1
        counts['supportBoundaryDegraded'] += 1
        evidence.append(_boundary_evidence_ref(row, reading))
        if best is None:
            best = (1, reading, row)

    if best is None:
        if counts['admitted'] == 0 and counts['boundaryAdmitted'] == 0:
            reasons.append('no candidate-specific development row or support-boundary record matched '
                           'the current compatibility, owner and window')
        else:
            reasons.append('no watched DPS/healer crossed MP before the run ended and no controlled '
                           'supported-degraded single-axis HP/DEF support-boundary point is persisted '
                           'for this candidate')
        if hypotheses:
            unavailable.append('%d watched non-fodder death(s) were observed, but a death alone does '
                               'not demonstrate that HP/DEF was limiting and is never used as an '
                               'HP/DEF trigger; no controlled support-boundary evidence exists for '
                               'this candidate' % len(hypotheses))
        return result

    _rank, reading, row = best
    role = reading['role']
    if reading['kind'] == 'mp-crossing':
        stats = MP_STATS
        explanation = ('%s reached %s%% MP at tick %s before the run ended at %s (evidence: %s); this '
                       'is an observed resource limitation, not a reward reading'
                       % (reading['unit'], reading.get('minimumMpPercent'), reading['tick'],
                          reading.get('runEnd'), row['sampleKey'][:12]))
        evidence_ref = dict(experimentId=row['experimentId'], sampleKey=row['sampleKey'],
                            seedPair=[row['seedA'], row['seedB']], createdAt=row['createdAt'])
    else:
        stats = (reading['axis'],)
        explanation = ('a controlled support-boundary comparison on this candidate lowered %s to %s '
                       'under the frozen policy/mechanics/encounter revisions and measurably lost '
                       'earnings (supported-degraded, %s complete matched pairs, boundary record %s); '
                       'the tested %s axis is load-bearing for the %s, so upward repair of that axis is '
                       'supported - this is a causal controlled comparison, never a reward reading'
                       % (reading['field'], reading['value'], reading['pairedCount'],
                          reading['boundaryId'], reading['axis'].upper(), role))
        evidence_ref = _boundary_evidence_ref(row, reading)
    result['supported'] = True
    result['limitingRole'] = role
    result['limitingStats'] = list(stats)
    result['parentTelemetry'] = dict(kind=reading['kind'], role=role, stats=list(stats),
                                     observed=dict(reading), reason=explanation,
                                     evidenceRef=evidence_ref)
    reasons.append(explanation)
    return result
