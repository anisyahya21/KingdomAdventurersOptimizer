"""Focused REAL-PATH integration checks for concurrent Community encounter scheduling.

It builds throwaway libraries, real independent coordinators, the optimizer's campaign start path,
and the experiment ledger. No background optimizer loop or battle runs. It verifies concurrent
activation, separate active budget scopes, shared-pool fairness, per-encounter state isolation,
and restart restoration through the real preview/activate path.

Run:  <venv>/python -B -X utf8 tools/recovery/check_community_runtime_integration.py
No native battle, no library write outside the throwaway temp dir, no desktop call.
"""
import os
import json
import shutil
import sys
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import strategy_optimizer as so
import strategy_optimizer_adapter as adapter
import strategy_students as students
import strategy_community_campaign as cc
import strategy_experiment_store as ledger

RECOVERY = 384
RESULTS = []


class NoopEvaluator:
    """A deterministic shared-pool stand-in; these integration cases never run battles."""
    def __init__(self, *, workers=1, **_kwargs):
        self.workers = workers
        self.pending = {}
        self.closed = False
        self.duty = 1.0

    def configure_duty(self, duty):
        self.duty = duty

    def harvest(self, _db):
        return []

    def status(self):
        return dict(workers=self.workers, duty=self.duty, inflight=len(self.pending),
                    submitted=0, completed=0, native=0, fallback=0, errors=0, timeouts=0)

    def busy(self):
        return bool(self.pending)

    def close(self):
        self.closed = True


so.encounter_search.EVALUATOR_FACTORY = NoopEvaluator


def check(name, condition, detail=''):
    RESULTS.append((name, bool(condition)))
    print(('PASS ' if condition else 'FAIL ') + name + ((' :: ' + str(detail)) if detail else ''))


REPO = os.path.dirname(os.path.dirname(HERE))
HOME = os.path.join(REPO, 'tmp', 'encounter-redesign-20260928',
                    '_cc-runtime-%s' % uuid.uuid4().hex[:8])


def make_runtime(ids, tag):
    path = os.path.join(HOME, tag, 'lib.sqlite')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    provenance = adapter.provenance()
    store = so.Store(path, provenance)
    opt = so.Optimizer.__new__(so.Optimizer)
    opt.path = path
    opt.provenance = provenance
    opt.battleCompatibilityRevision = 'check'
    opt.runtimeRevision = 'check'
    opt.encounters = {str(value): {} for value in ids}
    opt._encounter = so.encounter_search.Coordinator(
        path=path, revision='check', workers=1, telemetry=False, maximum=2)
    opt._encounter.load(store)
    opt._encounter_snapshot = lambda _store: dict(opt._encounter.__dict__)
    opt._encounter_report = opt._encounter.report()
    opt._student_shares = students.shares()
    opt._track_shares = {track.name: 0.0 for track in students.tracks()}
    opt._encounter_previous_shares = None
    opt._encounter_previous_tracks = None
    opt._encounter_preview_token = None
    opt._encounter_ledger = None
    opt._encounter_session_base = None
    opt._planner_dirty = True
    opt._campaign_started = False
    opt._campaign_report = None
    opt._session_since = None
    opt._session_active = 0.0
    opt._session_live = True
    records = []

    def preview(value):
        return opt._preview_encounter(store, dict(value))

    def activate(value):
        result = opt._activate_encounter(store, dict(value))
        records.append({'encounter': int(value.get('encounter')),
                        'session': (result or {}).get('sessionId'),
                        'studyScope': (value.get('config') or {}).get('studyScope')})
        return result

    opt._campaign = cc.CommunityCampaign(encounter_ids=list(ids), preview=preview,
                                         activate=activate, commit=store.db.commit, focus=[])
    return opt, store, records


def idle_report():
    return {'progress': {'idle': True, 'current': None, 'confirmation': None}}


def drained_pass(opt, store):
    """Drive the loop's real safe-boundary hook with a drained (idle) report."""
    opt._encounter_report = idle_report()
    return opt._campaign_supervise(store, 'Running')


