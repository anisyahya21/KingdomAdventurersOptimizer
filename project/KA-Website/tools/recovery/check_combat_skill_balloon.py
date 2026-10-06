"""Original CreateSkillBalloon dispatch arguments, including allegiance and lifetime."""
from pathlib import Path
exec(Path(__file__).with_name('check_combat_attack_effects.py').read_text().split('from itertools import product')[0])
from itertools import product
from combat_effects import skill_balloon_spec,healing_effect_spec,cell_skill_effect_spec
caster,position,skill,world=0x10004000,0x10004100,0x10004200,0x10004300
w64(caster+0x30,world)
def external(machine,address,size,user):
    if 0x15e0620<=address<0x15e0718 or 0x14f28a4<=address<0x14f29b8 or 0x15e0a14<=address<0x15e0ad4:return
    value=0
    if address==0x1471498:value=position
    elif address==0x146b824:value=int(ally)
    elif address==0x147e7e0:value=int(destroyed)
    elif address==0x1475b3c:value=world
    elif address==0x1478ea0:
        sp=reg('SP')
        actual.append(dict(type=reg('W1'),value1=i32(reg('W2')),value2=r32(sp+32),res=reg('W3'),seb=reg('W4'),
            depth=bool(reg('W5')),max_frame=reg('W7'),frame=r32(sp),loop=bool(reg('W6')),
            parent=None,image=r32(sp+16),animate=bool(r32(sp+24)),scale=r32(sp+40),
            position=tuple(struct.unpack('<f',struct.pack('<I',reg('S'+str(i))))[0] for i in range(3))))
    else:raise AssertionError(hex(address))
    machine.reg_write(UC_ARM64_REG_X0,value);machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
checks=0
for ally,sid,pos in product((False,True),(0,26,37,110),((24.,5.,48.),(-24.5,-50.,96.25))):
    w32(skill+0x18,sid);uc.mem_write(position+0x10,struct.pack('<3f',*pos));actual=[]
    run(0x15e0620,x0=caster,x1=skill)
    assert actual==[skill_balloon_spec(sid,pos,ally)],actual
    checks+=1
balloon_checks=checks
results=0x10005000;w64(caster+0x10,world)
for destroyed,amount,count in product((False,True),(0,1,999,-1),(0,1,3)):
    w32(results+0x18,count)
    for index in range(count):
        w64(results+0x28+index*32,caster);w32(results+0x38+index*32,amount)
    actual=[];run(0x14f28a4,x0=caster,x1=results)
    assert actual==([] if destroyed else [healing_effect_spec(amount,pos)]*count)
    checks+=1
healing_checks=checks-balloon_checks
w32(world+0x20,24);w32(world+0x24,24)
for img,seb,cell in product((-1,0,5),(0,1,7),((0,0),(-2,5),(2147483647,-2147483648))):
    w32(skill+0x60,img);w32(skill+0x64,seb);actual=[]
    run(0x15e0a14,x0=world,w1=cell[0],w2=cell[1],x3=skill)
    assert actual==[cell_skill_effect_spec(dict(img=img,seb=seb),cell)]
    checks+=1
report=dict(nativeSkillBalloonCases=balloon_checks,nativeHealingEffectCases=healing_checks,
            nativeCellEffectCases=checks-balloon_checks-healing_checks,
            scope='Original callers; full CreateEffect arguments compared; allocation tested separately')
(EVIDENCE/'skill-balloon-checks.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report))
