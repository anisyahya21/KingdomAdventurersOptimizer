"""Real captured-state differential parity: canonical engine vs the Rust kernel.

The reference is the *live* canonical `SharedControllers`. A frozen full-fight scenario is run to a
chosen tick, the engine deepcopy at that tick is the pre-state, and both sides then execute exactly
the same coherent fighter/action step:

    Python: combat_tick.update_fighters(engine.teams, engine.battle_state, engine.update,
                                        execute_shared_skill_commands)
    Rust:   ka_update_fighters(battle, same battle_state)

The pre-state travels through `ka_abi`'s one snapshot format, so Python and Rust are compared on the
same fields: blackboards (membership and values), long boards, parameters, equipment rows, skills,
levels, command queues, invoking state, position/velocity/offset, cell/grid/direction, animation,
modifiers, effects, projectiles, component presence, subset slot order, occupancy buckets, both RNG
streams with their draw counters, and the ordered event log.

Run it with the pinned PyPy runtime (the workers' own interpreter):

    pypy3.exe check_native_real_state.py
"""
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import ka_abi  # noqa: E402
import ka_events  # noqa: E402
from combat_sandbox import run_scenario  # noqa: E402
from combat_shared_controllers import ROWS  # noqa: E402
from combat_shared_resolution import execute_shared_skill_commands  # noqa: E402
from combat_tick import update_fighters  # noqa: E402
from strategy_optimizer_adapter import default_scenario  # noqa: E402
from combat_animation_selection import HUMAN_BASES  # noqa: E402

# Frozen full-fight cases (the same shapes `check_strategy_optimizer_speed.py` replays).
CASES = [dict(encounterId=0, tickLimit=1000, mathSeed=7, libSeed=8),
         dict(encounterId=5, tickLimit=1000, mathSeed=12, libSeed=13),
         dict(encounterId=12, tickLimit=1000, mathSeed=19, libSeed=20),
         dict(encounterId=19, tickLimit=1000, mathSeed=26, libSeed=27),
         dict(encounterId=19, tickLimit=7000, mathSeed=7, libSeed=8),
         dict(encounterId=19, tickLimit=7000, mathSeed=907303519, libSeed=267534053),
         # The reflection `skill_sound` regression: this pair used to end one Lib draw short
         # (first divergence at draw #1040, loop step 5342) because a reflected attack omitted the
         # `effects['sound']()` call that `use_fighter_skill`'s type-18 branch makes.
         dict(encounterId=19, tickLimit=7000, mathSeed=26, libSeed=27)]


class Checkpoints:
    def __init__(self, wanted):
        self.wanted_set = set(wanted)
        self.stored = {}

    def wanted(self, tick):
        return int(tick) in self.wanted_set

    def store(self, data):
        self.stored[int(data['tick'])] = data


def fighter_step(engine):
    """Exactly the fighters phase `run()` performs, as one coherent action step.

    `run()` advances the tick before the phase, so the step does too; the Rust entry point mirrors
    that.
    """
    engine.tick += 1
    update_fighters(engine.teams, engine.battle_state, engine.update,
                    lambda unit: execute_shared_skill_commands(
                        engine.world, unit['id'], lambda command: ROWS[command['skill']],
                        engine.animate, engine.use))


def cell_changed(engine, identity, old_key):
    engine.occupancy.changed(identity, old_key)
    engine.emit('cell_change', target=identity, oldKey=old_key,
                cell=list(engine.world.objects[identity]['components'][5]))


def global_phases(engine):
    """The four ported global phases, in the canonical order the native tick uses."""
    import math
    from combat_spatial import update_cells, update_facing, update_heights, update_positions
    update_positions(engine.moving.members, engine.world)
    update_facing(engine.rotating.members, engine.world, 0, lambda i: False,
                  lambda i: engine.specs[i]['human'],
                  lambda i, right: (engine.world.objects[i]['components'][7]['res_ids'],
                                    engine.world.objects[i]['components'][7]['tex_ids']),
                  math.atan2, math.fmod)
    update_cells(engine.cells.members, engine.world, engine.map_width, 24, 24,
                 lambda identity, old: cell_changed(engine, identity, old))
    update_heights(engine.height_members.members, engine.world, dict(
        has_product=lambda i: False, has_treasure=lambda i: False,
        has_human=lambda i: engine.world.objects[i]['components'][18] is not None,
        human_flag32=lambda i: bool(engine.world.objects[i]['components'][18]['flag'] & 32),
        can_move_in_air=lambda i: False, map_chip=lambda x, y: None,
        null_or_destroyed=lambda tile: tile is None))


