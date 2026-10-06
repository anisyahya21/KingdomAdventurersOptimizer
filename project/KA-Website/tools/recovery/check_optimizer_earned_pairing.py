"""Focused checks for the desktop optimiser's encounter-aware earned aggregation.

Best Earned must be the maximum ``finalEarned`` of exactly the compatible resolved canonical outcome
set that already supplies Mean Earned: same candidate, fight, policy, mechanics/revision and
measurement window. ``_aggregate_measure``, ``_apply_ea_evidence`` and ``_ea_strategy_row`` are driven
on a tiny scratch ea_* ledger built from the reported Community/Ninja readings as exact-window
fixtures:

  * n=8, mean 30.38, max 52; n=40, mean 28.93, max 58; n=20, mean 26.60, max 47.

The checks prove:

  * each fixture's mean and max come from the same window, and the top-level and Community pairs agree;
  * a known encounter-aware mean never pairs with an unknown best (the invariant);
  * an incompatible window's higher maximum never leaks into the compatible window's pair, and two
    incompatible windows withhold the pair instead of pooling a combined average;
  * a resolved loss contributes the loss gate's zero to mean earned while its ``pending`` opportunity
    can never become the earned maximum (Best Potential stays loss-only);
  * a legacy-only build (no ea_* evidence) keeps its legacy lifetime mean/max but never has those
    legacy readings published as a Community window;
  * every read is byte-for-byte read-only.

    python check_optimizer_earned_pairing.py
"""
import hashlib
import json
import sqlite3
import sys
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))

import strategy_encounter_overview as ov  # noqa: E402
import strategy_optimizer_desktop as desktop  # noqa: E402

WORK = REPO / 'tmp' / 'encounter-redesign-20260928' / 'check-earned-pairing'

_SCHEMA = '''
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE candidate(id TEXT PRIMARY KEY, scenario TEXT NOT NULL, label TEXT NOT NULL,
                       source TEXT NOT NULL, stats TEXT NOT NULL, created INTEGER NOT NULL);
CREATE TABLE candidate_meta(id TEXT PRIMARY KEY, encounter INTEGER NOT NULL,
                            defeat INTEGER NOT NULL, region TEXT NOT NULL);
CREATE TABLE run(candidate TEXT NOT NULL, phase TEXT NOT NULL, ordinal INTEGER NOT NULL,
                 result TEXT NOT NULL, PRIMARY KEY(candidate, phase, ordinal));
CREATE TABLE ea_sample(
    sample_key TEXT PRIMARY KEY, candidate_id TEXT NOT NULL, policy TEXT, mechanics_revision TEXT,
    encounter_revision TEXT, seed_a INTEGER NOT NULL, seed_b INTEGER NOT NULL,
    measurement_window TEXT, result TEXT, outcome TEXT, timing TEXT, created_at REAL NOT NULL);
'''


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def readonly(path):
    return sqlite3.connect('file:%s?mode=ro' % str(path).replace('\\', '/'), uri=True)


