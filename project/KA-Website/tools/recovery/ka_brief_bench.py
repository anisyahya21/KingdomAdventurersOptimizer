"""Measure brief retrieval against the full packages.

Acceptance test for candidate selection. For each topic it runs `ka_index.py brief`
at several budgets and reports:

  recall    share of the full packages' addresses that appear anywhere in the brief
  precision share of the brief's addresses that the full packages also contain
  size      bytes and approximate tokens

Precision is the guard against expansion: it falls when a brief starts pulling in
functions the evidence does not connect to the topic.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent
ROOT = WORKSPACE.parents[2]
PACKAGES = ROOT / "RE-evidence/20260920-native-index/packages"
PYTHON = Path(r"C:\Users\anisb\OneDrive\Desktop\replit kingdom adventures - Copy"
              r"\.venv\Scripts\python.exe")
INDEX = WORKSPACE / "ka_index.py"
RVA = re.compile(r"0x[0-9a-f]{5,8}", re.I)

TOPICS = [
    # Topic strings match the ones the packages were generated with, so the
    # comparison is brief-pipeline versus package-pipeline on the same query rather
    # than two different queries.
    ("B3 receipt", "treasure receipt dispatch", ["receipt-dispatch.md"]),
    ("B3 settlement", "settlement teardown exp confirmation", ["settlement.md"]),
    ("status / skills", "status skill invocation charging", ["status-skills.md"]),
    ("rng provenance", "rng seed random producer", ["rng-provenance.md"]),
    ("box award", "box award leaving prize", ["box-award.md"]),
]


def addresses(text: str):
    return {match.group(0).lower() for match in RVA.finditer(text)}


def measure(topic, reference, budgets, extra_args=()):
    reference_text = "".join((PACKAGES / name).read_text(encoding="utf-8")
                             for name in reference)
    reference_rvas = addresses(reference_text)
    rows = []
    for budget in budgets:
        result = subprocess.run(
            [str(PYTHON), str(INDEX), "brief", topic, "--budget", str(budget),
             *extra_args],
            capture_output=True, encoding="utf-8", errors="replace")
        text = result.stdout
        found = addresses(text)
        hit = found & reference_rvas
        rows.append({
            "budget": budget,
            "bytes": len(text),
            "tokens": len(text) // 4 + 1,
            "recall": len(hit),
            "recall_total": len(reference_rvas),
            "precision": len(hit),
            "precision_total": len(found),
        })
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--budgets", type=int, nargs="*", default=[700, 1100, 1600])
    parser.add_argument("--label", default="current")
    parser.add_argument("--topic", action="append", default=[],
                        help="limit to topics whose name contains this text")
    parser.add_argument("--no-overlay", action="store_true",
                        help="measure the brief without the combat overlay section")
    parser.add_argument("--no-keys", action="store_true",
                        help="measure the brief without the numeric-key section")
    args = parser.parse_args()
    print(f"# brief benchmark - {args.label}")
    print()
    print(f"{'topic':16} {'budget':>6} {'bytes':>7} {'tokens':>7} {'recall':>10} {'precision':>11}")
    summary = {}
    for name, topic, reference in TOPICS:
        if args.topic and not any(needle.lower() in name.lower() for needle in args.topic):
            continue
        extra = tuple(flag for flag, wanted in (("--no-overlay", args.no_overlay),
                                                ("--no-keys", args.no_keys)) if wanted)
        for row in measure(topic, reference, args.budgets, extra):
            recall = 100 * row["recall"] / max(1, row["recall_total"])
            precision = 100 * row["precision"] / max(1, row["precision_total"])
            print(f"{name:16} {row['budget']:>6} {row['bytes']:>7} {row['tokens']:>7} "
                  f"{row['recall']:>3}/{row['recall_total']:<3}={recall:>3.0f}% "
                  f"{row['precision']:>3}/{row['precision_total']:<3}={precision:>3.0f}%")
            summary[(name, row["budget"])] = (recall, precision, row["tokens"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
