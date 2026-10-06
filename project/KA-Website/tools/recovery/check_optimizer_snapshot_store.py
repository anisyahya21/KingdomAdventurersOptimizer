"""Bounded read-only SnapshotStore for async optimizer publication.

Proves, deterministically and against a real writer `Store` on a temporary library, that
`strategy_snapshot_store.SnapshotStore`:

  (a) opens the library read-only (URI `mode=ro` + `query_only=ON`) without running `Store.__init__`
      - no table or meta key changes, and it shares neither the writer's connection nor its caches;
  (b) pins one committed snapshot per `begin_snapshot()`/`end_snapshot()`: a concurrent writer commit
      is invisible inside the open snapshot and visible in the next one;
  (c) derives per-candidate run revisions from committed aggregates, so `Optimizer._finetune_rows`
      refreshes across snapshots and reuses its decoded evidence within one;
  (d) invalidates the equivalence index on candidate addition and on a real population prune, and
      retains it across snapshots while the resident ids are unchanged;
  (e) refuses writes through both the SQLite guard and the `SnapshotStore` overrides.

    python check_optimizer_snapshot_store.py
"""
import contextlib
import copy
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))

import strategy_optimizer as optimizer                                          # noqa: E402
from strategy_optimizer_adapter import (default_scenario, provenance, stats,      # noqa: E402
                                        validate_scenario)                        # noqa: E402
from strategy_snapshot_store import SnapshotStore                                # noqa: E402

FAILURES = []


def fixture_root():
    """A writable temp root: a sandboxed system temp can be read-only."""
    for candidate in (ROOT / 'TEMP' / 'ka-optimizer-20260924' / 'tmp',
                      Path(tempfile.gettempdir())):
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / '.write-probe'
            probe.write_text('ok')
            probe.unlink()
            return candidate
        except OSError:
            continue
    raise RuntimeError('no writable temporary directory')


@contextlib.contextmanager
def fixture_directory():
    """A writable workspace-temp library directory, removed even on failure.

    `tempfile.mkdtemp` directories are denied by this sandbox, so the fixture uses a plain
    `mkdir` under the workspace TEMP tree.
    """
    root = fixture_root() / f'ka-snapshot-{os.getpid()}-{time.time_ns()}'
    root.mkdir(parents=True)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def expect(label, condition, detail=''):
    print(f'  {"ok   " if condition else "FAIL "} {label}' + ('' if condition else f': {detail}'))
    if not condition:
        FAILURES.append(label)


def win_result(ordinal, prize_callbacks, awarded, seeds, digest):
    return dict(verdict=1, censored=False, ticks=40, prizeCallbacks=prize_callbacks, survivors=2,
                resourceUses=0, behavior=dict(attacks=2, heals=1, prizes=prize_callbacks),
                seeds=list(seeds), digest=digest, elapsedSeconds=.01,
                rewardOutcome=dict(awardedChests=awarded, pendingChests=awarded,
                                   awardedBasis='reward-entitlement-certificate'))


def set_raw_value(scenario, parameter_id, value):
    """A copy of `scenario` with one human unit's raw parameter raised, key form-agnostic."""
    clone = copy.deepcopy(scenario)
    parameters = next(unit for unit in clone['ownUnits'] if unit.get('human'))['parameters']
    key = parameter_id if parameter_id in parameters else str(parameter_id)
    entry = dict(parameters[key])
    entry['rawValue'] = value
    parameters[key] = entry
    return validate_scenario(clone)


def meta_rows(db):
    return [tuple(row) for row in db.execute('SELECT key,value FROM meta ORDER BY key')]


def master_rows(db):
    return [tuple(row) for row in db.execute('SELECT type,name,sql FROM sqlite_master ORDER BY name')]


