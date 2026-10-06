"""Deterministic checks for the focused four-objective portfolio and its planner wiring.

One actively focused fight shares its bounded intensive capacity between four objectives - the best
converted outcome, the best loss-only opportunity, the mean released chests and the mean discarded
opportunity - plus a reserved challenger slot and a bounded maintenance tier. This harness pins the
whole layer without running a battle:

  * the four lanes rank by their own estimator (a maximum with a maximum, a mean with a mean), carry
    the loss-only sample count, and admit a mean only at or above `PORTFOLIO_MIN_MEAN_EVIDENCE`;
  * a weak-mean record holder keeps a repeat/tune path, overlapping lanes collapse to one slot, the
    reserved challenger slot is filled even when all four lanes are full, and six active slots rotate
    round-robin so no lane member starves;
  * graduation needs both the observation floor and a bounded plateau of *distinct* flat comparisons;
    the maintenance tier is capacity-bounded, rechecks periodically, and a recheck that measures a
    fresh post-checkpoint improvement - a paired tune comparison or the objective's own validation
    estimate - returns the build to the intensive rotation; a recheck with no positive grant defers
    without a pending checkpoint, and too little fresh evidence is inconclusive, never a success;
  * the estimator never reads a maximum as statistical proof, the potential arm counts only resolved
    losses, and non-combat axes are refused;
  * the planner resolves every portfolio name through the module it imports (module `__getattr__` does
    not resolve function-body globals), raises only validation limits so an existing record/mean paired
    bank is never lowered, persists the graduation ledger so the pass number and rotation advance, and
    hands the store's genuine next ordinals to a ready list whose proposal capacity is unchanged.

    python check_optimizer_portfolio.py
"""
import copy
import json
import re
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import search_contract  # noqa: E402
import strategy_finetune as finetune  # noqa: E402
import strategy_learner as learner  # noqa: E402
import strategy_optimizer as optimizer  # noqa: E402
from strategy_optimizer_adapter import (default_scenario, provenance, stats,  # noqa: E402
                                        validate_scenario)

portfolio = optimizer.portfolio
OBJECTIVES = portfolio.PORTFOLIO_OBJECTIVES


def make_entry(cid, he=None, hes=0, hp=None, hps=0, ae=None, aes=0, ap=None, aps=0,
               wins=0, losses=0, n=0, resolved=None, comparable=False):
    """One portfolio evidence entry, shaped exactly as `portfolio_entry` builds it."""
    return dict(candidate=cid, label=cid, source='check', encounterId=19,
                highestEarned=he, highestEarnedSamples=hes,
                highestPotential=hp, highestPotentialSamples=hps,
                averageEarned=ae, averageEarnedSamples=aes,
                averagePotential=ap, averagePotentialSamples=aps,
                wins=wins, losses=losses,
                resolved=(wins + losses if resolved is None else resolved),
                unresolved=0, validationRuns=n, winInterval=(0., 1.), comparable=comparable)


def lane_fixture(count=12):
    """A population eligible for all four lanes, each build strictly weaker than the last."""
    return [make_entry(f'bulk{i}', he=100 - i, hes=64, hp=90 - i, hps=64,
                       ae=9.0 - i * 0.1, aes=64, ap=8.0 - i * 0.1, aps=64,
                       wins=32, losses=32, n=64) for i in range(count)]


def lane_candidates(lanes, objective):
    return [member['candidate'] for member in lanes[objective]]


def outcome(ordinal, verdict=1, chests=0, potential=0):
    """One accepted result, shaped like the runner's own record."""
    return dict(verdict=verdict, censored=False, ticks=40, prizeCallbacks=potential,
                survivors=1 if verdict == 1 else 0, resourceUses=0,
                behavior=dict(attacks=1, heals=0, prizes=potential),
                seeds=[ordinal, 100 + ordinal], digest=f'run-{ordinal}', elapsedSeconds=.01,
                rewardOutcome=dict(awardedChests=chests, pendingChests=potential,
                                   awardedBasis='check', inventoryVerified=False))


def variant(base, unit, delta):
    scenario = copy.deepcopy(base)
    scenario['ownUnits'][unit]['parameters'][13]['rawValue'] += delta
    return validate_scenario(scenario)


def open_store():
    """A bare temporary sqlite store (a sandbox may refuse a fresh subdirectory)."""
    handle = tempfile.NamedTemporaryFile(suffix='.sqlite', delete=False)
    handle.close()
    return optimizer.Store(Path(handle.name), provenance())


def close_store(store):
    path = store.path
    store.close()
    for suffix in ('', '-wal', '-shm'):
        leftover = Path(str(path) + suffix)
        if leftover.exists():
            leftover.unlink()


def read_aggregates(store):
    return {row[0]: json.loads(row[1]) for row in store.db.execute(
        "SELECT key, value FROM meta WHERE key LIKE 'aggregate:%'")}


def drive(entries, state, budget, passes, comparisons_by_pass=None):
    """Run `passes` portfolio passes, folding the comparisons scheduled for each pass number."""
    plan = None
    firings = []
    for _ in range(passes):
        if plan is None:
            number = (int(state.get('passNumber') or 0) if state else 0) + 1
        else:
            number = plan['passNumber'] + 1
        comparisons = (comparisons_by_pass or {}).get(number)
        plan, state = portfolio.focused_portfolio(entries, state, budget=budget,
                                                  comparisons=comparisons)
        if plan['maintenance']:
            firings.append((plan['passNumber'], plan['maintenance']))
    return plan, state, firings


def check_lanes(failures):
    """Four bounded lanes, each ordered by its own estimator, with loss-only sample counts."""
    checks = 0
    entries = [
        make_entry('record-weak', he=200, hes=1, hp=5, hps=64, ae=1.0, aes=64, ap=1.0, aps=64,
                   wins=32, losses=32, n=64),
        make_entry('mean-star', he=10, hes=64, hp=6, hps=64, ae=9.0, aes=64, ap=8.0, aps=64,
                   wins=32, losses=32, n=64),
    ] + lane_fixture(12)
    lanes = portfolio.focused_portfolio_lanes(entries)
    for objective in OBJECTIVES:
        if objective not in lanes:
            failures.append(f'lane {objective} missing')
            continue
        if len(lanes[objective]) != portfolio.PORTFOLIO_LANE_SIZE:
            failures.append(f'lane {objective} holds {len(lanes[objective])} members, '
                            f'expected the top {portfolio.PORTFOLIO_LANE_SIZE}')
        checks += 1
    highest = lane_candidates(lanes, portfolio.PORTFOLIO_HIGHEST_EARNED)
    if highest[:1] != ['record-weak']:
        failures.append(f'the highest-earned lane is headed by {highest[:1]}, '
                        'expected the 99-chest record even though its mean is weak')
    checks += 1
    average = lane_candidates(lanes, portfolio.PORTFOLIO_AVERAGE_EARNED)
    if average[:1] != ['mean-star']:
        failures.append(f'the average-earned lane is headed by {average[:1]}, '
                        'expected the 9.0 mean')
    checks += 1
    if not average or 'record-weak' in average:
        failures.append('the weak-mean record holder leaked into the average-earned lane')
    checks += 1
    if 'bulk11' in highest or 'bulk11' in average:
        failures.append('the weakest build was padded into a full top-ten lane')
    checks += 1
    losses_only = make_entry('losses-only', hp=50, hps=12, ap=4.0, aps=12, wins=0, losses=12,
                             n=12)
    lanes2 = portfolio.focused_portfolio_lanes([losses_only])
    if lane_candidates(lanes2, portfolio.PORTFOLIO_HIGHEST_POTENTIAL) != ['losses-only']:
        failures.append('the loss-only maximum was not admitted to highest-potential')
    checks += 1
    if portfolio.portfolio_objective_samples(losses_only,
                                             portfolio.PORTFOLIO_HIGHEST_POTENTIAL) != 12:
        failures.append('the highest-potential sample count is not the loss count')
    checks += 1
    owner = make_entry('owner', he=70, hes=64, hp=60, hps=64, ae=7.0, aes=64, ap=6.0, aps=64,
                       wins=32, losses=32, n=64)
    twin = make_entry('twin', he=70, hes=64, hp=60, hps=64, ae=7.0, aes=64, ap=6.0, aps=64,
                      wins=32, losses=32, n=64)
    deduped = portfolio.focused_portfolio_lanes([owner, twin], {'twin': 'owner'})
    for objective in OBJECTIVES:
        members = lane_candidates(deduped, objective)
        if len(members) != 1:
            failures.append(f'inert twins were not deduplicated in {objective}: {members}')
        checks += 1
    print(f'  lanes: {checks} checks passed', flush=True)
    return checks


def check_mean_evidence_floor(failures):
    """A mean needs `PORTFOLIO_MIN_MEAN_EVIDENCE` relevant observations; a record needs one."""
    checks = 0
    nine = make_entry('nine', he=99, hes=9, hp=99, hps=9, ae=99.0, aes=9, ap=99.0, aps=9,
                      wins=5, losses=4, n=9)
    ten = make_entry('ten', ae=1.0, aes=10, ap=1.0, aps=10, wins=5, losses=5, n=10)
    for objective in (portfolio.PORTFOLIO_AVERAGE_EARNED, portfolio.PORTFOLIO_AVERAGE_POTENTIAL):
        if portfolio.portfolio_lane_eligible(nine, objective):
            failures.append(f'a mean over 9 observations was admitted to {objective}')
        checks += 1
        if not portfolio.portfolio_lane_eligible(ten, objective):
            failures.append(f'10 observations were refused from {objective}')
        checks += 1
    if portfolio.portfolio_lane_eligible(make_entry('n', wins=1, n=1),
                                         portfolio.PORTFOLIO_HIGHEST_EARNED):
        failures.append('a build with no earned maximum was admitted to highest-earned')
    checks += 1
    if not portfolio.portfolio_lane_eligible(make_entry('one', he=3, hes=1),
                                             portfolio.PORTFOLIO_HIGHEST_EARNED):
        failures.append('a one-observation record was refused from highest-earned')
    checks += 1
    print(f'  mean evidence floor: {checks} checks passed', flush=True)
    return checks


def check_loss_only(failures):
    """The portfolio's potential numbers are loss-only, even when the lifetime maximum is mixed."""
    checks = 0
    record = dict(candidate='c', label='c', source='check', encounterId=19, earnedMax=5,
                  earnedCount=40, potentialMax=999, losses=24)
    validation = dict(n=64, wins=40, losses=24, censored=0, count=64, chestCount=64,
                      chestSum=40 * 5.0, lossCount=24, lossPotentialSum=24 * 30.0,
                      lossPotentialMax=77)
    entry = portfolio.portfolio_entry(record, validation)
    if entry['highestPotential'] != 77:
        failures.append(f"highestPotential read {entry['highestPotential']}, "
                        'expected the loss-only 77')
    checks += 1
    if entry['highestPotentialSamples'] != 24:
        failures.append(f"highestPotentialSamples {entry['highestPotentialSamples']}, expected 24")
    checks += 1
    if entry['averagePotential'] != 30.0:
        failures.append(f"averagePotential {entry['averagePotential']}, expected 30.0")
    checks += 1
    # Every resolved run is in the denominator and a loss contributes the zero its gate released.
    if entry['averageEarned'] != 40 * 5.0 / 64:
        failures.append(f"averageEarned {entry['averageEarned']}, expected "
                        f"{40 * 5.0 / 64} (the 24 losses contribute zero)")
    checks += 1
    if entry['highestEarned'] != 5 or entry['highestEarnedSamples'] != 40:
        failures.append('the earned maximum or its win count is wrong')
    checks += 1
    fallback = portfolio.portfolio_entry(
        dict(candidate='d', label='d', source='check', encounterId=19, earnedMax=None,
             earnedCount=0, potentialMax=88, losses=12),
        dict(n=12, wins=0, losses=12, censored=0, count=12, chestCount=12, chestSum=0.,
             lossCount=0, lossPotentialSum=0., lossPotentialMax=None))
    if fallback['highestPotential'] != 88 or fallback['highestPotentialSamples'] != 12:
        failures.append('the never-converted loss-only fallback is wrong')
    checks += 1
    print(f'  loss-only potential: {checks} checks passed', flush=True)
    return checks


