"""Shared, deterministic combat-mechanics intelligence for strategy search.

This module is a read-only layer over the recovered combat primitives. Every
helper wraps an authoritative recovered function: `combat_resolution`
(`critical_rate`, `hit_rate`, `attack_interval`, `base_damage`,
`skill_mp_cost`, `skill_invocation_rate`), `combat_setup.prepare_setup`,
`combat_encounters.special_enemy_baseline`, `combat_skill_selection`.
Importing this module or calling `profile` never runs a whole battle and never
touches SQLite or a fitted model; it only prepares inputs and enumerates exact
small-domain mathematics.

The two distribution primitives are promoted verbatim from
`tools/recovery/derive_atk_arms.py` (`base_damage_pmf`, `hits_to_kill`) so the
shared layer owns one exact implementation instead of importing that diagnostic
script (whose import also pulls unrelated modules). A focused check re-derives
`base_damage_pmf` from `combat_resolution.base_damage` to prevent drift.
"""
from __future__ import annotations

import bisect
import collections
import hashlib
import json
import pickle
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import combat_encounters
import combat_scenario
import combat_setup
from combat_initial_state import i32, trunc_div
from combat_parameters import average_training_level
from combat_resolution import (attack_interval, base_damage, critical_rate,
                               hit_rate, skill_invocation_rate, skill_mp_cost)
from combat_runtime_data import data_path, load_data, table_path, RUNTIME_DATA_FILES, RUNTIME_TABLES
from combat_skill_selection import active_skill_infos

MECHANICS_REVISION = 'strategy-mechanics-3'
_ENCOUNTER_DIGEST_REVISION = 'strategy-encounter-digest-1'
CACHE_KEY_REVISION = 'strategy-mechanics-cache-3'

HERE = Path(__file__).resolve().parent

#: Recovered source files and runtime tables whose contents the numbers in this
#: module are derived from. `dependency_manifest` hashes them at import, so a
#: cache key or revision born from this process names exactly the inputs that
#: produced it. This is NOT a live watcher: a file changed on disk after import
#: needs a process restart; `invalidate_caches` only clears in-process caches.
_SOURCE_FILES = tuple(sorted({path.name for path in HERE.glob('combat*.py')} | {
    'strategy_mechanics.py', 'strategy_encounter_compiler.py', 'strategy_build_domain.py',
    'search_contract.py'}))
_DATA_FILES = tuple(RUNTIME_DATA_FILES)

#: Parameter ids read from the recovered model (same set the runner uses).
PID_HP, PID_MP, PID_ATK, PID_DEF, PID_SPD, PID_LUK, PID_MAG, PID_DEX = (
    10, 11, 13, 14, 15, 16, 18, 19)

#: Damage/TTK reduction is explicitly capped for performance; callers must
#: surface this rather than calling the result an exact whole-battle prediction.
TTK_HIT_CAP = 256
TTK_HP_CAP = 400
TTK_WORK_CAP = 250000
#: Values retained when a soft attack-profile fit trims the mechanical boundary set.
ATTACK_CANDIDATE_CAP = 10
#: Native `attack_interval` clamps its input here; used only when the search
#: contract's authoritative synthetic AGI interval is unavailable.
NATIVE_AGILITY_CLAMP = 99999

_SKILL_ROWS = {row['id']: row for row in load_data('weapon-skill-profiles.json')['skills']}

_INVALIDATORS = []


def canonical(value):
    """Deterministic JSON used for every cache key and version digest."""
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _file_digest(path):
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


@lru_cache(maxsize=1)
def dependency_manifest():
    """Per-file digests of every source/data input plus the semantic revisions."""
    sources = {name: _file_digest(HERE / name) for name in _SOURCE_FILES}
    data = {}
    for name in _DATA_FILES:
        try:
            data[name] = _file_digest(data_path(name))
        except Exception:  # missing runtime data is recorded, never guessed
            data[name] = None
    for name in RUNTIME_TABLES:
        try:
            data['table/' + name] = _file_digest(table_path(name))
        except (OSError, ValueError):
            data['table/' + name] = None
    # Search bounds also affect inverse solutions; they live outside runtime tables.
    try:
        import search_contract
        data['synthetic-stat-bounds'] = hashlib.sha256(canonical(search_contract.stat_bounds()).encode()).hexdigest()
    except (OSError, ValueError):
        data['synthetic-stat-bounds'] = None
    return dict(mechanicsRevision=MECHANICS_REVISION, cacheKeyRevision=CACHE_KEY_REVISION,
                source=sources, data=data)


@lru_cache(maxsize=1)
def dependency_digest():
    """Content digest over `dependency_manifest`; part of every cache key here."""
    return hashlib.sha256(canonical(dependency_manifest()).encode('utf-8')).hexdigest()


def register_invalidator(callback):
    """Let a dependent module (the compiler) clear its own cache on invalidation."""
    _INVALIDATORS.append(callback)


def invalidate_caches():
    """Clear this process's caches. Does not reload already-imported source/data."""
    _prepared_cached.cache_clear()
    _normalized_cached.cache_clear()
    _profile_cached.cache_clear()
    _speed_table.cache_clear()
    synthetic_stat_bounds.cache_clear()
    dependency_manifest.cache_clear()
    dependency_digest.cache_clear()
    base_damage_pmf.cache_clear()
    mean_damage.cache_clear()
    damage_distribution.cache_clear()
    _ttk_cached.cache_clear()
    for callback in list(_INVALIDATORS):
        callback()


def _cache_key(kind, payload):
    return canonical(dict(kind=kind, mechanicsRevision=MECHANICS_REVISION,
                          cacheKeyRevision=CACHE_KEY_REVISION,
                          dependencyDigest=dependency_digest(), payload=payload))


@lru_cache(maxsize=8)
def synthetic_stat_bounds(parameter_id=None):
    """Authoritative permitted synthetic-stat interval from `search_contract`.

    Returns `(minimum, maximum)` for one parameter id, the whole `{id: (min, max)}`
    mapping when `parameter_id is None`, or `None` when the derived bound table is
    unavailable. It is the admissible synthetic search domain; signed-32 storage is
    a separate, weaker floor that this never substitutes for.
    """
    try:
        import search_contract
        table = search_contract.stat_bounds()
    except Exception:
        return None
    if parameter_id is None:
        return tuple(sorted((int(k), (int(v[0]), int(v[1]))) for k, v in table.items()))
    span = table.get(parameter_id)
    return None if span is None else (int(span[0]), int(span[1]))


