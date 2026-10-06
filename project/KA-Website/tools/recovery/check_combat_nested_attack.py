"""Native command timing and interruption primitives, with explicit callee stubs."""
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

from itertools import product
from combat_initial_state import trunc_div
from combat_resolution import attacker_invocation_phase,defender_attack_phase,resolve_attack,reflected_attack_result,apply_fighter_attack_results,subtract_raw_parameter
from elftools.elf.elffile import ELFFile
uc.mem_write(0x316b9fd,b'\x01')
uc.mem_write(0x316b483,b'\x01');uc.mem_write(0x316b1da,b'\x01')
attacker,target,incoming,result_=0x10004000,0x10004100,0x10004200,0x10004300
ai,skill_component,parameter,mp,enumerable,enumerator,empty_class=[0x10005000+i*0x1000 for i in range(7)]
w64(ai+0x58,0x10012000);w64(enumerable,empty_class);w64(enumerator,empty_class)
for i,got in enumerate([0x2f60040,0x2f5fe38,0x2f60048,0x2f5fb60,0x2f5fcf0,0x2f69a38,0x2f5fb10,0x2f5c140,0x2f5fb18,0x2f5c138,0x2f5fd60,0x2f5b960,0x2f5b570,0x2f69a48,0x2f69a60,0x2f69a68,0x2f69a50,0x2f60038,0x2f60078,0x2f5fd88]):
    ptr,cls,statics=0x10013000+i*0x10,0x10015000+i*0x100,0x10017000+i*0x100
    w64(got,ptr);w64(ptr,cls);w32(cls+0xe0,1);w64(cls+0xb8,statics);w64(statics+0x18,1);w64(statics+0x20,1)
uc.mem_map(0x772000,0x1000)
with (EVIDENCE.parent/'G2.1/39257e72291d/inputs/libil2cpp.so').open('rb') as f:
    elf=ELFFile(f);addr=0x772594
    seg=next(s for s in elf.iter_segments() if s['p_type']=='PT_LOAD' and s['p_vaddr']<=addr<s['p_vaddr']+s['p_filesz'])
    f.seek(seg['p_offset']+addr-seg['p_vaddr']);uc.mem_write(addr,f.read(7))
entries={0x168dc4c:0x10019000,0x168dcbc:0x10019010,0x168dd18:0x10019020,0x168def8:0x10019030,0x168d650:0x10019000,0x168d6c8:0x10019010,0x168d724:0x10019020,0x168da64:0x10019030}
for i,entry in enumerate(dict.fromkeys(entries.values())):w64(entry,0x30000100+i*4)
skills=[0x10020000+i*0x100 for i in range(3)]
hp,reflection_results,world,system=0x10040000,0x10041000,0x10042000,0x10043000
w64(attacker+0x30,world);w64(target+0x30,world)

