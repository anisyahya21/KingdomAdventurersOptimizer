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

from combat_resolution import attack_interval
from math import pow
from elftools.elf.elffile import ELFFile
uc.mem_write(0x316b9f0,b'\x01')
for i,got in enumerate((0x2f5b570,0x2f5b0f0)):
    ptr,cls=0x10001000+i*8,0x10002000+i*0x100
    w64(got,ptr);w64(ptr,cls);w32(cls+0xe0,1)
uc.mem_map(0x752000,0x1000)
with (EVIDENCE.parent/'G2.1/39257e72291d/inputs/libil2cpp.so').open('rb') as f:
    elf=ELFFile(f);addr=0x752bf0
    seg=next(s for s in elf.iter_segments() if s['p_type']=='PT_LOAD' and s['p_vaddr']<=addr<s['p_vaddr']+s['p_filesz'])
    f.seek(seg['p_offset']+addr-seg['p_vaddr']);raw=f.read(8);uc.mem_write(addr,raw)
    assert struct.unpack('<d',raw)[0]==0.362

def external(machine,address,size,user):
    if 0x168c380<=address<0x168c460:return
    if address==0x16657ec:machine.reg_write(UC_ARM64_REG_W0,max(i32(reg('W1')),min(i32(reg('W2')),i32(reg('W0')))))
    elif address==0x2651004:
        x,y=[struct.unpack('<d',struct.pack('<Q',reg('D'+str(i))))[0] for i in range(2)]
        machine.reg_write(UC_ARM64_REG_D0,struct.unpack('<Q',struct.pack('<d',pow(x,y)))[0])
    else:raise AssertionError(hex(address))
    machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
checks=0;closest=(1.0,None,None)
for agility in [-2147483648,-100,-1]+list(range(100000))+[100000,2147483647]:
    actual=i32(run(0x168c380,w0=agility));expected=attack_interval(agility)
    assert actual==expected,(agility,actual,expected)
    if 0<agility<100000:
        value=(1.0/(pow(agility/25.0,0.362)/1.5))*20.0
        distance=abs(value-round(value))
        if distance and distance<closest[0]:closest=(distance,agility,value)
    checks+=1
report=dict(nativeAttackIntervalCases=checks,closestNonExactIntegerBoundary=dict(distance=closest[0],agility=closest[1],untruncatedInterval=closest[2]),
            scope='Original interval arithmetic/conversion for every clamped AGI value; Clamp and System.Math.Pow stubbed with host double pow',
            limitations=['Android libm pow is an imported symbol; its numerical implementation and active device library remain uncaptured'])
(EVIDENCE/'attack-interval-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');print(json.dumps(report))
