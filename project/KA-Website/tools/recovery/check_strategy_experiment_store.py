"""Focused checks for `strategy_experiment_store` and `strategy_search_mode`.

Temporary SQLite databases only; no library, no simulator, nothing under the repo is written.
Pins the FROZEN one-ordered-pair-per-run API and every correctness contract the coordinator relies on:
pair validation (scalar/batch/length/range), one-pair-one-budget, distinct pairs, sample coalescing,
atomic rollback after a budget debit, global holdout freshness, confirmation budget + freeze
idempotence + no overwrite, intent-policy-derived outcomes, and config budget-matrix conservation.

    python check_strategy_experiment_store.py
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import strategy_experiment_store as store
import strategy_search_mode as mode


def _intent(**overrides):
    intent = dict(scope='encounter-19', planned_budget=10, stopping={'maxRuns': 10},
                  purpose='improvement', fixedFields={'enemy': '19'}, changedFields={'atk': 1},
                  policy={'finishPolicy': 'on-verdict'}, ownerShare=1.0)
    intent.update(overrides)
    return intent


def _result(pair, **extra):
    payload = dict(verdict=1, finishPolicy='ignore-this', seeds=[pair[0], pair[1]],
                   rewardOutcome=dict(awardedChests=None,
                                      awardedBasis='unknown-win-without-certificate',
                                      pendingChests=2))
    payload.update(extra)
    return payload


def _connect(path):
    db = sqlite3.connect(path)
    store.initialize(db)
    db.commit()
    return db


def _session_config(improvement_runs, extra=None):
    config = mode.default_config('all-strategy')
    matrix = {purpose: 0 for purpose in mode.PURPOSES}
    matrix['improvement'] = improvement_runs
    if extra:
        matrix.update(extra)
    config['purposes'] = dict(matrix)
    config['budgets'] = {'community': dict(matrix)}
    return config


def pair_contract_checks(tmpdir):
    checks = {}
    db = _connect(os.path.join(tmpdir, 'pair.sqlite'))
    experiment = store.create_experiment(db, _intent(planned_budget=10, encounterRevision='c'))

    # ONE ordered pair consumes ONE run; a duplicate reserve is an idempotent no-op.
    assert store.reserve(db, experiment, 'cand', [123, 456]) is True
    assert store.reserve(db, experiment, 'cand', (123, 456)) is True
    reserved = db.execute('SELECT reserved FROM ea_experiment_budget WHERE experiment_id=?',
                          (experiment,)).fetchone()[0]
    assert reserved == 1, reserved
    checks['onePairOneRun'] = reserved

    # Same first component, different second -> a DISTINCT pair.
    assert store.reserve(db, experiment, 'cand', (123, 457)) is True
    assert db.execute('SELECT COUNT(*) FROM ea_sample WHERE seed_a=123 AND seed_b=457'
                      ).fetchone()[0] == 1
    # Equal components are a valid pair.
    assert store.reserve(db, experiment, 'cand', (123, 123)) is True
    # Reversed pair is DISTINCT from the ordered pair.
    assert store.reserve(db, experiment, 'cand', (456, 123)) is True
    distinct = {tuple(row) for row in db.execute('SELECT seed_a, seed_b FROM ea_sample')}
    assert (123, 456) in distinct and (123, 457) in distinct and (123, 123) in distinct
    assert (456, 123) in distinct
    checks['orderedPairsDistinct'] = sorted(distinct)

    # Scalar seeds, batches, wrong lengths and out-of-range/non-bool elements are refused.
    for bad in (123, 123.0, [123], (123,), [1, 2, 3], [[123, 456]], [(123, 456)],
                [123, [456]], '123', None, (123, True), (False, 5), (-1, 5), (0, 2 ** 31),
                (2 ** 31, 0)):
        try:
            store.reserve(db, experiment, 'cand', bad)
            raise AssertionError('seed_pair %r must be refused' % (bad,))
        except ValueError:
            pass
    checks['invalidPairsRefused'] = True
    db.close()
    return checks


def one_pair_budget_checks(tmpdir):
    """The Astra regression: planned_budget=1 and reserve(pair) must succeed and charge exactly 1."""
    checks = {}
    db = _connect(os.path.join(tmpdir, 'budget.sqlite'))
    experiment = store.create_experiment(db, _intent(planned_budget=1, encounterRevision='c'))
    assert store.reserve(db, experiment, 'candidate', [123, 456]) is True, \
        'one ordered pair must fit a one-battle budget'
    budget = db.execute('SELECT total, reserved, completed FROM ea_experiment_budget '
                        'WHERE experiment_id=?', (experiment,)).fetchone()
    assert budget == (1, 1, 0), budget
    assert db.execute('SELECT COUNT(*) FROM ea_sample').fetchone()[0] == 1
    assert db.execute('SELECT COUNT(*) FROM ea_sample_link').fetchone()[0] == 1
    # A second, distinct pair cannot fit the exhausted one-run budget.
    assert store.reserve(db, experiment, 'candidate', [123, 457]) is False
    assert db.execute('SELECT reserved FROM ea_experiment_budget WHERE experiment_id=?',
                      (experiment,)).fetchone()[0] == 1
    checks['onePairChargedOne'] = dict(total=budget[0], reserved=budget[1])
    db.close()
    return checks


def _session_intent(session, **overrides):
    intent = _intent(owner='community', sessionId=session, purpose='improvement',
                     encounterRevision='c')
    intent.update(overrides)
    return intent


def _snapshot(db, experiment, session=None):
    snap = dict(
        samples=db.execute('SELECT COUNT(*) FROM ea_sample').fetchone()[0],
        links=db.execute('SELECT COUNT(*) FROM ea_sample_link').fetchone()[0],
        budget=db.execute('SELECT total, reserved, completed FROM ea_experiment_budget '
                          'WHERE experiment_id=?', (experiment,)).fetchone())
    if session is not None:
        snap['session'] = db.execute('SELECT total, reserved, completed FROM ea_session_budget '
                                     'WHERE session_id=? AND owner=? AND purpose=?',
                                     (session, 'community', 'improvement')).fetchone()
    return snap


def atomic_rollback_checks(tmpdir):
    """A crash after the budget debit must leave every row/counter unchanged."""
    checks = {}
    db = _connect(os.path.join(tmpdir, 'atomic.sqlite'))
    session = store.configure_session(db, _session_config(3))
    experiment = store.create_experiment(db, _session_intent(session, scope='atomic',
                                                             planned_budget=10))
    db.execute("CREATE TRIGGER ea_boom BEFORE INSERT ON ea_sample_link "
               "BEGIN SELECT RAISE(ABORT, 'boom'); END")
    db.commit()
    before = _snapshot(db, experiment, session)

    # Crash inside the single write transaction (with db): the session debit precedes the failing link.
    try:
        store.reserve(db, experiment, 'cand', (11, 12))
        raise AssertionError('the injected failure must propagate')
    except sqlite3.Error:
        pass
    assert _snapshot(db, experiment, session) == before, 'transaction split the budget debit from ' \
        'the sample writes'

    # Same fault while an outer transaction is open: the savepoint must roll the unit back too.
    db.execute('BEGIN')
    try:
        store.reserve(db, experiment, 'cand', (11, 12))
        raise AssertionError('the injected failure must propagate inside a savepoint')
    except sqlite3.Error:
        pass
    assert _snapshot(db, experiment, session) == before, 'savepoint split the budget debit'
    db.rollback()

    db.execute('DROP TRIGGER ea_boom')
    db.commit()
    assert store.reserve(db, experiment, 'cand', (11, 12)) is True
    after = _snapshot(db, experiment, session)
    assert after['session'] == (3, 1, 0) and after['samples'] == 1 and after['links'] == 1
    checks['rollbackUnchanged'] = dict(before=before['session'], after=after['session'])
    db.close()
    return checks


def nested_savepoint_checks(tmpdir):
    """Public writes preserve a caller's transaction; cleanup keeps the primary failure visible."""
    db = _connect(os.path.join(tmpdir, 'nested-savepoint.sqlite'))
    session = store.configure_session(db, _session_config(10))
    db.commit()

    # create_experiment used to use `with db`, which committed the caller's savepoint outright.
    db.execute('SAVEPOINT caller_create')
    experiment = store.create_experiment(
        db, _session_intent(session, scope='nested-create', planned_budget=10))
    assert db.in_transaction, 'create_experiment committed the caller transaction'
    db.execute('ROLLBACK TO caller_create')
    db.execute('RELEASE caller_create')
    assert db.execute('SELECT 1 FROM ea_experiment WHERE id=?', (experiment,)).fetchone() is None

    # The other append-only writers have the same composability requirement.
    writers = (
        ('portfolio', lambda: store.record_portfolio(db, experiment, {'test': True}),
         'ea_earning_portfolio'),
        ('mechanism', lambda: store.record_mechanism(db, experiment, {'test': True}),
         'ea_mechanism_archive'),
        ('boundary', lambda: store.record_boundary(db, experiment, 'fixed', None, [], []),
         'ea_boundary'),
    )
    for name, write, table in writers:
        db.execute('SAVEPOINT caller_' + name)
        write()
        assert db.in_transaction, '%s writer committed the caller transaction' % name
        db.execute('ROLLBACK TO caller_' + name)
        db.execute('RELEASE caller_' + name)
        assert db.execute('SELECT 1 FROM ' + table + ' LIMIT 1').fetchone() is None

    # If a body ends its connection transaction before failing, the lost-savepoint cleanup must not
    # hide the real body exception that the optimizer needs to report.
    db.execute('SAVEPOINT caller_error')
    try:
        with store._atomic(db):
            db.commit()
            raise ValueError('primary body failure')
        raise AssertionError('the injected body failure must propagate')
    except ValueError as exc:
        assert str(exc) == 'primary body failure'
        assert any('savepoint ea_sp_' in note and 'cleanup also failed' in note
                   for note in getattr(exc, '__notes__', [])), getattr(exc, '__notes__', None)
    db.close()
    return dict(nestedWritersPreserveOuterSavepoint=True,
                missingSavepointDoesNotMaskBodyFailure=True)


