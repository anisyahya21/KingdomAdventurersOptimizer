"""Native fixture: the Human job base/growth/cap chain.

Composes the original `JobData.GetParam*` getters, `Param.CreateAdventurerParamSet`,
`Parameter.AddLevel`/`AddMaxValue`/`get_maxValue`/`get_levelRate` and `ecs.ExpSystem.LevelUp`
out of the frozen `libil2cpp.so` (fb834373...30208) under Unicorn with declared stubs, and
compares every observed value against the static-decode claim:

    stat value = initValue + raiseValue * (level - 1),  level <= maxLevel(job, rank, param)

Scope of the claim: this fixture verifies the arithmetic and the cap enforcement of the already
decoded functions. It does not discover new mechanics, model exp accrual, or claim that any
specific level vector is gameplay-attainable.

Comparison inputs are `Job.csv` rows (13 blocks of `[maxLevel, needExp, initValue, raiseValue]`
in param-id order 10..22) plus a synthetic `ExpData[].exp` table. Both are inputs to the fixture,
not evidence for the native formula.

Parameter sets stay distinct:

* `HUMAN_STAT_PARAMS` is the 12-parameter Human combat/stat set (10,11,12,13,14,15,16,18,19,20,
  21,22) used by combat reads and `Param.AverageHumanParamLevels`.
* `CREATION_LIST_PARAMS` is the 13-entry id array that the creation loop iterates. Its contents are
  a supplied control input; the fixture asserts only that the loop honours the supplied array.
  Entry 17 is exercised as a creation-list member and carries no asserted role.
"""
import json
from pathlib import Path

from combat_initial_state import EVIDENCE, i32, trunc_div
from combat_native_composition import Composition
from combat_run_manifest import canonical_hash

SLICES = EVIDENCE / 'job-parameter-slices/slices.json'
OUT = EVIDENCE / 'job-parameter-checks.json'

INT_MAX = 0x7fffffff

# Class-initialization fast paths of the loaded slices, so no run reaches an init stub.
FLAGS = (0x316b163, 0x316b6ce, 0x316b87e, 0x316b884, 0x316b95e)
# GOT slots read by the loaded slices. Every one is read as `[slot] -> cell -> class`; the JobData
# slot's cell is also the static-field owner, so its +0xB8 must point at the static block.
GOT_CELL_SLOTS = (0x2f5b570, 0x2f5b890, 0x2f5d0d0)
JOB_DATA_CLASS_SLOT = 0x2f648b8

GETTERS = {'GetParamIndex': 0x162c3f0, 'GetParam': 0x162c3f8, 'GetParamMaxLevel': 0x162c42c,
           'GetParamInitValue': 0x162c584, 'GetParamRaiseValue': 0x162c5b0,
           'ParamHasMaxValue': 0x162c5dc, 'GetParamHighestLevel': 0x162c600,
           'SetParamHighestLevel': 0x162c634, 'UpdateParamHighestLevel': 0x162c668}

# JobData.ParamHasMaxValue is a bitmask 0x8007 over (paramId - 10): indices 0,1,2 and 15.
BOUNDED_PARAMS = (10, 11, 12, 25)
HUMAN_STAT_PARAMS = (10, 11, 12, 13, 14, 15, 16, 18, 19, 20, 21, 22)
CREATION_LIST_PARAMS = tuple(range(10, 23))
EXTRA_PARAMS = (25, 35, 39)
# `JobData.GetParamNeedExp` = parameters[i][1] * expData[level] / 100 (truncating). The table comes
# from the out-of-slice accessor `Param.get_expData` (0x16205e4) and is a fixture input.
EXP_DATA = {level: 100 + 2 * level for level in range(512)}

LEVELUP_ENTRY = 0x156d928
CREATE_ENTRY = 0x16600c0
EXP_CARRY = 250
MAXEXP_SEED = 100