def attack_search_bounds(context='synthetic', lo=None, hi=None):
    """Resolve ATT search bounds, naming the source. Never falls back to signed-32.

    `synthetic` uses the search contract's permitted interval; a caller may tighten
    either end. `player`/`unrestricted` have no authoritative interval here, so an
    explicit `lo`/`hi` is required and the reason is named rather than implied.
    """
    if lo is not None and hi is not None:
        if lo > hi:
            raise ValueError('attack bounds low must not exceed high')
        return dict(low=int(lo), high=int(hi), source='caller', context=context)
    span = synthetic_stat_bounds(PID_ATK) if context == 'synthetic' else None
    if span is None:
        raise ValueError('no authoritative ATT bounds for context %r; pass explicit lo/hi' % context)
    return dict(low=span[0] if lo is None else int(lo), high=span[1] if hi is None else int(hi),
                source='search-contract.stat_bounds', context='synthetic')


def speed_domain(context='synthetic'):
    """AGI interval the cached speed table covers, with an explicit named source."""
    if context == 'synthetic':
        span = synthetic_stat_bounds(PID_SPD)
        if span is not None:
            return dict(low=1, high=span[1], source='search-contract.stat_bounds', context='synthetic')
    return dict(low=1, high=NATIVE_AGILITY_CLAMP,
                source='native-attack-interval-clamp', context='unrestricted')


#: Resolved once at import; `speed_domain('unrestricted')` names the wider fallback.
_SPEED_DOMAIN = speed_domain('synthetic')


# --------------------------------------------------------------------------- #
# Promoted exact distributions (verbatim from derive_atk_arms.py)
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=1024)
def base_damage_pmf(attack, defense, critical=False):
    """Exact per-hit damage PMF from the recovered formula, over both uniform rolls."""
    armor = trunc_div(i32(defense), 8) if critical else i32(defense)
    pmf = collections.defaultdict(float)
    for attack_roll in range(80, 121):
        scaled = max(1, trunc_div(i32(i32(attack) * attack_roll), 100))
        for defense_roll in range(70, 91):
            damage = i32(scaled - trunc_div(i32(armor * defense_roll), 100))
            if damage <= 5:
                for value in range(1, 11):
                    pmf[value] += 0.1
            else:
                pmf[damage] += 1.0
    total = 41 * 21
    return {value: weight / total for value, weight in pmf.items()}


def hits_to_kill(pmf, hp, max_hits=100000, prune=0.0):
    """Distribution of the number of hits needed to remove `hp` (exact convolution)."""
    alive = {hp: 1.0}
    out = {}
    hits = 0
    while alive and hits < max_hits:
        hits += 1
        nxt = collections.defaultdict(float)
        for remaining, probability in alive.items():
            for damage, weight in pmf.items():
                if probability * weight <= prune:
                    continue
                left = remaining - damage
                if left <= 0:
                    out[hits] = out.get(hits, 0.0) + probability * weight
                else:
                    nxt[left] += probability * weight
        alive = nxt
    return out


@lru_cache(maxsize=2048)
def mean_damage(attack, defense, critical=False):
    """Exact expected damage per landed hit (fallback branch's uniform 1..10 mean)."""
    armor = trunc_div(i32(defense), 8) if critical else i32(defense)
    total = 0.0
    for attack_roll in range(80, 121):
        scaled = max(1, trunc_div(i32(i32(attack) * attack_roll), 100))
        for defense_roll in range(70, 91):
            damage = i32(scaled - trunc_div(i32(armor * defense_roll), 100))
            total += 5.5 if damage <= 5 else damage
    return total / (41 * 21)


def _quantile(distribution, q):
    if not distribution:
        return None
    total = sum(distribution.values())
    running = 0.0
    for value in sorted(distribution):
        running += distribution[value]
        if running >= q * total:
            return value
    return max(distribution)


def pmf_summary(pmf):
    """Min/max/mean/quantiles of a damage PMF; JSON serializable."""
    if not pmf:
        return dict(min=None, max=None, mean=None, p10=None, median=None, p90=None)
    total = sum(pmf.values())
    mean = sum(v * w for v, w in pmf.items()) / total
    return dict(min=min(pmf), max=max(pmf), mean=round(mean, 6),
                p10=_quantile(pmf, 0.1), median=_quantile(pmf, 0.5), p90=_quantile(pmf, 0.9))


# --------------------------------------------------------------------------- #
# Per-fact profiles (each wraps an authoritative function)
# --------------------------------------------------------------------------- #
def _bounded_inverse(predicate, lo, hi):
    """Smallest integer in [lo, hi] whose monotone-nondecreasing predicate is True.

    Returns None when the predicate is False at `hi`. It calls only the predicate, so
    every inverse in this module is defined by the authoritative function itself rather
    than by a transcribed clamp constant.
    """
    if lo > hi or not predicate(hi):
        return None
    left, right = lo, hi
    while left < right:
        mid = (left + right) // 2
        if predicate(mid):
            right = mid
        else:
            left = mid + 1
    return left


def _luck_domain(domain=None):
    span = synthetic_stat_bounds(PID_LUK) if domain is None else (int(domain[0]), int(domain[1]))
    if span is None:
        raise ValueError('no authoritative Luck domain; pass an explicit domain')
    return span


def _dex_domain(domain=None):
    span = synthetic_stat_bounds(PID_DEX) if domain is None else (int(domain[0]), int(domain[1]))
    if span is None:
        raise ValueError('no authoritative Dexterity domain; pass an explicit domain')
    return span


def crit_rate_inverse(target_rate, *, domain=None):
    """Smallest Luck whose authoritative `critical_rate` reaches `target_rate`.

    A bounded inverse over the explicit Luck domain; no crit-band constant is copied.
    """
    lo, hi = _luck_domain(domain)
    first = _bounded_inverse(lambda value: critical_rate(value) >= target_rate, lo, hi)
    return dict(targetRate=target_rate, luck=first, domain=dict(low=lo, high=hi),
                reached=first is not None,
                rate=None if first is None else critical_rate(first),
                source='bounded inverse of combat_resolution.critical_rate')