def status_phase(engine):
    """`combat_skills.update_status` over the skill-member subset, plus the `status_tick` event."""
    from combat_skills import update_status
    for identity in engine.skill_members.members:
        components = engine.world.objects[identity]['components']
        if components[28] is None:
            continue
        board = components[28]['board']
        before = {key: board[key] for key in (62, 63, 64) if key in board}
        update_status(board)
        after = {key: board[key] for key in (62, 63, 64) if key in board}
        if before != after:
            engine.emit('status_tick', target=identity, before=before, after=after)


def projectile_phase(engine):
    """`tick_projectiles` with the controller's rotate/trail/impact callbacks."""
    from combat_projectiles import tick_projectiles
    tick_projectiles(engine.projectiles.members, engine.world, lambda *a: None, engine.trail,
                     engine.impact_projectile)


def tick_step(engine):
    """One native tick: fighters, status, projectiles, movement block, then the trailing phases."""
    fighter_step(engine)
    status_phase(engine)
    projectile_phase(engine)
    global_phases(engine)
    trailing_phases(engine)


def trailing_phases(engine):
    """modifiers, animation, effects, garbage - the remaining canonical per-tick phases."""
    from combat_animation import update_animations
    from combat_effects import tick_modifiers, update_effect_phase
    from combat_lifecycle import update_garbage
    tick_modifiers(engine.modifiers.members, engine.world,
                   lambda angle: math.sin(math.radians(angle)), lambda: None)
    update_animations(engine.fighter_animations.members, engine.world,
                      engine.animation_resources,
                      dict(enabled=True, auto_animation=False, common_frames_update=True),
                      lambda identity, behavior: engine.animate(engine.units[identity], behavior))
    update_effect_phase(engine.effects.members, engine.effect_values, engine.effect_positions,
                        engine.world.is_destroyed, engine.world.destroy)
    update_garbage(engine.garbage.members, engine.lifetimes, engine.world.destroy)


def capture_ticks(horizon):
    ticks = sorted(set(list(range(1, min(horizon, 120))) +
                       list(range(120, min(horizon, 1000), 37)) +
                       [min(horizon, 1000), min(horizon, 2000), min(horizon, 4000),
                        min(horizon, 6000), horizon]))
    return [tick for tick in ticks if 1 <= tick <= horizon]


def classify(snapshot):
    """Which interesting states a captured pre-state actually represents."""
    marks = set()
    for unit in snapshot['units']:
        state = unit['board'].get(5)
        if state in (4, 6, 7, 8):
            marks.add({4: 'attack', 6: 'damage', 7: 'knockdown', 8: 'leaving'}[state])
        if unit['commands']:
            marks.add('command-queue')
        if unit['invoking']:
            marks.add('invoking')
        if 62 in unit['board']:
            marks.add('status')
        if unit['components']['modifier'] is not None:
            marks.add('modifier')
    for entity in snapshot['objects']:
        components = entity['components']
        if components['projectile'] is not None:
            marks.add('projectile')
        if components['effect'] is not None:
            marks.add('effect')
        if components['modifier'] is not None:
            marks.add('projectile-modifier')
    return marks


