"""Original ChangeAnimation combat clip/frame/restore behavior; image resources supplied."""
from pathlib import Path
exec(Path(__file__).with_name('check_combat_attack_effects.py').read_text().split('from itertools import product')[0])
from itertools import product
from combat_animation_selection import combat_clip,HUMAN_BASES,COMBAT_BEHAVIORS,WEAPON_MOTION_BEHAVIORS
entity,seb,animation,direction,human_obj,monster,job,style,empty,heads= [0x10010000+i*0x400 for i in range(10)]
statics={}
for i,got in enumerate((0x2f5fb70,0x2f5d0d0,0x2f5f410,0x2f5f430,0x2f5c538)):
    ptr=0x10001000+i*0x300;klass=ptr+0x40;static=ptr+0x140
    w64(got,ptr);w64(ptr,klass);w32(klass+0xe0,1);w64(klass+0xb8,static);statics[got]=static
uc.mem_write(0x316b873,b'\x01')
tables=0x10020000;ht=0x10020400;mt=0x10020800
w64(statics[0x2f5fb70],tables);w32(tables+0x18,2);w64(tables+0x20,ht);w64(tables+0x28,mt)
for addr,values in ((ht,HUMAN_BASES),(mt,[16 if i==4 else 0 for i in range(45)])):
    w32(addr+0x18,len(values))
    for i,v in enumerate(values):w32(addr+0x20+i*4,v)
w64(human_obj+0x20,style);w32(style+0x18,32);w64(human_obj+0x30,empty);w64(human_obj+0x40,empty)
w32(empty+0x18,0);w64(job+0x68,heads);w32(heads+0x18,1);w32(heads+0x20,77);w32(human_obj+0x58,0)
for off in (0,8,16,24):
    a=0x10022000+off*32;w64(statics[0x2f5f430]+off,a);w32(a+0x18,1);w64(a+0x20,empty)
def external(machine,address,size,user):
    if 0x165dea8<=address<0x165e91c:return
    result=0
    if address==0x1472250:result=1
    elif address==0x146ee7c:result=int(human)
    elif address==0x1470c84:result=int(not human)
    elif address==0x1470bfc or address==0x14cdb94:result=monster
    elif address==0x146edf4:result=human_obj
    elif address==0x14caa70:result=job
    elif address==0x165e91c:result=hp
    elif address==0x14cab78:result=int(special)
    elif address==0x148b7fc:result=0
    elif address==0x165ea64:remaps.append(reg('W0'));result=0
    elif address==0x146b9d8:result=animation
    elif address==0x14721c8:result=seb
    elif address==0x146d3f4:result=direction
    else:raise AssertionError(hex(address))
    machine.reg_write(UC_ARM64_REG_X0,result&0xffffffffffffffff);machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
checks=0
for human,behavior,dir_,hp,size,special in product((False,True),sorted(COMBAT_BEHAVIORS),range(4),(0,20,21,100),(0,3),(False,True)):
    w32(monster+0x34,size);w32(direction+0x10,dir_);w32(seb+0x14,123);w32(seb+0x18,55)
    w32(animation+0x10,4);w32(animation+0x14,36);remaps=[]
    run(0x165dea8,x0=entity,w1=behavior,w2=0)
    expected=combat_clip(behavior,human,dir_,hp_rate=hp,monster_size=size,special_human=special)
    assert r32(seb+0x14)==expected,(human,behavior,dir_,hp,size,special,r32(seb+0x14),expected)
    assert r32(seb+0x18)==0 and r32(animation+0x14)==-1 and r32(animation+0x10)==4
    checks+=1
'''
PASS 16 COMMAND 16.5: every ordinary weapon motion the recovered equipment table uses must be
accepted by this runner and must have a recovered human animation base. The inventory is read from
weapon-skill-profiles.json (the recovered EquipData rows), not from the website catalog, and each
weapon behaviour is compared against the native ChangeAnimation exactly like the other behaviours.
'''
profiles=json.loads((EVIDENCE/'weapon-skill-profiles.json').read_text(encoding='utf-8'))
weapon_motions={}
for row in profiles['equipment']:
    if row['category']==0:weapon_motions.setdefault(row['motion'],[]).append(row['id'])
missing=[motion for motion in sorted(weapon_motions) if motion not in WEAPON_MOTION_BEHAVIORS]
assert not missing,('equipment motions outside the integrated weapon behaviours',missing)
no_base=[motion for motion in sorted(weapon_motions) if not 0<=motion<len(HUMAN_BASES)]
assert not no_base,('equipment motions without a recovered human animation base',no_base)
for motion in sorted(weapon_motions):
    for direction in range(4):
        expected=combat_clip(motion,True,direction)
        assert expected==HUMAN_BASES[motion]+direction,(motion,direction,expected)
weapon_cases=len(weapon_motions)*4
report=dict(nativeCombatAnimationCases=checks,weaponMotionCases=weapon_cases,
    weaponMotions={str(motion):dict(records=len(ids),examples=ids[:3],base=HUMAN_BASES[motion],
                                    behavior='accepted') for motion,ids in sorted(weapon_motions.items())},
    scope='Original ChangeAnimation with ordinary non-vehicle inputs; full branch executes, image action mapping/arrays supplied; no random helper permitted',
    limitations=['Human image composition and source flags are not validated by the clip comparison; restore=true and noncombat motions excluded',
                 'A recovered base value is the clip identity the simulator writes; whether each base has a decoded SEB file belongs to the visual pass, not to this combat runner'])
(EVIDENCE/'animation-selection-checks.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