def coalescing_checks(tmpdir):
    checks = {}
    db = _connect(os.path.join(tmpdir, 'coalesce.sqlite'))
    session = store.configure_session(db, _session_config(5))
    first = store.create_experiment(db, _session_intent(session, scope='co-1'))
    second = store.create_experiment(db, _session_intent(session, scope='co-2'))

    assert store.reserve(db, first, 'cand-a', (10, 11)) is True
    assert store.reserve(db, second, 'cand-a', (10, 11)) is True
    assert db.execute('SELECT COUNT(*) FROM ea_sample').fetchone()[0] == 1, 'work must coalesce'
    link = db.execute('SELECT reused, charged_experiment_id FROM ea_sample_link WHERE '
                      'experiment_id=? AND candidate_id=? AND seed_a=10 AND seed_b=11',
                      (second, 'cand-a')).fetchone()
    assert link == (1, first), link
    session_budget = db.execute('SELECT reserved, completed FROM ea_session_budget WHERE '
                                'session_id=? AND owner=? AND purpose=?',
                                (session, 'community', 'improvement')).fetchone()
    assert session_budget == (1, 0), session_budget  # charged once, by the owner

    assert store.complete(db, first, 'cand-a', (10, 11), _result((10, 11))) is True
    seen = store.outcomes(db, second)
    assert seen and seen[0]['state'] == 'completed' and seen[0]['chargedExperimentId'] == first
    assert store.recover(db)['outstandingCount'] == 0, 'a completed sample is never pending again'
    checks['completedReuseExposesResult'] = True

    # A pending coalesced sample is ONE dispatch task carrying both requesting experiments.
    assert store.reserve(db, first, 'cand-a', (20, 21)) is True
    assert store.reserve(db, second, 'cand-a', (20, 21)) is True
    recovered = store.recover(db)
    task = [entry for entry in recovered['outstanding'] if entry['seedPair'] == [20, 21]]
    assert recovered['outstandingCount'] == 1 and len(task) == 1
    assert task[0]['experimentIds'] == [first, second], task[0]['experimentIds']
    checks['oneDispatchPerSample'] = task[0]['experimentIds']
    db.close()
    return checks


