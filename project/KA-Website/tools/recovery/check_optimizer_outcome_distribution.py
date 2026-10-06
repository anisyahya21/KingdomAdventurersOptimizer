"""Focused checks for the desktop detail payload's empirical chest distribution.

`strategy_detail` now carries `outcomeDistribution` for a selected resident build: the empirical
validation histogram read from the compact persistent `evidence` table (never by decoding a replay
row), grouped in SQL by the outcome fields `chest_count` consumes and interpreted with that same
rule. These checks drive tiny temporary self-contained SQLite libraries:

  * the bins/counts and min/max/mean/p10/median/p90/mode are those of the observed readings, with a
    resolved defeat contributing the native loss gate's zero;
  * a censored run and a resolved-but-unreadable run are counted as unresolved/unknown and are NOT
    folded into the histogram as a zero bucket, and a build with no known reading reports no
    statistics rather than zeroes;
  * coverage is stated: a lifetime validation total larger than the compact row count reports
    `partial` with the number of observations a legacy library lost, and a library without the
    evidence table is `unavailable`;
  * a frozen orphan holder (its candidate pruned) and a build whose evidence was never written return
    `available=False` instead of a fake distribution, and the fresh-trial sidecar is never merged in.

    python check_optimizer_outcome_distribution.py
"""
import json
import os
import shutil
import sqlite3
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import strategy_evidence  # noqa: E402
import strategy_optimizer as optimizer  # noqa: E402
import strategy_optimizer_desktop as desktop  # noqa: E402
from strategy_optimizer_adapter import provenance  # noqa: E402

ENCOUNTER = 250
HOLDER_KEY = f'earned:{ENCOUNTER}:0'


def scenario(marker=0):
    return dict(encounterId=ENCOUNTER, defeatCount=0, tickLimit=120,
                finishPolicy='terminal-verdict',
                ownUnits=[dict(name=f'Build {marker}', human=True, weaponId=1, skills=[1, 2],
                               invocationLevels=[0, 0], grid=0,
                               parameters={13: {'rawValue': 100+marker}})],
                enemies=[])


def result(verdict, seeds, *, awarded=None, pending=None, prize=None, digest='x'):
    """A compact run result shaped like the runner's, so the store's own gates are exercised."""
    return dict(verdict=verdict, censored=verdict is None, ticks=40, prizeCallbacks=prize,
                retained=None, survivors=1, resourceUses=0, elapsedSeconds=.01,
                seeds=list(seeds), digest=digest,
                rewardOutcome=dict(pendingChests=pending, awardedChests=awarded, awardedBasis=None,
                                   inventoryVerified=False, reason=None))


def bridge_for(path):
    """A Bridge with only the read attributes the detail APIs touch - no GUI, lock or optimizer."""
    bridge = desktop.Bridge.__new__(desktop.Bridge)
    bridge._library = Path(path)
    bridge._sidecar_lock = threading.RLock()
    bridge._average_lock = threading.Lock()
    bridge._average_summaries = {}
    return bridge


def check(condition, message, failures):
    if not condition:
        failures.append(message)


def close(actual, expected, what, failures):
    if actual is None or abs(float(actual)-float(expected)) > 1e-9:
        failures.append(f'{what}: got {actual!r}, expected {expected!r}')


def build_evidence_library(path):
    """A resident build whose validation evidence mixes every outcome `chest_count` distinguishes.

    Rows, in ordinal order: two 3-chest wins, one 5-chest win, two losses (the loss gate's zero), one
    win that carries no reading at all (unknown), and one censored fight (unresolved). So the known
    readings are 0,0,3,3,5 - seven compact rows, five of them measured - which pin every statistic.
    """
    store = optimizer.Store(path, provenance())
    with store.db:
        store.set('scope', optimizer.scope(scenario()))
        cid = store.add(scenario(), 'Distribution build', 'mutation', {})
    rows = [result(1, (1, 2), awarded=3, pending=3, prize=3, digest='w3a'),
            result(1, (3, 4), awarded=3, pending=3, prize=3, digest='w3b'),
            result(1, (5, 6), awarded=5, pending=5, prize=5, digest='w5'),
            result(2, (7, 8), pending=9, prize=9, digest='loss-a'),
            result(2, (9, 10), pending=4, prize=4, digest='loss-b'),
            result(1, (11, 12), digest='unreadable'),
            result(None, (13, 14), pending=6, prize=6, digest='censored')]
    for ordinal, row in enumerate(rows):
        store.record(cid, 'validation', ordinal, row)
    with store.db:
        store.set('recordHolders', {HOLDER_KEY: optimizer.record_holder(
            scenario(), cid, 'validation', 2, (5, 6), 1, 5, 'w5')})
    store.close()
    return cid


