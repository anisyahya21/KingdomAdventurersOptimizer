"""Owned measurement probe: what a 200 battles/s target actually requires (21 September 2026).

This file owns no engine code. It measures the headless LOOP on the dedicated PyPy runtime and
states the target gap arithmetically instead of by assertion:

  * how many Python-level calls the canonical engine makes per tick,
  * how much the deferred clip-frame loop actually removes (a bounded micro-measurement),
  * what per-tick budget 200 complete battles/s leaves, at the measured warm-JIT call cost.

Run:
  $LOCALAPPDATA/KingdomAdventurersOptimizer/runtime/pypy3.11-v7.3.23-win64/pypy3.exe \
      strategy_optimizer_native_probe.py --report strategy-optimizer-native-probe.json
"""
import argparse
import json
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Call count per tick from optimizer-headless-profile.txt (cProfile over one 1000-tick run:
# 18,857,871 calls). Used as the canonical call-density reference; the probe re-measures the hot
# accessors live so the reference is not the only witness.
PROFILE_CALLS_PER_TICK = 18_857_871 / 1000.0
TARGET_BATTLES_PER_SECOND = 200.0
FULL_BATTLE_TICKS = 7000


def clip_loop_cost(ticks=FULL_BATTLE_TICKS):
    """Canonical per-tick clip loop vs the deferred representation, warm and unprofiled."""
    import combat_animation
    import strategy_optimizer_loop_fast as fast
    from check_strategy_optimizer_loop_fast import real_animation_tables
    canonical = combat_animation.update_animations
    state = dict(enabled=True, auto_animation=False, common_frames_update=True)
    tables, rows = real_animation_tables()
    for _ in range(3):
        canonical((), None, tables, state, None)
    started = time.perf_counter()
    for _ in range(ticks):
        canonical((), None, tables, state, None)
    canonical_seconds = time.perf_counter() - started
    fast.install()
    patched = combat_animation.update_animations
    for _ in range(3):
        patched((), None, tables, state, None)
    started = time.perf_counter()
    for _ in range(ticks):
        patched((), None, tables, state, None)
    deferred_seconds = time.perf_counter() - started
    fast.uninstall()
    updates = rows * ticks
    return dict(rows=rows, ticks=ticks, rowUpdates=updates,
                canonicalSeconds=round(canonical_seconds, 4), deferredSeconds=round(deferred_seconds, 4),
                savedSeconds=round(canonical_seconds - deferred_seconds, 4),
                nanosecondsPerRowUpdate=round(canonical_seconds / updates * 1e9, 2))


def live_call_density(ticks=100):
    """Instrumented hot-accessor calls per tick on a real run (counts only, no timing claim)."""
    import strategy_optimizer_loop_fast as fast
    from combat_sandbox import run_scenario
    from strategy_optimizer_adapter import default_scenario
    fast.install()
    fast.instrument_calls()
    try:
        run_scenario(dict(default_scenario(), encounterId=19, tickLimit=ticks, mathSeed=7, libSeed=8),
                     include_trace=True)
        counts = fast.call_counts()
    finally:
        fast.restore_instrumentation()
        fast.uninstall()
    total = sum(counts.values())
    return dict(ticks=ticks, instrumentedCalls=total, callsPerTick=round(total / ticks, 1), counts=counts)


def warm_battle_seconds(ticks=FULL_BATTLE_TICKS, repeats=3):
    """Warm unprofiled wall-clock for one full-length battle with the accelerator installed."""
    import strategy_optimizer_loop_fast as fast
    from combat_sandbox import run_scenario
    from strategy_optimizer_adapter import default_scenario
    base = default_scenario()
    case = dict(encounterId=19, tickLimit=ticks, mathSeed=7, libSeed=8)
    fast.install()
    samples = []
    try:
        for _ in range(repeats):
            started = time.perf_counter()
            run_scenario(dict(base, **case), include_trace=True)
            samples.append(time.perf_counter() - started)
    finally:
        fast.uninstall()
    return min(samples)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, default=HERE / "strategy-optimizer-native-probe.json")
    args = parser.parse_args()

    clip = clip_loop_cost()
    density = live_call_density()
    battle_seconds = warm_battle_seconds()
    required_tick_seconds = 1.0 / (TARGET_BATTLES_PER_SECOND * FULL_BATTLE_TICKS)
    seconds_per_row_update = clip["canonicalSeconds"] / clip["rowUpdates"]
    # frame_resource -> i32 + signed_remainder -> 2x abs + 2x i32, i.e. ~6 Python-level calls per row
    # update (source-read, not guessed): 0.0417 s / 2.07M updates / 6 = ~3.3 ns per warm-JIT call.
    CALLS_PER_ROW_UPDATE = 6
    seconds_per_call = seconds_per_row_update / CALLS_PER_ROW_UPDATE
    report = dict(
        runtime="see caller",
        clipLoop=clip,
        callDensity=density,
        warmBattleSeconds=round(battle_seconds, 4),
        ticksPerSecond=round(FULL_BATTLE_TICKS / battle_seconds, 1),
        target=dict(battlesPerSecond=TARGET_BATTLES_PER_SECOND, battleTicks=FULL_BATTLE_TICKS,
                    requiredTickSeconds=round(required_tick_seconds, 9),
                    measuredTickSeconds=round(battle_seconds / FULL_BATTLE_TICKS, 9),
                    gapFactor=round((battle_seconds / FULL_BATTLE_TICKS) / required_tick_seconds, 1)),
        accounting=dict(profileCallsPerTick=round(PROFILE_CALLS_PER_TICK, 1),
                        measuredWarmSecondsPerRowUpdate=round(seconds_per_row_update, 10),
                        callsPerRowUpdate=CALLS_PER_ROW_UPDATE,
                        measuredWarmSecondsPerPythonCall=round(seconds_per_call, 11),
                        pythonCallsPerTickBudget=round(required_tick_seconds / seconds_per_call, 1),
                        callReductionFactor=round(PROFILE_CALLS_PER_TICK / (required_tick_seconds / seconds_per_call), 1)),
        note=("The target needs the whole per-tick state transition inside a compiled typed kernel: the "
              "budget is far below the canonical Python-level call density, so no loop-level patch can "
              "close it. Feasibility of that kernel is NOT measured here - no compiler was installed and "
              "this sandbox has no network, so that route is 'not attempted', not 'impossible'."))
    args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "callDensity"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