def holdout_checks(tmpdir):
    checks = {}
    db = _connect(os.path.join(tmpdir, 'holdout.sqlite'))
    dev = store.create_experiment(db, _intent(scope='dev', planned_budget=10, encounterRevision='c'))
    assert store.reserve(db, dev, 'cand-a', (500, 501)) is True
    other = store.create_experiment(db, _intent(scope='hold', planned_budget=10,
                                               encounterRevision='c'))
    # A holdout pair used by ANY candidate's development sample must be refused (global).
    try:
        store.freeze_confirmation(db, other, 'nom', 'ref', {'finishPolicy': 'on-verdict'}, 'earned',
                                  [dict(candidateId='cand-b', seeds=[[500, 501]])])
        raise AssertionError('a pair used in development by another candidate must be refused')
    except ValueError:
        pass
    checks['globalDevelopmentCollision'] = True

    # A common pair between the nominee and the comparator inside the SAME plan is allowed.
    confirmation = store.freeze_confirmation(
        db, other, 'nom', 'ref', {'finishPolicy': 'on-verdict'}, 'earned',
        [dict(candidateId='nom', seeds=[[600, 601], [600, 602]]),
         dict(candidateId='comp', seeds=[[600, 601]])])
    assert confirmation > 0
    assert db.execute('SELECT COUNT(*) FROM ea_holdout').fetchone()[0] == 3

    # A DIFFERENT experiment/confirmation freezing the same pair (another candidate) is refused.
    third = store.create_experiment(db, _intent(scope='hold-2', planned_budget=10,
                                                encounterRevision='c'))
    try:
        store.freeze_confirmation(db, third, 'nom', 'ref', {'finishPolicy': 'on-verdict'}, 'earned',
                                  [dict(candidateId='comp-2', seeds=[[600, 601]])])
        raise AssertionError('a pair frozen in another confirmation must be refused globally')
    except ValueError:
        pass
    # Development later cannot use a frozen holdout pair.
    assert store.reserve(db, dev, 'cand-a', (600, 601)) is False
    assert all(entry['candidateId'] != 'nom' for entry in store.development_reservations(db, other))
    checks['crossConfirmationCollision'] = True
    db.close()
    return checks


