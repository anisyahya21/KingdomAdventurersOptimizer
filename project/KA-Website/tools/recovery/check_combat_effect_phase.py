"""Native combat effect updates and deferred parent-dependent cleanup."""
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

from combat_effects import update_effect_phase
from elftools.elf.elffile import ELFFile
from itertools import product
system,subset,native_set,slots,queue,items,entity,parent=[0x10004000+i*0x1000 for i in range(8)]
effect,position=entity+0x100,entity+0x200
w64(system+0x20,subset);w64(system+0x28,queue);w64(subset+0x30,native_set)
w64(native_set+0x18,slots);w32(native_set+0x24,1);w32(slots+0x18,1)
w32(slots+0x20,1);w64(slots+0x28,entity);w64(queue+0x10,items);w32(items+0x18,4)
uc.mem_write(0x316b0e6,b'\x01')
for got in (0x2f5faf8,0x2f5fae8,0x2f5d7d0,0x2f61278,0x2f5fae0,0x2f5fe30,0x2f5fe20,0x2f5fe18):w64(got,0x10001000)
w64(0x10001000,0x10002000)
uc.mem_map(0x771000,0x1000)
with (EVIDENCE.parent/'G2.1/39257e72291d/inputs/libil2cpp.so').open('rb') as f:
    elf=ELFFile(f);addr=0x771b83
    seg=next(s for s in elf.iter_segments() if s['p_type']=='PT_LOAD' and s['p_vaddr']<=addr<s['p_vaddr']+s['p_filesz'])
    f.seek(seg['p_offset']+addr-seg['p_vaddr']);uc.mem_write(addr,f.read(23))
events=[];list_index=0
def state():return r32(effect+0x20),struct.unpack('<f',uc.mem_read(position+0x14,4))[0]
def external(machine,address,size,user):
    global list_index
    if any(a<=address<b for a,b in ((0x155d678,0x155da24),(0x1bcf0a8,0x1bcf148),
            (0x155db94,0x155dc94),(0x155dde8,0x155de78),(0x155decc,0x155df20))):return
    value=0
    if address==0x1cd2110:uc.mem_write(reg('X8'),bytes(24));w64(reg('X8'),native_set)
    elif address==0x146d644:value=effect
    elif address==0x1471498:value=position
    elif address==0x1473e24:value=parent_state==2
    elif address in (0x1bcf0a4,0x1bcf584,0x2671a34):pass
    elif address==0x18c4a54:value=int(r32(queue+0x18)==0)
    elif address==0x1dec758:uc.mem_write(reg('X8'),bytes(24));list_index=0
    elif address==0x1bcf588:
        value=int(list_index<r32(queue+0x18))
        if value:w64(reg('X0')+0x10,entity);list_index+=1
    elif address==0x1473e30:events.append(('destroy',state()))
    else:raise AssertionError(hex(address))
    machine.reg_write(UC_ARM64_REG_X0,int(value));machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
checks=0
for typ,parent_state,depth,frame,duration in product((0,1,12,15,17,19),range(3),(False,True),(-2147483648,-1,0,29,2147483647),(0,1,30,-1)):
    events=[];w32(queue+0x18,0)
    w32(effect+0x10,typ);uc.mem_write(effect+0x1c,bytes([int(depth)]));w32(effect+0x20,frame);w32(effect+0x24,duration)
    w64(effect+0x28,parent if parent_state else 0);uc.mem_write(position+0x10,struct.pack('<3f',0.,19.25,0.))
    run(0x155d678,x0=system)
    model=dict(type=typ,parent=parent if parent_state else None,depth=depth,frame=frame,max_frame=duration)
    pos=[0.,19.25,0.];expected=[]
    update_effect_phase([entity],{entity:model},{entity:pos},lambda p:parent_state==2,lambda e:expected.append(('destroy',(model['frame'],pos[1]))))
    assert state()==(model['frame'],pos[1]),(typ,parent_state,depth,frame,duration,state(),model,pos)
    assert events==expected
    checks+=1
report=dict(nativeCombatEffectPhaseCases=checks,
    scope='Original EffectSystem.Update switch, HashSet iteration and six reachable effect handlers; parent cleanup after update',
    limitations=['Single supplied member; component access, pending-list iteration and destruction supplied; other effect types unsupported', 'Ordinary EffectSystem subset excludes Depth components; depth-flag cases here test the isolated handler, not ordinary membership'])
(EVIDENCE/'effect-phase-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');print(json.dumps(report))

