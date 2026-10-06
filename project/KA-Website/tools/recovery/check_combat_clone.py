"""Original parameter clone serialization boundaries; stream primitives stubbed."""
import json
import random
import struct
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE
from unicorn.arm64_const import *
from elftools.elf.elffile import ELFFile
from combat_initial_state import EVIDENCE, i32
from combat_resolution import skill_mp_cost, buff_accuracy, buff_turns

uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
for base, size in [(0x1000000,0x2300000),(0x10000000,0x10000),(0x20000000,0x10000),(0x30000000,0x1000),(0x750000,0x10000)]:
    uc.mem_map(base,size)
binary = EVIDENCE.parent / 'G2.1/39257e72291d/inputs/libil2cpp.so'
raw = binary.read_bytes()
for entry in json.loads((EVIDENCE/'audit.json').read_text())['methods']:
    a,b,o = [int(entry[k],16) for k in ['rva','end','offset']]
    uc.mem_write(a,raw[o:o+b-a])
with binary.open('rb') as f:
    elf=ELFFile(f)
    for a in [0x753504,0x753590]:
        seg=next(s for s in elf.iter_segments() if s['p_type']=='PT_LOAD' and s['p_vaddr']<=a<s['p_vaddr']+s['p_filesz'])
        f.seek(seg['p_offset']+a-seg['p_vaddr']);uc.mem_write(a,f.read(4))
def w32(a,n):uc.mem_write(a,struct.pack('<I',n&0xffffffff))
def w64(a,n):uc.mem_write(a,struct.pack('<Q',n))
def run(a,**regs):
    uc.reg_write(UC_ARM64_REG_SP,0x20008000);uc.reg_write(UC_ARM64_REG_LR,0x30000000)
    for k,v in regs.items():uc.reg_write(globals()['UC_ARM64_REG_'+k.upper()],v&0xffffffffffffffff)
    uc.emu_start(a,0x30000000,count=4000)
    assert uc.reg_read(UC_ARM64_REG_PC)==0x30000000,hex(uc.reg_read(UC_ARM64_REG_PC))
    return i32(uc.reg_read(UC_ARM64_REG_W0))

for flag in [0x316b960,0x316b961]:uc.mem_write(flag,b'\x01')
w64(0x2f5b828,0x10001000);w64(0x10001000,0x10001100);w32(0x100011e0,1)
source,dest,stream=0x10002000,0x10003000,0x10004000
wire=[];cursor=0
widths=[1,4,4,4,4,2,2,4,4,4]
def external(m,a,size,user):
    global cursor
    if 0x1682a9c<=a<0x1682ccc:return
    writers={0x23ae20c:1,0x23ae24c:2,0x23a45f8:4}
    readers={0x23ac6e4:1,0x23ac7cc:2,0x23a37f8:4}
    if a in writers:
        width=writers[a];wire.append((width,m.reg_read(UC_ARM64_REG_W1)&((1<<(width*8))-1)))
    elif a in readers:
        width,value=wire[cursor];cursor+=1;assert width==readers[a]
        m.reg_write(UC_ARM64_REG_W0,value)
    else:raise AssertionError(hex(a))
    m.reg_write(UC_ARM64_REG_PC,m.reg_read(UC_ARM64_REG_LR))
hook=uc.hook_add(UC_HOOK_CODE,external)
# Literal exp-clamp boundaries for 0x1682c98-0x1682cbc, written out independently of the formula
# below: `maxExp == INT_MAX` skips the clamp, otherwise a negative `exp` becomes 0 *before* the
# `exp > maxExp` test (so a negative `maxExp` still leaves 0), and `exp == maxExp` is untouched.
boundaries=[(-1,-5,0),(0,-5,-5),(1,-5,-5),(-2**31,5,0),(5,5,5),(6,5,5),(150,100,100),(-5,0x7fffffff,-5),
            (150,0x7fffffff,150),(0x7fffffff,0x7fffffff,0x7fffffff),(100,100,100),(101,100,100),
            (-1,0x7fffffff,-1)]
rng=random.Random(20260912)
cases=0;observed=[]
for index in range(1000+len(boundaries)):
    literal=None
    values=[rng.randrange(-2**31,2**31) for _ in widths]
    if index>=1000:
        values[7],values[8],literal=boundaries[index-1000]
    elif cases%2==0:values[8]=0x7fffffff
    wire.clear();cursor=0
    for i,value in enumerate(values):w32(source+0x10+4*i,value)
    run(0x1682a9c,x0=source,x1=stream)
    assert [w for w,v in wire]==widths
    assert [v for w,v in wire]==[v&((1<<(8*w))-1) for w,v in zip(widths,values)]
    uc.mem_write(dest+0x10,bytes(40));run(0x1682b98,x0=dest,x1=stream)
    expected=[]
    for width,value in wire:
        expected.append(value-(1<<(width*8)) if value&(1<<(width*8-1)) else value)
    if expected[8]!=0x7fffffff:
        expected[7]=0 if expected[7]<0 else min(expected[7],expected[8])
    actual=list(struct.unpack('<10i',uc.mem_read(dest+0x10,40)))
    assert actual==expected,(values,actual,expected)
    if literal is not None:
        assert actual[7]==literal,(values[7],values[8],actual[7],literal)
        observed.append(dict(exp=values[7],maxExp=values[8],nativeExp=actual[7]))
    assert bytes(uc.mem_read(source+0x10,40))==struct.pack('<10i',*values)
    cases+=1
uc.hook_del(hook)
result=dict(status='pass',parameterRoundTrips=cases,randomRoundTrips=1000,
            boundaryRoundTrips=len(boundaries),serializedWidths=widths,expClampBoundaries=observed,
            limits='Original Serialize/Deserialize instructions, synthetic typed stream. Full BaseEntity cloning and component callbacks not executed.')
(EVIDENCE/'clone-checks.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result))
