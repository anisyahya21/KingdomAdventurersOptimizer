"""The students: a reserved share of every encounter's attempts, and the contract they are measured against.

The search has been one unweighted pool. This module names the streams that pool is split into, enforces
the community contract (C1-C6) as an *admission* rule rather than as a hope, and keeps the split
provenance-safe: every change here is scheduler-side, so no battle can reach a different outcome and no
library is invalidated by it.

Measured motivation (live library, encounter 11, 1,131,219 lifetime attempts):

  * 124 six-unit builds held 62,903 runs; exactly **one** satisfied the community contract and it received
    **88** runs - one screening bank - before the search moved on;
  * 120 of those 124 were five attackers plus one skill-less unit: the search handed attack skills to
    almost every member, which erases the healer and the fodder roles outright.

So the community shape was not tried and rejected. It was effectively never tried.

The contract, and why it is numbered: a rule is admitted only if it is a readable property of a *build*.
"The fodder dies early" is a run outcome, not a build property, so it is a survivability target rather
than an admission rule; "student 1 may only change values" is a permission about the student, so it is not
a rule either. What is left is checkable from a scenario alone, before any run is spent.
"""
from __future__ import annotations

from collections import namedtuple
from copy import deepcopy
import json
import math
import random
import sqlite3
import time

import search_contract as contract

# ---------------------------------------------------------------------------------------------
# Students and their shares
# ---------------------------------------------------------------------------------------------

#: The student that holds the community contract: fixed formation and classes, values only.
STUDENT_COMMUNITY = 'community'
#: The rebel: steals a current community best and breaks declared rules, in tiers x versions.
STUDENT_REBEL = 'rebel'
#: The blind learner: never reads the other two, credited later when it lands on what they found.
STUDENT_STUMBLING = 'stumble'
#: Named, deliberately undefined and deliberately unallocated until it has rules and a distance.
STUDENT_FREEWILL = 'freewill'
#: The average student: raise the best *reliable* mean earned on every fight, and never chase a record.
STUDENT_AVERAGE = 'average'
#: Breakthrough / reliability: turn exceptional episodes into measured, repeatable earning. Its own
#: share (0% by default) and its own budget; it investigates evidence from *any* rank rather than
#: refining an incumbent, which is what keeps it distinct from the average student above.
STUDENT_MECHANISM = 'mechanism'
#: Not a student: the existing unconstrained search, kept as a protected floor of the same pool.
STREAM_DISCOVERY = 'discovery'

STUDENT_NAMES = (STUDENT_COMMUNITY, STUDENT_REBEL, STUDENT_STUMBLING, STUDENT_AVERAGE,
                 STUDENT_FREEWILL, STUDENT_MECHANISM)
SHARE_STREAMS = (STUDENT_COMMUNITY, STREAM_DISCOVERY, STUDENT_REBEL, STUDENT_STUMBLING,
                 STUDENT_AVERAGE, STUDENT_MECHANISM)

#: The user's numbers. Applied **per encounter** rather than once globally, so a fight the search has
#: already ground for a million runs cannot buy the share of a fight that has had none. Student 4 has no
#: entry on purpose: an unallocated student cannot spend budget it was never given.
DEFAULT_SHARES = {STUDENT_COMMUNITY: 0.30, STUDENT_REBEL: 0.25,
                  STUDENT_STUMBLING: 0.20, STREAM_DISCOVERY: 0.25,
                  # Zero, not a share. The average student is switched on deliberately - the user's own
                  # words were that they want to decide how much compute it gets when they see the
                  # averages being this low - so it must not quietly take a slice of an existing
                  # configuration the moment it exists.
                  STUDENT_AVERAGE: 0.0,
                  # Zero for the same reason, and one more: this stream runs *experiments*, which cost
                  # more per answer than a mutation. It is switched on deliberately, never by default.
                  STUDENT_MECHANISM: 0.0}

#: The open search is the only stream that cannot be exhausted, so it keeps a floor whatever the others
#: are configured to. The community floor is an absolute reservation rather than a percentage because a
#: hand-judged reading needs a known number of attempts, not a fraction of an unknown total.
DISCOVERY_FLOOR = 0.25
COMMUNITY_FLOOR_RUNS_PER_ENCOUNTER = 1000


def shares(overrides=None):
    """`{stream: fraction}` with overrides applied, normalised so the total is 1.

    Overrides come from the settings surface. An unknown name is refused rather than ignored, and a
    negative or non-numeric share is refused outright. A total that is not 1 is normalised rather than
    rejected, because a settings control that edits one number at a time cannot keep the total at 1 while
    the user is mid-edit - but nothing here is silent: `share_warning` reports what the result actually is,
    so a caller can show the real split instead of assuming the one that was typed.

    A user setting 100% on one student is a legitimate configuration ("everything is just student one")
    and is normalised to exactly that, not refused by the discovery floor. The floor is a default to be
    defended in the interface, not a veto over the person who owns the budget.
    """
    values = dict(DEFAULT_SHARES)
    for name, fraction in (overrides or {}).items():
        if name not in SHARE_STREAMS:
            raise ValueError(f'unknown stream {name!r}; expected one of {SHARE_STREAMS}')
        try:
            fraction = float(fraction)
        except (TypeError, ValueError):
            raise ValueError(f'{name} share must be a number, not {fraction!r}') from None
        if not 0.0 <= fraction <= 1.0:
            raise ValueError(f'{name} share must be between 0 and 1, not {fraction}')
        values[name] = fraction
    total = sum(values.values())
    if total <= 0:
        raise ValueError('at least one stream must hold a positive share')
    return {name: value/total for name, value in values.items()}


def share_warning(values):
    """One sentence when the configured split has taken the open search below its floor, else None.

    Reported rather than enforced: the floor exists because open discovery is the only stream that cannot
    be exhausted, but the person setting the shares is allowed to overrule it deliberately.
    """
    discovery = float(values.get(STREAM_DISCOVERY, 0.0))
    if discovery + 1e-9 >= DISCOVERY_FLOOR:
        return None
    return (f'open discovery is {discovery:.0%} of the pool, below its {DISCOVERY_FLOOR:.0%} floor: '
            'the search that produced every build this plan is built on is being cut')


# ---------------------------------------------------------------------------------------------
# The community contract, C1-C8
# ---------------------------------------------------------------------------------------------

#: Healing spells that make a unit a healer. `Backup`/`Daring Charge`/`Leading the Charge` are formation
#: skills and are not healing; a DPS may carry one and still be the DPS.
HEALING_SKILLS = frozenset({37, 38, 39})
FORMATION_SKILLS = frozenset(contract.FORMATION_SKILLS)

#: The community placement, by placement index (see `combat_initial_state.formation`): the DPS first, the
#: four fodder in the front row, the healer behind the DPS. The absolute cell coordinates follow from the
#: encounter's enemy depth, so the contract is stated in indices and is the same on every fight.
COMMUNITY_PLACEMENT = ('dps', 'fodder', 'fodder', 'fodder', 'fodder', 'healer')

ROLE_DPS = 'dps'
ROLE_HEALER = 'healer'
ROLE_FODDER = 'fodder'

#: What each numbered rule says in one clause, so a rule-break ledger reads without the doc.
#:
#: C1-C6 are the user's own list, unchanged and in the user's own order. C7 and C8 are **added**, not
#: substituted: two of the moves the rebel design exists to test ("use or remove a skill" and "make the
#: fodder tougher") broke no rule at all under C1-C6, so they could not be attributed to a distance and
#: could not appear in the ledger. Adding them at the end leaves every earlier number meaning exactly
#: what it meant before.
RULE_TITLES = {
    'C1': 'six units, no more and no fewer',
    'C2': 'the community placement: DPS on index 0, fodder on 1-4, healer on 5',
    'C3': 'the DPS carries no healing spell',
    'C4': 'the healer carries only healing spells plus a formation skill',
    'C5': 'the fodder carries nothing',
    'C6': 'the herb rule',
    'C7': 'the community skill-set is held',
    'C8': 'the fodder is no tougher than the community fodder',
}
RULE_IDS = tuple(RULE_TITLES)

#: C8's ceiling, and where the numbers come from.
#:
#: Both are the **community baseline's own fodder**, read from the live library rather than chosen:
#: every one of the twenty encounters' supplied community cast carries its fodder at HP 150 / DEF 42
#: (read 25 September 2026 from `strategiesv18.sqlite`). So "no tougher than the community fodder" is a
#: statement about the build the rules already describe, not a new invented threshold - which is what
#: makes it checkable *before* a run instead of being read off the fight as "the fodder died early".
#:
#: It has to be a value ceiling, and it has to be a build property: "the fodder dies early" is a run
#: outcome, so it can never be an admission rule, while "this fodder's HP and Defence do not exceed the
#: community fodder's" can be read off a candidate before a run is spent.
FODDER_HP_CEILING = 150
FODDER_DEF_CEILING = 42

#: `(stat, parameter id, baseline ceiling)` for each stat C8 bounds.
FODDER_STATS = (('hp', 10, FODDER_HP_CEILING), ('def', 14, FODDER_DEF_CEILING))

#: The four placement indices the community formation gives to the fodder.
FODDER_INDICES = (1, 2, 3, 4)


def role(unit):
    """`dps` / `healer` / `fodder` for one unit, from what it carries.

    C3's "the DPS carries no healing spell" and C4's "the healer carries only healing spells plus a
    formation skill" are two halves of the same classification, which is why this returns a role and the
    admission test below compares it against the placement: a unit carrying a healing spell is a healer,
    so a DPS that gains one stops being the DPS and the candidate stops being a community build.
    """
    skills = set(unit.get('skills') or ())
    if skills & HEALING_SKILLS:
        return ROLE_HEALER
    return ROLE_DPS if skills - FORMATION_SKILLS else ROLE_FODDER


def placed_roles(scenario):
    """The role at each placement index, computed from the derived placement rather than the roster order.

    Placement is not authored: `combat_initial_state.formation` sorts units by `(priority, -effective
    defence, incoming index)`, so a defence change, a different formation skill or a roster reorder can
    each move a unit. Reading the derived placement is therefore the only honest way to ask "is this still
    the community formation", and it needs no simulation.
    """
    by_index = _by_index(scenario)
    return tuple(role(by_index[index]) if index in by_index else None
                 for index in range(len(by_index)))


def community_rules(scenario, parent=None):
    """`[(rule, ok, detail)]` for C1-C8, so a refusal can name the rule it refused.

    `ok` is deliberately **three-valued**:

      * `True`  - the rule holds;
      * `False` - the build breaks it;
      * `None`  - the rule does not apply to this build. A seven-unit roster has no community placement
        to compare against, so C2-C5 and C8 are simply not questions about it.

    The third value is what lets one function serve both jobs honestly. An **admission** test cares only
    about `False`, so `community_admits` reads it that way. A **rebel ledger** cares about exactly which
    rules are `False`, and would be badly wrong if "not applicable" counted as a break: a seventh unit
    would appear to break six rules instead of one.

    `parent` is the build the rules are measured *from* - the community build a rebel was stolen from.
    Only C7 needs it, because a skill-set can only be "held" relative to something; C8 uses it to take
    the fodder's ceiling from that parent's own fodder. With no parent, C7 is reported as not applicable
    rather than assumed held.
    """
    units = scenario.get('ownUnits') or []
    checks = []

    # C1 - six units, no more and no fewer.
    checks.append(('C1', len(units) == 6, f'{len(units)} units'))
    if len(units) != 6:
        # Every later rule is about a placement index or a per-unit comparison that only exists in a
        # six-unit roster, so the rest are reported as not applicable rather than guessed at.
        for rule in ('C2', 'C3', 'C4', 'C5', 'C8'):
            checks.append((rule, None, f'not applicable: {RULE_TITLES[rule]} needs six units'))
        checks.append(('C6', None, 'not applicable: the herb rule needs the six-unit roster'))
        checks.append(('C7', None, 'not applicable: a skill-set is only held relative to a parent'))
        return checks

    # The derived placement is the expensive part of this function (it rebuilds the setup), and every
    # rule below needs the same reading of it, so it is computed once. `placed_roles` is the same
    # computation written for callers that want only the roles.
    by_index = _by_index(scenario)
    roles = tuple(role(by_index[index]) if index in by_index else None
                  for index in range(len(by_index)))
    checks.append(('C2', roles == COMMUNITY_PLACEMENT,
                   f'placement {list(roles)}'))

    dps = by_index.get(0) or {}
    healer = by_index.get(5) or {}
    fodder = [by_index.get(index) or {} for index in FODDER_INDICES]

    # C3 - the DPS carries no healing spell.
    dps_heals = sorted(set(dps.get('skills') or ()) & HEALING_SKILLS)
    checks.append(('C3', not dps_heals, f'DPS healing spells {dps_heals}' if dps_heals else 'none'))

    # C4 - the healer carries only healing spells plus a formation skill.
    healer_skills = set(healer.get('skills') or ())
    healer_heals = sorted(healer_skills & HEALING_SKILLS)
    healer_extra = sorted(healer_skills - HEALING_SKILLS - FORMATION_SKILLS)
    checks.append(('C4', bool(healer_heals) and not healer_extra,
                   f'heals {healer_heals}, other {healer_extra}'))

    # C5 - the fodder carries nothing.
    carrying = [f'index {index+1}: {sorted(unit.get("skills") or ())}'
                for index, unit in enumerate(fodder) if unit.get('skills')]
    checks.append(('C5', not carrying, '; '.join(carrying) if carrying else 'none'))

    # C6 - the herb rule. The recovered item path has no automatic trigger, so a declared policy is the
    # only thing that makes a herb use possible; until the rule exists in the combat modules it is
    # reported as inert rather than as satisfied. **No candidate can break C6 today**: the herb branch
    # needs the new library (§9 of the plan), so a tier that lists C6 has nothing to draw on here.
    policy = scenario.get('herbPolicy')
    checks.append(('C6', policy is None or isinstance(policy, dict),
                   'declared' if isinstance(policy, dict) else 'not declared (inert)'))

    # C7 - the community skill-set is held.
    #
    # Read off the ordered skill list of every human and compared with the parent's. This is the rule
    # that makes a skill edit expressible at all: C3-C5 say what a role may *carry*, but nothing in them
    # forbids removing the DPS's Counter or swapping it for another approved attack - and "use a
    # disallowed skill" and "remove a crucial skill" are two of the moves the rebel design exists to
    # test. Without C7 those builds broke no rule and could not be attributed to a distance.
    #
    # It is the whole skill list rather than one unit's, so both of those moves land on one numbered
    # rule and read as one break in the ledger. Order is part of the strategy
    # (`search_contract.add_skill` says so), so the list is compared as a list, not as a set.
    #
    # Overlap with C3-C5 is expected and reported as it is: a healing spell added to the DPS breaks C3,
    # C7 - and C2, because a unit carrying a heal *is* a healer, so the derived placement moves it.
    # Those are three real things that happened to the build, and the ledger says three.
    #
    # Trigger settings are not part of C7. A trigger is part of a candidate's identity but it is not a
    # change to the skill set, and folding it in would make "broke C7" mean two different edits.
    if parent is None:
        checks.append(('C7', None, 'not applicable: a skill-set is only held relative to a parent'))
    else:
        mine, theirs = skill_inventory(scenario), skill_inventory(parent)
        changed = sorted(name for name in set(mine) | set(theirs) if mine.get(name) != theirs.get(name))
        checks.append(('C7', not changed, f'changed {changed}' if changed else 'held'))

    # C8 - the fodder is no tougher than the community fodder.
    #
    # A ceiling on values, never on behaviour. Only units still placed as fodder are measured: a fodder
    # that became a healer is C5's and C2's business, not this rule's.
    hp_ceiling, def_ceiling = fodder_ceiling(parent)
    ceilings = {'hp': hp_ceiling, 'def': def_ceiling}
    over = []
    for index in FODDER_INDICES:
        unit = by_index.get(index)
        if not unit or role(unit) != ROLE_FODDER:
            continue
        for stat, parameter, _baseline in FODDER_STATS:
            try:
                value = int(contract.battle_value(scenario, unit, parameter) or 0)
            except (KeyError, TypeError, ValueError, contract.ContractError):
                continue
            if value > ceilings[stat]:
                over.append(f'index {index+1} {stat} {value} > {ceilings[stat]}')
    checks.append(('C8', not over,
                   '; '.join(over) if over else f'hp<={hp_ceiling}, def<={def_ceiling}'))
    return checks


