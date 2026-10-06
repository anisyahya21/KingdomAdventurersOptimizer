"""Deterministic checks for the player operating-region read model.

Run later (the backend benchmark is frozen; do not run this while it is live):

    <venv>/python -B -X utf8 tools/recovery/check_player_regions.py

Read-only: mocks, one persisted real-Coordinator fixture and a small temporary SQLite library only.
No battle, no optimiser command, no live library, no settings and no heavy owner query. It exercises
the pure synthesis with snapshot fixtures, the real 32-observation boundary fixture, and the bounded
read-only loader/bridge with a throwaway candidate table.
"""
from __future__ import annotations

import copy
import hashlib
import json
import shutil
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

KA = HERE.parents[1]
WORKSPACE = HERE.parents[2]
SCENARIO_PATH = (WORKSPACE / 'RE-evidence' / '20260920-user-wairo-strategy' / 'scenarios'
                 / 'UREF.scenario.json')
REAL_FIXTURE = KA / 'tmp' / 'encounter-redesign-20260928' / 'compensated-boundary-example.json'

import strategy_encounter_presentation as presentation  # noqa: E402
import strategy_mechanics as mechanics                  # noqa: E402
import strategy_optimizer_desktop as desktop             # noqa: E402
import strategy_player_regions as player_regions         # noqa: E402

RESULTS = []


def check(name, condition, detail=''):
    RESULTS.append(dict(name=name, ok=bool(condition), detail=detail))
    print(('PASS' if condition else 'FAIL') + ' ' + name + ((' :: ' + str(detail)) if detail else ''))


# --------------------------------------------------------------------------- #
# Canonical scenario fixtures (existing canonical setup, never a duplicated formula)
# --------------------------------------------------------------------------- #
def load_scenario():
    scenario = json.loads(SCENARIO_PATH.read_text(encoding='utf-8'))
    scenario['encounterId'] = 3
    scenario['defeatCount'] = 0
    return scenario


def bump_raw(scenario, index, pid, delta):
    variant = copy.deepcopy(scenario)
    parameter = variant['ownUnits'][index]['parameters'][str(pid)]
    parameter['rawValue'] = int(parameter['rawValue']) + int(delta)
    return variant


def reorder_skills(scenario, index):
    variant = copy.deepcopy(scenario)
    variant['ownUnits'][index]['skills'] = list(reversed(variant['ownUnits'][index]['skills']))
    return variant


def set_finish_policy(scenario, policy):
    variant = copy.deepcopy(scenario)
    variant['finishPolicy'] = policy
    return variant


def set_invocation(scenario, index, slot, level):
    variant = copy.deepcopy(scenario)
    variant['ownUnits'][index]['invocationLevels'][slot] = level
    return variant


def canonical_effective(scenario):
    """The canonical prepared effective stats, read back through the shared services."""
    normalized = mechanics.normalize_scenario(scenario)
    prepared = mechanics.prepared_setup(normalized)
    roles, _status = presentation._role_indices(normalized)
    stats = {}
    for entry in presentation.question_fields(normalized, prepared, roles):
        if entry['currentValue'] is not None:
            stats['%s.%s' % (entry['role'], entry['stat'])] = entry['currentValue']
    return stats


# --------------------------------------------------------------------------- #
# Snapshot fixtures (JSON shapes only; nothing here runs a battle)
# --------------------------------------------------------------------------- #
def portfolio_row(cid, mean, **flags):
    row = dict(candidateId=cid, label=cid, meanEarned=mean, partialMean=mean, samples=12,
               total=12, unknownCount=0, uncertainty=dict(kind='SE', value=0.7), median=mean,
               window='development', development=True, confirmation=False, synthetic=True,
               playerUnknown=False, domainContext='synthetic', provenanceStatus='declared')
    row.update(flags)
    return row


def make_snapshot(portfolio, boundaries=(), reference_id='ref', mode='community-first',
                  runtime_revision='engine-rev-1', question=None):
    snapshot = dict(mode=mode, encounter=dict(encounterId=3), portfolio=list(portfolio),
                    boundaries=list(boundaries), runtimeRevision=runtime_revision,
                    presentation=dict(
                        version='strategy-encounter-presentation-1', referenceId=reference_id,
                        encounterDetails=dict(encounterId=3, revision='enc-digest'),
                        mechanicsSummary=dict(encounterRevision='mech-enc-rev',
                                              mechanicsRevision='mech-rev',
                                              compilerRevision='comp-rev')))
    if question is not None:
        snapshot['question'] = question
    return snapshot