def check_histogram(failures, root):
    path = root/'resident.sqlite'
    cid = build_evidence_library(path)
    bridge = bridge_for(path)
    detail = bridge.strategy_detail(candidate_id=cid)
    check(detail.get('ok') is True, f'detail refused: {detail.get("error")!r}', failures)
    if not detail.get('ok'):
        return
    distribution = detail.get('outcomeDistribution')
    if not isinstance(distribution, dict):
        failures.append(f'no outcomeDistribution in the detail payload: {sorted(detail)}')
        return
    check(distribution.get('available') is True, f'distribution unavailable: {distribution}', failures)
    check(distribution.get('phase') == 'validation', 'distribution phase is not validation', failures)
    check(distribution.get('bins') == [dict(chests=0, count=2), dict(chests=3, count=2),
                                       dict(chests=5, count=1)],
          f'bins are wrong: {distribution.get("bins")}', failures)
    check(distribution.get('knownSamples') == 5, 'known sample count is wrong', failures)
    check(distribution.get('evidenceRows') == 7, 'evidence row count is wrong', failures)
    check(distribution.get('lifetimeValidationTotal') == 7, 'lifetime validation total is wrong',
          failures)
    check(distribution.get('unresolved') == 1 and distribution.get('unknown') == 1,
          f'unresolved/unknown counts are wrong: {distribution}', failures)
    check(distribution.get('coverage') == 'complete' and distribution.get('missingEvidence') == 0,
          f'coverage is wrong: {distribution}', failures)
    check(distribution.get('min') == 0 and distribution.get('max') == 5,
          'min/max are wrong', failures)
    close(distribution.get('mean'), 11/5, 'mean', failures)
    # p10 sits inside the two-zero bucket (ranks 0 and 1), while p90 interpolates between 3 and 5.
    close(distribution.get('p10'), 0.0, 'p10', failures)
    close(distribution.get('median'), 3.0, 'median', failures)
    close(distribution.get('p90'), 4.2, 'p90', failures)
    check(distribution.get('mode') == 0, f'mode tie-break is wrong: {distribution.get("mode")}',
          failures)
    # The histogram never needs a replay row: the compact evidence alone produced it.
    check(len(detail.get('storedRuns') or []) == 7,
          'the detail payload no longer returns the retained replay rows', failures)
    check(detail['storedSummary']['meanEarned'] is not None,
          'the existing stored summary changed', failures)
    return bridge, path, cid


def check_partial_coverage(failures, bridge, path, cid):
    """A legacy library whose rows rolled off before evidence existed reports partial coverage."""
    db = sqlite3.connect(path)
    key = f'aggregate:{cid}:validation'
    total = json.loads(db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()[0])
    total['n'] = int(total['n'])+3
    db.execute('UPDATE meta SET value=? WHERE key=?', (json.dumps(total), key))
    db.commit()
    db.close()
    distribution = bridge.strategy_detail(candidate_id=cid).get('outcomeDistribution') or {}
    check(distribution.get('coverage') == 'partial',
          f'partial coverage not reported: {distribution}', failures)
    check(distribution.get('missingEvidence') == 3 and distribution.get('evidenceRows') == 7,
          f'missing-evidence count is wrong: {distribution}', failures)
    # A build with no lifetime counter at all reports unknown coverage, not a silent "complete".
    db = sqlite3.connect(path)
    db.execute('DELETE FROM meta WHERE key=?', (key,))
    db.commit()
    db.close()
    distribution = bridge.strategy_detail(candidate_id=cid).get('outcomeDistribution') or {}
    check(distribution.get('coverage') == 'unknown'
          and distribution.get('lifetimeValidationTotal') is None,
          f'unknown coverage not reported: {distribution}', failures)


