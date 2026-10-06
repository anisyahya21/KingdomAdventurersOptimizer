"""Controlled fine-tuning: the frozen-parent experiment engine, checked without running a battle.

The questions this pins are the ones the optimizer could not previously answer on a build that already
works, and the honesty rules that keep the answer measurable:

  * "MP 7149 -> 6500" is representable and scheduled: the point exists as a probe candidate, the frozen
    parent is banked to the same seed bank, and both carry a run bank of at least the readiness floor.
  * only combat-effective statistics are sweepable - Heart/Love, Gathering and Move are refused, and
    Intelligence only when the unit carries a magic attack skill.
  * a point is provisional until at least 100 runs have resolved on it, and paired comparisons use the
    shared seed ordinals, not two independent means.
  * the adaptive schedule brackets a boundary (outward while a point works, bisected once one fails)
    and only then spends a capped two-axis grid.
  * a program is inert unless its encounter is the focused one, and it progresses past the parent's
    own 512-sample cap by creating new candidates rather than extending the capped one.

Deterministic and store-only: no simulator runs, so it is safe to run anywhere the recovery checks run.

    python check_optimizer_finetune.py
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import search_contract                                                      # noqa: E402
import strategy_finetune as finetune                                       # noqa: E402
import strategy_probe as probe                                             # noqa: E402
from strategy_optimizer import (FINETUNE_BASE_CHILDREN,                     # noqa: E402
                                FINETUNE_CHILDREN_PER_ATTEMPTS, FINETUNE_MAX_CHILDREN, MAX_SAMPLES,
                                Optimizer, Store, finetune_capacity, interleave_ready, scope,
                                staged_limits, unfinished_child)
import strategy_learner as learner                                          # noqa: E402
from strategy_optimizer_adapter import (default_scenario, provenance, stats,  # noqa: E402
                                        validate_scenario)


def outcome(index, ordinal, chests=8, verdict=1):
    """One accepted result, shaped like the runner's own record (shared seeds per ordinal)."""
    return dict(verdict=verdict, censored=False, ticks=40, prizeCallbacks=chests + ordinal,
                survivors=1, resourceUses=0,
                behavior=dict(attacks=1, heals=0, prizes=chests + ordinal),
                seeds=[ordinal, 100 + ordinal], digest=f'd{index}-{ordinal}', elapsedSeconds=.01,
                rewardOutcome=dict(awardedChests=chests if verdict == 1 else 0,
                                   pendingChests=chests, awardedBasis='test',
                                   inventoryVerified=False))


def base_scenario(mp=7149, tick=40):
    """The frozen default with the Ninja's MP set to `mp`, on encounter 19."""
    scenario = validate_scenario(dict(default_scenario(), tickLimit=tick, encounterId=19))
    ninja = next(unit for unit in scenario['ownUnits'] if unit['name'].startswith('Ninja'))
    search_contract.set_effective_parameter(scenario, ninja, 11, mp)
    return validate_scenario(scenario)


def open_store():
    """A bare temporary sqlite store (some sandboxes refuse a database in a fresh subdirectory)."""
    handle = tempfile.NamedTemporaryFile(suffix='.sqlite', delete=False)
    handle.close()
    return Store(Path(handle.name), provenance())


def close_store(store):
    path = store.path
    store.close()
    for suffix in ('', '-wal', '-shm'):
        leftover = Path(str(path) + suffix)
        if leftover.exists():
            leftover.unlink()


def make_optimizer(focus):
    """A coordinator-shaped object without starting the search thread (private methods only)."""
    optimizer = Optimizer.__new__(Optimizer)
    optimizer._focus_encounter = focus
    optimizer._finetune_cache = None
    optimizer._last_finetune = None
    return optimizer


