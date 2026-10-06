"""Item-storage capacity: the decoded `AddItemToWarehouse` push loop, re-checked.

Portable, deterministic. It (a) re-reads the frozen disassembly slice and asserts the exact
instructions the model is built from - including the entry null-item guard, the absence of an
internal `num <= 0` guard, and the return path, (b) asserts the two-instruction capacity getter is
the only place its address appears, and (c) exercises the push loop over boundaries. Nothing here
runs native code and nothing here is a per-fight chest/treasure limit: the arithmetic is derived
from the static decode, and this check only proves the model still matches that decode. See
`combat_reward_capacity.py` for the RVAs, the whole-function trace and the withdrawn claims.
"""
import json
from pathlib import Path

from combat_reward_capacity import (
    PARAM_ITEM, RANKDATA_GET_MAX_TREASURE_CAPACITY, capacity_of, distribute, free_space, stock_of,
    store,
)
from combat_runtime_data import WORKSPACE_ROOT

BUILDING = WORKSPACE_ROOT / 'RE-evidence/20260911-building/placement'
RECEIPT = WORKSPACE_ROOT / 'RE-evidence/20260914-b3-receipt'
ADD_ASM = BUILDING / 'storage-159c9e4.asm'
GETTER_ASM = RECEIPT / '1630cf4.asm'

# The instructions the loop model is read from: address -> expected opcode text.
CLAMP_INSTRUCTIONS = {
    '159ca00': 'mov w19, w3',     # w19 = requested count
    '159ca70': 'cbz x20, #0x159caf0',  # null item argument -> log path
    '159cc28': 'bl #0x14ce7e0',   # GetMaxValue(e, 8)
    '159cc48': 'bl #0x14ce670',   # GetValue(e, 8)
    '159cc4c': 'sub w27, w24, w0',  # space = capacity - current
    '159cc50': 'cmp w27, #1',
    '159cc54': 'b.lt #0x159cb54',  # space < 1 -> skip warehouse
    '159cc58': 'add w8, w19, w0',  # num + current
    '159cc5c': 'sub w24, w8, w24',  # leftover = num + current - capacity
    '159cc60': 'mov x0, x22',     # first push happens before any count test
    '159cc6c': 'bl #0x159ce0c',   # PushItemToWarehouse(..., one item)
    '159cc70': 'sub w19, w19, #1',  # num--
    '159cc74': 'cmp w19, #0',     # count tested only after the first push
    '159cc78': 'b.le #0x159cc94',  # done
    '159cc7c': 'subs w27, w27, #1',  # space--
    '159cc80': 'b.ne #0x159cc60',  # space left -> push again
    '159cc84': 'mov w19, w24',   # space gone -> carry leftover
    '159cd04': 'mov w0, w19',    # return the count that fitted nowhere
}

ADD_ITEM_WAREHOUSE_START = '159c9e4'
ADD_ITEM_WAREHOUSE_LAST = '159cd1c'
FIRST_PUSH = '159cc60'


def instructions(path):
    rows = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        head, _, tail = line.partition(':')
        if head.strip():
            rows[head.strip()] = tail.split(';', 1)[0].strip()
    return rows


