"""Bounded, read-only historical-evidence reader for candidate-level adviser priors.

The mature optimiser library already holds millions of measured validation runs, but the
encounter-aware adviser only ever saw the parent scenarios it proposed in the current session.
This module exposes that history as *priors* for the adviser, without pretending it is a fresh
measurement and without ever writing to the library.

What it does, and what it refuses to do
---------------------------------------

* It reads the compact per-seed ``evidence`` table (falling back to the retained ``run`` replay
  window for a candidate with no compact rows), one indexed candidate at a time, taking a
  deterministic prefix of the ``validation`` bank ordered by ``ordinal`` - never a reward-ranked
  or lucky-seed subset, and every outcome class (win, loss, zero, unknown, censored) is kept.
* Each stored row is decoded with the canonical ``strategy_evidence.decode`` and turned into an
  outcome with the canonical ``strategy_outcomes.outcome``/``summarize``. A row whose verdict is
  unknown stays unknown: the reader never promotes a pending count to a fabricated zero and never
  selects winners.
* It proves, for the whole request, that the library was written by the *same simulator* the caller
  is running: the stored ``provenance`` digest must equal the caller's ``engine_revision`` (or an
  explicitly applied, reviewed observer-migration certificate must cover the transition). It also
  proves, per candidate, the immutable stored scenario's identity, encounter and declared
  measurement policy match the request. Anything it cannot prove is excluded with a specific reason
  and count - never silently treated as zero.
* Source scope/provenance (the historical library's own phase, window, stored policy and engine
  digest) is kept separate from the *current learner compatibility* key the observation is stamped
  with. Observations are labelled ``evidenceClass='historical-prior'`` and
  ``independentConfirmation=False``: old development evidence must never be read as independent
  confirmation.

The module is pure and read-only apart from the files it owns. It opens its own SQLite connections
with ``mode=ro`` and ``PRAGMA query_only`` and only ever issues ``SELECT``. It never opens a live
library on its own initiative: the caller hands it a path or an already-open store.

    import strategy_legacy_observations as legacy
    result = legacy.load_observations(
        baseline_path, candidate_ids,
        encounter={'encounterId': 19, 'defeatCount': 0},
        policy={'finishPolicy': 'on-verdict'}, mechanics_revision='strategy-mechanics-3',
        engine_revision='<simulator provenance digest>')

The returned mapping is ``{observations, excluded, provenance, counts, compatibility, limits}``.
Each observation is directly consumable by ``strategy_encounter_adviser`` (candidateId, window,
compatibility, features, meanEarned, resolved, total, standardError) and additionally carries the
full ``summary``, quantiles, unknown counts and provenance.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from copy import deepcopy
from pathlib import Path
from urllib.request import pathname2url

import strategy_build_domain as domain
import strategy_evidence as evidence_service
import strategy_outcomes as outcomes

VERSION = 'strategy-legacy-observations-1'

#: The stored measurement bank this reader takes its bounded prefix from.
VALIDATION_PHASE = 'validation'
#: The historical measurement window, kept distinct from the adviser-facing window class.
MEASUREMENT_WINDOW = 'legacy:validation'
#: The adviser treats this as prior/development evidence, never as confirmation.
ADVISER_WINDOW = 'development'
#: Library meta key holding the simulator provenance the stored runs were produced by.
PROVENANCE_KEY = 'provenance'

DEFAULT_MAXIMUM_CANDIDATES = 24
DEFAULT_ROWS_PER_CANDIDATE = 256
MAXIMUM_CANDIDATES_CAP = 256
ROWS_PER_CANDIDATE_CAP = 4096
#: Upper bound on the COUNT(*) used to report an old candidate's full bank size.
BANK_SIZE_CAP = 1000000

#: The exact scenario fields the encounter search freezes as the measurement/resource policy.
POLICY_KEYS = ('finishPolicy', 'tickLimit', 'holyHerbStock', 'startProfile', 'inputs',
               'holyHerbMaxUses', 'holyHerbTriggerUnits', 'mpWatchUnits')
#: Observation-only declarations that never enter identity and so are not part of the stored
#: measurement policy. They are ignored when matching a caller's declared policy against storage.
OBSERVATION_ONLY_POLICY_KEYS = ('mpWatchUnits',)

EVIDENCE_COLUMNS = ('seeds', 'verdict', 'censored', 'prizeCallbacks',
                    'awardedChests', 'awardedBasis', 'pendingChests')

_SCHEDULER_FILES = frozenset({'tools/recovery/strategy_optimizer.py',
                             'tools/recovery/strategy_optimizer_limits.py'})

#: Module-level honesty notes, returned with every result.
LIMITATIONS = (
    'Historical rows are stamped with the CURRENT learner compatibility key; the library stores '
    'only the simulator provenance digest, so the actual measurement policy is read from each '
    "candidate's immutable stored scenario rather than from the compact evidence rows.",
    'The prefix is the first N ordinals of the validation bank, not a random or reward-ranked '
    'sample; the historical bank may be larger, may have been pruned, and a prefix is not a '
    'replacement for a fresh matched measurement.',
    'Observations are prior/development evidence only (independentConfirmation=False); the old '
    'validation bank is not an independent confirmation set.',
    'The mean is the complete-claim mean over available readings. When any considered row is '
    'unknown, errored or censored the mean stays None and only partialMean is reported.',
    'mechanics_revision is caller-declared scope. The verifiable historical evidence is the engine '
    'provenance digest; a mechanics-revision string is not independently recoverable from storage.',
)


class ObservationError(ValueError):
    """The request or store handle is unusable (never a data verdict about the library)."""


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode('utf-8')).hexdigest()


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), default=str)


class ReadOnlyStore:
    """A strictly read-only view of a legacy optimiser library.

    Opened with ``mode=ro`` (plus ``immutable=1`` for a frozen snapshot) so SQLite itself refuses
    writes, and with ``PRAGMA query_only`` set as a second guard. ``get`` mirrors the optimiser
    ``Store.get`` so callers that already hold a store can be used interchangeably.
    """

    def __init__(self, path, *, immutable=True):
        self.path = Path(path)
        uri = 'file:' + pathname2url(str(self.path.resolve())) + '?mode=ro'
        if immutable:
            uri += '&immutable=1'
        self.db = sqlite3.connect(uri, uri=True, timeout=5.0)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA query_only=1')
        #: A path-only open cannot compute the optimiser's compatibility flag; the digest is
        #: checked directly instead.
        self.compatible = None

    def get(self, key, default=None):
        try:
            row = self.db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        except sqlite3.OperationalError:
            return default
        if row is None:
            return default
        try:
            return json.loads(row[0])
        except ValueError:
            return default

    def close(self):
        self.db.close()


class _ConnectionStore:
    """Wrap a caller-owned sqlite3 connection so it can be used like a store (no writes made)."""

    def __init__(self, connection):
        self.db = connection
        self.path = None
        self.compatible = None

    def get(self, key, default=None):
        try:
            row = self.db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        except sqlite3.OperationalError:
            return default
        if row is None:
            return default
        value = row['value'] if isinstance(row, sqlite3.Row) else row[0]
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return default


def open_readonly(path, *, immutable=True):
    """Open a legacy library read-only. The caller owns the returned store and should close it."""
    return ReadOnlyStore(path, immutable=immutable)


def _resolve_store(store, immutable):
    """Return ``(adapter, owned)`` where ``owned`` is the store to close, or ``None``."""
    if isinstance(store, (str, Path)):
        adapter = ReadOnlyStore(store, immutable=immutable)
        return adapter, adapter
    if isinstance(store, sqlite3.Connection):
        return _ConnectionStore(store), None
    if hasattr(store, 'db'):
        return store, None
    raise ObservationError('store must be a path, a sqlite3 connection, or an object exposing .db')


def _clamp_int(value, low, high, default):
    if isinstance(value, bool) or not isinstance(value, int):
        try:
            value = int(value)
        except (TypeError, ValueError):
            return default
    return max(low, min(high, value))


def _unique_ids(candidate_ids):
    unique, seen, duplicates, invalid = [], set(), 0, []
    for cid in candidate_ids or ():
        if not isinstance(cid, str) or not cid:
            invalid.append(cid)
            continue
        if cid in seen:
            duplicates += 1
            continue
        seen.add(cid)
        unique.append(cid)
    return unique, duplicates, invalid


def _normalize_encounter(encounter):
    if encounter is None:
        return None, 'encounter is required'
    if isinstance(encounter, bool):
        return None, 'encounter must not be a bool'
    if isinstance(encounter, int):
        return dict(encounterId=encounter, defeatCount=0, revision=None), None
    if isinstance(encounter, dict):
        eid = encounter.get('encounterId', encounter.get('id', encounter.get('encounter')))
        defeat = encounter.get('defeatCount', encounter.get('defeat', 0))
        revision = encounter.get('encounterRevision', encounter.get('revision'))
        if isinstance(eid, bool) or not isinstance(eid, int):
            return None, 'encounterId must be an integer'
        if isinstance(defeat, bool) or not isinstance(defeat, int):
            return None, 'defeatCount must be an integer'
        return dict(encounterId=eid, defeatCount=defeat, revision=revision), None
    return None, 'encounter must be an int or a mapping'


def _normalize_policy(policy):
    if isinstance(policy, str) and policy:
        declared = {'finishPolicy': policy}
    elif isinstance(policy, dict) and policy:
        declared = deepcopy(policy)
    else:
        return None, 'a measurement policy with an explicit finishPolicy is required'
    if not isinstance(declared.get('finishPolicy'), str) or not declared['finishPolicy']:
        return None, 'policy.finishPolicy is required'
    return declared, None


def _policy_matches(declared, scenario):
    """Every declared measurement field must equal the candidate's immutable stored value."""
    for key, value in declared.items():
        if key in OBSERVATION_ONLY_POLICY_KEYS:
            continue
        stored = scenario.get(key)
        if _canonical(stored) != _canonical(value):
            return False, 'declared %s=%r does not match stored %r' % (key, value, stored)
    return True, ''


