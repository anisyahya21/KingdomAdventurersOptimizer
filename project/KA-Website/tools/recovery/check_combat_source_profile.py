"""Declared pre-placement start profile: native producer, equivalence class and sensitivity.

The profile is a simulation condition. This check proves (a) the enemy source cell/board the
profile derives is the recovered World.CreateMonster 0x147780c producer, (b) every other
pre-placement field is InitFighters-overwritten or unread, so arbitrary source values cannot
change the resolved run, and (c) how far the declared enemy source cell can move the outcome.
"""
import json
from copy import deepcopy
from combat_initial_state import EVIDENCE, START_PROFILE_KINDS, div24, expand_start_profile, trunc_div
from combat_run_manifest import support_contract
from combat_runtime_data import WORKSPACE_ROOT
from combat_sandbox import run_scenario
from combat_scenario import load_scenario, ScenarioError
from check_combat_sandbox import example

def asm(name):
    return (WORKSPACE_ROOT / 'RE-evidence/20260912-combat' / name).read_text(encoding='utf-8').splitlines()

# --- 1. The producer, from the recorded CreateMonster slice ----------------------------
create_monster = asm('147780c.asm')
text = '\n'.join(create_monster)
for required in ('kairo.unity.ecs.Entity$$AddPosition', 'kairo.unity.ecs.Entity$$AddCell',
                 'kairo.unity.ai.BlackboardInt$$.ctor', 'kairo.unity.ecs.Entity$$AddAI',
                 'smull x9, w9, w10', 'lsr x11, x9, #0x20', 'lsr x9, x9, #0x3f'):
    assert required in text, required
assert 'mov w10, #0xaaab' in text and 'movk w10, #0x2aaa, lsl #16' in text
board_adds = [line.strip() for line in create_monster
              if 'mov w1, #0x13' in line or 'mov w1, #0x14' in line or
              ('Dictionary<Int32Enum, int>$$Add' in line)]
assert board_adds == ['1477ae0: mov w1, #0x13',
                      '1477af4: bl #0x1b1ffe4 System.Collections.Generic.Dictionary<Int32Enum, int>$$Add',
                      '1477afc: mov w1, #0x14',
                      '1477b08: bl #0x1b1ffe4 System.Collections.Generic.Dictionary<Int32Enum, int>$$Add'], board_adds
assert 'mov w2, w26' in text and 'mov w2, w25' in text

followers = '\n'.join(asm('16ac77c.asm'))
assert 'fmov s0, wzr' in followers and 'fmov s1, wzr' in followers and 'fmov s2, wzr' in followers
assert 'kairo.unity.ecs.World$$CreateEnemyMonster' in followers

# The /24 magic is exact truncation toward zero, the inverse of init_position's cell*24.
def asm_div24(value):
    product = (value * 0x2AAAAAAB) & 0xffffffffffffffff
    high32 = (product >> 32) & 0xffffffff
    if high32 >= 0x80000000: high32 -= 0x100000000
    return (product >> 63) + (high32 >> 2)

assert all(asm_div24(v) == trunc_div(v, 24) == div24(v) for v in range(-600, 601)), 'div24 mismatch'
assert (div24(0.0), div24(24.0), div24(-24.0)) == (0, 1, -1)

# --- 2. The profile removes the hand-written blackboard requirement --------------------
base = example()
assert support_contract(load_scenario(base))['conditionalSimulation']['supported'] is False
profiled = deepcopy(base)
profiled['startProfile'] = dict(kind=START_PROFILE_KINDS[0], enemySpawnCell=[0, 0])
contract = support_contract(load_scenario(profiled))
# The declared profile removes every source-state condition: the ONLY unmet condition is the
# global finish policy, which fails closed for every declared policy until the native automatic
# producer of the Ending KEY_SELECT edge is recovered (check_combat_auto_finish_producer.py).
# So the profile is complete as a SOURCE declaration while the run stays unranked; the two must
# not be conflated into "the profile is unsupported".
conditional = contract['conditionalSimulation']
assert conditional['supported'] is False, conditional
assert conditional['unmetConditions'] == ['yield_eligible_finish_policy'], conditional['unmetConditions']
assert all(condition['met'] for condition in conditional['conditions']
           if condition['name'] != 'yield_eligible_finish_policy'), \
    [condition['name'] for condition in conditional['conditions'] if not condition['met']]
assert contract['initialization'] == 'profile' and contract['sourceState']['profile']['kind'] == 'isolated-scene0'
assert support_contract(load_scenario(base))['initialization'] == 'approximate'

for bad in (dict(kind='nope'), dict(kind=START_PROFILE_KINDS[0], enemySpawnCell=[0]),
            dict(kind=START_PROFILE_KINDS[0], startingStatus={'synthetic farmer': [999, 1]}),
            dict(kind=START_PROFILE_KINDS[0], startingStatus={'synthetic farmer': [110, 0]})):
    broken = deepcopy(profiled); broken['startProfile'] = bad
    try: load_scenario(broken)
    except ScenarioError: pass
    else: raise AssertionError('startProfile accepted ' + repr(bad))
mixed = deepcopy(profiled)
mixed['prePlacement'] = {name: dict(cell=[0, 0], position=[0., 0., 0.], offset=[0., 0., 0.],
    board={4: 0, 5: 0, 6: 0, 7: 0, 8: 0}, longBoard={}) for name in ('synthetic farmer',)}
try: load_scenario(mixed)
except ScenarioError: pass
else: raise AssertionError('prePlacement and startProfile accepted together')

def run(data):
    return run_scenario(deepcopy(data), True)

def strip(trace):
    return [dict(event, **({} if event['kind'] != 'initial_placement' else {'oldCell': None})) for event in trace]

