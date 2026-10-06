"""Active SkillInfo filtering before category-specific target construction."""


def active_skill_infos(owned_rows, invocation_levels, mp, cost):
    # The native Where precedes indexed Select. This ordinal is intentionally
    # not the original equipped slot; do not repair it to match a UI assumption.
    filtered=[s for s in owned_rows if s['flags']&8 and not s['flags']&32 and mp>=cost(s)]
    return [(s,invocation_levels[index]) for index,s in enumerate(filtered)]


def first_invoked_target(candidates, invokes):
    """Candidates already ordered resurrection, recovery, then attack."""
    for candidate in candidates:
        if invokes(candidate):return candidate
    return None


def attack_skill_candidates(infos, target_for_skill, can_use):
    """Attack-category branch; caller handles recovery/resurrection first.

    Preserve filtered SkillInfo settings and order. No invocation roll belongs
    here: DecideNextState also queries candidate existence without rolling.
    """
    for skill,level in infos:
        if skill['category']!=0 or skill['flags']&64:
            continue
        target=target_for_skill(skill) if skill['flags']&16 else None
        if can_use(skill,target):
            yield skill,level,target
