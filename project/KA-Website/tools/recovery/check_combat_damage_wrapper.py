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
from combat_resolution import damage_from_parameters,random_range,subtract_raw_parameter
from combat_entities import CombatEntities
from combat_shared_resolution import SharedAttackMath
for flag in (0x316b9fb,0x316b9fc,0x316b9f1):uc.mem_write(flag,b'\x01')
for i,got in enumerate((0x2f5d0d0,0x2f5fe38,0x2f60048,0x2f5b890,0x2f5c538,0x2f5b570)):
    ptr,cls=0x10001000+i*8,0x10002000+i*0x100
    w64(got,ptr);w64(ptr,cls);w32(cls+0xe0,1)
attacker,target,ai,table,status_ptr,parameter=0x10004000,0x10004100,0x10004200,0x10004300,0x10004400,0x10004500
w64(ai+0x58,0x10005000);w32(table+0x18,1);w64(table+0x20,status_ptr)
ranges=[(0x168cf80,0x168d170),(0x168d178,0x168d1e8),(0x168c460,0x168c588),(0x16657b0,0x16657bc),(0x16828c8,0x1682910)]

def external(machine,address,size,user):
    if any(a<=address<b for a,b in ranges):return
    result=0
    if address==0x1470c84:result=monster
    elif address==0x166070c:
        vals=attack_values if reg('X0')==attacker else defense_values
        result=vals.get(reg('W1'),i32(reg('W2')))
    elif address==0x1468bc8:result=ai
    elif address==0x1a6a204:result=status is not None
    elif address==0x1a6a108:result=0
    elif address==0x161f56c:result=table
    elif address==0x145ff98:
        low,high=i32(reg('W0')),i32(reg('W1'))
        result=random_range(samples[len(calls)],low,high);calls.append((low,high))
    elif address==0x13eb3c8:result=max(i32(reg('W0')),i32(reg('W1')))
    else:raise AssertionError(hex(address))
    machine.reg_write(UC_ARM64_REG_X0,int(result)&0xffffffffffffffff);machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
checks=0
for critical,magical,monster,int_present,defense,status_value in product((False,True),(False,True),(False,True),(False,True),(0,8,101,10000),(None,-50,0,50,100,150)):
    attack_values={13:103,19:307}
    if int_present:attack_values[18]=601
    defense_values={14:defense}
    status=None if status_value is None else dict(type=66,value=status_value)
    if status:w32(status_ptr+0x2c,status['type']);w32(status_ptr+0x30,status['value'])
    samples=[20,10,5];calls=[]
    actual=i32(run(0x168cf80,w0=int(critical),x1=attacker,x2=target,w3=int(magical)))
    model_samples=iter(samples);consumed=[]
    def draw():value=next(model_samples);consumed.append(value);return value
    shared=CombatEntities(100,[]);a,b=shared.allocate(),shared.allocate()
    shared.add_component(b,'AI',dict(board={62:0} if status is not None else {}))
    math=SharedAttackMath(shared,{0:status},lambda identity,key,default:(attack_values if identity==a else defense_values).get(key,default),lambda identity:monster,draw)
    expected=math.damage(critical,a,b,magical)
    assert actual==expected,(critical,magical,monster,int_present,defense,status_value,actual,expected)
    assert len(calls)==len(consumed);checks+=1
sub_checks=0
for raw,maximum,amount in product((-2147483648,-1,0,5,2147483647),(0,10,2147483647),(-2147483648,-1,0,7,2147483647)):
    w32(parameter+0x14,raw);w32(parameter+0x18,maximum)
    correction=i32(run(0x16828c8,x0=parameter,w1=amount))
    assert (r32(parameter+0x14),correction)==subtract_raw_parameter(raw,maximum,amount)
    sub_checks+=1
report=dict(nativeDamageWrapperCases=checks,nativeRawParameterSubCases=sub_checks,sharedComponentStorage=True,
    scope='Original entity CalcDamage, monster INT fallback and base damage arithmetic; effective-stat getters and Random.Rand supplied; original Parameter.Sub with clamp helper supplied',
    limitations=['No live equipment/job stat retrieval or real random-state advance in this fixture'])
(EVIDENCE/'damage-wrapper-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');print(json.dumps(report))
