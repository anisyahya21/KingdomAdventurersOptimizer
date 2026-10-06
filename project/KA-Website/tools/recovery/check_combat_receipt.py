"""Portable B3 receipt checks: arm choice, grant order, ledger states, stock and param save.

Expectations are written out per decoded arm (HANDOFF-b3-treasure-receipt.md section 6/9) and do
not call the module's own arm helpers. No native execution, no gameplay claim: this checks the
portable receipt model against the recorded arm table only.
"""
import json
import struct
from pathlib import Path
from recover_special_combat import table
from combat_receipt import (PARAM_SAVE_BYTES, PARAM_SAVE_FIELDS, PORT_KEYS, ReceiptLedger,
    count_treasure_stock, deserialize_param_set, dispatch_result, is_coin_item, is_food, is_ticket,
    package_receipt, requires_town_select, serialize_param_set, treasure_result)

# B3 evidence lives under the tree this file physically sits in. Each tree has its own folder: the
# live tree at the workspace root, and the working copy at its own root since 2026-09-14, when the
# junction that used to make them one physical folder was replaced by a real copy.
#
# Resolving to the NEAREST match is what keeps a run inside one tree. Resolving to the outermost
# match sent the copy's runs into the live tree - measured 2026-09-14: running the copy's batch
# rewrote the live tree's receipt-dispatch-checks.json (byte-identical content, but a live write
# from the copy, which the isolation rule forbids). find_root in slice_b3_treasure_receipt.py takes
# the nearest match too, so both tools agree.
def workspace_root():
    found = [parent for parent in Path(__file__).resolve().parents
             if (parent / 'RE-evidence/20260914-b3-receipt').is_dir()]
    if not found:
        raise SystemExit('could not locate the workspace root (RE-evidence/20260914-b3-receipt)')
    return found[0]


OUT = workspace_root() / 'RE-evidence/20260914-b3-receipt/receipt-dispatch-checks.json'
POSITION = (0.0, 48.0, 72.0)
EFFECT_POSITION = (0.0, 68.0, 72.0)
MUTATIONS = ('add_item','add_coin','add_stamina','create_material','create_product',
             'add_gatherable','add_modify_animation','create_effect','add_popup','stock_add',
             'create_item_add_to_town_select','push_form','mark_found')


