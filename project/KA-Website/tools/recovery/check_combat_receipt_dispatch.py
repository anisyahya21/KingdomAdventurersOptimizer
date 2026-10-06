"""B4 receipt-dispatch composition: the original `AISystem.TreasureBoxResult` per category.

Runs `AISystem.TreasureBoxResult` `0x14b8e4c` out of the frozen `libil2cpp.so` (fb834373...30208)
under Unicorn together with every slice set the B3 decode produced for it — the dispatch head, the
five category arms and their fallback blocks, the gate getters, and the whole `data.Data.GetData`
DataType table — and stubs every call that leaves them. The ordered boundary trace is then compared
against the offline model's `combat_receipt.dispatch_result` with `combat_event_compare`, so a wrong
arm, a missing gate, a skipped mark-found or a swapped argument surfaces as the first divergence
rather than as a matching total.

Two things are *input* to this fixture and appear in the report as such: the two jump tables, which
are copied byte-for-byte out of the frozen binary (`0x771a3a` selects the category arm, `0x7721b2`
selects the DataType arm), and every stub, which supplies memory and handles only — never order.
`check_combat_receipt.py` already checks the same arm table statically against the decode; this
fixture runs it.
"""
import hashlib
import json
import struct

import unicorn.arm64_const as arm64_const
from elftools.elf.elffile import ELFFile

from check_combat_receipt import POSITION, Recorder
from combat_event_compare import describe, first_divergence, state_divergences
from combat_initial_state import EVIDENCE
from combat_native_composition import Composition, RECEIPT_SLICES, RETURN, manifest_key
from combat_receipt import (CATEGORY_DIA, CATEGORY_GLOBAL_ITEM, CATEGORY_ITEM, CATEGORY_TICKET,
                            ITEM_DATA_TYPE, TYPE_DIA, TYPE_FOOD, TYPE_POINT_STAMINA,
                            dispatch_result, treasure_result)
from combat_run_manifest import canonical_hash
from slice_b3_treasure_receipt import BINARY, va_to_off

OUT = EVIDENCE / 'receipt-dispatch-composition-checks.json'
DISPATCH_SLICES = RECEIPT_SLICES / 'dispatch/slices.json'
ARM_SLICES = RECEIPT_SLICES / 'box-result-arms/slices.json'
GATE_SLICES = RECEIPT_SLICES / 'dispatch-gates/slices.json'
GETDATA_SLICES = RECEIPT_SLICES / 'getdata-arms/slices.json'
B3_SLICES = RECEIPT_SLICES / 'b3-receipt-slices.json'

ENTRY = 0x14b8e4c                   # AISystem.TreasureBoxResult(Queue<TreasureResult>, Entity, bool)
CATEGORY_TABLE = 0x771a3a           # u16 word offsets, base 0x14b912c, indexed by category - 2
CATEGORY_TABLE_WORDS = 5
DATA_TYPE_TABLE = 0x7721b2          # u16 word offsets, base 0x1629090, indexed by DataType 0..0x31
DATA_TYPE_TABLE_WORDS = 0x32
# Class-initialization fast-path bytes of the slices that carry one, so no run reaches an
# initialization helper: TreasureBoxResult 0x316ac02, Data.GetData 0x316b6aa, TreasureComponent
# .get_data 0x316ad70.
INIT_FLAGS = (0x316ac02, 0x316b6aa, 0x316ad70)
# Data.GetData returns `((Data)statics[0]).<per-DataType field>`; the field offsets below come from
# the arm bodies inside this same manifest (`getdata-arms/*.asm`), not from the decode.
DATA_INSTANCE_FIELD = {0x0: 0x120, 0x2: 0x190, ITEM_DATA_TYPE: 0x58, 0x24: 0x150}
# GOT variable slots this composition reads; each one holds the pointer the original expects.
GOT_DATA_KLASS = 0x2f5b890          # data.Data
GOT_ISTOCK_KLASS = 0x2f5d210        # data.IStock, the cast target of the accepted DataType group
GOT_ITEMDATA_KLASS = 0x2f5d1f8      # data.ItemData, the manual hierarchy cast
GOT_BASESYSTEM_KLASS = 0x2f5f980    # ecs.BaseSystem: statics[0] apdat_, statics[2] formMan_
GOT_POPUP_KLASS = 0x2f60928         # statics[1]/statics[2] are the two popup dictionaries
GOT_HEADER_KLASS = 0x2f60908        # game.popupBar.Popup.Header
GOT_DISPLAY_KLASS = 0x2f60938       # AISystem.<>c__DisplayClass269_0
GOT_ARRAY_KLASS = 0x2f5ce90         # element klass handed to the three-slot popup array
GOT_SECOND_KLASS = 0x2f5d4c0        # the class whose interface carries the row's display name
GOT_TOWN_KLASS = 0x2f5d4c8          # the class whose interface carries the selected town
GOT_FORM_KLASS = 0x2f5bab8          # FormManager
GOT_FUNC_KLASS = 0x2f5fc18          # Func<MaterialData, bool>
GOT_LT_KLASS = 0x2f5b8a0            # the class the popup text block initializes before the lookups
# The two `BaseSystem.LT` tables the popup text is built from (DataType 11 selects the second).
GOT_LT_TABLES = (0x2f60940, 0x2f60948)
GOT_BOX_KLASS = 0x2f5b780            # the boxed-int class the popup array's third slot is boxed into
GOT_MATERIAL_METHOD = 0x2f5fbb8     # the FirstOrDefault method handle (never dereferenced)
GOT_DEQUEUE_METHOD = 0x2f60920      # Queue<T>.Dequeue method handle (never dereferenced)
ZERO_SLOTS = (GOT_MATERIAL_METHOD, GOT_DEQUEUE_METHOD, 0x2f60910, 0x2f60918, 0x2f5b0b8,
              0x2f60608, 0x2f5d628, 0x2f60930)
# `ItemData.get_IsCoinItem` `0x162a9cc` compares the row's type against the cctor-built static
# `COIN_ITEM_TYPES` int[] through `Enumerable.Any<int>`; that enumerator plumbing is outside this
# fixture, so the gate is a declared stub that re-encodes the decode's own rule - the same three
# coin types `check_combat_receipt.py` asserts.
IS_COIN_ITEM = 0x162a9cc
COIN_ITEM_TYPES = (15, 16, 17)

