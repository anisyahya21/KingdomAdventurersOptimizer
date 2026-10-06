"""A/B/C throughput for the MP telemetry + live Holy Herb policy, measured rather than estimated.

    A   the previous production kernel (`ka_kernel_v8.dll`) on the same battles - the baseline
    A2  the new kernel on the same battles with *nothing declared* - the production fast path
    B   the new kernel with the trigger units declared and the policy disabled (`holyHerbMaxUses=0`),
        so the per-tick MP sample runs but no charge can ever be spent
    C   the new kernel with the policy enabled (`holyHerbStock=holyHerbMaxUses=2`) - the fixture the
        policy tests use, declared here as test configuration, not as a production value

The baseline is the real old binary, not a re-implementation: `ka_kernel_v8.dll` is loaded with a
local copy of its own report layout (the v9 struct is 336 bytes longer) through the same snapshot
installer, so A and A2/B/C run identical battles and differ only in the kernel and the declaration.

A2 is the number that answers "did the new observer slow normal battles down"; B isolates the
per-tick MP sample from the policy; C adds the policy's own dispatches.

Two workloads are measured, because they answer different questions:

  * `normal`   - the seven frozen whole-fight cases: fights in which the policy almost never fires.
  * `policy`   - a DPS whose MP pool is lowered (a scenario parameter) so the pool really drains and
                 the policy really dispatches, which is where C can differ from B.

    python bench_optimizer_herb.py [--repetitions N] [--json OUT]
"""
import argparse
import ctypes
import json
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import ka_abi  # noqa: E402
import search_contract as contract  # noqa: E402
import strategy_optimizer_adapter as adapter  # noqa: E402
import strategy_optimizer_native as native  # noqa: E402
from check_native_real_state import CASES, Checkpoints  # noqa: E402
from combat_animation_selection import HUMAN_BASES  # noqa: E402
from combat_shared_controllers import ROWS  # noqa: E402

V8_DLL = HERE / 'native/ka_kernel/target/release/ka_kernel_v8.dll'
#: TEST FIXTURE: the declared stock and cap for the policy workload (never a game quantity).
POLICY_FIXTURE = dict(stock=2, maxUses=2, drainingMp=200, ampleMp=120)

#: The report layout `ka_kernel_v8.dll` actually writes: the v9 fields minus the 18 this change added.
V8_REPORT_FIELDS = [
    ('status', ctypes.c_int32), ('ticks', ctypes.c_int32), ('verdict', ctypes.c_int32),
    ('verdict_tick', ctypes.c_int32), ('battle_state', ctypes.c_int32),
    ('battle_frame', ctypes.c_int32), ('finish_policy', ctypes.c_int32),
    ('ending_gate_tick', ctypes.c_int32), ('ending_confirmed', ctypes.c_int32),
    ('ending_counter', ctypes.c_int32), ('prize_callbacks', ctypes.c_int32),
    ('pre_verdict_prize_callbacks', ctypes.c_int32), ('unresolved_commands', ctypes.c_int32),
    ('pending_projectiles', ctypes.c_int32), ('active_damage_or_leaving', ctypes.c_int32),
    ('math_draws', ctypes.c_int32), ('lib_draws', ctypes.c_int32),
    ('certificate_held', ctypes.c_int32), ('certificate_frame', ctypes.c_int32),
    ('certificate_pending', ctypes.c_int32), ('late_hold_frame', ctypes.c_int32),
    ('pending_at_verdict', ctypes.c_int32), ('pending_final', ctypes.c_int32),
    ('post_certificate_delta', ctypes.c_int32), ('observations', ctypes.c_int32),
    ('verdict_observations', ctypes.c_int32), ('clauses', ctypes.c_int32 * 4),
    ('boss_hp', ctypes.c_int32), ('boss_state', ctypes.c_int32),
    ('stored_target_holders', ctypes.c_int32), ('queued_command_holders', ctypes.c_int32),
    ('scope_allowed', ctypes.c_int32), ('heals', ctypes.c_int32), ('attack_attempts', ctypes.c_int32),
    ('survivors', ctypes.c_int32), ('own_hp', ctypes.c_int64), ('own_hp_max', ctypes.c_int64),
    ('own_present', ctypes.c_int32), ('resource_uses', ctypes.c_int32),
    ('progress_boss', ctypes.c_int32), ('progress_boss_death_tick', ctypes.c_int32),
    ('progress_boss_leaving_tick', ctypes.c_int32), ('progress_stored_at_death', ctypes.c_int32),
    ('progress_stored_targeting_boss_at_death', ctypes.c_int32),
    ('progress_stored_target_holders_at_death', ctypes.c_int32),
    ('progress_commands_targeting_boss', ctypes.c_int32),
    ('progress_commands_targeting_boss_released', ctypes.c_int32),
    ('progress_max_stored', ctypes.c_int32), ('progress_max_targeting_boss', ctypes.c_int32),
    ('progress_target_holders_peak', ctypes.c_int32),
    ('progress_post_death_reentries', ctypes.c_int32),
    ('progress_post_death_leavings', ctypes.c_int32), ('progress_post_death_prizes', ctypes.c_int32),
    ('progress_released_after_death', ctypes.c_int32),
    ('progress_released_after_death_targeting_boss', ctypes.c_int32),
    ('progress_first_release_after_death', ctypes.c_int32),
    ('progress_last_release_after_death', ctypes.c_int32)]


