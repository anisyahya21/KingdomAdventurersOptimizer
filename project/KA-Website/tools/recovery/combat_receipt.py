"""B3 treasure receipt: Entity.AddTreasure packaging and AISystem.TreasureBoxResult dispatch.

Static decode of the frozen libil2cpp.so (fb834373...30208) plus the original item/material
tables; see docs/reverse-engineering/HANDOFF-b3-treasure-receipt.md section 6 and 9. Master-data
rows, entity/world creation, popups and inventory writes are explicit port calls: this module
decides which arm runs, in which order, what is suppressed and what is retained. It never clamps
to a capacity, because RankData.maxTreasureCapacity (+0x50, 0x1630cf4) is unread on this path.
"""
import struct
from combat_initial_state import i32


def _f32(value):
    return struct.unpack('<f', struct.pack('<f', value))[0]

# Entity.AddTreasure `0x1473234`: the pooled TreasureComponent is added with this ComponentType
# (`br x4` through the vtable entry at klass+0x188, `w1 = 0x2c`; ComponentType.Treasure = 44).
COMPONENT_TYPE_TREASURE = 0x2c
# data.Data.GetData `0x1629040` is a jump table over DataType 0..0x31; TreasureBoxResult
# `0x14b8e4c` rejects DataType above 0x23 (35) before any arm, so 35 is the effective maximum for
# an arm. That gate sits *after* the row is resolved and an unfound row is marked found, so a
# DataType 0x24..0x31 reward still resolves its row and marks it found before the plain epilogue
# (native-executed by check_combat_receipt_dispatch.py's data-type-above-itemdata-unfound case).
# Above the GetData table (0x31) there is no row to resolve.
MAX_DATA_TYPE = 0x23
DATA_TYPE_TABLE_LIMIT = 0x31
# 32-bit bitset 0x801 | 6<<16 | 8<<32 at `0x14b904c`: accepted IStock data types, plus the
# ItemData type 24 checked separately by `cmp x8,#0x18`.
ISTOCK_DATA_TYPES = (0, 11, 17, 18, 35)
ITEM_DATA_TYPE = 24
# data.ItemData constants (dump.cs 63317 block) and the arm selector `[data+0x28] - 2`.
CATEGORY_BONUS = 0
CATEGORY_BILLING = 1
CATEGORY_DIA = 2
CATEGORY_POINT = 3
CATEGORY_GLOBAL_ITEM = 4
CATEGORY_ITEM = 5
CATEGORY_TICKET = 6
TYPE_FOOD = 1
TYPE_DIA = 8
TYPE_POINT_STAMINA = 14
TYPE_COIN_COPPER = 15
TYPE_COIN_SILVER = 16
TYPE_COIN_GOLD = 17
# ItemData.COIN_ITEM_TYPES is a cctor-built static int[] checked by get_IsCoinItem `0x162a9cc`
# (`Enumerable.Any<int>` `0x187beb8` over the lambda `0x162b950` = `t == this.<type>`).
COIN_ITEM_TYPES = (TYPE_COIN_COPPER, TYPE_COIN_SILVER, TYPE_COIN_GOLD)
FLAG_SELECT_TOWN_WHEN_GET = 512
# data.BaseData: state_ @0x10 (STATE_UNFOUND = 0), id_ @0x18, flag_ @0x1c.
STATE_UNFOUND = 0
STATE_FOUND = 1
# Arm effect literals: category 2/3 coin and stamina use type 8; category 4 uses type 13.
EFFECT_COIN_OR_STAMINA = 8
EFFECT_GLOBAL_ITEM = 13
EFFECT_MAX_FRAME = 40
EFFECT_SCALE = 100
EFFECT_Y_OFFSET = 20.0
GATHERABLE_LIFE_FRAME = 12000
MODIFY_ANIMATION_MAX_FRAME = 20
MODIFY_ANIMATION_ALPHA = 255
# parameter.Param.Treasure = 7 and data.MaterialData.TYPE_TREASURE = 7.
PARAM_TREASURE = 7
MATERIAL_TYPE_TREASURE = 7
# data.TreasureData statics: DATA_TYPE_NAME_TABLE @0x8 (Dictionary<int, string>) and
# EFFECT_TYPE_TABLE @0x10 (Dictionary<int, int>). Both are cctor-built, so their contents are
# supplied by the port; a missing effect entry reads as -1 and suppresses the effect.
EFFECT_TYPE_MISSING = -1
POPUP_RES_ID = 27
POPUP_SEB_ID = 2

