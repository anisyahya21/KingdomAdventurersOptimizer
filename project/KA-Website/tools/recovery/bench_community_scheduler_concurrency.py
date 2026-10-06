"""Controlled native A/B benchmark for the Community encounter FLEET (pre-change vs new).

This harness compares two module roots, never a serial control against a concurrent one:

  arm A = the frozen pre-change fleet snapshot under
          tmp/optimizer-concurrency-checkpoint-20260928-175321/tools/recovery/ (an isolated copy)
  arm B = the current tools/recovery modules

Both arms are driven through the SAME real Optimizer fleet entry points
(Optimizer._campaign_start -> per-encounter Coordinators sharing one Evaluator ->
Optimizer._campaign_fleet_pass) with identical throwaway libraries, encounters, supplied baselines,
DEFAULT_PURPOSES budgets, duty, and native battle kernel. The matched four-cell matrix is:

  A-all    B-all    A-focus (one encounter)    B-focus (same encounter)

Timing is only attempted after a fixed-input correctness gate proves that both arms submit the
identical request/proposal/seed multiset and produce identical native battle-output digests for
that cell. That gate compares, in exact order, the stable request fields, the stable result fields,
and the native/fallback/errors/timeouts counters. The per-library ``experimentId`` is NOT compared
for correctness: it is each throwaway SQLite library autoincrement primary key (storage-local
identity assigned in creation order), so it is reported for diagnostics only and never gates the
run. If the gate cannot be shown to pass, or if the pre-change modules cannot be loaded in a
provably isolated copy, the run fails closed with the exact limitation instead of labelling arm A
"pre-change".

Safety: no live library, no live process, no desktop window is touched. Every library is a fresh
throwaway sqlite file under the checkpoint directory. No architecture or performance conclusion is
drawn: the report publishes raw counters only.

Run:  python tools/recovery/bench_community_scheduler_concurrency.py
      (a visible Tk window shows live progress; closing it hides the monitor, the run continues)
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import importlib.util
import json
import math
import os
import queue
import shutil
import signal
import statistics
import subprocess
import sys
import threading
import time
import traceback
from collections import deque
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

MODULE_IMPORT_T0 = time.perf_counter()

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
CHECKPOINT_DIR = ROOT / 'tmp' / 'optimizer-concurrency-checkpoint-20260928-175321'
CHECKPOINT_RECOVERY = CHECKPOINT_DIR / 'tools' / 'recovery'
CHECKPOINT_MANIFEST = CHECKPOINT_DIR / 'checkpoint.json'
REPORT_DIR = ROOT / 'tmp' / 'deepseek-concurrency-20260928'
REPORT_MD = REPORT_DIR / 'bench-result.md'
SCRIPT = Path(__file__).resolve()
RUN_TAG = datetime.now().strftime('%Y%m%d-%H%M%S')
DEFAULT_OUT = CHECKPOINT_DIR / 'benchmark-run' / RUN_TAG

#: Frozen resource layout: total T = 24 worker processes = planner P = 2 + battles B = 22.
PLANNER_WORKERS = 2
BATTLE_WORKERS = 22
TOTAL_WORKERS = PLANNER_WORKERS + BATTLE_WORKERS
DUTY = 1.0

WARMUP_SECONDS = 10.0
MEASURE_SECONDS = 60.0
GATE_PASSES = 2
GATE_DRAIN_TIMEOUT = 150.0
#: Bounded planner pump for the fixed-input gate. The current (arm B) optimizer plans
#: asynchronously on a shared planner pool, and the harness drives `_campaign_fleet_pass`
#: directly, so it must also stream the host's harvest/fan-out slices the real loop calls
#: before every pass. Each gate pass pumps until every submitted route is delivered, bounded
#: by a slice cap and a wall-clock deadline so a stalled planner can never hang the gate.
GATE_SLICE_CAP = 4096
GATE_PUMP_SECONDS = 60.0
GATE_SLICE_SLEEP = 0.005
#: The gate compares ONE fixed native cohort: exactly the first ``GATE_TARGET_COHORT`` native
#: results, i.e. the configured effective battle capacity (the B=22 battle workers). The gate
#: stops the moment that count is reached and fails closed on an overrun, a shortfall, or any
#: pump/pass/wall cap hit; it never overruns and slices results afterwards.
GATE_TARGET_COHORT = BATTLE_WORKERS
#: Bounded pass cap for producing that cohort. The synchronous arm needs one dispatch pass; the
#: async arm needs one planning-only pass plus one dispatch pass, so two passes is the budget.
GATE_COHORT_PASS_CAP = 2
#: Overall wall-clock cap for one arm's whole gate cohort attempt (setup excluded).
GATE_COHORT_SECONDS = 300.0
#: Async-arm fleet passes that submit planning but dispatch no native work before its first
#: cohort. Each such pass still advances the fleet's round-robin fair cursor, so in
#: all-encounter mode the synchronous arm's gate starts its fair cursor this many slots later so
#: both arms compare the same cohort. Focus mode has one encounter, so the cursor is a no-op.
GATE_PRIME_PASSES = 1
#: Stable, order-sensitive fields the fixed-input gate compares exactly. Only simulation inputs
#: live here; a request/result mismatch on any of these is a hard, fail-closed gate failure.
GATE_REQUEST_FIELDS = ('encounterId', 'candidateId', 'seedPair', 'scenarioDigest')
GATE_RESULT_FIELDS = ('candidateId', 'seedPair', 'digest', 'verdict', 'censored', 'backend')
#: ``experimentId`` is each arm's throwaway SQLite library autoincrement primary key. It is
#: assigned when the coordinator creates the experiment batch, so it tracks storage-local creation
#: order (synchronous encounter order on arm A, async planner-completion order on arm B) rather
#: than any simulation input. It is therefore reported for diagnostics only and is NOT a
#: correctness condition: its equality is never required by the gate.
GATE_SURROGATE_FIELDS = ('experimentId',)
MIN_FREE_GB = 4.0
MAX_TIMED_SECONDS = 600.0
WORKER_SAMPLE_SECONDS = 0.25
CHILD_WALL_TIMEOUT = 900.0
TREEKILL_TIMEOUT = 15.0
STAGE_NAMES = ('proposalSeconds', 'planningSeconds', 'recoverySeconds', 'portfolioSeconds',
               'dispatchSeconds')
PROGRESS_QUEUE = queue.Queue()
CREATION_FLAGS = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

PLAN_SCHEMA = 'community-fleet-ab-plan-1'
GATE_SCHEMA = 'community-fleet-ab-gate-1'
CELL_SCHEMA = 'community-fleet-ab-cell-1'


class BenchmarkSafetyError(RuntimeError):
    pass


class IsolationError(BenchmarkSafetyError):
    pass


# -- tiny utilities -------------------------------------------------------------------------------

def write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding='utf-8')


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode('utf-8')).hexdigest()


# -- host memory / owned-process sampling (no psutil) ---------------------------------------------

class _MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ('dwLength', ctypes.c_ulong), ('dwMemoryLoad', ctypes.c_ulong),
        ('ullTotalPhys', ctypes.c_ulonglong), ('ullAvailPhys', ctypes.c_ulonglong),
        ('ullTotalPageFile', ctypes.c_ulonglong), ('ullAvailPageFile', ctypes.c_ulonglong),
        ('ullTotalVirtual', ctypes.c_ulonglong), ('ullAvailVirtual', ctypes.c_ulonglong),
        ('ullAvailExtendedVirtual', ctypes.c_ulonglong),
    ]


class _FILETIME(ctypes.Structure):
    _fields_ = [('dwLowDateTime', ctypes.c_ulong), ('dwHighDateTime', ctypes.c_ulong)]


class _PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
    _fields_ = [
        ('cb', ctypes.c_ulong), ('PageFaultCount', ctypes.c_ulong),
        ('PeakWorkingSetSize', ctypes.c_size_t), ('WorkingSetSize', ctypes.c_size_t),
        ('QuotaPeakPagedPoolUsage', ctypes.c_size_t), ('QuotaPagedPoolUsage', ctypes.c_size_t),
        ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t), ('QuotaNonPagedPoolUsage', ctypes.c_size_t),
        ('PagefileUsage', ctypes.c_size_t), ('PeakPagefileUsage', ctypes.c_size_t),
        ('PrivateUsage', ctypes.c_size_t),
    ]


_WIN32 = None


def _win32():
    """Configure the Win32 calls once. Absent on a non-Windows host (which then fails closed)."""
    global _WIN32
    if _WIN32 is not None:
        return _WIN32
    if os.name != 'nt':
        _WIN32 = False
        return _WIN32
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    kernel32.GetProcessTimes.argtypes = [ctypes.c_void_p, ctypes.POINTER(_FILETIME),
                                         ctypes.POINTER(_FILETIME), ctypes.POINTER(_FILETIME),
                                         ctypes.POINTER(_FILETIME)]
    kernel32.GetProcessTimes.restype = ctypes.c_int
    memory_info = getattr(kernel32, 'K32GetProcessMemoryInfo', None)
    if memory_info is None:
        try:
            memory_info = ctypes.windll.psapi.GetProcessMemoryInfo
        except Exception:  # noqa: BLE001
            memory_info = None
    if memory_info is not None:
        memory_info.argtypes = [ctypes.c_void_p, ctypes.POINTER(_PROCESS_MEMORY_COUNTERS_EX),
                                ctypes.c_uint32]
        memory_info.restype = ctypes.c_int
    _WIN32 = dict(kernel32=kernel32, memoryInfo=memory_info)
    return _WIN32


def free_memory_bytes():
    api = _win32()
    if not api:
        return None
    status = _MEMORYSTATUSEX()
    status.dwLength = ctypes.sizeof(status)
    if not api['kernel32'].GlobalMemoryStatusEx(ctypes.byref(status)):
        return None
    return int(status.ullAvailPhys)


def total_memory_bytes():
    api = _win32()
    if not api:
        return None
    status = _MEMORYSTATUSEX()
    status.dwLength = ctypes.sizeof(status)
    if not api['kernel32'].GlobalMemoryStatusEx(ctypes.byref(status)):
        return None
    return int(status.ullTotalPhys)


def free_memory_gb():
    value = free_memory_bytes()
    return None if value is None else value / (1024 ** 3)


def require_memory_floor(where):
    value = free_memory_gb()
    if value is None:
        raise BenchmarkSafetyError('free memory could not be measured during %s; refusing to '
                                   'continue without the %.1f GiB floor' % (where, MIN_FREE_GB))
    if value < MIN_FREE_GB:
        raise BenchmarkSafetyError('free memory %.2f GiB is below the %.1f GiB floor during %s'
                                   % (value, MIN_FREE_GB, where))
    return value


def _filetime_seconds(value):
    return ((value.dwHighDateTime << 32) | value.dwLowDateTime) / 1e7


def sample_process(pid):
    """Actual CPU seconds and working set of one OWNED pid; None when the reading is unavailable."""
    api = _win32()
    if not api or not pid:
        return None
    handle = api['kernel32'].OpenProcess(0x1000, False, int(pid))
    if not handle:
        handle = api['kernel32'].OpenProcess(0x0400, False, int(pid))
    if not handle:
        return None
    try:
        creation, exit_time = _FILETIME(), _FILETIME()
        kern, user = _FILETIME(), _FILETIME()
        if not api['kernel32'].GetProcessTimes(handle, ctypes.byref(creation),
                                               ctypes.byref(exit_time), ctypes.byref(kern),
                                               ctypes.byref(user)):
            return None
        cpu_seconds = _filetime_seconds(kern) + _filetime_seconds(user)
        working_set = None
        private_bytes = None
        if api['memoryInfo'] is not None:
            counters = _PROCESS_MEMORY_COUNTERS_EX()
            counters.cb = ctypes.sizeof(counters)
            try:
                if api['memoryInfo'](handle, ctypes.byref(counters), counters.cb):
                    working_set = int(counters.WorkingSetSize)
                    private_bytes = int(counters.PrivateUsage)
            except Exception:  # noqa: BLE001
                working_set = None
        return dict(pid=int(pid), cpuSeconds=cpu_seconds, workingSetBytes=working_set,
                    privateBytes=private_bytes)
    finally:
        api['kernel32'].CloseHandle(handle)


def collect_owned_pids(parallel, evaluator):
    """Roles -> PIDs for processes THIS harness owns. Unrelated host jobs are never sampled."""
    roles = {'harness': [os.getpid()]}
    processes = getattr(getattr(evaluator, 'pool', None), 'processes', None) or []
    battle = [getattr(process, 'pid', None) for process in processes if getattr(process, 'pid', None)]
    if battle:
        roles['battle-worker'] = battle
    prep_pids = []
    prep_pool = getattr(parallel, '_POOL', None)
    if prep_pool is not None:
        for worker in getattr(prep_pool, '_workers', None) or []:
            pid = getattr(getattr(worker, 'proc', None), 'pid', None)
            if pid:
                prep_pids.append(pid)
    if prep_pids:
        roles['prep-worker'] = prep_pids
    planning_pids = []
    shared = getattr(parallel, '_SHARED_PLANNING_POOL', None)
    if shared is not None and callable(getattr(shared, 'status', None)):
        try:
            planning_pids = [int(value) for value in (shared.status().get('aliveWorkerPids') or [])]
        except Exception:  # noqa: BLE001
            planning_pids = []
    if planning_pids:
        roles['planning-worker'] = planning_pids
    return roles


# -- pre-change arm A: verified isolated copy -----------------------------------------------------

def load_checkpoint_manifest():
    if not CHECKPOINT_MANIFEST.is_file():
        raise IsolationError('checkpoint manifest is missing at %s' % CHECKPOINT_MANIFEST)
    payload = read_json(CHECKPOINT_MANIFEST)
    entries = [row['path'] for row in payload.get('files', [])
               if str(row.get('path', '')).startswith('tools/recovery/')]
    if not entries:
        raise IsolationError('checkpoint manifest lists no tools/recovery modules')
    return payload, entries


def verify_checkpoint_files():
    payload, entries = load_checkpoint_manifest()
    by_path = {row['path']: row for row in payload['files']}
    problems = []
    for rel in entries:
        path = CHECKPOINT_DIR / rel
        if not path.is_file():
            problems.append('missing ' + rel)
        elif sha256_file(path) != by_path[rel]['sha256']:
            problems.append('sha256 mismatch ' + rel)
    if problems:
        raise IsolationError('checkpoint integrity failed: ' + '; '.join(problems))
    return payload, entries


class _ArmFinder:
    """Serve exactly the preserved pre-change modules; defer every other import to the real tree."""

    def __init__(self, mapping):
        self.mapping = {name: Path(path) for name, path in mapping.items()}

    def find_spec(self, fullname, path=None, target=None):
        location = self.mapping.get(fullname)
        if location is None:
            return None
        if not location.is_file():
            raise IsolationError('preserved module %s is missing from the isolated copy at %s'
                                 % (fullname, location))
        return importlib.util.spec_from_file_location(fullname, location)


def prepare_arm_a_copy(run_out):
    """Build a verified, importable pre-change copy of the preserved checkpoint modules.

    Two directories are produced:

      * importRoot - ONLY the preserved pre-change modules. This is what the isolated finder
        serves, and the directory the pre-change prep-pool worker re-executes as its own script
        directory, so every non-preserved support module still resolves to the real current tree.
      * digestRoot - the preserved modules plus current copies of every other source file, because
        strategy_optimizer's import-time revision digest reads those sibling files by name from its
        own directory. digestRoot is NEVER placed on sys.path; only strategy_optimizer is loaded
        from it.

    Any integrity problem, or a checkpoint that is byte-identical to the current tree, fails closed
    instead of pretending arm A is pre-change.
    """
    payload, entries = verify_checkpoint_files()
    by_path = {row['path']: row for row in payload['files']}
    preserved = [Path(rel).name for rel in entries]
    base = Path(run_out) / '_isolated' / 'armA'
    import_dir = base / 'import'
    digest_dir = base / 'digest'
    for directory in (import_dir, digest_dir):
        if directory.exists():
            shutil.rmtree(directory)
        directory.mkdir(parents=True, exist_ok=True)
    for source in sorted(HERE.glob('*.py')):
        shutil.copy2(source, digest_dir / source.name)
    for rel in entries:
        name = Path(rel).name
        shutil.copy2(CHECKPOINT_DIR / rel, import_dir / name)
        shutil.copy2(CHECKPOINT_DIR / rel, digest_dir / name)
    problems = []
    for rel in entries:
        name = Path(rel).name
        for directory in (import_dir, digest_dir):
            copied = directory / name
            if not copied.is_file() or sha256_file(copied) != by_path[rel]['sha256']:
                problems.append(str(copied))
    if problems:
        raise IsolationError('isolated pre-change copy failed verification: ' + ', '.join(problems))
    unexpected = sorted(name for name in (entry.name for entry in import_dir.glob('*.py'))
                        if name not in set(preserved))
    if unexpected:
        raise IsolationError('the importable isolated copy contains non-preserved modules: '
                             + ', '.join(unexpected))
    changed, identical = [], []
    for rel in entries:
        name = Path(rel).name
        current = HERE / name
        if current.is_file() and sha256_file(current) == by_path[rel]['sha256']:
            identical.append(name)
        else:
            changed.append(name)
    if not changed:
        raise IsolationError('every preserved module is byte-identical to the current tree; arm A '
                             'would not be pre-change, so the comparison is refused')
    shared = sorted(entry.name for entry in HERE.glob('*.py') if entry.name not in set(preserved))
    return dict(
        importRoot=str(import_dir), digestRoot=str(digest_dir),
        manifestPath=str(CHECKPOINT_MANIFEST), preservedModules=preserved,
        identicalToCurrent=identical, changedVsCurrent=changed,
        sharedModulesFromCurrent=shared,
        limitation=('Only the %d preserved checkpoint modules are pre-change. strategy_optimizer is '
                    'loaded from a digest directory that must also hold current copies of the other '
                    'source files, because its import-time revision digest reads them by name; that '
                    'directory is never placed on sys.path, so only preserved modules are imported. '
                    'Every non-preserved module resolves to the current tree and is recorded here by '
                    'name; its equality with the true pre-change tree is assumed, not proven. The '
                    'pre-change planner re-executes a prep-pool worker from the isolated import '
                    'directory, so that worker imports the same preserved modules and the same '
                    'current support modules.' % len(preserved)))


def load_arm(arm, arm_root, digest_root=None):
    """Import one arm's module set, failing closed on any contamination."""
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    import importlib
    digest_dir = None
    if arm == 'A':
        import_dir = Path(arm_root).resolve()
        digest_dir = Path(digest_root).resolve() if digest_root else None
        if digest_dir is None:
            raise IsolationError('arm A requires the digest directory of the isolated copy')
        preserved = sorted(entry.stem for entry in import_dir.glob('*.py'))
        if not preserved:
            raise IsolationError('the isolated arm A import directory holds no modules: %s' % import_dir)
        mapping = {name: import_dir / (name + '.py') for name in preserved}
        mapping['strategy_optimizer'] = digest_dir / 'strategy_optimizer.py'
        for location in mapping.values():
            if not location.is_file():
                raise IsolationError('arm A isolated module is missing: %s' % location)
        sys.meta_path.insert(0, _ArmFinder(mapping))
    so = importlib.import_module('strategy_optimizer')
    adapter = importlib.import_module('strategy_optimizer_adapter')
    students = importlib.import_module('strategy_students')
    search = importlib.import_module('strategy_encounter_search')
    parallel = importlib.import_module('strategy_parallel_proposals')
    community = importlib.import_module('strategy_community_campaign')
    if arm == 'A':
        expected = {'strategy_optimizer': digest_dir / 'strategy_optimizer.py'}
        for name in (search.__name__, parallel.__name__, community.__name__):
            expected[name] = Path(arm_root).resolve() / (name + '.py')
        for module in (so, search, parallel, community):
            if Path(module.__file__).resolve() != expected[module.__name__].resolve():
                raise IsolationError('arm A loaded %s from %s, not the isolated copy file %s'
                                     % (module.__name__, module.__file__, expected[module.__name__]))
    elif Path(so.__file__).resolve().parent != HERE:
        raise IsolationError('arm B loaded strategy_optimizer from %s, not %s'
                             % (so.__file__, HERE))
    if getattr(search, 'EVALUATOR_FACTORY', None) is not None:
        raise IsolationError('encounter_search.EVALUATOR_FACTORY is not None; a stubbed (non-native) '
                             'evaluator would make the measurement meaningless')
    return SimpleNamespace(so=so, adapter=adapter, students=students, search=search,
                           parallel=parallel, community=community,
                           root=(Path(arm_root).resolve() if arm == 'A' else HERE))


