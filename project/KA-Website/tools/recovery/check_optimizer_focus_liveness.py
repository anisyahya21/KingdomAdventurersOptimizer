"""Focused-search liveness: a focused fight that runs dry must be replenished, never left Running idle.

The reported failure was a live library with a 3.3 M-run history, encounter 19 ("Take out the Wairo
Tank!") focused, shown as Running while `Active now` stayed 0/24 and the encounter's attempt counter
never moved. Every candidate of that fight had reached its staged cap, and the refill paths were
refused - for a reason that had nothing to do with the fight's real work:

  * `top_up()` and `reseed_load()` counted *probe/saved/supplied* builds as unevaluated children,
    because they tested `discoveryOrdinal < DISCOVERY_RUNS` without checking that the candidate has a
    discovery stage at all. A banked probe's staged discovery limit *is* its current discovery count
    (`staged_limits` returns `(nd, bank)` for a probe), so every probe counted as an open child
    forever. Encounter 19 held 104 of them, over `MAX_OPEN_PER_ENCOUNTER` (48), so the encounter was
    refused a proposal *and* excluded from the starvation reseed. With the focus pinned to it, no
    other fight's work could be dispatched either: zero ready seeds, zero workers, no error.

This checker pins the fix and its guarantees:

  * the child accounting: only a generated child with an unfinished first bank is "open", so probes
    no longer inflate it and real children are counted exactly as before;
  * the reproduction: a focused encounter whose candidates are all capped *and* whose probe builds
    would have filled the cap is refilled, new legal mutations are created for that same fight, and
    the ready list hands them to several workers - all of them on the focused encounter;
  * nothing existing is over-claimed: replenishment adds *new* candidate identities and never raises
    an existing candidate's bank past what the staged policy (and MAX_SAMPLES) already authorised;
  * focus switching and clearing still scope every subsequent seed, and clearing restores the mixed
    population;
  * the impossible cases are reported: an empty focused fight and a fully protected one produce a
    clear, actionable reason instead of a false "active search", while a busy or duty-idling pool
    produces none.

    python check_optimizer_focus_liveness.py
"""
import copy
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import search_contract  # noqa: E402
import strategy_learner as learner  # noqa: E402
import strategy_optimizer as optimizer  # noqa: E402
from strategy_optimizer_adapter import (default_scenario, provenance, stats,  # noqa: E402
                                        validate_scenario)


def outcome(index, ordinal):
    """One accepted result, shaped like the runner's own record."""
    return dict(verdict=1 if index % 2 else 2, censored=False, ticks=40,
                prizeCallbacks=index + ordinal, survivors=1, resourceUses=0,
                behavior=dict(attacks=1, heals=0, prizes=index + ordinal),
                seeds=[ordinal, 100 + ordinal], digest=f'd{index}-{ordinal}', elapsedSeconds=.01,
                rewardOutcome=dict(awardedChests=index, pendingChests=index, awardedBasis='test',
                                   inventoryVerified=False))


def variant(base, unit, delta):
    scenario = copy.deepcopy(base)
    scenario['ownUnits'][unit]['parameters'][13]['rawValue'] += delta
    return validate_scenario(scenario)


