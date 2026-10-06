"""Recovered formation navigation predicates; map occupancy remains explicit."""
from combat_initial_state import i32,trunc_div
from combat_states import is_most_front,is_normal_attack_target


def same_grid(unit,team):
    grid=unit['board'][7]
    for other in team:
        if other is not unit and other['board'][5] not in (2,7,8) and other['board'][7]==grid:
            return True
    return False


def empty_cell(occupants,has_ai,state):
    return occupants is not None and not any(has_ai(i) and state(i) not in (7,8) for i in occupants)


def queue_position(unit,team,hp,row_offset):
    # The original rescanned the whole team once per column (5 x team hp reads). hp is a pure read,
    # so one pass with the same predicate is identical; a unit whose computed column falls outside
    # 0..4 contributes to no bucket, exactly as the per-column comparison did.
    counts=[0,0,0,0,0]
    for other in team:
        if hp(other)>0:
            grid=other['board'][7]
            column=i32(grid-trunc_div(grid,5)*5)
            if 0<=column<5:
                counts[column]+=1
    column=min(range(5),key=lambda c:counts[c])
    row=counts[column]+1
    y=row_offset+1+row if unit['board'][6]==0 else row_offset-row
    return column*24,y*24


def front_opponent(opponents,exists,hp,state,grid,distance):
    eligible=[i for i in opponents if is_normal_attack_target(exists(i),hp(i),state(i)) and is_most_front(grid(i))]
    return min(eligible,key=distance) if eligible else None