# --- boundaries the dispatch leaves, named from HANDOFF-b3-treasure-receipt.md section 6/9 and the
# --- companion packet; each is a declared stub, never code that ran.
DEQUEUE = 0x1f9ffa4                 # Queue<TreasureData.TreasureResult>.Dequeue
ISINST = 0x12d22a8                  # il2cpp cast helper
CLASS_INIT = 0x12d21a0              # il2cpp class initialization
RUNTIME_INIT = 0x12d22a4            # il2cpp runtime class initialization
ARRAY_ALLOC = 0x12d2214             # il2cpp array allocation
OBJECT_NEW = 0x12d23b8              # il2cpp object allocation
BOX_INT = 0x12d22ac                 # il2cpp boxing
THROW_NULL = 0x12d23c8
THROW_BOUNDS = 0x12d23d0
THROW_CAST = 0x12d2764
THROW_INVALID_CAST = 0x12d23ec
THROW_ARGUMENT = 0x12d2294
RESOLVER = 0x1332890                # the unnamed interface resolver (w2 = 2 stock, 0 row name)
POPUP_TEXT_LOOKUP = 0x18c0c88       # IDictionaryExtension.GetValueOrDefault<int, string>
EFFECT_KIND_LOOKUP = 0x18c0c38      # IDictionaryExtension.GetValueOrDefault<int, int>
LT_LOOKUP = 0x22be668               # the BaseSystem.LT table lookup
LT_FORMAT = 0x14c0804               # BaseSystem.LT text build
HEADER_CTOR = 0x169d9d8             # Popup.Header(resId: 27, sebId: 2, imgId: <treasure image>)
ADD_POPUP = 0x15949c0               # AddPopup(world, text, header)
GET_TREASURE = 0x147319c            # Entity.get_treasure
GET_POSITION = 0x1471498            # Entity.get_position
TREASURE_DATA = 0x16268c8           # data.Data.get_treasureData()
ADD_ITEM = 0x1673b20                # AppData.AddItem(town, itemData, num)
ADD_COIN = 0x1673854                # AppData.AddCoin(num, logType)
ADD_STAMINA = 0x16743fc             # AppData.AddStamina(num)
CREATE_MATERIAL = 0x1478cc4         # World.CreateMaterial(pos, materialId, num)
CREATE_PRODUCT = 0x147941c          # World.CreateProduct(pos, dataType, dataId, num)
ADD_GATHERABLE = 0x146e90c          # Entity.AddGatherable(product, lifeFrame)
ADD_MODIFY_ANIMATION = 0x14709bc    # AddModifyAnimation(maxFrame, alpha)
CREATE_EFFECT = 0x1478ea0           # World.CreateEffect(pos, type, value1, value2, maxFrame, ...)
CREATE_TOWN_SELECT = 0x1765b30      # FormManager.CreateItemAddToTownSelect(itemData, num)
PUSH_FORM = 0x237f9f0               # FormManager.AddForm(form)
TOWNS_COUNT = 0x15e3018             # the selected-town holder's get_townsCount
SELECTED_TOWN = 0x191de04           # the selected-town holder (+0x20 is the town entity)
MATERIAL_DATA = 0x1626e40           # data.Data.get_materialData()
FUNC_CTOR = 0x1cb5fc8               # Func<MaterialData, bool> construction
FIRST_OR_DEFAULT = 0x18947c8        # Enumerable.FirstOrDefault
COMPONENT_DATA_ID = 0x10            # TreasureComponent.dataId, read by get_data `0x14d2608`
TREASURE_IMAGE_FIELD = 0x2c         # the TreasureData field the Popup.Header image comes from
# The DisplayClass ctor slice `0x14d8a5c` is a two-instruction thunk (`mov x1, xzr; b 0x2691aa0`)
# and 0x2691aa0 is the shared `ret` it jumps to; that tail is a declared stub.
DISPLAY_CTOR_TAIL = 0x2691aa0

# The boundary set this composition claims equivalence over, in the model's own port vocabulary.
CHANNEL = ('mark_found', 'stock_add', 'add_item', 'add_coin', 'add_stamina', 'create_material',
           'create_product', 'add_gatherable', 'add_modify_animation', 'create_effect',
           'create_item_add_to_town_select', 'push_form', 'add_popup')
# How each model port call's positional log becomes named event fields: the same names the native
# stubs report, so the two channels are compared field by field rather than by arity.
MODEL_ARGS = {
    'mark_found': ('dataId',), 'stock_add': ('dataId', 'num'), 'add_item': ('town', 'dataId', 'num'),
    'add_coin': ('num', 'logType'), 'add_stamina': ('num',),
    'create_material': ('materialId', 'position', 'num'),
    'create_product': ('dataType', 'dataId', 'position', 'num'),
    'add_gatherable': ('product', 'lifeFrame'), 'add_modify_animation': ('maxFrame', 'alpha'),
    'create_effect': ('type', 'position', 'value1', 'value2', 'maxFrame', 'scale'),
    'add_popup': ('text', 'rowName', 'num', 'image'),
    'create_item_add_to_town_select': ('dataId', 'num'), 'push_form': ('form',),
}
INFRA = ('class_init', 'runtime_init', 'isinst', 'array_alloc', 'object_new', 'box_int',
         'throw_null', 'throw_bounds', 'throw_cast', 'throw_invalid_cast', 'throw_argument',
         'interface_resolver', 'popup_text_lookup', 'effect_kind_lookup', 'lt_lookup', 'lt_format',
         'header_ctor', 'get_treasure', 'get_position', 'treasure_data', 'material_data',
         'func_ctor', 'first_or_default', 'towns_count', 'selected_town', 'stock_add_method',
         'row_name_method')

# The two jump tables are read out of the frozen binary once: this fixture must not carry its own
# copy of "category 4 goes to 0x14b9538", which is part of what is being checked.
with BINARY.open('rb') as _handle:
    _elf = ELFFile(_handle)
    TABLE_OFFSETS = {rva: va_to_off(_elf, rva) for rva in (CATEGORY_TABLE, DATA_TYPE_TABLE)}


def f32(machine, index):
    """Read one float argument: the arms pass positions in v0/v1/v2."""
    raw = machine.uc.reg_read(getattr(arm64_const, f'UC_ARM64_REG_D{index}')).to_bytes(8, 'little')
    return struct.unpack('<f', raw[:4])[0]


def seed(machine, rva, words):
    """Copy a frozen jump table in: its entries are evidence, not fixture constants.

    Both tables sit below the harness's code region, so their page is mapped on first use.
    """
    page = rva & ~0xfff
    pages = getattr(machine, 'table_pages', None)
    if pages is None:
        pages = machine.table_pages = set()
    if page not in pages:
        machine.uc.mem_map(page, 0x1000)
        pages.add(page)
    offset = TABLE_OFFSETS[rva]
    machine.write(rva, machine.binary[offset:offset + 2 * words])


def halt(machine):
    """A declared throw helper: the arm raises instead of returning, so the run stops there."""
    machine.set_reg('lr', RETURN)


