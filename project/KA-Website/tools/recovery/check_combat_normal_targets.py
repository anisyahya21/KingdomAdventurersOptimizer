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
from combat_states import is_most_front,is_normal_attack_target
for flag in [0x316b1e8,0x316b1e6]:uc.mem_write(flag,b'\x01')
for i,got in enumerate([0x2f60048,0x2f5fd60]):
    ptr,cls=0x10001000+i*8,0x10002000+i*0x100
    w64(got,ptr);w64(ptr,cls);w32(cls+0xe0,1)
entity,ai,bb,params=0x10004000,0x10004100,0x10004200,0x10004300
w64(ai+0x58,bb)
def external(machine,address,size,user):
    if 0x1588554<=address<0x158868c or 0x15884f8<=address<0x1588554:return
    if address==0x1477df0:result=exists
    elif address==0x1471200:result=params
    elif address==0x14ce670:result=hp
    elif address==0x1468bc8:result=ai
    elif address==0x1a6a108:result=state
    else:raise AssertionError(hex(address))
    machine.reg_write(UC_ARM64_REG_X0,int(result)&0xffffffffffffffff);machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
checks=0
for exists,hp,state in product([False,True],[-1,0,1,2147483647],range(-1,10)):
    assert bool(run(0x1588554,x0=entity))==is_normal_attack_target(exists,hp,state)
    checks+=1
front_checks=0
for grid in list(range(-100,101))+[-2147483648,2147483647,2147483644]:
    assert bool(run(0x15884f8,w0=grid))==is_most_front(grid)
    front_checks+=1
report=dict(normalTargetChecks=checks,frontRowChecks=front_checks,limits=['Original state/HP filter with stubbed entity existence and component access.','Negative front-grid acceptance is native unsigned arithmetic, not evidence these grids occur in normal formation.'])
(EVIDENCE/'normal-target-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');print(json.dumps(report))
