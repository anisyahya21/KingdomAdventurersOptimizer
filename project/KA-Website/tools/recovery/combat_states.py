"""Recovered fighter decisions; callers supply separately recovered predicates."""
from combat_initial_state import i32


def change_fighter_state(unit, next_state, exit_handlers, enter_handlers):
    """Synchronous ChangeState, including same-state exit and re-entry."""
    board = unit['board']
    exit_handlers[board[5]](unit)
    board[5] = next_state
    board[4] = 0
    enter_handlers[board[5]](unit)


def exit_using_skill(unit, change_animation):
    unit['board'][8] = 0
    unit['board'][17] = -1
    unit['long_board'][16] = -1
    change_animation(unit, 3)


def enter_damaging(unit, change_animation):
    from combat_resolution import native_float_to_int
    unit['board'][12] = native_float_to_int(unit['position'][2])
    change_animation(unit, 30)


def update_damaging(unit, get_hp, has_human, has_monster, has_enemy,
                    is_pvp, decide, on_monster_defeated, change_state):
    from combat_projectiles import parabola
    from combat_resolution import f32
    board = unit['board']
    frame = board[4]
    height = 10 if board[6] == 0 else -10
    unit['offset'][2] = f32(i32(board[12] + parabola(height, 6, frame)))
    if frame >= 7:
        if get_hp(unit) > 0:
            change_state(unit, decide(unit))
        elif has_human(unit):
            change_state(unit, 7)
        elif has_monster(unit):
            if has_enemy(unit) and not is_pvp():
                on_monster_defeated(unit)
            change_state(unit, 8)


def update_knocking_down(unit, change_animation, change_state):
    from combat_initial_state import trunc_div
    frame = unit['board'][4]
    if frame <= 20:
        value = i32(unit['direction'] + 1)
        unit['direction'] = i32(value - trunc_div(value, 4)*4)
    elif frame == 21:
        change_animation(unit, 7)
    elif frame >= 101:
        change_state(unit, 8)


def update_leaving(unit):
    from combat_initial_state import trunc_div
    value = i32(unit['direction'] + 2)
    unit['direction'] = i32(value - trunc_div(value, 4)*4)


def cell_to_grid(team, row_offset, cell):
    from combat_initial_state import trunc_div
    row = i32(cell[1] - row_offset - (1 if team == 0 else 0))
    row = trunc_div(row, 1 if team == 0 else -1)
    return i32(i32(row*5) + cell[0])


def exit_damaging(unit, row_offset, steps=(24, 24)):
    from combat_initial_state import trunc_div
    from combat_resolution import f32
    grid, team = unit['board'][7], unit['board'][6]
    row = trunc_div(grid, 5)
    column = i32(grid - row*5)
    y = i32(row_offset + (1 if team == 0 else 0) + (row if team == 0 else -row))
    unit['position'][0] = f32(i32(column*steps[0]))
    unit['position'][2] = f32(i32(y*steps[1]))
    unit['offset'][2] = 0.


def enter_moving(unit, change_animation):
    from combat_geometry import fighter_path
    from combat_resolution import f32
    unit['path'] = fighter_path((unit['position'][0], unit['position'][2]),
                               (f32(unit['board'][13]), f32(unit['board'][14])))
    unit['animation_rate'] = 2
    change_animation(unit, 2)


def move_fighter(unit, target, speed):
    from combat_geometry import battle_move_base
    arrived, position, velocity = battle_move_base(
        (unit['position'][0], unit['position'][2]), target, speed,
        (unit['velocity'][0], unit['velocity'][2]))
    unit['position'][0], unit['position'][2] = position
    unit['velocity'][0], unit['velocity'][2] = velocity
    if arrived:
        unit['animation_rate'] = 1
    return arrived


def update_moving(unit, move, get_row_offset, decide, change_state):
    from combat_resolution import f32
    if unit['path']:
        if move(unit, unit['path'][0], f32(4.46)):
            unit['velocity'][0] = 0.
            unit['velocity'][2] = 0.
            unit['board'][7] = cell_to_grid(unit['board'][6], get_row_offset(), unit['cell'])
            unit['path'].pop(0)
    if not unit['path']:
        unit['direction'] = 0 if unit['board'][6] == 0 else 2
        change_state(unit, decide(unit))


def exit_moving(unit, get_row_offset):
    unit['velocity'][0] = 0.
    unit['velocity'][2] = 0.
    unit['animation_rate'] = 1
    if unit['path'] is not None:
        unit['path'].clear()
    unit['board'][7] = cell_to_grid(unit['board'][6], get_row_offset(), unit['cell'])


def enter_knocking_down(unit, teammates, row_offset, clear_commands,
                       fire_projectile, change_animation, smoke, sound_roll,
                       steps=(24, 24)):
    from combat_resolution import f32
    count = sum(other is not unit and other['board'][5] == 7 for other in teammates)
    back = 1 if unit['board'][6] == 0 else -1
    start = tuple(unit['position'])
    end = (f32(i32(-2*steps[0])), 0., f32(i32(i32(back*i32(row_offset+count))*steps[1])))
    unit['board'][7] = -1
    clear_commands(unit)
    fire_projectile(unit, 5, start, end)
    change_animation(unit, 28)
    smoke(unit)
    sound_roll(18)


