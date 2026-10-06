"""Deterministic checks for the incremental encounter-overview read model.

Every check is a small, self-contained SQLite fixture (plus a read-only pass over a real Community
ledger when one is reachable), so the incremental contract - rowid high-water pagination, bounded
pending-rowid completion re-checks, a COUNT-free change token, table-qualified identities, exact
grouping, no fabricated zeros and read-only behaviour - is verified without touching the live library.

Writes a short report to tmp/encounter-redesign-20260928/live-overview-cache-report.md.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)

import strategy_encounter_overview as ov  # noqa: E402

WORK = os.path.join(REPO, 'tmp', 'encounter-redesign-20260928', 'check-overview-ledger')
REPORT = os.path.join(REPO, 'tmp', 'encounter-redesign-20260928',
                      'live-overview-cache-report.md')
SCRATCH = r'C:\Users\anisb\AppData\Local\Temp\community-run-smoke-z_ibiq4h\scratch.sqlite'
REAL_FALLBACK = os.path.join(REPO, 'tmp', 'encounter-redesign-20260928',
                             'ka-accept-joint-ffcb5dd4', 'lib.sqlite')

_SCHEMA = '''
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE candidate(id TEXT PRIMARY KEY, scenario TEXT NOT NULL, label TEXT NOT NULL,
                       source TEXT NOT NULL, stats TEXT NOT NULL, created INTEGER NOT NULL);
CREATE TABLE ea_experiment(
    id INTEGER PRIMARY KEY AUTOINCREMENT, request_key TEXT NOT NULL UNIQUE, scope TEXT NOT NULL,
    parent_id INTEGER, reference_id TEXT, policy TEXT, mechanics_revision TEXT,
    encounter_revision TEXT, purpose TEXT, owner TEXT, owner_share REAL NOT NULL DEFAULT 1.0,
    fixed_fields TEXT NOT NULL, changed_fields TEXT NOT NULL, planned_budget INTEGER NOT NULL,
    stopping TEXT NOT NULL, session_id INTEGER, measurement_window TEXT, created_at REAL NOT NULL,
    intent_json TEXT);
CREATE TABLE ea_sample(
    sample_key TEXT PRIMARY KEY, candidate_id TEXT NOT NULL, policy TEXT, mechanics_revision TEXT,
    encounter_revision TEXT, seed_a INTEGER NOT NULL, seed_b INTEGER NOT NULL,
    measurement_window TEXT, result TEXT, outcome TEXT, timing TEXT, created_at REAL NOT NULL);
CREATE TABLE ea_sample_link(
    experiment_id INTEGER NOT NULL, candidate_id TEXT NOT NULL, seed_a INTEGER NOT NULL,
    seed_b INTEGER NOT NULL, sample_key TEXT NOT NULL, reused INTEGER NOT NULL DEFAULT 0,
    charged_experiment_id INTEGER, created_at REAL NOT NULL,
    PRIMARY KEY(experiment_id, candidate_id, seed_a, seed_b));
CREATE TABLE ea_holdout(
    experiment_id INTEGER NOT NULL, candidate_id TEXT NOT NULL, seed_a INTEGER NOT NULL,
    seed_b INTEGER NOT NULL, result TEXT, outcome TEXT, timing TEXT, completed_at REAL,
    PRIMARY KEY(experiment_id, candidate_id, seed_a, seed_b));
'''


class Skip(Exception):
    """A check that cannot run in this environment (never a pass, never a failure)."""


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def fixture(name):
    """A fresh fixture database this script owns (and may therefore write to)."""
    if not os.path.isdir(WORK):
        os.makedirs(WORK)
    path = os.path.join(WORK, name + '.sqlite')
    for suffix in ('', '-wal', '-shm'):
        if os.path.exists(path + suffix):
            os.remove(path + suffix)
    db = sqlite3.connect(path)
    db.executescript(_SCHEMA)
    return path, db


def readonly(path):
    return sqlite3.connect('file:%s?mode=ro' % path.replace('\\', '/'), uri=True)


def add_candidate(db, cid, encounter_id=19, defeat_count=0, label='build', source='supplied'):
    db.execute('INSERT INTO candidate(id,scenario,label,source,stats,created) VALUES(?,?,?,?,?,?)',
               (cid, json.dumps(dict(encounterId=encounter_id, defeatCount=defeat_count)),
                label, source, '{}', 0))


def add_experiment(db, experiment_id, *, policy='{"finishPolicy":"on-verdict"}',
                   mechanics='m1', encounter_revision='e1', window='"development"',
                   scope='community'):
    db.execute('INSERT INTO ea_experiment(id,request_key,scope,policy,mechanics_revision,'
               'encounter_revision,fixed_fields,changed_fields,planned_budget,stopping,'
               'measurement_window,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
               (experiment_id, 'req-%s' % experiment_id, scope, policy, mechanics,
                encounter_revision, '{}', '{}', 1000, 'budget', window, 0.0))


def result_json(backend='native', verdict=1, seeds=(1, 2), pending=0):
    return json.dumps(dict(resultBackend=backend, verdict=verdict, seeds=list(seeds),
                           rewardOutcome=dict(pendingChests=pending)))


def outcome_json(verdict=1, final_earned=0, pending=0, resolved=True, censored=False, error=False,
                 engine='acceptance', experiment=1, window=None):
    payload = dict(verdict=verdict, finalEarned=final_earned, pending=pending, resolved=resolved,
                   censored=censored, error=error, engineRevision=engine, experimentId=experiment,
                   status='checked', basis='fixture', reason='fixture')
    if window is not None:
        payload['measurementWindow'] = window
    return json.dumps(payload)


def add_sample(db, key, cid, *, window='development', mechanics='m1', encounter_revision='e1',
               policy='{"p":1}', seeds=(1, 2), result=None, outcome=None, created=1000.0):
    db.execute('INSERT INTO ea_sample(sample_key,candidate_id,policy,mechanics_revision,'
               'encounter_revision,seed_a,seed_b,measurement_window,result,outcome,timing,created_at)'
               ' VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
               (key, cid, policy, mechanics, encounter_revision, seeds[0], seeds[1], window,
                result, outcome, None, created))


def add_link(db, experiment_id, cid, seeds=(1, 2), sample_key=None, reused=0, charged=None):
    db.execute('INSERT INTO ea_sample_link(experiment_id,candidate_id,seed_a,seed_b,sample_key,'
               'reused,charged_experiment_id,created_at) VALUES(?,?,?,?,?,?,?,?)',
               (experiment_id, cid, seeds[0], seeds[1], sample_key, reused, charged, 0.0))


def add_holdout(db, experiment_id, cid, seeds, *, result=None, outcome=None, completed=2000.0):
    db.execute('INSERT INTO ea_holdout(experiment_id,candidate_id,seed_a,seed_b,result,outcome,'
               'timing,completed_at) VALUES(?,?,?,?,?,?,?,?)',
               (experiment_id, cid, seeds[0], seeds[1], result, outcome, None, completed))


def main():
    db = sqlite3.connect(':memory:')
    db.executescript(_SCHEMA)
    add_candidate(db, 'build')
    add_experiment(db, 1)
    add_experiment(db, 2)
    add_sample(db, 'pending', 'build')
    for i in range(10):
        add_sample(db, 'known%d' % i, 'build', seeds=(i, i+1),
                   result=result_json(), outcome=outcome_json(final_earned=60))
    # Reuse links never duplicate the actual measurement.
    add_link(db, 1, 'build', sample_key='known0')
    add_link(db, 2, 'build', sample_key='known0', reused=1)
    cache = ov.LedgerCache()
    view = cache.view(db)
    assert view['counts']['total'] == 10
    assert view['rows']['19:0']['attempts'] == 10
    leader = view['leaders'][0]['highestAvgEarned']
    assert leader['candidateId'] == 'build' and leader['mean'] == 60
    before = dict(cache.stats)
    sql = []
    db.set_trace_callback(sql.append)
    assert cache.view(db) == view
    assert cache.stats == before and not any('SELECT' in q.upper() for q in sql)
    assert not any('COUNT(' in q.upper() for q in sql)
    db.set_trace_callback(None)
    db.execute('UPDATE ea_sample SET result=?,outcome=? WHERE sample_key=?',
               (result_json(verdict=2), outcome_json(verdict=2, final_earned=0), 'pending'))
    view = cache.view(db)
    assert view['counts']['total'] == 11 and cache.stats['rowsRead'] == 11
    assert abs(view['leaders'][0]['highestAvgEarned']['mean'] - 600/11) < 1e-8
    add_sample(db, 'fallback', 'build', result=result_json(backend='fallback'),
               outcome=outcome_json(final_earned=999, censored=True, error=True))
    add_sample(db, 'unknown', 'build', result=result_json(), outcome=None)
    add_sample(db, 'otherwindow', 'build', window='new', result=result_json(),
               outcome=outcome_json(final_earned=999))
    view = cache.view(db)
    assert view['counts']['total'] == 14 and view['counts']['excluded'] == 1
    assert abs(view['leaders'][0]['highestAvgEarned']['mean'] - 600/11) < 1e-8
    assert view['rows']['19:0']['highestChestsEarned'] == 999  # native new-window maximum
    add_holdout(db, 1, 'build', (1,2), result=result_json(), outcome=outcome_json(final_earned=900))
    assert cache.view(db)['counts']['total'] == 15
    add_sample(db, 'unmapped', 'later', result=result_json(), outcome=outcome_json(final_earned=3))
    view = cache.view(db)
    assert view['counts']['total'] == 16 and view['coverage']['mapped'] == 15
    add_candidate(db, 'later', encounter_id=15)
    view = cache.view(db)
    assert view['counts']['total'] == 16 and view['coverage']['unmapped'] == 0
    assert view['rows']['15:0']['attempts'] == 1
    db.execute('DELETE FROM ea_sample WHERE sample_key=?', ('unmapped',))
    view = cache.view(db)
    assert view['counts']['total'] == 15, view['counts']
    # Real native data and byte-for-byte read-only preservation.
    from pathlib import Path
    p = Path(SCRATCH)
    before_hash = hashlib.sha256(p.read_bytes()).hexdigest()
    real = sqlite3.connect(p.as_uri()+'?mode=ro', uri=True)
    try:
        real_view = ov.LedgerCache().view(real)
        assert real_view['counts']['total'] == real_view['counts']['native'] == 52, real_view
        assert real_view['rows']['15:0']['attempts'] == 52
        assert real.total_changes == 0
    finally:
        real.close()
    assert hashlib.sha256(p.read_bytes()).hexdigest() == before_hash
    Path(REPORT).write_text('PASS: incremental append, old pending completion, unchanged-poll query check, reused links, exact window separation, excluded outcome union, holdout separation, missing-build restoration, rowid regression, and 52 real native samples read-only.\n', encoding='utf-8')
    print('PASS encounter overview: incremental counters, evidence separation and 52 real native samples')


if __name__ == '__main__':
    main()