def archetype_of(archetypes, cid):
    for archetype in archetypes:
        for region in archetype['regions']:
            for point in region['points']:
                if point['candidateId'] == cid:
                    return archetype
    return None


def region_of(archetype, kind):
    for region in archetype['regions']:
        if region['kind'] == kind:
            return region
    return None


def tested_points(archetype):
    """The measured portfolio points only (never a boundary region's echoed point)."""
    region = region_of(archetype, 'tested-alternatives')
    return {point['candidateId']: point for point in (region['points'] if region else [])}


def boundary_points(archetype):
    return [point for region in archetype['regions']
            if region['kind'] != 'tested-alternatives' for point in region['points']]


def regions_with(archetype, cid):
    return [region for region in archetype['regions']
            if any(point['candidateId'] == cid for point in region['points'])]


def iter_keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key)
            yield from iter_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_keys(child)


def boundary_value(field, kind, value):
    return dict(field=field, kind=kind, value=value)


def build_fixture():
    base = load_scenario()
    scenarios = dict(ref=base, a=base, b=bump_raw(base, 0, 13, 3),
                     policy=set_finish_policy(base, 'after-ending'),
                     skills=reorder_skills(base, 0), rates=set_invocation(base, 0, 0, 1),
                     noreward=base, degraded=base, historical=base, conf=base,
                     playerctx=base, censoredclaim=base, nominee_dev=base)
    portfolio = [
        portfolio_row('ref', 30.0, thresholds={'10': dict(count=5, denominator=12, probability=0.4)}),
        portfolio_row('a', 12.0),
        portfolio_row('b', 20.0, thresholdProbabilities={'10': dict(count=4, denominator=12,
                                                                   probability=0.33)}),
        portfolio_row('policy', 18.0),
        portfolio_row('skills', 14.0),
        portfolio_row('rates', 11.0),
        portfolio_row('noreward', None, partialMean=5.0, samples=6, total=12, unknownCount=6),
        portfolio_row('degraded', 8.0),
        portfolio_row('historical', 7.0, window=None, development=False,
                      evidenceClass='historical-unconfirmed'),
        portfolio_row('conf', 25.0, window='holdout', development=False, confirmation=True),
        portfolio_row('unknownprov', 9.0, synthetic=False, playerUnknown=False, domainContext=None,
                      provenanceStatus=None),
        portfolio_row('playerctx', 10.0, context='player', domainContext='player'),
        portfolio_row('nominee_dev', 6.0, confirmation=True, window='development',
                      development=True),
        portfolio_row('censoredclaim', 99.0, partialMean=99.0, samples=9, total=12,
                      unknownCount=3),
    ]
    degraded_value = boundary_value('ownUnits.0.parameters.13', 'fixed-build', 100)
    support_value = boundary_value('ownUnits.0.parameters.15', 'fixed-build', 60)
    boundaries = [
        dict(kind='fixed-build', frozenReference='ref', questionId='q-degraded',
             childValue=degraded_value,
             testedPoints=[dict(candidateId='degraded', value=degraded_value,
                                classification='supported-degraded', pairedCount=20,
                                diagnosticInterval=[-3.2, -0.8],
                                uncertaintyMethod='paired percentile bootstrap')],
             unresolvedGaps=[dict(candidateId='degraded', value=degraded_value,
                                  classification='unresolved', pairedCount=3,
                                  reason='Insufficient complete paired evidence for this declared '
                                         'diagnostic plan')],
             assessment=dict(classification='supported-degraded', pairedCount=20,
                             unresolvedReference=0, unresolvedCandidate=2, meanEarned=60.0,
                             totalCandidate=20, totalReference=20, tolerance=0.9,
                             diagnosticInterval=[-3.2, -0.8])),
        dict(kind='fixed-build', frozenReference='ref', questionId='q-support',
             childValue=support_value,
             testedPoints=[dict(candidateId='b', value=support_value,
                                classification='supported-acceptable', pairedCount=30,
                                diagnosticInterval=[1.0, 2.5],
                                uncertaintyMethod='paired percentile bootstrap')],
             unresolvedGaps=[],
             assessment=dict(classification='supported-acceptable', pairedCount=30,
                             unresolvedReference=0, unresolvedCandidate=0, totalReference=30,
                             totalCandidate=30, meanEarned=42.0, referenceMeanEarned=40.0,
                             tolerance=0.9, diagnosticInterval=[1.0, 2.5])),
        dict(kind='compensated', frozenReference='ref', questionId='q-compensated',
             childValue=degraded_value,
             testedPoints=[dict(candidateId='a', value=degraded_value,
                                classification='supported-acceptable', pairedCount=18,
                                diagnosticInterval=[0.5, 2.0],
                                uncertaintyMethod='paired percentile bootstrap')],
             unresolvedGaps=[],
             assessment=dict(classification='supported-acceptable', pairedCount=18,
                             unresolvedReference=0, unresolvedCandidate=0, totalReference=18,
                             totalCandidate=18, meanEarned=15.0, referenceMeanEarned=12.0,
                             tolerance=0.9, diagnosticInterval=[0.5, 2.0])),
    ]
    question = dict(id='q-support', referenceId='ref', kind='fixed-build',
                    encounterRevision='mech-enc-rev', mechanicsRevision='mech-rev',
                    policy=dict(finishPolicy='at-horizon', holyHerbStock=0), tolerance=0.9,
                    field='ownUnits.0.parameters.15', fixedFields={})
    return (make_snapshot(portfolio, boundaries, question=question), scenarios,
            dict(base=base, degraded_value=degraded_value, support_value=support_value))


