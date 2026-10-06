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

from random import Random
from combat_geometry import battle_move_base
from combat_resolution import f32
uc.mem_map(0x750000,0x10000)
uc.mem_write(0x75359c,struct.pack('<f',0.1))
for flag in [0x316ab73,0x316e376]: uc.mem_write(flag,b'\x01')
w64(0x2f5b0f0,0x10001000);w64(0x10001000,0x10002000);w32(0x100020e0,1)
position,velocity,entity = 0x10004000,0x10005000,0x10006000
native_ranges=[(0x148c8d8,0x148ca6c),(0x23dd6a4,0x23dd700),(0x15ed5e8,0x15ed610),(0x145fc10,0x145fc18)]
def external(machine,address,size,user):
    if any(a<=address<b for a,b in native_ranges):return
    if address == 0x1471498: result=position
    elif address == 0x1472858: result=velocity
    else:raise AssertionError(hex(address))
    machine.reg_write(UC_ARM64_REG_X0,result)
    machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
def write_pair(base,pair):
    for offset,value in zip([0x10,0x18],pair):uc.mem_write(base+offset,struct.pack('<f',value))
def read_pair(base):return tuple(struct.unpack('<f',uc.mem_read(base+o,4))[0] for o in [0x10,0x18])
rng=Random(20260912)
cases=[((0.,0.),(x,z),4.46) for x,z in [(0,0),(4.46,0),(4.46001,0),(0,32),(32,0),(-32,0),(100,200)]]
cases += [(tuple(f32(rng.uniform(-1000,1000)) for _ in range(2)),tuple(f32(rng.uniform(-1000,1000)) for _ in range(2)),f32(rng.uniform(.1,100))) for _ in range(2000)]
for pos,target,speed in cases:
    write_pair(position,pos);write_pair(velocity,(17.,-19.))
    for register,value in zip(['S0','S1','S2'],[*target,speed]):
        uc.reg_write(globals()['UC_ARM64_REG_'+register],struct.unpack('<I',struct.pack('<f',value))[0])
    arrived=run(0x148c8d8,x0=0x10007000,x1=entity)
    actual=(bool(arrived),read_pair(position),read_pair(velocity))
    expected=battle_move_base(pos,target,speed,(17.,-19.))
    assert actual==expected,(pos,target,speed,actual,expected)
cell_checks=0
for x in [-2147483648,-49,-25,-24,-23,-1,0,1,23,24,25,49,2147483647]:
    for z in [-49,-24,-1,0,1,24,49]:
        for width,height in [(24,24),(16,48),(1,1)]:
            run(0x15ed5e8,w1=x,w2=z,w3=width,w4=height)
            expected_x=abs(x)//width*(-1 if x<0 else 1)
            expected_z=abs(z)//height*(-1 if z<0 else 1)
            assert reg('X0') == (expected_x & 0xffffffff)+((expected_z & 0xffffffff)<<32)
            cell_checks+=1
report=dict(checks=len(cases),cellConversionChecks=cell_checks,status='Original ARM64 MoveBase and FastMath.Sqrt agree bit-for-bit with portable finite-input movement primitive',limits=['Entity component access is stubbed.','Does not yet check MoveSystem integration, cell update order or complete fighter paths.','Default speed lookup, negative speed and nonfinite inputs are outside this port.'])
(EVIDENCE/'movement-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
print(json.dumps(report))
