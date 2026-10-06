"""Compare bulk recovery with the prior per-sample reference on one multi-session fixture.

Run: py -3.12 -B -X utf8 tools/recovery/check_optimizer_bulk_recovery.py
"""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import sys
import tempfile


HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import strategy_experiment_store as ledger
import strategy_search_mode as modes


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


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def old_recover_reference(db, *, session_id=None, count_completed=True):
    """The exact pre-change algorithm, kept in the checker as an independent oracle."""
    outstanding, blocked = [], []
    rows = db.execute(
        'SELECT DISTINCT s.sample_key, s.candidate_id, s.seed_a, s.seed_b '
        'FROM ea_sample s JOIN ea_sample_link l ON l.sample_key=s.sample_key '
        'WHERE s.result IS NULL ORDER BY s.candidate_id, s.seed_a, s.seed_b').fetchall()
    for sample_key, candidate_id, seed_a, seed_b in rows:
        links = db.execute('SELECT experiment_id, charged_experiment_id FROM ea_sample_link '
                           'WHERE sample_key=? ORDER BY experiment_id', (sample_key,)).fetchall()
        experiment_ids = [int(row[0]) for row in links]
        chargers = {int(row[1]) for row in links if row[1] is not None}
        charged = min(chargers) if chargers else min(experiment_ids)
        experiment = ledger._experiment(db, charged)
        active = True
        if experiment['session_id'] is not None:
            active = ledger._session_row(db, experiment['session_id'])['active']
        entry = dict(sampleKey=sample_key, candidateId=candidate_id,
                     seedPair=[int(seed_a), int(seed_b)], experimentIds=experiment_ids,
                     chargedExperimentId=charged, owner=experiment['owner'],
                     purpose=experiment['purpose'], sessionId=experiment['session_id'])
        if (ledger._effective_share(db, experiment) <= 0 or not active
                or (session_id is not None and experiment['session_id'] != session_id)):
            blocked.append(entry)
        else:
            outstanding.append(entry)
    return dict(outstanding=outstanding, outstandingCount=len(outstanding),
                blocked=blocked, blockedCount=len(blocked),
                bySample={row['sampleKey']: row for row in outstanding},
                completedCount=(int(db.execute(
                    'SELECT COUNT(*) FROM ea_sample WHERE result IS NOT NULL').fetchone()[0])
                    if count_completed else None))


def result_for(pair):
    return {'seeds': pair, 'verdict': 1, 'resultBackend': 'native',
            'rewardOutcome': {'pendingChests': 7}}


def make_config(encounter_id, count, *, mode='community-first'):
    config = modes.default_config(mode)
    config['purposes'] = {'improvement': count, 'boundary': 0, 'support': 0,
                          'comparison': 1, 'exploration': 0}
    config['studyScope'] = {'campaignScope': 'bulk-recovery-check',
                            'encounter': encounter_id}
    return config