def crit_profile(luck, *, domain=None):
    """`critical_rate` plus the class boundaries around it, from bounded inverses."""
    rate = critical_rate(luck)
    if luck < 0:
        band = 'below-domain'
    elif luck < 100:
        band = 'band-0-99'
    elif luck < 1000:
        band = 'band-100-999'
    elif luck < 100000:
        band = 'band-1000-99999'
    else:
        band = 'at-or-above-100000'
    lo, hi = _luck_domain(domain)
    first_current = _bounded_inverse(lambda value: critical_rate(value) >= rate, lo, hi)
    next_value = None
    if critical_rate(hi) > rate:
        next_value = _bounded_inverse(lambda value: critical_rate(value) > rate, lo, hi)
    previous_value = None
    if rate - 1 >= critical_rate(lo):
        previous_value = _bounded_inverse(lambda value: critical_rate(value) >= rate - 1, lo, hi)
    return dict(rate=rate, band=band, nextLuckValueChangingRate=next_value,
                classEnteredAtLuck=first_current, previousClassEnteredAtLuck=previous_value,
                domain=dict(low=lo, high=hi),
                derivation='class boundaries are bounded inverses of combat_resolution.critical_rate')


def hit_rate_inverse(agility, luck, target_rate, *, domain=None):
    """Smallest enemy DEX whose authoritative incoming `hit_rate` reaches `target_rate`.

    The inverse calls `combat_resolution.hit_rate` itself, so the accuracy cap and floor
    are discovered inside the explicit DEX domain instead of being re-derived from a
    transcribed clamp offset.
    """
    lo, hi = _dex_domain(domain)
    first = _bounded_inverse(lambda value: hit_rate(value, agility, luck) >= target_rate, lo, hi)
    return dict(targetRate=target_rate, dexterity=first, domain=dict(low=lo, high=hi),
                reached=first is not None,
                rate=None if first is None else hit_rate(first, agility, luck),
                source='bounded inverse of combat_resolution.hit_rate')


def hit_profile(dexterity, agility, luck, *, domain=None):
    """`hit_rate` plus saturation/floor thresholds derived by bounded inverse.

    The rate ceiling, the rate floor and the first DEX that reaches each are read from
    `combat_resolution.hit_rate` over the explicit domain, so no clamp value (97) or
    clamp offset is copied into policy.
    """
    lo, hi = _dex_domain(domain)
    rate = hit_rate(dexterity, agility, luck)
    rate_ceiling = hit_rate(hi, agility, luck)
    rate_floor = hit_rate(lo, agility, luck)
    first_at_ceiling = _bounded_inverse(lambda value: hit_rate(value, agility, luck) >= rate_ceiling, lo, hi)
    first_above_floor = _bounded_inverse(lambda value: hit_rate(value, agility, luck) > rate_floor, lo, hi)
    return dict(rate=rate, domain=dict(low=lo, high=hi), rateCeiling=rate_ceiling,
                rateFloor=rate_floor, firstDexterityAtCeiling=first_at_ceiling,
                firstDexterityAboveFloor=first_above_floor,
                dexterityAtOrAboveCeiling=(dexterity >= first_at_ceiling) if first_at_ceiling is not None else False,
                capped=rate >= rate_ceiling,
                source='bounded inverse of combat_resolution.hit_rate',
                derivation='saturation and floor thresholds call hit_rate over the explicit domain; '
                           'no clamp constant is copied')


def hit_cap_dexterity(agility, luck, *, domain=None):
    """First DEX at which incoming `hit_rate` saturates; used by the encounter compiler."""
    return hit_profile(0, agility, luck, domain=domain)['firstDexterityAtCeiling']


@lru_cache(maxsize=8)
def _speed_table(max_agility):
    """(`agi_thresholds`, `intervals`) sorted ascending - one entry per band.

    The scan is bounded by `max_agility`, which comes from the search contract's
    permitted synthetic span (or the native clamp for an explicit unrestricted
    request). It is not a "legal-ish" round number.
    """
    thresholds, intervals = [], []
    previous = None
    for agi in range(1, int(max_agility) + 1):
        interval = attack_interval(agi)
        if interval != previous:
            thresholds.append(agi)
            intervals.append(interval)
            previous = interval
    return tuple(thresholds), tuple(intervals)


def _band_position(interval, max_agility):
    """Leftmost band index whose interval is <= `interval` (intervals descend)."""
    _, intervals = _speed_table(max_agility)
    return bisect.bisect_left([-value for value in intervals], -interval)


def action_timing(agility, max_agility=None):
    """Interval, the reduced-path period (+21) and the two adjacent band edges.

    `period` is the interval plus the recovered 21-frame gap for one normal attack
    on an uninterrupted path. It is *not* a universal action cadence: skills,
    reactions, movement and status all shift actual frames.
    """
    max_agility = _SPEED_DOMAIN['high'] if max_agility is None else max_agility
    interval = attack_interval(agility)
    thresholds, intervals = _speed_table(max_agility)
    position = _band_position(interval, max_agility)
    base = dict(interval=interval, period=interval + 21,
                periodSemantics='uninterrupted normal-attack reduced path',
                speedDomain=dict(low=1, high=max_agility))
    if position >= len(thresholds):
        base.update(lowerAgilityEdge=None, upperAgilityEdge=None,
                    nextFasterInterval=None, nextSlowerInterval=None)
        return base
    base.update(
        lowerAgilityEdge=thresholds[position],
        upperAgilityEdge=(thresholds[position + 1] - 1
                          if position + 1 < len(thresholds) else None),
        nextFasterInterval=intervals[position + 1]
        if position + 1 < len(intervals) else None,
        nextSlowerInterval=intervals[position - 1] if position > 0 else None)
    return base


def agility_for_interval(interval, max_agility=None):
    """Smallest AGI whose interval is <= `interval` (bounded inverse helper)."""
    max_agility = _SPEED_DOMAIN['high'] if max_agility is None else max_agility
    thresholds, _ = _speed_table(max_agility)
    position = _band_position(interval, max_agility)
    return thresholds[position] if position < len(thresholds) else None


