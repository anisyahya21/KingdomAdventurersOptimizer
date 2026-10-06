"""Structured strategy names: who changed what, and which build it came from.

The optimiser's own labels were written for a reader of the *search*: ``Student 1 · track:atk ·
atk 343->439`` names the student's internal track and ``Breakthrough · h:19:prior:atk+spd · arm C ·
damage (low)`` leads with a hypothesis id and an arm code. Neither is a description of the build. The
person looking at the strategies window needs the opposite order of information:

    <student making the change> · <unit and actual old->new change> · from <immediate parent's producer>

For example::

    Average · Ninja HP 17179->19287 · from Community
    Breakthrough · Ninja ATK 40->20; Speed 132->66 · from Average
    Breakthrough · Ninja Speed 66->48 · from Breakthrough

Everything here is *derived* from structured evidence, never parsed out of an old free-text label:

  * the creator comes from the candidate's lineage ``source`` (which stream recorded the child);
  * the immediate parent and its producer come from the lineage ``parent`` row, not the root, so a
    child of a child names its own parent and not the original ancestor;
  * the change is recomputed by diffing the parent's and the child's stored scenarios, so a joint
    intervention reports both of its stats rather than whichever one a label happened to mention.

Long hypothesis ids, arm codes and provenance markers are *not* dropped: `name_for` returns them in
`detail`, which the surface shows in the tooltip. A build whose provenance cannot be established (no
lineage row, or a parent that was never recorded) is named ``Unknown source`` rather than guessed.
"""
from __future__ import annotations

from functools import lru_cache

#: The stream that recorded a child, as `strategy_students` / `strategy_breakthrough` write it on the
#: lineage row, mapped to the name the surface shows. The registry keys students by *name* rather than
#: number, so the numbers the interface uses for them ("Student 1", "Student 9") stay in the UI layer.
STREAM_NAMES = {
    'community': 'Community',
    'rebel': 'Rebel',
    'stumble': 'Stumble',
    'average': 'Average',
    'mechanism': 'Breakthrough',
    'discovery': 'Discovery',
    'freewill': 'Freewill',
    # The MP-recovery corrective experiment: a general operation that notices a low MP pool and tests
    # an item-supported build. It is its own requesting owner, never Breakthrough, because Breakthrough
    # did not ask for it.
    'mp-recovery': 'MP recovery',
}

#: Lineage sources that belong to the ordinary search rather than to a named student: the learner
#: writes the lane or reason that nominated the parent ('earned', 'local:atk', 'reseed:elite', ...).
#: They are all the same producer - the open search - so the name says so instead of inventing a
#: student, and the raw marker is preserved in `detail`.
SEARCH_MARKERS = ('fresh', 'region', 'earned', 'potential', 'setup', 'efficiency', 'exploration',
                  'local:', 'reseed:', 'fine-tune:', 'ladder')

#: The supplied/imported build every search starts from. It has no producer that made a change.
ORIGIN_MARKER = 'origin'

#: What the name says when provenance is genuinely absent. Never a guess.
UNKNOWN_SOURCE = 'Unknown source'

#: Canonical stat keys (`search_contract.CORE_STATS`) to the label the surface shows.
STAT_LABELS = {'hp': 'HP', 'mp': 'MP', 'atk': 'ATK', 'def': 'DEF', 'spd': 'Speed',
               'lck': 'LCK', 'dex': 'DEX', 'int': 'INT'}

#: `rawValue` is the number the battle reads, so it is the number in the name.
STAT_PARAMETER_IDS = {'hp': 10, 'mp': 11, 'atk': 13, 'def': 14, 'spd': 15, 'lck': 16, 'int': 18,
                      'dex': 19}

MAX_NAME = 160


def producer_name(source):
    """The human-readable producer of a lineage ``source``, or None when there is no evidence.

    A named student maps to its own name; an ordinary-search marker maps to ``Discovery``; the
    supplied baseline maps to ``Baseline``; anything else is ``None``, which the caller renders as
    `Unknown source` rather than as a misleading student name.
    """
    if not source:
        return None
    text = str(source)
    if text in STREAM_NAMES:
        return STREAM_NAMES[text]
    if text == ORIGIN_MARKER:
        return 'Baseline'
    if text.startswith(SEARCH_MARKERS):
        return STREAM_NAMES['discovery']
    return None


