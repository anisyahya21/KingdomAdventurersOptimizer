"""Real continuous multi-step parity, with checkpoints, against the canonical engine.

Two things are tested, both from real captured canonical battle states:

  * **persistent per-step execution** - one native battle is stepped `ka_update_fighters` repeatedly
    with no re-import, and the complete canonical snapshot is compared after *every* step, so a
    divergence is localised to the exact tick rather than only observed at the end;
  * **internally-looped execution** - `ka_run_native_steps(battle, state, H)` runs the same H steps
    with a single Python call, and the final snapshot must match Python's H steps exactly. This is
    what proves state survives across repeated native ticks without ABI traffic.

Horizons 1, 2, 8, 30 and 100 (and longer where the ported mechanics permit). Execution stops at the
first genuinely unported global mechanic and reports it instead of approximating.

    pypy3.exe check_native_multistep.py
"""
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import ka_abi  # noqa: E402
import ka_events  # noqa: E402
from check_native_real_state import CASES, Checkpoints, tick_step  # noqa: E402
from combat_sandbox import run_scenario  # noqa: E402
from combat_shared_controllers import ROWS  # noqa: E402
from combat_animation_selection import HUMAN_BASES  # noqa: E402
from strategy_optimizer_adapter import default_scenario  # noqa: E402

HORIZONS = (1, 2, 8, 30, 100)


def start_ticks(horizon):
    """Four spread start points per case, clamped to the case's own horizon."""
    wanted = [1, min(40, horizon), min(200, horizon), min(600, horizon)]
    return sorted({tick for tick in wanted if tick >= 1})


def run_horizon(library, engine, pre, horizon, record, label):
    """Step both sides `horizon` times, comparing every checkpoint; returns the ticks compared."""
    battle = ka_abi.load_snapshot(library, pre)
    compared = 0
    try:
        alive = True
        for step in range(horizon):
            trace_start = len(engine.trace)
            tick_step(engine)
            status = library.ka_native_tick(battle, pre["config"]["battle_state"])
            expected = ka_abi.engine_snapshot(engine, ROWS, pre['config']['battle_state'],
                                              HUMAN_BASES)
            if status != 0:
                record['unsupported'].append(dict(label=label, step=step + 1, status=status,
                                                  marks=sorted(record['marks'])))
                alive = False
                break
            got = ka_abi.native_snapshot(library, battle, pre)
            diffs = ka_abi.diff_snapshots(expected, got)
            expected_events = ka_events.normalize_python(engine.trace[trace_start:])
            native_events = ka_events.normalize_native(ka_abi.native_events(library, battle))
            if expected_events != native_events:
                record['event_failures'].append(dict(label=label, step=step + 1,
                                                     python=expected_events[:12],
                                                     native=native_events[:12],
                                                     python_count=len(expected_events),
                                                     native_count=len(native_events)))
                break
            record['events_compared'] += len(expected_events)
            record['event_kinds'].update(kind for kind, _, _, _ in expected_events)
            if diffs:
                record['failures'].append(dict(label=label, step=step + 1, diffs=diffs[:20],
                                               python_state={u['identity']: u['board'].get(5)
                                                             for u in expected['units']}))
                alive = False
                break
            compared += 1
        if alive:
            record['completed'].append(dict(label=label, horizon=horizon, steps=compared))
    finally:
        library.ka_battle_free(battle)
    return compared


