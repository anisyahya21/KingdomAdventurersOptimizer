"""Clone path carries SkillComponent.invokingSkills; InitFighters never resets it.

Three bounded native facts, all from the frozen binary:
1. BaseEntity.CopyComponents 0x147e2d8 is a ByteArrayOutputStream -> ToByteArray ->
   ByteArrayInputStream -> ComponentFactory.Create -> Deserialize/DeserializeExtra round trip,
   so cloning a component means running that component's own Serialize/Deserialize.
2. SkillComponent.Serialize 0x14d1a50 writes invokingSkills (+0x28) as the last field and
   SkillComponent.Deserialize 0x14d1b10 restores it to +0x28, so in-flight invocations survive
   the clone instead of being reset.
3. The executed InitFighters call set contains no SkillComponent address, so initializing
   fighters cannot reset or rewrite invokingSkills.
"""
import json
import struct
from pathlib import Path
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE
from unicorn.arm64_const import *
from combat_initial_state import EVIDENCE, i32, init_position, NATIVE_INIT_OVERWRITTEN_BOARD_KEYS

recovery = Path(__file__).resolve().parent

# --- 1. Static instruction audit of the clone mechanism -------------------------------------
clone_asm = (EVIDENCE / '147e2d8.asm').read_text(encoding='utf-8').splitlines()
expected_calls = [
    ('0x2388b00', 'java.io.ByteArrayOutputStream$$.ctor'),
    ('0x2388c60', 'java.io.ByteArrayOutputStream$$ToByteArray'),
    ('0x23888e4', 'java.io.ByteArrayInputStream$$.ctor'),
    ('0x14c39dc', 'ecs.ComponentFactory$$Create'),
]
call_sites = []
for line in clone_asm:
    if 'bl #' in line:
        site, target = line.split('bl #', 1)
        rva = target.split(' ', 1)[0].strip()
        name = target.split(' ', 1)[1].strip() if ' ' in target else ''
        call_sites.append((site.split(':')[0].strip(), rva, name))
wire_sequence = [rva for _, rva, _ in call_sites
                 if rva in {rva for rva, _ in expected_calls}]
assert wire_sequence == [rva for rva, _ in expected_calls], wire_sequence
# Virtual dispatch order: HasComponent 0x1c8, GetComponent 0x1f8, then the source component's
# Serialize 0x178 and SerializeExtra 0x198, then the fresh component's Deserialize 0x188 and
# DeserializeExtra 0x1a8, then the entity's per-component copy hook 0x188.
virtual_sites = [line.split(':')[0].strip() for line in clone_asm if 'blr ' in line]
assert len(virtual_sites) == 7, virtual_sites
dispatch_offsets = [line.split('[x8, #')[1].split(']')[0]
                    for line in clone_asm if 'ldp x9, x' in line and '[x8, #' in line]
assert dispatch_offsets == ['0x1c8', '0x1f8', '0x178', '0x198', '0x188', '0x1a8', '0x188'], dispatch_offsets
assert not any('SkillComponent' in name for _, _, name in call_sites)

# --- 2/3. Execute the real SkillComponent Serialize/Deserialize under Unicorn -----------------
binary = (EVIDENCE.parent / 'G2.1/39257e72291d/inputs/libil2cpp.so').read_bytes()
audit = json.loads((EVIDENCE / 'audit.json').read_text())
uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
for base, size in [(0x1000000, 0x2300000), (0x10000000, 0x100000), (0x20000000, 0x10000),
                   (0x30000000, 0x1000)]: uc.mem_map(base, size)
for entry in audit['methods']:
    start, end, offset = [int(entry[k], 16) for k in ['rva', 'end', 'offset']]
    uc.mem_write(start, binary[offset:offset+end-start])

def w32(a, n): uc.mem_write(a, struct.pack('<I', n & 0xffffffff))
def w64(a, n): uc.mem_write(a, struct.pack('<Q', n))
def r64(a): return struct.unpack('<Q', uc.mem_read(a, 8))[0]
def s32(a): return struct.unpack('<i', uc.mem_read(a, 4))[0]
def reg(n): return uc.reg_read(globals()['UC_ARM64_REG_' + n])
def run(start, **regs):
    uc.reg_write(UC_ARM64_REG_SP, 0x20008000)
    uc.reg_write(UC_ARM64_REG_LR, 0x30000000)
    for name, value in regs.items():
        uc.reg_write(globals()['UC_ARM64_REG_' + name.upper()], value & 0xffffffffffffffff)
    uc.emu_start(start, 0x30000000, count=2000)
    assert reg('PC') == 0x30000000, hex(reg('PC'))
    return reg('W0')

# Class-init gate bytes and static slots the two methods read (same pattern as check_combat_clone).
uc.mem_write(0x316ad5f, b'\x01')
uc.mem_write(0x316ad60, b'\x01')
w64(0x2f5b828, 0x10001000)          # IComponent class
w64(0x10001000, 0x10001100)         # IComponent class object
w32(0x10001100 + 0xe0, 1)           # monitor: already initialized
w64(0x2f614a0, 0x10001200)         # SkillComponent class (Serialize literal pool)
w64(0x2f614a8, 0x10001280)         # SkillComponent class (Deserialize literal pool)
w64(0x10001200, 0x10001300)         # InvokingSkill class token
w64(0x10001280, 0x10001300)