def community_admits(scenario, parent=None):
    """`(True, [])` or `(False, [broken rules])` for a candidate about to spend a run.

    This is the whole point of Student 1: a candidate that answers No is refused before it can consume an
    attempt, so every build the community student produces is a community build by construction. A rule
    reported as *not applicable* is not a failure - see `community_rules` for why `ok` is three-valued.
    """
    failures = [(rule, detail) for rule, ok, detail in community_rules(scenario, parent) if ok is False]
    return (not failures), failures


def rule_breaks(scenario, parent):
    """The numbered rules `scenario` breaks *relative to `parent`* - one row of a rebel's ledger.

    `parent` is required rather than optional: a break is a distance, and a distance needs a point to be
    measured from. A rule reported as not applicable is never counted as broken.
    """
    return frozenset(rule for rule, ok, _detail in community_rules(scenario, parent) if ok is False)


def skill_inventory(scenario):
    """`{unit name: [skill id, ...]}` in carried order - what C7 compares."""
    return {unit['name']: list(unit.get('skills') or ())
            for unit in (scenario.get('ownUnits') or []) if unit.get('human')}


def fodder_ceiling(parent):
    """`(hp ceiling, def ceiling)` for C8, taken from `parent`'s own fodder.

    Relative to the parent wherever there is one, so a rebel is measured against the community build it
    was actually stolen from. On a community parent this agrees with `FODDER_HP_CEILING` /
    `FODDER_DEF_CEILING`, because Student 1's ladder never touches a fodder's survivability
    (`community_child` skips the fodder on every axis) - which is what makes the constant a *reading* of
    the baseline rather than a choice.
    """
    ceiling = {'hp': FODDER_HP_CEILING, 'def': FODDER_DEF_CEILING}
    if parent is None or len(parent.get('ownUnits') or []) != 6:
        return ceiling['hp'], ceiling['def']
    by_index = {int(row['grid']): row['unit'] for row in _placed_rows(parent)}
    for index in FODDER_INDICES:
        unit = by_index.get(index)
        if not unit or role(unit) != ROLE_FODDER:
            continue
        for stat, parameter, _baseline in FODDER_STATS:
            try:
                value = int(contract.battle_value(parent, unit, parameter) or 0)
            except (KeyError, TypeError, ValueError, contract.ContractError):
                continue
            ceiling[stat] = value
    return ceiling['hp'], ceiling['def']


#: A small bounded memo for the derived placement, keyed by the scenario object.
#:
#: Reading the placement rebuilds the whole setup, and one admission test asks for the same reading
#: several times over (the rule checks, the fodder ceiling, and the rebel break menu all want it). That
#: is what made a rebel draw cost about a quarter of a second, which is far too much to spend per
#: proposed child in a planner that runs on the coordinator thread.
#:
#: Caching on the object is safe because scenarios are treated as immutable throughout this module -
#: every `search_contract` helper returns a fresh deep copy rather than editing in place - and the memo
#: holds a reference to the scenario alongside its rows, so a recycled `id()` can never hand back a
#: different build's placement. It is cleared wholesale once it grows past the limit rather than
#: evicted one at a time, and 256 entries covers one replenishment pass across every encounter.
_PLACEMENT_CACHE = {}
_PLACEMENT_CACHE_LIMIT = 256


def _placed_rows(scenario):
    """`[{grid, cell, unit}]` in the derived placement, joining each prepared row back to its source unit."""
    key = id(scenario)
    hit = _PLACEMENT_CACHE.get(key)
    if hit is not None and hit[0] is scenario:
        return hit[1]
    from combat_setup import prepare_setup
    by_name = {unit['name']: unit for unit in scenario['ownUnits']}
    rows = []
    for row in prepare_setup(deepcopy(scenario))['ownUnits']:
        rows.append(dict(grid=int(row['grid']), cell=tuple(row['cell']),
                         unit=by_name[row['name']]))
    if len(_PLACEMENT_CACHE) >= _PLACEMENT_CACHE_LIMIT:
        _PLACEMENT_CACHE.clear()
    _PLACEMENT_CACHE[key] = (scenario, rows)
    return rows




def community_shape(scenario):
    """Where the community build stands, described for a note rather than for a pass/fail.

    Enough to read at a glance whether a fight is being worked in the community shape, and by how much a
    given build differs when it is not.
    """
    if len(scenario.get('ownUnits') or []) != 6:
        return dict(units=len(scenario.get('ownUnits') or []), placement=None, roles=None)
    roles = placed_roles(scenario)
    return dict(units=6, placement=[list(row['cell']) for row in _placed_rows(scenario)],
                roles=list(roles), admitted=roles == COMMUNITY_PLACEMENT)


# ---------------------------------------------------------------------------------------------
# Student 1: values only, and always admitted
# ---------------------------------------------------------------------------------------------

#: What Student 1 may vary: values, never structure. Attack, Speed and Luck are the sweep the user asked
#: for; Health, Defence and MP are the survivability gate, raised when runs show early death or exhaustion
#: and then frozen; Dexterity is included because the working design runs it low and low is a value.
COMMUNITY_VALUE_STATS = ('atk', 'spd', 'lck', 'hp', 'def', 'mp', 'dex')

#: Ceilings for a **Shaped** character, per stat, where they are lower than the derived legal wall.
#:
#: `contract.stat_bounds()` is the legal wall: a unit's own row at max level plus the best single
#: level-99 equipment contribution. That is the *gen-two* reach - a born character, whose stats are set
#: together at birth at roughly 30-80% of maximum and can therefore sit much higher on one axis (the
#: royal's MP) than a levelled character ever can.
#:
#: The defining property is **not bred**, never "generation one": a gen-one human that was bred is Maxed,
#: and a Shaped gen-two human cannot exist, because gen two is always bred. Naming this after the
#: generation rather than the breeding is exactly the confusion this comment exists to prevent.
#:
#: The community strategy's DPS is necessarily Shaped, because its whole design is *asymmetry*: chosen
#: stats high, others deliberately low. Normal levelling raises everything at once, so only a not-bred
#: character fed selectively can hold that shape. Measuring its rungs against the Maxed wall would
#: therefore test combinations that no Shaped character can be built into - the same class of error as
#: testing an unreachable loadout, one provenance over.
#:
#: Empty means "fall back to the derived wall". Every entry added here narrows the ladder to what a
#: gen-one character can actually be fed into.
SHAPED_CAPS = {}


def community_bounds(stat, parameter):
    """`(low, high)` for one axis as a **Shaped** character can reach it, or None.

    The floor is never raised: a Shaped character is levelled from one, so low values are the easy end.
    Only the ceiling is provenance-dependent.
    """
    legal = contract.stat_bounds().get(parameter)
    if not legal:
        return None
    low, high = legal
    cap = SHAPED_CAPS.get(stat)
    if cap is None:
        return low, high
    return low, min(int(high), int(cap))


def _retained_community_ids(store):
    """Only retained builds can match candidate-table queries; keep all lineage history intact."""
    retained = {row[0] for row in store.db.execute('SELECT id FROM candidate')}
    return [cid for cid, source in student_of(store).items()
            if source == COMMUNITY_SOURCE and cid in retained]


def community_parent(store, encounter):
    """`(candidate_id, scenario)` for the encounter's community parent, or `(None, None)`.

    The parent is chosen on **measured evidence, not recency**. Taking the newest child made Student 1 a
    random walk: it kept mutating whatever it had just made, so a good value combination was never built
    on and the sweep could not converge. Ranking by the fight's own numbers turns the same generator into
    a refinement - each move starts from the best community build this fight has produced so far.

    Ranking is by the strongest reading available, in this order: mean chests over a comparable sample,
    then the best single run, then the widest opportunity a defeat reached. Mean first, because the point
    of the community track is a build that pays every time rather than a build that paid once. A candidate
    with no measured sample yet ranks below any candidate that has one, which is what makes the first
    evaluated children the ones that get refined.

    Fallback: the supplied baseline, which is the encounter's own community cast and is admitted as it
    stands. It is used only when no admitted community child exists yet.
    """
    # A planning pass can ask for the same encounter's parent many times before any result or
    # candidate changes. Admission rebuilds combat placement for every eligible historical
    # build, so keep the selected id until the evidence or retained candidate set changes.
    # `totalRuns` advances with committed aggregate updates. The candidate shape and newest
    # creation time also catch a delete+insert that reuses SQLite's highest rowid.
    run_row = store.db.execute("SELECT value FROM meta WHERE key='totalRuns'").fetchone()
    candidate_count, newest_rowid, newest_created = store.db.execute(
        'SELECT COUNT(*), MAX(rowid), MAX(created) FROM candidate').fetchone()
    token = (run_row[0] if run_row else None, candidate_count, newest_rowid, newest_created)
    cache = getattr(store, '_community_parent_cache', None)
    cached = cache.get(int(encounter)) if isinstance(cache, dict) else None
    if cached is not None and cached[0] == token:
        cid = cached[1]
        return (cid, store.scenario(cid)) if cid is not None else (None, None)

    owned = _retained_community_ids(store)
    supplied = store.db.execute(
        "SELECT c.id AS cid, c.scenario AS scenario FROM candidate c JOIN candidate_meta m ON m.id=c.id "
        "WHERE m.encounter=? AND c.source='supplied' LIMIT 2", (int(encounter),)).fetchall()
    best = None
    if owned:
        # Lineage retains deleted candidates; binding every historical id can exceed SQLite's
        # variable limit. Read this encounter's retained candidates and apply the identical owner
        # membership test before ranking. The measured ordering and tie-breaks are unchanged.
        owned = set(owned)
        ranked = []
        for row in store.db.execute(
                "SELECT c.id AS cid, c.created AS created FROM candidate c "
                "JOIN candidate_meta m ON m.id=c.id WHERE m.encounter=? "
                "ORDER BY c.created DESC", (int(encounter),)):
            cid = row['cid']
            if cid not in owned:
                continue
            mean, samples, best_run, potential = _measure(store, cid)
            # A measured mean outranks an unmeasured one; ties break on the sample size behind it, then
            # on the record, then on the single-run opportunity, then on creation order for determinism.
            key = (mean is not None, mean or 0.0, samples, best_run, potential, row['created'])
            ranked.append((key, cid))
        # Admission needs a derived combat placement. Rank on the same measured key first,
        # then derive placements only until the best admitted build is found. Stable sorting
        # preserves the original newest-first tie order; an inadmissible high scorer cannot win.
        ranked.sort(key=lambda item: item[0], reverse=True)
        for key, cid in ranked:
            candidate = store.scenario(cid)
            if candidate is not None and community_admits(candidate)[0]:
                best = (key, cid, candidate)
                break
    for row in supplied:
        if best is not None:
            break
        cid, scenario = row['cid'], row['scenario']
        candidate = json.loads(scenario) if isinstance(scenario, (str, bytes)) else scenario
        if community_admits(candidate)[0]:
            best = (None, cid, candidate)
    if not isinstance(cache, dict):
        cache = {}
        store._community_parent_cache = cache
    cache[int(encounter)] = (token, best[1] if best else None)
    return (best[1], best[2]) if best else (None, None)


def _measure(store, cid):
    """`(mean chests, samples, best run, best opportunity)` for one candidate, from its aggregates.

    Read from the aggregate counters rather than the stored rows, so a candidate with thousands of runs
    costs the same as one with ten, and a loss counts the zero its native gate released - which is exactly
    the number the mean leaderboard already uses.
    """
    samples = 0
    chest_sum = 0.0
    counted = 0
    best_run = 0
    potential = 0
    for phase in ('discovery', 'validation'):
        row = store.db.execute('SELECT value FROM meta WHERE key=?',
                               (f'aggregate:{cid}:{phase}',)).fetchone()
        if not row:
            continue
        try:
            data = json.loads(row[0])
        except (TypeError, ValueError):
            continue
        samples += int(data.get('n') or 0)
        counted += int(data.get('chestCount') or 0)
        chest_sum += float(data.get('chestSum') or 0.0)
        best_run = max(best_run, int(data.get('chestMax') or 0))
        potential = max(potential, int(data.get('potentialMax') or 0))
    mean = (chest_sum/counted) if counted else None
    return mean, samples, best_run, potential


#: The fewest resolved runs a donor build needs before its values are worth carrying to another fight.
#: Below this a "winner" is a lucky seed, and copying luck between encounters teaches nothing.
TRANSFER_MIN_SAMPLES = 40


def _has_measured(store, encounter):
    """True once this fight owns a community candidate with a real measured reading."""
    owned = _retained_community_ids(store)
    if not owned:
        return False
    placeholders = ','.join('?' for _ in owned)
    for row in store.db.execute(
            f"SELECT c.id AS cid FROM candidate c JOIN candidate_meta m ON m.id=c.id "
            f"WHERE m.encounter=? AND c.id IN ({placeholders})",
            (int(encounter), *owned)):
        mean, samples, _best, _pot = _measure(store, row['cid'])
        if mean is not None and samples >= TRANSFER_MIN_SAMPLES:
            return True
    return False


def best_community_build(store, exclude_encounter, minimum=TRANSFER_MIN_SAMPLES):
    """`(candidate_id, scenario, mean)` for the best-measured community build on any *other* fight.

    Ranked on the mean, then the record, then the sample count - the same order the parent rule uses, so
    the donor is the build that pays most consistently rather than the one that paid once. Excluding the
    target fight is the point of the exercise: carrying a fight's own values back to itself would be a
    no-op dressed up as a transfer.
    """
    owned = _retained_community_ids(store)
    if not owned:
        return None, None, None
    placeholders = ','.join('?' for _ in owned)
    best = None
    for row in store.db.execute(
            f"SELECT c.id AS cid, c.scenario AS scenario, m.encounter AS encounter FROM candidate c "
            f"JOIN candidate_meta m ON m.id=c.id WHERE c.id IN ({placeholders})", tuple(owned)):
        if int(row['encounter']) == int(exclude_encounter):
            continue
        mean, samples, best_run, potential = _measure(store, row['cid'])
        if mean is None or samples < minimum:
            continue
        key = (mean, best_run, samples, potential)
        if best is None or key > best[0]:
            best = (key, row['cid'], json.loads(row['scenario']), mean)
    if best is None:
        return None, None, None
    return best[1], best[2], best[3]


