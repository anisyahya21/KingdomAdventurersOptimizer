"""A deep validation backlog must not prevent legal discovery proposals and their first runs."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import strategy_optimizer as optimizer
import strategy_optimizer_fast as fast
from strategy_optimizer_adapter import default_scenario, provenance, stats
from check_optimizer_portfolio import outcome


class SyntheticPool:
    def __init__(self, workers):
        self.pool = ThreadPoolExecutor(max_workers=workers)

    def submit(self, function, scenario, seeds):
        def run():
            time.sleep(.01)
            return dict(outcome(seeds[0], 1, 5, 5), seeds=seeds)
        return self.pool.submit(run)

    def shutdown(self, wait=True, cancel_futures=False):
        self.pool.shutdown(wait=wait, cancel_futures=cancel_futures)


class DiscoveryReservoirTests(unittest.TestCase):
    def trial(self, enabled):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'deep.sqlite'
            store = optimizer.Store(path, provenance())
            base = dict(default_scenario(), encounterId=19, tickLimit=40)
            cid = store.add(base, 'Deep baseline', 'supplied', stats(base))
            store.set('scope', optimizer.scope(base))
            for phase, count in [('discovery', 24), ('validation', 64)]:
                for ordinal in range(count):
                    store.record(cid, phase, ordinal,
                                 dict(outcome(ordinal, 1, 5, 5), seeds=optimizer.seed_pair(phase, ordinal)))
            store.set('probeBanks', {cid: 30000})
            store.db.commit()
            store.close()
            with patch.object(fast, 'HeadlessPool', SyntheticPool), \
                 patch.object(optimizer, 'ASYNC_PUBLICATION', False), \
                 patch.object(optimizer, 'BALANCED_DISCOVERY', enabled), \
                 patch.object(optimizer.Optimizer, '_auto_tune', return_value=False), \
                 patch.object(optimizer.Optimizer, '_advance_probe_plans', return_value=False), \
                 patch.object(optimizer.Optimizer, '_advance_finetune_programs', return_value=False):
                live = optimizer.Optimizer(path)
                peak = 0
                try:
                    deadline = time.monotonic()+10
                    while live.status()['state'] == 'Opening library':
                        self.assertLess(time.monotonic(), deadline)
                        time.sleep(.02)
                    live.command('focus_encounter', {'encounterId': 19}, wait=True)
                    live.command('start', {'workers': 4, 'duty': 1}, wait=True)
                    deadline = time.monotonic()+3
                    while time.monotonic() < deadline:
                        self.assertFalse(live.status().get('error'), live.status().get('error'))
                        peak = max(peak, len(live._planner_order))
                        time.sleep(.03)
                    live.command('pause', {}, wait=True)
                finally:
                    live.command('close')
                    live.thread.join(20)
                    self.assertFalse(live.thread.is_alive())
            store = optimizer.Store(path, provenance())
            try:
                candidates = [dict(r) for r in store.db.execute('SELECT id,scenario FROM candidate')]
                children = {r['id'] for r in candidates if r['id'] != cid}
                discovery = sum(store.next_ordinal(child, 'discovery') for child in children)
                validation = store.next_ordinal(cid, 'validation')-64
                self.assertTrue(all(json.loads(r['scenario'])['encounterId'] == 19 for r in candidates))
                self.assertGreater(validation, 0)
                self.assertLess(validation, 29936)  # deep work remains when discovery begins
                if enabled:
                    self.assertGreater(discovery, 0)
                    self.assertLessEqual(peak, 4*48)
                else:
                    self.assertEqual(discovery, 0)
                return discovery, validation, peak
            finally:
                store.close()

    def test_discovery_runs_while_validation_remains_authorized(self):
        control = self.trial(False)
        treatment = self.trial(True)
        print('Discovery, deep validation, peak ready queue:', control, '->', treatment)


if __name__ == '__main__':
    unittest.main(verbosity=2)
