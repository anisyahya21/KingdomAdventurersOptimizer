"""Controlled fine-tuning of one frozen parent build on one focused encounter.

The branching search answers "which of these builds is best" by proposing fresh mutations. It does
not answer the question a player asks about a build that already works: "my team has MP 7149 - does
it still work at 6500, and where does it stop?" A branch candidate is a *different* strategy, so the
population ends up holding "MP 6490 plus a reordered roster" rather than "MP 6500 with everything else
held fixed". Mutations also stop at the per-candidate sample cap, so a family already measured 512
times cannot be probed further through the ordinary path.

A fine-tuning *program* supplies the missing controlled experiment. It freezes one parent build and
varies exactly one combat statistic at a time, so every tested point is attributable to that one
input. It is deliberately narrow and honest:

  * only combat-effective statistics are sweepable - HP, MP, Energy, Attack, Defence, Agility, Luck,
    Dexterity, and Intelligence while (and only while) the unit carries a magic attack skill. Heart
    (Love), Gathering and Move are refused: no recovered damage or hit formula reads them.
  * every tested point carries its own recorded run bank, and a point is not called comparable until
    at least `MIN_POINT_SEEDS` runs have resolved on it.
  * comparisons are paired: the frozen parent is measured over the same seed bank, so the paired
    difference - not two independent means - is what a verdict rests on.
  * a program with no explicit target asks for a deterministic *broad* ladder first - the axis's legal
    walls, the absolute rungs (1000/2000/4000) and interior points at fractions of the baseline (a
    half, the ~9 % reduction of the motivating question, its increase and a doubling) - so a sweep
    covers a coarse range instead of only the ~5 %-of-baseline step, and it finishes that coverage
    before concluding any boundary.
  * the search then brackets *every* distinct working/failing transition with an adaptive step
    (outward while a point still works, bisected toward the last working point once one fails) rather
    than assuming monotonicity; with no transition at all it probes the interior around the best
    measured mean for a non-monotone peak, and only then spends a small, capped grid on a two-axis
    interaction.
  * a program is inert unless its encounter is the focused one, so every attempt it authorises stays
    on the encounter the user selected, and its child budget is capped - with the per-pass creation
    limit unchanged - so it cannot flood a fight. A boundary is only ever called resolved when every
    measured transition is tightened to the resolution and the broad coverage is complete; an
    untested interval is reported as untested, never as proven.

Nothing here is a new game fact. It reuses `strategy_probe` for the axis arithmetic and the paired
boundary analysis, and `strategy_optimizer` for the recorded run's own chest/potential rules. A
measured point is a measured point: this module never reports a proven continuous range.
"""
from __future__ import annotations

import statistics

import strategy_probe

#: The combat statistics a program may sweep, in the priority order the site itself groups them: the
#: three vitals, then the physical group, then the conditional mental one. The order is also the order
#: a program's interaction partner is chosen from, so an interaction test prefers a physical axis over
#: the conditional Intelligence unless the unit is a magic attacker.
COMBAT_STAT_PRIORITY = ('hp', 'mp', 'vig', 'atk', 'def', 'spd', 'lck', 'dex', 'int')

#: Statistic slots that are never sweepable. Gathering (20), Move (21) and Heart/Love (22) are carried
#: by the scenario and the equipment table, but no recovered damage or hit formula reads them, so a
#: fine-tuning point on one would vary a number the fight never reads.
NON_COMBAT_STATS = {'gth': 'Gathering', 'mov': 'Move', 'hrt': 'Heart/Love'}

#: Combat statistics that only move damage while the unit can express them. Intelligence is the one
#: case; the canonical rule is `search_contract.stat_is_searchable`.
CONDITIONAL_AXES = ('int',)

#: The smallest bank a tested point must have resolved before its result is called comparable. This is
#: the task's own budget floor: below it a point is reported as provisional, never as an answer.
MIN_POINT_SEEDS = 100

#: The bank a freshly created point is given, and the ceiling one may grow to. It is at least the
#: readiness floor, so a point is measurable once its bank drains, and capped so one boundary cannot
#: consume the whole encounter's budget.
DEFAULT_POINT_BANK = 128
MAX_POINT_BANK = 32768
DEPTH_BUDGET_VERSION = 2

#: The default incremental experiment budget. `maxChildren` bounds every candidate a program creates
#: (single-axis points plus interaction cells), so a program can never become the unbounded generator
#: the focus-liveness work removed. `maxNewPoints` bounds how many new candidates one pass may add,
#: and `maxInteractionCells` bounds the two-axis grid.
DEFAULT_BUDGET = dict(minSeeds=MIN_POINT_SEEDS, pointBank=DEFAULT_POINT_BANK, maxBank=MAX_POINT_BANK,
                      maxChildren=48, maxNewPoints=2, maxInteractionCells=6, stepRatio=0.05)

PROGRAM_STATUSES = ('active', 'done', 'blocked')

#: The absolute coarse rungs every broad sweep asks for, when the axis's own legal walls admit them.
#: They are a fixed ladder rather than a ratio so a 7149 MP and a 40 Attack landmark the same
#: easy-to-read numbers, and they are the rungs the automatic programs never used to emit.
BROAD_TARGET_RUNGS = (1000, 2000, 4000)

