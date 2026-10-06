"""One finite asynchronous evaluator for all encounter-aware experiment purposes.

The coordinator owns its SQLite connection. Child processes run native compact battles only;
mechanics compilation, fitting, ledger writes and publication never execute in workers.

Two job kinds share the same bounded queue: development jobs reserve EXACTLY ONE ordered pair through
the ledger (a retry or restart can never double-charge a sample), and frozen holdout jobs never
reserve because the pair was already planned and charged at freeze time.

Each worker honours its own duty cycle. With ``duty < 1`` a worker that just finished a battle of
``elapsed`` seconds is held for ``elapsed * (1/duty - 1)`` seconds before it accepts the next job, so
its busy fraction matches the requested duty. ``duty == 1`` (the default) never holds a worker, so the
public behaviour is unchanged for callers that do not opt in.

Telemetry is opt-in and never fabricated: a worker that does not report CPU seconds contributes an
absent reading, not a zero. An unresolved or errored job is recorded as an error outcome (which
blocks publication) rather than silently dropped. A job that exceeds the bounded timeout terminates
the workers and is recorded the same way.

Battle-timing tracing is a separate, opt-in diagnostic. It is OFF unless ``KA_OPTIMIZER_BATTLE_TRACE``
names a JSONL path; when on, THIS evaluator (the host thread) is the single writer. Worker processes
only return their own stage durations inside the response, and the timing is carried on the result
object as a non-serialised attribute so it can never reach the persisted outcome or the digest.
Each completed sampled row is held in trace-only memory until the slot's next pool submission, so the
row can carry the exact same-slot replacement boundary (that submit time) even when the replacing
battle is unsampled; close flushes any remaining rows with the replacement left unknown.
"""
from __future__ import annotations
from copy import deepcopy
import os
import time

import strategy_experiment_store as ledger

DEFAULT_TIMEOUT_SECONDS = 180.0


