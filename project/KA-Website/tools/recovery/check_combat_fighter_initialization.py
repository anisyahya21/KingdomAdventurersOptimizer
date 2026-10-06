"""Native command timing and interruption primitives, with explicit callee stubs."""
import json
import struct
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE
from unicorn.arm64_const import *
from combat_initial_state import EVIDENCE, i32, initialize_fighter_teams

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

uc.mem_write(0x316b1c0,b'\x01')
for page in (0x2f5b000,0x2f5d000,0x2f5e000,0x2f5f000,0x2f60000,0x2f64000):
    for offset in range(0,0x1000,8):w64(page+offset,0x10001000)
w64(0x10001000,0x10002000);w32(0x100020e0,1);w64(0x100020b8,0x10003000)
for offset in range(0,0x40,8):w64(0x10003000+offset,1)
w64(0x10003010,0x10008000);w32(0x10008018,4)
for direction in range(4):
    w64(0x10008020+direction*8,0x10008100+direction*0x100);w32(0x10008114+direction*0x100,1 if direction==2 else -1)
system,teams,world=0x10004000,0x10005000,0x10006000
w64(system+0x10,world);w32(teams+0x18,2)
entities=[0x10010000+i*0x1000 for i in range(6)]
arrays=[0x10007000,0x10007100];sorted_arrays=[0x10007200,0x10007300]
for team in range(2):
    base=teams+0x20+team*24;w32(base,team);w64(base+8,entities[team*3+2]);w64(base+0x10,arrays[team])
    w32(arrays[team]+0x18,3);w32(sorted_arrays[team]+0x18,3)
    for index in range(3):w64(arrays[team]+0x20+index*8,entities[team*3+index])
boards={e:{} for e in entities};events=[];current_team=0;allocation=0x10080000
stubbed=set()

def external(machine,address,size,user):
    global current_team,allocation
    if address==0x1582b10:current_team=r32(reg('X1'))
    if 0x1582a88<=address<0x1582b08 or 0x1582b10<=address<0x15833b4 or 0x158344c<=address<0x1583550:return
    stubbed.add(address)
    result=0
    if address==0x12d23b8:result=allocation;allocation+=0x100
    elif address in (0x12d21a0,0x2691aa0,0x1cb6888,0x1cb6438):pass
    elif address==0x15833c4:result=3
    elif address in (0x189dfa0,0x1897d14,0x1897f00,0x18970d8,0x18980e4,0x18a70c4):result=sorted_arrays[current_team]
    elif address==0x24044a4:w32(reg('X0'),reg('W1'));w32(reg('X0')+4,reg('W2'))
    elif address==0x146caa4:result=reg('X0')+0x100
    elif address==0x1471498:result=reg('X0')+0x200
    elif address==0x1472858:result=reg('X0')+0x300
    elif address==0x146d3f4:result=reg('X0')+0x400
    elif address==0x1468bc8:result=reg('X0')+0x500
    elif address in (0x1583550,0x158357c):result=i32(reg('W1'))*24
    elif address==0x145f3f4:result=(reg('W0')+2)%4
    elif address==0x1a6a12c:boards[reg('X0')][reg('W1')]=i32(reg('W2'))
    elif address==0x146f668:result=1
    elif address==0x146f6e4:events.append(('visible',reg('X0')))
    elif address==0x146ee7c:result=0
    elif address==0x14c3f98:events.append(('clear_commands',reg('X0')-0x500))
    elif address==0x1475b3c:result=world
    elif address==0x1500dd4:result=reg('W1')*10000+reg('W2')
    elif address==0x191e7c8:events.append(('cell_event',reg('X2'),reg('W1'),reg('W3')))
    elif address==0x15835a8:
        e=reg('X1');events.append(('decide',e,tuple(tuple(sorted(boards[x].items())) for x in entities)))
        result=3 if boards[e][7]==0 else 1
    elif address==0x1583b48:
        e=reg('X2');boards[e][5]=reg('W1');events.append(('state',e,reg('W1')))
    else:raise AssertionError(hex(address))
    machine.reg_write(UC_ARM64_REG_X0,int(result)&0xffffffffffffffff);machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
