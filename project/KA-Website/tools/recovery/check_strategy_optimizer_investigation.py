"""Focused checks for the Strategy Optimiser investigation workflow.

Builds tiny temporary SQLite libraries and drives the Bridge's three investigation APIs against a
stubbed simulator, so the checks are deterministic and never run a production batch:

  * encounter_strategies ranks only the requested encounter's resident builds and labels sample
    counts separately from lifetime attempts;
  * a record holder whose candidate was pruned stays navigable (as an orphan entry) and its frozen
    scenario stays usable, while a resident build is reachable by candidate id as well;
  * run_strategy draws seed pairs absent from both the stored runs and earlier trials, persists each
    trial's seeds, metrics and digest to the sidecar, keeps completed trials on a failure, and never
    writes to the optimiser library.
  * replay_strategy_run replays a stored run, a fresh trial and a frozen holder's own pair, resolves
    each target's exact scenario, refuses a seed pair the target never recorded and refuses a rerun
    whose digest disagrees with the record - by candidate and, after pruning, by holder key.
  * simulate_strategy_visual draws a fresh seed pair the target never ran, runs the canonical
    simulator once through the trace path, records exactly one new trial in the sidecar without
    touching the optimiser library, and returns the updated rows beside the trace/setup payload for
    a resident candidate and, after pruning, for a frozen record holder.

    python check_strategy_optimizer_investigation.py
"""
import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import strategy_optimizer as optimizer  # noqa: E402
import strategy_optimizer_adapter as adapter  # noqa: E402
import strategy_optimizer_desktop as desktop  # noqa: E402
from strategy_optimizer_adapter import provenance  # noqa: E402

ENCOUNTER = 125
OTHER_ENCOUNTER = 7
HOLDER_KEY = f'earned:{ENCOUNTER}:0'
POTENTIAL_KEY = f'potential:{ENCOUNTER}:0'


def scenario(encounter=ENCOUNTER, defeat=0, marker=0):
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


def build_library(path):
    """A library with two encounter-125 builds (one earned holder, one potential holder) and a
    third build in a different encounter, so the encounter filter has something to exclude."""
    store = optimizer.Store(path, provenance())
    with store.db:
        store.set('scope', optimizer.scope(scenario()))
        record = store.add(scenario(marker=1), 'Record holder build', 'mutation', {})
        runner_up = store.add(scenario(marker=2), 'Runner-up build', 'mutation', {})
        other = store.add(scenario(OTHER_ENCOUNTER, marker=3), 'Other encounter build', 'mutation', {})
    store.record(record, 'discovery', 0, result(2, seeds=(11, 12), digest='a-loss', prize=9, pending=9))
    store.record(record, 'discovery', 1, result(1, seeds=(13, 14), digest='a-win', prize=5, pending=5))
    store.record(runner_up, 'discovery', 0, result(2, seeds=(21, 22), digest='b-loss', prize=20, pending=20))
    store.record(runner_up, 'discovery', 1, result(1, seeds=(23, 24), digest='b-win', prize=3, pending=3))
    store.record(other, 'discovery', 0, result(1, seeds=(31, 32), digest='c-win', prize=4, pending=4))
    store.close()
    return dict(record=record, runner_up=runner_up, other=other)


def stub_runner(fail_at=None, verdict=1, prize=2):
    """A deterministic `_simulate_trials` seam: tiny stub results, optional failure at one index."""
    def runner(_scenario, seed_pairs):
        completed = []
        for index, seeds in enumerate(seed_pairs):
            if fail_at is not None and index == fail_at:
                return completed, f"Trial {seeds[0]}/{seeds[1]} failed: stub failure"
            completed.append((list(seeds), result(verdict, seeds=seeds,
                                                  digest=f"trial-{seeds[0]}-{seeds[1]}", prize=prize,
                                                  pending=prize)))
        return completed, None
    return runner


def replay_stub(digests, calls, override=None):
    """A deterministic `_trace_replay` seam: a compact stub result for one recorded run.

    Only the expensive simulator call is replaced. `digests` maps a recorded (math, lib) pair to the
    digest the record persisted; `override` lets one call answer with a different digest so a
    mismatch can be exercised. `calls` records the (scenario, seeds) each replay actually asked for.
    """
    def trace(scenario_value, seeds):
        pair = (int(seeds[0]), int(seeds[1]))
        calls.append((dict(scenario_value), pair))
        chosen = override if override is not None and pair in override else digests
        return dict(digest=chosen.get(pair, 'unrecorded'), seeds=[pair[0], pair[1]],
                    replay=dict(finalState=dict(verdict=1), events=[]))
    return trace


