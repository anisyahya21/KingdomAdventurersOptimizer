"""Shared entity storage for composing checked combat phases, without rendering.

Callers supply initial counter, ordered subsets and source components. This is
the entity/lifecycle layer, not a full fight runner or a saved-game decoder.
"""
from combat_collections import EntitySlotDictionary
from combat_lifecycle import create_entity, destroy_entity
from collections.abc import MutableMapping, MutableSequence

COMPONENT_IDS = dict(Position=0, Speed=1, Seb=2, Depth=4, Cell=5, Image=7,
                     Animation=12, Direction=14, ModifyAnimation=19, AI=28, Garbage=32, Effect=38,
                     Parameter=33, Projectile=39, Attack=46, Skill=51)


def image_component(tex_ids=None, res_ids=None, tex_u=None, tex_v=None, tex_w=None, tex_h=None):
    # Native AddImage retains nonempty arrays by reference; missing/empty inputs
    # each receive their own one-element [-1] array.
    return {name: values if values else [-1] for name, values in zip(
        ('tex_ids', 'res_ids', 'tex_u', 'tex_v', 'tex_w', 'tex_h'),
        (tex_ids, res_ids, tex_u, tex_v, tex_w, tex_h))}


def materialize_component(component_type, component):
    if isinstance(component, tuple):
        if component_type == 7:
            # Scalar AddImage constructor order is texture, resource, U,V,W,H.
            return image_component(*([value] for value in component))
        return list(component)
    return component


class ComponentRecord(MutableMapping):
    """Named fields over a retained mutable component object."""
    def __init__(self, values, fields):
        self.values, self.fields = values, dict(zip(fields, range(len(fields))))

    def __getitem__(self, key):
        return self.values[self.fields[key]]

    def __setitem__(self, key, value):
        self.values[self.fields[key]] = value

    def __delitem__(self, key):
        raise TypeError('Component records cannot resize')

    def __iter__(self):
        return iter(self.fields)

    def __len__(self):
        return len(self.fields)


class ComponentMap(MutableMapping):
    """Entity-keyed access to a component's fields, window or scalar."""
    def __init__(self, entities, component, *, fields=None, window=None, scalar=None):
        assert sum(v is not None for v in (fields, window, scalar)) == 1
        self.entities, self.component = entities, component
        self.fields, self.window, self.scalar = fields, window, scalar

    def __getitem__(self, identity):
        values = self.entities.objects[identity]['components'][self.component]
        if self.scalar is not None:
            return values[self.scalar]
        if self.window is not None:
            return ComponentWindow(values, *self.window)
        return ComponentRecord(values, self.fields)

    def __setitem__(self, identity, value):
        if self.scalar is None:
            raise TypeError('Write through the component view')
        self.entities.objects[identity]['components'][self.component][self.scalar] = value

    def __delitem__(self, identity):
        raise TypeError('Remove the owning component explicitly')

    def __iter__(self):
        return (i for i, e in self.entities.objects.items() if e['components'][self.component] is not None)

    def __len__(self):
        return sum(1 for _ in self)


class ComponentWindow(MutableSequence):
    """A fixed-size view of one component object, retaining reference identity."""
    def __init__(self, values, start, length):
        self.values, self.start, self.length = values, start, length

    def __len__(self):
        return self.length

    def __getitem__(self, index):
        # `slice` cannot be subclassed, so the exact class test is the same predicate as
        # isinstance() without the protocol lookup on every fixed-window read.
        if index.__class__ is slice:
            return [self[i] for i in range(*index.indices(self.length))]
        if index < 0:
            index += self.length
        if not 0 <= index < self.length:
            raise IndexError(index)
        return self.values[self.start+index]

    def __setitem__(self, index, value):
        if index.__class__ is slice:
            indices = list(range(*index.indices(self.length)))
            values = list(value)
            if len(indices) != len(values):
                raise ValueError('Component field windows cannot resize')
            for i, v in zip(indices, values):
                self[i] = v
            return
        if index < 0:
            index += self.length
        if not 0 <= index < self.length:
            raise IndexError(index)
        self.values[self.start+index] = value

    def __delitem__(self, index):
        raise TypeError('Component field windows cannot resize')

    def insert(self, index, value):
        raise TypeError('Component field windows cannot resize')

    def __add__(self, other):
        return list(self)+list(other)

    def __eq__(self, other):
        return list(self) == list(other)


class FighterView(MutableMapping):
    """State helpers access original component owners through this view.

    AI is a dictionary of board/long_board/path/commands. Numeric components use
    mutable field arrays in recovered order. No duplicate state/frame/position.
    """
    ai_fields = ('board', 'long_board', 'path', 'commands')
    windows = dict(position=(0, 0, 3), offset=(0, 3, 3), velocity=(1, 0, 3), speed=(1, 0, 3), cell=(5, 0, 2))
    scalars = dict(direction=(14, 0), animation_rate=(12, 0),parent=(0,6))
    owned_fields = dict(parameters=(33, 'parameters'), parameter_extras=(33, 'extras'), skills=(51, 'dataIds'),
                        invocation_levels=(51, 'invocationLevels'),
                        invoking=(51, 'invokingSkills'))

    def __init__(self, entity):
        self.entity = entity

    def __getitem__(self, key):
        entry = _VIEW_DISPATCH.get(key)
        if entry is None:
            return self.entity[key]
        kind, payload = entry
        c = self.entity['components']
        if kind == 0:
            return c[28][payload]
        if kind == 1:
            return c[payload[0]][payload[1]]
        if kind == 2:
            return ComponentWindow(c[payload[0]], payload[1], payload[2])
        return c[payload[0]][payload[1]]

    def __setitem__(self, key, value):
        entry = _VIEW_DISPATCH.get(key)
        if entry is None:
            self.entity[key] = value
            return
        kind, payload = entry
        c = self.entity['components']
        if kind == 0:
            c[28][payload] = value
        elif kind == 1:
            c[payload[0]][payload[1]] = value
        elif kind == 2:
            self[key][:] = value
        else:
            c[payload[0]][payload[1]] = value

    def __delitem__(self, key):
        if key in self.ai_fields or key in self.windows or key in self.scalars or key in self.owned_fields:
            raise TypeError('Remove the owning component explicitly')
        del self.entity[key]

    def __iter__(self):
        return iter(dict.fromkeys((*self.entity, *self.ai_fields, *self.windows, *self.scalars, *self.owned_fields)))

    def __len__(self):
        return sum(1 for _ in self)

    def command_animation(self):
        return CommandAnimationView(self.entity)