def native_library_path():
    from ka_abi import DLL
    return Path(DLL)


# -- frozen plan ----------------------------------------------------------------------------------

def catalogue_ids_and_titles():
    import combat_runtime_data
    data = combat_runtime_data.load_data('encounters.json')
    encounters = data.get('encounters') or []
    ids = sorted(int(row['id']) for row in encounters)
    titles = {int(row['id']): row.get('title') for row in encounters}
    if not ids:
        raise BenchmarkSafetyError('the recovered encounter catalogue lists no encounters')
    return ids, titles


def pick_focus(ids, override=None):
    if override is not None:
        value = int(override)
    else:
        value = int(os.environ.get('KA_BENCH_FOCUS_ENCOUNTER') or ids[0])
    if value not in ids:
        raise BenchmarkSafetyError('focus encounter %d is not in the recovered catalogue' % value)
    return value


def freeze_plan(ids, titles, focus):
    cells = [
        dict(cell='A-all', arm='A', mode='all', focusEncounter=None),
        dict(cell='B-all', arm='B', mode='all', focusEncounter=None),
        dict(cell='A-focus', arm='A', mode='focus', focusEncounter=focus),
        dict(cell='B-focus', arm='B', mode='focus', focusEncounter=focus),
    ]
    core = dict(
        schema=PLAN_SCHEMA, frozenAtLocal=datetime.now().isoformat(timespec='seconds'),
        totalWorkers=TOTAL_WORKERS, plannerWorkers=PLANNER_WORKERS, battleWorkers=BATTLE_WORKERS,
        duty=DUTY, warmupSeconds=WARMUP_SECONDS, measureSeconds=MEASURE_SECONDS,
        gatePasses=GATE_PASSES, gateSliceCap=GATE_SLICE_CAP, gatePumpSeconds=GATE_PUMP_SECONDS,
        gateTargetCohort=GATE_TARGET_COHORT, gateCohortPassCap=GATE_COHORT_PASS_CAP,
        gateCohortSeconds=GATE_COHORT_SECONDS, gatePrimePasses=GATE_PRIME_PASSES,
        minFreeGB=MIN_FREE_GB, maxTimedSeconds=MAX_TIMED_SECONDS,
        encounterIds=ids, encounterTitles=titles, focusEncounter=focus, cells=cells,
        battlePolicy='native kernel; identical supplied baselines, DEFAULT_PURPOSES budgets, duty',
        gateRules=('the fixed-input gate compares ONE fixed native cohort: exactly gateTargetCohort '
                   '(the effective battle capacity) native results per arm. Both arms run the same '
                   'bounded fleet passes, and before each pass stream the shared async planner host '
                   '(harvest/fan-out slices) until every submitted group is routed, bounded by '
                   'gateSliceCap, gatePumpSeconds and gateCohortSeconds. The async arm may use one '
                   'bounded planning-only pass plus the planner pump before its dispatch pass; the '
                   'synchronous arm stops after its first dispatch pass. In all-encounter mode the '
                   'synchronous arm starts its gate-only fair cursor on the async arm post-prime '
                   'cursor (gatePrimePasses slots later) so both first cohorts are the same; focus '
                   'mode needs no cursor alignment. The gate stops the moment the cohort is reached '
                   'and never overruns or slices results afterwards, failing closed on any shortfall, '
                   'overrun, or pump/pass/wall cap hit. The ordered comparison stays strict over '
                   'the stable request fields (encounterId,candidateId,seedPair,scenarioDigest), '
                   'the stable result fields (candidateId,seedPair,digest,verdict,censored,backend) '
                   'and the native/fallback/errors/timeouts counters; any stable-field or counter '
                   'mismatch fails closed. The per-throwaway-library experimentId autoincrement is '
                   'a storage-local surrogate key, reported for diagnostics but not a correctness '
                   'condition'),
        campaignWorkersByArm={'A': BATTLE_WORKERS, 'B': TOTAL_WORKERS},
        expectedBattleWorkers=BATTLE_WORKERS,
        engineAndData='shared current engine/data; only the arm module roots differ',
        sampleRules='one supplied baseline per catalogue encounter; identical cohort/sample rules',
        legacyResourceLayout=(
            'legacy layout handling is required because the two module roots size their workers '
            'differently. The pre-change optimizer has no planner split, so arm A is handed the '
            'battle count B=22 and its planner is the synchronous prep pool (KA_PREP_WORKERS=2). '
            'The new optimizer is handed the total T=24 and derives P=2 planner plus B=22 battle '
            'workers itself (the shared PlanningPool plus the battle Evaluator). Both arms therefore '
            'own T=24 worker processes and the observed owned PIDs are reported per role. No '
            'harness-local bridge is injected.'),
    )
    core['planSha256'] = canonical_digest(core)
    return core