def hash_of(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


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


def add_scenario(db, cid, encounter_id=19, defeat=0, label='build'):
    scenario = dict(encounterId=encounter_id, defeatCount=defeat)
    db.execute('INSERT INTO candidate(id,scenario,label,source,stats,created) VALUES(?,?,?,?,?,?)',
               (cid, json.dumps(scenario), label, 'community', '{}', 0))
    db.execute('INSERT INTO candidate_meta VALUES(?,?,?,?)', (cid, encounter_id, defeat, 'region'))


def result(seeds=(1, 2), backend='native'):
    return json.dumps(dict(resultBackend=backend, seeds=list(seeds),
                           rewardOutcome=dict(pendingChests=0)))


def outcome(verdict, earned, pending=0, *, resolved=True, censored=False, error=False):
    return json.dumps(dict(verdict=verdict, finalEarned=earned, pending=pending, resolved=resolved,
                           censored=censored, error=error, engineRevision='engine-a'))


def add_sample(db, key, cid, *, window='development', seeds=(1, 2), result_json=None,
               outcome_json=None):
    db.execute('INSERT INTO ea_sample(sample_key,candidate_id,policy,mechanics_revision,'
               'encounter_revision,seed_a,seed_b,measurement_window,result,outcome,timing,created_at)'
               ' VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
               (key, cid, '{"p":1}', 'm1', 'e1', seeds[0], seeds[1], window, result_json,
                outcome_json, None, 1000.0))


def bridge_for(path):
    bridge = desktop.Bridge.__new__(desktop.Bridge)
    bridge._library = Path(path)
    bridge._sidecar_lock = threading.RLock()
    bridge._ledger_lock = threading.RLock()
    return bridge


def check_earned_pairing():
    path, db = fresh('earned-pairing')
    for cid in ('fx8', 'fx40', 'fx20', 'fx8-extra', 'mixed-two', 'pending-loss', 'legacy-only'):
        add_scenario(db, cid, 19, 0, label=cid)

    def add_wins(cid, values, window='development', prefix=None):
        for index, value in enumerate(values):
            add_sample(db, '%s-%d' % (prefix or cid, index), cid, window=window,
                       seeds=(index, index + 1000), result_json=result(),
                       outcome_json=outcome(1, value))

    # The three reported fixtures, as exact compatible windows.
    fx8 = [52, 40, 40, 30, 30, 30, 20, 1]                       # n=8, sum 243, mean 30.375
    fx40 = [58] + [28] * 38 + [35]                               # n=40, sum 1157, mean 28.925
    fx20 = [47] + [26] * 18 + [17]                               # n=20, sum 532, mean 26.60
    add_wins('fx8', fx8)
    add_wins('fx40', fx40, prefix='fx40')
    add_wins('fx20', fx20, prefix='fx20')
    # The same compatible window plus a DIFFERENT window whose maximum (999) must not leak in.
    add_wins('fx8-extra', fx8, prefix='fx8-extra-dev')
    add_wins('fx8-extra', [999], window='new', prefix='fx8-extra-new')
    # Two incompatible windows: no top-level pair may be fabricated.
    add_wins('mixed-two', [10], prefix='mixed-a')
    add_wins('mixed-two', [999], window='new', prefix='mixed-b')
    # A compatible window with a resolved loss carrying a large pending opportunity: the loss gate
    # zero joins the mean and its pending can never become the earned maximum.
    add_sample(db, 'pending-win', 'pending-loss', seeds=(910, 911), result_json=result(),
               outcome_json=outcome(1, 10))
    add_sample(db, 'pending-loss-0', 'pending-loss', seeds=(912, 913), result_json=result(),
               outcome_json=outcome(2, 0, pending=999))
    # A legacy-only build: lifetime chest counters, no ea_* evidence at all.
    db.execute('INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)',
               ('aggregate:legacy-only:validation',
                json.dumps(dict(n=40, wins=40, losses=0, censored=0, chestCount=40, chestSum=1157,
                                chestMax=58, earnedMax=58))))
    db.commit()
    db.close()

    def stats(values):
        return dict(n=len(values), mean=sum(values) / len(values), max=max(values))

    before = hash_of(path)
    real = readonly(path)
    try:
        evidence = ov.LedgerCache().fight_evidence(19, 0, db=real)
    finally:
        real.close()
    by_id = {entry['candidateId']: entry for entry in evidence['candidates']}
    for cid, values in (('fx8', fx8), ('fx40', fx40), ('fx20', fx20)):
        want = stats(values)
        entry = by_id[cid]
        check(entry['earnedCount'] == want['n']
              and abs(entry['meanEarned'] - want['mean']) < 1e-9
              and entry['earnedMax'] == want['max'],
              '%s compatible window drifted: %r want %r' % (cid, entry, want))
        row = desktop._apply_ea_evidence(dict(attempts=0, wins=0, losses=0, noVerdict=0,
                                              earnedSamples=0, comparable=False), entry)
        check(row['earnedSamples'] == want['n']
              and abs(row['meanEarned'] - want['mean']) < 1e-9
              and row['bestEarned'] == want['max'],
              '%s top-level mean/best do not share a set: %r' % (cid, row))
        # Invariant: a known encounter-aware mean always travels with a known best.
        check(row['eaMeanEarned'] is not None and row['eaBestEarned'] == want['max']
              and row['eaEarnedSamples'] == want['n'],
              '%s Community window mean/best not paired: %r' % (cid, row))
        ea_row = desktop._ea_strategy_row(entry, None)
        check(ea_row['meanEarned'] == row['meanEarned'] and ea_row['bestEarned'] == row['bestEarned']
              and ea_row['eaMeanEarned'] == row['eaMeanEarned']
              and ea_row['eaBestEarned'] == row['eaBestEarned'],
              '%s EA-only row disagrees with the folded row: %r' % (cid, ea_row))
    # The incompatible window's maximum is excluded from the compatible window's pair.
    extra = by_id['fx8-extra']
    compatible = [window for window in extra['windows'] if window['earnedCount'] == len(fx8)]
    check(len(compatible) == 1 and compatible[0]['earnedMax'] == 52,
          'the compatible window maximum was polluted by an incompatible window: %r'
          % (extra['windows'],))
    check(extra['meanEarned'] is None and extra['earnedMax'] == 999,
          'incompatible windows pooled a top-level mean: %r' % extra)
    # A loss pending reading is potential, never earned.
    pending = by_id['pending-loss']
    check(pending['meanEarned'] == 5.0 and pending['earnedMax'] == 10
          and pending['meanPotential'] == 999.0,
          'a loss pending reading leaked into the earned pair: %r' % pending)

    bridge = bridge_for(path)
    listing = bridge.encounter_strategies(19, 0)
    check(listing.get('ok') is True, 'earned-pairing listing failed: %r' % listing.get('error'))
    rows = {str(entry['candidateId']): entry for entry in listing['strategies']}
    for cid, values in (('fx8', fx8), ('fx40', fx40), ('fx20', fx20)):
        want = stats(values)
        row = rows[cid]
        check(row['meanEarned'] == want['mean'] and row['bestEarned'] == want['max']
              and row['eaMeanEarned'] == want['mean'] and row['eaBestEarned'] == want['max']
              and row['eaEarnedSamples'] == want['n'],
              'ranked row mean/best do not share the compatible set: %r' % row)
    for cid, row in rows.items():
        if row.get('eaMeanEarned') is not None:
            check(row.get('eaBestEarned') is not None,
                  'known encounter-aware mean paired with unknown best: %r' % row)
        if row.get('meanEarned') is not None and row.get('bestEarned') is not None:
            check(row['bestEarned'] + 1e-9 >= row['meanEarned'],
                  'best earned below mean earned: %r' % row)
    check(rows['mixed-two']['meanEarned'] is None and rows['mixed-two']['bestEarned'] is None
          and rows['mixed-two']['eaBestEarned'] is None,
          'incompatible windows produced a top-level earned pair: %r' % rows['mixed-two'])
    check(rows['pending-loss']['bestEarned'] == 10,
          'pending leaked into the ranked best earned: %r' % rows['pending-loss'])
    bridge._close_encounter_ledger()
    check(hash_of(path) == before, 'the earned-pairing ledger was modified by a read')

    # A legacy-only build keeps its legacy mean/max but never seeds the encounter-aware pair.
    legacy = desktop._aggregate_measure(desktop._summarize_attempts([]),
                                        dict(n=40, wins=40, losses=0, censored=0, chestCount=40,
                                             chestSum=1157, chestMax=58, earnedMax=58), 0)
    check(legacy['meanEarned'] == 28.925 and legacy['bestEarned'] == 58,
          'legacy-only mean/max were not preserved: %r' % legacy)
    check(legacy.get('eaMeanEarned') is None and legacy.get('eaBestEarned') is None
          and not legacy.get('eaEarnedSamples'),
          'legacy counters were published as a Community window: %r' % legacy)


def main():
    try:
        check_earned_pairing()
    except Exception as exc:  # noqa: BLE001 - a raised check is a failure, never a skip
        print('FAIL: %s: %s' % (type(exc).__name__, exc))
        return 1
    print('PASS earned pairing: exact-window fixtures, known-mean/known-best invariant, '
          'incompatible-window isolation, pending excluded, loss-only potential, legacy preserved, '
          'read-only')
    return 0


if __name__ == '__main__':
    sys.exit(main())
