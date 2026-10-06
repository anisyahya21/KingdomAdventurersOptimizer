"""Deep-evidence checks for the desktop strategy optimiser statistics.

The desktop's three statistics displays - the Encounter ranked list, its overview average leaders
and a build's detail summary - used to read their means and sample counts from the rolling replay
bank, so a build whose lifetime aggregates held hundreds of observations but whose replay rows had
rolled at `MAX_SAMPLES` (512) reported a truncated mean. These checks drive synthetic temporary
libraries through `Bridge.__new__` (no GUI, no simulator, no worker threads) and prove:

  * a build with >700 lifetime observations reports the aggregate chest mean / sample count, not the
    512-row retained mean, while `storedRuns` stays bounded to the retained rows;
  * discovery + validation chest counters are summed across phases (the shared `merge_aggregates`
    alone drops the validation chest readings);
  * an asymmetric legacy library that only ever measured chests in one phase keeps what it measured,
    and a library with no chest counters at all keeps the retained reading instead of a zero;
  * the ranked list, the overview leaders and the detail summary report identical means, sample
    counts and attempts for the same build;
  * the overview cache reflects an aggregate-only update even when the retained rows (its cache key)
    are unchanged;
  * censored observations are counted as censored, never folded into a mean as a zero.

    python check_optimizer_desktop_deep_evidence.py
"""
import json
import os
import shutil
import sqlite3
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import strategy_optimizer_desktop as desktop  # noqa: E402
from strategy_optimizer import accumulate, merge_aggregates  # noqa: E402

MAX_SAMPLES = 512

SCHEMA = '''
CREATE TABLE candidate(id TEXT PRIMARY KEY, scenario TEXT NOT NULL, label TEXT NOT NULL,
    source TEXT NOT NULL, stats TEXT NOT NULL, created INTEGER NOT NULL);
CREATE TABLE run(candidate TEXT NOT NULL, phase TEXT NOT NULL, ordinal INTEGER NOT NULL,
    result TEXT NOT NULL, PRIMARY KEY(candidate, phase, ordinal));
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE candidate_meta(id TEXT PRIMARY KEY, encounter INTEGER NOT NULL,
    defeat INTEGER NOT NULL, region TEXT NOT NULL);
'''


def scenario(encounter, defeat=0, marker=0):
    return dict(encounterId=encounter, defeatCount=defeat, tickLimit=120,
                finishPolicy='terminal-verdict',
                ownUnits=[dict(name=f'Build {marker}', human=True, weaponId=1, skills=[1, 2],
                               invocationLevels=[0, 0], grid=0,
                               parameters={13: {'rawValue': 100+marker}})],
                enemies=[])


def win(chests):
    """One resolved victory whose certified chest reading is `chests`."""
    return dict(verdict=1, censored=False, ticks=40, prizeCallbacks=chests, retained=None,
                survivors=1, resourceUses=0,
                rewardOutcome=dict(awardedChests=chests, pendingChests=chests, awardedBasis=None,
                                   inventoryVerified=False, reason=None))


def loss(potential):
    """One resolved defeat: the native gate releases zero chests but builds `potential`."""
    return dict(verdict=2, censored=False, ticks=40, prizeCallbacks=potential, retained=0,
                survivors=0, resourceUses=0,
                rewardOutcome=dict(awardedChests=None, pendingChests=potential, awardedBasis=None,
                                   inventoryVerified=False, reason=None))


def censored(prize=5):
    """One unresolved fight: no verdict, so it contributes to no mean."""
    return dict(verdict=None, censored=True, ticks=40, prizeCallbacks=prize, retained=None,
                survivors=0, resourceUses=0,
                rewardOutcome=dict(awardedChests=None, pendingChests=prize, awardedBasis=None,
                                   inventoryVerified=False, reason=None))


def aggregate(rows):
    """The lifetime lane total the store itself would write for `rows`."""
    total = None
    for row in rows:
        total = accumulate(total, row)
    return total


def write_library(path, candidates):
    """A minimal read-only library: candidate index, retained runs and lifetime aggregates."""
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    for candidate in candidates:
        db.execute('INSERT INTO candidate VALUES (?,?,?,?,?,?)',
                   (candidate['id'], json.dumps(scenario(candidate['encounter'], candidate['defeat'],
                                                         candidate.get('marker', 0))),
                    candidate['id'], candidate.get('source', 'probe'), json.dumps({}),
                    candidate.get('created', 0)))
        db.execute('INSERT INTO candidate_meta VALUES (?,?,?,?)',
                   (candidate['id'], candidate['encounter'], candidate['defeat'], 'region'))
        for phase, total in (candidate.get('aggregates') or {}).items():
            if total is not None:
                db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',
                           (f'aggregate:{candidate["id"]}:{phase}', json.dumps(total)))
        for phase, rows in (candidate.get('runs') or {}).items():
            for ordinal, row in enumerate(rows):
                db.execute('INSERT INTO run VALUES (?,?,?,?)',
                           (candidate['id'], phase, ordinal, json.dumps(row)))
    db.commit()
    db.close()


