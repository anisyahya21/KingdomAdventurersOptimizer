"""Automatic tuning: focusing an encounter starts its frozen-parent programs, without a host command.

The reported gap: `fine_tune` programs were only ever created by an explicit host command, so a user
who used the Focus control could still watch a focused fight accumulate encounter-level attempts with
no systematic tuning of the strongest build. This checker pins the automatic start and the guarantees
around it:

  * a focus starts MP, Attack and Agility/speed programs on the strongest *credibly measured* build of
    that exact encounter - the parent is chosen from the library's own stored results (>=100 resolved,
    no unresolved, at least one win, highest mean earned, deterministic tie break), never an id;
  * the MP program's first point is "around 6500" for the 7149 baseline of the motivating question, and
    the adaptive schedule owns every later point;
  * the automatic set is bounded - a few candidates per program and half the encounter's base
    fine-tune floor in total - so it cannot oversubscribe the fight, while the encounter's work-scaled
    fine-tune budget still admits a newcomer once more runs are recorded;
  * the automatic programs are ordinary probe candidates, so the focused fight still proposes novel
    mutations (a nonzero novel-proposal stream) and the tested points never count as open children;
  * no qualified build reports `autoTuneReason` and leaves the focused search running;
  * repeated focus/status polls create no duplicate programs, switching encounters scopes the new
    programs to the new fight while the old ones wait, and refocusing resumes them;
  * a later, stronger leader may add programs but never duplicates one or discards measured points;
  * the Highest Earned record holder is tuned as a second, independent parent when it differs from the
    mean leader (a low mean must not leave it untuned), a pruned holder is reported rather than
    silently swapped, and the per-encounter program/child caps still bound the two together.

Store-only and deterministic: no battle is simulated, so it is safe to run anywhere the recovery
checks run.

    python check_optimizer_autotune.py
"""
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import search_contract                                                      # noqa: E402
import strategy_finetune as finetune                                       # noqa: E402
import strategy_learner as learner                                         # noqa: E402
from strategy_optimizer import (FINETUNE_BASE_CHILDREN,                     # noqa: E402
                                FINETUNE_CHILDREN_PER_ATTEMPTS, FINETUNE_MAX_CHILDREN, MAX_SAMPLES,
                                Optimizer, Store, encounter_scope, interleave_ready, scope,
                                spawn_children, staged_limits, unfinished_child)
from strategy_optimizer_adapter import (default_scenario, provenance, stats,  # noqa: E402
                                        validate_scenario)


def outcome(index, ordinal, chests=8, verdict=1, censored=False):
    """One accepted result, shaped like the runner's own record (shared seeds per ordinal)."""
    return dict(verdict=verdict, censored=censored, ticks=40, prizeCallbacks=chests + ordinal,
                survivors=1, resourceUses=0,
                behavior=dict(attacks=1, heals=0, prizes=chests + ordinal),
                seeds=[ordinal, 100 + ordinal], digest=f'd{index}-{ordinal}', elapsedSeconds=.01,
                rewardOutcome=dict(awardedChests=chests if verdict == 1 else 0,
                                   pendingChests=chests, awardedBasis='test',
                                   inventoryVerified=False))


def base_scenario(mp=7149, tick=40, encounter=19):
    """The frozen default with the Ninja's MP set to `mp`, on `encounter`."""
    scenario = validate_scenario(dict(default_scenario(), tickLimit=tick, encounterId=encounter))
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
    optimizer._auto_tune_state = None
    return optimizer


def plan_limits(store):
    """The staged limits the planner derives for every resident candidate."""
    banks = store.get('probeBanks') or {}
    limits = {}
    for row in store.db.execute('SELECT id FROM candidate'):
        cid = row['id']
        limits[cid] = staged_limits(0, 0, None, False, None, None, None, None, cid in banks,
                                    banks.get(cid, learner.VALIDATION_RUNS), True, 'branching')
    return limits


def plan_ordinals(store):
    """The next unrecorded ordinal per (candidate, phase), exactly as the coordinator keeps it."""
    return {(row[0], row[1]): int(row[2]) + 1 for row in store.db.execute(
        'SELECT candidate, phase, MAX(ordinal) FROM run GROUP BY candidate, phase')}


