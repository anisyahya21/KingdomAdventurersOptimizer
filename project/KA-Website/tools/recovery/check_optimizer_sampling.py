"""Budget regression checks against analytic variance/precision relationships."""
import unittest

from strategy_sampling import precision_budget, variance_from_totals


class SamplingTests(unittest.TestCase):
    def test_lucky_shallow_leader_does_not_exclude_a_precise_rival(self):
        from strategy_optimizer import focused_exploitation
        common = dict(encounterId=19, comparable=True, winInterval=[.9, 1.])
        records = [dict(candidate='lucky', validationRuns=64, wins=60, earnedMean=50.,
                        earnedVariance=2500., **common),
                   dict(candidate='rival', validationRuns=4096, wins=4000, earnedMean=37.,
                        earnedVariance=625., **common)]
        limits, decisions = focused_exploitation(records,
            {'lucky': (24, 64), 'rival': (24, 4096)}, 19, 48)
        self.assertGreater(decisions['rival']['target'], 4096)
        self.assertGreater(limits['rival'][1], 4096)

    def test_live_allocator_uses_reward_variance_above_old_cap(self):
        from strategy_optimizer import focused_exploit_target
        record = dict(candidate='strong', encounterId=19, validationRuns=4096, wins=4000,
                      comparable=True, earnedMean=37., earnedVariance=625., winInterval=[.9, 1.])
        plan = focused_exploit_target(record, 19, best_mean=37., band_size=1)
        self.assertGreater(plan['target'], 9000)
        self.assertFalse(plan['precisionReached'])
        self.assertNotEqual(plan['tier'], 'capped')
        # A high expected reward with rare wins deserves the same precision target.
        record.update(wins=100, winInterval=[.01, .05])
        self.assertEqual(focused_exploit_target(record, 19, best_mean=37.)['target'], plan['target'])

    def test_long_fight_variance_requires_thousands_more_than_screening(self):
        plan = precision_budget(512, 25 ** 2)
        self.assertGreater(plan['target'], 9000)
        self.assertLess(plan['target'], 10000)
        self.assertFalse(plan['precisionReached'])

    def test_budget_cap_is_not_confirmation(self):
        plan = precision_budget(65536, 1000 ** 2)
        self.assertEqual(plan['target'], 65536)
        self.assertTrue(plan['budgetExhausted'])
        self.assertFalse(plan['precisionReached'])

    def test_more_variance_gets_more_evidence_and_precision_reduces_need(self):
        self.assertGreater(precision_budget(500, 400)['target'],
                           precision_budget(500, 100)['target'])
        self.assertLess(precision_budget(500, 400, half_width=1)['target'],
                        precision_budget(500, 400, half_width=.5)['target'])

    def test_constant_samples_still_need_minimum(self):
        self.assertFalse(precision_budget(2, 0)['precisionReached'])
        self.assertTrue(precision_budget(1024, 0)['precisionReached'])

    def test_missing_variance_is_not_zero_uncertainty(self):
        self.assertFalse(precision_budget(1024, None)['precisionReached'])
        self.assertEqual(precision_budget(1024, None)['target'], 2048)

    def test_variance_matches_known_dataset(self):
        self.assertEqual(variance_from_totals(3, 6, 14), 1.)
        self.assertIsNone(variance_from_totals(1, 2, 4))


if __name__ == '__main__':
    unittest.main(verbosity=2)
