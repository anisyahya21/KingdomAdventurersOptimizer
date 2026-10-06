"""Focused regression checks for local stat probes and one-time evidence folding."""
import json
import random
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import search_contract as contract  # noqa: E402
import strategy_optimizer as optimizer  # noqa: E402
from strategy_optimizer_adapter import default_scenario, provenance, stats  # noqa: E402


def check_probe():
    parent = default_scenario()
    count = 0
    units_seen = set()
    for seed in range(80):
        probe = optimizer._local_probe_steps(
            random.Random(seed), parent, {}, parent['encounterId'])
        if probe is None:
            continue
        child, operation, stat, _change, _value = probe
        assert operation == 'set-stat', 'a pair needs single-axis evidence first'
        parameter = contract.stat_parameter(stat)
        changed = optimizer.changed_stat_unit(parent, child, parameter)
        assert changed is not None, 'a local probe must change exactly one human'
        unit_name, before, after = changed
        units_seen.add(unit_name)
        assert before != after and .75 <= after / before <= 1.25, (stat, before, after)
        count += 1
    assert count >= 60, count
    assert len(units_seen) >= 3, units_seen


def check_observation():
    parent = default_scenario()
    first = next(unit for unit in parent['ownUnits'] if unit.get('human'))
    before = contract.battle_value(parent, first, contract.stat_parameter('atk'))
    child = contract.set_stat(parent, first['name'], 'atk', before + 1)
    with tempfile.TemporaryDirectory(prefix='ka-local-check-', ignore_cleanup_errors=True) as root:
        store = optimizer.Store(Path(root) / 'test.sqlite', provenance())
        try:
            with store.db:
                pid = store.add(parent, 'parent', 'supplied', stats(parent))
                cid, existed = store.add_child(child, 'child', stats(child), pid, 'set-stat',
                                               'atk', 'atk +1', 'local:atk', 1)
                assert not existed
                # The old last-4000 lexical truncation discarded this active child on every pass.
                store.set('observedChildren', [f'z{i:04d}' for i in range(4001)])
                store.set('learnerEvidenceVersion', 4)
                # The aggregate high-water mark favours the parent, but every matched seed favours
                # the child. Local direction learning must use the latter evidence.
                for ordinal in range(8):
                    for candidate, chests in ((pid, 1), (cid, 2)):
                        store.db.execute('INSERT INTO run VALUES (?,?,?,?)',
                                         (candidate, 'discovery', ordinal,
                                          json.dumps(dict(verdict=1, prizeCallbacks=chests))))
            base = dict(n=8, wins=8, losses=0, censored=0, earnedCount=8,
                        resources=0, progressRuns=8, progress={})
            aggregates = {
                f'aggregate:{pid}:discovery': dict(base, earnedMax=10, potentialMax=10),
                f'aggregate:{cid}:discovery': dict(base, earnedMax=9, potentialMax=9),
            }
            keys = store.candidate_keys()
            optimizer.observe_children(store, aggregates, keys)
            original_get = store.get
            def no_learning_maps(key, default=None):
                assert key not in {'childImprovements', 'operatorStats', 'statScales',
                                   'statAnchors', 'localEvidence', 'strategyRegions'}, key
                return original_get(key, default)
            # An already folded child must neither be counted again nor load large historical maps.
            with patch.object(store, 'get', side_effect=no_learning_maps):
                optimizer.observe_children(store, aggregates, keys)
            assert cid in store.get('observedChildren')
            evidence = store.get('localEvidence') or {}
            assert sum(entry['n'] for entry in evidence.values()) == 1, evidence
            assert next(iter(evidence.values()))['improved'] == 1, evidence
            origin = optimizer.learner.strategy_region(parent)
            assert next(iter(evidence)).startswith(
                f"{parent['encounterId']}:{origin}:{first['name']}:atk:"), evidence
        finally:
            store.db.commit()
            store.close()


if __name__ == '__main__':
    check_probe()
    check_observation()
    print('local probe and evidence checks passed')
