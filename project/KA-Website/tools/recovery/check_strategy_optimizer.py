"""Critical persistence, scheduling, archive and process-boundary regression tests."""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import zlib
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor
from strategy_optimizer import (Store, identity, seed_pair, summarize, family_key, quality,
                                interesting, VALIDATION_RUNS, MAX_SAMPLES, worker,
                                same_simulator, discovery_viable, encounter_family,
                                _shallow_encounter_mapping)
from strategy_optimizer_limits import initialize_worker


def row(index=0, verdict=1, censored=False, prizes=3):
    return dict(verdict=verdict, censored=censored, prizeCallbacks=prizes, ticks=100,
                survivors=2, resourceUses=0, behavior=dict(heals=0, attacks=10, prizes=prizes),
                seeds=seed_pair('validation', index), digest=str(index))


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/'library.sqlite'
        self.store = Store(self.path, dict(digest='one'))
        with self.store.db:
            self.cid = self.store.add(dict(ownUnits=[1], tickLimit=100, encounterId=19), 'A', 'supplied', {})

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_atomic_resume_and_duplicate_completion(self):
        self.store.record(self.cid, 'validation', 0, row())
        self.store.record(self.cid, 'validation', 0, row())
        self.assertEqual(self.store.get('totalRuns'), 1)
        self.store.close()
        self.store = Store(self.path, dict(digest='one'))
        self.assertEqual(self.store.next_ordinal(self.cid, 'validation'), 1)
        self.assertEqual(self.store.rows(self.cid)[0]['seeds'], seed_pair('validation', 0))

    def test_predictor_history_keeps_pruned_scenario_and_result_summary(self):
        self.store.record(self.cid, 'validation', 0, row())
        self.store._archive_predictor_history(self.cid)
        self.store.db.execute('DELETE FROM run WHERE candidate=?', (self.cid,))
        self.store.db.execute('DELETE FROM candidate WHERE id=?', (self.cid,))
        self.store.db.commit()
        saved = self.store.db.execute(
            'SELECT scenario_zlib,validation FROM predictor_history WHERE candidate=?',
            (self.cid,)).fetchone()
        self.assertEqual(json.loads(zlib.decompress(saved[0]))['encounterId'], 19)
        self.assertEqual(json.loads(saved[1])['wins'], 1)

    def test_timing_counts_each_completed_battle_once(self):
        result = dict(row(), elapsedSeconds=6.25)
        self.store.record(self.cid, 'discovery', 0, result)
        self.store.record(self.cid, 'discovery', 0, result)
        self.assertEqual(self.store.get('timedRuns'), 1)
        self.assertEqual(self.store.get('simulationSeconds'), 6.25)

    def test_early_deferral_and_encounter_separation(self):
        self.assertTrue(discovery_viable([row(verdict=2)]))
        self.assertFalse(discovery_viable([row(verdict=2), row(verdict=2)]))
        self.assertTrue(discovery_viable([row(verdict=2), row(verdict=1)]))
        self.assertNotEqual(encounter_family({'encounterId': 0}, [row()]),
                            encounter_family({'encounterId': 19}, [row()]))

    def test_source_compatibility_is_narrow(self):
        key = 'tools/recovery/combat_shared_controllers.py'
        before = '797000821d5dc0dca722209dddd2be8ed197c2667d612b5844b04ad2dbe3e444'
        after = 'ea4f43b263293c232fb8a9b819c28a002653b6fe7370ea0ecd9317695230184d'
        a = dict(mode='research', files={key: before, 'combat_data': 'same'})
        b = dict(mode='research', files={key: after, 'combat_data': 'same',
                                      'tools/recovery/strategy_optimizer.py': 'updated'})
        self.assertTrue(same_simulator(a, b))
        b['files']['combat_data'] = 'changed'
        self.assertFalse(same_simulator(a, b))
        self.assertFalse(same_simulator({}, {}))

    def test_mode_mapping_keeps_one_rollback_snapshot(self):
        raw = '{}'
        for _ in range(2200):
            raw = '{"previous":{"mapping":' + raw + '}}'
        mapping = _shallow_encounter_mapping(raw)
        self.assertEqual(mapping, {'previous': {'mapping': {'previous': {}}}})
        # A new activation saves the immediate prior mode without its own ancestry.
        prior = {'enabled': True, 'config': {'mode': 'community-first'},
                 'previous': {'mapping': {'enabled': False}}}
        for _ in range(100):
            prior = {'enabled': True, 'config': prior['config'],
                     'previous': {'mapping': {key: value for key, value in prior.items()
                                              if key != 'previous'}}}
            self.assertLess(len(json.dumps(prior)), 250)

    def test_out_of_order_result_cannot_skip_an_unfinished_seed(self):
        with self.assertRaises(ValueError):
            self.store.record(self.cid, 'validation', 1, row(1))
        self.assertEqual(self.store.next_ordinal(self.cid, 'validation'), 0)
        self.store.record(self.cid, 'validation', 0, row(0))
        self.store.record(self.cid, 'validation', 1, row(1))
        self.assertEqual(self.store.next_ordinal(self.cid, 'validation'), 2)

    def test_rollback_preserves_previous_work(self):
        self.store.record(self.cid, 'discovery', 0, row())
        try:
            with self.store.db:
                self.store.set('totalRuns', 999)
                raise RuntimeError('interrupted transaction')
        except RuntimeError:
            pass
        self.assertEqual(self.store.get('totalRuns'), 1)
        self.assertEqual(self.store.db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')

    def test_process_death_rolls_back_uncommitted_cursor(self):
        code = ('import sqlite3,os,sys; c=sqlite3.connect(sys.argv[1]); '
                'c.execute("UPDATE meta SET value=\'900\' WHERE key=\'totalRuns\'"); os._exit(7)')
        result = subprocess.run([sys.executable, '-c', code, str(self.path)], capture_output=True)
        self.assertEqual(result.returncode, 7)
        self.assertEqual(self.store.get('totalRuns'), 0)

    def test_provenance_refuses_mixed_simulators(self):
        self.store.close()
        self.store = Store(self.path, dict(digest='two'))
        self.assertFalse(self.store.compatible)
        with self.assertRaises(ValueError):
            self.store.add({}, 'B', 'supplied', {})

    def test_archive_requires_holdout_and_reliability(self):
        for i in range(VALIDATION_RUNS-1):
            self.store.record(self.cid, 'validation', i, row(i))
        self.assertEqual(self.store.archive(), [])
        self.store.record(self.cid, 'validation', VALIDATION_RUNS-1, row(VALIDATION_RUNS-1))
        self.assertEqual(len(self.store.archive()), 1)
        self.assertIsNone(summarize(self.store.rows(self.cid))['retainedMean'])

    def test_recent_losses_keep_elite_and_incomparability_releases_it(self):
        """Objective v2: reliability is no longer a withdrawal gate, comparability still is.

        The old rule dropped a cell as soon as the recent win interval fell under 0.80, which is the
        rule that made a rare high-yield mechanism unreachable. Losing seeds now leave the cell alone;
        an unresolved run in the bank (which makes the banks incomparable) still releases it.
        """
        for i in range(64):
            self.store.record(self.cid, 'validation', i, row(i))
        self.assertEqual(len(self.store.archive()), 1)
        for i in range(64, 100):
            self.store.record(self.cid, 'validation', i, row(i, verdict=2))
        self.assertEqual(len(self.store.archive()), 1,
                         'a losing streak must not withdraw an archive cell any more')
        self.assertLess(summarize(self.store.rows(self.cid, 'validation'))['winInterval'][0], .8,
                        'the fixture must actually be unreliable, or this proves nothing')
        self.store.record(self.cid, 'validation', 100, row(100, None, True, None))
        self.assertEqual(self.store.archive(), [],
                         'an unresolved run makes the banks incomparable, so the cell is released')

    def test_bounded_samples_keep_anchor_and_seed_cursor(self):
        for i in range(MAX_SAMPLES+20):
            self.store.record(self.cid, 'validation', i, row(i))
        rows = self.store.rows(self.cid, 'validation')
        self.assertEqual(len(rows), MAX_SAMPLES)
        self.assertEqual(rows[63]['seeds'], seed_pair('validation', 63))
        self.assertEqual(rows[-1]['seeds'], seed_pair('validation', MAX_SAMPLES+19))
        self.assertEqual(self.store.next_ordinal(self.cid, 'validation'), MAX_SAMPLES+20)
        self.assertEqual(self.store.get('totalRuns'), MAX_SAMPLES+20)
        self.assertEqual(self.store.get(f'aggregate:{self.cid}:validation')['n'], MAX_SAMPLES+20)

    def test_near_duplicate_builds_share_behavior_cell(self):
        self.assertEqual(family_key([row(prizes=3)]), family_key([row(prizes=4)]))
        self.assertNotEqual(family_key([row(prizes=0)]), family_key([row(prizes=4)]))

    def test_censoring_and_lucky_seed_cannot_win(self):
        self.assertIsNone(quality(summarize([row(prizes=1000)])))
        rows = [row(i) for i in range(64)]
        rows[-1] = row(63, None, True, None)
        summary = summarize(rows)
        self.assertEqual(summary['censored'], 1)
        self.assertIsNone(quality(summary))

    def test_separate_seed_banks_and_replay_categories(self):
        a = {tuple(seed_pair('discovery', i)) for i in range(64)}
        b = {tuple(seed_pair('validation', i)) for i in range(64)}
        self.assertFalse(a & b)
        self.assertIn('Failure', interesting([row(verdict=2)]))


class IntegrationTests(unittest.TestCase):
    def test_real_worker_memory_limited_headless_and_replay(self):
        from strategy_optimizer_adapter import default_scenario, simulate
        scenario = default_scenario()
        scenario['tickLimit'] = 5
        pair = seed_pair('discovery', 0)
        with ProcessPoolExecutor(max_workers=1, initializer=initialize_worker) as pool:
            compact = pool.submit(worker, scenario, pair).result(timeout=60)
        traced = simulate(scenario, pair, trace=True)
        self.assertEqual(compact['digest'], traced['digest'])
        self.assertNotIn('replay', compact)
        self.assertEqual(traced['replay']['schema'], 'ka-battle-replay-1')

    def test_service_pause_close_resume(self):
        from strategy_optimizer_adapter import default_scenario, provenance, stats
        from strategy_optimizer import Optimizer, scope
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/'service.sqlite'
            store = Store(path, provenance())
            scenario = default_scenario()
            scenario['tickLimit'] = 5
            with store.db:
                store.set('scope', scope(scenario))
                store.add(scenario, 'Short integration scenario', 'supplied', stats(scenario))
            store.close()
            app = Optimizer(path)
            deadline = time.monotonic()+30
            while app.status()['state'] == 'Opening library' and time.monotonic()<deadline:
                time.sleep(.05)
            app.command('start', dict(workers=1, duty=.8))
            while (app.status().get('proposals') or 0)<1 and time.monotonic()<deadline:
                time.sleep(.05)
            app.command('pause')
            while app.status()['state'] not in ('Paused', 'Error') and time.monotonic()<deadline:
                time.sleep(.05)
            self.assertEqual(app.status()['state'], 'Paused', app.status())
            self.assertGreaterEqual(app.status()['proposals'], 1)
            self.assertLess(app.status()['totalRuns'], 72)
            self.assertGreater(app.status()['averageSimulationSeconds'], 0)
            self.assertGreater(app.status()['sessionElapsedSeconds'], 0)
            app.command('all_encounters', wait=True)
            self.assertIsNone(app.status()['error'])
            self.assertEqual({c['scenario']['encounterId'] for c in app.status()['candidates']}, set(range(20)))
            app.command('close')
            app.thread.join(15)
            self.assertFalse(app.thread.is_alive())
            resumed = Store(path, provenance())
            self.assertGreaterEqual(resumed.get('totalRuns'), 2)
            self.assertGreaterEqual(resumed.next_ordinal(resumed.candidates()[0]['id'], 'discovery'), 2)
            resumed.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
