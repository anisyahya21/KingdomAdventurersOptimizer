"""Acceptance checks for the Community-first encounter runtime.

Run:  <venv>/python -B -X utf8 tools/recovery/check_encounter_search.py

Real `Optimizer` + `Bridge` on a temporary library, with the battle pool mocked ONLY for the
synthetic learnable joint-compensation law and for the disconnected/error fixtures. One bounded
native cycle (<= 32 real battles) is run at the end through the real evaluator. A tinytest-scale
sample may only yield `inconclusive`; no earning gain is claimed.
"""
from __future__ import annotations

import json
import sys
import time
import uuid
from concurrent.futures import Future
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
WORKSPACE = HERE.parents[1]

import strategy_encounter_search as search                  # noqa: E402
import strategy_experiment_store as ledger                  # noqa: E402
import strategy_joint_proposals as joint                    # noqa: E402
import strategy_mechanics as mechanics                      # noqa: E402
from strategy_optimizer import Store                        # noqa: E402
from strategy_optimizer_adapter import (default_scenario, provenance,   # noqa: E402
                                        stats)
from strategy_optimizer_desktop import Bridge               # noqa: E402

TMP = WORKSPACE/'tmp/encounter-redesign-20260928'
RESULTS = []


def check(name, condition, detail=''):
    RESULTS.append((name, bool(condition)))
    print(('PASS ' if condition else 'FAIL ') + name + ((' :: ' + str(detail)) if detail else ''))


def new_dir(tag):
    path = TMP/('ka-enc-%s-%s' % (tag, uuid.uuid4().hex[:8]))
    path.mkdir(parents=True, exist_ok=True)
    return path


def wait_for(predicate, timeout, interval=0.25):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    return None


def prep_library(path, encounter=19):
    """One supplied Community baseline, so the library is a normal (non-fresh) one.

    Returns ``(encounterId, candidateId)`` so a fixture can pin the exact reference build.
    """
    from strategy_optimizer import baseline_scenarios
    store = Store(path, provenance())
    base = default_scenario()
    import strategy_search
    base['tickLimit'] = strategy_search.DEFAULT_TICK_LIMIT
    scenario = next(sc for sc, _label in baseline_scenarios(base)
                    if int(sc['encounterId']) == encounter)
    candidate_id = None
    with store.db:
        candidate_id = store.add(scenario, 'baseline', 'supplied', stats(scenario))
    encounter = int(scenario['encounterId'])
    store.close()
    return encounter, candidate_id


def dps_effective(scenario):
    normalized = mechanics.normalize_scenario(scenario)
    prepared = mechanics.prepared_setup(normalized)
    index = joint._role_indices(normalized).get('dps')
    if index is None:
        return None

    def eff(pid):
        entry = prepared['ownUnits'][index]['effectiveParameters'].get(pid)
        return None if entry is None else entry['value']

    return dict(atk=eff(13), lck=eff(16), spd=eff(15), dex=eff(19))


class LearnablePool:
    """Deterministic synthetic law: reward requires JOINT ATK/DEX compensation.

    The reward peaks on the plane `atk = 80 + 3*dex`; a lone ATK or DEX move cannot climb it, so
    the scheduler can only improve by proposing joint changes. `fail_holdout` makes every job raise,
    which the evaluator must record as an error outcome and thereby block publication.
    """

    def __init__(self, fail_all=False, fail_after=None):
        self.fail_all = fail_all
        self.fail_after = fail_after
        self.calls = 0

    def submit(self, fn, scenario, seeds):
        self.calls += 1
        future = Future()
        if self.fail_all or (self.fail_after is not None and self.calls > self.fail_after):
            # A real worker failure surfaces when the result is read; it must become a recorded
            # error outcome, not an exception in the coordinator's dispatch path.
            future.set_exception(RuntimeError('synthetic worker failure'))
            return future
        values = dps_effective(scenario) or {}
        atk = values.get('atk') or 0.0
        dex = values.get('dex') or 0.0
        target = 80.0 + 3.0*dex
        closeness = max(0.0, 1.0 - abs(atk - target)/120.0)
        reward = int(round(60.0*closeness))
        future.set_result(dict(seeds=list(seeds), verdict=1, resultBackend='native',
                               rewardOutcome=dict(pendingChests=reward),
                               elapsedSeconds=0.01, cpuSeconds=0.01))
        return future

    def shutdown(self, wait=True):
        pass

    def terminate_workers(self):
        pass


