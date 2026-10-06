"""Focused checks for the Community (ea_*) strategy readers wired into the desktop bridge.

Proves, on bounded scratch SQLite fixtures and one real native library the host exported:

  * a pruned candidate whose only frozen evidence is the experiment ``observedScenarios`` intent is
    mapped to the exact fight it ran under (never inferred from the current selection), while a
    candidate whose frozen intents disagree stays unmapped rather than being guessed;
  * ``encounter_strategies`` shows measured attempts (never a false 0) for an EA-backed build and
    gives an EA-only candidate a real navigable row keyed by the same id the overview ea block uses;
  * ``strategy_detail`` resolves an EA candidate id - resident or pruned - and exposes its persisted
    ea_* readings, without fabricating stored legacy runs;
  * two incompatible observation windows on one build are kept separate (no combined mean);
  * duplicate sample links never inflate a count, and unknown outcomes are counted but excluded;
  * every read is byte-for-byte read-only.

Writes a short report to tmp/encounter-redesign-20260928/community-readers-report.md.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import sys
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))

import strategy_encounter_overview as ov  # noqa: E402
import strategy_optimizer_desktop as desktop  # noqa: E402

WORK = REPO / 'tmp' / 'encounter-redesign-20260928' / 'check-community-readers'
REPORT = REPO / 'tmp' / 'encounter-redesign-20260928' / 'community-readers-tests.md'
REAL = REPO / 'tmp' / 'encounter-redesign-20260928' / 'ka-accept-joint-ffcb5dd4' / 'lib.sqlite'
SCRATCH = Path(r'C:\Users\anisb\AppData\Local\Temp\community-run-smoke-z_ibiq4h\scratch.sqlite')
DIAGNOSTICS = Path(r'C:\Users\anisb\AppData\Local\Temp\diagnostics.json')

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
CREATE TABLE run(candidate TEXT NOT NULL, phase TEXT NOT NULL, ordinal INTEGER NOT NULL,
                 result TEXT NOT NULL, PRIMARY KEY(candidate, phase, ordinal));
'''


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def readonly(path):
    return sqlite3.connect('file:%s?mode=ro' % str(path).replace('\\', '/'), uri=True)


def fresh(name):
    WORK.mkdir(parents=True, exist_ok=True)
    path = WORK / (name + '.sqlite')
    for suffix in ('', '-wal', '-shm'):
        try:
            Path(str(path) + suffix).unlink()
        except OSError:
            pass
    db = sqlite3.connect(path)
    db.executescript(_SCHEMA)
    return path, db


def add_scenario(db, cid, encounter_id=19, defeat=0, label='build', source='mutation'):
    db.execute('INSERT INTO candidate(id,scenario,label,source,stats,created) VALUES(?,?,?,?,?,?)',
               (cid, json.dumps(dict(encounterId=encounter_id, defeatCount=defeat)), label,
                source, '{}', 0))


def add_experiment(db, eid, observed, *, window='"development"', policy='{"p":1}',
                   mechanics='m1', revision='e1'):
    db.execute('INSERT INTO ea_experiment(id,request_key,scope,policy,mechanics_revision,'
               'encounter_revision,fixed_fields,changed_fields,planned_budget,stopping,'
               'measurement_window,created_at,intent_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
               (eid, 'req-%s' % eid, 'community', policy, mechanics, revision, '{}', '{}', 1000,
                'budget', window, 0.0, json.dumps(dict(observedScenarios=observed))))


def result(seeds=(1, 2), backend='native'):
    return json.dumps(dict(resultBackend=backend, seeds=list(seeds),
                           rewardOutcome=dict(pendingChests=0)))


def outcome(verdict, earned, pending=0, *, resolved=True, censored=False, error=False):
    return json.dumps(dict(verdict=verdict, finalEarned=earned, pending=pending, resolved=resolved,
                           censored=censored, error=error, engineRevision='engine-a',
                           experimentId=1, status='checked'))


def add_sample(db, key, cid, *, window='development', seeds=(1, 2), result_json=None,
               outcome_json=None):
    db.execute('INSERT INTO ea_sample(sample_key,candidate_id,policy,mechanics_revision,'
               'encounter_revision,seed_a,seed_b,measurement_window,result,outcome,timing,created_at)'
               ' VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
               (key, cid, '{"p":1}', 'm1', 'e1', seeds[0], seeds[1], window, result_json,
                outcome_json, None, 1000.0))


def add_link(db, eid, cid, seeds=(1, 2), sample_key=None, reused=0):
    db.execute('INSERT INTO ea_sample_link(experiment_id,candidate_id,seed_a,seed_b,sample_key,'
               'reused,charged_experiment_id,created_at) VALUES(?,?,?,?,?,?,?,?)',
               (eid, cid, seeds[0], seeds[1], sample_key, reused, None, 0.0))


def bridge_for(path):
    bridge = desktop.Bridge.__new__(desktop.Bridge)
    bridge._library = Path(path)
    bridge._sidecar_lock = threading.RLock()
    bridge._ledger_lock = threading.RLock()
    return bridge


def hash_of(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build_fixture():
    path, db = fresh('readers')
    add_scenario(db, 'resident', 19, 0, label='Resident build')
    add_scenario(db, 'other', 20, 0, label='Other-fight build')
    add_scenario(db, 'multi', 19, 0, label='Two-window build')
    add_scenario(db, 'unknowny', 19, 0, label='No-verdict build')
    # An experiment that froze the observed scenario of a candidate whose row was later pruned.
    add_experiment(db, 2, {'pruned': dict(encounterId=19, defeatCount=0, mathSeed=7, libSeed=8)})
    # Two experiments that disagree about the same candidate -> it must stay unmapped.
    add_experiment(db, 3, {'ambig': dict(encounterId=19, defeatCount=0)})
    add_experiment(db, 4, {'ambig': dict(encounterId=21, defeatCount=0)})
    return path, db


def seed_fixture(db):
    # Resident: 10 native wins of 6 earned, 2 native losses (30 pending), 1 unknown outcome.
    for i in range(10):
        add_sample(db, 'res-win-%d' % i, 'resident', seeds=(i, i + 1), result_json=result(),
                   outcome_json=outcome(1, 6))
    for i in range(2):
        add_sample(db, 'res-loss-%d' % i, 'resident', seeds=(100 + i, 200 + i), result_json=result(),
                   outcome_json=outcome(2, 0, pending=30))
    # A build whose only sample has no canonical outcome: counted, but it cannot join a mean.
    add_sample(db, 'unknowny-0', 'unknowny', seeds=(300, 301), result_json=result(),
               outcome_json=None)
    # Duplicate links for one resident sample never duplicate the measurement.
    add_link(db, 1, 'resident', sample_key='res-win-0')
    add_link(db, 2, 'resident', sample_key='res-win-0', reused=1)
    # Pruned: row gone, 5 native wins of 4 recorded under experiment 2's frozen scenario.
    for i in range(5):
        add_sample(db, 'pruned-%d' % i, 'pruned', seeds=(400 + i, 500 + i), result_json=result(),
                   outcome_json=outcome(1, 4))
        add_link(db, 2, 'pruned', seeds=(400 + i, 500 + i), sample_key='pruned-%d' % i)
    # Ambiguous: one sample, two disagreeing frozen intents.
    add_sample(db, 'ambig-0', 'ambig', seeds=(600, 601), result_json=result(),
               outcome_json=outcome(1, 9))
    add_link(db, 3, 'ambig', seeds=(600, 601), sample_key='ambig-0')
    add_link(db, 4, 'ambig', seeds=(600, 601), sample_key='ambig-0')
    # Multi: the same build measured in two different windows -> separate windows, no combined mean.
    add_sample(db, 'multi-a', 'multi', window='development', seeds=(700, 701),
               result_json=result(), outcome_json=outcome(1, 10))
    add_sample(db, 'multi-b', 'multi', window='new', seeds=(702, 703), result_json=result(),
               outcome_json=outcome(1, 0))
    db.execute('INSERT INTO meta(key,value) VALUES(?,?)', ('recordHolders', json.dumps({
        'earned:19:0': dict(candidate='gone', label='Frozen best', encounterId=19,
                            defeatCount=0, mathSeed=901, libSeed=902, value=52,
                            scenario=dict(encounterId=19, defeatCount=0, mathSeed=901,
                                          libSeed=902, ownUnits=[]))})))
    db.commit()


def check_fixture(failures):
    path, db = build_fixture()
    seed_fixture(db)
    db.close()
    before = hash_of(path)
    real = readonly(path)
    try:
        view = ov.LedgerCache().view(real)
        check(view['counts']['unmapped'] == 1,
              'expected exactly the ambiguous candidate unmapped, got %r' % (view['counts'],))
        check(view['counts']['total'] == 21, 'sample total %r' % view['counts']['total'])
        evidence = ov.LedgerCache().fight_evidence(19, 0, db=real)
        by_id = {entry['candidateId']: entry for entry in evidence['candidates']}
        check('pruned' in by_id, 'the pruned candidate was not attributed to its frozen fight')
        check(by_id['pruned']['attempts'] == 5 and by_id['pruned']['meanEarned'] == 4,
              'pruned evidence wrong: %r' % by_id['pruned'])
        check(by_id['ambig' if 'ambig' in by_id else 'resident']['candidateId'] == 'resident',
              'an ambiguous candidate was attributed to a fight')
        resident = by_id['resident']
        check(resident['attempts'] == 12 and resident['earnedCount'] == 12,
              'resident attempts %r' % resident['attempts'])
        check(len(resident['windows']) == 1 and resident['meanEarned'] == 5,
              'resident mean wrong: %r' % resident)
        check(resident['windows'][0]['earnedMax'] == 6,
              'window maximum was not calculated from its resolved outcomes: %r' % resident)
        check(by_id['unknowny']['unknownOutcome'] == 1 and by_id['unknowny']['meanEarned'] is None,
              'an outcome-less sample was folded into a mean: %r' % by_id['unknowny'])
        multi = by_id['multi']
        check(len(multi['windows']) == 2 and multi['meanEarned'] is None,
              'two incompatible windows were pooled: %r' % multi)
        check({w['measurementWindow'] for w in multi['windows']} == {'development', 'new'},
              'window provenance lost: %r' % multi['windows'])
        cache = ov.LedgerCache()
        candidate = cache.candidate_evidence('pruned', db=real)
        check(candidate['attempts'] == 5 and candidate['meanEarned'] == 4 and
              len(candidate['windows']) == 1, 'candidate evidence wrong: %r' % candidate)
        index = ov.FrozenIntentIndex()
        check(index.scenario(real, 'ambig') is None, 'an ambiguous candidate was recovered anyway')
        found = index.scenario(real, 'pruned')
        check(found and (found['encounterId'], found['defeatCount']) == (19, 0),
              'frozen recovery wrong: %r' % found)
        check(found['scenario'] == dict(encounterId=19, defeatCount=0, mathSeed=7, libSeed=8),
              'public frozen-scenario API stopped returning the exact observed scenario')
        compact = ov.FrozenIntentIndex()
        proof = compact.identity(real, 'pruned')
        check(proof == dict(basis='ea-development-intent', experimentId=2,
                            encounterId=19, defeatCount=0),
              'compact frozen identity wrong: %r' % proof)
        check(compact._identities.get(2) == {'pruned': (19, 0)}
              and not hasattr(compact, '_intents'),
              'long-lived frozen index retained full intent/scenario payloads')
    finally:
        real.close()
    check(hash_of(path) == before, 'the ea_* ledger fixture was modified by a read')


    # A rolled replay window can have a correct retained mean but an incomplete retained max. The
    # lifetime counters own both metrics when they own the lifetime mean.
    summary = dict(meanEarned=30.0, earnedSamples=600, bestEarned=30,
                   meanPotential=None, potentialSamples=0, bestPotential=None,
                   attempts=600, wins=600, losses=0, noVerdict=0, winRate=1.0)
    counters = dict(n=600, wins=600, losses=0, censored=0, chestCount=600,
                    chestSum=18000, chestMax=58, earnedMax=58)
    measured = desktop._aggregate_measure(summary, counters, retained_count=512)
    check(measured['meanEarned'] == 30 and measured['bestEarned'] == 58,
          'lifetime mean/max diverged after replay retention: %r' % measured)
    evidence = dict(candidateId='mixed', attempts=8, wins=8, losses=0, noVerdict=0,
                    earnedCount=8, meanEarned=30.375, potentialCount=0, meanPotential=None,
                    comparable=True, windows=[dict(earnedCount=8, meanEarned=30.375,
                                                   earnedMax=52)])
    row = dict(attempts=68, wins=67, losses=1, noVerdict=0, earnedSamples=68,
               meanEarned=None, comparable=False)
    desktop._apply_ea_evidence(row, evidence)
    check(row['eaMeanEarned'] == 30.375 and row['eaBestEarned'] == 52
          and row['eaEarnedSamples'] == 8,
          'legacy samples hid the compatible Community best/mean window: %r' % row)

    bridge = bridge_for(path)
    listing = bridge.encounter_strategies(19, 0)
    check(listing.get('ok') is True, 'encounter_strategies failed: %r' % listing.get('error'))
    rows = {str(entry['candidateId']): entry for entry in listing['strategies']}
    check('pruned' in rows, 'the EA-only candidate got no navigable ranked row')
    check(rows['pruned']['attempts'] == 5 and rows['pruned']['earnedSamples'] == 5,
          'the EA row still reads as unmeasured: %r' % rows['pruned'])
    check(rows['pruned']['meanEarned'] == 4 and rows['pruned']['bestEarned'] == 4,
          'the EA row mean/best do not share canonical samples: %r' % rows['pruned'])
    for candidate_id, expected_mean, expected_max, count in (
            ('resident', 5, 6, 12), ('pruned', 4, 4, 5)):
        row = rows[candidate_id]
        check(row['earnedSamples'] == count and row['meanEarned'] == expected_mean
              and row['bestEarned'] == expected_max,
              'mean/best mismatch for %s: %r' % (candidate_id, row))
    check(rows['multi']['meanEarned'] is None and rows['multi']['bestEarned'] is None,
          'incompatible windows produced a top-level earned metric: %r' % rows['multi'])
    check('ambig' not in rows, 'an unattributable candidate was given a ranked row')
    detail = bridge.strategy_detail(candidate_id='pruned')
    check(detail.get('ok') is True and detail.get('resident') is False,
          'pruned detail failed: %r' % detail.get('error'))
    check(detail['scenario'].get('encounterId') == 19 and detail['storedRuns'] == [],
          'pruned detail fabricated stored history: %r' % detail)
    ledger = detail.get('encounterLedger') or {}
    check(ledger.get('attempts') == 5 and ledger.get('earnedCount') == 5,
          'pruned detail hid its EA readings: %r' % ledger)
    frozen = bridge.strategy_detail(holder_key='earned:19:0')
    check(frozen.get('ok') is True and frozen.get('resident') is False
          and frozen.get('scenario', {}).get('encounterId') == 19,
          'pruned overview holder did not resolve by its own frozen key: %r' % frozen)
    missing = bridge.strategy_detail(candidate_id='no-such-candidate')
    check(missing.get('ok') is False, 'a candidate with no frozen scenario was accepted')
    bridge._close_encounter_ledger()
    check(hash_of(path) == before, 'the bridge writes to the ea_* ledger')


def check_real_library(failures):
    if not REAL.is_file():
        failures.append('the exported real library is missing: %s' % REAL)
        return None
    WORK.mkdir(parents=True, exist_ok=True)
    local = WORK / 'lib.sqlite'
    shutil.copyfile(REAL, local)
    before = hash_of(local)
    bridge = bridge_for(local)
    listing = bridge.encounter_strategies(19, 0)
    check(listing.get('ok') is True, 'real library listing failed: %r' % listing.get('error'))
    rows = {str(entry['candidateId']): entry for entry in listing['strategies']}
    check(rows and not all(int(entry['attempts']) == 0 for entry in rows.values()),
          'every real EA build still read as 0 attempts')
    cache = ov.LedgerCache()
    real_db = readonly(local)
    try:
        evidence = cache.fight_evidence(19, 0, db=real_db)
    finally:
        real_db.close()
    check(evidence and evidence['candidates'], 'no real per-candidate evidence was folded')
    holder_ids = [entry['candidateId'] for entry in evidence['candidates']
                  if entry['earnedCount'] or entry['potentialCount']]
    check(holder_ids, 'no measured real candidate to resolve')
    target = holder_ids[0]
    check(target in rows, 'an EA holder id did not resolve to a ranked row: %r' % target)
    detail = bridge.strategy_detail(candidate_id=target)
    check(detail.get('ok') is True, 'real EA candidate detail failed: %r' % detail.get('error'))
    ledger = detail.get('encounterLedger') or {}
    check(int(ledger.get('attempts') or 0) > 0, 'real EA detail hid the readings: %r' % ledger)
    bridge._close_encounter_ledger()
    check(hash_of(local) == before, 'the real library copy was modified by a read')
    return dict(rows=len(rows), target=target, attempts=ledger.get('attempts'))


def check_diagnostics():
    """A compact extraction of the exported diagnostics, never a full dump."""
    if not DIAGNOSTICS.is_file():
        return None
    with DIAGNOSTICS.open('r', encoding='utf-8') as handle:
        payload = json.load(handle)
    return dict(source=(payload.get('counts') or {}).get('source'),
                included=(payload.get('counts') or {}).get('included'),
                unmapped=(payload.get('optimizer') or {}).get('encounterCoverage', {}).get('unmapped'))


def check_scratch():
    """Read-only pass over the 52-native Community scratch ledger when the sandbox allows it."""
    try:
        if not SCRATCH.is_file():
            return 'unavailable'
        before = hash_of(SCRATCH)
        db = readonly(SCRATCH)
        try:
            view = ov.LedgerCache().view(db)
            check(view['counts']['total'] == 52 and view['counts']['native'] == 52,
                  'scratch ledger counts: %r' % view['counts'])
            check(view['rows']['15:0']['attempts'] == 52,
                  'scratch fight 15:0 attempts: %r' % view['rows'].get('15:0'))
        finally:
            db.close()
        check(hash_of(SCRATCH) == before, 'the scratch ledger was modified by a read')
        return 'read-only pass (52 native)'
    except PermissionError:
        return 'denied by the sandbox'


def main():
    failures = []
    checks = 0
    for name, fn in (('fixture', check_fixture), ('real', check_real_library)):
        try:
            fn(failures)
            checks += 1
        except Exception as exc:  # noqa: BLE001 - a raised check is a failure, never a skip
            failures.append('%s: %s: %s' % (name, type(exc).__name__, exc))
    scratch_note = 'not run'
    try:
        scratch_note = check_scratch()
    except Exception as exc:  # noqa: BLE001
        failures.append('scratch: %s: %s' % (type(exc).__name__, exc))
    diagnostics = None
    try:
        diagnostics = check_diagnostics()
    except Exception as exc:  # noqa: BLE001
        failures.append('diagnostics: %s: %s' % (type(exc).__name__, exc))
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    lines = ['# Community strategy readers report', '',
             '- checks run: %d' % checks,
             '- scratch db (%s): %s' % (SCRATCH, scratch_note),
             '- exported diagnostics: %s' % (diagnostics,)]
    if failures:
        lines.append('- FAILURES:')
        lines += ['  - ' + row for row in failures]
    else:
        lines.append('- result: PASS (frozen-intent mapping, unmeasured-0 fix, window separation, '
                     'read-only)')
    REPORT.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    if failures:
        print('FAILURES:')
        for row in failures:
            print('  ' + row)
        return 1
    print('PASS community readers: frozen-intent mapping, measured EA rows, window separation, '
          'read-only')
    return 0


if __name__ == '__main__':
    sys.exit(main())
