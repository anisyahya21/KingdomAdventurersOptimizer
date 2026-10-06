"""Out-of-order worker completion must never fail the search (Part A).

Workers finish in whatever order the machine gives them, so the coordinator buffers a completion
until its preceding seed lands and then records the now-contiguous prefix in ascending order. This
harness drives the real `Optimizer` with a scripted pool whose futures complete in a chosen order and
compares the persisted rows against an in-order reference:

    x,x,x,x   identity        - the reference
    x,x,x,x   0,2,1,3         - one inversion
    x,x,x,x   3,2,1,0         - fully reversed
    x,x,x,x   0,4,2,1,3,...   - two gaps then backfill
    validation ordinals completing backwards

Every order must produce exactly ordinals 0..n-1 with no gap and no duplicate, the same per-ordinal
result digests as the reference, and no "Out-of-order completion" pause.

    python check_optimizer_out_of_order.py
"""
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import strategy_optimizer as optimizer  # noqa: E402
import strategy_optimizer_fast as fast  # noqa: E402
from strategy_optimizer_adapter import default_scenario, provenance, stats  # noqa: E402

ORDERS = {
    'in-order': lambda count: list(range(count)),
    'one-inversion': lambda count: ([0, 2, 1, 3] + list(range(4, count)))[:count],
    'reversed': lambda count: list(reversed(range(count))),
    'two-gaps': lambda count: ([0, 4, 2, 1, 3] + list(range(5, count)))[:count],
    'validation-reversed': lambda count: list(reversed(range(count))),
}


class ScriptedFuture:
    def __init__(self, function, scenario, seeds):
        self.function, self.scenario, self.seeds = function, scenario, seeds
        self._result, self._error, self._done = None, None, False

    def resolve(self):
        try:
            self._result = self.function(self.scenario, self.seeds)
        except Exception as exc:  # noqa: BLE001 - a failing worker is part of the contract
            self._error = exc
        self._done = True

    def done(self):
        return self._done

    def result(self):
        if self._error is not None:
            raise self._error
        return self._result


class ScriptedPool:
    """One scripted completion order per round of submitted jobs."""

    def __init__(self, workers, order):
        self.workers = workers
        self.order = order
        self.submitted = []
        self._round = []
        self._last_submit = 0.
        self._lock = threading.Condition()
        # A round smaller than the worker count (the feeder ran out of work) still has to complete,
        # otherwise the coordinator waits forever for a future nobody resolves.
        threading.Thread(target=self._flush_when_quiet, daemon=True).start()

    def submit(self, function, scenario, seeds):
        future = ScriptedFuture(function, scenario, seeds)
        with self._lock:
            self.submitted.append(future)
            self._round.append(future)
            self._last_submit = time.monotonic()
            if len(self._round) >= self.workers:
                batch, self._round = self._round, []
                threading.Thread(target=self._resolve, args=(batch,), daemon=True).start()
        return future

    def _flush_when_quiet(self):
        while True:
            time.sleep(.02)
            batch = []
            with self._lock:
                if self._round and time.monotonic()-self._last_submit > .05:
                    batch, self._round = self._round, []
            if batch:
                self._resolve(batch)

    def _resolve(self, batch):
        seen = set()
        for index in self.order(len(batch)):
            if index >= len(batch):
                continue
            seen.add(index)
            batch[index].resolve()
            time.sleep(.002)          # make the completion order observable to the coordinator
        # A scripted order only has to name the order, never to be a permutation of a *short* round:
        # `two-gaps` is [0, 4, 2, 1, 3, ...], so a final round of fewer than five jobs would leave a
        # position unnamed. That stranded future is what made this harness time out waiting for
        # "paused": the coordinator waits for the job it submitted, and only reports its own
        # 180-second worker timeout. Every submitted job is resolved exactly once - in the scripted
        # order first, then whatever the scripted order did not name.
        for index in range(len(batch)):
            if index not in seen:
                batch[index].resolve()
                time.sleep(.002)
        with self._lock:
            self._lock.notify_all()

    def shutdown(self, wait=True, cancel_futures=False):
        with self._lock:
            batch, self._round = self._round, []
        if batch:
            self._resolve(batch)

    def terminate_workers(self):
        self.shutdown()


def rows(store):
    out = {}
    for row in store.db.execute('SELECT candidate, phase, ordinal, result FROM run'):
        out[(row[0], row[1], row[2])] = json.loads(row[3]).get('digest')
    return out


