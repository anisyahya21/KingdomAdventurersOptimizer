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
for flag in [0x316b480,0x316ad4f]: uc.mem_write(flag,b'\x01')
for i,got in enumerate([0x2f663b0,0x2f663b8,0x2f663c0,0x2f663c8,0x2f64be8,0x2f5efb8]):
    pointer,cls=0x10001000+i*8,0x10002000+i*0x100
    w64(got,pointer);w64(pointer,cls);w32(cls+0xe0,1);w64(cls+0xb8,0x10003000)
w64(0x10003000,0);w64(0x10003008,0)
closure,entity,component,skill,levels=0x10004000,0x10004100,0x10004200,0x10004300,0x10004400
w64(closure+0x10,entity);w32(component+0x10,4);w64(component+0x20,levels)
invocation_levels=[2,0,1,2]
native_ranges=[(0x15e0498,0x15e061c),(0x15e23b0,0x15e2410),(0x14d09d4,0x14d0a50),(0x1686390,0x1686398)]
index,attempts,outcomes=0,[],[]
def external(machine,address,size,user):
    global index
    if any(a<=address<b for a,b in native_ranges):return
    if address==0x14724cc:result=component
    elif address==0x1dc6274:result=invocation_levels[reg('W1')]
    elif address==0x1e12d6c:
        uc.mem_write(reg('X8'),bytes(32));index=0;result=0
    elif address==0x1bd2238:
        result=int(index<len(outcomes))
        if result:
            w64(reg('X0')+0x10,100+index);w64(reg('X0')+0x18,200+index);index+=1
    elif address==0x1bd2234:result=0
    elif address==0x15e03d8:
        attempts.append(reg('X0'));result=outcomes[index-1]
    else:raise AssertionError(hex(address))
    machine.reg_write(UC_ARM64_REG_X0,result & 0xffffffffffffffff)
    machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
counts=dict(invocationIndex=0,firstSuccessfulCandidate=0)
for selected_skill in [3,27,105,135]:
    w32(skill+0x18,selected_skill)
    for ordinal in range(4):
        run(0x15e23b0,x0=closure,x1=skill,w2=ordinal)
        assert reg('X0') == selected_skill+(invocation_levels[ordinal]<<32)
        counts['invocationIndex']+=1
for length in range(9):
    for outcomes in product([False,True],repeat=length):
        attempts=[];run(0x15e0498,x0=0x10005000)
        first=next((i for i,v in enumerate(outcomes) if v),None)
        expected=(0,0) if first is None else (100+first,200+first)
        assert (reg('X0'),reg('X1'))==expected
        attempted=length if first is None else first+1
        assert attempts==list(range(200,200+attempted))
        counts['firstSuccessfulCandidate']+=1
report=dict(checks=counts,status='Isolated original native selection methods',limits=['LINQ/list enumeration and activation decisions are stubbed.','Candidate construction order/filtering is statically traced, not exercised end-to-end here.','Invocation-index test certifies callback/getter behavior; filtered-index origin follows the native Select-after-Where chain.'])
(EVIDENCE/'selection-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
print(json.dumps(report))
