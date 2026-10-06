"""Native UseSkill cure through synchronous FighterSystem.OnCure."""
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

from combat_resolution import cure_result,apply_fighter_cure_results,add_raw_parameter
from itertools import product
caster,target,skill,hp,mp,results,world,system,ai=[0x10004000+n*0x1000 for n in range(9)]
w64(caster+0x30,world);w64(ai+0x58,ai)
for flag in (0x316b483,0x316b9ff,0x316b1db,0x316b884):uc.mem_write(flag,b'\x01')
for got in (0x2f5b570,0x2f60038,0x2f60040,0x2f60078,0x2f5fd88,
            0x2f5d0d0,0x2f69a80,0x2f663f8,0x2f66410,0x2f5fb60,0x2f64ba8):
    w64(got,0x10001000)
w64(0x10001000,0x10002000);w32(0x100020e0,1)
events=[];board={}
def external(machine,address,size,user):
    if any(a<=address<b for a,b in ((0x15e0c2c,0x15e1a14),(0x168e3c4,0x168e49c),
            (0x1ae1b2c,0x1ae1b3c),(0x1587e64,0x1588078),(0x1660de4,0x1660ef8),
            (0x1682824,0x1682868),(0x16828c8,0x1682910),(0x16825f8,0x168260c))):return
    value=0
    if address==0x15df0e0:events.append(('eligible',reg('W3')));value=1
    elif address==0x1471200:value=reg('X0')
    elif address==0x14c89d0:value=hp if reg('W1')==10 else mp
    elif address==0x16323a4:events.append(('cost',));value=7
    elif address==0x15e0620:events.append(('balloon',))
    elif address in (0x1477df0,0x1471288,0x14ce638):value=1
    elif address==0x146fd60:value=0
    elif address==0x147e7e0:value=int(destroyed)
    elif address==0x16609ec:value=maximum
    elif address==0x12d2214:value=results;w32(results+0x18,1)
    elif address==0x191e2ec:
        events.append(('event',reg('W1')))
        machine.reg_write(UC_ARM64_REG_X1,reg('X2'));machine.reg_write(UC_ARM64_REG_X0,system)
        machine.reg_write(UC_ARM64_REG_PC,0x1587e64);return
    elif address==0x13eb3a0:value=max(i32(reg('W1')),min(i32(reg('W2')),i32(reg('W0'))))
    elif address==0x13eb3c8:value=max(i32(reg('W0')),i32(reg('W1')))
    elif address==0x1588078:
        events.append(('queue',r32(hp+0x14)))
        uc.mem_write(reg('X8'),struct.pack('<B3x2f8x',int(available),-24.75,96.5))
    elif address==0x1f22e44:value=struct.unpack('<Q',struct.pack('<2f',-24.75,96.5))[0]
    elif address==0x1468bc8:value=ai
    elif address==0x1a6a12c:board[reg('W1')]=i32(reg('W2'))
    elif address==0x1583b48:events.append(('state',reg('W1'),r32(hp+0x14),dict(board)))
    elif address==0x15e0ad4:events.append(('sound',r32(hp+0x14)))
    else:raise AssertionError(hex(address))
    machine.reg_write(UC_ARM64_REG_X0,value&0xffffffffffffffff);machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
checks=0
for typ,destroyed,available,maximum,raw,value,index in product((2,15),(False,True),(False,True),(0,100,150),(0,99),(25,100,2147483647),(0,1)):
    events=[];board={}
    w32(hp+0x14,raw);w32(hp+0x18,100);w32(mp+0x14,10);w32(mp+0x18,10)
    w32(skill+0x28,1);w32(skill+0x2c,typ);w32(skill+0x30,value)
    try:run(0x15e0c2c,x0=caster,x1=target,x2=skill,w3=index,w4=0)
    except Exception:
        print(hex(reg('PC')));raise
    payload=cure_result(caster,target,dict(type=typ,value=value),maximum)
    expected=[('eligible',int(index==0))]
    if index==0:expected.extend([('cost',),('balloon',)])
    expected.append(('event',27));model_hp=[raw];model_board={}
    def add(entity,amount):model_hp[0]=add_raw_parameter(model_hp[0],amount,maximum)[0]
    def queue(entity):expected.append(('queue',model_hp[0]));return (-24.75,96.5) if available else None
    def destination(entity,point):model_board.update({13:point[0],14:point[1]})
    def change(entity,state):expected.append(('state',state,model_hp[0],dict(model_board)))
    apply_fighter_cure_results([payload],lambda entity:not destroyed,add,queue,destination,change)
    expected.append(('sound',model_hp[0]))
    assert events==expected,(typ,destroyed,available,maximum,raw,value,index,events,expected)
    assert r32(hp+0x14)==model_hp[0] and board==model_board
    assert r32(mp+0x14)==(3 if index==0 else 10)
    assert r32(results+0x38)==payload['amount']
    checks+=1
report=dict(nativeJoinedCureCases=checks,
    scope='Full original UseSkill healing/revival branch, Cure, OnCure, Param.AddValue and raw Parameter.Add/Sub',
    limitations=['Eligibility, effective maximum, component access and nullable queue location supplied; animation/state entry and sound recorded; no full world'])
(EVIDENCE/'cure-event-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');print(json.dumps(report))