def visual_stub(calls, verdict=1, prize=4):
    """A `_trace_replay` seam for a brand-new visual run: a scored compact result plus a stub trace.

    Unlike `replay_stub`, this answers with the compact metric dict a real `trace=True` call returns
    (a verdict and a reward reading), so the trial it records is a scored run, and it keys the digest
    to the seed pair so freshness can be read back from the answer alone.
    """
    def trace(scenario_value, seeds):
        pair = (int(seeds[0]), int(seeds[1]))
        calls.append((dict(scenario_value), pair))
        compact = result(verdict, seeds=list(seeds), digest=f'fresh-{pair[0]}-{pair[1]}',
                         prize=prize, pending=prize)
        return dict(compact, replay=dict(finalState=dict(verdict=verdict),
                                         seeds=[pair[0], pair[1]], events=[]))
    return trace


def wait_for_open(bridge, seconds=90):
    deadline = time.monotonic()+seconds
    while time.monotonic() < deadline:
        if bridge.status().get('state') != 'Opening library':
            return True
        time.sleep(.05)
    return False


def strategy_of(payload, candidate_id):
    return next((entry for entry in payload.get('strategies') or []
                 if entry['candidateId'] == candidate_id), None)


def check(condition, message, failures):
    if not condition:
        failures.append(message)


def check_listing(bridge, ids, failures):
    payload = bridge.encounter_strategies(ENCOUNTER)
    check(payload.get('ok') is True, f'encounter_strategies failed: {payload.get("error")!r}', failures)
    if not payload.get('ok'):
        return
    listed = [entry['candidateId'] for entry in payload['strategies']]
    check(ids['other'] not in listed, 'a build from another encounter was listed', failures)
    check(listed == [ids['runner_up'], ids['record']],
          f'strategies are not ranked by opportunity first: {listed}', failures)
    check(payload.get('orphanHolders') == [], 'resident holders produced orphan entries', failures)
    record = strategy_of(payload, ids['record'])
    check(record is not None, 'the exact record-holder build is missing from the listing', failures)
    if record:
        check(record['source'] == 'mutation' and record['label'] == 'Record holder build',
              'the listing did not carry the build identity', failures)
        check(record['attempts'] == 2 and record['wins'] == 1 and record['losses'] == 1
              and record['noVerdict'] == 0, f'lifetime counters are wrong: {record}', failures)
        check(record['meanEarned'] == 2.5 and record['earnedSamples'] == 2,
              f'mean earned / samples are wrong: {record}', failures)
        check(record['meanPotential'] == 9 and record['potentialSamples'] == 1,
              f'mean potential / samples are wrong: {record}', failures)
        check(record['bestEarned'] == 5 and record['bestPotential'] == 9,
              f'bests are wrong: {record}', failures)
        check(record['winRate'] == .5 and record['comparable'] is True,
              f'win rate / comparability are wrong: {record}', failures)


def check_holder_target(bridge, ids, failures):
    detail = bridge.strategy_detail(holder_key=HOLDER_KEY)
    check(detail.get('ok') is True, f'holder detail failed: {detail.get("error")!r}', failures)
    if not detail.get('ok'):
        return
    check(detail['candidateId'] == ids['record'], 'the holder did not resolve its candidate', failures)
    check(detail['label'] == 'Record holder build', 'the holder did not resolve the build label', failures)
    check(bool(detail.get('scenario')), 'the holder did not return a frozen scenario', failures)
    check(detail['holder'].get('value') == 5 and detail['holder'].get('verdict') == 1,
          'the holder did not echo its record', failures)
    check(detail.get('resident') is True, 'a resident holder did not report its candidate as resident',
          failures)
    check([ (row['phase'], row['ordinal']) for row in detail['storedRuns'] ] ==
          [('discovery', 0), ('discovery', 1)], 'the holder form omitted stored runs', failures)
    check(detail['trialRuns'] == [], 'a fresh scenario reported trials it never had', failures)
    check(detail['storedSummary']['attempts'] == 2 and detail['storedSummary']['wins'] == 1,
          f'holder stored summary is wrong: {detail["storedSummary"]}', failures)
    missing = bridge.strategy_detail(holder_key=f'earned:{ENCOUNTER}:9')
    check(missing.get('ok') is False, 'an unknown holder was resolved', failures)


