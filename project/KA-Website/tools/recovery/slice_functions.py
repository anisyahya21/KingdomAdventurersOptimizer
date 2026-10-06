"""Slice arbitrary functions out of the frozen libil2cpp.so by RVA.

Generic companion to slice_b3_treasure_receipt.py (which keeps its fixed B3
target list so its frozen manifest stays reproducible). Shares the binary/ELF
helpers with it and writes each slice plus a hashed manifest into --out, so
follow-up tracks (dispatch targets, readers, serialization) stay evidence-grade.

    python slice_functions.py --out <dir> 0x147df30:BaseEntity$$Add 0x147e030:BaseEntity$$Remove
"""
import hashlib
import json
import sys
from pathlib import Path

from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
from elftools.elf.elffile import ELFFile

from slice_b3_treasure_receipt import BINARY, MAX_INSTRS, WINDOW, va_to_off


def slice_instructions(data, offset, rva, window=WINDOW, max_instrs=MAX_INSTRS):
    """Disassemble one function window; stop at ret or at a branch leaving it."""
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    lines = []
    terminated = False
    for ins in md.disasm(data[offset:offset + window], rva):
        lines.append(f'{ins.address:x}: {ins.mnemonic} {ins.op_str}')
        if ins.mnemonic == 'ret':
            terminated = True
            break
        if ins.mnemonic in ('b', 'br'):
            target = None
            if ins.mnemonic == 'b' and ins.op_str.startswith('#'):
                target = int(ins.op_str[1:], 16)
            if target is None or not (rva <= target < rva + window):
                terminated = True
                break
        if len(lines) >= max_instrs:
            break
    return lines, terminated


def parse_targets(args):
    targets = {}
    for arg in args:
        rva_text, _, name = arg.partition(':')
        targets[int(rva_text, 16)] = name or f'unknown_{rva_text}'
    return targets


def main():
    args = sys.argv[1:]
    if '--out' not in args:
        raise SystemExit(__doc__)
    i = args.index('--out')
    out_dir = Path(args[i + 1])
    targets = parse_targets(args[:i] + args[i + 2:])
    if not targets:
        raise SystemExit(__doc__)

    data = BINARY.read_bytes()
    with BINARY.open('rb') as fh:
        offsets = {rva: va_to_off(ELFFile(fh), rva) for rva in targets}

    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = []
    for rva, name in sorted(targets.items()):
        window = data[offsets[rva]:offsets[rva] + WINDOW]
        lines, terminated = slice_instructions(data, offsets[rva], rva)
        if not terminated:
            print(f'warning: no clean ret/exit within window for {name} ({hex(rva)})')
        (out_dir / f'{rva:x}.asm').write_text('\n'.join(lines) + '\n', encoding='utf-8')
        manifest.append(dict(rva=hex(rva), name=name, offset=hex(offsets[rva]),
                             instrs=len(lines), terminated=terminated,
                             sha256=hashlib.sha256(window).hexdigest()))
    (out_dir / 'slices.json').write_text(
        json.dumps(dict(binary=hashlib.sha256(data).hexdigest(), slices=manifest),
                   indent=2) + '\n', encoding='utf-8')
    print(json.dumps({m['name']: m['instrs'] for m in manifest}))


if __name__ == '__main__':
    main()
