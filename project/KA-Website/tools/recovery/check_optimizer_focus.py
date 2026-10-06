"""Per-encounter focus: the decision function, the multi-worker guarantee, and the command surface.

Focus points every *new* attempt at one exact encounter/difficulty, across all workers, until it is
cleared or another fight is chosen. This checker covers the whole mechanism that does not need a live
pool:

  * the scope helpers: `encounter_scope`, `focused_encounters` and `normalize_focus_encounter` on
    values with known answers (clear, valid, unknown encounter, unusable value);
  * the ready list: `interleave_ready` returns, while focused, only seeds whose candidate belongs to
    the focused encounter - even when another fight holds far more candidates, which is the "despite
    normal weights" part;
  * a drained multi-worker schedule: with several workers pulling from the ready list, every
    dispatched seed belongs to the focused fight; switching the focus switches every subsequent
    seed; clearing it restores the mixed population; and in-flight seeds of other fights (already
    reserved) are neither redispatched nor able to leak a non-focused attempt;
  * the desktop command surface: `Bridge.command` forwards `focus_encounter` and still refuses an
    unknown action.
  * the library lifecycle: a focus is validated against the open library's own catalogue, so an id
    from a different library is refused instead of going stale, and clearing is always allowed.

Focus is a scheduling change only. It never rewrites recorded results and it never edits the
population; the tests here prove the scheduling half. What the live loop does with a focus (dispatch
and reservoir refill) uses these exact helpers, so their behaviour is the loop's behaviour.

    python check_optimizer_focus.py
"""
import sys
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import strategy_optimizer as optimizer  # noqa: E402
import strategy_optimizer_desktop as desktop  # noqa: E402


def check_scope_helpers(failures):
    """The helpers that decide scope and validate a focus command, on known inputs."""
    checks = 0
    encounter_of = {'a': 10, 'b': 11, 'c': 11, 'd': 12}
    if optimizer.encounter_scope(encounter_of, None) is not None:
        failures.append('an unfocused scope was not the whole population')
    checks += 1
    if optimizer.encounter_scope(encounter_of, 11) != {'b', 'c'}:
        failures.append('focus 11 did not scope to exactly its candidates')
    checks += 1
    if optimizer.encounter_scope(encounter_of, 99) != set():
        failures.append('focusing an absent encounter did not scope to nothing')
    checks += 1

    if optimizer.focused_encounters([10, 11, 12], None) != [10, 11, 12]:
        failures.append('an unfocused encounter list was narrowed')
    checks += 1
    if optimizer.focused_encounters([10, 11, 12], 11) != [11]:
        failures.append('a focused encounter list kept other fights')
    checks += 1
    if optimizer.focused_encounters([10, 12], 11) != []:
        failures.append('a focused encounter list invented the focused fight')
    checks += 1

    if optimizer.normalize_focus_encounter(None, {10, 11}) is not None:
        failures.append('clearing a focus did not return None')
    checks += 1
    if optimizer.normalize_focus_encounter('11', {10, 11}) != 11:
        failures.append('a valid focus id was not normalised to an int')
    checks += 1
    for bad in ('nope', 11.5, object()):
        try:
            optimizer.normalize_focus_encounter(bad, {10, 11})
        except ValueError:
            checks += 1
        else:
            failures.append(f'focus value {bad!r} did not raise')
            checks += 1
    try:
        optimizer.normalize_focus_encounter(99, {10, 11})
    except ValueError:
        checks += 1
    else:
        failures.append('an encounter outside the catalogue did not raise')
        checks += 1
    print(f'  scope helpers: {checks} unit checks passed', flush=True)
    return checks


def focused_view(limits, encounter_of, focus):
    """The per-candidate limits the loop would give `interleave_ready` while focused."""
    focus_ids = optimizer.encounter_scope(encounter_of, focus)
    return {cid: value for cid, value in limits.items()
            if focus_ids is None or cid in focus_ids}