def confirmation_checks(tmpdir):
    checks = {}
    db = _connect(os.path.join(tmpdir, 'confirmation.sqlite'))
    session = store.configure_session(db, _session_config(10))
    experiment = store.create_experiment(db, _session_intent(session, scope='conf',
                                                             planned_budget=10))
    plan = [dict(candidateId='nom', seeds=[[700, 701], [700, 702]]),
            dict(candidateId='comp', seeds=[[700, 701]])]
    confirmation = store.freeze_confirmation(db, experiment, 'nom', 'ref',
                                             {'finishPolicy': 'on-verdict'}, 'earned', plan)
    assert confirmation > 0
    reserved = db.execute('SELECT reserved FROM ea_session_budget WHERE session_id=? AND owner=? '
                          'AND purpose=?',
                          (session, 'community', 'improvement')).fetchone()[0]
    assert reserved == 3, 'the comparison budget must be charged from the session owner at freeze'
    assert store.freeze_confirmation(db, experiment, 'nom', 'ref', {'finishPolicy': 'on-verdict'},
                                     'earned', plan) == confirmation, 're-freezing is idempotent'
    assert db.execute('SELECT reserved FROM ea_session_budget WHERE session_id=? AND owner=? AND '
                      'purpose=?', (session, 'community', 'improvement')).fetchone()[0] == 3
    try:
        store.freeze_confirmation(db, experiment, 'nom', 'ref', {'finishPolicy': 'on-verdict'},
                                  'earned', [dict(candidateId='nom', seeds=[[800, 801]])])
        raise AssertionError('a different plan for the same experiment must be refused')
    except ValueError:
        pass
    checks['freezeIdempotentCharged'] = reserved

    report = store.confirmation_report(db, experiment)
    assert report['ready'] is False and report['runsPlanned'] == 3 and report['runsCompleted'] == 0
    assert 'outcomes' not in report and report['metric'] == 'earned', 'no early peek'
    assert store.record_holdout_result(db, experiment, 'nom', (700, 701),
                                       _result((700, 701))) is True
    report = store.confirmation_report(db, experiment)
    assert report['ready'] is False and report['runsCompleted'] == 1 and 'outcomes' not in report
    assert store.record_holdout_result(db, experiment, 'nom', (700, 701),
                                       _result((700, 701))) is False  # idempotent
    try:
        store.record_holdout_result(db, experiment, 'nom', (700, 701),
                                    _result((700, 701), pendingChests=99))
        raise AssertionError('a mismatched holdout result must not overwrite a completed run')
    except ValueError:
        pass
    try:
        store.record_holdout_result(db, experiment, 'nom', (700, 702), _result((700, 999)))
        raise AssertionError('result seeds must match the frozen pair')
    except ValueError:
        pass
    try:
        store.record_holdout_result(db, experiment, 'nom', (999, 999), _result((999, 999)))
        raise AssertionError('an unfrozen holdout pair must be refused')
    except ValueError:
        pass

    assert store.record_holdout_result(db, experiment, 'comp', (700, 701),
                                       _result((700, 701))) is True
    assert store.record_holdout_result(db, experiment, 'nom', (700, 702),
                                       _result((700, 702))) is True
    report = store.confirmation_report(db, experiment)
    assert report['ready'] is True and len(report['outcomes']) == 3
    assert db.execute('SELECT completed FROM ea_session_budget WHERE session_id=? AND owner=? AND '
                      'purpose=?', (session, 'community', 'improvement')).fetchone()[0] == 3
    checks['holdoutOnceNoOverwrite'] = report['runsCompleted']
    db.close()
    return checks