skill, stream = 0x10002000, 0x10003000
data_ids, invocation_levels, invoking_skills = 0x10004000, 0x10004100, 0x10004200
records = []
def serialize_hook(machine, address, size, user):
    if 0x14d1a50 <= address < 0x14d1b10: return
    if address == 0x147ddfc: records.append(('baseSerialize', reg('X0'), reg('X1')))
    elif address == 0x23ae20c: records.append(('writeByte', i32(reg('W1'))))
    elif address == 0x1455924: records.append(('writeIntList', reg('X1')))
    elif address == 0x1902e00: records.append(('writeList', reg('X1'), reg('X2')))
    else: raise AssertionError(hex(address))
    machine.reg_write(UC_ARM64_REG_PC, reg('LR'))
hook = uc.hook_add(UC_HOOK_CODE, serialize_hook)
w32(skill + 0x10, 3); w64(skill + 0x18, data_ids); w64(skill + 0x20, invocation_levels)
w64(skill + 0x28, invoking_skills)
run(0x14d1a50, x0=skill, x1=stream)
uc.hook_del(hook)
assert records == [('baseSerialize', skill, stream), ('writeByte', 3), ('writeIntList', data_ids),
                   ('writeIntList', invocation_levels), ('writeList', invoking_skills, 0x10001300)], records

read_values = {}
def deserialize_hook(machine, address, size, user):
    if 0x14d1b10 <= address < 0x14d1bd4: return
    if address == 0x147de00: records.append(('baseDeserialize', reg('X0'), reg('X1')))
    elif address == 0x23ac6e4:
        records.append(('readByte',)); machine.reg_write(UC_ARM64_REG_W0, read_values['maxSlotNum'])
    elif address == 0x1455af4:
        value = read_values['lists'].pop(0); records.append(('readIntList',)); machine.reg_write(UC_ARM64_REG_X0, value)
    elif address == 0x1901a60:
        records.append(('readList', reg('X1'))); machine.reg_write(UC_ARM64_REG_X0, read_values['invoking'])
    else: raise AssertionError(hex(address))
    machine.reg_write(UC_ARM64_REG_PC, reg('LR'))
records.clear()
read_values = dict(maxSlotNum=0xfe, lists=[data_ids, invocation_levels], invoking=invoking_skills)
uc.mem_write(skill, bytes(0x40))
hook = uc.hook_add(UC_HOOK_CODE, deserialize_hook)
run(0x14d1b10, x0=skill, x1=stream)
uc.hook_del(hook)
assert records == [('baseDeserialize', skill, stream), ('readByte',), ('readIntList',),
                   ('readIntList',), ('readList', 0x10001300)], records
assert s32(skill + 0x10) == -2, s32(skill + 0x10)              # ReadByte is read signed
assert r64(skill + 0x18) == data_ids and r64(skill + 0x20) == invocation_levels
assert r64(skill + 0x28) == invoking_skills

# --- 4. InitFighters does not reach SkillComponent -------------------------------------------
initialization = json.loads((EVIDENCE / 'fighter-initialization-checks.json').read_text())
skill_component_range = (0x14cfc80, 0x14d2100)
touched = [a for a in initialization['stubbedCallAddresses']
           if skill_component_range[0] <= int(a, 16) < skill_component_range[1]]
assert not touched, touched

# --- 5. Pre-placement source facts the runner must respect ------------------------------------
assert init_position([100, 100]) == [2400., 0., 2400.]
assert NATIVE_INIT_OVERWRITTEN_BOARD_KEYS == (4, 5, 6, 7, 8)

report = dict(
    cloneMechanism='wire round trip',
    serializeRecord=[list(r) for r in [('baseSerialize', 'SkillComponent', 'stream'), ('writeByte', 3),
        ('writeIntList', 'dataIds'), ('writeIntList', 'invocationLevels'), ('writeList', 'invokingSkills')]],
    deserializeRestores=dict(maxSlotNum=-2, dataIds=True, invocationLevels=True, invokingSkills=True),
    initFightersSkillComponentTouches=0,
    findings=['Entity.Clone deep-copies every component through its own Serialize/Deserialize, so '
              'invokingSkills is preserved by the clone, not reset by it',
              'InitFighters 0x1582a88/0x1582b10 executed call set contains no SkillComponent address; '
              'it overwrites BB4/5/6/7/8 and leaves every other source field (including invokingSkills) intact',
              'Clone-time extraValue/extraMaxValue are already covered by check_combat_clone round trips, and '
              'their overflow effect on starting stats by the native Param.GetValue/GetMaxValue cases'],
    limits=['Virtual Deserialize/DeserializeExtra and SerializeExtra dispatches are read from the clone '
            'instruction stream, not executed (the component vtable is supplied).',
            'Whether any later, still-untraced system appends to invokingSkills is not proven; only the '
            'clone and InitFighters steps are closed here.'])
(EVIDENCE / 'clone-skill-checks.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
print(json.dumps({k: report[k] for k in ('cloneMechanism', 'deserializeRestores',
                                         'initFightersSkillComponentTouches')}))
