"""Compare recovered arithmetic with original ARM64 instructions in Unicorn.
External getters/allocation are stubbed for priority tests. This is NOT a game
runtime trace. Run recover_special_combat.py first; requires unicorn 2.1.4.
"""
import itertools
import json
import random
import struct
from pathlib import Path
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE
from unicorn.arm64_const import *
from combat_initial_state import EVIDENCE, PARAM_IDS, formation, i32, monster_parameter, priority

ROOT = EVIDENCE.parent.parent
binary = (ROOT / 'RE-evidence/G2.1/39257e72291d/inputs/libil2cpp.so').read_bytes()
audit = json.loads((EVIDENCE / 'audit.json').read_text())
machine = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
machine.mem_map(0x1000000, 0x2300000)
machine.mem_map(0x10000000, 0x10000)
machine.mem_map(0x20000000, 0x10000)
machine.mem_map(0x30000000, 0x1000)
for entry in audit['methods']:
    address, end, offset = (int(entry[k], 16) for k in ('rva', 'end', 'offset'))
    machine.mem_write(address, binary[offset:offset+end-address])


def w32(address, value):
    machine.mem_write(address, struct.pack('<I', value & 0xffffffff))


def w64(address, value):
    machine.mem_write(address, struct.pack('<Q', value))


def run(start, end=0x30000000, **registers):
    # Also supply the saved LR for slices entered after a native prologue.
    w64(0x20008000, 0x30000000)
    machine.reg_write(UC_ARM64_REG_SP, 0x20008000)
    machine.reg_write(UC_ARM64_REG_LR, 0x30000000)
    for name, value in registers.items():
        machine.reg_write(globals()['UC_ARM64_REG_' + name.upper()], value & 0xffffffffffffffff)
    machine.emu_start(start, end, count=500)
    assert machine.reg_read(UC_ARM64_REG_PC) == end
    return i32(machine.reg_read(UC_ARM64_REG_W0))


data = json.loads((EVIDENCE / 'encounters.json').read_text())
checks = dict(parameter=0, priority=0, grid=0, roster=0)
seed = random.Random(20260912)
levels = [-1, 0, 1, 2, 15, 30, 99, 100, 101, 999, 1000, 1001,
          4999, 5000, 5001, 9999, 10000, 10001]
levels += [seed.randrange(1, 10001) for _ in range(32)]
curves = [c for m in data['monsters'] for c in m['parametersRaw']]
curves += [[100, 1, -10, -100], [0, 100000000, 200000000, 300000000]]
for curve in curves:
    machine.mem_write(0x10000020, struct.pack('<4i', *curve))
    for level in levels:
        # Start after lookup/length checks; stop before shared epilogue.
        actual = run(0x162f628, 0x162f680, x8=0x10000000, w19=level)
        expected = monster_parameter(curve, level)
        assert actual == expected, (curve, level, actual, expected)
        checks['parameter'] += 1

# Metadata class and static priority array for the actual projection function.
machine.mem_write(0x316b1fc, b'\x01')
w64(0x2f5fd60, 0x10001000)
w64(0x10001000, 0x10002000)
w32(0x100020e0, 1)
w64(0x100020b8, 0x10003000)
w64(0x10003000, 0x10004000)
w32(0x10004018, 6)
machine.mem_write(0x10004020, struct.pack('<6i', 0, 3, 6, 9, 10, 13))
w64(0x2f64cf8, 0x10001100)
w64(0x10001100, 0x10002100)
case = {}


def external(uc, address, size, user):
    if 0x15897cc <= address < 0x1589928:
        return
    results = {0x1472554: int(case['value'] is not None),
               0x14724cc: 0x10005000,
               0x14d1490: 0x10006000 if case['value'] is not None else 0,
               0x1473810: int(case['visitor']), 0x1470c84: int(case['monster']),
               0x1470f10: int(case['owner_player']), 0x12d23b8: 0x10007000,
               0x2691aa0: uc.reg_read(UC_ARM64_REG_X0)}
    assert address in results, hex(address)
    if address == 0x14d1490:
        assert uc.reg_read(UC_ARM64_REG_W1) == 60
    uc.reg_write(UC_ARM64_REG_X0, results[address])
    uc.reg_write(UC_ARM64_REG_PC, uc.reg_read(UC_ARM64_REG_LR))


