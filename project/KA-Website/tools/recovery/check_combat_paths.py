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

from combat_geometry import fighter_path
from combat_resolution import f32
from random import Random
for flag in (0x316b1f3,0x316e662):uc.mem_write(flag,b'\x01')
for got in (0x2f619a8,0x2f64bc0,0x2f64bc8,0x2f64bd0,0x2f5e4a8,0x2f5e4b0,0x2f5e4b8):w64(got,0x10001000)
w64(0x10001000,0x10002000);w32(0x100020e0,1)
allocation=0;points=[]
ranges=[(0x1584310,0x15845c8),(0x242d520,0x242d648),(0x24049bc,0x24049cc),(0x2404ee4,0x2404f24)]
def external(machine,address,size,user):
 global allocation,points
 if any(a<=address<b for a,b in ranges):return
 value=0
 if address in (0x12d2214,0x12d23b8):
  allocation+=1;value=0x10004000+allocation*0x100;w64(value,0x10002000)
  if address==0x12d2214:w32(value+0x18,reg('W1'))
 elif address==0x2190f94:w32(reg('X0')+0x10,reg('W1'));w32(reg('X0')+0x14,reg('W2'))
 elif address==0x12d22a8:value=reg('X0')
 elif address==0x1f9f9c0:
  arr=reg('X1');refs=struct.unpack('<2Q',uc.mem_read(arr+0x20,16));points=[(r32(p+0x10),r32(p+0x14)) for p in refs]
 else:raise AssertionError(hex(address))
 machine.reg_write(UC_ARM64_REG_X0,value);machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
rng=Random(20260912)
cases=[(tuple(f32(rng.uniform(-1000,1000)) for _ in range(2)),tuple(f32(rng.uniform(-1000,1000)) for _ in range(2))) for _ in range(1000)]
cases += [((a,b),(b,a)) for a in (-2147483648.,-16777216.,-24.,0.,24.,16777216.,2147483647.) for b in (-24.75,0.,96.,16777216.)]
for start,target in cases:
 allocation=0;points=[]
 packed=lambda p:struct.unpack('<Q',struct.pack('<2f',*p))[0]
 run(0x1584310,x1=packed(start),x2=0,x3=packed(target),x4=0)
 expected=fighter_path(start,target)
 assert points==expected,(start,target,points,expected)
result=dict(nativePathCases=len(cases),scope='Original GetPath, PointF arithmetic and GetCrossLine with full float32 intersection and integer conversion',limitations=['Arrays, vector allocation and queue construction supplied; not full moving state'])
(EVIDENCE/'path-checks.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8');print(json.dumps(result))
