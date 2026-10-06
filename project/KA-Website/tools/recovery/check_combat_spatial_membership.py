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

from itertools import product
system,world,filter_obj,klass,entry,entity=0x10001000,0x10002000,0x10003000,0x10004000,0x10005000,0x10006000
w64(filter_obj,klass);w64(entry,0x148058c);w64(entity,klass)
w64(klass+0x1d8,0x30000100);w64(klass+0x1e8,0x30000200)
for flag in (0x316b3b1,0x316addc,0x316aed1,0x316b3fc,0x316b390,0x316b25f,0x316b469,0x316ab59):uc.mem_write(flag,b'\x01')
for got in (0x2f650a0,0x2f662d0,0x2f5d228,0x2f5d230,0x2f5f908,0x2f5b218,0x2f5fa58,0x2f62400,0x2f62408,0x2f5f980,0x2f61678):w64(got,0x10007000)
w64(0x10007000,klass);w32(klass+0xe0,1);w64(klass+0xb8,0x10007100);w64(0x10007100,0x10007200)
constants=json.loads((EVIDENCE/'skill-combat-constants.json').read_text(encoding='utf-8'))
array_initializers=[]
arrays={};allocation=0;components=set();captured=None
ranges=[(0x15cebf4,0x15cecd8),(0x15bc280,0x15bc354),(0x1595a30,0x1595b98),(0x15dc464,0x15dc508),(0x15bfb58,0x15bfbf0),(0x15017f4,0x15018e8),(0x14dc718,0x14dc880),(0x148058c,0x1480660)]
def read_array(a):return arrays[a] if a else []
def external(machine,address,size,user):
    global allocation,captured
    if any(a<=address<b for a,b in ranges):return
    if address==0x12d2214:
        allocation+=1;value=0x10008000+allocation*0x1000;uc.mem_write(value,bytes(0x1000));w32(value+0x18,reg('W1'));arrays[value]=[]
    elif address==0x14759b8:
        a=reg('X0');arrays[a]=[r32(a+0x20+i*4) for i in range(r32(a+0x18))];w64(filter_obj+0x10,a);value=filter_obj
    elif address==0x1332890:
        assert reg('W2')==2;value=entry
    elif address==0x1475a1c:
        a=struct.unpack('<Q',uc.mem_read(filter_obj+0x20,8))[0]
        if a:arrays[a]=[r32(a+0x20+i*4) for i in range(r32(a+0x18))]
        captured=(read_array(struct.unpack('<Q',uc.mem_read(filter_obj+0x10,8))[0]),read_array(a));value=0x1000e000
    elif address==0x12d23b8:value=0x1000f000
    elif address==0x2576218:
        a=reg('X0');values=array_initializers.pop(0)
        for i,v in enumerate(values):w32(a+0x20+i*4,v)
        value=0
    elif address in (0x1deb4a0,0x1e92d40,0x1663ca0):value=0
    elif address==0x1475b3c:value=0x10007300
    elif address==0x18c4e74:value=not read_array(reg('X0'))
    elif address==0x30000100:value=all(t in components for t in read_array(reg('X1')))
    elif address==0x30000200:value=any(t in components for t in read_array(reg('X1')))
    else:raise AssertionError(hex(address))
    machine.reg_write(UC_ARM64_REG_X0,int(value));machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
checks=0;filters={}
for name,init,expected in [('move',0x15bfb58,([0,1],[])),('cell',0x15017f4,([5,0,1],[])),('animation',0x14dc718,([2,12],[])),('projectile',0x15cebf4,([39,0,1],[])),('modifier',0x15bc280,([19],[])),('height',0x1595a30,([0,5,1],[11,39,38])),('rotate',0x15dc464,([14,1,2,7],[]))]:
    for off in (0x10,0x18,0x20):w64(filter_obj+off,0)
    array_initializers=([constants['heightExcludedComponents']['values']] if name=='height' else [constants['rotateRequiredComponents']['values']] if name=='rotate' else [])
    run(init,x0=system,x1=world);assert captured==expected,(name,captured)
    filters[name]={'and':captured[0],'nor':captured[1]}
    relevant=tuple(dict.fromkeys(expected[0]+expected[1]))
    for present in product((False,True),repeat=len(relevant)):
        components={t for t,b in zip(relevant,present) if b}
        actual=run(0x1480594,x0=filter_obj,x1=entity)
        wanted=all(t in components for t in expected[0]) and not any(t in components for t in expected[1])
        assert actual==wanted,(name,components,actual);checks+=1
result=dict(nativeSystemInitializations=7,nativeMembershipChecks=checks,filters=filters,limitations=['Array allocation, Filter.And construction, entity Contains, pending list construction and resource-manager lookup supplied; native Init and Matches control flow executed'])
(EVIDENCE/'spatial-animation-membership-checks.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8');print(json.dumps(result))

