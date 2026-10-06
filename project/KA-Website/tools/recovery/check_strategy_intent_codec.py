"""Focused schema-5 intent storage and reader-contract checks; temporary SQLite only."""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import strategy_diagnostic_export as diagnostic_export
import strategy_encounter_overview as overview
import strategy_encounter_search as search
import strategy_experiment_store as ledger
import strategy_intent_codec as intent_codec
import strategy_payload_codec as payload_codec
import strategy_support_evidence as support
import check_support_evidence as support_fixtures


def _check(ok, message):
    if not ok:
        raise AssertionError(message)


def _base_intent(scope='intent-v5', **extra):
    value = dict(scope=scope, planned_budget=4, stopping={'maxRuns': 4}, purpose='support',
                 owner='community', policy={'finishPolicy': 'on-verdict'},
                 mechanicsRevision='mechanics-v5', encounterRevision='encounter-v5',
                 compatibility='compat-first', engineRevision='engine-v5',
                 measurementWindow='development', changedFields=['ownUnits.0.parameters.10'],
                 question='frozen support question',
                 observedScenarios={'candidate': {'encounterId': 29, 'defeatCount': 2,
                                                  'which': 'normal', 'padding': 'x' * 3000},
                                    'reference': {'encounterId': 29, 'defeatCount': 2,
                                                  'which': 'reference', 'padding': 'y' * 3000}})
    value.update(extra)
    return value


def _search_reader(db, experiment_id):
    coordinator = object.__new__(search.Coordinator)
    coordinator._intent_cache = {}
    coordinator._outcomes_cache = {}
    coordinator._compatible_ids_cache = None
    coordinator._compatibility = lambda: 'live'
    coordinator._mechanics_revision = lambda: 'mechanics-v5'
    coordinator.revision = 'revision-v5'
    coordinator._compatible_search_digests = lambda: ('compat-first',)
    coordinator._intent_matches_live = lambda intent: (
        intent.get('compatibility') == 'compat-last'
        and intent.get('engineRevision') == 'engine-v5'
        and intent.get('mechanicsRevision') == 'mechanics-v5'
        and intent.get('policy') == {'finishPolicy': 'on-verdict'})
    full = coordinator._intent(db, experiment_id)
    selected = coordinator._compatible_experiment_ids(db)
    return full, selected


def _projection_checks(db):
    raw = (
        '{"compatibil\\u0069ty":"compat-first","compatibility":"compat-last",'
        '"engineRevision":1e+03,"engineRevision":"engine-v5",'
        '"mechanicsRevision":"mechanics-v5","policy":{"finishPolicy":"on-verdict",'
        '"nested":{"n":1.2300,"n":2}},"measurementWindow":"development",'
        '"changedFields":["ownUnits.0.parameters.10"],'
        '"observedScenarios":{"candidate":{"encounterId":29,"which":"sql-first"}},'
        '"observedScenarios":{"candidate":{"encounterId":29,"which":"python-last"}},'
        '"question":"first question","question":"last question",'
        '"padding":"' + ('p' * 4096) + '"}')
    metadata, blob = intent_codec.encode_intent(raw)
    _check(isinstance(blob, bytes) and payload_codec.has_codec_magic(blob),
           'eligible full intents are stored as framed compressed BLOBs')
    _check(intent_codec.decode_intent(metadata, blob) == raw,
           'full intent codec round-trips exact source text')
    _check('observedScenarios' not in metadata and 'question' not in metadata,
           'large scenarios and question text stay outside the thin projection')
    _check('1e+03' in metadata and '1.2300' in metadata and 'compatibil\\u0069ty' in metadata,
           'projection preserves selected lexical spellings and escaped keys')

    for field in intent_codec.PROJECTION_FIELDS:
        path = '$.' + field
        source_value = db.execute('SELECT json_extract(?, ?)', (raw, path)).fetchone()[0]
        projected_value = db.execute('SELECT json_extract(?, ?)', (metadata, path)).fetchone()[0]
        _check(type(source_value) is type(projected_value) and source_value == projected_value,
               'JSON1 projection parity for %s' % field)
    first_observed = json.loads(db.execute(
        "SELECT json_extract(?, '$.observedScenarios')", (raw,)).fetchone()[0])
    last_observed = json.loads(raw)['observedScenarios']
    _check(first_observed['candidate']['which'] == 'sql-first'
           and last_observed['candidate']['which'] == 'python-last',
           'SQL first-duplicate and Python last-duplicate behavior remain distinct')
    _check(intent_codec.encode_intent('{"small":1}') == ('{"small":1}', None),
           'small intents retain the full-text fallback')
    _check(intent_codec.encode_intent('{"bad":}') == ('{"bad":}', None),
           'malformed intents retain the full-text fallback')
    oversized = '{"padding":"' + ('x' * (payload_codec.MAX_RAW_BYTES + 8)) + '"}'
    fallback, fallback_blob = intent_codec.encode_intent(oversized)
    _check(fallback == oversized and fallback_blob is None,
           'oversized intents retain the full-text fallback')
    return raw, metadata, blob


