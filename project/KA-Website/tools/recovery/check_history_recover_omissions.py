"""Targeted synthetic checks for ``strategy_history_recover_omissions`` (tiny hand-built fixtures).

Fixture shape (no simulator, no engine, no live library, no battle):

* a source whose candidate scenario was pruned but whose exact scenario is already archived in the
  destination (recovered earlier from ``predictor_history``), with retained evidence *and* run-only
  rows -- both must come back and dedupe;
* a second, engine-incompatible source whose pruned candidate is recovered into its own group;
* a pruned candidate with no archived scenario -- it must stay omitted;
* an already-represented duplicate candidate -- must refuse (stop) or exclude without merging.

Plus the bounded review corrections: a cross-phase collision of an existing *block group* must not
insert a summary whose block ``INSERT OR IGNORE`` would drop; serial vs precomputed-info equality;
restoration of ``summary._scenario_info`` on exception; omission-underflow rollback; and receipt
reuse that avoids any hashing/streaming on an idempotent rerun.

Run: ``python -B -u -X utf8 check_history_recover_omissions.py`` from ``tools/recovery``.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import time
import zlib
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_TMP = _HERE / '.tmp-history-recover'
_TMP.mkdir(parents=True, exist_ok=True)
tempfile.tempdir = str(_TMP)
for _env in ('TMPDIR', 'TEMP', 'TMP'):
    os.environ[_env] = str(_TMP)

sys.path.insert(0, str(_HERE))

import strategy_build_domain as domain  # noqa: E402
import strategy_history_summary as summary  # noqa: E402
import strategy_history_recover_omissions as recover_mod  # noqa: E402

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
    root TEXT NOT NULL, encounter INTEGER NOT NULL, created INTEGER NOT NULL, discovery TEXT,
    validation TEXT, pruned INTEGER NOT NULL);
CREATE TABLE rebel_break(ledger_id INTEGER PRIMARY KEY, detail TEXT);
"""

FAILURES = []


def check(condition, message):
    if condition:
        print('  ok  %s' % message)
    else:
        FAILURES.append(message)
        print('  FAIL %s' % message)


def provenance(digest, marker):
    return dict(digest=digest, count=1,
                files={'tools/recovery/combat_shared_controllers.py': marker})


def scenario(encounter, defeat, **policy):
    body = dict(encounterId=encounter, defeatCount=defeat, mathSeed=1, libSeed=2)
    body.update(policy)
    return body


def cid(encounter, defeat, **policy):
    return domain.identity(scenario(encounter, defeat, **policy))


def evidence_row(candidate, ordinal, seeds, verdict, awarded=None, basis=None, phase='validation'):
    return (candidate, phase, ordinal, None if seeds is None else json.dumps(list(seeds)),
            verdict, 0, 0, awarded, basis, None)


def run_row(candidate, ordinal, seeds, verdict, awarded=None, phase='validation'):
    body = dict(verdict=verdict, seeds=list(seeds), censored=0)
    if awarded is not None:
        body['rewardOutcome'] = dict(awardedChests=awarded)
    return (candidate, phase, ordinal, json.dumps(body))


def write_source(path, *, digest, marker, builds=(), evidence=(), runs=(), predictor=()):
    db = sqlite3.connect(str(path))
    try:
        db.executescript(SCHEMA)
        for key, value in (('schema', 1), ('objectiveVersion', 3), ('searchSpaceVersion', 4),
                           ('totalRuns', 5), ('timedRuns', 5)):
            db.execute('INSERT INTO meta VALUES(?,?)', (key, json.dumps(value)))
        db.execute('INSERT INTO meta VALUES(?,?)', ('provenance', json.dumps(provenance(digest, marker))))
        for build_id, encounter, defeat, scn in builds:
            db.execute('INSERT INTO candidate VALUES(?,?,?,?,?,?)',
                       (build_id, json.dumps(scn), build_id, 'fixture', '{}', 0))
            db.execute('INSERT INTO candidate_meta VALUES(?,?,?,?)', (build_id, encounter, defeat, ''))
        db.executemany('INSERT INTO evidence VALUES(?,?,?,?,?,?,?,?,?,?)', list(evidence))
        db.executemany('INSERT INTO run VALUES(?,?,?,?)', list(runs))
        for entry in predictor:
            db.execute('INSERT INTO predictor_history VALUES(?,?,?,?,?,?,?,?)',
                       (entry['id'], zlib.compress(json.dumps(entry['scenario']).encode('utf-8'), 1),
                        entry['id'], entry['encounter'], 0, None, None, 0))
        db.commit()
    finally:
        db.close()


