"""Native lifetime decrement pass and deferred ordered destruction."""
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

from combat_lifecycle import update_garbage
from combat_collections import EntitySlotSet
system,subset,native_set,slots,queue,items=[0x10004000+i*0x1000 for i in range(6)]
w64(system+0x20,subset);w64(subset+0x30,native_set);w64(native_set+0x18,slots)
w32(slots+0x18,4)
w64(queue+0x10,items);w32(items+0x18,8)
uc.mem_write(0x316b22d,b'\x01')
for got in (0x2f5d228,0x2f5d230,0x2f5faf8,0x2f5fae8,0x2f5d7d0,0x2f5fe30,0x2f5fe20,0x2f5fe18,0x2f5fae0):w64(got,0x10001000)
w64(0x10001000,0x10002000)
entities=[0x10010000+i*0x1000 for i in range(4)]
events=[];list_index=0
def external(machine,address,size,user):
    global list_index
    if 0x158eb54<=address<0x158edd4 or 0x1bcf0a8<=address<0x1bcf148:return
    value=0
    if address==0x12d23b8:value=queue
    elif address==0x1deb4a0:w32(queue+0x18,0)
    elif address==0x1cd2110:
        uc.mem_write(reg('X8'),bytes(24));w64(reg('X8'),native_set)
    elif address==0x146e624:value=reg('X0')
    elif address in (0x1bcf0a4,0x1bcf584,0x2671a34):pass
    elif address==0x1dec758:uc.mem_write(reg('X8'),bytes(24));list_index=0
    elif address==0x1bcf588:
        value=int(list_index<r32(queue+0x18))
        if value:
            w64(reg('X0')+0x10,struct.unpack('<Q',uc.mem_read(items+0x20+list_index*8,8))[0]);list_index+=1
    elif address==0x1473e30:events.append((reg('X0'),tuple(r32(e+0x10) for e in entities)))
    else:raise AssertionError(hex(address))
    machine.reg_write(UC_ARM64_REG_X0,value);machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
checks=0
for reuse in (False,True):
    for mask in range(16):
        for boundary in (False,True):
            members=EntitySlotSet()
            for entity in entities:members.add(entity)
            if reuse:
                members.remove(entities[0]);members.remove(entities[2]);members.add(entities[0]);members.add(entities[2])
            initial={e:((-2147483648 if i==0 else 0) if boundary else 1) if mask&(1<<i) else 3 for i,e in enumerate(entities)}
            for e,value in initial.items():w32(e+0x10,value)
            w32(native_set+0x24,4);w32(native_set+0x38,0)
            for i,e in enumerate(members.slots):w32(slots+0x20+i*16,1);w64(slots+0x28+i*16,e)
            events=[];run(0x158eb54,x0=system)
            model=dict(initial);expected=[]
            update_garbage(members,model,lambda e:expected.append((e,tuple(model[x] for x in entities))))
            assert events==expected,(reuse,mask,boundary,events,expected)
            assert tuple(r32(e+0x10) for e in entities)==tuple(model.values())
            checks+=1
report=dict(nativeGarbagePhaseCases=checks,
    scope='Original GarbageSystem.Update and HashSet enumeration; all lifetimes decremented before ordered destruction, including int32 wrap',
    limitations=['Component access, temporary-list allocation/enumeration and destruction callbacks supplied; Entity.Destroy checked separately'])
(EVIDENCE/'garbage-phase-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');print(json.dumps(report))