def check_candidate_target(bridge, ids, failures):
    detail = bridge.strategy_detail(candidate_id=ids['record'])
    check(detail.get('ok') is True, f'candidate detail failed: {detail.get("error")!r}', failures)
    if not detail.get('ok'):
        return
    check(detail['candidateId'] == ids['record'], 'the candidate form returned another build', failures)
    check(detail.get('resident') is True, 'a live candidate detail did not report itself resident',
          failures)
    check(len(detail['storedRuns']) == 2, 'the candidate form omitted retained runs', failures)
    run = detail['storedRuns'][0]
    for field in ('phase', 'ordinal', 'seeds', 'verdict', 'censored', 'chests', 'earned',
                  'potential', 'basis', 'prizeCallbacks', 'ticks', 'digest'):
        check(field in run, f'the run payload is missing {field}', failures)
    check(run['chests'] == 0 and run['basis'] == 'loss-gate',
          f'a defeat did not read as the gate zero: {run}', failures)
    win = detail['storedRuns'][1]
    check(win['earned'] == 5 and win['basis'] == 'queued-at-victory',
          f'a win did not read through the labelled basis: {win}', failures)
    check(detail['trialRuns'] == [], 'the candidate form invented trial history', failures)


def check_missing_is_null(bridge, ids, failures):
    """A win whose reading is genuinely absent must be null, never a fabricated zero."""
    store = optimizer.Store(bridge._library, provenance())
    blank = scenario(marker=4)
    with store.db:
        cid = store.add(blank, 'No-reading build', 'mutation', {})
    store.record(cid, 'discovery', 0, dict(result(1, seeds=(41, 42), digest='d-win', prize=None),
                                           prizeCallbacks=None, rewardOutcome=None))
    store.close()
    detail = bridge.strategy_detail(candidate_id=cid)
    check(detail.get('ok') is True, 'the no-reading build could not be read', failures)
    if detail.get('ok'):
        check(detail['storedSummary']['meanEarned'] is None and
              detail['storedSummary']['earnedSamples'] == 0,
              f'an unknown win reading was counted as a number: {detail["storedSummary"]}', failures)
        check(detail['storedRuns'][0]['earned'] is None, 'an unknown win read as zero', failures)
    store = optimizer.Store(bridge._library, provenance())
    with store.db:
        store.db.execute('DELETE FROM run WHERE candidate=?', (cid,))
    store.close()


def check_trials_and_persistence(bridge, ids, path, failures):
    bridge._simulate_trials = stub_runner()
    first = bridge.run_strategy(candidate_id=ids['record'], count=2)
    check(first.get('ok') is True, f'the first trial batch failed: {first.get("error")!r}', failures)
    if not first.get('ok'):
        return {}
    first_seeds = {tuple(row['seeds']) for row in first['trialRuns']}
    check(len(first_seeds) == 2, f'the batch did not preserve both trials: {first["trialRuns"]}', failures)
    check(first['trialSummary']['samples'] == 2 and first['trialSummary']['wins'] == 2,
          f'the trial summary is wrong: {first["trialSummary"]}', failures)
    check(first['batchSummary']['samples'] == 2, 'the batch summary is missing', failures)
    check(first['storedSummary']['attempts'] == 2, 'a trial changed the stored summary', failures)
    check(not first_seeds & {(11, 12), (13, 14)}, 'a trial reused a stored seed pair', failures)
    for row in first['trialRuns']:
        check(bool(row.get('digest')) and row.get('seeds'), 'a trial lost its seed pair or digest', failures)

    second = bridge.run_strategy(candidate_id=ids['record'], count=2)
    check(second.get('ok') is True, f'the second batch failed: {second.get("error")!r}', failures)
    second_seeds = {tuple(row['seeds']) for row in second.get('trialRuns') or []}
    check(len(second_seeds) == 4,
          f'the trial history did not accumulate two fresh pairs: {second_seeds}', failures)
    cumulative = second['trialSummary']
    check(cumulative['samples'] == 4, f'cumulative trial summary is wrong: {cumulative}', failures)

    sidecar = desktop._sidecar_path(path)
    check(sidecar.is_file(), 'the investigation sidecar was not written', failures)
    check(not list(sidecar.parent.glob(sidecar.name+'.tmp')), 'an atomic-write temp file was left behind', failures)
    payload = json.loads(sidecar.read_text(encoding='utf-8'))
    check(payload.get('schema') == desktop.INVESTIGATION_SCHEMA, 'the sidecar schema is wrong', failures)
    scenario_key = optimizer.identity(scenario(marker=1))
    ledger = payload.get('scenarios', {}).get(scenario_key)
    check(ledger is not None and len(ledger.get('trials') or []) == 4,
          'the sidecar is not keyed by scenario identity', failures)
    return {tuple(row['seeds']): row.get('digest') for row in second.get('trialRuns') or []}