def unit_label(raw_name):
    """The unit's short name: ``Ninja (A aw20)`` names the build, ``Ninja`` names the unit."""
    if not raw_name:
        return 'unit'
    text = str(raw_name).strip()
    for separator in (' (', '('):
        head, found, _tail = text.partition(separator)
        if found and head.strip():
            return head.strip()
    return text


@lru_cache(maxsize=1)
def _skill_names():
    """{skill id: display name} from the recovered whitelist, or an empty map if it is unavailable."""
    try:
        import search_contract
        return dict(search_contract.whitelist())
    except Exception:
        return {}


def _parameter(unit, parameter_id):
    entry = (unit.get('parameters') or {}).get(str(parameter_id))
    return (entry or {}).get('rawValue')


def unit_change(before, after):
    """Every readable difference between one unit's two states, as display parts.

    A joint intervention changes two stats and both are reported - the label the search wrote only
    ever named the first one. The order is fixed (skills, trigger, weapon, then stats in canonical
    order) so the same pair always reads the same way.
    """
    parts = []
    skills_before, skills_after = before.get('skills') or [], after.get('skills') or []
    if skills_before != skills_after:
        added = [skill for skill in skills_after if skill not in skills_before]
        removed = [skill for skill in skills_before if skill not in skills_after]
        names = _skill_names()
        if added and not removed:
            parts.append('+' + str(names.get(added[0], added[0])))
        elif removed and not added:
            parts.append('-' + str(names.get(removed[0], removed[0])))
        elif added and removed:
            parts.append(f'{names.get(removed[0], removed[0])}->{names.get(added[0], added[0])}')
        else:
            parts.append('skill order')
    triggers_before = before.get('invocationLevels') or []
    triggers_after = after.get('invocationLevels') or []
    if triggers_before != triggers_after:
        if len(triggers_before) != len(triggers_after):
            parts.append('triggers changed')
        else:
            index = next(i for i, (a, b) in enumerate(zip(triggers_before, triggers_after)) if a != b)
            parts.append(f'trigger {triggers_after[index]} on slot {index + 1}')
    if before.get('weaponId') != after.get('weaponId'):
        parts.append(f'weapon {before.get("weaponId")}->{after.get("weaponId")}')
    for stat, parameter_id in STAT_PARAMETER_IDS.items():
        old = _parameter(before, parameter_id)
        new = _parameter(after, parameter_id)
        if old != new:
            parts.append(f'{STAT_LABELS[stat]} {old}->{new}')
    return parts


def describe(parent, child):
    """The actual old->new change between two stored scenarios, unit by unit.

    Returns '' when the two scenarios are indistinguishable to a reader (which for two candidates of
    one library should not happen: identical scenarios share an identity). A team-size change or a
    roster reorder is reported as such rather than as a per-unit stat change, because there is no one
    unit whose numbers moved.

    An item policy is a change like any other: a child that differs only in its Holy Herb stock, use cap
    or watched units would otherwise be named as if nothing had been changed, which is precisely the
    information a reader comparing an item-free parent with its item-supported child needs.
    """
    if not parent or not child:
        return ''
    policy = policy_change(parent, child)
    parent_units = parent.get('ownUnits') or []
    child_units = child.get('ownUnits') or []
    if len(parent_units) != len(child_units):
        head = f'team {len(parent_units)}->{len(child_units)}'
        return f'{head}; {policy}' if policy else head
    groups = []
    for before, after in zip(parent_units, child_units):
        if before.get('name') != after.get('name'):
            return f'roster changed; {policy}' if policy else 'roster changed'
        parts = unit_change(before, after)
        if parts:
            groups.append(f'{unit_label(after.get("name"))} ' + '; '.join(parts))
    units = ', '.join(groups)
    if units and policy:
        return f'{units}; {policy}'
    return units or policy


