"""Permanent regression assertions for the six discovered native defects, plus the benchmark.

Every check is written against `ka_abi`'s authoritative mirror and fails loudly if the fix is lost:

  1. identity vs roster-index handling;
  2. `ChangeAnimation` Seb/frame/backup side effects;
  3. occupancy `on_added`/`on_removed`;
  4. subset holes / free-list semantics;
  5. one Lib sound draw per projectile;
  6. real observed capacities above the original limits.

The benchmark then measures the same real captured workload on both sides: canonical PyPy execution,
Rust internally-looped execution, and the import/export costs separately.

    pypy3.exe check_native_gate.py
"""
import ctypes
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import ka_abi  # noqa: E402
from check_native_real_state import CASES, Checkpoints, classify, fighter_step  # noqa: E402
from combat_animation_selection import HUMAN_BASES  # noqa: E402
from combat_collections import EntitySlotSet  # noqa: E402
from combat_effects import departure_effect_spec, healing_effect_spec  # noqa: E402
from combat_entities import CombatEntities  # noqa: E402
from combat_sandbox import run_scenario  # noqa: E402
from combat_shared_controllers import ROWS  # noqa: E402
from strategy_optimizer_adapter import default_scenario  # noqa: E402

FIRST_IDENTITY = 100
SLOT_CELL, SLOT_EFFECT, SLOT_MODIFY_ANIMATION = 5, 38, 19


def baseline_unit(identity, team=0, human=True, cell=(3, 6), state=1):
    unit = ka_abi.KaUnit()
    unit.present = 1
    unit.team = team
    unit.human = int(human)
    unit.monster = int(not human)
    unit.id = identity
    unit.identity = identity
    unit.body.id = identity
    for slot in (0, 1, 2, 5, 7, 12, 14, 28, 33, 51):
        unit.body.has[slot] = 1
    unit.body.has[18 if human else 20] = 1
    if team == 0:
        unit.body.has[49] = 1
    unit.body.cell[0], unit.body.cell[1] = cell
    unit.body.seb[0], unit.body.seb[2] = (11 if human else 22), 0
    unit.body.animation[0], unit.body.animation[1] = 1, -1
    unit.body.direction = 0
    unit.weapon_type = 0
    unit.weapon_shooting_range = 1
    unit.weapon_motion = 3
    unit.board.len = 5
    for position, (key, value) in enumerate(((4, 0), (5, state), (6, team), (7, 0), (8, 0))):
        unit.board.entries[position].key = key
        unit.board.entries[position].value = value
    unit.params.count = 1
    unit.params.rows[0].id = 10
    unit.params.rows[0].raw_value = 900
    unit.params.rows[0].raw_max = 900
    return unit


def load(library, units, human_bases=HUMAN_BASES, idents=FIRST_IDENTITY):
    buffer = (ka_abi.KaUnit * len(units))()
    for index, unit in enumerate(units):
        buffer[index] = unit
    battle = library.ka_battle_create()
    library.ka_battle_import(battle, buffer, len(units), 0)
    library.ka_battle_identity(battle, idents)
    bases = (ctypes.c_int32 * len(human_bases))(*human_bases)
    library.ka_battle_set_human_bases(battle, bases, len(human_bases))
    library.ka_battle_rebuild_subsets(battle)
    library.ka_battle_seed_occupancy(battle)
    return battle


def identity_vs_index(library, record):
    """1: targets/commands carry identities, never roster slots."""
    units = [baseline_unit(200, team=1, cell=(3, 6)), baseline_unit(100, team=0, cell=(3, 8))]
    battle = load(library, units, idents=100)
    try:
        skill = ka_abi.KaSkill(500, 0, 60, 8 | 16, 0, 0, -1, 1, 1, 1, 16, 0, 7, 3, -1, 0)
        library.ka_battle_add_row(battle, ctypes.byref(skill))
        library.ka_change_state(battle, 0, 3)
        unit = (ka_abi.KaUnit * 2)()
        library.ka_battle_export(battle, unit)
        target = None
        for position in range(unit[0].long_board.len):
            if unit[0].long_board.entries[position].key == 16:
                target = unit[0].long_board.entries[position].value
        if target is not None and target not in (-1, 100, 200):
            raise SystemExit(f'identity check: long_board[16]={target} is a roster slot')
        for index in range(unit[0].command_count):
            stored = unit[0].commands[index].target
            if stored not in (-1, 100, 200):
                raise SystemExit(f'identity check: command target {stored} is a roster slot')
        record['identity'] = 'long_board/command targets are identities'
    finally:
        library.ka_battle_free(battle)


