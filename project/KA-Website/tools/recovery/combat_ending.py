"""Recovered verdict boundary, separate from input-driven battle teardown."""

from combat_initial_state import i32, trunc_div


def advance_battle_frame(frame):
    """Original BattleSystem.Update signed increment modulo INT_MAX."""
    value=i32(frame+1)
    return value-trunc_div(value,2147483647)*2147483647


def update_ending(frame,key_pulse,has_conquest,winner):
    """Return counter and requested transition; caller owns entry/Finish effects."""
    if not key_pulse:return frame,None
    if frame<=79:return 80,None
    return frame,'getting_exp' if has_conquest and winner==1 else 'finish'


# The recovered Ending gate is counter-gated, not tick-gated: ONE pulse with frame>79 requests the
# Finish/EXP transition, so a single press finishes a fight whose counter is already past 79. Two
# presses are needed only when the first lands at frame<=79 - it writes the counter to 80 without a
# transition, and the counter then keeps advancing, so the very next pulse (frame>79) finishes. There
# is no "window opens on the 80th post-verdict tick and no earlier" rule: an early pulse itself raises
# the counter, so the Finish becomes reachable before that tick. `frame` is the counter reset by
# EnterEnding; this is the recovered gate, not a tick guess.
ENDING_CONFIRM_FRAME = 80


def after_ending_confirmation(frame,winner,has_conquest=False):
    """One declared player Ending confirmation at the current counter.

    Reuses ``update_ending`` and returns its counter/transition exactly: ``(counter,transition)``.
    A press with ``frame<=79`` yields ``(80,None)`` - the Ending stays open, so the caller must
    report awaiting-confirmation and dispatch no chests. A press with ``frame>79`` requests the
    native transition (``(frame,'finish')``, or ``'getting_exp'`` on a conquest own-team win). The
    counter is returned, never a separate gating result, so the authoritative timeline is the
    original gate.
    """
    return update_ending(frame,True,has_conquest,winner)


def experience_prizes(prizes):
    """EnterGettingExp selects only type1; empty selection immediately finishes."""
    return [p for p in prizes if p['type']==1]


def is_annihilated(roster):
    """BattleSystem.IsAnnihilated: Leaving state, not zero HP."""
    return all(unit['board'][5] == 8 for unit in roster)


def enter_ending(teams, change_state):
    """OnEnd followed by own-team verdict. Does not clear retained commands.

    State exit/entry callbacks remain mandatory; this is not a bare BB5 write.
    Presentation, source-world notifications and teardown are separate.
    """
    for team in teams:
        for unit in team:
            if unit['board'][5] not in (7,8):
                change_state(unit,0)
    return 2 if is_annihilated(teams[0]) else 1