class Fixture:
    """One dispatch case: the synthetic object graph, the stub table and the ordered channel.

    `machine` lets a caller stage this dispatch on an existing composition - the B3 warehouse
    fixture passes the staged-run machine so the queue consumed here is the object
    `Entity.AddTreasure` filled during the award stage, not a look-alike. With no machine the
    fixture builds its own five-manifest machine exactly as before, so this check is unchanged.
    """

    def __init__(self, case, machine=None):
        self.case = case
        machine = machine or Composition(
            [DISPATCH_SLICES, ARM_SLICES, GATE_SLICES, GETDATA_SLICES, B3_SLICES],
            name='treasure-box-result-dispatch')
        self.machine = machine
        self.channel = []
        self.handles = {}
        self.popup = {}
        self.image = None
        self.boxed = None
        self.termination = 'return'
        self.pending = [self.result(spec) for spec in case['queue']]
        for address in INIT_FLAGS:
            machine.w8(address, 1)
        seed(machine, CATEGORY_TABLE, CATEGORY_TABLE_WORDS)
        seed(machine, DATA_TYPE_TABLE, DATA_TYPE_TABLE_WORDS)
        self.build()
        self.stubs()

    # --- the object graph the dispatch walks -------------------------------------------------
    def result(self, spec):
        """One queued `TreasureResult`: Info @+0x10 {DataType @+0x10, DataId @+0x14}, Num @+0x18."""
        machine = self.machine
        if spec is None:
            return 0
        element = machine.alloc(0x30)
        if spec['info']:
            info = machine.alloc(0x20)
            machine.w32(info + 0x10, spec['dataType'])
            machine.w32(info + 0x14, spec['dataId'])
            machine.w64(element + 0x10, info)
        machine.w32(element + 0x18, spec['num'])
        return element

    def klass_with_hierarchy(self, depth=1):
        """An Il2CppClass whose hierarchy entry is itself, as the cast at 0x14b90e0 reads it."""
        machine = self.machine
        klass = machine.klass({})
        hierarchy = machine.alloc(16)
        machine.w64(hierarchy, klass)
        machine.w8(klass + 0x130, depth)
        machine.w64(klass + 0xc8, hierarchy)
        return klass

    def row(self):
        """The reward row: BaseData state_ @0x10, id_ @0x18, flag_ @0x1c, ItemData +0x28/+0x2c."""
        machine, case = self.machine, self.case
        if case.get('unresolved'):
            return 0
        spec = case.get('row', {})
        row = machine.object_of(self.item_klass if case.get('itemdata', True) else self.other_klass,
                                0x40)
        machine.w32(row + 0x10, spec.get('state', 1))
        machine.w32(row + 0x18, spec.get('id', 100))
        machine.w32(row + 0x1c, spec.get('flag', 0))
        machine.w32(row + 0x28, spec.get('category', CATEGORY_TICKET))
        machine.w32(row + 0x2c, spec.get('type', 18))
        return row

    def build(self):
        """Every pointer the dispatch dereferences, carrying the case's own values."""
        machine, case = self.machine, self.case
        self.item_klass = self.klass_with_hierarchy()
        self.other_klass = self.klass_with_hierarchy()      # a class outside the ItemData hierarchy
        self.world = machine.object_of(machine.klass({}), 0x40)
        self.actor = machine.object_of(machine.klass({}), 0x40)
        machine.w64(self.actor + 0x10, self.world)          # AISystem.world_
        self.chest = machine.object_of(machine.klass({}), 0x40)
        machine.w8(self.chest + 0x38, 0 if case.get('alive', True) else 2)   # Entity.IsAlive
        # One position object serves every `Entity.get_position` call of the case.
        self.position_object = machine.alloc(0x20)
        for index, value in enumerate(POSITION):
            machine.write(self.position_object + 0x10 + 4 * index, struct.pack('<f', value))
        # The chest's TreasureComponent: the frozen `get_data` `0x14d2608` reads dataId @+0x10 and
        # then indexes the frozen `data.Data.get_treasureData()` table, so the popup image is a
        # native read of this object rather than a value the model hands over.
        self.treasure = machine.alloc(0x40)
        machine.w32(self.treasure + TREASURE_IMAGE_FIELD, case.get('image', 7))
        self.treasure_array = machine.array(1, stride=8)
        machine.w64(self.treasure_array + 0x20, self.treasure)
        self.component = machine.object_of(machine.klass({}), 0x40)
        machine.w32(self.component + COMPONENT_DATA_ID, 0)
        # data.Data: statics[0] is the Data instance whose per-DataType field holds the row array.
        spec = case['queue'][0] if case['queue'] else None
        data_type = (spec or {}).get('dataType', ITEM_DATA_TYPE)
        field = DATA_INSTANCE_FIELD.get(data_type, 0x120)
        self.data = machine.object_of(machine.klass({}), max(field, 0x120) + 0x10)
        rows = machine.array(max(case.get('rowCount', 1), 1), stride=8)
        machine.w64(rows + 0x20, self.row())                # BaseData[] indexed by DataId
        machine.w64(self.data + field, rows)
        statics = machine.alloc(0x40)
        machine.w64(statics, self.data)
        data_klass = machine.klass({})
        machine.w64(data_klass + 0xb8, statics)
        machine.got(GOT_DATA_KLASS, data_klass)
        self.istock_klass = machine.klass({})
        machine.got(GOT_ISTOCK_KLASS, self.istock_klass)
        machine.got(GOT_ITEMDATA_KLASS, self.item_klass)
        # BaseSystem statics: [0] apdat_, [2] formMan_.
        self.apdat = machine.object_of(machine.klass({}), 0x40)
        self.form_man = machine.object_of(machine.klass({}), 0x40)
        base_statics = machine.alloc(0x40)
        machine.w64(base_statics, self.apdat)
        machine.w64(base_statics + 0x10, self.form_man)
        base_klass = machine.klass({})
        machine.w64(base_klass + 0xb8, base_statics)
        machine.got(GOT_BASESYSTEM_KLASS, base_klass)
        # The two popup dictionaries hang off another class's statics.
        popup_statics = machine.alloc(0x40)
        machine.w64(popup_statics + 8, machine.object_of(machine.klass({}), 0x40))
        machine.w64(popup_statics + 0x10, machine.object_of(machine.klass({}), 0x40))
        popup_klass = machine.klass({})
        machine.w64(popup_klass + 0xb8, popup_statics)
        machine.got(GOT_POPUP_KLASS, popup_klass)
        machine.got(GOT_HEADER_KLASS, machine.klass({}))
        machine.got(GOT_DISPLAY_KLASS, machine.klass({}))
        machine.got(GOT_ARRAY_KLASS, machine.klass({0x40: machine.klass({})}))
        machine.got(GOT_SECOND_KLASS, machine.klass({}))
        machine.got(GOT_TOWN_KLASS, machine.klass({}))
        machine.got(GOT_FORM_KLASS, machine.klass({}))
        machine.got(GOT_FUNC_KLASS, machine.klass({}))
        machine.got(GOT_LT_KLASS, machine.klass({}))
        machine.got(GOT_BOX_KLASS, machine.klass({}))
        for slot in GOT_LT_TABLES:
            machine.got(slot, machine.object_of(machine.klass({}), 0x40))
        for slot in ZERO_SLOTS:
            machine.got(slot, 0)
        # The queue object itself: `_size` @+0x20, consumed by the declared Dequeue stub.
        self.queue = machine.alloc(0x40)
        machine.w32(self.queue + 0x20, 0 if case.get('nullSize') else len(self.pending))

    # --- the declared boundaries -------------------------------------------------------------
    def reward(self, name, **fields):
        """Record one reward call in the ordered channel, under the model's own field names."""
        event = dict(kind='call', name=name, **fields)
        self.channel.append(event)
        self.machine.note(**fields)
        return event

    def handle(self, pointer):
        """The fixture's name for one of its own handles (product, form, town, text, row name)."""
        return self.handles.get(pointer) if pointer else None

    def position(self):
        return [f32(self.machine, index) for index in range(3)]

    def thrower(self, name):
        def handler(machine, *args):
            self.termination = name
            halt(machine)
        return handler

    def class_init(self, machine, klass):
        return klass

    def isinst(self, machine, object_, klass):
        """The IStock cast is the one gate this fixture controls; the others cast its own objects."""
        if object_ == 0:
            return 0
        if klass == self.istock_klass and not self.case.get('istock', True):
            return 0
        return object_

    def array_alloc(self, machine, klass, count):
        array = machine.array(count & 0xffffffff, stride=8, klass=klass)
        self.popup_array = array
        return array

    def object_new(self, machine, klass):
        return machine.object_of(klass, 0x60)

    def box_int(self, machine, klass, source):
        self.boxed = machine.r32(source)
        return machine.object_of(klass, 0x40)

    def resolver(self, machine, object_, klass, selector):
        """`0x1332890`: w2 = 2 asks for the IStock method, w2 = 0 for the row's display name."""
        return self.stock_info if selector == 2 else self.name_info

    def stock_add(self, machine, object_, num):
        self.reward('stock_add', dataId=machine.r32(object_ + 0x18), num=num & 0xffffffff)

    def row_name(self, machine, object_):
        return self.name_handle

    def is_coin_item(self, machine, row):
        """A declared gate: the row's type against the decode's three coin types."""
        return 1 if machine.r32(row + 0x2c) in COIN_ITEM_TYPES else 0

    def mark_found(self, machine, row, flag):
        self.marked = True
        self.reward('mark_found', dataId=machine.r32(row + 0x18))

    def popup_text(self, machine, dictionary, data_type):
        self.popup['dataType'] = data_type & 0xffffffff
        return self.text

    def effect_kind(self, machine, dictionary, data_type):
        self.popup['effectDataType'] = data_type & 0xffffffff
        return self.case.get('effectKind', -1)

    def header_ctor(self, machine, header, res_id, seb_id, image):
        self.image = image & 0xffffffff
        self.popup.update(resId=res_id & 0xffffffff, sebId=seb_id & 0xffffffff, image=self.image)
        return header

    def add_popup(self, machine, world, text, header):
        array = self.popup_array
        self.reward('add_popup', text=self.handle(machine.r64(array + 0x20)),
                    rowName=self.handle(machine.r64(array + 0x28)), num=self.boxed,
                    image=self.image)

    def dequeue(self, machine, queue):
        """`Queue<T>.Dequeue` `0x1f9ffa4`: pop the fixture's FIFO and shrink the live queue object."""
        machine.w32(queue + 0x20, max(machine.r32(queue + 0x20) - 1, 0))
        return self.pending.pop(0) if self.pending else 0

    def add_item(self, machine, appdata, town, row, num):
        self.reward('add_item', town=self.handle(town), dataId=machine.r32(row + 0x18),
                    num=num & 0xffffffff)

    def add_coin(self, machine, appdata, num, log_type):
        self.reward('add_coin', num=num & 0xffffffff, logType=log_type & 0xffffffff)

    def add_stamina(self, machine, appdata, num):
        self.reward('add_stamina', num=num & 0xffffffff)

    def create_material(self, machine, world, material_id, num):
        self.reward('create_material', materialId=material_id & 0xffffffff,
                    position=self.position(), num=num & 0xffffffff)

    def create_product(self, machine, world, data_type, data_id, num):
        self.reward('create_product', dataType=data_type & 0xffffffff,
                    dataId=data_id & 0xffffffff, position=self.position(), num=num & 0xffffffff)
        return self.product

    def add_gatherable(self, machine, product, life_frame):
        self.reward('add_gatherable', product=self.handle(product),
                    lifeFrame=life_frame & 0xffffffff)
        return self.gatherable

    def add_modify_animation(self, machine, unused, max_frame):
        """`AddModifyAnimation(...)` `0x14709bc`: maxFrame in w4, alpha in the first stack slot."""
        alpha = machine.r32(machine.reg('sp'))
        self.reward('add_modify_animation', maxFrame=max_frame & 0xffffffff,
                    alpha=alpha & 0xffffffff)

    def create_effect(self, machine, world, effect_type, value1, max_frame):
        stack = machine.reg('sp')
        self.reward('create_effect', type=effect_type & 0xffffffff, position=self.position(),
                    value1=value1 & 0xffffffff, value2=machine.r32(stack + 0x20),
                    maxFrame=max_frame & 0xffffffff, scale=machine.r32(stack + 0x28))

    def create_town_select(self, machine, row, num):
        self.reward('create_item_add_to_town_select', dataId=machine.r32(row + 0x18),
                    num=num & 0xffffffff)
        return self.form

    def push_form(self, machine, form_man, form):
        self.reward('push_form', form=self.handle(form))

    def towns_count(self, machine, holder):
        return self.case.get('towns', 2)

    def selected_town(self, machine, world, klass):
        return self.holder

    def first_or_default(self, machine, source, func, method):
        return self.material

    def get_treasure(self, machine, chest):
        return self.component

    def get_position(self, machine, entity):
        return self.position_object

    def get_treasure_data(self, machine, unused):
        return self.treasure_array

    def get_material_data(self, machine, unused):
        return self.material_array

    # --- the declared stub table ---------------------------------------------------------------
    def stubs(self):
        """Declare every boundary the dispatch leaves; each stub supplies memory or a handle."""
        machine, case = self.machine, self.case
        self.marked = False
        self.popup_array = 0
        self.material = 0
        self.product = machine.alloc(0x20)
        self.handles[self.product] = 'product'
        self.gatherable = machine.alloc(0x20)
        self.form = machine.alloc(0x20)
        self.handles[self.form] = 'select-form'
        self.town = machine.alloc(0x20)
        self.handles[self.town] = 'town-entity'
        self.text = 0
        if case.get('popupText'):
            self.text = machine.alloc(0x20)
            machine.w32(self.text + 0x10, 1)                # String.length, read by IsNullOrEmpty
            self.handles[self.text] = case['popupText']
        self.name_handle = machine.alloc(0x20)
        self.handles[self.name_handle] = 'row'
        self.holder = machine.object_of(machine.klass({}), 0x40)
        machine.w64(self.holder + 0x20, self.town)          # the selected town entity
        self.material_array = machine.array(1, stride=8)
        if case.get('material'):
            self.material = machine.alloc(0x40)
            machine.w32(self.material + 0x18, case['material']['id'])   # BaseData.id_ @0x18
        machine.w64(self.material_array + 0x20, self.material)
        # The unnamed resolver `0x1332890` hands back a method handle pair; both are stubs.
        self.stock_method = machine.new_stub('stock_add_method', self.stock_add,
                                             capture=('x0', 'w1'))
        self.name_method = machine.new_stub('row_name_method', self.row_name, capture=('x0',))
        self.stock_info = machine.alloc(16)
        machine.w64(self.stock_info, self.stock_method)
        self.name_info = machine.alloc(16)
        machine.w64(self.name_info, self.name_method)
        # The mark-found vtable slot of the row's class: `ldp x9, x2, [klass, #0x1b8]`.
        machine.w64(self.item_klass + 0x1b8,
                    machine.new_stub('mark_found', self.mark_found, capture=('x0', 'w1')))
        machine.stub(CLASS_INIT, 'class_init', self.class_init, capture=('x0',))
        machine.stub(DISPLAY_CTOR_TAIL, 'display_ctor_tail', lambda h, *a: None)
        machine.stub(RUNTIME_INIT, 'runtime_init', self.class_init, capture=('x0',))
        machine.stub(ISINST, 'isinst', self.isinst, capture=('x0', 'x1'))
        machine.stub(ARRAY_ALLOC, 'array_alloc', self.array_alloc, capture=('x0', 'w1'))
        machine.stub(OBJECT_NEW, 'object_new', self.object_new, capture=('x0',))
        machine.stub(BOX_INT, 'box_int', self.box_int, capture=('x0', 'x1'))
        for address, name in ((THROW_NULL, 'throw-null'), (THROW_BOUNDS, 'throw-bounds'),
                              (THROW_CAST, 'throw-cast'),
                              (THROW_INVALID_CAST, 'throw-invalid-cast'),
                              (THROW_ARGUMENT, 'throw-argument')):
            machine.stub(address, name.replace('-', '_'), self.thrower(name), capture=('x0',))
        machine.stub(RESOLVER, 'interface_resolver', self.resolver, capture=('x0', 'x1', 'w2'))
        machine.stub(POPUP_TEXT_LOOKUP, 'popup_text_lookup', self.popup_text, capture=('x0', 'w1'))
        machine.stub(EFFECT_KIND_LOOKUP, 'effect_kind_lookup', self.effect_kind,
                     capture=('x0', 'w1'))
        machine.stub(LT_LOOKUP, 'lt_lookup', lambda h, *a: self.text, capture=('x0',))
        machine.stub(LT_FORMAT, 'lt_format', lambda h, *a: self.text, capture=('x0', 'x1'))
        machine.stub(HEADER_CTOR, 'header_ctor', self.header_ctor, capture=('x0', 'w1', 'w2', 'w3'))
        machine.stub(ADD_POPUP, 'add_popup', self.add_popup, capture=('x0', 'x1', 'x2'))
        machine.stub(GET_TREASURE, 'get_treasure', self.get_treasure, capture=('x0',))
        machine.stub(GET_POSITION, 'get_position', self.get_position, capture=('x0',))
        machine.stub(TREASURE_DATA, 'treasure_data', self.get_treasure_data, capture=('x0',))
        machine.stub(MATERIAL_DATA, 'material_data', self.get_material_data, capture=('x0',))
        machine.stub(FUNC_CTOR, 'func_ctor',
                     lambda h, *a: machine.object_of(self.item_klass, 0x40),
                     capture=('x0', 'x1', 'x2'))
        machine.stub(FIRST_OR_DEFAULT, 'first_or_default', self.first_or_default,
                     capture=('x0', 'x1', 'x2'))
        machine.stub(IS_COIN_ITEM, 'is_coin_item', self.is_coin_item, capture=('x0',))
        machine.stub(TOWNS_COUNT, 'towns_count', self.towns_count, capture=('x0',))
        machine.stub(SELECTED_TOWN, 'selected_town', self.selected_town, capture=('x0', 'x1'))
        machine.stub(DEQUEUE, 'queue_dequeue', self.dequeue, capture=('x0',))
        machine.stub(ADD_ITEM, 'add_item', self.add_item, capture=('x0', 'x1', 'x2', 'w3'))
        machine.stub(ADD_COIN, 'add_coin', self.add_coin, capture=('x0', 'w1', 'w2'))
        machine.stub(ADD_STAMINA, 'add_stamina', self.add_stamina, capture=('x0', 'w1'))
        machine.stub(CREATE_MATERIAL, 'create_material', self.create_material,
                     capture=('x0', 'w1', 'w2'))
        machine.stub(CREATE_PRODUCT, 'create_product', self.create_product,
                     capture=('x0', 'w1', 'w2', 'w3'))
        machine.stub(ADD_GATHERABLE, 'add_gatherable', self.add_gatherable, capture=('x0', 'w2'))
        machine.stub(ADD_MODIFY_ANIMATION, 'add_modify_animation', self.add_modify_animation,
                     capture=('x0', 'w4'))
        machine.stub(CREATE_EFFECT, 'create_effect', self.create_effect,
                     capture=('x0', 'w1', 'w2', 'w7'))
        machine.stub(CREATE_TOWN_SELECT, 'create_item_add_to_town_select',
                     self.create_town_select, capture=('x0', 'w1'))
        machine.stub(PUSH_FORM, 'push_form', self.push_form, capture=('x0', 'x1'))

    # --- one run ------------------------------------------------------------------------------
    def run(self):
        """The original dispatch with this case's queue, chest and `addsPopup` argument."""
        case = self.case
        self.machine.run(ENTRY, {'x0': self.actor, 'x1': 0 if case.get('nullQueue') else self.queue,
                                 'x2': self.chest, 'w3': 1 if case.get('addsPopup') else 0})
        return self

    def observed(self):
        """What the run left behind: the ordered channel, the terminator and the popup state."""
        return dict(
            nativeChannel=self.channel,
            nativeReturned=self.termination == 'return',
            termination=self.termination,
            markedFound=self.marked,
            queueSizeAfter=self.machine.r32(self.queue + 0x20),
            popupHeader=self.popup or None,
            otherBoundaries=[event['name'] for event in self.machine.trace
                             if event['kind'] == 'call' and event['name'] not in CHANNEL])


