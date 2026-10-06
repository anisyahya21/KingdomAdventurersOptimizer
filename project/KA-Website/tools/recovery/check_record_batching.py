"""Batched record bookkeeping: same results, one transaction per batch, nothing lost.

    python check_record_batching.py
"""
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import strategy_optimizer as optimizer  # noqa: E402
from strategy_optimizer_adapter import default_scenario, provenance, stats  # noqa: E402


def fake_result(index, verdict=1, prize=3, ticks=100):
    return dict(verdict=verdict, censored=False, ticks=ticks, prizeCallbacks=prize,
                preVerdictPrizeCallbacks=prize, seeds=[index, index+1], resourceUses=0,
                survivors=3, healthFraction=.5, elapsedSeconds=.01,
                behavior=dict(heals=0, attacks=1, prizes=prize), progressMetrics={},
                rewardOutcome=dict(awardedChests=prize, pendingChests=prize,
                                   inventoryVerified=False),
                digest=f'batch-{index}')


def build(path, batching):
    store = optimizer.Store(path, provenance())
    scenario = dict(default_scenario(), tickLimit=200, encounterId=19)
    with store.db:
        store.set('scope', optimizer.scope(scenario))
        cid = store.add(scenario, 'Batch fixture', 'supplied', stats(scenario))
    if batching:
        store.batch_size = 8
        store.batch_seconds = 5.0
    return store, cid


def main():
    failures = []

    def expect(label, condition, detail=''):
        print(f'  {"ok   " if condition else "FAIL "} {label}' + ('' if condition else f': {detail}'))
        if not condition:
            failures.append(label)

    with tempfile.TemporaryDirectory(prefix='ka-batch-', ignore_cleanup_errors=True) as root:
        run_plain = Path(root)/'plain.sqlite'
        run_batch = Path(root)/'batch.sqlite'
        # ---- identical results, batched or not ------------------------------------------------
        for path, batching in ((run_plain, False), (run_batch, True)):
            store, cid = build(path, batching)
            for ordinal in range(20):
                store.record(cid, 'discovery', ordinal, fake_result(ordinal))
            pending = store.pending_records
            store.flush()
            with store.db:
                pass
            store.close()
            if batching:
                expect('results wait for the batch instead of being written one by one',
                       pending > 1, f'{pending} pending')
        plain = optimizer.Store(run_plain, provenance())
        batch = optimizer.Store(run_batch, provenance())
        try:
            for key in ('totalRuns', 'timedRuns', 'simulationSeconds'):
                expect(f'{key} matches the unbatched run', plain.get(key) == batch.get(key),
                       f'{plain.get(key)!r} vs {batch.get(key)!r}')
            expect('the aggregate matches the unbatched run',
                   plain.get('aggregate:{}:discovery'.format(plain.db.execute(
                       'SELECT id FROM candidate LIMIT 1').fetchone()[0])) ==
                   batch.get('aggregate:{}:discovery'.format(batch.db.execute(
                       'SELECT id FROM candidate LIMIT 1').fetchone()[0])))
            expect('every run row is present exactly once',
                   plain.db.execute('SELECT COUNT(*) FROM run').fetchone()[0] == 20 ==
                   batch.db.execute('SELECT COUNT(*) FROM run').fetchone()[0])
            expect('the lifetime record matches',
                   plain.get('encounterLifetime') == batch.get('encounterLifetime'))
            expect('the next ordinal matches',
                   plain.next_ordinal(plain.db.execute('SELECT id FROM candidate LIMIT 1').fetchone()[0],
                                      'discovery') == 20)
        finally:
            plain.close()
            batch.close()

        # ---- ordering guard, duplicates and a failed flush -------------------------------------
        store, cid = build(Path(root)/'guard.sqlite', True)
        try:
            store.record(cid, 'discovery', 0, fake_result(0))
            expect('a buffered ordinal is not offered twice', store.pending_records == 1,
                   f'{store.pending_records} pending')
            store.record(cid, 'discovery', 0, fake_result(0))
            expect('a duplicate result is dropped', store.pending_records == 1,
                   f'{store.pending_records} pending')
            try:
                store.record(cid, 'discovery', 5, fake_result(5))
                expect('an out-of-order result is refused', False, 'no error raised')
            except ValueError as exc:
                expect('an out-of-order result is refused', 'Out-of-order' in str(exc), str(exc))
            expect('the refused result did not enter the batch', store.pending_records == 1,
                   f'{store.pending_records} pending')
            store.record(cid, 'discovery', 1, fake_result(1))
            # A flush that fails must keep its results and leave the table untouched.
            original = store._apply
            calls = []

            def broken(entry, accumulator):
                calls.append(entry)
                raise RuntimeError('disk full')

            store._apply = broken
            try:
                store.flush()
                expect('a failed flush raises to the caller', False, 'no error raised')
            except RuntimeError as exc:
                expect('a failed flush raises to the caller', 'disk full' in str(exc), str(exc))
            finally:
                store._apply = original
            expect('a failed flush keeps every result', store.pending_records == 2,
                   f'{store.pending_records} pending')
            expect('a failed flush writes nothing',
                   store.db.execute('SELECT COUNT(*) FROM run').fetchone()[0] == 0)
            store.flush()
            expect('the retried flush writes them',
                   store.db.execute('SELECT COUNT(*) FROM run').fetchone()[0] == 2)
            expect('the next ordinal continues from the committed rows',
                   store.next_ordinal(cid, 'discovery') == 2,
                   str(store.next_ordinal(cid, 'discovery')))
            # Ordinary siblings do not force a sync; a population audit still gets a barrier.
            store.record(cid, 'discovery', 2, fake_result(2))
            other = dict(default_scenario(), tickLimit=200, encounterId=19)
            other['ownUnits'][0]['parameters'][13]['rawValue'] += 5
            with store.db:
                store.add(other, 'Another build', 'mutation', stats(other))
            expect('ordinary add keeps the batch pending', store.pending_records == 1,
                   f'{store.pending_records} pending')
            original_cap = optimizer.MAX_CANDIDATES_TOTAL
            original_audit = store._make_room
            seen = []
            def audit(_encounter):
                seen.append((store.pending_records,
                             store.db.execute('SELECT COUNT(*) FROM run').fetchone()[0]))
            # The supplied fixture is protected and does not consume a mutation slot.
            # One existing mutation therefore fills a one-slot pool and triggers the audit.
            optimizer.MAX_CANDIDATES_TOTAL = 1
            store._make_room = audit
            try:
                third = dict(default_scenario(), tickLimit=200, encounterId=19)
                third['ownUnits'][0]['parameters'][13]['rawValue'] += 10
                with store.db:
                    store.add(third, 'Third build', 'mutation', stats(third))
            finally:
                optimizer.MAX_CANDIDATES_TOTAL = original_cap
                store._make_room = original_audit
            expect('the buffered result was written before the population audit',
                   seen == [(0, 3)], str(seen))
        finally:
            store.close()

    print()
    print('FAILURES: ' + str(failures) if failures else
          'batched record bookkeeping preserves results, ordering, duplicates and durability')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