class FineTuneProgramTests(unittest.TestCase):
    def setUp(self):
        self.store = open_store()
        self.base = base_scenario()
        with self.store.db:
            self.store.set('searchSpaceVersion', search_contract.SEARCH_SPACE_VERSION)
            self.store.set('scope', scope(self.base))
            self.parent = self.store.add(self.base, 'Baseline enc19', 'supplied', stats(self.base))

    def tearDown(self):
        close_store(self.store)

    def ninja_parameter(self, parameter_id):
        ninja = next(unit for unit in self.base['ownUnits'] if unit['name'].startswith('Ninja'))
        return search_contract.battle_value(self.base, ninja, parameter_id)

    def create(self, focus=19, **value):
        optimizer = make_optimizer(focus)
        error, view = optimizer._create_finetune(
            self.store, dict(candidateId=self.parent, axis='mp', **value))
        return optimizer, error, view

    def test_mp_reduction_is_representable_and_scheduled(self):
        # The motivating question: MP 7149 -> around 6500, on the focused encounter.
        self.assertEqual(self.ninja_parameter(11), 7149)
        _optimizer, error, view = self.create(targets=[6500])
        self.assertIsNone(error)
        values = [point['value'] for point in view['points']]
        self.assertIn(6500, values)
        self.assertEqual(view['parent']['candidateId'], self.parent)
        self.assertTrue(view['focused'])
        self.assertTrue(view['dispatchable'])
        point = next(point for point in view['points'] if point['value'] == 6500)
        # The tested number is the effective value a player would see, and it moved.
        self.assertEqual(point['effective'], 6500)
        banks = self.store.get('probeBanks') or {}
        self.assertGreaterEqual(banks[point['candidateId']], finetune.MIN_POINT_SEEDS)
        # The frozen parent joins the same bank, which is what makes the comparison paired.
        self.assertGreaterEqual(banks[self.parent], finetune.MIN_POINT_SEEDS)
        limits = staged_limits(0, 0, None, False, None, None, None, None, True,
                               banks[point['candidateId']], True, 'branching')
        self.assertEqual(limits, (0, banks[point['candidateId']]))
        self.assertGreater(limits[1], 0, 'the new point must be dispatchable')

    def test_future_precision_target_does_not_hide_a_drained_bank(self):
        optimizer, error, created = self.create(targets=[6500])
        self.assertIsNone(error)
        cid = created['points'][0]['candidateId']
        optimizer._planner_exploit = {cid: dict(target=9604)}
        optimizer._planner_limits = {cid: (0, 512)}
        program = self.store.get('fineTunePrograms')[created['id']]
        _, measurements = optimizer._finetune_measurements(self.store, program)
        self.assertEqual(measurements[6500]['bank'], 512)
        self.assertEqual(optimizer._effective_bank(cid, self.store.get('probeBanks')), 9604)
        for ordinal in range(512):
            self.store.record(cid, 'validation', ordinal, outcome(0, ordinal))
        _, measurements = optimizer._finetune_measurements(self.store, program)
        self.assertEqual(measurements[6500]['completedBank'], 512)
        # No paired parent evidence: grow the exhausted bank rather than wait for an
        # eventual precision target which the scheduler has not granted yet.
        self.assertIn(dict(value=6500, bank=1024), finetune.plan(program, measurements)['extend'])

    def test_view_cache_rebuilds_only_for_its_program_inputs(self):
        optimizer, error, created = self.create(targets=[6500])
        self.assertIsNone(error)
        point_id = created['points'][0]['candidateId']
        with patch.object(optimizer, '_finetune_view', wraps=optimizer._finetune_view) as render:
            first = optimizer._finetune_snapshot(self.store)
            parent_rows = optimizer._finetune_rows(self.store, self.parent)
            point_rows = optimizer._finetune_rows(self.store, point_id)
            self.assertEqual(render.call_count, 1)
            self.assertEqual(optimizer._finetune_snapshot(self.store), first)
            self.assertEqual(render.call_count, 1)
            self.assertIs(optimizer._finetune_rows(self.store, self.parent), parent_rows)
            self.assertIs(optimizer._finetune_rows(self.store, point_id), point_rows)

            # An unrelated fight changes totalRuns, but none of this program's evidence.
            unrelated = base_scenario(mp=7000)
            with self.store.db:
                other_id = self.store.add(unrelated, 'Unrelated build', 'supplied', stats(unrelated))
            self.store.record(other_id, 'discovery', 0, outcome(1, 0))
            self.assertEqual(optimizer._finetune_snapshot(self.store), first)
            self.assertEqual(render.call_count, 1)

            # A tested point, its bank, and focus each affect the published view.
            self.store.record(point_id, 'discovery', 0, outcome(2, 0))
            updated = optimizer._finetune_snapshot(self.store)
            self.assertEqual(render.call_count, 2)
            self.assertNotEqual(updated, first)
            self.assertIs(optimizer._finetune_rows(self.store, self.parent), parent_rows)
            self.assertIsNot(optimizer._finetune_rows(self.store, point_id), point_rows)
            banks = self.store.get('probeBanks') or {}
            banks[point_id] += 1
            with self.store.db:
                self.store.set('probeBanks', banks)
            optimizer._finetune_snapshot(self.store)
            self.assertEqual(render.call_count, 3)
            optimizer._focus_encounter = 7
            self.assertFalse(optimizer._finetune_snapshot(self.store)[0]['focused'])
            self.assertEqual(render.call_count, 4)

    def test_noncombat_axes_are_refused(self):
        for axis in ('hrt', 'gth', 'mov'):
            with self.assertRaises(finetune.FineTuneError):
                finetune.require_combat_axis(axis)
            with self.assertRaises(ValueError):
                make_optimizer(19)._create_finetune(
                    self.store, dict(candidateId=self.parent, axis=axis))
        self.assertFalse(finetune.is_combat_axis('hrt'))
        self.assertTrue(finetune.is_combat_axis('vig'))
        self.assertIn('vig', finetune.COMBAT_STAT_PRIORITY)
        # Energy resolves as an extra axis (parameter 12) without joining the canonical probe set.
        self.assertEqual(probe.AXES['vig']['parameter'], 12)
        self.assertNotIn('vig', probe.axis_names())

    def test_int_is_conditional_on_a_magic_attacker(self):
        # The Ninja carries no magic attack skill, so its damage reads Attack, not Intelligence.
        with self.assertRaises(ValueError):
            make_optimizer(19)._create_finetune(
                self.store, dict(candidateId=self.parent, axis='int'))
        self.assertNotIn('int', finetune.combat_axis_options(False))
        self.assertIn('int', finetune.combat_axis_options(True))
        armed = validate_scenario(search_contract.add_skill(self.base, 'Ninja (A aw20)', 5))
        with self.store.db:
            armed_id = self.store.add(armed, 'Armed enc19', 'supplied', stats(armed))
        optimizer = make_optimizer(19)
        error, view = optimizer._create_finetune(
            self.store, dict(candidateId=armed_id, axis='int', targets=[100]))
        self.assertIsNone(error)
        self.assertEqual(view['axis'], 'int')
        self.assertTrue(view['dispatchable'])