def run_internal(library, engine, pre, horizon, record, label):
    """`ka_run_native_steps` for the whole horizon, then one final full comparison."""
    battle = ka_abi.load_snapshot(library, pre)
    try:
        cumulative = []
        for _ in range(horizon):
            trace_start = len(engine.trace)
            tick_step(engine)
            cumulative.extend(engine.trace[trace_start:])
        completed = __import__('ctypes').c_uint32()
        status = __import__('ctypes').c_int32()
        library.ka_run_native_steps(battle, pre['config']['battle_state'], horizon,
                                    __import__('ctypes').byref(completed),
                                    __import__('ctypes').byref(status))
        expected = ka_abi.engine_snapshot(engine, ROWS, pre['config']['battle_state'], HUMAN_BASES)
        if status.value != 0:
            record['internal_unsupported'].append(dict(label=label, step=completed.value + 1,
                                                       status=status.value))
            return 0
        got = ka_abi.native_snapshot(library, battle, pre)
        expected_events = ka_events.normalize_python(cumulative)
        native_events = ka_events.normalize_native(ka_abi.native_events(library, battle))
        if expected_events != native_events:
            record['event_failures'].append(dict(label=f'{label} [internal]', step=horizon,
                                                 python=expected_events[:12],
                                                 native=native_events[:12],
                                                 python_count=len(expected_events),
                                                 native_count=len(native_events)))
            return 0
        record['events_compared'] += len(expected_events)
        record['event_kinds'].update(kind for kind, _, _, _ in expected_events)
        diffs = ka_abi.diff_snapshots(expected, got)
        if diffs:
            record['failures'].append(dict(label=f'{label} [internal]', step=horizon,
                                           diffs=diffs[:20]))
            return 0
        record['internal_ok'].append(dict(label=label, horizon=horizon))
        return completed.value
    finally:
        library.ka_battle_free(battle)


def main():
    library = ka_abi.load()
    base = default_scenario()
    record = dict(failures=[], unsupported=[], internal_unsupported=[], completed=[],
                  internal_ok=[], marks=set(), event_failures=[], event_kinds=set(),
                  events_compared=0)
    max_horizon = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    started = time.perf_counter()
    for case in CASES:
        horizon = case['tickLimit']
        starts = start_ticks(horizon)
        feasible = [tick for tick in starts if tick + max_horizon <= horizon]
        checkpoints = Checkpoints(feasible)
        run_scenario(dict(base, **case), checkpoints=checkpoints,
                     stop_tick=max(feasible) if feasible else None)
        for start in feasible:
            stored = checkpoints.stored.get(start)
            if stored is None:
                continue
            for want in HORIZONS:
                if want > max_horizon or start + want > horizon:
                    continue
                pre = ka_abi.engine_snapshot(stored['engine'], ROWS,
                                             int(stored['engine'].battle_state), HUMAN_BASES)
                label = (f'enc{case["encounterId"]}@{horizon} seed{case["mathSeed"]} '
                         f'from{start}')
                import copy
                python_engine = copy.deepcopy(stored['engine'])
                run_horizon(library, python_engine, pre, want, record, label)
                if record['failures'] or record['event_failures']:
                    break
                python_engine = copy.deepcopy(stored['engine'])
                run_internal(library, python_engine, pre, want, record, label)
                if record['failures'] or record['event_failures']:
                    break
            if record['failures'] or record['event_failures']:
                break
        if record['failures'] or record['event_failures']:
            break
    elapsed = time.perf_counter() - started
    print(f'multi-step parity: {len(record["completed"])} checkpoint-verified sequences, '
          f'{len(record["internal_ok"])} internally-looped sequences')
    print(f'  steps compared (per-step, checkpoints every step): '
          f'{sum(entry["steps"] for entry in record["completed"])}')
    print(f'  unsupported stops: {len(record["unsupported"])} '
          f'(internal: {len(record["internal_unsupported"])})')
    for entry in record['unsupported'][:5]:
        print(f'    unsupported at {entry["label"]} step {entry["step"]} status {entry["status"]}')
    print(f'  wall time: {elapsed:.2f}s')
    print(f'  events compared: {record["events_compared"]} across '
          f'{len(record["event_kinds"])} canonical kinds {sorted(record["event_kinds"])}')
    print(f'  event mismatches: {len(record["event_failures"])}')
    if record['event_failures']:
        print('FIRST EVENT FAILURE')
        print(record['event_failures'][0])
        return 1
    if record['failures']:
        print('FIRST FAILURE')
        print(record['failures'][0])
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
