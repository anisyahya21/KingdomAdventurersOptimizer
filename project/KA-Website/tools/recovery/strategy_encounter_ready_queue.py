"""Bounded ready-ahead scheduler for encounter evaluations.

The canonical evaluator remains responsible for exact ledger reservations, frozen holdout checks,
result validation, and the single SQLite writer.  This thin subclass admits a small, finite number
of already-authorised jobs ahead of the native pool so coordinator analysis can run without leaving
the battle interpreters without work.

The extra window is enabled only for a pool that explicitly implements per-interpreter duty holds.
Injected pools and older callers keep the canonical evaluator's original one-job-per-slot behavior.
"""
from __future__ import annotations

import strategy_encounter_evaluation as evaluation

DEFAULT_READY_WINDOW = 16
MAX_READY_WINDOW = 16
MAX_READY_PENDING = 256
DEFAULT_TIMEOUT_SECONDS = evaluation.DEFAULT_TIMEOUT_SECONDS


class ReadyQueueEvaluator(evaluation.Evaluator):
    """Evaluator with at most ``workers * ready_window`` reserved/submitted jobs outstanding."""

    def __init__(self, *, ready_window=DEFAULT_READY_WINDOW, **kwargs):
        super().__init__(**kwargs)
        try:
            requested = int(ready_window)
        except (TypeError, ValueError):
            requested = DEFAULT_READY_WINDOW
        requested = max(1, min(MAX_READY_WINDOW, requested))
        self._ready_queue_enabled = bool(
            getattr(self.pool, 'supports_ready_window', False)
            and callable(getattr(self.pool, 'configure_duty', None)))
        effective = min(requested, max(1, MAX_READY_PENDING // self.workers))
        self.ready_window = effective if self._ready_queue_enabled else 1
        self.max_pending = self.workers * self.ready_window
        self._admission_cursor = 0
        if self._ready_queue_enabled:
            # Trace metadata follows bounded logical admissions rather than growing once per battle.
            self._slot_trace = [None] * self.max_pending
            self.pool.configure_duty(self.duty)

    def admission_capacity(self):
        """Remaining bounded admission capacity, including queued and unharvested futures."""
        if self.closed:
            return 0
        return max(0, self.max_pending - len(self.pending))

    def _expired(self):
        if not self.timeout:
            return []
        now = self.clock()
        expired = []
        for key, job in self.pending.items():
            future = job.get('future')
            if future is not None and future.done():
                # A finished result waits for the coordinator's single-writer harvest; analysis time
                # must not turn a completed battle into a timeout.
                continue
            dispatch = getattr(future, 'ka_dispatch_state', None)
            if isinstance(dispatch, dict):
                started = dispatch.get('startedAt')
                # A task still waiting in the bounded ready queue has not started its battle timer.
                if started is not None and now - started > self.timeout:
                    expired.append(key)
            elif now - job['started'] > self.timeout:
                # Injected pools without the scheduler timing contract retain base timeout behavior.
                expired.append(key)
        return expired

    def _acquire_slot(self, now):
        # The base evaluator's slot is used for trace attribution and duty reporting. In ready-queue
        # mode it does not stand for a process: HeadlessPool enforces duty against the actual
        # interpreter that ran the previous battle.
        if not self._ready_queue_enabled:
            return super()._acquire_slot(now)
        if len(self.pending) >= self.max_pending:
            return None
        # Logical slots are bounded and never collide with a slower still-pending task. They do not
        # identify processes: HeadlessPool owns execution and per-interpreter duty.
        occupied = {job.get('slot') for job in self.pending.values()}
        slot = next((candidate for offset in range(self.max_pending)
                     if (candidate := (self._admission_cursor + offset) % self.max_pending)
                     not in occupied), None)
        if slot is None:
            return None
        self._admission_cursor = (slot + 1) % self.max_pending
        return slot

    def _fill_trace(self, trace, slot, key, pair, holdout, runnable_at, submit_begin,
                    submitted_at):
        if self._ready_queue_enabled and slot is not None:
            # Reused bounded logical slots do not represent physical worker replacement.
            self._slot_trace[slot] = None
        return super()._fill_trace(trace, slot, key, pair, holdout, runnable_at,
                                   submit_begin, submitted_at)

    def _finalize_pending_trace(self, slot, replacement_at):
        if not self._ready_queue_enabled:
            return super()._finalize_pending_trace(slot, replacement_at)
        # A reused logical admission id is not evidence that the same process took the next task.
        # Flush old trace rows with the replacement unknown instead of inventing a worker boundary.
        pending = self._trace_pending.pop(slot, None)
        if not pending:
            return
        for record in pending:
            try:
                self._commit_trace_record(record, None, 'unknown')
            except Exception:  # trace diagnostics must not break dispatch
                pass

    def _release_slot(self, job, now, elapsed):
        if not self._ready_queue_enabled:
            return super()._release_slot(job, now, elapsed)
        slot = job.get('slot')
        if slot is None:
            return
        # Preserve the base status vector at its configured worker width; it is observational only
        # in queue mode, since the pool enforces actual per-interpreter duty.
        physical_slot = int(slot) % self.workers
        ready = now + self.duty_hold(elapsed)
        self._slot_ready[physical_slot] = max(self._slot_ready[physical_slot], ready)
        trace = job.get('trace')
        if trace is not None:
            trace['_slotReleasedAt'] = now
            trace['_slotReadyAt'] = ready

    def configure_duty(self, duty):
        value = super().configure_duty(duty)
        configure = getattr(self.pool, 'configure_duty', None)
        if self._ready_queue_enabled and callable(configure):
            configure(value)
        return value

    def status(self):
        result = super().status()
        completed_unharvested = sum(
            1 for job in self.pending.values()
            if job.get('future') is not None and job['future'].done())
        result['completedUnharvested'] = completed_unharvested
        result['windowCapacity'] = self.max_pending
        result['admissionCapacity'] = self.admission_capacity()
        snapshot = getattr(self.pool, 'snapshot', None)
        if callable(snapshot):
            try:
                pool = snapshot()
            except Exception:  # diagnostics must not interfere with dispatch
                pool = None
            if isinstance(pool, dict):
                result.update(
                    readyQueueDepth=pool.get('queued'),
                    executingWorkers=pool.get('executing'),
                    coolingWorkers=pool.get('cooling'),
                    availableWorkers=pool.get('available'),
                    readyWindow=self.ready_window,
                )
                if self._ready_queue_enabled:
                    result['gatedWorkers'] = pool.get('cooling', 0)
        return result

    def busy(self):
        if self._ready_queue_enabled:
            return bool(self.pending)
        return super().busy()
