"""Parity + warm unprofiled timing for strategy_optimizer_loop_fast.

Run it with the dedicated runtime so the measured serial loop is the real JIT one:

  $LOCALAPPDATA/KingdomAdventurersOptimizer/runtime/pypy3.11-v7.23-win64/pypy3.exe \
      check_strategy_optimizer_loop_fast.py --golden strategy-optimizer-parity.json \
      --report strategy-optimizer-loop-fast-report.json

The parent spawns two child workers with the SAME interpreter - one unpatched (`baseline`) and one
with `strategy_optimizer_loop_fast.install()` (`variant`). Each child warms the JIT, then times
every case serially and unprofiled, and compares the entire canonical report minus `manifest`
(trace, RNG final state, final state, receipt, metrics, setup...) against the golden digest.

Installation is explicit: the reference engine is only patched inside the variant child, so a
baseline run is byte-identical to the shipped runner and no golden is regenerated. Nothing here
imports combat internals beyond the canonical `run_scenario` entry point.

Targeted regressions (variant child, never inside the timed loop):

  * `unrelatedWrite` - `ComponentSubset.update` written with an untracked component type while
    `matches()` is true, `on_added` is live and `cache` holds a value. Canonical behaviour is
    `members.add`, `cache = None`, `on_added(...)`. A reintroduced early-out fails this.
  * `animationFrames` - the deferred global clip loop must materialise the exact canonical
    `resource['frame']` values for the real `animation-resources.json` tables, and must not count
    advances when `auto_animation` is set (the canonical loop does not advance then).
  * `opponentCache` - at every `opponent_in_range` call of a dedicated probe run, the cached roster
    is compared against the live canonical comprehension; any stale entry is counted as a mismatch.
  * `callCounts` - every alias-bound patch must show a non-zero call count, so "the patch is
    installed" is never confused with "the patch is reached".
"""
import argparse
import hashlib
import json
import platform
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

WARMUP_CASE = dict(encounterId=5, tickLimit=1000, mathSeed=12, libSeed=13)
WARMUP_RUNS = 3
PROBE_CASE = dict(encounterId=19, tickLimit=1000, mathSeed=7 + 19, libSeed=8 + 19)

# Every one of these must be reached at least once in the probe run. `combat_animation.frame_resource`
# is deliberately absent: with the deferred loop the canonical function is only called by
# `materialize_resource_frames`, and the animation regression proves it still runs there.
# `combat_shared_controllers` calls its own imported aliases, so the defining-module bindings
# (combat_navigation.queue_position, combat_animation.update_animations) are reported but never
# required. `combat_shared_controllers.queue_position` is patched but genuinely NOT reached in this
# corpus (`decide` only asks for a queue position when the native cell guard is open, which none of
# these encounters hit), so no timing credit is claimed for it - see `notReachedInCorpus`.
REQUIRED_COUNTS = (
    "FighterView.__getitem__", "FighterView.__setitem__",
    "ComponentWindow.__getitem__", "ComponentWindow.__setitem__",
    "SharedControllers.effective", "SharedControllers.opponent_in_range",
    "combat_shared_controllers.update_animations",
    "combat_animation.signed_remainder",
    "combat_shared_controllers.same_grid",
    "EntitySlotSet.__iter__",
)
OPTIONAL_COUNTS = ("combat_shared_controllers.queue_position", "combat_navigation.queue_position",
                   "combat_navigation.same_grid", "combat_animation.update_animations",
                   "combat_animation.frame_resource")


def cases():
    rows = [dict(encounterId=i, tickLimit=1000, mathSeed=7 + i, libSeed=8 + i) for i in range(20)]
    rows += [dict(encounterId=19, tickLimit=7000, mathSeed=a, libSeed=b)
             for a, b in ((7, 8), (907303519, 267534053))]
    return rows


def digest_of(report):
    # Manifest carries runtime/source identity only; every gameplay field stays in the digest.
    checked = {key: value for key, value in report.items() if key != "manifest"}
    return hashlib.sha256(json.dumps(checked, sort_keys=True).encode()).hexdigest()


class CallbackLog:
    def __init__(self):
        self.events = []

    def __call__(self, *args):
        self.events.append(args)