# -- shared runtime helpers (mirrors check_community_runtime_integration.make_runtime) -------------

def make_runtime(env, run_out, ids, tag):
    so, adapter, students, community = env.so, env.adapter, env.students, env.community
    path = Path(run_out) / 'libs' / tag / 'lib.sqlite'
    path.parent.mkdir(parents=True, exist_ok=True)
    provenance = adapter.provenance()
    store = so.Store(str(path), provenance)
    opt = so.Optimizer.__new__(so.Optimizer)
    opt.path = str(path)
    opt.provenance = provenance
    opt.runtimeRevision = 'bench'
    opt.encounters = {str(value): {} for value in ids}
    opt._encounter = so.encounter_search.Coordinator(path=str(path), revision='bench', workers=1,
                                                     telemetry=False, maximum=2)
    opt._encounter.load(store)
    opt._encounter_snapshot = lambda _store: dict(opt._encounter.__dict__)
    opt._encounter_report = opt._encounter.report()
    opt._student_shares = students.shares()
    opt._track_shares = {track.name: 0.0 for track in students.tracks()}
    opt._encounter_previous_shares = None
    opt._encounter_previous_tracks = None
    opt._encounter_preview_token = None
    opt._encounter_ledger = None
    opt._encounter_session_base = None
    # Planner incremental state plus the shared asynchronous planning host, mirroring the
    # shared-planner section of `Optimizer.__init__`. `make_runtime` builds the Optimizer with
    # `__new__` and seeds fields by hand, so every field a current planner method reads or writes
    # must exist here; without `_planner_routes` a submission raises AttributeError and the round
    # is reported unplanned instead of ever reaching a battle worker.
    opt._planner_order = []
    opt._planner_limits = {}
    opt._planner_rank_one = set()
    opt._planner_exploit = {}
    opt._planner_record = None
    opt._planner_portfolio = None
    opt._planner_portfolio_banks = {}
    opt._planner_portfolio_tune = None
    opt._planner_evidence = dict(at=0., value=None)
    opt._planner_scenarios = {}
    opt._planner_ordinals = None
    opt._planner_dirty = True
    # The shared asynchronous planning host. The Optimizer owns exactly ONE planner pool (shared
    # by every encounter coordinator) and ONE battle evaluator; `_planner_routes` maps a stable
    # request id to the owning coordinator and the campaign/session/generation/focus identity it
    # was submitted under.
    opt._planning_pool = None
    opt._planner_routes = {}
    opt._planning_sequence = 0
    opt._planner_submitted = 0
    opt._planner_harvested = 0
    opt._planner_accepted = 0
    opt._planner_rejected = 0
    opt._planner_cancelled = 0
    opt._planner_unrouted = 0
    opt._planner_latency = deque(maxlen=64)
    opt._planner_last_error = None
    opt._planner_close_report = None
    # Phase state machine for the shared planner: prepare -> compute -> finalize.
    opt._planner_chunk_tasks = {}
    opt._planner_task_phase = {}
    opt._planner_group_cursor = 0
    opt._planner_fanout_submitted = 0
    opt._planner_chunks_total = 0
    opt._planner_chunks_completed = 0
    opt._planner_phase_counts = {}
    opt._planning_focus = []
    opt._campaign_started = False
    opt._campaign_report = None
    opt._session_since = None
    opt._session_active = 0.0
    opt._session_live = True
    opt._workers = BATTLE_WORKERS
    opt._duty = DUTY
    opt._campaign_coordinators = {}
    opt._community_evaluator = None
    opt._campaign_fair_cursor = 0
    opt._community_fleet_state = None
    records = []

    def preview(value):
        return opt._preview_encounter(store, dict(value))

    def activate(value):
        result = opt._activate_encounter(store, dict(value))
        records.append(dict(encounter=int(value.get('encounter')),
                            session=(result or {}).get('sessionId')))
        return result

    opt._campaign = community.CommunityCampaign(encounter_ids=list(ids), preview=preview,
                                                activate=activate, commit=store.db.commit, focus=[])
    return opt, store


def add_baselines(env, store, ids):
    so, adapter = env.so, env.adapter
    wanted = {int(value) for value in ids}
    added = 0
    for scenario, label in so.baseline_scenarios(adapter.default_scenario()):
        if int(scenario.get('encounterId', -1)) in wanted:
            store.add(scenario, label, 'supplied', adapter.stats(scenario))
            added += 1
    store.db.commit()
    if added != len(wanted):
        raise BenchmarkSafetyError('only %d of %d catalogue encounters received a supplied baseline'
                                   % (added, len(wanted)))


def campaign_workers_for(arm):
    """Requested campaign worker total for one arm.

    The new optimizer (arm B) is handed the TOTAL T and derives P=2 planner plus B=22 battle
    workers itself. The pre-change optimizer (arm A) has no planner split, so it is handed the
    battle count B=22 directly and its planner is the synchronous prep pool sized by
    KA_PREP_WORKERS=2. Both arms therefore own exactly T=24 worker processes.
    """
    return BATTLE_WORKERS if arm == 'A' else TOTAL_WORKERS


def start_fleet(env, run_out, ids, focus, tag, arm):
    opt, store = make_runtime(env, run_out, ids, tag)
    add_baselines(env, store, ids)
    if focus is not None:
        store.set(getattr(env.so, 'COMMUNITY_FOCUS_KEY', 'communityFocusEncounters'), [int(focus)])
        store.db.commit()
    directive = opt._campaign_start(store, dict(workers=campaign_workers_for(arm), duty=DUTY))
    expected = 1 if focus is not None else len(ids)
    enabled = [int(value) for value in (directive.get('enabledEncounterIds') or [])]
    if len(enabled) != expected:
        raise BenchmarkSafetyError('fleet enabled %d encounters, expected %d: %r'
                                   % (len(enabled), expected, directive))
    evaluator = opt._community_evaluator
    if evaluator is None or int(getattr(evaluator, 'workers', 0)) != BATTLE_WORKERS:
        raise BenchmarkSafetyError('fleet evaluator workers=%r, expected %d'
                                   % (getattr(evaluator, 'workers', None), BATTLE_WORKERS))
    return opt, store, evaluator


class RecordingEvaluator:
    """Wraps the real Evaluator; records accepted requests and every harvested battle output."""

    def __init__(self, inner):
        self.inner = inner
        self.requests = []
        self.results = []

    def __getattr__(self, name):
        return getattr(self.inner, name)

    @staticmethod
    def _digest(scenario):
        return canonical_digest(scenario)

    def _note(self, encounter_id, experiment_id, candidate_id, seed_pair, scenario):
        self.requests.append(dict(
            encounterId=(scenario or {}).get('encounterId'), experimentId=experiment_id,
            candidateId=candidate_id, seedPair=[int(v) for v in seed_pair],
            scenarioDigest=self._digest(scenario)))

    def submit(self, db, experiment_id, candidate_id, scenario, seed_pair):
        accepted = self.inner.submit(db, experiment_id, candidate_id, scenario, seed_pair)
        if accepted:
            self._note((scenario or {}).get('encounterId'), experiment_id, candidate_id, seed_pair,
                       scenario)
        return accepted

    def submit_recovered(self, db, entry, scenario):
        accepted = self.inner.submit_recovered(db, entry, scenario)
        if accepted:
            self._note((scenario or {}).get('encounterId'), entry.get('chargedExperimentId'),
                       entry.get('candidateId'), entry.get('seedPair') or (), scenario)
        return accepted

    def submit_holdout(self, db, experiment_id, candidate_id, scenario, seed_pair):
        accepted = self.inner.submit_holdout(db, experiment_id, candidate_id, scenario, seed_pair)
        if accepted:
            self._note((scenario or {}).get('encounterId'), experiment_id, candidate_id, seed_pair,
                       scenario)
        return accepted

    def harvest(self, db):
        entries = self.inner.harvest(db)
        for entry in entries:
            result = dict(entry.get('result') or {}) if isinstance(entry, dict) else {}
            self.results.append(dict(
                experimentId=entry.get('experimentId') if isinstance(entry, dict) else None,
                candidateId=entry.get('candidateId') if isinstance(entry, dict) else None,
                seedPair=[int(v) for v in (entry.get('seeds') or ())] if isinstance(entry, dict) else [],
                digest=result.get('digest'), verdict=result.get('verdict'),
                censored=bool(result.get('censored')), backend=result.get('resultBackend'),
                accepted=bool(entry.get('accepted')) if isinstance(entry, dict) else False))
        return entries

    def close(self):
        return self.inner.close()


def install_recorder(opt, env):
    real = opt._community_evaluator
    if real is None:
        first = next(iter(opt._campaign_coordinators.values()))
        real = first._make_evaluator()
        opt._community_evaluator = real
    recorder = RecordingEvaluator(real)
    opt._community_evaluator = recorder
    for coord in opt._campaign_coordinators.values():
        coord.evaluator = recorder
    return recorder


