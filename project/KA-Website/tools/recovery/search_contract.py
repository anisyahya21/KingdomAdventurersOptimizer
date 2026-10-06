"""The hard search-space contract for the Strategy Optimiser: ONE UNIVERSAL SYNTHETIC HUMAN.

Discovery searches a synthetic human, not a character. A synthetic human is a combat-capable human
defined only by its stats, ordered skills, trigger settings, weapon behaviour, formation placement and
consumable configuration. There are no jobs, no ranks, no character levels, no job-specific stat
strengths, no job skill or equipment restrictions and no job weapon affinities anywhere in the search
space: every human slot uses the same universal rules, while slots may still differ from each other in
every searchable dimension. The battle itself is unchanged - the same separate fighters still fight,
and monsters keep their existing identities. Only the *type* model is collapsed. The real-build
resolver (which job/rank/equipment can reproduce a synthetic vector) is a later stage, deliberately
absent here.

This module defines what a *legal searchable synthetic human strategy* can contain, and it is the only
place that decides it. It exists because the previous generator could only re-order the skills a
fighter already had: add, remove, replace, trigger and conditional-INT mutations did not exist, so the
search spent its battles inside a tiny artificial space (see the library audit evidence).

Four things are kept strictly apart, per the task that created this module:

  * **HARD RULES** - proven illegal, impossible or behaviourally identical. They are rejected here,
    before any simulation, and never become a candidate.
  * **DERIVED STATE** - a value the simulator computes from something else (Bow Attack from the
    equipped weapon, the stored-attack setup from the command queue). It is never spent as a
    searchable skill slot.
  * **CONDITIONAL DIMENSIONS** - a dimension whose *existence* depends on another part of the
    strategy (INT requires a magic attack skill). Structural, not a learned preference.
  * **LEARNED IRRELEVANCE** - a dimension that merely looks unhelpful right now. Nothing here removes
    those; the later learning redesign decides how much effort they get. `search_contract` never
    prunes ATT/DEF/SPEED/LUCK/DEX just because a strategy stopped responding to them.

Recovered evidence behind the rules (all cited in the final report):

  * Skill rows and names: `RE-evidence/20260912-combat/weapon-skill-profiles.json` plus the
    site's own `website_icons/skills/skill_icon_map.json` (`skillsByName`), which is where the
    display names the user quoted - including "Heal Maddy" and the Area Attack tiers - resolve.
  * Combat selection: `combat_skill_selection.active_skill_infos` keeps only rows with `flags & 8`
    and not `flags & 32`; `attack_skill_candidates` keeps category 0 only. Every non-combat or
    passive row (category 2, `flags & 2`) is therefore never selected.
  * Formation skills: `docs/reverse-engineering/special-combat.md` - the first possessed type-60
    skill decides the fighter's formation priority (Leading the Charge 0, Daring Charge 3, Backup 10,
    default 6) and later ones are ignored; the search therefore keeps one and never gives them an
    ordinary trigger search.
  * Bow Attack: `weapon-skill-profiles.json['normalProjectileSkills']['bow'] == [1]`, and row 1
    requires equip type 8 - it is the weapon's normal attack, not a slot.
  * Trigger settings: `combat_scenario.load_scenario` accepts exactly `level in (0, 1, 2)`.
  * Stat arithmetic: `combat_parameters.fighter_parameter` (effective = raw + extra + equipment for
    unbounded stats; for HP/MP the battle value is the *maximum*, which is rawMax + equipment, because
    `combat_sandbox.run_scenario` fills both to the prepared maximum).
"""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

HERE = Path(__file__).resolve().parent

#: Bumped whenever a rule here changes what the generator can produce. Version 4 searches variable
#: synthetic team composition as well as job-free human combat inputs. A
#: library records the version it was generated under; a generator never continues a library from a
#: different version silently.
SEARCH_SPACE_VERSION = 4

#: Equipped skills on one human. The user's equip cap; the recovered native buffer is larger
#: (`KA_MAX_SKILLS = 12`, `Skill.maxSlotNum`), and no per-job slot cap is recovered, so this is a
#: declared search-contract cap and is reported as such - not as a recovered game limit.
MAX_SKILLS_PER_HUMAN = 9

#: `combat_scenario.load_scenario`: "Unknown skill or invocation setting" unless the level is 0/1/2.
TRIGGER_LEVELS = (0, 1, 2)

#: 6-Hit Attack is excluded from this search outright.
EXCLUDED_SKILLS = {109: '6-Hit Attack is excluded from this search by the task contract'}

#: Team-wide caps across the whole human team (not per fighter).
TEAM_SKILL_CAPS = {110: (2, '7-Hit Attack: at most 2 across the entire human team')}

#: Formation skills (type 60). Only the first one carried has any effect, and they never receive the
#: ordinary combat trigger search.
FORMATION_SKILLS = {105: 'Leading the Charge', 106: 'Daring Charge', 107: 'Backup'}
FORMATION_SKILL_TYPE = 60

#: Magic attack rows from the whitelist; carrying any of them makes INT a searchable dimension.
MAGIC_SKILL_IDS = tuple(range(5, 20))  # Fire I-V (5-9), Ice I-V (10-14), Lightning I-V (15-19)

#: Weapon-derived normal attacks: never a searchable skill slot (see the module docstring).
DERIVED_WEAPON_SKILLS = {1: 'Bow Attack', 2: 'Gun Attack'}
BOW_ATTACK_ID = 1

