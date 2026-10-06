"""Active threshold and range discovery for the strategy optimiser.

A search that only reports the builds it happened to test cannot answer "I only have 325 attack - can
I still use this?". This module supplies the missing half: named axes taken from the simulator's own
inputs, a ladder of values to probe around a chosen strategy, and an analysis that turns the
measured points into a boundary with its evidence.

Two rules keep it honest:

  * A boundary is only reported between two *measured* points, one that still works and one that
    does not. A range is never inferred from where successful candidates happened to sit, and the
    resolution is stated as the probed spacing.
  * Every comparison is paired on the shared seed bank, because the same seeds are run at every
    probed value. Paired differences are what make a 2-chest effect detectable at all; independent
    intervals over these per-run spreads are not.

Nothing here is a new game fact. An axis names a field the scenario already carries, and the module
reports the *effective* stat the engine computed for that input, so a boundary can be read in the
player's own numbers.
"""
from __future__ import annotations

import math
import statistics
from copy import deepcopy

# Canonical resident stat keys -> native combat parameter ids, from the site's recovered
# `stat-parameter-ids.ts` (Attack 13, Defence 14, HP 10, MP 11, Agility 15, Luck 16, Dexterity 19,
# Intelligence 18). The equipment-only slots that table also lists - Gathering 20, Move 21, Love 22 -
# are deliberately absent: no recovered damage or hit formula reads them, so they never become a
# probe axis and never join a generated stat mutation.
STAT_AXES = {
    'atk': (13, 'Attack'),
    'def': (14, 'Defence'),
    'hp': (10, 'HP'),
    'mp': (11, 'MP'),
    'spd': (15, 'Agility'),
    'lck': (16, 'Luck'),
    'dex': (19, 'Dexterity'),
    'int': (18, 'Intelligence'),
}

#: The bounded vitals parameter that reaches combat through the same `fighter_parameter` path as HP
#: and MP but is not one of the seven core stats the search contract rotates over: native parameter 12
#: "Energy" (the site's `vig`). It is offered as an *extra* axis so a fine-tuning program can sweep
#: it, and it is kept out of `axis_names()` so the canonical probe axis set the optimiser already
#: rotates over does not change underneath it. Nothing here claims a recovered damage or hit formula
#: reads parameter 12; a program that measures it reports what it saw, not why.
EXTRA_STAT_AXES = {
    'vig': (12, 'Energy'),
}
INPUT_AXES = {
    'herbs': ('holyHerbStock', 'Holy Herb stock'),
}

#: Axes that only move combat damage while the unit can express them. Intelligence is the one case:
#: `combat_resolution.damage_from_parameters` reads parameter 18 for a magical hit and parameter 13
#: otherwise, so INT is a real damage axis exactly while the unit carries a magic attack skill
#: (`search_contract.stat_is_searchable`). Every other axis is unconditional.
CONDITIONAL_AXES = {'int'}

AXES = {**{name: dict(parameter=pid, label=label, established=True,
                      requires_magic_attack=name in CONDITIONAL_AXES)
           for name, (pid, label) in {**STAT_AXES, **EXTRA_STAT_AXES}.items()},
        **{name: dict(input=field, label=label, established=True, requires_magic_attack=False)
           for name, (field, label) in INPUT_AXES.items()}}

#: The canonical probe axes (resolved info): the seven core stats, the conditional INT, and the
#: consumable input. `axis_names()` reports exactly these. `EXTRA_STAT_AXES` resolve through
#: `axis_info` but are not part of the set the optimiser's own probe rotation enumerates.
PROBE_AXES = {name: AXES[name] for name in (*STAT_AXES, *INPUT_AXES)}

PROBE_SOURCE = 'probe'
PROBE_LABEL_PREFIX = 'Probe · '


class ProbeError(ValueError):
    """A probe the optimiser refuses to create, with the reason."""


def axis_names():
    """Every axis a probe may use: the core combat stats, the conditional INT, and the herb input."""
    return sorted(PROBE_AXES)


def axis_info(axis):
    info = AXES.get(axis)
    if info is None:
        raise ProbeError(f'unknown probe axis {axis!r}; known axes are {", ".join(axis_names())}')
    return info