def check_overlap_dedup(failures):
    """Overlapping lanes are reported, then collapsed to one active slot."""
    checks = 0
    entries = [
        make_entry('overlap', he=80, hes=64, hp=70, hps=64, ae=8.0, aes=64, ap=7.0, aps=64,
                   wins=32, losses=32, n=64),
        make_entry('second', he=10, hes=64, hp=9, hps=64, ae=2.0, aes=64, ap=1.0, aps=64,
                   wins=32, losses=32, n=64),
    ]
    lanes = portfolio.focused_portfolio_lanes(entries)
    overlap = portfolio.portfolio_overlap(lanes)
    if len(overlap.get('overlap', [])) < 2:
        failures.append(f'the overlapping build was not reported across lanes: {overlap}')
    checks += 1
    plan, _ = portfolio.focused_portfolio(entries, None, budget=6000)
    served = [decision['candidate'] for decision in plan['active']]
    if served.count('overlap') > 1:
        failures.append('the overlapping build was granted two active slots')
    checks += 1
    if len(plan['active']) > portfolio.PORTFOLIO_ACTIVE_SLOTS:
        failures.append(f"the plan held {len(plan['active'])} active slots, over the "
                        f"{portfolio.PORTFOLIO_ACTIVE_SLOTS} bound")
    checks += 1
    print(f'  overlap dedup: {checks} checks passed', flush=True)
    return checks


def check_challenger_slot(failures):
    """A new or improving build is offered a reserved slot even when all four lanes are full."""
    checks = 0
    entries = lane_fixture(12) + [
        make_entry('newcomer', ae=0.5, aes=4, wins=2, losses=2, n=4, resolved=4)]
    lanes = portfolio.focused_portfolio_lanes(entries)
    if any(len(lanes[objective]) != portfolio.PORTFOLIO_LANE_SIZE for objective in OBJECTIVES):
        failures.append('the fixture did not fill all four lanes')
    checks += 1
    if 'newcomer' in {cid for objective in OBJECTIVES
                      for cid in lane_candidates(lanes, objective)}:
        failures.append('the newcomer unexpectedly holds a lane slot')
    checks += 1
    challengers = portfolio.portfolio_challenger_candidates(
        entries, lanes, improvements={'newcomer': {'lanes': ['earned']}})
    if [cid for cid, _ in challengers] != ['newcomer']:
        failures.append(f'the improving child was not offered as a challenger: {challengers}')
    checks += 1
    plan, _ = portfolio.focused_portfolio(entries, None, budget=6000, challengers=challengers)
    if [decision['candidate'] for decision in plan['challenger']] != ['newcomer']:
        failures.append('the reserved challenger slot was not filled with full lanes')
    checks += 1
    if not any(decision['candidate'] == 'newcomer' for decision in plan['active']):
        failures.append('the challenger never entered the active plan')
    checks += 1
    in_lane = lane_candidates(lanes, portfolio.PORTFOLIO_HIGHEST_EARNED)[0]
    offered = portfolio.portfolio_challenger_candidates(
        entries, lanes, improvements={in_lane: {'lanes': ['earned']}})
    if offered:
        failures.append(f'a build already in a lane was re-offered as a challenger: {offered}')
    checks += 1
    print(f'  challenger slot: {checks} checks passed', flush=True)
    return checks


def check_rotation(failures):
    """Six active slots rotate round-robin; the pass number advances and no lane member starves."""
    checks = 0
    entries = lane_fixture(10)
    state = None
    served, rotations = {}, []
    for expected in range(1, 9):
        plan, state = portfolio.focused_portfolio(entries, state, budget=6000)
        if plan['passNumber'] != expected:
            failures.append(f"pass number {plan['passNumber']} at expected {expected}")
        checks += 1
        if len(plan['active']) != portfolio.PORTFOLIO_ACTIVE_SLOTS:
            failures.append(f"pass {expected} served {len(plan['active'])} slots")
        checks += 1
        rotations.append(plan['rotation'])
        for decision in plan['active']:
            served.setdefault(decision['candidate'], set()).add(
                decision.get('objective') or decision['tier'])
    if rotations != sorted(rotations) or rotations[0] == rotations[-1]:
        failures.append(f'rotation did not advance monotonically: {rotations}')
    checks += 1
    starved = [entry['candidate'] for entry in entries if entry['candidate'] not in served]
    if starved:
        failures.append(f'rotation starved these lane members over 8 passes: {starved}')
    checks += 1
    roles = {role for roles in served.values() for role in roles}
    for objective in OBJECTIVES:
        if objective not in roles:
            failures.append(f'objective {objective} was never served in 8 passes')
        checks += 1
    first, _ = portfolio.focused_portfolio(entries, None, budget=6000)
    second, _ = portfolio.focused_portfolio(entries, None, budget=6000)
    if first != second:
        failures.append('focused_portfolio is not a pure function of its inputs')
    checks += 1
    print(f'  rotation and starvation: {checks} checks passed', flush=True)
    return checks


def check_graduation(failures):
    """Graduation needs the observation floor and a plateau of distinct flat comparisons."""
    checks = 0
    floor = portfolio.PORTFOLIO_STABLE_MIN_RUNS
    def flats(prefix, count):
        return {'veteran': [{'id': f'{prefix}{i}', 'verdict': 'flat'} for i in range(count)]}
    young = make_entry('veteran', he=50, hes=64, hp=40, hps=64, ae=5.0, aes=64, ap=4.0, aps=64,
                       wins=32, losses=32, n=64, resolved=floor - 1)
    plan, _ = portfolio.focused_portfolio([young], None, budget=6000, comparisons=flats('a', 3))
    if plan['stable']:
        failures.append('a build below the evidence floor graduated')
    checks += 1
    ready = make_entry('veteran', he=50, hes=600, hp=40, hps=600, ae=5.0, aes=600, ap=4.0,
                       aps=600, wins=300, losses=300, n=600, resolved=floor)
    plan, _ = portfolio.focused_portfolio([ready], None, budget=6000, comparisons=flats('b', 2))
    if plan['stable']:
        failures.append('a build with only 2 tune comparisons graduated')
    checks += 1
    plan, _ = portfolio.focused_portfolio([ready], None, budget=6000, comparisons=flats('c', 3))
    if not plan['stable']:
        failures.append('a build with enough evidence and 3 flat comparisons did not graduate')
    checks += 1
    if plan['graduated'] != ['veteran']:
        failures.append(f'graduation report {plan["graduated"]}')
    checks += 1
    plan, state = portfolio.focused_portfolio(
        [ready], None, budget=6000,
        comparisons={'veteran': [{'id': 'same', 'verdict': 'flat'}] * 3})
    if plan['stable']:
        failures.append('one comparison id repeated three times counted as a plateau')
    checks += 1
    plan, state = portfolio.focused_portfolio(
        [ready], state, budget=6000,
        comparisons={'veteran': [{'id': 'd0', 'verdict': 'flat'},
                                 {'id': 'd1', 'verdict': 'flat'},
                                 {'id': 'd2', 'verdict': 'flat'}]})
    if not plan['stable']:
        failures.append('three distinct flat comparisons did not complete the plateau')
    checks += 1
    print(f'  graduation: {checks} checks passed', flush=True)
    return checks


def check_maintenance(failures):
    """The maintenance tier is bounded, periodic, and reverses on a measured improvement."""
    checks = 0
    every = portfolio.PORTFOLIO_STABLE_RECHECK_EVERY
    share = portfolio.PORTFOLIO_STABLE_BUDGET_SHARE
    veteran = make_entry('veteran', he=50, hes=600, hp=40, hps=600, ae=5.0, aes=600, ap=4.0,
                         aps=600, wins=300, losses=300, n=600,
                         resolved=portfolio.PORTFOLIO_STABLE_MIN_RUNS)
    flat = [{'id': f'flat-{i}', 'verdict': 'flat'} for i in range(3)]
    plan, state, _ = drive([veteran], None, 6000, 1, {1: {'veteran': flat}})
    if not plan['stable']:
        failures.append('maintenance fixture failed to graduate')
        return checks
    checks += 1
    due = state['members']['veteran']['lastRecheckPass'] + every
    plan, state, firings = drive([veteran], state, 6000, every + 2)
    if [number for number, _ in firings] != [due]:
        failures.append(f'maintenance fired at {[n for n, _ in firings]}, expected only [{due}]')
        return checks
    checks += 1
    held = firings[0][1][0]
    if held['outcome'] != 'held' or held['granted'] <= 0:
        failures.append(f'the periodic recheck is not a bounded held grant: {held}')
    checks += 1
    if held['granted'] > 6000 // share:
        failures.append('the maintenance tier granted more than its bounded share')
    checks += 1
    if len(firings[0][1]) != portfolio.PORTFOLIO_RECHECK_SLOTS:
        failures.append('the pass rechecked more builds than the maintenance slot allows')
    checks += 1
    next_due = due + every
    plan, state, _ = drive([veteran], state, 6000, next_due - state['passNumber'] - 1)
    target = plan['passNumber'] + 1
    plan, state, _ = drive([veteran], state, 6000, 1,
                           {target: {'veteran': [{'id': 'improve-1', 'verdict': 'improved'}]}})
    if not plan['maintenance'] or plan['maintenance'][0]['outcome'] != 'reentered':
        failures.append(f'an improved recheck did not return the build to the rotation: '
                        f'{plan["maintenance"]}')
    checks += 1
    if plan['stable']:
        failures.append('a re-entered build stayed graduated')
    checks += 1
    plan, state, _ = drive([veteran], state, 6000, 1)
    if not any(decision['candidate'] == 'veteran' for decision in plan['active']):
        failures.append('a re-entered build was never served an intensive slot')
    checks += 1
    two = [make_entry('alpha', he=50, hes=600, hp=40, hps=600, ae=5.0, aes=600, ap=4.0, aps=600,
                      wins=300, losses=300, n=600, resolved=portfolio.PORTFOLIO_STABLE_MIN_RUNS),
           make_entry('beta', he=49, hes=600, hp=39, hps=600, ae=4.9, aes=600, ap=3.9, aps=600,
                      wins=300, losses=300, n=600, resolved=portfolio.PORTFOLIO_STABLE_MIN_RUNS)]
    comps = {cid: [{'id': f'{cid}-{i}', 'verdict': 'flat'} for i in range(3)]
             for cid in ('alpha', 'beta')}
    plan, state, _ = drive(two, None, 6000, 1, {1: comps})
    if sorted(plan['stable']) != ['alpha', 'beta']:
        failures.append('the two-build maintenance fixture failed to graduate')
    checks += 1
    plan, state, firings = drive(two, state, 6000, every + 2)
    over = [entry for entry in firings
            if len(entry[1]) != portfolio.PORTFOLIO_RECHECK_SLOTS]
    passes = [entry[0] for entry in firings]
    if over or len(passes) != len(set(passes)) or len(passes) != 2:
        failures.append(f'two due graduates shared one maintenance pass: {firings}')
    checks += 1
    if plan['maintenanceGranted'] > 6000 // share:
        failures.append('the maintenance tier exceeded its bounded capacity')
    checks += 1
    print(f'  maintenance: {checks} checks passed', flush=True)
    return checks