def build_fixture(path, *, samples_per_encounter=25):
    db = sqlite3.connect(path, timeout=20)
    db.row_factory = sqlite3.Row
    ledger.initialize(db)
    sessions, experiments = [], []
    for encounter_id in range(20):
        kind = ('zero-share' if 14 <= encounter_id < 17 else
                'inactive' if encounter_id >= 17 else 'eligible')
        mode = 'all-strategy' if kind == 'zero-share' else 'community-first'
        config = make_config(encounter_id, samples_per_encounter, mode=mode)
        session_id = ledger.configure_session(db, config)
        intent = dict(
            scope='community', owner='community', ownerShare=1.0, purpose='improvement',
            sessionId=session_id, planned_budget=samples_per_encounter,
            stopping={'rule': 'finite-bulk-recovery-fixture', 'maxRuns': samples_per_encounter},
            policy={'finishPolicy': 'on-verdict', 'tickLimit': 100},
            mechanicsRevision='bulk-fixture-mechanics',
            encounterRevision='encounter-%d' % encounter_id,
            engineRevision='bulk-fixture-engine', measurementWindow='development',
            observedScenarios={})
        experiment_id = ledger.create_experiment(db, intent)
        sessions.append(dict(encounterId=encounter_id, sessionId=session_id,
                             kind=kind, config=config))
        experiments.append(experiment_id)
        for ordinal in range(samples_per_encounter):
            candidate_id = 'candidate-e%02d-%04d' % (encounter_id, ordinal)
            pair = [1_500_000_000 + encounter_id * 10_000 + ordinal * 2,
                    1_500_000_001 + encounter_id * 10_000 + ordinal * 2]
            if not ledger.reserve(db, experiment_id, candidate_id, pair):
                raise RuntimeError('fixture reservation refused at encounter %d sample %d'
                                   % (encounter_id, ordinal))
            if ordinal == 0:
                ledger.complete(db, experiment_id, candidate_id, pair, result_for(pair))

        if kind == 'zero-share':
            ledger.deactivate_session(db, session_id)
            changed = make_config(encounter_id, samples_per_encounter, mode='all-strategy')
            replacement = next(owner for owner in modes.share_streams()
                               if owner != 'community')
            changed['allocations'] = {owner: 0.0 for owner in modes.share_streams()}
            changed['allocations'][replacement] = 1.0
            ledger.configure_session(db, changed, session_id=session_id)
            ledger.activate_session(db, session_id)
            sessions[-1]['config'] = changed
        elif kind == 'inactive':
            ledger.deactivate_session(db, session_id)

    # Re-reserving the same experiment pair is idempotent. A second experiment in another session
    # may link the same unfinished sample, but its original charger remains authoritative.
    first_pair = [1_500_000_002, 1_500_000_003]
    duplicate_before = db.execute('SELECT COUNT(*) FROM ea_sample_link').fetchone()[0]
    check('duplicate-link-reservation-is-idempotent',
          ledger.reserve(db, experiments[0], 'candidate-e00-0001', first_pair)
          and db.execute('SELECT COUNT(*) FROM ea_sample_link').fetchone()[0] == duplicate_before)
    reuse_intent = dict(
        scope='community', owner='community', ownerShare=1.0, purpose='improvement',
        sessionId=sessions[1]['sessionId'], planned_budget=1,
        stopping={'rule': 'reused-sample-fixture'},
        policy={'finishPolicy': 'on-verdict', 'tickLimit': 100},
        mechanicsRevision='bulk-fixture-mechanics', encounterRevision='encounter-0',
        engineRevision='bulk-fixture-engine', measurementWindow='development')
    reuse_experiment = ledger.create_experiment(db, reuse_intent)
    if not ledger.reserve(db, reuse_experiment, 'candidate-e00-0001', first_pair):
        raise RuntimeError('cross-session sample reuse was refused')

    # Frozen confirmation holdouts are a distinct table and never enter development recovery.
    confirmation_intent = dict(
        scope='community', owner='community', ownerShare=1.0, purpose='comparison',
        sessionId=sessions[0]['sessionId'], planned_budget=1,
        stopping={'rule': 'frozen-comparison-fixture'},
        policy={'finishPolicy': 'on-verdict', 'tickLimit': 100},
        mechanicsRevision='bulk-fixture-mechanics', encounterRevision='encounter-0',
        engineRevision='bulk-fixture-engine', measurementWindow='confirmation')
    confirmation_experiment = ledger.create_experiment(db, confirmation_intent)
    ledger.freeze_confirmation(
        db, confirmation_experiment, nomination={'id': 'nominee'}, reference={'id': 'reference'},
        policy=confirmation_intent['policy'], metric='finalEarned',
        sample_plan=[{'candidateId': 'confirmation-candidate',
                      'seeds': [[1_900_000_001, 1_900_000_002]]}])
    return db, sessions, experiments, reuse_experiment, confirmation_experiment


