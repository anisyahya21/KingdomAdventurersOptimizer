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

from combat_animation import update_animations
from combat_entities import CombatEntities
from combat_collections import EntitySlotSet
from itertools import product
for flag in (0x316addd,0x316dec6,0x316deef):uc.mem_write(flag,b'\x01')
for i,got in enumerate((0x2f5fb70,0x2f5fae0,0x2f5fae8,0x2f5faf0,0x2f5faf8,0x2f68718,0x2f5b778)):
 ptr,klass,statics=0x10001000+i*8,0x10002000+i*0x100,0x10003000+i*0x100
 w64(got,ptr);w64(ptr,klass);w32(klass+0xe0,1);w64(klass+0xb8,statics)
system,subset,native_set,native_slots,managers,manager,sebs,resource=[0x10004000+i*0x1000 for i in range(8)]
w64(system+0x20,subset);w64(subset+0x30,native_set);w64(native_set+0x18,native_slots);w32(native_slots+0x18,8)
w64(system+0x28,managers);w32(managers+0x18,1);w64(managers+0x20,manager)
w64(manager+0x18,sebs);w32(sebs+0x18,1);w64(sebs+0x20,resource)
refs={};events=[]
def snapshots():return tuple((e,r32(seb+0x18),r32(animation+0x14)) for e,(seb,animation,modifier) in refs.items())
def external(machine,address,size,user):
 if any(a<=address<b for a,b in ((0x14dc884,0x14dcbc4),(0x2351a64,0x2351b3c),(0x1bcf0a8,0x1bcf148))):return
 result=0
 if address==0x1cd2110:uc.mem_write(reg('X8'),bytes(24));w64(reg('X8'),native_set)
 elif address==0x1bcf0a4:pass
 elif address==0x14721c8:result=refs[reg('X0')][0]
 elif address==0x146b9d8:result=refs[reg('X0')][1]
 elif address==0x14709ac:result=refs[reg('X0')][2]!=0
 elif address==0x1470924:result=refs[reg('X0')][2]
 elif address==0x165dea8:
  e=reg('X0');events.append((e,reg('W1'),snapshots(),r32(resource+0x18)))
  # Supplied ChangeAnimation resets Seb.frame; actual image selection is separate.
  w32(refs[e][0]+0x18,0)
 else:raise AssertionError(hex(address))
 machine.reg_write(UC_ARM64_REG_X0,result);machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
checks=0
for enabled,auto,common,reuse,frame,rate,duration in product((False,True),(False,True),(False,True),(False,True),(-1,0,9,2147483647),(0,1,4),(0,1,10)):
 store=CombatEntities(0,[]);members=EntitySlotSet();refs={};events=[]
 for i in range(4):
  store.allocate();members.add(i);store.add_component(i,2,[0,0,frame,-1]);store.add_component(i,12,[rate,3 if i%2==0 else -1])
  if i>=2:store.add_component(i,19,{'type':10 if i==2 else 4})
  e=0x10010000+i*0x1000;refs[e]=(e+0x100,e+0x200,e+0x300 if i>=2 else 0)
  uc.mem_write(e+0x110,struct.pack('<4i',*store.objects[i]['components'][2]));uc.mem_write(e+0x210,struct.pack('<2i',*store.objects[i]['components'][12]));w32(e+0x310,10 if i==2 else 4)
 if reuse:members.remove(0);members.remove(2);members.add(0);members.add(2)
 w32(native_set+0x24,len(members.slots));w32(native_set+0x38,0)
 for slot,i in enumerate(members.slots):w32(native_slots+0x20+slot*16,1);w64(native_slots+0x28+slot*16,0x10010000+i*0x1000)
 uc.mem_write(system+0x18,bytes([enabled]));uc.mem_write(0x10003514,bytes([auto]));uc.mem_write(0x1000361c,bytes([common]));w32(resource+0x18,frame);w32(resource+0x1c,duration)
 run(0x14dc884,x0=system)
 resources=[[{'frame':frame,'max_frame':duration}]];expected=[]
 def change(i,m):
  snapshot=tuple((0x10010000+j*0x1000,e['components'][2][2],e['components'][12][1]) for j,e in store.objects.items())
  expected.append((0x10010000+i*0x1000,m,snapshot,resources[0][0]['frame']))
  store.objects[i]['components'][2][2]=0
 update_animations(members,store,resources,dict(enabled=enabled,auto_animation=auto,common_frames_update=common),change)
 assert events==expected,(enabled,frame,rate,duration,events,expected)
 assert resources[0][0]['frame']==r32(resource+0x18)
 for i,e in store.objects.items():
  seb,anim,mod=refs[0x10010000+i*0x1000]
  assert e['components'][2][2]==r32(seb+0x18) and e['components'][12][1]==r32(anim+0x14)
 checks+=1
report=dict(nativeAnimationPhaseCases=checks,scope='Original AnimationSystem.Update, Seb.Frame and HashSet.MoveNext versus shared components; global resource gates, paused type10 exception, overflow and zero-duration remainder, callback-visible frame/nextBehavior ordering',limitations=['Component getters, enumerator creation and ChangeAnimation image/behavior callee supplied; one valid resource manager/clip fixture'])
(EVIDENCE/'animation-phase-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');print(json.dumps(report))
