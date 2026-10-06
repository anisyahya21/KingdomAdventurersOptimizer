"""Joined native HP interruption, exit/entry handlers and persistent skill command."""
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

for flag in [0x316ab85, 0x316b1cb, 0x316b1ce, 0x316b1da, 0x316b1c2, 0x316ae69, 0x316b1d3, 0x316b1fe,0x316b1cf]:
    uc.mem_write(flag, b'\x01')
for index, got in enumerate([0x2f5b578, 0x2f5b890, 0x2f5fb70, 0x2f5b810,
                             0x2f5fb60, 0x2f5d628, 0x2f5fd88, 0x2f60048, 0x2f64bb0, 0x2f5fd60]):
    pointer, cls = 0x10002000 + index*8, 0x10003000 + index*0x100
    w64(got, pointer); w64(pointer, cls); w32(cls + 0xe0, 1)

entity, command, skill = 0x10004000, 0x10005000, 0x10006000
ai, animation, seb, params = 0x10007000, 0x10007100, 0x10007200, 0x10007300
skill_table, result_array, system = 0x10008000, 0x10009000, 0x1000a000
w64(entity+0x30, 0x10004100)
w32(command+0x18, 7); w32(skill_table+0x18, 1); w64(skill_table+0x20, skill)
w64(ai+0x58, 0x10007400); w64(ai+0x60, 0x10007500)
w64(system+0x30, 0x1000b000); w64(system+0x20, 0x1000b100)
events, board, tick = [], {}, 0
native_ranges = [(0x148a950, 0x148acdc), (0x1585a18, 0x1585a78),
                 (0x1585d2c, 0x1585e34), (0x1587d60, 0x1587e64),
                 (0x16828c8, 0x1682910), (0x22141a8, 0x22141b8), (0x14f3554, 0x14f35c8),
                 (0x1586954, 0x1586a7c), (0x15899bc, 0x1589ab0)]
test_change_state = True

def external(machine, address, size, user):
    global queue_present
    if 0x1585e34<=address<0x1585f04:return
    if any(a <= address < b for a, b in native_ranges): return
    if test_change_state and 0x1583b48 <= address < 0x1583cf0: return
    if address == 0x238ee30: result = 123 # joined target ID; World.GetEntity is stubbed
    elif address == 0x147b328: result = entity
    elif address == 0x161f56c: result = skill_table
    elif address == 0x165dea8:
        events.append(('animation', tick, reg('W1'))); result = 0
    elif address == 0x146b9d8: result = animation
    elif address == 0x14721c8: result = seb
    elif address == 0x1468bc8: result = ai
    elif address == 0x146d3f4: result = 0x1000d000
    elif address == 0x14ce670: result = r32(params+0x14)
    elif address == 0x15e0c2c:
        events.append(('use', tick, reg('W3'))); result = 0
    elif address == 0x14c3eb0:
        queue_present=False;events.append(('remove', tick)); result = 0
    elif address==0x18c4a54:result=int(not queue_present)
    elif address==0x1deb9a8:result=command
    elif address==0x191de04:result=0
    elif address==0x168e214:result=0
    elif address == 0x1a6a108: result = board[reg('W1')]
    elif address in [0x1a6a12c, 0x1a6a36c]:
        board[reg('W1')] = i32(reg('W2')); result = 0
    elif address == 0x1471498: result=0x1000e000
    elif address == 0x147e7e0: result = int(destroyed) if reg('LR')==0x1587df0 else 0
    elif address in [0x1471200, 0x14c89d0]: result = params
    elif address == 0x13eb3c8: result = max(i32(reg('W0')), i32(reg('W1')))
    elif address == 0x1583b48:
        events.append(('state', reg('W1'))); result = 0
    elif address == 0x1b114c8 and reg('X0')==0x1000b200:
        result=0x1000c100;w64(result+0x40,system);w64(result+0x18,0x30000104)
    elif address==0x30000104:
        machine.reg_write(UC_ARM64_REG_X1,result_array);machine.reg_write(UC_ARM64_REG_PC,0x1587d60);return
    elif address == 0x1b114c8:
        entering = reg('X0') == 0x1000b100
        events.append(('dispatch', 'enter' if entering else 'exit', reg('W1')))
        result = 0x1000c000
        w64(result+0x40, system); w64(result+0x18, 0x1585e34 if entering else 0x1585d2c); w64(result+0x28, 0)
    elif address == 0x30000100: result = 0 # state handlers stubbed in ChangeState test
    else: raise AssertionError(hex(address))
    machine.reg_write(UC_ARM64_REG_X0, result & 0xffffffffffffffff)
    machine.reg_write(UC_ARM64_REG_PC, reg('LR'))

