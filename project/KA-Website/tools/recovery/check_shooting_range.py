"""Section H: test the shooting-range / formation-row hypothesis against the real simulator.

The user's hypothesis is that a weapon's `shootingRange` means "how many formation rows away this
fighter can attack", so from row 1 a range of 1 is enough, row 2 needs range 2, and most larger
ranges collapse. This module tests that against the engine instead of assuming it, and reports which
ranges are provably redundant and which are not.

What the recovered code reads:

  * `combat_shared_controllers.SharedControllers.distance(a, b)` is `abs(dx) + abs(dy)` - the metric
    every range is compared against, so a range is a *Manhattan cell radius*, not a row count.
  * The **weapon's** `shootingRange` is read three ways: numerically as the normal attack's target
    radius (`ranged_target` -> `nearest_skill_target(range)`), and as the boolean `range > 1` in
    `decide`, `decide_next_state` and the charging `long_range` gate.
  * The **skill's** own `shootingRange` decides the skill target radius (`SharedControllers.
    candidates`) and the direct-attack line / area band length. It is separate from the weapon's, so
    a 2-Hit Attack equipped on a long bow still only reaches adjacent cells.

Method: one real fixture (the frozen UREF build against encounter 19's real 21-enemy roster), one
unit whose weapon is swapped between real weapons of the *same* weapon type carrying different
shooting ranges, with every raw parameter re-aligned so the effective parameters are identical in
every variant, and identical seeds. The trace's own attack events are decoded back to cells.
"""
from __future__ import annotations

import hashlib
import json
import sys
from copy import deepcopy
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import combat_runtime_data
import combat_sandbox
from combat_setup import prepare_setup
from strategy_optimizer_adapter import default_scenario

ROOT = HERE.parents[2]
OUT = ROOT / 'RE-evidence' / '20260922-search-contract' / 'shooting-range.json'

#: Real weapons, grouped by the weapon type id the simulator reads. Every entry inside a family
#: shares type, motion and projectileFlag, so the only combat-relevant difference is the range.
WEAPON_FAMILIES = {
    'bow': {1: 175, 6: 162, 7: 163, 8: 164, 9: 165, 10: 166},
    'spear': {2: 67, 3: 69, 4: 76},
    'gun': {1: 176, 7: 157, 8: 158, 9: 159, 10: 160, 12: 161},
}


def _equipment_rows():
    profiles = combat_runtime_data.load_data('weapon-skill-profiles.json')
    return {int(row['id']): row for row in profiles['equipment']}


def _skill_rows():
    profiles = combat_runtime_data.load_data('weapon-skill-profiles.json')
    return {int(row['id']): row for row in profiles['skills']}


def _effective(scenario, unit_name):
    prepared = prepare_setup(deepcopy(scenario))
    for row in prepared['ownUnits']:
        if row['name'] == unit_name:
            return {int(pid): (int(entry['value']), int(entry['maximum']))
                    for pid, entry in row['effectiveParameters'].items()}
    raise AssertionError(f'{unit_name} is not in the prepared setup')


def normalise_effective(scenario, unit_name, target):
    """Re-align the raw inputs so the *effective* parameters match `target` exactly.

    A weapon swap is only a behaviour change if the numbers the engine reads stay put: a
    representative item must never quietly move the intended searched stat.
    """
    probe = deepcopy(scenario)
    unit = next(u for u in probe['ownUnits'] if u['name'] == unit_name)
    for pid, (want_value, want_max) in sorted(target.items()):
        entry = unit['parameters'].get(str(pid)) or unit['parameters'].get(pid)
        if entry is None:
            continue
        for _ in range(4):
            have_value, have_max = _effective(probe, unit_name)[pid]
            if have_value == want_value and have_max == want_max:
                break
            # A bounded parameter (HP/MP/Vigor) carries its own maximum, and the runner reads the
            # prepared maximum for HP/MP; an unbounded one reports INT_MAX and needs no adjustment.
            if entry.get('rawMax') not in (None, 2147483647):
                entry['rawMax'] = int(entry['rawMax']) + (want_max - have_max)
            entry['rawValue'] = int(entry.get('rawValue') or 0) + (want_value - have_value)
    return probe


def _frames(report):
    """{tick: {identity: (x, y)}} rebuilt from the trace's own placement and cell-change events."""
    frames, current = {}, {}
    for event in report.get('trace') or []:
        tick = int(event.get('tick') or 0)
        if event.get('kind') == 'initial_placement':
            # One event per placed unit: `target` is its identity and `cell` is where the recovered
            # formation function put it. These events fire at tick -1 (before the first tick).
            if event.get('cell') is not None and event.get('target') is not None:
                current[int(event['target'])] = tuple(event['cell'])
        elif event.get('kind') == 'cell_change' and event.get('cell'):
            current[int(event['target'])] = tuple(event['cell'])
        frames[tick] = dict(current)
    return frames