def change_animation_side_effects(library, record):
    """2: ChangeAnimation writes Seb.id, Seb.frame=0 and Animation.backup=-1."""
    # (state, requested behaviour): state 3 enters waiting (behaviour 3), state 6 enters damaging
    # (behaviour 30), and a monster entering waiting requests behaviour 3 with base 0.
    for team, human, direction, state, behavior in ((0, True, 0, 3, 3), (1, True, 2, 6, 30),
                                                    (1, False, 0, 3, 3)):
        unit = baseline_unit(FIRST_IDENTITY, team=team, human=human)
        unit.body.direction = direction
        battle = load(library, [unit])
        try:
            library.ka_change_state(battle, 0, state)
            out = (ka_abi.KaUnit * 1)()
            library.ka_battle_export(battle, out)
            expected_clip = (HUMAN_BASES[behavior] if human else
                             (16 if behavior == 4 else 0)) + direction
            if out[0].body.seb[1] != expected_clip:
                raise SystemExit(f'ChangeAnimation clip: got {out[0].body.seb[1]} '
                                 f'want {expected_clip}')
            if out[0].body.seb[2] != 0:
                raise SystemExit(f'ChangeAnimation frame: got {out[0].body.seb[2]} want 0')
            if out[0].body.animation[1] != -1:
                raise SystemExit(f'ChangeAnimation backup: got {out[0].body.animation[1]} want -1')
        finally:
            library.ka_battle_free(battle)
    record['change_animation'] = 'Seb id/frame and Animation backup written on every state entry'


def occupancy_callbacks(library, record):
    """3: gaining/losing `Cell` updates the occupancy buckets."""
    battle = load(library, [baseline_unit(FIRST_IDENTITY)])
    try:
        library.ka_battle_seed_occupancy(battle)
        before = library.ka_bucket_count(battle)
        spec = ka_abi.EffectSpec(0, 0, 0, 28, 0, 240.0, 48.0, 240.0, 100, 6, 1, 0, 0, 0, -1, 1)
        entity = library.ka_create_effect(battle, ctypes.byref(spec))
        if entity < 0:
            raise SystemExit(f'occupancy check: create_effect refused with {entity}')
        after = library.ka_bucket_count(battle)
        if after <= before:
            raise SystemExit('occupancy check: a depth effect gained Cell but no bucket was made')
        seen = False
        for slot in range(after):
            key = ctypes.c_int32()
            count = ctypes.c_uint32()
            ids = (ctypes.c_int32 * 64)()
            library.ka_bucket_state(battle, slot, ctypes.byref(key), ctypes.byref(count), ids)
            if any(ids[i] == entity for i in range(count.value)):
                seen = True
        if not seen:
            raise SystemExit('occupancy check: the effect never entered a bucket')
        library.ka_battle_destroy(battle, entity)
        still = False
        for slot in range(library.ka_bucket_count(battle)):
            key = ctypes.c_int32()
            count = ctypes.c_uint32()
            ids = (ctypes.c_int32 * 64)()
            library.ka_bucket_state(battle, slot, ctypes.byref(key), ctypes.byref(count), ids)
            if any(ids[i] == entity for i in range(count.value)):
                still = True
        if still:
            raise SystemExit('occupancy check: destroying the effect left it in a bucket')
        record['occupancy'] = 'Cell add/remove enter and leave the occupancy buckets'
    finally:
        library.ka_battle_free(battle)


def subset_holes(library, record):
    """4: `EntitySlotSet` freed-slot reuse is LIFO and holes stay holes."""
    reference = EntitySlotSet()
    battle = load(library, [baseline_unit(FIRST_IDENTITY)])
    try:
        library.ka_battle_seed_occupancy(battle)
        ids = []
        for _ in range(5):
            spec = ka_abi.EffectSpec(0, 0, 0, 28, 0, 0.0, 0.0, 0.0, 100, -1, 0, 30, 0, 0, -1, 0)
            ids.append(library.ka_create_effect(battle, ctypes.byref(spec)))
            reference.add(ids[-1])
        for entity in (ids[1], ids[3]):
            reference.remove(entity)
            library.ka_battle_destroy(battle, entity)
        length = ctypes.c_uint32()
        free_len = ctypes.c_uint32()
        version = ctypes.c_int32()
        slots = (ctypes.c_int32 * ka_abi.KA_MAX_OBJECTS)()
        free = (ctypes.c_uint32 * ka_abi.KA_MAX_OBJECTS)()
        # subsets[5] is `effects`; hole semantics are identical across subsets.
        library.ka_subset_state(battle, 5, ctypes.byref(length), ctypes.byref(free_len),
                                ctypes.byref(version), slots, free)
        got = [None if index in set(free[i] for i in range(free_len.value))
               else slots[index] for index in range(length.value)]
        want = list(reference.slots)
        want = [None if value is None else value for value in want]
        if got != want:
            raise SystemExit(f'subset holes: rust={got} python={want}')
        record['subset_holes'] = 'freed slots reuse LIFO and encode as holes, exactly as Python'
    finally:
        library.ka_battle_free(battle)