def transfer_seed(store, encounter, proposal, stats, rng=None):
    """Carry another fight's working values onto this fight's community cast. Returns the new id or None.

    Only the numbers travel. The target keeps its own cast, its own formation and its own scope, so the
    result is a legal community build for *this* encounter with a proven starting point instead of an
    untuned baseline. The contract is re-checked afterwards: a borrowed defence value can reorder the
    roster, and if it does the transfer is refused rather than shipped as a broken shape. When that
    happens the value-only axes are retried on their own, because Attack, Speed and Luck cannot move a
    unit and are therefore always safe to carry.
    """
    import random
    donor_id, donor, donor_mean = best_community_build(store, encounter)
    if donor is None:
        return None
    parent_id, target = community_parent(store, encounter)
    if target is None:
        return None
    rng = rng or random.Random(encounter*104729 + int(proposal or 0))
    by_role = {ROLE_DPS: None, ROLE_HEALER: None}
    for unit in donor['ownUnits']:
        unit_role = role(unit)
        if unit_role in by_role and by_role[unit_role] is None:
            by_role[unit_role] = unit
    for stats_used in (COMMUNITY_VALUE_STATS, ('atk', 'spd', 'lck')):
        child = deepcopy(target)
        for unit_role, source_unit in by_role.items():
            if source_unit is None:
                continue
            for stat in stats_used:
                parameter = contract.stat_parameter(stat)
                try:
                    value = int(contract.battle_value(donor, source_unit, parameter) or 0)
                except (KeyError, TypeError, ValueError, contract.ContractError):
                    continue
                if value <= 0:
                    continue
                name = next((u['name'] for u in child['ownUnits'] if role(u) == unit_role), None)
                if name is None:
                    continue
                current = 0
                try:
                    current = int(contract.battle_value(child, next(
                        u for u in child['ownUnits'] if u['name'] == name), parameter) or 0)
                except (KeyError, TypeError, ValueError, contract.ContractError):
                    pass
                if current == value:
                    continue
                try:
                    child = contract.set_stat(child, name, stat, value)
                except (contract.ContractError, ValueError):
                    continue
        if community_admits(child)[0]:
            label = (f'Community · transfer · from enc {donor_id and store_encounter(store, donor_id)}'
                     f' (mean {donor_mean:.2f})')[:160]
            cid, existed = store.add_child(child, label, lambda child=child: stats(child), parent_id,
                                           'transfer', 'values', f'from {donor_id}',
                                           COMMUNITY_SOURCE, proposal)
            return None if existed else cid
    return None


def store_encounter(store, cid):
    """The encounter id a candidate belongs to, or None."""
    row = store.db.execute('SELECT encounter FROM candidate_meta WHERE id=?', (cid,)).fetchone()
    return int(row[0]) if row else None


#: The `candidate.source` value every community candidate carries. Attribution reuses the existing column
#: rather than adding a schema field, so a library written before this module reads back unchanged and
#: "how many runs did the community student get" is answered by the evidence that already exists.
#
# Attribution is recorded on the **lineage** row, not on `candidate.source`: `candidate.source` is the
# coarse category the evidence machinery uses ('supplied', 'mutation', 'probe', 'saved') and every
# generated child is 'mutation' regardless of which student proposed it. `Store.add_child` stores the
# proposing stream on `lineage.source`, which is where the students live.
COMMUNITY_SOURCE = 'community'


def student_of(store):
    """`{candidate_id: student}` from the lineage rows that recorded which stream proposed each child.

    A candidate with no lineage row is the supplied baseline and belongs to no student, which is correct:
    it is the material the students start from rather than something a student produced.
    """
    return {cid: source for cid, source in store.db.execute(
        'SELECT candidate, source FROM lineage') if source}


def run_report(store):
    """`{student: {candidates, runs, encounters}}` from the evidence the library already keeps.

    Runs are read from the per-candidate aggregate counters rather than by counting stored rows, so a
    library holding millions of runs answers in one pass over a small evidence table. `unowned` is the
    ordinary search and is reported alongside the students rather than hidden: a share is only meaningful
    next to what the rest of the pool actually received.
    """
    owned = student_of(store)
    encounters = {}
    for cid, source in owned.items():
        encounters.setdefault(source, set())
    counts = {}
    for row in store.db.execute(
            'SELECT l.source AS source, m.encounter AS encounter FROM lineage l '
            'JOIN candidate c ON c.id=l.candidate '
            'JOIN candidate_meta m ON m.id=c.id'):
        if row['source']:
            encounters.setdefault(row['source'], set()).add(row['encounter'])
    runs = {}
    for row in store.db.execute("SELECT key, value FROM meta WHERE key LIKE 'aggregate:%'"):
        cid = row['key'].split(':')[1]
        source = owned.get(cid) or 'unowned'
        try:
            runs[source] = runs.get(source, 0) + int(json.loads(row['value']).get('n') or 0)
        except (TypeError, ValueError):
            continue
    for source in set(list(encounters) + list(runs)):
        counts[source] = dict(candidates=sum(1 for s in owned.values() if s == source),
                              encounters=len(encounters.get(source) or ()),
                              runs=runs.get(source, 0))
    return counts


#: How many axes one local move may touch at once, and how far each may go.
#:
#: The step is a *fraction of the axis's own current value*, not another ladder rung: the ladder is
#: geometric over the whole legal span and is meant to find the region, while a local move is meant to
#: find the best point inside a region already known to be interesting.
LOCAL_AXES = (2, 4)
LOCAL_STEP = 0.12

#: How many draws a pair or local move gets before the neighbourhood is called saturated.
VALUE_ATTEMPTS = 12


# ---------------------------------------------------------------------------------------------
# Student 1's tracks: the named sub-divisions of its share
# ---------------------------------------------------------------------------------------------
#
# Student 1 is not one sweep. Its share divides across **tracks**, each a permanent line that varies one
# thing or one combination - the same structure Student 2 has in tiers, for the same reason: a line that
# is cut when another line starts cannot answer "does this number still matter now that the others have
# moved". Tracks carry their own share, so a user who wants Attack and Luck worked hard, and HP barely
# touched, can say so directly instead of hoping the rotation gets there.
#
# The reason the structure matters rather than being bookkeeping: a single-dimensional ladder sweep
# cannot reach a compensating pair at all. If Attack up is worse on its own and Speed down is worse on
# its own, then no sequence of one-axis steps contains the combination that beats both - the shape is
# absent from the space, however long the search runs. Pair and dial tracks exist to put it there.

#: One track. `kind` decides what a child of the track may move:
#:
#:   * `axis` - exactly one stat, on whichever units may carry it. That stat's ladder, both directions,
#:     finding its range;
#:   * `pair` - exactly two axes at once, walking **every** pair so the relation between any two axes is
#:     eventually tested rather than a few lucky ones;
#:   * `dial` - several axes together, each landing inside the span this fight has actually converted
#:     in. This is where a compensating move lives: "Attack up needs Speed down".
Track = namedtuple('Track', 'name kind stats title')

#: The default table. One track per axis, so "only Attack" and "only Luck" are real lines with their own
#: budgets; one pair track, the only way a compensating pair is reachable at all; one dial track.
#:
#: The herb-threshold track the plan lists is absent on purpose: the herb branch does not exist in this
#: library (plan §9), so a track for it would have nothing to vary, and inventing one would put a line
#: in the settings that can never spend its share.
DEFAULT_TRACKS = (
    Track('atk', 'axis', ('atk',), 'Attack'),
    Track('spd', 'axis', ('spd',), 'Speed'),
    Track('lck', 'axis', ('lck',), 'Luck'),
    Track('hp', 'axis', ('hp',), 'Health'),
    Track('def', 'axis', ('def',), 'Defence'),
    Track('mp', 'axis', ('mp',), 'MP'),
    Track('dex', 'axis', ('dex',), 'Dexterity'),
    Track('pair', 'pair', COMMUNITY_VALUE_STATS, 'every pair of values'),
    Track('dial', 'dial', COMMUNITY_VALUE_STATS, 'the dial'),
)


def tracks():
    """The track table, in the order the settings surface shows it."""
    return DEFAULT_TRACKS


def track_shares(overrides=None):
    """`{track: fraction}` summing to 1 - how one student's share divides across its tracks.

    Equal by default, because no track has been shown to deserve more than another yet. Normalised
    rather than refused, for the same reason the student split is: a control that edits one number at a
    time cannot keep a total at 1 while the user is mid-edit. A track set to zero stays zero - that is
    the user turning it off, not a number to be rebalanced.
    """
    known = {track.name for track in DEFAULT_TRACKS}
    values = {name: 1.0/len(DEFAULT_TRACKS) for name in known}
    for name, fraction in (overrides or {}).items():
        if name not in known:
            raise ValueError(f'unknown track {name!r}; expected one of {sorted(known)}')
        try:
            fraction = float(fraction)
        except (TypeError, ValueError):
            raise ValueError(f'{name} share must be a number, not {fraction!r}') from None
        if not 0.0 <= fraction <= 1.0:
            raise ValueError(f'{name} share must be between 0 and 1, not {fraction}')
        values[name] = fraction
    total = sum(values.values())
    if total <= 0:
        raise ValueError('at least one track must hold a positive share')
    return {name: value/total for name, value in values.items()}


def pair_keys(scenario, only=None, roles=None):
    """Every unordered pair of axes, in a stable order - the pair track's coverage list.

    "It has to know the relationship of every pair" is a coverage claim, so the pairs are *enumerated*
    rather than sampled and the track walks the list by index. That is what makes coverage checkable:
    `pair_coverage` can then say exactly which pairs have been tested and which are still owed, instead
    of "we have looked at pairs" being an assertion nobody can falsify.
    """
    keys = [(name, stat) for name, stat, *_ in _value_axes(scenario, only, roles)]
    return [(keys[i], keys[j]) for i in range(len(keys)) for j in range(i+1, len(keys))]


def pair_label(pair):
    """A pair's canonical text, so a stored child names the pair it tested."""
    return '+'.join(f'{name}|{stat}' for name, stat in pair)


def _value_axes(scenario, only=None, roles=None, units=None):
    """`[(unit name, stat, current, low, high)]` - every axis Student 1 may vary, in a stable order.

    The same restrictions the ladder always applied: Attack, Speed, Luck and Dexterity only on the DPS,
    survivability only on a unit that is not fodder. **The fodder is never a value axis** - growing a
    fodder is a rebellion (C8), not a value sweep.

    `only` narrows the result to one stat name or a set of them, which is how a *track* sees only the
    axes it owns. Filtering here rather than in each generator is what makes "the Attack track moved
    something other than Attack" unrepresentable instead of merely unlikely.

    `roles` narrows it further to units playing one of those roles *in this build's own derived
    placement* (see `role`). That is how the average student keeps its earning search on the DPS it is
    supposed to be improving, instead of tuning whatever unit happens to hold the axis.

    `units` names exact units, which is what a *repair* needs: the evidence says which unit was cut
    short, so the repair is scoped to that unit rather than to whichever one the rotation reaches first.

    When `roles` is given, the Attack/Speed/Luck/Dexterity-are-the-DPS's rule is replaced by that role
    filter, so a caller that explicitly asks about the healer receives the healer's own offensive axes.
    Student 1's tracks pass no roles and keep the original rule exactly.
    """
    wanted = None if only is None else ({only} if isinstance(only, str) else set(only))
    wanted_roles = None if roles is None else ({roles} if isinstance(roles, str) else set(roles))
    wanted_units = None if units is None else ({units} if isinstance(units, str) else set(units))
    axes = []
    for stat in COMMUNITY_VALUE_STATS:
        if wanted is not None and stat not in wanted:
            continue
        parameter = contract.stat_parameter(stat)
        bounds = community_bounds(stat, parameter)
        if not bounds:
            continue
        low, high = bounds
        for unit in scenario.get('ownUnits') or []:
            if not unit.get('human'):
                continue
            current_role = role(unit)
            if wanted_units is not None and unit.get('name') not in wanted_units:
                continue
            if (wanted_roles is None and stat in ('atk', 'spd', 'lck', 'dex')
                    and current_role != ROLE_DPS):
                continue
            if stat in ('hp', 'mp', 'def') and current_role == ROLE_FODDER:
                continue
            if wanted_roles is not None and current_role not in wanted_roles:
                continue
            try:
                current = int(contract.battle_value(scenario, unit, parameter) or 0)
            except (KeyError, TypeError, ValueError, contract.ContractError):
                continue
            axes.append((unit['name'], stat, current, low, high))
    return axes


def _rung_step(scenario, name, stat, current, low, high, rng, taken=None, direction=None):
    """One ladder rung on one axis, nearest the current value first. `(child, description)` or None.

    `direction='up'` keeps only rungs strictly above the current value, so a caller that means to raise
    a resource cannot be handed a rung that lowers it.
    """
    rungs = ladder_values(low, high, current)
    if direction == 'up':
        rungs = [rung for rung in rungs if rung > current]
    elif direction == 'down':
        rungs = [rung for rung in rungs if rung < current]
    if rng is not None and len(rungs) > 3:
        # Near-first still wins on average, but not deterministically: a probe that always took the
        # closest rung would test one cell of the neighbourhood forever.
        head, tail = rungs[:3], rungs[3:]
        rng.shuffle(head)
        rungs = head + tail
    for target in rungs:
        try:
            child = contract.set_stat(scenario, name, stat, target)
        except (contract.ContractError, ValueError, KeyError, TypeError):
            continue
        if taken is not None and taken(child):
            continue
        return child, f'{stat} {current}->{target}'
    return None


def _local_step(scenario, name, stat, current, low, high, rng):
    """One small step on one axis, as a fraction of where it already stands. `(child, text)` or None."""
    targets = []
    for factor in (1.0 - LOCAL_STEP, 1.0 + LOCAL_STEP):
        target = int(round(current * factor))
        target = max(int(low), min(int(high), target))
        if target != current and target not in targets:
            targets.append(target)
    if rng is not None:
        rng.shuffle(targets)
    for target in targets:
        try:
            child = contract.set_stat(scenario, name, stat, target)
        except (contract.ContractError, ValueError, KeyError, TypeError):
            continue
        return child, f'{stat} {current}->{target}'
    return None


def community_child(parent, rng=None, taken=None, offset=0, only=None, roles=None, direction=None,
                    units=None):
    """Stage 1 and 2: one rung of the frozen-base ladder, guaranteed to satisfy the contract.

    `only`/`roles` restrict which axes may move (see `_value_axes`), and `direction` restricts which way:
    ``'up'`` picks only rungs above the current value. A repair is allowed to raise a support resource
    and is never allowed to search for a smaller one, so that direction is enforced here rather than
    hoped for in the caller.

    This is the range-discovery stage, and it doubles as the coordinate-refinement stage, because the
    parent it steps from is always the **best measured** community build (`community_parent`), never the
    one most recently made. Stepping from the elite is what makes the sequence a coordinate descent: after
    one axis moves, the next call re-reads the elite and steps the next axis from the *updated*
    configuration, so an axis that was tuned before another one moved is revisited rather than frozen at
    a value that was only right in company it no longer keeps.

    What one call cannot do is move two axes at once - see `community_pair_child` and
    `community_local_child` for the two stages that can.

    **The axis is chosen by rotation, not drawn at random.** `offset` starts the walk at a different axis,
    so successive calls sweep the coordinates one after another and a passing cursor makes every axis take
    its turn. The earlier version shuffled the axis list on every call, which meant an axis could stay
    half-swept indefinitely while the others were re-proposed: a refinement that never finishes a
    coordinate is a random walk with a ladder attached, not a coordinate search. The *rung* within the
    axis is still sampled, so an axis is not stepped to the same point every time.

    Nothing here reads the whole axis list before answering - the walk stops at the first rung that is
    settable, still inside the contract and not already measured - because `taken` hashes a whole scenario
    and an axis survey would multiply that cost by the number of axes and rungs.

    The caller gets `(scenario, operation, target, change)`, and `(None, reason, None, None)` when no
    rung can be offered - a four-element answer either way, because the callers unpack four.
    """
    axes = _value_axes(parent, only, roles, units)
    if not axes:
        return None, 'no value axis for this build', None, None
    start = int(offset) % len(axes)
    order = axes[start:] + axes[:start]
    for name, stat, current, low, high in order:
        stepped = _rung_step(parent, name, stat, current, low, high, rng, taken=taken,
                             direction=direction)
        if stepped is None:
            continue
        child, change = stepped
        if not community_admits(child, parent)[0]:
            # The rung moved a unit out of the community formation. Skip it rather than spend a run on a
            # build that is no longer the community strategy.
            continue
        return child, 'ladder', stat, change
    return None, 'every axis of this build is fully swept or already measured', None, None


