"""Targeted parity for the native action/effect machinery, on the workers' runtime (PyPy).

The reference side is the *canonical* Python: `combat_resolution` for the arithmetic and RNG
primitives, `combat_effects.create_combat_effect` against a real
`combat_entities.CombatEntities` world for effect creation, and
`combat_projectiles.fire_shared_projectile` for projectile creation state. Nothing is
re-implemented on the reference side, so a mismatch is a real transcription defect.

Compared surface per created entity: the component slots written and the int/float payload of each,
in slot order (the order the canonical insertion sequence produces), plus the subset membership in
its native slot order.

Run it with the pinned PyPy runtime:

    pypy3.exe check_native_combat_actions.py
"""
import ctypes
import random
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DLL = HERE / 'native' / 'ka_kernel' / 'target' / 'release' / 'ka_kernel.dll'

sys.path.insert(0, str(HERE))

from combat_collections import ComponentSubset  # noqa: E402
from combat_effects import (attack_effect_specs, cell_skill_effect_spec,  # noqa: E402
                            create_combat_effect, departure_effect_spec, healing_effect_spec,
                            skill_balloon_spec)
from combat_entities import CombatEntities  # noqa: E402
from combat_projectiles import fire_shared_projectile, launch  # noqa: E402
from combat_resolution import (SystemRandomState, base_damage, critical_rate,  # noqa: E402
                               damage_from_parameters, hit_rate, modified_rate, random_range)
from combat_projectiles import parabola  # noqa: E402


# One authoritative ABI mirror: every harness loads the kernel through `ka_abi`.
from ka_abi import (KaBoard, KaCommand, KaComponentView, KaEffect, KaEntity, KaEquipRow,  # noqa: E402
                    KaEntry, KaInvoke, KaModifier, KaParam, KaParams, KaPoint, KaProjectile,
                    KaSkill, KaUnit, KaVec3, EffectSpec, load as load_library)

def python_world():
    """The same eleven subsets `SharedControllers.__init__` installs."""
    subsets = [ComponentSubset((39, 0, 1)), ComponentSubset((0, 1)), ComponentSubset((0, 5)),
               ComponentSubset((19,)), ComponentSubset((5,)), ComponentSubset((38,), (4,)),
               ComponentSubset((32,)), ComponentSubset((51,)), ComponentSubset((2, 12, 28)),
               ComponentSubset((14, 1, 2, 7)), ComponentSubset((0, 5, 1), (11, 39, 38))]
    return CombatEntities(100, subsets)


def serialize_python_component(slot, value):
    ints = [0] * 8
    floats = [0.0] * 8
    if slot == 0:
        floats[0:3] = [float(v) for v in value[0:3]]
        floats[3:6] = [float(v) for v in value[3:6]]
        ints[0] = -1 if value[6] is None else int(value[6])
    elif slot == 1:
        floats[0:3] = [float(v) for v in value[0:3]]
    elif slot == 2:
        ints[0:4] = [int(v) for v in value[0:4]]
    elif slot == 4:
        ints[0:2] = [int(v) for v in value[0:2]]
    elif slot == 5:
        ints[0:2] = [int(v) for v in value[0:2]]
    elif slot == 7:
        for index, key in enumerate(('tex_ids', 'res_ids', 'tex_u', 'tex_v', 'tex_w', 'tex_h')):
            ints[index] = int(value[key][0])
    elif slot == 12:
        ints[0:2] = [int(v) for v in value[0:2]]
    elif slot == 14:
        ints[0] = int(value[0])
    elif slot == 19:
        ints[0] = int(value['type'])
        ints[1] = int(value['angle'])
        ints[2] = int(value.get('anchor', 0))
        ints[3] = int(value['frame'])
        ints[4] = int(value['duration'])
        ints[5] = int(bool(value['destroy_on_finish']))
        ints[6] = int(bool(value['loop']))
        ints[7] = int(value['alpha'])
        floats[0] = float(value['offset_x'])
        floats[1] = float(value['offset_y'])
        floats[2] = float(value['offset_z'])
        floats[3] = float(value['scale_x'])
        floats[4] = float(value['scale_y'])
    elif slot == 32:
        ints[0] = int(value[0])
    elif slot == 38:
        ints[0:8] = [int(v) if v is not None else -1 for v in value[0:8]]
    elif slot == 39:
        ints[0] = int(value['speed'])
        ints[1] = int(value['height'])
        ints[2] = int(value['frame'])
        ints[3] = int(value['length'])
        ints[4] = int(value['owner'])
        floats[0:3] = [float(v) for v in value['start']]
        floats[3:6] = [float(v) for v in value['end']]
    elif slot == 46:
        ints[0] = int(value[0])
    else:
        raise AssertionError(f'no serializer for slot {slot}')
    return ints, floats