def projectile_sound_draws(library, record):
    """5: one Lib draw per launched projectile."""
    units = [baseline_unit(FIRST_IDENTITY, team=0, cell=(2, 6)),
             baseline_unit(FIRST_IDENTITY + 1, team=1, human=False, cell=(2, 8))]
    battle = load(library, units)
    try:
        row = ka_abi.KaSkill(700, 0, 1, 8 | 16, 0, 0, -1, 4, 3, 1, 16, 0, 7, 3, -1, 0)
        library.ka_battle_add_row(battle, ctypes.byref(row))
        for seb in (0, 7, 160):
            library.ka_battle_add_effect_resource(battle, seb, 30)
        command = ka_abi.KaCommand(29, FIRST_IDENTITY + 1, 700, 11, 19, 0)
        before = library.ka_battle_lib_draws(battle)
        used = ctypes.c_int32()
        status = library.ka_use_skill(battle, 0, ctypes.byref(command), 0, ctypes.byref(used))
        if status != 0:
            raise SystemExit(f'projectile sound: use_skill refused with {status}')
        launched = 0
        for slot in range(library.ka_object_count(battle)):
            entity = ka_abi.KaEntity()
            library.ka_object_entity(battle, slot, ctypes.byref(entity))
            if entity.has[39]:
                launched += 1
        drawn = library.ka_battle_lib_draws(battle) - before
        if launched == 0:
            raise SystemExit('projectile sound: no projectile was launched')
        if drawn != launched:
            raise SystemExit(f'projectile sound: {launched} projectiles but {drawn} Lib draws')
        record['projectile_sound'] = f'{launched} projectiles -> {drawn} Lib draws'
    finally:
        library.ka_battle_free(battle)


def capacity_limits(library, record):
    """6: the kernel accepts the maxima a real battle actually reaches."""
    observed = record['observed']
    for name, value, cap in (('objects', observed['objects'], ka_abi.KA_MAX_OBJECTS),
                             ('buckets', observed['buckets'], ka_abi.KA_MAX_BUCKETS),
                             ('commands', observed['commands'], 256),
                             ('invoking', observed['invoking'], 64),
                             ('board keys', observed['board_keys'], 48)):
        if value > cap:
            raise SystemExit(f'capacity: observed {name}={value} exceeds the kernel cap {cap}')
    record['capacity'] = (f'observed objects={observed["objects"]} buckets={observed["buckets"]} '
                          f'commands={observed["commands"]} all within kernel caps')


