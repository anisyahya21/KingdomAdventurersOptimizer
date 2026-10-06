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

from combat_spatial import update_cells
from combat_entities import CombatEntities
from combat_collections import EntitySlotSet
from itertools import product
for flag in (0x316aed2,):uc.mem_write(flag,b'\x01')
for got in (0x2f60588,0x2f5fae0,0x2f62410,0x2f5fae8,0x2f62418,0x2f62420,0x2f5faf0,0x2f5faf8,0x2f62428,0x2f62430,0x2f62438,0x2f605f0):w64(got,0x10001000)
w64(0x10001000,0x10001100);w32(0x100011e0,1)
system,subset,native_set,native_slots,map_obj,pending,items,delegate=[0x10002000+i*0x1000 for i in range(8)]
w64(system+0x10,0x1000f000);w64(system+0x20,subset);w64(subset+0x30,native_set)
w64(native_set+0x18,native_slots);w32(native_slots+0x18,8)
w64(system+0x28,map_obj);w64(system+0x30,pending);w64(system+0x38,delegate)
w64(pending+0x10,items);w32(items+0x18,8);w64(delegate+0x18,0x15ed5e8)
refs={};list_index=0;events=[]
def snapshots():return tuple((i,struct.unpack('<2i',uc.mem_read(cell+0x10,8))) for i,(pos,cell) in refs.items())
def external(machine,address,size,user):
 global list_index
 if any(a<=address<b for a,b in ((0x15018f0,0x1501c70),(0x15ed5e8,0x15ed610),(0x145fc10,0x145fc18),(0x1bcf0a8,0x1bcf148))):return
 result=0
 if address==0x1cd2110:
  uc.mem_write(reg('X8'),bytes(24));w64(reg('X8'),native_set)
 elif address in (0x1bcf0a4,0x1bdd508,0x2671a34):pass
 elif address==0x1471498:result=refs[reg('X0')][0]
 elif address==0x146caa4:result=refs[reg('X0')][1]
 elif address==0x1e94084:uc.mem_write(reg('X8'),bytes(32));list_index=0
 elif address==0x1bdd50c:
  result=int(list_index<r32(pending+0x18))
  if result:
   uc.mem_write(reg('X0')+0x10,bytes(uc.mem_read(items+0x20+list_index*16,16)));list_index+=1
 elif address==0x191e7c8:
  assert reg('W1')==40
  events.append((reg('X2'),i32(reg('W3')),snapshots()))
  # Simulate a synchronous subscriber changing the next queued entity's Cell.
  if mutate and len(events)==1:
   next_e=order[1];cell=refs[next_e][1];w32(cell+0x10,777)
 else:raise AssertionError(hex(address))
 machine.reg_write(UC_ARM64_REG_X0,result);machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
checks=0
for reuse,mutate,map_width,shift in product((False,True),(False,True),(5,64,2147483647),(-48.5,-24.,-.5,0.,24.5,2147483648.)):
 store=CombatEntities(0,[]);members=EntitySlotSet();refs={};events=[]
 for i in range(4):
  store.allocate();members.add(i)
  pos=[shift+i*24.,0.,shift-i*24.,0.,0.,0.,None]
  pos[:6]=struct.unpack('<6f',struct.pack('<6f',*pos[:6]))
  cell=[100+i,-100-i]
  store.add_component(i,0,pos);store.add_component(i,5,cell)
  e=0x10010000+i*0x1000;refs[e]=(e+0x100,e+0x200)
  uc.mem_write(e+0x110,struct.pack('<3f',*pos[:3]));uc.mem_write(e+0x210,struct.pack('<2i',*cell))
 if reuse:members.remove(0);members.remove(2);members.add(0);members.add(2)
 order=[0x10010000+i*0x1000 for i in members]
 w32(native_set+0x24,len(members.slots));w32(native_set+0x38,0)
 for slot,i in enumerate(members.slots):w32(native_slots+0x20+slot*16,1);w64(native_slots+0x28+slot*16,0x10010000+i*0x1000)
 w32(map_obj+0x10,map_width);w32(map_obj+0x20,24);w32(map_obj+0x24,24);w32(pending+0x18,0)
 run(0x15018f0,x0=system)
 expected=[]
 def on_change(i,old_key):
  snapshot=tuple((0x10010000+j*0x1000,tuple(e['components'][5])) for j,e in store.objects.items())
  expected.append((0x10010000+i*0x1000,old_key,snapshot))
  if mutate and len(expected)==1:store.objects[list(members)[1]]['components'][5][0]=777
 update_cells(members,store,map_width,24,24,on_change)
 assert events==expected,(reuse,mutate,map_width,shift,events,expected)
 assert r32(pending+0x18)==0
 checks+=1
report=dict(nativeCellSystemCases=checks,scope='Original CellSystem.Update, battle coordinate delegate and HashSet.MoveNext versus shared state; all Cell writes precede notifications, and later callbacks see earlier subscriber mutations',limitations=['Component getters, pending-list enumeration, Send subscribers and array clearing supplied; does not include CellCulling occupancy subscriber'])
(EVIDENCE/'cell-system-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');print(json.dumps(report))
