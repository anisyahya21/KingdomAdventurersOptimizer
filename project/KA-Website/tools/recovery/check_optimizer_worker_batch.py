'''Bounded headless batch protocol: parity, cache reuse, bounds, provenance, death.'''
import unittest

from strategy_optimizer_adapter import default_scenario, simulate
from strategy_optimizer_fast import BATCH_MAX_PAIRS, BATCH_MIN_PAIRS, HeadlessPool, runtime_path
from strategy_optimizer import worker

TIMING_KEYS = ('elapsedSeconds', 'executionBackend', 'accelerators')


def short_scenario():
    # The canonical default, shortened so the gate never spends a long fight.
    scenario = default_scenario()
    scenario['tickLimit'] = 150
    return scenario


def semantic(result):
    '''The complete simulation output with the per-worker timing metadata removed.'''
    return {key: value for key, value in result.items() if key not in TIMING_KEYS}


@unittest.skipUnless(runtime_path().is_file(), 'Install Headless Runtime.ps1 is required')
class BatchWorkerTests(unittest.TestCase):
    def test_batch_matches_scalar_and_preserves_order(self):
        pool = HeadlessPool(1)
        try:
            scenario = short_scenario()
            pairs = [[7, 8], [903, 217], [12345, 54321]]
            batch = pool.submit_batch(worker, scenario, pairs).result(timeout=120)
            self.assertEqual(len(batch), len(pairs))
            for pair, entry in zip(pairs, batch):
                self.assertEqual(entry['seeds'], pair)
                self.assertGreater(entry['elapsedSeconds'], 0)
                self.assertIn('headless native worker', entry['executionBackend'])
                scalar = pool.submit(worker, scenario, pair).result(timeout=120)
                self.assertEqual(semantic(entry), semantic(scalar))
                self.assertEqual(entry['digest'], simulate(scenario, pair)['digest'])
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
        self.assertTrue(all(p.poll() is not None for p in pool.processes))

    def test_template_cache_reuse_is_seed_deterministic(self):
        pool = HeadlessPool(1)
        try:
            scenario = short_scenario()
            pair = [903, 217]
            both = pool.submit_batch(worker, scenario, [[7, 8], pair]).result(timeout=120)
            alone = pool.submit_batch(worker, scenario, [pair]).result(timeout=120)
            # The same pair run after another seed in one batch equals running it alone, so the
            # reused pre-battle template never carries the previous seed's state.
            self.assertEqual(semantic(both[1]), semantic(alone[0]))
            self.assertEqual(both[1]['digest'], alone[0]['digest'])
            self.assertEqual(both[1]['digest'], simulate(scenario, pair)['digest'])
        finally:
            pool.shutdown(wait=True, cancel_futures=True)

    def test_batch_size_bounds_are_enforced_without_losing_the_pool(self):
        pool = HeadlessPool(1)
        try:
            scenario = short_scenario()
            self.assertEqual(BATCH_MIN_PAIRS, 1)
            self.assertEqual(BATCH_MAX_PAIRS, 8)
            for pairs in ([], [[1, 2]] * (BATCH_MAX_PAIRS + 1)):
                with self.assertRaises(ValueError):
                    pool.submit_batch(worker, scenario, pairs).result(timeout=30)
            valid = pool.submit_batch(worker, scenario, [[7, 8]]).result(timeout=120)
            self.assertEqual(valid[0]['digest'], simulate(scenario, [7, 8])['digest'])
        finally:
            pool.shutdown(wait=True, cancel_futures=True)

    def test_batch_provenance_mismatch_fails(self):
        pool = HeadlessPool(1)
        try:
            scenario = short_scenario()
            pool.digest = 'different-source'
            with self.assertRaisesRegex(RuntimeError, 'combat source differs'):
                pool.submit_batch(worker, scenario, [[7, 8]]).result(timeout=30)
        finally:
            pool.shutdown(wait=True, cancel_futures=True)

    def test_dead_worker_fails_the_batch_without_hanging(self):
        pool = HeadlessPool(1)
        try:
            scenario = short_scenario()
            pool.processes[0].kill()
            pool.processes[0].wait(timeout=10)
            future = pool.submit_batch(worker, scenario, [[7, 8]])
            try:
                future.result(timeout=15)
            except TimeoutError:
                self.fail('batch hung instead of failing after the worker process died')
            except Exception:
                pass
            else:
                self.fail('a dead worker produced a batch result')
        finally:
            pool.shutdown(wait=True, cancel_futures=True)


if __name__ == '__main__':
    unittest.main(verbosity=2)