# `KA GameData - Job.csv` rows [id, life, 13 x [maxLevel, needExp, initValue, raiseValue]].
PROFILES = {
    'D Rank Knight': dict(csvId=70, life=2, parameters=[
        [30, 100, 95, 7], [25, 200, 5, 2], [25, 200, 50, 4], [30, 100, 15, 2], [30, 100, 6, 2],
        [30, 100, 20, 2], [30, 100, 25, 2], [99, 100, 0, 5], [25, 200, 4, 2], [25, 200, 2, 2],
        [25, 200, 4, 2], [25, 200, 5, 2], [25, 200, 5, 2]]),
    'C Rank Knight': dict(csvId=71, life=3, parameters=[
        [35, 100, 115, 7], [25, 200, 6, 2], [25, 200, 60, 4], [35, 100, 18, 2], [35, 100, 7, 2],
        [35, 100, 24, 2], [35, 100, 36, 2], [99, 100, 0, 5], [25, 200, 5, 2], [25, 200, 2, 2],
        [25, 200, 5, 2], [25, 200, 6, 2], [25, 200, 6, 2]]),
    'S Rank Knight': dict(csvId=74, life=7, parameters=[
        [55, 100, 215, 13], [35, 200, 6, 5], [35, 200, 110, 7], [55, 100, 32, 4], [55, 100, 14, 3],
        [55, 100, 44, 3], [55, 100, 120, 3], [99, 100, 0, 5], [35, 200, 9, 3], [35, 200, 2, 3],
        [35, 200, 9, 3], [35, 200, 11, 3], [35, 200, 11, 3]]),
    'A Rank Magic Knight': dict(csvId=144, life=5, parameters=[
        [45, 100, 205, 11], [30, 200, 42, 3], [30, 200, 100, 5], [45, 100, 32, 3], [30, 200, 18, 2],
        [45, 100, 70, 2], [45, 100, 66, 2], [99, 100, 0, 5], [30, 200, 42, 2], [30, 200, 3, 2],
        [30, 200, 9, 2], [30, 200, 11, 2], [30, 200, 9, 2]]),
    'D Rank Guard': dict(csvId=65, life=2, parameters=[
        [30, 100, 90, 7], [25, 200, 5, 2], [25, 200, 50, 4], [30, 100, 10, 2], [30, 100, 8, 2],
        [30, 100, 24, 2], [30, 100, 20, 2], [99, 100, 0, 5], [25, 200, 3, 2], [25, 200, 3, 2],
        [25, 200, 6, 2], [25, 200, 5, 2], [25, 200, 5, 2]]),
    'S Rank Champion': dict(csvId=119, life=7, parameters=[
        [55, 100, 285, 15], [55, 100, 44, 5], [35, 200, 110, 7], [55, 100, 36, 4], [55, 100, 23, 3],
        [55, 100, 54, 3], [55, 100, 200, 3], [99, 100, 0, 5], [35, 200, 26, 3], [35, 200, 2, 3],
        [35, 200, 3, 3], [35, 200, 11, 3], [35, 200, 11, 3]]),
    'F Rank Scholar': dict(csvId=132, life=1, parameters=[
        [20, 200, 55, 5], [20, 200, 5, 2], [25, 100, 100, 4], [20, 200, 8, 2], [20, 150, 4, 2],
        [20, 200, 5, 2], [20, 200, 5, 2], [99, 100, 0, 4], [25, 100, 5, 2], [25, 100, 15, 4],
        [25, 100, 10, 3], [25, 100, 15, 3], [25, 100, 20, 3]]),
    'D Grade Farmer': dict(csvId=10, life=2, parameters=[
        [25, 200, 70, 6], [25, 200, 5, 2], [30, 100, 100, 5], [25, 200, 8, 2], [25, 150, 5, 2],
        [25, 200, 5, 2], [25, 200, 5, 2], [99, 100, 0, 4], [30, 100, 5, 2], [30, 100, 20, 2],
        [30, 100, 25, 4], [30, 100, 15, 3], [30, 100, 5, 2]]),
    'S Grade Farmer': dict(csvId=14, life=7, parameters=[
        [35, 200, 160, 12], [35, 200, 6, 5], [55, 100, 235, 8], [35, 200, 38, 4], [40, 150, 11, 3],
        [35, 200, 18, 3], [35, 200, 18, 3], [99, 100, 0, 4], [55, 100, 11, 3], [55, 100, 54, 3],
        [55, 100, 68, 5], [55, 100, 40, 4], [55, 100, 11, 3]]),
    'S Grade Monarch': dict(csvId=4, life=-1, parameters=[
        [60, 100, 235, 10], [60, 100, 44, 5], [60, 100, 235, 7], [60, 100, 98, 4], [60, 100, 14, 3],
        [60, 100, 98, 3], [60, 100, 98, 3], [99, 100, 0, 5], [60, 100, 40, 5], [60, 100, 26, 5],
        [60, 100, 26, 3], [60, 100, 40, 4], [60, 100, 54, 4]]),
}


