"""Fine-tuning uncertainty: readiness must rest on paired comparability, not a run count.

The two defects this pins are the ones the optimizer audit recorded before trusting automatic
boundaries:

  * readiness accepted a point whose own run count had reached the floor and whose `viable` verdict was
    set, even when only a handful of seeds were paired with the frozen parent or the evidence itself
    was marked non-comparable - so a bracket could be drawn from an incomparable point.
  * a point whose bank had fully drained but whose paired interval was still undecided (or never
    comparable) was neither judged nor extended, so the program stalled with no new work authorised.

The corrected rules, and the guards that keep the correction from becoming a new runaway, are checked
here without running a battle:

  * a judged point needs a comparable, complete, paired-observed bank and an actual verdict;
  * an unjudged point whose bank has drained (runs >= bank) and is still below `maxBank` is extended;
  * a measured bank still in flight (runs < bank) and a bank already at `maxBank` are never grown;
  * exhaustion is never reported as a resolved boundary.

Deterministic and store-free: it exercises `strategy_finetune.plan` and the readiness helper directly.

    python check_finetune_uncertainty.py
"""
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import strategy_finetune as finetune  # noqa: E402

MINIMUM = finetune.MIN_POINT_SEEDS
MAX_BANK = finetune.MAX_POINT_BANK


def paired(n, mean=0.0, lower=0.0, upper=0.0):
    """A paired-delta reading as `point_evidence` supplies it (only `n` is read by readiness)."""
    return dict(n=n, mean=mean, se=0.0, t=None, lower=lower, upper=upper)


def evidence(runs, bank, *, viable, comparable, n, unresolved=0):
    """One point's evidence in the shape `strategy_finetune.point_evidence` produces."""
    return dict(runs=runs, resolved=runs - unresolved, wins=0, losses=0, unresolved=unresolved,
                winRate=None, winInterval=None, meanEarned=None, earnedSamples=0, earnedSE=0.,
                meanPotential=None, potentialSamples=0, chestMean=None, chestSE=None, chestMin=None,
                chestMax=None, paired=paired(n), viable=viable, ready=runs >= MINIMUM,
                comparable=comparable, bank=bank, value=6500, candidateId='b' * 64, effective=6500)


def program(targets=(6500,)):
    parent = dict(candidateId='a' * 64, label='Baseline', encounterId=19)
    return finetune.new_program(parent=parent, axis='mp', unit='Ninja', baseline=7149,
                                encounter_id=19, targets=targets, bounds=(1, 30000))


class ReadinessTests(unittest.TestCase):
    def test_run_count_alone_is_not_readiness(self):
        # 128 own runs, five paired seeds, visibly non-comparable: the run count must not let it be
        # judged, so it can never anchor or extend a bracket.
        thin = evidence(128, 128, viable=False, comparable=False, n=5)
        self.assertFalse(finetune.measurement_ready(thin, MINIMUM))
        self.assertEqual(finetune._ready_measurements({6500: thin}, MINIMUM), {})

    def test_explicit_non_comparable_outranks_a_sufficient_pairing(self):
        # Even with a full paired overlap, an evidence-level comparable=False is honored.
        marked = evidence(128, 128, viable=True, comparable=False, n=128)
        self.assertTrue(finetune.measurement_ready(marked, MINIMUM))
        self.assertEqual(finetune._ready_measurements({6500: marked}, MINIMUM), {})

    def test_an_unresolved_observation_is_not_ready(self):
        censored = evidence(128, 128, viable=True, comparable=True, n=128, unresolved=1)
        self.assertFalse(finetune.measurement_ready(censored, MINIMUM))
        self.assertEqual(finetune._ready_measurements({6500: censored}, MINIMUM), {})

    def test_conclusive_paired_verdict_is_ready(self):
        good = evidence(128, 128, viable=True, comparable=True, n=128)
        failing = evidence(128, 128, viable=False, comparable=True, n=128)
        self.assertTrue(finetune.measurement_ready(good, MINIMUM))
        self.assertEqual(sorted(finetune._ready_measurements({6500: good, 7000: failing}, MINIMUM)),
                         [6500, 7000])


