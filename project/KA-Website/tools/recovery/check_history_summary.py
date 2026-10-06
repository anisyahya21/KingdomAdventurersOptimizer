"""Deterministic checks for `strategy_history_summary`.

Every case is a hand-built tiny source library (no simulator, no real game data, no live DB), so the
checks pin the summary contract without a real import:

* a repeated one-build large count stays compact: one small summary row and one compressed block;
* the stored moments are the exact weighted ``strategy_outcomes.summarize`` (quantiles/mean/stdev/
  thresholds), verified against a small shared canonical oracle; a 5M-count distribution is
  summarised with no per-sample allocation;
* the compressed seed-outcome block round-trips exactly (count + sha256), its histogram is derivable
  and stable vs jackpot same-mean cases differ in variance while sharing a mean; corruption refuses;
* overlap deduplicates, a differing same-seed reward becomes conflict/unknown (no winner picked),
  and evidence partial coverage never hides an unmeasured retained run;
* an alias identity deduplicates by canonical identity; an unknown engine never merges;
* pruned candidates are recovered from ``predictor_history``; original per-candidate source and
  lineage are preserved instead of being relabelled with the source's ownership;
* the source is byte-immutable, an existing destination/unauthorised import is refused, the new
  campaign stays at ``totalRuns`` 0, and old rankings/scores are not imported;
* known custom seeds are excludable (not blocking), missing seeds are incomplete coverage, and
  malformed seed identity fails closed;
* the pure loader returns the legacy shape and refuses an unproven engine/policy scope.

    python check_history_summary.py
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import sys
import tempfile
import tracemalloc
import zlib
from pathlib import Path

_HERE = Path(__file__).resolve().parent
# The sandbox only permits writes inside the workspace, so keep every temp artefact inside it.
_TMP = _HERE / '.tmp-history-summary'
_TMP.mkdir(parents=True, exist_ok=True)
tempfile.tempdir = str(_TMP)
for _env in ('TMPDIR', 'TEMP', 'TMP'):
    os.environ[_env] = str(_TMP)

sys.path.insert(0, str(_HERE))

import strategy_build_domain as domain  # noqa: E402
import strategy_evidence as evidence_service  # noqa: E402
import strategy_history_summary as summary  # noqa: E402
import strategy_outcomes as outcomes  # noqa: E402

SCHEMA = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE candidate(id TEXT PRIMARY KEY, scenario TEXT NOT NULL, label TEXT NOT NULL,
    source TEXT NOT NULL, stats TEXT NOT NULL, created INTEGER NOT NULL);
CREATE TABLE candidate_meta(id TEXT PRIMARY KEY, encounter INTEGER NOT NULL,
    defeat INTEGER NOT NULL, region TEXT NOT NULL DEFAULT '');
CREATE TABLE lineage(candidate TEXT PRIMARY KEY, parent TEXT, root TEXT NOT NULL,
    depth INTEGER NOT NULL, operation TEXT, target TEXT, change TEXT, source TEXT,
    encounterId INTEGER, proposal INTEGER, searchSpaceVersion INTEGER, created INTEGER NOT NULL);
CREATE TABLE run(candidate TEXT NOT NULL, phase TEXT NOT NULL, ordinal INTEGER NOT NULL,
    result TEXT NOT NULL, PRIMARY KEY(candidate,phase,ordinal));
CREATE TABLE evidence(candidate TEXT NOT NULL, phase TEXT NOT NULL, ordinal INTEGER NOT NULL,
    seeds TEXT, verdict INTEGER, censored INTEGER NOT NULL DEFAULT 0, prizeCallbacks INTEGER,
    awardedChests INTEGER, awardedBasis TEXT, pendingChests INTEGER,
    PRIMARY KEY(candidate,phase,ordinal));
CREATE TABLE archive(cell TEXT PRIMARY KEY, candidate TEXT NOT NULL, quality TEXT NOT NULL);
CREATE TABLE predictor_history(candidate TEXT PRIMARY KEY, scenario_zlib BLOB NOT NULL,
    root TEXT NOT NULL, encounter INTEGER NOT NULL, created INTEGER NOT NULL,
    discovery TEXT, validation TEXT, pruned INTEGER NOT NULL);
CREATE TABLE rebel_break(ledger_id INTEGER PRIMARY KEY, detail TEXT);
"""

CONSERVED_OBSERVATION_KEYS = (
    'candidateId', 'window', 'compatibility', 'meanEarned', 'partialMean', 'resolved', 'samples',
    'total', 'unknownCount', 'standardError', 'reliability', 'lossFrequency', 'median', 'features',
    'eligibleForRecommendation', 'adviserEligible', 'evidenceClass', 'provenanceClass',
    'independentConfirmation', 'confirmationEligible', 'sourcePhase', 'measurementWindow',
    'sourcePolicy', 'sourceEncounter', 'mechanicsRevision', 'counts', 'ordinals', 'summary',
    'provenance',
)

MOMENT_KEYS = ('partialMean', 'meanEarned', 'median', 'min', 'max', 'stdev', 'quantiles',
               'byStatus', 'byBasis', 'lossFrequency', 'zeroFrequency', 'denominators', 'thresholds',
               'diagnostics')


def _provenance(digest, marker):
    return dict(digest=digest, count=1,
                files={'tools/recovery/combat_shared_controllers.py': marker})


def _scenario(encounter, defeat, **policy):
    body = dict(encounterId=encounter, defeatCount=defeat, mathSeed=1, libSeed=2)
    body.update(policy)
    return body


def _cid(encounter, defeat, **policy):
    """The canonical identity a real source would store as the candidate id."""
    return domain.identity(_scenario(encounter, defeat, **policy))


def _ev(candidate, ordinal, seeds, verdict, awarded=None, basis=None, pending=None,
        censored=0, phase='validation'):
    return (candidate, phase, ordinal, None if seeds is None else json.dumps(list(seeds)),
            verdict, censored, 0, awarded, basis, pending)