#: The user-approved skill whitelist, exactly as supplied, mapped to recovered row ids through the
#: site's own `skillsByName` display names. The order of this table is the order of the report.
WHITELIST_NAMES = (
    # multi-hit / physical
    '2-Hit Attack', '3-Hit Attack', '4-Hit Attack', '5-Hit Attack', '7-Hit Attack',
    # other combat skills
    'Counter', 'Critical UP', 'Critical UP+',
    # magic
    'Fire Magic Ⅰ', 'Fire Magic Ⅱ', 'Fire Magic Ⅲ', 'Fire Magic Ⅳ', 'Fire Magic Ⅴ',
    'Ice Magic Ⅰ', 'Ice Magic Ⅱ', 'Ice Magic Ⅲ', 'Ice Magic Ⅳ', 'Ice Magic Ⅴ',
    'Lightning Magic Ⅰ', 'Lightning Magic Ⅱ', 'Lightning Magic Ⅲ', 'Lightning Magic Ⅳ',
    'Lightning Magic Ⅴ',
    # reflect
    'Full Reflect', 'Half Reflect',
    # healing
    'Heal L', 'Heal M', 'Heal Maddy',
    # other
    'Myriad Arrows', 'Parry', 'Perfect Dodge', 'Revive 100%', 'Revive 50%',
    # area attack (the supplied list's third entry is a duplicate "II"; the data resolves I/II/III)
    'Area Attack Ⅰ', 'Area Attack Ⅱ', 'Area Attack Ⅲ',
    # armor breaker
    'Armor Breaker Ⅱ', 'Armor Breaker Ⅲ', 'Armor Breaker Ⅳ',
    # direct attack
    'Direct Attack Ⅰ', 'Direct Attack Ⅱ',
    'Dodge UP', 'Arrow Rain',
    # sandman
    'Sandman Ⅰ', 'Sandman Ⅱ', 'Sandman Ⅲ',
    # formation skills (kept out of the ordinary combat selection; see FORMATION_SKILLS)
    'Backup', 'Daring Charge', 'Leading the Charge',
)


class ContractError(ValueError):
    """A generated candidate that the contract refuses, with the reason it refuses it."""


def _display_names():
    """The site's own skill display-name table (`skillsByName`), or an empty mapping."""
    path = (HERE.parents[1] / 'website_icons' / 'skills' / 'skill_icon_map.json')
    try:
        return json.loads(path.read_text(encoding='utf-8')).get('skillsByName', {})
    except (OSError, ValueError):
        return {}


_PROFILES = None


def profiles():
    """The recovered skill/equipment rows, decoded once.

    Every mutation reads a skill row, an equipment row or a weapon row. Re-reading and re-parsing the
    whole profile file per lookup made a single stat write cost more than a battle; the data is
    immutable for the process, so it is decoded once.
    """
    global _PROFILES
    if _PROFILES is None:
        from combat_runtime_data import load_data
        document = load_data('weapon-skill-profiles.json')
        _PROFILES = dict(skills={row['id']: row for row in document['skills']},
                         equipment={row['id']: row for row in document['equipment']},
                         normalProjectileSkills=document.get('normalProjectileSkills') or {})
    return _PROFILES


def _skill_rows():
    return profiles()['skills']


def whitelist():
    """{skill id: display name} for the approved set, verified against the recovered data."""
    names = _display_names()
    rows = _skill_rows()
    mapping = {}
    for display in WHITELIST_NAMES:
        entry = names.get(display)
        if entry is None:
            raise ContractError(f'the recovered skill name table has no entry for {display!r}')
        skill_id = int(entry['id'])
        row = rows.get(skill_id)
        if row is None:
            raise ContractError(f'skill {display!r} resolves to id {skill_id}, which has no row')
        mapping[skill_id] = display
    for skill_id in FORMATION_SKILLS:
        if skill_id not in mapping:
            raise ContractError(f'formation skill {skill_id} is missing from the whitelist')
    return mapping


def whitelist_by_name():
    return {display: skill_id for skill_id, display in whitelist().items()}


def skill_row(skill_id):
    return _skill_rows().get(skill_id)


def is_magic_attack(skill_id):
    return skill_id in MAGIC_SKILL_IDS


def is_formation_skill(skill_id):
    return skill_id in FORMATION_SKILLS


# ---------------------------------------------------------------------------------------------
# Stats: one clear numeric domain, and the derived walls
# ---------------------------------------------------------------------------------------------

#: The seven core combat stats plus the conditional one, by the site's own recovered ids
#: (`strategy_probe.STAT_AXES` / `website .../stat-parameter-ids`).
CORE_STATS = {'hp': 10, 'mp': 11, 'atk': 13, 'def': 14, 'spd': 15, 'lck': 16, 'dex': 19}
INT_STAT = {'int': 18}

#: HP and MP are *bounded* parameters: the battle value is the prepared maximum
#: (`rawMax + extraMax + equipment`), because `combat_sandbox.run_scenario` fills them to it. Every
#: other stat is unbounded and its effective value is `raw + extra + equipment`.
BOUNDED_STATS = (10, 11, 12)

#: Where the derived bounds live. `derive_search_bounds.py` writes them from the recovered
#: job/rank/level data; the contract refuses to invent one if the file is missing. The path is the
#: workspace-root evidence directory the other recovery checks already write into
#: (`RE-evidence/20260922-search-contract/`), not a directory next to this module.
BOUNDS_PATH = HERE.parents[2] / 'RE-evidence' / '20260922-search-contract' / 'stat-bounds.json'

_BOUNDS_CACHE = None


def stat_bounds():
    """{parameter id: (minimum, maximum)} - the synthetic human's effective-stat walls.

    Each stat is an independent synthetic combat variable inside its own interval, and the interval is
    deliberately not one real character's: the floor is the lowest parameter-level-1 value any job row
    carries, and the ceiling is the highest S-rank curve evaluated at parameter level **999** (not
    clamped to the row's own `maxLevel`) plus the best single level-99 equipment contribution. The
    job that supplied a wall is informational only - `search_contract` never models a job.
    """
    global _BOUNDS_CACHE
    if _BOUNDS_CACHE is None:
        try:
            document = json.loads(BOUNDS_PATH.read_text(encoding='utf-8'))
        except (OSError, ValueError) as error:
            raise ContractError(
                f'the derived stat bounds are unavailable ({error}); run the stat-bound derivation '
                'before generating candidates') from error
        verification = document.get('verification') or {}
        if verification and not verification.get('verified', True):
            # The derivation could not reproduce the reviewed table from the source data. That is a
            # stop-and-report condition, never something to paper over with another interpretation, so
            # the contract refuses to hand out numbers instead of substituting any.
            raise ContractError('the derived synthetic stat bounds disagree with the reviewed table: '
                                + json.dumps(verification.get('mismatches')))
        bounds = {}
        for entry in document.get('stats', []):
            parameter = int(entry['parameter'])
            bounds[parameter] = (int(entry['minimum']), int(entry['maximum']))
        missing = [name for name, pid in {**CORE_STATS, **INT_STAT}.items() if pid not in bounds]
        if missing:
            raise ContractError(f'the derived stat bounds do not cover {", ".join(missing)}')
        _BOUNDS_CACHE = bounds
    return _BOUNDS_CACHE


