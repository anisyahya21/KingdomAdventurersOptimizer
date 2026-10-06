"""Exact headless LOOP acceleration for the strategy optimiser.

This module is a *hanger*, not a combat implementation. It monkeypatches canonical combat
objects **only in the calling (headless worker) process** and restores them exactly on
`uninstall()`. No canonical source file is edited, so `strategy_optimizer_adapter.provenance()`
is byte-identical with and without the accelerator, and the reference engine is never silently
patched (nothing imports this module).

Two review findings from the 21 September Astra pass are fixed here:

  * The `ComponentSubset.update` early-out is **removed**. A write to an untracked component
    could not change `matches()`, but the canonical `update` still invalidates `cache` and still
    calls `on_added` on a matching unit; the early-out changed that callback/cache timing, which is
    canonical observable state ("callbacks see native cache timing"). The canonical method now runs
    unpatched, and `check_strategy_optimizer_loop_fast.py` pins the unrelated-write case with a
    non-`None` cache and a live `on_added`.
  * The opponent-roster cache no longer keys on a unit *count*. Counting cannot see a same-count
    roster replacement or a team change. The roster is built once per controller and the checker
    verifies, at every call over the whole corpus, that the cached list equals the live canonical
    comprehension.

Patches (each a loop/allocation reduction whose arithmetic and call order are preserved):

  * `FighterView.__getitem__`/`__setitem__` - the original scanned a 4-name tuple, two dicts and a
    further dict on every attribute read (millions of string comparisons per run). The patch fuses
    the four disjoint class tables into one dict built once at install, keeping the original
    precedence (ai, owned, windows, scalars, then entity fallback).
  * `ComponentWindow.__getitem__`/`__setitem__` - replace the `isinstance(index, slice)` protocol
    call with an exact class test and keep the bounds/IndexError (adjusted negative index) verbatim.
  * `SharedControllers.effective` - the source allocates a fresh `lambda` for the
    `equipment_contribution` callback on every call (~1.4M/1000-tick run). Pass a per-`human`
    callable instance instead; `fighter_parameter` invokes the callback positionally as
    `(row, parameter_id, level)` and the affinity/human arguments are unchanged.
  * `SharedControllers.opponent_in_range` - the per-team opponent roster is built once per
    controller instead of once per eligibility probe (12K/1000 ticks). `self.units` and
    `self.specs[i]['team']` are written only inside `SharedControllers.__init__`, and the checker
    re-derives the canonical list at every call to prove the cache is not stale.
  * `combat_navigation.queue_position` / `same_grid` - hoist `hp(other)` once per unit and bucket the
    column once instead of rescanning 5 columns x team; explicit short-circuiting loop with a hoisted
    `unit['board'][7]`. `combat_shared_controllers` binds these names with `from combat_navigation
    import ...`, so both the defining module and every importing module are patched by identity
    (`_patch_aliases`) - patching only the defining module would leave the real call sites unchanged.
  * `combat_animation.signed_remainder` / `frame_resource` - inline the two `i32` wraps as exact
    `& 0xFFFFFFFF` arithmetic (`x & 0xFFFFFFFF == x % 2**32` for every Python int) and the `abs`
    calls. Same values, fewer calls.
  * `combat_animation.update_animations` (also reachable through the `combat_shared_controllers`
    alias) - the global clip loop walks every manager row (~296 rows) once per tick purely to
    advance `resource['frame']`, and nothing in the engine ever reads that field: the only read and
    the only write of `resource['frame']` in the recovered engine is `frame_resource` itself. The
    patched call forwards indexing (`len`, `resources[res]`) unchanged for the Seb part but defers
    the global loop: iteration yields nothing and records how many advances are owed, and
    `materialize_resource_frames()` replays exactly that many canonical `frame_resource` calls on
    request. Fighter order, SEB arithmetic, callbacks, `enable`/`auto_animation`/
    `common_frames_update` gating and RNG are untouched, and no fighter is skipped.
  * `combat_collections.EntitySlotSet.__iter__` - one `indices.get(value, -1) == index` probe
    instead of `value in indices and indices[value] == index` per slot. The version guard that
    raises `RuntimeError('collection modified during enumeration')` is kept verbatim.

Timing must be warm and unprofiled; the parity check over the full canonical report (trace, RNG
final state, final state, all but `manifest`) is the equivalence proof, and `instrument_calls()`
provides per-patch call counts so a patch that is never reached reports zero instead of being
silently assumed to work.
"""
from __future__ import annotations

_MASK32 = 4294967295
_SIGN32 = 2147483648

_PATCHES = []  # (owner_object, attribute, original_value)
_ORIGINALS = {}