class ReadinessAndPairingTests(unittest.TestCase):
    def setUp(self):
        self.store = open_store()
        self.base = base_scenario()
        with self.store.db:
            self.store.set('searchSpaceVersion', search_contract.SEARCH_SPACE_VERSION)
            self.store.set('scope', scope(self.base))
            self.parent = self.store.add(self.base, 'Baseline enc19', 'supplied', stats(self.base))
        self.optimizer = make_optimizer(19)
        self._error, self.view = self.optimizer._create_finetune(
            self.store, dict(candidateId=self.parent, axis='mp', targets=[6500]))
        self.point = self.view['points'][0]

    def tearDown(self):
        close_store(self.store)

    def record(self, cid, count, chests, verdict=1, index=0):
        for ordinal in range(count):
            self.store.record(cid, 'validation', ordinal, outcome(index, ordinal, chests, verdict))
        self.store.flush()

    def refresh(self):
        return self.optimizer._finetune_view(
            self.store, self.store.get('fineTunePrograms')[self.view['id']])

    def test_point_is_provisional_until_100_seeds(self):
        self.record(self.point['candidateId'], 40, chests=8)
        view = self.refresh()
        point = view['points'][0]
        self.assertFalse(point['ready'])
        self.assertFalse(point['comparable'])
        self.assertLess(point['runs'], finetune.MIN_POINT_SEEDS)
        actions = finetune.plan(self.store.get('fineTunePrograms')[self.view['id']],
                                {6500: dict(runs=40, viable=None, bank=128)})
        # The authorised 128-seed bank already exceeds the readiness floor: let it finish.
        self.assertEqual(actions['extend'], [])

    def test_paired_comparison_uses_shared_seeds(self):
        self.record(self.parent, 110, chests=8, verdict=1, index=0)
        self.record(self.point['candidateId'], 110, chests=8, verdict=1, index=1)
        view = self.refresh()
        point = view['points'][0]
        self.assertTrue(point['comparable'])
        self.assertIsNotNone(point['paired'])
        self.assertGreaterEqual(point['paired']['n'], finetune.MIN_POINT_SEEDS)
        self.assertEqual(point['paired']['mean'], 0.0)
        self.assertTrue(point['viable'])

    def test_a_failing_point_is_not_called_working(self):
        self.record(self.parent, 128, chests=8, verdict=1, index=0)
        self.record(self.point['candidateId'], 128, chests=0, verdict=2, index=1)
        view = self.refresh()
        point = view['points'][0]
        self.assertTrue(point['comparable'])
        self.assertFalse(point['viable'])
        self.assertEqual(point['winRate'], 0.0)
        self.assertLess(point['paired']['upper'], 0)
        self.assertEqual(view['boundary']['low'], 6500)
        self.assertEqual(view['boundary']['high'], 7149)

    def test_a_thin_paired_overlap_is_not_comparable(self):
        # The point has its own 110 runs, but the parent only shares 5 ordinals, so the paired verdict
        # does not rest on the readiness floor and the point stays provisional for the report.
        self.record(self.parent, 5, chests=8, verdict=1, index=0)
        self.record(self.point['candidateId'], 110, chests=8, verdict=1, index=1)
        view = self.refresh()
        point = view['points'][0]
        self.assertEqual(point['runs'], 110)
        self.assertTrue(point['ready'])
        self.assertLess(point['paired']['n'], finetune.MIN_POINT_SEEDS)
        self.assertFalse(point['comparable'])
        self.assertIsNone(point['viable'])
        self.assertIsNone(view['boundary'])
        self.assertIsNone(view['viableLow'])
        self.assertIsNone(view['viableHigh'])

    def test_interaction_grid_is_created_once_after_a_bracket(self):
        # A single-axis boundary is the evidence that lets a second axis be spent - and only then.
        self.record(self.parent, 128, chests=8, verdict=1, index=0)
        self.record(self.point['candidateId'], 128, chests=0, verdict=2, index=1)
        self.assertTrue(self.optimizer._advance_finetune_programs(self.store))
        view = self.refresh()
        self.assertIsNotNone(view['boundary'], 'the sweep must report a bracketed boundary')
        self.assertGreater(view['used']['interactionCells'], 0, 'a capped grid follows the bracket')
        self.assertEqual(len(view['cells']), view['used']['interactionCells'])
        self.assertIsNotNone(view['grid'])
        self.assertIn(view['grid']['axis2'], finetune.COMBAT_STAT_PRIORITY)
        # The grid is spent once: a later pass cannot keep adding cells (before its results arrive).
        cells_before = len(view['cells'])
        self.optimizer._advance_finetune_programs(self.store)
        self.assertEqual(len(self.refresh()['cells']), cells_before)
        encounters = self.store.candidate_encounters()
        banks = self.store.get('probeBanks') or {}
        for cell in self.refresh()['cells']:
            self.assertEqual(encounters[cell['candidateId']], 19)
            self.assertGreaterEqual(banks[cell['candidateId']], finetune.MIN_POINT_SEEDS)
        # The fine-tune points are probe candidates, but they must not be folded into the ordinary
        # single-axis probe ladders (their tag is not a candidate label).
        analyses = Optimizer._probes(self.optimizer, self.store)
        self.assertFalse(any(str(item.get('pivotLabel') or '').startswith('fine-tune')
                             for item in analyses))