# --- the declared expectations ----------------------------------------------------------------
# Every case writes out what the decode says the arm does, independently of both the run and the
# model: the ordered reward channel, the model's classification and how the original arm ends.
def queued(data_type, data_id, num, info=True):
    """One queued `TreasureResult`; `info=False` leaves its Info pointer null."""
    return dict(dataType=data_type, dataId=data_id, num=num, info=info)


def declared(name, channel, state, arm, termination='return', **fields):
    return dict(name=name, expects=dict(channel=channel, state=state, arm=arm,
                                        termination=termination), **fields)


ITEM = ITEM_DATA_TYPE
CASES = [
    declared('cat6-ticket', ['add_item'], 'granted', 'cat6-ticket',
             queue=[queued(ITEM, 0, 5)], row=dict(category=CATEGORY_TICKET, type=18)),
    declared('cat4-global-item-alive', ['add_item', 'create_effect'], 'granted',
             'cat4-global-item', queue=[queued(ITEM, 0, 5)],
             row=dict(category=CATEGORY_GLOBAL_ITEM, type=0)),
    declared('cat4-global-item-dead', ['add_item'], 'granted', 'cat4-global-item', alive=False,
             queue=[queued(ITEM, 0, 5)], row=dict(category=CATEGORY_GLOBAL_ITEM, type=0)),
    declared('cat2-coin-alive', ['add_coin', 'create_effect'], 'granted', 'cat2-3-dia-point',
             queue=[queued(ITEM, 0, 5)], row=dict(category=CATEGORY_DIA, type=TYPE_DIA)),
    declared('cat3-stamina-alive', ['add_stamina', 'create_effect'], 'granted', 'cat2-3-dia-point',
             queue=[queued(ITEM, 0, 5)], row=dict(category=3, type=TYPE_POINT_STAMINA)),
    declared('cat2-other-material-alive', ['create_material', 'add_modify_animation'], 'granted',
             'cat2-3-other-material', queue=[queued(ITEM, 0, 5)],
             row=dict(category=CATEGORY_DIA, type=9), material=dict(id=101, type=1)),
    declared('cat2-other-material-dead', [], 'suppressed', 'cat2-3-other-material', alive=False,
             queue=[queued(ITEM, 0, 5)], row=dict(category=CATEGORY_DIA, type=9),
             material=dict(id=101, type=1)),
    declared('cat2-other-material-missing', [], 'suppressed', 'cat2-3-other-material',
             termination='throw-null', queue=[queued(ITEM, 0, 5)],
             row=dict(category=CATEGORY_DIA, type=9)),
    declared('cat5-coin-alive', ['create_product', 'add_gatherable', 'add_modify_animation'],
             'granted', 'cat5-item-coin', queue=[queued(ITEM, 0, 5)],
             row=dict(category=CATEGORY_ITEM, type=15)),
    declared('cat5-food-alive', ['create_product', 'add_gatherable', 'add_modify_animation'],
             'granted', 'cat5-item-food', queue=[queued(ITEM, 0, 5)],
             row=dict(category=CATEGORY_ITEM, type=TYPE_FOOD)),
    declared('cat5-alive-other', [], 'suppressed', 'cat5-item-alive-other',
             queue=[queued(ITEM, 0, 5)], row=dict(category=CATEGORY_ITEM, type=99)),
    declared('cat5-not-alive-plain', ['add_item'], 'granted', 'cat5-item-not-alive-plain-add',
             alive=False, queue=[queued(ITEM, 0, 5)], row=dict(category=CATEGORY_ITEM, type=15)),
    declared('cat5-not-alive-town-select', ['create_item_add_to_town_select', 'push_form'],
             'granted', 'cat5-item-town-select', alive=False, towns=2,
             queue=[queued(ITEM, 0, 5)], row=dict(category=CATEGORY_ITEM, type=16, flag=512)),
    declared('cat5-not-alive-selected-town', ['add_item'], 'granted', 'cat5-item-selected-town',
             alive=False, towns=1, queue=[queued(ITEM, 0, 5)],
             row=dict(category=CATEGORY_ITEM, type=17, flag=512)),
    declared('cat0-bonus', [], 'suppressed', 'no-grant',
             queue=[queued(ITEM, 0, 5)], row=dict(category=0, type=2)),
    declared('cat1-billing', [], 'suppressed', 'no-grant',
             queue=[queued(ITEM, 0, 5)], row=dict(category=1, type=3)),
    declared('cat7-outside-table', [], 'suppressed', 'no-grant',
             queue=[queued(ITEM, 0, 5)], row=dict(category=7, type=19)),
    declared('ticket-unfound-marked', ['mark_found', 'add_item'], 'granted', 'cat6-ticket',
             queue=[queued(ITEM, 0, 5)], row=dict(category=CATEGORY_TICKET, type=18, state=0)),
    declared('empty-result-queue', [], 'empty', None, queue=[]),
    declared('null-queue', [], 'empty', None, termination='throw-null', queue=[],
             nullQueue=True),
    declared('null-result-element', [], 'null-result', None, termination='throw-null',
             queue=[None]),
    declared('null-info-pointer', [], 'unresolved-data', None, termination='throw-null',
             queue=[queued(ITEM, 0, 5, info=False)]),
    declared('data-id-out-of-range', [], 'data-id-out-of-range', None, termination='throw-bounds',
             queue=[queued(ITEM, -1, 5)], row=dict(category=CATEGORY_TICKET, type=18)),
    declared('data-id-beyond-table', [], 'unresolved-data', None, termination='throw-bounds',
             queue=[queued(ITEM, 3, 5)], row=dict(category=CATEGORY_TICKET, type=18)),
    declared('unresolved-data', [], 'unresolved-data', None, termination='throw-null',
             unresolved=True, queue=[queued(ITEM, 0, 5)]),
    declared('row-not-itemdata', [], 'row-not-itemdata', None, termination='throw-cast',
             itemdata=False, queue=[queued(ITEM, 0, 5)],
             row=dict(category=CATEGORY_TICKET, type=18)),
    declared('data-type-above-itemdata', [], 'unsupported-data-type', None,
             queue=[queued(0x24, 0, 5)], row=dict(category=CATEGORY_TICKET, type=18)),
    # The >0x23 gate sits *after* the row is resolved and marked found, so a DataType 0x24 row
    # whose state is 0 still records mark_found natively before the plain epilogue rejects it.
    declared('data-type-above-itemdata-unfound', ['mark_found'], 'unsupported-data-type', None,
             queue=[queued(0x24, 0, 5)], row=dict(category=CATEGORY_TICKET, type=18, state=0)),
    declared('data-type-outside-group', [], 'unsupported-data-type', None,
             queue=[queued(2, 0, 5)], row=dict(category=CATEGORY_TICKET, type=18)),
    declared('istock-stock-and-effect', ['stock_add', 'create_effect'], 'granted',
             'istock-data-type', queue=[queued(0, 0, 5)], effectKind=3,
             row=dict(category=CATEGORY_TICKET, type=18)),
    declared('istock-popup-and-effect', ['stock_add', 'add_popup'], 'granted', 'istock-data-type',
             queue=[queued(0, 0, 5)], addsPopup=True, popupText='popup-0',
             row=dict(category=CATEGORY_TICKET, type=18)),
    declared('istock-dead-keeps-stock', ['stock_add'], 'granted', 'istock-data-type', alive=False,
             queue=[queued(0, 0, 5)], row=dict(category=CATEGORY_TICKET, type=18)),
    declared('istock-cast-fails-dead', [], 'suppressed', 'istock-data-type', alive=False,
             istock=False, queue=[queued(0, 0, 5)], row=dict(category=CATEGORY_TICKET, type=18)),
    declared('istock-cast-fails-popup', ['add_popup'], 'granted', 'istock-data-type',
             istock=False, queue=[queued(0, 0, 5)], addsPopup=True, popupText='popup-0',
             row=dict(category=CATEGORY_TICKET, type=18)),
    declared('istock-no-grant-path', [], 'suppressed', 'istock-data-type', istock=False,
             queue=[queued(0, 0, 5)], row=dict(category=CATEGORY_TICKET, type=18)),
    declared('two-elements-one-dispatch', ['add_item'], 'granted', 'cat6-ticket',
             queueSize=1, queue=[queued(ITEM, 0, 5), queued(ITEM, 0, 7)],
             row=dict(category=CATEGORY_TICKET, type=18)),
]


