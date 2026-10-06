"""Real coordinator/store checks with deterministic cheap worker results; no live-library writes."""
import json
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import strategy_experiments as experiments
import strategy_optimizer as optimizer
from strategy_optimizer_adapter import default_scenario, provenance, stats
import strategy_evidence
import strategy_probe


def worker(scenario, seeds):
    return dict(verdict=1, censored=False, ticks=10, prizeCallbacks=4, survivors=1,
                resourceUses=0, behavior=dict(attacks=1, heals=0, prizes=4),
                seeds=list(seeds), digest=str(seeds), elapsedSeconds=.01,
                rewardOutcome=dict(awardedChests=4, pendingChests=4,
                                   awardedBasis='test', inventoryVerified=False))


class ExperimentChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ka-experiments-')
        self.path = Path(self.temp.name) / 'test.sqlite'
        self.store = optimizer.Store(self.path, provenance())
        self.scenario = default_scenario()
        with self.store.db:
            self.cid = self.store.add(self.scenario, 'Original', 'supplied', stats(self.scenario))
            self.store.set('scope', optimizer.scope(self.scenario))

    def tearDown(self):
        if self.store:
            self.store.close()
        self.temp.cleanup()

    def test_count_validation_and_repeat_resume(self):
        for value in (0, -1, True, 1.5, 'nan', 10**12):
            with self.assertRaises(ValueError):
                experiments.run_count(value)
        self.assertEqual(experiments.run_count(500000), 500000)
        job = experiments.create(self.store, self.cid, 1300)
        for ordinal in range(7):
            self.store.record(self.cid, 'validation', ordinal,
                              worker(self.scenario, optimizer.seed_pair('validation', ordinal)))
        self.store.flush()
        self.assertEqual(experiments.progress(self.store)['completed'], 7)
        with self.assertRaises(ValueError):
            experiments.create(self.store, self.cid, 8)
        self.store.close()
        self.store = None
        with patch.object(optimizer, 'ASYNC_PUBLICATION', False), patch.object(optimizer, 'worker', worker), \
                patch('strategy_optimizer_fast.HeadlessPool', lambda n: ThreadPoolExecutor(max_workers=n)):
            coordinator = optimizer.Optimizer(self.path)
            try:
                deadline = time.monotonic() + 30
                while coordinator.status().get('state') not in ('Paused', 'Stopped'):
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(.05)
                coordinator.command('start', dict(workers=4, duty=1), wait=True)
                while True:
                    state = coordinator.status()
                    if state.get('error'):
                        self.fail(state['error'])
                    if (state.get('focusedExperiment') or {}).get('status') == 'complete':
                        break
                    self.assertLess(time.monotonic(), deadline, state.get('state'))
                    time.sleep(.05)
                self.assertEqual(state['state'], 'Paused')
                self.assertEqual(state['focusedExperiment']['completed'], 1300)
                self.assertEqual(state['totalRuns'], 1300)
            finally:
                coordinator.command('close')
                coordinator.thread.join(30)
                self.assertFalse(coordinator.thread.is_alive())
        self.store = optimizer.Store(self.path, provenance())
        self.assertEqual(strategy_evidence.count(self.store.db, self.cid), 1300)
        self.assertEqual(len(self.store.rows(self.cid)), optimizer.MAX_SAMPLES)
        # Re-accepting an old seed cannot inflate counters, even after its replay row rolled off.
        self.store.record(self.cid, 'validation', 70, worker(self.scenario, optimizer.seed_pair('validation', 70)))
        self.store.flush()
        self.assertEqual(self.store.get('totalRuns'), 1300)
        report = experiments.report(self.store.db, self.store.get(experiments.KEY))
        self.assertEqual(report['comparisons'][0]['runs'], 1300)
        self.assertEqual(report['comparisons'][0]['meanEarned'], 4)
        followup = experiments.create(self.store, self.cid, 10000)
        self.assertEqual(followup['pairedStart'], 1300)
        self.assertEqual(followup['pairedEnd'], 11300)
        self.assertEqual(len(self.store.get(experiments.HISTORY)), 1)

    def test_ablation_exact_changes_and_pairing(self):
        original = json.dumps(self.scenario, sort_keys=True)
        variants = list(experiments.skill_variants(self.scenario))
        self.assertTrue(variants)
        self.assertEqual(json.dumps(self.scenario, sort_keys=True), original)
        for _label, child in variants:
            for before, after in zip(self.scenario['ownUnits'], child['ownUnits']):
                self.assertEqual({k: v for k, v in before.items() if k not in ('skills', 'invocationLevels')},
                                 {k: v for k, v in after.items() if k not in ('skills', 'invocationLevels')})
                self.assertEqual(len(after.get('skills', [])), len(after.get('invocationLevels', [])))
        job = experiments.create(self.store, self.cid, 128, 'skills')
        self.assertGreater(len(job['arms']), 1)
        for arm in job['arms'][:2]:
            for ordinal in range(128):
                result = worker(self.scenario, optimizer.seed_pair('validation', ordinal))
                if arm['candidateId'] != self.cid:
                    result['rewardOutcome']['awardedChests'] = 1
                self.store.record(arm['candidateId'], 'validation', ordinal, result)
        self.store.flush()
        report = experiments.report(self.store.db, job)
        delta = report['comparisons'][1]['paired']
        self.assertEqual(delta['n'], 128)
        self.assertEqual(delta['mean'], -3)
        experiments.finish(self.store, 'ended')
        self.assertEqual(self.store.get(experiments.KEY)['status'], 'ended')

    def test_controlled_feedback_is_scoped_and_not_double_counted(self):
        unit = next(unit for unit in self.scenario['ownUnits'] if unit.get('human'))
        program = dict(id='test', parent=dict(candidateId=self.cid), baseline=dict(effective=100),
                       unit=unit['name'], axis='spd', encounterId=self.scenario['encounterId'])
        point = dict(runs=128, unresolved=0, paired=dict(n=128, mean=-2, lower=-3, upper=-1))
        self.assertTrue(experiments.learn_stat_points(self.store, program, {1000: point}))
        self.assertFalse(experiments.learn_stat_points(self.store, program, {1000: point}))
        entry = next(iter(self.store.get('localEvidence').values()))
        self.assertEqual((entry['n'], entry['worsened']), (1, 1))
        self.assertTrue(next(iter(self.store.get('localEvidence'))).endswith(':+900'))
        point['paired'] = dict(n=256, mean=-.1, lower=-1, upper=1)
        experiments.learn_stat_points(self.store, program, {1000: point})
        entry = next(iter(self.store.get('localEvidence').values()))
        self.assertEqual((entry['n'], entry['worsened']), (0, 0))

    def test_legacy_mp_and_unsettled_interior(self):
        import strategy_finetune as ft
        program = ft.new_program(parent=dict(candidateId=self.cid), axis='mp', unit='Unit',
                                 baseline=7149, encounter_id=19, targets=[ft.auto_first_target(7149)],
                                 bounds=(5, 7986), budget=dict(maxChildren=4))
        program.update(auto=True, status='done')
        program['schedule']['budgetExhausted'] = True
        self.assertTrue(ft.upgrade_broad_targets(program))
        self.assertEqual(program['status'], 'active')
        self.assertIn(7986, program['targets'])
        self.assertIn(1000, program['targets'])
        self.assertEqual(program['direction'], 'both')
        self.assertFalse(ft.upgrade_broad_targets(program))
        point = dict(runs=128, completedBank=128, bank=1024, unresolved=0, comparable=True,
                     viable=True, paired=dict(n=128, mean=1, lower=.5, upper=1.5))
        self.assertFalse(ft._point_ready(point, 100), 'a requested bank must finish before narrowing')
        program = ft.new_program(parent=dict(candidateId=self.cid), axis='atk', unit='Unit',
                                 baseline=500, encounter_id=19, targets=[100, 1000], bounds=(5, 2000))
        points = {}
        for value in program['targets']:
            program['points'][str(value)] = dict(candidateId=str(value))
            points[value] = dict(runs=128, bank=128, completedBank=128, unresolved=0,
                                 comparable=True, viable=None, meanEarned=10 if value == 100 else 9,
                                 paired=dict(n=128, mean=0, lower=-1, upper=1))
        actions = ft.plan(program, points)
        self.assertTrue(any(action['reason'] == 'interior' for action in actions['creates']))
        self.assertIsNone(actions['resolved'])

    def test_candidate_scoped_saved_evaluate_batches(self):
        import strategy_optimizer_desktop as desktop
        _label, other_scenario = next(iter(experiments.skill_variants(self.scenario)))
        with self.store.db:
            other = self.store.add(other_scenario, 'Other build', 'supplied', stats(other_scenario))
        self.assertNotEqual(other, self.cid)
        first = experiments.create(self.store, self.cid, 6)
        self.assertEqual((first['pairedStart'], first['pairedEnd']), (0, 6))
        for ordinal in range(6):
            self.store.record(self.cid, 'validation', ordinal,
                              worker(self.scenario, optimizer.seed_pair('validation', ordinal)))
        self.store.flush()
        experiments.finish(self.store, 'complete')
        other_job = experiments.create(self.store, other, 4)
        experiments.finish(self.store, 'complete')
        skills_job = experiments.create(self.store, self.cid, 128, 'skills')
        experiments.finish(self.store, 'ended')
        second = experiments.create(self.store, self.cid, 5)
        self.assertEqual((second['pairedStart'], second['pairedEnd']), (6, 11))
        for ordinal in range(6, 8):
            self.store.record(self.cid, 'validation', ordinal,
                              worker(self.scenario, optimizer.seed_pair('validation', ordinal)))
        self.store.flush()
        # A repeated history entry must be deduped, never reported as two batches.
        with self.store.db:
            history = list(self.store.get(experiments.HISTORY) or [])
            self.store.set(experiments.HISTORY, history + [dict(history[0])])
        self.store.close()
        self.store = None
        with patch.object(optimizer, 'ASYNC_PUBLICATION', False):
            bridge = desktop.Bridge(self.path)
            try:
                deadline = time.monotonic() + 20
                while bridge.status().get('state') not in ('Paused', 'Stopped'):
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(.02)
                # Without a candidate the reply is the legacy shape, plus nothing else.
                plain = bridge.experiment_report()
                self.assertTrue(plain['ok'], plain.get('error'))
                self.assertNotIn('batches', plain)
                self.assertEqual(plain['experiment']['id'], second['id'])
                self.assertIsNone(desktop.Bridge.experiment_report.__defaults__[0])
                result = bridge.experiment_report(self.cid)
                self.assertTrue(result['ok'], result.get('error'))
                self.assertEqual(result['batchLimit'], desktop.EXPERIMENT_BATCH_LIMIT)
                self.assertEqual(desktop.EXPERIMENT_BATCH_LIMIT, 20)
                self.assertFalse(result['batchesTruncated'])
                batches = result['batches']
                ids = [job['id'] for job in batches]
                # Newest first, this candidate only, evaluate mode only, deduped.
                self.assertEqual(ids, [second['id'], first['id']])
                self.assertEqual([job['candidateId'] for job in batches], [self.cid, self.cid])
                self.assertEqual([job['mode'] for job in batches], ['evaluate', 'evaluate'])
                self.assertNotIn(other_job['id'], ids)
                self.assertNotIn(skills_job['id'], ids)
                # Completion is this batch's own evidence, not the candidate's lifetime cursor.
                self.assertEqual([(job['completed'], job['total']) for job in batches],
                                 [(2, 5), (6, 6)])
                self.assertEqual([job['status'] for job in batches], ['active', 'complete'])
                self.assertEqual([job['requestedPerBuild'] for job in batches], [5, 6])
                self.assertEqual(batches[1]['arms'][0]['candidateId'], self.cid)
                self.assertEqual(batches[1]['comparisons'][0]['runs'], 6)
                self.assertEqual(batches[1]['comparisons'][0]['meanEarned'], 4)
                self.assertEqual(batches[0]['comparisons'][0]['runs'], 2)
            finally:
                bridge.close()
                bridge._optimizer.thread.join(30)
                bridge._guard.close()
        # Reopen persistence: the same ids, meta and evidence readings survive a fresh open.
        self.store = optimizer.Store(self.path, provenance())
        self.assertEqual(self.store.get(experiments.KEY)['id'], second['id'])
        self.assertEqual([job['id'] for job in self.store.get(experiments.HISTORY)],
                         [first['id'], other_job['id'], skills_job['id'], first['id']])
        self.assertEqual(experiments.batch_progress(self.store.db, self.store.get(experiments.KEY)),
                         dict(completed=2, total=5))
        self.assertEqual(experiments.report(self.store.db, self.store.get(experiments.KEY))
                         ['comparisons'][0]['runs'], 2)

    def test_manual_batch_and_new_visual_enter_library_once(self):
        import strategy_optimizer_desktop as desktop
        self.store.close()
        self.store = None
        with patch.object(optimizer, 'ASYNC_PUBLICATION', False):
            bridge = desktop.Bridge(self.path)
            try:
                deadline = time.monotonic() + 20
                while bridge.status().get('state') not in ('Paused', 'Stopped'):
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(.02)
                bridge._simulate_trials = lambda scenario, seeds: ([(pair, worker(scenario, pair)) for pair in seeds], None)
                first = bridge.run_strategy(self.cid, count=8)
                self.assertTrue(first['ok'], first.get('error'))
                self.assertEqual(first['storedSummary']['attempts'], 8)
                self.assertEqual(bridge.status()['totalRuns'], 8)
                bridge._trace_replay = lambda scenario, seeds: dict(worker(scenario, seeds), replay={'units': []})
                with patch.object(desktop, 'replay_visual_setup', return_value=dict(visualSetup={}, jobIdentity={}, warnings=[])):
                    visual = bridge.simulate_strategy_visual(self.cid)
                self.assertTrue(visual['ok'], visual.get('error'))
                self.assertEqual(visual['seeds'], optimizer.seed_pair('validation', 8))
                self.assertEqual(bridge.status()['totalRuns'], 9)
                # The sidecar is a replay list, not an extra copy in the totals.
                self.assertEqual(visual['storedSummary']['attempts'], 9)
                self.assertEqual(visual['trialSummary']['attempts'], 9)
                before = bridge.status()['totalRuns']
                with patch.object(desktop, 'replay_visual_setup', return_value=dict(visualSetup={}, jobIdentity={}, warnings=[])):
                    replay = bridge.replay_strategy_run(self.cid, seeds=visual['seeds'])
                self.assertTrue(replay['ok'], replay.get('error'))
                self.assertEqual(bridge.status()['totalRuns'], before)
            finally:
                bridge.close()
                bridge._optimizer.thread.join(30)
                bridge._guard.close()


if __name__ == '__main__':
    unittest.main()
