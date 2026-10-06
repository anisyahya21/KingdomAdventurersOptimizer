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

from copy import deepcopy
from combat_tick import update_fighters
uc.mem_write(0x316b1c1,b'\x01')
for i,got in enumerate([0x2f5f410,0x2f5f980,0x2f60048,0x2f5fb60,0x2f64bb0,0x2f5c298,0x2f64bb8]):
    ptr,cls=0x10001000+i*8,0x10002000+i*0x100
    w64(got,ptr);w64(ptr,cls);w32(cls+0xe0,1)
system,battle,teams,delegate=0x10004000,0x10004100,0x10004200,0x10004300
w64(system+0x10,0x10004400);w64(system+0x28,0x10004500);w64(system+0x38,teams)
w64(delegate+0x18,0x30000100);w64(delegate+0x40,system);w32(teams+0x18,2)
addresses=[0x10006000+i*0x100 for i in range(4)]
for i,a in enumerate(addresses):w64(a+0x58,a)
for i in range(2):
    array=0x10005000+i*0x100;w64(teams+0x30+i*24,array);w32(array+0x18,2)
    for j in range(2):w64(array+0x20+j*8,addresses[2*i+j])
records={};events=[];selected=0

def state_update(fighter,prior,all_records,log):
    log.append(('state',fighter['id'],prior,fighter['board'][4]))
    # Synthetic immediate hit proves later fighters see earlier state mutations.
    if fighter['id']==0 and interrupt:
        other=all_records[1];other['board'][5]=6;other['board'][4]=0
    if prior==7:fighter['board'][5]=8

def commands(fighter,log):log.append(('script',fighter['id'],fighter['board'][5],fighter['board'][4]))

def external(machine,address,size,user):
    global selected
    if 0x1583cf8<=address<0x15840dc:return
    result=0
    if address==0x191de04:result=battle if battle_state is not None else 0
    elif address==0x147e7e0:result=bool(records[reg('X0')]['flags'] & 2)
    elif address==0x1468bc8:result=reg('X0')
    elif address==0x1a6a108:result=records[reg('X0')]['board'][reg('W1')]
    elif address==0x1a6a12c:records[reg('X0')]['board'][4]=i32(reg('W2'))
    elif address==0x1b114c8:selected=reg('W1');result=delegate
    elif address==0x30000100:state_update(records[reg('X1')],selected,[records[a] for a in addresses],events)
    elif address==0x1481e54:commands(records[reg('X0')],events)
    elif address==0x168e214:events.append(('post',records[reg('X0')]['id']));result=0
    else:raise AssertionError(hex(address))
    machine.reg_write(UC_ARM64_REG_X0,int(result)&0xffffffffffffffff);machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
checks=0
for battle_state in [None,0,1,2,3,4,-1]:
 for interrupt in [False,True]:
  for destroyed in [False,True]:
   initial=[dict(id=i,board={4:10,5:s},flags=2 if destroyed and i==2 else 0) for i,s in enumerate([4,4,7,8])]
   records=dict(zip(addresses,deepcopy(initial)));events=[]
   if battle_state is not None:w32(battle+0x38,battle_state)
   run(0x1583cf8,x0=system)
   expected=deepcopy(initial);expected_events=[]
   update_fighters([expected[:2],expected[2:]],battle_state,lambda f,s:state_update(f,s,expected,expected_events),lambda f:commands(f,expected_events),lambda f,s:expected_events.append(('post',f['id'])))
   assert list(records.values())==expected
   assert events==expected_events,(battle_state,interrupt,destroyed,events,expected_events)
   checks+=1
report=dict(orderedFighterPhases=checks,limits=['Original full fighter iteration executes with state/command delegates and entity/blackboard stubs.','Synthetic immediate interruption establishes iteration visibility; actual damage/state-entry effects are checked separately.','Post-fighter terrain drawing is disabled by a false attribute result.'])
(EVIDENCE/'fighter-phase-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');print(json.dumps(report))
