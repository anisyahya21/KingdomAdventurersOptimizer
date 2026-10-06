"""Focused regression for opt-in battle-timing tracing in the encounter evaluator.

Run:  <python> -B -X utf8 tools/recovery/check_optimizer_battle_trace.py

Deterministic; in-memory ledger only; a stub pool stands in for the persistent workers, so no native
DLL and no production database are required. Prints PASS/FAIL.
"""
from concurrent.futures import Future
import io
import json
import os
import queue
import shutil
import sqlite3
import threading
import time
import types
import uuid

import strategy_experiment_store as ledger
import strategy_encounter_evaluation as evaluation
import strategy_optimizer_fast as fast


#: None of these may ever appear in a persisted outcome. ('timing' is an EXISTING canonical ledger
#: field, so it is deliberately not listed here.)
TRACE_KEYS = ('schema', 'katrace', 'trace', '_trace', 'worker', 't', 'host')
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))

#: Absolute worker-side monotonic boundaries added to a sampled trace (perf_counter seconds). An
#: untraced response must carry none of these.
WORKER_ABS_FIELDS = ('receiveAt', 'parseBeginAt', 'parseEndAt', 'simulationBeginAt',
                     'nativeBeginAt', 'nativeEndAt', 'convertBeginAt', 'convertEndAt',
                     'resultReadyAt', 'serializeBeginAt', 'serializeEndAt',
                     'responsePrefixFlushAt')
NEW_WORKER_FIELDS = WORKER_ABS_FIELDS + ('cpuSecondsScope',)

#: Distinct stand-ins so the regression proves the launcher PID and the worker interpreter PID are
#: never conflated. `STUB_INTERPRETER_PID` is what a worker's own os.getpid() would report; the pool
#: launcher's Popen PID is a different value.
STUB_INTERPRETER_PID = 4242
STUB_LAUNCHER_PID = 4243


class Traced(dict):
    """A stub worker result carrying one non-serialised timing attribute."""
    katrace = None


class StubPool:
    """A deterministic stand-in for the persistent workers (no processes, no native DLL).

    It advertises `supports_job_trace` and mirrors the real pool: only a job the evaluator marks as
    sampled carries a trace request/response. An unsampled job takes the normal path (no katrace).
    """

    supports_job_trace = True

    def __init__(self):
        self.calls = []
        self.trace_flags = []
        self._submitted = 0
        self._finished = 0

    def submit(self, fn, scenario, seeds, trace=False):
        write_at = time.perf_counter()
        self._submitted += 1
        self._finished += 1
        self.trace_flags.append(bool(trace))
        result = Traced(seeds=list(seeds), verdict=1, resultBackend='native',
                        rewardOutcome={'pendingChests': 5}, elapsedSeconds=0.02, cpuSeconds=0.01,
                        digest='stub-digest')
        if trace:
            # Absolute worker-side stamps, generated in the same monotonic order a real worker sees,
            # so the regression can order them against the host perf_counter stages.
            # Anchored a little in the past so the host response-read stamps taken afterwards are
            # always later, exactly as when a worker flushes before the host observes the line.
            ticks = {'t': time.perf_counter() - 0.02}

            def tick():
                value = ticks['t']
                ticks['t'] += 1e-6
                return value

            result.katrace = {
                'workerPid': STUB_INTERPRETER_PID, 'workerProcessPid': STUB_INTERPRETER_PID,
                'workerLauncherPid': STUB_LAUNCHER_PID,
                'requestBytes': 1234, 'scenarioBytes': 900,
                'pipeWriteAt': write_at,
                'receiveAt': tick(), 'parseBeginAt': tick(), 'parseEndAt': tick(),
                'simulationBeginAt': tick(),
                'nativeBeginAt': tick(), 'nativeEndAt': tick(),
                'convertBeginAt': tick(), 'convertEndAt': tick(),
                'resultReadyAt': tick(),
                'serializeBeginAt': tick(), 'serializeEndAt': tick(),
                'responsePrefixFlushAt': tick(),
                'parseSeconds': 0.0005, 'prepareSeconds': 0.001, 'seedSeconds': 0.0002,
                'nativeSeconds': 0.015, 'extractSeconds': 0.0001, 'freeSeconds': 0.0001,
                'convertSeconds': 0.0004, 'serializeSeconds': 0.0002, 'simulateSeconds': 0.016,
                'totalSeconds': 0.017, 'cpuSeconds': 0.01, 'nativePath': True,
                'cpuSecondsScope': 'stub: simulation/evaluator call only',
                'waitSeconds': 0.001, 'idleSeconds': 0.0005}
            # Keep the duration fields exactly consistent with the absolute stamps (as the real
            # worker does), so the regression can cross-check one against the other.
            kt = result.katrace
            kt['parseSeconds'] = kt['parseEndAt'] - kt['parseBeginAt']
            kt['nativeSeconds'] = kt['nativeEndAt'] - kt['nativeBeginAt']
            kt['convertSeconds'] = kt['convertEndAt'] - kt['convertBeginAt']
            kt['serializeSeconds'] = kt['serializeEndAt'] - kt['serializeBeginAt']
            kt['simulateSeconds'] = kt['resultReadyAt'] - kt['simulationBeginAt']
            kt['totalSeconds'] = kt['resultReadyAt'] - kt['receiveAt']
            result.katrace['pipeReceiveAt'] = time.perf_counter()
            result.katrace['futureAvailableAt'] = time.perf_counter()
        future = Future()
        future.set_result(result)
        self.calls.append((future, scenario, seeds))
        return future

    def snapshot(self):
        return {'submitted': self._submitted, 'started': self._submitted,
                'finished': self._finished, 'queued': 0, 'executing': 0, 'available': 2}

    def shutdown(self, wait=True):
        pass

    def terminate_workers(self):
        pass