def session_total(store, session_id):
    rows = ledger.session_status(store.db, session_id)['budgets']
    return sum(int(row['total']) for row in rows)


def test_community_start_all():
    ids = list(range(20))
    opt, store, records = make_runtime(ids, 'all')
    directive = opt._campaign_start(store, {'workers': 24, 'duty': 1})
    check('community_start activates every enabled encounter before any budget is exhausted',
          directive['activeEncounters'] == len(ids) and directive['concurrent'] is True,
          {'activeEncounters': directive['activeEncounters'],
           'enabledEncounters': directive['enabledEncounterIds'],
           'concurrent': directive['concurrent']})
    check('campaign has no global current encounter cursor',
          directive.get('currentEncounterId') is None)
    sessions = list(store.db.execute('SELECT id, active FROM ea_session ORDER BY id'))
    check('one independent session is activated per encounter',
          len(sessions) == len(ids) and len({row[0] for row in sessions}) == len(ids))
    check('every encounter has its own active finite session',
          all(session_total(store, r['session']) == RECOVERY
              and ledger.session_status(store.db, r['session'])['active'] for r in records))
    configs = [json.loads(row[0]) for row in store.db.execute('SELECT config FROM ea_session')]
    check('each encounter session carries an independent campaign scope',
          len({(cfg['studyScope']['campaignScope'], cfg['studyScope']['encounter'])
               for cfg in configs}) == len(ids))
    check('a switched-away encounter keeps its player snapshot',
          isinstance(store.get('encounterPlayerSnapshot:%d' % ids[0]), dict))
    coords = opt._campaign_coordinators
    coords[ids[0]].state['confirmation'] = {'frozen': True, 'ready': False}
    coords[ids[1]].state['queue'] = [{'purpose': 'improvement'}]
    check('confirmation on one encounter leaves another encounter runnable',
          coords[ids[1]]._has_work() and not coords[ids[0]].state['queue'])
    coords[ids[0]].state['idle'] = True
    coords[ids[1]].state['idle'] = False
    check('one idle encounter does not finish or pause the campaign',
          opt._campaign_fleet_status()['status'] == 'active'
          and opt._campaign_fleet_status()['activeEncounters'] >= 1)
    return opt, store, records


def test_campaign_forwards_reviewed_migration_plan():
    """The desktop campaign binds activation to the exact plan returned by preview."""
    import strategy_encounter_migration as migration

    opt, store, _records = make_runtime([17], 'migration-forward')
    saved_preview, saved_apply = migration.preview, migration.apply
    plan_id = 'campaign-migration-forward-check'
    applied = []

    def fake_preview(_old, _current):
        return dict(eligible=True, planId=plan_id, proof={'check': plan_id})

    def fake_apply(store_, _current, requested_plan_id):
        if requested_plan_id != plan_id:
            raise ValueError('activation did not preserve the previewed migration plan')
        applied.append(requested_plan_id)
        store_.compatible = True
        return dict(proof={'check': plan_id})

    migration.preview, migration.apply = fake_preview, fake_apply
    try:
        store.compatible = False
        directive = opt._campaign_start(store, {'workers': 4, 'duty': 1})
        check('campaign activation forwards the exact reviewed migration plan',
              applied == [plan_id] and directive.get('activeEncounters') == 1,
              {'applied': applied, 'activeEncounters': directive.get('activeEncounters')})
    finally:
        migration.preview, migration.apply = saved_preview, saved_apply
        store.close()


