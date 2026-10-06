"""Read-only HTTP status transport preserves the bridge snapshot while avoiding poll clones."""
from __future__ import annotations

import json
import secrets
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from statistics import median
from http.server import ThreadingHTTPServer
from unittest import mock
import unittest

import strategy_optimizer
import strategy_optimizer_desktop as desktop
from strategy_optimizer import Optimizer
from strategy_publication import ProjectedView


FIXTURE = (Path(__file__).resolve().parents[2] /
           'artifacts/kingdom-adventures/tools/encounter-optimizer-check/fresh-library-status.json')


def make_owner(state='Running', path=None):
    """Build an Optimizer-shaped fixture owner without opening or changing a library."""
    owner = Optimizer.__new__(Optimizer)
    owner.path = Path(path or 'fixture-library.sqlite')
    owner.lock = threading.Lock()
    owner.shutdown = threading.Event()
    owner._publication_epoch = 7
    owner.snapshot = json.loads(FIXTURE.read_text(encoding='utf-8'))
    owner.snapshot.update(state=state, error=None, focusEncounter=19,
                          publicationPending=False, publicationError=None,
                          publicationSeconds=.125, publishedAt=1234.5,
                          campaign={'revision': 'fixture-campaign'},
                          focusEncounters=[19, 27], planningHost={'fixture': True})
    owner._snapshot_wire_json = Optimizer._encode_status_wire(owner.snapshot)
    owner._snapshot_wire_lock = threading.Lock()
    owner._status_transport_bootstrap_lock = threading.Lock()
    owner._status_transport_bootstrap_ready = False
    owner._status_transport_encounter_bootstrap = None
    owner._status_transport_campaign_bootstrap = None
    owner._session_active = 0.0
    owner._session_since = None
    owner._work_started = None
    owner._student_shares = {'community': 0.5, 'rebel': 0.5}
    owner._student_warning = None
    owner._student_runs = {'total': 20}
    owner._student_ledger = {'rows': 3}
    owner._student_tracks = {'total': 4}
    owner._pair_coverage = {'pairs': 2}
    owner._track_shares = {'pair': 0.25}
    owner._average_report = {'mean': 1.25}
    owner._average_plan = {'target': 8}
    owner._breakthrough_status = {'enabled': True}
    owner._mp_recovery_status = {'allowance': 2}
    owner._planner_rank_one = {'candidate-a': 1600, 'candidate-b': 800}
    owner._encounter_report = {'enabled': True, 'encounter': 19, 'studies': 20}
    owner._campaign_report = {'revision': 'fixture-campaign', 'active': True}
    owner.encounters = {19: 'Fixture fight', 27: 'Second fight'}
    owner._workers = 24
    owner._duty = .75
    owner._planner_latency = []
    owner._planner_routes = {}
    owner._planning_pool = None
    owner._planner_task_phase = {}
    owner._planner_phase_counts = {}
    owner._planning_status = Optimizer._planning_status.__get__(owner, Optimizer)
    return owner


def make_bridge(owner, library=None):
    bridge = desktop.Bridge.__new__(desktop.Bridge)
    bridge._optimizer = owner
    bridge._library = Path(library or owner.path)
    bridge._status_path = '/__optimizer_status/' + secrets.token_urlsafe(32)
    return bridge


def merged_body(body):
    envelope = json.loads(body)
    return {**envelope['base'], **envelope['live']}


