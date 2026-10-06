"""Measured native worker scaling for `ka_run_battle` on the representative 7000-tick workload.

Each worker builds the canonical snapshot once (the production "prepare a candidate" cost), then
repeats: fresh native battle -> state import -> `ka_run_battle` (6999 ticks) -> result export. The
three costs are timed separately, and the scaling table reports wall time, battles/sec, efficiency
against one worker, CPU utilisation and per-process working set.

    pypy3.exe check_native_scaling.py [workers...] [--battles N]
"""
import ctypes
import ctypes.wintypes as wintypes
import multiprocessing as mp
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import ka_abi  # noqa: E402
from check_native_real_state import Checkpoints  # noqa: E402
from combat_animation_selection import HUMAN_BASES  # noqa: E402
from combat_sandbox import run_scenario  # noqa: E402
from combat_shared_controllers import ROWS  # noqa: E402
from strategy_optimizer_adapter import default_scenario  # noqa: E402

CASE = dict(encounterId=19, tickLimit=7000, mathSeed=7, libSeed=8)
POLICY = 0
TICKS = 6999


class ProcessMemoryCounters(ctypes.Structure):
    _fields_ = [('cb', ctypes.c_ulong), ('PageFaultCount', ctypes.c_ulong),
                ('PeakWorkingSetSize', ctypes.c_size_t), ('WorkingSetSize', ctypes.c_size_t),
                ('QuotaPeakPagedPoolUsage', ctypes.c_size_t),
                ('QuotaPagedPoolUsage', ctypes.c_size_t),
                ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t),
                ('QuotaNonPagedPoolUsage', ctypes.c_size_t), ('PagefileUsage', ctypes.c_size_t),
                ('PeakPagefileUsage', ctypes.c_size_t)]


def working_set_mb():
    try:
        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        handle = ctypes.windll.kernel32.GetCurrentProcess()
        if ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters),
                                                    counters.cb):
            return counters.WorkingSetSize / (1024 * 1024)
    except Exception:  # noqa: BLE001
        pass
    return 0.0


def build_snapshot():
    """The production-side 'prepare this candidate' cost, done once per worker."""
    checkpoints = Checkpoints([0])
    started = time.perf_counter()
    run_scenario(dict(default_scenario(), **CASE), checkpoints=checkpoints, stop_tick=0)
    stored = checkpoints.stored[0]
    engine = stored['engine']
    snapshot = ka_abi.engine_snapshot(engine, ROWS, int(engine.battle_state), HUMAN_BASES)
    return snapshot, time.perf_counter() - started


def worker(index, battles, queue):
    library = ka_abi.load()
    snapshot, setup_seconds = build_snapshot()
    library.ka_battle_set_scope_allowed
    imports, executions, exports = [], [], []
    started_wall = time.perf_counter()
    started_cpu = time.process_time()
    for _ in range(battles):
        mark = time.perf_counter()
        battle = ka_abi.load_snapshot(library, snapshot)
        imports.append(time.perf_counter() - mark)
        report = ka_abi.KaBattleReport()
        mark = time.perf_counter()
        status = library.ka_run_battle(battle, TICKS, POLICY, ctypes.byref(report))
        executions.append(time.perf_counter() - mark)
        mark = time.perf_counter()
        ka_abi.report_dict(report)
        exports.append(time.perf_counter() - mark)
        library.ka_battle_free(battle)
        if status != 0:
            queue.put(dict(index=index, error=status))
            return
    queue.put(dict(index=index, battles=battles,
                   wall=time.perf_counter() - started_wall,
                   cpu=time.process_time() - started_cpu,
                   setup=setup_seconds,
                   import_ms=statistics.median(imports) * 1e3,
                   exec_ms=statistics.median(executions) * 1e3,
                   export_ms=statistics.median(exports) * 1e3,
                   rss=working_set_mb()))


def main():
    counts = [int(value) for value in sys.argv[1:] if value.isdigit()]
    if not counts:
        counts = [1, 2, 4, 8, 12, 16, 24]
    battles = 20
    per_worker_exec = TICKS / 7000.0
    print(f'native worker scaling, encounter 19 @7000 ticks, {battles} battles per worker')
    base_rate = None
    for workers in counts:
        queue = mp.Queue()
        processes = [mp.Process(target=worker, args=(index, battles, queue))
                     for index in range(workers)]
        started = time.perf_counter()
        for process in processes:
            process.start()
        results = []
        for _ in processes:
            results.append(queue.get())
        for process in processes:
            process.join()
        wall = time.perf_counter() - started
        if any('error' in row for row in results):
            print(f'  {workers:2d} workers: native refused {[r for r in results if "error" in r]}')
            continue
        total = sum(row['battles'] for row in results)
        rate = total / wall
        if base_rate is None:
            base_rate = rate
        cpu = sum(row['cpu'] for row in results)
        print(f'  {workers:2d} workers: battles={total} wall={wall:.2f}s rate={rate:.1f}/s '
              f'eff={rate/(base_rate*workers)*100 if workers>1 else 100:.0f}% '
              f'cpu={cpu/(wall*workers)*100:.0f}% rss={results[0]["rss"]:.0f}MB '
              f'setup={results[0]["setup"]:.2f}s import={results[0]["import_ms"]:.1f}ms '
              f'exec={results[0]["exec_ms"]:.1f}ms export={results[0]["export_ms"]:.2f}ms')
    return 0


if __name__ == '__main__':
    sys.exit(main())
