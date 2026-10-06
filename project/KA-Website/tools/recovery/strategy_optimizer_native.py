"""Native bulk-battle backend for the Strategy Optimiser.

Replaces only the *battle execution* step of the optimiser worker. The external contract is
unchanged: `simulate_compact(scenario, seed_pair, backend)` returns exactly the compact dict
`strategy_optimizer_adapter._compact` produces, with `resultBackend` added **outside** the digest.

Per worker:

    load the cdylib once
    candidate -> build ONE pre-battle native template (seed-independent) and cache it
    seed      -> ka_battle_clone -> reseed -> replay the constructor's deferred Lib draws
                 -> ka_run_battle -> compact fields -> existing `_digest` -> free the clone

The cached template is never run or mutated; the clone is the unit of execution. Every failure path
falls back to the canonical Python simulator with a counted, reason-coded counter.
"""
import collections
import copy
import ctypes
import hashlib
import json
import threading
import time
from pathlib import Path

import ka_abi
import ka_encounter_abi
import combat_progress
from combat_animation_selection import HUMAN_BASES
from combat_initial_state import expand_start_profile
from combat_resolution import add_raw_parameter
from combat_reward_entitlement import RewardEntitlementWatch
from combat_runtime_data import table
from combat_scenario import load_scenario
from combat_setup import prepare_setup
from combat_shared_controllers import ROWS, SharedControllers

MAX_TEMPLATES = 4

#: `ai::KA_ERR_CAPACITY`. The kernel's created-entity arena and subset tables are fixed-size; a battle
#: that would need more than `KA_MAX_OBJECTS` created entities is refused rather than approximated, so
#: it falls back to Python with its own reason code instead of being reported as a generic run error.
KA_ERR_CAPACITY = -4
AUTO_NATIVE_SKILLS = frozenset((4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20,
                               21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35,
                               36, 37, 38, 39, 40, 41, 105, 106, 107, 108, 110, 114, 115,
                               116, 117, 118, 119))
AUTO_NATIVE_MAX_UNITS = 26
ROUTING_VERSION = 'legal49-consumables-v1'

_lock = threading.Lock()
_library = None
_cache = collections.OrderedDict()
_counters = collections.Counter()
_large_module = None

#: Wall-clock seam for the native stage timings. Production always uses the real monotonic clock; a
#: regression can replace this to make the stage intervals exact and prove they do not overlap.
_clock = time.perf_counter


def _large_backend():
    """Load an independent one-template context only when the small arena overflows.

    Both builds use the identical combat source; only object/subset storage capacity differs.
    Ordinary battles retain the smaller allocations and copies. No identities are reclaimed.
    """
    global _large_module
    if _large_module is not None:
        return _large_module
    path = ka_abi.HERE / 'native/ka_kernel/target/release/ka_kernel_v9_large.dll'
    if not path.is_file():
        return None
    import importlib.util
    spec = importlib.util.spec_from_file_location('_ka_native_large_arena', __file__)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module._library = ka_abi.load(path)
    if module._library.object_capacity != 262144:
        raise RuntimeError('Expanded kernel has an unexpected object capacity')
    module.MAX_TEMPLATES = 1
    _large_module = module
    return module

def library():
    global _library
    if _library is None:
        _library = ka_abi.load()
    return _library


#: SHA-256 of each loaded DLL file, keyed by path. A template is only ever cloned by the exact build
#: that created it; this is the concrete identity (file hash + sizeof/version probes) that stops a
#: pointer built by one DLL owner being handed to another.
_library_sha = {}


def _file_sha(path):
    cached = _library_sha.get(path)
    if cached is None:
        cached = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        _library_sha[path] = cached
    return cached