class Recorder:
    """Explicit receipt port with ordered call recording; no world is simulated.

    The three modelled storage channels acknowledge a successful write with the structured
    `{'confirmed': True, 'channel': ...}` flag (`storage_ack=True`, the default). `storage_ack`
    overrides that return for every channel (e.g. `False`/`None` for the unconfirmed counterexamples)
    and `storage_ack_by_channel` overrides one channel; `storage_raise` makes named channels raise so
    the callback-exception path is exercised. Channel names and arguments are unchanged either way.
    """

    def __init__(self, rows, materials=(), alive=True, towns=2, town='town-entity', is_item=True,
                 is_istock=True, effect_kinds=None, popup_names=None, product='product',
                 gatherable='gatherable', names=None, storage_ack=True,
                 storage_ack_by_channel=None, storage_raise=()):
        self.rows, self.materials, self.alive, self.towns, self.town = rows, list(materials), alive, towns, town
        self.is_item, self.istock, self.product, self.gatherable = is_item, is_istock, product, gatherable
        self.effect_kinds = dict(effect_kinds or {})
        self.popup_names = dict(popup_names or {})
        self.names = dict(names or {})
        self.storage_ack = storage_ack
        self.storage_ack_by_channel = dict(storage_ack_by_channel or {})
        self.storage_raise = tuple(storage_raise)
        self.calls = []
        self.port = {key: getattr(self, key) for key in PORT_KEYS}

    def _storage(self, channel):
        if channel in self.storage_raise:
            raise RuntimeError(f'modelled storage adapter {channel} failed')
        value = self.storage_ack_by_channel.get(channel, self.storage_ack)
        return dict(confirmed=True, channel=channel) if value is True else value

    def log(self, name, *args):
        self.calls.append(dict(call=name, args=[list(a) if isinstance(a, tuple) else a for a in args]))

    def mutations(self):
        return [(entry['call'], *entry['args']) for entry in self.calls if entry['call'] in MUTATIONS]

    def resolve(self, data_type, data_id):
        self.log('resolve', data_type, data_id)
        rows = self.rows.get(data_type) or []
        return rows[data_id] if data_id < len(rows) else None

    def row_state(self, row):
        return row.get('state', 1)

    def mark_found(self, row):
        self.log('mark_found', row.get('id'))

    def is_istock(self, row):
        return self.istock

    def stock_add(self, row, num):
        self.log('stock_add', row.get('id'), num)

    def is_item_data(self, row):
        return self.is_item

    def is_alive(self, chest):
        self.log('is_alive', chest)
        return self.alive

    def material_rows(self):
        self.log('material_rows')
        return self.materials

    def chest_position(self, chest):
        return POSITION

    def treasure_image(self, chest):
        return 7

    def row_name(self, row):
        return self.names.get(row.get('id'), 'row')

    def add_item(self, town, row, num):
        self.log('add_item', town, row.get('id'), num)
        return self._storage('add_item')

    def add_coin(self, num, log_type):
        self.log('add_coin', num, log_type)
        return self._storage('add_coin')

    def add_stamina(self, num):
        self.log('add_stamina', num)
        return self._storage('add_stamina')

    def create_material(self, position, material, num):
        self.log('create_material', material.get('id'), list(position), num)

    def create_product(self, position, data_type, data_id, num):
        self.log('create_product', data_type, data_id, list(position), num)
        return self.product

    def add_gatherable(self, product, life_frame):
        self.log('add_gatherable', product, life_frame)
        return self.gatherable

    def add_modify_animation(self, max_frame, alpha):
        self.log('add_modify_animation', max_frame, alpha)

    def create_effect(self, effect_type, position, value1, value2, max_frame, scale):
        self.log('create_effect', effect_type, list(position), value1, value2, max_frame, scale)

    def popup_text(self, data_type):
        return self.popup_names.get(data_type)

    def effect_kind(self, data_type):
        return self.effect_kinds.get(data_type, -1)

    def add_popup(self, text, row_name, num, image):
        self.log('add_popup', text, row_name, num, image)

    def create_item_add_to_town_select(self, row, num):
        self.log('create_item_add_to_town_select', row.get('id'), num)
        return 'select-form'

    def push_form(self, form):
        self.log('push_form', form)

    def towns_count(self):
        self.log('towns_count')
        return self.towns

    def selected_town(self):
        self.log('selected_town')
        return self.town


EP, CP = list(EFFECT_POSITION), list(POSITION)


def item(data_id, **fields):
    return {24: [None] * data_id + [dict(id=data_id, **fields)]}


def run_case(case):
    recorder = Recorder(case['rows'], case.get('materials', ()), case.get('alive', True),
                        case.get('towns', 2), case.get('town', 'town-entity'),
                        is_item=case.get('is_item', True), is_istock=case.get('is_istock', True),
                        effect_kinds=case.get('effect_kinds'), popup_names=case.get('popup_names'),
                        product=case.get('product', 'product'),
                        gatherable=case.get('gatherable', 'gatherable'))
    queue = list(case['results'])
    record = dispatch_result(recorder.port, queue, chest=case.get('chest', 'chest'),
                             adds_popup=case.get('adds_popup', False))
    assert record['state'] == case['state'], (case['name'], record['state'], case['state'])
    assert record['arm'] == case['arm'], (case['name'], record['arm'], case['arm'])
    assert recorder.mutations() == case['mutations'], (case['name'], recorder.mutations(),
                                                       case['mutations'])
    if 'markedFound' in case:
        assert record['markedFound'] == case['markedFound'], case['name']
    return dict(name=case['name'], state=record['state'], arm=record['arm'],
                dataType=record['dataType'], dataId=record['dataId'], num=record['num'],
                category=record['category'], type=record['type'],
                markedFound=record['markedFound'], grants=[g['kind'] for g in record['grants']],
                effects=record['effects'], popup=record['popup'], calls=recorder.calls)