def _run(candidate, ordinal, seeds, verdict, awarded=None, basis=None, censored=0,
         phase='validation'):
    body = dict(verdict=verdict, seeds=list(seeds), censored=censored)
    if awarded is not None or basis is not None:
        body['rewardOutcome'] = dict(awardedChests=awarded, awardedBasis=basis)
    return (candidate, phase, ordinal, json.dumps(body))


def _write_source(path, *, digest='a' * 64, builds=(), evidence=(), runs=(), predictor=(),
                  lineage_rows=(), meta_rows=(), marker='m0', total_runs=13, pruned=None,
                  with_provenance=True):
    db = sqlite3.connect(str(path))
    try:
        db.executescript(SCHEMA)
        db.execute('INSERT INTO meta VALUES(?,?)', ('schema', json.dumps(1)))
        db.execute('INSERT INTO meta VALUES(?,?)', ('objectiveVersion', json.dumps(3)))
        db.execute('INSERT INTO meta VALUES(?,?)', ('searchSpaceVersion', json.dumps(4)))
        db.execute('INSERT INTO meta VALUES(?,?)', ('totalRuns', json.dumps(total_runs)))
        db.execute('INSERT INTO meta VALUES(?,?)', ('timedRuns', json.dumps(total_runs)))
        if with_provenance:
            db.execute('INSERT INTO meta VALUES(?,?)',
                       ('provenance', json.dumps(_provenance(digest, marker))))
        if pruned is not None:
            db.execute('INSERT INTO meta VALUES(?,?)', ('prunedOrdinals', json.dumps(pruned)))
        for key, value in meta_rows:
            db.execute('INSERT INTO meta VALUES(?,?)', (key, json.dumps(value)))
        for build in builds:
            scenario = build.get('scenario') or _scenario(build['encounter'], build['defeat'],
                                                          **build.get('policy', {}))
            db.execute('INSERT INTO candidate VALUES(?,?,?,?,?,?)',
                       (build['id'], json.dumps(scenario), build.get('label', build['id']),
                        build.get('origin_source', 'fixture'), '{}', build.get('created', 0)))
            db.execute('INSERT INTO candidate_meta VALUES(?,?,?,?)',
                       (build['id'], build['encounter'], build['defeat'], build.get('region', '')))
        db.executemany('INSERT INTO lineage VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', list(lineage_rows))
        db.executemany('INSERT INTO evidence VALUES(?,?,?,?,?,?,?,?,?,?)', list(evidence))
        db.executemany('INSERT INTO run VALUES(?,?,?,?)', list(runs))
        for entry in predictor:
            db.execute('INSERT INTO predictor_history VALUES(?,?,?,?,?,?,?,?)',
                       (entry['id'], zlib.compress(json.dumps(entry['scenario']).encode('utf-8'), 1),
                        entry.get('root', entry['id']), entry['encounter'], entry.get('created', 0),
                        entry.get('discovery'), entry.get('validation'), entry.get('pruned', 0)))
        db.commit()
    finally:
        db.close()


def _summary_rows(db_path):
    db = sqlite3.connect(str(db_path))
    db.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in db.execute('SELECT * FROM history_build_summary'
                                                ' ORDER BY candidate_id,phase')]
    finally:
        db.close()


def _block_rows(db_path):
    db = sqlite3.connect(str(db_path))
    db.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in db.execute('SELECT * FROM history_seed_block'
                                                ' ORDER BY candidate_id')]
    finally:
        db.close()


def _column(db_path, sql, args=()):
    db = sqlite3.connect(str(db_path))
    try:
        row = db.execute(sql, args).fetchone()
        return row[0] if row else None
    finally:
        db.close()


def _rows(db_path, sql, args=()):
    db = sqlite3.connect(str(db_path))
    try:
        return [tuple(row) for row in db.execute(sql, args)]
    finally:
        db.close()


def _canonical_summary(evidence_rows, policy):
    decoded = [evidence_service.decode(*row[3:]) for row in evidence_rows]
    return outcomes.summarize([outcomes.outcome(row, policy=policy) for row in decoded],
                              thresholds=summary.REWARD_THRESHOLDS)


def _close(left, right):
    if isinstance(left, dict) and isinstance(right, dict):
        if set(left) != set(right):
            return False
        return all(_close(left[key], right[key]) for key in left)
    if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
        return len(left) == len(right) and all(_close(a, b) for a, b in zip(left, right))
    if isinstance(left, bool) or isinstance(right, bool):
        return left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return math.isclose(left, right, rel_tol=1e-9, abs_tol=1e-9)
    return left == right


def _assert_moments(moments, canonical):
    for key in MOMENT_KEYS:
        assert _close(moments[key], canonical[key]), (key, moments[key], canonical[key])


def compactness(folder):
    """One build measured 200 distinct pairs twice stays one small summary row and one block."""
    from strategy_optimizer import seed_pair
    source = folder / 'compact-source.sqlite'
    dest = folder / 'compact-summary.sqlite'
    cid = _cid(19, 0, finishPolicy='on-verdict')
    build = dict(id=cid, encounter=19, defeat=0, policy={'finishPolicy': 'on-verdict'})
    pairs = [seed_pair('validation', index) for index in range(200)]
    evidence = [_ev(cid, ordinal, pair, 1, awarded=3, basis='reward-entitlement-certificate')
                for ordinal, pair in enumerate(pairs)]
    evidence += [_ev(cid, 200 + ordinal, pair, 1, awarded=3,
                     basis='reward-entitlement-certificate')
                 for ordinal, pair in enumerate(pairs)]
    _write_source(source, builds=[build], evidence=evidence)
    report = summary.build_library([dict(path=str(source), label='s', ownership='u')], dest,
                                   authorised=True, batch_size=64)
    assert report['summaries'] == 1 and report['seedBlocks'] == 1, report
    rows = _summary_rows(dest)
    assert len(rows) == 1
    row = rows[0]
    assert row['sample_count'] == 200 and row['duplicate_count'] == 200, row
    assert _column(dest, 'SELECT COUNT(*) FROM evidence') == 0
    assert _column(dest, "SELECT value FROM meta WHERE key='totalRuns'") == '0'
    canonical = _canonical_summary(sorted(evidence, key=lambda item: item[2]),
                                   {'finishPolicy': 'on-verdict'})
    assert row['mean_earned'] == canonical['meanEarned'] == 3.0
    block = _block_rows(dest)[0]
    assert block['record_count'] == 200 and block['observation_count'] == 400, block
    assert block['compressed_bytes'] < block['uncompressed_bytes']
    return dict(rows=len(rows), samples=row['sample_count'], duplicates=row['duplicate_count'],
                destBytes=dest.stat().st_size)


