"""Regression for the canonical native index that the combat overlay depends on.

The combat gate protects combat behaviour; this check protects the *foundation* that combat
queries now read from. It asserts the properties the consolidation promised, and it fails on
evidence drift the old index could not detect:

  * frozen binary identity, on the file, in `meta` and in the manifest;
  * the documented population (127,751 IL2CPP methods, types, fields, aliases, mentions);
  * one direct call graph, with the method->method / method->non-method / unclaimed classes
    present and no intra-function branch stored as a call;
  * the quarantined copy and the archives are not indexed as evidence;
  * content-drift detection: a changed tool/evidence file fails here, a changed document is
    reported without failing (its only effect is citation line numbers);
  * the retrieval surface still answers: RVA lookup, topic brief, sections, packages.
"""
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

import ka_index as native
from combat_run_manifest import NATIVE_SHA256

ROOT = native.DEFAULT_ROOT
OUT = native.DEFAULT_OUT
DB = OUT / native.DB_NAME
MANIFEST = OUT / 'index-manifest.json'
CHECKS = OUT / 'native-layer-checks.json'
EXPECTED_METHODS = 127751
EXCLUDED_FROM_EVIDENCE = ('reverse engineering with deepseek/', 'RE-archive/', 'KA-Legacy-Archive/',
                          'RE-evidence/20260919-combat-registry/',
                          'RE-evidence/20260920-native-index/')


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(f'file:{DB.as_posix()}?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def main():
    assert DB.is_file(), f'{DB} missing - build the native index first'
    assert MANIFEST.is_file(), f'{MANIFEST} missing'
    conn = connect()
    manifest = json.loads(MANIFEST.read_text(encoding='utf-8'))
    results = {}

    # 1. binary provenance
    frozen = native.native_dir(ROOT) / 'inputs' / 'libil2cpp.so'
    live = hashlib.sha256(frozen.read_bytes()).hexdigest()
    recorded = conn.execute("SELECT value FROM meta WHERE key='binary_sha256'").fetchone()['value']
    assert live == NATIVE_SHA256 == recorded == manifest['binarySha256'], (live, recorded)
    results['binary'] = dict(sha256=NATIVE_SHA256, file=True, meta=True, manifest=True)

    # 2. population and schema
    counts = {table: conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
              for table in ('methods', 'types', 'fields', 'method_alias', 'audit_identity',
                            'artifacts', 'code_windows', 'code_map', 'calls', 'mentions',
                            'doc_sections', 'helper_findings', 'native_regions', 'files')}
    assert counts['methods'] == EXPECTED_METHODS, counts['methods']
    assert counts['code_map'] == EXPECTED_METHODS
    assert counts['types'] > 9000 and counts['fields'] > 28000 and counts['method_alias'] > 250000
    assert counts['audit_identity'] == 503
    assert counts['mentions'] > 100000 and counts['doc_sections'] > 5000
    assert counts['artifacts'] > 1000 and counts['code_windows'] >= 70
    assert counts['helper_findings'] == 12
    assert manifest['counts']['methods'] == EXPECTED_METHODS
    results['counts'] = counts

    # 3. one direct call graph, with the classes and no intra-function branches
    classes = {f"{row['source_kind']}->{row['target_kind']}": row['n'] for row in conn.execute(
        'SELECT source_kind, target_kind, COUNT(*) AS n FROM calls GROUP BY 1, 2')}
    assert classes.get('method->method', 0) > 250000, classes
    assert classes.get('method->non-method', 0) > 500000, classes
    assert classes.get('unclaimed->non-method', 0) > 10000, classes
    assert conn.execute("SELECT COUNT(*) FROM calls WHERE kind='branch'").fetchone()[0] > 30000
    intra = conn.execute(
        "SELECT COUNT(*) FROM calls c JOIN code_map m ON m.rva=c.caller_rva "
        "WHERE c.kind='branch' AND c.callee_rva BETWEEN m.first_instr AND m.last_instr").fetchone()[0]
    assert intra == 0, f'{intra} intra-function branches are stored as calls'
    unresolved_sources = conn.execute(
        "SELECT COUNT(*) FROM calls WHERE caller_rva IS NULL AND kind='branch'").fetchone()[0]
    assert unresolved_sources == 0, 'unclaimed-region branches must not be stored'
    results['callGraph'] = dict(classes=classes, branchEdges=conn.execute(
        "SELECT COUNT(*) FROM calls WHERE kind='branch'").fetchone()[0],
        intraFunctionBranches=0, unclaimedBranches=0,
        distinctNonMethodTargets=conn.execute(
            "SELECT COUNT(DISTINCT callee_rva) FROM calls WHERE target_kind='non-method'").fetchone()[0])
    # the documented DrawGauges pair, as a fixed point in the canonical graph
    gauges = {(row['site'], row['caller_rva']) for row in conn.execute(
        'SELECT site, caller_rva FROM calls WHERE callee_rva=?', (0x1587878,))}
    assert {(0x1587490, 0x1587184), (0x1587744, 0x1587184)} <= gauges, sorted(gauges)
    results['drawGaugesSites'] = sorted(hex(site) for site, _caller in gauges)

    # 4. excluded material really is excluded
    offenders = []
    for row in conn.execute('SELECT rel, kind FROM files'):
        rel = row['rel'].replace('\\', '/')
        if rel.startswith('RE-evidence/20260920-native-index/') and row['kind'] == 'derived':
            continue          # this index's own generated listings, recorded as derived
        if any(rel.startswith(bad) for bad in EXCLUDED_FROM_EVIDENCE):
            offenders.append(rel)
    assert not offenders, offenders[:5]
    assert conn.execute("SELECT COUNT(*) FROM mentions WHERE rel LIKE 'reverse engineering with%'"
                        ).fetchone()[0] == 0
    results['excludedRoots'] = list(EXCLUDED_FROM_EVIDENCE)

    # 5. content-drift detection
    hard, soft, missing = [], [], []
    for row in conn.execute('SELECT path, rel, kind, sha256 FROM files'):
        if native.is_volatile_output(row['rel']):
            continue          # the gate's own run output, rewritten every run
        path = Path(row['path'])
        if not path.is_file():
            missing.append(row['rel'])
            continue
        if row['sha256'] and native.sha256_of(path) != row['sha256']:
            (soft if row['kind'] == 'doc' else hard).append(row['rel'])
    assert not missing, missing[:5]
    assert not hard, ('indexed tool/evidence changed since the build - rerun '
                      f'ka_index.py build: {hard[:3]}')
    results['drift'] = dict(binary='same', hard=0, documents=len(soft), missing=0)

    # 6. the retrieval surface still answers
    describe = native.describe(conn, 0x1473234, 4)
    assert 'AddTreasure' in describe and 'calls (' in describe, describe[:200]
    search = list(conn.execute("SELECT rel, line FROM search WHERE search MATCH ? LIMIT 3",
                               ('"treasure"',)))
    assert search, 'full-text search returned nothing for a common term'
    sections = conn.execute('SELECT COUNT(*) FROM doc_sections WHERE rel LIKE ?',
                            ('%special-combat.md',)).fetchone()[0]
    assert sections > 50, sections
    payload = native.build_rva_sections(conn, 0x1587878, 400)
    assert payload, 'the brief builder returned no sections'
    results['retrieval'] = dict(describeLines=len(describe.splitlines()), ftsHits=len(search),
                                sections=sections, briefSections=len(payload))

    # 7. the manifest is self-consistent with the database
    assert manifest['contentDigest'] == conn.execute(
        "SELECT value FROM meta WHERE key='content_digest'").fetchone()['value']
    assert manifest['sourceSetDigest'] == conn.execute(
        "SELECT value FROM meta WHERE key='source_set_digest'").fetchone()['value']
    assert manifest['counts']['calls'] == counts['calls']
    results['manifest'] = dict(contentDigest=manifest['contentDigest'][:16],
                               sourceSetDigest=manifest['sourceSetDigest'][:16],
                               indexVersion=manifest['indexVersion'])

    CHECKS.parent.mkdir(parents=True, exist_ok=True)
    CHECKS.write_text(json.dumps(dict(schema='ka-native-layer-checks-1', results=results,
                                      output=str(CHECKS)), indent=1) + '\n', encoding='utf-8')
    print(json.dumps(dict(methods=counts['methods'], codeWindows=counts['code_windows'],
                          calls=counts['calls'], edgeClasses=classes,
                          mentions=counts['mentions'], files=counts['files'],
                          drift=results['drift'], contentDigest=manifest['contentDigest'][:16],
                          output=str(CHECKS))))


if __name__ == '__main__':
    main()