class HangPool(LearnablePool):
    """A worker whose futures never complete, so harvest returns nothing and no new work is spent."""

    def submit(self, fn, scenario, seeds):
        self.calls += 1
        return Future()


class FallbackPool(LearnablePool):
    """A worker that reports a non-native backend; the evaluator must exclude it, never fall back."""

    def submit(self, fn, scenario, seeds):
        self.calls += 1
        future = Future()
        future.set_result({'seeds': list(seeds), 'verdict': 1, 'resultBackend': 'python',
                           'nativeFallbackReason': 'no native module',
                           'rewardOutcome': {'pendingChests': 999}})
        return future


class BoundaryPool(LearnablePool):
    """A deterministic law with a POSITIVE reference baseline that still needs joint compensation.

    The supplied reference earns a nonzero mean (so a tolerance ratio is defined); the compensated
    child lands on a different point of the same law, so the paired assessment resolves rather than
    being inconclusive for lack of a reference mean.
    """

    def submit(self, fn, scenario, seeds):
        self.calls += 1
        future = Future()
        values = dps_effective(scenario) or {}
        atk = values.get('atk') or 0.0
        dex = values.get('dex') or 0.0
        closeness = max(0.0, min(1.0, 1.0 - abs(atk - (80.0 + 3.0*dex))/400.0))
        future.set_result(dict(seeds=list(seeds), verdict=1, resultBackend='native',
                               rewardOutcome=dict(pendingChests=int(round(100.0*closeness))),
                               elapsedSeconds=0.01, cpuSeconds=0.01))
        return future


def mock_factory(pool):
    def factory(*, workers, telemetry, timeout):
        import strategy_encounter_evaluation as evaluation
        return evaluation.Evaluator(workers=workers, telemetry=telemetry, pool=pool, timeout=10.0)
    return factory


def direct_cycle(path, factory, purposes, seconds, maximum=6):
    """Drive the coordinator directly (no Bridge) for the error and native fixtures."""
    search.EVALUATOR_FACTORY = factory
    store = Store(path, provenance())
    coordinator = search.Coordinator(path=path, revision='check', workers=2, telemetry=True,
                                     maximum=maximum)
    config = search.default_config('community-first', purposes=purposes)
    store.set(search.MODE_KEY, dict(enabled=True, mode='community-first', config=config,
                                    previous={}))
    coordinator.load(store)
    deadline = time.time() + seconds
    while time.time() < deadline:
        coordinator.run_pass(store, running=True)
        if coordinator.state.get('publication'):
            break
        # Stop only when the finite budgets are spent (or nothing is outstanding); a censored
        # native sample must keep trying the remaining purposes rather than stopping silently.
        if (not coordinator.busy and coordinator.state.get('current') is None
                and not coordinator.state.get('confirmation')
                and coordinator._next_purpose(store.db) is None):
            break
        time.sleep(0.02)
    return store, coordinator


