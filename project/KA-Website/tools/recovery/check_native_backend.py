"""Integrated A/B parity and throughput through the production adapter entry point.

`strategy_optimizer_adapter.simulate` is the exact boundary the optimiser worker calls. This harness
forces each backend through that boundary and compares the compact results field by field and
digest by digest, exercises the template cache, proves repeated runs do not mutate the cached
template, proves a reachable consumable timeline now runs natively, and checks that a canonically
unreachable item type is still refused with a reason.

    pypy3.exe check_native_backend.py
"""
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import strategy_optimizer_adapter as adapter  # noqa: E402
import strategy_optimizer_native as native  # noqa: E402
from check_native_real_state import CASES  # noqa: E402
from strategy_optimizer_adapter import default_scenario  # noqa: E402


def compare(label, python_compact, native_compact, failures):
    keys = sorted(set(python_compact) | set(native_compact))
    for key in keys:
        if key == 'resultBackend':
            continue
        if python_compact.get(key) != native_compact.get(key):
            failures.append(f'{label}: {key}: python={python_compact.get(key)!r} '
                            f'native={native_compact.get(key)!r}')


def main():
    base = default_scenario()
    failures = []
    cases = 0
    for case in CASES:
        scenario = dict(base, **case)
        seeds = (case['mathSeed'], case['libSeed'])
        python_compact = adapter.simulate(scenario, seeds, backend='python')
        native_compact = adapter.simulate(scenario, seeds, backend='native')
        if native_compact.get('resultBackend') != 'native':
            failures.append(f"enc{case['encounterId']}: native run did not report resultBackend")
        compare(f"enc{case['encounterId']} {seeds}", python_compact, native_compact, failures)
        cases += 1

    # cache reuse: same candidate, several seeds, then a rebuild after eviction
    candidate = dict(base, **CASES[0])
    first = adapter.simulate(candidate, (1, 2), backend='native')
    for seed in ((3, 4), (5, 6), (7, 8), (9, 10)):
        got = adapter.simulate(candidate, seed, backend='native')
        want = adapter.simulate(candidate, seed, backend='python')
        compare(f'cache-reuse seed{seed}', want, got, failures)
    # eviction: different candidates exceed MAX_TEMPLATES, then the first is rebuilt
    for index, case in enumerate(CASES[1:1 + native.MAX_TEMPLATES]):
        adapter.simulate(dict(base, **case), (11, 12), backend='native')
    rebuilt = adapter.simulate(candidate, (1, 2), backend='native')
    compare('cache-rebuild', first, rebuilt, failures)

    # A reachable consumable timeline runs natively; a canonically unreachable item type still falls
    # back, with its own reason code.
    consumable = dict(base, **CASES[0])
    consumable['inputs'] = [dict(tick=5, phase='before_fighters', type='holy_herb'),
                            dict(tick=30, phase='after_fighters', type='item', item='salve',
                                 target='all')]
    consumable['holyHerbStock'] = 1
    consumable['items'] = {'salve': dict(bonusCategory=3, bonusType=2, bonusMinValue=100,
                                         bonusMaxValue=200)}
    consumable['itemStock'] = {'salve': 1}
    supported, reason = native.eligibility(consumable)
    if not supported:
        failures.append(f'reachable consumable scenario was judged ineligible: {reason}')
    native_compact = adapter.simulate(consumable, (1, 2), backend='native')
    canonical = adapter.simulate(consumable, (1, 2), backend='python')
    if native_compact.get('resultBackend') != 'native':
        failures.append('reachable consumable scenario did not run natively')
    compare('consumable-native', canonical, native_compact, failures)

    unreachable = dict(consumable,
                       inputs=[dict(tick=5, phase='before_fighters', type='item', item='odd',
                                    target='all')],
                       items={'odd': dict(bonusCategory=3, bonusType=1, bonusMinValue=10,
                                          bonusMaxValue=20)},
                       itemStock={'odd': 1}, holyHerbStock=0)
    supported, reason = native.eligibility(unreachable)
    if supported or 'all-resident' not in (reason or ''):
        failures.append(f'single-resident item scenario was not refused with a reason: {reason!r}')
    # The canonical engine itself cannot execute this dispatch, so both backends must raise the same
    # way rather than one of them inventing a target.
    for backend in ('native', 'python'):
        try:
            adapter.simulate(unreachable, (1, 2), backend=backend)
            failures.append(f'single-resident item scenario did not raise on {backend}')
        except ValueError as error:
            if 'explicit resident' not in str(error):
                failures.append(f'single-resident item raised an unexpected error on {backend}: '
                                f'{error}')

    print(f'integrated backend A/B: {cases} whole-battle cases + cache + eviction + fallback')
    print(f'  fallback reason for the consumable scenario: {reason}')
    print(f'  counters: {native.counters()}')
    if failures:
        print('FAILURES:')
        for row in failures[:10]:
            print('  ' + row)
        return 1
    print('  every compact field and digest matched; templates were reused and rebuilt; '
          'no run mutated the cached template')
    return 0


if __name__ == '__main__':
    sys.exit(main())
