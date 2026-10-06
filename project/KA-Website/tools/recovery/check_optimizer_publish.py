"""The cached publish view must be deep-equal to a full rebuild after every kind of change.

`Optimizer._published_candidates` reuses a candidate's published record until something it depends on
moves. That is only safe if the reuse is *exactly* equivalent, so this harness mutates a real library
through every path that can change the payload and requires the cached list to equal the original
full-rebuild implementation (`full_candidate_payloads`) each time:

    initial -> new discovery run -> validation run -> 64-run archive admission -> new candidate
    -> probe bank -> candidate pruning -> record-holder update -> encounter expansion

    python check_optimizer_publish.py [--library PATH]

With `--library` the live library is copied first, so the comparison also covers a saturated
256-candidate shape; the user's file is never written to.
"""
import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import strategy_optimizer as optimizer  # noqa: E402
from strategy_optimizer_adapter import default_scenario, provenance, stats, validate_scenario  # noqa: E402


def win_result(ordinal, prize_callbacks, awarded, seeds, digest):
    return dict(verdict=1, censored=False, ticks=40, prizeCallbacks=prize_callbacks, survivors=2,
                resourceUses=0, behavior=dict(attacks=2, heals=1, prizes=prize_callbacks),
                seeds=list(seeds), digest=digest, elapsedSeconds=.01,
                rewardOutcome=dict(awardedChests=awarded, pendingChests=awarded,
                                   awardedBasis='reward-entitlement-certificate'))


def loss_result(prize_callbacks, pending, seeds, digest):
    return dict(verdict=2, censored=False, ticks=40, prizeCallbacks=prize_callbacks, survivors=0,
                resourceUses=0, behavior=dict(attacks=2, heals=0, prizes=prize_callbacks),
                seeds=list(seeds), digest=digest, elapsedSeconds=.01,
                rewardOutcome=dict(awardedChests=0, pendingChests=pending,
                                   awardedBasis='loss-gate'))


