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

import random
for flag in (0x316ad26,0x316ad27):uc.mem_write(flag,b'\x01')
w64(0x2f5b828,0x10001000);w64(0x10001000,0x10002000);w32(0x100020e0,1)
source,dest,stream=0x10004000,0x10005000,0x10006000
wire=[];cursor=0
writers={0x23ae20c:('int',1),0x23ae24c:('int',2),0x23ae49c:('float',4),0x23ae47c:('bool',1)}
readers={0x23ac6e4:('int',1),0x23ac7cc:('int',2),0x23acb04:('float',4),0x23aca88:('bool',1)}

def external(machine,address,size,user):
    global cursor
    if 0x14cd8f4<=address<0x14cdb8c:return
    if address in (0x147ddfc,0x147de00):pass
    elif address in writers:
        typ,width=writers[address];value=reg('S0') if typ=='float' else reg('W1')
        wire.append((typ,width,value&((1<<(width*8))-1)))
    elif address in readers:
        typ,width,value=wire[cursor];cursor+=1;assert (typ,width)==readers[address]
        machine.reg_write(UC_ARM64_REG_S0 if typ=='float' else UC_ARM64_REG_W0,value)
    else:raise AssertionError(hex(address))
    machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
fields=[(0x10,'int',1)]+[(offset,'float',4) for offset in range(0x14,0x28,4)]+[(offset,'int',2) for offset in (0x28,0x2c,0x30)]+[(0x34,'int',1),(0x38,'bool',1),(0x39,'bool',1),(0x3c,'int',2)]
rng=random.Random(641);checks=0
for case in range(256):
    expected={};wire=[];cursor=0
    for offset,typ,width in fields:
        if typ=='float':value=struct.unpack('<I',struct.pack('<f',rng.uniform(-100,100)))[0]
        elif typ=='bool':value=rng.randrange(2)
        else:value=rng.choice([-65536,-32769,-32768,-129,-128,-1,0,1,127,128,32767,32768,65535,65536])
        if typ=='bool':uc.mem_write(source+offset,bytes([value]))
        else:w32(source+offset,value)
        narrowed=value&((1<<(width*8))-1)
        expected[offset]=i32(narrowed-(1<<(width*8)) if typ=='int' and narrowed&(1<<(width*8-1)) else narrowed)
    run(0x14cd8f4,x0=source,x1=stream);uc.mem_write(dest,bytes(0x80));run(0x14cda30,x0=dest,x1=stream)
    assert [(t,w) for t,w,v in wire]==[(t,w) for off,t,w in fields]
    for offset,typ,width in fields:
        actual=uc.mem_read(dest+offset,1)[0] if typ=='bool' else r32(dest+offset)
        assert actual==expected[offset],(case,offset,typ,actual,expected[offset])
    checks+=1
report=dict(nativeModifierCloneRoundTrips=checks,scope='Original ModifyAnimationComponent Serialize/Deserialize with typed stream primitives supplied',
    serializedFields=[dict(offset=hex(off),type=t,width=w) for off,t,w in fields],
    findings=['Destroy and loop flags survive','Type/anchor narrow to signed8; angle/frame/maxFrame/alpha narrow to signed16; five float fields preserve bits'],
    limitations=['Does not prove that a specific live starting unit has this component; full BaseEntity clone/initialization not executed'])
(EVIDENCE/'modifier-clone-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');print(json.dumps({k:v for k,v in report.items() if k!='serializedFields'}))
