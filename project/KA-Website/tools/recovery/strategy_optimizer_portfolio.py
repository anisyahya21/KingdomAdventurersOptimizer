"""The focused four-objective portfolio: lanes, bounded slots, graduation and diagnostics.

One actively focused fight must improve four different things at once, and they are not the same
question:

  * `highest-earned`     - the best *converted* outcome ever observed (a lifetime maximum);
  * `highest-potential`  - the best loss-only opportunity ever built (a lifetime maximum);
  * `average-earned`     - the mean released chests over the resolved validation bank;
  * `average-potential`  - the mean loss-only opportunity over the resolved losses.

The two highest objectives may legitimately sit on a poor mean and a small sample and still deserve
repeat/tune work: a record is a claim about what once happened. The two average objectives need a
defensible minimum of resolved observations (`PORTFOLIO_MIN_MEAN_EVIDENCE`) before a mean is admitted,
and intensive allocation still prefers the comparable validation bank the existing scoring paths
already require. A maximum is only ever compared with a maximum and a mean with a mean.

This module is pure scheduling/model logic: it reads persisted evidence, never battle mechanics, and
it never touches the store's own record or run tables. `strategy_optimizer` imports it (at the bottom
of that module, to keep the one-way dependency clean) and applies the plan's bounded grants to the
staged `limits` map. It also names the bounded rotating lane candidates automatic tuning may freeze,
so a lane member is *tweaked* and not only granted repeat banks. Novel exploration, the mean-leader
lane, record repair and the focus/stop/pause controls all stay exactly where they were.
"""
from __future__ import annotations

import json

import strategy_finetune
import strategy_optimizer as so

#: The four objectives an actively focused fight's intensive capacity is shared between.
PORTFOLIO_HIGHEST_EARNED = 'highest-earned'
PORTFOLIO_HIGHEST_POTENTIAL = 'highest-potential'
PORTFOLIO_AVERAGE_EARNED = 'average-earned'
PORTFOLIO_AVERAGE_POTENTIAL = 'average-potential'
PORTFOLIO_OBJECTIVES = (PORTFOLIO_HIGHEST_EARNED, PORTFOLIO_HIGHEST_POTENTIAL,
                        PORTFOLIO_AVERAGE_EARNED, PORTFOLIO_AVERAGE_POTENTIAL)

#: How many distinct credible builds one objective studies. A breadth bound, not a quota: a lane holds
#: only builds that clear its own evidence rule, so a short lane is reported as a gap and filled from
#: the reserved challenger pool rather than padded with arbitrary low-evidence builds.
PORTFOLIO_LANE_SIZE = 10
#: The bounded intensive slots one focused fight holds. Serving them round-robin across the four
#: objectives (plus the reserved challenger and maintenance slots below) is what stops a static top
#: ten from locking newcomers out: each pass starts each lane one member further along its list.
PORTFOLIO_ACTIVE_SLOTS = 6
#: At least one active slot is kept for a new or improving challenger, so no incumbent set can exclude
#: a build that has just shown something.
PORTFOLIO_CHALLENGER_SLOTS = 1
#: At most one graduated build is rechecked per pass, so the maintenance tier cannot consume the
#: active capacity.
PORTFOLIO_RECHECK_SLOTS = 1
#: The resolved-observation floor for an *average* objective. A mean over fewer than ten resolved runs
#: is not a defensible claim; such a build is sampled as a challenger but never admitted to an average
#: lane.
PORTFOLIO_MIN_MEAN_EVIDENCE = 10
#: Graduation thresholds: enough observations, and a bounded run of paired tune comparisons with no
#: measured improvement. Stable is a maintenance state, not a claim of global optimality, and it is
#: reversible - a recheck that measures an improvement re-enters the build into the rotation.
PORTFOLIO_STABLE_MIN_RUNS = 512
PORTFOLIO_STABLE_PLATEAU = 3
#: How often (in passes) a graduated build is rechecked, and the most of one pass's portfolio budget
#: the maintenance tier may hold.
PORTFOLIO_STABLE_RECHECK_EVERY = 40
PORTFOLIO_STABLE_BUDGET_SHARE = 4
#: The least number of *fresh, objective-specific* samples a completed recheck needs before it may
#: judge the post-checkpoint validation evidence. Below it the check is `inconclusive` and the build
#: stays stable - it is never declared unchanged on evidence it did not collect.
PORTFOLIO_RECHECK_MIN_SAMPLES = PORTFOLIO_MIN_MEAN_EVIDENCE
#: The smallest relative gain a completed recheck accepts as an improvement: the new objective value
#: must beat the stored estimate by at least this fraction of it. The checkpoint stores only the
#: estimate and the sample count behind it, not the per-run spread, so this is an explicit effect-size
#: floor rather than a significance test - it refuses a fluctuation that is merely noise-sized.
PORTFOLIO_RECHECK_MARGIN = 0.05
#: The most one active build is granted per pass before a paired comparison can be made, and the hard
#: ceiling every portfolio grant is clamped to (the same cap the exploitation lanes use).
PORTFOLIO_STEP = 96
PORTFOLIO_CAP = so.FOCUS_EXPLOIT_CAP
#: The portfolio's ring-fenced share of the reservoir. It is a minority, so the existing novel
#: exploration, mean-leader and record-repair lanes all keep their own capacity.
PORTFOLIO_SHARE = 6
#: The meta key the graduation ledger lives under, and its schema version. Small and per-candidate,
#: so the coordinator can keep it in memory and write it back only when it actually changed.
PORTFOLIO_STATE_KEY = 'focusedPortfolioState'
PORTFOLIO_STATE_VERSION = 1
#: Gathering (20), Move (21) and Heart/Love (22): no recovered damage or hit formula reads them, so a
#: one-stat test that moved one would be measuring nothing. `strategy_finetune.is_combat_axis` is the
#: canonical guard; this tuple only spells the refusal out for the portfolio's own diagnostics.
PORTFOLIO_NON_TUNABLE_AXES = ('gth', 'mov', 'hrt')
#: The estimator each objective is compared with in a paired tune test.
PORTFOLIO_EARNED_OBJECTIVES = (PORTFOLIO_HIGHEST_EARNED, PORTFOLIO_AVERAGE_EARNED)
#: The longest per-candidate ledger of already-counted tune comparisons (bounded so a long search can
#: never grow it without limit).
PORTFOLIO_SEEN_COMPARISONS = 200
#: The bounded rotating automatic-tuning path. The portfolio already grants repeat banks to the top of
#: every lane, but a lane member that is never frozen as a fine-tune parent is never *tweaked*; this
#: path picks lane candidates round-robin over the four objectives (so Highest Potential and Average
#: Potential are reached, not only the mean leader and the Highest Earned holder) and freezes them on
#: the combat axes. It is a bounded *concurrent* window, so a 3.3m-run library cannot spawn a program
#: per lane member, yet a finished program releases its slot for the next candidate. The window and
#: its child budget are one mapping, owned by `strategy_finetune`; these aliases only spare the
#: portfolio's own diagnostics and checkers from naming a second source.
PORTFOLIO_TUNE_MAX_PROGRAMS = strategy_finetune.AUTO_TUNE_PORTFOLIO_MAX_PROGRAMS
PORTFOLIO_TUNE_CHILDREN_PER_PROGRAM = strategy_finetune.AUTO_TUNE_PORTFOLIO_CHILDREN_PER_PROGRAM
PORTFOLIO_TUNE_CHILDREN = strategy_finetune.AUTO_TUNE_PORTFOLIO_CHILDREN
#: The staged rotation: breadth freezes one axis on every distinct credible lane member first, then
#: depth allows a promising member a second (and further) established combat axis up to the hard cap.
PORTFOLIO_TUNE_BREADTH_AXES = strategy_finetune.AUTO_TUNE_PORTFOLIO_BREADTH_AXES
PORTFOLIO_TUNE_AXES_PER_CANDIDATE = strategy_finetune.AUTO_TUNE_PORTFOLIO_AXES_PER_CANDIDATE