def compare_reference(db, sessions, *, count_completed=False):
    global_report_old = old_recover_reference(
        db, session_id=None, count_completed=count_completed)
    global_report_new = ledger.recover(
        db, session_id=None, count_completed=count_completed)
    check('global-reference-equality', canonical(global_report_old) == canonical(global_report_new))
    for session in sessions:
        old_view = old_recover_reference(db, session_id=session['sessionId'],
                                         count_completed=count_completed)
        new_view = ledger.recover(db, session_id=session['sessionId'],
                                  count_completed=count_completed)
        check('session-%d-reference-equality' % session['encounterId'],
              canonical(old_view) == canonical(new_view))
    return global_report_old, global_report_new


def main():
    global PASSED, FAILED
    with tempfile.TemporaryDirectory(prefix='ka-bulk-recovery-check-') as temp:
        path = Path(temp) / 'fixture.sqlite'
        db, sessions, experiments, reuse_experiment, confirmation_experiment = build_fixture(path)
        before_changes = db.total_changes
        original_budgets = ledger.summary(db)
        report_old, report_new = compare_reference(db, sessions, count_completed=True)
        check('all-20-encounter-sessions-present',
              len({row['sessionId'] for row in sessions}) == 20)
        check('many-unfinished-reserved-samples',
              report_new['outstandingCount'] + report_new['blockedCount'] == 480)
        check('inactive-and-zero-share-samples-blocked',
              report_new['blockedCount'] == 144)
        check('eligible-session-ownership-retained',
              all(row['sessionId'] == sessions[0]['sessionId']
                  for row in report_new['outstanding'] if row['candidateId'].startswith('candidate-e00-')))
        reused = next(row for row in report_new['outstanding']
                      if row['candidateId'] == 'candidate-e00-0001')
        check('reused-sample-retains-original-charge-and-both-links',
              reused['chargedExperimentId'] == experiments[0]
              and reused['experimentIds'] == [experiments[0], reuse_experiment])
        check('frozen-confirmation-holdout-isolated',
              db.execute('SELECT COUNT(*) FROM ea_holdout WHERE experiment_id=?',
                         (confirmation_experiment,)).fetchone()[0] == 1
              and all(row['sampleKey'] != 'confirmation-candidate' for row in report_new['outstanding']))
        check('recovery-does-not-mutate-budget-or-database',
              db.total_changes == before_changes and ledger.summary(db) == original_budgets)
        check('completed-samples-excluded-and-counted',
              report_new['completedCount'] == 20
              and all(row['candidateId'].split('-')[-1] != '0000'
                      for row in report_new['outstanding'] + report_new['blocked']))

        # Restart/resume must rediscover the same immutable reserved jobs from SQLite.
        before_restart = canonical(report_new)
        db.close()
        db = sqlite3.connect(path, timeout=20)
        db.row_factory = sqlite3.Row
        after_restart = ledger.recover(db, session_id=None, count_completed=True)
        check('restart-resume-recovery-identical',
              canonical(json.loads(before_restart)) == canonical(after_restart))

        # Complete every unfinished sample via its charge owner, leaving no pending recovery work.
        remaining = old_recover_reference(db, session_id=None, count_completed=False)
        all_pending = {row['sampleKey']: row for row in remaining['outstanding'] + remaining['blocked']}
        for row in all_pending.values():
            ledger.complete(db, row['chargedExperimentId'], row['candidateId'],
                            row['seedPair'], result_for(row['seedPair']))
        empty_old = old_recover_reference(db, session_id=None, count_completed=True)
        empty_new = ledger.recover(db, session_id=None, count_completed=True)
        check('zero-unfinished-samples-reference-equality',
              canonical(empty_old) == canonical(empty_new)
              and empty_new['outstandingCount'] == 0 and empty_new['blockedCount'] == 0)
        check('zero-unfinished-completed-total',
              empty_new['completedCount'] == 500
              and ledger.summary(db)['conserved'])
        db.close()

    print('RESULT: %d passed, %d failed' % (PASSED, FAILED))
    return 1 if FAILED else 0


if __name__ == '__main__':
    raise SystemExit(main())
