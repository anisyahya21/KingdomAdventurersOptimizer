"""Bounded synthetic memory/throughput comparison for seed pairs and frozen encounter intents.

This does not open the live optimizer database. The default workload is capped at 200k seed pairs
and 256 JSON intents; increase either only on a machine with adequate free RAM.
"""
from __future__ import annotations

import argparse
import gc
import json
import time
import tracemalloc

from strategy_seed_freshness import SeedPairSet


def _measure_retained(build):
    gc.collect()
    tracemalloc.start()
    started = time.perf_counter()
    value = build()
    elapsed = time.perf_counter() - started
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return value, dict(seconds=elapsed, retainedBytes=current, peakBytes=peak)


def _pair_benchmark(count):
    probes = list(range(0, count, max(1, count // 20_000)))

    def build_tuples():
        return {(i, (i * 65_537 + 17) & 0x7fff_ffff) for i in range(count)}

    def build_packed():
        pairs = SeedPairSet()
        for i in range(count):
            pairs._add(i, (i * 65_537 + 17) & 0x7fff_ffff)
        return pairs

    old, old_stats = _measure_retained(build_tuples)
    old_started = time.perf_counter()
    old_hits = sum((i, (i * 65_537 + 17) & 0x7fff_ffff) in old for i in probes)
    old_lookup = time.perf_counter() - old_started
    del old
    gc.collect()

    packed, packed_stats = _measure_retained(build_packed)
    packed_started = time.perf_counter()
    packed_hits = sum((i, (i * 65_537 + 17) & 0x7fff_ffff) in packed for i in probes)
    packed_lookup = time.perf_counter() - packed_started
    if len(packed) != count or old_hits != packed_hits or packed_hits != len(probes):
        raise AssertionError('seed-pair benchmark changed membership/cardinality')
    return dict(count=count, probes=len(probes), tupleSet=old_stats,
                packedSet=packed_stats, tupleLookupSeconds=old_lookup,
                packedLookupSeconds=packed_lookup, membershipHits=packed_hits,
                retainedReduction=(1 - packed_stats['retainedBytes'] /
                                   max(1, old_stats['retainedBytes'])))


def _synthetic_intent(row_id):
    # Two observed scenarios and a nested configuration make the decoded-object workload close to
    # the measured 39–42 KB intents while avoiding any live DB data or DB writes.
    candidates = [f'candidate-{row_id}-a', f'candidate-{row_id}-b']
    observed = {}
    for offset, cid in enumerate(candidates):
        observed[cid] = dict(encounterId=(row_id + offset) % 500,
                             defeatCount=(row_id + offset) % 6,
                             parameters={f'p{i:03d}': ('v' * 48) for i in range(80)})
    intent = dict(observedScenarios=observed,
                  config={f'option{i:04d}': ('x' * 24) for i in range(740)},
                  previous={'revision': row_id % 11, 'description': 'synthetic'})
    return candidates, json.dumps(intent, separators=(',', ':'))


def _intent_benchmark(count):
    fixture = [_synthetic_intent(i) for i in range(count)]
    raw_bytes = sum(len(raw.encode('utf-8')) for _candidates, raw in fixture)

    def build_full():
        return {i: json.loads(raw) for i, (_candidates, raw) in enumerate(fixture)}

    def build_compact():
        result = {}
        for i, (candidates, raw) in enumerate(fixture):
            decoded = json.loads(raw)
            scenarios = decoded.get('observedScenarios') or {}
            result[i] = {cid: (int(scenarios[cid]['encounterId']),
                               int(scenarios[cid].get('defeatCount') or 0))
                         for cid in candidates if isinstance(scenarios.get(cid), dict)
                         and 'encounterId' in scenarios[cid]}
        return result

    full, full_stats = _measure_retained(build_full)
    probes = [(i, f'candidate-{i}-a') for i in range(count)]
    started = time.perf_counter()
    full_hits = sum(full[i]['observedScenarios'][cid]['encounterId'] >= 0 for i, cid in probes)
    full_lookup = time.perf_counter() - started
    del full
    gc.collect()

    compact, compact_stats = _measure_retained(build_compact)
    started = time.perf_counter()
    compact_hits = sum(compact[i].get(cid, (-1, 0))[0] >= 0 for i, cid in probes)
    compact_lookup = time.perf_counter() - started
    if full_hits != compact_hits or compact_hits != count:
        raise AssertionError('frozen-identity benchmark changed recovered candidates')
    return dict(count=count, rawJsonBytes=raw_bytes, fullIntentCache=full_stats,
                compactIdentityCache=compact_stats, fullLookupSeconds=full_lookup,
                compactLookupSeconds=compact_lookup, candidateLookups=count,
                retainedReduction=(1 - compact_stats['retainedBytes'] /
                                   max(1, full_stats['retainedBytes'])))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pairs', type=int, default=100_000)
    parser.add_argument('--intent-rows', type=int, default=256)
    args = parser.parse_args()
    if not 1 <= args.pairs <= 100_000 or not 1 <= args.intent_rows <= 2_000:
        parser.error('bounds are 1..100,000 pairs and 1..2,000 intent rows')
    report = dict(pairs=_pair_benchmark(args.pairs),
                  frozenIntents=_intent_benchmark(args.intent_rows))
    print(json.dumps(report, sort_keys=True, indent=2))


if __name__ == '__main__':
    main()
