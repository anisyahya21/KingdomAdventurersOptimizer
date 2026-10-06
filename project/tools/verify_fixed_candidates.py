"""Validate that every scenario found in an engine output satisfies the fixed-formation policy."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "KA-Website" / "tools" / "recovery"))
import fixed_formation as ff  # noqa: E402


def scenarios(value, acc):
    if isinstance(value, dict):
        if "ownUnits" in value and "encounterId" in value:
            acc.append(value)
        else:
            for item in value.values():
                scenarios(item, acc)
    elif isinstance(value, list):
        for item in value:
            scenarios(item, acc)


def load(path):
    text = Path(path).read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return [json.loads(line) for line in text.splitlines() if line.strip()]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("inputs", nargs="+")
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    found = []
    for path in args.inputs:
        scenarios(load(path), found)
    unique = {}
    for scenario in found:
        unique[ff.search_identity(scenario)] = scenario
    report = dict(schema="ka-fixed-formation-candidate-check-1", policyHash=ff.policy_hash(),
                  inputs=args.inputs, distinct=len(unique), checked=0, violations=[], statsVaried=False)
    baselines = {}
    for identity, scenario in unique.items():
        report["checked"] += 1
        ok, reasons = ff.admits(scenario)
        if not ok:
            report["violations"].append(dict(identity=identity, reasons=reasons))
        encounter = int(scenario["encounterId"])
        dossier = ff.dossier(scenario)
        if encounter not in baselines:
            baselines[encounter] = dossier
        elif baselines[encounter] != dossier:
            report["statsVaried"] = True
    report["ok"] = not report["violations"] and report["checked"] > 0
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("distinct", "checked", "ok", "statsVaried")}, indent=1))
    if report["violations"]:
        print(json.dumps(report["violations"][:5], indent=1))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
