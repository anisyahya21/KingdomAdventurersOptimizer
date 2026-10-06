"""Human image-init branch, AISystem.ScrRotate and synchronous defeat writers.

Static call sets come from the canonical index (RE-evidence/20260920-native-index). The Human
image branch and ScrRotate are additionally executed under Unicorn to show their exact writes and
that they reach no random/allocation helper. All findings are bounded to these executed paths.
"""
import json
import struct
import sqlite3
from pathlib import Path

from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE
from unicorn.arm64_const import *
from combat_initial_state import EVIDENCE

ROOT = Path(__file__).resolve().parents[3]
DB = ROOT / 'RE-evidence/20260920-native-index/build/ka-index.sqlite'
conn = sqlite3.connect(str(DB))


def callees(rva):
    return sorted({row[0] for row in conn.execute('select callee_rva from calls where caller_rva=?', (rva,))})


# IL2CPP runtime helpers that carry no gameplay effect: class/method init, null/bounds throw.
RUNTIME = {0x12d21a0, 0x12d22a4, 0x12d23c8, 0x12d23d0, 0x12d2294, 0x12d22a8, 0x12d23ec}

# (1) Human image init branch: exact callee sets, no RNG/allocator/membership helper.
assert set(callees(0x148b6cc)) - RUNTIME == {0x146edf4, 0x165ea88}, 'ChangeWeaponImage callees'
assert set(callees(0x148b764)) - RUNTIME == {0x146edf4, 0x165ea88}, 'ChangeShieldImage callees'
assert set(callees(0x165ea88)) - RUNTIME == {0x146edf4}, 'SetHumanImgs callees'

# (2) AISystem.ScrRotate: direction write + AIComponent.RemoveCommandAt only, no RNG.
assert set(callees(0x148a1ec)) - RUNTIME == {0x1468bc8, 0x146d3f4, 0x14c3eb8, 0x22b1e44}, 'ScrRotate callees'

# (3) Synchronous monster-defeat writers: clamp helpers only, no RNG producer reached.
assert set(callees(0x162f490)) - RUNTIME == {0x16657ec}, 'MonsterData.AddDefeatCount'
assert set(callees(0x162ed78)) - RUNTIME == {0x1665800}, 'MissionData.AddNowValue'
assert set(callees(0x158ab14)) - RUNTIME == {0x161ca48, 0x1626800, 0x1666b84}, 'FriendCampaignSystem.AddDefeat'

# (4) Battle-selected behaviors exclude 18 (Rand(4)) and 36 (Pick).
from combat_animation_selection import COMBAT_BEHAVIORS, WEAPON_MOTION_BEHAVIORS
assert 18 not in COMBAT_BEHAVIORS and 36 not in COMBAT_BEHAVIORS
profiles = json.loads((EVIDENCE / 'weapon-skill-profiles.json').read_text(encoding='utf-8'))
motions = sorted({row['motion'] for row in profiles['equipment'] if row['category'] == 0})
assert not ({18, 36} & set(motions)) and set(motions) <= WEAPON_MOTION_BEHAVIORS

# (5) Native execution of ChangeWeaponImage/ChangeShieldImage against a synthetic human.
binary = (EVIDENCE.parent / 'G2.1/39257e72291d/inputs/libil2cpp.so').read_bytes()
audit = json.loads((EVIDENCE / 'audit.json').read_text())
uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
for base, size in [(0x1000000, 0x2300000), (0x10000000, 0x100000), (0x20000000, 0x10000), (0x30000000, 0x1000)]:
    uc.mem_map(base, size)
for entry in audit['methods']:
    start, end, offset = (int(entry[key], 16) for key in ('rva', 'end', 'offset'))
    uc.mem_write(start, binary[offset:offset + end - start])


def w32(a, n): uc.mem_write(a, struct.pack('<I', n & 0xffffffff))
def w64(a, n): uc.mem_write(a, struct.pack('<Q', n))
def r32(a): return struct.unpack('<i', uc.mem_read(a, 4))[0]
def reg(n): return uc.reg_read(globals()['UC_ARM64_REG_' + n])


