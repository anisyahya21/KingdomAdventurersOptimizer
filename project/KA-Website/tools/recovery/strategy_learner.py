"""The Strategy Optimiser's learning model: lineage, staged evidence, operator and scale learning.

The v3 search space made genuinely different strategies reachable; this module is what turns the
*generator* into a *learner*. It exists because the previous scheduler behaved like

    pick one candidate -> run its whole bank -> wait -> propose one more

so the worker pool had almost nothing independent to do: measured runs showed `runnableDiscovery 0`
and `runnableValidation 0-3` at plan time, with the pool draining to whatever single bank had just
been opened. Three things were wrong and are fixed here:

  * **one child at a time** - a known good parent can produce many independent siblings, and the
    scheduler now keeps a reservoir of them (`RESERVOIR_SEEDS_PER_WORKER`);
  * **the two-loss rule** - a candidate that lost its first two seeds was removed from both the
    discovery and the validation list, so a sparse-reward branch (99 potential, zero earned, or a
    strong stored-attack setup) could never accumulate the evidence that makes it valuable;
  * **no memory of what worked** - every axis was rotated equally forever, and numeric mutations were
    always +-15-18% around the parent.

Nothing here scores a strategy with one scalar. An improvement is defined *per lane* against the
parent, a loss with better potential or a better stored-attack chain is a successful descendant, and
the four elite lanes keep nominating parents independently.

The module is pure with respect to combat: it only decides which candidate to generate, how much
evidence it should receive and which mutation to attempt.
"""
from __future__ import annotations

import hashlib
import json
import math
import random

#: Bumped when the learning policy changes in a way that would invalidate a comparison.
LEARNER_VERSION = 5

#: Evidence stages. Every child receives the first bank; only candidates that show something earn the
#: second, and only those that are still promising after that earn a validation bank.
DISCOVERY_RUNS = 8
EXTENDED_DISCOVERY_RUNS = 24
VALIDATION_RUNS = 64

#: Children produced per branching turn, and the cap on how many *unevaluated* children a single
#: encounter may have open at once. The cap is what keeps the reservoir honest: siblings are only
#: generated while the encounter still has evidence-bearing parents to branch from, never as an
#: open-ended speculative queue.
BRANCH_FACTOR = 4
#: ...and the cap is generous on purpose: a child costs 8 seeds, so a few dozen open children are what
#: keeps several banks runnable at once without turning the queue into a speculative backlog.
MAX_OPEN_PER_ENCOUNTER = 48

#: The reservoir the scheduler aims to keep runnable, expressed in seeds per configured worker. At 24
#: workers this is 144 runnable seeds. The larger cushion lets the coordinator publish and propose
#: without draining the queue between short native battles; the staged policy still authorises each
#: seed and the open-child cap still limits speculation.
RESERVOIR_SEEDS_PER_WORKER = 6

#: Parent sources. Lanes and regions are evidence; `exploration` is the guaranteed share that keeps a
#: lineage from being starved by a temporarily productive one.
PARENT_SOURCES = ('earned', 'potential', 'setup', 'efficiency', 'region', 'exploration', 'mean')

#: Numeric mutation ladder (multipliers of the chosen anchor value) plus one unbounded jump. The
#: ladder is what lets the learner move from broad exploration toward fine refinement without ever
#: losing the occasional large step.
STAT_SCALES = (1.05, 1.15, 1.4, 2.0, 3.0, 0.95, 0.7, 0.5)
STAT_JUMP_WEIGHT = 1.0

#: Every mutation family keeps at least this share of proposals, so a family can never be written off
#: permanently - INT, for instance, only becomes valuable once attack magic exists.
OPERATOR_FLOOR = 0.35

# --------------------------------------------------------------------------------------------
# Anti-starvation pressure control
# --------------------------------------------------------------------------------------------
# Normal branching replaces consumed work. When it cannot - the reservoir runs dry and workers sit
# idle while there is nothing useful to submit - the search injects a *small* bounded batch of new
# exploration candidates instead of idling. The trigger is sustained, not a single dip, and it is
# measured on the scheduler's own view of the pool: low useful utilisation only counts while the
# planner genuinely had nothing left to submit (never coordinator-busy, duty-cycle idle, startup or
# pause). The pressure rises with the shortfall and falls back to zero on its own once the reservoir
# is healthy again, so the mechanism is pressure-controlled rather than a permanent extra generator.

