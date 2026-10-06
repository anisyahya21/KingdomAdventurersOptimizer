"""Bounded candidate-affine dispatch without changing authorised seeds or result ordering."""
import math

MAX_BATCH = 8
TARGET_BATCH_SECONDS = .2

DISCOVERY = 'discovery'
VALIDATION = 'validation'

# One discovery seed for every three validation seeds while both phases still have authorised work;
# whichever phase still has a free seed keeps the window full, so a drained phase never idles the pool.
PHASE_PATTERN = (DISCOVERY, VALIDATION, VALIDATION, VALIDATION)

DEFAULT_WINDOW = 256


def evidence_refresh_delay(duration, minimum=1.0):
    """Rest between full evidence views, targeting at most 10% coordinator time.

    Bound learning latency at five seconds after a completed view. Callers bypass the delay
    when no authorised work remains, and invalidate the view on explicit scope changes.
    """
    return max(minimum, min(5.0, max(0.0, duration)*9.0))


def batch_size(seconds_per_run=None):
    if seconds_per_run is None:
        return 1  # Measure unfamiliar strategies before committing to a multi-seed job.
    if not math.isfinite(seconds_per_run) or seconds_per_run <= 0:
        return 1
    return max(1, min(MAX_BATCH, int(TARGET_BATCH_SECONDS / seconds_per_run)))


def take_affine(order, first, reserved, maximum):
    """Remove at most maximum already-authorised seeds for the first candidate/phase.

    All other candidates retain their relative position, and a reserved seed is never selected.
    The first seed was already popped by the coordinator. No new ordinals are generated here.
    """
    selected = [first]
    if maximum <= 1:
        return selected
    seen = set(reserved)
    seen.add(first)
    taken = []
    for index, key in enumerate(order):
        if key[:2] == first[:2] and key not in seen:
            selected.append(key)
            seen.add(key)
            taken.append(index)
            if len(selected) >= maximum:
                break
    for index in reversed(taken):
        del order[index]
    return sorted(selected, key=lambda key: key[2])


def task_keys(task):
    return task.get('seeds') or [(task['cid'], task['phase'], task['ordinal'])]


def reserved_keys(pending):
    return {key for task in pending for key in task_keys(task)}


def _empty_cursors():
    return {'offsets': {DISCOVERY: 0, VALIDATION: 0}}


def _first_free(phase, cid, limit, ordinal, reserved):
    """Lowest authorised, unfinished, unreserved ordinal at/above `ordinal`, or None.

    Walks only the reserved entries that shadow this candidate's next seeds, never the whole
    authorised range, so a limit in the millions costs the same as a limit of one.
    """
    while ordinal < limit:
        if (cid, phase, ordinal) not in reserved:
            return ordinal
        ordinal += 1
    return None


def ready_window(limits, ordinals, reserved, focus_ids=None, maximum=DEFAULT_WINDOW, cursors=None):
    """A bounded, lazy, fair slice of the authorised-but-unstarted seeds.

    `limits` maps cid -> (discovery_limit, validation_limit); `ordinals` maps (cid, phase) -> next
    expected ordinal (so an ordinal below it is already authorised and never reissued); `reserved`
    holds seeds already inflight/completed. Seeds are partitioned by phase and interleaved: one
    discovery turn then three validation turns while both phases still have seed, falling back to the
    phase that does, so the window is never short while any authorised seed remains. Inside a phase
    the candidates rotate in the limits' own insertion order.

    `cursors` (from a previous call, or None) carries only per-phase candidate rotation offsets.
    Callers must reserve submitted seeds; a discarded window must not advance seed progress.
    Completed ordinals and reservations alone determine which seeds remain. Only returned seeds are
    materialised: a limit in the millions still yields at most `maximum` tuples.
    """
    if maximum is None:
        maximum = DEFAULT_WINDOW
    maximum = int(maximum)

    saved = cursors or _empty_cursors()
    saved_offsets = saved.get('offsets') or {}

    # candidates[phase] = ordered [(cid, limit)]; `limits` insertion order is the deterministic rotation.
    candidates = {DISCOVERY: [], VALIDATION: []}
    state = {}   # Per-call seed positions, never persisted across discarded windows.
    next_cursors = {'offsets': {phase: int(saved_offsets.get(phase, 0) or 0)
                                for phase in (DISCOVERY, VALIDATION)}}

    for cid, phase_limits in limits.items():
        if focus_ids is not None and cid not in focus_ids:
            continue
        for phase, index in ((DISCOVERY, 0), (VALIDATION, 1)):
            key = (cid, phase)
            start = int(ordinals.get(key, 0) or 0)
            state[key] = start
            if start < int(phase_limits[index]):
                candidates[phase].append((cid, int(phase_limits[index])))

    offsets = next_cursors['offsets']
    window = []

    def emit(phase):
        bucket = candidates[phase]
        while bucket:
            index = offsets[phase] % len(bucket)
            cid, limit = bucket[index]
            ordinal = _first_free(phase, cid, limit, state[(cid, phase)], reserved)
            if ordinal is None:
                del bucket[index]           # every authorised seed of this candidate is taken/reserved
                if index < offsets[phase]:
                    offsets[phase] -= 1
                continue
            window.append((cid, phase, ordinal))
            state[(cid, phase)] = ordinal+1
            offsets[phase] += 1
            return True
        return False

    turn = 0
    while len(window) < maximum:
        wanted = PHASE_PATTERN[turn % len(PHASE_PATTERN)]
        if emit(wanted):
            turn += 1
            continue
        # The wanted phase is drained for this window; keep the pool fed from the other one without
        # moving the pattern, so the weights stay right if the phase comes back into scope.
        if not emit(VALIDATION if wanted == DISCOVERY else DISCOVERY):
            break
    return window, next_cursors


def discovery_backlog(limits, ordinals, focus_ids=None):
    """Authorised discovery seeds not yet completed, summed over the population.

    `ordinals[(cid, 'discovery')]` only advances on an accepted completion, so seeds that are merely
    inflight still count: the sum is the outstanding discovery work, including runs in the pool. No
    reserved set is needed and the pass is O(candidates).
    """
    total = 0
    for cid, (dlimit, _vlimit) in limits.items():
        if focus_ids is not None and cid not in focus_ids:
            continue
        total += max(0, dlimit-ordinals.get((cid, 'discovery'), 0))
    return total