class ScheduleTests(unittest.TestCase):
    def program(self, targets=(6500,)):
        parent = dict(candidateId='a' * 64, label='Baseline', encounterId=19)
        return finetune.new_program(parent=parent, axis='mp', unit='Ninja', baseline=7149,
                                    encounter_id=19, targets=targets, bounds=(1, 30000))

    def test_adaptive_schedule_brackets_and_expands(self):
        program = self.program()
        program['points']['6500'] = dict(value=6500, candidateId='b' * 64, bank=128)
        # A judged point needs the paired overlap the readiness rule requires, not just a run count.
        judged = dict(paired=dict(n=128), comparable=True, unresolved=0)
        # Everything measured still works: step outward, downward.
        working = {6500: dict(runs=128, viable=True, bank=128, **judged)}
        actions = finetune.plan(program, working, bounds=(1, 30000))
        self.assertEqual([step['reason'] for step in actions['creates']], ['expand-down'])
        self.assertLess(actions['creates'][0]['value'], 6500)
        # One point fails next to the working parent: bisect toward the last working point.
        failing = {6500: dict(runs=128, viable=False, bank=128, **judged)}
        actions = finetune.plan(program, failing, bounds=(1, 30000))
        self.assertEqual([step['reason'] for step in actions['creates']], ['bracket'])
        self.assertEqual(actions['creates'][0]['value'], (6500 + 7149) // 2)
        self.assertEqual(actions['bracket']['low'], 6500)
        self.assertEqual(actions['bracket']['high'], 7149)

    def test_explicit_target_is_asked_first(self):
        program = self.program(targets=(6500,))
        actions = finetune.plan(program, {}, bounds=(1, 30000))
        self.assertEqual([step['value'] for step in actions['creates']], [6500])
        self.assertEqual(actions['creates'][0]['reason'], 'target')

    def test_interaction_grid_only_after_a_bracket(self):
        program = self.program()
        program['points']['6500'] = dict(value=6500, candidateId='b' * 64, bank=128)
        # The paired overlap and comparability a judged point needs, not just its own run count.
        judged = dict(paired=dict(n=128), comparable=True, unresolved=0)
        working = {6500: dict(runs=128, viable=True, bank=128, **judged)}
        self.assertIsNone(finetune.interaction_plan(program, working,
                                                    dict(axis='def', baseline=353, step=18)))
        failing = {6500: dict(runs=128, viable=False, bank=128, **judged)}
        grid = finetune.interaction_plan(program, failing, dict(axis='def', baseline=353, step=18))
        self.assertEqual(grid['axis2'], 'def')
        self.assertEqual(sorted(grid['values']), [6500, 7149])
        self.assertLessEqual(len(grid['cells']), program['budget']['maxInteractionCells'])
        self.assertEqual(len(grid['cells']), 6)

    def test_budget_caps_children(self):
        program = self.program()
        program['budget']['maxChildren'] = 2
        program['counters']['children'] = 2
        self.assertEqual(finetune.children_left(program), 0)
        self.assertEqual(finetune.plan(program, {}, bounds=(1, 30000))['creates'], [])


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.store = open_store()
        self.base = base_scenario()
        with self.store.db:
            self.store.set('searchSpaceVersion', search_contract.SEARCH_SPACE_VERSION)
            self.store.set('scope', scope(self.base))
            self.parent = self.store.add(self.base, 'Baseline enc19', 'supplied', stats(self.base))
            other = validate_scenario(dict(self.base, encounterId=10))
            self.store.add(other, 'Baseline enc10', 'supplied', stats(other))

    def tearDown(self):
        close_store(self.store)

    def test_program_is_inert_until_its_encounter_is_focused(self):
        unfocused = make_optimizer(10)
        _error, view = unfocused._create_finetune(
            self.store, dict(candidateId=self.parent, axis='mp', targets=[6500]))
        self.assertFalse(view['focused'])
        self.assertFalse(view['dispatchable'])
        self.assertEqual(view['points'], [], 'no attempt may be authorised outside the focused fight')
        self.assertFalse(unfocused._advance_finetune_programs(self.store))
        focused = make_optimizer(19)
        self.assertTrue(focused._advance_finetune_programs(self.store))
        programs = focused._finetune_snapshot(self.store)
        self.assertEqual(len(programs), 1)
        self.assertTrue(programs[0]['dispatchable'])
        encounters = self.store.candidate_encounters()
        for point in programs[0]['points']:
            self.assertEqual(encounters[point['candidateId']], 19,
                             'every fine-tuning candidate belongs to the focused encounter')

    def test_focus_scopes_the_ready_list(self):
        optimizer = make_optimizer(19)
        optimizer._create_finetune(self.store, dict(candidateId=self.parent, axis='mp',
                                                    targets=[6500]))
        programs = optimizer._finetune_snapshot(self.store)
        point_id = programs[0]['points'][0]['candidateId']
        banks = self.store.get('probeBanks') or {}
        limits = {point_id: staged_limits(0, 0, None, False, None, None, None, None, True,
                                          banks[point_id], True, 'branching')}
        ordinals = {(point_id, 'validation'): 0}
        scoped = interleave_ready(limits, ordinals, set(), focus_ids={point_id})
        self.assertTrue(scoped, 'the focused view must offer the fresh point')
        self.assertTrue(all(cid == point_id for cid, _phase, _ordinal in scoped))

    def test_fine_tune_points_are_not_open_children(self):
        # A fine-tuning point is measured evidence, not an unevaluated speculation, so it must not
        # consume the encounter's open-child budget (the focus-liveness failure mode).
        optimizer = make_optimizer(19)
        optimizer._create_finetune(self.store, dict(candidateId=self.parent, axis='mp',
                                                    targets=[6500]))
        programs = optimizer._finetune_snapshot(self.store)
        point_id = programs[0]['points'][0]['candidateId']
        banks = self.store.get('probeBanks') or {}
        limits = {point_id: staged_limits(0, 0, None, False, None, None, None, None, True,
                                          banks[point_id], True, 'branching')}
        self.assertFalse(unfinished_child(point_id, limits, {(point_id, 'discovery'): 0}))
        # Encounter-level: the count of unevaluated children is unchanged by the fine-tune candidate,
        # so the fight can still spend its proposal slots on genuinely novel mutations.
        encounter_of = self.store.candidate_encounters()
        ordinals = {(cid, 'discovery'): 0 for cid in encounter_of}
        staged = {}
        for cid in encounter_of:
            staged[cid] = staged_limits(0, 0, None, False, None, None, None, None,
                                        cid in banks, banks.get(cid, 64), True, 'branching')
        open_children = sum(1 for cid in encounter_of
                            if encounter_of[cid] == 19 and unfinished_child(cid, staged, ordinals))
        self.assertEqual(open_children, 0)

    def fill_children(self, count, encounter=19, start=0):
        """Add `count` retained fine-tune lineage rows on distinct placeholder candidates."""
        with self.store.db:
            for index in range(start, start + count):
                variant = validate_scenario(dict(self.base))
                ninja = next(unit for unit in variant['ownUnits']
                             if unit['name'].startswith('Ninja'))
                search_contract.set_effective_parameter(variant, ninja, 10, 500 + index)
                variant = validate_scenario(variant)
                cid = self.store.add(variant, f'placeholder {index}', 'probe', stats(variant))
                learner.record_child(self.store.db, cid, None, cid, 0, 'fine-tune', index, 'test',
                                     f'fine-tune:seed-{index}', encounter, 0,
                                     search_contract.SEARCH_SPACE_VERSION, 0)

    def test_fresh_library_cannot_spawn_hundreds(self):
        # The budget is work-scaled, not a lifetime total: before a fight has recorded runs it holds
        # only the base floor, so a freshly created library cannot spawn hundreds of candidates.
        optimizer = make_optimizer(19)
        capacity = finetune_capacity(self.store, 19)
        self.assertEqual(capacity['attempts'], 0)
        self.assertEqual(capacity['allowed'], FINETUNE_BASE_CHILDREN)
        self.assertEqual(capacity['ceiling'], FINETUNE_MAX_CHILDREN)
        self.assertLess(capacity['allowed'], 100, 'a fresh library must not admit hundreds at once')
        self.assertFalse(capacity['saturated'])
        self.assertEqual(capacity['room'], FINETUNE_BASE_CHILDREN)
        self.fill_children(FINETUNE_BASE_CHILDREN)
        self.assertEqual(optimizer._finetune_capacity(self.store, 19)['room'], 0)
        _error, view = optimizer._create_finetune(
            self.store, dict(candidateId=self.parent, axis='mp', targets=[6500]))
        self.assertEqual(view['status'], 'active', 'admission pressure is not a resolved experiment')
        self.assertEqual(view['points'], [], 'the fresh-library floor must stop new candidates')

    def test_recorded_work_admits_a_newcomer_past_the_old_history(self):
        # The old fixed cap of 48 retained children meant a fight with this much history could never
        # tune another parent. New recorded runs must earn slots without deleting any of that history.
        optimizer = make_optimizer(19)
        self.fill_children(FINETUNE_BASE_CHILDREN)
        self.assertEqual(optimizer._finetune_capacity(self.store, 19)['room'], 0)
        with self.store.db:
            for ordinal in range(FINETUNE_CHILDREN_PER_ATTEMPTS * 4):
                self.store.record(self.parent, 'validation', ordinal, outcome(0, ordinal))
            self.store.flush()
        capacity = optimizer._finetune_capacity(self.store, 19)
        self.assertGreaterEqual(capacity['attempts'], FINETUNE_CHILDREN_PER_ATTEMPTS * 4)
        self.assertGreater(capacity['allowed'], FINETUNE_BASE_CHILDREN)
        self.assertGreater(capacity['room'], 0)
        self.assertEqual(capacity['retained'], FINETUNE_BASE_CHILDREN,
                         'no measured child may be pruned to free a slot')
        _error, view = optimizer._create_finetune(
            self.store, dict(candidateId=self.parent, axis='mp', targets=[6500]))
        self.assertEqual(view['status'], 'active')
        self.assertTrue(view['points'], 'a newcomer is admitted once the fight has recorded work')

    def test_saved_programs_stay_readable_at_the_cap(self):
        optimizer = make_optimizer(19)
        optimizer._create_finetune(self.store,
                                   dict(candidateId=self.parent, axis='mp', targets=[6500]))
        before = optimizer._finetune_snapshot(self.store)
        self.assertEqual(len(before), 1)
        saved_id = before[0]['id']
        saved_points = [point['candidateId'] for point in before[0]['points']]
        self.assertTrue(saved_points)
        # Fill the remaining budget so the encounter is at its cap. The saved program and its measured
        # points must stay readable - the cap is a refusal, never a reason to prune recorded evidence.
        room = optimizer._finetune_capacity(self.store, 19)['room']
        self.fill_children(room, start=len(saved_points))
        self.assertEqual(optimizer._finetune_capacity(self.store, 19)['room'], 0)
        optimizer._finetune_cache = None
        after = {program['id']: program for program in optimizer._finetune_snapshot(self.store)}
        self.assertIn(saved_id, after)
        self.assertEqual([point['candidateId'] for point in after[saved_id]['points']],
                         saved_points)
        self.assertEqual(after[saved_id]['used']['points'], len(saved_points))

    def test_admission_scales_with_work_and_saturates(self):
        # The policy reads the encounter's own recorded attempts: more work earns a larger budget up
        # to the explicit ceiling, and another encounter's runs never leak into this fight's budget.
        def capacity_for(attempts, encounter='19:0'):
            with self.store.db:
                self.store.set('encounterLifetime',
                               {encounter: dict(lifetimeAttempts=int(attempts))})
            return finetune_capacity(self.store, 19)

        fresh = capacity_for(0)
        self.assertEqual(fresh['allowed'], FINETUNE_BASE_CHILDREN)
        grown = capacity_for(FINETUNE_CHILDREN_PER_ATTEMPTS * 10)
        self.assertEqual(grown['allowed'], FINETUNE_BASE_CHILDREN + 10)
        self.assertEqual(grown['growth'], 10)
        huge = capacity_for(FINETUNE_MAX_CHILDREN * FINETUNE_CHILDREN_PER_ATTEMPTS * 4)
        self.assertEqual(huge['allowed'], FINETUNE_MAX_CHILDREN)
        self.assertTrue(huge['saturated'])
        self.assertLessEqual(huge['allowed'], FINETUNE_MAX_CHILDREN)
        other = capacity_for(FINETUNE_CHILDREN_PER_ATTEMPTS * 40, encounter='10:0')
        self.assertEqual(other['allowed'], FINETUNE_BASE_CHILDREN,
                         "another encounter's runs must not grow this fight's budget")

    def test_program_progresses_after_the_baseline_cap(self):
        # The parent has already spent its whole 512-sample ceiling; the program must still make
        # progress by creating a *new* candidate with its own bank rather than extending the parent.
        for ordinal in range(MAX_SAMPLES):
            self.store.record(self.parent, 'validation', ordinal, outcome(0, ordinal))
        self.store.flush()
        with self.store.db:
            banks = dict(self.store.get('probeBanks') or {})
            banks[self.parent] = MAX_SAMPLES
            self.store.set('probeBanks', banks)
        parent_runs = len(self.store.rows(self.parent, 'validation'))
        self.assertEqual(parent_runs, MAX_SAMPLES)
        capped = staged_limits(0, 0, None, False, None, None, None, None, True, MAX_SAMPLES, True,
                               'branching')
        self.assertEqual(capped, (0, MAX_SAMPLES), 'the parent itself is at its ceiling')
        optimizer = make_optimizer(19)
        optimizer._create_finetune(self.store, dict(candidateId=self.parent, axis='mp',
                                                    targets=[6500]))
        programs = optimizer._finetune_snapshot(self.store)
        point_id = programs[0]['points'][0]['candidateId']
        self.assertNotEqual(point_id, self.parent)
        banks = self.store.get('probeBanks') or {}
        fresh = staged_limits(0, 0, None, False, None, None, None, None, True, banks[point_id],
                              True, 'branching')
        self.assertGreater(fresh[1], 0, 'a fresh candidate restores dispatchable work')
        ready = interleave_ready({point_id: fresh}, {(point_id, 'validation'): 0}, set())
        self.assertEqual(len(ready), fresh[1])


if __name__ == '__main__':
    unittest.main(verbosity=2)
