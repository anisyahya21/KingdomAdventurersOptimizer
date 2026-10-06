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

from combat_projectiles import fire_shared_projectile
from combat_entities import CombatEntities
from itertools import product
for flag in (0x316ab62,0x316a9ce,0x316e376):uc.mem_write(flag,b'\x01')
for i,got in enumerate((0x2f5b570,0x2f5e538,0x2f5b0f0)):
 ptr,klass=0x10001000+i*8,0x10002000+i*0x100;w64(got,ptr);w64(ptr,klass);w32(klass+0xe0,1)
entity,skill=0x10004000,0x10005000;w32(skill+0x18,42)
events=[]
def floats():return tuple(struct.unpack('<f',struct.pack('<I',reg('S'+str(i))))[0] for i in range(6))
def external(machine,address,size,user):
 if any(a<=address<b for a,b in ((0x14832d0,0x1483504),(0x14607f8,0x14608d4),(0x14608e4,0x14608f4),(0x23dd6a4,0x23dd700))):return
 result=0
 if address==0x16657b0:result=max(i32(reg('W0')),i32(reg('W1')))
 elif address==0x16657ec:result=max(i32(reg('W1')),min(i32(reg('W2')),i32(reg('W0'))))
 elif address==0x14709ac:result=has_modifier
 elif address==0x1470b54:events.append(('remove',19));result=entity
 elif address==0x1471a10:
  v=floats();events.append(('add',39,dict(start=v[:3],end=v[3:],speed=i32(reg('W1')),height=i32(reg('W2')),length=i32(reg('W3')),frame=i32(reg('W4')),owner=reg('X5') or None)));result=entity
 elif address==0x14709bc:
  v=floats();events.append(('add',19,dict(type=reg('W1'),offset_x=v[0],offset_y=v[1],offset_z=v[2],scale_x=v[3],scale_y=v[4],angle=reg('W2'),anchor=reg('W3'),frame=reg('W4'),duration=reg('W5'),destroy_on_finish=bool(reg('W6')),loop=bool(reg('W7')),alpha=r32(reg('SP')))));result=entity
 elif address==0x146ba70:events.append(('add',12,[i32(reg('W1')),i32(reg('W2'))]));result=entity
 elif address==0x146bf4c:events.append(('add',46,[i32(reg('W1'))]));result=entity
 else:raise AssertionError(hex(address))
 machine.reg_write(UC_ARM64_REG_X0,result&0xffffffffffffffff);machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
checks=0
for has_modifier,occupied,animate,attack,speed in product((False,True),(False,True),(False,True),(False,True),(-1,1,5,10,100)):
 events=[];start=(24.,11.,-48.);end=(-48.,0.,96.)
 registers={'x0':entity,'w1':speed,'x2':123,'w3':animate,'x4':skill if attack else 0}
 registers.update({'s'+str(i):struct.unpack('<I',struct.pack('<f',v))[0] for i,v in enumerate(start+end)})
 actual=run(0x14832d0,**registers)
 store=CombatEntities(entity,[]);store.allocate();store.add_component(entity,0,[99.,98.,97.,0.,0.,0.,None]);store.add_component(entity,1,[9.,8.,7.])
 if has_modifier:store.add_component(entity,19,{'type':4})
 original_projectile={'frame':999}
 if occupied:
  store.add_component(entity,39,original_projectile);store.add_component(entity,12,[4,7]);store.add_component(entity,46,[88])
 expected=[];add=store.add_component;remove=store.remove_component
 def capture_add(e,t,c):expected.append(('add',t,list(c) if isinstance(c,tuple) else c));add(e,t,c)
 def capture_remove(e,t):
  if store.objects[e]['components'][t] is not None:expected.append(('remove',t))
  remove(e,t)
 store.add_component=capture_add;store.remove_component=capture_remove
 result=fire_shared_projectile(store,entity,start,end,speed,123,animate,42 if attack else None)
 assert actual==result and events==expected,(events,expected)
 c=store.objects[entity]['components'];assert c[0][:3]==[99.,98.,97.] and c[1]==[9.,8.,7.]
 if occupied:assert c[39] is original_projectile and c[12]==[4,7] and c[46]==[88]
 assert c[19]['type']==7
 checks+=1
report=dict(nativeSharedLaunchCases=checks,scope='Original FireProjectile and vector calculations; ordered remove/add constructor calls composed with checked BaseEntity.Add semantics',findings=['Launch preserves existing Position and Speed','Existing Projectile, Animation and Attack slots survive Add; modifier is explicitly removed and re-added','Returned flight length is calculated for this call even if an existing Projectile is retained'],limitations=['Generated Add/Remove wrappers, pooling and allocation supplied; native BaseEntity.Add separately checked'])
(EVIDENCE/'projectile-launch-shared-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');print(json.dumps(report))
