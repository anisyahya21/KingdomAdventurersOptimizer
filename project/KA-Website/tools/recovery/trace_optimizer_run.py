"""Record what the optimiser actually does for a few minutes: code, calls, calculations, ownership.

Writes a directory of files describing one real run, so the question "what is the app doing, and who
is doing it" can be answered from evidence rather than from a summary:

  summary.md     the run in numbers plus a written account of the division of labour
  trace.jsonl    every semantic event, one JSON object per line, timestamped
  profile.md     sampled call stacks - which code ran, in which thread, how often
  sql.csv        every SQL statement shape executed, with counts (the calculations on the library)
  timeseries.csv the 5-second samples of the host's own counters
  run.json       the exact configuration used, so the recording is reproducible

Three independent layers, none of which relies on a debugger or a profiler that is not installed:

  * **stack sampling.** A sampler thread reads every Python thread's stack every few milliseconds and
    aggregates it, exactly as a sampling profiler does. It is cheap (no per-call hook), it needs no
    third-party package, and it attributes work to a *thread* and a *module* - which is the "who".
  * **SQL tracing.** Every SQLite connection is wrapped so each statement is recorded as a normalised
    shape (`SELECT ... FROM meta WHERE key IN (...)`) with a count. That is the "calculations".
  * **semantic events.** The interesting functions are wrapped to log what they were asked to do and
    what they decided: dispatches, batches flushed, candidates pruned, evidence rebuilt, banks
    declared, student passes, MP-recovery and Breakthrough decisions, and every UI call into the host.

The run is always made on a **copy** of the library unless `--no-copy` is given, so a recording never
touches a live search's data. The live host cannot be attached to from here (no py-spy), so a recording
is of a host process started by this script - stated plainly rather than implied.

    python trace_optimizer_run.py --src A:/.../live.sqlite --out trace-5min --duration 300 --workers 24
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import pathlib
import re
import shutil
import sqlite3
import statistics
import sys
import threading
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

#: The thread that drives a recording. Its samples are the recorder's own work (polling the host), so
#: they are counted separately from the application's.
DRIVER_THREAD = 'ka-trace-driver'

#: Functions called so often that one event per call would swamp the trace. They are counted instead,
#: and their cost is visible in the sampled profile.
QUIET_FUNCTIONS = {'staged_limits', 'lanes_record', 'lane_record', 'merge_aggregates',
                   'equivalence_identity', 'to_limits', 'scenario_for'}

# ---------------------------------------------------------------------------------------------
# The recorder
# ---------------------------------------------------------------------------------------------

#: Where the sampler and the event log put their data. One recorder per process; a run is one process.
class Recorder:
    def __init__(self, out, sample_ms=5.0):
        self.out = pathlib.Path(out)
        self.out.mkdir(parents=True, exist_ok=True)
        self.started = time.time()
        self.sample_seconds = sample_ms/1000.0
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._events = []
        self._event_counts = collections.Counter()
        self._self_samples = collections.Counter()      # (thread, function) -> samples
        self._thread_samples = collections.Counter()    # thread -> samples
        self._stacks = collections.Counter()            # truncated stack -> samples
        self._module_samples = collections.Counter()    # top-level module -> samples
        self._idle_samples = collections.Counter()      # thread -> samples spent waiting, not working
        self._sql_shapes = collections.Counter()
        self._sql_modules = collections.Counter()
        self._sql_bytes = collections.Counter()
        self._maxed = False
        self._sampler = None
        self._connections = 0

    # ---- events -----------------------------------------------------------------------------
    def count(self, component, kind):
        """Count a hot call without writing an event for it."""
        with self._lock:
            self._event_counts[f'{component}.{kind}'] += 1

    def event(self, component, kind, **detail):
        """One semantic event. Kept JSON-safe and small; `trace.jsonl` is what a reader greps.

        The action is called `kind` in the signature on purpose: a caller's own payload may legitimately
        contain a key named `action` (an optimiser command, for instance), and that must be recorded
        rather than collide with this function's parameter.
        """
        if self._maxed:
            return
        entry = dict(t=round(time.time()-self.started, 4), thread=threading.current_thread().name,
                     component=component, action=kind)
        # A detail key that happens to be one of the reserved names must not collide with the event's
        # own fields: rename it rather than losing either value.
        for key, value in detail.items():
            if value is None:
                continue
            entry[f'arg_{key}' if key in entry else key] = value
        with self._lock:
            self._events.append(entry)
            self._event_counts[f'{component}.{kind}'] += 1
            if len(self._events) > 200000:
                self._maxed = True

    # ---- stack sampling ---------------------------------------------------------------------
    def _record_stack(self, thread_name, frames):
        """One sample: the self function, the thread, and a bounded call path."""
        if not frames:
            return
        top = frames[-1]
        source = pathlib.Path(top.f_code.co_filename).name
        # A thread parked in the executor's own work loop - or in the sampler's driver - is waiting,
        # not computing. Counting that as "work" is what made an earlier version report `thread.py` as
        # the busiest module in the application.
        if source in ('thread.py', 'queue.py', 'selectors.py') or thread_name == DRIVER_THREAD:
            with self._lock:
                self._idle_samples[thread_name] += 1
            return
        name = f'{pathlib.Path(top.f_code.co_filename).name}:{top.f_code.co_name}'
        module = source
        with self._lock:
            self._self_samples[(thread_name, name)] += 1
            self._thread_samples[thread_name] += 1
            self._module_samples[module] += 1
            if len(self._stacks) < 40000:
                path = ' -> '.join(f'{pathlib.Path(f.f_code.co_filename).name}:{f.f_code.co_name}'
                                   for f in frames[-8:])
                self._stacks[(thread_name, path)] += 1

    def _sample_loop(self):
        while not self._stop.wait(self.sample_seconds):
            try:
                frames = sys._current_frames()
            except Exception:  # noqa: BLE001
                continue
            for ident, frame in frames.items():
                thread = threading._active.get(ident)  # noqa: SLF001 - the only way to name a thread
                name = thread.name if thread is not None else f'thread-{ident}'
                if name == threading.current_thread().name:
                    continue
                frames_list = []
                depth = 0
                while frame is not None and depth < 60:
                    frames_list.append(frame)
                    frame = frame.f_back
                    depth += 1
                frames_list.reverse()
                # Threads that are idle in the interpreter's own machinery are not work.
                if frames_list and 'threading.py' in pathlib.Path(frames_list[-1].f_code.co_filename).name:
                    continue
                self._record_stack(name, frames_list)

    def start(self):
        self._sampler = threading.Thread(target=self._sample_loop, name='ka-trace-sampler', daemon=True)
        self._sampler.start()
        # Every SQLite connection in this process is traced, wherever it is opened.
        original_connect = sqlite3.connect
        recorder = self

        def traced_connect(*args, **kwargs):
            connection = original_connect(*args, **kwargs)
            recorder._connections += 1
            connection.set_trace_callback(recorder.sql_trace)
            return connection

        sqlite3.connect = traced_connect

    def stop(self):
        self._stop.set()
        if self._sampler is not None:
            self._sampler.join(timeout=2)

    # ---- SQL -------------------------------------------------------------------------------
    _LITERAL = re.compile(r"'[^']*'|\b\d+\b")

    def sql_trace(self, statement):
        """A normalised statement shape: literals replaced, long lists collapsed."""
        try:
            text = ' '.join(str(statement).split())
        except Exception:  # noqa: BLE001
            return
        shape = self._LITERAL.sub('?', text)
        shape = re.sub(r'(\?,){3,}\?', '?,?,...', shape)
        shape = shape[:180]
        with self._lock:
            self._sql_shapes[shape] += 1
            self._sql_bytes[shape] += len(text)

    # ---- output ----------------------------------------------------------------------------
    def write(self, run, timeseries, extra=None):
        seconds = max(0.001, time.time()-self.started)
        with self._lock:
            events = list(self._events)
            self_samples = dict(self._self_samples)
            thread_samples = dict(self._thread_samples)
            stacks = dict(self._stacks)
            modules = dict(self._module_samples)
            idle = dict(self._idle_samples)
            sql_shapes = dict(self._sql_shapes)
            sql_bytes = dict(self._sql_bytes)
            counts = dict(self._event_counts)
        (self.out/'trace.jsonl').write_text(
            '\n'.join(json.dumps(e, sort_keys=True) for e in events) + '\n', encoding='utf-8')
        (self.out/'run.json').write_text(json.dumps(dict(run=run, seconds=round(seconds, 1),
                                                         events=len(events),
                                                         connections=self._connections,
                                                         sampleIntervalMs=self.sample_seconds*1000,
                                                         extra=extra or {}), indent=1,
                                                    default=str), encoding='utf-8')
        self._write_timeseries(timeseries)
        self._write_sql(sql_shapes, sql_bytes)
        self._write_profile(thread_samples, self_samples, stacks, modules, seconds)
        self._write_summary(run, timeseries, counts, self_samples, thread_samples, modules,
                            sql_shapes, seconds, len(events), extra or {})
        (self.out/'waiting.csv').write_text(
            'thread,waiting_samples\n'
            + '\n'.join(f'{name},{count}' for name, count in sorted(idle.items(), key=lambda kv: -kv[1]))
            + '\n', encoding='utf-8')

    def _write_timeseries(self, timeseries):
        if not timeseries:
            return
        keys = sorted({k for row in timeseries for k in row})
        lines = [','.join(keys)]
        for row in timeseries:
            lines.append(','.join('' if row.get(k) is None else str(row.get(k)) for k in keys))
        (self.out/'timeseries.csv').write_text('\n'.join(lines) + '\n', encoding='utf-8')

    def _write_sql(self, shapes, sizes):
        rows = sorted(shapes.items(), key=lambda kv: -kv[1])
        total = sum(shapes.values())
        lines = ['count,mean_chars,statement']
        for shape, count in rows[:300]:
            lines.append(f'{count},{round(sizes[shape]/max(1,count))},"{shape.replace(chr(34), chr(39))}"')
        (self.out/'sql.csv').write_text('\n'.join(lines) + '\n', encoding='utf-8')
        return total

    def _write_profile(self, thread_samples, self_samples, stacks, modules, seconds):
        total = sum(thread_samples.values()) or 1
        out = ['# Sampled profile', '',
               f'{total} stack samples over {seconds:.0f} s '
               f'({total/seconds:.1f} samples/s across all threads).', '',
               '## By thread (who)', '', '| thread | samples | share |', '|---|---|---|']
        for name, count in sorted(thread_samples.items(), key=lambda kv: -kv[1]):
            out.append(f'| {name} | {count} | {count/total*100:.1f}% |')
        out += ['', '## By module (which code)', '', '| module | samples | share |', '|---|---|---|']
        module_total = sum(modules.values()) or 1
        for name, count in sorted(modules.items(), key=lambda kv: -kv[1])[:25]:
            out.append(f'| {name} | {count} | {count/module_total*100:.1f}% |')
        out += ['', '## Hottest functions (self samples)', '',
                '| thread | function | samples | share |', '|---|---|---|---|']
        for (thread, function), count in sorted(self_samples.items(), key=lambda kv: -kv[1])[:40]:
            out.append(f'| {thread} | {function} | {count} | {count/total*100:.2f}% |')
        out += ['', '## Most frequent call paths', '',
                '| samples | path (innermost last) |', '|---|---|']
        for (thread, path), count in sorted(stacks.items(), key=lambda kv: -kv[1])[:30]:
            out.append(f'| {count} | {thread}: {path} |')
        (self.out/'profile.md').write_text('\n'.join(out) + '\n', encoding='utf-8')

    def _write_summary(self, run, timeseries, counts, self_samples, thread_samples, modules,
                       sql_shapes, seconds, events, extra):
        total_samples = sum(thread_samples.values()) or 1
        runs = run.get('runsExecuted') or 0
        out = ['# What the optimiser did', '',
               f'Recorded {seconds:.0f} s of a real run on **{run.get("library")}** '
               f'({run.get("workers")} workers, duty {run.get("duty")}).', '',
               'This is a copy of that library, driven by a host process started for the recording - '
               'not the live window, which cannot be attached to from here.', '',
               '## Headline numbers', '',
               f'* battles executed: **{runs}** ({runs/max(1,seconds):.1f}/s)',
               f'* semantic events recorded: **{events}**',
               f'* SQL statements: **{sum(sql_shapes.values())}** in '
               f'{len(sql_shapes)} distinct shapes',
               f'* stack samples: {total_samples} ({total_samples/max(1,seconds):.0f}/s)',
               f'* SQLite connections opened: {self._connections}', '']
        if run.get('effectiveShares'):
            out += ['## The settings this recording actually ran with', '',
                    'A copied library does not reproduce in-memory settings, so these are read back from '
                    'the host *after* they were applied, not assumed from a file name.', '',
                    f'* student shares: `{json.dumps(run["effectiveShares"], sort_keys=True)}`',
                    f'* MP-recovery allowance: `{json.dumps(run.get("effectiveMpAllowance"), sort_keys=True)}`',
                    '']
        if extra.get('reconciliation'):
            row = extra['reconciliation']
            out += ['', '## Reconciling the totals', '',
                    f'* accepted runs in the window (host counter): **{row["totalRuns"]}**',
                    f'* attributed to a candidate in the window: **{row["attributed"]}**',
                    f'* residual: **{row["residual"]}**', '',
                    row['note'], '']
        if timeseries:
            # The final row is read after the stop, when the rate has decayed to zero; the row with the
            # highest run count is the last *running* state, which is what a reader wants.
            last = max(timeseries, key=lambda row: row.get('totalRuns') or 0)
            out += ['## Host counters at the end of the recording', '',
                    f'* totalRuns: {last.get("totalRuns")}',
                    f'* battles/s: {last.get("battlesPerSecond")}',
                    f'* active workers (10 s average): {last.get("activeWorkersAverage10s")}',
                    f'* occupancy: {last.get("workerOccupancy")}',
                    f'* open seeds: {last.get("reservoirSeeds")}',
                    f'* coordinator seconds spent - evidence {last.get("evidenceSeconds")}, '
                    f'publish {last.get("publishSeconds")}, record {last.get("recordSeconds")}, '
                    f'top-up {last.get("topUpSeconds")}, analysis {last.get("analysisSeconds")}',
                    f'* analysis passes: {last.get("analysisRuns")} run, '
                    f'{last.get("analysisDeferred")} deferred while the pool already had work',
                    f'* analysis seconds by step: {last.get("analysisSteps")}',
                    f'* focused portfolio recomputations: {last.get("portfolioRefresh")}', '']
        out += ['## Who was doing what', '',
                'The sampled profile attributes every sample to a thread, and the modules below say '
                'which code that thread was in.', '',
                '| thread | samples | share |', '|---|---|---|']
        for name, count in sorted(thread_samples.items(), key=lambda kv: -kv[1]):
            out.append(f'| {name} | {count} | {count/total_samples*100:.1f}% |')
        out += ['', '| module | samples | share |', '|---|---|---|']
        module_total = sum(modules.values()) or 1
        for name, count in sorted(modules.items(), key=lambda kv: -kv[1])[:15]:
            out.append(f'| {name} | {count} | {count/module_total*100:.1f}% |')
        out += ['', '## Event counts by component and action', '',
                '| component.action | count |', '|---|---|']
        for name, count in sorted(counts.items(), key=lambda kv: -kv[1]):
            out.append(f'| {name} | {count} |')
        if extra.get('by_stream'):
            out += ['', '## Completed battles in this recording, by the stream that owns the build', '',
                    'Measured as a delta across the recording, so these are this window\'s numbers and '
                    'not the library\'s lifetime totals.', '',
                    '| stream | battles | mean chests | wins | losses |', '|---|---|---|---|---|']
            for stream, row in sorted(extra['by_stream'].items(), key=lambda kv: -kv[1]['runs']):
                mean = (row['chests']/row['runs']) if row['runs'] else 0
                out.append(f"| {stream} | {row['runs']} | {mean:.3f} | {row['wins']} | {row['losses']} |")
        if extra.get('by_encounter'):
            out += ['', '## Completed battles in this recording, by encounter', '',
                    '| encounter | battles | mean chests |', '|---|---|---|']
            for encounter, row in sorted(extra['by_encounter'].items(), key=lambda kv: -kv[1]['runs']):
                mean = (row['chests']/row['runs']) if row['runs'] else 0
                out.append(f"| {encounter} | {row['runs']} | {mean:.3f} |")
        if extra.get('sql_top'):
            out += ['', '## The calculations: most frequent SQL shapes', '',
                    '| count | statement |', '|---|---|']
            for shape, count in extra['sql_top'][:12]:
                out.append(f'| {count} | `{shape}` |')
        if extra.get('notes'):
            out += ['', '## Notes', ''] + [f'* {note}' for note in extra['notes']]
        (self.out/'summary.md').write_text('\n'.join(out) + '\n', encoding='utf-8')


# ---------------------------------------------------------------------------------------------
# Instrumentation of the application's own functions
# ---------------------------------------------------------------------------------------------

def _wrap(recorder, module, name, component, action=None, detail=None):
    """Wrap one function or method so its calls, duration and a small payload are recorded."""
    target = getattr(module, name, None)
    if target is None:
        return False
    label = action or name
    # A function called once per candidate per pass (a limit calculation, say) is counted, never
    # logged: one event per call would drown the trace it is supposed to explain.
    quiet = name in QUIET_FUNCTIONS

    def wrapper(*args, **kwargs):
        if quiet:
            recorder.count(component, label)
            return target(*args, **kwargs)
        started = time.perf_counter()
        try:
            result = target(*args, **kwargs)
        except BaseException:
            recorder.event(component, label, failed=True,
                           ms=round((time.perf_counter()-started)*1000, 2))
            raise
        payload = detail(result, args, kwargs) if detail else None
        recorder.event(component, label, ms=round((time.perf_counter()-started)*1000, 2),
                       **(payload or {}))
        return result

    wrapper.__name__ = getattr(target, '__name__', name)
    wrapper.__doc__ = getattr(target, '__doc__', None)
    setattr(module, name, wrapper)
    return True


def instrument(recorder):
    """Wrap the functions a reader would want to see named in the trace. Returns what was wrapped."""
    import strategy_dispatch
    import strategy_optimizer
    import strategy_mp_recovery
    import strategy_breakthrough
    import strategy_students
    import strategy_optimizer_desktop

    wrapped = []

    def add(module, name, component, action=None, detail=None):
        if _wrap(recorder, module, name, component, action, detail):
            wrapped.append(f'{module.__name__}.{name}')

    # ---- dispatch: what the planner authorised and handed to the pool ----
    add(strategy_dispatch, 'ready_window', 'planner', 'ready-window',
        lambda result, a, k: dict(seeds=len(result[0]), top=result[0][:3]))
    add(strategy_optimizer, 'population_records', 'planner', 'population-records',
        lambda result, a, k: dict(candidates=len(result)))
    add(strategy_optimizer, 'incumbent_banks', 'planner', 'incumbent-banks',
        lambda result, a, k: dict(banks=len(result),
                                  review=sum(1 for v in result.values()
                                             if v >= strategy_optimizer.RANK_REVIEW_RUNS)))
    add(strategy_optimizer, 'staged_limits', 'planner', 'staged-limits')

    # ---- the store: the calculations that touch the library ----
    add(strategy_optimizer.Store, 'flush', 'store', 'flush',
        lambda result, a, k: dict(records=result))
    add(strategy_optimizer.Store, 'add_child', 'store', 'add-child',
        lambda result, a, k: dict(candidate=result[0], existed=result[1],
                                  stream=(a[8] if len(a) > 8 else None)))
    add(strategy_optimizer.Store, 'close', 'store', 'close')
    add(strategy_optimizer.Store, '_make_room', 'store', 'prune',
        lambda result, a, k: dict(encounter=a[1] if len(a) > 1 else None))

    # ---- the coordinator's own passes ----
    add(strategy_optimizer.Optimizer, '_publish', 'coordinator', 'publish')
    add(strategy_optimizer.Optimizer, 'flush_records', 'coordinator', 'flush-records',
        lambda result, a, k: dict(applied=result))
    add(strategy_optimizer.Optimizer, '_advance_breakthrough', 'breakthrough', 'pass',
        lambda result, a, k: dict(changed=result))
    add(strategy_optimizer.Optimizer, '_advance_mp_recovery', 'mp-recovery', 'pass',
        lambda result, a, k: dict(changed=result))
    add(strategy_mp_recovery, 'advance', 'mp-recovery', 'scan',
        lambda result, a, k: dict(examined=result.get('examined'),
                                  eligible=len(result.get('awaiting') or []),
                                  created=len(result.get('created') or []),
                                  reused=len(result.get('reused') or []),
                                  timedOut=result.get('timedOut'),
                                  decision=(result.get('decision') or '')[:120]))
    add(strategy_mp_recovery, 'configure', 'mp-recovery', 'configure',
        lambda result, a, k: dict(ok=result.get('ok')))
    add(strategy_breakthrough, 'advance', 'breakthrough', 'advance',
        lambda result, a, k: dict(enabled=result.get('enabled'), closed=len(result.get('closed') or []),
                                  encounter=result.get('encounter'),
                                  decision=(result.get('decision') or '')[:140]))
    add(strategy_breakthrough, 'start_confirmation', 'breakthrough', 'freeze-confirmation')
    add(strategy_breakthrough, 'evaluate_confirmation', 'breakthrough', 'evaluate-confirmation')
    add(strategy_students, 'average_report', 'student-9', 'report')
    add(strategy_students, 'replenish_average', 'student-9', 'replenish')
    add(strategy_optimizer, 'spawn_children', 'students', 'spawn-children',
        lambda result, a, k: dict(created=len(result or [])))

    # ---- the UI's own calls into the host ----
    add(strategy_optimizer.Optimizer, 'command', 'ui', 'command',
        lambda result, a, k: dict(action=a[1] if len(a) > 1 else None))
    add(strategy_optimizer.Optimizer, 'status', 'ui', 'status')
    add(strategy_optimizer_desktop.Bridge, 'status', 'ui', 'bridge-status')
    add(strategy_optimizer_desktop.Bridge, 'command', 'ui', 'bridge-command',
        lambda result, a, k: dict(action=a[1] if len(a) > 1 else None, ok=result.get('ok')))
    for name in ('encounter_strategies', 'overview_average_leaders', 'candidate_detail'):
        add(strategy_optimizer_desktop.Bridge, name, 'ui', name)

    return wrapped


# ---------------------------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------------------------

def main(argv=None):
    threading.current_thread().name = DRIVER_THREAD
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--src', required=True, help='library to copy and record')
    parser.add_argument('--out', required=True, help='directory for the recording')
    parser.add_argument('--duration', type=float, default=300.0)
    parser.add_argument('--budget', type=int, default=0,
                        help='stop after this many newly accepted runs (0 = time only)')
    parser.add_argument('--workers', type=int, default=24)
    parser.add_argument('--duty', type=float, default=1.0)
    parser.add_argument('--sample-ms', type=float, default=5.0)
    parser.add_argument('--no-copy', action='store_true',
                        help='record on the given library directly (not recommended)')
    parser.add_argument('--keep-library', action='store_true',
                        help='keep the working copy of the library (it is deleted by default)')
    parser.add_argument('--shares', help='student shares to apply, e.g. '
                                         '"community=0.25,average=0.70,discovery=0.05"')
    parser.add_argument('--mp-stock', type=int, help='MP-recovery Holy Herb stock to apply')
    parser.add_argument('--mp-uses', type=int, help='MP-recovery maximum uses per battle to apply')
    parser.add_argument('--focus', type=int, help='focus one encounter')
    args = parser.parse_args(argv)

    out = pathlib.Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    library = pathlib.Path(args.src).resolve()
    if not args.no_copy:
        target = out/'library.sqlite'
        if target.exists():
            target.unlink()
        with sqlite3.connect(f'file:{library}?mode=ro', uri=True) as source:
            with sqlite3.connect(str(target)) as copy:
                source.backup(copy)
        library = target.resolve()

    recorder = Recorder(out, sample_ms=args.sample_ms)
    # The attribution connection is opened *before* SQL tracing is installed, so the recorder's own
    # polls do not appear in the histogram of the application's statements.
    attribution = sqlite3.connect(f'file:{library}?mode=ro', uri=True)
    attribution.row_factory = sqlite3.Row
    stream_of = {row[0]: row[1] for row in attribution.execute(
        'SELECT candidate, source FROM lineage').fetchall()}
    encounter_of = {row[0]: row[1] for row in attribution.execute(
        'SELECT id, encounter FROM candidate_meta').fetchall()}
    recorder.start()
    wrapped = instrument(recorder)

    import strategy_optimizer_desktop as desktop
    bridge = desktop.Bridge(library)
    engine = bridge._optimizer
    run = dict(library=str(library), source=str(args.src), workers=args.workers, duty=args.duty,
               durationSeconds=args.duration, wrapped=wrapped)
    timeseries = []
    by_stream = collections.defaultdict(lambda: dict(runs=0, chests=0, wins=0, losses=0))
    by_encounter = collections.defaultdict(lambda: dict(runs=0, chests=0))
    # Window-exact attribution: each poll reads every candidate's validation aggregate and differences
    # it against the previous poll, so the table describes *this recording* rather than the library's
    # lifetime. Mixing the two - a 5-minute run count divided into a lifetime chest total - would have
    # produced a nonsense mean, which is what the first version of this summary did.
    previous_aggregate = {}
    attributed_total = 0
    try:
        deadline = time.monotonic() + 900
        while time.monotonic() < deadline and bridge.status().get('state') == 'Opening library':
            time.sleep(.25)
        recorder.event('recorder', 'start', workers=args.workers, duty=args.duty)
        # Apply the requested configuration *before* starting, and read it back from the host: a copy of
        # the library carries no in-memory settings, so what the run actually used has to be recorded
        # from the backend that accepted it rather than inferred from a file name.
        if args.shares:
            requested = {}
            for part in args.shares.split(','):
                name, _sep, value = part.partition('=')
                requested[name.strip()] = float(value)
            accepted = bridge.command('students', dict(shares=requested))
            recorder.event('recorder', 'apply-shares', requested=requested, accepted=accepted)
        if args.mp_stock is not None or args.mp_uses is not None:
            allowance = dict(enabled=True, stock=int(args.mp_stock or 0), maxUses=int(args.mp_uses or 0))
            accepted = bridge.command('mp_recovery', allowance)
            recorder.event('recorder', 'apply-mp-allowance', requested=allowance, accepted=accepted)
        if args.focus is not None:
            bridge.command('focus_encounter', dict(encounterId=int(args.focus)))
        bridge.command('start', dict(workers=args.workers, duty=args.duty))
        opened = bridge.status()
        run['effectiveShares'] = (opened.get('students') or {}).get('shares')
        run['effectiveMpAllowance'] = (opened.get('mpRecovery') or {}).get('setting')
        run['focusEncounter'] = opened.get('focusEncounter')
        recorder.event('recorder', 'effective-settings', shares=run['effectiveShares'],
                       mpAllowance=run['effectiveMpAllowance'], focus=run['focusEncounter'])
        recorder.event('recorder', 'attribution', candidates=len(encounter_of),
                       streams=len(set(stream_of.values())))
        started = time.time()
        base_runs = bridge.status().get('totalRuns') or 0
        last_seen = collections.Counter()
        while time.time() - started < args.duration:
            time.sleep(5)
            sample = bridge.status()
            if args.budget and (sample.get('totalRuns') or 0) - base_runs >= args.budget:
                recorder.event('recorder', 'budget-reached',
                               runs=(sample.get('totalRuns') or 0) - base_runs, budget=args.budget)
                break
            scheduler = sample.get('scheduler') or {}
            throughput = sample.get('throughput') or {}
            timeseries.append(dict(
                seconds=round(time.time()-started, 1),
                totalRuns=sample.get('totalRuns'),
                sessionRuns=(sample.get('totalRuns') or 0)-base_runs,
                battlesPerSecond=round(throughput.get('battlesPerSecond') or 0, 2),
                workers=throughput.get('workers'),
                occupancy=round(scheduler.get('workerOccupancy') or 0, 3),
                activeWorkersAverage10s=round(scheduler.get('activeWorkersAverage10s') or 0, 2),
                reservoirSeeds=scheduler.get('reservoirSeeds'),
                evidenceSeconds=round(scheduler.get('evidenceSeconds') or 0, 2),
                publishSeconds=round(scheduler.get('publishSeconds') or 0, 2),
                recordSeconds=round(scheduler.get('recordSeconds') or 0, 2),
                topUpSeconds=round(scheduler.get('topUpSeconds') or 0, 2),
                # The deferred-analysis counters: how much wall clock the cadence spent, how often
                # it ran at all, and how often it stood aside because the pool already had work.
                # Recorded rather than asserted, so "analysis is off the feeding path" is a
                # measurement instead of a design claim.
                analysisSeconds=round(scheduler.get('analysisSeconds') or 0, 2),
                analysisRuns=scheduler.get('analysisRuns'),
                analysisDeferred=scheduler.get('analysisDeferred'),
                analysisSteps=json.dumps(scheduler.get('analysisStepSeconds') or {}, sort_keys=True),
                portfolioRefresh=scheduler.get('portfolioRefresh'),
                dispatchedSeeds=scheduler.get('dispatchedSeeds'),
                payloadMB=None))
            # What the workers actually finished, attributed to the stream and fight that own the
            # build, all measured as a delta against the previous poll.
            try:
                finished = 0
                # Builds the search itself creates during the recording have no owner in the initial
                # map, so the maps are refreshed as we go - otherwise every child proposed mid-run is
                # attributed to "unknown", which is what the first version of this table showed.
                for candidate, source in attribution.execute(
                        'SELECT candidate, source FROM lineage').fetchall():
                    stream_of.setdefault(candidate, source)
                for candidate, encounter in attribution.execute(
                        'SELECT id, encounter FROM candidate_meta').fetchall():
                    encounter_of.setdefault(candidate, encounter)
                # Both phases: a discovery run is as much a battle as a validation one, and the first
                # version of this table polled validation only - which is one of the reasons its rows
                # summed to 36,390 while the headline reported 43,603.
                rows = attribution.execute(
                    "SELECT key, value FROM meta WHERE key LIKE 'aggregate:%'").fetchall()
                for key, value in rows:
                    parts = str(key).split(':')
                    if len(parts) != 3:
                        continue
                    candidate, phase = parts[1], parts[2]
                    try:
                        payload = json.loads(value)
                    except (TypeError, ValueError):
                        continue
                    now = (int(payload.get('n') or 0), float(payload.get('chestSum') or 0.0),
                           int(payload.get('wins') or 0), int(payload.get('losses') or 0))
                    before = previous_aggregate.get((candidate, phase))
                    previous_aggregate[(candidate, phase)] = now
                    if before is None:
                        continue     # first sight of this candidate: no window delta yet
                    runs = now[0]-before[0]
                    if runs <= 0:
                        continue
                    finished += runs
                    attributed_total += runs
                    stream = stream_of.get(candidate, 'unknown')
                    row = by_stream[stream]
                    row['runs'] += runs
                    row['chests'] += now[1]-before[1]
                    row['wins'] += now[2]-before[2]
                    row['losses'] += now[3]-before[3]
                    encounter = by_encounter[encounter_of.get(candidate, -1)]
                    encounter['runs'] += runs
                    encounter['chests'] += now[1]-before[1]
                recorder.event('recorder', 'sample', finished=finished, candidates=len(rows))
            except Exception as exc:  # noqa: BLE001 - a failed attribution must not stop recording
                recorder.event('recorder', 'sample-failed', error=repr(exc)[:120])
        run['runsExecuted'] = (bridge.status().get('totalRuns') or 0) - base_runs
    finally:
        try:
            bridge.command('stop', {})
        except Exception:  # noqa: BLE001
            pass
        time.sleep(3)
        recorder.event('recorder', 'stop')
        try:
            bridge.close()
        except Exception:  # noqa: BLE001
            pass

    # One final poll so the last interval's work is included rather than truncated.
    try:
        rows = attribution.execute(
            "SELECT key, value FROM meta WHERE key LIKE 'aggregate:%'").fetchall()
        for key, value in rows:
            parts = str(key).split(':')
            if len(parts) != 3:
                continue
            candidate, phase = parts[1], parts[2]
            try:
                payload = json.loads(value)
            except (TypeError, ValueError):
                continue
            now = (int(payload.get('n') or 0), float(payload.get('chestSum') or 0.0),
                   int(payload.get('wins') or 0), int(payload.get('losses') or 0))
            before = previous_aggregate.get((candidate, phase))
            if before is None:
                continue
            runs = now[0]-before[0]
            if runs <= 0:
                continue
            attributed_total += runs
            stream = stream_of.get(candidate, 'unknown')
            row = by_stream[stream]
            row['runs'] += runs
            row['chests'] += now[1]-before[1]
            row['wins'] += now[2]-before[2]
            row['losses'] += now[3]-before[3]
            encounter = by_encounter[encounter_of.get(candidate, -1)]
            encounter['runs'] += runs
            encounter['chests'] += now[1]-before[1]
    except Exception:  # noqa: BLE001
        pass
    attribution.close()

    recorder.stop()
    total_runs = int(run.get('runsExecuted') or 0)
    residual = total_runs - attributed_total
    extra = dict(by_stream={k: dict(v) for k, v in by_stream.items()},
                 by_encounter={str(k): dict(v) for k, v in by_encounter.items()},
                 sql_top=sorted(recorder._sql_shapes.items(), key=lambda kv: -kv[1])[:12],
                 reconciliation=dict(totalRuns=total_runs, attributed=attributed_total,
                                     residual=residual,
                                     note=(
                                         'The host counter counts every accepted result; the attribution '
                                         'differences each candidate\'s own counters between polls. The '
                                         'residual is what the attribution cannot see, and it is stated '
                                         'rather than smoothed away: (a) the first poll only establishes a '
                                         'baseline, so runs accepted before it are not attributed; '
                                         '(b) candidates the population cap prunes during the window take '
                                         'their counters out of the table before the next poll, so their '
                                         'last interval is lost; (c) results accepted after the final poll '
                                         'and before the stop are counted by neither. It is not lost work - '
                                         'the library holds every accepted result - and it is not a '
                                         'duplicate count either.')),
                 notes=[
                     'The sampler reads every Python thread\'s stack every '
                     f'{args.sample_ms:.0f} ms; a sample is attributed to the function at the top of '
                     'the stack (self time), which is how "who is doing what" is measured.',
                     'Worker processes are separate and cannot be sampled from here; their work is '
                     'represented by what the host dispatched and by the results it recorded.',
                     'MP-recovery and Breakthrough passes appear as events with their decisions, so a '
                     'pass that finds nothing is still visible.',
                 ])
    recorder.write(run, timeseries, extra)
    # The working copy is the recording's own scratch: 1.9 GB per run, left behind for no reason. The
    # source library is never touched either way.
    if not args.no_copy and not args.keep_library:
        for suffix in ('', '-wal', '-shm', '.lock'):
            candidate = pathlib.Path(str(library)+suffix)
            try:
                if candidate.exists():
                    candidate.unlink()
            except OSError:
                pass
        run['library'] = '(working copy deleted after recording; source: ' + str(args.src) + ')'
        (out/'run.json').write_text(json.dumps(run, indent=1, default=str), encoding='utf-8')
    print(f'recording written to {out}')
    for name in ('summary.md', 'profile.md', 'sql.csv', 'timeseries.csv', 'trace.jsonl'):
        path = out/name
        print(f'  {name}: {path.stat().st_size/1024:.1f} KB' if path.exists() else f'  {name}: missing')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
