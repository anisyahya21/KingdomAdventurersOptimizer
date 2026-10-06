"""At the first divergent tick, list the canonical lib draws by purpose and the native event log."""
import copy
import ctypes
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import ka_abi  # noqa: E402
import ka_events  # noqa: E402
from check_native_real_state import (Checkpoints, global_phases,  # noqa: E402
                                     projectile_phase, status_phase, trailing_phases)
from combat_animation_selection import HUMAN_BASES  # noqa: E402
from combat_ending import advance_battle_frame, enter_ending, is_annihilated  # noqa: E402
from combat_sandbox import run_scenario  # noqa: E402
from combat_shared_controllers import ROWS  # noqa: E402
from combat_shared_resolution import execute_shared_skill_commands  # noqa: E402
from combat_tick import update_fighters  # noqa: E402
from strategy_optimizer_adapter import default_scenario  # noqa: E402

CASE = dict(encounterId=19, tickLimit=7000, mathSeed=26, libSeed=27)
DIVERGENT_STEP = 5342


def python_tick(engine):
    engine.tick += 1
    engine.battle_frame = advance_battle_frame(engine.battle_frame)
    if engine.battle_state == 2 and any(is_annihilated(t) for t in engine.teams):
        engine.battle_frame = 0
        engine.verdict = enter_ending(engine.teams, engine.change)
        engine.battle_state = 3
        engine.emit('verdict', result=engine.verdict)
    update_fighters(engine.teams, engine.battle_state, engine.update,
                    lambda unit: execute_shared_skill_commands(
                        engine.world, unit['id'], lambda command: ROWS[command['skill']],
                        engine.animate, engine.use))
    status_phase(engine)
    projectile_phase(engine)
    global_phases(engine)
    trailing_phases(engine)


def main():
    library = ka_abi.load()
    checkpoints = Checkpoints([0])
    run_scenario(dict(default_scenario(), **CASE), checkpoints=checkpoints, stop_tick=0)
    engine = copy.deepcopy(checkpoints.stored[0]['engine'])
    snapshot = ka_abi.engine_snapshot(engine, ROWS, int(engine.battle_state), HUMAN_BASES)
    battle = ka_abi.load_snapshot(library, snapshot)
    for _ in range(DIVERGENT_STEP):
        python_tick(engine)
        library.ka_native_tick(battle, int(engine.battle_state))
    print(f'state before the divergent tick: python lib={engine.lib_draws} '
          f'native lib={ka_abi.KaBattleReport().lib_draws}')
    trace_start = len(engine.trace)
    python_tick(engine)
    library.ka_native_tick(battle, int(engine.battle_state))
    report = ka_abi.KaBattleReport()
    library.ka_battle_report(battle, 0, ctypes.byref(report))
    print(f'after the tick: python lib={engine.lib_draws} native lib={report.lib_draws}')
    print('canonical lib draws in this tick (purpose order):')
    for event in engine.trace[trace_start:]:
        if event['kind'] == 'rng' and event.get('stream') == 'lib':
            print(f"  draw {event['draw']} purpose={event['purpose']} bound={event['bound']}")
    print('canonical event kinds in this tick:')
    kinds = [event['kind'] for event in engine.trace[trace_start:]]
    print(' ', kinds)
    print('native event kinds in this tick:')
    print(' ', [row[0] for row in ka_abi.native_events(library, battle)])
    # Full lib-draw sequence comparison: Python (tick, purpose) vs native (tick, label).
    purposes = {0: 'skill_sound', 1: 'defender_sound', 2: 'normal_attack_update',
                3: 'leaving_sound', 4: 'knockdown_sound'}
    python_libs = [event['purpose'] for event in engine.trace
                   if event['kind'] == 'rng' and event.get('stream') == 'lib']
    native_libs = []
    for slot in range(library.ka_lib_log_count(battle)):
        row = (ctypes.c_int32 * 5)()
        library.ka_lib_log(battle, slot, row)
        native_libs.append(purposes.get(row[0], f'label{row[0]}'))
    print(f'lib sequence: python={len(python_libs)} native={len(native_libs)}')
    for index in range(max(len(python_libs), len(native_libs))):
        left = python_libs[index] if index < len(python_libs) else None
        right = native_libs[index] if index < len(native_libs) else None
        if left != right:
            print(f'  first differing draw #{index}: python={left} native={right}')
            print(f'  context python={python_libs[max(0,index-2):index+2]}')
            print(f'  context native={native_libs[max(0,index-2):index+2]}')
            break
    else:
        print('  lib draw sequences identical')
    library.ka_battle_free(battle)
    return 0


if __name__ == '__main__':
    sys.exit(main())
