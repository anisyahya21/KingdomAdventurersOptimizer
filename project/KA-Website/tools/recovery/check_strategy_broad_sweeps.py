"""Broad-stat sweeps: the coarse ladder, its bounded schedule, and the honest stop rule, store-free.

The audit found the automatic programs had no coarse target at all: an axis other than MP was swept
only by the ~5 %-of-baseline adaptive step, so "does MP 7149 still work at 1000, 2000, 4000 or the
wall?" was never asked. This checker pins the correction without running a battle:

  * `broad_targets` is deterministic, bounded, excludes the baseline, keeps the legal walls, the
    1000/2000/4000 rungs when they are legal and useful interior fractions, and invents no wall when
    the bounds are missing;
  * a program created with no explicit target defaults to that ladder, while an explicit target list
    is honored exactly;
  * the automatic children budget funds a full ladder plus refinement while the per-pass creation
    limit is unchanged, so the ladder is spent across passes, never as one burst;
  * `plan` finishes broad coverage before it concludes any boundary, tightens *every* distinct
    working/failing transition (coarsest gap first, not only the tightest) and probes the interior
    around the best measured mean when no transition has appeared (the non-monotone case);
  * no created point escapes the axis's legal bounds, an untested interval is never called resolved,
    and uncertainty only ever doubles an exhausted bank;
  * the legacy four-child/no-target automatic program is migrated (ladder added, default ceiling
    raised, custom ceiling and measured points preserved, completed program reopened), and a build
    with 64 resolved runs is admissible as an automatic parent (promotion is not confidence).

Two older guards are deliberately superseded and their checks conflict, not silently break:
`check_optimizer_autotune` pins an automatic child total of half the base fine-tune floor and the MP
program's first point at ~6500, and `check_finetune_uncertainty` pins one bracket create per pass.
A full coarse ladder cannot fit a four-child ceiling or a half-floor total, a ladder that asks the
legal wall minimum makes 5 the lowest MP point, and "tighten *every* transition" spends more than one
create. Those expectations are reported as old-contract conflicts rather than weakened here.

    python check_strategy_broad_sweeps.py
"""
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import strategy_finetune as finetune  # noqa: E402
from strategy_optimizer import FINETUNE_BASE_CHILDREN  # noqa: E402

MP_BOUNDS = (5, 7986)
MINIMUM = finetune.MIN_POINT_SEEDS


def evidence(value, *, viable, mean=None, runs=128, n=128, bank=None):
    """One point's evidence in the shape `strategy_finetune.point_evidence` produces."""
    bank = runs if bank is None else bank
    return dict(runs=runs, resolved=runs, wins=0, losses=0, unresolved=0, winRate=None,
                winInterval=None, meanEarned=mean, earnedSamples=(runs if mean is not None else 0),
                earnedSE=0., meanPotential=None, potentialSamples=0, chestMean=None, chestSE=None,
                chestMin=None, chestMax=None,
                paired=dict(n=n, mean=0.0, se=0.0, t=None, lower=0.0, upper=0.0),
                viable=viable, ready=runs >= MINIMUM, comparable=True, bank=bank, value=value,
                candidateId='b' * 64, effective=value)


def program(*, baseline=7149, bounds=MP_BOUNDS, targets=None, direction=None, budget=None,
            axis='mp'):
    """A program record; `targets=None` means the caller gave no explicit target."""
    parent = dict(candidateId='a' * 64, label='Baseline', encounterId=19)
    return finetune.new_program(parent=parent, axis=axis, unit='Ninja', baseline=baseline,
                                encounter_id=19, targets=(() if targets is None else targets),
                                direction=direction, bounds=bounds, budget=budget)


def place(program, measurements, action, viable, mean=None):
    """Mimic the optimiser: register one created point and record its verdict."""
    value = int(action['value'])
    program.setdefault('points', {})[str(value)] = dict(value=value, candidateId=f'c{value}',
                                                        bank=128)
    program['counters']['children'] = finetune.children_used(program) + 1
    program['counters']['points'] = len(program['points'])
    measurements[value] = evidence(value, viable=viable, mean=mean)