def close_fleet(opt, store, evaluator):
    real = getattr(evaluator, 'inner', evaluator)
    if real is not None and hasattr(real, 'close'):
        try:
            real.close()
        except Exception:  # noqa: BLE001
            pass
    for coord in (getattr(opt, '_campaign_coordinators', None) or {}).values():
        try:
            coord.evaluator = None
        except Exception:  # noqa: BLE001
            pass
    if getattr(opt, '_encounter', None) is not None:
        try:
            opt._encounter.evaluator = None
        except Exception:  # noqa: BLE001
            pass
    if store is not None:
        try:
            store.close()
        except Exception:  # noqa: BLE001
            pass


def teardown_pools(env):
    shutdown = getattr(env.parallel, 'shutdown', None)
    if callable(shutdown):
        try:
            shutdown()
        except Exception:  # noqa: BLE001
            pass
    close_planning = getattr(env.parallel, 'close_planning_pool', None)
    if callable(close_planning):
        try:
            close_planning()
        except Exception:  # noqa: BLE001
            pass


def session_sample_count(store):
    try:
        return int(store.db.execute(
            'SELECT COUNT(*) FROM ea_sample WHERE result IS NOT NULL').fetchone()[0])
    except Exception:  # noqa: BLE001
        return 0


def drain_evaluator(evaluator, db, timeout):
    deadline = time.monotonic() + timeout
    while True:
        evaluator.harvest(db)
        if not getattr(evaluator, 'pending', None) and not evaluator.busy():
            return
        if time.monotonic() > deadline:
            raise BenchmarkSafetyError('gate drain exceeded %.0f seconds' % timeout)
        time.sleep(0.02)


def planner_outstanding(opt):
    """Number of not-yet-delivered shared-planner groups for one optimizer host.

    A group is outstanding while its stable route id is still mapped, or while its owning
    coordinator still holds a pending marker. Both fall to zero only when the harvested result
    has actually been routed to the coordinator. A module root without the async planner
    (the pre-change arm A) reports zero.
    """
    routes = len(getattr(opt, '_planner_routes', None) or {})
    pending = 0
    for coord in (getattr(opt, '_campaign_coordinators', None) or {}).values():
        if getattr(coord, '_planner_pending', None) is not None:
            pending += 1
    return routes + pending


def pump_planner(opt, store, max_slices, deadline):
    """Bounded optimizer-thread harvest/fan-out slices for the shared async planner.

    The real optimizer loop calls the host's ``_harvest_planning`` immediately before every
    fleet pass. The harness calls ``_campaign_fleet_pass`` directly, so it must supply the same
    slices; otherwise arm B submits a planning group but never routes the result and every
    round is reported unplanned. This keeps pumping until no group is outstanding so the
    planner workers have time to finish, bounded by ``max_slices`` and ``deadline``. Arm A has
    no ``_harvest_planning`` and reports ``available=False``; the call is a no-op for it.
    """
    harvest = getattr(opt, '_harvest_planning', None)
    if not callable(harvest):
        return dict(available=False, slices=0, drained=True, capHit=False, outstanding=0)
    slices = 0
    while time.monotonic() < deadline and slices < max_slices:
        outstanding = planner_outstanding(opt)
        if outstanding == 0:
            return dict(available=True, slices=slices, drained=True, capHit=False, outstanding=0)
        try:
            harvest(store)
        except Exception as exc:  # noqa: BLE001 - a harvest error must fail closed, not hang
            return dict(available=True, slices=slices, drained=False, capHit=True,
                        outstanding=outstanding, error='%s: %s' % (type(exc).__name__, exc))
        slices += 1
        time.sleep(GATE_SLICE_SLEEP)
    outstanding = planner_outstanding(opt)
    return dict(available=True, slices=slices, drained=outstanding == 0,
                capHit=outstanding != 0, outstanding=outstanding)


def harvest_planner_once(opt, store):
    """One non-blocking optimizer-thread slice for the timed loops (mirrors the real loop).

    The real optimizer loop harvests the shared planner before every fleet pass; the timed
    warm-up/measure loops must do the same or the async arm never converts a submitted
    planning group into dispatched battles. This never waits, so it cannot distort timing.
    """
    harvest = getattr(opt, '_harvest_planning', None)
    if callable(harvest):
        harvest(store)


def cell_tag(arm, mode):
    return '%s-%s' % (arm, mode)


def write_child_status(run_out, args, phase, elapsed, total, **extra):
    payload = dict(time=time.time(), cell=cell_tag(args.arm, args.mode), arm=args.arm,
                   mode=args.mode, phase=phase, elapsed=round(elapsed, 2), total=total)
    payload.update(extra)
    path = Path(run_out) / 'children' / ('%s-%s.jsonl' % (cell_tag(args.arm, args.mode), phase))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(payload, sort_keys=True, default=str) + '\n')


def child_progress(recorder, phase, elapsed, base_battles):
    """Real counters for the visible monitor, read from the wrapped Evaluator.

    ``completedBattles`` is the number of harvested battle results the ``RecordingEvaluator``
    has actually recorded, never a guess from the configured worker count. ``battlesPerSecond``
    is the measured rate for the current phase only (``completedInPhase / elapsed``); it is
    ``None`` until the phase has started and no results have landed.
    """
    try:
        status = dict(recorder.status())
    except Exception:  # noqa: BLE001 - a missing status must never break the run
        status = {}
    completed = len(getattr(recorder, 'results', None) or [])
    in_phase = max(0, completed - int(base_battles or 0))
    elapsed = float(elapsed or 0.0)
    rate = (in_phase / elapsed) if elapsed > 0 else None
    return dict(
        completedBattles=completed,
        completedInPhase=in_phase,
        battlesPerSecond=(round(rate, 4) if rate is not None else None),
        nativeResults=int(status.get('native') or 0),
        fallbackResults=int(status.get('fallback') or 0),
        errors=int(status.get('errors') or 0),
        timeouts=int(status.get('timeouts') or 0),
        submitted=int(status.get('submitted') or 0),
        harvested=int(status.get('completed') or 0),
        inflight=int(status.get('inflight') or 0))


def summarize_samples(rows, workers, window_seconds):
    active = [row['active'] for row in rows]
    active_avg = statistics.fmean(active) if active else 0.0

    def window_mean(seconds):
        if not rows:
            return 0.0
        end = rows[-1]['t']
        subset = [row['active'] for row in rows if row['t'] >= end - seconds]
        return statistics.fmean(subset) if subset else active_avg

    intervals = [rows[index + 1]['t'] - rows[index]['t'] for index in range(len(rows) - 1)]
    histogram = {}
    for value in active:
        histogram[str(int(value))] = histogram.get(str(int(value)), 0) + 1
    longest = current = 0.0
    idle_seconds = idle_no_work_seconds = 0.0
    for index in range(1, len(rows)):
        step = rows[index]['t'] - rows[index - 1]['t']
        idle_seconds += max(0.0, workers - rows[index]['active']) * step
        if rows[index]['active'] == 0:
            current += step
            idle_no_work_seconds += workers * step
        else:
            longest = max(longest, current)
            current = 0.0
    longest = max(longest, current)
    if len(rows) < 2:
        idle_seconds = max(0.0, workers - active_avg) * window_seconds
        idle_no_work_seconds = (workers * window_seconds) if not active_avg else 0.0
    cpu_by_role, ws_peak, distinct = {}, {}, {}
    for row in rows:
        for reading in row['readings']:
            role = reading['role']
            cpu_by_role[role] = cpu_by_role.get(role, 0.0) + reading['cpuDelta']
            if reading['workingSetBytes'] is not None:
                ws_peak[role] = max(ws_peak.get(role, 0), reading['workingSetBytes'])
            distinct.setdefault(role, set()).add(reading['pid'])
    ages = [age for row in rows for age in row['jobSeconds']]
    free_values = [row['freeGB'] for row in rows if row['freeGB'] is not None]
    return dict(
        sampleCount=len(rows),
        activeWorkersAverage=round(active_avg, 3),
        activeWorkersAverage10s=round(window_mean(10), 3),
        activeWorkersAverage30s=round(window_mean(30), 3),
        workerOccupancy=round(active_avg / max(1, workers), 4),
        instantaneousActiveWorkers=(rows[-1]['active'] if rows else 0),
        workerIdleSeconds=round(idle_seconds, 2),
        workerIdleNoWorkSeconds=round(idle_no_work_seconds, 2),
        activeWorkerHistogram=histogram, longestIdleIntervalSeconds=round(longest, 3),
        workerSampleIntervalMeanSeconds=(round(statistics.fmean(intervals), 4) if intervals else None),
        workerSampleIntervalMinSeconds=(round(min(intervals), 4) if intervals else None),
        workerSampleIntervalMaxSeconds=(round(max(intervals), 4) if intervals else None),
        pendingJobAgeSeconds=dict(min=(round(min(ages), 3) if ages else None),
                                  median=(round(statistics.median(ages), 3) if ages else None),
                                  max=(round(max(ages), 3) if ages else None)),
        minFreeMemoryGB=(round(min(free_values), 3) if free_values else None),
        ownedProcessCpuSecondsByRole={key: round(value, 4) for key, value in sorted(cpu_by_role.items())},
        ownedProcessPeakWorkingSetBytesByRole={key: int(value) for key, value in sorted(ws_peak.items())},
        ownedProcessCountByRole={key: len(value) for key, value in sorted(distinct.items())},
    )


def per_encounter_summary(opt):
    summary = {}
    stage_names = ('proposalSeconds', 'planningSeconds', 'recoverySeconds', 'portfolioSeconds',
                   'dispatchSeconds')
    for encounter_id, coord in sorted((opt._campaign_coordinators or {}).items()):
        report = coord.report()
        progress = report.get('progress') or {}
        timings = report.get('timings') or {}
        current = progress.get('current')
        summary[str(encounter_id)] = dict(
            sessionId=report.get('sessionId'), title=(report.get('presentation') or {}).get('title')
            if isinstance(report.get('presentation'), dict) else None,
            cohortPending=int(progress.get('cohortPending') or 0),
            completed=int(progress.get('completed') or 0),
            reserved=int(progress.get('reserved') or 0),
            idle=bool(progress.get('idle')),
            currentBlocked=bool(current.get('blocked')) if isinstance(current, dict) else False,
            timings={name: timings.get(name) for name in stage_names})
    return summary


def gate_cursor_alignment(opt, ids, mode):
    """Gate-only fair-cursor offset so both arms compare the same fixed native cohort.

    In all-encounter mode the async arm runs ``GATE_PRIME_PASSES`` planning-only fleet pass
    before its first dispatch, and that pass still advances the fleet's round-robin fair cursor.
    Arm A is synchronous (no ``_harvest_planning``), so its first gate pass would otherwise
    dispatch the cohort one encounter earlier; it is seeded onto the async arm's post-prime
    cursor instead so both first cohorts are the same encounters. Focus mode has a single
    encounter, so the cursor rotation is a no-op and needs no alignment. This is gate-only: the
    timed warm-up/measure loops never call it and keep their own scheduling state.
    """
    if mode != 'all':
        return 0
    if callable(getattr(opt, '_harvest_planning', None)):
        return 0
    return GATE_PRIME_PASSES % max(1, len(ids))


