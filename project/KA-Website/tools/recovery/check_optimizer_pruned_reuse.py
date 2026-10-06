"""A build that is pruned and then reproposed comes back with its history, not as a new build.

The trace showed the same candidate - `8019aafb…` - reported as newly added again and again. The cause
is not a stale cache: pruning deletes a candidate's row, its aggregates, its compact evidence and its
run rows (that is what bounds the library), while the *identity* is a hash of the scenario. The same
build proposed later therefore arrives with the same id, no candidate row, and no history - so it is
re-measured from ordinal zero, its seed pairs are run a second time, and the outcomes are counted
again. Proposing it also overwrote its lineage row, relabelling a build the user had already measured
as the work of whoever reproposed it.

This check proves the replacement, on a library small enough to be saturated in a test:

  * pruning a measured build archives its per-phase aggregates and remembers how far it was measured;
  * reproposing the identical build adopts it again - same id - and reports it as *reused*, not new;
  * its measured counters come back, so it is not treated as untried;
  * its seed ordinals resume after the prefix it already ran, so those seeds are not bought twice;
  * its original lineage - creator, parent and change - is preserved exactly;
  * the next accepted result continues the count instead of double-counting a re-run.

    python check_optimizer_pruned_reuse.py [--json OUT]
"""
import argparse
import json
import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import strategy_optimizer as optimizer  # noqa: E402
from strategy_optimizer_adapter import default_scenario, provenance, stats as adapter_stats  # noqa: E402

ENCOUNTER = 19


def scenario(offset):
    """The frozen default fight with one DPS stat moved, so each build has its own identity."""
    built = default_scenario()
    built['encounterId'] = ENCOUNTER
    built['defeatCount'] = 0
    built['inputs'] = []
    for unit in built['ownUnits']:
        if unit.get('human') and unit['name'].startswith('Ninja'):
            unit['parameters'][13] = dict(unit['parameters'].get(13) or {}, rawValue=38 + offset)
    return built