class Ledger:
    """Every scalar comparison, so the report can state how many ran and which diverged."""

    def __init__(self):
        self.comparisons = 0
        self.divergences = []

    def check(self, label, observed, expected):
        self.comparisons += 1
        if observed != expected:
            self.divergences.append(dict(case=label, observed=observed, expected=expected))
            if len(self.divergences) > 40:
                raise AssertionError(f'too many divergences; first: {self.divergences[0]}')
        return observed


LEDGER = Ledger()


def s32(value):
    value &= 0xffffffff
    return value - 2 ** 32 if value >= 2 ** 31 else value


def block(profile, param_id):
    return profile['parameters'][param_id - 10]


def model_cap(profile, param_id):
    return block(profile, param_id)[0]


def model_value(profile, param_id, level):
    _, _, init, raise_value = block(profile, param_id)
    return i32(init + raise_value * (level - 1))


def model_has_max(param_id):
    return param_id in BOUNDED_PARAMS


def model_max_value(profile, param_id, level):
    return model_value(profile, param_id, level) if model_has_max(param_id) else INT_MAX


def model_need_exp(profile, param_id, level):
    return trunc_div(block(profile, param_id)[1] * EXP_DATA[level], 100)


def model_level_rate(level, max_level):
    if max_level == INT_MAX:
        return 0
    return max(0, min(100, trunc_div(level * 100, max_level)))


def unexpected_throw(machine, address):
    raise AssertionError(f'native throw path reached at {hex(machine.reg("pc"))}')


def record_add(machine, builder, param_id, value, max_value, level, max_level, exp, max_exp):
    """`ParamSet.ParamSetBuilder.Add(id, value, maxValue, level, maxLevel, exp, maxExp)`."""
    machine.added.append(dict(id=s32(param_id), value=s32(value), maxValue=s32(max_value),
                              level=s32(level), maxLevel=s32(max_level), exp=s32(exp),
                              maxExp=s32(max_exp)))
    return builder


def new_machine(name):
    machine = Composition([SLICES], name=name)
    for address in FLAGS:
        machine.w8(address, 1)
    machine.stub(0x12d21a0, 'il2cpp_class_init')
    machine.stub(0x12d22a4, 'il2cpp_method_init')
    machine.stub(0x12d23c8, 'il2cpp_throw_null', unexpected_throw, capture=('x0',))
    machine.stub(0x12d23d0, 'il2cpp_throw_range', unexpected_throw, capture=('x0',))
    for slot in GOT_CELL_SLOTS:
        machine.got(slot, machine.klass({}))
    # One shared plain class cell for every synthetic object: the heap is 2 MB and every
    # `klass()` call reserves 0x2000, so a fresh cell per object would exhaust it.
    machine.plain = machine.klass({})
    machine.builder = machine.object_of(machine.plain, 0x60)
    machine.added = []
    machine.stub(0x1682ccc, 'ParamSet.Builder', lambda h, klass: h.builder, capture=('x0',))
    machine.stub(0x1683710, 'ParamSet.ParamSetBuilder.Add', record_add,
                 capture=('x0', 'x1', 'x2', 'x3', 'x4', 'x5', 'x6', 'x7'))
    machine.stub(0x1683818, 'ParamSet.Build', lambda h, builder: h.builder, capture=('x0',))
    machine.exp_table = None
    install_exp_data(machine)
    return machine