def run_gate(env, run_out, args, ids, focus):
    tag = cell_tag(args.arm, args.mode)
    require_memory_floor('gate setup for ' + tag)
    opt = store = evaluator = recorder = None
    passage = []
    gate_work = []
    payload = None
    target = GATE_TARGET_COHORT
    cursor_alignment = 0
    cohort_reached = False
    cohort_overrun = None
    cohort_cap_hit = None
    try:
        opt, store, evaluator = start_fleet(env, run_out, ids, focus, 'gate-' + tag, args.arm)
        recorder = install_recorder(opt, env)
        # Gate-only cohort alignment (all-encounter mode): start the synchronous arm on the fair
        # cursor the async arm reaches after its planning-only tick so both first cohorts match.
        cursor_alignment = gate_cursor_alignment(opt, ids, args.mode)
        if cursor_alignment:
            opt._campaign_fair_cursor = ((int(getattr(opt, '_campaign_fair_cursor', 0)) +
                                          cursor_alignment) % max(1, len(ids)))
        gate_start = time.monotonic()
        base_battles = len(recorder.results)
        deadline = gate_start + GATE_COHORT_SECONDS
        for index in range(GATE_COHORT_PASS_CAP):
            if time.monotonic() > deadline:
                cohort_cap_hit = 'wall'
                break
            require_memory_floor('gate pass %d for %s' % (index, tag))
            began = time.monotonic()
            # Stream the shared planner the way the real optimizer loop does before every fleet
            # pass, but keep pumping (bounded) so the async arm's planner workers can finish the
            # group a previous pass submitted. Arm A has no async planner and pumps nothing.
            pump = pump_planner(opt, store, GATE_SLICE_CAP,
                                min(began + GATE_PUMP_SECONDS, deadline))
            opt._campaign_fleet_pass(store, running=True)
            drain_evaluator(recorder, store.db, GATE_DRAIN_TIMEOUT)
            passage.append(round(time.monotonic() - began, 3))
            native = int((recorder.status() or {}).get('native') or 0)
            reached = native == target
            gate_work.append(dict(
                gatePass=index + 1, plannerPump=pump,
                requests=len(recorder.requests), results=len(recorder.results),
                native=native, outstanding=planner_outstanding(opt), cohortReached=reached))
            write_child_status(run_out, args, 'gate', time.monotonic() - gate_start, None,
                               gatePass=index + 1, gatePasses=GATE_COHORT_PASS_CAP,
                               **child_progress(recorder, 'gate', time.monotonic() - gate_start,
                                                base_battles))
            # Stop the moment the fixed cohort is reached; never overrun and slice afterwards.
            if native == target:
                cohort_reached = True
                break
            if native > target:
                cohort_overrun = native
                break
        else:
            cohort_cap_hit = 'passes'
        status = dict(recorder.status())
        payload = dict(schema=GATE_SCHEMA, cell=tag, arm=args.arm, mode=args.mode,
                       phase='gate', focusEncounter=focus, moduleRoot=str(env.root),
                       gateTargetCohort=target, gatePassCap=GATE_COHORT_PASS_CAP,
                       gatePassesRun=len(passage), gatePassSeconds=passage,
                       gateCohortSeconds=GATE_COHORT_SECONDS,
                       gateCursorAlignment=cursor_alignment, gatePrimePasses=GATE_PRIME_PASSES,
                       gateCohortReached=cohort_reached, gateCohortOverrun=cohort_overrun,
                       gateCohortCapHit=cohort_cap_hit,
                       gateSliceCap=GATE_SLICE_CAP, gatePumpSeconds=GATE_PUMP_SECONDS,
                       gateWork=gate_work,
                       gateCapHit=any(entry['plannerPump'].get('capHit') for entry in gate_work),
                       plannerOutstanding=planner_outstanding(opt),
                       nativeSeconds=round(sum(passage), 3),
                       requests=recorder.requests, results=recorder.results,
                       native=status.get('native'), fallback=status.get('fallback'),
                       errors=status.get('errors'), timeouts=status.get('timeouts'),
                       submitted=status.get('submitted'), completed=status.get('completed'),
                       freeMemoryAtFinalizeGB=free_memory_gb())
    finally:
        close_fleet(opt, store, recorder)
        teardown_pools(env)
    write_json(Path(run_out) / ('gate-%s.json' % tag), payload)
    return payload


def run_measure(env, run_out, args, ids, focus):
    tag = cell_tag(args.arm, args.mode)
    require_memory_floor('measure setup for ' + tag)
    opt = store = evaluator = recorder = None
    payload = None
    try:
        opt, store, evaluator = start_fleet(env, run_out, ids, focus, 'measure-' + tag, args.arm)
        recorder = install_recorder(opt, env)
        cold_startup = time.perf_counter() - MODULE_IMPORT_T0

        def timing_snapshot():
            return {str(key): dict(coord.state.get('timings') or {})
                    for key, coord in (opt._campaign_coordinators or {}).items()}

        warm_start = time.monotonic()
        base_warm = len(recorder.results)
        last = 0.0
        while time.monotonic() - warm_start < WARMUP_SECONDS:
            require_memory_floor('warm-up for ' + tag)
            # Mirror the real optimizer loop: stream one non-blocking planner slice before the
            # pass, or the async arm submits a planning group and never routes its result.
            harvest_planner_once(opt, store)
            opt._campaign_fleet_pass(store, running=True)
            elapsed = time.monotonic() - warm_start
            if elapsed - last >= 1.0:
                write_child_status(run_out, args, 'warm-up', elapsed, WARMUP_SECONDS,
                                   **child_progress(recorder, 'warm-up', elapsed, base_warm))
                last = elapsed
            time.sleep(0.001)
        warm_elapsed = time.monotonic() - warm_start

        rows = []
        sample_lock = threading.Lock()
        stop = threading.Event()
        violation = {'message': None}
        last_cpu = {}

        def sampler():
            while not stop.is_set():
                free_gb = free_memory_gb()
                if free_gb is None:
                    violation['message'] = 'free memory could not be measured during measure'
                    return
                if free_gb < MIN_FREE_GB:
                    violation['message'] = ('free memory %.2f GiB below the %.1f GiB floor during '
                                            'measure' % (free_gb, MIN_FREE_GB))
                    return
                inner = getattr(recorder, 'inner', recorder)
                active = 0
                job_seconds = []
                try:
                    clock = getattr(inner, 'clock', time.perf_counter)
                    now = clock() if callable(clock) else time.perf_counter()
                    for job in inner.pending.values():
                        if not job['future'].done():
                            active += 1
                        job_seconds.append(round(max(0.0, now - job['started']), 3))
                except Exception:  # noqa: BLE001
                    pass
                roles = collect_owned_pids(env.parallel, inner)
                readings = []
                for role, pids in roles.items():
                    for pid in pids:
                        sample = sample_process(pid)
                        if not sample:
                            continue
                        previous = last_cpu.get(pid)
                        delta = 0.0 if previous is None else max(0.0, sample['cpuSeconds'] - previous)
                        last_cpu[pid] = sample['cpuSeconds']
                        readings.append(dict(pid=sample['pid'], role=role, cpuDelta=delta,
                                             cpuTotal=sample['cpuSeconds'],
                                             workingSetBytes=sample['workingSetBytes']))
                with sample_lock:
                    rows.append(dict(t=time.monotonic(), active=active, freeGB=free_gb,
                                     jobSeconds=job_seconds, readings=readings))
                stop.wait(WORKER_SAMPLE_SECONDS)

        sampler_thread = threading.Thread(target=sampler, daemon=True)
        sampler_thread.start()
        measure_start = time.monotonic()
        pass_seconds = []
        stage_totals = {name: 0.0 for name in STAGE_NAMES}
        before_count = session_sample_count(store)
        base_measure = len(recorder.results)
        last = 0.0
        try:
            while time.monotonic() - measure_start < MEASURE_SECONDS:
                require_memory_floor('measure for ' + tag)
                if violation['message']:
                    raise BenchmarkSafetyError(violation['message'])
                # Non-blocking planner slice on the optimizer thread, exactly as the real loop
                # does before every pass (never waits, so the measured window is not distorted).
                harvest_planner_once(opt, store)
                before_timings = timing_snapshot()
                began = time.perf_counter()
                opt._campaign_fleet_pass(store, running=True)
                pass_seconds.append(time.perf_counter() - began)
                after_timings = timing_snapshot()
                for encounter_id, after in after_timings.items():
                    before = before_timings.get(encounter_id) or {}
                    for name in STAGE_NAMES:
                        stage_totals[name] += max(0.0, float(after.get(name) or 0.0)
                                                  - float(before.get(name) or 0.0))
                elapsed = time.monotonic() - measure_start
                if elapsed - last >= 1.0:
                    extra = child_progress(recorder, 'measure', elapsed, base_measure)
                    if violation['message']:
                        extra['warning'] = violation['message']
                    write_child_status(run_out, args, 'measure', elapsed, MEASURE_SECONDS, **extra)
                    last = elapsed
                time.sleep(0.001)
        finally:
            stop.set()
            sampler_thread.join(timeout=3)
        measure_elapsed = time.monotonic() - measure_start
        if violation['message']:
            raise BenchmarkSafetyError(violation['message'])
        with sample_lock:
            sampled = list(rows)
        aggregate = summarize_samples(sampled, BATTLE_WORKERS, measure_elapsed)
        status = dict(recorder.status())
        completed = session_sample_count(store) - before_count
        worker_cpu_seconds = float(status.get('workerCpuSeconds') or 0.0)
        stage_values = {name: round(value, 3) for name, value in stage_totals.items()}
        payload = dict(
            schema=CELL_SCHEMA, cell=tag, arm=args.arm, mode=args.mode, phase='measure',
            focusEncounter=focus, moduleRoot=str(env.root),
            nativeLibrary=str(native_library_path()),
            coldStartupSeconds=round(cold_startup, 3),
            warmupSeconds=round(warm_elapsed, 3), measurementSeconds=round(measure_elapsed, 3),
            timedNativeSeconds=round(warm_elapsed + measure_elapsed, 3),
            completedBattles=completed,
            battlesPerSecond=round(completed / max(0.001, measure_elapsed), 3),
            nativeResults=status.get('native'), fallbackResults=status.get('fallback'),
            errors=status.get('errors'), timeouts=status.get('timeouts'),
            evaluatorSubmitted=status.get('submitted'), evaluatorCompleted=status.get('completed'),
            evaluatorInflight=status.get('inflight'),
            simulationSeconds=status.get('simulationSeconds'),
            persistenceSeconds=status.get('persistenceSeconds'),
            workerCpuSeconds=round(worker_cpu_seconds, 3),
            workerCpuUtilizationOfLogicalCpus=round(
                worker_cpu_seconds / max(0.001, measure_elapsed) / (os.cpu_count() or 1), 4),
            workerCpuSecondsPerBattle=(round(worker_cpu_seconds / completed, 5) if completed else None),
            coordinatorPassCount=len(pass_seconds),
            coordinatorPassMeanSeconds=(round(statistics.fmean(pass_seconds), 4) if pass_seconds else None),
            coordinatorPassP95Seconds=(round(sorted(pass_seconds)[min(len(pass_seconds) - 1,
                math.ceil(len(pass_seconds) * 0.95) - 1)], 4) if pass_seconds else None),
            stages=stage_values,
            perEncounter=per_encounter_summary(opt),
            plannerDiagnostics=dict(getattr(env.parallel, 'diagnostics', lambda: {})() or {}),
            planningPoolStatus=(getattr(env.parallel, 'planning_pool_status', lambda: None)()
                                if callable(getattr(env.parallel, 'planning_pool_status', None)) else None),
            libraryPath=str(store.path), sessionSampleCount=session_sample_count(store),
            freeMemoryFloorGB=MIN_FREE_GB,
            notes=['timers are sequential: cold setup, warm-up, then a single measured window',
                   'only owned PIDs (harness plus this run pools) are sampled'])
        payload.update(aggregate)
    finally:
        close_fleet(opt, store, recorder)
        teardown_pools(env)
    write_json(Path(run_out) / ('measure-%s.json' % tag), payload)
    return payload


