"""Bounded primary-path checks and matched complete-file evidence. Scratch databases only."""
import copy
import hashlib
import json
import pickle
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import strategy_battle_binary as binary
import strategy_experiment_store as store
import strategy_payload_codec as codec

HERE = Path(__file__).resolve().parent
CORPUS = HERE / 'studies/battle_binary_corpus'


def semantic_digest(path):
    digest = hashlib.sha256()
    count = 0
    with sqlite3.connect('file:' + path.as_posix() + '?mode=ro', uri=True) as db:
        for table in ('ea_sample', 'ea_holdout'):
            for result, outcome, timing in db.execute(
                    'SELECT result,outcome,timing FROM ' + table + ' ORDER BY rowid'):
                for cell in (result, outcome, timing):
                    raw = codec.decode_text(cell).encode()
                    digest.update(len(raw).to_bytes(8, 'little'))
                    digest.update(raw)
                count += 1
    return dict(rows=count, semanticDigest=digest.hexdigest())


def edge_checks(records):
    for row in records:
        for kind in ('result', 'outcome'):
            value = json.loads(codec.decode_text(row['columns'][kind]))
            text = store._canonical(value)
            packet = codec.encode_packed_text(text, kind)
            assert packet.startswith(codec.PACKED_MAGIC)
            binary.exact(value, json.loads(codec.decode_text(packet)))
            assert codec.decode_text(packet) == text
            assert codec.decode_prefix(packet, 17) == text[:17]
    value = json.loads(codec.decode_text(records[0]['columns']['result']))
    for change in ({'ticks': 2**80}, {'healthFraction': -0.0},
                   {'future': [None, False, 0, 0.0, '雪🧭']},
                   {'digest': '0'*64}, {'verdict': 'unknown-enum'}):
        changed = {**value, **change}
        packet = codec.encode_packed_text(store._canonical(changed))
        binary.exact(changed, json.loads(codec.decode_text(packet)))
    for raw in ('{"dup":1,"dup":2}', '{"n":1e+30}', '{"n":NaN}', '{"s":"\\ud800"}'):
        assert codec.decode_text(codec.encode_packed_text(raw)) == raw
    huge = {**value, 'ticks': 2**2000}
    raw = store._canonical(huge)
    assert codec.decode_text(codec.encode_packed_text(raw)) == raw
    packet = codec.encode_packed_text(store._canonical(value))
    bad = bytearray(packet); bad[-1] ^= 1
    missing = bytearray(packet); missing[8:16] = b'\xff'*8
    for corrupt in (bytes(bad), bytes(missing), packet[:12], b'KAC\x02'+packet[4:]):
        try:
            codec.decode_text(corrupt)
        except codec.PayloadCodecError:
            pass
        else:
            raise AssertionError('corrupt packed payload accepted')
    for identity in codec._registry_manifest()['definitions']:
        old = codec._packed_codec(identity)
        packet = old.encode({'result': value})
        binary.exact({'result': value}, json.loads(codec.decode_text(packet)))
    return dict(typedCells=len(records)*2, legacyDefinitions=True, corruptionFailsClosed=True,
                numericUnicodeAndFutureFields=True, exactTextFallback=True)