def _schema_mode_checks():
    db = sqlite3.connect(':memory:')
    try:
        _check(ledger.initialize(db) == 5, 'new ledgers initialize at schema v5')
        _check(ledger.activate_schema_v4(db) == 5,
               'legacy v4 activation helper is harmless on a v5 ledger')

        db.execute("UPDATE ea_meta SET value='3' WHERE key='schema_version'")
        _check(ledger.initialize(db) == 3, 'existing v3 marker survives initialize')
        v3_id = ledger.create_experiment(db, _base_intent(scope='v3-full-text'))
        v3_cells = db.execute('SELECT typeof(intent_json), full_intent FROM ea_experiment WHERE id=?',
                              (v3_id,)).fetchone()
        _check(v3_cells == ('text', None), 'schema v3 writes full intent as TEXT only')

        _check(ledger.activate_schema_v4(db) == 4, 'explicit v3 to v4 activation stays available')
        _check(ledger.initialize(db) == 4, 'existing v4 marker survives initialize')
        v4_id = ledger.create_experiment(db, _base_intent(scope='v4-full-text'))
        v4_cells = db.execute('SELECT typeof(intent_json), full_intent FROM ea_experiment WHERE id=?',
                              (v4_id,)).fetchone()
        _check(v4_cells == ('text', None), 'schema v4 keeps full intent TEXT until explicit v5')

        _check(ledger.activate_schema_v5(db) == 5, 'explicit schema v5 activation succeeds')
        _check(ledger.initialize(db) == 5, 'v5 initialize is idempotent')
        v5_id = ledger.create_experiment(db, _base_intent(scope='v5-compressed'))
        v5_cells = db.execute('SELECT typeof(intent_json), typeof(full_intent) '
                              'FROM ea_experiment WHERE id=?', (v5_id,)).fetchone()
        _check(v5_cells == ('text', 'blob'), 'schema v5 separates thin TEXT and full BLOB')
        return dict(newLedger=5, legacyAfterInitialize=3, schema4=4, explicitSchema5=5,
                    fullIntentBlob=True)
    finally:
        db.close()


