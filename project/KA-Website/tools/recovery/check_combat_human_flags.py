"""Human flags read closure: the verified special-battle read set and the fail-closed mask.

ecs.HumanComponent$$Check 0x14cab78 is the only reader of the per-instance `flag` field
(int +0x10). On the supported special-battle path (InitFighters/Update) the prepared index
reaches it with:
  * AISystem.IsOnVehicle 0x148b7fc          flag0x4     - vehicle, rejected by nonvehicle
  * HeightSystem.Update  0x1595ba0          flag0x20    - modelled by the height phase
  * AISystem.GetMoveSpeed 0x148c3cc         flag0x208000 - FighterSystem.UpdateMoving 0x15845dc
                                                           -> AISystem.Move 0x148c2e0 -> GetMoveSpeed
  * AnimationSet.ChangeAnimation 0x165dea8  flag0x208000 - presentation only, no RNG/state
The remaining Check callers (AISystem.UpdateResident/WakeUp/SearchAndRescue/ScrMove/ScrGather/
CanRemoveResident, form.SubForm.*) are town-resident and UI code, not reachable from the battle
fighter update. A missing humanFlags is the declared 0 default (HumanComponent..ctor 0x14cb02c),
a disclosed declaration, never captured source; any other bit fails closed.
"""
import json

from combat_initial_state import EVIDENCE, START_PROFILE_KINDS
from combat_run_manifest import support_contract
from combat_runtime_data import WORKSPACE_ROOT
from combat_scenario import load_scenario
from check_combat_sandbox import example

COM = WORKSPACE_ROOT / 'RE-evidence/20260912-combat'


def asm(name):
    return [line.rstrip() for line in
            (COM / f'{name}.asm').read_text(encoding='utf-8').splitlines()]


def check_bits(name, bit_lines):
    """The recorded flag-test immediate(s) precede the Check call in this function slice."""
    text = asm(name)
    call = next(i for i, line in enumerate(text) if line.endswith('0x14cab78 ecs.HumanComponent$$Check'))
    positions = [text.index(line) for line in bit_lines]
    assert positions == sorted(positions) and all(p < call for p in positions), (name, bit_lines)


def has(name, needle):
    assert needle in '\n'.join(asm(name)), (name, needle)


# 1. Every flag test reachable from the special battle, by recorded immediate bit.
check_bits('148b7fc', ['148b824: mov w1, #4', '148b828: mov x2, xzr'])
check_bits('1595ba0', ['1595cf4: mov w1, #0x20', '1595cf8: mov x2, xzr'])
check_bits('148c3cc', ['148c728: mov w1, #0x8000', '148c72c: movk w1, #0x20, lsl #16'])
check_bits('165dea8', ['165e0a4: mov w1, #0x8000', '165e0a8: movk w1, #0x20, lsl #16'])
has('148c2e0', 'bl #0x148c3cc ecs.AISystem$$GetMoveSpeed')
has('15845dc', 'ecs.AISystem$$Move')


# 2. Support contract: verified bits pass, a missing field is disclosed, all others fail closed.
def profiled():
    data = example()
    data['startProfile'] = dict(kind=START_PROFILE_KINDS[0], enemySpawnCell=[0, 0])
    return data


def contract(flags=None):
    data = profiled()
    if flags is not None:
        for unit in data['ownUnits']:
            if unit['human']:
                unit['humanFlags'] = flags
    got = support_contract(load_scenario(data))
    cond = next(c for c in got['conditionalSimulation']['conditions'] if c['name'] == 'human_flags')
    return cond, got


# The overall `supported` flag also reflects unrelated conditions (e.g. the finish-policy gate),
# so this asserts the human_flags condition entry and its disclosure directly.
cond, base = contract()
assert cond['met'] is True, cond
assert 'human_flags' in base['blockers']
assert 'human_flags' not in base['conditionalSimulation']['unmetConditions']

for flags in (0, 0x20):
    cond, got = contract(flags)
    assert cond['met'] is True, (hex(flags), cond)
    assert 'human_flags' not in got['blockers']

for flags in (0x208000, 0x1000, 0x100, -2147483648):
    cond, got = contract(flags)
    assert cond['met'] is False, (hex(flags), cond)
    assert 'human_flags' in got['conditionalSimulation']['unmetConditions'], hex(flags)

report = dict(nativeReadSites=4, verifiedBits=['0x4', '0x20'], failClosedMask='~0x24',
              moveSpeedBit='0x208000',
              scope='HumanComponent.flag read closure over the prepared index; town-resident and UI '
                    'Check callers stay outside the battle fighter update',
              cases=['default 0 disclosed', 'explicit 0', 'explicit 0x20', 'fail 0x208000',
                     'fail 0x1000', 'fail 0x100', 'fail -2147483648'])
(EVIDENCE / 'human-flags-checks.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
print(json.dumps(report))
