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
subset, filter_obj, klass, entry, collection = [0x10001000+i*0x1000 for i in range(5)]
added, removed = 0x10008000,0x10009000
w64(subset+0x28,filter_obj);w64(filter_obj,klass);w64(entry,0x30000100)
w64(subset+0x30,collection)
for delegate,address in [(added,0x30000200),(removed,0x30000300)]:
    w64(delegate+0x18,address);w64(delegate+0x40,777)
for flag in (0x316ab4b,0x316ab4c,0x316ab4d):uc.mem_write(flag,b'\x01')
for got in (0x2f5f8b0,0x2f5f8b8,0x2f5f8c0):w64(got,0x1000a000)
ranges=[(0x147f548,0x147f628),(0x147ef84,0x147f098),(0x147f09c,0x147f130)]

def external(machine,address,size,user):
    if any(a<=address<b for a,b in ranges):return
    if address==0x1332890:result=entry
    elif address==0x30000100:
        events.append(('filter',reg('X1')));result=matches
    elif address==0x1cd271c:
        result=members.add(reg('X1'));events.append(('add',reg('X1'),result))
    elif address==0x1cd1e68:
        result=members.remove(reg('X1'));events.append(('remove',reg('X1'),result))
    elif address in (0x30000200,0x30000300):
        events.append(('added' if address==0x30000200 else 'removed',reg('X1'),reg('W2'),reg('X3'),struct.unpack('<Q',uc.mem_read(subset+0x38,8))[0]))
        result=0
    else:raise AssertionError(hex(address))
    machine.reg_write(UC_ARM64_REG_X0,int(result));machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
checks=0
for op,rva in [('initial',0x147f548),('change',0x147ef84),('remove',0x147f09c)]:
 for matches in (False,True):
  for present in (False,True):
   for listeners in (False,True):
    members=EntitySlotSet()
    if present:members.add(123)
    w64(subset+0x10,added if listeners else 0);w64(subset+0x18,removed if listeners else 0)
    w64(subset+0x38,999);events=[]
    actual=run(rva,x0=subset,x1=123,x2=17,x3=456)
    if op=='remove':
        expected=[('remove',123,present)]
        if present and listeners:expected.append(('removed',123,17,456,999))
        assert list(members)==[]
        cache=0
    else:
        assert actual==matches
        expected=[('filter',123)]
        if matches:
            expected.append(('add',123,not present))
            if op=='change' and listeners:expected.append(('added',123,17,456,0))
        assert list(members)==([123] if present or matches else [])
        cache=0 if matches else 999
    assert events==expected,(op,matches,present,listeners,events,expected)
    assert struct.unpack('<Q',uc.mem_read(subset+0x38,8))[0]==cache
    checks+=1
report=dict(nativeSubsetCallbackChecks=checks,scope='Original two Subset.Add overloads and Remove; supplied filter, HashSet model and event recording callbacks',
    findings=['Matching component changes invoke added callback even for duplicate members',
              'Initial Add sends no added callback',
              'Removed callback runs before cache invalidation; added callback runs after cache invalidation'])
(EVIDENCE/'subset-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
print(json.dumps(report))