def main_child(args):
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    ids = [int(value) for value in json.loads(args.ids)]
    focus = int(args.focus) if args.focus is not None else None
    os.environ['KA_PREP_WORKERS'] = str(PLANNER_WORKERS)
    try:
        env = load_arm(args.arm, args.arm_root, getattr(args, 'digest_root', None))
        require_memory_floor('child setup for ' + cell_tag(args.arm, args.mode))
        library = native_library_path()
        if not library.is_file():
            raise BenchmarkSafetyError('native battle kernel is missing at %s' % library)
        if args.phase == 'gate':
            run_gate(env, out, args, ids, focus)
        else:
            run_measure(env, out, args, ids, focus)
    except Exception as exc:  # noqa: BLE001
        payload = dict(schema=CELL_SCHEMA, cell=cell_tag(args.arm, args.mode), arm=args.arm,
                       mode=args.mode, phase=args.phase, failed=True,
                       error='%s: %s' % (type(exc).__name__, exc),
                       traceback=traceback.format_exc())
        write_json(out / ('%s-%s.json' % (args.phase, cell_tag(args.arm, args.mode))), payload)
        return 2
    return 0


# -- parent: orchestration, correctness gate, report ----------------------------------------------

ACTIVE_OUT = {'path': DEFAULT_OUT}
ACTIVE_CHILD = {'status': None, 'cell': None}


def put_status(**payload):
    payload['time'] = time.time()
    PROGRESS_QUEUE.put(dict(payload))
    out = Path(payload.get('output') or ACTIVE_OUT['path'])
    out.mkdir(parents=True, exist_ok=True)
    with (out / 'progress.jsonl').open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(payload, sort_keys=True, default=str) + '\n')
    write_json(out / 'progress.json', payload)


def enforce_budget(total_timed, where):
    if total_timed > MAX_TIMED_SECONDS:
        raise BenchmarkSafetyError('timed native execution would exceed %.0f s at %s '
                                   '(%.1f s so far)' % (MAX_TIMED_SECONDS, where, total_timed))


def terminate_process_tree(process):
    """Terminate a child process and every process it spawned.

    Windows ``Popen.kill``/``terminate`` only calls TerminateProcess on the direct child,
    which orphans the Evaluator/PlanningPool worker subprocesses that child owns. The
    documented Windows way to reap a whole tree is ``taskkill /F /T``. Every step below is
    bounded and falls back to ``process.kill()`` so a missing, denied, or wedged
    ``taskkill`` can never hang cleanup. On POSIX ``run_child`` spawns the child in its own
    session (``start_new_session=True``), so ``killpg`` reaps the whole tree; the group kill
    only fires when the child is provably in its own group, never the parent's group.
    """
    if process.poll() is None:
        if os.name == 'nt':
            try:
                subprocess.run(['taskkill', '/F', '/T', '/PID', str(process.pid)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=CREATION_FLAGS, timeout=TREEKILL_TIMEOUT)
            except (OSError, subprocess.SubprocessError):
                pass
        else:
            try:
                group = os.getpgid(process.pid)
            except OSError:
                group = None
            if group is not None and group != os.getpgrp():
                try:
                    os.killpg(group, signal.SIGKILL)
                except OSError:
                    pass
    if process.poll() is None:
        process.kill()
    try:
        process.wait(timeout=TREEKILL_TIMEOUT)
    except subprocess.TimeoutExpired:
        pass


def run_child(out, arm, arm_root, digest_root, mode, focus, phase, ids):
    tag = cell_tag(arm, mode)
    log_path = Path(out) / 'children' / ('%s-%s.log' % (phase, tag))
    log_path.parent.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, '-B', '-X', 'utf8', str(SCRIPT),
               '--phase', phase, '--arm', arm, '--mode', mode, '--arm-root', str(arm_root),
               '--out', str(out), '--ids', json.dumps(ids)]
    if digest_root is not None:
        command += ['--digest-root', str(digest_root)]
    if focus is not None:
        command += ['--focus', str(focus)]
    environment = dict(os.environ)
    environment['KA_PREP_WORKERS'] = str(PLANNER_WORKERS)
    environment['KA_PREP_PARALLEL'] = '1'
    environment['PYTHONUNBUFFERED'] = '1'
    ACTIVE_CHILD['cell'] = tag
    ACTIVE_CHILD['status'] = str(Path(out) / 'children' / ('%s-%s.jsonl' % (tag, phase)))
    try:
        with log_path.open('w', encoding='utf-8') as stream:
            popen_kwargs = dict(cwd=str(HERE), stdout=stream, stderr=subprocess.STDOUT,
                                env=environment, creationflags=CREATION_FLAGS)
            if os.name != 'nt':
                # POSIX: own session/process group so terminate_process_tree can killpg the
                # whole tree. Windows keeps its CREATE_NO_WINDOW creation flags instead.
                popen_kwargs['start_new_session'] = True
            process = subprocess.Popen(command, **popen_kwargs)
            try:
                code = process.wait(timeout=CHILD_WALL_TIMEOUT)
            except subprocess.TimeoutExpired:
                terminate_process_tree(process)
                raise BenchmarkSafetyError('child %s exceeded %.0f s and was killed'
                                           % (tag, CHILD_WALL_TIMEOUT))
    finally:
        ACTIVE_CHILD['status'] = None
        ACTIVE_CHILD['cell'] = None
    payload_path = Path(out) / ('%s-%s.json' % (phase, tag))
    payload = read_json(payload_path) if payload_path.is_file() else None
    if code != 0 or payload is None or payload.get('failed'):
        detail = (payload or {}).get('error') or ('exit code %s' % code)
        raise BenchmarkSafetyError('child %s failed: %s (log %s)' % (tag, detail, log_path))
    return payload


def _normalise(row, fields):
    values = []
    for field in fields:
        value = row.get(field)
        if isinstance(value, list):
            value = tuple(value)
        values.append(value)
    return tuple(values)


def gate_work_summary(payload):
    """What bounded gate work one arm completed, for a clear fail-closed report.

    Surfaced in `require_native_gate` and in the correctness-gate failure message so a gate
    that stops on a cap (rather than on a genuine zero-work run) says exactly which pass and
    which planner-pump cap it reached.
    """
    payload = payload if isinstance(payload, dict) else {}
    return dict(
        cell=payload.get('cell'), arm=payload.get('arm'), mode=payload.get('mode'),
        gateTargetCohort=payload.get('gateTargetCohort'),
        gatePassCap=payload.get('gatePassCap'), gatePassesRun=payload.get('gatePassesRun'),
        gatePasses=payload.get('gatePasses'), gatePassSeconds=payload.get('gatePassSeconds'),
        gateCohortSeconds=payload.get('gateCohortSeconds'),
        gateCursorAlignment=payload.get('gateCursorAlignment'),
        gateCohortReached=payload.get('gateCohortReached'),
        gateCohortOverrun=payload.get('gateCohortOverrun'),
        gateCohortCapHit=payload.get('gateCohortCapHit'),
        gateSliceCap=payload.get('gateSliceCap'), gatePumpSeconds=payload.get('gatePumpSeconds'),
        gateCapHit=payload.get('gateCapHit'), plannerOutstanding=payload.get('plannerOutstanding'),
        native=payload.get('native'), fallback=payload.get('fallback'),
        errors=payload.get('errors'), timeouts=payload.get('timeouts'),
        requests=len(payload.get('requests') or []),
        results=len(payload.get('results') or []),
        gateWork=payload.get('gateWork'))


def surrogate_key_report(arm_a, arm_b):
    """Report raw ``experimentId`` sequences WITHOUT letting them gate correctness.

    ``experimentId`` is each arm's throwaway SQLite library autoincrement primary key, assigned in
    storage-local creation order when the coordinator creates the experiment batch: synchronous
    encounter order on arm A, async planner-completion order on arm B. It is a throwaway-library
    SURROGATE KEY, not a simulation input, so a raw-ID difference is reported here for
    diagnostics but is never a correctness condition and never a gate failure. The scientific
    content of each row is already compared exactly by the stable fields in ``compare_recordings``.
    """
    sections = {}
    for section in ('requests', 'results'):
        left = [row.get('experimentId') for row in (arm_a.get(section) or [])]
        right = [row.get('experimentId') for row in (arm_b.get(section) or [])]
        limit = min(len(left), len(right))
        differences = [dict(index=index, armA=left[index], armB=right[index])
                       for index in range(limit) if left[index] != right[index]]
        if len(left) != len(right):
            differences.append(dict(index=limit, armA=(left[limit] if limit < len(left) else None),
                                    armB=(right[limit] if limit < len(right) else None),
                                    note='arm length mismatch'))
        sections[section] = dict(
            surrogateKey='experimentId', matches=(left == right), armACount=len(left),
            armBCount=len(right), firstDifferenceIndex=(differences[0]['index']
                                                        if differences else None),
            armAExperimentIds=left, armBExperimentIds=right, differences=differences)
    return dict(
        fields=list(GATE_SURROGATE_FIELDS),
        label='throwaway-library surrogate key: per-throwaway-SQLite autoincrement primary key '
              'assigned in storage-local creation order, not a simulation input',
        correctnessCondition=False,
        matches=all(entry['matches'] for entry in sections.values()),
        sections=sections)


def compare_recordings(arm_a, arm_b):
    problems = []
    for section, fields in (('requests', GATE_REQUEST_FIELDS), ('results', GATE_RESULT_FIELDS)):
        left = [_normalise(row, fields) for row in (arm_a.get(section) or [])]
        right = [_normalise(row, fields) for row in (arm_b.get(section) or [])]
        if left != right:
            limit = min(len(left), len(right))
            index = next((position for position in range(limit) if left[position] != right[position]),
                         limit)
            problems.append(dict(
                section=section, fields=list(fields), armACount=len(left), armBCount=len(right),
                firstMismatchIndex=index,
                armA=(list(left[index]) if index < len(left) else None),
                armB=(list(right[index]) if index < len(right) else None)))
    counters = {}
    for key in ('native', 'fallback', 'errors', 'timeouts'):
        if arm_a.get(key) != arm_b.get(key):
            counters[key] = dict(armA=arm_a.get(key), armB=arm_b.get(key))
    if counters:
        problems.append(dict(section='counters', detail=counters))
    return dict(
        identical=not problems, problems=problems,
        comparedFields=dict(requests=list(GATE_REQUEST_FIELDS), results=list(GATE_RESULT_FIELDS),
                            counters=['native', 'fallback', 'errors', 'timeouts'],
                            surrogateKeys=list(GATE_SURROGATE_FIELDS)),
        surrogateKey=surrogate_key_report(arm_a, arm_b),
        armAGateWork=gate_work_summary(arm_a), armBGateWork=gate_work_summary(arm_b),
        armASummary=dict(cell=arm_a.get('cell'), requests=len(arm_a.get('requests') or []),
                         results=len(arm_a.get('results') or []), native=arm_a.get('native'),
                         fallback=arm_a.get('fallback'), errors=arm_a.get('errors')),
        armBSummary=dict(cell=arm_b.get('cell'), requests=len(arm_b.get('requests') or []),
                         results=len(arm_b.get('results') or []), native=arm_b.get('native'),
                         fallback=arm_b.get('fallback'), errors=arm_b.get('errors')))


