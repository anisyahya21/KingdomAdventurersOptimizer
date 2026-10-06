"""Portfolio decisions use deep paired evidence and cannot turn missing outcomes into a gain."""
import tempfile
from pathlib import Path
import unittest

import strategy_optimizer as optimizer
import strategy_optimizer_portfolio as portfolio
from strategy_optimizer_adapter import default_scenario, provenance, stats
from check_optimizer_portfolio import outcome, variant


class PortfolioEvidenceTests(unittest.TestCase):
    def test_replay_eviction_cannot_reverse_refinement_comparison(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'evidence.sqlite'
            store = optimizer.Store(path, provenance())
            try:
                base = dict(default_scenario(), encounterId=19, tickLimit=40)
                child = variant(base, 0, 50)
                parent_id = store.add(base, 'parent', 'supplied', stats(base))
                child_id = store.add(child, 'child', 'probe', stats(child))
                store.set('fineTunePrograms', {'paired': dict(encounterId=19,
                    parent={'candidateId': parent_id}, points={'50': {'candidateId': child_id}})})
                for index in range(900):
                    if index < 700:
                        store.record(parent_id, 'validation', index, outcome(index, 1, 10, 10))
                    # These wins disappear from the replay window, but remain valid evidence.
                    chests = 100 if 64 <= index < 252 else 0
                    store.record(child_id, 'validation', index, outcome(index, 1, chests, chests))
                store.flush()
                self.assertEqual(len(store.rows(parent_id, 'validation')), 512)
                self.assertEqual(len(store.rows(child_id, 'validation')), 512)
                cache = {}
                result = portfolio.portfolio_tune_comparisons(store, 19, cache=cache)
                earned = [r for r in result[child_id]
                          if r['objective'] == portfolio.PORTFOLIO_AVERAGE_EARNED]
                self.assertEqual(earned[0]['samples'], 700)
                self.assertEqual(earned[0]['verdict'], 'improved')
                self.assertEqual(portfolio.portfolio_tune_comparisons(store, 19, cache=cache), result)
                store.close()
                store = optimizer.Store(path, provenance())
                self.assertEqual(portfolio.portfolio_tune_comparisons(store, 19), result)
            finally:
                store.close()

    def test_asymmetric_unresolved_or_missing_reading_cannot_establish_improvement(self):
        before = [dict(verdict=1, censored=False, chests=10, potential=None) for _ in range(20)]
        for unknown in (dict(verdict=None, censored=True, chests=None, potential=None),
                        dict(verdict=1, censored=False, chests=None, potential=None)):
            after = [dict(row, chests=20) for row in before]
            after[0] = unknown
            result = portfolio.portfolio_tune_comparison(
                portfolio.PORTFOLIO_AVERAGE_EARNED, before, after)
            self.assertEqual(result['verdict'], 'inconclusive')
            self.assertTrue(result['incomplete'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
