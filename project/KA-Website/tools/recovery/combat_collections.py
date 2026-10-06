"""Recovered HashSet slot order for identity-keyed combat entities.

Models ordering and versioning, not comparer/hash-code generation or capacity.
Native AddIfNotPresent 1cd31a8, Remove 1cd1e68, MoveNext 1bcf0a8.
"""
from combat_initial_state import i32


class EntitySlotSet:
    def __init__(self):
        self.slots = []
        self.free = []
        self.indices = {}
        self.version = 0

    def add(self, value):
        if value in self.indices:
            return False
        if self.free:
            index = self.free.pop()
            self.slots[index] = value
        else:
            index = len(self.slots)
            self.slots.append(value)
        self.indices[value] = index
        self.version = i32(self.version + 1)
        return True

    def remove(self, value):
        if value not in self.indices:
            return False
        index = self.indices.pop(value)
        self.slots[index] = None
        self.free.append(index)
        if not self.indices:
            self.slots.clear()
            self.free.clear()
        self.version = i32(self.version + 1)
        return True

    def clear(self):
        self.slots.clear()
        self.free.clear()
        self.indices.clear()
        self.version = i32(self.version + 1)

    def __iter__(self):
        # Capture at GetEnumerator time, not the first MoveNext call.
        version = self.version
        def scan():
            index = 0
            while True:
                if version != self.version:
                    raise RuntimeError('collection modified during enumeration')
                if index >= len(self.slots):
                    return
                value = self.slots[index]
                # One hash probe: a slot is live exactly when its recorded index is this position.
                occupied = self.indices.get(value, -1) == index
                index += 1
                if occupied:
                    yield value
        return scan()


class EntitySlotDictionary:
    """Dictionary<long, entity> order; unlike HashSet, retains slots when empty."""
    def __init__(self):
        self.slots = []
        self.free = []
        self.indices = {}
        self.version = 0

    def insert(self, key, value, overwrite=False):
        # Native TryInsert increments even for a duplicate rejected insertion.
        self.version = i32(self.version + 1)
        if key in self.indices:
            if not overwrite:
                return False
            self.slots[self.indices[key]] = (key, value)
            return True
        if self.free:
            index = self.free.pop()
            self.slots[index] = (key, value)
        else:
            index = len(self.slots)
            self.slots.append((key, value))
        self.indices[key] = index
        return True

    def remove(self, key):
        if key not in self.indices:
            return False
        index = self.indices.pop(key)
        self.slots[index] = None
        self.free.append(index)
        self.version = i32(self.version + 1)
        return True

    def clear(self):
        self.slots.clear()
        self.free.clear()
        self.indices.clear()
        self.version = i32(self.version + 1)

    def values(self):
        version = self.version
        def scan():
            index = 0
            while True:
                if version != self.version:
                    raise RuntimeError('collection modified during enumeration')
                if index >= len(self.slots):
                    return
                entry = self.slots[index]
                index += 1
                if entry is not None:
                    yield entry[1]
        return scan()


class ComponentSubset:
    """Checked Subset.Add/Remove semantics; callbacks see native cache timing."""
    def __init__(self, required=(), excluded=(), on_added=None, on_removed=None, on_changed=None):
        self.required = tuple(required)
        self.excluded = tuple(excluded)
        self.members = EntitySlotSet()
        self.cache = None
        self.on_added = on_added
        self.on_removed = on_removed
        self.on_changed = on_changed

    def matches(self, unit):
        c = unit['components']
        # Same predicate as all(...) and not any(...), with the short-circuit written out so each
        # membership test avoids a generator resume; required/excluded order is unchanged.
        for t in self.required:
            if c[t] is None:
                return False
        for t in self.excluded:
            if c[t] is not None:
                return False
        return True

    def update(self, unit, component_type, component):
        key = unit['id']
        if self.matches(unit):
            self.members.add(key)
            self.cache = None
            if self.on_added is not None:
                self.on_added(unit, component_type, component)
        else:
            removed = self.members.remove(key)
            if removed and self.on_removed is not None:
                self.on_removed(unit, component_type, component)
            self.cache = None
