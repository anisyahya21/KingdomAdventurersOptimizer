"""Cached fine-tune statistics preserve deep evidence and invalidate either changed arm."""
import unittest
from unittest.mock import patch

import strategy_finetune as fine
from strategy_optimizer_adapter import stats
from check_optimizer_finetune import (base_scenario, close_store, make_optimizer,
                                     open_store, outcome)


class StatisticsCacheTests(unittest.TestCase):
    def test_revision_invalidation_and_fresh_bank_metadata(self):
        store = open_store()
        try:
            parent_scenario, child_scenario = base_scenario(7149), base_scenario(6500)
            parent = store.add(parent_scenario, 'parent', 'supplied', stats(parent_scenario))
            child = store.add(child_scenario, 'child', 'probe', stats(child_scenario))
            for ordinal in range(700):
                store.record(parent, 'validation', ordinal, outcome(0, ordinal, 8))
                store.record(child, 'validation', ordinal, outcome(1, ordinal, 7+ordinal % 3))
            store.flush()
            store.set('probeBanks', {child: 1000})
            owner = make_optimizer(19)
            program = dict(parent={'candidateId': parent}, budget={'minSeeds': 100},
                           points={'6500': dict(candidateId=child, effective=6500)})
            def verify():
                reference, measured = owner._finetune_measurements(store, program)
                expected = fine.point_evidence(owner._finetune_rows(store, child), reference,
                    value=6500, candidate_id=child, effective=6500,
                    bank=(store.get('probeBanks') or {})[child], minimum=100)
                expected['completedBank'] = store.next_ordinal(child, 'validation')
                self.assertEqual(measured[6500], expected)
                return measured
            first = verify()
            self.assertEqual(first[6500]['runs'], 700)
            self.assertEqual(len(store.rows(child, 'validation')), 512)
            with patch.object(fine, 'summarise_rows', side_effect=AssertionError('recalculated')):
                self.assertEqual(owner._finetune_measurements(store, program)[1], first)
                store.set('probeBanks', {child: 2000})
                self.assertEqual(owner._finetune_measurements(store, program)[1][6500]['bank'], 2000)
            first[6500]['paired']['lower'] = -99999
            self.assertNotEqual(verify()[6500]['paired']['lower'], -99999)
            parent_series = owner._finetune_series(store, parent)
            parent_series.clear()
            self.assertEqual(len(owner._finetune_series(store, parent)), 700)
            original = fine.summarise_rows
            for cid in (parent, child):
                store.record(cid, 'validation', 700, outcome(2, 700, 30))
                store.flush()
                with patch.object(fine, 'summarise_rows', wraps=original) as reduce:
                    owner._finetune_measurements(store, program)
                    self.assertEqual(reduce.call_count, 1)
                verify()
        finally:
            close_store(store)


if __name__ == '__main__':
    unittest.main(verbosity=2)