def outcome_intent_checks(tmpdir):
    checks = {}
    db = _connect(os.path.join(tmpdir, 'intent.sqlite'))
    policy = {'finishPolicy': 'on-verdict', 'simulator': {'skillPolicy': 'greedy'}}
    experiment = store.create_experiment(db, _intent(scope='intent', planned_budget=10,
                                                     policy=policy, mechanicsRevision='m7',
                                                     encounterRevision='c9',
                                                     measurementWindow={'from': 1}))
    assert store.reserve(db, experiment, 'cand', (1, 2)) is True
    assert store.complete(db, experiment, 'cand', (1, 2), _result((1, 2))) is True
    row = store.outcomes(db, experiment)[0]['outcome']
    assert row['finishPolicy'] == 'on-verdict', 'the stored intent policy must win over the result'
    assert row['policy'] == policy, 'the canonical outcome must carry the full declared policy'
    assert row['mechanicsRevision'] == 'm7' and row['encounterRevision'] == 'c9'
    assert row['measurementWindow'] == {'from': 1}
    assert row['status'] == 'terminal-policy-dispatch'

    assert store.reserve(db, experiment, 'cand', (3, 4)) is True
    for bad in (_result((5, 6)), dict(verdict=1, seeds=[3], rewardOutcome={}),
                dict(verdict=1, rewardOutcome={})):
        try:
            store.complete(db, experiment, 'cand', (3, 4), bad)
            raise AssertionError('a result whose seeds do not match the reserved pair must be refused')
        except ValueError:
            pass
    assert store.complete(db, experiment, 'cand', (3, 4), _result((3, 4))) is True
    try:
        store.complete(db, experiment, 'cand', (3, 4), _result((3, 4), pendingChests=42))
        raise AssertionError('a completed sample must not be overwritten')
    except ValueError:
        pass
    checks['intentDerivedOutcome'] = True
    db.close()
    return checks


def experiment_read_cache_checks(tmpdir):
    checks = {}
    db = _connect(os.path.join(tmpdir, 'experiment-cache.sqlite'))
    experiment = store.create_experiment(
        db, _intent(scope='cache', planned_budget=3, engineRevision='frozen-revision'))
    pairs = ((31, 32), (33, 34), (35, 36))
    for pair in pairs:
        assert store.reserve(db, experiment, 'cand', pair) is True

    experiment_reads = []
    db.set_trace_callback(experiment_reads.append)
    def read_count():
        return sum('select owner_share, planned_budget' in sql.lower()
                   and 'from ea_experiment where id=' in sql.lower()
                   for sql in experiment_reads)

    # Completing new, non-reused work is charged to the same experiment. The completion must reuse
    # its already-loaded immutable intent instead of selecting and decoding it a second time.
    assert store.complete(db, experiment, 'cand', pairs[0], _result(pairs[0])) is True
    assert read_count() == 1, read_count()
    reads_before_batch = read_count()
    with store.experiment_read_cache(db):
        first = store._experiment(db, experiment)
        first['engine_revision'] = 'caller-mutation'
        assert store._experiment(db, experiment)['engine_revision'] == 'frozen-revision'
        for pair in pairs[1:]:
            assert store.complete(db, experiment, 'cand', pair, _result(pair)) is True
    assert read_count() == reads_before_batch + 1, read_count()

    # Cache lifetime ends with the harvest scope; a later read observes SQLite again.
    assert store._experiment(db, experiment)['engine_revision'] == 'frozen-revision'
    assert read_count() == reads_before_batch + 2, read_count()
    checks['sameExperimentIntentReadOncePerBatch'] = read_count()
    db.set_trace_callback(None)
    db.close()
    return checks


