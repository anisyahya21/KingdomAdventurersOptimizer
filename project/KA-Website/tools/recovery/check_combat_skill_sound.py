"""Native skill sound RNG gate and sound selection."""
import json
import struct
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE
from unicorn.arm64_const import *
from combat_initial_state import EVIDENCE, i32

binary = (EVIDENCE.parent / 'G2.1/39257e72291d/inputs/libil2cpp.so').read_bytes()
audit = json.loads((EVIDENCE / 'audit.json').read_text())
uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
for base, size in [(0x1000000, 0x2300000), (0x10000000, 0x100000),
                   (0x20000000, 0x10000), (0x30000000, 0x1000)]: uc.mem_map(base, size)
for entry in audit['methods']:
    start, end, offset = [int(entry[k], 16) for k in ['rva', 'end', 'offset']]
    uc.mem_write(start, binary[offset:offset+end-start])

def w32(a, n): uc.mem_write(a, struct.pack('<I', n & 0xffffffff))
def w64(a, n): uc.mem_write(a, struct.pack('<Q', n))
def r32(a): return struct.unpack('<i', uc.mem_read(a, 4))[0]
def reg(n): return uc.reg_read(globals()['UC_ARM64_REG_' + n])
def run(start, **regs):
    uc.reg_write(UC_ARM64_REG_SP, 0x20008000)
    uc.reg_write(UC_ARM64_REG_LR, 0x30000000)
    for name, value in regs.items():
        uc.reg_write(globals()['UC_ARM64_REG_' + name.upper()], value & 0xffffffffffffffff)
    uc.emu_start(start, 0x30000000, count=10000)
    assert reg('PC') == 0x30000000, hex(reg('PC'))
    return reg('W0')

from combat_skills import play_skill_sound
skill,caster=0x10004000,0x10005000
uc.mem_write(0x316b482,b'\x01')
for got in (0x2f5fd60,0x2f5b960,0x2f5f980):w64(got,0x10001000)
w64(0x10001000,0x10002000);w32(0x100020e0,1);w64(0x100020b8,0x10003000)
w64(0x10003000,0x10006000);w32(0x10003008,37)
events=[]
def external(machine,address,size,user):
    if 0x15e0ad4<=address<0x15e0c2c:return
    value=0
    if address==0x144cdc8:events.append(('roll',reg('W0')));value=success
    elif address==0x161c200:value=int(bool(record['flags']&reg('W1')))
    elif address==0x1666d60:events.append(('play',reg('W1'),reg('X2')))
    else:raise AssertionError(hex(address))
    machine.reg_write(UC_ARM64_REG_X0,value);machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
records=json.loads((EVIDENCE/'weapon-skill-profiles.json').read_text(encoding='utf-8'))['skills']
checks=0
for record in records:
    for success in (False,True):
        events=[];w32(skill+0x28,record['category']);w32(skill+0x2c,record['type'])
        run(0x15e0ad4,x0=skill,x1=caster)
        expected=[]
        def hits(rate):expected.append(('roll',rate));return success
        play_skill_sound(record,37,hits,lambda sound:expected.append(('play',sound,caster)))
        assert events==expected,(record['id'],success,events,expected)
        checks+=1
report=dict(nativeSkillSoundChecks=checks,
    scope='Original PlaySkillSe for all136 skill rows and both Lib.Hits outcomes',
    limitations=['Lib RNG result and AppData.PlaySe supplied; rendering/audio backend not executed'])
(EVIDENCE/'skill-sound-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');print(json.dumps(report))

