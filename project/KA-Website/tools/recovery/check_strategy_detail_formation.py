"""Does the desktop strategy detail report the canonical prepared formation?

One deterministic fixture, two paths: the adapter's own frozen default scenario is prepared directly
with `combat_setup.prepare_setup`, and the desktop detail helper `_canonical_formation` prepares the
same scenario. The helper must return exactly the canonical units - no row/column inferred on its
own side. A scenario the preparation refuses must degrade to an empty grid plus an error, never
raise, so the rest of the detail stays usable.

    .venv/Scripts/python.exe check_strategy_detail_formation.py
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from combat_setup import prepare_setup  # noqa: E402
from strategy_optimizer_adapter import default_scenario  # noqa: E402
from strategy_optimizer_desktop import _canonical_formation  # noqa: E402


def canonical_units(scenario):
    """The expected detail units, taken straight from the canonical preparation."""
    prepared = prepare_setup(scenario)
    return [dict(name=member['name'], slot=member['incomingIndex']+1,
                 row=member['row'], column=member['column'],
                 parameters=member['effectiveParameters'],
                 effectiveDefense=member['effectiveDefense'])
            for member in prepared['ownUnits']]


def main():
    failures = []

    def expect(label, condition, detail=''):
        print(f'  {"ok   " if condition else "FAIL "} {label}' + ('' if condition else f': {detail}'))
        if not condition:
            failures.append(f'{label}: {detail}')

    scenario = default_scenario()
    expected = canonical_units(scenario)
    actual = _canonical_formation(scenario)
    units = actual.get('units') or []

    expect('the frozen fixture prepares at least one own unit', bool(expected))
    expect('a legal scenario reports no preparation error', 'error' not in actual,
           str(actual.get('error')))
    expect('the detail formation equals prepare_setup exactly',
           json.dumps(units, sort_keys=True) == json.dumps(expected, sort_keys=True),
           f'detail={json.dumps(units)[:400]} canonical={json.dumps(expected)[:400]}')
    expect('every unit carries a distinct 1-based slot',
           bool(units) and len({unit['slot'] for unit in units}) == len(units),
           str([unit['slot'] for unit in units]))
    expect('every unit sits on a non-negative row/column',
           all(isinstance(unit['row'], int) and unit['row'] >= 0
               and isinstance(unit['column'], int) and unit['column'] >= 0 for unit in units))
    expect('parameters carry only value/maximum pairs',
           all(set(pair) == {'value', 'maximum'}
               for unit in units for pair in unit['parameters'].values()))

    degraded = _canonical_formation(dict(schema='ka-special-combat-research-1'))
    expect('an unpreparable scenario degrades to empty grid plus error',
           degraded.get('units') == [] and bool(degraded.get('error')), str(degraded))

    print()
    if failures:
        print('FAILURES:')
        for row in failures:
            print('  ' + row)
        return 1
    print(f'  formation matches prepare_setup for {len(units)} prepared unit(s)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