def _counter(payload, key):
    value = payload.get(key)
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def require_native_gate(payload, where):
    """Fail closed unless a gate arm ran real native battles and produced the exact cohort.

    The native/error/timeout checks are unchanged; one more check is added: the arm must have
    produced exactly ``gateTargetCohort`` native results with no overrun and no pump/pass/wall
    cap hit. The gate compares ONE fixed cohort, so a short or overrun cohort is refused instead
    of being sliced to size afterwards.
    """
    native = _counter(payload, 'native')
    if native <= 0:
        raise BenchmarkSafetyError('%s ran no native battles (native=%d); the fixed-input gate '
                                   'cannot certify native work, so timing is refused; gate work: '
                                   '%s' % (where, native,
                                           json.dumps(gate_work_summary(payload), sort_keys=True,
                                                      default=str)))
    for key in ('errors', 'timeouts'):
        value = _counter(payload, key)
        if value:
            raise BenchmarkSafetyError('%s recorded %s=%d before timing; refusing to start the '
                                       'measured cells; gate work: %s'
                                       % (where, key, value,
                                          json.dumps(gate_work_summary(payload), sort_keys=True,
                                                     default=str)))
    target = _counter(payload, 'gateTargetCohort')
    if target and not payload.get('gateCohortReached'):
        raise BenchmarkSafetyError(
            '%s did not produce the exact %d-native gate cohort (native=%d, overrun=%s, '
            'capHit=%s, passesRun=%s); the fixed-input gate compares one fixed cohort, so timing '
            'is refused; gate work: %s'
            % (where, target, native, payload.get('gateCohortOverrun'),
               payload.get('gateCohortCapHit'), payload.get('gatePassesRun'),
               json.dumps(gate_work_summary(payload), sort_keys=True, default=str)))


def require_native_cell(payload, where):
    """Fail closed rather than report a cell that completed no native battles."""
    native = _counter(payload, 'nativeResults')
    if native <= 0:
        raise BenchmarkSafetyError('%s completed no native battles (nativeResults=%d); refusing to '
                                   'report it as a native measurement' % (where, native))


