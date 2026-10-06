"""Regression for the combat overlay and its linkage to the canonical native index.

The overlay owns combat semantics only; every native fact now comes from
`RE-evidence/20260920-native-index/build/ka-index.sqlite`. This check therefore asserts two
things at once:

  * the overlay still resolves every native entity it claims, without inventing identities -
    real methods resolve to `methods`, non-method windows resolve to `code_windows`, and no
    address that the index cannot identify is silently promoted;
  * the overlay is still the overlay of the *current* native index - the pinned content
    digest matches the database on disk, so a regenerated index without a regenerated
    overlay (or the reverse) fails here rather than producing quietly wrong answers.

Independent assertions that predate the consolidation are kept deliberately: the 503 pass-1
audit identities, the 505 pass-1 slices, the delegated 26-call-site gauge caller map, and the
two documented `DrawGauges` call sites.
"""
import hashlib
import json
import sqlite3
from pathlib import Path

import build_combat_registry as builder
import combat_native as query
from combat_run_manifest import NATIVE_SHA256, canonical_hash

OUT = builder.OUT_DIR
CHECKS = OUT / 'registry-checks.json'
GAUGE_CALLERS = builder.EVIDENCE / '20260919-battle-hud/deepseek/callers.json'
NATIVE_DB = builder.NATIVE_DB