PORT_KEYS = ('resolve','row_state','mark_found','is_istock','stock_add','is_item_data','is_alive',
             'material_rows','chest_position','treasure_image','row_name','add_item','add_coin',
             'add_stamina','create_material','create_product','add_gatherable',
             'add_modify_animation','create_effect','popup_text','effect_kind','add_popup',
             'create_item_add_to_town_select','push_form','towns_count','selected_town')

# What a grant proves about inventory receipt. Only a named modelled storage write
# (AppData.AddItem `0x1673b20`, AddCoin `0x1673854`, AddStamina `0x16743fc`) confirms that
# something entered inventory. Everything else is either presentation, a ground spawn whose
# collection is not modelled, a pending choice form, or an opaque port handoff:
#   * `create_product` + `add_gatherable` spawn a product on the ground (World.CreateProduct /
#     Entity.AddGatherable); whether anyone ever picks it up is unmodelled, so it is pending
#     collection, never a confirmed receipt.
#   * `create_item_add_to_town_select` + `push_form` open a town-selection dialog; the player's
#     choice and the storage write that follows it are not modelled, so it is pending choice.
#   * `create_material`, `add_modify_animation`, `create_effect`, `add_popup` are visuals.
#   * `stock_add` is the IStock interface call resolved through the unnamed resolver `0x1332890`;
#     the interface target is not decoded, so the call alone must not be read as a storage write
#     (the B3 warehouse rule measures the resulting Param.Treasure sum separately).
# A caller that needs "received into inventory" must require a confirmed grant (or an explicit
# acknowledgement it models itself); a nonempty `grants` list is not enough.
#
# A named storage channel is necessary but not sufficient. The native AppData.AddItem/AddCoin/
# AddStamina calls return void, so the port call's own return carries no receipt information; a
# void/False/opaque return stays unconfirmed. Only the modelled adapter can acknowledge a write, and
# it does so with a structured mapping `{'confirmed': True, ...}` (see `storage_acknowledged`). The
# channel and arguments the adapter receives stay native-equivalent; the explicit flag is the port
# contract's synthetic acknowledgement, never a reading of the native void return.
CONFIRMED_RECEIPT_GRANTS = ('add_item', 'add_coin', 'add_stamina')
PENDING_COLLECTION_GRANTS = ('create_product', 'add_gatherable')
PENDING_CHOICE_GRANTS = ('create_item_add_to_town_select', 'push_form')
PRESENTATION_GRANTS = ('create_material', 'add_modify_animation', 'create_effect', 'add_popup')


def storage_acknowledged(ack):
    """Explicit synthetic storage acknowledgement: a structured `{'confirmed': True}` flag.

    The modelled port adapter owns the write, so it owns the acknowledgement. The decode only knows
    that the native call returns void, which is not evidence of storage. Therefore:
      * `{'confirmed': True}` (optionally with `channel`) confirms the modelled write;
      * `None` (a void callback), `False`, a bare `True` and any opaque value do not.
    A caller that needs receipt must require this acknowledgement, or run a separate explicit
    confirmation step it models itself; it may never read a native void return as proof.
    """
    return isinstance(ack, dict) and ack.get('confirmed') is True


def retention_of(record):
    """Classify one dispatch record's grants into confirmed receipt versus pending/presentation.

    `confirmedReceipts` counts only modelled storage writes whose port adapter returned the explicit
    structured acknowledgement (`storage_acknowledged`); a named channel that returned void/False is
    listed under `unacknowledgedStorage` and does not confirm. `pendingCollection`, `pendingChoice`
    and `presentation` list the grants that do not prove a receipt, and `unclassified` catches a
    channel handoff such as `stock_add`. A dispatch whose grants are all pending or presentational is
    `acknowledgement='unconfirmed'`: it was consumed, but nothing is known to have been stored, so no
    caller may read it as retained inventory. A dispatch with no grants at all (a suppression or an
    unresolved row) is unconfirmed for the same reason.
    """
    grants = record.get('grants', [])
    kinds = [grant['kind'] for grant in grants]
    storage = [grant['kind'] for grant in grants if grant['kind'] in CONFIRMED_RECEIPT_GRANTS]
    confirmed = [grant['kind'] for grant in grants
                 if grant['kind'] in CONFIRMED_RECEIPT_GRANTS
                 and grant.get('acknowledged') is True]
    classified = set(CONFIRMED_RECEIPT_GRANTS) | set(PENDING_COLLECTION_GRANTS) | set(
        PENDING_CHOICE_GRANTS) | set(PRESENTATION_GRANTS)
    return dict(confirmedReceipts=len(confirmed), confirmedKinds=confirmed,
                unacknowledgedStorage=[kind for kind in storage if kind not in confirmed],
                pendingCollection=[kind for kind in kinds if kind in PENDING_COLLECTION_GRANTS],
                pendingChoice=[kind for kind in kinds if kind in PENDING_CHOICE_GRANTS],
                presentation=[kind for kind in kinds if kind in PRESENTATION_GRANTS],
                unclassified=[kind for kind in kinds if kind not in classified],
                acknowledgement='modelled-storage-write' if confirmed else 'unconfirmed')


