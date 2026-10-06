"""Original active-skill filter, with dynamic-MP filtered invocation indexing."""
from pathlib import Path
exec(Path(__file__).with_name('check_combat_damage_wrapper.py').read_text(encoding='utf-8').split('from itertools import product')[0])
from combat_skill_selection import active_skill_infos
from combat_resolution import skill_mp_cost

uc.mem_write(0x316b490,b'\x01')
uc.mem_write(0x316ad4f,b'\x01')
w64(0x2f5d0d0,0x10001000);w64(0x10001000,0x10002000);w32(0x100020e0,1)
closure,entity,skill,component,levels=tuple(0x10004000+i*0x200 for i in range(5))
w64(closure+0x10,entity);w64(component+0x20,levels);w32(levels+0x18,6);w32(component+0x10,6)
invocation_levels=[0,1,2,0,1,2]
for i,v in enumerate(invocation_levels):w32(levels+0x20+i*4,v)
for got in (0x2f5efb8,):w64(got,0x10001000)
calls=[]
def external(machine,address,size,user):
    if (0x15e22f8<=address<0x15e240c or 0x1632094<=address<0x16320a0
        or 0x14d09d4<=address<0x14d0a50 or 0x1686390<=address<0x1686398):return
    result=0
    if address==0x161c200:result=bool(row['flags']&reg('W1'))
    elif address==0x166070c:
        assert reg('X0')==entity and reg('W1')==11;calls.append('mp');result=mp
    elif address==0x16323a4:calls.append('cost');result=cost
    elif address==0x14724cc:result=component
    elif address==0x1dc6274:result=invocation_levels[reg('W1')]
    else:raise AssertionError(hex(address))
    machine.reg_write(UC_ARM64_REG_X0,int(result)&0xffffffffffffffff);machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
rows=json.loads((EVIDENCE/'weapon-skill-profiles.json').read_text(encoding='utf-8'))['skills'];by_id={r['id']:r for r in rows}
checks=0
for row in rows:
    for training in (1,123,999):
        cost=skill_mp_cost(row['minMp'],row['maxMp'],training)
        for mp in (cost-1,cost,cost+1):
            calls=[];outcome=bool(run(0x15e22f8,x0=closure,x1=skill))
            expected=bool(row['flags']&8 and not row['flags']&32 and mp>=cost)
            assert outcome==expected
            assert calls==(['mp','cost'] if row['flags']&8 and not row['flags']&32 else [])
            checks+=1
examples=[];owned=[by_id[i] for i in (26,110,25,24,23,22)]
for mp in (0,14,23,37,46,100):
    actual=[]
    for row in owned:
        cost=skill_mp_cost(row['minMp'],row['maxMp'],123)
        if bool(run(0x15e22f8,x0=closure,x1=skill)):
            w32(skill+0x18,row['id'])
            run(0x15e23b0,x0=closure,x1=skill,w2=len(actual))
            packed=reg('X0');actual.append((packed&0xffffffff,packed>>32))
    expected=[(r['id'],level) for r,level in active_skill_infos(owned,invocation_levels,mp,lambda r:skill_mp_cost(r['minMp'],r['maxMp'],123))]
    assert actual==expected
    examples.append(dict(mp=mp,skillAndInvocationLevel=actual))
report=dict(nativeActiveFilterCases=checks,composedNativeIndexCases=len(examples),examples=examples,
    findings=['Active selection excludes defensive flag32 skills, including Counter, before invocation indexing.',
              'Unaffordable skills are removed before indexed Select; surviving SkillInfos use invocation levels by filtered ordinal.',
              'With differing per-slot invocation levels, MP depletion can therefore change which level is attached to a surviving skill.'],
    limits=['Original filter/get_IsForBattle/SkillInfo callback/getter; flags, effective MP and calculated cost supplied.',
            'The six ordered recipe cases compose native filter and callback; LINQ Where-before-Select order is statically traced.',
            'Synthetic invocation levels0/1/2 demonstrate the boundary; player-provided levels remain unknown.',
            'Category target construction, final eligibility and invocation RNG remain separate.'])
(EVIDENCE/'active-skill-filter-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
print(json.dumps({k:v for k,v in report.items() if k!='examples'}))
