"""Detached, bounded native runs and a compact read model for the desktop UI.

No simulation or preparation occurs in Python. The three frozen executables own
that work. This host generates explicit configs and reads their durable journals.
"""
from __future__ import annotations
import argparse
import ctypes
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

SHARED = Path(__file__).resolve().parent
ROOT = SHARED.parents[2]
from optimizer_storage import configure_optimizer_storage
STORAGE = configure_optimizer_storage(ROOT)
DATA = Path(os.environ['LOCALAPPDATA']) / 'KingdomAdventurersOptimizer/native-runs'
MANIFEST = SHARED / 'comparison-inputs/bank2-v3/input-set-manifest.json'
ENGINES = {
    'rust': ('Rust', 'R8', 'rust/standalone-optimizer/builds/r8/ka-rust-standalone-optimizer.exe', '99133754795fd345ae5616099b47244d933f1ec79105bbd8f7adb4e4bcbaa718'),
    'go': ('Go', 'R11', 'go/standalone-optimizer/revisions/r11-comparison/go-optimizer.exe', '574861660dd39e0cfb25f22d8843ff31ea0a43608aa088ee9992d303b447d790'),
    'cpp': ('C++', 'R9', 'cpp/standalone-optimizer/revisions/r9/standalone-optimizer/optimizer.exe', 'ebcc51f1d29ece59462660a5253fd7e2d67875021c0f6689c4a3b96f2377c239'),
}
KERNEL_SHA = '07ee7d36e3bd1acc9b431095d241eb6cab04a77ffedd0e011074a77fa1848e19'
MAX_SECONDS = 1800


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f'.{os.getpid()}.{threading.get_ident()}.tmp')
    with tmp.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def object_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def engine_path(language):
    return SHARED.parent / ENGINES[language][2]


def resource_check():
    if shutil.disk_usage(DATA if DATA.exists() else ROOT).free < 1024**3:
        raise RuntimeError('Less than 1 GiB free storage. Free space before running.')
    if os.name == 'nt':
        class Memory(ctypes.Structure):
            _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong)] + [(x, ctypes.c_ulonglong) for x in ('totalPhysical', 'availablePhysical', 'totalPageFile', 'availablePageFile', 'totalVirtual', 'availableVirtual', 'availableExtendedVirtual')]
        memory = Memory()
        memory.length = ctypes.sizeof(memory)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
            raise RuntimeError('Could not read free memory.')
        if memory.availablePhysical < 6 * 1024**3:
            raise RuntimeError('Less than 6 GiB RAM available. Close other heavy jobs before running.')


def check_assets():
    manifest = load(MANIFEST)
    if manifest.get('complete') is not True:
        raise RuntimeError('The source input set is incomplete.')
    for lang, (_, _, _, sha) in ENGINES.items():
        if digest(engine_path(lang)) != sha:
            raise RuntimeError(f'{lang}: frozen executable hash mismatch')
    for name in ('facts', 'catalog', 'kernel'):
        expected = manifest['sourceHashes'][{'facts': 'factsSha256', 'catalog': 'catalogSha256', 'kernel': 'kernelSha256'}[name]]
        if digest(manifest['staticInputs'][name]) != expected:
            raise RuntimeError(f'{name}: input hash mismatch')
    if manifest['sourceHashes']['kernelSha256'] != KERNEL_SHA:
        raise RuntimeError('Unexpected battle engine identity')
    return manifest


