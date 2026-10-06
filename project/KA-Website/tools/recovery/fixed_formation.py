"""The FIXED-FORMATION search policy for the optimiser.

Latest explicit user scope (2026-10-06) supersedes the historical formation/search rules. This module
is the single reference implementation of that policy in the original Python engine:

  * exactly six units, fixed roster order, fixed canonical placement roles
        dps, fodder x4, healer  (the confirmed mapping for the six logical slots);
  * the ONLY search variables are the Synthetic DPS's raw stats, inside the derived synthetic walls;
  * the DPS is a neutral synthetic unit: no equipment, no job/awakening/class identity;
  * DPS skills, in exact order: Counter, 7-Hit, 5-Hit, 4-Hit, 3-Hit, 2-Hit (High trigger level 0);
  * healer skills, in exact order: Heal Maddy (High), Backup;
  * healer and fodder stats/skills/positions never mutate (four identical fodders).

The canonical template and the walls come from ``fixed-formation-policy.json`` (built by
``tools/fixed_formation_build_policy.py`` from the authoritative supplied cast and the derived stat
bounds). Nothing here invents a number: a missing policy file is a hard stop.

Admission (``check``) rejects any scenario that is not the fixed template, so historical libraries
cannot silently satisfy the new policy; identity (``search_identity``) is defined over only the
searched dimensions plus the fight identity, so a permutation of the identical fodders is not a new
strategy and cannot proliferate candidates.
"""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import search_contract as contract

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
POLICY_PATH = ROOT / "coordination" / "fixed-formation-20261006" / "fixed-formation-policy.json"

PARAMETERS = (10, 11, 12, 13, 14, 15, 16, 18, 19, 20, 21, 22)
DPS_INDEX = 0
HEALER_INDEX = 1
FODDER_INDICES = (2, 3, 4, 5)
PLACEMENT_ROLES = ("dps", "fodder", "fodder", "fodder", "fodder", "healer")


class FixedFormationError(ValueError):
    """A candidate the fixed-formation policy refuses, with the reason."""


@lru_cache(maxsize=1)
def policy():
    try:
        document = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise FixedFormationError(f"fixed-formation policy is unavailable ({error})") from error
    if document.get("schema") != "ka-fixed-formation-policy-1":
        raise FixedFormationError("fixed-formation policy has an unexpected schema")
    payload = dict(document)
    payload.pop("policyHash", None)
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    if digest != document.get("policyHash"):
        raise FixedFormationError("fixed-formation policy hash does not match its content")
    return document


def policy_hash():
    return policy()["policyHash"]


#: Opt-in activation for the original Python optimizer. Disabled by default so the historical search
#: space and stored candidates are untouched; when enabled, `strategy_admission_preparation.prepare`
#: and `strategy_optimizer.Store.add_child` refuse any candidate the fixed policy does not admit.
_ENABLED = False


def enabled():
    return _ENABLED


def set_enabled(value):
    """Turn fixed-formation enforcement on/off; returns the new state."""
    global _ENABLED
    _ENABLED = bool(value)
    return _ENABLED


def gate(scenario):
    """Return None when enforcement is off or the scenario is admissible, else its refusal reasons."""
    if not _ENABLED:
        return None
    admitted, reasons = admits(scenario)
    return None if admitted else list(reasons)


def searchable_parameters():
    """{parameter id: (minimum, maximum)} - the DPS raw-stat walls the search may vary."""
    return {int(pid): (int(lo), int(hi)) for pid, (lo, hi) in policy()["dps"]["searchableParameters"].items()}


_SEARCHABLE_NAMES = {"hp": 10, "mp": 11, "atk": 13, "def": 14, "spd": 15, "lck": 16, "dex": 19}


def searchable_stats():
    """{stat name: parameter id} for the seven DPS stats the policy lets the search vary."""
    return dict(_SEARCHABLE_NAMES)


def _parameter_entry(unit, parameter_id):
    parameters = unit.get("parameters") or {}
    if str(parameter_id) in parameters:
        return parameters[str(parameter_id)]
    return parameters.get(parameter_id)


def _set_searchable(entry, parameter_id, target):
    if parameter_id in contract.BOUNDED_STATS:
        entry["rawValue"] = int(target)
        entry["rawMax"] = int(target)
        entry["extraValue"] = 0
        entry["extraMax"] = 0
    else:
        entry["rawValue"] = int(target)
        entry["extraValue"] = 0


def _dps_unit(encounter_id):
    document = policy()
    template = document["dps"]["defaults"][str(int(encounter_id))]
    return dict(
        name=document["dps"]["name"],
        human=True,
        monsterId=None,
        leaderIdentity=False,
        visitor=False,
        weaponId=int(document["dps"]["weaponId"]),
        equipment=deepcopy(document["dps"]["equipment"]),
        skills=list(document["dps"]["skills"]),
        invocationLevels=list(document["dps"]["invocationLevels"]),
        parameters={pid: dict(block) for pid, block in template.items()},
    )