# --------------------------------------------------------------------------- #
# Checks - pure synthesis
# --------------------------------------------------------------------------- #
def check_archetype_grouping(summary, scenarios):
    archetypes = summary['archetypes']
    arch_a = archetype_of(archetypes, 'a')
    arch_b = archetype_of(archetypes, 'b')
    arch_policy = archetype_of(archetypes, 'policy')
    arch_skills = archetype_of(archetypes, 'skills')
    arch_rates = archetype_of(archetypes, 'rates')
    arch_player = archetype_of(archetypes, 'playerctx')
    check('same-structure-same-archetype',
          arch_a is not None and arch_a is arch_b and arch_a['id'] == arch_b['id'])
    check('different-policy-split', arch_policy is not None and arch_policy['id'] != arch_a['id'])
    check('different-skill-order-split', arch_skills is not None and arch_skills['id'] != arch_a['id'])
    check('different-invocation-rate-split',
          arch_rates is not None and arch_rates['id'] != arch_a['id'])
    check('different-context-split', arch_player is not None and arch_player['id'] != arch_a['id'])
    check('archetype-count', len(archetypes) == 6, [a['id'] for a in archetypes])

    points = tested_points(arch_a)
    check('same-archetype-both-points', 'a' in points and 'b' in points)
    check('adjustable-stats-are-actual-effective',
          points['a']['stats'] == canonical_effective(scenarios['a'])
          and points['b']['stats'] == canonical_effective(scenarios['b']))
    check('nearby-vectors-differ-in-effective-stats',
          points['a']['stats'] != points['b']['stats'])
    check('regional-conditions-are-fixed-requirements',
          any('formation:' in text for text in region_of(arch_a, 'tested-alternatives')['conditions'])
          and any('skills:' in text for text in region_of(arch_a, 'tested-alternatives')['conditions']))
    check('changed-fields-visible-vs-reference',
          set(points['b'].get('changedFields') or []) != set())


def check_no_pooling_or_gapfill(summary, scenarios):
    archetypes = summary['archetypes']
    arch_a = archetype_of(archetypes, 'a')
    points = tested_points(arch_a)
    check('no-cross-candidate-pooling',
          points['a']['meanEarned'] == 12.0 and points['b']['meanEarned'] == 20.0)
    check('no-averaged-point-invented', all(point['meanEarned'] != 16.0 for point in points.values()))
    tested = {point['candidateId'] for archetype in archetypes for region in archetype['regions']
              for point in region['points']}
    check('no-interpolated-points', tested <= set(scenarios) | {'unknownprov'},
          sorted(tested - (set(scenarios) | {'unknownprov'})))
    forbidden = ('viable', 'unviable', 'unusable', 'gapfill')
    labels = [point['classification'] for archetype in archetypes for region in archetype['regions']
              for point in region['points']]
    check('no-universal-viability-label',
          all(not any(word in label.lower() for word in forbidden) for label in labels))