_HUMAN_CALLBACKS = {}

# id(resources) -> [resources, owed_frame_advances]; a strong reference to the list keeps the id
# from being recycled by a later controller in the same process.
_RESOURCE_LEDGERS = {}

_COUNTS = {}
_INSTRUMENTED = []
_MISSING = object()


class _Contribution:
    """Callable equivalent of the per-call `lambda row,p,level: equipment_contribution(...)`."""

    __slots__ = ("human",)

    def __init__(self, human):
        self.human = human

    def __call__(self, row, parameter_id, level):
        return _equipment_contribution(row, parameter_id, level,
                                       affinity=row["affinity"], human=self.human)


_equipment_contribution = None
_fighter_parameter = None
_i32 = None
_trunc_div = None
_opponent_in_skill_cells = None


def _setattr_once(owner, name, value):
    _PATCHES.append((owner, name, getattr(owner, name)))
    setattr(owner, name, value)


def _patch_aliases(modules, original, name, replacement):
    """Patch every module that bound `name` to `original` by identity.

    `from combat_navigation import queue_position` creates a second binding in the importing module,
    so patching only the defining module leaves the call site running the canonical object. Each
    alias is recorded in `_PATCHES` so `uninstall()` restores it exactly.
    """
    patched = 0
    for module in modules:
        if getattr(module, name, _MISSING) is original:
            _setattr_once(module, name, replacement)
            patched += 1
    return patched


class _LazyResources:
    """`animation_resources` proxy that defers the global clip-frame loop.

    `update_animations` iterates `resources` once only to advance every clip's `frame`; the per-fighter
    Seb part only indexes it. Indexing is forwarded unchanged, iteration yields nothing and records
    how many canonical `frame_resource` advances are owed.
    """

    __slots__ = ("_resources", "_advancing")

    def __init__(self, resources, advancing):
        self._resources = resources
        self._advancing = advancing

    def __len__(self):
        return len(self._resources)

    def __getitem__(self, index):
        return self._resources[index]

    def __iter__(self):
        if self._advancing:
            key = id(self._resources)
            entry = _RESOURCE_LEDGERS.get(key)
            if entry is None or entry[0] is not self._resources:
                _RESOURCE_LEDGERS[key] = [self._resources, 1]
            else:
                entry[1] += 1
        return iter(())


def pending_resource_advances(resources):
    """Advances owed to `resources` since install/last materialisation (0 when not tracked)."""
    entry = _RESOURCE_LEDGERS.get(id(resources))
    if entry is None or entry[0] is not resources:
        return 0
    return entry[1]


def materialize_resource_frames(resources):
    """Replay the owed canonical `frame_resource` calls so `resource['frame']` is exact.

    Exact by construction: the same canonical function is applied the same number of times, in the
    same manager/row order, as the skipped per-tick loop would have. Returns the advances applied.
    """
    entry = _RESOURCE_LEDGERS.get(id(resources))
    if entry is None or entry[0] is not resources:
        return 0
    ticks = entry[1]
    if not ticks:
        return 0
    advance = _ORIGINALS["frame_resource"]
    for manager in resources:
        if manager is not None:
            for resource in manager:
                if resource is not None:
                    for _ in range(ticks):
                        advance(resource, False, True)
    entry[1] = 0
    return ticks


def _install_entities(entities):
    def window_getitem(self, index):
        if index.__class__ is not slice:
            if index < 0:
                index += self.length
            if 0 <= index < self.length:
                return self.values[self.start + index]
            raise IndexError(index)
        return [self[i] for i in range(*index.indices(self.length))]

    def window_setitem(self, index, value):
        if index.__class__ is not slice:
            if index < 0:
                index += self.length
            if not 0 <= index < self.length:
                raise IndexError(index)
            self.values[self.start + index] = value
            return
        indices = list(range(*index.indices(self.length)))
        values = list(value)
        if len(indices) != len(values):
            raise ValueError("Component field windows cannot resize")
        for i, v in zip(indices, values):
            self[i] = v

    _setattr_once(entities.ComponentWindow, "__getitem__", window_getitem)
    _setattr_once(entities.ComponentWindow, "__setitem__", window_setitem)

    view = entities.FighterView
    dispatch = {}
    for key in view.ai_fields:
        dispatch.setdefault(key, (0, key))
    for key, payload in view.owned_fields.items():
        dispatch.setdefault(key, (1, payload))
    for key, payload in view.windows.items():
        dispatch.setdefault(key, (2, payload))
    for key, payload in view.scalars.items():
        dispatch.setdefault(key, (3, payload))

    def view_getitem(self, key):
        entry = dispatch.get(key)
        if entry is None:
            return self.entity[key]
        kind, payload = entry
        components = self.entity["components"]
        if kind == 0:
            return components[28][payload]
        if kind == 1:
            return components[payload[0]][payload[1]]
        if kind == 2:
            return entities.ComponentWindow(components[payload[0]], payload[1], payload[2])
        return components[payload[0]][payload[1]]

    def view_setitem(self, key, value):
        entry = dispatch.get(key)
        if entry is None:
            self.entity[key] = value
            return
        kind, payload = entry
        components = self.entity["components"]
        if kind == 0:
            components[28][payload] = value
        elif kind == 1:
            components[payload[0]][payload[1]] = value
        elif kind == 2:
            self[key][:] = value
        else:
            components[payload[0]][payload[1]] = value

    _setattr_once(view, "__getitem__", view_getitem)
    _setattr_once(view, "__setitem__", view_setitem)


