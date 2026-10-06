"""Failure injection through the real activation path; temporary SQLite only, zero battles."""
from copy import deepcopy
from pathlib import Path
import tempfile

import strategy_encounter_search as search
import strategy_experiment_store as ledger
from strategy_optimizer import Store, canonical
from strategy_optimizer_adapter import provenance
from check_encounter_runtime_bridge import stub_optimizer


def run():
    checks = 0
    with tempfile.TemporaryDirectory(prefix='ka-rollout-') as directory:
        store = Store(Path(directory) / 'copy.sqlite', provenance())
        try:
            ledger.initialize(store.db)
            old_config = search.default_config(purposes={'improvement': 4})
            old_session = ledger.configure_session(store.db, old_config)
            store.set('unrelated-user-state', {'preserve': ['candidate', 'result', 'lineage']})
            store.db.commit()
            coordinator = search.Coordinator(path=store.path if hasattr(store, 'path') else 'test',
                                             revision='atomic-check')
            optimizer = stub_optimizer(coordinator)
            optimizer.provenance = provenance()
            before_preview = list(store.db.iterdump())
            preview = optimizer._preview_encounter(store, {
                'mode': 'community-first', 'purposes': {'improvement': 8},
                'encounter': 19, 'thresholds': [1, 10]})
            assert list(store.db.iterdump()) == before_preview
            assert preview['encounter'] == 19 and preview['referenceId'] is None
            assert preview['thresholds'] == [1, 10]
            assert len(preview['presentation']['questionFields']) > 0
            checks += 4
            for request in ({'referenceId': 'absent'}, {'encounter': True},
                            {'thresholds': [1.5]}, {'reference': 'x', 'referenceId': 'y'}):
                try:
                    optimizer._preview_encounter(store, request)
                except ValueError:
                    checks += 1
                else:
                    raise AssertionError('invalid preview scope accepted: %r' % request)
            config = search.default_config(purposes={'improvement': 8})
            scope = {key: getattr(coordinator, key, None)
                     for key in ('encounter', 'reference', 'constraints', 'thresholds')}
            optimizer._encounter_preview_token = dict(configDigest=canonical(config),
                                                       scopeDigest=canonical(scope))
            before_db = list(store.db.iterdump())
            before_state = deepcopy(coordinator.state)
            original_set = store.set

            def fail_after_runtime_write(key, value):
                original_set(key, value)
                if key == search.RUNTIME_KEY:
                    raise RuntimeError('injected after runtime state write')

            store.set = fail_after_runtime_write
            try:
                optimizer._activate_encounter(store, {'config': config})
            except RuntimeError as error:
                assert 'injected' in str(error)
            else:
                raise AssertionError('injected activation was accepted')
            finally:
                store.set = original_set
            assert list(store.db.iterdump()) == before_db, 'partial activation persisted database writes'
            checks += 1
            assert coordinator.state == before_state and not coordinator.enabled
            checks += 1
            assert store.db.execute('SELECT active FROM ea_session WHERE id=?',
                                    (old_session,)).fetchone()[0] == 1
            checks += 1
            result = optimizer._activate_encounter(store, {'config': config})
            assert result['ok'] and coordinator.enabled
            checks += 1
            assert store.db.execute('SELECT COUNT(*) FROM ea_session WHERE active=1').fetchone()[0] == 1
            checks += 1
            assert store.get('unrelated-user-state') == {'preserve': ['candidate', 'result', 'lineage']}
            checks += 1
            assert not store.db.in_transaction, 'activation acknowledgement preceded durable commit'
            checks += 1
            before_rollback_db = list(store.db.iterdump())
            before_rollback_state = deepcopy(coordinator.state)

            def fail_rollback(key, value):
                original_set(key, value)
                if key == search.MODE_KEY:
                    raise RuntimeError('injected rollback failure')

            store.set = fail_rollback
            try:
                optimizer._rollback_encounter(store)
            except RuntimeError as error:
                assert 'injected rollback' in str(error)
            else:
                raise AssertionError('failed rollback was acknowledged')
            finally:
                store.set = original_set
            assert list(store.db.iterdump()) == before_rollback_db
            assert coordinator.enabled and coordinator.state == before_rollback_state
            checks += 2
            optimizer._rollback_encounter(store)
            assert not coordinator.enabled and not store.db.in_transaction
            assert store.db.execute('SELECT COUNT(*) FROM ea_session WHERE active=1').fetchone()[0] == 0
            checks += 2
            sessions = []
            for encounter_id, thresholds in ((0, None), (0, [5]), (19, None)):
                request = {'mode': 'community-first', 'purposes': {'improvement': 8},
                           'encounter': encounter_id, 'thresholds': thresholds}
                preview = optimizer._preview_encounter(store, request)
                request['config'] = preview['config']
                sessions.append(optimizer._activate_encounter(store, request)['sessionId'])
                if len(sessions) == 2:
                    assert coordinator.state.get('studyMarker') == 0
                    checks += 1
                if len(sessions) == 3:
                    assert 'studyMarker' not in coordinator.state
                    checks += 1
                coordinator.state['studyMarker'] = encounter_id
                optimizer._rollback_encounter(store)
            assert sessions[0] == sessions[1], 'display thresholds minted another budget'
            assert sessions[0] != sessions[2], 'different encounters reused one budget session'
            checks += 2
            request = {'mode': 'all-strategy', 'purposes': {'improvement': 8},
                       'allocations': {'community': 0.5, 'rebel': 0.5}, 'encounter': 19}
            preview = optimizer._preview_encounter(store, request)
            request['config'] = preview['config']
            result = optimizer._activate_encounter(store, request)
            assert result['mode'] == 'all-strategy'
            rows = ledger.session_status(store.db, result['sessionId'])['budgets']
            assert sum(row['total'] for row in rows) == 8
            assert {row['owner'] for row in rows if row['total']} == {'community', 'rebel'}
            checks += 3
            optimizer._rollback_encounter(store)
            request = {'mode': 'community-first', 'purposes': {'improvement': 8},
                       'encounter': 0, 'allocations': {'community': 1}}
            preview = optimizer._preview_encounter(store, request)
            request['config'] = preview['config']
            result = optimizer._activate_encounter(store, request)
            assert result['sessionId'] == sessions[0], 'returning to a study minted another budget'
            assert coordinator.state.get('studyMarker') == 0
            checks += 2
            optimizer._rollback_encounter(store)
        finally:
            store.close()
    print(f'PASS {checks} atomic rollout checks; no battles or live writes')


if __name__ == '__main__':
    run()
