"""High-potential follow-up scheduling, and how it behaves under per-encounter focus.

Two properties, proven with the optimiser's own functions and no live worker pool:

  * a fight can hold more opportunity-bearing builds than the potential lane has places. The builds
    outside the lane had no lane place, no win and no new region, so `staged_limits` authorised
    nothing after their discovery bank even when a defeat had queued real chests. A measured loss
    opportunity at or above `POTENTIAL_FOLLOW_UP_MIN_CHESTS` now earns the ordinary bounded
    follow-up on its own, while one chest below the floor still stops after discovery - the promotion
    is a threshold, not a blanket "anything with a loss keeps running";
  * that follow-up obeys per-encounter focus. The ready list is built from the focused fight's
    candidates only, so a focused fight keeps 100% of the attempts - including the new follow-up
    seeds - and clearing the focus restores the mixed population. Focus dispatches the follow-up; it
    never lets it leak into another fight.

    python check_optimizer_potential_followup.py
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import strategy_optimizer as optimizer  # noqa: E402

EXTENDED_RUNS = optimizer.learner.EXTENDED_DISCOVERY_RUNS

FOCUS = 19
OTHER = 7


def loss_row(pending, index):
    """One resolved defeat carrying a measured opportunity, shaped like a stored run row."""
    return dict(verdict=2, censored=False, ticks=40, prizeCallbacks=pending, survivors=0,
                resourceUses=0, behavior=dict(attacks=1, heals=0, prizes=pending),
                seeds=[index, index+1], digest=f'l-{index}', elapsedSeconds=.01,
                rewardOutcome=dict(pendingChests=pending, awardedChests=0, awardedBasis=None,
                                   inventoryVerified=False, reason=None))


def loss_only(cid, pending, encounter, runs=8):
    """A build that has only ever lost: exactly the lane evidence the store's own counters write."""
    total = None
    for index in range(runs):
        total = optimizer.accumulate(total, loss_row(pending, index))
    return optimizer.lane_record(dict(id=cid, label=cid, source='mutation'), total,
                                 encounter=encounter, defeat=0)


def scheduler_limits(records):
    """The scheduler's own evidence pass: lane ranks first, then `staged_limits` per candidate.

    Mirrors the coordinator's `load_evidence`: `elite_lanes` gives every lane pool, a candidate's rank
    is its best (lowest) place across the pools, and a candidate with no place anywhere has rank
    `None`. `nd` is the discovery bank the record already holds; these fixtures only ever recorded
    discovery runs, so the merged total is that count.
    """
    lanes = optimizer.elite_lanes(records)
    ranks = {}
    for groups in lanes.values():
        for members in groups.values():
            for index, cid in enumerate(members):
                ranks[cid] = min(ranks.get(cid, 99), index)
    limits = {}
    for record in records:
        cid = record['candidate']
        limits[cid] = optimizer.staged_limits(
            record['n'], record['wins'], None, False, ranks.get(cid), record['earnedMax'],
            record['potentialMax'], record['progress'], record['source'] == 'probe', 160, True)
    return limits, ranks


def focused_view(limits, encounter_of, focus):
    """The per-candidate limits the loop hands `interleave_ready` while focused."""
    focus_ids = optimizer.encounter_scope(encounter_of, focus)
    return {cid: value for cid, value in limits.items()
            if focus_ids is None or cid in focus_ids}


def drain(limits, encounter_of, focus, workers, ordinals=None):
    """A drained multi-worker schedule: repeatedly take up to `workers` ready seeds."""
    ordinals = ordinals or {}
    reserved = set()
    dispatched = []
    while True:
        order = optimizer.interleave_ready(focused_view(limits, encounter_of, focus),
                                           ordinals, reserved)
        if not order:
            return dispatched
        for _ in range(workers):
            if not order:
                return dispatched
            seed = order.pop(0)
            reserved.add(seed)
            dispatched.append(seed)


