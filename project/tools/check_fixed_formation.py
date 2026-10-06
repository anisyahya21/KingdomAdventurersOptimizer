"""Deterministic verification of the fixed-formation policy in the original Python engine."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RECOVERY = ROOT / "KA-Website" / "tools" / "recovery"
sys.path.insert(0, str(RECOVERY))

import fixed_formation as ff  # noqa: E402
import search_contract as contract  # noqa: E402
import strategy_students as students  # noqa: E402

OUT = ROOT / "coordination" / "fixed-formation-20261006" / "python-verification.json"


def main():
    report = dict(engine="python", schema="ka-fixed-formation-verification-1",
                  policyHash=ff.policy_hash(), encounters=[], errors=[], warnings=[])
    for encounter in range(20):
        entry = dict(encounterId=encounter)
        scenario = ff.canonical_scenario(encounter, math_seed=1000 + encounter, lib_seed=2000 + encounter)
        ok, reasons = ff.admits(scenario)
        entry["admits"] = ok
        if not ok:
            report["errors"].append(dict(encounterId=encounter, reasons=reasons))
            report["encounters"].append(entry)
            continue
        # Canonical placement must be the fixed dps/fodder x4/healer shape.
        rows = students._placed_rows(scenario)
        roles = [students.role(row["unit"]) for row in sorted(rows, key=lambda r: r["grid"])]
        entry["placementRoles"] = roles
        entry["placementCells"] = [list(row["cell"]) for row in sorted(rows, key=lambda r: r["grid"])]
        if roles != list(ff.PLACEMENT_ROLES):
            report["errors"].append(dict(encounterId=encounter, placement=roles))
        # Every searched stat must be adjustable inside the walls and stay admitted.
        start = ff.search_identity(scenario)
        entry["mutations"] = []
        for stat in ("atk", "spd", "lck", "def", "hp"):
            low, high = ff.searchable_parameters()[ff.searchable_stats()[stat]]
            child = ff.mutate_stat(scenario, seed=encounter * 31 + low + high, stat=stat, value=low)
            entry["mutations"].append(dict(stat=stat,
                                           before=ff.dossier(scenario)[stat],
                                           after=ff.dossier(child)[stat],
                                           admitted=ff.admits(child)[0],
                                           identityChanged=ff.search_identity(child) != start))
        # A non-searched change (fodder stat, skill, equipment, extra unit) must be refused.
        bad_fodder = json.loads(json.dumps(scenario))
        bad_fodder["ownUnits"][2]["parameters"]["14"]["rawValue"] += 1
        bad_skill = json.loads(json.dumps(scenario))
        bad_skill["ownUnits"][0]["skills"][0] = 109
        bad_equip = json.loads(json.dumps(scenario))
        bad_equip["ownUnits"][0]["equipment"] = [dict(id=35, level=76, affinity=1)]
        bad_unit = json.loads(json.dumps(scenario))
        bad_unit["ownUnits"].append(dict(bad_unit["ownUnits"][2]))
        entry["refusals"] = dict(
            fodderStat=not ff.admits(bad_fodder)[0],
            dpsSkill=not ff.admits(bad_skill)[0],
            dpsEquipment=not ff.admits(bad_equip)[0],
            extraUnit=not ff.admits(bad_unit)[0])
        # Deterministic identity: same scenario -> same id; DPS stat change -> different id.
        entry["identityStable"] = ff.search_identity(ff.canonical_scenario(encounter, math_seed=7, lib_seed=9)) == start
        report["encounters"].append(entry)
    report["checks"] = dict(
        encountersAdmitted=sum(1 for e in report["encounters"] if e.get("admits")),
        placementCorrect=sum(1 for e in report["encounters"] if e.get("placementRoles") == list(ff.PLACEMENT_ROLES)),
        allMutationsAdmitted=all(m["admitted"] and m["identityChanged"] for e in report["encounters"] for m in e.get("mutations", [])),
        allRefusalsEnforced=all(all(e.get("refusals", {}).values()) for e in report["encounters"]),
        identityDeterministic=all(e.get("identityStable") for e in report["encounters"]),
    )
    # Engine integration: the legacy Python optimizer's admission and child-generation choke points
    # honour the policy only when explicitly enabled, and stay silent (unchanged) by default.
    scenario0 = ff.canonical_scenario(0, math_seed=1, lib_seed=2)
    non_fixed = json.loads(json.dumps(scenario0))
    non_fixed["ownUnits"][2]["parameters"]["14"]["rawValue"] += 1
    default_silent = ff.gate(scenario0) is None and ff.gate(non_fixed) is None
    ff.set_enabled(True)
    enabled_admits = ff.gate(scenario0) is None
    enabled_refuses = ff.gate(non_fixed) is not None
    admission_refused = False
    try:
        import strategy_admission_preparation
        strategy_admission_preparation.prepare(non_fixed, {})
    except ff.FixedFormationError:
        admission_refused = True
    except Exception:
        admission_refused = False
    ff.set_enabled(False)
    import inspect
    import strategy_optimizer
    optimizer_gate_wired = "fixed_formation.gate(scenario)" in inspect.getsource(strategy_optimizer.Store.add_child)
    report["integration"] = dict(
        defaultOffSilent=default_silent,
        enabledAdmitsFixed=enabled_admits,
        enabledRefusesNonFixed=enabled_refuses,
        admissionPreparationRefuses=admission_refused,
        optimizerChildGateWired=optimizer_gate_wired,
        activation="fixed_formation.set_enabled(True)",
    )
    report["ok"] = (not report["errors"] and report["checks"]["encountersAdmitted"] == 20
                    and report["checks"]["placementCorrect"] == 20
                    and report["checks"]["allMutationsAdmitted"]
                    and report["checks"]["allRefusalsEnforced"]
                    and report["checks"]["identityDeterministic"]
                    and all(report["integration"][k] for k in (
                        "defaultOffSilent", "enabledAdmitsFixed", "enabledRefusesNonFixed",
                        "admissionPreparationRefuses", "optimizerChildGateWired")))
    OUT.write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(json.dumps(report["checks"], indent=1))
    print("errors", len(report["errors"]))
    print("ok", report["ok"])
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
