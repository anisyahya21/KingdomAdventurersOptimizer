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

from combat_spatial import CellOccupancy,cell_key
from combat_entities import CombatEntities
from itertools import product
for flag in (0x316aec4,0x316aec5,0x316aec6,0x316aec8,0x316aec7):uc.mem_write(flag,b'\x01')
for got in (0x2f623b0,0x2f623b8,0x2f5d7d0,0x2f61fe0,0x2f5d230,0x2f5d228,0x2f60588,0x2f5eb50):w64(got,0x10001000)
klass=0x10002000;w64(0x10001000,klass);w32(klass+0xe0,1);uc.mem_write(klass+0x130,b'\x01');w64(klass+0xc8,0x10003000);w64(0x10003000,klass)
system,map_obj,entity,cell=0x10004000,0x10005000,0x10006000,0x10007000
w64(system+0x10,0x10008000);w64(system+0x28,0x10009000);w64(cell,klass)
buckets={};allocation=0

def alloc_list():
 global allocation
 allocation+=1;p=0x10010000+allocation*0x1000
 uc.mem_write(p,bytes(0x1000));w64(p+0x10,p+0x100);w32(p+0x118,32)
 return p

def get_values(p):return list(struct.unpack('<'+'Q'*r32(p+0x18),uc.mem_read(p+0x120,r32(p+0x18)*8)))

def set_values(p,values):
 w32(p+0x18,len(values))
 if values:uc.mem_write(p+0x120,struct.pack('<'+'Q'*len(values),*values))

def external(machine,address,size,user):
 if any(a<=address<b for a,b in ((0x1500c10,0x1500dd4),(0x1500a04,0x1500b14),(0x14fff90,0x1500118),(0x1500b14,0x1500c08))):return
 result=0
 if address==0x18c05a0:result=buckets.get(i32(reg('W1')),0)
 elif address==0x1ded25c:
  p=reg('X0');v=get_values(p);e=reg('X1');result=e in v
  if result:v.remove(e);set_values(p,v)
 elif address==0x146caa4:result=cell
 elif address==0x1475b3c:result=map_obj
 elif address==0x12d23b8:result=alloc_list()
 elif address==0x1deb4a0:pass
 elif address==0x1b11568:
  key=i32(reg('W1'));assert key not in buckets;buckets[key]=reg('X2')
 else:raise AssertionError(hex(address))
 machine.reg_write(UC_ARM64_REG_X0,result);machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
checks=0
for width,old_xy,new_xy,duplicates in product((5,64,2147483647),((0,0),(-2,4),(7,-9)),((0,0),(1,0),(-4,8)),(0,1,2,3)):
 store=CombatEntities(entity,[]);identity=store.allocate();assert identity==entity
 store.add_component(identity,5,list(new_xy));occupancy=CellOccupancy(store,width)
 old_key=cell_key(*old_xy,width);new_key=cell_key(*new_xy,width)
 buckets={};allocation=0
 occupancy.buckets[old_key]=[entity-1]+[entity]*duplicates+[entity+1]
 for key,values in occupancy.buckets.items():p=alloc_list();set_values(p,values);buckets[key]=p
 w32(map_obj+0x10,width);w32(cell+0x10,new_xy[0]);w32(cell+0x14,new_xy[1])
 # Change, add, non-Cell no-op, remove and repeated remove preserve empty buckets.
 for method,ctype in ((0x1500c10,None),(0x14fff90,5),(0x14fff90,0),(0x1500b14,0),(0x1500b14,5),(0x1500b14,5),(0x1500b14,5)):
  if ctype is None:
   run(method,x0=system,x1=entity,w2=old_key);occupancy.changed(identity,old_key)
  else:
   run(method,x0=system,x1=entity,w2=ctype,x3=cell)
   (occupancy.added if method==0x14fff90 else occupancy.removed)(store.objects[identity],ctype,list(new_xy))
  actual={key:get_values(p) for key,p in buckets.items()}
  assert actual==occupancy.buckets,(width,old_xy,new_xy,duplicates,hex(method),actual,occupancy.buckets)
  checks+=1
report=dict(nativeOccupancyOperations=checks,scope='Original AddCellEntity, RemoveCellEntity, ChangeCellEntity and GetCellKey; native inlined list append, supplied List.Remove and dictionary/allocation operations',findings=['Old buckets retained even when empty','Change removes one old occurrence then appends to current Cell bucket','Only component type5 triggers add/remove callbacks'])
(EVIDENCE/'cell-occupancy-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');print(json.dumps(report))