def check_potential_follow_up(failures):
    """The floor promotes a lane-less build; one chest below it still stops."""
    checks = 0
    floor = optimizer.POTENTIAL_FOLLOW_UP_MIN_CHESTS
    # Four builds at 40 chests fill the four-member potential lane, so `mid` and `low` have no lane
    # place at all - the state a large fight produces once more than four builds show opportunity.
    records = [loss_only(f'big{i}', 40, FOCUS) for i in range(4)]
    records.append(loss_only('mid', floor, FOCUS))
    records.append(loss_only('low', floor-1, FOCUS))
    limits, ranks = scheduler_limits(records)

    if 'mid' in ranks or 'low' in ranks:
        failures.append(f'the fixture did not leave the extra builds lane-less: {ranks}')
    checks += 1
    if any(f'big{i}' not in ranks for i in range(4)):
        failures.append(f'the four biggest opportunities did not hold the potential lane: {ranks}')
    checks += 1
    if limits.get('mid') != (EXTENDED_RUNS, 0):
        failures.append(f'a lane-less build at the opportunity floor got {limits.get("mid")}, '
                        f'expected the extended discovery bank')
    checks += 1
    if limits.get('low') != (optimizer.DISCOVERY_RUNS, 0):
        failures.append(f'a build one chest below the floor got {limits.get("low")}, '
                        f'expected no follow-up')
    checks += 1
    # The second rung: once the extended bank is held, the validation bank follows, and no more.
    held = optimizer.staged_limits(EXTENDED_RUNS, 0, None, False, None, None,
                                   floor, {}, False, 160, True)
    if held != (EXTENDED_RUNS, optimizer.VALIDATION_RUNS):
        failures.append(f'the second follow-up rung is wrong: {held}')
    checks += 1
    if EXTENDED_RUNS + optimizer.VALIDATION_RUNS > optimizer.MAX_SAMPLES:
        failures.append('the staged follow-up can outgrow the per-candidate sample ceiling')
    checks += 1
    print(f'  potential follow-up budget: {checks} unit checks passed', flush=True)
    return checks


def check_focus_dispatch(failures):
    """Focus keeps every attempt - including the follow-up bank - on the focused fight."""
    checks = 0
    floor = optimizer.POTENTIAL_FOLLOW_UP_MIN_CHESTS
    records = [loss_only(f'big{i}', 40, FOCUS) for i in range(3)]
    records.append(loss_only('mid', floor, FOCUS))
    records.append(loss_only('low', floor-1, FOCUS))
    records.append(loss_only('near', 30, OTHER))
    records.append(loss_only('far_low', floor-1, OTHER))
    limits, ranks = scheduler_limits(records)
    encounter_of = {record['candidate']: record['encounterId'] for record in records}
    workers = 6

    if limits.get('mid') != (EXTENDED_RUNS, 0):
        failures.append(f"the focused fight's follow-up build was not authorised: {limits.get('mid')}")
    checks += 1

    unfocused = drain(limits, encounter_of, None, workers)
    if {encounter_of[seed[0]] for seed in unfocused} != {FOCUS, OTHER}:
        failures.append('the unfocused schedule did not mix fights')
    checks += 1

    # The follow-up build has held its extended bank, so what it is owed now is the validation bank.
    held = dict(limits)
    held['mid'] = (EXTENDED_RUNS, optimizer.VALIDATION_RUNS)
    ordinals = {('mid', 'discovery'): EXTENDED_RUNS}

    for focus in (FOCUS, OTHER):
        dispatched = drain(held, encounter_of, focus, workers, ordinals)
        if not dispatched or {encounter_of[seed[0]] for seed in dispatched} != {focus}:
            failures.append(f'focus {focus} let a non-focused attempt through')
        checks += 1
    focused = drain(held, encounter_of, FOCUS, workers, ordinals)
    mid_seeds = [seed for seed in focused if seed[0] == 'mid']
    if len(mid_seeds) != optimizer.VALIDATION_RUNS:
        failures.append(f'the focused fight did not receive the whole follow-up bank: '
                        f'{len(mid_seeds)} of {optimizer.VALIDATION_RUNS}')
    checks += 1
    if {seed[1] for seed in mid_seeds} != {'validation'}:
        failures.append('the focused follow-up dispatched something other than its validation bank')
    checks += 1
    if any(seed[0] in ('near', 'far_low') for seed in focused):
        failures.append('another fight was dispatched while a fight was focused')
    checks += 1
    # A build below the floor is still dispatched inside focus - focus is not a filter on evidence -
    # but it is owed nothing past its discovery bank.
    low_seeds = [seed for seed in focused if seed[0] == 'low']
    if len(low_seeds) != optimizer.DISCOVERY_RUNS or {seed[1] for seed in low_seeds} != {'discovery'}:
        failures.append(f'a below-floor build was owed follow-up seeds: {low_seeds[:3]}')
    checks += 1
    # Clearing the focus restores the mixed population, follow-up bank included.
    cleared = drain(held, encounter_of, None, workers, ordinals)
    if {encounter_of[seed[0]] for seed in cleared} != {FOCUS, OTHER}:
        failures.append('clearing the focus did not restore the mixed population')
    checks += 1
    if not any(seed[0] == 'mid' for seed in cleared):
        failures.append('clearing the focus dropped the follow-up build from the schedule')
    checks += 1
    print(f'  focus interaction: {checks} unit checks passed', flush=True)
    return checks


def main():
    failures = []
    total = 0
    total += check_potential_follow_up(failures)
    total += check_focus_dispatch(failures)
    if failures:
        print(f'\noptimizer potential follow-up: {len(failures)} FAILURE(S)')
        for failure in failures:
            print(f'  - {failure}')
        raise SystemExit(1)
    print(f'\noptimizer potential follow-up: {total}/{total} checks passed')


if __name__ == '__main__':
    main()
