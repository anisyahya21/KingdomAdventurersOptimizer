"""Deterministic re-check of the PASS 15 COMMAND 15.1 human battle animation recovery.

Re-derives every constant quoted by
`RE-evidence/20260919-human-battle-animation/HUMAN-BATTLE-ANIMATION.md` straight from the frozen
ELF, the original `chara/seb.inf` and the stored `humanAnimationSebBases` field values, so the
report cannot drift away from the binary it cites. Read-only: nothing is written and no
production file is touched.

    python KA-Website/tools/recovery/verify_human_battle_animation.py

Exit code 0 = every quoted fact reproduced; 1 = at least one claim no longer matches.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
from elftools.elf.elffile import ELFFile

def find_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "RE-evidence/G2.1/39257e72291d/inputs/libil2cpp.so").is_file():
            return parent
    raise SystemExit("could not locate repo root (RE-evidence/G2.1/.../inputs/libil2cpp.so)")


ROOT = find_root()
BINARY = ROOT / "RE-evidence/G2.1/39257e72291d/inputs/libil2cpp.so"
SEB_INF = ROOT / "RE-evidence/20260912-combat/chara-animation-original/seb.inf"
CONSTANTS = ROOT / "RE-evidence/20260912-combat/skill-combat-constants.json"
REPORT = ROOT / "RE-evidence/20260919-human-battle-animation/HUMAN-BATTLE-ANIMATION.md"

EXPECTED_SHA256 = "fb834373cb3bd1dc7dac941fcf94113f3b5123e033cf0a41d5e00c0656e30208"
WINDOW = 0x1000

FAILURES: list[str] = []
CHECKS = 0


def check(condition: bool, label: str) -> None:
    global CHECKS
    CHECKS += 1
    if not condition:
        FAILURES.append(label)


def va_to_off(elf, va: int) -> int:
    for segment in elf.iter_segments():
        if segment["p_type"] != "PT_LOAD":
            continue
        start, size = segment["p_vaddr"], segment["p_filesz"]
        if start <= va < start + size:
            return segment["p_offset"] + (va - start)
    raise KeyError(hex(va))


def slice_lines(data: bytes, elf, rva: int) -> list[str]:
    """Same slicer the evidence uses: stop at the first ret or at a branch out of the window."""
    offset = va_to_off(elf, rva)
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    lines: list[str] = []
    for ins in md.disasm(data[offset:offset + WINDOW], rva):
        lines.append(f"{ins.address:x}: {ins.mnemonic} {ins.op_str}")
        if ins.mnemonic == "ret":
            break
        if ins.mnemonic in ("b", "br"):
            target = None
            if ins.mnemonic == "b" and ins.op_str.startswith("#"):
                target = int(ins.op_str[1:], 16)
            if target is None or not (rva <= target < rva + WINDOW):
                break
    return lines


def has(lines: list[str], needle: str) -> bool:
    return any(needle in line for line in lines)


# rva -> (label, [exact instruction fragments that must appear in the slice])
FUNCTION_FACTS: dict[int, tuple[str, list[str]]] = {
    0x15840F0: ("FighterSystem.EnterWaiting", ["mov w1, #3", "b #0x165dea8"]),
    0x1584194: ("FighterSystem.EnterMoving", [
        "mov w9, #2", "str w9, [x0, #0x10]", "mov w1, #2", "bl #0x165dea8",
    ]),
    0x15849D8: ("FighterSystem.ExitMoving", ["mov w8, #1", "str w8, [x0, #0x10]"]),
    0x1584B90: ("FighterSystem.EnterCharging", ["mov w1, #3", "b #0x165dea8"]),
    0x15854C0: ("FighterSystem.EnterAttacking", [
        "bl #0x146de58", "bl #0x146ddd0", "bl #0x14c7db0",
        "ldr w20, [x0, #0x64]", "mov w1, w20", "b #0x165dea8",
    ]),
    0x1585568: ("FighterSystem.UpdateAttacking", [
        "mov w1, #4", "cmp w0, #0xb", "cmp w0, #0x14",
        "bl #0x168d1e8", "bl #0x1583b48",
    ]),
    0x1585A78: ("FighterSystem.EnterUsingSkill", [
        "bl #0x1468bc8", "mov w1, #0x11", "bl #0x1a6a1cc", "bl #0x161f56c",
        "ldr w20, [x8, #0x4c]", "b #0x165dea8",
    ]),
    0x1585D2C: ("FighterSystem.ExitUsingSkill", ["mov w1, #3", "b #0x165dea8"]),
    0x1585E34: ("FighterSystem.EnterDamaging", [
        "mov w1, #0xc", "bl #0x1a6a12c", "mov w1, #0x1e", "b #0x165dea8",
    ]),
    0x1586444: ("FighterSystem.EnterKnockingDown", ["mov w1, #0x1c", "bl #0x165dea8"]),
    0x14DC884: ("ecs.AnimationSystem.Update", [
        "ldr w8, [x0, #0x10]", "ldr w9, [x26, #0x1c]",
        "sdiv w10, w8, w9", "msub w8, w10, w9, w8", "bl #0x165dea8",
    ]),
    0x165DEA8: ("resource.AnimationSet.ChangeAnimation", [
        "add w8, w8, w22", "str w8, [x20, #0x14]", "str wzr, [x0, #0x18]",
        "bl #0x165ddd8",
    ]),
    0x146BA70: ("kairo.unity.ecs.Entity.AddAnimation", ["stp w21, w20, [x22, #0x10]"]),
    0x1583B48: ("ecs.FighterSystem.ChangeState", [
        "mov w1, #5", "mov w1, #4", "mov w2, wzr", "bl #0x1a6a12c",
    ]),
    0x1583CF8: ("ecs.FighterSystem.Update", [
        "mov w1, #4", "mov w1, #5", "bl #0x1a6a108", "bl #0x1a6a12c", "blr x9",
    ]),
}

# behaviour id -> (human SEB base, expected SEB file for direction 0 / up)
COMBAT_BEHAVIOURS: dict[int, tuple[int, str]] = {
    2: (8, "equip_walk_up.seb"),
    3: (12, "equip_wait_up.seb"),
    4: (16, "attack_sword_up.seb"),
    9: (72, "attack_spear_up.seb"),
    10: (56, "attack_gun_up.seb"),
    11: (68, "attack_bow_up.seb"),
    15: (104, "tired_walk_up.seb"),
    16: (96, "attack_scoop_up.seb"),
    28: (44, "equip_sit_up.seb"),
    30: (188, "equip_damege2_up.seb"),
    31: (48, "attack_magic_up.seb"),
    32: (192, "tired_wait_up.seb"),
    33: (196, "equip_dash_up.seb"),
}


def parse_seb_inf(path: Path) -> dict[int, str]:
    """chara/seb.inf: clips 0..99 are positional, then (id, name) record pairs."""
    raw = path.read_bytes()
    text = "".join(chr(b) if 32 <= b < 127 else "\n" for b in raw)
    tokens = [t for t in text.split("\n") if len(t) >= 3]
    table: dict[int, str] = {index: tokens[index] for index in range(100)}
    for index in range(100, len(tokens) - 1, 2):
        table[int(tokens[index])] = tokens[index + 1]
    return table


def main() -> int:
    data = BINARY.read_bytes()
    digest = hashlib.sha256(data).hexdigest()

    check(digest == EXPECTED_SHA256, f"binary sha256 {digest} != {EXPECTED_SHA256}")
    if digest != EXPECTED_SHA256:
        print("FAILED: wrong binary; refusing to check slices")
        return 1

    with BINARY.open("rb") as handle:
        elf = ELFFile(handle)
        for rva, (label, fragments) in sorted(FUNCTION_FACTS.items()):
            lines = slice_lines(data, elf, rva)
            check(bool(lines), f"{label} 0x{rva:x}: no instructions")
            for fragment in fragments:
                check(has(lines, fragment), f"{label} 0x{rva:x}: missing '{fragment}'")

        # EnterLeaving has no ChangeAnimation call (re-verified, unchanged from FIGHTER-ANIMATION).
        leaving = "\n".join(slice_lines(data, elf, 0x1586A80))
        check("0x165dea8" not in leaving, "EnterLeaving unexpectedly calls ChangeAnimation")

        # UpdateAttacking applies damage on exactly timer 11 and leaves on timer >= 20.
        attacking = "\n".join(slice_lines(data, elf, 0x1585568))
        check("cmp w0, #0xb\n" in attacking + "\n" or "cmp w0, #0xb" in attacking,
              "UpdateAttacking 0x1585568: damage gate is not timer 11")
        check("cmp w0, #0x14" in attacking, "UpdateAttacking 0x1585568: exit gate is not timer 20")

    table = parse_seb_inf(SEB_INF)
    for behaviour, (base, seb_name) in sorted(COMBAT_BEHAVIOURS.items()):
        check(table.get(base) == seb_name,
              f"chara/seb.inf: base {base} (behaviour {behaviour}) = {table.get(base)!r}, "
              f"expected {seb_name!r}")

    values = json.loads(CONSTANTS.read_text(encoding="utf-8"))["humanAnimationSebBases"]["values"]
    check(len(values) == 43, f"humanAnimationSebBases has {len(values)} entries, expected 43")
    for behaviour, (base, _) in sorted(COMBAT_BEHAVIOURS.items()):
        check(values[behaviour] == base,
              f"humanAnimationSebBases[{behaviour}] = {values[behaviour]}, expected {base}")

    # Anti-drift: the report must actually cite each verified address.
    report = REPORT.read_text(encoding="utf-8") if REPORT.exists() else ""
    check(bool(report), f"missing report {REPORT}")
    for rva in FUNCTION_FACTS:
        check(f"0X{rva:X}" in report.upper(), f"report does not cite 0x{rva:X}")

    if FAILURES:
        print(f"FAILED {len(FAILURES)} of {CHECKS} checks")
        for failure in FAILURES:
            print(f"  - {failure}")
        return 1

    print(f"PASS {CHECKS}/{CHECKS} checks")
    print(f"binary sha256 {digest}")
    print(f"behaviours verified: {sorted(COMBAT_BEHAVIOURS)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
