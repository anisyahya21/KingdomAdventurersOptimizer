"""Focused checks for the Strategy Optimiser overview's per-encounter average leaders.

Builds one tiny temporary SQLite library and drives `Bridge.overview_average_leaders` against it just
as the desktop page will, so the checks are deterministic and never touch a live library:

  * a fight's average needs at least `AVERAGE_MIN_SAMPLES` qualifying observations for the same
    build - ten defeats that carried a measured opportunity for mean potential, ten resolved runs
    with an earned reading for mean earned. A single high-potential defeat, however large, is a
    record rather than an average, and the fight reports unknown for that metric instead of crowning
    it (this is the "AVG POTENTIAL 119 n=1" case);
  * the boundary is exact and has teeth: nine qualifying defeats do not qualify, ten do, and a
    higher thin mean (40 over 9) never outranks a lower eligible one (3 over 10);
  * the two metrics stay separate: a defeat contributes the loss gate's zero to mean earned and its
    own measured opportunity to mean potential, at eligible sample counts too;
  * the lifetime maxima are a different path: a fight whose average is unknown still reports its
    highest single potential, so the record stays visible and clickable;
  * each eligible crown names the exact build, mean and sample count that produced it, and agrees
    with the numbers `encounter_strategies` shows for the same fight;
  * a build with no measured sample for a metric is excluded rather than ranked, and a fight with no
    measured build returns None instead of falling back to a best max;
  * ties resolve deterministically by larger sample count, then by candidate id;
  * a probe that truly holds the highest eligible mean is returned with its source exposed;
  * a pruned candidate's stale run rows are never resurrected as an average, while its frozen maximum
    record stays reachable through the separate holder path;
  * the call writes nothing to the optimiser library.

    python check_optimizer_overview_leaders.py
"""
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import strategy_optimizer as optimizer  # noqa: E402
import strategy_optimizer_desktop as desktop  # noqa: E402
from strategy_optimizer_adapter import provenance  # noqa: E402

ENCOUNTER = 125      # one high-potential defeat, a two-win probe: every mean is thinner than the floor
OTHER = 7            # a loss-only build and a mixed build, both below the floor
UNMEASURED = 9       # a censored run plus a win with no reading at all
TIE = 11             # equal means, unequal sample counts
PRUNED = 13          # a build deleted after the first assertions
QUALIFIED = 21       # the two eligible crowns plus one 119-chest single defeat that must not lead
EDGE = 23            # nine qualifying defeats against ten: the exact boundary

FLOOR = desktop.AVERAGE_MIN_SAMPLES
assert FLOOR == 10


def scenario(encounter, defeat=0, marker=0):
    return dict(encounterId=encounter, defeatCount=defeat, tickLimit=120,
                finishPolicy='terminal-verdict',
                ownUnits=[dict(name=f'Build {marker}', human=True, weaponId=1, skills=[1, 2],
                               invocationLevels=[0, 0], grid=0,
                               parameters={13: {'rawValue': 100+marker}})],
                enemies=[])


def result(verdict, *, seeds, digest, prize=0, pending=None, awarded=None):
    """A compact run result shaped like the runner's, so the store's own gates are exercised."""
    return dict(verdict=verdict, censored=verdict is None, ticks=40, prizeCallbacks=prize,
                retained=None, survivors=1, resourceUses=0, elapsedSeconds=.01,
                behavior=dict(heals=0, attacks=1, prizes=prize), seeds=list(seeds), digest=digest,
                rewardOutcome=dict(pendingChests=pending, awardedChests=awarded, awardedBasis=None,
                                   inventoryVerified=False, reason=None))


def record_batch(store, candidate_id, count, verdict, *, prize, pending=None, awarded=None, start=0,
                 first=0):
    """`count` runs of one verdict, each with its own seed pair, from ordinal `first`."""
    for index in range(count):
        store.record(candidate_id, 'discovery', first + index, result(
            verdict, seeds=(start + 2*index, start + 2*index + 1),
            digest=f'{candidate_id[:8]}-{verdict}-{index}', prize=prize, pending=pending,
            awarded=awarded))


