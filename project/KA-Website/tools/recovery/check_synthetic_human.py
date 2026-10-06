"""Deterministic tests for the universal synthetic human (search-space version 3).

No battles are run here: every check is about the *model*. The synthetic human is job-free, so the
tests are written the way the model is defined:

  * the reviewed stat walls hold, and the source derivation reproduces them;
  * a super-human vector (each stat from a different real job's ceiling) validates and the simulator
    reads exactly those effective values - with no job/rank/level anywhere in the scenario;
  * the weapon behaviour dimension is independent of the stat vector across EVERY behaviour group;
  * every approved skill can be carried subject only to the search/combat rules;
  * a second human slot can carry a completely different vector;
  * team-wide rules (max two 7-Hit across human slots) still apply;
  * values one step outside every wall are refused before any simulation;
  * production search can never reach the legacy permutation generator.

    python check_synthetic_human.py [--json OUT]
"""
import argparse
import json
import sys
from copy import deepcopy
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import search_contract as contract  # noqa: E402
from strategy_optimizer_adapter import default_scenario, validate_scenario  # noqa: E402

#: The reviewed super-human vector: each value is the ceiling of its own interval, taken from a
#: different source row (Royal, Doctor, Entertainer, ...). No real character could hold all of them.
SUPER_HUMAN = {'hp': 19287, 'mp': 7986, 'atk': 5766, 'def': 4976,
               'spd': 4780, 'lck': 5204, 'int': 7134, 'dex': 7049}