def prepare_run(options, directory):
    language = options['language']
    if language not in ENGINES:
        raise ValueError('Choose Rust, Go or C++.')
    limits = {'encounterId': (0, 19), 'workers': (1, max(1, int((os.cpu_count() or 1) * .8))), 'trials': (1, 4096), 'generations': (1, 100)}
    for key, (low, high) in limits.items():
        value = options.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise ValueError(f'{key} must be a whole number in {low}..{high}')
    mode = options.get('mode')
    if mode not in ('baseline', 'search'):
        raise ValueError('Choose baseline or search.')
    # C++ may expand a mutation population each generation. Keep GUI runs bounded
    # until the coordinator establishes its measured growth/resource envelope.
    if mode == 'search' and language == 'cpp' and options['generations'] > 4:
        raise ValueError('C++ GUI search currently supports at most 4 generations. Baseline supports the full trial limit.')
    estimated_records = options['trials'] * (1 if mode == 'baseline' else (4 * options['generations'] if language == 'rust' else (2 ** (options['generations'] + 1) if language == 'cpp' else 1)))
    if estimated_records > 50000:
        raise ValueError('This run could exceed 50,000 records. Reduce trials or generations.')
    resource_check()
    manifest = check_assets()
    required_storage = estimated_records * 100000 + 1024**3
    if shutil.disk_usage(DATA if DATA.exists() else ROOT).free < required_storage:
        raise RuntimeError('Insufficient free storage for the requested run and 1 GiB headroom.')
    enc = str(options['encounterId'])
    raw_info = manifest['outputs']['perEncounter'][enc]
    if digest(raw_info['path']) != raw_info['sha256']:
        raise RuntimeError('Selected source candidate hash mismatch')
    raw = load(raw_info['path'])
    if len(raw) != 1 or raw[0]['encounterId'] != options['encounterId']:
        raise RuntimeError('Expected one source strategy for the selected encounter')
    directory.mkdir(parents=True, exist_ok=False)
    output = directory / 'optimizer-output'
    output.mkdir()
    candidate_file = directory / 'candidates.json'
    save(candidate_file, raw)
    pairs = [[100001 + i, 200003] for i in range(options['trials'])]
    seed_file = directory / 'seed-bank.json'
    save(seed_file, pairs)
    static = manifest['staticInputs']
    policy = {'options': options, 'seedBank': pairs, 'purpose': 'user-native-desktop-diagnostic', 'finishPolicy': raw[0].get('finishPolicy'), 'importDisabled': True}
    policy_hash = object_digest(policy)
    config_path = directory / 'config.json'
    if language == 'rust':
        config = {'schema': 'ka-rust-optimizer-config-v1', 'kernel': static['kernel'], 'catalog': static['catalog'], 'candidates': str(candidate_file), 'output': str(output), 'executors': options['workers'], 'generations': 1 if mode == 'baseline' else options['generations'], 'candidatesPerGeneration': 1 if mode == 'baseline' else 4, 'candidateLimit': 1, 'encounterFilter': options['encounterId'], 'searchSeed': 20261004, 'seedPairs': pairs, 'mechanicsProvenance': {'engineSha256': KERNEL_SHA, 'sourceInputSetSha256': manifest['inputSetSha256']}, 'policyProvenance': policy}
        command = [str(engine_path(language)), str(config_path)]
    elif language == 'go':
        workload_info = manifest['outputs']['goPerEncounter'][enc]
        if digest(workload_info['path']) != workload_info['sha256']:
            raise RuntimeError('Go workload hash mismatch')
        config = load(workload_info['path'])
        config['candidates'] = raw
        command = [str(engine_path(language)), '--mode', 'baseline-bank' if mode == 'baseline' else 'search', '--input', str(config_path), '--output', str(output), '--executors', str(options['workers']), '--budget', str(options['trials']), '--batch', str(min(32, options['trials'])), '--seed', '20261004']
        if mode == 'baseline':
            command += ['--seed-bank', str(seed_file), '--sync-batch', '1']
    else:
        provenance = dict(manifest['cppProvenance'][enc])
        provenance['rawCandidatesSha256'] = digest(candidate_file)
        provenance['policySha256'] = policy_hash
        config = {'rawCandidates': str(candidate_file), 'tables': static['facts'], 'kernelPath': static['kernel'], 'outputDir': str(output), 'executors': options['workers'], 'generations': 1 if mode == 'baseline' else options['generations'], 'seedStart': 100001, 'seedCount': options['trials'], 'seedPointers': ['/mathSeed'], 'headless': True, 'mutations': [] if mode == 'baseline' else [{'pointer': '/ownUnits/0/equipment/0/level', 'min': 1, 'max': 99, 'step': 1, 'stride': 1}], 'provenance': provenance}
        command = [str(engine_path(language)), str(config_path)]
    save(config_path, config)
    spec = {'schema': 'native-desktop-run-v1', **options, 'revision': ENGINES[language][1], 'executableSha256': ENGINES[language][3], 'kernelSha256': KERNEL_SHA, 'inputSetSha256': manifest['inputSetSha256'], 'policySha256': policy_hash, 'configSha256': digest(config_path), 'command': command, 'directory': str(directory), 'output': str(output), 'total': options['trials'] if mode == 'baseline' or language == 'go' else None, 'createdUtc': datetime.now(timezone.utc).isoformat(), 'verificationOnly': True, 'importDisabled': True, 'maxSeconds': MAX_SECONDS}
    save(directory / 'run-spec.json', spec)
    return spec