def treasure_result(data_type, data_id, num):
    """TreasureData.TreasureResult: Info @0x10 {DataType @0x10, DataId @0x14}, Num @0x18."""
    return dict(dataType=data_type, dataId=data_id, num=num)


def package_receipt(data_id, is_openable=True, result_queue=None):
    """Entity.AddTreasure packaging; it performs no capacity check and no world mutation.

    The pooled component holds dataId @0x10, isOpenable @0x14 (`& 1`) and resultQueue @0x18 and
    is forwarded to the component handler with selector ComponentType.Treasure.
    """
    return dict(componentType=COMPONENT_TYPE_TREASURE, dataId=i32(data_id),
                isOpenable=1 if is_openable else 0,
                resultQueue=list(result_queue) if result_queue is not None else [])


def is_coin_item(row, coin_item_types=COIN_ITEM_TYPES):
    """ItemData.get_IsCoinItem: COIN_ITEM_TYPES.Any(t => t == <type> @0x2c)."""
    return row.get('type') in tuple(coin_item_types)


def is_food(row):
    """ItemData.get_IsFood: `<type> @0x2c` == 1 (102 category-5 rows in Item.txt)."""
    return row.get('type') == TYPE_FOOD


def is_ticket(row):
    """ItemData.get_IsTicket: `<category> @0x28` == 6."""
    return row.get('category') == CATEGORY_TICKET


def requires_town_select(row):
    """ItemData.get_IsRequiredSelectTownWhenGet: bit 9 of flag_ @0x1c (FLAG_SELECT_TOWN_WHEN_GET)."""
    return bool(row.get('flag', 0) & FLAG_SELECT_TOWN_WHEN_GET)


def dispatch_result(port, result_queue, chest=None, adds_popup=False, coin_item_types=COIN_ITEM_TYPES):
    """AISystem.TreasureBoxResult `0x14b8e4c` for one call: one Dequeue, one arm.

    Returns the dispatch record with a `retention` classification attached (see `retention_of`),
    so a caller can tell a confirmed storage write from a pending spawn, a pending choice, a
    presentation call or a bare interface handoff. No capacity clamp.
    """
    record = _dispatch_result(port, result_queue, chest, adds_popup, coin_item_types)
    record['retention'] = retention_of(record)
    if record['storageErrors']:
        record['state'] = 'suppressed'
        record['reason'] = ('modelled storage callback raised without acknowledgement: '
                            + '; '.join(f"{item['kind']}: {item['error']}"
                                        for item in record['storageErrors']))
        record['retention'] = retention_of(record)
    return record


