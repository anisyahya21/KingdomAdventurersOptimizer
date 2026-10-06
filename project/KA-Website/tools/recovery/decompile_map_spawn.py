"""Decompile the map monster-spawn selection path from the hash-verified ELF.

Question: how does the map decide which MonsterData row spawns on a tile, and does a
cave use the same path (terrain + area level) as the open map?

Run with the isolated pyghidra environment, from the KA-Website directory, with the
ka-combat project closed (the scratch project is used when ka-combat is locked):
  C:/Users/anisb/AppData/Roaming/uv/tools/pyghidra-mcp/Scripts/python.exe \
      tools/recovery/decompile_map_spawn.py
"""
import argparse
import hashlib
import json
import os
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
EVIDENCE = ROOT / "RE-evidence/20260917-map-monster-spawn"
BINARY = ROOT / "RE-evidence/G2.1/39257e72291d/inputs/libil2cpp.so"
SCRIPT_JSON = ROOT / "RE-evidence/G2.1/39257e72291d/dump/script.json"
EXPECTED = "fb834373cb3bd1dc7dac941fcf94113f3b5123e033cf0a41d5e00c0656e30208"

TARGETS = {
    0x15666CC: "EnemyBaseSystem.ChooseMonsterData",
    0x1565798: "EnemyBaseSystem.GenerateMonster",
    0x1565F14: "EnemyBaseSystem.CreateMonster",
    0x1566268: "EnemyBaseSystem.GenerateMonsterFromEnemyGenerators",
    0x15670EC: "EnemyBaseSystem.ChooseMonster",
    0x15649BC: "EnemyBaseSystem.GenerateDungeon",
}


def method_end(address: int, addresses: list[int]) -> int:
    for candidate in addresses:
        if candidate > address:
            return candidate
    raise SystemExit(f"no following method for {address:#x}")


def load_segments(data: bytes) -> list[tuple[int, int, int]]:
    """PT_LOAD (p_vaddr, p_offset, p_filesz) triples; RVAs are not file offsets in general."""
    assert data[:4] == b"\x7fELF", "not an ELF"
    assert data[4] == 2, "expected ELF64"
    phoff = struct.unpack_from("<Q", data, 0x20)[0]
    phentsize = struct.unpack_from("<H", data, 0x36)[0]
    phnum = struct.unpack_from("<H", data, 0x38)[0]
    segments = []
    for index in range(phnum):
        base = phoff + index * phentsize
        if struct.unpack_from("<I", data, base)[0] != 1:  # PT_LOAD
            continue
        p_offset = struct.unpack_from("<Q", data, base + 8)[0]
        p_vaddr = struct.unpack_from("<Q", data, base + 16)[0]
        p_filesz = struct.unpack_from("<Q", data, base + 32)[0]
        segments.append((p_vaddr, p_offset, p_filesz))
    return segments


def rva_to_offset(segments: list[tuple[int, int, int]], rva: int) -> int:
    for vaddr, offset, filesz in segments:
        if vaddr <= rva < vaddr + filesz:
            return offset + (rva - vaddr)
    raise SystemExit(f"RVA {rva:#x} is not inside a loaded segment")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-location",
        default="C:/Users/anisb/GhidraProjects/ka-spawn-scratch",
        help="Override when the shared ka-combat project is locked by another process.",
    )
    parser.add_argument("--project-name", default="ka-spawn-scratch")
    args = parser.parse_args()

    binary_bytes = BINARY.read_bytes()
    digest = hashlib.sha256(binary_bytes).hexdigest()
    assert digest == EXPECTED, digest
    segments = load_segments(binary_bytes)

    metadata = json.loads(SCRIPT_JSON.read_text(encoding="utf-8"))
    addresses = sorted({entry["Address"] for entry in metadata["ScriptMethod"]})
    names = {entry["Address"]: entry["Name"] for entry in metadata["ScriptMethod"]}
    for address, label in TARGETS.items():
        assert names.get(address), f"address {address:#x} not in metadata ({label})"

    os.environ.setdefault("GHIDRA_INSTALL_DIR", "C:/Ghidra/ghidra_12.1_PUBLIC")
    os.environ.setdefault("JAVA_HOME", "C:/Program Files/Eclipse Adoptium/jdk-21.0.11.10-hotspot")
    import pyghidra

    pyghidra.start()
    from ghidra.app.cmd.disassemble import DisassembleCommand
    from ghidra.app.decompiler import DecompInterface
    from ghidra.program.model.address import AddressSet
    from ghidra.program.model.symbol import SourceType
    from ghidra.util.task import ConsoleTaskMonitor

    out = EVIDENCE / "ghidra"
    out.mkdir(parents=True, exist_ok=True)
    written = []
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
        transaction = program.startTransaction("Bound and decompile map monster spawn methods")
        try:
            # Ghidra imports this ET_DYN at 0x00100000; normalize to the RVA convention
            # used by the matching IL2CPP dump. Must happen inside the transaction.
            if program.getImageBase().getOffset() != 0:
                program.setImageBase(api.toAddr(0), True)
            for address, label in sorted(TARGETS.items()):
                end = method_end(address, addresses)
                start_addr = api.toAddr(address)
                body = AddressSet(start_addr, api.toAddr(end - 1))
                file_offset = rva_to_offset(segments, address)
                span = min(16, end - address)
                assert bytes(
                    int(value) & 255 for value in api.getBytes(start_addr, span)
                ) == binary_bytes[file_offset:file_offset + span], f"byte mismatch at {address:#x}"
                DisassembleCommand(start_addr, body, False).applyTo(program, monitor)
                function = program.getFunctionManager().getFunctionAt(start_addr)
                if function is None:
                    function = program.getFunctionManager().createFunction(
                        label.replace(".", "_"), start_addr, body, SourceType.USER_DEFINED
                    )
                decompiler = DecompInterface()
                decompiler.openProgram(program)
                result = decompiler.decompileFunction(function, 90, monitor)
                assert result.decompileCompleted(), f"decompile failed for {label}"
                c_path = out / f"{address:08x}.c"
                c_path.write_text(result.getDecompiledFunction().getC(), encoding="utf-8")
                decompiler.dispose()
                print(f"  {label} -> {c_path.name}", flush=True)
                written.append(
                    {
                        "rva": f"0x{address:08x}",
                        "fileOffset": f"0x{file_offset:08x}",
                        "end": f"0x{end:08x}",
                        "label": label,
                        "metadataName": names[address],
                        "cFile": c_path.name,
                    }
                )
        except Exception:
            import traceback

            traceback.print_exc()
            raise
        finally:
            transaction.end()
    (EVIDENCE / "decompilation.json").write_text(
        json.dumps(
            {
                "binary": str(BINARY.relative_to(ROOT)),
                "sha256": digest,
                "imageBase": 0,
                "methods": written,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    for entry in written:
        print(entry["rva"], entry["end"], entry["label"], entry["cFile"])


if __name__ == "__main__":
    main()
