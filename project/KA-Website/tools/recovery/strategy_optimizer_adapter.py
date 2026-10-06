"""Bounded, deterministic adapter for a persistent strategy optimiser.

This module is the only surface a long-running optimiser needs. It wraps the canonical research
pipeline (`strategy_search`, `combat_scenario`/`combat_setup`, `combat_sandbox`,
`combat_replay_export`) and never reimplements, approximates or scores combat itself:

  * `default_scenario` / `validate_scenario` produce a legal, normalized scenario under hard caps
    (32 units, tickLimit <= 10000, <= 256 KiB of JSON).
  * `propose` asks the existing bounded generators for exactly one legal move: a formation
    re-ordering inside one native placement tie, or a human unit's skill activation order. No stat,
    skill, level, piece of equipment or party member is ever invented.
  * `simulate` runs `combat_sandbox.run_scenario(..., include_trace=False)` and returns a compact
    metric dict; `trace=True` additionally returns the canonical
    `combat_replay_export.export_replay` payload for the identical scenario/seeds and verifies that
    the two agree.
  * `stats` exposes the effective per-fighter parameters from `combat_setup.prepare_setup`.
  * `provenance` digests every canonical `combat*.py`, `strategy_search.py`, this adapter and the
    actual runtime data files, cached once per process.

ENGINE STATE DISCLOSURE: `combat_sandbox.run_scenario` builds the full native event trace on every
run, and that trace is part of the engine's own timeline state (a checkpoint shares it). The bulk
path calls it with `include_trace=False`, which means the trace is not *transported* to the caller -
it is still produced and preserved inside the engine. "No trace" here must never be read as "no
trace exists". The additive `preVerdictPrizeCallbacks` sandbox metric exists precisely so a bulk
caller can read the pre-settlement box signal without transporting the whole trace.

This module adds no game facts, no model and no visual workload. It is bounded enumeration over the
caller's own validated inputs.
"""
from __future__ import annotations

import hashlib
import json
import sys
from copy import deepcopy
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import combat_progress
import combat_replay_export
import combat_runtime_data
import combat_sandbox
import combat_setup
import strategy_search
from combat_progress import herb_metrics
from combat_scenario import ScenarioError, load_scenario

MAX_UNITS = 32
#: Hard cap on a declared battle horizon. Raised with the measured horizon evidence: encounter 10
#: "Vs. Aloha Kairobot (Hard)" censored 29.9% of its real attempts at 10,000 ticks and every sampled
#: one of those resolved by 20,000 (longest verdict tick 19,780), so 30,000 keeps 1.5x headroom. A
#: resolved battle stops at its verdict, so the larger cap is paid only by fights that need it.
MAX_TICK_LIMIT = 30000
MAX_SCENARIO_BYTES = 262144
MAX_SEED = 2 ** 32 - 1

# Canonical sources this adapter is bound to. Runtime data is resolved live through
# `combat_runtime_data` so the digest follows the package/workspace the runner actually reads.
ADAPTER_SOURCE = 'strategy_optimizer_adapter.py'
SEARCH_SOURCE = 'strategy_search.py'
COMBAT_GLOB = 'combat*.py'

_PROVENANCE_CACHE = None


class StrategyOptimizerError(ValueError):
    """A scenario, seed or proposal that this bounded adapter refuses to act on."""


def _require(condition, message):
    if not condition:
        raise StrategyOptimizerError(message)


def _resolved_role(row):
    """The unit's declared role, else the community classifier's role, else `None`.

    The ordinary Community scenario authors no role, so the label was `unclassified`. The role is
    resolved here from the unit's own skills through `strategy_students.role` (the same classifier
    the community admission rule uses) and is used ONLY as the telemetry label: it never reaches the
    engine, a combat decision or a digest. `None` (no skills to classify) leaves `unclassified`.
    """
    for key in ('role', 'communityRole', 'community_role'):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    if 'skills' not in row:
        return None
    try:
        import strategy_students
        return strategy_students.role(row)
    except Exception:  # noqa: BLE001
        return None