def _integration_checks():
    db = sqlite3.connect(':memory:')
    db.row_factory = sqlite3.Row
    try:
        _check(ledger.initialize(db) == 5, 'integration fixture starts at v5')
        intent = _base_intent(scope='integration')
        experiment_id = ledger.create_experiment(db, intent)
        stored_key = db.execute('SELECT request_key FROM ea_experiment WHERE id=?',
                                 (experiment_id,)).fetchone()[0]
        key_fields = ledger._intent_fields(intent)
        key_fields['intent_json'] = ledger._canonical(intent)
        _check(stored_key == ledger._request_key(key_fields),
               'request_key derivation remains based on the original full canonical intent')

        raw = (
            '{"compatibility":"compat-first","compatibility":"compat-last",'
            '"engineRevision":"engine-v5","mechanicsRevision":"mechanics-v5",'
            '"policy":{"finishPolicy":"on-verdict"},'
            '"measurementWindow":"development",'
            '"changedFields":["ownUnits.0.parameters.10"],'
            '"observedScenarios":{"candidate":{"encounterId":29,"defeatCount":2,'
            '"which":"sql-first","padding":"' + ('a' * 2048) + '"},'
            '"reference":{"encounterId":29,"defeatCount":2,"which":"reference"}},'
            '"observedScenarios":{"candidate":{"encounterId":29,"defeatCount":2,'
            '"which":"python-last","padding":"' + ('b' * 2048) + '"},'
            '"reference":{"encounterId":29,"defeatCount":2,"which":"reference"}},'
            '"question":"first","question":"last",'
            '"padding":"' + ('p' * 4096) + '"}')
        metadata, blob = intent_codec.encode_intent(raw)
        _check(blob is not None, 'integration intent fixture compresses')
        db.execute('UPDATE ea_experiment SET intent_json=?, full_intent=? WHERE id=?',
                   (metadata, blob, experiment_id))
        _check(ledger.experiment_intent_raw(db, experiment_id) == raw,
               'store raw reader returns exact compressed full intent')
        full = ledger.experiment_intent(db, experiment_id)
        _check(full['observedScenarios']['candidate']['which'] == 'python-last',
               'canonical Python full reader keeps last duplicate semantics')
        sql_first = ledger.experiment_intent_json_extract(db, experiment_id,
                                                          '$.observedScenarios')
        _check(json.loads(sql_first)['candidate']['which'] == 'sql-first',
               'SQLite gate reader extracts first duplicate from the exact decoded text')
        _check(db.execute("SELECT json_extract(intent_json, '$.observedScenarios') "
                          'FROM ea_experiment WHERE id=?', (experiment_id,)).fetchone()[0] is None,
               'thin metadata does not retain the large scenario map')

        db.execute('INSERT INTO ea_sample_link(experiment_id,candidate_id,seed_a,seed_b,sample_key,'
                   'reused,charged_experiment_id,created_at) VALUES(?,?,?,?,?,?,?,?)',
                   (experiment_id, 'candidate', 1, 2, 'sample-v5', 0, experiment_id, 1.0))
        index = overview.FrozenIntentIndex()
        proof = index.identity(db, 'candidate')
        scenario = index.scenario(db, 'candidate')
        _check(proof['encounterId'] == 29 and scenario['scenario']['which'] == 'python-last',
               'overview frozen identity and scenario readers hydrate compressed intent')

        coordinator_intent, compatible = _search_reader(db, experiment_id)
        _check(coordinator_intent['observedScenarios']['candidate']['which'] == 'python-last',
               'search full intent hydration uses the canonical reader')
        _check(experiment_id in compatible,
               'search SQL prefilter uses thin first-duplicate metadata and retains last-key checks')

        exported = diagnostic_export._experiments_for(db, [experiment_id])[experiment_id]
        view = diagnostic_export._experiment_view(exported)
        _check(view['question'] == 'last',
               'diagnostic export obtains question from the exact full-intent BLOB')

        valid_blob = blob
        corrupted_blob = bytearray(valid_blob)
        corrupted_blob[-1] ^= 1
        db.execute('UPDATE ea_experiment SET full_intent=? WHERE id=?',
                   (bytes(corrupted_blob), experiment_id))
        try:
            ledger.experiment_intent_raw(db, experiment_id)
        except payload_codec.PayloadCodecError:
            corrupt_failed_closed = True
        else:
            corrupt_failed_closed = False
        _check(corrupt_failed_closed, 'corrupted exact-intent BLOB fails closed')
        db.execute('UPDATE ea_experiment SET full_intent=? WHERE id=?',
                   (valid_blob, experiment_id))

        oversized_blob = b'X' * (payload_codec.MAX_ENCODED_BYTES + 100)
        db.execute('UPDATE ea_experiment SET full_intent=? WHERE id=?',
                   (oversized_blob, experiment_id))
        bounded_export = diagnostic_export._experiments_for(db, [experiment_id])[experiment_id]
        _check(isinstance(bounded_export['full_intent'], bytes)
               and len(bounded_export['full_intent']) == payload_codec.MAX_ENCODED_BYTES + 1,
               'diagnostic export reads at most the codec bound plus one byte')
        _check(diagnostic_export._experiment_view(bounded_export)['question'] is None,
               'diagnostic export fails closed on an oversized corrupt intent frame')
        db.execute('UPDATE ea_experiment SET full_intent=? WHERE id=?',
                   (valid_blob, experiment_id))

        boundary_payload = json.dumps(dict(
            study=dict(referenceId='reference'), assessment=dict(candidateId='candidate')))
        db.execute('INSERT INTO ea_boundary(experiment_id,kind,frozen_reference,payload,created_at) '
                   'VALUES(?,?,?,?,?)', (experiment_id, 'support', 'reference', boundary_payload, 1.0))
        boundary_rows = support._support_boundary_rows(db, 'candidate', 10)
        _check(len(boundary_rows) == 1 and boundary_rows[0]['changedFields'] is not None
               and 'observedScenarios' not in boundary_rows[0],
               'support boundary query selects bounded thin metadata without eager full scenarios')
        late_row = dict(boundary_rows[0], windowIntent='confirmation')
        original_extract = ledger.experiment_intent_json_extract
        calls = []
        ledger.experiment_intent_json_extract = lambda *args: calls.append(args)
        try:
            _reading, rejection = support._controlled_support_boundary(
                db, late_row, {'ownUnits': []}, 'community', 'compat-first', 'candidate')
        finally:
            ledger.experiment_intent_json_extract = original_extract
        _check(rejection == 'notDevelopment' and not calls,
               'support reader avoids full-intent decode before cheap boundary gates pass')

        support_parity = _controlled_support_parity_checks(db)
        _check(support_parity, 'controlled support outcome is identical for legacy TEXT and v5 BLOB')

        conflict_order = _confirmation_conflict_checks(db)
        _check(conflict_order, 'search confirmation checks digest conflict before revision/fallback')
        legacy_reader = _legacy_reader_checks()
        _check(legacy_reader, 'store and diagnostic readers open a pre-v5 library read-only')

        return dict(exactRaw=True, distinctDuplicateSemantics=True, overview=True, search=True,
                    diagnosticExport=True, exportBlobBound=True, corruptedBlobFailsClosed=True,
                    legacyReader=legacy_reader, supportLazy=True, controlledSupportParity=True,
                    confirmationConflictOrder=True)
    finally:
        db.close()


