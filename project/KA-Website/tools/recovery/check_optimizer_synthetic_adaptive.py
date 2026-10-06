"""Focused stage-one roots and stored adaptive threshold lifecycle checks."""
from pathlib import Path
from tempfile import TemporaryDirectory

import search_contract
import strategy_probe
from strategy_optimizer import Optimizer, Store, seed_pair
from strategy_optimizer_adapter import default_scenario, provenance, simulate, stats
from strategy_synthetic_roots import maximum_team_size, synthetic_root


base = default_scenario()
encounter = dict(base, encounterId=19)
enemy_count = search_contract.enemy_count(19)
limit = maximum_team_size(enemy_count)
sizes = set()
roles_seen = set()
for index in range(limit * 3):
    root, roles = synthetic_root(encounter, index, enemy_count)
    search_contract.check_scenario(root)
    sizes.add(len(root['ownUnits']))
    roles_seen.update(roles)
    assert len(root['ownUnits']) == 1 + index % limit
    assert len({unit['name'] for unit in root['ownUnits']}) == len(root['ownUnits'])
    for unit, role in zip(root['ownUnits'], roles):
        if role == 'striker':
            assert len(unit['skills']) == 1, 'a fresh striker copied the six-skill example'
assert sizes == set(range(1, limit+1))
assert {'mage', 'bow', 'gun', 'spear', 'healer'} <= roles_seen
for index in (2, 4):
    root, roles = synthetic_root(encounter, index, enemy_count)
    for unit, role in zip(root['ownUnits'], roles):
        if role in ('mage', 'bow', 'gun', 'spear'):
            assert 107 in unit['skills'], (role, unit['skills'])

# Confirm the ranged/magic starts do real work from Backup in the runner, not just pass a schema check.
easy = dict(base, encounterId=0)
rear, roles = synthetic_root(easy, 2, search_contract.enemy_count(0))
events = simulate(rear, (11, 12), trace=True)['replay']['events']
ally_attacks = {entry.get('attackerUnitId') for entry in events if entry.get('kind') == 'attack'}
assert {'ally:0', 'ally:1', 'ally:2'} <= ally_attacks, (roles, ally_attacks)
assert any(entry.get('kind') == 'area_cell' and entry.get('skillId') == 5
           for entry in events), 'rear magic did not produce its area effect'
spear, roles = synthetic_root(easy, 4, search_contract.enemy_count(0))
events = simulate(spear, (11, 12), trace=True)['replay']['events']
assert any(entry.get('kind') == 'attack' and entry.get('attackerUnitId') == 'ally:0'
           for entry in events), 'rear spear did not attack'

with TemporaryDirectory() as directory:
    store = Store(Path(directory) / 'adaptive.sqlite', provenance())
    unit = base['ownUnits'][0]['name']
    pivot_value = strategy_probe.pivot_value(base, unit, 'atk')
    lower, upper = int(pivot_value * .5), int(pivot_value * 1.5)
    pivot = store.add(base, 'pivot', 'supplied', stats(base))
    low = strategy_probe.apply_axis(base, unit, 'atk', lower)
    high = strategy_probe.apply_axis(base, unit, 'atk', upper)
    low_id = store.add(low, 'low', 'probe', stats(low))
    high_id = store.add(high, 'high', 'probe', stats(high))
    for cid, chests in ((pivot, 10), (low_id, 0), (high_id, 10)):
        for ordinal in range(64):
            row = dict(verdict=1, censored=False, seeds=seed_pair('validation', ordinal),
                       rewardOutcome=dict(pendingChests=chests))
            store.db.execute('INSERT INTO run VALUES (?,?,?,?)',
                             (cid, 'validation', ordinal, __import__('json').dumps(row)))
    store.set('probeBanks', {cid: 64 for cid in (pivot, low_id, high_id)})
    key = f'{pivot}:{unit}:atk'
    store.set('adaptiveProbePlans', {key: dict(pivot=pivot, unit=unit, axis='atk',
                                              rungs={str(lower): low_id, str(pivot_value): pivot,
                                                     str(upper): high_id},
                                              done=False)})
    store.db.commit()
    assert Optimizer._advance_probe_plans(None, store)
    plan = store.get('adaptiveProbePlans')[key]
    assert len(plan['rungs']) == 4, plan
    midpoint = (lower + pivot_value)//2
    assert str(midpoint) in plan['rungs'], plan
    assert store.get('probeBanks')[plan['rungs'][str(midpoint)]] == 64
    assert not Optimizer._advance_probe_plans(None, store), 'must wait for new paired bank'
    store.close()

print(f'synthetic teams 1..{limit}, rear ranged/magic seeds, adaptive midpoint persistence passed')
