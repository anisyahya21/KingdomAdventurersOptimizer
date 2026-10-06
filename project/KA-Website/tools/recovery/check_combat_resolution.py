"""Original ARM64 primitive comparisons; synthetic state, not full game runs."""
import json
import random
import struct
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE
from unicorn.arm64_const import *
from combat_initial_state import EVIDENCE, i32
from combat_resolution import base_damage, equipment_parameter, hit_rate, random_range, xorshift_next
from combat_resolution import critical_rate, modified_rate, random_below
from elftools.elf.elffile import ELFFile

binary = (EVIDENCE.parent.parent / 'RE-evidence/G2.1/39257e72291d/inputs/libil2cpp.so').read_bytes()
audit = json.loads((EVIDENCE / 'audit.json').read_text())
uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
for base, size in [(0x1000000, 0x2300000), (0x10000000, 0x10000), (0x20000000, 0x10000), (0x30000000, 0x1000)]:
    uc.mem_map(base, size)
for entry in audit['methods']:
    start, end, offset = [int(entry[k], 16) for k in ['rva', 'end', 'offset']]
    uc.mem_write(start, binary[offset:offset+end-start])


def w32(a, n):
    uc.mem_write(a, struct.pack('<I', n & 0xffffffff))


def w64(a, n):
    uc.mem_write(a, struct.pack('<Q', n))


def run(start, end=0x30000000, **regs):
    uc.reg_write(UC_ARM64_REG_SP, 0x20008000)
    uc.reg_write(UC_ARM64_REG_LR, 0x30000000)
    w64(0x20008000, 0x30000000)
    for name, n in regs.items():
        uc.reg_write(globals()['UC_ARM64_REG_' + name.upper()], n & 0xffffffffffffffff)
    uc.emu_start(start, end, count=1000)
    assert uc.reg_read(UC_ARM64_REG_PC) == end
    return i32(uc.reg_read(UC_ARM64_REG_W0))


counts = dict(range=0, damage=0, hitRate=0, equipment=0, xorshift=0,
              criticalRate=0, rateModifier=0, randomBelow=0, attackPrefix=0)
rng = random.Random(20260913)
raws = [-2147483648, -101, -1, 0, 1, 9, 20, 40, 2147483647]
raws += [rng.randrange(-2**31, 2**31) for _ in range(100)]
for low, high in [(1, 10), (70, 90), (80, 120), (0, 99)]:
    for raw in raws:
        actual = run(0x145fffc, 0x1460010, w0=raw, w19=low, w20=high)
        assert actual == random_range(raw, low, high)
        counts['range'] += 1

# Original damage function, with only Random.Rand replaced by scripted draws.
# All arithmetic, branch decisions and ClampMin run as original instructions.
uc.mem_write(0x316b9f1, b'\x01')
for got, pointer, cls in [(0x2f5c538, 0x10001000, 0x10002000), (0x2f5b570, 0x10001100, 0x10002100)]:
    w64(got, pointer); w64(pointer, cls); w32(cls + 0xe0, 1)
draws = []
calls = []


def external(machine, address, size, user):
    if 0x168c460 <= address < 0x168c588 or 0x16657b0 <= address < 0x16657bc:
        return
    assert address == 0x145ff98, hex(address)
    low = i32(machine.reg_read(UC_ARM64_REG_W0))
    high = i32(machine.reg_read(UC_ARM64_REG_W1))
    calls.append([low, high])
    result = random_range(draws[len(calls)-1], low, high)
    machine.reg_write(UC_ARM64_REG_W0, result & 0xffffffff)
    machine.reg_write(UC_ARM64_REG_PC, machine.reg_read(UC_ARM64_REG_LR))


hook = uc.hook_add(UC_HOOK_CODE, external)
pairs = [(a, d) for a in [0, 1, 5, 6, 10, 100, 10000, 2**31-1]
         for d in [0, 1, 5, 10, 100, 10000, 2**31-1]]
pairs += [(rng.randrange(0, 100000), rng.randrange(0, 100000)) for _ in range(200)]
for attack, defense in pairs:
    for samples in [[0, 0, 0], [40, 20, 9], [20, 10, 5], [-1, -1, -1], [-2147483648, 2147483647, -9]]:
        draws, calls = samples, []
        actual = run(0x168c460, w0=attack, w1=defense)
        consumed = []
        def draw():
            n = samples[len(consumed)]; consumed.append(n); return n
        expected = base_damage(attack, defense, draw)
        assert (actual, len(calls)) == (expected, len(consumed)), (attack, defense, samples, actual, expected)
        assert calls == [[80, 120], [70, 90]] + ([[1, 10]] if len(consumed) == 3 else [])
        counts['damage'] += 1
uc.hook_del(hook)

for _ in range(1000):
    d, a, l = [rng.randrange(-10000, 10001) for _ in range(3)]
    actual = run(0x168c5d8, w19=d, w20=a, w21=l)
    assert actual == hit_rate(d, a, l), (d, a, l, actual)
    counts['hitRate'] += 1

w64(0x10000080, 0x10000200)
w32(0x10000218, 14)
for index in range(14):
    w64(0x10000220 + index * 8, 0x10000400)
w32(0x10000418, 2)
for base, growth in [(0, 0), (10, 2), (-10, -2), (100, -3), (2**31-1, 100000)]:
    w32(0x10000420, base); w32(0x10000424, growth)
    for level in [1, 2, 99, 100, 5000, 100000]:
        actual = run(0x16204c0, x0=0x10000000, w1=14, w2=level)
        assert actual == equipment_parameter([base, growth], level)
        counts['equipment'] += 1