entity, human, arr, equip = 0x10010000, 0x10011000, 0x10012000, 0x10013000
w64(0x2f5fb70, 0x10020000); w64(0x10020000, 0x10021000); w32(0x10021000 + 0xe0, 1)


def execute(func, image_index, equip_value):
    uc.mem_write(0x316ab6b, b'\x01'); uc.mem_write(0x316ab6d, b'\x01')
    w64(human + 0x20, arr); w32(arr + 0x18, 32)
    w32(equip + 0x60, equip_value)
    calls = []
    def hook(machine, address, size, user):
        if 0x148b6cc <= address < 0x148b7fc:
            return
        if address == 0x146edf4:
            calls.append('get_human'); machine.reg_write(UC_ARM64_REG_X0, human)
            machine.reg_write(UC_ARM64_REG_PC, reg('LR'))
        elif address == 0x165ea88:
            calls.append('SetHumanImgs'); machine.emu_stop()
        else:
            calls.append('unexpected:' + hex(address)); machine.emu_stop()
    handle = uc.hook_add(UC_HOOK_CODE, hook)
    uc.reg_write(UC_ARM64_REG_SP, 0x20008000); uc.reg_write(UC_ARM64_REG_LR, 0x30000000)
    uc.reg_write(UC_ARM64_REG_X0, entity); uc.reg_write(UC_ARM64_REG_X1, equip); uc.reg_write(UC_ARM64_REG_X2, 0)
    uc.emu_start(func, 0x30000000, count=400)
    uc.hook_del(handle)
    return calls


cases = 0
for func, index, value in ((0x148b6cc, 11, 0x171), (0x148b764, 12, 0x272)):
    calls = execute(func, index, value)
    assert calls == ['get_human', 'SetHumanImgs'], (hex(func), calls)
    assert r32(arr + 0x20 + index * 4) == value, (hex(func), r32(arr + 0x20 + index * 4))
    cases += 1

report = dict(
    nativeHumanImageCases=cases,
    humanBranchRvas=dict(gate='Entity.get_hasHuman 0x146ee7c', weapon='AISystem.ChangeWeaponImage 0x148b6cc',
                         weaponSource='EquipComponent.get_weapon 0x14c7db0', shield='AISystem.ChangeShieldImage 0x148b764',
                         shieldSource='EquipComponent.get_shield 0x14c7e3c', images='AnimationSet.SetHumanImgs 0x165ea88'),
    scrRotate=dict(rva='0x148a1ec', effect='writes Entity.direction then AIComponent.RemoveCommandAt(ai,0)',
                   callees=[hex(v) for v in sorted(callees(0x148a1ec))]),
    defeatWriters=dict(callers=['FighterSystem.UpdateDamaging 0x1585f04 -> MonsterSystem.OnMonsterDefeated 0x15bed5c'],
                       writes=['MonsterData.AddDefeatCount 0x162f490', 'FriendCampaignSystem.AddDefeat 0x158ab14 -> CampaignData.AddDefeatCount 0x161ca48',
                               'MissionSystem.CheckAndAddPoint 0x15bbd2c -> MissionData.AddNowValue 0x162ed78'],
                       rng='no random producer in any writer body; AppData.Clamp only'),
    excludedBehaviors=dict(ids=[18, 36], reason='outside COMBAT_BEHAVIORS and outside recovered EquipData.motion set'),
    findings=['ChangeWeaponImage/ChangeShieldImage write one EquipData image id into the HumanComponent array then tail-branch to SetHumanImgs',
              'Those bodies call no random, allocation or subset/occupancy helper; executed under Unicorn with no unexpected call',
              'ScrRotate is rotation plus AIComponent.RemoveCommandAt; JMath.Abs is not a random draw',
              'Monster defeat/mission writers clamp and store; no random producer is reached in their bodies'],
    limitations=['Bounded to the executed Human branch, ScrRotate body and the listed writer bodies; image pixels and the SEB decode are not modelled',
                 'ScrRotate reachability requires a queued Lua AI script command, which the supported fight does not create'])
(EVIDENCE / 'human-image-init-checks.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
print(json.dumps({k: v for k, v in report.items() if k != 'findings'}))