class JournalReader:
    """Incremental reader; complete but not yet synced rows wait for native counters."""
    def __init__(self, path, language):
        self.path, self.language = Path(path), language
        self.offset = 0
        self.pending = b''
        self.queue = []
        self.count = 0
        self.groups = {}

    def poll(self, durable_limit):
        if self.path.exists():
            with self.path.open('rb') as stream:
                stream.seek(self.offset)
                data = stream.read(16 * 1024 * 1024)
                self.offset = stream.tell()
            lines = (self.pending + data).split(b'\n')
            self.pending = lines.pop()
            for line in lines:
                if line.strip():
                    value = json.loads(line)
                    result = value.get('report', {}) if self.language == 'rust' else value.get('result', {})
                    intent = value.get('intent', value.get('rawScenario', {}))
                    encounter = value.get('encounterId', intent.get('encounterId'))
                    earned = result.get('earned', value.get('earned'))
                    known = isinstance(earned, (int, float)) and not isinstance(earned, bool) and math.isfinite(earned)
                    if self.language == 'go':
                        known = known and result.get('earnedValid') is True
                    if self.language == 'rust':
                        known = known and value.get('outcomeClassification') == 'resolved'
                    self.queue.append((encounter, float(earned) if known else None))
        take = min(len(self.queue), max(0, durable_limit - self.count))
        for encounter, earned in self.queue[:take]:
            if not isinstance(encounter, int):
                raise RuntimeError('Saved record has no encounter identity')
            group = self.groups.setdefault(encounter, {'encounterId': encounter, 'samples': 0, 'sum': 0., 'highestEarned': None, 'unknown': 0})
            if earned is None:
                group['unknown'] += 1
            else:
                group['samples'] += 1
                group['sum'] += earned
                group['highestEarned'] = earned if group['highestEarned'] is None else max(group['highestEarned'], earned)
            self.count += 1
        del self.queue[:take]
        if len(self.queue) > 10000 or len(self.pending) > 32 * 1024 * 1024:
            raise RuntimeError('Journal projection exceeded its bounded pending buffer')
        return [dict(encounterId=g['encounterId'], samples=g['samples'], meanEarned=g['sum'] / g['samples'] if g['samples'] else None, highestEarned=g['highestEarned'], unknown=g['unknown']) for g in sorted(self.groups.values(), key=lambda g: g['encounterId'])]


def durable_count(output, language):
    try:
        data = load(Path(output) / 'status.json')
    except (OSError, ValueError):
        return 0
    key = {'rust': 'completedTrials', 'go': 'durablyCompleted', 'cpp': 'savedRecords'}[language]
    return max(0, int(data.get(key, data.get('durable', 0))))


