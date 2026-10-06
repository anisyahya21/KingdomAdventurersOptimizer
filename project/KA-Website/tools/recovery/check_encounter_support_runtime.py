"""Runtime integration checks for the support-evidence path (mock/zero-battle only).

Run:  <venv>/python -B -X utf8 tools/recovery/check_encounter_support_runtime.py

This proves the ACTUAL coordinator calling path wired in `strategy_encounter_search`: the chosen
parent's own persisted, compatible DEVELOPMENT telemetry is read through the shared read-only
`strategy_support_evidence.diagnose`, and the improvement proposal pool then carries an MP repair
only for that parent. Missing telemetry, a frozen holdout pair, a foreign owner and a fodder unit
must NOT trigger a repair, and the repair must not spend unrelated DEF/HP or touch the frozen policy,
fodder or formation. Every fixture is synthetic and deterministic; no battle, native engine, live
library, setting or reset is touched.
"""
from __future__ import annotations

import copy
import json
import sys
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import check_encounter_search as base                                  # noqa: E402
import strategy_encounter_search as search                            # noqa: E402
import strategy_experiment_store as ledger                            # noqa: E402
import strategy_mechanics as mechanics                                # noqa: E402
import strategy_mp_recovery as mp_recovery                            # noqa: E402
import strategy_students as students                                  # noqa: E402
from strategy_optimizer import Store                                  # noqa: E402
from strategy_optimizer_adapter import provenance, stats              # noqa: E402

RESULTS = []


def check(name, condition, detail=''):
    RESULTS.append((name, bool(condition)))
    print(('PASS ' if condition else 'FAIL ') + name + ((' :: ' + str(detail)) if detail else ''))


def build(path, encounter, reference, maximum=12):
    store = Store(path, provenance())
    config = search.default_config('community-first', purposes={
        'improvement': 32, 'comparison': 8, 'boundary': 0, 'support': 0, 'exploration': 0})
    store.set(search.MODE_KEY, dict(enabled=True, mode='community-first', config=config,
                                    encounter=encounter, reference=reference, previous={}))
    coordinator = search.Coordinator(path=path, revision='support-runtime', workers=1, telemetry=True,
                                     maximum=maximum, reference=reference, encounter=encounter,
                                     timeout=5.0)
    coordinator.load(store)
    coordinator._ensure_schema(store.db)
    coordinator._ensure_session(store.db)
    return store, coordinator


def _low_mp_dps(parent):
    """The same parent with its DPS MP below its own full-kit cost, so an MP repair is warranted."""
    scenario = mechanics.normalize_scenario(copy.deepcopy(parent))
    placed = {int(row['grid']): row['unit'] for row in students._placed_rows(scenario)}
    grid = next(g for g in sorted(placed) if students.role(placed[g]) == students.ROLE_DPS)
    name = placed[grid]['name']
    index = next(i for i, unit in enumerate(scenario['ownUnits']) if unit['name'] == name)
    scenario['ownUnits'][index]['parameters'][11]['rawValue'] = 5
    return scenario


def _persist(store, coordinator, candidate, payload, *, owner='community', window='development',
             holdout=False, seed_pair=(101, 202), encounter_revision='enc-fixture'):
    policy = coordinator.state['policy']
    intent = dict(scope=owner, owner=owner, ownerShare=1.0, planned_budget=2, stopping='fixture',
                  policy=policy, mechanicsRevision=coordinator._mechanics_revision(),
                  encounterRevision=encounter_revision, measurementWindow=window,
                  compatibility=coordinator._compatibility(), sessionId=coordinator.session_id)
    experiment = ledger.create_experiment(store.db, intent)
    key = 's-%s-%s-%s-%s' % (candidate[:8], seed_pair[0], seed_pair[1], uuid.uuid4().hex[:6])
    db = store.db
    db.execute('INSERT INTO ea_sample(sample_key,candidate_id,policy,mechanics_revision,'
               'encounter_revision,seed_a,seed_b,measurement_window,result,created_at) '
               'VALUES(?,?,?,?,?,?,?,?,?,?)',
               (key, candidate, json.dumps(policy, sort_keys=True, separators=(',', ':')),
                coordinator._mechanics_revision(), encounter_revision, seed_pair[0], seed_pair[1],
                window, json.dumps(payload), 1.0))
    db.execute('INSERT INTO ea_sample_link(experiment_id,candidate_id,seed_a,seed_b,sample_key,'
               'reused,created_at) VALUES(?,?,?,?,?,0,?)',
               (experiment, candidate, seed_pair[0], seed_pair[1], key, 1.0))
    if holdout:
        db.execute('INSERT INTO ea_holdout(experiment_id,candidate_id,seed_a,seed_b) '
                   'VALUES(?,?,?,?)', (experiment, candidate, seed_pair[0], seed_pair[1]))
    db.commit()
    return experiment