def build_library(path):
    """Fights whose means and sample counts are known by hand, with the floor at 10 by construction.

    `QUALIFIED` carries the two eligible crowns and a one-defeat 119-chest opportunity that must stay
    a record; `EDGE` puts nine qualifying defeats next to ten; `ENCOUNTER`, `OTHER` and `TIE` keep
    every mean thinner than the floor or tied at it; `UNMEASURED` has no reading at all; `PRUNED`
    holds a build that is deleted after the first assertions.
    """
    store = optimizer.Store(path, provenance())
    ids = {}
    with store.db:
        store.set('scope', optimizer.scope(scenario(ENCOUNTER)))
        ids['record'] = store.add(scenario(ENCOUNTER, marker=1), 'Record build', 'mutation', {})
        ids['probe'] = store.add(scenario(ENCOUNTER, marker=2), 'Probe build', 'probe', {})
        ids['other'] = store.add(scenario(OTHER, marker=3), 'Other build', 'mutation', {})
        ids['loss_only'] = store.add(scenario(OTHER, defeat=1, marker=4), 'Loss-only build',
                                     'mutation', {})
        ids['unmeasured'] = store.add(scenario(UNMEASURED, marker=5), 'Unmeasured build',
                                      'mutation', {})
        ids['thin'] = store.add(scenario(TIE, marker=6), 'Thin mean build', 'mutation', {})
        ids['thick'] = store.add(scenario(TIE, marker=7), 'Thick mean build', 'mutation', {})
        ids['tie_a'] = store.add(scenario(TIE, marker=8), 'Tie build A', 'mutation', {})
        ids['tie_b'] = store.add(scenario(TIE, marker=9), 'Tie build B', 'mutation', {})
        ids['pruned'] = store.add(scenario(PRUNED, marker=10), 'Pruned build', 'mutation', {})
        ids['eligible_earned'] = store.add(scenario(QUALIFIED, marker=11), 'Eligible earned build',
                                           'mutation', {})
        ids['eligible_potential'] = store.add(scenario(QUALIFIED, marker=12),
                                              'Eligible potential build', 'mutation', {})
        ids['single_shot'] = store.add(scenario(QUALIFIED, marker=13), 'Single-defeat build',
                                       'mutation', {})
        ids['nine'] = store.add(scenario(EDGE, marker=14), 'Nine-defeat build', 'mutation', {})
        ids['ten'] = store.add(scenario(EDGE, marker=15), 'Ten-defeat build', 'mutation', {})

    # ENCOUNTER: record = loss(opportunity 9) + win 5 + win 1 -> mean earned 2 over 3, mean potential
    # 9 over 1. probe = two wins of 3 -> mean earned 3 over 2, no defeat so no potential sample. Both
    # means are real and both are thinner than the floor, so this fight reports no average at all
    # while its 9-chest potential record stays a record.
    store.record(ids['record'], 'discovery', 0, result(2, seeds=(11, 12), digest='r-loss',
                                                       prize=9, pending=9))
    store.record(ids['record'], 'discovery', 1, result(1, seeds=(13, 14), digest='r-win5',
                                                       prize=5, pending=5))
    store.record(ids['record'], 'discovery', 2, result(1, seeds=(15, 16), digest='r-win1',
                                                       prize=1, pending=1))
    store.record(ids['probe'], 'discovery', 0, result(1, seeds=(21, 22), digest='p-win3a',
                                                      prize=3, pending=3))
    store.record(ids['probe'], 'discovery', 1, result(1, seeds=(23, 24), digest='p-win3b',
                                                      prize=3, pending=3))
    # OTHER/0: one loss of 20 plus two wins of 10 -> mean earned 10 over 2, mean potential 20 over 1.
    store.record(ids['other'], 'discovery', 0, result(2, seeds=(31, 32), digest='o-loss',
                                                      prize=20, pending=20))
    record_batch(store, ids['other'], 2, 1, prize=10, start=33, first=1)
    # OTHER/1: two defeats with opportunities 4 and 8 -> mean earned 0 over 2, mean potential 6.
    store.record(ids['loss_only'], 'discovery', 0, result(2, seeds=(41, 42), digest='l-loss4',
                                                          prize=4, pending=4))
    store.record(ids['loss_only'], 'discovery', 1, result(2, seeds=(43, 44), digest='l-loss8',
                                                          prize=8, pending=8))
    # UNMEASURED: an unresolved run plus a win whose chest reading is genuinely absent.
    store.record(ids['unmeasured'], 'discovery', 0, result(None, seeds=(51, 52),
                                                           digest='u-censored', prize=None))
    store.record(ids['unmeasured'], 'discovery', 1,
                 dict(result(1, seeds=(53, 54), digest='u-blank', prize=None),
                      prizeCallbacks=None, rewardOutcome=None))
    # TIE: thin has mean 4 over ten runs; the other three have mean 4 over twelve.
    record_batch(store, ids['thin'], 10, 1, prize=4, start=60)
    record_batch(store, ids['thick'], 12, 1, prize=4, start=80)
    record_batch(store, ids['tie_a'], 12, 1, prize=4, start=110)
    record_batch(store, ids['tie_b'], 12, 1, prize=4, start=140)
    # PRUNED: two wins of 50.
    record_batch(store, ids['pruned'], 2, 1, prize=50, start=91)
    # QUALIFIED: six wins of 10 plus four empty defeats -> earned mean 6.0 over ten samples, and its
    # four zero-opportunity defeats leave the potential metric one sample short of the floor.
    record_batch(store, ids['eligible_earned'], 6, 1, prize=10, start=200)
    record_batch(store, ids['eligible_earned'], 4, 2, prize=0, start=220, first=6)
    # QUALIFIED: ten defeats of 12 plus two wins of 7 -> potential mean 12 over ten samples, earned
    # mean 14/12 over twelve (the defeats contribute the loss gate's zero, never their opportunity).
    record_batch(store, ids['eligible_potential'], 10, 2, prize=12, start=240)
    record_batch(store, ids['eligible_potential'], 2, 1, prize=7, start=270, first=10)
    # QUALIFIED: one defeat with a 119-chest opportunity plus five wins of 3 -> potential mean 119
    # over ONE sample: the largest mean in the fight and still not an average.
    store.record(ids['single_shot'], 'discovery', 0, result(2, seeds=(300, 301),
                                                            digest='s-loss119', prize=119,
                                                            pending=119))
    record_batch(store, ids['single_shot'], 5, 1, prize=3, start=310, first=1)
    # EDGE: nine defeats of 40 (mean 40 over nine) against ten defeats of 3 (mean 3 over ten).
    record_batch(store, ids['nine'], 9, 2, prize=40, start=400)
    record_batch(store, ids['ten'], 10, 2, prize=3, start=430)
    store.close()
    return ids