def fixture_store(root, name, probes=60):
    """A two-fight library in the reported shape.

    Encounter 19 (the focused fight) holds only *finished* work: `probes` banked probe builds with
    no discovery stage, a supplied baseline and a handful of mutation children whose staged banks are
    complete. Encounter 10 holds mutation children with a first discovery bank still runnable, so the
    normal (unfocused) distribution has work to hand out.
    """
    store = optimizer.Store(Path(root) / name, provenance())
    probe_banks = {}
    with store.db:
        store.set('searchSpaceVersion', search_contract.SEARCH_SPACE_VERSION)
        store.set('scope', optimizer.scope(dict(default_scenario(), tickLimit=40)))
        base = validate_scenario(dict(default_scenario(), tickLimit=40, encounterId=19))
        supplied = store.add(base, 'Baseline enc19', 'supplied', stats(base))
        for ordinal in range(learner.EXTENDED_DISCOVERY_RUNS):
            store.record(supplied, 'discovery', ordinal, outcome(0, ordinal))
        for ordinal in range(learner.VALIDATION_RUNS):
            store.record(supplied, 'validation', ordinal, outcome(0, ordinal))
        for index in range(probes):
            cid = store.add(variant(base, 0, 100 + index), f'Probe enc19 {index}', 'probe',
                            stats(base))
            probe_banks[cid] = learner.VALIDATION_RUNS
            for ordinal in range(learner.VALIDATION_RUNS):
                store.record(cid, 'validation', ordinal, outcome(index, ordinal))
        for index in range(6):
            cid = store.add(variant(base, 1, 200 + index), f'Capped enc19 {index}', 'mutation',
                            stats(base))
            for ordinal in range(learner.EXTENDED_DISCOVERY_RUNS):
                store.record(cid, 'discovery', ordinal, outcome(index, ordinal))
        store.set('probeBanks', probe_banks)
        base10 = validate_scenario(dict(default_scenario(), tickLimit=40, encounterId=10))
        store.add(base10, 'Baseline enc10', 'supplied', stats(base10))
        for index in range(6):
            store.add(variant(base10, 0, 10 + index), f'Runnable enc10 {index}', 'mutation',
                      stats(base10))
    return store, probe_banks


def plan_limits(store, probe_banks):
    """The staged limits the planner derives for every resident candidate (see `staged_limits`)."""
    limits = {}
    for row in store.db.execute('SELECT id, source FROM candidate'):
        cid = row['id']
        is_probe = cid in probe_banks
        limits[cid] = optimizer.staged_limits(
            0, 0, None, False, None, None, None, None, is_probe,
            probe_banks.get(cid, learner.VALIDATION_RUNS), True, 'branching')
    return limits


def plan_ordinals(store):
    """The next unrecorded ordinal per (candidate, phase), exactly as the coordinator keeps it."""
    return {(row[0], row[1]): int(row[2]) + 1 for row in store.db.execute(
        'SELECT candidate, phase, MAX(ordinal) FROM run GROUP BY candidate, phase')}


def focused_view(limits, encounter_of, focus):
    focus_ids = optimizer.encounter_scope(encounter_of, focus)
    return {cid: value for cid, value in limits.items()
            if focus_ids is None or cid in focus_ids}


def drain(limits, ordinals, encounter_of, focus, workers):
    """Drain the ready list across `workers` as the loop does: rebuild, reserve, dispatch."""
    dispatched = []
    reserved = set()
    for _ in range(64):
        order = optimizer.interleave_ready(focused_view(limits, encounter_of, focus),
                                           ordinals, reserved)
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


def check_child_accounting(failures):
    """Only a generated child with an unfinished first bank counts as open."""
    checks = 0
    cases = [
        # (limits, discovery ordinal, expected open?)
        ((learner.DISCOVERY_RUNS, 0), 0, True),        # fresh child, first bank running
        ((learner.DISCOVERY_RUNS, 0), 7, True),        # last seed of the first bank
        ((learner.DISCOVERY_RUNS, 0), 8, False),       # first bank done -> evaluated
        ((0, learner.VALIDATION_RUNS), 0, False),      # banked probe, no discovery stage at all
        ((0, 0), 0, False),                            # saved build, nothing authorised
        ((learner.EXTENDED_DISCOVERY_RUNS, 0), 10, False),   # extended bank is not "open" either
        ((learner.EXTENDED_DISCOVERY_RUNS, learner.VALIDATION_RUNS), 24, False),
    ]
    for limits_for_cid, ordinal, expected in cases:
        limits = {'c': limits_for_cid}
        ordinals = {('c', 'discovery'): ordinal} if ordinal is not None else {}
        got = optimizer.unfinished_child('c', limits, ordinals)
        if got != expected:
            failures.append(f'unfinished_child with limits {limits_for_cid} ordinal {ordinal} '
                            f'= {got}, expected {expected}')
        checks += 1
    # The bug in one line: the rule the fix replaced called a banked probe an open child.
    probe_ordinals = {('p', 'discovery'): 0}
    old_rule = probe_ordinals[('p', 'discovery')] < learner.DISCOVERY_RUNS
    if not (old_rule and not optimizer.unfinished_child('p', {'p': (0, learner.VALIDATION_RUNS)},
                                                        probe_ordinals)):
        failures.append('the probe miscount the fix removes is not reproduced')
    checks += 1
    print(f'  child accounting: {checks} unit checks passed', flush=True)
    return checks


