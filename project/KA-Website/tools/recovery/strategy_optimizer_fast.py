"""Persistent, bounded headless workers for bulk simulation; no desktop or rendering imports."""
import json
import heapq
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

RUNTIME_NAME = 'pypy3.11-v7.3.23-win64'
MAX_LINE = 1024 * 1024
BATCH_MIN_PAIRS = 1
BATCH_MAX_PAIRS = 8

#: Opt-in battle-timing trace. Disabled unless this env var names a JSONL output path. Exactly one
#: process (the optimiser host) opens and writes the file; workers only return their own stage
#: durations inside the response payload and never touch a shared file.
TRACE_ENV = 'KA_OPTIMIZER_BATTLE_TRACE'
TRACE_SAMPLE_ENV = 'KA_OPTIMIZER_BATTLE_TRACE_SAMPLE'
#: v4 separates the launcher PID from the worker interpreter PID. Before v4 the parent relabelled
#: its own Popen PID (`process.pid`) as `workerProcessPid`, which the evaluator then published as
#: `host.workerPid`, so the canonical worker PID was the launcher rather than the interpreter that
#: actually ran `os.getpid()`. v4 makes the split explicit and is not readable as a v3 record.
TRACE_SCHEMA = 'ka-battle-trace/4'

#: Traced responses report the instant the JSON response *prefix* (everything before the trailing
#: flush token) has been written and flushed -- NOT the completion of the response, whose suffix is
#: only sent afterwards. The line is split so that measured instant can be carried inside the line it
#: reports: body up to this sentinel, the flush, then the measured token plus the JSON tail. The host
#: reads a single line either way; an untraced response keeps the original one-write path.
_PREFIX_FLUSH_SENTINEL = '@@ka-battle-trace-prefix-flush@@'
_PREFIX_FLUSH_TOKEN = '"' + _PREFIX_FLUSH_SENTINEL + '"'


class _TracedResult(dict):
    """A normal compact result dict plus ONE non-serialised timing attribute.

    It *is* a dict, so every existing consumer keeps working unchanged, and the attribute is never
    part of the JSON payload. That keeps timing metadata out of the persisted battle outcome and the
    experiment digest: the evaluator reads `katrace` and converts back to a plain dict before the
    ledger write.
    """
    katrace = None


class BattleTrace:
    """Single-writer JSONL sink for bounded, deterministic, opt-in battle timing samples."""

    def __init__(self, path, sample=1, clock=time.perf_counter):
        self.path = str(path)
        self.sample = max(1, int(sample))
        self._clock = clock
        self.origin = clock()
        self._seq = 0
        self._lock = threading.Lock()
        self._file = open(self.path, 'a', encoding='utf-8', buffering=1)

    def next_seq(self):
        """The deterministic submission ordinal used for sampling (each submitted battle takes one)."""
        with self._lock:
            seq = self._seq
            self._seq += 1
        return seq

    def wants(self, seq):
        return (int(seq) % self.sample) == 0

    def offset_ms(self, timestamp):
        """Convert a host monotonic timestamp to milliseconds from the sink origin."""
        if timestamp is None:
            return None
        return round((timestamp - self.origin) * 1000.0, 3)

    def write(self, record):
        payload = dict(record)
        payload.setdefault('schema', TRACE_SCHEMA)
        try:
            line = json.dumps(payload, separators=(',', ':'), ensure_ascii=False)
        except (TypeError, ValueError):
            return False
        with self._lock:
            try:
                self._file.write(line + '\n')
                self._file.flush()
            except OSError:
                return False
        return True

    def close(self):
        try:
            self._file.close()
        except OSError:
            pass


_TRACE_OPEN_LOCK = threading.Lock()
_TRACE_SINGLETON = None
_TRACE_KEY = None


def open_battle_trace():
    """The process-wide sink for the current (path, sample) pair, or None when tracing is disabled.

    One handle per path, shared under one lock, so every evaluator in the optimiser host appends to
    the same file as a single writer.
    """
    global _TRACE_SINGLETON, _TRACE_KEY
    path = (os.environ.get(TRACE_ENV) or '').strip()
    if not path:
        return None
    try:
        sample = int(os.environ.get(TRACE_SAMPLE_ENV, '1'))
    except (TypeError, ValueError):
        sample = 1
    sample = max(1, sample)
    key = (path, sample)
    with _TRACE_OPEN_LOCK:
        if _TRACE_SINGLETON is None or _TRACE_KEY != key:
            if _TRACE_SINGLETON is not None:
                _TRACE_SINGLETON.close()
            _TRACE_SINGLETON = BattleTrace(path, sample)
            _TRACE_KEY = key
        return _TRACE_SINGLETON