def _dispatch_result(port, result_queue, chest, adds_popup, coin_item_types):
    """The dispatch body: `Queue.Dequeue` `0x1f9ffa4`, then one arm.

    `result_queue` is the receipt's Queue<TreasureData.TreasureResult> and is consumed in place
    (FIFO). Native ordering: an empty queue ends the call, then the reward row is resolved out of
    `data.Data.GetData` (whose jump table covers DataType 0..0x31), an unfound row is marked
    found, then the DataType > 0x23 gate, and only then the `ItemData.<category>` arm. Because
    mark-found precedes that gate, a DataType 0x24..0x31 reward still marks its row found before
    the plain epilogue rejects it (native-executed in check_combat_receipt_dispatch.py).
    """
    record = dict(state=None, reason=None, arm=None, dataType=None, dataId=None, num=None,
                  category=None, type=None, markedFound=False, grants=[], effects=[],
                  popup=None, stockCall=None, storageErrors=[])
    if not result_queue:
        record['state'] = 'empty'
        record['reason'] = 'queue empty; TreasureBoxResult returns without dispatch'
        return record
    result = result_queue.pop(0)
    if result is None:
        record['state'] = 'null-result'
        record['reason'] = 'null TreasureResult'
        return record
    data_type, data_id, num = result['dataType'], result['dataId'], result['num']
    record.update(dataType=data_type, dataId=data_id, num=num)
    if data_id < 0:
        record['state'] = 'data-id-out-of-range'
        record['reason'] = 'DataId is compared unsigned against the table length'
        return record
    if data_type < 0 or data_type > DATA_TYPE_TABLE_LIMIT:
        # GetData's jump table is 0..0x31; outside it there is no row to resolve or mark found.
        record['state'] = 'unsupported-data-type'
        record['reason'] = (f'DataType outside the GetData table (0..{DATA_TYPE_TABLE_LIMIT}) '
                            'reaches the plain epilogue')
        return record
    row = port['resolve'](data_type, data_id)
    if row is None:
        record['state'] = 'unresolved-data'
        record['reason'] = 'data.Data.GetData(DataType)[DataId] is null'
        return record
    if port['row_state'](row) == STATE_UNFOUND:
        record['markedFound'] = True
        port['mark_found'](row)
    if data_type > MAX_DATA_TYPE:
        record['state'] = 'unsupported-data-type'
        record['reason'] = (f'DataType > {MAX_DATA_TYPE} reaches the plain epilogue after the row '
                            'is resolved and marked found')
        return record
    if data_type in ISTOCK_DATA_TYPES:
        return _stock_path(port, record, row, chest, adds_popup)
    if data_type != ITEM_DATA_TYPE:
        record['state'] = 'unsupported-data-type'
        record['reason'] = 'DataType is neither an accepted IStock type nor ItemData'
        return record
    if not port['is_item_data'](row):
        record['state'] = 'row-not-itemdata'
        record['reason'] = 'cast to data.ItemData fails'
        return record
    category = row.get('category')
    record['category'] = category
    record['type'] = row.get('type')
    if category is None or category - CATEGORY_DIA < 0 or category - CATEGORY_DIA > 4:
        record['state'] = 'suppressed'
        record['arm'] = 'no-grant'
        record['reason'] = f'category {category} falls outside the jump table (2..6)'
        return record
    if category in (CATEGORY_DIA, CATEGORY_POINT):
        return _dia_point_arm(port, record, row, chest)
    if category == CATEGORY_GLOBAL_ITEM:
        return _global_item_arm(port, record, row, chest)
    if category == CATEGORY_ITEM:
        return _item_arm(port, record, row, chest, data_id, coin_item_types)
    return _ticket_arm(port, record, row)


def _grant(record, kind, **fields):
    grant = dict(kind=kind, **fields)
    record['grants'].append(grant)
    return grant


def _storage_grant(record, kind, call, **fields):
    """Record one modelled storage channel and the acknowledgement its adapter returned.

    `call` invokes the port with the native-equivalent arguments and returns the adapter's
    acknowledgement. The channel, arguments and argument order are recorded exactly as the native
    call site uses them; only `storage_acknowledged` decides confirmation. A raising callback is
    recorded in `record['storageErrors']` as lifecycle evidence and never confirms a receipt, so a
    thrown adapter cannot invent retained inventory nor lose the fact that the channel was reached.
    """
    ack, error = None, None
    try:
        ack = call()
    except Exception as raised:  # evidence is recorded, not swallowed
        error = f'{type(raised).__name__}: {raised}'
    grant = _grant(record, kind, acknowledged=storage_acknowledged(ack), **fields)
    if error is not None:
        grant['error'] = error
        record['storageErrors'].append(dict(kind=kind, error=error))
    return grant


def _effect(port, record, chest, effect_type, value1, value2):
    """Shared CreateEffect tail `0x14b9500`: chest position with y + 20, maxFrame 40, scale 100."""
    x, y, z = port['chest_position'](chest)
    effect = dict(type=effect_type, value1=value1, value2=value2, maxFrame=EFFECT_MAX_FRAME,
                  scale=EFFECT_SCALE, position=(x, _f32(y + EFFECT_Y_OFFSET), z))
    record['effects'].append(effect)
    port['create_effect'](effect_type, effect['position'], value1, value2,
                          EFFECT_MAX_FRAME, EFFECT_SCALE)
    return effect


def _suppress(record, arm, reason):
    record['state'] = 'suppressed'
    record['arm'] = arm
    record['reason'] = reason
    return record