def check_trial_failure(bridge, ids, failures):
    bridge._simulate_trials = stub_runner(fail_at=1)
    payload = bridge.run_strategy(candidate_id=ids['runner_up'], count=3)
    check(payload.get('ok') is False, 'a failed trial batch reported success', failures)
    check('stub failure' in str(payload.get('error')), f'the failure is not explained: {payload.get("error")!r}', failures)
    check(len(payload.get('trialRuns') or []) == 1, 'completed trials were not preserved', failures)
    check(payload.get('batchSummary', {}).get('samples') == 1,
          'the partial batch summary is wrong', failures)


def check_errors(bridge, ids, failures):
    for bad in (0, 9, -1, 'x', True):
        payload = bridge.run_strategy(candidate_id=ids['record'], count=bad)
        check(payload.get('ok') is False, f'count={bad!r} was accepted', failures)
    cases = [
        bridge.strategy_detail(),
        bridge.strategy_detail(candidate_id=ids['record'], holder_key=HOLDER_KEY),
        bridge.run_strategy(),
        bridge.strategy_detail(candidate_id='not-a-build'),
        bridge.encounter_strategies('x'),
        bridge.encounter_strategies(-1),
    ]
    for index, payload in enumerate(cases):
        check(payload.get('ok') is False, f'error case {index} was accepted', failures)


def check_rejections(bridge, ids, failures):
    """Active optimisation, an incompatible simulator and a held operation lock must all refuse."""
    bridge._start_requested = True
    try:
        payload = bridge.run_strategy(candidate_id=ids['runner_up'], count=1)
    finally:
        bridge._start_requested = False
    check(payload.get('ok') is False, 'a trial batch started while optimisation was active', failures)

    original = bridge.status
    bridge.status = lambda: dict(original(), compatible=False)
    try:
        payload = bridge.run_strategy(candidate_id=ids['runner_up'], count=1)
    finally:
        bridge.status = original
    check(payload.get('ok') is False and 'simulator' in str(payload.get('error')),
          f'an incompatible simulator was not refused: {payload!r}', failures)

    bridge._operation.acquire()
    try:
        payload = bridge.run_strategy(candidate_id=ids['runner_up'], count=1)
        read = bridge.strategy_detail(candidate_id=ids['runner_up'])
    finally:
        bridge._operation.release()
    check(payload.get('ok') is False and 'already running' in str(payload.get('error')),
          f'a concurrent trial batch was not refused: {payload!r}', failures)
    check(read.get('ok') is True, 'a read-only detail call was blocked by the operation lock', failures)


def check_retention_labels(bridge, ids, failures):
    """Drop one retained row while its lifetime counter stays, and prove the sample is labelled."""
    store = optimizer.Store(bridge._library, provenance())
    with store.db:
        store.db.execute("DELETE FROM run WHERE candidate=? AND phase='discovery' AND ordinal=1",
                         (ids['runner_up'],))
    store.close()
    entry = strategy_of(bridge.encounter_strategies(ENCOUNTER), ids['runner_up'])
    check(entry is not None, 'the rolled-off build vanished from the listing', failures)
    if entry:
        check(entry['attempts'] == 2, f'lifetime attempts dropped after roll-off: {entry}', failures)
        check(entry['meanEarned'] == 0 and entry['earnedSamples'] == 1,
              f'the retained sample was not labelled: {entry}', failures)
        check(entry['comparable'] is False,
              f'a rolled-off build claimed to be fully comparable: {entry}', failures)


def prune_candidate(path, candidate_id):
    db = sqlite3.connect(path)
    try:
        with db:
            known = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table, column in (('run', 'candidate'), ('archive', 'candidate'),
                                  ('lineage', 'candidate'), ('predictor_history', 'candidate'),
                                  ('candidate_meta', 'id')):
                if table in known:
                    db.execute(f'DELETE FROM {table} WHERE {column}=?', (candidate_id,))
            db.execute('DELETE FROM candidate WHERE id=?', (candidate_id,))
    finally:
        db.close()