def interval_contact_boundaries(agility, max_agility=None):
    """Timing-only interval ladder for this AGI band (never a hit-probability claim).

    This function is *timing*: it lists the AGI values at which the own attack interval
    crosses each incoming interval. It does not receive the enemy's Dexterity or the
    unit's Luck and therefore does not evaluate `hit_rate`; use
    `incoming_contact_boundaries` for the authoritative incoming-accuracy view.
    """
    timing = action_timing(agility, max_agility=max_agility)
    thresholds, intervals = _speed_table(_SPEED_DOMAIN['high'] if max_agility is None else max_agility)
    low_edge, high_edge = timing['lowerAgilityEdge'], timing['upperAgilityEdge']
    landmarks = []
    for position, incoming in enumerate(intervals):
        required = thresholds[position]
        in_band = (low_edge is not None and high_edge is not None
                   and low_edge <= required <= high_edge)
        landmarks.append(dict(incomingInterval=incoming, requiredAgility=required,
                              inCurrentBand=bool(in_band)))
    return dict(interval=timing['interval'], lowerAgilityEdge=low_edge,
                upperAgilityEdge=high_edge, period=timing['period'],
                periodSemantics=timing['periodSemantics'],
                speedDomain=timing['speedDomain'],
                matchToBeat={speed: agility_for_interval(speed)
                             for speed in (timing['interval'], timing['interval'] - 1)},
                matchSemantics='timing',
                timingMatchLandmarks=landmarks,
                timingMatchLandmarkCount=len(landmarks),
                note='Interval matching is timing only, not hit probability. Use '
                     'incoming_contact_boundaries for the authoritative hit_rate view.')


def incoming_contact_boundaries(enemy_dexterity, own_luck, agility, bounds=None):
    """Actual incoming `hit_rate` changes inside the current own-AGI interval band.

    For a fixed enemy DEX and the unit's own Luck, `hit_rate(enemy_dexterity, own_agi,
    own_luck)` falls as own AGI rises. Inside one timing band (`attack_interval`
    constant) the rate still steps down whenever `trunc_div(agi, 5)` increments, so two
    points can share an interval yet differ in incoming accuracy. The landmarks are the
    AGI values where the authoritative rate actually changes, found by a bounded inverse
    over rate classes (at most one search per class, never a per-AGI scan). `bounds` is
    the explicit own-AGI domain and defaults to the permitted synthetic Speed interval.
    """
    span = (speed_domain('synthetic') if bounds is None
            else dict(low=int(bounds[0]), high=int(bounds[1]), source='caller', context='caller'))
    lo, hi = int(span['low']), int(span['high'])
    timing = action_timing(agility, max_agility=hi)
    band_lo = int(timing['lowerAgilityEdge']) if timing['lowerAgilityEdge'] is not None else int(agility)
    band_hi = int(timing['upperAgilityEdge']) if timing['upperAgilityEdge'] is not None else int(agility)
    band_lo, band_hi = max(band_lo, lo), min(band_hi, hi)
    rate_at_lowest = hit_rate(enemy_dexterity, band_lo, own_luck)
    rate_at_highest = hit_rate(enemy_dexterity, band_hi, own_luck)
    transitions = []
    for threshold in range(rate_at_lowest - 1, rate_at_highest - 1, -1):
        step = _bounded_inverse(
            lambda value, t=threshold: hit_rate(enemy_dexterity, value, own_luck) <= t,
            band_lo, band_hi)
        if step is None or step <= band_lo:
            continue
        before = hit_rate(enemy_dexterity, step - 1, own_luck)
        after = hit_rate(enemy_dexterity, step, own_luck)
        if before != after and (not transitions or transitions[-1]['agility'] != step):
            transitions.append(dict(agility=int(step), hitRateBefore=before, hitRateAfter=after))
    return dict(
        schema='ka-incoming-contact-boundaries-1', enemyDexterity=int(enemy_dexterity),
        ownLuck=int(own_luck), agility=int(agility),
        hitRateAtAgility=hit_rate(enemy_dexterity, agility, own_luck),
        interval=timing['interval'], period=timing['period'],
        periodSemantics=timing['periodSemantics'],
        band=dict(low=band_lo, high=band_hi),
        hitRateAtBandLow=rate_at_lowest, hitRateAtBandHigh=rate_at_highest,
        rateChangeCount=len(transitions), interiorLandmarks=transitions,
        domain=dict(low=lo, high=hi, source=span['source']),
        semantics='Rate changes are authoritative hit_rate values inside a timing band; '
                  'the interval itself is timing only, so the same interval can hold '
                  'different incoming hit probabilities.')


@lru_cache(maxsize=512)
def damage_distribution(attack, defense, luck, dexterity=None, agility=None,
                        defender_luck=None):
    """Normal/crit landed PMFs plus the full per-hit PMF (miss mass at 0).

    `luck` is the attacker's crit Luck. When `dexterity`, `agility` and
    `defender_luck` are supplied the authoritative `hit_rate` gives the miss
    probability per the §2.4 combination; otherwise miss is reported as None.
    """
    normal = base_damage_pmf(attack, defense, False)
    crit = base_damage_pmf(attack, defense, True)
    crit_rate = critical_rate(luck)
    probabilities = dict(critical=crit_rate / 100.0)
    if dexterity is not None and agility is not None and defender_luck is not None:
        probabilities['hit'] = hit_rate(dexterity, agility, defender_luck) / 100.0
        probabilities['miss'] = round((1 - probabilities['critical'])
                                      * (1 - probabilities['hit']), 12)
    else:
        probabilities['hit'] = None
        probabilities['miss'] = None
    per_hit = collections.defaultdict(float)
    weight_crit = probabilities['critical']
    for value, probability in crit.items():
        per_hit[value] += weight_crit * probability
    if probabilities['hit'] is None:
        for value, probability in normal.items():
            per_hit[value] += (1 - weight_crit) * probability
    else:
        for value, probability in normal.items():
            per_hit[value] += (1 - weight_crit) * probabilities['hit'] * probability
        per_hit[0] += probabilities['miss']
    return dict(
        model='reduced base-law per landed hit (attack/defense roll formula plus the '
              'critical half-armour branch only)',
        modelKind='base-law-per-hit', completeUnitCombatPmf=False,
        notModelled=['invoking skill type/value modifiers', 'weapon type/affinity differences',
                     'status, buffs, barriers, reflection', 'target-selection or area branches'],
        normalPmf={k: round(v, 12) for k, v in normal.items()},
        critPmf={k: round(v, 12) for k, v in crit.items()},
        perHitPmf={k: round(v, 12) for k, v in per_hit.items()},
        probabilities={k: (None if v is None else round(v, 12))
                       for k, v in probabilities.items()},
        normalSummary=pmf_summary(normal), critSummary=pmf_summary(crit),
        perHitSummary=pmf_summary(dict(per_hit)))


def _expected_hits(per_hit_pmf, hp):
    total = sum(per_hit_pmf.values())
    if not total:
        return None
    mean = sum(value * weight for value, weight in per_hit_pmf.items()) / total
    return None if mean <= 0 else hp / mean


