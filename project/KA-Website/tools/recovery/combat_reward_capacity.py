"""Item storage model for `ecs.ItemSystem.AddItemToWarehouse` 0x159c9e4. Not a reward/chest limit.

Scope correction: this is **item storage**, not treasure and not a per-fight chest cap. An earlier
revision of this module and its check read the push loop out of a single slice and asserted
consequences the whole function does not support (treasure bounds, duplicate-id slot merging,
loss behaviour). Those claims are withdrawn. The whole 0x159c9e4 body was then read
(`RE-evidence/20260911-building/placement/storage-159c9e4.asm`, 0x159c9e4..0x159cd1c) and it is:

    entry            mov w19, w3            ; w19 = requested count
                     cbz x20, #0x159caf0    ; null item argument -> log and return w19 unchanged
                     ... SearchEmptyWarehouses(town, 8) 0x161881c supplies the candidate rows
    per warehouse    an unnamed predicate via the resolver 0x1332890 decides whether this row is used
                     space = GetMaxValue(e, 8) - GetValue(e, 8)   0x14ce7e0 / 0x14ce670
                     cmp w27, #1 / b.lt  -> skip a row with space < 1
    push loop        PushItemToWarehouse(this, warehouse, itemData)  0x159ce0c, one item per call
                     sub w19, w19, #1 ; cmp w19, #0 ; b.le -> done, stored count zero
                     subs w27, w27, #1 ; b.ne -> push again
                     mov w19, w24 (leftover = num + current - capacity) ; next warehouse
    return           mov w0, w19

There is **no `num <= 0` guard inside this function**: the first push happens before `num--` is
tested, so a zero count still stores one item when a usable row exists. Whether a caller ever
reaches AddItemToWarehouse with a zero count is NOT established here - the caller
`ecs.ItemSystem.AddItem` 0x159c828 was not executed - so this is modelled faithfully and flagged,
not presented as native zero-input behaviour.

`data.RankData.get_maxTreasureCapacity` `0x1630cf4` is literally `ldr w0,[x0,#0x50]; ret`.
That is a field read, not a cap: `ka_index callers 0x1630cf4` records **no direct caller**, and the
two-instruction slice is the only place `1630cf4`/`1630cf8` appear in any `.asm` under `RE-evidence`
(every other hit is `dump/dump.cs`, `script.json`, the registry row or prose). So nothing in the
decoded receipt/warehouse chain reads `RankData + 0x50`. (Proving the field is never read anywhere
would need an ELF xref pass this track deliberately does not build.)

What this module therefore models: the per-warehouse item clamp of `AddItemToWarehouse`
`0x159c9e4` (slice `RE-evidence/20260911-building/placement/storage-159c9e4.asm`):

    159cc20: mov w1, #8
    159cc28: bl  #0x14ce7e0   ; ParameterComponent.GetMaxValue(e, 8) -> w24 = capacity
    159cc40: mov w1, #8
    159cc48: bl  #0x14ce670   ; ParameterComponent.GetValue(e, 8)    -> w0  = current
    159cc4c: sub w27, w24, w0 ; space = capacity - current
    159cc50: cmp w27, #1
    159cc54: b.lt #0x159cb54  ; space < 1 -> skip this warehouse
    159cc58: add w8, w19, w0  ; num + current
    159cc5c: sub w24, w8, w24 ; leftover = num + current - capacity
    159cc6c: bl  #0x159ce0c   ; PushItemToWarehouse(this, warehouse, itemData) -- ONE item
    159cc70: sub w19, w19, #1 ; num--
    159cc74: cmp w19, #0
    159cc78: b.le #0x159cc94  ; num <= 0 -> done, stored count consumed
    159cc7c: subs w27, w27, #1; space--
    159cc80: b.ne #0x159cc60  ; space left -> push again
    159cc84: mov w19, w24     ; space gone -> carry leftover, next warehouse

`PushItemToWarehouse` `0x159ce0c` is `void (this, warehouse, itemData)` - no count argument - so the
loop pushes one item per iteration. Parameter id 8 is the "Item" stock count
(`combat_native.py param 8`, dump constant) and `SearchEmptyWarehouses(town, 8)` `0x161881c` supplies
the candidate row order; `WarehouseSystem.TotalCapacityOfWarehouse(town, paramId)` `0x161a2f4` and
`TotalStockInWarehouse` sum the same per-warehouse parameter.

Consequences modelled here (static decode of the push loop only, no native trace):
  * the number of items this loop stores is bounded by the summed per-warehouse `maxValue`;
  * the count that fits no warehouse is **returned** (the caller drops it) rather than silently
    clamped, so a push can be partially retained;
  * the extra 4 bytes per stored item are pushed with the loop, so a count that exceeds the summed
    space spills across rows in row order and then returns.

No claim is made here about: chest or treasure counts per fight; the chest-open result derivation;
duplicate-id merging into a param slot (or any other storage read/write beyond the loop's own
`GetValue + num` arithmetic); loss or early-exit behaviour; the world collection that follows a
ground spawn; or the unnamed resolver `0x1332890` (whose target the predicate calls stay undecoded).
"""