# --- the model side ---------------------------------------------------------------------------
def model_rows(case):
    """The master-data row the model resolves, in the shape `check_combat_receipt`'s port uses."""
    spec, queue = case.get('row'), case['queue']
    if case.get('unresolved') or not spec or not queue or queue[0] is None:
        return {}
    return {queue[0]['dataType']: [dict(id=spec.get('id', 100), category=spec.get('category'),
                                        type=spec.get('type'), state=spec.get('state', 1),
                                        flag=spec.get('flag', 0))]}


def model_channel(recorder):
    """The model's port log, projected onto the same named fields the native stubs report."""
    rows = []
    for entry in recorder.calls:
        name = entry['call']
        if name in CHANNEL:
            rows.append(dict(kind='call', name=name, **dict(zip(MODEL_ARGS[name], entry['args']))))
    return rows


def model_case(case):
    """One model dispatch through the B3 recorder port, on the same queue, chest and gates."""
    queue = case['queue']
    data_type = queue[0]['dataType'] if queue and queue[0] is not None else None
    recorder = Recorder(model_rows(case),
                        materials=(case['material'],) if case.get('material') else (),
                        alive=case.get('alive', True),
                        towns=case.get('towns', 2), is_item=case.get('itemdata', True),
                        is_istock=case.get('istock', True),
                        effect_kinds={data_type: case['effectKind']} if case.get('effectKind')
                        else None,
                        popup_names={data_type: case['popupText']} if case.get('popupText')
                        else None)
    results = [None if spec is None else treasure_result(spec['dataType'], spec['dataId'],
                                                         spec['num']) for spec in queue]
    record = dispatch_result(recorder.port, results, chest='chest',
                             adds_popup=case.get('addsPopup', False))
    return record, recorder