def _fixed_unit(block, name=None):
    unit = dict(
        human=True,
        monsterId=None,
        leaderIdentity=False,
        visitor=False,
        weaponId=int(block["weaponId"]),
        equipment=deepcopy(block["equipment"]),
        skills=list(block["skills"]),
        invocationLevels=list(block["invocationLevels"]),
        parameters={pid: dict(value) for pid, value in block["parameters"].items()},
    )
    unit["name"] = name if name is not None else block.get("name")
    return unit


def canonical_scenario(encounter_id, *, math_seed=0, lib_seed=0, defeat_count=0, tick_limit=30000,
                       holy_herb_stock=0, inputs=None, schema="ka-special-combat-research-1",
                       start_profile=None, mp_watch_units=None):
    """The one fixed scenario the policy admits for an encounter, before any DPS-stat change."""
    document = policy()
    units = [_dps_unit(encounter_id), _fixed_unit(document["healer"])]
    for name in document["fodder"]["names"]:
        units.append(_fixed_unit(document["fodder"], name=name))
    return dict(
        schema=schema,
        encounterId=int(encounter_id),
        defeatCount=int(defeat_count),
        mathSeed=int(math_seed),
        libSeed=int(lib_seed),
        tickLimit=int(tick_limit),
        holyHerbStock=int(holy_herb_stock),
        finishPolicy="on-verdict",
        inputs=list(inputs or []),
        mpWatchUnits=list(mp_watch_units or [document["dps"]["name"], document["healer"]["name"]]),
        startProfile=dict(start_profile or {"bossCell": None, "enemySpawnCell": [0, 0],
                                            "kind": "isolated-scene0", "startingStatus": {}}),
        note="Synthetic DPS fixed formation; only DPS raw stats are searched.",
        ownUnits=units,
    )


def _same_parameter(actual, expected):
    return (int(actual.get("rawValue", 0)) == int(expected.get("rawValue", 0))
            and int(actual.get("rawMax", 0)) == int(expected.get("rawMax", 0))
            and int(actual.get("extraValue", 0)) == int(expected.get("extraValue", 0))
            and int(actual.get("extraMax", 0)) == int(expected.get("extraMax", 0)))


def check(scenario):
    """Raise `FixedFormationError` unless `scenario` is a legal point of the fixed search space."""
    document = policy()
    units = scenario.get("ownUnits") or []
    if len(units) != 6:
        raise FixedFormationError(f"fixed formation needs exactly six units, found {len(units)}")
    expected_names = list(document["fixed"]["rosterOrder"])
    names = [str(u.get("name")) for u in units]
    if names != expected_names:
        raise FixedFormationError(f"roster order must be {expected_names}, found {names}")
    bounds = searchable_parameters()

    # Synthetic DPS - the only unit whose raw stats may differ from the template.
    dps = units[DPS_INDEX]
    template = document["dps"]["defaults"]
    if int(scenario["encounterId"]) not in {int(key) for key in template}:
        raise FixedFormationError(f"no fixed-formation template for encounter {scenario.get('encounterId')}")
    default_params = template[str(int(scenario["encounterId"]))]
    for field, want in (("human", True), ("monsterId", None), ("leaderIdentity", False)):
        if dps.get(field) != want:
            raise FixedFormationError(f"DPS {field} must be {want!r}")
    if int(dps.get("weaponId") or 0) != int(document["dps"]["weaponId"]):
        raise FixedFormationError("DPS must carry no weapon (weaponId 0)")
    if dps.get("equipment"):
        raise FixedFormationError("DPS must carry no equipment")
    if list(dps.get("skills") or []) != list(document["dps"]["skills"]):
        raise FixedFormationError(f"DPS skills must be exactly {document['dps']['skills']}")
    if list(dps.get("invocationLevels") or []) != list(document["dps"]["invocationLevels"]):
        raise FixedFormationError("DPS triggers must be the High trigger (level 0) on every slot")
    if sorted(int(pid) for pid in (dps.get("parameters") or {})) != list(PARAMETERS):
        raise FixedFormationError("DPS parameter blocks must be exactly the canonical twelve")
    for pid in PARAMETERS:
        entry = _parameter_entry(dps, pid)
        if pid in bounds:
            if int(entry.get("extraValue", 0)) != 0 or int(entry.get("extraMax", 0)) != 0:
                raise FixedFormationError(f"DPS parameter {pid} must have zero extra (no equipment)")
            value = int(contract.battle_value(scenario, dps, pid))
            low, high = bounds[pid]
            if not low <= value <= high:
                raise FixedFormationError(f"DPS parameter {pid} value {value} is outside [{low}, {high}]")
        elif not _same_parameter(entry, default_params[str(pid)]):
            raise FixedFormationError(f"DPS parameter {pid} is fixed and must match the template")

    # Healer and the four identical fodders - pinned byte-for-byte to the template.
    for index, block in ((HEALER_INDEX, document["healer"]),):
        _check_fixed_unit(units[index], block, label="healer")
    for index in FODDER_INDICES:
        _check_fixed_unit(units[index], document["fodder"], label=f"fodder {index}")
    first = {k: v for k, v in units[FODDER_INDICES[0]].items() if k != "name"}
    for index in FODDER_INDICES[1:]:
        other = {k: v for k, v in units[index].items() if k != "name"}
        if json.dumps(other, sort_keys=True) != json.dumps(first, sort_keys=True):
            raise FixedFormationError("the four fodders must be identical")
    return True


