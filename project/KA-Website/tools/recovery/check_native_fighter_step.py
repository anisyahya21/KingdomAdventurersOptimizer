"""Differential parity for the ported fighter phase, on the workers' own runtime (PyPy).

The native kernel now owns a board/state model, the keyed math RNG and the ported
`update_fighters -> SharedControllers.update -> decide -> choose` path. This harness asks the only
question that matters for that slice: given an identical randomized battle state, does the native
phase reach an identical state, identical RNG position and an identical ordered event log?

The reference side is *not* a second implementation of the rules. It imports the project's own
recovered primitives - `combat_resolution`, `combat_states`, `combat_skill_selection`,
`combat_targeting`, `combat_navigation`, `combat_skills`, `combat_geometry`, `combat_commands`,
`combat_parameters` - and composes them in the same order `combat_shared_controllers` does. Only the
controller composition itself is mirrored here, because building a real `SharedControllers` needs a
full world with projectiles, effects and prizes.

Deliberate boundary, identical on both sides: `use_skill` inside the opcode-29 command queue is
*recorded*, not applied. Applying it mutates HP and world state through the attack/projectile/cure
routes, which are later slices. The queue bookkeeping - tick, duration, use index, animation
rate/frame, removal - is compared exactly, as is the ordered sequence of use calls.

Run it with the pinned PyPy runtime:

    pypy3.exe check_native_fighter_step.py
"""
import ctypes
import random
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DLL = HERE / 'native' / 'ka_kernel' / 'target' / 'release' / 'ka_kernel.dll'

sys.path.insert(0, str(HERE))

from combat_initial_state import effective_parameter_rate  # noqa: E402
from combat_parameters import HUMAN_TRAINING_PARAMETERS, average_training_level  # noqa: E402
from combat_resolution import (SystemRandomState, attack_interval,  # noqa: E402
                               decide_skill_invocation, random_below, skill_mp_cost)
from combat_geometry import battle_move_base, fighter_path  # noqa: E402
from combat_navigation import front_opponent, queue_position, same_grid  # noqa: E402
from combat_skill_selection import active_skill_infos, attack_skill_candidates  # noqa: E402
from combat_skills import can_use_skill  # noqa: E402
from combat_states import (cell_to_grid, change_fighter_state, decide_next_state,  # noqa: E402
                           is_most_front, is_normal_attack_target)
from combat_targeting import nearest_skill_target, opponent_in_skill_cells  # noqa: E402
from combat_commands import enqueue_skill_command, execute_skill_queue  # noqa: E402
from combat_tick import update_fighters  # noqa: E402

# Event codes shared with the kernel (`state.rs`).
EV_STATE, EV_ENQUEUE, EV_INVOCATION, EV_ANIMATION, EV_USE = 1, 2, 3, 4, 5


class KaEntry(ctypes.Structure):
    _fields_ = [('key', ctypes.c_int32), ('value', ctypes.c_int64)]


class KaBoard(ctypes.Structure):
    _fields_ = [('len', ctypes.c_uint32), ('entries', KaEntry * 24)]


class KaSkill(ctypes.Structure):
    _fields_ = [('id', ctypes.c_int32), ('category', ctypes.c_int32), ('kind', ctypes.c_int32),
                ('flags', ctypes.c_int32), ('min_mp', ctypes.c_int32), ('max_mp', ctypes.c_int32),
                ('required_equip_type', ctypes.c_int32), ('shooting_range', ctypes.c_int32),
                ('range', ctypes.c_int32), ('count', ctypes.c_int32), ('motion', ctypes.c_int32),
                ('value', ctypes.c_int32)]


class KaCommand(ctypes.Structure):
    _fields_ = [('opcode', ctypes.c_int32), ('target', ctypes.c_int32), ('skill', ctypes.c_int32),
                ('tick', ctypes.c_int32), ('duration', ctypes.c_int32), ('use_index', ctypes.c_int32)]


class KaInvoke(ctypes.Structure):
    _fields_ = [('skill', ctypes.c_int32), ('remaining', ctypes.c_int32)]


class KaPoint(ctypes.Structure):
    _fields_ = [('x', ctypes.c_int32), ('y', ctypes.c_int32)]


