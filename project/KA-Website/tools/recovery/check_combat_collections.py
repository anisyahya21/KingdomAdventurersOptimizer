"""Native command timing and interruption primitives, with explicit callee stubs."""
import json
import struct
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE
from unicorn.arm64_const import *
from combat_initial_state import EVIDENCE, i32

binary = (EVIDENCE.parent / 'G2.1/39257e72291d/inputs/libil2cpp.so').read_bytes()
audit = json.loads((EVIDENCE / 'audit.json').read_text())
uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
for base, size in [(0x1000000, 0x2300000), (0x10000000, 0x100000),
                   (0x20000000, 0x10000), (0x30000000, 0x1000)]: uc.mem_map(base, size)
for entry in audit['methods']:
    start, end, offset = [int(entry[k], 16) for k in ['rva', 'end', 'offset']]
    uc.mem_write(start, binary[offset:offset+end-start])

def w32(a, n): uc.mem_write(a, struct.pack('<I', n & 0xffffffff))
def w64(a, n): uc.mem_write(a, struct.pack('<Q', n))
def r32(a): return struct.unpack('<i', uc.mem_read(a, 4))[0]
def reg(n): return uc.reg_read(globals()['UC_ARM64_REG_' + n])
def run(start, **regs):
    uc.reg_write(UC_ARM64_REG_SP, 0x20008000)
    uc.reg_write(UC_ARM64_REG_LR, 0x30000000)
    for name, value in regs.items():
        uc.reg_write(globals()['UC_ARM64_REG_' + name.upper()], value & 0xffffffffffffffff)
    uc.emu_start(start, 0x30000000, count=10000)
    assert reg('PC') == 0x30000000, hex(reg('PC'))
    return reg('W0')

from combat_collections import EntitySlotSet
import random

obj, buckets, slots = 0x10001000, 0x10002000, 0x10003000
method, klass, context = 0x10005000, 0x10006000, 0x10007000
comparer, interface, entry, enum = 0x10008000, 0x10009000, 0x1000a000, 0x1000b000
w64(method+0x20, klass); w64(klass+0xc0, context)
w64(context+0x20, interface); uc.mem_write(interface+0x135, b'\x01')
w64(comparer, klass); w64(entry, 0x30000100)
capacity=128
ranges=[(0x1cd31a8,0x1cd3428),(0x1cd1e68,0x1cd20bc),
        (0x1bcf0a8,0x1bcf148),(0x1cd1c3c,0x1cd1c98), (0x1cd3094,0x1cd31a0)]
hash_mode=0
allocation_cursor=0x10020000
slot_class, bucket_class=0x1000c000,0x1000d000
w64(context+0x118,slot_class); uc.mem_write(slot_class+0x135,b'\x01')
w64(0x2f5b218,0x1000e000); w64(0x1000e000,bucket_class)
uc.mem_write(0x316cb12,b'\x01')

def external(machine, address, size, user):
    global allocation_cursor
    if any(a<=address<b for a,b in ranges): return
    if address==0x1cd3540: result=reg('X1')%7 if hash_mode else reg('X1')
    elif address==0x1332890: result=entry
    elif address==0x30000100: result=int(reg('X1')==reg('X2'))
    elif address==0x12d2214:
        result=allocation_cursor; allocation_cursor+=0x4000
        stride=16 if reg('X0')==slot_class else 4
        machine.mem_write(result,bytes(0x20+reg('W1')*stride)); w32(result+0x18,reg('W1'))
    elif address==0x2671ce8:
        machine.mem_write(reg('X2')+0x20+reg('W3')*16,bytes(machine.mem_read(reg('X0')+0x20+reg('W1')*16,reg('W4')*16)))
        result=0
    elif address==0x2671a34:
        stride=16 if reg('X0')==slots else 4
        machine.mem_write(reg('X0')+0x20+reg('W1')*stride, bytes(reg('W2')*stride))
        result=0
    else: raise AssertionError(hex(address))
    machine.reg_write(UC_ARM64_REG_X0,result)
    machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)

checks=0
for hash_mode in (0,1):
    uc.mem_write(obj,bytes(0x100)); uc.mem_write(buckets,bytes(0x1000)); uc.mem_write(slots,bytes(0x1000))
    w64(obj+0x10,buckets); w64(obj+0x18,slots); w32(obj+0x28,-1); w64(obj+0x30,comparer)
    w32(buckets+0x18,capacity); w32(slots+0x18,capacity)
    model=EntitySlotSet()
    rng=random.Random(7301)
    operations=[('add',1),('add',2),('add',3),('remove',1),('add',4),('remove',2),('remove',4),('add',5)]
    operations += [(rng.choice(['add','add','remove','remove','clear']),rng.randrange(1,70)) for _ in range(2000)]
    for op,value in operations:
        actual=run({'add':0x1cd31a8,'remove':0x1cd1e68,'clear':0x1cd1c3c}[op],x0=obj,x1=value,x2=method)
        expected=getattr(model,op)(*(() if op=='clear' else (value,)))
        if op!='clear': assert actual==expected,(op,value,actual,expected)
        assert r32(obj+0x20)==len(model.indices)
        assert r32(obj+0x24)==len(model.slots)
        assert r32(obj+0x28)==(model.free[-1] if model.free else -1)
        assert r32(obj+0x38)==model.version
        w64(enum,obj); w32(enum+8,0); w32(enum+0xc,model.version)
        order=[]
        while run(0x1bcf0a8,x0=enum):
            order.append(struct.unpack('<Q',uc.mem_read(enum+0x10,8))[0])
        assert order==list(model),(op,value,order,list(model))
        checks+=1
# Growth is called only when lastIndex == capacity and freeList == -1.
# SetCapacity rebuilds chains without checking negative hashes; do not call it
# on arbitrary hole-bearing states as though it were a public resize API.
growth_checks=0
for hash_mode in (0,1):
    uc.mem_write(obj,bytes(0x100)); uc.mem_write(buckets,bytes(0x1000)); uc.mem_write(slots,bytes(0x1000))
    w64(obj+0x10,buckets); w64(obj+0x18,slots); w32(obj+0x28,-1); w64(obj+0x30,comparer)
    w32(buckets+0x18,capacity); w32(slots+0x18,capacity)
    model=EntitySlotSet()
    for value in range(1,129):
        assert run(0x1cd31a8,x0=obj,x1=value,x2=method)==model.add(value)
    # Remove and refill slots before growing, to retain a non-chronological order.
    for value in (2,7,4):
        assert run(0x1cd1e68,x0=obj,x1=value,x2=method)==model.remove(value)
    for value in (201,202,203):
        assert run(0x1cd31a8,x0=obj,x1=value,x2=method)==model.add(value)
    run(0x1cd3094,x0=obj,x1=257,x2=method)
    for value in range(300,350):
        assert run(0x1cd31a8,x0=obj,x1=value,x2=method)==model.add(value)
    w64(enum,obj); w32(enum+8,0); w32(enum+0xc,model.version)
    order=[]
    while run(0x1bcf0a8,x0=enum): order.append(struct.unpack('<Q',uc.mem_read(enum+0x10,8))[0])
    assert order==list(model)
    growth_checks+=1
report=dict(nativeOperationsAndEnumerationChecks=checks, nativeCapacityRebuilds=growth_checks,
            scope='Original Add/Remove/Clear/MoveNext; preallocated capacity128; hash/equality and Array.Clear stubbed; identity keys; collision and distinct hash modes',
            limitations=['Allocator/Array.Copy/Array.Clear/hash/equality stubbed; growth-size selection and exception construction not exercised'])
(EVIDENCE/'collection-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
print(json.dumps(report))