def stat_parameter(stat):
    """The recovered parameter id for a stat name, refusing anything outside the contract."""
    if stat in CORE_STATS:
        return CORE_STATS[stat]
    if stat in INT_STAT:
        return INT_STAT[stat]
    raise ContractError(f'unknown stat {stat!r}; the contract covers '
                        f'{", ".join(sorted({**CORE_STATS, **INT_STAT}))}')


def equipment_rows(scenario, unit):
    """(row, level, affinity, human) for every equipment slot the engine would read, weapon included."""
    from combat_parameters import equipment_affinity, equip_master_lifted_types
    rows = profiles()['equipment']
    skill_rows = [_skill_rows().get(skill_id, {}) for skill_id in unit['skills']]
    lifted = equip_master_lifted_types([row for row in skill_rows if row])
    resolved = []
    for slot in unit['equipment']:
        row = rows.get(int(slot['id']))
        if row is None:
            continue
        affinity = equipment_affinity(row['type'], slot['affinity'], lifted)
        resolved.append((row, int(slot['level']), affinity, bool(unit['human'])))
    return resolved


def engine_equipment(unit):
    """The unit's equipment in exactly the shape `combat_parameters.fighter_parameter` consumes.

    `combat_setup.prepare_setup` builds this same list (the recovered row plus the declared slot level
    and the affinity rule); reproducing it here means a synthetic stat read is a few integer
    operations instead of a full setup rebuild, which is what made generating a candidate's stats cost
    more than simulating a battle.
    """
    return [dict(row, level=int(level), affinity=affinity)
            for row, level, affinity, _human in equipment_rows(None, unit)]


def equipment_contribution(scenario, unit, parameter_id):
    """The equipment term `fighter_parameter` adds for this parameter, using the unit's own slots."""
    from combat_parameters import equipment_contribution as contribution
    total = 0
    for row in engine_equipment(unit):
        total += contribution(row, parameter_id, row['level'], affinity=row['affinity'],
                              human=bool(unit['human']))
    return total


def effective_parameter(scenario, unit, parameter_id, maximum=False):
    """The engine's own effective value for this parameter.

    The arithmetic is `combat_parameters.fighter_parameter`, called exactly the way
    `combat_setup.prepare_setup` calls it for an own (ally) unit - same equipment rows, same affinity,
    same bounded/unbounded rule - so this is the engine's own answer rather than a second model. The
    equivalence is pinned by a test that compares it with `prepare_setup` on real scenarios.
    """
    from combat_parameters import fighter_parameter, equipment_contribution as contribution
    entry = _parameter_entry(unit, parameter_id)
    if entry is None:
        return None
    rows = engine_equipment(unit)
    human = bool(unit['human'])
    return int(fighter_parameter(
        parameter_id, entry, rows,
        lambda row, parameter, level: contribution(row, parameter, level,
                                                   affinity=row['affinity'], human=human),
        human=human, ally=True, maximum=maximum))


def battle_value(scenario, unit, parameter_id):
    """What the fight actually starts this parameter at.

    For every stat except HP/MP that is the effective value. For HP/MP it is the prepared *maximum*,
    because the runner fills both to it before the first tick.
    """
    if parameter_id in BOUNDED_STATS:
        return effective_parameter(scenario, unit, parameter_id, maximum=True)
    return effective_parameter(scenario, unit, parameter_id)


def stat_is_searchable(scenario, unit, stat):
    """INT exists only while a magic attack skill is equipped; everything else is unconditional."""
    if stat != 'int':
        return True
    return any(is_magic_attack(skill_id) for skill_id in unit['skills'])


def _searched_parameters():
    """Every parameter the contract searches: the seven core stats plus the conditional INT."""
    return {**CORE_STATS, **INT_STAT}


def set_effective_parameter(scenario, unit, parameter_id, target):
    """Write one synthetic effective value, neutralising whatever the carrier equipment adds.

    The carrier (a representative weapon row, and any other slot a supplied build carries) contributes
    to the parameter through the engine's own arithmetic; the synthetic value must not be moved by it.
    The engine computes

        unbounded stat:  value = rawValue + extraValue + equipment
        HP/MP:           value = rawValue + extraValue,  maximum = rawMax + extraMax + equipment

    so the searched number is written into the raw channel and the carrier's contribution is cancelled
    in the extra channel (`extraValue = -equipment`, or `extraMax = -equipment` for the bounded stats,
    where the battle value IS the prepared maximum). The engine's equipment mathematics is untouched:
    this is the "neutral representation at the adapter boundary" the contract is required to use, and
    it is what makes every value inside the walls reachable with any weapon behaviour.
    """
    entry = _parameter_entry(unit, parameter_id)
    if entry is None:
        raise ContractError(f'{unit["name"]}: carries no parameter {parameter_id}')
    contribution = equipment_contribution(scenario, unit, parameter_id)
    if parameter_id in BOUNDED_STATS:
        entry['rawMax'] = int(target)
        entry['extraMax'] = -int(contribution)
        entry['rawValue'] = int(target)
        entry['extraValue'] = 0
    else:
        entry['rawValue'] = int(target)
        entry['extraValue'] = -int(contribution)
    return entry


# ---------------------------------------------------------------------------------------------
# Hard rules
# ---------------------------------------------------------------------------------------------

def weapon_type_of(scenario, unit):
    """The unit's weapon type id, from the same row the simulator reads."""
    return int(_weapon_row(unit)['type'])