def check_caps_and_budget(failures):
    """Grants are clamped to the shared cap and the pass budget, and never lower a paired bank."""
    checks = 0
    cap = portfolio.PORTFOLIO_CAP
    near = make_entry('near', he=90, hes=800, hp=80, hps=800, ae=9.0, aes=800, ap=8.0, aps=800,
                      wins=400, losses=400, n=cap - 5)
    plan, _ = portfolio.focused_portfolio([near], None, budget=6000)
    decision = plan['active'][0]
    if decision['target'] != cap:
        failures.append(f"target {decision['target']} was not clamped to the cap {cap}")
    checks += 1
    if decision['granted'] != 5:
        failures.append(f"grant {decision['granted']} did not stop at the remaining cap")
    checks += 1
    limits = {'near': (learner.EXTENDED_DISCOVERY_RUNS, 3000)}
    out = portfolio.portfolio_limits(limits, plan)
    if out['near'][1] < 3000 or out['near'][0] != learner.EXTENDED_DISCOVERY_RUNS:
        failures.append(f'portfolio_limits lowered or widened an existing paired bank: {out}')
    checks += 1
    if out['near'][1] > cap:
        failures.append('portfolio_limits exceeded the shared cap')
    checks += 1
    plan, _ = portfolio.focused_portfolio(lane_fixture(12), None, budget=100)
    if plan['granted'] > 100:
        failures.append('the portfolio granted more than its pass budget')
    checks += 1
    print(f'  caps and budget: {checks} checks passed', flush=True)
    return checks


def check_axes(failures):
    """Non-combat axes are refused, and the portfolio keeps no second axis list."""
    checks = 0
    for axis in portfolio.PORTFOLIO_NON_TUNABLE_AXES:
        if portfolio.portfolio_axis_tunable(axis):
            failures.append(f'non-combat axis {axis} was accepted as tunable')
        checks += 1
        if finetune.is_combat_axis(axis):
            failures.append(f'finetune disagrees that {axis} is non-combat')
        checks += 1
    for axis in ('atk', 'def', 'hp', 'int'):
        if not portfolio.portfolio_axis_tunable(axis):
            failures.append(f'combat axis {axis} was refused')
        checks += 1
        if portfolio.portfolio_axis_tunable(axis) != finetune.is_combat_axis(axis):
            failures.append(f'the portfolio and finetune disagree on {axis}')
        checks += 1
    print(f'  axis policy: {checks} checks passed', flush=True)
    return checks


def check_estimator_hygiene(failures):
    """No maximum is statistical proof; the potential arm counts only resolved losses."""
    checks = 0
    def reading(verdict, chests, potential):
        return dict(chests=chests, potential=potential, verdict=verdict, censored=False)
    equal = [reading(1, 5, None)] * 10
    comparison = portfolio.portfolio_tune_comparison(portfolio.PORTFOLIO_AVERAGE_EARNED, equal,
                                                     list(equal))
    if comparison['verdict'] != 'flat':
        failures.append(f'equal per-run chests produced {comparison["verdict"]}, not flat')
    checks += 1
    comparison = portfolio.portfolio_tune_comparison(
        portfolio.PORTFOLIO_AVERAGE_POTENTIAL, [reading(2, 0, 1)] * 3, [reading(2, 0, 9)] * 3)
    if comparison['verdict'] != 'inconclusive':
        failures.append(f'a 3-seed comparison returned {comparison["verdict"]}, not inconclusive')
    checks += 1
    before = [reading(2, 0, 10)] * 11
    after = [reading(2, 0, 10)] * 10 + [reading(1, 99, None)]
    comparison = portfolio.portfolio_tune_comparison(portfolio.PORTFOLIO_AVERAGE_POTENTIAL,
                                                     before, after)
    if comparison['after']['n'] != 10 or comparison['after']['mean'] != 10.0:
        failures.append(f'a win polluted the loss-only potential arm: {comparison}')
    checks += 1
    comparison = portfolio.portfolio_tune_comparison(portfolio.PORTFOLIO_AVERAGE_EARNED,
                                                     [reading(1, 4, None)] * 10,
                                                     [reading(1, 4, None)] * 10)
    if comparison['before']['mean'] != 4.0:
        failures.append('the earned arm mean is not the per-run chest mean')
    checks += 1
    mixed = make_entry('m', he=99, hes=1, ae=1.0, aes=10)
    if portfolio.portfolio_objective_value(mixed, portfolio.PORTFOLIO_AVERAGE_EARNED) != 1.0:
        failures.append('average-earned did not read the mean')
    checks += 1
    if portfolio.portfolio_objective_samples(mixed, portfolio.PORTFOLIO_AVERAGE_EARNED) != 10:
        failures.append('average-earned sample count is not the mean sample count')
    checks += 1
    print(f'  estimator hygiene: {checks} checks passed', flush=True)
    return checks


def check_recheck_checkpoint(failures):
    """A scheduled recheck must collect fresh evidence; a historical improvement cannot reenter."""
    checks = 0
    every = portfolio.PORTFOLIO_STABLE_RECHECK_EVERY
    veteran = make_entry('veteran', he=50, hes=600, hp=40, hps=600, ae=5.0, aes=600, ap=4.0,
                         aps=600, wins=300, losses=300, n=600,
                         resolved=portfolio.PORTFOLIO_STABLE_MIN_RUNS)
    # A build that already measured one improvement before graduation, then plateaued.
    history = ([{'id': 'old-improve', 'verdict': 'improved'}]
               + [{'id': f'flat-{i}', 'verdict': 'flat'} for i in range(3)])
    plan, state, _ = drive([veteran], None, 6000, 1, {1: {'veteran': history}})
    if not plan['stable']:
        failures.append('recheck-checkpoint fixture failed to graduate')
        return checks
    checks += 1
    plan, state, firings = drive([veteran], state, 6000, every + 1)
    if (len(firings) != 1 or len(firings[0][1]) != portfolio.PORTFOLIO_RECHECK_SLOTS
            or firings[0][1][0].get('phase') != 'scheduled'):
        failures.append(f'the due recheck was not scheduled exactly once: {firings}')
        return checks
    checks += 1
    checkpoint = state['members']['veteran'].get('recheck')
    if not checkpoint:
        failures.append('scheduling a recheck left no checkpoint on the ledger')
    checks += 1
    if not plan['stable']:
        failures.append('scheduling a recheck alone was treated as a completed improvement')
    checks += 1
    # The pre-graduation improvement is supplied again while the check is pending: it is historical
    # evidence, not fresh post-checkpoint evidence, so it must neither reenter nor complete the check.
    plan, state, _ = drive([veteran], state, 6000, 1,
                           {plan['passNumber'] + 1: {'veteran': [
                               {'id': 'old-improve', 'verdict': 'improved'}]}})
    if plan['maintenance']:
        failures.append('a still-collecting recheck reported a completed maintenance pass')
    checks += 1
    if not state['members']['veteran'].get('recheck') or not plan['stable']:
        failures.append('a historical improvement re-entered a pending check or cleared it')
    checks += 1
    # Only a fresh, post-checkpoint improvement may reenter the build.
    plan, state, _ = drive([veteran], state, 6000, 1,
                           {plan['passNumber'] + 1: {'veteran': [
                               {'id': 'fresh-improve', 'verdict': 'improved'}]}})
    if not plan['maintenance'] or plan['maintenance'][0].get('outcome') != 'reentered':
        failures.append(f'a fresh post-checkpoint improvement did not reenter: {plan["maintenance"]}')
    checks += 1
    if plan['stable'] or state['members']['veteran'].get('recheck'):
        failures.append('reentry did not clear both the graduation and the checkpoint')
    checks += 1
    # A fresh but unimproved measurement completes the check and keeps the build stable.
    plan, state, _ = drive([veteran], None, 6000, 1, {1: {'veteran': history}})
    plan, state, _ = drive([veteran], state, 6000, every + 1)
    plan, state, _ = drive([veteran], state, 6000, 1,
                           {plan['passNumber'] + 1: {'veteran': [
                               {'id': 'fresh-flat', 'verdict': 'flat'}]}})
    if not plan['maintenance'] or plan['maintenance'][0].get('phase') != 'completed':
        failures.append('a fresh flat measurement did not complete the check')
    checks += 1
    if not plan['stable'] or state['members']['veteran'].get('recheck'):
        failures.append('a fresh flat recheck did not keep the build stable')
    checks += 1
    print(f'  recheck checkpoint: {checks} checks passed', flush=True)
    return checks