def portfolio_axis_tunable(axis):
    """Whether a one-stat tune may move `axis`; Gathering/Move/Heart-Love are always refused."""
    if axis in PORTFOLIO_NON_TUNABLE_AXES:
        return False
    return bool(strategy_finetune.is_combat_axis(axis))


def portfolio_objective_value(entry, objective):
    """The primary number one objective ranks by, straight off the evidence entry."""
    if objective == PORTFOLIO_HIGHEST_EARNED:
        return entry.get('highestEarned')
    if objective == PORTFOLIO_HIGHEST_POTENTIAL:
        return entry.get('highestPotential')
    if objective == PORTFOLIO_AVERAGE_EARNED:
        return entry.get('averageEarned')
    if objective == PORTFOLIO_AVERAGE_POTENTIAL:
        return entry.get('averagePotential')
    return None


def portfolio_objective_samples(entry, objective):
    """How many observations the objective's number actually stands on."""
    if objective == PORTFOLIO_HIGHEST_EARNED:
        return int(entry.get('highestEarnedSamples', 0) or 0)
    if objective == PORTFOLIO_HIGHEST_POTENTIAL:
        return int(entry.get('highestPotentialSamples', 0) or 0)
    if objective == PORTFOLIO_AVERAGE_EARNED:
        return int(entry.get('averageEarnedSamples', 0) or 0)
    if objective == PORTFOLIO_AVERAGE_POTENTIAL:
        return int(entry.get('averagePotentialSamples', 0) or 0)
    return 0


def portfolio_recheck_checkpoint(pass_number, entry, objective, amount, seen):
    """The evidence a periodic recheck freezes when it is scheduled.

    It records where the bank stood (`runCount`), the level it must reach before anything may be
    judged (`target`), and the objective's own estimate and sample count at that moment. The
    estimate is compared like a like-for-like (`highestEarned` with `highestEarned`, a mean with a
    mean) when the check completes, so a post-checkpoint validation run can re-enter a build even
    with no new paired tune comparison.
    """
    run_count = int(entry.get('validationRuns') or 0)
    return dict(scheduledPass=int(pass_number), runCount=run_count,
                target=min(PORTFOLIO_CAP, run_count+max(0, int(amount))),
                objective=objective,
                estimate=portfolio_objective_value(entry, objective),
                estimateSamples=portfolio_objective_samples(entry, objective),
                comparisonIds=sorted(set(seen or ())))


def portfolio_recheck_evidence(objective, estimate, estimate_samples, value, samples,
                               minimum=PORTFOLIO_RECHECK_MIN_SAMPLES,
                               margin=PORTFOLIO_RECHECK_MARGIN):
    """A conservative verdict on the validation evidence collected since a recheck checkpoint.

    `estimate`/`estimate_samples` are the snapshot the checkpoint froze and `value`/`samples` the
    current reading of the *same* objective. Only a strictly better value, standing on at least
    `minimum` fresh objective samples and better than the stored estimate by at least the `margin`
    fraction of it, is `improved`: the sample floor refuses one lucky maximum and the margin refuses
    a fluctuation that is noise-sized. Anything with too few fresh samples is `inconclusive` and
    never `no-improvement`, so a build is never declared unchanged on evidence it did not collect.

    This reads counts and an estimate, not per-run values, so it cannot separate a single outlier
    from a genuine regime change beyond the margin and floor it enforces; that limitation is why it
    only ever *re-enters* a build (which stays measured) and never deletes or demotes one.
    """
    estimate = None if estimate is None else float(estimate)
    value = None if value is None else float(value)
    fresh = int(samples or 0)-int(estimate_samples or 0)
    result = dict(objective=objective, estimate=estimate, estimateSamples=int(estimate_samples or 0),
                  value=value, samples=int(samples or 0), freshSamples=fresh,
                  minimum=int(minimum), margin=float(margin), gain=None, floor=None,
                  verdict='inconclusive')
    if objective not in PORTFOLIO_OBJECTIVES:
        result['reason'] = 'the checkpoint names no objective this portfolio compares'
        return result
    if estimate is None or value is None:
        result['reason'] = 'the checkpoint or the build carries no objective estimate to compare'
        return result
    if fresh < int(minimum):
        result['reason'] = (f'only {fresh} fresh {objective} sample(s) since the checkpoint; '
                            f'{int(minimum)} are needed to judge it')
        return result
    gain = value-estimate
    floor = float(margin)*abs(estimate)
    result.update(gain=gain, floor=floor)
    if gain <= 0 or gain <= floor:
        result['verdict'] = 'no-improvement'
        result['reason'] = (f'the {objective} estimate moved from {estimate} to {value} (gain '
                            f'{gain}) on {fresh} fresh sample(s); it did not clear the {floor} '
                            f'improvement floor')
        return result
    result['verdict'] = 'improved'
    result['reason'] = (f'the {objective} estimate improved from {estimate} to {value} on {fresh} '
                        f'fresh sample(s), clearing the {floor} floor')
    return result


def portfolio_lane_eligible(entry, objective):
    """Whether a build's evidence clears the objective's own admission rule.

    The highest objectives need the outcome to have been observed at least once - a record with a bad
    mean is exactly what they exist to study. The average objectives additionally need the minimum
    resolved sample (`PORTFOLIO_MIN_MEAN_EVIDENCE`) before a mean is a defensible claim.
    """
    value = portfolio_objective_value(entry, objective)
    if value is None:
        return False
    samples = portfolio_objective_samples(entry, objective)
    if objective in (PORTFOLIO_AVERAGE_EARNED, PORTFOLIO_AVERAGE_POTENTIAL):
        return samples >= PORTFOLIO_MIN_MEAN_EVIDENCE
    return samples >= 1


def portfolio_lane_key(entry, objective):
    """One lane's ordering: the objective's own primary number, then reliability, then sample count.

    Structural on purpose. A maximum is compared with a maximum and a mean with a mean, so the two
    kinds of objective can never be silently mixed. Reliability (the Wilson lower bound) is a
    tie-break only; it never outweighs a strictly better primary number, which is what keeps a rare
    high-outcome build selectable.
    """
    lower = (entry.get('winInterval') or [0., 1.])[0]
    value = portfolio_objective_value(entry, objective)
    samples = portfolio_objective_samples(entry, objective)
    if objective == PORTFOLIO_HIGHEST_EARNED:
        return (value, lower, samples, entry.get('highestPotential') or 0)
    if objective == PORTFOLIO_HIGHEST_POTENTIAL:
        return (value, entry.get('highestEarned') or 0, lower, samples)
    return (value, lower, samples)


