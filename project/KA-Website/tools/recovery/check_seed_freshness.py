"""Focused checks for `strategy_seed_freshness`, the shared fail-closed freshness collector.

Temporary SQLite fixtures only; no library, no simulator, nothing under the repo is written.
Pins the contract the coordinator relies on when it calls `collect` once before a confirmation:
global coverage of every eliminated candidate's evidence, every pruned run window, every global
ordinal/aggregate/probe/fine-tune/declared bank and every EA reservation/holdout pair; strict
rejection of malformed or out-of-range seeds, missing schema and read failures; no database writes;
and a cache invalidated by new data, a changed revision or a different connection.

    python check_seed_freshness.py
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
from collections.abc import Set as AbstractSet
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import strategy_seed_freshness as freshness

REQUIRED_DDL = {
    'candidate': 'CREATE TABLE candidate(id TEXT PRIMARY KEY, scenario TEXT NOT NULL, '
                 'label TEXT NOT NULL, source TEXT NOT NULL, stats TEXT NOT NULL, '
                 'created INTEGER NOT NULL)',
    'run': 'CREATE TABLE run(candidate TEXT NOT NULL, phase TEXT NOT NULL, ordinal INTEGER NOT NULL, '
           'result TEXT NOT NULL, PRIMARY KEY(candidate,phase,ordinal))',
    'evidence': 'CREATE TABLE evidence(candidate TEXT NOT NULL, phase TEXT NOT NULL, '
                'ordinal INTEGER NOT NULL, seeds TEXT, verdict INTEGER, '
                'censored INTEGER NOT NULL DEFAULT 0, prizeCallbacks INTEGER, awardedChests INTEGER, '
                'awardedBasis TEXT, pendingChests INTEGER, '
                'PRIMARY KEY(candidate,phase,ordinal)) WITHOUT ROWID',
    'meta': 'CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)',
}
EA_DDL = {
    'ea_sample': 'CREATE TABLE ea_sample(sample_key TEXT PRIMARY KEY, candidate_id TEXT NOT NULL, '
                 'policy TEXT, mechanics_revision TEXT, encounter_revision TEXT, '
                 'seed_a INTEGER NOT NULL, seed_b INTEGER NOT NULL, measurement_window TEXT, '
                 'result TEXT, outcome TEXT, timing TEXT, created_at REAL NOT NULL)',
    'ea_sample_link': 'CREATE TABLE ea_sample_link(experiment_id INTEGER NOT NULL, '
                      'candidate_id TEXT NOT NULL, seed_a INTEGER NOT NULL, seed_b INTEGER NOT NULL, '
                      'sample_key TEXT NOT NULL, reused INTEGER NOT NULL DEFAULT 0, '
                      'charged_experiment_id INTEGER, created_at REAL NOT NULL, '
                      'PRIMARY KEY(experiment_id, candidate_id, seed_a, seed_b))',
    'ea_holdout': 'CREATE TABLE ea_holdout(experiment_id INTEGER NOT NULL, '
                  'candidate_id TEXT NOT NULL, seed_a INTEGER NOT NULL, seed_b INTEGER NOT NULL, '
                  'result TEXT, outcome TEXT, timing TEXT, completed_at REAL, '
                  'PRIMARY KEY(experiment_id, candidate_id, seed_a, seed_b))',
}


def _seed_pair(phase, index):
    from strategy_optimizer import seed_pair
    return tuple(int(value) for value in seed_pair(phase, index))


def _make_db(path, *, ea=True, required=None, overrides=None):
    connection = sqlite3.connect(str(path))
    for name in (list(REQUIRED_DDL) if required is None else list(required)):
        connection.execute((overrides or {}).get(name) or REQUIRED_DDL[name])
    if ea:
        for ddl in EA_DDL.values():
            connection.execute(ddl)
    return connection


def _add_evidence(connection, candidate, phase, ordinal, pair, *, raw=None):
    connection.execute(
        'INSERT INTO evidence(candidate,phase,ordinal,seeds,verdict,censored) VALUES(?,?,?,?,?,0)',
        (candidate, phase, ordinal, json.dumps(list(pair)) if raw is None else raw, 1))


def _add_run(connection, candidate, phase, ordinal):
    connection.execute('INSERT INTO run VALUES(?,?,?,?)',
                       (candidate, phase, ordinal, '{"verdict": 1}'))


def _set_meta(connection, key, value):
    connection.execute('INSERT INTO meta VALUES(?,?)',
                       (key, value if isinstance(value, str) else json.dumps(value)))


def _blocked(function):
    try:
        function()
    except freshness.Blocked as exc:
        return str(exc)
    raise AssertionError('expected freshness.Blocked')


def schema_checks(tmp):
    empty = sqlite3.connect(str(tmp / 'empty.sqlite'))
    reasons = {'emptyDatabase': _blocked(lambda: freshness.collect(empty, cache={}))}
    empty.close()
    no_meta = _make_db(tmp / 'no-meta.sqlite', ea=False, required=('candidate', 'run', 'evidence'))
    reasons['missingMeta'] = _blocked(lambda: freshness.collect(no_meta, cache={}))
    no_meta.close()
    no_evidence = _make_db(tmp / 'no-evidence.sqlite', ea=False, required=('candidate', 'run', 'meta'))
    reasons['missingEvidence'] = _blocked(lambda: freshness.collect(no_evidence, cache={}))
    no_evidence.close()
    bad_run = _make_db(tmp / 'bad-run.sqlite', ea=False,
                       overrides={'run': 'CREATE TABLE run(candidate TEXT, ordinal INTEGER)'})
    reasons['runReadFailure'] = _blocked(lambda: freshness.collect(bad_run, cache={}))
    bad_run.close()
    bad_evidence = _make_db(
        tmp / 'bad-evidence.sqlite', ea=False,
        overrides={'evidence': 'CREATE TABLE evidence(candidate TEXT, phase TEXT, ordinal INTEGER, '
                               'PRIMARY KEY(candidate,phase,ordinal)) WITHOUT ROWID'})
    reasons['evidenceReadFailure'] = _blocked(lambda: freshness.collect(bad_evidence, cache={}))
    bad_evidence.close()
    good = _make_db(tmp / 'good.sqlite', ea=False)
    reasons['noConnection'] = _blocked(lambda: freshness.collect(None, cache={}))
    reasons['nonPositiveTimeout'] = _blocked(
        lambda: freshness.collect(good, cache={}, timeout_seconds=0))
    good.close()
    legacy = _make_db(tmp / 'legacy.sqlite', ea=False)
    _add_evidence(legacy, 'legacy', 'validation', 0, (1, 2))
    legacy.commit()
    legacy_report = freshness.collect(legacy, cache={})
    assert legacy_report['counts']['eaTablesPresent'] == []
    assert legacy_report['counts']['eaPairs'] == {
        'ea_sample': 0, 'ea_sample_link': 0, 'ea_holdout': 0}
    legacy.close()
    assert all(reasons.values())
    return dict(reasons=reasons, legacyWithoutEa='ok')


def evidence_checks(tmp):
    db = _make_db(tmp / 'evidence.sqlite', ea=False)
    _add_evidence(db, 'eliminated', 'validation', 0, (7, 8))       # eliminated candidate only
    _add_evidence(db, 'reference', 'validation', 0, (9, 10))       # no run row: pruned window
    _add_evidence(db, 'nominee', 'validation', 0, (100, 200))
    _add_evidence(db, 'reference', 'validation', 1, (200, 100))    # reversed order
    _add_evidence(db, 'eliminated', 'validation', 1, (7, 8))       # same pair, other ordinal
    db.commit()
    report = freshness.collect(db, cache={})
    pairs = report['pairs']
    assert isinstance(pairs, AbstractSet) and (7, 8) in pairs and (9, 10) in pairs
    assert (100, 200) in pairs and (200, 100) in pairs and (100, 200) != (200, 100)
    assert report['counts']['evidencePairs'] == 4
    db.close()
    return dict(eliminatedCandidateOnlyCollision=True, evictedRunOnlyEvidence=True,
                reversedPairsDistinct=True, distinctEvidencePairs=4)


def bank_checks(tmp):
    db = _make_db(tmp / 'banks.sqlite', ea=False)
    db.executemany('INSERT INTO run VALUES(?,?,?,?)',
                   [('eliminated', 'validation', i, '{}') for i in range(100)]
                   + [('eliminated', 'discovery', i, '{}') for i in range(8)])
    _add_evidence(db, 'eliminated', 'validation', 500, (5, 6))     # evidence max ordinal
    _set_meta(db, 'aggregate:eliminated:validation', {'n': 700})   # aggregate counter
    _set_meta(db, 'probeBanks', {'probe': 30000})
    _set_meta(db, 'fineTunePrograms', {'ft': {'budget': {'maxBank': 40000},
                                              'points': {'p': {'bank': 41000}}}})
    _set_meta(db, 'averageBanks', {'avg': 1000})
    _set_meta(db, 'breakthrough', {'encounters': {'e': {'plans': [
        {'status': 'pending', 'bank': 2000, 'parent': 'p', 'arms': [{'candidate': 'q'}]}]}}})
    _set_meta(db, 'mpRecoveryLedger',
              {'requests': {'r': {'status': 'pending', 'child': 'm', 'bank': 3000}}})
    db.commit()
    report = freshness.collect(db, cache={})
    pairs, limits = report['pairs'], report['counts']['bankCeilings']
    assert limits['validation'] >= 41000 and limits['discovery'] >= 8
    for phase, index in (('validation', 99), ('validation', 500), ('validation', 699),
                         ('validation', 1999), ('validation', 2999), ('validation', 29999),
                         ('validation', 39999), ('validation', 40999),
                         ('discovery', 7), ('discovery', 0)):
        assert _seed_pair(phase, index) in pairs, (phase, index)
    assert report['counts']['declaredCeilings'] == {
        'probeBanks': 30000, 'fineTunePrograms': 41000, 'averageBanks': 1000,
        'breakthrough': 2000, 'mpRecoveryLedger': 3000}
    db.close()
    return dict(bankCeilings=limits, globalRunAndEvidenceOrdinal=True,
                aggregateProbeFineTuneDeclared=True)


def ea_checks(tmp):
    db = _make_db(tmp / 'ea.sqlite', ea=True)
    db.execute("INSERT INTO ea_sample(sample_key,candidate_id,seed_a,seed_b,created_at) "
               "VALUES('s','c1',11,12,0.0)")
    db.execute("INSERT INTO ea_sample_link(experiment_id,candidate_id,seed_a,seed_b,sample_key,"
               "created_at) VALUES(1,'c1',13,14,'s',0.0)")
    db.execute("INSERT INTO ea_holdout(experiment_id,candidate_id,seed_a,seed_b) "
               "VALUES(1,'c1',15,16)")
    db.execute("INSERT INTO ea_sample(sample_key,candidate_id,seed_a,seed_b,created_at) "
               "VALUES('s2','c1',20,21,0.0)")
    db.execute("INSERT INTO ea_holdout(experiment_id,candidate_id,seed_a,seed_b) "
               "VALUES(1,'c2',21,20)")
    db.commit()
    report = freshness.collect(db, cache={})
    pairs = report['pairs']
    assert all(pair in pairs for pair in ((11, 12), (13, 14), (15, 16), (20, 21), (21, 20)))
    assert report['counts']['eaPairs'] == {
        'ea_sample': 2, 'ea_sample_link': 1, 'ea_holdout': 2}
    db.close()
    bad = _make_db(tmp / 'ea-bad.sqlite', ea=True)
    bad.execute("INSERT INTO ea_holdout(experiment_id,candidate_id,seed_a,seed_b) "
                "VALUES(1,'c',2147483648,0)")
    bad.commit()
    reason = _blocked(lambda: freshness.collect(bad, cache={}))
    assert 'ea_holdout' in reason
    bad.close()
    return dict(allEaRowsCovered=True, reversedEaDistinct=True, malformedEaSeedBlocked=True)


def malformed_checks(tmp):
    reasons = {}
    for name, raw in (('notJson', 'nope'), ('boolSeed', json.dumps([True, 5])),
                      ('outOfRange', json.dumps([1, 2147483648])),
                      ('wrongLength', json.dumps([1, 2, 3]))):
        db = _make_db(tmp / f'malformed-{name}.sqlite', ea=False)
        _add_evidence(db, 'c', 'validation', 0, (1, 2), raw=raw)
        db.commit()
        reasons[name] = _blocked(lambda db=db: freshness.collect(db, cache={}))
        db.close()
    bad_probe = _make_db(tmp / 'bad-probe.sqlite', ea=False)
    _set_meta(bad_probe, 'probeBanks', '[1, 2]')
    bad_probe.commit()
    reasons['probeNotMapping'] = _blocked(lambda: freshness.collect(bad_probe, cache={}))
    bad_probe.close()
    bad_aggregate = _make_db(tmp / 'bad-aggregate.sqlite', ea=False)
    _set_meta(bad_aggregate, 'aggregate:c:validation', {'n': 'lots'})
    bad_aggregate.commit()
    reasons['aggregateNotNumeric'] = _blocked(lambda: freshness.collect(bad_aggregate, cache={}))
    bad_aggregate.close()
    assert all(reasons.values())
    return dict(reasons=reasons, failClosed=True)


def no_write_checks(tmp):
    path = tmp / 'readonly.sqlite'
    db = _make_db(path, ea=True)
    _add_evidence(db, 'c', 'validation', 0, (1, 2))
    _set_meta(db, 'probeBanks', {'c': 64})
    db.commit()
    db.close()
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    connection = sqlite3.connect(str(path))
    total_before = connection.total_changes
    version_before = connection.execute('PRAGMA data_version').fetchone()[0]
    report = freshness.collect(connection, cache={}, revision='rev')
    total_after = connection.total_changes
    version_after = connection.execute('PRAGMA data_version').fetchone()[0]
    connection.close()
    after = hashlib.sha256(path.read_bytes()).hexdigest()
    sidecar = [suffix for suffix in ('-wal', '-journal', '-shm')
               if (path.parent / (path.name + suffix)).exists()]
    read_only = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
    read_only_report = freshness.collect(read_only, cache={}, revision='rev')
    read_only.close()
    assert total_before == total_after == 0
    assert version_before == version_after
    assert before == after and not sidecar
    assert read_only_report['pairs'] == report['pairs']
    return dict(totalChangesUnchanged=True, fileBytesUnchanged=True, noJournalFiles=True,
                readOnlyConnection='ok')


def cache_checks(tmp):
    path = tmp / 'cache.sqlite'
    db = _make_db(path, ea=False)
    _add_evidence(db, 'c1', 'validation', 0, (11, 22))
    db.commit()
    cache = {}
    first = freshness.collect(db, cache=cache, revision='rev-1')
    second = freshness.collect(db, cache=cache, revision='rev-1')
    assert second['provenance']['cached'] is True
    assert second['pairs'] is first['pairs']
    assert second['cacheKey'] == first['cacheKey'] and second['pairs'] == first['pairs']
    third = freshness.collect(db, cache=cache, revision='rev-2')
    assert third['cacheKey'] != first['cacheKey']
    other = sqlite3.connect(str(path))
    _add_evidence(other, 'c2', 'validation', 0, (33, 44))
    other.commit()
    other.close()
    fourth = freshness.collect(db, cache=cache, revision='rev-1')
    assert fourth['cacheKey'] != first['cacheKey'] and (33, 44) in fourth['pairs']
    second_connection = sqlite3.connect(str(path))
    fifth = freshness.collect(second_connection, cache=cache, revision='rev-1')
    assert fifth['cacheKey'] != fourth['cacheKey']
    second_connection.close()
    foreign_path = tmp / 'cache-foreign.sqlite'
    foreign_db = _make_db(foreign_path, ea=False)
    _add_evidence(foreign_db, 'z', 'validation', 0, (55, 66))
    foreign_db.commit()
    foreign = freshness.collect(foreign_db, cache=cache, revision='rev-1')
    assert foreign['cacheKey'] != fifth['cacheKey']
    assert (55, 66) in foreign['pairs'] and (11, 22) not in foreign['pairs']
    foreign_db.close()
    db.close()
    return dict(reusedOnIdenticalRead=True, invalidatedOnRevision=True, invalidatedOnNewData=True,
                invalidatedOnConnection=True, foreignDatabaseNotReused=True)


def deadline_checks(tmp):
    original = freshness._clock
    db = _make_db(tmp / 'deadline.sqlite', ea=False)
    _add_evidence(db, 'c', 'validation', 0, (1, 2))
    db.commit()
    calls = {'n': 0}

    def fake_clock():
        calls['n'] += 1
        return 0.0 if calls['n'] == 1 else 1000.0

    try:
        freshness._clock = fake_clock
        reason = _blocked(lambda: freshness.collect(db, cache={}, revision='r', timeout_seconds=30))
        # The default (bounded, realistic) initial-scan budget must ALSO fail closed on a real
        # deadline; a cold full scan that cannot finish never returns a partial set.
        calls['n'] = 0
        default_reason = _blocked(lambda: freshness.collect(db, cache={}, revision='r'))
    finally:
        freshness._clock = original
        db.close()
    assert 'deadline' in reason and 'deadline' in default_reason
    return dict(deadlineFailClosed=True, boundedDefaultDeadlineFailClosed=True, reason=reason,
                defaultReason=default_reason)


def coverage_checks(tmp):
    db = _make_db(tmp / 'coverage.sqlite', ea=False)
    _add_evidence(db, 'c', 'validation', 0, (1, 2))
    db.commit()
    report = freshness.collect(db, cache={})
    db.close()
    coverage = report['counts'].get('coverage') or {}
    assert coverage.get('scope') == 'established-seed-history'
    assert 'evidence.seeds' in coverage.get('sources', [])
    note = coverage.get('note', '')
    assert 'pruned' in note and 'NOT' in note
    assert freshness.DEFAULT_TIMEOUT_SECONDS == 120.0
    return dict(scope=coverage.get('scope'),
                boundedDefaultScanSeconds=freshness.DEFAULT_TIMEOUT_SECONDS,
                prunedGapsNotClaimedFresh=True)


def determinism_checks(tmp):
    first_db = _make_db(tmp / 'determinism-a.sqlite', ea=False)
    _add_evidence(first_db, 'c', 'validation', 0, (7, 8))
    first_db.commit()
    second_db = _make_db(tmp / 'determinism-b.sqlite', ea=False)
    _add_evidence(second_db, 'c', 'validation', 0, (7, 8))
    second_db.commit()
    left = freshness.collect(first_db, cache={}, revision='same')
    right = freshness.collect(second_db, cache={}, revision='same')
    assert left['pairs'] == right['pairs']
    assert _seed_pair('discovery', 0) in left['pairs']
    assert (7, 8) in left['pairs'] and (8, 7) not in left['pairs']
    first_db.close()
    second_db.close()
    return dict(identicalHistorySamePairs=True, orderedSeedOrdersDistinct=True)


def packed_pair_set_checks():
    pairs = freshness.SeedPairSet()
    pairs._add(0, freshness.INT31)
    pairs._add(freshness.INT31, 0)
    pairs._add(42, 42)
    expected = {(0, freshness.INT31), (freshness.INT31, 0), (42, 42)}
    assert len(pairs) == 3 and all(pair in pairs for pair in expected)
    assert (0, freshness.INT31) in pairs and (freshness.INT31, 0) in pairs
    assert (1, 2) not in pairs and (True, 42) not in pairs
    assert pairs == expected and expected == pairs and set(pairs) == expected
    return dict(readOnlySetContract=True, orderedEncoding=True, maximumSeeds=True,
                builtinSetEquality=True)


def main():
    tmp = Path(__file__).resolve().parent / 'tmp' / ('seed-freshness-%d' % os.getpid())
    os.makedirs(tmp, exist_ok=True)
    try:
        report = dict(
            module='strategy_seed_freshness',
            api='collect(connection, *, cache=None, revision=None, timeout_seconds=120) -> '
                '{pairs: read-only Set[(a,b)], counts, cacheKey, provenance}',
            schema=schema_checks(tmp),
            evidence=evidence_checks(tmp),
            banks=bank_checks(tmp),
            ea=ea_checks(tmp),
            malformed=malformed_checks(tmp),
            writes=no_write_checks(tmp),
            cache=cache_checks(tmp),
            deadline=deadline_checks(tmp),
            coverage=coverage_checks(tmp),
            determinism=determinism_checks(tmp),
            packedPairSet=packed_pair_set_checks(),
            limits=[
                'Only SELECT/DISTINCT/aggregate reads: no DB writes and no PRAGMA changes.',
                'Legacy tables candidate/run/evidence/meta are required; ea_* tables are optional.',
                'A scoped SQLite progress handler bounds one long statement and is removed after.',
                'Pairs are ordered: (a,b) and (b,a) are distinct forbidden pairs.',
                'The freshness claim is limited to established coverage: recorded evidence, '
                'enumerated deterministic banks and ea_* rows; pruned/never-recorded seed gaps '
                'are not claimed fresh.',
                'The default initial scan budget is the bounded, realistic 120 s.',
            ])
        print(json.dumps(report))
    finally:
        for name in os.listdir(tmp):
            try:
                os.remove(tmp / name)
            except OSError:
                pass
        try:
            tmp.rmdir()
        except OSError:
            pass
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
