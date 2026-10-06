"""Regression for the combat key/index semantics layer.

The layer exists so a raw number stops being an identity problem. That only holds if
every stored number is still the number the frozen binary uses, every name is still the
name the frozen metadata declares, and every role is still backed by a document that
says so. This check therefore re-derives rather than re-reads:

  * it re-runs the access-site recovery over the frozen ELF and compares the result with
    the stored overlay, so a stale key file fails here instead of silently answering;
  * it re-parses `ai.BKI`, `ai.BKL`, `parameter.Param` and `data.SkillData` out of the
    frozen `dump.cs` and compares every official name and every value;
  * it resolves every stored access site, containing function and artifact through the
    canonical native index, so nothing in the layer can drift into a private truth;
  * it refuses a meaning on a dynamic number and a role without evidence;
  * it keeps the two key spaces apart, because BKI 62 and BKL 62 are different fields;
  * it measures the brief: the evidence body must be byte-identical with and without the
    key block, the block must stay inside its own allowance, and the three representative
    topics must actually surface their numbers.
"""
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

import combat_native as query
import ka_index as native
import recover_combat_keys as keys
from combat_run_manifest import NATIVE_SHA256, canonical_hash

ROOT = keys.ROOT
OVERLAY = keys.KEYS_PATH
REPORT = keys.REPORT_PATH
BRIEF_TOPICS = (
    "status passive generated invocation sleep defense down",
    "charging attack interval agility gauge",
    "initial state fighter initialization formation",
)