class Evaluator:
    def __init__(self, *, workers=1, telemetry=True, pool=None, require_native=True,
                 timeout=DEFAULT_TIMEOUT_SECONDS, duty=1.0, clock=None):
        if isinstance(workers, bool) or not isinstance(workers, int) or not 1 <= workers <= 32:
            raise ValueError('workers must be an integer in [1,32]')
        if not (timeout is None or (isinstance(timeout, (int, float))
                                    and not isinstance(timeout, bool) and timeout > 0)):
            raise ValueError('timeout must be a positive number of seconds or None')
        #: The focused encounter id, set by the coordinator. Metadata for the opt-in trace only.
        self.encounter_id = None
        try:
            from strategy_optimizer_fast import open_battle_trace
            self._trace = open_battle_trace()
        except Exception:  # noqa: BLE001 - tracing must never break evaluation
            self._trace = None
        if pool is None:
            from strategy_optimizer_fast import HeadlessPool
            pool = HeadlessPool(workers, telemetry=telemetry, trace=self._trace is not None)
        self.pool = pool
        self.workers = workers
        self.require_native = require_native
        self.timeout = timeout
        self.clock = clock if callable(clock) else time.perf_counter
        self.duty = self._clamp_duty(duty)
        #: Per-slot wall-clock time before which that worker may not accept new work (duty hold).
        self._slot_ready = [0.0] * workers
        #: The live trace dict of the last (or current) job dispatched to each logical slot.
        self._slot_trace = [None] * workers
        #: Completed sampled trace records held per slot until the slot's NEXT battle is submitted,
        #: so the exact same-slot replacement boundary can be captured even when that next battle is
        #: unsampled. Trace-only memory: never read by dispatch, never persisted, never in the ledger.
        self._trace_pending = {}
        self.pending = {}
        self.closed = False
        #: True only inside a ledger persistence call, so an instantaneous sample can report it.
        self._persisting = False
        self.metrics = {'submitted': 0, 'completed': 0, 'native': 0, 'fallback': 0, 'errors': 0,
                        'timeouts': 0, 'holdout': 0, 'simulationSeconds': 0.0,
                        'workerCpuSeconds': 0.0, 'workerCpuReadings': 0,
                        'telemetryMissing': 0, 'persistenceSeconds': 0.0}

    @staticmethod
    def _clamp_duty(value):
        try:
            requested = float(value)
        except (TypeError, ValueError):
            return 1.0
        return max(0.05, min(1.0, requested))

    def configure_duty(self, duty):
        """Set the per-worker duty cycle; ``1.0`` disables the deliberate idle hold."""
        self.duty = self._clamp_duty(duty)
        return self.duty

    def duty_hold(self, busy_seconds):
        """Seconds to hold a worker after ``busy_seconds`` of work so its duty matches ``self.duty``."""
        if self.duty >= 1.0:
            return 0.0
        return max(0.0, float(busy_seconds)) * (1.0 - self.duty) / self.duty

    def _acquire_slot(self, now):
        occupied = {job.get('slot') for job in self.pending.values()}
        for slot in range(self.workers):
            if slot not in occupied and now >= self._slot_ready[slot]:
                return slot
        return None

    # -- dispatch ---------------------------------------------------------------------------------
    def submit(self, db, experiment_id, candidate_id, scenario, seed_pair, *, runnable_at=None):
        submit_begin = self.clock()
        if self.closed:
            raise RuntimeError('Evaluator is closed')
        slot = self._acquire_slot(self.clock())
        if slot is None:
            return False
        pair = tuple(seed_pair)
        key = (experiment_id, candidate_id, pair)
        if key in self.pending:
            return False
        if not ledger.reserve(db, experiment_id, candidate_id, pair):
            return False
        if ledger.pending_sample(db, experiment_id, candidate_id, pair) is None:
            return False
        return self._dispatch(slot, key, scenario, pair, holdout=False,
                              trace=self._trace_token(), runnable_at=runnable_at,
                              submit_begin=submit_begin)

    def submit_recovered(self, db, entry, scenario, *, runnable_at=None):
        submit_begin = self.clock()
        if self.closed:
            raise RuntimeError('Evaluator is closed')
        experiment_id = entry.get('chargedExperimentId')
        candidate_id = entry.get('candidateId')
        pair = tuple(entry.get('seedPair') or ())
        if experiment_id is None or not candidate_id or len(pair) != 2:
            return False
        slot = self._acquire_slot(self.clock())
        if slot is None:
            return False
        key = (experiment_id, candidate_id, pair)
        if key in self.pending:
            return False
        if ledger.pending_sample(db, experiment_id, candidate_id, pair) is None:
            return False
        return self._dispatch(slot, key, scenario, pair, holdout=False,
                              trace=self._trace_token(), runnable_at=runnable_at,
                              submit_begin=submit_begin)

    def submit_holdout(self, db, experiment_id, candidate_id, scenario, seed_pair, *,
                       runnable_at=None):
        submit_begin = self.clock()
        if self.closed:
            raise RuntimeError('Evaluator is closed')
        slot = self._acquire_slot(self.clock())
        if slot is None:
            return False
        pair = tuple(seed_pair)
        key = ('holdout', experiment_id, candidate_id, pair)
        if key in self.pending:
            return False
        row = db.execute('SELECT result FROM ea_holdout WHERE experiment_id=? AND candidate_id=? '
                         'AND seed_a=? AND seed_b=?', (experiment_id, candidate_id, pair[0], pair[1])
                         ).fetchone()
        if row is None or row[0] is not None:
            # Not a frozen pair, or already complete: never re-run and never overwrite a holdout.
            return False
        return self._dispatch(slot, key, scenario, pair, holdout=True,
                              trace=self._trace_token(), runnable_at=runnable_at,
                              submit_begin=submit_begin)

    @property
    def tracing(self):
        """True only when the process-wide opt-in trace sink is open."""
        return self._trace is not None

    def _trace_token(self):
        """A per-battle trace token for a deterministically selected submission, or None."""
        if self._trace is None:
            return None
        seq = self._trace.next_seq()
        if not self._trace.wants(seq):
            return None
        return {'seq': seq, 'hostPid': os.getpid(), 'encounterId': self.encounter_id}

    def _instant_state(self):
        """Real instantaneous counts: evaluator pending, pool queue/executing/idle, persistence."""
        done = 0
        for job in self.pending.values():
            future = job.get('future')
            if future is not None and future.done():
                done += 1
        state = {'evaluatorPending': len(self.pending), 'completedUnharvested': done,
                 'persistence': int(bool(self._persisting)), 'idleWorkers': None}
        snapshot = getattr(self.pool, 'snapshot', None)
        if callable(snapshot):
            try:
                snap = snapshot()
            except Exception:  # noqa: BLE001 - a diagnostic read must never break dispatch
                snap = None
            if isinstance(snap, dict):
                state['poolQueueDepth'] = snap.get('queued')
                state['poolExecuting'] = snap.get('executing')
                state['poolExecutingMeaning'] = 'pool request/thread active count (not native compute)'
                state['poolAvailableWorkers'] = snap.get('available')
                state['idleWorkers'] = snap.get('available')
        return state

    def _fill_trace(self, trace, slot, key, pair, holdout, runnable_at, submit_begin,
                    submitted_at):
        """Attach host-side identity, slot and replacement metadata to a trace token.

        `runnableAt` is the scheduler's decision stamp (set by the Coordinator immediately before
        dispatch); `submitBeginAt` is this evaluator's own submit entry. They are distinct
        boundaries: a coordinator-supplied `runnable_at` is kept, and a direct evaluator call with
        no runnable stamp falls back to the submit entry so both fields stay meaningful.
        """
        experiment_id, candidate_id = (key[1], key[2]) if holdout else (key[0], key[1])
        submit_entry = submit_begin if submit_begin is not None else submitted_at
        trace.update(slot=slot, holdout=bool(holdout), experimentId=experiment_id,
                     candidateId=candidate_id, seedA=pair[0], seedB=pair[1],
                     admissionAt=runnable_at if runnable_at is not None else submit_entry,
                     submitBeginAt=submit_entry,
                     poolSubmitAt=submitted_at)
        prior = self._slot_trace[slot] if 0 <= slot < len(self._slot_trace) else None
        if prior is not None:
            trace['replacesPriorSeq'] = prior.get('seq')
            trace['replacesPriorCandidate'] = prior.get('candidateId')
            trace['priorSlotReleasedAt'] = prior.get('_slotReleasedAt')
        return trace

    def _submit_job(self, scenario, pair, *, sampled):
        """Submit one battle job, requesting a trace only for a sampled job on a trace-capable pool.

        An unsampled job, or any injected/fake pool that does not advertise `supports_job_trace`,
        keeps the pool's normal `submit(None, scenario, seeds)` signature and wire payload.
        """
        if sampled and getattr(self.pool, 'supports_job_trace', False):
            return self.pool.submit(None, scenario, pair, trace=True)
        return self.pool.submit(None, scenario, pair)

    def _dispatch(self, slot, key, scenario, pair, *, holdout, trace=None, runnable_at=None,
                  submit_begin=None):
        frozen = deepcopy(scenario)
        submitted_at = self.clock()
        future = self._submit_job(frozen, list(pair), sampled=trace is not None)
        # Only an ACCEPTED pool submit is the exact replacement boundary for any sampled row still
        # pending on this slot -- sampled or not. Finalise it AFTER the request returns, so a submit
        # that raises leaves the prior row pending (close flushes it with the replacement unknown
        # rather than mislabelling it replaced) and so the trace-only write can never delay dispatch
        # or touch the ledger.
        self._finalize_pending_trace(slot, submitted_at)
        submitted_done = self.clock()
        job = {'future': future, 'started': submitted_done, 'scenario': frozen,
               'holdout': bool(holdout), 'slot': slot}
        if trace is not None:
            self._fill_trace(trace, slot, key, pair, holdout, runnable_at, submit_begin,
                             submitted_at)
            self._slot_trace[slot] = trace
            job['trace'] = trace
        self.pending[key] = job
        if trace is not None:
            trace['stateAtSubmit'] = self._instant_state()
        self.metrics['submitted'] += 1
        self.metrics['holdout'] += int(bool(holdout))
        return True

    def _release_slot(self, job, now, elapsed):
        slot = job.get('slot')
        if slot is None:
            return
        ready = now + self.duty_hold(elapsed)
        self._slot_ready[slot] = ready
        trace = job.get('trace')
        if trace is not None:
            trace['_slotReleasedAt'] = now
            trace['_slotReadyAt'] = ready

    # -- harvest ----------------------------------------------------------------------------------
    def _error_result(self, pair, exc):
        return {'seeds': list(pair), 'error': f'{type(exc).__name__}: {exc}', 'verdict': None,
                'censored': False, 'rewardOutcome': None}

    def _expired(self):
        if not self.timeout:
            return []
        now = self.clock()
        return [key for key, job in self.pending.items() if now - job['started'] > self.timeout]

    def harvest(self, db):
        # A harvest can commit many samples against the same immutable experiment intent. Keep its
        # metadata cache scoped to this batch so shared futures avoid repeated JSON decoding without
        # retaining a connection or trusting stale rows across later harvests.
        with ledger.experiment_read_cache(db):
            return self._harvest_cached(db)

    def _harvest_cached(self, db):
        completed = []
        expired = self._expired()
        if expired:
            terminate = getattr(self.pool, 'terminate_workers', None)
            if callable(terminate):
                terminate()
            for key in expired:
                now = self.clock()
                job = self.pending.pop(key)
                self._release_slot(job, now, now - job['started'])
                error_result = self._error_result(key[-1], TimeoutError('bounded timeout'))
                completed.append(self._record(db, key, job, error_result, timeout=True))
                self._write_trace(job, None, now, error_result)
        for key, job in list(self.pending.items()):
            future = job['future']
            if not future.done():
                continue
            harvest_begin = self.clock()
            pair = key[-1]
            katrace = None
            try:
                raw = future.result()
                katrace = getattr(raw, 'katrace', None)
                if not isinstance(raw, dict):
                    raise ValueError('Worker result must be a compact mapping')
                if tuple(raw.get('seeds', ())) != tuple(pair):
                    raise ValueError('Worker returned a different seed pair')
                backend = raw.get('resultBackend')
                if backend == 'native':
                    self.metrics['native'] += 1
                else:
                    self.metrics['fallback'] += 1
                    if self.require_native:
                        raise ValueError('Native evaluation required; fallback result excluded: '
                                         + str(raw.get('nativeFallbackReason', backend)))
                result = dict(raw)
            except Exception as exc:  # noqa: BLE001 - every failure becomes a recorded error outcome
                self.metrics['errors'] += 1
                result = self._error_result(pair, exc)
            result.setdefault('elapsedSeconds', self.clock() - job['started'])
            cpu = result.get('cpuSeconds')
            if isinstance(cpu, (int, float)) and not isinstance(cpu, bool):
                self.metrics['workerCpuSeconds'] += float(cpu)
                self.metrics['workerCpuReadings'] += 1
            else:
                # Telemetry was not reported. This is an absent reading, never a zero.
                self.metrics['telemetryMissing'] += 1
            self.metrics['simulationSeconds'] += float(result.get('elapsedSeconds') or 0)
            del self.pending[key]
            self._release_slot(job, self.clock(), float(result.get('elapsedSeconds') or 0.0))
            completed.append(self._record(db, key, job, result, timeout=False))
            self._write_trace(job, katrace, harvest_begin, result)
        return completed

    def _record(self, db, key, job, result, *, timeout):
        began = self.clock()
        job['_persistBegin'] = began
        self._persisting = True
        try:
            if job['holdout']:
                _, experiment_id, candidate_id, pair = key
                accepted = ledger.record_holdout_result(db, experiment_id, candidate_id, pair, result)
            else:
                experiment_id, candidate_id, pair = key
                accepted = ledger.complete(db, experiment_id, candidate_id, pair, result)
        finally:
            self._persisting = False
        job['_persistEnd'] = self.clock()
        self.metrics['persistenceSeconds'] += job['_persistEnd'] - began
        self.metrics['completed'] += int(bool(accepted))
        self.metrics['timeouts'] += int(bool(timeout))
        return {'experimentId': experiment_id, 'candidateId': candidate_id, 'seeds': list(pair),
                'accepted': bool(accepted), 'holdout': bool(job['holdout']), 'timedOut': bool(timeout),
                'result': result}

    def _write_trace(self, job, katrace, harvest_begin, result):
        """Build ONE compact record for a sampled battle and HOLD it until the slot's next submit.

        The record is deliberately not written here: the exact same-slot replacement boundary (the
        next pool submission on this evaluator slot, sampled or not) does not exist yet. The row is
        kept in trace-only memory and finalised by :meth:`_finalize_pending_trace` on the next submit
        to the slot, or flushed by :meth:`_flush_pending_trace` at close with the replacement left
        unknown. Never persists any trace field and never touches the ledger.
        """
        sink = self._trace
        trace = job.get('trace')
        if sink is None or trace is None:
            return False
        try:
            # Canonical worker identity: `workerPid` is the worker interpreter's own os.getpid(),
            # reported in the worker trace. `workerProcessPid` is kept as a same-meaning alias for
            # older in-flight worker payloads; the launcher's Popen PID is named separately so the
            # two are never confused (see strategy_optimizer_fast.HeadlessPool._run).
            worker_pid = None
            if katrace is not None:
                worker_pid = katrace.get('workerPid', katrace.get('workerProcessPid'))
            host = {
                'seq': trace.get('seq'), 'hostPid': trace.get('hostPid'),
                'workerPid': worker_pid,
                'workerLauncherPid': (katrace or {}).get('workerLauncherPid'),
                'slot': job.get('slot'), 'encounterId': trace.get('encounterId'),
                'experimentId': trace.get('experimentId'),
                'candidateId': trace.get('candidateId'),
                'seedA': trace.get('seedA'), 'seedB': trace.get('seedB'),
                'holdout': bool(job.get('holdout')),
                'backend': result.get('resultBackend') if isinstance(result, dict) else None,
                'nativeFallbackReason': (result.get('nativeFallbackReason')
                                         if isinstance(result, dict) else None),
                'requestBytes': (katrace or {}).get('requestBytes'),
                'scenarioBytes': (katrace or {}).get('scenarioBytes'),
                'replacesPriorSeq': trace.get('replacesPriorSeq'),
                'replacesPriorCandidate': trace.get('replacesPriorCandidate'),
            }
            stages = {
                'admission': sink.offset_ms(trace.get('admissionAt')),
                'submitBegin': sink.offset_ms(trace.get('submitBeginAt')),
                'poolSubmit': sink.offset_ms(trace.get('poolSubmitAt')),
                'pipeWrite': sink.offset_ms((katrace or {}).get('pipeWriteAt')),
                'pipeReceive': sink.offset_ms((katrace or {}).get('pipeReceiveAt')),
                'futureAvailable': sink.offset_ms((katrace or {}).get('futureAvailableAt')),
                'harvestBegin': sink.offset_ms(harvest_begin),
                'persistBegin': sink.offset_ms(job.get('_persistBegin')),
                'persistEnd': sink.offset_ms(job.get('_persistEnd')),
                'slotReleased': sink.offset_ms(trace.get('_slotReleasedAt')),
                'slotReady': sink.offset_ms(trace.get('_slotReadyAt')),
            }
            worker = {}
            for name in ('workerPid', 'workerLauncherPid', 'parseSeconds', 'prepareSeconds', 'nativeSeconds',
                         'pythonComputeSeconds', 'convertSeconds', 'serializeSeconds',
                         'simulateSeconds', 'totalSeconds', 'cpuSeconds', 'nativePath',
                         'pipeWaitSeconds', 'waitSeconds', 'idleSeconds', 'seedSeconds',
                         'extractSeconds', 'freeSeconds',
                         # Absolute worker-side boundaries (perf_counter seconds).
                         'receiveAt', 'parseBeginAt', 'parseEndAt', 'simulationBeginAt',
                         'nativeBeginAt', 'nativeEndAt', 'convertBeginAt', 'convertEndAt',
                         'resultReadyAt', 'serializeBeginAt', 'serializeEndAt',
                         'responsePrefixFlushAt',
                         'cpuSecondsScope'):
                if katrace is not None and name in katrace:
                    worker[name] = katrace[name]
            # Match the desktop's assigned lifetime (pool submission through parent harvest), then
            # separately retain the child request wall window. The native interval is never inferred
            # from the pool's request/thread counter, and missing brackets stay unknown.
            assigned_wall = None
            if trace.get('poolSubmitAt') is not None and harvest_begin is not None:
                assigned_wall = harvest_begin - trace['poolSubmitAt']
            request_wall = None
            if (katrace is not None and katrace.get('pipeWriteAt') is not None
                    and katrace.get('futureAvailableAt') is not None):
                request_wall = katrace['futureAvailableAt'] - katrace['pipeWriteAt']
            native_call = None
            if (katrace is not None and katrace.get('nativeBeginAt') is not None
                    and katrace.get('nativeEndAt') is not None):
                native_call = katrace['nativeEndAt'] - katrace['nativeBeginAt']
            occupancy = (native_call / assigned_wall
                         if native_call is not None and assigned_wall and assigned_wall > 0
                         else None)
            request_fraction = (native_call / request_wall
                                if native_call is not None and request_wall and request_wall > 0
                                else None)
            record = {
                'host': host,
                't': stages,
                'tUnits': 'milliseconds from host trace origin (host perf_counter)',
                'originAt': sink.origin,
                'clockBasis': ('time.perf_counter (Windows QueryPerformanceCounter), monotonic and '
                               'system-wide, so worker *At seconds and host offsets share one basis'),
                'replacement': {'status': 'pending', 'submitOffsetMs': None, 'submitAbsolute': None,
                                'harvestToReplacementSeconds': None,
                                'persistToReplacementSeconds': None},
                'worker': worker,
                'workerUnits': ('seconds: durations are worker perf_counter deltas; *At fields are '
                                'absolute worker perf_counter (same Windows monotonic clock as host)'),
                'derived': {
                    'assignedWallSeconds': assigned_wall,
                    'assignedWallBasis': 'poolSubmitAt->harvestBegin',
                    'workerRequestWallSeconds': request_wall,
                    'nativeCallSeconds': native_call,
                    'nativeComputeOccupancy': occupancy,
                    'nativeRequestFraction': request_fraction,
                    'nativeComputeOccupancyBasis': ('nativeCallSeconds (strict ka_run_battle bracket '
                                                    'nativeEndAt-nativeBeginAt) over '
                                                    'assignedWallSeconds (poolSubmitAt->harvestBegin; '
                                                    'the desktop assigned interval), not poolExecuting'),
                },
                'state': {'atSubmit': trace.get('stateAtSubmit'),
                          'atHarvest': self._instant_state()},
                'hostClock': 'monotonic',
            }
            # Absolute anchors the deferred commit needs for the derived replacement delays.
            record['_pendingAbs'] = {'harvest': harvest_begin, 'persistEnd': job.get('_persistEnd')}
            self._trace_pending.setdefault(job.get('slot'), []).append(record)
            return True
        except Exception:  # noqa: BLE001 - a diagnostic write must never break a harvest
            return False

    def _commit_trace_record(self, record, replacement_at, status):
        """Write ONE held record, adding the replacement boundary when it is known."""
        sink = self._trace
        if sink is None:
            return False
        out = dict(record)
        anchors = out.pop('_pendingAbs', {})
        if status == 'replaced' and replacement_at is not None:
            harvest = anchors.get('harvest')
            persist_end = anchors.get('persistEnd')
            out['t'] = dict(record['t'])
            out['t']['replacementSubmit'] = sink.offset_ms(replacement_at)
            out['replacement'] = {
                'status': 'replaced',
                'submitOffsetMs': sink.offset_ms(replacement_at),
                'submitAbsolute': replacement_at,
                'harvestToReplacementSeconds': (replacement_at - harvest
                                                if harvest is not None else None),
                'persistToReplacementSeconds': (replacement_at - persist_end
                                                if persist_end is not None else None),
            }
        else:
            # No next same-slot battle exists (drained or closed): leave the boundary unknown and
            # never fabricate an interval.
            out['replacement'] = {'status': 'unknown', 'submitOffsetMs': None,
                                  'submitAbsolute': None, 'harvestToReplacementSeconds': None,
                                  'persistToReplacementSeconds': None}
        return sink.write(out)

    def _finalize_pending_trace(self, slot, replacement_at):
        """Finalise and write every sampled row held for `slot`, now that its next submit is known."""
        if self._trace is None or not self._trace_pending:
            return
        pending = self._trace_pending.pop(slot, None)
        if not pending:
            return
        for record in pending:
            try:
                self._commit_trace_record(record, replacement_at, 'replaced')
            except Exception:  # noqa: BLE001 - trace bookkeeping must never break dispatch
                pass

    def _flush_pending_trace(self):
        """Flush every still-pending sampled row with the replacement unknown; must never raise."""
        if self._trace is None or not self._trace_pending:
            return
        # Insertion order (FIFO) so the flushed rows keep their deterministic submission order.
        for slot in list(self._trace_pending):
            pending = self._trace_pending.pop(slot, [])
            for record in pending:
                try:
                    self._commit_trace_record(record, None, 'unknown')
                except Exception:  # noqa: BLE001 - close must not be broken by the diagnostic
                    pass

    # -- reports ----------------------------------------------------------------------------------
    def status(self):
        now = self.clock()
        return dict(self.metrics, inflight=len(self.pending), workers=self.workers,
                    duty=self.duty,
                    gatedWorkers=sum(1 for ready in self._slot_ready if now < ready),
                    jobs=[{'experimentId': key[0], 'candidateId': key[1], 'seeds': list(key[-1]),
                           'holdout': bool(job['holdout']), 'slot': job.get('slot'),
                           'seconds': now - job['started']}
                          for key, job in self.pending.items()])

    def busy(self):
        if self.pending:
            return True
        # A duty-held worker is not yet free, so the run is still busy until that hold expires.
        now = self.clock()
        return any(now < ready for ready in self._slot_ready)

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            if self.pending:
                terminate = getattr(self.pool, 'terminate_workers', None)
                if callable(terminate):
                    terminate()
        finally:
            try:
                shutdown = getattr(self.pool, 'shutdown', None)
                if callable(shutdown):
                    shutdown(wait=True)
            finally:
                # Always drain held trace rows, even if worker shutdown raised: a deferred diagnostic
                # record must never be lost. Flushed with the replacement unknown; no interval invented.
                self._flush_pending_trace()