def _library_identity(lib):
    """`(path, sha256, sizeof/version probes)` for the DLL a template was built by.

    The pointer in a template belongs to one shared-library image; cloning it with a different image
    (a v9 vs an encounter v2 DLL, or a small vs a large-arena build) is undefined behaviour. This
    identity is stored on every cache entry and re-checked immediately before each clone.
    """
    name = str(getattr(lib, '_name', '') or '')
    probes = {}
    for probe_name in ('ka_sizeof_report', 'ka_sizeof_encounter_report', 'ka_encounter_version'):
        probe = getattr(lib, probe_name, None)
        if probe is None:
            continue
        probe.restype = ctypes.c_uint32
        probes[probe_name] = int(probe())
    sha = _file_sha(name) if name and Path(name).is_file() else None
    return (name, sha, tuple(sorted(probes.items())))


#: The opt-in, versioned encounter-telemetry kernel. It is loaded only when `telemetry=True` and
#: lives in a separately named DLL (`ka_kernel_encounter_v1.dll`), so the default `ka_kernel_v9.dll`
#: path and every legacy run are untouched. A missing kernel/getter is an explicit error.
_encounter_library = None
_encounter_cache = collections.OrderedDict()


def encounter_library():
    global _encounter_library
    if _encounter_library is None:
        # The expanded-arena worker loads the matching large encounter DLL; the ordinary worker
        # loads the normal one. Both mirror the same source.
        large = library().object_capacity > ka_abi.KA_MAX_OBJECTS
        _encounter_library = ka_encounter_abi.load(
            ka_encounter_abi.encounter_dll_path(large=large))
    return _encounter_library


def encounter_available():
    try:
        encounter_library()
        return True
    except Exception:  # noqa: BLE001
        return False


def _encounter_template(scenario, key):
    entry = _encounter_cache.get(key)
    if entry is not None:
        _encounter_cache.move_to_end(key)
        return entry
    lib = encounter_library()
    entry = _build_template(scenario, key, lib=lib)
    _encounter_cache[key] = entry
    while len(_encounter_cache) > MAX_TEMPLATES:
        _, evicted = _encounter_cache.popitem(last=False)
        lib.ka_battle_free(evicted['handle'])
    return entry


def counters():
    return dict(_counters)


def reset_counters():
    _counters.clear()


def eligibility(scenario):
    """`(supported, reason)` - native execution requires exact compact-result parity."""
    # The consumable subsystem is ported: `inputs`, `itemStock` and `holyHerbStock` are native. Only
    # a declaration the canonical battle path cannot reach (an odd `bonusType`, i.e. the
    # single-resident recovery scope, or a non-consumable input type) keeps a scenario on Python.
    try:
        _state, _item_slot, reason = ka_abi.consumables_from_scenario(scenario)
    except Exception:  # noqa: BLE001
        return False, 'malformed consumable declaration'
    return (True, None) if reason is None else (False, reason)


def auto_native_safe(scenario):
    """Conservative bulk-worker subset with sampled saved-scenario parity.

    Tested rosters reach the simulator's 32-fighter total. Other skills have shown seed-specific
    divergence; explicit native probes can investigate them while auto keeps Python authoritative.
    """
    own = scenario.get('ownUnits') or ()
    return (0 < len(own) <= AUTO_NATIVE_MAX_UNITS
            and all(set(unit.get('skills') or ()) <= AUTO_NATIVE_SKILLS for unit in own))


def scenario_key(scenario):
    """Seed-independent candidate identity: the normalised scenario without its seed pair."""
    payload = {key: value for key, value in scenario.items() if key not in ('mathSeed', 'libSeed')}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()