def bridge_for(library):
    """A Bridge with only the attributes the read APIs touch - no GUI, lock file or optimizer."""
    bridge = desktop.Bridge.__new__(desktop.Bridge)
    bridge._library = Path(library)
    bridge._sidecar_lock = threading.RLock()
    bridge._average_lock = threading.Lock()
    bridge._average_summaries = {}
    return bridge


def close(actual, expected, what, failures):
    if abs(float(actual)-float(expected)) > 1e-9:
        failures.append(f'{what}: got {actual!r}, expected {expected!r}')


def leader(overview, encounter, defeat, metric):
    for row in overview.get('leaders') or []:
        if row['encounterId'] == encounter and row['defeatCount'] == defeat:
            return row[metric]
    return None


def ranked(encounters, candidate_id):
    for entry in encounters.get('strategies') or []:
        if entry['candidateId'] == candidate_id:
            return entry
    return None


def check_deep_history(failures, root):
    """>700 observations, 512 retained rows: the aggregate mean must be the one displayed."""
    path = root/'deep.sqlite'
    retained_wins = [win(4) for _ in range(384)]
    retained_losses = [loss(25) for _ in range(128)]
    evicted_wins = [win(20) for _ in range(160)]
    evicted_losses = [loss(75) for _ in range(128)]
    history = evicted_wins + evicted_losses + retained_wins + retained_losses
    retained = retained_wins + retained_losses
    assert len(history) == 800 and len(retained) == MAX_SAMPLES, 'fixture shape'
    total = aggregate(history)
    write_library(path, [dict(id='deep', encounter=19, defeat=0, aggregates={'discovery': total},
                              runs={'discovery': retained})])
    bridge = bridge_for(path)
    overview = bridge.overview_average_leaders()
    if not overview.get('ok'):
        failures.append(f'overview refused: {overview.get("error")}')
        return
    earned = leader(overview, 19, 0, 'highestAvgEarned')
    potential = leader(overview, 19, 0, 'highestAvgPotential')
    encounters = bridge.encounter_strategies(19, 0)
    entry = ranked(encounters, 'deep')
    detail = bridge.strategy_detail(candidate_id='deep')
    summary = detail.get('storedSummary') or {}

    expected_earned = total['chestSum']/total['chestCount']
    retained_earned = sum(row['rewardOutcome']['awardedChests'] for row in retained_wins)/len(retained)
    close(expected_earned, 5.92, 'aggregate mean earned', failures)
    close(retained_earned, 3.0, 'retained mean earned (fixture)', failures)
    if expected_earned == retained_earned:
        failures.append('fixture: eviction did not change the mean')
    for name, record in (('overview earned', earned), ('ranked earned', entry),
                         ('detail earned', summary)):
        if not record:
            failures.append(f'{name}: missing')
            continue
        close(record.get('meanEarned', record.get('mean')), expected_earned, name, failures)
        if int(record['attempts']) != 800:
            failures.append(f'{name}: attempts {record["attempts"]} != 800')
        samples = record.get('earnedSamples', record.get('samples'))
        if int(samples) != 800:
            failures.append(f'{name}: earnedSamples {samples} != 800')
    close(potential['mean'], total['lossPotentialSum']/total['lossCount'], 'overview potential',
          failures)
    if int(potential['samples']) != 256:
        failures.append(f'overview potential samples {potential["samples"]} != 256')
    if int(entry['retainedSampleCount']) != MAX_SAMPLES:
        failures.append(f'ranked retainedSampleCount {entry["retainedSampleCount"]} != {MAX_SAMPLES}')
    if int(summary['retainedSampleCount']) != MAX_SAMPLES:
        failures.append(f'detail retainedSampleCount {summary["retainedSampleCount"]} != {MAX_SAMPLES}')
    if entry['bestEarned'] != 20 or summary['bestEarned'] != 20:
        failures.append(f'actual earned maximum: ranked {entry["bestEarned"]}, '
                        f'detail {summary["bestEarned"]} (expected 20)')
    if entry['bestPotential'] != 75 or summary['bestPotential'] != 75:
        failures.append(f'actual potential maximum: ranked {entry["bestPotential"]}, '
                        f'detail {summary["bestPotential"]} (expected 75)')
    if len(detail['storedRuns']) != MAX_SAMPLES:
        failures.append(f'replay list not bounded: {len(detail["storedRuns"])} != {MAX_SAMPLES}')
    return bridge, path