def policy_change(parent, child):
    """The declared consumable policy difference between two scenarios, or '' when there is none.

    Only the fields that change what a battle may *do* are reported here: the stock, the per-battle use
    cap and which units may authorise a charge, plus declared battle items and their stock. The
    observation-only `mpWatchUnits` list is deliberately absent - it cannot change an outcome, so it is
    not a change to a build.
    """
    parts = []
    for field, label in (('holyHerbStock', 'Holy Herb stock'),
                         ('holyHerbMaxUses', 'Holy Herb max uses')):
        before = int(parent.get(field) or 0)
        after = int(child.get(field) or 0)
        if before != after:
            parts.append(f'{label} {before}->{after}')
    watched_before = [str(name) for name in parent.get('holyHerbTriggerUnits') or []]
    watched_after = [str(name) for name in child.get('holyHerbTriggerUnits') or []]
    if watched_before != watched_after:
        parts.append('herb watch ' + (', '.join(watched_after) if watched_after else 'none'))
    items_before = set(parent.get('items') or {})
    items_after = set(child.get('items') or {})
    if items_before != items_after:
        added = ', '.join(sorted(items_after - items_before)) or 'none'
        removed = ', '.join(sorted(items_before - items_after)) or 'none'
        parts.append(f'items +{added} -{removed}')
    elif parent.get('itemStock') != child.get('itemStock'):
        parts.append('item stock changed')
    return '; '.join(parts)


def normalise_stored(change):
    """An older row's stored change text, used only when the change cannot be recomputed.

    A parent that is no longer resident has no scenario to diff against, but the lineage row still
    holds the before/after the search recorded when it made the child ('atk 46->37'). That text is
    honest, so it is shown as it was stored; the placeholder wording a root row carries is not a
    change and is reported as none.
    """
    if not change:
        return ''
    text = str(change).strip()
    if text in ('origin', 'supplied build'):
        return ''
    return text


def name_for(candidate_id, lineage, scenarios, fallback=None):
    """One candidate's structured name and the evidence behind it.

    `lineage` is ``{candidate id: row}`` (the `lineage` table, which survives pruning) and `scenarios`
    is ``{candidate id: scenario}`` for whatever resident rows are available. `fallback` is the
    candidate's stored label, used unchanged for a root build - a fresh team or an imported build has
    no change to describe and its own label is already the honest answer.

    Returns a dict:

      * `name` - the string to show;
      * `creator` - the producing stream's name, or `Unknown source`;
      * `change` - the recomputed change, or the stored one, or None for a root;
      * `parent` / `parentProducer` - the immediate parent's id and producer;
      * `derived` - True when creator, parent and change all came from structured evidence;
      * `detail` - the raw provenance (source marker, parent id, stored label) for the tooltip.
    """
    row = lineage.get(candidate_id) or {}
    parent_id = row.get('parent')
    creator = producer_name(row.get('source'))
    parent_row = (lineage.get(parent_id) or {}) if parent_id else {}
    parent_producer = producer_name(parent_row.get('source'))
    child_scenario = scenarios.get(candidate_id)
    parent_scenario = scenarios.get(parent_id) if parent_id else None
    change = ''
    if parent_id and parent_scenario and child_scenario:
        change = describe(parent_scenario, child_scenario)
    if parent_id and not change:
        change = normalise_stored(row.get('change'))

    detail_parts = []
    if row.get('source'):
        detail_parts.append(f'source {row["source"]}')
    if row.get('operation'):
        detail_parts.append(f'operation {row["operation"]}')
    if parent_id:
        detail_parts.append(f'parent {parent_id[:12]}')
    if fallback:
        detail_parts.append(str(fallback))

    derived = bool(parent_id and creator and parent_producer and change)
    if not parent_id:
        # A root: a fresh team or a supplied build. It has no provenance to state and its own label
        # already describes it, so that label is the name. Nothing is relabelled as new work.
        return dict(name=str(fallback or UNKNOWN_SOURCE)[:MAX_NAME], creator=creator,
                    change=None, parent=None, parentProducer=None, derived=False,
                    detail=' · '.join(detail_parts))

    head = creator or UNKNOWN_SOURCE
    tail = f'from {parent_producer}' if parent_producer else f'from {UNKNOWN_SOURCE}'
    body = change or 'change not recorded'
    name = f'{head} · {body} · {tail}'
    return dict(name=name[:MAX_NAME], creator=creator, change=change or None, parent=parent_id,
                parentProducer=parent_producer, derived=derived,
                detail=' · '.join(detail_parts))
