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

for flag in [0x316ab85, 0x316b1cb, 0x316b1ce, 0x316b1da, 0x316b1c2, 0x316ae69, 0x316b1d3, 0x316b1fe]:
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
test_change_state = False

def external(machine, address, size, user):
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
        commands.pop(0); result = 0
    elif address == 0x18c4a54: result = int(not commands)
    elif address == 0x1deb9a8: result = commands[0]
    elif address == 0x1a6a108: result = board[reg('W1')]
    elif address in [0x1a6a12c, 0x1a6a36c]:
        board[reg('W1')] = i32(reg('W2')); result = 0
    elif address == 0x147e7e0: result = int(destroyed)
    elif address in [0x1471200, 0x14c89d0]: result = params
    elif address == 0x13eb3c8: result = max(i32(reg('W0')), i32(reg('W1')))
    elif address == 0x1583b48:
        events.append(('state', reg('W1'))); result = 0
    elif address == 0x1b114c8:
        entering = reg('X0') == 0x1000b100
        events.append(('dispatch', 'enter' if entering else 'exit', reg('W1')))
        result = 0x1000c000
        w64(result+0x40, 0); w64(result+0x18, 0x30000100); w64(result+0x28, 0)
    elif address == 0x30000100: result = 0 # state handlers stubbed in ChangeState test
    else: raise AssertionError(hex(address))
    machine.reg_write(UC_ARM64_REG_X0, result & 0xffffffffffffffff)
    machine.reg_write(UC_ARM64_REG_PC, reg('LR'))

uc.hook_add(UC_HOOK_CODE, external)
from combat_commands import execute_skill_queue
from elftools.elf.elffile import ELFFile
native_ranges.append((0x1481e54,0x1482398))
uc.mem_write(0x316ab69,b'\x01')
for got in (0x2f5f410,0x2f5fb00,0x2f5dd20):w64(got,0x10001000)
w64(0x10001000,0x10011000);w32(0x100110e0,1)
# Original switch table for opcode29; use the ELF load mapping.
uc.mem_map(0x770000,0x3000)
with (EVIDENCE.parent/'G2.1/39257e72291d/inputs/libil2cpp.so').open('rb') as f:
    elf=ELFFile(f);addr=0x77185e
    seg=next(s for s in elf.iter_segments() if s['p_type']=='PT_LOAD' and s['p_vaddr']<=addr< s['p_vaddr']+s['p_filesz'])
    f.seek(seg['p_offset']+addr-seg['p_vaddr']);uc.mem_write(addr,f.read(66))
w64(ai+0x70,0x10007600)
checks=0
for count in (1,2,5):
 for completed_heads in range(4):
  for final_tick in (0,1,11):
    duration=19 if count==1 else 19+5*count
    commands=[];model=[]
    for i in range(completed_heads+1):
        ptr=0x10020000+i*0x100
        done=i<completed_heads
        ct,ci=(duration,count) if done else (final_tick,0)
        commands.append(ptr);w32(ptr+0x18,7)
        uc.mem_write(ptr+0x20,struct.pack('<7i',29,123,0,0,ct,duration,ci))
        model.append(dict(opcode=29,tick=ct,duration=duration,use_index=ci))
    original_pointers=list(commands)
    w32(skill+0x34,count);w32(skill+0x4c,12);w32(animation+0x10,1);w32(seb+0x18,17)
    tick=0;events=[]
    try:run(0x1481e54,x0=entity)
    except Exception:
        print(hex(reg("PC")), hex(reg("X0")), hex(reg("X8")));raise
    expected_events=[];model_animation=dict(rate=1,frame=17)
    execute_skill_queue(model,lambda c:dict(count=count,motion=12),model_animation,
        lambda motion:expected_events.append(('animation',0,motion)),
        lambda c,index:expected_events.append(('use',0,index)))
    assert events==expected_events,(count,completed_heads,final_tick,events,expected_events)
    assert len(commands)==len(model)
    for ptr,c in zip(commands,model):assert (r32(ptr+0x30),r32(ptr+0x38))==(c['tick'],c['use_index'])
    assert (r32(animation+0x10),r32(seb+0x18))==(model_animation['rate'],model_animation['frame'])
    checks+=1
report=dict(nativeSkillQueueChecks=checks,scope='Original AISystem dispatch plus ScrSkill with original opcode switch table; completed heads followed by a live command',
            limitations=['Only opcode29; collection/component/target lookup, animation changes and UseSkill effects stubbed'])
(EVIDENCE/'command-queue-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');print(json.dumps(report))