def _same_simulator(left, right):
    """Canonical simulator-equivalence check, with a conservative local fallback."""
    try:
        import strategy_optimizer
        return bool(strategy_optimizer.same_simulator(left, right))
    except Exception:  # noqa: BLE001 - fall back to exact file-map equality, never to a guess
        def files(payload):
            return {k: v for k, v in (payload.get('files') or {}).items()
                    if k not in _SCHEDULER_FILES}
        a, b = files(left), files(right)
        return bool(a) and a == b


def _migration_basis(store, stored, current):
    """An explicitly applied, reviewed observer certificate covering the stored transition."""
    try:
        import strategy_encounter_migration as migration
    except Exception:  # noqa: BLE001
        return None, 'observer-migration module unavailable'
    try:
        plan = migration.preview(stored, current)
        record = store.get(migration.KEY)
        if plan.get('eligible') and isinstance(record, dict) and record.get('proof') == plan.get('proof'):
            return 'migration-certificate', None
        return None, plan.get('reason') or 'no applied observer-migration certificate matches'
    except Exception as exc:  # noqa: BLE001 - a failed verification is not a grant
        return None, 'observer-migration verification failed: %s' % (exc,)


def _engine_basis(store, engine_revision):
    """Prove the library's stored simulator provenance matches the caller's engine revision."""
    stored = store.get(PROVENANCE_KEY)
    stored_digest = stored.get('digest') if isinstance(stored, dict) else None
    info = dict(storedDigest=stored_digest, basis=None, compatible=False, reason=None, detail='')
    if not isinstance(stored, dict) or not stored_digest:
        info['reason'] = 'missing-stored-provenance'
        info['detail'] = 'the library has no stored simulator provenance digest'
        return info
    if getattr(store, 'compatible', None) is False:
        info['reason'] = 'store-incompatible'
        info['detail'] = 'the opened store reports itself incompatible with the current simulator'
        return info
    if isinstance(engine_revision, str) and engine_revision:
        if engine_revision == stored_digest:
            info.update(basis='exact', compatible=True)
        else:
            info['reason'] = 'engine-revision-mismatch'
            info['detail'] = ('stored %s != requested %s'
                              % (stored_digest[:12], engine_revision[:12]))
        return info
    if isinstance(engine_revision, dict):
        if engine_revision.get('digest') == stored_digest:
            info.update(basis='exact', compatible=True)
            return info
        if _same_simulator(stored, engine_revision):
            info.update(basis='exact-simulator-equivalence', compatible=True)
            return info
        basis, reason = _migration_basis(store, stored, engine_revision)
        if basis:
            info.update(basis=basis, compatible=True)
        else:
            info['reason'] = 'engine-revision-mismatch'
            info['detail'] = reason or 'the stored transition is not a reviewed observer migration'
        return info
    info['reason'] = 'engine-revision-missing'
    info['detail'] = 'engine_revision must be a provenance digest or a full provenance mapping'
    return info