def popup_state(native, record):
    """The header the original built, key by key against the popup record the model kept."""
    header, popup = native['popupHeader'] or {}, record['popup'] or {}
    return {key: dict(native=header.get(key), model=popup.get(key))
            for key in ('resId', 'sebId', 'image') if key in popup or key in header}


def run_case(case):
    """Run the original dispatch and the model on one case, then compare them as data."""
    fixture = Fixture(case).run()
    native = fixture.observed()
    record, recorder = model_case(case)
    model, expects = model_channel(recorder), case['expects']
    native_names = [event['name'] for event in native['nativeChannel']]
    model_names = [event['name'] for event in model]
    assert native_names == expects['channel'], (case['name'], native_names, expects['channel'])
    assert model_names == expects['channel'], (case['name'], model_names)
    assert native['termination'] == expects['termination'], (case['name'], native['termination'])
    assert record['state'] == expects['state'], (case['name'], record['state'], expects['state'])
    assert record['arm'] == expects['arm'], (case['name'], record['arm'], expects['arm'])
    if 'queueSize' in expects:
        assert native['queueSizeAfter'] == expects['queueSize'], (case['name'],
                                                                  native['queueSizeAfter'])
    divergence = first_divergence(native['nativeChannel'], model)
    assert divergence is None, describe(divergence, case['name'])
    state = state_divergences(dict(markedFound=native['markedFound']),
                              dict(markedFound=record['markedFound']))
    assert state == [], (case['name'], state)
    popup = popup_state(native, record)
    for key, values in popup.items():
        assert values['native'] == values['model'], (case['name'], key, values)
    return dict(name=case['name'], declared=expects, nativeChannel=native['nativeChannel'],
                modelChannel=model, channelDivergence=divergence,
                termination=native['termination'], nativeReturned=native['nativeReturned'],
                modelState=record['state'], modelArm=record['arm'],
                markedFound=native['markedFound'], queueSizeAfter=native['queueSizeAfter'],
                popupState=popup, otherBoundaries=sorted(set(native['otherBoundaries']))), fixture