def _canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def _sha256_bytes(payload):
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _digest(payload):
    """A stable sha256 over one canonical JSON payload."""
    return _sha256_bytes(_canonical_json(payload).encode('utf-8'))


def _seed_value(value, name):
    _require(isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= MAX_SEED,
             f'{name} must be an integer in [0, {MAX_SEED}]')
    return value


def _seed_pair(seed_pair):
    """Normalize the caller seed pair to `(mathSeed, libSeed)`; a mapping is accepted too."""
    if isinstance(seed_pair, dict):
        _require('mathSeed' in seed_pair and 'libSeed' in seed_pair,
                 'seed_pair mapping must carry mathSeed and libSeed')
        return (_seed_value(seed_pair['mathSeed'], 'seed_pair.mathSeed'),
                _seed_value(seed_pair['libSeed'], 'seed_pair.libSeed'))
    _require(isinstance(seed_pair, (list, tuple)) and len(seed_pair) == 2,
             'seed_pair must be a [mathSeed, libSeed] pair')
    return _seed_value(seed_pair[0], 'seed_pair[0]'), _seed_value(seed_pair[1], 'seed_pair[1]')


def validate_scenario(scenario):
    """Validate and normalize one scenario under the adapter's hard caps.

    Order: JSON size, the declared unit/tick caps, then the canonical `load_scenario` validator. The
    caps are re-checked on the normalized scenario because household-pet expansion can add units, so
    the returned (and later simulated) roster is always the one that respects the limit.
    """
    _require(isinstance(scenario, dict), 'scenario must be a JSON object')
    try:
        encoded = _canonical_json(scenario).encode('utf-8')
    except (TypeError, ValueError) as error:
        raise StrategyOptimizerError(f'scenario is not JSON serializable: {error}') from error
    _require(len(encoded) <= MAX_SCENARIO_BYTES,
             f'scenario JSON is {len(encoded)} bytes, above the {MAX_SCENARIO_BYTES} byte cap')
    units = scenario.get('ownUnits')
    _require(isinstance(units, list) and units, 'scenario must carry a non-empty ownUnits list')
    _require(len(units) <= MAX_UNITS, f'scenario carries {len(units)} own units, above the {MAX_UNITS} cap')
    tick_limit = scenario.get('tickLimit')
    _require(isinstance(tick_limit, int) and not isinstance(tick_limit, bool),
             'scenario tickLimit must be an integer')
    _require(tick_limit <= MAX_TICK_LIMIT,
             f'scenario tickLimit {tick_limit} is above the {MAX_TICK_LIMIT} cap')
    try:
        normalized = load_scenario(scenario)
    except ScenarioError as error:
        raise StrategyOptimizerError(f'scenario is not legal: {error}') from error
    _require(len(normalized['ownUnits']) <= MAX_UNITS,
             f'normalized scenario carries {len(normalized["ownUnits"])} own units (with household '
             f'pets) above the {MAX_UNITS} cap')
    _require(normalized['tickLimit'] <= MAX_TICK_LIMIT,
             f'normalized tickLimit {normalized["tickLimit"]} is above the {MAX_TICK_LIMIT} cap')
    return normalized


def default_scenario():
    """A validated, normalized copy of `strategy_search`'s frozen default UREF scenario."""
    path = strategy_search.DEFAULT_BASE_SCENARIO_PATH
    _require(path.is_file(), f'the frozen default scenario is missing at {path}')
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        raise StrategyOptimizerError(f'the frozen default scenario is unreadable: {error}') from error
    # Victory or defeat is terminal: the horizon is a safety ceiling, not a per-battle cost. The
    # frozen file still declares the old at-horizon cut, so the optimiser's own policy is applied
    # here rather than by editing recovered evidence.
    raw['finishPolicy'] = strategy_search.TERMINAL_VERDICT_POLICY
    return validate_scenario(raw)