class _Stub:
    """A placeholder for the entry points v9 added, so `ka_abi.load` can bind the v8 binary."""

    def __init__(self, value=0):
        self.argtypes = None
        self.restype = ctypes.c_int32
        self._value = value

    def __call__(self, *args, **kwargs):
        return self._value


def load_v8():
    """The previous production kernel, with its own report layout, through the same installer."""
    if not V8_DLL.is_file():
        return None
    mirror = type('KaBattleReportV8', (ctypes.Structure,), {'_fields_': V8_REPORT_FIELDS})
    stubs = {'ka_battle_set_mp_watch': _Stub(0), 'ka_battle_mp_config': _Stub(-1),
             'ka_battle_mp': _Stub(-1), 'ka_sizeof_report': _Stub(ctypes.sizeof(mirror))}
    real_cdll, real_report = ctypes.CDLL, ka_abi.KaBattleReport

    def patched(path, *args, **kwargs):
        library = real_cdll(path, *args, **kwargs)
        for name, stub in stubs.items():
            try:
                getattr(library, name)
            except AttributeError:
                setattr(library, name, stub)
        return library

    ctypes.CDLL = patched
    ka_abi.KaBattleReport = mirror
    try:
        library = ka_abi.load(V8_DLL)
    finally:
        ctypes.CDLL = real_cdll
        ka_abi.KaBattleReport = real_report
    return dict(library=library, report=mirror)


def snapshot_of(scenario):
    """The production pre-battle snapshot, so every configuration installs the same battle."""
    checkpoints = Checkpoints([0])
    from combat_sandbox import run_scenario
    run_scenario(scenario, checkpoints=checkpoints, stop_tick=0)
    engine = checkpoints.stored[0]['engine']
    consumables, _slot, _reason = ka_abi.consumables_from_scenario(scenario)
    names = [str(name) for name in scenario.get('holyHerbTriggerUnits') or ()]
    consumables['mp_watch'] = [engine.names[name] for name in names]
    return ka_abi.engine_snapshot(engine, ROWS, int(engine.battle_state), HUMAN_BASES, consumables)


