"""Research reference functions; inputs must be captured at the documented phase.
Not a complete combat simulator. See docs/reverse-engineering/special-combat.md.
"""
import json
from pathlib import Path

from combat_runtime_data import EVIDENCE_DIR, load_data

# Kept for the recovery/check tooling; the runner itself reads through combat_runtime_data so the
# packaged runtime never needs the workspace layout.
EVIDENCE = EVIDENCE_DIR
PRIORITIES = load_data('formation-rules.json')['priorities']
PARAM_IDS = [10, 11, 13, 14, 15, 16, 19]
# InitFighters overwrites exactly these AI blackboard keys on every placed fighter
# (BB4=0, BB5=Waiting1, BB6=team, BB7=grid, BB8=0). Any source value under them is
# discarded, so they are not a source-controlled input. All other keys, longBoard,
# the position offset window and the SkillComponent are left untouched.
NATIVE_INIT_OVERWRITTEN_BOARD_KEYS = (4, 5, 6, 7, 8)


def i32(value):
    # Exactly `(value + 2**31) % 2**32 - 2**31` for every Python int: for a power-of-two modulus
    # `x % 2**32 == x & 0xFFFFFFFF` holds for negative and positive x alike, so this is one bitmask
    # instead of two arbitrary-precision modulo/division steps.
    return ((value + 0x80000000) & 0xFFFFFFFF) - 0x80000000


def init_position(cell):
    """Position written by InitFighters from the placed cell (x*24, 0, z*24) as i32."""
    return [float(i32(cell[0] * 24)), 0.0, float(i32(cell[1] * 24))]