def propose(scenario, seed):
    """One deterministic, legal search-space mutation of `scenario`.

    The move comes from the hard search-space contract (`search_contract.mutate`), which owns what a
    legal searchable strategy is: the approved skill set, the 9-skill and no-duplicate rules, the
    team-wide 7-Hit cap, trigger settings, the conditional INT dimension, formation skills, weapon
    behaviour groups and placement. Every returned scenario is one small, interpretable change and is
    re-validated before it is handed back; a move that would leave the search-space identity unchanged
    is refused there, so no battle is ever spent on a no-op.

    There is deliberately **no fallback** to the old bounded permutation generator: that generator is
    what produced ~157k runs over one skill set, one trigger configuration, one placement and one
    weapon behaviour group, so a production search must never reach it. If the contract cannot produce
    a legal mutation it raises, the planner records the rejection and spends no simulation - see
    `strategy_optimizer.legacy_proposal`, which keeps the old generator reachable only behind the
    explicit `SEARCH_MODE='legacy'` measurement switch.

    `seed` reproduces the same proposal; a scenario with no legal contract move raises
    `StrategyOptimizerError`.
    """
    base = validate_scenario(scenario)
    search_seed = _seed_value(seed, 'seed')
    import search_contract
    try:
        return validate_scenario(search_contract.mutate(base, search_seed))
    except search_contract.ContractError as error:
        raise StrategyOptimizerError(f'no legal search-space mutation is available: {error}') from error


def _unit_state(units, identity):
    entry = units.get(identity)
    if entry is None:
        entry = units.get(str(identity))
    return entry or {}


def _own_rows(report):
    return (report.get('setup') or {}).get('ownUnits') or []


def _own_survivors(report):
    """Own fighters whose native state is not Leaving (8); None when the runner id map is absent."""
    result = report.get('result') or {}
    names = result.get('names') or {}
    units = result.get('units') or {}
    rows = _own_rows(report)
    if not rows or not names or not units:
        return None
    states = []
    for row in rows:
        identity = names.get(row['name'])
        if identity is None:
            return None
        states.append(_unit_state(units, identity).get('state'))
    if any(state is None for state in states):
        return None
    return sum(1 for state in states if state != 8)


def _own_health_fraction(report):
    """Own-team effective HP at the stop / own-team effective HP maximum; None when unavailable."""
    result = report.get('result') or {}
    names = result.get('names') or {}
    units = result.get('units') or {}
    rows = _own_rows(report)
    if not rows:
        return None
    total_hp, total_max = 0, 0
    for row in rows:
        identity = names.get(row['name'])
        state = _unit_state(units, identity) if identity is not None else None
        hp = state.get('hp') if state else None
        maximum = (row.get('effectiveParameters') or {}).get(10, {}).get('maximum')
        if hp is None or not maximum:
            return None
        total_hp += hp
        total_max += maximum
    return round(total_hp / total_max, 6) if total_max else None