def _numeric(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def raw_comparison(measures):
    pairs = {'all': ('A-all', 'B-all'), 'focus': ('A-focus', 'B-focus')}
    metrics = ('completedBattles', 'battlesPerSecond', 'nativeResults', 'fallbackResults', 'errors',
               'activeWorkersAverage', 'workerOccupancy', 'workerCpuSeconds',
               'coordinatorPassMeanSeconds', 'measurementSeconds')
    out = {}
    for name, (a_key, b_key) in pairs.items():
        arm_a = measures.get(a_key) or {}
        arm_b = measures.get(b_key) or {}
        rows = {}
        for metric in metrics:
            left, right = arm_a.get(metric), arm_b.get(metric)
            if _numeric(left) and _numeric(right):
                rows[metric] = dict(armA=left, armB=right, delta=round(right - left, 6),
                                    ratio=(round(right / left, 6) if left else None))
        out[name] = dict(armACell=a_key, armBCell=b_key, metrics=rows)
    return out


def render_markdown(report):
    plan = report['plan']
    isolation = report['isolation']
    lines = []
    lines.append('# Community fleet A/B benchmark (pre-change vs new)')
    lines.append('')
    lines.append('Generated %s on this host. Raw counters only: this harness makes **no** architecture '
                 'or performance claim.' % report['generatedAtLocal'])
    lines.append('')
    lines.append('## Frozen plan')
    lines.append('- Plan sha256: `%s`' % plan['planSha256'])
    lines.append('- Matrix: ' + ', '.join('`%s` (arm %s, %s)' % (cell['cell'], cell['arm'], cell['mode'])
                                          for cell in plan['cells']))
    lines.append('- Encounters: %d recovered catalogue ids (all-encounter); focus encounter `%s`'
                 % (len(plan['encounterIds']), plan['focusEncounter']))
    lines.append('- Layout: total T=%d = planner P=%d + battles B=%d, duty %s; requested campaign '
                 'workers per arm A/B = %s/%s'
                 % (plan['totalWorkers'], plan['plannerWorkers'], plan['battleWorkers'], plan['duty'],
                    plan['campaignWorkersByArm']['A'], plan['campaignWorkersByArm']['B']))
    lines.append('- Windows: warm-up %.0fs then a single measured window of >= %.0fs per cell; '
                 'overall timed native execution <= %.0fs (actual %.1fs)'
                 % (plan['warmupSeconds'], plan['measureSeconds'], plan['maxTimedSeconds'],
                    report['totalTimedNativeSeconds']))
    lines.append('')
    lines.append('## Arm A isolation')
    lines.append('- Isolated import copy: `%s`' % isolation['importRoot'])
    lines.append('- Isolated digest copy (never on sys.path): `%s`' % isolation['digestRoot'])
    lines.append('- Preserved pre-change modules: ' + ', '.join('`%s`' % name for name in isolation['preservedModules']))
    lines.append('- Differ from current tree: ' + ', '.join('`%s`' % name for name in isolation['changedVsCurrent']))
    lines.append('- Byte-identical to current: ' + (', '.join('`%s`' % name for name in isolation['identicalToCurrent']) or 'none'))
    lines.append('- Exact limitation: %s' % isolation['limitation'])
    lines.append('')
    lines.append('## Fixed-input correctness gate (before any timing)')
    lines.append('- Strict ordered comparison (fail-closed): requests=%s; results=%s; counters=%s'
                 % (list(GATE_REQUEST_FIELDS), list(GATE_RESULT_FIELDS),
                    ['native', 'fallback', 'errors', 'timeouts']))
    lines.append('- Reported but NOT a correctness condition: `%s` is a throwaway-library '
                 'surrogate key - each arm writes into its own throwaway SQLite library and this is '
                 'that library autoincrement primary key, assigned in storage-local creation order '
                 '(synchronous encounter order on A, async planner-completion order on B). It is not '
                 'a simulation input, so its raw-ID differences are listed per cell below and never '
                 'fail the gate.' % ', '.join(GATE_SURROGATE_FIELDS))
    for name, entry in sorted(report['correctnessGate'].items()):
        lines.append('- `%s`: identical=%s, A(%s requests/%s results) B(%s/%s)'
                     % (name, entry['identical'], entry['armASummary']['requests'],
                        entry['armASummary']['results'], entry['armBSummary']['requests'],
                        entry['armBSummary']['results']))
        for problem in entry['problems']:
            lines.append('  - %s' % json.dumps(problem, sort_keys=True, default=str))
        for section, block in sorted((entry.get('surrogateKey') or {}).get('sections', {}).items()):
            lines.append('  - surrogate `%s` [%s] (throwaway-library key, not a correctness '
                         'condition): matches=%s, firstDifferenceIndex=%s, differences=%s'
                         % (block.get('surrogateKey'), section, block.get('matches'),
                            block.get('firstDifferenceIndex'),
                            json.dumps(block.get('differences'), default=str)))
    lines.append('')
    lines.append('## Measured cells')
    header = ('| cell | seconds | battles | battles/s | native | fallback | errors | active | occupancy '
              '| workerCpu s | ownedCpu s | minFree GB |')
    lines.append(header)
    lines.append('| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |')
    for key in ('A-all', 'B-all', 'A-focus', 'B-focus'):
        cell = report['cells'].get(key) or {}
        owned = cell.get('ownedProcessCpuSecondsByRole') or {}
        lines.append('| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |' % (
            key, cell.get('measurementSeconds'), cell.get('completedBattles'),
            cell.get('battlesPerSecond'), cell.get('nativeResults'), cell.get('fallbackResults'),
            cell.get('errors'), cell.get('activeWorkersAverage'), cell.get('workerOccupancy'),
            cell.get('workerCpuSeconds'),
            round(sum(owned.values()), 3) if owned else None, cell.get('minFreeMemoryGB')))
    lines.append('')
    lines.append('## Owned process roles per cell')
    for key in ('A-all', 'B-all', 'A-focus', 'B-focus'):
        cell = report['cells'].get(key) or {}
        lines.append('- `%s`: cpu=%s, peakWs=%s, pids=%s, sampleIntervalMean=%s'
                     % (key, json.dumps(cell.get('ownedProcessCpuSecondsByRole')),
                        json.dumps(cell.get('ownedProcessPeakWorkingSetBytesByRole')),
                        json.dumps(cell.get('ownedProcessCountByRole')),
                        cell.get('workerSampleIntervalMeanSeconds')))
    lines.append('')
    lines.append('## Raw A-vs-B deltas (no conclusion drawn)')
    for name, entry in sorted(report['comparison'].items()):
        lines.append('- `%s` (`%s` vs `%s`):' % (name, entry['armACell'], entry['armBCell']))
        for metric, row in sorted(entry['metrics'].items()):
            lines.append('  - %s: A=%s B=%s delta=%s ratio=%s'
                         % (metric, row['armA'], row['armB'], row['delta'], row['ratio']))
    lines.append('')
    return '\n'.join(lines) + '\n'


def orchestrate(out, focus_override):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    put_status(stage='isolation', phase='verifying pre-change copy', elapsed=0, total=MAX_TIMED_SECONDS)
    isolation = prepare_arm_a_copy(out)
    ids, titles = catalogue_ids_and_titles()
    focus = pick_focus(ids, focus_override)
    plan = freeze_plan(ids, titles, focus)
    write_json(out / 'plan.json', plan)
    cells = plan['cells']
    total_timed = 0.0

    def arm_roots(arm):
        if arm == 'A':
            return isolation['importRoot'], isolation['digestRoot']
        return str(HERE), None

    correctness_gate = {}
    for mode in ('all', 'focus'):
        focus_value = focus if mode == 'focus' else None
        records = {}
        for arm in ('A', 'B'):
            require_memory_floor('before gate %s arm %s' % (mode, arm))
            arm_root, digest_root = arm_roots(arm)
            payload = run_child(out, arm, arm_root, digest_root, mode, focus_value, 'gate', ids)
            records[arm] = payload
            require_native_gate(payload, 'gate %s arm %s' % (mode, arm))
            total_timed += float(payload.get('nativeSeconds') or 0.0)
            enforce_budget(total_timed, 'gate %s arm %s' % (mode, arm))
            put_status(stage='gate', phase='%s arm %s' % (mode, arm),
                       elapsed=round(total_timed, 1), total=MAX_TIMED_SECONDS)
        correctness_gate[mode] = compare_recordings(records['A'], records['B'])
        put_status(stage='gate', phase='%s compared' % mode, elapsed=round(total_timed, 1),
                   total=MAX_TIMED_SECONDS,
                   gateIdentical=correctness_gate[mode]['identical'])
    if not all(entry['identical'] for entry in correctness_gate.values()):
        raise BenchmarkSafetyError('fixed-input correctness gate failed; timing was NOT started '
                                   '(fail-closed): ' + json.dumps(correctness_gate, sort_keys=True,
                                                                  default=str))

    measures = {}
    for cell in cells:
        require_memory_floor('before measure ' + cell['cell'])
        arm_root, digest_root = arm_roots(cell['arm'])
        payload = run_child(out, cell['arm'], arm_root, digest_root, cell['mode'],
                            cell['focusEncounter'], 'measure', ids)
        measures[cell['cell']] = payload
        require_native_cell(payload, 'measure cell %s' % cell['cell'])
        total_timed += float(payload['timedNativeSeconds'])
        enforce_budget(total_timed, cell['cell'])
        put_status(stage='measure', phase=cell['cell'], elapsed=round(total_timed, 1),
                   total=MAX_TIMED_SECONDS)

    host = dict(logicalProcessors=os.cpu_count(),
                totalMemoryGB=(round(total_memory_bytes() / (1024 ** 3), 1)
                               if total_memory_bytes() else None),
                freeMemoryAtReportGB=free_memory_gb())
    report = dict(schema='community-fleet-ab-report-1',
                  generatedAtLocal=datetime.now().isoformat(timespec='seconds'), host=host,
                  plan=plan, isolation=isolation, correctnessGate=correctness_gate, cells=measures,
                  totalTimedNativeSeconds=round(total_timed, 3),
                  withinBudget=bool(total_timed <= MAX_TIMED_SECONDS),
                  comparison=raw_comparison(measures), outputDir=str(out))
    write_json(out / 'report.json', report)
    markdown = render_markdown(report)
    (out / 'report.md').write_text(markdown, encoding='utf-8')
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_MD.write_text(markdown, encoding='utf-8')
    put_status(stage='finished', phase='complete', elapsed=round(total_timed, 1),
               total=MAX_TIMED_SECONDS, output=str(out), report=str(out / 'report.json'))
    return report


def read_child_progress():
    status_path = ACTIVE_CHILD.get('status')
    if not status_path:
        return None
    path = Path(status_path)
    if not path.is_file():
        return None
    try:
        with path.open('r', encoding='utf-8') as stream:
            rows = stream.readlines()
        if not rows:
            return None
        return json.loads(rows[-1])
    except Exception:  # noqa: BLE001
        return None


def format_duration(seconds):
    """Compact mm:ss / ss text for the monitor; never negative."""
    seconds = max(0.0, float(seconds or 0.0))
    minutes, remainder = divmod(int(seconds), 60)
    if minutes:
        return '%dm%02ds' % (minutes, remainder)
    return '%ds' % remainder


def format_progress_view(now, run_start, latest, child):
    """Pure real-counter view for the visible monitor (no Tk, no throughput estimates).

    ``latest`` is the newest parent ``put_status`` payload and ``child`` is the newest child
    status line (or ``None``). ETA is only calculable for the timed warm-up/measure windows,
    where the phase duration is fixed; gates and setup report ``unknown``. Completed work and
    the battle rate come from the ``RecordingEvaluator`` counters carried in the child status
    (harvested results / measured phase elapsed), never from the configured worker count. The
    gate phase instead reports its real rate as a clearly labelled diagnostic, not a measured
    benchmark rate, while its ETA stays ``unknown``.
    """
    latest = latest or {}
    stage = latest.get('stage') or 'setup'
    view = dict(
        stage=stage, phase=latest.get('phase') or 'working', cell=None,
        wallSeconds=round(max(0.0, now - run_start), 1), wallText=format_duration(now - run_start),
        work='completed/total unknown', percent=None, etaSeconds=None, etaText='ETA unknown',
        rateText='rate unknown (not in a timed window)',
        gate=latest.get('gateIdentical'), warnings=[], errors=[], outcome=None,
        output=latest.get('output'), report=latest.get('report'))
    if child:
        view['cell'] = child.get('cell')
        if child.get('phase'):
            view['phase'] = child.get('phase')
        completed = child.get('completedBattles')
        if completed is not None:
            completed = int(completed)
            phase_elapsed = float(child.get('elapsed') or 0.0)
            total = child.get('total')
            if child.get('phase') in ('warm-up', 'measure') and total:
                total = float(total)
                fraction = min(1.0, (phase_elapsed / total) if total else 0.0)
                view['percent'] = round(100.0 * fraction, 1)
                view['work'] = '%d battles | %s / %s window' % (
                    completed, format_duration(phase_elapsed), format_duration(total))
                view['etaSeconds'] = round(max(0.0, total - phase_elapsed), 1)
                view['etaText'] = 'ETA %s left in phase' % format_duration(view['etaSeconds'])
            elif child.get('phase') == 'gate' and child.get('gatePasses'):
                passes = int(child.get('gatePasses') or 0)
                done = int(child.get('gatePass') or 0)
                view['percent'] = round(100.0 * done / passes, 1) if passes else None
                view['work'] = 'gate pass %d/%d | %d battles' % (done, passes, completed)
            else:
                view['work'] = '%d battles' % completed
            rate = child.get('battlesPerSecond')
            if rate is not None and child.get('phase') in ('warm-up', 'measure'):
                view['rateText'] = '%.2f battles/s (measured this phase)' % float(rate)
            elif rate is not None and child.get('phase') == 'gate':
                view['rateText'] = ('%.2f battles/s (gate diagnostic; not a benchmark rate)'
                                    % float(rate))
        for key in ('errors', 'timeouts'):
            if child.get(key):
                view['errors'].append('%s=%s' % (key, child.get(key)))
        if child.get('warning'):
            view['warnings'].append(str(child.get('warning')))
    for value in (latest.get('warnings') or []):
        view['warnings'].append(str(value))
    for value in (latest.get('errors') or []):
        view['errors'].append(str(value))
    if latest.get('error'):
        view['errors'].append(str(latest.get('error')))
    if stage in ('finished', 'failed'):
        view['outcome'] = stage
    return view


def progress_bar_value(view):
    """Determinate bar value 0..100 from the real phase percent; 0.0 when unknown.

    The bar tracks completed work for the current phase, never timed-budget consumption. An
    unknown phase percent (setup or a child without counters yet) yields an empty determinate
    bar instead of inventing a percentage.
    """
    percent = view.get('percent')
    if percent is None:
        return 0.0
    return max(0.0, min(100.0, float(percent)))


def parse_args(argv):
    parser = argparse.ArgumentParser(description='Community fleet A/B benchmark')
    parser.add_argument('--phase', choices=('gate', 'measure'))
    parser.add_argument('--arm', choices=('A', 'B'))
    parser.add_argument('--mode', choices=('all', 'focus'))
    parser.add_argument('--arm-root')
    parser.add_argument('--digest-root')
    parser.add_argument('--out')
    parser.add_argument('--ids')
    parser.add_argument('--focus')
    parser.add_argument('--focus-encounter')
    return parser.parse_args(argv)


def main_parent(args):
    out = Path(args.out).resolve() if args.out else DEFAULT_OUT
    ACTIVE_OUT['path'] = out
    out.mkdir(parents=True, exist_ok=True)

    def guarded():
        try:
            orchestrate(out, args.focus_encounter)
        except Exception as exc:  # noqa: BLE001
            put_status(stage='failed', phase='error', error='%s: %s' % (type(exc).__name__, exc),
                       traceback=traceback.format_exc(), output=str(out))

    threading.Thread(target=guarded, daemon=True).start()
    try:
        import tkinter as tk
        from tkinter import ttk
        root = tk.Tk()
        root.title('Community Fleet A/B Benchmark')
        root.geometry('820x520')
        root.minsize(720, 440)
        heading = ttk.Label(root, text='Community fleet benchmark (pre-change A vs new B)',
                            font=('Segoe UI', 14, 'bold'))
        heading.pack(anchor='w', padx=16, pady=(14, 6))
        stage = ttk.Label(root, text='Preparing isolated benchmark libraries...')
        stage.pack(anchor='w', padx=16, pady=3)
        bar = ttk.Progressbar(root, mode='determinate', maximum=100.0)
        bar.pack(fill='x', padx=16, pady=7)
        details = ttk.Label(root, text='Warm-up plus one >=60 s measured window per cell.')
        details.pack(anchor='w', padx=16, pady=3)
        status = ttk.Label(root, text='warnings: none | errors: none', wraplength=780)
        status.pack(anchor='w', padx=16, pady=3)
        path_label = ttk.Label(root, text='Output: ' + str(out), wraplength=780)
        path_label.pack(anchor='w', padx=16, pady=3)
        text = tk.Text(root, height=18, wrap='word', font=('Consolas', 9))
        text.pack(fill='both', expand=True, padx=16, pady=(8, 14))
        close_note = ttk.Label(root, text='Closing this window hides the monitor; the benchmark keeps running.')
        close_note.pack(anchor='w', padx=16, pady=(0, 10))
        root.protocol('WM_DELETE_WINDOW', root.withdraw)
        history = []
        outcome = {'terminal': None, 'closing': False}
        run_start = time.monotonic()

        def poll():
            while True:
                try:
                    item = PROGRESS_QUEUE.get_nowait()
                except queue.Empty:
                    break
                history.append(item)
                history[:] = history[-14:]
                if item.get('stage') in ('finished', 'failed'):
                    outcome['terminal'] = item.get('stage')
            child = read_child_progress()
            latest = history[-1] if history else {}
            view = format_progress_view(time.monotonic(), run_start, latest, child)
            bar['value'] = progress_bar_value(view)
            stage_text = '%s | %s' % (view['stage'], view['phase'])
            if view['cell']:
                stage_text += ' | cell %s' % view['cell']
            stage.configure(text=stage_text)
            work = view['work']
            if view['percent'] is not None:
                work = '%s (%.0f%%)' % (work, view['percent'])
            details.configure(text='wall %s | %s | %s | %s' % (
                view['wallText'], work, view['rateText'], view['etaText']))
            status_line = 'gate=%s' % (view['gate'] if view['gate'] is not None else 'pending')
            status_line += ' | warnings: ' + ('; '.join(view['warnings']) or 'none')
            status_line += ' | errors: ' + ('; '.join(view['errors']) or 'none')
            if view['outcome']:
                status_line = 'outcome: %s | %s' % (view['outcome'], status_line)
            status.configure(text=status_line)
            path_label.configure(text='Output: %s%s' % (
                view['output'] or out,
                (' | report: %s' % view['report']) if view['report'] else ''))
            text.delete('1.0', 'end')
            for row in history:
                text.insert('end', json.dumps(row, sort_keys=True, default=str) + '\n')
            text.insert('end', json.dumps(view, sort_keys=True, default=str) + '\n')
            text.see('end')
            if outcome['terminal'] is not None and not outcome['closing']:
                outcome['closing'] = True
                stage.configure(text='%s | closing in 2s' % outcome['terminal'])
                root.after(2000, root.destroy)
            root.after(250, poll)

        poll()
        root.mainloop()
        return 1 if outcome['terminal'] == 'failed' else 0
    except Exception:  # noqa: BLE001
        terminal = None
        while True:
            try:
                item = PROGRESS_QUEUE.get(timeout=1)
                print(json.dumps(item, sort_keys=True, default=str), flush=True)
                if item.get('stage') in ('finished', 'failed'):
                    terminal = item.get('stage')
                    break
            except queue.Empty:
                pass
        return 1 if terminal == 'failed' else 0


def main():
    args = parse_args(sys.argv[1:])
    if args.phase:
        return main_child(args)
    return main_parent(args)


if __name__ == '__main__':
    sys.exit(main())
# end of community fleet A/B benchmark harness
