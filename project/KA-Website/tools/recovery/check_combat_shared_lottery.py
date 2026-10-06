"""Original LotCritical/LotHit control flow against shared invocation storage."""
from pathlib import Path
exec(Path(__file__).with_name('check_combat_damage_wrapper.py').read_text(encoding='utf-8').split('from itertools import product')[0])
from itertools import product
from combat_entities import CombatEntities
from combat_shared_resolution import SharedAttackMath
from combat_resolution import critical_rate, hit_rate, random_below

for flag in (0x316b9f8,0x316b9fa):uc.mem_write(flag,b'\x01')
for got in (0x2f69a38,0x2f61408,0x2f5c538):w64(got,0x10001000)
w64(0x10001000,0x10002000);w32(0x100020e0,1);w64(0x100020b8,0x10003000)
w64(0x10003008,0x10003100);w64(0x10003010,0x10003200)
attacker,target,component,chosen=0x10004000,0x10005000,0x10006000,0x10007000
calls=[]
def external(machine,address,size,user):
    if 0x168cc54<=address<0x168cf80:return
    value=0
    if address==0x168cad8:value=critical_rate(luck)
    elif address==0x168cb94:value=hit_rate(dex,agi,luck)
    elif address==0x1472554:
        calls.append(('has_skill',reg('X0')));value=has_skill
    elif address==0x14724cc:value=component
    elif address==0x14d1880:value=component
    elif address==0x18947c8:value=chosen if selected is not None else 0
    elif address==0x1460024:
        calls.append(('roll',i32(reg('W0'))));value=random_below(raw,100)<i32(reg('W0'))
    else:raise AssertionError(hex(address))
    machine.reg_write(UC_ARM64_REG_X0,int(value)&0xffffffffffffffff);machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
checks=0
for critical,has_skill,order,raw,stats in product((False,True),(False,True),
        ((),(0,1,2),(2,1,0),(1,0,2)),(-2147483648,-101,0,9,50,99,2147483647),
        ((0,0,0),(20,100,99),(1000,1000,99999))):
    dex,agi,luck=stats;wanted=23 if critical else 22
    rows={0:dict(type=99,value=0),1:dict(type=wanted,value=50),2:dict(type=wanted,value=200)}
    selected=next((rows[i] for i in order if rows[i]['type']==wanted),None) if has_skill else None
    if selected:w32(chosen+0x30,selected['value'])
    calls=[];actual=run(0x168cc54 if critical else 0x168cde4,x0=attacker,x1=target)
    shared=CombatEntities(100,[]);a,b=shared.allocate(),shared.allocate()
    if has_skill:
        # A zero remaining counter is still enumerated until Attack removes it.
        shared.add_component(a if critical else b,'Skill',dict(invokingSkills=[(i,0) for i in order]))
    draws=[]
    def draw():draws.append(raw);return raw
    math=SharedAttackMath(shared,rows,lambda identity,key,default:{19:dex,15:agi,16:luck}.get(key,default),lambda identity:False,draw)
    expected=math.critical(a) if critical else math.hit(a,b)
    assert actual==expected,(critical,has_skill,order,raw,stats,actual,expected)
    assert draws==[raw] and calls[0]==('has_skill',attacker if critical else target)
    assert len(calls)==2 and calls[1][0]=='roll'
    checks+=1
report=dict(nativeSharedLotteryCases=checks,
    scope='Original LotCritical/LotHit uses the correct unit, first matching invocation modifier and an unconditional RNG draw.',
    limitations=['Base rate helpers, invocation enumeration/LINQ selection and Hits supplied; arithmetic helpers independently native-checked.',
                'Enumeration/count semantics also traced in original EnumerateInvokingSkills and its Select projection; no live RNG capture.'])
(EVIDENCE/'shared-lottery-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
print(json.dumps(report))