def parameter_entry(unit, parameter_id):
    """A unit's parameter block, whether the scenario keys parameters by int or by string.

    The in-memory fixture keys them as integers and the same scenario round-tripped through a stored
    library keys them as strings, so both spellings have to resolve or a probe works in one place and
    fails in the other.
    """
    parameters = unit.get('parameters') or {}
    if str(parameter_id) in parameters:
        return parameters[str(parameter_id)]
    return parameters.get(parameter_id)


def unit_names(scenario):
    return [unit.get('name') for unit in scenario.get('ownUnits') or []]


def require_unit(scenario, unit_name):
    for unit in scenario.get('ownUnits') or []:
        if unit.get('name') == unit_name:
            if not bool(unit.get('human')):
                raise ProbeError(f'unit {unit_name!r} is not a human unit; only human stat inputs are '
                                 'probed')
            return unit
    raise ProbeError(f'unit {unit_name!r} is not in this scenario; it has {", ".join(unit_names(scenario))}')


def unit_has_magic_attack(scenario, unit):
    """True when the unit attacks magically, via the contract's own conditional-INT rule.

    `search_contract.stat_is_searchable(scenario, unit, 'int')` is the canonical classification: INT
    is searchable exactly while a magic attack skill is equipped (`MAGIC_SKILL_IDS`). Reusing it keeps
    the probe from growing a second magic-skill list that could drift from the search-space contract.
    """
    from search_contract import stat_is_searchable
    return bool(stat_is_searchable(scenario, unit, 'int'))


def axis_needs_magic_attack(axis):
    """True for a conditional axis that only moves damage while the unit is a magic attacker."""
    info = AXES.get(axis)
    return bool(info and info.get('requires_magic_attack'))


def require_probe_unit(scenario, unit_name, axes):
    """The unit a probe may run on, refusing one that cannot express a requested axis.

    Intelligence is the only conditional axis: `combat_resolution.damage_from_parameters` reads
    parameter 18 for a magical hit and parameter 13 otherwise, so probing INT on a unit with no magic
    attack skill would vary a number the fight never reads. That unit is refused with the reason.
    """
    unit = require_unit(scenario, unit_name)
    conditional = [axis for axis in axes if axis and axis_needs_magic_attack(axis)]
    if conditional and not unit_has_magic_attack(scenario, unit):
        labels = ' and '.join(AXES[axis]['label'] for axis in conditional)
        raise ProbeError(
            f'{unit_name!r} carries no magic attack skill, so its damage is read from Attack '
            f'(parameter 13) and {labels} is not a combat input for it. Probe a human unit that '
            'carries a magic attack skill, or equip one first.')
    return unit


def default_probe_unit(scenario, axes):
    """The default human for a probe: a magic attacker when a conditional axis needs one.

    INT only moves damage for a magic attacker, so defaulting to the first human would build a ladder
    that cannot move the number it measures. When a magic attacker is present it is chosen; when none
    is, the first human is returned and `require_probe_unit` reports why it cannot be used.
    """
    humans = [unit for unit in scenario.get('ownUnits') or [] if unit.get('human')]
    if not humans:
        return None
    if any(axis_needs_magic_attack(axis) for axis in axes):
        for unit in humans:
            if unit_has_magic_attack(scenario, unit):
                return unit['name']
    return humans[0]['name']


def resolve_probe_unit(scenario, axes, requested=None):
    """The unit a probe runs on, from an explicit request or the default, after eligibility checks.

    Both axes of a two-axis grid are checked, so an INT grid on a non-magic unit is refused exactly
    like a single INT ladder. Input-only probes (herb stock) need no unit and pass the request through
    untouched.
    """
    axes = [axis for axis in axes if axis]
    if not any('parameter' in AXES.get(axis, {}) for axis in axes):
        return requested
    unit_name = requested or default_probe_unit(scenario, axes)
    if unit_name is None:
        raise ProbeError('this scenario has no human unit to probe a statistic on')
    require_probe_unit(scenario, unit_name, axes)
    return unit_name