def check_component_subset_unrelated_write():
    """Canonical `update` semantics must survive: unrelated write still fires on_added + clears cache."""
    from combat_collections import ComponentSubset
    log_added, log_removed = CallbackLog(), CallbackLog()
    unit = {"id": 7, "components": {2: object(), 12: object(), 28: object()}}
    subset = ComponentSubset((2, 12, 28), (), on_added=log_added, on_removed=log_removed)
    checks = {}
    # matches() is true, member already present, cache holds a value: the unrelated write must still
    # invalidate the cache and call on_added with the written component.
    subset.update(unit, 2, unit["components"][2])
    subset.cache = {"cached": True}
    before = len(subset.members.slots), subset.members.version
    subset.update(unit, 99, None)
    checks["matchingUnrelatedWrite"] = dict(
        onAddedCalls=len(log_added.events), onRemovedCalls=len(log_removed.events),
        cacheIsNone=subset.cache is None,
        memberPresent=7 in subset.members.indices,
        versionUnchanged=subset.members.version == before[1],
        callbackArg=(log_added.events[-1][1], log_added.events[-1][2]) if log_added.events else None)
    # Non-matching unit, untracked write: canonical still clears the cache and never calls on_removed
    # (remove is a no-op), so a reintroduced early-out is caught here too.
    absent = {"id": 8, "components": {2: None, 12: object(), 28: object()}}
    subset.cache = {"cached": True}
    removed_before = len(log_removed.events)
    subset.update(absent, 99, None)
    checks["nonMatchingUnrelatedWrite"] = dict(
        onRemovedCalls=len(log_removed.events) - removed_before, cacheIsNone=subset.cache is None,
        memberPresent=8 in subset.members.indices)
    # A tracked write must still work normally.
    absent["components"][2] = object()
    added_before = len(log_added.events)
    subset.update(absent, 2, absent["components"][2])
    checks["trackedWrite"] = dict(onAddedDelta=len(log_added.events) - added_before,
                                  cacheIsNone=subset.cache is None, memberPresent=8 in subset.members.indices)
    checks["ok"] = (checks["matchingUnrelatedWrite"] == dict(
        onAddedCalls=2, onRemovedCalls=0, cacheIsNone=True, memberPresent=True, versionUnchanged=True,
        callbackArg=(99, None))
        and checks["nonMatchingUnrelatedWrite"] == dict(onRemovedCalls=0, cacheIsNone=True, memberPresent=False)
        and checks["trackedWrite"] == dict(onAddedDelta=1, cacheIsNone=True, memberPresent=True))
    return checks


def real_animation_tables():
    from combat_runtime_data import load_data
    clips = load_data("animation-resources.json")["resources"]
    tables, rows = [], 0
    for name in ("chara", "monster"):
        source = clips[name]
        table = [None] * (max(r["id"] for r in source) + 1)
        for row in source:
            table[row["id"]] = dict(frame=0, max_frame=row["maxFrame"])
        rows += sum(r is not None for r in table)
        tables.append(table)
    return tables, rows


def check_animation_frame_laziness(fast, canonical):
    """Deferred clip loop must materialise the exact canonical frames and gate on auto_animation."""
    import combat_animation
    state = dict(enabled=True, auto_animation=False, common_frames_update=True)
    tables, rows = real_animation_tables()
    ticks = 7000
    canonical_tables, _ = real_animation_tables()
    for _ in range(ticks):
        canonical((), None, canonical_tables, state, None)
    for _ in range(ticks):
        combat_animation.update_animations((), None, tables, state, None)
    lazy_before = [(r["frame"] if r is not None else None) for table in tables for r in table]
    owed = fast.pending_resource_advances(tables)
    fast.materialize_resource_frames(tables)
    lazy_after = [(r["frame"] if r is not None else None) for table in tables for r in table]
    expect = [(r["frame"] if r is not None else None) for table in canonical_tables for r in table]
    # auto_animation=True must not owe advances (the canonical loop leaves frames alone).
    auto_table, _ = real_animation_tables()
    auto_state = dict(enabled=True, auto_animation=True, common_frames_update=True)
    for _ in range(50):
        combat_animation.update_animations((), None, auto_table, auto_state, None)
    auto_owed = fast.pending_resource_advances(auto_table)
    return dict(rows=rows, ticks=ticks, owedAdvances=owed, framesUntouchedBeforeMaterialise=all(v == 0 for v in lazy_before),
                materialisedMatchesCanonical=lazy_after == expect, autoAnimationOwedAdvances=auto_owed,
                distinctMaxFrames=len({r["max_frame"] for table in tables for r in table if r is not None}),
                sample=lazy_after[:3], ok=bool(owed == ticks and lazy_after == expect and auto_owed == 0
                                               and all(v == 0 for v in lazy_before)))