checks=0
for permutation in ((0,1,2),(2,0,1),(1,2,0)):
    for team in range(2):
        for index,source_index in enumerate(permutation):w64(sorted_arrays[team]+0x20+index*8,entities[team*3+source_index])
    boards={e:{99:777} for e in entities};events=[]
    for e in entities:
        w32(e+0x110,100);w32(e+0x114,200);w64(e+0x228,0x123456)
        uc.mem_write(e+0x210,struct.pack('<3f',9.,8.,7.));uc.mem_write(e+0x310,struct.pack('<3f',6.,5.,4.));w64(e+0x558,e)
    try:run(0x1582a88,x0=system,x1=teams)
    except Exception:
        print(hex(reg("PC")),hex(reg("X0")),hex(reg("X8")));raise
    expected_order=[entities[team*3+i] for team in range(2) for i in permutation]
    assert [event[1] for event in events if event[0]=='decide']==expected_order
    assert [event[1] for event in events if event[0]=='cell_event']==expected_order
    for team in range(2):
        assert struct.unpack('<3Q',uc.mem_read(arrays[team]+0x20,24))==tuple(entities[team*3:team*3+3])
        for index,source_index in enumerate(permutation):
            e=entities[team*3+source_index]
            assert struct.unpack('<Q',uc.mem_read(e+0x228,8))[0]==0
            assert struct.unpack('<3f',uc.mem_read(e+0x210,12))==(float(index*24),0.,96. if team==0 else 72.)
            assert struct.unpack('<3f',uc.mem_read(e+0x310,12))==(0.,0.,0.)
            assert boards[e]=={99:777,4:0,6:team,7:index,8:0,5:3 if index==0 else 1}
    # Team0's initial decisions occur before team1's positioning/BB reset.
    first=next(event for event in events if event[0]=='decide')
    assert all(dict(record)=={99:777} for record in first[2][3:])
    portable={e:dict(id=e,board={99:777},cell=[100,200],parent=0x123456,
                    position=[9.,8.,7.],speed=[6.,5.,4.],direction=99,
                    invisible=True,human=False) for e in entities}
    portable_events=[]
    def remove_invisible(unit):
        unit['invisible']=False;portable_events.append(('visible',unit['id']))
    def clear_commands(unit):portable_events.append(('clear_commands',unit['id']))
    def cell_changed(unit,old):portable_events.append(('cell_event',unit['id'],40,old[0]*10000+old[1]))
    def decide(unit):
        portable_events.append(('decide',unit['id'],tuple(tuple(sorted(portable[e]['board'].items())) for e in entities)))
        return 3 if unit['board'][7]==0 else 1
    def change_state(unit,state):
        unit['board'][5]=state;portable_events.append(('state',unit['id'],state))
    initialize_fighter_teams(
        [[portable[e] for e in entities[t*3:t*3+3]] for t in range(2)],
        [[dict(incomingIndex=source,grid=index,cell=[index,4 if t==0 else 3])
          for index,source in enumerate(permutation)] for t in range(2)],
        decide,change_state,cell_changed,remove_invisible,clear_commands,
        lambda unit: (_ for _ in ()).throw(AssertionError('Unexpected Human')))
    assert portable_events==events,(portable_events,events)
    for e in entities:
        assert portable[e]['board']==boards[e]
        assert tuple(portable[e]['position'])==struct.unpack('<3f',uc.mem_read(e+0x210,12))
        assert tuple(portable[e]['speed'])==struct.unpack('<3f',uc.mem_read(e+0x310,12))
        assert portable[e]['direction']==r32(e+0x410)
    checks+=1
report=dict(nativeTwoTeamInitializationChecks=checks,
    stubbedCallAddresses=sorted(hex(address) for address in stubbed),
    scope='Original both InitFighters overloads and grid arithmetic; supplied LINQ permutation, component getters, coordinate conversions, initial decisions and state transitions',
    findings=['Formation array is temporary; original roster arrays remain unchanged',
              'Each team finishes placement and initial state decisions before next team initializes',
              'Position parent cleared, positions/speeds reset, Invisible removed, commands cleared; unrelated BB keys retained',
              'No SkillComponent address appears in the executed call set, so InitFighters cannot reset invokingSkills',
              'Human branch is gated by Entity.get_hasHuman 0x146ee7c and calls ChangeWeaponImage/ChangeShieldImage/SetHumanImgs only; its exact writes and RNG-free execution are covered by check_combat_human_image_init.py'],
    limitations=['Sort delegates supplied; the Human equipment/image branch is exercised natively by check_combat_human_image_init.py, not in this two-team fixture; initial state decision/entry side effects not executed'])
(EVIDENCE/'fighter-initialization-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');print(json.dumps(report))
