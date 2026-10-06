"""Auto backend covers every legal search skill with compact-result parity."""
from copy import deepcopy

import search_contract
import strategy_optimizer_native as native
from strategy_optimizer_adapter import default_scenario, validate_scenario
from strategy_synthetic_roots import synthetic_root


base = default_scenario()
for encounter_id, sizes in ((0, (1, 3, 6, 12, 16, 21, 26)),
                            (7, (1, 3, 6, 7, 8, 9, 10, 11)),
                            (19, (1, 3, 6))):
    encounter = dict(base, encounterId=encounter_id)
    for size in sizes:
        scenario, _roles = synthetic_root(encounter, size - 1, search_contract.enemy_count(encounter_id))
        scenario = __import__('json').loads(__import__('json').dumps(scenario))
        assert native.auto_native_safe(scenario)
        for seeds in ((11, 12), (21, 22)):
            fast = native.simulate_compact(scenario, seeds)
            slow = native.simulate_compact(scenario, seeds, backend='python')
            assert fast.pop('resultBackend') == 'native'
            slow.pop('resultBackend')
            assert fast == slow, (encounter_id, size, seeds)

for skill in sorted(search_contract.whitelist()):
    scenario, _roles = synthetic_root(dict(base, encounterId=0), 0, search_contract.enemy_count(0))
    scenario['ownUnits'][0]['skills'] = [skill]
    scenario['ownUnits'][0]['invocationLevels'] = [0]
    if skill == 36:  # Myriad Arrows requires a bow; exercise it rather than just equipping it.
        scenario['ownUnits'][0]['weaponId'] = 166
        scenario['ownUnits'][0]['equipment'][0]['id'] = 166
    scenario = validate_scenario(scenario)
    assert native.auto_native_safe(scenario)
    fast = native.simulate_compact(scenario, (11, 12))
    slow = native.simulate_compact(scenario, (11, 12), backend='python')
    assert fast.pop('resultBackend') == 'native'
    slow.pop('resultBackend')
    assert fast == slow, skill

secondary = deepcopy(base)
secondary['ownUnits'][0]['skills'].append(109)
secondary['ownUnits'][0]['invocationLevels'].append(0)
assert not native.auto_native_safe(secondary)
assert native.simulate_compact(secondary, (11, 12))['resultBackend'] == 'python'
print('native auto routing and compact parity passed')
