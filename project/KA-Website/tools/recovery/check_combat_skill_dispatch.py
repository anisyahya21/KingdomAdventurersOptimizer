"""Native UseSkill branch dispatch, ordering and per-cell sound calls."""
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

from combat_skill_use import use_fighter_skill
from combat_skills import play_skill_sound
from combat_resolution import reflected_attack_result
from itertools import product
caster,target,skill,mp,array,world,enumerable,enumerator,klass=[0x10004000+i*0x1000 for i in range(9)]
w64(caster+0x30,world);w64(enumerable,klass);w64(enumerator,klass)
for flag in (0x316b483,0x316b482):uc.mem_write(flag,b'\x01')
for got in (0x2f5b570,0x2f60038,0x2f60040,0x2f60078,0x2f5b960,0x2f5fd60,0x2f5f980,
            0x2f663f8,0x2f66410,0x2f66400,0x2f5c140,0x2f66408,0x2f5c138):w64(got,0x10001000)
w64(0x10001000,0x10002000);w32(0x100020e0,1);w64(0x100020b8,0x10003000)
w64(0x10003000,world);w32(0x10003008,100)
entries={0x15e12e8:0,0x15e1514:1,0x15e1570:2,0x15e1624:3,
         0x15e17c8:0,0x15e1838:1,0x15e1894:2,0x15e1940:3}
for index in range(4):w64(0x10013000+index*16,0x30000100+index*4)
events=[];cell_index=-1
def payload(address):return tuple(struct.unpack('<4Q',uc.mem_read(address,32)))
def external(machine,address,size,user):
    global cell_index
    if address==0x15e0ea4:
        # Projectile branch was checked separately. Here only its selection is checked.
        events.append(('projectiles',));machine.reg_write(UC_ARM64_REG_PC,0x15e1734);return
    if any(a<=address<b for a,b in ((0x15e0ad4,0x15e1a14),(0x16828c8,0x1682910),(0x22141b8,0x22141d8))):return
    value=0
    if address==0x15df0e0:events.append(('eligible',reg('W3')));value=eligible
    elif address in (0x1471200,0x14c89d0):value=mp
    elif address==0x16323a4:events.append(('cost',));value=7
    elif address==0x15e0620:events.append(('balloon',))
    elif address==0x1477df0:value=1
    elif address==0x146fd60:value=0
    elif address in (0x16657b0,0x13eb3c8):value=max(i32(reg('W0')),i32(reg('W1')))
    elif address==0x168e3c4:
        events.append(('cure',));uc.mem_write(reg('X8'),struct.pack('<4Q',1,2,3,4))
    elif address==0x168d1e8:
        events.append(('attack',));uc.mem_write(reg('X8'),struct.pack('<4Q',5,6,7,8))
    elif address==0x12d2214:value=array;w32(array+0x18,1)
    elif address==0x191e2ec:events.append(('send',reg('W1'),payload(reg('X2')+0x20)))
    elif address==0x144cdc8:events.append(('sound_roll',reg('W0')));value=1
    elif address==0x1666d60:events.append(('play',reg('W1')))
    elif address==0x161c200:value=int(bool(record['flags']&reg('W1')))
    elif address in (0x15e099c,0x15e0718):
        events.append(('cells','line' if address==0x15e099c else 'band'));value=enumerable;cell_index=-1
    elif address==0x1332890:value=0x10013000+entries[reg('LR')]*16
    elif address==0x30000100:value=enumerator
    elif address==0x30000104:cell_index+=1;value=int(cell_index<len(cells))
    elif address==0x30000108:value=(cells[cell_index][0]&0xffffffff)|((cells[cell_index][1]&0xffffffff)<<32)
    elif address==0x3000010c:pass
    elif address in (0x168ebfc,0x168ee44):events.append(('damage_cell' if address==0x168ebfc else 'buff_cell',(i32(reg('W0')),i32(reg('W1')))))
    elif address==0x15e0a14:events.append(('cell_effect',(i32(reg('W1')),i32(reg('W2')))))
    else:raise AssertionError(hex(address))
    machine.reg_write(UC_ARM64_REG_X0,int(value)&0xffffffffffffffff);machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
records=[r for r in json.loads((EVIDENCE/'weapon-skill-profiles.json').read_text(encoding='utf-8'))['skills'] if r['flags']&8]
checks=0
for record,eligible,index,cells in product(records,(False,True),(0,1),((),((0,3),(1,3),(2,3)))):
    events=[];w32(mp+0x14,10);w32(mp+0x18,10)
    w32(skill+0x28,record['category']);w32(skill+0x2c,record['type']);w32(skill+0x30,record['value'])
    try:run(0x15e0c2c,x0=caster,x1=target,x2=skill,w3=index,w4=50)
    except Exception:
        print(record['id'],hex(reg('PC')));raise
    expected=[]
    def can_use(check):expected.append(('eligible',int(check)));return eligible
    def get_cells(kind):expected.append(('cells',kind));return cells
    def cure():expected.append(('cure',));return (1,2,3,4)
    def attack():expected.append(('attack',));return (5,6,7,8)
    def reflect():
        result=reflected_attack_result(caster,target,record,50)
        return tuple(struct.unpack('<4Q',struct.pack('<QQ??2xiQ',caster,target,True,False,result['damage'],skill)))
    def hits(rate):expected.append(('sound_roll',rate));return True
    use_fighter_skill(record,index,dict(can_use=can_use,pay_mp=lambda:expected.append(('cost',)),
        balloon=lambda:expected.append(('balloon',)),fire_projectiles=lambda:expected.append(('projectiles',)),
        cure=cure,attack=attack,reflect=reflect,send=lambda event,results:expected.append(('send',event,results[0])),
        cells=get_cells,damage_cell=lambda cell:expected.append(('damage_cell',cell)),
        buff_cell=lambda cell:expected.append(('buff_cell',cell)),cell_effect=lambda cell:expected.append(('cell_effect',cell)),
        sound=lambda:play_skill_sound(record,100,hits,lambda sound:expected.append(('play',sound)))))
    assert events==expected,(record['id'],eligible,index,cells,events,expected)
    assert r32(mp+0x14)==(3 if eligible and index==0 else 10)
    checks+=1
report=dict(nativeSkillDispatchChecks=checks,skillRows=len(records),
    scope='Original UseSkill dispatch and PlaySkillSe; all original flag8 skill rows, eligibility outcomes, first/later hit and empty/three-cell iteration',
    limitations=['Projectile branch body bypassed after native selection; eligibility, effect helpers, cell enumeration and audio playback supplied; no full world'])
(EVIDENCE/'skill-dispatch-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');print(json.dumps(report))