def check_reward_and_evidence(summary):
    arch_a = archetype_of(summary['archetypes'], 'a')
    points = tested_points(arch_a)
    check('null-reward-stays-null',
          'noreward' in points and points['noreward']['meanEarned'] is None)
    check('null-reward-not-zero', points['noreward']['meanEarned'] != 0)
    check('lower-reward-retained', 'degraded' in points and points['degraded']['meanEarned'] == 8.0)
    check('unresolved-no-reward',
          points['noreward']['meanEarned'] is None and points['noreward']['partialMean'] == 5.0
          and points['noreward']['unknownCount'] == 6)
    check('censored-full-mean-fail-closed',
          points['censoredclaim']['meanEarned'] is None
          and points['censoredclaim']['partialMean'] == 99.0
          and points['censoredclaim']['unknownCount'] == 3)
    check('development-window-counts',
          points['a']['evidenceStatus'] == 'development' and points['a']['samples'] == 12
          and points['a']['total'] == 12 and points['a']['unknownCount'] == 0)
    check('confirmation-is-its-own-window',
          points['conf']['evidenceStatus'] == 'confirmed-holdout'
          and points['conf']['meanEarned'] == 25.0 and points['a']['meanEarned'] == 12.0)
    check('no-confirmation-without-holdout-window',
          points['nominee_dev']['evidenceStatus'] == 'development'
          and points['nominee_dev']['evidenceStatus'] != 'confirmed-holdout')
    check('historical-label-preserved',
          points['historical']['evidenceStatus'] == 'historical-unconfirmed')
    check('reference-classified', points['ref']['classification'] == 'reference')
    check('row-thresholds-preferred',
          points['ref'].get('thresholds') == {'10': dict(count=5, denominator=12, probability=0.4)})
    check('threshold-probabilities-fallback',
          points['b'].get('thresholds') == {'10': dict(count=4, denominator=12, probability=0.33)})
    check('resource-policy-known-from-scenario',
          isinstance(points['a'].get('resourcePolicy'), dict)
          and points['a']['resourcePolicy'].get('finishPolicy') == 'at-horizon')
    check('resource-policy-unknown-without-scenario',
          tested_points(archetype_of(summary['archetypes'], 'unknownprov'))
          ['unknownprov'].get('resourcePolicy') is None)


def check_context_field_honoured(summary):
    arch = archetype_of(summary['archetypes'], 'playerctx')
    check('explicit-context-field-honoured',
          arch is not None and arch['context'] == 'player'
          and tested_points(arch)['playerctx']['context'] == 'player')


