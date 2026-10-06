"""Formation-order native comparator + tie-heavy differential.

Bounded check for the P1 gap "placement ORDER is declared, not natively executed".
It EXECUTES the recovered native ordering primitives (not a supplied permutation):

  * ecs.FighterSystem.<>c$$<InitFighters>b__27_2  0x15895c8  MemberOrder -> Priority
  * ecs.FighterSystem.<>c$$<InitFighters>b__27_1  0x158955c  MemberOrder -> entity -> Param.GetValue(e,14,0)
  * System.Linq.EnumerableSorter<object,int>$$CompareKeys 0x1bc4880
        sign convention, `descending` negation, and the index1-index2 tie-break (0x1bc499c),
        plus the ThenBy-style next-sorter recursion (0x1bc4998).

The comparator is executed with a controlled int key comparer (Comparer<int>.Default is a-b in
sign). The full OrderedEnumerable/QuickSort/ToArray driver is NOT executed; see limits in the JSON.
"""
import itertools
import json
import random
import struct
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE
from unicorn.arm64_const import *
from combat_initial_state import EVIDENCE, formation, priority

ROOT = EVIDENCE.parent.parent
binary = (ROOT / 'RE-evidence/G2.1/39257e72291d/inputs/libil2cpp.so').read_bytes()
audit = json.loads((EVIDENCE / 'audit.json').read_text())
uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
for base, size in [(0x1000000, 0x2300000), (0x10000000, 0x100000),
                   (0x20000000, 0x10000), (0x30000000, 0x1000)]:
    uc.mem_map(base, size)
for entry in audit['methods']:
    start, end, offset = (int(entry[k], 16) for k in ('rva', 'end', 'offset'))
    uc.mem_write(start, binary[offset:offset + end - start])

def w32(a, n): uc.mem_write(a, struct.pack('<I', n & 0xffffffff))
def w64(a, n): uc.mem_write(a, struct.pack('<Q', n & 0xffffffffffffffff))
def i32(v): return (v + 2**31) % 2**32 - 2**31
def reg(n): return uc.reg_read(globals()['UC_ARM64_REG_' + n])

B1, B2 = 0x158955c, 0x15895c8                             # defense / priority selectors
CK, STUB = 0x1bc4880, 0x20009000                       # comparator + controlled int comparer
SORTER, KEYS, COMPARER, KLASS = 0x10020000, 0x10021000, 0x10022000, 0x10023000
METHOD, P1, P2, P3, DESC = 0x10024000, 0x10025000, 0x10026000, 0x10027000, 0x10028000
NEXT, NEXTKEYS, NEXTKLASS = 0x10029000, 0x1002a000, 0x1002b000
FAKE, FAKEOBJ = 0x1002c000, 0x1002c100
GENV = 0x316b1f9                                         # b__27_1 class-init flag byte

# Controlled int comparer: sign(key1 - key2). Reached through 0x1332890 "no vtable match".
uc.mem_write(STUB, b'\x20\x00\x02\x4b\xc0\x03\x5f\xd6')   # sub w0,w1,w2 ; ret
uc.mem_write(GENV, b'\x01')
w64(0x2f5d0d0, FAKE); w64(FAKE, FAKEOBJ); w32(FAKEOBJ + 0xe0, 1)
w64(DESC, STUB); w64(DESC + 8, 0)
w64(COMPARER, KLASS); w32(KLASS + 0x12e - (KLASS % 4), 0)
w64(METHOD + 0x20, P1); w64(P1 + 0xc0, P2); w64(P2 + 0x20, P3)
uc.mem_write(P3 + 0x135, b'\x01')                        # skip generic resolver 0x133278c

def arm_sorter(addr, keys, descending, next_sorter=0):
    n = len(keys)
    if n:
        w64(addr + 0x30, KEYS if addr == SORTER else NEXTKEYS)
    w64(addr + 0x18, COMPARER); w32(addr + 0x20, descending); w64(addr + 0x28, next_sorter)
    return

def set_keys(addr, keys):
    w64(addr + 0x18, len(keys))
    for i, k in enumerate(keys):
        w32(addr + 0x20 + i * 4, k)

