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

from combat_collections import EntitySlotDictionary
import random
obj,buckets,entries,method,klass,context,comparer,interface,hash_entry,eq_entry,enum=[0x10001000+i*0x2000 for i in range(11)]
w64(method+0x20,klass);w64(klass+0xc0,context);w64(context+8,interface)
uc.mem_write(interface+0x135,b'\x01');w64(comparer,klass)
w64(hash_entry,0x30000100);w64(eq_entry,0x30000200)
ranges=[(0x1b2f4f8,0x1b2f930),(0x1b2f954,0x1b2f95c),(0x1b2fe84,0x1b30180),
        (0x1beec40,0x1beecec),(0x1b2ec28,0x1b2ec90)]

def external(machine,address,size,user):
    if any(a<=address<b for a,b in ranges):return
    if address==0x1332890:result=hash_entry if reg('W2')==1 else eq_entry
    elif address==0x30000100:result=reg('X1')%7 if collisions else reg('X1')
    elif address==0x30000200:result=int(reg('X1')==reg('X2'))
    elif address==0x2671a34:
        stride=24 if reg('X0')==entries else 4
        machine.mem_write(reg('X0')+0x20+reg('W1')*stride,bytes(reg('W2')*stride));result=0
    else:raise AssertionError(hex(address))
    machine.reg_write(UC_ARM64_REG_X0,int(result));machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
checks=0
for collisions in (False,True):
    uc.mem_write(obj,bytes(0x100));uc.mem_write(buckets,bytes(0x1000));uc.mem_write(entries,bytes(0x1000))
    w64(obj+0x10,buckets);w64(obj+0x18,entries);w64(obj+0x30,comparer)
    w32(obj+0x24,-1);w32(buckets+0x18,128);w32(entries+0x18,128)
    model=EntitySlotDictionary();rng=random.Random(410)
    operations=[('insert',1),('insert',2),('remove',1),('remove',2),('insert',3),('insert',4)]
    operations += [(rng.choice(['insert','insert','overwrite','remove','clear']),rng.randrange(1,80)) for _ in range(2000)]
    for op,key in operations:
        if op in ('insert','overwrite'):
            actual=run(0x1b2f4f8,x0=obj,x1=key,x2=key+1000,x3=int(op=='overwrite'),x4=method)
            expected=model.insert(key,key+1000,op=='overwrite')
        elif op=='remove':
            actual=run(0x1b2fe84,x0=obj,x1=key,x2=method);expected=model.remove(key)
        else:
            run(0x1b2ec28,x0=obj);model.clear();actual=expected=0
        assert actual==expected,(op,key,actual,expected)
        assert r32(obj+0x20)==len(model.slots)
        assert r32(obj+0x24)==(model.free[-1] if model.free else -1)
        assert r32(obj+0x28)==len(model.free)
        assert r32(obj+0x2c)==model.version
        w64(enum,obj);w32(enum+8,0);w32(enum+0xc,model.version)
        order=[]
        while run(0x1beec40,x0=enum):order.append(struct.unpack('<Q',uc.mem_read(enum+0x10,8))[0])
        assert order==list(model.values()),(op,key,order,list(model.values()))
        checks+=1
report=dict(nativeDictionaryOperationAndOrderChecks=checks,
            scope='Original TryInsert/Remove/Clear/ValueEnumerator.MoveNext; preallocated128; custom comparer and Array.Clear stubbed',
            limitations=['No resize or native default Int64 comparer execution; duplicate throw behavior not exercised'])
(EVIDENCE/'dictionary-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');print(json.dumps(report))