def run_result(index, chests, verdict=1):
    return dict(verdict=verdict, censored=False, ticks=120, prizeCallbacks=chests, retained=None,
                survivors=1, resourceUses=0, elapsedSeconds=.01,
                behavior=dict(heals=0, attacks=3, prizes=chests),
                seeds=optimizer.seed_pair('validation', index), digest=f'd{index}',
                rewardOutcome=dict(pendingChests=chests, awardedChests=chests, awardedBasis=None,
                                   inventoryVerified=False, reason=None))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json')
    args = parser.parse_args(argv)
    report = dict(schema='ka-optimizer-pruned-reuse-1', checks=[], cases=0)
    failures = []

    def record_case(title, detail):
        entry = dict(detail)
        entry['name'] = title
        report['checks'].append(entry)
        report['cases'] += 1
        print(f'  OK {title}: {json.dumps(detail, sort_keys=True)[:240]}')

    def check(condition, message):
        if not condition:
            failures.append(message)

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as work:
        path = pathlib.Path(work)/'saturated.sqlite'
        saved = (optimizer.MAX_CANDIDATES_PER_ENCOUNTER, optimizer.MAX_CANDIDATES_TOTAL)
        optimizer.MAX_CANDIDATES_PER_ENCOUNTER, optimizer.MAX_CANDIDATES_TOTAL = 2, 3
        store = optimizer.Store(path, provenance())
        try:
            with store.db:
                store.set('scope', optimizer.scope(scenario(0)))
                root = store.add(scenario(0), 'Community build', 'supplied', {})
            target, _existed = store.add_child(scenario(1), 'Rebel T1.v2 · broke C7', lambda: {},
                                               root, 'rebel', 'T1', 'broke C7', 'rebel', 1)
            # Measured, but deliberately *not* a contender: four defeats with nothing earned, so the
            # population cap is free to prune it - which is the state this check is about.
            for index in range(4):
                store.record(target, 'validation', index, run_result(index, chests=0, verdict=2))
            store.flush()
            before_n = store.get(f'aggregate:{target}:validation')['n']
            before_next = store.next_ordinal(target, 'validation')
            lineage_before = dict(store.db.execute(
                'SELECT parent, source, operation, change FROM lineage WHERE candidate=?',
                (target,)).fetchone())
            check(before_n == 4 and before_next == 4,
                  f'the fixture did not measure its target: n={before_n} next={before_next}')

            # Fill the encounter to its (lowered) cap so the next proposals have to prune.
            for offset in range(2, 8):
                store.add_child(scenario(offset), f'filler {offset}', lambda: {}, root, 'branch',
                                'set-stat', f'atk 38->{38+offset}', 'fresh', offset)
            store.flush()
            gone = store.db.execute('SELECT 1 FROM candidate WHERE id=?', (target,)).fetchone() is None
            check(gone, 'the target was not pruned, so this check would prove nothing')
            archived = store.db.execute(
                'SELECT validation FROM predictor_history WHERE candidate=?', (target,)).fetchone()
            check(archived is not None and json.loads(archived[0])['n'] == before_n,
                  f'the pruned build did not keep its aggregate: {archived}')
            remembered = (store.get(optimizer.PRUNED_ORDINALS_KEY) or {}).get(target) or {}
            check(int(remembered.get('validation') or 0) == before_next,
                  f'the pruned build did not keep its ordinal: {remembered}')
            record_case('pruning-keeps-the-builds-history',
                        dict(aggregatesRestored=before_n, ordinalRemembered=remembered,
                             candidateRowRemoved=True))

            # The identical build is proposed again: same identity, adopted rather than re-created.
            again, existed = store.add_child(scenario(1), 'Rebel T1.v3 · broke C8', lambda: {},
                                             root, 'rebel', 'T1', 'broke C8', 'rebel', 9)
            check(again == target, f'the reproposal produced a different identity: {again[:12]}')
            check(existed is True, 'the reproposal was reported as newly created work')
            restored = store.get(f'aggregate:{target}:validation')
            check(restored and int(restored.get('n') or 0) == before_n,
                  f'the adopted build did not get its counters back: {restored}')
            check(store.next_ordinal(target, 'validation') == before_next,
                  f'the adopted build would re-run its prefix: next='
                  f'{store.next_ordinal(target, "validation")} expected {before_next}')
            lineage_after = dict(store.db.execute(
                'SELECT parent, source, operation, change FROM lineage WHERE candidate=?',
                (target,)).fetchone())
            check(lineage_after == lineage_before,
                  f'the reproposal rewrote the original provenance: {lineage_after}')
            record_case('a-reproposal-adopts-the-build-it-already-measured',
                        dict(sameIdentity=True, reportedAsReused=True, counters=restored.get('n'),
                             resumesAt=store.next_ordinal(target, 'validation'),
                             lineage=lineage_after))

            # The next result continues the count: the prefix is not bought again.
            store.record(target, 'validation', before_next, run_result(before_next, chests=3))
            store.flush()
            after = store.get(f'aggregate:{target}:validation')
            check(int(after.get('n') or 0) == before_n+1,
                  f'the adopted build double-counted a seed: n={after.get("n")}')
            check(int(after.get('wins') or 0) == int(restored.get('wins') or 0)+1,
                  'the adopted build did not credit the new result exactly once')
            record_case('the-prefix-is-not-bought-twice',
                        dict(nBefore=before_n, nAfter=after.get('n'),
                             resumedOrdinal=before_next))

            # An *unrelated* build still takes a fresh identity: adoption is identity, not a cache hit.
            fresh, fresh_existed = store.add_child(scenario(31), 'Branch · fresh build', lambda: {},
                                                   root, 'branch', 'set-stat', 'atk 38->69',
                                                   'fresh', 10)
            check(fresh != target and fresh_existed is False,
                  'an unrelated build was mistaken for a previously pruned one')
            record_case('only-the-identical-build-is-adopted',
                        dict(fresh=fresh[:12], existed=fresh_existed))
        finally:
            store.close()
            optimizer.MAX_CANDIDATES_PER_ENCOUNTER, optimizer.MAX_CANDIDATES_TOTAL = saved

    print(f'pruned-reuse checks: {report["cases"]} cases, {len(failures)} failures')
    for failure in failures:
        print(f'  FAIL {failure}')
    report['failures'] = failures
    if args.json:
        pathlib.Path(args.json).write_text(json.dumps(report, indent=1, sort_keys=True) + '\n',
                                           encoding='utf-8')
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
