"""Disassemble the map monster-spawn selection path with Ghidra, resolving IL2CPP names.

The C decompiler produced unusable output for these ranges (undefined code in the
no-auto-analysis project), so this exports annotated ARM64 disassembly instead: every
BL/B target is labelled with the IL2CPP method name from the matching script.json.
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
    0x15649BC: "GenerateDungeon",
    0x1565798: "GenerateMonster",
    0x1565F14: "CreateMonster",
    0x1566268: "GenerateMonsterFromEnemyGenerators",
    0x15666CC: "ChooseMonsterData",
    0x15670EC: "ChooseMonster",
    0x15681EC: "DropItem",
    0x1568A7C: "DropTreasure",
    0x1569830: "ChooseMonsterData_b__0_terrainPredicate",
    0x156985C: "ChooseMonsterData_b__1_monsterPredicate",
    0x1569920: "ChooseMonsterData_b__1_trueTail",
    0x14DB154: "AnimalSystem.GenerateAnimal",
    0x1487900: "AISystem.ScrGather_callWindow",
    0x14A1F20: "AISystem.UpdateExplore_callWindow",
}


def method_bounds(address: int, addresses: list[int]) -> int:
    return next(candidate for candidate in addresses if candidate > address)


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


def rva_to_offset(segments, rva: int) -> int:
    for vaddr, offset, filesz in segments:
        if vaddr <= rva < vaddr + filesz:
            return offset + (rva - vaddr)
    raise SystemExit(f"RVA {rva:#x} outside loaded segments")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-location", default="C:/Users/anisb/GhidraProjects/ka-spawn-dis")
    parser.add_argument("--project-name", default="ka-spawn-dis")
    args = parser.parse_args()

    binary_bytes = BINARY.read_bytes()
    digest = hashlib.sha256(binary_bytes).hexdigest()
    assert digest == EXPECTED, digest
    segments = load_segments(binary_bytes)

    metadata = json.loads(SCRIPT_JSON.read_text(encoding="utf-8"))
    addresses = sorted({entry["Address"] for entry in metadata["ScriptMethod"]})
    names = {entry["Address"]: entry["Name"] for entry in metadata["ScriptMethod"]}
    # Some targets are call windows or branch tails that are not method starts; only the
    # real method entries must exist in the IL2CPP metadata.
    window_targets = {0x1569920, 0x1487900, 0x14A1F20}

    os.environ.setdefault("GHIDRA_INSTALL_DIR", "C:/Ghidra/ghidra_12.1_PUBLIC")
    os.environ.setdefault("JAVA_HOME", "C:/Program Files/Eclipse Adoptium/jdk-21.0.11.10-hotspot")
    import pyghidra

    pyghidra.start()
    from ghidra.app.cmd.disassemble import DisassembleCommand
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
        transaction = program.startTransaction("Disassemble map spawn methods")
        try:
            if program.getImageBase().getOffset() != 0:
                program.setImageBase(api.toAddr(0), True)
            listing = program.getListing()
            for address, label in sorted(TARGETS.items()):
                end = method_bounds(address, addresses)
                start_addr = api.toAddr(address)
                body = AddressSet(start_addr, api.toAddr(end - 1))
                offset = rva_to_offset(segments, address)
                span = min(16, end - address)
                assert bytes(
                    int(value) & 255 for value in api.getBytes(start_addr, span)
                ) == binary_bytes[offset:offset + span], f"byte mismatch at {address:#x}"
                DisassembleCommand(start_addr, body, False).applyTo(program, monitor)
                if program.getFunctionManager().getFunctionAt(start_addr) is None:
                    program.getFunctionManager().createFunction(
                        f"EnemyBaseSystem_{label}", start_addr, body, SourceType.USER_DEFINED
                    )
                lines = [
                    f"; {label}  rva 0x{address:08x} end 0x{end:08x}  "
                    f"({names.get(address, 'call window, not a method start')})",
                    f"; sha256 {digest}  image base 0",
                    "",
                ]
                for instruction in listing.getInstructions(body, True):
                    text = instruction.toString()
                    line = f"0x{instruction.getAddress().getOffset():08x}  {text}"
                    for reference in instruction.getReferencesFrom():
                        target = reference.getToAddress().getOffset()
                        if target in names:
                            line += f"        ; -> {names[target]}"
                        elif instruction.getMnemonicString() in ("bl", "b"):
                            line += f"        ; -> 0x{target:08x}"
                    lines.append(line)
                asm_path = out / f"{address:08x}-{label}.asm"
                asm_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                exported.append(
                    {
                        "rva": f"0x{address:08x}",
                        "end": f"0x{end:08x}",
                        "label": label,
                        "metadataName": names.get(address) if address not in window_targets else None,
                        "asmFile": asm_path.name,
                    }
                )
                print(f"  {label}: {len(lines) - 3} instructions -> {asm_path.name}", flush=True)
        finally:
            transaction.end()
    (EVIDENCE / "disassembly.json").write_text(
        json.dumps({"sha256": digest, "methods": exported}, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
