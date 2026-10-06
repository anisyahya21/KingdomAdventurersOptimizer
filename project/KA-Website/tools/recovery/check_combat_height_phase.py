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

from combat_spatial import update_heights
from combat_entities import CombatEntities
from combat_collections import EntitySlotSet
from itertools import product
for flag in (0x316b260,):uc.mem_write(flag,b'\x01')
for i,got in enumerate((0x2f5f410,0x2f5fae0,0x2f5fae8,0x2f5faf0,0x2f5faf8,0x2f5f638,0x2f5f640,0x2f5fb68)):
 ptr,klass=0x10001000+i*8,0x10002000+i*0x100;w64(got,ptr);w64(ptr,klass);w32(klass+0xe0,1)
system,subset,native_set,native_slots,entity,position,cell,tile,tile_position,tile_cell,overlays=[0x10004000+i*0x1000 for i in range(11)]
w64(system+0x20,subset);w64(system+0x28,0x1000f000);w64(subset+0x30,native_set)
w64(native_set+0x18,native_slots);w32(native_set+0x24,1);w32(native_slots+0x18,1);w32(native_slots+0x20,1);w64(native_slots+0x28,entity)
w32(cell+0x10,2);w32(cell+0x14,4);w64(tile_cell+0x20,overlays)
tile_refs=[tile,tile+0x100,tile+0x200,tile+0x300];data_refs={t:0x10015000+i*0x100 for i,t in enumerate(tile_refs)}
events=[]
def external(machine,address,size,user):
 if 0x1595ba0<=address<0x1595f1c or 0x1bcf0a8<=address<0x1bcf148:return
 result=0
 if address==0x1cd2110:uc.mem_write(reg('X8'),bytes(24));w64(reg('X8'),native_set)
 elif address==0x1bcf0a4:pass
 elif address==0x14717ac:result=product_present
 elif address==0x1473224:result=treasure
 elif address==0x146ee7c:result=human
 elif address==0x146edf4:result=0x10016000
 elif address==0x14cab78:assert reg('W1')==32;result=human32
 elif address==0x148cca4:result=air
 elif address==0x146caa4:result=cell if reg('X0')==entity else tile_cell
 elif address==0x15afd9c:
  events.append(('map',i32(reg('W1')),i32(reg('W2')),bool(reg('W3'))));result=tile if tile_mode else 0
 elif address==0x147e7e0:result=reg('X0')==0 or tile_mode==2
 elif address==0x1471498:result=position if reg('X0')==entity else tile_position
 elif address in (0x146fcd8,):result=data_refs[reg('X0')]
 elif address in (0x14cca04,0x147dbd0):result=reg('X0')
 elif address==0x162d654:result=bool(r32(reg('X0')+0x1c)&reg('W1'))
 elif address==0x1deb9a8:result=tile_refs[1+reg('W1')]
 else:raise AssertionError(hex(address))
 machine.reg_write(UC_ARM64_REG_X0,result);machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
checks=0
for product_present,treasure,human,human32,air,tile_mode,blocked in product((False,True),(False,True),(False,True),(False,True),(False,True),(0,1,2),(False,True)):
 for initial_y in (99.,-3.5):
  events=[];w32(position+0x14,struct.unpack('<I',struct.pack('<f',initial_y))[0]);w32(tile_position+0x14,struct.unpack('<I',struct.pack('<f',2.5))[0]);w32(overlays+0x18,3)
  data={tile:dict(category=0,flags=0x4000000 if blocked else 0,height=7),
        tile_refs[1]:dict(category=7,flags=0,height=11),tile_refs[2]:dict(category=0,flags=0x4000000,height=100),tile_refs[3]:dict(category=0,flags=0,height=-3)}
  for t,d in data.items():w32(data_refs[t]+0x1c,d['flags']);w32(data_refs[t]+0x20,d['category']);w32(data_refs[t]+0x68,d['height'])
  run(0x1595ba0,x0=system)
  store=CombatEntities(entity,[]);store.allocate();store.add_component(entity,0,[0.,initial_y,96.,0.,0.,0.,None]);store.add_component(entity,5,[2,4])
  expected=[]
  def map_chip(x,y):expected.append(('map',x,y,False));return tile if tile_mode else None
  access=dict(has_product=lambda i:product_present,has_treasure=lambda i:treasure,has_human=lambda i:human,
      human_flag32=lambda i:human32,can_move_in_air=lambda i:air,map_chip=map_chip,
      null_or_destroyed=lambda t:t is None or tile_mode==2,data=lambda t:data[t],position=lambda t:[0.,2.5,0.],overlays=lambda t:tile_refs[1:])
  update_heights([entity],store,access)
  actual=struct.unpack('<f',uc.mem_read(position+0x14,4))[0]
  assert actual==store.objects[entity]['components'][0][1] and events==expected,(product_present,treasure,human,human32,air,tile_mode,blocked,actual,events,expected)
  checks+=1
report=dict(nativeHeightPhaseCases=checks,scope='Original HeightSystem.Update and HashSet.MoveNext versus shared Position, including all early gates, absent/destroyed/blocked base tile and stacked overlay heights',limitations=['Map/data/component access and CanMoveInAir supplied; membership separately native-checked; overlay fixtures contain non-null entities/data'])
(EVIDENCE/'height-phase-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');print(json.dumps(report))
