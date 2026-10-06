"""Focused temporary-SQLite checks for the staged v5 storage migration."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))

import migrate_strategy_payload_storage as migration
import strategy_payload_codec as payload_codec
import strategy_experiment_store as store


def _check(condition, message):
    if not condition:
        raise AssertionError(message)


def _raw_large(tag):
    # Keep duplicate keys, numeric spelling, escapes, order, and a compressible tail observable.
    return ('{ "compatibility":"first-%s","compatibility":"second-%s",'
            '"engineRevision":"eng-7","mechanicsRevision":"mech-9",'
            '"policy":{"dup":1,"dup":2,"float":1e+30},'
            '"measurementWindow":"holdout","changedFields":{"stat":1,"stat":2},'
            '"extra":{"escaped":"\\u96ea","unicode":"雪🧭"},"pad":"%s" }') % (
                tag, tag, 'raw intent migration payload ' * 120)


def _create_source(path, version=4, corrupted=False):
    db = sqlite3.connect(path)
    legacy_schema = [statement.replace('        intent_json TEXT,\n        full_intent BLOB)',
                                      '        intent_json TEXT)')
                     for statement in store.SCHEMA]
    db.executescript(';\n'.join(legacy_schema))
    db.execute('CREATE INDEX ea_experiment_compatibility ON ea_experiment('
               'mechanics_revision,json_extract(intent_json,\'$.compatibility\'))')
    db.execute('''CREATE TABLE provenance_note(
        note_id INTEGER PRIMARY KEY, seed_plan TEXT NOT NULL, source_hash BLOB NOT NULL)''')
    db.execute('CREATE TABLE fixture_without_rowid(a TEXT,b INTEGER,payload BLOB,PRIMARY KEY(a,b)) WITHOUT ROWID')
    db.execute('INSERT INTO fixture_without_rowid VALUES(?,?,?)', ('key', 2, b'raw provenance'))
    db.execute('CREATE TABLE fixture_text_key(key TEXT PRIMARY KEY,payload TEXT)')
    db.executemany('INSERT INTO fixture_text_key VALUES(?,?)', [('z','first'),('deleted','gone'),('a','last')])
    db.execute("DELETE FROM fixture_text_key WHERE key='deleted'")
    db.execute('CREATE TABLE fixture_sequence(id INTEGER PRIMARY KEY AUTOINCREMENT,value TEXT)')
    db.execute("INSERT INTO fixture_sequence(id,value) VALUES(1000,'deleted high watermark')")
    db.execute('DELETE FROM fixture_sequence')
    db.execute("INSERT INTO ea_meta VALUES('schema_version',?)", (str(version),))
    db.execute("INSERT INTO ea_meta VALUES('library_uuid','fixture-library-42')")
    experiment_values = [
        (1, 'request-a', 'development', None, None, None, 'mech-9', 'enc-4', 'improvement',
         'community', 1.0, '{}', '{"stat":1}', 4096, '{"maxRuns":4096}', 77,
         'holdout', 171234.5, _raw_large('a')),
        (2, 'request-b', 'holdout', None, None, None, 'mech-2', 'enc-1', 'confirmation',
         'community', 1.0, '{}', '{}', 128, '{"maxRuns":128}', 77,
         'holdout', 171235.5,
         '{"compatibility":"small","engineRevision":"e","changedFields":{}}'),
        (3, 'request-c', 'empty-intent', None, None, None, None, None, None,
         None, 1.0, '{}', '{}', 1, '{"maxRuns":1}', None, None, 171236.5, None),
    ]
    db.executemany('INSERT INTO ea_experiment(id,request_key,scope,parent_id,reference_id,policy,'
                   'mechanics_revision,encounter_revision,purpose,owner,owner_share,fixed_fields,'
                   'changed_fields,planned_budget,stopping,session_id,measurement_window,created_at,'
                   'intent_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', experiment_values)

    compressed = payload_codec.encode_text('{"verdict":1,"body":"' +
                                           ('sample result ' * 500) + '"}')
    _check(isinstance(compressed, bytes), 'fixture sample result is a real KAP frame')
    if corrupted:
        damaged = bytearray(compressed)
        damaged[8] ^= 1
        compressed = bytes(damaged)
    legacy_blob = b'{"legacy":"direct sqlite BLOB", "number":1e+30}'
    db.execute('INSERT INTO ea_sample VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
               ('sample-1', 'candidate-a', None, 'mech-9', 'enc-4', 10, 11, 'development',
                compressed, None, '{"elapsed":0.125}', 10.25))
    db.execute('INSERT INTO ea_sample VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
               ('sample-2', 'candidate-b', None, 'mech-9', 'enc-4', 12, 13, 'development',
                '{"small":true}', legacy_blob, None, 11.5))
    db.execute('INSERT INTO ea_sample VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
               ('sample-3', 'candidate-c', None, 'mech-9', 'enc-4', 14, 15, 'development',
                None, None, None, 12.75))
    db.execute('INSERT INTO ea_holdout VALUES(?,?,?,?,?,?,?,?)',
               (1, 'nominee', 90, 91, '{"verdict":1}', payload_codec.encode_text(
                   '{"finalEarned":17,"tail":"' + ('holdout ' * 500) + '"}'),
                '{"elapsed":0.25}', 18.25))
    db.execute('INSERT INTO ea_holdout VALUES(?,?,?,?,?,?,?,?)',
               (2, 'reference', 92, 93, None, None, None, None))
    db.execute('INSERT INTO ea_experiment_budget VALUES(1,4096,321,300)')
    db.execute('INSERT INTO ea_experiment_budget VALUES(2,128,12,10)')
    db.execute('INSERT INTO ea_session_experiment VALUES(1,77,"community","improvement")')
    db.execute('INSERT INTO ea_session_experiment VALUES(2,77,"community","confirmation")')
    db.execute('INSERT INTO provenance_note VALUES(1,?,?)',
               ('[[10,11],[12,13],[14,15]]', bytes.fromhex('00112233445566778899aabbccddeeff')))
    db.commit()
    db.close()


def _source_projection(path):
    db = sqlite3.connect(path)
    version = db.execute("SELECT value FROM ea_meta WHERE key='schema_version'").fetchone()[0]
    samples = db.execute('SELECT sample_key,typeof(result),result,typeof(outcome),outcome '
                         'FROM ea_sample ORDER BY rowid').fetchall()
    intents = db.execute('SELECT id,typeof(intent_json),intent_json FROM ea_experiment ORDER BY id').fetchall()
    budgets = db.execute('SELECT * FROM ea_experiment_budget ORDER BY experiment_id').fetchall()
    db.close()
    return version, samples, intents, budgets


def _interrupt_after_one_batch(_number, _table, _checkpoint):
    raise migration.MigrationInterrupted('synthetic process interruption after committed batch')


def _resumable_conversion_checks(root):
    source = root / 'source-v4.sqlite'
    stage = root / 'stage-v5.sqlite'
    output = root / 'compact-v5.sqlite'
    _create_source(source, version=4)
    original = _source_projection(source)
    try:
        migration.run_migration(source, stage, output, batch_rows=1,
                                encoder_threads=2, batch_hook=_interrupt_after_one_batch)
    except migration.MigrationInterrupted:
        pass
    else:
        raise AssertionError('synthetic interruption hook did not stop the first run')
    _check(stage.exists(), 'interrupted stage copy remains available')
    _check(not output.exists(), 'interrupted conversion did not produce compact output')
    stage_db = sqlite3.connect(stage)
    checkpoint = stage_db.execute('SELECT value FROM ea_meta WHERE key=?',
                                  (migration.CHECKPOINT_KEY,)).fetchone()
    stage_version = stage_db.execute("SELECT value FROM ea_meta WHERE key='schema_version'").fetchone()[0]
    stage_db.close()
    _check(checkpoint is not None, 'committed batch cursor is stored in the same stage database')
    _check(stage_version == '5', 'only the separate stage copy activates schema v5')

    report = migration.run_migration(source, stage, output, batch_rows=2, encoder_threads=2)
    _check(report['outcome'] == 'verified_compact_v5_copy_ready', 'resume reaches final proof')
    _check(report['logicalVerification']['rowsCompared'] >= 10,
           'full logical comparison checks every fixture table row')
    _check(report['logicalVerification']['tableRows']['ea_experiment_budget'] == 2,
           'budget rows remain present and exact')
    _check(report['logicalVerification']['tableRows']['provenance_note'] == 1,
           'seed plan and provenance rows remain present and exact')
    _check(output.exists(), 'verified compact output is a separate file')
    _check(_source_projection(source) == original, 'source schema and legacy values stay untouched')

    output_db = sqlite3.connect(output)
    _check(output_db.execute("SELECT value FROM ea_meta WHERE key='schema_version'").fetchone()[0]
           == '5', 'compact output is schema v5')
    _check(output_db.execute('SELECT typeof(result),typeof(outcome) FROM ea_sample '
                             'WHERE sample_key="sample-1"').fetchone() == ('blob', 'null'),
           'completed payload frame is encoded and NULL remains NULL')
    _check(output_db.execute('SELECT typeof(outcome),outcome FROM ea_sample '
                             'WHERE sample_key="sample-2"').fetchone() == ('blob',
                                                                           b'{"legacy":"direct '
                                                                           b'sqlite BLOB", '
                                                                           b'"number":1e+30}'),
           'nonmagic legacy BLOB remains byte-identical')
    intent_meta, intent_blob = output_db.execute(
        'SELECT intent_json,full_intent FROM ea_experiment WHERE id=1').fetchone()
    exact = __import__('strategy_intent_codec').decode_intent(intent_meta, intent_blob)
    _check(exact == _raw_large('a'), 'intent full text preserves exact duplicate-key JSON spelling')
    _check(intent_meta.count('"compatibility"') == 2,
           'compatibility duplicate occurrences remain SQL-visible in lexical order')
    _check(output_db.execute('SELECT typeof(intent_json),full_intent FROM ea_experiment '
                             'WHERE id=3').fetchone() == ('null', None),
           'NULL intent remains NULL in both v5 columns')
    _check(output_db.execute('SELECT key FROM ea_meta WHERE key=?',
                             (migration.CHECKPOINT_KEY,)).fetchone() is None,
           'migration checkpoint is absent from the compact deliverable')
    _check(output_db.execute("SELECT seq FROM sqlite_sequence WHERE name='fixture_sequence'").fetchone()[0] == 1000,
           'deleted autoincrement high watermark is preserved')
    _check(output_db.execute('SELECT * FROM fixture_without_rowid').fetchall() == [('key',2,b'raw provenance')],
           'WITHOUT ROWID evidence survives compaction')
    _check(output_db.execute('SELECT * FROM fixture_text_key ORDER BY key').fetchall() == [('a','last'),('z','first')],
           'text primary keys survive implicit rowid gaps and compaction')
    output_db.close()
    return {'resume': True, 'allRowsCompared': report['logicalVerification']['rowsCompared'],
            'sourceVersion': original[0], 'targetVersion': report['outputSchemaVersion'],
            'endToEndSeconds': report['endToEndElapsedSeconds'],
            'compactSavingsBytes': report['compactOutputSavingsBytes']}


def _corrupt_frame_refusal(root):
    source = root / 'corrupt-source.sqlite'
    stage = root / 'corrupt-stage.sqlite'
    output = root / 'corrupt-output.sqlite'
    _create_source(source, version=3, corrupted=True)
    original = _source_projection(source)
    try:
        migration.run_migration(source, stage, output, batch_rows=2, encoder_threads=2)
    except payload_codec.PayloadCodecError:
        refused = True
    except migration.MigrationError as exc:
        refused = 'checksum' in str(exc).lower() or 'codec' in str(exc).lower() \
            or 'payload' in str(exc).lower()
    else:
        refused = False
    _check(refused, 'malformed reserved KAP frame is refused before row conversion')
    _check(_source_projection(source) == original, 'corruption refusal does not modify source')
    _check(not output.exists(), 'corrupt source does not produce a compact output')
    return {'reservedMagicCorruptionRefused': True, 'sourceUntouched': True,
            'partialBackupPreserved': Path(str(stage) + '.backup-in-progress').exists()}


def _resume_corruption_refusal(root):
    source = root / 'resume-corrupt-source.sqlite'
    stage = root / 'resume-corrupt-stage.sqlite'
    output = root / 'resume-corrupt-output.sqlite'
    _create_source(source, version=4)
    try:
        migration.run_migration(source, stage, output, batch_rows=1, encoder_threads=2,
                                batch_hook=_interrupt_after_one_batch)
    except migration.MigrationInterrupted:
        pass
    db = sqlite3.connect(stage)
    value = db.execute('SELECT result FROM ea_sample WHERE sample_key="sample-1"').fetchone()[0]
    if isinstance(value, bytes) and payload_codec.has_codec_magic(value):
        damaged = bytearray(value)
        damaged[8] ^= 0x01
        db.execute('UPDATE ea_sample SET result=? WHERE sample_key="sample-1"', (bytes(damaged),))
    else:
        raise AssertionError('interrupted first batch did not contain the expected encoded result')
    db.commit()
    db.close()
    try:
        migration.run_migration(source, stage, output, batch_rows=1, encoder_threads=2)
    except (payload_codec.PayloadCodecError, migration.MigrationError):
        refused = True
    else:
        refused = False
    _check(refused, 'resume refuses a corrupted framed cell in the staged copy')
    _check(not output.exists(), 'corrupted resumed stage does not create compact output')
    return {'corruptedResumeRefused': True, 'stagePreserved': stage.exists()}


def _bounded_pilot_checks(root):
    source = root / 'pilot-source.sqlite'
    stage = root / 'pilot-stage.sqlite'
    output = root / 'pilot-output.sqlite'
    _create_source(source, version=4)
    source_db = sqlite3.connect(source)
    stage_db = sqlite3.connect(stage)
    source_db.backup(stage_db)
    stage_db.close()
    source_db.close()

    pilot = migration.run_migration(source, stage, output, pilot_rows=1,
                                   batch_rows=1, encoder_threads=2)
    _check(pilot['outcome'] == 'pilot_paused_with_transactional_checkpoint',
           'pilot returns after its requested row budget')
    _check(pilot['rowsProcessedThisRun'] == 1, 'pilot processes exactly one row')
    _check(pilot['sourceIntegrity']['result'] == 'skipped_for_bounded_pilot',
           'pilot avoids an unbounded full-source scan')
    _check(pilot['stageCheckpoint']['plannedRows'] is None,
           'pilot does not run a full-table COUNT(*) just to fabricate a total')
    _check(not output.exists(), 'pilot leaves compact output for the full verified run')

    full = migration.run_migration(source, stage, output, batch_rows=2, encoder_threads=2)
    _check(full['outcome'] == 'verified_compact_v5_copy_ready',
           'full migration resumes after pilot and completes verification')
    _check(full['logicalVerification']['rowsCompared'] >= 15,
           'pilot continuation completes the same all-column proof')
    return {'boundedRows': pilot['rowsProcessedThisRun'],
            'fullResume': True, 'endToEndSeconds': full['endToEndElapsedSeconds']}


def _compaction_resume_checks(root):
    source, stage, output = (root / name for name in ('vacuum-source.sqlite', 'vacuum-stage.sqlite', 'vacuum-output.sqlite'))
    _create_source(source, version=3)
    original_vacuum = migration._vacuum_into

    def interrupt(*args):
        raise migration.MigrationInterrupted('synthetic interruption before VACUUM')

    migration._vacuum_into = interrupt
    try:
        try:
            migration.run_migration(source, stage, output)
        except migration.MigrationInterrupted:
            pass
        else:
            raise AssertionError('compaction interruption did not fire')
    finally:
        migration._vacuum_into = original_vacuum
    db = migration._connect_readonly(stage)
    try:
        checkpoint = migration._read_checkpoint(db)
        _check(checkpoint['phaseIndex'] == len(migration.TABLE_PHASES) and checkpoint['verified'],
               'completed conversion checkpoint survives compaction interruption')
    finally:
        db.close()
    resumed = migration.run_migration(source, stage, output)
    _check(resumed['rowsProcessedThisRun'] == 0, 'compaction resume does not reconvert completed rows')
    _check(resumed['outcome'] == 'verified_compact_v5_copy_ready', 'compaction resume verifies output')
    return {'completedCheckpointRetained': True, 'reconvertedRows': 0}


def main():
    root = Path(__file__).resolve().parent / ('.ka-storage-migration-check-' + uuid.uuid4().hex)
    root.mkdir()
    completed = False
    try:
        report = {'resumableConversion': _resumable_conversion_checks(root),
                  'corruptionRefusal': _corrupt_frame_refusal(root),
                  'resumedStageCorruptionRefusal': _resume_corruption_refusal(root),
                  'boundedPilot': _bounded_pilot_checks(root),
                  'compactionResume': _compaction_resume_checks(root)}
        completed = True
    finally:
        if completed:
            # These paths were created exclusively by this synthetic checker.
            for artifact in root.iterdir():
                if artifact.is_file():
                    artifact.unlink()
            root.rmdir()
        else:
            print('synthetic diagnostics preserved at %s' % root, file=sys.stderr)
    print(json.dumps(report, sort_keys=True, separators=(',', ':')))


if __name__ == '__main__':
    main()