def _repairs(pool):
    return [row for row in pool
            if (row.get('expectedMechanicalDifferences') or {}).get('proposalKind') == 'repair']


def _setup(tag):
    path = base.new_dir(tag) / 'lib.sqlite'
    encounter, supplied = base.prep_library(path)
    store, coordinator = build(path, encounter, supplied)
    parent = store.scenario(supplied)
    coordinator.state['policy'] = coordinator._freeze_policy(parent)
    low = _low_mp_dps(parent)
    with store.db:
        low_id = store.add(low, 'low-mp dps', 'mutation', stats(low))
    names, _detail = mp_recovery.watch_units(low)
    return store, coordinator, str(low_id), store.scenario(low_id), (names or [None])[0]


def mp_crossing_payload(dps_name):
    return dict(ticks=100, mpMetrics=[dict(name=dps_name, reachedLowMp=True, firstLowMpTick=3,
                                           firstLowMpPhase='opening', minimumMp=1,
                                           minimumMpPercent=2)])


def _pool(store, coordinator, parent_id, parent):
    return coordinator._propose(None, parent, 'improvement', None, db=store.db,
                                parent_id=parent_id)


def main():
    # 1. A real-schema MP crossing persisted for THIS parent triggers an MP repair in the pool.
    store, coordinator, cid, parent, dps_name = _setup('support-hit')
    try:
        check('support-runtime-fixture-has-a-watched-dps', bool(dps_name), dps_name)
        _persist(store, coordinator, cid, mp_crossing_payload(dps_name))
        pool = _pool(store, coordinator, cid, parent)
        repairs = _repairs(pool)
        check('support-runtime-mp-crossing-fires-mp-repair', bool(repairs),
              [(row['changedFields'], row['expectedMechanicalDifferences']['proposalReason'])
               for row in repairs][:3])
        check('support-runtime-repair-honours-limiting-stats',
              repairs and all(row['changedFields']
                              and all(field.endswith('.11') for field in row['changedFields'])
                              for row in repairs),
              [row['changedFields'] for row in repairs])
        check('support-runtime-repair-keeps-policy-and-fodder-frozen',
              repairs and all(row['approximatelyPreserved']['fodder'] is True
                              and row['approximatelyPreserved']['formation'] is True
                              and not any(field.split('.')[-1] in ('20', '21', '22')
                                          for field in row['changedFields'])
                              for row in repairs),
              [row['approximatelyPreserved']['fodder'] for row in repairs][:2])
    finally:
        coordinator.close()
        store.close()

    # 2. Missing telemetry for the same parent must NOT fire a repair.
    store, coordinator, cid, parent, dps_name = _setup('support-missing')
    try:
        _persist(store, coordinator, cid, dict(ticks=100, mpMetrics=None))
        check('support-runtime-missing-telemetry-does-not-fire',
              _repairs(_pool(store, coordinator, cid, parent)) == [])
    finally:
        coordinator.close()
        store.close()

    # 3. A frozen holdout pair must NOT fire even with identical crossing telemetry.
    store, coordinator, cid, parent, dps_name = _setup('support-holdout')
    try:
        _persist(store, coordinator, cid, mp_crossing_payload(dps_name), holdout=True)
        check('support-runtime-holdout-pair-does-not-fire',
              _repairs(_pool(store, coordinator, cid, parent)) == [])
    finally:
        coordinator.close()
        store.close()

    # 4. A foreign owner's crossing must NOT fire for the community-first parent.
    store, coordinator, cid, parent, dps_name = _setup('support-foreign')
    try:
        _persist(store, coordinator, cid, mp_crossing_payload(dps_name), owner='rebel')
        check('support-runtime-foreign-owner-does-not-fire',
              _repairs(_pool(store, coordinator, cid, parent)) == [])
    finally:
        coordinator.close()
        store.close()

    # 5. A crossing for a unit that is not a watched DPS/healer (a fodder) must NOT fire.
    store, coordinator, cid, parent, dps_name = _setup('support-fodder')
    try:
        _persist(store, coordinator, cid, mp_crossing_payload('Unwatched Fodder Unit'))
        check('support-runtime-fodder-crossing-does-not-fire',
              _repairs(_pool(store, coordinator, cid, parent)) == [])
    finally:
        coordinator.close()
        store.close()

    passed = sum(1 for _name, ok in RESULTS if ok)
    print('%d/%d support-runtime checks passed' % (passed, len(RESULTS)))
    return 0 if passed == len(RESULTS) else 1


if __name__ == '__main__':
    sys.exit(main())