def focused_portfolio_lanes(entries, representative=None, size=PORTFOLIO_LANE_SIZE):
    """The four ordered lane lists, deduplicated within each lane.

    `representative` maps a candidate to the earliest holder of its inert-stat equivalence class
    (Gathering/Move/Love erased), so a legacy inert-only twin never occupies a second slot in one lane.
    Nothing is padded: a lane shorter than `size` reports its gap and the allocator fills it from the
    reserved challenger pool.
    """
    representative = representative or {}
    lanes = {}
    for objective in PORTFOLIO_OBJECTIVES:
        ranked = sorted((entry for entry in entries if portfolio_lane_eligible(entry, objective)),
                        key=lambda entry: portfolio_lane_key(entry, objective), reverse=True)
        members, seen = [], set()
        for entry in ranked:
            group = representative.get(entry['candidate'], entry['candidate'])
            if group in seen:
                continue
            seen.add(group)
            members.append(dict(objective=objective, candidate=entry['candidate'],
                                label=entry.get('label'), source=entry.get('source'),
                                value=portfolio_objective_value(entry, objective),
                                samples=portfolio_objective_samples(entry, objective),
                                winLower=(entry.get('winInterval') or [None])[0],
                                comparable=bool(entry.get('comparable')),
                                # The reading the automatic tuner's credibility rule needs, carried
                                # on the lane member so a tuning pass never re-reads the population.
                                resolved=int(entry.get('resolved') or 0),
                                unresolved=int(entry.get('unresolved') or 0),
                                wins=int(entry.get('wins') or 0),
                                averageEarned=entry.get('averageEarned'),
                                highestPotential=entry.get('highestPotential'),
                                averagePotential=entry.get('averagePotential')))
            if len(members) >= int(size):
                break
        lanes[objective] = members
    return lanes


def portfolio_overlap(lanes):
    """`{candidate: [objectives]}` for builds more than one objective studies.

    Overlap is reported, not silently collapsed: the same build can legitimately hold a record and a
    strong mean. The allocator deduplicates it to one active slot so two lanes never spend two slots
    on one build.
    """
    seen = {}
    for objective in PORTFOLIO_OBJECTIVES:
        for entry in lanes.get(objective, ()):
            seen.setdefault(entry['candidate'], []).append(objective)
    return {cid: objectives for cid, objectives in seen.items() if len(objectives) > 1}


def portfolio_tune_targets(lanes, state, limit, exclude=()):
    """Round-robin tune candidates: one lane member per objective visit, for automatic fine-tuning.

    Returns `(targets, state)`. The ledger's `tuneRotation`/`tuneCursor` start each pass one member
    further along each lane, so a static top ten cannot pin tuning to its head and every lane -
    Highest Potential and Average Potential included - is reached over time. `exclude` holds the
    candidates that already own a program (or are otherwise unavailable), which is what makes repeated
    passes idempotent: a lane member is frozen once, then the rotation moves on. Graduated (stable)
    builds are skipped here; they are rechecked on cadence and only return to tuning on fresh evidence.
    The caller bounds `limit` by the encounter's hard program total, so no lane can explode the set.
    """
    cursor = dict(state.get('tuneCursor') or {})
    rotation = int(state.get('tuneRotation') or 0)
    members = state.get('members') or {}
    blocked = set(exclude)
    targets = []
    attempts = 0
    max_attempts = len(PORTFOLIO_OBJECTIVES)*max(1, PORTFOLIO_LANE_SIZE)
    while len(targets) < max(0, int(limit)) and attempts < max_attempts:
        objective = PORTFOLIO_OBJECTIVES[rotation % len(PORTFOLIO_OBJECTIVES)]
        rotation += 1
        attempts += 1
        lane = lanes.get(objective) or ()
        if not lane:
            continue
        start = int(cursor.get(objective) or 0)
        chosen = None
        for offset in range(len(lane)):
            index = (start+offset) % len(lane)
            entry = lane[index]
            cid = entry['candidate']
            if cid in blocked:
                continue
            if (members.get(cid) or {}).get('stable'):
                continue
            if not portfolio_tune_credible(entry, objective):
                continue
            chosen = (index, entry)
            break
        if chosen is None:
            cursor[objective] = (start+1) % len(lane)
            continue
        index, entry = chosen
        cursor[objective] = index+1
        blocked.add(entry['candidate'])
        targets.append(dict(candidate=entry['candidate'], objective=objective,
                            value=entry.get('value'), samples=entry.get('samples'),
                            label=entry.get('label'),
                            resolved=entry.get('resolved'), unresolved=entry.get('unresolved'),
                            wins=entry.get('wins'), averageEarned=entry.get('averageEarned'),
                            highestPotential=entry.get('highestPotential'),
                            averagePotential=entry.get('averagePotential')))
    state['tuneCursor'] = cursor
    state['tuneRotation'] = rotation
    return targets, state


def portfolio_tune_credible(target, objective):
    """Whether a lane member clears the automatic tuner's credibility rule for its own objective.

    The sample rule is `strategy_finetune.select_parent`'s (the canonical one): enough resolved runs
    with no unresolved run. The conversion signal is objective-specific - the earned objectives need a
    converted win, while the potential objectives read the loss-only opportunity, because a build that
    has never converted can still be the Highest Potential lane's whole point and must still be tuned.
    """
    resolved = int(target.get('resolved') or 0)
    if resolved < strategy_finetune.AUTO_TUNE_MIN_RESOLVED or int(target.get('unresolved') or 0):
        return False
    if objective in (PORTFOLIO_HIGHEST_POTENTIAL, PORTFOLIO_AVERAGE_POTENTIAL):
        return ((target.get('highestPotential') or 0) > 0
                or (target.get('averagePotential') or 0) > 0)
    return target.get('averageEarned') is not None and int(target.get('wins') or 0) > 0


def portfolio_joint_interaction(views):
    """Report each multi-axis candidate's paired two-axis interaction: scheduled grid, or the gap.

    `views` are the optimiser's own fine-tune program views (each carrying ``portfolio``, ``axis``,
    ``parent``, ``boundary`` and ``grid``). Only a candidate holding two or more single-axis programs
    can answer "do these two stats interact?", and the fine-tune engine schedules the paired grid
    itself - once a single-axis boundary is bracketed - over the program's own axis and its partner
    (``grid.axis2``). This reads what the engine actually scheduled; it never derives a joint range
    from two independent single-axis brackets. When a candidate has the axes but no scheduled grid it
    returns the exact gap (a boundary still unmeasured, or the engine had no partner/budget), so the
    diagnostic can say *why* the interaction is open instead of inventing a result.
    """
    by_candidate = {}
    for view in views or ():
        if not view.get('portfolio'):
            continue
        cid = (view.get('parent') or {}).get('candidateId')
        if cid:
            by_candidate.setdefault(cid, []).append(view)
    readings = []
    for cid in sorted(by_candidate):
        programs = by_candidate[cid]
        axes = sorted({program.get('axis') for program in programs})
        if len(axes) < 2:
            continue
        bracketed = [program for program in programs if program.get('boundary')]
        grid = next((program for program in programs
                     if (program.get('grid') or {}).get('axis2')), None)
        # A grid is only claimed once two single-axis programs have each measured a bracket, the
        # evidence the requirement names; one measured axis is not yet a pair.
        if grid is not None and len(bracketed) >= 2:
            readings.append(dict(candidate=cid, axes=axes, scheduled=True,
                                 axis=grid['axis'], axis2=grid['grid']['axis2'],
                                 interaction=grid['grid'].get('interaction'),
                                 statement=grid['grid'].get('statement'), gap=None))
        else:
            waiting = sorted({program['axis'] for program in programs
                              if not program.get('boundary')})
            readings.append(dict(
                candidate=cid, axes=axes, scheduled=False, axis=None, axis2=None,
                interaction=None, statement=None,
                gap=('the paired two-axis grid waits for a measured single-axis bracket on '
                     + ', '.join(waiting) if waiting else
                     'the fine-tune engine has not scheduled a paired two-axis grid for these axes')))
    return readings