def weighted_large_count(folder):
    """A 5M-count distribution summarises exactly with no allocation proportional to the count."""
    dist = {(3.0, outcomes.CERTIFIED): 5_000_000, (0.0, outcomes.LOSS): 1,
            (None, outcomes.UNRESOLVED): 7}
    tracemalloc.start()
    report = summary._weighted_summary(dist, {'certificate': 5_000_000}, thresholds=(1, 3))
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert peak < 1_000_000, peak
    total = 5_000_008
    assert report['total'] == total and report['numericCount'] == 5_000_001
    assert report['meanEarned'] is None  # one unresolved reading blocks a full claim
    assert math.isclose(report['partialMean'], 15_000_000 / 5_000_001.0)
    assert report['lossCount'] == 1 and report['unresolvedCount'] == 7
    assert math.isclose(report['lossFrequency'], 1 / total)
    assert math.isclose(report['zeroFrequency'], 1 / 5_000_001.0)
    assert report['thresholds']['3']['count'] == 5_000_000
    assert report['quantiles']['p50'] == 3.0 and report['quantiles']['p100'] == 3.0
    complete = summary._weighted_summary({(2.5, outcomes.CERTIFIED): 5_000_000})
    assert complete['meanEarned'] == 2.5 and complete['stdev'] == 0.0
    return dict(peakBytes=peak, total=report['total'])


def seed_block_roundtrip(folder):
    """Blocks round-trip exactly; stable vs jackpot share a mean but differ in variance/tails."""
    from strategy_optimizer import seed_pair
    source = folder / 'blocks-source.sqlite'
    dest = folder / 'blocks-summary.sqlite'
    stable = _cid(19, 0, finishPolicy='on-verdict')
    jackpot = _cid(20, 0, finishPolicy='on-verdict')
    builds = [dict(id=stable, encounter=19, defeat=0, policy={'finishPolicy': 'on-verdict'}),
              dict(id=jackpot, encounter=20, defeat=0, policy={'finishPolicy': 'on-verdict'})]
    pairs = [seed_pair('validation', index) for index in range(4)]
    evidence = [_ev(stable, index, pair, 1, awarded=3, basis='reward-entitlement-certificate')
                for index, pair in enumerate(pairs)]
    evidence += [_ev(jackpot, index, pair, 2) for index, pair in enumerate(pairs[:3])]
    evidence += [_ev(jackpot, 3, pairs[3], 1, awarded=12,
                     basis='reward-entitlement-certificate')]
    _write_source(source, builds=builds, evidence=evidence)
    summary.build_library([dict(path=str(source), label='s', ownership='u')], dest,
                          authorised=True, batch_size=8)
    blocks = {row['candidate_id']: row for row in _block_rows(dest)}
    assert set(blocks) == {stable, jackpot}, set(blocks)
    for row in blocks.values():
        records = summary._decode_block(row['block_zlib'], expected_count=row['record_count'],
                                        expected_sha256=row['plain_sha256'])
        assert len(records) == row['record_count']
        assert summary._histogram_from_records(records) == json.loads(row['histogram_json'])
        blob, sha, count, size = summary._encode_block(records)
        assert sha == row['plain_sha256'] and count == row['record_count'] and size == \
            row['uncompressed_bytes'], row['candidate_id']
    rows = {row['candidate_id']: row for row in _summary_rows(dest)}
    stable_moments = json.loads(rows[stable]['moments_json'])
    jackpot_moments = json.loads(rows[jackpot]['moments_json'])
    assert rows[stable]['mean_earned'] == rows[jackpot]['mean_earned'] == 3.0
    assert stable_moments['stdev'] == 0 and jackpot_moments['stdev'] > 0
    assert stable_moments['diagnostics']['jackpotSensitive'] is False
    assert jackpot_moments['diagnostics']['jackpotSensitive'] is True
    assert json.loads(rows[jackpot]['reward_histogram_json']) == {'0': 3, '12': 1}
    return dict(blocks=len(blocks), stableStdev=stable_moments['stdev'],
                jackpotStdev=jackpot_moments['stdev'])


