"""Disassemble + decompile the z-sort depth chain from the frozen ELF.

Read-only analysis helper for PASS 14 (battle depth ordering). Opens the
hash-verified original ``libil2cpp.so`` in its own throw-away Ghidra project
(never the running MCP project), disassembles exactly the requested method
bodies, names BL targets from the matching IL2CPP ``script.json`` and writes
both the annotated ARM64 listing and the decompiler's pseudo-C.

Targets (frozen build 39257e72291d):

  ecs.RenderSystem.InsertZSortObject   0x15D0214  per-line z-sort queue insert
  ecs.RenderSystem.Init                0x15D61B0  default calcDepth wiring
  ecs.RenderSystem.LateUpdate          0x15D6224  frame driver for the queue
  ecs.RenderSystem..ctor               0x15D6010
  ecs.RenderSystem.CalcDepth..ctor     0x15D6124
  kairo.unity.ecs.SebComponentExt.InitLayers 0x147DD84
  ecs.RenderSystem.ToScreenPos         0x15CDEEC

    python KA-Website/tools/recovery/disassemble_zsort_depth.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
EVIDENCE = ROOT / "RE-evidence/20260919-battle-hud/zsort-decompile"
BINARY = ROOT / "RE-evidence/G2.1/39257e72291d/inputs/libil2cpp.so"
SCRIPT_JSON = ROOT / "RE-evidence/G2.1/39257e72291d/dump/script.json"
EXPECTED = "fb834373cb3bd1dc7dac941fcf94113f3b5123e033cf0a41d5e00c0656e30208"

TARGETS = {
    0x15D0214: "ecs.RenderSystem.InsertZSortObject",
    0x15D61B0: "ecs.RenderSystem.Init",
    0x15D6224: "ecs.RenderSystem.LateUpdate",
    0x15D6010: "ecs.RenderSystem..ctor",
    0x15D6124: "ecs.RenderSystem.CalcDepth..ctor",
    0x147DD84: "kairo.unity.ecs.SebComponentExt.InitLayers",
    0x15CDEEC: "ecs.RenderSystem.ToScreenPos",
}


def safe_stem(address: int, label: str) -> str:
    """Windows-safe file stem: IL2CPP lambda names contain characters like '<'."""
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", label)
    return f"{address:08x}-{cleaned.strip('_')}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-location", default="C:/Users/anisb/GhidraProjects/ka-zsort-dis")
    parser.add_argument("--project-name", default="ka-zsort-dis")
    parser.add_argument("--address", action="append", default=None,
                        help="extra RVA to export as hex, may repeat")
    args = parser.parse_args()

    binary_bytes = BINARY.read_bytes()
    digest = hashlib.sha256(binary_bytes).hexdigest()
    assert digest == EXPECTED, digest

    metadata = json.loads(SCRIPT_JSON.read_text(encoding="utf-8"))
    addresses = sorted({entry["Address"] for entry in metadata["ScriptMethod"]})
    names = {entry["Address"]: entry["Name"] for entry in metadata["ScriptMethod"]}

    targets = dict(TARGETS)
    for raw in args.address or []:
        value = int(raw, 16)
        targets[value] = names.get(value, f"extra_{value:08x}")

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
        transaction = program.startTransaction("Disassemble z-sort depth chain")
        try:
            if program.getImageBase().getOffset() != 0:
                program.setImageBase(api.toAddr(0), True)
            listing = program.getListing()
            for address, label in sorted(targets.items()):
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
                lines = [
                    f"; {label}  rva 0x{address:08x} end 0x{end:08x}",
                    f"; sha256 {digest}  image base 0",
                    "",
                ]
                for instruction in listing.getInstructions(body, True):
                    line = f"0x{instruction.getAddress().getOffset():08x}  {instruction}"
                    for reference in instruction.getReferencesFrom():
                        target = reference.getToAddress().getOffset()
                        if target in names:
                            line += f"        ; -> {names[target]}"
                        elif instruction.getMnemonicString() in ("bl", "b"):
                            line += f"        ; -> 0x{target:08x}"
                    lines.append(line)
                stem = safe_stem(address, label)
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
                print(f"  {label}: {len(lines) - 3} instructions (decompiled: {bool(c_path)})",
                      flush=True)
            decompiler.dispose()
            program.endTransaction(transaction, True)
            transaction = None
        finally:
            if transaction is not None:
                transaction.end()
    (EVIDENCE / "zsort-decompile.json").write_text(
        json.dumps({"sha256": digest, "methods": exported}, indent=2) + "\n", encoding="utf-8"
    )
    print("wrote", EVIDENCE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
