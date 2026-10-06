"""Coalesced read-only status projection, separate from simulation scheduling."""
import json
import pickle
import os
from pathlib import Path
import struct
import subprocess
import sys
import threading
import time


INPUTS = ('path', 'provenance', 'acceleration', 'encounters', '_workers', '_duty',
          '_session_runs_base', '_focus_encounter', '_planner_exploit', '_planner_record',
          '_planner_portfolio', '_planner_portfolio_banks', '_planner_limits',
          '_auto_tune_state', '_last_probe', '_last_finetune', '_scheduler',
          '_rate_samples', '_engine_samples', '_encounter_report',
          '_encounter_session_base', '_encounter_rate_samples', '_encounter_ledger',
          # The live campaign status and the multi-select focus must render in the projector too;
          # without this the async Running snapshot carried a null campaign and `status()` fell back
          # to the read-only bootstrap every poll, so the projected status never matched the direct.
          '_campaign_report')


class ProjectedView(dict):
    """Mapping-compatible published view with its already encoded browser representation."""
    def __init__(self, value=(), wire_json=None):
        super().__init__(value)
        self.wire_json = wire_json


def detached(value):
    """Copy internal built-in data, never deserialise external pickle input."""
    return pickle.loads(pickle.dumps(value, protocol=5))


def capture(owner):
    inputs = {name: getattr(owner, name, None) for name in INPUTS}
    # The projection renderer is an Optimizer.__new__(Optimizer), not the live
    # planner subclass. Capture role allocation and evaluator telemetry while we
    # still have the real host object; only detached data crosses the pipe.
    try:
        allocation_reader = getattr(owner, '_worker_allocation_status', None)
        inputs['_projection_worker_allocation'] = (
            allocation_reader() if callable(allocation_reader) else None)
    except Exception:  # telemetry must not block status publication
        inputs['_projection_worker_allocation'] = None
    try:
        evaluator = getattr(owner, '_community_evaluator', None)
        if evaluator is None:
            encounter = getattr(owner, '_encounter', None)
            evaluator = getattr(encounter, 'evaluator', None) if encounter is not None else None
        status_reader = getattr(evaluator, 'status', None)
        queue_status = status_reader() if callable(status_reader) else None
        if isinstance(queue_status, dict):
            queue_status = {key: queue_status.get(key) for key in (
                'workers', 'inflight', 'readyQueueDepth', 'executingWorkers',
                'coolingWorkers', 'availableWorkers', 'completedUnharvested',
                'windowCapacity', 'admissionCapacity', 'readyWindow')}
        else:
            queue_status = None
        inputs['_projection_execution_queue'] = queue_status
    except Exception:  # telemetry must not block status publication
        inputs['_projection_execution_queue'] = None
    return detached(inputs)


class LatestPublication:
    """One active render and at most one waiting request; newer requests replace waiting ones.

    No writer connection, mutable planner state, or render cache crosses thread ownership.
    Epoch checks at delivery prevent a late Running view from overwriting Pause or a new focus.
    """
    def __init__(self, render, deliver, failed, close=lambda: None):
        self.render, self.deliver, self.failed, self.close_render = render, deliver, failed, close
        self.condition = threading.Condition()
        self.waiting = None
        self.stopping = False
        self.thread = threading.Thread(target=self._loop, name='optimizer-status', daemon=False)
        self.thread.start()

    def submit(self, epoch, inputs):
        with self.condition:
            if self.stopping:
                raise RuntimeError('Status publisher is closed')
            self.waiting = (epoch, inputs)
            self.condition.notify()

    def close(self):
        with self.condition:
            self.stopping = True
            self.waiting = None
            self.condition.notify()
        self.thread.join(timeout=10)
        if self.thread.is_alive():
            abort = getattr(self.render, 'abort', None)
            if abort is not None:
                abort()
            self.thread.join(timeout=5)
            if self.thread.is_alive():
                raise RuntimeError('Status publisher did not shut down')

    def _loop(self):
        try:
            while True:
                with self.condition:
                    self.condition.wait_for(lambda: self.stopping or self.waiting is not None)
                    if self.stopping:
                        return
                    epoch, inputs = self.waiting
                    self.waiting = None
                started = time.monotonic()
                try:
                    view = self.render(inputs)
                    # The renderer retains mutable caches. Published values must be independent.
                    if not getattr(self.render, 'detached_result', False):
                        view = detached(view)
                    self.deliver(epoch, view, time.monotonic()-started)
                except Exception as exc:
                    self.failed(epoch, str(exc))
        finally:
            self.close_render()