def _library_view(store):
    """The historical library's own source scope/provenance (never the learner compatibility)."""
    provenance = store.get(PROVENANCE_KEY)
    return dict(
        path=str(store.path) if getattr(store, 'path', None) else None,
        storedProvenanceDigest=(provenance or {}).get('digest') if isinstance(provenance, dict) else None,
        storedProvenanceCount=(provenance or {}).get('count') if isinstance(provenance, dict) else None,
        storedScope=store.get('scope'),
        phase=VALIDATION_PHASE,
        measurementWindow=MEASUREMENT_WINDOW,
        schema=store.get('schema'),
        objectiveVersion=store.get('objectiveVersion'),
        searchSpaceVersion=store.get('searchSpaceVersion'),
        totalRuns=store.get('totalRuns'),
        evidenceBackfill=store.get('evidenceBackfill'),
    )


def _seed_key(seeds):
    """A dedupe key from an ordered seed pair, or None when the pair is malformed."""
    if isinstance(seeds, str):
        try:
            seeds = json.loads(seeds)
        except ValueError:
            return None
    if not isinstance(seeds, (list, tuple)) or len(seeds) != 2:
        return None
    a, b = seeds
    if isinstance(a, bool) or isinstance(b, bool):
        return None
    if not isinstance(a, int) or not isinstance(b, int):
        return None
    return (a, b)