def manifest_records():
    """The five manifests this composition loads, with their own digests and slice counts.

    `Composition.slice_manifest` now keys loaded manifests by evidence-relative path
    (`combat_native_composition.manifest_key`), so all five survive even though four are named
    `slices.json`. This list is a convenience cross-check with the same SHA-256 and slice counts,
    keyed the same way; `main` asserts it agrees with `sliceManifest.manifests` entry for entry.
    """
    rows = []
    for path in (DISPATCH_SLICES, ARM_SLICES, GATE_SLICES, GETDATA_SLICES, B3_SLICES):
        record = json.loads(path.read_text(encoding='utf-8'))
        rows.append(dict(key=manifest_key(path), file=path.parent.name, manifest=path.name,
                         sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                         slices=len(record['slices'])))
    return rows


def jump_table(machine, rva, words, base):
    """The seeded table, decoded: the entries the run actually dispatched on."""
    offset = TABLE_OFFSETS[rva]
    raw = machine.binary[offset:offset + 2 * words]
    rows = []
    for index in range(words):
        word = int.from_bytes(raw[2 * index:2 * index + 2], 'little')
        rows.append(dict(index=index, word=hex(word), target=hex(base + word * 4)))
    return rows


def main():
    pairs = [run_case(case) for case in CASES]
    results = [result for result, _ in pairs]
    machine = pairs[-1][1].machine
    thrown = {row['name']: row['termination'] for row in results if not row['nativeReturned']}
    # Every loaded manifest is preserved under its own evidence-relative key: no `slices.json`
    # collides, and the convenience list agrees with the harness record entry for entry.
    manifest_list = manifest_records()
    loaded = machine.slice_manifest()['manifests']
    assert len(loaded) == len(manifest_list) == 5, (loaded, manifest_list)
    for record in manifest_list:
        assert record['key'] in loaded, record['key']
        assert loaded[record['key']]['sha256'] == record['sha256'], record['key']
        assert loaded[record['key']]['slices'] == record['slices'], record['key']
    assert len({record['key'] for record in manifest_list}) == 5, manifest_list
    report = dict(
        schema='ka-b4-receipt-dispatch-composition-1',
        fixtures=dict(cases=len(results), dispatchCalls=len(results),
                      returnArms=len(results) - len(thrown), throwArms=len(thrown)),
        sliceManifest=machine.slice_manifest(),
        manifests=manifest_list,
        stubs=[dict(address=hex(address), name=entry[0])
               for address, entry in sorted(machine.stubs.items())],
        jumpTables=dict(
            category=jump_table(machine, CATEGORY_TABLE, CATEGORY_TABLE_WORDS, 0x14b912c),
            dataType=jump_table(machine, DATA_TYPE_TABLE, DATA_TYPE_TABLE_WORDS, 0x1629090)),
        throwArms=thrown,
        cases=results,
        findings=[
            'The category switch is a table and the run dispatched out of it: selector = '
            'ItemData.category - 2, categories 2/3 share 0x14b912c, 4 goes to 0x14b9538, 5 to '
            '0x14b9604 and 6 to 0x14b9948, while categories 0, 1 and >6 reach the plain epilogue '
            'with no grant. `jumpTables` below is decoded from the frozen binary; the fixture '
            'never wrote an entry.',
            'For every case the original arm\'s ordered reward calls match the model\'s port log '
            'field by field with no first divergence, including the values: AddCoin(num, logType '
            '4), AddStamina(num), the shared effect tail (type 8 on the coin/stamina arm, 13 on '
            'category 4, value2 = type - 8 or the row id, maxFrame 40, scale 100, y + 20), the '
            'material branch (material id read from the MaterialData the fixture supplied, num, '
            'raw position) and the product branch (dataType 24, the queued DataId, raw position, '
            'lifeFrame 12000, maxFrame 20, alpha 255).',
            'One Dequeue per call: the two-element case dispatches the head result and leaves one '
            'element in the live queue object, which is the "one dispatch per call" claim the '
            'model makes.',
            'An unfound reward row is marked found through the class vtable slot before the ' +
            'DataType > 0x23 (35) gate and before any arm runs, in that ' +
            'order. The `data-type-above-itemdata-unfound` case executes the original GetData arm ' +
            'for DataType 0x24 (inside the 0..0x31 jump table) with row state 0 and records ' +
            'mark_found natively before the plain epilogue rejects the type - the ordering is ' +
            'executed, not stubbed into the expectation.',
            '`addsPopup` gates exactly the popup block: with it false the IStock arm still makes '
            'the stock call and the effect and never builds a header; with it true the header is '
            'built (resId 27, sebId 2, imgId read through the frozen TreasureComponent.get_data) '
            'and AddPopup receives the text, the row name, the num and that header.',
            'The conditions the model reports as states are IL2CPP throws in the original: a null '
            'queue, a null TreasureResult, a null Info pointer, a null row, a null FirstOrDefault '
            'result, a DataId outside the table and a failed ItemData cast each end in a throw '
            'helper (see `throwArms`). No reward call runs on either side, so the outcomes agree, '
            'but the form differs: the model returns a state where the original raises.',
            'A positive DataId beyond the table is the one case that differs in kind: the '
            'original raises a bounds exception while the model reports unresolved data from its '
            'resolve port, so the port has to keep the bounds rule.',
            'The material row search itself (Enumerable.FirstOrDefault over the Func lambda) is '
            'stub-supplied; the material id, the position and the num that CreateMaterial '
            'receives are read out of the original code path.'],
        limits=[
            'The harness now keys loaded manifests by evidence-relative path, so all five are'
            ' preserved even though four share the basename `slices.json`; `manifests` above is a'
            ' same-keyed cross-check that `main` asserts against `sliceManifest.manifests`.',
            'The two jump tables and the Data singleton\'s per-DataType field offsets are read '
            'from the frozen binary; the fixture supplies the row array, the material array, the '
            'treasure table, the queue and every handle, and lists its whole stub table and the '
            'extra boundaries each run touched (`otherBoundaries`).',
            'Stubs supply memory and handles only, never order; the unnamed interface resolver '
            '0x1332890 and the interface method handles it returns are stubs, so the interface '
            'walk itself is not claimed.',
            'No world simulation, no inventory storage, no capacity rule (RankData.'
            'maxTreasureCapacity is unread on this path), no RNG, no rendering and no audio; '
            'apdat_, formMan_ and the world are opaque handles.',
            'One dispatch call per run: the caller\'s loop over the queue, the queue\'s own '
            'persistence and the chest-open result derivation stay outside this fixture.',
            'No runtime trace of the game: this is the frozen binary under Unicorn, so it '
            'supports the dispatch composition only, not an end-to-end encounter.'])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(dict(cases=len(results), slices=report['sliceManifest']['slices'],
                          stubs=len(report['stubs']), throwArms=thrown, output=str(OUT),
                          digest=canonical_hash(report))))


if __name__ == '__main__':
    main()