def moments_oracle(folder):
    """The persisted moments equal the canonical summariser over the same fixture rows."""
    from strategy_optimizer import seed_pair
    source = folder / 'oracle-source.sqlite'
    dest = folder / 'oracle-summary.sqlite'
    cid = _cid(19, 0, finishPolicy='on-verdict')
    build = dict(id=cid, encounter=19, defeat=0, policy={'finishPolicy': 'on-verdict'})
    pairs = [seed_pair('validation', index) for index in range(6)]
    evidence = [
        _ev(cid, 0, pairs[0], 2),                                               # loss -> 0
        _ev(cid, 1, pairs[1], 1, awarded=1, basis='reward-entitlement-certificate'),
        _ev(cid, 2, pairs[2], 1, awarded=1, basis='reward-entitlement-certificate'),
        _ev(cid, 3, pairs[3], 1, awarded=2, basis='reward-entitlement-certificate'),
        _ev(cid, 4, pairs[4], 1, awarded=40, basis='reward-entitlement-certificate'),
        _ev(cid, 5, pairs[5], 1),                                               # unknown win
    ]
    _write_source(source, builds=[build], evidence=evidence)
    summary.build_library([dict(path=str(source), label='s', ownership='u')], dest,
                          authorised=True, batch_size=4)
    row = _summary_rows(dest)[0]
    moments = json.loads(row['moments_json'])
    canonical = _canonical_summary(evidence, {'finishPolicy': 'on-verdict'})
    _assert_moments(moments, canonical)
    assert row['mean_earned'] is None and row['partial_mean_earned'] == canonical['partialMean']
    assert '0' in json.loads(row['reward_histogram_json'])
    return dict(partial=row['partial_mean_earned'], unknown=row['unknown_count'])


def overlap_and_conflict(folder):
    """Shared identical measurements dedupe; a differing reward becomes unknown/conflict."""
    from strategy_optimizer import seed_pair
    pairs = [seed_pair('validation', index) for index in range(3)]
    cid = _cid(19, 0, finishPolicy='on-verdict')
    build = dict(id=cid, encounter=19, defeat=0, policy={'finishPolicy': 'on-verdict'})
    source_a = folder / 'a.sqlite'
    source_b = folder / 'b.sqlite'
    evidence = [_ev(cid, i, pair, 1, awarded=2, basis='reward-entitlement-certificate')
                for i, pair in enumerate(pairs)]
    _write_source(source_a, digest='b' * 64, builds=[build], evidence=evidence)
    _write_source(source_b, digest='b' * 64, builds=[build], evidence=evidence)
    dest = folder / 'merged.sqlite'
    summary.build_library([dict(path=str(source_a), label='a', ownership='student9'),
                           dict(path=str(source_b), label='b', ownership='community')], dest,
                          authorised=True, batch_size=8)
    row = _summary_rows(dest)[0]
    assert row['status'] == summary.STATUS_MEASURED and row['duplicate_count'] == 3, row
    assert row['sample_count'] == 3 and row['mean_earned'] == 2.0

    conflict_a = folder / 'ca.sqlite'
    conflict_b = folder / 'cb.sqlite'
    _write_source(conflict_a, digest='c' * 64, builds=[build],
                  evidence=[_ev(cid, 0, pairs[0], 1, awarded=2,
                                basis='reward-entitlement-certificate')])
    _write_source(conflict_b, digest='c' * 64, builds=[build],
                  evidence=[_ev(cid, 0, pairs[0], 1, awarded=5,
                                basis='reward-entitlement-certificate')])
    conflict_dest = folder / 'conflict.sqlite'
    summary.build_library([dict(path=str(conflict_a), label='ca', ownership='student9'),
                           dict(path=str(conflict_b), label='cb', ownership='community')],
                          conflict_dest, authorised=True, batch_size=8)
    clash = _summary_rows(conflict_dest)[0]
    assert clash['status'] == summary.STATUS_CONFLICT, clash
    assert clash['mean_earned'] is None and clash['partial_mean_earned'] is None
    assert clash['conflicting_rewards'] >= 1
    block = _block_rows(conflict_dest)[0]
    records = summary._decode_block(block['block_zlib'], expected_count=block['record_count'],
                                    expected_sha256=block['plain_sha256'])
    assert len(records[0]['observations']) == 2, records[0]
    return dict(duplicates=row['duplicate_count'], conflictStatus=clash['status'],
                conflictObservations=len(records[0]['observations']))


def partial_evidence_run_union(folder):
    """Evidence only hides the run rows it actually covers; run-only measurements are retained."""
    from strategy_optimizer import seed_pair
    source = folder / 'union-source.sqlite'
    dest = folder / 'union-summary.sqlite'
    cid = _cid(19, 0, finishPolicy='on-verdict')
    run_only = _cid(21, 0, finishPolicy='on-verdict')
    builds = [dict(id=cid, encounter=19, defeat=0, policy={'finishPolicy': 'on-verdict'}),
              dict(id=run_only, encounter=21, defeat=0, policy={'finishPolicy': 'on-verdict'})]
    pairs = [seed_pair('validation', index) for index in range(5)]
    evidence = [_ev(cid, 0, pairs[0], 1, awarded=1, basis='reward-entitlement-certificate'),
                _ev(cid, 1, pairs[1], 1, awarded=1, basis='reward-entitlement-certificate')]
    runs = [_run(cid, index, pairs[index], 1, awarded=1,
                 basis='reward-entitlement-certificate') for index in range(4)]
    runs += [_run(run_only, index, pairs[index], 1, awarded=1,
                  basis='reward-entitlement-certificate') for index in range(5)]
    _write_source(source, builds=builds, evidence=evidence, runs=runs)
    summary.build_library([dict(path=str(source), label='s', ownership='u')], dest,
                          authorised=True, batch_size=4)
    rows = {row['candidate_id']: row for row in _summary_rows(dest)}
    assert rows[cid]['sample_count'] == 4, rows[cid]     # p0,p1 evidence + p2,p3 run-only
    assert rows[run_only]['sample_count'] == 5, rows[run_only]
    return dict(unionSamples=rows[cid]['sample_count'], runOnlySamples=rows[run_only]['sample_count'])


