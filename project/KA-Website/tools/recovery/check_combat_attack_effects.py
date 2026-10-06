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
from combat_effects import attack_effect_specs
uc.mem_write(0x316ba07,b'\x01')
attacker,target,result_,position,world=0x10004000,0x10004100,0x10004200,0x10004300,0x10004400
w64(target+0x30,world);uc.mem_write(position+0x10,struct.pack('<3f',24.,5.,48.))
w64(0x2f5fb20,0x10005000);w64(0x10005000,0x10006000);w32(0x100060e0,1);w64(0x100060b8,0x10007000);w32(0x10007020,9)

def external(machine,address,size,user):
    if 0x168f480<=address<0x168f820 or 0x168e800<=address<0x168e8c8:return
    result=0
    if address==0x12d21a0:pass
    elif address==0x147e7e0:result=destroyed
    elif address==0x1471498:result=position
    elif address==0x146b824:result=ally
    elif address==0x146fd60:result=0
    elif address==0x1470c84:result=monster
    elif address==0x14709ac:result=modified
    elif address==0x1470b54:events.append(('remove_modifier',))
    elif address==0x14709bc:events.append(('add_modifier',reg('W1'),reg('W4')))
    elif address==0x1479108:
        events.append(('impact_effect',reg('W1'),reg('W3')))
        effect_specs.append(dict(type=0,value1=0,value2=0,res=28,seb=0,depth=True,max_frame=0,frame=0,
            loop=False,parent=None,image=reg('W1'),animate=True,scale=reg('W3'),position=floats()))
    elif address==0x1478ea0:
        events.append(('effect',reg('W1'),reg('W2'),reg('W3'),reg('W4'),reg('W7')))
        sp=reg('SP')
        effect_specs.append(dict(type=reg('W1'),value1=reg('W2'),value2=r32(sp+32),res=reg('W3'),seb=reg('W4'),
            depth=bool(reg('W5')),max_frame=reg('W7'),frame=r32(sp),loop=bool(reg('W6')),parent=None,
            image=r32(sp+16),animate=bool(r32(sp+24)),scale=r32(sp+40),position=floats()))
    else:raise AssertionError(hex(address))
    machine.reg_write(UC_ARM64_REG_X0,int(result));machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
def floats():return tuple(struct.unpack('<f',struct.pack('<I',reg('S'+str(i))))[0] for i in range(3))
checks=0
for destroyed,hit,critical,ally,monster,modified in product((False,True),repeat=6):
    uc.mem_write(result_,struct.pack('<QQ??2xiQ',attacker,target,hit,critical,101,0));events=[];effect_specs=[]
    run(0x168f480,x0=result_)
    expected=[]
    if not destroyed:
        if not hit:expected=[('effect',12,0,6,126,30)]
        else:
            expected=[('impact_effect',1 if critical else 2,150 if critical else 100)]
            if critical:expected.append(('effect',12,9,6,126,30))
            expected.append(('effect',1,101,4,(237 if critical else 78) if ally else 112,30))
            if monster:
                if modified:expected.append(('remove_modifier',))
                expected.append(('add_modifier',4,6))
    assert events==expected,(destroyed,hit,critical,ally,monster,modified,events,expected)
    assert effect_specs==([] if destroyed else attack_effect_specs(dict(hit=hit,critical=critical,damage=101),(24.,5.,48.),ally))
    checks+=1
report=dict(nativeAttackEffectDispatchCases=checks,scope='Original ProcessAttackEffect and CreateAttackStrEffect; normal Human/Monster targets; effect allocation/component helpers recorded',
            findings=['Miss creates one text effect; hit two effect calls; critical hit three','Monster hit replaces ModifyAnimation with type4 duration6','No direct random helper called on these paths'],
            limitations=['CreateEffect allocation/resource duration/component propagation and modifier update are not executed here; MapChip target excluded'])
(EVIDENCE/'attack-effect-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');print(json.dumps(report))
