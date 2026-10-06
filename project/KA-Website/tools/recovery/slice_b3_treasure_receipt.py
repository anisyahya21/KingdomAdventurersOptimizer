"""Slice the B3 treasure receipt/capacity functions out of the frozen libil2cpp.so.

Does not touch the frozen audit.json or RE-evidence/20260912-combat exports; writes new
slices into RE-evidence/20260914-b3-receipt/ so the frozen evidence set keeps its hashes.
"""
import hashlib
import json
from pathlib import Path

from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
from elftools.elf.elffile import ELFFile

def find_root():
    for parent in Path(__file__).resolve().parents:
        if (parent / 'RE-evidence/G2.1/39257e72291d/inputs/libil2cpp.so').is_file():
            return parent
    raise SystemExit('could not locate repo root (RE-evidence/G2.1/.../inputs/libil2cpp.so)')


ROOT = find_root()
NATIVE = ROOT / 'RE-evidence/G2.1/39257e72291d'
BINARY = NATIVE / 'inputs/libil2cpp.so'
OUT = ROOT / 'RE-evidence/20260914-b3-receipt'


# rva -> short name. Owner = the *declaring class* (nearest `// Namespace:` + `class` header
# above the `// RVA:` line in dump.cs). `resolve_symbols.py` prints declarations WITHOUT their
# owner, which is how the earlier `kairo.unity.ecs.Entity$$` labels drifted (corrected
# 2026-09-14; see docs/reverse-engineering/…-addtreasure-countstock-…md §8.6). These labels are
# manifest metadata only — the recorded sha256 is over the raw binary window, so renaming a
# target cannot change a slice's evidence hash.
TARGETS = {
    0x1473234: 'kairo.unity.ecs.Entity$$AddTreasure',
    0x1473360: 'kairo.unity.ecs.Entity$$RemoveTreasure',
    0x1479898: 'kairo.unity.ecs.World$$CreateTreasure',      # was Entity$$CreateTreasure
    0x159F554: 'ecs.KingdomSystem$$CountTreasureStock',      # was Entity$$CountTreasureStock
    0x1630CF4: 'data.RankData$$get_maxTreasureCapacity',     # was data.TreasureData$$…
    0x14D2608: 'ecs.TreasureComponent$$get_data',
    0x1475B3C: 'kairo.unity.ecs.World$$get_map',
    # Resolved via dump/dump.cs (KA-Website/tools/recovery/resolve_symbols.py)
    0x159F73C: 'ecs.KingdomSystem$$CountItemStock',          # was Entity$$CountItemStock
    0x159F924: 'ecs.KingdomSystem$$CountBuiltMapChip',       # was Entity$$CountBuiltMapChip
}


WINDOW = 0x1000
MAX_INSTRS = 900


def va_to_off(elf, va):

    for seg in elf.iter_segments():
        if seg['p_type'] != 'PT_LOAD':
            continue
        start, size = seg['p_vaddr'], seg['p_filesz']
        if start <= va < start + size:
            return seg['p_offset'] + (va - start)
    raise KeyError(hex(va))


def main():
    data = BINARY.read_bytes()
    with BINARY.open('rb') as fh:
        elf = ELFFile(fh)
        offsets = {rva: va_to_off(elf, rva) for rva in TARGETS}
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = []
    for rva, name in TARGETS.items():
        code = data[offsets[rva]:offsets[rva] + WINDOW]
        lines = []
        terminated = False
        for ins in md.disasm(code, rva):
            lines.append(f'{ins.address:x}: {ins.mnemonic} {ins.op_str}')
            if ins.mnemonic == 'ret':
                terminated = True
                break
            # A branch that leaves the slice window is a tail call into a
            # sibling function, not part of this method.
            if ins.mnemonic in ('b', 'br'):
                target = None
                if ins.mnemonic == 'b' and ins.op_str.startswith('#'):
                    target = int(ins.op_str[1:], 16)
                if target is None or not (rva <= target < rva + WINDOW):
                    terminated = True
                    break
            if len(lines) >= MAX_INSTRS:
                break
        if not terminated:
            print(f'warning: no clean ret/exit within window for {name} ({hex(rva)})')

        (OUT / f'{rva:x}.asm').write_text('\n'.join(lines) + '\n', encoding='utf-8')
        manifest.append(dict(rva=hex(rva), name=name, offset=hex(offsets[rva]),
                             instrs=len(lines), terminated=terminated,
                             sha256=hashlib.sha256(
                                 data[offsets[rva]:offsets[rva] + WINDOW]).hexdigest()))

    (OUT / 'b3-receipt-slices.json').write_text(
        json.dumps(dict(binary=hashlib.sha256(data).hexdigest(), slices=manifest),
                   indent=2) + '\n', encoding='utf-8')
    print(json.dumps({m['name'].split('$$')[-1]: m['instrs'] for m in manifest}))


if __name__ == '__main__':
    main()