def _dia_point_arm(port, record, row, chest):
    """Categories 2/3 (`DIA`, `POINT`) at `0x14b912c`, including the material fallback."""
    record['arm'] = 'cat2-3-dia-point'
    item_type = row.get('type')
    param_id = i32(item_type - TYPE_DIA) if item_type is not None else None
    if item_type == TYPE_POINT_STAMINA:
        _storage_grant(record, 'add_stamina', lambda: port['add_stamina'](record['num']),
                       num=record['num'])
        if port['is_alive'](chest):
            _effect(port, record, chest, EFFECT_COIN_OR_STAMINA, record['num'], param_id)
        record['state'] = 'granted'
        return record
    if item_type == TYPE_DIA:
        _storage_grant(record, 'add_coin', lambda: port['add_coin'](record['num'], 4),
                       num=record['num'], logType=4, paramId=param_id)
        if port['is_alive'](chest):
            _effect(port, record, chest, EFFECT_COIN_OR_STAMINA, record['num'], param_id)
        record['state'] = 'granted'
        return record
    record['arm'] = 'cat2-3-other-material'
    if not port['is_alive'](chest):
        return _suppress(record, 'cat2-3-other-material', 'chest is not alive')
    material = next((m for m in port['material_rows']() if m.get('type') == param_id), None)
    if material is None:
        return _suppress(record, 'cat2-3-other-material',
                         f'no MaterialData with type {param_id} (FirstOrDefault default)')
    _grant(record, 'create_material', materialId=material.get('id'), num=record['num'],
           paramId=param_id)
    port['create_material'](port['chest_position'](chest), material, record['num'])
    _grant(record, 'add_modify_animation', maxFrame=MODIFY_ANIMATION_MAX_FRAME,
           alpha=MODIFY_ANIMATION_ALPHA)
    port['add_modify_animation'](MODIFY_ANIMATION_MAX_FRAME, MODIFY_ANIMATION_ALPHA)
    record['state'] = 'granted'
    return record


def _stock_path(port, record, row, chest, adds_popup):
    """Accepted IStock data types (`0x14b906c` -> join `0x14b91b4`): no ItemData category arm.

    The grant is one interface method call resolved through the unnamed resolver `0x1332890`
    (`w2 = 2`); the popup and the effect are driven by the two TreasureData tables. Both the
    interface call and every display value are port calls, so the unnamed resolver and the
    cctor-built tables stay explicit inputs.
    """
    record['arm'] = 'istock-data-type'
    if port['is_istock'](row):
        record['stockCall'] = dict(num=record['num'])
        port['stock_add'](row, record['num'])
    if not port['is_alive'](chest):
        record['state'] = 'granted' if record['stockCall'] else 'suppressed'
        if not record['stockCall']:
            record['reason'] = 'chest is not alive and the reward row is not IStock'
        return record
    text = port['popup_text'](record['dataType'])
    if adds_popup and text:
        popup = dict(text=text, rowName=port['row_name'](row), num=record['num'],
                     image=port['treasure_image'](chest), resId=POPUP_RES_ID, sebId=POPUP_SEB_ID)
        record['popup'] = popup
        port['add_popup'](popup['text'], popup['rowName'], popup['num'], popup['image'])
    effect_kind = port['effect_kind'](record['dataType'])
    if effect_kind != EFFECT_TYPE_MISSING:
        _effect(port, record, chest, effect_kind, record['num'], row.get('id'))
    if record['stockCall'] is None and record['popup'] is None and not record['effects']:
        return _suppress(record, 'istock-data-type',
                         'no IStock call, no popup and no effect-table entry')
    record['state'] = 'granted'
    return record


def _global_item_arm(port, record, row, chest):
    """Category 4 (`GLOBAL_ITEM`) at `0x14b9538`: AddItem(town: null), then effect type 13."""
    record['arm'] = 'cat4-global-item'
    _storage_grant(record, 'add_item', lambda: port['add_item'](None, row, record['num']),
                   town=None, dataId=row.get('id'), num=record['num'])
    if port['is_alive'](chest):
        _effect(port, record, chest, EFFECT_GLOBAL_ITEM, record['num'], row.get('id'))
    record['state'] = 'granted'
    return record


