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

from itertools import product
from combat_states import update_damaging,update_knocking_down,update_leaving
system,entity,ai,blackboard,position,parameter,direction,battle,worlds= [0x10004000+i*0x1000 for i in range(9)]
w64(ai+0x58,blackboard);w64(system+0x10,0x10003000);w64(worlds+0x10,0x10003000)
for flag in (0x316b1d0,0x316b1d3,0x316b1d5):uc.mem_write(flag,b'\x01')
for got in (0x2f60048,0x2f5fd60,0x2f5e4c0,0x2f64bb8,0x2f5d420,0x2f5fb70):w64(got,0x10001000)
w64(0x10001000,0x10002000);w32(0x100020e0,1);w64(0x100020b8,0x10002100);w64(0x10002110,0x10002200)
w32(0x10002218,4)
for i in range(4):
 ptr=0x10002300+i*0x100;w64(0x10002220+i*8,ptr);w32(ptr+0x14,(-1,0,1,0)[i])
ranges=[(0x1585f04,0x15861ec),(0x1444988,0x14449d8),(0x1586954,0x1586a78),(0x15870cc,0x1587174)]
board={};events=[]
def offset():return struct.unpack('<f',uc.mem_read(position+0x24,4))[0]
def external(machine,address,size,user):
 if any(a<=address<b for a,b in ranges):return
 result=0
 if address==0x1468bc8:result=ai
 elif address==0x1a6a108:result=board[reg('W1')]
 elif address==0x15833c4:result=3
 elif address==0x1471498:result=position
 elif address==0x1471200:result=parameter
 elif address==0x14ce670:events.append(('hp',offset()));result=hp
 elif address==0x15835a8:events.append(('decide',offset()));result=3
 elif address==0x146ee7c:result=human
 elif address==0x1470c84:result=monster
 elif address==0x146dbf4:result=enemy
 elif address==0x191de04:result=battle
 elif address==0x14c2b14:result=worlds
 elif address==0x1470bfc:result=0x1000d000
 elif address==0x14cdb94:result=0x1000e000
 elif address==0x15bed5c:events.append(('defeated',offset()))
 elif address==0x1583b48:events.append(('state',reg('W1'),offset()))
 elif address==0x165dea8:events.append(('animation',reg('W1'),offset()))
 elif address==0x146d3f4:result=direction
 else:raise AssertionError(hex(address))
 machine.reg_write(UC_ARM64_REG_X0,int(result)&0xffffffffffffffff);machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
checks=0
for frame,team,hp,human,monster,enemy,pvp,base_z in product((-1,0,3,6,7,8,2147483647),(0,1),(-1,0,1),(False,True),(False,True),(False,True),(False,True),(-2147483648,96,2147483647)):
 board={4:frame,6:team,7:0,12:base_z};events=[];uc.mem_write(battle+0x64,bytes([int(pvp)]));uc.mem_write(position+0x24,struct.pack('<f',123.))
 run(0x1585f04,x0=system,x1=entity)
 unit={'board':dict(board),'offset':[0.,0.,123.]};expected=[]
 def get_hp(u):expected.append(('hp',u['offset'][2]));return hp
 def decide(u):expected.append(('decide',u['offset'][2]));return 3
 def defeated(u):expected.append(('defeated',u['offset'][2]))
 def change(u,n):expected.append(('state',n,u['offset'][2]))
 update_damaging(unit,get_hp,lambda u:human,lambda u:monster,lambda u:enemy,lambda:pvp,decide,defeated,change)
 assert offset()==unit['offset'][2] and events==expected,(board,hp,human,monster,enemy,pvp,offset(),unit,events,expected)
 checks+=1
rotations=0
for method,helper in ((0x1586954,update_knocking_down),(0x15870cc,None)):
 for frame,d in product((-2147483648,-1,0,1,20,21,22,100,101,2147483647),(-2147483648,-5,-1,0,1,2,3,4,2147483647)):
  board={4:frame};w32(direction+0x10,d);events=[];run(method,x0=system,x1=entity)
  unit={'board':dict(board),'direction':d};expected=[]
  if helper:helper(unit,lambda u,n:expected.append(('animation',n,offset())),lambda u,n:expected.append(('state',n,offset())))
  else:update_leaving(unit)
  assert events==expected and r32(direction+0x10)==unit['direction'],(method,frame,d,events,expected,r32(direction+0x10),unit)
  rotations+=1
result=dict(nativeDamageStateCases=checks,nativeKnockdownLeavingCases=rotations,scope='Original damage reaction including native parabola, recovery/death dispatch and enemy bookkeeping gate; original knockdown/leaving direction and timing',limitations=['Component/blackboard access, decisions, animations, bookkeeping and final ChangeState callbacks supplied'])
(EVIDENCE/'damage-state-checks.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8');print(json.dumps(result))
