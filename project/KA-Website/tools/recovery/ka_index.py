"""Kingdom Adventurers native-code index: build and query.

Purpose
-------
Turn the frozen, already-hash-verified artifacts of the Kingdom Adventurers
reverse-engineering effort into one searchable database, so a later investigation
can retrieve a small relevant package (functions, relationships, evidence, doc
sections) instead of re-grepping a 20 MB IL2CPP dump and several 200 KB documents.

Everything this tool writes is derived. It never modifies the evidence tree.
Original identities are preserved: the Il2CppDumper name/address/signature for a
method is always stored as-is, and any additional name is stored separately as an
alias with an explicit kind, confidence and basis.

Inputs (all read-only)
----------------------
  <root>/RE-evidence/G2.1/39257e72291d/dump/script.json    all 127,751 methods
  <root>/RE-evidence/G2.1/39257e72291d/dump/dump.cs        types + field offsets
  <root>/RE-evidence/G2.1/39257e72291d/inputs/libil2cpp.so optional: extents, xrefs
  <root>/RE-evidence/20260912-combat/audit.json           503 audited slice identities
  *.asm / *.c / *.json under the evidence roots           artifacts + citations
  *.md / *.py under the docs and tool roots               citations

Usage
-----
  python ka_index.py build  [--root DIR] [--out DIR] [--no-xrefs]
  python ka_index.py query  [--rva 0x1473234] [--name AddTreasure] [--topic treasure]
  python ka_index.py search "treasure capacity" [--limit 40] [--phrase]
  python ka_index.py callers 0x1473234
  python ka_index.py callees 0x1473234
  python ka_index.py at 0x16f5330
  python ka_index.py stats

`build --no-xrefs` needs no third-party packages. The default build reads the
51 MB ELF with capstone + pyelftools, which are installed in the repository
virtual environment:

  "<root>/.venv/Scripts/python.exe" ka_index.py build
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Repository-local by construction: `<root>/KA-Website/tools/recovery/ka_index.py`.
DEFAULT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUT = DEFAULT_ROOT / "RE-evidence/20260920-native-index/build"
DEFAULT_SLICES = DEFAULT_ROOT / "RE-evidence/20260920-native-index/slices"
DEFAULT_DECOMPILED = DEFAULT_ROOT / "RE-evidence/20260920-native-index/decompiled"
DEFAULT_HELPER_ANALYSIS = DEFAULT_ROOT / "RE-evidence/20260920-native-index/helper-analysis.json"
DEFAULT_OVERLAY = DEFAULT_ROOT / "RE-evidence/20260919-combat-registry/combat-registry.json"
DEFAULT_KEYS = DEFAULT_ROOT / "RE-evidence/20260919-combat-registry/combat-keys.json"
DEFAULT_MANIFEST = DEFAULT_ROOT / "RE-evidence/20260920-native-index/build/index-manifest.json"
DB_NAME = "ka-index.sqlite"
INDEX_VERSION = "2"

# Source roots that are authoritative for this index. Anything not listed here is
# excluded and reported as excluded, so "why is this not indexed" is always answerable.
INCLUDED_ROOTS = (
    ("KA-Website/docs", "doc"),
    ("KA-Website/tools", "tool"),
    ("AGENTS.md", "doc"),
    ("Handoff and start prompts", "doc"),
    ("RE-evidence", "evidence"),
    ("wairo_full_encounter_review", "evidence"),
    ("RE-audit-2026-09-10", "audit"),
)
EXCLUDED_ROOTS = (
    "reverse engineering with deepseek/",   # quarantined working copy, non-authoritative
    "RE-archive/", "KA-Legacy-Archive/",    # archived snapshots
    "RE-evidence/20260920-native-index/",   # this index's own build output
    "RE-evidence/20260919-combat-registry/",  # the combat overlay, derived from this index
    "data/", "apk kingdom adventures/", ".pnpm-store/", ".venv/", "node_modules/",
    "vercel-deployment-cleanup-20260917/", "KA-Website/artifacts/", "KA-Website/tools/asset_extractor/",
)
# Generated analysis artifacts: indexed and hash-recorded, but never treated as citations.
# Every operand address inside a disassembly dump would otherwise register as a mention and
# swamp the ranking that `gaps` and `package` depend on.
DUMP_DIR_MARKERS = ("/disassembly/", "/ghidra/", "/dumps/", "/zsort-decompile/")
DUMP_NAME_PATTERNS = ("stringliteral.json", "dump.cs", "script.json", "il2cpp.h")
DUMP_SUFFIXES = ("-checks.json",)
# Locally generated run output. These are rewritten by every gate run (they carry wall-clock
# fields), so they are indexed for search but never treated as evidence drift.
VOLATILE_OUTPUT_NAMES = ("check-runner.json",)


def is_volatile_output(rel_text: str) -> bool:
    name = rel_text.replace("\\", "/").rsplit("/", 1)[-1]
    return name in VOLATILE_OUTPUT_NAMES or name.endswith(DUMP_SUFFIXES)
# The site ships a handful of recovered native-fact files. They are evidence, not dumps,
# so they are indexed even though the rest of the app tree is not.
SHIPPED_NATIVE_RE = re.compile(
    r"^KA-Website/artifacts/kingdom-adventures/src/"
    r"(lib/native-[\w-]+\.ts|game-data/(native-[\w-]+|battle-animation|human-battle-idle)\.json)$")
# Superseded ledgers: indexed as historical `record`, never as current guidance.
HISTORICAL_FILES = ("PLAN.md", "reviews.jsonl", "claims.json", "documents.json", "work-items.json")
# The index's own implementation. It is tooling about the index rather than evidence, and
# indexing it would make every edit to this file invalidate the build it produced.
SELF_FILES = ("ka_index.py", "ka_slice.py", "ka_brief_bench.py", "ka_ghidra_helpers.py",
              "build_combat_registry.py", "combat_native.py", "check_combat_registry.py",
              "check_combat_native_layer.py")

# The documents that describe the work as it currently stands, plus the recovery
# tools and their evidence. Everything else in the indexed corpus is a historical
# record (quarantine packets, archived before-images, the 2026-09 superseded set),
# which is still searched but never used to rank what is worth slicing next.
CURRENT_SOURCES = (
    "CURRENT.md", "special-combat.md", "HANDOFF-b3-treasure-receipt.md", "treasure-lookup.md",
    "state.json", "combat-sandbox.md", "combat-community-observations.md",
    "surround-effects.md", "treasure-receipt-addtreasure-countstock",
    "treasure-receipt-createtreasure", "rng-seed-provenance", "NATIVE-INDEX.md",
    r"KA-Website\tools\recovery",
)

# BCL, engine and third-party namespaces. A listing of `Enumerable.Sum` explains
# bookkeeping but is not game logic, so these are ranked out of the default batch.
FRAMEWORK_RE = re.compile(
    r"^(System\.|UnityEngine\.|Newtonsoft\.|MoonSharp\.|Mono\.|Microsoft\.|Unity\.|"
    r"Firebase\.|Tapjoy\.|TMPro\.|DG\.|JetBrains\.|<)")

RVA_RE = re.compile(r"0x([0-9A-Fa-f]{5,8})\b")
DOTTED_RE = re.compile(
    r"\b[A-Za-z_][A-Za-z0-9_]*(?:<[^<>]{0,40}>)?(?:\.[A-Za-z_][A-Za-z0-9_]*(?:<[^<>]{0,40}>)?)+"
    r"(?:\$\$[A-Za-z0-9_<>]+)?")
DOLLAR_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_.]*\$\$[A-Za-z0-9_<>]+")
FIELD_RE = re.compile(r"^\s*(?:\[[^\]]*\]\s*)?(?:public|private|protected|internal|static|readonly|const|volatile|\s)*[\w<>,.\[\]?]+\s+([\w<>@]+)\s*;\s*//\s*(0x[0-9A-Fa-f]+)")
TYPE_RE = re.compile(r"^((?:public|private|internal|protected|sealed|abstract|static|partial|\s)*)(class|struct|interface|enum)\s+([\w`<>@.,]+)")
NS_RE = re.compile(r"^//\s*Namespace:\s*(\S*)\s*$")
IDX_RE = re.compile(r"//\s*TypeDefIndex:\s*(\d+)")
ASM_CALL_RE = re.compile(r"^\s*([0-9a-fA-F]+):\s+(bl|b)\s+#0x([0-9A-Fa-f]+)")

TEXT_EXT = {".md", ".py", ".json", ".jsonl", ".asm", ".c", ".txt", ".tsv", ".csv"}
FIELD_MODIFIERS = {"public", "private", "protected", "internal", "static", "readonly",
                   "const", "volatile", "new", "unsafe", "extern"}

# IL2CPP runtime helpers that the existing evidence already names. Seeded only from
# files in the repository that state the identification; never guessed from usage.
RUNTIME_HELPERS = {
    0x12D23C8: ("il2cpp_raise_null_reference",
                "prepare_ghidra_combat.py marks this no-return wrapper as the null-reference raiser"),
    0x12D23D0: ("il2cpp_raise_bounds_exception",
                "prepare_ghidra_combat.py marks this no-return wrapper as the bounds-exception raiser"),
}
SKIP_DIRS = {"__pycache__", ".git", "node_modules", ".venv", "DummyDll", "inputs"}
MAX_FILE_BYTES = 6_000_000


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_rva(value):
    if isinstance(value, int):
        return value
    text = str(value or "").strip().lower()
    if text.startswith("0x"):
        text = text[2:]
    if not text or len(text) > 9 or any(c not in "0123456789abcdef" for c in text):
        return None
    return int(text, 16)


def split_name(name: str):
    owner, sep, method = name.partition("$$")
    return (owner, method) if sep else (name, "")


def short_type(full: str) -> str:
    base = full.rsplit(".", 1)[-1]
    return re.sub(r"<.*>$", "", base).lstrip(".")


def relative(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


# --------------------------------------------------------------------------- #
# schema
# --------------------------------------------------------------------------- #


SCHEMA = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE methods(
  rva INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  owner TEXT,
  method TEXT,
  signature TEXT,
  type_signature TEXT
);
CREATE INDEX methods_owner ON methods(owner);
CREATE INDEX methods_method ON methods(method);

CREATE TABLE method_alias(
  rva INTEGER, alias TEXT, kind TEXT, confidence TEXT, basis TEXT, source TEXT
);
CREATE INDEX alias_rva ON method_alias(rva);
CREATE INDEX alias_alias ON method_alias(alias);

CREATE TABLE types(
  name TEXT PRIMARY KEY, short TEXT, namespace TEXT, typedef_index INTEGER, decl_line INTEGER
);

CREATE TABLE fields(
  type TEXT, field TEXT, field_type TEXT, offset INTEGER, is_static INTEGER
);
CREATE INDEX fields_type ON fields(type);

CREATE TABLE audit_identity(
  rva INTEGER PRIMARY KEY, name TEXT, file_offset INTEGER, end_rva INTEGER,
  sha256 TEXT, source TEXT
);

CREATE TABLE artifacts(
  rva INTEGER, kind TEXT, rel TEXT, bytes INTEGER, sha256 TEXT,
  instrs INTEGER, notes TEXT
);
CREATE INDEX artifacts_rva ON artifacts(rva);

CREATE TABLE mentions(
  rva INTEGER, how TEXT, kind TEXT, rel TEXT, line INTEGER,
  heading TEXT, snippet TEXT
);
CREATE INDEX mentions_rva ON mentions(rva);

CREATE TABLE calls(
  caller_rva INTEGER, callee_rva INTEGER, site INTEGER, kind TEXT, source TEXT,
  source_kind TEXT, target_kind TEXT
);
CREATE INDEX calls_caller ON calls(caller_rva);
CREATE INDEX calls_callee ON calls(callee_rva);
CREATE INDEX calls_site ON calls(site);

-- Non-method native code that current evidence deliberately reached: jump-table arms,
-- windows inside a larger function, and listings produced for an address that is not a
-- managed method start. These are native evidence *without* a managed identity, and they
-- are never inserted into `methods`, so the two categories stay distinguishable.
CREATE TABLE code_windows(
  rva INTEGER PRIMARY KEY, name TEXT, kind TEXT, manifest TEXT, instrs INTEGER,
  sha256 TEXT, end_rva INTEGER, inside_method INTEGER, note TEXT
);

CREATE TABLE code_map(
  rva INTEGER PRIMARY KEY, first_instr INTEGER, last_instr INTEGER, instrs INTEGER,
  end_rva INTEGER, source TEXT
);

CREATE TABLE regions(
  name TEXT, file_offset INTEGER, vaddr INTEGER, filesz INTEGER, memsz INTEGER,
  executable INTEGER, writable INTEGER
);

-- Executable runs that no dumped managed method claims. These are the native
-- helpers, thunks and IL2CPP runtime routines that the documents cite by address
-- without a name. Recording them keeps "cited but not a method start" honest and
-- gives future work a ranked starting list instead of a guess.
CREATE TABLE native_regions(
  start_rva INTEGER PRIMARY KEY, last_instr INTEGER, instrs INTEGER
);

-- Findings for addressing holes that are not managed methods and therefore carry
-- no name anywhere. A classification is stored, never an invented name.
CREATE TABLE helper_findings(
  rva INTEGER PRIMARY KEY, verdict TEXT, confidence TEXT, implementation TEXT,
  observed TEXT, evidence TEXT, anchor TEXT, note TEXT
);

CREATE TABLE doc_sections(
  path TEXT, rel TEXT, kind TEXT, level INTEGER, heading TEXT, start_line INTEGER,
  end_line INTEGER, rvas INTEGER
);
CREATE INDEX doc_sections_rel ON doc_sections(rel);

CREATE TABLE files(
  path TEXT PRIMARY KEY, rel TEXT, kind TEXT, bytes INTEGER, sha256 TEXT, duplicate_of TEXT
);

CREATE VIRTUAL TABLE search USING fts5(rel, kind UNINDEXED, line UNINDEXED, text);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=OFF")
    conn.execute("PRAGMA synchronous=OFF")
    return conn


# --------------------------------------------------------------------------- #
# build: frozen metadata
# --------------------------------------------------------------------------- #


def native_dir(root: Path) -> Path:
    return root / "RE-evidence" / "G2.1" / "39257e72291d"


def load_methods(conn: sqlite3.Connection, native: Path) -> dict:
    payload = json.loads((native / "dump" / "script.json").read_text(encoding="utf-8"))
    rows = []
    for entry in payload["ScriptMethod"]:
        rva = normalize_rva(entry.get("Address"))
        if rva is None:
            continue
        name = entry.get("Name") or ""
        owner, method = split_name(name)
        rows.append((rva, name, owner, method, entry.get("Signature"), entry.get("TypeSignature")))
    conn.executemany("INSERT OR REPLACE INTO methods VALUES (?,?,?,?,?,?)", rows)
    return {"methods": len(rows)}


def load_types_and_fields(conn: sqlite3.Connection, native: Path) -> dict:
    """Parse dump.cs for declared types and their field offsets.

    dump.cs carries a line comment per field of the form `// 0x28`. Offsets are
    stored exactly as the dump states them, so a static or unlaid-out field reads
    0 and can never be mistaken for a real instance offset.
    """
    type_rows = {}
    field_rows = []
    namespace = ""
    current = None
    line_no = 0
    with (native / "dump" / "dump.cs").open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line_no += 1
            if not line.strip():
                continue
            stripped = line.strip()
            ns_match = NS_RE.match(line)
            if ns_match:
                namespace = ns_match.group(1)
                continue
            type_match = TYPE_RE.match(stripped)
            if type_match:
                short = type_match.group(3)
                full = f"{namespace}.{short}" if namespace else short
                if full not in type_rows:
                    idx_match = IDX_RE.search(line)
                    type_rows[full] = (full, short, namespace,
                                       int(idx_match.group(1)) if idx_match else None, line_no)
                current = full
                continue
            if current is None or line[:1] not in (" ", "\t"):
                continue
            if stripped.startswith(("//", "[")):
                continue
            field_match = FIELD_RE.match(line)
            if not field_match:
                continue
            declared = line.split(";")[0].strip()
            tokens = re.sub(r"^\[[^\]]*\]\s*", "", declared).split()
            position = 0
            while position < len(tokens) and tokens[position] in FIELD_MODIFIERS:
                position += 1
            modifiers = set(tokens[:position])
            field_type = " ".join(tokens[position:-1]) or "?"
            # Il2CppDumper prints a storage offset for static and const fields too,
            # but that number is not an instance offset. Recording it would let a
            # later reader treat a static cache as a member, so it is dropped and
            # the row is marked instead.
            is_static = int(bool(modifiers & {"static", "const"}))
            offset = None if is_static else int(field_match.group(2), 16)
            field_rows.append((current, field_match.group(1), field_type, offset, is_static))
    conn.executemany("INSERT OR REPLACE INTO types VALUES (?,?,?,?,?)", list(type_rows.values()))
    conn.executemany("INSERT INTO fields VALUES (?,?,?,?,?)", field_rows)
    return {"types": len(type_rows), "fields": len(field_rows),
            "fields_with_offset": sum(1 for row in field_rows if row[3] is not None),
            "static_fields_without_offset": sum(1 for row in field_rows if row[4])}


def load_audit(conn: sqlite3.Connection, root: Path, native: Path) -> dict:
    seen = {}
    for base in (root / "RE-evidence" / "20260912-combat", native):
        audit = base / "audit.json"
        if not audit.is_file():
            continue
        payload = json.loads(audit.read_text(encoding="utf-8"))
        for entry in payload.get("methods", []):
            rva = normalize_rva(entry.get("rva"))
            if rva is None or rva in seen:
                continue
            seen[rva] = (rva, entry.get("name"), normalize_rva(entry.get("offset")),
                         normalize_rva(entry.get("end")), entry.get("sha256"),
                         relative(audit, root))
    conn.executemany("INSERT OR REPLACE INTO audit_identity VALUES (?,?,?,?,?,?)", list(seen.values()))
    return {"audited_methods": len(seen)}


def load_fixture_stub_names(conn: sqlite3.Connection, rows) -> dict:
    """Adopt names the existing fixtures already give to native helpers.

    The combat fixtures stub native callees by address and name them in the call,
    for example `machine.stub(0x12d21a0, 'il2cpp_class_init')`. Those names are
    statements already written in this repository, so they are recorded as aliases
    with the file and line as their basis - not invented here.
    """
    pattern = re.compile(r"stub\(\s*0x([0-9a-fA-F]{4,9})\s*,\s*['\"]([^'\"]{1,60})['\"]")
    aliases = {}
    for path, kind, _digest, raw in rows:
        if kind != "tool":
            continue
        for number, line in enumerate(raw.decode("utf-8", errors="replace").splitlines(), 1):
            match = pattern.search(line)
            if not match:
                continue
            key = (int(match.group(1), 16), match.group(2))
            aliases.setdefault(key, (key[0], key[1], "fixture-stub", "stated",
                                     line.strip()[:200], f"{path.name}:{number}"))
    conn.executemany("INSERT INTO method_alias VALUES (?,?,?,?,?,?)", list(aliases.values()))
    return {"fixture_stub_names": len(aliases),
            "fixture_stub_basis": "the `machine.stub(address, name)` call in the cited fixture"}


def load_prior_naming(conn: sqlite3.Connection) -> dict:
    """Adopt the earlier world-builder naming pass as aliases, at its own confidence.

    Rows the earlier pass marked UNCERTAIN, or that name no address, are dropped
    rather than promoted; nothing here overwrites an Il2CppDumper identity.
    """
    path = Path(r"C:\APK-RE\kingdom-adventurers\Reverse engineering\exports"
                r"\active\il2cpp-ghidra-naming-audit\managed-method-map-worldbuilder.tsv")
    if not path.is_file():
        return {"prior_naming_rows": 0, "prior_naming_source": "not found"}
    rows = {}
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        header = [part.strip('"') for part in handle.readline().rstrip("\n").split("\t")]
        for line in handle:
            parts = [part.strip('"') for part in line.rstrip("\n").split("\t")]
            if len(parts) != len(header):
                continue
            record = dict(zip(header, parts))
            rva = normalize_rva(record.get("rva"))
            alias = (record.get("managed_type", "") + "." + record.get("managed_method", "")).strip(".")
            if rva is None or not alias or alias.endswith("."):
                continue
            if record.get("confidence", "").upper() == "UNCERTAIN":
                continue
            rows.setdefault((rva, alias),
                            (rva, alias, "world-builder-naming-pass",
                             record.get("confidence", "MEDIUM"), record.get("notes", ""), str(path)))
    conn.executemany("INSERT INTO method_alias VALUES (?,?,?,?,?,?)", list(rows.values()))
    return {"prior_naming_rows": len(rows), "prior_naming_source": str(path)}


def insert_canonical_aliases(conn: sqlite3.Connection) -> dict:
    rows = []
    for rva, name, owner, method in conn.execute(
            "SELECT rva, name, owner, method FROM methods"):
        rows.append((rva, name, "il2cpp-dumper", "confirmed", "script.json Name", "script.json"))
        if owner and method:
            rows.append((rva, f"{short_type(owner)}.{method}", "short-managed", "confirmed",
                         "derived from the script.json Name", "derived"))
    conn.executemany("INSERT INTO method_alias VALUES (?,?,?,?,?,?)", rows)
    return {"canonical_aliases": len(rows)}


def current_predicate():
    clause = " OR ".join("x.rel LIKE ?" for _ in CURRENT_SOURCES)
    params = [f"%{source}%" for source in CURRENT_SOURCES]
    return clause, params


def unsliced_cited(conn: sqlite3.Connection):
    """Cited methods that have no instruction listing yet, with a priority tier.

    "Cited" means some indexed file actually names this address. "Current" means
    the citation comes from a document that describes the work as it stands today
    or from a recovery tool, rather than from a quarantine packet or an archived
    before-image. The tier is only a reading order; it asserts nothing about the
    function itself.
    """
    clause, params = current_predicate()
    rows = conn.execute(
        f"SELECT m.rva AS rva, m.name AS name, COUNT(*) AS citations, "
        f"       SUM(CASE WHEN {clause} THEN 1 ELSE 0 END) AS current_citations "
        f"FROM methods m JOIN mentions x ON x.rva = m.rva "
        f"WHERE NOT EXISTS (SELECT 1 FROM artifacts a WHERE a.rva = m.rva) "
        f"GROUP BY m.rva ORDER BY current_citations DESC, citations DESC",
        params).fetchall()
    out = []
    for row in rows:
        record = dict(row)
        record["framework"] = bool(FRAMEWORK_RE.match(row["name"] or ""))
        current = row["current_citations"]
        if record["framework"]:
            record["tier"] = "-"
        elif current >= 8:
            record["tier"] = "A"
        elif current >= 4:
            record["tier"] = "B"
        elif current >= 2:
            record["tier"] = "C"
        elif current >= 1:
            record["tier"] = "D"
        else:
            record["tier"] = "record-only"
        out.append(record)
    return out


def build_alias_lookup(conn: sqlite3.Connection) -> dict:
    """Keys used when scanning prose for cited method names.

    Only keys that contain a separator are used, and a short-type key is kept only
    when it addresses exactly one method, so a common short name cannot silently
    attach a citation to the wrong function.
    """
    lookup = {}

    def add(key, rva):
        if not key or ("." not in key and "$$" not in key):
            return
        bucket = lookup.setdefault(key, [])
        if rva not in bucket and len(bucket) < 8:
            bucket.append(rva)

    for rva, name, owner, method in conn.execute("SELECT rva, name, owner, method FROM methods"):
        if not owner or not method:
            continue
        add(name, rva)
        add(f"{owner}.{method}", rva)
        add(f"{owner}$${method}", rva)
        add(f"{short_type(owner)}.{method}", rva)
        add(f"{short_type(owner)}$${method}", rva)
    for rva, alias in conn.execute(
            "SELECT rva, alias FROM method_alias WHERE kind <> 'short-managed'"):
        add(alias, rva)
    return {key: value for key, value in lookup.items()
            if "$$" in key or len(key.rsplit(".", 1)[0]) > 3}


# --------------------------------------------------------------------------- #
# build: artifacts and citations
# --------------------------------------------------------------------------- #


def collect_files(root: Path):
    """Every authoritative text source, by kind, with the excluded roots made explicit.

    The quarantined `reverse engineering with deepseek/` working copy is *not* indexed:
    it is superseded by the live tree, and indexing it as evidence produced duplicated
    handoff sections and citations to a non-authoritative source.
    """
    groups = []

    def excluded(rel: str) -> bool:
        return (any(rel.startswith(bad) for bad in EXCLUDED_ROOTS)
                or Path(rel).name in SELF_FILES)

    def is_dump(rel: str) -> bool:
        lowered = rel.lower()
        if any(marker in lowered for marker in DUMP_DIR_MARKERS):
            return True
        if any(pattern in lowered for pattern in DUMP_NAME_PATTERNS):
            return True
        return any(lowered.endswith(suffix) for suffix in DUMP_SUFFIXES)

    def add(base: Path, kind: str):
        if not base.exists():
            return
        if base.is_file():
            rel = base.relative_to(root).as_posix()
            if base.suffix.lower() in TEXT_EXT and not excluded(rel):
                groups.append((base, kind))
            return
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in TEXT_EXT:
                continue
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            rel = path.relative_to(root).as_posix()
            if rel.startswith("KA-Website/artifacts/"):
                if SHIPPED_NATIVE_RE.match(rel):
                    groups.append((path, "evidence"))
                continue
            if excluded(rel):
                continue
            if path.name in HISTORICAL_FILES:
                # Superseded ledgers stay searchable as history, never as guidance.
                groups.append((path, "record"))
                continue
            if is_dump(rel):
                # Generated dump: hashed and listable, but never a citation source.
                groups.append((path, "dump"))
                continue
            try:
                if path.stat().st_size > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            groups.append((path, kind))

    for relative_root, kind in INCLUDED_ROOTS:
        add(root / relative_root, kind)
    return groups


def digest_files(files):
    """Read every candidate once and keep the first copy of each distinct content.

    The workspace holds a working copy and the original tree, so most documents
    and listings exist twice. Content hashing keeps one of each, which removes
    duplicate query results and roughly halves the build's read work.
    """
    rows = []
    seen = {}
    for path, kind in sorted(files, key=lambda item: str(item[0])):
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        digest = hashlib.sha256(raw).hexdigest()
        rows.append((path, kind, digest, raw))
        seen.setdefault(digest, str(path))
    return rows, seen


def load_artifacts_and_edges(conn: sqlite3.Connection, root: Path, rows) -> dict:
    """Index existing instruction listings, and cross-check their edges against the sweep.

    The combat listings were generated with every `bl`/`b` target already annotated
    with the Il2CppDumper name. Their call lines are *not* inserted as edges: the sweep
    already decodes the whole executable segment, so a listing edge is a duplicate of a
    sweep edge with a different caller label. They are returned instead, and the build
    asserts that every listing edge exists in the sweep - an independent check on the
    graph rather than a second graph.
    """
    artifact_rows = []
    listing_edges = []
    seen = set()
    for path, _kind, digest, raw in rows:
        if digest in seen:
            continue
        seen.add(digest)
        suffix = path.suffix.lower()
        if suffix not in (".asm", ".c"):
            continue
        rva = normalize_rva(path.stem)
        if rva is None:
            continue
        rel = relative(path, root)
        size = len(raw)
        if suffix == ".c":
            artifact_rows.append((rva, "ghidra-decompile", rel, size, None, None, None))
            continue
        text = raw.decode("utf-8", errors="replace")
        lines = [line for line in text.splitlines() if line.strip()]
        artifact_rows.append((rva, "asm-slice", rel, size, None, len(lines), None))
        for line in lines:
            match = ASM_CALL_RE.match(line)
            if not match:
                continue
            listing_edges.append((int(match.group(1), 16), int(match.group(3), 16), rel))
    conn.executemany("INSERT INTO artifacts VALUES (?,?,?,?,?,?,?)", artifact_rows)
    return {"asm_slices": sum(1 for row in artifact_rows if row[1] == "asm-slice"),
            "ghidra_exports": sum(1 for row in artifact_rows if row[1] == "ghidra-decompile"),
            "listing_call_edges": len(listing_edges),
            "_listing_edges": listing_edges}


def check_listing_edges(conn: sqlite3.Connection, listing_edges) -> dict:
    """Every call line in an existing listing must already be a decoded sweep site.

    This is the independent verification the two former systems can still provide for
    free: the listings were produced by a different tool (a slicer) at a different time,
    so agreement is evidence that the sweep is not dropping call sites.

    The comparison is against the *raw* sweep, because the final graph deliberately drops
    intra-function branches; how many listing lines that costs is reported separately
    rather than hidden.
    """
    raw = {(row[0], row[1]) for row in conn.execute("SELECT site, target FROM _sweep_sites")}
    kept = {(row[0], row[1]) for row in conn.execute("SELECT site, callee_rva FROM calls")}
    missing = [row for row in listing_edges if (row[0], row[1]) not in raw]
    pruned = [row for row in listing_edges if (row[0], row[1]) in raw and (row[0], row[1]) not in kept]
    return {"listing_edges_checked": len(listing_edges),
            "listing_edges_missing_from_sweep": len(missing),
            "listing_edges_pruned_as_control_flow": len(pruned),
            "_listing_edges_missing_sample": [f"{hex(a)}->{hex(b)} {rel}" for a, b, rel in missing[:5]]}


MANIFEST_READ_FAILURES: list = []


def read_manifest(path: Path):
    """Read a slice manifest, tolerating a file that another writer is mid-way through.

    The evidence tree is worked on by more than one agent at a time; a manifest that is
    being rewritten can be momentarily unreadable. That is reported in the build summary
    instead of aborting a 30-second build, and `verify` will show the file as changed.
    """
    for attempt in (1, 2):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            if attempt == 1:
                time.sleep(0.25)
                continue
            MANIFEST_READ_FAILURES.append(str(path))
            return None
        if isinstance(payload, dict) and "slices" in payload:
            return payload
        if attempt == 2:
            MANIFEST_READ_FAILURES.append(str(path))
            return None
    return None


def load_code_windows(conn: sqlite3.Connection, root: Path) -> dict:
    """Record non-method native code that current evidence deliberately sliced.

    Two sources, both structured and both already hash-verified:
      * every `slices.json` manifest under RE-evidence whose entry is not a managed method
        start (the B3 dispatch arms and the `Data.GetData` jump-table windows);
      * any indexed artifact whose address is not a managed method start.
    The window is stored with its own name, hash and containing method. Nothing is added
    to `methods`: a window is evidence, not an identity.
    """
    method_set = {row[0] for row in conn.execute("SELECT rva FROM methods")}
    rows = {}
    manifests = 0
    for path in sorted((root / "RE-evidence").rglob("slices.json")):
        payload = read_manifest(path)
        if payload is None:
            continue
        if not isinstance(payload, dict) or "slices" not in payload:
            continue
        manifests += 1
        rel = relative(path, root)
        folder = path.parent.name
        for entry in payload["slices"]:
            rva = normalize_rva(str(entry.get("rva")))
            if rva is None or rva in method_set:
                continue
            kind = "jump-table-arm" if ("arm" in str(entry.get("name", "")).lower()
                                        or "arm" in folder) else "code-window"
            containing = conn.execute(
                "SELECT rva FROM methods WHERE rva<=? ORDER BY rva DESC LIMIT 1", (rva,)).fetchone()
            instrs = entry.get("instrs")
            end_rva = rva + 4 * instrs if isinstance(instrs, int) and instrs else None
            rows[rva] = (rva, entry.get("name"), kind, rel, entry.get("instrs"),
                         entry.get("sha256"), end_rva, containing[0] if containing else None,
                         f"non-method window from {folder}; {entry.get('terminated')} "
                         "terminated")
    for rva, kind, rel in conn.execute("SELECT rva, kind, rel FROM artifacts"):
        if rva in method_set or rva in rows:
            continue
        containing = conn.execute(
            "SELECT rva, name FROM methods WHERE rva<=? ORDER BY rva DESC LIMIT 1", (rva,)).fetchone()
        rows[rva] = (rva, None, "artifact-window", rel, None, None, None,
                     containing[0] if containing else None,
                     f"artifact listing without a managed method start"
                     + (f"; inside {containing[1]}" if containing else ""))
    conn.executemany("INSERT OR REPLACE INTO code_windows VALUES (?,?,?,?,?,?,?,?,?)",
                     list(rows.values()))
    return {"slice_manifests": manifests, "code_windows": len(rows),
            "code_windows_arms": sum(1 for row in rows.values() if row[2] == "jump-table-arm")}


def scan_citations(conn: sqlite3.Connection, root: Path, rows, alias_lookup) -> dict:
    file_rows = []
    section_rows = []
    mention_rows = []
    fts_rows = []
    seen = set()
    unique = 0
    for path, kind, digest, raw in rows:
        rel = relative(path, root)
        if digest in seen:
            file_rows.append((str(path), rel, kind, len(raw), digest, "identical copy of an "
                              "already-indexed file (working copy vs original tree)"))
            continue
        seen.add(digest)
        file_rows.append((str(path), rel, kind, len(raw), digest, None))
        unique += 1
        if kind == "dump":
            # Generated analysis output: recorded, never scanned for citations.
            continue
        lines = raw.decode("utf-8", errors="replace").splitlines()

        headings = []
        if path.suffix.lower() == ".md":
            for index, line in enumerate(lines, 1):
                if line.startswith("#"):
                    headings.append((index, len(line) - len(line.lstrip("#")), line.strip("# ").strip()))
            for position, (start, level, heading) in enumerate(headings):
                end = headings[position + 1][0] - 1 if position + 1 < len(headings) else len(lines)
                body = "\n".join(lines[start - 1:end])
                section_rows.append((str(path), rel, kind, level, heading, start, end,
                                     len(set(RVA_RE.findall(body)))))

        heading = ""
        cursor = 0
        for index, line in enumerate(lines, 1):
            while cursor < len(headings) and headings[cursor][0] <= index:
                heading = headings[cursor][2]
                cursor += 1
            if len(line) > 4000:
                line = line[:4000]
            stripped = line.strip()
            if "0x" in line:
                for match in RVA_RE.finditer(line):
                    mention_rows.append((int(match.group(1), 16), "address", kind,
                                         rel, index, heading, stripped[:200]))
            if "." in line or "$$" in line:
                tokens = {match.group(0).strip("`") for match in DOTTED_RE.finditer(line)}
                tokens |= {match.group(0).strip("`") for match in DOLLAR_RE.finditer(line)}
                for token in tokens:
                    for rva in alias_lookup.get(token, ())[:4]:
                        mention_rows.append((rva, "name", kind, rel, index,
                                             heading, stripped[:200]))
            if kind in ("doc", "tool", "record") and stripped:
                fts_rows.append((rel, kind, index, stripped[:800]))

    conn.executemany("INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?)", file_rows)
    conn.executemany("INSERT INTO doc_sections VALUES (?,?,?,?,?,?,?,?)", section_rows)
    conn.executemany("INSERT INTO mentions VALUES (?,?,?,?,?,?,?)", mention_rows)
    conn.executemany("INSERT INTO search VALUES (?,?,?,?)", fts_rows)
    return {"files": unique, "files_total": len(file_rows), "duplicate_files": len(file_rows) - unique,
            "doc_sections": len(section_rows), "mentions": len(mention_rows),
            "fts_lines": len(fts_rows)}


# --------------------------------------------------------------------------- #
# build: optional ELF sweep
# --------------------------------------------------------------------------- #


def load_regions(conn: sqlite3.Connection, native: Path) -> dict:
    """Record the ELF load segments so any cited number can be classified.

    The research documents mix code addresses, GOT/pointer slots and field offsets
    in the same notation. Knowing which loadable region an address falls in keeps
    a data slot from being read as a function.
    """
    binary = native / "inputs" / "libil2cpp.so"
    if not binary.is_file():
        return {"regions": 0, "region_status": "binary missing"}
    try:
        from elftools.elf.elffile import ELFFile
    except ImportError as exc:
        return {"regions": 0, "region_status": f"skipped, {exc}"}
    rows = []
    with binary.open("rb") as handle:
        elf = ELFFile(handle)
        for index, segment in enumerate(elf.iter_segments()):
            if segment["p_type"] != "PT_LOAD":
                continue
            flags = segment["p_flags"]
            rows.append((f"PT_LOAD[{index}]", segment["p_offset"], segment["p_vaddr"],
                         segment["p_filesz"], segment["p_memsz"],
                         int(bool(flags & 1)), int(bool(flags & 2))))
    conn.executemany("INSERT INTO regions VALUES (?,?,?,?,?,?,?)", rows)
    return {"regions": len(rows), "region_status": "ok"}


def region_of(conn: sqlite3.Connection, rva: int) -> str:
    row = conn.execute(
        "SELECT executable, writable FROM regions WHERE vaddr <= ? AND ? < vaddr + memsz "
        "ORDER BY memsz LIMIT 1", (rva, rva)).fetchone()
    if row is None:
        return "outside the mapped image"
    if row["executable"]:
        return "code"
    return "data" + (" (writable)" if row["writable"] else "")


def build_code_map(conn: sqlite3.Connection, native: Path) -> dict:
    """One linear sweep of the executable segments: method extents and call sites.

    The zero-based RVA convention is the one script.json, audit.json and every
    existing slice already use. IL2CPP pads between methods, so an extent is
    reported as the last decoded instruction before the next method start - an
    upper bound, labelled as such wherever it is shown.
    """
    binary = native / "inputs" / "libil2cpp.so"
    if not binary.is_file():
        return {"code_map": 0, "binary_edges": 0, "xref_status": "binary missing"}
    try:
        from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN
        from elftools.elf.elffile import ELFFile
    except ImportError as exc:
        return {"code_map": 0, "binary_edges": 0,
                "xref_status": f"skipped, {exc} (run under the repository .venv python)"}

    methods = [row[0] for row in conn.execute("SELECT rva FROM methods ORDER BY rva")]
    method_set = set(methods)
    blob = binary.read_bytes()
    engine = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)
    # IL2CPP keeps literal pools and padding inside .text. Without skipdata the linear
    # decode stops at the first undecodable word, which silently truncates the sweep and
    # loses every call site after it; skipped words decode as `.byte` and are ignored.
    engine.skipdata = True
    starts = []
    edges = []
    with binary.open("rb") as handle:
        elf = ELFFile(handle)
        segments = [segment for segment in elf.iter_segments()
                    if segment["p_type"] == "PT_LOAD" and segment["p_flags"] & 1
                    and segment["p_filesz"]]
        for segment in segments:
            data = blob[segment["p_offset"]:segment["p_offset"] + segment["p_filesz"]]
            for ins in engine.disasm(data, segment["p_vaddr"]):
                starts.append(ins.address)
                if ins.mnemonic in ("bl", "b") and ins.op_str.startswith("#"):
                    index = bisect.bisect_right(methods, ins.address) - 1
                    caller = methods[index] if index >= 0 else None
                    target = int(ins.op_str[1:], 16)
                    edges.append((caller, target, ins.address,
                                  "call" if ins.mnemonic == "bl" else "branch", "linear-sweep",
                                  "method" if caller is not None else "unclaimed",
                                  "method" if target in method_set else "non-method"))
    starts.sort()
    rows = []
    cursor = 0
    total = len(starts)
    for method in methods:
        while cursor < total and starts[cursor] < method:
            cursor += 1
        if cursor >= total:
            break
        first = last = starts[cursor]
        index = cursor
        while index < total:
            address = starts[index]
            if address != last and address != last + 4:
                break
            if address != first and address in method_set:
                break
            last = address
            index += 1
        rows.append((method, first, last, (last - first) // 4 + 1, last + 4, "linear-sweep"))
    conn.executemany("INSERT OR REPLACE INTO code_map VALUES (?,?,?,?,?,?)", rows)
    conn.executemany("INSERT INTO calls VALUES (?,?,?,?,?,?,?)", edges)
    # Kept for the listing cross-check only, and dropped again once it has run.
    conn.execute("DROP TABLE IF EXISTS _sweep_sites")
    conn.execute("CREATE TEMP TABLE _sweep_sites(site INTEGER, target INTEGER)")
    conn.executemany("INSERT INTO _sweep_sites VALUES (?,?)",
                     [(edge[2], edge[1]) for edge in edges])

    # Everything the sweep decoded that no managed method claims. Walked in address
    # order against the method extents, so this is a set difference, not a judgement.
    helper_rows = []
    row_index = 0
    run_start = None
    run_count = 0
    for address in starts:
        while row_index < len(rows) and rows[row_index][2] < address:
            row_index += 1
        inside = row_index < len(rows) and rows[row_index][1] <= address <= rows[row_index][2]
        if not inside:
            if run_start is not None and address == run_start + 4 * run_count:
                run_count += 1
            else:
                if run_start is not None:
                    helper_rows.append((run_start, run_start + 4 * (run_count - 1), run_count))
                run_start, run_count = address, 1
        elif run_start is not None:
            helper_rows.append((run_start, run_start + 4 * (run_count - 1), run_count))
            run_start = None
    if run_start is not None:
        helper_rows.append((run_start, run_start + 4 * (run_count - 1), run_count))
    helper_rows = [row for row in helper_rows if row[2] >= 4]
    conn.executemany("INSERT OR REPLACE INTO native_regions VALUES (?,?,?)", helper_rows)
    by_class = {}
    for edge in edges:
        key = f"{edge[5]}-to-{edge[6]}"
        by_class[key] = by_class.get(key, 0) + 1
    return {"code_map": len(rows), "binary_edges_pre_prune": len(edges), "xref_status": "ok",
            "decoded_instructions": total, "unclaimed_native_regions": len(helper_rows),
            "edge_classes_pre_prune": by_class}


# --------------------------------------------------------------------------- #
# build driver
# --------------------------------------------------------------------------- #


def postprocess(conn: sqlite3.Connection) -> dict:
    """Remove control flow that is not a call, and label the runtime helpers already named.

    A linear sweep cannot tell a loop back-edge from a call, so `b` targets that land
    inside the caller's own extent are dropped: they are control flow, not a
    relationship between two functions. A `b` whose containing method is unknown (the
    unclaimed prologue region) cannot be judged either way and is dropped as well,
    leaving only real tail calls in the graph. `bl` is kept in full, including calls
    into addresses that have no managed identity - those are exactly the runtime
    helpers and thunks the combat work needs to see.
    """
    intra = conn.execute(
        "DELETE FROM calls WHERE kind='branch' AND EXISTS "
        "(SELECT 1 FROM code_map cm WHERE cm.rva=calls.caller_rva "
        " AND calls.callee_rva BETWEEN cm.first_instr AND cm.last_instr)").rowcount
    unclaimed = conn.execute(
        "DELETE FROM calls WHERE kind='branch' AND caller_rva IS NULL").rowcount
    rows = [(rva, name, "il2cpp-runtime", "supported", basis, "prepare_ghidra_combat.py")
            for rva, (name, basis) in RUNTIME_HELPERS.items()]
    conn.executemany("INSERT INTO method_alias VALUES (?,?,?,?,?,?)", rows)
    return {"intra_function_branches_removed": intra,
            "unclaimed_region_branches_removed": unclaimed,
            "runtime_helpers_labelled": len(rows)}


def load_helper_findings(conn: sqlite3.Connection, path: Path) -> dict:
    """Adopt the helper classification pass, if it has been run.

    These rows classify addressing holes that no dump names. They deliberately store
    a verdict rather than a name: the exception to naming is not taken here.
    """
    if not path.is_file():
        return {"helper_findings": 0}
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = []
    for entry in payload.get("findings", []):
        rva = normalize_rva(entry.get("rva"))
        if rva is None:
            continue
        rows.append((rva, entry.get("verdict"), entry.get("confidence"),
                     entry.get("implementation"), " | ".join(entry.get("observed", [])),
                     " | ".join(entry.get("evidence", [])), entry.get("anchor"),
                     entry.get("note")))
    conn.executemany("INSERT OR REPLACE INTO helper_findings VALUES (?,?,?,?,?,?,?,?)", rows)
    return {"helper_findings": len(rows),
            "helper_findings_source": str(path),
            "helper_findings_binary": payload.get("binarySha256")}


def load_derived_artifacts(conn: sqlite3.Connection, directory: Path, label: str,
                           key: str = "derived") -> dict:
    """Index generated listings so a query can hand back the code, not a to-do.

    These files are produced by `ka_slice.py` from the same frozen binary. They are
    recorded as artifacts and as files, but deliberately *not* as citations: every
    operand address in a listing would otherwise register as a mention and inflate
    the evidence counts this index uses for ranking.
    """
    if not directory.is_dir():
        return {f"{key}_artifacts": 0, f"{key}_artifacts_dir": str(directory)}
    artifact_rows = []
    file_rows = []
    for path in sorted(directory.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in (".asm", ".c"):
            continue
        rva = normalize_rva(path.stem)
        if rva is None:
            continue
        raw = path.read_bytes()
        rel = f"{label}/{path.name}"
        if path.suffix.lower() == ".c":
            artifact_rows.append((rva, "ghidra-decompile", rel, len(raw), sha256_of(path),
                                  None, "derived export, outside the evidence tree"))
        else:
            instrs = sum(1 for line in raw.splitlines() if line.strip())
            artifact_rows.append((rva, "asm-slice", rel, len(raw), sha256_of(path), instrs,
                                  "derived by ka_slice.py from the frozen binary; the code "
                                  "is original, the listing is generated"))
        file_rows.append((str(path), rel, "derived", len(raw), sha256_of(path), None))
    conn.executemany("INSERT INTO artifacts VALUES (?,?,?,?,?,?,?)", artifact_rows)
    conn.executemany("INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?)", file_rows)
    return {f"{key}_artifacts": len(artifact_rows), f"{key}_artifacts_dir": str(directory)}


CONTENT_TABLES = ("methods", "method_alias", "types", "fields", "audit_identity", "artifacts",
                  "code_windows", "code_map", "calls", "native_regions", "helper_findings",
                  "doc_sections", "mentions")


def content_digest(conn: sqlite3.Connection) -> str:
    """A deterministic digest of the index's *content*, independent of the SQLite file.

    The combat overlay pins this value, so "the overlay was built against a different
    canonical native state" is a cheap, exact comparison rather than a timestamp guess.
    """
    digest = hashlib.sha256()
    for table in CONTENT_TABLES:
        rows = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        digest.update(f"{table}:{rows}\n".encode())
        for row in conn.execute(f"SELECT * FROM {table}"):
            digest.update(("|".join("" if value is None else str(value) for value in row)
                           + "\n").encode("utf-8", errors="replace"))
    return digest.hexdigest()


def source_set_digest(conn: sqlite3.Connection) -> str:
    """A digest of the indexed source set: relative path + kind + content hash."""
    digest = hashlib.sha256()
    for rel, kind, sha in conn.execute(
            "SELECT rel, kind, sha256 FROM files WHERE duplicate_of IS NULL ORDER BY rel"):
        digest.update(f"{rel}|{kind}|{sha}\n".encode())
    return digest.hexdigest()


def write_manifest(out: Path, root: Path, conn: sqlite3.Connection, summary: dict) -> dict:
    counts = {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
              for table in CONTENT_TABLES + ("files", "mentions", "regions", "meta")}
    edge_classes = {f"{row[0]}-to-{row[1]}": row[2] for row in conn.execute(
        "SELECT source_kind, target_kind, COUNT(*) FROM calls GROUP BY 1, 2 ORDER BY 1, 2")}
    manifest = dict(
        schema="ka-native-index-manifest-1", indexVersion=INDEX_VERSION,
        root=str(root), binarySha256=summary.get("meta_binary_sha256"),
        contentDigest=content_digest(conn), sourceSetDigest=source_set_digest(conn),
        counts=counts, edgeClasses=edge_classes,
        includedRoots=[{"root": r, "kind": k} for r, k in INCLUDED_ROOTS],
        excludedRoots=list(EXCLUDED_ROOTS),
        historicalFiles=list(HISTORICAL_FILES),
        generatedUtc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    path = out / "index-manifest.json"
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return manifest


def build(root: Path, out: Path, with_xrefs: bool, slices_dir: Path | None = None,
          helper_analysis: Path | None = None) -> dict:
    native = native_dir(root)
    if not (native / "dump" / "script.json").is_file():
        raise SystemExit(f"script.json not found under {native}")
    out.mkdir(parents=True, exist_ok=True)
    db_path = out / DB_NAME
    if db_path.exists():
        db_path.unlink()
    conn = connect(db_path)
    conn.executescript(SCHEMA)

    summary = {}
    summary.update(load_methods(conn, native))
    summary.update(load_types_and_fields(conn, native))
    summary.update(load_audit(conn, root, native))
    summary.update(load_regions(conn, native))
    conn.commit()

    if with_xrefs:
        summary.update(build_code_map(conn, native))
    else:
        summary.update({"code_map": 0, "binary_edges": 0, "xref_status": "disabled"})

    files = collect_files(root)
    rows, _ = digest_files(files)
    summary["candidate_files"] = len(rows)
    summary["distinct_file_contents"] = len({row[2] for row in rows})
    listing = load_artifacts_and_edges(conn, root, rows)
    listing_edges = listing.pop("_listing_edges", [])
    summary.update(listing)
    summary.update(load_prior_naming(conn))
    summary.update(load_fixture_stub_names(conn, rows))
    summary.update(insert_canonical_aliases(conn))
    summary.update(load_derived_artifacts(conn, slices_dir or DEFAULT_SLICES,
                                          "RE-evidence/20260920-native-index/slices",
                                          "slices"))
    summary.update(load_derived_artifacts(conn, DEFAULT_DECOMPILED,
                                          "RE-evidence/20260920-native-index/decompiled",
                                          "decompiled"))
    summary.update(load_helper_findings(conn, helper_analysis or DEFAULT_HELPER_ANALYSIS))
    summary.update(load_code_windows(conn, root))
    conn.commit()

    alias_lookup = build_alias_lookup(conn)
    summary["alias_lookup_keys"] = len(alias_lookup)
    summary.update(postprocess(conn))
    if with_xrefs:
        check = check_listing_edges(conn, listing_edges)
        summary["listing_edges_missing_from_sweep"] = check["listing_edges_missing_from_sweep"]
        summary["listing_edges_pruned_as_control_flow"] = check["listing_edges_pruned_as_control_flow"]
        summary["listing_edges_checked"] = check["listing_edges_checked"]
        summary["_listing_edges_missing_sample"] = check["_listing_edges_missing_sample"]
        conn.execute("DROP TABLE IF EXISTS _sweep_sites")
        summary["binary_edges"] = conn.execute("SELECT COUNT(*) FROM calls").fetchone()[0]
        summary["edge_classes"] = {f"{row[0]}-to-{row[1]}": row[2] for row in conn.execute(
            "SELECT source_kind, target_kind, COUNT(*) FROM calls GROUP BY 1, 2 ORDER BY 1, 2")}
    summary.update(scan_citations(conn, root, rows, alias_lookup))

    binary = native / "inputs" / "libil2cpp.so"
    conn.execute("INSERT OR REPLACE INTO meta VALUES (?,?)",
                 ("binary_sha256", sha256_of(binary) if binary.is_file() else "missing"))
    conn.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", ("root", str(root)))
    conn.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", ("schema", "1"))
    conn.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", ("index_version", INDEX_VERSION))
    conn.commit()
    summary["meta_binary_sha256"] = conn.execute(
        "SELECT value FROM meta WHERE key='binary_sha256'").fetchone()[0]
    manifest = write_manifest(out, root, conn, summary)
    for key, value in (("content_digest", manifest["contentDigest"]),
                       ("source_set_digest", manifest["sourceSetDigest"]),
                       ("generated_utc", manifest["generatedUtc"])):
        conn.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, value))
    conn.commit()
    conn.execute("VACUUM")
    conn.commit()
    conn.close()

    summary["database"] = str(db_path)
    summary["database_mb"] = round(db_path.stat().st_size / 1_048_576, 1)
    summary["content_digest"] = manifest["contentDigest"]
    summary["source_set_digest"] = manifest["sourceSetDigest"]
    summary["manifest"] = str(out / "index-manifest.json")
    if MANIFEST_READ_FAILURES:
        summary["unreadable_manifests"] = list(MANIFEST_READ_FAILURES)
    (out / "build-summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return summary


# --------------------------------------------------------------------------- #
# query
# --------------------------------------------------------------------------- #


def open_db(out: Path) -> sqlite3.Connection:
    db_path = out / DB_NAME
    if not db_path.is_file():
        raise SystemExit(f"{db_path} does not exist - run `ka_index.py build` first")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def fmt(value) -> str:
    return "unknown" if value is None else f"0x{int(value):x}"


def name_for(conn: sqlite3.Connection, rva: int) -> str:
    """Best available label: the Il2CppDumper name, else a sourced runtime alias."""
    row = conn.execute("SELECT name FROM methods WHERE rva=?", (rva,)).fetchone()
    if row:
        return row["name"]
    alias = conn.execute(
        "SELECT alias, kind, confidence, source FROM method_alias WHERE rva=? "
        "AND kind IN ('il2cpp-runtime','fixture-stub') ORDER BY kind LIMIT 1",
        (rva,)).fetchone()
    if alias:
        return f"{alias['alias']} (name given by {alias['source']})"
    region = conn.execute(
        "SELECT instrs FROM native_regions WHERE start_rva=?", (rva,)).fetchone()
    if region:
        return f"(unclaimed native code, {region['instrs']} instructions)"
    return ""


def native_region_containing(conn: sqlite3.Connection, rva: int):
    return conn.execute(
        "SELECT * FROM native_regions WHERE start_rva <= ? AND ? <= last_instr "
        "ORDER BY instrs LIMIT 1", (rva, rva)).fetchone()


def describe(conn: sqlite3.Connection, rva: int, limit: int = 12, verbose: bool = False) -> str:
    lines = []
    row = conn.execute("SELECT * FROM methods WHERE rva=?", (rva,)).fetchone()
    if row is None:
        window = conn.execute("SELECT * FROM code_windows WHERE rva=?", (rva,)).fetchone()
        region = native_region_containing(conn, rva)
        if window:
            owner = (conn.execute("SELECT name FROM methods WHERE rva=?",
                                  (window["inside_method"],)).fetchone() if window["inside_method"]
                     else None)
            lines.append(f"{fmt(rva)}  NATIVE CODE WINDOW "
                         f"{'(' + window['name'] + ') ' if window['name'] else ''}"
                         f"[{window['kind']}]")
            lines.append("    this address is NOT an IL2CPP method start; it is a deliberately "
                         "sliced window of native code")
            if window["manifest"]:
                lines.append(f"    window manifest: {window['manifest']}")
            if window["instrs"]:
                lines.append(f"    window size: {window['instrs']} instructions")
            if window["sha256"]:
                lines.append(f"    window sha256: {window['sha256']}")
            if owner:
                lines.append(f"    inside method: {fmt(window['inside_method'])}  {owner['name']}")
            else:
                lines.append("    inside method: (outside every indexed managed method)")
            if window["note"]:
                lines.append(f"    note: {window['note']}")
        elif region:
            lines.append(f"{fmt(rva)}  is inside an unclaimed native run "
                         f"{fmt(region['start_rva'])}..{fmt(region['last_instr'])} "
                         f"({region['instrs']} decoded instructions). No Il2CppDumper method "
                         "claims this address, so it carries no managed name - not even a "
                         "synthetic one. Treat the run bounds as a hint, not a function "
                         "boundary.")
        else:
            lines.append(f"{fmt(rva)}  (not an Il2CppDumper method start; "
                         f"location: {region_of(conn, rva)})")
    else:
        lines.append(f"{fmt(rva)}  {row['name']}")
        if row["signature"]:
            lines.append(f"    signature: {row['signature']}")
    audited = conn.execute("SELECT * FROM audit_identity WHERE rva=?", (rva,)).fetchone()
    if audited and audited["end_rva"]:
        lines.append(f"    audited slice: file offset {fmt(audited['file_offset'])}, "
                     f"end {fmt(audited['end_rva'])}, sha256 {audited['sha256']}")
        lines.append(f"    audited identity source: {audited['source']}")
    code = conn.execute("SELECT * FROM code_map WHERE rva=?", (rva,)).fetchone()
    if code:
        lines.append(f"    decoded extent: {fmt(code['first_instr'])}..{fmt(code['last_instr'])} "
                     f"({code['instrs']} instructions; upper bound from a linear sweep)")

    aliases = conn.execute(
        "SELECT alias, kind, confidence FROM method_alias WHERE rva=? AND kind NOT IN "
        "('il2cpp-dumper','short-managed') LIMIT 8", (rva,)).fetchall()
    if aliases:
        lines.append("    additional names (not original identities):")
        for alias in aliases:
            lines.append(f"      {alias['alias']}   [{alias['kind']}, {alias['confidence']}]")

    finding = conn.execute("SELECT * FROM helper_findings WHERE rva=?", (rva,)).fetchone()
    if finding:
        lines.append(f"    analysis verdict: {finding['verdict']}   [confidence: "
                     f"{finding['confidence']}]")
        lines.append(f"      implementation: {finding['implementation']}")
        lines.append(f"      observed: {finding['observed'][:400]}")
        lines.append(f"      evidence: {finding['evidence'][:300]}")
        if finding["anchor"]:
            lines.append(f"      anchor: {finding['anchor']}")
        if finding["note"]:
            lines.append(f"      note: {finding['note']}")

    fields = conn.execute(
        "SELECT field, field_type, offset FROM fields WHERE type IN "
        "(SELECT owner FROM methods WHERE rva=?) AND is_static=0 ORDER BY offset", (rva,)).fetchall()
    if fields:
        lines.append("    instance fields of the declaring type (static members excluded; "
                     "dump.cs prints a meaningless offset for those):")
        for field in fields:
            lines.append(f"      +{field['offset']:#04x}  {field['field']} : {field['field_type']}")
    statics = conn.execute(
        "SELECT field, field_type FROM fields WHERE type IN "
        "(SELECT owner FROM methods WHERE rva=?) AND is_static=1 ORDER BY field LIMIT 12",
        (rva,)).fetchall()
    if statics:
        names = ", ".join(f"{row['field']} : {row['field_type']}" for row in statics)
        lines.append(f"    static members (no instance offset): {names}")

    artifacts = conn.execute("SELECT kind, rel FROM artifacts WHERE rva=? ORDER BY kind", (rva,)).fetchall()
    if artifacts:
        lines.append("    existing artifacts (no need to re-slice):")
        for artifact in artifacts:
            lines.append(f"      [{artifact['kind']}] {artifact['rel']}")

    callees = conn.execute(
        "SELECT DISTINCT c.callee_rva AS rva FROM calls c WHERE c.caller_rva=? "
        "ORDER BY c.callee_rva LIMIT ?",
        (rva, limit)).fetchall()
    if callees:
        lines.append("    calls (direct bl/b only; virtual and indirect calls unresolved):")
        for callee in callees:
            label = name_for(conn, callee["rva"]) or "(unidentified)"
            lines.append(f"      -> {fmt(callee['rva'])}  {label}")

    callers = conn.execute(
        "SELECT DISTINCT c.caller_rva AS rva FROM calls c WHERE c.callee_rva=? "
        "ORDER BY c.caller_rva LIMIT ?",
        (rva, limit)).fetchall()
    if callers:
        lines.append("    called by:")
        for caller in callers:
            label = name_for(conn, caller["rva"]) or "(unidentified)"
            lines.append(f"      <- {fmt(caller['rva'])}  {label}")

    mentions = conn.execute(
        "SELECT kind, rel, line, heading, snippet FROM mentions WHERE rva=? ORDER BY rel, line LIMIT ?",
        (rva, 500 if verbose else limit)).fetchall()
    if mentions:
        lines.append(f"    cited in {len(mentions)} place(s):")
        for mention in mentions:
            heading = f"  {mention['heading']}" if mention["heading"] else ""
            lines.append(f"      [{mention['kind']}] {mention['rel']}:{mention['line']}{heading}")
            lines.append(f"          {mention['snippet'][:200]}")
    return "\n".join(lines)


def cmd_query(conn: sqlite3.Connection, args) -> None:
    if args.rva:
        rva = normalize_rva(args.rva)
        if rva is None:
            raise SystemExit(f"unrecognised rva {args.rva!r}")
        print(describe(conn, rva, args.limit, args.verbose))
        return
    if args.name:
        rows = conn.execute(
            "SELECT DISTINCT m.rva AS rva FROM methods m LEFT JOIN method_alias a ON a.rva=m.rva "
            "WHERE m.name LIKE ? OR m.method = ? OR a.alias LIKE ? ORDER BY m.rva LIMIT ?",
            (f"%{args.name}%", args.name, f"%{args.name}%", args.limit)).fetchall()
    elif args.topic:
        rows = conn.execute(
            "SELECT DISTINCT m.rva AS rva FROM methods m LEFT JOIN mentions x ON x.rva=m.rva "
            "WHERE m.name LIKE ? OR x.snippet LIKE ? OR x.heading LIKE ? ORDER BY m.rva LIMIT ?",
            (f"%{args.topic}%", f"%{args.topic}%", f"%{args.topic}%", args.limit)).fetchall()
    else:
        raise SystemExit("query needs --rva, --name or --topic")
    if not rows:
        print("no match")
        return
    for row in rows:
        print(describe(conn, row["rva"], args.limit, args.verbose))
        print("-" * 78)


def sanitize(term: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", " ", term).strip()
    return cleaned


def cmd_search(conn: sqlite3.Connection, args) -> None:
    terms = [sanitize(term) for term in args.terms]
    terms = [term for term in terms if term]
    if not terms:
        raise SystemExit("search needs at least one alphanumeric term")
    expression = " AND ".join(f'"{term}"' for term in terms) if args.all else " OR ".join(f'"{term}"' for term in terms)
    rows = conn.execute(
        "SELECT rel, kind, line, snippet(search, 3, '[', ']', ' ... ', 12) AS hit FROM search "
        "WHERE search MATCH ? ORDER BY rank LIMIT ?", (expression, args.limit)).fetchall()
    if not rows:
        print("no match")
        return
    for row in rows:
        print(f"[{row['kind']}] {row['rel']}:{row['line']}")
        print(f"    {row['hit'].strip()[:400]}")


def cmd_at(conn: sqlite3.Connection, args) -> None:
    address = normalize_rva(args.address)
    if address is None:
        raise SystemExit(f"unrecognised address {args.address!r}")
    exact = conn.execute("SELECT rva, name FROM methods WHERE rva=?", (address,)).fetchone()
    if exact:
        print(f"{fmt(address)} is the start of {exact['name']}")
        print(describe(conn, address, args.limit))
        return
    rows = conn.execute(
        "SELECT rva, first_instr, last_instr, instrs FROM code_map WHERE first_instr <= ? "
        "AND ? <= last_instr ORDER BY rva LIMIT 3", (address, address)).fetchall()
    if not rows:
        print(describe(conn, address, args.limit))
        return
    for row in rows:
        print(f"{fmt(address)} lies inside {fmt(row['rva'])} "
              f"({fmt(row['first_instr'])}..{fmt(row['last_instr'])}, {row['instrs']} instructions, "
              "extent is an upper bound)")
        print(describe(conn, row["rva"], args.limit))


SENTENCE_SPLIT = re.compile(r"(?<=[.;:!?])\s+|\s+(?=[-*\u2022]\s)")

CODEISH = re.compile(
    r"(machine\.|self\.|fixture\.|\bdef \b|\bimport \b|^\s*[A-Z][A-Z_0-9]{3,}\s*=|"
    r"\bassert\b|==\s*\{|\[\d+\]\s*=|\.py:\d|^\s*#)")
GRADE_MARKERS = [
    ("retracted", ("retracted", "refuted", "disproved", "superseded", "wrong")),
    ("confirmed", ("confirmed", "confirmed)", "verified")),
    ("strong", ("strongly supported", "strongly", "high confidence")),
    ("hypothesis", ("hypothesis", "hypothesised", "hypothesized", "likely", "appears to")),
    ("unknown", ("unknown", "not established", "unresolved", "not recovered", "open item",
                 "remains open", "no recovered", "remain")),
]
GAP_MARKERS = ("unknown", "unresolved", "not established", "remains", "blocked", "pending",
               "open", "not recovered", "never been", "not claimed", "hypothesis",
               "out of scope", "no direct caller", "unidentified")


def est_tokens(text: str) -> int:
    return len(text) // 4 + 1


def clean_sentence(text: str) -> str:
    text = re.sub(r"\*\*|__", "", text)
    text = re.sub(r"\s+", " ", text).strip(" -•\t")
    return text


def grade_of(text: str) -> str:
    lowered = text.lower()
    for label, needles in GRADE_MARKERS:
        if any(needle in lowered for needle in needles):
            return label
    return ""


def dedupe_key(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", text.lower())[:90]


def is_current(rel: str) -> bool:
    return any(source in rel for source in CURRENT_SOURCES)


def extract_claims(conn: sqlite3.Connection, terms, limit: int, gaps_only: bool = False):
    """Deduplicated sentences from current sources that mention the topic.

    Sentences rather than lines: the documents are written in long paragraphs, so a
    matching line can carry five separate conclusions and quoting it whole wastes
    the budget. Tool sources are excluded from this section - a fixture constant
    assignment is not a recovered conclusion - and code-shaped lines are dropped.
    """
    expression = " OR ".join(f'"{term}"' for term in terms)
    rows = conn.execute(
        "SELECT rel, kind, line, text FROM search WHERE search MATCH ? ORDER BY rank LIMIT 400",
        (expression,)).fetchall()
    seen, out = set(), []
    for row in rows:
        rel = row["rel"]
        marker = (rel, row["line"])
        if not is_current(rel) or marker in seen or row["kind"] != "doc":
            continue
        seen.add(marker)
        for sentence in SENTENCE_SPLIT.split(row["text"]):
            sentence = clean_sentence(sentence)
            lowered = sentence.lower()
            if not any(term in lowered for term in terms):
                continue
            if CODEISH.search(sentence):
                continue
            if gaps_only:
                if not any(marker in lowered for marker in GAP_MARKERS):
                    continue
            elif len(sentence) < 40:
                # too short to carry a conclusion, and usually a fragment
                continue
            elif grade_of(sentence) == "unknown":
                # an open item, so it belongs in the gap list rather than here
                continue
            if not sentence.rstrip().endswith((".", ":", "!", "?", ")", "`")):
                sentence += "…"
            out.append({"text": sentence, "rel": rel, "line": row["line"],
                        "grade": grade_of(sentence)})
    out.sort(key=lambda item: (0 if item["grade"] in ("confirmed", "strong") else 1,
                               0 if "0x" in item["text"] else 1,
                               len(item["text"])))
    kept, keys = [], []
    for item in out:
        candidate = dedupe_key(item["text"])
        if not candidate or any(candidate[:70] in existing or existing in candidate[:70]
                                for existing in keys):
            continue
        keys.append(candidate)
        kept.append(item)
        if len(kept) >= limit:
            break
    return kept


# Share of the budget each section may claim. Fixed, so the same query and budget
# always produce the same brief and a reader can tell what was trimmed.
SECTION_SHARES = {
    "identity": 0.06, "established": 0.22, "functions": 0.22, "gaps": 0.18,
    "relationships": 0.06, "pointers": 0.06, "analysis": 0.20, "neighbourhood": 0.10,
    "recorded": 0.28, "all-addresses": 0.22, "overlay": 0.05,
}

# The key/number block is priced separately from the evidence budget, on purpose. Naming a
# number costs tokens but carries almost no addresses, so sharing the budget with the
# address roll-call trades away recall for a line of prose (measured with
# ka_brief_bench.py: a shared 15% share cut the status brief from 44 addresses to 25 at the
# same total size). The block therefore gets its own small allowance, is reported
# separately in the header, and can never displace an address.
KEY_BLOCK_SHARE = 0.09

WORD_SPLIT = re.compile(r"[^A-Za-z0-9]+|(?<=[a-z0-9])(?=[A-Z])")


def word_tokens(text: str) -> set:
    """Words of a name or a role sentence, camel case separated.

    Matching a topic word against a raw substring makes `down` match `KnockingDown` and
    `state` match `status`, which is how a key section fills up with the wrong numbers.
    Splitting on case and punctuation keeps `KnockingDown` matching `down` (a real word in
    the phrase) while `status` no longer matches `BATTLE_STATE`.
    """
    return {token.lower() for token in WORD_SPLIT.split(text or "") if token}


def load_key_index(path) -> dict:
    """The combat key/parameter/status number overlay, built by recover_combat_keys.py.

    It owns no native fact: every entry is a number, an official name from the frozen
    metadata and a role quoted from current evidence. The brief only ever shows the
    numbers the topic itself reaches, so a key database is never dumped wholesale.
    """
    if not path or not Path(path).is_file():
        return {}
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("schema") != "ka-combat-keys-1":
        return {}
    return payload


def key_index_section(conn: sqlite3.Connection, index: dict, terms, rvas, limit: int = 4):
    """The named numbers this topic actually reaches, and nothing else.

    The blackboard keys and the id spaces the same routines reach are ranked in two pools
    and then joined. One pool would let three skill-type constants crowd out every key;
    two pools keep both visible in the same handful of lines. Within a pool the official
    name is matched first, then the recovered role, and among role matches the earliest
    mention in the role sentence wins - which is what separates "Single status slot ..."
    from a key whose role only lists the word `KnockingDown` at the end of an enumeration.
    """
    if not index:
        return []
    wanted = {int(rva) for rva in rvas}
    lowered = {term.lower() for term in terms if len(term) >= 3}

    def name_hit(text):
        return bool(word_tokens(text) & lowered)

    def role_position(role):
        if not role:
            return None
        for match in re.finditer(r"[A-Za-z0-9]+", role):
            if match.group(0).lower() in lowered:
                return match.start()
        return None

    def score(entry, functions, label):
        by_address = len(functions & wanted)
        if name_hit(entry.get("officialName")):
            rank = 0
        elif role_position(entry.get("recoveredRole")) is not None:
            rank = 1
        elif by_address:
            rank = 2
        else:
            return None
        return (rank, -by_address, role_position(entry.get("recoveredRole")) or 9999,
                -entry.get("sites", 0), label, entry.get("recoveredRole") or "",
                entry.get("confidence") or "", entry.get("operations") or {},
                entry.get("sites", 0))

    key_rows, number_rows = [], []
    for entry in index.get("keys", []):
        label = f'`{entry["key"]}` {entry.get("officialName") or "(no official name)"}'
        row = score(entry, {int(rva, 16) for rva in entry["functions"]}, label)
        if row:
            key_rows.append(row)
    for entry in index.get("params", []):
        label = (f'Param `{entry["value"]}` '
                 f'{entry.get("officialName") or "(no official name)"}')
        row = score(entry, {int(rva, 16) for rva in entry["functions"]}, label)
        if row:
            number_rows.append(row)
    for entry in index.get("statusConstants", []):
        # A status/skill-type constant is only worth a line when the topic names it: its
        # numeric id is not something the topic's addresses can lead a reader to.
        if not name_hit(entry.get("officialName")):
            continue
        label = f'`{entry["entry"]}` {entry.get("officialName") or ""}'.strip()
        row = score(entry, set(), label)
        if row:
            number_rows.append(row)
    if not key_rows and not number_rows:
        return []
    key_rows.sort()
    number_rows.sort()
    if key_rows and number_rows:
        # Blackboard keys first: naming `board[8]` is this layer's main job, so the keys
        # take half the lines and the id spaces the same routines reach take the rest.
        take_keys = key_rows[:max(1, limit // 2)]
        selected = take_keys + number_rows[:max(0, limit - len(take_keys))]
    else:
        selected = (key_rows or number_rows)[:limit]
    items = ["**Numbers behind this topic:**"]
    for _rank, _hits, _position, _sites, label, role, confidence, operations, sites in selected:
        grade = f" [{confidence}]" if confidence and confidence != "unknown" else ""
        detail = f" ({sites} sites)" if sites else ""
        items.append(f"- {label}{grade}: "
                     f"{shorten(role, 58) if role else '(no recovered role recorded)'}{detail}")
    return items


def fit_key_block(sections, budget: int):
    """Render the key/number block inside its own small allowance.

    Returns (text, cost). The block is listed after the evidence body and its cost is
    reported next to the evidence cost, so a reader always sees what the naming layer
    added instead of finding the budget quietly exceeded.
    """
    block = [section for section in sections if section[0] == "keys"]
    if not block:
        return "", 0
    allowance = max(60, min(int(budget * KEY_BLOCK_SHARE), 150))
    taken, cost = [], 0
    for item in block[0][2]:
        item_cost = est_tokens(item)
        if taken and cost + item_cost > allowance:
            break
        taken.append(item)
        cost += item_cost
    return "\n".join(taken), cost


def load_overlay(path) -> dict:
    """The combat overlay, if it is present, keyed by RVA.

    The overlay owns semantics only (purpose, confidence, checks, models, unresolved
    questions); everything native - identity, signature, extent, citations, callers and
    callees - is read from this index. Keeping that split is what stops the two systems
    from drifting into two truths.
    """
    if not path or not Path(path).is_file():
        return {}
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    entries = {}
    for row in payload.get("functions", []):
        rva = normalize_rva(str(row.get("rva")))
        if rva is None:
            continue
        annotation = row.get("annotation") or {}
        row_kind = row.get("kind") or ("method" if row.get("firstPass") or row.get("nameSource") in
                                       ("audit.json", "script.json") else "code-window")
        entries[rva] = dict(
            kind=row_kind, name=row.get("name"), tracks=row.get("tracks") or [],
            purpose=annotation.get("purpose"), confidence=annotation.get("confidence"),
            evidence=annotation.get("evidence") or [],
            unresolved=annotation.get("unresolved") or [],
            checks=[Path(p).name for p in (row.get("relatedChecks") or [])],
            models=[Path(p).name for p in (row.get("relatedModels") or [])],
            annotatedModels=annotation.get("relatedModels") or [],
        )
    return dict(functions=entries,
                nativeIndex=payload.get("nativeIndex"),
                schema=payload.get("schema"), generatedUtc=payload.get("generatedUtc"))


def overlay_section(overlay: dict, rvas, limit: int = 8) -> list:
    """Combat semantics for the addresses a topic or a package already reached.

    Nothing is injected wholesale: only addresses the native layer ranked into this
    retrieval get their overlay line, and only when the overlay actually says something.
    """
    entries = (overlay or {}).get("functions") or {}
    if not entries:
        return []
    rows = []
    for rva in rvas:
        entry = entries.get(rva)
        if not entry:
            continue
        rows.append((rva, entry))
        if len(rows) >= limit:
            break
    if not rows:
        return []
    items = ["**Combat overlay** (semantic layer over the native index; purpose and checks come "
             "from the overlay, the code identity from the index):"]
    for rva, entry in rows:
        purpose = entry.get("purpose") or "(no established purpose recorded)"
        grade = f" [{entry['confidence']}]" if entry.get("confidence") else ""
        detail = []
        if entry.get("checks"):
            detail.append("checks: " + ", ".join(entry["checks"][:3]))
        models = list(entry.get("models") or []) + list(entry.get("annotatedModels") or [])
        if models:
            detail.append("models: " + ", ".join(str(m).split("/")[-1] for m in models[:2]))
        if entry.get("tracks"):
            detail.append("track: " + ",".join(entry["tracks"][:3]))
        suffix = (" - " + "; ".join(detail)) if detail else ""
        items.append(f"- `{fmt(rva)}` {shorten(purpose, 130)}{grade}{suffix}")
        for note in (entry.get("unresolved") or [])[:1]:
            items.append(f"  - unresolved: {shorten(note, 130)}")
    return items


def fit_sections(sections, budget: int):
    """Render enumerated sections into a fixed budget.

    Greedy top-to-bottom would let a long conclusion list starve the gap list, which
    is the one section a reader most needs. Each section therefore has a share, and
    leftover budget is handed back in priority order afterwards.
    """
    entries = []
    for name, priority, items in sorted(sections, key=lambda section: section[1]):
        cap = budget if priority == 0 else max(45, int(budget * SECTION_SHARES.get(name, 0.1)))
        entries.append({"name": name, "priority": priority, "items": items,
                        "taken": [], "cost": 0, "cap": cap,
                        "extra": 0 if priority == 0 else max(20, int(cap * 0.5))})
    for entry in entries:
        for item in entry["items"]:
            cost = est_tokens(item)
            if entry["cost"] + cost > entry["cap"]:
                break
            entry["taken"].append(item)
            entry["cost"] += cost
    used = sum(entry["cost"] for entry in entries)
    for entry in entries:
        if used >= budget:
            break
        for item in entry["items"][len(entry["taken"]):]:
            cost = est_tokens(item)
            if used + cost > budget or entry["cost"] + cost > entry["cap"] + entry["extra"]:
                break
            entry["taken"].append(item)
            entry["cost"] += cost
            used += cost
    text = "\n".join("\n".join(entry["taken"]) for entry in entries if entry["taken"])
    dropped = [entry["name"] for entry in entries if not entry["taken"]]
    trimmed = [entry["name"] for entry in entries
               if entry["taken"] and len(entry["taken"]) < len(entry["items"])]
    return text, used, dropped, trimmed


def cmd_brief(conn: sqlite3.Connection, args) -> None:
    """The smallest package that lets a reader start reasoning.

    Compact by construction: deduplicated conclusions, a short function table that
    links to code rather than embedding it, the relationships that carry the issue,
    and an explicit gap list. It is sized by --budget, not by the length of the
    documents it draws from.
    """
    budget = args.budget
    overlay = {} if getattr(args, "no_overlay", False) else load_overlay(
        getattr(args, "overlay", DEFAULT_OVERLAY))
    key_index = {} if getattr(args, "no_keys", False) else load_key_index(
        getattr(args, "keys_file", DEFAULT_KEYS))
    lines = []
    if args.rva:
        rva = normalize_rva(args.rva)
        if rva is None:
            raise SystemExit(f"unrecognised rva {args.rva!r}")
        subject = f"{fmt(rva)} {name_for(conn, rva) or '(no managed name)'}"
        sections = build_rva_sections(conn, rva, budget, overlay, key_index)
    else:
        terms = [word for argument in args.topic for word in sanitize(argument).split()]
        terms = [term for term in terms if term]
        if not terms:
            raise SystemExit("brief needs a topic or --rva")
        subject = " ".join(args.topic)
        sections = build_topic_sections(conn, terms, budget, args.max_functions, overlay,
                                        key_index)

    evidence = [section for section in sections if section[0] != "keys"]
    body, used, dropped, trimmed = fit_sections(evidence, budget)
    numbers, number_cost = fit_key_block(sections, budget)
    lines.append(f"# Brief: {subject}")
    lines.append("")
    priced = (f", plus {number_cost} tokens naming the numeric keys this topic uses"
              if number_cost else "")
    lines.append(f"Compact retrieval ({used} tokens of a {budget} budget{priced}) from the frozen "
                 f"evidence. Statements are quoted from current documents with their own "
                 f"grades; nothing here is re-derived. Code is linked, not embedded.")
    lines.append("")
    lines.append(body)
    if numbers:
        lines.append("")
        lines.append(numbers)
    if dropped:
        lines.append("")
        lines.append(f"_Dropped for budget: {', '.join(dropped)}._")
    if trimmed:
        lines.append(f"_Trimmed for budget: {', '.join(trimmed)}._")
    lines.append("")
    if args.rva:
        lines.append(f"Escalate with: `ka_index.py query --rva {args.rva} --verbose` for every "
                     f"citation, `ka_ghidra_helpers.py --targets {args.rva}` to decompile it.")
    else:
        lines.append(f"Escalate with: `ka_index.py query --rva <rva> --verbose` for one function, "
                     f"`ka_index.py package {subject}` for the full package, and "
                     f"`combat_native.py keys` / `key <BKI:62>` to name any number in one step.")
    text = "\n".join(lines) + "\n"
    if args.brief_file:
        target = Path(args.brief_file)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        print(f"wrote {target} ({len(text)} bytes, ~{est_tokens(text)} tokens, "
              f"{len(dropped)} section(s) cut)")
    else:
        print(f"<!-- {len(text)} bytes, ~{est_tokens(text)} tokens -->")
        print(text)


def build_topic_sections(conn: sqlite3.Connection, terms, budget: int, max_functions: int,
                         overlay: dict | None = None, key_index: dict | None = None):
    hit_count, doc_sections, grouped, matched_terms = collect_matching_citations(
        conn, terms, hit_limit=args_limit(budget), section_limit=40)
    terms = matched_terms or terms
    sections = []
    sections.append(("identity", 0, [
        f"**Topic words matched:** {' '.join(terms)}. Matching document lines: {hit_count}. "
        f"Distinct addresses found: {len(grouped)}."]))

    claims = extract_claims(conn, terms, 6)
    if claims:
        items = ["**Established, deduplicated from current documents** (each line is quoted at "
                 "the grade its author gave it):"]
        for claim in claims:
            grade = f" _({claim['grade']})_" if claim["grade"] else ""
            items.append(f"- {shorten(claim['text'], 210)}{grade} "
                         f"`{Path(claim['rel']).name}:{claim['line']}`")
        sections.append(("established", 1, items))

    ranked = rank_functions(conn, grouped, max_functions, terms)
    if ranked:
        items = [f"**Functions that carry the issue** (top {len(ranked)} of {len(grouped)} "
                 f"addresses this topic reaches, ranked by citations in current work; code is "
                 f"linked, not embedded):", "",
                 "| rva | function | code |", "|---|---|---|"]
        for index, (rva, note) in enumerate(ranked):
            code = artifact_link(conn, rva)
            # Notes are the expensive column and matter least as the tail gets long.
            suffix = f" - {note}" if note and index < 6 else ""
            items.append(f"| `{fmt(rva)}` | {name_for(conn, rva) or '(unnamed)'}{suffix} | {code} |")
        sections.append(("functions", 2, items))

    rel = relationships(conn, [entry[0] for entry in ranked], 8)
    if rel:
        sections.append(("relationships", 5,
                         ["**Relationships among the above** (direct `bl`/`b` only; absence "
                          "means indirect, virtual or stubbed, never 'no relationship'):"] +
                         [f"- `{fmt(a)}` {name_for(conn, a) or ''} -> "
                          f"`{fmt(b)}` {name_for(conn, b) or ''}".rstrip() for a, b in rel]))

    combat = overlay_section(overlay or {}, [entry[0] for entry in ranked], limit=5)
    if combat:
        # Priority 6: after the relationships, so the address roll-call is filled first and
        # the overlay can never starve recall (measured with ka_brief_bench.py).
        sections.append(("overlay", 6, combat))

    numbers = key_index_section(conn, key_index or {}, terms, grouped, limit=4)
    if numbers:
        # Priority 7: last of all. Naming a number must never cost the brief an address,
        # so this section fills only after everything that carries recall has been placed.
        sections.append(("keys", 7, numbers))

    if grouped:
        # A roll-call keeps recall: the table above carries detail for the few that
        # matter most, and this makes sure no address in the set is invisible.
        parts = []
        # Most-cited first: if the budget truncates the roll-call, the addresses that
        # survive are the ones the topic actually leans on.
        ordered = sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0]))
        for index, (rva, _cites) in enumerate(ordered):
            # Names for the addresses most likely to be queried; bare addresses after
            # that, so the roll-call can stay complete without eating the budget.
            # Names only for the head of the list: they cost ~6 tokens each and stop
            # mattering well before the tail, while bare addresses are ~2.
            label = name_for(conn, rva) if index < 30 else ""
            parts.append(f"`{fmt(rva)}`" + (f" {label.split('$$')[-1]}" if label else ""))
        items = [f"**Every address this topic reaches** ({len(grouped)}; listed so nothing is "
                 f"silently dropped by the budget):"]
        items += ["- " + "; ".join(parts[index:index + 10])
                  for index in range(0, min(len(parts), 150), 10)]
        if len(parts) > 150:
            items.append(f"- …and {len(parts) - 150} more")
        sections.append(("all-addresses", 4, items))

    gaps = extract_claims(conn, terms, 6, gaps_only=True)
    unresolved = helper_gaps(conn, [entry[0] for entry in ranked])
    if gaps or unresolved:
        items = ["**Unresolved** (as stated in the current documents):"]
        for gap in gaps:
            items.append(f"- {shorten(gap['text'], 190)} "
                         f"`{Path(gap['rel']).name}:{gap['line']}`")
        for line in unresolved:
            items.append(f"- {line}")
        sections.append(("gaps", 3, items))

    if doc_sections:
        items = ["**Read instead of whole documents** (paths relative to the RE root):"]
        for section in doc_sections[:5]:
            items.append(f"- `{section['rel']}` lines {section['start_line']}-"
                         f"{section['end_line']} - {section['heading']}")
        sections.append(("pointers", 6, items))
    return sections


def build_rva_sections(conn: sqlite3.Connection, rva: int, budget: int,
                       overlay: dict | None = None, key_index: dict | None = None):
    sections = []
    row = conn.execute("SELECT * FROM methods WHERE rva=?", (rva,)).fetchone()
    audited = conn.execute("SELECT * FROM audit_identity WHERE rva=?", (rva,)).fetchone()
    head = [f"**Identity:** `{fmt(rva)}` {name_for(conn, rva) or '(no managed name)'}"]
    if row and row["signature"]:
        head.append(f"`{row['signature'][:300]}`")
    head.append(f"**Code:** {artifact_link(conn, rva)}")
    if audited and audited["end_rva"]:
        head.append(f"**Audited slice:** end `{fmt(audited['end_rva'])}`, sha256 "
                    f"`{audited['sha256'][:16]}...` (`{audited['source']}`)")
    sections.append(("identity", 0, ["  \n".join(head)]))

    finding = conn.execute("SELECT * FROM helper_findings WHERE rva=?", (rva,)).fetchone()
    if finding:
        sections.append(("analysis", 1, [
            f"**Analysis verdict:** {finding['verdict']} [confidence: {finding['confidence']}]",
            f"- observed: {shorten(finding['observed'], 380)}",
            f"- implementation: {finding['implementation']}"]))

    callers = [r["rva"] for r in conn.execute(
        "SELECT DISTINCT caller_rva AS rva FROM calls WHERE callee_rva=? LIMIT 12", (rva,))]
    callees = [r["rva"] for r in conn.execute(
        "SELECT DISTINCT callee_rva AS rva FROM calls WHERE caller_rva=? LIMIT 12", (rva,))]
    combat = overlay_section(overlay or {}, [rva])
    if combat:
        sections.append(("overlay", 1, combat))
    numbers = key_index_section(conn, key_index or {}, [], [rva], limit=4)
    if numbers:
        sections.append(("keys", 7, numbers))
    if callers or callees:
        items = ["**Neighbourhood** (direct `bl`/`b` only):"]
        if callers:
            items.append("- called by: " + ", ".join(
                f"`{fmt(c)}` {name_for(conn, c) or ''}".strip() for c in callers[:6]))
        if callees:
            items.append("- calls: " + ", ".join(
                f"`{fmt(c)}` {name_for(conn, c) or ''}".strip() for c in callees[:6]))
        sections.append(("neighbourhood", 2, items))

    mentions = conn.execute(
        "SELECT rel, line, snippet, heading FROM mentions WHERE rva=? ORDER BY rel, line LIMIT 40",
        (rva,)).fetchall()
    claims = []
    keys = []
    for mention in mentions:
        if not is_current(mention["rel"]):
            continue
        sentence = clean_sentence(mention["snippet"])
        key = dedupe_key(sentence)
        if not key or any(key[:70] in existing or existing in key[:70] for existing in keys):
            continue
        keys.append(key)
        claims.append((sentence, mention["rel"], mention["line"]))
    if claims:
        items = ["**Recorded about it** (deduplicated, current sources):"]
        for sentence, rel, line in claims[:5]:
            items.append(f"- {shorten(sentence, 200)} `{Path(rel).name}:{line}`")
        sections.append(("recorded", 3, items))

    has_code = conn.execute("SELECT 1 FROM artifacts WHERE rva=?", (rva,)).fetchone() is not None
    if not finding and not has_code:
        sections.append(("gaps", 4, ["**Unresolved:** no listing, decompile or analysis verdict is "
                                     "indexed for this address; slice it before reasoning about it."]))
    elif not finding and not audited:
        sections.append(("gaps", 4, ["**Unresolved:** no audited slice identity and no analysis "
                                     "verdict for this address; the listing above is the only "
                                     "evidence indexed so far."]))
    return sections


def args_limit(budget: int) -> int:
    return 250 if budget >= 800 else 120


def shorten(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    return (cut.rsplit(" ", 1)[0] if " " in cut else cut) + "…"


def first_clause(text: str, limit: int) -> str:
    """A short role hint: the first sentence-shaped fragment, or nothing.

    Quoting a mid-sentence fragment in a table reads as a broken claim, so a fragment
    that does not start a sentence is dropped rather than trimmed.
    """
    for candidate in SENTENCE_SPLIT.split(clean_sentence(text)):
        candidate = clean_sentence(candidate)
        if len(candidate) < 20:
            continue
        if not candidate[:1].isupper() and not candidate.startswith("`"):
            continue
        return shorten(candidate, limit)
    return ""


def artifact_link(conn: sqlite3.Connection, rva: int) -> str:
    rows = conn.execute(
        "SELECT kind, rel FROM artifacts WHERE rva=? ORDER BY kind LIMIT 2", (rva,)).fetchall()
    if not rows:
        return "_none - would need slicing_"
    return "; ".join(f"`{row['rel']}`" for row in rows)


def helper_gaps(conn: sqlite3.Connection, rvas) -> list:
    out = []
    for rva in rvas:
        finding = conn.execute(
            "SELECT verdict, confidence FROM helper_findings WHERE rva=? AND "
            "(confidence LIKE '%unknown%' OR confidence LIKE 'low%' OR verdict LIKE '%unresolved%')",
            (rva,)).fetchone()
        if finding:
            out.append(f"`{fmt(rva)}` {finding['verdict']} [{finding['confidence']}]")
    return out[:3]


def rank_functions(conn: sqlite3.Connection, grouped, limit: int, terms):
    clause, params = current_predicate()
    scored = []
    for rva, cites in grouped.items():
        is_method = conn.execute("SELECT 1 FROM methods WHERE rva=?", (rva,)).fetchone() is not None
        # Rank on how often this topic's own sections cite the function, not on how
        # often it is cited anywhere. Global popularity drags unrelated hot functions
        # such as the B3 receipt pair into every other topic's table.
        local = len(cites)
        local_current = sum(1 for cite in cites if is_current(cite["rel"]))
        has_code = conn.execute("SELECT 1 FROM artifacts WHERE rva=?", (rva,)).fetchone() is not None
        note = ""
        if is_method:
            for cite in sorted(cites, key=lambda item: (KIND_RANK.get(item["kind"], 9),
                                                        len(item["snippet"]))):
                if is_current(cite["rel"]):
                    note = first_clause(cite["snippet"], 95)
                    break
        scored.append((0 if is_method else 1, -local_current, -local,
                       0 if has_code else 1, rva, note))
    scored.sort()
    return [(rva, note) for _m, _lc, _l, _h, rva, note in scored[:limit]]


def relationships(conn: sqlite3.Connection, rvas, limit: int):
    if not rvas:
        return []
    placeholders = ",".join("?" for _ in rvas)
    rows = conn.execute(
        f"SELECT DISTINCT caller_rva, callee_rva FROM calls WHERE caller_rva IN ({placeholders}) "
        f"AND callee_rva IN ({placeholders}) ORDER BY caller_rva LIMIT ?",
        (*rvas, *rvas, limit)).fetchall()
    return [(row["caller_rva"], row["callee_rva"]) for row in rows]


def cmd_gaps(conn: sqlite3.Connection, args) -> None:
    """What the documents already reference but no listing covers yet.

    This is the reading order for slicing work: it names the functions a current
    document keeps citing while the only thing a query can return for them is
    "would need slicing".
    """
    records = unsliced_cited(conn)
    tiers = {}
    for record in records:
        tiers[record["tier"]] = tiers.get(record["tier"], 0) + 1
    print(f"  cited with no listing                {len(records)}")
    for tier in ("A", "B", "C", "D", "record-only", "-"):
        label = {"-": "framework/BCL (excluded)",
                 "record-only": "cited only by quarantine or archive records",
                 "A": "game code, cited in 8+ current places",
                 "B": "game code, cited in 4-7 current places",
                 "C": "game code, cited in 2-3 current places",
                 "D": "game code, cited once by a current place"}[tier]
        print(f"    tier {tier:<11} {tiers.get(tier, 0):>5}   {label}")
    if args.summary:
        return
    wanted = {tier.strip().upper() for tier in args.tier.split(",")} if args.tier else {"A"}
    selected = [record for record in records if record["tier"] in wanted]
    print(f"\n{'tier':>4} {'current':>7} {'all':>5} {'rva':>10}  name")
    for record in selected[: args.limit]:
        print(f"{record['tier']:>4} {record['current_citations']:>7} {record['citations']:>5} "
              f"{fmt(record['rva']):>10}  {(record['name'] or '')[:92]}")
    if len(selected) > args.limit:
        print(f"  ... and {len(selected) - args.limit} more in this tier")


def cmd_helpers(conn: sqlite3.Connection, args) -> None:
    """Executable code no dumped method claims, ranked by how much the docs cite it.

    These are the addresses the research documents quote without a name. Listing
    them turns "there is probably a helper in there" into a ranked, reproducible
    starting list.
    """
    rows = conn.execute(
        "SELECT c.callee_rva AS rva, COUNT(DISTINCT c.caller_rva) AS callers, "
        "       COUNT(*) AS call_sites, "
        "       (SELECT COUNT(*) FROM mentions m WHERE m.rva = c.callee_rva) AS cites, "
        "       (SELECT COUNT(*) FROM audit_identity a WHERE a.rva = c.callee_rva) AS audited "
        "FROM calls c "
        "LEFT JOIN methods m ON m.rva = c.callee_rva "
        "LEFT JOIN code_map cm ON cm.first_instr <= c.callee_rva "
        "     AND c.callee_rva <= cm.last_instr "
        "WHERE m.rva IS NULL AND cm.rva IS NULL "
        "GROUP BY c.callee_rva ORDER BY callers DESC, cites DESC LIMIT ?",
        (args.limit,)).fetchall()
    if not rows:
        print("no unclaimed entry points recorded - rebuild with the ELF sweep enabled")
        return
    print(f"{'entry point':>12}  {'callers':>7}  {'sites':>6}  {'cites':>6}  cited by")
    for row in rows:
        if row["cites"] == 0 and not args.all:
            continue
        sources = conn.execute(
            "SELECT DISTINCT rel FROM mentions WHERE rva=? LIMIT 2", (row["rva"],)).fetchall()
        first = "; ".join(Path(source["rel"]).name for source in sources)
        print(f"{fmt(row['rva']):>12}  {row['callers']:>7}  {row['call_sites']:>6}  "
              f"{row['cites']:>6}  {first}")
    print(f"\n{len(rows)} shown. Every row is a call target that is neither a dumped method "
          "start nor inside a dumped method's extent.")
    print("These are addresses no Il2CppDumper method start claims. That is a fact about the "
          "dump, not a claim that the code is unimportant - several are the IL2CPP runtime "
          "helpers every fight routine calls.")


def cmd_sections(conn: sqlite3.Connection, args) -> None:
    """List a document's headings with their line ranges and address counts.

    This is how a 216 KB research document becomes readable in bounded pieces
    instead of being loaded whole.
    """
    rows = conn.execute(
        "SELECT rel, level, heading, start_line, end_line, rvas FROM doc_sections "
        "WHERE rel LIKE ? ORDER BY rel, start_line LIMIT ?",
        (f"%{args.doc}%", args.limit)).fetchall()
    if not rows:
        print("no document matched")
        return
    current = None
    for row in rows:
        if row["rel"] != current:
            current = row["rel"]
            print(f"\n{current}")
        indent = "  " * max(0, row["level"] - 1)
        print(f"  {indent}{row['heading']}   [lines {row['start_line']}-{row['end_line']}, "
              f"{row['rvas']} distinct rva(s)]")


KIND_RANK = {"doc": 0, "tool": 1, "evidence": 2, "audit": 3, "record": 4, "name-match": 8}


def collect_matching_citations(conn: sqlite3.Connection, terms, hit_limit: int,
                               section_limit: int = 40, include_records: bool = False):
    """Topic -> documentation sections -> every address those sections cite.

    Matching single lines fails on multi-word topics: a section can discuss the
    holy herb, healing and the banner without any one line containing all three
    words. So each term is searched separately and the results are combined at the
    section level, most-terms-present first, and only then expanded to the
    addresses those sections cite.
    """
    conn.execute("DROP TABLE IF EXISTS _hits")
    conn.execute("CREATE TEMP TABLE _hits(term TEXT, rel TEXT, line INTEGER, position INTEGER)")
    lines_per_term = {}
    for index, term in enumerate(terms):
        rows = conn.execute(
            "SELECT rel, line FROM search WHERE search MATCH ? ORDER BY rank LIMIT ?",
            (f'"{term}"', hit_limit)).fetchall()
        lines_per_term[term] = rows
        conn.executemany(
            "INSERT INTO _hits VALUES (?,?,?,?)",
            [(term, row["rel"], row["line"], position) for position, row in enumerate(rows)])
    total_lines = sum(len(rows) for rows in lines_per_term.values())
    if not total_lines:
        return 0, [], {}, []

    # Quarantine packets and archived before-images repeat live text verbatim; they
    # are the record of a change, not the place to read a rule, so they are held out
    # unless explicitly asked for.
    record_filter = "" if include_records else "AND ds.kind <> 'record' "
    rows = conn.execute(
        "SELECT ds.rel, ds.kind, ds.heading, ds.start_line, ds.end_line, "
        "       COUNT(DISTINCT h.term) AS terms_present, COUNT(*) AS hit_lines, "
        "       MIN(h.position) AS best_position "
        "FROM _hits h JOIN doc_sections ds ON ds.rel = h.rel "
        "     AND h.line BETWEEN ds.start_line AND ds.end_line "
        f"WHERE 1=1 {record_filter}"
        "GROUP BY ds.rel, ds.start_line "
        "ORDER BY terms_present DESC, best_position ASC LIMIT ?",
        (hit_limit,)).fetchall()
    sections = [dict(row) for row in rows]

    def rank(section):
        return (KIND_RANK.get(section["kind"], 9), section["rel"], section["start_line"])

    # Density first: a section that matches more of the topic's words, on more lines,
    # is a better candidate than one that happens to sit earlier in the alphabet. The
    # previous ordering dropped topic density here and made selection unstable - the
    # 40 survivors changed with the candidate limit rather than with relevance.
    sections.sort(key=lambda section: (-section["terms_present"], *rank(section)))
    sections = sections[:section_limit]

    grouped: dict[int, list] = {}
    seen_citations = set()

    def add(cursor_rows):
        for row in cursor_rows:
            key = (row["rva"], row["rel"], row["line"])
            if key in seen_citations:
                continue
            seen_citations.add(key)
            grouped.setdefault(row["rva"], []).append(dict(row))

    for section in sections:
        add(conn.execute(
            "SELECT rva, rel, line, kind, heading, snippet FROM mentions WHERE rel=? "
            "AND line >= ? AND line <= ? ORDER BY line",
            (section["rel"], section["start_line"], section["end_line"])).fetchall())
    for term, hits in lines_per_term.items():
        for hit in hits[: 40]:
            add(conn.execute(
                "SELECT rva, rel, line, kind, heading, snippet FROM mentions "
                "WHERE rel=? AND line=?", (hit["rel"], hit["line"])).fetchall())

    present = {term for term, hits in lines_per_term.items() if hits}
    return total_lines, sections, grouped, sorted(present, key=terms.index)


def seed_by_name(conn: sqlite3.Connection, terms, per_term: int = 60):
    """Second, independent route into a mechanic: identifiers and evidence filenames.

    A topic like "holy herb" may be discussed only in a sentence that cites no
    address, yet the functions and evidence files named after it are exactly what
    a reader wants. These rows are labelled `name-match` so they are never confused
    with a document citation.
    """
    grouped: dict[int, list] = {}
    for term in terms:
        like = f"%{term.lower()}%"
        for row in conn.execute(
                "SELECT DISTINCT m.rva AS rva, m.name AS name FROM methods m "
                "JOIN artifacts a ON a.rva = m.rva WHERE lower(m.name) LIKE ? "
                "ORDER BY m.rva LIMIT ?", (like, per_term)).fetchall():
            grouped.setdefault(row["rva"], []).append(dict(
                rva=row["rva"], rel="(method identifier match)", line=0, kind="name-match",
                heading="", snippet=f"method identity contains `{term}`: {row['name']}"))
        for row in conn.execute(
                "SELECT rel FROM files WHERE duplicate_of IS NULL AND kind IN ('evidence','tool') "
                "AND lower(rel) LIKE ? LIMIT ?", (like, per_term)).fetchall():
            for cite in conn.execute(
                    "SELECT DISTINCT rva, rel, line, kind, heading, snippet FROM mentions "
                    "WHERE rel=? AND how='address' LIMIT 12", (row["rel"],)).fetchall():
                grouped.setdefault(cite["rva"], []).append(dict(
                    rva=cite["rva"], rel=cite["rel"], line=cite["line"], kind=cite["kind"],
                    heading=cite["heading"], snippet=cite["snippet"]))
    return grouped


def cmd_package(conn: sqlite3.Connection, args) -> None:
    """Build one small retrieval package for a mechanic.

    The package is assembled from what the repository already records - the
    functions cited by matching document lines, their existing slices and
    decompiles, the documentation sections that discuss them, and the direct call
    edges between them. Nothing is asserted that is not already written down, and
    each function carries the sentence that cites it so a reader can judge it.
    """
    terms = [word for argument in args.topic for word in sanitize(argument).split()]
    terms = [term for term in terms if term]
    if not terms:
        raise SystemExit("package needs at least one alphanumeric term")
    hit_count, sections, grouped, matched_terms = collect_matching_citations(
        conn, terms, args.limit, include_records=args.include_records)
    if not hit_count:
        print("no document lines matched - try different or fewer terms")
        return
    if matched_terms != terms:
        print(f"note: only these topic words occur in the corpus: {' '.join(matched_terms)}",
              file=sys.stderr)
    for rva, extra in seed_by_name(conn, matched_terms).items():
        bucket = grouped.setdefault(rva, [])
        seen_keys = {(cite["rel"], cite["line"]) for cite in bucket}
        for cite in extra:
            if (cite["rel"], cite["line"]) not in seen_keys:
                bucket.append(cite)

    citations = {rva: sorted(items, key=lambda item: (KIND_RANK.get(item["kind"], 9),
                                                      item["rel"], item["line"]))
                 for rva, items in grouped.items()}
    ranked = sorted(citations.items(),
                    key=lambda item: (0 if conn.execute(
                        "SELECT 1 FROM methods WHERE rva=?", (item[0],)).fetchone() else 1,
                        -sum(1 for cite in item[1] if cite["kind"] == "doc"),
                        -len(item[1]), item[0]))[: args.max_functions]
    out = []
    out.append(f"# Retrieval package: {' '.join(args.topic)}")
    out.append("")
    out.append("Derived from the frozen Kingdom Adventurers evidence tree by `ka_index.py package`. "
               "Every row below is something the repository already records; nothing here is a new "
               "deduction. Confidence grades are the ones written at the source.")
    out.append("")
    named_only = sum(1 for _rva, items in ranked
                     if all(cite["kind"] == "name-match" for cite in items))
    out.append(f"Document lines matched: {hit_count}. Sections containing them: {len(sections)}. "
               f"Distinct addresses found: {len(grouped)}.")
    out.append("")
    out.append("A function is listed because a matching section cites it, or - when the row says "
               "`name-match` - because its identifier or an evidence filename contains a topic "
               "word. The second route is weaker: it finds the right area without claiming the "
               "document already linked it to this mechanic.")
    if matched_terms != terms:
        out.append("")
        out.append(f"Match note: the full topic matched no line, so the package was built from "
                   f"`{' AND '.join(matched_terms)}`.")
    out.append("")
    out.append("## 0. Start here - the sections that discuss this")
    out.append("")
    for section in sections[:24]:
        out.append(f"- `{section['rel']}` lines {section['start_line']}-{section['end_line']} - "
                   f"{section['heading']}")
    out.append("")
    out.append("## 1. Functions the matching lines cite")
    out.append("")
    out.append("| rva | Il2CppDumper identity | location | existing artifacts | citations |")
    out.append("|---|---|---|---|---|")
    for rva, cites in ranked:
        name = name_for(conn, rva) or "(not an Il2CppDumper method start)"
        artifacts = conn.execute(
            "SELECT kind, rel FROM artifacts WHERE rva=? ORDER BY kind", (rva,)).fetchall()
        artifact_text = "<br>".join(f"{row['kind']}: `{row['rel']}`" for row in artifacts) or "none - would need slicing"
        out.append(f"| `{fmt(rva)}` | `{name}` | {region_of(conn, rva)} | {artifact_text} | {len(cites)} |")
    out.append("")
    out.append("## 2. Why each function is here (the sentence that cites it)")
    out.append("")
    for rva, cites in ranked:
        out.append(f"### `{fmt(rva)}` {name_for(conn, rva) or ''}")
        for cite in cites[: args.evidence_per_function]:
            heading = f" - {cite['heading']}" if cite["heading"] else ""
            out.append(f"- `{cite['rel']}:{cite['line']}`{heading}")
            out.append(f"  > {cite['snippet']}")
        audited = conn.execute("SELECT end_rva, sha256 FROM audit_identity WHERE rva=?",
                               (rva,)).fetchone()
        if audited and audited["end_rva"]:
            out.append(f"- audited slice: end `{fmt(audited['end_rva'])}`, "
                       f"sha256 `{audited['sha256']}`")
        out.append("")

    selected = [rva for rva, _ in ranked]
    if selected:
        placeholders = ",".join("?" for _ in selected)
        edges = conn.execute(
            f"SELECT DISTINCT caller_rva, callee_rva FROM calls WHERE caller_rva IN ({placeholders}) "
            f"AND callee_rva IN ({placeholders}) ORDER BY caller_rva, callee_rva",
            selected + selected).fetchall()
        out.append("## 3. Direct calls recorded between these functions")
        out.append("")
        if edges:
            out.append("Direct `bl`/`b` edges only; virtual and indirect dispatch is not resolved.")
            out.append("")
            for edge in edges:
                out.append(f"- `{fmt(edge['caller_rva'])}` {name_for(conn, edge['caller_rva'])} "
                           f"-> `{fmt(edge['callee_rva'])}` {name_for(conn, edge['callee_rva'])}")
        else:
            out.append("None recorded among the selected functions. Absence here is not evidence of "
                       "no relationship: fixture stubs, delegates and indirect calls are unresolved.")
        out.append("")

    out.append("## 4. Documentation sections to read instead of whole documents")
    out.append("")
    seen_sections = set()
    for rva, cites in ranked:
        for cite in cites:
            heading = cite["heading"]
            if not heading or (cite["rel"], heading) in seen_sections:
                continue
            seen_sections.add((cite["rel"], heading))
            section = conn.execute(
                "SELECT start_line, end_line FROM doc_sections WHERE rel=? AND heading=? LIMIT 1",
                (cite["rel"], heading)).fetchone()
            span = f"lines {section['start_line']}-{section['end_line']}" if section else f"line {cite['line']}"
            out.append(f"- `{cite['rel']}` - {heading} ({span})")
    out.append("")

    overlay = {} if getattr(args, "no_overlay", False) else load_overlay(
        getattr(args, "overlay", DEFAULT_OVERLAY))
    combat = overlay_section(overlay, [entry[0] for entry in ranked], limit=10)
    if combat:
        out.append("## 4b. Combat overlay (semantic layer)")
        out.append("")
        out.append("Purpose, confidence, related checks and related model modules come from the "
                   "combat overlay; every native identity, extent and citation above comes from "
                   "this index.")
        out.append("")
        out.extend(combat)
        out.append("")

    out.append("## 5. Evidence files under these citations")
    out.append("")
    evidence = {}
    for rva, cites in ranked:
        for cite in cites:
            if cite["kind"] in ("evidence", "tool", "record"):
                evidence.setdefault(cite["rel"], 0)
                evidence[cite["rel"]] += 1
    if evidence:
        for rel, count in sorted(evidence.items()):
            out.append(f"- `{rel}` ({count})")
    else:
        out.append("None matched this topic directly.")
    out.append("")
    out.append("## 6. What this package does not say")
    out.append("")
    out.append("- It does not assert that the above functions are the complete set for this mechanic.")
    out.append("- It does not promote any hypothesis to a conclusion; read the cited sentences and "
               "their own grades.")
    out.append("- Absence of a call edge means the call is indirect, virtual, stubbed in the fixture, "
               "or simply not present in the artifacts indexed so far.")
    text = "\n".join(out) + "\n"
    if args.package_file:
        target = Path(args.package_file)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        print(f"wrote {target} ({len(text)} bytes, {len(ranked)} functions)")
    else:
        print(text)


def cmd_verify(conn: sqlite3.Connection, args) -> None:
    """Report, in four distinct categories, whether the index still matches its evidence.

    The distinction matters because the responses differ:
      * binary changed        - the index is invalid; every address may have moved.
      * tool/evidence changed - the index is stale; rebuild before trusting citations.
      * document changed      - only citation line numbers can be stale (prose edits do not
                                move code); reported, and fatal only with --strict.
      * file missing          - the index references something that no longer exists.
    """
    problems, warnings = [], []
    expected = conn.execute("SELECT value FROM meta WHERE key='binary_sha256'").fetchone()
    binary = native_dir(Path(args.root)) / "inputs" / "libil2cpp.so"
    if not binary.is_file():
        problems.append(f"binary missing: {binary}")
    elif expected and expected["value"] != sha256_of(binary):
        problems.append("binary changed: the index was built from a different libil2cpp.so")
    hard, soft, missing = [], [], []
    for row in conn.execute("SELECT path, rel, kind, sha256 FROM files"):
        if is_volatile_output(row["rel"]):
            continue
        path = Path(row["path"])
        if not path.is_file():
            missing.append(row["rel"])
            continue
        if row["sha256"] and sha256_of(path) != row["sha256"]:
            if row["kind"] == "doc":
                soft.append(row["rel"])
            else:
                hard.append((row["rel"], row["kind"]))
    if missing:
        problems.append(f"{len(missing)} indexed file(s) no longer exist (e.g. {missing[0]})")
    if hard:
        problems.append(f"{len(hard)} indexed tool/evidence file(s) changed since the build "
                        f"(e.g. {hard[0][0]}) - rebuild the native index")
    if soft:
        affected = 0
        for rel, in [(rel,) for rel in soft]:
            affected += conn.execute("SELECT COUNT(*) FROM mentions WHERE rel=?", (rel,)).fetchone()[0]
        code_irrelevant = all(rel.endswith((".md", ".json")) for rel in soft)
        warnings.append(f"{len(soft)} indexed document(s) changed; {affected} citation(s) may "
                        f"point at shifted lines"
                        + (" (documentation only - no code identity depends on it)"
                           if code_irrelevant else ""))
    print(f"  binary hash        {expected['value'] if expected else 'not recorded'}")
    print(f"  indexed files      {conn.execute('SELECT COUNT(*) FROM files').fetchone()[0]}"
          f"  ({conn.execute('SELECT COUNT(*) FROM files WHERE duplicate_of IS NULL').fetchone()[0]}"
          " unique)")
    digest_row = conn.execute("SELECT value FROM meta WHERE key='content_digest'").fetchone()
    print(f"  content digest     {digest_row['value'] if digest_row else '(not recorded)'}")
    print(f"  drift: binary={'changed' if any('binary changed' in p for p in problems) else 'same'}"
          f"  tools/evidence={len(hard)}  documents={len(soft)}  missing={len(missing)}")
    for warning in warnings:
        print(f"  WARNING: {warning}")
    for problem in problems:
        print(f"  PROBLEM: {problem}")
    if problems:
        print("  result             FAIL - rebuild with `ka_index.py build` before trusting output")
        raise SystemExit(1)
    if warnings and getattr(args, "strict", False):
        print("  result             FAIL (--strict) - document citations are stale")
        raise SystemExit(1)
    print("  result             ok - the index still matches the frozen evidence"
          + (" (documents changed; citations may be stale)" if warnings else ""))


def cmd_stats(conn: sqlite3.Connection, _args) -> None:
    queries = [
        ("methods", "SELECT COUNT(*) FROM methods"),
        ("types", "SELECT COUNT(*) FROM types"),
        ("fields with a real offset", "SELECT COUNT(*) FROM fields WHERE offset > 0"),
        ("audited slice identities", "SELECT COUNT(*) FROM audit_identity"),
        ("aliases", "SELECT COUNT(*) FROM method_alias"),
        ("artifacts", "SELECT COUNT(*) FROM artifacts"),
        ("citation mentions", "SELECT COUNT(*) FROM mentions"),
        ("  of which resolved by name", "SELECT COUNT(*) FROM mentions WHERE how='name'"),
        ("call edges", "SELECT COUNT(*) FROM calls"),
        ("  method -> method", "SELECT COUNT(*) FROM calls WHERE source_kind='method' AND target_kind='method'"),
        ("  method -> non-method", "SELECT COUNT(*) FROM calls WHERE source_kind='method' AND target_kind='non-method'"),
        ("  unclaimed -> target", "SELECT COUNT(*) FROM calls WHERE source_kind='unclaimed'"),
        ("  tail branches (B)", "SELECT COUNT(*) FROM calls WHERE kind='branch'"),
        ("non-method code windows", "SELECT COUNT(*) FROM code_windows"),
        ("method extents", "SELECT COUNT(*) FROM code_map"),
        ("doc sections", "SELECT COUNT(*) FROM doc_sections"),
        ("indexed files (unique content)", "SELECT COUNT(*) FROM files WHERE duplicate_of IS NULL"),
        ("distinct methods cited anywhere", "SELECT COUNT(DISTINCT rva) FROM mentions"),
        ("methods with artifact and citation",
         "SELECT COUNT(*) FROM (SELECT DISTINCT rva FROM artifacts) a "
         "WHERE a.rva IN (SELECT rva FROM mentions)"),
    ]
    for label, query in queries:
        print(f"  {label:<34} {conn.execute(query).fetchone()[0]}")
    for row in conn.execute("SELECT key, value FROM meta ORDER BY key"):
        print(f"  meta {row['key']:<29} {row['value']}")


def main() -> int:
    # The research documents contain table glyphs and arrows; the Windows console
    # default code page cannot encode them, so force a tolerant UTF-8 stream.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="build directory")
    sub = parser.add_subparsers(dest="command", required=True)

    build_parser = sub.add_parser("build", help="rebuild the index from the frozen evidence")
    build_parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    build_parser.add_argument("--no-xrefs", action="store_true",
                              help="skip the ELF sweep (then capstone/pyelftools are not needed)")
    build_parser.add_argument("--slices-dir", type=Path, default=DEFAULT_SLICES,
                              help="directory of generated listings to index as artifacts")
    build_parser.add_argument("--helper-analysis", type=Path, default=DEFAULT_HELPER_ANALYSIS,
                              help="helper classification JSON produced by ka_ghidra_helpers.py")

    query_parser = sub.add_parser("query", help="describe one or more functions")
    query_parser.add_argument("--rva")
    query_parser.add_argument("--name")
    query_parser.add_argument("--topic")
    query_parser.add_argument("--limit", type=int, default=12)
    query_parser.add_argument("--verbose", action="store_true", help="list every citation")

    search_parser = sub.add_parser("search", help="full-text search the docs and tools")
    search_parser.add_argument("terms", nargs="+")
    search_parser.add_argument("--limit", type=int, default=20)
    search_parser.add_argument("--all", action="store_true", help="require every term")

    at_parser = sub.add_parser("at", help="which function contains this address")
    at_parser.add_argument("address")
    at_parser.add_argument("--limit", type=int, default=12)

    for name, help_text in (("callers", "who calls this function"),
                            ("callees", "what this function calls")):
        sub.add_parser(name, help=help_text).add_argument("rva")

    sections_parser = sub.add_parser("sections", help="headings, line ranges and rva counts of a document")
    sections_parser.add_argument("doc", help="substring of the document path")
    sections_parser.add_argument("--limit", type=int, default=120)

    package_parser = sub.add_parser("package", help="assemble one small retrieval package for a mechanic")
    package_parser.add_argument("topic", nargs="+")
    package_parser.add_argument("--limit", type=int, default=400, help="matching document lines to consider")
    package_parser.add_argument("--max-functions", type=int, default=40)
    package_parser.add_argument("--evidence-per-function", type=int, default=4)
    package_parser.add_argument("--file", dest="package_file",
                                help="write the package to this file instead of stdout")
    package_parser.add_argument("--include-records", action="store_true",
                                help="also list quarantine packets and archived before-images")
    package_parser.add_argument("--overlay", type=Path, default=DEFAULT_OVERLAY,
                                help="combat overlay JSON folded into the package")
    package_parser.add_argument("--no-overlay", action="store_true",
                                help="do not add the combat overlay section")

    verify_parser = sub.add_parser("verify", help="confirm the index still matches the frozen evidence")
    verify_parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    verify_parser.add_argument("--strict", action="store_true",
                               help="also fail when an indexed document changed")
    helpers_parser = sub.add_parser("helpers", help="executable code no dumped method claims")
    helpers_parser.add_argument("--limit", type=int, default=25)
    helpers_parser.add_argument("--all", action="store_true", help="include regions no document cites")
    gaps_parser = sub.add_parser("gaps", help="cited functions that no listing covers yet")
    gaps_parser.add_argument("--tier", default="A", help="A,B,C,D (comma separated); '-' and "
                                                         "'record-only' are also valid")
    gaps_parser.add_argument("--limit", type=int, default=80)
    gaps_parser.add_argument("--summary", action="store_true", help="counts only")

    brief_parser = sub.add_parser("brief", help="smallest useful package, sized by a token budget")
    brief_parser.add_argument("topic", nargs="*", default=[])
    brief_parser.add_argument("--rva", help="brief a single function instead of a topic")
    brief_parser.add_argument("--budget", type=int, default=900, help="approximate token budget")
    brief_parser.add_argument("--max-functions", type=int, default=10)
    brief_parser.add_argument("--file", dest="brief_file", help="write to a file instead of stdout")
    brief_parser.add_argument("--overlay", type=Path, default=DEFAULT_OVERLAY,
                              help="combat overlay JSON folded into the brief")
    brief_parser.add_argument("--no-overlay", action="store_true",
                              help="do not add the combat overlay section")
    brief_parser.add_argument("--keys-file", type=Path, default=DEFAULT_KEYS,
                              help="combat key/parameter index folded into the brief")
    brief_parser.add_argument("--no-keys", action="store_true",
                              help="do not name the numeric keys the topic reaches")

    sub.add_parser("stats", help="index coverage")

    args = parser.parse_args()
    if args.command == "build":
        summary = build(args.root.resolve(), args.out.resolve(), not args.no_xrefs,
                        args.slices_dir.resolve(), args.helper_analysis.resolve())
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        return 0

    conn = open_db(args.out.resolve())
    if args.command == "query":
        cmd_query(conn, args)
    elif args.command == "search":
        cmd_search(conn, args)
    elif args.command == "at":
        cmd_at(conn, args)
    elif args.command in ("callers", "callees"):
        rva = normalize_rva(args.rva)
        if rva is None:
            raise SystemExit(f"unrecognised rva {args.rva!r}")
        print(f"{fmt(rva)}  {name_for(conn, rva) or '(not a method start)'}")
        column, other = ("callee_rva", "caller_rva") if args.command == "callers" else ("caller_rva", "callee_rva")
        rows = conn.execute(
            f"SELECT DISTINCT c.{other} AS rva, c.source FROM calls c "
            f"WHERE c.{column}=? ORDER BY c.{other}", (rva,)).fetchall()
        if not rows:
            print("  (no direct edge recorded)")
        for row in rows:
            label = name_for(conn, row["rva"]) or "(unidentified)"
            print(f"  {fmt(row['rva'])}  {label}   [{row['source']}]")
    elif args.command == "stats":
        cmd_stats(conn, args)
    elif args.command == "sections":
        cmd_sections(conn, args)
    elif args.command == "package":
        cmd_package(conn, args)
    elif args.command == "verify":
        cmd_verify(conn, args)
    elif args.command == "helpers":
        cmd_helpers(conn, args)
    elif args.command == "gaps":
        cmd_gaps(conn, args)
    elif args.command == "brief":
        cmd_brief(conn, args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