def _weapon_row(unit):
    rows = profiles()['equipment']
    row = rows.get(int(unit['weaponId']))
    if row is None:
        raise ContractError(f'weapon {unit["weaponId"]} has no recovered equipment row')
    return row


def check_human(scenario, unit, *, generated=True):
    """Every hard rule for one human, in the order a candidate would break them.

    `generated=False` is the read-only path for a supplied/imported build: those are readable but the
    generator must never *produce* one that breaks these rules.
    """
    approved = whitelist()
    skills = list(unit['skills'])
    levels = list(unit['invocationLevels'])
    if len(skills) != len(levels):
        raise ContractError(f'{unit["name"]}: every skill slot needs a trigger setting')
    if len(skills) > MAX_SKILLS_PER_HUMAN:
        raise ContractError(f'{unit["name"]}: {len(skills)} skills is above the '
                            f'{MAX_SKILLS_PER_HUMAN}-skill contract cap')
    if len(set(skills)) != len(skills):
        raise ContractError(f'{unit["name"]}: the same skill is carried more than once; a duplicate is '
                            'illegal even with a different trigger setting')
    for index, (skill_id, level) in enumerate(zip(skills, levels)):
        if skill_id in EXCLUDED_SKILLS:
            raise ContractError(f'{unit["name"]}: {EXCLUDED_SKILLS[skill_id]}')
        if skill_id not in approved:
            raise ContractError(f'{unit["name"]}: skill {skill_id} is outside this encounter\'s '
                                'approved search set')
        if level not in TRIGGER_LEVELS:
            raise ContractError(f'{unit["name"]}: trigger setting {level} is not one of the recovered '
                                f'legal values {TRIGGER_LEVELS}')
        row = skill_row(skill_id)
        required = int(row['requiredEquipType'])
        if required != -1 and weapon_type_of(scenario, unit) != required:
            raise ContractError(f'{unit["name"]}: {approved[skill_id]} needs weapon type {required}, '
                                f'and this build carries {weapon_type_of(scenario, unit)}')
        if is_formation_skill(skill_id) and index > 0 and any(
                is_formation_skill(other) for other in skills[:index]):
            raise ContractError(f'{unit["name"]}: a second formation skill has no effect - the first '
                                'possessed type-60 skill decides the formation priority')
    return True


def check_team(scenario):
    """Team-wide caps across the whole human team."""
    counts = {}
    for unit in scenario['ownUnits']:
        if not unit.get('human'):
            continue
        for skill_id in unit['skills']:
            counts[skill_id] = counts.get(skill_id, 0)+1
    for skill_id, (cap, reason) in TEAM_SKILL_CAPS.items():
        if counts.get(skill_id, 0) > cap:
            raise ContractError(f'{reason} (found {counts[skill_id]})')
    return True


_ENEMY_COUNTS = None


def enemy_count(encounter_id):
    global _ENEMY_COUNTS
    if _ENEMY_COUNTS is None:
        from combat_runtime_data import load_data
        _ENEMY_COUNTS = {int(row['id']): 1 + len(row.get('followers') or [])
                         for row in load_data('encounters.json')['encounters']}
    try:
        return _ENEMY_COUNTS[int(encounter_id)]
    except KeyError:
        raise ContractError(f'unknown encounter {encounter_id}') from None


def check_scenario(scenario):
    """The whole contract for a candidate that is about to be generated."""
    if len(scenario['ownUnits']) + enemy_count(scenario['encounterId']) > 32:
        raise ContractError('this team and the encounter exceed the native 32-fighter capacity')
    if not _human_units(scenario):
        raise ContractError('a synthetic strategy needs at least one human fighter')
    for unit in scenario['ownUnits']:
        if unit.get('human'):
            check_human(scenario, unit)
    check_team(scenario)
    return True


# ---------------------------------------------------------------------------------------------
# Search-space identity
# ---------------------------------------------------------------------------------------------

def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


def _parameter_entry(unit, parameter_id):
    """A parameter block, whether the scenario keys parameters by int or by str (stored libraries)."""
    parameters = unit.get('parameters') or {}
    if str(parameter_id) in parameters:
        return parameters[str(parameter_id)]
    return parameters.get(parameter_id)


def search_identity(scenario):
    """The canonical identity of everything the contract lets the optimiser search.

    Ordered skill list (never sorted - the game tries skills in order), each skill's trigger setting,
    the conditional stats, the formation placement, the weapon *behaviour group* (two items that fight
    identically are one strategy), the resources and the fight identity. Two scenarios with the same
    identity are the same search-space point and must never both be simulated.
    """
    projection = []
    for unit in scenario['ownUnits']:
        parameters = {str(pid): int((_parameter_entry(unit, pid) or {}).get('rawValue') or 0)
                      for pid in sorted({**CORE_STATS, **INT_STAT}.values())}
        # A formation skill's trigger value is never read (`active_skill_infos` requires `flags & 8`,
        # and the type-60 rows do not carry it), so it is canonicalised here: a candidate that only
        # changed a formation trigger is a no-op in this search space, not a new strategy.
        triggers = [None if is_formation_skill(skill_id) else level
                    for skill_id, level in zip(unit['skills'], unit['invocationLevels'])]
        projection.append(dict(
            name=unit['name'], human=bool(unit.get('human')), monsterId=unit.get('monsterId'),
            skills=list(unit['skills']), triggers=triggers,
            stats=parameters, weaponGroup=weapon_group_id(unit),
            # The equipment list itself is deliberately NOT part of the identity: the only thing the
            # simulator reads from it is its numeric contribution (already carried by `stats`) and the
            # weapon's behaviour (carried by `weaponGroup`). Keeping item ids here would split one
            # strategy into thousands of "variants" that fight identically.
            placement=(unit.get('grid'), tuple(unit.get('cell') or ()))))
    payload = dict(
        encounterId=scenario['encounterId'], defeatCount=scenario['defeatCount'],
        tickLimit=scenario['tickLimit'], holyHerbStock=scenario['holyHerbStock'],
        inputs=scenario['inputs'], units=projection)
    return hashlib.sha256(_canonical(payload).encode('utf-8')).hexdigest()


