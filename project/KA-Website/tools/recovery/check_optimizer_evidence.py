"""Persistent compact per-seed evidence: pairing survives the replay window and a reopen.

The optimiser's `run` table is a rolling replay window; the fine-tune pairing and a record-repeat
bank must not be bounded by it. These checks pin that contract without running a battle:

  * evidence keeps every accepted seed past the window (more than `MAX_SAMPLES`, and more than
    1024 here), while `Store.rows` keeps reading only the legacy window,
  * a paired series still counts more than 512 shared seeds after the library is reopened,
  * a legacy library is backfilled once, only for the run rows still resident, and no lifetime
    counter moves,
  * a repeated ordinal whose replay example was evicted is dropped instead of counted twice, and a
    genuinely out-of-order result is still refused,
  * a failed flush rolls the compact row back with the run row,
  * an unresolved run stays unknown (NULL), never a fabricated zero,
  * evidence never touches provenance or the compatibility decision,
  * a genuinely pruned candidate loses its evidence while a held candidate keeps theirs.

    python check_optimizer_evidence.py
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import strategy_evidence                                                    # noqa: E402
from strategy_optimizer import (MAX_SAMPLES, Optimizer, Store, chest_count, # noqa: E402
                                potential_chests, scope)
import strategy_optimizer as optimizer_module                               # noqa: E402
from strategy_optimizer_adapter import (default_scenario, provenance,       # noqa: E402
                                        stats)


def outcome(index, ordinal, chests=8, verdict=1, censored=False):
    """One accepted result shaped like the runner's own record (shared seeds per ordinal)."""
    return dict(verdict=verdict, censored=censored, ticks=40, prizeCallbacks=chests + ordinal,
                survivors=1, resourceUses=0,
                behavior=dict(attacks=1, heals=0, prizes=chests + ordinal),
                seeds=[ordinal, 100 + ordinal], digest=f'd{index}-{ordinal}', elapsedSeconds=.01,
                rewardOutcome=dict(awardedChests=chests if verdict == 1 else 0,
                                   pendingChests=chests, awardedBasis='test'))


def scenario(encounter=19):
    return dict(default_scenario(), tickLimit=200, encounterId=encounter)


