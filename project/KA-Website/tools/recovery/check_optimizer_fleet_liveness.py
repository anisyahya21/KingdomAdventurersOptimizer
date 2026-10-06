"""Regression checks for stale frozen-plan retirement and fair campaign worker shares."""
from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import strategy_encounter_search as search
import strategy_optimizer as optimizer
import strategy_revision


class _FakeCoordinator:
    def __init__(self, *, cohort, compatible):
        self.enabled = True
        self.cohort = cohort
        self.compatible = compatible

    def _has_work(self):
        return True

    def _cohort(self):
        return self.cohort

    def _has_compatible_frozen_work(self, _db):
        return self.compatible


class FleetLivenessTests(unittest.TestCase):
    def test_seventeen_blocked_encounters_do_not_dilute_three_runnable_shares(self):
        coordinators = {
            encounter: _FakeCoordinator(cohort=['stale-plan'], compatible=False)
            for encounter in range(1, 18)
        }
        coordinators.update({
            encounter: _FakeCoordinator(cohort=['current-plan'], compatible=True)
            for encounter in range(18, 21)
        })

        runnable = optimizer._campaign_runnable_encounters(coordinators, object())
        self.assertEqual(runnable, {18, 19, 20})
        self.assertEqual((22 + len(runnable) - 1) // len(runnable), 8)

    def test_stale_plan_waits_for_submitted_battles_then_retires_without_ledger_writes(self):
        experiment_id = 41
        coord = search.Coordinator(path='retire-test.sqlite', revision='current-engine',
                                   encounter=11)
        old = dict(
            experimentId=experiment_id,
            candidateId='old-candidate',
            engineRevision=strategy_revision.LEGACY_KNOWN_GOOD_SCHEDULER_REVISION,
            blocked=True,
            blockedReason='loaded engine revision differs from the frozen plan',
            jobs=[dict(candidateId='old-candidate', seedPair=[1, 2])],
            observedCandidate={},
        )
        coord._set_cohort([old])
        db = sqlite3.connect(':memory:')
        try:
            coord.evaluator = type('Evaluator', (), {
                'pending': {(experiment_id, 'old-candidate', (1, 2)): {'holdout': False}}
            })()
            before = db.total_changes
            self.assertEqual(coord._retire_incompatible_frozen_plans(db), 0)
            self.assertEqual(coord._cohort(), [old])
            self.assertEqual(db.total_changes, before)

            coord.evaluator.pending.clear()
            self.assertEqual(coord._retire_incompatible_frozen_plans(db), 1)
            self.assertEqual(coord._cohort(), [])
            self.assertEqual(db.total_changes, before)
            self.assertEqual(coord.state['retiredFrozenPlans'][0]['experimentId'], experiment_id)
            coord.evaluator = None
            self.assertEqual(coord.report()['progress']['retiredFrozenPlanCount'], 1)
        finally:
            db.close()

    def test_idle_retired_generation_rolls_over_even_without_completed_experiments(self):
        self.assertTrue(optimizer._campaign_should_rollover(dict(
            idle=True, experiments=0, retiredFrozenPlanCount=1)))
        self.assertFalse(optimizer._campaign_should_rollover(dict(
            idle=True, experiments=0, retiredFrozenPlanCount=0)))
        self.assertFalse(optimizer._campaign_should_rollover(dict(
            idle=False, experiments=0, retiredFrozenPlanCount=1)))


if __name__ == '__main__':
    unittest.main(verbosity=2)
