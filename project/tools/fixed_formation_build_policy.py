"""Build the canonical FIXED-FORMATION policy from authoritative preparation data.

Latest explicit user scope (2026-10-06) supersedes the historical formation/search rules:

  * roster of exactly six units in fixed roster order
        [Synthetic DPS, fixed healer, four identical fixed fodders]
    whose derived canonical placement roles are  dps, fodder x4, healer;
  * the ONLY search variables are the Synthetic DPS's raw stats, inside the
    existing derived synthetic walls (RE-evidence/20260922-search-contract/stat-bounds.json);
  * the DPS carries NO equipment and NO job/awakening identity;
  * DPS skills, in exact order: Counter, 7-Hit, 5-Hit, 4-Hit, 3-Hit, 2-Hit (High trigger);
  * healer skills, in exact order: Heal Maddy (High), Backup;
  * healer and fodder stats/skills/positions never mutate.

The fodder/healer definitions and the per-encounter DPS starting values are READ from the
authoritative supplied cast (coordination/native-preparation/shared/comparison-inputs/bank2-v3/
inputs/all20-candidates.json) rather than invented. The DPS is converted to a neutral synthetic
unit by resolving each parameter through `search_contract.battle_value` (the engine's own
arithmetic) and re-expressing that effective value with no equipment, so behaviour is preserved
and the carrier equipment/class identity is dropped.

Output: coordination/fixed-formation-20261006/fixed-formation-policy.json
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
RECOVERY = ROOT / "KA-Website" / "tools" / "recovery"
sys.path.insert(0, str(RECOVERY))

import search_contract as contract  # noqa: E402

SUPPLIED = ROOT / "coordination" / "native-preparation" / "shared" / "comparison-inputs" / "bank2-v3" / "inputs" / "all20-candidates.json"
OUT = ROOT / "coordination" / "fixed-formation-20261006" / "fixed-formation-policy.json"

DPS_NAME = "Synthetic DPS"
DPS_SKILLS = [26, 110, 25, 24, 23, 22]  # Counter, 7-Hit, 5-Hit, 4-Hit, 3-Hit, 2-Hit
DPS_TRIGGERS = [0, 0, 0, 0, 0, 0]        # invocation level 0 == player-facing "High"
HEALER_SKILLS = [37, 107]                # Heal Maddy, Backup
HEALER_TRIGGERS = [0, 0]
# Bare hands: equipment row 0, empty equipment list (the established skill-less unit shape).
WEAPON_ID = 0
EQUIPMENT = []

# The seven parameters the DPS may vary (INT is not searchable: the DPS carries no magic attack).
SEARCHABLE = ("hp", "mp", "atk", "def", "spd", "lck", "dex")
SEARCHABLE_PARAMETERS = {"hp": 10, "mp": 11, "atk": 13, "def": 14, "spd": 15, "lck": 16, "dex": 19}
# Every parameter block a prepared unit carries, in the order the supplied cast writes them.
PARAMETERS = (10, 11, 12, 13, 14, 15, 16, 18, 19, 20, 21, 22)


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _resolve_defaults(scenario, unit):
    """The unit's parameter blocks rewritten to the no-equipment effective values."""
    resolved = {}
    for pid in unit["parameters"]:
        pid = int(pid)
        effective = int(contract.battle_value(scenario, unit, pid))
        bounded = contract.effective_parameter(scenario, unit, pid, maximum=True)
        entry = dict(unit["parameters"][str(pid)])
        if pid in contract.BOUNDED_STATS:
            entry["rawValue"] = effective
            entry["rawMax"] = effective
            entry["extraValue"] = 0
            entry["extraMax"] = 0
        else:
            entry["rawValue"] = effective
            entry["rawMax"] = 2147483647
            entry["extraValue"] = 0
            entry["extraMax"] = 0
        resolved[str(pid)] = entry
    return resolved


def _synthetic_dps(scenario):
    source = scenario["ownUnits"][0]
    unit = dict(source)
    unit["name"] = DPS_NAME
    unit["weaponId"] = WEAPON_ID
    unit["equipment"] = list(EQUIPMENT)
    unit["skills"] = list(DPS_SKILLS)
    unit["invocationLevels"] = list(DPS_TRIGGERS)
    unit["parameters"] = _resolve_defaults(scenario, source)
    return unit