class KaUnit(ctypes.Structure):
    _fields_ = [
        ('present', ctypes.c_uint8), ('exists', ctypes.c_uint8), ('team', ctypes.c_uint8),
        ('human', ctypes.c_uint8), ('monster', ctypes.c_uint8),
        ('flags', ctypes.c_int32), ('id', ctypes.c_int32),
        ('hp_value', ctypes.c_int32), ('hp_max', ctypes.c_int32), ('mp_value', ctypes.c_int32),
        ('agility', ctypes.c_int32), ('cell_x', ctypes.c_int32), ('cell_y', ctypes.c_int32),
        ('direction', ctypes.c_int32),
        ('pos_x', ctypes.c_float), ('pos_z', ctypes.c_float),
        ('vel_x', ctypes.c_float), ('vel_z', ctypes.c_float), ('offset_z', ctypes.c_float),
        ('animation_rate', ctypes.c_int32), ('animation_frame', ctypes.c_int32),
        ('weapon_type', ctypes.c_int32), ('weapon_shooting_range', ctypes.c_int32),
        ('weapon_motion', ctypes.c_int32),
        ('training', ctypes.c_int32 * 12),
        ('board', KaBoard), ('long_board', KaBoard),
        ('skill_ids', ctypes.c_int32 * 12), ('skill_count', ctypes.c_uint32),
        ('levels', ctypes.c_int32 * 12), ('level_count', ctypes.c_uint32),
        ('invoking', KaInvoke * 8), ('invoking_count', ctypes.c_uint32),
        ('commands', KaCommand * 8), ('command_count', ctypes.c_uint32),
        ('path', KaPoint * 8), ('path_count', ctypes.c_uint32),
    ]


class KaEvent(ctypes.Structure):
    _fields_ = [('kind', ctypes.c_int32), ('unit', ctypes.c_int32), ('a', ctypes.c_int32),
                ('b', ctypes.c_int32), ('c', ctypes.c_int32)]


def load_library():
    raise SystemExit(
        'SUPERSEDED: this harness was written against the pre-action kernel, where the fighter\n'
        'step recorded `use_skill` instead of applying it and `KaUnit` carried flat effective\n'
        'parameter reads. The kernel now owns the parameter layer, the created-entity arena and\n'
        'the real `use_skill` path, so the old struct layout no longer describes the ABI and the\n'
        'old reference would silently compare against state the kernel no longer produces.\n'
        'Verified coverage for the action machinery lives in check_native_combat_actions.py;\n'
        'this corpus must be re-pointed at a canonical-world controller mirror before it can be\n'
        'run again. It is disabled deliberately rather than left to mis-parse.\n')


def _load_library_legacy():
    if not DLL.is_file():
        raise SystemExit(f'build it first: cargo build --release in {DLL.parent.parent}')
    library = ctypes.CDLL(str(DLL))
    library.ka_battle_create.restype = ctypes.c_void_p
    library.ka_battle_import.argtypes = [ctypes.c_void_p, ctypes.POINTER(KaUnit),
                                         ctypes.c_uint32, ctypes.c_uint64]
    library.ka_battle_import.restype = ctypes.c_uint32
    library.ka_battle_seed_rng.argtypes = [ctypes.c_void_p, ctypes.c_int32, ctypes.c_int32]
    library.ka_battle_config.argtypes = [ctypes.c_void_p, ctypes.c_int32, ctypes.c_int32,
                                         ctypes.c_uint8]
    library.ka_battle_add_row.argtypes = [ctypes.c_void_p, ctypes.POINTER(KaSkill)]
    library.ka_battle_add_row.restype = ctypes.c_uint32
    library.ka_update_fighters.argtypes = [ctypes.c_void_p, ctypes.c_int32]
    library.ka_update_fighters.restype = ctypes.c_int32
    library.ka_battle_export.argtypes = [ctypes.c_void_p, ctypes.POINTER(KaUnit)]
    library.ka_battle_export.restype = ctypes.c_uint32
    library.ka_battle_export_events.argtypes = [ctypes.c_void_p, ctypes.POINTER(KaEvent)]
    library.ka_battle_export_events.restype = ctypes.c_uint32
    library.ka_battle_event_count.argtypes = [ctypes.c_void_p]
    library.ka_battle_event_count.restype = ctypes.c_uint32
    library.ka_battle_math_draws.argtypes = [ctypes.c_void_p]
    library.ka_battle_math_draws.restype = ctypes.c_uint32
    library.ka_battle_math_rng.argtypes = [ctypes.c_void_p]
    library.ka_battle_math_rng.restype = ctypes.c_void_p
    library.ka_rng_index.argtypes = [ctypes.c_void_p]
    library.ka_rng_index.restype = ctypes.c_int32
    library.ka_rng_partner.argtypes = [ctypes.c_void_p]
    library.ka_rng_partner.restype = ctypes.c_int32
    library.ka_rng_value.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    library.ka_rng_value.restype = ctypes.c_int32
    library.ka_battle_free.argtypes = [ctypes.c_void_p]
    library.ka_attack_interval.argtypes = [ctypes.c_int32]
    library.ka_attack_interval.restype = ctypes.c_int32
    return library