def worker(spec_path):
    spec = load(spec_path)
    directory, output = Path(spec['directory']), Path(spec['output'])
    language = spec['language']
    status = {'state': 'starting', 'language': language, 'mode': spec['mode'], 'revision': spec['revision'], 'encounterId': spec['encounterId'], 'workers': spec['workers'], 'trials': spec['trials'], 'elapsedSeconds': 0., 'completed': 0, 'total': spec['total'], 'runsPerSecond': None, 'output': str(output), 'rows': [], 'pid': os.getpid(), 'history': []}
    save(directory / 'runner-status.json', status)
    child = None
    queue_lock = None
    started = None
    reader = JournalReader(output / ('battles.jsonl' if language == 'rust' else 'results.jsonl'), language)
    try:
        import language_test_queue as queue
        # Same coordinator mutex: it cannot start a new test while a UI run owns it.
        queue_lock = queue.locked()
        active = [r for r in queue.load()['requests'] if r['status'] == 'active']
        if active:
            raise RuntimeError('Chat 1 is running a native check. Wait for it to finish.')
        resource_check()
        if digest(engine_path(language)) != spec['executableSha256'] or digest(directory / 'config.json') != spec['configSha256']:
            raise RuntimeError('Run executable or config changed after launch preparation')
        if digest(load(directory / 'config.json').get('kernel', load(directory / 'config.json').get('kernelPath'))) != KERNEL_SHA:
            raise RuntimeError('Native kernel changed after preparation')
        save(DATA / 'last-run.json', {'directory': str(directory)})
        with (directory / 'engine.stdout.log').open('wb') as stdout, (directory / 'engine.stderr.log').open('wb') as stderr:
            started = time.perf_counter()
            status['startedUtc'] = datetime.now(timezone.utc).isoformat()
            child = subprocess.Popen(spec['command'], cwd=ROOT, stdout=stdout, stderr=stderr, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            exit_times = []
            def observe_exit():
                child.wait()
                exit_times.append(time.perf_counter())
            observer = threading.Thread(target=observe_exit, daemon=True)
            observer.start()
            if os.name == 'nt':
                ctypes.windll.kernel32.SetPriorityClass(int(child._handle), 0x4000)
            status.update(state='running', enginePid=child.pid)
            save(directory / 'runner-status.json', status)
            while child.poll() is None:
                elapsed = time.perf_counter() - started
                if elapsed > spec['maxSeconds']:
                    raise TimeoutError('The 30-minute run limit was reached. Partial results and logs are preserved.')
                if shutil.disk_usage(directory).free < 1024**3:
                    raise RuntimeError('Run stopped because free storage fell below 1 GiB. Partial results are preserved.')
                status['rows'] = reader.poll(durable_count(output, language))
                status.update(elapsedSeconds=elapsed, completed=reader.count, runsPerSecond=reader.count / elapsed if elapsed and reader.count else None)
                save(directory / 'runner-status.json', status)
                time.sleep(.25)
            # External elapsed ends at process exit, before Python journal projection.
            observer.join(timeout=10)
            if not exit_times:
                raise RuntimeError('Could not confirm process exit time')
            elapsed = exit_times[0] - started
            return_code = child.returncode
            # Finish reading buffered rows. On a successful engine exit its journal
            # is synced. On failure only the last confirmed native durable count counts.
            limit = 2**63 - 1 if return_code == 0 else durable_count(output, language)
            for _ in range(10000):
                old_offset = reader.offset
                status['rows'] = reader.poll(limit)
                if reader.offset == old_offset:
                    break
            status.update(elapsedSeconds=elapsed, completed=reader.count, runsPerSecond=reader.count / elapsed if elapsed and reader.count else None, returnCode=return_code)
            if return_code:
                detail = (directory / 'engine.stderr.log').read_text(encoding='utf-8', errors='replace')[-2500:]
                raise RuntimeError(f'{ENGINES[language][0]} exited with code {return_code}. {detail}')
            if spec['total'] is not None and reader.count != spec['total']:
                raise RuntimeError(f'Expected {spec["total"]} durable records, observed {reader.count}. Inspect engine diagnostics.')
            status['state'] = 'complete'
    except Exception as error:
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=10)
        if started is not None:
            status['elapsedSeconds'] = time.perf_counter() - started
        status.update(state='failed', error=str(error))
        (directory / 'runner-error.log').write_text(traceback.format_exc(), encoding='utf-8')
    finally:
        save(directory / 'runner-status.json', status)
        save(directory / 'report.json', {'spec': spec, 'status': status, 'rateBasis': 'fresh durable journal records / external process start-to-exit seconds; native saving batches differ', 'meanBasis': 'all known Earned readings in this run; multiple search candidates may contribute', 'strategyEvidence': False, 'verificationOnly': True})
        if queue_lock is not None:
            with (DATA / 'history.jsonl').open('a', encoding='utf-8') as stream:
                stream.write(json.dumps({k: status.get(k) for k in ('state', 'language', 'mode', 'revision', 'encounterId', 'workers', 'trials', 'completed', 'elapsedSeconds', 'runsPerSecond', 'output')}) + '\n')
                stream.flush()
                os.fsync(stream.fileno())
            queue_lock.unlink(missing_ok=True)


