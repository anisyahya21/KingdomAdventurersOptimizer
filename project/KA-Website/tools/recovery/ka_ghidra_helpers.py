"""Decompile the unnamed native helpers with Ghidra, inside this workspace.

The Ghidra MCP server is not exposed to this session, so this drives the same
Ghidra 12.1 installation directly through pyghidra, with its own project under
`outputs/ka-index/ghidra/` so the existing `ka-combat` project is never touched.

For each requested entry point it disassembles with flow following from the entry,
lets Ghidra compute the real function boundary, and decompiles it. The boundary is
the point: these addresses are not dumped managed methods, so the index could only
give them an upper bound. Callee and caller lists come from Ghidra's own graph and
are recorded as addresses; names are resolved later against the frozen dump.

Run with the isolated pyghidra environment:

    C:/Users/anisb/AppData/Roaming/uv/tools/pyghidra-mcp/Scripts/python.exe \\
        ka_ghidra_helpers.py --targets 0x1332890 0x12d23b8 ...
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("GHIDRA_INSTALL_DIR", "C:/Ghidra/ghidra_12.1_PUBLIC")
os.environ.setdefault("JAVA_HOME", "C:/Program Files/Eclipse Adoptium/jdk-21.0.11.10-hotspot")

WORKSPACE = Path(__file__).resolve().parent
EXPECTED_SHA256 = "fb834373cb3bd1dc7dac941fcf94113f3b5123e033cf0a41d5e00c0656e30208"
BINARY = Path(r"C:\Users\anisb\OneDrive\Desktop\replit kingdom adventures - Copy"
              r"\RE-evidence\G2.1\39257e72291d\inputs\libil2cpp.so")

DEFAULT_TARGETS = [0x1332890, 0x12D23B8, 0x13D2C14, 0x114C1C8, 0x12D23C0, 0x12D22A8,
                   0x12D2294, 0x12D2764, 0x12D23EC, 0x12D21B4, 0x12D27F4, 0x13327E8]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--targets", nargs="*", default=[hex(t) for t in DEFAULT_TARGETS])
    parser.add_argument("--window", type=lambda value: int(value, 0), default=0xA00,
                        help="maximum bytes to consider from each entry")
    parser.add_argument("--out", type=Path, default=WORKSPACE / "decompiled")
    parser.add_argument("--project", type=Path, default=WORKSPACE / "ghidra")
    parser.add_argument("--name", default="ka-helpers")
    args = parser.parse_args()

    targets = [int(text, 16) for text in args.targets]
    blob = BINARY.read_bytes()
    digest = hashlib.sha256(blob).hexdigest()
    if digest != EXPECTED_SHA256:
        raise SystemExit(f"binary hash mismatch: {digest}")

    import pyghidra
    pyghidra.start()
    from ghidra.app.cmd.disassemble import DisassembleCommand
    from ghidra.app.cmd.function import CreateFunctionCmd
    from ghidra.app.decompiler import DecompInterface
    from ghidra.program.model.address import AddressSet
    from ghidra.util.task import ConsoleTaskMonitor

    args.out.mkdir(parents=True, exist_ok=True)
    args.project.mkdir(parents=True, exist_ok=True)
    monitor = ConsoleTaskMonitor()
    report = {"binary": str(BINARY), "binarySha256": digest, "window": args.window,
              "project": str(args.project), "functions": []}

    with pyghidra.open_program(BINARY, project_location=str(args.project),
                               project_name=args.name, nested_project_location=False,
                               analyze=False) as api:
        program = api.getCurrentProgram()
        if program.getImageBase().getOffset() != 0:
            program.setImageBase(api.toAddr(0), True)
        assert str(program.getExecutableSHA256()).lower() == EXPECTED_SHA256
        decompiler = DecompInterface()
        decompiler.openProgram(program)
        transaction = program.startTransaction("Prepare unnamed helper entry points")
        try:
            for rva in targets:
                start = api.toAddr(rva)
                body = AddressSet(start, api.toAddr(rva + args.window - 1))
                DisassembleCommand(start, body, True).applyTo(program, monitor)
                CreateFunctionCmd(start).applyTo(program, monitor)
                function = program.getFunctionManager().getFunctionContaining(start)
                entry = {"rva": hex(rva), "created": function is not None}
                if function is None:
                    report["functions"].append(entry)
                    continue
                entry["entry"] = str(function.getEntryPoint())
                entry["bodyStart"] = str(function.getBody().getMinAddress())
                entry["bodyEnd"] = str(function.getBody().getMaxAddress())
                entry["bodyBytes"] = function.getBody().getNumAddresses()
                entry["callees"] = sorted({str(target.getEntryPoint())
                                           for target in function.getCalledFunctions(monitor)})
                entry["callers"] = sorted({str(source.getEntryPoint())
                                           for source in function.getCallingFunctions(monitor)})
                result = decompiler.decompileFunction(function, 90, monitor)
                entry["decompileCompleted"] = bool(result.decompileCompleted())
                entry["decompileError"] = str(result.getErrorMessage())
                if result.decompileCompleted():
                    text = str(result.getDecompiledFunction().getC())
                    (args.out / f"{rva:x}.c").write_text(text, encoding="utf-8")
                    entry["decompiledChars"] = len(text)
                report["functions"].append(entry)
                print(json.dumps(entry), flush=True)
        finally:
            program.endTransaction(transaction, True)
        decompiler.dispose()
        report["imageBase"] = str(program.getImageBase())
        report["limits"] = [
            "Imported without whole-program auto-analysis; only these entry points were "
            "disassembled, with flow following.",
            "Callee/caller lists are Ghidra's graph over what is disassembled so far, not "
            "a complete xref database.",
            "Decompiler prototypes for unnamed functions are inferred.",
        ]

    (args.out / "ghidra-helpers.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"prepared": sum(1 for f in report["functions"] if f.get("created")),
                      "requested": len(targets), "out": str(args.out)}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
