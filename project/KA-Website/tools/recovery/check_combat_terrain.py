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
from combat_resolution import battle_terrain_match
for flag in [0x316b9fe,0x316ae5f]:uc.mem_write(flag,b'\x01')
for i,got in enumerate([0x2f5fb68,0x2f64bb8,0x2f5d620,0x2f5b890]):
    ptr,cls=0x10001000+i*8,0x10002000+i*0x100
    w64(got,ptr);w64(ptr,cls);w32(cls+0xe0,1)
entity,world,worlds,battle,conquest,weapon,table,area=[0x10004000+i*0x100 for i in range(8)]
w64(entity+0x30,world);w64(worlds+0x10,0x10005000);w64(battle+0x48,conquest)
w32(table+0x18,1);w64(table+0x20,area)
def external(machine,address,size,user):
    if 0x168e214<=address<0x168e3c0 or 0x14f2e08<=address<0x14f2ea8:return
    if address==0x146de58:result=equipped
    elif address==0x14c2b14:result=worlds
    elif address==0x24cf170:result=1
    elif address==0x191de04:result=battle
    elif address==0x1626abc:result=table
    elif address in [0x146ddd0,0x14c7db0]:result=weapon
    else:raise AssertionError(hex(address))
    machine.reg_write(UC_ARM64_REG_X0,int(result)&0xffffffffffffffff);machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
checks=0
for equipped,area_id,terrain,attribute in product([False,True],[-1,0],range(-1,8),range(-1,8)):
    w32(conquest+0x38,area_id);w32(area+0x24,terrain);w32(weapon+0x78,attribute)
    expected=battle_terrain_match(equipped,-1 if area_id==-1 else terrain,attribute)
    assert bool(run(0x168e214,x0=entity))==expected
    checks+=1
report=dict(terrainCases=checks,limits=['Original battle-world IsGoodMatchAttribute and GetTerrain execute together; equipment/world identity/area table access are stubbed.','Special launcher explicitly sets conquest.areaDataId=-1; all weapon attributes consequently fail terrain-match in that path.'])
(EVIDENCE/'terrain-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');print(json.dumps(report))
