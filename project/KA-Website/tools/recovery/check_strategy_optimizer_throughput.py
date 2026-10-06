"""Varied full-report parity and warmed, serial wall-clock throughput benchmark."""
import argparse
from copy import deepcopy
import hashlib
import importlib
import json
from pathlib import Path
import platform
import statistics
import time


def cases():
    from strategy_optimizer_adapter import default_scenario, propose
    from strategy_optimizer import seed_pair
    base = default_scenario()
    rows = []
    for encounter in range(20):
        rows.append((f'encounter-{encounter}', dict(base, encounterId=encounter, tickLimit=1000,
            mathSeed=100+encounter, libSeed=700+encounter)))
    for ordinal in range(8):
        a, b = seed_pair('discovery', ordinal)
        scenario = dict(base, tickLimit=7000, mathSeed=a, libSeed=b)
        if ordinal in (2, 3):
            scenario = dict(scenario, holyHerbStock=3 if ordinal == 3 else 0,
                inputs=[dict(tick=t, type='holy_herb', phase=phase)
                        for t, phase in ((150, 'before_fighters'), (450, 'after_fighters'), (900, 'before_fighters'))])
        if ordinal in (4, 5):
            scenario = propose(scenario, ordinal)
        if ordinal == 6:
            scenario = dict(scenario, defeatCount=3)
        rows.append((f'full-{ordinal}', scenario))
    # Explicit A/B/A repetition to catch mutable state leaking through process caches.
    rows.extend((('repeat-A', deepcopy(rows[20][1])), ('repeat-B', deepcopy(rows[23][1])),
                 ('repeat-A-again', deepcopy(rows[20][1]))))
    return rows


def gameplay_digest(report):
    gameplay = {key: value for key, value in report.items() if key != 'manifest'}
    return hashlib.sha256(json.dumps(gameplay, sort_keys=True).encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=('reference', 'setup', 'loop', 'both'), default='reference')
    parser.add_argument('--reference', type=Path, required=True)
    parser.add_argument('--record-reference', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.record_reference:
        if args.mode != 'reference' or args.reference.exists():
            raise ValueError('Reference recording requires reference mode and a new output path')
        expected = None
    else:
        expected = json.loads(args.reference.read_text())
    from combat_sandbox import run_scenario
    modules = []
    try:
        for name in ('setup', 'loop'):
            if args.mode in (name, 'both'):
                module = importlib.import_module('strategy_optimizer_'+name+'_fast')
                module.install()
                modules.append(module)
        rows = []
        for name, scenario in cases():
            started = time.perf_counter()
            report = run_scenario(deepcopy(scenario), include_trace=True)
            seconds = time.perf_counter()-started
            digest = gameplay_digest(report)
            if expected is not None:
                assert expected[name] == digest, f'Gameplay/trace mismatch in {name}'
            rows.append(dict(name=name, seconds=seconds, ticks=report['result']['ticks'],
                             verdictTick=report['result']['verdictTick'], digest=digest))
            print(json.dumps(dict(case=name, seconds=round(seconds, 4), matched=expected is not None)), flush=True)
        if args.record_reference:
            args.reference.write_text(json.dumps({r['name']: r['digest'] for r in rows}, indent=2))
        full = [r['seconds'] for r in rows if r['name'].startswith('full-')]
        output = dict(mode=args.mode, runtime=platform.python_implementation()+' '+platform.python_version(),
            fullBattleMean=statistics.mean(full), fullBattlesPerSecond=len(full)/sum(full),
            allParityCases=len(rows), fullBattleCount=len(full), results=rows)
        args.output.write_text(json.dumps(output, indent=2))
        print(json.dumps({k:v for k,v in output.items() if k != 'results'}), flush=True)
    finally:
        for module in reversed(modules):
            module.uninstall()


if __name__ == '__main__':
    main()