def _controlled_support_parity_checks(db):
    """Use the existing real paired-support fixture against full TEXT and compressed intent."""
    reference_id, candidate_id = 'ref-dup-support', 'candidate-dup-support'
    child = support_fixtures.build_scenario([
        (support_fixtures.DPS_INDEX, support_fixtures.HP, 1200)])
    wrong_reference = support_fixtures.build_scenario([
        (support_fixtures.DPS_INDEX, support_fixtures.DEF, 100)])
    field = support_fixtures.DPS_HP_FIELD
    experiment_id = support_fixtures.support_experiment(
        db, child_id=candidate_id, reference_id=reference_id,
        child_scenario=child, reference_scenario=support_fixtures.SCENARIO,
        changed_fields=[field], tag='v5-duplicate-observed')
    support_fixtures.boundary(
        db, experiment_id, candidate_id, reference_id=reference_id,
        question_field=field, value=1200, child_scenario=child,
        reference_scenario=support_fixtures.SCENARIO, changed_fields=[field])

    first_observed = {reference_id: support_fixtures.SCENARIO, candidate_id: child}
    last_observed = {reference_id: wrong_reference, candidate_id: child}
    raw = '{' + ','.join((
        '"compatibility":' + json.dumps(support_fixtures.COMPAT),
        '"engineRevision":' + json.dumps(support_fixtures.ENGINE),
        '"mechanicsRevision":' + json.dumps(support_fixtures.MECH),
        '"policy":' + json.dumps(support_fixtures.POLICY, separators=(',', ':')),
        '"measurementWindow":"development"',
        '"changedFields":' + json.dumps([field], separators=(',', ':')),
        '"observedScenarios":' + json.dumps(first_observed, separators=(',', ':')),
        '"observedScenarios":' + json.dumps(last_observed, separators=(',', ':')),
        '"padding":' + json.dumps('p' * 5000))) + '}'
    metadata, full_blob = intent_codec.encode_intent(raw)
    _check(full_blob is not None, 'support duplicate-observation fixture compresses')

    # The Python full reader deliberately sees the LAST map, where the reference also changed DEF.
    db.execute('UPDATE ea_experiment SET intent_json=?,full_intent=NULL WHERE id=?',
               (raw, experiment_id))
    last_reference = ledger.experiment_intent(db, experiment_id)['observedScenarios'][reference_id]
    _check(support._support_axis_changes(last_reference, child)
           == {support_fixtures.HP, support_fixtures.DEF},
           'Python last duplicate would classify the support point as multi-axis')
    first_raw = ledger.experiment_intent_json_extract(db, experiment_id, '$.observedScenarios')
    first_reference = json.loads(first_raw)[reference_id]
    _check(support._support_axis_changes(first_reference, child) == {support_fixtures.HP},
           'SQLite first duplicate retains the valid single-axis reference')
    legacy_result = support.diagnose(
        db, candidate_id=candidate_id, scenario=child, owner='community',
        compatibility=support_fixtures.COMPAT)

    db.execute('UPDATE ea_experiment SET intent_json=?,full_intent=? WHERE id=?',
               (metadata, full_blob, experiment_id))
    compressed_result = support.diagnose(
        db, candidate_id=candidate_id, scenario=child, owner='community',
        compatibility=support_fixtures.COMPAT)
    return (legacy_result == compressed_result
            and legacy_result['supported'] is True
            and legacy_result['counts']['boundaryAdmitted'] == 1
            and legacy_result['counts']['boundaryRejections']['multiAxis'] == 0)