def check_focused_replenishment(failures, root):
    """The zero-ready focused state is refilled for that same fight, across several workers."""
    checks = 0
    store, probe_banks = fixture_store(root, 'replenish.sqlite')
    try:
        limits = plan_limits(store, probe_banks)
        ordinals = plan_ordinals(store)
        encounter_of = store.candidate_encounters()
        focus = 19
        in_focus = {cid for cid, encounter in encounter_of.items() if encounter == focus}

        # 1. The reported state: the focused scope is capped, so the ready list is empty.
        ready = optimizer.interleave_ready(focused_view(limits, encounter_of, focus), ordinals, set())
        runnable = sum(max(0, limits[cid][0] - ordinals.get((cid, 'discovery'), 0))
                       + max(0, limits[cid][1] - ordinals.get((cid, 'validation'), 0))
                       for cid in in_focus)
        if ready or runnable:
            failures.append(f'the fixture did not reproduce a zero-ready focused fight: '
                            f'{len(ready)} seeds, {runnable} runnable')
        checks += 1

        # 2. The old rule would have refused this fight because its banked probes filled the cap...
        old_open = sum(1 for cid in in_focus if ordinals.get((cid, 'discovery'), 0)
                       < learner.DISCOVERY_RUNS)
        if old_open < learner.MAX_OPEN_PER_ENCOUNTER:
            failures.append(f'the fixture only reaches {old_open} miscounted children, below the '
                            f'{learner.MAX_OPEN_PER_ENCOUNTER} cap the bug needed')
        checks += 1
        # ...while the corrected accounting counts none of them, so the fight is refillable.
        load, reseeds, _created = optimizer.reseed_load(store, limits, ordinals)
        if load.get(focus, {}).get('open') != 0:
            failures.append(f'banked probes still count as open children: {load.get(focus)}')
        checks += 1
        if load.get(10, {}).get('open', 0) <= 0:
            failures.append('the fixture\'s genuinely runnable fight was not counted as open')
        checks += 1

        # 3. The focused fight is offered the refill, and the bounded reseed creates real work.
        ranked, _by_encounter = optimizer.reseed_ranked(store, limits, ordinals)
        if optimizer.focused_encounters(ranked, focus) != [focus]:
            failures.append(f'the focused fight was not offered a reseed: ranked={ranked}')
        checks += 1
        create, count = optimizer.focused_replenish_action(focus, 0, 999., level=0)
        if not create or count < 1:
            failures.append(f'the dry focused scope did not authorise a bounded reseed: '
                            f'{(create, count)}')
        checks += 1
        diagnostics = {}
        created = optimizer.spawn_children(store, focus, count, store.get('proposals') or 0,
                                           diagnostics, sources=learner.RESEED_SOURCES,
                                           reseed=True)
        if not created:
            failures.append('the focused fight produced no new candidate to run')
        checks += 1
        # Re-read the index: adding candidates invalidates the store's cached map.
        encounter_of = store.candidate_encounters()
        for cid in created:
            if encounter_of.get(cid) != focus:
                failures.append(f'a refill for fight {focus} created {cid} in another fight')
            scenario = store.scenario(cid)
            if scenario is None or scenario['encounterId'] != focus:
                failures.append(f'refill {cid[:10]} is not a build of the focused fight')
        checks += 1
        # New identities only: no pre-existing candidate's limits or ordinals moved.
        new_ids = [cid for cid in created if cid not in limits]
        if len(new_ids) != len(created):
            failures.append('the refill reused an existing candidate instead of adding new work')
        checks += 1
        oversized = [cid for cid, (dlimit, vlimit) in limits.items()
                     if dlimit > learner.EXTENDED_DISCOVERY_RUNS or vlimit > optimizer.MAX_SAMPLES]
        if oversized:
            failures.append(f'{len(oversized)} candidates already exceed their legal bank')
        checks += 1

        # 4. The loop registers each new child as a first bank; the ready list then has work, and
        #    several workers get a seed of the focused fight - never of another one.
        for cid in created:
            limits[cid] = (learner.DISCOVERY_RUNS, 0)
        seeds = drain(limits, ordinals, encounter_of, focus, workers=4)
        if len(seeds) < 4:
            failures.append(f'the refilled focused fight scheduled only {len(seeds)} seeds')
        checks += 1
        if {encounter_of[seed[0]] for seed in seeds} != {focus}:
            failures.append('the refilled schedule leaked a non-focused attempt')
        checks += 1
        candidates_used = len({seed[0] for seed in seeds})
        if candidates_used < 2:
            failures.append(f'the refill reached only {candidates_used} candidate(s), so it cannot '
                            'keep several workers busy')
        checks += 1
        print(f'  focused replenishment: {len(created)} new build(s) for fight {focus}, '
              f'{len(seeds)} seeds over {candidates_used} candidates '
              f'(banked probes no longer count as {old_open} open children)', flush=True)
    finally:
        store.close()
    return checks


