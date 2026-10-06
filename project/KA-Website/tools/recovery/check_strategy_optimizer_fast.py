"""Real headless worker parity, reuse, error handling and bounded shutdown."""
import unittest
from concurrent.futures import wait
from strategy_optimizer_adapter import default_scenario, simulate
from strategy_optimizer_fast import HeadlessPool, runtime_path
from strategy_optimizer import worker


@unittest.skipUnless(runtime_path().is_file(), 'Install Headless Runtime.ps1 is required')
class FastWorkerTests(unittest.TestCase):
    def test_reuses_runtime_and_matches_reference(self):
        pool = HeadlessPool(1)
        try:
            pid = pool.processes[0].pid
            scenario = default_scenario()
            scenario['tickLimit'] = 250
            for seeds in ([7, 8], [903, 217]):
                result = pool.submit(worker, scenario, seeds).result(timeout=60)
                self.assertEqual(result['digest'], simulate(scenario, seeds)['digest'])
                self.assertGreater(result['elapsedSeconds'], 0)
                self.assertIn('headless native worker', result['executionBackend'])
                self.assertEqual(pool.processes[0].pid, pid)
            pool.digest = 'different-source'
            with self.assertRaisesRegex(RuntimeError, 'combat source differs'):
                pool.submit(worker, scenario, [7, 8]).result(timeout=30)
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
        self.assertTrue(all(p.poll() is not None for p in pool.processes))

    def test_termination_unblocks_both_workers(self):
        pool = HeadlessPool(2)
        scenario = default_scenario()
        scenario['tickLimit'] = 7000
        jobs = [pool.submit(worker, scenario, [7+i, 8+i]) for i in range(2)]
        pool.terminate_workers()
        self.assertEqual(len(wait(jobs, timeout=5).not_done), 0)
        self.assertTrue(all(p.poll() is not None for p in pool.processes))


if __name__ == '__main__':
    unittest.main(verbosity=2)
