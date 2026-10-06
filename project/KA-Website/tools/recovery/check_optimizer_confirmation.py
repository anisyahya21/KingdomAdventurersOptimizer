"""Deterministic checks for the independent frozen-strategy confirmation tool.

Uses small synthetic libraries and a synthetic outcome mock (no real combat, no live library, no
GUI). Proves that `strategy_confirmation`:

  (a) draws common cryptographic seed pairs, rejects any collision with the library's authorised
      discovery/validation bank pairs, and freezes scenarios, provenance and seeds into an
      immutable manifest written before evaluation;
  (b) rejects a changed request (run count, candidate set, scenario or provenance) and refuses to
      adopt results without a manifest;
  (c) never scores an unresolved or failed run as zero - such readings are stored as such, mark the
      candidate incomplete, and withhold a claimed confirmation;
  (d) resumes only the missing (candidate, ordinal) pairs of the same manifest without duplicating
      any durable row;
  (e) reports known summary statistics (mean, sample SD, approximate fixed-sample interval) and
      paired mean differences over matched resolved pairs only;
  (f) leaves a missing library uncreated.

    python check_optimizer_confirmation.py
"""
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor
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

import strategy_confirmation as confirmation                                   # noqa: E402
from strategy_optimizer import DISCOVERY_RUNS, VALIDATION_RUNS, seed_pair as bank_seed  # noqa: E402

FAILURES = []
SIMULATOR = 'check_optimizer_confirmation:synthetic_simulate'


def synthetic_simulate(scenario, seed_pair):
    """A tiny synthetic outcome mock keyed only by the drawn seeds (importable in pool workers)."""
    base, math_seed = scenario['base'], seed_pair[0]
    if math_seed % 5 == 4:
        return dict(verdict=None, censored=True, ticks=100)
    if math_seed % 5 == 3:
        raise RuntimeError('synthetic engine failure')
    return dict(verdict=1, censored=False, ticks=50,
                rewardOutcome=dict(awardedChests=base + math_seed % 3,
                                   awardedBasis='reward-entitlement-certificate'))


def expect(label, condition, detail=''):
    print(f'  {"ok   " if condition else "FAIL "} {label}' + ('' if condition else f': {detail}'))
    if not condition:
        FAILURES.append(label)


def thread_pool(workers):
    """A bounded in-process executor; the sandbox denies multiprocessing pipes."""
    return ThreadPoolExecutor(max_workers=max(1, workers))


def controller(values):
    """A deterministic `randbits` that walks a fixed sequence (one seed value per call)."""
    seq = iter(values)

    def randbits(bits):
        return next(seq) % (1 << bits)

    return randbits


def fixture_root():
    for candidate in (ROOT / 'TEMP' / 'ka-optimizer-20260924' / 'tmp', Path(tempfile.gettempdir())):
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / '.write-probe'
            probe.write_text('ok')
            probe.unlink()
            return candidate
        except OSError:
            continue
    raise RuntimeError('no writable temporary directory')


def make_library(path, candidates):
    db = sqlite3.connect(path)
    db.executescript(
        'CREATE TABLE candidate(id TEXT PRIMARY KEY, scenario TEXT NOT NULL, label TEXT NOT NULL,'
        ' source TEXT NOT NULL, stats TEXT NOT NULL, created INTEGER NOT NULL);'
        'CREATE TABLE run(candidate TEXT NOT NULL, phase TEXT NOT NULL, ordinal INTEGER NOT NULL,'
        ' result TEXT NOT NULL, PRIMARY KEY(candidate,phase,ordinal));')
    for cid, scenario in candidates.items():
        db.execute('INSERT INTO candidate VALUES (?,?,?,?,?,?)',
                   (cid, json.dumps(scenario), 'fixture', 'supplied', '{}', 0))
        for ordinal in range(2):  # a couple of real bank rows, as a live library would hold
            db.execute('INSERT INTO run VALUES (?,?,?,?)',
                       (cid, 'discovery', ordinal, json.dumps(dict(seeds=bank_seed('discovery', ordinal)))))
    db.commit()
    db.close()


def check_pure():
    print('bounded submission and seed separation')
    tasks = list(range(10))
    seen, results = [], []
    futures = {}

    def submit(task):
        future = concurrent.futures.Future()
        future.set_result(task * 2)
        futures[task] = future
        return future

    confirmation.bounded_submit(tasks, submit, lambda task, value: results.append(value), 3,
                                observer=seen.append)
    expect('bounded submission never exceeded the bound', max(seen) <= 3, str(max(seen)))
    expect('every job streamed exactly once',
           len(results) == len(tasks) and sorted(results) == sorted(task * 2 for task in tasks))

    reserved = {(123, 456), tuple(bank_seed('discovery', 0))}
    pairs, rejected = confirmation.draw_seed_pairs(
        3, reserved, controller([123, 456, 1, 2, 3, 4, 5, 6, *bank_seed('discovery', 0)]))
    expect('a reserved bank pair is rejected', rejected == 1, str(rejected))
    expect('drawn pairs never collide with the bank', all(tuple(p) not in reserved for p in pairs))
    expect('exactly the requested pairs are drawn', len(pairs) == 3)


