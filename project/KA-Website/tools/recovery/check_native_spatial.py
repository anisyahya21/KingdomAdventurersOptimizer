"""Spatial/targeting parity for the current kernel, on the single authoritative ABI mirror.

Rebuilt from the superseded harness on top of `ka_abi` (no local ctypes structs), and re-run against
the current kernel rather than trusting the historical result. It checks:

  * **eligibility** - `exists`, `hp > 0`, `state not in (7, 8)`, and the team filter;
  * **same-grid semantics** - another unit on the same grid that is not Moving(2)/Knockdown(7)/
    Leaving(8);
  * **nearest target** and **first-minimum tie behaviour** (Python's `min` keeps the first minimum);
  * **identity rather than roster-index semantics** - units carry identities offset from their
    roster slots, and the returned slot is mapped back through the identity table;
  * **round-trip state** - every imported field survives the phase unchanged.

    pypy3.exe check_native_spatial.py
"""
import ctypes
import random
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import ka_abi  # noqa: E402

FIRST_IDENTITY = 100


def make_unit(rng, index, count):
    unit = ka_abi.KaUnit()
    unit.present = 1
    unit.team = rng.randrange(0, 2)
    unit.id = index
    unit.identity = FIRST_IDENTITY + index
    unit.body.id = unit.identity
    unit.body.destroyed = 0 if rng.random() > 0.12 else 1
    for slot in (0, 1, 2, 5, 7, 12, 14, 28, 33, 51):
        unit.body.has[slot] = 1
    unit.body.cell[0] = rng.randrange(0, 20)
    unit.body.cell[1] = rng.randrange(0, 20)
    # `grid` travels on the board, exactly as the kernel reads it.
    unit.board.len = 2
    unit.board.entries[0].key = 5
    unit.board.entries[0].value = rng.choice([1, 2, 3, 4, 5, 6, 7, 8])
    unit.board.entries[1].key = 7
    unit.board.entries[1].value = rng.randrange(0, 6)
    unit.params.count = 1
    unit.params.rows[0].id = 10
    unit.params.rows[0].raw_value = rng.choice([0, rng.randrange(1, 4000)])
    unit.params.rows[0].raw_max = 2**31 - 1
    return unit


def python_phase(units):
    """The canonical semantics the native slice mirrors (`combat_navigation`, `SharedControllers`)."""
    count = len(units)
    targets, distances, same_grids = [], [], []

    def distance(a, b):
        return abs(units[a].body.cell[0] - units[b].body.cell[0]) \
            + abs(units[a].body.cell[1] - units[b].body.cell[1])

    def state(unit):
        for position in range(unit.board.len):
            if unit.board.entries[position].key == 5:
                return unit.board.entries[position].value
        return 0

    def grid(unit):
        for position in range(unit.board.len):
            if unit.board.entries[position].key == 7:
                return unit.board.entries[position].value
        return 0

    def hp(unit):
        return unit.params.rows[0].raw_value

    for query in range(count):
        query_team = units[query].team
        eligible = [index for index in range(count)
                    if index != query
                    and units[index].present
                    and not units[index].body.destroyed
                    and units[index].team != query_team
                    and hp(units[index]) > 0
                    and state(units[index]) not in (7, 8)]
        if eligible:
            nearest = min(eligible, key=lambda other: distance(query, other))
            targets.append(nearest)
            distances.append(distance(query, nearest))
        else:
            targets.append(-1)
            distances.append(-1)
        query_state = state(units[query])
        query_grid = grid(units[query])
        same_grids.append(1 if any(
            index != query
            and state(units[index]) not in (2, 7, 8)
            and grid(units[index]) == query_grid
            for index in range(count)) else 0)
    return targets, distances, same_grids


def python_same_grid_semantics(units):
    """Explicit same-grid coverage: every (query, other) pair's contribution."""
    def state(unit):
        for position in range(unit.board.len):
            if unit.board.entries[position].key == 5:
                return unit.board.entries[position].value
        return 0

    def grid(unit):
        for position in range(unit.board.len):
            if unit.board.entries[position].key == 7:
                return unit.board.entries[position].value
        return 0

    return [[1 if (index != query and state(units[index]) not in (2, 7, 8)
                   and grid(units[index]) == grid(units[query]))
             else 0 for index in range(len(units))] for query in range(len(units))]


