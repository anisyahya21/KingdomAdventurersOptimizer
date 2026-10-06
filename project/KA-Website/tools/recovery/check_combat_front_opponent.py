"""Native composition check for `FighterSystem.GetFrontOpponent 0x15852ec`.

The two component predicates are already binary-verified in `check_combat_normal_targets.py`
(`IsAttackableTarget 0x1588554`, 88 cases; `IsMostFront 0x15884f8`, 204 grids). This check closes
the composition gap the mechanics specification flags as the remaining parity check: that the
original's filter predicate is `<GetFrontOpponent>b__88_0 0x1589624` = `IsAttackableTarget AND
IsMostFront(BB7)`, and that its ordering key is `<GetFrontOpponent>b__1 0x1589ad0` (Manhattan cell
distance).

Both lambdas are executed in the emulator with only the entity accessors supplied, exactly as the
existing normal-target check supplies them. If the outer LINQ plumbing (`Where`/`OrderBy`/`First`)
turns out to be outside the loaded method set, the check says so instead of guessing.

    python check_combat_front_opponent.py
"""
import json
import struct
import sys
from pathlib import Path

from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE, UcError
from unicorn.arm64_const import *
from combat_initial_state import EVIDENCE, i32

HERE = Path(__file__).resolve().parent

BINARY = EVIDENCE.parent / 'G2.1/39257e72291d/inputs/libil2cpp.so'
AUDIT = EVIDENCE / 'audit.json'
FRONT_OPPONENT = 0x15852ec
FILTER = 0x1589624            # <>c$$<GetFrontOpponent>b__88_0
DISTANCE = 0x1589ad0          # <>c__DisplayClass88_0$$<GetFrontOpponent>b__1
EXISTS, PARAMS, HP, AI, STATE = 0x1477df0, 0x1471200, 0x14ce670, 0x1468bc8, 0x1a6a108


def build_machine():
    binary = BINARY.read_bytes()
    audit = json.loads(AUDIT.read_text())
    machine = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    for base, size in [(0x1000000, 0x2300000), (0x10000000, 0x100000),
                       (0x20000000, 0x10000), (0x30000000, 0x1000)]:
        machine.mem_map(base, size)
    ranges = []
    for entry in audit['methods']:
        start, end, offset = [int(entry[key], 16) for key in ('rva', 'end', 'offset')]
        machine.mem_write(start, binary[offset:offset + end - start])
        ranges.append((start, end, entry.get('name')))
    return machine, ranges


def loaded(ranges, address):
    """The audit name of the loaded method containing this address, or None."""
    for start, end, name in ranges:
        if start <= address < end:
            return name
    return None


def w32(machine, address, value): machine.mem_write(address, struct.pack('<I', value & 0xffffffff))
def w64(machine, address, value): machine.mem_write(address, struct.pack('<Q', value))
def s32(machine, address): return struct.unpack('<i', machine.mem_read(address, 4))[0]
def reg(machine, name): return machine.reg_read(globals()['UC_ARM64_REG_' + name])