def revive_and_status_cases(library, record):
    """Canonical coverage for the two previously unexercised event kinds.

    `revive` is exercised through the real recovered path: a queued opcode-29 command whose skill is
    the recovered type-15 resurrection row, fired by `execute_shared_skill_commands` inside
    `update_fighters`, so the `revive` event is produced by the engine itself.

    `status_text` cannot be produced by the canonical engine: every `flags & 0x40000` row
    (113..119) has type 66/67, and `SharedControllers.skill_cells` returns no cells for anything
    other than types 26/27, so `opponent_in_range` is False, `eligible` is False and
    `use_fighter_skill` never reaches the `buff_cell` / `show_text` route. The case below proves
    that canonically and then requires both engines to agree that the use fails and no status event
    is emitted.
    """
    import copy
    import ka_events

    revive_rows = [row for row in ROWS.values() if row['type'] == 15]
    status_rows = [row for row in ROWS.values() if row['flags'] & 0x40000]
    if not revive_rows or not status_rows:
        raise SystemExit('canonical rows for revive/status coverage are missing')
    revive_skill = revive_rows[0]['id']
    status_skill = status_rows[0]['id']

    base = default_scenario()
    case = dict(encounterId=0, tickLimit=1000, mathSeed=7, libSeed=8)
    checkpoints = Checkpoints([40])
    run_scenario(dict(base, **case), checkpoints=checkpoints, stop_tick=40)
    stored = checkpoints.stored.get(40)
    if stored is None:
        raise SystemExit('revive/status base checkpoint missing')

    def prepare(engine, skill_id, kind):
        """Give the first own human the given skill and a command that fires it on the next step."""
        own = [unit for unit in engine.teams[0]]
        caster = own[0]
        ally = own[1]
        spec = engine.specs[caster['id']]
        spec['skills'] = [skill_id]
        spec['levels'] = [0]
        skill_component = engine.world.objects[caster['id']]['components'][51]
        skill_component['dataIds'] = [skill_id]
        skill_component['invocationLevels'] = [0]
        skill_component['maxSlotNum'] = 1
        caster['invoking'].clear()
        caster['commands'].clear()
        caster['commands'].append(dict(opcode=29, target=ally['id'], skill=skill_id, tick=11,
                                       duration=19, use_index=0, traceId=0))
        caster['board'][5] = 5
        caster['board'][17] = skill_id
        caster['board'][7] = 4              # most front, so the guard can pass
        caster['parameters'][11]['rawValue'] = 100000
        if kind == 'revive':
            ally['parameters'][10]['rawValue'] = 0
        else:
            # park an enemy in the band so only the cell-list defect can block the use
            enemy = engine.teams[1][0]
            enemy['cell'][0] = caster['cell'][0]
            enemy['cell'][1] = caster['cell'][1] + 1
        return engine

    def run_case(kind, skill_id):
        engine = prepare(copy.deepcopy(stored['engine']), skill_id, kind)
        battle_state = int(engine.battle_state)
        pre = ka_abi.engine_snapshot(engine, ROWS, battle_state, HUMAN_BASES)
        battle = ka_abi.load_snapshot(library, pre)
        try:
            import_diffs = ka_abi.diff_snapshots(pre, ka_abi.native_snapshot(library, battle, pre))
            if import_diffs:
                raise SystemExit(f'{kind}: state import is lossy: {import_diffs[:6]}')
            trace_start = len(engine.trace)
            fighter_step(engine)
            expected = ka_abi.engine_snapshot(engine, ROWS, battle_state, HUMAN_BASES)
            status = library.ka_update_fighters(battle, battle_state)
            got = ka_abi.native_snapshot(library, battle, pre)
            if status != 0:
                raise SystemExit(f'{kind}: native refused the step with {status}')
            expected_events = ka_events.normalize_python(engine.trace[trace_start:])
            native_events = ka_events.normalize_native(ka_abi.native_events(library, battle))
            if expected_events != native_events:
                raise SystemExit(f'{kind}: event mismatch\n python={expected_events}\n '
                                 f'native={native_events}')
            diffs = ka_abi.diff_snapshots(expected, got)
            if diffs:
                raise SystemExit(f'{kind}: state mismatch {diffs[:6]}')
            python_kinds = [event['kind'] for event in engine.trace[trace_start:]]
            return python_kinds, expected_events
        finally:
            library.ka_battle_free(battle)

    kinds, revive_events = run_case('revive', revive_skill)
    if 'revive' not in kinds:
        raise SystemExit(f'revive was not produced by the canonical engine (kinds={kinds})')
    record['revive'] = (f'skill {revive_skill}: canonical kinds {kinds}, '
                        f'{len(revive_events)} ordered events matched')

    kinds, status_events = run_case('status', status_skill)
    if 'status_text' in kinds or 'status_apply' in kinds:
        raise SystemExit(f'status route unexpectedly produced events: {kinds}')

    # Canonical unreachability proof over every generated-status row.
    engine = copy.deepcopy(stored['engine'])
    proof = []
    for row in status_rows:
        caster = engine.units[engine.teams[0][0]['id']]
        cells = engine.skill_cells(caster['id'], row)
        in_range = engine.opponent_in_range(caster['id'], row)
        eligible = engine.eligible(caster['id'], row, None)
        proof.append((row['id'], row['type'], len(cells), bool(in_range), bool(eligible)))
    if any(entry[2] or entry[3] or entry[4] for entry in proof):
        raise SystemExit(f'status reachability proof failed: {proof}')
    record['status_text'] = (f'canonical kinds {kinds} (no status event, as proven): '
                             f'{len(proof)} generated-status rows all have zero cells, '
                             f'opponent_in_range False and eligible False')


