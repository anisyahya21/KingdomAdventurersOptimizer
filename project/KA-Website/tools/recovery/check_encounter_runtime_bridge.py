"""Bounded, mock-only checks for the encounter runtime bridge.

Run:  <venv>/python -B -X utf8 tools/recovery/check_encounter_runtime_bridge.py

No battle is run and no native pool is started: the evaluator is driven by a stub pool that returns
immediate synthetic results, and every migration helper used here is a synthetic stand-in. The file
is about the runtime bridge in `strategy_optimizer.py` only:

  * ea_* session counters/timing derived from the persisted ledger (coalesced samples counted once),
  * event-driven publishing while paused (not once per loop iteration),
  * activation prevalidation + preview binding + atomic rollback on failure,
  * rollback grant lifecycle and exact share/track restore,
  * the legacy command guards while encounter mode is enabled.
"""
from __future__ import annotations

import sys
import time
import uuid
from concurrent.futures import Future
from copy import deepcopy
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
WORKSPACE = HERE.parents[1]

import strategy_encounter_migration as migration              # noqa: E402
import strategy_encounter_search as search                    # noqa: E402
import strategy_experiment_store as ledger                    # noqa: E402
import strategy_search_mode as modes                          # noqa: E402
import strategy_students as students                          # noqa: E402
from strategy_optimizer import Optimizer, Store, canonical    # noqa: E402
from strategy_optimizer_adapter import provenance             # noqa: E402
from strategy_optimizer_desktop import Bridge                 # noqa: E402

TMP = WORKSPACE/'tmp/encounter-redesign-20260928'
RESULTS = []


def check(name, condition, detail=''):
    RESULTS.append((name, bool(condition)))
    print(('PASS ' if condition else 'FAIL ') + name + ((' :: ' + str(detail)[:700]) if detail else ''))


def new_dir(tag):
    path = TMP/('ka-rt-%s-%s' % (tag, uuid.uuid4().hex[:8]))
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


class ConstantPool:
    """Immediate synthetic native results; no worker, no battle, no native module."""

    def __init__(self):
        self.calls = 0

    def submit(self, fn, scenario, seeds):
        self.calls += 1
        future = Future()
        future.set_result(dict(seeds=list(seeds), verdict=1, resultBackend='native',
                               rewardOutcome=dict(pendingChests=4), elapsedSeconds=0.01,
                               cpuSeconds=0.01))
        return future

    def shutdown(self, wait=True):
        pass

    def terminate_workers(self):
        pass


def mock_factory(pool):
    def factory(*, workers, telemetry, timeout):
        import strategy_encounter_evaluation as evaluation
        return evaluation.Evaluator(workers=workers, telemetry=telemetry, pool=pool, timeout=10.0)
    return factory


