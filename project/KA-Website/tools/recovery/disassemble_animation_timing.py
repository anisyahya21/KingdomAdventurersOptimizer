"""Disassemble/decompile the AnimationComponent frame-timing path (throw-away Ghidra project).

Answers the timing questions for the battle replay: where AnimationComponent.rate comes from
(Entity.AddAnimation / the component ctor), what AnimationSystem.Update does per update
(SebComponent.frame += rate, wrap at the SEB maxFrame, backupBehaviour), and how the animation
system is driven (caller chain / loop pacing).

    python KA-Website/tools/recovery/disassemble_animation_timing.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
EVIDENCE = ROOT / "RE-evidence/20260918-battle-visual-replay/animation-timing"
BINARY = ROOT / "RE-evidence/G2.1/39257e72291d/inputs/libil2cpp.so"
SCRIPT_JSON = ROOT / "RE-evidence/G2.1/39257e72291d/dump/script.json"
EXPECTED = "fb834373cb3bd1dc7dac941fcf94113f3b5123e033cf0a41d5e00c0656e30208"

TARGETS = {
    0x146BA70: "Entity.AddAnimation",
    0x14C639C: "AnimationComponent..ctor",
    0x14C627C: "AnimationComponent.Serialize",
    0x14DC884: "AnimationSystem.Update",
    0x14C627C + 0x8C: "AnimationComponent.Deserialize",
    0x147780C: "World.CreateMonster",
    0x1478EA0: "World.CreateEffect",
    0x1475DA8: "World.CreateMapChip",
    0x14C1B08: "SystemManager.Update",
    0x14C1DD4: "SystemManager.Draw",
    0x1693E0C: "main.Main.OnUpdate",
    0x147B240: "World.Update",
    0x147B26C: "World.LateUpdate",
    0x2381B98: "FormManagerBase.GetTargetFps",
    0x16A8E8C: "BattleForm..ctor",
    0x16AA254: "BattleForm.Init",
    0x141F69C: "MyFormBase..ctor",
    0x237C8A8: "FormBase..ctor",
    0x16B8E14: "form.FormManager..ctor",
    0x16B9058: "form.FormManager.Updated",
    0x2384BF0: "FormManagerBase.SetTargetFps",
    0x16AAD2C: "BattleForm.DynamicRender",
    0x16AAC0C: "BattleForm.DrawOffscreen",
    0x16AAAF0: "BattleForm.Draw",
    0x2449EA8: "Offscreen.SetStaticsTransform",
    0x2449F78: "Offscreen.SetStaticsTransform2",
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
        transaction = program.startTransaction("Disassemble animation timing path")
        try:
            if program.getImageBase().getOffset() != 0:
                program.setImageBase(api.toAddr(0), True)
            listing = program.getListing()
            for address, label in sorted(TARGETS.items()):
                if address not in addresses:
                    continue
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
    (EVIDENCE / "animation-timing.json").write_text(
        json.dumps({"sha256": digest, "methods": exported}, indent=2) + "\n", encoding="utf-8"
    )
    print("wrote", EVIDENCE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