def _bank_size(db, table, cid):
    try:
        return db.execute('SELECT COUNT(*) FROM (SELECT 1 FROM %s WHERE candidate=? AND phase=? '
                          'LIMIT ?)' % table, (cid, VALIDATION_PHASE, BANK_SIZE_CAP)).fetchone()[0]
    except sqlite3.OperationalError:
        return None


def _evidence_prefix(db, cid, limit):
    sql = ('SELECT ordinal,' + ','.join(EVIDENCE_COLUMNS) + ' FROM evidence '
           'WHERE candidate=? AND phase=? ORDER BY ordinal LIMIT ?')
    try:
        return list(db.execute(sql, (cid, VALIDATION_PHASE, limit)))
    except sqlite3.OperationalError:
        return None


def _run_prefix(db, cid, limit):
    try:
        return list(db.execute('SELECT ordinal,result FROM run WHERE candidate=? AND phase=? '
                               'ORDER BY ordinal LIMIT ?', (cid, VALIDATION_PHASE, limit)))
    except sqlite3.OperationalError:
        return None


def _prefix_outcomes(db, cid, limit, stored_policy, encounter_revision, mechanics_revision):
    """A bounded, deterministic prefix of the validation bank, decoded through the canon.

    Prefers the compact ``evidence`` table; falls back to the retained ``run`` replay window for a
    candidate that has no compact rows. Both paths dedupe by ordered seed pair and never drop a
    non-winning row.
    """
    data = dict(source=None, considered=0, outcomes=[], deduplicated=0, malformed=0,
                ordinalRange=None, bankSize=None)
    raw = _evidence_prefix(db, cid, limit)
    if raw:
        data['source'] = 'evidence'
        data['bankSize'] = _bank_size(db, 'evidence', cid)
        ordinals, seen = [], set()
        for record in raw:
            ordinals.append(record[0])
            key = _seed_key(record[1])
            if key is None:
                data['malformed'] += 1
                continue
            if key in seen:
                data['deduplicated'] += 1
                continue
            seen.add(key)
            decoded = evidence_service.decode(*record[1:])
            data['outcomes'].append(outcomes.outcome(
                decoded, candidate_id=cid, encounter_revision=encounter_revision,
                mechanics_revision=mechanics_revision, measurement_window=MEASUREMENT_WINDOW,
                policy=stored_policy))
        data['considered'] = len(ordinals)
        data['ordinalRange'] = [min(ordinals), max(ordinals)] if ordinals else None
        return data
    raw = _run_prefix(db, cid, limit)
    if raw:
        data['source'] = 'run-replay'
        data['bankSize'] = _bank_size(db, 'run', cid)
        ordinals, seen = [], set()
        for ordinal, payload in raw:
            ordinals.append(ordinal)
            try:
                result = json.loads(payload)
            except (TypeError, ValueError):
                data['malformed'] += 1
                continue
            if not isinstance(result, dict):
                data['malformed'] += 1
                continue
            key = _seed_key(result.get('seeds'))
            if key is None:
                data['malformed'] += 1
                continue
            if key in seen:
                data['deduplicated'] += 1
                continue
            seen.add(key)
            data['outcomes'].append(outcomes.outcome(
                result, candidate_id=cid, encounter_revision=encounter_revision,
                mechanics_revision=mechanics_revision, measurement_window=MEASUREMENT_WINDOW,
                policy=stored_policy))
        data['considered'] = len(ordinals)
        data['ordinalRange'] = [min(ordinals), max(ordinals)] if ordinals else None
    return data