def check_no_known_reading(failures, root):
    """Censored and unreadable rows are never a zero bucket, and no reading means no statistic."""
    path = root/'unmeasured.sqlite'
    store = optimizer.Store(path, provenance())
    with store.db:
        store.set('scope', optimizer.scope(scenario(1)))
        cid = store.add(scenario(1), 'Unmeasured build', 'mutation', {})
    store.record(cid, 'validation', 0, result(None, (1, 2), pending=4, prize=4))
    store.record(cid, 'validation', 1, result(None, (3, 4), pending=8, prize=8))
    store.record(cid, 'validation', 2, result(1, (5, 6), digest='no-reading'))
    store.close()
    distribution = (bridge_for(path).strategy_detail(candidate_id=cid)
                    .get('outcomeDistribution') or {})
    check(distribution.get('available') is True, f'distribution unavailable: {distribution}', failures)
    check(distribution.get('bins') == [], f'unmeasured rows became a bin: {distribution}', failures)
    check(distribution.get('knownSamples') == 0 and distribution.get('evidenceRows') == 3,
          f'known/evidence counts are wrong: {distribution}', failures)
    check(distribution.get('unresolved') == 2 and distribution.get('unknown') == 1,
          f'unresolved/unknown counts are wrong: {distribution}', failures)
    for field in ('min', 'max', 'mean', 'p10', 'median', 'p90', 'mode'):
        check(distribution.get(field) is None, f'{field} was invented as {distribution.get(field)!r}',
              failures)


def check_unavailable(failures, root):
    """A pruned holder, an evidence-less build and a legacy library all report unavailable."""
    path = root/'orphan.sqlite'
    cid = build_evidence_library(path)
    db = sqlite3.connect(path)
    db.execute('DELETE FROM candidate WHERE id=?', (cid,))
    db.commit()
    db.close()
    db = sqlite3.connect(path)
    strategy_evidence.delete_candidate(db, cid)
    db.commit()
    db.close()
    detail = bridge_for(path).strategy_detail(holder_key=HOLDER_KEY)
    check(detail.get('ok') is True and detail.get('resident') is False,
          f'the orphan holder did not resolve: {detail.get("error")!r}', failures)
    distribution = detail.get('outcomeDistribution') or {}
    check(distribution.get('available') is False and not distribution.get('bins'),
          f'a frozen orphan holder was given a distribution: {distribution}', failures)

    # The evidence table exists but this build has no row in it: the run rows must not be decoded as
    # a substitute distribution, so the answer stays unavailable.
    path = root/'run-only.sqlite'
    cid = build_evidence_library(path)
    db = sqlite3.connect(path)
    strategy_evidence.delete_candidate(db, cid)
    db.commit()
    db.close()
    distribution = (bridge_for(path).strategy_detail(candidate_id=cid)
                    .get('outcomeDistribution') or {})
    check(distribution.get('available') is False and distribution.get('reason'),
          f'run rows were used as a distribution: {distribution}', failures)

    # A library written before the evidence table existed.
    path = root/'legacy.sqlite'
    db = sqlite3.connect(path)
    db.executescript('''
        CREATE TABLE candidate(id TEXT PRIMARY KEY, scenario TEXT NOT NULL, label TEXT NOT NULL,
            source TEXT NOT NULL, stats TEXT NOT NULL, created INTEGER NOT NULL);
        CREATE TABLE run(candidate TEXT NOT NULL, phase TEXT NOT NULL, ordinal INTEGER NOT NULL,
            result TEXT NOT NULL, PRIMARY KEY(candidate, phase, ordinal));
        CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE candidate_meta(id TEXT PRIMARY KEY, encounter INTEGER NOT NULL,
            defeat INTEGER NOT NULL, region TEXT NOT NULL);''')
    db.execute('INSERT INTO candidate VALUES (?,?,?,?,?,0)',
               ('legacy', json.dumps(scenario(2)), 'Legacy build', 'mutation', '{}'))
    db.execute('INSERT INTO meta VALUES (?,?)', ('recordHolders', '{}'))
    db.execute('INSERT INTO run VALUES (?,?,?,?)',
               ('legacy', 'validation', 0, json.dumps(result(1, (1, 2), awarded=7, pending=7))))
    db.commit()
    db.close()
    distribution = (bridge_for(path).strategy_detail(candidate_id='legacy')
                    .get('outcomeDistribution') or {})
    check(distribution.get('available') is False,
          f'a legacy library produced a distribution: {distribution}', failures)


def main():
    failures = []
    base = HERE/'tmp'
    base.mkdir(parents=True, exist_ok=True)
    root = base/f'ka-outcome-distribution-{os.getpid()}-{time.time_ns()}'
    root.mkdir(parents=True, exist_ok=True)
    try:
        built = check_histogram(failures, root)
        if built:
            check_partial_coverage(failures, *built)
        check_no_known_reading(failures, root)
        check_unavailable(failures, root)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    if failures:
        print('FAILURES:')
        for row in failures:
            print('  '+row)
        return 1
    print('  desktop outcome distribution ok: SQL-grouped compact validation evidence, shared '
          'chest_count rule, honest unknown/unresolved and partial-coverage reporting, '
          'unavailable for pruned holders and legacy libraries')
    return 0


if __name__ == '__main__':
    sys.exit(main())
