"""Combat-native query entry point: the canonical native index plus the combat overlay.

Native facts (identity, signature, declaring type, extent, fields, artifacts, citations,
callers, callees, binary provenance) come from the canonical index database built by
`ka_index.py`. Combat semantics (purpose, confidence, related checks, related model modules,
track, unresolved questions) come from `build_combat_registry.py`'s overlay. There is one
native truth; this tool only joins the two.

    python combat_native.py 0x1587878              # RVA or name
    python combat_native.py DecideNextState --names
    python combat_native.py callers 0x1587878 --names
    python combat_native.py callees 0x1587184 --limit 20
    python combat_native.py find Gauge             # partial name / class search
    python combat_native.py class ecs.FighterSystem
    python combat_native.py checks 0x14b8e4c
    python combat_native.py keys                     # the blackboard key/number inventory
    python combat_native.py key 62                   # what is blackboard[62]?
    python combat_native.py key BATTLE_ATTACK_GAUGE
    python combat_native.py params                   # the parameter-id inventory
    python combat_native.py param HP
    python combat_native.py stubs                    # standard fixture helper targets
    python combat_native.py stats
    python combat_native.py conflicts

Add --json for machine-readable output. Anything not combat-scoped is `ka_index.py`'s job:
`ka_index.py query --rva <rva>`, `ka_index.py search <terms>`, `ka_index.py brief <topic>`.

The key/param/stub answers come from `recover_combat_keys.py`'s overlay, which stores only
RVAs and names-of-numbers; every function, class and listing is resolved here from the
canonical index, so this tool is the one place a number becomes a named thing.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parents[2]
OVERLAY_PATH = ROOT / 'RE-evidence/20260919-combat-registry/combat-registry.json'
KEYS_PATH = ROOT / 'RE-evidence/20260919-combat-registry/combat-keys.json'
NATIVE_BUILD = ROOT / 'RE-evidence/20260920-native-index/build'
NATIVE_DB = NATIVE_BUILD / 'ka-index.sqlite'
NATIVE_MANIFEST = NATIVE_BUILD / 'index-manifest.json'

_OVERLAY = None
_KEYS = None
_CONN = None


class LookupError_(SystemExit):
    pass


def overlay() -> dict:
    global _OVERLAY
    if _OVERLAY is None:
        if not OVERLAY_PATH.is_file():
            raise LookupError_(f'missing combat overlay {OVERLAY_PATH}; run '
                               'build_combat_registry.py')
        _OVERLAY = json.loads(OVERLAY_PATH.read_text(encoding='utf-8'))
    return _OVERLAY


def overlay_by_rva() -> dict:
    return {int(row['rva'], 16): row for row in overlay()['functions']}


def keys_index() -> dict:
    """The key/number semantics overlay, built by recover_combat_keys.py."""
    global _KEYS
    if _KEYS is None:
        if not KEYS_PATH.is_file():
            raise LookupError_(f'missing combat key index {KEYS_PATH}; run '
                               'recover_combat_keys.py')
        _KEYS = json.loads(KEYS_PATH.read_text(encoding='utf-8'))
    return _KEYS


def key_entry(text: str) -> dict:
    """Find one key entry by `BKI:62`, by a bare number, or by its official name."""
    index = keys_index()
    needle = text.strip()
    if ':' in needle and needle.upper().split(':')[0] in ('BKI', 'BKL'):
        for entry in index['keys']:
            if entry['key'].lower() == needle.lower():
                return entry
    bare = needle.lstrip('0x') if needle.lower().startswith('0x') else needle
    if bare.lstrip('-').isdigit():
        hits = [entry for entry in index['keys'] if entry['value'] == int(bare)]
        if len(hits) == 1:
            return hits[0]
        if hits:
            # The same number in two key spaces is the whole point of the namespace split.
            raise LookupError_(f'{needle} is defined in more than one key space '
                               f'({", ".join(entry["key"] for entry in hits)}). Qualify it as '
                               f'BKI:{bare} or BKL:{bare}.')
    upper = needle.upper()
    for entry in index['keys']:
        if (entry['officialName'] or '').upper() == upper:
            return entry
    # A board index that is not a key at all (a fixture sentinel, or the BKL id carried in
    # the merged dictionary) is documented in the board block; answer from there instead of
    # saying "no such key", which would read like a gap in the layer.
    for row in index.get('boardIndices', []):
        if row['key'].lower() == needle.lower() or \
                (row['officialName'] or '').upper() == upper:
            return dict(row, value=row['index'], sites=0, functions=row['nativeFunctions'],
                        operations=row.get('operations') or {}, relatedChecks=row['relatedChecks'],
                        relatedModels=row.get('usedByFiles') or [])
    raise LookupError_(f'no combat key matches {text!r}; try `keys` for the inventory')


def param_entry(text: str) -> dict:
    index = keys_index()
    needle = text.strip()
    if needle.isdigit():
        for entry in index['params']:
            if entry['value'] == int(needle):
                return entry
    upper = needle.upper()
    for entry in index['params']:
        if (entry['officialName'] or '').upper() == upper:
            return entry
    for entry in index['params']:
        if upper in (entry['officialName'] or '').upper():
            return entry
    raise LookupError_(f'no combat parameter matches {text!r}; try `params` for the list')


def key_sites(entry: dict, limit: int = 4) -> list:
    """Representative access sites for one key, straight out of the stored site list."""
    rows = []
    for site in keys_index().get('sites', []):
        if site['namespace'] == entry['namespace'] and site.get('key') == entry['value']:
            rows.append(site)
            if len(rows) >= limit:
                break
    return rows


def param_sites(entry: dict, limit: int = 4) -> list:
    rows = []
    for site in keys_index().get('paramSites', []):
        if site.get('paramId') == entry['value']:
            rows.append(site)
            if len(rows) >= limit:
                break
    return rows


def board_entry(entry: dict):
    for row in keys_index().get('boardIndices', []):
        if row['key'] == entry['key']:
            return row
    return None


def db() -> sqlite3.Connection:
    global _CONN
    if _CONN is None:
        if not NATIVE_DB.is_file():
            raise LookupError_(f'missing canonical native index {NATIVE_DB}; run '
                               'ka_index.py build')
        _CONN = sqlite3.connect(f'file:{NATIVE_DB.as_posix()}?mode=ro', uri=True)
        _CONN.row_factory = sqlite3.Row
    return _CONN


def manifest() -> dict:
    return (json.loads(NATIVE_MANIFEST.read_text(encoding='utf-8'))
            if NATIVE_MANIFEST.is_file() else {})


def normalise_rva(text: str) -> int:
    value = str(text).strip().lower()
    if value.startswith('0x'):
        value = value[2:]
    if not value or any(character not in '0123456789abcdef' for character in value):
        raise LookupError_(f'not an RVA: {text!r}')
    return int(value, 16)


def resolve(text: str, combat_only: bool = False):
    """Find a native entity by RVA or by name. Returns (rva, kind)."""
    try:
        rva = normalise_rva(text)
        rva = rva if rva is not None else None
    except LookupError_:
        rva = None
    if rva is not None:
        entry = overlay_by_rva().get(rva)
        if entry is not None:
            return rva, entry.get('kind') or 'overlay'
        row = db().execute('SELECT rva FROM methods WHERE rva=?', (rva,)).fetchone()
        if row:
            return row['rva'], 'method'
        row = db().execute('SELECT rva FROM code_windows WHERE rva=?', (rva,)).fetchone()
        if row:
            return row['rva'], 'code-window'
        return None, None
    needle = text.lower()
    rows = db().execute(
        "SELECT rva, name FROM methods WHERE lower(name)=? OR lower(method)=? "
        "OR lower(name) LIKE ? ORDER BY rva LIMIT 12", (needle, needle, f'%{needle}%')).fetchall()
    known = overlay_by_rva()
    if combat_only:
        rows = [row for row in rows if row['rva'] in known]
    # An exact match on the bare method name wins over the LIKE noise, combat scope first.
    exact = [row for row in rows if row['name'].lower() == needle]
    bare = [row for row in rows if row['name'].split('$$')[-1].lower() == needle]
    if combat_only:
        exact = [row for row in exact if row['rva'] in known]
        bare = [row for row in bare if row['rva'] in known]
    if len(bare) == 1:
        return bare[0]['rva'], 'method'
    if len(exact) == 1:
        return exact[0]['rva'], 'method'
    if len(rows) == 1:
        return rows[0]['rva'], 'method'
    if not rows:
        # a code window may carry a manifest name instead of a method name
        row = db().execute("SELECT rva, name FROM code_windows WHERE lower(name)=? OR lower(name) LIKE ? "
                           "ORDER BY rva LIMIT 12", (needle, f'%{needle}%')).fetchall()
        if len(row) == 1:
            return row[0]['rva'], 'code-window'
        raise LookupError_(f'no native entity matches {text!r}')
    listing = '\n'.join(f'    {hex(row["rva"]):>10}  {row["name"]:<56} '
                        f'{"combat" if row["rva"] in overlay_by_rva() else "-":<6}'
                        for row in rows)
    raise LookupError_(f'{text!r} is ambiguous ({len(rows)} matches). Qualify with the class or '
                       f'the RVA:\n{listing}')


def name_of(rva: int) -> str:
    row = db().execute('SELECT name FROM methods WHERE rva=?', (rva,)).fetchone()
    if row:
        return row['name']
    row = db().execute('SELECT name FROM code_windows WHERE rva=?', (rva,)).fetchone()
    if row and row['name']:
        return row['name']
    return f'{hex(rva)} (no identity)' if not row else f'{hex(rva)} (code window)' 


def edges(direction: str, rva: int, limit: int) -> list:
    column = 'callee_rva' if direction == 'callers' else 'caller_rva'
    sql = (f'SELECT site, caller_rva, callee_rva, kind, source_kind, target_kind FROM calls '
           f'WHERE {column}=? ORDER BY site')
    rows = []
    for row in db().execute(sql, (rva,)):
        other = row['caller_rva'] if direction == 'callers' else row['callee_rva']
        rows.append(dict(site=hex(row['site']), kind='tail-b' if row['kind'] == 'branch' else 'bl',
                         other=other, otherName=name_of(other) if other else '(unclaimed code)',
                         otherIsMethod=is_method(other) if other else False,
                         direction=direction))
        if len(rows) >= limit:
            break
    return rows


def edge_count(direction: str, rva: int) -> int:
    column = 'callee_rva' if direction == 'callers' else 'caller_rva'
    return db().execute(f'SELECT COUNT(*) FROM calls WHERE {column}=?', (rva,)).fetchone()[0]


def is_method(rva: int) -> bool:
    return db().execute('SELECT 1 FROM methods WHERE rva=?', (rva,)).fetchone() is not None


def native_block(rva: int) -> dict:
    conn = db()
    method = conn.execute('SELECT * FROM methods WHERE rva=?', (rva,)).fetchone()
    audited = conn.execute('SELECT * FROM audit_identity WHERE rva=?', (rva,)).fetchone()
    code = conn.execute('SELECT * FROM code_map WHERE rva=?', (rva,)).fetchone()
    window = conn.execute('SELECT * FROM code_windows WHERE rva=?', (rva,)).fetchone()
    helper = conn.execute('SELECT * FROM helper_findings WHERE rva=?', (rva,)).fetchone()
    artifacts = [dict(kind=row['kind'], path=row['rel']) for row in
                 conn.execute('SELECT kind, rel FROM artifacts WHERE rva=? ORDER BY kind, rel', (rva,))]
    aliases = [dict(alias=row['alias'], kind=row['kind'], confidence=row['confidence'])
               for row in conn.execute(
                   "SELECT alias, kind, confidence FROM method_alias WHERE rva=? AND kind NOT IN "
                   "('il2cpp-dumper','short-managed') ORDER BY kind, alias LIMIT 8", (rva,))]
    fields = [dict(field=row['field'], type=row['field_type'], offset=row['offset']) for row in
              conn.execute("SELECT field, field_type, offset FROM fields WHERE type IN "
                           "(SELECT owner FROM methods WHERE rva=?) AND is_static=0 ORDER BY offset",
                           (rva,))]
    statics = [row['field'] for row in conn.execute(
        "SELECT field FROM fields WHERE type IN (SELECT owner FROM methods WHERE rva=?) "
        "AND is_static=1 ORDER BY field LIMIT 8", (rva,))]
    citations = [dict(kind=row['kind'], path=row['rel'], line=row['line'], heading=row['heading'],
                      snippet=row['snippet']) for row in conn.execute(
        'SELECT kind, rel, line, heading, snippet FROM mentions WHERE rva=? '
        'ORDER BY CASE kind WHEN \'doc\' THEN 0 WHEN \'tool\' THEN 1 ELSE 2 END, rel, line LIMIT 400',
        (rva,))]
    return dict(isMethod=method is not None,
                name=method['name'] if method else None,
                owner=method['owner'] if method else None,
                signature=method['signature'] if method else None,
                declaration=None,
                audited=dict(end=hex(audited['end_rva']), sha256=audited['sha256'],
                             source=audited['source']) if audited and audited['end_rva'] else None,
                extent=dict(first=hex(code['first_instr']), last=hex(code['last_instr']),
                            instrs=code['instrs'], end=hex(code['end_rva'])) if code else None,
                window=dict(name=window['name'], kind=window['kind'], manifest=window['manifest'],
                            instrs=window['instrs'], sha256=window['sha256'],
                            insideMethod=window['inside_method'], note=window['note'])
                if window else None,
                helper=dict(row=helper['verdict'], confidence=helper['confidence'],
                            implementation=helper['implementation']) if helper else None,
                artifacts=artifacts, aliases=aliases, fields=fields, statics=statics,
                citations=citations)


def describe(rva: int, show_names: bool, limit: int) -> str:
    native = native_block(rva)
    entry = overlay_by_rva().get(rva)
    annotation = (entry or {}).get('annotation') or {}
    lines = [f'{hex(rva)}  {native["name"] or "(no managed name)"}'
             + ('   [combat overlay]' if entry else '')]
    if native['signature']:
        lines.append(f'  signature         {native["signature"][:150]}')
    lines.append(f'  declaring type    {native["owner"] or "(not a managed method)"}')
    if native['isMethod'] is False:
        if native['window']:
            lines.append(f'  NATIVE CODE WINDOW [{native["window"]["kind"]}]'
                         + (f'  {native["window"]["name"]}' if native['window']['name'] else ''))
            if native['window']['insideMethod']:
                lines.append(f'  inside method     {hex(native["window"]["insideMethod"])}  '
                             f'{name_of(native["window"]["insideMethod"])}')
            if native['window']['manifest']:
                lines.append(f'  window manifest   {native["window"]["manifest"]}')
            if native['window']['sha256']:
                lines.append(f'  window sha256     {native["window"]["sha256"]}')
        lines.append('  identity note     this address is not an IL2CPP method start; the native '
                     'index stores it as native code evidence, not as a managed identity')
    if native['audited']:
        lines.append(f'  audited slice     end {native["audited"]["end"]}, '
                     f'sha256 {native["audited"]["sha256"][:16]}... ({native["audited"]["source"]})')
    if native['extent']:
        lines.append(f'  decoded extent    {native["extent"]["first"]}..{native["extent"]["last"]} '
                     f'({native["extent"]["instrs"]} instructions; upper bound from the linear sweep)')
    if annotation:
        lines.append(f'  purpose           {annotation.get("purpose")}')
        lines.append(f'  purpose status    {annotation.get("confidence")}'
                     + (f'  (evidence: {", ".join(annotation.get("evidence", []))})'
                        if annotation.get('evidence') else ''))
    elif entry:
        lines.append('  purpose           (no established purpose recorded; not in the annotated set)')
    if entry:
        if entry.get('tracks'):
            lines.append(f'  combat track      {", ".join(entry["tracks"])}')
        if entry.get('relatedChecks'):
            lines.append(f'  related checks    {", ".join(Path(p).name for p in entry["relatedChecks"])}')
        models = list(entry.get('relatedModels') or [])
        annotated_models = annotation.get('relatedModels') or []
        if models or annotated_models:
            lines.append(f'  related models    {", ".join(str(m).split("/")[-1] for m in models + annotated_models)}')
    if native['helper']:
        lines.append(f'  helper verdict    {native["helper"]["row"]} '
                     f'[{native["helper"]["confidence"]}]')
    if native['aliases']:
        lines.append('  aliases           ' + '; '.join(
            f'{a["alias"]} [{a["kind"]},{a["confidence"]}]' for a in native['aliases'][:5]))
    if native['fields']:
        lines.append('  instance fields   ' + '; '.join(
            f'+{hex(f["offset"])} {f["field"]}:{f["type"]}' for f in native['fields'][:6]))
    if native['statics']:
        lines.append(f'  static members    {", ".join(native["statics"][:6])}')
    if native['artifacts']:
        for artifact in native['artifacts'][:limit]:
            lines.append(f'  artifact          [{artifact["kind"]}] {artifact["path"]}')
    caller_total = edge_count('callers', rva)
    callee_total = edge_count('callees', rva)
    callers = edges('callers', rva, limit) if show_names else []
    callees = edges('callees', rva, limit) if show_names else []
    lines.append(f'  callers / callees {caller_total} / {callee_total}'
                 + ('  (showing first %d)' % limit if show_names else ''))
    if show_names:
        for row in callers:
            lines.append(f'    caller  {row["site"]}  {row["kind"]:6} {row["otherName"]}')
        for row in callees:
            marker = '' if row['otherIsMethod'] else '   (non-method target)'
            lines.append(f'    callee  {row["site"]}  {row["kind"]:6} {row["otherName"]}{marker}')
    deduped = {}
    for citation in native['citations']:
        key = (citation['kind'], citation['path'], citation['line'])
        deduped.setdefault(key, citation)
    native['citations'] = list(deduped.values())
    grouped = {}
    for citation in native['citations']:
        grouped.setdefault(citation['kind'], []).append(citation)
    if native['citations']:
        lines.append('  evidence citations '
                     + ', '.join(f'{kind}: {len(rows)}' for kind, rows in sorted(grouped.items())))
        for citation in native['citations'][:limit]:
            snippets = (citation['snippet'] or '')[:110]
            lines.append(f'    [{citation["kind"]}] {citation["path"]}:{citation["line"]}'
                         f'  {snippets}')
    if annotation.get('unresolved'):
        for note in annotation['unresolved'][:limit]:
            lines.append(f'  unresolved        {note}')
    for bucket in ('nameConflicts', 'orphanAnnotations', 'citedWithoutIdentity'):
        for item in overlay()['conflicts'].get(bucket, []):
            if isinstance(item, dict) and item.get('rva') == hex(rva):
                lines.append(f'  CONFLICT ({bucket}) {json.dumps(item)[:180]}')
            elif item == hex(rva):
                lines.append(f'  CONFLICT ({bucket}) {item}')
    return '\n'.join(lines)


def main(argv=None) -> int:
    # Research documents carry table glyphs and arrows; the Windows console default code
    # page cannot encode them, so force a tolerant UTF-8 stream.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', nargs='?', default='show')
    parser.add_argument('target', nargs='?')
    parser.add_argument('--json', action='store_true', dest='as_json')
    parser.add_argument('--names', action='store_true', help='expand caller/callee rows')
    parser.add_argument('--limit', type=int, default=10)
    args = parser.parse_args(argv)

    command, target = args.command, args.target
    if command not in ('show', 'find', 'class', 'callers', 'callees', 'checks', 'conflicts', 'stats',
                       'native', 'keys', 'key', 'params', 'param', 'stubs'):
        target, command = command, 'show'

    if command == 'stats':
        payload = dict(overlay=overlay()['counts'], nativeIndex=overlay()['nativeIndex'],
                       nativeCounts=manifest().get('counts'), edgeClasses=manifest().get('edgeClasses'),
                       limitations=[
                           'Indirect calls (BLR/BR, virtual, interface, delegates, function '
                           'pointers) are not resolved by the native sweep.',
                           'Edge rows keep the containing method as the source; call sites in '
                           'unclaimed executable regions have no source and are labelled so.',
                           'A non-method target is a real direct call to native code with no '
                           'managed identity - usually IL2CPP runtime machinery.'])
        print(json.dumps(payload, indent=1) if args.as_json else _stats_text(payload))
        return 0
    if command == 'conflicts':
        payload = overlay()['conflicts']
        if args.as_json:
            print(json.dumps(payload, indent=1))
        else:
            for bucket, rows in payload.items():
                print(f'{bucket}: {len(rows)}')
                for row in rows[:args.limit]:
                    print(f'  {json.dumps(row)[:190]}')
        return 0
    if command == 'find':
        if not target:
            raise LookupError_('find needs a name or class fragment')
        needle = target.lower()
        known = overlay_by_rva()
        rows = db().execute(
            "SELECT rva, name, owner FROM methods WHERE lower(name) LIKE ? OR lower(owner) LIKE ? "
            "ORDER BY rva LIMIT ?", (f'%{needle}%', f'%{needle}%', args.limit)).fetchall()
        payload = [dict(rva=hex(row['rva']), name=row['name'], owner=row['owner'],
                        combat=row['rva'] in known,
                        purpose=((known.get(row['rva'], {}).get('annotation') or {}).get('purpose')))
                   for row in rows]
        if args.as_json:
            print(json.dumps(payload, indent=1))
        else:
            print(f'{len(payload)} match(es) for {target!r} in the canonical native index')
            for row in payload:
                print(f'  {row["rva"]:>10}  {row["name"]:<58} '
                      f'{"[combat]" if row["combat"] else "":9}'
                      + (f'{row["purpose"][:52]}' if row['purpose'] else ''))
        return 0
    if command == 'class':
        if not target:
            raise LookupError_('class needs a class name fragment')
        known = overlay_by_rva()
        rows = db().execute('SELECT rva, name, owner FROM methods WHERE owner=? ORDER BY rva',
                            (target,)).fetchall()
        if not rows:
            rows = db().execute('SELECT rva, name, owner FROM methods WHERE lower(owner) LIKE ? '
                                'ORDER BY rva LIMIT ?', (f'%{target.lower()}%', args.limit)).fetchall()
        if args.as_json:
            print(json.dumps([dict(rva=hex(r['rva']), name=r['name'], owner=r['owner'],
                                   combat=r['rva'] in known) for r in rows], indent=1))
        else:
            print(f'{len(rows)} function(s) in {target}')
            for row in rows[:args.limit]:
                entry = known.get(row['rva']) or {}
                purpose = (entry.get('annotation') or {}).get('purpose')
                print(f'  {hex(row["rva"]):>10}  {row["name"].split("$$")[-1]:<44}'
                      f'{"[combat]" if row['rva'] in known else "":9}'
                      + (f'{purpose[:44]}' if purpose else ''))
        return 0
    if command in ('callers', 'callees'):
        rva, _kind = resolve(target, combat_only=False)
        if rva is None:
            raise LookupError_(f'no native entity for {target!r}')
        rows = edges('callers' if command == 'callers' else 'callees', rva, args.limit)
        if args.as_json:
            print(json.dumps(dict(rva=hex(rva), name=name_of(rva), direction=command, rows=rows),
                             indent=1))
        else:
            print(f'{command} of {name_of(rva)} ({hex(rva)}): {len(rows)} site(s)')
            for row in rows:
                marker = '' if row['otherIsMethod'] else '   (non-method target)'
                print(f'  {row["site"]}  {row["kind"]:6} {row["otherName"]}{marker}')
        return 0
    if command == 'checks':
        rva, _kind = resolve(target)
        entry = overlay_by_rva().get(rva) or {}
        annotation = entry.get('annotation') or {}
        payload = dict(rva=hex(rva), name=name_of(rva), relatedChecks=entry.get('relatedChecks'),
                       relatedModels=entry.get('relatedModels'),
                       annotatedModels=annotation.get('relatedModels'),
                       purpose=annotation.get('purpose'), confidence=annotation.get('confidence'))
        if args.as_json:
            print(json.dumps(payload, indent=1))
        else:
            print(f'{name_of(rva)} ({hex(rva)})')
            print(f'  purpose  {payload["purpose"] or "(none recorded)"}'
                  + (f'  [{payload["confidence"]}]' if payload['confidence'] else ''))
            for value in payload['relatedChecks'] or ['(none)']:
                print(f'  check    {value}')
            for value in payload['relatedModels'] or ['(none)']:
                print(f'  model    {value}')
            for value in payload['annotatedModels'] or []:
                print(f'  model    {value}')
        return 0
    if command == 'native':
        # Convenience passthrough so one tool can answer "is this combat or not".
        rva, kind = resolve(target)
        entry = overlay_by_rva().get(rva)
        print(json.dumps(dict(rva=hex(rva), kind=kind, combat=entry is not None,
                              name=name_of(rva), tracks=(entry or {}).get('tracks')), indent=1))
        return 0
    if command == 'keys':
        index = keys_index()
        rows = index['keys']
        if args.as_json:
            print(json.dumps(rows, indent=1))
            return 0
        counts = index['counts']
        print(f'combat key inventory: {counts["keys"]} distinct keys across '
              f'{counts["blackboardSites"]} blackboard access sites in '
              f'{counts["blackboardFunctions"]} combat functions '
              f'({counts["blackboardSitesResolved"]} statically resolved)')
        for space in ('BKI', 'BKL'):
            space_rows = [row for row in rows if row['namespace'] == space]
            print(f'\n{space} ({index["keySpaces"][space]["enumType"]}, '
                  f'{index["keySpaces"][space]["holder"]})')
            for row in space_rows:
                grade = f'[{row["confidence"]}]' if row['confidence'] != 'unknown' else ''
                role = (row['recoveredRole'] or '(no recovered role recorded)')
                print(f'  {row["value"]:>4}  {row["officialName"] or "(no official name)":<46}'
                      f'sites {row["sites"]:>3}  {row["operations"]} {grade}')
                print(f'        {role[:150]}')
        print('\nA bare number is not unique: BKI and BKL share numbers. Query with '
              '`key BKI:62` or `key BATTLE_ATTACK_GAUGE`.')
        return 0
    if command == 'key':
        if not target:
            raise LookupError_('key needs a number, a namespace-qualified key or an official name')
        entry = key_entry(target)
        if args.as_json:
            print(json.dumps(entry, indent=1))
            return 0
        print(f'{entry["key"]}  {entry["officialName"] or "(no official name)"}'
              f'  [{entry["confidence"]}]')
        print(f'  key space        {entry["namespace"]}'
              f'  ({"official enum member" if entry["officialNameDefined"] else "not a defined " + entry["namespace"] + " member"})')
        print(f'  recovered role   {entry["recoveredRole"] or "(no recovered role recorded)"}')
        print(f'  access sites     {entry["sites"]}  {entry["operations"]}')
        for reference in entry['evidence']:
            line = f':{reference["line"]}' if reference.get('line') else ''
            note = f'  - {reference["note"]}' if reference.get('note') else ''
            print(f'  evidence         {reference["path"]}{line}{note}')
        for rva in entry['functions'][:args.limit]:
            print(f'  function         {rva}  {name_of(int(rva, 16))}')
        if len(entry['functions']) > args.limit:
            print(f'                   ... and {len(entry["functions"]) - args.limit} more')
        board = board_entry(entry)
        if board:
            for place in board['usedBy'][:3]:
                print(f'  live model use   {place}')
            if board['readers']:
                print(f'  representative   read  {", ".join(board["readers"][:3])}')
            if board['writers']:
                print(f'                   write {", ".join(board["writers"][:3])}')
        else:
            for site in key_sites(entry, 3):
                print(f'  access site      {site["site"]}  {site["operation"]:6} '
                      f'{site["accessorMethod"]:10} in {site["function"]} '
                      f'({site["keyDerivation"]})')
        for check in entry['relatedChecks'][:6]:
            print(f'  related check    {Path(check).name}')
        for model in entry['relatedModels'][:6]:
            print(f'  related model    {Path(model).name}')
        return 0
    if command == 'params':
        index = keys_index()
        rows = index['params']
        if args.as_json:
            print(json.dumps(rows, indent=1))
            return 0
        counts = index['counts']
        print(f'combat parameter inventory: {counts["params"]} distinct ids across '
              f'{counts["paramSites"]} parameter access sites in {counts["paramFunctions"]} '
              f'functions ({counts["paramSitesResolved"]} statically resolved)')
        for row in rows:
            grade = f'[{row["confidence"]}]' if row['confidence'] != 'unknown' else ''
            print(f'  {row["value"]:>3}  {row["officialName"] or "(no official name)":<14}'
                  f'sites {row["sites"]:>3}  {row["operations"]} {grade}')
            if row['recoveredRole']:
                print(f'       {row["recoveredRole"][:150]}')
        return 0
    if command == 'param':
        if not target:
            raise LookupError_('param needs a number or a name')
        entry = param_entry(target)
        if args.as_json:
            print(json.dumps(entry, indent=1))
            return 0
        print(f'Param {entry["value"]}  {entry["officialName"] or "(no official name)"}'
              f'  [{entry["confidence"]}]')
        print(f'  recovered role   {entry["recoveredRole"] or "(no recovered role recorded)"}')
        print(f'  access sites     {entry["sites"]}  {entry["operations"]}')
        print(f'  accessors        {", ".join(entry["accessors"])}')
        for reference in entry['evidence']:
            line = f':{reference["line"]}' if reference.get('line') else ''
            note = f'  - {reference["note"]}' if reference.get('note') else ''
            print(f'  evidence         {reference["path"]}{line}{note}')
        for rva in entry['functions'][:args.limit]:
            print(f'  function         {rva}  {name_of(int(rva, 16))}')
        if len(entry['functions']) > args.limit:
            print(f'                   ... and {len(entry["functions"]) - args.limit} more')
        for site in param_sites(entry, 3):
            print(f'  access site      {site["site"]}  {site["accessorName"]:24} '
                  f'in {site["function"]} ({site["idDerivation"]})')
        return 0
    if command == 'stubs':
        index = keys_index()
        rows = sorted(index['stubs'], key=lambda row: (-row['handlerArms'], row['address']))
        if args.as_json:
            print(json.dumps(rows, indent=1))
            return 0
        counts = index['counts']
        print(f'fixture stub catalogue: {counts["stubs"]} addresses '
              f'({counts["stubsDeclared"]} carry an explicit fixture name), ranked by how many '
              f'fixture handler arms dispatch on them')
        for row in rows[:args.limit]:
            names = ','.join(row['fixtureNames']) or '-'
            label = row['managedName'] or row['recordedVerdict'] or row['classification']
            print(f'  {row["address"]:>12}  arms {row["handlerArms"]:>3}  '
                  f'in {row["incomingDirectCalls"]:>7}  {names:<20} {str(label)[:70]}')
        if len(rows) > args.limit:
            print(f'  ... and {len(rows) - args.limit} more (--limit)')
        return 0
    if not target:
        raise LookupError_('show needs an RVA or a name')
    rva, _kind = resolve(target)
    if rva is None:
        raise LookupError_(f'no native entity for {target!r}')
    if args.as_json:
        print(json.dumps(dict(rva=hex(rva), native=native_block(rva),
                              combat=overlay_by_rva().get(rva)), indent=1, ensure_ascii=False))
    else:
        print(describe(rva, args.names, args.limit))
    return 0


def _stats_text(payload: dict) -> str:
    lines = ['combat overlay over the canonical native index']
    lines.append('  overlay counts    ' + json.dumps(payload['overlay']))
    lines.append('  native index      ' + json.dumps(payload['nativeIndex']))
    lines.append('  edge classes      ' + json.dumps(payload.get('edgeClasses')))
    native = payload.get('nativeCounts') or {}
    for key in ('methods', 'types', 'fields', 'method_alias', 'mentions', 'calls', 'artifacts',
                'code_windows'):
        if key in native:
            lines.append(f'  native {key:<12} {native[key]}')
    for item in payload.get('limitations', []):
        lines.append(f'  limitation        {item}')
    return '\n'.join(lines)


if __name__ == '__main__':
    sys.exit(main())