def _attack_rows(report, weapon_range, skill_ranges):
    frames = _frames(report)
    names = (report.get('result') or {}).get('names') or {}
    identity_names = {int(v): k for k, v in names.items()}
    # `emit` returns the event *id*, and an `attack_batch` carries those ids, so the batch has to be
    # resolved through the trace's own index before the attack payloads can be read.
    by_id = {int(event['id']): event for event in report.get('trace') or []
             if event.get('id') is not None}
    rows = []
    for event in report.get('trace') or []:
        kind = event.get('kind')
        if kind == 'attack':
            payloads = [event]
        elif kind == 'attack_batch':
            payloads = [by_id.get(int(entry)) for entry in event.get('attacks') or []]
        else:
            continue
        tick = int(event.get('tick') or 0)
        frame = frames.get(tick) or {}
        for attack in payloads:
            if not attack or attack.get('kind') != 'attack':
                continue
            attacker, target = int(attack['attacker']), int(attack['target'])
            if attacker not in frame or target not in frame:
                continue
            ax, ay = frame[attacker][:2]
            tx, ty = frame[target][:2]
            skill = attack.get('skill')
            command = attack.get('commandId')
            rows.append(dict(
                tick=tick, attacker=attacker, attackerName=identity_names.get(attacker),
                target=target, targetName=identity_names.get(target), skill=skill, commandId=command,
                # A normal release carries no command id; an enqueued skill release always does. The
                # *row* id is not the discriminator: a bow's normal attack is emitted with the derived
                # Bow Attack row (1) and a gun's with Gun Attack (2), per `update_attacking`.
                release=('normal' if command is None else 'skill'),
                route=attack.get('route'),
                skillRange=(None if skill is None
                            else int(skill_ranges.get(int(skill), {}).get('shootingRange') or 0)),
                dx=abs(ax - tx), dy=abs(ay - ty), manhattan=abs(ax - tx) + abs(ay - ty),
                weaponRange=weapon_range))
    return rows


def _run(scenario, tick_limit, seeds):
    runs = []
    for math_seed, lib_seed in seeds:
        data = dict(deepcopy(scenario), tickLimit=tick_limit, mathSeed=math_seed, libSeed=lib_seed)
        runs.append(combat_sandbox.run_scenario(data, include_trace=True))
    return runs


