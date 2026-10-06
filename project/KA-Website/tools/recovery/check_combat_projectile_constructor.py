"""Original World.CreateProjectile overloads, with component-add boundaries."""
from pathlib import Path
exec(Path(__file__).with_name('check_combat_damage_wrapper.py').read_text(encoding='utf-8').split('from itertools import product')[0])
world,manager,entity,mapdata,skill=0x10004000,0x10005000,0x10006000,0x10007000,0x10008000
w64(world+0x30,manager);w32(mapdata+0x20,24);w32(mapdata+0x24,24)
events=[]
def external(machine,address,size,user):
    if 0x1479ad0<=address<0x1479c34:return
    if address==0x1474148:events.append(('entity',reg('W1'),reg('W2')));result=entity
    elif address==0x1475b3c:result=mapdata
    elif address==0x1471530:events.append(('position',));result=entity
    elif address==0x146cb3c:events.append(('cell',i32(reg('W1')),i32(reg('W2'))));result=entity
    elif address==0x14728f0:events.append(('speed',));result=entity
    elif address==0x1472260:events.append(('seb',reg('W1'),reg('W2'),reg('W3'),i32(reg('W4'))));result=entity
    elif address==0x146f154:events.append(('image',*[i32(reg('W'+str(i))) for i in range(1,7)]));result=entity
    elif address==0x146d370:events.append(('depth',reg('W1'),reg('W2')));result=entity
    elif address==0x146bf4c:events.append(('attack',reg('W1')));result=entity
    else:raise AssertionError(hex(address))
    machine.reg_write(UC_ARM64_REG_X0,result);machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
def bits(v):return struct.unpack('<I',struct.pack('<f',v))[0]
cases=0
for sid,seb,img in ((1,27,27),(2,28,28),(17,2,3)):
    for x,z,cx,cz in ((0.,48.,0,2),(-25.,-23.,-1,0),(240.,72.,10,3)):
        w32(skill+0x18,sid);w32(skill+0x5c,seb);w32(skill+0x58,img);events=[]
        run(0x1479c08,x0=world,x1=skill,s0=bits(x),s1=bits(15.),s2=bits(z))
        assert events==[('entity',0,0),('position',),('cell',cx,cz),('speed',),('seb',28,seb,0,-1),
                       ('image',img,-1,-1,-1,-1,-1),('depth',0,0),('attack',sid)],events
        cases+=1
report=dict(nativeConstructorCases=cases,scope='Original two overloads; entity allocation and component-add callees supplied')
(EVIDENCE/'projectile-constructor-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');print(json.dumps(report))
