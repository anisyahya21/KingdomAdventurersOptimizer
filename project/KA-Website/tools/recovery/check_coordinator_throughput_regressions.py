"""Regression checks for pruned confirmation, scoped reads and immutable UI publication."""
import json
import sqlite3
import check_cohort_dispatch as fixture
import strategy_encounter_search as search
import strategy_experiment_store as ledger
import strategy_mechanics as mechanics


def check_legacy_candidate_prefix(scenario, encounter):
    db = sqlite3.connect(':memory:')
    db.executescript('''
        CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE candidate(id TEXT PRIMARY KEY, scenario TEXT NOT NULL, created INTEGER NOT NULL);
        CREATE TABLE candidate_meta(id TEXT PRIMARY KEY, encounter INTEGER NOT NULL);
        CREATE INDEX candidate_meta_encounter ON candidate_meta(encounter);
        CREATE TABLE lineage(candidate TEXT NOT NULL, source TEXT);
        CREATE TABLE history_build_summary(candidate_id TEXT PRIMARY KEY,
            sample_count INTEGER, resolved_count INTEGER);
    ''')
    db.execute("INSERT INTO meta VALUES('historySummaryVersion','1')")
    payload = json.dumps(scenario, sort_keys=True)
    for index in range(30):
        candidate_id = 'legacy-%02d' % index
        db.execute('INSERT INTO candidate VALUES(?,?,?)', (candidate_id, payload, index))
        db.execute('INSERT INTO candidate_meta VALUES(?,?)', (candidate_id, encounter))
    coordinator = search.Coordinator(path=':memory:', encounter=encounter, reference=None)
    calls = []
    traced = []
    original_admits = search.students.community_admits
    db.set_trace_callback(traced.append)
    try:
        search.students.community_admits = lambda candidate, parent=None: (calls.append(candidate) or True, [])
        selected = coordinator._legacy_candidate_ids(db)
    finally:
        search.students.community_admits = original_admits
        coordinator.close()
        db.close()
    expected = ['legacy-%02d' % index for index in range(29, 5, -1)]
    query = next((sql for sql in traced if 'FROM candidate c' in sql and
                  'candidate_meta m' in sql), '')
    assert selected == expected, (selected, expected)
    assert len(calls) == search.LEGACY_MAXIMUM_CANDIDATES, len(calls)
    assert 'WHERE m.encounter=' in query and '? IS NULL OR' not in query, query
    print('PASS: legacy priors keep the ordered 24-candidate prefix and use encounter equality')


path = fixture.new_dir('throughput-regressions') / 'lib.sqlite'
encounter, reference, scenario = fixture.prep(path)
check_legacy_candidate_prefix(scenario, encounter)
pool = fixture.GatedPool(mechanics.normalize_scenario(scenario)['ownUnits'])
store, coord = fixture.build_coordinator(path, fixture.purposes(16,128), encounter,
                                        reference, pool, workers=16)
