"""Native CanBuff filter for area status applications."""
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

from combat_skills import can_receive_status
from itertools import product
caster,target,skill,ai,worlds,world,data=[0x10004000+i*0x1000 for i in range(7)]
uc.mem_write(0x316ba03,b'\x01');w64(0x2f5fe38,0x10001000);w64(0x10001000,0x10002000)
w64(ai+0x58,ai);w64(target+0x30,world)
def external(machine,address,size,user):
    if 0x168ea2c<=address<0x168ebfc:return
    value=0
    if address==0x1471288:value=has_parameter
    elif address==0x1471200:value=data
    elif address==0x14ce638:value=has_hp
    elif address==0x146fd60:value=map_chip
    elif address==0x14ce670:value=stored_hp
    elif address==0x1468bc8:value=ai
    elif address==0x1a6a204:value=has_status
    elif address==0x1470c84:value=monster
    elif address in (0x1470bfc,0x14cdb94):value=data
    elif address==0x14cdc10:value=boss
    elif address==0x14c2b14:value=worlds
    elif address==0x161c200:value=hostile_flag
    elif address==0x146b824:value=caster_ally if reg('X0')==caster else target_ally
    else:raise AssertionError(hex(address))
    machine.reg_write(UC_ARM64_REG_X0,int(value)&0xffffffffffffffff);machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
checks=0
for has_parameter,has_hp,map_chip,has_status,monster,boss,main_world,hostile_flag,caster_ally,target_ally in product((False,True),repeat=10):
    for stored_hp,monster_type in product((-1,0,1),(0,1,2)):
        w64(worlds+0x10,world if main_world else world+8);w32(data+0x30,monster_type)
        actual=run(0x168ea2c,x0=caster,x1=target,x2=skill)
        expected=can_receive_status(has_parameter=has_parameter,has_hp=has_hp,map_chip=map_chip,stored_hp=stored_hp,
            has_status=has_status,monster=monster,boss=boss,main_world=main_world,monster_type=monster_type,
            hostile_flag=hostile_flag,caster_ally=caster_ally,target_ally=target_ally)
        assert actual==int(expected)
        checks+=1
report=dict(nativeStatusTargetFilterChecks=checks,
    scope='Full original CanBuff with component, parameter and flag getter stubs; all Boolean gate combinations and HP/type boundaries',
    limitations=['Non-null target and skill with AI; no collection or whole-world replay'])
(EVIDENCE/'status-filter-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');print(json.dumps(report))

