"""Focused checks for opt-in battle-binary shadow persistence.

Uses temporary primary and sidecar databases plus a test-local frozen codec definition
snapshot, so these mechanics checks never train or write the production registry. The separate
corpus parity/size gate remains responsible for validating production definitions. Run with:

    python check_battle_binary_shadow.py
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import struct
import sys
import tempfile
import warnings
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parent))

import strategy_battle_binary as codec
import strategy_battle_binary_shadow as shadow
import strategy_experiment_store as store


def _intent(**overrides):
    result = dict(scope='shadow-check', planned_budget=12, stopping={'maxRuns': 12},
                  purpose='improvement', fixedFields={'encounter': 'shadow-check'},
                  changedFields={'atk': 1}, policy={'finishPolicy': 'on-verdict'},
                  mechanicsRevision='shadow-mechanics-1', encounterRevision='shadow-encounter-1')
    result.update(overrides)
    return result


def _result(pair, **extra):
    result = dict(verdict=1, seeds=[pair[0], pair[1]], ticks=17, finishPolicy='on-verdict',
                  rewardOutcome=dict(awardedChests=1, awardedBasis='native-win-loss-gate',
                                     pendingChests=2), timing={'elapsedMs': 91})
    result.update(extra)
    return result


def _connect(path):
    db = sqlite3.connect(path)
    store.initialize(db)
    db.commit()
    return db


class _Env:
    def __init__(self, name, value):
        self.name = name
        self.old = os.environ.get(name)
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = str(value)

    def restore(self):
        if self.old is None:
            os.environ.pop(self.name, None)
        else:
            os.environ[self.name] = self.old


def _primary_json(db, table, where, columns=('result', 'outcome', 'timing')):
    clause = ' AND '.join('%s=?' % key for key in where)
    row = db.execute('SELECT %s FROM %s WHERE %s' % (','.join(columns), table, clause),
                     tuple(where[key] for key in where)).fetchone()
    assert row is not None
    return {column: store._json_from_stored_payload(value)
            for column, value in zip(columns, row)}


def _record_map(root, kind=None):
    return {(entry['kind'], entry['recordKey']): entry for entry in
            shadow.read_records(root, kind=kind)}


def _record_count(root, kind, record_key):
    side_db = sqlite3.connect(str(Path(root) / shadow.DB_NAME))
    try:
        return int(side_db.execute('SELECT COUNT(*) FROM shadow_record WHERE kind=? AND record_key=?',
                                   (kind, record_key)).fetchone()[0])
    finally:
        side_db.close()


def _assert_packed_core(packet):
    assert packet[:4] == b'KAT\x01', 'timing-bearing sidecar record must use the split envelope'
    assert len(packet) >= 8
    core_length = struct.unpack('<I', packet[4:8])[0]
    assert 14 <= core_length <= len(packet) - 8
    core = packet[8:8 + core_length]
    assert core[:4] == codec.MAGIC
    schema_id = struct.unpack('<H', core[12:14])[0]
    assert schema_id != 65535, 'real result/outcome core must not use the full-record JSON escape'


def run_checks(tmpdir, *, require_packed_core=True):
    checks = {}
    sidecar_root = Path(tmpdir) / 'sidecar-enabled'
    primary_path = Path(tmpdir) / 'primary.sqlite'

    env = _Env(shadow.ENV_NAME, None)
    db = None
    try:
        db = _connect(str(primary_path))
        experiment = store.create_experiment(db, _intent())
        pair = (11, 12)
        assert store.reserve(db, experiment, 'candidate-a', pair)
        assert store.complete(db, experiment, 'candidate-a', pair, _result(pair))
        assert not (Path(tmpdir) / shadow.DB_NAME).exists(), 'disabled shadow created a sidecar'
        assert shadow.count_records(sidecar_root) == 0
    finally:
        if db is not None:
            db.close()
        env.restore()
    checks['disabledWrites'] = True

    env = _Env(shadow.ENV_NAME, sidecar_root)
    db = None
    try:
        db = _connect(str(primary_path))
        # A completed legacy row created while shadowing was disabled is copied when its
        # existing reservation is encountered again after explicit opt-in.
        legacy_pair = (11, 12)
        assert store.reserve(db, experiment, 'candidate-a', legacy_pair)
        legacy_key = db.execute('SELECT sample_key FROM ea_sample_link WHERE experiment_id=? '
                                'AND candidate_id=? AND seed_a=? AND seed_b=?',
                                (experiment, 'candidate-a', *legacy_pair)).fetchone()[0]
        legacy_primary = _primary_json(db, 'ea_sample', {'sample_key': legacy_key})
        assert _record_map(sidecar_root, 'sample')[('sample', legacy_key)]['value'] == legacy_primary
        checks['legacyCompletedReuseBackfill'] = dict(sampleKey=legacy_key, exactValue=True)

        # New rows copy result, canonical outcome, and timing after the primary commit.
        pair = (21, 22)
        assert store.reserve(db, experiment, 'candidate-a', pair)
        assert store.complete(db, experiment, 'candidate-a', pair, _result(pair, score=7))
        link = db.execute('SELECT sample_key FROM ea_sample_link WHERE experiment_id=? AND '
                          'candidate_id=? AND seed_a=? AND seed_b=?',
                          (experiment, 'candidate-a', *pair)).fetchone()
        sample_key = link[0]
        primary = _primary_json(db, 'ea_sample', {'sample_key': sample_key})
        sample_record = _record_map(sidecar_root, 'sample')[('sample', sample_key)]
        assert sample_record['value'] == primary, (sample_record['value'], primary)
        assert sample_record['context']['seedPair'] == list(pair)
        assert sample_record['context']['candidateId'] == 'candidate-a'

        # A second experiment coalesces onto this sample. The shared primary identity maps to
        # one shadow record; replaying complete is a no-op in primary and idempotent in shadow.
        reuse_exp = store.create_experiment(db, _intent(scope='shadow-reuse'))
        assert store.reserve(db, reuse_exp, 'candidate-a', pair)
        reused_key = db.execute('SELECT sample_key FROM ea_sample_link WHERE experiment_id=? AND '
                                'candidate_id=? AND seed_a=? AND seed_b=?',
                                (reuse_exp, 'candidate-a', *pair)).fetchone()[0]
        assert reused_key == sample_key
        assert _record_count(sidecar_root, 'sample', sample_key) == 1
        assert store.complete(db, reuse_exp, 'candidate-a', pair, _result(pair, score=7)) is False
        assert _record_count(sidecar_root, 'sample', sample_key) == 1
        checks['sampleParityAndReuse'] = dict(sampleKey=sample_key, reuseKey=reused_key,
                                              sharedRecordCount=1)

        # Holdout results get a separate immutable identity and match the exact primary values.
        holdout_pair = (31, 32)
        holdout_exp = store.create_experiment(db, _intent(scope='shadow-holdout'))
        store.freeze_confirmation(db, holdout_exp, 'nominee', 'reference',
                                  {'finishPolicy': 'on-verdict'}, 'earned',
                                  [dict(candidateId='nominee', seeds=[list(holdout_pair)])])
        assert store.record_holdout_result(db, holdout_exp, 'nominee', holdout_pair,
                                           _result(holdout_pair, score=11))
        holdout_where = {'experiment_id': holdout_exp, 'candidate_id': 'nominee',
                         'seed_a': holdout_pair[0], 'seed_b': holdout_pair[1]}
        holdout_primary = _primary_json(db, 'ea_holdout', holdout_where)
        holdout_records = list(_record_map(sidecar_root, 'holdout').values())
        assert len(holdout_records) == 1
        holdout_record = holdout_records[0]
        assert holdout_record['value'] == holdout_primary
        assert holdout_record['context']['experimentId'] == holdout_exp
        assert store.record_holdout_result(db, holdout_exp, 'nominee', holdout_pair,
                                           _result(holdout_pair, score=11)) is False
        assert _record_count(sidecar_root, 'holdout', holdout_record['recordKey']) == 1
        checks['holdoutParityAndRetry'] = holdout_record['recordKey']

        # Definitions survive independently of the encoder's process-global registry.
        side_db = sqlite3.connect(str(sidecar_root / shadow.DB_NAME))
        try:
            row = side_db.execute('SELECT packet,definition_digest FROM shadow_record '
                                  'WHERE kind=? AND record_key=?', ('sample', sample_key)).fetchone()
        finally:
            side_db.close()
        if require_packed_core:
            _assert_packed_core(bytes(row[0]))
        definitions_path = sidecar_root / 'definitions' / (row[1] + '.json.zlib')
        packed_definitions = definitions_path.read_bytes()
        assert __import__('hashlib').sha256(packed_definitions).hexdigest() == row[1]
        definitions = json.loads(zlib.decompress(packed_definitions))
        fresh_value = codec.decode_record(bytes(row[0]), definitions)
        assert fresh_value == primary
        checks['freshDecoder'] = dict(definitionDigest=row[1], exactValue=True)

        # Caller-owned transactions defer shadow writes. A committed row flushes; a rolled-back
        # row is discarded after verification against the authoritative primary database.
        committed_pair = (41, 42)
        assert store.reserve(db, experiment, 'candidate-a', committed_pair)
        before = shadow.count_records(sidecar_root)
        db.execute('BEGIN')
        assert store.complete(db, experiment, 'candidate-a', committed_pair,
                              _result(committed_pair, score=13))
        assert db.in_transaction
        assert shadow.count_records(sidecar_root) == before
        assert shadow.flush_pending_shadow(db)['deferred'] is True
        db.commit()
        committed_flush = shadow.flush_pending_shadow(db)
        assert committed_flush == {'persisted': 1, 'discarded': 0, 'deferred': False}

        rolled_back_pair = (43, 44)
        assert store.reserve(db, experiment, 'candidate-a', rolled_back_pair)
        rollback_key = db.execute('SELECT sample_key FROM ea_sample_link WHERE experiment_id=? '
                                  'AND candidate_id=? AND seed_a=? AND seed_b=?',
                                  (experiment, 'candidate-a', *rolled_back_pair)).fetchone()[0]
        before = shadow.count_records(sidecar_root)
        db.execute('BEGIN')
        assert store.complete(db, experiment, 'candidate-a', rolled_back_pair,
                              _result(rolled_back_pair, score=17))
        db.rollback()
        rollback_flush = shadow.flush_pending_shadow(db)
        assert rollback_flush == {'persisted': 0, 'discarded': 1, 'deferred': False}
        assert shadow.count_records(sidecar_root) == before
        assert db.execute('SELECT result FROM ea_sample WHERE sample_key=?',
                          (rollback_key,)).fetchone()[0] is None
        checks['transactionLifecycle'] = dict(committed=committed_flush, rolledBack=rollback_flush)

        # The pending queue is bounded per connection. Overflow skips only that shadow copy and
        # leaves the completed primary result available for an identical retry to repair it.
        overflow_pairs = ((47, 48), (49, 50))
        for overflow_pair in overflow_pairs:
            assert store.reserve(db, experiment, 'candidate-a', overflow_pair)
        before = shadow.count_records(sidecar_root)
        old_event_limit = shadow.MAX_PENDING_EVENTS
        shadow.MAX_PENDING_EVENTS = 1
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('ignore')
                db.execute('BEGIN')
                assert store.complete(db, experiment, 'candidate-a', overflow_pairs[0],
                                      _result(overflow_pairs[0], score=23))
                assert store.complete(db, experiment, 'candidate-a', overflow_pairs[1],
                                      _result(overflow_pairs[1], score=29))
                db.commit()
                overflow_flush = shadow.flush_pending_shadow(db)
        finally:
            shadow.MAX_PENDING_EVENTS = old_event_limit
        assert overflow_flush == {'persisted': 1, 'discarded': 0, 'deferred': False}
        assert shadow.count_records(sidecar_root) == before + 1
        assert db.execute('SELECT result FROM ea_sample WHERE seed_a=? AND seed_b=?',
                          overflow_pairs[1]).fetchone()[0] is not None
        overflow_lines = (sidecar_root / 'shadow.log').read_text(encoding='utf-8').splitlines()
        assert any(json.loads(line).get('state') == 'overflow' for line in overflow_lines)
        assert store.complete(db, experiment, 'candidate-a', overflow_pairs[1],
                              _result(overflow_pairs[1], score=29)) is False
        assert shadow.count_records(sidecar_root) == before + 2

        # Explicit discard releases the per-connection queue after caller rollback.
        discard_pair = (53, 54)
        assert store.reserve(db, experiment, 'candidate-a', discard_pair)
        before = shadow.count_records(sidecar_root)
        db.execute('BEGIN')
        assert store.complete(db, experiment, 'candidate-a', discard_pair,
                              _result(discard_pair, score=31))
        db.rollback()
        assert shadow.discard_pending_shadow(db) == {'discarded': 1}
        assert shadow.count_records(sidecar_root) == before
        checks['boundedPendingQueue'] = dict(overflowSkipped=True, retryRepaired=True,
                                             explicitDiscard=True)

        # A codec/sidecar error is durably recorded, while the primary completion still succeeds.
        failed_pair = (45, 46)
        assert store.reserve(db, experiment, 'candidate-a', failed_pair)
        original_codec = shadow._codec
        class BrokenCodec:
            @staticmethod
            def encode_record(value):
                raise OSError('injected shadow codec failure')
        shadow._codec = lambda: BrokenCodec
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('ignore')
                assert store.complete(db, experiment, 'candidate-a', failed_pair,
                                      _result(failed_pair, score=19))
        finally:
            shadow._codec = original_codec
        failed_row = db.execute('SELECT result FROM ea_sample WHERE seed_a=? AND seed_b=?',
                                 failed_pair).fetchone()
        assert failed_row is not None and failed_row[0] is not None
        failure_status = json.loads((sidecar_root / 'status.json').read_text(encoding='utf-8'))
        assert failure_status['state'] == 'error'
        assert 'injected shadow codec failure' in failure_status['error']
        assert (sidecar_root / 'shadow.log').exists()
        checks['primarySurvivesShadowFailure'] = dict(primaryResultPresent=True,
                                                       durableState=failure_status['state'])
    finally:
        if db is not None:
            db.close()
        env.restore()
    return checks


def check_real_battle_fixture(tmpdir):
    """Prove the shipped registry packs a real result/outcome core through the sidecar."""
    fixture_path = (Path(__file__).resolve().parent / 'studies' / 'compact_battle_prototype12' /
                    'one-real-battle.json')
    fixture = json.loads(fixture_path.read_text(encoding='utf-8'))
    decoded = fixture['decoded']
    columns = fixture['columns']
    value = dict(result=decoded['result'], outcome=decoded['outcome'],
                 timing=json.loads(columns['timing']))

    packet, definitions = codec.encode_record(value)
    codec.exact(value, codec.decode_record(packet, definitions))
    _assert_packed_core(packet)

    root = Path(tmpdir) / 'real-battle-sidecar'
    env = _Env(shadow.ENV_NAME, root)
    db = None
    try:
        db = _connect(str(Path(tmpdir) / 'real-battle-primary.sqlite'))
        sample_key = 'real-battle-' + columns['sample_key']
        where = {'sample_key': sample_key}
        db.execute('INSERT INTO ea_sample(sample_key,candidate_id,policy,mechanics_revision,'
                   'encounter_revision,seed_a,seed_b,measurement_window,result,outcome,timing,created_at) '
                   'VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                   (sample_key, columns['candidate_id'], columns['policy'],
                    columns['mechanics_revision'], columns['encounter_revision'],
                    columns['seed_a'], columns['seed_b'], columns['measurement_window'],
                    store._canonical(value['result']), store._canonical(value['outcome']),
                    store._canonical(value['timing']), float(columns['created_at'])))
        db.commit()
        shadow.record_primary_write(
            db, kind='sample', record_key=sample_key,
            context=dict(sampleKey=sample_key, candidateId=columns['candidate_id'],
                         seedPair=[columns['seed_a'], columns['seed_b']],
                         policy=json.loads(columns['policy']),
                         mechanicsRevision=columns['mechanics_revision'],
                         encounterRevision=columns['encounter_revision']),
            value=value, primary_where=where)
        records = shadow.read_records(root, kind='sample')
        assert len(records) == 1
        codec.exact(value, records[0]['value'])
        side_db = sqlite3.connect(str(root / shadow.DB_NAME))
        try:
            stored = side_db.execute('SELECT packet FROM shadow_record WHERE kind=? AND record_key=?',
                                     ('sample', sample_key)).fetchone()
        finally:
            side_db.close()
        _assert_packed_core(bytes(stored[0]))
        return dict(exactRoundTrip=True, packedCore=True, decodedBytes=len(packet),
                    recordKey=sample_key)
    finally:
        if db is not None:
            db.close()
        env.restore()


def _install_test_encoder():
    """Freeze one representative JSON shape in memory; never touch production definitions."""
    db = _connect(':memory:')
    try:
        experiment = store.create_experiment(db, _intent())
        pair = (51, 52)
        payload = _result(pair, score=0)
        value = dict(result=payload,
                     outcome=store._outcome_for(store._experiment(db, experiment),
                                                'candidate-a', experiment, payload),
                     timing=payload['timing'])
        encoder = codec.RecordEncoder.fit([
            dict(result=value['result'], outcome=value['outcome']),
            dict(timing=value['timing']),
        ])
    finally:
        db.close()
    prior = codec.default_encoder
    codec.default_encoder = lambda: encoder
    return prior


def main():
    prior = _install_test_encoder()
    try:
        with tempfile.TemporaryDirectory(prefix='battle-binary-shadow-') as tmpdir:
            fixture_checks = run_checks(tmpdir)
    finally:
        codec.default_encoder = prior
    report = {'module': 'strategy_battle_binary_shadow', 'fixtureRegistryChecks': fixture_checks}
    if codec.DEFAULT_DEFINITIONS.exists():
        codec._default = None
        with tempfile.TemporaryDirectory(prefix='battle-binary-shadow-production-') as tmpdir:
            report['defaultRegistryChecks'] = run_checks(tmpdir, require_packed_core=False)
        codec._default = None
        with tempfile.TemporaryDirectory(prefix='battle-binary-shadow-real-battle-') as tmpdir:
            report['realBattleRegistry'] = check_real_battle_fixture(tmpdir)
    report_path = (Path(__file__).resolve().parent / 'studies' / 'battle_binary_corpus' /
                   'shadow-checks.json')
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report['reportPath'] = str(report_path)
    serialized = json.dumps(report, sort_keys=True, indent=2)
    temporary = report_path.with_suffix('.json.tmp')
    temporary.write_text(serialized + '\n', encoding='utf-8')
    os.replace(temporary, report_path)
    print(serialized)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