class BroadTargetTests(unittest.TestCase):
    BASELINES = (7149, 200, 5, 40, 4000, 19287, 900, 6600)
    BOUNDS = ((5, 7986), (6, 5766), (3, 4976), (5, 4780), (2, 7134), (55, 19287), None,
              (30, 400))

    def test_ladder_is_deterministic_sorted_bounded_and_excludes_the_baseline(self):
        for baseline in self.BASELINES:
            for bounds in self.BOUNDS:
                ladder = finetune.broad_targets(baseline, bounds)
                self.assertEqual(ladder, finetune.broad_targets(baseline, bounds),
                                 'the ladder must be deterministic')
                self.assertEqual(list(ladder), sorted(set(ladder)), 'sorted and duplicate-free')
                self.assertLessEqual(len(ladder), finetune.BROAD_TARGETS_MAX)
                self.assertNotIn(baseline, ladder, 'the baseline is the anchor, not a target')
                self.assertTrue(all(isinstance(value, int) and value >= 0 for value in ladder))
                if bounds:
                    low, high = min(bounds), max(bounds)
                    self.assertTrue(all(low <= value <= high for value in ladder),
                                    f'{ladder} escaped {bounds}')

    def test_legal_walls_rungs_and_interior_fractions_are_present(self):
        ladder = finetune.broad_targets(7149, MP_BOUNDS)
        self.assertIn(MP_BOUNDS[0], ladder, 'the read minimum is a legal wall')
        self.assertIn(MP_BOUNDS[1], ladder, 'the read maximum is a legal wall')
        for rung in finetune.BROAD_TARGET_RUNGS:
            self.assertIn(rung, ladder)
        interior = [value for value in ladder if value not in MP_BOUNDS
                    and value not in finetune.BROAD_TARGET_RUNGS]
        self.assertGreaterEqual(len(interior), 2, 'the ladder needs interior fractions')
        for value in interior:
            self.assertTrue(MP_BOUNDS[0] < value < MP_BOUNDS[1])
        # The motivating question survives as a ladder point: MP 7149 -> ~6506 ("around 6500").
        reduction = round(7149 * (1 - finetune.AUTO_TUNE_FIRST_REDUCTION))
        self.assertIn(reduction, ladder)
        self.assertLess(abs(reduction - 6500), 50)

    def test_illegal_rungs_are_dropped_and_no_wall_is_invented(self):
        narrow = finetune.broad_targets(200, (30, 400))
        self.assertNotIn(1000, narrow)
        self.assertNotIn(2000, narrow)
        self.assertNotIn(4000, narrow)
        self.assertIn(30, narrow)
        self.assertIn(400, narrow)
        unbounded = finetune.broad_targets(7149, None)
        self.assertEqual(unbounded, tuple(sorted(finetune.BROAD_TARGET_RUNGS)))

    def test_rounded_interior_duplicates_are_not_padded_in(self):
        # A baseline whose half rounds onto the 1000 rung and whose double rounds onto 4000: the
        # near-duplicates are dropped, the required rungs stay.
        ladder = finetune.broad_targets(1990, MP_BOUNDS)
        self.assertIn(1000, ladder)
        self.assertIn(4000, ladder)
        self.assertNotIn(round(1990 * 0.5), ladder, 'a rounded near-duplicate must not pad it')
        self.assertNotIn(round(1990 * 2.0), ladder)

    def test_direction_keeps_only_the_asked_side(self):
        down = finetune.broad_targets(7149, MP_BOUNDS, direction='down')
        self.assertTrue(down and all(value < 7149 for value in down))
        up = finetune.broad_targets(200, MP_BOUNDS, direction='up')
        self.assertTrue(up and all(value > 200 for value in up))