def _build_engine(scenario):
    """The canonical pre-battle engine: `combat_sandbox.run_scenario`'s setup without `.run`."""
    support = prepare_setup(scenario)
    for source, prepared in zip(scenario['ownUnits'], support['ownUnits'], strict=True):
        for parameter in (10, 11):
            maximum = prepared['effectiveParameters'][parameter]['maximum']
            # Candidate scenarios round-trip through JSON in SQLite, which turns integer parameter
            # keys into strings. The Python engine accepts either spelling; the native template must
            # do the same or every stored build silently falls back to the Python battle loop.
            parameters = source['parameters']
            entry = parameters[str(parameter)] if str(parameter) in parameters else parameters[parameter]
            entry['rawValue'] = add_raw_parameter(entry['rawValue'], maximum, maximum)[0]
    support = prepare_setup(scenario)
    monsters = table('Monster')
    roster = []
    for source, prepared in zip(scenario['ownUnits'], support['ownUnits'], strict=True):
        roster.append(dict(
            name=source['name'], human=source['human'], team=0, grid=prepared['grid'],
            cell=prepared['cell'],
            parameters={int(k): v for k, v in source['parameters'].items()},
            skills=source['skills'],
            levels=source['invocationLevels'], equipment=source['equipment'],
            weaponId=source['weaponId'], humanFlags=source.get('humanFlags', 0),
            invokingSkills=source.get('invokingSkills', []),
            petOwnerName=source.get('petOwnerName'),
            monsterType=int(monsters[source['monsterId']][4])
            if source['monsterId'] is not None else None,
            monsterSize=int(monsters[source['monsterId']][5])
            if source['monsterId'] is not None else 0))
    enemy = support['encounter']
    for source in enemy['fighters']:
        roster.append(dict(
            name=f"enemy:{source['incomingIndex']}:{source['monsterId']}", human=False, team=1,
            grid=source['grid'], cell=source['cell'], boss=source['leaderIdentity'],
            monsterType=int(monsters[source['monsterId']][4]),
            monsterSize=int(monsters[source['monsterId']][5]),
            parameters={int(k): v for k, v in source['parameters'].items()},
            skills=source['skills']['dataIds'],
            levels=source['skills']['invocationLevels']))
    pre = scenario.get('prePlacement')
    if pre is None and 'startProfile' in scenario:
        pre = expand_start_profile(scenario['startProfile'], roster)
    if pre is not None:
        for spec in roster:
            spec['prePlacement'] = pre[spec['name']]
    engine = SharedControllers(
        roster, scenario['mathSeed'], scenario['libSeed'],
        row_offset=max(3, len(enemy['fighters']) // 5 + 1), movement=True,
        initialization_orders=[support['ownFormationOrder'], enemy['formationOrder']],
        prize_candidates=None)
    return engine, support, enemy


def _build_template(scenario, key, lib=None):
    """One native template per candidate, built from the seed-independent pre-battle state."""
    engine, support, enemy = _build_engine(copy.deepcopy(scenario))
    from combat_prizes import special_prize_candidates
    engine.prize_candidates = special_prize_candidates(scenario['encounterId'])
    # `SharedControllers.run` initialises these; a pre-battle engine has not run yet.
    engine.ending_gate_tick = None
    engine.ending_confirmed = None
    engine.ending_counter = None
    consumables, item_slot, _reason = ka_abi.consumables_from_scenario(scenario)
    # The watched units are scenario configuration; the names are resolved to the roster identities
    # both engines address a fighter by, so the reference observer and the kernel sample exactly the
    # same fighters. `mpWatchUnits` is the observation-only list (no stock, authorises nothing);
    # `holyHerbTriggerUnits` is the list that may spend a charge and is sampled too, because an MP
    # threshold policy has to observe the unit it acts for.
    watch_names = [str(name) for name in (scenario.get('mpWatchUnits')
                                          or scenario.get('holyHerbTriggerUnits') or ())]
    consumables['mp_watch'] = [engine.names[name] for name in watch_names]
    snapshot = ka_abi.engine_snapshot(engine, ROWS, 2, HUMAN_BASES, consumables)
    lib = lib or library()
    handle = ka_abi.load_snapshot(lib, snapshot)
    scope = RewardEntitlementWatch(
        dict(encounterId=scenario['encounterId'], fighters=[
            dict(skills=dict(dataIds=list(spec['skills']['dataIds'])))
            for spec in enemy['fighters']]),
        ROWS)
    lib.ka_battle_set_scope_allowed(handle, int(scope.scope['allowed']))
    return dict(handle=handle, tick_limit=int(scenario['tickLimit']),
                library_identity=_library_identity(lib),
                # The declared trigger-unit names, so the MP block can name the units it reports
                # without re-reading the scenario for every seed.
                watch_names=watch_names,
                # The declared finish policy travels with the template: `on-verdict` (the optimiser's
                # terminal-verdict rule) makes the kernel stop the tick loop at the recovered verdict
                # instead of running the whole horizon, exactly as `combat_sandbox` does in Python.
                # `battle_control`'s policy codes are 0 at-horizon, 1 after-ending, 2 on-verdict.
                policy=2 if scenario.get('finishPolicy') == 'on-verdict' else 0,
                follower_draws=int(enemy.get('followerSelectionDraws', 0)),
                scope=scope.scope, item_slot=item_slot)


def _template(scenario, key):
    entry = _cache.get(key)
    if entry is not None:
        _cache.move_to_end(key)
        _counters['templateCacheHits'] += 1
        return entry
    _counters['templateCacheMisses'] += 1
    entry = _build_template(scenario, key)
    _cache[key] = entry
    while len(_cache) > MAX_TEMPLATES:
        _, evicted = _cache.popitem(last=False)
        library().ka_battle_free(evicted['handle'])
        _counters['templateEvictions'] += 1
    return entry


def clear_cache():
    lib = library()
    for entry in _cache.values():
        lib.ka_battle_free(entry['handle'])
    _cache.clear()
    if _large_module is not None:
        _large_module.clear_cache()


def _encounter_counters(encounter):
    """The native event-site counters, shaped exactly like `combat_progress.EncounterCounters.report()`
    so `encounter_telemetry` and the parity check compare the two backends as one object."""
    return dict(
        ownFirstDeathTick=[[int(slot['identity']), int(slot['firstDeathTick'])]
                           for slot in encounter['own']],
        ownFirstLeavingTick=[[int(slot['identity']), int(slot['firstLeavingTick'])]
                             for slot in encounter['own']],
        enemyRolls=int(encounter['enemyRolls']),
        enemyRollHits=int(encounter['enemyRollHits']),
        enemyRollMisses=int(encounter['enemyRollMisses']),
        enemyResolvedAttacks=int(encounter['enemyResolvedAttacks']),
        enemyHits=int(encounter['enemyHits']),
        enemyMisses=int(encounter['enemyMisses']),
        counterChecks=int(encounter['counterChecks']),
        counterEnqueues=int(encounter['counterEnqueues']),
        bossDeathTick=int(encounter['bossDeathTick']),
        bossPostdeathAttempts=int(encounter['bossPostdeathAttempts']),
        bossPostdeathLands=int(encounter['bossPostdeathLands']),
        bossReentries=int(encounter['bossReentries']),
        bossLeavings=int(encounter['bossLeavings']),
        bossPostdeathGapCount=int(encounter['bossPostdeathGapCount']),
        bossPostdeathGapMin=int(encounter['bossPostdeathGapMin']),
        bossPostdeathGapMax=int(encounter['bossPostdeathGapMax']),
        bossPostdeathGapSum=int(encounter['bossPostdeathGapSum']),
        bossFutureHitsAtDeath=int(encounter['futureHitsAtDeath']),
        bossFutureIncludeExecuting=int(encounter['futureHitsIncludeExecuting']),
        bossAccessFirstCommand=int(encounter['bossAccessFirstCommand']),
        bossAccessFirstAttempt=int(encounter['bossAccessFirstAttempt']),
        targetableTicks=int(encounter['targetableTicks']),
        usingSkillTicks=int(encounter['usingSkillTicks']),
        bossDamagingResets=int(encounter['bossDamagingResets']))


def _native_compact(scenario, seed_pair, telemetry=False, timing=None):
    """The compact dict for one native run, or `(None, reason)` to fall back.

    `telemetry=True` attaches the additive `encounterTelemetry` block AFTER the digest, built from
    the kernel report fields that already exist. A compact counter the current kernel report does
    not publish is `null` with its reason, never a fabricated zero.

    `timing`, when given, receives NON-OVERLAPPING stage durations for this call: `prepareSeconds`
    (template/identity/clone), `seedSeconds` (RNG seed/replay setup), `nativeSeconds` (strictly the
    single `ka_run_battle` call), `freeSeconds` (clone release) and `convertSeconds` (report
    extraction / payload conversion). It is opt-in and never changes the payload or the digest; the
    fields are written only on the successful native return, so a fallback to Python never inherits a
    stale native interval. Absolute `nativeBeginAt`/`nativeEndAt` bracket ONLY `lib.ka_run_battle`,
    and `convertBeginAt`/`convertEndAt` bracket report/payload conversion. All absolute stamps use
    `_clock` (perf_counter), the same monotonic basis the worker and host use.
    """
    clock = _clock
    prepare_began = clock()
    supported, reason = eligibility(scenario)
    if not supported:
        return None, 'unsupported'
    key = scenario_key(scenario)
    if telemetry:
        # Opt-in versioned ABI: a separately named kernel that also publishes `ka_encounter_report`.
        # A missing kernel or getter is an explicit error; there is no silent Python fallback.
        lib = encounter_library()
        entry = _encounter_template(scenario, key)
    else:
        try:
            entry = _template(scenario, key)
        except Exception:  # noqa: BLE001
            return None, 'template'
        lib = library()
    if entry.get('library_identity') != _library_identity(lib):
        # The cached pointer was built by a different DLL image. Never clone across owners.
        if telemetry:
            raise RuntimeError('encounter template belongs to a different kernel build')
        return None, 'library-identity'
    clone = lib.ka_battle_clone(entry['handle'])
    if not clone:
        return None, 'clone'
    prepare_seconds = clock() - prepare_began
    seed_began = clock()
    encounter = None
    try:
        lib.ka_battle_seed_rng(clone, seed_pair[0], seed_pair[1])
        if entry['follower_draws']:
            lib.ka_battle_skip_lib_draws(clone, entry['follower_draws'])
        seed_seconds = clock() - seed_began
        report = ka_abi.KaBattleReport()
        native_began = clock()
        status = lib.ka_run_battle(clone, entry['tick_limit'], entry['policy'],
                                   ctypes.byref(report))
        native_ended = clock()
        native_seconds = native_ended - native_began
        if status != 0:
            if status == KA_ERR_CAPACITY:
                return None, ('object-capacity'
                              if lib.ka_object_count(clone) >= lib.object_capacity else 'capacity')
            return None, 'run'
        extract_began = clock()
        if telemetry:
            encounter = ka_encounter_abi.read(lib, clone, entry['policy'])
        extract_seconds = clock() - extract_began
    except Exception:  # noqa: BLE001
        if telemetry:
            raise
        return None, 'error'
    finally:
        free_began = clock()
        lib.ka_battle_free(clone)
        free_seconds = clock() - free_began
    convert_began = clock()
    import strategy_optimizer_adapter as adapter
    resolved = report.verdict != 0
    maximum = report.own_hp_max
    payload = dict(
        verdict=report.verdict if resolved else None,
        censored=not resolved,
        ticks=report.ticks,
        prizeCallbacks=(report.pre_verdict_prize_callbacks if resolved else None),
        retained=None,
        survivors=report.survivors if report.own_present else None,
        # `_compact`'s own definition: successful Holy Herb uses + successful battle-item uses.
        resourceUses=report.resource_uses,
        healthFraction=(round(report.own_hp / maximum, 6) if maximum else None),
        behavior=dict(heals=report.heals, attacks=report.attack_attempts,
                      prizes=report.prize_callbacks),
        seeds=list(seed_pair))
    payload['digest'] = adapter._digest(dict(payload))
    payload['rewardOutcome'] = _reward_outcome(report, entry['scope'])
    # Part B/C stored-attack progress, from the kernel's own observer. The keys and their order are
    # `combat_progress.blank_progress()`, so the Python and native readings are the same object as
    # far as the comparison is concerned (`check_native_progress.py` requires exact equality).
    payload['progressMetrics'] = dict(
        bossIdentity=(report.progress_boss if report.progress_boss >= 0 else -1),
        bossDeathTick=report.progress_boss_death_tick,
        bossLeavingTick=report.progress_boss_leaving_tick,
        storedCommandsAtDeath=report.progress_stored_at_death,
        storedCommandsTargetingBossAtDeath=report.progress_stored_targeting_boss_at_death,
        storedTargetHoldersAtDeath=report.progress_stored_target_holders_at_death,
        commandsTargetingBoss=report.progress_commands_targeting_boss,
        commandsTargetingBossReleased=report.progress_commands_targeting_boss_released,
        maxSimultaneousStoredCommands=report.progress_max_stored,
        maxSimultaneousCommandsTargetingBoss=report.progress_max_targeting_boss,
        storedTargetHoldersPeak=report.progress_target_holders_peak,
        postDeathBossReentries=report.progress_post_death_reentries,
        postDeathBossLeavings=report.progress_post_death_leavings,
        postDeathPrizes=report.progress_post_death_prizes,
        commandsReleasedAfterDeath=report.progress_released_after_death,
        commandsReleasedAfterDeathTargetingBoss=report.progress_released_after_death_targeting_boss,
        firstPostDeathCommandReleaseTick=report.progress_first_release_after_death,
        lastPostDeathCommandReleaseTick=report.progress_last_release_after_death)
    # MP telemetry for the declared trigger units and the explicit Holy Herb evidence, built through
    # the *same* builders the reference sandbox uses, so Python and native are compared as one object.
    # Both are attached after the digest, exactly like `progressMetrics`, and neither enters it.
    watch = max(0, min(report.mp_watch_count, ka_abi.KA_MAX_MP_WATCH))
    payload['mpMetrics'] = combat_progress.mp_metrics(
        entry['watch_names'], list(report.mp_identity)[:watch], list(report.mp_min)[:watch],
        list(report.mp_min_percent)[:watch], [bool(value) for value in list(report.mp_low)[:watch]],
        list(report.mp_first_low_tick)[:watch], list(report.mp_first_low_phase)[:watch],
        [bool(value) for value in list(report.mp_zero)[:watch]])
    logged = max(0, min(report.herb_log_count, ka_abi.KA_MAX_HERB_USES))
    payload['herbMetrics'] = combat_progress.herb_metrics(
        report.herb_stock_start, report.herb_stock_remaining, report.herb_max_uses,
        report.herb_use_count,
        [(list(report.herb_use_tick)[slot], list(report.herb_use_phase)[slot],
          list(report.herb_use_source)[slot], bool(list(report.herb_use_ok)[slot]))
         for slot in range(logged)])
    if telemetry:
        progress = dict(
            bossIdentity=(report.progress_boss if report.progress_boss >= 0 else -1),
            bossDeathTick=report.progress_boss_death_tick,
            bossLeavingTick=report.progress_boss_leaving_tick,
            storedCommandsAtDeath=report.progress_stored_at_death,
            storedCommandsTargetingBossAtDeath=report.progress_stored_targeting_boss_at_death,
            storedTargetHoldersAtDeath=report.progress_stored_target_holders_at_death,
            commandsTargetingBoss=report.progress_commands_targeting_boss,
            commandsTargetingBossReleased=report.progress_commands_targeting_boss_released,
            maxSimultaneousStoredCommands=report.progress_max_stored,
            maxSimultaneousCommandsTargetingBoss=report.progress_max_targeting_boss,
            storedTargetHoldersPeak=report.progress_target_holders_peak,
            postDeathBossReentries=report.progress_post_death_reentries,
            postDeathBossLeavings=report.progress_post_death_leavings,
            postDeathPrizes=report.progress_post_death_prizes,
            commandsReleasedAfterDeath=report.progress_released_after_death,
            commandsReleasedAfterDeathTargetingBoss=report.progress_released_after_death_targeting_boss,
            firstPostDeathCommandReleaseTick=report.progress_first_release_after_death,
            lastPostDeathCommandReleaseTick=report.progress_last_release_after_death)
        # Stable own identity comes from the kernel's own roster (the same identities the Python
        # engine addresses a fighter by); a declared semantic role is carried when the setup row has
        # one, otherwise `encounter_telemetry` labels it `unclassified`.
        roster = scenario.get('ownUnits') or []
        roles = {unit.get('name'): adapter._resolved_role(unit) for unit in roster}
        own_units = []
        for index, slot in enumerate(encounter['own']):
            name = roster[index].get('name') if index < len(roster) else None
            own_units.append(dict(identity=slot['identity'], name=name, role=roles.get(name)))
        own_units = own_units or None
        payload['encounterTelemetry'] = combat_progress.encounter_telemetry(
            progress,
            own_units=own_units,
            own_survivors=(report.survivors if report.own_present else None),
            own_present=bool(report.own_present),
            resource_uses=report.resource_uses,
            herb=payload['herbMetrics'],
            item_use_count=int(encounter['itemUsesOk']),
            verdict=payload['verdict'],
            censored=payload['censored'],
            finish_boundary=None,
            finish_boundary_label=None,
            reward_outcome=payload['rewardOutcome'],
            finish_policy=scenario.get('finishPolicy'),
            # The kernel field `finish_dispatched_chests` is documented and set in `battle_control`
            # as the pre-verdict prize count (`prizes_at_verdict`), a pending/queue reading, not a
            # final dispatch. Report it under `queueAtVerdict`; `encounter_telemetry` applies the
            # canonical win-only dispatch gate so a terminal loss never dispatches that queue.
            diagnostic_finish=(dict(queueAtVerdict=int(encounter['finishDispatchedChests']))
                               if encounter['finishDispatchedChests'] >= 0 else None),
            counters=_encounter_counters(encounter))
    payload['resultBackend'] = 'native'
    convert_ended = clock()
    if timing is not None:
        timing['prepareSeconds'] = prepare_seconds
        timing['seedSeconds'] = seed_seconds
        # Strict native bracket: these two stamps surround ONLY the lib.ka_run_battle call above.
        timing['nativeBeginAt'] = native_began
        timing['nativeEndAt'] = native_ended
        timing['nativeSeconds'] = native_seconds
        timing['nativePath'] = True
        timing['extractSeconds'] = extract_seconds
        timing['freeSeconds'] = free_seconds
        timing['convertBeginAt'] = convert_began
        timing['convertEndAt'] = convert_ended
        timing['convertSeconds'] = convert_ended - convert_began
    return payload, None


class _WatchStub:
    """The `RewardEntitlementWatch` surface `entitlement_report` reads, filled from the native
    report. The prose is produced by the canonical formatter, so it cannot drift."""

    def __init__(self, report, scope):
        self.scope = scope
        self.pending_final = report.pending_final
        self.pending_at_verdict = (None if report.pending_at_verdict < 0
                                   else report.pending_at_verdict)
        self.post_certificate_delta = report.post_certificate_delta
        self.post_certificate_changed = report.post_certificate_delta != 0
        self.observations = report.observations
        self.verdict_observations = report.verdict_observations
        clauses = dict(zip(('C1_bossHpZero', 'C2_bossLeavingState8', 'C3_noStoredTarget',
                            'C4_noQueuedCommandTarget'), [bool(v) for v in report.clauses]))
        self._readings = dict(bossHp=report.boss_hp, bossState=report.boss_state,
                              clauses=clauses, storedTargetHolders=[], bossTargetCommandHolders=[])
        self.certificate = None
        if report.certificate_held:
            self.certificate = dict(frame=report.certificate_frame,
                                    pendingChestCount=report.certificate_pending, clauses=clauses,
                                    storedTargetHolders=[], bossTargetCommandHolders=[])
        self.late_hold = (None if report.late_hold_frame < 0
                          else dict(frame=report.late_hold_frame, pendingChestCount=None,
                                    clauses=clauses))

    def readings(self):
        return self._readings


def _reward_outcome(report, scope):
    """The canonical `_reward_outcome`, built from the native observer state."""
    from combat_reward_entitlement import entitlement_report
    resolved = report.verdict != 0
    if not resolved:
        boundary = 'censored-unresolved-battle'
    elif report.ending_confirmed:
        boundary = 'diagnostic-declared-finish; native-auto-producer-unproven'
    else:
        boundary = 'awaiting-confirmation'
    entitlement = entitlement_report(
        watch=_WatchStub(report, scope), verdict=(report.verdict if resolved else None),
        prize_callbacks=report.prize_callbacks, diagnostic_dispatched=0,
        finish_boundary=boundary)
    outcome = dict(pendingChests=entitlement.get('pendingChestCount'),
                   awardedChests=entitlement.get('awardedChestCount'),
                   awardedBasis=entitlement.get('awardedChestCountBasis'),
                   inventoryVerified=False,
                   reason=entitlement.get('rewardCountReason'))
    certificate = entitlement.get('certificate') or {}
    if certificate.get('holds'):
        outcome['certificate'] = dict(certificateId=entitlement.get('certificateId'), holds=True,
                                      frame=certificate.get('frame'),
                                      issuedBeforeVerdict=bool(certificate.get('issuedBeforeVerdict')),
                                      timingRule=certificate.get('timingRule'))
    return outcome


def simulate_compact(scenario, seed_pair, backend='auto', telemetry=False, timing=None):
    """`backend`: 'auto' (native when eligible, else Python), 'native', or 'python'.

    `telemetry` is opt-in and only adds the additive `encounterTelemetry` block after the digest;
    the legacy digest and metrics are unchanged whether telemetry is on or off.
    """
    import strategy_optimizer_adapter as adapter
    if backend == 'python':
        _counters['pythonRuns'] += 1
        compact = adapter._python_simulate_compact(scenario, seed_pair, telemetry=telemetry,
                                                   timing=timing)
        compact['resultBackend'] = 'python'
        compact['routingVersion'] = ROUTING_VERSION
        return compact
    supported, why = eligibility(scenario)
    reason = ('unvalidated auto-native combination' if backend == 'auto' and not auto_native_safe(scenario)
              else why or 'unsupported')
    if supported and reason != 'unvalidated auto-native combination':
        compact, reason = _native_compact(scenario, seed_pair, telemetry=telemetry, timing=timing)
        if compact is None and reason == 'object-capacity':
            expanded = _large_backend()
            if expanded is not None:
                _counters['nativeCapacityRetries'] += 1
                try:
                    compact, reason = expanded._native_compact(scenario, seed_pair,
                                                               telemetry=telemetry, timing=timing)
                finally:
                    # Large arenas are exceptional: release the template as well as the clone
                    # after each retry instead of retaining one large allocation per worker.
                    expanded.clear_cache()
                if compact is not None:
                    _counters['nativeLargeRuns'] += 1
    else:
        compact = None
    if compact is not None:
        _counters['nativeRuns'] += 1
        compact['routingVersion'] = ROUTING_VERSION
        return compact
    if telemetry and supported and reason != 'unvalidated auto-native combination':
        # The opt-in encounter kernel was required for a supported scenario but the native run
        # could not produce it. Fail loudly instead of silently building a Python telemetry block
        # that lacks the native counters.
        raise RuntimeError(f'encounter telemetry native run failed: {reason}')
    _counters['nativeFallbacks'] += 1
    _counters['nativeFallbackReasons[' + str(reason) + ']'] += 1
    _counters['pythonRuns'] += 1
    fallback = adapter._python_simulate_compact(scenario, seed_pair, telemetry=telemetry,
                                                timing=timing)
    fallback['resultBackend'] = 'python'
    fallback['routingVersion'] = ROUTING_VERSION
    # Keep the reason with the saved run. Candidate scenarios may be pruned later, so worker-local
    # counters alone cannot explain a persistent fallback rate in the live library.
    fallback['nativeFallbackReason'] = reason
    return fallback


#: `auto` uses the sampled parity subset and leaves unvalidated combinations on Python.
DEFAULT_BACKEND = 'auto'