def check_phase_sum(failures, root):
    """Chest counters must be summed across discovery and validation, not truncated to phase one."""
    path = root/'phase.sqlite'
    discovery = aggregate([win(8), win(10), win(12)])
    validation = aggregate([win(40), win(60)])
    write_library(path, [dict(id='phasey', encounter=30, defeat=0,
                              aggregates={'discovery': discovery, 'validation': validation})])
    merged = desktop._merge_totals(discovery, validation)
    if (merged['chestCount'], merged['chestSum'], merged['chestSum2']) != (5, 130, 5508):
        failures.append(f'phase merge: {merged["chestCount"]}/{merged["chestSum"]}/'
                        f'{merged["chestSum2"]} != 5/130/5508')
    raw = merge_aggregates(discovery, validation)
    if raw['chestCount'] != 3:
        failures.append('phase merge: shared merge_aggregates unexpectedly fused chest counts')
    db = desktop._readonly_db(path)
    try:
        lifetime = desktop._lifetime_counters(db, 'phasey')
    finally:
        db.close()
    if (lifetime['chestCount'], lifetime['chestSum']) != (5, 130):
        failures.append(f'lifetime counters: {lifetime["chestCount"]}/{lifetime["chestSum"]} '
                        f'!= 5/130')


def check_legacy_coverage(failures, root):
    """Asymmetric and absent chest counters keep measured evidence, never an invented zero."""
    path = root/'legacy.sqlite'
    asym_discovery = aggregate([win(10) for _ in range(5)])
    asym_validation = dict(n=4, wins=2, losses=2, censored=0, count=0, sum=0., sum2=0.,
                           resources=0., survivors=0., ticks=0., minimum=None, maximum=None,
                           histogram=[0]*7)
    legacy_total = dict(n=9, wins=5, losses=4, censored=0, count=5, sum=35, sum2=253,
                        resources=0., survivors=0., ticks=0., minimum=7, maximum=7,
                        histogram=[0]*7)
    retained = [win(7) for _ in range(4)]
    write_library(path, [
        dict(id='asym', encounter=32, defeat=0,
             aggregates={'discovery': asym_discovery, 'validation': asym_validation}),
        dict(id='legacy', encounter=31, defeat=0, aggregates={'discovery': legacy_total},
             runs={'discovery': retained})])
    db = desktop._readonly_db(path)
    try:
        asym = desktop._lifetime_counters(db, 'asym')
        legacy = desktop._lifetime_counters(db, 'legacy')
    finally:
        db.close()
    if (asym.get('chestCount'), asym.get('chestSum')) != (5, 50):
        failures.append(f'asymmetric legacy: {asym.get("chestCount")}/{asym.get("chestSum")} '
                        f'!= 5/50')
    if 'chestCount' in legacy:
        failures.append('legacy library invented a chestCount it never measured')
    retained_summary = desktop._summarize_attempts(retained)
    measure = desktop._aggregate_measure(retained_summary, legacy, len(retained))
    close(measure['meanEarned'], 7.0, 'legacy fallback mean', failures)
    if measure['earnedSamples'] != 4 or measure['attempts'] != 9:
        failures.append(f'legacy fallback: earnedSamples {measure["earnedSamples"]}, '
                        f'attempts {measure["attempts"]} != 4/9')
    if measure['bestEarned'] != 7:
        failures.append(f'legacy fallback bestEarned {measure["bestEarned"]} != 7')