def enter_leaving_special(unit, row_offset, has_monster, is_rival_leader,
                          fire_projectile, smoke, add_special_prize, sound_roll,
                          steps=(24, 24)):
    """Non-PvP Wairo/Kairo (isGuerrilla) branch; ordinary EXP/egg drops separate."""
    from combat_initial_state import trunc_div
    from combat_resolution import f32
    grid = i32(unit['board'][7] + 100)
    row = trunc_div(grid, 5)
    team = unit['board'][6]
    start = tuple(unit['position'])
    end = (f32(i32(i32(grid-row*5)*steps[0])), 0.,
           f32(i32(i32(row_offset+(1 if team == 0 else 0)+(row if team == 0 else -row))*steps[1])))
    unit['board'][7] = grid
    fire_projectile(unit, 10, start, end)
    smoke(unit)
    if team == 1 and has_monster(unit) and is_rival_leader(unit):
        add_special_prize()
    sound_roll(18)


def is_most_front(grid):
    return (i32(grid+4) & 0xffffffff) < 9


def is_normal_attack_target(exists, hp, state):
    return bool(exists and hp > 0 and state in (1, 3, 4, 6))


def decide_next_state(state, team, cell, most_front, long_range, skill_candidate,
                      same_grid=False, advanceable=False, queue_position=None,
                      steps=(24, 24)):
    """Return (next state, optional BB13/14 destination).

    Does not roll skill invocation or change the fighter state itself.
    queue_position models the nullable GetQueueingPosition result.
    """
    if state not in (2, 7, 8):
        if not 0 <= cell[0] < 5 and queue_position is not None:
            return 2, tuple(i32(int(v)) for v in queue_position)
        if same_grid or advanceable:
            backward = 1 if team == 0 else -1
            direction = backward if same_grid else -backward
            return 2, (i32(cell[0]*steps[0]), i32(i32(cell[1]+direction)*steps[1]))
    return (3 if most_front or long_range or skill_candidate else 1), None


def update_charging(gauge, sleeping, interval, choose_skill, long_range,
                    nearest_target, most_front, front_target, decide):
    """Ordered charging phase; callbacks retain eligibility/RNG call order.

    Returns updated gauge, optional state transition, and command/target writes.
    Skill result is (skill_id, target_id); None means no invocation succeeded.
    """
    if not sleeping:
        gauge=i32(gauge+1)
        if gauge <= interval: return gauge, None, {}
        skill=choose_skill()
        if skill is not None:
            return gauge, 5, {'skill':skill[0], 'command_target':skill[1]}
        target=nearest_target() if long_range() else None
        if target is None and most_front(): target=front_target()
        if target is not None: return gauge, 4, {'target':target}
        writes={'target':-1}
    else:
        writes={}
    next_state=decide()
    return gauge, (next_state if next_state != 3 else None), writes


def update_attacking(frame, gauge, get_target, projectile_weapon, weapon_type,
                     direct_attack, fire_projectile, sound_roll, decide):
    """Normal release timing. Target selection is not repeated at release."""
    if frame == 11:
        target=get_target()
        if target is None: return gauge, None
        if not projectile_weapon:
            direct_attack(target)
        elif weapon_type in (7,8):
            fire_projectile(target,1 if weapon_type==8 else 2)
        sound_roll()
        return 0, None
    return gauge, (decide() if frame >= 20 else None)


def update_waiting(decide):
    next_state = decide()
    return next_state if next_state != 1 else None


def update_using_skill(gauge, commands, decide):
    # Any skill command anywhere in the queue holds this state, not just its head.
    if any(command['opcode'] == 29 for command in commands):
        return gauge, None
    return 0, decide()


def tick_using_skill(unit, decide, change_state):
    """In-place variant: decisions must see the cleared gauge immediately."""
    if any(command['opcode'] == 29 for command in unit['commands']):
        return
    unit['board'][8] = 0
    change_state(unit, decide(unit))


def tick_charging(unit, sleeping, effects):
    """In-place UpdateCharging preserving writes visible inside callbacks."""
    if not sleeping:
        interval = effects['interval'](unit)
        board = unit['board']
        board[8] = i32(board[8]+1)
        if board[8] <= interval:
            return
        selected = effects['choose_skill'](unit)
        if selected is not None:
            skill, target = selected
            effects['add_command'](unit, skill, target)
            unit['board'][17] = skill
            effects['change_state'](unit, 5)
            return
        target = effects['nearest_target'](unit) if effects['long_range'](unit) else None
        if target is None and effects['most_front'](unit):
            target = effects['front_target'](unit)
        unit['long_board'][16] = -1 if target is None else target
        if target is not None:
            effects['change_state'](unit, 4)
            return
    decision = effects['decide'](unit)
    if decision != 3:
        effects['change_state'](unit, decision)


def enter_using_skill(unit, skill_by_id, change_animation, decide, change_state):
    skill_id = unit['board'].get(17, -1)
    if skill_id != -1:
        change_animation(unit, skill_by_id(skill_id)['motion'])
    else:
        change_state(unit, decide(unit))