def check_pruned_holder(bridge, ids, path, failures):
    prune_candidate(path, ids['record'])
    payload = bridge.encounter_strategies(ENCOUNTER)
    check(payload.get('ok') is True, 'the listing failed after pruning', failures)
    orphans = {entry['key']: entry for entry in payload.get('orphanHolders') or []}
    check(HOLDER_KEY in orphans, f'the pruned holder is not navigable: {orphans}', failures)
    if HOLDER_KEY in orphans:
        check(orphans[HOLDER_KEY]['candidateId'] == ids['record'],
              'the orphan entry lost the candidate identity', failures)
        check(orphans[HOLDER_KEY]['value'] == 5, 'the orphan entry lost the record value', failures)
    check(ids['record'] not in [entry['candidateId'] for entry in payload['strategies']],
          'a pruned candidate is still listed as resident', failures)

    detail = bridge.strategy_detail(holder_key=HOLDER_KEY)
    check(detail.get('ok') is True, f'the pruned holder detail failed: {detail.get("error")!r}', failures)
    if detail.get('ok'):
        check(detail['scenario'].get('encounterId') == ENCOUNTER,
              'the pruned holder did not return its frozen scenario', failures)
        check(detail.get('resident') is False,
              'a pruned holder claimed its candidate was still resident', failures)
        check(detail['storedRuns'] == [], 'a pruned candidate reported stored runs', failures)
        check(len(detail['trialRuns']) == 4, 'the pruned holder lost its trial history', failures)
    return detail


def check_replay_targets(bridge, ids, trial_digests, failures):
    """replay_strategy_run must replay stored, trial and holder runs, and refuse everything else."""
    calls = []
    digests = {**trial_digests, (11, 12): 'a-loss', (13, 14): 'a-win'}
    bridge._trace_replay = replay_stub(digests, calls)

    stored = bridge.replay_strategy_run(candidate_id=ids['record'], seeds=[11, 12])
    check(stored.get('ok') is True, f'the stored-run replay failed: {stored.get("error")!r}', failures)
    if stored.get('ok'):
        for field in ('replay', 'scenario', 'visualSetup', 'jobIdentity', 'warnings'):
            check(field in stored, f'the replay payload is missing {field}', failures)
        check(stored['candidateId'] == ids['record'],
              'the stored replay lost its candidate identity', failures)
        check((stored['mathSeed'], stored['libSeed']) == (11, 12),
              'the stored replay changed its seed pair', failures)
        check(stored['scenario'].get('encounterId') == ENCOUNTER,
              'the stored replay lost the frozen scenario', failures)
    check(calls and calls[0][0].get('encounterId') == ENCOUNTER
          and (calls[0][0].get('mathSeed'), calls[0][0].get('libSeed')) == (11, 12),
          f'the replay did not run the frozen scenario on the requested seeds: {calls[:1]}', failures)

    # Every recorded pair - a retained run row and each fresh trial - replays by the same candidate.
    for pair in sorted(digests):
        payload = bridge.replay_strategy_run(candidate_id=ids['record'], seeds=list(pair))
        check(payload.get('ok') is True,
              f'replay refused recorded seeds {pair}: {payload.get("error")!r}', failures)

    holder = bridge.replay_strategy_run(holder_key=HOLDER_KEY, seeds=[13, 14])
    check(holder.get('ok') is True, f'the holder replay failed: {holder.get("error")!r}', failures)
    if holder.get('ok'):
        check(holder['candidateId'] == ids['record'],
              'the holder replay lost its candidate identity', failures)
        check(holder['label'] == 'Record holder build',
              'the holder replay lost the build label', failures)
        check(holder['scenario'].get('encounterId') == ENCOUNTER,
              'the holder replay lost the frozen scenario', failures)

    unknown = bridge.replay_strategy_run(candidate_id=ids['record'], seeds=[99, 100])
    check(unknown.get('ok') is False and 'recorded' in str(unknown.get('error')),
          f'an unrecorded seed pair was replayed: {unknown!r}', failures)

    bridge._trace_replay = replay_stub(digests, calls, override={(13, 14): 'not-the-recorded-digest'})
    drift = bridge.replay_strategy_run(candidate_id=ids['record'], seeds=[13, 14])
    check(drift.get('ok') is False and 'differ' in str(drift.get('error')),
          f'a digest mismatch was not refused: {drift!r}', failures)
    bridge._trace_replay = replay_stub(digests, calls)

    for bad in (None, [1], [1, 2, 3], 'x', [True, 2]):
        payload = bridge.replay_strategy_run(candidate_id=ids['record'], seeds=bad)
        check(payload.get('ok') is False, f'seeds={bad!r} was accepted by replay_strategy_run',
              failures)
    for bad_target in ({}, {'candidate_id': ids['record'], 'holder_key': HOLDER_KEY}):
        payload = bridge.replay_strategy_run(seeds=[11, 12], **bad_target)
        check(payload.get('ok') is False, f'target {bad_target!r} was accepted', failures)

    bridge._operation.acquire()
    try:
        busy = bridge.replay_strategy_run(candidate_id=ids['record'], seeds=[11, 12])
    finally:
        bridge._operation.release()
    check(busy.get('ok') is False and 'already being prepared' in str(busy.get('error')),
          f'a concurrent replay was not refused: {busy!r}', failures)