hook = machine.hook_add(UC_HOOK_CODE, external)
for value, visitor, leader, monster, owner in itertools.product([None, 0, 1, 4], [False, True], [False, True], [False, True], [False, True]):
    case = dict(value=value, visitor=visitor, monster=monster, owner_player=owner)
    w32(0x10006030, value or 0)
    w64(0x10000218, 0x10000100 if leader else 0x10000800)
    run(0x15897cc, x0=0x10000200, x1=0x10000100)
    actual = struct.unpack('<i', machine.mem_read(0x10007010, 4))[0]
    expected = priority(value, visitor=visitor, leader_identity=leader, monster=monster, owner_player=owner)
    assert actual == expected, (case, leader, actual, expected)
    checks['priority'] += 1
machine.hook_del(hook)

for count in [0, 1, 4, 5, 6, 9, 10, 11, 15, 16, 20, 21, 100]:
    # Execute the arithmetic plus its native ClampMin tail target.
    offset = run(0x1588298, w19=count)
    assert offset == max(3, count // 5 + 1)
    checks['grid'] += 1
    for index in range(101):
        assert run(0x158708c, w0=index) == index // 5
        assert run(0x15870a8, w0=index) == index % 5
        checks['grid'] += 2
        for direction, dy in [(0, -1), (2, 1)]:
            w32(0x10000914, dy)
            run(0x15834ec, 0x158351c, x8=0x10000900, w19=index, w20=offset, w21=direction)
            actual = [i32(machine.reg_read(r)) for r in [UC_ARM64_REG_W1, UC_ARM64_REG_W2]]
            expected = [index % 5, offset + (direction == 2) + dy * (index // 5)]
            assert actual == expected
            checks['grid'] += 1

# Hand-specified behavioral fixtures: priorities outrank defense, pets do not
# inherit the owner's placement skill, defense ties keep incoming order.
members = [dict(id='normal-strong', effectiveDefense=999),
           dict(id='backup', effectiveDefense=9999, formationValue=4),
           dict(id='pet', effectiveDefense=5000, monster=True),
           dict(id='leading-weak', effectiveDefense=1, formationValue=0),
           dict(id='daring', effectiveDefense=20, formationValue=1),
           dict(id='normal-tie', effectiveDefense=999)]
ordered = formation(members, 0, 6)
assert [r['id'] for r in ordered] == ['leading-weak', 'daring', 'normal-strong', 'normal-tie', 'pet', 'backup']
assert [r['cell'] for r in ordered] == [[0, 4], [1, 4], [2, 4], [3, 4], [4, 4], [0, 5]]
checks['roster'] += 2

snapshots = []
monster_lookup = {m['id']: m for m in data['monsters']}
for encounter in data['encounters']:
    for defeats in [0, 4, 5, 9, 10]:
        level = encounter['levelField'] + defeats // 5
        members = []
        for follower in encounter['followers']:
            members.append(dict(monsterId=follower['monsterId'], rankArgument=level, levelArgument=level, boss=False))
        members.append(dict(monsterId=encounter['bossId'], rankArgument=1, levelArgument=level, boss=True))
        for member in members:
            monster = monster_lookup[member['monsterId']]
            member['creationParameters'] = {str(pid): monster_parameter(curve, level)
                                            for pid, curve in zip(PARAM_IDS, monster['parametersRaw'])}
        snapshots.append(dict(encounterId=encounter['id'], defeatCount=defeats, members=members))

report = dict(status='Isolated native instructions with synthetic inputs; NOT original-game runtime validation',
              engine='Unicorn 2.1.4', checks=checks, sampleFormation=ordered,
              limits=['Priority test stubs entity getters, first type60 skill lookup and allocation.',
                      'Grid test supplies native Direction vectors; it does not run full movement.',
                      'Raw parameter values precede later battle initialization/modifiers.',
                      'Roster fixture tests the research model; no original-game roster was captured.'])
(EVIDENCE / 'initial-state-checks.json').write_text(json.dumps(report, indent=2) + '\n')
(EVIDENCE / 'monster-creation-samples.json').write_text(json.dumps(dict(
    phase='Immediately after World.CreateMonster parameter creation, before battle-world initialization',
    finalCombatStats=False, samples=snapshots), indent=2) + '\n')
print(json.dumps(checks))
