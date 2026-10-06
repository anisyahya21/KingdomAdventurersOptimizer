"""Offline parity/performance proof for the standalone Rust encounter-revision prototype.

Run from the workspace root after `cargo build --release --offline` in
`KA-Website/tools/recovery/native/ka_revision`. This launches no battles and touches no library DB.
"""
from __future__ import annotations

import copy
import hashlib
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
WORKSPACE = HERE.parents[2]
sys.path.insert(0, str(HERE))

import combat_encounters
import combat_runtime_data
import combat_scenario
import combat_resolution
import strategy_encounter_compiler as compiler
import strategy_encounter_revision_native as native
import strategy_mechanics as mechanics


def _assert_revision(scenario, label):
    expected = compiler.compile_encounter(scenario)['revision']
    actual = native.native_revision(scenario)
    assert actual == expected, f'{label}: native={actual!r}, python={expected!r}'
    prepared = mechanics.prepared_setup(scenario)
    level, draws, roster = native._current_context().roster(
        scenario['encounterId'], scenario['defeatCount'], scenario['libSeed'])
    encounter = prepared['encounter']
    python_rows = []
    for fighter in encounter['fighters']:
        params = fighter['parameters']
        python_rows.append([
            fighter['name'], bool(fighter.get('leaderIdentity')),
            [mechanics._param(params, pid) for pid in
             (mechanics.PID_HP, mechanics.PID_MP, mechanics.PID_ATK, mechanics.PID_DEF,
              mechanics.PID_SPD, mechanics.PID_LUK, mechanics.PID_DEX)],
            list(fighter.get('skills', {}).get('dataIds', [])),
        ])
    assert (level, draws, roster) == (encounter['level'], encounter['followerSelectionDraws'],
                                     python_rows), f'{label}: roster/order mismatch'
    return actual


