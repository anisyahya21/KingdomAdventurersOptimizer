"""Publication cannot block dispatch, alias planner state, or revive an obsolete session."""
import threading
import tempfile
import time
from pathlib import Path
import unittest
from strategy_publication import LatestPublication, StatusProjection, ProcessProjection, capture, detached
from strategy_optimizer import Optimizer, Store, scope


class PublicationTests(unittest.TestCase):
    def test_slow_render_coalesces_waiting_requests_without_blocking_submit(self):
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        rendered, delivered = [], []
        def render(value):
            rendered.append(value)
            if value == 1:
                entered.set()
                if not release.wait(5):
                    raise TimeoutError('test did not release renderer')
            return {'value': value}
        def deliver(epoch, view, seconds):
            delivered.append((epoch, view['value']))
            if view['value'] == 3:
                finished.set()
        publisher = LatestPublication(render, deliver, lambda *_: None)
        try:
            publisher.submit(1, 1)
            self.assertTrue(entered.wait(5))
            # These calls complete while rendering is held, and the waiting queue stays bounded.
            publisher.submit(1, 2)
            publisher.submit(1, 3)
            release.set()
            self.assertTrue(finished.wait(5))
            self.assertEqual(rendered, [1, 3])
            self.assertEqual(delivered, [(1, 1), (1, 3)])
        finally:
            release.set()
            publisher.close()

    def test_delivered_view_is_detached_from_renderer_cache(self):
        cached = {'candidate': {'n': 1}}
        delivered, done = [], threading.Event()
        def receive(_epoch, view, _seconds):
            delivered.append(view)
            done.set()
        publisher = LatestPublication(lambda _: cached, receive, lambda *_: None)
        try:
            publisher.submit(1, None)
            self.assertTrue(done.wait(5))
            cached['candidate']['n'] = 999
            self.assertEqual(delivered[0]['candidate']['n'], 1)
        finally:
            publisher.close()

    def test_failure_is_reported_and_reader_closes(self):
        failed, closed = threading.Event(), threading.Event()
        errors = []
        def render(_):
            raise ValueError('broken projection')
        def on_failure(epoch, message):
            errors.append((epoch, message))
            failed.set()
        publisher = LatestPublication(render, lambda *_: None, on_failure, closed.set)
        publisher.submit(7, None)
        self.assertTrue(failed.wait(5))
        publisher.close()
        self.assertTrue(closed.is_set())
        self.assertEqual(errors, [(7, 'broken projection')])
        self.assertFalse(publisher.thread.is_alive())

    def owner(self):
        owner = Optimizer.__new__(Optimizer)
        owner.lock = threading.Lock()
        owner.shutdown = threading.Event()
        owner._publication_epoch = 2
        owner.snapshot = dict(state='Paused', totalRuns=100)
        return owner

    def test_late_running_view_cannot_overwrite_pause_or_new_focus(self):
        owner = self.owner()
        owner._accept_publication(1, dict(state='Running', totalRuns=90), .5)
        self.assertEqual(owner.snapshot, dict(state='Paused', totalRuns=100))
        owner._fail_publication(1, 'old failure')
        self.assertNotIn('publicationError', owner.snapshot)

    def test_shutdown_rejects_delivery(self):
        owner = self.owner()
        owner.shutdown.set()
        owner._accept_publication(2, dict(state='Running'), .5)
        self.assertEqual(owner.snapshot['state'], 'Paused')

    def test_capture_does_not_share_nested_planner_data(self):
        owner = self.owner()
        owner._planner_portfolio = {'active': [{'target': 512}]}
        captured = capture(owner)
        owner._planner_portfolio['active'][0]['target'] = 1024
        self.assertEqual(captured['_planner_portfolio']['active'][0]['target'], 512)

    def test_projection_preserves_live_worker_split_and_queue_telemetry(self):
        from strategy_optimizer_adapter import default_scenario, provenance, stats
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'projection-workers.sqlite'
            store = Store(path, provenance())
            scenario = dict(default_scenario(), tickLimit=40, encounterId=19)
            with store.db:
                store.set('scope', scope(scenario))
                store.add(scenario, 'Projection baseline', 'supplied', stats(scenario))
            store.close()

            owner = Optimizer(path)
            projection = StatusProjection()
            try:
                deadline = time.monotonic()+10
                while owner.status()['state'] == 'Opening library':
                    if time.monotonic() > deadline:
                        self.fail('fixture did not open')
                    time.sleep(.01)
                allocation = owner._worker_allocation_status()
                allocation.update(effectiveWorkers=48, effectivePlannerWorkers=43,
                                  effectiveBattleWorkers=5)
                owner._worker_allocation_status = lambda: dict(allocation)

                class Evaluator:
                    @staticmethod
                    def status():
                        return dict(workers=5, inflight=80, readyQueueDepth=75,
                                    executingWorkers=5, coolingWorkers=0,
                                    availableWorkers=0, completedUnharvested=0,
                                    windowCapacity=80, admissionCapacity=0, readyWindow=16)

                owner._community_evaluator = Evaluator()
                inputs = capture(owner)
                projected = projection(inputs)
                self.assertEqual(projected['throughput']['plannerWorkers'], 43)
                self.assertEqual(projected['throughput']['effectiveBattleWorkers'], 5)
                self.assertEqual(projected['throughput']['capacityWorkers'], 5)
                self.assertEqual(projected['scheduler']['executionQueue']['inflight'], 80)
                self.assertEqual(projected['scheduler']['executionQueue']['windowCapacity'], 80)
            finally:
                projection.close()
                owner.command('close')
                owner.thread.join(10)
                self.assertFalse(owner.thread.is_alive())

    def test_readonly_projection_matches_writer_view_after_new_evidence(self):
        from strategy_optimizer_adapter import default_scenario, provenance, stats
        from check_optimizer_finetune import outcome
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'projection.sqlite'
            store = Store(path, provenance())
            scenario = dict(default_scenario(), tickLimit=40, encounterId=19)
            with store.db:
                store.set('scope', scope(scenario))
                cid = store.add(scenario, 'Projection baseline', 'supplied', stats(scenario))
            store.close()
            owner = Optimizer(path)
            projection = StatusProjection()
            process_projection = ProcessProjection()
            writer = None
            try:
                deadline = time.monotonic()+10
                while owner.status()['state'] == 'Opening library':
                    if time.monotonic()>deadline:
                        self.fail('fixture did not open')
                    time.sleep(.01)
                self.assertEqual(owner.status()['state'], 'Paused')
                inputs = capture(owner)
                writer = Store(path, provenance())
                control = Optimizer.__new__(Optimizer)
                for name, value in detached(inputs).items():
                    setattr(control, name, value)
                control.lock = threading.Lock()
                control._candidate_cache = {}
                control._library_encounters = []
                for batch in range(2):
                    if batch:
                        for ordinal in range(80):
                            writer.record(cid, 'validation', ordinal, outcome(0, ordinal))
                    control._snapshot_wire_json = b'previous synchronous wire'
                    control._publish_sync(writer, 'Running')
                    self.assertIsNone(control._snapshot_wire_json,
                                      'synchronous publication must invalidate its prior wire')
                    actual = detached(projection(inputs))
                    self.assertEqual(actual, control.snapshot)
                    process_view = process_projection(inputs)
                    self.assertEqual(process_view, control.snapshot)
                    self.assertIsInstance(getattr(process_view, 'wire_json', None), bytes)
                    self.assertEqual(actual['totalRuns'], 80*batch)
                    if batch == 0:
                        # A failed publisher can be restarted without restarting the optimizer.
                        dead_process = process_projection.process
                        process_projection.abort()
                        dead_process.wait(timeout=5)
                        self.assertEqual(process_projection(inputs), control.snapshot)
                        self.assertIsNot(process_projection.process, dead_process)
            finally:
                projection.close()
                process_projection.close()
                if process_projection.process is not None:
                    self.assertIsNotNone(process_projection.process.poll())
                if writer:
                    writer.close()
                owner.command('close')
                owner.thread.join(10)
                self.assertFalse(owner.thread.is_alive())


if __name__ == '__main__':
    unittest.main(verbosity=2)
