"""Execute original cell generators with synthetic IL2CPP objects/allocators."""
import json
import struct
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE
from unicorn.arm64_const import *
from combat_initial_state import EVIDENCE, i32
from combat_geometry import line_cells, battle_band, target_area

binary = (EVIDENCE.parent / 'G2.1/39257e72291d/inputs/libil2cpp.so').read_bytes()
audit = json.loads((EVIDENCE / 'audit.json').read_text())
uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
for base, size in [(0x1000000, 0x2300000), (0x10000000, 0x200000),
                   (0x20000000, 0x10000), (0x30000000, 0x1000)]:
    uc.mem_map(base, size)
for entry in audit['methods']:
    start, end, offset = [int(entry[k], 16) for k in ['rva', 'end', 'offset']]
    uc.mem_write(start, binary[offset:offset+end-start])


def w32(a, n): uc.mem_write(a, struct.pack('<I', n & 0xffffffff))
def w64(a, n): uc.mem_write(a, struct.pack('<Q', n))
def r32(a): return struct.unpack('<i', uc.mem_read(a, 4))[0]
def r64(a): return struct.unpack('<Q', uc.mem_read(a, 8))[0]
def reg(n): return uc.reg_read(globals()['UC_ARM64_REG_' + n])


def run(start, end=0x30000000, **regs):
    uc.reg_write(UC_ARM64_REG_SP, 0x20008000)
    uc.reg_write(UC_ARM64_REG_LR, 0x30000000)
    for name, value in regs.items():
        uc.reg_write(globals()['UC_ARM64_REG_' + name.upper()], value & 0xffffffffffffffff)
    uc.emu_start(start, end, count=100000)
    assert reg('PC') == end, hex(reg('PC'))
    return reg('X0')


heap = 0x10010000
def alloc(size=0x100):
    global heap
    result = heap; heap += (size + 15) & ~15
    assert heap < 0x10200000
    uc.mem_write(result, bytes(size))
    return result


def external(machine, address, size, user):
    if address == 0x12d23b8:
        result = alloc(); w64(result, reg('X0'))
    elif address == 0x12d2214:
        length = reg('W1'); assert length < 10000
        result = alloc(0x20 + length * 8)
        w64(result, reg('X0')); w32(result + 0x18, length)
    elif address == 0x12d22a8:
        result = reg('X0')  # synthetic objects are assignable to these arrays
    elif address in [0x2190f94, 0x145fc10]:
        offset = 0x10 if address == 0x2190f94 else 0
        w32(reg('X0') + offset, reg('W1')); w32(reg('X0') + offset + 4, reg('W2'))
        result = reg('X0')
    elif address == 0x146caa4:
        result = 0x10001000
    elif address == 0x146d3f4:
        result = 0x10001100
    else:
        raise AssertionError(hex(address))
    machine.reg_write(UC_ARM64_REG_X0, result)
    machine.reg_write(UC_ARM64_REG_PC, reg('LR'))


for address in [0x12d23b8, 0x12d2214, 0x12d22a8, 0x2190f94, 0x145fc10,
                0x146caa4, 0x146d3f4]:
    uc.hook_add(UC_HOOK_CODE, external, begin=address, end=address)
for address in [0x316a9aa, 0x316a9ae, 0x316b491]: uc.mem_write(address, b'\x01')
for index, got in enumerate([0x2f5e4a0, 0x2f5e4a8, 0x2f5e4b0, 0x2f5e4b8, 0x2f5e4c0]):
    pointer, cls = 0x10002000 + index * 8, 0x10003000 + index * 0x100
    w64(got, pointer); w64(pointer, cls); w32(cls + 0xe0, 1)

counts = dict(targetArea=0, line=0, bandRectangle=0, nearestSelection=0)
samples = []
for center in [(0, 0), (2, -4)]:
    for size in range(0, 11):
        for include in [False, True]:
            heap = 0x10010000
            result = run(0x145e364, w0=center[0], w1=center[1], w2=size, w3=int(include))
            actual = []
            for index in range(r32(result + 0x18)):
                pair = r64(result + 0x20 + 8 * index)
                point = r64(pair + 0x20)
                actual.append((r32(point + 0x10), r32(point + 0x14)))
            assert actual == target_area(*center, size, include), (center, size, include, actual)
            counts['targetArea'] += 1
            if center == (0, 0) and include and size <= 4:
                samples.append(dict(kind='targetArea', size=size, cells=actual))

# Synthetic direction vectors, matching the previously recovered native table.
w64(0x100034b8, 0x10004000); w64(0x10004010, 0x10004100); w32(0x10004118, 4)
directions = [(0, -1), (1, 0), (0, 1), (-1, 0)]
for index, (dx, dy) in enumerate(directions):
    vector = 0x10004200 + index * 0x100
    w64(0x10004120 + index * 8, vector)
    w32(vector + 0x10, dx); w32(vector + 0x14, dy)
for direction in [0, 2]:
    for x, y in [(0, 0), (2, 6), (4, -2)]:
        for size in range(0, 11):
            iterator, skill = 0x10000000, 0x10000500
            uc.mem_write(iterator, bytes(0x80))
            w64(iterator + 0x20, 0x10000800); w64(iterator + 0x30, skill)
            w32(skill + 0x38, size); w32(0x10001010, x); w32(0x10001014, y)
            w32(0x10001110, direction)
            actual = []
            while run(0x15e2534, x0=iterator):
                actual.append((r32(iterator + 0x14), r32(iterator + 0x18)))
                assert len(actual) <= 11
            assert actual == line_cells(x, y, *directions[direction], size)
            counts['line'] += 1
            # Rectangle MoveNext, supplied with the traced GetRangeAttackLocations
            # argument mapping. Tests iteration order, not the higher-level wrapper.
            uc.mem_write(iterator, bytes(0x80))
            start_y = y - size if direction == 0 else y + 1
            for offset, value in [(0x24, 5), (0x2c, size), (0x34, direction),
                                  (0x3c, 0), (0x44, start_y)]: w32(iterator + offset, value)
            actual = []
            while run(0x145e8e8, x0=iterator):
                point = r64(iterator + 0x18)
                actual.append((r32(point + 0x10), r32(point + 0x14)))
                assert len(actual) <= 55
            assert actual == battle_band(y, direction, size)
            counts['bandRectangle'] += 1

for radius in [1, 2, 3, 5, 10]:
    for previous in range(0, 12):
        for distance in range(0, 12):
            run(0x1588978, 0x1588994, w0=distance, w27=radius, w26=previous, x20=11, x22=22)
            assert reg('X20') == (22 if distance <= radius and distance < previous else 11)
            counts['nearestSelection'] += 1
report = dict(status='Original isolated ARM64, synthetic objects; not original-game battle validation',
              checks=counts, samples=samples,
              limits=['Allocation, object type checks, vector constructors and entity getters are stubbed.',
                      'Line direction vectors supplied from prior native recovery.',
                      'Band wrapper argument mapping is statically traced; rectangle iterator is emulated.',
                      'Nearest selection tests comparison block only; candidate eligibility and enumeration are not emulated.',
                      'Projectile impacts, cell occupants and complete skill execution remain outside these tests.'])
(EVIDENCE / 'geometry-checks.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(counts))