def groups_split(folder):
    """Policy, encounter and engine-compat differences stay in separate summaries."""
    source = folder / 'groups.sqlite'
    dest = folder / 'groups-summary.sqlite'
    on = _cid(19, 0, finishPolicy='on-verdict')
    cert = _cid(19, 0, finishPolicy='certified-only')
    enc = _cid(20, 0, finishPolicy='on-verdict')
    builds = [
        dict(id=on, encounter=19, defeat=0, policy={'finishPolicy': 'on-verdict'}),
        dict(id=cert, encounter=19, defeat=0, policy={'finishPolicy': 'certified-only'}),
        dict(id=enc, encounter=20, defeat=0, policy={'finishPolicy': 'on-verdict'}),
    ]
    evidence = [_ev(build['id'], 0, [11, 12], 1, awarded=1,
                    basis='reward-entitlement-certificate') for build in builds]
    _write_source(source, digest='d' * 64, builds=builds, evidence=evidence)
    other = folder / 'groups-other-engine.sqlite'
    _write_source(other, digest='e' * 64, builds=[builds[0]], marker='other',
                  evidence=[_ev(on, 0, [13, 14], 1, awarded=1,
                                basis='reward-entitlement-certificate')])
    summary.build_library([dict(path=str(source), label='s', ownership='u'),
                           dict(path=str(other), label='o', ownership='u')], dest,
                          authorised=True, batch_size=8)
    rows = _summary_rows(dest)
    assert len(rows) == 4, [row['candidate_id'] for row in rows]
    assert len({row['policy_key'] for row in rows}) == 2
    assert len({row['encounter_id'] for row in rows}) == 2
    assert len({row['engine_compat_group'] for row in rows}) == 2
    return dict(rows=len(rows))


def unknown_not_zero(folder):
    """An unresolved win keeps NULL and is counted unknown; a loss is a resolved zero."""
    source = folder / 'unknown.sqlite'
    dest = folder / 'unknown-summary.sqlite'
    cid = _cid(19, 0)
    build = dict(id=cid, encounter=19, defeat=0)  # no finishPolicy -> no promotion
    evidence = [
        _ev(cid, 0, [21, 22], 1, awarded=4, basis='reward-entitlement-certificate'),
        _ev(cid, 1, [23, 24], 1),           # win, no certificate, no policy -> unknown
        _ev(cid, 2, [25, 26], 2),           # terminal loss -> resolved zero
    ]
    _write_source(source, digest='f' * 64, builds=[build], evidence=evidence)
    summary.build_library([dict(path=str(source), label='s', ownership='u')], dest,
                          authorised=True, batch_size=4)
    row = _summary_rows(dest)[0]
    assert row['unknown_count'] == 1, row
    assert row['mean_earned'] is None
    assert row['partial_mean_earned'] == 2.0  # (4 + 0) / 2 resolved readings
    assert row['loss_count'] == 1 and row['resolved_count'] == 2
    canonical = _canonical_summary(sorted(evidence, key=lambda item: item[2]), {})
    assert canonical['meanEarned'] is None and canonical['partialMean'] == 2.0
    assert json.loads(row['reward_histogram_json'])['0'] == 1
    return dict(unknown=row['unknown_count'], partial=row['partial_mean_earned'])


def immutable_and_existing_dest(folder):
    """A source is never written, an existing destination is refused, and --authorised is gated."""
    source = folder / 'immut.sqlite'
    cid = _cid(19, 0, finishPolicy='on-verdict')
    build = dict(id=cid, encounter=19, defeat=0, policy={'finishPolicy': 'on-verdict'})
    _write_source(source, digest='2' * 64, builds=[build],
                  evidence=[_ev(cid, 0, [41, 42], 1, awarded=1,
                                basis='reward-entitlement-certificate')])
    before = summary._sha256_file(source)
    existing = folder / 'existing.sqlite'
    existing.write_bytes(b'not-a-library')
    try:
        summary.build_library([dict(path=str(source), label='s', ownership='u')], existing,
                              authorised=True)
        raise AssertionError('an existing destination must be refused')
    except FileExistsError:
        pass
    assert existing.read_bytes() == b'not-a-library'
    gated = folder / 'gated.sqlite'
    try:
        summary.build_library([dict(path=str(source), label='s', ownership='u')], gated,
                              authorised=False)
        raise AssertionError('an unauthorised import must be refused')
    except summary.NotAuthorised:
        pass
    assert not gated.exists() and not (folder / 'gated.sqlite.partial').exists()
    dest = folder / 'immut-summary.sqlite'
    summary.build_library([dict(path=str(source), label='s', ownership='u')], dest,
                          authorised=True, batch_size=4)
    assert summary._sha256_file(source) == before
    assert _column(dest, 'SELECT COUNT(*) FROM history_seed_block') == 1
    return dict(sourceUnchanged=True, refusedExisting=True, gated=True)


def fresh_counters(folder):
    """The new campaign stays at zero; imported history counters are kept separate."""
    from strategy_optimizer import seed_pair
    source = folder / 'counters.sqlite'
    dest = folder / 'counters-summary.sqlite'
    cid = _cid(19, 0, finishPolicy='on-verdict')
    build = dict(id=cid, encounter=19, defeat=0, policy={'finishPolicy': 'on-verdict'})
    evidence = [_ev(cid, index, seed_pair('validation', index), 1, awarded=1,
                    basis='reward-entitlement-certificate') for index in range(5)]
    runs = [_run(cid, index, seed_pair('validation', index), 1, awarded=1,
                 basis='reward-entitlement-certificate') for index in range(2)]
    _write_source(source, digest='3' * 64, builds=[build], evidence=evidence, runs=runs,
                  total_runs=13)
    summary.build_library([dict(path=str(source), label='s', ownership='student9')], dest,
                          authorised=True, batch_size=4)
    status = summary.history_status(dest)
    assert status['currentTotalRuns'] == 0, status
    assert status['importedSources'] == 1
    assert status['importedRuns'] >= 1
    assert status['importedEvidence'] >= 5
    assert status['historicalConfirmed'] == 0
    assert status['candidates'] == 1 and status['seedBlocks'] == 1
    assert status['lifetimeCountersAreNotUniqueRuns'] is True
    assert status['independentConfirmation'] is False
    assert _column(dest, "SELECT value FROM meta WHERE key='totalRuns'") == '0'
    return status