def _build_view_dispatch(view):
    """``FighterView`` key -> ``(kind, payload)`` fused lookup.

    The original ``__getitem__``/``__setitem__`` scanned the ai tuple and three dicts in order
    (ai, owned, windows, scalars) on every attribute read. The four recovered tables are disjoint;
    building one dict in the same precedence order with first-wins keeps the exact branch each
    existing key takes while removing the repeated scans.
    """
    dispatch = {}
    for key in view.ai_fields:
        dispatch.setdefault(key, (0, key))
    for key, payload in view.owned_fields.items():
        dispatch.setdefault(key, (1, payload))
    for key, payload in view.windows.items():
        dispatch.setdefault(key, (2, payload))
    for key, payload in view.scalars.items():
        dispatch.setdefault(key, (3, payload))
    return dispatch


_VIEW_DISPATCH = _build_view_dispatch(FighterView)


class CommandAnimationView(MutableMapping):
    """ScrSkill's rate/frame refer to separate Animation and Seb components."""
    fields = dict(rate=(12, 0), frame=(2, 2))

    def __init__(self, entity):
        self.entity = entity

    def __getitem__(self, key):
        component, index = self.fields[key]
        return self.entity['components'][component][index]

    def __setitem__(self, key, value):
        component, index = self.fields[key]
        self.entity['components'][component][index] = value

    def __delitem__(self, key):
        raise TypeError('Remove the owning component explicitly')

    def __iter__(self):
        return iter(self.fields)

    def __len__(self):
        return len(self.fields)


class CombatEntities:
    def __init__(self, created_entities, ordered_subsets, on_destroyed=lambda unit: None):
        self.manager = {'created_entities': created_entities}
        self.index = EntitySlotDictionary()
        self.objects = {}  # Keep destroyed references for parent/command checks.
        self.fighter_views = {}
        self.subsets = list(ordered_subsets)
        self.on_destroyed = on_destroyed

    def fighter(self, identity):
        if identity not in self.fighter_views:
            self.fighter_views[identity] = FighterView(self.objects[identity])
        return self.fighter_views[identity]

    def allocate(self):
        def factory(entity_type, identity):
            if identity in self.objects:
                raise NotImplementedError('Identity wrap/collision requires object-reference keys')
            unit = dict(id=identity, flags=0, components=[None]*52, listeners=[None]*3)
            self.objects[identity] = unit
            return unit

        def register(unit, draft):
            assert not draft
            assert self.index.insert(unit['id'], unit)
            unit['listeners'][:] = [self._component_event, self._component_event, self._component_changed]

        return create_entity(self.manager, 0, False, factory, register)['id']

    def _component_event(self, unit, component_type, component):
        for subset in self.subsets:
            subset.update(unit, component_type, component)

    def _component_changed(self, unit, component_type, old, new):
        # EntityManager forwards this event to every subset without filtering
        # or refreshing membership/cache, unlike Added and Removed events.
        for subset in self.subsets:
            if subset.on_changed is not None:
                subset.on_changed(unit, component_type, old, new)

    def add_component(self, identity, component_type, component):
        if isinstance(component_type, str):
            component_type = COMPONENT_IDS[component_type]
        unit = self.objects[identity]
        if unit['components'][component_type] is not None:
            return  # BaseEntity.Add silently ignores an occupied slot.
        # Constructor tuples describe fields; the allocated native component is
        # mutable. Already mutable inputs retain their supplied object identity.
        component = materialize_component(component_type, component)
        unit['components'][component_type] = component
        listener = unit['listeners'][0]
        if listener is not None:
            listener(unit, component_type, component)

    def change_component(self, identity, component_type, component):
        if isinstance(component_type, str):
            component_type = COMPONENT_IDS[component_type]
        unit = self.objects[identity]
        old = unit['components'][component_type]
        if old is None:
            self.add_component(identity, component_type, component)
            return
        component = materialize_component(component_type, component)
        unit['components'][component_type] = component
        listener = unit['listeners'][2]
        if listener is not None:
            listener(unit, component_type, old, component)

    def remove_component(self, identity, component_type):
        unit = self.objects[identity]
        old = unit['components'][component_type]
        if old is not None:
            unit['components'][component_type] = None
            listener = unit['listeners'][1]
            if listener is not None:
                listener(unit, component_type, old)

    def destroy(self, identity):
        destroy_entity(self.objects[identity], self.on_destroyed, self._component_event,
                       lambda unit: self.index.remove(unit['id']))

    def is_destroyed(self, identity):
        return bool(self.objects[identity]['flags'] & 2)