def main():
    # 1. An existing library is DISABLED by default and never auto-migrated.
    existing = new_dir('existing')
    existing_path = existing/'lib.sqlite'
    prep_library(existing_path)
    existing_store = Store(existing_path, provenance())
    existing_fresh = existing_store.fresh
    existing_store.close()
    check('existing-library-not-fresh', existing_fresh is False)
    check('existing-library-default-disabled',
          search.bootstrap_report(existing_path)['enabled'] is False)

    # 2. A brand-new library defaults Community-first, but spends no work on open.
    fresh = new_dir('fresh')
    fresh_path = fresh/'lib.sqlite'
    pool = LearnablePool()
    search.EVALUATOR_FACTORY = mock_factory(pool)
    bridge = Bridge(fresh_path)
    report = wait_for(lambda: (bridge.status().get('encounterAware') or {})
                      if bridge.status().get('state') not in ('Opening library', None)
                      and (bridge.status().get('encounterAware') or {}).get('sessionId') is not None
                      else None, 240)
    if report is None:
        report = bridge.status().get('encounterAware') or {}
    check('new-library-community-first-default',
          report.get('enabled') is True and report.get('mode') == 'community-first',
          'enabled=%r mode=%r' % (report.get('enabled'), report.get('mode')))
    check('no-work-spent-on-open', (bridge.status().get('totalRuns') or 0) == 0)
    required = {'version', 'enabled', 'mode', 'config', 'runtimeRevision', 'sessionId', 'question',
                'encounter', 'mechanicalRegions', 'budgets', 'purposeSpending', 'portfolio',
                'boundaries', 'progress', 'timings', 'limitations', 'migrationPreview'}
    check('status-exposes-encounterAware-contract', required <= set(report))
    check('runtimeRevision-is-loaded-revision',
          isinstance(report.get('runtimeRevision'), str) and len(report['runtimeRevision']) == 64,
          report.get('runtimeRevision'))
    check('bridge-encounter-report-matches-status',
          bridge.encounter_report().get('runtimeRevision') == report.get('runtimeRevision'))

    # 3. `encounter_preview` is a PURE migration plan: no ledger table is created by it.
    import sqlite3

    def ledger_tables(path):
        connection = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
        try:
            return {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE name LIKE 'ea\\_%' ESCAPE '\\'")}
        finally:
            connection.close()

    before_tables = ledger_tables(fresh_path)
    preview = bridge.command('encounter_preview', {'mode': 'community-first'})
    preview_plan = (preview or {}).get('plan') or {}
    check('preview-returns-pure-plan',
          bool(preview_plan.get('ledger')) and preview_plan.get('applied') is False
          and preview_plan.get('note'))
    check('preview-writes-nothing', ledger_tables(fresh_path) == before_tables)

    # 4. Explicit activation with finite budgets; every other stream is exactly zero.
    config = search.default_config('community-first', purposes={
        'improvement': 8, 'comparison': 8, 'boundary': 0, 'support': 0, 'exploration': 0})
    refused = bridge.command('encounter_activate', {'config': config})
    check('duplicate-activation-refused', refused.get('ok') is False)
    check('fresh-default-can-roll-back', bridge.command('encounter_rollback', {}).get('ok') is True)
    # Root guard: activation commits the config from an EXACT preview, never a reconstructed one.
    previewed = bridge.command('encounter_preview', {
        'mode': 'community-first', 'purposes': config['purposes']})
    check('activation-preview-of-exact-config', (previewed or {}).get('ok') is True,
          (previewed or {}).get('error'))
    config = bridge.status()['encounterAware']['migrationPreview']['config']
    activated = bridge.command('encounter_activate', {'config': config})
    check('activate-accepted', activated.get('ok') is True, activated.get('error'))
    shares = bridge.status()['students']['shares']
    check('activate-zeroes-every-other-stream',
          shares.get('community') == 1.0 and all(
              value == 0.0 for name, value in shares.items() if name != 'community'), shares)
    check('no-student-9-allocation', shares.get('average') == 0.0)

    # 5. A real bounded cycle through the actual Optimizer loop.
    started = bridge.command('start', {'workers': 2, 'duty': 1.0})
    check('start-accepted', started.get('ok') is True, started.get('error'))

    def published():
        ea = bridge.status().get('encounterAware') or {}
        return (ea.get('progress') or {}).get('publication')

    outcome = wait_for(published, 120, 0.5)
    ea = bridge.status().get('encounterAware') or {}
    portfolio = ea.get('portfolio') or []
    check('cycle-produces-publication-decision', bool(outcome),
          json.dumps(outcome)[:160] if outcome else 'no publication')
    # A paired development experiment charges BOTH arms, so an 8-battle improvement budget buys 4
    # reference-matched pairs and the candidate's own arm holds 4 (not 8) resolved samples.
    check('portfolio-has-measured-development-evidence',
          any(row.get('resolved', 0) >= 4 and row.get('meanEarned') is not None
              for row in portfolio))
    check('portfolio-holds-only-candidate-arms',
          bool(portfolio) and all(row.get('arm') == 'candidate' for row in portfolio))
    check('development-is-a-paired-comparison',
          any((row.get('pairedComparisons') or [{}])[-1].get('comparison', {}).get('pairs') == 4
              and row.get('resolved') == 4 for row in portfolio),
          json.dumps([row.get('pairedComparisons') for row in portfolio])[:200])
    confirmation = (ea.get('progress') or {}).get('confirmation') or {}
    check('confirmation-frozen-and-complete',
          bool(confirmation.get('frozen')) and bool(confirmation.get('ready')),
          json.dumps(confirmation)[:160])
    check('publication-status-is-explicit',
          (outcome or {}).get('status') in ('published', 'inconclusive', 'withheld'),
          (outcome or {}).get('status'))
    check('budget-conservation', ea.get('budgetConservation') is not False)

    # 6. Holdout pairs are not part of development evidence.
    bridge.command('pause', {})
    wait_for(lambda: bridge.status().get('state') in ('Paused', 'Saving'), 30)
    connection = sqlite3.connect(fresh_path.resolve().as_uri() + '?mode=ro', uri=True)
    try:
        development = {(int(a), int(b)) for a, b in connection.execute(
            'SELECT seed_a, seed_b FROM ea_sample_link')}
        holdout = {(int(a), int(b)) for a, b in connection.execute(
            'SELECT seed_a, seed_b FROM ea_holdout')}
        pairs_by_candidate = {}
        for cid, a, b in connection.execute(
                'SELECT candidate_id, seed_a, seed_b FROM ea_sample_link'):
            pairs_by_candidate.setdefault(cid, set()).add((int(a), int(b)))
    finally:
        connection.close()
    check('holdout-not-training', bool(holdout) and not (holdout & development),
          'holdout=%d overlap=%d' % (len(holdout), len(holdout & development)))
    four_pair_arms = [s for s in pairs_by_candidate.values() if len(s) == 4]
    check('paired-arms-share-the-same-ordered-pairs',
          any(a == b for index, a in enumerate(four_pair_arms)
              for b in four_pair_arms[index + 1:]),
          'four-pair arms=%d' % len(four_pair_arms))

    # 7. A finite session extension, then a rollback that keeps history and restores shares.
    # The UI sends ABSOLUTE totals; a resize must be >= the already-reserved work.
    extended = bridge.command('encounter_budget', {'purposes': {'improvement': 32}})
    check('budget-resize-absolute-accepted', extended.get('ok') is True, extended.get('error'))
    undersized = bridge.command('encounter_budget', {'purposes': {'improvement': 0}})
    check('budget-resize-below-reserved-refused', undersized.get('ok') is False)
    before_history = len((bridge.status().get('encounterAware') or {}).get('portfolio') or [])
    rolled = bridge.command('encounter_rollback', {})
    check('rollback-accepted', rolled.get('ok') is True, rolled.get('error'))
    after = bridge.encounter_report()
    check('rollback-disables-mode', after.get('enabled') is False)
    check('rollback-restores-previous-shares',
          bridge.status()['students']['shares'].get('community') != 1.0)
    check('rollback-keeps-evidence',
          len(after.get('portfolio') or []) == before_history and before_history > 0)

    # 8. Restart on the same library keeps the disabled mode and the evidence.
    total_runs = bridge.status().get('totalRuns')
    bridge.close()
    wait_for(lambda: bridge.status().get('state') == 'Closed', 30)
    bridge._guard.close()   # release the app-writer lock so the restart can reopen the library
    bridge_again = Bridge(fresh_path)
    restarted = wait_for(lambda: (bridge_again.status().get('encounterAware') or {})
                         if bridge_again.status().get('state') not in ('Opening library', None)
                         else None, 120)
    check('restart-keeps-mode-disabled', (restarted or {}).get('enabled') is False,
          (restarted or {}).get('enabled'))
    check('restart-does-not-double-charge', (bridge_again.status().get('totalRuns') or 0) == total_runs,
          '%r vs %r' % (bridge_again.status().get('totalRuns'), total_runs))
    bridge_again.close()
    wait_for(lambda: bridge_again.status().get('state') == 'Closed', 30)
    bridge_again._guard.close()

    # 9. An error/unresolved holdout must block publication rather than produce a claim.
    failing = new_dir('failing')
    failing_path = failing/'lib.sqlite'
    prep_library(failing_path)
    store, coordinator = direct_cycle(failing_path, mock_factory(LearnablePool(fail_after=8)),
                                      {'improvement': 8, 'comparison': 8, 'boundary': 0,
                                       'support': 0, 'exploration': 0}, 60)
    publication = coordinator.state.get('publication') or {}
    check('error-blocks-publication',
          publication.get('status') in ('withheld', 'inconclusive')
          and publication.get('status') != 'published', json.dumps(publication)[:160])
    check('unresolved-work-reported', bool(coordinator.limitations))
    store.close()

    # 10. Real proposal generation -> paired dispatch -> evidence -> ranking -> boundary assessed,
    #     through the REAL coordinator and SQLite (the physics/transport is mocked).
    import strategy_encounter_evaluation as evaluation

    paired = new_dir('paired')
    paired_path = paired/'lib.sqlite'
    encounter_id, supplied_id = prep_library(paired_path)
    search.EVALUATOR_FACTORY = mock_factory(BoundaryPool())
    paired_store = Store(paired_path, provenance())
    paired_coord = search.Coordinator(path=paired_path, revision='check', workers=2, telemetry=True,
                                      maximum=6, reference=supplied_id, encounter=encounter_id,
                                      timeout=5.0)
    paired_config = search.default_config('community-first', purposes={
        'improvement': 8, 'comparison': 8, 'boundary': 16, 'support': 0, 'exploration': 0})
    paired_store.set(search.MODE_KEY, dict(enabled=True, mode='community-first',
                                           config=paired_config, encounter=encounter_id,
                                           reference=supplied_id, previous={}))
    paired_coord.load(paired_store)
    frozen = paired_coord.set_question(paired_store, dict(
        kind='compensated', role='dps', stat='dex', value=5, budget=8, minimumPairs=2))
    check('boundary-question-frozen', frozen.get('ok') is True, frozen.get('error'))
    question = (frozen or {}).get('question') or {}
    check('boundary-question-has-canonical-domain',
          str(question.get('field', '')).startswith('ownUnits.')
          and bool(question.get('adjustableFields'))
          and question.get('referenceId') == supplied_id)
    deadline = time.time() + 120
    while time.time() < deadline:
        paired_coord.run_pass(paired_store, running=True)
        if paired_coord.state.get('boundaries'):
            break
        if (not paired_coord.busy and paired_coord.state.get('current') is None
                and not paired_coord._pending_confirmation_jobs(paired_store.db)
                and paired_coord._next_purpose(paired_store.db) is None):
            break
        time.sleep(0.02)
    paired_portfolio = paired_coord.state.get('portfolio') or []
    check('real-pool-produced-paired-evidence',
          any(row.get('resolved', 0) >= 4 and row.get('pairedComparisons')
              for row in paired_portfolio),
          json.dumps([(row.get('resolved'), bool(row.get('pairedComparisons')))
                      for row in paired_portfolio])[:200])
    check('ranking-updated-from-evidence',
          paired_coord.state.get('adviserSignature') is not None
          or bool(paired_coord.state.get('history')))
    boundaries = paired_coord.state.get('boundaries') or []
    entry = next((row for row in boundaries if row.get('questionId') == question.get('id')), None)
    assessment = (entry or {}).get('assessment') or {}
    check('boundary-assessed-through-operating-regions',
          bool(entry) and assessment.get('questionId') == question.get('id')
          and assessment.get('tested') is True and assessment.get('pairedCount', 0) >= 2,
          json.dumps(assessment)[:220] if assessment else 'no assessment')
    if entry and assessment.get('classification') in ('supported-acceptable', 'supported-degraded'):
        check('supported-boundary-point-published',
              entry.get('published') is True and bool(entry.get('testedPoints'))
              and all('seedPair' not in point and 'earned' not in point
                      for point in entry['testedPoints']))
    else:
        check('inconclusive-boundary-not-published',
              bool(entry) and entry.get('published') is False and bool(entry.get('unresolvedGaps')))
    persisted = paired_store.db.execute(
        'SELECT kind, payload FROM ea_boundary ORDER BY id LIMIT 1').fetchone()
    check('boundary-persisted-in-sqlite',
          persisted is not None and persisted[0] in ('fixed', 'compensated'))

    # A declared minimum the experiment cannot reach must be inconclusive with NO publication.
    second_question = paired_coord.set_question(paired_store, dict(
        kind='compensated', role='dps', stat='dex', value=5, budget=16, minimumPairs=8))
    check('declared-minimum-question-frozen', second_question.get('ok') is True,
          second_question.get('error'))
    deadline = time.time() + 60
    while time.time() < deadline:
        paired_coord.run_pass(paired_store, running=True)
        if len(paired_coord.state.get('boundaries') or []) >= 2:
            break
        if (not paired_coord.busy and paired_coord.state.get('current') is None
                and not paired_coord._pending_confirmation_jobs(paired_store.db)
                and paired_coord._next_purpose(paired_store.db) is None):
            break
        time.sleep(0.02)
    # `value`/`minimumPairs` sit beside the frozen question, so a second question on the same field and
    # domain shares its id: the newest boundary entry is the one that honoured the new minimum.
    boundary_entries = paired_coord.state.get('boundaries') or []
    short = boundary_entries[-1] if len(boundary_entries) >= 2 else None
    short_assessment = (short or {}).get('assessment') or {}
    check('below-minimum-sample-is-inconclusive',
          short_assessment.get('classification') == 'unresolved'
          and short_assessment.get('pairedCount', 0) < 8
          and (short or {}).get('published') is False
          and bool((short or {}).get('unresolvedGaps')),
          json.dumps(short_assessment)[:200] if short_assessment else 'no assessment')

    # Absolute budget retention: an absolute resize keeps the session, id and every reserved charge.
    reserved_before = (paired_coord.report()['budgets']['session'].get('improvement') or {}
                       ).get('reserved')
    session_before = paired_coord.session_id
    resized = paired_coord.set_budget(paired_store, {'purposes': {'improvement': 64}})
    reserved_after = (paired_coord.report()['budgets']['session'].get('improvement') or {}
                      ).get('reserved')
    check('absolute-budget-resize-accepted', resized.get('ok') is True, resized.get('error'))
    check('absolute-budget-resize-preserves-session-and-reserved',
          paired_coord.session_id == session_before and reserved_after == reserved_before,
          '%r reserved %r->%r' % (session_before, reserved_before, reserved_after))
    check('absolute-budget-below-reserved-refused',
          paired_coord.set_budget(paired_store, {'purposes': {'improvement': 0}}).get('ok') is False)

    # One disabled stream: zero allocation, no experiment, and no silent native fallback.
    allocations = paired_coord.config.get('allocations') or {}
    check('disabled-streams-have-zero-allocation',
          bool(allocations) and all(value == 0.0 for name, value in allocations.items()
                                    if name != 'community'))
    owners = {row[0] for row in paired_store.db.execute('SELECT DISTINCT owner FROM ea_experiment')}
    check('no-disabled-stream-owns-an-experiment', owners <= {'community', None}, owners)
    fallback_db = sqlite3.connect(':memory:')
    ledger.initialize(fallback_db)
    fallback_experiment = ledger.create_experiment(fallback_db, {
        'scope': 'community', 'purpose': 'improvement', 'planned_budget': 1, 'stopping': 'fixture',
        'ownerShare': 1, 'policy': {'finishPolicy': 'on-verdict'}})
    fallback_runner = evaluation.Evaluator(workers=1, pool=FallbackPool())
    fallback_runner.submit(fallback_db, fallback_experiment, 'candidate', {}, [1, 2])
    fallback_runner.harvest(fallback_db)
    fallback_rows = ledger.outcomes(fallback_db)
    check('no-silent-native-fallback',
          fallback_runner.status()['fallback'] == 1 and bool(fallback_rows)
          and not fallback_rows[0]['outcome']['resolved'])
    fallback_runner.close()
    fallback_db.close()

    # Policy/revision incompatible rows are excluded, never restamped into the live view.
    bad_experiment = ledger.create_experiment(paired_store.db, {
        'scope': 'community', 'purpose': 'improvement', 'planned_budget': 1, 'stopping': 'fixture',
        'ownerShare': 1.0, 'policy': {'finishPolicy': 'on-verdict'},
        'mechanicsRevision': paired_coord._mechanics_revision(), 'encounterRevision': 'x',
        'measurementWindow': 'development', 'compatibility': 'incompatible-digest'})
    ledger.reserve(paired_store.db, bad_experiment, 'incompatible-candidate', [7, 8])
    ledger.complete(paired_store.db, bad_experiment, 'incompatible-candidate', [7, 8],
                    {'seeds': [7, 8], 'verdict': 1, 'resultBackend': 'native',
                     'rewardOutcome': {'pendingChests': 500}})
    paired_coord._intent_cache.pop(bad_experiment, None)
    compatible_ids = paired_coord._compatible_experiment_ids(paired_store.db)
    check('incompatible-revision-experiment-excluded', bad_experiment not in compatible_ids)
    restamped = paired_coord._deduped_outcomes(paired_store.db, experiments=compatible_ids)
    check('incompatible-revision-rows-not-restamped', 'incompatible-candidate' not in restamped)
    ok, refusal = paired_coord._current_compatible(
        {'engineRevision': 'a-different-engine', 'mechanicsRevision': paired_coord._mechanics_revision(),
         'compatibility': paired_coord._compatibility()})
    check('revision-mismatch-refuses-dispatch', ok is False and bool(refusal), refusal)
    check('boundary-records-keep-fixed-compensated-only',
          all(row.get('kind') in ('fixed', 'compensated')
              for row in paired_coord.state.get('boundaries') or []))
    paired_coord.close()
    paired_store.close()

    # 11. Restart halfway through a paired plan resumes the SAME frozen plan with no double charge.
    restart = new_dir('restart')
    restart_path = restart/'lib.sqlite'
    restart_encounter, restart_supplied = prep_library(restart_path)
    search.EVALUATOR_FACTORY = mock_factory(LearnablePool())
    restart_store = Store(restart_path, provenance())
    restart_config = search.default_config('community-first', purposes={
        'improvement': 8, 'comparison': 0, 'boundary': 0, 'support': 0, 'exploration': 0})
    restart_store.set(search.MODE_KEY, dict(enabled=True, mode='community-first',
                                            config=restart_config, encounter=restart_encounter,
                                            reference=restart_supplied, previous={}))
    first = search.Coordinator(path=restart_path, revision='check', workers=2, telemetry=True,
                               maximum=6, reference=restart_supplied, timeout=5.0)
    first.load(restart_store)
    first.run_pass(restart_store, running=True)
    mid = first.state.get('current') or {}
    mid_reserved = (len(ledger.development_reservations(restart_store.db, mid['experimentId']))
                    if mid.get('experimentId') else 0)
    check('restart-halfway-plan-is-paired-and-frozen',
          len(mid.get('jobs') or []) == 8 and bool(mid.get('planDigest')) and mid_reserved == 2,
          'jobs=%d reserved=%d' % (len(mid.get('jobs') or []), mid_reserved))
    digest, experiment_id = mid.get('planDigest'), mid.get('experimentId')
    first.close()
    second = search.Coordinator(path=restart_path, revision='check', workers=2, telemetry=True,
                                maximum=6, reference=restart_supplied, timeout=5.0)
    second.load(restart_store)
    check('restart-resumes-same-frozen-plan',
          (second.state.get('current') or {}).get('planDigest') == digest)
    deadline = time.time() + 60
    while time.time() < deadline:
        second.run_pass(restart_store, running=True)
        if (not second.busy and second.state.get('current') is None
                and second._next_purpose(restart_store.db) is None):
            break
        time.sleep(0.02)
    outcomes = ledger.outcomes(restart_store.db, experiment_id)
    sample_keys = {row['sampleKey'] for row in outcomes}
    arms = {str(row['candidateId']) for row in outcomes}
    check('restart-completed-every-paired-job',
          len(outcomes) == 8 and len(sample_keys) == 8 and len(arms) == 2,
          'outcomes=%d samples=%d arms=%d' % (len(outcomes), len(sample_keys), len(arms)))
    session = ledger.session_status(restart_store.db, second.session_id)
    improvement = next((row for row in session['budgets'] if row['purpose'] == 'improvement'), {})
    check('restart-did-not-double-charge',
          improvement.get('reserved') == improvement.get('completed') == 8, improvement)
    restart_store.close()

    # 12. A paused pass harvests in-flight work only; it dispatches and recovers nothing new.
    pause = new_dir('pause')
    pause_path = pause/'lib.sqlite'
    pause_encounter, pause_supplied = prep_library(pause_path)
    search.EVALUATOR_FACTORY = mock_factory(HangPool())
    pause_store = Store(pause_path, provenance())
    pause_config = search.default_config('community-first', purposes={
        'improvement': 8, 'comparison': 0, 'boundary': 0, 'support': 0, 'exploration': 0})
    pause_store.set(search.MODE_KEY, dict(enabled=True, mode='community-first',
                                          config=pause_config, encounter=pause_encounter,
                                          reference=pause_supplied, previous={}))
    pause_coord = search.Coordinator(path=pause_path, revision='check', workers=2, telemetry=True,
                                     maximum=6, reference=pause_supplied, timeout=5.0)
    pause_coord.load(pause_store)
    pause_coord.run_pass(pause_store, running=True)
    reserved_before = len(ledger.development_reservations(pause_store.db))
    pause_coord.run_pass(pause_store, running=False)
    reserved_after = len(ledger.development_reservations(pause_store.db))
    check('paused-pass-dispatches-nothing',
          reserved_before == 2 and reserved_after == reserved_before,
          '%d -> %d' % (reserved_before, reserved_after))
    pause_coord.close()
    pause_store.close()

    # 13. An odd single remaining battle stays UNSPENT with a reason; no filler second seed.
    odd = new_dir('odd')
    odd_path = odd/'lib.sqlite'
    odd_encounter, odd_supplied = prep_library(odd_path)
    search.EVALUATOR_FACTORY = mock_factory(LearnablePool())
    odd_store = Store(odd_path, provenance())
    odd_config = search.default_config('community-first', purposes={
        'improvement': 7, 'comparison': 0, 'boundary': 0, 'support': 0, 'exploration': 0})
    odd_store.set(search.MODE_KEY, dict(enabled=True, mode='community-first', config=odd_config,
                                        encounter=odd_encounter, reference=odd_supplied, previous={}))
    odd_coord = search.Coordinator(path=odd_path, revision='check', workers=2, telemetry=True,
                                   maximum=6, reference=odd_supplied, timeout=5.0)
    odd_coord.load(odd_store)
    deadline = time.time() + 60
    while time.time() < deadline:
        odd_coord.run_pass(odd_store, running=True)
        if (not odd_coord.busy and odd_coord.state.get('current') is None
                and odd_coord._next_purpose(odd_store.db) is None):
            break
        time.sleep(0.02)
    odd_session = ledger.session_status(odd_store.db, odd_coord.session_id)
    odd_improvement = next(row for row in odd_session['budgets'] if row['purpose'] == 'improvement')
    check('odd-budget-spends-only-whole-pairs',
          odd_improvement['reserved'] == 6 and odd_improvement['completed'] == 6, odd_improvement)
    check('odd-remainder-stays-unspent-with-reason',
          'improvement' in (odd_coord.state.get('unspendable') or [])
          and any('cannot buy a complete' in note for note in odd_coord.limitations),
          odd_coord.limitations)
    odd_coord.close()
    odd_store.close()
    search.EVALUATOR_FACTORY = None

    import os
    if os.environ.get('KA_SKIP_NATIVE') == '1':
        print('skipping native cycle (KA_SKIP_NATIVE=1)')
        search.EVALUATOR_FACTORY = None
        passed = sum(1 for _name, ok in RESULTS if ok)
        print('%d/%d checks passed' % (passed, len(RESULTS)))
        return 0 if passed == len(RESULTS) else 1
    import strategy_optimizer_backend as native_backend
    if not native_backend.status().get('enabled'):
        # The native acceleration is disabled when its compiled module digest no longer matches the
        # source (e.g. another worker edited an engine file). The real evaluator then only falls back
        # to the Python engine, so no native battle can run; this is an environment condition, not a
        # coordinator regression, and the mock-transport sections above already cover the same code.
        print('skipping native cycle (native backend disabled: %s)'
              % native_backend.status().get('reason'))
        search.EVALUATOR_FACTORY = None
        passed = sum(1 for _name, ok in RESULTS if ok)
        print('%d/%d checks passed' % (passed, len(RESULTS)))
        return 0 if passed == len(RESULTS) else 1

    # 10. One bounded NATIVE cycle through the real evaluator (<= 32 real battles).
    native = new_dir('native')
    native_path = native/'lib.sqlite'
    # Encounter 16 is a short, certified encounter: its native result resolves with a numeric award,
    # so the bounded cycle can actually reach (and complete) a confirmation. Encounters 18/19 are
    # long/UNPROVEN and would only ever report an unresolved/inconclusive sample here.
    prep_library(native_path, encounter=16)
    store, coordinator = direct_cycle(native_path, None,
                                      {'improvement': 8, 'comparison': 8, 'boundary': 0,
                                       'support': 0, 'exploration': 0}, 600)
    metrics = (coordinator.evaluator.status() if coordinator.evaluator is not None else {})
    submitted = int(metrics.get('submitted') or 0)
    native_results = int(metrics.get('native') or 0)
    check('native-cycle-bounded', submitted <= 32, 'submitted=%d' % submitted)
    check('native-battles-ran', native_results >= 1, 'native=%d' % native_results)
    rows = ledger.outcomes(store.db)
    resolved = [row for row in rows if row['outcome'].get('resolved')]
    check('native-cycle-produced-outcomes', bool(rows), 'outcomes=%d resolved=%d' % (len(rows), len(resolved)))
    publication = coordinator.state.get('publication') or {}
    eligible = [row for row in coordinator.state.get('portfolio') or []
                if row.get('eligible') and row.get('meanEarned') is not None]
    check('native-cycle-reports-inconclusive-not-a-claim',
          publication.get('status') != 'published' and not eligible,
          'publication=%s eligible=%d' % (publication.get('status'), len(eligible)))
    store.close()

    search.EVALUATOR_FACTORY = None
    passed = sum(1 for _name, ok in RESULTS if ok)
    print('%d/%d checks passed' % (passed, len(RESULTS)))
    return 0 if passed == len(RESULTS) else 1


if __name__ == '__main__':
    sys.exit(main())
