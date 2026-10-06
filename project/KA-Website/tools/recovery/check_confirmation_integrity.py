"""Independent regression checks for frozen experiments, including real spawned workers."""
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

import strategy_confirmation as c
from check_optimizer_confirmation import make_library, controller, SIMULATOR
from strategy_optimizer import seed_pair


class IntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.library = self.root / 'library.sqlite'
        self.scenarios = {'A': {'base': 10}, 'B': {'base': 7}}
        make_library(self.library, self.scenarios)
        self.provenance = c.simulator_provenance(SIMULATOR)
        self.manifest = c.build_manifest(self.library, 3, self.scenarios, self.provenance,
                                        set(), SIMULATOR, controller([0, 10, 1, 11, 2, 12]))

    def tearDown(self):
        self.temp.cleanup()

    def test_unique_draws_and_evicted_seed_exclusion(self):
        pairs, rejected = c.draw_seed_pairs(2, set(), controller([1, 2, 1, 2, 3, 4]))
        self.assertEqual((pairs, rejected), ([(1, 2), (3, 4)], 1))
        db = c.open_library_readonly(self.library)
        try:
            reserved = c.authorized_bank_seeds(db, ['A'])
            self.assertIn(tuple(seed_pair('validation', 50000)), reserved)
        finally:
            db.close()

    def test_manifest_tamper_and_duplicate_seeds_rejected(self):
        for key, value in [('seeds', [[4, 5], [1, 11], [2, 12]]), ('runs', 99)]:
            bad = copy.deepcopy(self.manifest)
            bad[key] = value
            with self.assertRaises(ValueError):
                c.verify_manifest(bad, self.manifest['fingerprint'], set(), self.scenarios, SIMULATOR)
        bad = copy.deepcopy(self.manifest)
        bad['seeds'][1] = bad['seeds'][0]
        bad['digest'] = c._digest({k: v for k, v in bad.items() if k != 'digest'})
        with self.assertRaises(ValueError):
            c.verify_manifest(bad, self.manifest['fingerprint'], set(), self.scenarios, SIMULATOR)

    def test_results_database_mismatches_rejected(self):
        for index, sql in enumerate([
            "UPDATE meta SET value='foreign' WHERE key='digest'",
            'UPDATE seed SET math=999 WHERE ordinal=0',
            "INSERT INTO run(candidate,ordinal,status) VALUES ('foreign',0,'failed')",
            "INSERT INTO run(candidate,ordinal,status) VALUES ('A',99,'failed')",
        ]):
            store = c.RunStore(self.root / f'result-{index}.sqlite')
            try:
                store.save_manifest(self.manifest)
                store.db.execute(sql)
                store.db.commit()
                with self.assertRaises(ValueError):
                    store.save_manifest(self.manifest)
            finally:
                store.close()

    def test_spawned_pool_and_completed_resume(self):
        output = self.root / 'spawn'
        kwargs = dict(library=self.library, candidates=['A', 'B'], runs=3, workers=2,
                      output=output, simulator=SIMULATOR)
        report = c.run_confirmation(**kwargs, randbits=controller([0, 10, 1, 11, 2, 12]))
        self.assertFalse(report['candidates']['A']['incomplete'])
        self.assertAlmostEqual(report['candidates']['A']['mean'], 11.)
        self.assertAlmostEqual(report['candidates']['A']['approximateFixedSample95Halfwidth'],
                               1.96 / 3**.5)
        self.assertEqual(report['paired'][0]['meanDelta'], 3.)
        self.assertEqual(report, c.run_confirmation(**kwargs, randbits=controller([])))

    def test_submission_consumes_only_bounded_prefix(self):
        from concurrent.futures import Future
        consumed = []
        def tasks():
            for i in range(100):
                consumed.append(i)
                yield i
        def submit(value):
            result = Future()
            result.set_result(value)
            return result
        def receive(job, value):
            if value == 0:
                self.assertEqual(len(consumed), 3)
        c.bounded_submit(tasks(), submit, receive, 3)


if __name__ == '__main__':
    unittest.main(verbosity=2)
