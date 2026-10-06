"""Parity + initialization-floor measurement for strategy_optimizer_setup_fast.

Run it with the dedicated runtime so the measured setup floor is the real one:

  $LOCALAPPDATA/KingdomAdventurersOptimizer/runtime/pypy3.11-v7.3.23-win64/pypy3.exe \
      check_strategy_optimizer_setup_fast.py --golden strategy-optimizer-parity.json \
      --report strategy-optimizer-setup-fast-report.json

The parent spawns child workers with the SAME interpreter - one unpatched (`baseline`) and one with
`strategy_optimizer_setup_fast.install()` (`variant`). Each worker warms up, then measures warm
unprofiled timings and compares the whole canonical report minus `manifest` (trace, RNG final state,
final state, receipts, metrics, setup...) against the golden digest fixture. The manifest payload
itself is compared field-for-field between the two workers, so the cached attestation is proven to
return the same metadata.

Correctness cases:
  * 22 golden fixtures = 20 encounters @ 1000 ticks + 2 full 7000-tick runs.
  * A/B/A over different seeds, builds, encounters and item stocks: A must repeat exactly, and must
    differ when the seed or the build changes (so nothing gameplay-level is memoised), with the
    caller's input dict proven unmutated (no state leak between runs).
  * tickLimit=0 is asserted ILLEGAL (`ScenarioError`); the initialization floor is measured on the
    legal 1 / 10 / 1000-tick runs.
  * direct A/B of `native_identity` / `run_manifest` medians, plus an in-process proof that
    `reference_run_manifest` (canonical) == the cached manifest, and a stat-keyed drift test.

Timing is warm and unprofiled for both processes; cProfile runs are collected separately and are
never compared to an unprofiled duration.
"""
import argparse
import hashlib
import json
import platform
import statistics
import subprocess
import sys
import time
from copy import deepcopy
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

WARMUP_RUNS = 3
WARMUP_CASE = dict(encounterId=5, tickLimit=1000, mathSeed=12, libSeed=13)
FLOOR_TICKS = (1, 10, 1000)
FLOOR_REPS = {1: 20, 10: 10, 1000: 4}
PROBE_REPS = 5


def golden_cases():
    rows = [dict(encounterId=i, tickLimit=1000, mathSeed=7 + i, libSeed=8 + i) for i in range(20)]
    rows += [dict(encounterId=19, tickLimit=7000, mathSeed=a, libSeed=b)
             for a, b in ((7, 8), (907303519, 267534053))]
    return rows


def ab_cases():
    """Different builds, encounters, item stocks and seeds for the A/B/A leakage test."""
    build_a = dict(encounterId=3, tickLimit=10, mathSeed=101, libSeed=202)
    build_b = dict(encounterId=11, tickLimit=10, mathSeed=303, libSeed=404,
                   itemStock={'medicine': 2}, holyHerbStock=3)
    return [dict(label='A', case=build_a), dict(label='B', case=build_b),
            dict(label='A-again', case=dict(build_a)),
            dict(label='A-other-seed', case=dict(build_a, mathSeed=999, libSeed=1000))]


def digest_of(report):
    # Manifest carries runtime/source identity only; every gameplay field stays in the digest.
    checked = {key: value for key, value in report.items() if key != 'manifest'}
    return hashlib.sha256(json.dumps(checked, sort_keys=True).encode()).hexdigest()


