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
from combat_states import update_charging
uc.mem_write(0x316b1c8,b'\x01')
for i,got in enumerate([0x2f5fe38,0x2f60048,0x2f5fb60,0x2f5d628,0x2f5b890,0x2f5fd60,0x2f5d0d0,0x2f64be0,0x2f64be8]):
    ptr,cls=0x10001000+i*8,0x10002000+i*0x100
    w64(got,ptr);w64(ptr,cls);w32(cls+0xe0,1)
system,entity,ai,bb,longbb,table,skilldata,target=[0x10004000+i*0x100 for i in range(8)]
w64(ai+0x58,bb);w64(ai+0x60,longbb);w32(table+0x18,1);w64(table+0x20,skilldata);w32(skilldata+0x2c,67);w64(target+0x28,1234)
values={};writes={};transition=None;calls=[]

def external(machine,address,size,user):
    global transition
    if 0x1584bf0<=address<0x1585034:return
    result=0
    if address==0x1468bc8:result=ai
    elif address==0x1a6a204:result=sleeping
    elif address==0x1a6a108:result=values[reg('W1')]
    elif address==0x1a6a12c:values[reg('W1')]=i32(reg('W2'))
    elif address==0x161f56c:result=table
    elif address==0x166070c:result=100
    elif address==0x168c380:result=interval
    elif address==0x15e002c:result=0x10006000
    elif address==0x15e0498:
        calls.append('choose');result=target if selected else 0
        machine.reg_write(UC_ARM64_REG_X1,42 if selected else 0)
    elif address==0x16863e4:result=not selected
    elif address==0x1686304:result=skilldata
    elif address==0x14c5738:writes.update(skill=42,command_target=1234)
    elif address==0x158503c:uc.mem_write(reg('X8'),bytes(24))
    elif address==0x158511c:calls.append('long');result=long_range
    elif address==0x1585170:calls.append('nearest');result=target if nearest else 0
    elif address==0x1585250:calls.append('front');result=front
    elif address==0x15852ec:calls.append('front_target');result=target if front_target else 0
    elif address==0x1a6a36c:writes['target']=reg('X2') if reg('X2')!=0xffffffffffffffff else -1
    elif address==0x15835a8:calls.append('decide');result=decision
    elif address==0x1583b48:transition=reg('W1')
    else:raise AssertionError(hex(address))
    machine.reg_write(UC_ARM64_REG_X0,int(result)&0xffffffffffffffff);machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
checks=0
for gauge,sleeping,selected,long_range,nearest,front,front_target,decision in product([0,9,10,11,2147483647],[False,True],[False,True],[False,True],[False,True],[False,True],[False,True],[1,2,3]):
    interval=10;values={8:gauge,62:0};writes={};transition=None;calls=[]
    run(0x1584bf0,x0=system,x1=entity)
    expected_calls=[]
    def callback(name,value):
        def call():expected_calls.append(name);return value
        return call
    expected=update_charging(gauge,sleeping,interval,callback('choose',(42,1234) if selected else None),callback('long',long_range),callback('nearest',1234 if nearest else None),callback('front',front),callback('front_target',1234 if front_target else None),callback('decide',decision))
    assert (values[8],transition,writes)==expected,((values[8],transition,writes),expected)
    assert calls==expected_calls,(calls,expected_calls)
    checks+=1
report=dict(chargingCases=checks,limits=['Original UpdateCharging with stubbed interval, candidate invocation, targets, blackboards and transitions.','Checks branch order and writes including signed overflow; does not execute attack/skill entry effects.'])
(EVIDENCE/'charging-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');print(json.dumps(report))