def _compact(report, seeds, telemetry=False, scenario=None):
    """The bounded, deterministic compact metric dict for one real simulator run.

    `telemetry=True` attaches the additive `encounterTelemetry` block AFTER the digest, so the
    digest and every legacy metric are byte-identical whether telemetry is on or off. The block is
    built only from readings the report already publishes; a counter the bounded observer cannot
    produce is `null` with its reason, never a fabricated zero.
    """
    result = report.get('result') or {}
    metrics = report.get('metrics') or {}
    resolved = result.get('verdict') is not None and not result.get('censored')
    uses = report.get('holyHerbUses') or []
    item_uses = report.get('itemUses') or []
    payload = dict(
        verdict=result.get('verdict'),
        censored=bool(result.get('censored')),
        ticks=result.get('ticks'),
        prizeCallbacks=result.get('preVerdictPrizeCallbacks') if resolved else None,
        retained=None,
        survivors=_own_survivors(report),
        # A single count of successful consumable uses (Holy Herb + battle items) so the persistent
        # optimiser can average it and rank fewer-resource runs; a blocked/declined use is not a use.
        resourceUses=sum(1 for row in uses if row.get('used'))
        + sum(1 for row in item_uses if row.get('used')),
        healthFraction=_own_health_fraction(report),
        behavior=dict(heals=metrics.get('heals'), attacks=metrics.get('attackAttempts'),
                      prizes=result.get('prizeCallbacks')),
        seeds=list(seeds),
    )
    payload['digest'] = _digest(payload)
    # Additive reward reading, attached AFTER the digest on purpose: the digest still covers exactly
    # the metrics it covered before, so every digest a saved library already holds stays valid. The
    # reading is taken verbatim from the runner's own `rewardEntitlement` block; nothing is re-derived.
    payload['rewardOutcome'] = _reward_outcome(report)
    # Part B stored-attack progress, attached AFTER the digest for the same reason: the digest keeps
    # covering exactly the metrics it covered before, so every digest a saved library holds stays
    # valid, while the new lane metrics travel beside it. Absent (older rows) reads as None.
    payload['progressMetrics'] = (report.get('result') or {}).get('progressMetrics')
    # MP telemetry for the declared trigger units and the explicit Holy Herb evidence, attached after
    # the digest for the same reason: the digest keeps covering exactly the metrics it covered before,
    # so every digest a saved library already holds stays valid. A scenario with no declared trigger
    # units reports an empty MP block and a zeroed herb block rather than a guessed reading.
    result = report.get('result') or {}
    payload['mpMetrics'] = result.get('mpMetrics') or []
    payload['herbMetrics'] = result.get('herbMetrics') or herb_metrics(0, 0, 0, 0, [])
    if telemetry:
        entitlement = result.get('rewardEntitlement') or {}
        item_uses = report.get('itemUses') or []
        # Stable own identity comes from the engine's own name -> identity map (`result['names']`),
        # not from a page-local mapping. A declared semantic role is carried when the setup row has
        # one; otherwise `encounter_telemetry` labels it `unclassified` rather than guessing.
        names = result.get('names') or {}
        # The role is resolved from the *scenario* rows (which carry `skills`, unlike the engine's
        # normalized setup rows), matched by name; `strategy_students.role` is the authoritative
        # skill-based classifier. The label never reaches the engine, a decision or the digest.
        role_by_name = {row['name']: _resolved_role(row)
                        for row in (scenario or {}).get('ownUnits') or []}
        own = [dict(identity=names.get(row['name']), name=row['name'],
                    role=role_by_name.get(row['name']) or _resolved_role(row))
               for row in _own_rows(report) if names.get(row['name']) is not None]
        payload['encounterTelemetry'] = combat_progress.encounter_telemetry(
            result.get('progressMetrics') or {},
            own_units=own or None,
            own_survivors=payload['survivors'],
            own_present=payload['survivors'] is not None,
            resource_uses=payload['resourceUses'],
            herb=payload['herbMetrics'],
            item_use_count=sum(1 for row in item_uses if row.get('used')),
            verdict=payload['verdict'],
            censored=payload['censored'],
            # `result['finishBoundary']` is the sandbox's declared finish-*policy* descriptor
            # ('diagnostic-truncated' whenever the declared policy is on-verdict), not an observed
            # finish boundary at the shared seam, so it is reported under the separate policy-label
            # key. The observed-boundary slot stays null: the native encounter report publishes no
            # boundary field, and inventing one to match would be a fabricated reading.
            finish_boundary=None,
            finish_boundary_label=result.get('finishBoundary'),
            reward_outcome=payload['rewardOutcome'],
            finish_policy=(scenario or {}).get('finishPolicy'),
            diagnostic_finish=entitlement.get('diagnosticFinish'),
            counters=result.get('encounterCounters'))
    return payload


def _reward_outcome(report):
    """The runner's own reward-entitlement reading, exposed verbatim under one compact key.

    A certified award is an *entitlement*, not a retained chest: `inventoryVerified` is always False
    because automatic Finish and world collection are unproven, so no pending count, callback or
    certificate is ever promoted into a retained-chest count here. `awardedChests` is the runner's
    native value - 0 from the native win/loss gate on a loss, the certified pre-verdict pending count
    on a certified win, and null when unresolved or a win without a certificate. The optional
    `certificate` block is included only when the runner actually holds one.
    """
    entitlement = (report.get('result') or {}).get('rewardEntitlement') or {}
    outcome = dict(
        pendingChests=entitlement.get('pendingChestCount'),
        awardedChests=entitlement.get('awardedChestCount'),
        awardedBasis=entitlement.get('awardedChestCountBasis'),
        inventoryVerified=False,
        reason=entitlement.get('rewardCountReason'),
    )
    certificate = entitlement.get('certificate') or {}
    if certificate.get('holds'):
        outcome['certificate'] = dict(
            certificateId=entitlement.get('certificateId'),
            holds=True,
            frame=certificate.get('frame'),
            issuedBeforeVerdict=bool(certificate.get('issuedBeforeVerdict')),
            timingRule=certificate.get('timingRule'),
        )
    return outcome