def run_worker(mode):
    from strategy_optimizer_adapter import default_scenario, provenance
    from combat_sandbox import run_scenario
    from combat_scenario import ScenarioError
    import combat_run_manifest as crm
    import combat_scenario
    import strategy_optimizer_setup_fast as fast

    prov = provenance()
    base = default_scenario()
    if mode == 'variant':
        fast.install()
        assert fast.installed()
    try:
        for _ in range(WARMUP_RUNS):
            run_scenario(dict(base, **WARMUP_CASE), include_trace=True)
        probe_scenario = combat_scenario.load_scenario(dict(base, **WARMUP_CASE))
        probe_contract = crm.support_contract(probe_scenario)

        def timed(case, trace=True):
            scenario = dict(base, **case)
            snapshot = deepcopy(scenario)
            started = time.perf_counter()
            report = run_scenario(scenario, include_trace=trace)
            seconds = time.perf_counter() - started
            manifest = report['manifest']
            return dict(case=case, seconds=seconds, digest=digest_of(report),
                        manifestDigest=hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest(),
                        nativeSha256Source=manifest['nativeSha256Source'],
                        inputUnchanged=scenario == snapshot)

        golden = [timed(case) for case in golden_cases()]
        ab = [dict(label=row['label'], **timed(row['case'])) for row in ab_cases()]
        a = {row['label']: row['digest'] for row in ab}
        assert a['A'] == a['A-again'], 'A/B/A repeated a fixed input but produced a different report'
        assert a['A'] != a['A-other-seed'], 'changing the seeds did not change the report (result memoised?)'
        assert a['A'] != a['B'], 'changing the build did not change the report (result memoised?)'

        floor = {}
        for ticks in FLOOR_TICKS:
            case = dict(WARMUP_CASE, tickLimit=ticks)
            samples = []
            for _ in range(FLOOR_REPS[ticks]):
                started = time.perf_counter()
                run_scenario(dict(base, **case), include_trace=False)
                samples.append(time.perf_counter() - started)
            floor[ticks] = dict(reps=FLOOR_REPS[ticks], medianMs=statistics.median(samples) * 1000,
                                minMs=min(samples) * 1000, meanSeconds=statistics.mean(samples))
        illegal_zero = False
        try:
            run_scenario(dict(base, **dict(WARMUP_CASE, tickLimit=0)), include_trace=False)
        except ScenarioError:
            illegal_zero = True

        def probe(fn):
            samples = []
            for _ in range(PROBE_REPS):
                started = time.perf_counter()
                fn()
                samples.append(time.perf_counter() - started)
            return statistics.median(samples) * 1000

        setup_probe = dict(nativeIdentityMs=probe(crm.native_identity),
                           runManifestMs=probe(lambda: crm.run_manifest(probe_scenario, probe_contract)))
        if mode == 'variant':
            first = dict(base, **WARMUP_CASE)
            equivalent = True
            for case in golden_cases()[:3]:
                loaded = combat_scenario.load_scenario(dict(first, **case))
                contract = crm.support_contract(loaded)
                equivalent = equivalent and (fast.reference_run_manifest(loaded, contract)
                                             == crm.run_manifest(loaded, contract))
            setup_probe['canonicalManifestEqualsCached'] = equivalent
        ab_asserted = True
    finally:
        if mode == 'variant':
            fast.uninstall()
            assert not fast.installed()
    payload = dict(mode=mode, runtime=platform.python_implementation() + ' ' + platform.python_version(),
                   provenanceDigest=prov['digest'],
                   engineDigest=prov['files']['tools/recovery/combat_entities.py'],
                   golden=golden, ab=ab, abAsserted=ab_asserted, floor=floor, illegalTick0=illegal_zero,
                   setupProbe=setup_probe)
    if mode == 'variant':
        payload['cacheStats'] = fast.cache_stats()
    return payload


def profiled(mode):
    import cProfile
    from strategy_optimizer_adapter import default_scenario
    from combat_sandbox import run_scenario
    import strategy_optimizer_setup_fast as fast

    base = default_scenario()
    case = dict(base, **dict(WARMUP_CASE, tickLimit=1))
    if mode == 'variant':
        fast.install()
    try:
        run_scenario(dict(case), include_trace=False)
        profiler = cProfile.Profile()
        profiler.enable()
        run_scenario(dict(case), include_trace=False)
        profiler.disable()
    finally:
        if mode == 'variant':
            fast.uninstall()
    # The variant patches install lambdas, so the canonical names show up as '<lambda>' there.
    watched = ('native_identity', 'run_manifest', 'load_scenario', 'prepare_setup', 'support_contract',
               'load_data', 'table', 'canonical_hash', '<lambda>')
    import io
    import pstats
    entries = {}
    # pstats normalises both the CPython tuple form and the PyPy StatsEntry object.
    for (_filename, _line, name), (_cc, _nc, _tt, ct, _callers) in pstats.Stats(profiler, stream=io.StringIO()).stats.items():
        if name in watched:
            entries[name] = entries.get(name, 0.0) + ct
    return dict(mode=mode, watchedCumulativeSeconds={k: round(v, 6) for k, v in sorted(entries.items())})


