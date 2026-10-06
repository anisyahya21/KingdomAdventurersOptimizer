"""Build the combat overlay on top of the canonical native index.

The native index (`ka_index.py` -> `RE-evidence/20260920-native-index/build/ka-index.sqlite`)
owns every native fact: method identity, signature, declaring type, extent, artifacts,
citations, the direct call graph and binary provenance. This builder does **not** re-derive
any of that. It reads the index and adds only what the combat work owns:

  * which native entities are relevant to combat (the population rule below);
  * the combat track/provenance of each one;
  * established purpose, confidence and evidence (hand-written in
    `registry-annotations.json`; the generator never writes that file);
  * related combat checks and combat model modules;
  * combat-specific unresolved questions and conflict/ambiguity information.

The overlay pins the native index's content digest, so a fresh agent - or the check in the
combat gate - can tell whether the overlay was generated from the index that is on disk.

    .venv\\Scripts\\python.exe build_combat_registry.py            # build the overlay
    .venv\\Scripts\\python.exe build_combat_registry.py --verify   # rebuild in memory and compare

Query it with `combat_native.py`; the regression is `check_combat_registry.py`.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from fnmatch import fnmatch
from datetime import datetime, timezone
from pathlib import Path

from combat_run_manifest import NATIVE_SHA256, canonical_hash

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parents[2]
EVIDENCE = ROOT / 'RE-evidence'
NATIVE_BUILD = EVIDENCE / '20260920-native-index/build'
NATIVE_DB = NATIVE_BUILD / 'ka-index.sqlite'
NATIVE_MANIFEST = NATIVE_BUILD / 'index-manifest.json'
OUT_DIR = EVIDENCE / '20260919-combat-registry'
REGISTRY_PATH = OUT_DIR / 'combat-registry.json'
ANNOTATIONS_PATH = OUT_DIR / 'registry-annotations.json'
REPORT_PATH = OUT_DIR / 'registry-build-report.json'

SCHEMA = 'ka-combat-overlay-2'
ANNOTATIONS_SCHEMA = 'ka-combat-native-annotations-1'
# Tooling about the overlay rather than a combat rule: never a related check or model.
INFRASTRUCTURE_FILES = ('combat_native.py', 'build_combat_registry.py', 'check_combat_registry.py',
                        'check_combat_native_layer.py')
DISASSEMBLY_NAME_RE = re.compile(r'^(0[0-9a-f]{5,7})[-_](.+)$')

# Which indexed sources make a native entity *combat-relevant*. The distinction matters:
# a hand-written document or a combat tool cites a function on purpose, while a generated
# disassembly listing mentions every operand address it contains. Only the former may add
# a function to the overlay; the latter stays available as evidence for a function that is
# already in it.
COMBAT_TRACK_PREFIXES = (
    'RE-evidence/20260912-combat/',
    'RE-evidence/20260914-b3-receipt/',
    'RE-evidence/20260918-battle-visual-replay/',
    'RE-evidence/20260919-human-battle-animation/',
    'RE-evidence/20260919-battle-hud/',
    'RE-evidence/20260917-map-monster-spawn/',
    'RE-evidence/20260917-legendary-cave-selection/',
    'RE-evidence/20260917-treasure-source-graph/',
)
COMBAT_DOC_FILES = (
    'KA-Website/docs/reverse-engineering/CURRENT.md',
    'KA-Website/docs/reverse-engineering/special-combat.md',
    'KA-Website/docs/reverse-engineering/combat-sandbox.md',
    'KA-Website/docs/reverse-engineering/state.json',
    'KA-Website/docs/reverse-engineering/HANDOFF-b3-treasure-receipt.md',
    'KA-Website/docs/reverse-engineering/treasure-receipt-addtreasure-countstock-20260914-0144.md',
    'KA-Website/docs/reverse-engineering/treasure-receipt-createtreasure-20260914-0100.md',
    'KA-Website/docs/reverse-engineering/rng-seed-provenance-20260914-0037.md',
    'KA-Website/docs/reverse-engineering/surround-effects.md',
    'KA-Website/docs/reverse-engineering/combat-community-observations.md',
    'KA-Website/docs/reverse-engineering/treasure-lookup.md',
)
# Structured extraction artifacts that carry function identity on purpose.
# Combat-recovery tools only: the same scope the overlay used before the consolidation.
COMBAT_TOOL_PATTERNS = (
    'combat_*.py', 'check_combat_*.py', 'recover_special_combat.py',
    'recover_combat_animation_resources.py', 'export_battle_animation.py',
    'disassemble_*.py', 'slice_*.py', 'resolve_*.py', 'dump_type.py',
    'lookup_metadata_slots.py', 'prepare_ghidra_combat.py',
)
CURATED_FACT_FILES = (
    'RE-evidence/20260912-combat/encounters.json',
    'RE-evidence/20260912-combat/enemy-start-baselines.json',
    'RE-evidence/20260912-combat/monster-creation-samples.json',
    'RE-evidence/20260912-combat/weapon-skill-profiles.json',
    'RE-evidence/20260912-combat/enemy-combat-roles.json',
    'RE-evidence/20260912-combat/animation-resources.json',
    'RE-evidence/20260912-combat/skill-combat-constants.json',
    'RE-evidence/20260912-combat/formation-rules.json',
    'RE-evidence/20260912-combat/stat-rng-callers.json',
    'RE-evidence/20260912-combat/event-runtime-slices.json',
    'RE-evidence/20260914-b3-receipt/resolved-symbols.json',
    'RE-evidence/20260914-b3-receipt/metadata-slots.json',
    'RE-evidence/20260914-b3-receipt/b3-receipt-slices.json',
    'RE-evidence/20260919-battle-hud/deepseek/callers.json',
)
# Non-combat material inside the combat folders is still indexed; it just never *adds*
# a function to the overlay (for example the render-depth work reached this way).
TRACK_RULES = (
    ('render-depth', ('zsort', 'DEPTH-')),
    ('battle-animation', ('20260919-human-battle-animation',)),
    ('battle-hud', ('20260919-battle-hud',)),
    ('battle-visual-replay', ('20260918-battle-visual-replay',)),
    ('b3-treasure-receipt', ('20260914-b3-receipt', 'treasure-receipt-',
                             'HANDOFF-b3-treasure-receipt')),
    ('map-monster-spawn', ('20260917-map-monster-spawn',)),
    ('legendary-cave', ('20260917-legendary-cave-selection',)),
    ('treasure-source-graph', ('20260917-treasure-source-graph',)),
    ('pass1-combat', ('20260912-combat',)),
    ('combat-tools', ('KA-Website/tools/recovery/',)),
    ('shipped-native-facts', ('/src/lib/native-', '/src/game-data/native-', 'battle-animation.json',
                              'human-battle-idle.json')),
    ('combat-log', ('/docs/reverse-engineering/',)),
)


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def track_of(path_text: str) -> str:
    path_text = path_text.replace('\\', '/')
    for label, markers in TRACK_RULES:
        if any(marker in path_text for marker in markers):
            return label
    return 'other'


def is_population_source(kind: str, path_text: str) -> bool:
    """Does this citation mark a native entity as combat-relevant?"""
    path_text = norm(path_text)
    if kind == 'tool':
        if not path_text.startswith('KA-Website/tools/recovery/'):
            return False
        name = path_text.rsplit('/', 1)[-1]
        return any(fnmatch(name, pattern) for pattern in COMBAT_TOOL_PATTERNS)
    if kind == 'doc':
        return path_text in COMBAT_DOC_FILES
    if kind == 'evidence':
        if path_text in CURATED_FACT_FILES:
            return True
        return (path_text.endswith('.md')
                and any(path_text.startswith(prefix) for prefix in COMBAT_TRACK_PREFIXES))
    return False


def norm(path_text: str) -> str:
    """The index stores Windows-style relative paths; the overlay reasons in posix form."""
    return str(path_text).replace('\\', '/')


class NativeIndex:
    """Read-only adapter over the canonical index. No native fact is derived here."""

    def __init__(self, db_path: Path = NATIVE_DB, manifest_path: Path = NATIVE_MANIFEST):
        if not db_path.is_file():
            raise SystemExit(f'canonical native index missing: {db_path}\n'
                             'build it first: python KA-Website/tools/recovery/ka_index.py build')
        self.path = db_path
        self.uri = f'file:{db_path.as_posix()}?mode=ro'
        self.conn = sqlite3.connect(self.uri, uri=True)
        self.conn.row_factory = sqlite3.Row
        self.manifest = (json.loads(manifest_path.read_text(encoding='utf-8'))
                         if manifest_path.is_file() else {})
        self.methods = {row['rva']: row for row in self.conn.execute('SELECT * FROM methods')}
        self.code_map = {row['rva']: row for row in self.conn.execute('SELECT * FROM code_map')}
        self.windows = {row['rva']: row for row in self.conn.execute('SELECT * FROM code_windows')}
        self.audit = {row['rva']: row for row in self.conn.execute('SELECT * FROM audit_identity')}
        self.helpers = {row['rva']: row for row in self.conn.execute('SELECT * FROM helper_findings')}

    # --- provenance -------------------------------------------------------------------
    def binary_sha256(self) -> str:
        row = self.conn.execute("SELECT value FROM meta WHERE key='binary_sha256'").fetchone()
        return row['value'] if row else None

    def verify_binary(self) -> str:
        recorded = self.binary_sha256()
        if recorded != NATIVE_SHA256:
            raise SystemExit(f'the native index records binary {recorded}, expected {NATIVE_SHA256}')
        return recorded

    def identity(self) -> dict:
        return dict(database=rel(self.path),
                    binarySha256=self.binary_sha256(),
                    contentDigest=self.manifest.get('contentDigest'),
                    sourceSetDigest=self.manifest.get('sourceSetDigest'),
                    indexVersion=self.manifest.get('indexVersion'),
                    generatedUtc=self.manifest.get('generatedUtc'),
                    counts=self.manifest.get('counts'))

    # --- native lookups ---------------------------------------------------------------
    def is_method(self, rva: int) -> bool:
        return rva in self.methods

    def name(self, rva: int):
        row = self.methods.get(rva)
        return row['name'] if row else None

    def owner(self, rva: int):
        row = self.methods.get(rva)
        return row['owner'] if row else None

    def extent(self, rva: int):
        audited = self.audit.get(rva)
        code = self.code_map.get(rva)
        if audited and audited['end_rva']:
            return dict(end=hex(audited['end_rva']), source='audit-index',
                        instrs=code['instrs'] if code else None)
        if code:
            return dict(end=hex(code['end_rva']), source='next-metadata-address',
                        instrs=code['instrs'])
        return None

    def artifacts(self, rva: int) -> list:
        return [dict(kind=row['kind'], path=row['rel']) for row in
                self.conn.execute('SELECT kind, rel FROM artifacts WHERE rva=? ORDER BY kind, rel',
                                  (rva,))]

    def mentions(self, rva: int, kinds=None, how=None) -> list:
        sql = 'SELECT how, kind, rel, line, heading, snippet FROM mentions WHERE rva=?'
        params = [rva]
        if kinds:
            sql += f" AND kind IN ({','.join('?' * len(kinds))})"
            params.extend(kinds)
        if how:
            sql += ' AND how=?'
            params.append(how)
        return [dict(row) for row in self.conn.execute(sql + ' ORDER BY kind, rel, line', params)]

    def window(self, rva: int):
        row = self.windows.get(rva)
        if not row:
            return None
        return dict(name=row['name'], kind=row['kind'], manifest=row['manifest'],
                    instrs=row['instrs'], sha256=row['sha256'], end=row['end_rva'],
                    insideMethod=row['inside_method'], insideMethodName=self.name(row['inside_method'])
                    if row['inside_method'] else None, note=row['note'])

    def helper(self, rva: int):
        row = self.helpers.get(rva)
        return dict(row) if row else None

    def out_edges(self, rva: int) -> list:
        return [dict(row) for row in self.conn.execute(
            'SELECT site, caller_rva, callee_rva, kind, source_kind, target_kind FROM calls '
            'WHERE caller_rva=? ORDER BY site', (rva,))]

    def in_edges(self, rva: int) -> list:
        return [dict(row) for row in self.conn.execute(
            'SELECT site, caller_rva, callee_rva, kind, source_kind, target_kind FROM calls '
            'WHERE callee_rva=? ORDER BY site', (rva,))]

    def edge_counts(self, rva: int) -> tuple:
        out_rows = self.conn.execute('SELECT COUNT(*) FROM calls WHERE caller_rva=?', (rva,)).fetchone()[0]
        in_rows = self.conn.execute('SELECT COUNT(*) FROM calls WHERE callee_rva=?', (rva,)).fetchone()[0]
        return in_rows, out_rows

    def cited_rvas(self, limit_prefixes=None) -> dict:
        """RVA -> [mention rows] for combat sources, address mentions only."""
        result = {}
        for row in self.conn.execute(
                "SELECT rva, kind, rel, line, heading, snippet FROM mentions "
                "WHERE how='address' ORDER BY rva, kind, rel, line"):
            if not is_population_source(row['kind'], row['rel']):
                continue
            result.setdefault(row['rva'], []).append(dict(row, rel=norm(row['rel'])))
        return result

    def named_rvas(self) -> dict:
        """RVA -> [mention rows] reached through an identifier rather than a literal address."""
        result = {}
        for row in self.conn.execute(
                "SELECT rva, kind, rel, line, heading, snippet FROM mentions "
                "WHERE how='name' ORDER BY rva, rel, line"):
            if not is_population_source(row['kind'], row['rel']):
                continue
            result.setdefault(row['rva'], []).append(dict(row, rel=norm(row['rel'])))
        return result

    def disassembly_dump_rvas(self) -> dict:
        """RVA -> [path] from index file names of the form `<rva>-<name>.asm`."""
        found = {}
        for row in self.conn.execute("SELECT rel FROM files WHERE kind IN ('dump','evidence')"):
            path = norm(row['rel'])
            if '/disassembly/' not in path and '/zsort-decompile/' not in path:
                continue
            if not any(path.startswith(prefix) for prefix in COMBAT_TRACK_PREFIXES):
                continue
            stem = Path(path).stem
            match = DISASSEMBLY_NAME_RE.match(stem)
            if not match:
                continue
            found.setdefault(int(match.group(1), 16), []).append(path)
        return found


def load_slice_manifests() -> list:
    manifests = []
    for path in sorted(EVIDENCE.rglob('slices.json')):
        text = rel(path)
        if text.startswith('RE-evidence/20260920-native-index/'):
            continue
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            # Another writer may be mid-way through this file; the overlay can be rebuilt.
            print(f'warning: unreadable slice manifest skipped: {text}', file=sys.stderr)
            continue
        if not isinstance(payload, dict) or 'slices' not in payload:
            print(f'warning: slice manifest without a "slices" list skipped: {text}',
                  file=sys.stderr)
            continue
        manifests.append(dict(path=path, rel=text, binary=payload.get('binary'),
                              slices=payload['slices']))
    return manifests


def build(verbose: bool = False) -> dict:
    index = NativeIndex()
    binary_hash = index.verify_binary()
    annotations_doc = None
    if ANNOTATIONS_PATH.is_file():
        annotations_doc = json.loads(ANNOTATIONS_PATH.read_text(encoding='utf-8'))
        if annotations_doc.get('schema') != ANNOTATIONS_SCHEMA:
            raise SystemExit(f'unexpected annotations schema in {rel(ANNOTATIONS_PATH)}')
    manifests = load_slice_manifests()
    cited = index.cited_rvas()
    named = index.named_rvas()
    dumps = index.disassembly_dump_rvas()
    if verbose:
        print(f'native index: {len(index.methods)} methods, {len(index.windows)} code windows, '
              f'{len(manifests)} slice manifests, {len(cited)} combat-cited addresses')

    records: dict = {}
    conflicts = dict(nameConflicts=[], orphanAnnotations=[], citedWithoutIdentity=[],
                     sliceBinaryConflicts=[])

    def record(rva: int) -> dict:
        entry = records.get(rva)
        if entry is None:
            window = index.window(rva)
            entry = dict(rva=hex(rva), name=index.name(rva) or (window or {}).get('name'),
                         kind='method' if index.is_method(rva) else (
                             'code-window' if window else 'unidentified'),
                         declaringClass=index.owner(rva),
                         firstPass=rva in index.audit, tracks=[], sources=[],
                         nameSources=[], annotation=None)
            records[rva] = entry
        return entry

    # 1. the pass-1 audit set
    for rva, row in index.audit.items():
        entry = record(rva)
        entry['tracks'].append('pass1-combat')
        entry['sources'].append(dict(kind='audit-identity', path=row['source']))

    # 2. every slice manifest under RE-evidence (structured, hash-backed)
    for manifest in manifests:
        if manifest['binary'] and manifest['binary'].lower() != binary_hash.lower():
            conflicts['sliceBinaryConflicts'].append(
                dict(path=manifest['rel'], manifestBinary=manifest['binary']))
            continue
        for item in manifest['slices']:
            rva = int(item['rva'], 16)
            entry = record(rva)
            entry['tracks'].append(track_of(manifest['rel']))
            entry['sources'].append(dict(kind='slice-manifest', path=manifest['rel']))
            entry['nameSources'].append(item.get('name') or f'slice_{item["rva"][2:]}')

    # 3. addresses the current combat sources cite (doc/tool/evidence, via the index)
    for rva, rows in cited.items():
        entry = record(rva)
        for track in sorted({track_of(row['rel']) for row in rows}):
            entry['tracks'].append(track)
        seen = set()
        for row in rows:
            key = (row['kind'], row['rel'])
            if key in seen:
                continue
            seen.add(key)
            entry['sources'].append(dict(kind=row['kind'], path=row['rel']))

    # 4. addresses reached by identifier rather than literal address.
    #    These *annotate* a member; they do not create one, because a name match is weaker
    #    evidence than a citation that names the address itself.
    for rva, rows in named.items():
        if rva not in records:
            continue
        entry = record(rva)
        for track in sorted({track_of(row['rel']) for row in rows}):
            entry['tracks'].append(track)
        entry['sources'].append(dict(kind='name-reference', path=rows[0]['rel']))

    # 5. later-track disassembly dumps: one function identity per file name
    for rva, paths in sorted(dumps.items()):
        entry = record(rva)
        for path in paths[:1]:
            entry['tracks'].append(track_of(path))
            entry['sources'].append(dict(kind='disassembly-dump', path=path))

    # 6. classify, and keep only what the native layer can actually identify
    population = {}
    for rva, entry in records.items():
        if entry['kind'] == 'unidentified':
            conflicts['citedWithoutIdentity'].append(dict(
                rva=entry['rva'],
                reason='cited by combat sources but the native index has neither a method nor a '
                       'code window at this address',
                citedIn=sorted({row['path'] for row in entry['sources']})[:2]))
            continue
        population[rva] = entry
        entry['tracks'] = sorted(set(entry['tracks']))
        entry['sources'] = sorted({(row['kind'], row['path']): row for row in entry['sources']}.values(),
                                  key=lambda row: (row['kind'], row['path']))
        if entry['kind'] == 'code-window':
            window = index.window(rva) or {}
            entry['windowKind'] = window.get('kind')
            entry['insideMethod'] = hex(window['insideMethod']) if window.get('insideMethod') else None
            entry['insideMethodName'] = window.get('insideMethodName')
        native_name = index.name(rva)
        for name_source in entry.pop('nameSources', []):
            if native_name and name_source and name_source != native_name:
                conflicts['nameConflicts'].append(dict(
                    rva=entry['rva'], severity='slice-label',
                    reason='slice manifest label differs from the canonical name',
                    names=[name_source, native_name]))

    # 7. the semantic layer: hand-written annotations only
    annotated = 0
    for rva, entry in population.items():
        annotation = (annotations_doc or {}).get('annotations', {}).get(hex(rva))
        if annotation:
            entry['annotation'] = annotation
            annotated += 1
        entry['relatedChecks'] = sorted({row['path'] for row in entry['sources']
                                         if Path(row['path']).name.startswith('check_')
                                         and Path(row['path']).name not in INFRASTRUCTURE_FILES})
        entry['relatedModels'] = sorted({row['path'] for row in entry['sources']
                                         if Path(row['path']).name.startswith('combat_')
                                         and Path(row['path']).name not in INFRASTRUCTURE_FILES})
        entry['callers'], entry['callees'] = index.edge_counts(rva)
    if annotations_doc:
        for key in sorted((annotations_doc.get('annotations') or {}), key=lambda k: int(k, 16)):
            if int(key, 16) not in population:
                conflicts['orphanAnnotations'].append(key)

    functions = [population[rva] for rva in sorted(population)]
    by_kind = {}
    for entry in functions:
        by_kind[entry['kind']] = by_kind.get(entry['kind'], 0) + 1
    overlay = dict(
        schema=SCHEMA,
        generatedUtc=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        nativeIndex=index.identity(),
        populationRule=dict(
            description='native entities reached by current combat evidence',
            sources=['the 503 pass-1 audit identities', 'every RE-evidence slice manifest',
                     'address and identifier mentions in the combat source roots',
                     'later-track disassembly dump file names'],
            combatSourcePrefixes=list(COMBAT_TRACK_PREFIXES),
            docFiles=list(COMBAT_DOC_FILES), factFiles=list(CURATED_FACT_FILES),
            toolRoot='KA-Website/tools/recovery/'),
        functions=functions,
        conflicts=conflicts,
        counts=dict(functions=len(functions), byKind=by_kind,
                    annotated=annotated,
                    withChecks=sum(1 for e in functions if e['relatedChecks']),
                    withModels=sum(1 for e in functions if e['relatedModels']),
                    tracks=sorted({t for e in functions for t in e['tracks']})),
    )
    stable = canonical_hash(stable_projection(overlay))
    report = dict(
        schema='ka-combat-overlay-build-2', nativeIndex=index.identity(),
        outputs=dict(overlay=rel(REGISTRY_PATH), annotations=rel(ANNOTATIONS_PATH)),
        counts=overlay['counts'],
        nativeCounts=index.manifest.get('counts'),
        edgeClasses=index.manifest.get('edgeClasses'),
        stableDigest=stable,
    )
    return dict(overlay=overlay, report=report, index=index)


def stable_projection(overlay: dict) -> dict:
    """The overlay with provenance lists reduced to (kind, path) sets.

    Editing a document shifts citation line numbers; that must not be reported as "the
    overlay is stale". A new source, function or annotation must be.
    """
    functions = []
    for entry in overlay['functions']:
        item = {key: value for key, value in entry.items() if key != 'sources'}
        item['sourceSet'] = sorted({(row['kind'], row['path']) for row in entry.get('sources', [])})
        functions.append(item)
    return dict(schema=overlay['schema'], nativeIndex=overlay['nativeIndex'],
                functions=functions, conflicts=overlay['conflicts'])


def write(result: dict) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    REGISTRY_PATH.write_text(json.dumps(result['overlay'], indent=1, ensure_ascii=False) + '\n',
                             encoding='utf-8')
    REPORT_PATH.write_text(json.dumps(result['report'], indent=1, ensure_ascii=False) + '\n',
                           encoding='utf-8')


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--verify', action='store_true',
                        help='rebuild in memory and compare against the stored overlay')
    parser.add_argument('--quiet', action='store_true')
    args = parser.parse_args(argv)
    result = build(verbose=not args.quiet)
    if args.verify:
        if not REGISTRY_PATH.is_file():
            print('MISSING overlay; run the build')
            return 1
        stored = json.loads(REGISTRY_PATH.read_text(encoding='utf-8'))
        if canonical_hash(stable_projection(stored)) != canonical_hash(
                stable_projection(result['overlay'])):
            print('STALE overlay: the stored file differs semantically from a fresh build')
            return 1
        if stored.get('nativeIndex', {}).get('contentDigest') != result['overlay']['nativeIndex']['contentDigest']:
            print('STALE overlay: generated from a different native index state')
            return 1
        print(json.dumps(dict(verified=True, functions=result['report']['counts']['functions'],
                              byKind=result['report']['counts']['byKind'],
                              nativeContentDigest=result['overlay']['nativeIndex']['contentDigest'],
                              stableDigest=result['report']['stableDigest'])))
        return 0
    write(result)
    print(json.dumps(result['report']['counts'], indent=1, ensure_ascii=False))
    print(json.dumps(dict(nativeIndex=result['overlay']['nativeIndex']['contentDigest'],
                          stableDigest=result['report']['stableDigest'])))
    return 0


if __name__ == '__main__':
    sys.exit(main())