def main():
    overlay = json.loads(builder.REGISTRY_PATH.read_text(encoding='utf-8'))
    report = json.loads(builder.REPORT_PATH.read_text(encoding='utf-8'))
    annotations = json.loads(builder.ANNOTATIONS_PATH.read_text(encoding='utf-8'))
    assert overlay['schema'] == builder.SCHEMA, overlay['schema']
    by_rva = {int(row['rva'], 16): row for row in overlay['functions']}
    assert len(by_rva) == len(overlay['functions']), 'duplicate RVA in the overlay'

    index = builder.NativeIndex()
    conn = index.conn
    results = {}

    # 1. binary identity, on both sides
    frozen = builder.ROOT / 'RE-evidence/G2.1/39257e72291d/inputs/libil2cpp.so'
    live = hashlib.sha256(frozen.read_bytes()).hexdigest()
    assert live == NATIVE_SHA256, (live, NATIVE_SHA256)
    assert index.binary_sha256() == NATIVE_SHA256
    assert overlay['nativeIndex']['binarySha256'] == NATIVE_SHA256
    results['binary'] = dict(sha256=NATIVE_SHA256, nativeIndexRecorded=True, verified=True)

    # 2. the overlay is the overlay of the current native index
    manifest = builder.NativeIndex().manifest
    assert overlay['nativeIndex']['contentDigest'] == manifest['contentDigest'], \
        'the overlay pins a different native content digest than the manifest on disk'
    assert overlay['nativeIndex']['sourceSetDigest'] == manifest['sourceSetDigest']
    results['nativeLinkage'] = dict(contentDigest=manifest['contentDigest'],
                                    sourceSetDigest=manifest['sourceSetDigest'],
                                    generatedUtc=manifest['generatedUtc'], linked=True)

    # 3. every claimed identity resolves, and nothing is invented
    methods = {row[0] for row in conn.execute('SELECT rva FROM methods')}
    windows = {row[0] for row in conn.execute('SELECT rva FROM code_windows')}
    kinds = {}
    for rva, row in by_rva.items():
        kinds[row['kind']] = kinds.get(row['kind'], 0) + 1
        if row['kind'] == 'method':
            assert rva in methods, f"overlay calls {hex(rva)} a method but the index does not"
        elif row['kind'] == 'code-window':
            assert rva in windows, f"overlay calls {hex(rva)} a window but the index does not"
            assert rva not in methods, f"{hex(rva)} is a real method and must not be a window"
        else:
            raise AssertionError(f"unexpected overlay kind {row['kind']!r} at {hex(rva)}")
    assert kinds.get('method', 0) >= 1125, kinds
    assert kinds.get('code-window', 0) >= 70, kinds
    results['identities'] = dict(byKind=kinds, methodsInIndex=len(methods), windowsInIndex=len(windows))

    # 4. the pass-1 sets, independently
    audit = {row[0] for row in conn.execute('SELECT rva FROM audit_identity')}
    assert len(audit) == 503, len(audit)
    assert audit <= set(by_rva), sorted(hex(r) for r in audit - set(by_rva))[:5]
    pass1_slices = sorted((builder.EVIDENCE / '20260912-combat').glob('*.asm'))
    assert len(pass1_slices) == 505, len(pass1_slices)
    artifacts = {row[0] for row in conn.execute('SELECT rva FROM artifacts')}
    missing = [path.stem for path in pass1_slices if int(path.stem, 16) not in artifacts]
    assert not missing, missing[:5]
    results['pass1'] = dict(auditIdentities=len(audit), slices=len(pass1_slices),
                            auditInOverlay=len(audit & set(by_rva)))

    # 5. non-method combat windows resolve to explicit windows (never to fake methods)
    manifest_rvas = set()
    for path in sorted((builder.EVIDENCE).rglob('slices.json')):
        if '20260920-native-index' in str(path):
            continue
        for item in json.loads(path.read_text(encoding='utf-8'))['slices']:
            manifest_rvas.add(int(item['rva'], 16))
    non_method_manifest = sorted(rva for rva in manifest_rvas if rva not in methods)
    bad = [hex(rva) for rva in non_method_manifest if by_rva.get(rva, {}).get('kind') != 'code-window']
    assert not bad, bad[:5]
    orphans = [0x11725bc, 0x12cdef8]
    assert all(by_rva.get(rva, {}).get('kind') == 'code-window' for rva in orphans)
    results['nonMethodWindows'] = dict(manifestNonMethod=len(non_method_manifest),
                                       orphans=len(orphans), resolvedAsWindows=True)

    # 6. annotations resolve and keep their grades
    for key, annotation in annotations['annotations'].items():
        rva = int(key, 16)
        assert rva in by_rva, f'annotation for an unknown address {key}'
        assert annotation.get('purpose') and annotation.get('confidence'), key
        assert annotation.get('evidence'), key
        assert by_rva[rva].get('annotation') == annotation, key
    assert not overlay['conflicts'].get('orphanAnnotations'), overlay['conflicts']['orphanAnnotations']
    results['annotations'] = dict(entries=len(annotations['annotations']),
                                  withChecks=overlay['counts']['withChecks'],
                                  withModels=overlay['counts']['withModels'])
    assert overlay['counts']['withChecks'] > 500 and overlay['counts']['withModels'] > 20

    # 7. the delegated gauge caller map, read from the canonical graph
    stored = json.loads(GAUGE_CALLERS.read_text(encoding='utf-8'))
    edge_rows = {(row[0], row[1]): row[2] for row in
                 conn.execute('SELECT site, callee_rva, caller_rva FROM calls')}
    pairs = 0
    for target_row in stored['targets']:
        for site_row in target_row.get('call_sites') or []:
            key = (int(site_row['call_site_rva'], 16), int(site_row['target_rva'], 16))
            assert key in edge_rows, f'call site missing from the canonical graph: {key}'
            assert edge_rows[key] == int(site_row['containing_method']['address'], 16), key
            pairs += 1
    assert pairs == 26, pairs
    results['gaugeCallerMap'] = dict(matched=pairs, expected=26)

    # 8. the two documented DrawGauges call sites
    gauges = {(row[0], row[1]) for row in
              conn.execute('SELECT site, caller_rva FROM calls WHERE callee_rva=?', (0x1587878,))}
    assert {(0x1587490, 0x1587184), (0x1587744, 0x1587184)} <= gauges, sorted(
        (hex(a), hex(b)) for a, b in gauges)
    results['drawGaugesCallers'] = sorted(hex(site) for site, _caller in gauges)

    # 9. representative queries through the merged tool
    representatives = (('DecideNextState', 0x15835a8), ('0x168c460', 0x168c460),
                       ('0x14b8e4c', 0x14b8e4c), ('DrawGauges', 0x1587878))
    for text, expected in representatives:
        rva, _kind = query.resolve(text, combat_only=True)
        assert rva == expected, (text, hex(rva) if rva else None, hex(expected))
    # The native index is game-wide, so a bare name that two classes share stays ambiguous
    # there and the message says which candidate is the combat one.
    try:
        query.resolve('DecideNextState')
    except SystemExit as error:
        message = str(error)
        assert 'ambiguous' in message and '0x1496628' in message and '0x15835a8' in message, message
    else:
        raise AssertionError('expected the game-wide index to report the shared short name')
    unresolved_window, kind = query.resolve('0x14b912c')      # B3 jump-table arm
    assert kind == 'code-window', kind
    results['queries'] = dict(representatives=len(representatives),
                              windowResolves=kind, window=hex(unresolved_window))

    # 10. determinism and staleness
    first = builder.build()
    second = builder.build()
    # The overlay records its own generation timestamp, so determinism is asserted over the
    # stable projection (native linkage + functions + conflicts) rather than the raw file.
    assert canonical_hash(builder.stable_projection(first['overlay'])) == canonical_hash(
        builder.stable_projection(second['overlay'])), 'the overlay build is not deterministic'
    assert canonical_hash(builder.stable_projection(overlay)) == canonical_hash(
        builder.stable_projection(first['overlay'])), 'the stored overlay is stale'
    assert report['stableDigest'] == first['report']['stableDigest'], 'stable digest drift'
    results['determinism'] = dict(stableDigest=report['stableDigest'],
                                  functions=overlay['counts']['functions'], reproducible=True)

    CHECKS.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(schema='ka-combat-overlay-checks-1', results=results,
                   functions=overlay['counts']['functions'], byKind=kinds,
                   output=str(CHECKS))
    CHECKS.write_text(json.dumps(payload, indent=1) + '\n', encoding='utf-8')
    print(json.dumps(dict(functions=overlay['counts']['functions'], byKind=kinds,
                          annotated=results['annotations']['entries'],
                          withChecks=results['annotations']['withChecks'],
                          withModels=results['annotations']['withModels'],
                          gaugeCallersMatched=26,
                          drawGaugesCallers=results['drawGaugesCallers'],
                          reproducible=True, output=str(CHECKS))))


if __name__ == '__main__':
    main()