def effective_stat(scenario, unit_name, axis):
    """The engine's own effective value for this unit's axis stat, or None for an input axis.

    Read through the canonical setup preparation, so a probed point can be reported in the numbers
    the fight actually used rather than in the raw input that produced them.
    """
    info = axis_info(axis)
    if 'parameter' not in info:
        return None
    from combat_setup import prepare_setup
    from combat_scenario import ScenarioError
    try:
        prepared = prepare_setup(deepcopy(scenario))
    except ScenarioError:
        return None
    for row in prepared['ownUnits']:
        if row.get('name') == unit_name:
            entry = row['effectiveParameters'].get(info['parameter'])
            return int(entry['value']) if entry else None
    return None


def pivot_value(scenario, unit_name, axis):
    """This unit's current value on the axis: the raw stat input, or the scenario input field."""
    info = axis_info(axis)
    if 'parameter' in info:
        unit = require_probe_unit(scenario, unit_name, [axis])
        entry = parameter_entry(unit, info['parameter'])
        if not entry:
            raise ProbeError(f'unit {unit_name!r} carries no parameter {info["parameter"]} '
                             f'({info["label"]}) to probe')
        return int(entry.get('rawValue') or 0)
    return int(scenario.get(info['input']) or 0)


def apply_axis(scenario, unit_name, axis, value):
    """The same scenario with one axis set to `value`; every other field is untouched."""
    info = axis_info(axis)
    probe = deepcopy(scenario)
    if 'parameter' in info:
        unit = require_probe_unit(probe, unit_name, [axis])
        entry = parameter_entry(unit, info['parameter'])
        if not entry:
            raise ProbeError(f'unit {unit_name!r} carries no parameter {info["parameter"]}')
        entry['rawValue'] = int(value)
        # A bounded parameter (HP/MP/Vigor) must not claim a maximum below its own value.
        if entry.get('rawMax') is not None and info['parameter'] in (10, 11, 12):
            entry['rawMax'] = max(int(entry['rawMax']), int(value))
    else:
        probe[info['input']] = int(value)
    return probe


def default_ladder(scenario, unit_name, axis, points=7):
    """A ladder of probe values around this unit's current one.

    Wide enough to find a fall-off, spaced evenly in *ratio* rather than in absolute units so the
    same ladder is sensible for an attack of 40 and an attack of 400. The pivot value itself is
    always included, so every probe has a like-for-like reference measured on the same seeds.
    """
    pivot = pivot_value(scenario, unit_name, axis)
    if pivot <= 0:
        raise ProbeError(f'the current {axis} value is {pivot}, which gives no ladder to probe')
    ratios = {3: (0.6, 1.0, 1.4), 5: (0.5, 0.75, 1.0, 1.25, 1.5),
              7: (0.4, 0.6, 0.8, 1.0, 1.2, 1.4, 1.6)}.get(points)
    if ratios is None:
        ratios = tuple(0.4 + 1.2*index/(points-1) for index in range(points))
    ladder = sorted({max(1, int(round(pivot*ratio))) for ratio in ratios} | {pivot})
    return ladder


def probe_label(axis, unit_name, value, pivot_label):
    info = axis_info(axis)
    subject = unit_name if 'parameter' in info else 'scenario'
    return f'{PROBE_LABEL_PREFIX}{info["label"]} {value} · {subject} · {pivot_label}'


def is_probe_candidate(candidate):
    return (candidate.get('source') == PROBE_SOURCE
            or str(candidate.get('label') or '').startswith(PROBE_LABEL_PREFIX))


def pivot_label_of(label):
    """The pivot label encoded in a probe candidate's label, or None."""
    text = str(label or '')
    if not text.startswith(PROBE_LABEL_PREFIX):
        return None
    parts = text.split(' · ')
    return parts[-1] if len(parts) >= 4 else None


def chest_series(rows):
    """{seed pair: chests} for the resolved runs of one candidate, via the optimiser's own rule.

    Keyed by the run's own seed pair when it carries one, because that is what makes two candidates
    comparable: the same seeds, not the same position in a list.
    """
    from strategy_optimizer import chest_count
    series = {}
    for index, row in enumerate(rows):
        value = chest_count(row)[0]
        if value is not None:
            seeds = row.get('seeds')
            key = tuple(seeds) if isinstance(seeds, (list, tuple)) and len(seeds) == 2 else index
            series[key] = value
    return series