def alias_identity(folder):
    """Two stored ids for one scenario deduplicate by canonical identity and are reported."""
    from strategy_optimizer import seed_pair
    source = folder / 'alias.sqlite'
    canonical = _cid(19, 0, finishPolicy='on-verdict')
    scenario = _scenario(19, 0, finishPolicy='on-verdict')
    builds = [dict(id='alias-a', encounter=19, defeat=0, scenario=scenario),
              dict(id='alias-b', encounter=19, defeat=0, scenario=scenario)]
    pairs = [seed_pair('validation', index) for index in range(2)]
    evidence = [_ev('alias-a', 0, pairs[0], 1, awarded=2,
                    basis='reward-entitlement-certificate'),
                _ev('alias-b', 0, pairs[1], 1, awarded=2,
                    basis='reward-entitlement-certificate')]
    dest = folder / 'alias-summary.sqlite'
    _write_source(source, builds=builds, evidence=evidence)
    summary.build_library([dict(path=str(source), label='s', ownership='student9')], dest,
                          authorised=True, batch_size=4)
    rows = _summary_rows(dest)
    assert len(rows) == 1 and rows[0]['candidate_id'] == canonical, rows
    assert rows[0]['sample_count'] == 2, rows[0]
    status = summary.history_status(dest)
    assert status['aliases'] == 2 and status['candidates'] == 1, status
    assert _column(dest, "SELECT COUNT(*) FROM candidate") == 1
    aliases = _rows(dest, 'SELECT candidate_id,alias_of FROM history_candidate ORDER BY stored_id')
    assert all(row[1] for row in aliases), aliases
    return dict(aliases=status['aliases'], summaries=len(rows))


def provenance_unknown(folder):
    """A source with no stored provenance stays an unknown engine group and never merges."""
    source = folder / 'noprov.sqlite'
    dest = folder / 'noprov-summary.sqlite'
    cid = _cid(19, 0, finishPolicy='on-verdict')
    build = dict(id=cid, encounter=19, defeat=0, policy={'finishPolicy': 'on-verdict'})
    _write_source(source, builds=[build], with_provenance=False, marker='m0',
                  evidence=[_ev(cid, 0, [71, 72], 1, awarded=1,
                                basis='reward-entitlement-certificate')])
    summary.build_library([dict(path=str(source), label='s', ownership='u')], dest,
                          authorised=True, batch_size=4)
    row = _summary_rows(dest)[0]
    assert row['status'] == summary.STATUS_UNCONFIRMED, row
    assert row['engine_compat_group'].startswith('unknown:'), row
    result = summary.load_observations(dest, [cid],
                                       encounter={'encounterId': 19, 'defeatCount': 0},
                                       policy={'finishPolicy': 'on-verdict'},
                                       engine_revision='anything')
    assert not result['observations']
    return dict(status=row['status'], engineGroup=row['engine_compat_group'])


def pruned_scenario_recovery(folder):
    """A pruned candidate's exact scenario and lineage are recovered from predictor_history."""
    cid = _cid(31, 2, finishPolicy='on-verdict')
    scenario = _scenario(31, 2, finishPolicy='on-verdict')
    source = folder / 'pruned.sqlite'
    dest = folder / 'pruned-summary.sqlite'
    _write_source(source, builds=[], predictor=[dict(id=cid, scenario=scenario, root=cid,
                                                     encounter=31, created=99,
                                                     discovery=json.dumps({'n': 4}),
                                                     validation=None, pruned=1)])
    summary.build_library([dict(path=str(source), label='s', ownership='student9')], dest,
                          authorised=True, batch_size=4)
    assert _column(dest, 'SELECT COUNT(*) FROM candidate WHERE id=?', (cid,)) == 1
    lineage = _rows(dest, 'SELECT candidate,root FROM lineage WHERE candidate=?', (cid,))
    assert lineage == [(cid, cid)], lineage
    recovered = _rows(dest, 'SELECT candidate_id,recovered_from FROM history_candidate')
    assert (cid, 'predictor_history') in recovered, recovered
    omissions = dict(_rows(dest, 'SELECT kind,SUM(count) FROM history_omission GROUP BY kind'))
    assert omissions.get('predictor-aggregate-unseeded') == 1, omissions
    assert summary.history_status(dest)['currentTotalRuns'] == 0
    return dict(candidateRecovered=True, omissions=len(omissions))


def aggregate_only(folder):
    """A pruned aggregate is kept source-attributed, minus rankings, without inventing a claim."""
    source = folder / 'aggregate.sqlite'
    dest = folder / 'aggregate-summary.sqlite'
    cid = _cid(31, 2, finishPolicy='on-verdict')
    scenario = _scenario(31, 2, finishPolicy='on-verdict')
    raw = json.dumps({'n': 12, 'mean': 2.5, 'score': 999, 'rank': 3, 'quality': 'A',
                      'histogram': {'1': 2}})
    _write_source(source, builds=[], predictor=[dict(id=cid, scenario=scenario, root=cid,
                                                     encounter=31, created=1, discovery=raw,
                                                     validation=None, pruned=1)])
    summary.build_library([dict(path=str(source), label='agg', ownership='unknown')], dest,
                          authorised=True, batch_size=4)
    view = summary.aggregate_evidence(dest)
    assert view['count'] == 1, view
    record = view['records'][0]
    assert record['hasSeedIdentity'] is False and record['confirmedReward'] is False
    assert record['independentConfirmation'] is False
    assert 'mean' in record['fields'] and 'n' in record['fields'], record['fields']
    for dropped in ('score', 'rank', 'quality'):
        assert dropped not in record['fields'], (dropped, record['fields'])
    assert record['measurementCount'] == 12
    # An aggregate-only build is never returned as a measurement prior.
    result = summary.load_observations(
        dest, [cid], encounter={'encounterId': 31, 'defeatCount': 2},
        policy={'finishPolicy': 'on-verdict'}, mechanics_revision='mech-1',
        engine_revision='9' * 64)
    assert not result['observations'], result['observations']
    return dict(records=view['count'], measurementCount=record['measurementCount'])


