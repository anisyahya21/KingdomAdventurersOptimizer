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

from combat_effects import update_hit_modifier
from itertools import product
from elftools.elf.elffile import ELFFile
uc.mem_write(0x316b391,b'\x01')
for i,got in enumerate((0x2f5faf8,0x2f5fae8,0x2f5d7d0,0x2f5fe20,0x2f5f980,0x2f5fae0,0x2f5fe30,0x2f5fe18)):
    ptr,cls,statics=0x10001000+i*8,0x10002000+i*0x100,0x10003000+i*0x100
    w64(got,ptr);w64(ptr,cls);w32(cls+0xe0,1);w64(cls+0xb8,statics)
    w64(statics,0x10009000);w64(0x10009098,0x1000a000)
uc.mem_map(0x772000,0x2000)
with (EVIDENCE.parent/'G2.1/39257e72291d/inputs/libil2cpp.so').open('rb') as f:
    elf=ELFFile(f)
    for addr,size in ((0x772133,12),(0x7536d8,4)):
        if addr==0x7536d8:uc.mem_map(0x753000,0x1000)
        seg=next(s for s in elf.iter_segments() if s['p_type']=='PT_LOAD' and s['p_vaddr']<=addr<s['p_vaddr']+s['p_filesz'])
        f.seek(seg['p_offset']+addr-seg['p_vaddr']);uc.mem_write(addr,f.read(size))
system,subset,entity,modifier,queue,items=[0x10004000+i*0x100 for i in range(6)]
w64(system+0x20,subset);w64(subset+0x30,0x10006000);w64(system+0x28,queue)
w64(queue+0x10,items);w32(items+0x18,10)

def external(machine,address,size,user):
    global enumerated,list_index
    if 0x15bc35c<=address<0x15bca78 or 0x16bbc28<=address<0x16bbc34:return
    result=0
    if address==0x1cd2110:enumerated=False;machine.mem_write(reg('X8'),bytes(24))
    elif address==0x1bcf0a8:
        result=int(not enumerated)
        if result:w64(reg('X0')+0x10,entity)
        enumerated=True
    elif address in (0x1bcf0a4,0x1bcf584,0x2671a34):pass
    elif address==0x1470924:result=modifier
    elif address==0x1dec758:list_index=0;machine.mem_write(reg('X8'),bytes(24))
    elif address==0x1bcf588:
        result=int(list_index<r32(queue+0x18));list_index+=1
        if result:w64(reg('X0')+0x10,entity)
    elif address==0x1473e30:events.append('destroy')
    elif address==0x1470b54:events.append('remove_modifier')
    else:raise AssertionError(hex(address))
    machine.reg_write(UC_ARM64_REG_X0,int(result));machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
checks=0
for frame,duration,loop,destroy in product((-2,-1,0,1,2,5,6,7),(0,1,6),(False,True),(False,True)):
    model=dict(type=4,frame=frame,duration=duration,loop=loop,destroy_on_finish=destroy,offset_x=0.)
    w32(modifier+0x10,4);w32(modifier+0x14,0);w32(modifier+0x2c,frame);w32(modifier+0x30,duration)
    uc.mem_write(modifier+0x38,bytes((destroy,loop)));uc.mem_write(0x1000a114,b'\x00')
    events=[];run(0x15bc35c,x0=system)
    complete=update_hit_modifier(model)
    expected_events=(['destroy'] if destroy else ['remove_modifier']) if complete else []
    assert events==expected_events,(frame,duration,loop,destroy,events,expected_events)
    assert r32(modifier+0x2c)==model['frame']
    actual=struct.unpack('<f',uc.mem_read(modifier+0x14,4))[0]
    assert actual==model['offset_x'],(frame,duration,loop,actual,model)
    assert bool(uc.mem_read(0x1000a114,1)[0])==(complete and not destroy)
    checks+=1
report=dict(nativeHitModifierLifecycleCases=checks,scope='Original ModifyAnimatinSystem.Update type4 and static-render dirty setter; supplied enumeration/getter/destruction/removal',
    findings=['Normal duration6 frame0 modifier completes on its seventh update','Completion may destroy an entity when the stored flag is true; normal hit-created modifiers set it false'],
    limitations=['Other modifier types and entity-destruction propagation not executed'])
(EVIDENCE/'hit-modifier-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');print(json.dumps(report))