events = []
def external(m, address, size, user):
    if CK <= address < 0x1bc49b0 or B1 <= address < 0x15895c8 or B2 <= address < 0x15895e0 \
            or STUB <= address < STUB + 8:
        return
    if address in (0x12d21a0, 0x12d22a4):
        m.reg_write(UC_ARM64_REG_PC, reg('LR')); return
    if address == 0x1332890:
        m.reg_write(UC_ARM64_REG_X0, DESC); m.reg_write(UC_ARM64_REG_PC, reg('LR')); return
    if address == 0x166070c:
        events.append(('defense14', reg('X0'), reg('W1'), reg('W2')))
        m.reg_write(UC_ARM64_REG_X0, globals()['DEFENSE'] & 0xffffffff)
        m.reg_write(UC_ARM64_REG_PC, reg('LR')); return
    raise AssertionError(hex(address))
uc.hook_add(UC_HOOK_CODE, external)

def run(start, **regs):
    w64(0x20008000, 0x30000000)
    uc.reg_write(UC_ARM64_REG_SP, 0x20008000)
    uc.reg_write(UC_ARM64_REG_LR, 0x30000000)
    for name, value in regs.items():
        uc.reg_write(globals()['UC_ARM64_REG_' + name.upper()], value & 0xffffffffffffffff)
    uc.emu_start(start, 0x30000000, count=400)
    assert reg('PC') == 0x30000000, hex(reg('PC'))
    return i32(reg('W0'))

checks = dict(prioritySelector=0, defenseSelector=0, sign=0, tie=0, descending=0, chaining=0)

# --- priority key selector b__27_2: MemberOrder.Priority at +0x10 -------------
mo = 0x1002d000
for value in (-5, -1, 0, 2, 13, 15, 1234, 0x7fffffff, -0x80000000):
    w32(mo + 0x10, value & 0xffffffff)
    got = run(B2, x1=mo)
    assert got == i32(value), (value, got)
    checks['prioritySelector'] += 1

# --- defense key selector b__27_1: mo.Entity +0x18 -> Param.GetValue(e,14,0) --
for defense in (0, 1, 7, 999, -3, 0x7fffffff):
    globals()['DEFENSE'] = defense
    w64(mo + 0x18, 0x1002e000)
    events.clear()
    got = run(B1, x1=mo)
    assert events == [('defense14', 0x1002e000, 14, 0)], events
    assert got == i32(defense), (defense, got)
    checks['defenseSelector'] += 1

# --- CompareKeys 0x1bc4880: sign, descending negation, index tie-break -------
def compare(keys, i1, i2, descending=0, next_keys=None, next_desc=0):
    set_keys(KEYS, keys)
    arm_sorter(SORTER, keys, descending, NEXT if next_keys is not None else 0)
    if next_keys is not None:
        set_keys(NEXTKEYS, next_keys)
        arm_sorter(NEXT, next_keys, next_desc, 0)
        w64(NEXT, NEXTKLASS); w64(NEXTKLASS + 0x188, CK); w64(NEXTKLASS + 0x190, METHOD)
    return run(CK, x0=SORTER, x1=i1, x2=i2, x3=METHOD)

rng = random.Random(20260920)
for _ in range(120):
    n = rng.randint(2, 8)
    keys = [rng.choice([-2, -1, 0, 1, 5, 5, 9, 100]) for _ in range(n)]
    i1, i2 = rng.sample(range(n), 2)
    for descending in (0, 1):
        got = compare(keys, i1, i2, descending)
        expect = i1 - i2 if keys[i1] == keys[i2] else \
            (-(keys[i1] - keys[i2]) if descending else (keys[i1] - keys[i2]))
        assert (got > 0) - (got < 0) == (expect > 0) - (expect < 0), (keys, i1, i2, descending, got, expect)
        checks['descending' if descending else 'sign'] += 1
        if keys[i1] == keys[i2]:
            assert got == i1 - i2, (i1, i2, got)      # exact index tie-break, not just sign
            checks['tie'] += 1

# --- ThenBy-style next-sorter recursion (0x1bc4998) --------------------------
for _ in range(40):
    n = 4
    primary = [5, 5, 5, 5]  # all equal -> forces the next-sorter (ThenBy) recursion
    secondary = [rng.randrange(-3, 3) for _ in range(n)]
    i1, i2 = rng.sample(range(n), 2)
    got = compare(primary, i1, i2, 0, next_keys=secondary)
    if secondary[i1] == secondary[i2]:
        expect = i1 - i2
    else:
        expect = secondary[i1] - secondary[i2]
    assert (got > 0) - (got < 0) == (expect > 0) - (expect < 0), (secondary, i1, i2, got, expect)
    checks['chaining'] += 1

