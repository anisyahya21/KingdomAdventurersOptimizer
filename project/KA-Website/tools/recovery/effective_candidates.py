"""Bounded *effective-stat* candidate generation for the Wairo chest-farm search.

This is generation, not combat logic. It never predicts a fight and never invents a raw stat:
every candidate is a legal saved loadout (``SavedLoadout`` shape: jobName/rank/statLevels/equipment/
skills) whose *displayed* effective stats are computed by the same owners the canonical converter
uses:

  * the shared job curve (``api-server/data/ka_shared.json`` jobs[].ranks[].stats) at a saved
    per-stat training level, ``round(base + (level-1)*inc)``, capped at the rank ``maxLevel``
    plus ``30 * awakening`` (the jobs-marriage rule the user-reference worker recovered);
  * the native equipment owner ``combat_parameters.equipment_contribution`` (affinity- and
    integer-aware) driven through ``combat_parameters.fighter_parameter`` with the canonical
    ``rawMax`` convention (HP(10)/MP(11) bounded at rawValue and read at maximum, every other
    parameter the 2147483647 uncapped sentinel) so equipment contributes exactly as the fixed
    converter produces.

The generator *solves for the training level* that lands each desired effective stat on a target
(nearest legal level, integer rounding, curve cap) so the caller states a stat *profile* - "kill the
boss while keeping Speed low enough to preserve the Leaving re-entries, with enough DEF/HP/MP to
survive" - instead of matching training levels across different growth curves.

Output is the legal-setup JSON the import side consumes; ``loadouts`` is the exact ``SavedLoadout[]``
the canonical converter (``battleSetupFromLoadouts``) takes. Missing skills, equipment levels and
affinities are caller-declared inputs, never guessed here.
"""
import json
import pathlib
import sys

from combat_parameters import fighter_parameter, equipment_contribution
from combat_runtime_data import load_data

HERE = pathlib.Path(__file__).resolve().parent
RELATIVE_SHARED = pathlib.Path("KA-Website/artifacts/api-server/data/ka_shared.json")


def _find_shared(path=None):
    """Locate the shared owner table from either checkout location (tools/recovery or api runtime)."""
    if path:
        return pathlib.Path(path)
    for base in [HERE, *HERE.parents]:
        candidate = base / RELATIVE_SHARED
        if candidate.is_file():
            return candidate
    return HERE.parents[2] / RELATIVE_SHARED


SHARED_PATH = _find_shared()

UNBOUNDED_PARAMETER_MAX = 2147483647
BOUNDED_PARAMETER_IDS = (10, 11)
AWAKENING_MAX_LEVEL_STEP = 30
DEFAULT_CURVE_MAX_LEVEL = 300

STAT_TO_PARAM = {"hp": 10, "mp": 11, "vig": 12, "atk": 13, "def": 14, "spd": 15,
                 "lck": 16, "int": 18, "dex": 19, "gth": 20, "mov": 21, "hrt": 22}
PARAM_TO_STAT = {v: k for k, v in STAT_TO_PARAM.items()}
STAT_KEY_OF_RAW_NAME = {"HP": "hp", "MP": "mp", "Vigor": "vig", "Attack": "atk", "Defence": "def",
                        "Defense": "def", "Speed": "spd", "Luck": "lck", "Intelligence": "int",
                        "Dexterity": "dex", "Gather": "gth", "Move": "mov", "Heart": "hrt"}

# Supported option bounds (an unbounded/greedy search is never attempted).
BOUNDS = dict(maxCandidates=32, maxJobs=8, maxRanks=5, maxAwakenings=4, maxGearSets=6,
              maxTrainingLevel=600, defaultTrainingLevel=1)
PROFILE_KEYS = ("hpMin", "mpMin", "defMin", "lckMin", "atkMin", "atkMax", "spdMax", "dexMax")

_CACHE = {}


def load_shared(path=None):
    path = pathlib.Path(path or SHARED_PATH)
    key = str(path)
    if key not in _CACHE:
        _CACHE[key] = json.loads(path.read_text(encoding="utf-8"))
    return _CACHE[key]


def equipment_catalog():
    if "equipment" not in _CACHE:
        rows = load_data("weapon-skill-profiles.json")["equipment"]
        _CACHE["equipment"] = {row["name"]: row for row in rows if row.get("name")}
    return _CACHE["equipment"]


def curve_for(shared, job_name, rank):
    """The shared job-curve owner, canonicalised to short stat keys. None if the pair is absent."""
    job = (shared.get("jobs") or {}).get(job_name)
    if not job:
        return None
    rank_row = (job.get("ranks") or {}).get(rank)
    if not rank_row:
        return None
    curve = {}
    for raw_name, entry in (rank_row.get("stats") or {}).items():
        key = STAT_KEY_OF_RAW_NAME.get(raw_name)
        if key:
            curve[key] = entry
    return curve or None


