"""Focused check: INT is a combat probe axis only for a unit whose attack is magical.

The probe path is exercised through `Optimizer._create_probe`, the same delegation the desktop host
uses, against a real stored library (no battles are run). It pins the refusals - INT on a non-magic
pivot, INT as either axis of a two-axis grid, and an explicitly selected non-magic unit - and that
the default selection prefers a magic attacker rather than the first human.
"""
import tempfile
from pathlib import Path
from unittest.mock import patch

import search_contract
from strategy_optimizer import Optimizer, Store
from strategy_optimizer_adapter import default_scenario, provenance, stats


def refused(store, value):
    """The refusal reason for a probe the optimiser rejects, or None when it is accepted."""
    try:
        Optimizer._create_probe(None, store, value)
    except ValueError as error:
        return str(error)
    return None


def main():
    base = default_scenario()
    magic = next(unit['name'] for unit in base['ownUnits']
                 if unit.get('human') and not unit['skills'])
    plain = base['ownUnits'][0]['name']
    armed = search_contract.add_skill(base, magic, 5)  # Fire Magic I: a magic attack row.

    # A bare temporary file rather than a TemporaryDirectory: some sandboxes refuse a sqlite database
    # created inside a freshly made subdirectory, and this keeps the check runnable in them.
    handle = tempfile.NamedTemporaryFile(suffix='.sqlite', delete=False)
    handle.close()
    path = Path(handle.name)
    store = Store(path, provenance())
    try:
        pivot_id = store.add(base, 'pivot', 'supplied', stats(base))
        armed_id = store.add(armed, 'armed pivot', 'supplied', stats(armed))

        assert refused(store, dict(candidateId=pivot_id, axis='int')), \
            'INT must be refused without a magic attack skill'
        assert refused(store, dict(candidateId=pivot_id, axis='int', axis2='atk'))
        assert refused(store, dict(candidateId=pivot_id, axis='atk', axis2='int'))
        assert refused(store, dict(candidateId=pivot_id, axis='int', unit=plain))
        assert refused(store, dict(candidateId=armed_id, axis='int', unit=plain)), \
            'an explicitly selected non-magic unit must be refused'

        error, created = Optimizer._create_probe(None, store, dict(candidateId=armed_id, axis='int'))
        assert error is None, error
        assert created['unit'] == magic, 'the default must prefer the magic attacker'
        assert created['established'] is True
        assert created['created'] or created['reused'], 'the INT ladder must produce rungs'

        error, grid = Optimizer._create_probe(
            None, store, dict(candidateId=armed_id, axis='atk', axis2='int', points=3, points2=3))
        assert error is None, error
        assert grid['unit'] == magic and grid['ladder2']

        # The publish path reads each stored rung back through `strategy_probe.effective_stat`, so a
        # stored INT probe must analyse without raising and keep its probed points.
        analyses = Optimizer.__new__(Optimizer)._probes(store)
        int_analysis = next((item for item in analyses if item.get('axis') == 'int'
                             and item.get('pivotId') == armed_id), None)
        assert int_analysis is not None, 'the stored INT probe must publish an analysis'
        assert int_analysis['axisLabel'] == 'Intelligence' and int_analysis['points']
        assert int_analysis['points'][0]['effective'] is not None, 'INT must report its effective value'
        grid_analysis = next((item for item in analyses if item.get('kind') == 'grid'
                              and item.get('axis2') == 'int'), None)
        assert grid_analysis is not None, 'the stored INT grid must publish an analysis'

        optimizer = Optimizer.__new__(Optimizer)
        optimizer._probe_cache = None
        def run(ordinal):
            return dict(verdict=1, censored=False, ticks=40, prizeCallbacks=1,
                        survivors=1, resourceUses=0, behavior=dict(attacks=1, heals=0, prizes=1),
                        seeds=[ordinal, 100+ordinal], digest=f'probe-check-{ordinal}',
                        elapsedSeconds=.01,
                        rewardOutcome=dict(awardedChests=1, pendingChests=1,
                                           awardedBasis='check', inventoryVerified=False))
        with patch.object(optimizer, '_probes', wraps=optimizer._probes) as analyse:
            first = optimizer._probe_snapshot(store)
            assert analyse.call_count == 1
            store.record(pivot_id, 'discovery', 0, run(0))
            assert optimizer._probe_snapshot(store) == first
            assert analyse.call_count == 1, 'an unrelated run rebuilt every probe ladder'
            point_id = int_analysis['points'][0]['candidateId']
            store.record(point_id, 'validation', 0, run(1))
            optimizer._probe_snapshot(store)
            assert analyse.call_count == 2, 'a new probe reading did not refresh its ladder'
        store.batch_size = 256
        for ordinal in range(700):
            store.record(armed_id, 'validation', ordinal, run(ordinal + 1))
            if ordinal:
                store.record(point_id, 'validation', ordinal, run(ordinal + 1))
        store.flush()
        assert len(store.rows(point_id, 'validation')) == 512
        deep = next(item for item in optimizer._probe_snapshot(store)
                    if item.get('axis') == 'int' and item.get('pivotId') == armed_id)
        point = next(item for item in deep['points'] if item['candidateId'] == point_id)
        assert point['delta']['n'] == 700, 'probe comparisons lost evidence at the replay cap'
    finally:
        store.close()
        for suffix in ('', '-wal', '-shm'):
            leftover = Path(str(path) + suffix)
            if leftover.exists():
                leftover.unlink()
    print('INT probe axis checks passed (refusals, magic default, single and grid ladders)')


if __name__ == '__main__':
    main()
