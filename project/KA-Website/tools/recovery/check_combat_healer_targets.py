"""Native recovery range/category predicates and nearest-target reduction."""
from pathlib import Path
exec(Path(__file__).with_name('check_combat_damage_wrapper.py').read_text(encoding='utf-8').split('from itertools import product')[0])
from itertools import product,permutations
from combat_targeting import nearest_skill_target

uc.mem_write(0x316b48e,b'\x01')
w64(0x2f5d0d0,0x10001000);w64(0x10001000,0x10002000);w32(0x100020e0,1)
caster,closure,skill,delegate,category,enumerator,empty_cls=tuple(0x10004000+i*0x200 for i in range(7))
members=[0x10007000+i*0x100 for i in range(3)]
w64(closure+0x10,caster);w64(closure+0x18,skill);w64(closure+0x20,delegate)
w64(delegate+0x18,0x15e2190);w64(delegate+0x40,category);w64(category+0x10,caster)
w64(enumerator,empty_cls)
entries={0x15de8c4:0x10009000,0x15de920:0x10009010}
for i,e in enumerate(entries.values()):w64(e,0x30000100+i*4)
events=[];index=-1
def distance(a,b):return abs(positions[a]-positions[b])
def external(machine,address,size,user):
    global index
    if address==0x15de9c4:
        machine.reg_write(UC_ARM64_REG_PC,0x30000000);return
    if 0x15e2418<=address<0x15e24f8 or 0x15e2190<=address<0x15e2234 or 0x15de880<=address<0x15de9c4:return
    result=0
    if address==0x146caa4:
        identity=reg('X0');result=identity+0x80;w32(result+0x10,0);w32(result+0x14,positions[identity])
    elif address==0x145e7d8:result=abs(i32(reg('W0'))-i32(reg('W2')))+abs(i32(reg('W1'))-i32(reg('W3')))
    elif address==0x146b824:result=allegiance[reg('X0')]
    elif address==0x165e91c:
        assert reg('W1')==10;events.append(reg('X0'));result=rates[reg('X0')]
    elif address==0x1332890:result=entries[reg('LR')]
    elif address==0x30000100:index+=1;result=index<len(filtered)
    elif address==0x30000104:result=filtered[index]
    else:raise AssertionError(hex(address))
    machine.reg_write(UC_ARM64_REG_X0,int(result)&0xffffffffffffffff);machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
checks=0
for same,self_target,rate,reach,gap in product((False,True),(False,True),(0,1,50,99,100),(-1,0,1,3),(0,1,2,4)):
    target=caster if self_target else members[0]
    positions={caster:0,members[0]:gap};rates={target:rate};allegiance={caster:True,members[0]:same};events=[]
    w32(skill+0x38,reach)
    outcome=bool(run(0x15e2418,x0=closure,x1=target))
    assert outcome==(not self_target and gap<=reach and same and rate<100)
    assert len(events)==int(not self_target and gap<=reach and same)
    checks+=1
examples=[]
for order,health,reach in product(list(permutations(members)),product((0,50,100),repeat=3),(1,2)):
    positions={caster:0,members[0]:1,members[1]:1,members[2]:2};rates=dict(zip(members,health));allegiance={i:True for i in [caster,*members]}
    w32(skill+0x38,reach)
    filtered=[t for t in order if bool(run(0x15e2418,x0=closure,x1=t))]
    index=-1
    for name,value in dict(X19=enumerator,X21=closure,X27=0x10001000,X28=0x10001000,SP=0x20008000).items():uc.reg_write(globals()['UC_ARM64_REG_'+name],value)
    uc.emu_start(0x15de880,0x30000000,count=10000);assert reg('PC')==0x30000000
    expected=nearest_skill_target(caster,order,reach,distance,lambda t:rates[t]<100)
    assert reg('X20')==(expected or 0)
    examples.append(dict(order=list(order),rates=list(health),range=reach,chosen=expected,
        laterHealingHPEligible=expected is not None and 1<=rates[expected]<=99))
report=dict(nativeRecoveryPredicateCases=checks,nativeNearestReductionCases=len(examples),examples=examples,
    findings=['Recovery excludes the caster itself, uses skill shootingRange and same allegiance with HP rate<100.',
              'Nearest Manhattan target wins; equal distances preserve subset enumeration order, not lowest HP.',
              'A nearest zero-HP target passes selection but fails later healing HP eligibility; no fallback is performed by this reduction.'],
    limits=['Original range/category predicates and original reduction loop; component access, distances and subset enumeration supplied.',
            'Later CanUseSkill HP rule uses previously native-checked port; full candidate assembly/invocation not joined.',
            'No full encounter, healer survival, or original-runtime replay.'])
(EVIDENCE/'healer-target-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
print(json.dumps({k:v for k,v in report.items() if k!='examples'}))
