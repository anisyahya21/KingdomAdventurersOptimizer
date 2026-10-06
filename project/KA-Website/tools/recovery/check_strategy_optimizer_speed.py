"""Cross-runtime/full-trace parity and unprofiled timing. Run each runtime serially."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import time
from strategy_optimizer_adapter import default_scenario
from combat_sandbox import run_scenario


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--compare', type=Path)
    args = parser.parse_args()
    expected = json.loads(args.compare.read_text())['runs'] if args.compare else None
    base = default_scenario()
    cases = [dict(encounterId=i, tickLimit=1000, mathSeed=7+i, libSeed=8+i) for i in range(20)]
    cases += [dict(encounterId=19, tickLimit=7000, mathSeed=a, libSeed=b)
              for a, b in ((7, 8), (907303519, 267534053))]
    rows = []
    for index, case in enumerate(cases):
        started = time.perf_counter()
        report = run_scenario(dict(base, **case), include_trace=True)
        seconds = time.perf_counter()-started
        # Manifest contains runtime/source identity; gameplay comparison excludes only that metadata.
        checked = {key: value for key, value in report.items() if key != 'manifest'}
        digest = hashlib.sha256(json.dumps(checked, sort_keys=True).encode()).hexdigest()
        if expected:
            assert expected[index]['case'] == case
            assert expected[index]['digest'] == digest, f'Full report mismatch: {case}'
        rows.append(dict(case=case, seconds=seconds, digest=digest))
        print(json.dumps(dict(case=index, seconds=round(seconds, 3), matched=bool(expected))), flush=True)
    payload = dict(runtime=platform.python_implementation()+' '+platform.python_version(), runs=rows)
    args.output.write_text(json.dumps(payload, indent=2))


if __name__ == '__main__':
    main()
