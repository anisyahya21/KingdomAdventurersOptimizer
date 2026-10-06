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

from combat_effects import tick_modifiers
from combat_entities import CombatEntities
from combat_collections import ComponentSubset,EntitySlotSet
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

native_set,native_slots=0x10010000,0x10011000
w64(subset+0x30,native_set);w64(native_set+0x18,native_slots);w32(native_slots+0x18,8)
refs={};events=[];list_index=0

def snapshot():return tuple((e,r32(p+0x2c)) for e,p in refs.items())
def external(machine,address,size,user):
 global list_index
 if address==0x16bbc28:events.append(('dirty',snapshot()))
 if 0x15bc35c<=address<0x15bca78 or 0x16bbc28<=address<0x16bbc34 or 0x1bcf0a8<=address<0x1bcf148:return
 result=0
 if address==0x1cd2110:machine.mem_write(reg('X8'),bytes(24));w64(reg('X8'),native_set)
 elif address in (0x1bcf0a4,0x1bcf584,0x2671a34):pass
 elif address==0x1470924:result=refs[reg('X0')]
 elif address==0x1dec758:list_index=0;machine.mem_write(reg('X8'),bytes(24))
 elif address==0x1bcf588:
  result=int(list_index<r32(queue+0x18))
  if result:
   w64(reg('X0')+0x10,struct.unpack('<Q',uc.mem_read(items+0x20+list_index*8,8))[0]);list_index+=1
 elif address==0x1473e30:events.append(('destroy',reg('X0'),snapshot()))
 elif address==0x1470b54:events.append(('remove',reg('X0'),snapshot()))
 else:raise AssertionError(hex(address))
 machine.reg_write(UC_ARM64_REG_X0,int(result));machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
checks=0
for mask,reuse,base_frame in product(range(16),(False,True),(-1,0,5,6,7)):
 members=EntitySlotSet();refs={};models={};events=[]
 for i in range(4):
  e=0x10020000+i*0x1000;p=e+0x100;refs[e]=p;members.add(e)
  model=dict(type=4 if i%2==0 else 7,frame=base_frame+i%2,duration=6,loop=False,destroy_on_finish=bool(mask&(1<<i)),offset_x=0.)
  models[e]=model
  w32(p+0x10,model['type']);w32(p+0x14,0);w32(p+0x2c,model['frame']);w32(p+0x30,6);uc.mem_write(p+0x38,bytes((model['destroy_on_finish'],False)))
 if reuse:
  keys=list(refs);members.remove(keys[0]);members.remove(keys[2]);members.add(keys[0]);members.add(keys[2])
 w32(native_set+0x24,len(members.slots));w32(native_set+0x38,0)
 for i,e in enumerate(members.slots):w32(native_slots+0x20+i*16,1);w64(native_slots+0x28+i*16,e)
 run(0x15bc35c,x0=system)
 expected=[]
 def model_snapshot():return tuple((e,m['frame']) for e,m in models.items())
 modifier_set=ComponentSubset((19,),on_removed=lambda u,t,c:expected.append(('remove',u['id'],model_snapshot())) if not u.get('being_destroyed') else None)
 world=CombatEntities(0,[modifier_set])
 for e,m in models.items():world.manager['created_entities']=e;world.allocate();world.add_component(e,19,m)
 if reuse:
  keys=list(refs);modifier_set.members.remove(keys[0]);modifier_set.members.remove(keys[2]);modifier_set.members.add(keys[0]);modifier_set.members.add(keys[2])
 def destroyed(u):expected.append(('destroy',u['id'],model_snapshot()));u['being_destroyed']=True
 world.on_destroyed=destroyed
 tick_modifiers(modifier_set.members,world,lambda a:0.,lambda:expected.append(('dirty',model_snapshot())))
 assert events==expected,(mask,reuse,base_frame,events,expected)
 for e,p in refs.items():
  assert models[e]['frame']==r32(p+0x2c)
  actual=struct.unpack('<f',uc.mem_read(p+0x14,4))[0];assert actual==models[e]['offset_x']
 checks+=1
report=dict(nativeSharedModifierPhaseCases=checks,scope='Original ModifyAnimatinSystem.Update, HashSet.MoveNext and static-render dirty setter; all modifier writes precede ordered destroy/remove callbacks over shared storage',limitations=['Types4/7 in this batch; type5 arithmetic separately checked; component getters, list primitives and entity cleanup supplied'])
(EVIDENCE/'modifier-shared-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');print(json.dumps(report))