try:
    ledger.initialize(store.db)
    assert coord._confirmation_scenario(store.db, reference, store) is not None, 'a resident supplied comparator needs no prior development samples'
    coord.run_pass(store)
    advance_timing = (coord.state.get('timings') or {}).get('lastAdvanceStagesSeconds') or {}
    assert {'retireIncompatible', 'hydrate', 'experimentState', 'finishExperiment',
            'confirmation', 'planRound', 'other', 'total', 'cohortMembers',
            'finishedExperiments'} <= set(advance_timing), advance_timing
    duration_keys = ('retireIncompatible', 'hydrate', 'experimentState', 'finishExperiment',
                     'confirmation', 'planRound', 'other')
    assert all(isinstance(advance_timing[key], (int, float))
               and advance_timing[key] >= 0 for key in duration_keys), advance_timing
    assert abs(sum(advance_timing[key] for key in duration_keys)
               - advance_timing['total']) < 0.001, advance_timing
    assert advance_timing['cohortMembers'] >= 0
    # The campaign fleet needs only this compact rollover view inside each pass; it constructs the
    # full per-encounter report once, after all coordinators have advanced.
    expected_progress = {
        'idle': bool(coord.state.get('idle')),
        'experiments': len(coord.state.get('completedExperiments') or []),
        'retiredFrozenPlanCount': len(coord.state.get('retiredFrozenPlans') or []),
    }
    original_report = coord.report
    report_calls = []
    try:
        def forbidden_report():
            report_calls.append('called')
            raise AssertionError('compact pass result must not materialize a full report')
        coord.report = forbidden_report
        compact = coord.run_pass(store, running=False, harvested_entries=[],
                                 return_report=False)
        assert compact == {'progress': expected_progress,
                           'budgets': {'session': coord._last_budget_rows}}
        assert not report_calls, report_calls

        marker = {'fullReportSentinel': True}
        def default_report():
            report_calls.append('called')
            return marker
        coord.report = default_report
        full = coord.run_pass(store, running=False, harvested_entries=[])
        assert full is marker, 'the default still returns the full report'
        assert report_calls == ['called'], report_calls
    finally:
        coord.report = original_report
    cohort = list(coord._cohort())
    frozen = {m['candidateId']: m['observedCandidate'] for m in cohort}
    published = coord.report()
    before = json.dumps(published, sort_keys=True)
    # Prune BOTH the nominee candidates and the reference before the last development result.
    for cid in [reference, *frozen]:
        store.db.execute('DELETE FROM candidate WHERE id=?', (cid,))
    store.db.commit()
    pool.release(16)
    coord.run_pass(store)
    finish_timing = (coord.state.get('timings') or {}).get('lastFinishStagesSeconds') or {}
    finish_keys = {'outcomes', 'aggregate', 'pairedEvidence', 'portfolioWrite',
                   'mechanismArchive', 'boundaryAssessment', 'cohortRemoval',
                   'portfolioRefresh', 'adviserFit', 'confirmation', 'other', 'total',
                   'finishedExperiments'}
    assert finish_keys <= set(finish_timing), finish_timing
    assert finish_timing['finishedExperiments'] > 0, finish_timing
    finish_duration_keys = finish_keys - {'finishedExperiments'}
    assert all(isinstance(finish_timing[key], (int, float)) and finish_timing[key] >= 0
               for key in finish_duration_keys), finish_timing
    assert abs(sum(finish_timing[key] for key in finish_duration_keys if key != 'total')
               - finish_timing['total']) < 0.003, finish_timing
    confirmation = coord.state.get('confirmation') or {}
    assert confirmation.get('frozen'), coord.report()
    intent = coord._intent(store.db, confirmation['experimentId'])
    assert intent['observedScenarios'][confirmation['nominee']] == json.loads(json.dumps(frozen[confirmation['nominee']]))
    assert all(intent.get(k) for k in ('mechanicsRevision','encounterRevision','engineRevision'))
    assert json.dumps(published, sort_keys=True) == before, 'published report aliases mutable state'
    ids = coord._compatible_experiment_ids(store.db)
    actual = coord._deduped_outcomes(store.db, ids)
    expected = {}; seen = set()
    for row in ledger.outcomes(store.db):
        key = (row['candidateId'], row['sampleKey'])
        if row['experimentId'] in ids and row['state']=='completed' and key not in seen:
            seen.add(key); expected.setdefault(row['candidateId'], []).append(row['outcome'])
    assert actual == expected
    old = ledger.outcomes
    def forbid(*args,**kwargs): raise AssertionError('unchanged outcomes decoded again')
    ledger.outcomes=forbid
    try: assert coord._deduped_outcomes(store.db, ids) == expected
    finally: ledger.outcomes=old
    assert ledger.recover(store.db, count_completed=False)['completedCount'] is None
    print('PASS: pruned nominee and reference confirm from exact frozen builds; revisions preserved; '
          'UI snapshot immutable; scoped cache equals uncached deduplication; unchanged outcomes not redecoded')
finally:
    coord.close(); store.close()
