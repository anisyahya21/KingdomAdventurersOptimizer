"""Check conservation/fairness of already-authorised work under candidate-affine batching."""
import unittest
from strategy_dispatch import batch_size, take_affine, reserved_keys


class BatchDispatchTests(unittest.TestCase):
    def test_conserves_work_and_other_candidates_order(self):
        first = ('a', 'validation', 0)
        order = [('b', 'validation', 0), ('a', 'validation', 1),
                 ('c', 'discovery', 0), ('a', 'validation', 2),
                 ('a', 'discovery', 0), ('b', 'validation', 1)]
        original = [first] + order[:]
        batch = take_affine(order, first, set(), 2)
        self.assertEqual(batch, [first, ('a', 'validation', 1)])
        self.assertCountEqual(batch + order, original)
        self.assertEqual([k for k in order if k[0] != 'a'],
                         [k for k in original if k[0] != 'a'])

    def test_never_steals_reserved_seeds(self):
        first = ('a', 'validation', 2)
        held = ('a', 'validation', 3)
        order = [held, ('a', 'validation', 4)]
        self.assertEqual(take_affine(order, first, {held}, 8),
                         [first, ('a', 'validation', 4)])
        self.assertEqual(order, [held])

    def test_all_batch_members_are_reserved(self):
        keys = [('a', 'validation', i) for i in range(4)]
        self.assertEqual(reserved_keys([dict(seeds=keys)]), set(keys))
        self.assertEqual(reserved_keys([dict(cid='b', phase='discovery', ordinal=0)]),
                         {('b', 'discovery', 0)})

    def test_slow_fights_use_single_seed_fast_fights_are_bounded(self):
        self.assertEqual(batch_size(10), 1)
        self.assertEqual(batch_size(.001), 8)
        self.assertEqual(batch_size(.05), 4)
        self.assertEqual(batch_size(float('nan')), 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
