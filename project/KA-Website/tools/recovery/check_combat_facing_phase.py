"""Native projectile creation and flight, with explicit component/event stubs."""
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

from combat_spatial import update_facing
from combat_entities import CombatEntities,image_component
from itertools import product
from elftools.elf.elffile import ELFFile
from math import atan2,fmod
for flag in (0x316b46a,):uc.mem_write(flag,b'\x01')
for got in (0x2f5f410,0x2f5fae0,0x2f5fae8,0x2f5faf0,0x2f5faf8,0x2f5b0f0,0x2f623c8):w64(got,0x10001000)
w64(0x10001000,0x10002000);w32(0x100020e0,1)
uc.mem_map(0x753000,0x1000)
with (EVIDENCE.parent/'G2.1/39257e72291d/inputs/libil2cpp.so').open('rb') as f:
 elf=ELFFile(f);seg=next(s for s in elf.iter_segments() if s['p_type']=='PT_LOAD' and s['p_vaddr']<=0x753000<s['p_vaddr']+s['p_filesz']);f.seek(seg['p_offset']+0x753000-seg['p_vaddr']);uc.mem_write(0x753000,f.read(0x1000))
system,subset,native_set,native_slots,entity,speed_obj,direction,seb,image,human,render=[0x10004000+i*0x1000 for i in range(11)]
w64(system+0x10,0x10003000);w64(system+0x20,subset);w64(subset+0x30,native_set)
w64(native_set+0x18,native_slots);w32(native_set+0x24,1);w32(native_slots+0x18,1);w32(native_slots+0x20,1);w64(native_slots+0x28,entity)
for off,value in ((0x28,101),(0x30,102),(0x38,201),(0x40,202)):w64(human+off,value)
events=[]
def read_double(n):return struct.unpack('<d',struct.pack('<Q',reg('D'+str(n))))[0]
def external(machine,address,size,user):
 if 0x15dc510<=address<0x15dc9e0 or 0x1bcf0a8<=address<0x1bcf148:return
 result=0
 if address==0x1cd2110:uc.mem_write(reg('X8'),bytes(24));w64(reg('X8'),native_set)
 elif address==0x1bcf0a4:pass
 elif address==0x191de04:result=render
 elif address==0x1472858:result=speed_obj
 elif address==0x146d3f4:result=direction
 elif address==0x14721c8:result=seb
 elif address==0x146ee7c:result=is_human
 elif address==0x146edf4:result=human
 elif address==0x146f0bc:result=image
 elif address==0x148cca4:events.append(('air',));result=air
 elif address in (0x2ded6b0,0x2ded6c0):
  args=(read_double(0),read_double(1));events.append(('atan2' if address==0x2ded6b0 else 'fmod',args))
  value=(atan2 if address==0x2ded6b0 else fmod)(*args)
  machine.reg_write(UC_ARM64_REG_D0,struct.unpack('<Q',struct.pack('<d',value))[0])
 else:raise AssertionError(hex(address))
 machine.reg_write(UC_ARM64_REG_X0,result);machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
checks=0
for mode,is_human,air,velocity,initial_direction,initial_seb in product((0,1),(False,True),(False,True),((0.,0.),(0.,-5.),(5.,0.),(0.,5.),(-5.,0.),(5.,5.),(-5.,5.),(3.,-4.),(-4.,-3.)),range(4),(-5,0,120)):
 w32(render+0x38,mode);uc.mem_write(speed_obj+0x10,struct.pack('<3f',velocity[0],9.,velocity[1]));w32(direction+0x10,initial_direction);w32(seb+0x14,initial_seb);w64(image+0x10,301);w64(image+0x18,302);events=[]
 run(0x15dc510,x0=system)
 store=CombatEntities(entity,[]);store.allocate();store.add_component(entity,1,[velocity[0],9.,velocity[1]]);store.add_component(entity,14,[initial_direction]);store.add_component(entity,2,[0,initial_seb,99,-1]);store.add_component(entity,7,dict(res_ids=301,tex_ids=302));expected=[]
 def can_air(i):expected.append(('air',));return air
 def trig(name,fn):
  def call(a,b):expected.append((name,(a,b)));return fn(a,b)
  return call
 update_facing([entity],store,mode,can_air,lambda i:is_human,lambda i,front:(101,102) if front else (201,202),trig('atan2',atan2),trig('fmod',fmod))
 c=store.objects[entity]['components']
 assert events==expected and c[14][0]==r32(direction+0x10) and c[2][1]==r32(seb+0x14),(mode,air,velocity,initial_direction,initial_seb,c[14],r32(direction+0x10),events,expected)
 assert (c[7]['res_ids'],c[7]['tex_ids'])==struct.unpack('<2Q',uc.mem_read(image+0x10,16))
 checks+=1
report=dict(nativeFacingPhaseCases=checks,scope='Original RotateSystem.Update and HashSet.MoveNext versus shared Direction/Seb/Image references; stationary/axis/tie/diagonal vectors, render modes, humans and vehicle-air branch',limitations=['CanMoveInAir and component access supplied; double atan2/fmod supplied from host math on both sides, not Android libm capture'])
(EVIDENCE/'facing-phase-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');print(json.dumps(report))