class Reference:
    """The controller composition, ordered exactly as combat_shared_controllers orders it."""

    def __init__(self, units, rows, math_seed, map_width, row_offset, movement_enabled):
        self.units = {unit['id']: unit for unit in units}
        self.order = list(self.units)
        self.rows = rows
        self.teams = {0: [], 1: []}
        for unit in units:
            self.teams[unit['team']].append(unit)
        self.target_order = sorted(self.units, key=lambda i: not self.units[i]['human'])
        self.map_width = map_width
        self.row_offset = row_offset
        self.movement_enabled = movement_enabled
        self.math_rng = SystemRandomState(math_seed)
        self.math_draws = 0
        self.events = []

    def emit(self, kind, unit, a, b, c):
        self.events.append((kind, unit, a, b, c))

    def value(self, i, key):
        if key == 11:
            return self.units[i]['mp_value']
        if key == 15:
            return self.units[i]['agility']
        return self.units[i]['hp_value']

    def maximum(self, i, key):
        return self.units[i]['hp_max']

    def rate(self, i, key):
        return effective_parameter_rate(self.value(i, key), self.maximum(i, key))

    def cost(self, i, skill):
        training = {key: {'trainingLevel': level} for key, level in
                    zip(HUMAN_TRAINING_PARAMETERS, self.units[i]['training'])}
        return skill_mp_cost(skill['minMp'], skill['maxMp'],
                             average_training_level(training, self.units[i]['human']),
                             monster=not self.units[i]['human'])

    def distance(self, a, b):
        return abs(self.units[a]['cell'][0] - self.units[b]['cell'][0]) \
            + abs(self.units[a]['cell'][1] - self.units[b]['cell'][1])

    def same_team(self, a, b):
        return self.units[a]['team'] == self.units[b]['team']

    def target_exists(self, t):
        return t is not None and t in self.units and self.units[t]['exists']

    def next_math(self, purpose='unclassified', bound=None):
        self.math_draws += 1
        return self.math_rng.next_int()

    def skill_cells(self, i, skill):
        from combat_geometry import battle_band, line_cells
        x, y = self.units[i]['cell']
        direction = self.units[i]['direction']
        if skill['type'] == 26:
            dx, dy = ((0, -1), (1, 0), (0, 1), (-1, 0))[direction % 4]
            return line_cells(x, y, dx, dy, skill['shootingRange'])
        if skill['type'] == 27:
            return battle_band(y, direction, skill['range'])
        return []

    def opponent_in_range(self, i, skill):
        return opponent_in_skill_cells([t for t in self.order if not self.same_team(i, t)],
                                       self.skill_cells(i, skill),
                                       lambda t: self.units[t]['board'][5],
                                       lambda t: self.units[t]['cell'])

    def eligible(self, i, s, t, first=True):
        return can_use_skill(s, check_mp=first, mp=self.value(i, 11), cost=self.cost(i, s),
            target_exists=self.target_exists(t), weapon_type=self.units[i]['weapon_type'],
            target_hp=self.value(t, 10) if t in self.units else 0,
            target_hp_rate=self.rate(t, 10) if t in self.units else 0,
            invoking=any(sid == s['id'] for sid, _ in self.units[i]['invoking']),
            battle_world=True, most_front=is_most_front(self.units[i]['board'][7]),
            opponent_in_range=self.opponent_in_range(i, s))

    def candidates(self, i):
        infos = active_skill_infos([self.rows[s] for s in self.units[i]['skill_ids']],
                                   self.units[i]['levels'], self.value(i, 11),
                                   lambda s: self.cost(i, s))
        for s, level in infos:
            if s['category'] != 1:
                continue
            target = nearest_skill_target(i, self.target_order, s['shootingRange'], self.distance,
                lambda t: self.same_team(i, t) and self.rate(t, 10) < 100)
            if self.eligible(i, s, target):
                yield s, level, target

        def attack_target(s):
            return nearest_skill_target(i, self.target_order, s['shootingRange'], self.distance,
                lambda t: not self.same_team(i, t) and self.rate(t, 10) > 0)

        yield from attack_skill_candidates(infos, attack_target, lambda s, t: self.eligible(i, s, t))

    def invoke(self, i, s, level):
        passed = decide_skill_invocation(s['type'], level, s['flags'],
            lambda n: random_below(self.next_math('skill_invocation', n), n))
        self.emit(EV_INVOCATION, i, s['id'], level, int(passed))
        return passed

    def choose(self, u):
        for s, level, target in self.candidates(u['id']):
            if self.invoke(u['id'], s, level):
                return s['id'], target
        return None

    def decide(self, u):
        i = u['id']
        if self.movement_enabled:
            team = self.teams[u['board'][6]]
            x, y = u['cell']
            forward = [x, y + (-1 if u['board'][6] == 0 else 1)]
            occupants = [v for v in self.order if self.units[v]['cell'] == forward]
            advance = not is_most_front(u['board'][7]) and not any(
                self.units[o]['board'][5] not in (7, 8) for o in occupants)
            decision = (u['board'][5], u['board'][6], u['cell'],
                        is_most_front(u['board'][7]), u['weapon_shooting_range'] > 1)
            navigation = dict(same_grid=same_grid(u, team), advanceable=advance,
                queue_position=(queue_position(u, team, lambda other: self.value(other['id'], 10),
                                               self.row_offset)
                                if u['board'][5] not in (2, 7, 8) and not 0 <= x < 5 else None))
            state, destination = decide_next_state(*decision, False, **navigation)
            if state == 1:
                state, destination = decide_next_state(
                    *decision, next(self.candidates(i), None) is not None, **navigation)
            if destination is not None:
                u['board'][13], u['board'][14] = destination
            return state
        return 3 if is_most_front(u['board'][7]) or u['weapon_shooting_range'] > 1 \
            or next(self.candidates(i), None) is not None else 1

    def animate(self, u, behavior):
        self.emit(EV_ANIMATION, u['id'], behavior, 0, 0)

    def enqueue(self, i, skill_id, target):
        command = enqueue_skill_command(self.units[i]['commands'], target, self.rows[skill_id],
                                        target_exists=self.target_exists(target))
        # The event log encodes the Python `None` target as the kernel's `-1`; this is a
        # representation mapping only, and the stored command target is compared separately.
        self.emit(EV_ENQUEUE, i, skill_id, -1 if target is None else target, 0)
        return command

    def change(self, u, state):
        i = u['id']
        self.emit(EV_STATE, i, u['board'][5], state, self.value(i, 10))

        def exit_moving(unit):
            unit['velocity'][0] = 0.
            unit['velocity'][2] = 0.
            unit['animation_rate'] = 1
            unit['path'].clear()
            unit['board'][7] = cell_to_grid(unit['board'][6], self.row_offset, unit['cell'])

        def exit_damaging(unit):
            grid, team = unit['board'][7], unit['board'][6]
            row = grid // 5
            column = grid - row * 5
            y = self.row_offset + (1 if team == 0 else 0) + (row if team == 0 else -row)
            unit['position'][0] = float(column * 24)
            unit['position'][2] = float(y * 24)
            unit['offset'][2] = 0.

        def exit_using_skill(unit):
            unit['board'][8] = 0
            unit['board'][17] = -1
            unit['long_board'][16] = -1
            self.animate(unit, 3)

        def enter_moving(unit):
            unit['path'] = fighter_path((unit['position'][0], unit['position'][2]),
                                        (float(unit['board'][13]), float(unit['board'][14])))
            unit['animation_rate'] = 2
            self.animate(unit, 2)

        def enter_damaging(unit):
            from combat_resolution import native_float_to_int
            unit['board'][12] = native_float_to_int(unit['position'][2])
            self.animate(unit, 30)

        def enter_using_skill(unit):
            skill_id = unit['board'].get(17, -1)
            if skill_id != -1:
                self.animate(unit, self.rows[skill_id]['motion'])
            else:
                self.change(unit, self.decide(unit))

        empty = lambda unit: None
        change_fighter_state(u, state,
            {0: empty, 1: empty, 2: exit_moving, 3: empty, 4: lambda unit: self.animate(unit, 3),
             5: exit_using_skill, 6: exit_damaging, 7: empty, 8: empty},
            {0: empty, 1: lambda unit: self.animate(unit, 3), 2: enter_moving,
             3: lambda unit: self.animate(unit, 3),
             4: lambda unit: self.animate(unit, self.units[unit['id']]['weapon_motion']),
             5: enter_using_skill, 6: enter_damaging, 7: empty, 8: empty})

    def ranged_target(self, u):
        i = u['id']
        return nearest_skill_target(i, [v['id'] for v in self.teams[1 - u['board'][6]]],
            u['weapon_shooting_range'], self.distance,
            lambda t: is_normal_attack_target(self.target_exists(t), self.value(t, 10),
                                              self.units[t]['board'][5]))

    def normal_target(self, u):
        return front_opponent([v['id'] for v in self.teams[1 - u['board'][6]]],
            lambda t: self.target_exists(t), lambda t: self.value(t, 10),
            lambda t: self.units[t]['board'][5], lambda t: self.units[t]['board'][7],
            lambda t: self.distance(u['id'], t))

    def tick_charging(self, u, sleeping):
        i = u['id']
        most_front = is_most_front(u['board'][7])
        long_range = u['weapon_shooting_range'] > 1
        if not sleeping:
            interval = attack_interval(self.value(i, 15))
            u['board'][8] = ((u['board'][8] + 1 + 2**31) % 2**32) - 2**31
            if u['board'][8] <= interval:
                return
            selected = self.choose(u)
            if selected is not None:
                skill, target = selected
                self.enqueue(i, skill, target)
                u['board'][17] = skill
                self.change(u, 5)
                return
            target = self.ranged_target(u) if long_range else None
            if target is None and most_front:
                target = self.normal_target(u)
            u['long_board'][16] = -1 if target is None else target
            if target is not None:
                self.change(u, 4)
                return
        decision = self.decide(u)
        if decision != 3:
            self.change(u, decision)

    def tick_using_skill(self, u):
        if any(command['opcode'] == 29 for command in u['commands']):
            return
        u['board'][8] = 0
        self.change(u, self.decide(u))

    def move_fighter(self, unit, target, speed):
        arrived, position, velocity = battle_move_base(
            (unit['position'][0], unit['position'][2]), target, speed,
            (unit['velocity'][0], unit['velocity'][2]))
        unit['position'][0], unit['position'][2] = position
        unit['velocity'][0], unit['velocity'][2] = velocity
        if arrived:
            unit['animation_rate'] = 1
        return arrived

    def update_moving(self, u):
        from combat_resolution import f32
        if u['path']:
            if self.move_fighter(u, u['path'][0], f32(4.46)):
                u['velocity'][0] = 0.
                u['velocity'][2] = 0.
                u['board'][7] = cell_to_grid(u['board'][6], self.row_offset, u['cell'])
                u['path'].pop(0)
        if not u['path']:
            u['direction'] = 0 if u['board'][6] == 0 else 2
            self.change(u, self.decide(u))

    def update(self, u, state):
        if state == 1:
            decision = self.decide(u)
            if decision != 1:
                self.change(u, decision)
        elif state == 2:
            self.update_moving(u)
        elif state == 3:
            sleeping = 62 in u['board'] and self.rows[u['board'][62]]['type'] == 67
            self.tick_charging(u, sleeping)
        elif state == 5:
            self.tick_using_skill(u)
        else:
            raise AssertionError(f'harness generated an unsupported state {state}')

    def use_skill(self, u, command, index):
        self.emit(EV_USE, u['id'], command['skill'], index, 0)

    def execute_commands(self, u):
        animation = {'rate': u['animation_rate'], 'frame': u['animation_frame']}
        execute_skill_queue(u['commands'], lambda command: self.rows[command['skill']], animation,
            lambda behavior: self.animate(u, behavior),
            lambda command, index: self.use_skill(u, command, index))
        u['animation_rate'] = animation['rate']
        u['animation_frame'] = animation['frame']

    def run_step(self, battle_state):
        update_fighters(list(self.teams.values()), battle_state,
                        lambda fighter, prior: self.update(fighter, prior),
                        self.execute_commands, lambda fighter, prior: None)