#: The first reduction applied to a baseline when the motivating question is asked directly. It is
#: ~9 %, the size of the question "an MP of 7149 tested at 6506". The broad ladder below derives one
#: of its interior fractions from it (and one symmetric increase), so the question survives as one
#: point of the ladder even when no caller pins it.
AUTO_TUNE_FIRST_REDUCTION = 0.09

#: The interior ladder: fractions *of the baseline itself*, kept only where they fall inside the read
#: walls. They are what put coarse points near the baseline - a half, the ~9 % reduction of the
#: motivating question, its symmetric increase, and a doubling - where the walls and the 1000/2000/
#: 4000 rungs leave a hole. Points that would just round onto a wall or a rung are dropped.
BROAD_TARGET_INTERIOR_FRACTIONS = (0.5, 1 - AUTO_TUNE_FIRST_REDUCTION,
                                   1 + AUTO_TUNE_FIRST_REDUCTION, 2.0)

#: The structural ceiling of one broad ladder - the two legal walls, the three rungs and the four
#: interior fractions - before coincidences collapse them. It is what sizes the automatic children
#: budget (`AUTO_TUNE_MAX_CHILDREN_PER_PROGRAM`).
BROAD_TARGETS_MAX = 9

#: Near-duplicate guard for the *optional* interior points: a fractional point within this fraction of
#: its own magnitude of a value the ladder already holds is dropped, so "1000" and a rounded "1004"
#: never both appear. The required walls and rungs are never subject to it.
BROAD_TARGET_MIN_GAP_RATIO = 0.01

#: Version marker for the broad-ladder migration of legacy automatic programs (see
#: `upgrade_broad_targets`). Bumping it re-offers the ladder to programs that already carry a version.
BROAD_TARGETS_VERSION = 1

#: The axes an automatically started tuning set covers, in the order it starts them: the three the
#: motivating question names - MP, Attack and Agility/speed. Each is an unconditional combat axis, so
#: the only eligibility rule is that a unit in the frozen team carries a usable value for it.
AUTO_TUNE_AXES = ('mp', 'atk', 'spd')

#: The refinement headroom added on top of a broad ladder: enough bisection passes that every distinct
#: working/failing transition the ladder opens can be tightened, not only the single tightest one.
AUTO_TUNE_REFINEMENT_CHILDREN = 39

#: The candidate budget one automatically started program gets: a full coarse ladder *plus* refinement
#: headroom (`BROAD_TARGETS_MAX` walls/rungs/interior points + `AUTO_TUNE_REFINEMENT_CHILDREN`
#: bisections). The per-pass creation limit (`maxNewPoints`, 2) is unchanged, so the larger ceiling is
#: spent across many passes rather than as a single burst. Reserved budgets are spent over time; actual admissions
#: obey the encounter's work-scaled fine-tune cap.
AUTO_TUNE_MAX_CHILDREN_PER_PROGRAM = BROAD_TARGETS_MAX + AUTO_TUNE_REFINEMENT_CHILDREN

#: The per-program ceiling the broad-ladder correction replaced (auto programs used to get four
#: children and no targets). It is the marker `upgrade_broad_targets` uses to recognise an untouched
#: legacy automatic budget, so a genuinely custom ceiling is never overwritten.
LEGACY_AUTO_TUNE_MAX_CHILDREN_PER_PROGRAM = 4

#: How many automatically started programs one encounter may hold. Three cover the initial axes; the
#: remaining slots let a later leader be tuned without duplicating a program or discarding the
#: measured points of the earlier one. Finished programs release their concurrent slots.
AUTO_TUNE_MAX_PROGRAMS_PER_ENCOUNTER = 6

#: The concurrent reserved child budget for six automatic programs, shared across parents.
#: Actual retained candidates still obey the encounter's work-scaled admission cap. This is a
#: scheduling ceiling, not permission to create all reserved candidates at once:
#: the work-scaled retention cap (`strategy_optimizer.finetune_capacity`) still bounds the candidates
#: actually created and grows with the fight's recorded runs, so a later leader's ladders are admitted
#: once the fight has earned the capacity rather than on the first pass.
AUTO_TUNE_ENCOUNTER_CHILDREN = AUTO_TUNE_MAX_PROGRAMS_PER_ENCOUNTER * AUTO_TUNE_MAX_CHILDREN_PER_PROGRAM

#: The bounded rotating tuning the four-objective portfolio adds *beside* the mean leader and the
#: Highest Earned holder: a few lane candidates - one per objective visit, round-robin over all four
#: lanes - are frozen on the combat axes, so Highest Potential and Average Potential are tweaked too.
#: These are a *concurrent* window, not a lifetime total: only programs still `active` occupy a slot,
#: so a program that finishes (`done`) or is blocked releases its slot for the next lane member or
#: challenger, while the finished program and its measured points stay stored and inspectable. The
#: absolute ceiling remains the encounter's work-scaled fine-tune budget
#: (`strategy_optimizer.finetune_capacity`), which bounds the fine-tune candidates actually retained
#: across every program on the encounter.
AUTO_TUNE_PORTFOLIO_MAX_PROGRAMS = 4
#: The child budget one active portfolio program may hold. It matches the core automatic per-program
#: budget, so a program is bounded the same way on either path.
AUTO_TUNE_PORTFOLIO_CHILDREN_PER_PROGRAM = AUTO_TUNE_MAX_CHILDREN_PER_PROGRAM
#: The *concurrent* child budget: active portfolio programs together may hold this many scheduled
#: children. Tying it to the active window (rather than a lifetime total) is what lets a completed
#: program release its capacity for the next candidate.
AUTO_TUNE_PORTFOLIO_CHILDREN = (AUTO_TUNE_PORTFOLIO_MAX_PROGRAMS
                                * AUTO_TUNE_PORTFOLIO_CHILDREN_PER_PROGRAM)
