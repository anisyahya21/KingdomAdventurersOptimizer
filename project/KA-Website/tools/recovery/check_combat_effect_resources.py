"""Original APK effect SEB headers against the native loader's header branch.

Executes through maxFrame assignment, stopping before sprite allocation/decoding.
Stream reads and object allocation are supplied; not a full resource loader test.
"""
import hashlib
import json
import struct
from pathlib import Path
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE
from unicorn.arm64_const import *

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'RE-evidence/20260912-combat'
ASSETS = ROOT / 'RE-evidence/20260911-building/placement/effect-original'
binary = (ROOT / 'RE-evidence/G2.1/39257e72291d/inputs/libil2cpp.so').read_bytes()
audit = json.loads((OUT / 'audit.json').read_text(encoding='utf-8'))
entry = next(e for e in audit['methods'] if int(e['rva'], 16) == 0x234f940)
uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
for address, size in [(0x1000000, 0x2300000), (0x10000000, 0x10000), (0x20000000, 0x10000)]:
    uc.mem_map(address, size)
start, end, offset = (int(entry[k], 16) for k in ['rva', 'end', 'offset'])
uc.mem_write(start, binary[offset:offset+end-start])
uc.mem_write(0x316debf, b'\x01')
uc.mem_write(0x2f77ab0, struct.pack('<Q', 0x10001000))
uc.mem_write(0x10001000, struct.pack('<Q', 0x10002000))
data, cursor = b'', 0

def hook(machine, address, size, user):
    global cursor
    if address == 0x234faf0:
        machine.emu_stop()
        return
    if start <= address < end:
        return
    value = 0
    if address in (0x2388e44, 0x2388f00):
        length = 1 if address == 0x2388e44 else 2
        value = int.from_bytes(data[cursor:cursor+length], 'big', signed=True)
        cursor += length
    elif address == 0x12d23b8:
        value = 0x10003000
    else:
        assert address in (0x234a294, 0x2388d88), hex(address)
    machine.reg_write(UC_ARM64_REG_X0, value & 0xffffffffffffffff)
    machine.reg_write(UC_ARM64_REG_PC, machine.reg_read(UC_ARM64_REG_LR))

uc.hook_add(UC_HOOK_CODE, hook)
bindings = dict(line.split('\t', 1) for line in (ASSETS / 'seb.inf').read_text(encoding='utf-8-sig').splitlines())
rows = []
for index, filename in bindings.items():
    data = (ASSETS / filename).read_bytes()
    cursor = 0
    layers, maximum = struct.unpack_from('>Hh', data)
    assert data[0] < 128, 'Only original format-zero files decoded here'
    pos = 4
    frame_records = []
    for _ in range(layers):
        count, reserved = struct.unpack_from('>hh', data, pos)
        assert count >= 0
        pos += 4
        frames = [struct.unpack_from('>10h', data, pos + n*20) for n in range(count)]
        pos += count*20
        # Some original resources contain keys beyond their declared duration.
        # Preserve the header; do not silently replace it with the largest key.
        assert all(f[0] >= 0 for f in frames)
        frame_records.append(len(frames))
    assert pos == len(data)
    uc.reg_write(UC_ARM64_REG_SP, 0x20008000)
    uc.reg_write(UC_ARM64_REG_X0, 0x10004000)
    uc.reg_write(UC_ARM64_REG_X1, 0x10005000)
    uc.emu_start(start, end, count=10000)
    assert uc.reg_read(UC_ARM64_REG_PC) == 0x234faf0 and cursor == 4
    actual = struct.unpack('<i', uc.mem_read(0x1000401c, 4))[0]
    assert actual == maximum
    rows.append(dict(id=int(index), file=filename, layers=layers, records=frame_records,
                     maxFrame=maximum, defaultNonLoopLifetime=maximum-1,
                     sha256=hashlib.sha256(data).hexdigest()))

profiles = json.loads((OUT / 'weapon-skill-profiles.json').read_text(encoding='utf-8'))['skills']
by_id = {r['id']: r for r in rows}
skills = [dict(skillId=s['id'], impactSeb=s['impactSeb'], impactImg=s['impactImg'],
               impactLifetime=by_id[s['impactSeb']]['defaultNonLoopLifetime'],
               projectileSeb=s['seb'], projectileAnimationLength=by_id[s['seb']]['maxFrame'])
          for s in profiles if s['flags'] & 8]
assert len(skills) == 52 and all(s['impactLifetime'] == 9 for s in skills)
result = dict(nativeHeaderChecks=len(rows), combatSkillBindings=len(skills), resourceId=28,
              archiveSource='../20260911-building/placement/effect-source.json',
              resources=rows, skills=skills,
              limits=['Native header branch only; stream reads supplied; sprite decoder not executed.',
                      'Projectile animation length does not determine projectile travel or impact timing.',
                      'Table bindings do not imply every skill creates an impact on every dispatch branch.'])
(OUT / 'effect-resource-checks.json').write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
print(json.dumps({k: result[k] for k in ['nativeHeaderChecks', 'combatSkillBindings']}))
