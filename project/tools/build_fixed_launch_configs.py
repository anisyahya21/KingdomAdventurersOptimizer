"""Emit shared, app-consumable fixed-formation launch configs for all four engines.

Each artifact encodes exactly how to start an engine so it runs the pinned formation and searches
only the Synthetic DPS raw stats: the Go CLI policy flag, the Rust and C++ config flag, and the
Python reference module contract. A launcher/app reads launch.json and forwards the per-engine
config, so a running search cannot silently start without the fixed policy.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "KA-Website" / "tools" / "recovery"))
import fixed_formation as ff  # noqa: E402

BASE = ROOT / "coordination" / "fixed-formation-20261006"
LAUNCH = BASE / "launch"
KERNEL = BASE / "kernel" / "ka_kernel_encounter_v3.dll"
TABLES = ROOT / "coordination" / "native-preparation" / "shared" / "builder-inputs" / "facts.json"
FIXED_RAWS = BASE / "workloads" / "rust-candidates-fixed-all20.json"
GO_WORKLOAD = BASE / "workloads" / "go-fixed-all20.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def write(name: str, value) -> str:
    target = LAUNCH / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return str(target.relative_to(ROOT))


def provenance() -> dict:
    return {
        "engineSha256": sha256(KERNEL),
        "mechanicsSha256": "32338978751a75c0e9fd099be2c82f7512030c1cd1800319250288b9c035edb2",
        "policySha256": ff.policy_hash(),
        "abiSha256": "4ea5a1307f9842c6154c3ef4319435e8c64e8410736e5e95e460bd683c6034f8",
        "arenaSha256": "51d7ccf31674459f136af28969e1f2b40f0b40c95894112d086cd2d7a51db169",
        "rawCandidatesSha256": sha256(FIXED_RAWS),
        "tablesSha256": sha256(TABLES),
        "currentKernelSha256": sha256(KERNEL),
    }


def main() -> int:
    policy = json.loads((BASE / "fixed-formation-policy.json").read_text(encoding="utf-8"))
    common = {"generations": 4, "seedCount": 2, "seedStart": 100001, "executors": 2,
              "focusEncounterIds": [0]}
    rust_config = {
        "schema": "ka-rust-optimizer-config-v1", "kernel": str(KERNEL),
        "catalog": str(ROOT / "coordination" / "native-preparation" / "rust" / "standalone-optimizer" / "catalog.json"),
        "candidates": str(FIXED_RAWS), "output": str(BASE / "launch" / "rust" / "output"),
        "encounterFilter": 0, "candidateLimit": 2, "executors": common["executors"],
        "generations": common["generations"], "candidatesPerGeneration": 4, "searchSeed": 20261006,
        "seedPairs": [[100001, 100001], [100002, 100002]],
        "mechanicsProvenance": {"engine": "canonical-ka-kernel-encounter-v3",
                                "initialization": "shared-native-rust-ai", "comparisonStatus": "not-compared"},
        "policyProvenance": {"selection": "mean-Earned", "finish": "explicit-full-candidate-intent",
                             "purpose": "fixed-formation-search", "fixedFormationPolicyHash": ff.policy_hash()},
        "fixedFormation": True, "executionMode": "diagnostic"}
    cpp_config = {
        "rawCandidates": str(FIXED_RAWS), "tables": str(TABLES), "kernelPath": str(KERNEL),
        "outputDir": str(BASE / "launch" / "cpp" / "output"), "executors": common["executors"],
        "generations": common["generations"], "seedCount": common["seedCount"],
        "seedStart": common["seedStart"], "seedPointers": ["/mathSeed", "/libSeed"],
        "headless": True, "mutations": [], "searchProfile": "adaptive-native",
        "searchScheduling": "encounter-wave", "focusEncounterIds": common["focusEncounterIds"],
        "fixedFormation": True, "executionMode": "diagnostic", "purpose": "diagnostic",
        "provenance": provenance()}
    go_launch = {
        "engine": "go", "workload": str(GO_WORKLOAD), "kernelPath": str(KERNEL),
        "output": str(BASE / "launch" / "go" / "output"),
        "command": ["<go-optimizer.exe>", "--mode", "search", "--input", str(GO_WORKLOAD),
                    "--output", str(BASE / "launch" / "go" / "output"),
                    "--executors", str(common["executors"]), "--batch", str(common["seedCount"]),
                    "--policy", "fixed-formation", "--seed", "20261006",
                    "--budget", str(common["seedCount"])],
        "policyFlag": "--policy fixed-formation"}
    python_launch = {
        "engine": "python", "module": str(ROOT / "KA-Website" / "tools" / "recovery" / "fixed_formation.py"),
        "api": {"admit": "admits(scenario) -> (bool, reasons)",
                "identity": "search_identity(scenario) -> sha256 hex",
                "canonicalScenario": "canonical_scenario(encounter_id, math_seed=..., lib_seed=...)",
                "mutate": "mutate_stat(scenario, seed, stat=None, value=None)"},
        "policyHash": ff.policy_hash(),
        "wallEnforcement": "the Python search must call admits()/search_identity()/mutate_stat(); only "
                           "the Synthetic DPS raw stats inside the walls may change"}
    index = {
        "schema": "ka-fixed-formation-launch-1",
        "policyHash": ff.policy_hash(),
        "policyDocument": str((BASE / "fixed-formation-policy.json").relative_to(ROOT)),
        "dpsBounds": policy["dps"]["searchableParameters"],
        "dpsSkills": policy["dps"]["skills"],
        "healerSkills": policy["healer"]["skills"],
        "fodderSkills": policy["fodder"]["skills"],
        "engines": {
            "go": {"activation": "CLI flag --policy fixed-formation", "artifact": write("go/launch.json", go_launch)},
            "rust": {"activation": "config key fixedFormation=true", "artifact": write("rust/config.json", rust_config)},
            "cpp": {"activation": "config key fixedFormation=true", "artifact": write("cpp/config.json", cpp_config)},
            "python": {"activation": "fixed_formation reference API", "artifact": write("python/launch.json", python_launch)},
        },
        "note": "Config-path engines must receive the generated artifact unchanged; the Go launcher must "
                "pass --policy fixed-formation. A search started without these is not the fixed policy.",
    }
    write("launch.json", index)
    print(json.dumps({"launch": str(LAUNCH / "launch.json"), "policyHash": ff.policy_hash(),
                      "engines": list(index["engines"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