def portfolio_challenger_candidates(entries, lanes, improvements=None, new_regions=()):
    """`(candidate, reason)` for new or improving builds not already in a lane.

    The sources are the learner's own evidence - a build that opened a strategy region nothing else
    reached, or a one-stat child that improved a lane on matched seeds - never a re-derivation. Only
    candidates present in `entries` (the focused encounter) are offered.
    """
    lane_candidates = {entry['candidate'] for objective in PORTFOLIO_OBJECTIVES
                       for entry in lanes.get(objective, ())}
    present = {entry['candidate'] for entry in entries}
    reasons, seen = [], set()
    for cid in sorted(new_regions or ()):
        if cid in present and cid not in lane_candidates and cid not in seen:
            seen.add(cid)
            reasons.append((cid, 'opened a strategy region no other build had reached'))
    for cid in sorted((improvements or {}).keys()):
        if cid in present and cid not in lane_candidates and cid not in seen:
            seen.add(cid)
            reasons.append((cid, 'a one-stat child improved a lane on matched seeds'))
    return reasons


def _portfolio_challenger_entries(entries, lanes, representative, challengers, limit):
    """The challenger pool, ordered by evidence strength, deduplicated by inert equivalence."""
    lane_candidates = {entry['candidate'] for objective in PORTFOLIO_OBJECTIVES
                       for entry in lanes.get(objective, ())}
    by_id = {entry['candidate']: entry for entry in entries}
    ranked, seen = [], set()
    for item in challengers or ():
        if isinstance(item, dict):
            cid, reason = item.get('candidate'), item.get('reason')
        elif isinstance(item, (tuple, list)) and item:
            cid, reason = item[0], (item[1] if len(item) > 1 else None)
        else:
            cid, reason = item, None
        entry = by_id.get(cid)
        if entry is None or cid in lane_candidates:
            continue
        if int(entry.get('resolved') or 0) < 1:
            continue
        group = (representative or {}).get(cid, cid)
        if group in seen:
            continue
        seen.add(group)
        ranked.append(dict(entry, challengerReason=reason))
    ranked.sort(key=lambda entry: (bool(entry.get('comparable')), int(entry.get('resolved') or 0),
                                   entry['candidate']), reverse=True)
    return ranked[:max(0, int(limit))]


def _portfolio_arm(rows, earned):
    """Paired-arm summary for one tune comparison: the objective's estimator mean and the win rate.

    A censored run is unknown and is dropped from both arms equally. For the earned objectives each
    resolved run contributes its released chests (a loss is the zero the native gate released); for the
    potential objectives only the resolved losses contribute, each its discarded opportunity. No
    maximum is read anywhere.
    """
    resolved = [row for row in rows if not row.get('censored') and row.get('verdict') in (1, 2)]
    if earned:
        values = [float(row['chests']) for row in resolved if row.get('chests') is not None]
    else:
        values = [float(row['potential']) for row in resolved
                  if row.get('verdict') == 2 and row.get('potential') is not None]
    wins = sum(1 for row in resolved if row.get('verdict') == 1)
    return dict(n=len(values), mean=(sum(values)/len(values) if values else None),
                resolved=len(resolved), wins=wins,
                winRate=(wins/len(resolved) if resolved else None))


def portfolio_tune_comparison(objective, before, after, minimum=None, identifier=None):
    """A paired-seed verdict for one one-stat tune, on the objective's own estimator.

    `before`/`after` are the aligned per-run readings of the *same* seed ordinals, each a mapping with
    `chests` (released chests; a loss is zero), `potential` (the opportunity a loss built, else None),
    `verdict` and `censored`. The earned objectives compare the paired mean chests and win rate; the
    potential objectives compare the paired loss-only opportunity and win rate. A single lucky seed
    cannot make a result `improved`: only a strictly better paired mean (with the win rate not worse
    for the earned objectives), or a strictly better conversion on an equal mean, does.
    """
    minimum = PORTFOLIO_MIN_MEAN_EVIDENCE if minimum is None else int(minimum)
    if len(before) != len(after):
        raise ValueError('a paired tune comparison needs the same number of seeds on both sides')
    earned = objective in PORTFOLIO_EARNED_OBJECTIVES
    before_arm = _portfolio_arm(before, earned)
    after_arm = _portfolio_arm(after, earned)
    n = min(before_arm['n'], after_arm['n'])
    comparison = dict(objective=objective, identifier=identifier, earned=bool(earned), paired=True,
                      samples=n, minimum=minimum, before=before_arm, after=after_arm)
    # Independent filtering would compare different seed subsets and could manufacture a gain.
    # Keep arm summaries descriptive, but never graduate a build on an incomplete comparison.
    def missing(row):
        if row.get('censored') or row.get('verdict') not in (1, 2):
            return True
        return (row.get('chests') is None if earned else
                row.get('verdict') == 2 and row.get('potential') is None)
    if any(missing(left) or missing(right) for left, right in zip(before, after)):
        comparison.update(verdict='inconclusive', incomplete=True)
        return comparison
    if n < minimum or before_arm['mean'] is None or after_arm['mean'] is None:
        comparison['verdict'] = 'inconclusive'
        return comparison
    delta = after_arm['mean']-before_arm['mean']
    win_delta = (after_arm['winRate'] or 0.)-(before_arm['winRate'] or 0.)
    comparison.update(deltaMean=delta, deltaWinRate=win_delta)
    if delta > 0 and (not earned or win_delta >= 0):
        verdict = 'improved'
    elif delta < 0:
        verdict = 'worsened'
    elif delta == 0 and win_delta > 0:
        verdict = 'improved'
    elif delta == 0 and win_delta < 0:
        verdict = 'worsened'
    else:
        verdict = 'flat'
    comparison['verdict'] = verdict
    return comparison


def portfolio_run_reading(row):
    """One stored run row reduced to the paired-comparison reading (never a maximum)."""
    chests, _basis = so.chest_count(row)
    return dict(chests=chests, potential=so.potential_chests(row),
                verdict=row.get('verdict'), censored=bool(row.get('censored')))