# Every expectation below is the decoded arm behaviour, written out literally; RVAs are cited in
# combat_receipt.py and HANDOFF-b3-treasure-receipt.md section 9.
CASES = (
    dict(name='cat2-dia-alive', rows=item(100, category=2, type=8), results=[treasure_result(24, 100, 5)],
         state='granted', arm='cat2-3-dia-point',
         mutations=[('add_coin', 5, 4), ('create_effect', 8, EP, 5, 0, 40, 100)]),
    dict(name='cat2-dia-dead', rows=item(100, category=2, type=8), results=[treasure_result(24, 100, 5)],
         alive=False, state='granted', arm='cat2-3-dia-point', mutations=[('add_coin', 5, 4)]),
    dict(name='cat3-stamina-alive', rows=item(100, category=3, type=14), results=[treasure_result(24, 100, 5)],
         state='granted', arm='cat2-3-dia-point',
         mutations=[('add_stamina', 5), ('create_effect', 8, EP, 5, 6, 40, 100)]),
    dict(name='cat2-other-material-alive', rows=item(100, category=2, type=9),
         results=[treasure_result(24, 100, 5)], materials=[dict(id=0, type=1)],
         state='granted', arm='cat2-3-other-material',
         mutations=[('create_material', 0, CP, 5), ('add_modify_animation', 20, 255)]),
    dict(name='cat2-other-material-dead', rows=item(100, category=2, type=9),
         results=[treasure_result(24, 100, 5)], materials=[dict(id=0, type=1)], alive=False,
         state='suppressed', arm='cat2-3-other-material', mutations=[]),
    dict(name='cat2-other-material-missing', rows=item(100, category=2, type=13),
         results=[treasure_result(24, 100, 5)], materials=[dict(id=0, type=1)],
         state='suppressed', arm='cat2-3-other-material', mutations=[]),
    dict(name='cat4-global-item-alive', rows=item(100, category=4, type=0),
         results=[treasure_result(24, 100, 5)], state='granted', arm='cat4-global-item',
         mutations=[('add_item', None, 100, 5), ('create_effect', 13, EP, 5, 100, 40, 100)]),
    dict(name='cat4-global-item-dead', rows=item(100, category=4, type=0),
         results=[treasure_result(24, 100, 5)], alive=False, state='granted',
         arm='cat4-global-item', mutations=[('add_item', None, 100, 5)]),
    dict(name='cat5-coin-alive', rows=item(100, category=5, type=15),
         results=[treasure_result(24, 100, 5)], state='granted', arm='cat5-item-coin',
         mutations=[('create_product', 24, 100, CP, 5), ('add_gatherable', 'product', 12000),
                    ('add_modify_animation', 20, 255)]),
    dict(name='cat5-food-alive', rows=item(100, category=5, type=1),
         results=[treasure_result(24, 100, 5)], state='granted', arm='cat5-item-food',
         mutations=[('create_product', 24, 100, CP, 5), ('add_gatherable', 'product', 12000),
                    ('add_modify_animation', 20, 255)]),
    dict(name='cat5-alive-other', rows=item(100, category=5, type=99),
         results=[treasure_result(24, 100, 5)], state='suppressed', arm='cat5-item-alive-other',
         mutations=[]),
    dict(name='cat5-not-alive-plain', rows=item(100, category=5, type=15), alive=False,
         results=[treasure_result(24, 100, 5)], state='granted',
         arm='cat5-item-not-alive-plain-add', mutations=[('add_item', None, 100, 5)]),
    dict(name='cat5-not-alive-town-select', rows=item(100, category=5, type=16, flag=512),
         alive=False, towns=2, results=[treasure_result(24, 100, 5)], state='granted',
         arm='cat5-item-town-select',
         mutations=[('create_item_add_to_town_select', 100, 5), ('push_form', 'select-form')]),
    dict(name='cat5-not-alive-selected-town', rows=item(100, category=5, type=17, flag=512),
         alive=False, towns=1, results=[treasure_result(24, 100, 5)], state='granted',
         arm='cat5-item-selected-town', mutations=[('add_item', 'town-entity', 100, 5)]),
    dict(name='cat6-ticket', rows=item(100, category=6, type=18),
         results=[treasure_result(24, 100, 5)], state='granted', arm='cat6-ticket',
         mutations=[('add_item', None, 100, 5)]),
    dict(name='cat0-bonus', rows=item(100, category=0, type=2),
         results=[treasure_result(24, 100, 5)], state='suppressed', arm='no-grant', mutations=[]),
    dict(name='cat1-billing', rows=item(100, category=1, type=3),
         results=[treasure_result(24, 100, 5)], state='suppressed', arm='no-grant', mutations=[]),
    dict(name='cat7-outside-table', rows=item(100, category=7, type=19),
         results=[treasure_result(24, 100, 5)], state='suppressed', arm='no-grant', mutations=[]),
    dict(name='ticket-unfound-marked', rows=item(100, category=6, type=18, state=0),
         results=[treasure_result(24, 100, 5)], state='granted', arm='cat6-ticket',
         markedFound=True, mutations=[('mark_found', 100), ('add_item', None, 100, 5)]),
    dict(name='row-not-itemdata', rows=item(100, category=6, type=18), is_item=False,
         results=[treasure_result(24, 100, 5)], state='row-not-itemdata', arm=None, mutations=[]),
    dict(name='no-capacity-clamp', rows=item(100, category=6, type=18, maxTreasureCapacity=0),
         results=[treasure_result(24, 100, 5)], state='granted', arm='cat6-ticket',
         mutations=[('add_item', None, 100, 5)]),
    dict(name='istock-11-dead', rows={11: [dict(id=900, state=1)]}, results=[treasure_result(11, 0, 5)],
         alive=False, state='granted', arm='istock-data-type',
         mutations=[('stock_add', 900, 5)]),
    dict(name='istock-11-alive-no-tables', rows={11: [dict(id=900, state=1)]},
         results=[treasure_result(11, 0, 5)], state='granted', arm='istock-data-type',
         mutations=[('stock_add', 900, 5)]),
    dict(name='istock-35-popup-and-effect', rows={35: [dict(id=901, state=0)]},
         results=[treasure_result(35, 0, 7)], adds_popup=True, effect_kinds={35: 13},
         popup_names={35: 'DataTypeName'}, state='granted', arm='istock-data-type',
         markedFound=True,
         mutations=[('mark_found', 901), ('stock_add', 901, 7),
                    ('add_popup', 'DataTypeName', 'row', 7, 7),
                    ('create_effect', 13, EP, 7, 901, 40, 100)]),
    dict(name='istock-0-not-istock-row', rows={0: [dict(id=902, state=1)]},
         results=[treasure_result(0, 0, 3)], is_istock=False, state='suppressed',
         arm='istock-data-type', mutations=[]),
    dict(name='datatype-25-unaccepted', rows={25: [dict(id=903, state=1)]},
         results=[treasure_result(25, 0, 4)], state='unsupported-data-type', arm=None,
         mutations=[]),
    dict(name='datatype-36-above-gate', rows={36: [dict(id=904, state=1)]},
         results=[treasure_result(36, 0, 4)], state='unsupported-data-type', arm=None,
         mutations=[]),
    dict(name='data-id-negative', rows=item(100, category=6, type=18),
         results=[treasure_result(24, -1, 5)], state='data-id-out-of-range', arm=None, mutations=[]),
    dict(name='data-id-out-of-table', rows=item(100, category=6, type=18),
         results=[treasure_result(24, 500, 5)], state='unresolved-data', arm=None, mutations=[]),
    dict(name='queue-empty', rows=item(100, category=6, type=18), results=[],
         state='empty', arm=None, mutations=[]),
    dict(name='queue-null-result', rows=item(100, category=6, type=18), results=[None],
         state='null-result', arm=None, mutations=[]),
)