#: Useful utilisation at or above which nothing extra is created.
STARVATION_HEALTHY = 0.80
#: ...and the share of the window the coordinator's own accounted work (planning, recording,
#: publishing) may consume before low utilisation stops counting as starvation. A pool can idle
#: because the single coordinator thread cannot feed it fast enough, and creating more candidates for
#: an already-saturated coordinator is the "flood" failure mode, not a fix: the measured real-library
#: run spent ~90% of the wall clock inside the coordinator with a reservoir 1-5x its target.
COORDINATOR_SATURATED = 0.75
#: (below this utilisation, this pressure level). Ordered from the most severe shortfall.
STARVATION_LEVELS = ((0.30, 3), (0.50, 2), (0.70, 1))
#: The trailing window the utilisation is measured over, and the shortest window that may decide.
STARVATION_WINDOW_SECONDS = 12.0
STARVATION_MIN_WINDOW_SECONDS = 8.0
#: New exploration candidates created per pressure decision. Deliberately small: a 24-worker pool
#: needs a handful of extra 8-seed banks, not a speculative queue, and the next decision only comes
#: after `RESEED_INTERVAL_SECONDS` and only while the reservoir is still short of its target.
RESEED_BATCH = (0, 2, 4, 6)
RESEED_INTERVAL_SECONDS = 5.0
#: In-population bounds on starvation-created candidates. They are counted from the lineage table, so
#: they cannot be evaded by pruning a row from the candidate table, and they keep a starved search
#: from turning into the 867-child flood the earlier reservoir work had to fix.
MAX_RESEED_PER_ENCOUNTER = 16
MAX_RESEED_TOTAL = 96
#: Where a starvation reseed draws its parent from: a current elite lane, the surviving representative
#: of a quiet older lineage, one of the encounter's region nominees, or nothing at all (a genuine
#: multi-change restart, recorded as its own root).
RESEED_SOURCES = ('elite', 'historical', 'region', 'root')
#: Deliberate stat exploration for reseeds, as multipliers of the parent's own value. The normal
#: operator learner is free to down-weight `set-stat` for a stat that has not paid off yet; a
#: starvation reseed must not, or stat space quietly disappears from the search (the measured
#: branching runs had only 3-4 distinct stat vectors).
RESEED_STAT_SCALES = (1.1, 0.9, 1.2, 0.8, 1.5, 0.6, 3.0)
RESEED_STAT_SHARE = 0.45
#: ...and, occasionally, a multi-change restart rather than another small local nudge.
RESEED_RESTART_SHARE = 0.15

# --------------------------------------------------------------------------------------------
# Local exploitation (per encounter + strategy region)
# --------------------------------------------------------------------------------------------
#: Local numeric probing alongside the global search. The share is a *ceiling*: at most this
#: fraction of a branching turn may be local tuning, so global exploration (new skills, triggers,
#: weapons, formations, fresh roots, large stat jumps, historical resurrection) always keeps at
#: least `1 - LOCAL_SHARE` of every turn. Local probes are ordinary sibling candidates with ordinary
#: 8-seed discovery banks, so they never serialise the search or starve the pool.
LOCAL_SHARE = 0.35
#: Probe sizes around a promising parent: +/-5%, +/-10%, +/-20%, biased finer once a direction works.
LOCAL_SCALES = (0.95, 1.05, 0.90, 1.10, 0.80, 1.20)
LOCAL_FINE_SCALES = (0.97, 1.03, 0.95, 1.05)
#: Two-variable compensations, tested only when single-variable evidence suggests an interaction.
LOCAL_PAIRS = (('atk', 'spd'), ('atk', 'lck'), ('spd', 'lck'), ('hp', 'def'))
# Learn the individual directions before spending probes on a possible interaction.
LOCAL_PAIR_MIN_OBSERVATIONS = 3
#: Every (stat, scale) direction keeps this weight, so a direction is never permanently written off.
LOCAL_FLOOR = 0.35
#: Evidence is kept per encounter + strategy region: a direction is a property of a family, never a
#: global conclusion about a stat.
LOCAL_OUTCOMES = ('improved', 'flat', 'worsened')