# ---------------------------------------------------------------------------------------------
# Weapon behaviour groups
# ---------------------------------------------------------------------------------------------

#: The weapon fields the canonical simulator actually reads to decide behaviour. Everything else on
#: an equipment row is either presentation or a numeric bonus, and numeric bonuses are compensated in
#: the stat domain instead of being treated as behaviour.
WEAPON_BEHAVIOUR_FIELDS = ('type', 'motion', 'shootingRange', 'projectileFlag', 'category')


def weapon_behaviour(unit):
    row = _weapon_row(unit)
    return tuple(row.get(field) for field in WEAPON_BEHAVIOUR_FIELDS)


def weapon_group_id(unit):
    return '|'.join(str(value) for value in weapon_behaviour(unit))


@lru_cache(maxsize=1)
def weapon_groups():
    """{group id: {'fields': {...}, 'weapons': [ids]}} over every recovered weapon row."""
    from combat_runtime_data import load_data
    profiles = load_data('weapon-skill-profiles.json')
    groups = {}
    for row in profiles['equipment']:
        if int(row.get('category', 0)) != 0:
            continue
        key = '|'.join(str(row.get(field)) for field in WEAPON_BEHAVIOUR_FIELDS)
        groups.setdefault(key, dict(fields={field: row.get(field) for field in WEAPON_BEHAVIOUR_FIELDS},
                                   weapons=[]))['weapons'].append(int(row['id']))
    return groups


# ---------------------------------------------------------------------------------------------
# Legal mutation primitives
# ---------------------------------------------------------------------------------------------

def _human_units(scenario):
    return [unit for unit in scenario['ownUnits'] if unit.get('human')]


def add_unit(scenario, source_name):
    """Clone a synthetic combat vector into one more independently mutable fighter slot."""
    probe = _clone(scenario)
    source = _unit_named(probe, source_name)
    unit = _clone(source)
    suffix = 1
    names = {entry['name'] for entry in probe['ownUnits']}
    while f'Synthetic {suffix}' in names:
        suffix += 1
    unit['name'] = f'Synthetic {suffix}'
    if sum(110 in entry['skills'] for entry in _human_units(probe)) >= 2:
        kept = [(skill, level) for skill, level in zip(unit['skills'], unit['invocationLevels'])
                if skill != 110]
        unit['skills'] = [skill for skill, _ in kept]
        unit['invocationLevels'] = [level for _, level in kept]
    probe['ownUnits'].append(unit)
    check_scenario(probe)
    return probe


def remove_unit(scenario, unit_name):
    """Drop one synthetic fighter, retaining at least one human."""
    probe = _clone(scenario)
    if len(_human_units(probe)) <= 1:
        raise ContractError('the synthetic team must retain at least one human')
    _unit_named(probe, unit_name)
    probe['ownUnits'] = [unit for unit in probe['ownUnits'] if unit['name'] != unit_name]
    if unit_name in (probe.get('prePlacement') or {}):
        raise ContractError('a captured pre-placement cannot lose a fighter')
    status = (probe.get('startProfile') or {}).get('startingStatus') or {}
    status.pop(unit_name, None)
    check_scenario(probe)
    return probe


def _clone(scenario):
    return deepcopy(scenario)


def _unit_named(scenario, name):
    for unit in scenario['ownUnits']:
        if unit.get('name') == name:
            if not unit.get('human'):
                raise ContractError(f'{name!r} is not a human unit; the contract searches human skills')
            return unit
    raise ContractError(f'{name!r} is not in this scenario')


def add_skill(scenario, unit_name, skill_id, position=None, trigger=1):
    """Add one approved skill at `position` (default: the end). Order is part of the strategy."""
    approved = whitelist()
    if skill_id not in approved:
        raise ContractError(f'skill {skill_id} is outside this encounter\'s approved search set')
    probe = _clone(scenario)
    unit = _unit_named(probe, unit_name)
    if skill_id in unit['skills']:
        raise ContractError(f'{unit_name}: {approved[skill_id]} is already carried; duplicates are '
                            'illegal even with different trigger settings')
    if len(unit['skills']) >= MAX_SKILLS_PER_HUMAN:
        raise ContractError(f'{unit_name}: already carries {MAX_SKILLS_PER_HUMAN} skills')
    if is_formation_skill(skill_id) and any(is_formation_skill(other) for other in unit['skills']):
        raise ContractError(f'{unit_name}: a second formation skill has no effect')
    index = len(unit['skills']) if position is None else int(position)
    if not 0 <= index <= len(unit['skills']):
        raise ContractError(f'{unit_name}: position {index} is outside the skill list')
    unit['skills'].insert(index, int(skill_id))
    # A formation skill is never selected in combat, so it keeps the recovered default trigger.
    unit['invocationLevels'].insert(index, 1 if is_formation_skill(skill_id) else int(trigger))
    check_scenario(probe)
    return probe


def remove_skill(scenario, unit_name, index):
    """Remove one skill by position."""
    probe = _clone(scenario)
    unit = _unit_named(probe, unit_name)
    if not 0 <= int(index) < len(unit['skills']):
        raise ContractError(f'{unit_name}: no skill at position {index}')
    del unit['skills'][int(index)]
    del unit['invocationLevels'][int(index)]
    check_scenario(probe)
    return probe


def replace_skill(scenario, unit_name, index, skill_id, trigger=1):
    """Replace the skill in one slot with another approved skill."""
    approved = whitelist()
    if skill_id not in approved:
        raise ContractError(f'skill {skill_id} is outside this encounter\'s approved search set')
    probe = _clone(scenario)
    unit = _unit_named(probe, unit_name)
    if not 0 <= int(index) < len(unit['skills']):
        raise ContractError(f'{unit_name}: no skill at position {index}')
    if skill_id == unit['skills'][int(index)]:
        raise ContractError(f'{unit_name}: slot {index} already carries {approved[skill_id]}')
    probe = remove_skill(probe, unit_name, index)
    return add_skill(probe, unit_name, skill_id, position=int(index), trigger=trigger)