def _install_controllers(controllers):
    def effective(self, i, p, maximum, default=0):
        spec = self.specs[i]
        human = spec["human"]
        callback = _HUMAN_CALLBACKS.get(human)
        if callback is None:
            callback = _HUMAN_CALLBACKS[human] = _Contribution(human)
        return _fighter_parameter(p, self.units[i]["parameters"].get(p), spec["equipmentRows"],
                                  callback, human=human, ally=spec["team"] == 0,
                                  maximum=maximum, default=default)

    def opponent_in_range(self, i, s):
        opponents = getattr(self, "_loop_fast_opponents", None)
        if opponents is None:
            opponents = {team: [target for target in self.units if self.specs[target]["team"] != team]
                         for team in (0, 1)}
            self._loop_fast_opponents = opponents
        return _opponent_in_skill_cells(opponents[self.specs[i]["team"]], self.skill_cells(i, s),
            lambda t: self.units[t]["board"][5], lambda t: self.units[t]["cell"])

    _setattr_once(controllers.SharedControllers, "effective", effective)
    _setattr_once(controllers.SharedControllers, "opponent_in_range", opponent_in_range)


def _install_navigation(navigation, modules):
    def queue_position(unit, team, hp, row_offset):
        counts = [0, 0, 0, 0, 0]
        for other in team:
            if hp(other) > 0:
                board7 = other["board"][7]
                column = ((board7 - _trunc_div(board7, 5) * 5 + _SIGN32) & _MASK32) - _SIGN32
                if 0 <= column < 5:
                    counts[column] += 1
        column = min(range(5), key=counts.__getitem__)
        row = counts[column] + 1
        y = row_offset + 1 + row if unit["board"][6] == 0 else row_offset - row
        return column * 24, y * 24

    def same_grid(unit, team):
        board7 = unit["board"][7]
        for other in team:
            if other is not unit and other["board"][5] not in (2, 7, 8) and other["board"][7] == board7:
                return True
        return False

    original_queue_position = navigation.queue_position
    original_same_grid = navigation.same_grid
    _setattr_once(navigation, "queue_position", queue_position)
    _setattr_once(navigation, "same_grid", same_grid)
    _patch_aliases(modules, original_queue_position, "queue_position", queue_position)
    _patch_aliases(modules, original_same_grid, "same_grid", same_grid)


def _install_animation(animation, modules):
    def signed_remainder(value, divisor):
        if divisor == 0:
            quotient = 0
        else:
            quotient = (value if value >= 0 else -value) // (divisor if divisor > 0 else -divisor)
            if (value < 0) != (divisor < 0):
                quotient = -quotient
        quotient = ((quotient + _SIGN32) & _MASK32) - _SIGN32
        return ((value - quotient * divisor + _SIGN32) & _MASK32) - _SIGN32

    def frame_resource(resource, auto_animation, common_frames_update):
        if not auto_animation and common_frames_update:
            resource["frame"] = signed_remainder(
                ((resource["frame"] + 1 + _SIGN32) & _MASK32) - _SIGN32, resource["max_frame"])

    _ORIGINALS["frame_resource"] = animation.frame_resource
    _setattr_once(animation, "signed_remainder", signed_remainder)
    _setattr_once(animation, "frame_resource", frame_resource)

    original_update_animations = animation.update_animations

    def update_animations(members, entities, resources, state, change_animation):
        proxy = _LazyResources(resources, bool(state["enabled"] and not state["auto_animation"]
                                               and state["common_frames_update"]))
        return original_update_animations(members, entities, proxy, state, change_animation)

    _setattr_once(animation, "update_animations", update_animations)
    _patch_aliases(modules, original_update_animations, "update_animations", update_animations)