def portfolio_entry(record, validation=None):
    """One candidate's four-objective evidence, with the sample count behind every number.

    The highest numbers are lifetime observations (`earnedMax`, and the loss-only potential maximum)
    and deliberately carry no evidence floor beyond having been seen once: an exceptional outcome is the
    whole point. The average numbers are means over the validation bank alone - a loss contributing the
    zero its native gate released for earned, and the discarded opportunity for potential - each paired
    with the resolved count behind it. A build with no loss-only aggregate yet and no ever-converted win
    has a provably loss-only lifetime maximum, so that fallback is used and labelled by its source
    count.
    """
    validation = validation or {}
    n = int(validation.get('n', 0) or 0)
    wins = int(validation.get('wins', 0) or 0)
    losses = int(validation.get('losses', 0) or 0)
    unresolved = int(validation.get('censored', 0) or 0)
    chest_counted = int(validation.get('chestCount', 0) or 0)
    chest_sum = float(validation.get('chestSum', 0.) or 0.)
    loss_count = int(validation.get('lossCount', 0) or 0)
    loss_sum = float(validation.get('lossPotentialSum', 0.) or 0.)
    earned_sources = int(record.get('earnedCount', 0) or 0)
    highest_potential = validation.get('lossPotentialMax')
    if highest_potential is None and not earned_sources:
        # No win ever converted here, so the lifetime potential maximum can only have come from a
        # discard: it is loss-only even though the dedicated counter predates this library's runs.
        highest_potential = record.get('potentialMax')
        potential_sources = int(record.get('losses', 0) or 0)
    else:
        potential_sources = loss_count
    return dict(
        candidate=record.get('candidate'), label=record.get('label'), source=record.get('source'),
        encounterId=int(record.get('encounterId', -1)),
        highestEarned=record.get('earnedMax'), highestEarnedSamples=earned_sources,
        highestPotential=highest_potential, highestPotentialSamples=potential_sources,
        averageEarned=(chest_sum/chest_counted if chest_counted else None),
        averageEarnedSamples=chest_counted,
        averagePotential=(loss_sum/loss_count if loss_count else None),
        averagePotentialSamples=loss_count,
        wins=wins, losses=losses, resolved=wins+losses, unresolved=unresolved,
        validationRuns=n, winInterval=so.wilson(wins, n),
        comparable=bool(validation) and n >= so.VALIDATION_RUNS and unresolved == 0
        and int(validation.get('count', 0) or 0) == n)


def focused_portfolio_inputs(store, records, aggregates, focus_encounter):
    """`(entries, representative)` for `focused_portfolio`, from the store's own indexes.

    Only the focused encounter's builds are read. Nothing decodes a run row or a scenario: the
    loss-only mean and maximum come from the validation aggregate `so.accumulate` maintains, and the
    inert-equivalence classes come from the store's cached read-only index.
    """
    entries = []
    if focus_encounter is None:
        return entries, {}
    encounter = int(focus_encounter)
    for record in records:
        if int(record.get('encounterId', -1)) != encounter:
            continue
        total = aggregates.get(f"aggregate:{record['candidate']}:validation") or {}
        entries.append(portfolio_entry(record, total))
    representative, _counts = store.equivalence_view()
    return entries, representative


def _tune_definition(program):
    """Only identities and point labels affect paired evidence, not program UI/budget state."""
    return ((program.get('parent') or {}).get('candidateId'),
            tuple((value, point.get('candidateId')) for value, point in
                  sorted((program.get('points') or {}).items()) if point.get('candidateId')))


def portfolio_tune_comparisons(store, focus_encounter, minimum=None, cache=None):
    """`{candidate: [{'id', 'verdict'}, ...]}` from the focused fight's fine-tune programs.

    Each program pairs a frozen parent with its tested points; every pair is compared on the shared
    `(phase, ordinal)` seeds with `portfolio_tune_comparison`, which reads the objective's own estimator
    and never a maximum. Both the earned and the potential estimator are recorded, because a stat change
    can move either. The comparison id is the program/point/objective triple, so folding the same
    program twice can never count as two tune comparisons.
    """
    comparisons = {}
    if focus_encounter is None:
        return comparisons
    encounter = int(focus_encounter)
    cache = {} if cache is None else cache
    if cache.get('store') is not store:
        cache.clear()
        cache['store'] = store
    reading_cache = cache.setdefault('readings', {})
    program_cache = cache.setdefault('programs', {})
    pair_cache = cache.setdefault('pairs', {})
    used_candidates, used_programs, used_pairs = set(), set(), set()

    def merge(target, source):
        for member, entries in source.items():
            target.setdefault(member, []).extend(dict(entry) for entry in entries)

    def readings(cid):
        used_candidates.add(cid)
        revision = store.run_revision(cid)
        previous = reading_cache.get(cid)
        if previous and previous[0] == revision:
            return previous[1]
        from strategy_evidence import indexed
        rows = {(phase, ordinal): portfolio_run_reading(row)
                for phase, ordinal, row in indexed(store.db, cid)}
        reading_cache[cid] = (revision, rows)
        return rows

    for program_id, program in sorted((store.get('fineTunePrograms') or {}).items()):
        if int(program.get('encounterId', -1)) != encounter:
            continue
        parent_id = (program.get('parent') or {}).get('candidateId')
        if not parent_id:
            continue
        used_programs.add(program_id)
        point_ids = [point.get('candidateId') for point in
                     (program.get('points') or {}).values() if point.get('candidateId')]
        definition = _tune_definition(program)
        used_pairs.update((program_id, value) for value, _cid in definition[1])
        signature = (definition, minimum,
                     tuple(sorted((cid, store.run_revision(cid))
                                  for cid in {parent_id, *point_ids})))
        previous = program_cache.get(program_id)
        if previous and previous[0] == signature:
            merge(comparisons, previous[1])
            used_candidates.update((parent_id, *point_ids))
            continue
        contribution = {}
        used_candidates.update((parent_id, *point_ids))
        for value, point in sorted((program.get('points') or {}).items()):
            cid = point.get('candidateId')
            if not cid:
                continue
            pair_key = (program_id, value)
            pair_signature = (parent_id, cid, store.run_revision(parent_id),
                              store.run_revision(cid), minimum)
            prior_pair = pair_cache.get(pair_key)
            if prior_pair and prior_pair[0] == pair_signature:
                merge(contribution, prior_pair[1])
                continue
            paired = {}
            parent_rows = readings(parent_id)
            child_rows = readings(cid)
            shared = sorted(set(parent_rows) & set(child_rows))
            if not shared:
                pair_cache[pair_key] = (pair_signature, paired)
                continue
            before = [parent_rows[key] for key in shared]
            after = [child_rows[key] for key in shared]
            for objective in (PORTFOLIO_AVERAGE_EARNED, PORTFOLIO_AVERAGE_POTENTIAL):
                verdict = portfolio_tune_comparison(
                    objective, before, after, minimum=minimum,
                    identifier=f'{program_id}:{value}:{objective}')
                if verdict['verdict'] == 'inconclusive':
                    continue
                for member in (cid, parent_id):
                    paired.setdefault(member, []).append(
                        dict(id=verdict['identifier'], verdict=verdict['verdict'],
                             objective=objective, samples=verdict['samples']))
            pair_cache[pair_key] = (pair_signature, paired)
            merge(contribution, paired)
        program_cache[program_id] = (signature, contribution)
        merge(comparisons, contribution)
    for cid in set(reading_cache)-used_candidates:
        del reading_cache[cid]
    for program_id in set(program_cache)-used_programs:
        del program_cache[program_id]
    for pair_key in set(pair_cache)-used_pairs:
        del pair_cache[pair_key]
    return comparisons