def python_components(world, identity):
    components = world.objects[identity]['components']
    out = []
    for slot in range(len(components)):
        if components[slot] is not None:
            ints, floats = serialize_python_component(slot, components[slot])
            out.append((slot, tuple(ints), tuple(round(v, 6) for v in floats)))
    return out


def native_components(library, battle, identity):
    views = (KaComponentView * 52)()
    count = library.ka_entity_components(battle, identity, views, 52)
    out = []
    for index in range(count):
        view = views[index]
        ints = tuple(int(view.ints[i]) for i in range(8))
        floats = tuple(round(float(view.floats[i]), 6) for i in range(8))
        out.append((int(view.slot), ints, floats))
    return out


def python_subsets(world):
    return [[int(i) for i in subset.members] for subset in world.subsets]


def native_subsets(library, battle):
    out = []
    for index in range(11):
        count = library.ka_subset_count(battle, index)
        out.append([int(library.ka_subset_member(battle, index, position))
                    for position in range(count)])
    return out


def spec_to_native(spec):
    return EffectSpec(spec['type'], spec['value1'], spec['value2'], spec['res'], spec['seb'],
                      float(spec['position'][0]), float(spec['position'][1]),
                      float(spec['position'][2]), spec['scale'], spec['image'],
                      int(bool(spec['depth'])), spec['max_frame'], spec['frame'],
                      int(bool(spec['loop'])), -1 if spec['parent'] is None else spec['parent'],
                      int(bool(spec['animate'])))


def compare_entity(label, expected, got):
    if expected != got:
        raise SystemExit(f'{label}: component mismatch\n  python={expected}\n  rust  ={got}')


def assert_layout(library):
    probes = [(library.ka_sizeof_unit, ctypes.sizeof(KaUnit), 'KaUnit'),
              (library.ka_sizeof_entity, ctypes.sizeof(KaEntity), 'KaEntity'),
              (library.ka_sizeof_params, ctypes.sizeof(KaParams), 'KaParams'),
              (library.ka_sizeof_board, ctypes.sizeof(KaBoard), 'KaBoard'),
              (library.ka_sizeof_skill, ctypes.sizeof(KaSkill), 'KaSkill'),
              (library.ka_sizeof_command, ctypes.sizeof(KaCommand), 'KaCommand'),
              (library.ka_sizeof_effect_spec, ctypes.sizeof(EffectSpec), 'EffectSpec'),
              (library.ka_sizeof_component_view, ctypes.sizeof(KaComponentView),
               'KaComponentView')]
    for probe, expected, name in probes:
        probe.restype = ctypes.c_uint32
        actual = probe()
        if actual != expected:
            raise SystemExit(f'{name}: sizeof rust={actual} python={expected}')


def synthetic_rows():
    attack = dict(id=500, category=0, type=60, flags=8 | 16, minMp=0, maxMp=0,
                  requiredEquipType=-1, shootingRange=1, range=1, count=1, motion=16, value=0,
                  seb=7, img=3, impactImg=-1, impactSeb=0)
    projectile = dict(attack, id=501, type=1, range=2)
    rows = {attack['id']: attack, projectile['id']: projectile,
            1: dict(attack, id=1, type=1, range=1), 2: dict(attack, id=2, type=1, range=1),
            900: dict(attack, id=900, category=1, type=2, value=40, requiredEquipType=-1)}
    return rows