def paired_delta(series, reference):
    """Paired mean difference against the reference over the ordinals both measured."""
    shared = sorted(set(series) & set(reference))
    if len(shared) < 2:
        return None
    differences = [series[ordinal]-reference[ordinal] for ordinal in shared]
    mean = statistics.mean(differences)
    sd = statistics.stdev(differences)
    se = sd/math.sqrt(len(differences)) if sd else 0.
    return dict(n=len(shared), mean=mean, sd=sd, se=se,
                t=(mean/se) if se else None,
                lower=mean-1.96*se, upper=mean+1.96*se)


def paired_status(delta, tolerance=0.0):
    """True=established working, False=established failing, None=still uncertain."""
    if delta is None:
        return None
    if delta['lower'] >= -tolerance:
        return True
    if delta['upper'] < -tolerance:
        return False
    return None


def summarise_point(series):
    values = list(series.values())
    if not values:
        return dict(n=0, chestMean=None, chestSE=None, chestMin=None, chestMax=None)
    mean = statistics.mean(values)
    sd = statistics.stdev(values) if len(values) > 1 else 0.
    return dict(n=len(values), chestMean=mean, chestSD=sd,
                chestSE=(sd/math.sqrt(len(values)) if sd else 0.),
                chestMin=min(values), chestMax=max(values))


def analyse(ladder, reference_series, tolerance=0.0):
    """Turn measured probe points into a boundary statement with its evidence.

    `ladder` is a list of dicts sorted by value, each with `value`, `effective`, `chests` (a
    chest_series mapping) and `label`. The reference is the pivot's own series. A point works only
    when its paired interval establishes non-inferiority. Intervals crossing the boundary are
    unresolved, never silently counted as working builds.
    """
    rows = []
    for point in ladder:
        summary = summarise_point(point['chests'])
        delta = paired_delta(point['chests'], reference_series)
        viable = paired_status(delta, tolerance)
        rows.append(dict(value=point['value'], effective=point.get('effective'), label=point.get('label'),
                         viable=viable, delta=delta, **summary))
    measured = [row for row in rows if row['n'] > 0 and row['viable'] is not None]
    uncertain = [row['value'] for row in rows if row['n'] > 0 and row['viable'] is None]
    viable_values = [row['value'] for row in measured if row['viable']]
    failing = [row for row in measured if not row['viable']]
    transitions = [dict(fromValue=left['value'], toValue=right['value'],
                        fromWorking=left['viable'], toWorking=right['viable'])
                   for left, right in zip(measured, measured[1:])
                   if left['viable'] != right['viable']]
    statement, low, high = None, None, None
    if len(measured) < 2:
        statement = ('not enough conclusive points yet: a boundary needs a confirmed working value '
                     'and a confirmed failing value measured on shared seeds')
    elif not viable_values:
        statement = ('no value on this ladder is within 95% of the reference strategy, including its '
                     'own current value - the ladder may not bracket the strategy')
    else:
        low, high = min(viable_values), max(viable_values)
        parts = [f'confirmed working values on this ladder: {", ".join(map(str, viable_values))}']
        for edge in transitions:
            failure = edge['fromValue'] if not edge['fromWorking'] else edge['toValue']
            working = edge['fromValue'] if edge['fromWorking'] else edge['toValue']
            parts.append(f'stops working at measured {failure} next to working {working}; '
                         f'boundary bracketed between {min(failure, working)} and '
                         f'{max(failure, working)}')
        if not transitions:
            parts.append('no working/failing transition was bracketed')
        if any(low < row['value'] < high for row in failing):
            parts.append('working values are separated by a measured failure; do not treat them as '
                         'one continuous range')
        spacing = sorted({row['value'] for row in measured})
        if len(spacing) > 1:
            steps = [b-a for a, b in zip(spacing, spacing[1:])]
            parts.append(f'boundaries are resolved to the probed spacing of about '
                         f'{int(round(statistics.median(steps)))}')
        if uncertain:
            parts.append(f'uncertain at {", ".join(map(str, uncertain))}; run more shared seeds')
        statement = '; '.join(parts)
    return dict(rows=rows, viableLow=low, viableHigh=high, fails=failing,
                transitions=transitions,
                statement=statement,
                basis=('every point ran the same shared seed bank and is compared to the reference '
                       'strategy by paired differences over the ordinals both measured'))