def make_rows(rng):
    """A small recovered-shaped row table covering every branch the phase can take."""
    rows = {}
    next_id = 100
    for _ in range(14):
        kind = rng.choice([1, 2, 15, 26, 27, 60, 67])
        flags = 8
        if rng.random() < 0.7:
            flags |= 16
        if rng.random() < 0.3:
            flags |= 64
        if rng.random() < 0.15:
            flags |= 8192
        if rng.random() < 0.2:
            flags |= 0x40000
        rows[next_id] = dict(id=next_id, category=rng.choice([0, 0, 0, 0, 1, 2]), type=kind,
                             flags=flags, minMp=rng.choice([0, 4, 12]), maxMp=rng.choice([0, 30, 90]),
                             requiredEquipType=rng.choice([-1, 0, 3]),
                             shootingRange=rng.randint(1, 4), range=rng.randint(1, 3),
                             count=rng.choice([1, 1, 2, 3]), motion=rng.choice([16, 48, 72]),
                             value=rng.randint(0, 40))
        next_id += 1
    rows[next_id] = dict(id=next_id, category=0, type=67, flags=8, minMp=0, maxMp=0,
                         requiredEquipType=-1, shootingRange=1, range=1, count=1, motion=16, value=0)
    return rows


def make_units(rng, rows):
    count = rng.randint(3, 8)
    pool = list(rows.values())
    units = []
    for index in range(count):
        team = 0 if index < (count + 1) // 2 else 1
        human = rng.random() < 0.5
        state = rng.choice([1, 1, 2, 3, 3, 3, 5])
        board = {4: rng.randint(0, 30), 5: state, 6: team, 7: rng.randint(0, 24),
                 8: rng.randint(0, 150)}
        if rng.random() < 0.4:
            board[17] = rng.choice(pool)['id']
        if rng.random() < 0.2:
            sleeping = [row for row in pool if row['type'] == 67][0]
            board[62], board[63], board[64] = sleeping['id'], rng.randint(1, 3), 0
        long_board = {}
        if rng.random() < 0.3:
            long_board[16] = rng.choice([-1, 0, 1])
        cell = [rng.choice([0, 1, 2, 3, 4, 5, 6, 7]), rng.randint(0, 15)]
        if rng.random() < 0.25:
            cell[0] = rng.choice([-1, 5, 6, 8])
        skills = rng.sample(pool, rng.choice([0, 1, 1, 2, 2, 3]))
        levels = [rng.randint(0, 2) for _ in range(len(skills) + 1)]
        path = []
        if state == 2:
            path = [[rng.randint(0, 200), rng.randint(0, 400)] for _ in range(rng.randint(0, 2))]
        commands = []
        if rng.random() < 0.35:
            skill = rng.choice(pool)
            commands.append(dict(opcode=29, target=rng.choice([-1, 0, 1]), skill=skill['id'],
                                 tick=rng.randint(0, 25),
                                 duration=19 if skill['count'] == 1 else 19 + 5 * skill['count'],
                                 use_index=rng.randint(0, max(0, skill['count']))))
        units.append(dict(id=index, present=1, exists=1 if rng.random() < 0.9 else 0, team=team,
                          human=human, monster=not human, flags=0,
                          hp_value=rng.choice([0, rng.randint(1, 4000)]),
                          hp_max=rng.choice([0, rng.randint(1, 4000)]),
                          mp_value=rng.choice([0, rng.randint(20, 300), rng.randint(20, 300)]),
                          agility=rng.choice([0, rng.randint(1, 400)]),
                          cell=cell, direction=rng.randint(0, 3),
                          position=[float(rng.randint(0, 400)), 0.0, float(rng.randint(0, 400))],
                          offset=[0.0, 0.0, 0.0], velocity=[0.0, 0.0, 0.0],
                          animation_rate=1, animation_frame=0,
                          weapon_type=rng.choice([-1, 0, 3]),
                          weapon_shooting_range=rng.choice([1, 1, 3]),
                          weapon_motion=rng.choice([16, 48, 72]),
                          training=[rng.randint(1, 40) for _ in range(12)],
                          board=board, long_board=long_board,
                          skill_ids=[s['id'] for s in skills], levels=levels,
                          invoking=[], commands=commands, path=path))
    return units


