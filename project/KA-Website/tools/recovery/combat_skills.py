"""Recovered skill eligibility, separate from candidate target selection."""
from combat_initial_state import i32


def update_status(board, has_ai=True):
    """SkillSystem status tick; runs after fighters and before projectiles.

    BB64 is an accumulating counter, not reset after each duration decrement.
    This function has no HP or fighter-state gate.
    """
    if not has_ai or not all(key in board for key in (62, 63, 64)):
        return
    board[64] = i32(board[64] + 1)
    if board[64] % 20 == 0:
        board[63] = i32(board[63] - 1)
        if board[63] <= 0:
            for key in (62, 63, 64):
                board.pop(key, None)


def can_receive_status(*, has_parameter, has_hp, map_chip, stored_hp,
                       has_status, monster, boss, main_world, monster_type,
                       hostile_flag, caster_ally, target_ally):
    """CanBuff for a non-null target and non-null skill, with required AI.

    Native null target/missing AI can throw; they are not silent false cases.
    Boss immunity here applies only in the main world, not special battle worlds.
    """
    if not has_parameter or not has_hp or map_chip or stored_hp <= 0 or has_status:
        return False
    if monster and ((boss and main_world) or monster_type == 1):
        return False
    return not hostile_flag or caster_ally != target_ally


def apply_status(skill, board, accuracy, hits, turns, show_text):
    """Buff after target filtering; turns queried only after a successful roll."""
    if hits(accuracy()):
        board[62] = skill['id']
        board[63] = turns()
        board[64] = 0
        if skill['type'] in (66, 67):
            show_text('defense_down' if skill['type'] == 66 else 'sleep')
    else:
        show_text('miss')


def buff_entities_on_cell(skill, candidates, can_receive, accuracy, hits, turns, show_text):
    """`BuffEntitiesOnCell` 0x168ee44 + the `Buff` writer for the flags&0x40000 route.

    Native order: filter the cell's entities through `CanBuff` (lambda 0x168f9c4) into a
    materialized array, then run the writer per array entry in array order. The writer always
    performs one Hits call, including on a miss (the failed draw is consumed and the board is left
    untouched); a success overwrites the single BB62/63/64 status slot rather than stacking.
    Returns the targets that were actually written, in order.
    """
    applied = []
    for target in candidates:
        if not can_receive(target):
            continue
        board = target['board']
        apply_status(skill, board, lambda t=target: accuracy(t), lambda rate, t=target: hits(t, rate),
                     lambda t=target: turns(t), lambda kind, t=target: show_text(t, kind))
        if board.get(62) == skill['id']:
            applied.append(target)
    return applied


def play_skill_sound(skill, sound_rate, lib_hits, play):
    """PlaySkillSe performs its Lib RNG check even if playback is suppressed."""
    if not lib_hits(sound_rate):
        return
    if skill['category'] == 1:
        sound = 16
    elif skill['type'] == 1:
        sound = 28
    elif skill['flags'] & 32:
        sound = 30
    else:
        sound = 28 if skill['flags'] & 0x40000 else 27
    play(sound)


def can_use_skill(skill, *, check_mp, mp, cost, target_exists,
                  weapon_type, target_hp, target_hp_rate, invoking,
                  battle_world, most_front, opponent_in_range):
    if skill is None or skill['category']==2:return False
    if check_mp and mp<cost:return False
    if skill['flags']&16 and not target_exists:return False
    required=skill['requiredEquipType']
    if required!=-1 and weapon_type!=required:return False
    if skill['category']==1:
        if skill['type']==2 and not 1<=target_hp_rate<=99:return False
        if skill['type']==15 and target_hp>0:return False
    if invoking:return False
    if battle_world and (skill['type'] in (26,27) or skill['flags']&0x60000):
        return most_front and opponent_in_range
    return True
