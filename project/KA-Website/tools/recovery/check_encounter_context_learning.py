"""Zero-battle regression checks for owner-contextual learning and seed freshness.

Run:  <venv>/python -B -X utf8 tools/recovery/check_encounter_context_learning.py

Covers the four integrated repairs: bounded compatible historical priors, per-owner contextual
advisers, incumbent/reference retention in earning selection, and the global legacy+EA seed
freshness collector wired into `_fresh_pairs`. Every fixture is synthetic and deterministic; no
real battle, native engine, live library, setting or reset is touched. The battle pool is never
used here (the coordinator is driven only through pure bookkeeping).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import check_encounter_search as base                                  # noqa: E402
import strategy_encounter_search as search                             # noqa: E402
import strategy_legacy_observations as legacy                          # noqa: E402
import strategy_seed_freshness as freshness                            # noqa: E402
import strategy_students as students                                   # noqa: E402
from strategy_optimizer import Store                                   # noqa: E402
from strategy_optimizer_adapter import provenance                      # noqa: E402

RESULTS = []


def check(name, condition, detail=''):
    RESULTS.append((name, bool(condition)))
    print(('PASS ' if condition else 'FAIL ') + name + ((' :: ' + str(detail)) if detail else ''))


def coordinator(path, purposes, *, reference=None, encounter=None, maximum=6):
    store = Store(path, provenance())
    coord = search.Coordinator(path=path, revision='check', workers=1, telemetry=True,
                               maximum=maximum, reference=reference, encounter=encounter,
                               timeout=5.0)
    config = search.default_config('community-first', purposes=purposes)
    mapping = dict(enabled=True, mode='community-first', config=config, previous={})
    if encounter is not None:
        mapping['encounter'] = encounter
    if reference is not None:
        mapping['reference'] = reference
    store.set(search.MODE_KEY, mapping)
    coord.load(store)
    return store, coord


# ------------------------------------------------------------------ item 1: historical priors
def prior_checks():
    path = base.new_dir('ctx-prior') / 'lib.sqlite'
    encounter_id, supplied_id = base.prep_library(path)
    store, coord = coordinator(path, {'improvement': 8, 'comparison': 8, 'boundary': 0,
                                      'support': 0, 'exploration': 0},
                               reference=supplied_id, encounter=encounter_id)
    coord.state['policy'] = {'finishPolicy': 'on-verdict'}
    coord.state['features'] = {'cand-a': {'dps_atk': 1.0}, 'cand-b': {'dps_atk': 2.0}}
    coord.state['history'] = [
        dict(candidateId='cand-a', owner='community', referenceId=supplied_id),
        dict(candidateId='cand-b', owner='stumble', referenceId=supplied_id),
    ]
    calls = []

    def fake_load(store_arg, candidate_ids, **kwargs):
        calls.append((list(candidate_ids), dict(kwargs)))
        return dict(compatibility={'compatible': True, 'reason': None},
                    observations=[dict(candidateId='cand-a', window='development',
                                       compatibility='legacy-key', meanEarned=7.0, resolved=8,
                                       total=8, features={'dps_atk': 1.0}, adviserEligible=True)])

    original = legacy.load_observations
    legacy.load_observations = fake_load
    try:
        entries = coord._legacy_observations(store.db, 'community')
        again = coord._legacy_observations(store.db, 'community')
        check('context-prior-admitted-and-stamped',
              len(entries) == 1 and entries[0]['candidateId'] == 'cand-a'
              and entries[0]['evidenceClass'] == 'historical-prior'
              and entries[0]['independentConfirmation'] is False
              and entries[0]['confirmationEligible'] is False
              and entries[0]['compatibility'] == coord._compatibility(), entries)
        check('context-prior-bounded-and-basis-kwargs',
              calls and calls[0][1].get('maximum_candidates') == 24
              and calls[0][1].get('rows_per_candidate') == 256
              and calls[0][1].get('encounter') == encounter_id
              and calls[0][1].get('policy') == {'finishPolicy': 'on-verdict'}
              and calls[0][1].get('mechanics_revision') == coord._mechanics_revision()
              and isinstance(calls[0][1].get('engine_revision'), dict)
              and calls[0][1]['engine_revision'].get('digest'),
              calls[0][1] if calls else None)
        check('context-prior-cached-per-context', len(calls) == 1 and again == entries, len(calls))
        check('context-prior-selection-is-legacy-library-not-ea-history',
              calls[0][0] == [str(supplied_id)], calls[0][0])
        # A second, incompatible basis is refused and reported, never admitted.
        coord._legacy_cache.clear()
        legacy.load_observations = lambda *a, **k: dict(
            compatibility={'compatible': False, 'reason': 'engine-revision-mismatch'},
            observations=[dict(candidateId='cand-a')])
        refused = coord._legacy_observations(store.db, 'community')
        check('context-prior-incompatible-refused', refused == []
              and any('historical priors refused' in note for note in coord.limitations),
              coord.limitations[-1:] if coord.limitations else None)
    finally:
        legacy.load_observations = original
        store.close()


def fresh_beats_prior_checks():
    path = base.new_dir('ctx-fresh') / 'lib.sqlite'
    encounter_id, supplied_id = base.prep_library(path)
    store, coord = coordinator(path, {'improvement': 8, 'comparison': 8, 'boundary': 0,
                                      'support': 0, 'exploration': 0},
                               reference=supplied_id, encounter=encounter_id)
    coord.state['policy'] = {'finishPolicy': 'on-verdict'}
    coord.state['history'] = [dict(candidateId='cand-a', owner='community')]
    coord._observations = lambda db, owner=None: [dict(
        candidateId='cand-a', window='development', resolved=4, total=4, meanEarned=3.0,
        features={'dps_atk': 1.0}, compatibility=coord._compatibility())]
    coord._legacy_observations = lambda db, owner: [dict(
        candidateId='cand-a', window='development', resolved=99, total=99, meanEarned=500.0,
        features={'dps_atk': 1.0}, compatibility=coord._compatibility(),
        evidenceClass='historical-prior', confirmationEligible=False)]
    seen = {}
    original = search.adviser.fit
    search.adviser.fit = lambda observations, **kwargs: (seen.setdefault(
        'rows', [(o['candidateId'], o['evidenceClass'] if o.get('evidenceClass') else 'development',
                  o['meanEarned']) for o in observations]), {'status': 'coverage'})[1]
    try:
        coord._fit_adviser(store.db, 'community')
    finally:
        search.adviser.fit = original
        store.close()
    rows = seen.get('rows') or []
    check('context-fresh-development-beats-duplicate-prior',
          [r for r in rows if r[0] == 'cand-a'] == [('cand-a', 'development', 3.0)], rows)


# ------------------------------------------------------------------ item 1: per-owner advisers
def per_owner_adviser_checks():
    path = base.new_dir('ctx-owner') / 'lib.sqlite'
    encounter_id, supplied_id = base.prep_library(path)
    store, coord = coordinator(path, {'improvement': 8, 'comparison': 8, 'boundary': 0,
                                      'support': 0, 'exploration': 0},
                               reference=supplied_id, encounter=encounter_id)
    model_a = {'status': 'fitted', 'tag': 'a'}
    model_b = {'status': 'fitted', 'tag': 'b'}
    coord.state['advisers'] = {'owner-a': model_a, 'owner-b': model_b}
    used = []
    original = search.adviser.rank
    search.adviser.rank = lambda model, pool, **kwargs: (used.append(model.get('tag')), pool)[1]
    try:
        coord._rank([dict(id='x')], 'owner-a')
        coord._rank([dict(id='x')], 'owner-b')
    finally:
        search.adviser.rank = original
        store.close()
    check('context-per-owner-adviser-isolated', used == ['a', 'b'], used)


# ------------------------------------------------------------------ item 2: incumbent retention
def _incumbent_fixture(purposes):
    path = base.new_dir('ctx-incumbent') / 'lib.sqlite'
    encounter_id, supplied_id = base.prep_library(path)
    store, coord = coordinator(path, purposes, reference=supplied_id, encounter=encounter_id)
    coord._ensure_schema(store.db)
    coord._compatible_experiment_ids = lambda db: {'exp'}
    strong = [dict(finalEarned=100.0, resolved=True, verdict=1) for _ in range(6)]
    coord._deduped_outcomes = lambda db, experiments=None: {str(supplied_id): strong}
    coord.state['portfolio'] = [
        dict(candidateId='child-1', meanEarned=5.0, resolved=8, eligible=True, features={'x': 1.0}),
        dict(candidateId='child-2', meanEarned=1.0, resolved=8, eligible=True, features={'x': 2.0}),
    ]
    return store, coord, supplied_id


def incumbent_checks():
    store, coord, supplied_id = _incumbent_fixture({'improvement': 0, 'comparison': 8,
                                                    'boundary': 0, 'support': 0, 'exploration': 0})
    try:
        incumbent = coord._incumbent_evidence(store.db)
        check('context-incumbent-evidence-canonical',
              incumbent is not None and incumbent['candidateId'] == str(supplied_id)
              and incumbent['arm'] == 'reference' and incumbent['incumbent'] is True
              and incumbent['meanEarned'] == 100.0 and incumbent['resolved'] == 6
              and incumbent['evidenceClass'] == 'development-reference'
              and incumbent['independentConfirmation'] is False
              and incumbent['confirmationEligible'] is False
              and incumbent['owner'] == 'community', incumbent)
        coord._maybe_freeze_confirmation(store.db, store)
        check('context-strong-incumbent-blocks-weak-children-nomination',
              coord.state.get('confirmation') is None
              and any('retained incumbent/reference remains the best' in note
                      for note in coord.limitations), coord.state.get('confirmation'))
        holdout = store.db.execute('SELECT COUNT(*) FROM ea_holdout').fetchone()[0]
        check('context-incumbent-not-self-confirmed', holdout == 0, holdout)
        coord._refresh_portfolio(store.db)
        public = coord.report()['portfolio']
        strongest = max(public, key=lambda row: row.get('meanEarned') or 0)
        check('context-public-portfolio-retains-strong-incumbent',
              strongest['candidateId'] == str(supplied_id) and strongest['meanEarned'] == 100.0,
              [(row['candidateId'], row.get('meanEarned')) for row in public])
    finally:
        store.close()


# ------------------------------------------------------------------ item 4: seed freshness
def seed_freshness_checks():
    path = base.new_dir('ctx-seeds') / 'lib.sqlite'
    encounter_id, supplied_id = base.prep_library(path)
    store, coord = coordinator(path, {'improvement': 8, 'comparison': 8, 'boundary': 0,
                                      'support': 0, 'exploration': 0},
                               reference=supplied_id, encounter=encounter_id)
    coord._ensure_schema(store.db)
    try:
        # A collision recorded for an ELIMINATED candidate (not the nominee/reference) still
        # forbids that pair: the collector covers the whole history, not just the frozen pair.
        import strategy_evidence
        strategy_evidence.store_schema(store.db)
        store.db.execute('INSERT OR IGNORE INTO evidence(candidate,phase,ordinal,seeds) '
                         'VALUES(?,?,?,?)', ('eliminated-candidate', 'validation', 7001,
                                             json.dumps([123456, 654321])))
        store.db.commit()
        reserved = coord._seed_freshness()
        check('context-seed-freshness-reads-eliminated-candidate-collision',
              reserved is not None and (123456, 654321) in reserved,
              None if reserved is None else len(reserved))
        drawn = coord._fresh_pairs(store, [str(supplied_id), 'x'], 200)
        check('context-fresh-pairs-never-redraw-eliminated-collision',
              len(drawn) == 200 and (123456, 654321) not in drawn, len(drawn))
        # A read failure must fail closed: no draws, and the reason is surfaced.
        original = freshness.collect
        freshness.collect = lambda *a, **k: (_ for _ in ()).throw(freshness.Blocked('boom'))
        coord._freshness = None
        coord._freshness_key = None
        refused = coord._fresh_pairs(store, [str(supplied_id), 'x'], 8)
        check('context-seed-freshness-read-failure-fails-closed',
              refused == [] and any('seed freshness failed closed' in note
                                    for note in coord.limitations), refused)
        freshness.collect = original
    finally:
        try:
            freshness.collect = original  # noqa: F821 - restored on the happy path too
        except NameError:
            pass
        store.close()


def _insert_validation_rows(db, cid, rows):
    for ordinal, seeds, chests in rows:
        db.execute(
            'INSERT OR REPLACE INTO evidence(candidate,phase,ordinal,seeds,verdict,censored,'
            'prizeCallbacks,awardedChests,awardedBasis,pendingChests) VALUES(?,?,?,?,?,?,?,?,?,?)',
            (cid, 'validation', ordinal, json.dumps(seeds), 1, 0, chests, chests,
             'reward-entitlement-certificate', chests))


def legacy_library_prior_checks():
    """A migrated mature library with EMPTY EA history must yield non-empty priors.

    The reader is the REAL shared reader: this fixture builds a real optimiser library (candidate,
    candidate_meta, lineage, evidence) and never mocks `load_observations`. It proves the priors come
    from the legacy candidate/meta/lineage tables, that a Stumble child is excluded while a Discovery
    child is not vetoed, that the original lineage stays the attribution while the budget owner is the
    requester, and that the priors actually reach the adviser model.
    """
    path = base.new_dir('ctx-legacy') / 'lib.sqlite'
    encounter_id, supplied_id = base.prep_library(path)
    store = Store(path, provenance())
    baseline = store.scenario(supplied_id)
    family_of = {}
    try:
        import copy
        from strategy_optimizer_adapter import stats as adapter_stats
        with store.db:
            for lane, atk in (('stumble', 91), ('community', 92), ('discovery', 93)):
                child = copy.deepcopy(baseline)
                # A real build difference (identity ignores mathSeed/libSeed): raise the lead unit's
                # ATK so each child is distinct and shareable.
                param = child['ownUnits'][0]['parameters']['13']
                param['rawValue'] = int(param['rawValue']) + atk
                child_id, _existed = store.add_child(
                    child, 'legacy %s' % lane, adapter_stats(child), supplied_id,
                    'legacy-fixture', lane, 'legacy fixture', lane, atk)
                family_of[lane] = child_id
                _insert_validation_rows(store.db, child_id,
                                        [(i, [1000 + i, 2000 + atk + i], 5) for i in range(4)])
        store.db.commit()
    finally:
        store.close()

    store, coord = coordinator(path, {'improvement': 8, 'comparison': 8, 'boundary': 0,
                                      'support': 0, 'exploration': 0},
                               reference=supplied_id, encounter=encounter_id)
    coord.state['policy'] = {'finishPolicy': 'on-verdict'}
    coord.state['history'] = []  # migrated mature library with EMPTY EA state
    coord._ensure_schema(store.db)
    try:
        entries = coord._legacy_observations(store.db, 'community')
        ids = {entry['candidateId'] for entry in entries}
        check('context-legacy-empty-ea-loads-real-priors', bool(entries), ids)
        check('context-legacy-selection-excludes-stumble-family',
              family_of['stumble'] not in ids, sorted(ids))
        check('context-legacy-selection-keeps-discovery-family',
              family_of['discovery'] in ids and family_of['community'] in ids, sorted(ids))
        check('context-legacy-prior-keeps-original-attribution',
              all(entry.get('evidenceFamily') in ('community', 'discovery', 'origin')
                  for entry in entries)
              and any(entry.get('evidenceFamily') == 'discovery' for entry in entries)
              and all(entry.get('budgetOwner') == 'community' for entry in entries),
              [(entry['candidateId'], entry.get('evidenceFamily'), entry.get('budgetOwner'))
               for entry in entries])
        seen = {}
        original = search.adviser.fit
        search.adviser.fit = lambda observations, **kwargs: (seen.setdefault(
            'priors', [o['candidateId'] for o in observations
                       if o.get('evidenceClass') == 'historical-prior']),
            {'status': 'coverage'})[1]
        try:
            coord._fit_adviser(store.db, 'community')
        finally:
            search.adviser.fit = original
        check('context-legacy-priors-influence-adviser-model',
              family_of['community'] in (seen.get('priors') or [])
              and family_of['discovery'] in (seen.get('priors') or [])
              and family_of['stumble'] not in (seen.get('priors') or []),
              seen.get('priors'))
    finally:
        store.close()


def main():
    prior_checks()
    fresh_beats_prior_checks()
    per_owner_adviser_checks()
    incumbent_checks()
    seed_freshness_checks()
    legacy_library_prior_checks()
    passed = sum(1 for _name, ok in RESULTS if ok)
    print('%d/%d context-learning checks passed' % (passed, len(RESULTS)))
    return 0 if passed == len(RESULTS) else 1


if __name__ == '__main__':
    sys.exit(main())