def _digest(report):
    payload = json.dumps({key: report['result'].get(key) for key in
                          ('ticks', 'verdict', 'mathDraws', 'libDraws', 'rngFinalState')},
                         sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _placement_table(report):
    """{'ownRow:enemyRank': closest manhattan distance} from the run's own initial placement."""
    names = (report.get('result') or {}).get('names') or {}
    placement = {}
    for event in report.get('trace') or []:
        if event.get('kind') == 'initial_placement' and event.get('cell') is not None:
            placement[int(event['target'])] = tuple(event['cell'])
    if not placement:
        return {}
    own_cells, enemy_cells = [], []
    for name, identity in names.items():
        cell = placement.get(int(identity))
        if cell is None:
            continue
        (enemy_cells if str(name).startswith('enemy:') else own_cells).append(cell)
    own_rows = sorted({cell[1] for cell in own_cells})
    enemy_rows = sorted({cell[1] for cell in enemy_cells}, reverse=True)
    table = {}
    for own_index, own_y in enumerate(own_rows):
        for rank, enemy_y in enumerate(enemy_rows):
            table[f'{own_index}:{rank}'] = min(
                abs(own_x - enemy_x) + abs(own_y - enemy_y)
                for own_x, oy in own_cells if oy == own_y
                for enemy_x, ey in enemy_cells if ey == enemy_y)
    return table


def _unit_row(report, unit_name):
    names = (report.get('result') or {}).get('names') or {}
    identity = names.get(unit_name)
    for event in report.get('trace') or []:
        if event.get('kind') == 'initial_placement' and event.get('target') == identity:
            return tuple(event['cell'])
    return None


def probe_scenario(base, extra=5):
    """The base fight plus `extra` empty-handed fodder units, so a probe unit lands in row 2.

    `combat_states.is_most_front(grid)` is `grid < 5`, and the recovered `decide` short-circuits to
    state 3 for a most-front fighter, which hides the weapon's range. The added fodder are placed
    last (lowest effective defence), carry no skills at all, and therefore reach the decision with
    nothing but the weapon's `shootingRange` left to distinguish them - which is exactly the case the
    user's hypothesis is about.
    """
    scenario = deepcopy(base)
    template = deepcopy(scenario['ownUnits'][2])
    template.update(skills=[], invocationLevels=[], equipment=[], weaponId=0)
    # A validated scenario keys parameters by int; a stored library keys them by str.
    entry = template['parameters'].get(14, template['parameters'].get('14'))
    entry['rawValue'] = 1
    hp = template['parameters'].get(10, template['parameters'].get('10'))
    hp.update(rawValue=500, rawMax=500)
    for index in range(int(extra)):
        unit = deepcopy(template)
        unit['name'] = f'Range probe {index+1}'
        scenario['ownUnits'].append(unit)
    return scenario


def main():
    base = default_scenario()
    tick_limit, seeds = 300, [(11, 22), (33, 44)]
    rows, skills = _equipment_rows(), _skill_rows()
    probe_base = probe_scenario(base)
    subjects = [
        # The front-row (most-front) Ninja: `decide` short-circuits on `is_most_front`, so the
        # weapon's range should be invisible for it. This is the control.
        dict(key='ninja', scenario=base, index=0, name=base['ownUnits'][0]['name']),
        # The Healer sits in the second own row (grid 5) but still has skill candidates, so it is
        # the intermediate case: not most-front, but never short of a candidate either.
        dict(key='healer', scenario=base, index=1, name=base['ownUnits'][1]['name']),
        # The decisive probe: second own row, no skills, nothing but the weapon left to read.
        dict(key='probeRow2', scenario=probe_base, index=len(probe_base['ownUnits'])-1,
             name=probe_base['ownUnits'][-1]['name']),
    ]
    record = dict(
        schema='ka-shooting-range-check-1',
        fixture=dict(
            scenario='RE-evidence/20260920-user-wairo-strategy/scenarios/UREF.scenario.json',
            encounterId=base['encounterId'], tickLimit=tick_limit,
            subjects=[dict(key=s['key'], index=s['index'], name=s['name']) for s in subjects],
            ownUnits=len(base['ownUnits']), seeds=[list(seed) for seed in seeds],
            note=('the Ninja carries 2/3/4/5/7-Hit Attack and Counter; every one of those rows '
                  'carries its own shootingRange of 1, so a long bow never extends a ranged skill - '
                  'only the weapon-driven normal attack')),
        units={}, placement={}, checks={})

    for subject in subjects:
        index, unit_name = subject['index'], subject['name']
        subject_base = subject['scenario']
        entry = dict(key=subject['key'], index=index, families={},
                     placementCell=list(_unit_row(_run(subject_base, tick_limit, seeds[:1])[0],
                                                  unit_name) or ()))
        for family, ranges in WEAPON_FAMILIES.items():
            reference, family_record = None, {}
            for weapon_range in sorted(ranges):
                weapon_id = ranges[weapon_range]
                scenario = deepcopy(subject_base)
                unit = scenario['ownUnits'][index]
                keep = [row for row in unit['equipment']
                        if int(rows[int(row['id'])]['category']) != 0]
                unit['weaponId'] = weapon_id
                unit['equipment'] = [dict(id=weapon_id, level=99, affinity=1)] + keep
                if reference is None:
                    reference = _effective(scenario, unit_name)
                scenario = normalise_effective(scenario, unit_name, reference)
                if _effective(scenario, unit_name) != reference:
                    raise AssertionError(f'{unit_name} {family} {weapon_range}: stats did not align')
                runs = _run(scenario, tick_limit, seeds)
                attacks = [attack for run in runs
                           for attack in _attack_rows(run, weapon_range, skills)
                           if attack['attackerName'] == unit_name]
                normal = [a for a in attacks if a['release'] == 'normal']
                family_record[str(weapon_range)] = dict(
                    weaponId=weapon_id, weaponType=int(rows[weapon_id]['type']),
                    projectileFlag=bool(rows[weapon_id]['projectileFlag']),
                    motion=int(rows[weapon_id]['motion']),
                    verdicts=[run['result']['verdict'] for run in runs],
                    ticks=[run['result']['ticks'] for run in runs],
                    digests=[_digest(run) for run in runs],
                    attacks=len(attacks), normalAttacks=len(normal),
                    skillAttacks=len(attacks) - len(normal),
                    normalAttackSkillRows=sorted({a['skill'] for a in normal}),
                    normalAttackMaxDistance=max((a['manhattan'] for a in normal), default=None),
                    skillAttackMaxDistance=max((a['manhattan'] for a in attacks
                                                if a['skill'] is not None), default=None),
                    attackedTargets=sorted({a['targetName'] for a in attacks}),
                    perTargetMaxDistance={name: max(a['manhattan'] for a in attacks
                                                    if a['targetName'] == name)
                                          for name in sorted({a['targetName'] for a in attacks})})
            entry['families'][family] = family_record
        record['units'][unit_name] = entry
        record['units'][unit_name]['key'] = subject['key']

    record['placement'] = _placement_table(_run(base, tick_limit, seeds[:1])[0])
    record['probePlacement'] = _placement_table(_run(probe_base, tick_limit, seeds[:1])[0])
    record['checks'] = _checks(record)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(record, indent=2, sort_keys=True), encoding='utf-8')
    print(json.dumps(record['checks'], indent=2))
    print('wrote', OUT)
    return 0


