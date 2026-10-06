"""Bounded read-only snapshot view of a strategy optimiser library.

The desktop publisher used to read the coordinator's own `Store` on the scheduling thread.
It flushed pending writes first, but dispatch had to wait for the view to finish. This module adds a
separate reader: it opens the same library file through a read-only SQLite URI, never runs
`Store.__init__` (no schema creation, no migration, no meta write), and pins one committed snapshot
per publication.

Contract:

    SnapshotStore(path, provenance)   # existing library, read-only URI, PRAGMA query_only=ON
    begin_snapshot()                  # one deferred read transaction + fresh per-snapshot caches
    end_snapshot()                    # roll back the read transaction
    close()                           # close the connection; never the writer's flush/checkpoint

Every read between `begin_snapshot` and `end_snapshot` sees one committed snapshot, so a publication
is internally consistent even while the writer keeps committing new runs. The reader owns its own
connection and its own caches - it never touches the writer's. `run_revision` is derived from the
committed aggregate counters (with the highest resident ordinal as a fallback), so
`Optimizer._finetune_rows` refreshes its decoded evidence exactly when a snapshot actually saw new
runs and reuses it otherwise.

The equivalence index is retained across snapshots while the resident candidate ids are unchanged:
a stored scenario is immutable under its content-hash id, so an unchanged id set means an unchanged
index. An addition or a prune invalidates it for a lazy rebuild inside the next snapshot.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from strategy_optimizer import Store, same_simulator


class SnapshotStore(Store):
    """Read-only, transaction-pinned view of an existing optimiser library.

    Deliberately does not call `Store.__init__`: the schema, the learner tables, the evidence
    backfill and the provenance migration all write, and a reader must do none of them. Only the
    read half of the `Store` surface is inherited; every state attribute that surface reads is set
    here from scratch.
    """

    def __init__(self, path, provenance):
        self.path = Path(path)
        # `mode=ro` is the primary write guard; `query_only=ON` is the second, so even the
        # inherited write statements that a caller might reach cannot change the file.
        uri = self.path.resolve().as_uri() + '?mode=ro'
        self.db = sqlite3.connect(uri, uri=True, timeout=20)
        self.db.row_factory = sqlite3.Row
        self.db.isolation_level = None
        self.db.execute('PRAGMA query_only=ON')
        # Read caches, each a private object mirroring its `Store` counterpart.
        self.batch_size = 1
        self.batch_seconds = 0.
        self._batch = []
        self._batch_keys = set()
        self._batch_started = None
        self._next = {}
        self._run_revisions = {}
        self._revision_signatures = {}
        self._revision_counter = 0
        self._resident_ids = frozenset()
        self._in_snapshot = False
        self._flush_apply_seconds = 0.
        self._flush_commit_seconds = 0.
        self._checkpoints = 0
        self._checkpoint_seconds = 0.
        self._wal_high = 0
        self.on_prune = None
        self._scenario_cache = {}
        self._candidate_meta_cache = None
        self._elite_cache = {}
        self._equivalence = None
        self._selection = {}
        self._encounter_keys = {}
        try:
            stored = self.get('provenance')
        except sqlite3.DatabaseError as error:
            self.db.close()
            raise ValueError(f'not an optimiser library: {self.path}') from error
        if not isinstance(stored, dict):
            self.db.close()
            raise ValueError(f'not an optimiser library: {self.path}')
        self.compatible = bool(stored.get('digest') == (provenance or {}).get('digest')
                               or same_simulator(stored, provenance or {}))

    # ------------------------------------------------------------------ writer surface, refused

    def set(self, key, value):
        raise RuntimeError('SnapshotStore is read-only: set() is not available.')

    def record(self, cid, phase, ordinal, result):
        raise RuntimeError('SnapshotStore is read-only: record() is not available.')

    def add(self, scenario, label, source, stats):
        raise RuntimeError('SnapshotStore is read-only: add() is not available.')

    def add_child(self, scenario, label, stats, parent, operation, target, change, lane_source,
                  proposal):
        raise RuntimeError('SnapshotStore is read-only: add_child() is not available.')

    # ------------------------------------------------------------------ snapshot lifetime

    def begin_snapshot(self):
        """Open one read transaction and reset the caches a fresh committed state invalidates.

        The first statement is a real read so the deferred transaction pins its snapshot here rather
        than at some later read: a writer commit that lands after `begin_snapshot` returns is then
        invisible to this snapshot.
        """
        if self._in_snapshot:
            raise RuntimeError('a snapshot is already open')
        self.db.execute('BEGIN')
        self._in_snapshot = True
        self.db.execute('SELECT 1 FROM meta LIMIT 1').fetchone()
        # Ordinals and metadata only describe the state the writer had committed at the previous
        # snapshot; both change under this reader's feet, so they are re-derived per snapshot.
        self._next.clear()
        self._candidate_meta_cache = None
        self._elite_cache.clear()
        self._selection.clear()
        resident = frozenset(row[0] for row in self.db.execute('SELECT id FROM candidate'))
        if resident != self._resident_ids:
            # Scenarios are immutable under their content-hash id, so the equivalence index stays
            # valid while the resident set is unchanged; an addition or prune rebuilds it lazily.
            self._equivalence = None
        self._resident_ids = resident
        for cache in (self._scenario_cache, self._encounter_keys):
            for cid in [cid for cid in cache if cid not in resident]:
                del cache[cid]
        self._refresh_revisions()

    def end_snapshot(self):
        """Release the pinned read snapshot. A read transaction holds no writes to keep."""
        if not self._in_snapshot:
            return
        self.db.rollback()
        self._in_snapshot = False

    def close(self):
        """Close the read-only connection. No writer flush, no WAL checkpoint."""
        if self._in_snapshot:
            self.db.rollback()
            self._in_snapshot = False
        self.db.close()

    # ------------------------------------------------------------------ revisions

    def _refresh_revisions(self):
        """Per-candidate revisions from committed run state, stable across snapshots.

        `Store.run_revision` is a session counter bumped on flush; that counter lives in the writer's
        memory, so a separate reader cannot see it. The committed signal is the per-phase aggregate
        the writer stores in the same transaction as each run row (`n`), with the highest resident
        ordinal as a fallback for a library whose aggregate row is missing. A revision therefore
        changes exactly when the committed rows for that candidate change - which is all
        `Optimizer._finetune_rows` compares - and a pruned candidate drops its entry so a later
        content-identical re-add starts fresh.
        """
        signature = {}
        for cid, phase, value in self.db.execute(
                "SELECT c.id, p.phase, m.value FROM candidate c "
                "CROSS JOIN (SELECT 'discovery' AS phase UNION ALL SELECT 'validation') p "
                "JOIN meta m ON m.key='aggregate:'||c.id||':'||p.phase"):
            total = json.loads(value) or {}
            if 'n' in total:
                signature.setdefault(cid, {})[phase] = int(total['n'])
        # Missing legacy aggregates need indexed ordinal reads; normal snapshots do not scan
        # the entire rolling replay table just to discover which candidates changed.
        for cid in self._resident_ids:
            for phase in ('discovery', 'validation'):
                if phase not in signature.get(cid, {}):
                    maximum = self.db.execute(
                        'SELECT MAX(ordinal) FROM run WHERE candidate=? AND phase=?',
                        (cid, phase)).fetchone()[0]
                    signature.setdefault(cid, {})[phase] = 0 if maximum is None else int(maximum)+1
        current = {cid: tuple(sorted(phases.items()))
                   for cid, phases in signature.items()}
        for cid in [cid for cid in self._revision_signatures if cid not in current]:
            self._revision_signatures.pop(cid, None)
            self._run_revisions.pop(cid, None)
        for cid, value in current.items():
            if self._revision_signatures.get(cid) != value:
                # A pruned/re-added content hash must never reuse an old renderer cache token.
                self._revision_counter += 1
                self._run_revisions[cid] = self._revision_counter
        self._revision_signatures = current
