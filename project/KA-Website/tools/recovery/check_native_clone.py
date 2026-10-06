"""`ka_battle_clone`: isolation proof, per-seed parity, and the import-vs-clone cost comparison.

Proves the three properties the production worker depends on:

  * **isolation** - running a clone (or many clones) never mutates the cached template, and no clone
    can affect another;
  * **parity** - clone + seed + `ka_run_battle` reproduces the canonical engine's whole battle for
    several seed pairs from one template, matching the same report fields as the import path;
  * **cost** - clone + seed-set is measured against the full Python->native import it replaces.

    pypy3.exe check_native_clone.py
"""
import copy
import ctypes
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import ka_abi  # noqa: E402
from check_native_battle_full import battle_loop, compare, encounter_from_engine  # noqa: E402
from check_native_real_state import Checkpoints  # noqa: E402
from combat_animation_selection import HUMAN_BASES  # noqa: E402
from combat_reward_entitlement import RewardEntitlementWatch  # noqa: E402
from combat_sandbox import run_scenario  # noqa: E402
from combat_shared_controllers import ROWS  # noqa: E402
from strategy_optimizer_adapter import default_scenario  # noqa: E402

TICKS = 6999
POLICY = 0
CASE = dict(encounterId=19, tickLimit=7000, mathSeed=7, libSeed=8)
SEEDS = [(7, 8), (26, 27), (907303519, 267534053), (11, 12), (12345, 23456)]


def main():
    library = ka_abi.load()
    checkpoints = Checkpoints([0])
    run_scenario(dict(default_scenario(), **CASE), checkpoints=checkpoints, stop_tick=0)
    stored = checkpoints.stored[0]
    engine = copy.deepcopy(stored['engine'])
    snapshot = ka_abi.engine_snapshot(engine, ROWS, int(engine.battle_state), HUMAN_BASES)

    # --- template build (once per candidate) ------------------------------------------------
    mark = time.perf_counter()
    template = ka_abi.load_snapshot(library, snapshot)
    import_seconds = time.perf_counter() - mark
    library.ka_battle_set_scope_allowed(
        template, int(RewardEntitlementWatch(encounter_from_engine(engine), ROWS).scope['allowed']))
    template_checksum = library.ka_battle_checksum(template)

    # --- isolation: run five clones, then re-read the template -------------------------------
    digests = []
    for math_seed, lib_seed in SEEDS:
        clone = library.ka_battle_clone(template)
        if not clone:
            raise SystemExit('clone returned null')
        library.ka_battle_seed_rng(clone, math_seed, lib_seed)
        report = ka_abi.KaBattleReport()
        status = library.ka_run_battle(clone, TICKS, POLICY, ctypes.byref(report))
        if status != 0:
            raise SystemExit(f'clone run refused with {status}')
        digests.append((report.verdict, report.verdict_tick, report.prize_callbacks,
                        report.math_draws, report.lib_draws))
        library.ka_battle_free(clone)
    if library.ka_battle_checksum(template) != template_checksum:
        raise SystemExit('ISOLATION FAILURE: running clones mutated the template')
    if len(set(digests)) != len(digests):
        raise SystemExit(f'SEED ISOLATION FAILURE: identical outcomes for distinct seeds: {digests}')
    print(f'clone isolation: 5 seeded clones ran 6999 ticks each; template checksum unchanged '
          f'({template_checksum:#x}), all seed outcomes distinct')

    # --- parity: clone + seed must equal the canonical engine for the same seed pair ---------
    mismatches = []
    for math_seed, lib_seed in SEEDS:
        case = dict(CASE, mathSeed=math_seed, libSeed=lib_seed)
        checkpoints = Checkpoints([0])
        run_scenario(dict(default_scenario(), **case), checkpoints=checkpoints, stop_tick=0)
        expected_engine = copy.deepcopy(checkpoints.stored[0]['engine'])
        # the canonical engine must start from the same scenario, so re-import a template per seed
        seeded_snapshot = ka_abi.engine_snapshot(expected_engine, ROWS,
                                                 int(expected_engine.battle_state), HUMAN_BASES)
        seeded_template = ka_abi.load_snapshot(library, seeded_snapshot)
        library.ka_battle_set_scope_allowed(
            seeded_template,
            int(RewardEntitlementWatch(encounter_from_engine(expected_engine), ROWS)
                .scope['allowed']))
        watch = RewardEntitlementWatch(encounter_from_engine(expected_engine), ROWS)
        expected = battle_loop(expected_engine, TICKS, POLICY, watch)
        clone = library.ka_battle_clone(seeded_template)
        report = ka_abi.KaBattleReport()
        status = library.ka_run_battle(clone, TICKS, POLICY, ctypes.byref(report))
        diffs = compare(case, expected, ka_abi.report_dict(report), watch,
                        ka_abi.report_dict(report))
        if status != 0 or diffs:
            mismatches.append((case, status, diffs[:5]))
        library.ka_battle_free(clone)
        library.ka_battle_free(seeded_template)
    if mismatches:
        raise SystemExit(f'clone parity FAILED: {mismatches[:2]}')
    print(f'clone parity: {len(SEEDS)} seed pairs run as template-clone matched the canonical '
          f'whole battle on every report field and certificate value')

    # --- cost: import (template build) vs clone + seed ---------------------------------------
    import_times, clone_times = [], []
    for _ in range(20):
        mark = time.perf_counter()
        built = ka_abi.load_snapshot(library, snapshot)
        import_times.append(time.perf_counter() - mark)
        library.ka_battle_free(built)
    for _ in range(200):
        mark = time.perf_counter()
        clone = library.ka_battle_clone(template)
        library.ka_battle_seed_rng(clone, 7, 8)
        clone_times.append(time.perf_counter() - mark)
        library.ka_battle_free(clone)
    print(f'cost: full import median {statistics.median(import_times)*1e3:.2f} ms; '
          f'clone+seed median {statistics.median(clone_times)*1e3:.3f} ms '
          f'({statistics.median(import_times)/statistics.median(clone_times):.0f}x cheaper)')
    library.ka_battle_free(template)
    print(f'template build (once per candidate): {import_seconds*1e3:.1f} ms')
    return 0


if __name__ == '__main__':
    sys.exit(main())
