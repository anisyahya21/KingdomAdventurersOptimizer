"""Dry generation tests for the hard search-space contract: no battles are run.

Every check here is a *generation* check: it proves that a legal dimension is reachable, or that an
illegal/no-op one is refused before any simulation. The counterpart measurement (a short controlled
search) is `check_search_contract_search.py`.

    python check_search_contract.py [--json OUT]
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


def refused(action):
    """True when the action raises a contract refusal (the only legal outcome for an illegal move)."""
    try:
        action()
    except contract.ContractError:
        return True
    except Exception:  # noqa: BLE001 - any other failure is a real error, not a refusal
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
    roomy = next((unit for unit in humans if len(unit['skills']) < contract.MAX_SKILLS_PER_HUMAN),
                 humans[0])
    approved = contract.whitelist()
    report['baseline'] = dict(unit=roomy['name'], skills=list(roomy['skills']),
                              trigger=list(roomy['invocationLevels']))

    # ---- reachability: every approved skill can be introduced --------------------------------
    reachable, unreachable = [], []
    for skill_id, display in sorted(approved.items()):
        row = contract.skill_row(skill_id)
        added = False
        last = ''
        for target_unit in humans:
            probe = base
            if skill_id in target_unit['skills']:
                continue
            if int(row['requiredEquipType']) != -1:
                # A weapon-conditional skill is reachable through the weapon dimension as well; that
                # pair is the honest statement of reachability, so it is exercised rather than skipped.
                group = next((info for info in contract.weapon_groups().values()
                              if any(int(contract._weapon_row({'weaponId': wid})['type'])
                                     == int(row['requiredEquipType']) for wid in info['weapons'])), None)
                if group is None:
                    last = f'no weapon of type {row["requiredEquipType"]}'
                    continue
                try:
                    probe = contract.set_weapon(base, target_unit['name'], group['weapons'][0])
                except contract.ContractError as error:
                    last = str(error)
                    continue
            try:
                candidate = contract.add_skill(probe, target_unit['name'], skill_id)
            except contract.ContractError as error:
                last = str(error)
                continue
            if any(skill_id in unit['skills'] for unit in candidate['ownUnits']):
                added = True
                break
        (reachable if added else unreachable).append(display if added else f'{display}: {last}')
    expect('every approved skill is reachable from the baseline', not unreachable,
           '; '.join(unreachable[:4]))
    report['skillsReachable'] = len(reachable)

    # ---- reachability: every legal trigger setting, and each mutation family ------------------
    triggers = set()
    for level in contract.TRIGGER_LEVELS:
        # Reach each level by *changing* to it, so the no-op refusal for the current value does not
        # hide the level itself.
        seeded = base
        if seeded['ownUnits'][0]['invocationLevels'][0] == level:
            for other in contract.TRIGGER_LEVELS:
                if other != level:
                    seeded = contract.set_trigger(base, seeded['ownUnits'][0]['name'], 0, other)
                    break
        try:
            candidate = contract.set_trigger(seeded, seeded['ownUnits'][0]['name'], 0, level)
            triggers.add(candidate['ownUnits'][0]['invocationLevels'][0])
        except contract.ContractError:
            pass
    expect('every legal trigger setting is reachable', triggers == set(contract.TRIGGER_LEVELS),
           f'reached {sorted(triggers)}')

    ops_seen = {}
    for op in contract.MUTATION_OPS:
        for seed in range(60):
            try:
                candidate = contract.mutate(base, seed, op=op)
            except contract.ContractError:
                continue
            ops_seen[op] = contract.describe_change(base, candidate)
            break
    missing_ops = [op for op in contract.MUTATION_OPS if op not in ops_seen]
    expect('every mutation family produces a candidate', not missing_ops,
           f'no candidate for {missing_ops}')
    report['mutationOps'] = ops_seen

    # ---- hard rules --------------------------------------------------------------------------
    nine = base
    while len(nine['ownUnits'][0]['skills']) < contract.MAX_SKILLS_PER_HUMAN:
        stray = [skill for skill in sorted(approved)
                 if skill not in nine['ownUnits'][0]['skills']
                 and int(contract.skill_row(skill)['requiredEquipType']) == -1
                 and not contract.is_formation_skill(skill)]
        if not stray:
            break
        nine = contract.add_skill(nine, nine['ownUnits'][0]['name'], stray[0])
    expect('a human can reach exactly the 9-skill cap',
           len(nine['ownUnits'][0]['skills']) == contract.MAX_SKILLS_PER_HUMAN,
           f"{len(nine['ownUnits'][0]['skills'])} skills")
    expect('a tenth skill is refused',
           refused(lambda: contract.add_skill(
               nine, nine['ownUnits'][0]['name'],
               next(skill for skill in sorted(approved)
                    if skill not in nine['ownUnits'][0]['skills']))),
           'the cap did not refuse a tenth skill')

    expect('a duplicate skill on one human is refused',
           refused(lambda: contract.add_skill(base, roomy['name'], roomy['skills'][0])),
           'a duplicate was accepted')

    six_hit = next(skill for skill in contract.EXCLUDED_SKILLS)
    expect('the excluded 6-Hit Attack is refused even though the row exists',
           refused(lambda: contract.add_skill(base, 'Scholar Fodder 1', six_hit)),
           '6-Hit Attack was accepted')

    outside = next(skill for skill in contract._skill_rows()
                   if skill not in approved and skill not in contract.EXCLUDED_SKILLS
                   and not contract.is_formation_skill(skill))
    expect('a skill outside the approved set is refused',
           refused(lambda: contract.add_skill(base, 'Scholar Fodder 1', outside)),
           f'skill {outside} was accepted')

    # The baseline Ninja already carries 7-Hit, so the team cap allows exactly one more fighter.
    third = contract.remove_skill(base, roomy['name'],
                                  base['ownUnits'][0]['skills'].index(110))
    for name in ('Scholar Fodder 1', 'Scholar Fodder 2'):
        third = contract.add_skill(third, name, 110, trigger=0)
    expect('a third team-wide 7-Hit Attack is refused',
           refused(lambda: contract.add_skill(third, 'Scholar Fodder 3', 110, trigger=0)),
           'a third 7-Hit Attack was accepted')
    report['teamSevenHitCap'] = 2

    bow = next(group for group in contract.weapon_groups().values()
               if any(int(contract._weapon_row({'weaponId': wid})['type']) == 8
                      for wid in group['weapons']))
    armed = contract.set_weapon(base, 'Scholar Fodder 1', bow['weapons'][0])
    expect('a bow-only skill is reachable once a bow is equipped',
           contract.add_skill(armed, 'Scholar Fodder 1', contract.BOW_ATTACK_ID if
                              contract.BOW_ATTACK_ID in approved else 36) is not None,
           'the bow-conditional skill could not be added with a bow')
    expect('a bow-only skill is refused without a bow',
           refused(lambda: contract.add_skill(base, 'Scholar Fodder 1', 36)),
           'Arrow Rain was accepted without a bow')

    # ---- formation skills ---------------------------------------------------------------------
    formation = contract.set_formation_skill(base, roomy['name'], 105)
    expect('a formation skill can be set',
           105 in formation['ownUnits'][0]['skills'], 'the formation skill was not added')
    expect('a second formation skill is refused',
           refused(lambda: contract.add_skill(formation, roomy['name'], 107)),
           'two formation skills were accepted')
    expect('switching the formation skill replaces it rather than stacking',
           contract.set_formation_skill(formation, roomy['name'], 107)
           ['ownUnits'][0]['skills'].count(107) == 1
           and 105 not in contract.set_formation_skill(formation, roomy['name'], 107)
           ['ownUnits'][0]['skills'],
           'the formation switch did not replace the previous formation skill')
    expect('a formation skill takes no ordinary trigger search',
           refused(lambda: contract.set_trigger(formation, roomy['name'], 0, 2)),
           'a formation trigger was mutated')
    with_formation = contract.add_skill(base, 'Scholar Fodder 1', 105, trigger=1)
    plain = contract.add_skill(base, 'Scholar Fodder 1', 30, trigger=1)
    expect('changing only a formation trigger is not a new strategy',
           contract.search_identity(with_formation) != contract.search_identity(plain)
           and contract.search_identity(with_formation) == contract.search_identity(
               _retrigger_formation(with_formation, 2)),
           'the identity followed an unread trigger value')

    # ---- conditional INT ----------------------------------------------------------------------
    expect('INT is refused for a fighter without a magic skill',
           refused(lambda: contract.set_stat(base, roomy['name'], 'int', 500)),
           'INT was searched without a magic skill')
    magic = contract.add_skill(base, roomy['name'], 5)  # Fire Magic I
    expect('INT becomes searchable once a magic attack skill is equipped',
           contract.stat_is_searchable(magic, magic['ownUnits'][0], 'int'),
           'INT stayed unavailable with Fire Magic I equipped')
    target = 900
    with_int = contract.set_stat(magic, roomy['name'], 'int', target)
    expect('INT can then be set inside its derived interval',
           contract.battle_value(with_int, with_int['ownUnits'][0], 18) == target,
           'the INT step did not reach its target')
    stripped = contract.remove_skill(magic, roomy['name'], len(magic['ownUnits'][0]['skills'])-1)
    expect('removing the last magic skill removes the INT dimension again',
           not contract.stat_is_searchable(stripped, stripped['ownUnits'][0], 'int')
           and '18' in {str(key) for key in stripped['ownUnits'][0]['parameters']}
           or 18 in stripped['ownUnits'][0]['parameters'],
           'INT was deleted from the model instead of becoming unavailable')

    # ---- stat walls ---------------------------------------------------------------------------
    bounds = contract.stat_bounds()
    outside_bounds = []
    # The walls are global (the best and worst a real build can reach). A specific fighter carrying
    # heavy equipment cannot go below its own equipment floor, so the in-bounds half of this test runs
    # on a fighter with no equipment, where both walls are physically reachable.
    bare = next(unit for unit in base['ownUnits'] if unit.get('human') and not unit['equipment'])
    for stat, pid in sorted({**contract.CORE_STATS, **contract.INT_STAT}.items(),
                            key=lambda item: item[1]):
        minimum, maximum = bounds[pid]
        probe = base
        if stat == 'int':
            probe = contract.add_skill(base, bare['name'], 5)
        for target, allowed in ((minimum, True), (maximum, True),
                                (minimum-1, False), (maximum+1, False)):
            action = (lambda probe=probe, stat=stat, target=target:
                      contract.set_stat(probe, bare['name'], stat, target))
            ok = not refused(action) if allowed else refused(action)
            if not ok:
                outside_bounds.append(f'{stat}={target} ({ "should be legal" if allowed else "should be refused"})')
    expect('every stat step stays inside the derived walls', not outside_bounds,
           '; '.join(outside_bounds[:4]))
    report['bounds'] = {str(pid): list(pair) for pid, pair in bounds.items()}

    # ---- weapon behaviour groups --------------------------------------------------------------
    groups = contract.weapon_groups()
    reached = 0
    for group in groups.values():
        for weapon_id in group['weapons']:
            try:
                contract.set_weapon(base, 'Scholar Fodder 1', weapon_id)
                reached += 1
                break
            except contract.ContractError:
                continue
    expect('every weapon behaviour group is reachable', reached == len(groups),
           f'{reached} of {len(groups)} groups')
    def armed_with(weapon_id):
        probe = contract.set_weapon(base, 'Scholar Fodder 1', weapon_id)
        unit = next(unit for unit in probe['ownUnits'] if unit['name'] == 'Scholar Fodder 1')
        return probe, contract.weapon_group_id(unit)

    multi = [group for group in groups.values() if len(group['weapons']) > 1]
    if multi:
        probe_a, group_a = armed_with(multi[0]['weapons'][0])
        probe_b, group_b = armed_with(multi[0]['weapons'][1])
        expect('two weapons inside one behaviour group are the same search-space point',
               group_a == group_b
               and contract.search_identity(probe_a) == contract.search_identity(probe_b),
               f'{group_a} vs {group_b}')
    else:
        expect('a behaviour group with several weapons exists', False,
               'no group collapsed more than one weapon')
    probe_c, group_c = armed_with(multi[0]['weapons'][0])
    other = next(group for group in groups.values() if group['weapons']
                 and contract.weapon_group_id(dict(weaponId=group['weapons'][0])) != group_c)
    probe_d, group_d = armed_with(other['weapons'][0])
    expect('different behaviour groups stay different strategies',
           group_c != group_d
           and contract.search_identity(probe_c) != contract.search_identity(probe_d),
           f'{group_c} vs {group_d}')
    report['weaponGroups'] = len(groups)

    # ---- no-ops are refused before a battle ---------------------------------------------------
    no_ops = {
        'trigger to its current value':
            lambda: contract.set_trigger(base, roomy['name'], 0,
                                         base['ownUnits'][0]['invocationLevels'][0]),
        'stat to its current value':
            lambda: contract.set_stat(base, roomy['name'], 'atk',
                                      contract.battle_value(base, base['ownUnits'][0], 13)),
        'skill the unit already carries':
            lambda: contract.add_skill(base, roomy['name'], roomy['skills'][0]),
        'removing a skill that is not there':
            lambda: contract.remove_skill(base, 'Scholar Fodder 1', 0),
        'a roster swap that changes no placement':
            lambda: contract.reorder_roster(base, 2, 3),
    }
    accepted = [name for name, action in no_ops.items() if not refused(action)]
    expect('every no-op proposal is refused before it can be simulated', not accepted,
           f'accepted: {accepted}')

    # ---- order is part of the strategy --------------------------------------------------------
    ordered_a = contract.move_skill(base, roomy['name'], 0, 1)
    ordered_b = contract.move_skill(base, roomy['name'], 0, 2)
    expect('the same skill set in a different order is a different strategy',
           contract.search_identity(ordered_a) != contract.search_identity(ordered_b)
           != contract.search_identity(base),
           'two orderings shared an identity')

    # ---- versioning: a legacy library is readable but never continued silently ----------------
    import tempfile
    import time
    from strategy_optimizer import Optimizer, Store, scope
    from strategy_optimizer_adapter import provenance as adapter_provenance
    from strategy_optimizer_adapter import stats as adapter_stats
    root = tempfile.mkdtemp(prefix='ka-contract-version-')
    legacy_path = Path(root)/'legacy.sqlite'
    fixture = dict(default_scenario(), tickLimit=40, encounterId=19)
    store = Store(legacy_path, adapter_provenance())
    with store.db:
        store.set('scope', scope(fixture))
        store.add(fixture, 'Legacy fixture', 'supplied', adapter_stats(fixture))
        store.db.execute("DELETE FROM meta WHERE key='searchSpaceVersion'")
        store.set('searchSpaceVersion', 1)
    store.close()
    live = Optimizer(legacy_path)
    try:
        deadline = time.monotonic()+30
        while live.status()['state'] == 'Opening library' and time.monotonic() < deadline:
            time.sleep(.05)
        live.command('start', dict(workers=1, duty=1), wait=True)
        time.sleep(1.0)
        status = live.status()
    finally:
        live.command('close')
        live.thread.join(180)
    expect('a legacy library refuses a new search and says why',
           status['state'] != 'Running' and 'search-space version' in (status.get('error') or ''),
           f"state {status['state']}, error {status.get('error')!r}")

    print()
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, indent=1, default=str), encoding='utf-8')
    if failures:
        print('FAILURES:')
        for row in failures:
            print('  ' + row)
        return 1
    print('  the contract is reachable where it should be and refuses illegal and no-op proposals '
          'before any battle is spent')
    return 0


def _retrigger_formation(scenario, level):
    """Force a formation skill's trigger to `level` directly, bypassing the refusing setter."""
    probe = deepcopy(scenario)
    for unit in probe['ownUnits']:
        for index, skill_id in enumerate(unit['skills']):
            if contract.is_formation_skill(skill_id):
                unit['invocationLevels'][index] = level
    return probe


if __name__ == '__main__':
    sys.exit(main())
