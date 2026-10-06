"""Original skill MP cost joined to AverageHumanParamLevels and Graph.Easing."""
from pathlib import Path
exec(Path(__file__).with_name('check_combat_skills.py').read_text(encoding='utf-8').split('for flag in ')[0])
from itertools import product
from combat_entities import CombatEntities
from combat_parameters import HUMAN_TRAINING_PARAMETERS
from combat_shared_resolution import shared_skill_cost

for flag in (0x316b71b,0x316b887):uc.mem_write(flag,b'\x01')
for got in (0x2f5d0d0,0x2f5b570):w64(got,0x10001000)
w64(0x10001000,0x10001100);w32(0x100011e0,1);w64(0x100011b8,0x10001200)
w64(0x10001220,0x10001300);w32(0x10001318,12)
for index,key in enumerate(HUMAN_TRAINING_PARAMETERS):w32(0x10001320+index*4,key)
skill,caster,parameter=0x10002000,0x10003000,0x10004000
def reg(name):return uc.reg_read(globals()['UC_ARM64_REG_'+name])
calls=[]
def external(machine,address,size,user):
    if any(lo<=address<hi for lo,hi in ((0x16323a4,0x1632458),(0x16610f4,0x166124c),(0x23fe33c,0x23fe41c))):return
    result=0
    if address==0x1470c84:calls.append(('monster',));result=monster
    elif address==0x146ee7c:calls.append(('human',));result=human
    elif address==0x1471200:result=parameter
    elif address==0x14c89d0:
        key=reg('W1');calls.append(('level',key));w32(parameter+0x24,levels[key]);result=parameter
    elif address==0x16657b0:result=max(i32(reg('W0')),i32(reg('W1')))
    elif address==0x23dd630:machine.reg_write(UC_ARM64_REG_S0,0)
    else:raise AssertionError(hex(address))
    machine.reg_write(UC_ARM64_REG_X0,int(result)&0xffffffffffffffff);machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
rows=json.loads((EVIDENCE/'weapon-skill-profiles.json').read_text(encoding='utf-8'))['skills']
checks=0
for row,monster,human,base in product(rows,(False,True),(False,True),(-10,1,500,999,2147483647)):
    levels={key:i32(base+index) for index,key in enumerate(HUMAN_TRAINING_PARAMETERS)}
    w32(skill+0x44,row['minMp']);w32(skill+0x48,row['maxMp']);calls=[]
    actual=run(0x16323a4,x0=skill,x1=caster)
    shared=CombatEntities(100,[]);identity=shared.allocate()
    shared.add_component(identity,'Parameter',dict(parameters={key:dict(trainingLevel=value) for key,value in levels.items()}))
    seen=[]
    def is_monster(identity):seen.append(('monster',));return monster
    def is_human(identity):seen.append(('human',));return human
    expected=shared_skill_cost(shared,identity,row,is_monster,is_human)
    assert actual==expected,(row['id'],monster,human,base,actual,expected)
    assert [event for event in calls if event[0]!='level']==seen
    assert [event[1] for event in calls if event[0]=='level']==(list(HUMAN_TRAINING_PARAMETERS) if human and not monster and (row['minMp'] or row['maxMp']) else [])
    checks+=1
report=dict(nativeSharedSkillCostCases=checks,
    scope='All136 original skill rows: original MP cost, human training-level averaging and zero-amplitude easing joined.',
    limitations=['Human/Monster predicates, parameter access, ClampMin and zero-amplitude sine supplied; no captured player training levels.'])
(EVIDENCE/'shared-cost-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
print(json.dumps(report))
