'''Focused, zero-battle checks for the coordinator seed-history readiness handling.

Run:  <venv>/python -B -u -X utf8 tools/recovery/check_seed_freshness_readiness.py

Driven entirely by a FAKE collector (the fake-clock collector deadline lives in
check_seed_freshness) over the synthetic, deterministic coordinator fixture from
check_encounter_search: no simulator, no battle, no live library, no settings change. Pins the
startup/freshness repair:

  * a blocked global seed-history read fails closed and is recorded as READINESS, not as spent or
    unspendable budget, reserving nothing;
  * a failed full scan is NOT repeated within the same session/fingerprint tick;
  * the purpose budget is byte-for-byte unchanged across a block;
  * recovery retries exactly once on a restart or on a changed library, with budget intact;
  * `_start_experiment` refuses before creating any candidate or reservation while blocked.
'''
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import check_encounter_search as base                                  # noqa: E402
import strategy_encounter_search as search                             # noqa: E402
import strategy_experiment_store as ledger                             # noqa: E402
import strategy_seed_freshness as freshness                            # noqa: E402
from strategy_optimizer import Store                                   # noqa: E402
from strategy_optimizer_adapter import provenance                      # noqa: E402

RESULTS = []
PURPOSES = {'improvement': 8, 'comparison': 8, 'boundary': 0, 'support': 0, 'exploration': 0}


def check(name, condition, detail=''):
    RESULTS.append((name, bool(condition)))
    print(('PASS ' if condition else 'FAIL ') + name + ((' :: ' + str(detail)) if detail else ''))


def coordinator(path, purposes, *, reference=None, encounter=None, maximum=6):
    store = Store(path, provenance())
    coord = search.Coordinator(path=path, revision='check', workers=1, telemetry=True,
                               maximum=maximum, reference=reference, encounter=encounter,
                               timeout=5.0)
    config = search.default_config('community-first', purposes=purposes)
    mapping = dict(enabled=True, mode='community-first', config=config, previous={})
    if encounter is not None:
        mapping['encounter'] = encounter
    if reference is not None:
        mapping['reference'] = reference
    store.set(search.MODE_KEY, mapping)
    coord.load(store)
    coord._ensure_schema(store.db)
    coord._ensure_session(store.db)
    coord._refresh_budget_rows(store.db)
    return store, coord


def budget_rows(coord, store):
    return {row['purpose']: dict(total=row['total'], reserved=row['reserved'],
                                 completed=row['completed'])
            for row in ledger.session_status(store.db, coord.session_id)['budgets']}


def blocked_collect(calls, reason='freshness: read deadline exceeded'):
    def collect(*_a, **_k):
        calls['n'] += 1
        raise freshness.Blocked(reason)
    return collect


def working_collect(calls):
    def collect(*_a, **_k):
        calls['n'] += 1
        return dict(pairs=set(), counts=dict(coverage=dict(scope='established-seed-history')),
                    cacheKey='fake-%d' % calls['n'], provenance=dict(collector='fake'))
    return collect


def blocked_gate_checks():
    path = base.new_dir('seed-ready-block') / 'lib.sqlite'
    encounter_id, reference_id = base.prep_library(path)
    store, coord = coordinator(path, PURPOSES, reference=reference_id, encounter=encounter_id)
    before = budget_rows(coord, store)
    calls = {'n': 0}
    original = freshness.collect
    try:
        freshness.collect = blocked_collect(calls)
        ready = coord._seed_history_ready(store)
        check('readiness-blocked-fails-closed', ready is False and calls['n'] == 1, calls['n'])
        check('readiness-blocked-recorded-as-readiness-not-spent',
              bool(coord._readiness_blocked)
              and coord._readiness_blocked.get('reason', '').startswith('freshness:')
              and 'improvement' not in (coord.state.get('unspendable') or []),
              (coord._readiness_blocked, coord.state.get('unspendable')))
        check('readiness-blocked-limitation-is-distinct',
              any('seed-history readiness blocked' in note for note in coord.limitations)
              and any('seed freshness failed closed' in note for note in coord.limitations),
              list(coord.limitations))
        check('readiness-blocked-preserves-budget', budget_rows(coord, store) == before,
              (budget_rows(coord, store), before))
        again = coord._seed_history_ready(store)
        check('readiness-not-retried-in-same-tick', again is False and calls['n'] == 1, calls['n'])

        def boom(*_a, **_k):
            raise AssertionError('seed-history gate bypassed: candidate/pairs path reached')
        coord._ensure_candidate = boom
        coord._fresh_pairs = boom
        coord._active_child = None
        coord.state['idle'] = False
        proposal = dict(scenario={'ownUnits': []}, plannedBudget=dict(runs=8), features={},
                        domain={}, label='probe', changedFields=[], fixedFields={})
        coord._start_experiment(store.db, store, proposal, 'improvement', None, {}, 'community')
        check('readiness-gate-blocks-before-candidate-and-reservation',
              'improvement' not in (coord.state.get('unspendable') or [])
              and coord.state.get('idle') is True
              and budget_rows(coord, store) == before,
              (coord.state.get('unspendable'), budget_rows(coord, store)))
        before_calls = calls['n']
        store.db.commit()
        external = sqlite3.connect(str(path), timeout=10)
        external.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('readinessProbe','1')")
        external.commit()
        external.close()
        freshness.collect = working_collect(calls)
        recovered = coord._seed_history_ready(store)
        check('readiness-recovers-on-changed-library',
              recovered is True and coord._readiness_blocked is None
              and calls['n'] == before_calls + 1, calls['n'])
        check('readiness-recovery-preserves-budget', budget_rows(coord, store) == before,
              budget_rows(coord, store))
    finally:
        freshness.collect = original
        store.close()


def restart_recovery_checks():
    path = base.new_dir('seed-ready-restart') / 'lib.sqlite'
    encounter_id, reference_id = base.prep_library(path)
    store, coord = coordinator(path, PURPOSES, reference=reference_id, encounter=encounter_id)
    before = budget_rows(coord, store)
    calls = {'n': 0}
    original = freshness.collect
    store2 = None
    try:
        freshness.collect = blocked_collect(calls)
        check('restart-setup-blocked', coord._seed_history_ready(store) is False and calls['n'] == 1,
              calls['n'])
        coord.close()
        store.close()
        store = None
        store2, coord2 = coordinator(path, PURPOSES, reference=reference_id, encounter=encounter_id)
        freshness.collect = working_collect(calls)
        ready = coord2._seed_history_ready(store2)
        check('readiness-recovers-on-restart', ready is True and calls['n'] == 2, calls['n'])
        check('readiness-restart-unchanged-budget', budget_rows(coord2, store2) == before,
              (budget_rows(coord2, store2), before))
    finally:
        freshness.collect = original
        if store is not None:
            store.close()
        if store2 is not None:
            store2.close()


def main():
    blocked_gate_checks()
    restart_recovery_checks()
    passed = sum(1 for _name, ok in RESULTS if ok)
    print('%d/%d seed-history-readiness checks passed' % (passed, len(RESULTS)))
    return 0 if passed == len(RESULTS) else 1


if __name__ == '__main__':
    sys.exit(main())