def _joint_features(scenario):
    """Real pre-run joint mechanical features from the shared proposal generator, or None."""
    try:
        import strategy_mechanics as mechanics
        import strategy_encounter_compiler as compiler
        import strategy_joint_proposals as joint
        normalized = mechanics.normalize_scenario(scenario)
        roles = joint._role_indices(normalized)
        compiled = compiler.compile_encounter(normalized)
        profile = mechanics.profile(normalized)
        features = joint._features(profile, roles, compiled)
    except Exception:  # noqa: BLE001 - a feature that cannot be derived is simply absent
        return None
    if not isinstance(features, dict) or not features:
        return None
    return {name: float(value) for name, value in features.items()}


def _domain_block(scenario):
    """The build-domain fingerprint of the stored scenario, explicitly marked as current-computed."""
    try:
        fingerprint = domain.fingerprint(scenario)
    except Exception:  # noqa: BLE001
        return None
    revision = fingerprint.get('encounterRevision') or {}
    return dict(
        source='current-computed',
        identity=fingerprint.get('identity'),
        exactKey=fingerprint.get('exactKey'),
        normalizedFingerprint=fingerprint.get('normalizedFingerprint'),
        preparedDigest=fingerprint.get('preparedDigest'),
        mechanicsRevision=fingerprint.get('mechanicsRevision'),
        compilerRevision=fingerprint.get('compilerRevision'),
        encounterRevisionDigest=revision.get('digest') if isinstance(revision, dict) else None,
        domain=fingerprint.get('domain'),
        note='computed now from the stored scenario; not a stored historical revision stamp',
    )


def _standard_error(summary):
    resolved = summary.get('resolvedCount') or 0
    stdev = summary.get('stdev')
    if resolved < 2 or stdev is None:
        return None
    return stdev / (resolved ** 0.5)


def _excluded(cid, reason, detail='', counts=None):
    return dict(candidateId=cid, reason=reason, detail=detail, counts=counts or {})


def _stored_defeat(scenario):
    try:
        return int(scenario.get('defeatCount') or 0)
    except (TypeError, ValueError):
        return None