def fill_board(board, mapping):
    for slot, key in enumerate(sorted(mapping)):
        board.entries[slot].key = key
        board.entries[slot].value = mapping[key]
    board.len = len(mapping)


def board_to_dict(board):
    return {board.entries[slot].key: board.entries[slot].value for slot in range(board.len)}


def to_buffers(units):
    buffer = (KaUnit * len(units))()
    for index, unit in enumerate(units):
        target = buffer[index]
        target.present, target.exists = unit['present'], unit['exists']
        target.team, target.human, target.monster = unit['team'], unit['human'], unit['monster']
        target.flags, target.id = unit['flags'], unit['id']
        target.hp_value, target.hp_max = unit['hp_value'], unit['hp_max']
        target.mp_value, target.agility = unit['mp_value'], unit['agility']
        target.cell_x, target.cell_y = unit['cell']
        target.direction = unit['direction']
        target.pos_x, target.pos_z = unit['position'][0], unit['position'][2]
        target.vel_x, target.vel_z = unit['velocity'][0], unit['velocity'][2]
        target.offset_z = unit['offset'][2]
        target.animation_rate, target.animation_frame = unit['animation_rate'], unit['animation_frame']
        target.weapon_type = unit['weapon_type']
        target.weapon_shooting_range = unit['weapon_shooting_range']
        target.weapon_motion = unit['weapon_motion']
        for slot, level in enumerate(unit['training']):
            target.training[slot] = level
        fill_board(target.board, unit['board'])
        fill_board(target.long_board, unit['long_board'])
        for slot, skill_id in enumerate(unit['skill_ids']):
            target.skill_ids[slot] = skill_id
        target.skill_count = len(unit['skill_ids'])
        for slot, level in enumerate(unit['levels']):
            target.levels[slot] = level
        target.level_count = len(unit['levels'])
        for slot, entry in enumerate(unit['invoking']):
            target.invoking[slot].skill, target.invoking[slot].remaining = entry
        target.invoking_count = len(unit['invoking'])
        for slot, command in enumerate(unit['commands']):
            target.commands[slot].opcode = command['opcode']
            target.commands[slot].target = command['target']
            target.commands[slot].skill = command['skill']
            target.commands[slot].tick = command['tick']
            target.commands[slot].duration = command['duration']
            target.commands[slot].use_index = command['use_index']
        target.command_count = len(unit['commands'])
        for slot, point in enumerate(unit['path']):
            target.path[slot].x, target.path[slot].y = point
        target.path_count = len(unit['path'])
    return buffer