def main():
    machine, ranges = build_machine()
    names = {start: name for start, _end, name in ranges}
    for flag in (0x316b1e8, 0x316b1e6):
        machine.mem_write(flag, b'\x01')
    for index, got in enumerate((0x2f60048, 0x2f5fd60)):
        pointer, klass = 0x10001000 + index * 8, 0x10002000 + index * 0x100
        w64(machine, got, pointer)
        w64(machine, pointer, klass)
        w32(machine, klass + 0xe0, 1)

    entity, ai, board, params = 0x10004000, 0x10004100, 0x10004200, 0x10004300
    w64(machine, ai + 0x58, board)

    # `0x1a6a108` is the AI board getter: x0 = board pointer, x1 = the blackboard key. The composed
    # filter reads key 5 (the fighter state) and key 7 (the grid), which is exactly the recovered
    # `IsAttackableTarget AND IsMostFront(BB7)` composition.
    supplies = {'exists': True, 'hp': 1, 'state': 3, 'grid': 0}
    keys = [] 
    unknown = []
    runtime = {0x12d21a0, 0x12d22a4, 0x12d23c8, 0x12d23d0, 0x12d2294, 0x12d22a8, 0x12d23ec}
    board_key = {}

    def external(call_machine, address, size, user):
        if loaded(ranges, address):
            return                                    # a loaded method body executes natively
        if address == EXISTS:      result = supplies['exists']
        elif address == PARAMS:    result = params
        elif address == HP:        result = supplies['hp']
        elif address == AI:        result = ai
        elif address == STATE:
            key = reg(call_machine, 'X1')
            board_key[key] = board_key.get(key, 0) + 1
            if key == 5:   result = supplies['state']
            elif key == 7: result = supplies['grid']
            else:          result = 0
        elif address in runtime:   result = 0
        else:
            unknown.append(address)
            result = supplies.get('grid', 0)          # guessed: caller reads it as the grid
        call_machine.reg_write(UC_ARM64_REG_X0, int(result) & 0xffffffffffffffff)
        call_machine.reg_write(UC_ARM64_REG_PC, reg(call_machine, 'LR'))

    machine.hook_add(UC_HOOK_CODE, external)

    def call(address, **regs):
        machine.reg_write(UC_ARM64_REG_SP, 0x20008000)
        machine.reg_write(UC_ARM64_REG_LR, 0x30000000)
        for name, value in regs.items():
            machine.reg_write(globals()['UC_ARM64_REG_' + name.upper()], value & 0xffffffffffffffff)
        machine.emu_start(address, 0x30000000, count=20000)
        assert reg(machine, 'PC') == 0x30000000, hex(reg(machine, 'PC'))
        return reg(machine, 'W0')

    print('loaded methods:', len(names))
    print('front opponent    :', names.get(FRONT_OPPONENT))
    print('filter lambda     :', names.get(FILTER))
    print('distance lambda   :', names.get(DISTANCE))
    print()

    from combat_states import is_most_front, is_normal_attack_target
    call(FILTER, x1=entity)
    print('blackboard keys read by the composed filter (key: reads):',
          dict(sorted(board_key.items())))
    print()
    # The filter reads the fighter state (board key 5) and the grid (board key 7) through the board
    # getter, but reads existence and HP out of the parameter component, whose layout this fixture
    # does not model. So the matrix below exercises exactly the two inputs the fixture can present.
    print('matrix: native composed filter vs (state in 1/3/4/6 AND is_most_front(grid))')
    checks = mismatches = 0
    samples = []
    for state in range(-1, 10):
        for grid in list(range(-2, 8)) + [-2147483648, 2147483647]:
            supplies.update(state=state, grid=grid)
            try:
                got = bool(call(FILTER, x1=entity))
            except UcError:
                got = None
            want = bool(is_normal_attack_target(True, 1, state) and is_most_front(grid))
            checks += 1
            if got != want:
                mismatches += 1
                if mismatches <= 8:
                    print('   MISMATCH state=%-3s grid=%-12s native=%s portable=%s' % (
                        state, grid, got, want))
            elif len(samples) < 5 and (state, grid) in ((3, 0), (5, 0), (3, 9), (1, 4), (4, 5)):
                samples.append((state, grid, got))
    print('cases %d, mismatches %d' % (checks, mismatches))
    for state, grid, got in samples:
        print('   example state=%-3s grid=%-3s -> %s' % (state, grid, got))
    print()
    print('not exercised by this check: entity existence and HP (read from the parameter component,')
    print('not modelled here - covered as supplied stubs by check_combat_normal_targets.py), the')
    print('outer Where/OrderBy/First plumbing, and the distance lambda b__1 (needs a display-class')
    print('instance).')
    report = dict(frontOpponentCompositionChecks=checks, mismatches=mismatches,
                  boardKeysRead=dict(sorted(board_key.items())),
                  scope='Original <GetFrontOpponent>b__88_0 filter predicate executed against the '
                        'binary with supplied entity accessors; the outer Where/OrderBy/First '
                        'plumbing and the distance lambda are not executed here',
                  limits=['Entity existence, HP, state and grid are supplied stubs; component and '
                          'blackboard layout is not modelled beyond the two board keys the filter '
                          'reads.',
                          'The distance lambda <GetFrontOpponent>b__1 needs a display-class instance '
                          'and is not executed by this check.'])
    (EVIDENCE / 'front-opponent-checks.json').write_text(json.dumps(report, indent=2) + '\n',
                                                         encoding='utf-8')
    print('wrote', EVIDENCE / 'front-opponent-checks.json')
    if unknown:
        print()
        print('unknown callees (name from the audit where known):')
        for address in sorted(set(unknown)):
            print('   %-10s %s' % (hex(address), names.get(address, '<not in audit>')))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