def starvation_level(utilisation):
    """The pressure the controller asks for: 0 healthy, 1..3 increasing shortfall.

    Pure and deliberately tiny, so the thresholds are testable without a running search.
    """
    if utilisation is None:
        return 0
    for threshold, level in STARVATION_LEVELS:
        if utilisation < threshold:
            return level
    return 0


def window_utilisation(samples, window=STARVATION_WINDOW_SECONDS,
                       minimum=STARVATION_MIN_WINDOW_SECONDS):
    """`(utilisation, starved)` over a trailing window of `(time, idle worker-seconds, workers, starved)`.

    The samples carry the scheduler's *cumulative* idle worker-seconds, so the utilisation is the
    honest time-weighted reading over every loop pass in the window (idle worker-seconds over available
    worker-seconds) - not an average of the moments the planner happened to be awake, which would
    report a saturated pool as starved. It is `None` until the window actually spans `minimum`
    seconds: a freshly started or freshly resumed pool has no history to judge and must not be
    reseeded on the strength of one idle moment.

    `starved` is true when at least one sample in the window was taken at a moment when the planner
    wanted a worker and had nothing runnable to give it. That is what separates "the machine has
    nothing useful to do" (reseed) from "the coordinator was busy, or the pool was deliberately duty
    cycle idling" (do nothing).
    """
    if not samples:
        return None, False
    cutoff = samples[-1][0] - window
    recent = [sample for sample in samples if sample[0] >= cutoff]
    if len(recent) < 2:
        return None, False
    span = recent[-1][0] - recent[0][0]
    starved = any(bool(sample[3]) for sample in recent)
    if span < minimum:
        return None, starved
    idle = max(0.0, float(recent[-1][1]) - float(recent[0][1]))
    available = float(recent[-1][2] or 0) * span
    if available <= 0:
        return None, starved
    return max(0.0, 1.0 - idle / available), starved


def window_coordinator_fraction(samples, window=STARVATION_WINDOW_SECONDS,
                                minimum=STARVATION_MIN_WINDOW_SECONDS):
    """The share of the trailing window the coordinator's own work consumed, or `None`.

    Each sample may carry a fifth field: the scheduler's cumulative coordinator seconds (planning,
    recording, publishing). A pool that idles because one thread cannot feed it looks exactly like a
    pool that idles because there is no work, and only this reading tells them apart - which is what
    stops the anti-starvation controller from answering a coordinator bottleneck with more candidates.
    """
    recent = [sample for sample in samples if len(sample) > 4]
    if len(recent) < 2:
        return None
    cutoff = samples[-1][0] - window
    recent = [sample for sample in recent if sample[0] >= cutoff]
    if len(recent) < 2:
        return None
    span = recent[-1][0] - recent[0][0]
    if span < minimum:
        return None
    return max(0.0, (float(recent[-1][4]) - float(recent[0][4])) / span)


def store_schema(connection):
    """The lineage table. Deliberately separate from `candidate` so pruning never erases ancestry."""
    connection.executescript('''
        CREATE TABLE IF NOT EXISTS lineage(
            candidate TEXT PRIMARY KEY,
            parent TEXT,
            root TEXT NOT NULL,
            depth INTEGER NOT NULL,
            operation TEXT,
            target TEXT,
            change TEXT,
            source TEXT,
            encounterId INTEGER,
            proposal INTEGER,
            searchSpaceVersion INTEGER,
            created INTEGER NOT NULL);
        CREATE INDEX IF NOT EXISTS lineage_parent ON lineage(parent);
        CREATE INDEX IF NOT EXISTS lineage_root ON lineage(root);
        CREATE INDEX IF NOT EXISTS lineage_encounter_depth ON lineage(encounterId, depth);
        -- The per-candidate search-space indexes the learner reads on every pass. They live in a table
        -- rather than in one JSON meta blob because the population grows to hundreds of candidates and
        -- rewriting the whole blob per child made the coordinator the bottleneck it was built to
        -- remove: a row upsert is O(1) regardless of population size.
        CREATE TABLE IF NOT EXISTS candidate_meta(
            id TEXT PRIMARY KEY,
            encounter INTEGER NOT NULL,
            defeat INTEGER NOT NULL,
            region TEXT NOT NULL);
        -- The per-encounter population bound is checked before every child is added, so that count
        -- has to be an index lookup rather than a scan of the population on the coordinator thread.
        CREATE INDEX IF NOT EXISTS candidate_meta_encounter ON candidate_meta(encounter);
    ''')