def compare_case(library, base, case, record):
    horizon = case['tickLimit']
    ticks = capture_ticks(horizon)
    checkpoints = Checkpoints(ticks)
    run_scenario(dict(base, **case), checkpoints=checkpoints, stop_tick=max(ticks))
    stages = {}
    for tick in ticks:
        stored = checkpoints.stored.get(tick)
        if stored is None:
            continue
        engine = stored['engine']
        battle_state = int(engine.battle_state)
        pre = ka_abi.engine_snapshot(engine, ROWS, battle_state, HUMAN_BASES)
        marks = classify(pre)
        stages.setdefault('marks', set()).update(marks)
        battle = ka_abi.load_snapshot(library, pre)
        try:
            reloaded = ka_abi.native_snapshot(library, battle, pre)
            import_diffs = ka_abi.diff_snapshots(pre, reloaded)
            if import_diffs:
                raise SystemExit(f'{case} tick {tick}: state import is lossy:\n  ' +
                                 '\n  '.join(import_diffs[:12]))
            trace_start = len(engine.trace)
            fighter_step(engine)
            expected = ka_abi.engine_snapshot(engine, ROWS, battle_state, HUMAN_BASES)
            status = library.ka_update_fighters(battle, battle_state)
            got = ka_abi.native_snapshot(library, battle, pre)
            if status != 0:
                record['unsupported'].append(dict(
                    case=dict(case), tick=tick, status=status,
                    marks=sorted(marks),
                    units={unit['identity']: unit['board'].get(5) for unit in pre['units']},
                    commands={unit['identity']: len(unit['commands']) for unit in pre['units']
                              if unit['commands']},
                    invoking={unit['identity']: len(unit['invoking']) for unit in pre['units']
                              if unit['invoking']},
                    status_slots={unit['identity']: unit['board'][62] for unit in pre['units']
                                  if 62 in unit['board']},
                    objects=len(pre['objects']),
                    buckets=len(pre['buckets']),
                    max_commands=max((len(unit['commands']) for unit in pre['units']), default=0),
                    max_invoking=max((len(unit['invoking']) for unit in pre['units']), default=0),
                    board_keys=max((len(unit['board']) for unit in pre['units']), default=0),
                ))
                continue
            diffs = ka_abi.diff_snapshots(expected, got)
            expected_events = ka_events.normalize_python(engine.trace[trace_start:])
            native_events = ka_events.normalize_native(ka_abi.native_events(library, battle))
            if expected_events != native_events:
                record['event_failures'].append(dict(
                    case=dict(case), tick=tick,
                    python=expected_events[:20], native=native_events[:20],
                    python_count=len(expected_events), native_count=len(native_events)))
                record['checked'] += 1
                return record
            record['event_kinds'].update(kind for kind, _, _, _ in expected_events)
            record['events_compared'] += len(expected_events)
            if diffs:
                record['failures'].append({'case': dict(case), 'tick': tick,
                                           'python_state': {u['identity']: u['board'].get(5)
                                                            for u in expected['units']},
                                           'diffs': diffs[:20]})
                record['checked'] += 1
                return record
            record['checked'] += 1
            record['stages'].setdefault(frozenset(marks), 0)
            record['stages'][frozenset(marks)] += 1
        finally:
            library.ka_battle_free(battle)
    return record


def main():
    library = ka_abi.load()
    base = default_scenario()
    record = dict(checked=0, failures=[], unsupported=[], stages={}, event_failures=[],
                  event_kinds=set(), events_compared=0)
    started = time.perf_counter()
    for case in CASES:
        compare_case(library, base, case, record)
        if record['failures'] or record['event_failures']:
            break
    elapsed = time.perf_counter() - started
    print(f'real captured-state single-step parity: {record["checked"]} pre-states compared')
    print(f'  unsupported-mechanic stops: {len(record["unsupported"])}')
    for stage, count in sorted(record['stages'].items(), key=lambda item: -item[1]):
        print(f'  stage {sorted(stage)}: {count} states')
    print(f'  events compared: {record["events_compared"]} across '
          f'{len(record["event_kinds"])} canonical kinds {sorted(record["event_kinds"])}')
    print(f'  event mismatches: {len(record["event_failures"])}')
    print(f'  wall time: {elapsed:.2f}s')
    if record['event_failures']:
        print('FIRST EVENT FAILURE')
        failure = record['event_failures'][0]
        print(f'  {failure["case"]} tick {failure["tick"]} '
              f'python_count={failure["python_count"]} native_count={failure["native_count"]}')
        for index in range(max(len(failure['python']), len(failure['native']))):
            left = failure['python'][index] if index < len(failure['python']) else None
            right = failure['native'][index] if index < len(failure['native']) else None
            mark = '   ' if left == right else '***'
            print(f'  {mark} {index:3d} python={left}')
            if left != right:
                print(f'      {index:3d} native={right}')
                break
        return 1
    if record['unsupported']:
        codes = {}
        for refusal in record['unsupported']:
            codes.setdefault(refusal['status'], []).append(refusal)
        print('  refusal triage:')
        for code in sorted(codes):
            group = codes[code]
            print(f'    status {code}: {len(group)} refusals')
            sample = group[0]
            print(f'      first: {sample["case"]} tick {sample["tick"]}')
            print(f'      marks={sample["marks"]} units={sample["units"]}')
            print(f'      commands={sample["commands"]} invoking={sample["invoking"]} '
                  f'status_slots={sample["status_slots"]}')
            print(f'      objects={sample["objects"]} buckets={sample["buckets"]} '
                  f'max_commands={sample["max_commands"]} max_invoking={sample["max_invoking"]} '
                  f'board_keys={sample["board_keys"]}')
            for other in group[1:6]:
                print(f'      also: {other["case"]} tick {other["tick"]} '
                      f'marks={other["marks"]} objects={other["objects"]} '
                      f'buckets={other["buckets"]}')
    if record['failures']:
        failure = record['failures'][0]
        print('FIRST FAILURE')
        print(json.dumps(failure, indent=2)[:4000])
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