def drift_self_test():
    """In-process unit proof that the file cache is stat-keyed and uninstall restores the canonical objects."""
    import combat_run_manifest as crm
    import strategy_optimizer_setup_fast as fast

    before = (crm.native_identity, crm.run_manifest)
    fast.install()
    assert (crm.native_identity, crm.run_manifest) != before
    native = crm.NATIVE / 'inputs' / 'libil2cpp.so'
    scratch = HERE / 'setup-fast-drift-scratch'
    scratch.mkdir(exist_ok=True)
    try:
        probe = scratch / 'probe.bin'
        probe.write_bytes(b'alpha')
        first = fast._file_digest(probe)
        assert fast._file_digest(probe) == first
        probe.write_bytes(b'beta')
        assert fast._file_digest(probe) != first, 'file cache did not reject a changed file'
    finally:
        for leftover in scratch.glob('*'):
            leftover.unlink()
        scratch.rmdir()
    identity = fast._native_identity(crm)
    assert identity == before[0](), 'cached native identity differs from the canonical one'
    signature = fast._stat_signature(native)
    store = fast._STATE['native'][str(native)]
    assert store[0] == signature, 'native cache is not keyed by the current stat signature'
    del fast._STATE['native'][str(native)]
    assert fast._native_identity(crm) == identity, 'native cache is not reproducible after eviction'
    fast.uninstall()
    assert (crm.native_identity, crm.run_manifest) == before, 'uninstall did not restore the canonical objects'
    return True


def spawn(mode, output):
    command = [sys.executable, str(Path(__file__).resolve()), '--worker', '--mode', mode,
               '--output', str(output)]
    print('running %s worker: %s' % (mode, ' '.join(command)), flush=True)
    subprocess.run(command, check=True, cwd=str(HERE))


