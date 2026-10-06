"""Native cell status snapshot application and per-target status mutation."""
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

from combat_skills import apply_status
from itertools import product
caster,skill,world,array,closure,delegate=[0x10004000+n*0x1000 for n in range(6)]
targets=[0x10010000+n*0x1000 for n in range(3)]
w64(caster+0x30,world)
for target in targets:w64(target+0x58,target)
for flag in (0x316ba00,0x316ba05):uc.mem_write(flag,b'\x01')
for got in (0x2f5c538,0x2f5fb20,0x2f5fb60,0x2f69ab8,0x2f5d5b8,0x2f69ac0,
            0x2f5d7b0,0x2f5b560,0x2f60a38,0x2f5fcc8,0x2f5d5a8):w64(got,0x10001000)
w64(0x10001000,0x10002000);w32(0x100020e0,1);w64(0x100020b8,0x10003000)
for offset,value in ((0x10,1),(0x24,2),(0x28,3)):w32(0x10003000+offset,value)
boards={};events=[];roll_index=0;current=0;allocated=0
def snapshot():return tuple(tuple(sorted(boards[t].items())) for t in targets)
def external(machine,address,size,user):
    global roll_index,current,allocated
    if 0x168ee44<=address<0x168f02c or 0x168e49c<=address<0x168e66c:return
    value=0
    if address==0x12d23b8:value=closure if allocated==0 else delegate;allocated+=1
    elif address in (0x2691aa0,0x1cb5fc8):pass
    elif address==0x191de04:value=world
    elif address in (0x1500e38,0x18abfac,0x18a70c4):value=array
    elif address==0x18c1278:value=int(not order)
    elif address==0x168e66c:
        current=reg('X1');events.append(('accuracy',current,snapshot()));value=37
    elif address==0x1460024:
        events.append(('roll',reg('W0'),roll_index));value=(mask>>roll_index)&1;roll_index+=1
    elif address==0x1468bc8:value=reg('X0')
    elif address==0x1a6a12c:boards[reg('X0')][reg('W1')]=i32(reg('W2'))
    elif address==0x168e760:events.append(('turns',current,snapshot()));value=4
    elif address==0x168e800:events.append(('text',reg('X0'),reg('W1'),snapshot()))
    else:raise AssertionError(hex(address))
    machine.reg_write(UC_ARM64_REG_X0,value&0xffffffffffffffff);machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
checks=0
for typ,mask,order in product((66,67,22),range(8),((),(0,1,2),(2,0,1),(0,1,0))):
    boards={target:{62:99,63:2,64:19} for target in targets};events=[];roll_index=0;allocated=0
    w32(skill+0x18,113);w32(skill+0x2c,typ);w32(array+0x18,len(order))
    for i,index in enumerate(order):w64(array+0x20+i*8,targets[index])
    run(0x168ee44,w0=1,w1=2,x2=caster,x3=skill)
    native_boards={target:dict(board) for target,board in boards.items()};native_events=list(events)
    boards={target:{62:99,63:2,64:19} for target in targets};events=[];roll_index=0
    for index in order:
        current=targets[index]
        def accuracy():events.append(('accuracy',current,snapshot()));return 37
        def hits(rate):
            global roll_index
            events.append(('roll',rate,roll_index));hit=(mask>>roll_index)&1;roll_index+=1;return hit
        def turns():events.append(('turns',current,snapshot()));return 4
        def show(kind):events.append(('text',current,dict(miss=1,defense_down=2,sleep=3)[kind],snapshot()))
        apply_status(dict(id=113,type=typ),boards[current],accuracy,hits,turns,show)
    assert boards==native_boards
    assert events==native_events,(typ,mask,order,events,native_events)
    checks+=1
report=dict(nativeCellStatusApplicationCases=checks,
    scope='Original BuffEntitiesOnCell materialized-array loop and complete Buff status writer; callback order compared to portable model',
    limitations=['Supplied filtered snapshot, accuracy/turn calculations and hit rolls; duplicates/existing statuses intentionally test low-level behavior, not naturally reachable eligibility'])
(EVIDENCE/'status-application-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');print(json.dumps(report))