def test_focus_two_and_clear():
    ids = [1, 2, 3, 4, 5]
    opt, store, records = make_runtime(ids, 'focus')
    opt._community_set_focus(store, [4, 2])
    directive = opt._campaign_start(store, {'workers': 24, 'duty': 1})
    check('a focus subset enables multiple selected encounters concurrently',
          directive['enabledEncounterIds'] == [4, 2]
          and directive['activeEncounters'] == 2, directive)
    active_encounters = {json.loads(row[0])['studyScope']['encounter'] for row in
                        store.db.execute('SELECT config FROM ea_session WHERE active=1')}
    check('unselected encounters receive no activation', active_encounters == {2, 4})
    check('each selected encounter has a separate runtime cursor',
          len({opt._campaign_coordinators[i].runtime_key for i in (2, 4)}) == 2)


def test_shared_worker_fairness_quota():
    from types import SimpleNamespace
    coords = [so.encounter_search.Coordinator(path='unused', workers=24, telemetry=False)
              for _ in range(3)]
    shared = SimpleNamespace(pending={}, workers=24, closed=False)
    for coord in coords:
        coord.evaluator = shared
        coord._submission_quota = 8
    capacities = [coord._submission_capacity() for coord in coords]
    check('three independent eight-job plans can fill 24 shared worker slots',
          capacities == [8, 8, 8] and sum(capacities) == 24, capacities)
    shared.pending.update({('experiment', i): {} for i in range(8)})
    slots_left = shared.workers-len(shared.pending)
    fair_quotas = []
    for encounters_left in (3, 2, 1):
        quota = min(8, (slots_left+encounters_left-1)//encounters_left)
        fair_quotas.append(quota)
        slots_left -= quota
    check('shared occupancy is divided fairly across remaining encounters',
          fair_quotas == [6, 5, 5] and sum(fair_quotas) == 16, fair_quotas)


def test_safe_switch_and_persisted_resume():
    ids = [2, 5, 8]
    opt, store, records = make_runtime(ids, 'resume')
    first = opt._campaign_start(store, {'workers': 4, 'duty': 1})
    before = {int(row[0]): int(row[1]) for row in store.db.execute(
        "SELECT json_extract(config,'$.studyScope.encounter'), id FROM ea_session")}
    for encounter_id, coord in opt._campaign_coordinators.items():
        coord.state['roundIndex'] = encounter_id
        coord.state['features'] = {'restartMarker': encounter_id}
        coord._persist(store)
    # Create a new optimizer process view; every independent runtime key must load without activation.
    opt2 = so.Optimizer.__new__(so.Optimizer)
    opt2.path = opt.path
    opt2.provenance = opt.provenance
    opt2.battleCompatibilityRevision = 'check'
    opt2.runtimeRevision = 'check'
    opt2.encounters = opt.encounters
    opt2._encounter = opt._encounter
    opt2._encounter_preview_token = None
    opt2._encounter_previous_shares = None
    opt2._encounter_previous_tracks = None
    opt2._campaign_coordinators = {}
    opt2._campaign_fair_cursor = 0
    opt2._community_evaluator = None
    opt2._workers = 4
    opt2._duty = 1
    opt2._student_shares = students.shares()
    opt2._track_shares = {track.name: 0.0 for track in students.tracks()}
    opt2._campaign_start(store, {'workers': 4, 'duty': 1})
    after = {int(row[0]): int(row[1]) for row in store.db.execute(
        "SELECT json_extract(config,'$.studyScope.encounter'), id FROM ea_session")}
    check('restart restores every encounter session without duplicate activation',
          len(after) == len(ids) and before == after, after)
    check('restart restores independent runtime keys and independent state',
          all(opt2._campaign_coordinators[i].runtime_key.endswith(':%d:0' % i)
              and opt2._campaign_coordinators[i].state['features'].get('restartMarker') == i
              for i in ids))



def test_automatic_round_scope():
    ids = [3, 5]
    opt, store, records = make_runtime(ids, 'newscope')
    opt._campaign_start(store, {'workers': 4, 'duty': 1})
    scopes = [json.loads(row[0])['studyScope']['campaignScope'] for row in
             store.db.execute('SELECT config FROM ea_session')]
    check('each encounter has a distinct generation scope', len(set(scopes)) == len(ids))
    check('concurrent campaign activation preserves each finite budget',
          all(sum(int(row['total']) for row in ledger.session_status(
              store.db, session_id)['budgets']) == RECOVERY
              for (session_id,) in store.db.execute('SELECT id FROM ea_session')))
    check('round metadata is independent per encounter',
          all(opt._campaign_coordinators[i].encounter == i for i in ids))


def test_continuous_campaign_rolls_one_drained_encounter():
    """A bounded session rolls forward without pausing peers or pressing Run again."""
    ids = [3, 5]
    opt, store, records = make_runtime(ids, 'continuous')
    opt._campaign_start(store, {'workers': 4, 'duty': 1})
    before_sessions = {cid: coord.session_id
                       for cid, coord in opt._campaign_coordinators.items()}

    class Evaluator:
        pending = {}
        closed = False
        workers = 4

        def harvest(self, _db):
            return []

        def status(self):
            return {'submitted': 0, 'completed': 0, 'inflight': 0, 'workers': 4}

    evaluator = Evaluator()
    opt._community_evaluator = evaluator
    for cid, coord in opt._campaign_coordinators.items():
        coord.evaluator = evaluator
        def run_pass(_store, *, running, harvested_entries, recovery_snapshot=None,
                     return_report=True, _cid=cid):
            return {'progress': {'idle': _cid == ids[0], 'experiments': 1,
                                 'cohortPending': 0, 'confirmation': None,
                                 'current': None},
                    'budgets': {'session': {'remaining': 0}}}
        coord.run_pass = run_pass

    status_builder = opt._campaign_fleet_status
    def measured_status_builder(*args, **kwargs):
        time.sleep(0.005)
        return status_builder(*args, **kwargs)
    opt._campaign_fleet_status = measured_status_builder
    report = opt._campaign_fleet_pass(store, running=True)
    opt._campaign_fleet_status = status_builder
    fleet_stages = (report.get('timings') or {}).get('fleetStagesSeconds') or {}
    report_seconds = float(fleet_stages.get('reportConstructionSeconds') or 0.0)
    status_seconds = float(fleet_stages.get('campaignStatusSeconds') or 0.0)
    pass_seconds = float((report.get('timings') or {}).get('fleetPassSeconds') or 0.0)
    check('fleet diagnostics time report snapshots and campaign status separately',
          'reportConstructionSeconds' in fleet_stages
          and 'campaignStatusSeconds' in fleet_stages
          and report_seconds >= 0 and status_seconds >= 0.004
          and pass_seconds >= report_seconds + status_seconds,
          {'reportConstructionSeconds': report_seconds,
           'campaignStatusSeconds': status_seconds, 'fleetPassSeconds': pass_seconds})
    opt._scheduler = {}
    original_status_builder = opt._campaign_fleet_status
    repetitions = 100
    before_started = time.perf_counter()
    for _ in range(repetitions):
        opt._campaign_supervise(store, 'Running')
    before_average_ms = (time.perf_counter()-before_started)*1000/repetitions
    fresh_status = report['campaign']
    opt._campaign_report = fresh_status

    def unexpected_status_rebuild(*_args, **_kwargs):
        raise AssertionError('fresh campaign status was rebuilt during supervision')

    opt._campaign_fleet_status = unexpected_status_rebuild
    after_started = time.perf_counter()
    supervised = None
    for _ in range(repetitions):
        supervised = opt._campaign_supervise(store, 'Running', reuse_fresh_report=True)
    after_average_ms = (time.perf_counter()-after_started)*1000/repetitions
    opt._campaign_fleet_status = original_status_builder
    check('supervision reuses the status produced by this fleet pass',
          supervised == ('Running', None) and opt._campaign_report is fresh_status
          and opt._scheduler.get('communitySuperviseStatusReused') is True
          and opt._scheduler.get('communitySuperviseStatusSeconds') == 0.0,
          {'scheduler': opt._scheduler, 'baselineRefreshAverageMs': round(before_average_ms, 4),
           'reuseAverageMs': round(after_average_ms, 4), 'repetitions': repetitions})
    fleet = store.get('communityCampaignFleet')
    first = fleet['encounters'][str(ids[0])]
    second = fleet['encounters'][str(ids[1])]
    active_sessions = [row[0] for row in store.db.execute(
        'SELECT id FROM ea_session WHERE active=1 ORDER BY id')]
    check('drained encounter starts its next finite generation automatically',
          first.get('generation') == 1 and first.get('sessionId') != before_sessions[ids[0]]
          and len(active_sessions) == len(ids) + 1, first)
    check('one generation rollover leaves its peer generation and runtime intact',
          second.get('generation') == 0 and second.get('sessionId') == before_sessions[ids[1]]
          and opt._campaign_coordinators[ids[1]].runtime_key.endswith(':%d:0' % ids[1]))
    check('campaign remains active through an individual budget rollover',
          (report.get('campaign') or {}).get('status') == 'active'
          and (report.get('campaign') or {}).get('concurrent') is True,
          report.get('campaign'))


def test_campaign_pause_and_stop_are_durable():
    """Pause preserves the live fleet; Stop persists termination and releases its pool."""
    path = os.path.join(HOME, 'finish', 'lib.sqlite')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    store = so.Store(path, adapter.provenance())
    opt = so.Optimizer.__new__(so.Optimizer)
    opt._campaign_started = True
    opt._campaign_coordinators = {}
    opt._campaign_fair_cursor = 0
    opt._community_fleet_state = dict(version=1, campaignId='check-stop',
        encounterIds=[3], enabledEncounterIds=[3], status='active', encounters={})
    class Evaluator:
        closed = False
        def close(self):
            self.closed = True
    evaluator = Evaluator()
    opt._community_evaluator = evaluator
    store.set('communityCampaignFleet', opt._community_fleet_state)
    store.db.commit()
    opt._campaign_finish(store, 'paused')
    pause_ok = (store.get('communityCampaignFleet').get('status') == 'paused'
                and opt._campaign_started and opt._community_evaluator is evaluator
                and not evaluator.closed)
    check('Pause is durable while preserving campaign state and the reusable pool', pause_ok)
    opt._campaign_finish(store, 'stopped')
    stop_ok = (store.get('communityCampaignFleet').get('status') == 'stopped'
               and not opt._campaign_started and opt._community_evaluator is None
               and evaluator.closed and opt._campaign_report.get('status') == 'stopped')
    check('Stop is durable and releases the idle shared worker pool', stop_ok)
    store.close()


def test_focus_command_validation():
    catalogue = {1, 2, 3}
    check('an empty selection means the whole catalogue',
          so.normalize_focus_encounters([], catalogue) == [])
    check('a selection keeps order and drops duplicates',
          so.normalize_focus_encounters([3, 1, 3], catalogue) == [3, 1])
    try:
        so.normalize_focus_encounters([9], catalogue)
        check('an id outside the catalogue is refused', False)
    except ValueError:
        check('an id outside the catalogue is refused', True)
    try:
        so.normalize_focus_encounters(3, catalogue)
        check('a bare scalar is refused', False)
    except ValueError:
        check('a bare scalar is refused', True)


def main():
    try:
        test_community_start_all()
        test_campaign_forwards_reviewed_migration_plan()
        test_focus_two_and_clear()
        test_shared_worker_fairness_quota()
        test_safe_switch_and_persisted_resume()
        test_automatic_round_scope()
        test_continuous_campaign_rolls_one_drained_encounter()
        test_campaign_pause_and_stop_are_durable()
        test_focus_command_validation()
    finally:
        shutil.rmtree(HOME, ignore_errors=True)
    passed = sum(1 for _name, ok in RESULTS if ok)
    total = len(RESULTS)
    print('\ncheck_community_runtime_integration: %d/%d' % (passed, total))
    return 0 if passed == total else 1


if __name__ == '__main__':
    raise SystemExit(main())