uc.hook_add(UC_HOOK_CODE, external)
from elftools.elf.elffile import ELFFile
native_ranges.extend(((0x1583cf8,0x15840dc),(0x1481e54,0x1482398)))
for flag in (0x316b1c1,0x316ab69):uc.mem_write(flag,b'\x01')
for got in (0x2f5f410,0x2f5f980,0x2f5c298,0x2f64bb8,0x2f5fb00,0x2f5dd20):w64(got,0x10001000)
w64(0x10001000,0x10011000);w32(0x100110e0,1)
w64(system+0x10,0x10004100);w64(system+0x28,0x1000b200);w64(system+0x38,0x10012000)
w32(0x10012018,1);w64(0x10012030,0x10012100)
w32(0x10012118,1);w64(0x10012120,entity);w64(ai+0x70,0x10007600)
uc.mem_map(0x770000,0x3000)
with (EVIDENCE.parent/'G2.1/39257e72291d/inputs/libil2cpp.so').open('rb') as f:
    elf=ELFFile(f);addr=0x77185e
    seg=next(s for s in elf.iter_segments() if s['p_type']=='PT_LOAD' and s['p_vaddr']<=addr<s['p_vaddr']+s['p_filesz'])
    f.seek(seg['p_offset']+addr-seg['p_vaddr']);uc.mem_write(addr,f.read(66))

from combat_resolution import apply_fighter_attack_results,subtract_raw_parameter
from combat_states import change_fighter_state,exit_using_skill,enter_damaging
from combat_commands import update_skill_command
from combat_entities import CombatEntities
from combat_shared_resolution import apply_shared_attack_results, execute_shared_skill_commands
from combat_tick import update_fighters
from itertools import product
checks=0
w32(result_array+0x18,1);w64(result_array+0x28,entity)
for hit,destroyed,damage,tick,z in product((False,True),(False,True),(0,101),(0,1,5,6,11,34),(-24.75,0.,96.5,1e12,-1e12,float('inf'),float('-inf'),float('nan'))):
    board={4:13,5:5,8:57,17:31,16:123};events=[]
    w32(params+0x14,100);w32(params+0x18,100)
    uc.mem_write(result_array+0x30,bytes([int(hit)]));w32(result_array+0x34,damage)
    uc.mem_write(0x1000e018,struct.pack('<f',z))
    uc.mem_write(command+0x20,struct.pack('<7i',29,123,0,0,tick,34,1))
    w32(skill+0x34,3);w32(skill+0x4c,12);w32(animation+0x10,4)
    before=bytes(uc.mem_read(command+0x20,28))
    queue_present=True
    run(0x1583cf8,x0=system)
    # The subsequent command phase executes even though HP may now be zero
    # and the fighter has entered Damaging; command target/index remain stored.
    shared=CombatEntities(100,[]);identity=shared.allocate()
    shared.add_component(identity,'AI',dict(board={4:13,5:5,8:57,17:31},long_board={16:123},commands=[],path=[]))
    shared.add_component(identity,'Position',[0.,0.,z,0.,0.,0.,None])
    shared.add_component(identity,'Parameter',dict(parameters={10:dict(rawValue=100,rawMax=100,extraValue=0,extraMax=0)}))
    shared.add_component(identity,'Animation',[4,-1]);shared.add_component(identity,'Seb',[0,0,0,-1])
    model=shared.fighter(identity)
    model_command=dict(opcode=29,target=123,tick=tick,duration=34,use_index=1)
    model['commands'].append(model_command)
    model_animation=model.command_animation()
    expected=[]
    def animation_change(unit,behavior):expected.append(('animation',tick,behavior))
    def exit_handler(unit):
        expected.append(('dispatch','exit',unit['board'][5]));exit_using_skill(unit,animation_change)
    def enter_handler(unit):
        expected.append(('dispatch','enter',unit['board'][5]));enter_damaging(unit,animation_change)
    def change(unit,state):change_fighter_state(model,state,{5:exit_handler},{6:enter_handler})
    def state_update(unit,state):
        # Native fixture supplies target-destroyed only inside OnAttack, after
        # Fighter.Update has already admitted the fighter into this tick.
        shared.objects[identity]['flags']=2 if destroyed else 0
        apply_shared_attack_results(shared,[dict(target=identity,hit=hit,damage=damage)],change)
    def command_update(unit):
        execute_shared_skill_commands(shared,identity,lambda cmd:dict(count=3,motion=12),
                                      animation_change,lambda fighter,cmd,index:expected.append(('use',tick,index)))
        if not unit['commands']:expected.append(('remove',tick))
    update_fighters([[model]],2,state_update,command_update)
    assert events==expected,(hit,destroyed,damage,tick,z,events,expected)
    assert board==model['board']|model['long_board']
    assert r32(params+0x14)==model['parameters'][10]['rawValue']
    assert r32(command+0x30)==model_command['tick']
    assert r32(command+0x38)==model_command['use_index']
    assert r32(animation+0x10)==model_animation['rate']
    checks+=1
report=dict(nativeFighterInterruptionCases=checks, sharedComponentStorage=True,
    scope='Original FighterSystem.Update, synchronous HP interruption with original exit/entry handlers, and original AISystem dispatcher plus ScrSkill',
    limitations=['Component/blackboard/dictionary access, animation changes and UseSkill supplied; one-fighter update callback supplies an incoming result; other state updates and terrain presentation excluded'])
(EVIDENCE/'fighter-interruption-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
print(json.dumps(report))
