"""Native-owned battle state: differential parity and boundary economics.

Runs under the workers' own runtime (PyPy). Two questions:

  * does the native spatial/targeting slice reproduce the canonical Python semantics field for
    field on randomized battle states; and
  * what does the intended architecture cost - one create/import/export per battle, then thousands
    of ticks with no crossing at all.

The `KaUnit` layout now carries the native board model (`board[5]` state, `board[7]` grid), so the
struct definitions are imported from `check_native_fighter_step` rather than duplicated: one ABI
layout, one place to keep it honest.
"""
import ctypes
import random
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DLL = HERE / 'native' / 'ka_kernel' / 'target' / 'release' / 'ka_kernel.dll'

sys.path.insert(0, str(HERE))

from check_native_fighter_step import KaUnit, fill_board  # noqa: E402


def load():
    raise SystemExit(
        'SUPERSEDED: this spatial-slice harness was written against the pre-action `KaUnit`\n'
        'layout (flat `state`/`hp`/`grid` mirror fields). The kernel now stores those as real\n'
        'components (`board[5]`, `board[7]`, the `Parameter` table), so this loader would\n'
        'mis-parse the ABI. The spatial scan itself is unchanged and still exercised through\n'
        '`check_native_combat_actions.py`; this file is disabled deliberately, not left to\n'
        'compare against the wrong bytes.\n')


def _load_legacy():
    if not DLL.is_file():
        raise SystemExit('build first: cargo build --release in native/ka_kernel')
    lib = ctypes.CDLL(str(DLL))
    lib.ka_battle_create.restype = ctypes.c_void_p
    lib.ka_battle_import.argtypes = [ctypes.c_void_p, ctypes.POINTER(KaUnit), ctypes.c_uint32,
                                     ctypes.c_uint64]
    lib.ka_battle_import.restype = ctypes.c_uint32
    lib.ka_spatial_phase.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int32),
                                     ctypes.POINTER(ctypes.c_int32), ctypes.POINTER(ctypes.c_uint8)]
    lib.ka_spatial_phase.restype = ctypes.c_uint32
    lib.ka_spatial_phase_many.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                                          ctypes.POINTER(ctypes.c_int32),
                                          ctypes.POINTER(ctypes.c_int32),
                                          ctypes.POINTER(ctypes.c_uint8)]
    lib.ka_spatial_phase_many.restype = ctypes.c_uint32
    lib.ka_battle_export.argtypes = [ctypes.c_void_p, ctypes.POINTER(KaUnit)]
    lib.ka_battle_export.restype = ctypes.c_uint32
    lib.ka_battle_free.argtypes = [ctypes.c_void_p]
    return lib


def python_phase(units):
    """The canonical Python semantics of the ported slice, on dict-shaped state."""
    count = len(units)
    targets, distances, same_grids = [], [], []

    def distance(a, b):
        return abs(units[a]['cell_x'] - units[b]['cell_x']) \
            + abs(units[a]['cell_y'] - units[b]['cell_y'])

    for query in range(count):
        query_team = units[query]['team']
        eligible = [index for index in range(count)
                    if index != query
                    and units[index]['present']
                    and units[index]['exists']
                    and units[index]['team'] != query_team
                    and units[index]['hp'] > 0
                    and units[index]['state'] not in (7, 8)]
        if eligible:
            nearest = min(eligible, key=lambda other: distance(query, other))
            targets.append(nearest)
            distances.append(distance(query, nearest))
        else:
            targets.append(-1)
            distances.append(-1)
        unit = units[query]
        same_grids.append(1 if any(
            index != query
            and units[index]['state'] not in (2, 7, 8)
            and units[index]['grid'] == unit['grid']
            for index in range(count)) else 0)
    return targets, distances, same_grids


def random_units(rng, count):
    return [dict(present=1, exists=1, team=rng.randrange(0, 2),
                 state=rng.choice([1, 2, 3, 4, 5, 6, 7, 8]),
                 hp=rng.choice([0, rng.randrange(1, 4000)]), mp=rng.randrange(0, 200),
                 cell_x=rng.randrange(0, 20), cell_y=rng.randrange(0, 20),
                 grid=rng.randrange(0, 6), direction=rng.randrange(0, 4))
            for _ in range(count)]


# The fields the spatial slice reads, plus the ones it must leave untouched.
SPATIAL_FIELDS = ('present', 'exists', 'team', 'hp_value', 'cell_x', 'cell_y', 'direction',
                  'board', 'long_board')


