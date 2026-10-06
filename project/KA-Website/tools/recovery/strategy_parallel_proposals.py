"""Bounded persistent local process pool for independent joint-proposal builds.

`strategy_joint_proposals.pool` prepares each candidate with `_build`, a pure, deterministic
function of a frozen parent context and one spec. Those builds are independent, so a bounded local
process pool can prepare them concurrently without changing any experimental semantics: the caller
re-assembles results in ORIGINAL spec order, so first-maximum-valid / dedup / sort / result fields
are byte-identical to the serial loop.

The transport is a long-lived child process per worker speaking a small length-prefixed pickle
protocol over anonymous stdin/stdout pipes (the same re-exec of this file runs the worker loop). It
deliberately does NOT use `multiprocessing`, whose Windows pool needs named pipes that some managed
sandboxes refuse; anonymous pipes are always available and the child is a plain `subprocess`, so
there is no import of the parent `__main__`.

Design limits, all measured on this host and configurable:

* the worker count is bounded by CPU headroom (about 20% left idle), by free RAM (a >=4 GiB free
  reserve and a <6 GiB prep ceiling), and by an explicit ``KA_PREP_WORKERS`` request;
* workers run at BELOW_NORMAL priority so they never outrank the coordinator or a live battle pool;
* the pool is created once and reused across rounds (never per proposal); ``shutdown`` closes it
  cleanly and an ``atexit`` hook is a backstop;
* failures are fail-closed: a worker exception propagates to the caller (no silent partial result,
  no automatic expensive retry) and poisons the pool so the next call rebuilds it;
* tiny workloads, an explicit opt-out, an unsupported host, or a caller-supplied non-default
  admission callback stay SERIAL - never a silent false "parallel" claim.
"""
from __future__ import annotations

import atexit
import importlib
import os
import pickle
import re
import struct
import subprocess
import sys
import threading
import time
import traceback
from collections import deque

VERSION = 'strategy-parallel-proposals-1'

#: The host-measured default and hard ceiling for pre-dispatch preparation workers.
DEFAULT_PREP_WORKERS = 8
MAX_PREP_WORKERS = 12
#: Below this many specs the pool round-trip costs more than it saves; stay serial.
MIN_SPECS_FOR_PARALLEL = 8
#: Conservative per-worker resident estimate and the bounds used to admit workers.
PER_WORKER_BYTES = 256 * 1024 * 1024
MIN_FREE_BYTES = 4 * 1024 ** 3
MAX_TOTAL_BYTES = 6 * 1024 ** 3
CPU_HEADROOM_FRACTION = 0.2

#: Asynchronous planning pool: one persistent bounded worker set shared by every encounter state.
PLANNING_VERSION = 'strategy-planning-pool-1'
#: Host-supplied hard ceiling on planning workers; the pool refuses to exceed it.
MAX_PLANNING_WORKERS = MAX_PREP_WORKERS
DEFAULT_PLANNING_WORKERS = 2
DEFAULT_MAX_PENDING = 64
DEFAULT_MAX_PENDING_BYTES = 32 * 1024 * 1024
_PLANNING_IDENT = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
_PLANNING_DOTTED = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$')

_LOCK = threading.Lock()
_POOL = None
_POOL_WORKERS = 0
_LAST_ERROR = None
_TOKEN = 0


def _free_bytes():
    """Best-effort available physical memory, or ``None`` when it cannot be measured."""
    try:
        import ctypes

        class _MemoryStatusEx(ctypes.Structure):
            _fields_ = [('dwLength', ctypes.c_ulong), ('dwMemoryLoad', ctypes.c_ulong),
                        ('ullTotalPhys', ctypes.c_ulonglong), ('ullAvailPhys', ctypes.c_ulonglong),
                        ('ullTotalPageFile', ctypes.c_ulonglong),
                        ('ullAvailPageFile', ctypes.c_ulonglong),
                        ('ullTotalVirtual', ctypes.c_ulonglong),
                        ('ullAvailVirtual', ctypes.c_ulonglong),
                        ('ullAvailExtendedVirtual', ctypes.c_ulonglong)]

        status = _MemoryStatusEx()
        status.dwLength = ctypes.sizeof(_MemoryStatusEx)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return int(status.ullAvailPhys)
    except Exception:  # noqa: BLE001 - a host without the Win32 call falls through
        pass
    try:
        return int(os.sysconf('SC_AVPHYS_PAGES') * os.sysconf('SC_PAGE_SIZE'))
    except Exception:  # noqa: BLE001
        return None