def new_ledger():
    db = sqlite3.connect(':memory:')
    ledger.initialize(db)
    return db


def intent(budget):
    return {'scope': 'community', 'purpose': 'improvement', 'planned_budget': budget,
            'stopping': 'finite', 'ownerShare': 1, 'policy': {'finishPolicy': 'on-verdict'},
            'expectedMechanicalDifferences': {'test': 1}}


def check(name, condition, detail=''):
    print(('PASS ' if condition else 'FAIL ') + name + ((' :: ' + str(detail)) if detail else ''))
    return bool(condition)


def read_records(path):
    with open(path, encoding='utf-8') as handle:
        return [json.loads(line) for line in handle if line.strip()]


def outcome_payload(db, experiment):
    return json.dumps([row['outcome'] for row in ledger.outcomes(db, experiment)], sort_keys=True)


def state_label_ok(rec):
    """`poolExecuting` must be labelled as a pool request/thread count, with occupancy derived."""
    at_submit = ((rec.get('state') or {}).get('atSubmit') or {})
    derived = rec.get('derived') or {}
    return (at_submit.get('poolExecutingMeaning')
            == 'pool request/thread active count (not native compute)'
            and 'nativeComputeOccupancy' in derived
            and 'not poolExecuting' in str(derived.get('nativeComputeOccupancyBasis')))


def host_abs(rec, name):
    """Host absolute perf_counter for a stage, reconstructed from the origin plus its ms offset."""
    value = (rec.get('t') or {}).get(name)
    if value is None or rec.get('originAt') is None:
        return None
    return rec['originAt'] + value / 1000.0


def stored_result_digests(db, experiment):
    """The persisted compact result digest for every completed sample of an experiment."""
    rows = db.execute(
        'SELECT s.result FROM ea_sample_link l JOIN ea_sample s ON s.sample_key=l.sample_key '
        'WHERE l.experiment_id=? AND s.result IS NOT NULL '
        'ORDER BY l.candidate_id, l.seed_a, l.seed_b', (experiment,)).fetchall()
    return [json.loads(row[0]).get('digest') for row in rows]


def has_new_worker_fields(value):
    """True when a (plain) result mapping carries any worker-absolute trace field."""
    return isinstance(value, dict) and any(name in value for name in NEW_WORKER_FIELDS)


def run_receive_boundary_check():
    """Bug 1: `received` is stamped AFTER the blocking read, so the idle wait is never charged to
    parse/prep/simulation; the inter-job idle uses the previous response-written boundary."""

    class FakeClock:
        def __init__(self):
            self.t = 1000.0

        def __call__(self):
            return self.t

        def advance(self, dt):
            self.t += dt

    class FakeStdin:
        def __init__(self, clock, lines):
            self.clock = clock
            self.lines = list(lines)

        def readline(self, limit):
            self.clock.advance(5.0)  # the blocking wait
            return self.lines.pop(0) if self.lines else ''

    class Sink:
        def __init__(self):
            self._buffer = ''
            self.lines = []

        def write(self, text):
            # A real pipe delivers bytes; readline yields one complete line. Buffer and split so a
            # traced response written in two chunks (body, then the post-flush token + tail + '\n')
            # is still observed as ONE line.
            self._buffer += text
            while '\n' in self._buffer:
                line, _, self._buffer = self._buffer.partition('\n')
                self.lines.append(line + '\n')

        def flush(self):
            pass

    def simulate(scenario, seeds, timing=None, telemetry=False):
        return {'seeds': list(seeds), 'verdict': 1, 'resultBackend': 'native',
                'rewardOutcome': {'pendingChests': 1}, 'cpuSeconds': 0.0}

    clock = FakeClock()
    request = json.dumps({'scenario': {}, 'seeds': [1, 2], 'provenance': 'D',
                          'telemetry': False, 'trace': True}) + '\n'
    stdin = FakeStdin(clock, [request, request])
    out = Sink()
    fast.serve(stdin, out, digest='D', simulate=simulate, accelerated=[], backend='test',
               clock=clock)
    records = [json.loads(line) for line in out.lines if line.strip()]
    first = records[0]['trace'] if records else {}
    second = records[1]['trace'] if len(records) > 1 else {}
    ok = True
    ok &= check('receive-after-read: parse excludes idle wait',
                first.get('waitSeconds') == 5.0 and first.get('parseSeconds') == 0.0
                and first.get('parseSeconds', 9) < first.get('waitSeconds', 0), first)
    ok &= check('first-job-has-no-interjob-idle', 'idleSeconds' not in first, first)
    ok &= check('interjob-idle-from-previous-response-boundary',
                second.get('idleSeconds') == 5.0, second)
    return ok


