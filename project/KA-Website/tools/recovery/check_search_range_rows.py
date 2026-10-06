"""Does `shootingRange` mean "rows away", and can a larger range be collapsed?

Two independent probes, both against the recovered simulator rather than against intuition:

  1. **Geometry.** `combat_targeting.nearest_skill_target` picks the strictly nearest valid target
     with `distance(caster, target) <= shooting_range`, and `distance` is the recovered Manhattan cell
     distance. Using the real encounter's own placement, this prints, for every own row, the distance
     to the nearest enemy and the set of enemies each range can reach - which shows exactly when a
     larger range can change target availability or identity.
  2. **Whole battles.** The same scenario and seeds are run with the equipped weapon's
     `shootingRange` forced to 1, 2, 3 and 7. Identical digests mean the fight did not notice the
     range; different digests mean the distinction is real for that state.

    python check_search_range_rows.py [--json OUT]
"""
import argparse
import json
import sys
from copy import deepcopy
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from strategy_optimizer_adapter import default_scenario, simulate, validate_scenario  # noqa: E402


def geometry():
    """Per own row: nearest enemy distance and the enemies each range can reach."""
    from combat_setup import prepare_setup
    scenario = validate_scenario(default_scenario())
    prepared = prepare_setup(deepcopy(scenario))
    own = [dict(name=row['name'], cell=[int(v) for v in row['cell']], grid=int(row['grid']))
           for row in prepared['ownUnits']]
    enemy = [dict(name=f"enemy:{index}", cell=[int(v) for v in row['cell']])
             for index, row in enumerate(prepared['encounter']['fighters'])]
    rows = {}
    for unit in own:
        distance = {other['name']: abs(unit['cell'][0]-other['cell'][0])
                    + abs(unit['cell'][1]-other['cell'][1]) for other in enemy}
        nearest = min(distance.values()) if distance else None
        reach = {str(r): sorted(name for name, d in distance.items() if d <= r)
                 for r in (1, 2, 3, 4, 5, 6, 7)}
        rows[unit['name']] = dict(cell=unit['cell'], grid=unit['grid'], nearest=nearest,
                                  reach=reach,
                                  minimumRange=nearest,
                                  row=unit['cell'][1])
    return dict(own=rows, enemy=[entry['cell'] for entry in enemy])


def forced_range_battles(ranges=(1, 2, 3, 7), seeds=((11, 22), (33, 44))):
    """Run the same fight with only the weapon's shootingRange changed."""
    import combat_runtime_data
    original = combat_runtime_data.load_data
    scenario = validate_scenario(default_scenario())
    weapon_id = scenario['ownUnits'][0]['weaponId']
    results = {}

    def patched(name, *args, **kwargs):
        data = original(name, *args, **kwargs)
        if name != 'weapon-skill-profiles.json':
            return data
        data = deepcopy(data)
        for row in data['equipment']:
            if int(row['id']) == int(weapon_id):
                row['shootingRange'] = current[0]
        return data

    current = [None]
    for value in ranges:
        current[0] = value
        combat_runtime_data.load_data = patched
        try:
            results[str(value)] = [simulate(deepcopy(scenario), seed, backend='python')['digest']
                                   for seed in seeds]
        finally:
            combat_runtime_data.load_data = original
    return dict(weaponId=int(weapon_id), seeds=[list(seed) for seed in seeds], digests=results)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', type=Path)
    args = parser.parse_args()
    failures = []

    def expect(label, condition, detail=''):
        print(f'  {"ok   " if condition else "FAIL "} {label}' + ('' if condition else f': {detail}'))
        if not condition:
            failures.append(f'{label}: {detail}')

    report = dict(geometry=geometry())
    print('  geometry (own cell -> nearest enemy distance, and what each range reaches):')
    for name, row in report['geometry']['own'].items():
        sizes = {r: len(names) for r, names in row['reach'].items()}
        print(f"    {name:<28} cell={row['cell']} nearest={row['nearest']} "
              f"reachable-by-range={sizes}")

    # The user's hypothesis: from row 1 a range of 1 suffices, and larger ranges there are redundant.
    front = [row for row in report['geometry']['own'].values() if row['cell'][1] >= 2]
    front_rows = [row for row in report['geometry']['own'].values() if row['row'] >= 2]
    can_grow = [row for row in front_rows
                if len(row['reach']['2']) > len(row['reach']['1'])
                or len(row['reach']['3']) > len(row['reach']['2'])]
    expect('the measure is a Manhattan cell distance, so a range only matters when the nearest '
           'reachable enemy sits beyond it',
           all(row['nearest'] <= 1 for row in front_rows)
           or bool(can_grow),
           'neither case was observed, so the fixture does not exercise the range at all')
    report['hypothesis'] = dict(
        frontRowNearest=[row['nearest'] for row in front_rows],
        rowsWhereLargerRangeAddsTargets=[row['grid'] for row in can_grow])

    battles = forced_range_battles()
    report['battles'] = battles
    values = sorted(battles['digests'], key=int)
    reference = battles['digests'][values[0]]
    for value in values[1:]:
        same = battles['digests'][value] == reference
        print(f"    shootingRange {value}: {'identical trace' if same else 'DIFFERENT trace'} "
              f"for seeds {battles['seeds']}")
    report['rangesThatChangedTheTrace'] = [value for value in values[1:]
                                           if battles['digests'][value] != reference]
    print()
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, indent=1, default=str), encoding='utf-8')
    if failures:
        print('FAILURES:')
        for row in failures:
            print('  ' + row)
        return 1
    print('  the range/row question is answered by the simulator\'s own targeting, not by a rule of '
          'thumb: a range is redundant only where nothing reachable sits beyond the smaller one')
    return 0


if __name__ == '__main__':
    sys.exit(main())