# A recorded citation has two parts: the file still says what was cited, and (for evidence
# that does not move) it still says it on the recorded line. The frozen dump and the
# append-only research documents are line-stable, so their lines are asserted. The live
# combat tools and the rolling CURRENT.md are edited continuously by other work, so only
# their content is asserted - a moved line is not a changed claim.
MOVING_EVIDENCE = ('KA-Website/tools/recovery/', 'KA-Website/docs/reverse-engineering/CURRENT.md')


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(f'file:{(ROOT / keys.NATIVE_DB.relative_to(keys.ROOT)).as_posix()}'
                           '?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def check_evidence(reference, results, label):
    """A cited document must exist, and a cited line must still say what was cited."""
    path = ROOT / reference['path']
    assert path.is_file(), f'{label}: evidence path missing: {reference["path"]}'
    moving = any(reference['path'].startswith(prefix) for prefix in MOVING_EVIDENCE)
    if reference.get('line') and not moving:
        lines = path.read_text(encoding='utf-8', errors='replace').splitlines()
        assert 1 <= reference['line'] <= len(lines), \
            f'{label}: {reference["path"]}:{reference["line"]} is past the end of the file'
        if reference.get('contains'):
            assert reference['contains'] in lines[reference['line'] - 1], (
                f'{label}: {reference["path"]}:{reference["line"]} no longer contains '
                f'{reference["contains"]!r}')
    elif reference.get('contains'):
        text = path.read_text(encoding='utf-8', errors='replace')
        assert reference['contains'] in text, (
            f'{label}: {reference["path"]} no longer contains {reference["contains"]!r}')
        if moving:
            results['evidenceMoved'] = results.get('evidenceMoved', 0) + 1
    results.setdefault('evidence', 0)
    results['evidence'] += 1


def main():
    assert OVERLAY.is_file(), f'{OVERLAY} missing - run recover_combat_keys.py'
    assert REPORT.is_file(), f'{REPORT} missing - run recover_combat_keys.py'
    overlay = json.loads(OVERLAY.read_text(encoding='utf-8'))
    report = json.loads(REPORT.read_text(encoding='utf-8'))
    annotations = json.loads((ROOT / keys.ANNOTATIONS_PATH.relative_to(keys.ROOT))
                             .read_text(encoding='utf-8'))
    assert overlay['schema'] == keys.SCHEMA, overlay['schema']
    assert annotations['schema'] == keys.ANNOTATIONS_SCHEMA, annotations['schema']
    results = {}

    # 1. binary and index provenance, on both sides
    frozen = ROOT / 'RE-evidence/G2.1/39257e72291d/inputs/libil2cpp.so'
    live = hashlib.sha256(frozen.read_bytes()).hexdigest()
    manifest = json.loads((ROOT / keys.NATIVE_MANIFEST.relative_to(keys.ROOT))
                          .read_text(encoding='utf-8'))
    assert live == NATIVE_SHA256 == overlay['nativeIndex']['binarySha256'] == \
        manifest['binarySha256']
    assert overlay['nativeIndex']['contentDigest'] == manifest['contentDigest'], \
        'the key overlay pins a different native content digest than the manifest on disk'
    assert overlay['nativeIndex']['sourceSetDigest'] == manifest['sourceSetDigest']
    assert overlay['frozenMetadata']['sha256'] == hashlib.sha256(
        (ROOT / overlay['frozenMetadata']['dump']).read_bytes()).hexdigest()
    results['provenance'] = dict(binarySha256=NATIVE_SHA256,
                                 contentDigest=manifest['contentDigest'][:16],
                                 frozenMetadata=overlay['frozenMetadata']['sha256'][:16])

    # 2. the stored overlay is exactly what a rebuild produces - run this before any
    #    assertion about a role, so a hand edit that was never regenerated says so.
    first, second = keys.build(), keys.build()
    assert canonical_hash(keys.stable_projection(first['keys'])) == \
        canonical_hash(keys.stable_projection(second['keys'])), \
        'the key overlay build is not deterministic'
    assert canonical_hash(keys.stable_projection(overlay)) == \
        canonical_hash(keys.stable_projection(first['keys'])), (
            'the stored combat-keys overlay is stale - rerun recover_combat_keys.py '
            '(its annotations or the frozen evidence changed)')
    assert report['stableDigest'] == first['report']['stableDigest'], 'stable digest drift'
    results['determinism'] = dict(stableDigest=report['stableDigest'], reproducible=True)

    conn = connect()
    methods = {row['rva']: row['name'] for row in conn.execute('SELECT rva, name FROM methods')}
    edges = {}
    for row in conn.execute('SELECT site, callee_rva, caller_rva FROM calls'):
        edges[row['site']] = (row['callee_rva'], row['caller_rva'])
    artifact_rows = {}
    for row in conn.execute('SELECT rva, rel FROM artifacts'):
        artifact_rows.setdefault(row['rva'], set()).add(keys.norm(row['rel']))

    # 2. every accessor identity resolves, and the family matches the key space
    accessor_family = {}
    for space, block in overlay['keySpaces'].items():
        for rva_text in block['accessors']:
            rva = int(rva_text, 16)
            name = methods.get(rva)
            assert name, f'{rva_text} is not a method in the canonical index'
            expected = 'BlackboardInt' if space == 'BKI' else 'BlackboardLong'
            assert f'Blackboard<Int32Enum, {"int" if space == "BKI" else "long"}>' in name, name
            assert expected in (block['host'] or ''), block['host']
            accessor_family[rva] = space
    for rva_text in overlay['paramSpace']['accessors']:
        rva = int(rva_text, 16)
        name = methods.get(rva)
        assert name and name.startswith('parameter.Param'), f'{rva_text}: {name}'
    results['accessors'] = dict(blackboard=len(accessor_family),
                               params=len(overlay['paramSpace']['accessors']),
                               keySpaces=sorted(overlay['keySpaces']))

    # 3. every access site resolves through the canonical graph, and nothing is invented
    overlay_rows = json.loads(keys.COMBAT_OVERLAY.read_text(encoding='utf-8'))['functions']
    combat_population = {int(row['rva'], 16) for row in overlay_rows}
    for site in overlay['sites'] + overlay['paramSites']:
        site_rva = int(site['site'], 16)
        accessor = int(site['accessor'], 16)
        function = int(site['function'], 16)
        assert site_rva in edges, f'call site {site["site"]} is not in the canonical graph'
        assert edges[site_rva] == (accessor, function), (
            f'call site {site["site"]} does not reach {site["accessor"]} from '
            f'{site["function"]} in the canonical graph')
        assert accessor in accessor_family or accessor in {
            int(rva, 16) for rva in overlay['paramSpace']['accessors']}
        assert function in methods, f'{site["function"]} is not a method'
        assert function in combat_population, (
            f'{site["function"]} is outside the combat overlay population')
        for path in site['artifacts']:
            assert keys.norm(path) in artifact_rows.get(function, set()), (
                f'{site["function"]}: {path} is not an indexed artifact of that function')
            assert (ROOT / path).is_file(), f'artifact missing on disk: {path}'
    results['sites'] = dict(blackboard=len(overlay['sites']), params=len(overlay['paramSites']),
                            allResolve=True,
                            population=len(combat_population))

    # 4. names come from the frozen metadata, and only from the right namespace
    spaces = keys.load_named_spaces(conn, ROOT / overlay['frozenMetadata']['dump'])
    for space, block in overlay['keySpaces'].items():
        assert block['enumType'] == spaces[space]['type']
        assert block['typeDefIndex'] == spaces[space]['typeDefIndex']
        assert block['declLine'] == spaces[space]['declLine'], (
            f'{space}: the enum moved from line {block["declLine"]} to '
            f'{spaces[space]["declLine"]} - rebuild the key overlay')
    assert [m['name'] for m in overlay['paramSpace']['constants']] == \
        [m['name'] for m in spaces['param']['members']]
    assert [m['value'] for m in overlay['paramSpace']['constants']] == \
        [m['value'] for m in spaces['param']['members']]
    assert [m['name'] for m in overlay['skillTypeSpace']['constants']] == \
        [m['name'] for m in spaces['skillType']['typeMembers']]
    assert len(overlay['skillTypeSpace']['categories']) == \
        len(spaces['skillType']['categories'])
    assert len(overlay['skillTypeSpace']['flags']) == len(spaces['skillType']['flags'])
    for entry in overlay['keys']:
        table = spaces[entry['namespace']]['byValue']
        assert entry['officialNameDefined'] == (entry['value'] in table), (
            f'{entry["key"]}: officialNameDefined disagrees with the frozen metadata')
        assert entry['officialName'] == table.get(entry['value']), (
            f'{entry["key"]}: {entry["officialName"]} is not the frozen name for '
            f'{entry["value"]} in {entry["namespace"]}')
        other = 'BKL' if entry['namespace'] == 'BKI' else 'BKI'
        if not entry['officialNameDefined']:
            assert entry['value'] not in spaces[other]['byValue'], (
                f'{entry["key"]} is undefined in its own space but is a defined '
                f'{other} member - the key space is mislabelled')
    for entry in overlay['params']:
        table = spaces['param']['byValue']
        assert entry['officialNameDefined'] == (entry['value'] in table)
        assert entry['officialName'] == table.get(entry['value'])
    for entry in overlay['statusConstants']:
        if entry['namespace'].startswith('skillType'):
            table = spaces['skillType']['typeByValue']
        else:
            table = spaces[entry['namespace']]['byValue']
        assert entry['officialName'] == table.get(entry['value']), (
            f'{entry["entry"]}: {entry["officialName"]} is not the frozen name')
    results['names'] = dict(keys=len(overlay['keys']), params=len(overlay['params']),
                            status=len(overlay['statusConstants']),
                            enumMembers={'BKI': len(spaces['BKI']['members']),
                                         'BKL': len(spaces['BKL']['members'])},
                            skillTypes=len(spaces['skillType']['typeMembers']))

    # 5. the two key spaces stay distinguishable, including where the numbers collide
    by_key = {entry['key']: entry for entry in overlay['keys']}
    assert len(by_key) == len(overlay['keys']), 'duplicate key id in the overlay'
    shared = {entry['value'] for entry in overlay['keys'] if entry['namespace'] == 'BKI'} & \
        {entry['value'] for entry in overlay['keys'] if entry['namespace'] == 'BKL'}
    assert shared, 'expected at least one number that exists in both key spaces'
    # The overlap is a metadata fact, not an accident of what combat reaches: BKI and BKL
    # really do declare members with the same numbers, which is why one flat lookup would
    # be wrong even when only one of the two is ever accessed.
    metadata_overlap = set(spaces['BKI']['byValue']) & set(spaces['BKL']['byValue'])
    assert len(metadata_overlap) >= 5, sorted(metadata_overlap)
    for value in sorted(shared):
        left, right = by_key[f'BKI:{value}'], by_key[f'BKL:{value}']
        assert left['officialName'] != right['officialName'], (
            f'BKI and BKL both map {value} to {left["officialName"]}')
        assert left['officialName'] == spaces['BKI']['byValue'].get(value)
        assert right['officialName'] == spaces['BKL']['byValue'].get(value)
    # the live query tool must keep them apart as well
    assert query.key_entry('BKI:62')['officialName'] == spaces['BKI']['byValue'].get(62)
    assert query.keys_index()['keys'], 'the query tool sees no key overlay'
    results['namespaces'] = dict(
        shared=sorted(shared),
        metadataOverlap=sorted(metadata_overlap),
        example={f'BKI:{value}': by_key[f'BKI:{value}']['officialName']
                 for value in sorted(shared)[:3]},
        exampleBKL={f'BKL:{value}': by_key[f'BKL:{value}']['officialName']
                    for value in sorted(shared)[:3]})

    # 6. a dynamic number is never given a meaning
    dynamic = 0
    for site in overlay['sites']:
        if site['keyState'] == 'dynamic':
            dynamic += 1
            assert site['key'] is None, f'{site["site"]} is dynamic but carries a key'
        else:
            assert site['key'] is not None and site['keyDerivation'], (
                f'{site["site"]} is stored as resolved but records no derivation')
    for site in overlay['paramSites']:
        if site['idState'] == 'dynamic':
            dynamic += 1
            assert site['paramId'] is None
    assert dynamic == len(report['dynamicSites']) + len(report['dynamicParamSites'])
    for entry in overlay['keys'] + overlay['params']:
        if entry['officialNameDefined']:
            continue
        role = entry.get('recoveredRole')
        assert not role or 'Not a' in role, (
            f'{entry.get("key", entry["value"])} has no official name but carries a role '
            f'that does not say it is not a game key: {role!r}')
    results['dynamic'] = dict(
        sites=dynamic, blackboard=[row['site'] for row in report['dynamicSites']],
        params=len(report['dynamicParamSites']),
        undefinedKeys=sorted(entry['key'] for entry in overlay['keys']
                             if not entry['officialNameDefined']))

    # 7. a documented role has evidence, and the evidence still says what was cited
    graded = 0
    for entry in overlay['keys'] + overlay['params'] + overlay['statusConstants']:
        confidence = entry.get('confidence')
        if 'key' in entry:
            label = entry['key']
        elif 'entry' in entry:
            label = entry['entry']
        else:
            label = f'Param {entry["value"]}'
        if confidence in (None, 'unknown'):
            assert not entry.get('recoveredRole'), f'{label}: a role without a grade'
            continue
        graded += 1
        assert entry.get('recoveredRole'), f'{label}: a grade without a role'
        assert entry.get('evidence'), f'{label}: grade {confidence} with no evidence'
        for reference in entry['evidence']:
            check_evidence(reference, results, label)
        for reference in list(entry.get('relatedChecks') or []) + \
                list(entry.get('relatedModels') or []):
            assert (ROOT / reference).is_file(), f'{label}: related file missing: {reference}'
    for entry in overlay['boardIndices']:
        if entry.get('recoveredRole'):
            graded += 1
            assert entry.get('evidence'), f'{entry["key"]}: a board role without evidence'
            for reference in entry['evidence']:
                check_evidence(reference, results, entry['key'])
        for place in entry['usedBy']:
            path_text, _, line_text = place.rpartition(':')
            path = ROOT / path_text
            assert path.is_file(), f'{entry["key"]}: model use points at {path_text}'
            lines = path.read_text(encoding='utf-8', errors='replace').splitlines()
            assert 1 <= int(line_text) <= len(lines), f'{entry["key"]}: {place} is stale'
    results['roles'] = dict(graded=graded, evidenceEntries=results.get('evidence', 0),
                            boardIndices=len(overlay['boardIndices']))

    # 8. the status space refers to keys that exist, and its readers resolve
    for entry in overlay['statusConstants']:
        for reference in entry['relatedKeys']:
            assert reference in by_key, f'{entry["entry"]}: unknown related key {reference}'
        for rva in list(entry['readers']) + list(entry['writers']):
            assert int(rva, 16) in methods, f'{entry["entry"]}: {rva} is not a method'
    assert any('BKI:62' in entry['relatedKeys'] for entry in overlay['statusConstants']), \
        'the status space no longer points at the three affected-skill keys'
    results['status'] = dict(entries=len(overlay['statusConstants']),
                             linked=sorted({reference for entry in overlay['statusConstants']
                                            for reference in entry['relatedKeys']}))

    # 9. the stub catalogue describes the index, not a guess
    for stub in overlay['stubs']:
        address = int(stub['address'], 16)
        if stub['classification'] == 'managed-method':
            assert methods.get(address) == stub['managedName'], (
                f'{stub["address"]}: managed name disagrees with the index')
        elif stub['classification'] == 'unidentified-native-address':
            assert address not in methods and stub['recordedVerdict'] is None
        for path in stub['declaredBy'] + stub['handlerFiles']:
            assert (ROOT / path).is_file(), f'{stub["address"]}: fixture missing: {path}'
        for sample in stub['assumedHandling']:
            sample_path = ROOT / sample['path']
            assert sample_path.is_file(), f'{stub["address"]}: {sample["path"]} is missing'
            lines = sample_path.read_text(encoding='utf-8', errors='replace').splitlines()
            assert 1 <= sample['line'] <= len(lines), (
                f'{stub["address"]}: {sample["path"]}:{sample["line"]} is past the end')
            assert sample['text'] in lines[sample['line'] - 1], (
                f'{stub["address"]}: {sample["path"]}:{sample["line"]} no longer shows the '
                f'stub handling it was recorded from')
        if stub['fixtureNames']:
            assert any(path.endswith('.py') for path in stub['declaredBy'])
    declared = {name for stub in overlay['stubs'] for name in stub['fixtureNames']}
    assert {'array_alloc', 'il2cpp_class_init', 'il2cpp_method_init'} <= declared, \
        f'the documented fixture stub names disappeared: {sorted(declared)}'
    results['stubs'] = dict(distinct=len(overlay['stubs']), declared=len(declared),
                            classified={})
    for stub in overlay['stubs']:
        bucket = results['stubs']['classified']
        bucket[stub['classification']] = bucket.get(stub['classification'], 0) + 1

    # 10. retrieval: the evidence body must not move, the block must stay affordable
    ka = native.open_db(native.DEFAULT_OUT)
    index = native.load_key_index(native.DEFAULT_KEYS)
    assert index, 'the brief loader sees no key overlay'
    surfaced = {}
    for topic in BRIEF_TOPICS:
        terms = [word for part in [topic] for word in native.sanitize(part).split()]
        sections = native.build_topic_sections(ka, terms, 900, 10,
                                               native.load_overlay(native.DEFAULT_OVERLAY),
                                               index)
        evidence = [section for section in sections if section[0] != 'keys']
        body, used, _dropped, _trimmed = native.fit_sections(evidence, 900)
        baseline, _used, _d, _t = native.fit_sections(evidence, 900)
        assert body == baseline
        block, cost = native.fit_key_block(sections, 900)
        allowance = max(55, min(int(900 * native.KEY_BLOCK_SHARE), 130))
        assert cost <= allowance, f'{topic}: the key block costs {cost} > {allowance}'
        assert 'BKI:' in block or 'Param `' in block, (
            f'{topic}: no numeric key surfaced in the brief')
        surfaced[topic] = dict(cost=cost, evidenceTokens=used, lines=block.count('\n') + 1)
    results['retrieval'] = dict(topics=surfaced, blockShare=native.KEY_BLOCK_SHARE,
                                evidenceBodyUnchanged=True)

    CHECK_OUT = keys.OVERLAY_DIR / 'keys-checks.json'
    CHECK_OUT.write_text(json.dumps(dict(schema='ka-combat-keys-checks-1', results=results,
                                         output=str(CHECK_OUT)), indent=1) + '\n',
                         encoding='utf-8')
    print(json.dumps(dict(
        blackboardSites=results['sites']['blackboard'],
        blackboardFunctions=overlay['counts']['blackboardFunctions'],
        paramSites=results['sites']['params'], keys=results['names']['keys'],
        params=results['names']['params'], status=results['status']['entries'],
        boardIndices=results['roles']['boardIndices'], stubs=results['stubs']['distinct'],
        dynamic=results['dynamic']['sites'],
        sharedNumbers=results['namespaces']['shared'],
        stableDigest=results['determinism']['stableDigest'],
        output=str(CHECK_OUT))))


if __name__ == '__main__':
    sys.exit(main())