def run_native_timing_check():
    """Bug 2: `nativeSeconds` is strictly the single native call; seed/extract/free/convert are
    separate non-overlapping fields, native timing is only written on the native path, and the result
    digest is unchanged by collecting timing."""
    import strategy_optimizer_native as native

    class FakeClock:
        def __init__(self):
            self.t = 500.0

        def __call__(self):
            return self.t

        def advance(self, dt):
            self.t += dt

    class Report:
        def __init__(self):
            self.__dict__.update(verdict=1, own_present=True, own_hp=1, own_hp_max=2, ticks=3,
                                 pre_verdict_prize_callbacks=0, survivors=1, resource_uses=0,
                                 heals=0, attack_attempts=0, prize_callbacks=0,
                                 ending_confirmed=False, mp_watch_count=0, herb_log_count=0,
                                 progress_boss=-1, mp_identity=[], mp_min=[], mp_min_percent=[],
                                 mp_low=[], mp_first_low_tick=[], mp_first_low_phase=[], mp_zero=[],
                                 herb_use_tick=[], herb_use_phase=[], herb_use_source=[],
                                 herb_use_ok=[])

        def __getattr__(self, name):
            # Any unset numeric kernel counter reads as 0; the payload builder only reads scalars it
            # does not already receive from the explicit array fields above.
            return 0

    class FakeLib:
        object_capacity = 1_000_000
        _name = ''

        def __init__(self, clock):
            self.clock = clock
            self.status = 0

        def ka_battle_clone(self, handle):
            return object()

        def ka_battle_seed_rng(self, clone, a, b):
            self.clock.advance(1.0)

        def ka_battle_skip_lib_draws(self, clone, n):
            self.clock.advance(0.5)

        def ka_run_battle(self, clone, tick_limit, policy, report_ref):
            self.clock.advance(2.0)
            return self.status

        def ka_battle_free(self, clone):
            self.clock.advance(0.5)

        def ka_object_count(self, clone):
            return 0

    clock = FakeClock()
    lib = FakeLib(clock)
    saved = {name: getattr(native, name) for name in
             ('_clock', 'eligibility', 'scenario_key', 'library', '_template',
              '_library_identity', '_reward_outcome', 'ctypes', 'combat_progress', 'ka_abi')}
    saved_report = native.ka_abi.KaBattleReport
    try:
        native._clock = clock
        native.eligibility = lambda scenario: (True, None)
        native.scenario_key = lambda scenario: 'k'
        native.library = lambda: lib
        identity = ('fake', None, ())
        native._library_identity = lambda library: identity
        native._template = lambda scenario, key: {
            'handle': object(), 'library_identity': identity, 'tick_limit': 10, 'policy': 0,
            'follower_draws': 0, 'scope': None, 'watch_names': []}
        native._reward_outcome = lambda report, scope: {}
        native.ctypes = types.SimpleNamespace(byref=lambda value: value)
        native.combat_progress = types.SimpleNamespace(mp_metrics=lambda *a, **k: {},
                                                       herb_metrics=lambda *a, **k: {})
        native.ka_abi = types.SimpleNamespace(KaBattleReport=Report, KA_MAX_MP_WATCH=4,
                                              KA_MAX_HERB_USES=8)

        import strategy_optimizer_adapter as adapter

        collect = {}
        compact, reason = native._native_compact({}, (1, 2), telemetry=False, timing=collect)
        ok = True
        ok &= check('native-path-succeeds', compact is not None and reason is None, reason)
        ok &= check('native-seconds-strictly-around-call',
                    collect.get('nativeSeconds') == 2.0 and collect.get('seedSeconds') == 1.0
                    and collect.get('freeSeconds') == 0.5 and collect.get('prepareSeconds') == 0.0
                    and collect.get('extractSeconds') == 0.0
                    and collect.get('convertSeconds') == 0.0, collect)
        ok &= check('native-stages-nonnegative',
                    all(collect.get(name, 0.0) >= 0.0 for name in
                        ('prepareSeconds', 'seedSeconds', 'nativeSeconds', 'extractSeconds',
                         'freeSeconds', 'convertSeconds')))
        # Digest unchanged: the same native run without a timing collector produces one digest.
        plain, _ = native._native_compact({}, (1, 2), telemetry=False, timing=None)
        ok &= check('native-digest-unchanged-by-timing',
                    plain is not None and compact.get('digest') == plain.get('digest'))
        # A failed native run must leave the collector clean (no stale native interval) so a later
        # Python fallback cannot be mislabelled.
        lib.status = 1
        failed_timing = {}
        failed, why = native._native_compact({}, (1, 2), telemetry=False, timing=failed_timing)
        ok &= check('failed-native-leaves-no-native-timing',
                    failed is None and why == 'run' and failed_timing == {}, (why, failed_timing))
        # The Python path never reports a native interval.
        python_timing = {}
        adapter.simulate(adapter.default_scenario(), (1, 2), backend='python',
                         timing=python_timing)
        ok &= check('python-path-has-no-native-timing',
                    python_timing.get('nativePath') is False
                    and 'nativeSeconds' not in python_timing, python_timing)
        return ok
    finally:
        for name, value in saved.items():
            setattr(native, name, value)
        native.ka_abi.KaBattleReport = saved_report