def _check_fixed_unit(unit, block, *, label):
    for field, want in (("human", True), ("monsterId", None), ("leaderIdentity", False)):
        if unit.get(field) != want:
            raise FixedFormationError(f"{label} {field} must be {want!r}")
    if int(unit.get("weaponId") or 0) != int(block["weaponId"]) or unit.get("equipment"):
        raise FixedFormationError(f"{label} must carry no weapon or equipment")
    if list(unit.get("skills") or []) != list(block["skills"]):
        raise FixedFormationError(f"{label} skills do not match the fixed template")
    if list(unit.get("invocationLevels") or []) != list(block["invocationLevels"]):
        raise FixedFormationError(f"{label} triggers do not match the fixed template")
    if sorted(int(pid) for pid in (unit.get("parameters") or {})) != list(PARAMETERS):
        raise FixedFormationError(f"{label} parameter blocks do not match the fixed template")
    for pid in PARAMETERS:
        if not _same_parameter(_parameter_entry(unit, pid), block["parameters"][str(pid)]):
            raise FixedFormationError(f"{label} parameter {pid} is fixed and must not mutate")


def admits(scenario):
    try:
        check(scenario)
        return True, []
    except FixedFormationError as error:
        return False, [str(error)]


def dossier(scenario):
    """The seven searched values, in the fixed order, for labels and evidence."""
    bounds = searchable_parameters()
    dps = scenario["ownUnits"][DPS_INDEX]
    return {name: int(contract.battle_value(scenario, dps, pid))
            for name, pid in sorted(_SEARCHABLE_NAMES.items(), key=lambda item: item[1])}


def search_identity(scenario):
    """Canonical identity over only the searched dimensions plus the fight identity.

    Everything else (names, fodder order, skill lists, equipment, non-searched stats) is pinned by
    admission, so it is deliberately absent: two scenarios with the same identity are the same
    search-space point and must never both be simulated.
    """
    payload = dict(
        encounterId=int(scenario["encounterId"]),
        defeatCount=int(scenario.get("defeatCount") or 0),
        tickLimit=int(scenario.get("tickLimit") or 0),
        holyHerbStock=int(scenario.get("holyHerbStock") or 0),
        inputs=scenario.get("inputs") or [],
        dpsStats={str(pid): int(contract.battle_value(scenario, scenario["ownUnits"][DPS_INDEX], pid))
                  for pid in sorted(_SEARCHABLE_NAMES.values())},
    )
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _targets(current, low, high, rng, steps=5):
    factors = (1.0, 1.15, 1.35, 1.6, 0.85, 0.7)
    targets = []
    for factor in factors:
        target = max(low, min(high, int(round(current * factor))))
        if target != current and target not in targets:
            targets.append(target)
    if low != current and low not in targets:
        targets.append(low)
    if high != current and high not in targets:
        targets.append(high)
    rng.shuffle(targets)
    return targets[:steps]


def mutate_stat(scenario, seed, stat=None, value=None, _random=None):
    """A child that changes exactly one Synthetic DPS raw stat, still inside the walls."""
    import random
    rng = _random or random.Random(seed)
    check(scenario)
    bounds = searchable_parameters()
    names = {name: pid for name, pid in _SEARCHABLE_NAMES.items()}
    if stat is not None and stat not in names:
        raise FixedFormationError(f"{stat!r} is not a searchable DPS stat")
    candidates = [stat] if stat is not None else sorted(names)
    rng.shuffle(candidates)
    dps = scenario["ownUnits"][DPS_INDEX]
    for name in candidates:
        pid = names[name]
        current = int(contract.battle_value(scenario, dps, pid))
        low, high = bounds[pid]
        targets = [int(value)] if value is not None else _targets(current, low, high, rng)
        for target in targets:
            if target == current or not low <= target <= high:
                continue
            child = deepcopy(scenario)
            entry = _parameter_entry(child["ownUnits"][DPS_INDEX], pid)
            _set_searchable(entry, pid, target)
            try:
                check(child)
            except FixedFormationError:
                continue
            if search_identity(child) == search_identity(scenario):
                continue
            return child
    raise FixedFormationError("no legal DPS stat step inside the fixed walls")