def check_recheck_evidence(failures):
    """A zero-grant recheck defers, and a drained one is judged on its own validation evidence.

    A checkpoint that cannot be granted a positive bank (no maintenance share, or a build already at
    the shared cap) must defer without leaving a zero-width window pending, and without advancing
    the cadence. A drained checkpoint is judged on the objective's own post-checkpoint estimate: a
    clear improvement re-enters the build with no new paired tune comparison, a noise-sized move
    holds it stable, and too few fresh samples is inconclusive - never a success.
    """
    checks = 0
    cap = portfolio.PORTFOLIO_CAP
    every = portfolio.PORTFOLIO_STABLE_RECHECK_EVERY

    def ledger(cid, objective, recheck=None, last=0):
        return {'version': portfolio.PORTFOLIO_STATE_VERSION, 'passNumber': 0, 'rotation': 0,
                'laneCursor': {},
                'members': {cid: dict(stable=True, observations=600, comparisons=3, noImprove=3,
                                      objective=objective, lastRecheckPass=last, graduatedPass=0,
                                      seen=[], recheck=recheck)}}

    veteran = make_entry('veteran', he=50, hes=300, hp=40, hps=300, ae=5.0, aes=600, ap=4.0,
                         aps=600, wins=300, losses=300, n=600)

    # (1) No maintenance share (budget//share == 0): defer without a pending checkpoint, and leave
    # the cadence anchor where it was so the build is retried as soon as budget allows.
    plan, state = portfolio.focused_portfolio([veteran], ledger('veteran', 'highest-earned'),
                                              pass_number=every, budget=3)
    decision = plan['maintenance'][0] if plan['maintenance'] else {}
    if decision.get('outcome') != 'deferred' or decision.get('phase') != 'deferred':
        failures.append(f'a recheck with no maintenance budget was not deferred: '
                        f'{plan["maintenance"]}')
    checks += 1
    if int(decision.get('granted') or 0) != 0 or state['members']['veteran'].get('recheck'):
        failures.append('a no-budget defer left a pending (or granted) checkpoint')
    checks += 1
    if int(state['members']['veteran'].get('lastRecheckPass') or 0) != 0:
        failures.append('a no-budget defer advanced the cadence and delayed the retry')
    checks += 1

    # (2) At the shared cap there is no room for a positive bank: defer, no pending checkpoint.
    capped = make_entry('veteran', he=50, hes=300, hp=40, hps=300, ae=5.0, aes=600, ap=4.0,
                        aps=600, wins=300, losses=300, n=cap,
                        resolved=portfolio.PORTFOLIO_STABLE_MIN_RUNS)
    plan, state = portfolio.focused_portfolio([capped], ledger('veteran', 'highest-earned'),
                                              pass_number=every, budget=6000)
    decision = plan['maintenance'][0] if plan['maintenance'] else {}
    if decision.get('outcome') != 'deferred' or state['members']['veteran'].get('recheck'):
        failures.append(f'a recheck at the shared cap was not deferred: {plan["maintenance"]}')
    checks += 1

    # (3) A drained checkpoint whose post-checkpoint validation estimate improved re-enters the build
    # with no new paired tune comparison at all.
    plan, state = portfolio.focused_portfolio([veteran], ledger('veteran', 'highest-earned'),
                                              pass_number=every, budget=6000)
    checkpoint = state['members']['veteran'].get('recheck') or {}
    target = int(checkpoint.get('target') or 0)
    if not checkpoint or target <= 600:
        failures.append('the due recheck did not open a positive-width checkpoint')
        return checks
    checks += 1
    improved = make_entry('veteran', he=80, hes=320, hp=40, hps=300, ae=5.0, aes=600, ap=4.0,
                          aps=600, wins=320, losses=300, n=target)
    plan, state = portfolio.focused_portfolio([improved], state, pass_number=every + 1, budget=6000)
    if not plan['maintenance'] or plan['maintenance'][0].get('outcome') != 'reentered':
        failures.append(f'a fresh post-checkpoint validation improvement did not re-enter: '
                        f'{plan["maintenance"]}')
    checks += 1
    if plan['stable'] or state['members']['veteran'].get('recheck'):
        failures.append('a validation-driven reentry did not clear the graduation and checkpoint')
    checks += 1

    # (4) The same window with a noise-sized mean move holds the build stable (no false reentry),
    # and a mean objective is judged on its own estimate, not on another objective's.
    mean_entry = make_entry('veteran', he=None, hp=None, ae=5.0, aes=600, wins=300, losses=300, n=600)
    plan, state = portfolio.focused_portfolio([mean_entry], ledger('veteran', 'average-earned'),
                                              pass_number=every, budget=6000)
    checkpoint = state['members']['veteran'].get('recheck') or {}
    if checkpoint.get('objective') != portfolio.PORTFOLIO_AVERAGE_EARNED:
        failures.append(f'the mean fixture checkpointed the wrong objective: {checkpoint}')
        return checks
    checks += 1
    noisy = make_entry('veteran', he=None, hp=None, ae=5.05, aes=620, wins=310, losses=300,
                       n=checkpoint['target'])
    plan, state = portfolio.focused_portfolio([noisy], state, pass_number=every + 1, budget=6000)
    if not plan['maintenance'] or plan['maintenance'][0].get('outcome') != 'held':
        failures.append(f'a noise-sized fresh mean did not hold the build stable: '
                        f'{plan["maintenance"]}')
    checks += 1
    if plan['stable'] != ['veteran'] or state['members']['veteran'].get('recheck'):
        failures.append('a held validation recheck did not keep the build stable and clear the check')
    checks += 1

    # (5) Too few fresh objective samples is inconclusive, never a success: one lucky maximum cannot
    # re-enter a build, and the check keeps collecting rather than declaring the build unchanged.
    plan, state = portfolio.focused_portfolio([veteran], ledger('veteran', 'highest-earned'),
                                              pass_number=every, budget=6000)
    target = int((state['members']['veteran'].get('recheck') or {}).get('target') or 0)
    lucky = make_entry('veteran', he=500, hes=305, hp=40, hps=300, ae=5.0, aes=600, ap=4.0,
                       aps=600, wins=305, losses=300, n=target)
    plan, state = portfolio.focused_portfolio([lucky], state, pass_number=every + 1, budget=6000)
    grant = plan['recheckGrants'][0] if plan['recheckGrants'] else {}
    if (plan['maintenance'] or grant.get('outcome') != 'inconclusive'
            or grant.get('phase') != 'collecting'):
        failures.append(f'too few fresh samples did not stay inconclusive and collecting: '
                        f'{plan["maintenance"]} {plan["recheckGrants"]}')
    checks += 1
    if plan['stable'] != ['veteran'] or int(grant.get('granted') or 0) <= 0:
        failures.append('an inconclusive recheck did not hold the build stable and keep collecting')
    checks += 1

    # (6) A historical tune improvement (its id is already inside the checkpoint) can never re-enter;
    # only a fresh, post-checkpoint tune id can. This pins the rule next to the validation evidence it
    # shares a checkpoint with.
    history = dict(scheduledPass=every, runCount=600, target=600 + portfolio.PORTFOLIO_STEP,
                   objective='highest-earned', estimate=50, estimateSamples=300,
                   comparisonIds=['old-improve'])
    plan, state = portfolio.focused_portfolio(
        [veteran], ledger('veteran', 'highest-earned', recheck=history),
        pass_number=every + 1, budget=6000,
        comparisons={'veteran': [{'id': 'old-improve', 'verdict': 'improved'}]})
    if plan['maintenance'] or plan['stable'] != ['veteran'] or not state['members']['veteran'].get('recheck'):
        failures.append('a historical tune improvement re-entered or completed the pending check')
    checks += 1
    plan, state = portfolio.focused_portfolio(
        [veteran], state, pass_number=every + 2, budget=6000,
        comparisons={'veteran': [{'id': 'fresh-improve', 'verdict': 'improved'}]})
    if not plan['maintenance'] or plan['maintenance'][0].get('outcome') != 'reentered':
        failures.append(f'a fresh post-checkpoint tune improvement did not re-enter: '
                        f'{plan["maintenance"]}')
    checks += 1

    # The evidence helper itself: an explicit floor and margin, and inconclusive below the sample floor.
    if portfolio.portfolio_recheck_evidence(
            objective=portfolio.PORTFOLIO_AVERAGE_EARNED, estimate=5.0, estimate_samples=600,
            value=5.5, samples=620)['verdict'] != 'improved':
        failures.append('a clear fresh mean improvement was not accepted')
    checks += 1
    if portfolio.portfolio_recheck_evidence(
            objective=portfolio.PORTFOLIO_AVERAGE_EARNED, estimate=5.0, estimate_samples=600,
            value=5.05, samples=620)['verdict'] != 'no-improvement':
        failures.append('a noise-sized mean move was treated as an improvement')
    checks += 1
    if portfolio.portfolio_recheck_evidence(
            objective=portfolio.PORTFOLIO_HIGHEST_EARNED, estimate=50, estimate_samples=300,
            value=500, samples=301)['verdict'] != 'inconclusive':
        failures.append('one lucky maximum on a single fresh sample was judged')
    checks += 1
    print(f'  recheck evidence: {checks} checks passed', flush=True)
    return checks


def check_maintenance_budget(failures):
    """A maintenance pass stays inside the one pass budget and keeps the challenger served."""
    checks = 0
    every = portfolio.PORTFOLIO_STABLE_RECHECK_EVERY
    share = portfolio.PORTFOLIO_STABLE_BUDGET_SHARE
    budget = 400
    veteran = make_entry('veteran', he=5000, hes=600, hp=5000, hps=600, ae=20.0, aes=600,
                         ap=20.0, aps=600, wins=300, losses=300, n=600,
                         resolved=portfolio.PORTFOLIO_STABLE_MIN_RUNS)
    newcomer = make_entry('newcomer', ae=0.5, aes=4, wins=2, losses=2, n=4, resolved=4)
    entries = lane_fixture(10) + [veteran, newcomer]
    flat = [{'id': f'f{i}', 'verdict': 'flat'} for i in range(3)]
    plan, state, _ = drive(entries, None, budget, 1, {1: {'veteran': flat}})
    if not plan['stable']:
        failures.append('maintenance-budget fixture failed to graduate')
        return checks
    checks += 1
    for _ in range(every - 1):
        plan, state = portfolio.focused_portfolio(entries, state, budget=budget)
    plan, state = portfolio.focused_portfolio(
        entries, state, budget=budget, challengers=[('newcomer', 'a fresh challenger')])
    if not plan['maintenance'] or int(plan['maintenance'][0]['granted']) <= 0:
        failures.append(f'the due maintenance pass did not fire: {plan["maintenance"]}')
        return checks
    checks += 1
    if plan['granted'] > plan['budget']:
        failures.append(f'the maintenance pass granted {plan["granted"]} over its budget '
                        f'{plan["budget"]}')
    checks += 1
    if plan['maintenanceGranted'] > budget // share:
        failures.append('the maintenance tier exceeded its bounded share of the budget')
    checks += 1
    if not plan['challenger'] or int(plan['challenger'][0]['granted']) <= 0:
        failures.append('the maintenance grant starved the reserved challenger slot')
    checks += 1
    if plan['granted'] != (sum(decision['granted'] for decision in plan['active'])
                           + plan['maintenanceGranted']):
        failures.append('granted does not equal the active and maintenance grants together')
    checks += 1
    print(f'  maintenance budget: {checks} checks passed', flush=True)
    return checks


def auto_optimizer(focus):
    """A coordinator-shaped object for the private auto-tune methods, without a search thread."""
    instance = optimizer.Optimizer.__new__(optimizer.Optimizer)
    instance._focus_encounter = focus
    instance._finetune_cache = None
    instance._last_finetune = None
    instance._auto_tune_state = None
    return instance


def distinct_scenario(base, attack):
    """A genuinely different stored build of the same encounter (a distinct candidate id)."""
    scenario = copy.deepcopy(base)
    ninja = next(unit for unit in scenario['ownUnits'] if unit['name'].startswith('Ninja'))
    search_contract.set_effective_parameter(scenario, ninja, 13, attack)
    return validate_scenario(scenario)