def _item_arm(port, record, row, chest, data_id, coin_item_types=COIN_ITEM_TYPES):
    """Category 5 (`ITEM`) at `0x14b9604` with the not-alive fallback `0x14b98bc`."""
    record['arm'] = 'cat5-item'
    if port['is_alive'](chest):
        if is_coin_item(row, coin_item_types):
            record['arm'] = 'cat5-item-coin'
        elif is_food(row):
            record['arm'] = 'cat5-item-food'
        else:
            return _suppress(record, 'cat5-item-alive-other',
                             'alive chest reward is neither a coin item nor type 1')
        position = port['chest_position'](chest)
        product = port['create_product'](position, ITEM_DATA_TYPE, data_id, record['num'])
        if product is None:
            return _suppress(record, record['arm'], 'World.CreateProduct returned null')
        _grant(record, 'create_product', dataType=ITEM_DATA_TYPE, dataId=data_id,
               num=record['num'], position=position)
        gatherable = port['add_gatherable'](product, GATHERABLE_LIFE_FRAME)
        if gatherable is None:
            return _suppress(record, record['arm'], 'Entity.AddGatherable returned null')
        _grant(record, 'add_gatherable', lifeFrame=GATHERABLE_LIFE_FRAME)
        _grant(record, 'add_modify_animation', maxFrame=MODIFY_ANIMATION_MAX_FRAME,
               alpha=MODIFY_ANIMATION_ALPHA)
        port['add_modify_animation'](MODIFY_ANIMATION_MAX_FRAME, MODIFY_ANIMATION_ALPHA)
        record['state'] = 'granted'
        return record
    if not requires_town_select(row):
        record['arm'] = 'cat5-item-not-alive-plain-add'
        return _ticket_arm(port, record, row)
    towns = port['towns_count']()
    if towns >= 2:
        record['arm'] = 'cat5-item-town-select'
        form = port['create_item_add_to_town_select'](row, record['num'])
        _grant(record, 'create_item_add_to_town_select', townsCount=towns, num=record['num'])
        port['push_form'](form)
        _grant(record, 'push_form', form=form)
        record['state'] = 'granted'
        return record
    record['arm'] = 'cat5-item-selected-town'
    town = port['selected_town']()
    _storage_grant(record, 'add_item', lambda: port['add_item'](town, row, record['num']),
                   town=town, dataId=row.get('id'), num=record['num'], townsCount=towns)
    record['state'] = 'granted'
    return record


class ReceiptLedger:
    """Queued, dispatched and retained receipt accounting for one run; no silent drops.

    One native `TreasureBoxResult` call consumes exactly one queued result, so a receipt that
    carries several results is dispatched repeatedly until its queue is exhausted: each call's
    record is appended to `entry['dispatches']` (never overwritten), and the entry stays
    `partial` while any results remain. Only a fully consumed entry whose records contain a
    confirmed modelled storage write (see `retention_of`) is `retained`; an entry that was merely
    consumed with pending/visual grants is `dispatched`, and a mixed entry is `mixed`. `censor`
    marks a horizon: results still queued stay in `entry['results']` and are reported as censored
    rather than counted as losses or as retained inventory.
    """
    # Entry-level states. `queued` and `partial` are non-terminal; the rest are terminal.
    STATES = ('queued', 'partial', 'retained', 'dispatched', 'suppressed', 'mixed',
              'unresolved-data', 'data-id-out-of-range', 'unsupported-data-type',
              'row-not-itemdata', 'empty', 'null-result')
    UNRESOLVED_STATES = ('unresolved-data', 'data-id-out-of-range', 'unsupported-data-type',
                         'row-not-itemdata')
    TERMINAL = ('retained', 'dispatched', 'suppressed', 'mixed') + UNRESOLVED_STATES
    # Result-level states: one per native dispatch call.
    RESULT_STATES = ('granted', 'suppressed', 'empty', 'null-result') + UNRESOLVED_STATES

    def __init__(self):
        self.entries = []
        self.censored = False
        self.censor_reason = None

    def queue(self, treasure_id, tick=None, target=None, results=None):
        entry = dict(treasureId=treasure_id, tick=tick, target=target, state='queued',
                     results=list(results) if results is not None else [], dispatches=[])
        self.entries.append(entry)
        return entry

    def dispatch(self, port, entry, chest=None, adds_popup=False):
        """Consume exactly one result; accumulate the record; refuse an exhausted retry."""
        if entry['state'] not in ('queued', 'partial'):
            raise ValueError(f'Receipt is not dispatchable from state {entry["state"]!r}')
        if not entry['results']:
            raise ValueError('Receipt result queue is exhausted; no further dispatch')
        record = dispatch_result(port, entry['results'], chest, adds_popup)
        entry['dispatches'].append(record)
        entry['state'] = self._entry_state(entry)
        return record

    @classmethod
    def _entry_state(cls, entry):
        """Terminal state only once every result is consumed; otherwise `partial`.

        `retained` is chest-level and honest: every consumed result must carry a confirmed storage
        write and none may be unresolved, suppressed or deferred (a pending ground collection or a
        town-selection choice). A confirmed result next to an unresolved/deferred one is `mixed`, so
        one real receipt never makes a half-known chest look wholly received; the result-level
        `retainedReceipts` count still reports each confirmed result separately.
        """
        records = entry['dispatches']
        if not records:
            return 'queued'
        if entry['results']:
            return 'partial'
        confirmed = [record for record in records if record['retention']['confirmedReceipts']]
        if confirmed:
            wholly = all(record['retention']['confirmedReceipts']
                         and not record['retention']['pendingCollection']
                         and not record['retention']['pendingChoice'] for record in records)
            return 'retained' if wholly else 'mixed'
        states = {record['state'] for record in records}
        if states == {'granted'}:
            return 'dispatched'
        if states == {'suppressed'}:
            return 'suppressed'
        if states <= set(cls.UNRESOLVED_STATES):
            return 'unresolved-data'
        return 'mixed'

    def censor(self, reason):
        self.censored, self.censor_reason = True, reason
        return self

    def report(self):
        counts = {name: sum(entry['state'] == name for entry in self.entries)
                  for name in self.STATES}
        results = {}
        for entry in self.entries:
            for record in entry['dispatches']:
                results[record['state']] = results.get(record['state'], 0) + 1
        confirmed = [grant for entry in self.entries for record in entry['dispatches']
                     for grant in record['grants']
                     if grant['kind'] in CONFIRMED_RECEIPT_GRANTS
                     and grant.get('acknowledged') is True]
        pending = [grant for entry in self.entries for record in entry['dispatches']
                   for grant in record['grants']
                   if grant['kind'] in (PENDING_COLLECTION_GRANTS + PENDING_CHOICE_GRANTS)]
        remaining = sum(len(entry['results']) for entry in self.entries)
        return dict(
            entries=[dict(entry) for entry in self.entries], counts=counts,
            queued=counts['queued'], partial=counts['partial'],
            dispatched=counts['dispatched'] + counts['retained'],
            retained=counts['retained'], suppressed=counts['suppressed'],
            mixed=counts['mixed'],
            unresolved=sum(counts[name] for name in self.UNRESOLVED_STATES),
            resultsQueued=sum(len(entry['dispatches']) + len(entry['results'])
                              for entry in self.entries),
            resultsDispatched=sum(len(entry['dispatches']) for entry in self.entries),
            resultsRemaining=remaining, resultsByState=results,
            retainedReceipts=len(confirmed), retainedGrants=confirmed,
            pendingGrants=pending,
            censored=self.censored, censorReason=self.censor_reason,
            censoredPendingResults=remaining if self.censored else 0,
            limits=['Queued rewards are Leaving-entry callbacks, not inventory receipts.',
                    'Dispatched rewards need a collected chest and explicit caller-supplied '
                    'ItemData/MaterialData rows; the chest-open result derivation from '
                    'TreasureData item/equip fields is not decoded.',
                    'Retained means a modelled storage write whose adapter returned the explicit '
                    'structured acknowledgement (AppData.AddItem/AddCoin/AddStamina; a void/False '
                    'return is not proof). A ground spawn, a town-selection form, a material/'
                    'effect/popup call or the opaque IStock interface handoff is dispatched or '
                    'pending, never retained inventory; confirmed next to unresolved/deferred is '
                    'mixed, not retained.',
                    'No capacity clamp is modelled: RankData.maxTreasureCapacity is unread '
                    'on this path.'])