def ttk_profile(per_hit_pmf, hp, max_hits=TTK_HIT_CAP, hp_cap=TTK_HP_CAP):
    """Capped, pruned hits-to-kill profile; every approximation is labelled.

    `hp > hp_cap` skips the convolution entirely and returns only an approximate
    expected-hits value, so a large roster cannot make the profile expensive.
    """
    return _ttk_cached(tuple(sorted(per_hit_pmf.items())), hp, max_hits, hp_cap)


@lru_cache(maxsize=512)
def _ttk_cached(entries, hp, max_hits, hp_cap):
    per_hit_pmf = dict(entries)
    approximate = _expected_hits(per_hit_pmf, hp)
    approximate = None if approximate is None else round(approximate, 4)
    if hp > hp_cap:
        return dict(computed=False, truncated=True, reason='enemy-hp-above-cap',
                    enemyHp=hp, hpCap=hp_cap, hitCap=max_hits,
                    approximateExpectedHits=approximate)
    compact = {value: weight for value, weight in per_hit_pmf.items() if weight >= 1e-9}
    if max_hits * max(1, hp) * len(compact) > TTK_WORK_CAP:
        return dict(computed=False, truncated=True, reason='convolution-work-cap',
                    enemyHp=hp, hpCap=hp_cap, hitCap=max_hits, workCap=TTK_WORK_CAP,
                    approximateExpectedHits=approximate)
    # Bound the convolution itself, not just the returned rows. Miss mass can
    # otherwise keep a nonempty alive map for 100,000 iterations.
    kept = hits_to_kill(compact, hp, max_hits=max_hits, prune=1e-14)
    truncated = sum(kept.values()) < 1.0 - 1e-8
    summary = None
    if not truncated and kept:
        summary = dict(mean=round(sum(v * w for v, w in kept.items()) / sum(kept.values()), 4),
                       median=_quantile(kept, 0.5), p10=_quantile(kept, 0.1),
                       p90=_quantile(kept, 0.9))
    return dict(computed=True, hits={int(k): round(v, 12) for k, v in kept.items()},
                truncated=truncated, enemyHp=hp, hpCap=hp_cap, hitCap=max_hits,
                convergedMass=round(sum(kept.values()), 12),
                approximateExpectedHits=approximate, summary=summary,
                approximation='reduced-model hits-to-kill; branches below 1e-14 pruned; not a whole-battle prediction')


def skill_selection_profile(prepared_unit, mp_available, skill_rows=_SKILL_ROWS):
    """Exact §3.1-§3.3 cascade with the authoritative MP filter applied.

    The eligibility filter is `combat_skill_selection.active_skill_infos` with the
    recovered `skill_mp_cost` (so MP excludes unaffordable skills) and the
    invocation level comes from the *filtered* ordinal, matching native. `count` is
    the command hit count only; the landed-damage model is per-hit and lives in
    `damage_distribution`/`ttk_profile`, never here.
    """
    rows = [skill_rows[sid] for sid in prepared_unit['skills']]
    training = prepared_unit['averageTrainingLevel']
    monster = bool(prepared_unit['monster'])

    def cost_of(skill):
        return skill_mp_cost(skill['minMp'], skill['maxMp'], training, monster=monster)

    infos = active_skill_infos(rows, prepared_unit['invocationLevels'], mp_available, cost_of)
    survival = 1.0
    entries = []
    expected_command_hits = 0.0
    expected_mp = 0.0
    for ordinal, (skill, level) in enumerate(infos):
        rate = skill_invocation_rate(skill['type'], level) / 100.0
        cost = cost_of(skill)
        probability = survival * rate
        expected_command_hits += probability * skill['count']
        expected_mp += probability * cost
        entries.append(dict(ordinal=ordinal, skillId=skill['id'], type=skill['type'],
                            category=skill['category'], count=skill['count'],
                            commandHitCount=skill['count'],
                            invocationLevel=level, invocationRate=rate,
                            mpCost=cost, selectProbability=round(probability, 12)))
        survival *= (1 - rate)
    normal_probability = survival
    expected_command_hits += normal_probability
    eligible_ids = {skill['id'] for skill, _ in infos}
    unavailable = [dict(skillId=skill['id'], mpCost=cost_of(skill), availableMp=mp_available,
                        reason='mp-insufficient')
                   for skill in rows
                   if skill['id'] not in eligible_ids and skill['flags'] & 8
                   and not skill['flags'] & 32 and mp_available < cost_of(skill)]
    return dict(
        eligible=entries,
        normalAttackProbability=round(normal_probability, 12),
        expectedCommandHitsPerAction=round(expected_command_hits, 6),
        # Retained alias for consumers written before the split; it is the command
        # hit count per action, never landed damage.
        expectedHitsPerAction=round(expected_command_hits, 6),
        expectedMpPerAction=round(expected_mp, 6),
        commandHitSemantics='count is the command hit count; the per-command target/skill '
                            'branch and land/miss are decided elsewhere',
        landedDamageModel='not modelled here; use damage_distribution / ttk_profile per landed hit',
        unavailableRoutes=unavailable,
        reducedModel=True)


def solve_attack_for_expected_damage(target, luck, defense, lo=None, hi=None, context='synthetic',
                                     dexterity=None, agility=None, defender_luck=None):
    """Bounded inverse: smallest ATK whose expected damage per *attempt* reaches `target`.

    Expected damage per attempt includes the miss probability, and a critical hit
    bypasses the non-critical hit check, so
    ``E = pCrit*E[crit] + (1-pCrit)*pHit*E[normal]``.
    When `dexterity`, the target's `agility` and `defender_luck` are supplied the
    authoritative `hit_rate` gives ``pHit``; otherwise the inverse assumes every attempt
    lands and says so rather than silently dropping miss. Bounds default to the search
    contract's permitted synthetic ATT interval; `player`/`unrestricted` needs explicit
    bounds.
    """
    span = attack_search_bounds(context, lo, hi)
    lo, hi = span['low'], span['high']
    crit = critical_rate(luck) / 100.0
    hit = None
    if dexterity is not None and agility is not None and defender_luck is not None:
        hit = hit_rate(dexterity, agility, defender_luck) / 100.0

    def expected(attack):
        landed = 1.0 if hit is None else hit
        return (crit * mean_damage(attack, defense, True)
                + (1 - crit) * landed * mean_damage(attack, defense, False))
    if expected(hi) < target:
        return dict(attack=hi, reached=False, expectedDamage=round(expected(hi), 6), bounds=span,
                    includesMiss=hit is not None, hitProbability=hit)
    left, right = lo, hi
    while left < right:
        mid = (left + right) // 2
        if expected(mid) >= target:
            right = mid
        else:
            left = mid + 1
    return dict(attack=left, reached=True, expectedDamage=round(expected(left), 6), bounds=span,
                includesMiss=hit is not None, hitProbability=hit)