def to_buffer(units):
    buffer = (KaUnit * len(units))()
    for index, unit in enumerate(units):
        target = buffer[index]
        target.present, target.exists = unit['present'], unit['exists']
        target.team = unit['team']
        target.hp_value = unit['hp']
        target.mp_value = unit['mp']
        target.cell_x, target.cell_y = unit['cell_x'], unit['cell_y']
        target.direction = unit['direction']
        fill_board(target.board, {5: unit['state'], 7: unit['grid']})
        fill_board(target.long_board, {})
    return buffer


def run_native(lib, units, iterations=1):
    count = len(units)
    battle = lib.ka_battle_create()
    buffer = to_buffer(units)
    lib.ka_battle_import(battle, buffer, count, 0x1234_5678_9abc_def0)
    targets = (ctypes.c_int32 * count)()
    distances = (ctypes.c_int32 * count)()
    same_grids = (ctypes.c_uint8 * count)()
    if iterations == 1:
        lib.ka_spatial_phase(battle, targets, distances, same_grids)
    else:
        lib.ka_spatial_phase_many(battle, iterations, targets, distances, same_grids)
    exported = (KaUnit * count)()
    lib.ka_battle_export(battle, exported)
    lib.ka_battle_free(battle)
    return (list(targets), list(distances), list(same_grids), exported)


def main():
    lib = load()
    rng = random.Random(20260922)

    battles = 0
    for trial in range(1500):
        count = rng.choice([2, 3, 6, 11, 20, 27])
        units = random_units(rng, count)
        expected = python_phase(units)
        got_targets, got_distances, got_same_grid, exported = run_native(lib, units)
        if (got_targets, got_distances, got_same_grid) != tuple(map(list, expected)):
            raise SystemExit(
                f'phase parity FAILED on trial {trial} (count={count})\n'
                f' targets rust={got_targets} python={expected[0]}\n'
                f' distances rust={got_distances} python={expected[1]}\n'
                f' same_grid rust={got_same_grid} python={expected[2]}\n units={units}')
        # Export round trip: every imported field is unchanged by the phase.
        for index, unit in enumerate(units):
            if (exported[index].present, exported[index].exists, exported[index].team,
                    exported[index].hp_value, exported[index].cell_x, exported[index].cell_y,
                    exported[index].direction) != (unit['present'], unit['exists'], unit['team'],
                                                   unit['hp'], unit['cell_x'], unit['cell_y'],
                                                   unit['direction']):
                raise SystemExit(f'export mismatch trial {trial} unit {index}')
            if exported[index].board.len != 2:
                raise SystemExit(f'board mismatch trial {trial} unit {index}')
        battles += 1
    print(f'differential parity: {battles} randomized battles matched on targets, distances, '
          f'same-grid flags and the exported board state')

    count = 27
    units = random_units(random.Random(11), count)
    buffer = to_buffer(units)
    targets = (ctypes.c_int32 * count)()
    distances = (ctypes.c_int32 * count)()
    same_grids = (ctypes.c_uint8 * count)()
    exported = (KaUnit * count)()

    per_battle_iterations = 5000
    started = time.perf_counter()
    for _ in range(per_battle_iterations):
        battle = lib.ka_battle_create()
        lib.ka_battle_import(battle, buffer, count, 1)
        lib.ka_spatial_phase(battle, targets, distances, same_grids)
        lib.ka_battle_export(battle, exported)
        lib.ka_battle_free(battle)
    boundary = time.perf_counter() - started
    print(f'boundary cost: {boundary/per_battle_iterations*1e6:.2f}us per '
          f'create+import+phase+export+free cycle')

    ticks = 7000
    battle = lib.ka_battle_create()
    lib.ka_battle_import(battle, buffer, count, 1)
    started = time.perf_counter()
    lib.ka_spatial_phase_many(battle, ticks, targets, distances, same_grids)
    native_ticks = time.perf_counter() - started
    lib.ka_battle_export(battle, exported)
    lib.ka_battle_free(battle)

    started = time.perf_counter()
    for _ in range(ticks):
        python_phase(units)
    python_ticks = time.perf_counter() - started

    print(f'{ticks}-tick spatial loop: native {native_ticks:.4f}s vs python {python_ticks:.4f}s '
          f'-> {python_ticks/native_ticks:.0f}x on the ported slice')
    battle_seconds = 1.5
    share = python_ticks / battle_seconds
    projected = battle_seconds - python_ticks + native_ticks
    print(f'ported slice = {share*100:.0f}% of a {battle_seconds}s battle; '
          f'if only this slice were native: {battle_seconds/projected:.2f}x whole-battle')
    print(f'runtime: {sys.implementation.name} {sys.version.split()[0]}')


if __name__ == '__main__':
    main()