class PlanUncertaintyTests(unittest.TestCase):
    def test_historical_gaps_do_not_look_like_inflight_work(self):
        prog = program()
        measured = evidence(512, 4096, viable=None, comparable=False, n=90)
        measured['completedBank'] = 4096
        self.assertEqual(finetune.plan(prog, {6500: measured})['extend'],
                         [dict(value=6500, bank=8192)])

    def test_legacy_default_upgrade_is_idempotent_and_auditable(self):
        prog = program()
        prog.pop('depthBudgetVersion')
        prog['budget']['maxBank'] = 512
        self.assertTrue(finetune.upgrade_depth_budget(prog))
        self.assertEqual(prog['previousDepthBudget']['maxBank'], 512)
        self.assertEqual(prog['budget']['maxBank'], MAX_BANK)
        self.assertFalse(finetune.upgrade_depth_budget(prog))

    def test_new_explicit_budget_is_preserved(self):
        prog = program()
        prog['budget']['maxBank'] = 512
        self.assertFalse(finetune.upgrade_depth_budget(prog))
        self.assertEqual(prog['budget']['maxBank'], 512)

    def test_budget_exhaustion_reopens_but_resolved_boundary_does_not(self):
        for resolved in (False, True):
            prog = program()
            prog.pop('depthBudgetVersion')
            prog['budget']['maxBank'] = 512
            prog['status'] = 'done'
            prog['schedule']['budgetExhausted'] = True
            if resolved:
                prog['schedule']['resolvedBoundary'] = dict(low=6500, high=6501)
            finetune.upgrade_depth_budget(prog)
            self.assertEqual(prog['status'], 'done' if resolved else 'active')

    def test_incomparable_point_cannot_form_a_bracket(self):
        # The audit's first reproduction: 128 runs, five paired, comparable=False, viable=False.
        prog = program()
        prog['points']['6500'] = dict(value=6500, candidateId='b' * 64, bank=128)
        thin = evidence(128, 128, viable=False, comparable=False, n=5)
        actions = finetune.plan(prog, {6500: thin}, bounds=(1, 30000))
        self.assertEqual(actions['readyValues'], [])
        self.assertIsNone(actions['resolved'])
        self.assertFalse([step for step in actions['creates'] if step['reason'] == 'bracket'])
        self.assertIsNone(finetune.interaction_plan(prog, {6500: thin},
                                                    dict(axis='def', baseline=353, step=18)))

    def test_drained_inconclusive_point_is_extended_not_stalled(self):
        # The audit's second reproduction: 128 runs, bank 128, viable=None -> no extensions, no points.
        prog = program()
        prog['points']['6500'] = dict(value=6500, candidateId='b' * 64, bank=128)
        undecided = evidence(128, 128, viable=None, comparable=False, n=20)
        actions = finetune.plan(prog, {6500: undecided}, bounds=(1, 30000))
        self.assertEqual(actions['readyValues'], [])
        self.assertEqual([step['value'] for step in actions['extend']], [6500])
        self.assertGreater(actions['extend'][0]['bank'], 128)
        self.assertLessEqual(actions['extend'][0]['bank'], prog['budget']['maxBank'])
        self.assertIsNone(actions['resolved'])

    def test_drained_insufficiently_comparable_point_is_extended(self):
        prog = program()
        prog['points']['6500'] = dict(value=6500, candidateId='b' * 64, bank=128)
        thin = evidence(128, 128, viable=False, comparable=False, n=5)
        actions = finetune.plan(prog, {6500: thin}, bounds=(1, 30000))
        self.assertEqual([step['value'] for step in actions['extend']], [6500])
        self.assertGreater(actions['extend'][0]['bank'], 128)

    def test_conclusive_point_is_not_extended_and_can_bracket(self):
        prog = program()
        prog['points']['6500'] = dict(value=6500, candidateId='b' * 64, bank=128)
        prog['points']['7000'] = dict(value=7000, candidateId='c' * 64, bank=128)
        working = evidence(128, 128, viable=True, comparable=True, n=128)
        failing = evidence(128, 128, viable=False, comparable=True, n=128)
        actions = finetune.plan(prog, {6500: working, 7000: failing}, bounds=(1, 30000))
        self.assertEqual(sorted(actions['readyValues']), [6500, 7000])
        self.assertEqual(actions['extend'], [])
        self.assertEqual([step['reason'] for step in actions['creates']], ['bracket'])
        self.assertEqual(actions['bracket'],
                         dict(low=7000, high=7149, resolution=149, bracketed=True))
        self.assertIsNone(actions['resolved'])

    def test_pending_measured_bank_is_not_grown(self):
        # A measured point whose bank is still in flight (runs < bank) is left to resolve; the planner
        # must not double an authorised extension on every pass.
        prog = program()
        prog['points']['6500'] = dict(value=6500, candidateId='b' * 64, bank=256)
        pending = evidence(128, 256, viable=None, comparable=False, n=10)
        actions = finetune.plan(prog, {6500: pending}, bounds=(1, 30000))
        self.assertEqual(actions['extend'], [])
        self.assertEqual(actions['readyValues'], [])

    def test_pending_provisional_bank_is_not_repeatedly_doubled(self):
        prog = program()
        for bank in (128, 256):
            pending = evidence(40, bank, viable=None, comparable=False, n=5)
            self.assertEqual(finetune.plan(prog, {6500: pending})['extend'], [])

    def test_small_bank_can_be_raised_to_readiness_floor_once(self):
        prog = program()
        pending = evidence(10, 32, viable=None, comparable=False, n=5)
        self.assertEqual(finetune.plan(prog, {6500: pending})['extend'],
                         [dict(value=6500, bank=MINIMUM)])
        pending['bank'] = MINIMUM
        self.assertEqual(finetune.plan(prog, {6500: pending})['extend'], [])

    def test_uncertainty_at_the_max_bank_is_never_resolved(self):
        prog = program()
        prog['points']['6500'] = dict(value=6500, candidateId='b' * 64, bank=MAX_BANK)
        capped = evidence(MAX_BANK, MAX_BANK, viable=None, comparable=False, n=9)
        actions = finetune.plan(prog, {6500: capped}, bounds=(1, 30000))
        self.assertEqual(actions['extend'], [])
        self.assertEqual(actions['readyValues'], [])
        self.assertIsNone(actions['resolved'])
        self.assertNotEqual(actions['status'], 'done')


if __name__ == '__main__':
    unittest.main(verbosity=2)