def wait_for_open(bridge, seconds=90):
    deadline = time.monotonic()+seconds
    while time.monotonic() < deadline:
        if bridge.status().get('state') != 'Opening library':
            return True
        time.sleep(.05)
    return False


def check(condition, message, failures):
    if not condition:
        failures.append(message)


def leader_row(payload, encounter_id, defeat_count):
    return next((row for row in payload.get('leaders') or []
                 if row['encounterId'] == encounter_id and row['defeatCount'] == defeat_count), None)


def check_eligible_means(bridge, ids, failures):
    """Each eligible crown must name the exact build and the exact mean/sample count behind it."""
    payload = bridge.overview_average_leaders()
    check(payload.get('ok') is True,
          f'overview_average_leaders failed: {payload.get("error")!r}', failures)
    if not payload.get('ok'):
        return
    check(payload.get('minSamples') == FLOOR,
          f'the payload did not publish the eligibility floor: {payload.get("minSamples")!r}',
          failures)
    check(payload.get('encounters') == 8 and len(payload['leaders']) == 8,
          f'the eight occupied fights were not all listed: {payload.get("encounters")}', failures)

    row = leader_row(payload, QUALIFIED, 0)
    check(row is not None, 'the QUALIFIED fight is missing from the overview', failures)
    if row:
        check(row['residentCount'] == 3, f'resident count is wrong: {row["residentCount"]}', failures)
        earned, potential = row['highestAvgEarned'], row['highestAvgPotential']
        check(earned and earned['candidateId'] == ids['eligible_earned'],
              f'the eligible mean-earned leader is wrong: {earned}', failures)
        check(earned and earned['mean'] == 6 and earned['samples'] == FLOOR,
              f'the eligible mean-earned mean/samples are wrong: {earned}', failures)
        check(earned and earned['attempts'] == 10 and earned['comparable'] is True,
              f'the eligible mean-earned attempts/comparability are wrong: {earned}', failures)
        check(potential and potential['candidateId'] == ids['eligible_potential'],
              f'the eligible mean-potential leader is wrong: {potential}', failures)
        check(potential and potential['mean'] == 12 and potential['samples'] == FLOOR,
              f'the eligible mean-potential mean/samples are wrong: {potential}', failures)
        check(potential and potential['attempts'] == 12 and potential['comparable'] is True,
              f'the eligible mean-potential attempts/comparability are wrong: {potential}', failures)
        check(earned and potential and earned['candidateId'] != potential['candidateId'],
              'the two metrics wrongly share one leader', failures)
        # The 119-chest single defeat is the fight's largest opportunity and must not lead either
        # metric: it is one sample, not an average.
        check(potential and potential['candidateId'] != ids['single_shot'],
              'a one-defeat opportunity was crowned as the fight average', failures)

    edge = leader_row(payload, EDGE, 0)
    check(edge is not None, 'the EDGE fight is missing from the overview', failures)
    if edge:
        earned, potential = edge['highestAvgEarned'], edge['highestAvgPotential']
        check(potential and potential['candidateId'] == ids['ten']
              and potential['mean'] == 3 and potential['samples'] == 10,
              f'the ten-sample mean did not outrank the nine-sample one: {potential}', failures)
        check(earned and earned['candidateId'] == ids['ten']
              and earned['mean'] == 0 and earned['samples'] == 10,
              f'a loss-only earned mean at the floor is wrong: {earned}', failures)
        check(earned and potential and earned['mean'] != potential['mean'],
              'the loss opportunity leaked into the earned mean at the floor', failures)

    # The overview must agree with the ranked listing for the same fight, build for build.
    listing = bridge.encounter_strategies(QUALIFIED)
    check(listing.get('ok') is True, 'the cross-check listing failed', failures)
    if listing.get('ok') and row:
        earned, potential = row['highestAvgEarned'], row['highestAvgPotential']
        for entry in listing['strategies']:
            if entry['candidateId'] == ids['eligible_earned']:
                check(entry['meanEarned'] == earned['mean']
                      and entry['earnedSamples'] == earned['samples'],
                      f'the earned crown disagrees with encounter_strategies: {entry}', failures)
            if entry['candidateId'] == ids['eligible_potential']:
                check(entry['meanPotential'] == potential['mean']
                      and entry['potentialSamples'] == potential['samples'],
                      f'the potential crown disagrees with encounter_strategies: {entry}', failures)
                # Ten defeats contribute the loss gate's zero over twelve resolved runs, so the
                # build's earned mean is 14/12 - the opportunity never leaks across metrics.
                check(abs(entry['meanEarned'] - 14/12) < 1e-9 and entry['earnedSamples'] == 12,
                      f'the defeats did not contribute zero earned: {entry}', failures)
                check(entry['meanEarned'] != entry['meanPotential'],
                      'the loss opportunity leaked into the earned mean', failures)