def external(machine,address,size,user):
    global index,mode
    if address==0x15e0c2c:events.append(('reflect',i32(reg('W4'))))
    if any(a<=address<b for a,b in ((0x168d1e8,0x168df74),(0x15e0c2c,0x15e1a14),(0x1587d60,0x1587e64),(0x22141a8,0x22141d8),(0x16828c8,0x1682910))):return
    result=0
    if address==0x147e7e0:result=0
    elif address==0x168cc54:events.append(('critical',));result=critical
    elif address==0x168cde4:events.append(('hit',));result=hit
    elif address==0x168cf80:events.append(('damage',));result=101
    elif address==0x168e214:result=0
    elif address==0x1472554:result=1
    elif address==0x1468bc8:result=ai
    elif address==0x14724cc:result=skill_component if reg('X0')==attacker else skill_component+0x100
    elif address==0x1a6a204:result=int(reg('W1') in board)
    elif address==0x1a6a108:result=board[reg('W1')]
    elif address==0x1a6a12c:board[reg('W1')]=i32(reg('W2'))
    elif address==0x1a6a2b8:board.pop(reg('W1'),None)
    elif address==0x14d0c80:result=enumerable;index=-1;mode='attacker' if reg('X0')==skill_component else 'defender'
    elif address==0x1332890:result=entries[reg('LR')]
    elif address==0x30000100:result=enumerator
    elif address==0x30000104:index+=1;result=int(index<len(skills))
    elif address==0x30000108:result=skills[index] if mode=='attacker' else defender_skills[index]
    elif address==0x3000010c:pass
    elif address==0x15df0e0 and reg('LR')==0x15e0d7c:events.append(('reflect_eligible',));result=1
    elif address==0x15df0e0:events.append(('eligible',mode,index,read_invoking(),dict(board)));result=index!=0 and (mode=='defender' or all(sid!=index for sid,count in read_invoking()))
    elif address==0x1dd0638:
        result=struct.unpack('<Q',uc.mem_read(list_items+0x20+reg('W1')*8,8))[0]
    elif address==0x1dd068c:w64(list_items+0x20+reg('W1')*8,reg('X2'))
    elif address==0x1dd2188:
        remaining=read_invoking();remaining.pop(reg('W1'));write_invoking(remaining)
    elif address==0x14d1974:result=any(sid==r32(reg('X1')+0x18) for sid,count in read_invoking())
    elif address==0x14d0a50:result=1
    elif address==0x15e03d8:events.append(('invoke',mode,index));result=index==2 and (mode=='defender' or succeeds)
    elif address==0x1477df0:result=1
    elif address==0x146fd60:result=0
    elif address==0x12d2214:result=reflection_results;w32(result+0x18,1)
    elif address==0x191e2ec:
        events.append(('reflection_event',))
        machine.reg_write(UC_ARM64_REG_X1,reg('X2'));machine.reg_write(UC_ARM64_REG_X0,system)
        machine.reg_write(UC_ARM64_REG_PC,0x1587d60);return
    elif address==0x1583b48:events.append(('state',reg('X2'),reg('W1'),r32(hp+0x14)))
    elif address==0x15e0ad4:events.append(('reflect_sound',))
    elif address==0x14c5738:events.append(('counter',reg('W3')))
    elif address==0x1471200:result=parameter if reg('X0')==attacker else parameter+0x100
    elif address==0x14c89d0:result=hp if reg('W1')==10 else mp if reg('X0')==parameter else mp+0x100
    elif address==0x16323a4:events.append(('cost',mode));result=7
    elif address in (0x16657b0,0x13eb3c8):result=max(i32(reg('W0')),i32(reg('W1')))
    elif address==0x15e0620:events.append(('balloon',mode))
    elif address==0x144cdc8:events.append(('sound_roll',));result=0
    elif address==0x22141b8:
        uc.mem_write(reg('X0'),struct.pack('<QQ??2xiQ',reg('X1'),reg('X2'),bool(reg('W3')),bool(reg('W4')),i32(reg('W5')),reg('X6')))
    else:raise AssertionError(hex(address))
    machine.reg_write(UC_ARM64_REG_X0,int(result)&0xffffffffffffffff);machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
list_,list_items=0x10030000,0x10031000
w64(skill_component+0x28,list_);w64(list_+0x10,list_items);w32(list_items+0x18,16)
def read_invoking():return [struct.unpack('<ii',uc.mem_read(list_items+0x20+i*8,8)) for i in range(r32(list_+0x18))]
def write_invoking(values):
    w32(list_+0x18,len(values))
    for i,pair in enumerate(values):uc.mem_write(list_items+0x20+i*8,struct.pack('<ii',*pair))
from combat_entities import CombatEntities
from combat_shared_resolution import apply_shared_attack_results