#: The staged rotation's breadth stage: the first pass over the four lanes freezes exactly one combat
#: axis on each *distinct* credible lane member before any candidate is revisited, so an incumbent can
#: never be deepened while another lane member is still waiting for its first sweep.
AUTO_TUNE_PORTFOLIO_BREADTH_AXES = 1

#: The staged rotation's depth stage, and the hard per-candidate ceiling. Once no credible untuned lane
#: member is waiting, a promising candidate that already owns its breadth sweep may take a second axis
#: (Attack then Speed, say) - and the further established combat axes - while the concurrent
#: program/child budget still has room, up to this many. It is what lets a second stat *range* be
#: learned, and what gives the engine a second single-axis program to pair into a two-axis interaction.
#: No candidate+axis pair is ever repeated, so depth can never duplicate a program.
AUTO_TUNE_PORTFOLIO_AXES_PER_CANDIDATE = len(COMBAT_STAT_PRIORITY)

#: The resolved sample floor a build needs before it is a credible automatic parent. It matches the
#: staged non-probe validation ceiling (8/24/64 runs): 64 resolved runs with no unresolved and at
#: least one win is enough to *promote* a build to a tuning parent. It is deliberately not the
#: tested-point readiness floor (100) - promotion is not a confidence claim, and a point is still
#: judged only at `MIN_POINT_SEEDS` paired seeds.
AUTO_TUNE_MIN_RESOLVED = 64


class FineTuneError(ValueError):
    """A fine-tuning program the optimiser refuses to create, with the reason."""


def axis_label(axis):
    info = strategy_probe.AXES.get(axis)
    return info['label'] if info else axis


def require_combat_axis(axis):
    """The axis info for a sweepable combat statistic, or a refusal with the reason.

    Heart/Love, Gathering and Move are refused explicitly - values the scenario carries but that no
    recovered formula reads - and so is anything outside the combat priority order.
    """
    if axis in NON_COMBAT_STATS:
        raise FineTuneError(
            f'{axis!r} ({NON_COMBAT_STATS[axis]}) is not combat-effective: no recovered damage or hit '
            'formula reads it, so it is never a fine-tuning axis')
    if axis not in COMBAT_STAT_PRIORITY:
        raise FineTuneError(f'unknown fine-tuning axis {axis!r}; combat axes are '
                            + ', '.join(COMBAT_STAT_PRIORITY))
    info = strategy_probe.AXES.get(axis)
    if info is None or 'parameter' not in info:
        raise FineTuneError(f'{axis!r} has no combat parameter to sweep')
    return info


def is_combat_axis(axis):
    try:
        require_combat_axis(axis)
    except FineTuneError:
        return False
    return True


def combat_axis_options(magic_attacker):
    """The axes a program may sweep for a unit, in priority order.

    Intelligence appears only for a magic attacker. `magic_attacker` is the canonical
    `search_contract.stat_is_searchable(scenario, unit, "int")` reading; this helper keeps no second
    magic-skill list.
    """
    return tuple(axis for axis in COMBAT_STAT_PRIORITY
                 if axis not in CONDITIONAL_AXES or magic_attacker)


def conditional_axis(axis):
    return axis in CONDITIONAL_AXES


def default_step(baseline, ratio=None, minimum=1):
    """A first probe distance: a fixed fraction of the baseline, at least one unit.

    Ratios rather than absolute units keep the same program sensible for an Attack of 40 and an MP of
    7149. The step only seeds the search; the adaptive schedule replaces it from measured results.
    """
    ratio = DEFAULT_BUDGET['stepRatio'] if ratio is None else float(ratio)
    return max(int(minimum), int(round(abs(int(baseline)) * ratio)))


def program_id(parent_candidate_id, axis, suffix=0):
    head = str(parent_candidate_id)[:12]
    return f'ft-{head}-{axis}' + (f'-{suffix}' if suffix else '')


def auto_budget(per_program=None):
    """The bounded child budget one automatically started program gets.

    It differs from :data:`DEFAULT_BUDGET` in exactly one knob - ``maxChildren`` - so the automatic
    programs keep the tested-point readiness floor, the per-pass creation limit and the banks every
    explicit program has. The ceiling is a full broad ladder plus refinement headroom, and the three
    of them reserve future refinement headroom; the work-scaled retention cap still bounds
    what is actually created.
    """
    budget = dict(DEFAULT_BUDGET)
    budget['maxChildren'] = int(AUTO_TUNE_MAX_CHILDREN_PER_PROGRAM if per_program is None
                                else per_program)
    return budget