def summarize(rows, golden):
    seconds = [row['seconds'] for row in rows]
    return dict(runs=len(rows), totalSeconds=sum(seconds), meanSeconds=sum(seconds) / len(seconds),
                meanBattlesPerSecond=len(rows) / sum(seconds), minSeconds=min(seconds),
                maxSeconds=max(seconds),
                perCase=[dict(case=row['case'], seconds=round(row['seconds'], 4), digest=row['digest'],
                              matched=row['digest'] == golden[i]['digest'])
                         for i, row in enumerate(rows)])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--worker', action='store_true')
    parser.add_argument('--mode', choices=('baseline', 'variant'))
    parser.add_argument('--profile', action='store_true')
    parser.add_argument('--reuse', action='store_true',
                        help='reuse existing worker output files instead of re-spawning them')
    parser.add_argument('--golden', type=Path, default=HERE / 'strategy-optimizer-parity.json')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--report', type=Path, default=HERE / 'strategy-optimizer-setup-fast-report.json')
    args = parser.parse_args()
    if args.worker:
        payload = profiled(args.mode) if args.profile else run_worker(args.mode)
        args.output.write_text(json.dumps(payload, indent=2), encoding='utf-8')
        print(json.dumps(dict(mode=args.mode, profile=args.profile,
                              cases=len(payload.get('golden', [])))))
        return 0

    golden = json.loads(args.golden.read_text(encoding='utf-8'))['runs']
    assert len(golden) == len(golden_cases()), 'golden fixture does not match this checker case list'
    assert len(golden) == 22

    outputs = {mode: HERE / ('setup-fast-%s.json' % mode) for mode in ('baseline', 'variant')}
    profiles_out = {mode: HERE / ('setup-fast-profile-%s.json' % mode) for mode in ('baseline', 'variant')}
    fresh = not args.reuse or not all(path.is_file() for path in list(outputs.values()) + list(profiles_out.values()))
    if fresh:
        for mode in ('baseline', 'variant'):
            spawn(mode, outputs[mode])
        for mode in ('baseline', 'variant'):
            command = [sys.executable, str(Path(__file__).resolve()), '--worker', '--mode', mode,
                       '--profile', '--output', str(profiles_out[mode])]
            subprocess.run(command, check=True, cwd=str(HERE))
    else:
        print('reusing existing worker outputs (--reuse)')
    payloads = {mode: json.loads(path.read_text(encoding='utf-8')) for mode, path in outputs.items()}
    profiles = {mode: json.loads(path.read_text(encoding='utf-8')) for mode, path in profiles_out.items()}

    def manifest_equal(index):
        left = payloads['baseline']['golden'][index]
        right = payloads['variant']['golden'][index]
        return (left['manifestDigest'] == right['manifestDigest']
                and left['nativeSha256Source'] == right['nativeSha256Source'])

    checks = {}
    for mode, payload in payloads.items():
        rows = payload['golden']
        checks[mode] = dict(runtime=payload['runtime'], provenanceDigest=payload['provenanceDigest'],
                            engineDigest=payload['engineDigest'],
                            goldenMatched=all(row['digest'] == golden[i]['digest'] for i, row in enumerate(rows)))
    checks['manifestsEqual'] = all(manifest_equal(i) for i in range(len(golden)))
    checks['nativeSha256Source'] = sorted({row['nativeSha256Source']
                                           for row in payloads['variant']['golden']})
    checks['sameEngineSource'] = payloads['baseline']['engineDigest'] == payloads['variant']['engineDigest']
    checks['sameProvenance'] = payloads['baseline']['provenanceDigest'] == payloads['variant']['provenanceDigest']
    checks['sameRuntime'] = payloads['baseline']['runtime'] == payloads['variant']['runtime']
    checks['abRepeatable'] = all(payloads[mode]['abAsserted'] for mode in payloads)
    checks['inputsUnmutated'] = all(row['inputUnchanged'] for mode in payloads for row in payloads[mode]['golden'])
    checks['tickLimitZeroIllegal'] = all(payloads[mode]['illegalTick0'] for mode in payloads)
    checks['uninstallRestoresCanonical'] = drift_self_test()
    checks['canonicalManifestEqualsCached'] = all(
        payloads[mode]['setupProbe'].get('canonicalManifestEqualsCached', True) for mode in payloads)

    summary = {mode: summarize(payloads[mode]['golden'], golden) for mode in payloads}
    floor = {}
    for ticks in FLOOR_TICKS:
        key = str(ticks)  # JSON object keys are strings
        base_row = payloads['baseline']['floor'][key]
        variant_row = payloads['variant']['floor'][key]
        floor[str(ticks)] = dict(reps=base_row['reps'],
                                 baselineMedianMs=base_row['medianMs'], variantMedianMs=variant_row['medianMs'],
                                 baselineMinMs=base_row['minMs'], variantMinMs=variant_row['minMs'],
                                 speedup=base_row['medianMs'] / variant_row['medianMs'])
    report = dict(
        runtime=payloads['baseline']['runtime'],
        method=('warm serial, unprofiled: 3 warmup runs per worker then one run per case; baseline and '
                'variant are separate processes of the same interpreter; cProfile measurements are separate'),
        checks=checks,
        target=dict(requestedBattlesPerSecond=200.0,
                    variantGoldenMeanBattlesPerSecond=summary['variant']['meanBattlesPerSecond'],
                    meetsTarget=summary['variant']['meanBattlesPerSecond'] >= 200.0),
        goldenBaseline=summary['baseline'], goldenVariant=summary['variant'],
        meanSpeedup=summary['baseline']['meanSeconds'] / summary['variant']['meanSeconds'],
        initializationFloor=floor,
        setupProbe={mode: payloads[mode]['setupProbe'] for mode in payloads},
        cacheStats=payloads['variant'].get('cacheStats'),
        profiledSeparate={mode: profiles[mode]['watchedCumulativeSeconds'] for mode in profiles})
    assert checks['baseline']['goldenMatched'], 'unpatched engine no longer matches the golden digests'
    assert checks['variant']['goldenMatched'], 'accelerated setup changed at least one canonical report'
    assert checks['manifestsEqual'], 'cached attestation changed the manifest payload'
    assert checks['sameEngineSource'] and checks['sameProvenance'] and checks['sameRuntime'], 'sources moved between workers'
    assert checks['tickLimitZeroIllegal'] and checks['inputsUnmutated'], 'scenario contract regressed'
    assert checks['uninstallRestoresCanonical'] and checks['canonicalManifestEqualsCached'], 'accelerator is not cleanly reversible'
    args.report.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(dict(matched=True, runtime=report['runtime'],
                          baselineBps=round(summary['baseline']['meanBattlesPerSecond'], 3),
                          variantBps=round(summary['variant']['meanBattlesPerSecond'], 3),
                          meanSpeedup=round(report['meanSpeedup'], 4),
                          floorTick1Speedup=round(floor['1']['speedup'], 3),
                          floorTick1Ms=round(floor['1']['variantMedianMs'], 3),
                          meetsTarget=report['target']['meetsTarget'], report=str(args.report)), indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
