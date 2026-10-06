"""Check the bounded fair ready window and the discovery backlog it feeds."""
import unittest
from strategy_dispatch import (DISCOVERY, VALIDATION, PHASE_PATTERN, discovery_backlog,
                               ready_window, evidence_refresh_delay)


class CountingReserved(set):
    """A reserved set that counts membership tests, to prove the scan is output-bounded."""

    def __init__(self, *args):
        super().__init__(*args)
        self.checks = 0

    def __contains__(self, key):
        self.checks += 1
        return super().__contains__(key)


def drain(limits, ordinals, reserved, maximum, focus_ids=None):
    """Feed each window back as a cursor and mark its seeds inflight, until the chain runs dry."""
    window = []
    cursors = None
    for _ in range(10000):
        batch, cursors = ready_window(limits, ordinals, reserved, focus_ids=focus_ids,
                                      maximum=maximum, cursors=cursors)
        if not batch:
            break
        window.extend(batch)
        reserved = set(reserved) | set(batch)
    return window, cursors


class ReadyWindowTests(unittest.TestCase):
    def test_evidence_work_budget_bounds_learning_latency(self):
        self.assertEqual(evidence_refresh_delay(0.01), 1.0)
        self.assertEqual(evidence_refresh_delay(0.5), 4.5)
        self.assertEqual(evidence_refresh_delay(4.0), 5.0)
        self.assertEqual(evidence_refresh_delay(0.5, minimum=30), 30)

    def test_one_discovery_to_three_validation(self):
        window, _ = ready_window({'a': (1000, 1000)}, {}, set(), maximum=16)
        self.assertEqual(len(window), 16)
        self.assertEqual([seed[1] for seed in window],
                         [PHASE_PATTERN[i % len(PHASE_PATTERN)] for i in range(16)])
        self.assertEqual(sum(1 for seed in window if seed[1] == DISCOVERY), 4)
        self.assertEqual(sum(1 for seed in window if seed[1] == VALIDATION), 12)

    def test_falls_back_to_the_available_phase_without_idling(self):
        only_discovery, _ = ready_window({'d': (4, 0)}, {}, set(), maximum=8)
        self.assertEqual([seed[1] for seed in only_discovery], [DISCOVERY]*4)
        only_validation, _ = ready_window({'v': (0, 3)}, {}, set(), maximum=8)
        self.assertEqual([seed[1] for seed in only_validation], [VALIDATION]*3)
        window, _ = ready_window({'d': (2, 0), 'v': (0, 20)}, {}, set(), maximum=10)
        self.assertEqual(len(window), 10)
        self.assertEqual([seed[1] for seed in window[:5]],
                         [DISCOVERY, VALIDATION, VALIDATION, VALIDATION, DISCOVERY])
        self.assertTrue(all(seed[1] == VALIDATION for seed in window[5:]))

    def test_skips_completed_and_reserved_seeds(self):
        limits = {'a': (10, 4)}
        ordinals = {('a', DISCOVERY): 2, ('a', VALIDATION): 1}
        reserved = {('a', DISCOVERY, 3), ('a', VALIDATION, 2)}
        window, _ = ready_window(limits, ordinals, reserved, maximum=100)
        self.assertEqual([seed[2] for seed in window if seed[1] == DISCOVERY],
                         [2, 4, 5, 6, 7, 8, 9])
        self.assertEqual([seed[2] for seed in window if seed[1] == VALIDATION], [1, 3])
        self.assertFalse(any(seed in reserved for seed in window))

    def test_focus_narrows_population_and_backlog(self):
        limits = {'here': (3, 3), 'elsewhere': (50, 50)}
        window, _ = ready_window(limits, {}, set(), focus_ids={'here'}, maximum=100)
        self.assertEqual({seed[0] for seed in window}, {'here'})
        self.assertEqual(len(window), 6)
        self.assertEqual(discovery_backlog(limits, {}, focus_ids={'here'}), 3)
        self.assertEqual(discovery_backlog(limits, {}), 53)

    def test_never_exceeds_limits_or_duplicates(self):
        limits = {'a': (5, 3), 'b': (2, 4)}
        window, _ = ready_window(limits, {}, set(), maximum=1000)
        self.assertEqual(len(window), len(set(window)))
        for cid, phase, ordinal in window:
            self.assertLess(ordinal, limits[cid][0 if phase == DISCOVERY else 1])
        expected = [(cid, phase, ordinal) for cid, (dlimit, vlimit) in limits.items()
                    for phase, limit in ((DISCOVERY, dlimit), (VALIDATION, vlimit))
                    for ordinal in range(limit)]
        self.assertEqual(sorted(window), sorted(expected))

    def test_output_is_bounded_by_maximum(self):
        limits = {'c%d' % i: (7, 7) for i in range(40)}
        window, _ = ready_window(limits, {}, set(), maximum=256)
        self.assertEqual(len(window), 256)

    def test_rotation_advances_past_unreached_candidates(self):
        limits = {'c%d' % i: (10**6, 0) for i in range(288)}
        first, cursors = ready_window(limits, {}, set(), maximum=144)
        second, _ = ready_window(limits, {}, set(), maximum=144, cursors=cursors)
        self.assertEqual(len(first), 144)
        self.assertEqual(len(second), 144)
        self.assertEqual({seed[0] for seed in first} & {seed[0] for seed in second}, set())
        self.assertEqual(len({seed[0] for seed in first} | {seed[0] for seed in second}), 288)

    def test_millions_of_ordinals_stay_structurally_bounded(self):
        limits = {'big': (10**7, 10**7)}
        reserved = CountingReserved()
        window, _ = ready_window(limits, {}, reserved, maximum=5)
        self.assertEqual(len(window), 5)
        self.assertLess(max(seed[2] for seed in window), 10)
        self.assertLessEqual(reserved.checks, 2*5)
        blocked = CountingReserved(('big', DISCOVERY, ordinal) for ordinal in range(1000))
        window, _ = ready_window(limits, {}, blocked, maximum=1)
        self.assertEqual(window, [('big', DISCOVERY, 1000)])
        self.assertLessEqual(blocked.checks, 1001)

    def test_mixed_paired_counts_drain_exactly_once(self):
        window, _ = drain({'a': (2, 4), 'b': (1, 2)}, {}, set(), 1000)
        self.assertEqual(len(window), 9)
        self.assertEqual(len(set(window)), 9)
        self.assertEqual(sum(1 for seed in window if seed[1] == DISCOVERY), 3)
        self.assertEqual(sum(1 for seed in window if seed[1] == VALIDATION), 6)

    def test_repeated_small_windows_never_repeat_a_seed(self):
        window, _ = drain({'a': (20, 20), 'b': (20, 20), 'c': (20, 20)}, {}, set(), 7)
        self.assertEqual(len(window), 120)
        self.assertEqual(len(set(window)), 120)

    def test_is_deterministic(self):
        limits = {'a': (4, 4), 'b': (4, 4)}
        self.assertEqual(ready_window(limits, {}, set(), maximum=9),
                         ready_window(limits, {}, set(), maximum=9))

    def test_discarded_window_does_not_skip_unsubmitted_seeds(self):
        limits = {'a': (20, 20)}
        first, cursors = ready_window(limits, {}, set(), maximum=8)
        rebuilt, cursors = ready_window(limits, {}, set(), maximum=8, cursors=cursors)
        self.assertEqual(first, rebuilt)
        submitted = set(first[:2])
        rebuilt, _ = ready_window(limits, {}, submitted, maximum=8, cursors=cursors)
        self.assertFalse(submitted.intersection(rebuilt))
        self.assertTrue(set(first[2:]).issubset(rebuilt))


class DiscoveryBacklogTests(unittest.TestCase):
    def test_counts_unfinished_including_inflight(self):
        limits = {'a': (5, 100), 'b': (2, 0)}
        self.assertEqual(discovery_backlog(limits, {('a', DISCOVERY): 1}), 4+2)
        self.assertEqual(discovery_backlog(limits, {('a', DISCOVERY): 0},
                                           focus_ids={'a'}), 5)
        self.assertEqual(discovery_backlog(limits, {}, focus_ids=set()), 0)

    def test_never_negative_when_authorisation_shrinks(self):
        self.assertEqual(discovery_backlog({'a': (1, 0)}, {('a', DISCOVERY): 4}), 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
