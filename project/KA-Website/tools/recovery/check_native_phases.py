"""Per-phase differential parity for the newly ported global tick phases.

Each phase is tested *in isolation* from real captured canonical states, so a defect is attributed to
one phase rather than to the tick as a whole:

    0 `update_positions`   combat_spatial.update_positions
    1 `update_facing`      combat_spatial.update_facing (render_mode 0)
    2 `update_cells`       combat_spatial.update_cells + CellOccupancy.changed + cell_change event
    3 `update_heights`     combat_spatial.update_heights (null-tile map)
    4 all four             canonical order

The comparison is the whole canonical snapshot plus the ordered events, so position, velocity,
offset, cell/grid, direction, height, occupancy buckets, component presence, subset
membership/order/version, board values and both RNG streams are all covered.

    pypy3.exe check_native_phases.py
"""
import copy
import math
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import ka_abi  # noqa: E402
import ka_events  # noqa: E402
from check_native_real_state import (CASES, Checkpoints, cell_changed, projectile_phase,  # noqa: E402
                                     status_phase, trailing_phases)  # noqa: E402
from combat_animation_selection import HUMAN_BASES  # noqa: E402
from combat_sandbox import run_scenario  # noqa: E402
from combat_shared_controllers import ROWS  # noqa: E402
from combat_spatial import update_cells, update_facing, update_heights, update_positions  # noqa: E402
from strategy_optimizer_adapter import default_scenario  # noqa: E402

PHASES = [(0, 'positions'), (1, 'facing'), (2, 'cells'), (3, 'heights'),
          (5, 'status'), (6, 'projectiles'), (7, 'status+proj'), (8, 'prefix'),
          (9, 'status expiry'), (10, 'modifiers'), (11, 'animation'), (12, 'effects'),
          (13, 'garbage'), (14, 'trailing')]


def force_status_counters(snapshot_or_engine, native=False):
    """Put two own fighters on the 20-tick boundary: one expiring, one merely decrementing."""
    if native:
        units = snapshot_or_engine['units']
        victims = [unit for unit in units if unit['team'] == 0 and unit['skills']][:2]
        for position, unit in enumerate(victims):
            unit['board'][62] = unit['skills'][0]
            unit['board'][63] = 1 if position == 0 else 3
            unit['board'][64] = 19
        return
    for position, unit in enumerate([u for u in snapshot_or_engine.teams[0] if u['skills']][:2]):
        board = unit['board']
        board[62] = snapshot_or_engine.specs[unit['id']]['skills'][0]
        board[63] = 1 if position == 0 else 3
        board[64] = 19


def python_phase(engine, phase):
    if phase == 0:
        update_positions(engine.moving.members, engine.world)
    elif phase == 1:
        update_facing(engine.rotating.members, engine.world, 0, lambda i: False,
                      lambda i: engine.specs[i]['human'],
                      lambda i, right: (engine.world.objects[i]['components'][7]['res_ids'],
                                        engine.world.objects[i]['components'][7]['tex_ids']),
                      math.atan2, math.fmod)
    elif phase == 2:
        update_cells(engine.cells.members, engine.world, engine.map_width, 24, 24,
                     lambda identity, old: cell_changed(engine, identity, old))
    elif phase == 3:
        update_heights(engine.height_members.members, engine.world, dict(
            has_product=lambda i: False, has_treasure=lambda i: False,
            has_human=lambda i: engine.world.objects[i]['components'][18] is not None,
            human_flag32=lambda i: bool(engine.world.objects[i]['components'][18]['flag'] & 32),
            can_move_in_air=lambda i: False, map_chip=lambda x, y: None,
            null_or_destroyed=lambda tile: tile is None))
    elif phase == 5:
        status_phase(engine)
    elif phase == 6:
        projectile_phase(engine)
    elif phase == 7:
        status_phase(engine)
        projectile_phase(engine)
    elif phase == 8:
        status_phase(engine)
        projectile_phase(engine)
        for inner in (0, 1, 2, 3):
            python_phase(engine, inner)
    elif phase == 9:
        status_phase(engine)
    elif phase == 10:
        from combat_effects import tick_modifiers
        tick_modifiers(engine.modifiers.members, engine.world,
                       lambda angle: math.sin(math.radians(angle)), lambda: None)
    elif phase == 11:
        from combat_animation import update_animations
        update_animations(engine.fighter_animations.members, engine.world,
                          engine.animation_resources,
                          dict(enabled=True, auto_animation=False, common_frames_update=True),
                          lambda identity, behavior: engine.animate(engine.units[identity],
                                                                    behavior))
    elif phase == 12:
        from combat_effects import update_effect_phase
        update_effect_phase(engine.effects.members, engine.effect_values, engine.effect_positions,
                            engine.world.is_destroyed, engine.world.destroy)
    elif phase == 13:
        from combat_lifecycle import update_garbage
        update_garbage(engine.garbage.members, engine.lifetimes, engine.world.destroy)
    elif phase == 14:
        trailing_phases(engine)
    else:
        for inner in (0, 1, 2, 3):
            python_phase(engine, inner)


def main():
    library = ka_abi.load()
    base = default_scenario()
    ticks = (1, 40, 120, 400)
    counts = {name: 0 for _, name in PHASES}
    events = {name: 0 for _, name in PHASES}
    started = time.perf_counter()
    for case in CASES[:5]:
        horizon = case['tickLimit']
        wanted = [tick for tick in ticks if tick < horizon]
        checkpoints = Checkpoints(wanted)
        run_scenario(dict(base, **case), checkpoints=checkpoints, stop_tick=max(wanted))
        for tick in wanted:
            stored = checkpoints.stored.get(tick)
            if stored is None:
                continue
            for phase, name in PHASES:
                engine = copy.deepcopy(stored['engine'])
                if phase == 9:
                    force_status_counters(engine)
                battle_state = int(engine.battle_state)
                pre = ka_abi.engine_snapshot(engine, ROWS, battle_state, HUMAN_BASES)
                if phase == 9:
                    force_status_counters(pre, native=True)
                trace_start = len(engine.trace)
                python_phase(engine, phase)
                expected = ka_abi.engine_snapshot(engine, ROWS, battle_state, HUMAN_BASES)
                expected_events = ka_events.normalize_python(engine.trace[trace_start:])

                battle = ka_abi.load_snapshot(library, pre)
                try:
                    status = library.ka_run_phase(battle, phase)
                    if status != 0:
                        raise SystemExit(f'phase {name}: native refused with {status}')
                    got = ka_abi.native_snapshot(library, battle, pre)
                    native_events = ka_events.normalize_native(
                        ka_abi.native_events(library, battle))
                finally:
                    library.ka_battle_free(battle)
                diffs = ka_abi.diff_snapshots(expected, got)
                if diffs:
                    raise SystemExit(f'phase {name} {case} tick {tick}: state mismatch\n  ' +
                                     '\n  '.join(diffs[:8]))
                if expected_events != native_events:
                    raise SystemExit(f'phase {name} {case} tick {tick}: event mismatch\n'
                                     f'  python={expected_events[:8]}\n'
                                     f'  native={native_events[:8]}')
                counts[name] += 1
                events[name] += len(expected_events)
    elapsed = time.perf_counter() - started
    print('per-phase differential parity (real captured states, 5 cases x 4 ticks):')
    for _, name in PHASES:
        print(f'  {name:9s}: {counts[name]} states, {events[name]} events, 0 mismatches')
    print(f'  wall time: {elapsed:.2f}s')
    print(f'  runtime: {sys.implementation.name} {sys.version.split()[0]}')


if __name__ == '__main__':
    main()