def community_pair_child(parent, rng=None, taken=None, pair=None):
    """Stage 3: one child that moves **two** axes at once - the interaction probe.

    A ladder of single-axis steps cannot reach a compensating pair. If Attack up is worse on its own and
    Speed down is worse on its own, then no sequence of one-axis moves contains the combination that beat
    both - the combination is simply not in the space the search can propose. This is the generator that
    puts it there.

    It does not assert that any pair is good. It makes pairs *testable*, and records **both** axes, so the
    chest number is attached to the combination that produced it rather than to either axis on its own -
    which is the whole difference between "Attack 350 is best" and "Attack 350 is best when Speed is 300".

    `pair` names the two axes to test, so the pair *track* can walk every pair in turn instead of
    sampling two at random and calling the rest unknown. Without it the choice is random, which is what
    the untracked form did.
    """
    rng = rng or random.Random()
    axes = _value_axes(parent)
    by_key = {(name, stat): (name, stat, current, low, high)
              for name, stat, current, low, high in axes}
    if pair is not None:
        chosen_once = [by_key[key] for key in pair if key in by_key]
        if len(chosen_once) != 2:
            return None, f'{pair_label(pair)} is not available on this build', None, None
    else:
        if len(axes) < 2:
            return None, 'fewer than two value axes to pair', None, None
        chosen_once = None
    reasons = []
    for _ in range(VALUE_ATTEMPTS):
        chosen = chosen_once if chosen_once is not None else rng.sample(axes, 2)
        child, moved, stats = parent, [], []
        for name, stat, current, low, high in chosen:
            stepped = _rung_step(child, name, stat, current, low, high, rng)
            if stepped is None:
                break
            child, text = stepped
            moved.append(text)
            stats.append(stat)
        if len(moved) != 2:
            reasons.append('one of the pair had no settable rung')
            continue
        if not community_admits(child, parent)[0]:
            reasons.append('the pair left the community contract')
            continue
        if taken is not None and taken(child):
            reasons.append('already measured')
            continue
        return child, 'pair', '+'.join(stats), ' + '.join(moved)
    return None, '; '.join(reasons[-2:]) or 'no pair was available', None, None


def community_local_child(parent, rng=None, taken=None):
    """Stage 4: one child that moves **several** axes by a **small** step - the local neighbourhood.

    Coordinate descent misses a move whose parts are each individually unhelpful: Attack +20 alone worse,
    Speed -15 alone worse, and the two together better. Only a move that changes both at once can reach
    it, and a ladder rung is the wrong size for the question - the ladder spans the whole legal range in
    nine geometric steps, so it answers "which region", not "which point in this region".

    The step here is therefore a fixed small fraction of each axis's own current value, and the axes
    touched are chosen at random from the ones Student 1 is allowed to vary, so the neighbourhood is
    sampled rather than enumerated.
    """
    rng = rng or random.Random()
    axes = _value_axes(parent)
    if len(axes) < 2:
        return None, 'fewer than two value axes for a local move', None, None
    reasons = []
    for _ in range(VALUE_ATTEMPTS):
        count = min(len(axes), rng.randint(*LOCAL_AXES))
        chosen = rng.sample(axes, count)
        child, moved, stats = parent, [], []
        for name, stat, current, low, high in chosen:
            stepped = _local_step(child, name, stat, current, low, high, rng)
            if stepped is None:
                continue
            child, text = stepped
            moved.append(text)
            stats.append(stat)
        if len(moved) < 2:
            reasons.append('fewer than two axes could move locally')
            continue
        if not community_admits(child, parent)[0]:
            reasons.append('the local move left the community contract')
            continue
        if taken is not None and taken(child):
            reasons.append('already measured')
            continue
        return child, 'local', '+'.join(stats), ' + '.join(moved)
    return None, '; '.join(reasons[-2:]) or 'no local move was available', None, None


#: How many community candidates one encounter keeps in flight: **one per track**, so no line sits idle
#: while another spends the whole share, and every line's run count stays comparable. Small on purpose -
#: the point is that work is always available for the reserved share, not that the population is
#: dominated by one shape.
COMMUNITY_TRACK_WIDTH = len(DEFAULT_TRACKS)


def community_runnable(store, limits, ordinals, encounter_ids):
    """How many authorised, unfinished community seeds exist in the given scope."""
    owned = {cid for cid, source in student_of(store).items() if source == COMMUNITY_SOURCE}
    total = 0
    for cid in owned:
        if encounter_ids is not None and cid not in encounter_ids:
            continue
        limit = limits.get(cid)
        if limit is None:
            continue
        total += max(0, limit[0]-ordinals.get((cid, 'discovery'), 0))
        total += max(0, limit[1]-ordinals.get((cid, 'validation'), 0))
    return total


def track_plan(count, weights, proposal):
    """`[(track, n)]` - how many of `count` children each track gets, in table order.

    Largest remainder, with the leftover slot rotating on `proposal` so a track whose share rounds to
    zero still takes a regular turn instead of never being reached. A share of zero is honoured as
    zero: that is the user switching the track off, not a number to be rebalanced.
    """
    exact = {track.name: float(weights.get(track.name, 0.0))*int(count) for track in DEFAULT_TRACKS}
    whole = {name: int(value) for name, value in exact.items()}
    leftover = int(count) - sum(whole.values())
    live = [track for track in DEFAULT_TRACKS if weights.get(track.name, 0.0) > 0]
    if leftover > 0 and live:
        order = sorted(live, key=lambda track: (-(exact[track.name]-whole[track.name]), track.name))
        start = int(proposal or 0) % len(order)
        order = order[start:] + order[:start]
        for index in range(leftover):
            whole[order[index % len(order)].name] += 1
    return [(track, whole[track.name]) for track in DEFAULT_TRACKS]


#: A bounded cache for the measured working spans, keyed by library and encounter.
_SPAN_CACHE = {}
_SPAN_CACHE_LIMIT = 64


def measured_spans(store, encounter, minimum=1):
    """`{(unit, stat): (low, high)}` - the values this fight has actually **converted** with.

    "A range that works" means a range that earned something, so the span is taken from resident builds
    with at least one win rather than from everything that was merely tried. That distinction is the
    whole point of the dial: the ladder finds where a number was *tested*, this finds where it *paid*.

    Bounded and cached - one encounter's resident population, refreshed only when that population
    changes. The dial asks for this on every replenishment pass, and decoding every scenario each time
    would cost more than the draws it informs.
    """
    key = (str(getattr(store, 'path', '')), int(encounter))
    rows = store.db.execute(
        'SELECT c.id AS cid, c.scenario AS scenario FROM candidate c '
        'JOIN candidate_meta m ON m.id=c.id WHERE m.encounter=?', (int(encounter),)).fetchall()
    cached = _SPAN_CACHE.get(key)
    if cached is not None and cached[0] == len(rows):
        return cached[1]
    keys = [f'aggregate:{row[0]}:{phase}' for row in rows
            for phase in ('discovery', 'validation')]
    documents = {}
    if keys:
        placeholders = ','.join('?' for _ in keys)
        documents = {row[0]: row[1] for row in store.db.execute(
            f'SELECT key,value FROM meta WHERE key IN ({placeholders})', keys)}
    spans = {}
    for row in rows:
        wins = 0
        for phase in ('discovery', 'validation'):
            stored = documents.get(f'aggregate:{row[0]}:{phase}')
            if not stored:
                continue
            try:
                wins += int(json.loads(stored).get('wins') or 0)
            except (TypeError, ValueError):
                continue
        if wins < minimum:
            continue
        try:
            scenario = json.loads(row[1])
        except (TypeError, ValueError):
            continue
        for name, stat, current, _low, _high in _value_axes(scenario):
            low, high = spans.get((name, stat), (current, current))
            spans[(name, stat)] = (min(low, current), max(high, current))
    if len(_SPAN_CACHE) >= _SPAN_CACHE_LIMIT:
        _SPAN_CACHE.clear()
    _SPAN_CACHE[key] = (len(rows), spans)
    return spans


def _dial_step(scenario, name, stat, current, low, high, spans, rng):
    """One axis moved by a step sized to the range that is known to work. `(child, text)` or None."""
    span = (spans or {}).get((name, stat))
    if span:
        lo, hi = max(int(low), int(span[0])), min(int(high), int(span[1]))
    else:
        lo, hi = int(low), int(high)
    if hi <= lo:
        return None
    if span:
        # A step of one local fraction of the *working span*, so the dial moves in units of what this
        # axis has been shown to be sensitive to, not in units of the whole legal range.
        step = max(1, int(round((hi-lo)*LOCAL_STEP)))
    else:
        step = max(1, int(round(abs(current)*LOCAL_STEP)))
    targets = []
    for target in (current+step, current-step, hi, lo):
        target = max(lo, min(hi, int(target)))
        if target != current and target not in targets:
            targets.append(target)
    rng.shuffle(targets)
    for target in targets:
        try:
            child = contract.set_stat(scenario, name, stat, target)
        except (contract.ContractError, ValueError, KeyError, TypeError):
            continue
        return child, f'{stat} {current}->{target}'
    return None


def dial_child(parent, rng=None, taken=None, spans=None, only=None, roles=None, units=None):
    """The dial track: several axes at once, each landing inside a range this fight has converted in.

    Once the single-axis tracks have found where each number works, the useful question stops being "what
    is the best Attack" and becomes "if Attack goes up, where does Speed have to go". A move answering
    that has to change several axes together, and coordinate descent cannot reach it because neither half
    is an improvement on its own.

    The step is sized by the *working span*, so the dial moves in units that axis has been shown to
    respond to. With no measurement yet for an axis it falls back to the local step, which is the only
    scale available before anything is known.
    """
    rng = rng or random.Random()
    axes = _value_axes(parent, only, roles, units)
    if len(axes) < 2:
        return None, 'fewer than two value axes for a dial move', None, None
    reasons = []
    for _ in range(VALUE_ATTEMPTS):
        count = min(len(axes), rng.randint(*LOCAL_AXES))
        child, moved, stats = parent, [], []
        for name, stat, current, low, high in rng.sample(axes, count):
            stepped = _dial_step(child, name, stat, current, low, high, spans, rng)
            if stepped is None:
                continue
            child, text = stepped
            moved.append(text)
            stats.append(stat)
        if len(moved) < 2:
            reasons.append('fewer than two axes could move inside their ranges')
            continue
        if not community_admits(child, parent)[0]:
            reasons.append('the dial move left the community contract')
            continue
        if taken is not None and taken(child):
            reasons.append('already measured')
            continue
        return child, 'dial', '+'.join(stats), ' + '.join(moved)
    return None, '; '.join(reasons[-2:]) or 'no dial move was available', None, None


def track_child(parent, track, rng=None, taken=None, offset=0, spans=None):
    """One child from one track. `(scenario, operation, target, change)` or `(None, reason, None, None)`.

    Four elements either way, on every path. A two-element failure path is what made the community track
    raise and stop replenishing the first time a fight ran out of new value moves.
    """
    if track.kind == 'axis':
        return community_child(parent, rng, taken=taken, offset=offset, only=track.stats)
    if track.kind == 'pair':
        keys = pair_keys(parent)
        if not keys:
            return None, 'no pair of axes to test on this build', None, None
        pair = keys[int(offset) % len(keys)]
        child, operation, _target, change = community_pair_child(parent, rng, taken=taken, pair=pair)
        if child is None:
            # The pair could not move on this parent. Naming the pair in the refusal is what lets the
            # caller walk on to the next one instead of calling the whole track exhausted.
            return None, f'{pair_label(pair)}: {change}', None, None
        return child, operation, pair_label(pair), change
    if track.kind == 'dial':
        return dial_child(parent, rng, taken=taken, spans=spans)
    return None, f'unknown track kind {track.kind!r}', None, None


def replenish_track(store, encounter, track, count, proposal, stats, rng=None):
    """Create up to `count` children for **one track**. Returns their ids."""
    import random
    parent_id, parent = community_parent(store, encounter)
    if parent is None:
        return []
    rng = rng or random.Random(encounter*7919 + int(proposal or 0)
                               + sum(ord(char) for char in track.name))
    spans = measured_spans(store, encounter) if track.kind == 'dial' else None
    from strategy_optimizer import identity as _identity
    known = {row[0] for row in store.db.execute('SELECT id FROM candidate')}

    def taken(scenario):
        return _identity(scenario) in known

    created = []
    misses = 0
    for index in range(int(count)):
        # The cursor is carried by `proposal` so a sweep continues across passes instead of restarting
        # at the first axis or the first pair every time.
        child, operation, target, change = track_child(
            parent, track, rng, taken=taken, offset=int(proposal or 0)+index, spans=spans)
        if child is None:
            # A pair that cannot move is not the track being exhausted, so the walk continues; eight
            # consecutive misses across it means the neighbourhood really is saturated.
            misses += 1
            if misses >= 8:
                break
            continue
        label = f'Student 1 · {track.name} · {change}'[:160]
        cid, existed = store.add_child(child, label, lambda child=child: stats(child), parent_id,
                                       f'track:{track.name}', target, change, COMMUNITY_SOURCE,
                                       proposal)
        known.add(cid)
        if existed:
            misses += 1
            if misses >= 8:
                break
            continue
        misses = 0
        created.append(cid)
    return created


def track_runnable(store, limits, ordinals, encounter_ids, track=None):
    """How many authorised, unfinished seeds Student 1's tracks have in the given scope.

    One track's own count as well as all of them, so a track that has run dry can be topped up without
    refilling the ones that have not.
    """
    owned = {cid for cid, source in student_of(store).items() if source == COMMUNITY_SOURCE}
    if track is not None:
        owned &= {row[0] for row in store.db.execute(
            'SELECT candidate FROM lineage WHERE operation=?', (f'track:{track}',))}
    total = 0
    for cid in owned:
        if encounter_ids is not None and cid not in encounter_ids:
            continue
        limit = limits.get(cid)
        if limit is None:
            continue
        total += max(0, limit[0]-ordinals.get((cid, 'discovery'), 0))
        total += max(0, limit[1]-ordinals.get((cid, 'validation'), 0))
    return total


def pair_coverage(store, encounter, parent=None):
    """Which pairs of axes the pair track has tested on this fight, and which it still owes.

    "It has to know the relationship of every pair" is a coverage claim, and this readout is what makes
    it falsifiable: the pairs are enumerated from the build and the ones actually recorded in the lineage
    are subtracted. A generator that sampled two axes at random could never answer this question, which
    is the reason the pair track walks an enumerated list rather than drawing.
    """
    if parent is None:
        _parent_id, parent = community_parent(store, encounter)
    if parent is None:
        return dict(all=[], tested=[], owed=[])
    wanted = [pair_label(pair) for pair in pair_keys(parent)]
    tested = {row[0] for row in store.db.execute(
        "SELECT DISTINCT target FROM lineage WHERE operation='track:pair' AND encounterId=?",
        (int(encounter),))}
    known = [label for label in wanted if label in tested]
    return dict(all=wanted, tested=known, owed=[label for label in wanted if label not in tested])