def check_censored_and_agreement(failures, root):
    """Censored runs are counted, never zeroed; all three displays agree on one build."""
    path = root/'censor.sqlite'
    rows = [win(10) for _ in range(6)] + [loss(30) for _ in range(10)] + [censored() for _ in range(14)]
    total = aggregate(rows)
    write_library(path, [dict(id='censor', encounter=20, defeat=0,
                              aggregates={'discovery': total})])
    bridge = bridge_for(path)
    overview = bridge.overview_average_leaders()
    earned = leader(overview, 20, 0, 'highestAvgEarned')
    potential = leader(overview, 20, 0, 'highestAvgPotential')
    entry = ranked(bridge.encounter_strategies(20, 0), 'censor')
    summary = bridge.strategy_detail(candidate_id='censor').get('storedSummary') or {}
    for name, record in (('overview', earned), ('ranked', entry), ('detail', summary)):
        if not record:
            failures.append(f'censor {name}: missing')
            continue
        close(record.get('meanEarned', record.get('mean')), 3.75, f'censor {name} mean', failures)
        samples = record.get('earnedSamples', record.get('samples'))
        if int(samples) != 16:
            failures.append(f'censor {name}: earnedSamples {samples} != 16')
        if int(record['noVerdict']) != 14:
            failures.append(f'censor {name}: noVerdict {record["noVerdict"]} != 14')
        if record.get('comparable'):
            failures.append(f'censor {name}: claimed comparability with censored runs')
    close(potential['mean'], 30.0, 'censor overview potential', failures)
    if int(potential['samples']) != 10:
        failures.append(f'censor potential samples {potential["samples"]} != 10')
    if entry['meanEarned'] == total['chestSum']/total['n']:
        failures.append('censor: mean folded censored runs in as zeros')


def check_cache_freshness(failures, root):
    """An aggregate-only update moves the overview mean even when the replay rows are unchanged."""
    path = root/'cache.sqlite'
    total = aggregate([win(6) for _ in range(700)])
    write_library(path, [dict(id='cachey', encounter=40, defeat=0,
                              aggregates={'discovery': total}, runs={'discovery': [win(6)]})])
    bridge = bridge_for(path)
    before = leader(bridge.overview_average_leaders(), 40, 0, 'highestAvgEarned')
    fingerprint_before = dict(bridge._average_summaries)
    if not before or abs(before['mean']-6.0) > 1e-9:
        failures.append(f'cache before: {before}')
        return
    raised = dict(total)
    raised['chestSum'] = float(total['chestCount'])*12.0
    db = sqlite3.connect(path)
    db.execute('UPDATE meta SET value=? WHERE key=?',
               (json.dumps(raised), 'aggregate:cachey:discovery'))
    db.commit()
    db.close()
    after = leader(bridge.overview_average_leaders(), 40, 0, 'highestAvgEarned')
    if not after or abs(after['mean']-12.0) > 1e-9:
        failures.append(f'cache after aggregate update: {after} (expected mean 12.0)')
    if dict(bridge._average_summaries).keys() != fingerprint_before.keys():
        failures.append('cache key set changed across an aggregate-only update')


def main():
    failures = []
    base = HERE/'tmp'
    base.mkdir(parents=True, exist_ok=True)
    root = base/f'ka-deep-evidence-{os.getpid()}-{time.time_ns()}'
    root.mkdir(parents=True, exist_ok=True)
    try:
        check_deep_history(failures, root)
        check_phase_sum(failures, root)
        check_legacy_coverage(failures, root)
        check_censored_and_agreement(failures, root)
        check_cache_freshness(failures, root)
        check_overview_skips_replay_decoding(failures, root)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    if failures:
        print('FAILURES:')
        for row in failures:
            print('  '+row)
        return 1
    print('  deep desktop evidence ok: aggregate-authoritative means/samples/attempts across the '
          'ranked, overview and detail displays; 512-row replay list preserved; phase-sum, legacy '
          'fallback, cache freshness and censored handling verified')
    return 0


def check_overview_skips_replay_decoding(failures, root):
    path = root/'overview-without-replays.sqlite'
    total = aggregate([win(6) for _ in range(700)])
    write_library(path, [dict(id='complete', encounter=40, defeat=0,
                              aggregates={'discovery': total},
                              runs={'discovery': [win(6)]*512})])
    bridge = bridge_for(path)
    with patch.object(desktop, '_stored_runs', side_effect=AssertionError('replay decode')), \
         patch.object(desktop, '_resident_run_rows', side_effect=AssertionError('bulk decode')):
        result = bridge.overview_average_leaders()
    entry = leader(result, 40, 0, 'highestAvgEarned')
    if not entry or entry['samples'] != 700 or entry['retainedSampleCount'] != 512:
        failures.append(f'overview aggregate-only path failed: {result}')
    # A counter removal must restore the legacy fallback even though replay fingerprints match.
    for key in list(total):
        if key.startswith('chest'):
            del total[key]
    db = sqlite3.connect(path)
    db.execute('UPDATE meta SET value=? WHERE key=?',
               (json.dumps(total), 'aggregate:complete:discovery'))
    db.commit()
    db.close()
    entry = leader(bridge.overview_average_leaders(), 40, 0, 'highestAvgEarned')
    if not entry or entry['samples'] != 512 or entry['mean'] != 6:
        failures.append(f'overview lost legacy fallback after counter removal: {entry}')


if __name__ == '__main__':
    sys.exit(main())