def move_skill(scenario, unit_name, from_index, to_index):
    """Move a skill to another position; the ordered list is the strategy, so this is a real move."""
    probe = _clone(scenario)
    unit = _unit_named(probe, unit_name)
    count = len(unit['skills'])
    if not 0 <= int(from_index) < count or not 0 <= int(to_index) < count:
        raise ContractError(f'{unit_name}: positions {from_index}->{to_index} are outside the list')
    if int(from_index) == int(to_index):
        raise ContractError(f'{unit_name}: moving a skill onto itself changes nothing')
    skill_id = unit['skills'].pop(int(from_index))
    level = unit['invocationLevels'].pop(int(from_index))
    unit['skills'].insert(int(to_index), skill_id)
    unit['invocationLevels'].insert(int(to_index), level)
    check_scenario(probe)
    return probe


def set_trigger(scenario, unit_name, index, level):
    """Change one skill's trigger setting. Trigger is part of the candidate's identity."""
    if int(level) not in TRIGGER_LEVELS:
        raise ContractError(f'trigger setting {level} is not one of {TRIGGER_LEVELS}')
    probe = _clone(scenario)
    unit = _unit_named(probe, unit_name)
    if not 0 <= int(index) < len(unit['skills']):
        raise ContractError(f'{unit_name}: no skill at position {index}')
    skill_id = unit['skills'][int(index)]
    if is_formation_skill(skill_id):
        raise ContractError(f'{unit_name}: formation skills do not take an ordinary trigger search')
    if unit['invocationLevels'][int(index)] == int(level):
        raise ContractError(f'{unit_name}: skill {skill_id} already triggers at {level}')
    unit['invocationLevels'][int(index)] = int(level)
    check_scenario(probe)
    return probe


def set_stat(scenario, unit_name, stat, value):
    """Set one stat to an effective value inside its synthetic walls.

    The searched number is the *effective* value the runner reports: what the fight starts the
    parameter at (`battle_value`). It is written through `set_effective_parameter`, which cancels the
    carrier equipment's contribution in the extra channel - so a synthetic value is reachable with any
    weapon behaviour and no item ever adds itself on top of the requested number.
    """
    parameter_id = stat_parameter(stat)
    minimum, maximum = stat_bounds()[parameter_id]
    target = int(value)
    if not minimum <= target <= maximum:
        raise ContractError(f'{stat} target {target} is outside the derived interval '
                            f'[{minimum}, {maximum}]')
    probe = _clone(scenario)
    unit = _unit_named(probe, unit_name)
    if stat == 'int' and not stat_is_searchable(probe, unit, 'int'):
        raise ContractError(f'{unit_name}: INT is only searchable while a magic attack skill is '
                            'equipped')
    entry = _parameter_entry(unit, parameter_id)
    if entry is None:
        raise ContractError(f'{unit_name}: carries no parameter {parameter_id}')
    current = battle_value(probe, unit, parameter_id)
    if current is None:
        raise ContractError(f'{unit_name}: the runner reports no effective {stat}')
    if current == target:
        raise ContractError(f'{unit_name}: {stat} is already {target}')
    set_effective_parameter(probe, unit, parameter_id, target)
    achieved = battle_value(probe, unit, parameter_id)
    if achieved != target:
        raise ContractError(f'{unit_name}: {stat} target {target} did not survive the runner\'s own '
                            f'arithmetic (it reads {achieved})')
    return probe


def set_formation_skill(scenario, unit_name, skill_id):
    """Set (or clear) the unit's formation skill, which must be the first type-60 skill it carries."""
    probe = _clone(scenario)
    unit = _unit_named(probe, unit_name)
    existing = [index for index, skill in enumerate(unit['skills']) if is_formation_skill(skill)]
    for index in reversed(existing):
        del unit['skills'][index]
        del unit['invocationLevels'][index]
    if skill_id is not None:
        if skill_id not in FORMATION_SKILLS:
            raise ContractError(f'{skill_id} is not a recovered formation skill')
        probe = add_skill(probe, unit_name, skill_id, position=0, trigger=1)
    check_scenario(probe)
    return probe


def set_weapon(scenario, unit_name, weapon_id):
    """Switch to another weapon, compensating the stats so the searched values do not move.

    The weapon is part of the equipment list as well as `weaponId` (the validator requires it), so
    both are updated, and every non-bounded core stat is re-anchored to the value it had before.
    """
    rows = profiles()['equipment']
    row = rows.get(int(weapon_id))
    if row is None or int(row.get('category', 0)) != 0:
        raise ContractError(f'{weapon_id} is not a recovered weapon')
    probe = _clone(scenario)
    unit = _unit_named(probe, unit_name)
    # Every stat the contract searches must survive the swap, INT included: a weapon carrier's
    # contribution is cancelled by rewriting each parameter's compensation for the new equipment.
    before = {pid: battle_value(probe, unit, pid) for pid in _searched_parameters().values()}
    slots = [slot for slot in unit['equipment'] if int(slot['id']) != int(unit['weaponId'])]
    old = next((slot for slot in unit['equipment'] if int(slot['id']) == int(unit['weaponId'])), None)
    level = int(old['level']) if old else 1
    affinity = old['affinity'] if old else 0
    slots.append(dict(id=int(weapon_id), level=level, affinity=affinity))
    unit['equipment'] = slots
    unit['weaponId'] = int(weapon_id)
    for stat, pid in _searched_parameters().items():
        target = before.get(pid)
        if target is None:
            continue
        current = battle_value(probe, unit, pid)
        if current == target:
            continue
        set_effective_parameter(probe, unit, pid, int(target))
    return probe


# ---------------------------------------------------------------------------------------------
# The deterministic mutation dispatcher
# ---------------------------------------------------------------------------------------------

