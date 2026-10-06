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
from combat_initial_state import weaken_friend_parameter, effective_parameter_rate
uc.mem_write(0x316b00e,b'\x01')
uc.mem_write(0x316b882,b'\x01')
for i,got in enumerate([0x2f5b570,0x2f5b810,0x2f5b5e0,0x2f5d0d0]):
    ptr,cls=0x10001000+i*8,0x10002000+i*0x100
    w64(got,ptr);w64(ptr,cls);w32(cls+0xe0,1);w64(cls+0xb8,0x10003000)
parameter,ids=0x10004000,0x10005000
w32(ids+0x18,1);w32(ids+0x20,10)
native_ranges=[(0x1542c4c,0x1542eb4),(0x16825cc,0x1682610),(0x1682868,0x16828a4),(0x165e91c,0x165ea64)]
def external(machine,address,size,user):
    if any(a<=address<b for a,b in native_ranges):return
    if address==0x147e7e0:result=0
    elif address in [0x1471288,0x14ce638]:result=1
    elif address in [0x1471200,0x14c89d0]:result=parameter
    elif address==0x166070c:result=effective_value
    elif address==0x16609ec:result=effective_max
    elif address==0x16657ec:result=min(max(i32(reg("W0")),i32(reg("W1"))),i32(reg("W2")))
    elif address==0x1660630:
        assert reg('W0')==34;result=ids
    elif address==0x166107c:result=parameter
    elif address==0x16657b0:result=max(i32(reg('W0')),i32(reg('W1')))
    elif address==0x13eb3a0:result=min(max(i32(reg('W0')),i32(reg('W1'))),i32(reg('W2')))
    else:raise AssertionError(hex(address))
    machine.reg_write(UC_ARM64_REG_X0,result & 0xffffffffffffffff);machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
rng=Random(20260912)
cases=[(30000,30000,5000,5000),(30000,0x7fffffff,5000,5000),(1,1,0,-100),(0,0,0,0),(837278237,2147483647,36254,27613)]
cases += [(rng.randint(-2**31,2**31-1),rng.choice([0x7fffffff,rng.randint(-2**31,2**31-1)]),rng.randint(-50000,50000),rng.randint(-50000,50000)) for _ in range(1000)]
for values in cases:
    for offset,value in zip([0x14,0x18,0x1c,0x20],values):w32(parameter+offset,value)
    run(0x1542c4c,x0=0x10006000)
    actual=tuple(r32(parameter+o) for o in [0x14,0x18,0x1c,0x20])
    assert actual==weaken_friend_parameter(*values),(values,actual,weaken_friend_parameter(*values))
rate_checks=0
for effective_value in [-1,0,1,2,99,100,99999,21474836,21474837,2147483647]:
    for effective_max in [-100,0,1,100,10000,2147483647]:
        for bounded in [False,True]:
            w32(parameter+0x18,100 if bounded else 2147483647)
            got=i32(run(0x165e91c,x0=0x10006000,w1=10))
            expected=effective_parameter_rate(effective_value,effective_max,bounded)
            assert got==expected,(effective_value,effective_max,bounded,got,expected)
            rate_checks+=1
report=dict(checks=len(cases),parameterRateChecks=rate_checks,status='Original friend weakening plus native Parameter getters and Set',limits=['Group34 ID list and entity lookup are stubbed to one present parameter.','Illegal-friend eligibility and full party refill are separate paths.','Clamp helpers are explicit arithmetic stubs.'])
(EVIDENCE/'friend-weakening-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');print(json.dumps(report))
