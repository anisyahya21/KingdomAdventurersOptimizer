"""Native command timing and interruption primitives, with explicit callee stubs."""
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
from combat_states import decide_next_state
uc.mem_write(0x316b1ed,b'\x01')
for i,got in enumerate([0x2f60048,0x2f5fb60,0x2f5e4c0,0x2f5fd60,0x2f64ba0,0x2f64ba8]):
    ptr,cls=0x10001000+i*8,0x10002000+i*0x100
    w64(got,ptr);w64(ptr,cls);w32(cls+0xe0,1);w64(cls+0xb8,0x10003000)
# Direction offsets array: original cardinal order up/right/down/left.
w64(0x10003010,0x10003100);w32(0x10003118,4)
for i,y in enumerate([-1,0,1,0]):
    point=0x10003200+i*0x20
    w64(0x10003120+i*8,point);w32(point+0x14,y)
system,entity,ai,bb,cell,map_= [0x10004000+i*0x100 for i in range(6)]
w64(system+0x10,0x10005000);w64(ai+0x58,bb);w32(map_+0x20,24);w32(map_+0x24,24)
values={};calls=[];queue_position=None

def external(machine,address,size,user):
    if 0x15835a8<=address<0x1583b40:return
    if address==0x1468bc8:result=ai
    elif address==0x146caa4:result=cell
    elif address==0x1475b3c:result=map_
    elif address==0x1a6a108:result=values[reg('W1')]
    elif address==0x1a6a12c:values[reg('W1')]=i32(reg('W2'));result=0
    elif address==0x145f3f4:result=(reg('W0')+2)%4
    elif address in predicates:
        calls.append(address);result=int(predicates[address])
    elif address==0x1588078:
        uc.mem_write(reg('X8'),bytes([int(queue_position is not None)])+bytes(19));result=0
    elif address==0x1f22e44:result=struct.unpack('<Q',struct.pack('<2f',*queue_position))[0]
    else:raise AssertionError(hex(address))
    machine.reg_write(UC_ARM64_REG_X0,result&0xffffffffffffffff);machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
checks=0
for state,team,x,flags,queue_position in product(range(9),[0,1],[-1,0,4,5],product([False,True],repeat=5),[None,(48.75,-24.75)]):
    front,long_range,skill,same,advance=flags
    predicates=dict(zip([0x15884f8,0x158511c,0x1588b38,0x1588b88,0x1588cb8],flags))
    values={5:state,6:team,7:12};calls=[];w32(cell+0x10,x);w32(cell+0x14,7)
    actual=run(0x15835a8,x0=system,x1=entity)
    expected,destination=decide_next_state(state,team,(x,7),front,long_range,skill,same,advance,queue_position)
    assert actual==expected,(state,team,x,flags,queue_position,actual,expected)
    assert (tuple(values[k] for k in (13,14)) if 13 in values else None)==destination
    if state in (2,7,8):assert 0x1588b88 not in calls and 0x1588cb8 not in calls
    checks+=1
report=dict(decisionCases=checks,limits=['Original DecideNextState runs with component, spatial predicate and nullable queue-position stubs.','Checks branch precedence and destination writes, not predicate correctness or full fight outcomes.'])
(EVIDENCE/'state-decision-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
print(json.dumps(report))