def check_portfolio_autotune(failures):
    """The rotating path freezes potential-lane candidates, not only the mean leader and record."""
    checks = 0
    store = open_store()
    try:
        base = validate_scenario(dict(default_scenario(), tickLimit=40, encounterId=19))
        with store.db:
            store.set('searchSpaceVersion', search_contract.SEARCH_SPACE_VERSION)
            store.set('scope', optimizer.scope(base))
            leader_scenario = distinct_scenario(base, 900)
            record_scenario = distinct_scenario(base, 800)
            hp_scenario = distinct_scenario(base, 700)
            ap_scenario = distinct_scenario(base, 600)
            leader = store.add(leader_scenario, 'Mean leader', 'supplied', stats(leader_scenario))
            holder = store.add(record_scenario, 'Highest Earned holder', 'supplied',
                               stats(record_scenario))
            hp_star = store.add(hp_scenario, 'Highest Potential', 'supplied', stats(hp_scenario))
            ap_star = store.add(ap_scenario, 'Average Potential', 'supplied', stats(ap_scenario))
        with store.db:
            for ordinal in range(160):
                store.record(leader, 'validation', ordinal, outcome(ordinal, 1, 40, 41 + ordinal))
            store.record(holder, 'validation', 0, outcome(0, 1, 300, 301))
            for ordinal in range(1, 120):
                store.record(holder, 'validation', ordinal, outcome(ordinal, 1, 2, 3 + ordinal))
            for ordinal in range(130):
                store.record(hp_star, 'validation', ordinal, outcome(ordinal, 2, 0, 400 + ordinal))
                store.record(ap_star, 'validation', ordinal, outcome(ordinal, 2, 0, 200 + ordinal))
            store.flush()
        # The fixture's own lane heads, so the assertions name the right parents.
        aggregates = read_aggregates(store)
        records = optimizer.population_records(store, store.candidate_keys(), aggregates)
        entries, representative = portfolio.focused_portfolio_inputs(store, records, aggregates, 19)
        lanes = portfolio.focused_portfolio_lanes(entries, representative)
        hp_lane = lane_candidates(lanes, portfolio.PORTFOLIO_HIGHEST_POTENTIAL)
        ap_lane = lane_candidates(lanes, portfolio.PORTFOLIO_AVERAGE_POTENTIAL)
        if hp_star not in hp_lane or ap_star not in ap_lane:
            failures.append(f'the fixture lanes are wrong: hp={hp_lane[:2]} ap={ap_lane[:2]}')
            return checks
        checks += 1
        instance = auto_optimizer(19)
        if not instance._auto_tune(store):
            failures.append('the automatic tuning pass created nothing')
        programs = [program for program in instance._finetune_snapshot(store)
                    if program.get('auto')]
        core = [program for program in programs if not program.get('portfolio')]
        rotating = [program for program in programs if program.get('portfolio')]
        by_parent = {}
        for program in core:
            by_parent.setdefault(program['parent']['candidateId'], set()).add(program['axis'])
        if by_parent.get(leader) != set(finetune.AUTO_TUNE_AXES):
            failures.append('the mean leader lost an axis to the rotating portfolio path')
        checks += 1
        if by_parent.get(holder) != set(finetune.AUTO_TUNE_AXES):
            failures.append('the Highest Earned holder lost an axis to the rotating path')
        checks += 1
        rotating_parents = {program['parent']['candidateId'] for program in rotating}
        if hp_star not in rotating_parents:
            failures.append('no program was created for the Highest Potential lane candidate')
        checks += 1
        if ap_star not in rotating_parents:
            failures.append('no program was created for the Average Potential lane candidate')
        checks += 1
        if leader in rotating_parents or holder in rotating_parents:
            failures.append('the rotating path re-froze the mean leader or the record holder')
        checks += 1
        total_cap = (finetune.AUTO_TUNE_MAX_PROGRAMS_PER_ENCOUNTER
                     + portfolio.PORTFOLIO_TUNE_MAX_PROGRAMS)
        budgeted = sum(int((program['budget'] or {}).get('maxChildren', 0))
                       for program in programs)
        if len(programs) > total_cap:
            failures.append(f'the automatic set held {len(programs)} programs, over the hard '
                            f'{total_cap} total')
        checks += 1
        if budgeted > (finetune.AUTO_TUNE_ENCOUNTER_CHILDREN + portfolio.PORTFOLIO_TUNE_CHILDREN):
            failures.append('the automatic child budget exceeded the hard total')
        checks += 1
        for program in rotating:
            if program['axis'] in portfolio.PORTFOLIO_NON_TUNABLE_AXES:
                failures.append('a rotating program used a non-combat axis')
                break
        checks += 1
        before = sorted(program['id'] for program in programs)
        for _ in range(3):
            instance._auto_tune(store)
            instance._finetune_snapshot(store)
        after = sorted(program['id'] for program in instance._finetune_snapshot(store)
                       if program.get('auto'))
        if after != before:
            failures.append('repeated automatic tuning passes created duplicate programs')
        checks += 1
        if len(after) > total_cap:
            failures.append('repeated passes exceeded the hard total program cap')
        checks += 1
        # The potential lane's own objective is compared with a paired mean, never a maximum: the
        # parent and its tested point share seeds, and the reading is the loss-only opportunity mean.
        potential_program = next((program for program in rotating
                                  if program['parent']['candidateId'] == hp_star
                                  and program['points']), None)
        if potential_program is None:
            failures.append('the Highest Potential program carried no tested point')
            return checks
        checks += 1
        point_id = potential_program['points'][0]['candidateId']
        with store.db:
            for ordinal in range(40):
                store.record(point_id, 'validation', ordinal, outcome(ordinal, 2, 0, 900 + ordinal))
            store.flush()
        comparisons = portfolio.portfolio_tune_comparisons(store, 19)
        cached_readings = {}
        cached_comparisons = portfolio.portfolio_tune_comparisons(
            store, 19, cache=cached_readings)
        if cached_comparisons != comparisons:
            failures.append('cached portfolio comparisons differ from a full rebuild')
        checks += 1
        parent_bank = cached_readings['readings'][hp_star][1]
        point_bank = cached_readings['readings'][point_id][1]
        if portfolio.portfolio_tune_comparisons(store, 19, cache=cached_readings) != comparisons:
            failures.append('an unchanged portfolio comparison changed on reread')
        checks += 1
        if cached_readings['readings'][hp_star][1] is not parent_bank:
            failures.append('an unchanged parent run bank was decoded again')
        checks += 1
        store.record(point_id, 'validation', 40, outcome(40, 2, 0, 940))
        portfolio.portfolio_tune_comparisons(store, 19, cache=cached_readings)
        if cached_readings['readings'][point_id][1] is point_bank:
            failures.append('a changed fine-tune point reused stale run readings')
        checks += 1
        if cached_readings['readings'][hp_star][1] is not parent_bank:
            failures.append('changing one point rebuilt its unchanged parent readings')
        checks += 1
        improvement = next((item for item in comparisons.get(hp_star, [])
                            if item['objective'] == portfolio.PORTFOLIO_AVERAGE_POTENTIAL), None)
        if improvement is None:
            failures.append('the potential-lane program produced no average-potential comparison')
        checks += 1
        if improvement and improvement.get('verdict') != 'improved':
            failures.append('a strictly better paired potential mean did not read as improved')
        checks += 1
    finally:
        close_store(store)
    print(f'  portfolio autotune: {checks} checks passed', flush=True)
    return checks


def check_concurrent_window(failures):
    """A finished portfolio program releases its slot, so the window rotates new candidates in.

    The lockout the old lifetime totals caused: once four portfolio programs existed on an encounter
    - even after all four had finished - `PORTFOLIO_TUNE_MAX_PROGRAMS` reported no capacity, so no
    new lane member or challenger could ever be tuned. The window is now *concurrent*: only `active`
    programs occupy a slot, a `done` program stops counting while its stored evidence and measured
    points stay inspectable, and the rotation moves on to the next lane member or challenger.
    """
    checks = 0
    store = open_store()
    try:
        base = validate_scenario(dict(default_scenario(), tickLimit=40, encounterId=19))
        with store.db:
            store.set('searchSpaceVersion', search_contract.SEARCH_SPACE_VERSION)
            store.set('scope', optimizer.scope(base))
            leader_scenario = distinct_scenario(base, 990)
            leader = store.add(leader_scenario, 'Concurrent leader', 'supplied',
                               stats(leader_scenario))
            contenders = []
            for index in range(portfolio.PORTFOLIO_LANE_SIZE):
                scenario = distinct_scenario(base, 800 - 10*index)
                contenders.append(store.add(scenario, f'Contender {index}', 'supplied',
                                            stats(scenario)))
        with store.db:
            for ordinal in range(120):
                store.record(leader, 'validation', ordinal, outcome(ordinal, 1, 100, 0))
            for index, candidate in enumerate(contenders):
                for ordinal in range(60):
                    store.record(candidate, 'validation', ordinal, outcome(ordinal, 1, 30-index))
                for ordinal in range(60, 120):
                    store.record(candidate, 'validation', ordinal,
                                 outcome(ordinal, 2, 0, 200-index))
            store.flush()
        window = portfolio.PORTFOLIO_TUNE_MAX_PROGRAMS
        instance = auto_optimizer(19)
        objectives, pairs, axes_by_parent, distinct = set(), [], {}, set()
        for _ in range(4):
            instance._auto_tune(store)
            for started in (instance._auto_tune_state or {}).get('startedAxes') or []:
                if started.get('parentKind') != 'portfolio':
                    continue
                objectives.add(started.get('objective'))
                pairs.append((started['parentId'], started['axis']))
                axes_by_parent.setdefault(started['parentId'], set()).add(started['axis'])
                distinct.add(started['parentId'])
            programs = [program for program in instance._finetune_snapshot(store)
                        if program.get('portfolio')]
            active = [program for program in programs if program['status'] == 'active']
            if len(active) > window:
                failures.append(f'the concurrent window held {len(active)} active portfolio '
                                f'programs, over the {window}-program bound')
            checks += 1
            # Completion is a status change, never a deletion: the stored program, its budget and
            # its measured points must survive to stay inspectable.
            before = {program['id']: (program['budget'].get('maxChildren'),
                                      program['used']['children'], program['used']['points'])
                      for program in programs}
            stored = dict(store.get('fineTunePrograms') or {})
            for program in stored.values():
                if program.get('portfolio'):
                    program['status'] = 'done'
            with store.db:
                store.set('fineTunePrograms', stored)
            instance._finetune_cache = None
            kept = {program['id']: program for program in instance._finetune_snapshot(store)
                    if program.get('portfolio')}
            if set(kept) != set(before):
                failures.append('completing a program deleted stored portfolio evidence')
            checks += 1
            for identifier, program in kept.items():
                after = (program['budget'].get('maxChildren'), program['used']['children'],
                         program['used']['points'])
                if after != before[identifier]:
                    failures.append('completing a program changed its recorded budget or children')
            checks += 1
        if len(distinct) <= window:
            failures.append(f'only {len(distinct)} distinct portfolio candidates initiated tuning '
                            f'across completed rounds; the window never rotated a new candidate in')
        checks += 1
        if objectives != set(OBJECTIVES):
            failures.append('portfolio tuning reached '
                            f'{sorted(objectives)}, not all four objectives')
        checks += 1
        if set(contenders) - distinct:
            failures.append('not every top-ten contender initiated tuning')
        checks += 1
        if len(pairs) != len(set(pairs)):
            failures.append('a portfolio program repeated a candidate+axis')
        checks += 1
        for parent, axes in axes_by_parent.items():
            if len(axes) > portfolio.PORTFOLIO_TUNE_AXES_PER_CANDIDATE:
                failures.append(f'the incumbent {parent} consumed {len(axes)} axes before the '
                                'rotation moved on')
        checks += 1
        # A challenger that appears after every top-ten member is already tuned must still initiate.
        with store.db:
            challenger_scenario = distinct_scenario(base, 300)
            challenger = store.add(challenger_scenario, 'Late challenger', 'supplied',
                                   stats(challenger_scenario))
            for ordinal in range(120):
                store.record(challenger, 'validation', ordinal, outcome(ordinal, 2, 0, 999))
            store.flush()
        instance._auto_tune(store)
        parents = {program['parent']['candidateId']
                   for program in instance._finetune_snapshot(store)
                   if program.get('portfolio')}
        if challenger not in parents:
            failures.append('a new challenger could not initiate tuning after a full top ten')
        checks += 1
    finally:
        close_store(store)
    print(f'  concurrent window: {checks} checks passed', flush=True)
    return checks