def export_unit(unit):
    return dict(
        present=unit.present, exists=unit.exists, team=unit.team, human=unit.human,
        flags=unit.flags, id=unit.id, hp_value=unit.hp_value, mp_value=unit.mp_value,
        cell=(unit.cell_x, unit.cell_y), direction=unit.direction,
        position=(round(unit.pos_x, 5), round(unit.pos_z, 5)),
        velocity=(round(unit.vel_x, 5), round(unit.vel_z, 5)),
        offset_z=round(unit.offset_z, 5),
        animation_rate=unit.animation_rate, animation_frame=unit.animation_frame,
        board=board_to_dict(unit.board), long_board=board_to_dict(unit.long_board),
        commands=[(unit.commands[i].opcode, unit.commands[i].target, unit.commands[i].skill,
                   unit.commands[i].tick, unit.commands[i].duration, unit.commands[i].use_index)
                  for i in range(unit.command_count)],
        path=[(unit.path[i].x, unit.path[i].y) for i in range(unit.path_count)],
    )


def export_reference(reference, units):
    return dict(
        units=[dict(
            present=unit['present'], exists=unit['exists'], team=unit['team'],
            human=unit['human'], flags=unit['flags'], id=unit['id'],
            hp_value=unit['hp_value'], mp_value=unit['mp_value'],
            cell=tuple(unit['cell']), direction=unit['direction'],
            position=(round(unit['position'][0], 5), round(unit['position'][2], 5)),
            velocity=(round(unit['velocity'][0], 5), round(unit['velocity'][2], 5)),
            offset_z=round(unit['offset'][2], 5),
            animation_rate=unit['animation_rate'], animation_frame=unit['animation_frame'],
            board=dict(unit['board']), long_board=dict(unit['long_board']),
            commands=[(c['opcode'], c['target'], c['skill'], c['tick'], c['duration'],
                       c['use_index']) for c in unit['commands']],
            path=[tuple(p) for p in unit['path']],
        ) for unit in units],
        events=list(reference.events),
        math_draws=reference.math_draws,
        rng_index=reference.math_rng.index,
        rng_partner=reference.math_rng.partner,
        rng_values=reference.math_rng.values[1:56],
    )