def _attack_fallback_boundaries(enemy_roster, low, high):
    """Integer ATK values where the base damage branch changes for each victim.

    The base law falls back to 1..10 whenever the minimum-roll scaled attack no longer
    exceeds the maximum-roll armour subtraction, and the critical branch halves armour.
    Those crossings are mechanical ATK integers, so candidate neighbours are the actual
    damage boundaries instead of an arbitrary percentage ladder.
    """
    boundaries = set()
    for victim in enemy_roster:
        defense = int(victim.get('defense') or 0)
        for armor in (i32(defense), trunc_div(i32(defense), 8)):
            need = trunc_div(i32(armor) * 90, 100) + 5
            crossing = ((need + 1) * 100 + 79) // 80
            for value in (crossing - 1, crossing, crossing + 1):
                if low <= value <= high:
                    boundaries.add(int(value))
    return sorted(boundaries)


def solve_attack_profile(target_profiles, luck, dex, enemy_roster, bounds):
    """Soft bounded inverse over boss+followers with *normalized* quantile residuals.

    `target_profiles` maps an enemy name (or roster index) to a target dict with any of
    `expectedHitsToKill`, `damageP10`, `damageMedian`, `damageP90` and `hitProbability`.
    `bounds` is `{'attack': [low, high]}` or `[low, high]`. For each target one
    bounded inverse seeds candidate ATT values: the integer neighbours of the seed plus
    the mechanical fallback-branch boundaries (never a percentage ladder). Each candidate
    is scored by normalized quantile residuals ``|actual-wanted| / max(1, |wanted|)``
    across boss and followers. Results are soft: residuals are reported, never raised,
    unmatched or HP-less profiles are reported rather than rejected, one seed per target
    keeps it O(targets) rather than a Cartesian product, and no whole-battle call is made.
    """
    if isinstance(bounds, dict):
        low, high = bounds['attack']
    else:
        low, high = bounds
    span = attack_search_bounds('caller', int(low), int(high))
    low, high = span['low'], span['high']
    boundaries = _attack_fallback_boundaries(list(enemy_roster), low, high)

    def candidate_attacks(value):
        candidates = {min(high, max(low, value + offset)) for offset in (-1, 0, 1)}
        nearest = sorted(boundaries, key=lambda boundary: abs(boundary - value))[:ATTACK_CANDIDATE_CAP]
        candidates.update(nearest)
        return sorted(candidates)

    def normalized(actual, wanted):
        if actual is None or wanted is None:
            return None
        return abs(actual - wanted) / max(1.0, abs(wanted))

    enemies = list(enemy_roster)
    by_key = {}
    for index, enemy in enumerate(enemies):
        by_key[enemy['name']] = enemy
        by_key[index] = enemy
    goals_by_name = {}
    for key, target in target_profiles.items():
        resolved = by_key.get(key)
        if resolved is not None:
            goals_by_name[resolved['name']] = target
    results = []
    evaluated_attacks = {}
    for key, target in target_profiles.items():
        enemy = by_key.get(key)
        if enemy is None:
            results.append(dict(target=key, error='unknown-enemy'))
            continue
        hp = enemy.get('hp')
        wanted_hits = target.get('expectedHitsToKill')
        seed_damage = max(1.0, (hp / wanted_hits) if (hp and wanted_hits) else 1.0)
        seed = solve_attack_for_expected_damage(seed_damage, luck, enemy['defense'],
                                                lo=low, hi=high, context='caller', dexterity=dex,
                                                agility=enemy['speed'], defender_luck=enemy['luck'])
        evaluations = []
        for attack in candidate_attacks(seed['attack']):
            if attack in evaluated_attacks:
                evaluations.append(evaluated_attacks[attack])
                continue
            per_enemy = {}
            residuals_by_enemy = {}
            for victim in enemies:
                goal = goals_by_name.get(victim['name']) or {}
                profile = damage_distribution(attack, victim['defense'], luck, dex,
                                              victim['speed'], victim['luck'])
                pmf = profile['perHitPmf']
                mean = sum(value * weight for value, weight in pmf.items()) / sum(pmf.values())
                pmf_quantiles = pmf_summary(pmf)
                hp_victim = victim.get('hp')
                expected_hits = (None if mean <= 0 or hp_victim is None else hp_victim / mean)
                needs_ttk_quantiles = any(field in goal for field in ('medianHitsToKill', 'p10Hits', 'p90Hits'))
                summary = (ttk_profile(pmf, hp_victim or 1).get('summary')
                           if needs_ttk_quantiles and (hp_victim or 0) <= TTK_HP_CAP else None)
                row = dict(enemy=victim['name'],
                           hitProbability=profile['probabilities']['hit'],
                           missProbability=profile['probabilities']['miss'],
                           expectedDamagePerHit=round(mean, 4),
                           expectedHitsToKill=None if expected_hits is None else round(expected_hits, 4),
                           damageP10=pmf_quantiles['p10'], damageMedian=pmf_quantiles['median'],
                           damageP90=pmf_quantiles['p90'],
                           medianHitsToKill=(summary or {}).get('median'),
                           p10Hits=(summary or {}).get('p10'), p90Hits=(summary or {}).get('p90'))
                field_residuals = []
                for field in ('expectedHitsToKill', 'damageP10', 'damageMedian', 'damageP90',
                              'medianHitsToKill', 'p10Hits', 'p90Hits', 'hitProbability'):
                    value = normalized(row[field], goal.get(field))
                    if value is not None:
                        field_residuals.append(value)
                if field_residuals:
                    residuals_by_enemy[victim['name']] = round(sum(field_residuals) / len(field_residuals), 6)
                per_enemy[victim['name']] = row
            total = (sum(residuals_by_enemy.values()) / len(residuals_by_enemy)
                     if residuals_by_enemy else 0.0)
            boss = next((name for name in residuals_by_enemy
                         if (by_key[name] or {}).get('boss')), None)
            evaluations.append(dict(attack=attack, residual=round(total, 6),
                                    normalizedQuantileResidual=round(total, 6),
                                    perEnemyResidual=residuals_by_enemy,
                                    bossResidual=None if boss is None else residuals_by_enemy[boss],
                                    perEnemy=per_enemy))
            evaluated_attacks[attack] = evaluations[-1]
        evaluations.sort(key=lambda item: item['residual'])
        results.append(dict(target=key, enemy=enemy['name'], enemyHasHp=hp is not None,
                            seedAttack=seed['attack'], inverseReached=seed['reached'],
                            includesMiss=seed['includesMiss'],
                            best=evaluations[0] if evaluations else None,
                            candidates=evaluations))
    return dict(schema='ka-strategy-attack-profile-solve-2', soft=True,
                attackBounds=span, mechanicalBoundaries=boundaries,
                candidatesPerTarget=3 + min(len(boundaries), ATTACK_CANDIDATE_CAP),
                candidateCap=ATTACK_CANDIDATE_CAP, wholeBattleCalls=0,
                residualNormalization='|actual-wanted| / max(1, |wanted|), equal weight per victim',
                results=results,
                note='soft normalized-quantile fit; expected-damage-only matches are not treated as '
                     'sufficient, unmatched profiles are reported not rejected, and no reachability '
                     'is claimed')