def check_graduated_slot(failures):
    """A graduated build releases its rotating slot while its residual program stays stored.

    With the window full (four active programs) a fifth candidate cannot start; marking one member
    `stable` in the ledger - graduation - frees its slot without deleting the stored program, so the
    waiting challenger is tuned on the next pass.
    """
    checks = 0
    store = open_store()
    try:
        base = validate_scenario(dict(default_scenario(), tickLimit=40, encounterId=19))
        with store.db:
            store.set('searchSpaceVersion', search_contract.SEARCH_SPACE_VERSION)
            store.set('scope', optimizer.scope(base))
            leader_scenario = distinct_scenario(base, 990)
            leader = store.add(leader_scenario, 'Slot leader', 'supplied', stats(leader_scenario))
            members = []
            for index in range(portfolio.PORTFOLIO_TUNE_MAX_PROGRAMS):
                scenario = distinct_scenario(base, 800 - 10*index)
                members.append(store.add(scenario, f'Slot member {index}', 'supplied',
                                         stats(scenario)))
        with store.db:
            for ordinal in range(120):
                store.record(leader, 'validation', ordinal, outcome(ordinal, 1, 100, 0))
            for index, candidate in enumerate(members):
                for ordinal in range(60):
                    store.record(candidate, 'validation', ordinal, outcome(ordinal, 1, 30-index))
                for ordinal in range(60, 120):
                    store.record(candidate, 'validation', ordinal,
                                 outcome(ordinal, 2, 0, 200-index))
            store.flush()
        instance = auto_optimizer(19)
        instance._auto_tune(store)
        active = [program for program in instance._finetune_snapshot(store)
                  if program.get('portfolio') and program['status'] == 'active']
        if len(active) != portfolio.PORTFOLIO_TUNE_MAX_PROGRAMS:
            failures.append(f'the fixture did not fill the window (held {len(active)})')
        checks += 1
        with store.db:
            challenger_scenario = distinct_scenario(base, 500)
            challenger = store.add(challenger_scenario, 'Waiting challenger', 'supplied',
                                   stats(challenger_scenario))
            for ordinal in range(120):
                store.record(challenger, 'validation', ordinal, outcome(ordinal, 2, 0, 999))
            store.flush()
        instance._auto_tune(store)
        parents = {program['parent']['candidateId']
                   for program in instance._finetune_snapshot(store)
                   if program.get('portfolio')}
        if challenger in parents:
            failures.append('a fifth candidate started while the window was full')
        checks += 1
        ledger = portfolio.portfolio_state(store.get(portfolio.PORTFOLIO_STATE_KEY) or {})
        ledger.setdefault('members', {})[members[0]] = dict(stable=True, observations=600,
                                                            comparisons=3, noImprove=3)
        with store.db:
            store.set(portfolio.PORTFOLIO_STATE_KEY, ledger)
        instance._auto_tune(store)
        parents = {program['parent']['candidateId']
                   for program in instance._finetune_snapshot(store)
                   if program.get('portfolio')}
        if challenger not in parents:
            failures.append('graduating a member did not release its slot for the challenger')
        checks += 1
    finally:
        close_store(store)
    print(f'  graduated slot: {checks} checks passed', flush=True)
    return checks


def check_portfolio_axis_policy(failures):
    """The portfolio sweeps the combat axes, with Intelligence only for a magic attacker.

    The portfolio's axis set is the canonical `COMBAT_STAT_PRIORITY`, never a second list: the
    conditional Intelligence axis is offered exactly while a human unit carries a magic attack
    skill, and Gathering/Move/Heart-Love are refused everywhere. A deterministic ledger offset
    proves the scheduler freezes an Intelligence program for a magic build.
    """
    checks = 0
    store = open_store()
    try:
        base = validate_scenario(dict(default_scenario(), tickLimit=40, encounterId=19))
        armed = validate_scenario(search_contract.add_skill(base, 'Ninja (A aw20)', 5))
        with store.db:
            store.set('searchSpaceVersion', search_contract.SEARCH_SPACE_VERSION)
            store.set('scope', optimizer.scope(base))
            leader_scenario = distinct_scenario(base, 990)
            leader = store.add(leader_scenario, 'Axis leader', 'supplied', stats(leader_scenario))
            wizard = store.add(armed, 'Magic wizard', 'supplied', stats(armed))
        with store.db:
            for ordinal in range(120):
                store.record(leader, 'validation', ordinal, outcome(ordinal, 1, 100, 0))
                store.record(wizard, 'validation', ordinal, outcome(ordinal, 2, 0, 900))
            store.flush()
        instance = auto_optimizer(19)
        if set(finetune.COMBAT_STAT_PRIORITY) & set(portfolio.PORTFOLIO_NON_TUNABLE_AXES):
            failures.append('a non-combat axis leaked into the combat priority order')
        checks += 1
        for axis in portfolio.PORTFOLIO_NON_TUNABLE_AXES:
            if portfolio.portfolio_axis_tunable(axis):
                failures.append(f'non-combat axis {axis} was accepted by the portfolio')
        checks += 1
        wizard_options = [option for option in instance._auto_tune_units(
                              store.scenario(wizard), finetune.COMBAT_STAT_PRIORITY)
                          if not option.get('reason')]
        if 'int' not in [option['axis'] for option in wizard_options]:
            failures.append('Intelligence was not offered for a magic attacker')
        checks += 1
        plain_options = [option for option in instance._auto_tune_units(
                             store.scenario(leader), finetune.COMBAT_STAT_PRIORITY)
                         if not option.get('reason')]
        if 'int' in [option['axis'] for option in plain_options]:
            failures.append('Intelligence was offered for a build with no magic attack')
        checks += 1
        if set(option['axis'] for option in wizard_options) - set(finetune.COMBAT_STAT_PRIORITY):
            failures.append('the portfolio offered an axis outside the combat priority order')
        checks += 1
        ledger = portfolio.portfolio_state({})
        ledger['tuneAxis'] = [option['axis'] for option in wizard_options].index('int')
        ledger['tuneRotation'] = OBJECTIVES.index(portfolio.PORTFOLIO_HIGHEST_POTENTIAL)
        with store.db:
            store.set(portfolio.PORTFOLIO_STATE_KEY, ledger)
        instance._auto_tune(store)
        int_programs = [program for program in instance._finetune_snapshot(store)
                        if program.get('portfolio') and program['axis'] == 'int'
                        and program['parent']['candidateId'] == wizard]
        if not int_programs:
            failures.append('the portfolio scheduler never froze an Intelligence program')
        checks += 1
    finally:
        close_store(store)
    print(f'  portfolio axis policy: {checks} checks passed', flush=True)
    return checks


def check_breadth_before_depth(failures):
    """Breadth is served before depth: every credible member gets a first axis before any repeat.

    Five credible members and the four-program window: the first four starts are four distinct
    members, the fifth start is the last untuned member, and only then may the staged rotation deepen
    an incumbent. No candidate+axis pair is produced twice.
    """
    checks = 0
    store = open_store()
    try:
        base = validate_scenario(dict(default_scenario(), tickLimit=40, encounterId=19))
        with store.db:
            store.set('searchSpaceVersion', search_contract.SEARCH_SPACE_VERSION)
            store.set('scope', optimizer.scope(base))
            leader_scenario = distinct_scenario(base, 990)
            leader = store.add(leader_scenario, 'Staged leader', 'supplied', stats(leader_scenario))
            members = []
            for index in range(5):
                scenario = distinct_scenario(base, 800 - 10*index)
                members.append(store.add(scenario, f'Staged member {index}', 'supplied',
                                         stats(scenario)))
        with store.db:
            for ordinal in range(120):
                store.record(leader, 'validation', ordinal, outcome(ordinal, 1, 100, 0))
            for index, candidate in enumerate(members):
                for ordinal in range(60):
                    store.record(candidate, 'validation', ordinal, outcome(ordinal, 1, 30-index))
                for ordinal in range(60, 120):
                    store.record(candidate, 'validation', ordinal,
                                 outcome(ordinal, 2, 0, 200-index))
            store.flush()
        instance = auto_optimizer(19)
        sequence, pairs = [], []
        for _ in range(4):
            instance._auto_tune(store)
            for started in (instance._auto_tune_state or {}).get('startedAxes') or []:
                if started.get('parentKind') == 'portfolio':
                    sequence.append(started['parentId'])
                    pairs.append((started['parentId'], started['axis']))
            # Let the window drain so every round has room, exactly as a real program that finishes a
            # boundary would release its slot; the stored program and its measured points stay.
            stored = dict(store.get('fineTunePrograms') or {})
            for program in stored.values():
                if program.get('portfolio'):
                    program['status'] = 'done'
            with store.db:
                store.set('fineTunePrograms', stored)
            instance._finetune_cache = None
        if len(set(sequence[:5])) != 5 or set(sequence[:5]) != set(members):
            failures.append('the first five portfolio starts were not the five distinct members: '
                            f'{sequence[:5]}')
        checks += 1
        if len(sequence) < 6 or sequence[5] not in set(sequence[:5]):
            failures.append('depth was not deferred until every member held a first axis')
        checks += 1
        if len(pairs) != len(set(pairs)):
            failures.append('a staged pass duplicated a candidate+axis program')
        checks += 1
    finally:
        close_store(store)
    print(f'  breadth before depth: {checks} checks passed', flush=True)
    return checks


def check_depth_second_axis(failures):
    """After breadth is exhausted a promising member takes a second distinct axis, bounded and unique.

    Two credible members and the four-program window: breadth freezes one axis on each, then depth
    spends the remaining capacity on their next combat axes. Every stored program is a distinct
    candidate+axis, no candidate passes the per-candidate cap, and the active window stays inside its
    bound.
    """
    checks = 0
    store = open_store()
    try:
        base = validate_scenario(dict(default_scenario(), tickLimit=40, encounterId=19))
        with store.db:
            store.set('searchSpaceVersion', search_contract.SEARCH_SPACE_VERSION)
            store.set('scope', optimizer.scope(base))
            leader_scenario = distinct_scenario(base, 990)
            leader = store.add(leader_scenario, 'Depth leader', 'supplied', stats(leader_scenario))
            members = []
            for index in range(2):
                scenario = distinct_scenario(base, 800 - 10*index)
                members.append(store.add(scenario, f'Depth member {index}', 'supplied',
                                         stats(scenario)))
        with store.db:
            for ordinal in range(120):
                store.record(leader, 'validation', ordinal, outcome(ordinal, 1, 100, 0))
            for index, candidate in enumerate(members):
                for ordinal in range(60):
                    store.record(candidate, 'validation', ordinal, outcome(ordinal, 1, 30-index))
                for ordinal in range(60, 120):
                    store.record(candidate, 'validation', ordinal,
                                 outcome(ordinal, 2, 0, 200-index))
            store.flush()
        instance = auto_optimizer(19)
        for _ in range(3):
            instance._auto_tune(store)
        programs = [program for program in instance._finetune_snapshot(store)
                    if program.get('portfolio')]
        by_parent = {}
        for program in programs:
            by_parent.setdefault(program['parent']['candidateId'], set()).add(program['axis'])
        if not any(len(axes) >= 2 for axes in by_parent.values()):
            failures.append('no candidate took a second axis after breadth was exhausted')
        checks += 1
        pairs = [(program['parent']['candidateId'], program['axis']) for program in programs]
        if len(pairs) != len(set(pairs)):
            failures.append('a candidate+axis program was duplicated')
        checks += 1
        if any(len(axes) > portfolio.PORTFOLIO_TUNE_AXES_PER_CANDIDATE
               for axes in by_parent.values()):
            failures.append('a candidate exceeded the per-candidate axis cap')
        checks += 1
        active = [program for program in programs if program['status'] == 'active']
        if len(active) > portfolio.PORTFOLIO_TUNE_MAX_PROGRAMS:
            failures.append(f'the active window held {len(active)} programs, over its bound')
        checks += 1
    finally:
        close_store(store)
    print(f'  depth second axis: {checks} checks passed', flush=True)
    return checks