class NativeBattle:
    """One native battle, imported once and stepped many times with no further crossings."""

    def __init__(self, library, units, rows, math_seed, map_width, row_offset, movement_enabled):
        self.library = library
        self.count = len(units)
        self.battle = library.ka_battle_create()
        if not self.battle:
            raise SystemExit('ka_battle_create returned null')
        buffer = to_buffers(units)
        library.ka_battle_import(self.battle, buffer, self.count, 0)
        library.ka_battle_config(self.battle, map_width, row_offset, movement_enabled)
        library.ka_battle_seed_rng(self.battle, math_seed, 0x1234)
        for row in rows.values():
            skill = KaSkill(row['id'], row['category'], row['type'], row['flags'], row['minMp'],
                            row['maxMp'], row['requiredEquipType'], row['shootingRange'],
                            row['range'], row['count'], row['motion'], row['value'])
            if library.ka_battle_add_row(self.battle, ctypes.byref(skill)) == 0:
                raise SystemExit('row table overflowed')

    def step(self, battle_state):
        return self.library.ka_update_fighters(self.battle, battle_state)

    def snapshot(self, status):
        library = self.library
        exported = (KaUnit * self.count)()
        count = library.ka_battle_export(self.battle, exported)
        event_total = library.ka_battle_event_count(self.battle)
        events = (KaEvent * max(1, event_total))()
        library.ka_battle_export_events(self.battle, events)
        rng = library.ka_battle_math_rng(self.battle)
        return dict(
            status=status,
            units=[export_unit(exported[index]) for index in range(count)],
            events=[(events[i].kind, events[i].unit, events[i].a, events[i].b, events[i].c)
                    for i in range(event_total)],
            math_draws=library.ka_battle_math_draws(self.battle),
            rng_index=library.ka_rng_index(rng),
            rng_partner=library.ka_rng_partner(rng),
            rng_values=[library.ka_rng_value(rng, index) for index in range(1, 56)],
        )

    def close(self):
        self.library.ka_battle_free(self.battle)
        self.battle = None