def broad_targets(baseline, bounds, rungs=BROAD_TARGET_RUNGS,
                  fractions=BROAD_TARGET_INTERIOR_FRACTIONS, direction=None):
    """The coarse ladder a sweep with no explicit target asks for, deterministic and bounded.

    The ladder is the axis's own legal walls (only when `bounds` carries them - a missing wall is
    never invented), the absolute rungs (``1000``/``2000``/``4000``) that fall inside those walls,
    and interior points at a half, the ~9 % reduction of the motivating MP question, its symmetric
    increase and a doubling of the baseline. That is the wide, easy-to-read coverage an automatic
    program never used to request, and it keeps a coarse point near the baseline.

    Rules the ladder keeps:

      * the baseline itself is never a target (the parent is the anchor, not a point to test);
      * every value is a distinct integer inside ``[bounds[0], bounds[1]]`` when bounds are known,
        and no value is negative otherwise;
      * ``direction`` ('down'/'up') keeps only the side the program was asked to search;
      * optional interior points within ``BROAD_TARGET_MIN_GAP_RATIO`` of a value already held are
        dropped, so a rounded near-duplicate never pads the ladder;
      * the result is sorted and never longer than :data:`BROAD_TARGETS_MAX`.

    With no bounds it returns only absolute test rungs; those are proposals, not claims about
    legal minimum/maximum walls. Canonical scenario validation still owns admissibility.
    """
    base = int(baseline)
    low = high = None
    if bounds:
        low, high = int(bounds[0]), int(bounds[1])
        if high < low:
            low, high = high, low
    keep, seen = [], set()

    def accept(value, *, required):
        value = int(round(value))
        if value == base or value < 0 or value in seen:
            return
        if low is not None and value < low:
            return
        if high is not None and value > high:
            return
        if direction == 'down' and value > base:
            return
        if direction == 'up' and value < base:
            return
        if not required:
            gap = max(1, int(round(abs(value) * BROAD_TARGET_MIN_GAP_RATIO)))
            if any(abs(value - other) < gap for other in seen):
                return
        seen.add(value)
        keep.append(value)

    if low is not None:
        accept(low, required=True)
        accept(high, required=True)
    for rung in rungs:
        accept(rung, required=True)
    if low is not None:
        # Baseline-relative interior points need a read wall to be known-legal, so they are skipped
        # without bounds - the function never spends a point past an unread ceiling.
        for fraction in fractions:
            accept(base * float(fraction), required=False)
    return tuple(sorted(keep[:BROAD_TARGETS_MAX]))


def auto_first_target(baseline, ratio=None):
    """The first MP point an automatic program asks for: ``baseline`` reduced by ~9 %.

    ``7149 -> 6506``, i.e. the "around 6500" of the motivating question. Only this first point is
    pinned; the adaptive schedule owns every later point. The result is never negative.
    """
    ratio = AUTO_TUNE_FIRST_REDUCTION if ratio is None else float(ratio)
    baseline = int(baseline)
    step = max(1, int(round(abs(baseline) * ratio)))
    return max(0, baseline - step)


def select_parent(records, minimum=None):
    """The build automatic tuning should freeze, from per-candidate result evidence.

    ``records`` are ``{candidateId, label, meanEarned, resolved, unresolved, wins}`` readings produced
    by reading the library's own stored results (the optimiser passes its aggregate result counters,
    the same ones the published evidence uses). A build is qualified only when it has at least
    ``minimum`` resolved runs, no unresolved run, and at least one win. ``minimum`` defaults to
    `AUTO_TUNE_MIN_RESOLVED` (64, the staged validation ceiling): enough to *promote* a build to a
    tuning parent, which is a scheduling decision, not a claim about confidence - every tested point
    is still judged only at `MIN_POINT_SEEDS` paired seeds. The highest mean earned chests wins, and
    ties are broken by candidate id ascending, so a library always selects the same build. Returns
    ``(record, None)`` or ``(None, reason)``.
    """
    minimum = AUTO_TUNE_MIN_RESOLVED if minimum is None else int(minimum)
    qualified = [record for record in records
                 if record.get('meanEarned') is not None
                 and int(record.get('resolved') or 0) >= minimum
                 and not int(record.get('unresolved') or 0)
                 and int(record.get('wins') or 0) > 0]
    if not qualified:
        best = max((int(record.get('resolved') or 0) for record in records), default=0)
        return None, (f'no candidate on this encounter is ready to tune: none has {minimum} resolved '
                      f'runs with no unresolved runs and at least one win yet (best so far: {best} '
                      'resolved). Novel search continues.')
    return min(qualified, key=lambda record: (-float(record['meanEarned']),
                                              str(record['candidateId']))), None


def point_bank(program, requested=None):
    budget = program.get('budget') or DEFAULT_BUDGET
    if requested is None:
        requested = budget.get('pointBank', DEFAULT_POINT_BANK)
    return max(int(budget.get('minSeeds', MIN_POINT_SEEDS)),
               min(int(budget.get('maxBank', MAX_POINT_BANK)), int(requested)))