def install_opponent_probe(fast, stats):
    from combat_shared_controllers import SharedControllers
    patched = SharedControllers.opponent_in_range

    def probe(self, i, s):
        result = patched(self, i, s)
        cache = getattr(self, "_loop_fast_opponents", None)
        if cache is None:
            stats["misses"] += 1
            return result
        cached = cache[self.specs[i]["team"]]
        live = [t for t in self.units if self.specs[t]["team"] != self.specs[i]["team"]]
        stats["calls"] += 1
        if cached != live:
            stats["mismatches"] += 1
            if len(stats["details"]) < 5:
                stats["details"].append(dict(target=i, cached=list(cached), live=list(live)))
        return result

    SharedControllers.opponent_in_range = probe
    return patched


def run_worker(mode, golden_path):
    from strategy_optimizer_adapter import default_scenario, provenance
    from combat_sandbox import run_scenario
    import strategy_optimizer_loop_fast as fast

    import combat_animation
    canonical_update_animations = combat_animation.update_animations
    provenance_digest = provenance()["digest"]
    engine_digest = provenance()["files"]["tools/recovery/combat_entities.py"]
    base = default_scenario()
    regression = {}
    if mode == "variant":
        fast.install()
        assert fast.installed()
        regression["unrelatedWrite"] = check_component_subset_unrelated_write()
        regression["animationFrames"] = check_animation_frame_laziness(fast, canonical_update_animations)
    try:
        if mode == "variant":
            stats = dict(calls=0, mismatches=0, misses=0, details=[])
            patched = install_opponent_probe(fast, stats)
            fast.instrument_calls()
            probe_report = run_scenario(dict(base, **PROBE_CASE), include_trace=True)
            counts = fast.call_counts()
            fast.restore_instrumentation()
            from combat_shared_controllers import SharedControllers
            SharedControllers.opponent_in_range = patched
            regression["opponentCache"] = stats
            regression["callCounts"] = counts
            regression["probeDigest"] = digest_of(probe_report)
        for _ in range(WARMUP_RUNS):
            run_scenario(dict(base, **WARMUP_CASE), include_trace=True)
        records = []
        for case in cases():
            started = time.perf_counter()
            report = run_scenario(dict(base, **case), include_trace=True)
            seconds = time.perf_counter() - started
            records.append(dict(case=case, seconds=seconds, digest=digest_of(report)))
    finally:
        if mode == "variant":
            fast.uninstall()
            assert not fast.installed()
    return dict(mode=mode, runtime=platform.python_implementation() + " " + platform.python_version(),
                provenanceDigest=provenance_digest, engineDigest=engine_digest, runs=records,
                regression=regression)


def worker_main(args):
    payload = run_worker(args.mode, args.golden)
    args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(dict(mode=args.mode, runtime=payload["runtime"], runs=len(payload["runs"]))))
    return 0


def spawn(mode, golden, output):
    command = [sys.executable, str(Path(__file__).resolve()), "--worker", "--mode", mode,
               "--golden", str(golden), "--output", str(output)]
    print("running %s worker: %s" % (mode, " ".join(command)), flush=True)
    subprocess.run(command, check=True, cwd=str(HERE))


