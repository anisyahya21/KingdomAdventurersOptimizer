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

from combat_entities import image_component,materialize_component
from itertools import product
for flag in (0x316aa95,0x316aa96):uc.mem_write(flag,b'\x01')
for got in (0x2f5b218,0x2f5ea08,0x2f5ed28,0x2f5ed30,0x2f5ed38):w64(got,0x10001000)
klass,statics,pool,entity,component=0x10002000,0x10003000,0x10004000,0x10005000,0x10006000
w64(0x10001000,klass);w32(klass+0xe0,1);w64(klass+0xb8,statics);w64(statics+0xb0,pool);w32(pool+0x18,1)
w64(entity,klass);w64(klass+0x188,0x30000100)
allocation=0;captured=None
fields=('tex_ids','res_ids','tex_u','tex_v','tex_w','tex_h');offsets=(0x18,0x10,0x20,0x28,0x30,0x38)
def arr(values):
 global allocation
 allocation+=1;p=0x10010000+allocation*0x100;uc.mem_write(p,bytes(0x100));w32(p+0x18,len(values))
 for i,v in enumerate(values):w32(p+0x20+i*4,v)
 return p

def external(machine,address,size,user):
 global captured
 if 0x146f154<=address<0x146f29c or 0x146f2a4<=address<0x146f51c:return
 result=0
 if address==0x12d2214:result=arr([0]*reg('W1'))
 elif address==0x204a0c4:result=component
 elif address==0x30000100:
  assert reg('W1')==7 and reg('X2')==component
  captured={}
  for name,offset in zip(fields,offsets):
   p=struct.unpack('<Q',uc.mem_read(component+offset,8))[0]
   captured[name]=(p,[r32(p+0x20+i*4) for i in range(r32(p+0x18))])
  result=entity
 else:raise AssertionError(hex(address))
 machine.reg_write(UC_ARM64_REG_X0,result);machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
checks=0
for kinds in product(range(3),repeat=6):
 allocation=0;values=[None if k==0 else [] if k==1 else [i,10+i] for i,k in enumerate(kinds)]
 pointers=[0 if v is None else arr(v) for v in values]
 run(0x146f2a4,x0=entity,**{'x'+str(i+1):p for i,p in enumerate(pointers)})
 expected=image_component(*values)
 for name,v,p in zip(fields,values,pointers):
  assert captured[name][1]==expected[name]
  if v:assert captured[name][0]==p and expected[name] is v
  else:assert captured[name][0]!=p and expected[name] is not v
 assert len({captured[name][0] for name in fields})==6
 checks+=1
for seed in range(16):
 allocation=0;values=tuple(seed*7-i*13 for i in range(6))
 run(0x146f154,x0=entity,**{'w'+str(i+1):v for i,v in enumerate(values)})
 assert {name:v for name,(p,v) in captured.items()}==materialize_component(7,values)
 checks+=1
report=dict(nativeImageConstructionCases=checks,scope='Original scalar and array AddImage overloads with retained input-array identity, missing/empty array defaults and shared component materialization',limitations=['Integer-array allocation, component pool Pop and final BaseEntity.Add supplied'])
(EVIDENCE/'image-component-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');print(json.dumps(report))
