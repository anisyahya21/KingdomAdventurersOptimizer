"""Does a native AUTOMATIC Ending->Finish producer exist? (fail-closed evidence, 2026-09-20)

Player observation: a Wairo/special fight shows victory, returns to the map and throws chests with no
click. Static recovery of the Android 2.6.2 Ending state has not explained that observation, so this
check records the limited direct-call evidence and keeps yield eligibility disabled.

Recovered gate (unchanged primitives):
  * `BattleSystem.UpdateStateEnding` 0x14ef4e4 is the only exit from `STATE_ENDING 3` (0x14ed3d4
    `ChangeState` resets `frame_` 0x3c; `BattleSystem.Update` 0x14ed47c increments it then dispatches
    `updateStateTable_`).
  * The gate is `Canvas.CheckKeyPulse(Canvas.KEY_SELECT 0x100000)` 0x14ef538 -> 0x22f44c8 ->
    `Keypad.CheckKeyPulse` 0x23438b4, which tests and clears one bit of `keyPulse_` (Keypad+0x38).
  * `Keypad.DecideKeyState` 0x2343818 recomputes that bit every frame as
    `(keyEventState_ | keyStateWork_) & ~keyState_`: a ONE-FRAME input edge, not a timer, animation
    key or synthetic counter.

What this check proves:
  1. Every recovered direct producer of that edge is an input entry point. A `bl`-site scan of the
     libil2cpp.so finds calls to `Canvas.KeyDown` 0x22f5038, `Canvas.KeyUp` 0x22f50c0,
     `Canvas.KeyClick` 0x22f5124 and `Canvas.KeyClickImmediate` 0x22f519c (plus the `Keys` overloads
     0x22f50b8/0x22f50bc/0x22f5120/0x22f5198) only from touch/keyboard/gamepad handlers and one
     held-joystick auto-repeat in `IApplication.Update` 0x2322028 (`KeyClick(1)` = KEY_0, not
     KEY_SELECT).
  2. No battle-driven, coroutine, guerrilla/special or "auto" producer of KEY_SELECT exists in the
     direct call graph. If one is ever added/found, the allowed-owner set below fails and names it.
  3. Finish (`BattleSystem.Finish` 0x14ed864..0x14ee830, full function) and its automatic EXP entry
     (`BattleSystem.EnterStateGettingExp` 0x14ef5a8..0x14ef880) contain NO direct call to the Math RNG
     (`kairo.unity.math.Random` 0x145ff98/0x1460024/0x1460084/0x14600e4/0x1460144) or the Lib RNG
     (`ext.util.Lib` 0x144cdc8/0x1449274/0x14492d8/0x144938c/0x1449418), so no `trace + 3*N` post-Finish
     RNG claim is made; indirect/virtual edges stay unresolved and are labelled.
  4. The sandbox/runner FAIL CLOSED: no policy is yield-eligible until the real producer is recovered.
"""
import json
import struct
from pathlib import Path

from capstone import CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN, Cs

EVIDENCE = Path(__file__).resolve().parents[3] / 'RE-evidence' / '20260912-combat'
BINARY = EVIDENCE.parent / 'G2.1/39257e72291d/inputs/libil2cpp.so'
INDEX_DB = EVIDENCE.parent / '20260920-native-index/build/ka-index.sqlite'

KEY_EDGE_PRODUCERS = {
    0x22f5038: 'kairo.unity.ui.Canvas$$KeyDown(int,bool)', 0x22f50b8: 'kairo.unity.ui.Canvas$$KeyDown(Keys,bool)',
    0x22f50c0: 'kairo.unity.ui.Canvas$$KeyUp(int)', 0x22f50bc: 'kairo.unity.ui.Canvas$$KeyUp(Keys)',
    0x22f5124: 'kairo.unity.ui.Canvas$$KeyClick(int)', 0x22f5120: 'kairo.unity.ui.Canvas$$KeyClick(Keys)',
    0x22f519c: 'kairo.unity.ui.Canvas$$KeyClickImmediate(int)',
    0x22f5198: 'kairo.unity.ui.Canvas$$KeyClickImmediate(Keys)',
}
# Every owner is an INPUT handler. `IApplication.Update` 0x2322028 auto-repeats KEY_0 while a
# joystick button is held; it is the only non-touch producer and it never sends KEY_SELECT.
ALLOWED_OWNERS = {
    'system.form.BillingForm$$OnTouchEvent', 'surface.GamePad$$OnTouchEvent',
    'surface.GameView$$OnTouchDlgPage', 'surface.GameView$$OnTouchPlusMinus',
    'surface.GameView$$OnTouchButton', 'surface.GameView$$OnTouchButtonPress',
    'form.AirshipForm$$OnTouchCamera', 'form.BattleForm$$OnTouchCamera', 'form.GameForm$$OnTouchCamera',
    'form.SubForm$$OnTouchEvent', 'kairo.unity.ui.Canvas$$OnKeyDown', 'kairo.unity.ui.IApplication$$Update',
}
MATH_RNG = {0x145ff98, 0x1460024, 0x1460084, 0x14600e4, 0x1460144}
LIB_RNG = {0x144cdc8, 0x1449274, 0x14492d8, 0x144938c, 0x1449418}
FINISH = (0x14ed864, 0x14ee830)
EXP_ENTRY = (0x14ef5a8, 0x14ef880)


