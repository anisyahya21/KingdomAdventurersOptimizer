"""Continuous coordinator for the frozen Rust, Go and C++ optimizer executables.

Python owns scheduling, checkpoints and library writes only. Search, raw preparation and
battle simulation remain inside the selected native executable.
"""
from __future__ import annotations

import hashlib
import json
import errno
import os
import sys
import threading
import time
import traceback
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
HERE = Path(__file__).resolve().parent
SHARED = HERE.parents[2] / 'coordination/native-preparation/shared'
if str(SHARED) not in sys.path:
    sys.path.insert(0, str(SHARED))
from optimizer_storage import configure_optimizer_storage
STORAGE = configure_optimizer_storage(HERE.parents[2])
from native_state_relocation import prepare_resume_state as _prepare_resume_state
from strategy_native_workflows import NativeWorkflowMixin
from strategy_native_replay import NativeReplayMixin
from strategy_native_assets import validate_engine
from strategy_native_run_config import prepare_native_run
from strategy_native_parent_identity import merge_parent_identity_alias
from strategy_native_ui_projection import project_candidate_view

from native_optimizer_app import (ENGINES, KERNEL_SHA, MANIFEST, check_assets,
                                 digest, engine_path, load, prepare_run, save)


WAVE_SEEDS = 32
_STATUS_TRANSPORT_REFRESH_SECONDS = 5.0
_DATA = Path(os.environ['LOCALAPPDATA']) / 'KingdomAdventurersOptimizer/native-continuous'
_RESUME_RELOCATION_MAP = HERE.parents[2].parent / 'migration-20261005/relocation-map.json'
_RESUME_APPDATA_MANIFEST = HERE.parents[2].parent / 'migration-20261005/optimizer-appdata.sha256.jsonl'
_SEED_BANK_THREAD_LOCK = threading.Lock()

def _prepare_resume_import(pointer, language, run_folder):
    return _prepare_resume_state(
        pointer, language, run_folder, HERE.parents[2], STORAGE['localappdata'],
        _RESUME_RELOCATION_MAP, _RESUME_APPDATA_MANIFEST)



def _durable(output, lang):
    return _durable_from_status(_native_work_status(output), lang)


def _durable_from_status(s, lang):
    key = {'rust': 'completedTrials', 'go': 'durablyCompleted', 'cpp': 'savedRecords'}[lang]
    return max(0, int(s.get(key, s.get('durable', 0))))


