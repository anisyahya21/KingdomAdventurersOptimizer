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
from combat_states import update_attacking
uc.mem_write(0x316b1ca,b'\x01')
for i,got in enumerate([0x2f60038,0x2f5f980,0x2f5d3e8,0x2f60048,0x2f5fb60,0x2f5fd60,0x2f5b960,0x2f5e538,0x2f60078]):
    ptr,cls=0x10001000+i*8,0x10002000+i*0x100
    w64(got,ptr);w64(ptr,cls);w32(cls+0xe0,1);w64(cls+0xb8,0x10003000)
w32(0x10003008,100)
system,entity,ai,bb,target,weapon,position,array,skill=[0x10004000+i*0x100 for i in range(9)]
w64(system+0x10,0x10005000);w64(ai+0x58,bb);w64(ai+0x60,bb);w32(array+0x18,1)
uc.mem_write(position+0x10,struct.pack('<3f',48,0,96))
values={};events=[];transition=None

def external(machine,address,size,user):
    global transition
    if 0x1585568<=address<0x1585a10:return
    result=0
    if address==0x1468bc8:result=ai
    elif address==0x1a6a108:result=values[reg('W1')]
    elif address==0x1a6a12c:values[reg('W1')]=i32(reg('W2'))
    elif address==0x1a6a40c:result=1234
    elif address==0x147b328:events.append('target');result=target if present else 0
    elif address==0x146de58:result=1
    elif address in [0x146ddd0,0x14c7db0]:result=weapon
    elif address==0x161c200:result=projectile
    elif address==0x168d1e8:events.append('direct');uc.mem_write(reg('X8'),bytes(32))
    elif address==0x12d2214:result=array
    elif address==0x191e2ec:events.append('event26');assert reg('W1')==26
    elif address in [0x161f418,0x161f5d0]:result=skill
    elif address==0x1471498:result=position
    elif address==0x14607ec:uc.mem_write(reg('X0'),struct.pack('<3I',reg('S0'),reg('S1'),reg('S2')))
    elif address==0x15e1c90:events.append('projectile'+str(1 if kind==8 else 2))
    elif address==0x144cdc8:events.append('sound_roll');result=0
    elif address==0x15835a8:events.append('decide');result=decision
    elif address==0x1583b48:transition=reg('W1')
    else:raise AssertionError(hex(address))
    machine.reg_write(UC_ARM64_REG_X0,int(result)&0xffffffffffffffff);machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
checks=0
for frame,present,projectile,kind,decision in product([0,1,10,11,12,19,20,21],[False,True],[False,True],[0,4,7,8,9],[1,2,3]):
    values={4:frame,8:456};events=[];transition=None;w32(weapon+0x4c,kind)
    run(0x1585568,x0=system,x1=entity)
    expected_events=[]
    def get_target():expected_events.append('target');return 1234 if present else None
    def direct(t):expected_events.extend(['direct','event26'])
    def fire(t,s):expected_events.append('projectile'+str(s))
    def sound():expected_events.append('sound_roll')
    def decide():expected_events.append('decide');return decision
    expected=update_attacking(frame,456,get_target,projectile,kind,direct,fire,sound,decide)
    assert (values[8],transition)==expected
    assert events==expected_events,(frame,present,projectile,kind,events,expected_events)
    checks+=1
report=dict(releaseCases=checks,limits=['Original UpdateAttacking with target/equipment/attack/event/projectile/decision stubs.','Sound probability callback records a failed outcome to skip playback; damage resolution and projectile construction are checked separately.'])
(EVIDENCE/'release-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');print(json.dumps(report))