def portfolio_state(state=None):
    """The graduation ledger, normalised and version-checked into pure, persistable data."""
    state = dict(state or {})
    if int(state.get('version') or 0) < PORTFOLIO_STATE_VERSION:
        state = dict(version=PORTFOLIO_STATE_VERSION, passNumber=0, rotation=0,
                     laneCursor={}, members={})
    state['version'] = PORTFOLIO_STATE_VERSION
    state['passNumber'] = int(state.get('passNumber') or 0)
    state['rotation'] = int(state.get('rotation') or 0)
    cursor = dict(state.get('laneCursor') or {})
    for objective in PORTFOLIO_OBJECTIVES:
        cursor[objective] = int(cursor.get(objective) or 0)
    state['laneCursor'] = cursor
    state['members'] = {cid: dict(member) for cid, member in (state.get('members') or {}).items()}
    return state


def _portfolio_member(row):
    """A fresh graduation ledger row for one candidate."""
    return dict(observations=int(row.get('observations') or 0),
                comparisons=int(row.get('comparisons') or 0),
                noImprove=int(row.get('noImprove') or 0),
                stable=bool(row.get('stable')),
                graduatedPass=row.get('graduatedPass'),
                lastRecheckPass=row.get('lastRecheckPass'),
                objective=row.get('objective'), seen=list(row.get('seen') or []),
                recheck=dict(row['recheck']) if row.get('recheck') else None)


