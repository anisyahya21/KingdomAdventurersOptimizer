"""Run two trials of the corrected original learner; no model or private library."""
import argparse
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT / "project"
BASE = PROJECT / "coordination/fixed-formation-20261006"

def relocate(value):
    if isinstance(value, str) and value.startswith(("coordination/", "KA-Website/")):
        return str(PROJECT / value)
    if isinstance(value, dict):
        return {k: relocate(v) for k, v in value.items()}
    if isinstance(value, list):
        return [relocate(v) for v in value]
    return value

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("engine", choices=("cpp", "rust", "go"))
    parser.add_argument("--exe", type=Path, required=True, help="Freshly built corrected executable")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--output-dir", type=Path, help="Fresh output directory; defaults under runtime")
    args = parser.parse_args()
    output = args.output_dir.resolve() if args.output_dir else ROOT / "runtime/corrected-smoke" / args.engine
    if output.exists():
        raise SystemExit("Output already exists; preserve it and select a fresh --output-dir.")
    if not args.exe.is_file():
        raise SystemExit("Build the corrected source first and pass its executable with --exe.")
    workload = relocate(json.loads((BASE / "workloads/go-fixed-all20.json").read_text()))
    candidate = workload["candidates"][0]
    candidate.update(mathSeed=2026100817, libSeed=2026100819)
    for pid, value in {"10": 2500, "11": 2500, "14": 2500, "19": 18, "13": 6, "15": 5, "16": 5}.items():
        parameter = candidate["ownUnits"][0]["parameters"][pid]
        parameter["rawValue"] = value
        if pid in ("10", "11"):
            parameter["rawMax"] = value
    output.mkdir(parents=True)
    input_path = output / "input.json"
    if args.engine == "go":
        workload["candidates"] = [candidate]
        workload.pop("seeds", None)  # Search uses scenario seeds, then its normal child-seed policy.
        input_path.write_text(json.dumps(workload))
        command = [str(args.exe.resolve()), "--mode", "search", "--input", str(input_path),
                   "--output", str(output / "results"), "--executors", "1", "--batch", "1",
                   "--policy", "fixed-formation", "--seed", "2026100817", "--budget", "2",
                   "--objective-mode", "mechanism-lanes-v3", "--learner-mode", "branching",
                   "--constraint-profile", "fixed-dps-3stats-v1"]
    else:
        input_path.write_text(json.dumps([candidate]))
        config = relocate(json.loads((BASE / f"launch/{args.engine}/config.json").read_text()))
        config.update(executors=1, generations=1, seedPairs=[[2026100817, 2026100819]],
                      objectiveMode="mechanism-lanes-v3", executionMode="production")
        config["candidates" if args.engine == "rust" else "rawCandidates"] = str(input_path)
        config["output" if args.engine == "rust" else "outputDir"] = str(output / "results")
        config["syntheticDpsSearch"] = {"fixedParameters": {"10": 2500, "11": 2500, "14": 2500, "19": 18},
                                       "mutableParameters": ["13", "15", "16"]}
        if args.engine == "cpp":
            config.update(prospectiveChildGuidance=False, freshTrialBudget=2, purpose="strategy", seedCount=1,
                          candidateProfile="synthetic-dps-fixed-formation-v1",
                          fixedFormationPolicyHash="403a443d931840b23ccc1bad071055422fd6e1f465681fbf2b5a6291fef7382f")
            config["provenance"].update(engineSha256=hashlib.sha256(args.exe.read_bytes()).hexdigest(),
                                         rawCandidatesSha256=hashlib.sha256(input_path.read_bytes()).hexdigest())
        else:
            config["candidatesPerGeneration"] = 2  # One initial candidate plus one child.
            config["policyProvenance"]["selection"] = "mechanism-lanes-v3"
        config_path = output / "config.json"
        config_path.write_text(json.dumps(config, indent=2))
        command = [str(args.exe.resolve()), str(config_path)]
    (output / "command.json").write_text(json.dumps(command, indent=2))
    if args.prepare_only:
        print(json.dumps({"state": "prepared", "command": command, "battlesLaunched": 0}))
    else:
        subprocess.run(command, cwd=PROJECT, check=True)

if __name__ == "__main__":
    main()
