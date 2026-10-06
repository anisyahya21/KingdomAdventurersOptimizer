"""Combat cell damage eligibility and snapshot boundaries."""
from combat_shared_resolution import shared_parameter_value


def opponent_in_skill_cells(opponents, cells, state, cell):
    """ExistsOpponentIsInRange: excludes states7/8, without an HP test."""
    return any(state(target) not in (7,8) and any(tuple(loc)==tuple(cell(target)) for loc in cells)
               for target in opponents)


def nearest_skill_target(caster, candidates, shooting_range, distance, predicate):
    """Human/Monster subset order supplied; strict minimum preserves first ties.

    Category predicate runs after self/range rejection. Skill eligibility is a
    separate later operation; callers must not silently skip an unusable winner.
    """
    chosen=None;best=2147483647
    for target in candidates:
        if target==caster:
            continue
        d=distance(caster,target)
        if d<=shooting_range and predicate(target) and d<best:
            chosen=target;best=d
    return chosen


def can_damage_shared(entities, attacker, target, skill, monster_is_boss,
                      is_main_world, monster_data):
    # Native null input raises; this is not the general alive-target predicate.
    target_components = entities.objects[target]['components']
    parameters = target_components[33]
    if parameters is None or 10 not in parameters['parameters']:
        return False
    if skill is not None and target_components[11] is not None:
        return False
    if shared_parameter_value(entities, target, 10) <= 0:
        return False
    monster = target_components[20] is not None
    if monster and monster_is_boss(target) and is_main_world(target):
        return False
    if (entities.objects[attacker]['components'][49] is not None) == (target_components[49] is not None):
        return False
    return not monster or monster_data(target)['type'] != 1


def damage_cell(occupants, eligible, attack, send):
    """DamageEntitiesOnCell: Any, repeat filter to snapshot, all attacks, Send26.

    occupants is the replayable cell collection obtained once by the caller.
    No eligible occupants means no message. Nested reactions during attack()
    can change later world state, but do not replace the target snapshot.
    """
    if not any(eligible(target) for target in occupants):
        return
    targets = [target for target in occupants if eligible(target)]
    results = [attack(target) for target in targets]
    send(26, results)