def check_staged_duplicates_and_slots(failures):
    """Across completed rounds the staged rotation never duplicates a candidate+axis or overflows.

    Three credible members and eight rounds in which every portfolio program completes: the window
    releases a slot each round and depth adds a further axis until the per-candidate cap, so the same
    (candidate, axis) is never recreated and the active window stays inside its bound every round.
    """
    checks = 0
    store = open_store()
    try:
        base = validate_scenario(dict(default_scenario(), tickLimit=40, encounterId=19))
        with store.db:
            store.set('searchSpaceVersion', search_contract.SEARCH_SPACE_VERSION)
            store.set('scope', optimizer.scope(base))
            leader_scenario = distinct_scenario(base, 990)
            leader = store.add(leader_scenario, 'Rounds leader', 'supplied', stats(leader_scenario))
            members = []
            for index in range(3):
                scenario = distinct_scenario(base, 800 - 10*index)
                members.append(store.add(scenario, f'Rounds member {index}', 'supplied',
                                         stats(scenario)))
        with store.db:
            for ordinal in range(120):
                store.record(leader, 'validation', ordinal, outcome(ordinal, 1, 100, 0))
            for index, candidate in enumerate(members):
                for ordinal in range(60):
                    store.record(candidate, 'validation', ordinal, outcome(ordinal, 1, 30-index))
                for ordinal in range(60, 120):
                    store.record(candidate, 'validation', ordinal,
                                 outcome(ordinal, 2, 0, 200-index))
            store.flush()
        instance = auto_optimizer(19)
        pairs, overflow = [], False
        for _ in range(8):
            instance._auto_tune(store)
            for started in (instance._auto_tune_state or {}).get('startedAxes') or []:
                if started.get('parentKind') == 'portfolio':
                    pairs.append((started['parentId'], started['axis']))
            programs = [program for program in instance._finetune_snapshot(store)
                        if program.get('portfolio')]
            active = [program for program in programs if program['status'] == 'active']
            if len(active) > portfolio.PORTFOLIO_TUNE_MAX_PROGRAMS:
                overflow = True
            stored = dict(store.get('fineTunePrograms') or {})
            for program in stored.values():
                if program.get('portfolio'):
                    program['status'] = 'done'
            with store.db:
                store.set('fineTunePrograms', stored)
            instance._finetune_cache = None
        if len(pairs) != len(set(pairs)):
            failures.append('the staged rotation recreated a candidate+axis program')
        checks += 1
        if overflow:
            failures.append('the concurrent window overflowed its bound during a completed round')
        checks += 1
        if not pairs:
            failures.append('the staged rotation started no portfolio program')
        checks += 1
    finally:
        close_store(store)
    print(f'  staged duplicates and slots: {checks} checks passed', flush=True)
    return checks


def check_depth_axis_policy(failures):
    """Depth keeps the axis policy: Intelligence only on a magic attacker, never a noncombat axis."""
    checks = 0
    store = open_store()
    try:
        base = validate_scenario(dict(default_scenario(), tickLimit=40, encounterId=19))
        armed = validate_scenario(search_contract.add_skill(base, 'Ninja (A aw20)', 5))
        with store.db:
            store.set('searchSpaceVersion', search_contract.SEARCH_SPACE_VERSION)
            store.set('scope', optimizer.scope(base))
            leader_scenario = distinct_scenario(base, 990)
            leader = store.add(leader_scenario, 'Depth policy leader', 'supplied',
                               stats(leader_scenario))
            plain_scenario = distinct_scenario(base, 700)
            plain = store.add(plain_scenario, 'Depth plain', 'supplied', stats(plain_scenario))
            wizard = store.add(armed, 'Depth magic', 'supplied', stats(armed))
        with store.db:
            for ordinal in range(120):
                store.record(leader, 'validation', ordinal, outcome(ordinal, 1, 100, 0))
                store.record(plain, 'validation', ordinal, outcome(ordinal, 2, 0, 700))
                store.record(wizard, 'validation', ordinal, outcome(ordinal, 2, 0, 900))
            store.flush()
        instance = auto_optimizer(19)
        options = [option for option in instance._auto_tune_units(
                       store.scenario(wizard), finetune.COMBAT_STAT_PRIORITY)
                   if not option.get('reason')]
        if 'int' not in [option['axis'] for option in options]:
            failures.append('Intelligence was not offered for a magic attacker')
        checks += 1
        ledger = portfolio.portfolio_state({})
        ledger['tuneAxis'] = [option['axis'] for option in options].index('int')
        ledger['tuneRotation'] = OBJECTIVES.index(portfolio.PORTFOLIO_HIGHEST_POTENTIAL)
        with store.db:
            store.set(portfolio.PORTFOLIO_STATE_KEY, ledger)
        for _ in range(3):
            instance._auto_tune(store)
        programs = [program for program in instance._finetune_snapshot(store)
                    if program.get('portfolio')]
        axes = {program['axis'] for program in programs}
        if axes - set(finetune.COMBAT_STAT_PRIORITY):
            failures.append(f'the staged rotation used axes outside the combat order: {sorted(axes)}')
        checks += 1
        if axes & set(portfolio.PORTFOLIO_NON_TUNABLE_AXES):
            failures.append('the staged rotation used a non-combat axis')
        checks += 1
        if not any(program['parent']['candidateId'] == wizard and program['axis'] == 'int'
                   for program in programs):
            failures.append('the staged scheduler never froze an Intelligence program for the wizard')
        checks += 1
        if any(program['parent']['candidateId'] == plain and program['axis'] == 'int'
               for program in programs):
            failures.append('Intelligence was swept on a build with no magic attack')
        checks += 1
    finally:
        close_store(store)
    print(f'  depth axis policy: {checks} checks passed', flush=True)
    return checks


def check_joint_interaction_report(failures):
    """A two-axis interaction is only claimed when the engine scheduled the grid; else the gap is named.

    The reading is built from the optimiser's own program views, so this pins the rule without a
    store: two single-axis programs on one candidate are grouped, a scheduled paired grid is reported,
    an unscheduled one reports the missing bracket, and two independent single-axis brackets are never
    read as a joint range.
    """
    checks = 0

    def view(axis, cid='c1', boundary=True, grid=None):
        return dict(portfolio=True, axis=axis, parent=dict(candidateId=cid),
                    boundary=(dict(low=10, high=20) if boundary else None), grid=grid)

    scheduled = portfolio.portfolio_joint_interaction([
        view('atk', grid=dict(axis2='spd', interaction=dict(verdict='interaction', n=120),
                              statement='Attack x Speed measured')),
        view('spd'),
    ])
    if len(scheduled) != 1 or scheduled[0]['axes'] != ['atk', 'spd']:
        failures.append('the joint reading did not group one candidate by its two axes')
    checks += 1
    if not scheduled[0]['scheduled'] or scheduled[0]['axis2'] != 'spd':
        failures.append('a scheduled paired grid was not reported as scheduled')
    checks += 1
    gapped = portfolio.portfolio_joint_interaction([view('atk'), view('spd', boundary=False)])
    if gapped[0]['scheduled'] or not gapped[0]['gap'] or 'spd' not in gapped[0]['gap']:
        failures.append('an unscheduled interaction did not name the missing bracket')
    checks += 1
    if portfolio.portfolio_joint_interaction([view('atk')]):
        failures.append('a single-axis candidate was reported as a two-axis interaction')
    checks += 1
    both = portfolio.portfolio_joint_interaction([view('atk', grid=None), view('spd', grid=None)])
    if both[0]['scheduled'] or both[0]['interaction'] is not None:
        failures.append('a joint range was claimed without a scheduled grid')
    checks += 1
    print(f'  joint interaction report: {checks} checks passed', flush=True)
    return checks


def check_planner_wiring(failures):
    """The planner must resolve portfolio names through the module it imports.

    `strategy_optimizer` re-exports the portfolio's public names lazily through a module-level
    `__getattr__`, which PEP 562 applies to attribute access only. A bare global call inside a
    function body does *not* consult it, so a planner that calls `focused_portfolio(...)` unqualified
    raises `NameError` on the first focused pass and pauses the run.
    """
    checks = 0
    used = ('focused_portfolio', 'focused_portfolio_inputs', 'focused_portfolio_lanes',
            'portfolio_challenger_candidates', 'portfolio_tune_signature',
            'portfolio_tune_comparisons', 'portfolio_limits', 'PORTFOLIO_SHARE',
            'PORTFOLIO_STATE_KEY')
    if getattr(optimizer, 'portfolio', None) is not portfolio:
        failures.append('strategy_optimizer does not expose the portfolio module it uses')
    checks += 1
    source = Path(optimizer.__file__).read_text(encoding='utf-8')
    for name in used:
        if not hasattr(portfolio, name):
            failures.append(f'the integration name {name} is not public on the portfolio module')
        checks += 1
        if ('portfolio.' + name) not in source:
            failures.append(f'the planner never resolves {name} through the portfolio module')
        checks += 1
        if re.search(r'(?<![\w.])' + re.escape(name) + r'\s*\(', source):
            failures.append(f'{name} is called as a bare global in strategy_optimizer; the module '
                            '__getattr__ re-export does not resolve function-body globals')
        checks += 1
    print(f'  planner wiring: {checks} checks passed', flush=True)
    return checks


def fixture_store():
    """A one-fight library with a winning baseline, a losing child and an off-focus fight."""
    store = open_store()
    with store.db:
        store.set('searchSpaceVersion', search_contract.SEARCH_SPACE_VERSION)
        store.set('scope', optimizer.scope(dict(default_scenario(), tickLimit=40)))
        base = validate_scenario(dict(default_scenario(), tickLimit=40, encounterId=19))
        winner = store.add(base, 'Focus winner', 'supplied', stats(base))
        for ordinal in range(learner.EXTENDED_DISCOVERY_RUNS):
            store.record(winner, 'discovery', ordinal, outcome(ordinal, 1, 6, 9))
        for ordinal in range(learner.VALIDATION_RUNS):
            store.record(winner, 'validation', ordinal, outcome(ordinal, 1, 6, 9))
        loser = store.add(variant(base, 0, 40), 'Focus loser', 'mutation', stats(base))
        for ordinal in range(learner.EXTENDED_DISCOVERY_RUNS):
            store.record(loser, 'discovery', ordinal, outcome(ordinal, 2, 0, 20 + ordinal))
        for ordinal in range(learner.VALIDATION_RUNS):
            store.record(loser, 'validation', ordinal, outcome(ordinal, 2, 0, 30 + ordinal))
        other = validate_scenario(dict(default_scenario(), tickLimit=40, encounterId=10))
        store.add(other, 'Other fight', 'supplied', stats(other))
    return store, winner, loser


