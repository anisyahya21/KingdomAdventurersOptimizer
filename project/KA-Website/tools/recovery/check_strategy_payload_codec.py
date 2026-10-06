"""Bounded regression checks for the lossless encounter result/outcome codec.

Temporary/in-memory SQLite only. Does not open the production library, run battles or activate v5.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import struct
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import strategy_diagnostic_export as export
import strategy_encounter_overview as overview
import strategy_encounter_search as search
import strategy_experiment_store as store
import strategy_payload_codec as codec
import strategy_support_evidence as support


def _check(ok, message):
    if not ok:
        raise AssertionError(message)


def _raises(kind, fn, message):
    try:
        fn()
    except kind:
        return
    raise AssertionError(message)


def _large_policy():
    return {'finishPolicy': 'on-verdict', 'annotation': 'policy evidence ' * 200}


def codec_checks():
    raw = '{"n":900719925474099312345,"float":1e+30,"dup":1,"dup":2,' \
          '"text":"雪🧭"}' + (' ' * 400)
    encoded = codec.encode_text(raw)
    _check(isinstance(encoded, bytes), 'compressible exact JSON text is stored as BLOB')
    _check(codec.decode_text(encoded) == raw, 'decode returns the exact original JSON spelling')
    _check(json.loads(codec.decode_text(encoded)) == json.loads(raw),
           'numeric, unicode and duplicate-key JSON semantics survive round-trip')
    _check(codec.encode_text('{"small":1}') == '{"small":1}', 'small payload stays TEXT')
    _check(codec.encode_text('x' * (codec.MAX_RAW_BYTES + 1)) == 'x' *
           (codec.MAX_RAW_BYTES + 1), 'over-limit raw payload remains TEXT')

    # Preserve json.loads(bytes)' UTF-8/16/32 detection and surrogatepass legacy behavior.
    legacy = ('{"v":"x雪"}', '"\ud800"')
    fixtures = []
    for text in legacy:
        fixtures.append(text.encode('utf-8', 'surrogatepass'))
        fixtures.append(b'\xef\xbb\xbf' + text.encode('utf-8', 'surrogatepass'))
        fixtures.append(b'\xff\xfe' + text.encode('utf-16-le', 'surrogatepass'))
        fixtures.append(b'\xfe\xff' + text.encode('utf-16-be', 'surrogatepass'))
        fixtures.append(b'\xff\xfe\x00\x00' + text.encode('utf-32-le', 'surrogatepass'))
        fixtures.append(b'\x00\x00\xfe\xff' + text.encode('utf-32-be', 'surrogatepass'))
    for value in fixtures:
        _check(json.loads(codec.decode_text(value)) == json.loads(value),
               'legacy BLOB full decode matches json.loads(bytes)')

    prefix_source = '"雪🧭tail"'
    prefix = prefix_source.encode('utf-8')
    _check(codec.decode_prefix(prefix, 4) == prefix_source[:4],
           'legacy UTF-8 BLOB prefix preserves character bound')
    _check(codec.decode_prefix(codec.encode_text(raw), 17) == raw[:17],
           'compressed prefix validates the full cell then applies character bound')

    _raises(codec.PayloadCodecError, lambda: codec.decode_text(encoded[:-1]),
            'truncated zlib stream must fail closed')
    _raises(codec.PayloadCodecError, lambda: codec.decode_text(encoded + b'x'),
            'trailing bytes must fail closed')
    damaged = bytearray(encoded)
    damaged[8] ^= 1
    _raises(codec.PayloadCodecError, lambda: codec.decode_text(bytes(damaged)),
            'checksum mismatch must fail closed')
    bad_version = bytearray(encoded)
    bad_version[3] = 2
    _raises(codec.PayloadCodecError, lambda: codec.decode_text(bytes(bad_version)),
            'unknown reserved codec version must fail closed')
    _raises(codec.PayloadCodecError, lambda: codec.decode_text(b'KAP'),
            'truncated reserved magic must fail closed')
    too_large = b'KAP\x01' + (b'\0' * codec.MAX_ENCODED_BYTES)
    _raises(codec.PayloadCodecError, lambda: codec.decode_text(too_large),
            'oversized encoded BLOB must fail before header slicing/decompression')
    _raises(ValueError,
            lambda: codec.decode_prefix(encoded, True), 'bool is not a valid prefix length')
    return dict(roundTrip=True, legacyByteEncodings=len(fixtures), strictCorruption=True,
                encodedBound=codec.MAX_ENCODED_BYTES, rawBound=codec.MAX_RAW_BYTES)


def ledger_checks():
    fresh = sqlite3.connect(':memory:')
    _check(store.initialize(fresh) == 5, 'new ledgers initialize directly at schema v5')
    fresh.close()

    db = sqlite3.connect(':memory:')
    db.execute('CREATE TABLE ea_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
    db.execute("INSERT INTO ea_meta VALUES('schema_version','3')")
    _check(store.initialize(db) == 3, 'existing v3 ledger stays v3 on initialize')
    policy = _large_policy()
    intent = dict(scope='codec-check', planned_budget=4, stopping={'maxRuns': 4},
                  purpose='improvement', fixedFields={}, changedFields={}, policy=policy,
                  mechanicsRevision='m', encounterRevision='e')
    experiment = store.create_experiment(db, intent)
    _check(store.reserve(db, experiment, 'candidate', (11, 12)), 'v3 reservation succeeds')
    result = dict(seeds=[11, 12], verdict=1, notes='result body ' * 500)
    _check(store.complete(db, experiment, 'candidate', (11, 12), result),
           'v3 completion succeeds')
    old_type = db.execute('SELECT typeof(result),typeof(outcome) FROM ea_sample').fetchone()
    _check(old_type == ('text', 'text'), 'v3 writes remain TEXT')

    _check(store.activate_schema_v4(db) == 4, 'explicit v3 to v4 activation succeeds')
    _check(db.execute('SELECT typeof(result) FROM ea_sample').fetchone()[0] == 'text',
           'activation leaves historical v3 payloads untouched')
    _check(store.complete(db, experiment, 'candidate', (11, 12), result) is False,
           'compressed-result replay remains an idempotent no-op')

    second = store.create_experiment(db, dict(intent, scope='codec-check-v4'))
    _check(store.reserve(db, second, 'candidate', (21, 22)), 'v4 reservation succeeds')
    result2 = dict(seeds=[21, 22], verdict=1, notes='result body ' * 500)
    _check(store.complete(db, second, 'candidate', (21, 22), result2), 'v4 completion succeeds')
    stored_row = db.execute('SELECT result,outcome,typeof(result),typeof(outcome) FROM ea_sample '
                            'WHERE seed_a=21').fetchone()
    stored_types = stored_row[2:]
    _check(stored_types[0] == 'blob', 'v4 compressed result uses same-column BLOB storage')
    _check(stored_types[1] == 'blob', 'v4 compressed outcome uses same-column BLOB storage')
    _check(codec.decode_text(stored_row[0]) == store._canonical(result2),
           'v4 sample result decodes to the exact stored JSON spelling')
    _check(store.outcomes(db, second)[0]['outcome']['seeds'] == [21, 22],
           'store outcome reader decodes compressed rows')
    _check(store.complete(db, second, 'candidate', (21, 22), result2) is False,
           'v4 compressed sample replay remains an idempotent no-op')
    _raises(ValueError,
            lambda: store.complete(db, second, 'candidate', (21, 22),
                                   dict(result2, verdict=2)),
            'v4 compressed sample refuses a different replay')

    confirmation = store.freeze_confirmation(
        db, second, 'nominee', 'reference', policy, 'earned',
        [dict(candidateId='nominee', seeds=[[31, 32]])])
    holdout = dict(seeds=[31, 32], verdict=1, finalEarned=42,
                   notes='holdout body ' * 500)
    _check(store.record_holdout_result(db, second, 'nominee', (31, 32), holdout),
           'v4 holdout completion succeeds')
    report = store.confirmation_report(db, second)
    _check(report['ready'] and report['outcomes'][0]['policy']['annotation'] == policy['annotation'],
           'confirmation report decodes compressed holdout outcome without changing fields')
    holdout_row = db.execute('SELECT result,outcome,typeof(result),typeof(outcome) '
                             'FROM ea_holdout WHERE experiment_id=?', (second,)).fetchone()
    holdout_types = holdout_row[2:]
    _check(holdout_types == ('blob', 'blob'),
           'v4 holdout result/outcome use same-column BLOB storage')
    _check(codec.decode_text(holdout_row[0]) == store._canonical(holdout),
           'v4 holdout result decodes to the exact stored JSON spelling')
    _check(store.record_holdout_result(db, second, 'nominee', (31, 32), holdout) is False,
           'v4 compressed holdout replay remains an idempotent no-op')
    _raises(ValueError,
            lambda: store.record_holdout_result(
                db, second, 'nominee', (31, 32), dict(holdout, verdict=2)),
            'v4 compressed holdout refuses a different replay')
    _check(confirmation > 0, 'confirmation plan is frozen')
    db.close()
    return dict(v3Gated=True, metadataOnlyActivation=True, sampleAndHoldout=True,
                duplicateReplay=True)


def reader_checks():
    # Overview keeps malformed/nonmagic legacy fallback while decoding the reserved v4 codec.
    db = sqlite3.connect(':memory:')
    db.row_factory = sqlite3.Row
    view = overview.LedgerCache()
    result_raw = '{"resultBackend":"native","seeds":[1,2],"annotation":"overview ' + \
                 ('payload ' * 150) + '"}'
    outcome_raw = '{"verdict":1,"resolved":true,"finalEarned":7,"annotation":"overview ' + \
                  ('outcome ' * 150) + '"}'
    _check(json.loads(result_raw)['resultBackend'] == 'native' and
           json.loads(outcome_raw)['finalEarned'] == 7, 'overview raw fixtures are valid JSON')
    result = codec.encode_text(result_raw)
    outcome = codec.encode_text(outcome_raw)
    _check(isinstance(result, bytes) and isinstance(outcome, bytes),
           'overview fixtures are actual compressed BLOBs')
    _check(codec.decode_text(result) == result_raw and codec.decode_text(outcome) == outcome_raw,
           'overview BLOBs decode to their exact original JSON spelling')
    decoded = view._decode(db, table='ea_sample', kind='development', ident=1,
                           candidate_id='missing', policy=None, window='development',
                           mechanics=None, encounter_revision=None, result_raw=result,
                           outcome_raw=outcome, stamp=1, experiment=1)
    _check(decoded['backend'] == 'native' and decoded['finalEarned'] == 7,
           'overview reads compressed result/outcome')
    malformed = view._decode(db, table='ea_sample', kind='development', ident=2,
                             candidate_id='missing', policy=None, window='development',
                             mechanics=None, encounter_revision=None, result_raw=b'not-json',
                             outcome_raw=b'\xff', stamp=2, experiment=1)
    _check(malformed['unknownOutcome'], 'overview preserves malformed legacy BLOB fallback')

    # Support evidence must retain the prior bytes type for ordinary legacy SQLite BLOBs.
    db.executescript('''
        CREATE TABLE ea_sample(sample_key TEXT,candidate_id TEXT,policy TEXT,
            mechanics_revision TEXT,encounter_revision TEXT,seed_a INTEGER,seed_b INTEGER,
            measurement_window TEXT,result,outcome,timing,created_at REAL);
        CREATE TABLE ea_sample_link(sample_key TEXT,candidate_id TEXT,experiment_id INTEGER,
            seed_a INTEGER,seed_b INTEGER);
        CREATE TABLE ea_experiment(id INTEGER,owner TEXT,owner_share REAL,measurement_window TEXT,
            policy TEXT,mechanics_revision TEXT,encounter_revision TEXT,intent_json TEXT);
        CREATE TABLE ea_holdout(experiment_id INTEGER,candidate_id TEXT,seed_a INTEGER,seed_b INTEGER,
            result,outcome);
    ''')
    db.execute('INSERT INTO ea_experiment VALUES(1,"community",1,"development",NULL,"m","e",?)',
               ('{"compatibility":"c","measurementWindow":"development"}',))
    support_raw = '{"ticks":2,"annotation":"support ' + ('payload ' * 150) + '"}'
    _check(json.loads(support_raw)['ticks'] == 2, 'support raw fixture is valid JSON')
    support_blob = codec.encode_text(support_raw)
    _check(isinstance(support_blob, bytes), 'support fixture is an actual compressed BLOB')
    for key, value in (('legacy', b'{"ticks":1}'), ('compressed', support_blob)):
        db.execute('INSERT INTO ea_sample(sample_key,candidate_id,seed_a,seed_b,result,policy,'
                   'mechanics_revision,encounter_revision,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
                   (key, 'candidate', 1, 2, value, None, 'm', 'e', 1.0))
        db.execute('INSERT INTO ea_sample_link VALUES(?,?,?,?,?)', (key, 'candidate', 1, 1, 2))
    sample_rows = support._sample_rows(db, 'candidate', 10)
    by_key = {row['sampleKey']: row for row in sample_rows}
    _check(isinstance(by_key['legacy']['result'], bytes),
           'support reader retains legacy BLOB result type')
    _check(by_key['compressed']['result'] == support_raw,
           'support reader decodes compressed result')

    # Confirmation-search comparison reads its stored outcome BLOB through the shared codec.
    nominee_outcome = '{"finalEarned":12,"annotation":"search ' + ('nominee ' * 150) + '"}'
    reference_outcome = '{"finalEarned":10,"annotation":"search ' + ('reference ' * 150) + '"}'
    _check(json.loads(nominee_outcome)['finalEarned'] == 12 and
           json.loads(reference_outcome)['finalEarned'] == 10,
           'confirmation-search raw fixtures are valid JSON')
    nominee_blob, reference_blob = (codec.encode_text(nominee_outcome),
                                   codec.encode_text(reference_outcome))
    _check(isinstance(nominee_blob, bytes) and isinstance(reference_blob, bytes),
           'confirmation-search fixtures are actual compressed BLOBs')
    _check(codec.decode_text(nominee_blob) == nominee_outcome and
           codec.decode_text(reference_blob) == reference_outcome,
           'confirmation-search BLOBs decode to exact original JSON spellings')
    db.executemany('INSERT INTO ea_holdout VALUES(1,?,50,51,1,?)', [
        ('nominee', nominee_blob), ('reference', reference_blob)])
    coordinator = object.__new__(search.Coordinator)
    comparison = coordinator._paired_comparison(
        db, 1, dict(nominee='nominee', reference='reference'))
    _check(comparison['pairedDifference'] == 2,
           'confirmation search reads compressed outcomes')

    # Run the production bounded SQL and row builder over TEXT and BLOB versions of one battle.
    db.executescript('''
        CREATE TABLE candidate(id TEXT PRIMARY KEY,scenario TEXT,label TEXT,source TEXT,stats TEXT,
            created INTEGER);
    ''')
    db.execute('INSERT INTO candidate VALUES("build",?,"build","test","{}",1)',
               ('{"ownUnits":[],"items":{}}',))
    raw_result = json.dumps(dict(seeds=[60, 61], verdict=1, replay={'events': 'x' * 500}),
                            separators=(',', ':'))
    raw_outcome = json.dumps(dict(verdict=1, resolved=True, finalEarned=6,
                                  annotation='outcome ' * 200), separators=(',', ':'))
    blob_result = codec.encode_text(raw_result)
    blob_outcome = codec.encode_text(raw_outcome)
    _check(isinstance(blob_result, bytes) and isinstance(blob_outcome, bytes),
           'diagnostic-export fixtures are actual compressed result/outcome BLOBs')
    _check(codec.decode_text(blob_result) == raw_result and codec.decode_text(blob_outcome) == raw_outcome,
           'diagnostic-export BLOBs decode to exact original JSON spellings')
    db.execute('INSERT INTO ea_sample(sample_key,candidate_id,seed_a,seed_b,result,outcome,'
               'mechanics_revision,encounter_revision,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
               ('text', 'build', 60, 61, raw_result, raw_outcome, 'm', 'e', 1.0))
    db.execute('INSERT INTO ea_sample(sample_key,candidate_id,seed_a,seed_b,result,outcome,'
               'mechanics_revision,encounter_revision,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
               ('blob', 'build', 60, 61, blob_result, blob_outcome, 'm', 'e', 2.0))
    # The paired TEXT row carries the identical outcome payload.
    batch = list(export._ea_batches(db, cutoff=5, limit=2))[0]
    rows = {}
    scenarios = export._ScenarioStore(db)
    for row in batch:
        rows[row['sample_key']] = export._battle_payload(db, 'ea_sample', row, scenarios, {}, {})[0]
    for field in ('result', 'outcome', 'traceOmitted', 'notes'):
        _check(rows['text'][field] == rows['blob'][field],
               'diagnostic export TEXT/BLOB parity for %s' % field)
    db.close()
    return dict(overview=True, support=True, confirmationSearch=True, exportParity=True)


def main():
    report = dict(codec=codec_checks(), ledger=ledger_checks(), readers=reader_checks())
    print(json.dumps(report, sort_keys=True))


if __name__ == '__main__':
    main()
