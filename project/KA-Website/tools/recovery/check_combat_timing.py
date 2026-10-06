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
        events.append(('remove', tick)); result = 0
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
counts = dict(skillCommands=0, exitHandlers=0, hpApplication=0, stateTransitions=0)
traces = []
for hit_count in range(1, 11):
    duration = 19 if hit_count == 1 else 19 + 5*hit_count
    uc.mem_write(command+0x20, struct.pack('<7i', 29, 123, 0, 0, 0, duration, 0))
    w32(skill+0x34, hit_count); w32(skill+0x4c, 12); w32(animation+0x10, 1)
    events = []
    for tick in range(duration+1):
        completed = run(0x148a950, x0=entity, x1=command)
        assert completed == int(tick == duration), (hit_count, tick, completed)
    expected_ticks = [11] if hit_count == 1 else [1 + 5*i for i in range(hit_count)]
    assert [event[1] for event in events if event[0] == 'use'] == expected_ticks
    assert [event[2] for event in events if event[0] == 'use'] == list(range(hit_count))
    assert events[-1] == ('remove', duration)
    assert r32(animation+0x10) == 1
    traces.append(dict(count=hit_count, useCommandTicks=expected_ticks, removedAtCommandTick=duration,
                       animationEvents=[event for event in events if event[0]=='animation']))
    counts['skillCommands'] += 1

for gauge in [0, 1, 20, 1000]:
    board = {8:gauge, 17:31, 16:123}; events=[]
    run(0x1585a18, x0=system, x1=entity)
    assert board == {8:gauge, 17:31, 16:123}
    counts['exitHandlers'] += 1
    run(0x1585d2c, x0=system, x1=entity)
    assert board == {8:0, 17:-1, 16:-1}
    counts['exitHandlers'] += 1

w32(result_array+0x18, 1); w64(result_array+0x28, entity)
for hit in [False, True]:
    for destroyed in [False, True]:
        for damage in [0, 1, 99, 100, 101]:
            w32(params+0x14, 100); w32(params+0x18, 100)
            uc.mem_write(result_array+0x30, bytes([int(hit)]))
            w32(result_array+0x34, damage); events=[]
            run(0x1587d60, x0=system, x1=result_array)
            applies = hit and not destroyed
            assert r32(params+0x14) == (max(0,100-damage) if applies else 100)
            assert events == ([('state',6)] if applies else [])
            counts['hpApplication'] += 1

test_change_state = True
for old in [3,4,5,6]:
    for frame in [0,10,11,19]:
        board={4:frame,5:old,8:57}; events=[]
        run(0x1583b48, x0=system, w1=6, x2=entity)
        assert board == {4:0,5:6,8:57}
        assert events == [('dispatch','exit',old),('dispatch','enter',6)]
        counts['stateTransitions'] += 1

# Native defeat predicate uses state, not HP. Native knockdown lasts through frame100.
test_change_state = False
counts['annihilationPredicate'] = 0
counts['knockdownTicks'] = 0
counts['queueColumnPredicate'] = 0
for state in range(12):
    board = {5:state}
    assert run(0x14f3554, x1=entity) == int(state == 8)
    counts['annihilationPredicate'] += 1
for tick in range(1, 105):
    board = {4:tick}; events=[]; w32(0x1000d010, 2)
    run(0x1586954, x0=system, x1=entity)
    expected = [('animation', tick, 7)] if tick == 21 else [('state',8)] if tick >= 101 else []
    assert events == expected, (tick,events)
    assert r32(0x1000d010) == (3 if tick <= 20 else 2)
    counts['knockdownTicks'] += 1
for hp in [-1,0,1,100]:
    for grid in range(-10,40):
        for column in range(5):
            w32(params+0x14,hp); w32(system+0x10,column); board={7:grid}
            remainder = grid - int(grid/5)*5
            assert run(0x15899bc,x0=system,x1=entity) == int(hp>0 and remainder==column)
            counts['queueColumnPredicate'] += 1

report = dict(status='Isolated native methods; synthetic state/callee stubs, not full battle replay',
              checks=counts, skillCommandTraces=traces,
              limits=['UseSkill, animation switching, entity/blackboard access and queue removal are stubbed.',
                      'ScrSkill tests certify scheduling/counters, not damage or MP outcomes.',
                      'OnAttack tests use native HP subtraction with ClampMin stubbed; ChangeState is recorded.',
                      'ChangeState tests stub enter/exit delegates; separate original exit-handler tests cover gauge clearing.',
                      'No wall-clock cadence, complete world dispatch order or original-game capture is validated.'])
(EVIDENCE/'timing-checks.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(counts))