class EvidenceCase(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(suffix='.sqlite', delete=False)
        handle.close()
        self.path = Path(handle.name)
        self.store = Store(self.path, provenance())

    def tearDown(self):
        try:
            self.store.close()
        except Exception:
            pass
        for suffix in ('', '-wal', '-shm'):
            leftover = Path(str(self.path) + suffix)
            if leftover.exists():
                os.unlink(leftover)

    def add(self, encounter=19, label='Evidence fixture', source='supplied'):
        with self.store.db:
            return self.store.add(scenario(encounter), label, source, stats(scenario(encounter)))

    def reopen(self):
        self.store.close()
        self.store = Store(self.path, provenance())
        return self.store

    @staticmethod
    def optimizer():
        return Optimizer.__new__(Optimizer)


class ReplayWindowTests(EvidenceCase):
    def test_more_than_1024_rows_survive_replay_pruning(self):
        with self.store.db:
            self.store.set('scope', scope(scenario()))
        cid = self.add()
        self.store.batch_size = 256
        for ordinal in range(1100):
            self.store.record(cid, 'validation', ordinal, outcome(0, ordinal))
        self.store.flush()
        retained = self.store.db.execute('SELECT COUNT(*) FROM run WHERE candidate=?',
                                         (cid,)).fetchone()[0]
        self.assertEqual(retained, MAX_SAMPLES, 'the replay window must still roll to MAX_SAMPLES')
        self.assertGreater(strategy_evidence.count(self.store.db, cid), 1024)
        self.assertEqual(strategy_evidence.count(self.store.db, cid), 1100)
        self.assertEqual(len(self.store.rows(cid, 'validation')), MAX_SAMPLES)

    def test_pairing_count_over_512_survives_reopen(self):
        with self.store.db:
            self.store.set('scope', scope(scenario()))
        cid = self.add()
        self.store.batch_size = 256
        for ordinal in range(700):
            self.store.record(cid, 'validation', ordinal, outcome(1, ordinal))
        self.store.flush()
        self.assertGreater(len(self.optimizer()._finetune_series(self.store, cid)), 512)
        self.reopen()
        series = self.optimizer()._finetune_series(self.store, cid)
        self.assertEqual(len(series), 700)
        self.assertGreater(len(series), 512)


class BackfillTests(EvidenceCase):
    def test_legacy_backfill_uses_only_observed_rows_and_leaves_counters(self):
        with self.store.db:
            self.store.set('scope', scope(scenario()))
        cid = self.add()
        self.store.batch_size = 256
        for ordinal in range(600):
            self.store.record(cid, 'validation', ordinal, outcome(2, ordinal, verdict=2))
        self.store.flush()
        total = self.store.get('totalRuns')
        lifetime = self.store.get('encounterLifetime')
        holders = self.store.get('recordHolders')
        retained = self.store.db.execute('SELECT COUNT(*) FROM run WHERE candidate=?',
                                         (cid,)).fetchone()[0]
        # Simulate a library written before the evidence table existed.
        with self.store.db:
            self.store.db.execute('DELETE FROM evidence')
            self.store.db.execute("DELETE FROM meta WHERE key='evidenceBackfill'")
        self.assertEqual(strategy_evidence.count(self.store.db), 0)
        self.reopen()
        self.assertEqual(strategy_evidence.count(self.store.db, cid), retained)
        self.assertLess(strategy_evidence.count(self.store.db, cid), 600,
                        'an evicted run must not be claimed reconstructed')
        self.assertEqual(self.store.get('totalRuns'), total)
        self.assertEqual(self.store.get('encounterLifetime'), lifetime)
        self.assertEqual(self.store.get('recordHolders'), holders)
        self.assertEqual(strategy_evidence.backfill(self.store.db), 0, 'a second pass inserts nothing')
        self.assertEqual(strategy_evidence.count(self.store.db, cid), retained)


class GuardTests(EvidenceCase):
    def test_duplicate_evicted_ordinal_is_dropped_and_future_is_refused(self):
        with self.store.db:
            self.store.set('scope', scope(scenario()))
        cid = self.add()
        for ordinal in range(513):
            self.store.record(cid, 'validation', ordinal, outcome(3, ordinal))
        self.store.flush()
        self.assertIsNone(self.store.db.execute(
            'SELECT 1 FROM run WHERE candidate=? AND phase=? AND ordinal=64',
            (cid, 'validation')).fetchone())
        self.assertTrue(strategy_evidence.has(self.store.db, cid, 'validation', 64))
        runs_before = self.store.get('totalRuns')
        self.store.record(cid, 'validation', 64, outcome(3, 64))
        self.assertEqual(self.store.get('totalRuns'), runs_before, 'a repeat must not count twice')
        self.assertEqual(strategy_evidence.count(self.store.db, cid), 513)
        with self.assertRaises(ValueError):
            self.store.record(cid, 'validation', 9999, outcome(3, 9999))
        self.store.batch_size = 4
        self.store.record(cid, 'validation', 513, outcome(4, 513))
        self.store.record(cid, 'validation', 513, outcome(4, 513))
        self.assertEqual(self.store.pending_records, 1, 'a buffered duplicate is dropped')
        self.store.flush()
        self.assertEqual(strategy_evidence.count(self.store.db, cid), 514)

    def test_failed_flush_rolls_back_the_compact_row(self):
        with self.store.db:
            self.store.set('scope', scope(scenario()))
        cid = self.add()
        self.store.batch_size = 8
        self.store.record(cid, 'validation', 0, outcome(5, 0))
        self.store.record(cid, 'validation', 1, outcome(5, 1))
        original = self.store._apply

        def broken(entry, acc):
            original(entry, acc)
            if entry['ordinal'] == 1:
                raise RuntimeError('disk full after writes')

        self.store._apply = broken
        try:
            with self.assertRaises(RuntimeError):
                self.store.flush()
        finally:
            self.store._apply = original
        self.assertEqual(self.store.pending_records, 2)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM run').fetchone()[0], 0)
        self.assertEqual(strategy_evidence.count(self.store.db), 0)
        self.store.flush()
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM run').fetchone()[0], 2)
        self.assertEqual(strategy_evidence.count(self.store.db, cid), 2)


class FidelityTests(EvidenceCase):
    def test_unresolved_run_stays_unknown(self):
        with self.store.db:
            self.store.set('scope', scope(scenario()))
        cid = self.add()
        unresolved = dict(verdict=None, censored=True, ticks=40, prizeCallbacks=None,
                          survivors=1, resourceUses=0, seeds=[0, 100], digest='c0')
        self.store.record(cid, 'validation', 0, unresolved)
        self.store.record(cid, 'validation', 1, outcome(6, 1, chests=5, verdict=2))
        self.store.flush()
        raw = self.store.db.execute(
            'SELECT verdict,censored,prizeCallbacks,awardedChests,awardedBasis,pendingChests '
            'FROM evidence WHERE candidate=? AND phase=? AND ordinal=0',
            (cid, 'validation')).fetchone()
        self.assertIsNone(raw[0])
        self.assertEqual(raw[1], 1)
        self.assertIsNone(raw[2])
        self.assertIsNone(raw[3])
        self.assertIsNone(raw[4])
        self.assertIsNone(raw[5])
        rows = strategy_evidence.rows(self.store.db, cid, 'validation')
        self.assertEqual(chest_count(rows[0]), (None, 'unresolved'))
        self.assertIsNone(potential_chests(rows[0]))
        self.assertEqual(chest_count(rows[1]), (0, 'loss-gate'))
        self.assertEqual(potential_chests(rows[1]), 5)

    def test_evidence_does_not_touch_provenance_or_compatibility(self):
        with self.store.db:
            self.store.set('scope', scope(scenario()))
        cid = self.add()
        raw_before = self.store.db.execute(
            "SELECT value FROM meta WHERE key='provenance'").fetchone()[0]
        self.assertTrue(self.store.compatible)
        self.store.record(cid, 'validation', 0, outcome(7, 0))
        self.store.flush()
        self.reopen()
        self.assertTrue(self.store.compatible)
        self.assertEqual(self.store.db.execute(
            "SELECT value FROM meta WHERE key='provenance'").fetchone()[0], raw_before)
        self.store.close()
        self.store = Store(self.path, dict(digest='two'))
        self.assertFalse(self.store.compatible)

    def test_pruned_candidate_loses_evidence_held_keeps_theirs(self):
        with self.store.db:
            self.store.set('scope', scope(scenario(1)))
        first = self.add(encounter=1, label='First', source='mutation')
        self.store.record(first, 'validation', 0, outcome(8, 0))
        self.store.flush()
        second = self.add(encounter=2, label='Second', source='mutation')
        self.store.record(second, 'validation', 0, outcome(9, 0))
        self.store.flush()
        self.assertGreater(strategy_evidence.count(self.store.db, first), 0)
        original = optimizer_module.MAX_CANDIDATES_TOTAL
        optimizer_module.MAX_CANDIDATES_TOTAL = 2
        try:
            with self.store.db:
                self.store.add(scenario(3), 'Third', 'mutation', stats(scenario(3)))
        finally:
            optimizer_module.MAX_CANDIDATES_TOTAL = original
        self.assertIsNone(self.store.db.execute(
            'SELECT 1 FROM candidate WHERE id=?', (first,)).fetchone())
        self.assertEqual(strategy_evidence.count(self.store.db, first), 0)
        self.assertGreater(strategy_evidence.count(self.store.db, second), 0)


class IntegrationTests(unittest.TestCase):
    def test_finetune_suite_passes(self):
        proc = subprocess.run([sys.executable, str(HERE/'check_optimizer_finetune.py')],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_flush_check_passes(self):
        proc = subprocess.run([sys.executable, str(HERE/'check_record_batching.py')],
                              capture_output=True, text=True)
        output = proc.stdout + proc.stderr
        if proc.returncode and 'unable to open database file' in output:
            self.skipTest('this sandbox refuses a database in a fresh temp subdirectory')
        self.assertEqual(proc.returncode, 0, output)


if __name__ == '__main__':
    unittest.main(verbosity=2)