def count_stock(warehouses, material_type, parameter_id):
    """ecs.KingdomSystem CountTreasureStock/CountItemStock shape: Where(materialType) then Sum.

    `WarehouseSystem.EnumerateWarehouses()` rows are supplied as materialType plus the entity
    parameter mapping; the parameter reads with `Param.GetValue(e, id, 0)` semantics.
    """
    return sum(warehouse.get('parameters', {}).get(parameter_id, 0)
               for warehouse in warehouses
               if warehouse.get('materialType') == material_type)


def count_treasure_stock(warehouses):
    """Where(WarehouseData.materialType == Param.Treasure).Sum(Param.GetValue(e, 7, 0)) `0x159f554`."""
    return count_stock(warehouses, MATERIAL_TYPE_TREASURE, PARAM_TREASURE)


def _ticket_arm(port, record, row):
    """Category 6 (`TICKET`) at `0x14b9948`, also the tail of the item not-alive path."""
    if record['arm'] is None:
        record['arm'] = 'cat6-ticket'
    _storage_grant(record, 'add_item', lambda: port['add_item'](None, row, record['num']),
                   town=None, dataId=row.get('id'), num=record['num'])
    record['state'] = 'granted'
    return record


# Parameter.Serialize `0x1682a9c` writes these fields at these widths, in this order; the
# Parameter field offsets in dump.cs 9062 (id_ @0x10 ... expBuf_ @0x34) follow the same order.
PARAM_SAVE_FIELDS = (('id', 1), ('value', 4), ('maxValue', 4), ('extraValue', 4),
                     ('extraMaxValue', 4), ('level', 2), ('maxLevel', 2), ('exp', 4),
                     ('maxExp', 4), ('expBuf', 4))