def new_program(*, parent, axis, unit, baseline, encounter_id, targets=(), direction=None,
                step=None, budget=None, axis2=None, bounds=None, suffix=0, note=None):
    """One frozen-parent program record (no store writes, so it is testable on its own).

    `parent` is `{candidateId, label, encounterId, input?}` for the frozen build. `baseline` is the
    parent's *effective* value on the axis - the number the player sees - because every tested point is
    expressed and compared in effective terms. `targets` are explicit values to test (the "MP 6500" of
    the motivating question); the adaptive schedule supplies the refinement. With no explicit targets
    the program asks for `broad_targets` instead - the legal walls, the absolute rungs and the interior
    fractions - so a program started without a question still sweeps a coarse range.
    """
    require_combat_axis(axis)
    if axis2 is not None:
        require_combat_axis(axis2)
        if axis2 == axis:
            raise FineTuneError('the interaction axis must differ from the swept axis')
    base = int(baseline)
    explicit = sorted({int(value) for value in targets})
    if direction is None:
        direction = 'both'
        if explicit:
            below = [value for value in explicit if value < base]
            above = [value for value in explicit if value > base]
            if below and not above:
                direction = 'down'
            elif above and not below:
                direction = 'up'
    if direction not in ('down', 'up', 'both'):
        raise FineTuneError(f'unknown search direction {direction!r}; use down, up or both')
    merged = dict(DEFAULT_BUDGET)
    merged.update(budget or {})
    if bounds:
        merged['bounds'] = [int(bounds[0]), int(bounds[1])]
    # An explicit target list is the question the caller asked; it is honored exactly. Only a program
    # created with no targets at all defaults to the deterministic broad ladder, so an automatic
    # atk/spd program (and any explicit `fine_tune` with no values) sweeps walls and rungs rather than
    # standing on the ~5 %-of-baseline step alone.
    targets = explicit or list(broad_targets(base, merged.get('bounds'), direction=direction))
    program = dict(
        id=program_id(parent['candidateId'], axis, suffix),
        depthBudgetVersion=DEPTH_BUDGET_VERSION,
        status='active',
        axis=axis, axis2=axis2, axisLabel=axis_label(axis),
        unit=unit, encounterId=int(encounter_id),
        parent=dict(candidateId=parent['candidateId'], label=parent.get('label'),
                    encounterId=int(encounter_id)),
        baseline=dict(effective=base, input=parent.get('input')),
        direction=direction,
        targets=targets,
        step=int(step) if step else default_step(base),
        resolution=1,
        bounds=(merged.get('bounds') or None),
        budget=merged,
        points={},
        cells={},
        schedule=dict(attempted=[], notes=[]),
        counters=dict(children=0, points=0, interactionCells=0),
    )
    if note:
        program['schedule']['notes'].append(str(note))
    return program


def point_values(program):
    return sorted(int(value) for value in (program.get('points') or {}))


def upgrade_broad_targets(program):
    """Give a legacy automatic program the coarse broad ladder it was created without.

    The first automatic programs stored no explicit target, so their whole schedule was the ~5 %-of-
    baseline step: a program that finished had only ever asked about one narrow offset near the
    baseline. This migration hands such a program the deterministic broad ladder, raises only an
    *untouched legacy* four-child ceiling to the current broad budget, keeps every measured point and
    any custom ceiling (recorded under ``previousAutoBudget``), and reopens a completed program so it
    can sweep the ladder. It is idempotent through ``BROAD_TARGETS_VERSION`` and is applied only by
    the existing focused caller (``_finetune_advance_one``), never by rewriting the store externally.
    """
    if int(program.get('broadTargetsVersion') or 0) >= BROAD_TARGETS_VERSION:
        return False
    legacy_mp_target = (program.get('axis') == 'mp'
                        and program.get('targets') == [auto_first_target(int(
                            (program.get('baseline') or {}).get('effective') or 0))])
    if not program.get('auto') or (program.get('targets') and not legacy_mp_target):
        return False
    baseline = (program.get('baseline') or {}).get('effective')
    if baseline is None:
        return False
    budget = program.setdefault('budget', dict(DEFAULT_BUDGET))
    ceiling = int(budget.get('maxChildren', DEFAULT_BUDGET['maxChildren']))
    target_ceiling = auto_budget()['maxChildren']
    if 0 < ceiling <= LEGACY_AUTO_TUNE_MAX_CHILDREN_PER_PROGRAM and ceiling < target_ceiling:
        program['previousAutoBudget'] = dict(budget)
        budget['maxChildren'] = target_ceiling
    program['previousAutoTargets'] = list(program.get('targets') or [])
    if legacy_mp_target:
        program['direction'] = 'both'
    ladder = broad_targets(int(baseline), program.get('bounds'), direction=program.get('direction'))
    if ladder:
        program['targets'] = [int(value) for value in ladder]
    if program.get('status') == 'done':
        schedule = program.setdefault('schedule', {})
        if schedule.get('resolvedBoundary'):
            schedule['previousResolvedBoundary'] = schedule.pop('resolvedBoundary')
        schedule.pop('budgetExhausted', None)
        schedule['reopenedForBroadSweep'] = True
        program['status'] = 'active'
    program['broadTargetsVersion'] = BROAD_TARGETS_VERSION
    return True