def ro(path):
    conn = sqlite3.connect('file:' + str(path).replace('\\', '/') + '?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def scalar(path, sql, args=()):
    conn = ro(path)
    try:
        row = conn.execute(sql, args).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def rows(path, sql, args=()):
    conn = ro(path)
    try:
        return [tuple(r) for r in conn.execute(sql, args)]
    finally:
        conn.close()


def sha(path):
    return summary._sha256_file(path)


def build_pruned_destination(folder):
    src_a = folder / 'src-a.sqlite'
    src_b = folder / 'src-b.sqlite'
    xa = cid(0, 0, finishPolicy='on-verdict')
    ya_scn = scenario(1, 0, finishPolicy='on-verdict')
    yb_scn = scenario(3, 0, finishPolicy='on-verdict', tickLimit=99)
    write_source(
        src_a, digest='a' * 64, marker='ma',
        builds=[(xa, 0, 0, scenario(0, 0, finishPolicy='on-verdict'))],
        evidence=[evidence_row(xa, 0, [1, 1], 1, awarded=2),
                  evidence_row('Y_a', 0, [11, 11], 1, awarded=5),
                  evidence_row('Y_a', 1, [11, 11], 1, awarded=5),
                  evidence_row('Z_a', 0, [41, 41], 0),
                  evidence_row('Z_a', 1, [51, 51], 1, awarded=2)],
        runs=[run_row('Y_a', 10, [21, 21], 1, awarded=3),
              run_row('Y_a', 11, [31, 31], 1, awarded=7)],
        predictor=[dict(id='Y_a', encounter=1, scenario=ya_scn)])
    write_source(
        src_b, digest='b' * 64, marker='mb',
        evidence=[evidence_row('Y_b', 0, [61, 61], 1, awarded=9)],
        predictor=[dict(id='Y_b', encounter=3, scenario=yb_scn)])
    dest = folder / 'dest.sqlite'
    summary.build_library([dict(path=str(src_a), label='a', ownership='u'),
                           dict(path=str(src_b), label='b', ownership='u')],
                          str(dest), authorised=True)
    return src_a, src_b, dest, xa, domain.identity(ya_scn), domain.identity(yb_scn), \
        domain.identity(scenario(2, 0, finishPolicy='on-verdict'))


def test_recovery(folder):
    src_a, src_b, dest, xa, ya, yb, za = build_pruned_destination(folder)
    sid_a = scalar(dest, 'SELECT source_id FROM history_source WHERE source_path=?',
                   (str(Path(src_a).resolve()),))
    sid_b = scalar(dest, 'SELECT source_id FROM history_source WHERE source_path=?',
                   (str(Path(src_b).resolve()),))
    check(sid_a and sid_b, 'both sources imported')
    check(scalar(dest, "SELECT SUM(count) FROM history_omission WHERE source_id=? AND "
                        "kind='missing-scenario'", (sid_a,)) == 6, 'src_a counted 6 omissions')
    check(scalar(dest, "SELECT SUM(count) FROM history_omission WHERE source_id=? AND "
                        "kind='missing-scenario'", (sid_b,)) == 1, 'src_b counted 1 omission')
    old_summaries = scalar(dest, 'SELECT COUNT(*) FROM history_build_summary')
    old_blocks = scalar(dest, 'SELECT COUNT(*) FROM history_seed_block')
    old_scenarios = scalar(dest, 'SELECT COUNT(*) FROM history_scenario')
    cand_before = rows(dest, 'SELECT id FROM candidate ORDER BY id')
    hash_a, hash_b = sha(src_a), sha(src_b)

    dry = recover_mod.recover(str(dest), authorised=False, report_path=str(folder / 'dry.md'))
    check(dry['collisionCount'] == 0, 'dry plan sees no collision')
    check(dry['totals'] == dict(omissionBefore=7, recoverableInputRows=5,
                                retainedObservations=5, residualInputRows=2,
                                collisionInputRows=0, undecodableInputRows=0),
          'dry plan rows: 7 omitted -> 5 recovered, 2 residual (%r)' % dry['totals'])
    check(all(row['balanced'] for row in dry['conservation']), 'dry plan conservation balances')
    check((folder / 'dry.md').is_file(), 'dry plan report written')

    run = recover_mod.recover(str(dest), authorised=True, report_path=str(folder / 'run.md'))
    check(run['applied'] and run['receiptsWritten'], 'authorised recovery applied')
    check(scalar(dest, 'SELECT COUNT(*) FROM history_build_summary') == old_summaries + 2,
          'two group summaries appended')
    check(scalar(dest, 'SELECT COUNT(*) FROM history_seed_block') == old_blocks + 2,
          'two seed blocks appended')
    check(scalar(dest, 'SELECT COUNT(*) FROM history_scenario') == old_scenarios,
          'no new scenarios written')
    check(scalar(dest, "SELECT SUM(count) FROM history_omission WHERE source_id=? AND "
                        "kind='missing-scenario'", (sid_a,)) == 2,
          'src_a omission corrected to the 2 genuinely unknown rows')
    check(scalar(dest, "SELECT SUM(count) FROM history_omission WHERE source_id=? AND "
                        "kind='missing-scenario'", (sid_b,)) == 0,
          'src_b omission fully corrected')
    check(scalar(dest, "SELECT COUNT(*) FROM meta WHERE key LIKE 'historyOmissionRecovery:%'") == 2,
          'two idempotency receipts persisted')
    check(rows(dest, 'SELECT id FROM candidate ORDER BY id') == cand_before,
          'active candidate set unchanged')
    check(scalar(dest, "SELECT value FROM meta WHERE key='totalRuns'") == json.dumps(0),
          'campaign counters still zero')
    check(sha(src_a) == hash_a and sha(src_b) == hash_b, 'original source hashes unchanged')
    engines = dict(rows(dest, 'SELECT candidate_id,engine_compat_group FROM history_build_summary '
                              'WHERE candidate_id IN (?,?)', (ya, yb)))
    check(engines.get(ya) and engines.get(yb) and engines[ya] != engines[yb],
          'incompatible engine groups stayed separate')
    check(scalar(dest, "SELECT COUNT(*) FROM history_scenario WHERE candidate_id=?", (za,)) == 0,
          'unknown scenario stayed unarchived and omitted')
    conn = ro(dest)
    try:
        row = conn.execute('SELECT * FROM history_build_summary WHERE candidate_id=?', (ya,)).fetchone()
        summary_row = dict(row)
        block = conn.execute('SELECT record_count,observation_count FROM '
                             'history_seed_block WHERE candidate_id=?', (ya,)).fetchone()
    finally:
        conn.close()
    check(summary_row['retained_measurements'] == 3 and summary_row['duplicate_count'] == 1,
          'evidence+run-only deduped to 3 measurement keys with 1 duplicate')
    check(tuple(block) == (3, 4), 'block holds 3 records / 4 observations')

    rerun = recover_mod.recover(str(dest), authorised=True, report_path=str(folder / 'rerun.md'))
    check(rerun['alreadyApplied'], 'rerun is a no-op (receipt found)')
    check(scalar(dest, 'SELECT COUNT(*) FROM history_build_summary') == old_summaries + 2
          and scalar(dest, 'SELECT COUNT(*) FROM history_seed_block') == old_blocks + 2,
          'rerun appended nothing')
    check(sha(src_a) == hash_a and sha(src_b) == hash_b, 'rerun left sources unchanged')


def build_collision_destination(folder):
    src_c = folder / 'src-c.sqlite'
    src_d = folder / 'src-d.sqlite'
    canonical = cid(5, 0, finishPolicy='on-verdict')
    write_source(src_c, digest='c' * 64, marker='mc',
                 builds=[(canonical, 5, 0, scenario(5, 0, finishPolicy='on-verdict'))],
                 evidence=[evidence_row(canonical, 0, [81, 81], 1, awarded=2)])
    write_source(src_d, digest='c' * 64, marker='mc',
                 evidence=[evidence_row(canonical, 5, [71, 71], 1, awarded=4)])
    dest = folder / 'dest-collision.sqlite'
    summary.build_library([dict(path=str(src_c), label='c', ownership='u'),
                           dict(path=str(src_d), label='d', ownership='u')],
                          str(dest), authorised=True)
    return src_c, src_d, dest, canonical


def test_collision(folder):
    src_c, src_d, dest, canonical = build_collision_destination(folder)
    sid_d = scalar(dest, 'SELECT source_id FROM history_source WHERE source_path=?',
                   (str(Path(src_d).resolve()),))
    old_summaries = scalar(dest, 'SELECT COUNT(*) FROM history_build_summary')
    check(scalar(dest, "SELECT COUNT(*) FROM history_build_summary WHERE candidate_id=?",
                 (canonical,)) >= 1, 'the duplicate identity is already summarised')
    stopped = False
    try:
        recover_mod.recover(str(dest), authorised=True, report_path=str(folder / 'stop.md'),
                            on_collision='stop')
    except recover_mod.CollisionError:
        stopped = True
    check(stopped, 'collision stops by default (fail closed)')
    check(scalar(dest, 'SELECT COUNT(*) FROM history_build_summary') == old_summaries,
          'stop wrote nothing')
    skipped = recover_mod.recover(str(dest), authorised=True, report_path=str(folder / 'skip.md'),
                                  on_collision='skip')
    check(skipped['collisionCount'] >= 1, 'skip mode reports the collision')
    check(scalar(dest, 'SELECT COUNT(*) FROM history_build_summary') == old_summaries,
          'skip appended no duplicate group')
    check(scalar(dest, "SELECT SUM(count) FROM history_omission WHERE source_id=? AND "
                        "kind='missing-scenario'", (sid_d,)) == 1,
          'skip left the excluded row omitted (no false correction)')


def test_cli(folder):
    dest = _TMP / 'case1' / 'dest.sqlite'
    code = recover_mod.main(['--library', str(dest), '--report', str(folder / 'cli.md'),
                             '--no-verify-hashes'])
    check(code == 0, 'CLI dry plan exits 0')
    check((folder / 'cli.md').is_file(), 'CLI wrote the report')
    check((folder / 'cli.json').is_file(), 'CLI wrote the compact JSON report alongside it')
    progress = folder / 'history-omission-progress.json'
    check(progress.is_file(), 'CLI exposed a progress JSON for the local monitor')
    plan = recover_mod.recover(str(dest), report_path=str(folder / 'plan.md'), plan_only=True)
    check(plan['outcome'] == 'plan-only' and plan['collisionCount'] == 2 and not plan['applied'],
          'plan-only mode estimates the two collisions without streaming or writing')


def build_crossphase_destination(folder):
    src_c = folder / 'xp-c.sqlite'
    src_d = folder / 'xp-d.sqlite'
    canonical = cid(7, 0, finishPolicy='on-verdict')
    write_source(src_c, digest='e' * 64, marker='me',
                 builds=[(canonical, 7, 0, scenario(7, 0, finishPolicy='on-verdict'))],
                 evidence=[evidence_row(canonical, 0, [11, 11], 1, awarded=3, phase='validation')])
    write_source(src_d, digest='e' * 64, marker='me',
                 evidence=[evidence_row(canonical, 0, [21, 21], 1, awarded=5, phase='holdout')])
    dest = folder / 'dest-xp.sqlite'
    summary.build_library([dict(path=str(src_c), label='c', ownership='u'),
                           dict(path=str(src_d), label='d', ownership='u')],
                          str(dest), authorised=True)
    return src_c, src_d, dest, canonical


def test_cross_phase_collision(folder):
    src_c, src_d, dest, canonical = build_crossphase_destination(folder)
    sid_d = scalar(dest, 'SELECT source_id FROM history_source WHERE source_path=?',
                   (str(Path(src_d).resolve()),))
    old_summaries = scalar(dest, 'SELECT COUNT(*) FROM history_build_summary')
    old_blocks = scalar(dest, 'SELECT COUNT(*) FROM history_seed_block')
    phase_list = scalar(dest, 'SELECT phase_list_json FROM history_seed_block WHERE candidate_id=?',
                        (canonical,))
    block_sha = scalar(dest, 'SELECT plain_sha256 FROM history_seed_block WHERE candidate_id=?',
                       (canonical,))
    check(scalar(dest, 'SELECT COUNT(*) FROM history_build_summary WHERE candidate_id=?',
                 (canonical,)) == 1, 'the existing block has one phase summary')
    stopped = False
    try:
        recover_mod.recover(str(dest), authorised=True, report_path=str(folder / 'xp-stop.md'),
                            on_collision='stop', workers=1)
    except recover_mod.CollisionError:
        stopped = True
    check(stopped, 'a new phase of an existing block stops (fail closed)')
    check(scalar(dest, 'SELECT COUNT(*) FROM history_build_summary') == old_summaries
          and scalar(dest, 'SELECT COUNT(*) FROM history_seed_block') == old_blocks,
          'stop wrote no summary and lost no block')
    skipped = recover_mod.recover(str(dest), authorised=True, report_path=str(folder / 'xp-skip.md'),
                                  on_collision='skip', workers=1)
    check(skipped['collisionCount'] == 1, 'skip detects the block-group (not just phase) collision')
    check(scalar(dest, 'SELECT COUNT(*) FROM history_build_summary') == old_summaries,
          'no false summary inserted for the colliding new phase')
    check(scalar(dest, 'SELECT COUNT(*) FROM history_seed_block') == old_blocks,
          'no block lost to INSERT OR IGNORE and no duplicate block')
    check(scalar(dest, 'SELECT phase_list_json FROM history_seed_block WHERE candidate_id=?',
                 (canonical,)) == phase_list
          and scalar(dest, 'SELECT plain_sha256 FROM history_seed_block WHERE candidate_id=?',
                     (canonical,)) == block_sha,
          'the existing block payload is unchanged')
    check(skipped['totals']['collisionInputRows'] == 1,
          'the excluded source observations are counted exactly (1)')
    check(scalar(dest, "SELECT SUM(count) FROM history_omission WHERE source_id=? AND "
                       "kind='missing-scenario'", (sid_d,)) == 1,
          'the excluded row stays omitted (no false correction)')
    check(scalar(dest, "SELECT COUNT(*) FROM history_build_summary WHERE candidate_id=? "
                       "AND phase='holdout'", (canonical,)) == 0,
          'the colliding new phase was not inserted')


def _recovered_rows(dest):
    conn = ro(dest)
    try:
        summaries = [tuple(r) for r in conn.execute(
            'SELECT candidate_id,phase,status,mean_earned,sample_count,resolved_count,'
            'retained_measurements,moments_json,reward_histogram_json FROM history_build_summary '
            'ORDER BY candidate_id,phase')]
        blocks = [tuple(r) for r in conn.execute(
            'SELECT candidate_id,record_count,observation_count,plain_sha256,block_zlib '
            'FROM history_seed_block ORDER BY candidate_id')]
    finally:
        conn.close()
    return summaries, blocks


def test_worker_equivalence(folder):
    scn = scenario(9, 0, finishPolicy='on-verdict')
    canonical = domain.identity(scn)
    candidates = {canonical: dict(canonical=canonical, scenario=domain.canonical(scn),
                                  encounter=9, defeat=0)}
    lookup1, _ = recover_mod._prepare_infos(candidates, 1, None, done_base=0,
                                            total=len(candidates), since=time.time())
    lookup2, mode2 = recover_mod._prepare_infos(candidates, 2, None, done_base=0,
                                                total=len(candidates), since=time.time())
    check(lookup1 == lookup2, 'serial and 2-worker precomputed scenario infos are identical')
    if mode2 != 'pool':
        print('  note  process pool unavailable here (%s); 2-worker path used the identical '
              'serial precompute' % mode2)
    a = folder / 'wa'
    b = folder / 'wb'
    a.mkdir(parents=True, exist_ok=True)
    b.mkdir(parents=True, exist_ok=True)
    _, _, dest1, *_ = build_pruned_destination(a)
    _, _, dest2, *_ = build_pruned_destination(b)
    recover_mod.recover(str(dest1), authorised=True, report_path=str(a / 'r.md'), workers=1)
    recover_mod.recover(str(dest2), authorised=True, report_path=str(b / 'r.md'), workers=2)
    check(_recovered_rows(dest1) == _recovered_rows(dest2),
          'serial and 2-worker runs append identical summaries and seed-block payloads')


def test_context_restoration():
    original = summary._scenario_info
    scn = scenario(3, 0, finishPolicy='on-verdict')
    canonical = domain.identity(scn)
    key = recover_mod._scenario_key(scn, None, canonical)
    prepared = summary._scenario_info(scn, None, stored_id=canonical)
    try:
        with recover_mod._precomputed_scenario_info({key: prepared}):
            check(summary._scenario_info is not original, 'lookup installed during the stream')
            got = summary._scenario_info(scn, None, canonical)
            check(got == prepared and got is not prepared,
                  'lookup serves an equal copy of the precomputed info')
            raise RuntimeError('boom')
    except RuntimeError:
        pass
    check(summary._scenario_info is original, 'original restored after an exception')
    refused = False
    try:
        with recover_mod._precomputed_scenario_info({}):
            summary._scenario_info(scn, None, canonical)
    except recover_mod.RecoveryError:
        refused = True
    check(refused, 'a lookup miss refuses instead of fabricating metadata')
    check(summary._scenario_info is original, 'original restored after a refusal')


def test_omission_underflow(folder):
    src_a, src_b, dest, *_ = build_pruned_destination(folder)
    sid_a = scalar(dest, 'SELECT source_id FROM history_source WHERE source_path=?',
                   (str(Path(src_a).resolve()),))
    conn = sqlite3.connect(str(dest))
    try:
        conn.execute("UPDATE history_omission SET count=1 WHERE source_id=? AND "
                     "kind='missing-scenario'", (sid_a,))
        conn.commit()
    finally:
        conn.close()
    old_summaries = scalar(dest, 'SELECT COUNT(*) FROM history_build_summary')
    old_receipts = scalar(dest, "SELECT COUNT(*) FROM meta WHERE key LIKE 'historyOmissionRecovery:%'")
    underflow = False
    try:
        recover_mod.recover(str(dest), authorised=True, report_path=str(folder / 'under.md'), workers=1)
    except recover_mod.RecoveryError as exc:
        underflow = 'underflow' in str(exc)
    check(underflow, 'omission underflow refuses to clamp to zero')
    check(scalar(dest, 'SELECT COUNT(*) FROM history_build_summary') == old_summaries,
          'underflow rolled the summary append back')
    check(scalar(dest, "SELECT COUNT(*) FROM meta WHERE key LIKE 'historyOmissionRecovery:%'")
          == old_receipts, 'underflow rolled the receipt back')


def test_receipt_reuse(folder):
    src_a, src_b, dest, *_ = build_pruned_destination(folder)
    recover_mod.recover(str(dest), authorised=True, report_path=str(folder / 'apply.md'), workers=1)
    orig_verify = recover_mod._verify_source
    orig_stream = summary._stream_measurements

    def boom(*args, **kwargs):
        raise AssertionError('heavy reprocessing happened on an idempotent rerun')

    recover_mod._verify_source = boom
    summary._stream_measurements = boom
    try:
        rerun = recover_mod.recover(str(dest), authorised=True,
                                    report_path=str(folder / 'reuse.md'), workers=6)
    finally:
        recover_mod._verify_source = orig_verify
        summary._stream_measurements = orig_stream
    check(rerun['alreadyApplied'] and rerun['outcome'] == 'already-applied',
          'receipt reuse short-circuits before hashing or streaming')
    check(rerun['hashesVerified'] is False,
          'the replay report does not claim the source hashes were verified')
    check((folder / 'reuse.json').is_file(), 'a compact JSON report is saved with the markdown')


def main():
    print('history omission recovery checks')
    import shutil
    for name in ('case1', 'case2', 'case3', 'case4', 'case5', 'case6', 'case7'):
        shutil.rmtree(_TMP / name, ignore_errors=True)
        (_TMP / name).mkdir(parents=True, exist_ok=True)
    print('fixture 1: pruned scenario + evidence + run-only, unknown stays omitted')
    test_recovery(_TMP / 'case1')
    print('fixture 2: already-represented duplicate (collision policy)')
    test_collision(_TMP / 'case2')
    print('fixture 3: CLI dry plan and progress')
    test_cli(_TMP / 'case3')
    print('fixture 4: cross-phase block-group collision (no lost block, no false insert)')
    test_cross_phase_collision(_TMP / 'case4')
    print('fixture 5: serial vs 2-worker precompute and summary/outcome equality')
    test_worker_equivalence(_TMP / 'case5')
    print('fixture 6: omission underflow rollback')
    test_omission_underflow(_TMP / 'case6')
    print('fixture 7: idempotency receipt reuse')
    test_receipt_reuse(_TMP / 'case7')
    print('fixture 8: lookup context restoration (no report folder)')
    test_context_restoration()
    print('failures: %d' % len(FAILURES))
    return 1 if FAILURES else 0


if __name__ == '__main__':
    raise SystemExit(main())
