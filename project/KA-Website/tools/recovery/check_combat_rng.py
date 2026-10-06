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
    for a in [0x753504,0x753590,0x753470]:
        seg=next(s for s in elf.iter_segments() if s['p_type']=='PT_LOAD' and s['p_vaddr']<=a<s['p_vaddr']+s['p_filesz'])
        f.seek(seg['p_offset']+a-seg['p_vaddr']);uc.mem_write(a,f.read(8))
def w32(a,n):uc.mem_write(a,struct.pack('<I',n&0xffffffff))
def w64(a,n):uc.mem_write(a,struct.pack('<Q',n))
def run(a,**regs):
    uc.reg_write(UC_ARM64_REG_SP,0x20008000);uc.reg_write(UC_ARM64_REG_LR,0x30000000)
    for k,v in regs.items():uc.reg_write(globals()['UC_ARM64_REG_'+k.upper()],v&0xffffffffffffffff)
    uc.emu_start(a,0x30000000,count=20000)
    assert uc.reg_read(UC_ARM64_REG_PC)==0x30000000,hex(uc.reg_read(UC_ARM64_REG_PC))
    return i32(uc.reg_read(UC_ARM64_REG_W0))


from combat_resolution import SystemRandomState
uc.mem_write(0x316fa8f,b'\x01')
for got in [0x2f5b218,0x2f5b0f0]:w64(got,0x10001000)
w64(0x10001000,0x10001100);w32(0x100011e0,1)
obj,arr=0x10002000,0x10003000

def external(m,a,size,user):
    if 0x2660020<=a<0x26601c8 or 0x26601f0<=a<0x2660274:return
    if a==0x12d2214:
        assert m.reg_read(UC_ARM64_REG_W1)==56
        uc.mem_write(arr,bytes(256));w32(arr+0x18,56)
        m.reg_write(UC_ARM64_REG_X0,arr)
    elif a==0x2691aa0:pass
    else:raise AssertionError(hex(a))
    m.reg_write(UC_ARM64_REG_PC,m.reg_read(UC_ARM64_REG_LR))
hook=uc.hook_add(UC_HOOK_CODE,external)
rng=random.Random(20260912);seeds=[-2147483648,-2147483647,-161803399,-1,0,1,161803398,161803399,2147483647]
seeds += [rng.randrange(-2**31,2**31) for _ in range(41)]
for seed in seeds:
    expected=SystemRandomState(seed);run(0x2660020,x0=obj,w1=seed)
    assert list(struct.unpack('<56i',uc.mem_read(arr+0x20,224)))==expected.values,seed
    assert struct.unpack('<2i',uc.mem_read(obj+0x10,8))==(0,21)
    for _ in range(256):
        actual=run(0x26601f0,x0=obj)
        assert actual==expected.next_int(),(seed,actual)
        assert struct.unpack('<2i',uc.mem_read(obj+0x10,8))==(expected.index,expected.partner)
        assert list(struct.unpack('<56i',uc.mem_read(arr+0x20,224)))==expected.values
uc.hook_del(hook)
result=dict(status='pass',seedInitializations=len(seeds),nextIntAndFullState=len(seeds)*256,
            limits='Original System.Random constructor and InternalSample with allocation/Object constructor stubs; active runtime backend/state not captured.')
(EVIDENCE/'system-rng-checks.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result))