class ProgramDefaultTests(unittest.TestCase):
    def test_no_target_defaults_to_the_broad_ladder(self):
        prog = program()
        self.assertEqual(prog['targets'], list(finetune.broad_targets(7149, MP_BOUNDS)))
        self.assertTrue(prog['targets'])

    def test_explicit_target_is_honored_without_the_ladder(self):
        prog = program(targets=(6500,))
        self.assertEqual(prog['targets'], [6500])
        self.assertEqual(prog['direction'], 'down')

    def test_auto_budget_funds_ladder_and_refinement_but_not_a_burst(self):
        budget = finetune.auto_budget()
        self.assertEqual(budget['maxChildren'], finetune.AUTO_TUNE_MAX_CHILDREN_PER_PROGRAM)
        self.assertEqual(budget['maxChildren'],
                         finetune.BROAD_TARGETS_MAX + finetune.AUTO_TUNE_REFINEMENT_CHILDREN)
        self.assertGreater(budget['maxChildren'],
                           finetune.LEGACY_AUTO_TUNE_MAX_CHILDREN_PER_PROGRAM)
        self.assertGreaterEqual(budget['maxChildren'], len(finetune.broad_targets(7149, MP_BOUNDS)))
        self.assertEqual(budget['maxNewPoints'], finetune.DEFAULT_BUDGET['maxNewPoints'])
        self.assertLessEqual(budget['maxNewPoints'], 2, 'no per-pass burst')
        self.assertEqual(finetune.AUTO_TUNE_ENCOUNTER_CHILDREN,
                         finetune.AUTO_TUNE_MAX_PROGRAMS_PER_ENCOUNTER * finetune.AUTO_TUNE_MAX_CHILDREN_PER_PROGRAM)
        self.assertLessEqual(finetune.AUTO_TUNE_MAX_PROGRAMS_PER_ENCOUNTER * budget['maxNewPoints'],
                             FINETUNE_BASE_CHILDREN, 'per-pass admissions remain bounded')