# -- pure helper checks --------------------------------------------------------------------------
def helper_checks():
    report = dict(enabled=True, budgets={'session': {
        'improvement': dict(total=8, reserved=6, completed=4),
        'comparison': dict(total=8, reserved=2, completed=2)}},
        timings={'evaluator': dict(completed=6, simulationSeconds=1.2, inflight=2)})
    counts = Optimizer._encounter_counters(report)
    check('session-counters-summed-once',
          counts == dict(total=16, reserved=8, completed=6), counts)

    signature = Optimizer._encounter_publish_signature(report, 'Paused', None)
    check('publish-signature-stable',
          Optimizer._encounter_publish_signature(deepcopy(report), 'Paused', None) == signature)
    moved = deepcopy(report)
    moved['timings']['evaluator']['completed'] = 7
    check('publish-signature-changes-on-completion',
          Optimizer._encounter_publish_signature(moved, 'Paused', None) != signature)
    check('publish-signature-changes-on-state',
          Optimizer._encounter_publish_signature(report, 'Running', None) != signature)
    check('publish-signature-changes-on-error',
          Optimizer._encounter_publish_signature(report, 'Paused', 'boom') != signature)

    rate = Optimizer.__new__(Optimizer)
    rate._encounter = None
    rate._encounter_report = None
    rate._encounter_ledger = None
    rate._session_runs_base = None
    rate._encounter_session_base = None
    rate._workers, rate._duty = 4, 0.9
    rate._rate_samples, rate._engine_samples = [], []
    rate._encounter_rate_samples = []

    path = new_dir('rate')/'rate.sqlite'
    store = Store(path, provenance())
    try:
        legacy = rate._bridge_rate_view(store)
        check('legacy-view-when-no-ledger',
              legacy['totalRuns'] == 0 and legacy['sessionRuns'] is None
              and legacy['sessionRunsBasis'] == 'legacy-session'
              and legacy['throughput']['secondsPerRunBasis'] == 'library-average', legacy)
        ledger.initialize(store.db)
        with store.db:
            store.set('totalRuns', 5)
            store.db.execute("INSERT INTO ea_sample(sample_key,candidate_id,seed_a,seed_b,result,"
                             "created_at) VALUES('k1','c1',1,2,'{}',0)")
            store.db.execute("INSERT INTO ea_sample(sample_key,candidate_id,seed_a,seed_b,created_at) "
                             "VALUES('k2','c1',3,4,0)")
            # Two links to the SAME sample: the coalesced copy must not be counted twice.
            store.db.execute("INSERT INTO ea_sample_link(experiment_id,candidate_id,seed_a,seed_b,"
                             "sample_key,reused,charged_experiment_id,created_at) "
                             "VALUES(1,'c1',1,2,'k1',0,1,0)")
            store.db.execute("INSERT INTO ea_sample_link(experiment_id,candidate_id,seed_a,seed_b,"
                             "sample_key,reused,charged_experiment_id,created_at) "
                             "VALUES(2,'c1',1,2,'k1',1,1,0)")
        rate._encounter_ledger = None
        counted = rate._bridge_rate_view(store)
        check('lifetime-counts-distinct-completed-samples', counted['encounterRuns'] == 1, counted)
        check('total-runs-adds-encounter-lifetime',
              counted['totalRuns'] == 6 and counted['legacyTotalRuns'] == 5, counted)

        class Enabled:
            enabled = True

        rate._encounter = Enabled()
        rate._encounter_report = report
        rate._encounter_session_base = 1
        view = rate._bridge_rate_view(store)
        check('encounter-session-runs-is-delta',
              view['sessionRuns'] == 5 and view['sessionRunsBasis'] == 'encounter-session', view)
        check('encounter-throughput-basis', view['throughput']['basis'] == 'encounter-ledger'
              and abs(view['throughput']['secondsPerRun']-0.2) < 1e-9, view['throughput'])
        check('encounter-capacity-uses-workers-and-duty',
              abs(view['throughput']['capacityBattlesPerSecond'] - (4*0.9/0.2)) < 1e-9,
              view['throughput'])
    finally:
        store.close()


# -- activation / rollback with synthetic migration ----------------------------------------------
class FakeEncounter:
    def __init__(self, config):
        self.config = config
        self.mode = config['mode']
        self.session_id = None
        self.previous = {}
        self.enabled = False
        self.activated = False
        self.constraints = None
        self.encounter = None
        self.reference = None
        self.fail = False
        self.activate_calls = 0

    def activate(self, store, value):
        self.activate_calls += 1
        config = dict(value['config'])
        self.config, self.mode = config, config['mode']
        ledger.initialize(store.db)
        self.session_id = ledger.configure_session(store.db, config)
        self.previous = dict(shares=dict(value.get('previousShares') or {}),
                             tracks=dict(value.get('previousTracks') or {}),
                             mapping=value.get('previousMapping') or {})
        ledger.activate_exclusive_session(store.db, self.session_id)
        with ledger._atomic(store.db):
            store.set(search.MODE_KEY, dict(enabled=True, mode=self.mode, config=config,
                                            constraints=self.constraints, encounter=self.encounter,
                                            reference=self.reference, previous=self.previous))
        if self.fail:
            raise RuntimeError('synthetic activation failure after partial writes')
        self.enabled, self.activated = True, True
        return dict(ok=True, sessionId=self.session_id, mode=self.mode)


def stub_optimizer(encounter):
    opt = Optimizer.__new__(Optimizer)
    opt._encounter = encounter
    opt.provenance = provenance()
    opt._student_shares = dict(students.shares())
    opt._track_shares = dict(students.track_shares())
    opt._planner_dirty = False
    opt._encounter_preview_token = None
    opt._encounter_ledger = None
    opt._encounter_session_base = None
    return opt