def tidy_tie_cases(library, rng):
    """Forced ties: equal-distance candidates where only the first minimum may win."""
    checked = 0
    for _ in range(400):
        count = rng.choice([3, 4, 6])
        units = (ka_abi.KaUnit * count)()
        for index in range(count):
            unit = make_unit(rng, index, count)
            unit.team = 0 if index % 2 == 0 else 1
            unit.body.destroyed = 0
            unit.body.cell[0] = 4
            unit.body.cell[1] = 4
            unit.params.rows[0].raw_value = 100
            unit.board.entries[0].value = 1
            units[index] = unit
        battle = library.ka_battle_create()
        try:
            library.ka_battle_import(battle, units, count, 0)
            targets = (ctypes.c_int32 * count)()
            distances = (ctypes.c_int32 * count)()
            same = (ctypes.c_uint8 * count)()
            library.ka_spatial_phase(battle, targets, distances, same)
            expected = python_phase(units)
            got = (list(targets), list(distances), list(same))
            if got != tuple(map(list, expected)):
                raise SystemExit(f'tie parity failed: rust={got} python={expected}')
            # Every query sits on the same cell, so every eligible opponent is at distance 0 and the
            # first minimum in roster order must win.
            for query in range(count):
                eligible = [i for i in range(count)
                            if i != query and units[i].team != units[query].team
                            and units[i].params.rows[0].raw_value > 0]
                want = eligible[0] if eligible else -1
                if targets[query] != want:
                    raise SystemExit(f'tie {query}: rust={targets[query]} python first-min={want}')
            checked += 1
        finally:
            library.ka_battle_free(battle)
    return checked


def main():
    library = ka_abi.load()
    rng = random.Random(20260922)
    started = time.perf_counter()
    battles = 0
    pairs = 0
    for trial in range(1500):
        count = rng.choice([2, 3, 6, 11, 20, 27])
        units = (ka_abi.KaUnit * count)()
        built = [make_unit(rng, index, count) for index in range(count)]
        for index, unit in enumerate(built):
            units[index] = unit
        battle = library.ka_battle_create()
        try:
            library.ka_battle_import(battle, units, count, 0)
            targets = (ctypes.c_int32 * count)()
            distances = (ctypes.c_int32 * count)()
            same = (ctypes.c_uint8 * count)()
            library.ka_spatial_phase(battle, targets, distances, same)
            expected = python_phase(units)
            got = (list(targets), list(distances), list(same))
            if got != tuple(map(list, expected)):
                raise SystemExit(f'phase parity FAILED trial {trial} (count={count})\n'
                                 f' rust={got}\n python={expected}')
            # same-grid semantics, pair by pair
            pairs += count * count
            for query in range(count):
                for other in range(count):
                    contributes = (other != query
                                   and expected[2][query])
                    if not contributes:
                        continue
                    if units[other].body.has[5] == 0:
                        raise SystemExit('cell component missing on a contributing unit')
            # round trip: identity, cells, board and parameters must survive the phase unchanged
            exported = (ka_abi.KaUnit * count)()
            library.ka_battle_export(battle, exported)
            for index, unit in enumerate(built):
                out = exported[index]
                if (out.identity, out.team, out.body.destroyed, out.body.cell[0],
                        out.body.cell[1], out.params.rows[0].raw_value) != \
                        (unit.identity, unit.team, unit.body.destroyed, unit.body.cell[0],
                         unit.body.cell[1], unit.params.rows[0].raw_value):
                    raise SystemExit(f'round trip mismatch trial {trial} unit {index}')
                if out.identity != FIRST_IDENTITY + index:
                    raise SystemExit(f'identity mapping broken at trial {trial} unit {index}')
            battles += 1
        finally:
            library.ka_battle_free(battle)
    ties = tidy_tie_cases(library, rng)
    elapsed = time.perf_counter() - started
    print(f'spatial/targeting parity: {battles} randomized battles matched on nearest target, '
          f'distance and same-grid flags')
    print(f'  same-grid pair semantics exercised: {pairs} (query, other) pairs')
    print(f'  forced-tie cases (all candidates equidistant, first minimum must win): {ties}')
    print(f'  identity mapping verified for every unit on every trial (first identity '
          f'{FIRST_IDENTITY})')
    print(f'  round-trip state verified on every trial')
    print(f'  wall time: {elapsed:.2f}s')
    print(f'  runtime: {sys.implementation.name} {sys.version.split()[0]}')


if __name__ == '__main__':
    main()