def _native_work_status(output):
    """Read only the selected child's small, current status document."""
    try:
        value = load(Path(output) / 'status.json')
    except (OSError, ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def _native_work_counters(language, status):
    """Normalize only published child counters; absent counters stay unknown."""
    if not isinstance(status, dict):
        return {}
    keys = {
        'go': ('nativeInFlight', 'readyQueueDepth', 'executingWorkers',
               'completedUnharvested', 'nativeCompleted', 'nativeWorkerBusySeconds',
               'meanWorkerJobSeconds', 'nativeCallSeconds', 'dispatchPending',
               'acceptedWork', 'nativeSuccessfulBattles',
               'durablyCompletedThisProcess', 'pendingDurableResults'),
        'rust': ('acceptedOutstandingTrials', 'uncommittedCompletedTrials',
                 'remainingUnsubmittedTrialSlots', 'durableTrials',
                 'generationTrialsDone', 'generationTrialsTotal', 'executors'),
    }
    values = {key: status[key] for key in keys.get(language, ()) if key in status}
    if language == 'go':
        numeric = ('nativeSuccessfulBattles', 'durablyCompletedThisProcess',
                   'completedUnharvested', 'nativeInFlight')
        counts = {key: status.get(key) for key in numeric}
        if all(isinstance(value, int) and not isinstance(value, bool) and value >= 0
               for value in counts.values()):
            unsaved = max(0, counts['nativeSuccessfulBattles'] -
                          counts['durablyCompletedThisProcess'])
            awaiting_save = max(0, unsaved - counts['completedUnharvested'])
            values.update(unsavedSuccessfulResults=unsaved,
                          harvestedAwaitingSave=awaiting_save,
                          acceptedOutstanding=counts['nativeInFlight'] + awaiting_save)
    if language == 'cpp' and isinstance(status.get('workTelemetry'), dict):
        values['workTelemetry'] = dict(status['workTelemetry'])
    return values


def _library_record(record):
    """Add a lookup alias for native schemas that call their exact intent rawScenario."""
    if isinstance(record, dict) and not ('intent' in record or 'scenario' in record) \
            and isinstance(record.get('rawScenario'), dict):
        record = dict(record)
        record['scenario'] = record['rawScenario']
    return record


def _process_cpu_seconds(process):
    """Return child-process CPU seconds from the OS, or None when the platform cannot measure it."""
    try:
        if os.name == 'nt':
            import ctypes
            from ctypes import wintypes

            class _FileTime(ctypes.Structure):
                _fields_ = [('low', wintypes.DWORD), ('high', wintypes.DWORD)]

            creation, exit_time, kernel, user = _FileTime(), _FileTime(), _FileTime(), _FileTime()
            api = ctypes.WinDLL('kernel32', use_last_error=True)
            api.GetProcessTimes.argtypes = [wintypes.HANDLE, ctypes.POINTER(_FileTime),
                ctypes.POINTER(_FileTime), ctypes.POINTER(_FileTime), ctypes.POINTER(_FileTime)]
            api.GetProcessTimes.restype = wintypes.BOOL
            if not api.GetProcessTimes(wintypes.HANDLE(int(process._handle)),
                    ctypes.byref(creation), ctypes.byref(exit_time), ctypes.byref(kernel),
                    ctypes.byref(user)):
                return None
            ticks = lambda value: (int(value.high) << 32) | int(value.low)
            return (ticks(kernel) + ticks(user)) / 10_000_000.0
        if sys.platform.startswith('linux'):
            raw = Path(f'/proc/{int(process.pid)}/stat').read_text(encoding='ascii')
            fields = raw[raw.rfind(')') + 2:].split()
            ticks = int(fields[11]) + int(fields[12])  # utime + stime (fields 14 and 15)
            return ticks / float(os.sysconf('SC_CLK_TCK'))
    except (OSError, ValueError, AttributeError, TypeError, IndexError):
        return None
    return None


def _journal_chunk(path, offset):
    try:
        with path.open('rb') as stream:
            stream.seek(offset)
            chunk = stream.read(8 * 1024 * 1024)
            return stream.tell(), chunk
    except PermissionError:
        # Some Windows native writers briefly hold an exclusive journal handle.
        # Leave the cursor intact; import on a later durable counter or after close.
        return offset, b''


@contextmanager
def _seed_bank_guard():
    """Coordinate seed-bank transactions across threads, controllers and processes."""
    _DATA.mkdir(parents=True, exist_ok=True)
    lock_path = _DATA / 'seed-bank.lock'
    started = time.monotonic()
    if not _SEED_BANK_THREAD_LOCK.acquire(timeout=30.0):
        _seed_lock_timeout(lock_path, time.monotonic()-started)
    try:
        with lock_path.open('a+b') as stream:
            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0:
                stream.write(b'\0')
                stream.flush()
                os.fsync(stream.fileno())
            stream.seek(0)
            deadline = started + 30.0
            if os.name == 'nt':
                import msvcrt
                while True:
                    try:
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError as exc:
                        if (getattr(exc, 'winerror', None) != 33
                                and exc.errno not in (errno.EACCES, errno.EAGAIN,
                                                      errno.EDEADLK)):
                            raise
                        if time.monotonic() >= deadline:
                            _seed_lock_timeout(lock_path, time.monotonic()-started)
                        time.sleep(min(0.025, max(0.0, deadline-time.monotonic())))
            else:
                import fcntl
                while True:
                    try:
                        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        if time.monotonic() >= deadline:
                            _seed_lock_timeout(lock_path, time.monotonic()-started)
                        time.sleep(min(0.025, max(0.0, deadline-time.monotonic())))
            try:
                yield
            finally:
                stream.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    finally:
        _SEED_BANK_THREAD_LOCK.release()


def _seed_lock_timeout(lock_path, waited):
    diagnostic = dict(kind='seed-bank-lock-timeout', lockPath=str(lock_path),
        waitedSeconds=round(waited, 3), pid=os.getpid(), threadId=threading.get_ident(),
        occurredUtc=datetime.now(timezone.utc).isoformat(), seedBankChanged=False)
    diagnostic_path = _DATA / (f"seed-bank-lock-timeout-{os.getpid()}-"
                               f"{threading.get_ident()}-{time.time_ns()}.json")
    try:
        save(diagnostic_path, diagnostic)
    except OSError:
        pass
    raise RuntimeError(f"Timed out after {waited:.1f}s acquiring the seed-bank lock at "
                       f"{lock_path}; the seed bank was not changed. Diagnostic: "
                       f"{diagnostic_path}")


def _seed_bank_read(path):
    if not path.exists():
        return {'next': 100001}
    state = load(path)
    if not isinstance(state, dict):
        raise RuntimeError('Seed bank is not a JSON object; refusing to reset or reuse seeds.')
    if 'next' not in state:
        raise RuntimeError('Existing seed bank has no next cursor; refusing to reset or reuse seeds.')
    next_value = state['next']
    if isinstance(next_value, bool) or not isinstance(next_value, int):
        raise RuntimeError('Seed bank next value is invalid; refusing to reset or reuse seeds.')
    return dict(state, next=next_value)


def _seed_bank_write(path, state):
    save(path, state)
    # ``save`` fsyncs the temporary file before atomic replacement. Sync the directory too
    # where the platform supports it, so a returned reservation survives a sudden power loss.
    if os.name != 'nt':
        directory_fd = os.open(str(path.parent), os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)


class NativeOptimizer(NativeReplayMixin, NativeWorkflowMixin):
    """Old desktop-controller surface backed by unbounded, resumable native waves."""
    def __init__(self, path, language='rust'):
        self.path = Path(path).resolve()
        self.language = str(language).lower()
        if self.language not in ENGINES:
            raise ValueError('language must be rust, go or cpp')
        self._engine_assets = validate_engine(self.language)
        self._execution_mode = ('production' if self._engine_assets['source'] == 'finishing-manifest'
                                else 'diagnostic')
        from strategy_native_library import NativeLibrary
        self._library_type = NativeLibrary
        _DATA.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._job_lock = threading.Lock()
        self._commands = __import__('queue').Queue()
        self.commands = self._commands
        self._library_calls = __import__('queue').Queue()
        self._library_ready = threading.Event()
        self._library_error = None
        self.shutdown_event = threading.Event()
        self._lib_thread = threading.Thread(target=self._library_loop, daemon=True,
                                            name='native-library-writer')
        self._lib_thread.start()
        if not self._library_ready.wait(30):
            raise TimeoutError('Native strategy library writer did not initialize.')
        if self._library_error:
            raise RuntimeError(self._library_error)
        self._state = 'Stopped'
        self._drain_target = 'Stopped'
        self._error = None
        self._error_traceback = None
        self._focus = list(range(20))
        self._scheduler_mode = os.environ.get('KA_NATIVE_SCHEDULER_MODE', 'mixed')
        if self._scheduler_mode not in ('mixed', 'encounter-sequential'):
            raise ValueError('KA_NATIVE_SCHEDULER_MODE must be mixed or encounter-sequential')
        from strategy_optimizer_limits import default_workers, worker_ceiling, MIN_DUTY, MAX_DUTY
        self._worker_ceiling = min(worker_ceiling(), max(1, int((os.cpu_count() or 1) * .8)))
        self._workers = default_workers()
        self._duty = .9
        self._duty_range = (MIN_DUTY, MAX_DUTY)
        self._active = None
        self._active_started = None
        self._child = None
        self._wave_thread = None
        self._started = None
        self._session_started = None
        self._session_elapsed_accumulated = 0.0
        self._session_clock_started = None
        self._session_completed_base = None
        self._session_committed = 0
        self._session_cpu_seconds = 0.0
        self._completed = 0
        self._fresh_rate = None
        self._wave_completed = 0
        self._wave_total = None
        self._process_cpu_pid = None
        self._process_cpu_samples = deque(maxlen=256)
        self._wave_process_cpu_start = None
        self._last_wave_cpu_seconds_per_run = None
        self._library_total_runs = int(self._libcall('get', 'totalRuns', 0) or 0)
        self._last_native_work_counters = {}
        self._encounter = None
        self._history = []
        self._status_cache_at = 0.0
        self._status_library_cache = None
        self._status_snapshot_lock = threading.Lock()
        self._status_transport_lock = threading.Lock()
        self._status_transport_json = None
        self._status_transport_updated_at = 0.0
        self._status_transport_started_at = 0.0
        self._status_transport_refreshing = False
        self._status_transport_error = None
        self._diagnostic_export_lock = threading.Lock()
        self._diagnostic_export = None
        self._meta_load()
        self.thread = threading.Thread(target=self._loop, daemon=True, name='native-optimizer-controller')
        self.thread.start()

    def _meta_load(self):
        try:
            settings = self._libcall('get', 'nativeController', {})
            if settings.get('language') == self.language:
                self._focus = self._valid_focus(settings.get('focusEncounters', []))
                self._workers = self._valid_workers(settings.get('workers', self._workers))
                self._duty = self._valid_duty(settings.get('duty', self._duty))
        except Exception:
            pass

    def _set_state_locked(self, state, now=None):
        """Transition state while accumulating only Running/Saving session time."""
        now = time.monotonic() if now is None else now
        was_timed = self._state in ('Running', 'Saving')
        will_be_timed = state in ('Running', 'Saving')
        if was_timed and not will_be_timed and self._session_clock_started is not None:
            self._session_elapsed_accumulated += max(0.0, now - self._session_clock_started)
            self._session_clock_started = None
        elif will_be_timed and not was_timed and self._session_started is not None:
            self._session_clock_started = now
        self._state = state

    def _session_elapsed_locked(self, now):
        if self._session_started is None:
            return None
        elapsed = self._session_elapsed_accumulated
        if self._session_clock_started is not None:
            elapsed += max(0.0, now - self._session_clock_started)
        return elapsed

    @staticmethod
    def _valid_focus(value):
        if value in (None, []):
            return list(range(20))
        if not isinstance(value, (list, tuple)):
            raise ValueError('focus_encounter expects encounterIds or an empty list for all encounters')
        out = []
        for item in value:
            if isinstance(item, bool) or not isinstance(item, int) or not 0 <= item <= 19:
                raise ValueError('encounter IDs must be integers from 0 through 19')
            if item not in out:
                out.append(item)
        return out or list(range(20))

    def _valid_workers(self, value):
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= self._worker_ceiling:
            raise ValueError(f'workers must be a whole number in 1..{self._worker_ceiling}')
        return value

    def _valid_duty(self, value):
        low, high = self._duty_range
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
            raise ValueError(f'duty must be a number in {low}..{high}')
        return float(value)

    def _persist_settings(self):
        current = dict(self._libcall('get', 'nativeController', {}))
        current.update(language=self.language, focusEncounters=self._focus, workers=self._workers,
                       duty=self._duty)
        self._libcall('set', 'nativeController', current)

    def _library_loop(self):
        try:
            self._library = self._library_type(self.path, self.language, provenance={
                'source': 'continuous-native-controller',
                'mechanicsRevision': self._engine_assets['kernel']['sha256'],
                'kernelSha256': self._engine_assets['kernel']['sha256'],
                'executableSha256': self._engine_assets['executable']['sha256'],
                'revision': self._engine_assets['revision'],
                'executionMode': self._execution_mode,
                'purpose': 'strategy-search',
                'engineAssets': json.loads(json.dumps(self._engine_assets, default=str)),
                'policy': 'strategy-outcomes-terminal-policy-dispatch-v1'})
        except Exception as exc:
            self._library_error = str(exc)
            self._library_ready.set()
            return
        self._library_ready.set()
        while not self.shutdown_event.is_set() or not self._library_calls.empty():
            try:
                method, args, done, result = self._library_calls.get(timeout=.2)
            except __import__('queue').Empty:
                continue
            try:
                if method == '_native_seed_inputs':
                    enc, limit = args
                    found = self._library.candidate_seed_inputs(enc, limit)
                    if len(found) < limit:
                        # Bootstrap exact intents from the user's original library. They are
                        # not copied observations or rewards: only native execution can measure
                        # them in this isolated engine scope. The frozen engine decides legality.
                        tables = {r[0] for r in self._library.db.execute(
                            "SELECT name FROM sqlite_master WHERE type='table'")}
                        if 'candidate' in tables:
                            for row in self._library.db.execute(
                                    'SELECT id,scenario FROM candidate ORDER BY created DESC,id'):
                                try:
                                    scenario = json.loads(row['scenario'])
                                except Exception:
                                    continue
                                if (not isinstance(scenario, dict) or scenario.get('encounterId') != enc
                                        or int(scenario.get('defeatCount') or 0) != 0):
                                    continue
                                candidate_id = str(row['id'])
                                alias = dict(candidateId=candidate_id, scenario=scenario,
                                             source='original-library', verificationOnly=True)
                                same_id = next((item for item in found
                                                if str(item.get('candidateId', '')) == candidate_id), None)
                                if same_id is not None:
                                    # Candidate IDs identify seed-stripped strategies. Retain a
                                    # seed-variant source row as provenance only when identity is
                                    # exact; the native run still receives one stable parent ID.
                                    merge_parent_identity_alias(same_id, alias)
                                    continue
                                if any(item.get('scenario') == scenario for item in found):
                                    continue
                                found.append(alias)
                                if len(found) >= limit:
                                    break
                    if not found:
                        source = load(MANIFEST)['outputs']['all20Candidates']
                        source_path = Path(source['path'])
                        if digest(source_path) != source['sha256']:
                            raise RuntimeError('Original all-encounter parent input hash changed.')
                        raw = load(source_path)
                        baselines = raw.get('candidates', []) if isinstance(raw, dict) else raw
                        for intent in baselines:
                            if intent.get('encounterId') == enc:
                                cid = self._library.import_candidate(intent, 'Original encounter baseline')
                                found.append(dict(candidateId=cid, scenario=intent,
                                    source='original-encounter-baseline', measured=False))
                                if len(found) >= limit:
                                    break
                    result['value'] = found
                elif method == '_native_strategy_list':
                    enc, defeat = args
                    result['value'] = self._original_strategy_list(enc, defeat)
                elif method == '_native_status_snapshot':
                    candidates = []
                    for enc in range(20):
                        for row in self._library.strategies(enc, 0).get('strategies', []):
                            candidates.append(self._native_candidate_view(row))
                    stats = self._library.encounter_stats()
                    try:
                        from strategy_optimizer import encounter_catalogue, encounter_campaigns
                        catalogue = encounter_catalogue()
                        campaigns = encounter_campaigns(catalogue)
                    except Exception:
                        catalogue = {}
                        campaigns = []
                    library_encounters = {int(row['intent']['encounterId']) for row in candidates
                                         if isinstance(row.get('intent'), dict)
                                         and isinstance(row['intent'].get('encounterId'), int)}
                    tables = {r[0] for r in self._library.db.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'")}
                    if 'candidate_meta' in tables:
                        library_encounters.update(int(r[0]) for r in self._library.db.execute(
                            'SELECT DISTINCT encounter FROM candidate_meta'))
                    result['value'] = dict(candidates=candidates, encounterStats=stats,
                        encounterAverageLeaders=self._library.leaders(),
                        totalRuns=self._library.get('totalRuns', 0), proposals=len(candidates),
                        improvements=0, recordHolders=self._library.record_holders(),
                        libraryEncounters=sorted(library_encounters),
                        encounters=catalogue, campaigns=campaigns)
                elif method == '_native_detail':
                    candidate_id, = args
                    detail = self._library.detail(candidate_id)
                    if detail.get('ok'):
                        result['value'] = detail
                    else:
                        result['value'] = self._original_detail(candidate_id)
                elif method == '_native_evidence_runs':
                    result['value'] = self._library.evidence_runs(*args)
                elif method == '_native_seed_pairs':
                    result['value'] = self._library.seed_pairs(*args)
                elif method == '_set_run_provenance':
                    extra = args[0]
                    if hasattr(self._library, 'set_run_provenance'):
                        self._library.set_run_provenance(extra)
                    else:
                        self._library.provenance = dict(self._library.provenance, **extra)
                    result['value'] = True
                elif method == 'export_diagnostics':
                    result['value'] = self._library.export_diagnostics(*args)
                else:
                    result['value'] = getattr(self._library, method)(*args)
            except Exception as exc:
                result['error'] = exc
            finally:
                done.set()
        try:
            self._library.db.close()
        except Exception:
            pass

    def _original_strategy_list(self, enc, defeat):
        value = self._library.strategies(enc, defeat)
        rows = list(value.get('strategies', []))
        seen = {row['candidateId'] for row in rows}
        seen_intents = {json.dumps(row.get('intent'), sort_keys=True, separators=(',', ':')) for row in rows}
        tables = {r[0] for r in self._library.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if 'candidate' not in tables:
            return value
        if 'candidate_meta' in tables:
            selected = self._library.db.execute(
                'SELECT c.id,c.label,c.source,c.scenario FROM candidate c JOIN candidate_meta m '
                'ON m.id=c.id WHERE m.encounter=? AND m.defeat=? ORDER BY c.created,c.id',
                (enc, defeat))
            incomplete = getattr(self._library, '_native_meta_index_incomplete', None)
            if incomplete is None:
                incomplete = bool(self._library.db.execute(
                    'SELECT 1 FROM candidate c LEFT JOIN candidate_meta m ON m.id=c.id '
                    'WHERE m.id IS NULL LIMIT 1').fetchone())
                self._library._native_meta_index_incomplete = incomplete
            if incomplete:
                missing = self._library.db.execute(
                    'SELECT c.id,c.label,c.source,c.scenario FROM candidate c LEFT JOIN candidate_meta m '
                    'ON m.id=c.id WHERE m.id IS NULL ORDER BY c.created,c.id')
                candidate_rows = list(selected) + list(missing)
            else:
                candidate_rows = selected
        else:
            candidate_rows = self._library.db.execute(
                'SELECT id,label,source,scenario FROM candidate ORDER BY created,id')
        for row in candidate_rows:
            if row['id'] in seen:
                continue
            try:
                scenario = json.loads(row['scenario'])
                row_enc = scenario.get('encounterId')
                row_defeat = scenario.get('defeatCount') or 0
            except Exception:
                continue
            if row_enc != enc or row_defeat != defeat:
                continue
            signature = json.dumps(scenario, sort_keys=True, separators=(',', ':'))
            if signature in seen_intents:
                continue
            seen_intents.add(signature)
            rows.append(dict(candidateId=row['id'], label=row['label'], source=row['source'],
                displayName=row['label'], creator=row['source'], change=None, parentId=None,
                parentProducer=None, nameDerived=False, nameDetail=None, attempts=0, wins=0,
                losses=0, noVerdict=0, meanEarned=None, earnedSamples=0, meanPotential=None,
                potentialSamples=0, bestEarned=None, bestPotential=None, winRate=None,
                comparable=False, retainedSampleCount=0, eaMeanEarned=None, eaBestEarned=None,
                eaEarnedSamples=0, verificationOnly=True, native=True, originalCandidate=True,
                measured=False, intent=scenario, nativeFlags=[]))
        rows.sort(key=lambda row: (row.get('meanEarned') if row.get('meanEarned') is not None else -1,
                                   row.get('bestEarned') if row.get('bestEarned') is not None else -1,
                                   row['candidateId']), reverse=True)
        value['strategies'] = rows
        value['originalUnmeasuredIncluded'] = True
        return value

    def _original_detail(self, candidate_id):
        tables = {r[0] for r in self._library.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if 'candidate' not in tables:
            return {'ok': False, 'error': 'Candidate is not in the native library.'}
        row = self._library.db.execute('SELECT id,label,source,scenario FROM candidate WHERE id=?',
                                       (candidate_id,)).fetchone()
        if row is None:
            return {'ok': False, 'error': 'Candidate is not in the selected library.'}
        return dict(ok=True, candidateId=row['id'], label=row['label'], scenario=json.loads(row['scenario']),
                    holder=None, resident=True, storedRuns=[], trialRuns=[],
                    storedSummary={'attempts': 0, 'samples': 0, 'meanEarned': None, 'bestEarned': None,
                                   'verificationOnly': True, 'originalMeasurementsExcluded': True},
                    trialSummary={'attempts': 0, 'samples': 0, 'meanEarned': None, 'bestEarned': None},
                    encounterLedger=None, outcomeDistribution=None, formation=None,
                    verificationOnly=True, native=True, originalCandidate=True, measured=False,
                    source=row['source'], provenance={'intentSource': 'original-library',
                    'measurementsImported': False})

    def _libcall(self, method, *args):
        done, result = threading.Event(), {}
        self._library_calls.put((method, args, done, result))
        if not done.wait(120):
            raise TimeoutError(f'Native library operation {method} is still pending.')
        if 'error' in result:
            raise result['error']
        if method in ('import_record', 'import_records', 'import_candidate'):
            self._status_cache_at = 0.0
            self._native_library_import_epoch = getattr(self, '_native_library_import_epoch', 0) + 1
        return result.get('value')

    def _import_native_lines(self, lines, records, retain_records):
        imported = 0
        for offset in range(0, len(lines), 128):
            batch = [json.loads(line) for line in lines[offset:offset + 128]]
            results = self._libcall('import_records', self.language,
                                    [_library_record(row) for row in batch])
            with self._lock:
                inserted = sum(not row.get('duplicate', False) for row in results)
                self._session_committed += inserted
                self._library_total_runs += inserted
            if retain_records:
                records.extend(batch)
            imported += len(batch)
        return imported

    def command(self, action, value=None, wait=False):
        value = value or {}
        if not isinstance(value, dict):
            return {'ok': False, 'error': 'Command value must be an object.'}
        ack = threading.Event()
        result = {}
        self._commands.put((action, value, ack, result))
        if wait:
            if not ack.wait(10):
                raise TimeoutError('Command is pending; inspect status for its outcome.')
            return result
        return {'ok': True, 'pending': True}

    def _loop(self):
        while not self.shutdown_event.is_set():
            try:
                action, value, ack, result = self._commands.get(timeout=.25)
            except __import__('queue').Empty:
                continue
            try:
                result.update(self._apply(action, value))
            except Exception as exc:
                result.update(ok=False, error=str(exc))
            finally:
                ack.set()

    def _apply(self, action, value):
        if action in ('import', 'import_build'):
            return self._workflow_import_build(value.get('scenario'), value.get('label', 'Imported native build'))
        if action == 'end_experiment':
            return self._workflow_end_experiment()
        if action == 'probe':
            return self._workflow_start_native_probe(value.get('candidateId'), value.get('axis'),
                axis2=value.get('axis2'), unit=value.get('unit'),
                unestablished=value.get('unestablished', False),
                count=value.get('runs', value.get('count', 160)),
                values=value.get('values'), values2=value.get('values2'),
                points=value.get('points', 7), points2=value.get('points2'))
        if action == 'fine_tune':
            return self._workflow_start_native_finetune(value.get('candidateId'), value.get('axis'),
                unit=value.get('unit'), targets=value.get('targets'),
                axis2=value.get('axis2'), targets2=value.get('targets2'),
                count=value.get('budget', {}).get('pointBank', value.get('count', 128)))
        if action == 'focused_experiment':
            if value.get('mode') in ('skills', 'remove-skills'):
                return self._workflow_start_native_skills(value.get('candidateId'),
                    unit=value.get('unit'), count=value.get('count', 128))
            return self._workflow_start_strategy_experiment(value.get('candidateId'),
                value.get('holderKey'), value.get('count', 4096), value.get('mode', 'evaluate'))
        if action in ('start', 'community_start', 'keep', 'all_encounters'):
            with self._lock:
                if self._state == 'Saving':
                    raise RuntimeError('Wait for the current native wave to drain before starting again.')
                workers = self._valid_workers(value.get('workers', self._workers))
                duty = self._valid_duty(value.get('duty', self._duty))
                if action == 'all_encounters':
                    self._focus = list(range(20))
                if self._state == 'Stopped':
                    self._session_started = None
                    self._session_elapsed_accumulated = 0.0
                    self._session_clock_started = None
                    self._session_completed_base = self._completed
                    self._session_committed = 0
                    self._session_cpu_seconds = 0.0
                    self._last_wave_cpu_seconds_per_run = None
                self._workers, self._duty = workers, duty
                self._error = None
                self._error_traceback = None
                self._session_started = self._session_started or time.monotonic()
                self._set_state_locked('Running')
                self._persist_settings()
                if self._wave_thread is None or not self._wave_thread.is_alive():
                    self._wave_thread = threading.Thread(target=self._run_forever, daemon=True,
                                                         name='native-optimizer-waves')
                    self._wave_thread.start()
            return {'ok': True}
        if action in ('pause', 'stop'):
            with self._lock:
                target = 'Paused' if action == 'pause' else 'Stopped'
                if self._active:
                    self._drain_target = target
                    self._set_state_locked('Saving')
                else:
                    self._set_state_locked(target)
                self._write_native_control_locked()
            return {'ok': True, 'draining': bool(self._active)}
        if action == 'focus_encounter':
            ids = value.get('encounterIds', value.get('focusEncounters',
                  value.get('encounters', value.get('ids', value.get('encounterId', [])))))
            if isinstance(ids, int) and not isinstance(ids, bool):
                ids = [ids]
            with self._lock:
                self._focus = self._valid_focus(ids)
                self._write_native_control_locked()
                self._persist_settings()
            return {'ok': True, 'encounterIds': self._focus}
        if action in ('workers', 'duty'):
            with self._lock:
                if action == 'workers' or 'workers' in value:
                    self._workers = self._valid_workers(value.get('workers', self._workers))
                if action == 'duty' or 'duty' in value:
                    self._duty = self._valid_duty(value.get('duty', self._duty))
                self._persist_settings()
            return {'ok': True, 'workers': self._workers, 'duty': self._duty}
        if action == 'status':
            return {'ok': True, 'status': self.status()}
        if action == 'close':
            with self._lock:
                self._drain_target = 'Stopped'
                self._set_state_locked('Saving' if self._active else 'Stopped')
            thread = self._wave_thread
            if thread and thread.is_alive() and thread is not threading.current_thread():
                thread.join()
            self.shutdown_event.set()
            return {'ok': True, 'drained': True}
        raise ValueError('Unsupported native controller command: ' + str(action))

    def _run_forever(self):
        cursor = 0
        try:
            while True:
                with self._lock:
                    if self._state != 'Running':
                        return
                    encs = list(self._focus)
                    enc = encs[cursor % len(encs)]
                    workers = self._workers
                if self._workflow_run_pending_chunk():
                    continue
                try:
                    if self._scheduler_mode == 'mixed':
                        self._run_portfolio(workers)
                    else:
                        self._run_wave(enc, workers, scheduled=True)
                    cursor += 1
                except Exception as exc:
                    with self._lock:
                        self._error = str(exc)
                        self._error_traceback = traceback.format_exc()
                        self._set_state_locked('Error')
                    return
        finally:
            with self._lock:
                self._wave_thread = None
                if self._state == 'Running' and not self.shutdown_event.is_set():
                    self._wave_thread = threading.Thread(target=self._run_forever, daemon=True,
                                                        name='native-optimizer-waves')
                    self._wave_thread.start()

    def _run_portfolio(self, workers):
        if not self._job_lock.acquire(blocking=False):
            raise RuntimeError('Another native drain is active')
        try:
            with self._lock:
                if self._state != 'Running':
                    return []
                focus = list(self._focus)
                self._active = dict(encounterId=None, encounterIds=list(range(20)),
                                    stage='preparing', pid=None)
                self._active_started = time.monotonic()
            parents = []
            for encounter in range(20):
                found = self._libcall('_native_seed_inputs', encounter, 8)
                if not found:
                    raise RuntimeError(f'No exact native parent intent for encounter{encounter}')
                parents.extend(found)
            folder = _DATA / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') +
                              f'-{self.language}-portfolio')
            state_key = f'nativeSearchState:{self.language}:portfolio'
            previous = self._libcall('get', state_key, None)
            resolved_previous, prior, _resume_relocation = _prepare_resume_import(
                previous, self.language, folder)
            pending_pairs = prior.get('pendingSeedPairs') if isinstance(prior, dict) else None
            if self.language == 'rust' and isinstance(prior, dict):
                pending_selected = [row for row in (prior.get('pendingPopulation') or [])
                    if row.get('intent', {}).get('encounterId') in focus]
                if prior.get('pendingGeneration') is None or not pending_selected:
                    pending_pairs = None
            if self.language == 'cpp' and isinstance(prior, dict):
                pending_policy = prior.get('pendingSeedPolicy')
                if isinstance(pending_policy, dict):
                    pending_pairs = pending_policy.get('seedPairs')
            if pending_pairs:
                pairs = pending_pairs
                seed = int(pairs[0][0])
            else:
                seed = self._allocate_seed(focus[0])
                pairs = [[seed+i, seed+1000003+i] for i in range(WAVE_SEEDS)]
            if self.language == 'go' and isinstance(prior, dict):
                # Deferred reservations retain their exact old executed pair, even
                # when parked by Focus. Admit those pairs without changing them.
                admitted_pairs = {tuple(pair) for pair in pairs}
                for reservation in prior.get('deferred', []):
                    raw = reservation.get('raw') if isinstance(reservation, dict) else None
                    if not isinstance(raw, dict):
                        raise ValueError('Go deferred reservation lacks exact raw intent')
                    pair = (raw.get('mathSeed'), raw.get('libSeed'))
                    if any(isinstance(value, bool) or not isinstance(value, int) for value in pair):
                        raise ValueError('Go deferred reservation lacks exact integer seeds')
                    if pair not in admitted_pairs:
                        pairs.append(list(pair))
                        admitted_pairs.add(pair)
            options = dict(language=self.language, workers=workers, generations=2,
                           mode='search', searchSeed=seed, purpose='strategy-search',
                           mixedEncounters=True, focusEncounters=focus)
            if self.language == 'go':
                options['budget'] = WAVE_SEEDS * len(focus)
            if prior is not None:
                options['initialCheckpoint' if self.language == 'rust' else 'searchStatePath'] = str(resolved_previous)
                if self.language == 'cpp' and isinstance(prior.get('pendingSeedPolicy'), dict):
                    options['pendingSeedPolicy'] = dict(prior['pendingSeedPolicy'])
                if self.language == 'go':
                    options['searchSeed'] = int(prior.get('config', {}).get('seed', seed))
            spec = prepare_native_run(options, folder, parents, pairs, engine=self._engine_assets)
            spec.update(seedStart=seed, candidateParents=[p.get('candidateId') for p in parents],
                        inputSetSha256=spec['inputHashes'].get('sourceSetSha256'))
            if self.language == 'rust' and isinstance(prior, dict):
                banks = prior.get('pendingSeedPairsByEncounter') or {}
                spec['admittedSeedPairsByEncounter'] = {
                    str(encounter): [list(pair) for pair in dict.fromkeys(
                        tuple(pair) for pair in list(pairs)+list(banks.get(str(encounter), [])))]
                    for encounter in range(20)}
            save(folder / 'run-spec.json', spec)
            with self._lock:
                self._active['controlPath'] = spec['controlPath']
                self._write_native_control_locked()
            self._launch_native(folder, spec, None)
            state = Path(spec['output']) / ('checkpoint.json' if self.language == 'rust' else 'search-state.json')
            if state.is_file():
                self._libcall('set', state_key, str(state))
            return []
        finally:
            with self._lock:
                self._active = None
                self._active_started = None
                if self._state == 'Saving':
                    self._set_state_locked(self._drain_target)
            self._job_lock.release()

    def _write_native_control_locked(self):
        path = (self._active or {}).get('controlPath')
        if path:
            save(Path(path), {'focusEncounterIds': list(self._focus),
                 'pauseRequested': self._state == 'Saving' and self._drain_target == 'Paused',
                 'stopRequested': self._state != 'Running' and self._drain_target != 'Paused'})

    def _run_wave(self, enc, workers, parents=None, seeds=None, search=True, scheduled=False):
        if not self._job_lock.acquire(blocking=False):
            raise RuntimeError('Another native wave is active; exact evaluation cannot overlap continuous search.')
        try:
            with self._lock:
                if scheduled and (self._state != 'Running' or enc not in self._focus):
                    return []
                self._active = dict(encounterId=enc, stage='preparing', pid=None)
                self._active_started = time.monotonic()
            return self._execute_wave(enc, workers, parents, seeds, search)
        finally:
            with self._lock:
                self._active = None
                self._active_started = None
                if self._state == 'Saving':
                    self._set_state_locked(self._drain_target)
            self._job_lock.release()

    def _execute_wave(self, enc, workers, parents=None, seeds=None, search=True):
        now = datetime.now(timezone.utc)
        folder = _DATA / (now.strftime('%Y%m%dT%H%M%S%fZ') + f'-{self.language}-e{enc}')
        parents = parents if parents is not None else self._libcall('_native_seed_inputs', enc, 8)
        if not parents:
            raise RuntimeError(f'No exact native parent intents are available for encounter {enc}.')
        parents = list(parents)
        seed = self._allocate_seed(enc) if seeds is None else int(seeds[0][0])
        pairs = seeds if seeds is not None else [[seed+i, seed+1000003+i] for i in range(WAVE_SEEDS)]
        opts = dict(language=self.language, encounterId=enc, workers=workers,
                    generations=2 if search else 1, mode='search' if search else 'evaluate',
                    searchSeed=seed, purpose='strategy-search' if search else 'strategy-evaluate')
        state_key = f'nativeSearchState:{self.language}:{enc}'
        previous_state = self._libcall('get', state_key, None) if search else None
        resolved_previous, prior, _resume_relocation = _prepare_resume_import(
            previous_state, self.language, folder)
        if resolved_previous is not None:
            opts['searchStatePath'] = str(resolved_previous)
            if self.language == 'go' and isinstance(prior, dict):
                opts['searchSeed'] = int(prior.get('config', {}).get('seed', seed))
        spec = prepare_native_run(opts, folder, parents, pairs, engine=self._engine_assets)
        spec['seedStart'] = seed
        spec['candidateParents'] = [row.get('candidateId') for row in parents if isinstance(row, dict)]
        spec['inputSetSha256'] = spec['inputHashes'].get('sourceSetSha256')
        save(folder / 'run-spec.json', spec)
        save(folder / 'checkpoint.json', {'state':'prepared', 'encounterId':enc,
            'seedPairs':pairs, 'specSha256':digest(folder/'run-spec.json')})
        records = self._launch_native(folder, spec, enc)
        search_state = Path(spec['output']) / 'search-state.json'
        if search and search_state.is_file():
            self._libcall('set', state_key, str(search_state))
        return records

    def _allocate_seed(self, enc, count=WAVE_SEEDS):
        """Atomically consume a durable contiguous block from the shared seed bank."""
        if isinstance(enc, bool) or not isinstance(enc, int) or not 0 <= enc < 20:
            raise ValueError('encounterId must be an integer from 0 through 19')
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise ValueError('seed count must be a positive integer')
        p = _DATA / 'seed-bank.json'
        with _seed_bank_guard():
            state = _seed_bank_read(p)
            n = state['next']
            end = n + count
            if n < 1 or end + 1000003 >= 2**31:
                raise RuntimeError('Seed bank exhausted its nonnegative 31-bit range.')
            state.update(next=end, lastEncounter=enc,
                updatedUtc=datetime.now(timezone.utc).isoformat(),
                lastReservation=dict(encounterId=enc, start=n, count=count, next=end))
            _seed_bank_write(p, state)
            return n

    def _save_next_seed(self, enc, value):
        """Advance legacy workflow cursors monotonically; this method never frees seeds."""
        if isinstance(enc, bool) or not isinstance(enc, int) or not 0 <= enc < 20:
            raise ValueError('encounterId must be an integer from 0 through 19')
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError('next seed value must be a positive integer')
        if value >= 2**31:
            raise ValueError('next seed value must remain inside the nonnegative 31-bit range')
        p = _DATA / 'seed-bank.json'
        with _seed_bank_guard():
            state = _seed_bank_read(p)
            current = state['next']
            if value < current:
                raise RuntimeError('Seed bank cursor cannot move backwards or reuse a reserved seed.')
            if value == current:
                return current
            state.update(next=value, lastEncounter=enc,
                updatedUtc=datetime.now(timezone.utc).isoformat(),
                lastReservation=dict(encounterId=enc, start=current,
                                     count=value-current, next=value, advanced=True))
            _seed_bank_write(p, state)
            return value

    def _reserve_seed_pairs(self, enc, count, used=(), pair_builder=None):
        """Reserve unique seed pairs and persist the next cursor before returning them.

        Workflow investigations can pass their deterministic pair builder so used historical pairs
        are skipped inside the same cross-process transaction instead of allocating a range and
        trying to advance its cursor later.
        """
        if isinstance(enc, bool) or not isinstance(enc, int) or not 0 <= enc < 20:
            raise ValueError('encounterId must be an integer from 0 through 19')
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise ValueError('seed count must be a positive integer')
        if pair_builder is not None and not callable(pair_builder):
            raise ValueError('pair_builder must be callable when supplied')
        used_pairs = set()
        for pair in used or ():
            if isinstance(pair, (list, tuple)) and len(pair) == 2 and all(
                    isinstance(x, int) and not isinstance(x, bool) and 0 <= x < 2**31
                    for x in pair):
                used_pairs.add(tuple(pair))
        p = _DATA / 'seed-bank.json'
        with _seed_bank_guard():
            state = _seed_bank_read(p)
            start = cursor = state['next']
            if cursor < 1 or cursor >= 2**31:
                raise RuntimeError('Seed bank exhausted its nonnegative 31-bit range.')
            pairs = []
            while len(pairs) < count:
                if cursor >= 2**31:
                    raise RuntimeError('Seed bank exhausted its nonnegative 31-bit range.')
                pair = (pair_builder(cursor) if pair_builder is not None
                        else [cursor, cursor+1000003])
                if (not isinstance(pair, (list, tuple)) or len(pair) != 2 or any(
                        isinstance(x, bool) or not isinstance(x, int) or x < 0 or x >= 2**31
                        for x in pair)):
                    raise RuntimeError('Seed pair builder returned values outside the nonnegative 31-bit range.')
                cursor += 1
                key = tuple(pair)
                if key in used_pairs:
                    continue
                used_pairs.add(key)
                pairs.append([int(pair[0]), int(pair[1])])
            state.update(next=cursor, lastEncounter=enc,
                updatedUtc=datetime.now(timezone.utc).isoformat(),
                lastReservation=dict(encounterId=enc, start=start, count=count,
                                     skipped=cursor-start-count, next=cursor))
            _seed_bank_write(p, state)
            return pairs

    def _launch_native(self, folder, spec, enc):
        import subprocess
        import native_optimizer_app as app
        import language_test_queue as queue
        lock_path = queue.locked()
        child = None
        output = Path(spec['output'])
        journal = output / ('battles.jsonl' if self.language == 'rust' else 'results.jsonl')
        offset = 0
        observed = 0
        started = time.monotonic()
        imported = 0
        raw_records = []
        pending = b''
        ready_rows = []
        spec_digest = digest(folder / 'run-spec.json')
        with self._lock:
            self._active = dict(directory=str(folder), encounterId=enc, pid=None,
                                encounterIds=spec.get('encounterIds'),
                                controlPath=spec.get('controlPath'),
                                stage='launching', accepted=True, durable=0, imported=0,
                                workCounters={})
            self._encounter, self._started = enc, started
            self._wave_completed = 0
            self._wave_total = spec.get('total')
            if self._wave_total is None:
                self._wave_total = spec.get('budget')
            self._wave_process_cpu_start = None
            self._last_wave_cpu_seconds_per_run = None
        try:
            active = [r for r in queue.load()['requests'] if r['status'] == 'active']
            owner = os.environ.get('KA_NATIVE_COORDINATOR_REQUEST_ID')
            if any(r['request_id'] != owner for r in active):
                raise RuntimeError('Another serialized native check is active; search will not overlap it.')
            if digest(spec['executablePath']) != spec['executableSha256']:
                raise RuntimeError('Frozen native executable hash changed before launch.')
            config = load(folder / 'config.json')
            kernel_path = config.get('kernel', config.get('kernelPath'))
            if not kernel_path or digest(kernel_path) != spec['kernelSha256']:
                raise RuntimeError('Native battle kernel hash changed before launch.')
            self._libcall('_set_run_provenance', {'runSpecSha256': spec_digest,
                         'configSha256': spec['configSha256'], 'inputSetSha256': spec['inputSetSha256'],
                         'candidateParents': spec.get('candidateParents', []),
                         'seedStart': spec.get('seedStart'), 'seedPairs': spec.get('seedPairs'),
                         'admittedSeedPairsByEncounter': spec.get('admittedSeedPairsByEncounter'),
                         'encounterId': enc, 'encounterIds': spec.get('encounterIds'),
                         'runDirectory': str(folder),
                         'executionMode': spec['executionMode'], 'purpose': spec['purpose'],
                         'policySha256': spec['policySha256'], 'runSpec': spec,
                         'actualExecutableSha256': spec['executableSha256'],
                         'currentKernelSha256': spec['kernelSha256']})
            with (folder / 'engine.stdout.log').open('wb') as out, (folder / 'engine.stderr.log').open('wb') as err:
                child = subprocess.Popen(spec['command'], cwd=app.ROOT, stdout=out, stderr=err,
                                         creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                with self._lock:
                    self._active['pid'] = child.pid
                    self._active['stage'] = 'executing'
                    self._child = child
                self._record_native_process_cpu(child)
                with self._lock:
                    if self._process_cpu_samples:
                        self._wave_process_cpu_start = self._process_cpu_samples[-1][1]
                if os.name == 'nt':
                    import ctypes
                    ctypes.windll.kernel32.SetPriorityClass(int(child._handle), 0x4000)
                save(folder / 'runner-status.json', {'state': 'running', 'language': self.language,
                     'encounterId': enc, 'workers': spec['workers'], 'completed': 0, 'total': None,
                     'startedUtc': datetime.now(timezone.utc).isoformat(), 'output': str(output),
                     'pid': child.pid})
                last_status_write = started
                while child.poll() is None:
                    self._record_native_process_cpu(child)
                    native_status = _native_work_status(output)
                    durable = _durable_from_status(native_status, self.language)
                    native_counters = _native_work_counters(self.language, native_status)
                    if journal.exists():
                        offset, chunk = _journal_chunk(journal, offset)
                        rows = (pending + chunk).split(b'\n')
                        pending = rows.pop()
                        ready_rows.extend(line for line in rows if line.strip())
                        imported += self._import_native_lines(
                            ready_rows[:max(0, durable - observed)], raw_records,
                            spec.get('mode') != 'search')
                        take = min(len(ready_rows), max(0, durable - observed))
                        del ready_rows[:take]
                        observed += take
                    with self._lock:
                        self._completed += max(0, durable - self._wave_completed)
                        self._wave_completed = max(self._wave_completed, durable)
                        if self._active is not None:
                            self._active['durable'] = self._wave_completed
                            self._active['imported'] = imported
                            self._active['workCounters'] = native_counters
                            if native_counters:
                                self._last_native_work_counters = native_counters
                    if time.monotonic() - last_status_write >= 1.0:
                        elapsed = max(.001, time.monotonic() - started)
                        save(folder / 'runner-status.json', {'state': 'running', 'language': self.language,
                             'encounterId': enc, 'workers': spec['workers'], 'completed': durable,
                             'total': None, 'elapsedSeconds': elapsed, 'runsPerSecond': durable/elapsed,
                             'output': str(output), 'pid': child.pid})
                        save(folder / 'checkpoint.json', {'state': 'running', 'durable': durable,
                             'imported': imported, 'encounterId': enc, 'seedStart': spec.get('seedStart'),
                             'seedCount': len(spec.get('seedPairs', [])),
                             'updatedUtc': datetime.now(timezone.utc).isoformat(),
                             'specSha256': spec_digest})
                        last_status_write = time.monotonic()
                    time.sleep(.2)
                rc = child.wait()
                self._record_native_process_cpu(child)
                with self._lock:
                    if self._active is not None:
                        self._active['stage'] = 'saving'
                if rc:
                    tail = (folder / 'engine.stderr.log').read_text(encoding='utf-8', errors='replace')[-2500:]
                    raise RuntimeError(f'{self.language} native wave exited {rc}: {tail}')
                # Successful exit implies native journal synchronization. Keep final
                # ingestion bounded even when a native writer outruns SQLite imports.
                final_durable = _durable(output, self.language)
                with journal.open('rb') as stream:
                    stream.seek(offset)
                    while True:
                        take = min(len(ready_rows), max(0, final_durable - observed))
                        if take:
                            imported += self._import_native_lines(ready_rows[:take], raw_records,
                                                                  spec.get('mode') != 'search')
                            del ready_rows[:take]
                            observed += take
                        if ready_rows:
                            raise RuntimeError('Native journal has more rows than its final durable counter')
                        chunk = stream.read(8 * 1024 * 1024)
                        if not chunk:
                            if pending.strip():
                                ready_rows = [pending]
                                pending = b''
                                continue
                            break
                        rows = (pending + chunk).split(b'\n')
                        pending = rows.pop()
                        ready_rows.extend(line for line in rows if line.strip())
                if observed != final_durable:
                    raise RuntimeError(f'Native journal has{observed} complete rows against durable{final_durable}')
                if imported != final_durable:
                    raise RuntimeError(f'Imported {imported} rows against native durable counter {final_durable}.')
                final_native_counters = _native_work_counters(
                    self.language, _native_work_status(output))
                status = {'state': 'complete', 'durable': _durable(output, self.language),
                          'imported': imported, 'returnCode': rc, 'elapsedSeconds': time.monotonic()-started}
                with self._lock:
                    self._completed += max(0, status['durable'] - self._wave_completed)
                    self._wave_completed = status['durable']
                    if self._active is not None:
                        self._active['durable'] = self._wave_completed
                        self._active['imported'] = imported
                        self._active['workCounters'] = final_native_counters
                    if final_native_counters:
                        self._last_native_work_counters = final_native_counters
                    if (self._wave_process_cpu_start is not None and self._process_cpu_samples
                            and self._wave_completed > 0):
                        cpu_work = max(0.0, self._process_cpu_samples[-1][1] -
                                       self._wave_process_cpu_start)
                        self._last_wave_cpu_seconds_per_run = cpu_work / self._wave_completed
                save(folder / 'runner-status.json', status)
                with self._lock:
                    self._history.append(dict(encounterId=enc, completed=status['durable'],
                                              imported=imported, output=str(folder)))
                    self._fresh_rate = status['durable'] / max(.001, status['elapsedSeconds'])
                    if self._wave_process_cpu_start is not None and self._process_cpu_samples:
                        cpu_work = max(0.0, self._process_cpu_samples[-1][1] -
                                       self._wave_process_cpu_start)
                        self._session_cpu_seconds += cpu_work
                        if status['durable'] > 0:
                            self._last_wave_cpu_seconds_per_run = cpu_work / status['durable']
                    self._wave_process_cpu_start = None
        except Exception as error:
            if child is not None and child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
            save(folder / 'runner-status.json', {'state': 'failed', 'encounterId': enc,
                 'durable': _durable(output, self.language), 'error': str(error), 'output': str(output),
                 'endedUtc': datetime.now(timezone.utc).isoformat()})
            save(folder / 'checkpoint.json', {'state': 'failed-preserved',
                 'durable': _durable(output, self.language), 'imported': imported,
                 'error': str(error), 'updatedUtc': datetime.now(timezone.utc).isoformat()})
            raise
        finally:
            with self._lock:
                self._started = None
                self._child = None
            Path(lock_path).unlink(missing_ok=True)
        return raw_records

    def _record_native_process_cpu(self, child):
        cpu_seconds = _process_cpu_seconds(child)
        if cpu_seconds is None:
            return
        sampled_at = time.monotonic()
        with self._lock:
            if self._process_cpu_pid != child.pid:
                self._process_cpu_pid = child.pid
                self._process_cpu_samples.clear()
            self._process_cpu_samples.append((sampled_at, cpu_seconds))

    def _native_process_telemetry_locked(self, now):
        child = self._child
        process_active = bool(child is not None and child.poll() is None)
        samples = list(self._process_cpu_samples)
        recent = [sample for sample in samples if sample[0] >= now - 10.0]
        cpu_utilization = None
        cpu_window = None
        if not process_active:
            cpu_utilization = 0.0
        elif len(recent) >= 2:
            cpu_window = recent[-1][0] - recent[0][0]
            if cpu_window >= 0.5:
                cpu_work = max(0.0, recent[-1][1] - recent[0][1])
                cpu_utilization = cpu_work / cpu_window / max(1, os.cpu_count() or 1)

        session_cpu = self._session_cpu_seconds
        if process_active and self._wave_process_cpu_start is not None and samples:
            session_cpu += max(0.0, samples[-1][1] - self._wave_process_cpu_start)
        session_native_runs = (max(0, self._completed - self._session_completed_base)
                               if self._session_completed_base is not None else 0)
        cpu_seconds_per_run = (session_cpu / session_native_runs
                               if session_native_runs > 0 else self._last_wave_cpu_seconds_per_run)
        return process_active, cpu_utilization, cpu_window, cpu_seconds_per_run

    def status(self):
        workflow = self._workflow_status()
        base = self._status_library_snapshot()
        with self._lock:
            now = time.monotonic()
            state = self._state
            elapsed = now-self._started if self._started else None
            session = self._session_elapsed_locked(now)
            runs = max(int(base.get('totalRuns', 0)), int(self._library_total_runs))
            all_focus = self._focus == list(range(20))
            focus_view = [] if all_focus else list(self._focus)
            session_runs = (self._session_committed if self._session_started is not None else None)
            rate = (session_runs / session if session is not None and session > 0
                    and session_runs is not None else None)
            process_active, cpu_utilization, cpu_window, cpu_seconds_per_run = \
                self._native_process_telemetry_locked(now)
            active = dict(self._active) if self._active else None
            active_elapsed = (max(0.0, now-self._active_started)
                              if self._active_started is not None else None)
            pending_import = (max(0, int((active or {}).get('durable', 0)) -
                                  int((active or {}).get('imported', 0))) if active else 0)
            native_process = {
                'active': bool(active),
                'processRunning': process_active,
                'stage': (active or {}).get('stage', 'idle'),
                'pid': (active or {}).get('pid'),
                'acceptedCommands': 1 if active and active.get('pid') is not None else 0,
                'durableJournalResults': int((active or {}).get('durable', 0)),
                'importedJournalResults': int((active or {}).get('imported', 0)),
                'pendingImportResults': pending_import,
                'configuredExecutors': self._workers,
                'executorActivity': 'see measured child work counters',
                'workCounters': dict(active.get('workCounters') or {}) if active is not None
                                else dict(self._last_native_work_counters),
            }
            throughput = dict(workers=self._workers, duty=self._duty,
                requestedWorkers=self._workers, effectiveWorkers=self._workers,
                effectiveBattleWorkers=self._workers, capacityWorkers=self._workers,
                plannerWorkers=0,
                requestedBattleWorkers=self._workers, requestedPlannerWorkers=0,
                pendingResize=None, ceiling=self._worker_ceiling, accelerators=[],
                runsPerSecond=rate, battlesPerSecond=rate,
                battlesPerHour=(rate*3600 if rate is not None else None),
                windowSeconds=session, rateBasis='newly committed library rows / active Start-session time (Running and Saving)',
                secondsPerRun=cpu_seconds_per_run,
                secondsPerRunBasis=('native-process CPU seconds / durable journal result'
                                    if cpu_seconds_per_run is not None else 'not measured'),
                secondsPerRunWindowSeconds=cpu_window,
                cpuUtilization=cpu_utilization,
                cpuUtilizationBasis=('native child CPU / host logical CPU capacity, rolling OS sample'
                                     if cpu_utilization is not None else 'not measured'),
                capacityBattlesPerSecond=None, achievedFraction=None)
            return {'schema': 'strategy-optimizer-status-v1', 'state': state, 'error': self._error,
                    'errorTraceback': self._error_traceback,
                    'candidates': base.get('candidates', []), 'archive': [],
                    'focusedExperiment': workflow.get('focusedExperiment'),
                    'compatible': True, 'totalRuns': runs, 'legacyTotalRuns': None,
                    'encounterRuns': runs, 'encounterSessionRuns': self._session_committed,
                    'encounterSessionReserved': 0, 'sessionRuns': (None if self._session_completed_base is None
                        else self._session_committed),
                    'sessionElapsedSeconds': session,
                    'currentRunElapsedSeconds': active_elapsed,
                    'sessionRunsBasis': 'native-session', 'proposals': base.get('proposals', 0),
                    'improvements': 0, 'lastImprovementRun': None, 'provenance': self._provenance(),
                    'acceleration': None, 'encounters': base.get('encounters', {}),
                    'campaigns': base.get('campaigns', []),
                    'encounterStats': base.get('encounterStats', {}), 'encounterLedger': None,
                    'encounterCoverage': None, 'encounterAverageLeaders': base.get('encounterAverageLeaders'),
                    'objectiveVersion': None, 'searchSpaceVersion': None, 'objectiveMigration': None,
                    'eliteLanes': [], 'laneEvidence': {'population': len(base.get('candidates', [])),
                        'withProgress': 0, 'members': 0},
                    'libraryEncounters': base.get('libraryEncounters', []),
                    'focusEncounter': self._focus[0] if len(self._focus) == 1 else None,
                    'focusEncounters': focus_view,
                    'campaign': {'enabled': state == 'Running', 'status': state.lower(),
                                 'focusEncounters': focus_view, 'remaining': None},
                    'focusPortfolio': None, 'idleReason': self._error, 'encounterLifetime': None,
                    'recordHolders': base.get('recordHolders', {}), 'horizonTicks': None,
                    'probes': workflow.get('probes', []), 'lastProbe': workflow.get('lastProbe'),
                    'fineTune': workflow.get('fineTune', []),
                    'lastFineTune': workflow.get('lastFineTune'),
                    'autoTune': None, 'autoTuneReason': None, 'throughput': throughput,
                    'learner': None, 'scheduler': {'nativeController': True,
                        'mode': self._scheduler_mode,
                        'activeJob': active,
                        'nativeProcess': native_process,
                        'history': list(self._history[-20:])},
                    'averageSimulationSeconds': None, 'timedRuns': self._session_committed, 'diskBytes': None,
                    'native': {'engine': self.language, 'workers': self._workers, 'duty': self._duty,
                        'completed': self._session_committed,
                        'journalCompleted': self._completed,
                        'total': None, 'waveCompleted': self._wave_completed,
                        'waveTotal': self._wave_total, 'runsPerSecond': rate,
                        'currentRunElapsedSeconds': active_elapsed,
                         'sessionElapsedSeconds': session,
                         'verificationOnly': self._execution_mode != 'production'}}

    def _status_library_snapshot(self):
        """Read the expensive library aggregate without holding the controller state lock."""
        now = time.monotonic()
        with self._lock:
            if self._status_library_cache is not None and now-self._status_cache_at < 1.0:
                return self._status_library_cache
        try:
            with self._status_snapshot_lock:
                now = time.monotonic()
                with self._lock:
                    if self._status_library_cache is not None and now-self._status_cache_at < 1.0:
                        return self._status_library_cache
                snapshot = self._libcall('_native_status_snapshot')
                with self._lock:
                    self._status_library_cache = snapshot
                    self._status_cache_at = time.monotonic()
                    return self._status_library_cache
        except Exception as exc:
            with self._lock:
                if not self._error:
                    self._error = f'Library snapshot unavailable: {exc}'
            return {}

    def _provenance(self):
        return {'engine': self.language, 'revision': self._engine_assets['revision'],
                'executableSha256': self._engine_assets['executable']['sha256'],
                'kernelSha256': self._engine_assets['kernel']['sha256'],
                'executionMode': self._execution_mode,
                'verificationOnly': self._execution_mode != 'production'}

    @staticmethod
    def _native_candidate_view(row):
        return project_candidate_view(row)

    def prepare_status_transport(self):
        self._refresh_status_transport()

    def _refresh_status_transport(self):
        """Refresh one status snapshot in the background; HTTP polls never build it inline.

        The browser aborts status requests after five seconds. Building workflow projections in
        each request let the abandoned server handler keep working while the next poll started.
        A single publisher avoids that backlog and serves the last complete snapshot meanwhile.
        """
        now = time.monotonic()
        with self._status_transport_lock:
            if self._status_transport_refreshing:
                return
            if (self._status_transport_json is not None and
                    now - self._status_transport_updated_at < _STATUS_TRANSPORT_REFRESH_SECONDS):
                return
            if (self._status_transport_error is not None and
                    now - self._status_transport_started_at < 2.0):
                return
            self._status_transport_refreshing = True
            self._status_transport_started_at = now

        def publish():
            try:
                payload = json.dumps(self.status(), separators=(',', ':'), allow_nan=False)
                with self._status_transport_lock:
                    self._status_transport_json = payload
                    self._status_transport_updated_at = time.monotonic()
                    self._status_transport_error = None
            except Exception as exc:
                with self._status_transport_lock:
                    self._status_transport_error = str(exc)
            finally:
                with self._status_transport_lock:
                    self._status_transport_refreshing = False

        threading.Thread(target=publish, daemon=True,
                         name=f'native-status-{self.language}').start()

    def status_transport_data(self):
        self._refresh_status_transport()
        with self._status_transport_lock:
            payload = self._status_transport_json
            updated = self._status_transport_updated_at
            refreshing = self._status_transport_refreshing
            error = self._status_transport_error
        if payload is None:
            if error:
                raise RuntimeError(f'Native status snapshot is not ready: {error}')
            raise RuntimeError('Native status snapshot is being prepared.')
        age = max(0.0, time.monotonic() - updated)
        # Small live fields keep Run/Pause and durable counters current even while the full
        # workflow projection is refreshing. These values come from controller memory only.
        with self._lock:
            now = time.monotonic()
            live_state = self._state
            live_error = self._error
            session = self._session_elapsed_locked(now)
            session_runs = self._session_committed if session is not None else None
            rate = (session_runs / session if session_runs is not None and session and session > 0
                    else (0.0 if session is not None and session > 0 else None))
            process_active, cpu_utilization, cpu_window, cpu_seconds_per_run = \
                self._native_process_telemetry_locked(now)
            active = self._active
            active_elapsed = (max(0.0, now-self._active_started)
                              if self._active_started is not None else None)
            native_process = {
                'active': bool(active), 'processRunning': process_active,
                'stage': active.get('stage', 'unknown') if active else 'idle',
                'pid': active.get('pid') if active else None,
                'acceptedCommands': 1 if active and active.get('pid') is not None else 0,
                'durableJournalResults': int((active or {}).get('durable', 0)),
                'importedJournalResults': int((active or {}).get('imported', 0)),
                'pendingImportResults': max(0, int((active or {}).get('durable', 0)) -
                                            int((active or {}).get('imported', 0))) if active else 0,
                'configuredExecutors': self._workers,
                'executorActivity': 'see measured child work counters',
                'workCounters': dict(active.get('workCounters') or {}) if active is not None
                                else dict(self._last_native_work_counters),
            }
            live_progress = {
                'completed': self._session_committed,
                'journalCompleted': self._completed,
                'waveCompleted': self._wave_completed,
                'waveTotal': self._wave_total,
            }
            live_throughput = {
                'workers': self._workers,
                'effectiveBattleWorkers': self._workers,
                'capacityWorkers': self._workers,
                'duty': self._duty,
                'battlesPerSecond': rate,
                'battlesPerHour': rate*3600 if rate is not None else None,
                'runsPerSecond': rate,
                'windowSeconds': session,
                'rateBasis': 'newly committed library rows / active Start-session time (Running and Saving)',
                'secondsPerRun': cpu_seconds_per_run,
                'secondsPerRunBasis': ('native-process CPU seconds / durable journal result'
                    if cpu_seconds_per_run is not None else 'not measured'),
                'secondsPerRunWindowSeconds': cpu_window,
                'cpuUtilization': cpu_utilization,
                'cpuUtilizationBasis': ('native child CPU / host logical CPU capacity, rolling OS sample'
                    if cpu_utilization is not None else 'not measured'),
                'capacityBattlesPerSecond': None,
                'achievedFraction': None,
            }
            live_scheduler = {'nativeProcess': native_process, 'mode': self._scheduler_mode}
            live = {'state': live_state, 'error': live_error,
                'totalRuns': max(0, self._library_total_runs),
                'sessionRuns': session_runs, 'sessionElapsedSeconds': session,
                'currentRunElapsedSeconds': active_elapsed,
                'encounterSessionRuns': self._session_committed,
                'timedRuns': self._session_committed,
                'throughput': live_throughput, 'scheduler': live_scheduler,
                'nativeProgress': live_progress}
        live['statusTransport'] = {
            'generatedAtUnix': time.time() - age,
            'ageSeconds': round(age, 3),
            'refreshing': refreshing,
            'lastError': error,
        }
        return payload, live

    def strategies(self, encounter_id, defeat_count=0):
        value = self._libcall('_native_strategy_list', encounter_id, defeat_count)
        seen = {row.get('candidateId') for row in value.get('strategies', [])}
        extra = [row for row in self._workflow_candidate_rows(encounter_id, defeat_count)
                 if row.get('candidateId') not in seen]
        return self._workflow_project_strategy_result(
            dict(value, strategies=list(value.get('strategies', [])) + extra))

    def detail(self, candidate_id):
        return self._libcall('_native_detail', candidate_id)

    def leaders(self):
        return self._libcall('leaders')

    def read(self, method, *args, **kwargs):
        if method in ('status', 'snapshot'):
            return self.status()
        if method in ('encounter_strategies', 'strategies'):
            return self.strategies(*args, **kwargs)
        if method in ('overview_average_leaders', 'leaders'):
            return self.leaders()
        if method in ('strategy_detail', 'detail'):
            candidate = kwargs.get('candidate_id', args[0] if args else None)
            holder = kwargs.get('holder_key', args[1] if len(args) > 1 else None)
            return self._workflow_detail(candidate, holder)
        if method == 'encounter_stats':
            return self._libcall('encounter_stats')
        if method == 'fine_tune_programs':
            candidate = args[0] if args else kwargs.get('candidate_id')
            return [job for job in self._workflow_status().get('fineTune', [])
                    if candidate is None or (job.get('parent') or {}).get('candidateId') == candidate]
        if method == 'experiment_report':
            candidate = args[0] if args else kwargs.get('candidate_id')
            return self._workflow_experiment_report(candidate)
        if method == 'encounter_player_regions':
            return {'ok': False, 'error': 'Player-region analysis is not implemented in the native controller.'}
        if method == 'export_diagnostics_status':
            with self._diagnostic_export_lock:
                current = self._diagnostic_export
                if current is None:
                    return {'ok': False, 'error': 'there is no diagnostic export running'}
                requested_id = args[0] if args else kwargs.get('job_id')
                if requested_id is not None and requested_id != current['jobId']:
                    return {'ok': False, 'error': 'that diagnostic export job is no longer current'}
                return dict(current, ok=True)
        raise ValueError('Unsupported native read: ' + str(method))

    def request(self, action, value=None):
        value = value or {}
        if action in ('start', 'community_start', 'pause', 'stop', 'keep', 'all_encounters',
                      'focus_encounter', 'workers', 'duty'):
            return self.command(action, value, wait=True)
        if action == 'export_diagnostics':
            path = value.get('path')
            if not path:
                return {'ok': False, 'error': 'Export path is required.'}
            try:
                from strategy_diagnostic_export import normalize_count
                requested = normalize_count(value.get('count'))
                import uuid
                with self._diagnostic_export_lock:
                    if self._diagnostic_export and self._diagnostic_export['state'] == 'running':
                        return {'ok': False, 'error': 'A diagnostic export is already running.'}
                    job_id = uuid.uuid4().hex
                    self._diagnostic_export = dict(jobId=job_id, state='running', stage='prepare',
                        stageLabel='Counting selected rows', requestedCount=requested, included=0,
                        path=str(path), bytesWritten=0,
                        capBytes=500_000_000, startedAt=time.time())
                def report_progress(update):
                    with self._diagnostic_export_lock:
                        if self._diagnostic_export and self._diagnostic_export['jobId'] == job_id:
                            self._diagnostic_export.update(update)
                done, result_box = threading.Event(), {}
                # Enqueue before returning to the UI: close() will then drain this accepted job.
                self._library_calls.put(('export_diagnostics',
                    (path, requested, report_progress), done, result_box))
                def export_worker():
                    try:
                        done.wait()
                        if 'error' in result_box:
                            raise result_box['error']
                        result = result_box.get('value')
                        with self._diagnostic_export_lock:
                            if self._diagnostic_export and self._diagnostic_export['jobId'] == job_id:
                                self._diagnostic_export.update(state='done', stage='complete',
                                    requested=result.get('observations', 0),
                                    included=result.get('observations', 0), bytesWritten=result.get('bytes', 0),
                                    result=result, elapsedSeconds=max(0.0, time.time()-self._diagnostic_export['startedAt']))
                    except Exception as exc:
                        with self._diagnostic_export_lock:
                            if self._diagnostic_export and self._diagnostic_export['jobId'] == job_id:
                                self._diagnostic_export.update(state='error', stage='failed', error=str(exc),
                                    elapsedSeconds=max(0.0, time.time()-self._diagnostic_export['startedAt']))
                # Keep the process alive through an accepted export if the UI closes. The queued
                # library call drains before its writer closes, so the atomic destination is kept.
                threading.Thread(target=export_worker, daemon=False,
                                 name='native-diagnostic-export').start()
                return dict(ok=True, jobId=job_id, path=str(path), count=requested)
            except Exception as exc:
                return {'ok': False, 'error': str(exc)}
        if action == 'start_strategy_experiment':
            if value.get('mode') in ('skills', 'remove-skills'):
                return self._workflow_start_native_skills(value.get('candidateId'),
                    unit=value.get('unit'), count=value.get('count', 128))
            return self._workflow_start_strategy_experiment(value.get('candidateId'), value.get('holderKey'),
                                                           value.get('count', 8), value.get('mode', 'evaluate'))
        if action == 'restore_strategy':
            return self._workflow_restore_strategy(value.get('holderKey'))
        if action == 'run_strategy':
            return self._workflow_run_strategy(value.get('candidateId'), value.get('holderKey'), value.get('count', 8))
        if action == 'simulate_strategy_visual':
            return self._workflow_simulate_strategy_visual(candidate_id=value.get('candidateId'),
                holder_key=value.get('holderKey'))
        if action in ('replay', 'replay_strategy_run', 'replay_holder'):
            return self._workflow_replay(candidate_id=value.get('candidateId'),
                holder_key=value.get('holderKey'), seeds=value.get('seeds'))
        if action == 'legacy_evaluate_replay':
            candidate = value.get('candidateId')
            if not candidate:
                return {'ok': False, 'error': 'Native replay requires a resident native candidate ID.'}
            detail = self.detail(candidate)
            if not detail.get('ok'):
                return detail
            scenario = detail.get('scenario') or detail.get('intent')
            seeds = value.get('seeds')
            if seeds is None:
                count = value.get('count', 8)
                if isinstance(count, bool) or not isinstance(count, int) or count < 1:
                    return {'ok': False, 'error': 'count must be a positive integer.'}
                start = self._allocate_seed(scenario['encounterId'], count)
                if self.language == 'cpp':
                    seeds = [[start+i, start+i] for i in range(count)]
                else:
                    seeds = [[start+i, start+1000003+i] for i in range(count)]
            try:
                records = self.evaluate(scenario, seeds, len(seeds))
                return {'ok': True, 'candidateId': candidate, 'runs': records,
                        'count': len(records), 'verificationOnly': True}
            except Exception as exc:
                return {'ok': False, 'error': str(exc)}
        if action == 'simulate_strategy_visual':
            return {'ok': False, 'error': 'Native visual replay is not implemented by this controller.'}
        raise ValueError('Unsupported native controller request: ' + str(action))

    def evaluate(self, scenario, seeds, count=None):
        """Run exact caller supplied intents/seeds through native preparation and return raw rows.

        This API deliberately runs a finite requested batch; it does not alter continuous-wave
        scheduling or impose a hidden trial cap.
        """
        if not isinstance(scenario, dict) or not isinstance(scenario.get('encounterId'), int):
            raise ValueError('scenario must be an exact native intent with encounterId')
        if not isinstance(seeds, list) or not seeds or any(not isinstance(pair, list) or len(pair) != 2
                or any(isinstance(x, bool) or not isinstance(x, int) or x < 0 or x >= 2**31 for x in pair)
                for pair in seeds):
            raise ValueError('seeds must be a nonempty list of nonnegative 31-bit seed pairs')
        if count is not None and count != len(seeds):
            raise ValueError('count must match the explicit seed list; no silent truncation is allowed')
        return self._run_wave(scenario['encounterId'], self._workers, parents=[scenario],
                              seeds=seeds, search=False)

    def close(self):
        self.command('stop', wait=True)
        thread = self._wave_thread
        if thread and thread.is_alive():
            thread.join(timeout=120)
            if thread.is_alive():
                child = getattr(self, '_child', None)
                if child is not None and child.poll() is None:
                    child.terminate()
                    try:
                        child.wait(timeout=10)
                    except Exception:
                        child.kill()
                thread.join(timeout=15)
        self.shutdown_event.set()
        self._lib_thread.join(timeout=30)