# --- Differential: recovered ordering algorithm vs combat_initial_state.formation
# The two native OrderBy calls each break equal keys by index1-index2 (CompareKeys 0x1bc499c),
# so the composite is exactly (priority asc, Defense14 desc, incoming index asc) for ANY algorithm.
def native_order(priorities, defenses):
    first = sorted(range(len(priorities)), key=lambda i: (-defenses[i], i))     # OrderByDescending
    position = {source: index for index, source in enumerate(first)}
    return sorted(first, key=lambda i: (priorities[i], position[i]))           # OrderBy
    # (second sort's index tie-break == position in first result -> preserves pass 1)

orders = 0
for _ in range(300):
    n = rng.randint(1, 6)
    members = []
    for i in range(n):
        members.append(dict(id=f'u{i}', effectiveDefense=rng.choice([0, 1, 1, 50, 50, 50, 999]),
                            **({'formationValue': rng.choice([0, 1, 4])} if rng.random() < .5 else {}),
                            **({'monster': True} if rng.random() < .2 else {}),
                            **({'visitor': True} if rng.random() < .2 else {}),
                            **({'leaderIdentity': True} if rng.random() < .2 else {}),
                            **({'ownerPlayer': True} if rng.random() < .2 else {})))
    placed = formation(members, 0, rng.choice([0, 5, 6, 11, 16, 21]))
    got = [r['incomingIndex'] for r in placed]
    pri = [r['priority'] for r in placed]
    want = native_order([priority(m.get('formationValue'), visitor=m.get('visitor', False),
                                  leader_identity=m.get('leaderIdentity', False),
                                  monster=m.get('monster', False), owner_player=m.get('ownerPlayer', False))
                         for m in members],
                        [m['effectiveDefense'] for m in members])
    assert got == want, (members, got, want)
    # priorities recomputed from placed rows must match the sorted order too
    assert pri == sorted(pri), (pri,)
    orders += 1
for n in range(1, 7):
    members = [dict(id=f'u{i}', effectiveDefense=7) for i in range(n)]          # full tie
    placed = formation(members, 0, 6)
    assert [r['incomingIndex'] for r in placed] == list(range(n))
    orders += 1

report = dict(
    checks=checks, differentialOrders=orders,
    executed={'prioritySelector': hex(B2), 'defenseSelector': hex(B1),
              'compareKeys': hex(CK), 'controlledComparerStub': hex(STUB)},
    orderingAlgorithm={
        'nativePath': 'Enumerable.OrderByDescending(Defense14) then Enumerable.OrderBy(Priority) '
                      'over the MemberOrder projection <InitFighters>b__0 0x15897cc',
        'comparator': 'System.Linq.EnumerableSorter<object,int>.CompareKeys 0x1bc4880',
        'descendingNegation': '0x1bc4958 cneg w0,w0,ne (this+0x20)',
        'tieBreak': '0x1bc499c sub w0,w20,w19 => index1-index2 (incoming index ascending)',
        'chaining': '0x1bc4998 br x4 -> next.CompareKeys (ThenBy/ThenByDescending style)',
        'keys': ['priority ascending', 'Param.GetValue(entity,14,0) descending', 'incoming index ascending'],
        'note': 'Each OrderBy breaks all equal keys by index, so the composite is a strict total '
                'order and independent of the sort algorithm (stable or unstable).'},
    limits=['The full OrderedEnumerable/ComputeKeys/QuickSort/ToArray driver is not executed; only the '
            'comparator core (CompareKeys) and the two key selectors are executed natively.',
            'The int key comparer is a controlled stub returning sign(k1-k2); Comparer<int>.Default is '
            'the same sign and the compare-via-vtable dispatch (0x1332890 path) is bypassed.',
            'b__27_1 is executed to its tail branch; Param.GetValue 0x166070c is stubbed to return the '
            'supplied Defense14, matching the model input (not re-deriving effective defense).'],
    status='Native comparator + selectors executed; ordering algorithm recovered from native callgraph; '
           'model differential passes. NOT a whole-fight proof.')
(EVIDENCE / 'formation-order-checks.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
print(json.dumps(dict(checks=checks, differentialOrders=orders)))