def check_replay_after_prune(bridge, ids, trial_digests, failures):
    """A pruned holder keeps its own frozen pair and its trials; a stored-only pair is gone with it."""
    calls = []
    digests = {**trial_digests, (11, 12): 'a-loss', (13, 14): 'a-win'}
    bridge._trace_replay = replay_stub(digests, calls)
    holder = bridge.replay_strategy_run(holder_key=HOLDER_KEY, seeds=[13, 14])
    check(holder.get('ok') is True,
          f'a pruned holder could not replay its frozen run: {holder.get("error")!r}', failures)
    stored_only = bridge.replay_strategy_run(holder_key=HOLDER_KEY, seeds=[11, 12])
    check(stored_only.get('ok') is False,
          'a pruned holder replayed a seed pair it never kept after pruning', failures)
    for pair in sorted(trial_digests):
        payload = bridge.replay_strategy_run(holder_key=HOLDER_KEY, seeds=list(pair))
        check(payload.get('ok') is True,
              f'a pruned holder lost its trial pair {pair}: {payload.get("error")!r}', failures)


def check_simulate_visual_candidate(bridge, ids, path, failures):
    """A candidate's fresh visual simulation draws a new pair, traces it once and records one trial."""
    before = logical_snapshot(path)
    detail = bridge.strategy_detail(candidate_id=ids['runner_up'])
    if not detail.get('ok'):
        failures.append(f'the visual target detail failed: {detail.get("error")!r}')
        return
    stored = {tuple(row['seeds']) for row in detail['storedRuns']}
    trials_before = {tuple(row['seeds']) for row in detail['trialRuns']}
    samples_before = detail['trialSummary']['samples']
    matched = detail.get('holder') or {}
    frozen_pair = (matched.get('mathSeed'), matched.get('libSeed'))

    def forbidden(*_args):
        raise AssertionError('the batch path was used instead of the single trace path')

    bridge._simulate_trials = forbidden
    calls = []
    bridge._trace_replay = visual_stub(calls)
    payload = bridge.simulate_strategy_visual(candidate_id=ids['runner_up'])
    check(payload.get('ok') is True,
          f'the fresh visual simulation failed: {payload.get("error")!r}', failures)
    if not payload.get('ok'):
        return
    seeds = tuple(payload.get('seeds') or ())
    check(len(seeds) == 2, f'no fresh seed pair was returned: {payload.get("seeds")!r}', failures)
    check(seeds not in stored and seeds not in trials_before and seeds != frozen_pair,
          f'the fresh simulation reused a recorded seed pair: {seeds}', failures)
    for field in ('replay', 'visualSetup', 'jobIdentity', 'warnings', 'scenario'):
        check(field in payload, f'the fresh simulation payload is missing {field}', failures)
    check(payload.get('candidateId') == ids['runner_up'] and payload.get('label') == 'Runner-up build',
          'the fresh simulation lost the build identity', failures)
    check((payload['scenario'].get('mathSeed'), payload['scenario'].get('libSeed')) == seeds
          and payload['scenario'].get('encounterId') == ENCOUNTER,
          'the returned scenario is not the frozen build on the fresh pair', failures)
    check((payload['mathSeed'], payload['libSeed']) == seeds,
          'the returned seeds disagree with mathSeed/libSeed', failures)
    check(len(calls) == 1 and calls[0][1] == seeds and calls[0][0].get('encounterId') == ENCOUNTER,
          f'the fresh simulation did not make exactly one trace call on the fresh pair: {calls}',
          failures)
    rows = {tuple(row['seeds']): row for row in payload['trialRuns']}
    check(seeds in rows, 'the fresh trial is missing from the returned trial rows', failures)
    check(payload['trialSummary']['samples'] == samples_before+1,
          f'the fresh trial did not grow the trial summary: {payload["trialSummary"]}', failures)
    check(bool(payload.get('digest')) and rows.get(seeds, {}).get('digest') == payload['digest'],
          'the returned digest disagrees with the returned trial row', failures)
    sidecar = json.loads(desktop._sidecar_path(path).read_text(encoding='utf-8'))
    ledger = sidecar.get('scenarios', {}).get(optimizer.identity(scenario(marker=2)))
    recorded = [trial for trial in (ledger or {}).get('trials') or []
                if tuple(trial.get('seeds') or ()) == seeds]
    check(len(recorded) == 1 and recorded[0]['result'].get('digest') == payload['digest'],
          'the sidecar did not receive the fresh trial with its digest', failures)
    check('replay' not in recorded[0]['result'],
          'the full trace was persisted into the trial history', failures)
    check(logical_snapshot(path) == before,
          'a fresh visual simulation wrote to the optimiser library', failures)