def candidate_index(connection, column):
    """One column of `candidate_meta` as a `{candidate: value}` mapping."""
    if column not in ('encounter', 'defeat', 'region'):
        raise ValueError('unknown candidate_meta column')
    return {row[0]: row[1] for row in connection.execute(f'SELECT id, {column} FROM candidate_meta')}


def record_candidate_meta(connection, candidate, encounter, defeat, region):
    connection.execute('INSERT OR REPLACE INTO candidate_meta VALUES (?,?,?,?)',
                       (candidate, int(encounter), int(defeat), region))


def forget_candidate_meta(connection, candidate):
    connection.execute('DELETE FROM candidate_meta WHERE id=?', (candidate,))


def record_child(connection, candidate, parent, root, depth, operation, target, change, source,
                 encounter_id, proposal, search_space_version, created):
    connection.execute(
        'INSERT OR REPLACE INTO lineage VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
        (candidate, parent, root, int(depth), operation, str(target), str(change), source,
         None if encounter_id is None else int(encounter_id), int(proposal or 0),
         int(search_space_version), int(created)))


def lineage_rows(connection, root=None, parent=None, limit=None, encounter_id=None):
    if root is not None:
        rows = connection.execute('SELECT * FROM lineage WHERE root=? ORDER BY depth',
                                  (root,)).fetchall()
    elif parent is not None:
        rows = connection.execute('SELECT * FROM lineage WHERE parent=?', (parent,)).fetchall()
    elif encounter_id is not None:
        rows = connection.execute('SELECT * FROM lineage WHERE encounterId=? ORDER BY depth',
                                  (int(encounter_id),)).fetchall()
    else:
        rows = connection.execute('SELECT * FROM lineage ORDER BY depth').fetchall()
    return [dict(row) for row in rows][:limit] if limit else [dict(row) for row in rows]


def lineage_summary(connection, encounter_id=None):
    """Active lineages, deepest generation and descendant counts - the diagnostics the UI shows."""
    rows = [dict(row) for row in connection.execute('SELECT * FROM lineage')]
    if encounter_id is not None:
        rows = [row for row in rows if row['encounterId'] == int(encounter_id)]
    roots = {}
    for row in rows:
        entry = roots.setdefault(row['root'], dict(root=row['root'], members=0, depth=0))
        entry['members'] += 1
        entry['depth'] = max(entry['depth'], int(row['depth']))
    return dict(entries=len(rows), lineages=len(roots), deepest=max(
        (entry['depth'] for entry in roots.values()), default=0), byLineage=roots)