def _observe(db, cid, encounter_key, policy_declared, constraints, mechanics_revision,
             engine, compatibility, rows_per_candidate):
    row = db.execute('SELECT scenario FROM candidate WHERE id=?', (cid,)).fetchone()
    if row is None:
        return None, _excluded(cid, 'missing-scenario', 'no candidate row for this id')
    try:
        scenario = json.loads(row[0])
    except (TypeError, ValueError):
        return None, _excluded(cid, 'unreadable-scenario', 'stored scenario is not valid JSON')
    if not isinstance(scenario, dict):
        return None, _excluded(cid, 'unreadable-scenario', 'stored scenario is not a mapping')
    if domain.identity(scenario) != cid:
        return None, _excluded(cid, 'identity-mismatch',
                               'stored scenario does not hash to the requested candidate id')
    if scenario.get('encounterId') != encounter_key['encounterId']:
        return None, _excluded(cid, 'encounter-mismatch',
                               'stored encounterId %r != requested %r'
                               % (scenario.get('encounterId'), encounter_key['encounterId']))
    stored_defeat = _stored_defeat(scenario)
    if stored_defeat is None:
        return None, _excluded(cid, 'unreadable-scenario', 'stored defeatCount is not an integer')
    if stored_defeat != encounter_key['defeatCount']:
        return None, _excluded(cid, 'encounter-mismatch',
                               'stored defeatCount %r != requested %r'
                               % (scenario.get('defeatCount'), encounter_key['defeatCount']))
    if constraints is not None:
        try:
            validated = domain.validate_constraints(scenario, constraints)
        except Exception as exc:  # noqa: BLE001
            return None, _excluded(cid, 'constraints-invalid',
                                   'constraint validation failed: %s' % (exc,))
        if not validated.get('valid'):
            return None, _excluded(cid, 'constraints-mismatch',
                                   'stored scenario violates the supplied constraints')
    stored_policy = {key: scenario[key] for key in POLICY_KEYS if key in scenario}
    matched, detail = _policy_matches(policy_declared, scenario)
    if not matched:
        return None, _excluded(cid, 'policy-mismatch', detail)

    encounter_revision = encounter_key.get('revision')
    data = _prefix_outcomes(db, cid, rows_per_candidate, stored_policy, encounter_revision,
                            mechanics_revision)
    counts = dict(considered=data['considered'], bankSize=data['bankSize'],
                  deduplicated=data['deduplicated'], malformed=data['malformed'])
    if data['malformed']:
        return None, _excluded(cid, 'malformed-evidence',
                               'malformed seed pairs cannot be deduped safely', counts)
    if data['considered'] == 0:
        return None, _excluded(cid, 'no-validation-evidence',
                               'no retained validation rows in the compact table or replay window',
                               counts)

    summary = outcomes.summarize(data['outcomes'])
    features = _joint_features(scenario)
    complete = summary['meanEarned'] is not None and data['malformed'] == 0
    standard_error = _standard_error(summary)
    observation = dict(
        candidateId=cid,
        window=ADVISER_WINDOW,
        compatibility=compatibility,
        meanEarned=summary['meanEarned'],
        arithmeticMeanEarned=summary['partialMean'],
        partialMean=summary['partialMean'],
        resolved=summary['resolvedCount'],
        samples=summary['resolvedCount'],
        total=summary['total'],
        unknownCount=summary['unresolvedCount'],
        standardError=standard_error,
        uncertainty=dict(kind='sample-standard-error', value=standard_error,
                         note='over the available readings; heuristic, not a confidence interval'),
        reliability=(summary['resolvedCount'] / float(summary['total'])) if summary['total'] else None,
        lossFrequency=summary['lossFrequency'],
        median=summary['median'],
        features=features,
        eligibleForRecommendation=summary['eligibleForRecommendation'],
        adviserEligible=bool(complete and features is not None),
        evidenceClass='historical-prior',
        provenanceClass='legacy-library-prior',
        independentConfirmation=False,
        confirmationEligible=False,
        sourcePhase=VALIDATION_PHASE,
        measurementWindow=MEASUREMENT_WINDOW,
        sourcePolicy=stored_policy,
        sourceEncounter=dict(encounterId=encounter_key['encounterId'],
                             defeatCount=encounter_key['defeatCount']),
        ordinals=dict(source=data['source'], bankSize=data['bankSize'],
                      considered=data['considered'], filtered=data['deduplicated'],
                      malformed=data['malformed'], range=data['ordinalRange']),
        counts=dict(resolved=summary['resolvedCount'], unknown=summary['unresolvedCount'],
                    loss=summary['lossCount'], zero=summary['zeroCount'],
                    censored=summary['censoredCount'], error=summary['errorCount']),
        quantiles=summary['quantiles'],
        stdev=summary['stdev'],
        byStatus=summary['byStatus'],
        byBasis=summary['byBasis'],
        summary=summary,
        domain=_domain_block(scenario),
        mechanicsRevision=mechanics_revision,
        engine=dict(storedDigest=engine['storedDigest'], basis=engine['basis']),
    )
    return observation, None