def reset_battle_trace():
    """Drop the cached sink; the next :func:open_battle_trace reopens from the environment."""
    global _TRACE_SINGLETON, _TRACE_KEY
    with _TRACE_OPEN_LOCK:
        if _TRACE_SINGLETON is not None:
            _TRACE_SINGLETON.close()
        _TRACE_SINGLETON = None
        _TRACE_KEY = None


def runtime_path():
    pypy = (Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) /
            'KingdomAdventurersOptimizer' / 'runtime' / RUNTIME_NAME / 'pypy3.exe')
    requested = os.environ.get('KA_OPTIMIZER_WORKER_RUNTIME', '').lower()
    if requested == 'pypy':
        return pypy
    cpython = Path(sys.executable)
    if cpython.name.lower() == 'pythonw.exe':
        cpython = cpython.with_name('python.exe')
    # The native kernel moved combat out of Python. On the live library, CPython 3.14 delivered
    # more completed battles than the old PyPy default on the same 12-worker fixture. Keep PyPy for
    # a Python-only engine or an explicit comparison/recovery request.
    if requested == 'cpython' or (requested != 'pypy' and cpython.is_file()
                                  and _native_kernel_available()):
        return cpython
    return pypy


def _native_kernel_available():
    try:
        from ka_abi import DLL
        return DLL.is_file()
    except ImportError:
        return False