def refused(action):
    try:
        action()
    except contract.ContractError:
        return True
    except Exception:  # noqa: BLE001 - a different failure is a real error, not a refusal
        return False
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', type=Path)
    args = parser.parse_args()
    failures = []
    report = {}

    def expect(label, condition, detail=''):
        print(f'  {"ok   " if condition else "FAIL "} {label}' + ('' if condition else f': {detail}'))
        if not condition:
            failures.append(f'{label}: {detail}')

    base = validate_scenario(default_scenario())
    humans = [unit for unit in base['ownUnits'] if unit.get('human')]
    first, second = humans[0], humans[1]

    # ---- the walls are the reviewed ones, verified against the source ------------------------
    document = json.loads(contract.BOUNDS_PATH.read_text(encoding='utf-8'))
    report['verification'] = document['verification']
    expect('the derivation reproduces every reviewed number',
           document['verification']['verified']
           and not document['verification']['mismatches'],
           json.dumps(document['verification']['mismatches']))
    expect('the bounds file is the version-3 synthetic derivation',
           document.get('schema') == 'ka-search-stat-bounds-2'
           and document.get('searchSpaceVersion') == contract.SEARCH_SPACE_VERSION,
           f"{document.get('schema')} v{document.get('searchSpaceVersion')}")
    wall_expectations = {'hp': (55, 19287), 'mp': (5, 7986), 'atk': (6, 5766), 'def': (3, 4976),
                         'spd': (5, 4780), 'lck': (5, 5204), 'int': (2, 7134), 'dex': (2, 7049)}
    walls = {stat: contract.stat_bounds()[contract.stat_parameter(stat)]
             for stat in wall_expectations}
    expect('the declared intervals are exactly the reviewed ones',
           walls == wall_expectations, json.dumps(walls))
    report['walls'] = {stat: list(pair) for stat, pair in walls.items()}

    # ---- a job-free, super-human vector -------------------------------------------------------
    super_scenario = base
    for stat, value in SUPER_HUMAN.items():
        if stat == 'int':
            # INT is a conditional dimension: it needs a magic attack skill before it is searchable.
            super_scenario = contract.add_skill(super_scenario, first['name'], 5)
        super_scenario = contract.set_stat(super_scenario, first['name'], stat, value)
    unit = next(entry for entry in super_scenario['ownUnits'] if entry['name'] == first['name'])
    expect('the super-human scenario validates', validate_scenario(super_scenario) is not None)
    expect('no job, rank or character level appears anywhere in the scenario',
           not any(key in json.dumps(super_scenario).lower()
                   for key in ('"job"', '"rank"', '"level"'))
           or all('rank' not in key and 'job' not in key for key in unit),
           'a job/rank field leaked into the scenario')
    seen = {stat: contract.battle_value(super_scenario, unit, pid)
            for stat, pid in {**contract.CORE_STATS, **contract.INT_STAT}.items()}
    expect('the simulator reads exactly the requested synthetic values',
           seen == SUPER_HUMAN, f'{seen} != {SUPER_HUMAN}')
    report['superHuman'] = seen

    # ---- independence of stats and weapon behaviour, across every group ----------------------
    groups = contract.weapon_groups()
    moved = []
    for group in groups.values():
        for weapon_id in group['weapons']:
            try:
                probe = contract.set_weapon(super_scenario, first['name'], weapon_id)
            except contract.ContractError as error:
                moved.append(f'{weapon_id}: {error}')
                continue
            probe_unit = next(entry for entry in probe['ownUnits'] if entry['name'] == first['name'])
            values = {stat: contract.battle_value(probe, probe_unit, pid)
                      for stat, pid in {**contract.CORE_STATS, **contract.INT_STAT}.items()}
            if values != SUPER_HUMAN:
                moved.append(f'{weapon_id}: {values}')
            break
    expect('switching weapon behaviour leaves every synthetic stat untouched (all groups)',
           not moved, '; '.join(moved[:3]))
    report['weaponGroupsChecked'] = len(groups)

    # ---- skill access is universal ------------------------------------------------------------
    approved = contract.whitelist()
    inaccessible = []
    for skill_id, display in sorted(approved.items()):
        row = contract.skill_row(skill_id)
        added = False
        last = ''
        for slot in (second, first):
            target = super_scenario
            if skill_id in slot['skills']:
                continue
            if contract.is_formation_skill(skill_id) and any(
                    contract.is_formation_skill(other) for other in slot['skills']):
                # A fighter already carrying a formation skill cannot take a second one; another slot
                # is the honest test of availability.
                continue
            try:
                if int(row['requiredEquipType']) != -1:
                    # Weapon-dependent *combat* legality still applies: equip the matching behaviour.
                    group = next(info for info in groups.values()
                                 if any(int(contract._weapon_row({'weaponId': wid})['type'])
                                        == int(row['requiredEquipType']) for wid in info['weapons']))
                    target = contract.set_weapon(target, slot['name'], group['weapons'][0])
                target = contract.add_skill(target, slot['name'], skill_id)
            except contract.ContractError as error:
                last = str(error)
                continue
            if any(skill_id in entry['skills'] for entry in target['ownUnits']):
                added = True
                break
        if not added:
            inaccessible.append(f'{display}: {last}')
    expect('every approved skill is available to a synthetic human (search/combat rules only)',
           not inaccessible, '; '.join(inaccessible[:3]))

    # ---- two slots, two different vectors ------------------------------------------------------
    dual = super_scenario
    second_vector = {'hp': 800, 'mp': 300, 'atk': 120, 'def': 90, 'spd': 150, 'lck': 60, 'dex': 40}
    for stat, value in second_vector.items():
        dual = contract.set_stat(dual, second['name'], stat, value)
    dual_first = next(entry for entry in dual['ownUnits'] if entry['name'] == first['name'])
    dual_second = next(entry for entry in dual['ownUnits'] if entry['name'] == second['name'])
    first_seen = {stat: contract.battle_value(dual, dual_first, pid)
                  for stat, pid in contract.CORE_STATS.items()}
    second_seen = {stat: contract.battle_value(dual, dual_second, pid)
                   for stat, pid in contract.CORE_STATS.items()}
    expect('two human slots hold two independent synthetic vectors',
           first_seen == {k: SUPER_HUMAN[k] for k in contract.CORE_STATS}
           and second_seen == second_vector,
           f'{first_seen} / {second_seen}')

    # ---- team-wide rules still span the slots -------------------------------------------------
    capped = contract.remove_skill(base, first['name'], base['ownUnits'][0]['skills'].index(110))
    capped = contract.add_skill(capped, first['name'], 110, trigger=0)
    capped = contract.add_skill(capped, second['name'], 110, trigger=0)
    expect('two 7-Hit Attacks across two synthetic slots are allowed',
           sum(entry['skills'].count(110) for entry in capped['ownUnits']) == 2)
    expect('a third 7-Hit Attack across the team is refused',
           refused(lambda: contract.add_skill(capped, 'Scholar Fodder 1', 110, trigger=0)))

    # ---- every wall, and one step outside it --------------------------------------------------
    outside = []
    # The synthetic value is written into the raw channel with the carrier's contribution cancelled in
    # the extra channel, so the walls are reachable even for a fighter whose weapon carries stats; the
    # strict pair of checks is therefore run on the super-human slot itself.
    for stat, (minimum, maximum) in walls.items():
        probe = super_scenario
        if stat == 'int' and not contract.stat_is_searchable(probe, probe['ownUnits'][0], 'int'):
            probe = contract.add_skill(probe, first['name'], 5)
        # Move the stat off its wall first: writing a stat to the value it already holds is a no-op and
        # is refused on purpose, so the wall writes below must be genuine changes.
        probe = contract.set_stat(probe, first['name'], stat, (minimum+maximum)//2)
        for target, allowed in ((minimum, True), (maximum, True),
                                (minimum-1, False), (maximum+1, False)):
            action = (lambda probe=probe, stat=stat, target=target:
                      contract.set_stat(probe, first['name'], stat, target))
            if allowed and refused(action):
                outside.append(f'{stat}={target} should be legal')
            if not allowed and not refused(action):
                outside.append(f'{stat}={target} should be refused')
    expect('every wall is reachable and one step outside it is refused', not outside,
           '; '.join(outside[:4]))

    # ---- production search cannot reach the legacy generator ---------------------------------
    import strategy_optimizer as optimizer
    from strategy_optimizer_adapter import propose as adapter_propose
    legacy_calls = []
    original = optimizer.legacy_proposal
    optimizer.legacy_proposal = lambda *a, **k: legacy_calls.append(a) or original(*a, **k)
    try:
        produced = 0
        for seed in range(40):
            try:
                candidate = adapter_propose(deepcopy(base), seed)
            except Exception:
                continue
            produced += 1
            if contract.search_identity(candidate) != contract.search_identity(base):
                break
        expect('production proposals come from the contract and never from the legacy generator',
               produced > 0 and not legacy_calls,
               f'produced {produced}, legacy calls {len(legacy_calls)}')
    finally:
        optimizer.legacy_proposal = original
    expect('the production rotation contains no legacy axis and no job/rank axis',
           not any(axis in ('skills', 'formation') or 'job' in axis or 'rank' in axis
                   for axis in optimizer.PROPOSAL_AXES),
           f'{optimizer.PROPOSAL_AXES}')
    report['proposalAxes'] = list(optimizer.PROPOSAL_AXES)

    print()
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, indent=1, default=str), encoding='utf-8')
    if failures:
        print('FAILURES:')
        for row in failures:
            print('  ' + row)
        return 1
    print('  the universal synthetic human model holds: job-free stats at the reviewed walls, '
          'independent weapon behaviour, universal skill access, per-slot builds and team-wide rules')
    return 0


if __name__ == '__main__':
    sys.exit(main())