def regressions(library):
    record = dict(observed=dict(objects=0, buckets=0, commands=0, invoking=0, board_keys=0))
    identity_vs_index(library, record)
    change_animation_side_effects(library, record)
    occupancy_callbacks(library, record)
    subset_holes(library, record)
    projectile_sound_draws(library, record)
    revive_and_status_cases(library, record)
    return record


def benchmark(library, record):
    base = default_scenario()
    case = dict(encounterId=19, tickLimit=7000, mathSeed=7, libSeed=8)
    starts = [1, 40, 120]
    checkpoints = Checkpoints(starts)
    run_scenario(dict(base, **case), checkpoints=checkpoints, stop_tick=max(starts))
    rows = []
    for start in starts:
        stored = checkpoints.stored.get(start)
        if stored is None:
            continue
        pre = ka_abi.engine_snapshot(stored['engine'], ROWS, int(stored['engine'].battle_state),
                                     HUMAN_BASES)
        horizon = 30
        rounds = 5
        imports, exports, python_times, native_times = [], [], [], []
        for _ in range(rounds):
            mark = time.perf_counter()
            battle = ka_abi.load_snapshot(library, pre)
            imports.append(time.perf_counter() - mark)
            mark = time.perf_counter()
            ka_abi.native_snapshot(library, battle, pre)
            exports.append(time.perf_counter() - mark)
            library.ka_battle_free(battle)
            import copy
            engine = copy.deepcopy(stored['engine'])
            mark = time.perf_counter()
            for _ in range(horizon):
                fighter_step(engine)
            python_times.append(time.perf_counter() - mark)
            battle = ka_abi.load_snapshot(library, pre)
            completed = ctypes.c_uint32()
            status = ctypes.c_int32()
            mark = time.perf_counter()
            library.ka_run_native_steps(battle, pre['config']['battle_state'], horizon,
                                        ctypes.byref(completed), ctypes.byref(status))
            native_times.append(time.perf_counter() - mark)
            library.ka_battle_free(battle)
        rows.append(dict(
            start=start, horizon=horizon, units=len(pre['units']), objects=len(pre['objects']),
            import_ms=statistics.median(imports) * 1e3,
            export_ms=statistics.median(exports) * 1e3,
            python_ms=statistics.median(python_times) * 1e3,
            native_total_ms=statistics.median(native_times) * 1e3))
    record['benchmark'] = rows
    return rows


def main():
    library = ka_abi.load()
    record = regressions(library)
    print('regression assertions:')
    for key in ('identity', 'change_animation', 'occupancy', 'subset_holes', 'projectile_sound',
                'revive', 'status_text'):
        print(f'  {key}: {record[key]}')
    observed = record['observed']
    base = default_scenario()
    deepest = dict(encounterId=19, tickLimit=7000, mathSeed=907303519, libSeed=267534053)
    marks = {}
    for case, tick in ((dict(encounterId=19, tickLimit=7000, mathSeed=7, libSeed=8), 2000),):
        checkpoints = Checkpoints([tick])
        run_scenario(dict(base, **case), checkpoints=checkpoints, stop_tick=tick)
        stored = checkpoints.stored.get(tick)
        pre = ka_abi.engine_snapshot(stored['engine'], ROWS, int(stored['engine'].battle_state),
                                     HUMAN_BASES)
        observed.update(objects=len(pre['objects']), buckets=len(pre['buckets']),
                        commands=max((len(u['commands']) for u in pre['units']), default=0),
                        invoking=max((len(u['invoking']) for u in pre['units']), default=0),
                        board_keys=max((len(u['board']) for u in pre['units']), default=0))
    capacity_limits(library, record)
    print(f'  capacity (re-measured on a real 2000-tick state): {record["capacity"]}')
    try:
        rows = benchmark(library, record)
    except SystemExit:
        raise
    except Exception as error:  # noqa: BLE001
        print(f'benchmark unavailable: {error!r}')
        return 0
    print('benchmark (median of 5, horizon 30, real captured states):')
    for row in rows:
        native_exec = row['native_total_ms'] - row['export_ms'] - row['import_ms'] / row['horizon']
        print(f'  from tick {row["start"]}: units={row["units"]} objects={row["objects"]} '
              f'python={row["python_ms"]:.2f}ms rust_native={row["native_total_ms"]:.2f}ms '
              f'import={row["import_ms"]:.2f}ms export={row["export_ms"]:.2f}ms '
              f'speedup={row["python_ms"]/row["native_total_ms"]:.1f}x')
    return 0


if __name__ == '__main__':
    sys.exit(main())