def _verify_trace(replay, compact):
    """Compare the replay run against the bulk run on every shared metric; fail closed on a mismatch.

    A disagreement raises instead of being averaged or hidden. When the replay payload exposes
    nothing comparable the check is a no-op ("if possible"): attaching the replay is still safe.
    """
    final = replay.get('finalState') or {}
    metrics = replay.get('metrics') or {}
    checks = [
        ('verdict', final.get('verdict'), compact['verdict']),
        ('censored', final.get('censored'), compact['censored']),
        ('ticks', final.get('ticks'), compact['ticks']),
        ('heals', metrics.get('heals'), compact['behavior']['heals']),
        ('attacks', metrics.get('attackAttempts'), compact['behavior']['attacks']),
        ('prizes', metrics.get('prizeCallbacks'), compact['behavior']['prizes']),
    ]
    if compact['prizeCallbacks'] is not None and final.get('verdictTick') is not None:
        verdict_tick = final['verdictTick']
        checks.append(('preVerdictPrizeCallbacks',
                       sum(1 for event in replay.get('events') or []
                           if event.get('kind') == 'prize' and event.get('tick') <= verdict_tick),
                       compact['prizeCallbacks']))
    comparable = [(name, left, right) for name, left, right in checks if left is not None]
    mismatched = [f'{name}: bulk={right!r} trace={left!r}'
                  for name, left, right in comparable if left != right]
    if mismatched:
        raise StrategyOptimizerError(
            'trace replay disagrees with the bulk run on ' + '; '.join(mismatched))


def simulate(scenario, seed_pair, trace=False, backend=None, telemetry=False, timing=None):
    """One canonical run of `scenario` at `seed_pair`, returned as a compact metric dict.

    Bulk runs call `combat_sandbox.run_scenario(..., include_trace=False)` and never transport the
    trace. With `trace=True` the identical scenario/seeds are also replayed through
    `combat_replay_export.export_replay` and the shared metrics are verified before the replay is
    attached under the extra `replay` key. Combat is never approximated: the digest covers the
    compact metrics only, so a trace run and a bulk run of the same inputs share one digest.

    `timing` is an optional, opt-in collector dict. When given, the native path fills it with the
    Python-preparation, native-invocation and result-conversion durations for THIS call. It never
    changes the returned metrics or the digest.
    """
    base = validate_scenario(scenario)
    math_seed, lib_seed = _seed_pair(seed_pair)
    run_scenario_input = dict(base, mathSeed=math_seed, libSeed=lib_seed)
    if not trace:
        # Bulk path: the native backend when eligible, the canonical Python backend otherwise, with
        # `resultBackend` recorded outside the compact digest. `trace=True` always uses Python
        # because the replay export needs the canonical trace.
        import strategy_optimizer_native as native
        return native.simulate_compact(run_scenario_input, (math_seed, lib_seed),
                                       backend=backend or native.DEFAULT_BACKEND,
                                       telemetry=telemetry, timing=timing)
    report = combat_sandbox.run_scenario(deepcopy(run_scenario_input), include_trace=False)
    compact = _compact(report, (math_seed, lib_seed), telemetry=telemetry, scenario=base)
    compact['resultBackend'] = 'python'
    if trace:
        replay = combat_replay_export.export_replay(deepcopy(run_scenario_input), include_events=True)
        _verify_trace(replay, compact)
        compact['replay'] = replay
    return compact


