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

from combat_collections import EntitySlotSet, ComponentSubset
from itertools import product
subset, filter_obj, klass, entry, collection = [0x10001000+i*0x1000 for i in range(5)]
added, removed = 0x10008000,0x10009000
w64(subset+0x28,filter_obj);w64(filter_obj,klass);w64(entry,0x30000100)
w64(subset+0x30,collection)
for delegate,address in [(added,0x30000200),(removed,0x30000300)]:
    w64(delegate+0x18,address);w64(delegate+0x40,777)
for flag in (0x316ab4b,0x316ab4c,0x316ab4d):uc.mem_write(flag,b'\x01')
for got in (0x2f5f8b0,0x2f5f8b8,0x2f5f8c0):w64(got,0x1000a000)
ranges=[(0x147f548,0x147f628),(0x147ef84,0x147f098),(0x147f09c,0x147f130)]

manager=0x1000b000
w64(manager+0x28,0x1000c000)
for flag in (0x316ab37,0x316ab38):uc.mem_write(flag,b"\x01")
for got in (0x2f5f888,0x2f5f890,0x2f5f898,0x2f5f8a0,0x2f5f8a8):w64(got,0x1000a000)
ranges += [(0x147ede4,0x147ef10),(0x147f134,0x147f260)]
enum_index=0
def external(machine,address,size,user):
    global enum_index
    if any(a<=address<b for a,b in ranges):return
    if address==0x1b42f78:result=0x1000d000
    elif address==0x211b28c:enum_index=0;result=0
    elif address==0x1bf2490:
        result=enum_index==0
        if result:w64(reg('X0')+0x10,subset)
        enum_index+=1
    elif address==0x1bf248c:result=0
    elif address==0x1332890:result=entry
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
for listeners in (False,True):
 for present in (False,True):
  for sequence in product((False,True),repeat=4):
   members=EntitySlotSet()
   if present:members.add(123)
   recorded=[]
   portable=ComponentSubset(required=(38,),excluded=(4,),
       on_added=(lambda u,t,c:recorded.append(('added',u['id'],t,c,portable.cache or 0))) if listeners else None,
       on_removed=(lambda u,t,c:recorded.append(('removed',u['id'],t,c,portable.cache or 0))) if listeners else None)
   if present:portable.members.add(123)
   unit={'id':123,'components':[None]*52};unit['components'][38]=1
   w64(subset+0x10,added if listeners else 0);w64(subset+0x18,removed if listeners else 0)
   for step,matches in enumerate(sequence):
    unit['components'][4]=None if matches else 1
    w64(subset+0x38,999);portable.cache=999;events=[];recorded.clear()
    run(0x147ede4 if step%2==0 else 0x147f134,x0=manager,x1=123,x2=4,x3=456)
    portable.update(unit,4,456)
    assert [e for e in events if e[0] in ('added','removed')]==recorded,(events,recorded)
    assert list(members)==list(portable.members)
    assert members.slots==portable.members.slots and members.free==portable.members.free
    assert struct.unpack('<Q',uc.mem_read(subset+0x38,8))[0]==(portable.cache or 0)
    checks+=1
result=dict(nativeSubsetReconciliationChecks=checks,scope='Original EntityManager component-added/removed loops and Subset callbacks versus portable component reconciliation',limitations=['Single supplied subset enumeration, supplied filter predicate, HashSet operations use separately native-checked slot model'])
(EVIDENCE/'subset-reconciliation-checks.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8');print(json.dumps(result))