def compare(trial, expected, got):
    if got['status'] != 0:
        raise SystemExit(f'trial {trial}: native reported status {got["status"]} for a '
                         f'supported state')
    if expected['events'] != got['events']:
        for index in range(max(len(expected['events']), len(got['events']))):
            left = expected['events'][index] if index < len(expected['events']) else None
            right = got['events'][index] if index < len(got['events']) else None
            if left != right:
                raise SystemExit(f'trial {trial}: event {index} differs\n'
                                 f'  python={left}\n  rust  ={right}')
    for key in ('math_draws', 'rng_index', 'rng_partner', 'rng_values'):
        if expected[key] != got[key]:
            raise SystemExit(f'trial {trial}: {key} differs\n'
                             f'  python={expected[key]}\n  rust  ={got[key]}')
    for index, (want, have) in enumerate(zip(expected['units'], got['units'])):
        for field in want:
            if want[field] != have[field]:
                raise SystemExit(f'trial {trial}: unit {index} field {field} differs\n'
                                 f'  python={want[field]}\n  rust  ={have[field]}')


def coverage(got, totals):
    """Count how often each ported branch actually fired, so the corpus cannot pass vacuously."""
    def bump(key, amount=1):
        totals[key] = totals.get(key, 0) + amount

    if got['math_draws'] > 0:
        bump('trials_with_math_draws')
    bump('math_draws', got['math_draws'])
    for kind, _unit, a, _b, c in got['events']:
        bump('events')
        if kind == EV_STATE:
            bump('state_changes')
            bump('entered_state_%d' % _b)
        elif kind == EV_ENQUEUE:
            bump('enqueues')
        elif kind == EV_INVOCATION:
            bump('invocations')
            bump('invocations_passed', c)
        elif kind == EV_ANIMATION:
            bump('animations')
            bump('animation_behaviour_%d' % a)
        elif kind == EV_USE:
            bump('command_uses')


def main():
    library = load_library()
    rng = random.Random(20260922)
    trials = int(sys.argv[1]) if len(sys.argv) > 1 else 1500
    steps = int(sys.argv[2]) if len(sys.argv) > 2 else 1

    # `attack_interval` is the one recovered routine whose result depends on the host `pow`. Sweep it
    # directly so a libm disagreement is reported as itself rather than as a mystery state mismatch.
    for agility in list(range(0, 1000)) + [1234, 2000, 7777, 20000, 99999, 100000, -5]:
        expected = attack_interval(agility)
        got = library.ka_attack_interval(agility)
        if expected != got:
            raise SystemExit(f'attack_interval({agility}) differs: python={expected} rust={got}')
    print('attack_interval parity: 1006 agility values matched (host pow agreement confirmed)')

    totals = {}
    started = time.perf_counter()
    for trial in range(trials):
        rows = make_rows(rng)
        units = make_units(rng, rows)
        math_seed = rng.randint(-2**31, 2**31 - 1)
        map_width = rng.choice([8, 8, 5])
        row_offset = rng.choice([3, 4, 5])
        movement_enabled = rng.choice([1, 1, 0])
        battle_state = rng.choice([2, 3])

        reference = Reference(units, rows, math_seed, map_width, row_offset, movement_enabled)
        native = NativeBattle(library, units, rows, math_seed, map_width, row_offset,
                              movement_enabled)
        try:
            for step in range(steps):
                status = native.step(battle_state)
                if status != 0:
                    # The phase reached a state whose handler is a later slice. Stop here rather
                    # than compare a partial step; the run is reported as truncated.
                    totals['trials_stopped_unsupported'] = \
                        totals.get('trials_stopped_unsupported', 0) + 1
                    break
                got = native.snapshot(status)
                # The kernel returns the events of one phase call, so compare like for like: the
                # reference log is reset per step instead of accumulating over the battle.
                reference.events = []
                reference.run_step(battle_state)
                compare(f'{trial}.{step}', export_reference(reference, units), got)
                coverage(got, totals)
                if step == 0:
                    totals['trials'] = totals.get('trials', 0) + 1
                totals['steps_compared'] = totals.get('steps_compared', 0) + 1
        finally:
            native.close()
    elapsed = time.perf_counter() - started
    print(f'fighter-step differential parity: {trials} randomized battles, '
          f'up to {steps} step(s) each, matched exactly')
    print('  compared: board, long_board, direction, position, velocity, offset, animation rate/frame,')
    print('            command queue, path, ordered event log, math draws and the full RNG table')
    print('  coverage:')
    for key in sorted(totals):
        print(f'    {key}: {totals[key]}')
    print(f'  wall time: {elapsed:.2f}s ({elapsed/trials*1000:.2f}ms per battle)')
    print(f'  runtime: {sys.implementation.name} {sys.version.split()[0]}')


if __name__ == '__main__':
    main()