def main():
    fixtures_path = WORKSPACE / 'coordination/ratio-fix/native-revision-fixtures.json'
    fixtures = json.loads(fixtures_path.read_text(encoding='utf-8'))
    assert len(fixtures) == 40
    frozen = 0
    for index, record in enumerate(fixtures):
        scenario = record['scenario']
        assert scenario['encounterId'] == record['encounterId']
        revision = _assert_revision(scenario, f'frozen-{index}')
        assert revision['digest'] == record['expectedEncounterRevision']
        frozen += 1
    print(f'PASS frozen-compatible candidate/reference scenarios: {frozen}')

    base = copy.deepcopy(fixtures[0]['scenario'])
    edge_cases = [
        (0, 0), (1, -1), (4, _I32_MAX := (1 << 31) - 1),
        (5, -(1 << 31)), (99, 1 << 31), (100, (1 << 32) + 1),
        (101, -(1 << 32) - 7), (999, (1 << 63) - 1),
        (1000, -(1 << 63)), (1001, (1 << 80) + 17),
        (10_000, -(1 << 80) - 3), (10_001, _I32_MAX),
        (1 << 31, 1 << 32), ((1 << 32) + 5, -(1 << 31) - 1),
    ]
    cases = 0
    for encounter_id in range(20):
        for index, (defeats, seed) in enumerate(edge_cases):
            scenario = copy.deepcopy(base)
            scenario.update(encounterId=encounter_id, defeatCount=defeats, libSeed=seed + index)
            _assert_revision(scenario, f'edge-{encounter_id}-{index}')
            cases += 1
    for encounter_id in range(20):
        for defeat_count in (99, 100, 101, 999, 1000, 1001, 10_000, 10_001):
            scenario = copy.deepcopy(base)
            scenario.update(encounterId=encounter_id, defeatCount=defeat_count, libSeed=7731)
            _assert_revision(scenario, f'level-boundary-{encounter_id}-{defeat_count}')
            cases += 1
    print(f'PASS exact level/i32/RNG parity cases: {cases}')

    # Exercise threshold selection and draw order with a test-only copy of the source data. The
    # repository data currently has rate100 rows only, so this also proves rejected followers still
    # consume draws and selected duplicates remain in incoming order.
    attestation, raw = native._read_current_data()
    encounter_data = json.loads(raw['encounters.json'].decode('utf-8'))
    synthetic = copy.deepcopy(encounter_data)
    synthetic_encounter = next(row for row in synthetic['encounters'] if row['id'] == 0)
    rates = (0, 1, 50, 99, 100)
    for follower, rate in zip(synthetic_encounter['followers'], rates):
        follower['checkRate'] = rate
    synthetic_ctx = native._context_from_data(synthetic, hashlib.sha256(b'synthetic').digest())
    rng_seeds = (0, -1, -(1 << 31), (1 << 31) - 1, 1 << 32, -(1 << 80) - 11)
    for seed in rng_seeds:
        level, draws, rows = synthetic_ctx.roster(0, 1001, seed)
        rng = combat_resolution.SystemRandomState(seed)
        with patch.object(combat_encounters, 'load_data', lambda _name: synthetic):
            reference = combat_encounters.special_enemy_baseline(
                0, 1001, lambda limit: combat_resolution.random_below(rng.next_int(), limit))
        reference_rows = [[fighter['name'], bool(fighter.get('leaderIdentity')),
                           [fighter['parameters'][str(pid)]['rawValue'] for pid in
                            (10, 11, 13, 14, 15, 16, 19)],
                           list(fighter['skills']['dataIds'])]
                          for fighter in reference['fighters']]
        assert (level, draws, rows) == (reference['level'], reference['followerSelectionDraws'],
                                       reference_rows)
    synthetic_ctx.close()
    print(f'PASS synthetic follower thresholds, order and {len(rng_seeds)} RNG edge seeds')

    # Python validation must run before the optimization. Invalid inputs keep the exact canonical
    # normalizer exception; valid-but-unproved own-unit affinity inputs fall back to Python compile.
    invalid = copy.deepcopy(base)
    invalid.pop('inputs', None)
    errors = []
    for call in (native.native_revision, compiler.compile_encounter):
        try:
            call(invalid)
        except Exception as exc:  # compare exact validation failure
            errors.append((type(exc), str(exc)))
        else:
            errors.append(None)
    assert errors[0] is not None and errors[0] == errors[1], errors
    unusual = copy.deepcopy(base)
    unusual['ownUnits'][0]['equipment'][0]['affinity'] = 1e308
    canonical_error = None
    try:
        compiler.compile_encounter(unusual)
    except Exception as exc:
        canonical_error = (type(exc).__name__, str(exc))
    assert native.native_revision(unusual) is None
    print(f'PASS malformed validation parity; unusual-affinity fallback (canonical={canonical_error})')

    # The normalizer rejects own raw/training fields outside signed i32 before either
    # implementation can run. Oversized positive equipment levels are accepted and
    # explicitly wrapped by the canonical equipment formula, so the shortcut must still
    # produce the exact same revision for that valid edge case.
    oversized_raw = copy.deepcopy(base)
    first_parameter = next(iter(oversized_raw['ownUnits'][0]['parameters'].values()))
    first_parameter['rawValue'] = 1 << 100
    errors = []
    for call in (native.native_revision, compiler.compile_encounter):
        try:
            call(oversized_raw)
        except Exception as exc:
            errors.append((type(exc), str(exc)))
        else:
            errors.append(None)
    assert errors[0] is not None and errors[0] == errors[1], errors
    huge_equipment_level = copy.deepcopy(base)
    huge_equipment_level['ownUnits'][0]['equipment'][0]['level'] = 1 << 100
    _assert_revision(huge_equipment_level, 'i32-wrapped huge equipment level')
    print('PASS oversized own raw-value rejection parity; huge equipment level wrapped with exact revision parity')

    # Mutating attested source data invalidates the resident context. The first request falls back;
    # the following request can build only from the fresh source bytes.
    original_data_path = combat_runtime_data.data_path
    with tempfile.TemporaryDirectory(prefix='ka-native-revision-attestation-') as directory:
        root = Path(directory)
        paths = {}
        for name in native._DATA_NAMES:
            path = root / name
            path.write_bytes(original_data_path(name).read_bytes())
            paths[name] = path
        with patch.object(combat_runtime_data, 'data_path', lambda name: paths[name]):
            native.clear_context()
            assert native.native_revision(base) is not None
            source = json.loads(paths['encounters.json'].read_text(encoding='utf-8'))
            source['encounters'][19]['levelField'] += 1
            paths['encounters.json'].write_text(json.dumps(source), encoding='utf-8')
            assert native.native_revision(base) is None
            mechanics.invalidate_caches()
            updated = native.native_revision(base)
            expected = compiler.compile_encounter(base)['revision']
            assert updated == expected
    native.clear_context()
    mechanics.invalidate_caches()
    assert native.native_revision(base) == compiler.compile_encounter(base)['revision']
    print('PASS stale context rejected on source change; re-attested context matches canonical')

    # A local warm-path microbenchmark only: vary an irrelevant own statistic so the canonical
    # compiler must rebuild prepared own equipment while the enemy-only output stays invariant.
    variants = []
    for index in range(24):
        scenario = copy.deepcopy(base)
        parameter = scenario['ownUnits'][0]['parameters'].get('13')
        if parameter is None:
            parameter = scenario['ownUnits'][0]['parameters'][13]
        parameter['rawValue'] += index + 1
        variants.append(scenario)
    for scenario in variants:
        assert native.native_revision(scenario) == compiler.compile_encounter(scenario)['revision']
    mechanics.invalidate_caches()
    native.clear_context()
    start = time.perf_counter()
    native_results = [native.native_revision(scenario) for scenario in variants]
    native_seconds = time.perf_counter() - start
    mechanics.invalidate_caches()
    start = time.perf_counter()
    python_results = [compiler.compile_encounter(scenario)['revision'] for scenario in variants]
    python_seconds = time.perf_counter() - start
    assert native_results == python_results
    print(json.dumps({
        'status': 'ok', 'frozenScenarios': frozen, 'exactBoundaryCases': cases,
        'syntheticRngSeeds': len(rng_seeds), 'microbenchmark': {
            'variants': len(variants), 'warmNativeTotalSeconds': native_seconds,
            'canonicalPythonTotalSeconds': python_seconds,
            'nativeMedianMs': statistics.median([native_seconds / len(variants)]) * 1000,
            'pythonMedianMs': statistics.median([python_seconds / len(variants)]) * 1000,
            'speedup': python_seconds / native_seconds if native_seconds else None,
            'scope': 'offline warm microbenchmark, not end-to-end optimizer throughput',
        },
    }, sort_keys=True))


if __name__ == '__main__':
    main()