def pipeline(store, focus):
    """The planner's own evidence refresh, in the same order and with the same helpers."""
    keys = store.candidate_keys()
    aggregates = read_aggregates(store)
    records = optimizer.population_records(store, keys, aggregates)
    entries, representative = portfolio.focused_portfolio_inputs(store, records, aggregates, focus)
    lanes = portfolio.focused_portfolio_lanes(entries, representative)
    improvements = store.get('childImprovements') or {}
    regions = store.candidate_regions()
    nominees = store.region_nominees()
    new_regions = {cid for cid, key in regions.items() if nominees.get(key) == cid}
    challengers = portfolio.portfolio_challenger_candidates(
        entries, lanes, improvements, new_regions)
    signature = portfolio.portfolio_tune_signature(store, focus)
    comparisons = portfolio.portfolio_tune_comparisons(store, focus)
    prior = store.get(portfolio.PORTFOLIO_STATE_KEY) or {}
    plan, ledger = portfolio.focused_portfolio(
        entries, prior, budget=6000, representative=representative, challengers=challengers,
        comparisons=comparisons)
    if ledger != prior:
        with store.db:
            store.set(portfolio.PORTFOLIO_STATE_KEY, ledger)
    return dict(entries=entries, representative=representative, lanes=lanes, plan=plan,
                ledger=ledger, signature=signature, comparisons=comparisons)


def check_integration(failures):
    """The synthetic Store path: focus filtering, persistence, ordinals and proposal capacity."""
    checks = 0
    store, winner, loser = fixture_store()
    try:
        first = pipeline(store, 19)
        entries = first['entries']
        ids = {entry['candidate'] for entry in entries}
        if ids != {winner, loser}:
            failures.append(f'the focused input did not narrow to the two encounter-19 builds: '
                            f'{len(ids)} entries')
        checks += 1
        if any(entry['encounterId'] != 19 for entry in entries):
            failures.append('an off-focus build reached the portfolio entries')
        checks += 1
        if first['plan']['passNumber'] != 1:
            failures.append(f"the first pass number is {first['plan']['passNumber']}, expected 1")
        checks += 1
        stored = store.get(portfolio.PORTFOLIO_STATE_KEY)
        if not stored or stored.get('passNumber') != 1:
            failures.append('the graduation ledger was not persisted after the first pass')
        checks += 1
        second = pipeline(store, 19)
        if second['plan']['passNumber'] != 2:
            failures.append(f"the persisted pass number did not advance: "
                            f"{second['plan']['passNumber']}")
        checks += 1
        if second['plan']['rotation'] <= first['plan']['rotation']:
            failures.append('the rotation did not advance across the persisted passes')
        checks += 1
        keys = store.candidate_keys()
        aggregates = read_aggregates(store)
        records = optimizer.population_records(store, keys, aggregates)
        limits = {record['candidate']: optimizer.staged_limits(
            0, 0, None, False, None, None, None, None, False, 0, True, 'branching')
            for record in records}
        staged = dict(limits)
        limits = portfolio.portfolio_limits(limits, second['plan'])
        ordinals = {(row[0], row[1]): int(row[2]) + 1 for row in store.db.execute(
            'SELECT candidate, phase, MAX(ordinal) FROM run GROUP BY candidate, phase')}
        ready = optimizer.interleave_ready(limits, ordinals, set())
        expected = sum(max(0, dlimit - ordinals.get((cid, 'discovery'), 0))
                       + max(0, vlimit - ordinals.get((cid, 'validation'), 0))
                       for cid, (dlimit, vlimit) in limits.items())
        if len(ready) != expected:
            failures.append(f'the ready list holds {len(ready)} seeds, expected {expected}')
        checks += 1
        for cid, phase, ordinal in ready:
            dlimit, vlimit = limits[cid]
            low = ordinals.get((cid, phase), 0)
            high = dlimit if phase == 'discovery' else vlimit
            if not low <= ordinal < high:
                failures.append(f'ready seed {(cid[:8], phase, ordinal)} outside [{low}, {high})')
                break
        checks += 1
        if any(limits[cid][1] > portfolio.PORTFOLIO_CAP for cid in limits):
            failures.append('a portfolio grant exceeded the shared cap')
        checks += 1
        held = dict(limits)
        held[winner] = (staged[winner][0], 2500)
        held = portfolio.portfolio_limits(held, second['plan'])
        if held[winner][1] < 2500:
            failures.append('the portfolio overlay lowered an existing paired bank')
        checks += 1
        before_load, _, _ = optimizer.reseed_load(store, staged, ordinals)
        after_load, _, _ = optimizer.reseed_load(store, limits, ordinals)
        if ({enc: row['open'] for enc, row in before_load.items()}
                != {enc: row['open'] for enc, row in after_load.items()}):
            failures.append('the portfolio overlay changed open-child counts')
        checks += 1
        if any(row['open'] > learner.MAX_OPEN_PER_ENCOUNTER for row in after_load.values()):
            failures.append('proposal capacity exceeded MAX_OPEN_PER_ENCOUNTER')
        checks += 1
    finally:
        close_store(store)
    print(f'  synthetic store integration: {checks} checks passed', flush=True)
    return checks


def check_sustained_parent_breadth(failures):
    """The work-scaled fine-tune budget must let the rotation reach 40 distinct one-axis parents.

    The four lanes' top tens are up to 40 distinct builds (overlapping members collapse to one slot).
    The old fixed 48-child encounter cap was exhausted by about twelve four-child programs, so most of
    that breadth could never be tuned. This fixture builds four disjoint ten-build lanes plus a
    lane-ineligible mean leader and a probe Highest Earned holder, then drives the real rotation
    through ten completed rounds - the concurrent window releases a finished program's slot each time -
    and checks that all 40 parents were reached with their measured points intact.
    """
    checks = 0
    store = open_store()
    try:
        base = validate_scenario(dict(default_scenario(), tickLimit=40, encounterId=19))
        with store.db:
            store.set('searchSpaceVersion', search_contract.SEARCH_SPACE_VERSION)
            store.set('scope', optimizer.scope(base))
            # The mean leader is recorded in discovery only: it is a credible auto-tune parent but has
            # no validation aggregate, so it is ineligible for every lane and costs no lane slot.
            leader_scenario = distinct_scenario(base, 999)
            leader = store.add(leader_scenario, 'Breadth leader', 'supplied', stats(leader_scenario))
            groups = {}
            attack = 900
            for group in ('earned', 'potential', 'average-earned', 'average-potential'):
                members = []
                for index in range(10):
                    scenario = distinct_scenario(base, attack)
                    attack -= 1
                    members.append(store.add(scenario, f'{group} {index}', 'probe', stats(scenario)))
                groups[group] = members
        with store.db:
            for ordinal in range(100):
                store.record(leader, 'discovery', ordinal, outcome(ordinal, 1, 100, 0))
            # Highest-Earned lane: one exceptional record plus a low mean, all converted wins.
            for index, cid in enumerate(groups['earned']):
                store.record(cid, 'validation', 0, outcome(0, 1, 2000 - index, 0))
                for ordinal in range(1, 100):
                    store.record(cid, 'validation', ordinal, outcome(ordinal, 1, 1, 0))
            # Average-Earned lane: a high mean and no record to speak of.
            for cid in groups['average-earned']:
                for ordinal in range(100):
                    store.record(cid, 'validation', ordinal, outcome(ordinal, 1, 50, 0))
            # Highest-Potential lane: one exceptional loss opportunity plus a low mean.
            for index, cid in enumerate(groups['potential']):
                store.record(cid, 'validation', 0, outcome(0, 2, 0, 2000 - index))
                for ordinal in range(1, 100):
                    store.record(cid, 'validation', ordinal, outcome(ordinal, 2, 0, 1))
            # Average-Potential lane: a high mean opportunity.
            for cid in groups['average-potential']:
                for ordinal in range(100):
                    store.record(cid, 'validation', ordinal, outcome(ordinal, 2, 0, 50))
            store.flush()
        instance = auto_optimizer(19)
        capacity = optimizer.finetune_capacity(store, 19)
        if capacity['allowed'] <= optimizer.FINETUNE_BASE_CHILDREN:
            failures.append(f"the fixture's recorded runs did not grow the budget ({capacity})")
        checks += 1
        seen = set()
        for _ in range(10):
            instance._auto_tune(store)
            for started in (instance._auto_tune_state or {}).get('startedAxes') or []:
                if started.get('parentKind') == 'portfolio':
                    seen.add(started['parentId'])
            stored = dict(store.get('fineTunePrograms') or {})
            for program in stored.values():
                if program.get('portfolio'):
                    program['status'] = 'done'
            with store.db:
                store.set('fineTunePrograms', stored)
            instance._finetune_cache = None
        if len(seen) != 40:
            failures.append(f'the rotation reached {len(seen)} distinct portfolio parents, expected 40')
        checks += 1
        programs = [program for program in instance._finetune_snapshot(store)
                    if program.get('portfolio')]
        if len(programs) != 40:
            failures.append(f'{len(programs)} stored portfolio programs, expected 40')
        checks += 1
        if any(program['used']['points'] < 1 for program in programs):
            failures.append('a reached portfolio parent was left with no measured points')
        checks += 1
        stored = store.get('fineTunePrograms') or {}
        capped = [program['id'] for program in stored.values()
                  if program.get('portfolio')
                  and (program.get('schedule') or {}).get('encounterCapReached')]
        if capped:
            failures.append(f'the work-scaled budget still refused reached parents: {capped[:3]}')
        checks += 1
        live = optimizer.finetune_capacity(store, 19)
        if live['room'] < 0 or live['retained'] < 40:
            failures.append(f'the retained fine-tune inventory is inconsistent: {live}')
        checks += 1
    finally:
        close_store(store)
    print(f'  sustained parent breadth: {checks} checks passed', flush=True)
    return checks


def main():
    failures = []
    checks = 0
    checks += check_lanes(failures)
    checks += check_mean_evidence_floor(failures)
    checks += check_loss_only(failures)
    checks += check_overlap_dedup(failures)
    checks += check_challenger_slot(failures)
    checks += check_rotation(failures)
    checks += check_graduation(failures)
    checks += check_maintenance(failures)
    checks += check_recheck_checkpoint(failures)
    checks += check_recheck_evidence(failures)
    checks += check_maintenance_budget(failures)
    checks += check_caps_and_budget(failures)
    checks += check_axes(failures)
    checks += check_estimator_hygiene(failures)
    checks += check_portfolio_autotune(failures)
    checks += check_concurrent_window(failures)
    checks += check_graduated_slot(failures)
    checks += check_sustained_parent_breadth(failures)
    checks += check_portfolio_axis_policy(failures)
    checks += check_breadth_before_depth(failures)
    checks += check_depth_second_axis(failures)
    checks += check_staged_duplicates_and_slots(failures)
    checks += check_depth_axis_policy(failures)
    checks += check_joint_interaction_report(failures)
    checks += check_planner_wiring(failures)
    checks += check_integration(failures)
    if failures:
        print(f'\nfocused four-objective portfolio: {len(failures)} FAILURE(S)')
        for failure in failures:
            print(f'  - {failure}')
        raise SystemExit(1)
    print(f'\nfocused four-objective portfolio: {checks} checks passed')


if __name__ == '__main__':
    main()