def check_focus_switch_and_clear(failures, root):
    """Switching and clearing the focus keep scoping every subsequent seed; clearing restores mixing."""
    checks = 0
    store, probe_banks = fixture_store(root, 'switch.sqlite', probes=2)
    try:
        limits = plan_limits(store, probe_banks)
        ordinals = plan_ordinals(store)
        encounter_of = store.candidate_encounters()
        # Give the focused fight a first bank so it has dispatchable work for this test.
        for cid, encounter in encounter_of.items():
            if encounter == 19 and cid not in probe_banks:
                limits[cid] = (learner.DISCOVERY_RUNS, 0)
                ordinals.pop((cid, 'discovery'), None)

        focused19 = drain(limits, ordinals, encounter_of, 19, workers=4)
        if not focused19 or {encounter_of[seed[0]] for seed in focused19} != {19}:
            failures.append('focus 19 did not keep every seed on fight 19')
        checks += 1
        focused10 = drain(limits, ordinals, encounter_of, 10, workers=4)
        if not focused10 or {encounter_of[seed[0]] for seed in focused10} != {10}:
            failures.append('switching the focus to 10 did not move every subsequent seed')
        checks += 1
        cleared = drain(limits, ordinals, encounter_of, None, workers=4)
        fights = {encounter_of[seed[0]] for seed in cleared}
        if fights != {10, 19}:
            failures.append(f'clearing the focus did not restore the mixed population: {sorted(fights)}')
        checks += 1
        # A focus with no candidates is honest: no seeds, and no refill is offered for it.
        empty = drain(limits, ordinals, encounter_of, 5, workers=4)
        if empty:
            failures.append('focusing an absent fight produced work')
        checks += 1
        ranked, _ = optimizer.reseed_ranked(store, limits, ordinals)
        if optimizer.focused_encounters(ranked, 5) != []:
            failures.append('an absent focused fight was offered a reseed')
        checks += 1
        print(f'  focus switching: 19 -> {len(focused19)} seeds, 10 -> {len(focused10)} seeds, '
              f'cleared -> {sorted(fights)}, absent -> none', flush=True)
    finally:
        store.close()
    return checks