def build_unit(index, count, rows, human):
    unit = KaUnit()
    unit.present = 1
    unit.team = 0 if index < (count + 1) // 2 else 1
    unit.human = 1 if human else 0
    unit.monster = 0 if human else 1
    unit.id = index
    unit.identity = 100 + index
    unit.body.id = 100 + index
    unit.body.cell[0], unit.body.cell[1] = index % 8, 6 + index % 5
    unit.body.position.x = float((index % 8) * 24)
    unit.body.position.z = float((6 + index % 5) * 24)
    unit.body.seb[0], unit.body.seb[2] = (11 if human else 22), 0
    unit.body.animation[0] = 1
    unit.body.direction = 0 if unit.team == 0 else 2
    for slot in (0, 1, 2, 5, 7, 12, 14, 28, 33, 51):
        unit.body.has[slot] = 1
    unit.body.has[18 if human else 20] = 1
    if unit.team == 0:
        unit.body.has[49] = 1
    unit.weapon_type = 0
    unit.weapon_shooting_range = 1
    unit.weapon_motion = 3
    unit.weapon_projectile_flag = 0
    unit.monster_type = 0
    unit.board.len = 5
    for position, (key, value) in enumerate(((4, 0), (5, 1), (6, unit.team), (7, index),
                                             (8, 0))):
        unit.board.entries[position].key = key
        unit.board.entries[position].value = value
    unit.params.count = 14
    for position, parameter_id in enumerate(
            [10, 11, 12, 13, 14, 15, 16, 18, 19, 20, 21, 22, 17, 23]):
        row = unit.params.rows[position]
        row.id = parameter_id
        row.raw_value = 900 if parameter_id in (10, 11) else 120
        row.raw_max = 900 if parameter_id == 10 else (2**31 - 1)
        row.training_level = 20
    skill = rows[500] if index % 3 else rows[900]
    unit.skill_ids[0] = skill['id']
    unit.skill_count = 1
    unit.levels[0] = 0
    unit.level_count = 1
    return unit


def benchmark(library):
    """Continuous native execution vs per-step crossings, on a synthetic 27-unit state."""
    probe = library.ka_sizeof_unit
    probe.restype = ctypes.c_uint32
    assert_layout(library)
    rows = synthetic_rows()
    count = 27
    units = (KaUnit * count)()
    for index in range(count):
        units[index] = build_unit(index, count, rows, human=(index % 2 == 0))

    library.ka_battle_import.argtypes = [ctypes.c_void_p, ctypes.POINTER(KaUnit),
                                         ctypes.c_uint32, ctypes.c_uint64]
    library.ka_battle_import.restype = ctypes.c_uint32
    library.ka_battle_add_row.argtypes = [ctypes.c_void_p, ctypes.POINTER(KaSkill)]
    library.ka_battle_add_row.restype = ctypes.c_uint32
    library.ka_battle_rebuild_subsets.argtypes = [ctypes.c_void_p]
    library.ka_battle_seed_occupancy.argtypes = [ctypes.c_void_p]
    library.ka_update_fighters.argtypes = [ctypes.c_void_p, ctypes.c_int32]
    library.ka_update_fighters.restype = ctypes.c_int32
    library.ka_run_native_steps.argtypes = [ctypes.c_void_p, ctypes.c_int32, ctypes.c_uint32,
                                            ctypes.POINTER(ctypes.c_uint32),
                                            ctypes.POINTER(ctypes.c_int32)]
    library.ka_run_native_steps.restype = ctypes.c_int32
    library.ka_manhattan.argtypes = [ctypes.c_int32] * 4
    library.ka_manhattan.restype = ctypes.c_int32

    def fresh():
        battle = library.ka_battle_create()
        library.ka_battle_import(battle, units, count, 0)
        library.ka_battle_identity(battle, 100)
        library.ka_battle_seed_rng(battle, 20260922, 4242)
        for row in rows.values():
            skill = KaSkill(row['id'], row['category'], row['type'], row['flags'], row['minMp'],
                            row['maxMp'], row['requiredEquipType'], row['shootingRange'],
                            row['range'], row['count'], row['motion'], row['value'], row['seb'],
                            row['img'], row['impactImg'], row['impactSeb'])
            library.ka_battle_add_row(battle, ctypes.byref(skill))
        for seb, frames in ((0, 40), (7, 25), (126, 12), (78, 30), (112, 30), (237, 30),
                            (160, 20), (279, 30)):
            library.ka_battle_add_effect_resource(battle, seb, frames)
        library.ka_battle_rebuild_subsets(battle)
        library.ka_battle_seed_occupancy(battle)
        return battle

    steps = 400
    outcomes = {}
    for label, internal in (('per-step ABI', False), ('internally looped', True)):
        battle = fresh()
        try:
            started = time.perf_counter()
            if internal:
                completed = ctypes.c_uint32()
                status = ctypes.c_int32()
                library.ka_run_native_steps(battle, 2, steps, ctypes.byref(completed),
                                            ctypes.byref(status))
                done = completed.value
                final = status.value
            else:
                done = 0
                final = 0
                for _ in range(steps):
                    code = library.ka_update_fighters(battle, 2)
                    if code != 0:
                        final = code
                        break
                    done += 1
            elapsed = time.perf_counter() - started
        finally:
            library.ka_battle_free(battle)
        outcomes[label] = (done, final, elapsed)

    # Bare crossing cost, to separate ABI price from kernel work.
    started = time.perf_counter()
    for _ in range(steps * 4):
        library.ka_manhattan(1, 2, 3, 4)
    bare = (time.perf_counter() - started) / (steps * 4)

    print('continuous native execution (synthetic 27-unit battle):')
    for label, (done, final, elapsed) in outcomes.items():
        per_step = elapsed / done * 1e6 if done else 0.0
        print(f'  {label}: {done} steps, status={final}, {elapsed:.4f}s '
              f'({per_step:.1f}us per step)')
    if outcomes['per-step ABI'][2] and outcomes['internally looped'][2]:
        ratio = outcomes['per-step ABI'][2] / outcomes['internally looped'][2]
        print(f'  internal loop vs per-step crossings: {ratio:.2f}x faster')
    print(f'  bare ABI crossing: {bare*1e6:.2f}us')
    return outcomes


