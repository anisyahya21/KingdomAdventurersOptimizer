"""The staged evidence policy: novelty keeps discovery, only real evidence earns more.

`staged_limits` is the one function that decides how many seeds a candidate's evidence deserves, so
this check pins every branch that does not need a live pool:

  * a candidate that shows nothing stops after its discovery bank (the novel-but-fruitless case);
  * a converted win, a lane place, a new strategy region, concrete causal progress or being the
    reference baseline each earn the extended discovery bank and then a validation bank - unchanged;
  * a measured loss opportunity at or above `POTENTIAL_FOLLOW_UP_MIN_CHESTS` chests now earns that
    same follow-up on its own, even for a build that is not a new region, holds no lane place, has
    never won and has no causal progress. That promotion used to depend on the potential lane's
    four-member pool, so a fight with more than four opportunity-bearing builds could leave the rest
    at its discovery bank even when its defeats had queued real chests;
  * one chest below the floor still stops after discovery, so the promotion never fires on
    single-digit noise;
  * the authorisation stays bounded: one extended discovery bank and one validation bank, never an
    open-ended resimulation, so a high-potential build cannot outgrow the staged policy;
  * probes keep their own shared bank and the legacy reproduction never sees the new reason.

    python check_optimizer_staged_budget.py
"""
from strategy_optimizer import (MAX_SAMPLES, POTENTIAL_FOLLOW_UP_MIN_CHESTS, VALIDATION_RUNS,
                                learner, staged_limits)


def budget(nd=24, wins=0, potential=0, progress=None, *, root=True, probe=False):
    """The novelty/baseline path: a new strategy region, never a followed-up mutation."""
    return staged_limits(nd, wins, False, True, 0, None, potential, progress or {},
                         probe, 160, root)


def follow_up(nd=8, potential=0, *, rank=None, win=False, earned=None, progress=None, root=True,
              probe=False, mode='branching'):
    """A mutation with no lane place, no new region and no lineage bonus: only its own evidence."""
    return staged_limits(nd, 1 if win else 0, None, False, rank, earned, potential,
                         progress or {}, probe, 160, root, mode)


# --- the branches that already existed -------------------------------------------------------
assert budget(nd=8) == (8, 0)
assert budget() == (24, 0)
assert budget(wins=1) == (24, 64)
assert budget(potential=1) == (24, 64)
assert budget(progress={'storedCommandsTargetingBossAtDeath': 1}) == (24, 64)
assert budget(root=False) == (24, 64)
assert budget(probe=True) == (24, 160)

# --- a measured high-potential defeat earns its own follow-up --------------------------------
assert POTENTIAL_FOLLOW_UP_MIN_CHESTS == 10
assert follow_up(nd=8, potential=POTENTIAL_FOLLOW_UP_MIN_CHESTS) == (24, 0)
assert follow_up(nd=24, potential=POTENTIAL_FOLLOW_UP_MIN_CHESTS) == (24, VALIDATION_RUNS)
assert follow_up(nd=8, potential=POTENTIAL_FOLLOW_UP_MIN_CHESTS+1) == (24, 0)
assert follow_up(nd=8, potential=119) == (24, 0)

# One chest below the floor is still just discovery: the promotion never fires on single digits.
assert follow_up(nd=8, potential=POTENTIAL_FOLLOW_UP_MIN_CHESTS-1) == (8, 0)
assert follow_up(nd=24, potential=POTENTIAL_FOLLOW_UP_MIN_CHESTS-1) == (24, 0)
assert follow_up(nd=8, potential=0) == (8, 0)

# --- bounded: one extended bank plus one validation bank, and never a third rung --------------
assert follow_up(nd=24, potential=1000) == (learner.EXTENDED_DISCOVERY_RUNS, VALIDATION_RUNS)
assert follow_up(nd=learner.EXTENDED_DISCOVERY_RUNS, potential=1000) == \
    (learner.EXTENDED_DISCOVERY_RUNS, VALIDATION_RUNS)
assert learner.EXTENDED_DISCOVERY_RUNS + VALIDATION_RUNS <= MAX_SAMPLES

# --- the other promotion reasons still work on their own -------------------------------------
assert follow_up(nd=8, potential=0, win=True, earned=40) == (24, 0)
assert follow_up(nd=8, potential=1, rank=0) == (24, 0)
assert follow_up(nd=8, potential=1, rank=7) == (24, 0)
assert follow_up(nd=8, potential=1, rank=8) == (8, 0)
assert follow_up(nd=8, potential=0, rank=0, progress={'postDeathPrizes': 1}) == (24, 0)
assert follow_up(nd=8, potential=0, root=False) == (24, 0)
assert follow_up(nd=24, potential=0, root=False) == (24, 64)
assert follow_up(nd=8, potential=0, probe=True) == (8, 160)
assert follow_up(nd=24, potential=1000, probe=True) == (24, 160)

# --- the legacy reproduction is untouched by the new reason -----------------------------------
assert follow_up(nd=8, potential=119, mode='legacy') == (8, 0)
assert follow_up(nd=8, potential=119, mode='legacy') == follow_up(nd=8, potential=0, mode='legacy')
assert follow_up(nd=1, potential=119, mode='legacy') == (8, 64)
print('staged policy: novelty keeps discovery, a measured high-potential defeat earns the ordinary '
      'bounded follow-up, and the legacy reproduction is unchanged')
