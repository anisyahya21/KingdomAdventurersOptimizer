"""Recompute only changed paired evidence; cache lifecycle cannot change a verdict."""
import unittest
from unittest.mock import patch

import strategy_optimizer as optimizer
import strategy_optimizer_portfolio as portfolio
from strategy_optimizer_adapter import stats
from check_optimizer_finetune import open_store, close_store, base_scenario, outcome


class PairCacheTests(unittest.TestCase):
    def test_changed_sibling_metadata_threshold_and_parent(self):
        store = open_store()
        try:
            ids = []
            for index, hp in enumerate((7149, 6500, 6000)):
                scenario = base_scenario(hp)
                cid = store.add(scenario, str(index), 'probe', stats(scenario))
                ids.append(cid)
                for ordinal in range(700):
                    store.record(cid, 'validation', ordinal, outcome(index, ordinal, 8+index))
            store.flush()
            parent, first, sibling = ids
            program = dict(encounterId=19, parent={'candidateId': parent},
                           points={'6500': {'candidateId': first}, '6000': {'candidateId': sibling}})
            store.set('fineTunePrograms', {'program': program})
            cache = {}
            original = portfolio.portfolio_tune_comparison
            def verify(expected_calls, minimum=None):
                with patch.object(portfolio, 'portfolio_tune_comparison', wraps=original) as compare:
                    actual = portfolio.portfolio_tune_comparisons(store, 19, minimum, cache)
                    self.assertEqual(compare.call_count, expected_calls)
                self.assertEqual(actual, portfolio.portfolio_tune_comparisons(store, 19, minimum))
                return actual
            result = verify(4)
            result[first][0]['verdict'] = 'corrupted by consumer'
            verify(0)
            signature = portfolio.portfolio_tune_signature(store, 19)
            program['updatedAt'] = 1234
            program['points']['6500']['bank'] = 4000
            store.set('fineTunePrograms', {'program': program})
            self.assertEqual(signature, portfolio.portfolio_tune_signature(store, 19))
            verify(0)
            store.record(first, 'validation', 700, outcome(1, 700, 50)); store.flush()
            verify(2)  # Two objectives for the changed point; its sibling stays cached.
            store.record(parent, 'validation', 700, outcome(0, 700, 50)); store.flush()
            verify(4)
            verify(4, minimum=1000)  # Threshold changes cannot reuse an old conclusion.
            del program['points']['6000']
            store.set('fineTunePrograms', {'program': program})
            verify(0, minimum=1000)
            self.assertEqual(set(cache['pairs']), {('program', '6500')})
            self.assertNotIn(sibling, cache['readings'])
            store.set('fineTunePrograms', {})
            verify(0)
            self.assertFalse(cache['pairs']); self.assertFalse(cache['readings'])
        finally:
            store.db.commit()
            close_store(store)

    def test_reusing_cache_for_another_store_cannot_reuse_revision_zero(self):
        stores = [open_store(), open_store()]
        try:
            cache = {}
            results = []
            for index, store in enumerate(stores):
                ids = []
                for member, hp in enumerate((7149, 6500)):
                    scenario = base_scenario(hp)
                    cid = store.add(scenario, str(member), 'probe', stats(scenario)); ids.append(cid)
                    for ordinal in range(20):
                        store.record(cid, 'validation', ordinal,
                                     outcome(member, ordinal, 10 if member == 0 else 20-20*index))
                store.flush()
                store.set('fineTunePrograms', {'same': dict(encounterId=19,
                    parent={'candidateId': ids[0]}, points={'6500': {'candidateId': ids[1]}})})
                result = portfolio.portfolio_tune_comparisons(store, 19, cache=cache)
                self.assertEqual(result, portfolio.portfolio_tune_comparisons(store, 19))
                results.append(result)
            self.assertNotEqual(*results)
        finally:
            for store in stores:
                store.db.commit()
                close_store(store)


if __name__ == '__main__':
    unittest.main(verbosity=2)