def main():
    supplied = json.loads(SUPPLIED.read_text(encoding="utf-8"))
    bounds = contract.stat_bounds()
    searchable_bounds = {str(SEARCHABLE_PARAMETERS[name]): list(bounds[SEARCHABLE_PARAMETERS[name]]) for name in SEARCHABLE}

    defaults = {}
    mismatches = []
    healer_payload = None
    fodder_payloads = []
    for scenario in supplied:
        encounter = int(scenario["encounterId"])
        if [u["name"] for u in scenario["ownUnits"]][1] != scenario["ownUnits"][1]["name"]:
            pass
        dps = _synthetic_dps(scenario)
        defaults[str(encounter)] = {pid: dict(block) for pid, block in dps["parameters"].items()}
        # Verify: with no equipment the synthetic unit's battle value equals the source effective value.
        probe = dict(scenario)
        probe["ownUnits"] = [dps] + [dict(u) for u in scenario["ownUnits"][1:]]
        for pid in dps["parameters"]:
            pid = int(pid)
            want = int(contract.battle_value(scenario, scenario["ownUnits"][0], pid))
            got = int(contract.battle_value(probe, dps, pid))
            if want != got:
                mismatches.append(dict(encounter=encounter, parameter=pid, want=want, got=got))
        for name in SEARCHABLE:
            pid = SEARCHABLE_PARAMETERS[name]
            value = int(contract.battle_value(probe, dps, pid))
            low, high = bounds[pid]
            if not low <= value <= high:
                mismatches.append(dict(encounter=encounter, stat=name, value=value, low=low, high=high))
        healer = scenario["ownUnits"][1]
        fodders = scenario["ownUnits"][2:]
        payload = _canonical([{k: v for k, v in u.items() if k != "name"} for u in fodders])
        if healer_payload is None:
            healer_payload = healer
        elif _canonical({k: v for k, v in healer.items() if k != "name"}) != _canonical({k: v for k, v in healer_payload.items() if k != "name"}):
            mismatches.append(dict(encounter=encounter, what="healer differs across encounters"))
        fodder_payloads.append(payload)

    if len(set(fodder_payloads)) != 1:
        mismatches.append(dict(what="fodder payloads differ across encounters", count=len(set(fodder_payloads))))
    if mismatches:
        raise SystemExit("authoritative cast is not self-consistent: " + json.dumps(mismatches, indent=1))

    healer = dict(supplied[0]["ownUnits"][1])
    healer["skills"] = list(HEALER_SKILLS)
    healer["invocationLevels"] = list(HEALER_TRIGGERS)
    healer["weaponId"] = WEAPON_ID
    healer["equipment"] = list(EQUIPMENT)
    healer["parameters"] = _resolve_defaults(supplied[0], supplied[0]["ownUnits"][1])

    fodder_source = supplied[0]["ownUnits"][2]
    fodder = dict(fodder_source)
    fodder["skills"] = []
    fodder["invocationLevels"] = []
    fodder["weaponId"] = WEAPON_ID
    fodder["equipment"] = list(EQUIPMENT)
    fodder["parameters"] = _resolve_defaults(supplied[0], fodder_source)
    fodder["names"] = [u["name"] for u in supplied[0]["ownUnits"][2:]]

    roster_order = [DPS_NAME, healer["name"]] + list(fodder["names"])
    payload = dict(
        schema="ka-fixed-formation-policy-1",
        source=dict(
            suppliedCast=str(SUPPLIED.relative_to(ROOT)),
            statBounds=str(contract.BOUNDS_PATH.relative_to(ROOT)),
            note="fodder/healer definitions and per-encounter DPS starting values read from the "
                 "authoritative supplied cast; DPS converted to no-equipment synthetic via "
                 "search_contract.battle_value",
        ),
        fixed=dict(
            rosterOrder=roster_order,
            placementRoles=["dps", "fodder", "fodder", "fodder", "fodder", "healer"],
            equipment=list(EQUIPMENT),
            weaponId=WEAPON_ID,
            unitCount=6,
            fodderCount=4,
        ),
        dps=dict(
            name=DPS_NAME,
            skills=list(DPS_SKILLS),
            invocationLevels=list(DPS_TRIGGERS),
            weaponId=WEAPON_ID,
            equipment=list(EQUIPMENT),
            searchableParameters=searchable_bounds,
            defaults=defaults,
        ),
        healer=dict(
            name=healer["name"],
            skills=list(HEALER_SKILLS),
            invocationLevels=list(HEALER_TRIGGERS),
            weaponId=WEAPON_ID,
            equipment=list(EQUIPMENT),
            parameters=healer["parameters"],
        ),
        fodder=dict(
            names=list(fodder["names"]),
            skills=[],
            invocationLevels=[],
            weaponId=WEAPON_ID,
            equipment=list(EQUIPMENT),
            parameters=fodder["parameters"],
        ),
    )
    payload["policyHash"] = hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()
    OUT.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    print("wrote", OUT)
    print("policyHash", payload["policyHash"])
    print("searchable", json.dumps(searchable_bounds))
    print("DPS defaults per encounter:", len(defaults), "parameters:", sorted(defaults["0"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
