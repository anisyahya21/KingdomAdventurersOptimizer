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
    the Monster component (selector 0x15bf7e4 -> predicate 0x15bf900; the predicate calls
    `get_hasAlly` 0x146b824 and `get_hasMonster` 0x1470c84).
  * Roster order: selected-member order, then per-owner furniture/staff enumeration order.

Source contract
---------------
`households` (scenario `housePets`) maps a selected human house-owner name to that owner's COMPLETE
ordered household lookup result; the caller supplies it and this module never infers it. Membership
is never inferred from proximity, name, monster id or stats. Every selected human house owner (a
selected unit with `human` true and `isHouseOwner` true) MUST carry an explicit entry, `[]` for an
explicit "no pets"; an absent entry is an error (`HOUSEHOLD_PETS_NOT_DECLARED`), never read as "no
pets". There is no native-save importer in this project, so there is deliberately NO import/source
flag that could bypass that contract: the complete list itself is the only accepted declaration.

Normalized form
---------------
`load_scenario` materializes a declaration into `ownUnits` (the pets become inline units tagged
`petOwnerName`, and `housePets` is consumed). A zero-pet owner would leave no trace, so the loader
also records the declared owner names in the scenario's `householdOwners` list. Those owners are
passed back as `materialized=` and need no `housePets` entry: their complete list is already inline
(or explicitly empty). This keeps re-normalizing a scenario idempotent without ever reading an
absent `housePets` entry as "no pets". Production code must never import a `check_combat_*.py` check.
"""
from copy import deepcopy


class HouseholdPetsError(ValueError):
    def __init__(self, code, message, path=None):
        super().__init__(message)
        self.code = code
        self.path = path


def is_ally_monster(pet):
    """Native predicate 0x15bf900: an entity carrying BOTH the Ally and the Monster component."""
    return isinstance(pet, dict) and not pet.get('human', True) and pet.get('monsterId') is not None


def human_house_owners(own_units):
    """Selected humans the native owner gate (0x15a4a3c) would accept, in roster order."""
    return [unit['name'] for unit in own_units if unit.get('human') and unit.get('isHouseOwner')]


def _inline_pets(own_units):
    """Owner name -> already-materialized inline pets (normalized `petOwnerName` units)."""
    inline = {}
    for unit in own_units:
        owner = unit.get('petOwnerName')
        if owner is not None:
            inline.setdefault(owner, []).append(unit)
    return inline


def _check_ally_monsters(owner, pets):
    for pet in pets:
        if is_ally_monster(pet):
            continue
        raise HouseholdPetsError('HOUSE_PET_NOT_ALLY_MONSTER',
                                 f"'{pet.get('name')}' is not an ally monster (native predicate "
                                 '0x15bf900 needs both Ally and Monster components).', pet.get('name'))


def validate_household_declaration(own_units, households, materialized=()):
    """Check the declared household collection against the native membership rule.

    Returns the declared owner names in native append order (selected roster order): owners with a
    `households` entry, owners whose pets are already inline, plus `materialized` owners (the
    loader's own normalized marker for zero-pet declarations). Raises HouseholdPetsError on any
    mismatch. An owner with no declaration, no inline list and no materialized marker is an error.
    """
    if not isinstance(households, dict):
        raise HouseholdPetsError('HOUSEHOLD_PETS_MALFORMED', 'housePets must map owner names to pets')
    if isinstance(materialized, (str, bytes)) or not isinstance(materialized, (list, tuple, set)):
        raise HouseholdPetsError('HOUSEHOLD_PETS_MALFORMED',
                                 'householdOwners must be a list of owner names')
    materialized = list(materialized)
    names = {unit['name'] for unit in own_units}
    owners = human_house_owners(own_units)
    inline = _inline_pets(own_units)
    for owner in materialized:
        if owner not in owners:
            raise HouseholdPetsError('HOUSE_PETS_REQUIRE_HOUSE_OWNER_HUMAN',
                                     f"Materialized household '{owner}' is not a selected human "
                                     'house owner.', owner)
    for owner, pets in households.items():
        if owner not in names:
            raise HouseholdPetsError('PET_OWNER_NOT_SELECTED_HUMAN',
                                     f"Pet owner '{owner}' is not a selected unit.", owner)
        if owner not in owners:
            raise HouseholdPetsError('HOUSE_PETS_REQUIRE_HOUSE_OWNER_HUMAN',
                                     f"Pet owner '{owner}' is not a selected human house owner.", owner)
        if owner in inline:
            raise HouseholdPetsError('HOUSEHOLD_PETS_ALREADY_MATERIALIZED',
                                     f"'{owner}' has both inline petOwnerName units and a household "
                                     'entry; declare the list exactly once.', owner)
        if not isinstance(pets, list):
            raise HouseholdPetsError('HOUSEHOLD_PETS_MALFORMED',
                                     f"'{owner}' must map to a list of pets", owner)
        _check_ally_monsters(owner, pets)
    for owner, pets in inline.items():
        if owner not in owners:
            raise HouseholdPetsError('HOUSE_PETS_REQUIRE_HOUSE_OWNER_HUMAN',
                                     f"Inline pet owner '{owner}' is not a selected human house owner.",
                                     owner)
        _check_ally_monsters(owner, pets)
    known = set(households) | set(inline) | set(materialized)
    missing = [owner for owner in owners if owner not in known]
    if missing:
        raise HouseholdPetsError(
            'HOUSEHOLD_PETS_NOT_DECLARED',
            'Selected human house owner(s) ' + ', '.join(missing) + ' have no declared household '
            'entry; declare the complete household list (use [] for an explicit "no pets"). An '
            'absent entry is never read as "no pets".', missing)
    return [owner for owner in owners if owner in known]


def collect_household_pets(own_units, households, materialized=()):
    """Return the pets the native path would append, ordered, with petOwnerName set.

    The result is a copy: this helper never mutates or appends to `own_units`. Order and
    multiplicity match the native enumeration (no sort, no cap, no deduplication). Already-inline
    and materialized owners contribute no new pets.
    """
    pets = []
    for owner in validate_household_declaration(own_units, households, materialized):
        for pet in households.get(owner, []):
            declared = deepcopy(pet)
            declared['petOwnerName'] = owner
            pets.append(declared)
    return pets