def _checks(record):
    """The measured answers, asserted, so a regression in the engine fails this check."""
    checks = {}
    for unit_name, entry in (record.get('units') or {}).items():
        key = entry.get('key') or unit_name
        bow = entry['families'].get('bow') or {}
        if not bow:
            continue
        ordered = [bow[name] for name in sorted(bow, key=int)]
        short = ordered[0]
        long_ranges = ordered[1:]
        # 1. Every variant in the family fought identical effective parameters (asserted at
        #    alignment time), so any difference left over is the range itself.
        entry['firstRangeChange'] = next(
            (int(name) for name, row in zip(sorted(bow, key=int), ordered)
             if tuple(row['digests']) != tuple(short['digests'])), None)
        checks[f'info:{key}:range1AndLongestRangeDiffer'] = (
            tuple(short['digests']) != tuple(ordered[-1]['digests']))
        # 2. The collapse the user suspected: every range above the distance to the nearest
        #    reachable enemy rank selects the same target, so those traces agree.
        checks[f'info:{key}:rangesAboveNearestTargetCollapse'] = len(
            {tuple(row['digests']) for row in long_ranges}) == 1
        # 3. A normal release is the weapon's own route: it never exceeds the weapon's range, and on
        #    a bow or a gun it is emitted with the *derived* Bow Attack (1) / Gun Attack (2) row.
        #    Informational: the engine deliberately does NOT recheck range at release
        #    (`combat_resolution.resolve_attack`), so a target that moved away can be hit beyond the
        #    range that selected it. The measured maximum is recorded, not asserted.
        checks[f'info:{key}:normalAttackMaxDistance'] = all(
            row['normalAttackMaxDistance'] is None
            or row['normalAttackMaxDistance'] <= int(range_value)
            for range_value, row in zip(sorted(bow, key=int), ordered))
        derived = {'bow': {1}, 'gun': {2}}
        checks[f'{key}:normalAttackUsesDerivedWeaponRow'] = all(
            set(row['normalAttackSkillRows']) <= derived.get(family_used, set())
            for family_used in ('bow', 'gun')
            for row in [entry['families'].get(family_used, {}).get(range_value)
                        for range_value in sorted(entry['families'].get(family_used, {}), key=int)]
            if row and row['normalAttackSkillRows'])
        if key == 'probeRow2':
            # Measured: range 1 is uniquely distinct, and every range from the point where the
            # radius starts to matter onward collapses onto one trace. The radius is not a row
            # count - the state decision branches on `range > 1`, and the numeric radius only
            # changes the run once a target is actually inside it.
            checks['probeRow2:rangeOneDiffersAndLongRangesCollapse'] = (
                entry['firstRangeChange'] is not None
                and tuple(short['digests']) != tuple(ordered[-1]['digests'])
                and len({tuple(row['digests']) for row in long_ranges}) == 1)
            entry['measuredNote'] = (
                'the first range whose trace differs from range 1 is '
                f'{entry["firstRangeChange"]}; every range above it produced one identical trace')
        if key == 'ninja':
            checks['ninja:mostFrontFighterIsRangeInsensitive'] = (
                entry['firstRangeChange'] is None)
    placement = record.get('placement') or {}
    checks['placement:frontRowReachesFrontRankAtDistanceOne'] = placement.get('0:0') == 1
    checks['placement:distanceIsOnePlusBothRows'] = all(
        placement.get(f'{own}:{rank}') == 1 + int(own) + int(rank)
        for key in placement for own, rank in [key.split(':')])
    # `info:` entries are measurements kept for the report; PASS covers the assertions only.
    checks['PASS'] = all(value for key, value in checks.items()
                         if key != 'PASS' and not key.startswith('info:'))
    return checks


if __name__ == '__main__':
    raise SystemExit(main())
