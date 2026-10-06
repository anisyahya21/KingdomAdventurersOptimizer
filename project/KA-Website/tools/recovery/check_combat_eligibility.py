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

from random import Random
from combat_skills import can_use_skill
uc.mem_write(0x316b481,b'\x01')
for i,got in enumerate([0x2f5fd60,0x2f5d0d0,0x2f61dc8,0x2f5d620]):
    ptr,cls=0x10001000+i*8,0x10002000+i*0x100
    w64(got,ptr);w64(ptr,cls);w32(cls+0xe0,1)
entity,target,skill,weapon,world=0x10004000,0x10004100,0x10004200,0x10004300,0x10004400
w64(entity+0x30,world)
def external(machine,address,size,user):
    if 0x15df0e0<=address<0x15df3a4:return
    result=0
    if address==0x166070c:result=args['mp'] if reg('W1')==11 else args['target_hp']
    elif address==0x16323a4:result=args['cost']
    elif address==0x161c200:result=bool(row['flags']&reg('W1'))
    elif address==0x147e7e0:result=not args['target_exists']
    elif address==0x146de58:result=args['weapon_type'] is not None
    elif address in [0x146ddd0,0x14c7db0]:result=weapon
    elif address==0x165e91c:result=args['target_hp_rate']
    elif address==0x14724cc:result=0x10004500
    elif address==0x14d1974:result=args['invoking']
    elif address==0x24cf170:result=args['battle_world']
    elif address==0x1585250:result=args['most_front']
    elif address in [0x15e099c,0x15e0718]:result=0x10004600
    elif address==0x191de04:result=0x10004700
    elif address==0x15890c0:result=args['opponent_in_range']
    else:raise AssertionError(hex(address))
    machine.reg_write(UC_ARM64_REG_X0,int(result)&0xffffffffffffffff);machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
rows=json.loads((EVIDENCE/'weapon-skill-profiles.json').read_text(encoding='utf-8'))['skills'];rng=Random(20260912)
checks=0
for row in rows+[None]:
 for index in range(100):
    args=dict(check_mp=bool(rng.randrange(2)),mp=rng.choice([-1,0,10,100]),cost=rng.choice([0,10,100]),target_exists=bool(rng.randrange(2)),weapon_type=rng.choice([None,-1,0,4,7,8]),target_hp=rng.choice([-1,0,1,100]),target_hp_rate=rng.choice([0,1,50,99,100]),invoking=bool(rng.randrange(2)),battle_world=bool(rng.randrange(2)),most_front=bool(rng.randrange(2)),opponent_in_range=bool(rng.randrange(2)))
    if row:
        for offset,key in [(0x28,'category'),(0x2c,'type'),(0x50,'requiredEquipType')]:w32(skill+offset,row[key])
    w32(weapon+0x4c,args['weapon_type'] if args['weapon_type'] is not None else -1)
    actual=bool(run(0x15df0e0,x0=entity,x1=target,x2=skill if row else 0,w3=args['check_mp']))
    assert actual==can_use_skill(row,**args),(row,args,actual)
    checks+=1
report=dict(eligibilityCases=checks,skills=len(rows),limits=['Original full CanUseSkill with component/MP/target-existence/invoking/spatial helpers stubbed.','No generic caster HP/state or MapChip rejection exists in this method; target-selection and CanDamage are separate.','Deterministic generated inputs sample combinations across all136 skill records and null skill.'])
(EVIDENCE/'skill-eligibility-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');print(json.dumps(report))
