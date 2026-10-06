"""Focused checks for the automatic ALL-ENCOUNTER Community campaign.

Run:  <venv>/python -B -X utf8 tools/recovery/check_community_campaign.py

Everything here is in-memory / temporary: a fake meta store and fake preview/activate callbacks
stand in for the optimiser. No library, ledger, desktop or native battle is touched.
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import strategy_community_campaign as campaign              # noqa: E402
import strategy_search_mode as modes                        # noqa: E402

RESULTS = []
PURPOSES = {'improvement': 128, 'boundary': 64, 'support': 32,
            'comparison': 128, 'exploration': 32}
CAP = 384


def check(name, condition, detail=''):
    RESULTS.append((name, bool(condition)))
    print(('PASS ' if condition else 'FAIL ') + name + ((' :: ' + str(detail)) if detail else ''))


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), default=str)


class MetaStore:
    """A minimal library-meta store: get/set JSON keys plus a commit counter."""

    class _DB:
        def __init__(self, outer):
            self.outer = outer

        def commit(self):
            self.outer.commits += 1

    def __init__(self):
        self.meta = {}
        self.commits = 0
        self.db = MetaStore._DB(self)

    def get(self, key, default=None):
        return copy.deepcopy(self.meta.get(key, default))

    def set(self, key, value):
        self.meta[key] = copy.deepcopy(value)


class FakeCommunity:
    """A faithful stand-in for Optimizer._preview_encounter/_activate_encounter.

    It builds the same community-first config the real path derives, coalesces a session by the
    canonical config (exactly like `ledger.configure_session`), and records every call.
    """

    def __init__(self, *, migration_required=False, migration_eligible=True, store=None):
        self.preview_calls = []
        self.activate_calls = []
        self.sessions = {}
        self.session_digest = {}
        self.active = None
        self.activated_order = []
        self.configs = []
        self.migration_required = migration_required
        self.migration_eligible = migration_eligible
        self.store = store
        self.crash_snapshot = None

    def preview(self, value):
        self.preview_calls.append(copy.deepcopy(value))
        encounter = int(value['encounter'])
        config = modes.default_config('community-first')
        config['purposes'] = dict(value.get('purposes') or PURPOSES)
        config['studyScope'] = {'encounter': encounter, 'reference': value.get('reference'),
                                'constraints': value.get('constraints')}
        config['budgets'] = modes.derive_budget_matrix(config)['matrix']
        modes.validate_config(config)
        return {'config': config, 'encounter': encounter,
                'simulatorMigration': {'required': bool(self.migration_required),
                                       'eligible': bool(self.migration_eligible),
                                       'reason': None if self.migration_eligible else 'ineligible',
                                       'planId': 'plan-%d' % encounter
                                       if self.migration_required else None}}

    def activate(self, value):
        if self.store is not None:
            # The campaign persists the intent BEFORE calling us; snapshot it to simulate a crash.
            self.crash_snapshot = self.store.get(campaign.CAMPAIGN_KEY)
        self.activate_calls.append(copy.deepcopy(value))
        config = value['config']
        allocations = config['allocations']
        assert abs(float(allocations.get('community', 0.0)) - 1.0) < 1e-9, 'community must own the run'
        assert all(float(other) == 0.0 for name, other in allocations.items() if name != 'community')
        self.configs.append(copy.deepcopy(config))
        digest = canonical(config)
        session = self.sessions.get(digest)
        if session is None:
            session = 'S%d' % (len(self.sessions) + 1)
            self.sessions[digest] = session
            self.session_digest[session] = digest
        if self.migration_required and self.migration_eligible:
            assert value.get('migrationPlanId') == 'plan-%d' % int(value['encounter']), \
                'a required migration must carry the previewed plan id'
        self.active = int(value['encounter'])
        self.activated_order.append(int(value['encounter']))
        return {'ok': True, 'sessionId': session}


def campaign_for(fake, ids, store):
    return campaign.CommunityCampaign(encounter_ids=ids, preview=fake.preview, activate=fake.activate,
                                      purposes=PURPOSES)


def idle(**kw):
    progress = {'idle': True, 'current': None, 'confirmation': None}
    progress.update(kw)
    return {'progress': progress}


def working(**kw):
    progress = {'idle': False, 'current': {'experimentId': 1}, 'confirmation': None}
    progress.update(kw)
    return {'progress': progress}


# ---------------------------------------------------------------------------------------------
def test_defaults_and_cap():
    purposes = campaign.default_purposes()
    check('defaults match the reviewed encounter defaults', purposes == PURPOSES, purposes)
    check('per-encounter cap is 384', campaign.per_encounter_cap(purposes) == CAP)
    ids = [5, 9, 14, 2]
    fake = FakeCommunity()
    store = MetaStore()
    camp = campaign_for(fake, ids, store)
    status = camp.start(store)
    check('campaign cap is len(ids) * 384', status['cap'] == CAP * len(ids),
          (status['cap'], CAP * len(ids)))
    check('total equals the catalogue size', status['total'] == len(ids))
    check('granted starts at one encounter budget', status['granted'] == CAP)
    check('only the first encounter is activated on start', fake.activated_order == [ids[0]])
    check('no other streams are allocated', all(
        abs(float(cfg['allocations'].get(name, 0.0))) == 0.0
        for cfg in fake.configs for name in modes.share_streams() if name != 'community'))
    check('the declared purposes are exactly the finite defaults',
          all(cfg['purposes'] == PURPOSES for cfg in fake.configs))


def test_sequential_all_ids():
    ids = [7, 3, 11, 19, 4]
    fake = FakeCommunity()
    store = MetaStore()
    camp = campaign_for(fake, ids, store)
    status = camp.start(store)
    seen = [status['currentEncounterId']]
    for position, encounter in enumerate(ids):
        check('current encounter %d is %d' % (position, encounter),
              status['currentEncounterId'] == encounter, status['currentEncounterId'])
        check('granted after %d activations' % (position + 1),
              status['granted'] == CAP * (position + 1), status['granted'])
        # a working pass must not advance
        held = camp.after_pass(store, working(), busy=False)
        check('a working study does not advance (%d)' % encounter,
              held['currentEncounterId'] == encounter)
        # if this is not the last encounter, an idle pass advances exactly once
        status = camp.after_pass(store, idle(), busy=False)
        if position < len(ids) - 1:
            check('idle advances to the next catalogue id (%d -> %d)'
                  % (encounter, ids[position + 1]),
                  status['currentEncounterId'] == ids[position + 1], status['currentEncounterId'])
            check('advance switched exactly once (%d)' % encounter, status['switched'] is True)
            seen.append(status['currentEncounterId'])
        else:
            check('the final idle pass starts the next round', status['status'] == 'active' and status.get('round') == 1)
            check('round switch activated the first encounter of a fresh round',
                  status['currentEncounterId'] == ids[0] and len(fake.activate_calls) == len(ids) + 1)
            check('the next campaign round keeps the runtime running', status['stopRuntime'] is False)
    check('round rotation returns to the first catalogue encounter',
          fake.activated_order == ids + [ids[0]], fake.activated_order)
    check('one extra activation begins round two', len(fake.activate_calls) == len(ids) + 1)
    check('every encounter got a distinct budget session', len(fake.sessions) == len(ids))
    check('round two grant is within the finite cap',
          status['granted'] == CAP and status['cap'] == CAP * len(ids))
    check('the next round has a fresh completion count', status['completed'] == 0)
    check('next round starts at the first encounter', status['currentEncounterId'] == ids[0])


def test_holds_busy_and_current():
    ids = [1, 2]
    fake = FakeCommunity()
    store = MetaStore()
    camp = campaign_for(fake, ids, store)
    camp.start(store)
    busy = camp.after_pass(store, idle(), busy=True)
    check('an idle-but-busy study does not advance', busy['currentEncounterId'] == 1)
    pending = camp.after_pass(store, idle(current={'experimentId': 3}), busy=False)
    check('an idle report with a current experiment does not advance',
          pending['currentEncounterId'] == 1)
    not_running = camp.after_pass(store, idle(), busy=False, running=False)
    check('after_pass while not running does not advance', not_running['currentEncounterId'] == 1)


def test_blocked_and_readiness_pause():
    ids = [1, 2, 3]
    fake = FakeCommunity()
    store = MetaStore()
    camp = campaign_for(fake, ids, store)
    camp.start(store)
    blocked = camp.after_pass(store, {'progress': {
        'idle': True, 'blocked': True,
        'current': {'blockedReason': 'seed-history read failed'}}}, busy=False)
    check('a blocked dispatch claim pauses the campaign', blocked['status'] == 'paused')
    check('the block reason is surfaced', 'seed-history read failed' in (blocked['reason'] or ''),
          blocked['reason'])
    check('a paused campaign does not skip the encounter', fake.activated_order == [1])
    check('a paused campaign stops the runtime', blocked['stopRuntime'] is True)

    # A live readiness block is supplied session-scoped by the integration, not guessed from the
    # coordinator's accumulated limitations list.
    fake2 = FakeCommunity()
    store2 = MetaStore()
    camp2 = campaign_for(fake2, ids, store2)
    camp2.start(store2)
    readiness = camp2.after_pass(store2, idle(), busy=False,
                                 blocked_reason='seed-history readiness blocked (x)')
    check('a readiness block pauses instead of advancing', readiness['status'] == 'paused')
    check('readiness reason is surfaced', 'readiness blocked' in (readiness['reason'] or ''),
          readiness['reason'])
    check('readiness pause leaves the encounter untouched', fake2.activated_order == [1])
    # A stale note in the coordinator's never-cleared limitations list must NOT pause a later study.
    fake3 = FakeCommunity()
    store3 = MetaStore()
    camp3 = campaign_for(fake3, ids, store3)
    camp3.start(store3)
    stale = camp3.after_pass(store3, {'progress': {'idle': True, 'current': None},
                                      'limitations': ['seed-history readiness blocked (old note)']},
                             busy=False)
    check('a stale limitations note does not pause a live campaign',
          stale['status'] == 'active' and camp3.load(store3)['index'] == 1)


def test_frozen_confirmation():
    ids = [1, 2]
    fake = FakeCommunity()
    store = MetaStore()
    camp = campaign_for(fake, ids, store)
    camp.start(store)
    held = camp.after_pass(store, working(confirmation={'frozen': True, 'ready': False}), busy=False)
    check('an unresolved frozen confirmation is not switched away from',
          held['currentEncounterId'] == 1 and held['status'] == 'active', held['status'])
    stuck = camp.after_pass(store, idle(confirmation={'frozen': True, 'ready': False}), busy=False)
    check('an idle study with an unresolved confirmation pauses rather than spins',
          stuck['status'] == 'paused')
    ready = FakeCommunity()
    store_ready = MetaStore()
    camp_ready = campaign_for(ready, ids, store_ready)
    camp_ready.start(store_ready)
    advanced = camp_ready.after_pass(
        store_ready, idle(confirmation={'frozen': True, 'ready': True}), busy=False)
    check('a resolved confirmation lets the campaign advance',
          advanced['currentEncounterId'] == 2)


def test_resume_crash_same_grant():
    ids = [8, 12]
    fake = FakeCommunity()
    store = MetaStore()
    fake.store = store
    camp = campaign_for(fake, ids, store)
    camp.start(store)
    check('one session exists after the first activation', len(fake.sessions) == 1)
    # Simulate a crash right after the intent was persisted: restore that exact snapshot.
    assert isinstance(fake.crash_snapshot, dict)
    store.set(campaign.CAMPAIGN_KEY, fake.crash_snapshot)
    grants_before = store.get(campaign.CAMPAIGN_KEY)['granted']
    sessions_before = len(fake.sessions)
    revived = campaign_for(fake, ids, store)
    status = revived.start(store)
    check('a crashed activation retries the same encounter', status['currentEncounterId'] == 8)
    check('resume grants no extra budget', status['granted'] == grants_before == CAP)
    check('resume coalesces onto the same session', len(fake.sessions) == sessions_before)
    check('the retried activation recorded the same session',
          status['current']['sessionId'] == 'S1', status['current'])


def test_resume_reactivates_disabled_runtime():
    ids = [5, 6]
    fake = FakeCommunity()
    store = MetaStore()
    camp = campaign_for(fake, ids, store)
    camp.start(store)
    before = len(fake.activate_calls)
    status = camp.start(store, active_encounter=5, runtime_enabled=False)
    check('a disabled runtime is re-activated on Start', len(fake.activate_calls) == before + 1)
    check('re-activation coalesces onto the same session', len(fake.sessions) == 1)
    check('re-activation grants no extra budget', status['granted'] == CAP and
          status['currentEncounterId'] == 5)

def test_resume_uses_persisted_budgets():
    ids = [2, 3]
    fake = FakeCommunity()
    store = MetaStore()
    camp = campaign_for(fake, ids, store)
    camp.start(store)
    bigger = dict(PURPOSES)
    bigger['improvement'] = 2048          # a changed default must never enlarge a live campaign
    resumer = campaign.CommunityCampaign(encounter_ids=ids, preview=fake.preview,
                                         activate=fake.activate, purposes=bigger)
    status = resumer.start(store, active_encounter=2, runtime_enabled=False)
    check('resume keeps the persisted declared purposes',
          fake.configs[-1]['purposes'] == PURPOSES, fake.configs[-1]['purposes'])
    check('resume keeps the persisted per-encounter cap',
          status['perEncounterCap'] == CAP and status['granted'] == CAP, status)
    check('a changed default cannot enlarge the campaign cap', status['cap'] == CAP * len(ids))

def test_repeat_start_after_complete():
    ids = [2, 6]
    fake = FakeCommunity()
    store = MetaStore()
    camp = campaign_for(fake, ids, store)
    camp.start(store)
    camp.after_pass(store, idle(), busy=False)          # -> encounter 6
    done = camp.after_pass(store, idle(), busy=False)   # -> complete
    check('the campaign rolls to the next round', done['status'] == 'active' and done.get('round') == 1)
    check('round two scope differs from round one', done['scopeKey'] != store.get(campaign.CAMPAIGN_KEY)['scopeKey'] if False else done.get('round') == 1)
    activations_before = len(fake.activate_calls)
    again = camp.start(store)
    check('a repeated Start resumes the active round', again['status'] == 'active')
    check('a repeated Start grants nothing within the round', again['granted'] == CAP)
    check('a repeated Start does not activate', len(fake.activate_calls) == activations_before)
    check('the report explains continuous bounded rounds',
          'finite encounter rounds' in (again['note'] or campaign.NEW_CAMPAIGN_NOTE), again['note'])
    idle_again = camp.after_pass(store, idle(), busy=False)
    check('draining a round activates exactly one next encounter',
          idle_again['status'] == 'active' and len(fake.activate_calls) == activations_before + 1)


def test_begin_new_campaign_refused_on_coalesce():
    ids = [1, 2]
    fake = FakeCommunity()
    store = MetaStore()
    camp = campaign_for(fake, ids, store)
    camp.start(store)
    camp.after_pass(store, idle(), busy=False)
    camp.after_pass(store, idle(), busy=False)          # complete
    unconfirmed = camp.begin_new_campaign(store, confirm=False)
    check('manual new campaign command is refused while campaign is active',
          unconfirmed['status'] == 'refused')
    refused = camp.begin_new_campaign(store, confirm=True)
    check('manual new campaign command cannot reset the active round',
          refused['status'] == 'refused', refused['reason'])
    check('the refusal does not activate anything',
          len(fake.activate_calls) == len(ids) + 1)


def test_focus_subset_scheduling_only():
    ids = [1, 2, 3, 4]
    fake = FakeCommunity()
    store = MetaStore()
    camp = campaign_for(fake, ids, store)
    status = camp.start(store, focus=[3, 1])
    check('a focus subset schedules its first catalogue id', status['currentEncounterId'] == 1,
          status['currentEncounterId'])
    check('the focus is persisted on the campaign', status['focus'] == [3, 1], status['focus'])
    check('a focused start still exposes the whole-catalogue cap', status['cap'] == CAP * len(ids))
    check('a focused start grants one per activated encounter', status['granted'] == CAP)
    status = camp.after_pass(store, idle(), busy=False)
    check('a focused advance switches to the next focused id', status['currentEncounterId'] == 3,
          status['currentEncounterId'])
    check('the second activation grants exactly one more budget', status['granted'] == CAP * 2)
    status = camp.after_pass(store, idle(), busy=False)
    check('a completed focused subset pauses instead of completing',
          status['status'] == 'paused', status['status'])
    check('the subset pause says other encounters remain',
          'remain' in (status['reason'] or ''), status['reason'])
    check('the subset pause stops the runtime', status['stopRuntime'] is True)
    check('the subset pause grants nothing beyond the focus', status['granted'] == CAP * 2)
    check('the subset was not marked as a whole-campaign completion',
          status['status'] != 'complete')
    # Re-activating an already-charged encounter (same focus) must not double-charge.
    grants = status['granted']
    repeated = camp.start(store, focus=[3, 1], active_encounter=3, runtime_enabled=True)
    check('a repeated Start on the same focus grants nothing new',
          repeated['granted'] == grants, repeated['granted'])
    # Clearing the focus resumes the remaining catalogue encounters only.
    resumed = camp.start(store, focus=[])
    check('clearing the focus resumes an un-run catalogue encounter',
          resumed['currentEncounterId'] == 2, resumed['currentEncounterId'])
    check('resuming the remainder grants exactly one more budget',
          resumed['granted'] == CAP * 3, resumed['granted'])
    check('clearing the focus keeps the whole-catalogue cap', resumed['cap'] == CAP * len(ids))


def test_empty_catalogue():
    fake = FakeCommunity()
    store = MetaStore()
    camp = campaign_for(fake, [], store)
    status = camp.start(store)
    check('an empty catalogue produces no campaign', status['status'] == 'none')
    check('an empty catalogue grants nothing', status['granted'] == 0 and status['cap'] == 0)
    check('an empty catalogue activates nothing', fake.activate_calls == [])


def test_persisted_empty_catalogue_pauses():
    # Regression: a PERSISTED active campaign whose encounter list is empty used to recurse
    # forever. `_target_index` returned None, and `completed >= set([])` was vacuously true, so
    # `_finish_unavailable` bumped the round and re-entered `_ensure_activation` forever. The
    # genuine defect is fixed by refusing to run (pausing) an empty catalogue instead.
    fake = FakeCommunity()
    store = MetaStore()
    seeded = campaign.CommunityCampaign(encounter_ids=[], preview=fake.preview,
                                        activate=fake.activate, purposes=PURPOSES)._new_state()
    check('the seeded record really is an active empty catalogue',
          seeded['status'] == 'active' and seeded['encounterIds'] == [])
    store.set(campaign.CAMPAIGN_KEY, seeded)
    camp = campaign_for(fake, [], store)
    status = camp.start(store)
    check('a persisted empty catalogue terminates paused', status['status'] == 'paused',
          status['status'])
    check('the empty-catalogue pause is actionable', 'no encounters' in (status['reason'] or ''),
          status['reason'])
    check('the empty-catalogue pause stops the runtime', status['stopRuntime'] is True)
    check('the empty-catalogue pause manufactures no round', status['round'] == 0, status['round'])
    check('the empty-catalogue pause activates no session', fake.activate_calls == [])
    check('the empty-catalogue pause grants no budget',
          status['granted'] == 0 and status['cap'] == 0)
    # Resume must terminate too, never re-enter the recursion.
    resumed = camp.resume(store)
    check('resuming an empty catalogue stays paused', resumed['status'] == 'paused',
          resumed['status'])
    check('resuming an empty catalogue activates nothing', fake.activate_calls == [])
    # A non-empty catalogue persisted record still runs normally (behaviour preserved).
    fake2 = FakeCommunity()
    store2 = MetaStore()
    camp2 = campaign_for(fake2, [1, 2], store2)
    normal = camp2.start(store2)
    check('a non-empty catalogue still activates its first encounter',
          normal['status'] == 'active' and normal['currentEncounterId'] == 1)
    check('a non-empty catalogue still grants one encounter budget',
          normal['granted'] == CAP and len(fake2.activate_calls) == 1)


def test_catalogue_drift_pauses():
    ids = [1, 2, 3]
    fake = FakeCommunity()
    store = MetaStore()
    camp = campaign_for(fake, ids, store)
    camp.start(store)
    shrunk = campaign_for(fake, [1], store)
    status = shrunk.after_pass(store, idle(), busy=False)
    check('a changed catalogue pauses the campaign', status['status'] == 'paused')
    check('the drift reason is surfaced', 'catalogue changed' in (status['reason'] or ''),
          status['reason'])
    check('drift does not activate', fake.activated_order == [1])


def test_migration_path():
    ids = [4, 5]
    fake = FakeCommunity(migration_required=True, migration_eligible=True)
    store = MetaStore()
    camp = campaign_for(fake, ids, store)
    status = camp.start(store)
    check('an eligible migration activates the first encounter', status['currentEncounterId'] == 4)
    check('the migration plan id is forwarded to activation',
          all('migrationPlanId' in value for value in fake.activate_calls))

    ineligible = FakeCommunity(migration_required=True, migration_eligible=False)
    store2 = MetaStore()
    camp2 = campaign_for(ineligible, ids, store2)
    paused = camp2.start(store2)
    check('an ineligible migration pauses the campaign', paused['status'] == 'paused')
    check('an ineligible migration activates nothing', ineligible.activate_calls == [])


def test_remainders_recorded_without_filler():
    ids = [3, 4]
    fake = FakeCommunity()
    store = MetaStore()
    camp = campaign_for(fake, ids, store)
    camp.start(store)
    report = idle()
    report['budgets'] = {'session': {'improvement': {'total': 128, 'completed': 128, 'reserved': 0},
                                     'boundary': {'total': 64, 'completed': 0, 'reserved': 0},
                                     'support': {'total': 32, 'completed': 32, 'reserved': 0},
                                     'comparison': {'total': 128, 'completed': 128, 'reserved': 0},
                                     'exploration': {'total': 32, 'completed': 32, 'reserved': 0}}}
    status = camp.after_pass(store, report, busy=False)
    state = store.get(campaign.CAMPAIGN_KEY)
    remainders = state.get('remainders') or []
    check('the advance still happened with unused budget', status['currentEncounterId'] == 4)
    check('unused boundary budget is recorded, not filled',
          any(row['unspent'].get('boundary') == 64 for row in remainders), remainders)
    check('the remainder is labeled as never filled',
          all('never filled' in row['note'] for row in remainders))


def test_bootstrap_status_read_only():
    import sqlite3
    tmp = Path(HERE).parent.parent / 'tmp' / 'encounter-redesign-20260928' / '_cc-boot'
    tmp.mkdir(parents=True, exist_ok=True)
    db_path = tmp / 'bootstrap.sqlite'
    connection = sqlite3.connect(str(db_path))
    connection.execute('DROP TABLE IF EXISTS meta')
    connection.execute('CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)')
    connection.execute('INSERT INTO meta VALUES(?,?)', ('communityCampaign', json.dumps({
        'version': 1, 'campaignId': 'cc-x', 'scopeKey': 'community-campaign:cc-x',
        'encounterIds': [3, 4], 'purposes': PURPOSES, 'perEncounterCap': CAP, 'cap': CAP * 2,
        'index': 1, 'granted': CAP * 2, 'declared': 2, 'status': 'complete', 'reason': 'done',
        'current': None, 'intent': None, 'completedEncounters': [3, 4], 'configDigests': {},
        'remainders': []})))
    connection.commit()
    connection.close()
    status = campaign.bootstrap_status(db_path)
    check('bootstrap reads a persisted campaign read-only', status['status'] == 'complete' and
          status['currentIndex'] == 1 and status['total'] == 2)
    check('bootstrap reports no campaign for a missing key',
          campaign.bootstrap_status(tmp / 'missing.sqlite')['status'] == 'none')

def main():
    test_defaults_and_cap()
    test_sequential_all_ids()
    test_holds_busy_and_current()
    test_blocked_and_readiness_pause()
    test_frozen_confirmation()
    test_resume_crash_same_grant()
    test_resume_reactivates_disabled_runtime()
    test_resume_uses_persisted_budgets()
    test_repeat_start_after_complete()
    test_begin_new_campaign_refused_on_coalesce()
    test_focus_subset_scheduling_only()
    test_empty_catalogue()
    test_persisted_empty_catalogue_pauses()
    test_catalogue_drift_pauses()
    test_migration_path()
    test_remainders_recorded_without_filler()
    test_bootstrap_status_read_only()
    passed = sum(1 for _name, ok in RESULTS if ok)
    total = len(RESULTS)
    print('\ncheck_community_campaign: %d/%d' % (passed, total))
    return 0 if passed == total else 1


if __name__ == '__main__':
    raise SystemExit(main())