def run_order(label, order, seed_library, phase='discovery'):
    """One library, one scripted completion order; returns its rows plus the meta it published."""
    with tempfile.TemporaryDirectory(prefix='ka-order-') as root:
        path = Path(root) / 'order.sqlite'
        if seed_library is not None:
            import shutil
            shutil.copyfile(seed_library, path)
        store = optimizer.Store(path, provenance())
        if seed_library is None:
            scenario = dict(default_scenario(), tickLimit=5, mathSeed=7, libSeed=8)
            with store.db:
                store.set('scope', optimizer.scope(scenario))
                store.add(scenario, 'Order fixture', 'supplied', stats(scenario))
        store.close()
        previous = fast.HeadlessPool
        fast.HeadlessPool = lambda workers: ScriptedPool(workers, order)
        try:
            live = optimizer.Optimizer(path)
        finally:
            fast.HeadlessPool = previous
        try:
            wait(lambda: live.status()['state'] != 'Opening library', 'library opened')
            live.command('start', dict(workers=8, duty=1), wait=True)
            wait(lambda: (live.status()['totalRuns'] or 0) >= 8, 'eight runs recorded', 120)
            time.sleep(.4)                      # let any buffered completion flush
            live.command('pause', {}, wait=True)
            wait(lambda: live.status()['state'] in ('Paused', 'Stopped'), 'paused')
            status = live.status()
        finally:
            live.command('close')
            live.thread.join(120)
        store = optimizer.Store(path, provenance())
        try:
            recorded = rows(store)
            meta = dict(totalRuns=store.get('totalRuns'),
                        lifetime=store.get('encounterLifetime'),
                        proposals=store.get('proposals'))
        finally:
            store.close()
        return dict(label=label, rows=recorded, error=status.get('error'), state=status.get('state'),
                    scheduler=status.get('scheduler'), meta=meta)


def wait(predicate, label, seconds=60):
    deadline = time.monotonic()+seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(.05)
    raise AssertionError(f'timed out: {label}')


def main():
    failures = []
    reference = None
    for label in ('in-order', 'one-inversion', 'reversed', 'two-gaps'):
        result = run_order(label, ORDERS[label], None)
        per_stream = {}
        for (cid, phase, ordinal), digest in result['rows'].items():
            per_stream.setdefault((cid, phase), {})[ordinal] = digest
        for (cid, phase), entries in per_stream.items():
            expected = list(range(len(entries)))
            if sorted(entries) != expected:
                failures.append(f'{label}: {phase} ordinals {sorted(entries)} are not contiguous')
        if result['error'] and 'Out-of-order' in str(result['error']):
            failures.append(f'{label}: stopped with {result["error"]}')
        if reference is None:
            reference = result['rows']
        else:
            shared = {key: value for key, value in reference.items() if key in result['rows']}
            mismatched = [key for key, value in shared.items() if result['rows'][key] != value]
            if mismatched:
                failures.append(f'{label}: {len(mismatched)} shared rows differ from the in-order '
                                f'reference, e.g. {mismatched[0]}')
        print(f'  {label:<20} rows={len(result["rows"]):>3} state={result["state"]} '
              f'error={result["error"]}')

    # Validation ordinals completing backwards, on top of a library whose discovery bank is done.
    with tempfile.TemporaryDirectory(prefix='ka-order-seed-') as root:
        path = Path(root) / 'seed.sqlite'
        store = optimizer.Store(path, provenance())
        scenario = dict(default_scenario(), tickLimit=5, mathSeed=7, libSeed=8)
        with store.db:
            store.set('scope', optimizer.scope(scenario))
            cid = store.add(scenario, 'Order fixture', 'supplied', stats(scenario))
            for ordinal in range(optimizer.DISCOVERY_RUNS):
                store.record(cid, 'discovery', ordinal,
                             dict(verdict=1, censored=False, ticks=5, prizeCallbacks=1,
                                  survivors=1, resourceUses=0, behavior=dict(attacks=1, heals=0,
                                                                              prizes=1),
                                  seeds=list(optimizer.seed_pair('discovery', ordinal)),
                                  digest=f'seed-{ordinal}', elapsedSeconds=.01))
        store.close()
        result = run_order('validation-reversed', ORDERS['validation-reversed'], path)
        validation = {ordinal: digest for (cid_key, phase, ordinal), digest in result['rows'].items()
                      if phase == 'validation'}
        if validation and sorted(validation) != list(range(len(validation))):
            failures.append(f'validation-reversed: ordinals {sorted(validation)} are not contiguous')
        if result['error'] and 'Out-of-order' in str(result['error']):
            failures.append(f'validation-reversed: stopped with {result["error"]}')
        print(f'  {"validation-reversed":<20} rows={len(result["rows"]):>3} validation={len(validation)} '
              f'state={result["state"]} error={result["error"]}')
        if not validation:
            print('    (no validation seeds were scheduled in the window; discovery order still checked)')

    print(f'orders checked: {len(ORDERS)}')
    if failures:
        print('FAILURES:')
        for row in failures[:10]:
            print('  ' + row)
        return 1
    print('  every completion order produced contiguous ordinals, no duplicates, the same per-ordinal '
          'results as the in-order reference, and no out-of-order pause')
    return 0


if __name__ == '__main__':
    sys.exit(main())