class HeadlessPool:
    """One pipe and one outstanding battle per persistent interpreter."""
    def __init__(self, max_workers, *, telemetry=False, trace=False):
        self.telemetry = bool(telemetry)
        self.trace = bool(trace)
        from strategy_optimizer_adapter import provenance
        self.digest = provenance()['digest']
        self.processes = []
        self.available = queue.Queue()
        # Pool-owned counters so callers can read real queue/execute state without touching the
        # private executor's internals. queued is submitted-but-not-yet-holding-a-worker and
        # executing is actually running a battle on a worker process.
        self._counters = {'submitted': 0, 'started': 0, 'finished': 0,
                          'executing': 0, 'cooling': 0}
        self._counter_lock = threading.Lock()
        self._duty = 1.0
        self._cooldown_condition = threading.Condition()
        self._cooldown_heap = []
        self._cooldown_sequence = 0
        self._cooldown_stopping = False
        self._cooldown_thread = threading.Thread(
            target=self._cooldown_loop, name='ka-worker-duty-gate', daemon=True)
        self._cooldown_thread.start()
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        try:
            for _ in range(max_workers):
                process = subprocess.Popen([str(runtime_path()), '-u', str(Path(__file__).resolve()), '--worker'],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    text=True, encoding='utf-8', bufsize=1,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                self.processes.append(process)
                self.available.put(process)
        except BaseException:
            self.terminate_workers()
            raise

    #: The pool's `submit` accepts a per-job `trace` flag. The evaluator only sets it for the
    #: deterministically sampled jobs, so an unsampled request keeps the normal wire payload.
    supports_job_trace = True
    # ReadyQueueEvaluator admits beyond the process count only for pools that provide this
    # per-interpreter duty gate.
    supports_ready_window = True

    def submit(self, function, scenario, seeds, trace=False):
        # The same interface as the existing executor; only compact battle jobs belong here.
        self._note('submitted')
        dispatch_state = {'startedAt': None}
        future = self.executor.submit(self._run, scenario, seeds, trace, dispatch_state)
        # Scheduler-only timing for distinguishing ready-queue wait from an active battle timeout.
        future.ka_dispatch_state = dispatch_state
        return future

    def _note(self, name):
        with self._counter_lock:
            self._counters[name] += 1

    def _adjust(self, name, amount):
        with self._counter_lock:
            self._counters[name] += amount

    def snapshot(self):
        """Real instantaneous pool state: queued, executing and idle persistent workers."""
        with self._counter_lock:
            counters = dict(self._counters)
        return {'submitted': counters['submitted'], 'started': counters['started'],
                'finished': counters['finished'],
                'queued': max(0, counters['submitted'] - counters['started']),
                'executing': counters['executing'], 'cooling': counters['cooling'],
                'available': self.available.qsize()}

    def configure_duty(self, duty):
        """Set a duty fraction enforced by each actual persistent worker interpreter."""
        try:
            value = float(duty)
        except (TypeError, ValueError):
            value = 1.0
        self._duty = max(0.05, min(1.0, value))
        return self._duty

    def _return_process(self, process, busy_seconds):
        hold = max(0.0, float(busy_seconds)) * (1.0 / self._duty - 1.0)
        if hold > 0:
            self._adjust('cooling', 1)
            with self._cooldown_condition:
                self._cooldown_sequence += 1
                heapq.heappush(self._cooldown_heap,
                               (time.perf_counter() + hold, self._cooldown_sequence, process))
                self._cooldown_condition.notify()
        else:
            self.available.put(process)

    def _cooldown_loop(self):
        """Return cooled workers with one timer thread, leaving result Futures promptly complete."""
        while True:
            with self._cooldown_condition:
                while not self._cooldown_heap and not self._cooldown_stopping:
                    self._cooldown_condition.wait()
                if self._cooldown_stopping and not self._cooldown_heap:
                    return
                ready_at, _sequence, process = self._cooldown_heap[0]
                remaining = ready_at - time.perf_counter()
                if remaining > 0:
                    self._cooldown_condition.wait(remaining)
                    continue
                heapq.heappop(self._cooldown_heap)
            self._adjust('cooling', -1)
            self.available.put(process)

    def _run(self, scenario, seeds, trace=False, dispatch_state=None):
        process = self.available.get()
        if isinstance(dispatch_state, dict):
            dispatch_state['startedAt'] = time.perf_counter()
        self._note('started')
        self._adjust('executing', 1)
        busy_started = time.perf_counter()
        duty_work_seconds = None
        trace = {} if (self.trace and trace) else None
        try:
            fields = dict(scenario=scenario, seeds=seeds, provenance=self.digest,
                          telemetry=self.telemetry)
            if trace is not None:
                fields['trace'] = True
            request = json.dumps(fields, separators=(',', ':'))
            request_bytes = len(request.encode('utf-8'))
            if request_bytes >= MAX_LINE:
                raise ValueError('Headless request exceeds the bounded pipe payload')
            if trace is not None:
                trace['requestBytes'] = request_bytes
                trace['scenarioBytes'] = len(
                    json.dumps(scenario, separators=(',', ':')).encode('utf-8'))
                trace['pipeWriteAt'] = time.perf_counter()
            process.stdin.write(request+'\n')
            process.stdin.flush()
            line = process.stdout.readline(MAX_LINE)
            if trace is not None:
                trace['pipeReceiveAt'] = time.perf_counter()
                trace['pipeWaitSeconds'] = trace['pipeReceiveAt'] - trace['pipeWriteAt']
            if not line or not line.endswith('\n'):
                raise RuntimeError('Headless worker exited or exceeded the response limit; unfinished seed is safe to retry')
            response = json.loads(line)
            if trace is not None:
                # Parent-side boundary: the worker thread has read AND parsed the response, so the
                # Future is (about to be) available. Distinct from `pipeReceiveAt` (line read) and
                # from the parent `harvestBegin`.
                trace['futureAvailableAt'] = time.perf_counter()
            if 'error' in response:
                raise RuntimeError(response['error'])
            result = response['result']
            reported_elapsed = result.get('elapsedSeconds') if isinstance(result, dict) else None
            if isinstance(reported_elapsed, (int, float)) and not isinstance(reported_elapsed, bool):
                duty_work_seconds = max(0.0, float(reported_elapsed))
            if trace is not None:
                worker = response.get('trace')
                if isinstance(worker, dict):
                    trace.update(worker)
                # `process.pid` is the PID the parent spawned (the pool launcher). It is NOT the
                # worker interpreter PID: the worker reports its own `os.getpid()` as `workerPid`
                # (and `workerProcessPid`). Keep the two apart so the canonical worker PID stays the
                # interpreter that actually ran the battle, and the launcher is named explicitly.
                trace['workerLauncherPid'] = process.pid
                traced = _TracedResult(result)
                traced.katrace = trace
                return traced
            return result
        finally:
            busy_seconds = (duty_work_seconds if duty_work_seconds is not None
                            else time.perf_counter() - busy_started)
            self._adjust('executing', -1)
            self._return_process(process, busy_seconds)
            self._note('finished')

    def submit_batch(self, function, scenario, seed_pairs):
        """Queue one bounded batch of 1..8 seed pairs on this pool's persistent workers.

        Mirrors `submit`: the same persistent process, the same provenance pinning, the same bounded
        payload. The returned Future resolves to a list of ordinary compact result dicts in the order
        the pairs were given, each carrying its own `elapsedSeconds` and the worker's
        `executionBackend` and `accelerators`. The worker reuses one scenario/template cache across
        the whole batch, so a pair is never re-prepared between seeds.
        """
        pairs = [list(pair) if isinstance(pair, (list, tuple)) else pair for pair in seed_pairs]
        self._note('submitted')
        dispatch_state = {'startedAt': None}
        future = self.executor.submit(self._run_batch, scenario, pairs, dispatch_state)
        future.ka_dispatch_state = dispatch_state
        return future

    def _run_batch(self, scenario, seed_pairs, dispatch_state=None):
        count = len(seed_pairs)
        if not BATCH_MIN_PAIRS <= count <= BATCH_MAX_PAIRS:
            raise ValueError(f'Headless batch requires {BATCH_MIN_PAIRS}..{BATCH_MAX_PAIRS} seed '
                             f'pairs per request, got {count}')
        process = self.available.get()
        if isinstance(dispatch_state, dict):
            dispatch_state['startedAt'] = time.perf_counter()
        self._note('started')
        self._adjust('executing', 1)
        busy_started = time.perf_counter()
        duty_work_seconds = None
        try:
            request = json.dumps(dict(scenario=scenario, seedPairs=seed_pairs, provenance=self.digest, telemetry=self.telemetry),
                                 separators=(',', ':'))
            if len(request.encode('utf-8')) >= MAX_LINE:
                raise ValueError('Headless request exceeds the bounded pipe payload')
            process.stdin.write(request+'\n')
            process.stdin.flush()
            line = process.stdout.readline(MAX_LINE)
            if not line or not line.endswith('\n'):
                raise RuntimeError('Headless worker exited or exceeded the response limit; '
                                   'uncommitted seeds are safe to retry')
            response = json.loads(line)
            if 'error' in response:
                raise RuntimeError(response['error'])
            results = response.get('results')
            # A lost, extra or mistyped entry must fail loudly: the coordinator retries uncommitted
            # deterministic seeds rather than accepting a short or misordered batch.
            if not isinstance(results, list):
                raise RuntimeError('Headless worker returned a non-list batch response; '
                                   'uncommitted seeds are safe to retry')
            if len(results) != count:
                raise RuntimeError(f'Headless worker returned {len(results)} results for {count} seed '
                                   'pairs; uncommitted seeds are safe to retry')
            if not all(isinstance(entry, dict) for entry in results):
                raise RuntimeError('Headless worker returned a non-dict batch entry; '
                                   'uncommitted seeds are safe to retry')
            if any(entry.get('seeds') != pair for entry, pair in zip(results, seed_pairs)):
                raise RuntimeError('Headless worker returned mismatched batch seeds; '
                                   'uncommitted seeds are safe to retry')
            duty_work_seconds = sum(
                max(0.0, float(entry.get('elapsedSeconds') or 0.0)) for entry in results)
            return results
        finally:
            busy_seconds = (duty_work_seconds if duty_work_seconds is not None
                            else time.perf_counter() - busy_started)
            self._adjust('executing', -1)
            self._return_process(process, busy_seconds)
            self._note('finished')

    def shutdown(self, wait=True, cancel_futures=False):
        self.executor.shutdown(wait=wait, cancel_futures=cancel_futures)
        if wait:
            with self._cooldown_condition:
                # The executor is drained, so no queued request can consume a cooling worker.
                # Return those interpreter handles immediately; shutdown must not wait for the last
                # battle's duty delay when there is no next battle to run.
                while self._cooldown_heap:
                    _ready_at, _sequence, process = heapq.heappop(self._cooldown_heap)
                    self._adjust('cooling', -1)
                    self.available.put(process)
                self._cooldown_stopping = True
                self._cooldown_condition.notify_all()
            self._cooldown_thread.join(timeout=5)
        for process in self.processes:
            try:
                process.stdin.close()
            except OSError:
                # A killed child can leave a buffered write whose close/flush raises.
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            process.stdout.close()

    def terminate_workers(self):
        for process in self.processes:
            if process.poll() is None:
                process.kill()
        self.shutdown(wait=True, cancel_futures=True)


def _seed_pair_results(simulate, perf_counter, scenario, seed_pairs, backend, accelerated):
    """Run each seed pair through the same `simulate` call, in order, on one persistent worker.

    The worker's process-wide scenario/template cache is reused across the pairs on purpose: the
    pre-battle template is seed-independent, so reuse is exactly the scalar path. One pair raising
    fails the whole batch with the seed named and the original exception type and text preserved; no
    partial outcome is invented, and the coordinator retries the uncommitted deterministic seeds.
    """
    from time import process_time
    results = []
    for index, pair in enumerate(seed_pairs):
        started = perf_counter()
        cpu_started = process_time()
        try:
            result = simulate(scenario, pair)
        except Exception as exc:
            raise RuntimeError(
                f'seedPairs[{index}]={pair!r} failed: {type(exc).__name__}: {exc}') from exc
        result['elapsedSeconds'] = perf_counter()-started
        result['cpuSeconds'] = process_time()-cpu_started
        result['executionBackend'] = backend
        result['accelerators'] = accelerated
        results.append(result)
    return results


def serve(stdin, stdout, *, digest, simulate, accelerated, backend, clock=None):
    """The worker request loop, factored out of :func:`main` so its receive/parse boundary can be
    exercised with injected streams and clock.

    `received` is stamped immediately AFTER the blocking read returns and before any parsing, so the
    previous idle wait is never charged to this job's parse/prep/simulation. The previous
    response-written boundary, when known for this worker, yields the inter-job idle duration.

    A traced request additionally returns absolute perf_counter stamps (receive/parse/simulation/
    native/convert/result-ready/serialize/response-prefix-flush) plus the original durations; the
    strict native bracket is filled by the native backend around ONLY `lib.ka_run_battle`. An
    untraced request keeps the original one-write wire path with no trace timestamps or extra
    serialization.
    """
    from time import process_time
    clock = clock or time.perf_counter
    last_response_at = None
    while True:
        wait_began = clock()
        line = stdin.readline(MAX_LINE)
        received = clock()
        if not line:
            break
        if not line.endswith('\n'):
            raise ValueError('Oversized worker request')
        timing = None
        result = None
        scalar_started = None
        try:
            parse_began = clock()
            request = json.loads(line)
            parsed = clock()
            if request['provenance'] != digest:
                raise ValueError('Worker combat source differs from the library coordinator')
            evaluator = simulate
            if request.get('telemetry'):
                from functools import partial
                evaluator = partial(simulate, telemetry=True)
            if request.get('trace'):
                # Worker stages are returned only inside this response trace block; the worker
                # never writes a file, so the host stays the single writer. Absolute `*At` stamps
                # use the same perf_counter basis as the host (system-wide on Windows), so the host
                # can order worker receive/parse/sim/native/convert against its own stages.
                from functools import partial
                # `workerPid` / `workerProcessPid` are both THIS interpreter's PID (`os.getpid()`,
                # echoed as the worker's own process identity, never a launcher's). The launching
                # parent adds `workerLauncherPid` when it merges this block, so the two roles never
                # share a name.
                timing = dict(workerPid=os.getpid(), workerProcessPid=os.getpid(),
                              parseSeconds=parsed - parse_began,
                              waitSeconds=received - wait_began,
                              receiveAt=received, parseBeginAt=parse_began, parseEndAt=parsed)
                if last_response_at is not None:
                    timing['idleSeconds'] = received - last_response_at
                evaluator = partial(evaluator, timing=timing)
            if 'seedPairs' in request:
                if (not isinstance(request['seedPairs'], list)
                        or not BATCH_MIN_PAIRS <= len(request['seedPairs']) <= BATCH_MAX_PAIRS):
                    raise ValueError('Headless worker requires 1..8 seed pairs')
                response = dict(results=_seed_pair_results(
                    evaluator, clock, request['scenario'], request['seedPairs'],
                    backend, accelerated))
            else:
                scalar_started = clock()
                cpu_started = process_time()
                result = evaluator(request['scenario'], request['seeds'])
                result['elapsedSeconds'] = clock()-scalar_started
                result['cpuSeconds'] = process_time()-cpu_started
                result['executionBackend'] = backend
                result['accelerators'] = accelerated
                response = dict(result=result)
            if timing is not None:
                ready = clock()
                if scalar_started is not None:
                    timing['simulationBeginAt'] = scalar_started
                    timing['resultReadyAt'] = ready
                    timing['simulateSeconds'] = ready - scalar_started
                else:
                    timing['simulateSeconds'] = ready - received - timing.get('parseSeconds', 0.0)
                timing['totalSeconds'] = ready - received
                timing['cpuSeconds'] = (result.get('cpuSeconds')
                                        if isinstance(result, dict) else None)
                # The CPU reading brackets only the evaluator/simulation call (the native kernel or
                # the canonical Python engine). Parse and response serialization are excluded.
                timing['cpuSecondsScope'] = ('worker process_time over the simulation/evaluator '
                                             'call only; excludes request parse and response '
                                             'serialization')
                timing.setdefault('nativePath', None)
                response['trace'] = timing
        except Exception as exc:
            response = dict(error=f'{type(exc).__name__}: {exc}')
        serialize_began = clock()
        payload = json.dumps(response, separators=(',', ':'))
        serialize_ended = clock()
        traced = timing is not None and isinstance(response.get('trace'), dict)
        if traced:
            # Second, tiny encode so the measured serialization duration is itself in the record
            # (opt-in only; disabled tracing keeps the single original encode).
            response['trace']['serializeSeconds'] = serialize_ended - serialize_began
            response['trace']['serializeBeginAt'] = serialize_began
            response['trace']['serializeEndAt'] = serialize_ended
            # `responsePrefixFlushAt` is stamped AFTER the JSON response *prefix* has been written and
            # flushed and BEFORE the remaining suffix is written: it is NOT the completion of the
            # response. The instant cannot be known before the line that carries it is flushed, so
            # emit the body up to this sentinel, flush, then append the measured token + tail. The
            # host reads one complete line either way. Untraced responses keep one write + flush.
            response['trace']['responsePrefixFlushAt'] = _PREFIX_FLUSH_SENTINEL
            payload = json.dumps(response, separators=(',', ':'))
        if traced and payload.count(_PREFIX_FLUSH_TOKEN) == 1:
            head, _, tail = payload.partition(_PREFIX_FLUSH_TOKEN)
            stdout.write(head)
            stdout.flush()
            prefix_flushed_at = clock()
            response['trace']['responsePrefixFlushAt'] = prefix_flushed_at
            stdout.write(json.dumps(prefix_flushed_at, separators=(',', ':')) + tail + '\n')
            stdout.flush()
        else:
            if traced:
                # Sentinel could not be located exactly once (never expected): keep the field but
                # mark the prefix-flush boundary unknown rather than serialising the sentinel.
                response['trace']['responsePrefixFlushAt'] = None
                payload = json.dumps(response, separators=(',', ':'))
            stdout.write(payload + '\n')
            stdout.flush()
        last_response_at = clock()


def main():
    from strategy_optimizer_limits import initialize_worker
    initialize_worker()
    from strategy_optimizer_adapter import provenance, simulate
    digest = provenance()['digest']
    # Exact loop/setup acceleration is enabled only when a recorded parity report and this engine
    # snapshot agree; otherwise the worker runs the canonical path unchanged. Either way the
    # request/response contract, the adapter and the stored digest are identical.
    import strategy_optimizer_backend as acceleration
    active = acceleration.install(acceleration.identity())
    accelerated = sorted(module.__name__ for module in active)
    backend = (f'{sys.implementation.name} {sys.version_info.major}.{sys.version_info.minor} '
               '/ headless native worker')
    serve(sys.stdin, sys.stdout, digest=digest, simulate=simulate, accelerated=accelerated,
          backend=backend)


if __name__ == '__main__':
    main()