assert run(0x16204c0, x0=0x10000000, w1=9, w2=1) == 0
counts['equipment'] += 1

state = (123456789, 362436069, 521288629, 88675123)
for _ in range(1000):
    uc.mem_write(0x10000020, struct.pack('<4I', *state))
    actual = run(0x22a53dc, x0=0x10000000)
    actual_state = struct.unpack('<4I', uc.mem_read(0x10000020, 16))
    state, expected = xorshift_next(state)
    assert (actual, actual_state) == (expected, state)
    counts['xorshift'] += 1

# Map original float constants used by Graph.Easing. Its amplitude is zero in
# CalcCriticalRate; the sine call is replaced by finite zero, all other arithmetic
# executes natively. Critical tables below are the immediate stores in Define.cctor.
uc.mem_map(0x750000, 0x10000)
with (EVIDENCE.parent / 'G2.1/39257e72291d/inputs/libil2cpp.so').open('rb') as handle:
    elf = ELFFile(handle)
    for address in [0x753504, 0x753590]:
        segment = next(s for s in elf.iter_segments()
                       if s['p_vaddr'] <= address < s['p_vaddr'] + s['p_filesz'])
        offset = segment['p_offset'] + address - segment['p_vaddr']
        uc.mem_write(address, binary[offset:offset+4])
uc.mem_write(0x316b9f3, b'\x01')
w64(0x2f5d2b8, 0x10003000); w64(0x10003000, 0x10003100)
w32(0x100031e0, 1); w64(0x100031b8, 0x10003300)
for index, pair in enumerate([(10, 20), (21, 35), (36, 75)]):
    address = 0x10003600 + index * 0x100
    w64(0x10003300 + 0x190 + index * 8, address)
    w32(address + 0x18, 2)
    w32(address + 0x20, pair[0]); w32(address + 0x24, pair[1])


def sine(machine, address, size, user):
    machine.reg_write(UC_ARM64_REG_S0, 0)
    machine.reg_write(UC_ARM64_REG_PC, machine.reg_read(UC_ARM64_REG_LR))


hook = uc.hook_add(UC_HOOK_CODE, sine, begin=0x23dd630, end=0x23dd630)
for luck in list(range(-1, 100001)) + [-2**31, 2**31-1]:
    assert run(0x168c65c, w0=luck) == critical_rate(luck), luck
    counts['criticalRate'] += 1
uc.hook_del(hook)

for rate in [0, 5, 10, 35, 75, 97, 100]:
    for value in [-100, -1, 0, 1, 25, 50, 100, 200, 2**31-1]:
        w32(0x10000030, value)
        for evasion, start, end in [(False, 0x168cd8c, 0x168cdb0),
                                    (True, 0x168cf24, 0x168cf4c)]:
            run(start, end, x0=0x10000000, w19=rate)
            assert i32(uc.reg_read(UC_ARM64_REG_W19)) == modified_rate(rate, value, evasion=evasion)
            counts['rateModifier'] += 1
for raw in raws:
    assert run(0x145ff70, 0x145ff80, w20=raw, w19=100) == random_below(raw, 100)
    counts['randomBelow'] += 1

# Original Attack prefix through damage and terrain adjustment only. Stub the
# subroutines to isolate order/branches, not to certify full skill resolution.
uc.mem_write(0x316b9fd, b'\x01')
prefix_calls = []
answers = {}


def attack_external(machine, address, size, user):
    if 0x168d1e8 <= address < 0x168d3f4:
        return
    assert address in answers, hex(address)
    prefix_calls.append(address)
    if address == 0x168cf80:
        assert machine.reg_read(UC_ARM64_REG_W0) == int(crit)
        assert machine.reg_read(UC_ARM64_REG_W3) == int(skill_type == 1)
    machine.reg_write(UC_ARM64_REG_W0, answers[address])
    machine.reg_write(UC_ARM64_REG_PC, machine.reg_read(UC_ARM64_REG_LR))


hook = uc.hook_add(UC_HOOK_CODE, attack_external)
for crit in [False, True]:
    for hit in [False, True]:
        for skill_type in [None, 0, 1, 2]:
            for terrain in [False, True]:
                prefix_calls = []
                answers = {0x147e7e0: 0, 0x168cc54: int(crit), 0x168cde4: int(hit),
                           0x168cf80: 101, 0x168e214: int(terrain)}
                w32(0x1000002c, skill_type or 0)
                run(0x168d1e8, 0x168d3f4, x0=0x10000100, x1=0x10000200,
                    x2=0 if skill_type is None else 0x10000000, x8=0x10000400)
                expected_calls = [0x147e7e0, 0x168cc54]
                if not crit:
                    expected_calls += [0x168cde4]
                landed = crit or hit
                if landed:
                    expected_calls += [0x168cf80]
                expected_calls += [0x168e214]
                assert prefix_calls == expected_calls
                assert uc.reg_read(UC_ARM64_REG_W28) == int(landed)
                assert uc.reg_read(UC_ARM64_REG_W29) == (151 if terrain else 101) * int(landed)
                counts['attackPrefix'] += 1
uc.hook_del(hook)

report = dict(status='Isolated native code with synthetic inputs; no original-game fight validation',
              checks=counts, engine='Unicorn 2.1.4',
              limits=['Active JRandom backend/seed and complete call order unresolved.',
                      'Damage helper alone excludes skill multipliers, hit/critical selection and final application.',
                      'Equipment tests cover raw growth; affinity wrapper is statically traced, not emulated here.',
                      'Critical-rate tests supply traced constant tables and stub sine at zero amplitude.',
                      'Attack-prefix tests stub callees and stop before reactive skills and final damage application.'])
(EVIDENCE / 'resolution-checks.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(counts))
