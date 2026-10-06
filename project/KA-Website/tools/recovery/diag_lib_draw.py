"""Locate the first tick where the native lib RNG diverges for enc19 @7000, seeds 26/27.

Runs Python and native in lockstep from the identical tick-0 state, comparing the math and lib draw
counters after every tick, then (at the first divergent tick) after every individual phase to
localise the phase. Read-only diagnostic; no harness semantics are changed.

    pypy3.exe diag_lib_draw.py
"""
import copy
import ctypes
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import ka_abi  # noqa: E402
from check_native_real_state import (Checkpoints, fighter_step, global_phases,  # noqa: E402
                                     projectile_phase, status_phase, trailing_phases)
from combat_animation_selection import HUMAN_BASES  # noqa: E402
from combat_ending import advance_battle_frame, enter_ending, is_annihilated  # noqa: E402
from combat_sandbox import run_scenario  # noqa: E402
from combat_shared_controllers import ROWS  # noqa: E402
from strategy_optimizer_adapter import default_scenario  # noqa: E402

CASE = dict(encounterId=19, tickLimit=7000, mathSeed=26, libSeed=27)
TICKS = 6999


def python_head(engine):
    engine.tick += 1
    engine.battle_frame = advance_battle_frame(engine.battle_frame)
    if engine.battle_state == 2 and any(is_annihilated(t) for t in engine.teams):
        engine.battle_frame = 0
        engine.verdict = enter_ending(engine.teams, engine.change)
        engine.battle_state = 3
        engine.emit('verdict', result=engine.verdict)


def main():
    library = ka_abi.load()
    checkpoints = Checkpoints([0])
    run_scenario(dict(default_scenario(), **CASE), checkpoints=checkpoints, stop_tick=0)
    engine = copy.deepcopy(checkpoints.stored[0]['engine'])
    snapshot = ka_abi.engine_snapshot(engine, ROWS, int(engine.battle_state), HUMAN_BASES)
    battle = ka_abi.load_snapshot(library, snapshot)

    first = None
    for step in range(TICKS):
        python_head(engine)
        fighter_step(engine)
        status_phase(engine)
        projectile_phase(engine)
        global_phases(engine)
        trailing_phases(engine)
        report = ka_abi.KaBattleReport()
        # native tick (head + all phases) runs the same phase set
        status = library.ka_native_tick(battle, int(engine.battle_state))
        if status != 0:
            print('native refused at step', step, status)
            break
        library.ka_battle_report(battle, 0, ctypes.byref(report))
        if (engine.lib_draws, engine.math_draws) != (report.lib_draws, report.math_draws):
            first = step
            print(f'first divergence at loop step {step} (tick {engine.tick})')
            print(f'  python lib={engine.lib_draws} math={engine.math_draws}')
            print(f'  native lib={report.lib_draws} math={report.math_draws}')
            break
    if first is None:
        print('no divergence over the whole run')
        library.ka_battle_free(battle)
        return 0

    # phase-by-phase localisation at the divergent tick, from the previous tick's state
    engine = copy.deepcopy(checkpoints.stored[0]['engine'])
    battle = ka_abi.load_snapshot(library, snapshot)
    for step in range(first):
        python_head(engine)
        fighter_step(engine)
        status_phase(engine)
        projectile_phase(engine)
        global_phases(engine)
        trailing_phases(engine)
        library.ka_native_tick(battle, int(engine.battle_state))
    python_head(engine)
    library.ka_native_tick(battle, int(engine.battle_state))  # heads only; phases run below
    # Re-run the tick from the pre-head state so phases can be compared one at a time.
    engine = copy.deepcopy(checkpoints.stored[0]['engine'])
    battle = ka_abi.load_snapshot(library, snapshot)
    for step in range(first):
        python_head(engine)
        fighter_step(engine)
        status_phase(engine)
        projectile_phase(engine)
        global_phases(engine)
        trailing_phases(engine)
        library.ka_native_tick(battle, int(engine.battle_state))
    python_head(engine)
    library.ka_native_tick(battle, int(engine.battle_state))
    # from here the native battle has already run this tick; rebuild both from the tick-`first` start
    engine = copy.deepcopy(checkpoints.stored[0]['engine'])
    battle = ka_abi.load_snapshot(library, snapshot)
    for step in range(first):
        python_head(engine)
        fighter_step(engine)
        status_phase(engine)
        projectile_phase(engine)
        global_phases(engine)
        trailing_phases(engine)
        library.ka_native_tick(battle, int(engine.battle_state))
    print(f'phase localisation at tick {engine.tick + 1}:')
    for name, phase in (('head+fighters', 0), ('status', 1), ('projectiles', 2),
                        ('movement block', 3), ('trailing', 4)):
        # run one phase on each side and compare the lib counter
        if phase == 0:
            python_head(engine)
            before_py = engine.lib_draws
            fighter_step(engine)
            report = ka_abi.KaBattleReport()
            library.ka_native_tick(battle, int(engine.battle_state))
        else:
            before_py = engine.lib_draws
            if phase == 1:
                status_phase(engine)
                library.ka_run_phase(battle, 5)
            elif phase == 2:
                projectile_phase(engine)
                library.ka_run_phase(battle, 6)
            elif phase == 3:
                global_phases(engine)
                library.ka_run_phase(battle, 4)
            else:
                trailing_phases(engine)
                library.ka_run_phase(battle, 14)
        library.ka_battle_report(battle, 0, ctypes.byref(report))
        print(f'  {name:15s} python_lib={engine.lib_draws} native_lib={report.lib_draws} '
              f'delta_python={engine.lib_draws - before_py}')
    library.ka_battle_free(battle)
    return 0


if __name__ == '__main__':
    sys.exit(main())