def load_binary():
    import sqlite3
    data = BINARY.read_bytes()
    assert data[:4] == b'\x7fELF'
    phoff, phentsize, phnum = struct.unpack_from('<Q', data, 0x20)[0], struct.unpack_from('<H', data, 0x36)[0], struct.unpack_from('<H', data, 0x38)[0]
    loads = []
    for i in range(phnum):
        o = phoff + i * phentsize
        p_type = struct.unpack_from('<I', data, o)[0]
        p_offset, p_vaddr, _p, p_filesz = struct.unpack_from('<QQQQ', data, o + 8)
        if p_type == 1:
            loads.append((p_vaddr, p_offset, p_filesz))
    db = sqlite3.connect(f'file:{INDEX_DB}?mode=ro', uri=True)
    return data, loads, db

def owner(db, address):
    row = db.execute('select name from methods where rva<=? order by rva desc limit 1', (address,)).fetchone()
    return row[0] if row else None

def scan(db, data, loads):
    """Exhaustive BL sweep; returns [(target, site, owner)] for the key-edge producers."""
    produced = {}
    for vaddr, offset, filesz in loads:
        if filesz < 0x1000:
            continue
        for i in range(0, filesz - 4, 4):
            word = struct.unpack_from('<I', data, offset + i)[0]
            if word >> 26 != 0x25:  # BL
                continue
            imm = word & 0x03ffffff
            if imm & 0x02000000:
                imm -= 0x04000000
            target = vaddr + i + (imm << 2)
            if target in KEY_EDGE_PRODUCERS:
                produced.setdefault(target, []).append((vaddr + i, owner(db, vaddr + i)))
    return produced

def direct_calls(data, loads, lo, hi):
    def foff(rva):
        for vaddr, offset, filesz in loads:
            if vaddr <= rva < vaddr + filesz:
                return offset + (rva - vaddr)
        raise AssertionError(hex(rva))
    md = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)
    targets = set()
    for instruction in md.disasm(data[foff(lo):foff(lo) + (hi - lo)], lo):
        if instruction.mnemonic in ('bl', 'b') and instruction.op_str.startswith('#0x'):
            targets.add(int(instruction.op_str[1:], 16))
    return sorted(targets)

def main():
    data, loads, db = load_binary()
    produced = scan(db, data, loads)
    # The `Keys` overloads and KeyClickImmediate/KeyUp tails are never called directly (they are
    # thunks into the int overloads); the two entry points that the game actually uses must be found.
    no_call_sites = sorted(set(KEY_EDGE_PRODUCERS) - set(produced))
    assert 0x22f5038 in produced and 0x22f5124 in produced, [hex(x) for x in no_call_sites]
    owners = {}
    for target, sites in produced.items():
        for site, name in sites:
            owners.setdefault(name, []).append(site)
    unexpected = sorted(set(owners) - ALLOWED_OWNERS)
    assert not unexpected, ('a new key-edge producer appeared - if it is an automatic battle producer, '
                            'update AUTO_FINISH_PRODUCER_PROVEN and the policies: %s' % unexpected)
    finish_calls = direct_calls(data, loads, *FINISH)
    exp_calls = direct_calls(data, loads, *EXP_ENTRY)
    rng_in_finish = sorted(set(finish_calls) & (MATH_RNG | LIB_RNG))
    rng_in_exp = sorted(set(exp_calls) & (MATH_RNG | LIB_RNG))
    assert not rng_in_finish, [hex(x) for x in rng_in_finish]
    assert not rng_in_exp, [hex(x) for x in rng_in_exp]
    # Fail-closed wiring: the sandbox/controller/manifest/evaluation must not claim a trusted yield.
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import combat_sandbox
    assert combat_sandbox.AUTO_FINISH_PRODUCER_PROVEN is False
    assert 'no automatic producer' in combat_sandbox.AUTO_FINISH_UNPROVEN_REASON
    report = dict(
        nativeAutoFinishProducerFound=False, nativeAutoFinishProducerProven=False,
        nativeEndingGate='0x14ef4e4 STATE_ENDING 3 exit: Canvas.CheckKeyPulse(KEY_SELECT 0x100000) at '
                         '0x14ef538; one-frame input edge from Keypad.DecideKeyState 0x2343818',
        keyEdgeProducerSites={KEY_EDGE_PRODUCERS[t]: sorted(s for s, _ in sites) for t, sites in produced.items()},
        keyEdgeProducerOwners=sorted(owners),
        allowedOwnerKind='input handlers only (touch/keyboard/gamepad; IApplication auto-repeat sends KEY_0)',
        entryPointsWithNoCallSites=['0x%x' % x for x in no_call_sites],
        finishRva='0x14ed864-0x14ee830 (1011 instructions, full function)',
        expEntryRva='0x14ef5a8-0x14ef880',
        finishDirectCallSet=['0x%x' % x for x in finish_calls],
        expEntryDirectCallSet=['0x%x' % x for x in exp_calls],
        finishOrExpDrawsMathOrLibRng=False,
        rngScope='direct bl/b edges only; indirect/virtual edges unresolved',
        failClosed=dict(sandbox=combat_sandbox.AUTO_FINISH_PRODUCER_PROVEN is False,
                        yieldTrustedAlwaysFalse=True, policiesDiagnosticOnly=True),
        limits=['Canvas focus keypad, physical input feed and touch delivery are supplied by the game loop, '
                'not executed here; the automatic producer may live outside the direct call graph (e.g. a '
                'platform input source), which is exactly why the yield policy fails closed.'])
    (EVIDENCE / 'auto-finish-producer-checks.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(dict(producers=len(produced), owners=len(owners), finishCalls=len(finish_calls),
                          expCalls=len(exp_calls), rng=False, failClosed=True)))

if __name__ == '__main__':
    main()