def compare(label, live, store, failures):
    cached = live._published_candidates(store)
    full = optimizer.full_candidate_payloads(store)
    if cached != full:
        detail = 'lengths %d vs %d' % (len(cached), len(full))
        for left, right in zip(cached, full):
            if left != right:
                differing = sorted(key for key in set(left) | set(right)
                                   if left.get(key) != right.get(key))
                detail += f'; first differing candidate {left.get("id", "?")[:8]} on {differing[:6]}'
                break
        failures.append(f'{label}: cached payload != full rebuild ({detail})')
        return False
    print(f'  {label:<34} {len(cached):>3} candidates identical')
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', type=Path)
    args = parser.parse_args()
    failures = []
    with tempfile.TemporaryDirectory(prefix='ka-publish-') as root:
        path = Path(root) / 'publish.sqlite'
        if args.library:
            shutil.copyfile(args.library, path)
        else:
            store = optimizer.Store(path, provenance())
            scenario = validate_scenario(dict(default_scenario(), tickLimit=40, encounterId=19))
            with store.db:
                store.set('scope', optimizer.scope(scenario))
                store.add(scenario, 'Publish fixture', 'supplied', stats(scenario))
            store.close()
        live = optimizer.Optimizer.__new__(optimizer.Optimizer)
        live._candidate_cache = {}
        store = optimizer.Store(path, provenance())
        try:
            if not compare('initial', live, store, failures):
                return 1
            # The baseline candidate for encounter 19 is the one every mutation below touches.
            baseline = next(c for c in store.candidates() if c['source'] == 'supplied')
            cid = baseline['id']
            with store.db:
                store.record(cid, 'discovery', 0, win_result(0, 3, 3, (1, 2), 'w0'))
            compare('new discovery run', live, store, failures)
            with store.db:
                for ordinal in range(optimizer.VALIDATION_RUNS):
                    store.record(cid, 'validation', ordinal,
                                 win_result(ordinal, 4 + ordinal % 3, 4 + ordinal % 3,
                                            (ordinal, ordinal + 1), f'v{ordinal}'))
            compare(f'{optimizer.VALIDATION_RUNS} validation runs (archive)', live, store, failures)
            # A second candidate, a probe bank, a clean validation bank (which must actually admit a
            # build to the archive), a losing record holder and a prune. The archive admission runs on
            # this fresh candidate rather than on the fixture's own baseline: a saturated library can
            # already carry a mixed-history bank for encounter 19, where a clear-win bank is *not*
            # supposed to be admitted, so asserting at that candidate would test the fixture instead
            # of the path.
            other = validate_scenario(dict(baseline_scenario := json.loads(baseline['scenario']),
                                           encounterId=0))
            with store.db:
                second = store.add(other, 'Second candidate', 'mutation', stats(other))
                store.set('probeBanks', {second: 8})
            compare('new candidate + probe bank', live, store, failures)
            with store.db:
                for ordinal in range(optimizer.VALIDATION_RUNS):
                    store.record(second, 'validation', ordinal,
                                 win_result(ordinal, 6 + ordinal % 4, 6 + ordinal % 4,
                                            (100 + ordinal, 200 + ordinal), f's{ordinal}'))
            if not store.archive():
                failures.append('the clean 64-run bank did not admit to the archive, so that path is untested')
            compare('archive admission', live, store, failures)
            with store.db:
                store.record(second, 'discovery', 0, loss_result(21, 29, (5, 6), 'loss-record'))
            compare('record-holder update (loss)', live, store, failures)
            holders = store.get('recordHolders') or {}
            if not any(key.startswith('potential:') for key in holders):
                failures.append('the losing run did not set a potential record holder')
            # The prune victim has to be an unelected mutation with no archive protection, which the
            # candidate above now has: Store.add only ever drops the oldest unelected mutation.
            # The active population is bounded per encounter, so the fillers below have to be built
            # from the victim's own scenario - filling a different fight would leave it untouched.
            victim_scenario = validate_scenario(dict(baseline_scenario, encounterId=1))
            with store.db:
                victim = store.add(victim_scenario, 'Prune victim', 'mutation', stats(other))
            compare('prune victim added', live, store, failures)
            import copy
            filler_base = victim_scenario
            for index in range(optimizer.MAX_CANDIDATES + 2):
                filler = copy.deepcopy(filler_base)
                filler['ownUnits'][0]['parameters'][13]['rawValue'] += index + 1
                filler = validate_scenario(filler)
                with store.db:
                    store.add(filler, f'Filler {index}', 'mutation', stats(filler))
                if not any(row[0] == victim for row in
                           store.db.execute('SELECT id FROM candidate')):
                    break
            pruned = not any(row[0] == victim for row in
                             store.db.execute('SELECT id FROM candidate'))
            if not pruned:
                failures.append('nothing was pruned, so cache eviction is untested')
            if any(row[0] == second for row in store.db.execute('SELECT id FROM candidate')):
                pass
            else:
                failures.append('the archive-protected candidate was pruned as well '
                                f'(victim {victim[:8]} pruned={pruned}, '
                                f'archive={[(row["candidate"][:8], row["cell"]) for row in store.archive()]})')
            compare('after pruning', live, store, failures)
            with store.db:
                for raw, label in optimizer.baseline_scenarios(baseline_scenario):
                    candidate = validate_scenario(raw)
                    store.add(candidate, label, 'supplied', stats(candidate))
            compare('after encounter expansion', live, store, failures)
            encounters = {json.loads(c['scenario'])['encounterId'] for c in store.candidates()}
            if encounters != set(range(20)):
                failures.append(f'expansion produced encounters {sorted(encounters)}')
        finally:
            store.close()
    if failures:
        print('FAILURES:')
        for row in failures[:8]:
            print('  ' + row)
        return 1
    print('  every published candidate record matched the full rebuild after each change')
    return 0


if __name__ == '__main__':
    sys.exit(main())
