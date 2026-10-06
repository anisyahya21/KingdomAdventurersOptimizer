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
from combat_resolution import resolve_attack
uc.mem_write(0x316b9fd,b'\x01')
# This no-skill-component path only dereferences the AttackResult method metadata.
w64(0x2f60040,0x10001000)
attacker,target,skill,result_=0x10004000,0x10004100,0x10004200,0x10004300
calls=[]
def external(machine,address,size,user):
    if 0x168d1e8<=address<0x168df74:return
    result=0
    if address==0x147e7e0:result=destroyed
    elif address==0x168cc54:calls.append('critical');result=critical
    elif address==0x168cde4:calls.append('hit');result=hit
    elif address==0x168cf80:calls.append(('damage',reg('W0'),reg('W3')));result=777
    elif address==0x168e214:result=0
    elif address==0x1472554:result=0
    elif address==0x22141b8:
        uc.mem_write(reg('X0'),struct.pack('<QQ??2xiQ',reg('X1'),reg('X2'),bool(reg('W3')),bool(reg('W4')),i32(reg('W5')),reg('X6')))
    else:raise AssertionError(hex(address))
    machine.reg_write(UC_ARM64_REG_X0,int(result)&0xffffffffffffffff);machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
checks=0
for destroyed,critical,hit,typ,value in product([False,True],[False,True],[False,True],[0,1,11,19,26,27],[0,90,150,240]):
    calls=[];w32(skill+0x2c,typ);w32(skill+0x30,value)
    run(0x168d1e8,x0=attacker,x1=target,x2=skill,x8=result_)
    actual=struct.unpack('<QQ??2xiQ',uc.mem_read(result_,32))
    model_calls=[]
    def crit(a):model_calls.append('critical');return critical
    def hits(a,t):model_calls.append('hit');return hit
    def damage(c,a,t,magic):model_calls.append(('damage',int(c),int(magic)));return 777
    resolved=resolve_attack(attacker,target,dict(type=typ,value=value),lambda t:not destroyed,
                            crit,hits,damage,lambda a:False)
    expected=(attacker,target,resolved['hit'],resolved['critical'],resolved['damage'],skill)
    assert actual==expected,(destroyed,critical,hit,typ,value,actual,expected)
    assert calls==model_calls,(calls,model_calls)
    checks+=1
report=dict(noReactionAttackCases=checks,limits=['Executes entire original Attack with neither fighter having a SkillComponent, and no terrain match.','Critical/hit/damage helpers are stubbed; test proves dispatch/result preservation and absence of incoming skill.value scaling in this path.','Incoming skill pointer is present independently of possessed SkillComponent.'])
(EVIDENCE/'attack-tail-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');print(json.dumps(report))