def check_below_floor(bridge, ids, failures):
    """A mean over fewer than ten qualifying observations is a record, never the fight average."""
    payload = bridge.overview_average_leaders()
    # ENCOUNTER's best potential mean is 9 over one defeat and its best earned mean 3 over two wins.
    row = leader_row(payload, ENCOUNTER, 0)
    check(row is not None and row['residentCount'] == 2,
          f'the ENCOUNTER fight is missing or miscounted: {row}', failures)
    if row:
        check(row['highestAvgEarned'] is None and row['highestAvgPotential'] is None,
              f'a thin mean was crowned as an average: {row}', failures)
    # The record itself is not lost: the ranked listing still shows the build's own thin mean.
    listing = bridge.encounter_strategies(ENCOUNTER)
    if listing.get('ok'):
        record = next((entry for entry in listing['strategies']
                       if entry['candidateId'] == ids['record']), None)
        check(record and record['meanPotential'] == 9 and record['potentialSamples'] == 1,
              f'the one-defeat mean vanished from the listing: {record}', failures)
        probe = next((entry for entry in listing['strategies']
                      if entry['candidateId'] == ids['probe']), None)
        check(probe and probe['meanEarned'] == 3 and probe['earnedSamples'] == 2,
              f'the two-win mean vanished from the listing: {probe}', failures)
    # The lifetime maximum is a different path and must survive the floor: QUALIFIED's record is the
    # single 119-chest defeat whose mean is excluded from the average above.
    stats = (bridge.status().get('encounterStats') or {}).get(f'{QUALIFIED}:0') or {}
    check(stats.get('highestPotentialChests') == 119,
          f'the 119-chest record was not kept visible: {stats.get("highestPotentialChests")!r}',
          failures)
    # Every other sub-floor difficulty reports unknown for both metrics rather than a leader.
    for fight, member in ((OTHER, 0), (OTHER, 1)):
        row = leader_row(payload, fight, member)
        check(row is not None, f'the sub-floor fight {fight}:{member} is missing', failures)
        if row:
            check(row['highestAvgEarned'] is None and row['highestAvgPotential'] is None,
                  f'a sub-floor mean was crowned in {fight}:{member}: {row}', failures)


