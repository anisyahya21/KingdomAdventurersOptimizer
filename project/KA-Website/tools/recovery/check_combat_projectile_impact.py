"""Native effect creation component order and resource-derived lifetime."""
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

from combat_projectiles import process_projectile_impact
from itertools import product
entity,owner,projectile,position,cell,attack,skill,image,seb,mapchip,mapdata,map_obj,system,delegate=[0x10004000+i*0x1000 for i in range(14)]
w64(entity+0x30,0x10003000);w64(projectile+0x38,owner);w64(system+0x38,delegate);w64(delegate+0x18,0x30000100)
w32(map_obj+0x20,24);w32(map_obj+0x24,24);w32(cell+0x10,1);w32(cell+0x14,3)
uc.mem_write(position+0x10,struct.pack('<3f',49.,0.,97.))
uc.mem_write(0x316ba06,b'\x01')
for got in (0x2f5fb68,0x2f61e68):w64(got,0x10001000)
w64(0x10001000,0x10002000);w32(0x100020e0,1)
events=[]
def floats(n):return tuple(struct.unpack('<f',struct.pack('<I',reg('S'+str(i))))[0] for i in range(n))
def external(machine,address,size,user):
 if 0x168f034<=address<0x168f47c:return
 result=0
 if address==0x147e7e0:result=destroyed
 elif address==0x146cb2c:result=has_cell
 elif address==0x1471978:result=projectile
 elif address==0x1477df0:result=owner_kind!=0 and owner_alive
 elif address==0x146ee7c:result=owner_kind==1
 elif address==0x1470c84:result=owner_kind==2
 elif address==0x146bf3c:result=has_attack
 elif address==0x191de04:result=system
 elif address==0x1471498:result=position
 elif address==0x1475b3c:result=map_obj
 elif address==0x30000100:result=2+(4<<32)
 elif address==0x146beb4:result=attack
 elif address==0x14c6560:result=skill if impact is not None else 0
 elif address==0x168ebfc:events.append(('damage',reg('W0'),reg('W1'),reg('X2'),reg('X3')))
 elif address==0x146caa4:result=cell
 elif address==0x15b00c8:events.append(('terrain',reg('W1'),reg('W2')));result=mapchip if category is not None else 0
 elif address==0x146fcd8:result=mapchip
 elif address==0x147dbd0:result=mapdata
 elif address==0x1479108:events.append(('effect',reg('W1'),floats(3)))
 elif address==0x146f0bc:result=image
 elif address==0x14cbde8:result=texture
 elif address==0x14721c8:result=seb
 elif address==0x14709bc:events.append(('fade',reg('W1'),reg('W2'),reg('W3'),reg('W4'),bool(reg('W6')),bool(reg('W7')),r32(reg('SP'))))
 elif address==0x146e6bc:events.append(('garbage',reg('W1')))
 else:raise AssertionError(hex(address))
 machine.reg_write(UC_ARM64_REG_X0,int(result)&0xffffffffffffffff);machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
checks=0
for destroyed,has_cell,has_attack,owner_kind,owner_alive,category,impact,texture,seb_id in product((False,True),(False,True),(False,True),range(4),(False,True),(None,0,9,49,1,50),(None,-1,20),(27,28),(0,27)):
 events=[];w32(mapdata+0x24,category or 0);w32(skill+0x60,impact or 0);w32(seb+0x14,seb_id)
 run(0x168f034,x0=entity,w1=1)
 expected=[];unit={}
 def terrain(u):expected.append(('terrain',1,3));return category
 def effect(u,n,y):expected.append(('effect',n,(49.,y,97.)))
 def fade(u,m):expected.append(('fade',m['type'],m['angle'],m['frame'],m['duration'],m['destroy_on_finish'],m['loop'],m['alpha']))
 fx={'destroyed':lambda u:destroyed,'has_cell':lambda u:has_cell,'owner':lambda u:owner,
     'alive':lambda o:owner_kind!=0 and owner_alive,'fighter':lambda o:owner_kind in (1,2),
     'has_attack':lambda u:has_attack,'skill':lambda u:{'impactImg':impact} if impact is not None else None,
     'damage_at_position':lambda u,o,s:expected.append(('damage',2,4,owner,skill if s is not None else 0)),
     'terrain_at_cell':terrain,'effect':effect,'texture':lambda u:texture,'seb':lambda u:seb_id,
     'fade':fade,'garbage':lambda u,n:expected.append(('garbage',n))}
 process_projectile_impact(unit,fx)
 assert events==expected,(destroyed,has_cell,has_attack,owner_kind,owner_alive,category,impact,texture,seb_id,events,expected)
 checks+=1
result=dict(nativeProjectileImpactCases=checks,scope='Full original ProcessProjectileImpact dispatch; owner/attack gates, actual-position damage vs stored-cell terrain, optional effects, bow fade and garbage cleanup',limitations=['Component getters, owner existence and cell conversion supplied; damage/effect/component mutation callbacks recorded rather than full world'])
(EVIDENCE/'projectile-impact-checks.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8');print(json.dumps(result))
