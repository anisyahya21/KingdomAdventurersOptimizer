"""Bounded, strategy-only admission of identity-verified Student1 source leaders.

The manifest contains candidate identities, not historical reward or sample values. The source
library's outcomes stay provenance-incompatible with the current engine; each admitted strategy
must earn its current Development evidence through the normal encounter ledger before it can rank
or become a mutation parent.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import strategy_build_domain as domain
import strategy_students as students

MANIFEST_PATH = Path(__file__).with_name('student1_strategy_seed_manifest.json')
MAX_PRIORS_PER_ENCOUNTER = 2
VERSION = 'strategy-source-priors-1'
POLICY_KEYS = ('finishPolicy', 'tickLimit', 'holyHerbStock', 'startProfile', 'inputs',
               'holyHerbMaxUses', 'holyHerbTriggerUnits', 'mpWatchUnits')
_MISSING = object()


def _path_key(value):
    return str(value or '').replace('/', '\\').casefold()


def _manifest():
    try:
        value = json.loads(MANIFEST_PATH.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError) as error:
        return None, 'source-prior manifest unavailable: %s' % error
    if (not isinstance(value, dict) or value.get('schema') != 'strategy-source-prior-manifest-1'
            or not isinstance(value.get('encounters'), list)
            or int(value.get('candidatesPerEncounterMaximum') or 0) != MAX_PRIORS_PER_ENCOUNTER):
        return None, 'source-prior manifest has an unsupported shape'
    return value, None


def _manifest_candidates(value, encounter):
    row = next((item for item in value['encounters']
                if int(item.get('encounter', -1)) == int(encounter)), None)
    if row is None:
        return []
    candidates = row.get('candidates')
    if not isinstance(candidates, list) or len(candidates) > MAX_PRIORS_PER_ENCOUNTER:
        return []
    seen = set()
    selected = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        cid = str(item.get('candidateId') or '')
        identity = str(item.get('expectedIdentity') or '')
        rank = str(item.get('rank') or '')
        if (len(cid) != 64 or cid != identity or rank not in ('mean', 'highest')
                or cid in seen):
            continue
        seen.add(cid)
        selected.append(dict(candidateId=cid, expectedIdentity=identity, rank=rank))
    return selected


def _attribution(db, candidate_id, manifest, encounter):
    """Point-read exact import attribution; never inspect run/evidence/EA tables."""
    try:
        source = db.execute(
            'SELECT source_path,label,ownership,provenance_digest FROM history_source '
            'WHERE source_id=?', (int(manifest['sourceId']),)).fetchone()
        attribution = db.execute(
            'SELECT stored_id,identity_verified,alias_of,encounter_id,source_label,'
            'source_ownership,source_path FROM history_candidate '
            'WHERE candidate_id=? AND source_id=? ORDER BY stored_id LIMIT 1',
            (str(candidate_id), int(manifest['sourceId']))).fetchone()
    except Exception as error:  # noqa: BLE001 - absent summary schema fails closed
        return None, 'source attribution unavailable: %s' % error
    if source is None or attribution is None:
        return None, 'candidate has no matching imported source attribution'
    path, label, ownership, provenance_digest = source
    stored_id, identity_verified, alias_of, source_encounter, row_label, row_ownership, row_path = attribution
    if (_path_key(path) != _path_key(manifest.get('sourcePath'))
            or label != manifest.get('sourceLabel')
            or ownership != manifest.get('sourceOwnership')
            or provenance_digest != manifest.get('sourceProvenanceDigest')):
        return None, 'imported source identity differs from the pinned Student1 source'
    source_encounter_id = -1 if source_encounter is None else int(source_encounter)
    if (not identity_verified or str(stored_id) != str(candidate_id) or alias_of is not None
            or source_encounter_id != int(encounter)
            or row_label != manifest.get('sourceLabel')
            or row_ownership != manifest.get('sourceOwnership')
            or _path_key(row_path) != _path_key(manifest.get('sourcePath'))):
        return None, 'candidate import attribution is unverified, aliased, or cross-encounter'
    return dict(sourceId=int(manifest['sourceId']), sourceLabel=label,
                sourceOwnership=ownership, sourceProvenanceDigest=provenance_digest,
                sourceCandidateId=str(candidate_id), rank=None,
                historicalOutcomesUsed=False), None


def _policy_matches(scenario, policy, candidate_id=None):
    policy = policy or {}
    # Source identity is only preserved when every outcome-affecting input matches the current
    # frozen intent. In particular, a different start profile or declared item/MP policy must not
    # slip through merely because Finish and the tick limit match.
    # The current coordinator freezes the worker-visible build, which may add `mpWatchUnits` to an
    # evaluation copy. Apply that same observation-only projection for comparison; never mutate or
    # re-identify the stored source strategy. The watch list is still compared exactly after
    # projection, and every other policy field remains an exact match.
    try:
        import strategy_mp_recovery as mp_recovery
        observation_patch = mp_recovery.observation_patch(scenario, key=candidate_id)
    except Exception:  # noqa: BLE001 - uncertain observer projection fails closed
        return False
    compared = scenario
    if observation_patch:
        compared = dict(scenario)
        compared.update(observation_patch)
    for key in POLICY_KEYS:
        if compared.get(key, _MISSING) != policy.get(key, _MISSING):
            return False
    return True


def _features(scenario, constraints):
    """Derive the same current-mechanics feature vector proposals use; no outcomes involved."""
    import strategy_joint_proposals as joint
    normalized = joint.mechanics.normalize_scenario(scenario)
    _prepared, compiled, profile, roles, _table, _context, _provenance = \
        joint._parent_build_context(normalized, constraints)
    if roles.get(students.ROLE_DPS) is None:
        return None
    return joint._features(profile, roles, compiled)


def prepare(db, *, encounter, parent, policy, constraints, encounter_revision=None,
            current_engine_revision=None):
    """Return current-intent-valid source strategies plus one distinct transfer source, fail-closed."""
    manifest, error = _manifest()
    if error:
        return dict(proposals=[], transferScenario=None, transferBasis=None, refusals=[error])
    try:
        import strategy_history_summary as history
        import strategy_joint_proposals as joint
        import strategy_mechanics as mechanics
        if current_engine_revision is None:
            import strategy_revision
            current_engine_revision = strategy_revision.current_battle_compatibility_revision()[0]
    except Exception as exc:  # noqa: BLE001
        return dict(proposals=[], transferScenario=None, transferBasis=None,
                    refusals=['current strategy runtime unavailable: %s' % exc])
    proposals = []
    refusals = []
    seen = set()
    normalized_parent = mechanics.normalize_scenario(parent)
    parent_identity = domain.identity(normalized_parent)
    for item in _manifest_candidates(manifest, encounter):
        cid = item['candidateId']
        attribution, reason = _attribution(db, cid, manifest, encounter)
        if reason:
            refusals.append('%s: %s' % (cid, reason))
            continue
        attribution['rank'] = item['rank']
        scenario = history.stored_scenario(db, cid)
        if not isinstance(scenario, dict):
            refusals.append('%s: archived scenario unavailable' % cid)
            continue
        try:
            if domain.identity(scenario) != item['expectedIdentity']:
                raise ValueError('raw scenario identity differs from the pinned manifest')
            normalized = mechanics.normalize_scenario(scenario)
            if int(normalized.get('encounterId', -1)) != int(encounter):
                raise ValueError('scenario encounter does not match current encounter')
            if not _policy_matches(normalized, policy, candidate_id=cid):
                raise ValueError('source scenario policy differs from the frozen current policy')
            if not joint.domain.validate_constraints(
                    normalized, constraints if constraints is not None else joint.SYNTHETIC_CONSTRAINTS
            ).get('valid'):
                raise ValueError('source scenario is outside the current search domain')
            admitted, failures = students.community_admits(normalized, normalized_parent)
            if not admitted:
                raise ValueError('source scenario fails current Community rules: %s' %
                                 ', '.join(str(row[0]) for row in failures))
            compiled = joint.compiler.compile_encounter(normalized)
            current_encounter_revision = (compiled.get('revision') or {}).get('digest')
            if not current_encounter_revision or (encounter_revision and
                                                   current_encounter_revision != encounter_revision):
                raise ValueError('scenario does not match the frozen current encounter revision')
            identity = domain.identity(normalized)
            if identity in seen:
                continue
            seen.add(identity)
            features = _features(normalized, constraints)
            if not features:
                raise ValueError('current-mechanics feature vector unavailable')
            import strategy_stream_proposals as stream
            changed_fields = stream._changed_fields(normalized_parent, normalized)
            source_basis = dict(kind='source-strategy-prior', sourceId=attribution['sourceId'],
                                sourceLabel=attribution['sourceLabel'],
                                sourceOwnership=attribution['sourceOwnership'],
                                sourceProvenanceDigest=attribution['sourceProvenanceDigest'],
                                sourceCandidateId=cid, sourceIdentity=item['expectedIdentity'],
                                sourceRank=item['rank'], historicalOutcomesUsed=False,
                                currentEngineRevision=current_engine_revision,
                                currentEncounterRevision=current_encounter_revision)
            proposal_id = hashlib.sha256(domain.canonical(dict(
                version=VERSION, source=source_basis, encounter=int(encounter),
                policy=policy, identity=identity)).encode('utf-8')).hexdigest()
            proposals.append(dict(
                id='source-strategy-prior:' + proposal_id, childIdentity=identity,
                scenario=normalized, purpose='improvement', changedFields=changed_fields, fixedFields={},
                features=features, domain=joint.domain.validate_constraints(
                    normalized, constraints if constraints is not None else joint.SYNTHETIC_CONSTRAINTS),
                label='Student1 source %s strategy' % item['rank'],
                sourcePrior=source_basis, strategyBasis=source_basis,
                expectedMechanicalDifferences=None, approximatelyPreserved=None,
                whySimulation='identity-verified source strategy; current Development evidence only; '
                              'historical outcomes excluded from ranking and measurement'))
        except Exception as exc:  # noqa: BLE001 - invalid sources are refused, never repaired silently
            refusals.append('%s: %s' % (cid, exc))
    proposals.sort(key=lambda row: (0 if row['sourcePrior']['sourceRank'] == 'mean' else 1,
                                    row['childIdentity']))
    transfer = next((row for row in proposals if row['childIdentity'] != parent_identity), None)
    return dict(proposals=proposals, transferScenario=(transfer['scenario'] if transfer else None),
                transferBasis=(transfer['strategyBasis'] if transfer else None),
                refusals=refusals, manifestSourceId=int(manifest['sourceId']))


__all__ = ['MANIFEST_PATH', 'MAX_PRIORS_PER_ENCOUNTER', 'prepare']