def trunc_div(value, divisor):
    return (abs(value) // divisor) * (-1 if value < 0 else 1)


def monster_parameter(curve, level):
    """MonsterData.GetParameter, raw creation value, before later modifiers."""
    a, b, c, d = curve
    if level < 1:
        return a
    if level > 10000:
        return d
    if level <= 100:
        low, high, step, span = a, b, level - 1, 99
    elif level <= 1000:
        low, high, step, span = b, c, level - 100, 900
    else:
        low, high, step, span = c, d, level - 1000, 9000
    return i32(low + trunc_div(i32(i32(high - low) * step), span))


def priority(formation_value=None, *, visitor=False, leader_identity=False,
             monster=False, owner_player=False):
    """formation_value is the FIRST possessed type60 skill's value, or None.
    leader_identity means the SAME entity object as TeamMember.Leader.
    OwnerPlayer is a native component; it does not mean every local ally.
    """
    category = 2 if formation_value is None else formation_value
    if visitor:
        category = 3
    if leader_identity:
        category = 5
    return PRIORITIES[category] + int(monster) + 2 * int(owner_player)


def formation(members, team_id, opponent_count):
    """Requires ordered members with effectiveDefense at InitFighters.
    No attempt to infer effectiveDefense from training levels or equipment.
    Returns placement order, NOT the stored roster's later update order.
    incomingIndex preserves that separate order.
    """
    assert team_id in (0, 1) and opponent_count >= 0
    offset = max(3, opponent_count // 5 + 1)
    rows = []
    for index, m in enumerate(members):
        p = priority(m.get('formationValue'), visitor=m.get('visitor', False),
                     leader_identity=m.get('leaderIdentity', False),
                     monster=m.get('monster', False), owner_player=m.get('ownerPlayer', False))
        rows.append(dict(m, incomingIndex=index, priority=p))
    rows.sort(key=lambda m: (m['priority'], -m['effectiveDefense'], m['incomingIndex']))
    for index, m in enumerate(rows):
        column, row = index % 5, index // 5
        m.update(grid=index, row=row, column=column,
                 cell=[column, offset + 1 + row if team_id == 0 else offset - row])
    return rows


def initialize_fighter_teams(teams, placements, decide, change_state,
                           cell_changed, remove_invisible, clear_commands,
                           update_human_images):
    """InitFighters mutation order, with explicit synchronous dependencies.

    Teams contain original entity dictionaries. Each placement list contains
    formation() rows referring to that team's incomingIndex. The caller must
    supply real state-entry, occupancy, component and image callbacks; these
    can have gameplay side effects. This does not replace the roster arrays.
    Unrelated cloned fields, including position offsets, remain intact.
    """
    import struct
    def native_float(value):
        return struct.unpack('<f', struct.pack('<f', value))[0]
    for team_id, (roster, ordered) in enumerate(zip(teams, placements, strict=True)):
        for placement in ordered:
            unit = roster[placement['incomingIndex']]
            old_cell = tuple(unit['cell'])
            unit['cell'][:] = placement['cell']
            unit['parent'] = None
            x, z = unit['cell']
            unit['position'][:] = [native_float(i32(x * 24)), 0.,
                                   native_float(i32(z * 24))]
            unit['speed'][:] = [0., 0., 0.]
            unit['direction'] = 0 if team_id == 0 else 2
            unit['board'].update({4: 0, 6: team_id, 7: placement['grid'], 8: 0, 5: 1})
            if unit['invisible']:
                remove_invisible(unit)
            if unit['human']:
                update_human_images(unit)
            clear_commands(unit)
            cell_changed(unit, old_cell)
        for placement in ordered:
            unit = roster[placement['incomingIndex']]
            change_state(unit, decide(unit))


def weaken_friend_parameter(raw_value, raw_max, extra_value=0, extra_max=0):
    """One present group34 Parameter in FriendSystem.WeakenIllegalParamFriend."""
    effective_max = extra_max if raw_max == 0x7fffffff else i32(raw_max+extra_max)
    if raw_max != 0x7fffffff:
        raw_max = max(1,trunc_div(effective_max,1000))
    value = max(1,trunc_div(i32(raw_value+extra_value),1000))
    effective_max = extra_max if raw_max == 0x7fffffff else i32(raw_max+extra_max)
    if effective_max >= 1: value = min(max(value,0),effective_max)
    return value,raw_max,extra_value,extra_max


def effective_parameter_rate(value, maximum, eligible=True):
    """Param.GetRate after its effective-value/max getters; eligibility requires a bounded parameter."""
    if not eligible or maximum == 0: return 0
    product=i32(value*100)
    quotient=(abs(product)//abs(maximum))*(-1 if (product<0)!=(maximum<0) else 1)
    rate=min(100,max(0,quotient))
    return int(value>0) if rate==0 else rate


# --- Native pre-placement producers -----------------------------------------------------
# Every enemy entity in a special battle comes from the one factory World.CreateMonster
# 0x147780c, reached through World.CreateEnemyMonster 0x1477c5c:
#   SubForm.UpdateGuerrillaDungeon 0x16f4f14 (boss) creates it at the SpecialBoss entity's own
#       world position (get_position 0x1471498 reads 0x16f52fc..0x16f5340) with direction1;
#   BattleForm.CreateBossBattle 0x16a8fa0 -> follower projection 0x16ac77c creates followers at
#       x=y=z=0 (fmov s0/s1/s2, wzr at 0x16ac79c..0x16ac7a4) with direction0.
# The factory writes the complete source component set: AddPosition 0x14779f0 (x,y,z,0,0,0),
# AddCell 0x146cb3c, AddDepth 0x146d370, AddImage 0x146f2a4, AddSeb 0x1472260, AddSpeed
# 0x14728f0, AddAnimation 0x146ba70 (1,-1), AddDirection 0x146d48c, AddMonster 0x1470c94,
# BlackboardInt..ctor 0x1465378, AddAI 0x146b520, Param.Create 0x16602c8, AddParameter 0x1471298
# and the MonsterData skill list. Cell is derived from the float x/z: `fcvtzs` to int, then the
# 0x2AAAAAAB magic (/24), high>>2 plus sign (0x147795c..0x14779b4), i.e. truncation toward zero,
# the exact inverse of init_position. The AI board it installs has exactly two entries:
# Dictionary Add(key=0x13=19, value=cellX) 0x1477af4 and Add(key=0x14=20, value=cellY)
# 0x1477b08. Nothing in the factory writes BKI4/5/6/7/8; those are InitFighters outputs.
MONSTER_SOURCE_BOARD_KEYS = (19, 20)
START_PROFILE_KINDS = ('isolated-scene0',)


def div24(value):
    """Cell coordinate CreateMonster 0x147780c computes from a world coordinate.

    Exact instruction sequence at 0x1477960..0x14779b4: fcvtzs (truncate the float), the
    0x2AAAAAAB signed multiply, then `sign + (high >> 2)`. Equal to truncation toward zero.
    """
    return trunc_div(int(value), 24)


def monster_source_cell(x, z):
    """(Cell.x, Cell.y) a freshly created monster entity carries before InitFighters."""
    return [div24(x), div24(z)]


def expand_start_profile(profile, roster):
    """Derive every fighter's pre-placement state from a declared start profile.

    A profile is a declared simulation condition, never captured save data. Only fields the
    supported battle can observe are derived:
      * enemy Cell (and the factory board keys 19/20 = that cell) from the recovered
        CreateMonster producer;
      * declared starting status 62/63/64.
    Everything else is the InitFighters-overwritten / unread placeholder documented in
    support_contract().sourceState, not a claim about the source entity.
    """
    kind = profile.get('kind')
    if kind not in START_PROFILE_KINDS:
        raise ValueError(f'Unknown start profile kind {kind!r}')
    spawn = [int(v) for v in profile.get('enemySpawnCell', (0, 0))]
    boss = None if profile.get('bossCell') is None else [int(v) for v in profile['bossCell']]
    status = profile.get('startingStatus', {})
    known = {spec['name'] for spec in roster}
    unknown = sorted(name for name in status if name not in known)
    if unknown:
        raise ValueError('startingStatus names an unknown fighter: ' + ', '.join(unknown))
    derived = {}
    for spec in roster:
        enemy = spec['team'] == 1
        # Own members are world-entity clones; their source cell is placed before any decision
        # reads it, so the placeholder is not a claim about the world position.
        cell = list(boss if (enemy and boss is not None and spec.get('boss')) else
                    (spawn if enemy else [0, 0]))
        # Keys 4/5/6/7/8 are InitFighters outputs; a not-yet-initialized fighter carries the
        # declared board-contract values instead (5:0 keeps it out of IsAttackableTarget
        # 0x1588554, which reads BKI:5 through Dictionary.get_Item).
        board = {4: 0, 5: 0, 6: spec['team'], 7: 0, 8: 0}
        if enemy:
            board[19], board[20] = cell
        entry = status.get(spec['name'])
        if entry is not None:
            board[62], board[63], board[64] = int(entry[0]), int(entry[1]), 0
        derived[spec['name']] = dict(cell=cell,
            position=[float(cell[0] * 24), 0.0, float(cell[1] * 24)],
            offset=[0.0, 0.0, 0.0], board=board, longBoard={})
    return derived