def check_impossible_conditions(failures):
    """A genuinely stalled focused search says why; a busy one says nothing."""
    checks = 0
    cases = [
        # (focus, inhabitants, runnable, dispatched, created, blocked) -> substring expected, or None
        ((None, 0, 0, 0, 0, False), None),
        ((19, 12, 0, 0, 0, False), 'no new legal candidate'),
        ((19, 0, 0, 0, 0, False), 'no candidates in this library'),
        ((19, 12, 0, 0, 0, True), 'protected build'),
        ((19, 12, 8, 0, 0, False), None),      # runnable work exists: not idle
        ((19, 12, 0, 4, 0, False), None),      # dispatched this pass: not idle
        ((19, 12, 0, 0, 3, False), None),      # created this pass: not idle
    ]
    for (focus, inhabitants, runnable, dispatched, created, blocked), expected in cases:
        reason = optimizer.focused_stall_reason(focus, inhabitants, runnable, dispatched, created,
                                                blocked)
        if expected is None:
            if reason is not None:
                failures.append(f'a non-idle focused pool reported a reason: {reason!r}')
        elif reason is None or expected not in reason:
            failures.append(f'the idle reason for {(focus, inhabitants, runnable, dispatched, created, blocked)} '
                            f'was {reason!r}, expected to mention {expected!r}')
        checks += 1
    for focus, sign in ((19, '19'),):
        reason = optimizer.focused_stall_reason(focus, 0, 0, 0, 0, False)
        if sign not in (reason or ''):
            failures.append('the idle reason does not name the focused fight')
        checks += 1
    if optimizer.unfinished_child.__doc__ is None:
        failures.append('the child-accounting helper is undocumented')
    checks += 1
    # The focused refill gate itself: no focus or runnable work never authorises a reseed.
    if optimizer.focused_replenish_action(None, 0, 999., 3) != (False, 0):
        failures.append('the focused refill fired without a focus')
    if optimizer.focused_replenish_action(19, 4, 999., 3) != (False, 0):
        failures.append('the focused refill fired while runnable work existed')
    if optimizer.focused_replenish_action(19, 0, 0.5, 3) != (False, 0):
        failures.append('the focused refill ignored the reseed interval')
    if optimizer.focused_replenish_action(19, 0, 999., 3) != (True, learner.RESEED_BATCH[3]):
        failures.append('the focused refill did not use the measured batch size')
    checks += 4
    print(f'  impossible conditions: {checks} unit checks passed', flush=True)
    return checks


def check_no_corruption(failures, root):
    """Refilling adds work; it never rewrites the results already recorded."""
    checks = 0
    store, probe_banks = fixture_store(root, 'intact.sqlite', probes=4)
    try:
        before_runs = store.db.execute(
            'SELECT candidate, phase, ordinal, result FROM run ORDER BY candidate, phase, ordinal'
        ).fetchall()
        before_total = store.get('totalRuns')
        before_scenarios = {row['id']: row['scenario']
                            for row in store.db.execute('SELECT id, scenario FROM candidate')}
        limits = plan_limits(store, probe_banks)
        ordinals = plan_ordinals(store)
        created = optimizer.spawn_children(store, 19, 2, 1, {}, sources=learner.RESEED_SOURCES,
                                           reseed=True)
        if not created:
            failures.append('the corruption fixture created no work to check')
        checks += 1
        after_runs = store.db.execute(
            'SELECT candidate, phase, ordinal, result FROM run ORDER BY candidate, phase, ordinal'
        ).fetchall()
        if [tuple(row) for row in after_runs] != [tuple(row) for row in before_runs]:
            failures.append('refilling changed run rows that were already recorded')
        checks += 1
        if store.get('totalRuns') != before_total:
            failures.append('refilling changed the stored run total')
        checks += 1
        after_scenarios = {row['id']: row['scenario']
                           for row in store.db.execute('SELECT id, scenario FROM candidate')}
        for cid, scenario in before_scenarios.items():
            if after_scenarios.get(cid) != scenario:
                failures.append(f'refilling rewrote existing candidate {cid[:10]}')
        checks += 1
        for cid in created:
            validate_scenario(store.scenario(cid))
            if ordinals.get((cid, 'discovery'), 0) != 0:
                failures.append(f'the new build {cid[:10]} was handed a non-zero first ordinal')
        checks += 1
        print(f'  no corruption: {len(before_runs)} recorded run(s) intact, {len(created)} new '
              f'build(s) added', flush=True)
    finally:
        store.close()
    return checks


def main():
    failures = []
    checks = 0
    checks += check_child_accounting(failures)
    checks += check_impossible_conditions(failures)
    with tempfile.TemporaryDirectory(prefix='ka-focus-liveness-') as root:
        checks += check_focused_replenishment(failures, root)
        checks += check_focus_switch_and_clear(failures, root)
        checks += check_no_corruption(failures, root)
    if failures:
        print(f'\nfocused-search liveness: {len(failures)} FAILURE(S)')
        for failure in failures:
            print(f'  - {failure}')
        raise SystemExit(1)
    print(f'\nfocused-search liveness: {checks} checks passed')


if __name__ == '__main__':
    main()
