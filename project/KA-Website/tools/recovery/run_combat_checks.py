"""Run the combat checks in one batch and record a comparable outcome for each.

Usage: python run_combat_checks.py [--only NAME ...] [--list] [--timeout SECONDS] [--quiet]

Every `check_combat_*.py` runs in its own interpreter, so a native Unicorn fixture and a portable
model check cannot leak state into each other. The batch record names the frozen native build, the
per-check outcome, the captured output and a digest over the **outcomes only** (wall-clock seconds and
the interpreter version are recorded but excluded from the digest), which is what makes a
differential-fixture run reproducible instead of a series of ad-hoc invocations.
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from combat_initial_state import EVIDENCE
from combat_run_manifest import NATIVE_SHA256, canonical_hash

TOOLS = Path(__file__).parent
OUT = EVIDENCE / 'check-runner.json'


def discover():
    return sorted(path.stem for path in TOOLS.glob('check_combat_*.py'))


def run_one(name, timeout):
    started = time.perf_counter()
    entry = dict(check=name)
    try:
        finished = subprocess.run([sys.executable, f'{name}.py'], cwd=TOOLS, capture_output=True,
                                  text=True, errors='replace', timeout=timeout, input='')
        entry.update(passed=finished.returncode == 0, returncode=finished.returncode,
                     stdout=finished.stdout.strip(), stderr=finished.stderr.strip())
    except subprocess.TimeoutExpired:
        entry.update(passed=False, returncode=None, stdout='', stderr=f'timeout after {timeout}s')
    entry['seconds'] = round(time.perf_counter() - started, 2)
    return entry


def outcome_digest(passed, failed, results, checks=None):
    """Digest the outcome fields only: wall-clock seconds and the interpreter version are not results.

    Two runs of unchanged code must produce the same digest, so a changed digest means a changed
    outcome (a check, its exit code or its output) rather than a slower machine.
    """
    return canonical_hash(dict(
        schema='ka-combat-check-run-1', nativeSha256=NATIVE_SHA256, passed=passed, failed=failed,
        checks=len(results) if checks is None else checks,
        results=[{key: entry[key] for key in ('check', 'passed', 'returncode', 'stdout', 'stderr')}
                 for entry in results]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--only', nargs='*', default=None, help='exact check names to run')
    parser.add_argument('--list', action='store_true', help='list checks and exit')
    parser.add_argument('--timeout', type=float, default=900.0)
    parser.add_argument('--quiet', action='store_true', help='do not echo each check output')
    args = parser.parse_args()
    names = discover()
    if args.list:
        print(json.dumps(dict(checks=names, count=len(names))))
        return 0
    if args.only:
        missing = [name for name in args.only if name not in names]
        if missing:
            raise SystemExit(f'Unknown checks: {missing}')
        names = list(args.only)
    results = []
    for name in names:
        entry = run_one(name, args.timeout)
        results.append(entry)
        if not args.quiet:
            print(f"{'pass' if entry['passed'] else 'FAIL'} {name} ({entry['seconds']}s)")
            if not entry['passed']:
                print(entry['stderr'] or entry['stdout'])
    failed = [entry['check'] for entry in results if not entry['passed']]
    report = dict(schema='ka-combat-check-run-1', nativeSha256=NATIVE_SHA256,
                  passed=len(results) - len(failed), failed=failed, checks=len(results),
                  results=results, python=sys.version)
    report['digest'] = outcome_digest(report['passed'], failed, results, report['checks'])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(dict(passed=report['passed'], failed=report['failed'],
                          digest=report['digest'], output=str(OUT))))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