def install_exp_data(machine):
    """Synthetic `ExpData[]`: `GetParamNeedExp` indexes it by level and reads +0x20."""
    table = machine.array(max(EXP_DATA) + 1, stride=8)
    for level, value in EXP_DATA.items():
        entry = machine.object_of(machine.plain, 0x40)
        machine.w32(entry + 0x20, i32(value))
        machine.w64(table + 0x20 + 8 * level, entry)
    machine.exp_table = table
    machine.stub(0x16205e4, 'Param.get_expData', lambda h: h.exp_table)
    return table


def install_job(machine, profile, param_ids):
    """JobData object: +0x98 life, +0xB8 `int[][] parameters`, +0xD0 highest-level array."""
    rows = []
    for values in profile['parameters']:
        array = machine.array(len(values), stride=4)
        for index, value in enumerate(values):
            machine.w32(array + 0x20 + 4 * index, i32(value))
        rows.append(array)
    outer = machine.array(len(rows), stride=8)
    for index, array in enumerate(rows):
        machine.w64(outer + 0x20 + 8 * index, array)
    highest = machine.array(len(param_ids), stride=4)
    job = machine.object_of(machine.plain, 0xE0)
    machine.w32(job + 0x98, i32(profile['life']))
    machine.w64(job + 0xB8, outer)
    machine.w64(job + 0xD0, highest)
    ids = machine.array(len(param_ids), stride=4)
    for index, param_id in enumerate(param_ids):
        machine.w32(ids + 0x20 + 4 * index, i32(param_id))
    static_fields = machine.alloc(0x40)
    machine.w64(static_fields + 0x10, ids)                      # JobData.PARAM_IDS
    machine.got(JOB_DATA_CLASS_SLOT, machine.klass({0xB8: static_fields}))
    machine.highest = highest
    return job


def install_parameter(machine, param_id, spec):
    parameter = machine.object_of(machine.plain, 0x40)
    machine.w32(parameter + 0x10, i32(param_id))
    machine.w32(parameter + 0x14, i32(spec['value']))
    machine.w32(parameter + 0x18, i32(spec['maxValue']))
    machine.w32(parameter + 0x1C, i32(spec.get('extraValue', 0)))
    machine.w32(parameter + 0x20, i32(spec.get('extraMaxValue', 0)))
    machine.w32(parameter + 0x24, i32(spec['level']))
    machine.w32(parameter + 0x28, i32(spec['maxLevel']))
    machine.w32(parameter + 0x2C, i32(spec['exp']))
    machine.w32(parameter + 0x30, i32(spec['maxExp']))
    machine.w32(parameter + 0x34, 0)
    return parameter


def install_entity(machine, parameters):
    """Synthetic entity graph; the lookups are supplied handles, never values or ordering."""
    machine.entity = machine.object_of(machine.plain, 0x60)
    machine.component = machine.object_of(machine.plain, 0x40)
    machine.human = machine.object_of(machine.plain, 0x40)
    machine.parameters = parameters
    machine.stub(0x1471200, 'Entity.get_parameter', lambda h, entity: h.component, capture=('x0',))
    machine.stub(0x14c89d0, 'ParameterComponent.get_Item',
                 lambda h, component, param_id: h.parameters[s32(param_id)], capture=('x0', 'w1'))
    machine.stub(0x146edf4, 'Entity.get_human', lambda h, entity: h.human, capture=('x0',))
    machine.stub(0x14caa70, 'HumanComponent.get_job', lambda h, human: h.job, capture=('x0',))
    machine.stub(0x147e7e0, 'Entity.IsNullOrDestroyed', lambda h, entity: 0, capture=('x0',))
    machine.stub(0x1471288, 'Entity.get_hasParameter', lambda h, entity: 1, capture=('x0',))
    machine.stub(0x14ce638, 'ParameterComponent.ContainsId', lambda h, component, param_id: 1,
                 capture=('x0', 'w1'))
    machine.stub(0x16609ec, 'Param.GetMaxValue',
                 lambda h, entity, param_id, default: h.r32(h.parameters[s32(param_id)] + 0x18),
                 capture=('x0', 'w1', 'w2'))
    return machine.entity