def check_zero_samples(bridge, ids, failures):
    """No measured sample means no average - never the best max wearing a mean's label."""
    payload = bridge.overview_average_leaders()
    row = leader_row(payload, UNMEASURED, 0)
    check(row is not None and row['residentCount'] == 1,
          f'the unmeasured fight is missing or miscounted: {row}', failures)
    if row:
        check(row['highestAvgEarned'] is None and row['highestAvgPotential'] is None,
              f'an unmeasured fight produced an average: {row}', failures)


def check_ties(bridge, ids, failures):
    """Equal means break by larger sample count, then by candidate id - never by accident."""
    payload = bridge.overview_average_leaders()
    row = leader_row(payload, TIE, 0)
    check(row is not None, 'the TIE fight is missing from the overview', failures)
    expected = min(ids['thick'], ids['tie_a'], ids['tie_b'])
    if row:
        earned = row['highestAvgEarned']
        check(earned and earned['candidateId'] == expected,
              f'the equal-mean tie did not resolve to the lowest id: {earned}', failures)
        check(earned and earned['mean'] == 4 and earned['samples'] == 12,
              f'the tie leader has the wrong mean/samples: {earned}', failures)
        check(not earned or earned['candidateId'] != ids['thin'],
              'a ten-run mean outranked a twelve-run mean of the same value', failures)
    # The rule itself, independent of the hash ids the fixture happened to draw.
    check(desktop._outranks_mean(dict(mean=4, samples=10, candidateId='aaaa'),
                                 dict(mean=4, samples=12, candidateId='zzzz')),
          'a larger sample count did not outrank a smaller one', failures)
    check(not desktop._outranks_mean(dict(mean=4, samples=12, candidateId='aaaa'),
                                     dict(mean=4, samples=12, candidateId='zzzz')),
          'an equal mean and count did not lose to the lower id', failures)
    check(desktop._outranks_mean(dict(mean=4, samples=12, candidateId='zzzz'),
                                 dict(mean=4, samples=12, candidateId='aaaa')),
          'the lower id did not win an exact tie', failures)
    check(not desktop._outranks_mean(dict(mean=5, samples=10, candidateId='zzzz'),
                                     dict(mean=4, samples=12, candidateId='aaaa')),
          'a larger sample count outranked a higher mean', failures)


def drop_candidate_row(path, candidate_id):
    """Remove only the resident candidate row, leaving its run rows behind.

    A real prune removes the runs too; dropping just the candidate row is the harder case, and it is
    what proves the overview is driven by the resident population rather than by whatever stale rows
    an earlier life of the library left in `run`.
    """
    db = sqlite3.connect(path)
    try:
        with db:
            db.execute('DELETE FROM candidate WHERE id=?', (candidate_id,))
    finally:
        db.close()


def check_pruned(bridge, ids, path, failures):
    """A pruned build's stale averages must vanish while its frozen record stays reachable."""
    drop_candidate_row(path, ids['pruned'])
    db = sqlite3.connect(path)
    try:
        stale = db.execute('SELECT COUNT(*) FROM run WHERE candidate=?', (ids['pruned'],)).fetchone()[0]
    finally:
        db.close()
    check(stale == 2, f'the fixture did not keep the pruned runs for the honesty check: {stale}',
          failures)
    payload = bridge.overview_average_leaders()
    check(leader_row(payload, PRUNED, 0) is None,
          'a pruned candidate left an overview row from its stale runs', failures)
    check(all(entry is None or entry['candidateId'] != ids['pruned']
              for row in payload['leaders']
              for entry in (row['highestAvgEarned'], row['highestAvgPotential'])),
          'a pruned candidate was resurrected as a leader', failures)
    check(leader_row(payload, TIE, 0) is not None,
          'pruning one fight disturbed another fight\'s leaders', failures)
    # The maximum holder is a separate path: it survives the prune and stays navigable.
    listing = bridge.encounter_strategies(PRUNED)
    check(listing.get('ok') is True, 'the holder listing failed after pruning', failures)
    if listing.get('ok'):
        check(listing['strategies'] == [], 'a pruned candidate is still listed as resident', failures)
        check(any(entry['candidateId'] == ids['pruned'] for entry in listing['orphanHolders']),
              f'the frozen maximum record did not survive the prune: {listing["orphanHolders"]}',
              failures)


