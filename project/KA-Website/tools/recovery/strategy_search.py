"""Bounded local strategy search over legal native formation/skill-order variation.

Public contract
---------------
`default_request()` returns a JSON-serializable request for the frozen UREF base scenario;
`run_search(request, on_progress=None, cancelled=None)` returns a JSON dictionary with schema
`ka-strategy-search-result-1`. The CLI accepts `--request FILE --output FILE [--progress FILE]
[--cancel-file FILE]` and `--write-default FILE`.

The search invents no rule and explores two legal source-input axes, selected by `searchAxis`
(`both` default, `formation`, `skill-order`):

* `formation` - the input order of `ownUnits`, which is the only input that can move an own fighter:
  the recovered native placement (`combat_initial_state.formation`) sorts members by
  `(priority, -effectiveDefense, incomingIndex)`, so an `ownUnits` permutation is a distinct input
  only when it flips the relative order of two units that share the same native priority AND the same
  effective defense AND carry different content. That axis and its legality helpers are the canonical
  ones in `combat_search`.
* `skill-order` - the activation priority order of a unit's own skills. The recovered simulator keeps
  input skill order (`combat_skill_selection.active_skill_infos`, then `attack_skill_candidates`, and
  `SharedController.choose` invokes the first candidate whose invocation roll passes). Only the
  positions of skills the recovered static filter can keep (`flags & 8`, not `flags & 32`, category
  != 2) are permuted, and only among those positions, so skill ownership, equipment, roster and each
  skill's invocation level stay exactly the same. A unit whose permutable active skills do not all
  share one invocation level is skipped: the native filtered-ordinal `invocationLevels` mapping would
  then change under reordering. Only human own units are permuted: a monster is skipped entirely
  because the native additional-slot legality and the fixed first slot of a monster are not
  recovered, so reordering a monster's skill list would invent a rule. Before
  `combat_search.skill_arrangements` is called for any unit, that unit's raw permutation upper bound
  `len(skills)!` is checked against `MAX_UNIT_SKILL_PERMUTATIONS`, so an oversized skill array fails
  fast instead of enumerating a factorial. Player evidence that order matters is the recovered guide
  `RE-evidence/G1.1/20260910T132730Z-93f4c6/restore-check/workspace/tmp-guide.md` ("Skill order
  matters, so put the skills you want used most the top") plus the ordered `skills`/`invocationLevels`
  source contract in `RE-evidence/20260919-configurable-battle-setup/BATTLE-SETUP-16.3.md`.

Every generated candidate is re-validated with `combat_scenario.load_scenario`; formation candidates
also have their placement recomputed with `combat_setup.prepare_setup` before they are allowed to
run. Roster, owned equipment, levels, skills, resources and the per-run `mathSeed`/`libSeed`/
`tickLimit` handling are preserved exactly outside the searched axis.

Objectives
----------
`battle-outcome` scores real end-of-battle state. `generated-boxes` cannot be scored as an exact box
yield: the native automatic Ending->Finish producer is unproven, so `yieldTrusted` is false and no
dispatched-chest count is trusted. That objective therefore scores exactly one explicitly named real
pre-settlement metric already present in the run - the count of native prize-callback trace events
(`kind == "prize"`) recorded at or before the verdict tick (`preVerdictPrizeCallbacks`). Results
under it are marked `provisional` and never claim dispatched, queued or owned boxes. If a simulator
ever stops exposing that metric, the objective is rejected with a clear error instead of guessing.

Nothing here is a native validation: only the frozen UREF baseline formation/seed pair (7/8) has
been compared with native through the Ending transition; generated candidates have not.

Improvement
-----------
A candidate can be `best` or count as an improvement only when it and the baseline both completed
(uncensored) on the full requested seed bank at the same `tickLimit`. A censored or errored run on
any requested seed makes that candidate non-comparable: it is still reported with its honest partial
score, but it can never be `best` or an improvement, so the `-ticks` tiebreak can never be won by
completing fewer seeds.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import random
import sys
import time
from collections import Counter
from copy import deepcopy
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORKSPACE = HERE.parents[2]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import combat_search
from combat_search import unit_content_signature  # canonical unit identity (name excluded)
from combat_scenario import ScenarioError, load_scenario
from combat_sandbox import run_scenario
from combat_setup import prepare_setup

REQUEST_SCHEMA = 'ka-strategy-search-1'
RESULT_SCHEMA = 'ka-strategy-search-result-1'
SCENARIO_SCHEMA = 'ka-special-combat-research-1'

DEFAULT_BASE_SCENARIO_PATH = WORKSPACE / 'RE-evidence/20260920-user-wairo-strategy/scenarios/UREF.scenario.json'
DEFAULT_CANDIDATE_COUNT = 4
MAX_CANDIDATE_COUNT = 64
DEFAULT_SEARCH_SEED = 1
DEFAULT_SEEDS = ({'mathSeed': 7, 'libSeed': 8},)
MAX_SEEDS = 8
#
# The safety horizon. It is one simulation-limit, not a combat rule: a battle that reaches it is
# reported as "no verdict by limit", never as a loss.
#
# Measured 22 September 2026 against the live Search Space v3 library (`strategiesv6.sqlite`,
# 24 workers): the 7,000-tick tail was only the visible part of the problem. The one cell that
# actually censors is encounter 10 "Vs. Aloha Kairobot (Hard)" - 687 of 2,296 attempts (29.9%) ended
# with no verdict at 10,000 ticks, while every other cell (including 11 "Vs. Aloha Kairobot
# (Extreme)") censored nothing at all. Re-running 40 of those exact stored scenario/seed pairs at
# escalating horizons (`RE-evidence/20260922-horizon/horizon-probe.json`) resolved **0/40 by 10,000**
# and **40/40 by 20,000**, with verdict ticks of min 11,675 / median 16,702 / p90 19,067 / max
# **19,780** (16:29 of game time), 27 wins and 13 losses. 30,000 ticks is therefore the cap: 1.5x
# the longest legitimate resolution observed, not a few percent over it.
#
# A resolved battle no longer pays the horizon (see `TERMINAL_VERDICT_POLICY`): the runner stops at
# the verdict, so the larger cap costs extra ticks only for the fights that genuinely need them.
DEFAULT_TICK_LIMIT = 30000
MAX_TICK_LIMIT = 30000

#: The finish policy optimiser scenarios are generated with. Victory or defeat is terminal: the run
#: ends at the recovered verdict, and only a battle that reaches `DEFAULT_TICK_LIMIT` with no verdict
#: is reported as "no verdict". The recovered post-death stored-attack chain is pre-verdict activity
#: (boss death happens well before the final annihilation), so the cut preserves it exactly - measured
#: against real runs, only `ticks` and `finishBoundary` differ from the old at-horizon cut, while
#: prize callbacks, the reward reading and every progress metric are identical.
TERMINAL_VERDICT_POLICY = 'on-verdict'
OBJECTIVES = ('battle-outcome', 'generated-boxes')
DEFAULT_OBJECTIVE = 'generated-boxes'
SEARCH_AXIS_CHOICES = ('both', 'formation', 'skill-order')
DEFAULT_SEARCH_AXIS = 'both'

# Resource guards: one simulation at a time, bounded request sizes, no snapshot history.
MAX_PLANNED_RUNS = 64
MAX_ENUMERATED_ARRANGEMENTS = 4096
# `combat_search.skill_arrangements` walks factorial(len(skills)) raw permutations of one unit before
# the global product cap below can apply, so every unit is bounded before that helper is called.
MAX_UNIT_SKILL_PERMUTATIONS = 40320
MAX_BASE_SCENARIO_BYTES = 262144
MAX_OWN_UNITS = 64

PRE_SETTLEMENT_BOX_METRIC = 'preVerdictPrizeCallbacks'
PRE_SETTLEMENT_BOX_METRIC_SOURCE = ('native prize-callback trace events (kind == "prize") at or '
                                    'before the verdict tick; pre-settlement, never a dispatched or '
                                    'owned chest count')

CORE_MODEL_FILE = 'combat_shared_controllers.py'
CORE_MODEL_SHA256 = 'dbf2bd9bc7ebf9bf0487f9304e2c744b787fc99e1a2d4732213ce2899d240287'
SIMULATOR_FILES = ('combat_shared_controllers.py', 'combat_initial_state.py', 'combat_setup.py',
                   'combat_sandbox.py', 'combat_search.py')

VERDICT_LABELS = {1: 'win (opposing team annihilated)', 2: 'loss (own team annihilated)'}
SCORE_DEFINITIONS = {
    'battle-outcome': ('lexicographic [wins, totalOwnSurvivors, -totalTicks] over the full requested '
                       'seed bank at one tickLimit; only entries that completed (uncensored) on every '
                       'requested seed are comparable and ranked, higher is better'),
    'generated-boxes': ('lexicographic [totalPreVerdictPrizeCallbacks, wins, -totalTicks] over the '
                        'full requested seed bank at one tickLimit; only entries that completed '
                        '(uncensored) on every requested seed are comparable and ranked; provisional '
                        'pre-settlement metric, higher is better'),
}
IMPROVEMENT_DEFINITION = ('improvement is true only when a candidate and the baseline both completed '
                          '(uncensored) on the full requested seed bank at the same tickLimit, on the '
                          'identical paired seed pairs, and the candidate scores strictly higher; a '
                          'censored or errored run on any requested seed makes that candidate '
                          'non-comparable, so a partial/censored candidate can never be best or an '
                          'improvement')
METRIC_LABELS = {
    'verdict': 'native battle verdict (1 win, 2 loss) or null when the battle stayed unresolved',
    'censored': 'true when the tick horizon was reached before IsAnnihilated (no verdict)',
    'ticks': 'ticks simulated before the run stopped',
    'battleFrame': 'engine battle frame at the stop',
    'verdictTick': 'tick of the native verdict, null when censored',
    'ownSurvivors': 'own fighters whose state is not Leaving (8) at the stop',
    'ownLeaving': 'own fighters in Leaving state 8 at the stop',
    'enemySurvivors': 'opposing fighters whose state is not Leaving (8) at the stop',
    'prizeCallbacks': 'engine prize-callback counter for the run (real, pre-settlement)',
    'prizeTraceEvents': 'trace events with kind == "prize" (real, pre-settlement)',
    PRE_SETTLEMENT_BOX_METRIC: PRE_SETTLEMENT_BOX_METRIC_SOURCE,
    'queuedChestAwards': 'diagnostic Finish queue size (never a trusted yield)',
    'dispatchedChestAwards': 'diagnostic spawned chest entities (never a trusted yield)',
    'yieldTrusted': 'simulator fail-closed yield flag; false while the automatic Finish producer is unproven',
    'autoFinishProducerProven': 'simulator flag; false in the current canonical model',
    'rewardEntitlementAwarded': 'reward-entitlement awarded count (0 on a loss, null when unknown)',
    'rewardEntitlementBasis': 'rule that produced the entitlement count',
    'attackAttempts': 'attack events emitted',
    'heals': 'heal events emitted',
    'commands': 'enqueue events emitted',
    'failedReleases': 'release events that did not fire',
    'elapsedSeconds': 'wall-clock seconds for this run',
}


class StrategySearchError(ValueError):
    """Invalid request, unavailable objective or illegal candidate; the search never guesses."""


def _require(condition, message):
    if not condition:
        raise StrategySearchError(message)


def _bounded_int(value, name, minimum, maximum, default=None):
    if value is None:
        return default
    _require(type(value) is int, f'{name} must be an integer')
    _require(minimum <= value <= maximum, f'{name} must be between {minimum} and {maximum}')
    return value


def _sha256_text(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _sha256_file(path):
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def _canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


def scenario_sha256(scenario):
    """Stable identity of one scenario dictionary (sorted keys, compact separators)."""
    return _sha256_text(_canonical_json(scenario))


def simulator_hashes():
    """SHA256 of the canonical simulator modules actually used by this search."""
    files = []
    for name in SIMULATOR_FILES:
        path = HERE / name
        files.append(dict(path=name, sha256=_sha256_file(path)))
    core = next((entry for entry in files if entry['path'] == CORE_MODEL_FILE), None)
    return dict(files=files, coreModelFile=CORE_MODEL_FILE, coreModelSha256=CORE_MODEL_SHA256,
                coreModelMatches=bool(core and core['sha256'] == CORE_MODEL_SHA256))


def default_request():
    """A JSON-serializable request for the frozen UREF base scenario."""
    return {
        'schema': REQUEST_SCHEMA,
        'candidateCount': DEFAULT_CANDIDATE_COUNT,
        'searchSeed': DEFAULT_SEARCH_SEED,
        'seeds': [dict(seed) for seed in DEFAULT_SEEDS],
        'tickLimit': DEFAULT_TICK_LIMIT,
        'objective': DEFAULT_OBJECTIVE,
        'searchAxis': DEFAULT_SEARCH_AXIS,
        'baseScenario': None,
        'baseScenarioSource': str(DEFAULT_BASE_SCENARIO_PATH.relative_to(WORKSPACE)),
        'baseScenarioSha256': _sha256_file(DEFAULT_BASE_SCENARIO_PATH),
        'notes': [
            'baseScenario omitted means the frozen UREF scenario is copied from baseScenarioSource',
            'objective generated-boxes is provisional: it scores only the named pre-settlement '
            'prize-callback count, never a dispatched or owned box count',
            'searchAxis chooses the legal source-input axes: formation (ownUnits order inside native '
            'formation tie groups), skill-order (activation priority order of each unit own active '
            'skills, preserving skill ownership, equipment and invocation levels), or both',
            'a candidate counts as best/improvement only when it completed uncensored on the full '
            'requested seed bank, the same bank the baseline completed',
        ],
    }

def _resolve_base_scenario(request):
    """(scenario, source label, sha256) for the inline base scenario or the frozen default copy."""
    inline = request.get('baseScenario')
    if inline is None:
        path = DEFAULT_BASE_SCENARIO_PATH
        _require(path.is_file(), f'frozen base scenario not found at {path}')
        try:
            text = path.read_text(encoding='utf-8')
        except OSError as error:
            raise StrategySearchError(f'cannot read frozen base scenario: {error}') from error
        _require(len(text.encode('utf-8')) <= MAX_BASE_SCENARIO_BYTES,
                 f'frozen base scenario exceeds {MAX_BASE_SCENARIO_BYTES} bytes')
        try:
            scenario = json.loads(text)
        except ValueError as error:
            raise StrategySearchError(f'frozen base scenario is not JSON: {error}') from error
        source = str(path.relative_to(WORKSPACE))
        file_hash = _sha256_file(path)
    else:
        _require(isinstance(inline, dict), 'baseScenario must be a JSON object or null')
        encoded = _canonical_json(inline).encode('utf-8')
        _require(len(encoded) <= MAX_BASE_SCENARIO_BYTES,
                 f'baseScenario exceeds {MAX_BASE_SCENARIO_BYTES} bytes')
        scenario, source = deepcopy(inline), 'inline baseScenario'
        file_hash = None
    return scenario, source, _sha256_text(_canonical_json(scenario)), file_hash


def _validate_base_scenario(scenario):
    _require(isinstance(scenario, dict), 'baseScenario must be a JSON object')
    _require(scenario.get('schema') == SCENARIO_SCHEMA,
             f'baseScenario schema must be {SCENARIO_SCHEMA}')
    try:
        load_scenario(scenario)
    except ScenarioError as error:
        raise StrategySearchError(f'baseScenario is not a legal scenario: {error}') from error
    units = scenario.get('ownUnits') or []
    _require(1 <= len(units) <= MAX_OWN_UNITS,
             f'baseScenario must carry between 1 and {MAX_OWN_UNITS} own units')


def _normalize_request(request):
    """Validate and normalize; unknown keys are reported, never silently applied."""
    _require(isinstance(request, dict), 'the search request must be a JSON object')
    _require(request.get('schema') == REQUEST_SCHEMA, f'schema must be {REQUEST_SCHEMA}')
    known = {'schema', 'baseScenario', 'candidateCount', 'searchSeed', 'seeds', 'tickLimit',
             'objective', 'searchAxis', 'baseScenarioSource', 'baseScenarioSha256', 'notes'}
    ignored = sorted(set(request) - known)

    objective = request.get('objective', DEFAULT_OBJECTIVE)
    _require(objective in OBJECTIVES,
             f'objective must be one of {", ".join(OBJECTIVES)} (got {objective!r})')
    search_axis = request.get('searchAxis', DEFAULT_SEARCH_AXIS)
    _require(search_axis in SEARCH_AXIS_CHOICES,
             f'searchAxis must be one of {", ".join(SEARCH_AXIS_CHOICES)} (got {search_axis!r})')
    candidate_count = _bounded_int(request.get('candidateCount'), 'candidateCount', 1,
                                   MAX_CANDIDATE_COUNT, DEFAULT_CANDIDATE_COUNT)
    search_seed = _bounded_int(request.get('searchSeed'), 'searchSeed', 0, 2 ** 32 - 1,
                               DEFAULT_SEARCH_SEED)
    tick_limit = _bounded_int(request.get('tickLimit'), 'tickLimit', 1, MAX_TICK_LIMIT,
                              DEFAULT_TICK_LIMIT)

    raw_seeds = request.get('seeds')
    if raw_seeds is None:
        raw_seeds = [dict(seed) for seed in DEFAULT_SEEDS]
    _require(isinstance(raw_seeds, list) and raw_seeds, 'seeds must be a non-empty list')
    _require(len(raw_seeds) <= MAX_SEEDS, f'seeds exceeds the {MAX_SEEDS} seed cap')
    seeds, seen = [], set()
    for entry in raw_seeds:
        _require(isinstance(entry, dict), 'each seed must be an object with mathSeed and libSeed')
        math_seed = _bounded_int(entry.get('mathSeed'), 'seeds[].mathSeed', 0, 2 ** 32 - 1)
        lib_seed = _bounded_int(entry.get('libSeed'), 'seeds[].libSeed', 0, 2 ** 32 - 1)
        payload = (math_seed, lib_seed)
        if payload in seen:
            continue
        seen.add(payload)
        seeds.append(dict(mathSeed=math_seed, libSeed=lib_seed))

    base, source, base_hash, file_hash = _resolve_base_scenario(request)
    _validate_base_scenario(base)
    normalized = dict(schema=REQUEST_SCHEMA, candidateCount=candidate_count,
                      searchSeed=search_seed, seeds=seeds, tickLimit=tick_limit,
                      objective=objective, searchAxis=search_axis, baseScenarioSource=source,
                      baseScenarioSha256=base_hash,
                      baseScenarioIncludedInline=request.get('baseScenario') is not None,
                      baseScenarioFileSha256=file_hash,
                      ignoredRequestKeys=ignored, maxCandidateCount=MAX_CANDIDATE_COUNT,
                      maxSeeds=MAX_SEEDS, maxTickLimit=MAX_TICK_LIMIT,
                      maxPlannedRuns=MAX_PLANNED_RUNS)
    return normalized, base


def _arrangement_space_size(units, placement_keys):
    """How many content-distinct orderings the native formation sort can distinguish."""
    groups = {}
    for index, unit in enumerate(units):
        if combat_search.is_owner_bound(unit):
            continue
        key = placement_keys.get(unit['name'])
        if key is None:
            continue
        groups.setdefault(key, []).append(index)
    total = 1
    for indices in groups.values():
        if len(indices) < 2:
            continue
        counts = Counter(combat_search.unit_content_signature(units[index]) for index in indices)
        total *= math.factorial(len(indices)) // math.prod(math.factorial(v) for v in counts.values())
    return total


def formation_axis(base):
    """Legal ownUnits-order axis: canonical arrangements, re-verified with the real validator."""
    units = list(base['ownUnits'])
    placement_keys = combat_search.native_placement_keys(base)
    space = _arrangement_space_size(units, placement_keys)
    _require(space <= MAX_ENUMERATED_ARRANGEMENTS,
             f'the native formation tie groups of this base scenario allow {space} distinct '
             f'orderings, above the {MAX_ENUMERATED_ARRANGEMENTS} enumeration cap')
    raw = combat_search.formation_arrangements(units, placement_keys)
    arrangements = [dict(entry, axis='formation') for entry in raw
                    if not entry.get('identity')]
    classes = raw[0]['classes'] if raw else []
    reason = None
    if not arrangements:
        reason = ('no ownUnits permutation can change the native placement: every tie on '
                  '(priority, effectiveDefense) holds content-identical units, so the formation '
                  'axis is degenerate for this base scenario')
    return dict(space=space, arrangements=arrangements, classes=classes, reason=reason,
                nativePlacementKeys=[[list(key), [name for name, value in placement_keys.items()
                                                  if value == key]]
                                     for key in sorted(set(placement_keys.values()))])


def skill_axis(base):
    """Legal skill activation-order axis, using the canonical `combat_search.skill_arrangements`.

    That recovered helper permutes one unit's `skills` and `invocationLevels` jointly (each skillId
    stays paired with its own invocation level) and emits only orderings that flip two skills which
    can reach the same native candidate pass. Owner-bound pets keep their owner's order and are never
    permuted. Non-human own units (monsters) are skipped entirely: the native additional-slot
    legality and the fixed first slot of a monster are not recovered, so their skill list is never
    reordered even when they are not owner-bound. Each eligible unit's raw permutation upper bound
    `len(skills)!` is checked against `MAX_UNIT_SKILL_PERMUTATIONS` before
    `combat_search.skill_arrangements` is called, so an oversized skill array fails fast instead of
    enumerating a factorial. Skill ownership, equipment, roster and every stat are untouched.
    """
    try:
        rows = combat_search.native_skill_rows()
    except Exception as error:  # noqa: BLE001 - unavailable skill rows make this axis degenerate
        return dict(space=0, arrangements=[], reason=('the recovered skill profiles are unavailable '
                   f'({type(error).__name__}: {error}), so the skill activation-order axis cannot be '
                   'generated'), units=[], skippedNonhumanUnits=[], nonhumanSkipReason=None)
    entries, space, skipped = [], 1, []
    for unit in base['ownUnits']:
        if combat_search.is_owner_bound(unit):
            continue
        if not bool(unit.get('human')):
            skipped.append(unit['name'])
            continue
        slots = len(unit['skills'])
        bound = math.factorial(slots)
        _require(bound <= MAX_UNIT_SKILL_PERMUTATIONS,
                 f'own unit {unit["name"]!r} carries {slots} skill slots, whose {bound} raw '
                 f'permutations exceed the {MAX_UNIT_SKILL_PERMUTATIONS} per-unit enumeration cap; '
                 'the skill activation-order axis refuses to enumerate it')
        orderings = combat_search.skill_arrangements(unit, rows)
        if not orderings:
            continue
        entries.append((unit['name'], orderings))
        space *= len(orderings)
    if entries:
        _require(space <= MAX_ENUMERATED_ARRANGEMENTS,
                 f'the order-relevant skill permutations of this base scenario allow {space} distinct '
                 f'activation orderings, above the {MAX_ENUMERATED_ARRANGEMENTS} enumeration cap')
    arrangements = []
    for combo in (itertools.product(*[orderings for _, orderings in entries]) if entries else ()):
        changes = {}
        for (name, _), arrangement in zip(entries, combo):
            changes[name] = dict(skills=list(arrangement['skills']),
                                 invocationLevels=list(arrangement['invocationLevels']))
        arrangements.append(dict(axis='skill-order', changes=changes))
    reason = None
    if not arrangements:
        reason = ('no own unit has two order-relevant skills that can reach the same native pass, so '
                  'the skill activation-order axis is degenerate for this base scenario')
    return dict(space=space if entries else 1, arrangements=arrangements, reason=reason,
                units=[dict(unit=name, orderRelevantArrangements=len(orderings))
                       for name, orderings in entries],
                skippedNonhumanUnits=skipped,
                nonhumanSkipReason=('monster own units are skipped: the native additional-slot '
                                    'legality and the fixed first slot are not recovered, so their '
                                    'skill activation order is never permuted' if skipped else None))


def build_axes(base, search_axis):
    """The enabled axis reports: `formation`, `skill-order`, or both, in a stable order."""
    axes = {}
    if search_axis in ('both', 'formation'):
        axes['formation'] = formation_axis(base)
    if search_axis in ('both', 'skill-order'):
        axes['skill-order'] = skill_axis(base)
    return axes


def _placement_signature(scenario):
    """(name, grid, cell) per own fighter after the canonical native placement."""
    prepared = prepare_setup(deepcopy(scenario))
    rows = sorted(prepared['ownUnits'], key=lambda row: row['grid'])
    return tuple((row['name'], row['grid'], tuple(row['cell'])) for row in rows)


def _candidate_scenario(base, units):
    """The base scenario with only the ownUnits list replaced (nothing else is touched)."""
    scenario = deepcopy(base)
    scenario['ownUnits'] = deepcopy(units)
    _require(set(scenario) == set(base), 'candidate changed the scenario key set')
    for key in base:
        if key == 'ownUnits':
            continue
        _require(_canonical_json(scenario[key]) == _canonical_json(base[key]),
                 f'candidate changed the base scenario field {key!r}')
    return scenario


def _candidate_units(base, arrangement):
    """The base ownUnits with only this axis proposal applied; every other field is untouched."""
    units = deepcopy(base['ownUnits'])
    if arrangement['axis'] == 'formation':
        original = base['ownUnits']
        ordered = list(units)
        for position, source in arrangement['assignment'].items():
            ordered[position] = deepcopy(original[source])
        return ordered
    by_name = {unit['name']: unit for unit in units}
    for name, change in arrangement['changes'].items():
        _require(name in by_name, f'skill-order proposal names unknown unit {name!r}')
        by_name[name]['skills'] = list(change['skills'])
        by_name[name]['invocationLevels'] = list(change['invocationLevels'])
    return units


def _roster_signature(units):
    """Per-unit identity that ignores only the order of a unit's own skills.

    Everything else (name, stats, parameters, equipment, weapon) and the per-unit multiset of
    (skillId, invocationLevel) pairs must be byte-identical, so no axis can change what a unit owns.
    """
    rows = []
    for unit in units:
        fixed = {key: value for key, value in unit.items()
                 if key not in ('name', 'skills', 'invocationLevels')}
        pairs = tuple(sorted(zip(unit['skills'], unit['invocationLevels'])))
        rows.append((unit['name'], _canonical_json(fixed), pairs))
    return sorted(rows)


def _select_candidates(base, axes, candidate_count, search_seed):
    """Deterministic bounded sample over the enabled axes, deduplicated by full candidate identity."""
    roster = _roster_signature(base['ownUnits'])
    sampled = [proposal for name in SEARCH_AXIS_CHOICES
               for proposal in (axes[name]['arrangements'] if name in axes else [])]
    random.Random(search_seed).shuffle(sampled)
    candidates, seen, rejected = [], {_canonical_json(base['ownUnits'])}, []
    for arrangement in sampled:
        if len(candidates) >= candidate_count:
            break
        ordered = _candidate_units(base, arrangement)
        _require(_roster_signature(ordered) == roster,
                 'generated arrangement changed the roster, a unit stat or a unit skill/level '
                 'multiset')
        scenario = _candidate_scenario(base, ordered)
        try:
            placement = _placement_signature(scenario)
        except Exception as error:  # noqa: BLE001 - a candidate that cannot be prepared is rejected
            rejected.append(dict(axis=arrangement['axis'], proposal=arrangement,
                                 reason=f'{type(error).__name__}: {error}'))
            continue
        signature = _canonical_json(scenario['ownUnits'])
        if signature in seen:
            rejected.append(dict(axis=arrangement['axis'], proposal=arrangement,
                                 reason='identical ownUnits to the base scenario or an already '
                                        'selected candidate'))
            continue
        seen.add(signature)
        candidates.append(dict(candidateId=f'cand{len(candidates) + 1:02d}',
                               axis=arrangement['axis'], arrangement=arrangement,
                               scenario=scenario, scenarioSha256=scenario_sha256(scenario),
                               placement=[[name, grid, list(cell)] for name, grid, cell in placement]))
    return candidates, rejected

def _unit_state(units, identity):
    entry = units.get(identity)
    if entry is None:
        entry = units.get(str(identity))
    return entry or {}


def _run_metrics(report, objective, elapsed):
    """Explicit, labelled metrics from one real simulator run."""
    result = report['result']
    trace = report.get('trace') or []
    if objective == 'generated-boxes' and 'prizeCallbacks' not in result:
        raise StrategySearchError(
            'objective generated-boxes is unavailable: the simulator report does not expose the '
            f'{PRE_SETTLEMENT_BOX_METRIC} source (result["prizeCallbacks"])')
    own_names = [row['name'] for row in report['setup']['ownUnits']]
    names = result.get('names') or {}
    units = result.get('units') or {}
    own_ids = [names[name] for name in own_names if name in names]
    enemy_ids = [identity for name, identity in names.items() if name.startswith('enemy:')]
    own_states = [_unit_state(units, identity).get('state') for identity in own_ids]
    enemy_states = [_unit_state(units, identity).get('state') for identity in enemy_ids]
    prize_ticks = [event['tick'] for event in trace if event.get('kind') == 'prize']
    verdict_tick = result.get('verdictTick')
    pre_verdict = (None if verdict_tick is None
                   else sum(1 for tick in prize_ticks if tick <= verdict_tick))
    finish = report.get('finish') or {}
    entitlement = result.get('rewardEntitlement') or {}
    counters = report.get('metrics') or {}
    verdict = result.get('verdict')
    return {
        'verdict': verdict,
        'verdictLabel': VERDICT_LABELS.get(verdict),
        'censored': bool(result.get('censored')),
        'ticks': result.get('ticks'),
        'battleFrame': result.get('battleFrame'),
        'verdictTick': verdict_tick,
        'stopReason': result.get('stopReason'),
        'ownSurvivors': sum(1 for state in own_states if state != 8),
        'ownLeaving': sum(1 for state in own_states if state == 8),
        'enemySurvivors': sum(1 for state in enemy_states if state != 8),
        'prizeCallbacks': result.get('prizeCallbacks'),
        'prizeTraceEvents': len(prize_ticks),
        PRE_SETTLEMENT_BOX_METRIC: pre_verdict,
        'queuedChestAwards': (finish.get('queuedChestAwards') or {}).get('count'),
        'dispatchedChestAwards': (finish.get('dispatchedChestAwards') or {}).get('count'),
        'yieldTrusted': result.get('yieldTrusted'),
        'autoFinishProducerProven': result.get('autoFinishProducerProven'),
        'rewardEntitlementAwarded': entitlement.get('awarded'),
        'rewardEntitlementBasis': entitlement.get('basis'),
        'attackAttempts': counters.get('attackAttempts'),
        'heals': counters.get('heals'),
        'commands': counters.get('commands'),
        'failedReleases': counters.get('failedReleases'),
        'elapsedSeconds': round(elapsed, 3),
    }


def _run_row(scenario, seed, tick_limit, objective):
    """One real simulator run; the trace is discarded before the next run starts."""
    started = time.monotonic()
    try:
        report = run_scenario(dict(scenario, mathSeed=seed['mathSeed'], libSeed=seed['libSeed'],
                                   tickLimit=tick_limit), True)
        metrics = _run_metrics(report, objective, time.monotonic() - started)
        del report
        row = dict(mathSeed=seed['mathSeed'], libSeed=seed['libSeed'], tickLimit=tick_limit,
                   error=None, **metrics)
        return row
    except StrategySearchError:
        raise
    except Exception as error:  # noqa: BLE001 - one bad candidate must not kill the search
        return dict(mathSeed=seed['mathSeed'], libSeed=seed['libSeed'], tickLimit=tick_limit,
                    error=f'{type(error).__name__}: {error}', censored=None, verdict=None)


def _completed(row):
    """A run counts only when it finished with a real verdict (no error, not censored)."""
    return not row.get('error') and not row.get('censored')


def _seed_key(row):
    return (row['mathSeed'], row['libSeed'])


def _score(objective, rows):
    """(score, completed rows, unranked reason) for one entry; censored runs never score."""
    completed = [row for row in rows if _completed(row)]
    if not completed:
        return None, completed, 'no run of this entry completed (every run was censored or errored)'
    if objective == 'battle-outcome':
        primary = sum(1 for row in completed if row.get('verdict') == 1)
        secondary = sum(row.get('ownSurvivors') or 0 for row in completed)
    else:
        primary = sum(row.get(PRE_SETTLEMENT_BOX_METRIC) or 0 for row in completed)
        secondary = sum(1 for row in completed if row.get('verdict') == 1)
    ticks = sum(row.get('ticks') or 0 for row in completed)
    return [primary, secondary, -ticks], completed, None


def _summary(entry_id, base, units_scenario, rows, baseline_rows, objective, tick_limit, kind,
             full_seed_keys, axis):
    """One baseline/candidate summary with paired, same-horizon scoring and a comparability verdict.

    `paired` is the intersection of seed pairs this entry and the baseline both completed. The
    entry's own score and the baseline score recomputed on that identical subset are reported, so a
    candidate that censors on a seed can never win the `-ticks` tiebreak against a baseline that
    finished it. `comparable` is true only when that intersection is the full requested seed bank.
    """
    paired_keys = ({_seed_key(row) for row in rows if _completed(row)}
                   & {_seed_key(row) for row in baseline_rows if _completed(row)})
    paired = [row for row in rows if _completed(row) and _seed_key(row) in paired_keys]
    paired_baseline = [row for row in baseline_rows if _completed(row)
                       and _seed_key(row) in paired_keys]
    score, completed_rows, reason = _score(objective, paired)
    paired_baseline_score, _, _ = _score(objective, paired_baseline)
    bank_complete = paired_keys == set(full_seed_keys)
    comparable = bool(bank_complete and score is not None and paired_baseline_score is not None)
    seed = paired[0] if paired else (rows[0] if rows else dict(mathSeed=7, libSeed=8))
    emitted = _candidate_scenario(base, units_scenario['ownUnits'])
    emitted.update(mathSeed=seed['mathSeed'], libSeed=seed['libSeed'], tickLimit=tick_limit)
    metrics = {
        'seedsRequested': len(rows),
        'runsCompleted': len(completed_rows),
        'runsCensored': sum(1 for row in rows if row.get('censored')),
        'runsErrored': sum(1 for row in rows if row.get('error')),
        'pairedCompletedSeedPairs': len(paired),
        'wins': sum(1 for row in completed_rows if row.get('verdict') == 1),
        'losses': sum(1 for row in completed_rows if row.get('verdict') == 2),
        'ownSurvivors': sum(row.get('ownSurvivors') or 0 for row in completed_rows),
        'prizeCallbacks': sum(row.get('prizeCallbacks') or 0 for row in completed_rows),
        'preVerdictPrizeCallbacks': sum(row.get(PRE_SETTLEMENT_BOX_METRIC) or 0
                                        for row in completed_rows),
        'queuedChestAwards': sum(row.get('queuedChestAwards') or 0 for row in completed_rows),
        'totalTicks': sum(row.get('ticks') or 0 for row in completed_rows),
        'yieldTrusted': any(row.get('yieldTrusted') for row in completed_rows),
    }
    return dict(candidateId=entry_id, kind=kind, axis=axis, completed=bool(paired),
                comparable=comparable,
                fullSeedBankComplete=bank_complete, score=score,
                pairedBaselineScore=paired_baseline_score,
                scoreDefinition=SCORE_DEFINITIONS[objective], unrankedReason=reason,
                provisional=objective == 'generated-boxes', scenario=emitted,
                scenarioSha256=scenario_sha256(emitted), metrics=metrics, evaluations=rows,
                seedsUsed=[dict(mathSeed=row['mathSeed'], libSeed=row['libSeed'])
                           for row in paired])


def _decide(summaries, baseline, cancelled_flag):
    """beatsBaseline flags, ranking, improvement and best under the comparability rule.

    Only entries that completed the full seed bank (and therefore share one identical scored seed
    set with the baseline) are ranked, so a partial or censored candidate is reported honestly but
    can never be `best` or an improvement.
    """
    for entry in summaries:
        entry['beatsBaseline'] = bool(
            not cancelled_flag and entry.get('comparable') and entry['score'] is not None
            and entry.get('pairedBaselineScore') is not None
            and entry['score'] > entry['pairedBaselineScore'])
    ranked = sorted([entry for entry in summaries
                     if entry.get('comparable') and entry['score'] is not None],
                    key=lambda entry: (-entry['score'][0], -entry['score'][1], -entry['score'][2],
                                       entry['candidateId']))
    improved = bool(not cancelled_flag and ranked and ranked[0]['kind'] == 'generated'
                    and ranked[0]['score'] > ranked[0]['pairedBaselineScore'])
    best = dict(ranked[0], isBaseline=False) if improved else dict(baseline, isBaseline=True)
    return dict(ranked=ranked, improved=improved, best=best)


AXIS_DESCRIPTIONS = {
    'formation': 'legal ownUnits order inside native formation tie groups',
    'skill-order': ('legal activation priority order of each human own unit own order-relevant '
                    'skills (canonical combat_search.skill_arrangements; every skill keeps its own '
                    'invocation level; monster own units are skipped because their additional-slot '
                    'legality and fixed first slot are not recovered)'),
}


def _axis_report(name, axis):
    """One searchSpace axis entry; the non-human skip disclosure appears only when it applies, so a
    base scenario without monsters keeps exactly its previous report shape."""
    report = dict(axis=name, distinctArrangements=axis['space'],
                  arrangements=len(axis['arrangements']),
                  degenerateReason=axis['reason'],
                  units=axis.get('units', []),
                  classes=axis.get('classes', []),
                  nativePlacementKeys=axis.get('nativePlacementKeys', []))
    if axis.get('skippedNonhumanUnits'):
        report['skippedNonhumanUnits'] = list(axis['skippedNonhumanUnits'])
        report['nonhumanSkipReason'] = axis.get('nonhumanSkipReason')
    return report


def _limitations(normalized, axes, rejected, baseline, hashes):
    objective = normalized['objective']
    enabled = list(axes)
    limits = [
        'searched axes: ' + '; '.join(AXIS_DESCRIPTIONS[name] for name in enabled)
        + '. Every other input (skill ownership, equipment, levels, items, resources, encounter) is '
          'held fixed',
        'bounded sample of at most %d candidates on %d seed pair(s); no global optimum is claimed'
        % (normalized['candidateCount'], len(normalized['seeds'])),
        'a candidate counts as best/improvement only when it and the baseline both completed '
        '(uncensored) on the full requested seed bank; a censored or errored candidate is reported '
        'with its honest partial score but is never ranked',
        'generated candidates have no native comparison: only the frozen UREF formation with seeds '
        '7/8 was checked against native and only through the Ending transition '
        '(RE-evidence/20260920-runtime-oracle-wairo-sentinel/RESULT.md), not its Finish, reward '
        'settlement or visuals',
        'Finish policies are diagnostic cuts, so no settled or owned yield is ranked; yieldTrusted '
        'stays false while the automatic Ending->Finish producer is unproven',
        'censored runs (tick horizon reached before IsAnnihilated) are labelled, excluded from the '
        'score, and never read as a completed reward',
    ]
    if objective == 'generated-boxes':
        limits.insert(1, 'objective generated-boxes is PROVISIONAL: it scores only the named real '
                         'pre-settlement metric %s and never claims dispatched, queued or owned '
                         'boxes' % PRE_SETTLEMENT_BOX_METRIC)
    if not hashes['coreModelMatches']:
        limits.insert(0, 'the canonical core model %s does not match the expected SHA256 %s; the '
                         'results below were produced by the file on disk'
                         % (CORE_MODEL_FILE, CORE_MODEL_SHA256))
    for name in enabled:
        if axes[name]['reason']:
            limits.append('%s axis: %s' % (name, axes[name]['reason']))
        skipped_nonhuman = axes[name].get('skippedNonhumanUnits') or []
        if skipped_nonhuman:
            limits.append('%s axis skipped %d non-human own unit(s) (%s): the native monster '
                          'additional-slot legality and fixed first slot are not recovered, so '
                          'their skill activation order was neither permuted nor claimed legal'
                          % (name, len(skipped_nonhuman), ', '.join(skipped_nonhuman)))
    if rejected:
        limits.append('%d sampled arrangement(s) were rejected by the real legality/placement '
                      're-check' % len(rejected))
    if baseline is not None and not baseline.get('comparable'):
        limits.append('the baseline did not complete (uncensored) on the full requested seed bank, '
                      'so no candidate is comparable: the result keeps the baseline as best and '
                      'claims no improvement')
    return limits

def run_search(request, on_progress=None, cancelled=None):
    """Run the bounded search. `on_progress(dict)` is called between runs; `cancelled()` -> bool."""
    started = time.monotonic()
    emit = on_progress or (lambda event: None)
    cancel = cancelled or (lambda: False)
    normalized, base = _normalize_request(request)
    objective = normalized['objective']
    seeds, tick_limit = normalized['seeds'], normalized['tickLimit']
    hashes = simulator_hashes()
    axes = build_axes(base, normalized['searchAxis'])
    candidates, rejected = _select_candidates(base, axes, normalized['candidateCount'],
                                              normalized['searchSeed'])
    total_runs = (1 + len(candidates)) * len(seeds)
    _require(total_runs <= MAX_PLANNED_RUNS,
             f'planned runs {total_runs} exceed the {MAX_PLANNED_RUNS} run cap; lower candidateCount '
             f'or the seed count')
    full_seed_keys = [_seed_key(seed) for seed in seeds]
    state = dict(completed=0, total=total_runs, bestScore=None, bestCandidateId=None,
                 status='running')

    def progress(phase, candidate_id=None, seed=None):
        emit(dict(phase=phase, completed=state['completed'], total=state['total'],
                  candidateId=candidate_id, bestScore=state['bestScore'],
                  bestCandidateId=state['bestCandidateId'],
                  mathSeed=(seed or {}).get('mathSeed'), libSeed=(seed or {}).get('libSeed'),
                  elapsedSeconds=round(time.monotonic() - started, 3), status=state['status']))

    progress('search-start')
    cancelled_flag = False
    baseline_rows = []
    for seed in seeds:
        if cancel():
            cancelled_flag = True
            break
        progress('run-start', 'baseline', seed)
        baseline_rows.append(_run_row(base, seed, tick_limit, objective))
        state['completed'] += 1
        progress('run-complete', 'baseline', seed)

    baseline_complete = ({_seed_key(row) for row in baseline_rows if _completed(row)}
                         == set(full_seed_keys))
    candidate_rows = {candidate['candidateId']: [] for candidate in candidates}
    best_score = _score(objective, baseline_rows)[0] if baseline_complete else None
    if best_score is not None:
        state['bestScore'] = best_score
    for candidate in candidates:
        if cancelled_flag:
            break
        rows = candidate_rows[candidate['candidateId']]
        for seed in seeds:
            if cancel():
                cancelled_flag = True
                break
            progress('run-start', candidate['candidateId'], seed)
            rows.append(_run_row(candidate['scenario'], seed, tick_limit, objective))
            state['completed'] += 1
            progress('run-complete', candidate['candidateId'], seed)
        # Progress only ever reports a candidate that finished the whole requested seed bank, so it
        # can never advertise a partial/censored candidate as the running best.
        if (not cancelled_flag and baseline_complete
                and {_seed_key(row) for row in rows if _completed(row)} == set(full_seed_keys)):
            score = _score(objective, rows)[0]
            if score is not None and (best_score is None or score > best_score):
                best_score = score
                state['bestScore'], state['bestCandidateId'] = score, candidate['candidateId']

    baseline = _summary('baseline', base, base, baseline_rows, baseline_rows, objective, tick_limit,
                        'baseline', full_seed_keys, 'baseline')
    summaries = [baseline] + [
        _summary(candidate['candidateId'], base, candidate['scenario'],
                 candidate_rows[candidate['candidateId']], baseline_rows, objective, tick_limit,
                 'generated', full_seed_keys, candidate['axis'])
        for candidate in candidates]
    state['status'] = 'cancelled' if cancelled_flag else 'completed'
    progress('search-complete')

    decision = _decide(summaries, baseline, cancelled_flag)
    ranked, improved, best = decision['ranked'], decision['improved'], decision['best']

    limitations = _limitations(normalized, axes, rejected, baseline, hashes)
    if cancelled_flag:
        limitations.insert(0, 'the search was cancelled before every planned run finished; partial '
                              'results are reported and no improvement is claimed')
    degenerate = [name for name in axes if axes[name]['reason']]
    if degenerate:
        improvement_definition = IMPROVEMENT_DEFINITION + '; ' + '; '.join(
            '%s axis: %s' % (name, axes[name]['reason']) for name in degenerate)
    else:
        improvement_definition = IMPROVEMENT_DEFINITION
    available = sum(len(axes[name]['arrangements']) for name in axes)
    degenerate_reason = None
    if available == 0:
        degenerate_reason = ('; '.join('%s axis: %s' % (name, axes[name]['reason'])
                                       for name in axes if axes[name]['reason'])
                             or 'no enabled axis can generate a distinct candidate for this base '
                                'scenario')
    return dict(
        schema=RESULT_SCHEMA,
        status=state['status'],
        objective=objective,
        scoreDefinition=SCORE_DEFINITIONS[objective],
        improvementDefinition=improvement_definition,
        improvement=improved,
        request=normalized,
        baseScenario=dict(source=normalized['baseScenarioSource'],
                          sha256=normalized['baseScenarioSha256'],
                          fileSha256=normalized['baseScenarioFileSha256'],
                          scenarioIncludedInline=normalized['baseScenarioIncludedInline']),
        simulator=hashes,
        searchSpace=dict(searchAxis=normalized['searchAxis'],
                         axis=' + '.join(axes),
                         axes=[_axis_report(name, axes[name]) for name in axes],
                         distinctArrangements=available,
                         degenerateReason=degenerate_reason,
                         candidateCountRequested=normalized['candidateCount'],
                         candidateCountGenerated=len(candidates),
                         rejectedArrangements=rejected,
                         deduplicatedBy='full ownUnits identity (order and per-unit skill order)',
                         plannedRuns=total_runs, completedRuns=state['completed'],
                         fullSeedBank=[dict(mathSeed=math_seed, libSeed=lib_seed)
                                       for math_seed, lib_seed in full_seed_keys],
                         baselineCompletedFullSeedBank=baseline_complete,
                         rankedComparableCandidates=[entry['candidateId'] for entry in ranked],
                         bestCandidateIdByScore=ranked[0]['candidateId'] if ranked else None),
        baseline=baseline,
        candidates=summaries[1:],
        best=best,
        improvements=[entry['candidateId'] for entry in summaries if entry['beatsBaseline']],
        metricLabels=METRIC_LABELS,
        preSettlementBoxMetric=dict(name=PRE_SETTLEMENT_BOX_METRIC,
                                    source=PRE_SETTLEMENT_BOX_METRIC_SOURCE,
                                    provisional=objective == 'generated-boxes'),
        replay=dict(scenarioSchema=SCENARIO_SCHEMA, runner='combat_sandbox.run_scenario',
                    api='POST /api/battle-run with the scenario object as the request envelope',
                    reproduces='the first paired seed pair of that entry'),
        nativeValidationScope=dict(
            validated='one UREF formation with seeds 7/8 compared with native through the Ending '
                      'transition (Ending frame0 at tick6069)',
            notValidated=['automatic Finish', 'reward or chest settlement', 'visuals',
                          'every weapon and skill combination', 'the generated candidates above'],
            evidence='RE-evidence/20260920-runtime-oracle-wairo-sentinel/RESULT.md'),
        limitations=limitations,
        elapsedSeconds=round(time.monotonic() - started, 3),
    )


def _progress_writer(path):
    def write(event):
        with Path(path).open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(event, sort_keys=True) + '\n')
    return write


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--request', type=Path, help='request JSON file')
    parser.add_argument('--output', type=Path, help='result JSON file')
    parser.add_argument('--progress', type=Path, help='append JSON-lines progress here')
    parser.add_argument('--cancel-file', type=Path,
                        help='the search stops between runs once this file exists')
    parser.add_argument('--write-default', type=Path, help='write default_request() and exit')
    args = parser.parse_args(argv)
    if args.write_default is not None:
        args.write_default.write_text(json.dumps(default_request(), indent=2) + '\n',
                                      encoding='utf-8')
        print(json.dumps(dict(wrote=str(args.write_default), schema=REQUEST_SCHEMA)))
        return 0
    if args.request is None or args.output is None:
        parser.error('--request and --output are required (or use --write-default)')
    try:
        request = json.loads(args.request.read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        print(json.dumps(dict(error=f'{type(error).__name__}: {error}', status='rejected')),
              file=sys.stderr)
        return 2
    on_progress = _progress_writer(args.progress) if args.progress is not None else None
    cancel_file = args.cancel_file
    cancelled = (lambda: cancel_file.is_file()) if cancel_file is not None else None
    try:
        result = run_search(request, on_progress=on_progress, cancelled=cancelled)
    except StrategySearchError as error:
        print(json.dumps(dict(error=str(error), status='rejected')), file=sys.stderr)
        return 2
    args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(dict(status=result['status'], schema=result['schema'],
                          objective=result['objective'], improvement=result['improvement'],
                          bestCandidateId=result['best']['candidateId'],
                          baselineScore=result['baseline']['score'],
                          bestScore=result['best']['score'],
                          candidates=len(result['candidates']),
                          completedRuns=result['searchSpace']['completedRuns'],
                          plannedRuns=result['searchSpace']['plannedRuns'],
                          output=str(args.output))))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