def upgrade_depth_budget(program):
    """Lift legacy default ceilings once, preserving the old budget for inspection.

    Two migrations run together because the focused caller already invokes this at the one moment a
    program may change: the depth ceiling (old default 512 -> `MAX_POINT_BANK`) and, for an automatic
    program that never got one, the broad target ladder. Existing custom ceilings other than the old
    default 512 are preserved. Neither migration rewrites the user's DB externally; both are
    idempotent (depth via ``depthBudgetVersion``, broad via ``broadTargetsVersion``).
    """
    changed = False
    if int(program.get('depthBudgetVersion') or 0) < DEPTH_BUDGET_VERSION:
        budget = program.setdefault('budget', dict(DEFAULT_BUDGET))
        if int(budget.get('maxBank', 512)) == 512:
            program['previousDepthBudget'] = dict(budget)
            budget['maxBank'] = MAX_POINT_BANK
            schedule = program.setdefault('schedule', {})
            if (program.get('status') == 'done' and not schedule.get('resolvedBoundary')
                    and (schedule.get('budgetExhausted') or schedule.get('encounterCapReached'))):
                program['status'] = 'active'
                schedule['reopenedForDepth'] = True
                schedule.pop('budgetExhausted', None)
        program['depthBudgetVersion'] = DEPTH_BUDGET_VERSION
        changed = True
    if upgrade_broad_targets(program):
        changed = True
    return changed


def children_used(program):
    return int((program.get('counters') or {}).get('children') or 0)


def children_left(program):
    budget = program.get('budget') or DEFAULT_BUDGET
    return max(0, int(budget.get('maxChildren', DEFAULT_BUDGET['maxChildren'])) - children_used(program))


def measurement_ready(evidence, minimum=None):
    """True when a point's own bank has resolved and it pairs with the parent over enough seeds."""
    minimum = MIN_POINT_SEEDS if minimum is None else int(minimum)
    if int(evidence.get('runs') or 0) < minimum or int(evidence.get('unresolved') or 0):
        return False
    paired = evidence.get('paired')
    return paired is not None and int(paired.get('n') or 0) >= minimum


def _point_ready(evidence, minimum):
    """A point may be judged only when it is paired-comparable over the floor and complete.

    `measurement_ready` already requires the point's own runs, no unresolved observation and a paired
    overlap at the readiness floor; an explicit `comparable=False` from the evidence is honored on top
    so a view that already marked the point non-comparable can never be promoted back into a bracket.
    """
    if evidence.get('comparable') is False:
        return False
    if ('completedBank' in evidence and int(evidence.get('completedBank') or 0)
            < int(evidence.get('bank') or 0)):
        return False
    return measurement_ready(evidence, minimum)


def _working_failing(program, ready):
    """(working values, failing values) as *effective* numbers, anchored on the frozen parent.

    The parent is always a working anchor: it is the build the program was created from, so a failing
    point is only meaningful next to it. A program without that anchor could call an all-failing sweep
    a boundary.
    """
    baseline = int(program['baseline']['effective'])
    working, failing = {baseline}, set()
    for value, evidence in ready.items():
        value = int(value)
        if value == baseline:
            continue
        (working if evidence['viable'] else failing).add(value)
    return working, failing


def brackets(program, ready):
    """Adjacent measured working/failing pairs, tightest first, anchored on the parent."""
    working, failing = _working_failing(program, ready)
    measured = sorted(working | failing)
    pairs = []
    for left, right in zip(measured, measured[1:]):
        if (left in working) != (right in working):
            pairs.append((left, right))
    pairs.sort(key=lambda pair: (abs(pair[1] - pair[0]), pair[0]))
    return pairs


def _ready_measurements(measurements, minimum):
    """Measured points that may be judged: paired-comparable and carrying a verdict.

    A point whose own runs resolved but whose paired overlap never reached the readiness floor - or that
    the evidence itself marked non-comparable - is kept out of every bracket. So is a point whose paired
    interval is still undecided (`viable is None`): that point is extended, never judged.
    """
    return {int(value): evidence for value, evidence in measurements.items()
            if evidence.get('viable') is not None and _point_ready(evidence, minimum)}


