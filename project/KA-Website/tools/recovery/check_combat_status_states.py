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

from combat_skills import update_status
from itertools import product
uc.mem_write(0x316b477,b'\x01')
for got in (0x2f5faf8,0x2f5fae8,0x2f5fe38,0x2f60048,0x2f5fb60,0x2f5fcf0,0x2f5fae0):w64(got,0x10005000)
system,subset,ai,board,entity=0x10006000,0x10006100,0x10006200,0x10006300,0x10006500
w64(system+0x20,subset);w64(subset+0x30,0x10006400);w64(ai+0x58,board)
enumerated=False

def external(machine,address,size,user):
    global enumerated
    if 0x15ddc04<=address<0x15ddf58:return
    result=0
    if address==0x1cd2110:enumerated=False;machine.mem_write(reg('X8'),bytes(24))
    elif address==0x1bcf0a8:
        result=int(not enumerated)
        if result:w64(reg('X0')+0x10,entity)
        enumerated=True
    elif address==0x1bcf0a4:pass
    elif address==0x146b510:result=has_ai
    elif address==0x1468bc8:result=ai
    elif address==0x1a6a204:result=reg('W1') in values
    elif address==0x1a6a108:result=values[reg('W1')]
    elif address==0x1a6a12c:values[reg('W1')]=i32(reg('W2'))
    elif address==0x1a6a2b8:values.pop(reg('W1'),None)
    else:raise AssertionError(hex(address))
    machine.reg_write(UC_ARM64_REG_X0,int(result)&0xffffffffffffffff);machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
checks=0
for has_ai,mask,duration,counter in product((False,True),range(8),(-2147483648,0,1,3),(-2147483648,-21,-20,-1,0,18,19,20,39,2147483647)):
    original={62:100,63:duration,64:counter}
    values={key:value for i,(key,value) in enumerate(original.items()) if mask&(1<<i)}
    expected=dict(values);run(0x15ddc04,x0=system)
    update_status(expected,has_ai)
    assert values==expected,(has_ai,mask,duration,counter,values,expected)
    checks+=1
report=dict(nativeStatusStateChecks=checks,scope='Original SkillSystem.Update with one supplied subset member; missing keys, AI gate, signed-counter and duration boundaries',
            limitations=['Subset enumeration and blackboard operations stubbed; no full world loop'])
(EVIDENCE/'status-state-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');print(json.dumps(report))