class StatusProjection:
    """A private Optimizer-shaped renderer and SQLite reader, owned by one background thread."""
    def __init__(self):
        self.store = self.renderer = None
        self.ledger_seed_packet = None
        self.ledger_seed_status = dict(state='fallback_full_fold', reason='seed_not_offered', packetBytes=0)
        self.ledger_seed_consumed = False

    def __call__(self, inputs):
        from strategy_optimizer import Optimizer
        from strategy_snapshot_store import SnapshotStore
        if self.renderer is None:
            self.renderer = Optimizer.__new__(Optimizer)
            self.renderer.lock = threading.Lock()
            self.renderer._candidate_cache = {}
            self.renderer._library_encounters = []
        if self.store is None:
            self.store = SnapshotStore(inputs['path'], inputs['provenance'])
        for name, value in inputs.items():
            setattr(self.renderer, name, value)
        try:
            self.store.begin_snapshot()
            if not self.ledger_seed_consumed:
                self.ledger_seed_consumed = True
                if isinstance(self.ledger_seed_packet, bytes):
                    from strategy_encounter_overview import LedgerCache
                    packet = self.ledger_seed_packet
                    offered = dict(self.ledger_seed_status or {})
                    self.ledger_seed_packet = None
                    cache, self.ledger_seed_status = LedgerCache.from_seed_packet(
                        packet, self.store.db, inputs['path'],
                        inputs.get('provenance'), MAX_PACKET - 64 * 1024)
                    if offered.get('exportSeconds') is not None:
                        self.ledger_seed_status['exportSeconds'] = offered['exportSeconds']
                    if cache is not None:
                        self.renderer._encounter_overview_cache = cache
                self.renderer._projection_cache_seed = dict(self.ledger_seed_status)
            self.renderer._publish_sync(self.store, 'Running', detach=False)
            view = self.renderer.snapshot
            wire_json = json.dumps(view, separators=(',', ':'), ensure_ascii=False,
                                   allow_nan=False).encode('utf-8')
            return ProjectedView(view, wire_json)
        finally:
            self.store.end_snapshot()

    def close(self):
        if self.store is not None:
            self.store.close()


MAX_PACKET = 256 * 1024 * 1024


def read_packet(stream):
    header = stream.read(4)
    if not header:
        raise EOFError('Status process closed its pipe')
    if len(header) != 4:
        raise RuntimeError('Truncated status header')
    size = struct.unpack('<I', header)[0]
    if size > MAX_PACKET:
        raise RuntimeError('Status packet exceeds the bounded pipe payload')
    chunks, remaining = [], size
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            raise EOFError('Truncated status packet')
        chunks.append(chunk)
        remaining -= len(chunk)
    # Both pipe endpoints are our own local processes. No external pickle data is accepted.
    return pickle.loads(b''.join(chunks))


def write_packet(stream, value):
    payload = pickle.dumps(value, protocol=5)
    if len(payload) > MAX_PACKET:
        raise RuntimeError('Status packet exceeds the bounded pipe payload')
    stream.write(struct.pack('<I', len(payload)))
    stream.write(payload)
    stream.flush()


