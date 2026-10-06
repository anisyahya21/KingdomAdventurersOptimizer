"""Deterministic local verification of fixed-formation enforcement in the C++ engine.

Runs the built C++ optimizer through its canonical-preparation, proposal and headless-search
entry points and records real observed behaviour (not code inspection) for the fixed policy.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "KA-Website" / "tools" / "recovery"))
import fixed_formation as ff  # noqa: E402

BASE = ROOT / "coordination" / "fixed-formation-20261006"
CPP = ROOT / "coordination" / "native-preparation" / "cpp" / "standalone-optimizer"
EXE = CPP / "optimizer.exe"
TABLES = ROOT / "coordination" / "native-preparation" / "shared" / "builder-inputs" / "facts.json"
KERNEL = BASE / "kernel" / "ka_kernel_encounter_v3.dll"
FIXED_RAWS = BASE / "workloads" / "rust-candidates-fixed-all20.json"
ORIGINAL = ROOT / "coordination" / "native-preparation" / "shared" / "comparison-inputs" / "bank2-v3" / "inputs" / "all20-candidates.json"
RUN = BASE / "runs" / "cpp-verification"
REPORT = BASE / "cpp-verification.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def invoke(args: list[str]) -> tuple[int, str, str]:
    completed = subprocess.run(args, capture_output=True, text=True, cwd=str(ROOT))
    return completed.returncode, completed.stdout, completed.stderr


def candidate_check(scenarios: list[dict]) -> dict:
    unique = {}
    violations = []
    baselines = {}
    varied = False
    for scenario in scenarios:
        unique.setdefault(ff.search_identity(scenario), scenario)
    for identity, scenario in unique.items():
        ok, reasons = ff.admits(scenario)
        if not ok:
            violations.append({"identity": identity, "reasons": reasons})
        dossier = ff.dossier(scenario)
        encounter = int(scenario["encounterId"])
        if encounter in baselines and baselines[encounter] != dossier:
            varied = True
        baselines[encounter] = dossier
    return {"schema": "ka-fixed-formation-candidate-check-1", "policyHash": ff.policy_hash(),
            "distinct": len(unique), "checked": len(unique), "violations": violations,
            "statsVaried": varied, "ok": not violations and len(unique) > 0}


def main() -> int:
    if not EXE.is_file():
        raise SystemExit(f"missing C++ optimizer executable: {EXE}")
    RUN.mkdir(parents=True, exist_ok=True)
    fixed_scenarios = json.loads(FIXED_RAWS.read_text(encoding="utf-8"))
    original_cast = json.loads(ORIGINAL.read_text(encoding="utf-8"))
    original = original_cast[0] if isinstance(original_cast, list) else original_cast["candidates"][0]
    enc0 = fixed_scenarios[0]
    write_json(RUN / "raw-enc0.json", enc0)
    write_json(RUN / "raw-original-enc0.json", original)

    checks: dict = {}
    ok = True

    # 1. Canonical preparation accepts the fixed point for its encounter.
    code, out, err = invoke([str(EXE), "--prepare-fixed", str(RUN / "raw-enc0.json"), str(TABLES)])
    prepared = json.loads(out) if code == 0 and out.strip() else {}
    placement = prepared.get("ownFormationOrder")
    checks["canonicalPreparationPositive"] = {
        "exit": code, "snapshotPresent": "snapshot" in prepared, "ownFormationOrder": placement,
        "placementRolesMatched": placement == [0, 2, 3, 4, 5, 1], "stderr": err.strip()}
    ok = ok and code == 0 and "snapshot" in prepared and placement == [0, 2, 3, 4, 5, 1]

    # 2. Canonical preparation refuses the historical equipped cast.
    code, out, err = invoke([str(EXE), "--prepare-fixed", str(RUN / "raw-original-enc0.json"), str(TABLES)])
    checks["canonicalPreparationNegativeEquippedCast"] = {"exit": code, "error": err.strip()}
    ok = ok and code == 2 and bool(err.strip())

    # 3. Fixed proposal generates only Synthetic DPS raw-stat children.
    request = RUN / "propose-request.json"
    write_json(request, {"mode": "search", "fixedFormation": True, "count": 7, "seed": 11})
    out_path = RUN / "propose-out.json"
    code, out, err = invoke([str(EXE), "--propose", str(RUN / "raw-enc0.json"), str(TABLES), str(request), str(out_path)])
    proposal = json.loads(out_path.read_text(encoding="utf-8")) if out_path.is_file() else {}
    children = [child.get("rawScenario") for child in proposal.get("children", [])]
    ops = sorted({child.get("mutation", {}).get("op") for child in proposal.get("children", [])})
    validation = candidate_check([scenario for scenario in children if scenario])
    checks["fixedProposal"] = {"exit": code, "generated": proposal.get("generated"),
                               "requested": proposal.get("requested"), "operators": ops,
                               "allFixedStatOp": ops == ["fixed-dps-stat"], "validation": validation,
                               "stderr": err.strip()}
    ok = ok and code == 0 and ops == ["fixed-dps-stat"] and validation["ok"]

    # 4. Fixed proposal refuses the historical equipped cast.
    neg_out = RUN / "propose-out-negative.json"
    code, out, err = invoke([str(EXE), "--propose", str(RUN / "raw-original-enc0.json"), str(TABLES), str(request), str(neg_out)])
    neg_response = json.loads(neg_out.read_text(encoding="utf-8")) if neg_out.is_file() else {}
    checks["fixedProposalNegativeEquippedCast"] = {"exit": code, "ok": neg_response.get("ok"),
                                                   "error": neg_response.get("error")}
    ok = ok and code == 2 and neg_response.get("ok") is False

    # 5. Headless search runs under the fixed policy and only emits compliant, varied candidates.
    provenance = {
        "engineSha256": sha256(KERNEL),
        "mechanicsSha256": "32338978751a75c0e9fd099be2c82f7512030c1cd1800319250288b9c035edb2",
        "policySha256": ff.policy_hash(),
        "abiSha256": "4ea5a1307f9842c6154c3ef4319435e8c64e8410736e5e95e460bd683c6034f8",
        "arenaSha256": "51d7ccf31674459f136af28969e1f2b40f0b40c95894112d086cd2d7a51db169",
        "rawCandidatesSha256": sha256(FIXED_RAWS),
        "tablesSha256": sha256(TABLES),
        "currentKernelSha256": sha256(KERNEL)}
    output = RUN / "optimizer-output"
    config = {"rawCandidates": str(FIXED_RAWS), "tables": str(TABLES), "kernelPath": str(KERNEL),
              "outputDir": str(output), "executors": 2, "generations": 4, "seedCount": 2,
              "seedStart": 100001, "seedPointers": ["/mathSeed", "/libSeed"], "headless": True,
              "mutations": [], "searchProfile": "adaptive-native", "searchScheduling": "encounter-wave",
              "focusEncounterIds": [0], "fixedFormation": True, "executionMode": "diagnostic",
              "purpose": "diagnostic", "provenance": provenance}
    config_path = RUN / "config.json"
    write_json(config_path, config)
    code, out, err = invoke([str(EXE), str(config_path)])
    rows = []
    journal = output / "results.jsonl"
    if journal.is_file():
        rows = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines() if line.strip()]
    scenarios = []
    for row in rows:
        for key in ("rawScenario", "candidateScenario"):
            if isinstance(row.get(key), dict):
                scenarios.append(row[key])
    search_validation = candidate_check(scenarios)
    status = json.loads((output / "status.json").read_text(encoding="utf-8-sig")) if (output / "status.json").is_file() else {}
    checks["headlessSearch"] = {"exit": code, "savedRows": len(rows),
                                "executionErrors": status.get("executionErrors"),
                                "invalidCandidates": status.get("invalidCandidates"),
                                "state": status.get("state"), "validation": search_validation,
                                "stderr": err.strip()}
    ok = ok and code == 0 and len(rows) > 0 and status.get("executionErrors", 1) == 0 and search_validation["ok"]

    policy = json.loads((BASE / "fixed-formation-policy.json").read_text(encoding="utf-8"))
    report = {
        "schema": "ka-fixed-formation-cpp-verification-1",
        "engine": "cpp",
        "policyHash": ff.policy_hash(),
        "checkedUtc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "executable": {"path": str(EXE), "sha256": sha256(EXE)},
        "dpsBounds": policy["dps"]["searchableParameters"],
        "dpsSkills": policy["dps"]["skills"],
        "healerSkills": policy["healer"]["skills"],
        "fodderSkills": policy["fodder"]["skills"],
        "checks": checks,
        "ok": bool(ok),
    }
    write_json(REPORT, report)
    print(json.dumps({"engine": "cpp", "ok": report["ok"], "report": str(REPORT),
                      "checks": {key: value.get("exit") if isinstance(value, dict) else None
                                 for key, value in checks.items()}}, indent=1))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