def activation_checks():
    path = new_dir('activate')/'lib.sqlite'
    store = Store(path, provenance())
    saved = (migration.preview, migration.apply, migration.rollback)
    calls = dict(apply=0, rollback=0)

    def fake_preview(old, current):
        return dict(eligible=True, planId='synthetic-plan', proof={'synthetic': True},
                    limitation='synthetic test stand-in')

    def fake_apply(store_, current, plan_id):
        calls['apply'] += 1
        assert plan_id == 'synthetic-plan'
        with ledger._atomic(store_.db):
            store_.set(migration.KEY, dict(proof={'synthetic': True}, appliedAt=time.time(),
                                           originalProvenancePreserved=True))
        store_.compatible = True

    def fake_rollback(store_, current):
        calls['rollback'] += 1
        with store_.db:
            store_.db.execute('DELETE FROM meta WHERE key=?', (migration.KEY,))
        store_.compatible = False

    migration.preview, migration.apply, migration.rollback = fake_preview, fake_apply, fake_rollback
    config = search.default_config('community-first', purposes={
        'improvement': 8, 'comparison': 8, 'boundary': 0, 'support': 0, 'exploration': 0})
    try:
        encounter = FakeEncounter(config)
        opt = stub_optimizer(encounter)
        store.compatible = False

        # (a) migration required but not previewed: refuse before any write.
        try:
            opt._activate_encounter(store, {'config': config, 'migrationPlanId': 'synthetic-plan'})
            check('activation-refused-without-preview', False)
        except ValueError as exc:
            check('activation-refused-without-preview', 'preview' in str(exc).lower(), exc)
        check('no-grant-without-preview', calls['apply'] == 0 and store.compatible is False, calls)

        # (b) previewed, but activation fails after partial writes: roll back atomically.
        opt._encounter_preview_token = dict(migrationPlanId='synthetic-plan', required=True,
            configDigest=canonical(config), scopeDigest=canonical({key: getattr(encounter, key, None)
                for key in ('encounter', 'reference', 'constraints', 'thresholds')}))
        changed_config = deepcopy(config)
        changed_config['purposes']['improvement'] += 2
        for changed in ({'config': changed_config}, {'config': config, 'encounter': 15}):
            try:
                opt._activate_encounter(store, dict(changed, migrationPlanId='synthetic-plan'))
            except ValueError:
                check('changed-preview-refused-before-grant', calls['apply'] == 0)
            else:
                check('changed-preview-refused-before-grant', False)
        before_mapping = store.get(search.MODE_KEY)
        before_shares = dict(opt._student_shares)
        before_tracks = dict(opt._track_shares)
        encounter.fail = True
        try:
            opt._activate_encounter(store, {'config': config, 'migrationPlanId': 'synthetic-plan'})
            check('activation-failure-raised', False)
        except RuntimeError:
            check('activation-failure-raised', True)
        created = store.db.execute('SELECT COUNT(*) FROM ea_session WHERE active=1').fetchone()[0]
        check('failed-activation-deactivates-session', created == 0, created)
        check('failed-activation-removes-grant',
              calls['rollback'] == 0 and store.compatible is False
              and store.get(migration.KEY) is None, calls)
        check('failed-activation-restores-mapping', store.get(search.MODE_KEY) == before_mapping)
        check('failed-activation-restores-in-memory',
              opt._student_shares == before_shares and opt._track_shares == before_tracks
              and opt._planner_dirty is False and encounter.enabled is False)

        # (c) previewed and healthy: the grant, mapping and session activate together.
        encounter.fail = False
        store.compatible = False
        store.db.execute('DELETE FROM ea_session')
        applies, rollbacks = calls['apply'], calls['rollback']
        opt._encounter_preview_token = dict(migrationPlanId='synthetic-plan', required=True,
            configDigest=canonical(config), scopeDigest=canonical({key: getattr(encounter, key, None)
                for key in ('encounter', 'reference', 'constraints', 'thresholds')}))
        result = opt._activate_encounter(store, {'config': config,
                                                 'migrationPlanId': 'synthetic-plan'})
        check('activation-succeeds-with-exact-preview', result.get('ok') is True, result)
        check('activation-applies-grant',
              calls['apply'] == applies+1 and calls['rollback'] == rollbacks
              and store.compatible is True, calls)
        shares = opt._student_shares
        check('activation-zeroes-non-community',
              shares.get('community') == 1.0 and all(
                  value == 0.0 for name, value in shares.items() if name != 'community'), shares)
        check('activation-clears-preview-token', opt._encounter_preview_token is None)

        # (d) all-strategy is refused at the config check, before anything is written.
        before = (store.get(search.MODE_KEY), store.compatible)
        try:
            opt._activate_encounter(store, {'config': modes.default_config('all-strategy')})
            check('activation-refuses-all-strategy', False)
        except ValueError as exc:
            check('activation-refuses-unpreviewed-config', 'preview' in str(exc).lower(), exc)
        check('all-strategy-refusal-writes-nothing',
              (store.get(search.MODE_KEY), store.compatible) == before)
    finally:
        migration.preview, migration.apply, migration.rollback = saved
        store.close()