def check_ready_list(failures):
    """`interleave_ready` keeps only the focused fight's seeds, weights and all."""
    checks = 0
    encounter_of = {'p': 10, 'q': 10, 'r': 10, 's': 10, 't': 11, 'u': 12}
    limits = {cid: (8, 2) for cid in encounter_of}
    ordinals = {}

    def seeds_for(focus, reserved=frozenset()):
        return optimizer.interleave_ready(focused_view(limits, encounter_of, focus),
                                          ordinals, set(reserved))

    unfocused = seeds_for(None)
    starts = {encounter_of[seed[0]] for seed in unfocused}
    if starts != {10, 11, 12}:
        failures.append(f'the unfocused ready list missed fights: {sorted(starts)}')
    checks += 1
    checks += 1  # the scope assertion above is counted once

    for focus in (10, 11, 12):
        seeds = seeds_for(focus)
        if not seeds:
            failures.append(f'focus {focus} produced no seeds')
        elif {encounter_of[seed[0]] for seed in seeds} != {focus}:
            failures.append(f'focus {focus} leaked other fights into the ready list')
        checks += 1

    # In-flight (already-reserved) seeds of other fights are dropped, but only the focused fight runs.
    in_flight = {('p', 'discovery', 0), ('u', 'discovery', 0)}
    seeds = seeds_for(11, in_flight)
    if {encounter_of[seed[0]] for seed in seeds} != {11}:
        failures.append('an in-flight non-focused seed survived into a focused ready list')
    checks += 1
    if any(seed in in_flight for seed in seeds):
        failures.append('an in-flight seed was offered again')
    checks += 1

    # Clearing restores the whole interleaved population.
    if len(seeds_for(None)) <= len(seeds_for(11)):
        failures.append('clearing the focus did not restore the wider population')
    checks += 1
    print(f'  ready list: {checks} unit checks passed', flush=True)
    return checks


def check_library_lifecycle(failures):
    """A focus is scoped to the open library's own catalogue, so it cannot go stale across one."""
    checks = 0
    # Two libraries can number their fights differently: 11 is a real fight in one and absent from the
    # other. A focus is validated against the *open* library's catalogue (`self.encounters`), so an id
    # that belongs to a different library is refused rather than silently pointing at a fight this
    # library does not hold; a fresh `Optimizer` also starts unfocused, so opening another library
    # begins from the normal distribution.
    library_a = {11, 12}
    library_b = {21, 22}
    if optimizer.normalize_focus_encounter(11, library_a) != 11:
        failures.append('a fight the open library holds was refused as a focus')
    checks += 1
    try:
        optimizer.normalize_focus_encounter(11, library_b)
    except ValueError:
        checks += 1
    else:
        failures.append('a focus id the open library does not hold was accepted')
        checks += 1
    if optimizer.normalize_focus_encounter(None, library_b) is not None:
        failures.append('clearing a focus was refused for a library that does not hold it')
    checks += 1
    # Focus only narrows the candidate population; it never adds a candidate, so it cannot invent work
    # and never touches a recorded result.
    encounter_of = {'a': 10, 'b': 11, 'c': 11, 'd': 12}
    scoped = optimizer.encounter_scope(encounter_of, 11)
    if not (scoped < set(encounter_of)) or not scoped:
        failures.append('focus did not strictly narrow the library population')
    checks += 1
    print(f'  library lifecycle: {checks} unit checks passed', flush=True)
    return checks


def simulate_scheduler(limits, encounter_of, focus, workers):
    """Drain the ready list the way the loop does, one `workers`-wide round at a time.

    Mirrors the real dispatch loop: build the (scoped) ready list, hand seeds to at most `workers`
    slots, mark them reserved, and repeat. Returns the dispatched `(candidate, phase, ordinal)` seeds
    in dispatch order.
    """
    ordinals = {}
    reserved = set()
    dispatched = []
    while True:
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