def logical_snapshot(path):
    db = sqlite3.connect(path)
    try:
        names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        snapshot = {}
        if 'candidate' in names:
            snapshot['candidates'] = sorted(row[0] for row in db.execute('SELECT id FROM candidate'))
        if 'run' in names:
            snapshot['runs'] = sorted((row[0], row[1], row[2]) for row in
                                      db.execute('SELECT candidate, phase, ordinal FROM run'))
        snapshot['meta'] = {row[0]: row[1] for row in db.execute('SELECT key, value FROM meta')}
        return snapshot
    finally:
        db.close()


def check_no_library_write(bridge, path, failures):
    before = logical_snapshot(path)
    for _ in range(2):
        bridge.overview_average_leaders()
    check(logical_snapshot(path) == before,
          'overview_average_leaders wrote to the optimiser library', failures)


def check_cached_summary_refresh(bridge, ids, path, failures):
    """A new recorded run refreshes its build while unchanged builds reuse their summaries."""
    bridge.overview_average_leaders()
    before = dict(bridge._average_summaries)
    target = ids['eligible_earned']
    untouched = ids['ten']
    bridge.overview_average_leaders()
    check(bridge._average_summaries[target][1] is before[target][1],
          'an unchanged build summary was decoded again', failures)

    store = optimizer.Store(path, provenance())
    try:
        ordinal = store.next_ordinal(target, 'discovery')
        store.record(target, 'discovery', ordinal,
                     result(1, seeds=(9000, 9001), digest='cache-refresh', prize=1000,
                            pending=1000))
    finally:
        store.close()
    payload = bridge.overview_average_leaders()
    after = bridge._average_summaries
    check(after[target][1]['earnedSamples'] == before[target][1]['earnedSamples']+1,
          'a newly recorded run did not refresh its build summary', failures)
    check(after[untouched][1] is before[untouched][1],
          'a run for one build rebuilt an unrelated build summary', failures)
    crown = leader_row(payload, QUALIFIED, 0)['highestAvgEarned']
    listing = bridge.encounter_strategies(QUALIFIED)
    listed = next(entry for entry in listing['strategies'] if entry['candidateId'] == target)
    check(crown and crown['mean'] == listed['meanEarned']
          and crown['samples'] == listed['earnedSamples'],
          'the refreshed overview mean disagrees with the ranked strategy', failures)


def scratch_library(scratch):
    """A unique library path in an existing writable directory, cleaned up by `cleanup_library`."""
    scratch.mkdir(exist_ok=True)
    handle, name = tempfile.mkstemp(prefix='ka-overview-', suffix='.sqlite', dir=scratch)
    os.close(handle)
    os.unlink(name)
    return Path(name)


def cleanup_library(path):
    sidecar = desktop._sidecar_path(path)
    trace = path.with_suffix('.replay.json')
    for target in (path, Path(str(path)+'-wal'), Path(str(path)+'-shm'), Path(str(path)+'.lock'),
                   sidecar, Path(str(sidecar)+'.tmp'), trace, Path(str(trace)+'.tmp')):
        try:
            target.unlink()
        except OSError:
            pass


def main():
    failures = []
    path = scratch_library(HERE/'tmp')
    try:
        ids = build_library(path)
        bridge = desktop.Bridge(path)
        try:
            if not wait_for_open(bridge):
                failures.append('the optimiser never finished opening the fixture library')
            else:
                check_eligible_means(bridge, ids, failures)
                check_below_floor(bridge, ids, failures)
                check_zero_samples(bridge, ids, failures)
                check_ties(bridge, ids, failures)
                check_no_library_write(bridge, path, failures)
                check_pruned(bridge, ids, path, failures)
                check_cached_summary_refresh(bridge, ids, path, failures)
        finally:
            bridge.close()
            bridge._optimizer.thread.join(60)
            bridge._guard.close()
    finally:
        cleanup_library(path)
    if failures:
        print('FAILURES:')
        for row in failures:
            print('  '+row)
        return 1
    print(f'  overview average leaders ok: a mean needs {FLOOR} qualifying observations for the same '
          'build, a single 119-chest defeat stays a record (and its lifetime maximum stays visible), '
          'nine defeats do not qualify while ten do, exact leader id/mean/n per fight, loss-only '
          'potentials, zero-sample exclusion, deterministic ties, exposed probe source, pruned builds '
          'excluded, frozen holders untouched and no library write')
    return 0


if __name__ == '__main__':
    sys.exit(main())