def check_boundaries_and_honesty(summary, scenarios):
    archetypes = summary['archetypes']
    arch_a = archetype_of(archetypes, 'a')
    degraded_all = regions_with(arch_a, 'degraded')
    degraded_regions = [region for region in degraded_all if region['kind'] == 'fixed-build']
    support_regions = [region for region in regions_with(arch_a, 'b')
                       if region['kind'] == 'fixed-build']
    degraded_region = degraded_regions[0] if degraded_regions else None
    support_region = support_regions[0] if support_regions else None
    check('failed-tolerance-tests-retained',
          degraded_region is not None and degraded_region['gaps'])
    check('degraded-tested-point-retained',
          degraded_region is not None and any(point['classification'] == 'supported-degraded'
                                              for point in degraded_region['points']))
    check('degraded-region-makes-no-support-claim',
          degraded_region is not None and degraded_region['support'] == [])
    check('controlled-support-shown-when-supported',
          support_region is not None and support_region['support'])
    check('no-low-impact-claim',
          all(region['lowImpact'] == [] for archetype in archetypes
              for region in archetype['regions']))
    check('no-safe-box-or-continuous-coverage',
          all(region['continuousCoverage'] is False and region['safeCartesianProduct'] is False
              for archetype in archetypes for region in archetype['regions']))
    minimum_keys = [key for key in iter_keys(summary) if 'minimum' in key.lower()]
    check('no-minimum-key-invented', not minimum_keys, minimum_keys[:5])
    check('every-region-declares-uncovered-gaps',
          all(region['gaps'] for archetype in archetypes for region in archetype['regions']))

    support_point = support_region['points'][0] if support_region else {}
    check('boundary-known-mean-retained',
          support_point.get('meanEarned') == 42.0 and support_point.get('samples') == 30
          and support_point.get('total') == 30 and support_point.get('unknownCount') == 0
          and support_point.get('evidenceStatus') == 'development')
    check('boundary-reference-mean-and-tolerance-captured',
          support_point.get('referenceMeanEarned') == 40.0 and support_point.get('tolerance') == 0.9)
    degraded_point = degraded_region['points'][0] if degraded_region else {}
    check('boundary-incomplete-mean-unknown',
          degraded_point.get('meanEarned') is None and degraded_point.get('unknownCount') == 2)
    complete = dict(meanEarned=10, unresolvedReference=0, unresolvedCandidate=0,
                    totalReference=8, totalCandidate=8, pairedCount=8)
    for missing in ('unresolvedReference', 'unresolvedCandidate'):
        incomplete = {key: value for key, value in complete.items() if key != missing}
        check('boundary-missing-count-unknown-' + missing,
              player_regions._boundary_assessment_mean(incomplete) is None)
    check('boundary-holdout-window-alone-not-confirmed',
          player_regions._boundary_evidence_status(dict(window='holdout')) == 'holdout')
    check('boundary-confirmation-label-alone-not-confirmed',
          player_regions._boundary_evidence_status(dict(confirmationStatus='confirmed'))
          == 'development')
    check('boundary-explicit-confirmed-holdout',
          player_regions._boundary_evidence_status(dict(window='holdout', confirmation=True))
          == 'confirmed-holdout')
    check('boundary-point-stats-are-own-child',
          degraded_point.get('stats') == canonical_effective(scenarios['degraded'])
          and support_point.get('stats') == canonical_effective(scenarios['b']))
    check('boundary-point-child-ids-not-reference',
          degraded_point.get('candidateId') == 'degraded'
          and support_point.get('candidateId') == 'b'
          and support_point.get('referenceId') == 'ref')
    check('boundary-not-duplicated-across-candidates',
          len(degraded_all) == 2 and len(support_regions) == 1
          and {point['candidateId'] for point in support_region['points']} == {'b'})


def check_unknown_provenance(summary):
    archetypes = summary['archetypes']
    arch_unknown = archetype_of(archetypes, 'unknownprov')
    check('missing-scenario-own-archetype',
          arch_unknown is not None and arch_unknown['id'] != 'unknown'
          and sum(1 for a in archetypes if a['id'] == arch_unknown['id']) == 1)
    check('missing-scenario-context-unknown',
          arch_unknown is not None and arch_unknown['context'] == 'unknown')
    check('missing-scenario-reachability-unknown',
          arch_unknown is not None and arch_unknown['reachability'] == 'unknown')
    point = tested_points(arch_unknown).get('unknownprov')
    check('missing-scenario-stats-empty-not-fake',
          point is not None and point['stats'] == {} and point['meanEarned'] == 9.0)
    check('missing-scenario-unknowns-reported',
          arch_unknown is not None and any('unknown' in text.lower()
                                           for text in arch_unknown['unknowns']))
    ids = [archetype['id'] for archetype in archetypes]
    check('archetype-ids-unique', len(ids) == len(set(ids)))
    region_ids = [region['id'] for archetype in archetypes for region in archetype['regions']]
    check('region-ids-unique', len(region_ids) == len(set(region_ids)))


def check_scope_coverage_and_serialisation(summary, snapshot, scenarios):
    check('version', summary['version'] == player_regions.READ_MODEL_VERSION)
    check('status-available', summary['status'] == 'available')
    check('scope-echoed', summary['scope']['encounterId'] == 3
          and summary['scope']['referenceId'] == 'ref'
          and summary['scope']['mode'] == 'community-first')
    check('source-revision-is-backend-revision',
          summary['sourceRevision'] == 'engine-rev-1')
    check('revisions-actual-presentation-shape',
          player_regions._revisions(snapshot['presentation'])
          == dict(encounterRevision='mech-enc-rev', mechanicsRevision='mech-rev',
                  compilerRevision='comp-rev'))
    coverage = summary['coverage']
    check('coverage-shape',
          set(coverage) == {'candidateLimit', 'availableCandidates', 'includedCandidates',
                            'truncated'})
    check('coverage-counts', coverage['truncated'] is False
          and coverage['availableCandidates'] == coverage['includedCandidates']
          and coverage['includedCandidates'] == len(snapshot['portfolio']))
    check('json-round-trip', json.loads(json.dumps(summary)) == summary)
    check('deterministic', player_regions.build_summary(snapshot, scenarios) == summary)
    check('unavailable-honest',
          player_regions.build_summary(None)['status'] == 'unavailable'
          and player_regions.build_summary({})['status'] == 'unavailable')


