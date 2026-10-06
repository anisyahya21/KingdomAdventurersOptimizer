"""Scheduling readiness must not fetch/decode holdout evidence or change plan counting."""
import json
import sqlite3
import statistics
import time
from pathlib import Path

import strategy_experiment_store as ledger
import strategy_payload_codec as codec


def fixture():
    db = sqlite3.connect(':memory:')
    ledger.initialize(db)
    plan = [dict(candidateId=candidate, seeds=[[i, i + 1000] for i in range(64)])
            for candidate in ('nominee', 'reference')]
    payload = dict(samplePlan=plan, metric='earnedMean', runsPlanned=128)
    db.execute('INSERT INTO ea_confirmation(experiment_id,plan_key,payload,created_at) '
               'VALUES(1,"frozen",?,0)', (json.dumps(payload),))
    db.executemany('INSERT INTO ea_holdout(experiment_id,candidate_id,seed_a,seed_b) '
                   'VALUES(1,?,?,?)',
                   ((entry['candidateId'], *pair) for entry in plan for pair in entry['seeds']))
    return db, payload


def compare(db):
    full = ledger.confirmation_report(db, 1)
    progress = ledger.confirmation_progress(db, 1)
    assert progress == {key: value for key, value in full.items() if key != 'outcomes'}
    assert 'outcomes' not in progress and progress.get('earlyPeek') is False


def main():
    db, payload = fixture()
    assert ledger.confirmation_progress(db, 2) == ledger.confirmation_report(db, 2)
    compare(db)
    outcome = codec.encode_text(json.dumps(dict(score=7, provenance=['exact-evidence'] * 2048)))
    assert isinstance(outcome, bytes), 'test must exercise real compressed evidence'
    db.execute('UPDATE ea_holdout SET result="{}",outcome=? WHERE candidate_id="nominee"',
               (outcome,))
    compare(db)
    assert ledger.confirmation_progress(db, 1)['runsCompleted'] == 64
    db.execute('UPDATE ea_holdout SET result="{}",outcome=?', (outcome,))
    compare(db)
    assert ledger.confirmation_progress(db, 1)['ready']
    # Unplanned completed rows cannot inflate readiness; duplicate plan occurrences retain
    # the legacy report's membership/count behavior even on malformed historical fixtures.
    db.execute('INSERT INTO ea_holdout VALUES(1,"extra",9000,9001,"{}",?,NULL,NULL)', (outcome,))
    compare(db)
    payload['samplePlan'][0]['seeds'].append([0, 1000])
    db.execute('UPDATE ea_confirmation SET payload=?', (json.dumps(payload),))
    compare(db)
    assert ledger.confirmation_progress(db, 1)['runsPlanned'] == 129
    # Prove outcome cells are not read. SQL trace inspection alone would be weaker.
    reads = []
    def authorizer(action, table, column, *_):
        if action == sqlite3.SQLITE_READ and table == 'ea_holdout' and column == 'outcome':
            reads.append(column)
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    db.set_authorizer(authorizer)
    assert ledger.confirmation_progress(db, 1)['ready'] and not reads
    try:
        ledger.confirmation_report(db, 1)
    except sqlite3.DatabaseError:
        pass
    else:
        raise AssertionError('full reader should still fetch outcome evidence')
    db.set_authorizer(None)
    # Corrupt evidence must fail at the explicit full report boundary, not be decoded by dispatch.
    db.execute('UPDATE ea_holdout SET outcome="invalid-json" WHERE candidate_id="nominee"')
    assert ledger.confirmation_progress(db, 1)['ready']
    try:
        ledger.confirmation_report(db, 1)
    except ValueError:
        pass
    else:
        raise AssertionError('corrupt full evidence was accepted')
    db.execute('UPDATE ea_holdout SET outcome=?', (outcome,))
    # Readiness follows result IS NOT NULL, not outcome availability/encoding.
    db.execute('UPDATE ea_holdout SET result=NULL WHERE candidate_id="reference" AND seed_a=0')
    compare(db)
    assert not ledger.confirmation_progress(db, 1)['ready']
    db.execute('UPDATE ea_holdout SET result="{}"')
    times = {'full': [], 'progress': []}
    for trial in range(7):
        names = ('full', 'progress') if trial % 2 == 0 else ('progress', 'full')
        for name in names:
            method = ledger.confirmation_report if name == 'full' else ledger.confirmation_progress
            started = time.perf_counter()
            for _ in range(5):
                method(db, 1)
            times[name].append((time.perf_counter() - started) / 5)
    report = dict(passed=True, cases=['unfrozen', 'empty', 'partial', 'complete', 'unplanned-extra',
                  'duplicate-plan-occurrence', 'no-outcome-column-read', 'corrupt-evidence-boundary',
                  'NULL-completion'], benchmarkScope='synthetic ready confirmation gate only',
                  medianSeconds={name: statistics.median(values) for name, values in times.items()},
                  appThroughputGainMeasured=False)
    target = Path(__file__).parent / 'studies/throughput_architecture11/confirmation-progress-check.json'
    target.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report))
    db.close()


if __name__ == '__main__':
    main()