# --------------------------------------------------------------------------- #
# Scenario preparation (deterministic; never a battle)
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=256)
def _normalized_cached(key):
    # Validation and pet expansion are deterministic for exact inputs and dependencies. Keep the
    # cached value private: public callers receive their own mutable copy below.
    return combat_scenario.load_scenario(pickle.loads(key[-1]))


def _normalization_key(scenario):
    # Internal in-memory bytes only, never loaded from a file or an external sender. Preserve
    # integer versus string parameter keys and tuple/list types that a JSON round-trip would lose.
    return (MECHANICS_REVISION, CACHE_KEY_REVISION, dependency_digest(),
            pickle.dumps(scenario, protocol=pickle.HIGHEST_PROTOCOL))


def _normalize(scenario):
    return deepcopy(_normalized_cached(_normalization_key(scenario)))


def normalize_scenario(scenario):
    """Canonical, validated scenario (household pets expanded); JSON serializable."""
    return _normalize(scenario)


@lru_cache(maxsize=256)
def _prepared_cached(key):
    return combat_setup.prepare_setup(json.loads(key)['payload'])


def prepared_setup(scenario):
    """Cached `combat_setup.prepare_setup`; the key is the scenario + dependency digest."""
    normalized = dict(_normalized_cached(_normalization_key(scenario)))
    normalized.pop('housePets', None)
    return _prepared_cached(_cache_key('prepared', normalized))


def _param(params, pid, default=None):
    entry = params.get(pid, params.get(str(pid)))
    if entry is None:
        return default
    if isinstance(entry, dict):
        for field in ('rawValue', 'value'):
            if field in entry:
                return entry[field]
    return entry


def _own_effective(prepared_member, pid):
    entry = prepared_member['effectiveParameters'].get(pid)
    return None if entry is None else entry['value']


def classify_role(skill_ids, skill_rows=_SKILL_ROWS):
    """Recovered-semantics role, never by name (§25.1).

    A unit that carries Counter *and* an ordinary active is classified by the
    active it actually attacks with; only an all-reactive kit is `reactive`.
    """
    rows = [skill_rows[sid] for sid in skill_ids if sid in skill_rows]
    if any(row['category'] == 1 for row in rows):
        return 'healer'
    actives = [row for row in rows if row['flags'] & 8 and not row['flags'] & 32]
    if any(row['type'] == 22 for row in rows):
        return 'evasion'
    if any(row['type'] == 1 for row in rows):
        return 'magic-attack'
    if any(row['type'] in (26, 27) for row in rows):
        return 'area-attack'
    if actives:
        return 'ordinary-attack'
    if any(row['flags'] & 32 for row in rows):
        return 'reactive'
    return 'ordinary-attack'


def _enemy_view(fighter, index):
    params = fighter['parameters']
    return dict(fighterIndex=index, name=fighter['name'], boss=bool(fighter.get('leaderIdentity')),
                hp=_param(params, PID_HP), mp=_param(params, PID_MP),
                attack=_param(params, PID_ATK), defense=_param(params, PID_DEF, fighter.get('effectiveDefense')),
                speed=_param(params, PID_SPD), luck=_param(params, PID_LUK),
                magic=_param(params, PID_MAG), dexterity=_param(params, PID_DEX),
                skillIds=list(fighter.get('skills', {}).get('dataIds', [])))


def encounter_revision(prepared):
    """Digest of the actual compiled enemy roster + encounter revision inputs."""
    encounter = prepared['encounter']
    roster = []
    for fighter in encounter['fighters']:
        params = fighter['parameters']
        roster.append([fighter['name'], bool(fighter.get('leaderIdentity')),
                       [_param(params, pid) for pid in
                        (PID_HP, PID_MP, PID_ATK, PID_DEF, PID_SPD, PID_LUK, PID_DEX)],
                       list(fighter.get('skills', {}).get('dataIds', []))])
    payload = dict(id=encounter['encounterId'], defeatCount=encounter['defeatCount'],
                   level=encounter['level'], terrain=encounter.get('terrain'),
                   roster=roster, mechanicsRevision=MECHANICS_REVISION)
    digest = hashlib.sha256(canonical(payload).encode('utf-8')).hexdigest()
    return dict(digest=digest, digestRevision=_ENCOUNTER_DIGEST_REVISION,
                encounterId=encounter['encounterId'], defeatCount=encounter['defeatCount'],
                level=encounter['level'], rosterDigest=digest)


def _consumable_policy(normalized):
    return dict(holyHerbStock=normalized.get('holyHerbStock', 0),
                holyHerbMaxUses=normalized.get('holyHerbMaxUses', 0),
                holyHerbTriggerUnits=list(normalized.get('holyHerbTriggerUnits') or []),
                mpWatchUnits=list(normalized.get('mpWatchUnits') or []),
                itemStock=dict(normalized.get('itemStock', {})),
                inputs=list(normalized.get('inputs', [])))