def check_ledger():
    ledger = ReceiptLedger()
    entries = [ledger.queue(treasure_id, tick=tick, target=0,
                            results=[treasure_result(24, data_id, 5)])
               for treasure_id, tick, data_id in ((710, 100, 100), (711, 101, 100),
                                                  (712, 102, 400), (713, 103, 100))]
    ledger.dispatch(Recorder(item(100, category=6, type=18)).port, entries[0])
    ledger.dispatch(Recorder(item(100, category=0, type=2)).port, entries[1])
    ledger.dispatch(Recorder(item(100, category=6, type=18)).port, entries[2])
    states = [entry['state'] for entry in ledger.entries]
    assert states == ['retained', 'suppressed', 'unresolved-data', 'queued'], states
    assert entries[0]['results'] == []
    ledger.censor('tick horizon; settlement is not implemented')
    report = ledger.report()
    assert (report['queued'], report['retained'], report['dispatched'], report['suppressed'],
            report['unresolved']) == (1, 1, 1, 1, 1), report['counts']
    assert report['censored'] and report['censorReason'].startswith('tick horizon')
    assert [grant['kind'] for grant in report['retainedGrants']] == ['add_item']
    try:
        ledger.dispatch(Recorder(item(100, category=6, type=18)).port, entries[0])
    except ValueError:
        pass
    else:
        raise AssertionError('Re-dispatch of a settled receipt was accepted')
    two = ledger.queue(714, results=[treasure_result(24, 100, 5), treasure_result(24, 100, 9)])
    ledger.dispatch(Recorder(item(100, category=6, type=18)).port, two)
    assert len(two['results']) == 1 and two['results'][0]['num'] == 9
    return report


