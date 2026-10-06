"""Verify campaign encounters reuse one historical seed scan per library version."""

from pathlib import Path
from types import SimpleNamespace
import sqlite3
import tempfile

import strategy_confirmation
import strategy_seed_freshness
from strategy_encounter_search import Coordinator


def main():
    calls = []
    original_open = strategy_confirmation.open_library_readonly
    original_collect = strategy_seed_freshness.collect

    class ReadConnection:
        def close(self):
            pass

    def collect(_connection, **_kwargs):
        calls.append(1)
        return dict(pairs={(101, 102)}, cacheKey='test', counts={}, provenance={})

    def make_coordinator(host, session):
        coord = Coordinator.__new__(Coordinator)
        coord.revision = 'battle-revision'
        coord.path = Path('campaign.sqlite')
        coord.session_id = session
        coord.planning_host = host
        coord._freshness_key = None
        coord._freshness = None
        coord._freshness_report = None
        coord._freshness_blocked = None
        coord._mechanics_revision = lambda: 'mechanics-revision'
        coord._live_library_version = lambda store: store.version
        coord._note = lambda message: None
        return coord

    try:
        strategy_confirmation.open_library_readonly = lambda path: ReadConnection()
        strategy_seed_freshness.collect = collect
        host = SimpleNamespace()
        store = SimpleNamespace(db=object(), version=1)
        first = make_coordinator(host, 10)
        second = make_coordinator(host, 11)
        assert first._seed_freshness(store) == {(101, 102)}
        assert second._seed_freshness(store) == {(101, 102)}
        assert len(calls) == 1, 'second encounter rescanned unchanged history'
        store.version = 2
        assert second._seed_freshness(store) == {(101, 102)}
        assert len(calls) == 2, 'external library change failed to invalidate snapshot'
        store.db = object()
        assert first._seed_freshness(store) == {(101, 102)}
        assert len(calls) == 3, 'new writer connection reused stale snapshot'

        db = sqlite3.connect(':memory:')
        db.execute('CREATE TABLE ea_sample_link(seed_a INTEGER, seed_b INTEGER)')
        db.execute('CREATE TABLE ea_holdout(seed_a INTEGER, seed_b INTEGER)')
        seen = []
        original_draw = strategy_confirmation.draw_seed_pairs
        strategy_confirmation.draw_seed_pairs = lambda count, reserved, **kwargs: (
            seen.append(set(reserved)) or [(201, 202)], 0)
        try:
            base = {(101, 102)}
            host = SimpleNamespace()
            coord = SimpleNamespace(planning_host=host, _freshness=base, encounter=1,
                                    _seed_freshness=lambda store: base)
            store = SimpleNamespace(db=db)
            Coordinator._fresh_pairs(coord, store, ['candidate'], 1,
                                     reserved_extra=[(301, 302)])
            assert (301, 302) in seen[-1]
            assert (301, 302) not in host._live_seed_pairs_snapshot['newPairs']
            db.execute('INSERT INTO ea_sample_link VALUES(401,402)')
            db.execute('INSERT INTO ea_holdout VALUES(501,502)')
            Coordinator._fresh_pairs(coord, store, ['candidate'], 1)
            assert {(101, 102), (401, 402), (501, 502)} <= seen[-1]
            assert (301, 302) not in seen[-1]
            assert host._live_seed_pairs_snapshot['linkRowid'] == 1
            assert host._live_seed_pairs_snapshot['holdoutRowid'] == 1
        finally:
            strategy_confirmation.draw_seed_pairs = original_draw
            db.close()
    finally:
        strategy_confirmation.open_library_readonly = original_open
        strategy_seed_freshness.collect = original_collect
    real_sqlite_version_checks()
    print('PASS shared campaign seed snapshot and version/connection invalidation')