def signature(report):
    return (report['result']['mathDraws'], report['result']['libDraws'], report['result']['verdict'],
            report['result']['rngFinalState'], len(report['trace']))

profile_run = run(profiled)
names = profile_run['result']['names']
manual = deepcopy(profiled)
manual.pop('startProfile')
# Arbitrary values in every field native overwrites or never reads.
manual['prePlacement'] = {name: dict(cell=[100, 100], position=[2400., 0., 2400.], offset=[1., 2., 3.],
    board={4: 99, 5: 0, 6: 99, 7: 99, 8: 99, 88: 123}, longBoard={90: 456}) for name in names}
manual_run = run(manual)
assert manual_run['manifest']['support']['initialization'] == 'supplied'
assert strip(manual_run['trace']) == strip(profile_run['trace']), 'overwritten/unread source fields changed the run'
assert signature(manual_run) == signature(profile_run)
assert profile_run['result']['initializationMode'] == 'supplied pre-placement state, sequential teams'
assert sum(event['kind'] == 'initial_placement' for event in profile_run['trace']) == len(names)
old_cells = {event['target']: event['oldCell'] for event in profile_run['trace'] if event['kind'] == 'initial_placement'}
enemy_ids = {identity for name, identity in names.items() if name.startswith('enemy:')}
assert enemy_ids and all(old_cells[i] == (0, 0) for i in enemy_ids)
assert all(old_cells[i] == (0, 0) for n, i in names.items() if not n.startswith('enemy:'))

# Own members are placed before any decision can read them: their source cell is inert.
own_shifted = deepcopy(manual)
for name in names:
    if not name.startswith('enemy:'):
        own_shifted['prePlacement'][name]['cell'] = [7, 9]
        own_shifted['prePlacement'][name]['position'] = [168., 0., 216.]
owed = run(own_shifted)
assert signature(owed) == signature(profile_run)
assert [e for e in strip(owed['trace']) if e['kind'] != 'initial_placement'] == \
       [e for e in strip(profile_run['trace']) if e['kind'] != 'initial_placement']
own_old = {event['target']: event['oldCell'] for event in owed['trace'] if event['kind'] == 'initial_placement'}
assert all(own_old[i] == (7, 9) for name, i in names.items() if not name.startswith('enemy:'))

# --- 3. How far the declared enemy source cell can move the run ------------------------
sensitivity = []
for spawn in ([0, 0], [1, 1], [5, 5], [50, 50], [-4, -4]):
    variant = deepcopy(profiled)
    variant['startProfile'] = dict(kind=START_PROFILE_KINDS[0], enemySpawnCell=spawn)
    report = run(variant)
    assert report == run(variant)
    sensitivity.append(dict(enemySpawnCell=spawn, events=len(report['trace']),
        mathDraws=report['result']['mathDraws'], libDraws=report['result']['libDraws'],
        verdict=report['result']['verdict']))
distinct = {json.dumps({k: v for k, v in row.items() if k != 'enemySpawnCell'}, sort_keys=True)
            for row in sensitivity}
bounded = len(distinct) == 1

# --- 4. Declared starting status is carried, not invented ------------------------------
from combat_farmer_slice import ROWS
status_skill = next(row for row in ROWS.values() if row['type'] == 66)
roster = [dict(name='own', team=0, boss=False), dict(name='boss', team=1, boss=True)]
derived = expand_start_profile(dict(kind=START_PROFILE_KINDS[0],
    startingStatus={'own': [status_skill['id'], 2]}), roster)
assert derived['own']['board'][62] == status_skill['id']
assert (derived['own']['board'][63], derived['own']['board'][64]) == (2, 0)
assert (derived['boss']['board'][4], derived['boss']['board'][5], derived['boss']['board'][7]) == (0, 0, 0)
assert (derived['boss']['board'][19], derived['boss']['board'][20]) == (0, 0)
assert 62 not in derived['boss']['board'] and derived['boss']['longBoard'] == {}
assert derived['own']['cell'] == [0, 0] and derived['own']['position'] == [0.0, 0.0, 0.0]
assert derived['boss']['cell'] == [0, 0]
boss_cell = expand_start_profile(dict(kind=START_PROFILE_KINDS[0], enemySpawnCell=[1, 1], bossCell=[7, 9]), roster)
assert boss_cell['boss']['cell'] == [7, 9] and boss_cell['boss']['board'][19] == 7 and boss_cell['boss']['board'][20] == 9

report = dict(nativeProducer='World.CreateMonster 0x147780c via World.CreateEnemyMonster 0x1477c5c',
    producerChecks=dict(cellMagic=True, boardKey19=True, boardKey20=True, followerSpawnZero=True),
    # The profile is a complete source declaration; the run still fails closed on the unproven
    # native automatic Finish, so no declared policy is yield-eligible and nothing is ranked.
    profileSourceConditionsMet=True, profileConditionalSimulationSupported=False,
    profileUnmetConditions=['yield_eligible_finish_policy'],
    overwrittenFieldEquivalence=True, ownSourceInert=True,
    enemySpawnSensitivity=sensitivity, enemySpawnSensitivityBounded=bounded,
    startingStatus=status_skill['id'],
    limits=['The boss source cell is the boss entity world cell, which is not captured; it stays an explicit profile field (bossCell).',
            'Unplaced fighters carry the board-contract placeholder 5:0/7:0 (IsAttackableTarget 0x1588554 reads BKI:5 through Dictionary.get_Item, so a not-yet-initialized board cannot carry Waiting1).',
            'Sensitivity is measured on the synthetic fixtures, not on a captured save.',
            'The profile conditionalSimulation stays unsupported only because no native-automatic Finish producer is proven; every source/skill condition is met.'])
(EVIDENCE / 'source-profile-checks.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
print(json.dumps(report))