def run_pool_run_check():
    """Bugs 4 and 5: the real pool only marks a sampled request, stamps the parent Future-available
    boundary after the response is read and parsed, and keeps the launcher Popen PID distinct from
    the worker interpreter PID the worker reports for itself."""
    from strategy_optimizer_fast import HeadlessPool

    interpreter_pid = 8765

    class FakeProcess:
        def __init__(self, response_line):
            self.pid = STUB_LAUNCHER_PID
            self.stdin = io.StringIO()
            self.stdout = io.StringIO(response_line + '\n')

    response = json.dumps({
        'result': {'seeds': [1, 2], 'verdict': 1, 'resultBackend': 'native'},
        'trace': {'workerPid': interpreter_pid, 'workerProcessPid': interpreter_pid,
                  'nativePath': True}})

    def make_pool(fake):
        pool = object.__new__(HeadlessPool)
        pool.trace = True
        pool.telemetry = False
        pool.digest = 'D'
        pool.processes = []
        pool.available = queue.Queue()
        pool.available.put(fake)
        pool._counters = {'submitted': 0, 'started': 0, 'finished': 0}
        pool._counter_lock = threading.Lock()
        pool.executor = None
        return pool

    sampled_process = FakeProcess(response)
    sampled = make_pool(sampled_process)._run({'a': 1}, [1, 2], True)
    sent = sampled_process.stdin.getvalue()
    katrace = getattr(sampled, 'katrace', None) or {}
    ok = check('sampled-request-carries-trace-marker', '"trace":true' in sent, sent)
    ok &= check('parent-future-available-after-pipe-receive',
                katrace.get('futureAvailableAt') is not None
                and katrace['futureAvailableAt'] >= katrace['pipeReceiveAt'], katrace)
    ok &= check('pool-run-keeps-interpreter-worker-pid',
                katrace.get('workerPid') == interpreter_pid
                and katrace.get('workerProcessPid') == interpreter_pid, katrace)
    ok &= check('pool-run-names-launcher-separately',
                katrace.get('workerLauncherPid') == STUB_LAUNCHER_PID
                and katrace.get('workerLauncherPid') != katrace.get('workerPid'), katrace)

    plain_process = FakeProcess(response)
    plain = make_pool(plain_process)._run({'a': 1}, [1, 2], False)
    ok &= check('unsampled-request-has-no-trace-marker',
                '"trace"' not in plain_process.stdin.getvalue(), plain_process.stdin.getvalue())
    ok &= check('unsampled-result-has-no-katrace', getattr(plain, 'katrace', None) is None)
    return ok