def focused_portfolio(entries, state=None, pass_number=None, budget=0, representative=None,
                      challengers=(), comparisons=None, active_slots=PORTFOLIO_ACTIVE_SLOTS,
                      challenge_slots=PORTFOLIO_CHALLENGER_SLOTS, step=PORTFOLIO_STEP,
                      encounter_id=None):
    """One pass of the four-objective focused portfolio: lanes, slots, graduation, diagnostics.

    Returns `(plan, state)`. `plan` is the bounded allocation the scheduler applies - at most
    `active_slots` intensive grants spread round-robin over the four objective lanes, one reserved
    challenger slot, and at most one maintenance recheck drawn from a ring-fenced minority of the
    budget - plus transparent diagnostics. `state` is the graduation ledger to persist; both are pure
    functions of the inputs, so the same evidence and ledger always produce the same plan.

    `challengers` is an iterable of `(candidate, reason)` or `{'candidate','reason'}` and fills the
    reserved slot even when every lane is full. `comparisons` is `{candidate: [{'id','verdict'}...]}`
    of paired, mean-based tune results; ids are remembered so folding the same comparison twice is
    idempotent and the plateau is a bounded count of *distinct* comparisons. A graduated build's
    periodic recheck completes on a fresh, post-checkpoint tune comparison *or* on the objective's
    own post-checkpoint validation evidence once the granted bank drains, so a stable build that only
    receives new validation runs can still be re-entered; a recheck that cannot be granted a positive
    bank is deferred without a pending checkpoint.

    `budget` bounds the whole pass, maintenance included: the maintenance share (at most
    `1/PORTFOLIO_STABLE_BUDGET_SHARE` of the pass) is drawn from the same allowance rather than added
    to it, and the active allocation is capped at whatever the maintenance tier left, so
    `plan['granted']` can never exceed `plan['budget']` and the reserved challenger slot is served
    first from the active remainder.
    """
    entries = [entry for entry in entries if entry.get('candidate')]
    representative = representative or {}
    by_id = {entry['candidate']: entry for entry in entries}
    lanes = focused_portfolio_lanes(entries, representative)
    overlap = portfolio_overlap(lanes)
    state = portfolio_state(state)
    if pass_number is None:
        pass_number = int(state.get('passNumber') or 0)+1
    pass_number = int(pass_number)
    members = {cid: _portfolio_member(row) for cid, row in state['members'].items() if cid in by_id}
    comparisons = comparisons or {}

    # 1. Fold in this pass's distinct paired tune comparisons.
    for cid, items in comparisons.items():
        if cid not in by_id:
            continue
        row = members.setdefault(cid, _portfolio_member({}))
        seen, seen_set = row['seen'], set(row['seen'])
        for item in items:
            if isinstance(item, dict):
                key, verdict = item.get('id'), item.get('verdict')
            elif isinstance(item, (tuple, list)) and len(item) == 2:
                key, verdict = item
            else:
                key, verdict = None, item
            if verdict not in ('improved', 'flat', 'worsened'):
                continue
            if key is not None:
                if key in seen_set:
                    continue
                seen_set.add(key)
                seen.append(key)
            row['comparisons'] += 1
            row['noImprove'] = 0 if verdict == 'improved' else row['noImprove']+1
        row['seen'] = seen[-PORTFOLIO_SEEN_COMPARISONS:]

    # 2. Observe every lane member's current resolved depth; a build newly reaching a lane is tracked.
    lane_membership = {}
    for objective in PORTFOLIO_OBJECTIVES:
        for entry in lanes[objective]:
            lane_membership.setdefault(entry['candidate'], []).append(objective)
    for cid, objectives in lane_membership.items():
        row = members.setdefault(cid, _portfolio_member({}))
        row['observations'] = int(by_id[cid].get('resolved') or 0)
        if not row.get('objective'):
            row['objective'] = objectives[0]

    # 3. Graduation: enough observations and a bounded plateau of distinct tune comparisons.
    graduated = []
    for cid, row in members.items():
        if row['stable']:
            continue
        if (row['observations'] >= PORTFOLIO_STABLE_MIN_RUNS
                and row['comparisons'] >= PORTFOLIO_STABLE_PLATEAU
                and row['noImprove'] >= PORTFOLIO_STABLE_PLATEAU):
            row['stable'] = True
            row['graduatedPass'] = pass_number
            row['lastRecheckPass'] = pass_number
            graduated.append(cid)

    # 4. Recheck: at most one newly-due graduated build is scheduled per pass, from the maintenance
    #    minority of the *same* pass budget (never added on top of it). Scheduling records a
    #    checkpoint - the run count, the objective estimate and its sample count, and the comparison
    #    ids at that moment - and a scheduled check must *finish collecting fresh evidence* before it
    #    can judge anything. A check completes only on evidence that appeared after the checkpoint: a
    #    fresh paired tune comparison, or, once the granted bank has drained, the objective's own
    #    post-checkpoint validation estimate. A historical improvement recorded before graduation can
    #    never re-enter the build, and a check is never called complete merely because it was
    #    scheduled. A recheck that cannot receive a positive grant is deferred without a pending
    #    checkpoint, so a zero-width window can never sit pending forever.
    maintenance = []
    recheck_grants = []
    recheck_step = max(0, int(step))
    total_budget = max(0, int(budget))
    stable_budget = (total_budget//PORTFOLIO_STABLE_BUDGET_SHARE
                     if PORTFOLIO_STABLE_BUDGET_SHARE else 0)
    stable_left = stable_budget

    def grantable(entry):
        """The positive bank a recheck may still draw, or 0 when room or budget is exhausted."""
        room = max(0, PORTFOLIO_CAP-int(entry.get('validationRuns') or 0))
        return min(recheck_step, stable_left, room) if stable_left > 0 else 0

    def fresh_comparisons(cid):
        """Verdicts of this pass's comparisons whose ids are not in the pending checkpoint."""
        checkpoint = members[cid].get('recheck') or {}
        known = set(checkpoint.get('comparisonIds') or ())
        verdicts = []
        for item in comparisons.get(cid) or ():
            if isinstance(item, dict):
                key, verdict = item.get('id'), item.get('verdict')
            elif isinstance(item, (tuple, list)) and len(item) == 2:
                key, verdict = item
            else:
                key, verdict = None, item
            if key is None or key in known:
                continue
            verdicts.append(verdict)
        return verdicts

    def reenter(cid, row, current, reason):
        row['stable'] = False
        row['graduatedPass'] = None
        row['noImprove'] = 0
        row['recheck'] = None
        maintenance.append(dict(candidate=cid, tier='maintenance', current=current,
                                target=min(PORTFOLIO_CAP, current+recheck_step), granted=0,
                                outcome='reentered', phase='completed', reason=reason))

    # (a) Progress a pending check before scheduling another: a fresh comparison can re-enter the
    #     build, and a bank that has drained is judged on the objective's own post-checkpoint
    #     validation evidence. A check that has collected nothing yet stays pending (its bank is
    #     topped up silently, so it does not consume the single diagnostic maintenance slot).
    pending = [cid for cid, row in members.items() if row['stable'] and row.get('recheck')]
    pending.sort(key=lambda cid: (int((members[cid].get('recheck') or {})
                                     .get('scheduledPass') or 0), cid))
    for cid in pending:
        row = members[cid]
        checkpoint = dict(row.get('recheck') or {})
        entry = by_id.get(cid) or {}
        current = int(entry.get('validationRuns') or 0)
        objective = checkpoint.get('objective') or row.get('objective')
        # A checkpoint whose target never rose above its own run count (written before a positive
        # grant was required, or with no room left under the cap) can collect nothing. Drop it
        # instead of leaving a zero-width window pending forever; the build stays stable and due.
        if int(checkpoint.get('target') or 0) <= int(checkpoint.get('runCount') or 0):
            row['recheck'] = None
            maintenance.append(dict(candidate=cid, tier='maintenance', current=current,
                                    target=current, granted=0, outcome='deferred', phase='deferred',
                                    reason=('the scheduled recheck held no positive grant window; '
                                            'it is deferred and stays due for a later pass')))
            continue
        verdicts = fresh_comparisons(cid)
        drained = current >= int(checkpoint.get('target') or 0)
        if 'improved' in verdicts:
            reenter(cid, row, current,
                    ('a periodic recheck measured a fresh post-checkpoint tune improvement; the '
                     'build re-enters the rotation'))
            continue
        if verdicts:
            row['recheck'] = None
            row['lastRecheckPass'] = pass_number
            maintenance.append(dict(candidate=cid, tier='maintenance', current=current,
                                    target=min(PORTFOLIO_CAP, current+recheck_step), granted=0,
                                    outcome='held', phase='completed',
                                    reason=('the periodic recheck measured a fresh comparison with '
                                            'no improvement; the build stays stable')))
            continue
        if drained:
            evidence = portfolio_recheck_evidence(
                objective, checkpoint.get('estimate'), checkpoint.get('estimateSamples'),
                portfolio_objective_value(entry, objective),
                portfolio_objective_samples(entry, objective))
            if evidence['verdict'] == 'improved':
                reenter(cid, row, current, evidence['reason'])
                continue
            if evidence['verdict'] == 'no-improvement':
                row['recheck'] = None
                row['lastRecheckPass'] = pass_number
                maintenance.append(dict(candidate=cid, tier='maintenance', current=current,
                                        target=min(PORTFOLIO_CAP, current+recheck_step), granted=0,
                                        outcome='held', phase='completed', evidence=evidence,
                                        reason=('the periodic recheck finished collecting fresh '
                                                'validation evidence with no measured improvement; '
                                                'the build stays stable')))
                continue
            # Inconclusive: no valid new sample to judge. Never declare success; keep the build
            # stable and honest, and either collect another window or defer without a checkpoint.
            amount = grantable(entry) if objective in PORTFOLIO_OBJECTIVES else 0
            if amount > 0:
                stable_left -= amount
                row['recheck'] = portfolio_recheck_checkpoint(pass_number, entry, objective,
                                                              amount, row.get('seen'))
                recheck_grants.append(dict(candidate=cid, tier='maintenance', current=current,
                                           target=row['recheck']['target'], granted=amount,
                                           outcome='inconclusive', phase='collecting',
                                           evidence=evidence, reason=evidence['reason']))
            else:
                row['recheck'] = None
                maintenance.append(dict(candidate=cid, tier='maintenance', current=current,
                                        target=current, granted=0, outcome='inconclusive',
                                        phase='deferred', evidence=evidence,
                                        reason=('the periodic recheck found no valid new sample '
                                                'and cannot collect more; '+evidence['reason'])))
            continue
        amount = grantable(entry)
        if amount > 0:
            stable_left -= amount
            recheck_grants.append(dict(candidate=cid, tier='maintenance', current=current,
                                       target=min(PORTFOLIO_CAP, current+amount), granted=amount,
                                       outcome='collecting', phase='collecting',
                                       reason=('the periodic recheck is still collecting fresh '
                                               'evidence')))

    # (b) Schedule at most one newly-due build, and only when it can receive a positive grant: a
    #     pass with no maintenance allowance (or a build already at the shared cap) defers instead
    #     of opening a pending checkpoint that could never drain. The cadence advances only on a real
    #     schedule, so a deferred build stays due and is retried as soon as budget allows.
    due = [cid for cid, row in members.items()
           if row['stable'] and not row.get('recheck')
           and pass_number-int(row.get('lastRecheckPass') or 0) >= PORTFOLIO_STABLE_RECHECK_EVERY]
    due.sort(key=lambda cid: (int(members[cid].get('lastRecheckPass') or 0), cid))
    scheduled = 0
    deferred = None
    for cid in due:
        if scheduled >= PORTFOLIO_RECHECK_SLOTS:
            break
        row = members[cid]
        entry = by_id.get(cid) or {}
        current = int(entry.get('validationRuns') or 0)
        amount = grantable(entry)
        if amount <= 0:
            if deferred is None:
                deferred = (cid, current)
            continue
        stable_left -= amount
        row['recheck'] = portfolio_recheck_checkpoint(pass_number, entry, row.get('objective'),
                                                      amount, row.get('seen'))
        row['lastRecheckPass'] = pass_number
        maintenance.append(dict(candidate=cid, tier='maintenance', current=current,
                                target=row['recheck']['target'], granted=amount, outcome='held',
                                phase='scheduled',
                                reason=('a periodic recheck was scheduled; it must finish '
                                        'collecting fresh evidence before it can judge the build')))
        scheduled += 1
    if scheduled == 0 and deferred is not None:
        cid, current = deferred
        maintenance.append(dict(candidate=cid, tier='maintenance', current=current, target=current,
                                granted=0, outcome='deferred', phase='deferred',
                                reason=('the periodic recheck cannot be granted a positive bank from '
                                        'this pass budget or the shared cap; it is deferred without '
                                        'a pending checkpoint')))

    # 5. Active slots. The reserved challenger opportunity is filled first; the rest is served by
    #    rotating round-robin over the four objective lanes, each starting one member further along
    #    its list than last pass, so no static top ten can permanently exclude a newcomer. The active
    #    pool is what the maintenance tier left of the same pass budget, so the reserved challenger
    #    slot can never be starved by a grant that was added beyond the budget.
    maintenance_granted = sum(int(decision.get('granted') or 0)
                              for decision in maintenance+recheck_grants)
    active_budget = max(0, total_budget-maintenance_granted)
    cap = max(0, min(int(active_slots), PORTFOLIO_ACTIVE_SLOTS))
    requests, used_groups = [], set()
    challenger_limit = max(0, min(int(challenge_slots), PORTFOLIO_CHALLENGER_SLOTS, cap))
    for entry in _portfolio_challenger_entries(entries, lanes, representative, challengers, cap):
        if sum(1 for request in requests if request['tier'] == 'challenger') >= challenger_limit:
            break
        requests.append(dict(candidate=entry['candidate'], objective=None, tier='challenger',
                             value=None, samples=int(entry.get('resolved') or 0),
                             reason=entry.get('challengerReason')
                             or 'reserved challenger slot: a new or improving build'))
        used_groups.add(representative.get(entry['candidate'], entry['candidate']))

    rotation = int(state.get('rotation') or 0)
    cursor = dict(state.get('laneCursor') or {})
    remaining = max(0, cap-len(requests))
    attempts = 0
    max_attempts = len(PORTFOLIO_OBJECTIVES)*max(1, PORTFOLIO_LANE_SIZE)
    while remaining > 0 and attempts < max_attempts:
        objective = PORTFOLIO_OBJECTIVES[rotation % len(PORTFOLIO_OBJECTIVES)]
        rotation += 1
        attempts += 1
        lane = lanes[objective]
        if not lane:
            continue
        start = int(cursor.get(objective) or 0)
        picked = None
        for offset in range(len(lane)):
            index = (start+offset) % len(lane)
            entry = lane[index]
            cid = entry['candidate']
            group = representative.get(cid, cid)
            if members.get(cid, {}).get('stable'):
                continue
            if cid in {request['candidate'] for request in requests} or group in used_groups:
                continue
            picked = (index, entry)
            break
        if picked is None:
            continue
        index, entry = picked
        cursor[objective] = index+1
        used_groups.add(representative.get(entry['candidate'], entry['candidate']))
        requests.append(dict(candidate=entry['candidate'], objective=objective, tier='lane',
                             value=entry.get('value'), samples=entry.get('samples'),
                             reason=objective+' rotation slot ('+('comparable'
                             if entry.get('comparable') else 'screening')+')'))
        remaining -= 1

    active, left = [], active_budget
    for request in requests:
        entry = by_id.get(request['candidate']) or {}
        current = int(entry.get('validationRuns') or 0)
        decision = dict(request)
        decision['current'] = current
        decision['target'] = min(PORTFOLIO_CAP, current+max(0, int(step)))
        amount = min(max(0, int(step)), left, decision['target']-current)
        decision['granted'] = amount
        left -= amount
        decision['limit'] = decision['target']
        active.append(decision)

    state['members'] = members
    state['rotation'] = rotation
    state['laneCursor'] = cursor
    state['passNumber'] = pass_number
    plan = dict(
        passNumber=pass_number, lanes=lanes,
        laneSizes={objective: len(lanes[objective]) for objective in PORTFOLIO_OBJECTIVES},
        gaps={objective: max(0, PORTFOLIO_LANE_SIZE-len(lanes[objective]))
              for objective in PORTFOLIO_OBJECTIVES},
        overlap=overlap, active=active,
        challenger=[decision for decision in active if decision['tier'] == 'challenger'],
        maintenance=maintenance, graduated=graduated,
        stable=sorted(cid for cid, row in members.items() if row['stable']),
        rotation=rotation, laneCursor=cursor,
        encounterId=encounter_id,
        budget=total_budget, granted=sum(d['granted'] for d in active)+maintenance_granted,
        maintenanceBudget=stable_budget,
        maintenanceGranted=maintenance_granted,
        recheckGrants=recheck_grants,
        minMeanEvidence=PORTFOLIO_MIN_MEAN_EVIDENCE, laneSize=PORTFOLIO_LANE_SIZE,
        activeSlots=cap, challengerSlots=challenger_limit,
        stableMinRuns=PORTFOLIO_STABLE_MIN_RUNS, stablePlateau=PORTFOLIO_STABLE_PLATEAU,
        recheckEvery=PORTFOLIO_STABLE_RECHECK_EVERY)
    return plan, state


