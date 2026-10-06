"""Derive the SYNTHETIC HUMAN stat-search interval for every stat, from the recovered game data.

The formula is the native-verified one (`check_combat_job_parameters.py`, Unicorn fixture over
`JobData.GetParam*` / `Param.CreateAdventurerParamSet` / `ExpSystem.LevelUp`):

    stat value = initValue + raiseValue * (level - 1),   level <= maxLevel(job, rank, param)

with each `Job.csv` row supplying thirteen `[maxLevel, needExp, initValue, raiseValue]` blocks in
parameter-id order 10..22.

The synthetic search-space walls are deliberately *not* tied to one real build, so:

    minimum = the lowest `initValue` any job row carries at parameter level 1 (the global floor)
    maximum = the highest `initValue + raiseValue * 998` over every S-rank job row - the curve
              evaluated at PARAMETER LEVEL 999 and deliberately NOT clamped to the row's own
              `maxLevel`, because the synthetic fighter is not a real job at a real level
    ceiling = maximum + the best single level-99 equipment contribution for that stat
              (`equipment_parameter(pair, level) = base + growth * (level - 1)` at affinity 1)

The result is expressed in the **effective** domain the runner reports, which is what the optimiser
searches: `raw + extra + equipment` for the unbounded stats, and the prepared maximum for HP/MP.
The job that produced a wall is informational only - the synthetic fighter does not become Royal,
Doctor or Blacksmith.

`SUPPLIED` below is the reviewed expected table. The script **verifies** every number against the
source data and records each comparison, so a silent substitution cannot happen: a mismatch is
written into the JSON as `verification.mismatches` and printed.

Output: `RE-evidence/20260922-search-contract/stat-bounds.json`.
"""
import csv
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

ROOT = HERE.parents[2]
JOB_CSV = ROOT / 'KA-Website' / 'data' / 'Sheet csv' / 'KA GameData - Job.csv'
# The other recovery checks write their evidence into the workspace-root RE-evidence directory, and
# `search_contract.BOUNDS_PATH` reads it from there; keep one location.
ROOT_EVIDENCE = HERE.parents[2] / 'RE-evidence' / '20260922-search-contract'
OUT = ROOT_EVIDENCE / 'stat-bounds.json'

PARAM_ORDER = [10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22]
BLOCK_START = 40
BLOCK_STRIDE = 5

#: The stats this contract searches, with the name the report uses.
STATS = {'hp': 10, 'mp': 11, 'atk': 13, 'def': 14, 'spd': 15, 'lck': 16, 'dex': 19, 'int': 18}

#: The reviewed expected table for the synthetic human contract
#: (minimum at parameter level 1, S-rank base at parameter level 999, level-99 equipment, ceiling).
SUPPLIED = {
    'hp':  dict(minimum=55,    base=18369, equip=918,  ceiling=19287, source='S Rank Royal'),
    'mp':  dict(minimum=5,     base=7092,  equip=894,  ceiling=7986,  source='S Rank Royal'),
    'atk': dict(minimum=6,     base=5044,  equip=722,  ceiling=5766,  source='S Rank Royal'),
    'def': dict(minimum=3,     base=4040,  equip=936,  ceiling=4976,  source='S Rank Royal'),
    'spd': dict(minimum=5,     base=4046,  equip=734,  ceiling=4780,  source='S Rank Royal'),
    'lck': dict(minimum=5,     base=4154,  equip=1050, ceiling=5204,  source='S Rank Royal'),
    'int': dict(minimum=2,     base=6042,  equip=1092, ceiling=7134,  source='S Grade Doctor'),
    'dex': dict(minimum=2,     base=5999,  equip=1050, ceiling=7049,  source='S Rank Entertainer'),
}

#: The parameter level the synthetic ceiling extrapolates to.
SYNTHETIC_LEVEL = 999

#: `ParamHasMaxValue` bitmask 0x8007 over (paramId - 10): these carry a stored maximum in the build.
BOUNDED_PARAMS = (10, 11, 12, 25)


def combat_jobs():
    """Every job row with its thirteen parameter blocks, from the original sheet.

    Both the combat (`Rank`) and non-combat (`Grade`) rows are included: the contract's ceiling rule is
    "every S-rank job curve", and exactly one of the supplied numbers (INT, from `S Grade Doctor`)
    depends on that reading. The synthetic fighter never becomes a job, so the row's class is
    irrelevant to what the number means.
    """
    rows = list(csv.reader(JOB_CSV.read_text(encoding='utf-8-sig').splitlines()))
    jobs = []
    for row in rows[2:]:
        if len(row) < 13 or (' Rank ' not in row[1] and ' Grade ' not in row[1]):
            continue
        blocks = []
        for index in range(len(PARAM_ORDER)):
            start = BLOCK_START + index * BLOCK_STRIDE
            cell = row[start + 1:start + 5]
            if len(cell) != 4 or not all(value.strip() for value in cell):
                blocks = None
                break
            blocks.append([int(value) for value in cell])
        if blocks:
            jobs.append(dict(csvId=int(row[0]), sheetName=row[1], blocks=blocks,
                             rank=row[1].split(' Rank ')[0].strip()
                             if ' Rank ' in row[1] else row[1].split(' Grade ')[0].strip(),
                             combat=' Rank ' in row[1]))
    return jobs