def real_sqlite_version_checks():
    """Worker writes on the host connection reuse the snapshot; peer writes invalidate it."""
    original_open = strategy_confirmation.open_library_readonly
    original_collect = strategy_seed_freshness.collect
    calls = []

    with tempfile.TemporaryDirectory(prefix='ka-seed-version-') as directory:
        path = Path(directory) / 'seed-cache.sqlite'
        writer = sqlite3.connect(path, timeout=5)
        writer.executescript(
            'CREATE TABLE seed_history(seed_a INTEGER, seed_b INTEGER);'
            'CREATE TABLE ea_sample(seed_a INTEGER, seed_b INTEGER);'
            'CREATE TABLE ea_sample_link(seed_a INTEGER, seed_b INTEGER);'
            'CREATE TABLE ea_holdout(seed_a INTEGER, seed_b INTEGER);'
            'INSERT INTO seed_history VALUES(101,102);')
        writer.commit()
        external = sqlite3.connect(path, timeout=5)
        host = SimpleNamespace()
        store = SimpleNamespace(db=writer)

        def make_real_coordinator(session):
            coord = Coordinator.__new__(Coordinator)
            coord.revision = 'battle-revision'
            coord.path = path
            coord.session_id = session
            coord.encounter = 14
            coord.planning_host = host
            coord._freshness_key = None
            coord._freshness = None
            coord._freshness_report = None
            coord._freshness_blocked = None
            coord._mechanics_revision = lambda: 'mechanics-revision'
            coord._live_library_version = lambda value: Coordinator._live_library_version(
                coord, value)
            coord._note = lambda message: None
            return coord

        def open_readonly(_path):
            return sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)

        def collect(connection, **_kwargs):
            calls.append(1)
            pairs = set()
            for table in ('seed_history', 'ea_sample', 'ea_sample_link', 'ea_holdout'):
                pairs.update(tuple(row) for row in connection.execute(
                    f'SELECT seed_a,seed_b FROM {table}'))
            return dict(pairs=pairs, cacheKey='real-sqlite', counts={}, provenance={})

        coord_a = make_real_coordinator(10)
        coord_b = make_real_coordinator(11)
        original_draw = strategy_confirmation.draw_seed_pairs
        seen = []

        def capture_draw(_count, reserved, **_kwargs):
            seen.append(set(reserved))
            return [(901, 902)], 0

        try:
            strategy_confirmation.open_library_readonly = open_readonly
            strategy_seed_freshness.collect = collect
            strategy_confirmation.draw_seed_pairs = capture_draw

            initial_version = coord_a._live_library_version(store)
            assert coord_a._seed_freshness(store) == {(101, 102)}
            Coordinator._fresh_pairs(coord_a, store, ['candidate'], 1)
            assert calls == [1]

            # This is the evaluator's persistence model: results and reservations are written and
            # committed through the optimiser's single long-lived writer connection.
            writer.execute('INSERT INTO ea_sample_link VALUES(201,202)')
            writer.execute('INSERT INTO ea_holdout VALUES(301,302)')
            writer.commit()
            assert coord_a._live_library_version(store) == initial_version
            assert coord_b._seed_freshness(store) == {(101, 102)}
            assert len(calls) == 1, 'same-connection battle commits invalidated shared history'
            Coordinator._fresh_pairs(coord_b, store, ['candidate'], 1)
            assert {(201, 202), (301, 302)} <= seen[-1]

            # A peer connection can update legacy global history. Its commit must invalidate the
            # host snapshot, and the next collection must include the newly recorded pair.
            external.execute('INSERT INTO seed_history VALUES(401,402)')
            external.commit()
            assert coord_a._live_library_version(store) != initial_version
            refreshed = coord_b._seed_freshness(store)
            assert (401, 402) in refreshed
            assert len(calls) == 2, 'external write failed to trigger exactly one full refresh'
            assert coord_a._seed_freshness(store) == refreshed
            assert len(calls) == 2, 'other campaign sessions did not reuse refreshed snapshot'
        finally:
            strategy_confirmation.open_library_readonly = original_open
            strategy_seed_freshness.collect = original_collect
            strategy_confirmation.draw_seed_pairs = original_draw
            external.close()
            writer.close()


if __name__ == '__main__':
    main()