def _confirmation_conflict_checks(db):
    """Exercise the production search selector and confirmation recovery loop over v5 readers."""
    first_scenario = {'encounterId': 29, 'revision': 'current', 'padding': 'a' * 3000}
    conflicting_scenario = {'encounterId': 29, 'revision': 'stale', 'padding': 'b' * 3000}
    fallback_scenario = {'encounterId': 29, 'revision': 'current', 'padding': 'c' * 3000}

    def intent(scope, candidate_id, scenario, encounter_revision):
        return _base_intent(
            scope=scope,
            compatibility='compat-first',
            engineRevision='engine-v5',
            mechanicsRevision='mechanics-v5',
            policy={'finishPolicy': 'on-verdict'},
            encounterRevision=encounter_revision,
            measurementWindow='development',
            observedScenarios={candidate_id: scenario})

    first_id = ledger.create_experiment(
        db, intent('search-conflict-first', 'conflict-candidate', first_scenario, 'current'))
    second_id = ledger.create_experiment(
        db, intent('search-conflict-second', 'conflict-candidate', conflicting_scenario, 'bad-revision'))
    stale_id = ledger.create_experiment(
        db, intent('search-fallback-stale', 'fallback-candidate', fallback_scenario, 'bad-revision'))

    coordinator = object.__new__(search.Coordinator)
    coordinator._intent_cache = {}
    coordinator._outcomes_cache = {}
    coordinator._compatible_ids_cache = None
    coordinator._compatibility = lambda: 'compat-first'
    coordinator._mechanics_revision = lambda: 'mechanics-v5'
    coordinator._compatible_search_digests = lambda: ('compat-first',)
    coordinator._intent_matches_live = lambda frozen: (
        frozen.get('compatibility') == 'compat-first'
        and frozen.get('engineRevision') == 'engine-v5'
        and frozen.get('mechanicsRevision') == 'mechanics-v5'
        and frozen.get('policy') == {'finishPolicy': 'on-verdict'})
    coordinator.revision = 'engine-v5'
    coordinator.encounter = 29
    revisions_seen = []
    coordinator._encounter_revision = lambda scenario: revisions_seen.append(
        scenario['revision']) or scenario['revision']

    resident_calls = []

    class Resident:
        @staticmethod
        def scenario(candidate_id):
            resident_calls.append(('scenario', candidate_id))
            return {'encounterId': 29, 'candidateId': candidate_id}

        @staticmethod
        def observed_scenario(scenario, candidate=None):
            resident_calls.append(('observed', candidate))
            return {'encounterId': 29, 'candidateId': candidate, 'source': 'resident'}

    conflict = coordinator._confirmation_scenario(db, 'conflict-candidate', Resident())
    if not (conflict is None and resident_calls == [] and revisions_seen == ['current']
            and first_id < second_id < stale_id):
        return False

    revisions_seen.clear()
    fallback = coordinator._confirmation_scenario(db, 'fallback-candidate', Resident())
    return (fallback == {'encounterId': 29, 'candidateId': 'fallback-candidate',
                         'source': 'resident'}
            and resident_calls == [('scenario', 'fallback-candidate'),
                                  ('observed', 'fallback-candidate')]
            and revisions_seen == ['current'])


def _legacy_reader_checks():
    """Read a pre-v5 library without initialize(), activation, or a schema write."""
    db = sqlite3.connect(':memory:')
    db.row_factory = sqlite3.Row
    db.execute('CREATE TABLE ea_experiment('
               'id INTEGER PRIMARY KEY, request_key TEXT, scope TEXT, parent_id INTEGER, '
               'reference_id TEXT, policy TEXT, mechanics_revision TEXT, encounter_revision TEXT, '
               'purpose TEXT, owner TEXT, owner_share REAL, fixed_fields TEXT, changed_fields TEXT, '
               'planned_budget INTEGER, stopping TEXT, session_id INTEGER, measurement_window TEXT, '
               'created_at REAL, intent_json TEXT)')
    raw = json.dumps({'question': 'old library question', 'purpose': 'legacy'}, separators=(',', ':'))
    db.execute('INSERT INTO ea_experiment VALUES(1,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
               ('legacy', 'community', None, None, None, 'mechanics-v5', 'encounter-v5',
                'support', 'community', 1.0, '{}', '[]', 4, '{}', None,
                '"development"', 1.0, raw))
    db.execute('PRAGMA query_only=ON')
    try:
        exact = ledger.experiment_intent_raw(db, 1)
        view = diagnostic_export._experiment_view(
            diagnostic_export._experiments_for(db, [1])[1])
        return exact == raw and view['question'] == 'old library question'
    finally:
        db.close()


def main():
    sql_db = sqlite3.connect(':memory:')
    try:
        _projection_checks(sql_db)
    finally:
        sql_db.close()
    report = dict(projection=True, schemaModes=_schema_mode_checks(), readers=_integration_checks())
    print(json.dumps(report, sort_keys=True))


if __name__ == '__main__':
    main()