def candidate_table_digest(library):
    """A read-only digest of the library's candidate rows, proving a load mutates nothing."""
    digest = hashlib.sha256()
    db = desktop._readonly_db(library)
    try:
        for row in db.execute('SELECT id, scenario FROM candidate ORDER BY id'):
            digest.update(str(row['id']).encode('utf-8'))
            digest.update(b'\x00')
            digest.update(str(row['scenario']).encode('utf-8'))
            digest.update(b'\x01')
    finally:
        db.close()
    return digest.hexdigest()


def check_real_coordinator_fixture():
    check('reference-id-plain-string', player_regions._reference_id('abc') == 'abc')
    check('reference-id-json-encoded-string',
          player_regions._reference_id('"abc"') == 'abc')
    check('reference-id-dict',
          player_regions._reference_id({'referenceId': 'abc'}) == 'abc')
    check('boundary-owner-excludes-reference',
          player_regions._boundary_child_packs(
              dict(frozenReference='ref',
                   testedPoints=[dict(candidateId='ref'), dict(candidateId='child')])) == [
                  ('testedPoints', 'child', dict(candidateId='child'))])
    if not REAL_FIXTURE.is_file():
        check('real-fixture-exists', False, str(REAL_FIXTURE))
        return
    fixture = json.loads(REAL_FIXTURE.read_text(encoding='utf-8'))
    entries = fixture['boundaryEntries']
    ref_id = fixture['baseline']['suppliedCandidateId']
    real_policy = fixture['frozenQuestion']['policy']['finishPolicy']
    library = KA / str(fixture['runDir']).replace('\\', '/') / 'lib.sqlite'
    check('real-fixture-library-exists', library.is_file(), str(library))

    # Enumerate children by the ACTUAL stored scenario id (testedPoints.candidateId), never by the
    # initial proposal (childId). The persisted Coordinator fixture lets the two diverge; the old
    # fabrication keyed a copied UREF scenario by childId and changed only the tested DEX, which
    # attached the real boundary mean 20 to an ATK343/at-horizon point that was never stored.
    child_ids = []
    for entry in entries:
        for _field, child, _pack in player_regions._boundary_child_packs(entry):
            if child not in child_ids:
                child_ids.append(child)
    declared_atk = {row['candidateId']: row['childAtk'] for row in fixture['childChangedFields']
                    if row.get('candidateId') is not None}
    declared_dex = {row['candidateId']: row['childDex'] for row in fixture['childChangedFields']
                    if row.get('candidateId') is not None}
    check('real-fixture-children-keyed-by-stored-scenario',
          set(child_ids) == set(declared_atk), sorted(set(child_ids) ^ set(declared_atk)))

    # The real production path: load the stored reference/children read-only from the run library.
    loaded = player_regions.load_candidate_scenarios(desktop._readonly_db, library,
                                                     [ref_id] + child_ids)
    digest_before = candidate_table_digest(library)
    snapshot = make_snapshot([portfolio_row(ref_id, 10.0, label='reference')], entries,
                             reference_id=ref_id)
    summary = player_regions.build_summary_from_library(snapshot, library,
                                                        readonly_db=desktop._readonly_db)
    digest_after = candidate_table_digest(library)

    def effective(cid):
        scenario = loaded.get(cid)
        return canonical_effective(scenario) if scenario is not None else None

    check('real-fixture-status', summary['status'] == 'available', summary.get('reason'))
    check('real-fixture-database-unchanged', digest_before == digest_after, digest_before[:12])
    check('real-fixture-zero-real-battles', fixture.get('realBattles') == 0)
    ref_arch = archetype_of(summary['archetypes'], ref_id)
    children_arch = archetype_of(summary['archetypes'], child_ids[0]) if child_ids else None
    check('real-fixture-role-separation',
          ref_arch is not None and children_arch is not None and ref_arch is not children_arch)
    check('real-fixture-reference-has-no-child-region',
          ref_arch is not None
          and all(region['kind'] == 'tested-alternatives' for region in ref_arch['regions']))
    ref_point = tested_points(ref_arch).get(ref_id) if ref_arch is not None else None
    check('real-fixture-reference-stats-own',
          ref_point is not None and ref_point['stats'] == effective(ref_id))
    check('real-fixture-reference-atk-dex-pair',
          effective(ref_id) is not None
          and effective(ref_id).get('dps.atk') == fixture['baseline']['dpsAtk']
          and effective(ref_id).get('dps.dex') == fixture['baseline']['dpsDex'])
    points = boundary_points(children_arch) if children_arch is not None else []
    check('real-fixture-children-own-stats',
          children_arch is not None
          and all(point['stats'] == effective(point['candidateId']) for point in points))
    check('real-fixture-children-atk-dex-pairs',
          bool(points) and all(point['stats'].get('dps.atk') == declared_atk[point['candidateId']]
                               and point['stats'].get('dps.dex') == declared_dex[point['candidateId']]
                               for point in points))
    check('real-fixture-child-atk-not-baseline',
          bool(points) and all(point['stats'].get('dps.atk') != fixture['baseline']['dpsAtk']
                               for point in points))
    check('real-fixture-children-distinct-stats',
          children_arch is not None
          and len({json.dumps(point['stats'], sort_keys=True) for point in points}) == len(child_ids))
    check('real-fixture-no-reference-test-point',
          all(point['candidateId'] != ref_id for point in points))
    check('real-fixture-known-boundary-mean',
          len(points) == len(child_ids) and all(point['meanEarned'] == 20.0 for point in points)
          and all(point['samples'] == 8 and point['total'] == 8 and point['unknownCount'] == 0
                  for point in points))
    check('real-fixture-tolerance-and-reference-mean',
          bool(points) and all(point.get('tolerance') == 0.9
                               and point.get('referenceMeanEarned') == 10.0 for point in points))
    check('real-fixture-reference-mean-separate',
          ref_point is not None and ref_point.get('meanEarned') == 10.0
          and all(point['meanEarned'] == 20.0 for point in points))
    check('real-fixture-development-window',
          bool(points) and all(point['evidenceStatus'] == 'development' for point in points))
    check('real-fixture-boundary-region-kind',
          children_arch is not None
          and all(region['kind'] == 'compensated' for region in children_arch['regions']))
    check('real-fixture-coverage-count',
          summary['coverage']['includedCandidates'] == len(child_ids) + 1)
    check('real-fixture-no-duplicate-child-points',
          len(child_ids) == len(set(child_ids)))
    check('real-fixture-child-policy-on-verdict',
          bool(points) and all((point.get('resourcePolicy') or {}).get('finishPolicy')
                               == real_policy for point in points)
          and all((loaded.get(cid) or {}).get('finishPolicy') == real_policy for cid in child_ids))
    check('real-fixture-reference-policy-on-verdict',
          (loaded.get(ref_id) or {}).get('finishPolicy') == real_policy)
    check('real-fixture-no-fabricated-at-horizon',
          real_policy != 'at-horizon'
          and all((loaded.get(cid) or {}).get('finishPolicy') != 'at-horizon'
                  for cid in child_ids + [ref_id]))


