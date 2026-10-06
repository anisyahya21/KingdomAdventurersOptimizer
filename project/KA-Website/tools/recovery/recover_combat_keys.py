"""Build the combat key/index semantics overlay.

Raw combat numbers used to be an identity problem: a document or a model says
`blackboard[62]`, `board[8]` or `Param.GetValue(e, 10)` and a reader has to work out
what the number means before any behaviour can be discussed. This builder removes that
step by joining three things that already exist:

  * the canonical native index (`ka_index.py` -> `ka_index.sqlite`): which call site
    reaches which accessor, inside which function, with which listing;
  * the frozen IL2CPP metadata (`RE-evidence/G2.1/.../dump/dump.cs`): the official
    constant names of `ai.BKI`, `ai.BKL`, `parameter.Param` and `data.SkillData`;
  * the combat overlay (`combat-registry.json`): which functions are combat-relevant,
    their tracks, their related checks and models.

It adds exactly one thing of its own: the value of the key/number register **at the
call site**, recovered by a bounded backward dataflow walk over the real instructions.
Nothing is guessed. A number that cannot be recovered statically is stored as
`dynamic` with the reason and is never given a name.

The result is an overlay, not a second database: every stored identity is an RVA that
resolves in the canonical index and every stored document/model reference is a path,
so the file stays small and cannot become a competing source of truth.

    .venv\\Scripts\\python.exe recover_combat_keys.py            # build
    .venv\\Scripts\\python.exe recover_combat_keys.py --verify   # rebuild and compare

Query it with `combat_native.py keys | key | params | stubs`; the regression is
`check_combat_keys.py`.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from combat_run_manifest import NATIVE_SHA256, canonical_hash

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parents[2]
EVIDENCE = ROOT / 'RE-evidence'
NATIVE_BUILD = EVIDENCE / '20260920-native-index/build'
NATIVE_DB = NATIVE_BUILD / 'ka-index.sqlite'
NATIVE_MANIFEST = NATIVE_BUILD / 'index-manifest.json'
OVERLAY_DIR = EVIDENCE / '20260919-combat-registry'
COMBAT_OVERLAY = OVERLAY_DIR / 'combat-registry.json'
KEYS_PATH = OVERLAY_DIR / 'combat-keys.json'
REPORT_PATH = OVERLAY_DIR / 'combat-keys-report.json'
ANNOTATIONS_PATH = OVERLAY_DIR / 'key-annotations.json'
FROZEN_METADATA = EVIDENCE / 'G2.1/39257e72291d/dump/dump.cs'

SCHEMA = 'ka-combat-keys-1'
ANNOTATIONS_SCHEMA = 'ka-combat-key-annotations-1'

# Tools that *are* this layer. They are never treated as fixtures (their own text would
# otherwise be scanned as if it stubbed something) and never become "related checks".
SELF_FILES = ('recover_combat_keys.py', 'check_combat_keys.py', 'combat_native.py',
              'build_combat_registry.py', 'check_combat_registry.py',
              'check_combat_native_layer.py', 'ka_index.py')

# --------------------------------------------------------------------------------------- #
# the key spaces
# --------------------------------------------------------------------------------------- #

# dump.cs declares `BlackboardInt : Blackboard<BKI, int>` and `BlackboardLong :
# Blackboard<BKL, long>`, and `ecs.AIComponent` holds one of each (`bi` at +0x58, `bl` at
# +0x60). The int-typed accessors therefore carry BKI keys and the long-typed accessors
# carry BKL keys: two different key spaces that share numbers. That is the whole reason
# `blackboard[62]` (CURRENT_AFFECTED_SKILL_ID) and `longBoard[62]` (SHELF_ENTITY_ID) must
# not collapse into one lookup.
BLACKBOARD_ACCESSORS = {
    0x1a6a108: dict(namespace='BKI', method='get_Item', operation='read', default=True),
    0x1a6a12c: dict(namespace='BKI', method='set_Item', operation='write'),
    0x1a6a1cc: dict(namespace='BKI', method='Get', operation='read', default=True),
    0x1a6a204: dict(namespace='BKI', method='ContainsKey', operation='check'),
    0x1a6a2b8: dict(namespace='BKI', method='Remove', operation='remove'),
    0x1a6a348: dict(namespace='BKL', method='get_Item', operation='read'),
    0x1a6a36c: dict(namespace='BKL', method='set_Item', operation='write'),
    0x1a6a40c: dict(namespace='BKL', method='Get', operation='read', default=True),
}

# `parameter.Param` statics. `id_register` is the register that carries the parameter id
# at the call: the instance-method convention puts it in w1 (`this` in x0), while the one
# static that takes an id as its first argument uses w0. `GetMatchsParams` and the
# `ParamSet` accessors are included because they are the same id space reached by a
# different door.
PARAM_ACCESSORS = {
    0x166070c: dict(name='Param.GetValue', id_register='w1', operation='read'),
    0x16609ec: dict(name='Param.GetMaxValue', id_register='w1', operation='read'),
    0x165e91c: dict(name='Param.GetRate', id_register='w1', operation='read'),
    0x1660ccc: dict(name='Param.SetValue', id_register='w1', operation='write'),
    0x1660de4: dict(name='Param.AddValue', id_register='w1', operation='write'),
    0x1660ef8: dict(name='Param.SubValue', id_register='w1', operation='write'),
    0x1661000: dict(name='Param.Full', id_register='w1', operation='write'),
    0x166107c: dict(name='Param.Get', id_register='w1', operation='read'),
    0x1660630: dict(name='Param.GetMatchsParams', id_register='w0', operation='read'),
    0x1683024: dict(name='ParamSet.get_Item', id_register='w1', operation='read'),
    0x1682ee8: dict(name='ParamSet.Get', id_register='w1', operation='read'),
    0x1682f7c: dict(name='ParamSet.ContainsId', id_register='w1', operation='check'),
    0x1682dd8: dict(name='ParamSet.AddParameter', id_register='w1', operation='write'),
    0x1683710: dict(name='ParamSet.ParamSetBuilder.Add', id_register='w1', operation='write'),
}

# Named types whose const blocks are the official names of a numeric space. Read from the
# frozen dump through the canonical `types` table, so a moved declaration is detected.
NAMED_SPACES = (
    ('BKI', 'ai.BKI', 'enum'),
    ('BKL', 'ai.BKL', 'enum'),
    ('param', 'parameter.Param', 'class'),
    ('skillType', 'data.SkillData', 'class'),
)

CONST_RE = re.compile(r'^\s*public const (\S+) (\w+) = (-?\d+);')
STUB_DECL_RE = re.compile(r"\.stub\(\s*0x([0-9a-fA-F]+)\s*,\s*'([^']+)'")
STUB_HANDLER_RE = re.compile(r'address\s*==\s*0x([0-9a-fA-F]+)')
MODEL_BOARD_INDEX_RE = re.compile(
    r"['\"]?(long_board|longBoard|board)['\"]?\s*\]?\s*\[\s*(\d+)\s*\]")
MODEL_BOARD_LITERAL_RE = re.compile(
    r"['\"]?(long_board|longBoard|board)['\"]?\s*\]?\s*[:=]\s*\{([^}]*)\}")
MODEL_BOARD_KEY_RE = re.compile(r"(\d+)\s*:")


def is_self_path(path_text: str) -> bool:
    return Path(norm(path_text)).name in SELF_FILES


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def norm(text: str) -> str:
    return str(text).replace('\\', '/')


def key_id(namespace: str, value: int) -> str:
    return f'{namespace}:{value}'


# --------------------------------------------------------------------------------------- #
# frozen metadata
# --------------------------------------------------------------------------------------- #


def parse_const_block(dump_path: Path, decl_line: int):
    """Read one type's `public const` members straight out of the frozen dump.

    IL2CPP enum members are not layout fields, so `dump.cs` is the only place the official
    names exist - the canonical `fields` table deliberately holds only `value__` for an
    enum. The block is delimited by brace counting from the declaration line, so a renamed
    or reordered type is read correctly and a moved one is caught by the decl_line check.
    """
    text = dump_path.read_text(encoding='utf-8', errors='replace').splitlines()
    if not (1 <= decl_line <= len(text)):
        return None
    depth = 0
    started = False
    members = []
    for line in text[decl_line - 1:]:
        if '{' in line:
            depth += line.count('{')
            started = True
        if '}' in line:
            depth -= line.count('}')
        match = CONST_RE.match(line)
        if match and not match.group(2).startswith('value__'):
            members.append(dict(token=match.group(1), name=match.group(2),
                               value=int(match.group(3))))
        if started and depth <= 0:
            break
    return members


def load_named_spaces(conn: sqlite3.Connection, dump_path: Path):
    spaces = {}
    for label, type_name, kind in NAMED_SPACES:
        row = conn.execute('SELECT * FROM types WHERE name=?', (type_name,)).fetchone()
        if row is None:
            raise SystemExit(f'the frozen metadata no longer declares {type_name}')
        members = parse_const_block(dump_path, row['decl_line'])
        if not members:
            raise SystemExit(f'no const members parsed for {type_name} at line '
                             f'{row["decl_line"]} of {rel(dump_path)}')
        spaces[label] = dict(label=label, type=type_name, kind=kind,
                             typeDefIndex=row['typedef_index'], declLine=row['decl_line'],
                             members=members,
                             byValue={m['value']: m['name'] for m in members},
                             byName={m['name']: m['value'] for m in members})
        if label == 'skillType':
            # data.SkillData declares three separate constant families in one block. A bare
            # value lookup would answer `1` with FLAG_MULTI instead of TYPE_MAGIC, so the
            # type family gets its own map and the other two are kept apart.
            types = [m for m in members if m['name'].startswith('TYPE_')]
            if not types:
                raise SystemExit('data.SkillData no longer declares TYPE_* constants')
            spaces[label]['typeMembers'] = types
            spaces[label]['typeByValue'] = {m['value']: m['name'] for m in types}
            spaces[label]['categories'] = [m for m in members
                                           if m['name'].startswith('CATEGORY_')]
            spaces[label]['flags'] = [m for m in members if m['name'].startswith('FLAG_')]
            spaces[label]['byValue'] = spaces[label]['typeByValue']
    return spaces


def load_annotations(path: Path):
    if not path.is_file():
        return dict(keys={}, params={}, status={})
    payload = json.loads(path.read_text(encoding='utf-8'))
    if payload.get('schema') != ANNOTATIONS_SCHEMA:
        raise SystemExit(f'{rel(path)} has schema {payload.get("schema")!r}, '
                         f'expected {ANNOTATIONS_SCHEMA!r}')
    for bucket in ('keys', 'params', 'status', 'board'):
        payload.setdefault(bucket, {})
    return payload


# --------------------------------------------------------------------------------------- #
# instruction-level recovery
# --------------------------------------------------------------------------------------- #

CALLER_SAVED = {'x0', 'x1', 'x2', 'x3', 'x4', 'x5', 'x6', 'x7', 'x8', 'x9', 'x10', 'x11',
                'x12', 'x13', 'x14', 'x15', 'x16', 'x17', 'x18', 'x30'}
# Instructions that do not write their first operand even though capstone prints a register
# there: comparisons, branches and stores. Treating `cmp w1, #0` or `cbz w1, ...` as a
# definition of w1 is exactly the sort of silent wrong answer this layer must not produce.
NO_DESTINATION = {
    'cmp', 'cmn', 'tst', 'ccmp', 'ccmn', 'cbz', 'cbnz', 'tbz', 'tbnz', 'b', 'bl', 'br',
    'blr', 'brk', 'ret', 'nop', 'svc',
}
IMMEDIATE_RE = re.compile(r'^#(-?(?:0x[0-9a-fA-F]+|\d+))$')
REGISTER_RE = re.compile(r'^[wx]\d+$')
TRANSFER = ('b', 'ret', 'br', 'brk')


def canonical_register(name: str) -> str:
    """w1 and x1 are the same storage; the key is a fixed 32-bit field in both."""
    name = name.strip().lower()
    if name.startswith('w') and name[1:].isdigit():
        return 'x' + name[1:]
    return name


def immediate(operand: str):
    match = IMMEDIATE_RE.match(operand.strip())
    if not match:
        return None
    token = match.group(1)
    return int(token, 16) if '0x' in token else int(token)


def destination_register(ins) -> str:
    if ins.mnemonic in NO_DESTINATION or ins.mnemonic.startswith('st') \
            or ins.mnemonic.startswith('b.'):
        return None
    operand = ins.op_str.split(',')[0].strip()
    return operand if REGISTER_RE.match(operand) else None


class InstructionView:
    """Just enough of the frozen binary to read the instructions before a call site."""

    def __init__(self, binary: Path, window: int = 80):
        try:
            from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN
            from elftools.elf.elffile import ELFFile
        except ImportError as exc:      # pragma: no cover - environment guard
            raise SystemExit(f'{exc}: run this under the repository .venv python')
        if not binary.is_file():
            raise SystemExit(f'frozen binary missing: {binary}')
        self.blob = binary.read_bytes()
        with binary.open('rb') as handle:
            elf = ELFFile(handle)
            self.segments = [segment for segment in elf.iter_segments()
                             if segment['p_type'] == 'PT_LOAD' and segment['p_flags'] & 1
                             and segment['p_filesz']]
        self.engine = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)
        self.window = window

    def offset_of(self, address: int):
        for segment in self.segments:
            if segment['p_vaddr'] <= address < segment['p_vaddr'] + segment['p_filesz']:
                return segment['p_offset'] + (address - segment['p_vaddr'])
        return None

    def before(self, address: int, floor: int):
        low = max(floor, address - 4 * self.window)
        offset = self.offset_of(low)
        if offset is None:
            return []
        data = self.blob[offset:offset + (address - low)]
        return [ins for ins in self.engine.disasm(data, low) if ins.address < address]

    def resolve(self, address: int, floor: int, register: str):
        """Where does `register` come from at this call site?

        Returns (value, how, derivation). The walk is bounded by the caller's own first
        instruction, never crosses the function start, and stops at the first instruction
        that defines the register being tracked. Because IL2CPP reserves x19-x28, a copy
        chain through a callee-saved register may cross a call; a chain through a
        caller-saved register may not, so that case is reported as clobbered rather than
        resolved to a stale earlier value.
        """
        need = {canonical_register(register)}
        for ins in reversed(self.before(address, floor)):
            mnemonic, operands = ins.mnemonic, ins.op_str
            if mnemonic in ('bl', 'blr', 'blraa', 'blrab') or mnemonic in TRANSFER:
                if (need & CALLER_SAVED) and mnemonic.startswith('bl'):
                    return None, 'clobbered-by-call', f'{hex(ins.address)}: {mnemonic} {operands}'
                if (need & CALLER_SAVED) and mnemonic in TRANSFER:
                    return None, 'control-flow-boundary', f'{hex(ins.address)}: {mnemonic} {operands}'
                continue
            destination = destination_register(ins)
            if destination is None or canonical_register(destination) not in need:
                continue
            parts = [part.strip() for part in operands.split(',')]
            if mnemonic == 'movz' and len(parts) >= 2:
                value = immediate(parts[1])
                if value is not None:
                    return value, 'constant', f'{hex(ins.address)}: movz {operands}'
            elif mnemonic == 'movn' and len(parts) >= 2:
                value = immediate(parts[1])
                if value is not None:
                    return (~value) & 0xffffffff, 'constant', f'{hex(ins.address)}: movn {operands}'
            elif mnemonic == 'mov' and len(parts) >= 2:
                source = canonical_register(parts[1])
                if source in ('wzr', 'xzr'):
                    return 0, 'constant', f'{hex(ins.address)}: mov {operands}'
                value = immediate(parts[1])
                if value is not None:
                    return value & 0xffffffff, 'constant', f'{hex(ins.address)}: mov {operands}'
                if REGISTER_RE.match(source):
                    need = {source}
                    continue
                return None, 'mov-from-unknown', f'{hex(ins.address)}: mov {operands}'
            elif mnemonic == 'orr' and len(parts) >= 3 and parts[1].lower() in ('wzr', 'xzr'):
                value = immediate(parts[2])
                if value is not None:
                    return value & 0xffffffff, 'constant', f'{hex(ins.address)}: orr {operands}'
            reason = 'written-by-' + mnemonic
            return None, reason, f'{hex(ins.address)}: {mnemonic} {operands}'
        return None, 'argument', None


# --------------------------------------------------------------------------------------- #
# index joins
# --------------------------------------------------------------------------------------- #


def open_native(path: Path = NATIVE_DB) -> sqlite3.Connection:
    if not path.is_file():
        raise SystemExit(f'canonical native index missing: {path}\n'
                         'build it first: python KA-Website/tools/recovery/ka_index.py build')
    conn = sqlite3.connect(f'file:{path.as_posix()}?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def load_combat_population() -> dict:
    if not COMBAT_OVERLAY.is_file():
        raise SystemExit(f'combat overlay missing: {COMBAT_OVERLAY}\n'
                         'build it first: python KA-Website/tools/recovery/build_combat_registry.py')
    payload = json.loads(COMBAT_OVERLAY.read_text(encoding='utf-8'))
    rows = {}
    for row in payload['functions']:
        rows[int(row['rva'], 16)] = row
    return rows


def artifact_paths(conn: sqlite3.Connection) -> dict:
    out = {}
    for row in conn.execute('SELECT rva, rel FROM artifacts ORDER BY rel'):
        out.setdefault(row['rva'], []).append(norm(row['rel']))
    return out


def collect_sites(conn: sqlite3.Connection, accessors, population, view: InstructionView,
                  register_of):
    """Every combat-population call site to one of `accessors`, with its recovered number.

    The site list itself comes from the canonical call graph, so this layer cannot invent
    a call the index does not have, and a rebuild after the index changes cannot silently
    keep a stale site.
    """
    extents = {row['rva']: (row['first_instr'], row['last_instr'])
               for row in conn.execute('SELECT * FROM code_map')}
    placeholders = ','.join(str(rva) for rva in sorted(accessors))
    sites = []
    for row in conn.execute(
            f'SELECT site, callee_rva, caller_rva, kind FROM calls '
            f'WHERE callee_rva IN ({placeholders}) ORDER BY site'):
        caller = row['caller_rva']
        if caller not in population:
            continue
        floor = extents.get(caller, (None, None))[0]
        if floor is None:
            floor = row['site'] - 4 * view.window
        value, how, derivation = view.resolve(row['site'], floor, register_of(row['callee_rva']))
        sites.append(dict(site=row['site'], accessor=row['callee_rva'], function=caller,
                          kind=row['kind'], value=value, keyState=how,
                          derivation=derivation, insideExtent=(
                              floor <= row['site'] <= extents.get(caller, (0, 0))[1])))
    return sites


# --------------------------------------------------------------------------------------- #
# board / positional indices
# --------------------------------------------------------------------------------------- #


def scan_model_blackboard_indices(tools: Path):
    """Which numeric indices do the live combat models actually address?

    `combat_*.py` models the AI/blackboard pair directly: `board` is the BKI dictionary and
    `long_board` the BKL one, so a positional index in those modules *is* a key number.
    Recording where each index appears turns "what is board[8]" into a lookup instead of a
    re-derivation, and keeps any index that is not a defined key visible instead of named.
    """
    found = {}
    for path in sorted(tools.glob('*.py')):
        if path.name in SELF_FILES:
            continue
        for number, line in enumerate(
                path.read_text(encoding='utf-8', errors='replace').splitlines(), 1):
            hits = [(match.group(1), match.group(2), match.start())
                    for match in MODEL_BOARD_INDEX_RE.finditer(line)]
            for match in MODEL_BOARD_LITERAL_RE.finditer(line):
                hits += [(match.group(1), key.group(1), match.start())
                         for key in MODEL_BOARD_KEY_RE.finditer(match.group(2))]
            for family, raw, _position in hits:
                space = 'BKL' if family.lower() in ('long_board', 'longboard') else 'BKI'
                found.setdefault((space, int(raw)), []).append(
                    dict(path=rel(path), line=number, text=line.strip()[:160]))
    return found


# --------------------------------------------------------------------------------------- #
# stub catalogue
# --------------------------------------------------------------------------------------- #


def scan_fixture_stubs(tools: Path):
    """Which fixture addresses are stubbed, by which fixture, and how they are handled.

    `declared` records the harness form `machine.stub(address, name)`, which is where a
    fixture states what it *assumes* the target does. `arms` records the manual hook form
    `address == 0x...`, which is how the Unicorn fixtures dispatch. A short sample of the
    handling source is kept per address, so the catalogue states the assumption in the
    fixture's own words instead of paraphrasing it.
    """
    declared, arms, samples = {}, {}, {}
    for path in sorted(tools.glob('*.py')):
        if path.name in SELF_FILES:
            continue
        relative = rel(path)
        for number, line in enumerate(
                path.read_text(encoding='utf-8', errors='replace').splitlines(), 1):
            for match in STUB_DECL_RE.finditer(line):
                declared.setdefault(int(match.group(1), 16), {}).setdefault(
                    match.group(2), set()).add(relative)
                samples.setdefault(int(match.group(1), 16), []).append(
                    dict(path=relative, line=number, text=line.strip()[:150]))
            for match in STUB_HANDLER_RE.finditer(line):
                address = int(match.group(1), 16)
                arms.setdefault(address, {}).setdefault(relative, 0)
                arms[address][relative] += 1
                samples.setdefault(address, []).append(
                    dict(path=relative, line=number, text=line.strip()[:150]))
    return declared, arms, samples


# --------------------------------------------------------------------------------------- #
# build
# --------------------------------------------------------------------------------------- #


def build(root: Path = ROOT, keys_path: Path = KEYS_PATH):
    dump_path = root / FROZEN_METADATA.relative_to(ROOT)
    conn = open_native(root / NATIVE_DB.relative_to(ROOT))
    manifest = json.loads((root / NATIVE_MANIFEST.relative_to(ROOT)).read_text(encoding='utf-8'))
    overlay_rows = load_combat_population()
    artifacts = artifact_paths(conn)
    spaces = load_named_spaces(conn, dump_path)
    annotations = load_annotations(root / ANNOTATIONS_PATH.relative_to(ROOT))
    tools = root / TOOLS.relative_to(ROOT)
    view = InstructionView(root / 'RE-evidence/G2.1/39257e72291d/inputs/libil2cpp.so')

    blackboard_sites = collect_sites(conn, BLACKBOARD_ACCESSORS, overlay_rows, view,
                                     lambda rva: 'w1')
    param_sites = collect_sites(conn, PARAM_ACCESSORS, overlay_rows, view,
                                lambda rva: PARAM_ACCESSORS[rva]['id_register'])

    # --- keys ------------------------------------------------------------------------ #
    rows = {}
    for site in blackboard_sites:
        accessor = BLACKBOARD_ACCESSORS[site['accessor']]
        namespace = accessor['namespace']
        entry = rows.get((namespace, site['value']))
        if entry is None and site['value'] is not None:
            entry = rows.setdefault((namespace, site['value']), dict(
                namespace=namespace, value=site['value'], operations={}, functions=set(),
                sites=[]))
        if site['value'] is None:
            continue
        entry['operations'][accessor['operation']] = \
            entry['operations'].get(accessor['operation'], 0) + 1
        entry['functions'].add(site['function'])
        entry['sites'].append(site)

    keys = []
    for (namespace, value), entry in sorted(rows.items()):
        space = spaces[namespace]
        official = space['byValue'].get(value)
        annotation = annotations['keys'].get(key_id(namespace, value), {})
        functions = sorted(entry['functions'])
        checks, models, tracks = set(), set(), set()
        for rva in functions:
            row = overlay_rows.get(rva) or {}
            tracks.update(row.get('tracks') or [])
            checks.update(norm(p) for p in row.get('relatedChecks') or [])
            for model in (row.get('relatedModels') or []) + \
                    ((row.get('annotation') or {}).get('relatedModels') or []):
                models.add(norm(model))
        checks.update(annotation.get('relatedChecks') or [])
        models.update(annotation.get('relatedModels') or [])
        keys.append(dict(
            key=key_id(namespace, value), namespace=namespace, value=value,
            officialName=official, officialNameDefined=official is not None,
            recoveredRole=annotation.get('role'),
            confidence=annotation.get('confidence') or 'unknown',
            evidence=list(annotation.get('evidence') or []),
            sites=len(entry['sites']),
            operations=dict(sorted(entry['operations'].items())),
            functions=[hex(rva) for rva in functions],
            relatedChecks=sorted(c for c in checks if not is_self_path(c)),
            relatedModels=sorted(models),
            tracks=sorted(tracks)))

    # --- parameters ------------------------------------------------------------------ #
    param_rows = {}
    for site in param_sites:
        accessor = PARAM_ACCESSORS[site['accessor']]
        if site['value'] is None:
            continue
        entry = param_rows.setdefault(site['value'], dict(
            value=site['value'], operations={}, accessors=set(), functions=set(), sites=[]))
        entry['operations'][accessor['operation']] = \
            entry['operations'].get(accessor['operation'], 0) + 1
        entry['accessors'].add(accessor['name'])
        entry['functions'].add(site['function'])
        entry['sites'].append(site)
    params = []
    for value, entry in sorted(param_rows.items()):
        annotation = annotations['params'].get(str(value), {})
        checks, models, tracks = set(), set(), set()
        for rva in sorted(entry['functions']):
            row = overlay_rows.get(rva) or {}
            tracks.update(row.get('tracks') or [])
            checks.update(norm(p) for p in row.get('relatedChecks') or [])
            for model in (row.get('relatedModels') or []) + \
                    ((row.get('annotation') or {}).get('relatedModels') or []):
                models.add(norm(model))
        checks.update(annotation.get('relatedChecks') or [])
        models.update(annotation.get('relatedModels') or [])
        params.append(dict(
            value=value, officialName=spaces['param']['byValue'].get(value),
            officialNameDefined=value in spaces['param']['byValue'],
            recoveredRole=annotation.get('role'),
            confidence=annotation.get('confidence') or 'unknown',
            evidence=list(annotation.get('evidence') or []),
            sites=len(entry['sites']), operations=dict(sorted(entry['operations'].items())),
            accessors=sorted(entry['accessors']),
            functions=[hex(rva) for rva in sorted(entry['functions'])],
            relatedChecks=sorted(c for c in checks if not is_self_path(c)),
            relatedModels=sorted(models),
            tracks=sorted(tracks)))

    # --- status / skill-type space ---------------------------------------------------- #
    status = []
    for annotation_key, annotation in sorted(annotations['status'].items()):
        namespace, _, raw = annotation_key.partition(':')
        try:
            value = int(raw, 0)
        except ValueError:
            raise SystemExit(f'status annotation key {annotation_key!r} is not <space>:<value>')
        if namespace.startswith('skillType'):
            official = spaces['skillType']['byValue'].get(value)
        elif namespace in ('BKI', 'BKL'):
            official = spaces[namespace]['byValue'].get(value)
        else:
            official = None
        status.append(dict(
            entry=annotation_key, namespace=namespace, value=value,
            officialName=official, officialNameDefined=official is not None,
            recoveredRole=annotation.get('role'), confidence=annotation.get('confidence'),
            evidence=list(annotation.get('evidence') or []),
            relatedKeys=list(annotation.get('relatedKeys') or []),
            readers=[hex(rva) for rva in annotation.get('readers') or []],
            writers=[hex(rva) for rva in annotation.get('writers') or []],
            relatedChecks=list(annotation.get('relatedChecks') or []),
            relatedModels=list(annotation.get('relatedModels') or []),
            unresolved=list(annotation.get('unresolved') or [])))

    # --- board / positional indices --------------------------------------------------- #
    model_indices = scan_model_blackboard_indices(tools)
    keys_by_id = {entry['key']: entry for entry in keys}
    sites_by_key = {}
    for site in blackboard_sites:
        if site['value'] is None:
            continue
        sites_by_key.setdefault(key_id(BLACKBOARD_ACCESSORS[site['accessor']]['namespace'],
                                       site['value']), []).append(site)
    board = []
    for (namespace, index), places in sorted(model_indices.items()):
        official = spaces[namespace]['byValue'].get(index)
        # A board index *is* a key: `combat_*.py`'s `board` dictionary is the BKI blackboard
        # and `long_board` the BKL one. Its role is therefore the same role the key has,
        # unless the board bucket says something index-specific (a sentinel, say).
        annotation = annotations['board'].get(key_id(namespace, index)) \
            or annotations['keys'].get(key_id(namespace, index), {})
        # Reference the native side of the same number instead of copying a second meaning:
        # the board block answers "where does the live model use this index", the key entry
        # answers "which native routines read and write it".
        identifier = key_id(namespace, index)
        native = keys_by_id.get(identifier, {})
        native_sites = sites_by_key.get(identifier, [])
        board.append(dict(
            key=identifier, namespace=namespace, index=index,
            officialName=official, officialNameDefined=official is not None,
            recoveredRole=annotation.get('role'), confidence=annotation.get('confidence'),
            evidence=list(annotation.get('evidence') or []),
            usedBy=[f'{p["path"]}:{p["line"]}' for p in places],
            nativeKey=native.get('key') if native else None,
            nativeFunctions=list(native.get('functions') or []),
            operations=dict(native.get('operations') or {}),
            readers=[f'{hex(s["site"])} {BLACKBOARD_ACCESSORS[s["accessor"]]["method"]}'
                     for s in native_sites
                     if BLACKBOARD_ACCESSORS[s['accessor']]['operation'] in ('read', 'check')][:8],
            writers=[f'{hex(s["site"])} {BLACKBOARD_ACCESSORS[s["accessor"]]["method"]}'
                     for s in native_sites
                     if BLACKBOARD_ACCESSORS[s['accessor']]['operation'] in ('write',
                                                                             'remove')][:8],
            relatedChecks=list(annotation.get('relatedChecks') or []),
            usedByFiles=sorted({p['path'] for p in places})))

    # --- stub catalogue --------------------------------------------------------------- #
    declared, arms, samples = scan_fixture_stubs(tools)
    method_names, helper_rows = {}, {}
    for row in conn.execute('SELECT rva, name FROM methods'):
        method_names[row['rva']] = row['name']
    for row in conn.execute('SELECT rva, verdict, confidence FROM helper_findings'):
        helper_rows[row['rva']] = row
    windows = {row['rva'] for row in conn.execute('SELECT rva FROM code_windows')}

    def region_of(address):
        for row in conn.execute('SELECT * FROM regions'):
            if row['vaddr'] <= address < row['vaddr'] + row['memsz']:
                return 'code' if row['executable'] else 'data'
        return 'outside the mapped image'

    stubs = []
    for address in sorted(set(declared) | set(arms)):
        helper = helper_rows.get(address)
        if address in method_names:
            classification = 'managed-method'
        elif helper is not None:
            classification = 'documented-native-helper'
        elif address in windows:
            classification = 'artifact-window'
        elif region_of(address) == 'outside the mapped image':
            classification = 'fixture-address'
        else:
            classification = 'unidentified-native-address'
        stubs.append(dict(
            address=hex(address),
            fixtureNames=sorted(declared.get(address, {})),
            declaredBy=sorted({path for paths in declared.get(address, {}).values()
                               for path in paths}),
            handlerArms=sum(arms.get(address, {}).values()),
            handlerFiles=sorted(arms.get(address, {})),
            managedName=method_names.get(address),
            recordedVerdict=(helper['verdict'] if helper else None),
            recordedConfidence=(helper['confidence'] if helper else None),
            classification=classification,
            assumedHandling=(samples.get(address) or [])[:2],
            incomingDirectCalls=conn.execute(
                'SELECT COUNT(*) FROM calls WHERE callee_rva=?', (address,)).fetchone()[0],
            citations=conn.execute(
                'SELECT COUNT(*) FROM mentions WHERE rva=?', (address,)).fetchone()[0]))

    # --- payload ---------------------------------------------------------------------- #
    payload = dict(
        schema=SCHEMA,
        generatedUtc=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        nativeIndex=dict(database=rel(root / NATIVE_DB.relative_to(ROOT)),
                         binarySha256=manifest.get('binarySha256'),
                         contentDigest=manifest.get('contentDigest'),
                         sourceSetDigest=manifest.get('sourceSetDigest'),
                         indexVersion=manifest.get('indexVersion'),
                         counts=manifest.get('counts')),
        frozenMetadata=dict(dump=rel(dump_path),
                            sha256=hashlib_sha256(dump_path)),
        keySpaces={
            'BKI': dict(enumType=spaces['BKI']['type'],
                        typeDefIndex=spaces['BKI']['typeDefIndex'],
                        declLine=spaces['BKI']['declLine'], valueType='int',
                        host='kairo.unity.ai.BlackboardInt : Blackboard<BKI, int>',
                        holder='ecs.AIComponent.bi (+0x58)',
                        accessors={hex(rva): dict(method=row['method'],
                                                  operation=row['operation'],
                                                  defaultInstance=bool(row.get('default')))
                                   for rva, row in sorted(BLACKBOARD_ACCESSORS.items())
                                   if row['namespace'] == 'BKI'}),
            'BKL': dict(enumType=spaces['BKL']['type'],
                        typeDefIndex=spaces['BKL']['typeDefIndex'],
                        declLine=spaces['BKL']['declLine'], valueType='long',
                        host='kairo.unity.ai.BlackboardLong : Blackboard<BKL, long>',
                        holder='ecs.AIComponent.bl (+0x60)',
                        accessors={hex(rva): dict(method=row['method'],
                                                  operation=row['operation'],
                                                  defaultInstance=bool(row.get('default')))
                                   for rva, row in sorted(BLACKBOARD_ACCESSORS.items())
                                   if row['namespace'] == 'BKL'}),
        },
        paramSpace=dict(type=spaces['param']['type'],
                        typeDefIndex=spaces['param']['typeDefIndex'],
                        declLine=spaces['param']['declLine'],
                        constants=[dict(name=m['name'], value=m['value'])
                                   for m in spaces['param']['members']],
                        accessors={hex(rva): dict(row) for rva, row in sorted(PARAM_ACCESSORS.items())}),
        skillTypeSpace=dict(type=spaces['skillType']['type'],
                            typeDefIndex=spaces['skillType']['typeDefIndex'],
                            declLine=spaces['skillType']['declLine'],
                            constants=[dict(name=m['name'], value=m['value'])
                                       for m in spaces['skillType']['typeMembers']],
                            categories=[dict(name=m['name'], value=m['value'])
                                        for m in spaces['skillType']['categories']],
                            flags=[dict(name=m['name'], value=m['value'])
                                   for m in spaces['skillType']['flags']]),
        keys=keys, params=params, statusConstants=status, boardIndices=board, stubs=stubs,
        sites=[dict(site=hex(s['site']), accessor=hex(s['accessor']),
                    accessorMethod=BLACKBOARD_ACCESSORS[s['accessor']]['method'],
                    namespace=BLACKBOARD_ACCESSORS[s['accessor']]['namespace'],
                    operation=BLACKBOARD_ACCESSORS[s['accessor']]['operation'],
                    kind='tail-b' if s['kind'] == 'branch' else 'bl',
                    key=s['value'], keyState=('constant' if s['value'] is not None
                                              else 'dynamic'),
                    keyDerivation=s['derivation'], function=hex(s['function']),
                    artifacts=artifacts.get(s['function'], [])[:2]) for s in blackboard_sites],
        paramSites=[dict(site=hex(s['site']), accessor=hex(s['accessor']),
                         accessorName=PARAM_ACCESSORS[s['accessor']]['name'],
                         operation=PARAM_ACCESSORS[s['accessor']]['operation'],
                         kind='tail-b' if s['kind'] == 'branch' else 'bl',
                         paramId=s['value'], idState=('constant' if s['value'] is not None
                                                      else 'dynamic'),
                         idDerivation=s['derivation'], function=hex(s['function']),
                         artifacts=artifacts.get(s['function'], [])[:2]) for s in param_sites],
        counts=dict(
            blackboardSites=len(blackboard_sites),
            blackboardSitesResolved=sum(1 for s in blackboard_sites if s['value'] is not None),
            blackboardFunctions=len({s['function'] for s in blackboard_sites}),
            blackboardSitesWithListing=sum(1 for s in blackboard_sites
                                           if artifacts.get(s['function'])),
            blackboardFunctionsWithListing=len({s['function'] for s in blackboard_sites
                                                if artifacts.get(s['function'])}),
            paramSites=len(param_sites),
            paramSitesResolved=sum(1 for s in param_sites if s['value'] is not None),
            paramFunctions=len({s['function'] for s in param_sites}),
            paramFunctionsWithListing=len({s['function'] for s in param_sites
                                           if artifacts.get(s['function'])}),
            keys=len(keys), params=len(params), boardIndices=len(board), stubs=len(stubs),
            stubsDeclared=sum(1 for s in stubs if s['fixtureNames'])),
        populationRule=dict(
            description='a site is recorded when its containing function is in the combat '
                        'overlay population; the accessor run itself is read from the '
                        'canonical direct call graph',
            combatPopulation=len(overlay_rows),
            blackboardSitesInImage=conn.execute(
                'SELECT COUNT(*) FROM calls WHERE callee_rva IN (%s)'
                % ','.join(str(r) for r in sorted(BLACKBOARD_ACCESSORS))).fetchone()[0],
            paramSitesInImage=conn.execute(
                'SELECT COUNT(*) FROM calls WHERE callee_rva IN (%s)'
                % ','.join(str(r) for r in sorted(PARAM_ACCESSORS))).fetchone()[0]),
        limitations=[
            'A key is recovered only from the instruction that defines the register at '
            'the call site. A value produced by a load, a computed expression or a '
            'caller-supplied argument is stored as dynamic with the reason and is never '
            'given a name.',
            'Indirect dispatch (BLR/BR, virtual, interface, delegate) is not resolved by '
            'the canonical sweep, so a blackboard access reached only that way is absent '
            'rather than guessed.',
            'The int/long accessor split is the BKI/BKL split because dump.cs declares '
            'BlackboardInt : Blackboard<BKI,int> and BlackboardLong : Blackboard<BKL,long>; '
            'the two spaces share numbers and must stay distinguishable.',
            'A recovered role is quoted from current evidence. The official enum name is '
            'metadata, not a claim about behaviour, and no role is inferred from a name.'])

    report = dict(
        schema='ka-combat-keys-report-1',
        generatedUtc=payload['generatedUtc'],
        nativeIndex=payload['nativeIndex'],
        counts=payload['counts'],
        keySitesByNamespace={
            namespace: sum(entry['sites'] for entry in keys if entry['namespace'] == namespace)
            for namespace in ('BKI', 'BKL')},
        dynamicSites=[dict(site=hex(s['site']), accessorName=BLACKBOARD_ACCESSORS
                           [s['accessor']]['method'], namespace=BLACKBOARD_ACCESSORS
                           [s['accessor']]['namespace'], function=hex(s['function']),
                           reason=s['keyState'], derivation=s['derivation'])
                      for s in blackboard_sites if s['value'] is None],
        dynamicParamSites=[dict(site=hex(s['site']), accessorName=PARAM_ACCESSORS
                                [s['accessor']]['name'], function=hex(s['function']),
                                reason=s['keyState'], derivation=s['derivation'])
                           for s in param_sites if s['value'] is None],
        stubCensus=dict(distinct=len(stubs),
                        recurring=sum(1 for s in stubs if s['handlerArms'] >= 2),
                        declared=sum(1 for s in stubs if s['fixtureNames'])),
    )
    report['stableDigest'] = canonical_hash(stable_projection(payload))
    payload['stableDigest'] = report['stableDigest']
    return dict(keys=payload, report=report)


def hashlib_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stable_projection(payload: dict) -> dict:
    """The part of the payload a rebuild must reproduce exactly."""
    return {key: value for key, value in payload.items()
            if key not in ('generatedUtc', 'stableDigest')}


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--verify', action='store_true',
                        help='rebuild in memory and compare with the stored overlay')
    parser.add_argument('--out', type=Path, default=KEYS_PATH)
    args = parser.parse_args()

    first = build()
    if args.verify:
        stored = json.loads(args.out.read_text(encoding='utf-8'))
        if canonical_hash(stable_projection(stored)) != canonical_hash(
                stable_projection(first['keys'])):
            raise SystemExit('the stored combat-keys overlay is stale - rerun '
                             'recover_combat_keys.py')
        print(json.dumps(dict(verified=True, counts=first['keys']['counts'],
                              stableDigest=first['report']['stableDigest'])))
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(first['keys'], indent=1) + '\n', encoding='utf-8')
    REPORT_PATH.write_text(json.dumps(first['report'], indent=1) + '\n', encoding='utf-8')
    print(json.dumps(dict(counts=first['keys']['counts'],
                          registry=rel(args.out), report=rel(REPORT_PATH),
                          dynamicBlackboardSites=len(first['report']['dynamicSites']),
                          dynamicParamSites=len(first['report']['dynamicParamSites']),
                          stableDigest=first['report']['stableDigest']), indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