def _interior_probes(ready, baseline, resolution):
    """Untested interior midpoints around the best measured mean, nearest a non-monotone extremum.

    A non-monotone peak or trough can sit entirely inside a run of "everything still works" points and
    would never appear as a working/failing transition, so the bracket schedule alone would step over
    it. This picks the judged point with the highest measured mean (deterministically, lowest value on
    a tie) and offers the midpoint of each adjoining interval that is still wider than the program's
    resolution. Points with no measured mean (an older or partial evidence record) contribute nothing.
    """
    measured = sorted({int(value) for value in ready} | {int(baseline)})
    if len(measured) < 2:
        return []
    best_value = best_mean = None
    for value in sorted(ready):
        mean = ready[value].get('meanEarned')
        if mean is None:
            continue
        value = int(value)
        if best_mean is None or mean > best_mean or (mean == best_mean and value < best_value):
            best_value, best_mean = value, mean
    if best_value is None or best_value not in measured:
        return []
    index = measured.index(best_value)
    probes = []
    for neighbour in (index - 1, index + 1):
        if 0 <= neighbour < len(measured):
            lower, upper = sorted((measured[neighbour], best_value))
            if upper - lower > resolution:
                probes.append((lower + upper) // 2)
    return sorted(set(probes))


def plan(program, measurements, bounds=None):
    """The next controlled actions for one program: create points, extend banks, or stop.

    `measurements` maps an effective value to its own evidence (`{runs, viable, bank, ...}`). The result
    is structural only - the caller creates the candidates and sets their banks; the wording lives in
    `report`. The schedule is: finish broad coverage first (every declared target placed and judged),
    then tighten every distinct working/failing transition (coarsest untested gap first, never only the
    tightest), then - once no transition has appeared - probe the interior around the best measured
    mean and step outward. A program is called resolved only when *every* measured transition is
    tightened to the resolution and broad coverage is complete: an untested interval is never reported
    as a proven boundary.
    """
    budget = program.get('budget') or DEFAULT_BUDGET
    minimum = int(budget.get('minSeeds', MIN_POINT_SEEDS))
    max_bank = int(budget.get('maxBank', MAX_POINT_BANK))
    max_new = max(0, int(budget.get('maxNewPoints', DEFAULT_BUDGET['maxNewPoints'])))
    baseline = int(program['baseline']['effective'])
    known = set(point_values(program))
    if bounds is None:
        bounds = program.get('bounds')
    low = int(bounds[0]) if bounds else None
    high = int(bounds[1]) if bounds else None
    resolution = max(1, int(program.get('resolution') or 1))

    ready = _ready_measurements(measurements, minimum)

    # Extend undecided points only after their authorised bank drains. A provisional point may
    # first be raised to the readiness floor, but repeated planning must not double in-flight work.
    extend = []
    for value in sorted(measurements):
        if int(value) in ready:
            continue
        evidence = measurements[value]
        runs = int(evidence.get('completedBank', evidence.get('runs')) or 0)
        bank = int(evidence.get('bank') or 0)
        if bank >= max_bank:
            continue
        if bank < minimum:
            extend.append(dict(value=int(value), bank=min(max_bank, minimum)))
        elif runs >= bank:
            extend.append(dict(value=int(value),
                               bank=min(max_bank, max(minimum, bank * 2))))
    creates = []
    status = 'active'
    bracket = None
    resolved = None

    def add(value, reason):
        value = int(value)
        if value == baseline or (low is not None and value < low) or (high is not None and value > high):
            return False
        if value in known or any(action['value'] == value for action in creates):
            return False
        if len(creates) >= max_new or children_left(program) <= len(creates):
            return False
        creates.append(dict(value=value, reason=reason))
        return True

    targets = [int(value) for value in (program.get('targets') or ())]
    for target in targets:
        add(target, 'target')

    pairs = brackets(program, ready)
    # Tighten every distinct working/failing transition, the coarsest untested gap first, so a wide
    # transition is bracketed before any one transition is narrowed to the program's resolution.
    for left, right in sorted(pairs, key=lambda pair: (-abs(pair[1] - pair[0]), pair[0])):
        if abs(right - left) > resolution:
            add((left + right) // 2, 'bracket')
    if pairs:
        left, right = pairs[0]                    # brackets() is tightest-first
        bracket = dict(low=min(left, right), high=max(left, right),
                       resolution=abs(right - left), bracketed=True)

    # An explicit target that has not been judged yet is the question being asked: a target whose point
    # is still provisional, undecided or non-comparable is not answered: broad coverage is unfinished,
    # so no boundary may be concluded yet and the pass must not step past it.
    unsettled_targets = [target for target in targets if target not in ready]
    coverage_open = bool(unsettled_targets) or any(
        action['reason'] == 'target' for action in creates)

    sampled = {int(value): point for value, point in measurements.items()
               if _point_ready(point, minimum)}
    sampled_coverage = all(target in sampled for target in targets)
    if not pairs and sampled_coverage and not creates:
        # Nothing is bracketed, so every measured point agrees with the parent. Probe the interior
        # around the best measured mean (the non-monotone case) and step outward from the extreme
        # point in the requested direction, clamped to the axis's legal bounds. One interior probe
        # per pass leaves the outward step its own slot.
        probes = [probe for probe in _interior_probes(sampled, baseline, resolution)
                  if not ((program.get('direction') == 'down' and probe > baseline)
                          or (program.get('direction') == 'up' and probe < baseline))]
        for probe in probes[:1]:
            add(probe, 'interior')
        step = max(1, int(program.get('step') or default_step(baseline)))
        if program.get('direction') in ('down', 'both'):
            floor = min(list(ready) + [baseline]) if ready else baseline
            floor_limit = low if low is not None else 0
            if floor > floor_limit:
                add(max(floor_limit, floor - step), 'expand-down')
        if program.get('direction') in ('up', 'both'):
            ceiling = max(list(ready) + [baseline]) if ready else baseline
            if high is None or ceiling < high:
                add(ceiling + step if high is None else min(high, ceiling + step), 'expand-up')

    transitions = [dict(low=min(left, right), high=max(left, right), resolution=abs(right - left),
                        resolved=abs(right - left) <= resolution)
                   for left, right in pairs]
    unsettled_points = set(point_values(program)) - set(ready)
    if pairs and not coverage_open and not unsettled_points and not creates and all(item['resolved'] for item in transitions):
        best = transitions[0]
        resolved = dict(low=best['low'], high=best['high'], resolution=best['resolution'])
        status = 'done'

    return dict(creates=creates, extend=extend, status=status, bracket=bracket, resolved=resolved,
                readyValues=sorted(ready), parentAnchor=baseline, transitions=transitions,
                coverageComplete=not coverage_open)


def interaction_plan(program, measurements, partner):
    """A capped two-axis grid spec once a single-axis boundary is bracketed, else None.

    A boundary is the only evidence that a second axis might compensate, so the grid is never spent
    until one is bracketed. `partner` is `{axis, baseline, step}` for the second axis (the program's
    declared `axis2`, else the caller's next priority axis); the grid is the cross product of the
    bracket edges with a three-point ladder on that axis, capped by `maxInteractionCells`.
    """
    budget = program.get('budget') or DEFAULT_BUDGET
    if children_left(program) <= 0 or not partner:
        return None
    minimum = int(budget.get('minSeeds', MIN_POINT_SEEDS))
    ready = _ready_measurements(measurements, minimum)
    pairs = brackets(program, ready)
    if not pairs:
        return None
    left, right = pairs[0]
    axis2 = partner.get('axis')
    if axis2 is None or axis2 == program['axis']:
        return None
    base2 = int(partner['baseline'])
    step2 = max(1, int(partner.get('step') or default_step(base2)))
    values2 = sorted({max(0, base2 - step2), base2, base2 + step2})
    values = sorted({int(left), int(right)})
    cells = min(int(budget.get('maxInteractionCells', DEFAULT_BUDGET['maxInteractionCells'])),
                children_left(program))
    grid = []
    for first in values:
        for second in values2:
            if len(grid) >= cells:
                break
            grid.append(dict(value=first, value2=second))
    return dict(axis2=axis2, axis2Label=axis_label(axis2), values=values, values2=values2, cells=grid)


def summarise_rows(rows, reference_series=None):
    """Reported evidence for one tested point.

    Counts and outcomes, the mean earned chests, the mean opportunity a loss reached, the win rate
    with its Wilson interval, and - when a reference series is supplied - the paired difference against
    the frozen parent over the ordinals both measured. Nothing is substituted for an unresolved run.
    """
    from strategy_optimizer import chest_count, potential_chests, wilson

    series = strategy_probe.chest_series(rows)
    summary = strategy_probe.summarise_point(series)
    runs = len(rows)
    resolved = [row for row in rows if not row.get('censored') and row.get('verdict') in (1, 2)]
    wins = sum(1 for row in resolved if row['verdict'] == 1)
    losses = sum(1 for row in resolved if row['verdict'] == 2)
    earned = [chest_count(row)[0] for row in rows]
    earned = [value for value in earned if value is not None]
    potential = [potential_chests(row) for row in rows
                 if row.get('verdict') == 2 and not row.get('censored')]
    potential = [value for value in potential if value is not None]
    delta = (strategy_probe.paired_delta(series, reference_series) if reference_series else None)
    earned_sd = statistics.stdev(earned) if len(earned) > 1 else 0.
    return dict(
        runs=runs, resolved=len(resolved), wins=wins, losses=losses, unresolved=runs - len(resolved),
        winRate=(wins / len(resolved)) if resolved else None,
        winInterval=(wilson(wins, len(resolved)) if resolved else None),
        meanEarned=(statistics.mean(earned) if earned else None),
        earnedSamples=len(earned),
        earnedSE=(earned_sd / len(earned) ** 0.5 if earned_sd else 0.),
        meanPotential=(statistics.mean(potential) if potential else None),
        potentialSamples=len(potential),
        chestMean=summary['chestMean'], chestSE=summary['chestSE'],
        chestMin=summary['chestMin'], chestMax=summary['chestMax'],
        paired=(None if delta is None else dict(
            n=delta['n'], mean=delta['mean'], se=delta['se'], t=delta['t'],
            lower=delta['lower'], upper=delta['upper'])),
        viable=(strategy_probe.paired_status(delta) if delta else None),
    )


def point_evidence(rows, reference_series=None, *, value=None, candidate_id=None, effective=None,
                   bank=None, minimum=None, summary=None):
    """`summarise_rows` plus the point's own identity and its readiness verdict."""
    minimum = MIN_POINT_SEEDS if minimum is None else int(minimum)
    # A revision-checked caller may reuse the statistical reduction while refreshing metadata.
    # Detach nested intervals so consumers cannot mutate a cached calculation.
    from copy import deepcopy
    evidence = (summarise_rows(rows, reference_series) if summary is None else deepcopy(summary))
    evidence.update(value=value, candidateId=candidate_id, effective=effective, bank=bank,
                    ready=int(evidence['runs']) >= minimum,
                    comparable=measurement_ready(evidence, minimum))
    if not evidence['comparable']:
        evidence['viable'] = None
    return evidence


def ladder(evidence_points):
    """A `strategy_probe.analyse` ladder from measured points (chests series preserved)."""
    return [dict(value=point['value'], effective=point.get('effective'), label=point.get('label'),
                 chests=point['chests'])
            for point in evidence_points]


def analyse_points(evidence_points, reference_series):
    """The honest boundary statement for a program's measured points, via the probe analyser.

    The parent-anchored reference is the frozen build's own series; `strategy_probe.analyse` only
    reports a boundary between a measured point that works and a measured point that does not, and
    never merges disconnected working points into one range.
    """
    return strategy_probe.analyse(ladder(evidence_points), reference_series)


def analyse_cells(cell_points, reference_series):
    """The two-axis interaction reading for a program's grid, via the probe grid analyser."""
    cells = [dict(v1=cell['value'], v2=cell['value2'], chests=cell['chests'],
                  effective1=cell.get('effective'), effective2=cell.get('effective2'))
             for cell in cell_points]
    return strategy_probe.analyse_grid(cells, reference_series)