def measure(runs, repeat, order_offset=0):
    """Per-battle seconds for one configuration, as a distribution over repetitions.

    Each repetition times the whole configuration as one block, and the blocks are *rotated* so no
    configuration is always measured first: the machine's own drift over a run must not be read as a
    difference between the kernels. Both the median and the best (least interfered) block are
    reported, and the caller measures the same binary twice to state the noise floor of the method.
    """
    samples = []
    for repetition in range(repeat):
        started = time.perf_counter()
        for index in range(len(runs)):
            runs[(index + order_offset + repetition) % len(runs)]()
        samples.append((time.perf_counter() - started)/len(runs))
    samples.sort()
    best, median = samples[0], statistics.median(samples)
    return dict(seconds=median, bestSeconds=best,
                rate=(1.0/best if best else 0.0), medianRate=(1.0/median if median else 0.0),
                lowSeconds=best, highSeconds=samples[-1], runs=repeat*len(runs),
                deciles=[round(value, 6) for value in samples[::max(1, len(samples)//10)]])


def declared_policy(scenario):
    """The finish policy the scenario declares, as the kernel's own code (`2` = on-verdict).

    Every configuration must run the *same* finish rule, or the comparison measures the horizon rather
    than the kernel: running one configuration at-horizon and the next on-verdict is exactly the
    mistake that made the old compact-parity checker disagree with the canonical engine.
    """
    return 2 if scenario.get('finishPolicy') == 'on-verdict' else 0


def v8_runner(library_bundle, snapshot, seeds, ticks):
    library, report_type = library_bundle['library'], library_bundle['report']
    template = ka_abi.load_snapshot(library, snapshot)

    def run(policy=0):
        clone = library.ka_battle_clone(template)
        try:
            library.ka_battle_seed_rng(clone, seeds[0], seeds[1])
            report = report_type()
            status = library.ka_run_battle(clone, ticks - 1, policy, ctypes.byref(report))
            return (status, report.verdict, report.ticks)
        finally:
            library.ka_battle_free(clone)

    return run, template


def v9_runner(snapshot, seeds, ticks, policy):
    library = native.library()
    template = ka_abi.load_snapshot(library, snapshot)

    def run():
        clone = library.ka_battle_clone(template)
        try:
            library.ka_battle_seed_rng(clone, seeds[0], seeds[1])
            report = ka_abi.KaBattleReport()
            status = library.ka_run_battle(clone, ticks - 1, policy, ctypes.byref(report))
            return (status, report.verdict, report.ticks)
        finally:
            library.ka_battle_free(clone)

    return run, template


def normal_cases():
    """The seven frozen whole fights: real battles whose watched units rarely reach 3%."""
    base = adapter.default_scenario()
    out = []
    for case in CASES:
        scenario = dict(base, **case)
        out.append((f"enc{case['encounterId']} {case['tickLimit']}t "
                    f"seed{case['mathSeed']}/{case['libSeed']}",
                    scenario, (case['mathSeed'], case['libSeed']), case['tickLimit'], ()))
    return out


def policy_cases():
    """The same roster with a lowered DPS pool, so the policy really dispatches (test fixture)."""
    base = adapter.default_scenario()
    humans = [unit['name'] for unit in base['ownUnits'] if unit['human']]
    out = []
    for offset in (0, 1, 2):
        scenario = json.loads(json.dumps(base))
        scenario['tickLimit'] = 20000
        scenario['mathSeed'] = 7 + offset*7919
        scenario['libSeed'] = 8 + offset*104729
        dps = next(unit for unit in scenario['ownUnits'] if unit['name'] == humans[0])
        contract.set_effective_parameter(scenario, dps, 11, POLICY_FIXTURE['drainingMp'])
        scenario = adapter.validate_scenario(scenario)
        out.append((f'drained-MP DPS seed{offset}', scenario,
                    (scenario['mathSeed'], scenario['libSeed']), scenario['tickLimit'],
                    humans[:2]))
    return out


def noop_cases():
    """The same battles with watched units that never reach the threshold.

    The two rear fodder are declared as the trigger units: their MP is never spent, so the observer
    runs its full work for both units on every tick while the latch can never fire. C and B therefore
    run *literally* the same fight - same ticks, same events - which is what isolates the policy's own
    per-tick work from the fight a successful dispatch changes.
    """
    base = adapter.default_scenario()
    humans = [unit['name'] for unit in base['ownUnits'] if unit['human']]
    out = []
    for offset in (0, 1, 2):
        scenario = json.loads(json.dumps(base))
        scenario['tickLimit'] = 20000
        scenario['mathSeed'] = 7 + offset*7919
        scenario['libSeed'] = 8 + offset*104729
        scenario = adapter.validate_scenario(scenario)
        out.append((f'fodder watch seed{offset}', scenario,
                    (scenario['mathSeed'], scenario['libSeed']), scenario['tickLimit'],
                    humans[2:4]))
    return out


def declare(scenario, stock, max_uses, with_watch=True, triggers=()):
    out = json.loads(json.dumps(scenario))
    if not with_watch:
        out['holyHerbStock'] = stock
        out['holyHerbMaxUses'] = max_uses
        out.pop('holyHerbTriggerUnits', None)
        return adapter.validate_scenario(out)
    humans = list(triggers) or [unit['name'] for unit in out['ownUnits'] if unit['human']][:2]
    out['holyHerbStock'] = stock
    out['holyHerbMaxUses'] = max_uses
    out['holyHerbTriggerUnits'] = humans
    return adapter.validate_scenario(out)


def configs(scenario, library_v8, triggers=()):
    """The four measured configurations, all on the identical battle."""
    plain = declare(scenario, stock=0, max_uses=0, with_watch=False)
    watched = declare(scenario, stock=POLICY_FIXTURE['stock'],
                      max_uses=POLICY_FIXTURE['maxUses'], triggers=triggers)
    disabled = declare(scenario, stock=POLICY_FIXTURE['stock'], max_uses=0, triggers=triggers)
    out = {}
    if library_v8 is not None:
        snapshot = snapshot_of(plain)
        seeds = (scenario['mathSeed'], scenario['libSeed'])
        run, _ = v8_runner(library_v8, snapshot, seeds, scenario['tickLimit'])
        v8_policy = declared_policy(plain)
        out['A  previous kernel (v8), nothing declared'] = \
            lambda run=run, policy=v8_policy: run(policy)
        # The same binary, measured a second time under its own label: the difference between the two
        # readings is this machine's noise floor for this method, so a smaller A/B difference than
        # the A/A difference is not a result.
        repeat_run, _ = v8_runner(library_v8, snapshot, seeds, scenario['tickLimit'])
        out["A' previous kernel (v8), repeated control"] = \
            lambda run=repeat_run, policy=v8_policy: run(policy)
    for label, declared in (
            ('A2 new kernel, nothing declared', plain),
            ('B  new kernel, policy disabled (maxUses 0)', disabled),
            ('C  new kernel, policy enabled (stock 2 / maxUses 2)', watched)):
        snapshot = snapshot_of(declared)
        seeds = (declared['mathSeed'], declared['libSeed'])
        ticks = declared['tickLimit']
        run, _ = v9_runner(snapshot, seeds, ticks, declared_policy(declared))
        out[label] = run
    return out


def run_workload(name, cases, library_v8, repeat):
    print(f'workload {name}: {len(cases)} battles x {repeat} repetitions')
    rows = {}
    for label, scenario, seeds, ticks, triggers in cases:
        runs = configs(scenario, library_v8, triggers)
        for config, run in runs.items():
            rows.setdefault(config, []).append(run)
    baseline = None
    order = ['A  previous kernel (v8), nothing declared',
             "A' previous kernel (v8), repeated control", 'A2 new kernel, nothing declared',
             'B  new kernel, policy disabled (maxUses 0)',
             'C  new kernel, policy enabled (stock 2 / maxUses 2)']
    report = []
    for position, config in enumerate(order):
        if config not in rows:
            continue
        runs = rows[config]
        measure([runs[0]], 3)  # warm-up: allocator/page-in effects, discarded
        measured = measure(runs, repeat, order_offset=position)
        # The battle each configuration actually ran: C changes the fight, so the per-tick cost is the
        # only fair comparison between it and A/B.
        fired = [run() for run in runs]
        mean_ticks = statistics.mean(result[2] for result in fired)
        rate = measured['rate']
        if baseline is None:
            baseline = rate
        report.append(dict(config=config, battlesPerSecond=round(rate, 3),
                           medianBattlesPerSecond=round(measured['medianRate'], 3),
                           bestSecondsPerBattle=round(measured['bestSeconds'], 6),
                           medianSecondsPerBattle=round(measured['seconds'], 6),
                           secondsPerTick=round(measured['bestSeconds']/mean_ticks, 8),
                           slowdownPercent=round((baseline/rate - 1)*100, 2) if rate else None,
                           spreadSeconds=[round(measured['lowSeconds'], 6),
                                          round(measured['highSeconds'], 6)],
                           deciles=measured['deciles'],
                           meanTicks=round(mean_ticks, 1), runs=measured['runs'],
                           sampleOutcomes=[list(result) for result in fired[:3]]))
    noise = None
    control = next((row for row in report if row['config'].startswith("A'")), None)
    if control is not None:
        noise = round(abs(control['battlesPerSecond']/report[0]['battlesPerSecond'] - 1)*100, 2)
    for row in report:
        print(f"  {row['config']:<52} {row['battlesPerSecond']:>8.2f} battles/s  "
              f"{row['slowdownPercent']:>6.2f}% vs A  "
              f"ticks {row['meanTicks']:>8.1f}  {row['secondsPerTick']*1e6:>8.2f} us/tick")
    if noise is not None:
        print(f"  noise floor (A measured twice): {noise:.2f}%")
    return dict(workload=name, cases=len(cases), repetitions=repeat, noiseFloorPercent=noise,
                rows=report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repetitions', type=int, default=25)
    parser.add_argument('--json', type=Path)
    args = parser.parse_args()
    library_v8 = load_v8()
    print(f'kernel A : {V8_DLL if library_v8 else "unavailable - A omitted"}')
    print(f'kernel A2: {ka_abi.DLL}')
    if library_v8:
        print(f'  v8 report bytes {ctypes.sizeof(library_v8["report"])}, '
              f'v9 report bytes {ctypes.sizeof(ka_abi.KaBattleReport)}')
    results = [run_workload('normal', normal_cases(), library_v8, args.repetitions),
               run_workload('policy-noop', noop_cases(), library_v8, max(5, args.repetitions//2)),
               run_workload('policy', policy_cases(), library_v8, max(5, args.repetitions//2))]
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(dict(kernelA=str(V8_DLL) if library_v8 else None,
                                             kernelA2=str(ka_abi.DLL), results=results), indent=1),
                             encoding='utf-8')
    return 0


if __name__ == '__main__':
    sys.exit(main())