def call_names(machine):
    return [event['name'] for event in machine.trace if event['kind'] == 'call']


def listing_stubs():
    """The stub table of a machine with every group of lookups registered, for the record."""
    machine = new_machine('stub-listing')
    machine.job = install_job(machine, PROFILES['D Rank Knight'], CREATION_LIST_PARAMS)
    parameter = install_parameter(machine, 10, dict(value=1, maxValue=1, level=1, maxLevel=30,
                                                    exp=0, maxExp=100))
    install_entity(machine, {10: parameter})
    return machine.stubs


def getter_cases():
    """Every getter for every (job profile, parameter id) pair, plus highest-level bookkeeping."""
    machine = new_machine('job-getters')
    cases = []
    for name, profile in PROFILES.items():
        job = install_job(machine, profile, CREATION_LIST_PARAMS)
        for param_id in (list(HUMAN_STAT_PARAMS) + [17]):
            values = block(profile, param_id)
            label = f'{name}/param{param_id}'
            index = machine.run(GETTERS['GetParamIndex'], {'x0': job, 'w1': param_id})
            array = machine.run(GETTERS['GetParam'], {'x0': job, 'w1': param_id})
            observed = dict(
                index=LEDGER.check(f'{label} index', s32(index), param_id - 10),
                length=LEDGER.check(f'{label} arrayLength', machine.r32(array + 0x18), 4),
                maxLevel=LEDGER.check(f'{label} maxLevel',
                                      s32(machine.run(GETTERS['GetParamMaxLevel'],
                                                      {'x0': job, 'w1': param_id})), values[0]),
                initValue=LEDGER.check(f'{label} initValue',
                                       s32(machine.run(GETTERS['GetParamInitValue'],
                                                       {'x0': job, 'w1': param_id})), values[2]),
                raiseValue=LEDGER.check(f'{label} raiseValue',
                                        s32(machine.run(GETTERS['GetParamRaiseValue'],
                                                        {'x0': job, 'w1': param_id})), values[3]),
                hasMaxValue=LEDGER.check(f'{label} hasMaxValue',
                                         s32(machine.run(GETTERS['ParamHasMaxValue'],
                                                         {'x0': job, 'w1': param_id})),
                                         1 if model_has_max(param_id) else 0))
            cases.append(dict(profile=name, paramId=param_id, **observed))
    job = install_job(machine, PROFILES['S Rank Knight'], CREATION_LIST_PARAMS)
    machine.run(GETTERS['SetParamHighestLevel'], {'x0': job, 'w1': 13, 'w2': 11})
    LEDGER.check('highest/read-back', s32(machine.run(GETTERS['GetParamHighestLevel'],
                                                      {'x0': job, 'w1': 13})), 11)
    LEDGER.check('highest/update-below', s32(machine.run(GETTERS['UpdateParamHighestLevel'],
                                                         {'x0': job, 'w1': 13, 'w2': 5})), 0)
    LEDGER.check('highest/update-above', s32(machine.run(GETTERS['UpdateParamHighestLevel'],
                                                         {'x0': job, 'w1': 13, 'w2': 20})), 1)
    LEDGER.check('highest/after-update', s32(machine.run(GETTERS['GetParamHighestLevel'],
                                                         {'x0': job, 'w1': 13})), 20)
    cases.append(dict(profile='S Rank Knight', paramId=13, highestLevel=20, updated=True))
    return cases, machine