def main():
    with_fixture = fixture_root() / f'ka-confirmation-{os.getpid()}-{time.time_ns()}'
    output = with_fixture / 'out'
    library = with_fixture / 'library.sqlite'
    try:
        with_fixture.mkdir(parents=True)
        check_pure()

        print('freezing, statistics and durability')
        make_library(library, {'A': {'base': 10}, 'B': {'base': 7}})
        draws = [0, 10, 1, 11, 2, 12, 3, 13, 4, 14, 5, 15]
        report = confirmation.run_confirmation(
            library, ['A', 'B'], 6, 2, output, simulator=SIMULATOR, randbits=controller(draws),
            executor_factory=thread_pool)
        manifest = json.loads((output / 'manifest.json').read_text(encoding='utf-8'))
        expect('manifest frozen before evaluation', (output / 'manifest.json').is_file()
               and (output / 'confirmation.sqlite').is_file())
        expect('manifest stores the requested scenarios',
               [c['id'] for c in manifest['candidates']] == ['A', 'B']
               and manifest['candidates'][0]['scenario'] == {'base': 10})
        expect('manifest stores provenance and six seed pairs',
               bool(manifest['provenanceDigest']) and len(manifest['seeds']) == 6)
        recomputed = confirmation._digest({k: v for k, v in manifest.items() if k != 'digest'})
        expect('manifest digest covers its own body', recomputed == manifest['digest'])
        expect('manifest declares the fixed run count', manifest['runs'] == 6)

        a = report['candidates']['A']
        expect('resolved / unresolved / failed counted separately',
               (a['resolved'], a['unresolved'], a['failed']) == (4, 1, 1), str(a))
        expect('unresolved reading is incomplete and withholds confirmation', a['incomplete'] is True)
        expect('mean covers resolved readings only', abs(a['mean'] - 11.25) < 1e-9, str(a['mean']))
        expect('sample SD is the resolved sample SD',
               abs(a['sampleSD'] - 0.9574271077563381) < 1e-9, str(a['sampleSD']))
        expect('incomplete experiment has no fixed-sample interval',
               a['approximateFixedSample95Halfwidth'] is None)
        expect('largest resolved reading reported', a['max'] == 12)

        pair = report['paired'][0]
        expect('paired difference uses complete matched resolved pairs',
               pair['matched'] == 4 and pair['unresolved'] == 2, str(pair))
        expect('paired mean difference is known (3.0)', abs(pair['meanDelta'] - 3.0) < 1e-9)
        expect('unresolved pair marks the comparison incomplete', pair['incomplete'] is True)

        store = confirmation.RunStore(output / 'confirmation.sqlite')
        rows = store.rows()
        expect('unresolved never scored zero',
               all(row['earned'] is None for row in rows if row['status'] != 'resolved'))
        expect('failed run stored as failed', any(row['status'] == 'failed' for row in rows))
        expect('every candidate/ordinal stored exactly once',
               len(rows) == 12 and len({(r['candidate'], r['ordinal']) for r in rows}) == 12)
        expect('seed pairs persisted in the output database',
               [tuple(r) for r in store.db.execute('SELECT math,lib FROM seed ORDER BY ordinal')]
               == [tuple(p) for p in manifest['seeds']])
        store.close()

        print('resume and duplicate durability')
        store = confirmation.RunStore(output / 'confirmation.sqlite')
        with store.db:
            store.db.execute("DELETE FROM run WHERE candidate='A' AND ordinal>=4")
        jobs = list(confirmation.pending_jobs(manifest, store.completed(), SIMULATOR))
        store.close()
        expect('resume schedules only the missing pairs',
               [(job['candidate'], job['ordinal']) for job in jobs] == [('A', 4), ('A', 5)], str(jobs))
        confirmation.run_confirmation(library, ['A', 'B'], 6, 2, output, simulator=SIMULATOR,
                                      randbits=controller([]), executor_factory=thread_pool)
        store = confirmation.RunStore(output / 'confirmation.sqlite')
        rows = store.rows()
        expect('resume restores the missing rows without duplicating any',
               len(rows) == 12 and len({(r['candidate'], r['ordinal']) for r in rows}) == 12)
        expect('resume reuses the frozen seeds and manifest',
               json.loads((output / 'manifest.json').read_text(encoding='utf-8'))['digest']
               == manifest['digest'])
        store.close()

        print('mismatch rejection and read-only safety')
        for label, call in (
                ('changed run count', lambda: confirmation.run_confirmation(
                    library, ['A', 'B'], 8, 2, output, simulator=SIMULATOR,
                    executor_factory=thread_pool)),
                ('changed candidate set', lambda: confirmation.run_confirmation(
                    library, ['A'], 6, 2, output, simulator=SIMULATOR))):
            try:
                call()
                expect(label + ' rejected', False, 'no error')
            except ValueError:
                expect(label + ' rejected', True)

        library.unlink()
        make_library(library, {'A': {'base': 11}, 'B': {'base': 7}})
        try:
            confirmation.run_confirmation(library, ['A', 'B'], 6, 2, output,
                                          simulator=SIMULATOR, executor_factory=thread_pool)
            expect('changed scenario rejected', False, 'no error')
        except ValueError:
            expect('changed scenario rejected', True)

        orphan = with_fixture / 'orphan'
        orphan.mkdir()
        confirmation.RunStore(orphan / 'confirmation.sqlite').close()
        try:
            confirmation.run_confirmation(library, ['A'], 6, 2, orphan, simulator=SIMULATOR,
                                          executor_factory=thread_pool)
            expect('results without a manifest refused', False, 'no error')
        except ValueError:
            expect('results without a manifest refused', True)

        missing = with_fixture / 'absent.sqlite'
        try:
            confirmation.run_confirmation(missing, ['A'], 6, 2, with_fixture / 'newout',
                                          simulator=SIMULATOR, executor_factory=thread_pool)
            expect('missing library is an error', False, 'no error')
        except FileNotFoundError:
            expect('missing library is an error', True)
        expect('missing library is never created', not missing.exists())
    finally:
        shutil.rmtree(with_fixture, ignore_errors=True)

    if FAILURES:
        print(f'FAILURES ({len(FAILURES)}): ' + ', '.join(FAILURES[:8]))
        return 1
    print('  every confirmation invariant held')
    return 0


if __name__ == '__main__':
    sys.exit(main())