def track_report(store, encounter=None):
    """`{track: {candidates, runs}}` - what each of Student 1's lines has actually been given.

    The point of tracking the split is that "30% went to Student 1" hides which sub-division spent it,
    and a stalled axis looks exactly like one that has been sweeping for hours.
    """
    owned = student_of(store)
    report = {}
    for row in store.db.execute(
            "SELECT candidate, operation, encounterId FROM lineage WHERE operation LIKE 'track:%'"):
        if owned.get(row[0]) != COMMUNITY_SOURCE:
            continue
        # `row[2]` is the encounter id, and encounter 0 is a real encounter - `row[2] or -1` would read
        # it as "missing" and silently drop every fight whose id is zero.
        stored_encounter = None if row[2] is None else int(row[2])
        if encounter is not None and stored_encounter != int(encounter):
            continue
        entry = report.setdefault(str(row[1]).split(':', 1)[1], dict(candidates=0, runs=0))
        entry['candidates'] += 1
        entry['runs'] += _measure(store, row[0])[1]
    return report


def replenish_community(store, encounter, count, proposal, stats, rng=None, shares=None):
    """Create up to `count` value children of the encounter's community parent. Returns their ids.

    Called once per planning pass for the encounters in scope, so the reserved share always has something
    to dispatch. A failure is not fatal and never blocks the ordinary search: if no value move can preserve
    the contract this returns an empty list and the pass carries on.

    **The budget is divided across the tracks by their shares**, not cycled through anonymous generators.
    That is the difference between "the search sometimes changes two values" and "there is a line whose
    entire job is the relationship between two values, and it has a budget the user can see and set".
    """
    import random
    rng = rng or random.Random(encounter*7919 + int(proposal or 0))
    created = []
    # A fight with nothing of its own gets a transfer first: "this worked for me, try it here". The
    # values are borrowed from the best-measured community build on another encounter and applied to
    # *this* fight's cast, so the shape stays this fight's shape and only the numbers travel. On a fight
    # that already has measured community evidence the transfer is skipped, because there the climb
    # starts from its own best.
    if not _has_measured(store, encounter):
        borrowed = transfer_seed(store, encounter, proposal, stats, rng)
        if borrowed:
            created.append(borrowed)
    for track, share in track_plan(count, track_shares(shares), proposal):
        if share <= 0:
            continue
        created.extend(replenish_track(store, encounter, track, share, proposal, stats, rng))
    return created


#: Rungs per axis, counting both endpoints. Nine gives a near/mid/far reading in each direction without
#: spending the sweep inside the baseline's own neighbourhood.
LADDER_RUNGS = 9


def ladder_values(low, high, baseline, rungs=LADDER_RUNGS):
    """Even rungs across one axis's legal span, ordered by how far each moves from the baseline.

    **Geometric, not arithmetic.** The legal spans run into the thousands (Defence reaches 4,976), so an
    arithmetic ladder of even steps would spend the whole sweep inside the baseline's neighbourhood and
    never reach the far end - which is precisely where the answer lives when a fight wants a much weaker
    build.

    **Ordered outward from the baseline, in both directions from the start.** This is the part that makes
    "keep everything low" reachable. A climb that always restarts from its current best only ever
    reinforces what already worked, and going up is what works first; the low region falls behind it and
    is never revisited. Testing below the baseline as an equal, first-class direction - against the same
    frozen base - is what turns "deliberately weaker is better" into a bracket instead of an anecdote.
    """
    import math
    low, high, baseline = int(low), int(high), int(baseline)
    rungs = max(3, int(rungs))
    lo = max(1, low)
    if high <= lo:
        return []
    span = math.log(high/lo)
    values = {int(round(lo*math.exp(span*index/(rungs-1)))) for index in range(rungs)}
    # The baseline is its own rung: it is the zero point every other rung is read against, and its
    # measurement already exists, so it is excluded from the walk rather than re-proposed.
    values.add(min(max(baseline, low), high))
    ordered = sorted(value for value in values if low <= value <= high)
    reference = math.log(max(1, min(max(baseline, 1), high)))
    return sorted((value for value in ordered if value != baseline),
                  key=lambda value: (abs(math.log(max(1, value)) - reference), value))


# ---------------------------------------------------------------------------------------------
# Student 2: the rebellion tiers, and the ledger of what each one broke
# ---------------------------------------------------------------------------------------------
#
# Student 2 is not a second copy of Student 1. It starts from a current **community best** - the
# working formation is stolen wholesale - and then breaks a declared subset of the numbered rules.
# What it is disciplined about is *attribution*, not restriction: nothing forbids it a class or a
# placement, because those are exactly what it exists to change. What is required is that every
# candidate records the community build it came from and the set of rules it broke relative to it.
#
# That is what makes a tier mean something. A tier is a **distance**: how many rules may be broken at
# once. A result then reads as "breaking C4 earned 6 chests" or "breaking C2 and C5 cost 12", and the
# number is a paired comparison against the same parent, on the same encounter, on the same seeds.


class Tier(namedtuple('Tier', 'name rules minimum maximum')):
    """One rebellion distance: which rules it may break, and how many at once.

    `rules` is the tier's allowed set and `minimum`/`maximum` its size window. Both are part of the
    tier because they answer different questions: the set says *what* the tier may touch, the window
    says *how far* it stands from the community strategy. A window of 1 is the fine probe around
    Student 1's answer; a window of 4-7 is the far end.

    Tiers are **parallel and permanent**. They never retire and they never hand over: after a hundred
    thousand runs T1 is still making one-rule changes from a working build, because that is its entire
    job. A tier's failure is never another tier's evidence, so no tier prunes another tier's space.
    """

    __slots__ = ()

    def takes(self, breaks):
        """True when a break set belongs here: inside the allowed set and inside the size window."""
        if not set(breaks) <= set(self.rules):
            return False
        return self.minimum <= len(breaks) <= self.maximum

    @property
    def span(self):
        """How far from the community strategy this tier stands, as a size window."""
        return (self.minimum, self.maximum)


#: The tier table, and the default the Settings surface edits.
#:
#: T1 breaks one rule, T2 two, T3 three, and T4 all but one - which covers four and five as well, so
#: nothing is needed above it. "All but one" is derived from the rule count rather than written as a
#: literal, so adding a rule later widens T4 automatically instead of leaving a distance no tier can
#: reach.
DEFAULT_TIERS = (
    Tier('T1', RULE_IDS, 1, 1),
    Tier('T2', RULE_IDS, 2, 2),
    Tier('T3', RULE_IDS, 3, 3),
    Tier('T4', RULE_IDS, 4, max(4, len(RULE_IDS) - 1)),
)


def tiers(overrides=None):
    """The tier table, with any per-tier rule override applied.

    Refuses a tier that may break nothing, for the reason the plan gives: a rebel that may break nothing
    is Student 1, and a tier with no rule is not a distance. An override may only narrow a tier to rules
    that exist.
    """
    table = {tier.name: tier for tier in DEFAULT_TIERS}
    for name, rules in (overrides or {}).items():
        if name not in table:
            raise ValueError(f'unknown tier {name!r}; expected one of {sorted(table)}')
        rules = tuple(rules)
        unknown = [rule for rule in rules if rule not in RULE_TITLES]
        if unknown:
            raise ValueError(f'{name}: unknown rule(s) {unknown}; expected {RULE_IDS}')
        if not rules:
            raise ValueError(f'{name}: a tier must be allowed to break at least one rule')
        current = table[name]
        top = max(current.minimum, len(rules))
        table[name] = Tier(name, rules, min(current.minimum, top), min(current.maximum, top))
    return tuple(table[tier.name] for tier in DEFAULT_TIERS)


#: How many draws one child gets before the neighbourhood is called saturated for this tier. Each draw
#: that misses costs a rule evaluation, so this is a real cost knob rather than a nicety.
REBEL_ATTEMPTS = 16

#: How many rebel candidates one encounter keeps in flight, per tier. Small on purpose: the point is
#: that every tier always has something ready to dispatch, not that the population is dominated by
#: rebels. A rebel draw costs several times a value draw because it has to check what it broke.
REBEL_TRACK_WIDTH = 2


def _by_index(scenario):
    """`{placement index: unit}` in the derived placement."""
    return {int(row['grid']): row['unit'] for row in _placed_rows(scenario)}


def _usable_skills(scenario, unit, *, healing=False):
    """Approved skills this unit could legally take, in id order. Formation skills are never included."""
    carried = set(unit.get('skills') or ())
    usable = []
    for skill_id in sorted(contract.whitelist()):
        if skill_id in carried or contract.is_formation_skill(skill_id):
            continue
        if (skill_id in HEALING_SKILLS) != bool(healing):
            continue
        required = int(contract.skill_row(skill_id).get('requiredEquipType') or -1)
        if required != -1:
            try:
                if contract.weapon_type_of(scenario, unit) != required:
                    continue
            except (KeyError, TypeError, ValueError, contract.ContractError):
                continue
        usable.append(skill_id)
    return usable


