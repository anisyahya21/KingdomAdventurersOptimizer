"""Guard the battle/measurement revision boundary and its one exact legacy transition.

Run: py -3.12 -B -X utf8 tools/recovery/check_optimizer_revision_boundary.py
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import sys


HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import strategy_encounter_search as search
import strategy_experiment_store as ledger
import strategy_revision as revisions
import strategy_optimizer as optimizer


PASSED = 0
FAILED = 0


def check(name, condition, detail=None):
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print('ok - ' + name)
    else:
        FAILED += 1
        print('not ok - ' + name + ('' if detail is None else ': ' + str(detail)))


def result_for(pair):
    return {'seeds': pair, 'verdict': 1, 'resultBackend': 'native',
            'rewardOutcome': {'pendingChests': 11}}


def main():
    global PASSED, FAILED
    baseline_path = (HERE / 'studies' / 'coordinator_single_scan_20260929'
                     / 'pre-implementation-revision.json')
    baseline = json.loads(baseline_path.read_text(encoding='utf-8'))
    current_revision, current_manifest = revisions.current_battle_compatibility_revision()
    captured_revision = baseline['battleCompatibilityRevisionAtBaseline']
    captured_inputs = baseline['battleCompatibilityManifestAtBaseline']
    changed_inputs = []
    for section in ('simulator', 'measurementAndPolicyFiles'):
        if section == 'simulator':
            before = captured_inputs[section]['files']
            after = current_manifest[section]['files']
        else:
            before = captured_inputs[section]
            after = current_manifest[section]
        changed_inputs.extend(name for name in sorted(set(before) | set(after))
                              if before.get(name) != after.get(name))
    legacy_is_current = (current_revision == revisions.LEGACY_KNOWN_GOOD_BATTLE_REVISION
                         == captured_revision)
    check('captured-legacy-digest-is-still-the-only-legacy-bridge',
          revisions.LEGACY_KNOWN_GOOD_BATTLE_REVISION == captured_revision)
    check('current-source-drift-closes-legacy-bridge',
          revisions.accepts_legacy_scheduler_revision(
              revisions.LEGACY_KNOWN_GOOD_SCHEDULER_REVISION, current_revision)
          == legacy_is_current,
          dict(currentRevision=current_revision, capturedRevision=captured_revision,
               changedInputs=changed_inputs))
    check('legacy-scheduler-revision-is-the-captured-runtime',
          revisions.LEGACY_KNOWN_GOOD_SCHEDULER_REVISION
          == baseline['legacyKnownGoodSchedulerRevision'])

    provenance = {
        'mode': 'test-runtime',
        'files': {
            'tools/recovery/strategy_optimizer.py': 'scheduler-before',
            'tools/recovery/strategy_optimizer_limits.py': 'limits-before',
            'tools/recovery/combat_resolution.py': 'combat-before',
            'tools/recovery/strategy_search.py': 'candidate-search-before',
        },
        'missing': [],
    }
    measurement = {'tools/recovery/strategy_outcomes.py': 'measurement-before'}
    policy = {'POLICY_KEYS': 'policy-keys', '_freeze_policy': 'policy-projection'}
    base, _ = revisions.revision_from_manifest(provenance, measurement, policy)
    scheduler_changed = dict(provenance, files=dict(provenance['files']))
    scheduler_changed['files']['tools/recovery/strategy_optimizer.py'] = 'scheduler-after'
    scheduler_changed['files']['tools/recovery/strategy_optimizer_limits.py'] = 'limits-after'
    unchanged, _ = revisions.revision_from_manifest(scheduler_changed, measurement, policy)
    check('scheduler-only-source-change-does-not-change-battle-revision', base == unchanged)
    combat_changed = dict(provenance, files=dict(provenance['files']))
    combat_changed['files']['tools/recovery/combat_resolution.py'] = 'combat-after'
    combat_revision, _ = revisions.revision_from_manifest(combat_changed, measurement, policy)
    check('combat-mechanics-change-changes-battle-revision', combat_revision != base)
    search_changed = dict(provenance, files=dict(provenance['files']))
    search_changed['files']['tools/recovery/strategy_search.py'] = 'candidate-search-after'
    candidate_revision, _ = revisions.revision_from_manifest(search_changed, measurement, policy)
    check('candidate-generation-source-is-protected', candidate_revision != base)
    measurement_changed = dict(measurement, **{'tools/recovery/strategy_outcomes.py': 'measurement-after'})
    measured_revision, _ = revisions.revision_from_manifest(provenance, measurement_changed, policy)
    check('measurement-source-is-protected', measured_revision != base)

    live_policy = {'finishPolicy': 'on-verdict', 'tickLimit': 100}
    coord = search.Coordinator(path='revision-check.sqlite', revision=current_revision,
                               scheduler_revision='scheduler-after', encounter=7)
    coord.state['policy'] = live_policy
    mechanics_revision = coord._mechanics_revision()
    legacy_revision = revisions.LEGACY_KNOWN_GOOD_SCHEDULER_REVISION
    legacy_compatibility = coord._compatibility_for_revision(legacy_revision)
    legacy_plan = dict(engineRevision=legacy_revision,
                       compatibility=legacy_compatibility,
                       mechanicsRevision=mechanics_revision,
                       encounterRevision='frozen-encounter-revision',
                       observedCandidate=None)
    if legacy_is_current:
        check('exact-known-good-frozen-plan-remains-accepted',
              coord._current_compatible(legacy_plan) == (True, None))
    else:
        check('changed-battle-inputs-block-old-frozen-plan',
              coord._current_compatible(legacy_plan)[0] is False,
              dict(currentRevision=current_revision, storedRevision=legacy_revision))
    blocked_legacy_plan = dict(legacy_plan, blocked=True,
                              blockedReason='loaded engine revision differs from the frozen plan')
    if legacy_is_current:
        check('persisted-known-good-scheduler-block-is-revalidated-and-resumed',
              coord._recover_known_good_scheduler_block(blocked_legacy_plan)
              and not blocked_legacy_plan.get('blocked')
              and 'blockedReason' not in blocked_legacy_plan
              and blocked_legacy_plan['engineRevision'] == legacy_revision
              and blocked_legacy_plan['compatibility'] == legacy_compatibility)
    else:
        check('persisted-old-plan-stays-blocked-after-battle-input-drift',
              not coord._recover_known_good_scheduler_block(blocked_legacy_plan)
              and blocked_legacy_plan.get('blocked'))
    other_block = dict(legacy_plan, blocked=True, blockedReason='mechanics revision differs')
    check('unrelated-block-reason-is-not-cleared',
          not coord._recover_known_good_scheduler_block(other_block)
          and other_block.get('blocked')
          and other_block.get('blockedReason') == 'mechanics revision differs')
    changed_semantics_coord = search.Coordinator(path='revision-check.sqlite',
                                                 revision=combat_revision,
                                                 scheduler_revision='scheduler-after', encounter=7)
    changed_semantics_coord.state['policy'] = live_policy
    legacy_allowed_after_mechanics_change = revisions.accepts_legacy_scheduler_revision(
        legacy_revision, combat_revision)
    check('legacy-bridge-closes-after-mechanics-change', not legacy_allowed_after_mechanics_change)
    check('mechanics-change-invalidates-old-frozen-plan',
          not changed_semantics_coord._current_compatible(legacy_plan)[0])
    blocked_after_mechanics_change = dict(
        legacy_plan, blocked=True,
        blockedReason='loaded engine revision differs from the frozen plan')
    check('scheduler-block-does-not-resume-after-mechanics-change',
          not changed_semantics_coord._recover_known_good_scheduler_block(
              blocked_after_mechanics_change)
          and blocked_after_mechanics_change.get('blocked'))

    db = sqlite3.connect(':memory:')
    db.row_factory = sqlite3.Row
    ledger.initialize(db)
    scenario = {'encounterId': 7, 'finishPolicy': 'on-verdict', 'tickLimit': 100,
                'ownUnits': [], 'foeUnits': []}
    intent = dict(scope='community', owner='community', ownerShare=1.0,
                  purpose='improvement', planned_budget=1, stopping='frozen-legacy-fixture',
                  policy=live_policy, mechanicsRevision=mechanics_revision,
                  encounterRevision='frozen-encounter-revision',
                  engineRevision=legacy_revision, compatibility=legacy_compatibility,
                  measurementWindow='development', observedScenarios={'candidate-old': scenario})
    experiment = ledger.create_experiment(db, intent)
    pair = [501, 502]
    ledger.reserve(db, experiment, 'candidate-old', pair)
    ledger.complete(db, experiment, 'candidate-old', pair, result_for(pair))
    frozen_before = ledger.experiment_intent(db, experiment)
    sample_before = db.execute('SELECT result,outcome FROM ea_sample').fetchone()
    changes_before = db.total_changes
    accepted_ids = coord._compatible_experiment_ids(db)
    _ = coord._deduped_outcomes(db, experiments=accepted_ids)
    frozen_after = ledger.experiment_intent(db, experiment)
    sample_after = db.execute('SELECT result,outcome FROM ea_sample').fetchone()
    check('legacy-experiment-admission-respects-current-battle-baseline',
          (experiment in accepted_ids) == legacy_is_current,
          dict(admitted=experiment in accepted_ids, legacyBaselineStillCurrent=legacy_is_current))
    check('historical-intent-and-evidence-are-never-restamped',
          frozen_before == frozen_after and tuple(sample_before) == tuple(sample_after)
          and db.total_changes == changes_before)

    retirement_coord = search.Coordinator(path='retirement-check.sqlite', revision=current_revision,
                                          scheduler_revision='scheduler-after', encounter=7)
    retirement_coord.state['policy'] = live_policy
    retirement_member = dict(
        experimentId=experiment, candidateId='candidate-old', referenceId='reference-old',
        engineRevision=legacy_revision, compatibility=legacy_compatibility,
        mechanicsRevision=mechanics_revision, encounterRevision='frozen-encounter-revision',
        jobs=[dict(candidateId='candidate-old', seedPair=pair)], observedCandidate={})
    retirement_coord._set_cohort([retirement_member])
    retired_count = retirement_coord._retire_incompatible_frozen_plans(db)
    frozen_after_retirement = ledger.experiment_intent(db, experiment)
    sample_after_retirement = db.execute('SELECT result,outcome FROM ea_sample').fetchone()
    check('engine-drift-retires-live-plan-without-touching-immutable-ledger-or-results',
          retired_count == (0 if legacy_is_current else 1)
          and frozen_after_retirement == frozen_before
          and tuple(sample_after_retirement) == tuple(sample_before)
          and db.total_changes == changes_before
          and ((experiment in retirement_coord._compatible_experiment_ids(db))
               == legacy_is_current))

    # The new revision is written only on future experiments; old JSON retains its original digest.
    current_compatibility = coord._compatibility()
    check('new-runtime-uses-semantic-battle-revision', current_revision != legacy_revision
          and coord._frozen_compatibility_matches(current_revision, current_compatibility))
    db.close()
    print('RESULT: %d passed, %d failed' % (PASSED, FAILED))
    return 1 if FAILED else 0


if __name__ == '__main__':
    raise SystemExit(main())