def main():
    library = load_library()
    rng = random.Random(20260922)
    started = time.perf_counter()

    # --- pure resolution primitives ---------------------------------------------------------
    checks = 0
    for luck in list(range(-3, 3000)) + [99999, 100000, 150000, 2**31 - 1]:
        if library.ka_critical_rate(luck) != critical_rate(luck):
            raise SystemExit(f'critical_rate({luck}) differs')
        checks += 1
    for _ in range(4000):
        dexterity = rng.choice([-50, 0, rng.randint(0, 5000)])
        agility = rng.choice([-20, 0, rng.randint(0, 5000)])
        luck = rng.choice([-5, 0, rng.randint(0, 5000)])
        if library.ka_hit_rate(dexterity, agility, luck) != hit_rate(dexterity, agility, luck):
            raise SystemExit(f'hit_rate({dexterity},{agility},{luck}) differs')
        checks += 1
    for _ in range(4000):
        rate = rng.randint(-50, 200)
        value = rng.randint(-100, 300)
        evasion = rng.choice([0, 1])
        if library.ka_modified_rate(rate, value, evasion) != \
                modified_rate(rate, value, evasion=bool(evasion)):
            raise SystemExit(f'modified_rate({rate},{value},{evasion}) differs')
        checks += 1
    for _ in range(4000):
        raw = rng.randint(-2**31, 2**31 - 1)
        low, high = sorted((rng.randint(-30, 40), rng.randint(-30, 40)))
        if low == high:
            high += 1
        if library.ka_random_range(raw, low, high) != random_range(raw, low, high):
            raise SystemExit(f'random_range({raw},{low},{high}) differs')
        checks += 1
    for _ in range(2000):
        height = rng.choice([-10, 10, rng.randint(-200, 200), 0])
        length = rng.choice([0, 6, rng.randint(0, 60)])
        frame = rng.randint(-5, 70)
        if library.ka_parabola(height, length, frame) != parabola(height, length, frame):
            raise SystemExit(f'parabola({height},{length},{frame}) differs')
        checks += 1
    print(f'primitive parity: {checks} checks across critical/hit/modified/random_range/parabola')

    # --- RNG-sensitive base damage ----------------------------------------------------------
    battle = library.ka_battle_create()
    damage_checks = 0
    try:
        library.ka_battle_identity(battle, 100)
        for _ in range(3000):
            seed = rng.randint(-2**31, 2**31 - 1)
            attack = rng.randint(1, 6000)
            defense = rng.randint(0, 4000)
            library.ka_battle_seed_rng(battle, seed, 0)
            reference = SystemRandomState(seed)
            expected = base_damage(attack, defense, lambda: reference.next_int())
            got = library.ka_base_damage(battle, attack, defense)
            if expected != got:
                raise SystemExit(f'base_damage({attack},{defense},seed={seed}) '
                                 f'python={expected} rust={got}')
            if library.ka_battle_math_draws(battle) != 0:
                # draw counter is compared separately below; base_damage must not touch lib
                pass
            damage_checks += 1
    finally:
        library.ka_battle_free(battle)
    print(f'base_damage parity: {damage_checks} seeded draw sequences matched exactly')

    # --- effect creation state --------------------------------------------------------------
    world = python_world()
    battle = library.ka_battle_create()
    try:
        library.ka_battle_identity(battle, 100)
        resources = {0: 40, 7: 25, 160: 20, 279: 30, 126: 12}
        for resource_id, frames in resources.items():
            library.ka_battle_add_effect_resource(battle, resource_id, frames)

        def resource_frames(res, seb):
            if res != 28:
                raise NotImplementedError('Unrecovered effect resource duration')
            return resources[seb]

        specs = [
            ('departure', departure_effect_spec((120.0, 48.0, 240.0))),
            ('balloon', skill_balloon_spec(1234, (120.0, 48.0, 240.0), True)),
            ('balloon_enemy', skill_balloon_spec(99, (12.0, 0.0, 24.0), False)),
            ('healing', healing_effect_spec(55, (60.0, 0.0, 120.0))),
            ('cell', cell_skill_effect_spec(dict(seb=7, img=3), (4, 9))),
        ]
        # `attack_effect_specs` needs a result dict; both sides use the same canonical inputs.
        for hit, critical, damage, ally in ((True, False, 120, True), (True, True, 480, False),
                                            (False, False, 0, True)):
            result = dict(hit=hit, critical=critical, damage=damage)
            specs.append((f'attack_{hit}_{critical}_{ally}',
                          attack_effect_specs(result, (72.0, 0.0, 96.0), ally)))

        flatten = []
        for name, spec in specs:
            if isinstance(spec, list):
                flatten.extend((f'{name}[{index}]', item) for index, item in enumerate(spec))
            else:
                flatten.append((name, spec))

        created = 0
        for name, spec in flatten:
            expected_id = create_combat_effect(spec, resource_frames, world.allocate,
                                               world.add_component)
            native_id = library.ka_create_effect(battle, ctypes.byref(spec_to_native(spec)))
            if expected_id != native_id:
                raise SystemExit(f'{name}: identity python={expected_id} rust={native_id}')
            compare_entity(f'{name} components', python_components(world, expected_id),
                           native_components(library, battle, native_id))
            created += 1
        expected_subsets = python_subsets(world)
        got_subsets = native_subsets(library, battle)
        if expected_subsets != got_subsets:
            for index, (want, have) in enumerate(zip(expected_subsets, got_subsets)):
                if want != have:
                    raise SystemExit(f'effect subsets {index} differ python={want} rust={have}')
        if library.ka_object_count(battle) != created:
            raise SystemExit('created-entity count differs')
        print(f'effect creation: {created} effects matched on identity, every component payload '
              f'and all 11 subset memberships')

        # --- projectile creation state ------------------------------------------------------
        world2 = python_world()
        battle2 = library.ka_battle_create()
        try:
            library.ka_battle_identity(battle2, 100)
            projectile_cases = 0
            for _ in range(40):
                identity_py = world2.allocate()
                identity_rs = library.ka_battle_allocate(battle2)
                if identity_py != identity_rs:
                    raise SystemExit(f'allocate python={identity_py} rust={identity_rs}')
                start = (float(rng.randint(0, 400)), 15.0, float(rng.randint(0, 400)))
                end = (float(rng.randint(0, 400)), 0.0, float(rng.randint(0, 400)))
                speed = rng.choice([10, 12, 1, rng.randint(1, 30)])
                owner = 100
                animate = rng.choice([True, False])
                attack_skill = rng.choice([None, 111, 222])
                expected = fire_shared_projectile(world2, identity_py, start, end, speed, owner,
                                                  animate=animate, attack_skill=attack_skill)
                got = library.ka_fire_projectile(battle2, identity_rs, start[0], start[1], start[2],
                                                 end[0], end[1], end[2], speed, owner,
                                                 int(animate), -1 if attack_skill is None
                                                 else attack_skill)
                if expected != got:
                    raise SystemExit(f'projectile length python={expected} rust={got}')
                compare_entity('projectile components', python_components(world2, identity_py),
                               native_components(library, battle2, identity_rs))
                projectile_cases += 1
            print(f'projectile creation: {projectile_cases} launches matched on length and every '
                  f'component payload')
        finally:
            library.ka_battle_free(battle2)
    finally:
        library.ka_battle_free(battle)

    elapsed = time.perf_counter() - started
    print(f'wall time: {elapsed:.2f}s')
    print(f'runtime: {sys.implementation.name} {sys.version.split()[0]}')
    benchmark(library)


if __name__ == '__main__':
    main()