#: One name per legal mutation family. The optimiser rotates through them; the learning redesign
#: decides later which to prefer, so nothing here weights them beyond a rotation.
MUTATION_OPS = ('add-skill', 'remove-skill', 'replace-skill', 'move-skill', 'set-trigger',
                'set-stat', 'set-formation-skill', 'set-weapon', 'reorder-roster',
                'add-unit', 'remove-unit')


def placement_signature(scenario):
    """Where the recovered formation function actually puts every own unit.

    `combat_initial_state.formation` sorts by (priority, -effective defence, incoming index), so a
    roster reorder only moves a unit when it flips two units that differ in content - comparing this
    signature is how a placement mutation that changes nothing is caught before it is simulated.
    Units are keyed by their *content*, not their name, because swapping two identical fighters is
    exactly the no-op this has to catch (their names would otherwise make the swap look real).
    """
    from combat_setup import prepare_setup
    prepared = prepare_setup(deepcopy(scenario))
    # `prepared['ownUnits']` rows carry `grid` as the placement index (an int) and `cell` as a
    # two-element list, so only `cell` is iterable. Wrapping `grid` raised on every roster reorder.
    by_name = {unit['name']: unit for unit in scenario['ownUnits']}
    signature = []
    for row in prepared['ownUnits']:
        source = by_name.get(row['name'], {})
        content = _canonical({key: value for key, value in source.items() if key != 'name'})
        signature.append((hashlib.sha256(content.encode('utf-8')).hexdigest()[:16],
                          int(row['grid']), tuple(row['cell'])))
    return tuple(signature)


def reorder_roster(scenario, first, second):
    """Swap two own units' positions in the roster (the input that decides placement)."""
    probe = _clone(scenario)
    units = probe['ownUnits']
    if not (0 <= int(first) < len(units) and 0 <= int(second) < len(units)):
        raise ContractError('roster positions are outside the own-unit list')
    if int(first) == int(second):
        raise ContractError('swapping a unit with itself changes nothing')
    units[int(first)], units[int(second)] = units[int(second)], units[int(first)]
    if placement_signature(probe) == placement_signature(scenario):
        raise ContractError('that roster swap leaves every placement unchanged')
    return probe


def _draw(seed):
    import random
    return random.Random(int(seed) & 0xffffffff)


def _candidates_for_skill(skill_id, scenario, rng):
    """Which humans could legally receive this skill, and where."""
    options = []
    for unit in _human_units(scenario):
        if skill_id in unit['skills'] or len(unit['skills']) >= MAX_SKILLS_PER_HUMAN:
            continue
        if is_formation_skill(skill_id) and any(
                is_formation_skill(other) for other in unit['skills']):
            continue
        row = skill_row(skill_id)
        if int(row['requiredEquipType']) != -1 and weapon_type_of(scenario, unit) != int(
                row['requiredEquipType']):
            continue
        for position in range(len(unit['skills'])+1):
            options.append((unit['name'], position))
    rng.shuffle(options)
    return options


def _stray_skills(scenario):
    """Approved skills nobody carries yet."""
    carried = {skill for unit in _human_units(scenario) for skill in unit['skills']}
    return [skill for skill in sorted(whitelist()) if skill not in carried]


def mutate(scenario, seed, op=None, stat=None, value=None):
    """One legal, interpretable mutation of `scenario`; raises `ContractError` when it cannot.

    The op is chosen by a rotation over `MUTATION_OPS` so every legal dimension is reachable from any
    starting point, including a baseline that carries none of the approved skills. The result is always
    re-validated by `check_scenario`, and a mutation that leaves the search-space identity unchanged is
    refused here - before any battle is spent on it.

    `op` (and `stat`/`value` for the `set-stat` op) let the caller name the dimension it wants - the
    learner chooses the operator and the numeric target from measured results and reports which one a
    candidate came from; `None` keeps the internal rotation. A supplied `value` is still validated
    against the synthetic walls like any other target, so a learned step cannot leave the space.
    """
    rng = _draw(seed)
    if op is not None and op not in MUTATION_OPS:
        raise ContractError(f'unknown mutation op {op!r}')
    order = [op] if op is not None else list(MUTATION_OPS)
    rng.shuffle(order)
    reasons = []
    start_identity = search_identity(scenario)
    for op in order:
        try:
            probe = _apply_op(op, scenario, rng, stat=stat, value=value)
        except ContractError as error:
            reasons.append(f'{op}: {error}')
            continue
        if probe is None:
            reasons.append(f'{op}: no legal move')
            continue
        if search_identity(probe) == start_identity:
            reasons.append(f'{op}: the mutation left the search-space identity unchanged')
            continue
        check_scenario(probe)
        return probe
    raise ContractError('no legal mutation of this scenario: ' + '; '.join(reasons[:4]))