def _cache_key(cid, engine, mechanics_revision, encounter_key, policy_declared, constraints,
               rows_per_candidate, compatibility):
    # ``compatibility`` is included because the observation carries it and the raw request values
    # (not just their normalized form) determine it.
    return _digest(dict(candidate=cid, engine=engine.get('storedDigest'), basis=engine.get('basis'),
                        mechanics=mechanics_revision, encounter=encounter_key,
                        policy=policy_declared, constraints=constraints, rows=rows_per_candidate,
                        compatibility=compatibility))


def load_observations(store, candidate_ids, *, encounter, policy, mechanics_revision,
                      engine_revision, constraints=None,
                      maximum_candidates=DEFAULT_MAXIMUM_CANDIDATES,
                      rows_per_candidate=DEFAULT_ROWS_PER_CANDIDATE, cache=None, immutable=True):
    """Read candidate-level adviser priors from a legacy library. Bounded and read-only.

    ``store`` is a path (opened read-only and closed here), a ``sqlite3.Connection``, or an object
    exposing ``.db``/``.get``/``.compatible`` such as the optimiser ``Store`` or a
    :class:`ReadOnlyStore`. ``candidate_ids`` are caller-admitted candidates, in the caller's order (the first
    ``maximum_candidates`` are read); each is read on its own and never pooled with another.
    ``engine_revision`` is the simulator provenance digest (or a
    full provenance mapping) the current learner runs under. ``cache`` may be a caller-owned dict
    used to reuse an observation across identical calls.
    """
    requested, duplicate_count, invalid = _unique_ids(candidate_ids)
    maximum_candidates = _clamp_int(maximum_candidates, 1, MAXIMUM_CANDIDATES_CAP,
                                    DEFAULT_MAXIMUM_CANDIDATES)
    rows_per_candidate = _clamp_int(rows_per_candidate, 1, ROWS_PER_CANDIDATE_CAP,
                                    DEFAULT_ROWS_PER_CANDIDATE)
    adapter, owned = _resolve_store(store, immutable)
    try:
        # A validated history-summary library is read through the compact loader: the same public
        # signature, but per-candidate summaries plus exact stored scenarios instead of scanning
        # millions of raw outcome rows. The summary loader re-checks the version and fails closed.
        if adapter.get('historySummaryVersion') is not None:
            import strategy_history_summary as summary
            return summary.load_observations(
                adapter, candidate_ids, encounter=encounter, policy=policy,
                mechanics_revision=mechanics_revision, engine_revision=engine_revision,
                constraints=constraints, maximum_candidates=maximum_candidates,
                rows_per_candidate=rows_per_candidate, window=summary.DEFAULT_WINDOW,
                cache=cache, immutable=immutable)
        db = adapter.db
        encounter_key, encounter_reason = _normalize_encounter(encounter)
        policy_declared, policy_reason = _normalize_policy(policy)
        mechanics_reason = None
        if not isinstance(mechanics_revision, str) or not mechanics_revision:
            mechanics_reason = 'mechanics-revision-unavailable'
        engine = _engine_basis(adapter, engine_revision)
        library = _library_view(adapter)
        revision = engine_revision if isinstance(engine_revision, str) else (
            (engine_revision or {}).get('digest') if isinstance(engine_revision, dict) else None)
        compatibility = _digest(dict(encounter=encounter, mechanics=mechanics_revision,
                                     revision=revision, constraints=constraints, policy=policy))
        request_reason = engine['reason'] or encounter_reason or policy_reason or mechanics_reason
        request_detail = engine['detail'] if engine['reason'] else (
            encounter_reason or policy_reason or mechanics_reason or '')

        excluded, observations = [], []
        for cid in invalid:
            excluded.append(_excluded(repr(cid), 'invalid-candidate-id',
                                      'candidate ids must be non-empty strings'))
        selected = requested[:maximum_candidates]
        overflow = requested[maximum_candidates:]
        for cid in overflow:
            excluded.append(_excluded(cid, 'above-candidate-cap',
                                      'requested beyond maximum_candidates=%d' % maximum_candidates))
        for cid in selected:
            if request_reason:
                excluded.append(_excluded(cid, request_reason, request_detail))
                continue
            key = None
            if isinstance(cache, dict):
                key = _cache_key(cid, engine, mechanics_revision, encounter_key, policy_declared,
                                 constraints, rows_per_candidate, compatibility)
                if key in cache:
                    observations.append(deepcopy(cache[key]))
                    continue
            observation, rejection = _observe(db, cid, encounter_key, policy_declared, constraints,
                                              mechanics_revision, engine, compatibility,
                                              rows_per_candidate)
            if rejection is not None:
                excluded.append(rejection)
                continue
            if key is not None:
                cache[key] = deepcopy(observation)
            observations.append(observation)

        by_reason = {}
        for entry in excluded:
            by_reason[entry['reason']] = by_reason.get(entry['reason'], 0) + 1
        counts = dict(
            requestedCandidates=len(requested) + len(invalid), uniqueCandidates=len(requested),
            duplicateCandidateIds=duplicate_count, invalidCandidateIds=len(invalid),
            includedCandidates=len(observations), excludedCandidates=len(excluded),
            observationsWithMean=sum(1 for obs in observations if obs['meanEarned'] is not None),
            adviserEligible=sum(1 for obs in observations if obs.get('adviserEligible')),
            rowsConsidered=sum(obs['ordinals']['considered'] for obs in observations),
            rowsResolved=sum(obs['counts']['resolved'] for obs in observations),
            rowsUnknown=sum(obs['counts']['unknown'] for obs in observations),
            rowsDeduplicated=sum(obs['ordinals']['filtered'] for obs in observations),
            rowsMalformed=sum(obs['ordinals']['malformed'] for obs in observations),
            byReason=by_reason,
        )
        provenance = dict(
            source=library,
            learner=dict(engineRevision=revision, engineBasis=engine['basis'],
                         storedProvenanceDigest=engine['storedDigest'],
                         mechanicsRevision=mechanics_revision, encounter=encounter_key,
                         policy=policy_declared, constraints=constraints,
                         compatibilityKey=compatibility),
            adviserWindow=ADVISER_WINDOW,
            independentConfirmation=False,
            reason=request_reason,
        )
        return dict(
            version=VERSION,
            observations=observations,
            excluded=excluded,
            provenance=provenance,
            counts=counts,
            compatibility=dict(compatible=engine['compatible'], basis=engine['basis'],
                               compatibilityKey=compatibility, reason=request_reason,
                               detail=request_detail),
            limits=list(LIMITATIONS),
        )
    finally:
        if owned is not None:
            owned.close()


__all__ = ['VERSION', 'VALIDATION_PHASE', 'MEASUREMENT_WINDOW', 'ADVISER_WINDOW',
           'ObservationError', 'ReadOnlyStore', 'open_readonly', 'load_observations',
           'DEFAULT_MAXIMUM_CANDIDATES', 'DEFAULT_ROWS_PER_CANDIDATE']