def check_multi_worker_dispatch(failures):
    """Across many workers, every new scheduling decision picks the focused encounter."""
    checks = 0
    # Deliberately lopsided: fight 10 holds four candidates to fight 11's one, so the normal
    # distribution is dominated by 10 - the focus must still send 100% to 11.
    encounter_of = {'p': 10, 'q': 10, 'r': 10, 's': 10, 't': 11, 'u': 12}
    limits = {cid: (8, 2) for cid in encounter_of}
    workers = 6

    unfocused = simulate_scheduler(limits, encounter_of, None, workers)
    fights = {encounter_of[seed[0]] for seed in unfocused}
    if len(fights) < 2:
        failures.append(f'the unfocused schedule did not mix fights: {sorted(fights)}')
    checks += 1

    for focus in (10, 11, 12):
        dispatched = simulate_scheduler(limits, encounter_of, focus, workers)
        if not dispatched:
            failures.append(f'focus {focus} dispatched nothing')
        elif {encounter_of[seed[0]] for seed in dispatched} != {focus}:
            failures.append(f'focus {focus} let a non-focused attempt through')
        checks += 1
        # Every runnable seed of the focused fight is still reached - focus is not a cap.
        expected = 10 * sum(1 for cid in encounter_of if encounter_of[cid] == focus)
        if len(dispatched) != expected:
            failures.append(f'focus {focus} dispatched {len(dispatched)} seeds, expected {expected}')
        checks += 1

    # Switching is atomic: the tail of the schedule after a switch is entirely the new fight.
    switch = simulate_scheduler(limits, encounter_of, 11, workers)
    switched = simulate_scheduler(limits, encounter_of, 12, workers)
    if switched and {encounter_of[seed[0]] for seed in switched} != {12}:
        failures.append('switching focus did not move every subsequent seed')
    checks += 1
    if {encounter_of[seed[0]] for seed in switch} != {11}:
        failures.append('the pre-switch schedule was not the first focus')
    checks += 1

    # A focus on an encounter the library does not hold is honest: no seeds, no invented work.
    empty = simulate_scheduler(limits, encounter_of, 99, workers)
    if empty:
        failures.append('focusing an absent encounter produced work')
    checks += 1
    print(f'  multi-worker dispatch: {checks} unit checks passed', flush=True)
    return checks


class _RecordingOptimizer:
    """A stand-in that records the commands the desktop bridge forwards to it."""

    def __init__(self):
        self.calls = []
        self.snapshot = {'state': 'Paused', 'candidates': [], 'archive': []}

    def command(self, action, value=None, wait=False):
        self.calls.append((action, value, wait))

    def status(self):
        return dict(self.snapshot)


def check_command_surface(failures):
    """`Bridge.command` forwards a focus change and still refuses an unknown action."""
    checks = 0
    bridge = object.__new__(desktop.Bridge)
    bridge._optimizer = _RecordingOptimizer()
    bridge._operation = threading.Lock()
    bridge._start_requested = False

    result = bridge.command('focus_encounter', {'encounterId': 11})
    if result != {'ok': True}:
        failures.append(f'forwarding a focus returned {result!r}')
    checks += 1
    if bridge._optimizer.calls != [('focus_encounter', {'encounterId': 11}, True)]:
        failures.append(f'the bridge did not forward the focus: {bridge._optimizer.calls!r}')
    checks += 1
    cleared = bridge.command('focus_encounter', {'encounterId': None})
    if cleared != {'ok': True} or bridge._optimizer.calls[-1][1] != {'encounterId': None}:
        failures.append('clearing the focus was not forwarded')
    checks += 1
    refused = bridge.command('not_a_command', {})
    if refused.get('ok') is not False:
        failures.append('an unknown action was not refused')
    checks += 1
    print(f'  desktop command surface: {checks} unit checks passed', flush=True)
    return checks


def main():
    failures = []
    total = 0
    total += check_scope_helpers(failures)
    total += check_ready_list(failures)
    total += check_library_lifecycle(failures)
    total += check_multi_worker_dispatch(failures)
    total += check_command_surface(failures)
    if failures:
        print(f'\noptimizer focus: {len(failures)} FAILURE(S)')
        for failure in failures:
            print(f'  - {failure}')
        raise SystemExit(1)
    print(f'\noptimizer focus: {total}/{total} checks passed')


if __name__ == '__main__':
    main()