def _gear_rows(gear, affinity_of):
    """gear: [{name, level, affinity?}] -> native equipment rows for the equipment owner."""
    catalog = equipment_catalog()
    rows = []
    for item in gear or []:
        row = catalog.get(item.get("name"))
        if row is None:
            raise KeyError(f"equipment {item.get('name')!r} is not in the recovered combat catalog")
        affinity = item.get("affinity")
        if affinity is None and affinity_of is not None:
            affinity = affinity_of(item["name"])
        rows.append(dict(row, level=int(item.get("level", 1)),
                         affinity=1 if affinity is None else affinity))
    return rows


def _contribution(row, parameter_id, level):
    return equipment_contribution(row, parameter_id, level, affinity=row["affinity"], human=True)


def _parameter(param_id, raw_value, training_level):
    bounded = param_id in BOUNDED_PARAMETER_IDS
    return dict(rawValue=raw_value, rawMax=raw_value if bounded else UNBOUNDED_PARAMETER_MAX,
                extraValue=0, extraMax=0, trainingLevel=int(training_level))


def parameter_value(param_id, raw_value, rows, training_level=1):
    """The displayed effective value: HP/MP are read at their (bounded) maximum, others on value."""
    parameter = _parameter(param_id, raw_value, training_level)
    return fighter_parameter(param_id, parameter, rows, _contribution, human=True, ally=True,
                             maximum=param_id in BOUNDED_PARAMETER_IDS)


def effective_stats(job_name, rank, awakening, stat_levels, gear=None, shared=None, affinity_of=None):
    """Owner-computed effective stats for one loadout. Raises on an absent job/rank pair."""
    shared = shared or load_shared()
    curve = curve_for(shared, job_name, rank)
    if curve is None:
        raise KeyError(f"job {job_name!r} rank {rank!r} is not in the shared job table")
    rows = _gear_rows(gear, affinity_of)
    out = {}
    for key, param_id in STAT_TO_PARAM.items():
        entry = curve.get(key)
        if entry is None:
            continue
        max_level = int(entry.get("maxLevel") or DEFAULT_CURVE_MAX_LEVEL) + AWAKENING_MAX_LEVEL_STEP * int(awakening)
        level = max(1, min(int(stat_levels.get(key, 1)), max_level))
        raw = int(round(entry.get("base", 0) + (level - 1) * entry.get("inc", 0)))
        value = parameter_value(param_id, raw, rows, level)
        out[key] = dict(level=level, maxLevel=max_level, rawValue=raw, value=value,
                        gearContribution=value - raw)
    return out


def nearest_training_level(job_name, rank, awakening, key, target, gear=None,
                           shared=None, affinity_of=None):
    """Nearest legal training level whose owner-computed effective value hits `target`.

    The equipment contribution is level/affinity aware and constant in the training level, so the
    raw value is solved directly and then snapped to the nearest legal curve level."""
    shared = shared or load_shared()
    curve = curve_for(shared, job_name, rank)
    if curve is None:
        raise KeyError(f"job {job_name!r} rank {rank!r} is not in the shared job table")
    entry = curve.get(key)
    if entry is None:
        return None
    param_id = STAT_TO_PARAM[key]
    rows = _gear_rows(gear, affinity_of)
    max_level = int(entry.get("maxLevel") or DEFAULT_CURVE_MAX_LEVEL) + AWAKENING_MAX_LEVEL_STEP * int(awakening)
    gear_only = parameter_value(param_id, 0, rows, 1)
    inc = entry.get("inc", 0) or 0
    raw_needed = target - gear_only
    if inc <= 0:
        candidates = [1, max_level]
    else:
        raw_level = 1 + (raw_needed - entry.get("base", 0)) / inc
        candidates = [1, max_level, int(raw_level), int(raw_level) + 1, int(round(raw_level)),
                      int(raw_level) + 2]
    best, best_gap, best_value = None, None, None
    for level in sorted({max(1, min(int(c), max_level)) for c in candidates}):
        value = effective_stats(job_name, rank, awakening, {key: level}, gear, shared,
                                affinity_of)[key]["value"]
        gap = abs(value - target)
        if best_gap is None or gap < best_gap or (gap == best_gap and level < best):
            best, best_gap, best_value = level, gap, value
    return dict(level=best, value=best_value, target=target, maxLevel=max_level)


def profile_deficit(effective, profile):
    """Total violation of the declared effective-stat profile (Min/Max suffix -> stat key)."""
    total = 0
    for key, bound in (profile or {}).items():
        stat = key[:-3] if key.endswith(("Min", "Max")) else key
        value = effective.get(stat, {}).get("value", 0)
        if key.endswith("Min"):
            total += max(0, bound - value)
        elif key.endswith("Max"):
            total += max(0, value - bound)
    return total


