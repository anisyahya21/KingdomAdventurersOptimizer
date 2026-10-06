"""Recovered ordered fighter phase, for composing checked combat primitives.

This is not a complete world/fight simulator. Callers own the explicit state
handlers, AI command execution and optional post-update presentation work.
"""
from combat_initial_state import i32


def update_fighters(teams, battle_state, update_state, execute_commands,
                    after_fighter=lambda fighter, prior_state: None):
    if battle_state is not None and (battle_state & 0xffffffff) < 2:
        return
    for team in teams:
        for fighter in team:
            if fighter is None or (fighter['flags'] & 2):
                continue
            fighter['board'][4]=i32(fighter['board'][4]+1)
            prior_state=fighter['board'][5]
            update_state(fighter,prior_state)
            execute_commands(fighter)
            if prior_state not in (7,8):
                after_fighter(fighter,prior_state)
