"""Enable only accelerators validated against this exact engine and source snapshot.

An accelerator is monkeypatch-style loop/setup acceleration that preserves the canonical engine's
arithmetic, call order and RNG stream. It is only enabled when a recorded parity report and the
engine's own provenance agree, so a changed simulator silently disables it instead of silently
scoring different numbers.

Record a new snapshot only after a parity run passes, then the headless worker enables it:

    pypy3 check_strategy_optimizer_throughput.py --mode both \\
        --reference strategy-optimizer-throughput-reference.json --output <report.json>
    python strategy_optimizer_backend.py --record <report.json>
"""
import argparse
import hashlib
import importlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
VALIDATION = HERE / 'strategy-optimizer-acceleration-validation.json'
SCHEMA = 'ka-accelerator-validation-1'

# Order matters only for reporting; both modules patch disjoint objects.
TARGETS = ('strategy_optimizer_setup_fast', 'strategy_optimizer_loop_fast')
MIN_PARITY_CASES = 22

# Scheduler modules whose contents cannot affect a battle result or the accelerated code paths.
# This mirrors the ignore set in `strategy_optimizer.same_simulator`, so a throughput or UI change
# never has to be re-validated as an engine change - and never silently keeps a stale validation
# either. Anything else moving still disables the accelerators.
NOT_ENGINE_FILES = {'tools/recovery/strategy_optimizer.py',
                    'tools/recovery/strategy_optimizer_limits.py'}


def _digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def identity():
    from strategy_optimizer_adapter import provenance
    if not VALIDATION.is_file():
        return dict(enabled=False, digest=None, reason='No validated accelerator snapshot')
    validation = json.loads(VALIDATION.read_text(encoding='utf-8'))
    files = provenance()['files']
    for name, expected in validation['referenceFiles'].items():
        if files.get(name) != expected:
            return dict(enabled=False, digest=None, reason='Reference changed: '+name)
    for name, expected in validation['accelerators'].items():
        path = HERE / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            return dict(enabled=False, digest=None, reason='Accelerator changed: '+name)
    return dict(enabled=True, digest=hashlib.sha256(VALIDATION.read_bytes()).hexdigest(),
                modules=validation['modules'])


def status():
    """Read-only acceleration state, safe to publish to the UI."""
    state = identity()
    if VALIDATION.is_file():
        recorded = json.loads(VALIDATION.read_text(encoding='utf-8'))
        state['evidence'] = recorded.get('evidence')
        state['recordedAt'] = recorded.get('recordedAt')
    return state


def record(parity_report, modules=TARGETS):
    """Pin the current engine snapshot + accelerator bytes using a passing parity report."""
    report = json.loads(Path(parity_report).read_text(encoding='utf-8'))
    if report.get('mode') != 'both':
        raise ValueError('The parity report must come from --mode both (loop and setup together).')
    cases = report.get('allParityCases') or 0
    if cases < MIN_PARITY_CASES:
        raise ValueError(f'The parity report covers {cases} cases; at least {MIN_PARITY_CASES} are '
                         'required, and every case must have matched the frozen digests.')
    modules = list(modules)
    for name in modules:
        if not (HERE / f'{name}.py').is_file():
            raise ValueError(f'Accelerator module not found: {name}.py')
    # Freeze the engine snapshot as it is now, so any later edit disables the accelerators.
    sys.path.insert(0, str(HERE))
    from strategy_optimizer_adapter import provenance
    payload = dict(
        schema=SCHEMA,
        recordedAt=datetime.now(timezone.utc).isoformat(timespec='seconds'),
        evidence=dict(report=Path(parity_report).name, mode=report['mode'], cases=cases,
                      runtime=report.get('runtime'),
                      fullBattleMeanSeconds=report.get('fullBattleMean'),
                      fullBattlesPerSecond=report.get('fullBattlesPerSecond')),
        referenceFiles={name: digest for name, digest in sorted(provenance()['files'].items())
                        if name not in NOT_ENGINE_FILES},
        # Keyed by file name: `identity()` re-hashes the accelerator *file* before enabling it.
        accelerators={f'{name}.py': _digest(HERE / f'{name}.py') for name in modules},
        modules=modules)
    VALIDATION.write_text(json.dumps(payload, indent=2, sort_keys=True)+'\n', encoding='utf-8')
    return payload


def install(validated):
    if not validated['enabled']:
        return []
    installed = []
    try:
        for name in validated['modules']:
            module = importlib.import_module(name)
            module.install()
            installed.append(module)
        return installed
    except BaseException:
        for module in reversed(installed):
            module.uninstall()
        raise


def uninstall(installed):
    for module in reversed(installed):
        if hasattr(module, 'uninstall'):
            module.uninstall()


def main(argv=None):
    parser = argparse.ArgumentParser(description='Report or record the optimiser accelerator state.')
    parser.add_argument('--record', type=Path, metavar='PARITY_REPORT',
                        help='record the current engine + accelerator bytes as validated')
    parser.add_argument('--status', action='store_true', help='print the current state as JSON')
    args = parser.parse_args(argv)
    if args.record:
        payload = record(args.record)
        print(json.dumps(dict(recorded=True, modules=payload['modules'],
                              referenceFiles=len(payload['referenceFiles']),
                              evidence=payload['evidence']), indent=2))
        return 0
    print(json.dumps(status(), indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