def _python_simulate_compact(scenario, seed_pair, telemetry=False, timing=None):
    """The canonical Python battle path, returning the compact dict only (never the trace)."""
    from time import perf_counter
    began = perf_counter()
    base = validate_scenario(scenario)
    math_seed, lib_seed = _seed_pair(seed_pair)
    report = combat_sandbox.run_scenario(dict(base, mathSeed=math_seed, libSeed=lib_seed),
                                         include_trace=False)
    compute_ended = perf_counter()
    convert_began = perf_counter()
    compact = _compact(report, (math_seed, lib_seed), telemetry=telemetry, scenario=base)
    if timing is not None:
        # The Python engine has no separately callable native boundary, so the whole validation +
        # simulation span is reported as the compute stage and the native call stays absent (none),
        # never mislabelled as native. `convertBeginAt`/`convertEndAt` still bracket the compact
        # result conversion on the same perf_counter basis the worker reports.
        convert_ended = perf_counter()
        timing['nativePath'] = False
        timing['pythonComputeSeconds'] = compute_ended - began
        timing['convertBeginAt'] = convert_began
        timing['convertEndAt'] = convert_ended
        timing['convertSeconds'] = convert_ended - convert_began
    return compact


def stats(scenario):
    """Effective per-fighter stats from `combat_setup.prepare_setup`, keyed by fighter name.

    Values are the runner's own prepared values (effective parameters, defense, training level,
    weapon and skill MP costs); nothing here is re-derived and the result is JSON serializable.
    """
    normalized = validate_scenario(scenario)
    prepared = combat_setup.prepare_setup(deepcopy(normalized))
    fighters = {}
    for member in prepared['ownUnits']:
        fighters[member['name']] = dict(
            monster=bool(member['monster']),
            weaponId=member['weaponId'],
            weaponRange=member['weaponRange'],
            effectiveDefense=member['effectiveDefense'],
            averageTrainingLevel=member['averageTrainingLevel'],
            parameters={int(pid): dict(value=value['value'], maximum=value['maximum'])
                        for pid, value in member['effectiveParameters'].items()},
            skillCosts=[dict(skillId=row['skillId'], cost=row['cost']) for row in member['skillCosts']],
        )
    return fighters


def _source_names():
    combat = sorted(path.name for path in HERE.glob(COMBAT_GLOB) if path.is_file())
    return combat + [SEARCH_SOURCE, ADAPTER_SOURCE, 'strategy_optimizer.py', 'strategy_optimizer_limits.py']


def provenance(refresh=False):
    """Stable digest of every canonical source and live runtime data file this adapter binds to.

    The digest covers all `combat*.py`, `strategy_search.py`, this adapter and the runtime data /
    table files resolved through `combat_runtime_data` (package or recovery workspace). The result is
    cached for the process: an optimiser that simulates thousands of times hashes these files once.
    Pass `refresh=True` to re-hash deliberately.
    """
    global _PROVENANCE_CACHE
    if _PROVENANCE_CACHE is not None and not refresh:
        return deepcopy(_PROVENANCE_CACHE)
    files, missing = {}, []
    for name in _source_names():
        label = f'tools/recovery/{name}'
        path = HERE / name
        files[label] = _sha256_file(path) if path.is_file() else None
        if files[label] is None:
            missing.append(label)
    for name in combat_runtime_data.RUNTIME_DATA_FILES:
        label = f'runtime-data/{name}'
        try:
            path = combat_runtime_data.data_path(name)
        except FileNotFoundError:
            path = None
        files[label] = _sha256_file(path) if path is not None and path.is_file() else None
        if files[label] is None:
            missing.append(label)
    for name in combat_runtime_data.RUNTIME_TABLES:
        label = f'runtime-table/{name}'
        try:
            path = combat_runtime_data.table_path(name)
        except FileNotFoundError:
            path = None
        files[label] = _sha256_file(path) if path is not None and path.is_file() else None
        if files[label] is None:
            missing.append(label)
    files = dict(sorted(files.items()))
    payload = dict(files=files, count=len(files), missing=sorted(missing),
                   mode=combat_runtime_data.mode())
    payload['digest'] = _digest(payload)
    _PROVENANCE_CACHE = payload
    return deepcopy(payload)