def equipment_rows():
    from combat_runtime_data import load_data
    return load_data('weapon-skill-profiles.json')['equipment']


def equipment_parameter(pair, level):
    base, growth = pair
    return int(base) + int(growth) * (int(level) - 1)


def equipment_ceiling(parameter_id, level=99):
    """The largest level-99 contribution any recovered equipment row makes to this parameter."""
    index = parameter_id - 10
    best = 0
    best_row = None
    for row in equipment_rows():
        parameters = row.get('parameters') or []
        if index < 0 or index >= len(parameters):
            continue
        value = equipment_parameter(parameters[index], level)
        if value > best:
            best, best_row = value, row
    return best, best_row


def main():
    jobs = combat_jobs()
    derived = []
    mismatches = []
    for stat, parameter_id in sorted(STATS.items(), key=lambda item: item[1]):
        index = PARAM_ORDER.index(parameter_id)
        minimum = None
        maximum = None
        for job in jobs:
            max_level, _need_exp, init_value, raise_value = job['blocks'][index]
            low = init_value
            high = (init_value + raise_value * (SYNTHETIC_LEVEL - 1)
                    if job['rank'] == 'S' else None)
            if minimum is None or low < minimum[0]:
                minimum = (low, job, 1)
            if high is not None and (maximum is None or high > maximum[0]):
                maximum = (high, job, SYNTHETIC_LEVEL)
        # The floor is quoted the way the reviewed table quotes it: the lowest D-rank level-1 value,
        # and the global level-1 value, reported together so the two can never be confused.
        d_rank = min(((job['blocks'][index][2], job) for job in jobs if job['rank'] == 'D'),
                     key=lambda pair: pair[0])
        contribution, item = equipment_ceiling(parameter_id)
        expected = SUPPLIED[stat]
        entry = dict(
            stat=stat, parameter=parameter_id,
            domain='effective (synthetic human)',
            bounded=parameter_id in BOUNDED_PARAMS,
            minimum=minimum[0],
            minimumSource=dict(sheetName=minimum[1]['sheetName'], csvId=minimum[1]['csvId'],
                               level=minimum[2], initValue=minimum[0]),
            minimumDRank=d_rank[0],
            minimumDRankSource=dict(sheetName=d_rank[1]['sheetName'], csvId=d_rank[1]['csvId'],
                                    level=1, initValue=d_rank[0]),
            maximumCurve=maximum[0],
            maximumSource=dict(sheetName=maximum[1]['sheetName'], csvId=maximum[1]['csvId'],
                               level=SYNTHETIC_LEVEL, value=maximum[0],
                               rowMaxLevel=maximum[1]['blocks'][index][0], rank=maximum[1]['rank']),
            equipmentContribution=contribution,
            equipmentSource=(None if item is None else dict(id=item['id'], name=item['name'],
                                                            level=99, category=item.get('category'),
                                                            type=item.get('type'))),
            maximum=maximum[0] + contribution,
            expected=expected,
            formula=f'value = initValue + raiseValue * ({SYNTHETIC_LEVEL} - 1) for every S-rank row, '
                    'unclamped; equipment = base + growth * (equipmentLevel - 1) at affinity 1',
        )
        for field, actual in (('minimum', entry['minimum']), ('base', entry['maximumCurve']),
                              ('equip', entry['equipmentContribution']),
                              ('ceiling', entry['maximum'])):
            if actual != expected[field]:
                mismatches.append(dict(stat=stat, field=field, supplied=expected[field],
                                       computed=actual))
        derived.append(entry)
        print(f"{stat:<4} param {parameter_id:<3} min {minimum[0]:<8} "
              f"(global {minimum[1]['sheetName']}, D-rank {d_rank[0]}) max {maximum[0]:<8} "
              f"({maximum[1]['sheetName']}) +{contribution:<6}= {maximum[0] + contribution:<9} "
              f"[supplied {expected['ceiling']}]")
    document = dict(
        schema='ka-search-stat-bounds-2',
        searchSpaceVersion=3,
        domain='synthetic effective values as the canonical runner reports them '
               '(battle_value: raw+extra+equipment, or the prepared maximum for HP/MP)',
        formula='native-verified in check_combat_job_parameters.py: '
                'value = initValue + raiseValue * (level - 1); the synthetic ceiling evaluates the '
                'S-rank curve at parameter level 999 without the row maxLevel clamp',
        source=dict(jobSheet=str(JOB_CSV.relative_to(ROOT)), jobRows=len(jobs),
                    combatRows=sum(1 for job in jobs if job['combat']),
                    nonCombatRows=sum(1 for job in jobs if not job['combat']),
                    parameterBlocks='[maxLevel, needExp, initValue, raiseValue] in param order 10..22',
                    equipmentLevel=99, syntheticParameterLevel=SYNTHETIC_LEVEL),
        verification=dict(supplied=SUPPLIED, mismatches=mismatches, verified=not mismatches),
        stats=derived)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(document, indent=1), encoding='utf-8')
    print(f'wrote {OUT}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
