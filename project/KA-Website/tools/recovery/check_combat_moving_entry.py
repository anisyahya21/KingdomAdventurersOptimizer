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

from combat_geometry import fighter_path
from combat_states import enter_moving
from combat_entities import FighterView
from itertools import product
from combat_resolution import f32
from random import Random
for flag in (0x316b1c4,0x316b1f3,0x316e662):uc.mem_write(flag,b'\x01')
for got in (0x2f5fb70,0x2f60048,0x2f619a8,0x2f64bc0,0x2f64bc8,0x2f64bd0,0x2f5e4a8,0x2f5e4b0,0x2f5e4b8):w64(got,0x10001000)
w64(0x10001000,0x10002000);w32(0x100020e0,1)
allocation=0;points=[]
system,entity,ai,bb,position,animation=[0x10010000+i*0x100 for i in range(6)]
w64(ai+0x58,bb)
board={};events=[]
ranges=[(0x1584194,0x1584310),(0x1584310,0x15845c8),(0x242d520,0x242d648),(0x24049bc,0x24049cc),(0x2404ee4,0x2404f24)]
def external(machine,address,size,user):
 global allocation,points
 if any(a<=address<b for a,b in ranges):return
 value=0
 if address==0x1468bc8:value=ai
 elif address==0x1471498:value=position
 elif address==0x146b9d8:value=animation
 elif address==0x1a6a108:value=board[reg('W1')]&0xffffffff
 elif address==0x165dea8:
  stored=struct.unpack('<Q',uc.mem_read(ai+0x68,8))[0]
  events.append((reg('W1'),r32(animation+0x10),list(points),stored!=0))
 elif address in (0x12d2214,0x12d23b8):
  allocation+=1;value=0x10004000+allocation*0x100;w64(value,0x10002000)
  if address==0x12d2214:w32(value+0x18,reg('W1'))
 elif address==0x2190f94:w32(reg('X0')+0x10,reg('W1'));w32(reg('X0')+0x14,reg('W2'))
 elif address==0x12d22a8:value=reg('X0')
 elif address==0x1f9f9c0:
  arr=reg('X1');refs=struct.unpack('<2Q',uc.mem_read(arr+0x20,16));points=[(r32(p+0x10),r32(p+0x14)) for p in refs]
 else:raise AssertionError(hex(address))
 machine.reg_write(UC_ARM64_REG_X0,value);machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
checks=0
for start,target,rate in product(((0.,0.),(23.75,-24.75),(-16777216.,16777216.),(96.,96.)),
                               ((0,0),(48,96),(-24,-96),(2147483647,-2147483648)),(-1,0,1,4)):
 allocation=0;points=[];events=[];board={13:target[0],14:target[1]}
 w64(ai+0x68,0);w32(animation+0x10,rate)
 uc.mem_write(position+0x10,struct.pack('<3f',start[0],99.,start[1]))
 components=[None]*52;components[0]=[start[0],99.,start[1],0.,0.,0.,None]
 components[12]=[rate,-1];components[28]={'board':dict(board),'path':[(999,999)]}
 unit=FighterView({'id':1,'flags':0,'components':components})
 run(0x1584194,x0=system,x1=entity)
 expected=[]
 enter_moving(unit,lambda u,m:expected.append((m,u['animation_rate'],list(u['path']),True)))
 assert events==expected and points==unit['path'],(start,target,events,expected)
 assert board==unit['board']
 checks+=1
result=dict(nativeMovingEntryCases=checks,scope='Original EnterMoving joined to GetPath, PointF and GetCrossLine versus component-backed state; animation callback checks path and rate writes',limitations=['Component/blackboard access, allocations, vector and queue construction, and animation callee supplied'])
(EVIDENCE/'moving-entry-checks.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8');print(json.dumps(result))