def run_coordinator_admission_check():
    """Bug 3: the Coordinator stamps the runnable decision before the evaluator's submit entry; a
    non-tracing evaluator keeps the unchanged submit signature."""
    import strategy_encounter_search as search

    class FakeClock:
        def __init__(self):
            self.t = 0.0

        def __call__(self):
            self.t += 1.0
            return self.t

    class TracedEvaluator:
        tracing = True

        def __init__(self, clock):
            self.clock = clock
            self.calls = []

        def submit(self, db, experiment_id, candidate_id, scenario, pair, *, runnable_at=None):
            self.calls.append({'runnable_at': runnable_at, 'entry': self.clock()})
            return True

    class PlainEvaluator:
        def __init__(self):
            self.calls = []

        def submit(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            return True

    current = {'experimentId': 'e1', 'candidateId': 'c1',
               'jobs': [{'candidateId': 'c1', 'seedPair': [1, 2]}],
               'observedCandidate': {}, 'observedReference': {}}

    def prepare(coordinator, evaluator):
        coordinator.evaluator = evaluator
        coordinator._hydrate_current = lambda db, item: True
        coordinator._experiment_state = lambda db, exp: {'complete': False, 'remaining': 1}
        coordinator._submission_capacity = lambda: 4
        coordinator._reserved_jobs = lambda db, exp: set()
        coordinator._current_compatible = lambda item: (True, None)
        coordinator._record_submission_limit = lambda capacity, ready: None

    clock = FakeClock()
    coordinator = search.Coordinator(path=os.path.join(HERE, 'fake-battle-trace'),
                                     revision='check', workers=2, telemetry=False)
    traced = TracedEvaluator(clock)
    prepare(coordinator, traced)
    dispatched = coordinator._dispatch_member(None, current)
    ok = check('coordinator-dispatches', dispatched is True and len(traced.calls) == 1)
    call = traced.calls[0] if traced.calls else {}
    ok &= check('coordinator-runnable-before-evaluator-submit',
                call.get('runnable_at') is not None and call.get('entry') is not None
                and call['runnable_at'] < call['entry'], call)

    plain_coordinator = search.Coordinator(path=os.path.join(HERE, 'fake-battle-trace-plain'),
                                           revision='check', workers=2, telemetry=False)
    plain = PlainEvaluator()
    prepare(plain_coordinator, plain)
    plain_coordinator._dispatch_member(None, current)
    args, kwargs = plain.calls[0] if plain.calls else ((), {})
    ok &= check('non-tracing-evaluator-keeps-submit-signature',
                len(args) == 5 and kwargs == {}, (args, kwargs))
    return ok


def run_failed_replacement_check(tmp):
    """A replacement pool submit that RAISES must never be recorded as a real replacement.

    The prior completed sampled row keeps its slot's next-submit boundary unproven, so it stays held
    and close flushes it with the replacement left `unknown` -- no fabricated replacement interval for
    a submission the pool never accepted."""

    class FailingReplacePool(StubPool):
        def __init__(self):
            super().__init__()
            self.fail_next = False

        def submit(self, fn, scenario, seeds, trace=False):
            if self.fail_next:
                self.trace_flags.append(bool(trace))
                raise RuntimeError('simulated replacement pool submit failure')
            return super().submit(fn, scenario, seeds, trace=trace)

    path = os.path.join(tmp, 'failed-replace.jsonl')
    configure_trace(path, 1)
    db = new_ledger()
    experiment = ledger.create_experiment(db, intent(2))
    pool = FailingReplacePool()
    runner = evaluation.Evaluator(workers=1, pool=pool)
    runner.encounter_id = 9
    runner.submit(db, experiment, 'cand', {}, [1, 2])   # seq 0 -> sampled
    runner.harvest(db)
    ok = check('failed-replace: prior row held pending',
               sum(len(rows) for rows in runner._trace_pending.values()) == 1)
    pool.fail_next = True
    raised = False
    try:
        runner.submit(db, experiment, 'cand', {}, [3, 4])
    except RuntimeError:
        raised = True
    ok &= check('failed-replace: submit failure propagates', raised)
    ok &= check('failed-replace: row still pending after failed submit',
                sum(len(rows) for rows in runner._trace_pending.values()) == 1)
    runner.close()
    records = read_records(path)
    ok &= check('failed-replace: flushed unknown, not replaced',
                len(records) == 1
                and (records[0].get('replacement') or {}).get('status') == 'unknown'
                and 'replacementSubmit' not in (records[0].get('t') or {}),
                records)
    return ok


def configure_trace(env_path, sample):
    if env_path is None:
        os.environ.pop(fast.TRACE_ENV, None)
    else:
        os.environ[fast.TRACE_ENV] = env_path
        os.environ[fast.TRACE_SAMPLE_ENV] = str(sample)
    fast.reset_battle_trace()


def run_flow(env_path, sample, *, workers, pairs, encounter_id=7):
    configure_trace(env_path, sample)
    db = new_ledger()
    experiment = ledger.create_experiment(db, intent(len(pairs)))
    runner = evaluation.Evaluator(workers=workers, pool=StubPool())
    runner.encounter_id = encounter_id
    for pair in pairs:
        runner.submit(db, experiment, 'candidate', {}, pair)
    completed = runner.harvest(db)
    runner.close()
    return db, experiment, completed, runner


def main():
    ok = True

    # 1. Disabled by default: no sink, a plain persisted result, and no trace file.
    db_off, exp_off, done_off, runner_off = run_flow(None, 1, workers=1, pairs=[(1, 2)])
    ok &= check('disabled-no-sink', runner_off._trace is None)
    ok &= check('disabled-persists-plain-result',
                len(done_off) == 1 and type(done_off[0]['result']) is dict)
    ok &= check('disabled-result-has-no-trace-fields',
                not has_new_worker_fields(done_off[0]['result']))
    ok &= check('disabled-outcome-canonical',
                ledger.outcomes(db_off, exp_off)[0]['outcome']['finalEarned'] == 5)
    off_payload = outcome_payload(db_off, exp_off)

    tmp = os.path.join(REPO, 'tmp', 'battle-trace-check-%s' % uuid.uuid4().hex[:8])
    os.makedirs(tmp, exist_ok=True)
    try:
        path = os.path.join(tmp, 'trace.jsonl')

        # 2. Enabled, sample 1: one ordered record; the ledger result is untouched.
        db_on, exp_on, done_on, runner_on = run_flow(path, 1, workers=1, pairs=[(1, 2)])
        ok &= check('enabled-outcome-identical', outcome_payload(db_on, exp_on) == off_payload)
        ok &= check('enabled-result-plain-dict', type(done_on[0]['result']) is dict)
        ok &= check('enabled-no-trace-keys-in-outcome',
                    not any(key in ledger.outcomes(db_on, exp_on)[0]['outcome'] for key in TRACE_KEYS))
        records = read_records(path)
        ok &= check('enabled-one-record', len(records) == 1)
        rec = records[0] if records else {}
        ok &= check('schema-key-versioned', rec.get('schema') == fast.TRACE_SCHEMA)
        host = rec.get('host') or {}
        ok &= check('host-metadata',
                    host.get('encounterId') == 7 and host.get('experimentId') == exp_on
                    and host.get('candidateId') == 'candidate' and host.get('seedA') == 1
                    and host.get('seedB') == 2 and host.get('backend') == 'native'
                    and isinstance(host.get('hostPid'), int))
        # The canonical worker PID is the interpreter's own PID; the launcher is named separately.
        ok &= check('host-worker-pid-is-interpreter',
                    host.get('workerPid') == STUB_INTERPRETER_PID, host.get('workerPid'))
        ok &= check('host-worker-launcher-pid-separate',
                    host.get('workerLauncherPid') == STUB_LAUNCHER_PID
                    and host.get('workerLauncherPid') != host.get('workerPid'),
                    (host.get('workerLauncherPid'), host.get('workerPid')))
        stages = rec.get('t') or {}
        chain = ('admission', 'submitBegin', 'poolSubmit', 'pipeWrite', 'pipeReceive',
                 'futureAvailable', 'harvestBegin', 'slotReleased', 'persistBegin', 'persistEnd')
        values = [stages.get(name) for name in chain]
        ok &= check('stage-timestamps-present', all(value is not None for value in values), values)
        ok &= check('stage-timestamps-ordered',
                    all(a <= b for a, b in zip(values, values[1:]))
                    if all(value is not None for value in values) else False, values)
        ok &= check('pipe-receive<=future-available<=harvest',
                    stages.get('pipeReceive') is not None
                    and stages.get('futureAvailable') is not None
                    and stages['pipeReceive'] <= stages['futureAvailable'] <= stages['harvestBegin'],
                    (stages.get('pipeReceive'), stages.get('futureAvailable'),
                     stages.get('harvestBegin')))
        worker = rec.get('worker') or {}
        ok &= check('worker-stages-present',
                    worker.get('nativePath') is True and worker.get('nativeSeconds') is not None
                    and worker.get('workerPid') == STUB_INTERPRETER_PID)
        ok &= check('worker-launcher-pid-in-timing',
                    worker.get('workerLauncherPid') == STUB_LAUNCHER_PID
                    and worker.get('workerPid') == STUB_INTERPRETER_PID, worker.get('workerLauncherPid'))
        ok &= check('worker-stage-durations-nonnegative',
                    all(value is None or value >= 0 for value in worker.values()
                        if isinstance(value, (int, float)) and not isinstance(value, bool)),
                    worker)
        ok &= check('pool-executing-labeled-not-native',
                    state_label_ok(rec))
        state = rec.get('state') or {}
        ok &= check('instantaneous-state',
                    isinstance(state.get('atSubmit'), dict)
                    and 'evaluatorPending' in state['atSubmit']
                    and 'poolQueueDepth' in state['atSubmit']
                    and 'completedUnharvested' in state['atSubmit'])
        ok &= check('no-trace-token-in-ledger-rows',
                    'katrace' not in json.dumps(ledger.outcomes(db_on, exp_on), sort_keys=True)
                    and 'ka-battle-trace' not in json.dumps(ledger.outcomes(db_on, exp_on),
                                                            sort_keys=True))

        # 2b. Worker absolute boundaries, cross-process ordering and native-occupancy derivation.
        ok &= check('clock-basis-declared',
                    isinstance(rec.get('clockBasis'), str) and 'perf_counter' in rec['clockBasis']
                    and isinstance(rec.get('originAt'), (int, float)), rec.get('clockBasis'))
        absolute = [worker.get(name) for name in WORKER_ABS_FIELDS]
        ok &= check('worker-absolute-stamps-present',
                    all(value is not None for value in absolute), absolute)
        ok &= check('worker-absolute-stamps-ordered',
                    all(a <= b for a, b in zip(absolute, absolute[1:])), absolute)
        ok &= check('worker-native-bracket-consistent',
                    abs(worker['nativeSeconds']
                        - (worker['nativeEndAt'] - worker['nativeBeginAt'])) < 1e-12)
        ok &= check('worker-prefix-flush-named-accurately',
                    'responsePrefixFlushAt' in worker and 'flushEndAt' not in worker,
                    sorted(worker))
        ok &= check('worker-prefix-flush-after-serialize-before-response-available',
                    worker['serializeEndAt'] <= worker['responsePrefixFlushAt']
                    <= host_abs(rec, 'futureAvailable'),
                    (worker['serializeEndAt'], worker['responsePrefixFlushAt'],
                     host_abs(rec, 'futureAvailable')))
        ok &= check('worker-receive-before-host-pipe-read',
                    worker['receiveAt'] <= host_abs(rec, 'pipeReceive'),
                    (worker['receiveAt'], host_abs(rec, 'pipeReceive')))
        derived = rec.get('derived') or {}
        ok &= check('native-occupancy-from-strict-native-bracket',
                    derived.get('assignedWallSeconds') is not None
                    and abs(derived.get('nativeCallSeconds', 0.0)
                            - (worker['nativeEndAt'] - worker['nativeBeginAt'])) < 1e-12
                    and abs(derived['nativeComputeOccupancy']
                            - derived['nativeCallSeconds'] / derived['assignedWallSeconds']) < 1e-12,
                    derived)
        assigned_from_stages = ((stages['harvestBegin'] - stages['poolSubmit']) / 1000.0
                                if stages.get('harvestBegin') is not None
                                and stages.get('poolSubmit') is not None else None)
        request_from_stages = ((stages['futureAvailable'] - stages['pipeWrite']) / 1000.0
                               if stages.get('futureAvailable') is not None
                               and stages.get('pipeWrite') is not None else None)
        ok &= check('assigned-occupancy-uses-desktop-submit-to-harvest-window',
                    derived.get('assignedWallBasis') == 'poolSubmitAt->harvestBegin'
                    and assigned_from_stages is not None
                    and abs(derived.get('assignedWallSeconds', 0.0) - assigned_from_stages) < 2e-6
                    and abs(derived.get('workerRequestWallSeconds', 0.0)
                            - request_from_stages) < 2e-6,
                    (derived, assigned_from_stages, request_from_stages))
        replacement = rec.get('replacement') or {}
        ok &= check('unreplaced-sample-marked-unknown',
                    replacement.get('status') == 'unknown'
                    and replacement.get('submitAbsolute') is None
                    and replacement.get('harvestToReplacementSeconds') is None
                    and replacement.get('persistToReplacementSeconds') is None
                    and 'replacementSubmit' not in stages, replacement)
        ok &= check('enabled-result-has-no-trace-fields',
                    not has_new_worker_fields(done_on[0]['result']))

        # 3. Deterministic sampling: 1-in-2 keeps submission ordinals 0 and 2.
        sample_path = os.path.join(tmp, 'sample.jsonl')
        db_s, exp_s, done_s, sample_runner = run_flow(sample_path, 2, workers=4,
                                                      pairs=[(1, 2), (3, 4), (5, 6), (7, 8)])
        sample_records = read_records(sample_path)
        ok &= check('sampling-keeps-every-second',
                    [record['host']['seq'] for record in sample_records] == [0, 2],
                    [record['host'].get('seq') for record in sample_records])
        ok &= check('trace-only-affects-selected-jobs',
                    sample_runner.pool.trace_flags == [True, False, True, False],
                    sample_runner.pool.trace_flags)
        ok &= check('sampled-and-unsampled-results-have-no-trace-fields',
                    all(not has_new_worker_fields(entry['result']) for entry in done_s))
        ok &= check('every-written-record-has-absolute-stamps',
                    all(all(record['worker'].get(name) is not None
                            for name in WORKER_ABS_FIELDS) for record in sample_records))

        # 4. Replacement metadata: the second job on one slot names the first.
        replace_path = os.path.join(tmp, 'replace.jsonl')
        configure_trace(replace_path, 1)
        db3 = new_ledger()
        exp3 = ledger.create_experiment(db3, intent(2))
        runner3 = evaluation.Evaluator(workers=1, pool=StubPool())
        runner3.encounter_id = 3
        runner3.submit(db3, exp3, 'cand', {}, [1, 2])
        runner3.harvest(db3)
        runner3.submit(db3, exp3, 'cand', {}, [3, 4])
        runner3.harvest(db3)
        runner3.close()
        replace_records = read_records(replace_path)
        ok &= check('replacement-metadata',
                    len(replace_records) == 2
                    and replace_records[1]['host'].get('replacesPriorSeq')
                    == replace_records[0]['host'].get('seq')
                    and replace_records[1]['host'].get('replacesPriorCandidate') == 'cand')

        # 4a. Cross-process ordering: worker receive -> native start/end -> host response available
        #     -> harvest -> persist -> the exact same-slot replacement submit.
        r0, r1 = replace_records
        chain = [r0['worker']['receiveAt'], r0['worker']['nativeBeginAt'],
                 r0['worker']['nativeEndAt'], host_abs(r0, 'futureAvailable'),
                 host_abs(r0, 'harvestBegin'), host_abs(r0, 'persistBegin'),
                 host_abs(r0, 'persistEnd'), host_abs(r0, 'replacementSubmit')]
        ok &= check('receive-native-avail-harvest-persist-replacement-ordered',
                    all(value is not None for value in chain)
                    and all(a <= b for a, b in zip(chain, chain[1:])), chain)
        rep0 = r0.get('replacement') or {}
        ok &= check('replacement-interval-derived-from-anchors',
                    rep0.get('status') == 'replaced'
                    and abs(rep0['harvestToReplacementSeconds']
                            - (rep0['submitAbsolute'] - host_abs(r0, 'harvestBegin'))) < 1e-3
                    and abs(rep0['persistToReplacementSeconds']
                            - (rep0['submitAbsolute'] - host_abs(r0, 'persistEnd'))) < 1e-3, rep0)
        ok &= check('last-unreplaced-sample-marked-unknown',
                    (r1.get('replacement') or {}).get('status') == 'unknown'
                    and 'replacementSubmit' not in (r1.get('t') or {})
                    and (r1.get('replacement') or {}).get('harvestToReplacementSeconds') is None)

        # 4c. The replacing battle being UNSAMPLED must still finalise the prior sampled row.
        mixed_path = os.path.join(tmp, 'mixed.jsonl')
        configure_trace(mixed_path, 2)
        db4 = new_ledger()
        exp4 = ledger.create_experiment(db4, intent(2))
        runner4 = evaluation.Evaluator(workers=1, pool=StubPool())
        runner4.encounter_id = 4
        runner4.submit(db4, exp4, 'cand', {}, [1, 2])   # seq 0 -> sampled
        runner4.harvest(db4)
        runner4.submit(db4, exp4, 'cand', {}, [3, 4])   # seq 1 -> unsampled replacement
        runner4.harvest(db4)
        runner4.close()
        mixed = read_records(mixed_path)
        ok &= check('unsampled-replacement-finalises-prior-row',
                    runner4.pool.trace_flags == [True, False]
                    and len(mixed) == 1
                    and (mixed[0].get('replacement') or {}).get('status') == 'replaced'
                    and (mixed[0].get('t') or {}).get('replacementSubmit') is not None,
                    (runner4.pool.trace_flags, len(mixed)))

        # 4d. The persisted compact digest is identical with tracing on and off.
        ok &= check('ledger-digest-unchanged-by-tracing',
                    stored_result_digests(db_on, exp_on) == stored_result_digests(db_off, exp_off))

        # 4b. Focused boundary regressions for the reviewed lifecycle bugs.
        ok &= run_receive_boundary_check()
        ok &= run_native_timing_check()
        ok &= run_pool_run_check()
        ok &= run_coordinator_admission_check()
        ok &= run_failed_replacement_check(tmp)

        # 5. The adapter timing hook fills the real worker stages on the pure-Python path and never
        #    changes the compact result. No native DLL is involved.
        import strategy_optimizer_adapter as adapter
        scenario = adapter.default_scenario()
        plain = adapter.simulate(scenario, (1, 2), backend='python')
        collected = {}
        measured = adapter.simulate(scenario, (1, 2), backend='python', timing=collected)
        ok &= check('adapter-timing-keeps-result',
                    plain.get('digest') == measured.get('digest'))
        ok &= check('adapter-python-stages-filled',
                    collected.get('nativePath') is False
                    and isinstance(collected.get('pythonComputeSeconds'), float)
                    and isinstance(collected.get('convertSeconds'), float)
                    and 'nativeSeconds' not in collected)
    finally:
        configure_trace(None, 1)
        shutil.rmtree(tmp, ignore_errors=True)

    print('ALL PASS' if ok else 'FAILURES PRESENT')
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
