"""Household pet membership: the native selected-owner -> owned ally monster rule.

CONFIRMED native paths (frozen libil2cpp.so; slices under RE-evidence/20260912-combat):

  * form.BattleForm$$CreateBossBattle 0x16a8fa0 builds the own Team: the selected conquest
    members, the accepted friends, then each selected member's household pets. At 0x16a9544 it
    calls ecs.MonsterSystem$$SearchAllyMonstersInHouse 0x15bede8 and clones every returned entity
    (kairo.unity.ecs.Entity$$Clone 0x147405c at 0x16a9684), appending in enumeration order. This
    path has no count cap, no random pet choice, no HP filter and no deduplication.
  * ecs.MonsterSystem$$SearchAllyMonstersInHouse 0x15bede8 returns nothing unless
    ecs.LandSystem$$IsHouseowner 0x15a4a3c holds for the owner (a Resident living in a house whose
    Land.owner is that same entity). It then walks Land.furnitures (Land +0x38) and, per furniture,
    ecs.FacilityComponent$$EnumerateAssignedStaffs 0x14c8fc4 -- MoveNext 0x14c9d5c keeps the
    assigned-staff array order and skips null slots -- keeping entities that carry BOTH the Ally and
    the Monster component (selector 0x15bf7e4 -> predicate 0x15bf900).
  * Roster order: selected-member order, then per-owner furniture/staff enumeration order.

Source contract
---------------
The production owner of the pure rule is `combat_household_pets` (same directory): `housePets` maps a
selected human house-owner name to that owner's COMPLETE ordered household lookup result, and a
selected human house owner with no entry is an error (`HOUSEHOLD_PETS_NOT_DECLARED`): "no pets" must
be declared explicitly with `[]`, never claimed by omission. There is no native-save importer and no
import/`source` flag, so nothing can bypass the complete-list contract. This check only pins the
decoded rule lines and exercises that production helper; it never defines a second rule copy.
"""
import json

from combat_initial_state import EVIDENCE
from combat_household_pets import (
    HouseholdPetsError,
    collect_household_pets,
    human_house_owners,
)

NATIVE = {
    'setup_caller': ('form.BattleForm$$CreateBossBattle', 0x16a8fa0),
    'pet_loop_call_site': 0x16a9544,
    'pet_clone_call_site': 0x16a9684,
    'membership_lookup': ('ecs.MonsterSystem$$SearchAllyMonstersInHouse', 0x15bede8),
    'owner_gate': ('ecs.LandSystem$$IsHouseowner', 0x15a4a3c),
    'selector': ('ecs.MonsterSystem.<>c$$<SearchAllyMonstersInHouse>b__36_0', 0x15bf7e4),
    'predicate': ('ecs.MonsterSystem.<>c$$<SearchAllyMonstersInHouse>b__36_1', 0x15bf900),
    'assigned_staffs': ('ecs.FacilityComponent$$EnumerateAssignedStaffs', 0x14c8fc4),
    'assigned_staffs_movenext': ('ecs.FacilityComponent.<EnumerateAssignedStaffs>d__56$$MoveNext',
                                 0x14c9d5c),
    'clone': ('kairo.unity.ecs.Entity$$Clone', 0x147405c),
}

# Each entry pins one decoded instruction that carries the rule: address prefix + operand token.
EVIDENCE_LINES = {
    '16a8fa0.asm': [('16a9544', '0x15bede8'), ('16a9684', '0x147405c')],
    '15bede8.asm': [('15bee50', '0x15a4a3c'), ('15bee8c', '#0x38'), ('15bef20', '0x18a0ca4')],
    '15bf7e4.asm': [('15bf858', '0x14c8fc4'), ('15bf8f8', '0x18abfac')],
    '15bf900.asm': [('15bf914', '0x146b824'), ('15bf928', '0x1470c84')],
    '14c9d5c.asm': [('14c9d80', '#0x40'), ('14c9dbc', '0x14c9de4')],
}