class ScheduleTests(unittest.TestCase):
    def test_coarse_breadth_is_placed_without_a_burst_and_before_any_resolution(self):
        prog = program()
        ladder = list(prog['targets'])
        measurements = {}
        placed, per_pass = set(), []
        actions = None
        for _ in range(20):
            actions = finetune.plan(prog, measurements, MP_BOUNDS)
            per_pass.append(len(actions['creates']))
            self.assertLessEqual(len(actions['creates']), prog['budget']['maxNewPoints'])
            if [value for value in ladder if value not in placed]:
                self.assertNotEqual(actions['status'], 'done',
                                    'broad coverage must finish before a boundary is concluded')
            for action in actions['creates']:
                place(prog, measurements, action, viable=True)
                placed.add(int(action['value']))
            if not actions['creates']:
                break
        self.assertEqual(set(ladder) - placed, set(), 'the whole ladder must be created')
        self.assertTrue(all(count <= 2 for count in per_pass))

    def test_narrowing_is_deterministic_and_reaches_a_resolution(self):
        prog = program(targets=(1000,))
        prog['points']['1000'] = dict(value=1000, candidateId='c1000', bank=128)
        measurements = {1000: evidence(1000, viable=False)}
        seen = []
        actions = None
        for _ in range(20):
            actions = finetune.plan(prog, measurements, MP_BOUNDS)
            self.assertEqual(actions, finetune.plan(prog, measurements, MP_BOUNDS),
                             'planning the same state twice must agree')
            if actions['status'] == 'done':
                break
            bracketed = [action['value'] for action in actions['creates']
                         if action['reason'] == 'bracket']
            self.assertTrue(bracketed, 'a live bracket must be bisected')
            for action in actions['creates']:
                place(prog, measurements, action, viable=False)
                seen.append(int(action['value']))
        self.assertEqual(actions['status'], 'done')
        self.assertIsNotNone(actions['resolved'])
        self.assertLessEqual(actions['resolved']['resolution'], prog['resolution'])
        self.assertEqual(len(seen), len(set(seen)), 'no point is ever asked for twice')

    def test_every_distinct_transition_is_tightened_not_only_the_tightest(self):
        prog = program(targets=(1000, 2000, 4000, 5000))
        for value in (1000, 2000, 4000, 5000):
            prog['points'][str(value)] = dict(value=value, candidateId=f'c{value}', bank=128)
        measurements = {1000: evidence(1000, viable=True), 2000: evidence(2000, viable=False),
                        4000: evidence(4000, viable=True), 5000: evidence(5000, viable=False)}
        actions = finetune.plan(prog, measurements, MP_BOUNDS)
        created = [action['value'] for action in actions['creates']]
        self.assertEqual([action['reason'] for action in actions['creates']], ['bracket', 'bracket'])
        self.assertEqual(set(created), {6074, 3000}, 'the two widest transitions are bracketed')
        self.assertNotIn(1500, created, 'the tightest transitions are not the only ones refined')
        self.assertNotIn(4500, created)
        self.assertEqual(actions['bracket']['low'], 1000)
        self.assertEqual(actions['bracket']['high'], 2000)
        self.assertEqual(len(actions['transitions']), 4)

    def test_an_unsettled_target_blocks_a_resolved_boundary(self):
        prog = program(targets=(5000,))
        for value in (6999, 7000):
            prog['points'][str(value)] = dict(value=value, candidateId=f'c{value}', bank=128)
        measurements = {6999: evidence(6999, viable=True), 7000: evidence(7000, viable=False)}
        actions = finetune.plan(prog, measurements, MP_BOUNDS)
        self.assertEqual(actions['transitions'][0]['resolution'], 1)
        self.assertTrue(actions['transitions'][0]['resolved'])
        self.assertFalse(actions['coverageComplete'])
        self.assertNotEqual(actions['status'], 'done')
        self.assertIsNone(actions['resolved'])

    def test_interior_around_the_best_mean_is_probed_when_nothing_flips(self):
        prog = program(baseline=6000, bounds=MP_BOUNDS, targets=(1000, 2000, 4000))
        for value in (1000, 2000, 4000):
            prog['points'][str(value)] = dict(value=value, candidateId=f'c{value}', bank=128)
        measurements = {1000: evidence(1000, viable=True, mean=5.0),
                        2000: evidence(2000, viable=True, mean=9.0),
                        4000: evidence(4000, viable=True, mean=4.0)}
        actions = finetune.plan(prog, measurements, MP_BOUNDS)
        self.assertFalse(actions['transitions'])
        interior = [action['value'] for action in actions['creates']
                    if action['reason'] == 'interior']
        self.assertTrue(interior, 'a non-monotone peak must be probed inside the run')
        self.assertIn(interior[0], (1500, 3000),
                      'the probe belongs between the best mean and a neighbour')

    def test_no_created_point_escapes_the_legal_bounds(self):
        prog = program(baseline=150, bounds=(100, 200), targets=(100, 200))
        for value in (100, 200):
            prog['points'][str(value)] = dict(value=value, candidateId=f'c{value}', bank=128)
        measurements = {100: evidence(100, viable=True, mean=3.0),
                        200: evidence(200, viable=True, mean=1.0)}
        for _ in range(6):
            actions = finetune.plan(prog, measurements, (100, 200))
            for action in actions['creates']:
                self.assertTrue(100 <= action['value'] <= 200, 'a point left the legal range')
                place(prog, measurements, action, viable=True)
            if not actions['creates']:
                break

    def test_uncertainty_doubles_a_drained_bank_and_never_claims_resolution(self):
        prog = program(targets=(6500,))
        prog['budget']['maxBank'] = 4096
        undecided = evidence(6500, viable=None, mean=None, runs=1024, bank=1024)
        undecided['comparable'] = False
        actions = finetune.plan(prog, {6500: undecided}, MP_BOUNDS)
        self.assertEqual(actions['extend'], [dict(value=6500, bank=2048)])
        self.assertIsNone(actions['resolved'])


