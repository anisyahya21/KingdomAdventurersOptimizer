"""Independent synthetic team starts for stage-one combat discovery.

These are combat inputs, not claims that a real job/rank/loadout can reproduce them. The later
loadout matcher is deliberately separate. Every root is validated by the same search contract and
scenario adapter as an ordinary mutation.
"""
from __future__ import annotations

from copy import deepcopy
import random

import search_contract as contract
from strategy_optimizer_adapter import validate_scenario


NATIVE_TOTAL_FIGHTERS = 32
INITIAL_SIZES = (1, 2, 3, 4, 5)
ROLES = ('striker', 'healer', 'mage', 'bow', 'gun', 'spear', 'guard')
STARTER_ROLES = {
    1: ('striker',),
    2: ('striker', 'healer'),
    3: ('bow', 'gun', 'mage'),
    4: ('striker', 'healer', 'bow', 'gun'),
    5: ('spear', 'mage', 'bow', 'healer', 'guard'),
    6: ('striker', 'healer', 'guard', 'bow', 'gun', 'mage'),
}
ATTACK_STARTERS = (26, 110, 25, 24, 23, 22)


def maximum_team_size(enemy_count):
    """Native state holds 32 fighters in total, including the encounter's enemies."""
    return max(1, NATIVE_TOTAL_FIGHTERS-int(enemy_count))


def _ranged_weapon(weapon_type):
    groups = contract.weapon_groups()
    options = [(int(group['fields']['shootingRange']), min(group['weapons']))
               for group in groups.values()
               if int(group['fields']['type']) == weapon_type and group['weapons']]
    if not options:
        raise contract.ContractError(f'no recovered weapon group for type {weapon_type}')
    return max(options)[1]


def _roles(size, index):
    if index < len(STARTER_ROLES) and size in STARTER_ROLES:
        return list(STARTER_ROLES[size])
    # Rotate size, healer count, role composition and placement independently. The first roots are
    # readable tactics; later roots keep opening regions rather than cloning the best six-unit team.
    rng = random.Random(index * 65537 + size)
    healers = min(size, (index // max(1, size)) % 3)
    nonhealing = ('striker', 'mage', 'bow', 'gun', 'spear', 'guard')
    offset = index // 3
    roles = [nonhealing[(offset + i) % len(nonhealing)] for i in range(size-healers)]
    roles += ['healer'] * healers
    rng.shuffle(roles)
    return roles


def synthetic_root(base, index, enemy_count):
    """One deterministic, independently seeded roster of 1..native-cap synthetic humans."""
    limit = maximum_team_size(enemy_count)
    size = 1 + int(index) % limit
    originals = [unit for unit in base['ownUnits'] if unit.get('human')]
    if not originals:
        raise contract.ContractError('a synthetic root needs a human carrier template')
    striker = originals[0]
    healer = next((unit for unit in originals if any(
        skill in unit.get('skills', ()) for skill in (37, 38, 39))), striker)
    roles = _roles(size, int(index))
    probe = deepcopy(base)
    probe['ownUnits'] = []
    for position, role in enumerate(roles):
        unit = deepcopy(healer if role == 'healer' else striker)
        unit['name'] = f'Synthetic {position+1}'
        if role == 'striker':
            # A root opens a tactic with one attack, rather than copying the supplied six-skill
            # working example into every new fighter. Mutations can add combinations after evidence
            # says this attack/placement is worth refining.
            attack = ATTACK_STARTERS[(int(index)+position) % len(ATTACK_STARTERS)]
            if attack == 110 and sum(110 in other['skills'] for other in probe['ownUnits']) >= 2:
                attack = 26
            unit['skills'] = [attack]
            unit['invocationLevels'] = [0]
        else:
            role_skills = {
                'healer': (37, 107), 'mage': (5, 107),
                'bow': (107,), 'gun': (107,), 'spear': (107,),
                'guard': (27, 105),
            }[role]
            unit['skills'] = list(role_skills)
            unit['invocationLevels'] = [1 if skill in contract.FORMATION_SKILLS else 0
                                        for skill in role_skills]
        probe['ownUnits'].append(unit)
    # Weapon behaviour is a separate combat axis. Bow/gun normals are derived from the equipped
    # weapon; their rear placement comes from Backup. Spear range is preserved as its own group.
    for position, role in enumerate(roles):
        if role in ('bow', 'gun', 'spear'):
            weapon_type = {'bow': 8, 'gun': 7, 'spear': 4}[role]
            probe = contract.set_weapon(probe, f'Synthetic {position+1}',
                                        _ranged_weapon(weapon_type))
    contract.check_scenario(probe)
    return validate_scenario(probe), tuple(roles)
