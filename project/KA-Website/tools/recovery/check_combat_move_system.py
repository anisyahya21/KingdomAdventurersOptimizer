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

from combat_spatial import update_positions
from combat_entities import CombatEntities
from combat_collections import EntitySlotSet
from itertools import product
for flag in (0x316b3b2,):uc.mem_write(flag,b'\x01')
for got in (0x2f5fae0,0x2f5fae8,0x2f5faf0,0x2f5faf8):w64(got,0x10001000)
system,subset,native_set,native_slots=0x10002000,0x10003000,0x10004000,0x10005000
w64(system+0x20,subset);w64(subset+0x30,native_set);w64(native_set+0x18,native_slots);w32(native_slots+0x18,8)
refs={}
def external(machine,address,size,user):
 if 0x15bfbf8<=address<0x15bfda0 or 0x1bcf0a8<=address<0x1bcf148:return
 result=0
 if address==0x1cd2110:
  uc.mem_write(reg('X8'),bytes(24));w64(reg('X8'),native_set)
 elif address==0x1bcf0a4:pass
 elif address==0x1471498:result=refs[reg('X0')][0]
 elif address==0x1472858:result=refs[reg('X0')][1]
 elif address==0x1471520:result=refs[reg('X0')][0]!=0
 else:raise AssertionError(hex(address))
 machine.reg_write(UC_ARM64_REG_X0,result);machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
checks=0
for parent_mode,reuse,zero_speed,negative in product(range(6),(False,True),(False,True),(False,True)):
 store=CombatEntities(0,[]);members=EntitySlotSet();refs={}
 for i in range(4):
  identity=store.allocate();assert identity==i;members.add(i)
  pos=[float(i*24),float(i*3),float(i*-24),1.25,-3.,7.,None]
  speed=[0.,0.,0.] if zero_speed else [(-1. if negative else 1.)*4.46,2.5,-3.25]
  # Exact stored float32 values feed both sides.
  speed=list(struct.unpack('<3f',struct.pack('<3f',*speed)))
  store.add_component(i,0,pos);store.add_component(i,1,speed)
 if parent_mode==1:store.objects[1]['components'][0][6]=0
 elif parent_mode==2:store.objects[0]['components'][0][6]=1
 elif parent_mode==3:store.objects[0]['components'][0][6]=0
 elif parent_mode==4:
  store.objects[0]['components'][0][6]=1;store.objects[1]['components'][0][6]=0
 elif parent_mode==5:
  p=store.allocate();store.objects[0]['components'][0][6]=p
 if reuse:members.remove(0);members.remove(2);members.add(0);members.add(2)
 for i in store.objects:
  entity=0x10010000+i*0x1000;pos=store.objects[i]['components'][0]
  refs[entity]=(entity+0x100 if pos is not None else 0,entity+0x200)
  if pos is not None:
   uc.mem_write(entity+0x110,struct.pack('<6f',*pos[:6]))
   w64(entity+0x128,0 if pos[6] is None else 0x10010000+pos[6]*0x1000)
   uc.mem_write(entity+0x210,struct.pack('<3f',*store.objects[i]['components'][1]))
 w32(native_set+0x24,len(members.slots));w32(native_set+0x38,0)
 for slot,i in enumerate(members.slots):w32(native_slots+0x20+slot*16,1);w64(native_slots+0x28+slot*16,0x10010000+i*0x1000)
 run(0x15bfbf8,x0=system);update_positions(members,store)
 for i in members:
  actual=struct.unpack('<3f',uc.mem_read(refs[0x10010000+i*0x1000][0]+0x10,12))
  assert actual==tuple(store.objects[i]['components'][0][:3]),(parent_mode,reuse,i,actual,store.objects[i])
 checks+=1
report=dict(nativeMoveSystemCases=checks,scope='Original MoveSystem.Update and HashSet.MoveNext versus shared components, including parent-before/after, self-parent, cycles and missing parent Position',limitations=['Component getters and enumerator creation supplied; parent links supplied as fixture inputs, not a claim that cycles occur in game'])
(EVIDENCE/'move-system-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');print(json.dumps(report))
