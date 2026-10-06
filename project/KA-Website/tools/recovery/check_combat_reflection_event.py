"""Native reflected payload through synchronous FighterSystem.OnAttack."""
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

from combat_resolution import reflected_attack_result, apply_fighter_attack_results, subtract_raw_parameter

caster,target,skill,params,results,world,system=[0x10004000+n*0x1000 for n in range(7)]
for got in (0x2f5b570,0x2f60038,0x2f60040,0x2f60078,0x2f5fd88):
    w64(got,0x10001000)
w64(0x10001000,0x10002000);w32(0x100020e0,1)
uc.mem_write(0x316b1da,b'\x01')
events=[];destroyed=False
def external(machine,address,size,user):
    if any(a<=address<b for a,b in ((0x15e164c,0x15e1734),(0x22141b8,0x22141d8),
            (0x1587d60,0x1587e64),(0x22141a8,0x22141b8),(0x16828c8,0x1682910))):return
    value=0
    if address in (0x16657b0,0x13eb3c8):value=max(i32(reg('W0')),i32(reg('W1')))
    elif address==0x12d2214:
        value=results;w32(results+0x18,1)
    elif address==0x191e2ec:
        events.append(('event',reg('W1')))
        machine.reg_write(UC_ARM64_REG_X1,reg('X2'))
        machine.reg_write(UC_ARM64_REG_X0,system)
        machine.reg_write(UC_ARM64_REG_PC,0x1587d60)
        return
    elif address==0x147e7e0:value=int(destroyed)
    elif address in (0x1471200,0x14c89d0):value=params
    elif address==0x1583b48:events.append(('state',reg('X2'),reg('W1'),r32(params+0x14)))
    elif address==0x15e0ad4:events.append(('sound',r32(params+0x14)))
    else:raise AssertionError(hex(address))
    machine.reg_write(UC_ARM64_REG_X0,value&0xffffffffffffffff)
    machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
checks=0
for destroyed in (False,True):
    for damage in (0,1,3,100,99999,2147483647,-2147483648):
        for multiplier in (0,1,50,100,200,2147483647):
            events=[];w32(params+0x14,73);w32(params+0x18,100)
            w32(skill+0x30,multiplier)
            for name,value in dict(SP=0x20008000,X28=caster,X21=target,X22=skill,
                                   W23=damage,W8=18).items():
                uc.reg_write(globals()['UC_ARM64_REG_'+name],value&0xffffffffffffffff)
            w64(0x20008000+0x128,world)
            try:uc.emu_start(0x15e164c,0x15e1734,count=10000)
            except Exception:
                print(hex(reg('PC')));raise
            assert reg('PC')==0x15e1734,hex(reg('PC'))
            expected=reflected_attack_result(caster,target,dict(value=multiplier),damage)
            assert struct.unpack('<QQ',uc.mem_read(results+0x20,16))==(caster,target)
            assert bytes(uc.mem_read(results+0x30,2))==b'\x01\x00'
            assert r32(results+0x34)==expected['damage']
            assert struct.unpack('<Q',uc.mem_read(results+0x38,8))[0]==skill
            portable_hp=[73];portable_events=[('event',26)]
            def subtract_hp(entity,amount):portable_hp[0]=subtract_raw_parameter(portable_hp[0],100,amount)[0]
            def change_state(entity,state):portable_events.append(('state',entity,state,portable_hp[0]))
            apply_fighter_attack_results([expected],lambda entity:not destroyed,subtract_hp,change_state)
            portable_events.append(('sound',portable_hp[0]))
            assert portable_hp[0]==r32(params+0x14)
            assert portable_events==events,(portable_events,events)
            checks+=1
report=dict(nativeReflectionEventChecks=checks,
    scope='Original successful type18 UseSkill payload, AttackResult constructor, FighterSystem.OnAttack and Parameter.Sub joined through synchronous event redirect',
    limitations=['Starts after eligibility, MP and balloon; other event subscribers omitted; state transition and skill sound recorded; not full nested Attack or world replay'])
(EVIDENCE/'reflection-event-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
print(json.dumps(report))