def build(path, records, legacy):
    old_encode, old_now = codec.encode_packed_text, store._now
    if legacy:
        codec.encode_packed_text = lambda text, kind='result': codec.encode_text(text)
    store._now = lambda: 1000.0
    try:
        db = sqlite3.connect(path)
        store.initialize(db); db.commit()
        intent = dict(scope='primary-packed-check', planned_budget=1024,
                      stopping={'maxRuns': 1024}, purpose='improvement', fixedFields={},
                      changedFields={}, policy={'finishPolicy': 'on-verdict'})
        experiment = store.create_experiment(db, intent)
        schema = list(db.execute("SELECT type,name,sql FROM sqlite_master ORDER BY name"))
        planned = [dict(candidateId='fixture-%d' % i, seeds=[[i*2, i*2+1]])
                   for i in range(256, 512)]
        store.freeze_confirmation(db, experiment, 'fixture-256', 'reference',
                                  intent['policy'], 'earned', planned)
        db.commit()
        for i, row in enumerate(records):
            result = json.loads(codec.decode_text(row['columns']['result']))
            result = {**result, 'seeds': [i*2, i*2+1]}
            candidate, pair = 'fixture-%d' % i, (i*2, i*2+1)
            if i < 256:
                assert store.reserve(db, experiment, candidate, pair)
                writer = store.complete
                table = 'ea_sample'
            else:
                writer = store.record_holdout_result
                table = 'ea_holdout'
            # Caller-owned rollback leaves result pending, retaining its reservation.
            if i in (0, 256):
                db.commit(); db.execute('SAVEPOINT caller')
                assert writer(db, experiment, candidate, pair, result)
                assert db.in_transaction
                db.execute('ROLLBACK TO caller'); db.execute('RELEASE caller')
                assert db.execute('SELECT result FROM '+table+' WHERE candidate_id=?',
                                  (candidate,)).fetchone()[0] is None
            assert writer(db, experiment, candidate, pair, result)
            assert writer(db, experiment, candidate, pair, result) is False
            try:
                writer(db, experiment, candidate, pair, {**result, 'ticks': -1})
            except ValueError:
                pass
            else:
                raise AssertionError('changed result overwrote completion')
            cells = db.execute('SELECT result,outcome FROM '+table+' WHERE candidate_id=?',
                               (candidate,)).fetchone()
            assert codec.decode_text(cells[0]) == store._canonical(result)
            if not legacy:
                assert all(cell.startswith(codec.PACKED_MAGIC) for cell in cells)
        assert len(store.outcomes(db, experiment)) == 256
        assert len(store.confirmation_report(db, experiment)['outcomes']) == 256
        assert schema == list(db.execute("SELECT type,name,sql FROM sqlite_master ORDER BY name"))
        db.commit()
        packed_schemas = {}
        for table in ('ea_sample', 'ea_holdout'):
            for result, outcome in db.execute('SELECT result,outcome FROM '+table):
                for cell in (result, outcome):
                    if isinstance(cell, bytes) and cell.startswith(codec.PACKED_MAGIC):
                        schema_id = int.from_bytes(cell[16:18], 'little')
                        packed_schemas[str(schema_id)] = packed_schemas.get(str(schema_id), 0) + 1
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        pages, free = db.execute('PRAGMA page_count').fetchone()[0], db.execute('PRAGMA freelist_count').fetchone()[0]
        db.close()
        assert not Path(str(path)+'-wal').exists() and not Path(str(path)+'-shm').exists()
        return dict(bytes=path.stat().st_size, pages=pages, freelistPages=free, schema=schema,
                    packedSchemaCounts=packed_schemas)
    finally:
        codec.encode_packed_text, store._now = old_encode, old_now


def main():
    manifest = json.loads((CORPUS/'manifest.json').read_text())
    records = []
    for shard in manifest['shards']:
        raw = (CORPUS/shard['file']).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == shard['sha256']
        records.append(pickle.loads(raw)[0])
    assert len(records) == 512 and [r['table'] for r in records].count('ea_sample') == 256
    checks = edge_checks(records)
    output = CORPUS/'primary-storage-check'
    output.mkdir(exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix='matched-', dir=output))
    before, after = run/'legacy.sqlite', run/'packed.sqlite'
    legacy = build(before, records, True)
    packed = build(after, records, False)
    assert legacy.pop('schema') == packed.pop('schema')
    assert semantic_digest(before) == semantic_digest(after)
    with sqlite3.connect(before) as left, sqlite3.connect(after) as right:
        tables = [r[0] for r in left.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        for table in tables:
            columns = [r[1] for r in left.execute('PRAGMA table_info('+table+')')]
            old_rows = list(left.execute('SELECT * FROM '+table+' ORDER BY rowid'))
            new_rows = list(right.execute('SELECT * FROM '+table+' ORDER BY rowid'))
            assert len(old_rows) == len(new_rows)
            for old, new in zip(old_rows, new_rows):
                for field, a, b in zip(columns, old, new):
                    if table in ('ea_sample', 'ea_holdout') and field in ('result', 'outcome'):
                        a, b = codec.decode_text(a), codec.decode_text(b)
                    assert type(a) is type(b) and a == b, (table, field)
    fresh = json.loads(subprocess.check_output([sys.executable, __file__, '--decode', str(after)], text=True))
    assert fresh == semantic_digest(before)
    definitions = sum(p.stat().st_size for p in codec._REGISTRY.iterdir() if p.is_file())
    report = dict(passed=True, checks=checks, independentDecoder=fresh,
                  scope='512 saved real fixtures: one from each shard, 256 sample + 256 holdout; '
                        'reserved seeds adapted and outcomes regenerated through existing API',
                  legacyFile=legacy, packedFile=packed, deployedDefinitionsBytes=definitions,
                  packedCombinedBytes=packed['bytes']+definitions,
                  transactionRollback=True, duplicateReplay=True, differentReplayRejected=True,
                  allPersistedMetadataAndLinksExact=True,
                  schemaIdentical=True, tablesOwnershipAndCoordinatorUnchanged=True,
                  files=str(run), productionOpened=False,
                  fileScope='Complete SQLite files including all metadata, links, indexes, pages and free space; '
                            'all three deployed definition versions plus manifest counted separately; no sidecars/WAL/SHM')
    (output/'report.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report))


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--decode':
        print(json.dumps(semantic_digest(Path(sys.argv[2]))))
    else:
        main()
