"""Parity and speed check for the isolated native kernel, run under the workers' own runtime.

Two questions, both answered against the canonical Python semantics rather than against a guess:

  * does the Rust kernel reproduce the recovered Python scan exactly, including the first-minimum
    tie-break and the i32/f32 primitives; and
  * how much of the measured hot loop would actually be removed if this loop were native.

Run it with the pinned PyPy runtime (`pypy3.exe check_native_kernel.py`) because that is what the
optimiser's bulk workers use; ctypes behaves differently enough between CPython and PyPy that a
CPython-only result would not transfer.
"""
import ctypes
import random
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DLL = HERE / 'native' / 'ka_kernel' / 'target' / 'release' / 'ka_kernel.dll'


def load_library():
    if not DLL.is_file():
        raise SystemExit(f'build it first: cargo build --release in {DLL.parent.parent}')
    library = ctypes.CDLL(str(DLL))
    library.ka_manhattan.argtypes = [ctypes.c_int32]*4
    library.ka_manhattan.restype = ctypes.c_int32
    library.ka_i32_wrap.argtypes = [ctypes.c_int64]
    library.ka_i32_wrap.restype = ctypes.c_int32
    library.ka_f32_to_int.argtypes = [ctypes.c_float]
    library.ka_f32_to_int.restype = ctypes.c_int32
    library.ka_nearest_eligible.argtypes = [
        ctypes.POINTER(ctypes.c_int32), ctypes.POINTER(ctypes.c_uint8),
        ctypes.POINTER(ctypes.c_int32), ctypes.POINTER(ctypes.c_int32),
        ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t, ctypes.c_size_t,
        ctypes.c_uint8, ctypes.POINTER(ctypes.c_int32)]
    library.ka_nearest_eligible.restype = ctypes.c_size_t
    return library


def python_scan(cells, present, hp, state, team, query, query_team):
    """The canonical Python scan, written to mirror the recovered functions exactly."""
    eligible = []
    for index in range(len(hp)):
        if index == query:
            continue
        if team[index] == query_team:          # same_team
            continue
        if not present[index]:                 # exists
            continue
        if hp[index] <= 0:
            continue
        if state[index] in (7, 8):             # KnockingDown / Leaving
            continue
        eligible.append(index)
    if not eligible:
        return 0, -1
    # min(..., key=distance): first minimum in iteration order, ties keep the earlier index.
    def distance(other):
        return abs(cells[query*2] - cells[other*2]) + abs(cells[query*2+1] - cells[other*2+1])
    nearest = min(eligible, key=distance)
    return len(eligible), nearest


def random_field(rng, count):
    cells = [rng.randrange(0, 480) for _ in range(count*2)]
    present = [rng.random() > 0.15 for _ in range(count)]
    hp = [rng.choice([0, rng.randrange(1, 4000)]) for _ in range(count)]
    state = [rng.choice([1, 2, 3, 4, 5, 6, 7, 8]) for _ in range(count)]
    team = [rng.randrange(0, 2) for _ in range(count)]
    return cells, present, hp, state, team


def main():
    library = load_library()

    # --- primitive parity -------------------------------------------------
    for value in (0, 1, -1, 2**31 - 1, -(2**31), 2**31, -(2**31) - 1, 2**32 + 7):
        expected = ctypes.c_int32(value).value
        got = library.ka_i32_wrap(value)
        assert got == expected, (value, got, expected)
    for left, right in ((3, 5), (5, 3), (0, 0), (-4, 9), (300, 7)):
        # |ax-bx| + |ay-by|: the recovered distance sums both axes, so the y term is part of it.
        assert library.ka_manhattan(left, 2, right, 9) == abs(left-right) + abs(2-9)
    for value in (0.0, 1.9, -1.9, 2.5, -2.5, 1e9, -1e9):
        expected = int(value.__trunc__()) if hasattr(value, '__trunc__') else int(value)
        assert library.ka_f32_to_int(value) == expected, (value, library.ka_f32_to_int(value))
    print('primitive parity: i32 wrap, manhattan, f32 truncation OK')

    # --- scan parity ------------------------------------------------------
    rng = random.Random(20260922)
    checked = 0
    for trial in range(4000):
        count = rng.choice([2, 3, 6, 11, 20, 27])
        cells, present, hp, state, team = random_field(rng, count)
        query = rng.randrange(count)
        query_team = team[query]
        expected_count, expected_index = python_scan(cells, present, hp, state, team, query, query_team)
        buf_index = ctypes.c_int32(-1)
        got_count = library.ka_nearest_eligible(
            (ctypes.c_int32*len(cells))(*cells),
            (ctypes.c_uint8*count)(*[1 if flag else 0 for flag in present]),
            (ctypes.c_int32*count)(*hp),
            (ctypes.c_int32*count)(*state),
            (ctypes.c_uint8*count)(*team),
            count, query, query_team, ctypes.byref(buf_index))
        if (got_count, buf_index.value) != (expected_count, expected_index):
            raise SystemExit(
                f'scan parity FAILED on trial {trial}: rust=({got_count},{buf_index.value}) '
                f'python=({expected_count},{expected_index})\n'
                f'cells={cells} hp={hp} state={state} team={team} query={query}')
        checked += 1
    print(f'scan parity: {checked} random fields matched exactly (count and nearest index)')

    # --- speed ------------------------------------------------------------
    count = 27           # representative field: 6 allies + 21 enemies
    cells, present, hp, state, team = random_field(random.Random(7), count)
    present_buf = (ctypes.c_uint8*count)(*[1 if flag else 0 for flag in present])
    cells_buf = (ctypes.c_int32*len(cells))(*cells)
    hp_buf = (ctypes.c_int32*count)(*hp)
    state_buf = (ctypes.c_int32*count)(*state)
    team_buf = (ctypes.c_uint8*count)(*team)
    out = ctypes.c_int32()
    nearest = library.ka_nearest_eligible

    iterations = 300000
    # Vary the query unit every iteration: benchmark a constant field and PyPy is entitled to hoist
    # the whole computation out of the loop, which measures nothing.
    queries = [index % count for index in range(iterations)]
    checksum = 0
    started = time.perf_counter()
    for query in queries:
        nearest(cells_buf, present_buf, hp_buf, state_buf, team_buf, count, query, team[query],
                ctypes.byref(out))
    native_seconds = time.perf_counter() - started

    started = time.perf_counter()
    for query in queries:
        checksum += python_scan(cells, present, hp, state, team, query, team[query])[0]
    python_seconds = time.perf_counter() - started

    # Bare call cost: one ctypes round trip that does almost no work, to separate the price of
    # crossing the boundary from the price of the kernel behind it.
    started = time.perf_counter()
    for _ in range(iterations):
        library.ka_manhattan(1, 2, 3, 4)
    call_seconds = time.perf_counter() - started

    print(f'scan speed: native {native_seconds:.3f}s vs python {python_seconds:.3f}s for '
          f'{iterations} scans -> {python_seconds/native_seconds:.1f}x')
    print(f'per scan: native {native_seconds/iterations*1e6:.2f}us  python {python_seconds/iterations*1e6:.2f}us')
    print(f'bare ctypes call: {call_seconds/iterations*1e6:.2f}us per call '
          f'(checksum {checksum})')
    print(f'runtime under test: {sys.implementation.name} {sys.version.split()[0]}')


if __name__ == '__main__':
    main()