def plan(spec_count, *, requested=None):
    """Return ``(workers, reason)``. ``workers == 0`` means run SERIAL for the declared reason."""
    if spec_count < MIN_SPECS_FOR_PARALLEL:
        return 0, 'tiny-workload-serial'
    if os.environ.get('KA_PREP_PARALLEL', '').strip().lower() in ('0', 'false', 'no', 'off'):
        return 0, 'disabled-by-environment'
    try:
        requested = int(requested) if requested is not None else int(
            os.environ.get('KA_PREP_WORKERS', DEFAULT_PREP_WORKERS))
    except (TypeError, ValueError):
        requested = DEFAULT_PREP_WORKERS
    if requested <= 0:
        return 0, 'disabled-by-request'
    cpu = os.cpu_count() or 1
    if cpu < 2:
        return 0, 'single-cpu-host'
    cpu_cap = max(1, cpu - max(1, int(round(cpu * CPU_HEADROOM_FRACTION))))
    ram_cap = MAX_PREP_WORKERS
    free = _free_bytes()
    if free is not None:
        if free < MIN_FREE_BYTES:
            return 0, 'insufficient-free-memory'
        budget_cap = max(1, int(MAX_TOTAL_BYTES // PER_WORKER_BYTES))
        ram_cap = min(budget_cap, max(1, int((free - MIN_FREE_BYTES) // PER_WORKER_BYTES)))
    workers = max(0, min(requested, MAX_PREP_WORKERS, cpu_cap, ram_cap))
    if workers < 2:
        return 0, 'no-worker-headroom'
    return workers, 'bounded-parallel'


# -- worker process (also the __main__ re-exec entry point) ---------------------------------------


def _set_below_normal_priority():
    try:
        import ctypes

        handle = ctypes.windll.kernel32.GetCurrentProcess()
        #: BELOW_NORMAL_PRIORITY_CLASS - battle workers and the coordinator always outrank prep.
        ctypes.windll.kernel32.SetPriorityClass(handle, 0x00004000)
    except Exception:  # noqa: BLE001 - a host without the Win32 call keeps default priority
        pass


def _read_exact(stream, size):
    chunks = []
    remaining = size
    while remaining:
        data = stream.read(remaining)
        if not data:
            return None
        chunks.append(data)
        remaining -= len(data)
    return b''.join(chunks)


def _run_task_payload(payload):
    """Run one module-level picklable target and report value, timing, CPU and byte size."""
    request_id = payload.get('requestId')
    started = time.time()
    cpu_start = time.process_time()
    pid = os.getpid()
    try:
        module = importlib.import_module(payload['module'])
        func = getattr(module, payload['func'])
        args = tuple(payload.get('args') or ())
        kwargs = dict(payload.get('kwargs') or {})
        value = func(*args, **kwargs)
        finished = time.time()
        result_bytes = len(pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL))
        return {'ok': True, 'requestId': request_id, 'result': value, 'startedAt': started,
                'finishedAt': finished, 'cpuSeconds': time.process_time() - cpu_start, 'pid': pid,
                'resultBytes': result_bytes}
    except BaseException as exc:  # noqa: BLE001 - compact error + traceback handed to the parent
        finished = time.time()
        return {'ok': False, 'requestId': request_id, 'error': '%s: %s' % (type(exc).__name__, exc),
                'traceback': traceback.format_exc(), 'startedAt': started, 'finishedAt': finished,
                'cpuSeconds': time.process_time() - cpu_start, 'pid': pid, 'resultBytes': 0}


def _encode_reply(reply):
    """Pickle a worker reply, converting an unpicklable value into a compact error reply."""
    try:
        return pickle.dumps(reply, protocol=pickle.HIGHEST_PROTOCOL)
    except BaseException as exc:  # noqa: BLE001 - never drop a reply on an unpicklable value
        fallback = {'ok': False,
                    'requestId': reply.get('requestId') if isinstance(reply, dict) else None,
                    'error': 'ResultNotPicklable: %s: %s' % (type(exc).__name__, exc),
                    'traceback': traceback.format_exc(), 'resultBytes': 0}
        return pickle.dumps(fallback, protocol=pickle.HIGHEST_PROTOCOL)


def _worker_main():
    _set_below_normal_priority()
    stdin, stdout = sys.stdin.buffer, sys.stdout.buffer
    state = {'token': None, 'module': None, 'func': None, 'context': None}
    while True:
        header = _read_exact(stdin, 8)
        if header is None:
            return 0
        size = struct.unpack('>Q', header)[0]
        body = _read_exact(stdin, size)
        if body is None:
            return 0
        payload = pickle.loads(body)
        op = payload.get('op')
        if op == 'close':
            return 0
        if op == 'context':
            state.update(token=payload['token'], module=payload['module'],
                         func=payload['func'], context=payload['context'])
            reply = {'ok': True}
        elif op == 'build':
            try:
                if payload.get('token') != state['token']:
                    raise RuntimeError('prep worker context token mismatch')
                module = importlib.import_module(state['module'])
                builder = getattr(module, state['func'])
                reply = {'ok': True, 'result': builder(state['context'], payload['spec'])}
            except BaseException as exc:  # noqa: BLE001 - returned to the parent, never swallowed
                reply = {'ok': False, 'error': '%s: %s' % (type(exc).__name__, exc),
                         'traceback': traceback.format_exc()}
        elif op == 'task':
            reply = _run_task_payload(payload)
        else:
            reply = {'ok': False, 'error': 'unknown op %r' % (op,)}
        data = _encode_reply(reply)
        stdout.write(struct.pack('>Q', len(data)) + data)
        stdout.flush()


# -- parent-side worker handle --------------------------------------------------------------------


class _Worker:
    def __init__(self):
        env = dict(os.environ)
        paths = [path for path in sys.path if path]
        existing = env.get('PYTHONPATH')
        env['PYTHONPATH'] = os.pathsep.join(paths + ([existing] if existing else []))
        self.proc = subprocess.Popen(  # noqa: S603 - fixed interpreter + this file, no shell
            [sys.executable, '-B', '-X', 'utf8', os.path.abspath(__file__)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.token = None

    def _exchange(self, payload):
        data = pickle.dumps(payload, protocol=pickle.HIGHEST_PROTOCOL)
        try:
            self.proc.stdin.write(struct.pack('>Q', len(data)) + data)
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise RuntimeError('prep worker stdin closed: %s' % (exc,)) from exc
        header = _read_exact(self.proc.stdout, 8)
        if header is None:
            raise RuntimeError('prep worker exited unexpectedly (code %s)' % (self.proc.poll(),))
        size = struct.unpack('>Q', header)[0]
        body = _read_exact(self.proc.stdout, size)
        if body is None:
            raise RuntimeError('prep worker truncated a reply')
        return pickle.loads(body)

    def set_context(self, token, module, func, context):
        if self.token == token:
            return
        reply = self._exchange({'op': 'context', 'token': token, 'module': module, 'func': func,
                                'context': context})
        if not reply.get('ok'):
            raise RuntimeError(reply.get('error'))
        self.token = token

    def build(self, token, spec):
        reply = self._exchange({'op': 'build', 'token': token, 'spec': spec})
        if not reply.get('ok'):
            raise RuntimeError('prep worker build failed: %s' % (reply.get('error'),))
        return reply['result']

    def run_task(self, request_id, module_name, function_name, args, kwargs):
        """Send one async planning task and return the worker reply dict (blocking exchange)."""
        return self._exchange({'op': 'task', 'requestId': request_id, 'module': module_name,
                               'func': function_name, 'args': tuple(args),
                               'kwargs': dict(kwargs or {})})

    def close(self):
        # Closing input delivers EOF without waiting for a possibly stuck worker reply.
        for step in (lambda: self.proc.stdin.close(), lambda: self.proc.wait(timeout=5)):
            try:
                step()
            except Exception:  # noqa: BLE001
                try:
                    self.proc.kill()
                except Exception:  # noqa: BLE001
                    pass


class _PrepPool:
    """One persistent bounded set of child processes; a map is one parallel wave."""

    def __init__(self, workers):
        self.workers = workers
        self._workers = [_Worker() for _ in range(workers)]

    def map(self, module, func, context, specs):
        global _TOKEN
        _TOKEN += 1
        token = _TOKEN
        results = [None] * len(specs)
        errors = []
        lanes = [[] for _ in self._workers]
        for index, spec in enumerate(specs):
            lanes[index % self.workers].append((index, spec))

        def lane(worker, items):
            try:
                worker.set_context(token, module, func, context)
                for index, spec in items:
                    results[index] = worker.build(token, spec)
            except BaseException as exc:  # noqa: BLE001 - surfaced after the wave joins
                errors.append(exc)

        threads = [threading.Thread(target=lane, args=(worker, items), daemon=True)
                   for worker, items in zip(self._workers, lanes) if items]
        for thread in threads:
            thread.start()
        deadline = time.monotonic() + 60
        for thread in threads:
            thread.join(timeout=max(0, deadline - time.monotonic()))
        if any(thread.is_alive() for thread in threads):
            for worker in self._workers:
                if worker.proc.poll() is None:
                    worker.proc.kill()
            for thread in threads:
                thread.join(timeout=2)
            raise TimeoutError('proposal preparation exceeded 60 seconds; workers stopped, no automatic retry')
        if errors:
            raise errors[0]
        return results

    def close(self):
        for worker in self._workers:
            worker.close()
        self._workers = []


def _acquire_pool(workers):
    global _POOL, _POOL_WORKERS
    with _LOCK:
        if _POOL is not None and _POOL_WORKERS == workers:
            return _POOL
        _close_locked()
        _POOL = _PrepPool(workers)
        _POOL_WORKERS = workers
        return _POOL


def _close_locked():
    global _POOL, _POOL_WORKERS
    if _POOL is None:
        return
    pool = _POOL
    _POOL = None
    _POOL_WORKERS = 0
    try:
        pool.close()
    except Exception:  # noqa: BLE001
        pass


def _invalidate_pool():
    with _LOCK:
        _close_locked()


def iter_build_chunks(module_name, func_name, context, specs, *, workers=None, chunk_size=None):
    """Yield ``(chunk_specs, chunk_results)`` in ORIGINAL order on a persistent bounded pool.

    The caller processes each yielded chunk before requesting the next, so stopping at the first
    maximum-valid proposal sees exactly the prefix a serial loop would. Any worker error is re-raised
    (fail-closed) after the pool is discarded.
    """
    global _LAST_ERROR
    specs = list(specs)
    workers = workers or plan(len(specs))[0]
    if workers < 2:
        raise ValueError('iter_build_chunks requires a bounded parallel plan; got %r' % (workers,))
    pool = _acquire_pool(workers)
    size = max(1, int(chunk_size) if chunk_size else workers)
    for start in range(0, len(specs), size):
        chunk = specs[start:start + size]
        try:
            results = pool.map(module_name, func_name, context, chunk)
        except Exception as exc:  # noqa: BLE001 - fail closed, never a silent partial result
            _LAST_ERROR = '%s: %s' % (type(exc).__name__, exc)
            _invalidate_pool()
            raise
        yield chunk, results


def _completed_record(request, reply):
    """Detach a finished worker reply into a plain record with stable field names."""
    ok = bool(reply.get('ok'))
    return {
        'requestId': request['requestId'],
        'result': reply.get('result') if ok else None,
        'error': None if ok else (reply.get('error') or 'PlanningWorkerError'),
        'traceback': None if ok else reply.get('traceback'),
        'submittedAt': request['submittedAt'],
        'queuedMonotonicAt': request.get('queuedMonotonicAt'),
        'laneStartedMonotonicAt': request.get('laneStartedMonotonicAt'),
        'startedAt': reply.get('startedAt'),
        'finishedAt': reply.get('finishedAt'),
        'harvestedAt': None,
        'futureReadyAt': None,
        'futureReadyMonotonicAt': None,
        'hostNoticedAt': None,
        'hostNoticedMonotonicAt': None,
        'requestBytes': request['requestBytes'],
        'resultBytes': reply.get('resultBytes'),
        'workerCpuSeconds': reply.get('cpuSeconds'),
        'workerPid': reply.get('pid'),
    }


class PlanningPool:
    """One persistent bounded process pool for asynchronous planner work.

    ``submit`` enqueues a module-level picklable target and returns immediately: it never runs the
    target inline and never waits for completion (no silent synchronous fallback). ``harvest_ready``
    drains only already-finished requests as detached records; unfinished work stays queued.
    """

    def __init__(self, workers=DEFAULT_PLANNING_WORKERS, max_pending=DEFAULT_MAX_PENDING, *,
                 max_pending_bytes=DEFAULT_MAX_PENDING_BYTES,
                 hard_max_workers=MAX_PLANNING_WORKERS):
        workers = int(workers)
        hard_max_workers = int(hard_max_workers)
        if workers < 1:
            raise ValueError('PlanningPool requires at least one worker, got %r' % (workers,))
        if workers > hard_max_workers:
            raise ValueError('PlanningPool workers %d exceeds the host hard maximum %d'
                             % (workers, hard_max_workers))
        max_pending = int(max_pending)
        if max_pending < 1:
            raise ValueError('PlanningPool max_pending must be >= 1, got %r' % (max_pending,))
        self.workers = workers
        self.hard_max_workers = hard_max_workers
        self.max_pending = max_pending
        self.max_pending_bytes = int(max_pending_bytes)
        self._cond = threading.Condition()
        self._wlock = threading.Lock()
        self._requests = {}
        self._pending = deque()
        self._results = deque()
        self._pending_bytes = 0
        self._closed = False
        self._submitted = 0
        self._harvested = 0
        self._cancelled = 0
        self._errors = 0
        self._last_error = None
        self._lane_children = [None] * workers
        self._lane_threads = [threading.Thread(target=self._lane_loop, args=(index,),
                                               name='ka-planning-lane-%d' % index, daemon=True)
                              for index in range(workers)]
        for thread in self._lane_threads:
            thread.start()

    def submit(self, request_id, module_name, function_name, args=(), kwargs=None):
        """Queue one task and return at once. ``False`` means rejected, never run inline."""
        try:
            hash(request_id)
        except TypeError:
            return self._reject('unhashable-request-id %r' % (type(request_id).__name__,))
        if not isinstance(module_name, str) or not _PLANNING_DOTTED.match(module_name or ''):
            return self._reject('invalid-module-name %r' % (module_name,))
        if not isinstance(function_name, str) or not _PLANNING_IDENT.match(function_name or ''):
            return self._reject('invalid-function-name %r' % (function_name,))
        call_args = tuple(args)
        call_kwargs = dict(kwargs or {})
        payload = {'op': 'task', 'requestId': request_id, 'module': module_name,
                   'func': function_name, 'args': call_args, 'kwargs': call_kwargs}
        try:
            request_bytes = len(pickle.dumps(payload, protocol=pickle.HIGHEST_PROTOCOL))
        except BaseException as exc:  # noqa: BLE001 - an unpicklable payload is a caller error
            return self._reject('RequestNotPicklable: %s: %s' % (type(exc).__name__, exc))
        with self._cond:
            if self._closed:
                return self._reject('pool-closed')
            if request_id in self._requests:
                return self._reject('duplicate-request-id %r' % (request_id,))
            if len(self._requests) >= self.max_pending:
                return self._reject('pending-limit %d' % (self.max_pending,))
            if self._pending_bytes + request_bytes > self.max_pending_bytes:
                return self._reject('pending-bytes-limit %d' % (self.max_pending_bytes,))
            self._requests[request_id] = {
                'requestId': request_id, 'module': module_name, 'functionName': function_name,
                'args': call_args, 'kwargs': call_kwargs, 'requestBytes': request_bytes,
                'submittedAt': time.time(), 'queuedMonotonicAt': time.monotonic(),
                'state': 'pending',
            }
            self._pending.append(request_id)
            self._pending_bytes += request_bytes
            self._submitted += 1
            self._cond.notify()
            return True

    def _reject(self, reason):
        with self._cond:
            self._last_error = reason
        return False

    def harvest_ready(self):
        """Return finished requests as detached records; never blocks and never runs tasks."""
        now = time.time()
        monotonic_now = time.monotonic()
        out = []
        with self._cond:
            while self._results:
                record = dict(self._results.popleft())
                record['harvestedAt'] = now
                record['hostNoticedAt'] = now
                record['hostNoticedMonotonicAt'] = monotonic_now
                if record.get('error') is not None:
                    self._errors += 1
                self._harvested += 1
                out.append(record)
        return out

    def cancel(self, request_id):
        """Cancel a task that has not started yet. True only when it truly never ran."""
        with self._cond:
            request = self._requests.get(request_id)
            if request is None or request.get('state') != 'pending':
                return False
            try:
                self._pending.remove(request_id)
            except ValueError:
                return False
            self._pending_bytes -= request['requestBytes']
            self._requests.pop(request_id, None)
            self._cancelled += 1
            return True

    def cancel_unstarted(self):
        """Cancel every queued-but-unstarted task; returns the number cancelled."""
        with self._cond:
            count = 0
            for request_id in list(self._pending):
                request = self._requests.pop(request_id, None)
                if request is None:
                    continue
                self._pending_bytes -= request['requestBytes']
                count += 1
            self._pending.clear()
            self._cancelled += count
            return count

    def status(self):
        with self._cond:
            pending = len(self._pending)
            running = sum(1 for request in self._requests.values()
                          if request.get('state') == 'running')
            snapshot = dict(version=PLANNING_VERSION, closed=self._closed, workers=self.workers,
                            hardMaxWorkers=self.hard_max_workers, pending=pending,
                            running=running, outstanding=len(self._requests),
                            completedUnharvested=len(self._results), pendingBytes=self._pending_bytes,
                            maxPending=self.max_pending, maxPendingBytes=self.max_pending_bytes,
                            submitted=self._submitted, harvested=self._harvested,
                            cancelled=self._cancelled, errors=self._errors,
                            lastError=self._last_error)
        snapshot['aliveWorkerPids'] = self._alive_pids()
        return snapshot

    def close(self, timeout=5.0):
        """Stop accepting work, bound shutdown by ``timeout``, report surviving owned PIDs."""
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self._cond:
            already = self._closed
            if not already:
                self._closed = True
                cancelled = self._cancel_pending_locked()
                self._cond.notify_all()
            else:
                cancelled = 0
        if already:
            return dict(closed=True, alreadyClosed=True, cancelledPending=0,
                        remainingPids=self._alive_pids(), unharvestedResults=len(self._results))
        for thread in self._lane_threads:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            thread.join(timeout=remaining)
        stragglers = [index for index, thread in enumerate(self._lane_threads) if thread.is_alive()]
        for index in stragglers:
            worker = self._lane_snapshot(index)
            if worker is not None and worker.proc.poll() is None:
                try:
                    worker.proc.kill()
                except Exception:  # noqa: BLE001 - already gone
                    pass
        for index in stragglers:
            remaining = max(0.0, deadline - time.monotonic())
            self._lane_threads[index].join(timeout=remaining)
        remaining_pids = []
        for index in range(self.workers):
            worker = self._lane_snapshot(index)
            if worker is None:
                continue
            proc = worker.proc
            try:
                worker.close()
            except Exception:  # noqa: BLE001 - best-effort teardown
                pass
            if proc.poll() is None:
                remaining_pids.append(proc.pid)
            with self._wlock:
                self._lane_children[index] = None
        with self._cond:
            unharvested = len(self._results)
            outstanding = len(self._requests)
        return dict(closed=True, alreadyClosed=False, cancelledPending=cancelled,
                    remainingPids=remaining_pids, unharvestedResults=unharvested,
                    outstandingRequests=outstanding, workers=self.workers)

    @property
    def closed(self):
        with self._cond:
            return self._closed

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()

    def _cancel_pending_locked(self):
        count = 0
        for request_id in list(self._pending):
            request = self._requests.pop(request_id, None)
            if request is None:
                continue
            self._pending_bytes -= request['requestBytes']
            count += 1
        self._pending.clear()
        self._cancelled += count
        return count

    def _lane_snapshot(self, index):
        with self._wlock:
            return self._lane_children[index]

    def _alive_pids(self):
        with self._wlock:
            children = list(self._lane_children)
        return [worker.proc.pid for worker in children
                if worker is not None and worker.proc.poll() is None]

    def _lane_loop(self, index):
        while True:
            with self._cond:
                while True:
                    if self._closed:
                        return
                    if self._pending:
                        request_id = self._pending.popleft()
                        request = self._requests.get(request_id)
                        if request is None:
                            continue
                        request['state'] = 'running'
                        request['laneStartedMonotonicAt'] = time.monotonic()
                        self._pending_bytes -= request['requestBytes']
                        break
                    self._cond.wait()
            reply = self._execute(index, request)
            record = _completed_record(request, reply)
            record['futureReadyAt'] = time.time()
            record['futureReadyMonotonicAt'] = time.monotonic()
            with self._cond:
                self._requests.pop(request_id, None)
                self._results.append(record)
                self._cond.notify_all()

    def _execute(self, index, request):
        worker = None
        try:
            worker = self._lane_worker(index)
            reply = worker.run_task(request['requestId'], request['module'],
                                    request['functionName'], request['args'], request['kwargs'])
            if not isinstance(reply, dict):
                raise RuntimeError('planning worker returned a malformed reply')
            return reply
        except BaseException as exc:  # noqa: BLE001 - surface transport failures as records
            with self._wlock:
                if self._lane_children[index] is worker:
                    self._lane_children[index] = None
            if worker is not None:
                try:
                    worker.close()
                except Exception:  # noqa: BLE001
                    pass
            return {'ok': False,
                    'error': 'PlanningWorkerTransportError: %s: %s' % (type(exc).__name__, exc),
                    'traceback': traceback.format_exc(), 'resultBytes': 0,
                    'startedAt': request.get('submittedAt'), 'finishedAt': time.time(),
                    'cpuSeconds': None, 'pid': None}

    def _lane_worker(self, index):
        with self._wlock:
            worker = self._lane_children[index]
            if worker is None:
                worker = _Worker()
                self._lane_children[index] = worker
            return worker


_SHARED_PLANNING_POOL = None
_SHARED_PLANNING_LOCK = threading.Lock()


def get_planning_pool(workers=DEFAULT_PLANNING_WORKERS, max_pending=DEFAULT_MAX_PENDING, *,
                      max_pending_bytes=DEFAULT_MAX_PENDING_BYTES,
                      hard_max_workers=MAX_PLANNING_WORKERS):
    """Return the single application-owned planning pool, creating it once on first use."""
    global _SHARED_PLANNING_POOL
    with _SHARED_PLANNING_LOCK:
        pool = _SHARED_PLANNING_POOL
        if pool is None or pool.closed:
            pool = PlanningPool(workers=workers, max_pending=max_pending,
                                max_pending_bytes=max_pending_bytes,
                                hard_max_workers=hard_max_workers)
            _SHARED_PLANNING_POOL = pool
        return pool


def planning_pool_status():
    """Observer-only snapshot of the shared planning service; never a selection input."""
    with _SHARED_PLANNING_LOCK:
        pool = _SHARED_PLANNING_POOL
    if pool is None:
        return dict(version=PLANNING_VERSION, shared=True, alive=False, closed=True)
    snapshot = pool.status()
    snapshot['shared'] = True
    snapshot['alive'] = True
    return snapshot


def close_planning_pool(timeout=5.0):
    """Close and detach the shared planning pool (idempotent, bounded)."""
    global _SHARED_PLANNING_POOL
    with _SHARED_PLANNING_LOCK:
        pool = _SHARED_PLANNING_POOL
        _SHARED_PLANNING_POOL = None
    if pool is None:
        return dict(closed=True, alreadyClosed=True, cancelledPending=0, remainingPids=[])
    return pool.close(timeout=timeout)


def shutdown(wait=True):
    """Close the persistent pool cleanly (idempotent)."""
    with _LOCK:
        _close_locked()


def diagnostics():
    """Observer-only description of the pool; never a selection input."""
    with _LOCK:
        alive = _POOL is not None
        workers = _POOL_WORKERS
    return dict(version=VERSION, poolAlive=alive, poolWorkers=workers, lastError=_LAST_ERROR,
                cpuCount=os.cpu_count() or 1, freeBytes=_free_bytes(),
                minSpecs=MIN_SPECS_FOR_PARALLEL, maxWorkers=MAX_PREP_WORKERS,
                defaultWorkers=DEFAULT_PREP_WORKERS)


atexit.register(shutdown)
atexit.register(lambda: close_planning_pool())

if __name__ == '__main__':
    sys.exit(_worker_main())

__all__ = ['VERSION', 'DEFAULT_PREP_WORKERS', 'MAX_PREP_WORKERS', 'MIN_SPECS_FOR_PARALLEL',
           'plan', 'iter_build_chunks', 'shutdown', 'diagnostics',
           'PLANNING_VERSION', 'MAX_PLANNING_WORKERS', 'DEFAULT_PLANNING_WORKERS',
           'DEFAULT_MAX_PENDING', 'DEFAULT_MAX_PENDING_BYTES', 'PlanningPool',
           'get_planning_pool', 'planning_pool_status', 'close_planning_pool']