def _install_collections(collections):
    def slot_iter(self):
        version = self.version
        slots, indices = self.slots, self.indices

        def scan():
            index = 0
            while True:
                if version != self.version:
                    raise RuntimeError("collection modified during enumeration")
                if index >= len(slots):
                    return
                value = slots[index]
                owned = indices.get(value, -1) == index
                index += 1
                if owned:
                    yield value

        return scan()

    _setattr_once(collections.EntitySlotSet, "__iter__", slot_iter)


def install():
    """Patch the canonical combat objects in this process; returns the number of patches applied."""
    if _PATCHES:
        raise RuntimeError("strategy_optimizer_loop_fast is already installed")
    import combat_animation
    import combat_collections
    import combat_entities
    import combat_navigation
    import combat_parameters
    import combat_shared_controllers
    import combat_targeting

    modules = (combat_animation, combat_collections, combat_entities, combat_navigation,
               combat_parameters, combat_shared_controllers, combat_targeting)

    global _equipment_contribution, _fighter_parameter, _i32, _trunc_div, _opponent_in_skill_cells
    _equipment_contribution = combat_parameters.equipment_contribution
    _fighter_parameter = combat_parameters.fighter_parameter
    _i32 = combat_navigation.i32
    _trunc_div = combat_navigation.trunc_div
    _opponent_in_skill_cells = combat_targeting.opponent_in_skill_cells
    _HUMAN_CALLBACKS.clear()
    _RESOURCE_LEDGERS.clear()

    _install_entities(combat_entities)
    _install_controllers(combat_shared_controllers)
    _install_navigation(combat_navigation, modules)
    _install_animation(combat_animation, modules)
    _install_collections(combat_collections)
    return len(_PATCHES)


def uninstall():
    """Restore every patched attribute to its original object; returns the number restored."""
    restored = 0
    _INSTRUMENTED.clear()
    _COUNTS.clear()
    _RESOURCE_LEDGERS.clear()
    while _PATCHES:
        owner, name, original = _PATCHES.pop()
        setattr(owner, name, original)
        restored += 1
    return restored


def installed():
    return bool(_PATCHES)


def instrument_calls():
    """Wrap every installed patch in a counter so call counts prove each patch is actually reached."""
    if not _PATCHES:
        raise RuntimeError("nothing is installed to instrument")
    for owner, name, original in list(_PATCHES):
        current = getattr(owner, name, None)
        if not callable(current):
            continue
        label = "%s.%s" % (getattr(owner, "__qualname__", getattr(owner, "__name__", str(owner))), name)
        if label in _COUNTS:
            continue
        counter = [0]

        def wrapper(*args, __fn=current, __counter=counter, **kwargs):
            __counter[0] += 1
            return __fn(*args, **kwargs)

        wrapper.__name__ = getattr(current, "__name__", name)
        _INSTRUMENTED.append((owner, name, current))
        _COUNTS[label] = counter
        setattr(owner, name, wrapper)
    return sorted(_COUNTS)


def call_counts():
    """Call counts recorded since `instrument_calls()` (or since the last `install`)."""
    return {label: counter[0] for label, counter in _COUNTS.items()}


def restore_instrumentation():
    """Undo `instrument_calls()` so a timed loop is never measured through the count wrappers."""
    restored = 0
    while _INSTRUMENTED:
        owner, name, previous = _INSTRUMENTED.pop()
        setattr(owner, name, previous)
        restored += 1
    _COUNTS.clear()
    return restored


class accelerated:
    """Context manager: install on entry, always uninstall on exit."""

    def __enter__(self):
        install()
        return self

    def __exit__(self, *exc):
        uninstall()
        return False


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Install/uninstall the exact headless loop accelerator.")
    parser.add_argument("action", choices=("install", "check"))
    args = parser.parse_args()
    if args.action == "install":
        print(f"patches={install()}")
    else:
        import combat_entities
        import combat_navigation
        import combat_shared_controllers
        before = (combat_entities.FighterView.__getitem__, combat_navigation.queue_position,
                  combat_shared_controllers.queue_position,
                  combat_shared_controllers.update_animations)
        install()
        during = (combat_entities.FighterView.__getitem__, combat_navigation.queue_position,
                  combat_shared_controllers.queue_position,
                  combat_shared_controllers.update_animations)
        assert during != before
        assert combat_shared_controllers.queue_position is combat_navigation.queue_position, \
            "imported alias still points at the canonical queue_position"
        uninstall()
        after = (combat_entities.FighterView.__getitem__, combat_navigation.queue_position,
                 combat_shared_controllers.queue_position,
                 combat_shared_controllers.update_animations)
        assert after == before, "uninstall did not restore the canonical objects"
        print("install/uninstall restores the canonical objects, aliases included")


if __name__ == "__main__":
    main()