def check_param_save():
    """`Parameter`/`ParamSet` wire widths and the read-side `exp` clamp (`0x1682a9c`/`0x1682b98`)."""
    treasure = dict(id=7, value=120, maxValue=2147483647, extraValue=0, extraMaxValue=0, level=1,
                    maxLevel=1, exp=0, maxExp=2147483647, expBuf=0)
    offset, wire = 3, {}
    for name, width in PARAM_SAVE_FIELDS:
        wire[name], offset = offset, offset + width
    assert wire == {'id': 3, 'value': 4, 'maxValue': 8, 'extraValue': 12, 'extraMaxValue': 16,
                    'level': 20, 'maxLevel': 22, 'exp': 24, 'maxExp': 28, 'expBuf': 32}, wire
    assert offset == 3 + PARAM_SAVE_BYTES == 36, offset
    blob = serialize_param_set({7: treasure})
    assert blob[:3] == bytes([1, 1, 7]) and len(blob) == 36, (blob[:3], len(blob))
    assert deserialize_param_set(blob)[7]['value'] == 120
    # The null ParamSet is one byte and stays null; the empty ParamSet is a present set whose count
    # byte is 0 -- `ReadBoolean` guards the whole read, so the two are not interchangeable.
    assert serialize_param_set(None) == b'\x00' and deserialize_param_set(b'\x00') is None
    assert serialize_param_set({}) == b'\x01\x00' and deserialize_param_set(b'\x01\x00') == {}
    assert deserialize_param_set(serialize_param_set(None)) is None
    # The clamp is literally the `b.eq` on maxExp, then `tbnz` on exp < 0, then `cmp`/`b.le`. The
    # zero floor applies to the incoming exp only: it is not re-applied to the `exp > maxExp`
    # result, so a negative maxExp can leave a negative exp (`0` / `maxExp = -5` -> `-5`).
    clamps = {(exp, limit): deserialize_param_set(serialize_param_set(
                  {7: dict(treasure, exp=exp, maxExp=limit)}))[7]['exp']
              for exp, limit in ((-5, 100), (150, 100), (-5, 2147483647), (150, 2147483647),
                                 (2147483647, 2147483647), (-2147483648, 5), (-1, -5), (0, -5),
                                 (1, -5), (0, 2147483647))}
    assert clamps == {(-5, 100): 0, (150, 100): 100, (-5, 2147483647): -5,
                      (150, 2147483647): 150, (2147483647, 2147483647): 2147483647,
                      (-2147483648, 5): 0, (-1, -5): 0, (0, -5): -5, (1, -5): -5,
                      (0, 2147483647): 0}, clamps
    # `Serialize` writes exp_ raw -- no clamp, no source mutation -- so save -> load -> save is not
    # a no-op and the correction exists only in `0x1682b98`.
    unclamped = dict(treasure, exp=150, maxExp=100)
    raw = serialize_param_set({7: unclamped})
    assert struct.unpack_from('>i', raw, wire['exp'])[0] == 150, raw.hex()
    assert struct.unpack_from('>i', raw, wire['maxExp'])[0] == 100
    assert deserialize_param_set(raw)[7]['exp'] == 100
    assert struct.unpack_from('>i', serialize_param_set(deserialize_param_set(raw)),
                              wire['exp'])[0] == 100
    assert unclamped['exp'] == 150 and unclamped['maxExp'] == 100
    # Only id_ (byte) and level_/maxLevel_ (short) narrow, and nothing re-validates them.
    narrowed = dict(treasure, level=70000, maxLevel=-1, exp=-3, maxExp=9)
    decoded = deserialize_param_set(serialize_param_set({7: narrowed}))[7]
    assert (decoded['level'], decoded['maxLevel'], decoded['exp']) == (4464, -1, 0), decoded
    # Keys are signed bytes; the key byte is the dictionary key while `id_` is the Parameter's own
    # field, read separately and never reconciled on load (`OnDeserialized` only prunes `extras`).
    keys = list(deserialize_param_set(serialize_param_set({200: treasure})))
    assert keys == [-56], keys
    patched = bytearray(blob)
    patched[wire['id']] = 9
    mismatched = deserialize_param_set(bytes(patched))
    assert list(mismatched) == [7] and mismatched[7]['id'] == 9, mismatched
    # The count byte is read sign-extended and the loop needs count >= 1, so a 128+ entry set comes
    # back empty and its 33n bytes are left unread (the rest of the component stream desynchronizes).
    many = serialize_param_set({index + 1: treasure for index in range(200)})
    assert many[1] == 200 and deserialize_param_set(many) == {}, many[:2]
    try:
        serialize_param_set({index: treasure for index in range(256)})
    except ValueError:   # this helper refuses; the native writer truncates `(sbyte)Count` instead
        pass
    else:
        raise AssertionError('The 255-parameter count guard was dropped')
    return dict(paramSetBytes=len(blob), paramWireBytes=PARAM_SAVE_BYTES, fieldOffsets=wire,
                expClamps={f'exp={exp} maxExp={limit}': value
                           for (exp, limit), value in clamps.items()},
                signedByteKey=keys[0], writeUnclampedExp=150, truncatedCountByte=many[1],
                nullSetBytes=len(serialize_param_set(None)))