# -- real Optimizer loop with a mock evaluator ---------------------------------------------------
def loop_checks():
    path = new_dir('loop')/'lib.sqlite'
    search.EVALUATOR_FACTORY = mock_factory(ConstantPool())
    bridge = None
    try:
        bridge = Bridge(path)
        opened = wait_for(lambda: (bridge.status().get('encounterAware') or {})
                          if bridge.status().get('state') not in ('Opening library', None)
                          and (bridge.status().get('encounterAware') or {}).get('sessionId') is not None
                          else None, 180)
        if opened is None:
            check('loop-library-opened', False, bridge.status().get('state'))
            return
        check('loop-library-opened', True)
        bridge.command('encounter_rollback', {})
        config = search.default_config('community-first', purposes={
            'improvement': 8, 'comparison': 8, 'boundary': 0, 'support': 0, 'exploration': 0})
        bridge.command('encounter_preview', {'mode': 'community-first', 'purposes': config['purposes']})
        config = bridge.status()['encounterAware']['migrationPreview']['config']
        activated = bridge.command('encounter_activate', {'config': config})
        check('loop-activation-accepted', activated.get('ok') is True, activated.get('error'))

        # A legacy scheduling command must not reach anything while the mode is enabled.
        refused = bridge.command('students', {'shares': {'community': 1.0}})
        check('legacy-share-command-refused', refused.get('ok') is False, refused)

        legacy_before = bridge.status().get('legacyTotalRuns')
        started = bridge.command('start', {'workers': 2, 'duty': 1.0})
        check('loop-start-accepted', started.get('ok') is True, started.get('error'))
        ran = wait_for(lambda: (bridge.status().get('sessionRuns') or 0) >= 1, 120, 0.25)
        check('encounter-session-runs-published', bool(ran), bridge.status().get('sessionRuns'))

        optimizer = bridge._optimizer
        publishes = []
        original = optimizer._publish_sync

        def counting(store, state, error=None, *, detach=True):
            publishes.append((time.monotonic(), state))
            return original(store, state, error, detach=detach)

        optimizer._publish_sync = counting
        bridge.command('pause', {})
        wait_for(lambda: bridge.status().get('state') in ('Paused', 'Saving'), 30)
        wait_for(lambda: not optimizer._encounter.busy, 30)
        time.sleep(0.5)              # let the final drain publish settle
        baseline = len(publishes)
        time.sleep(3.0)              # a paused loop must not publish with nothing new
        check('paused-loop-publishes-are-event-driven', len(publishes)-baseline <= 3,
              '%d publishes in 3s' % (len(publishes)-baseline))

        status = bridge.status()
        check('encounter-session-basis-published',
              status.get('sessionRunsBasis') == 'encounter-session', status.get('sessionRunsBasis'))
        check('legacy-runs-still-zero',
              status.get('legacyTotalRuns') == legacy_before, status.get('legacyTotalRuns'))
        check('total-runs-includes-encounter-lifetime',
              status.get('totalRuns') == (status.get('legacyTotalRuns') or 0)
              + (status.get('encounterRuns') or 0), status)
        throughput = status.get('throughput') or {}
        check('encounter-throughput-published',
              throughput.get('basis') == 'encounter-ledger'
              and throughput.get('secondsPerRun') is not None, throughput)
        check('throughput-concurrency-matches-pool',
              throughput.get('workers') == 2 and throughput.get('duty') == 1.0, throughput)
    finally:
        search.EVALUATOR_FACTORY = None
        if bridge is not None:
            try:
                bridge.close()
            except Exception:  # noqa: BLE001 - teardown only
                pass
            bridge._optimizer.thread.join(timeout=20)
            check('loop-close-drains-and-exits', not bridge._optimizer.thread.is_alive())
            try:
                bridge._guard.close()
            except Exception:  # noqa: BLE001 - teardown only
                pass


def main():
    helper_checks()
    activation_checks()
    loop_checks()
    failed = [name for name, ok in RESULTS if not ok]
    print('\n%d/%d checks passed' % (len(RESULTS)-len(failed), len(RESULTS)))
    if failed:
        print('FAILED: ' + ', '.join(failed))
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
