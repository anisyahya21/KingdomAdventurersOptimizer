"""Native effect creation component order and resource-derived lifetime."""
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

from combat_states import update_moving,exit_moving,exit_damaging,move_fighter
from itertools import product
from combat_entities import FighterView
system,entity,ai,blackboard,position,velocity,animation,direction,cell,queue,point,map_obj = [0x10004000+i*0x1000 for i in range(12)]
w64(ai+0x58,blackboard);w64(ai+0x68,queue);w64(system+0x10,0x10003000)
uc.mem_map(0x750000,0x10000);uc.mem_write(0x753540,struct.pack('<f',4.46));uc.mem_write(0x75359c,struct.pack('<f',.1))
for flag in (0x316b1c5,0x316b1c6,0x316b1d1,0x316b1df,0x316b1e0,0x316b1e1,0x316b1e2,0x316b1e3,0x316ab72,0x316ab73,0x316e376):uc.mem_write(flag,b'\x01')
for got in (0x2f5f410,0x2f5fd60,0x2f60048,0x2f602c0,0x2f64bd8,0x2f602c8,0x2f5fb60,0x2f5e4c0,0x2f5fea0,0x2f5b0f0):w64(got,0x10001000)
w64(0x10001000,0x10002000);w32(0x100020e0,1);w64(0x100020b8,0x10002100);w64(0x10002110,0x10002200);w32(0x10002218,4)
for i in range(4):
 ptr=0x10002300+i*0x100;w64(0x10002220+i*8,ptr);w32(ptr+0x14,(-1,0,1,0)[i])
ranges=[(0x15845dc,0x1584918),(0x158491c,0x15849d0),(0x15849d8,0x1584b8c),(0x15861f4,0x1586350),
 (0x148c2e0,0x148c3c8),(0x148c8d8,0x148ca6c),(0x23dd6a4,0x23dd700),(0x145fc10,0x145fc18),
 (0x1586354,0x1586444),(0x15882c8,0x158840c),(0x1583550,0x1583578),(0x158357c,0x15835a4)]
board={};events=[];path=[]
def external(machine,address,size,user):
 if any(a<=address<b for a,b in ranges):return
 result=0
 if address==0x1468bc8:result=ai
 elif address==0x1a6a108:result=board[reg('W1')]
 elif address==0x1a6a12c:board[reg('W1')]=i32(reg('W2'))
 elif address==0x1471498:result=position
 elif address==0x1472858:result=velocity
 elif address==0x146b9d8:result=animation
 elif address==0x146d3f4:result=direction
 elif address==0x146caa4:result=cell
 elif address==0x1475b3c:result=map_obj
 elif address==0x15833c4:result=row_offset
 elif address==0x1fa0024:w32(point+0x10,path[0][0]);w32(point+0x14,path[0][1]);result=point
 elif address==0x1f9ffa4:path.pop(0);w32(queue+0x20,len(path))
 elif address==0x1f9fae0:path.clear();w32(queue+0x20,0)
 elif address==0x145f3f4:result=(reg('W0')+2)%4
 elif address==0x15835a8:events.append(('decide',));result=3
 elif address==0x1583b48:events.append(('state',reg('W1')))
 else:raise AssertionError(hex(address))
 machine.reg_write(UC_ARM64_REG_X0,int(result)&0xffffffffffffffff);machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
checks=0
for method in (0x15845dc,0x15849d8,0x15861f4):
 for team,row_offset,grid,count,start,cell_z in product((0,1),(3,8),(-6,0,6),(0,1,2),((0.,15.,0.),(23.,15.,24.),(24.,15.,24.)),(0,4)):
  board={6:team,7:grid};events=[];path=[(24,24),(48,24)][:count]
  w32(queue+0x20,count);w32(animation+0x10,2);w32(direction+0x10,1);w32(cell+0x10,2);w32(cell+0x14,cell_z)
  w32(map_obj+0x20,24);w32(map_obj+0x24,24)
  uc.mem_write(position+0x10,struct.pack('<6f',*start,3.,4.,5.));uc.mem_write(velocity+0x10,struct.pack('<3f',7.,8.,9.))
  components=[None]*52
  components[0]=list(start)+[3.,4.,5.,None];components[1]=[7.,8.,9.];components[5]=[2,cell_z]
  components[12]=[2,-1];components[14]=[1];components[28]={'board':dict(board),'long_board':{},'path':list(path),'commands':[]}
  unit=FighterView({'id':1,'flags':0,'components':components})
  run(method,x0=system,x1=entity)
  expected=[]
  def decide(u):expected.append(('decide',));return 3
  if method==0x15845dc:update_moving(unit,move_fighter,lambda:row_offset,decide,lambda u,n:expected.append(('state',n)))
  elif method==0x15849d8:exit_moving(unit,lambda:row_offset)
  else:exit_damaging(unit,row_offset)
  assert events==expected and board==unit['board'] and path==unit['path'],(method,events,expected,board,unit,path)
  assert tuple(unit['position']+unit['offset'])==struct.unpack('<6f',uc.mem_read(position+0x10,24)),(method,unit)
  assert tuple(unit['velocity'])==struct.unpack('<3f',uc.mem_read(velocity+0x10,12))
  assert unit['animation_rate']==r32(animation+0x10) and unit['direction']==r32(direction+0x10)
  checks+=1
result=dict(nativeMovingAndDamageExitCases=checks,scope='Original UpdateMoving with AISystem.Move/MoveBase and CellToGrid, ExitMoving, and ExitDamaging with native grid/position conversions',limitations=['Component/blackboard access, waypoint queue primitives, row offset and next-state callback supplied; no later MoveSystem/CellSystem pass'])
(EVIDENCE/'moving-state-checks.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8');print(json.dumps(result))