def _apply_op(op, scenario, rng, stat=None, value=None):
    stat_filter = stat
    humans = _human_units(scenario)
    if not humans:
        raise ContractError('this scenario has no human unit to search')
    if op == 'add-unit':
        for unit in rng.sample(humans, len(humans)):
            try:
                return add_unit(scenario, unit['name'])
            except ContractError:
                continue
        raise ContractError('no additional fighter fits the encounter')
    if op == 'remove-unit':
        for unit in rng.sample(humans, len(humans)):
            try:
                return remove_unit(scenario, unit['name'])
            except ContractError:
                continue
        raise ContractError('the team cannot lose another fighter')
    if op == 'add-skill':
        stray = _stray_skills(scenario)
        rng.shuffle(stray)
        for skill_id in stray:
            options = _candidates_for_skill(skill_id, scenario, rng)
            if options:
                unit_name, position = options[0]
                trigger = rng.choice(list(TRIGGER_LEVELS))
                return add_skill(scenario, unit_name, skill_id, position=position, trigger=trigger)
        raise ContractError('every approved skill is already carried')
    if op == 'remove-skill':
        options = [(unit['name'], index) for unit in humans
                   for index in range(len(unit['skills']))]
        rng.shuffle(options)
        for unit_name, index in options:
            try:
                return remove_skill(scenario, unit_name, index)
            except ContractError:
                continue
        raise ContractError('no removable skill')
    if op == 'replace-skill':
        options = [(unit['name'], index, skill_id) for unit in humans
                   for index, skill_id in enumerate(unit['skills'])
                   for skill_id in sorted(whitelist()) if skill_id != unit['skills'][index]]
        rng.shuffle(options)
        for unit_name, index, skill_id in options:
            try:
                return replace_skill(scenario, unit_name, index, skill_id,
                                     trigger=rng.choice(list(TRIGGER_LEVELS)))
            except ContractError:
                continue
        raise ContractError('no legal replacement')
    if op == 'move-skill':
        options = [(unit['name'], a, b) for unit in humans for a in range(len(unit['skills']))
                   for b in range(len(unit['skills'])) if a != b]
        rng.shuffle(options)
        for unit_name, a, b in options:
            try:
                return move_skill(scenario, unit_name, a, b)
            except ContractError:
                continue
        raise ContractError('no legal reorder')
    if op == 'set-trigger':
        options = [(unit['name'], index, level) for unit in humans
                   for index, skill_id in enumerate(unit['skills'])
                   if not is_formation_skill(skill_id)
                   for level in TRIGGER_LEVELS
                   if level != unit['invocationLevels'][index]]
        rng.shuffle(options)
        for unit_name, index, level in options:
            try:
                return set_trigger(scenario, unit_name, index, level)
            except ContractError:
                continue
        raise ContractError('no legal trigger change')
    if op == 'set-stat':
        bounds = stat_bounds()
        options = []
        for unit in humans:
            for stat, pid in {**CORE_STATS, **INT_STAT}.items():
                if stat_filter is not None and stat != stat_filter:
                    continue
                if not stat_is_searchable(scenario, unit, stat):
                    continue
                current = battle_value(scenario, unit, pid)
                if current is None:
                    continue
                minimum, maximum = bounds[pid]
                targets = ([int(value)] if value is not None
                           else _stat_targets(current, minimum, maximum, rng))
                for target in targets:
                    options.append((unit['name'], stat, target))
        rng.shuffle(options)
        for unit_name, stat, target in options:
            try:
                return set_stat(scenario, unit_name, stat, target)
            except ContractError:
                continue
        raise ContractError('no legal stat step inside the derived bounds')
    if op == 'set-formation-skill':
        choices = [None]+sorted(FORMATION_SKILLS)
        rng.shuffle(choices)
        for unit in humans:
            for choice in choices:
                try:
                    return set_formation_skill(scenario, unit['name'], choice)
                except ContractError:
                    continue
        raise ContractError('no legal formation-skill change')
    if op == 'set-weapon':
        groups = weapon_groups()
        options = []
        for unit in humans:
            current = weapon_group_id(unit)
            for group, info in groups.items():
                if group == current or not info['weapons']:
                    continue
                options.append((unit['name'], group, info))
        rng.shuffle(options)
        for unit_name, group, info in options:
            weapons = list(info['weapons'])
            rng.shuffle(weapons)
            for weapon_id in weapons:
                try:
                    return set_weapon(scenario, unit_name, weapon_id)
                except ContractError:
                    continue
        raise ContractError('no other weapon behaviour group is reachable')
    if op == 'reorder-roster':
        count = len(scenario['ownUnits'])
        options = [(a, b) for a in range(count) for b in range(count) if a < b]
        rng.shuffle(options)
        for a, b in options:
            try:
                return reorder_roster(scenario, a, b)
            except ContractError:
                continue
        raise ContractError('no roster swap changes the placement')
    raise ContractError(f'unknown mutation op {op!r}')


def _stat_targets(current, minimum, maximum, rng, steps=5):
    """Candidate effective values for one stat: proportional steps, clamped inside the derived walls."""
    factors = (1.0, 1.15, 1.35, 1.6, 0.85, 0.7)
    targets = []
    for factor in factors:
        target = int(round(current*factor))
        target = max(minimum, min(maximum, target))
        if target != current and target not in targets:
            targets.append(target)
    if minimum != current and minimum not in targets:
        targets.append(minimum)
    if maximum != current and maximum not in targets:
        targets.append(maximum)
    rng.shuffle(targets)
    return targets[:steps]


def label_for(scenario, parent, op_hint=None):
    """A short, honest label for a generated candidate (what changed, in the search space's terms)."""
    if parent is None:
        return 'Contract mutation'
    change = describe_change(parent, scenario)
    return f'Contract {change}' if change else 'Contract mutation'


def describe_change(parent, child):
    """The single interpretable difference between two scenarios, or '' when there is none."""
    if len(parent['ownUnits']) != len(child['ownUnits']):
        return f'team {len(parent["ownUnits"])}->{len(child["ownUnits"])}'
    parts = []
    for before, after in zip(parent['ownUnits'], child['ownUnits']):
        if before['name'] != after['name']:
            continue
        if before['skills'] != after['skills']:
            added = [skill for skill in after['skills'] if skill not in before['skills']]
            removed = [skill for skill in before['skills'] if skill not in after['skills']]
            names = whitelist()
            if added and not removed:
                parts.append(f'+{names.get(added[0], added[0])}')
            elif removed and not added:
                parts.append(f'-{names.get(removed[0], removed[0])}')
            elif added and removed:
                parts.append(f'{names.get(removed[0], removed[0])}->{names.get(added[0], added[0])}')
            else:
                parts.append('reorder skills')
        elif before['invocationLevels'] != after['invocationLevels']:
            index = next(i for i, (a, b) in enumerate(
                zip(before['invocationLevels'], after['invocationLevels'])) if a != b)
            parts.append(f'trigger {after["invocationLevels"][index]} on slot {index+1}')
        elif before['weaponId'] != after['weaponId']:
            parts.append(f'weapon {before["weaponId"]}->{after["weaponId"]}')
        else:
            for stat, pid in {**CORE_STATS, **INT_STAT}.items():
                old = (_parameter_entry(before, pid) or {}).get('rawValue')
                new = (_parameter_entry(after, pid) or {}).get('rawValue')
                if old != new:
                    parts.append(f'{stat} {old}->{new}')
                    break
    return ' · '.join(parts)