def main():
    with fixture_directory() as root:
        path = Path(root) / 'snapshot.sqlite'
        writer = optimizer.Store(path, provenance())
        scenario = validate_scenario(dict(default_scenario(), tickLimit=40, encounterId=19))
        with writer.db:
            cid = writer.add(scenario, 'Snapshot fixture', 'supplied', stats(scenario))
        reader = SnapshotStore(path, provenance())
        try:
            print('contract and isolation')
            expect('reader has its own connection', reader.db is not writer.db)
            expect('reader caches are private objects',
                   all(getattr(reader, name) is not getattr(writer, name)
                       for name in ('_scenario_cache', '_elite_cache', '_next', '_run_revisions',
                                    '_selection', '_encounter_keys')))
            expect('query_only guard armed',
                   reader.db.execute('PRAGMA query_only').fetchone()[0] == 1)
            expect('compatible library', reader.compatible is True)
            expect('no pending records on a reader', reader.pending_records == 0)
            master_before = master_rows(writer.db)

            print('repeatable read across a concurrent writer commit')
            reader.begin_snapshot()
            runs_before = reader.get('totalRuns')
            rows_before = len(reader.rows(cid, 'discovery'))
            rev_before = reader.run_revision(cid)
            writer.record(cid, 'discovery', 0, win_result(0, 3, 3, (1, 2), 'w0'))
            expect('writer committed the run', writer.get('totalRuns') == runs_before + 1)
            expect('open snapshot totalRuns unchanged', reader.get('totalRuns') == runs_before)
            expect('open snapshot rows unchanged', len(reader.rows(cid, 'discovery')) == rows_before)
            expect('open snapshot revision unchanged', reader.run_revision(cid) == rev_before)
            reader.end_snapshot()

            print('a fresh snapshot sees the new committed state')
            reader.begin_snapshot()
            expect('refreshed totalRuns', reader.get('totalRuns') == runs_before + 1)
            expect('refreshed rows', len(reader.rows(cid, 'discovery')) == rows_before + 1)
            rev_after = reader.run_revision(cid)
            expect('revision advanced', rev_after > rev_before)
            live = optimizer.Optimizer.__new__(optimizer.Optimizer)
            live._candidate_cache = {}
            live._finetune_run_cache = {}
            expect('finetune rows decoded once', len(live._finetune_rows(reader, cid)) == 1)
            writer.record(cid, 'discovery', 1, win_result(1, 4, 4, (3, 4), 'w1'))
            expect('finetune cache reused inside one snapshot',
                   len(live._finetune_rows(reader, cid)) == 1)
            reader.end_snapshot()
            reader.begin_snapshot()
            expect('finetune refreshed across snapshots',
                   len(live._finetune_rows(reader, cid)) == 2)
            expect('revision advanced again', reader.run_revision(cid) > rev_after)
            reader.end_snapshot()

            print('equivalence index: retained, then invalidated by addition and prune')
            reader.begin_snapshot()
            view = reader.equivalence_view()
            index = reader._equivalence
            expect('equivalence index built', index is not None and cid in view[0])
            reader.end_snapshot()
            reader.begin_snapshot()
            expect('index retained while resident ids unchanged', reader._equivalence is index)
            reader.end_snapshot()

            with writer.db:
                second = writer.add(validate_scenario(dict(scenario, encounterId=0)),
                                    'Second', 'mutation', stats(scenario))
            reader.begin_snapshot()
            expect('addition invalidated the index', reader._equivalence is None)
            expect('rebuilt index sees the addition', second in reader.equivalence_view()[0])
            reader.end_snapshot()

            victim_scenario = validate_scenario(dict(scenario, encounterId=1))
            fillers = []
            for index in range(optimizer.MAX_CANDIDATES_PER_ENCOUNTER):
                with writer.db:
                    fillers.append(writer.add(set_raw_value(victim_scenario, 13, index + 1),
                                              f'Filler {index}', 'mutation', stats(scenario)))
            with writer.db:
                writer.add(set_raw_value(victim_scenario, 13, 10_000), 'Filler cap', 'mutation',
                           stats(scenario))
            pruned = [candidate for candidate in fillers if not writer.db.execute(
                'SELECT 1 FROM candidate WHERE id=?', (candidate,)).fetchone()]
            expect('writer prune removed a filler', len(pruned) == 1, f'pruned {len(pruned)}')
            reader.begin_snapshot()
            expect('prune invalidated the index', reader._equivalence is None)
            resident = {row[0] for row in reader.db.execute('SELECT id FROM candidate')}
            expect('pruned build gone from the snapshot', all(c not in resident for c in pruned))
            expect('pruned build dropped from revisions',
                   all(reader.run_revision(c) == 0 for c in pruned))
            expect('rebuilt index excludes the pruned build',
                   all(c not in reader.equivalence_view()[0] for c in pruned))
            reader.end_snapshot()

            print('writes blocked, metadata untouched')
            meta_before = meta_rows(writer.db)
            reader.begin_snapshot()
            blocked = []
            for label, statement in (('insert', "INSERT INTO meta VALUES ('snapshot-probe','1')"),
                                     ('update', "UPDATE meta SET value='1' WHERE key='proposals'"),
                                     ('delete', "DELETE FROM run")):
                try:
                    reader.db.execute(statement)
                except sqlite3.DatabaseError:
                    blocked.append(label)
            expect('sqlite write guard blocked insert/update/delete', len(blocked) == 3, str(blocked))
            for name, call in (('set', lambda: reader.set('snapshot-probe', 1)),
                               ('record', lambda: reader.record(cid, 'discovery', 99, {}))):
                try:
                    call()
                except RuntimeError:
                    blocked.append(name)
            expect('SnapshotStore overrides refuse writes',
                   all(name in blocked for name in ('set', 'record')), str(blocked))
            reader.end_snapshot()
            expect('no meta key changed by the reader', meta_rows(writer.db) == meta_before)
            expect('no table created by the reader', master_rows(writer.db) == master_before)

            print('a minimal library is not migrated on open')
            minimal_path = Path(root) / 'minimal.sqlite'
            raw = sqlite3.connect(minimal_path)
            raw.executescript(
                'CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);'
                'CREATE TABLE candidate(id TEXT PRIMARY KEY, scenario TEXT NOT NULL,'
                ' label TEXT NOT NULL, source TEXT NOT NULL, stats TEXT NOT NULL,'
                ' created INTEGER NOT NULL);'
                'CREATE TABLE run(candidate TEXT NOT NULL, phase TEXT NOT NULL,'
                ' ordinal INTEGER NOT NULL, result TEXT NOT NULL,'
                ' PRIMARY KEY(candidate,phase,ordinal));')
            raw.execute('INSERT INTO meta VALUES (?,?)', ('provenance', optimizer.canonical(provenance())))
            raw.commit()
            minimal_master, minimal_meta = master_rows(raw), meta_rows(raw)
            raw.close()
            minimal = SnapshotStore(minimal_path, provenance())
            minimal.begin_snapshot()
            minimal.get('totalRuns')
            minimal.candidates()
            minimal.rows('absent', 'discovery')
            minimal.next_ordinal('absent', 'discovery')
            expect('absent candidate has no revision', minimal.run_revision('absent') == 0)
            minimal.end_snapshot()
            minimal.close()
            check = sqlite3.connect(minimal_path)
            expect('no table added to a minimal library', master_rows(check) == minimal_master)
            expect('no meta key added to a minimal library', meta_rows(check) == minimal_meta)
            check.close()
            expect('reader activity left no probe key behind',
                   not any(row[0] == 'snapshot-probe' for row in meta_rows(writer.db)))
        finally:
            reader.close()
            writer.close()

    if FAILURES:
        print(f'FAILURES ({len(FAILURES)}): ' + ', '.join(FAILURES[:6]))
        return 1
    print('  every snapshot-store invariant held')
    return 0


if __name__ == '__main__':
    sys.exit(main())