def lineage_preservation(folder):
    """The original per-candidate source/lineage is preserved, not replaced by the ownership."""
    source = folder / 'lineage.sqlite'
    dest = folder / 'lineage-summary.sqlite'
    cid = _cid(19, 0, finishPolicy='on-verdict')
    build = dict(id=cid, encounter=19, defeat=0, policy={'finishPolicy': 'on-verdict'},
                 origin_source='reseed:herb', label='Herb child', created=7)
    lineage_rows = [(cid, None, cid, 0, 'root', '', '', 'mutation', 19, 0, 4, 7)]
    _write_source(source, builds=[build], lineage_rows=lineage_rows,
                  evidence=[_ev(cid, 0, [81, 82], 1, awarded=1,
                                basis='reward-entitlement-certificate')])
    summary.build_library([dict(path=str(source), label='s', ownership='student9')], dest,
                          authorised=True, batch_size=4)
    assert _column(dest, 'SELECT source FROM candidate WHERE id=?', (cid,)) == 'reseed:herb'
    assert _column(dest, 'SELECT stats FROM candidate WHERE id=?', (cid,)) == '{}'
    assert _column(dest, 'SELECT source FROM lineage WHERE candidate=?', (cid,)) == 'mutation'
    assert _column(dest, 'SELECT source_ownership FROM history_candidate WHERE candidate_id=?',
                   (cid,)) == 'student9'
    return dict(candidateSource='reseed:herb', lineageSource='mutation')


def seed_exclusions(folder):
    """Known pairs are excludable, missing seeds are incomplete coverage, malformed fails closed."""
    from strategy_optimizer import seed_pair
    clean = folder / 'clean.sqlite'
    clean_dest = folder / 'clean-summary.sqlite'
    cid = _cid(19, 0, finishPolicy='on-verdict')
    build = dict(id=cid, encounter=19, defeat=0, policy={'finishPolicy': 'on-verdict'})
    banked = [tuple(seed_pair('validation', index)) for index in range(4)]
    _write_source(clean, digest='4' * 64, builds=[build],
                  evidence=[_ev(cid, index, pair, 1, awarded=1,
                                basis='reward-entitlement-certificate')
                            for index, pair in enumerate(banked)])
    summary.build_library([dict(path=str(clean), label='clean', ownership='u')], clean_dest,
                          authorised=True, batch_size=4)
    view = summary.forbidden_pairs(clean_dest, expand=True)
    assert set(banked) <= view['pairs']
    assert all(entry['bankCeiling'] >= 65536 for entry in view['bounds'])
    assert view['holdoutBlocked'] is False
    assert view['coverage']['establishedPairs'] >= 4
    assert any('no collision' in note for note in view['notes'])

    custom = folder / 'custom.sqlite'
    custom_dest = folder / 'custom-summary.sqlite'
    off_bank = seed_pair('validation', 1_000_000)
    _write_source(custom, digest='5' * 64, builds=[build],
                  evidence=[_ev(cid, 0, off_bank, 1, awarded=1,
                                basis='reward-entitlement-certificate')])
    summary.build_library([dict(path=str(custom), label='custom', ownership='u')], custom_dest,
                          authorised=True, batch_size=4)
    custom_view = summary.forbidden_pairs(custom_dest, expand=False)
    assert custom_view['holdoutBlocked'] is False
    assert tuple(off_bank) in set(custom_view['exceptionPairs'])
    assert custom_view['coverage']['exceptionPairs'] >= 1
    assert summary.history_status(custom_dest)['holdoutBlocked'] is False

    missing = folder / 'missing.sqlite'
    missing_dest = folder / 'missing-summary.sqlite'
    _write_source(missing, digest='7' * 64, builds=[build],
                  evidence=[_ev(cid, 0, None, 1, awarded=1,
                                basis='reward-entitlement-certificate')])
    summary.build_library([dict(path=str(missing), label='missing', ownership='u')], missing_dest,
                          authorised=True, batch_size=4)
    missing_view = summary.forbidden_pairs(missing_dest, expand=False)
    assert missing_view['holdoutBlocked'] is False
    assert missing_view['coverage']['unknownPairs'] >= 1
    bounds = missing_view['bounds'][0]
    assert bounds['missingSeedRows'] >= 1

    broken = folder / 'broken.sqlite'
    broken_dest = folder / 'broken-summary.sqlite'
    _write_source(broken, digest='8' * 64, builds=[build],
                  evidence=[(cid, 'validation', 0, 'not-json', 1, 0, 0, 1,
                             'reward-entitlement-certificate', None)])
    report = summary.build_library([dict(path=str(broken), label='broken', ownership='u')],
                                   broken_dest, authorised=True, batch_size=4)
    assert report['holdoutBlocked'] is True, report
    broken_view = summary.forbidden_pairs(broken_dest, expand=False)
    assert broken_view['holdoutBlocked'] is True
    return dict(established=view['coverage']['establishedPairs'],
                exceptionPairs=len(custom_view['exceptionPairs']),
                missingUnknown=missing_view['coverage']['unknownPairs'])