def portfolio_limits(limits, plan):
    """Raise the validation limits of a portfolio plan's grants, clamped to the shared cap."""
    out = dict(limits)
    decisions = (list((plan or {}).get('active') or ())
                 + list((plan or {}).get('maintenance') or ())
                 + list((plan or {}).get('recheckGrants') or ()))
    for decision in decisions:
        cid = decision.get('candidate')
        granted = int(decision.get('granted') or 0)
        if not cid or granted <= 0 or cid not in out:
            continue
        dlimit, vlimit = out[cid]
        out[cid] = (dlimit, min(PORTFOLIO_CAP,
                                max(int(vlimit), int(decision.get('current') or 0)+granted)))
    return out


def portfolio_tune_signature(store, focus_encounter):
    """A cheap signature of the focused fight's fine-tune banks, for caching the comparisons.

    Only matching program definitions and committed run revisions are read, so a caller can tell
    whether a paired comparison could have changed without decoding a stored run row. Returns
    `None` when nothing is focused or no program matches.
    """
    if focus_encounter is None:
        return None
    encounter = int(focus_encounter)
    programs = store.get('fineTunePrograms') or {}
    parts = []
    for program_id, program in sorted(programs.items()):
        if int(program.get('encounterId', -1)) != encounter:
            continue
        parts.append(('program', program_id, _tune_definition(program)))
        ids = [(program.get('parent') or {}).get('candidateId')]
        ids.extend(point.get('candidateId') for point in (program.get('points') or {}).values())
        for cid in ids:
            if not cid:
                continue
            # The *milestone* revision, not the per-run one: this key decides whether a 0.6-second
            # comparison is recomputed, and filling a bank with 63 more seeds does not change what the
            # comparison says. Crossing a bank/rung does, and so does a candidate appearing or leaving.
            revision = (store.milestone_revision(cid) if hasattr(store, 'milestone_revision')
                        else store.run_revision(cid))
            parts.append(('candidate', cid, revision))
    return (encounter, tuple(sorted(parts)))