def strategy_region(scenario):
    """A coarse strategy region: materially different approaches, not numerical noise.

    Two candidates share a region when they carry the same skill *set* per human, the same trigger
    pattern, the same weapon behaviour, the same placement slot and the same coarse stat regime. Tiny
    stat nudges inside one regime stay in one region; a different skill set or a different stat regime
    is a different region.
    """
    from search_contract import CORE_STATS, INT_STAT
    parameters = {**CORE_STATS, **INT_STAT}
    humans = []
    for unit in scenario.get('ownUnits') or []:
        # A supplied or imported scenario need not be a full synthetic-human build (the store's own
        # tests use deliberately minimal ones), so a unit that cannot be described coarsely is simply
        # not part of the region rather than an error on the candidate-insert path.
        if not isinstance(unit, dict):
            continue
        if not unit.get('human'):
            continue
        buckets = []
        for stat, pid in sorted(parameters.items(), key=lambda item: item[1]):
            entry = (unit.get('parameters') or {}).get(pid) or \
                (unit.get('parameters') or {}).get(str(pid)) or {}
            value = int(entry.get('rawValue') or 0)
            # Four coarse bands per stat: a regime change is visible, a 15% nudge inside a band is not.
            magnitude = max(1, abs(value))
            buckets.append(int(math.log10(magnitude) * 4))
        humans.append((tuple(sorted(unit['skills'])), tuple(unit['invocationLevels']),
                       tuple(buckets), int(unit.get('weaponId') or 0),
                       int(unit.get('grid') if unit.get('grid') is not None else -1)))
    payload = json.dumps(humans, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()[:16]


def improved_lanes(parent, child):
    """Which lanes the child improved on, compared with its parent, using the objective's orderings.

    A lane counts as improved when the child qualifies for it and either the parent did not qualify or
    the child's lane key is strictly better. Losing seeds cannot take an improvement away: the lane
    keys are built from maxima (earned, potential, stored-attack chain) plus reliability as a *tie*
    break, exactly as the objective defines them.
    """
    import strategy_optimizer as optimizer
    better = []
    for lane in optimizer.LANE_NAMES:
        if not optimizer.lane_qualifies(child, lane):
            continue
        if not optimizer.lane_qualifies(parent, lane):
            better.append(lane)
            continue
        if optimizer.lane_key(child, lane) > optimizer.lane_key(parent, lane):
            better.append(lane)
    return better


def promotion_stage(record, parent, better_lanes, is_new_region, encounter_rank=None,
                    open_siblings=0):
    """How much more evidence this candidate deserves: `stop`, `extend` or `validate`.

    This is the replacement for `two losses -> defer`. It is deliberately staged rather than
    threshold-based on wins alone:

      * a child that improved a lane, sits in the top ranks of one of its encounter's lanes, opened a
        new region, or merely has an unfinished comparison against its parent gets more evidence;
      * a child that shows nothing beyond its parent *and* ranks below the encounter's own population
        stops after its first bank, so worthless candidates still cost only 8 seeds.

    Reliability is an input (it is a tie-break in every lane key), never a gate.
    """
    if record is None:
        return 'stop'
    if better_lanes or is_new_region:
        return 'extend'
    if parent is None:
        # A supplied baseline has no parent to be compared with: it is the reference, so it earns the
        # full validation bank as before.
        return 'extend'
    if encounter_rank is not None and encounter_rank < 8:
        return 'extend'
    if int(record.get('progressRuns') or 0) and (record.get('earnedMax') or 0) > 0:
        # A converted win with no lane improvement is still worth the second bank: the comparison may
        # simply be against a strong parent.
        return 'extend'
    return 'stop'


def _weighted_choice(rng, weights):
    """One key from `{key: weight}` by weight; never raises on an empty or zeroed table."""
    total = sum(weights.values())
    if total <= 0:
        return next(iter(weights))
    target = rng.random() * total
    upto = 0.0
    for key, weight in weights.items():
        upto += weight
        if target <= upto:
            return key
    return next(reversed(weights))


def operator_weights(stats, operations):
    """Bounded operator weights: productive families get more proposals, nothing is ever zeroed.

    `stats` is `{operation: {'attempts': n, 'improved': n, ...}}` for the encounter being searched.
    A family that produced useful descendants keeps a share proportional to its successes; a family
    that has produced nothing keeps `OPERATOR_FLOOR`, so it can always re-earn its share later (INT is
    the canonical example: it is worthless until the branch that equips attack magic exists).
    """
    weights = {}
    for operation in operations:
        entry = (stats or {}).get(operation) or {}
        attempts = int(entry.get('attempts') or 0)
        improved = int(entry.get('improved') or 0)
        rate = (improved + 1.0) / (attempts + 4.0)
        weights[operation] = max(OPERATOR_FLOOR, 1.0 + 2.0 * improved * rate)
    return weights


def choose_operator(stats, operations, rng):
    return _weighted_choice(rng, operator_weights(stats, operations))


def scale_weights(stats, scales=STAT_SCALES):
    """Scale weights for one (encounter, stat): larger steps survive only while they keep helping."""
    weights = {}
    for scale in scales:
        entry = (stats or {}).get(str(scale)) or {}
        attempts = int(entry.get('attempts') or 0)
        improved = int(entry.get('improved') or 0)
        weights[str(scale)] = max(OPERATOR_FLOOR, 1.0 + 2.0 * improved
                                  - 0.15 * max(0, attempts - improved))
    weights['jump'] = STAT_JUMP_WEIGHT + sum(
        1.0 for entry in (stats or {}).values() if entry.get('improved'))
    return weights


def choose_stat_target(rng, value, minimum, maximum, scale_stats, anchor=None, scales=None):
    """One numeric mutation target: a learned local step, or an occasional jump into the interval.

    The anchor is the best value this stat has been seen to *improve* at (evidence about where the
    interesting region is), falling back to the candidate's own value. Both directions are explored,
    and the response is never assumed monotone - a larger stat is not assumed better.

    `scales` overrides the ladder (the anti-starvation reseed path uses its own deliberate
    +10/-10/+20/-20 ladder so stat space cannot be dropped by a temporarily unfavourable weighting).
    """
    base = int(anchor if anchor is not None else value)
    scale = _weighted_choice(rng, scale_weights(scale_stats, scales or STAT_SCALES))
    if scale == 'jump' or base <= 0:
        target = rng.randint(int(minimum), int(maximum))
    else:
        factor = float(scale)
        direction = 1.0 if rng.random() < 0.5 else -1.0 if factor > 1 else 1.0
        target = int(round(base * (factor if direction > 0 else 1.0 / factor)))
    target = max(int(minimum), min(int(maximum), target))
    if target == value:
        target = max(int(minimum), min(int(maximum), value + (1 if value < maximum else -1)))
    return target, scale


def bump(stats, key, field, amount=1):
    """Accumulate one counter into a nested stats dict, returning it for storage."""
    entry = stats.setdefault(str(key), {})
    entry[field] = int(entry.get(field) or 0) + int(amount)
    return stats


def productivity(lineage_rows_by_root):
    """{root: {'children': n, 'improved': n, 'rate': float}} for parent weighting."""
    result = {}
    for row in lineage_rows_by_root:
        entry = result.setdefault(row['root'], dict(children=0, improved=0, depth=0))
        entry['children'] += 1
        entry['improved'] += 1 if row.get('improved') else 0
        entry['depth'] = max(entry['depth'], int(row.get('depth') or 0))
    for entry in result.values():
        entry['rate'] = (entry['improved'] + 1.0) / (entry['children'] + 2.0)
    return result


def local_scale_bucket(fraction):
    """The signed percent bucket an observed change belongs to (nearest rung of the local ladder)."""
    if not fraction:
        return 0
    percent = fraction*100
    rungs = [int(round((scale-1)*100)) for scale in LOCAL_SCALES]
    return min(rungs, key=lambda value: abs(value-percent))


def local_key(encounter, region, unit, stat, percent):
    """One direction for one fighter in one strategy region and encounter."""
    return f'{encounter}:{region}:{unit}:{stat}:{percent:+d}'


def local_evidence_for(evidence, encounter, region, unit, stat):
    """`{percent: entry}` - evidence for one fighter/stat inside one encounter and region."""
    prefix = f'{encounter}:{region}:{unit}:{stat}:'
    entries = {}
    for key, entry in (evidence or {}).items():
        if key.startswith(prefix):
            try:
                entries[int(key[len(prefix):])] = entry
            except ValueError:
                continue
    return entries


def choose_local_percent(rng, entries, scales=LOCAL_SCALES):
    """One probe size for a stat, weighted by its own contextual evidence.

    A direction that improved something gains weight; one that worsened it loses weight but keeps
    `LOCAL_FLOOR`, so no direction is ever permanently written off and an unprobed stat still gets
    exploratory probes. Once anything improved at a coarse rung the finer rungs take over, which is
    the "probe more finely around a region that works" step.
    """
    if any((entry or {}).get('improved') for entry in entries.values()):
        scales = LOCAL_FINE_SCALES
    weights = {}
    for scale in scales:
        percent = int(round((scale-1)*100))
        entry = entries.get(percent) or {}
        improved = int(entry.get('improved', 0) or 0)
        worsened = int(entry.get('worsened', 0) or 0)
        weights[percent] = max(LOCAL_FLOOR, LOCAL_FLOOR + improved*2.0 - worsened*1.0)
    return _weighted_choice(rng, weights)


def local_outcome(better_lanes, worse_lanes):
    """`improved` / `worsened` / `flat` for one local probe, from the objective's own lane orderings."""
    if better_lanes:
        return 'improved'
    if worse_lanes:
        return 'worsened'
    return 'flat'