def block_corruption_refused(folder):
    """A corrupted compressed block is refused, never silently accepted."""
    from strategy_optimizer import seed_pair
    source = folder / 'corrupt.sqlite'
    dest = folder / 'corrupt-summary.sqlite'
    cid = _cid(19, 0, finishPolicy='on-verdict')
    build = dict(id=cid, encounter=19, defeat=0, policy={'finishPolicy': 'on-verdict'})
    _write_source(source, builds=[build],
                  evidence=[_ev(cid, 0, seed_pair('validation', 0), 1, awarded=1,
                                basis='reward-entitlement-certificate')])
    summary.build_library([dict(path=str(source), label='s', ownership='u')], dest,
                          authorised=True, batch_size=4)
    db = sqlite3.connect(str(dest))
    try:
        db.execute('UPDATE history_seed_block SET block_zlib=?', (b'not zlib',))
        db.commit()
    finally:
        db.close()
    for call in (lambda: summary.verify_blocks(dest),
                 lambda: summary.forbidden_pairs(dest, expand=False)):
        try:
            call()
            raise AssertionError('a corrupted block must be refused')
        except summary.HistorySummaryError:
            pass
    return dict(refused=True)


def loader_contract(folder):
    """The pure loader returns the legacy shape, derives scenario features and refuses bad scope."""
    from strategy_optimizer_adapter import default_scenario
    source = folder / 'loader.sqlite'
    dest = folder / 'loader-summary.sqlite'
    scenario = default_scenario()
    cid = domain.identity(scenario)
    build = dict(id=cid, encounter=19, defeat=0, scenario=scenario)
    evidence = [_ev(cid, index, [51 + index, 61 + index], 1, awarded=2,
                    basis='reward-entitlement-certificate') for index in range(3)]
    _write_source(source, digest='6' * 64, builds=[build], evidence=evidence)
    summary.build_library([dict(path=str(source), label='s', ownership='student9')], dest,
                          authorised=True, batch_size=4)
    result = summary.load_observations(
        dest, [cid], encounter={'encounterId': 19, 'defeatCount': 0},
        policy={'finishPolicy': 'on-verdict'}, mechanics_revision='mech-1',
        engine_revision='6' * 64)
    assert len(result['observations']) == 1, result['excluded']
    observation = result['observations'][0]
    missing = [key for key in CONSERVED_OBSERVATION_KEYS if key not in observation]
    assert not missing, missing
    assert observation['meanEarned'] == 2.0
    assert observation['evidenceClass'] == 'historical-prior'
    assert observation['independentConfirmation'] is False
    assert observation['confirmationEligible'] is False
    assert observation['adviserEligible'] is True
    assert observation['featuresAvailable'] is True and observation['features'], observation['features']
    assert observation['domain'] is not None
    assert observation['historicalConfirmed'] is False

    # Full build constraints are validated against the exact stored scenario, never refused.
    constrained = summary.load_observations(
        dest, [cid], encounter={'encounterId': 19, 'defeatCount': 0},
        policy={'finishPolicy': 'on-verdict'}, mechanics_revision='mech-1',
        engine_revision='6' * 64, constraints={})
    assert len(constrained['observations']) == 1, constrained['excluded']
    rejected = summary.load_observations(
        dest, [cid], encounter={'encounterId': 19, 'defeatCount': 0},
        policy={'finishPolicy': 'on-verdict'}, mechanics_revision='mech-1',
        engine_revision='6' * 64, constraints={'fixed': {'encounterId': 999}})
    assert not rejected['observations'], rejected['observations']
    assert rejected['excluded'][0]['reason'] in ('constraints-mismatch', 'constraints-invalid'), \
        rejected['excluded']

    wrong_engine = summary.load_observations(
        dest, [cid], encounter={'encounterId': 19, 'defeatCount': 0},
        policy={'finishPolicy': 'on-verdict'}, mechanics_revision='mech-1',
        engine_revision='9' * 64)
    assert not wrong_engine['observations']
    assert wrong_engine['excluded'][0]['reason'] == 'engine-revision-mismatch'

    no_engine = summary.load_observations(
        dest, [cid], encounter={'encounterId': 19, 'defeatCount': 0},
        policy={'finishPolicy': 'on-verdict'}, mechanics_revision='mech-1')
    assert not no_engine['observations']
    assert no_engine['excluded'][0]['reason'] == 'engine-revision-missing'

    wrong_policy = summary.load_observations(
        dest, [cid], encounter={'encounterId': 19, 'defeatCount': 0},
        policy={'finishPolicy': 'certified-only'}, mechanics_revision='mech-1',
        engine_revision='6' * 64)
    assert not wrong_policy['observations']
    assert wrong_policy['excluded'][0]['reason'] == 'policy-mismatch'
    return dict(keys=len(CONSERVED_OBSERVATION_KEYS), mean=observation['meanEarned'])


def main():
    with summary._temp_dir('ka-history-check-') as raw:
        folder = Path(raw)
        report = dict(
            module='strategy_history_summary',
            compactness=compactness(folder),
            weightedLargeCount=weighted_large_count(folder),
            seedBlockRoundtrip=seed_block_roundtrip(folder),
            momentsOracle=moments_oracle(folder),
            overlapAndConflict=overlap_and_conflict(folder),
            partialEvidenceRunUnion=partial_evidence_run_union(folder),
            groupsSplit=groups_split(folder),
            unknownNotZero=unknown_not_zero(folder),
            immutableAndExistingDest=immutable_and_existing_dest(folder),
            freshCounters=fresh_counters(folder),
            aliasIdentity=alias_identity(folder),
            provenanceUnknown=provenance_unknown(folder),
            prunedScenarioRecovery=pruned_scenario_recovery(folder),
            aggregateOnly=aggregate_only(folder),
            lineagePreservation=lineage_preservation(folder),
            seedExclusions=seed_exclusions(folder),
            blockCorruptionRefused=block_corruption_refused(folder),
            loaderContract=loader_contract(folder),
            limits=['Tiny hand-built fixtures only; no simulator, engine, live library or battle.',
                    'The statistical check compares the persisted weighted summary to the canonical '
                    'strategy_outcomes.summarize over the same fixture rows.',
                    'The compressed block check decodes/encodes real stored blocks; no full replay.'],
        )
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