class ProcessProjection:
    """Persistent projection process: expensive Python view code does not hold the scheduler GIL."""
    detached_result = True

    def __init__(self):
        self.process = None
        self._ledger_seed_packet = None
        self._ledger_seed_offer_pending = False
        self._ledger_seed_status = dict(state='fallback_full_fold', reason='seed_not_offered', packetBytes=0)

    def offer_ledger_seed(self, packet, diagnostic):
        """Offer one detached cache packet before the first projection request."""
        if self.process is not None or self._ledger_seed_offer_pending:
            return False
        self._ledger_seed_status = dict(diagnostic or {})
        if isinstance(packet, bytes) and len(packet) > MAX_PACKET - 64 * 1024:
            packet = None
            self._ledger_seed_status.update(state='fallback_full_fold', reason='packet_limit')
        self._ledger_seed_packet = packet
        self._ledger_seed_offer_pending = True
        return True

    def __call__(self, inputs):
        if self.process is not None and self.process.poll() is not None:
            self.close()
            self.process = None
        if self.process is None:
            runtime = Path(sys.executable)
            if runtime.name.lower() == 'pythonw.exe':
                runtime = runtime.with_name('python.exe')
            self.process = subprocess.Popen(
                [str(runtime), '-u', str(Path(__file__).resolve()), '--worker'],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        if self._ledger_seed_offer_pending:
            write_packet(self.process.stdin, dict(_ledgerSeedOffer=True,
                                                   packet=self._ledger_seed_packet,
                                                   diagnostic=self._ledger_seed_status))
            self._ledger_seed_offer_pending = False
            self._ledger_seed_packet = None
        write_packet(self.process.stdin, inputs)
        response = read_packet(self.process.stdout)
        if 'error' in response:
            raise RuntimeError(response['error'])
        view = response['view']
        self._ledger_seed_status = dict(view.get('projectionCacheSeed') or self._ledger_seed_status)
        if response.get('hasWire'):
            wire_json = read_packet(self.process.stdout)
            if not isinstance(wire_json, bytes):
                raise RuntimeError('Status projection returned an invalid JSON wire payload')
            return ProjectedView(view, wire_json)
        return view

    def close(self):
        if self.process is None:
            return
        try:
            self.process.stdin.close()
        except OSError:
            pass
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.abort()
            self.process.wait(timeout=5)
        self.process.stdout.close()

    def abort(self):
        """Terminate only this publisher's process tree after bounded shutdown expires."""
        if self.process is None or self.process.poll() is not None:
            return
        if os.name == 'nt':
            # A Windows venv launcher can have a real interpreter child inheriting its pipes.
            subprocess.run(['taskkill', '/PID', str(self.process.pid), '/T', '/F'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=subprocess.CREATE_NO_WINDOW, timeout=5)
        else:
            self.process.kill()


def projection_worker():
    projection = StatusProjection()
    try:
        first = True
        while True:
            try:
                inputs = read_packet(sys.stdin.buffer)
            except EOFError:
                break
            if first and isinstance(inputs, dict) and inputs.get('_ledgerSeedOffer') is True:
                projection.ledger_seed_packet = inputs.get('packet')
                projection.ledger_seed_status = dict(inputs.get('diagnostic') or {})
                first = False
                continue
            first = False
            try:
                view = projection(inputs)
                wire_json = getattr(view, 'wire_json', None)
                # Keep the two independently bounded payloads separate. A mature view that already
                # fits MAX_PACKET must not fail publication merely because its browser JSON is large.
                has_wire = isinstance(wire_json, bytes) and len(wire_json) <= MAX_PACKET - 32
                write_packet(sys.stdout.buffer, {'view': dict(view), 'hasWire': has_wire})
                if has_wire:
                    write_packet(sys.stdout.buffer, wire_json)
            except Exception as exc:
                write_packet(sys.stdout.buffer, {'error': str(exc)})
    finally:
        projection.close()


if __name__ == '__main__' and sys.argv[1:] == ['--worker']:
    projection_worker()