def _break_moves(rule, scenario, parent, rng):
    """`[callable, ...]` - moves that set out to break `rule`, each returning a child or raising.

    One menu entry per *move* rather than one per rule, because the same rule is reachable several ways
    and the point of the machinery is a sample over them: the player who raises the fodder's HP and the
    one who raises its Defence broke the same rule, and both have to be reachable.

    A rule with no move is not an error. **C6 cannot be broken at all today** - the herb branch needs
    the new library (plan §9) - so a tier that permits C6 has nothing to draw on rather than a broken
    menu, and `rebel_child` reports that instead of quietly substituting a different rule.
    """
    units = _by_index(scenario)
    dps = units.get(0) or {}
    healer = units.get(5) or {}
    fodder = [units.get(index) or {} for index in FODDER_INDICES]
    dps_name = dps.get('name')
    moves = []

    if rule == 'C1':
        # A second healer as a seventh unit, or a five-unit team. Either way C1 is the only rule that
        # moves: with no sixth-unit placement to compare against, C2-C5 and C8 report as not applicable
        # rather than as extra breaks, which is why seven units read as one broken rule and not six.
        if dps_name:
            moves.append(lambda: contract.add_unit(scenario, dps_name))
        for unit in fodder:
            if unit.get('name'):
                moves.append(lambda unit=unit: contract.remove_unit(scenario, unit['name']))

    elif rule == 'C2':
        # Placement is derived, not authored: it sorts by (priority, -effective defence, incoming
        # index), so C2 is broken by moving a unit's Defence past another's or by changing which unit
        # carries the formation skill. The rule is about the result, not about which gene caused it.
        if dps_name and any(unit for unit in fodder):
            floor = min((int(contract.battle_value(scenario, unit, 14) or 0)
                         for unit in fodder if unit), default=0)
            moves.append(lambda: contract.set_stat(scenario, dps_name, 'def', max(3, floor - 1)))
            dps_defence = int(contract.battle_value(scenario, dps, 14) or 0)
            for unit in fodder:
                if unit.get('name'):
                    moves.append(lambda unit=unit: contract.set_stat(
                        scenario, unit['name'], 'def', dps_defence + 1))
            for skill_id in sorted(FORMATION_SKILLS):
                moves.append(lambda skill_id=skill_id: contract.set_formation_skill(
                    scenario, dps_name, skill_id))

    elif rule == 'C3' and dps_name:
        for skill_id in _usable_skills(scenario, dps, healing=True):
            moves.append(lambda skill_id=skill_id: contract.add_skill(
                scenario, dps_name, skill_id, trigger=1))

    elif rule == 'C4' and healer.get('name'):
        for skill_id in _usable_skills(scenario, healer):
            moves.append(lambda skill_id=skill_id: contract.add_skill(
                scenario, healer['name'], skill_id, trigger=1))

    elif rule == 'C5':
        for unit in fodder:
            if not unit.get('name'):
                continue
            for skill_id in _usable_skills(scenario, unit):
                moves.append(lambda unit=unit, skill_id=skill_id: contract.add_skill(
                    scenario, unit['name'], skill_id, trigger=1))

    elif rule == 'C7' and dps_name:
        # The DPS is the unit whose skills can change without its role changing, which is what makes a
        # single-rule C7 break possible at all: give it another approved attack, take one of its skills
        # away, or swap one for another. "Use a disallowed skill" and "remove a crucial skill" both land
        # here, which is exactly why they share a number.
        for skill_id in _usable_skills(scenario, dps):
            moves.append(lambda skill_id=skill_id: contract.add_skill(
                scenario, dps_name, skill_id, trigger=1))
        for index, skill_id in enumerate(list(dps.get('skills') or ())):
            if contract.is_formation_skill(skill_id):
                continue
            moves.append(lambda index=index: contract.remove_skill(scenario, dps_name, index))
            for replacement in _usable_skills(scenario, dps):
                moves.append(lambda index=index, replacement=replacement: contract.replace_skill(
                    scenario, dps_name, index, replacement, trigger=1))

    elif rule == 'C8':
        # Thicker fodder, which Student 1 never makes: a tougher fodder is a rebellion against the
        # shape rather than a value within it, so `community_child` skips the fodder on every axis.
        hp_ceiling, def_ceiling = fodder_ceiling(parent)
        dps_defence = int(contract.battle_value(scenario, dps, 14) or 0) if dps else 0
        for unit in fodder:
            if not unit.get('name'):
                continue
            name = unit['name']
            moves.append(lambda name=name: contract.set_stat(scenario, name, 'hp', hp_ceiling + 1))
            moves.append(lambda name=name: contract.set_stat(scenario, name, 'hp', hp_ceiling * 2))
            if dps_defence > def_ceiling + 1:
                # Staying under the DPS's Defence means the fodder is tougher *without* the derived
                # placement moving it: one rule broken, not two. Above that line it is a C2 break as
                # well, and the ledger says both, because both really happened.
                moves.append(lambda name=name: contract.set_stat(
                    scenario, name, 'def', def_ceiling + 1))
                moves.append(lambda name=name: contract.set_stat(
                    scenario, name, 'def', (def_ceiling + dps_defence) // 2))

    return moves


def _break_plan(tier, parent, rng):
    """Which rules one attempt sets out to break: a sample of the tier's set, of the tier's size.

    Two rules are taken out of the pool before the draw, for reasons that are properties of the rule set
    rather than of this particular build:

      * **C6** has no move at all until the herb branch exists (§9 of the plan), so drawing it only
        wastes an attempt. A tier that permits C6 still permits it - it simply has nothing to draw on,
        which is a fact about the herb library rather than about the tier.
      * **C1 cannot be combined with anything.** A roster that is not six units long has no community
        placement to compare against, so C2-C5 and C8 report as not applicable and the attempt breaks
        exactly one rule however many it set out to break. C1 is therefore a legal *single*-rule draw
        and never a legal multi-rule one, and drawing it alongside others could only fail.
    """
    pool = [rule for rule in tier.rules if rule != 'C6']
    if not pool:
        # Every rule this tier permits is one nothing can break yet - the herb rule alone, today.
        return []
    count = max(1, min(rng.randint(tier.minimum, tier.maximum), len(pool)))
    if count > 1:
        pool = [rule for rule in pool if rule != 'C1']
        count = min(count, len(pool))
    return rng.sample(pool, count)


def _apply_move(moves, rng):
    """The first move in `moves` that produces a legal child, or None."""
    rng.shuffle(moves)
    for move in moves:
        try:
            child = move()
        except (contract.ContractError, ValueError, KeyError, TypeError):
            continue
        if child is not None:
            return child
    return None


def rebel_child(parent, tier, rng=None, taken=None):
    """One child of a rebel tier. `(scenario, [rules broken])` or `(None, why not)`.

    The whole discipline lives here: a child is returned only when the rules it **actually** broke lie
    inside the tier - inside its allowed set and inside its size window. A T1 attempt that ends up
    breaking two rules is refused, not relabelled T2, because a result is evidence about the combination
    it tested and nothing else. Refusing it here is what stops it being spent as evidence for a
    distance it does not stand at.

    `taken(scenario)` lets the caller skip builds the library already holds, so a saturated
    neighbourhood stops spending draws instead of re-proposing one build forever.
    """
    import random
    rng = rng or random.Random()
    if not tier.rules:
        return None, f'{tier.name} may break nothing, so it is Student 1'
    reasons = []
    for _ in range(REBEL_ATTEMPTS):
        plan = _break_plan(tier, parent, rng)
        if not plan:
            # Every rule this tier permits is one nothing can break yet - the herb rule alone, today.
            return None, (f'{tier.name} permits only {", ".join(tier.rules)}, which nothing can break '
                          'yet: the herb branch needs the new library')
        scenario = parent
        dead = False
        for rule in plan:
            moved = _apply_move(_break_moves(rule, scenario, parent, rng), rng)
            if moved is None:
                reasons.append(f'{rule} has no legal move from this build')
                dead = True
                break
            scenario = moved
        if dead:
            continue
        breaks = rule_breaks(scenario, parent)
        if not breaks:
            reasons.append('the attempt left every rule intact')
            continue
        outside = sorted(breaks - set(tier.rules))
        if outside:
            reasons.append(f'broke {outside}, outside {tier.name} rules')
            continue
        if not tier.minimum <= len(breaks) <= tier.maximum:
            reasons.append(f'broke {len(breaks)} rules, {tier.name} takes '
                           f'{tier.minimum}-{tier.maximum}')
            continue
        if taken is not None and taken(scenario):
            reasons.append('already measured')
            continue
        return scenario, sorted(breaks)
    return None, '; '.join(reasons[-3:]) or 'no rebel move produced an admitted break'


# ---------------------------------------------------------------------------------------------
# Student 2's ledger: what each tier broke, what it cost, and which version found it
# ---------------------------------------------------------------------------------------------
#
# The ledger lives in its own additive table rather than in `lineage`, for the same reason
# `strategy_evidence` does: `lineage` is written for every generated child by every part of the search,
# and a library written before this module must read back unchanged. `CREATE TABLE IF NOT EXISTS` is a
# no-op on an existing library, and a candidate with no ledger row is simply one no tier proposed.


def store_schema(connection):
    """Create the rebel ledger table. Idempotent, and additive to any existing library."""
    connection.executescript('''
        CREATE TABLE IF NOT EXISTS rebel_break(
            candidate TEXT PRIMARY KEY,
            tier TEXT NOT NULL,
            version INTEGER NOT NULL,
            broken TEXT NOT NULL,
            encounter INTEGER,
            proposal INTEGER,
            created INTEGER NOT NULL);
        CREATE INDEX IF NOT EXISTS rebel_break_tier ON rebel_break(tier, encounter);
    ''')


def record_rebel(connection, candidate, tier, version, broken, encounter, proposal, created):
    """One ledger row: this candidate is tier/version, and these are the rules it broke."""
    connection.execute(
        'INSERT OR REPLACE INTO rebel_break VALUES (?,?,?,?,?,?,?)',
        (candidate, str(tier), int(version), json.dumps(sorted(broken)),
         None if encounter is None else int(encounter), int(proposal or 0), int(created)))


def rebel_rows(store, encounter=None, tier=None):
    """The ledger's rows, filtered. Empty when the library predates the table or no tier has run."""
    sql = 'SELECT candidate, tier, version, broken, encounter, proposal, created FROM rebel_break'
    clauses, args = [], []
    if encounter is not None:
        clauses.append('encounter=?')
        args.append(int(encounter))
    if tier is not None:
        clauses.append('tier=?')
        args.append(str(tier))
    if clauses:
        sql += ' WHERE ' + ' AND '.join(clauses)
    try:
        rows = store.db.execute(sql, tuple(args)).fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(row) for row in rows]


def rebel_parent(store, encounter, tier):
    """`(candidate_id, scenario, version)` - the build this tier's next version starts from.

    **A tier's versions anchor on that tier's own best, never on another tier's.** Version 1 starts from
    the community parent, because a rebellion is measured against the community strategy; version 2
    starts from whatever T1.v1 actually produced; and so on. Anchoring every tier on Student 1 would
    drag the far tiers back into the near tier's territory and make the tiers incomparable however long
    they ran.

    Ranking is the same evidence order `community_parent` uses - mean chests first, because a build that
    pays every time beats one that paid once - so a version only extends when the version before it
    actually produced something better.
    """
    best = None
    for row in rebel_rows(store, encounter=encounter, tier=tier.name):
        cid = row['candidate']
        stored = store.db.execute('SELECT scenario FROM candidate WHERE id=?', (cid,)).fetchone()
        if not stored:
            continue
        scenario = json.loads(stored['scenario'])
        mean, samples, best_run, potential = _measure(store, cid)
        key = (mean is not None, mean or 0.0, samples, best_run, potential, int(row['version']))
        if best is None or key > best[0]:
            best = (key, cid, scenario, int(row['version']))
    if best is not None:
        return best[1], best[2], best[3] + 1
    parent_id, parent = community_parent(store, encounter)
    return parent_id, parent, 1


def rebel_runnable(store, limits, ordinals, encounter_ids, tier=None):
    """How many authorised, unfinished rebel seeds exist in the given scope, optionally one tier's."""
    owned = {cid for cid, source in student_of(store).items() if source == STUDENT_REBEL}
    if tier is not None:
        owned &= {row['candidate'] for row in rebel_rows(store, tier=tier)}
    total = 0
    for cid in owned:
        if encounter_ids is not None and cid not in encounter_ids:
            continue
        limit = limits.get(cid)
        if limit is None:
            continue
        total += max(0, limit[0]-ordinals.get((cid, 'discovery'), 0))
        total += max(0, limit[1]-ordinals.get((cid, 'validation'), 0))
    return total


def replenish_rebels(store, encounter, tier, count, proposal, stats, rng=None):
    """Create up to `count` children of this encounter's current parent at this tier.

    Called once per planning pass, so the rebel share always has something to dispatch. A failure is not
    fatal and never blocks the ordinary search: if no move can produce a legal break at this tier this
    returns an empty list and the pass carries on.
    """
    import random
    import time
    parent_id, parent, version = rebel_parent(store, encounter, tier)
    if parent is None:
        return []
    rng = rng or random.Random(encounter*7919 + int(proposal or 0)
                               + sum(ord(char) for char in tier.name))
    from strategy_optimizer import identity as _identity
    known = {row[0] for row in store.db.execute('SELECT id FROM candidate')}

    def taken(scenario):
        return _identity(scenario) in known

    created = []
    misses = 0
    for _ in range(int(count)):
        child, breaks = rebel_child(parent, tier, rng, taken=taken)
        if child is None:
            break
        label = f'Rebel {tier.name}.v{version} · broke {", ".join(breaks)}'[:160]
        cid, existed = store.add_child(child, label, lambda child=child: stats(child), parent_id,
                                       'rebel', tier.name, f'broke {", ".join(breaks)}',
                                       STUDENT_REBEL, proposal)
        known.add(cid)
        if existed:
            misses += 1
            if misses >= 8:
                break
            continue
        misses = 0
        record_rebel(store.db, cid, tier.name, version, breaks, encounter, proposal, time.time_ns())
        created.append(cid)
    return created


def rebel_report(store, encounter=None):
    """The rule-break ledger: one line per tier, version and break set actually tested.

    This is the readout the whole student exists to produce. It answers *which community rules are
    load-bearing and which are superstition* from the other side: a tier that broke exactly C5 and
    gained nothing is evidence that C5 matters; one that broke C2+C5 and gained says the pair does
    something neither does alone.

    Grouping is by the **exact** break set rather than by rule, because a result is only interpretable
    against the combination it tested - "broke C2" and "broke C2+C5" are different experiments.
    """
    owned = student_of(store)
    ledger = {}
    for row in rebel_rows(store, encounter=encounter):
        cid = row['candidate']
        if owned.get(cid) != STUDENT_REBEL:
            continue
        try:
            broken = json.loads(row['broken'])
        except (TypeError, ValueError):
            broken = []
        entry = ledger.setdefault(f"{row['tier']}.v{int(row['version'])}", {})
        bucket = entry.setdefault('+'.join(sorted(broken)) or 'nothing',
                                  dict(candidates=0, runs=0))
        mean, samples, _best, _potential = _measure(store, cid)
        bucket['candidates'] += 1
        bucket['runs'] += samples
        if mean is not None:
            best = bucket.get('bestMeanChests')
            bucket['bestMeanChests'] = mean if best is None else max(best, mean)
    for entry in ledger.values():
        for bucket in entry.values():
            if 'bestMeanChests' in bucket:
                bucket['bestMeanChests'] = round(bucket['bestMeanChests'], 3)
    return ledger


# ---------------------------------------------------------------------------------------------
# The average student: raise the best RELIABLE mean earned, and never chase a record
# ---------------------------------------------------------------------------------------------
#
# The problem it exists for, in the user's own numbers from one encounter: a build that reached 28
# chests once and averages **1.92 over 2,156 runs**, another at 24 averaging 1.90 over ~1,400, another
# at 22 averaging 1.6-2.0 over 1,000+. Meanwhile a different build that never exceeded ~9-12 on a single
# run holds the encounter's best mean at 3.09. Best earned ranks the first three above the last one.
# Best earned is therefore *wrong* here, and this student does not read it.
#
# Mean earned and best earned are different questions and the student treats them that way: a rare high
# run is not evidence of a good strategy when thousands of runs show a poor mean, and a mean is not
# evidence either until enough runs stand behind it. What it maximises is the highest mean that is
# *statistically credible*.

#: The store's key for what each fight has achieved and how long it has stood still. Persisted so a
#: restart does not make the student re-learn that a direction pays nothing.
AVERAGE_PROGRESS_KEY = 'averageProgress'

#: Runs a mean needs before the student will credit it as the fight's best. Below this a build may still
#: be *validated* - that is a different job - but it is never the incumbent.
AVERAGE_MIN_SAMPLES = 64

#: Runs below which a high-mean build is treated as unproven rather than merely new.
AVERAGE_VALIDATE_MIN = 24

#: Consecutive passes the fight's best credible mean may stand still before the student stops refining
#: and broadens instead.
AVERAGE_STAGNATION_PASSES = 40


def reliable_mean(entry, minimum=AVERAGE_MIN_SAMPLES):
    """The mean this build can actually be *credited* with: its own mean less its own uncertainty.

    `mean - 1.96 * sd / sqrt(n)` - the same penalty `strategy_mean_parents` already applies, so the two
    agree about what "reliable" means. It is `None` until enough runs stand behind it, because a mean
    without a sample is not a reading: that is precisely the 12-run build that used to top the board.

    The two effects are what the user asked for. A build with 2,000 runs and a mean of 1.92 has a tight
    interval around a poor number and will never lead. A build with 12 runs and a mean of 3.09 has a wide
    interval that reaches down to nothing, so it cannot lead either - but its *point* mean still marks it
    as worth testing, which is the next function's job.
    """
    samples = int(entry.get('samples') or 0)
    mean, sd = entry.get('mean'), entry.get('sd')
    if mean is None or sd is None or samples < minimum:
        return None
    return mean - 1.96*sd/math.sqrt(samples)


def _mean_records(store, encounter):
    """`[{candidate, scenario, mean, sd, samples, best}]` for every resident build of one fight.

    Read from the aggregate counters, which the store maintains inside the run's own transaction, so no
    run row is ever decoded. The two phase documents for the *whole fight* are fetched in one query
    rather than one query per candidate: the five-minute trace showed 253,404 individual
    `SELECT value FROM meta WHERE key=?` statements in five minutes, ~780 a second, and this function
    called once per encounter per pass was the largest single source of them.
    """
    rows = store.db.execute(
        'SELECT c.id AS cid, c.scenario AS scenario FROM candidate c '
        'JOIN candidate_meta m ON m.id=c.id WHERE m.encounter=?', (int(encounter),)).fetchall()
    keys = [f'aggregate:{row[0]}:{phase}' for row in rows
            for phase in ('discovery', 'validation')]
    documents = {}
    if keys:
        placeholders = ','.join('?' for _ in keys)
        documents = {row[0]: row[1] for row in store.db.execute(
            f'SELECT key,value FROM meta WHERE key IN ({placeholders})', keys)}
    out = []
    for row in rows:
        cid = row[0]
        samples, total, total2, best = 0, 0.0, 0.0, 0
        for phase in ('discovery', 'validation'):
            stored = documents.get(f'aggregate:{cid}:{phase}')
            if not stored:
                continue
            try:
                data = json.loads(stored)
            except (TypeError, ValueError):
                continue
            count = int(data.get('chestCount') or 0)
            samples += count
            total += float(data.get('chestSum') or 0.0)
            total2 += float(data.get('chestSum2') or 0.0)
            best = max(best, int(data.get('earnedMax') or 0))
        if samples < 1:
            continue
        try:
            scenario = json.loads(row[1])
        except (TypeError, ValueError):
            continue
        mean = total/samples
        sd = (math.sqrt(max(0.0, (total2 - total*total/samples)/(samples-1)))
              if samples > 1 else None)
        out.append(dict(candidate=cid, scenario=scenario, mean=mean, sd=sd, samples=samples,
                        best=best))
    return out


def average_readings(store, encounter):
    """`(best, entries)` - the fight's best credible mean and every resident build's reading.

    `best` is the highest `reliable_mean` on the fight, or None when nothing has been measured enough to
    be credited. `entries` carries both the mean and the best single run for each build, because the
    report has to show the contrast that made this student necessary.
    """
    entries = _mean_records(store, encounter)
    for entry in entries:
        entry['reliable'] = reliable_mean(entry)
    ranked = [entry for entry in entries if entry['reliable'] is not None]
    best = max(ranked, key=lambda entry: (entry['reliable'], entry['samples'])) if ranked else None
    return best, entries


def average_parent(store, encounter):
    """`(candidate_id, scenario, why)` - what the average student works on next.

    Two jobs, in the order the user set, and neither of them looks at best earned:

      1. **Validate.** A build whose *point* mean beats the fight's best credible mean but whose sample is
         still too thin to be credited gets the next runs. The student is not chasing that number, it is
         testing whether the number survives - which is exactly the question a single big run cannot
         answer and a mean can.
      2. **Refine.** Otherwise the build with the best credible mean.

    A fight where nothing is measured well enough yet has no parent: it returns None and the caller
    carries on, rather than inventing an incumbent from a twelve-run reading.
    """
    best, entries = average_readings(store, encounter)
    if not entries:
        return None, None, 'nothing measured on this fight yet'
    ceiling = best['reliable'] if best else float('-inf')
    thin = [entry for entry in entries
            if entry['samples'] < AVERAGE_MIN_SAMPLES
            and entry['samples'] >= AVERAGE_VALIDATE_MIN
            and entry['mean'] > ceiling]
    if thin:
        pick = max(thin, key=lambda entry: (entry['mean'], entry['samples']))
        # `best` can be None here: a fight whose builds are all still below the credibility minimum has
        # a ceiling of -infinity, so any 24+-run build with a positive mean is "thin" and there is no
        # incumbent to name. Formatting `best["samples"]` in that state raised, and the caller's guard
        # turned a startup edge case into a silent `averageError`. The reason says what is true instead.
        against = (f'{ceiling:.2f} over {best["samples"]} runs' if best else
                   'no credible mean yet')
        return (pick['candidate'], pick['scenario'],
                f'validate mean {pick["mean"]:.2f} over {pick["samples"]} runs against {against}')
    if best is None:
        # Nothing is credible yet, so the best partially-measured mean is what to keep measuring.
        pick = max((entry for entry in entries if entry['samples'] >= AVERAGE_VALIDATE_MIN),
                   key=lambda entry: (entry['mean'], entry['samples']), default=None)
        if pick is None:
            return None, None, 'nothing measured enough to mean anything yet'
        return (pick['candidate'], pick['scenario'],
                f'keep measuring the best partial mean {pick["mean"]:.2f} over '
                f'{pick["samples"]} runs')
    return best['candidate'], best['scenario'], (
        f'refine the best credible mean {best["reliable"]:.2f} '
        f'(mean {best["mean"]:.2f} over {best["samples"]} runs)')


#: Per-candidate validation banks the average student has authorised beyond the ordinary staged policy.
#: Same shape and purpose as `probeBanks`: a candidate in this map is measured to its own declared bank.
#: This is what makes "validate" a *scheduled run of the parent* rather than another mutation of it, and
#: what lets a promising candidate continue past 64 runs instead of being capped by the screening bank.
AVERAGE_BANKS_KEY = 'averageBanks'
#: Average-owned builds whose outstanding request was retired because its only purpose was the
#: disallowed support-minimum tuning. They keep their identity, lineage and evidence; only the request
#: is withdrawn. Recorded here so a restart does not resurrect it and the surface can say why.
AVERAGE_RETIRED_KEY = 'averageRetired'
#: The last decision this student made, for the surface: which parent, which operation, and why.
AVERAGE_PLAN_KEY = 'averagePlan'


def average_banks(store):
    """`{candidate id: validation bank}` the average student has authorised."""
    return dict(store.get(AVERAGE_BANKS_KEY) or {})


def average_progress(store, encounter, best_now, point_now=None, completed=None,
                     limit=AVERAGE_STAGNATION_PASSES):
    """`(credited best, is it stalled)` - and persist both, so a restart does not reset the clock.

    The user's stagnation rule: when many adequately-tested variants cluster around the same best mean,
    stop making tiny variations in that region. A tiny variation is cheap and usually harmless, but a
    hundred thousand of them in a region already measured to 1.92 is the thing this exists to interrupt.

    Two corrections matter here. A pass counts only when *finished work* arrived since the last one
    (`completed`, the number of scored runs the student's own candidates have produced) - the scheduler
    visiting this fight more often than a battle completes is not a search pass. And the clock resets on
    the measured *point* mean rising, not on the reliable mean tightening: a credible best whose interval
    narrows because more of the same runs arrived has not found anything new, and treating that as
    progress is how a stalled fight keeps its clock at zero forever.
    """
    state = dict(store.get(AVERAGE_PROGRESS_KEY) or {})
    key = str(int(encounter))
    entry = dict(state.get(key) or {'best': None, 'passes': 0})
    value = None if best_now is None else round(float(best_now), 6)
    point = value if point_now is None else (None if point_now is None else round(float(point_now), 6))
    if point is not None and (entry.get('point') is None or point > float(entry['point'])):
        entry = dict(best=value, point=point, passes=0, completed=entry.get('completed'))
    else:
        if completed is None or entry.get('completed') is None \
                or int(completed) > int(entry['completed']):
            entry['passes'] = int(entry.get('passes') or 0) + 1
        entry['best'] = value if value is not None else entry.get('best')
        entry['point'] = point if point is not None else entry.get('point')
    if completed is not None:
        entry['completed'] = int(completed)
    state[key] = entry
    if state != (store.get(AVERAGE_PROGRESS_KEY) or {}):
        with store.db:
            store.set(AVERAGE_PROGRESS_KEY, state)
    return entry.get('best'), int(entry['passes']) >= int(limit)


#: Student 9's ordinary earning search may move exactly these, and only on the DPS. Attack reaches the
#: boss and accumulates stored attacks, Luck changes the likelihood and timing of stronger hits through
#: the recovered critical-hit behaviour, and Speed balances interruption/accumulation against actually
#: delivering useful actions. Nothing else is an earning axis.
AVERAGE_EARNING_STATS = ('atk', 'lck', 'spd')
#: Support resources an ordinary earning refinement must leave exactly as it inherits them. The values
#: may be far above the legal minimum; "sufficient" is not a reason to lower one, and Student 9 is not a
#: minimum finder. Student 1's own range work is where minimums are explored.
AVERAGE_PROTECTED_STATS = ('hp', 'mp', 'def')
#: What an explicitly labelled repair may change - and only upward. A repair raises a resource whose
#: insufficiency the evidence has shown, or hands the case to the MP-recovery operation.
AVERAGE_REPAIR_STATS = ('hp', 'mp', 'def')
#: The operations Student 9 performs, each with its own label and its own admission rule.
AVERAGE_PURPOSES = ('earning', 'cleanup', 'repair')


def average_roles(scenario):
    """`(dps name, healer name)` from the build's own derived placement, or None for either."""
    dps = healer = None
    for _index, unit in sorted(_by_index(scenario).items()):
        unit_role = role(unit)
        if unit_role == ROLE_DPS and dps is None:
            dps = unit.get('name')
        elif unit_role == ROLE_HEALER and healer is None:
            healer = unit.get('name')
    return dps, healer


def _value_map(scenario):
    """`{(unit, stat): battle value}` for every human unit, without the axis role/bounds filters.

    A proposal is judged against this rather than against `_value_axes`, so a change to a unit or stat
    that the generators are not allowed to touch at all is still *detected* - the policy check has to
    see everything the child changed, not only the part it was asked about.
    """
    out = {}
    for unit in scenario.get('ownUnits') or []:
        if not unit.get('human'):
            continue
        for stat in COMMUNITY_VALUE_STATS:
            try:
                parameter = contract.stat_parameter(stat)
                out[(unit['name'], stat)] = int(contract.battle_value(scenario, unit, parameter) or 0)
            except (KeyError, TypeError, ValueError, contract.ContractError):
                continue
    return out


def _non_value_signature(scenario):
    """Everything about a build that an ordinary value proposal must not change."""
    return tuple(
        (unit.get('name'), tuple(unit.get('skills') or ()), tuple(unit.get('invocationLevels') or ()),
         unit.get('weaponId'), bool(unit.get('human')))
        for unit in scenario.get('ownUnits') or []
    ), (scenario.get('holyHerbStock'), scenario.get('holyHerbMaxUses'),
        tuple(scenario.get('holyHerbTriggerUnits') or ()))


def average_change_allowed(parent, child, purpose, unit=None):
    """`(ok, reason)` - is this child a legal proposal for the operation `purpose`?

    This is the single admission point. Every generator Student 9 uses - the single-axis ladder, the
    pair walk and the dial - goes through it, so a future generator cannot quietly reintroduce a
    protected-stat reduction or an unrelated axis: such a child is refused here, not merely discouraged
    where it was made.
    """
    if purpose not in AVERAGE_PURPOSES:
        return False, f'unknown average purpose {purpose!r}'
    if not parent or not child:
        return False, 'the proposal has no parent or no child to compare'
    if _non_value_signature(parent) != _non_value_signature(child):
        return False, 'the proposal changed something other than a value (skills, weapon, roster or policy)'
    before, after = _value_map(parent), _value_map(child)
    changed = [(key, before.get(key), after.get(key)) for key in set(before) | set(after)
               if before.get(key) != after.get(key)]
    if not changed:
        return False, 'the proposal changed no value at all'
    roles = {}
    for _index, unit_value in sorted(_by_index(parent).items()):
        roles[unit_value.get('name')] = role(unit_value)
    allowed = AVERAGE_EARNING_STATS if purpose in ('earning', 'cleanup') else AVERAGE_REPAIR_STATS
    for (name, stat), old, new in changed:
        if stat not in allowed:
            return False, f'{stat} is not part of the {purpose} search'
        if unit is not None and name != unit:
            return False, f'the {purpose} search was scoped to {unit}'
        if purpose == 'earning' and roles.get(name) != ROLE_DPS:
            return False, 'earning refinement may only move the DPS'
        if purpose == 'cleanup' and roles.get(name) != ROLE_HEALER:
            return False, 'a cleanup proposal may only move the healer'
        if purpose == 'repair':
            if roles.get(name) == ROLE_FODDER:
                return False, 'a repair does not grow fodder'
            if old is None or new is None or int(new) <= int(old):
                return False, 'a repair may only raise a support resource'
    return True, None


def average_axes(parent, purpose, unit=None):
    """The axes one Average operation is allowed to consider on this parent."""
    if purpose == 'repair':
        stats, roles = AVERAGE_REPAIR_STATS, None
    elif purpose == 'cleanup':
        stats, roles = AVERAGE_EARNING_STATS, (ROLE_HEALER,)
    else:
        stats, roles = AVERAGE_EARNING_STATS, (ROLE_DPS,)
    axes = _value_axes(parent, stats, roles)
    if unit is not None:
        axes = [axis for axis in axes if axis[0] == unit]
    return axes


def average_child(parent, rng=None, taken=None, broaden=False, offset=0, purpose='earning',
                  unit=None, spans=None):
    """One child for the average student. `(scenario, operation, target, change)` or a refusal.

    **Purpose decides what may move, and that is the whole correction.** Student 9 used to walk Student
    1's seven single-axis tracks, so its ordinary refinement proposed Health, Defence, MP and Dexterity
    changes - the minimum-stat tuning this student is not for. Now:

      * `earning` - the DPS's Attack, Luck and Speed only. Enough Attack to reach the boss without
        clearing so fast that accumulation is lost, Luck through the recovered critical-hit behaviour,
        Speed for the balance between interruption, accumulation and delivery.
      * `cleanup` - the healer's Attack, Luck and Speed, for when its *offensive cleanup* is the
        bottleneck (the DPS is gone and the fight is not being finished). Not because healer attacks
        interfere while the DPS lives; they cannot.
      * `repair` - a support resource raised for one named unit, upward only.

    Refining uses the single-axis ladder - the region is known to pay, so the question is which value
    inside it. When the fight has stalled, `broaden` switches to pairs and then the dial over the same
    allowed axes: several values moved at once, which is the only way a move whose halves are each
    individually unhelpful can be reached. Attack and Luck are deliberately pairable - their interaction
    is a primary search question, not an afterthought.

    Every candidate answer is checked by `average_change_allowed` before it is returned, so a pair or
    dial move that would have touched HP, MP, Defence, Dexterity or a non-DPS unit is refused here
    rather than reaching the library.
    """
    rng = rng or random.Random()
    axes = average_axes(parent, purpose, unit)
    if not axes:
        return None, f'no {purpose} axis is available on this build', None, None
    allowed_stats = tuple(dict.fromkeys(axis[1] for axis in axes))
    allowed_roles = ((ROLE_HEALER,) if purpose == 'cleanup'
                     else (None if purpose == 'repair' else (ROLE_DPS,)))
    allowed_units = {unit} if unit is not None else None
    direction = 'up' if purpose == 'repair' else None
    refusals = []

    def accept(child, operation, target, change):
        """Return a proposal only if the policy admits it; otherwise record why and carry on."""
        if child is None:
            return None
        ok, reason = average_change_allowed(parent, child, purpose, unit=unit)
        if not ok:
            refusals.append(reason)
            return None
        return child, operation, target, change

    # Refining first, then widening - or the other way round once the fight has stalled. The fall-through
    # matters: on a mature library every rung near the incumbent is already measured, so a refine-only
    # student would spend its whole share proposing nothing.
    order = (('pair', 'dial'), ('axis',)) if broaden else (('axis',), ('pair', 'dial'))
    for kinds in order:
        if 'axis' in kinds:
            child, operation, target, change = community_child(
                parent, rng, taken=taken, offset=offset, only=allowed_stats,
                roles=allowed_roles, direction=direction, units=allowed_units)
            accepted = accept(child, operation, target, change)
            if accepted:
                return accepted
            if change:
                refusals.append(f'axis: {change}')
        if 'pair' in kinds:
            pairs = pair_keys(parent, only=allowed_stats, roles=allowed_roles)
            if pairs:
                pair = pairs[int(offset) % len(pairs)]
                child, operation, target, change = community_pair_child(
                    parent, rng, taken=taken, pair=pair)
                accepted = accept(child, operation, target, change)
                if accepted:
                    return accepted
                if change:
                    refusals.append(f'{pair_label(pair)}: {change}')
        if 'dial' in kinds:
            child, operation, target, change = dial_child(
                parent, rng, taken=taken, spans=spans, only=allowed_stats, roles=allowed_roles,
                units=allowed_units)
            accepted = accept(child, operation, target, change)
            if accepted:
                return accepted
            if change:
                refusals.append(f'dial: {change}')
    return None, '; '.join(refusals[-2:]) or 'no move was available from this parent', None, None


def average_runnable(store, limits, ordinals, encounter_ids):
    """How many authorised, unfinished seeds the average student has in the given scope."""
    owned = {cid for cid, source in student_of(store).items() if source == STUDENT_AVERAGE}
    total = 0
    for cid in owned:
        if encounter_ids is not None and cid not in encounter_ids:
            continue
        limit = limits.get(cid)
        if limit is None:
            continue
        total += max(0, limit[0]-ordinals.get((cid, 'discovery'), 0))
        total += max(0, limit[1]-ordinals.get((cid, 'validation'), 0))
    return total


#: How many average-student candidates one encounter keeps in flight.
AVERAGE_TRACK_WIDTH = 3
#: How many retained runs one evidence probe reads, and how many fights one pass inspects for support
#: evidence. Both bounded: this runs on the coordinator thread.
AVERAGE_EVIDENCE_RUNS = 6
AVERAGE_EVIDENCE_MIN_SCORE = 1


def _encounter_residents(store, encounter, limit=12):
    """This fight's most-measured resident builds, bounded - the parents Student 9 could refine."""
    rows = store.db.execute(
        'SELECT c.id AS id, c.scenario AS scenario FROM candidate c '
        'JOIN candidate_meta m ON m.id=c.id WHERE m.encounter=? '
        'ORDER BY c.created DESC LIMIT ?', (int(encounter), int(limit))).fetchall()
    return [dict(row) for row in rows]


def average_support_evidence(store, encounter):
    """`{unit: reading}` for watched units that became MP-limited, with the fight's own loss count.

    Read from the aggregates' MP block - the same telemetry the MP-recovery operation uses - so this
    costs a handful of point reads and no run decoding. It establishes that a watched DPS or healer
    *did* reach low MP in a measured run; it does **not** establish that low MP is what cost the
    reward. The distinction the task insists on is kept: the reading is reported as the trigger for a
    bounded repair or for handing the case to MP recovery, and never as a diagnosis.
    """
    evidence = {}
    for row in _encounter_residents(store, encounter):
        stored = store.db.execute('SELECT value FROM meta WHERE key=?',
                                  (f'aggregate:{row["id"]}:validation',)).fetchone()
        if not stored:
            continue
        try:
            payload = json.loads(stored[0]) or {}
        except (TypeError, ValueError):
            continue
        for unit, reading in (payload.get('mp') or {}).items():
            percent = reading.get('minimumMpPercent')
            if not isinstance(percent, int) or percent < 0 or percent > 3:
                continue
            if int(reading.get('lowRuns') or 0) < 1:
                continue
            found = evidence.get(unit) or dict(readings=0, lowRuns=0, worstPercent=100,
                                               losses=0, candidates=0)
            found['readings'] += int(reading.get('runs') or 0)
            found['lowRuns'] += int(reading.get('lowRuns') or 0)
            found['worstPercent'] = min(found['worstPercent'], percent)
            found['losses'] += int(payload.get('losses') or 0)
            found['candidates'] += 1
            evidence[unit] = found
    return evidence


def average_cleanup_indicator(store, encounter):
    """`(fraction, sample)` - lost runs in which somebody was still standing.

    A defeat with own units present at the end is the shape of a fight that was not finished rather than
    one that was wiped out. It is an *indicator*, not a diagnosis: the survivor may be the healer whose
    cleanup attacks were not enough, or a fodder with the DPS already gone. It is only used to *offer*
    the healer's offensive cleanup as a purpose, and it is reported with its own uncertainty.
    """
    lost = 0
    standing = 0
    for row in _encounter_residents(store, encounter):
        for _phase, _ordinal, blob in store.db.execute(
                'SELECT phase,ordinal,result FROM run WHERE candidate=? '
                'ORDER BY ordinal DESC LIMIT ?', (row['id'], AVERAGE_EVIDENCE_RUNS)).fetchall():
            try:
                result = json.loads(blob)
            except (TypeError, ValueError):
                continue
            if result.get('verdict') != 2:
                continue
            lost += 1
            survivors = result.get('survivors')
            if isinstance(survivors, int) and survivors >= 1:
                standing += 1
    if lost < AVERAGE_EVIDENCE_MIN_SCORE:
        return None, lost
    return standing/lost, lost


def average_retire_protected_requests(store, encounter):
    """Retire unstarted Average requests whose only purpose was the disallowed support tuning.

    An Average-owned build with **no measured run at all** and a lineage change that moved only HP, MP
    or Defence was proposed by the old policy - "find a smaller sufficient support value". Its bank is
    released and it is recorded as retired, so the 70% allocation stops being spent on it. The candidate,
    its lineage and its place in the population are all left exactly as they are: the correction is to
    what Student 9 *proposes next*, never to what the library already holds. Work already executing is
    untouched, and another student's requests are never considered here.
    """
    banks = dict(store.get(AVERAGE_BANKS_KEY) or {})
    retired = dict(store.get(AVERAGE_RETIRED_KEY) or {})
    released = []
    # One query for the whole student, not one per candidate: the first version ran a correlated
    # sub-select for every Average-owned build on every pass - 22,113 statements in a 47-second window,
    # measured in `docs/benchmarks/optimizer-20260926-trace` - which is more work than the retirement
    # it exists to do.
    rows = store.db.execute(
        'SELECT l.candidate, l.operation, l.change, v.value '
        'FROM lineage l LEFT JOIN meta v ON v.key=(\'aggregate:\'||l.candidate||\':validation\') '
        "WHERE l.source=?", (STUDENT_AVERAGE,)).fetchall()
    for cid, operation, change, stored in rows:
        measured = 0
        if stored:
            try:
                measured = int((json.loads(stored) or {}).get('n') or 0)
            except (TypeError, ValueError):
                measured = 0
        if measured > 0:
            continue
        stats = {part.split()[0] for part in str(change or '').split('->') if part.strip()}
        stats = {stat for stat in stats if stat in COMMUNITY_VALUE_STATS}
        if not stats or not stats <= set(AVERAGE_PROTECTED_STATS):
            continue
        if cid in banks:
            banks.pop(cid, None)
            released.append(cid)
        retired[cid] = dict(operation=operation, change=change, at=time.time(),
                            reason='unstarted support-minimum tuning: not an earning proposal')
    if released or retired != (store.get(AVERAGE_RETIRED_KEY) or {}):
        with store.db:
            store.set(AVERAGE_BANKS_KEY, banks)
            store.set(AVERAGE_RETIRED_KEY, retired)
    return released



def replenish_average(store, encounter, count, proposal, stats, rng=None):
    """One pass for the average student on one fight. Returns the ids of children it created.

    The pass has two operations and they are genuinely different, so they are named and recorded:

      * **evaluate** - the parent it chose is under-sampled ("validate"), so the parent itself is
        authorised to a larger validation bank. A reason string saying "validate" is not validation;
        without this the same parent would be re-chosen forever while only ever producing children.
      * **create** - otherwise `count` children are proposed from that parent, as before.

    Both banks and the decision travel in the store, so a restart resumes them instead of relearning.
    """
    import random
    import time
    from strategy_optimizer import MAX_SAMPLES, VALIDATION_RUNS
    created = []
    rng = rng or random.Random(encounter*104729 + int(proposal or 0))
    parent_id, parent, why = average_parent(store, encounter)
    best, _entries = average_readings(store, encounter)
    owned = {cid for cid, source in student_of(store).items() if source == STUDENT_AVERAGE}
    completed = 0
    if owned:
        # One query for the student's whole population rather than one per build: this runs on every
        # replenishment pass, and the per-candidate form was measured as thousands of statements a pass.
        keys = [f'aggregate:{cid}:validation' for cid in owned]
        placeholders = ','.join('?' for _ in keys)
        for _key, value in store.db.execute(
                f'SELECT key,value FROM meta WHERE key IN ({placeholders})', keys):
            try:
                completed += int((json.loads(value) or {}).get('n') or 0)
            except (TypeError, ValueError):
                pass
    _credited, stalled = average_progress(
        store, encounter, None if best is None else best['reliable'],
        point_now=None if best is None else best['mean'], completed=completed)
    if parent is None:
        return created
    from strategy_optimizer import identity as _identity
    known = {row[0] for row in store.db.execute('SELECT id FROM candidate')}
    banks = dict(store.get(AVERAGE_BANKS_KEY) or {})
    operation = 'create'
    if why.startswith('validate'):
        # Schedule the *parent's* own evaluation, and grow the bank along the existing ladder
        # (64 -> 128 -> ... -> MAX_SAMPLES) so a promising candidate keeps being measured instead of
        # being frozen at the screening bank.
        operation = 'evaluate'
        banks[parent_id] = min(MAX_SAMPLES, max(VALIDATION_RUNS, banks.get(parent_id, 0)*2))
        with store.db:
            store.set(AVERAGE_BANKS_KEY, banks)
            store.set(AVERAGE_PLAN_KEY, dict(encounter=int(encounter), operation='evaluate',
                                             candidate=parent_id, runs=banks[parent_id],
                                             reason=why, at=time.time()))
        # Evaluate-only: the pass spends its compute on measuring the parent it already has. Creating
        # children in the same pass would make "which operation ran" ambiguous, and the next pass
        # proposes children as soon as the parent is credible.
        return created

    def taken(scenario):
        return _identity(scenario) in known

    # ---- purpose: what this pass is allowed to be about -------------------------------------------
    # Ordinary work is the DPS earning search. A repair is only ever considered when the evidence shows
    # a watched unit became MP-limited, and the healer's offensive cleanup only when defeats leave own
    # units standing. Neither is inferred from a mere absence of telemetry, and the MP case is handed to
    # the MP-recovery operation when that allowance is configured - that path already owns it.
    support = average_support_evidence(store, encounter)
    cleanup_fraction, cleanup_sample = average_cleanup_indicator(store, encounter)
    purpose, purpose_reason, repair_unit = 'earning', None, None
    try:
        import strategy_mp_recovery
        allowance = strategy_mp_recovery.setting(store)
        covered = set((request.get('watch') or [])[0]
                      for request in (strategy_mp_recovery.ledger(store).get('requests') or {}).values())
    except Exception:  # noqa: BLE001 - the MP operation is optional, the earning search is not
        allowance, covered = dict(effective=False), set()
    for unit, reading in sorted(support.items()):
        if allowance.get('effective') and unit in covered:
            purpose_reason = (f'{unit} reached {reading["worstPercent"]}% of its own maximum MP and the '
                              'MP-recovery operation already owns a bounded correction for it')
            continue
        if allowance.get('effective'):
            purpose_reason = (f'{unit} reached {reading["worstPercent"]}% of its own maximum MP; the '
                              'MP-recovery operation can test that hypothesis, so the earning search '
                              'continues instead of guessing at a bigger pool')
            continue
        purpose, repair_unit = 'repair', unit
        purpose_reason = (f'{unit} was measured at {reading["worstPercent"]}% of its own maximum MP in '
                          f'{reading["lowRuns"]} of {reading["readings"]} watched runs - a bounded '
                          'repair, not a search for a smaller sufficient value')
        break
    if purpose == 'earning' and cleanup_fraction is not None and cleanup_fraction >= 0.5:
        _dps, healer = average_roles(parent)
        if healer:
            purpose = 'cleanup'
            purpose_reason = (f'{cleanup_fraction:.0%} of the last {cleanup_sample} defeats left own '
                              f'units standing, so the healer\'s offensive cleanup is the open '
                              'question for this fight')
    spans = None
    if purpose in ('earning', 'cleanup'):
        spans = measured_spans(store, encounter)

    misses = 0
    for index in range(int(count)):
        child, operation, target, change = average_child(
            parent, rng, taken=taken, broaden=stalled, offset=int(proposal or 0)+index,
            purpose=purpose, unit=(repair_unit if purpose == 'repair' else None), spans=spans)
        if child is None:
            misses += 1
            if misses >= 4:
                break
            continue
        label = (f'Average · {purpose if purpose != "earning" else ("widen" if stalled else "refine")}'
                 f' · {change}')[:160]
        cid, existed = store.add_child(child, label, lambda child=child: stats(child), parent_id,
                                       operation, target, change, STUDENT_AVERAGE, proposal)
        known.add(cid)
        if existed:
            misses += 1
            if misses >= 4:
                break
            continue
        misses = 0
        created.append(cid)
    retired = average_retire_protected_requests(store, encounter)
    if created or retired:
        with store.db:
            store.set('averageLastReason', {'encounter': int(encounter), 'reason': why,
                                            'stalled': stalled, 'purpose': purpose,
                                            'purposeReason': purpose_reason,
                                            'retired': len(retired)})
            if operation == 'create':
                store.set(AVERAGE_PLAN_KEY, dict(encounter=int(encounter), operation='create',
                                                 candidate=parent_id, runs=len(created),
                                                 reason=why, stalled=stalled, purpose=purpose,
                                                 purposeReason=purpose_reason,
                                                 retired=len(retired), at=time.time()))
    return created


def average_pending_states(store, encounter, shares=None, owners=None):
    """`[{candidate, operation, change, state, reason, bank}]` - every Average build with no runs yet.

    A candidate that has been created and never measured is the state the user asked to be able to read:
    is it newly queued, waiting on an authorised bank, blocked because this stream's share is zero,
    attached to a scope it cannot run in, retired by the policy change, or is there a concrete
    scheduling error? Counting such builds without saying why is how a backlog can grow while nothing
    runs - so each one carries its own state and the reason behind it.
    """
    banks = dict(store.get(AVERAGE_BANKS_KEY) or {})
    retired = dict(store.get(AVERAGE_RETIRED_KEY) or {})
    encounters_of = store.candidate_encounters()
    average_on = None
    if shares is not None:
        try:
            average_on = float(shares.get(STUDENT_AVERAGE, 0.0) or 0.0) > 0
        except (TypeError, ValueError):
            average_on = None
    owners = student_of(store) if owners is None else owners
    rows = []
    candidate_ids = [cid for cid, source in owners.items()
                     if source == STUDENT_AVERAGE and encounters_of.get(cid) == int(encounter)]
    measured_by_candidate = {}
    if candidate_ids:
        keys = [f'aggregate:{cid}:validation' for cid in candidate_ids]
        placeholders = ','.join('?' for _ in keys)
        for key, value in store.db.execute(
                f'SELECT key,value FROM meta WHERE key IN ({placeholders})', keys):
            try:
                measured_by_candidate[str(key).split(':')[1]] = int(
                    (json.loads(value) or {}).get('n') or 0)
            except (TypeError, ValueError):
                continue
    for cid, source in owners.items():
        if source != STUDENT_AVERAGE:
            continue
        if encounters_of.get(cid) != int(encounter):
            continue
        measured = measured_by_candidate.get(cid, 0)
        if measured > 0:
            continue
        lineage = store.db.execute(
            'SELECT operation, change FROM lineage WHERE candidate=?', (cid,)).fetchone()
        entry = dict(candidate=cid,
                     operation=(lineage['operation'] if lineage else None),
                     change=(lineage['change'] if lineage else None),
                     bank=banks.get(cid), reason=None)
        if cid in retired:
            entry['state'] = 'retired'
            entry['reason'] = retired[cid].get('reason')
        elif average_on is False:
            entry['state'] = 'share disabled'
            entry['reason'] = 'Student 9 is at 0% of the pool, so nothing of its own may be dispatched'
        elif cid not in encounters_of:
            entry['state'] = 'invalid scope'
            entry['reason'] = 'the build has no encounter index, so no scheduler will plan it'
        elif banks.get(cid):
            entry['state'] = 'waiting for authorised runs'
            entry['reason'] = f'authorised to {banks[cid]} validation runs, none recorded yet'
        else:
            entry['state'] = 'newly queued'
            entry['reason'] = ('no bank has been authorised for it yet; the next pass either measures '
                               'it through the staged policy or gives it one')
        rows.append(entry)
    return rows


def average_report(store, shares=None):
    """`{encounter: {...}}` - the student's own success metric, per fight.

    The metric is the user's: *did this fight's best RELIABLE mean earned go up*. So the report leads
    with it, and carries the best single earned beside it only as the diagnostic the student is
    explicitly told not to chase - deliberately side by side, because the gap between the two columns is
    the whole reason this student exists.
    """
    state = dict(store.get(AVERAGE_PROGRESS_KEY) or {})
    # One lineage read for the whole report: the per-encounter pending scan used to re-read the entire
    # lineage table for every fight on every pass.
    owners = student_of(store)
    report = {}
    for encounter in sorted(set(store.candidate_encounters().values())):
        best, entries = average_readings(store, encounter)
        if not entries:
            continue
        peak = max(entries, key=lambda entry: (entry['best'], entry['samples']))
        record = dict(state.get(str(int(encounter))) or {'best': None, 'passes': 0})
        report[int(encounter)] = dict(
            bestReliableMean=None if best is None else round(best['reliable'], 3),
            bestMean=None if best is None else round(best['mean'], 3),
            bestMeanSamples=0 if best is None else best['samples'],
            bestMeanCandidate=None if best is None else best['candidate'],
            # Secondary, and only secondary: the number this student must not optimise.
            bestEarnedDiagnostic=peak['best'],
            bestEarnedMean=round(peak['mean'], 3),
            bestEarnedSamples=peak['samples'],
            creditedBest=record.get('best'),
            passesSinceImprovement=int(record.get('passes') or 0),
            stalled=int(record.get('passes') or 0) >= AVERAGE_STAGNATION_PASSES,
            candidates=len(entries),
            pending=average_pending_states(store, encounter, shares=shares, owners=owners))
    return report