class StatusTransportTests(unittest.TestCase):
    def test_transport_bootstrap_fallback_matches_legacy_status_for_empty_reports(self):
        encounter_bootstrap = {
            'source': 'encounter bootstrap',
            'encounters': [{'encounter': 19, 'enabled': True}],
        }
        campaign_bootstrap = {
            'source': 'campaign bootstrap',
            'campaigns': [{'id': 'fixture-campaign'}],
        }
        for encounter_report in (None, {}):
            with self.subTest(encounter_report=encounter_report):
                owner = make_owner()
                owner.snapshot.pop('campaign', None)
                owner.snapshot.pop('focusEncounters', None)
                owner._encounter_report = encounter_report
                owner._campaign_report = None
                bridge = make_bridge(owner)

                with mock.patch.object(strategy_optimizer.encounter_search,
                                       'bootstrap_report',
                                       return_value=encounter_bootstrap) as encounter_bootstrap_mock, \
                     mock.patch.object(strategy_optimizer.community_campaign,
                                       'bootstrap_status',
                                       return_value=campaign_bootstrap) as campaign_bootstrap_mock:
                    bridge.status_transport()
                    direct = bridge.status()

                self.assertEqual(encounter_bootstrap_mock.call_count, 2)
                self.assertEqual(campaign_bootstrap_mock.call_count, 2)
                with mock.patch.object(strategy_optimizer.encounter_search,
                                       'bootstrap_report',
                                       side_effect=AssertionError('HTTP transport read encounter evidence')), \
                     mock.patch.object(strategy_optimizer.community_campaign,
                                       'bootstrap_status',
                                       side_effect=AssertionError('HTTP transport read campaign evidence')):
                    actual = merged_body(bridge._status_transport_body())

                self.assertEqual(actual, direct)
                self.assertEqual(actual['campaign'], campaign_bootstrap)
                self.assertEqual(actual['encounterAware'], encounter_bootstrap)
                self.assertEqual(actual['focusEncounters'], [])

    def test_descriptor_and_http_get_match_independent_bridge_snapshot(self):
        owner = make_owner()
        bridge = make_bridge(owner)
        route = bridge.status_transport()['path']
        self.assertEqual(route, bridge._status_path)
        snapshot = owner.snapshot
        wire = owner._snapshot_wire_json
        before = json.loads(json.dumps(owner.snapshot))

        with tempfile.TemporaryDirectory() as directory:
            server = ThreadingHTTPServer(('127.0.0.1', 0),
                                         desktop.asset_handler(Path(directory), bridge))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                # A steady GET must not pickle/clone the complete snapshot or re-encode its base.
                with mock.patch('pickle.dumps', side_effect=AssertionError('unexpected status clone')):
                    with mock.patch.object(strategy_optimizer.encounter_search, 'bootstrap_report',
                                           side_effect=AssertionError('GET attempted an evidence read')):
                        with mock.patch.object(strategy_optimizer.community_campaign,
                                               'bootstrap_status',
                                               side_effect=AssertionError('GET attempted a database read')):
                            url = f'http://127.0.0.1:{server.server_port}{route}'
                            with urllib.request.urlopen(url, timeout=5) as response:
                                self.assertEqual(response.status, 200)
                                self.assertEqual(response.headers['Cache-Control'], 'no-store')
                                actual = merged_body(response.read())
                direct = bridge.status()
                self.assertEqual(actual, direct)
                self.assertIs(owner.snapshot, snapshot)
                self.assertIs(owner._snapshot_wire_json, wire)
                self.assertEqual(owner.snapshot, before)
                self.assertGreater(len(actual['candidates']), 0)
                self.assertEqual(actual['planningHost']['requestedWorkers'], 24)
                self.assertEqual(actual['planningHost']['effectiveBattleWorkers'] +
                                 actual['planningHost']['plannerWorkers'], 24)

                # The bridge API still returns an independent nested snapshot for commands/exports.
                direct['candidates'].append({'testMutation': True})
                direct['encounterAware']['testMutation'] = True
                self.assertNotIn('testMutation', owner.snapshot['candidates'][-1])
                self.assertNotIn('testMutation', owner._encounter_report)

                wrong_route = '/__optimizer_status/' + secrets.token_urlsafe(32)
                try:
                    urllib.request.urlopen(
                        f'http://127.0.0.1:{server.server_port}{wrong_route}', timeout=5)
                    self.fail('an unguessable status nonce should not resolve')
                except urllib.error.HTTPError as missing:
                    with missing:
                        self.assertEqual(missing.code, 404)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(5)

    def test_lazy_sync_encoding_happens_once_and_live_state_overlays_old_base(self):
        owner = make_owner(state='Paused')
        owner._snapshot_wire_json = None
        bridge = make_bridge(owner)
        bridge.status_transport()
        encode_count = 0
        real_encode = owner._encode_status_wire

        def counted(snapshot):
            nonlocal encode_count
            encode_count += 1
            return real_encode(snapshot)

        owner._encode_status_wire = counted
        first_base, first_live = owner.status_transport_data()
        second_base, second_live = owner.status_transport_data()
        self.assertEqual(encode_count, 1)
        self.assertIs(first_base, second_base)
        self.assertEqual(first_live, second_live)

        # Paused, Error and publisher-failure metadata can change without replacing a huge base.
        with owner.lock:
            owner.snapshot = dict(owner.snapshot, state='Paused', focusEncounter=27,
                                  publicationPending=True)
        self.assertEqual(merged_body(bridge._status_transport_body()), bridge.status())
        owner._fail_publication(7, 'fixture publication failure')
        with owner.lock:
            owner.snapshot = dict(owner.snapshot, state='Error', error='fixture error')
        self.assertEqual(merged_body(bridge._status_transport_body()), bridge.status())
        self.assertEqual(owner._snapshot_wire_json, first_base)

    def test_stale_epoch_cannot_replace_paired_view_or_wire(self):
        owner = make_owner(state='Paused')
        before_snapshot, before_wire = owner.snapshot, owner._snapshot_wire_json
        owner._accept_publication(6, ProjectedView({'state': 'Running'}, b'{"state":"Running"}'), .25)
        self.assertIs(owner.snapshot, before_snapshot)
        self.assertIs(owner._snapshot_wire_json, before_wire)

        view = {'state': 'Running', 'campaign': {'revision': 'new'}, 'focusEncounters': [19]}
        wire = Optimizer._encode_status_wire(view)
        owner._accept_publication(7, ProjectedView(view, wire), .25)
        self.assertEqual(owner.snapshot['state'], 'Running')
        self.assertIs(owner._snapshot_wire_json, wire)
        self.assertEqual({key: owner.snapshot[key] for key in view}, view)
        self.assertFalse(owner.snapshot['publicationPending'])
        self.assertEqual(owner.snapshot['publicationSeconds'], .25)
        self.assertGreater(owner.snapshot['publishedAt'], 0)

    def test_library_switch_replaces_owner_and_mid_read_switch_fails_closed(self):
        class FakeThread:
            def join(self):
                pass

        class FakeOptimizer:
            def __init__(self):
                self.thread = FakeThread()
                self.closed = False

            def command(self, action):
                self.closed = action == 'close'

        class FakeGuard:
            def close(self):
                pass

        old_owner = FakeOptimizer()
        new_path = Path(tempfile.gettempdir()) / 'status-transport-switched.sqlite'
        new_owner = make_owner(state='Paused', path=new_path)
        new_guard = FakeGuard()
        bridge = make_bridge(old_owner, Path(tempfile.gettempdir()) / 'status-transport-old.sqlite')
        bridge._guard = FakeGuard()
        bridge._remember = False
        with mock.patch.object(desktop, 'LibraryLock', return_value=new_guard), \
             mock.patch.object(desktop, 'Optimizer', return_value=new_owner):
            result = bridge._switch_library(new_path)
        self.assertTrue(result['ok'])
        self.assertIs(bridge._optimizer, new_owner)
        self.assertTrue(old_owner.closed)
        self.assertEqual(merged_body(bridge._status_transport_body())['library'], str(new_path))
        self.assertEqual(merged_body(bridge._status_transport_body())['state'], 'Paused')

        racing = make_owner(state='Running')
        bridge._optimizer = racing
        def switch_during_read():
            bridge._optimizer = new_owner
            return racing._snapshot_wire_json, {'state': 'Running'}
        racing.status_transport_data = switch_during_read
        with self.assertRaisesRegex(RuntimeError, 'changed during status read'):
            bridge._status_transport_body()

    def test_fixture_benchmark_reports_payload_and_steady_poll_cost(self):
        owner = make_owner()
        bridge = make_bridge(owner)
        bridge.status_transport()
        full_times, wire_times = [], []
        for _ in range(5):
            started = time.perf_counter()
            for _ in range(40):
                bridge.status()
            full_times.append((time.perf_counter()-started)/40)
            started = time.perf_counter()
            for _ in range(40):
                bridge._status_transport_body()
            wire_times.append((time.perf_counter()-started)/40)
        body = bridge._status_transport_body()
        envelope = json.loads(body)
        print('STATUS_TRANSPORT_BENCHMARK', json.dumps(dict(
            fixture=str(FIXTURE), candidates=len(owner.snapshot.get('candidates', [])),
            baseBytes=len(owner._snapshot_wire_json),
            liveBytes=len(json.dumps(envelope['live'], separators=(',', ':')).encode('utf-8')),
            responseBytes=len(body), fullStatusMedianMs=round(median(full_times)*1000, 3),
            transportMedianMs=round(median(wire_times)*1000, 3),
            speedup=round(median(full_times)/median(wire_times), 2)), sort_keys=True))


if __name__ == '__main__':
    unittest.main(verbosity=2)