def evidence():
    """Assert the decoded rule lines exist in the frozen slices; return the native RVA table."""
    checked = {}
    for name, required in EVIDENCE_LINES.items():
        lines = (EVIDENCE / name).read_text(encoding='utf-8').splitlines()
        for address, token in required:
            hit = any(line.strip().startswith(address + ':') and token in line for line in lines)
            if not hit:
                raise AssertionError(f'{name}: missing {address} carrying {token}')
            checked[f'{name}:{address}'] = token
    return checked


if __name__ == '__main__':
    from copy import deepcopy
    from check_combat_sandbox import example
    from combat_scenario import load_scenario, ScenarioError

    rule = evidence()
    data = example()
    own = data['ownUnits']
    households = data['housePets']
    owner = human_house_owners(own)[0]
    pet = households[owner][0]

    # 1. The helper matches the loader's native append order for a declared household.
    helpers = collect_household_pets(own, households)
    normalized = load_scenario(deepcopy(data))
    appended = normalized['ownUnits'][len(own):]
    assert [u['name'] for u in appended] == [p['name'] for p in helpers]
    assert [u['petOwnerName'] for u in appended] == [p['petOwnerName'] for p in helpers]

    # 2. A selected house owner with no entry fails closed, in the helper and in the loader.
    for runner in (lambda: collect_household_pets(own, {}), lambda: load_scenario(deepcopy(dict(data, housePets={})))):
        try:
            runner()
        except (HouseholdPetsError, ScenarioError) as error:
            code = getattr(error, 'code', 'HOUSEHOLD_PETS_NOT_DECLARED')
            assert code == 'HOUSEHOLD_PETS_NOT_DECLARED', code
        else:
            raise AssertionError('Undeclared household accepted')
    assert collect_household_pets(own, {owner: []}) == []

    # 3. Order and multiplicity are preserved; there is no cap and no deduplication.
    three = [dict(pet, name=f'household pet {i}') for i in (1, 2, 3)]
    ordered = collect_household_pets(own, {owner: three})
    assert [p['name'] for p in ordered] == ['household pet 1', 'household pet 2', 'household pet 3']
    assert all(p['petOwnerName'] == owner for p in ordered)
    assert len(collect_household_pets(own, {owner: [pet, dict(pet)]})) == 2
    multi = deepcopy(data)
    multi['housePets'] = {owner: three}
    assert [u['name'] for u in load_scenario(multi)['ownUnits'][len(own):]] == \
        [p['name'] for p in ordered]

    # 4. Eligibility: only a selected human house owner and only ally monsters qualify.
    bystander = next(u['name'] for u in own if not (u.get('human') and u.get('isHouseOwner')))
    for households_bad, code in (
        ({'ghost owner': [pet]}, 'PET_OWNER_NOT_SELECTED_HUMAN'),
        ({bystander: [pet]}, 'HOUSE_PETS_REQUIRE_HOUSE_OWNER_HUMAN'),
        ({owner: [dict(pet, human=True, monsterId=None)]}, 'HOUSE_PET_NOT_ALLY_MONSTER'),
        ({owner: [dict(pet, monsterId=None)]}, 'HOUSE_PET_NOT_ALLY_MONSTER'),
    ):
        try:
            collect_household_pets(own, households_bad)
        except HouseholdPetsError as error:
            assert error.code == code, (error.code, code)
        else:
            raise AssertionError(f'{code} not raised')

    # 5. A roster with no declared house owner is unaffected: no pets are invented or appended.
    non_owner = deepcopy(data)
    non_owner['ownUnits'] = [u for u in non_owner['ownUnits'] if not u.get('isHouseOwner')]
    non_owner['housePets'] = {}
    assert load_scenario(non_owner)['ownUnits'] == non_owner['ownUnits']

    print(json.dumps(dict(schema='ka-combat-household-pets-check-1', ruleLines=len(rule),
                          owners=[owner], appendedPets=len(helpers), orderPreserved=True, dedup=False,
                          cap=None, explicitDeclarationRequired=True, importBypass=None,
                          limits='Pure contract/evidence check; no whole-fight proof and no UI wiring.')))