def check_tables():
    materials = {int(row[2]) for row in table('Material').values()}
    assert {item_type - 8 for item_type in range(9, 14)} <= materials
    assert 0 in materials and 6 in materials
    items = list(table('Item').values())
    food = [row for row in items if int(row[3]) == 1]
    category_five_food = [row for row in food if int(row[2]) == 5]
    assert (len(food), len(category_five_food)) == (102, 102), (len(food), len(category_five_food))
    coin = sorted(int(row[3]) for row in items if int(row[3]) in (15, 16, 17))
    assert coin == [15, 16, 17] and all(int(row[2]) == 5 for row in items
                                        if int(row[3]) in (15, 16, 17))
    tickets = {int(row[3]) for row in items if int(row[2]) == 6}
    assert tickets == {18, 19, 20, 21, 22}, tickets
    dia = {int(row[3]) for row in items if int(row[2]) == 2}
    point = {int(row[3]) for row in items if int(row[2]) == 3}
    assert dia == {8} and point == {9, 10, 11, 12, 13, 14}, (dia, point)
    samples = {row_id: dict(id=int(row[0]), category=int(row[2]), type=int(row[3]))
               for row_id, row in ((0, table('Item')[0]), (3, table('Item')[3]),
                                   (71, table('Item')[71]), (100, table('Item')[100]))}
    assert not is_food(samples[0]) and not is_food(samples[3]), samples
    assert is_food(samples[71]) and is_food(samples[100]), samples
    assert not is_ticket(samples[71]) and not is_coin_item(samples[71])
    assert not is_coin_item(samples[0]) and not is_ticket(samples[0])
    coins = [(int(row[0]), row[1], int(row[2])) for row in items
             if int(row[3]) in (15, 16, 17)]
    assert all(is_coin_item(dict(type=row_type)) for row_type in (15, 16, 17))
    assert not is_coin_item(dict(type=14)) and not is_coin_item(dict(type=1))
    samples['coinItems'] = coins
    assert not requires_town_select(dict(flag=0)) and requires_town_select(dict(flag=512))
    assert not requires_town_select(dict(flag=1024))
    assert 'capacity' not in PORT_KEYS and len(PORT_KEYS) == 26
    assert set(PORT_KEYS) == set(Recorder(dict()).port), 'port key drift'
    return dict(materialTypes=sorted(materials), coinItemTypes=coin, ticketTypes=sorted(tickets),
                diaTypes=sorted(dia), pointTypes=sorted(point), foodRows=len(food),
                samples=samples)