# 1 byte + 4 int32 + 2 int16 + 3 int32: the exact per-parameter wire span, same order on both
# sides, with no length prefix, padding or version tag (`0x1682a9c` vs `0x1682b98`).
PARAM_SAVE_BYTES = sum(width for _, width in PARAM_SAVE_FIELDS)
INT_MAX = 2147483647


def _signed(value, bits):
    masked = i32(value) & ((1 << bits) - 1)
    return masked - (1 << bits) if masked >= 1 << (bits - 1) else masked


def _pack(value, width):
    # `StreamUtil.Write*` emits multi-byte fields big-endian: the native writers call
    # BitConverter.GetBytes (a plain `strh`/`str` store) and then reverse the bytes in place at
    # 0x2393330, so WriteShort(300) puts 01 2c on the stream. Native-executed in
    # RE-evidence/20260912-combat/composition-checks.json; the earlier little-endian assumption in
    # this module was wrong.
    if width == 1:
        return struct.pack('>b', _signed(value, 8))
    if width == 2:
        return struct.pack('>h', _signed(value, 16))
    return struct.pack('>i', i32(value))


def serialize_parameter(parameter):
    """Parameter.Serialize `0x1682a9c`: WriteByte id, 4x WriteInt, 2x WriteShort, 3x WriteInt."""
    return b''.join(_pack(parameter.get(name, 0), width) for name, width in PARAM_SAVE_FIELDS)


def serialize_param_set(parameters):
    """ParamSet.Serialize `0x16831d0`: WriteBoolean(hasSet), WriteByte Count, then entries.

    The count and every key are single signed bytes (`(sbyte)Count`, `(sbyte)Key` of the enumerated
    `KeyValuePair<int,Parameter>`), so a ParamSet only round-trips up to 127 parameters: the native
    writer truncates the count instead of throwing, and the reader sign-extends it, so 128+ entries
    read back as none. This helper refuses a count above 255 rather than reproducing that silent
    truncation. Enumeration order is the dictionary's own order and is preserved here.
    """
    if parameters is None:
        return struct.pack('<B', 0)
    if len(parameters) > 255:
        raise ValueError('ParamSet writes its Count as one byte')
    out = bytearray(struct.pack('<B', 1))
    out += struct.pack('<B', len(parameters))
    for key, parameter in parameters.items():
        out += struct.pack('<b', _signed(key, 8))
        out += serialize_parameter(parameter)
    return bytes(out)


def deserialize_param_set(data):
    """ParamSet.Deserialize `0x16833ec` + Parameter.Deserialize `0x1682b98` (width-symmetric).

    `0x16833ec` = `ReadBoolean` (`0x23aca88`); `false` leaves `parameters_` null, which is why a
    missing ParamSet is one byte while an empty one is two. The count is a `ReadByte` widened with
    `sxtb` and the entry loop is entered only when `count >= 1` (`cmp w24,#1` / `b.lt`), so the
    signed read is reproduced here: a count byte of 128..255 is negative and yields an **empty**
    set while the 33n data bytes stay unread (`None` is returned only for the null case, so the two
    are distinguishable). Each entry is a `sxtb` key byte plus a 33-byte `Parameter`; the key and
    the Parameter's own `id_` are read independently and are never reconciled on load
    (`ParameterComponent.OnDeserialized` `0x14ceabc` only prunes `extras`), so a mismatch is kept
    as `{key: {'id': ...}}`.

    `exp` is clamped **on read only** (the native writer emits `exp_` raw), after all ten fields:
    `if maxExp != INT_MAX: exp = 0 if exp < 0 else min(exp, maxExp)`. The negative test comes first
    (`tbnz w9,#0x1f`) and the floor is not re-applied to the `exp > maxExp` result, so `exp = 0` with
    `maxExp = -5` leaves `-5` (native-executed: `RE-evidence\20260914-b3-receipt\
    param-clone-boundary-checks.json`).
    """
    if data[0] == 0:
        return None
    count = struct.unpack_from('<b', data, 1)[0]
    offset = 2
    parameters = {}
    for _ in range(count):
        key = struct.unpack_from('<b', data, offset)[0]
        offset += 1
        parameter = {}
        for name, width in PARAM_SAVE_FIELDS:
            if width == 1:
                parameter[name] = struct.unpack_from('>b', data, offset)[0]
            elif width == 2:
                parameter[name] = struct.unpack_from('>h', data, offset)[0]
            else:
                parameter[name] = struct.unpack_from('>i', data, offset)[0]
            offset += width
        if parameter['maxExp'] != INT_MAX:
            parameter['exp'] = 0 if parameter['exp'] < 0 else min(parameter['exp'],
                                                                 parameter['maxExp'])
        parameters[key] = parameter
    return parameters
