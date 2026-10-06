"""Native SkillSystem.FireProjectile argument wiring and per-shot sound call."""
from pathlib import Path
exec(Path(__file__).with_name('check_combat_damage_wrapper.py').read_text(encoding='utf-8').split('from itertools import product')[0])
from itertools import product
from combat_resolution import f32
uc.mem_write(0x316b486,b'\x01')
for got in (0x2f5f410,0x2f5e538):w64(got,0x10001000)
w64(0x10001000,0x10002000);w32(0x100020e0,1)
caster,projectile,world,skill=0x10010000,0x10011000,0x10012000,0x10013000
w64(caster+0x30,world)
def floats(n):return tuple(struct.unpack('<f',struct.pack('<I',reg('S'+str(i))))[0] for i in range(n))
events=[]
def external(machine,address,size,user):
    if 0x15e1c90<=address<0x15e1e4c:return
    result=0
    if address==0x1471498:result=reg('X0')+0x100
    elif address==0x1479c08:
        xyz=floats(3);events.append(('create',reg('X0'),reg('X1'),xyz))
        machine.mem_write(projectile+0x110,struct.pack('<3f',*xyz));result=projectile
    elif address==0x14607ec:machine.mem_write(reg('X0'),struct.pack('<3f',*floats(3)))
    elif address==0x14832d0:
        events.append(('fire',reg('X0'),reg('W1'),reg('X2'),reg('W3'),reg('X4'),floats(6)))
    elif address==0x15e0ad4:events.append(('sound',reg('X0'),reg('X1')))
    else:raise AssertionError(hex(address))
    machine.reg_write(UC_ARM64_REG_X0,result);machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
cases=0
for x,y,z,dx in product((-24.,0.,120.5),(0.,15.),(-48.,72.),(-24.,0.,48.)):
    position=(x,y,z);destination=(x+dx,y-5,z+72)
    uc.mem_write(caster+0x110,struct.pack('<3f',*position))
    events=[]
    regs={'s'+str(i):struct.unpack('<I',struct.pack('<f',v))[0] for i,v in enumerate(destination)}
    run(0x15e1c90,x0=caster,x1=skill,**regs)
    start=tuple(map(f32,(x,y+15,z)));end=tuple(map(f32,destination))
    assert events==[('create',world,skill,start),('fire',projectile,10,caster,1,skill,start+end),('sound',skill,caster)]
    cases+=1
report=dict(nativeSkillProjectileLaunchCases=cases,
    finding='Original launch uses caster actual XYZ with Y+15, speed10, supplied destination, caster owner and skill, followed by one sound call.',
    limits=['CreateProjectile allocation/components, Vector3 constructor, AISystem flight setup and sound body supplied.',
            'UseSkill area destination generation is separate; this check covers the launch wrapper.'])
(EVIDENCE/'skill-projectile-launch-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
print(json.dumps(report))