PARAM_ITEM = 8

ADD_ITEM_TO_WAREHOUSE = '0x159c9e4'
SEARCH_EMPTY_WAREHOUSES = '0x161881c'
PARAMETER_GET_MAX_VALUE = '0x14ce7e0'
PARAMETER_GET_VALUE = '0x14ce670'
PUSH_ITEM_TO_WAREHOUSE = '0x159ce0c'
RANKDATA_GET_MAX_TREASURE_CAPACITY = '0x1630cf4'
TOTAL_CAPACITY_OF_WAREHOUSE = '0x161a2f4'


def free_space(warehouse, param_id=PARAM_ITEM):
    """`space = GetMaxValue(e, param_id) - GetValue(e, param_id)` (`0x159cc4c`)."""
    return warehouse.get('maxValue', 0) - warehouse.get('value', 0)


def distribute(num, warehouses, param_id=PARAM_ITEM):
    """Model of the `0x159c9e4` push loop only, over supplied `SearchEmptyWarehouses` rows.

    `warehouses` is the enumerator result as `{'maxValue': int, 'value': int}` rows. Returns
    `perWarehouse` (index, pushed, capacity, before, after), `stored` and `leftover` - the method's
    own return value (`w0 = w19`), i.e. the count that fitted nowhere and is **not** stored.

    Not modelled: the entry null-item guard (which returns the count unchanged), the unnamed
    per-row predicate through `0x1332890`, and the enumerator itself. `num` is the caller-supplied
    count; whether a caller ever passes 0 is unproven (see the module docstring).
    """
    remaining = num
    per_warehouse = []
    for index, warehouse in enumerate(warehouses):
        space = free_space(warehouse, param_id)
        if space < 1:
            continue
        pushed = 0
        # `0x159cc60`..`0x159cc80`: push one item, then `num--` (b.le) before `space--` (b.ne).
        # The whole function has no `num <= 0` guard, so with num == 0 this loop still pushes once
        # and returns 0 stored. Reproduce the decoded order; caller-side reachability is unproven.
        while True:
            pushed += 1
            remaining -= 1
            if remaining <= 0:
                per_warehouse.append(dict(index=index, pushed=pushed,
                                          capacity=warehouse.get('maxValue', 0),
                                          before=warehouse.get('value', 0),
                                          after=warehouse.get('value', 0) + pushed))
                return dict(perWarehouse=per_warehouse, stored=num - remaining, leftover=0)
            space -= 1
            if space == 0:
                break
        per_warehouse.append(dict(index=index, pushed=pushed,
                                  capacity=warehouse.get('maxValue', 0),
                                  before=warehouse.get('value', 0),
                                  after=warehouse.get('value', 0) + pushed))
    return dict(perWarehouse=per_warehouse, stored=num - remaining, leftover=remaining)


def store(num, warehouses, param_id=PARAM_ITEM):
    """Apply `distribute` to a mutable warehouse list; returns the leftover (un-stored) count."""
    plan = distribute(num, warehouses, param_id)
    for row in plan['perWarehouse']:
        warehouses[row['index']]['value'] = row['after']
    return plan['leftover']


def capacity_of(warehouses, param_id=PARAM_ITEM):
    """`WarehouseSystem.TotalCapacityOfWarehouse` `0x161a2f4` sum shape over the same rows."""
    return sum(w.get('maxValue', 0) for w in warehouses)


def stock_of(warehouses, param_id=PARAM_ITEM):
    """`WarehouseSystem.TotalStockInWarehouse` sum shape over the same rows."""
    return sum(w.get('value', 0) for w in warehouses)
