"""Regression and local throughput check for large compatible outcome caches.

Run with the workspace Python: ``python -B tools/recovery/check_encounter_outcomes_cache_capacity.py``.
The fixture is an in-memory SQLite ledger; it never opens or changes the optimizer's live library.
"""
from __future__ import annotations

import json
import statistics
import sqlite3
import time

import strategy_encounter_search as search
import strategy_experiment_store as ledger


def make_db(count):
    db = sqlite3.connect(':memory:')
    db.executescript('''
        CREATE TABLE ea_experiment_budget(
            experiment_id INTEGER PRIMARY KEY, reserved INTEGER, completed INTEGER);
        CREATE TABLE ea_sample(
            sample_key TEXT PRIMARY KEY, outcome TEXT, result TEXT);
        CREATE TABLE ea_sample_link(
            experiment_id INTEGER, candidate_id TEXT, seed_a INTEGER, seed_b INTEGER,
            sample_key TEXT, charged_experiment_id INTEGER,
            PRIMARY KEY(experiment_id, candidate_id, seed_a, seed_b));
        CREATE INDEX ea_sample_link_by_sample ON ea_sample_link(sample_key, experiment_id);
    ''')
    budgets = [(experiment_id, 1, 1) for experiment_id in range(1, count + 1)]
    samples = []
    links = []
    for experiment_id in range(1, count + 1):
        # Eight rows per experiment approximates the mature library's link/experiment ratio.
        # The first two experiments intentionally share one observation for dedupe coverage.
        candidate_id = ('shared-candidate' if experiment_id <= 2 else
                        'candidate-%05d' % experiment_id)
        for ordinal in range(8):
            shared = experiment_id == 2 and ordinal == 0
            sample_id = 1 if shared else ((experiment_id - 1) * 8 + ordinal + 1)
            if not shared:
                samples.append(('sample-%05d' % sample_id,
                                json.dumps({'earned': sample_id}), '{}'))
            links.append((experiment_id, candidate_id, ordinal * 2, ordinal * 2 + 1,
                          'sample-%05d' % sample_id, sample_id))
    db.executemany('INSERT INTO ea_experiment_budget VALUES(?,?,?)', budgets)
    db.executemany('INSERT INTO ea_sample VALUES(?,?,?)', samples)
    db.executemany('INSERT INTO ea_sample_link VALUES(?,?,?,?,?,?)', links)
    db.commit()
    return db


def reference_deduped(db, experiments, cache):
    """The former 512-entry FIFO implementation, retained here only as a benchmark reference."""
    source = []
    for experiment_id in sorted(experiments):
        counts = db.execute('SELECT reserved,completed FROM ea_experiment_budget '
                            'WHERE experiment_id=?', (experiment_id,)).fetchone()
        token = tuple(counts) if counts is not None else None
        cached = cache.get(experiment_id)
        if cached is None or cached[0] != token:
            cached = (token, ledger.outcomes(db, experiment_id))
            if len(cache) >= 512:
                cache.pop(next(iter(cache)))
            cache[experiment_id] = cached
        source.extend(cached[1])
    return aggregate(source, experiments)


def aggregate(source, experiments):
    by_candidate = {}
    seen = set()
    for row in source:
        if row['state'] != 'completed' or row['experimentId'] not in experiments:
            continue
        identity = (row['candidateId'],
                    row.get('sampleKey') or tuple(row.get('seedPair') or ()))
        if identity in seen:
            continue
        seen.add(identity)
        by_candidate.setdefault(row['candidateId'], []).append(row['outcome'])
    return by_candidate


def median_seconds(call, repeats=5):
    samples = []
    for _ in range(repeats):
        started = time.perf_counter()
        call()
        samples.append(time.perf_counter() - started)
    return statistics.median(samples)


def run():
    count = 2283
    db = make_db(count)
    coordinator = object.__new__(search.Coordinator)
    coordinator._outcomes_cache = {}
    ids = set(range(1, count + 1))
    expected = search.Coordinator._deduped_outcomes(coordinator, db, ids)
    assert len(expected) == count - 1, len(expected)
    assert len(expected['shared-candidate']) == 15
    assert len({json.dumps(item, sort_keys=True) for item in expected['shared-candidate']}) == 15
    assert len(coordinator._outcomes_cache) == count

    # A repeat must use every cached outcome while reading freshness in only three bind-safe batches.
    decoded = []
    original_outcomes = ledger.outcomes

    def counted_outcomes(db_arg, experiment_id=None):
        decoded.append(experiment_id)
        return original_outcomes(db_arg, experiment_id)

    ledger.outcomes = counted_outcomes
    traces = []
    db.set_trace_callback(traces.append)
    try:
        repeated = search.Coordinator._deduped_outcomes(coordinator, db, ids)
        assert repeated == expected
        assert decoded == [], decoded
        budget_queries = [sql for sql in traces
                          if 'SELECT experiment_id,reserved,completed' in sql]
        assert len(budget_queries) == 3, len(budget_queries)

        # A local budget change invalidates exactly its experiment's cached outcomes.
        db.execute('UPDATE ea_experiment_budget SET completed=0 WHERE experiment_id=77')
        db.commit()
        search.Coordinator._deduped_outcomes(coordinator, db, ids)
        assert decoded == [77], decoded

        # Switching to another compatible subset prunes all inactive IDs from retained memory.
        subset = set(range(1500, 1600))
        search.Coordinator._deduped_outcomes(coordinator, db, subset)
        assert set(coordinator._outcomes_cache) == subset
    finally:
        ledger.outcomes = original_outcomes
        db.set_trace_callback(None)

    timings = {}
    for size in (629, 2283):
        active = set(range(1, size + 1))
        old_cache = {}
        # Warm once, then time steady repeat passes; FIFO churns on every pass above 512 entries.
        reference_deduped(db, active, old_cache)
        new_cache = {}
        current = object.__new__(search.Coordinator)
        current._outcomes_cache = new_cache
        search.Coordinator._deduped_outcomes(current, db, active)
        before = median_seconds(lambda: reference_deduped(db, active, old_cache))
        after = median_seconds(lambda: search.Coordinator._deduped_outcomes(current, db, active))
        timings[size] = (before, after, len(new_cache))
    db.close()
    print('PASS: 2,283 compatible outcomes stay warm; shared samples dedupe; one changed budget '
          'refreshes only that experiment; changing the compatible set prunes inactive entries')
    for size, (before, after, cached) in timings.items():
        print('BENCH %s experiments: FIFO %.3f ms, scoped-cache/batched-counts %.3f ms '
              '(%.1fx faster; %s cached)' %
              (size, before * 1000, after * 1000, before / after if after else float('inf'), cached))


if __name__ == '__main__':
    run()
