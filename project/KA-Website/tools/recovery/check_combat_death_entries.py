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

from combat_states import enter_knocking_down,enter_leaving_special
from itertools import product
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
battle,teams,leader=0x10015000,0x10016000,0x10017000
w64(system+0x38,teams);w32(teams+0x18,2);w64(teams+0x30,0x10018000);w64(teams+0x48,0x10018000)
w64(0x10002100,0x10019000);w32(0x10002108,100);w64(entity+0x28,1234)
uc.mem_write(battle+0x64,b'\x00\x01')
for flag in (0x316b1d2,0x316b1d4,0x316b1fd):uc.mem_write(flag,b'\x01')
for got in (0x2f5fb70,0x2f5f980,0x2f5d708,0x2f5d5b8,0x2f5b960,0x2f64c28,0x2f64c20,0x2f5e538,0x2f64bb8):w64(got,0x10001000)
ranges=[(0x1586444,0x1586944),(0x1586a80,0x1587088),(0x1586354,0x1586444),(0x15882c8,0x158840c),(0x1583550,0x1583578),(0x158357c,0x15835a4),(0x1589930,0x15899b8)]
board={};events=[];commands=[];allocation=0

def floats(n):return tuple(struct.unpack('<f',struct.pack('<I',reg('S'+str(i))))[0] for i in range(n))
def snapshot():return board[7],tuple(commands)
def external(machine,address,size,user):
 global allocation
 if any(a<=address<b for a,b in ranges):return
 result=0
 if address==0x1468bc8:result=ai
 elif address==0x1a6a108:result=board[reg('W1')]
 elif address==0x1a6a12c:board[reg('W1')]=i32(reg('W2'))
 elif address==0x1471498:result=position
 elif address==0x1475b3c:result=map_obj
 elif address==0x15833c4:result=row_offset
 elif address==0x12d23b8:allocation+=1;result=0x10020000+allocation*0x100
 elif address in (0x2691aa0,0x1cb5fc8):pass
 elif address==0x188e34c:result=other_down
 elif address==0x14607ec:uc.mem_write(reg('X0'),struct.pack('<3f',*floats(3)))
 elif address==0x14c3f98:events.append(('clear',snapshot()));commands.clear()
 elif address==0x14832d0:events.append(('projectile',reg('W1'),floats(6),snapshot()))
 elif address==0x165dea8:events.append(('animation',reg('W1'),snapshot()))
 elif address==0x1479108:events.append(('smoke',reg('W1'),floats(3),snapshot()))
 elif address==0x144cdc8:events.append(('sound_roll',reg('W0')));result=sound
 elif address==0x16655c8:events.append(('play_sound',reg('W1')))
 elif address==0x191de04:result=battle
 elif address==0x1470c84:result=monster
 elif address==0x1582048:uc.mem_write(reg('X8'),struct.pack('<3Q',0,leader,0))
 elif address==0x14ed618:events.append(('prize',snapshot()))
 else:raise AssertionError(hex(address))
 machine.reg_write(UC_ARM64_REG_X0,int(result)&0xffffffffffffffff);machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
checks=0
for method in (0x1586444,0x1586a80):
 for team,row_offset,grid,other_down,monster,is_leader,sound in product((0,1),(3,8),(-1,0,6),(0,1,3),(False,True),(False,True),(False,True)):
  board={5:7,6:team,7:grid};commands=[29,29];events=[];allocation=0
  w64(leader+0x28,1234 if is_leader else 1235);w32(map_obj+0x20,24);w32(map_obj+0x24,24)
  start=(24.5,0.,96.25);uc.mem_write(position+0x10,struct.pack('<3f',*start))
  unit={'board':dict(board),'position':list(start),'commands':list(commands)}
  run(method,x0=system,x1=entity)
  expected=[]
  def snap():return unit['board'][7],tuple(unit['commands'])
  def clear(u):expected.append(('clear',snap()));u['commands'].clear()
  def fire(u,s,a,b):expected.append(('projectile',s,tuple(a)+tuple(b),snap()))
  def anim(u,n):expected.append(('animation',n,snap()))
  def smoke(u):expected.append(('smoke',6,(u['position'][0],u['position'][1]+10.,u['position'][2]),snap()))
  def roll(n):
   expected.append(('sound_roll',100))
   if sound:expected.append(('play_sound',n))
  if method==0x1586444:
   teammates=[unit]+[{'board':{5:7}} for _ in range(other_down)]+[{'board':{5:8}}]
   enter_knocking_down(unit,teammates,row_offset,clear,fire,anim,smoke,roll)
  else:enter_leaving_special(unit,row_offset,lambda u:monster,lambda u:is_leader,fire,smoke,lambda:expected.append(('prize',snap())),roll)
  assert board==unit['board'] and commands==unit['commands'] and events==expected,(method,board,unit,events,expected)
  checks+=1
predicate_checks=0
w64(0x10020010,entity)
for same,state in product((False,True),(-1,0,1,6,7,8,9)):
 board={5:state}
 assert run(0x1589930,x0=0x10020000,x1=entity if same else entity+0x100)==int(not same and state==7)
 predicate_checks+=1
result=dict(nativeDeathEntryCases=checks,nativeSidePlacementPredicateChecks=predicate_checks,scope='Original EnterKnockingDown and non-PvP guerrilla EnterLeaving with native grid conversions; callback ordering, retained/cleared commands, side placement, leader prize and Lib sound draw',limitations=['Teammate count supplied with predicate checked separately; component access, projectile/effect creation, animations and prize calculation supplied; not complete post-death world'])
(EVIDENCE/'death-entry-checks.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8');print(json.dumps(result))