def check_cap_and_readonly_loader():
    tmp_root = KA / 'tmp' / 'player-regions-check'
    tmp_root.mkdir(parents=True, exist_ok=True)
    work = tmp_root / 'work'
    shutil.rmtree(str(work), ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    try:
        db_path = work / 'tiny-library.sqlite'
        base = load_scenario()
        connection = sqlite3.connect(str(db_path))
        connection.execute('CREATE TABLE candidate (id TEXT PRIMARY KEY, scenario TEXT)')
        total_rows = 300
        for index in range(total_rows):
            connection.execute('INSERT INTO candidate (id, scenario) VALUES (?, ?)',
                               ('c%03d' % index, json.dumps(base)))
        connection.execute('INSERT INTO candidate (id, scenario) VALUES (?, ?)',
                           ('edge', json.dumps(base)))
        connection.execute('INSERT INTO candidate (id, scenario) VALUES (?, ?)',
                           ('ref', json.dumps(base)))
        connection.commit()
        connection.close()

        portfolio = [portfolio_row('c%03d' % index, float(index)) for index in range(total_rows)]
        edge_value = boundary_value('ownUnits.0.parameters.19', 'fixed-build', 11)
        boundaries = [dict(kind='fixed-build', frozenReference='ref', questionId='q-edge',
                           childValue=edge_value,
                           testedPoints=[dict(candidateId='edge', value=edge_value,
                                              classification='supported-degraded', pairedCount=20,
                                              diagnosticInterval=[-3.0, -1.0])],
                           unresolvedGaps=[],
                           assessment=dict(classification='supported-degraded', pairedCount=20,
                                           unresolvedCandidate=0, unresolvedReference=0,
                                           totalCandidate=20, totalReference=20, meanEarned=3.0,
                                           referenceMeanEarned=9.0, tolerance=0.9))]
        snapshot = make_snapshot(portfolio, boundaries)

        opened = []
        original_readonly = desktop._readonly_db

        def spy(path):
            handle = original_readonly(path)
            opened.append(handle)
            return handle

        class FakeOptimizer:
            def __init__(self, payload):
                self.payload = payload
                self.commands = []
                self.status_calls = 0

            def status(self):
                self.status_calls += 1
                return dict(encounterAware=self.payload)

            def command(self, *args, **kwargs):
                self.commands.append((args, kwargs))

        fake = FakeOptimizer(snapshot)
        bridge = desktop.Bridge.__new__(desktop.Bridge)
        bridge._optimizer = fake
        bridge._library = db_path
        desktop._readonly_db = spy
        try:
            result = bridge.encounter_player_regions(candidate_limit=64)
        finally:
            desktop._readonly_db = original_readonly

        check('bridge-status-available', result['status'] == 'available', result.get('reason'))
        check('bridge-no-optimizer-commands', fake.commands == [] and fake.status_calls == 1)
        check('cap-enforced',
              result['coverage']['candidateLimit'] == 64
              and result['coverage']['availableCandidates'] == total_rows + 2
              and result['coverage']['truncated'] is True
              and result['coverage']['includedCandidates'] <= 64)
        included = {point['candidateId'] for archetype in result['archetypes']
                    for region in archetype['regions'] for point in region['points']}
        check('cap-preserves-boundary-candidate', 'edge' in included)
        check('cap-keeps-lowest-mean-candidate', 'c299' in included)
        check('cap-keeps-highest-mean-candidate', 'c000' in included)
        check('bridge-opens-one-readonly-connection', len(opened) == 1)
        closed = True
        try:
            opened[0].execute('SELECT 1')
            closed = False
        except sqlite3.ProgrammingError:
            closed = True
        check('bridge-closes-readonly-connection', closed)

        readonly = True
        probe = original_readonly(db_path)
        try:
            probe.execute("INSERT INTO candidate (id, scenario) VALUES ('x', '{}')")
            readonly = False
        except sqlite3.OperationalError:
            readonly = True
        finally:
            probe.close()
        check('readonly-library-connection-refuses-writes', readonly)

        empty = player_regions.build_summary_from_library(None, db_path,
                                                          readonly_db=original_readonly)
        check('empty-snapshot-unavailable', empty['status'] == 'unavailable')
        check('empty-snapshot-opens-no-database', len(opened) == 1)
    finally:
        shutil.rmtree(str(work), ignore_errors=True)


def main():
    check('scenario-fixture-exists', SCENARIO_PATH.is_file(), str(SCENARIO_PATH))
    snapshot, scenarios, _variants = build_fixture()
    summary = player_regions.build_summary(snapshot, scenarios)

    check_archetype_grouping(summary, scenarios)
    check_no_pooling_or_gapfill(summary, scenarios)
    check_reward_and_evidence(summary)
    check_context_field_honoured(summary)
    check_boundaries_and_honesty(summary, scenarios)
    check_unknown_provenance(summary)
    check_scope_coverage_and_serialisation(summary, snapshot, scenarios)
    check_real_coordinator_fixture()
    check_cap_and_readonly_loader()

    failed = [row for row in RESULTS if not row['ok']]
    print('\n%d/%d checks passed' % (len(RESULTS) - len(failed), len(RESULTS)))
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