def config_checks():
    checks = {}
    config = _session_config(7)
    assert mode.validate_config(config) is True
    snap = mode.snapshot(config)
    assert snap['budgets'] == {'community': {purpose: (7 if purpose == 'improvement' else 0)
                                             for purpose in mode.PURPOSES}}
    checks['snapshotKeepsBudgets'] = True

    # Disabled owners must be zero, and the matrix must not double-spend the declared purposes.
    community_first = mode.default_config('community-first')
    community_first['budgets'] = {'community': {purpose: 0 for purpose in mode.PURPOSES},
                                  'rebel': {purpose: 0 for purpose in mode.PURPOSES}}
    community_first['budgets']['rebel']['improvement'] = 3
    community_first['purposes'] = {purpose: 0 for purpose in mode.PURPOSES}
    community_first['purposes']['improvement'] = 3
    try:
        mode.validate_config(community_first)
        raise AssertionError('a disabled owner with a positive budget must be refused')
    except ValueError:
        pass
    double = _session_config(7)
    double['purposes']['improvement'] = 9
    try:
        mode.validate_config(double)
        raise AssertionError('a declared purpose that disagrees with the matrix must be refused')
    except ValueError:
        pass
    checks['budgetMatrixValidated'] = True

    old_shares = mode.default_shares()
    old_budgets = {'community': {purpose: (2 if purpose == 'improvement' else 0)
                                 for purpose in mode.PURPOSES},
                   'average': {purpose: (3 if purpose == 'improvement' else 0)
                               for purpose in mode.PURPOSES}}
    purposes = {purpose: (5 if purpose == 'improvement' else 0) for purpose in mode.PURPOSES}
    migration = mode.plan_migration(old_shares, 'all-strategy', old_purposes=purposes,
                                    old_budgets=old_budgets)
    assert migration['config']['budgets']['community']['improvement'] == 5
    assert migration['rollback']['budgets'] == old_budgets
    assert migration['budgetsPreserved'] is True
    checks['migrationKeepsBudgets'] = migration['config']['budgets']
    return checks


def seed_pair_index_checks():
    db = sqlite3.connect(':memory:')
    try:
        store.initialize(db)
        plans = {}
        for table, index in (('ea_sample_link', 'ea_sample_link_by_seed_pair'),
                             ('ea_holdout', 'ea_holdout_by_seed_pair')):
            plan = db.execute('EXPLAIN QUERY PLAN SELECT 1 FROM %s '
                              'WHERE seed_a=? AND seed_b=? LIMIT 1' % table, (1, 2)).fetchall()
            detail = ' '.join(str(row[3]) for row in plan)
            assert 'SEARCH' in detail and index in detail, (table, detail)
            plans[table] = detail
        return plans
    finally:
        db.close()


def main():
    tmpdir = str(Path(__file__).resolve().parent / 'tmp' / ('ea-check-%d' % os.getpid()))
    os.makedirs(tmpdir, exist_ok=True)
    try:
        report = dict(
            module='strategy_experiment_store+strategy_search_mode',
            api=dict(
                reserve='reserve(db, experiment_id, candidate_id, seed_pair) -> bool',
                complete='complete(db, experiment_id, candidate_id, seed_pair, result) -> bool',
                seed_pair='EXACTLY ONE ordered [a, b]/(a, b) of non-bool ints 0..2**31-1'),
            pairContract=pair_contract_checks(tmpdir),
            onePairBudget=one_pair_budget_checks(tmpdir),
            atomicRollback=atomic_rollback_checks(tmpdir),
            nestedSavepoints=nested_savepoint_checks(tmpdir),
            coalescing=coalescing_checks(tmpdir),
            holdout=holdout_checks(tmpdir),
            confirmation=confirmation_checks(tmpdir),
            outcomeIntent=outcome_intent_checks(tmpdir),
            experimentReadCache=experiment_read_cache_checks(tmpdir),
            seedPairIndexes=seed_pair_index_checks(),
            config=config_checks(),
            limits=['Additive ea_* schema only: initialize() must not be called on the production '
                    'library.',
                    'Budget is a reserved/completed count, not a wall-clock or optimizer budget.',
                    'Reserve/complete wrap every debit + sample/link write in one transaction or '
                    'savepoint; a crash cannot split them.',
                    'API CHANGE for Astra: the root check_encounter_contracts.py still calls '
                    'reserve(db, e, cid, [(a, b)]) with a nested list; the frozen API now takes ONE '
                    'pair, so it must call reserve(db, e, cid, [a, b]).'])
        print(json.dumps(report))
    finally:
        for name in os.listdir(tmpdir):
            try:
                os.remove(os.path.join(tmpdir, name))
            except OSError:
                pass
        try:
            os.rmdir(tmpdir)
        except OSError:
            pass
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
