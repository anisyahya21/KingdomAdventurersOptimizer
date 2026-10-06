"""Native multicast combination and reentrant event delivery."""
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

from elftools.elf.elffile import ELFFile
from capstone import Cs,CS_ARCH_ARM64,CS_MODE_ARM
import hashlib
# Unsymbolized runtime entry boundaries established from ctor references and ret.
runtime=[]
with (EVIDENCE.parent/'G2.1/39257e72291d/inputs/libil2cpp.so').open('rb') as f:
    elf=ELFFile(f)
    for start,end,origin in ((0x11725bc,0x1172604,'EventHandler<object>.ctor stores this multicast invoker at +0x38'),
                             (0x12cdef8,0x12cdf38,'MulticastDelegate.CombineImpl calls this allocator/setup helper')):
        seg=next(s for s in elf.iter_segments() if s['p_type']=='PT_LOAD' and s['p_vaddr']<=start<s['p_vaddr']+s['p_filesz'])
        offset=seg['p_offset']+start-seg['p_vaddr'];f.seek(offset);code=f.read(end-start);uc.mem_write(start,code)
        (EVIDENCE/f'{start:x}.asm').write_text('\n'.join(f'{i.address:x}: {i.mnemonic} {i.op_str}' for i in Cs(CS_ARCH_ARM64,CS_MODE_ARM).disasm(code,start))+'\n',encoding='utf-8')
        runtime.append(dict(rva=hex(start),end=hex(end),offset=hex(offset),sha256=hashlib.sha256(code).hexdigest(),origin=origin))
(EVIDENCE/'event-runtime-slices.json').write_text(json.dumps(runtime,indent=2)+'\n',encoding='utf-8')
klass,manager=0x10002000,0x10004000
uc.mem_write(klass+0x130,b'\x01');w64(klass+0xc8,0x10003000);w64(0x10003000,klass);w32(klass+0xe0,1)
for got in (0x2f8aad8,0x2f8a948,0x2f6f240):w64(got,0x10001000)
w64(0x10001000,klass)
for flag in (0x316fd20,0x316c35c):uc.mem_write(flag,b'\x01')
leaves=[0x10010000+i*0x100 for i in range(4)]
for i,leaf in enumerate(leaves):
    w64(leaf,klass);w64(leaf+0x18,0x30000100);w64(leaf+0x38,0x11725bc);w64(leaf+0x40,i+1)
allocation=0x10040000;combined=0;events=[];counter=0;nested=False
def allocate(size):
    global allocation
    result=allocation;allocation+=size;uc.mem_write(result,b'\x00'*size);w64(result,klass);return result
def external(machine,address,size,user):
    global counter
    if any(a<=address<b for a,b in ((0x269eec4,0x269f12c),(0x12cdef8,0x12cdf38),
            (0x11725bc,0x1172604),(0x18b2f00,0x18b2f8c))):return
    value=0
    if address==0x13363a8:value=allocate(0x100)
    elif address==0x1358a10:w64(reg('X0'),reg('X1'))
    elif address==0x12d2214:value=allocate(0x100);w32(value+0x18,reg('W1'))
    elif address==0x12d22a8:value=reg('X0')
    elif address==0x2671ce8:
        data=bytes(uc.mem_read(reg('X0')+0x20+reg('W1')*8,reg('W4')*8))
        uc.mem_write(reg('X2')+0x20+reg('W3')*8,data)
    elif address==0x18c05a0:value=combined
    elif address==0x30000100:
        owner,payload=reg('X0'),reg('X1');counter+=1;events.append((owner,payload,counter))
        if nested and payload==42 and owner==expected_order[0]:
            machine.reg_write(UC_ARM64_REG_X0,manager);machine.reg_write(UC_ARM64_REG_X1,26)
            machine.reg_write(UC_ARM64_REG_X2,43);machine.reg_write(UC_ARM64_REG_PC,0x18b2f00);return
    else:raise AssertionError(hex(address))
    machine.reg_write(UC_ARM64_REG_X0,value);machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
def make(indices):
    if len(indices)==1:return leaves[indices[0]]
    obj=allocate(0x100);array=allocate(0x100);w64(obj+0x78,array);w32(array+0x18,len(indices))
    w64(obj+0x18,0x11725bc);w64(obj+0x38,0x11725bc);w64(obj+0x40,obj)
    for i,index in enumerate(indices):w64(array+0x20+i*8,leaves[index])
    return obj
checks=0
for left in ((0,),(0,1),(2,0,1)):
    for right in ((),(3,),(3,2)):
        for nested in (False,True):
            allocation=0x10040000;events=[];counter=0
            a,b=make(left),make(right) if right else 0
            run(0x269eec4,x0=a,x1=b);combined=reg('X0')
            expected_order=[i+1 for i in left+right]
            run(0x18b2f00,x0=manager,w1=26,x2=42)
            order=[]
            for owner in expected_order:
                order.append((owner,42))
                if nested and owner==expected_order[0]:
                    order.extend((inner,43) for inner in expected_order)
            assert events==[(owner,payload,i+1) for i,(owner,payload) in enumerate(order)],events
            checks+=1
report=dict(nativeCombinedMulticastDispatchChecks=checks,
    scope='Original MulticastDelegate.CombineImpl, native allocation/setup helper, EventManager.Send<object> and generated multicast invoker; reentrant nested events',
    limitations=['Allocation, array copy, type checks and event dictionary lookup supplied; subscription registration and system initialization order checked separately'])
(EVIDENCE/'event-multicast-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');print(json.dumps(report))