defender_skills=[s+0x1000 for s in skills]
checks=0
for typ,critical,hit,succeeds,remaining in product((18,20,21,24),(False,True),(False,True),(False,True),(1,2)):
    events=[];w32(hp+0x14,17);w32(hp+0x18,100);board={62:8,63:1,64:19};original=[(2,remaining),(8,2)];write_invoking(original)
    for ptr in (mp,mp+0x100):w32(ptr+0x14,5);w32(ptr+0x18,10)
    for i,(s,d) in enumerate(zip(skills,defender_skills)):
        w32(s+0x18,i);w32(s+0x2c,22);w32(s+0x30,50)
        w32(d+0x18,i);w32(d+0x2c,typ);w32(d+0x30,50)
    run(0x168d1e8,x0=attacker,x1=target,x2=0,x8=result_)
    shared=CombatEntities(100,[]);identities={mode:shared.allocate() for mode in ('attacker','defender')}
    for mode,identity in identities.items():
        shared.add_component(identity,'Parameter',dict(parameters={10:dict(rawValue=17,rawMax=100),11:dict(rawValue=5,rawMax=10)}))
        shared.add_component(identity,'AI',dict(board={62:8,63:1,64:19} if mode=='defender' else {},long_board={},commands=[],path=[]))
        shared.add_component(identity,'Skill',dict(dataIds=[0,1,2],invocationLevels=[1,1,1],invokingSkills=list(original) if mode=='attacker' else [],maxSlotNum=3))
    model_attacker=shared.fighter(identities['attacker']);model_defender=shared.fighter(identities['defender'])
    expected=[];model_invoking=model_attacker['invoking'];model_board=model_defender['board']
    def current_hp():return model_attacker['parameters'][10]['rawValue']
    def eligible(mode,s):
        expected.append(('eligible',mode,s['id'],list(model_invoking),dict(model_board)))
        return s['id']!=0 and (mode=='defender' or all(sid!=s['id'] for sid,count in model_invoking))
    def invokes(mode,s):expected.append(('invoke',mode,s['id']));return s['id']==2 and (mode=='defender' or succeeds)
    def pay(mode,s):
        expected.append(('cost',mode));parameter=shared.fighter(identities[mode])['parameters'][11]
        parameter['rawValue']=subtract_raw_parameter(parameter['rawValue'],parameter['rawMax'],7)[0]
    def balloon(mode,s):expected.append(('balloon',mode))
    def reflect(s,d):
        expected.append(('reflect',d));expected.append(('reflect_eligible',))
        pay('defender',s);balloon('defender',s)
        payload=reflected_attack_result(identities['defender'],identities['attacker'],s,d)
        expected.append(('reflection_event',))
        apply_shared_attack_results(shared,[payload],lambda unit,state:expected.append(('state',attacker,state,current_hp())))
        expected.append(('reflect_sound',))
    def defense_phase(h,c,d):
        return defender_attack_phase(h,c,d,model_board,[dict(id=i,type=typ,value=50) for i in range(3)],
            lambda s:eligible('defender',s),lambda s:invokes('defender',s),reflect,
            lambda s:expected.append(('counter',0)),lambda s:pay('defender',s),lambda s:balloon('defender',s),lambda:expected.append(('sound_roll',)))
    def attack_phase():
        attacker_invocation_phase(model_invoking,[dict(id=i) for i in range(3)],lambda s:eligible('attacker',s),
            lambda s:invokes('attacker',s),lambda s:pay('attacker',s),lambda s:balloon('attacker',s))
    def crit(a):expected.append(('critical',));return critical
    def hits(a,t):expected.append(('hit',));return hit
    def damage(c,a,t,m):expected.append(('damage',));return 101
    resolved=resolve_attack(attacker,target,None,lambda t:True,crit,hits,damage,lambda a:False,defense_phase,attack_phase)
    actual=struct.unpack('<QQ??2xiQ',uc.mem_read(result_,32))
    assert actual==(attacker,target,resolved['hit'],resolved['critical'],resolved['damage'],0)
    assert events==expected,(typ,critical,hit,succeeds,remaining,events,expected)
    assert r32(hp+0x14)==current_hp()
    assert read_invoking()==model_invoking
    assert board==model_board
    assert (r32(mp+0x14),r32(mp+0x114))==tuple(shared.fighter(identities[mode])['parameters'][11]['rawValue'] for mode in ('attacker','defender'))
    checks+=1
report=dict(nativeNestedAttackCases=checks,sharedComponentStorage=True,scope='Entire original Attack with both SkillComponents, nested original reflection UseSkill and Fighter.OnAttack HP application before attacker countdown/activation',
    limitations=['Stat/roll/candidate helpers supplied; reflection runs full UseSkill and HP event handler with supplied eligibility; counter recorded; state transitions and sound recorded; no world event loop'])
(EVIDENCE/'nested-attack-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');print(json.dumps(report))