def main():
    add = instructions(ADD_ASM)
    for address, expected in CLAMP_INSTRUCTIONS.items():
        assert add.get(address) == expected, (address, add.get(address), expected)

    # Whole-function guard check: inside 0x159c9e4 there is no `num <= 0` test before the first
    # push (`w19` is the count). A caller-side guard would live in ecs.ItemSystem.AddItem
    # 0x159c828, which this check does not execute, so zero-count reachability stays unproven.
    body = {k: v for k, v in add.items() if int(ADD_ITEM_WAREHOUSE_START, 16) <= int(k, 16) <= int(ADD_ITEM_WAREHOUSE_LAST, 16)}
    assert body, 'storage-159c9e4.asm does not cover the whole function'
    before_push = {k: v for k, v in body.items() if int(k, 16) < int(FIRST_PUSH, 16)}
    assert not any(v.startswith(('cbz w19', 'cbnz w19', 'cmp w19')) for v in before_push.values()), \
        [row for row in before_push.items() if 'w19' in row[1]]
    assert body[FIRST_PUSH] == 'mov x0, x22'
    assert body['159cd04'] == 'mov w0, w19'

    getter = instructions(GETTER_ASM)
    assert getter == {'1630cf4': 'ldr w0, [x0, #0x50]', '1630cf8': 'ret'}, getter
    # Bounded negative: the address appears in no other listing under the building/receipt evidence.
    hits = [path.name for root in (BUILDING, RECEIPT) for path in root.rglob('*.asm')
            if RANKDATA_GET_MAX_TREASURE_CAPACITY[2:] in path.read_text(encoding='utf-8')]
    assert hits == [GETTER_ASM.name], hits

    cases = []

    def case(name, num, warehouses, **expected):
        plan = distribute(num, warehouses, PARAM_ITEM)
        observed = dict(stored=plan['stored'], leftover=plan['leftover'],
                        pushed=[row['pushed'] for row in plan['perWarehouse']])
        assert observed == expected, (name, observed, expected)
        cases.append(dict(name=name, num=num, warehouses=warehouses, **observed))
        return plan

    # One warehouse with room: everything is stored, nothing returned.
    case('single-room', 5, [dict(maxValue=10, value=0)], stored=5, leftover=0, pushed=[5])
    # Exact fit.
    case('exact-fit', 10, [dict(maxValue=10, value=0)], stored=10, leftover=0, pushed=[10])
    # A count larger than one row spills into the next row in order.
    case('spill', 15, [dict(maxValue=10, value=0), dict(maxValue=10, value=0)],
         stored=15, leftover=0, pushed=[10, 5])
    # No warehouse has room: the whole award is returned, not clamped.
    case('full', 7, [dict(maxValue=10, value=10)], stored=0, leftover=7, pushed=[])
    # Space < 1 is skipped, including an over-full warehouse.
    case('skip-full-then-fit', 4, [dict(maxValue=10, value=12), dict(maxValue=10, value=8)],
         stored=2, leftover=2, pushed=[2])
    # Partial retention: 25 in, capacity 10 + 5, 10 returned.
    case('partial-retention', 25, [dict(maxValue=10, value=0), dict(maxValue=5, value=0)],
         stored=15, leftover=10, pushed=[10, 5])
    # Decoded loop order: `num--` is tested only after the first push (`b.le`), so num == 0 still
    # stores one item. The function has no internal guard; caller reachability is unproven.
    case('zero-num-no-internal-guard', 0, [dict(maxValue=10, value=0)], stored=1, leftover=0, pushed=[1])

    # `store` mutates the rows and returns the same leftover as the plan.
    rows = [dict(maxValue=10, value=3), dict(maxValue=4, value=4)]
    assert store(9, rows) == 2, rows
    assert rows == [dict(maxValue=10, value=10), dict(maxValue=4, value=4)], rows
    assert (capacity_of(rows), stock_of(rows)) == (14, 14), rows
    assert free_space(dict(maxValue=10, value=12)) == -2

    report = dict(
        nativeRva=dict(addItemToWarehouse='0x159c9e4', pushItemToWarehouse='0x159ce0c',
                       searchEmptyWarehouses='0x161881c', parameterGetMaxValue='0x14ce7e0',
                       parameterGetValue='0x14ce670', paramId=PARAM_ITEM,
                       rankDataGetMaxTreasureCapacity=RANKDATA_GET_MAX_TREASURE_CAPACITY),
        cases=len(cases), details=cases,
        finding=('Item storage is bounded by the summed per-warehouse Parameter(id 8) maxValue; the '
                 'count that fits nowhere is returned un-stored (partial retention, never a silent '
                 'clamp). RankData.maxTreasureCapacity 0x1630cf4 is a field getter with no decoded '
                 'caller and is not the cap. This is item storage, not a per-fight chest limit.'),
        limits=['Static decode only: no native trace; the model is the 0x159c9e4 push loop, not the '
                'whole function (the null-item guard, the unnamed 0x1332890 row predicate and the '
                'enumerator are documented but not modelled).',
                'No claim about treasure (param 7), chest counts per fight, duplicate-id slot merging, '
                'loss/early-exit retention or any inventory maximum.',
                'Zero-count caller reachability is unproven: ecs.ItemSystem.AddItem 0x159c828 was not '
                'executed, so the num==0 case is a faithful loop-order consequence only.',
                'Chest-open result derivation, world collection and the unnamed resolver 0x1332890 '
                'stay outside this check.'])
    print(json.dumps(report))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