def main():
    examples = [run_case(case) for case in CASES]
    ledger = check_ledger()
    param = check_param_save()
    tables = check_tables()
    stock = count_treasure_stock([dict(materialType=7, parameters={7: 3}),
                                  dict(materialType=7, parameters={}),
                                  dict(materialType=2, parameters={7: 9}),
                                  dict(materialType=7, parameters={7: -1})])
    assert stock == 2, stock
    receipt = package_receipt(710, True, [treasure_result(24, 100, 5)])
    assert receipt == dict(componentType=44, dataId=710, isOpenable=1,
                           resultQueue=[treasure_result(24, 100, 5)]), receipt
    assert package_receipt(710, False)['isOpenable'] == 0
    report = dict(schema='ka-b3-receipt-model-checks-1', receiptDispatchCases=len(examples),
        ledgerCases=len(ledger['entries']), paramSave=param, tables=tables,
        treasureStock=stock, examples=examples, ledger=ledger,
        findings=['The five decoded arms reproduce the recorded order and gate: category 2/3 coin '
                  'and stamina pay before the chest check with effect value2 = type-8, the material '
                  'fallback and the category 5 product branch need a live chest, and the category 5 '
                  'not-alive path switches on flag512 and townsCount>=2.',
                  'Category 4 pays AddItem(town null) then effect type 13 with value2 = the row id; '
                  'category 6 and the plain not-alive item path tail into AddItem(town null).',
                  'An unfound reward row is resolved and marked found before the DataType gate '
                  '(which sits after mark-found and before any arm); categories 0/1 and beyond 6 '
                  'grant nothing, and rows that are not ItemData stop at the cast.',
                  'A receipt is only `retained` when a confirmed modelled storage write '
                  '(AppData.AddItem/AddCoin/AddStamina) happened. A town-selection form, a ground '
                  'product/gatherable, a material/animation/effect/popup and the opaque IStock '
                  'handoff are dispatched or pending, never retained inventory; one native '
                  'dispatch consumes one queued result, so an entry with results left is `partial`.',
                  'No capacity clamp exists on this path: a row carrying maxTreasureCapacity 0 still '
                  'grants, and the port has no capacity input at all.',
                  'Warehouse treasure stock is the Param.Treasure sum over materialType 7 rows; the '
                  'param save round-trips as a 1-byte count, sbyte keys and ids, int16 levels and a '
                  '33-byte Parameter (1 byte + 4 int + 2 short + 3 int), so param 7 rides the entity '
                  'save stream. The count byte is read sign-extended, so a ParamSet with 128+ '
                  'entries reads back empty, and the exp clamp exists only on the read side.'],
        limits=['Portable model check against the recorded arm decode; no native execution here and '
                'no gameplay claim.',
                'Master-data rows, the two TreasureData tables, popups and every world/inventory '
                'effect are supplied by this check.',
                'The unnamed interface resolver target, the chest-open result derivation from '
                'TreasureData and every capacity reader stay outside this check.',
                'The StreamUtil Read/Write bodies and the receipt component were run natively in '
                'check_combat_composition.py: per-field widths, their order and the big-endian byte '
                'order of every multi-byte field are native-confirmed there, so this check no longer '
                'rests on an endianness assumption.'])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(dict(cases=len(examples), ledger=ledger['counts'], paramSave=param,
                          stock=stock, output=str(OUT))))


if __name__ == '__main__':
    main()