def check_simulate_visual_holder(bridge, ids, path, failures):
    """A pruned record holder still simulates a fresh fight from its own frozen scenario."""
    before = logical_snapshot(path)
    detail = bridge.strategy_detail(holder_key=HOLDER_KEY)
    if not detail.get('ok'):
        failures.append(f'the holder visual target detail failed: {detail.get("error")!r}')
        return
    check(detail.get('resident') is False,
          'the holder fixture was not pruned before the frozen-holder check', failures)
    saved = {tuple(row['seeds']) for row in detail['storedRuns']}
    trials_before = {tuple(row['seeds']) for row in detail['trialRuns']}
    frozen = (detail['holder'].get('mathSeed'), detail['holder'].get('libSeed'))
    calls = []
    bridge._trace_replay = visual_stub(calls, verdict=2, prize=6)
    payload = bridge.simulate_strategy_visual(holder_key=HOLDER_KEY)
    check(payload.get('ok') is True,
          f'the holder visual simulation failed: {payload.get("error")!r}', failures)
    if not payload.get('ok'):
        return
    seeds = tuple(payload.get('seeds') or ())
    check(seeds not in saved and seeds not in trials_before and seeds != frozen,
          f'the holder simulation reused a recorded or frozen seed pair: {seeds}', failures)
    check(payload.get('candidateId') == ids['record'] and payload.get('resident') is False,
          'the holder simulation lost the pruned candidate identity', failures)
    check(payload['scenario'].get('encounterId') == ENCOUNTER
          and (payload['scenario'].get('mathSeed'), payload['scenario'].get('libSeed')) == seeds,
          'the holder simulation lost its frozen scenario or fresh pair', failures)
    rows = {tuple(row['seeds']): row for row in payload['trialRuns']}
    check(seeds in rows and rows[seeds].get('digest') == payload.get('digest'),
          'the holder simulation did not return its own trial row and digest', failures)
    check(logical_snapshot(path) == before,
          'a holder visual simulation wrote to the optimiser library', failures)


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


def check_no_library_writes(bridge, ids, path, failures):
    before = logical_snapshot(path)
    bridge.encounter_strategies(ENCOUNTER)
    bridge.strategy_detail(candidate_id=ids['runner_up'])
    bridge.strategy_detail(holder_key=HOLDER_KEY)
    bridge._simulate_trials = stub_runner()
    bridge._trace_replay = replay_stub({(21, 22): 'b-loss'}, [])
    bridge.run_strategy(candidate_id=ids['runner_up'], count=2)
    bridge.run_strategy(holder_key=HOLDER_KEY, count=2)
    bridge.replay_strategy_run(candidate_id=ids['runner_up'], seeds=[21, 22])
    bridge.simulate_strategy_visual(candidate_id=ids['runner_up'])
    bridge.simulate_strategy_visual(holder_key=HOLDER_KEY)
    check(logical_snapshot(path) == before,
          'an investigation read or trial batch wrote to the optimiser library', failures)