def create_case(profile_name, profile, level, param_ids=CREATION_LIST_PARAMS, label=None):
    """One `Param.CreateAdventurerParamSet(data, level)` run; the Adds are the boundary record."""
    name = label or f'{profile_name}@{level}'
    machine = new_machine(f'create-{name}')
    job = install_job(machine, profile, param_ids)
    machine.run(CREATE_ENTRY, {'x0': job, 'w1': level})
    expected = [dict(id=param_id, value=model_value(profile, param_id, level),
                     maxValue=model_max_value(profile, param_id, level), level=level,
                     maxLevel=model_cap(profile, param_id), exp=0,
                     maxExp=model_need_exp(profile, param_id, level)) for param_id in param_ids]
    expected += [dict(id=25, value=profile['life'], maxValue=INT_MAX, level=1, maxLevel=INT_MAX,
                      exp=0, maxExp=INT_MAX),
                 dict(id=35, value=0, maxValue=INT_MAX, level=1, maxLevel=99, exp=0, maxExp=100),
                 dict(id=39, value=0, maxValue=INT_MAX, level=1, maxLevel=INT_MAX, exp=0,
                      maxExp=INT_MAX)]
    LEDGER.check(f'{name} addCount', len(machine.added), len(expected))
    for position, (observed, want) in enumerate(zip(machine.added, expected)):
        for field in ('id', 'value', 'maxValue', 'level', 'maxLevel', 'exp', 'maxExp'):
            LEDGER.check(f'{name} add{position}(param{want["id"]}).{field}', observed[field],
                         want[field])
    return dict(case=name, level=level, suppliedIds=list(param_ids), adds=machine.added,
                expectedAdds=expected, calls=call_names(machine))


def levelup_case(profile_name, profile, param_id, level):
    """One `ExpSystem.LevelUp(entity, paramId)` run with a fully specified parameter."""
    name = f'{profile_name}/param{param_id}@{level}'
    machine = new_machine(f'levelup-{name}')
    machine.job = install_job(machine, profile, CREATION_LIST_PARAMS)
    value = model_value(profile, param_id, level)
    max_value = model_max_value(profile, param_id, level)
    max_level = model_cap(profile, param_id)
    parameter = install_parameter(machine, param_id, dict(value=value, maxValue=max_value,
                                                          level=level, maxLevel=max_level,
                                                          exp=EXP_CARRY, maxExp=MAXEXP_SEED))
    entity = install_entity(machine, {param_id: parameter})
    machine.run(LEVELUP_ENTRY, {'x0': entity, 'w1': param_id})
    gained = 1 if level < max_level else 0
    level_after = level + gained
    raise_value = block(profile, param_id)[3]
    if model_has_max(param_id):
        expected_max_value = i32(max_value + raise_value * gained)
        expected_value = value
    else:
        expected_value = max(0, min(INT_MAX, i32(value + raise_value * gained)))
        expected_max_value = max_value
    exp_after = max(i32(EXP_CARRY - MAXEXP_SEED), 0)
    if model_level_rate(level_after, max_level) >= 100:
        exp_after = 0
    observed = dict(
        value=LEDGER.check(f'{name} value', machine.r32(parameter + 0x14), expected_value),
        maxValue=LEDGER.check(f'{name} maxValue', machine.r32(parameter + 0x18), expected_max_value),
        level=LEDGER.check(f'{name} level', machine.r32(parameter + 0x24), level_after),
        maxLevel=LEDGER.check(f'{name} maxLevel unchanged', machine.r32(parameter + 0x28), max_level),
        exp=LEDGER.check(f'{name} exp', machine.r32(parameter + 0x2c), exp_after),
        maxExp=LEDGER.check(f'{name} maxExp', machine.r32(parameter + 0x30),
                            model_need_exp(profile, param_id, level_after)),
        highestLevel=LEDGER.check(f'{name} highestLevel', machine.r32(machine.highest + 0x20 + 4 * (param_id - 10)),
                                  level_after))
    # The bounded/unbounded branch is observable: only the unbounded path enters Param.AddValue,
    # whose own entity lookups are declared stubs and therefore appear in the boundary trace.
    calls = call_names(machine)
    LEDGER.check(f'{name} delta', machine.r32(parameter + 0x14) - value
                 if not model_has_max(param_id) else machine.r32(parameter + 0x18) - max_value,
                 raise_value * gained)
    entered_add_value = [call for call in calls if call in ('Entity.IsNullOrDestroyed',
                                                            'Entity.get_hasParameter',
                                                            'ParameterComponent.ContainsId')]
    LEDGER.check(f'{name} AddValueLookupCalls', len(entered_add_value),
                 0 if model_has_max(param_id) else 3)
    LEDGER.check(f'{name} firstLookup', calls[:2], ['Entity.get_parameter',
                                                    'ParameterComponent.get_Item'])
    return dict(case=name, paramId=param_id, level=level, maxLevel=max_level, bounded=model_has_max(param_id),
                raiseValue=raise_value, gained=gained,
                branch='Parameter.AddMaxValue' if model_has_max(param_id)
                else 'Param.AddValue->Parameter.Add',
                expected=dict(value=expected_value, maxValue=expected_max_value, level=level_after,
                              exp=exp_after), observed=observed, calls=calls)


