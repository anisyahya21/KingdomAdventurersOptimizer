"""Build fixed-formation native workload inputs from the canonical policy (Python reference)."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RECOVERY = ROOT / "KA-Website" / "tools" / "recovery"
sys.path.insert(0, str(RECOVERY))
import fixed_formation as ff  # noqa: E402

BASE = ROOT / "coordination" / "native-preparation" / "shared" / "builder-inputs" / "go-current-all20-workload.json"
OUTDIR = ROOT / "coordination" / "fixed-formation-20261006" / "workloads"
KERNEL_SRC = ROOT / "coordination" / "native-preparation" / "cpp" / "runs" / "cpp-20-pool-smoke-20261004" / "isolated-workspace" / "KA-Website" / "tools" / "recovery" / "native" / "ka_kernel" / "target" / "release" / "ka_kernel_encounter_v3.dll"
KERNEL_DIR = ROOT / "coordination" / "fixed-formation-20261006" / "kernel"


def main():
    OUTDIR.mkdir(parents=True, exist_ok=True)
    KERNEL_DIR.mkdir(parents=True, exist_ok=True)
    kernel = KERNEL_DIR / "ka_kernel_encounter_v3.dll"
    if not kernel.exists():
        shutil.copy2(KERNEL_SRC, kernel)
    base = json.loads(BASE.read_text(encoding="utf-8"))
    seeds = base.get("seeds") or [{"mathSeed": 100001 + i, "libSeed": 200001 + i} for i in range(20)]
    candidates = []
    for encounter in range(20):
        seed = seeds[encounter % len(seeds)]
        scenario = ff.canonical_scenario(encounter, math_seed=int(seed["mathSeed"]), lib_seed=int(seed["libSeed"]))
        ok, reasons = ff.admits(scenario)
        if not ok:
            raise SystemExit(f"encounter {encounter} not admitted: {reasons}")
        candidates.append(scenario)
    workload = dict(base)
    workload["candidates"] = candidates
    workload["kernelPath"] = str(kernel)
    workload["note"] = "Fixed formation: pinned six-unit roster; only Synthetic DPS raw stats are searched."
    out = OUTDIR / "go-fixed-all20.json"
    out.write_text(json.dumps(workload), encoding="utf-8")
    print("wrote", out, "candidates", len(candidates), "kernel", kernel)


if __name__ == "__main__":
    raise SystemExit(main())