def drain(limits, ordinals, encounter_of, focus, workers):
    """Drain the ready list across `workers`, exactly as the loop rebuilds and reserves it."""
    focus_ids = encounter_scope(encounter_of, focus)
    dispatched = []
    reserved = set()
    for _ in range(64):
        view = {cid: value for cid, value in limits.items()
                if focus_ids is None or cid in focus_ids}
        order = interleave_ready(view, ordinals, reserved)
        if not order:
            break
        took = 0
        while order and took < workers:
            seed = order.pop(0)
            reserved.add(seed)
            dispatched.append(seed)
            took += 1
        if took == 0:
            break
    return dispatched

class AutoTuneTests(unittest.TestCase):
    def setUp(self):
        self.store = open_store()
        self.base = base_scenario()
        with self.store.db:
            self.store.set('searchSpaceVersion', search_contract.SEARCH_SPACE_VERSION)
            self.store.set('scope', scope(self.base))
        self.other = base_scenario(mp=600, encounter=10)

    def tearDown(self):
        close_store(self.store)

    def add_candidate(self, scenario, label, source='mutation'):
        with self.store.db:
            return self.store.add(scenario, label, source, stats(scenario))

    def record(self, cid, count, chests=20, verdict=1, phase='validation', index=0, censored=False,
               start=0):
        with self.store.db:
            for ordinal in range(start, start + count):
                self.store.record(cid, phase, ordinal,
                                  outcome(index, ordinal, chests, verdict, censored))
            self.store.flush()

    def distinct(self, attack):
        """A genuinely different build of encounter 19 (a different stored scenario => different id)."""
        scenario = validate_scenario(dict(self.base))
        ninja = next(unit for unit in scenario['ownUnits'] if unit['name'].startswith('Ninja'))
        search_contract.set_effective_parameter(scenario, ninja, 13, attack)
        return validate_scenario(scenario)

    def auto_programs(self, optimizer):
        return [program for program in optimizer._finetune_snapshot(self.store)
                if program.get('auto')]

    def test_focus_starts_three_axis_programs_and_points_dispatch(self):
        winner = self.add_candidate(self.base, 'Strong winner enc19')
        self.record(winner, MAX_SAMPLES, chests=20)
        optimizer = make_optimizer(19)
        self.assertTrue(optimizer._auto_tune(self.store))
        programs = self.auto_programs(optimizer)
        self.assertEqual({program['axis'] for program in programs}, set(finetune.AUTO_TUNE_AXES))
        for program in programs:
            self.assertEqual(program['encounterId'], 19)
            self.assertEqual(program['parent']['candidateId'], winner)
            self.assertTrue(program['dispatchable'])
        # The MP program covers legal walls and large absolute changes before local refinement.
        mp = next(program for program in programs if program['axis'] == 'mp')
        self.assertTrue(mp['points'])
        raw = self.store.get('fineTunePrograms')[mp['id']]
        self.assertTrue({1000, 2000, 4000}.issubset(raw['targets']))
        self.assertIn(raw['bounds'][1], raw['targets'])
        self.assertLess(mp['points'][0]['value'], 7149)
        # New point attempts begin: the fresh test points are dispatchable to the pool.
        limits = plan_limits(self.store)
        ordinals = plan_ordinals(self.store)
        encounter_of = self.store.candidate_encounters()
        dispatched = drain(limits, ordinals, encounter_of, 19, 24)
        self.assertTrue(dispatched, 'no point attempt reached the pool')
        point_ids = {point['candidateId'] for program in programs for point in program['points']}
        self.assertTrue(point_ids & {cid for cid, _phase, _ordinal in dispatched})
        self.assertTrue(all(encounter_of.get(cid) == 19 for cid, _phase, _ordinal in dispatched))
        # Fine-tune points are probes, so the novel-mutation stream is untouched: they are not open
        # children, and the focused fight still proposes a fresh mutation.
        banks = self.store.get('probeBanks') or {}
        self.assertTrue(all(not unfinished_child(cid, limits, ordinals) for cid in point_ids))
        self.assertGreater(len(spawn_children(self.store, 19, 1, 0, {})), 0,
                           'the focused fight must still propose novel mutations')

    def test_no_qualified_parent_reports_reason_and_keeps_exploring(self):
        thin = self.add_candidate(self.base, 'Thin candidate enc19')
        self.record(thin, 40, chests=20)
        self.record(thin, 1, verdict=None, censored=True)
        optimizer = make_optimizer(19)
        self.assertFalse(optimizer._auto_tune(self.store))
        self.assertEqual(self.auto_programs(optimizer), [])
        state = optimizer._auto_tune_snapshot(self.store)
        self.assertEqual(state['status'], 'no-parent')
        self.assertIn('resolved', state['reason'])
        self.assertGreater(len(spawn_children(self.store, 19, 1, 0, {})), 0,
                           'a fight without a qualified parent must keep exploring')

    def test_strong_candidate_appearing_later_is_picked_up(self):
        # A focus set before a build is measurable must start tuning once the build crosses the floor,
        # without a new command and without a second focus.
        late = self.add_candidate(self.base, 'Late winner enc19')
        self.record(late, 40, chests=20)
        optimizer = make_optimizer(19)
        self.assertFalse(optimizer._auto_tune(self.store))
        self.assertEqual(self.auto_programs(optimizer), [])
        self.record(late, 80, chests=20, start=40, index=1)
        self.assertTrue(optimizer._auto_tune(self.store))
        programs = self.auto_programs(optimizer)
        self.assertEqual({program['axis'] for program in programs}, set(finetune.AUTO_TUNE_AXES))
        self.assertTrue(all(program['parent']['candidateId'] == late for program in programs))

    def test_repeated_focus_and_status_polls_create_no_duplicates(self):
        winner = self.add_candidate(self.base, 'Strong winner enc19')
        self.record(winner, MAX_SAMPLES, chests=20)
        optimizer = make_optimizer(19)
        optimizer._auto_tune(self.store)
        first = [program['id'] for program in self.auto_programs(optimizer)]
        for _ in range(4):
            optimizer._auto_tune(self.store)
            optimizer._finetune_snapshot(self.store)   # a status poll
        again = [program['id'] for program in self.auto_programs(optimizer)]
        self.assertEqual(again, first)
        self.assertEqual(len(again), 3)
        total = sum(program['budget'].get('maxChildren', 0) for program in self.auto_programs(optimizer))
        self.assertLessEqual(total, finetune.AUTO_TUNE_ENCOUNTER_CHILDREN)

    def test_switching_encounters_respects_scope_and_refocus_resumes(self):
        winner = self.add_candidate(self.base, 'Strong winner enc19')
        self.record(winner, MAX_SAMPLES, chests=20)
        other = self.add_candidate(self.other, 'Strong winner enc10')
        self.record(other, 120, chests=12)
        optimizer = make_optimizer(19)
        optimizer._auto_tune(self.store)
        first = {program['id'] for program in self.auto_programs(optimizer)}
        self.assertEqual(len(first), 3)
        # Switch the focus: new programs belong to the new fight only, and the old ones are kept.
        optimizer._focus_encounter = 10
        self.assertTrue(optimizer._auto_tune(self.store))
        programs = self.auto_programs(optimizer)
        self.assertEqual(len(programs), 6)
        for program in programs:
            if program['id'] in first:
                self.assertEqual(program['encounterId'], 19)
                self.assertTrue(program['waitingForFocus'])
            else:
                self.assertEqual(program['encounterId'], 10)
                self.assertTrue(program['focused'])
        self.assertEqual(optimizer._auto_tune_snapshot(self.store)['encounterId'], 10)
        old = self.store.get('fineTunePrograms')[sorted(first)[0]]
        self.assertFalse(optimizer._finetune_advance_one(self.store, old))
        # Clearing the focus publishes the wait state; refocusing creates nothing new.
        optimizer._focus_encounter = None
        self.assertFalse(optimizer._auto_tune(self.store))
        self.assertEqual(optimizer._auto_tune_snapshot(self.store)['status'], 'waiting')
        optimizer._focus_encounter = 19
        optimizer._auto_tune(self.store)
        self.assertEqual(len(self.auto_programs(optimizer)), 6,
                         'refocusing must resume the existing programs, not add new ones')

    def test_leader_shift_adds_without_duplicates_or_data_loss(self):
        winner = self.add_candidate(self.base, 'Strong winner enc19')
        self.record(winner, MAX_SAMPLES, chests=20)
        optimizer = make_optimizer(19)
        optimizer._auto_tune(self.store)
        before = {program['id']: len(program['points']) for program in self.auto_programs(optimizer)}
        self.assertEqual(len(before), 3)
        stronger = self.add_candidate(self.distinct(500), 'Stronger winner enc19')
        self.record(stronger, 160, chests=40, index=1)
        optimizer._auto_tune(self.store)
        programs = self.auto_programs(optimizer)
        pairs = [(program['parent']['candidateId'], program['axis']) for program in programs]
        self.assertEqual(len(pairs), len(set(pairs)), 'a program was duplicated')
        for program in programs:
            if program['id'] in before:
                self.assertEqual(len(program['points']), before[program['id']],
                                 'measured points were discarded on a leader shift')
        self.assertIn(stronger, {program['parent']['candidateId'] for program in programs})
        self.assertTrue(optimizer._auto_tune_snapshot(self.store)['leaderChanged'])

    def test_unit_is_chosen_by_usable_stat(self):
        scenario = base_scenario()
        healer = next(unit for unit in scenario['ownUnits'] if unit['name'].startswith('Healer'))
        search_contract.set_effective_parameter(scenario, healer, 13, 999)
        scenario = validate_scenario(scenario)
        winner = self.add_candidate(scenario, 'Healer-led enc19')
        self.record(winner, MAX_SAMPLES, chests=20)
        optimizer = make_optimizer(19)
        optimizer._auto_tune(self.store)
        programs = {program['axis']: program for program in self.auto_programs(optimizer)}
        self.assertEqual(programs['atk']['unit'], healer['name'])
        self.assertEqual(programs['mp']['unit'], 'Ninja (A aw20)')

    def test_total_caps_stay_bounded(self):
        winner = self.add_candidate(self.base, 'Strong winner enc19')
        self.record(winner, MAX_SAMPLES, chests=20)
        optimizer = make_optimizer(19)
        optimizer._auto_tune(self.store)
        # Two leader shifts after the first would exhaust the per-encounter program/child budgets.
        for index, chests in enumerate((40, 60)):
            stronger = self.add_candidate(self.distinct(500 + 100*index),
                                          f'Stronger winner enc19 {index}')
            self.record(stronger, 120, chests=chests, index=index + 1)
            optimizer._auto_tune(self.store)
            programs = self.auto_programs(optimizer)
            self.assertLessEqual(len(programs), finetune.AUTO_TUNE_MAX_PROGRAMS_PER_ENCOUNTER)
            budgeted = sum(program['budget'].get('maxChildren', 0) for program in programs)
            self.assertLessEqual(budgeted, finetune.AUTO_TUNE_ENCOUNTER_CHILDREN)
            # Reserved budgets are concurrent; actual admissions obey the work-scaled floor.
            self.assertLessEqual(optimizer._finetune_capacity(self.store, 19)['retained'],
                                 optimizer._finetune_capacity(self.store, 19)['allowed'])
            self.assertGreaterEqual(optimizer._finetune_room(self.store, 19), 0)
        state = optimizer._auto_tune_snapshot(self.store)
        self.assertIsNotNone(state['reason'])
        self.assertIn('budget', state['reason'])

    def fill_children(self, count, encounter=19):
        """Add `count` retained fine-tune lineage rows on distinct placeholder candidates."""
        with self.store.db:
            for index in range(count):
                variant = validate_scenario(dict(self.base))
                ninja = next(unit for unit in variant['ownUnits']
                             if unit['name'].startswith('Ninja'))
                search_contract.set_effective_parameter(variant, ninja, 10, 400 + index)
                variant = validate_scenario(variant)
                cid = self.store.add(variant, f'placeholder {index}', 'probe', stats(variant))
                learner.record_child(self.store.db, cid, None, cid, 0, 'fine-tune', index, 'test',
                                     f'fine-tune:seed-{index}', encounter, 0,
                                     search_contract.SEARCH_SPACE_VERSION, 0)

    def test_work_scaled_cap_blocks_then_admits_new_auto_programs(self):
        # The budget scales with the fight's own recorded runs. Fill the current work-scaled budget and
        # no new automatic program may start; record more runs and a challenger is admitted again.
        winner = self.add_candidate(self.base, 'Strong winner enc19')
        self.record(winner, 120, chests=20)
        optimizer = make_optimizer(19)
        capacity = optimizer._finetune_capacity(self.store, 19)
        self.assertGreater(capacity['allowed'], 0)
        self.fill_children(capacity['allowed'])
        self.assertEqual(optimizer._finetune_room(self.store, 19), 0)
        self.assertFalse(optimizer._auto_tune(self.store))
        self.assertEqual(self.auto_programs(optimizer), [],
                         'the work-scaled budget must stop new automatic programs while it is full')
        reason = optimizer._auto_tune_snapshot(self.store)['reason']
        self.assertIn('cap', reason)
        self.assertIn(str(FINETUNE_MAX_CHILDREN), reason,
                      'the true ceiling must be stated in the refusal')
        self.record(winner, FINETUNE_CHILDREN_PER_ATTEMPTS * 4, chests=20, start=120)
        self.assertGreater(optimizer._finetune_room(self.store, 19), 0)
        self.assertTrue(optimizer._auto_tune(self.store))
        self.assertNotEqual(self.auto_programs(optimizer), [],
                            'newly recorded runs must admit a fresh automatic program')

    def test_record_holder_is_tuned_beside_the_mean_leader(self):
        # The mean leader is the highest-average build; the Highest Earned holder can be a different
        # build with a low mean. Both must be tuned, the record holder's programs must not disturb the
        # leader's, and a pruned holder must be reported with its frozen replay kept.
        leader = self.add_candidate(self.base, 'Mean leader enc19')
        self.record(leader, 160, chests=40)
        holder = self.add_candidate(self.distinct(300), 'Highest Earned enc19')
        self.record(holder, 1, chests=120, index=2, start=0)
        self.record(holder, 119, chests=2, index=2, start=1)
        saved = self.store.get('recordHolders')['earned:19:0']
        self.assertEqual(saved['candidate'], holder)
        optimizer = make_optimizer(19)
        self.assertTrue(optimizer._auto_tune(self.store))
        programs = self.auto_programs(optimizer)
        by_parent = {}
        for program in programs:
            by_parent.setdefault(program['parent']['candidateId'], set()).add(program['axis'])
        self.assertEqual(by_parent.get(leader), set(finetune.AUTO_TUNE_AXES))
        self.assertEqual(by_parent.get(holder), set(finetune.AUTO_TUNE_AXES))
        self.assertLessEqual(len(programs), finetune.AUTO_TUNE_MAX_PROGRAMS_PER_ENCOUNTER)
        state = optimizer._auto_tune_snapshot(self.store)
        self.assertEqual(state['record']['status'], 'tuning')
        self.assertFalse(state['record']['sameAsLeader'])
        self.assertEqual(state['record']['recordValue'], 120)
        # Idempotent: repeated passes and status polls add no duplicate program.
        before = sorted(program['id'] for program in programs)
        for _ in range(3):
            optimizer._auto_tune(self.store)
            optimizer._finetune_snapshot(self.store)
        self.assertEqual(sorted(p['id'] for p in self.auto_programs(optimizer)), before)
        # A pruned holder is reported as such, and its frozen replay is left intact - no other build
        # is silently frozen in its place.
        with self.store.db:
            self.store.db.execute('DELETE FROM candidate WHERE id=?', (holder,))
            self.store.db.execute('DELETE FROM candidate_meta WHERE id=?', (holder,))
        optimizer._auto_tune(self.store)
        state = optimizer._auto_tune_snapshot(self.store)
        self.assertEqual(state['record']['status'], 'pruned')
        self.assertIn('frozen replay', state['record']['reason'])
        self.assertEqual(self.store.get('recordHolders')['earned:19:0'], saved)


if __name__ == '__main__':
    unittest.main(verbosity=2)