def summarize(records, golden):
    seconds = [row["seconds"] for row in records]
    hottest = sorted(zip(seconds, (row["case"]["tickLimit"] for row in records)))[-5:]
    return dict(runs=len(records), totalSeconds=sum(seconds), meanSeconds=sum(seconds) / len(records),
                minSeconds=min(seconds), maxSeconds=max(seconds),
                meanBattlesPerSecond=len(records) / sum(seconds),
                tailSeconds=[round(value, 4) for value, _ in hottest],
                tailTicks=[ticks for _, ticks in hottest],
                perCase=[dict(case=row["case"], seconds=round(row["seconds"], 4),
                              digest=row["digest"], matched=row["digest"] == golden[i]["digest"])
                         for i, row in enumerate(records)])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--mode", choices=("baseline", "variant"))
    parser.add_argument("--golden", type=Path, default=HERE / "strategy-optimizer-parity.json")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--report", type=Path, default=HERE / "strategy-optimizer-loop-fast-report.json")
    args = parser.parse_args()
    if args.worker:
        return worker_main(args)

    golden = json.loads(args.golden.read_text(encoding="utf-8"))["runs"]
    assert len(golden) == len(cases()), "golden case list does not match this checker"
    outputs = {mode: HERE / ("loop-fast-%s.json" % mode) for mode in ("baseline", "variant")}
    for mode in ("baseline", "variant"):
        spawn(mode, args.golden, outputs[mode])
    payloads = {mode: json.loads(path.read_text(encoding="utf-8")) for mode, path in outputs.items()}

    checks = {}
    for mode, payload in payloads.items():
        checks[mode] = dict(runtime=payload["runtime"], provenanceDigest=payload["provenanceDigest"],
                            engineDigest=payload["engineDigest"],
                            allMatched=all(row["digest"] == golden[i]["digest"]
                                           for i, row in enumerate(payload["runs"])))
    checks["sameEngineSource"] = payloads["baseline"]["engineDigest"] == payloads["variant"]["engineDigest"]
    checks["sameProvenance"] = payloads["baseline"]["provenanceDigest"] == payloads["variant"]["provenanceDigest"]
    checks["sameRuntime"] = payloads["baseline"]["runtime"] == payloads["variant"]["runtime"]

    regression = payloads["variant"]["regression"]
    checks["probeRunUnchanged"] = regression["probeDigest"] == golden[cases().index(PROBE_CASE)]["digest"]
    checks["unrelatedWriteCanonical"] = regression["unrelatedWrite"]["ok"]
    checks["animationFramesExact"] = regression["animationFrames"]["ok"]
    checks["opponentCacheMatchesLiveRoster"] = (regression["opponentCache"]["calls"] > 0
                                                and regression["opponentCache"]["mismatches"] == 0
                                                and regression["opponentCache"]["misses"] == 0)
    missing_counts = {label: regression["callCounts"].get(label, 0) for label in REQUIRED_COUNTS
                      if regression["callCounts"].get(label, 0) <= 0}
    checks["everyPatchReached"] = not missing_counts
    not_reached = [label for label in OPTIONAL_COUNTS if regression["callCounts"].get(label, 0) <= 0]

    summary = {mode: summarize(payload["runs"], golden) for mode, payload in payloads.items()}
    speedup = summary["baseline"]["meanSeconds"] / summary["variant"]["meanSeconds"]
    report = dict(
        runtime=payloads["baseline"]["runtime"],
        method=("warm serial, unprofiled: 3 warmup runs per worker then one run per case; "
                "baseline and variant are separate processes of the same interpreter"),
        checks=checks,
        regressions=regression,
        missingCounts=missing_counts,
        notReachedInCorpus=not_reached,
        target=dict(requestedBattlesPerSecond=200.0,
                    variantMeanBattlesPerSecond=summary["variant"]["meanBattlesPerSecond"],
                    meetsTarget=summary["variant"]["meanBattlesPerSecond"] >= 200.0,
                    feasibility="undetermined"),
        baseline=summary["baseline"], variant=summary["variant"], meanSpeedup=speedup)
    assert checks["baseline"]["allMatched"], "unpatched engine no longer matches the golden digests"
    assert checks["variant"]["allMatched"], "accelerated engine changed at least one canonical report"
    assert checks["sameEngineSource"] and checks["sameProvenance"], "canonical sources moved between workers"
    assert checks["probeRunUnchanged"], "the instrumented probe run changed the canonical report"
    assert checks["unrelatedWriteCanonical"], "ComponentSubset.update lost canonical callback/cache semantics"
    assert checks["animationFramesExact"], "deferred clip frames do not materialise to the canonical values"
    assert checks["opponentCacheMatchesLiveRoster"], "cached opponent roster diverged from the live roster"
    assert checks["everyPatchReached"], "a patch was installed but never reached: %s" % missing_counts
    args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(dict(matched=True, runtime=report["runtime"],
                          baselineBps=round(summary["baseline"]["meanBattlesPerSecond"], 3),
                          variantBps=round(summary["variant"]["meanBattlesPerSecond"], 3),
                          speedup=round(speedup, 4), meetsTarget=report["target"]["meetsTarget"],
                          report=str(args.report)), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