def main():
    getters, getter_machine = getter_cases()
    creates = [create_case('D Rank Knight', PROFILES['D Rank Knight'], 1),
               create_case('D Rank Knight', PROFILES['D Rank Knight'], 15),
               create_case('D Rank Knight', PROFILES['D Rank Knight'], 30),
               create_case('C Rank Knight', PROFILES['C Rank Knight'], 35),
               create_case('S Rank Knight', PROFILES['S Rank Knight'], 55),
               create_case('S Rank Champion', PROFILES['S Rank Champion'], 55),
               create_case('A Rank Magic Knight', PROFILES['A Rank Magic Knight'], 45),
               create_case('F Rank Scholar', PROFILES['F Rank Scholar'], 20),
               create_case('D Grade Farmer', PROFILES['D Grade Farmer'], 25),
               create_case('S Grade Monarch', PROFILES['S Grade Monarch'], 60),
               create_case('D Rank Knight', PROFILES['D Rank Knight'], 0,
                           label='level 0 control (no clamp claimed)'),
               create_case('S Rank Knight', PROFILES['S Rank Knight'], 1,
                           param_ids=(10, 13), label='supplied-id-list control (2 ids)')]
    levelups = [levelup_case('S Rank Knight', PROFILES['S Rank Knight'], 10, 1),
                levelup_case('S Rank Knight', PROFILES['S Rank Knight'], 10, 54),
                levelup_case('S Rank Knight', PROFILES['S Rank Knight'], 10, 55),
                levelup_case('S Rank Knight', PROFILES['S Rank Knight'], 13, 1),
                levelup_case('S Rank Knight', PROFILES['S Rank Knight'], 13, 54),
                levelup_case('S Rank Knight', PROFILES['S Rank Knight'], 13, 55),
                levelup_case('D Rank Knight', PROFILES['D Rank Knight'], 13, 29),
                levelup_case('D Rank Knight', PROFILES['D Rank Knight'], 13, 30),
                levelup_case('S Grade Farmer', PROFILES['S Grade Farmer'], 11, 35),
                levelup_case('F Rank Scholar', PROFILES['F Rank Scholar'], 12, 25),
                levelup_case('S Grade Monarch', PROFILES['S Grade Monarch'], 14, 60),
                levelup_case('D Grade Farmer', PROFILES['D Grade Farmer'], 13, 1),
                levelup_case('A Rank Magic Knight', PROFILES['A Rank Magic Knight'], 18, 44),
                levelup_case('C Rank Knight', PROFILES['C Rank Knight'], 15, 1)]
    report = dict(
        schema='ka-job-parameter-checks-1',
        fixtures=dict(getterCases=len(getters), createCases=len(creates), levelUpCases=len(levelups),
                      comparisons=LEDGER.comparisons),
        divergences=LEDGER.divergences,
        sliceManifest=getter_machine.slice_manifest(),
        stubs=[dict(address=hex(address), name=entry[0])
               for address, entry in sorted(listing_stubs().items())],
        suppliedInputs=dict(
            humanStatParams=list(HUMAN_STAT_PARAMS),
            creationListParams=list(CREATION_LIST_PARAMS),
            boundedParams=list(BOUNDED_PARAMS),
            extraParams=list(EXTRA_PARAMS),
            expDataSample={str(level): EXP_DATA[level] for level in (0, 1, 2, 55, 60)},
            profiles={name: dict(csvId=profile['csvId'], life=profile['life'],
                                 parameters=profile['parameters']) for name, profile in PROFILES.items()}),
        getterCases=getters, createCases=creates, levelUpCases=levelups,
        findings=[
            'JobData.GetParam resolves `parameters[paramId - 10]` and the getters read that row: '
            '[0] maxLevel, [1] needExp coefficient, [2] initValue, [3] raiseValue. Every observed '
            'value matches the Job.csv row supplied to the fixture across 10 job/rank profiles, the '
            '12-parameter Human stat set and creation-list entry 17.',
            'Param.CreateAdventurerParamSet passes `value = initValue + raiseValue * (level - 1)` '
            'for every id it is given, `maxValue = value` for the bounded ids 10/11/12 and INT_MAX '
            'otherwise, `maxLevel = GetParamMaxLevel`, `level = the single input level` and '
            '`exp = 0`; it then adds ids 25 (value = JobData.life), 35 (level 1, maxLevel 99, '
            'maxExp 100) and 39. The loop iterates the supplied id array: the 2-entry control run '
            'produces exactly 2 stat Adds, so the 13-entry list is an input rather than a '
            'hardcoded range, and no role is asserted for entry 17.',
            'ExpSystem.LevelUp carries `max(0, exp - maxExp)`, gains one level while '
            '`level < maxLevel`, and applies exactly one `raiseValue` per gained level: through '
            '`Parameter.AddMaxValue` when the effective maximum is non-zero (bounded HP/MP/Vigor) '
            'and through `Param.AddValue` -> `Parameter.Add(value, INT_MAX)` otherwise. At the cap '
            'the gain is zero and neither value nor maximum moves, which is the cap boundary the '
            'static decode predicted.',
            'LevelUp then writes `maxExp = GetParamNeedExp(paramId, newLevel)`, records the new '
            'level through `JobData.UpdateParamHighestLevel`, and zeroes exp when '
            '`get_levelRate() >= 100`. Measured against a synthetic ExpData table the maxExp '
            'relation `coefficient * expData[level] / 100` (truncating) matches for every case.'],
        limits=[
            'Only the listed slices ran. Entity/component lookups, the ParamSet builder, the '
            'ExpData accessor, class initialization and throw helpers are declared stubs: they '
            'supply handles, memory and inputs, never values or ordering.',
            'The job rows and the ExpData table are supplied inputs. This fixture does not claim '
            'that Job.csv is complete, nor the semantics of ExpData rows beyond the index and '
            'multiplication the loaded code performs.',
            'No exp accrual, grow/awakening application, inheritance roll, equipment contribution, '
            'combat read or encounter behavior is part of this fixture; `LevelUp` was driven with '
            'explicit parameter state rather than a produced one.',
            'The 999 ceiling is not exercised here: it is enforced by growth/inheritance sites '
            'outside this slice set. JobData.PARAM_IDS contents remain a supplied input.',
            'Three functions are mapped as two manifest rows each (`IntExtension.Clamp`, '
            '`Parameter.get_levelRate`, `Param.AddValue`): the linear slicer stops at the first '
            '`ret`, and each function branches past it into the rest of its own body. Both rows '
            'hold original bytes at their original addresses, so nothing but the mapping record '
            'differs from a single-row slice.'],
    )
    assert not LEDGER.divergences, LEDGER.divergences[:5]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(dict(fixtures=report['fixtures'], slices=report['sliceManifest']['slices'],
                          output=str(OUT), digest=canonical_hash(report))))


if __name__ == '__main__':
    main()