def check_corrupt_sidecar(bridge, ids, path, failures):
    sidecar = desktop._sidecar_path(path)
    sidecar.write_text('{ this is not json', encoding='utf-8')
    read = bridge.strategy_detail(candidate_id=ids['runner_up'])
    check(read.get('ok') is False and 'sidecar' in str(read.get('error')),
          f'a corrupt sidecar was not surfaced: {read!r}', failures)
    bridge._simulate_trials = stub_runner()
    run = bridge.run_strategy(candidate_id=ids['runner_up'], count=1)
    check(run.get('ok') is False, 'a corrupt sidecar did not stop a trial batch', failures)
    check(sidecar.read_text(encoding='utf-8') == '{ this is not json',
          'a corrupt sidecar was overwritten instead of being preserved', failures)


def check_trial_worker_trace(failures):
    """trial_worker must call the canonical adapter's simulate with trace=False and nothing else."""
    calls = []
    original = adapter.simulate

    def spy(scenario_value, seeds, trace=False, **kwargs):
        calls.append((scenario_value, list(seeds), trace, kwargs))
        return result(1, seeds=seeds, digest='spy')

    adapter.simulate = spy
    try:
        value = desktop.trial_worker(scenario(marker=9), [7, 8])
    finally:
        adapter.simulate = original
    check(value.get('digest') == 'spy', 'trial_worker did not return the adapter result', failures)
    check(calls and calls[0][1] == [7, 8] and calls[0][2] is False and calls[0][3] == {},
          f'trial_worker did not use simulate(..., trace=False): {calls}', failures)


def check_replay_worker_trace(failures):
    """replay_worker must call the canonical adapter's simulate with trace=True and nothing else.

    This is what makes `simulate_strategy_visual`'s single fresh run produce a visual trace: the seam
    it reuses is the same one every replay goes through, and that seam is the only caller that asks
    the canonical simulator for `trace=True`.
    """
    calls = []
    original = adapter.simulate

    def spy(scenario_value, seeds, trace=False, **kwargs):
        calls.append((scenario_value, list(seeds), trace, kwargs))
        return dict(digest='spy', seeds=list(seeds), replay=dict(events=[]))

    adapter.simulate = spy
    try:
        value = desktop.replay_worker(scenario(marker=9), [7, 8])
    finally:
        adapter.simulate = original
    check(value.get('digest') == 'spy', 'replay_worker did not return the adapter result', failures)
    check(calls and calls[0][1] == [7, 8] and calls[0][2] is True and calls[0][3] == {},
          f'replay_worker did not use simulate(..., trace=True): {calls}', failures)


def scratch_library(scratch):
    """A unique library path in an existing writable directory.

    The scratch directory sits beside this module: the workspace is writable here while the system
    temp directory is not, and every fixture is only a few kilobytes. A fresh *file* name is used
    rather than a fresh subdirectory so the sandbox only has to allow writes into a directory that
    already exists.
    """
    scratch.mkdir(exist_ok=True)
    handle, name = tempfile.mkstemp(prefix='ka-investigation-', suffix='.sqlite', dir=scratch)
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
                check_listing(bridge, ids, failures)
                check_holder_target(bridge, ids, failures)
                check_candidate_target(bridge, ids, failures)
                check_missing_is_null(bridge, ids, failures)
                trial_digests = check_trials_and_persistence(bridge, ids, path, failures)
                check_trial_failure(bridge, ids, failures)
                check_errors(bridge, ids, failures)
                check_rejections(bridge, ids, failures)
                check_replay_targets(bridge, ids, trial_digests, failures)
                check_simulate_visual_candidate(bridge, ids, path, failures)
                check_retention_labels(bridge, ids, failures)
                check_pruned_holder(bridge, ids, path, failures)
                check_replay_after_prune(bridge, ids, trial_digests, failures)
                check_simulate_visual_holder(bridge, ids, path, failures)
                check_no_library_writes(bridge, ids, path, failures)
                check_corrupt_sidecar(bridge, ids, path, failures)
                check_trial_worker_trace(failures)
                check_replay_worker_trace(failures)
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
    print('  investigation workflow ok: ranked listing, holder/candidate detail, fresh-seed trials, '
          'digest-checked stored/trial/holder replays and fresh-seed visual simulations, all without '
          'a main-library write')
    return 0


if __name__ == '__main__':
    sys.exit(main())