def generate_effective_candidates(profile, options, shared=None):
    """Bounded generation of legal loadouts that reach an effective-stat profile.

    profile: {hpMin, mpMin, defMin, lckMin, atkMin/atkMax, spdMax, dexMax} - effective-stat bounds
      derived from the actual trace, not from job names.
    options (all bounded by BOUNDS):
      jobs: [{jobName, rank, awakening}] candidate identities to score
      gearSets: [{id, equipment:[{name, level, affinity?}]}] (max maxGearSets)
      skills / invocationLevel: declared attacker inputs, copied through unchanged
      partyTemplate: extra loadouts (healer/fodder) appended after the attacker, copied unchanged
      affinityOf: name -> affinity (declared job/equipment access rule)
      maxCandidates: cap after ranking by (deficit, training cost)
      statKeys: which stats to solve for the profile (default: the profile keys present in the curve)
    """
    shared = shared or load_shared()
    jobs = options.get("jobs") or []
    gear_sets = options.get("gearSets") or [dict(id="none", equipment=[])]
    affinity_of = options.get("affinityOf")
    stat_keys = options.get("statKeys") or sorted({k[:-3] for k in PROFILE_KEYS}, key=PROFILE_KEYS.index)
    stat_keys = [k[:-3] if k.endswith(("Min", "Max")) else k for k in stat_keys]
    max_candidates = int(options.get("maxCandidates", BOUNDS["maxCandidates"]))
    space = sum(1 for j in jobs) * len(gear_sets)
    if space > 4096:
        raise ValueError("generation space exceeds the bounded product of jobs x gearSets")

    candidates, seen = [], set()
    for identity in jobs:
        job_name = identity["jobName"]
        rank = identity.get("rank") or "A"
        awakening = int(identity.get("awakening", 0))
        curve = curve_for(shared, job_name, rank)
        if curve is None:
            continue
        for gear_set in gear_sets:
            gear = gear_set.get("equipment", [])
            levels, gaps = {}, []
            for key in stat_keys:
                bound = None
                for suffix in ("Min", "Max"):
                    if key + suffix in (profile or {}):
                        bound = profile[key + suffix]
                        break
                if bound is None or key not in curve:
                    continue
                solved = nearest_training_level(job_name, rank, awakening, key, bound, gear,
                                                shared, affinity_of)
                if solved is None:
                    continue
                levels[key] = solved["level"]
                gaps.append(dict(stat=key, bound=bound, level=solved["level"], value=solved["value"],
                                 target=solved["target"], gap=abs(solved["value"] - solved["target"])))
            if not levels:
                continue
            effective = effective_stats(job_name, rank, awakening, levels, gear, shared, affinity_of)
            deficit = profile_deficit(effective, profile)
            cid = f"{job_name}-{rank}-aw{awakening}-{gear_set.get('id')}"
            key_tuple = (cid, tuple(sorted(levels.items())))
            if key_tuple in seen:
                continue
            seen.add(key_tuple)
            attacker = dict(id=cid, name=f"{job_name} ({rank} aw{awakening}) {gear_set.get('id')}",
                            jobName=job_name, rank=rank, statLevels={k: effective[k]["level"] for k in effective},
                            equipment=[dict(name=i["name"], level=int(i["level"]),
                                            affinity=i.get("affinity") if i.get("affinity") is not None
                                            else (affinity_of(i["name"]) if affinity_of else 1))
                                       for i in gear],
                            skills=list(options.get("skills") or []))
            loadouts = [attacker] + [dict(l) for l in (options.get("partyTemplate") or [])]
            candidates.append(dict(
                id=cid, jobName=job_name, rank=rank, awakening=awakening, gearSet=gear_set.get("id"),
                deficit=deficit,
                cost=dict(trainingLevelSum=sum(levels.values()),
                          maxTrainingLevel=max(levels.values()) if levels else 0,
                          gearLevelSum=sum(int(i["level"]) for i in gear),
                          consumableBudget="declared separately (baseline = no items)"),
                effective={k: effective[k]["value"] for k in
                           ("hp", "mp", "atk", "def", "spd", "lck", "dex", "int") if k in effective},
                effectiveDetail=effective,
                solved=sorted(gaps, key=lambda g: (-g["gap"], g["stat"])),
                loadouts=loadouts,
                assumptions=[
                    "statLevels solve the desired EFFECTIVE stat (curve + gear), not another job's training levels",
                    "effective values come from the shared job curve + native equipment owner (affinity, integer rounding, curve cap)",
                    "skills/invocation and the party template are declared inputs copied through unchanged",
                ]))
    candidates.sort(key=lambda c: (c["deficit"], c["cost"]["trainingLevelSum"], c["id"]))
    truncated = max(0, len(candidates) - max_candidates)
    return dict(schema="ka-effective-candidates-1",
                status="research-only; not a fight prediction",
                claim=("bounded profile-solved generation over declared jobs/ranks/awakenings/gear; "
                       "never an exhaustive search and no global optimum"),
                profile=dict(profile or {}), bounds=dict(BOUNDS, appliedMaxCandidates=max_candidates),
                assumptions=options.get("assumptions") or [], truncatedFrom=truncated,
                candidates=candidates[:max_candidates])


if __name__ == "__main__":
    payload = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
    print(json.dumps(generate_effective_candidates(payload["profile"], payload["options"]), indent=1))
