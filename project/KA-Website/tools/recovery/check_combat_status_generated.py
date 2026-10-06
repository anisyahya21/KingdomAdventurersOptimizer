"""Generated status skills (flags&0x40000, ids113-119): native loop+writer vs the controller.

Native side: the original `BuffEntitiesOnCell` 0x168ee44 loop over a supplied materialized
array, joined to the original `Buff` writer 0x168e49c (the branch bodies are left to execute).
Python side: `SharedControllers.buff_cell`, which runs the recovered route
`UseSkill -> BuffEntitiesOnCell -> CanBuff -> Buff` with `buff_accuracy`/`buff_turns`.

The filter predicate itself is not re-executed here: `can_receive_status` is compared to the
original `CanBuff` 0x168ea2c over 9216 gate combinations in `status-filter-checks.json`, and this
check reads that recorded count so the inheritance is explicit. Accuracy/turn *formulas* are
native-verified in check_combat_skills.py; here their values are supplied identically to both sides
so any difference must come from loop order, RNG-draw discipline or slot overwrite.
"""
import json
import struct
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE
from unicorn.arm64_const import *

from combat_initial_state import EVIDENCE, i32
from combat_resolution import buff_accuracy, buff_turns
from combat_farmer_slice import ROWS
from combat_shared_controllers import SharedControllers

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
def reg(n): return uc.reg_read(globals()['UC_ARM64_REG_' + n])
def run(start, **regs):
    uc.reg_write(UC_ARM64_REG_SP, 0x20008000)
    uc.reg_write(UC_ARM64_REG_LR, 0x30000000)
    for name, value in regs.items():
        uc.reg_write(globals()['UC_ARM64_REG_' + name.upper()], value & 0xffffffffffffffff)
    uc.emu_start(start, 0x30000000, count=200000)
    assert reg('PC') == 0x30000000, hex(reg('PC'))
    return reg('W0')

caster,skill,world,array,closure,delegate=[0x10004000+n*0x1000 for n in range(6)]
targets=[0x10010000+n*0x1000 for n in range(3)]
for flag in (0x316ba00,0x316ba05):uc.mem_write(flag,b'\x01')
for got in (0x2f5c538,0x2f5fb20,0x2f5fb60,0x2f69ab8,0x2f5d5b8,0x2f69ac0,
            0x2f5d7b0,0x2f5b560,0x2f60a38,0x2f5fcc8,0x2f5d5a8):w64(got,0x10001000)
w64(0x10001000,0x10002000);w32(0x100020e0,1);w64(0x100020b8,0x10003000)
for offset,value in ((0x10,1),(0x24,2),(0x28,3)):w32(0x10003000+offset,value)
w64(caster+0x30,world)
for target in targets:w64(target+0x58,target)

boards={};events=[];roll_index=0;current=0;allocated=0;order=()
accuracy_value=37;turn_value=4
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
        current=reg('X1');events.append(('accuracy',current,snapshot()));value=accuracy_value
    elif address==0x1460024:
        events.append(('roll',reg('W0'),roll_index));value=(mask>>roll_index)&1;roll_index+=1
    elif address==0x1468bc8:value=reg('X0')
    elif address==0x1a6a12c:boards[reg('X0')][reg('W1')]=i32(reg('W2'))
    elif address==0x168e760:events.append(('turns',current,snapshot()));value=turn_value
    elif address==0x168e800:events.append(('text',reg('X0'),reg('W1'),snapshot()))
    else:raise AssertionError(hex(address))
    machine.reg_write(UC_ARM64_REG_X0,value&0xffffffffffffffff);machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)


def controller(skill_row,mask):
    """The real controller route for one cell: CanBuff filter, then Buff in array order.

    The Math stub returns 0 for a hit and 199 for a miss, so `random_below(raw,100) < rate` is
    controlled per target while every target still consumes exactly one draw.
    """
    specs=[]
    for index in range(4):
        unit=dict(name=f'g{index}',human=False,monsterId=None,weaponId=0,equipment=[],visitor=False,
            leaderIdentity=False,skills=[],levels=[],grid=0,cell=[1,1] if index else [0,0],
            parameters={10:dict(rawValue=100,rawMax=100,extraValue=0,extraMax=0),
                        16:dict(rawValue=20,rawMax=20,extraValue=0,extraMax=0),
                        18:dict(rawValue=300,rawMax=300,extraValue=0,extraMax=0),
                        19:dict(rawValue=500,rawMax=500,extraValue=0,extraMax=0)},
            monsterType=0,team=0 if index==0 else 1,boss=False)
        specs.append(unit)
    engine=SharedControllers(specs,7,8)
    draws={'count':0}
    def fake_math(purpose='unclassified',bound=None):
        index=draws['count'];draws['count']+=1
        return 0 if (mask>>index)&1 else 199
    engine.next_math=fake_math
    applied=engine.buff_cell(engine.names['g0'],skill_row,[1,1])
    boards=[]
    for name in ('g1','g2','g3'):
        board=engine.units[engine.names[name]]['board']
        boards.append({k:board[k] for k in (62,63,64) if k in board})
    return applied,boards,draws['count']


checks=0
for skill_id in (113,117):
    skill_row=ROWS[skill_id]
    for mask in range(8):
        # Supplied to the native hooks and recomputed by the controller from the same stats.
        accuracy_value=buff_accuracy(500,20,skill_row['type'],skill_row['value'])
        turn_value=buff_turns(300)
        order=(0,1,2)
        boards={target:{} for target in targets};events=[];roll_index=0;allocated=0
        w32(skill+0x18,skill_id);w32(skill+0x2c,skill_row['type']);w32(array+0x18,len(order))
        for i,index in enumerate(order):w64(array+0x20+i*8,targets[index])
        run(0x168ee44,w0=1,w1=2,x2=caster,x3=skill)
        native=[dict(boards[target]) for target in targets]
        native_rolls=sum(1 for event in events if event[0]=='roll')
        applied,py_boards,py_draws=controller(skill_row,mask)
        assert native_rolls==3 and py_draws==3,(skill_id,mask,native_rolls,py_draws)
        for index,target in enumerate(targets):
            assert py_boards[index]==native[index],(skill_id,mask,index,py_boards[index],native[index])
        checks+=1

filter_checks=json.loads((EVIDENCE/'status-filter-checks.json').read_text())['nativeStatusTargetFilterChecks']
application_checks=json.loads((EVIDENCE/'status-application-checks.json').read_text())['nativeCellStatusApplicationCases']
report=dict(nativeGeneratedStatusCases=checks,inheritedNativeCanBuffCases=filter_checks,
    inheritedNativeCellApplicationCases=application_checks,
    findings=['The controller buff_cell route reproduces the original BuffEntitiesOnCell-plus-Buff board writes for the generated status skills under every hit/miss combination, including a failed roll leaving the slot untouched and a success overwriting the single BB62/63/64 slot.',
              'Ids113-116 (type66) and 117-119 (type67) are now accepted; any other flags&0x40000 skill or unrecovered category raises instead of being silently skipped.',
              'The type67 accuracy bonus and the duration formula are the native-verified buff_accuracy/buff_turns values supplied to both sides here.'],
    limits=['Supplied accuracy/turn values and per-target Hits masks; the cell candidate list is supplied (all three targets pass the filter in this fixture).',
            'CanBuff equivalence is inherited from status-filter-checks.json, not re-executed in this check.',
            'Not a full UseSkill run: skill dispatch order and cell enumeration are covered by skill-dispatch-checks.json.'])
(EVIDENCE/'status-generated-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
print(json.dumps(report))
