"""Generate the instruction listings the index reports as missing.

Scope rule
----------
Only functions a *current* document or a recovery tool already names are sliced,
and only game/project code by default. This is not a coverage pass over the
127,751-method dump; it fills the holes that current work keeps hitting.

What is produced
----------------
One listing per function under `slices/<rva>.asm`, in the same shape the existing
combat listings use, so a reader sees one format everywhere:

    147319c: stp x30, x21, [sp, #-0x20]!
    14731a0: ...
    14731b8: bl #0x146e90c kairo.unity.ecs.Entity$$AddGatherable

The instruction text is the original binary. The trailing name on a call line is
looked up mechanically in the frozen `script.json`; it is a label, not an
interpretation, and a target with no dumped name is left unlabelled rather than
guessed. `slices.json` records, per function: the index extent used, the trimmed
body end, the instruction count, the sha256 of the exact code bytes and the hash
of the binary they came from.

Nothing outside this workspace is written, and no function is renamed.

Usage
-----
    python ka_slice.py --from-index --tier A
    python ka_slice.py --rvas 0x147319c 0x16268c8
    python ka_slice.py --from-index --tier A,B,C,D --limit 400
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

import ka_index

MAX_INSTRUCTIONS = 8000


def load_method_names(conn: sqlite3.Connection):
    names = {row[0]: row[1] for row in conn.execute("SELECT rva, name FROM methods")}
    for rva, alias in conn.execute(
            "SELECT rva, alias FROM method_alias WHERE kind IN ('il2cpp-runtime','fixture-stub')"):
        names.setdefault(rva, alias)
    return names


def classify(conn: sqlite3.Connection, rva: int):
    """Current-workstanding citation count and tier for any function.

    Deliberately independent of whether a listing already exists, so the manifest
    can describe every file in the directory rather than only the current batch.
    """
    clause, params = ka_index.current_predicate()
    current = conn.execute(
        f"SELECT COUNT(*) FROM mentions x WHERE x.rva=? AND ({clause})",
        (rva, *params)).fetchone()[0]
    row = conn.execute("SELECT name FROM methods WHERE rva=?", (rva,)).fetchone()
    name = row["name"] if row else "(not a dumped method start)"
    if ka_index.FRAMEWORK_RE.match(name):
        tier = "-"
    elif current >= 8:
        tier = "A"
    elif current >= 4:
        tier = "B"
    elif current >= 2:
        tier = "C"
    elif current >= 1:
        tier = "D"
    else:
        tier = "record-only"
    return name, current, tier


def describe_listing(engine, blob, segments, extents, names, rva, path, selection):
    """Recompute one manifest entry from the frozen binary and the index."""
    extent = extents.get(rva)
    offset = segment_offset(segments, rva)
    if extent is None or offset is None:
        return None
    span = min(extent["instrs"], MAX_INSTRUCTIONS) * 4
    code = blob[offset:offset + span]
    last_ret_end = None
    last_address = None
    for ins in engine.disasm(code, rva):
        last_address = ins.address
        if ins.mnemonic == "ret":
            last_ret_end = ins.address + 4
        if ins.address + 4 - rva >= span:
            break
    instrs = sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    return {
        "rva": hex(rva),
        "name": names.get(rva, "(not a dumped method start)"),
        "listing": path.name,
        "selection": selection,
        "fileOffset": hex(offset),
        "indexExtent": [hex(extent["first_instr"]), hex(extent["last_instr"])],
        "extentInstrs": extent["instrs"],
        "bodyEnd": hex(last_ret_end) if last_ret_end else None,
        "instrs": instrs,
        "endsWithReturn": bool(last_address is not None and last_ret_end is not None
                               and last_address + 4 == last_ret_end),
        "truncated": extent["instrs"] > MAX_INSTRUCTIONS,
        "extentIsUpperBound": True,
        "codeBytes": span,
        "codeSha256": hashlib.sha256(code).hexdigest(),
    }


def segment_offset(segments, rva):
    for segment in segments:
        if segment["p_vaddr"] <= rva < segment["p_vaddr"] + segment["p_filesz"]:
            return segment["p_offset"] + rva - segment["p_vaddr"]
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=ka_index.DEFAULT_ROOT)
    parser.add_argument("--out", type=Path, default=ka_index.DEFAULT_OUT)
    parser.add_argument("--slices-dir", type=Path, default=ka_index.DEFAULT_SLICES)
    parser.add_argument("--from-index", action="store_true",
                        help="select functions from the index's gaps ranking")
    parser.add_argument("--tier", default="A", help="tiers to select, comma separated")
    parser.add_argument("--limit", type=int, default=120)
    parser.add_argument("--rvas", nargs="*", default=[], help="explicit addresses")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    conn = ka_index.open_db(args.out.resolve())
    native = ka_index.native_dir(args.root.resolve())
    binary = native / "inputs" / "libil2cpp.so"
    if not binary.is_file():
        raise SystemExit(f"frozen binary not found at {binary}")

    try:
        from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN
        from elftools.elf.elffile import ELFFile
    except ImportError as exc:
        raise SystemExit(f"run this under the repository .venv python: {exc}")

    # Selection -------------------------------------------------------------- #
    names = load_method_names(conn)
    selected: list[tuple[int, str, int]] = []
    if args.rvas:
        for text in args.rvas:
            rva = ka_index.normalize_rva(text)
            if rva is None:
                raise SystemExit(f"unrecognised rva {text!r}")
            row = conn.execute("SELECT rva, name FROM methods WHERE rva=?", (rva,)).fetchone()
            selected.append((rva, row["name"] if row else "(not a dumped method start)", 0))
    else:
        tiers = {tier.strip().upper() for tier in args.tier.split(",")}
        for record in ka_index.unsliced_cited(conn):
            if record["tier"] in tiers:
                selected.append((record["rva"], record["name"],
                                 record["current_citations"]))
        selected = selected[: args.limit]
    if not selected:
        raise SystemExit("nothing selected")

    # Disassemble ------------------------------------------------------------ #
    blob = binary.read_bytes()
    binary_sha = hashlib.sha256(blob).hexdigest()
    with binary.open("rb") as handle:
        elf = ELFFile(handle)
        segments = [dict(p_type=segment["p_type"], p_offset=segment["p_offset"],
                         p_vaddr=segment["p_vaddr"], p_filesz=segment["p_filesz"],
                         p_flags=segment["p_flags"]) for segment in elf.iter_segments()]
    engine = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)
    extents = {row["rva"]: row for row in conn.execute("SELECT * FROM code_map")}

    args.slices_dir.mkdir(parents=True, exist_ok=True)
    skipped = []
    written = []
    manifest = []
    for rva, name, current_citations in selected:
        extent = extents.get(rva)
        if extent is None:
            skipped.append({"rva": hex(rva), "name": name,
                            "reason": "the index has no decoded extent for this address"})
            continue
        offset = segment_offset(segments, rva)
        if offset is None:
            skipped.append({"rva": hex(rva), "name": name, "reason": "not file-backed"})
            continue
        count = extent["instrs"]
        span = min(count, MAX_INSTRUCTIONS) * 4
        code = blob[offset:offset + span]

        lines = []
        last_ret_end = None
        for ins in engine.disasm(code, rva):
            label = ""
            if ins.mnemonic in ("bl", "b") and ins.op_str.startswith("#"):
                label = names.get(int(ins.op_str[1:], 16), "")
            lines.append(f"{ins.address:x}: {ins.mnemonic} {ins.op_str} {label}".rstrip())
            if ins.mnemonic == "ret":
                last_ret_end = ins.address + 4
            if len(lines) >= MAX_INSTRUCTIONS:
                break
        if not lines:
            skipped.append({"rva": hex(rva), "name": name,
                            "reason": "no instruction decoded in the indexed extent"})
            continue

        # Trailing padding between methods decodes as instructions. The body ends at
        # the last `ret`; anything after it inside the extent is dropped from the
        # listing, which is why the manifest keeps both bounds and says so.
        if last_ret_end is not None:
            lines = [line for line in lines if int(line.split(":")[0], 16) < last_ret_end]

        target = args.slices_dir / f"{rva:x}.asm"
        if not args.dry_run:
            target.write_text("\n".join(lines) + "\n", encoding="utf-8")
        written.append(rva)

    if not args.dry_run:
        # The manifest describes the whole directory, not just this batch, so a
        # later tranche adds to it instead of replacing the record of an earlier one.
        manifest_path = args.slices_dir / "slices.json"
        previous = {}
        batches = []
        if manifest_path.is_file():
            old = json.loads(manifest_path.read_text(encoding="utf-8"))
            previous = {entry["rva"]: entry for entry in old.get("slices", [])}
            batches = list(old.get("batches", []))
        label = ("explicit addresses" if args.rvas else f"tiers {args.tier} from `ka_index.py gaps`")
        if label not in batches:
            batches.append(label)

        entries = []
        for path in sorted(args.slices_dir.glob("*.asm"), key=lambda p: int(p.stem, 16)):
            rva = int(path.stem, 16)
            _name, _current, tier = classify(conn, rva)
            previous_selection = previous.get(hex(rva), {}).get("selection")
            # An entry added from the ranking is described by its tier, which stays
            # correct as later batches are added; an explicitly requested address
            # keeps saying so.
            if previous_selection == "explicit addresses":
                selection = previous_selection
            elif tier in ("A", "B", "C", "D"):
                selection = f"tier {tier} from `ka_index.py gaps`"
            else:
                selection = previous_selection or label
            entry = describe_listing(engine, blob, segments, extents, names, rva, path, selection)
            if entry is None:
                continue
            entry["currentCitations"], entry["tier"] = _current, tier
            entries.append(entry)
        entries.sort(key=lambda entry: int(entry["rva"], 16))

        (args.slices_dir / "slices.json").write_text(json.dumps({
            "schema": 1,
            "producedBy": "ka_slice.py",
            "binary": str(binary),
            "binarySha256": binary_sha,
            "batches": batches,
            "method": ("Index extent from the linear sweep, disassembled from the frozen "
                       "binary, trailing padding after the last `ret` trimmed. Call labels "
                       "come from script.json; nothing is renamed or inferred."),
            "limits": ["The index extent is an upper bound; bodyEnd records where the "
                       "listing actually stops.",
                       "A listing is generated text over original code. The authority is "
                       "the binary hash plus the code hash per entry."],
            "slices": entries,
            "skipped": skipped,
        }, indent=2) + "\n", encoding="utf-8")
        manifest = entries

    print(json.dumps({"selected": len(selected), "written": len(written),
                      "skipped": len(skipped), "dry_run": args.dry_run,
                      "slices_in_manifest": (0 if args.dry_run else len(manifest)),
                      "dir": str(args.slices_dir)}, indent=2))
    for entry in skipped[:10]:
        print(f"  skipped {entry['rva']} {entry['name']}: {entry['reason']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