class PromotionTests(unittest.TestCase):
    def record(self, resolved, **over):
        base = dict(candidateId='x' * 64, label='Build', meanEarned=2.0, resolved=resolved,
                    unresolved=0, wins=1)
        base.update(over)
        return base

    def test_promotion_floor_is_the_validation_ceiling_not_the_point_floor(self):
        self.assertEqual(finetune.AUTO_TUNE_MIN_RESOLVED, 64)
        self.assertLess(finetune.AUTO_TUNE_MIN_RESOLVED, MINIMUM)
        chosen, reason = finetune.select_parent([self.record(64)])
        self.assertIsNone(reason)
        self.assertEqual(chosen['resolved'], 64)
        chosen, reason = finetune.select_parent([self.record(63)])
        self.assertIsNone(chosen)
        self.assertIn('64 resolved', reason)

    def test_unresolved_or_winless_builds_still_lose(self):
        self.assertIsNone(finetune.select_parent([self.record(64, unresolved=1)])[0])
        self.assertIsNone(finetune.select_parent([self.record(64, wins=0)])[0])


class LegacyMigrationTests(unittest.TestCase):
    def legacy(self, *, budget=None, status='done', explicit=None, axis='atk'):
        prog = program(axis=axis, targets=explicit,
                       budget=budget or dict(finetune.DEFAULT_BUDGET, maxChildren=4))
        prog['targets'] = list(explicit) if explicit else []
        prog['auto'] = True
        prog['status'] = status
        prog['points']['6500'] = dict(value=6500, candidateId='c6500', bank=128)
        prog['schedule']['budgetExhausted'] = True
        prog.pop('broadTargetsVersion', None)
        return prog

    def test_completed_legacy_program_is_reopened_with_the_ladder(self):
        prog = self.legacy()
        self.assertTrue(finetune.upgrade_broad_targets(prog))
        self.assertEqual(prog['status'], 'active')
        self.assertTrue(prog['schedule']['reopenedForBroadSweep'])
        self.assertNotIn('budgetExhausted', prog['schedule'])
        self.assertEqual(prog['targets'], list(finetune.broad_targets(
            prog['baseline']['effective'], prog['bounds'], direction=prog['direction'])))
        self.assertTrue(prog['targets'])
        self.assertEqual(prog['budget']['maxChildren'],
                         finetune.AUTO_TUNE_MAX_CHILDREN_PER_PROGRAM)
        self.assertEqual(prog['previousAutoBudget']['maxChildren'], 4)
        self.assertIn('6500', prog['points'], 'measured points are preserved')
        self.assertFalse(finetune.upgrade_broad_targets(prog), 'idempotent through the marker')

    def test_the_depth_caller_runs_the_migration_too(self):
        prog = self.legacy()
        prog.pop('depthBudgetVersion', None)
        prog['budget']['maxBank'] = 512
        self.assertTrue(finetune.upgrade_depth_budget(prog))
        self.assertTrue(prog['targets'])
        self.assertEqual(prog['budget']['maxBank'], finetune.MAX_POINT_BANK)
        self.assertFalse(finetune.upgrade_depth_budget(prog), 'idempotent on both migrations')

    def test_custom_ceiling_and_explicit_targets_are_preserved(self):
        custom = self.legacy(budget=dict(finetune.DEFAULT_BUDGET, maxChildren=9))
        finetune.upgrade_broad_targets(custom)
        self.assertEqual(custom['budget']['maxChildren'], 9)
        self.assertNotIn('previousAutoBudget', custom)
        explicit = self.legacy(explicit=(6500,))
        self.assertFalse(finetune.upgrade_broad_targets(explicit))
        self.assertEqual(explicit['targets'], [6500])
        self.assertEqual(explicit['status'], 'done')

    def test_a_non_automatic_program_is_untouched(self):
        prog = self.legacy()
        prog['auto'] = False
        self.assertFalse(finetune.upgrade_broad_targets(prog))
        self.assertEqual(prog['targets'], [])
        self.assertEqual(prog['status'], 'done')


if __name__ == '__main__':
    unittest.main(verbosity=2)
