"""High-average families survive peak-based selection without deleting near-miss paths."""
import unittest
from unittest.mock import patch

import strategy_optimizer as optimizer
import strategy_learner as learner
from strategy_mean_parents import mean_parent_pool, MEAN_PARENT_LIMIT
from check_optimizer_finetune import open_store, close_store, base_scenario, outcome
from strategy_optimizer_adapter import stats


def entry(cid, mean=35., n=512, sd=5., region=None, encounter=19):
    return dict(candidate=cid, encounterId=encounter, defeatCount=0, region=region or cid,
                total=dict(n=n, count=n, censored=0, chestCount=n, chestSum=mean*n,
                           chestSum2=mean*mean*n+sd*sd*(n-1)))


class MeanParentTests(unittest.TestCase):
    def test_average_and_evidence_depth_outrank_lucky_peak(self):
        selected = mean_parent_pool([entry('steady'), entry('peak', 4, sd=25),
                                     entry('thin', 45, n=64, sd=100)], limit=1)
        self.assertEqual(selected[0]['candidate'], 'steady')

    def test_diversity_is_bounded_and_order_independent(self):
        rows = [entry(str(i), 40-i, region='same') for i in range(8)]
        rows += [entry('other', 25), entry('elsewhere', 30, encounter=10)]
        chosen = mean_parent_pool(rows, limit=2)
        self.assertEqual(chosen, mean_parent_pool(list(reversed(rows)), limit=2))
        self.assertEqual({r['candidate'] for r in chosen}, {'0', 'other', 'elsewhere'})

    def test_unknown_or_incomplete_is_not_a_mean_champion(self):
        for changes in ({'censored': 1}, {'chestCount': 511}, {'chestSum2': None},
                        {'chestSum2': float('nan')}, {'chestSum2': 0}, {'count': 511}):
            row = entry('bad'); row['total'].update(changes)
            self.assertEqual(mean_parent_pool([row]), [])
        self.assertEqual(mean_parent_pool([entry('thin', n=8)]), [])

    def test_store_preservation_and_mean_source_use_same_pool(self):
        store = open_store()
        try:
            base = base_scenario()
            steady_scenario = dict(base, note='steady')
            steady = store.add(steady_scenario, 'steady', 'mutation', stats(steady_scenario))
            for ordinal in range(64):
                store.record(steady, 'validation', ordinal, outcome(0, ordinal, 35))
            # Four peak-heavy builds outrank steady in every conversion/potential lane.
            peaks = []
            for index in range(4):
                scenario = dict(base, note=f'peak-{index}')
                cid = store.add(scenario, f'peak-{index}', 'mutation', stats(scenario)); peaks.append(cid)
                for ordinal in range(64):
                    store.record(cid, 'validation', ordinal,
                                 outcome(index+1, ordinal, 100+index if ordinal == 0 else 1))
            store.flush()
            self.assertNotIn(steady, store.elite_members(19))
            self.assertIn(steady, store.mean_members(19))
            self.assertLessEqual(len(store.mean_members(19)), MEAN_PARENT_LIMIT)
            # Isolate mean-parent protection from the historical archive.
            store.db.execute('DELETE FROM archive'); store.db.commit()
            with patch.object(optimizer, 'MAX_CANDIDATES_PER_ENCOUNTER', 6):
                for index in range(5):
                    scenario = dict(base, note=f'new-{index}')
                    store.add(scenario, 'new', 'mutation', stats(scenario))
                self.assertIsNotNone(store.scenario(steady))
            diagnostics = {}
            with patch.object(learner, 'LOCAL_SHARE', 0):
                children = optimizer.spawn_children(store, 19, 1, 7, diagnostics, sources=['mean'])
            self.assertEqual(len(children), 1)
            lineage = store.db.execute('SELECT parent,source FROM lineage WHERE candidate=?',
                                       (children[0],)).fetchone()
            self.assertEqual(lineage[0], steady)
            self.assertEqual(lineage[1], 'mean')
            self.assertEqual(diagnostics['meanParentPool'][0]['candidate'], steady)
            self.assertTrue({'potential', 'setup', 'exploration'} <= set(learner.PARENT_SOURCES))
            # New accepted evidence invalidates preservation cache.
            for ordinal in range(64, 764):
                store.record(steady, 'validation', ordinal, outcome(0, ordinal, 0, verdict=2))
                store.record(peaks[0], 'validation', ordinal, outcome(1, ordinal, 5))
            store.flush()
            self.assertNotIn(steady, store.mean_members(19))
        finally:
            close_store(store)


if __name__ == '__main__':
    unittest.main(verbosity=2)
