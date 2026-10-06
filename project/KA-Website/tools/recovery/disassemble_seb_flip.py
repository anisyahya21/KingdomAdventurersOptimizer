"""Disassemble/decompile the Seb flip + Graphics mirror path in a throw-away Ghidra project.

Answers the direction/mirror questions for the battle replay: how the flipped frame variant is
encoded (``IsFlipFrame``/``GetFlipFrame``/``GetRemoveFlipFrame``), what ``reversU``/``reversV``
do to the sprite (``ConvBufferToSprite``), and how the z-sort draw turns them into a Graphics
mirror mode (``AppData.DrawZSortObjects`` -> ``Graphics.DrawImage``/``DrawScaledImage``).

    python KA-Website/tools/recovery/disassemble_seb_flip.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
EVIDENCE = ROOT / "RE-evidence/20260918-battle-visual-replay/seb-flip"
BINARY = ROOT / "RE-evidence/G2.1/39257e72291d/inputs/libil2cpp.so"
SCRIPT_JSON = ROOT / "RE-evidence/G2.1/39257e72291d/dump/script.json"
EXPECTED = "fb834373cb3bd1dc7dac941fcf94113f3b5123e033cf0a41d5e00c0656e30208"

TARGETS = {
    0x2351444: "Seb.IsFlipHorizontal",
    0x2351450: "Seb.IsFlipVertical",
    0x235145C: "Seb.GetRemoveFlipFrame",
    0x2351480: "Seb.IsFlipFrame",
    0x2358500: "Seb.GetFlipFrame",
    0x2352EE8: "Seb.get_Flip",
    0x2352EF0: "Seb.set_Flip",
    0x2351A4C: "Seb.GetCurFrame",
    0x2351A54: "Seb.SetCurFrame",
    0x2352EFC: "Seb.ConvBufferToSprite",
    0x2355310: "Seb.DrawRectLayer",
    0x2305030: "Graphics.DrawImage",
    0x2305160: "Graphics.DrawScaledImage",
    0x167110C: "AppData.DrawZSortObjects",
    0x145F3F4: "Direction.Invert",
    0x1582B10: "FighterSystem.InitFighters",
}


def load_segments(data: bytes) -> list[tuple[int, int, int]]:
    assert data[:4] == b"\x7fELF" and data[4] == 2, "expected ELF64"
    phoff = struct.unpack_from("<Q", data, 0x20)[0]
    phentsize = struct.unpack_from("<H", data, 0x36)[0]
    phnum = struct.unpack_from("<H", data, 0x38)[0]
    segments = []
    for index in range(phnum):
        base = phoff + index * phentsize
        if struct.unpack_from("<I", data, base)[0] != 1:
            continue
        segments.append(
            (
                struct.unpack_from("<Q", data, base + 16)[0],
                struct.unpack_from("<Q", data, base + 8)[0],
                struct.unpack_from("<Q", data, base + 32)[0],
            )
        )
    return segments


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-location", default="C:/Users/anisb/GhidraProjects/ka-seb-dis")
    parser.add_argument("--project-name", default="ka-seb-dis")
    args = parser.parse_args()

    binary_bytes = BINARY.read_bytes()
    digest = hashlib.sha256(binary_bytes).hexdigest()
    assert digest == EXPECTED, digest
    load_segments(binary_bytes)

    metadata = json.loads(SCRIPT_JSON.read_text(encoding="utf-8"))
    addresses = sorted({entry["Address"] for entry in metadata["ScriptMethod"]})
    names = {entry["Address"]: entry["Name"] for entry in metadata["ScriptMethod"]}

    os.environ.setdefault("GHIDRA_INSTALL_DIR", "C:/Ghidra/ghidra_12.1_PUBLIC")
    os.environ.setdefault("JAVA_HOME", "C:/Program Files/Eclipse Adoptium/jdk-21.0.11.10-hotspot")
    import pyghidra

    pyghidra.start()
    from ghidra.app.cmd.disassemble import DisassembleCommand
    from ghidra.app.decompiler import DecompInterface
    from ghidra.program.model.address import AddressSet
    from ghidra.program.model.symbol import SourceType
    from ghidra.util.task import ConsoleTaskMonitor

    out = EVIDENCE / "disassembly"
    out.mkdir(parents=True, exist_ok=True)
    exported = []
    with pyghidra.open_program(
        BINARY,
        project_location=args.project_location,
        project_name=args.project_name,
        nested_project_location=False,
        analyze=False,
    ) as api:
        program = api.getCurrentProgram()
        assert str(program.getExecutableSHA256()).lower() == EXPECTED
        monitor = ConsoleTaskMonitor()
        decompiler = DecompInterface()
        decompiler.openProgram(program)
        transaction = program.startTransaction("Disassemble Seb flip path")
        try:
            if program.getImageBase().getOffset() != 0:
                program.setImageBase(api.toAddr(0), True)
            listing = program.getListing()
            for address, label in sorted(TARGETS.items()):
                index = addresses.index(address)
                end = addresses[index + 1]
                start_addr = api.toAddr(address)
                body = AddressSet(start_addr, api.toAddr(end - 1))
                DisassembleCommand(start_addr, body, False).applyTo(program, monitor)
                cursor = start_addr
                while cursor.getOffset() < end:
                    if listing.getInstructionAt(cursor) is None:
                        DisassembleCommand(cursor, None, False).applyTo(program, monitor)
                    cursor = cursor.add(4)
                function = program.getFunctionManager().getFunctionAt(start_addr)
                if function is None:
                    function = program.getFunctionManager().createFunction(
                        label.replace(".", "_"), start_addr, body, SourceType.USER_DEFINED
                    )
                lines = [f"; {label}  rva 0x{address:08x} end 0x{end:08x}", f"; sha256 {digest}  image base 0", ""]
                for instruction in listing.getInstructions(body, True):
                    line = f"0x{instruction.getAddress().getOffset():08x}  {instruction}"
                    for reference in instruction.getReferencesFrom():
                        target = reference.getToAddress().getOffset()
                        if target in names:
                            line += f"        ; -> {names[target]}"
                        elif instruction.getMnemonicString() in ("bl", "b"):
                            line += f"        ; -> 0x{target:08x}"
                    lines.append(line)
                stem = f"{address:08x}-{label.replace('.', '_')}"
                (out / f"{stem}.asm").write_text("\n".join(lines) + "\n", encoding="utf-8")
                c_path = None
                if function is not None:
                    result = decompiler.decompileFunction(function, 180, monitor)
                    if result is not None and result.decompileCompleted():
                        c_path = out / f"{stem}.c"
                        c_path.write_text(result.getDecompiledFunction().getC(), encoding="utf-8")
                exported.append(
                    {
                        "rva": f"0x{address:08x}",
                        "end": f"0x{end:08x}",
                        "label": label,
                        "metadataName": names.get(address),
                        "instructions": len(lines) - 3,
                        "asmFile": f"{stem}.asm",
                        "cFile": c_path.name if c_path else None,
                    }
                )
                print(f"  {label}: {len(lines) - 3} instructions (decompiled: {bool(c_path)})", flush=True)
            decompiler.dispose()
            program.endTransaction(transaction, True)
            transaction = None
        finally:
            if transaction is not None:
                transaction.end()
    (EVIDENCE / "seb-flip.json").write_text(
        json.dumps({"sha256": digest, "methods": exported}, indent=2) + "\n", encoding="utf-8"
    )
    print("wrote", EVIDENCE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