class NativeOptimizerApp:
    def __init__(self, preferred_language=None):
        DATA.mkdir(parents=True, exist_ok=True)
        self.preferred_language = preferred_language
        self.directory = None
        self._start_lock = threading.Lock()
        self.attach_last()

    def info(self):
        cpu = os.cpu_count() or 1
        return {'preferredLanguage': self.preferred_language, 'cpuCount': cpu, 'suggestedWorkers': max(1, int(cpu * .8)), 'engines': [{'id': k, 'name': v[0], 'revision': v[1], 'validation': 'passed supplied canonical/save/resume checks'} for k, v in ENGINES.items()], 'warning': 'All three reuse the canonical Rust battle DLL. Saving batch sizes differ, so these visible run rates are not a controlled language ranking. Results remain diagnostic and separate from your strategy library. Runs are bounded to 30 minutes.'}

    def start(self, options):
        with self._start_lock:
            try:
                self.attach_last()
                if self.status()['state'] in ('running', 'starting'):
                    raise RuntimeError('A native run is already active. Reconnect to it and wait for completion.')
                directory = DATA / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '-' + str(options.get('language', 'unknown')))
                spec = prepare_run(options, directory)
                self.directory = directory
                save(directory / 'runner-status.json', {'state': 'starting', 'language': spec['language'], 'elapsedSeconds': 0, 'completed': 0, 'total': spec['total'], 'runsPerSecond': None, 'output': spec['output'], 'rows': []})
                executable = Path(sys.executable)
                if executable.name.lower() == 'pythonw.exe':
                    executable = executable.with_name('python.exe')
                with (directory / 'worker.log').open('wb') as log:
                    subprocess.Popen([str(executable), str(Path(__file__).resolve()), '--worker', str(directory / 'run-spec.json')], cwd=ROOT, stdout=log, stderr=log, creationflags=(subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS) if os.name == 'nt' else 0, start_new_session=os.name != 'nt')
                return {'ok': True}
            except Exception as error:
                return {'ok': False, 'error': str(error)}

    def attach_last(self):
        try:
            directory = Path(load(DATA / 'last-run.json')['directory']).resolve()
            if not directory.is_relative_to(DATA.resolve()):
                raise ValueError('Saved run path escapes the native run folder')
            if not (directory / 'run-spec.json').is_file():
                raise ValueError('Saved run spec is missing')
            self.directory = directory
            return {'ok': True}
        except FileNotFoundError:
            return {'ok': False, 'error': 'No native run has been started yet.'}
        except Exception as error:
            return {'ok': False, 'error': str(error)}

    def status(self):
        value = {'state': 'idle', 'elapsedSeconds': 0, 'completed': 0, 'total': None, 'runsPerSecond': None, 'rows': []}
        if self.directory is not None:
            try:
                value = load(self.directory / 'runner-status.json')
                if value.get('state') in ('running', 'starting') and value.get('pid') and os.name == 'nt':
                    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
                    kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
                    kernel.OpenProcess.restype = ctypes.c_void_p
                    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
                    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
                    handle = kernel.OpenProcess(0x100000, False, value['pid'])
                    try:
                        if not handle or kernel.WaitForSingleObject(handle, 0) == 0:
                            value.update(state='failed', error='The runner process has stopped. Saved logs and results remain in the output folder; inspect any stale queue lock before another run.')
                    finally:
                        if handle:
                            kernel.CloseHandle(handle)
            except (OSError, ValueError):
                value['error'] = 'Runner status unavailable; inspect the output folder.'
        history = DATA / 'history.jsonl'
        if history.exists():
            with history.open('rb') as stream:
                size = stream.seek(0, 2)
                start = max(0, size - 65536)
                stream.seek(start)
                lines = stream.read().splitlines()
                if start:
                    lines = lines[1:]
            value['history'] = [json.loads(line) for line in lines[-20:] if line.strip()][::-1]
        else:
            value['history'] = []
        return value

    def open_output(self):
        if self.directory is None:
            return {'ok': False, 'error': 'Start a run first.'}
        os.startfile(str(self.directory))
        return {'ok': True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--worker', type=Path)
    parser.add_argument('--check-assets', action='store_true')
    args = parser.parse_args()
    if args.check_assets:
        manifest = check_assets()
        print(json.dumps({'assets': 'verified', 'engines': {k: v[1] for k, v in ENGINES.items()}, 'inputSetSha256': manifest['inputSetSha256'], 'nativeExecuted': False}))
    elif args.worker:
        worker(args.worker)
    else:
        parser.error('Choose --check-assets or --worker RUN_SPEC')


if __name__ == '__main__':
    main()