def next_probe_actions(rows, minimum, maximum, *, max_runs=320, max_new=2):
    """Choose evidence before geography: extend uncertain points, then split every known edge.

    A response can work, fail, then work again. Each adjacent confirmed transition is a separate
    boundary. If no transition is bracketed, expand outside the measured range without inferring
    monotonicity. The caller supplies the axis's actual legal bounds.
    """
    ordered = sorted(rows, key=lambda row: int(row['value']))
    if not ordered:
        return dict(extend=[], values=[])
    extend = [int(row['value']) for row in ordered
              if row.get('viable') is None and 0 < int(row.get('n') or 0) < max_runs]
    if extend:
        return dict(extend=extend[:max_new], values=[])
    existing = {int(row['value']) for row in ordered}
    confirmed = [row for row in ordered if row.get('viable') is not None]
    gaps = []
    for left, right in zip(confirmed, confirmed[1:]):
        a, b = int(left['value']), int(right['value'])
        if left['viable'] != right['viable'] and b-a > 1:
            midpoint = (a+b)//2
            if midpoint not in existing:
                gaps.append((b-a, midpoint))
    if gaps:
        return dict(extend=[], values=[value for _gap, value in
                                       sorted(gaps, reverse=True)[:max_new]])
    low, high = int(ordered[0]['value']), int(ordered[-1]['value'])
    outward = []
    if low > minimum:
        outward.append(max(int(minimum), low-max(1, (high-low)//2, low//2)))
    if high < maximum:
        outward.append(min(int(maximum), high+max(1, (high-low)//2, high//2)))
    return dict(extend=[], values=[value for value in outward
                                   if value not in existing][:max_new])


def candidate_value(label, axis):
    """The probed value encoded in a probe candidate label, or None."""
    # Parsing reads `AXES` directly rather than going through `axis_info`: a stored label only needs
    # the table lookup, and it must not re-run any per-unit eligibility rule, so a stored INT probe
    # stays readable even for a unit that no longer carries a magic attack skill.
    info = AXES[axis]
    marker = f'{PROBE_LABEL_PREFIX}{info["label"]} '
    text = str(label or '')
    if not text.startswith(marker):
        return None
    try:
        return int(text[len(marker):].split(' · ')[0])
    except (ValueError, IndexError):
        return None


def grid_label(axis, value, axis2, value2, unit_name, pivot_label):
    first, second = AXES[axis], AXES[axis2]
    subject = unit_name if 'parameter' in first or 'parameter' in second else 'scenario'
    return (f'{PROBE_LABEL_PREFIX}{first["label"]} {value} × {second["label"]} {value2} · '
            f'{subject} · {pivot_label}')


def grid_values(label, axis, axis2):
    """(value, value2) encoded in a grid candidate's label, or (None, None)."""
    text = str(label or '')
    if not text.startswith(PROBE_LABEL_PREFIX):
        return None, None
    head = text[len(PROBE_LABEL_PREFIX):].split(' · ')[0]
    first, second = AXES[axis], AXES[axis2]
    for prefix, name in ((f'{first["label"]} ', 'first'), (f'{second["label"]} ', 'second')):
        if not head.startswith(prefix):
            return None, None
        head = head[len(prefix):]
        break
    try:
        left, right = head.split(' × ')
        right = right[len(second['label'])+1:] if right.startswith(f'{second["label"]} ') else right
        return int(left), int(right)
    except (ValueError, IndexError):
        return None, None


def analyse_grid(cells, reference_series, tolerance=0.0):
    """Turn a two-axis probe into a viability map, a compensation finding, and its evidence.

    `cells` is a list of dicts with `v1`, `v2`, `chests` and optional `effective1`/`effective2`.
    A cell is working only when its paired interval establishes non-inferiority. An uncertain cell
    cannot make a boundary. A boundary exists only between confirmed working and failing cells.

    Compensation is claimed only from two bracketed rows whose thresholds differ: that is the
    observable form of "lower attack works if defence is higher". One bracketed row is not a trend,
    and is reported as such.
    """
    rows = []
    for cell in cells:
        summary = summarise_point(cell['chests'])
        delta = paired_delta(cell['chests'], reference_series)
        viable = paired_status(delta, tolerance)
        rows.append(dict(v1=cell['v1'], v2=cell['v2'], viable=viable, delta=delta,
                         effective1=cell.get('effective1'), effective2=cell.get('effective2'),
                         **summary))
    measured = [row for row in rows if row['n'] > 0 and row['viable'] is not None]
    uncertain = [row for row in rows if row['n'] > 0 and row['viable'] is None]
    by_first = {}
    for row in measured:
        by_first.setdefault(row['v1'], []).append(row)
    thresholds = {}
    for first, group in sorted(by_first.items()):
        viable_second = [row['v2'] for row in group if row['viable']]
        if not viable_second:
            thresholds[first] = dict(threshold=None, bracketed=False, note='no probed value works in this row')
            continue
        low = min(viable_second)
        below = [row for row in group if row['v2'] < low and not row['viable']]
        if below:
            edge = max(below, key=lambda row: row['v2'])
            thresholds[first] = dict(threshold=low, bracketed=True, failsAt=edge['v2'],
                                     delta=edge['delta'])
        else:
            # No measured cell below this row's lowest working value failed, so the requirement is
            # *at or below* it. Recording that as the threshold lets one row be compared with
            # another even when only one of them was bracketed - the comparison then reports the
            # direction (needs at least X versus works at the floor) instead of dropping the row.
            thresholds[first] = dict(threshold=low, bracketed=False, failsAt=None,
                                     note='the lowest probed value in this row still works, so its '
                                          'requirement is at or below it')
    bracketed = {first: entry for first, entry in thresholds.items() if entry['bracketed']}
    statement, interaction = None, None
    second_label = None
    if len(measured) < 2:
        statement = ('not enough measured cells yet: a boundary needs at least one cell that still '
                     'works and one that does not, both measured on shared seeds')
    elif not bracketed:
        statement = ('no boundary was bracketed anywhere on this grid; '
                     + (f'{len(uncertain)} measured cell(s) remain uncertain'
                        if uncertain else 'the measured cells do not show a limiting boundary'))
    else:
        rows_with_requirement = sorted((first, entry) for first, entry in thresholds.items()
                                       if entry['threshold'] is not None)
        parts = [f'{len(bracketed)} of {len(by_first)} rows bracket a boundary']
        steps = sorted({row['v2'] for row in measured})
        step = min((b-a for a, b in zip(steps, steps[1:])), default=None)
        if len(rows_with_requirement) >= 2:
            (first_a, entry_a), (first_b, entry_b) = rows_with_requirement[0], rows_with_requirement[-1]
            span = abs(entry_a['threshold']-entry_b['threshold'])
            if step is not None and span >= step:
                def describe(entry):
                    return (f'at least {entry["threshold"]}' if entry['bracketed']
                            else f'at or below {entry["threshold"]} (nothing lower was probed, or '
                                 'everything lower still worked)')
                interaction = dict(
                    kind=('compensation' if entry_a['threshold'] > entry_b['threshold']
                          else 'reinforcement'),
                    axis='axis-1', other='axis-2',
                    fromValue=first_a, fromThreshold=entry_a['threshold'], fromBracketed=entry_a['bracketed'],
                    toValue=first_b, toThreshold=entry_b['threshold'], toBracketed=entry_b['bracketed'],
                    difference=span, gridStep=step)
                parts.append(
                    f'the second axis must reach {describe(entry_a)} when the first is {first_a}, but '
                    f'{describe(entry_b)} when the first is {first_b} - the requirement moves by '
                    f'{span}, one grid step is {step}')
            else:
                parts.append('the rows share the same threshold, so the two axes are not shown to '
                             'compensate across the probed range')
        statement = '; '.join(parts)
    return dict(rows=rows, thresholds=thresholds, interaction=interaction, statement=statement,
                resolution=(sorted({row['v2'] for row in measured}) or None),
                basis=('every cell ran the same shared seed bank and is compared to the reference '
                       'strategy by paired differences; a boundary is only reported where a measured '
                       'cell that works sits next to a measured cell that does not'))