@lru_cache(maxsize=256)
def _profile_cached(key):
    scenario = json.loads(key)['payload']
    prepared = prepared_setup(scenario)
    normalized = _normalize(scenario)
    # Scenario unit names are unique (`load_scenario`); the name link recovers the
    # source skills for a formation-ordered prepared member. Indices are carried on
    # every unit/enemy so features never key on a name alone.
    source_by_name = {unit['name']: unit for unit in normalized['ownUnits']}
    enemies = [_enemy_view(f, index) for index, f in enumerate(prepared['encounter']['fighters'])]
    enemy_intervals = [dict(fighterIndex=enemy['fighterIndex'], enemy=enemy['name'],
                            interval=attack_interval(enemy['speed'])) for enemy in enemies]
    interval_by_index = {row['fighterIndex']: row['interval'] for row in enemy_intervals}
    units = []
    for unit_index, member in enumerate(prepared['ownUnits']):
        attack = _own_effective(member, PID_ATK)
        magic_attack = _own_effective(member, PID_MAG)
        defense = _own_effective(member, PID_DEF)
        speed = _own_effective(member, PID_SPD)
        luck = _own_effective(member, PID_LUK)
        dexterity = _own_effective(member, PID_DEX)
        hp = _own_effective(member, PID_HP)
        mp = _own_effective(member, PID_MP)
        source = source_by_name.get(member['name'], {})
        skill_ids = list(source.get('skills', []))
        unit_view = dict(member)
        unit_view['skills'] = skill_ids
        unit_view['invocationLevels'] = list(source.get('invocationLevels', []))
        outgoing = []
        incoming = []
        hit_classes = []
        for enemy in enemies:
            distribution = damage_distribution(
                attack, enemy['defense'], luck, dexterity, enemy['speed'], enemy['luck'])
            hp_target = enemy['hp'] or 1
            outgoing.append(dict(enemyIndex=enemy['fighterIndex'], enemy=enemy['name'], boss=enemy['boss'],
                                 damageModel=distribution['modelKind'],
                                 completeUnitCombatPmf=distribution['completeUnitCombatPmf'],
                                 normalPmf=distribution['normalPmf'],
                                 critPmf=distribution['critPmf'],
                                 perHitPmf=distribution['perHitPmf'],
                                 probabilities=distribution['probabilities'],
                                 normalSummary=distribution['normalSummary'],
                                 critSummary=distribution['critSummary'],
                                 perHitSummary=distribution['perHitSummary'],
                                 ttk=ttk_profile(distribution['perHitPmf'], hp_target)))
            incoming_rate = hit_rate(enemy['dexterity'], speed, luck)
            incoming.append(dict(enemyIndex=enemy['fighterIndex'], enemy=enemy['name'], boss=enemy['boss'],
                                 hitRate=incoming_rate, enemyDexterity=enemy['dexterity'],
                                 playerSpeed=speed, playerLuck=luck,
                                 enemyInterval=interval_by_index[enemy['fighterIndex']]))
            hit_classes.append(incoming_rate)
        timing = action_timing(speed)
        # The authoritative MP filter runs on the unit's actual battle MP (the
        # pool the fight starts at), so unaffordable skills are excluded.
        unit_mp = skill_selection_profile(unit_view, max(mp, 0))
        units.append(dict(
            unitIndex=unit_index, name=member['name'], role=classify_role(skill_ids),
            effectiveStats=dict(hp=hp, mp=mp, attack=attack, magicAttack=magic_attack,
                                defense=defense, speed=speed, luck=luck, dexterity=dexterity),
            critProfile=crit_profile(luck),
            outgoing=outgoing,
            incomingAccuracy=dict(perEnemy=incoming,
                                  minHitClass=min(hit_classes) if hit_classes else None,
                                  maxHitClass=max(hit_classes) if hit_classes else None),
            speed=dict(interval=timing['interval'], period=timing['period'],
                       periodSemantics=timing['periodSemantics'],
                       relativeToEnemies=[dict(fighterIndex=row['fighterIndex'], enemy=row['enemy'],
                                               interval=row['interval'],
                                               playerFaster=timing['interval'] <= row['interval'])
                                          for row in enemy_intervals]),
            skills=dict(eligible=unit_mp['eligible'],
                        normalAttackProbability=unit_mp['normalAttackProbability'],
                        expectedCommandHitsPerAction=unit_mp['expectedCommandHitsPerAction'],
                        expectedHitsPerAction=unit_mp['expectedHitsPerAction'],
                        commandHitSemantics=unit_mp['commandHitSemantics'],
                        landedDamageModel=unit_mp['landedDamageModel'],
                        invocationIndexedProbabilities={
                            entry['ordinal']: entry['invocationRate'] for entry in unit_mp['eligible']},
                        unavailableRoutes=unit_mp['unavailableRoutes']),
            mpDependencies=dict(expectedMpPerAction=unit_mp['expectedMpPerAction'],
                                averageTrainingLevel=member['averageTrainingLevel'],
                                skillCosts=member['skillCosts']),
            monster=bool(member['monster']),
        ))
    return dict(
        schema='ka-strategy-mechanics-profile-1', mechanicsRevision=MECHANICS_REVISION,
        dependencyDigest=dependency_digest(),
        encounterRevision=encounter_revision(prepared),
        encounter=dict(encounterId=prepared['encounter']['encounterId'],
                       title=prepared['encounter'].get('title'),
                       defeatCount=prepared['encounter']['defeatCount'],
                       level=prepared['encounter']['level'],
                       order=prepared['encounter']['formationOrder'],
                       enemies=enemies),
        ownUnits=units,
        formation=dict(ownOrder=prepared['ownFormationOrder'],
                       enemyOrder=prepared['encounter']['formationOrder'],
                       targetingRule='nearest valid candidate; command target chosen once at enqueue'),
        consumables=_consumable_policy(normalized),
        reducedModel=True,
        approximations=[
            'TTK is a reduced-model convolution capped at %d hits; it is not a whole-battle prediction.' % TTK_HIT_CAP,
            'Per-hit damage is the base attack/defense law with the critical half-armour branch only: '
            'invoking skill/weapon-type/affinity/status modifiers are NOT applied, so this is not a complete per-unit combat PMF.',
            'The period (interval+21) is an uninterrupted normal-attack reduced path, not universal actual action cadence.',
            'Skill count is a command hit count; landed damage is modelled separately and is not a whole-battle PMF.',
            'Incoming accuracy is the static hit_rate; invoking type-22 windows are not applied without a live state.',
            'No simulator run, seed, or telemetry is consumed here.'],
    )


def profile(scenario):
    """Deterministic per-own-unit mechanics profile for one scenario (cached)."""
    normalized = dict(_normalized_cached(_normalization_key(scenario)))
    normalized.pop('housePets', None)
    return _profile_cached(_cache_key('profile', normalized))


def cache_info():
    return dict(profile=_profile_cached.cache_info()._asdict(),
                prepared=_prepared_cached.cache_info()._asdict(),
                dependencyDigest=dependency_digest(), speedDomain=_SPEED_DOMAIN)





