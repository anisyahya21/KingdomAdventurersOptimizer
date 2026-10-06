"""Deterministic matched complete scratch SQLite corpus-artifact size measurement.

Reads only saved battle-binary corpus artifacts (manifest shards, baseline
evidence packets/metrics/definitions) and the candidate definitions file. Writes
only scratch SQLite databases plus status/report JSON under --output-dir. It never
opens a production database and never rewrites the saved corpus.

Three matched databases are built with byte-identical schema:
  legacy-before  original result/outcome column values
  binary-before  baseline saved payload packets + raw metadata
  binary-after   new candidate payload packets + same raw metadata
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pickle
import sqlite3
import sys
import time
import zlib
from concurrent.futures import ProcessPoolExecutor

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import strategy_battle_binary as binary
import strategy_payload_codec as legacy

CORPUS = HERE / 'studies' / 'battle_binary_corpus'
DEFAULT_CANDIDATE = HERE / 'battle_binary_definitions.json'
EXPECTED_BASELINE_SHA256 = 'b9f86ce61fd7c3cf26dcc45a9e8ab8c0a5b0cf71e14bd4ce73a9566566fbe7f0'
PAGE_SIZE = 4096
SCHEMA_VERSION = 1
DB_NAMES = ('legacy-before', 'binary-before', 'binary-after')
DIAGNOSTIC_NAMES = frozenset(('status.json', 'report.json', 'build-state.json'))
EXCLUDED_SUFFIXES = ('.log', '.tmp')

CREATE_RECORDS = (
    'CREATE TABLE records('
    'table_name TEXT NOT NULL,'
    'rowid INTEGER NOT NULL,'
    'payload BLOB,'
    'result BLOB,'
    'outcome BLOB,'
    'metadata TEXT NOT NULL,'
    'candidate_id TEXT,'
    'experiment_id INTEGER,'
    'PRIMARY KEY(table_name,rowid))'
)
CREATE_INDEXES = (
    'CREATE INDEX idx_records_candidate_id ON records(candidate_id)',
    'CREATE INDEX idx_records_experiment_id ON records(experiment_id)',
)
CREATE_DEFINITIONS = (
    'CREATE TABLE definitions('
    'variant TEXT PRIMARY KEY,'
    'registry_version INTEGER NOT NULL,'
    'codec_version INTEGER NOT NULL,'
    'raw_sha256 TEXT NOT NULL,'
    'zlib_sha256 TEXT NOT NULL,'
    'raw_bytes INTEGER NOT NULL,'
    'zlib_bytes INTEGER NOT NULL,'
    'zlib BLOB NOT NULL)'
)
INSERT_RECORDS = 'INSERT INTO records VALUES(?,?,?,?,?,?,?,?)'

_START = time.perf_counter()
_STAGE = None
_STAGE_START = _START
_STAGE_DONE = 0
_OUT = None


def atomic_json(path, value):
    path = Path(path)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True), encoding='utf-8')
    for attempt in range(5):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.05)


def sha256_bytes(raw):
    return hashlib.sha256(raw).hexdigest()


def sha256_file(path):
    return sha256_bytes(Path(path).read_bytes())


def status(stage, done, total, error=None):
    global _STAGE, _STAGE_START, _STAGE_DONE
    now = time.perf_counter()
    if stage != _STAGE:
        _STAGE = stage
        _STAGE_START = now
        _STAGE_DONE = done
    duration = now - _STAGE_START
    delta = done - _STAGE_DONE
    rate = (delta / duration) if (duration > 0.5 and delta > 0) else None
    eta = ((total - done) / rate) if (rate and total and total > done) else None
    if _OUT is not None:
        atomic_json(_OUT / 'status.json', {
            'stage': stage,
            'completed': done,
            'total': total,
            'elapsed': now - _START,
            'rate': rate,
            'eta': eta,
            'error': error,
        })


def load_candidate_definitions(path):
    doc = json.loads(Path(path).read_text(encoding='utf-8'))
    raw = binary.dumps(doc).encode('utf-8')
    return doc, raw


def resolve_baseline_definitions(report):
    frozen = (report or {}).get('frozenDefinitions') or {}
    if frozen.get('sha256') and frozen['sha256'] != EXPECTED_BASELINE_SHA256:
        raise ValueError('report frozenDefinitions sha256 does not match the pinned baseline')
    bases = []
    if (report or {}).get('evidenceDirectory'):
        bases.append(Path(report['evidenceDirectory']))
    bases.append(CORPUS / EXPECTED_BASELINE_SHA256[:16])
    bases.append(CORPUS)
    seen = set()
    for base in bases:
        if str(base) in seen:
            continue
        seen.add(str(base))
        for name, compressed in (('definitions.json', False), ('definitions.zlib', True)):
            path = base / name
            if not path.exists():
                continue
            raw = path.read_bytes()
            if compressed:
                raw = zlib.decompress(raw)
            digest = sha256_bytes(raw)
            if digest != EXPECTED_BASELINE_SHA256:
                raise ValueError('baseline definitions snapshot at %s hashes %s, expected %s' % (path, digest, EXPECTED_BASELINE_SHA256))
            return raw, digest, str(path)
    raise FileNotFoundError('baseline definitions snapshot not found in evidence directory or sibling corpus; refusing to guess')


def ordered_shards(shards):
    return sorted(shards, key=lambda s: (s['file'].split('-', 1)[0], s['file']))


def plan_takes(shards, limit):
    takes = []
    remaining = limit if (limit and limit > 0) else None
    for shard in shards:
        if remaining is None:
            takes.append(shard['rows'])
        elif remaining <= 0:
            takes.append(0)
        else:
            take = min(shard['rows'], remaining)
            takes.append(take)
            remaining -= take
    return takes


# ---------------------------------------------------------------------------
# Worker-side build: decode/encode/verify new packets. No SQLite, no writes.
# ---------------------------------------------------------------------------
_W = {}


def worker_init(baseline_doc, candidate_doc, corpus_dir, evidence_dir, baseline_sha, db_paths):
    _W['baseline'] = binary.RecordEncoder(baseline_doc)
    _W['candidate'] = binary.RecordEncoder(candidate_doc)
    _W['corpus'] = Path(corpus_dir)
    _W['evidence'] = Path(evidence_dir)
    _W['baseline_sha'] = baseline_sha
    _W['db_paths'] = dict(db_paths or {})
    _W['conns'] = {}


def _decode_cell(cell):
    if cell is None:
        return None
    return legacy.decode_text(cell)


def _value_from_cells(left, right):
    return {
        'result': json.loads(left) if left is not None else None,
        'outcome': json.loads(right) if right is not None else None,
    }


def _meta_from_record(record):
    row = record['columns']
    return {
        'columns': {k: v for k, v in row.items() if k not in ('result', 'outcome')},
        'links': record['links'],
    }


def _exact(label, left, right):
    try:
        binary.exact(left, right)
    except AssertionError as exc:
        raise AssertionError('exact typed equality failed: %s' % label) from exc


def _build_shard(filename, take):
    raw = (_W['corpus'] / filename).read_bytes()
    shard_sha = sha256_bytes(raw)
    records = pickle.loads(raw)
    full_len = len(records)
    records = records[:take]
    packed_raw = (_W['evidence'] / (filename + '.packed')).read_bytes()
    packed_sha = sha256_bytes(packed_raw)
    packets = pickle.loads(packed_raw)
    metrics = json.loads((_W['evidence'] / (filename + '.metrics.json')).read_text(encoding='utf-8'))
    if metrics.get('definitionHash') != _W['baseline_sha']:
        raise ValueError('%s metrics definitionHash is not the pinned baseline' % filename)
    if metrics.get('packedSha256') != packed_sha:
        raise ValueError('%s packed sha256 differs from its metrics record' % filename)
    if len(metrics['rows']) != full_len:
        raise ValueError('%s metrics row count differs from its shard' % filename)
    if len(packets) != full_len:
        raise ValueError('%s baseline packet count differs from its shard' % filename)
    rows = []
    counters = {'candidateExact': 0, 'baselineExact': 0, 'metadataExact': 0, 'multiExperiment': 0}
    for record, (baseline_packet, metadata_packet) in zip(records, packets):
        row = record['columns']
        value = _value_from_cells(_decode_cell(row.get('result')), _decode_cell(row.get('outcome')))
        _exact('%s rowid=%s baseline payload' % (filename, row.get('rowid')), value, _W['baseline'].decode(baseline_packet))
        counters['baselineExact'] += 1
        candidate_packet = _W['candidate'].encode(value)
        _exact('%s rowid=%s candidate roundtrip' % (filename, row.get('rowid')), value, _W['candidate'].decode(candidate_packet))
        counters['candidateExact'] += 1
        meta = _meta_from_record(record)
        _exact('%s rowid=%s metadata packet' % (filename, row.get('rowid')), meta, _W['baseline'].decode(metadata_packet))
        counters['metadataExact'] += 1
        experiments = sorted({l['experiment_id'] for l in record['links'] if l.get('experiment_id') is not None})
        if len(experiments) > 1:
            counters['multiExperiment'] += 1
        rows.append((
            record['table'],
            row['rowid'],
            baseline_packet,
            candidate_packet,
            binary.dumps(meta),
            row.get('candidate_id'),
            row.get('experiment_id') if row.get('experiment_id') is not None else (experiments[0] if experiments else None),
            row.get('result'),
            row.get('outcome'),
        ))
    return {'file': filename, 'shardSha': shard_sha, 'packedSha': packed_sha, 'rows': rows, 'counters': counters}


def build_job(job):
    return _build_shard(job[0], job[1])


# ---------------------------------------------------------------------------
# Worker-side verification: read back persisted scratch rows and compare.
# ---------------------------------------------------------------------------
def _conn(name):
    conns = _W['conns']
    if name not in conns:
        path = Path(_W['db_paths'][name]).resolve()
        conn = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
        conn.execute('PRAGMA query_only=ON')
        conns[name] = conn
    return conns[name]


def _query_rows(name, table, low, high):
    return _conn(name).execute(
        'SELECT table_name,rowid,payload,result,outcome,metadata FROM records '
        'WHERE table_name=? AND rowid BETWEEN ? AND ? ORDER BY rowid',
        (table, low, high),
    ).fetchall()


def _verify_shard(filename, take):
    raw = (_W['corpus'] / filename).read_bytes()
    shard_sha = sha256_bytes(raw)
    records = pickle.loads(raw)[:take]
    if not records:
        return {'counters': {'rowsVerified': 0}, 'shardSha': shard_sha}
    table = records[0]['table']
    rowids = [r['columns']['rowid'] for r in records]
    low, high = min(rowids), max(rowids)
    fetched = {name: _query_rows(name, table, low, high) for name in DB_NAMES}
    for name in DB_NAMES:
        if len(fetched[name]) != len(records):
            raise ValueError('%s persisted %s row count %d, expected %d' % (filename, name, len(fetched[name]), len(records)))
    counters = {'rowsVerified': 0, 'afterExact': 0, 'beforeExact': 0, 'legacyExact': 0, 'metadataExact': 0}
    for record, after, before, legacy_row in zip(records, fetched['binary-after'], fetched['binary-before'], fetched['legacy-before']):
        rowid = record['columns']['rowid']
        if not (after[1] == before[1] == legacy_row[1] == rowid):
            raise ValueError('%s persisted rowid mismatch at %s' % (filename, rowid))
        meta = _meta_from_record(record)
        value = _value_from_cells(_decode_cell(record['columns'].get('result')), _decode_cell(record['columns'].get('outcome')))
        _exact('%s rowid=%s persisted candidate payload' % (filename, rowid), value, _W['candidate'].decode(after[2]))
        counters['afterExact'] += 1
        _exact('%s rowid=%s persisted baseline payload' % (filename, rowid), value, _W['baseline'].decode(before[2]))
        counters['beforeExact'] += 1
        legacy_value = _value_from_cells(_decode_cell(legacy_row[3]), _decode_cell(legacy_row[4]))
        _exact('%s rowid=%s persisted legacy result/outcome' % (filename, rowid), value, legacy_value)
        counters['legacyExact'] += 1
        for name, stored in (('binary-after', after), ('binary-before', before), ('legacy-before', legacy_row)):
            _exact('%s rowid=%s persisted %s metadata' % (filename, rowid, name), meta, json.loads(stored[5]))
            counters['metadataExact'] += 1
        counters['rowsVerified'] += 1
    return {'counters': counters, 'shardSha': shard_sha, 'file': filename}


def verify_job(job):
    return _verify_shard(job[0], job[1])


# ---------------------------------------------------------------------------
# Parent-side schema, writing, measurement.
# ---------------------------------------------------------------------------
def open_build_db(tmp_path, role, doc, raw, zlib_bytes):
    tmp_path = Path(tmp_path)
    tmp_path.unlink(missing_ok=True)
    db = sqlite3.connect(str(tmp_path))
    db.execute('PRAGMA page_size=%d' % PAGE_SIZE)
    db.execute('PRAGMA auto_vacuum=NONE')
    db.execute('PRAGMA journal_mode=DELETE')
    db.execute(CREATE_RECORDS)
    for statement in CREATE_INDEXES:
        db.execute(statement)
    db.execute(CREATE_DEFINITIONS)
    if role != 'legacy':
        db.execute(
            'INSERT INTO definitions VALUES(?,?,?,?,?,?,?,?)',
            (role, int(doc['registryVersion']), int(doc['codecVersion']),
             sha256_bytes(raw), sha256_bytes(zlib_bytes), len(raw), len(zlib_bytes), zlib_bytes),
        )
    db.commit()
    return db


def insert_rows(conns, rows):
    legacy_rows = []
    before_rows = []
    after_rows = []
    for table, rowid, baseline_packet, candidate_packet, metadata, candidate_id, experiment_id, result, outcome in rows:
        legacy_rows.append((table, rowid, None, result, outcome, metadata, candidate_id, experiment_id))
        before_rows.append((table, rowid, baseline_packet, None, None, metadata, candidate_id, experiment_id))
        after_rows.append((table, rowid, candidate_packet, None, None, metadata, candidate_id, experiment_id))
    conns['legacy-before'].executemany(INSERT_RECORDS, legacy_rows)
    conns['binary-before'].executemany(INSERT_RECORDS, before_rows)
    conns['binary-after'].executemany(INSERT_RECORDS, after_rows)


def build_databases(out, ordered, takes, baseline_doc, baseline_raw, candidate_doc, candidate_raw,
                    evidence_dir, manifest_sha, baseline_sha, candidate_sha, workers):
    tmp_paths = {name: out / (name + '.sqlite.tmp') for name in DB_NAMES}
    final_paths = {name: out / (name + '.sqlite') for name in DB_NAMES}
    for path in list(tmp_paths.values()) + list(final_paths.values()):
        path.unlink(missing_ok=True)
    baseline_zlib = zlib.compress(baseline_raw, 9)
    candidate_zlib = zlib.compress(candidate_raw, 9)
    conns = {}
    for name in DB_NAMES:
        if name == 'binary-after':
            conns[name] = open_build_db(tmp_paths[name], 'candidate', candidate_doc, candidate_raw, candidate_zlib)
        else:
            conns[name] = open_build_db(tmp_paths[name], 'legacy' if name == 'legacy-before' else 'baseline', baseline_doc, baseline_raw, baseline_zlib)
    expected = {s['file']: s['sha256'] for s in ordered}
    jobs = [(s['file'], t) for s, t in zip(ordered, takes) if t > 0]
    total = sum(takes)
    counters = {'rows': 0, 'shards': 0, 'candidateExact': 0, 'baselineExact': 0, 'metadataExact': 0, 'multiExperiment': 0}
    try:
        with ProcessPoolExecutor(max_workers=workers, initializer=worker_init,
                                 initargs=(baseline_doc, candidate_doc, str(CORPUS), str(evidence_dir), baseline_sha, {})) as pool:
            for result in pool.map(build_job, jobs, chunksize=1, buffersize=workers*2):
                if result['shardSha'] != expected[result['file']]:
                    raise ValueError('%s shard sha256 changed since manifest was written' % result['file'])
                insert_rows(conns, result['rows'])
                for name in DB_NAMES:
                    conns[name].commit()
                counters['rows'] += len(result['rows'])
                counters['shards'] += 1
                for key in ('candidateExact', 'baselineExact', 'metadataExact', 'multiExperiment'):
                    counters[key] += result['counters'][key]
                status('Building matched scratch SQLite databases', counters['rows'], total)
    finally:
        for name in DB_NAMES:
            conns[name].close()
    state = {'schemaVersion': SCHEMA_VERSION, 'databases': {}}
    for name in DB_NAMES:
        os.replace(tmp_paths[name], final_paths[name])
        state['databases'][name] = {'sha256': sha256_file(final_paths[name]), 'bytes': final_paths[name].stat().st_size}
    state['rows'] = counters['rows']
    state['inputsDigest'] = manifest_sha
    state['baselineDefinitionsSha256'] = baseline_sha
    state['candidateDefinitionsSha256'] = candidate_sha
    return counters, state


def dbstat_report(db):
    try:
        rows = db.execute('SELECT name,SUM(pgsize) AS bytes,COUNT(*) AS pages FROM dbstat GROUP BY name ORDER BY name').fetchall()
    except sqlite3.OperationalError as exc:
        return {'available': False, 'reason': str(exc)}
    return {'available': True, 'objects': [{'name': n, 'bytes': b, 'pages': p} for n, b, p in rows]}


def measure_database(path, role, expected_raw_sha256):
    path = Path(path)
    db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
    try:
        page_size = db.execute('PRAGMA page_size').fetchone()[0]
        page_count = db.execute('PRAGMA page_count').fetchone()[0]
        freelist = db.execute('PRAGMA freelist_count').fetchone()[0]
        tables = {}
        for table in ('ea_sample', 'ea_holdout'):
            tables[table] = db.execute('SELECT count(*) FROM records WHERE table_name=?', (table,)).fetchone()[0]
        definitions_verified = True
        definitions = []
        for variant, raw_sha, zlib_sha, raw_bytes, zlib_bytes, stored in db.execute(
                'SELECT variant,raw_sha256,zlib_sha256,raw_bytes,zlib_bytes,zlib FROM definitions ORDER BY variant'):
            raw = zlib.decompress(stored)
            ok = (sha256_bytes(raw) == raw_sha and sha256_bytes(stored) == zlib_sha
                  and len(raw) == raw_bytes and len(stored) == zlib_bytes and raw_sha == expected_raw_sha256)
            definitions_verified = definitions_verified and ok
            definitions.append({'variant': variant, 'rawSha256': raw_sha, 'zlibSha256': zlib_sha,
                                'rawBytes': raw_bytes, 'zlibBytes': zlib_bytes, 'verified': ok})
        definitions_verified = definitions_verified and len(definitions) == (0 if path.name.startswith('legacy-') else 1)
        stats = dbstat_report(db)
    finally:
        db.close()
    file_bytes = path.stat().st_size
    return {
        'file': path.name,
        'fileBytes': file_bytes,
        'pageSize': page_size,
        'pageCount': page_count,
        'freelistCount': freelist,
        'pageCountBytes': page_count * page_size,
        'usedBytes': (page_count - freelist) * page_size,
        'fileBytesEqualsPageCountBytes': file_bytes == page_count * page_size,
        'rowCounts': tables,
        'totalRows': sum(tables.values()),
        'definitionsVerified': definitions_verified,
        'definitions': definitions,
        'dbstat': stats,
    }


def scan_artifact_files(out):
    files = []
    total = 0
    for path in sorted(Path(out).iterdir()):
        if not path.is_file():
            continue
        if path.name in DIAGNOSTIC_NAMES or path.suffix in EXCLUDED_SUFFIXES:
            continue
        size = path.stat().st_size
        total += size
        files.append({'file': path.name, 'bytes': size})
    return files, total


def wal_shm_state(final_paths):
    state = {}
    for name, path in final_paths.items():
        for suffix in ('-wal', '-shm'):
            sidecar = Path(str(path) + suffix)
            state[name + suffix] = sidecar.stat().st_size if sidecar.exists() else 0
    return state


def run(args):
    global _OUT, _START
    out = Path(args.output_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    _OUT = out
    _START = time.perf_counter()
    status('Loading inputs', 0, 1)
    manifest_path = CORPUS / 'manifest.json'
    manifest_raw = manifest_path.read_bytes()
    manifest = json.loads(manifest_raw)
    manifest_sha = sha256_bytes(manifest_raw)
    report_path = CORPUS / 'report.json'
    baseline_report = json.loads(report_path.read_text(encoding='utf-8')) if report_path.exists() else {}
    evidence_dir = Path(baseline_report['evidenceDirectory']) if baseline_report.get('evidenceDirectory') else (CORPUS / EXPECTED_BASELINE_SHA256[:16])
    baseline_raw, baseline_sha, baseline_source = resolve_baseline_definitions(baseline_report)
    baseline_doc = json.loads(baseline_raw)
    candidate_doc, candidate_raw = load_candidate_definitions(args.definitions)
    candidate_sha = sha256_bytes(candidate_raw)
    ordered = ordered_shards(manifest['shards'])
    takes = plan_takes(ordered, args.limit)
    total = sum(takes)
    final_paths = {name: out / (name + '.sqlite') for name in DB_NAMES}
    inputs_digest = sha256_bytes(json.dumps({
        'schemaVersion': SCHEMA_VERSION,
        'manifestSha256': manifest_sha,
        'baselineSha256': baseline_sha,
        'candidateSha256': candidate_sha,
        'limit': args.limit or 0,
        'rows': total,
    }, sort_keys=True).encode('utf-8'))
    resume_state = None
    state_path = out / 'build-state.json'
    if state_path.exists() and not args.no_resume:
        try:
            stored = json.loads(state_path.read_text(encoding='utf-8'))
            if stored.get('inputsDigest') == inputs_digest and stored.get('rows') == total:
                valid = all(final_paths[name].exists() and sha256_file(final_paths[name]) == stored['databases'][name]['sha256'] for name in DB_NAMES)
                if valid:
                    resume_state = stored
        except (OSError, KeyError, ValueError):
            resume_state = None
    if resume_state is not None:
        status('Reusing completed scratch databases', total, total)
        build_counters = {'rows': total, 'shards': len(ordered), 'candidateExact': total, 'baselineExact': total,
                          'metadataExact': total, 'multiExperiment': None, 'reused': True}
    else:
        build_counters, resume_state = build_databases(out, ordered, takes, baseline_doc, baseline_raw, candidate_doc,
                                                       candidate_raw, evidence_dir, manifest_sha, baseline_sha, candidate_sha, args.workers)
        resume_state['inputsDigest'] = inputs_digest
        atomic_json(state_path, resume_state)
    status('Verifying persisted scratch roundtrip', 0, total)
    verify_counters = {'rowsVerified': 0, 'afterExact': 0, 'beforeExact': 0, 'legacyExact': 0, 'metadataExact': 0, 'shards': 0}
    expected = {s['file']: s['sha256'] for s in ordered}
    jobs = [(s['file'], t) for s, t in zip(ordered, takes) if t > 0]
    with ProcessPoolExecutor(max_workers=args.workers, initializer=worker_init,
                             initargs=(baseline_doc, candidate_doc, str(CORPUS), str(evidence_dir), baseline_sha,
                                       {name: str(final_paths[name]) for name in DB_NAMES})) as pool:
        for result in pool.map(verify_job, jobs, chunksize=1, buffersize=args.workers*2):
            if result['shardSha'] != expected[result['file']]:
                raise ValueError('%s shard sha256 changed during verification' % result['file'])
            verify_counters['shards'] += 1
            for key, value in result['counters'].items():
                verify_counters[key] += value
            status('Verifying persisted scratch roundtrip', verify_counters['rowsVerified'], total)
    status('Measuring complete on-disk artifacts', 0, 3)
    databases = {}
    for index, name in enumerate(DB_NAMES, 1):
        databases[name] = measure_database(final_paths[name], 'candidate' if name == 'binary-after' else 'baseline',
                                           candidate_sha if name == 'binary-after' else baseline_sha)
        if name == 'binary-after':
            databases[name]['candidateDefinitionsSha256'] = candidate_sha
        status('Measuring complete on-disk artifacts', index, 3)
    artifact_files, artifact_total = scan_artifact_files(out)
    wal_shm = wal_shm_state(final_paths)
    plan_by_table = {}
    for shard, take in zip(ordered, takes):
        table = shard['file'].split('-', 1)[0]
        plan_by_table[table] = plan_by_table.get(table, 0) + take
    total_definition_rows = total * 3
    gate = bool(
        build_counters['rows'] == total
        and verify_counters['rowsVerified'] == total
        and verify_counters['afterExact'] == total
        and verify_counters['beforeExact'] == total
        and verify_counters['legacyExact'] == total
        and verify_counters['metadataExact'] == total_definition_rows
        and (resume_state.get('rows') == total)
        and all(info['definitionsVerified'] for info in databases.values())
        and all(info['totalRows'] == total and info['fileBytesEqualsPageCountBytes'] for info in databases.values())
        and all(v == 0 for v in wal_shm.values())
        and artifact_total > 0
    ) if not resume_state.get('reused') else bool(
        verify_counters['rowsVerified'] == total and verify_counters['afterExact'] == total
        and verify_counters['beforeExact'] == total and verify_counters['legacyExact'] == total
        and verify_counters['metadataExact'] == total_definition_rows
        and all(info['definitionsVerified'] for info in databases.values())
        and all(info['totalRows'] == total and info['fileBytesEqualsPageCountBytes'] for info in databases.values())
        and all(v == 0 for v in wal_shm.values())
    )
    legacy_bytes = databases['legacy-before']['fileBytes']
    report = {
        'scope': {
            'kind': 'deterministic matched complete scratch SQLite corpus-artifact size measurement',
            'not': 'not a production migration; no production database connection; saved corpus untouched',
            'corpusRows': total,
            'note': 'Result/outcome representations differ; every other source column and all links are identical metadata JSON in all three databases.',
        },
        'inputs': {
            'manifest': str(manifest_path),
            'manifestSha256': manifest_sha,
            'evidenceDirectory': str(evidence_dir),
            'baselineDefinitionsSource': baseline_source,
            'baselineDefinitionsSha256': baseline_sha,
            'candidateDefinitions': str(Path(args.definitions).resolve()),
            'candidateDefinitionsSha256': candidate_sha,
            'candidateEqualsBaseline': candidate_sha == baseline_sha,
            'reportFrozenDefinitionsSha256': (baseline_report.get('frozenDefinitions') or {}).get('sha256'),
        },
        'plan': {
            'shards': len([t for t in takes if t > 0]),
            'rows': total,
            'byTable': plan_by_table,
            'limit': args.limit or 0,
            'workers': args.workers,
        },
        'schema': {
            'records': CREATE_RECORDS,
            'indexes': list(CREATE_INDEXES),
            'definitions': CREATE_DEFINITIONS,
            'primaryKey': ['table_name', 'rowid'],
        },
        'build': build_counters,
        'verification': {
            'persistedRoundtrip': verify_counters,
            'shardSha256Verified': verify_counters['shards'],
            'baselinePacketDecodeExact': build_counters['baselineExact'],
            'candidateEncodeDecodeExact': build_counters['candidateExact'],
            'metadataDecodeExact': build_counters['metadataExact'],
        },
        'databases': databases,
        'completeArtifact': {
            'files': artifact_files,
            'totalBytes': artifact_total,
            'excludedDiagnostics': sorted(DIAGNOSTIC_NAMES),
        },
        'walShmAfterClose': wal_shm,
        'ratios': {
            'binaryBeforeOverLegacy': databases['binary-before']['fileBytes'] / legacy_bytes if legacy_bytes else None,
            'binaryAfterOverLegacy': databases['binary-after']['fileBytes'] / legacy_bytes if legacy_bytes else None,
            'binaryAfterOverBinaryBefore': databases['binary-after']['fileBytes'] / databases['binary-before']['fileBytes'] if databases['binary-before']['fileBytes'] else None,
        },
        'gatePassed': gate,
    }
    atomic_json(out / 'report.json', report)
    if not gate:
        raise AssertionError('measurement gate failed; see report.json')
    status('Complete: matched scratch SQLite sizes measured', total, total)
    print(json.dumps({
        'rows': total,
        'legacyBytes': databases['legacy-before']['fileBytes'],
        'binaryBeforeBytes': databases['binary-before']['fileBytes'],
        'binaryAfterBytes': databases['binary-after']['fileBytes'],
        'artifactTotalBytes': artifact_total,
        'gatePassed': gate,
    }, indent=2))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', default=str(HERE / 'studies' / 'battle_binary_complete_files'))
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--definitions', default=str(DEFAULT_CANDIDATE))
    parser.add_argument('--limit', type=int, default=0, help='optional bounded short job; 0 means the whole 50000-row corpus')
    parser.add_argument('--no-resume', action='store_true')
    args = parser.parse_args(argv)
    args.workers = max(1, int(args.workers))
    try:
        return run(args)
    except Exception as exc:
        status('Failed; scratch databases retained', 0, None, '%s: %s' % (type(exc).__name__, exc))
        raise


if __name__ == '__main__':
    main()
