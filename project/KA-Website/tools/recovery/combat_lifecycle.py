"""Native lifetime expiry and entity removal ordering for the combat world."""
from combat_initial_state import i32


def create_entity(manager, entity_type, draft, create, add):
    """Native ID reservation occurs before the virtual creation callback."""
    identity = -1 if draft else manager['created_entities']
    if not draft:
        value = (identity + 1) & 0xffffffffffffffff
        manager['created_entities'] = value if value < 0x8000000000000000 else value - 0x10000000000000000
    entity = create(entity_type, identity)
    if entity is None:
        raise RuntimeError('native entity factory returned null')
    add(entity, bool(draft))
    return entity


def update_garbage(members, lifetimes, destroy):
    pending = []
    for entity in members:
        lifetimes[entity] = i32(lifetimes[entity] - 1)
        if lifetimes[entity] <= 0:
            pending.append(entity)
    for entity in pending:
        destroy(entity)


def destroy_entity(unit, on_destroyed, on_component_removed, remove_from_index):
    """Entity.Destroy -> MyEntityManager callback -> RemoveAll -> index removal.

    This models Entity.Destroy, not the manager's direct overload, which lacks
    the wrapper's final flag write. Callbacks run synchronously with fields intact.
    """
    if unit['flags'] & 2:
        return
    on_destroyed(unit)
    index = 0
    while index < len(unit['components']):
        component = unit['components'][index]
        if component is not None:
            unit['components'][index] = None
            if unit['listeners'][1] is not None:
                on_component_removed(unit, index, component)
        index += 1
    unit['listeners'][:] = [None, None, None]
    remove_from_index(unit)
    unit['flags'] |= 2
