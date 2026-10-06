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
from combat_resolution import attacker_invocation_phase,defender_attack_phase,resolve_attack
from elftools.elf.elffile import ELFFile
uc.mem_write(0x316b9fd,b'\x01')
attacker,target,incoming,result_=0x10004000,0x10004100,0x10004200,0x10004300
ai,skill_component,parameter,mp,enumerable,enumerator,empty_class=[0x10005000+i*0x1000 for i in range(7)]
w64(ai+0x58,0x10012000);w64(enumerable,empty_class);w64(enumerator,empty_class)
for i,got in enumerate([0x2f60040,0x2f5fe38,0x2f60048,0x2f5fb60,0x2f5fcf0,0x2f69a38,0x2f5fb10,0x2f5c140,0x2f5fb18,0x2f5c138,0x2f5fd60,0x2f5b960,0x2f5b570,0x2f69a48,0x2f69a60,0x2f69a68,0x2f69a50]):
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

def external(machine,address,size,user):
    global index,mode
    if 0x168d1e8<=address<0x168df74:return
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
    elif address==0x15df0e0:events.append(('eligible',mode,index,read_invoking(),dict(board)));result=index!=0 and (mode=='defender' or all(sid!=index for sid,count in read_invoking()))
    elif address==0x1dd0638:
        result=struct.unpack('<Q',uc.mem_read(list_items+0x20+reg('W1')*8,8))[0]
    elif address==0x1dd068c:w64(list_items+0x20+reg('W1')*8,reg('X2'))
    elif address==0x1dd2188:
        remaining=read_invoking();remaining.pop(reg('W1'));write_invoking(remaining)
    elif address==0x14d1974:result=any(sid==r32(reg('X1')+0x18) for sid,count in read_invoking())
    elif address==0x14d0a50:result=1
    elif address==0x15e03d8:events.append(('invoke',mode,index));result=index==2 and (mode=='defender' or succeeds)
    elif address==0x15e0c2c:events.append(('reflect',i32(reg('W4'))))
    elif address==0x14c5738:events.append(('counter',reg('W3')))
    elif address==0x1471200:result=parameter if reg('X0')==attacker else parameter+0x100
    elif address==0x14c89d0:result=mp if reg('X0')==parameter else mp+0x100
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
defender_skills=[s+0x1000 for s in skills]
checks=0
for typ,critical,hit,succeeds,remaining in product((18,20,21,24),(False,True),(False,True),(False,True),(1,2)):
    events=[];board={62:8,63:1,64:19};original=[(2,remaining),(8,2)];write_invoking(original)
    for ptr in (mp,mp+0x100):w32(ptr+0x14,5);w32(ptr+0x18,10)
    for i,(s,d) in enumerate(zip(skills,defender_skills)):
        w32(s+0x18,i);w32(s+0x2c,22);w32(s+0x30,50)
        w32(d+0x18,i);w32(d+0x2c,typ);w32(d+0x30,50)
    run(0x168d1e8,x0=attacker,x1=target,x2=0,x8=result_)
    expected=[];model_invoking=list(original);model_board={62:8,63:1,64:19};model_mp={'attacker':5,'defender':5}
    def eligible(mode,s):
        expected.append(('eligible',mode,s['id'],list(model_invoking),dict(model_board)))
        return s['id']!=0 and (mode=='defender' or all(sid!=s['id'] for sid,count in model_invoking))
    def invokes(mode,s):expected.append(('invoke',mode,s['id']));return s['id']==2 and (mode=='defender' or succeeds)
    def pay(mode,s):expected.append(('cost',mode));model_mp[mode]=max(0,model_mp[mode]-7)
    def balloon(mode,s):expected.append(('balloon',mode))
    def defense_phase(h,c,d):
        return defender_attack_phase(h,c,d,model_board,[dict(id=i,type=typ,value=50) for i in range(3)],
            lambda s:eligible('defender',s),lambda s:invokes('defender',s),lambda s,d:expected.append(('reflect',d)),
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
    assert read_invoking()==model_invoking
    assert board==model_board
    assert (r32(mp+0x14),r32(mp+0x114))==(model_mp['attacker'],model_mp['defender'])
    checks+=1
report=dict(nativeCombinedAttackCases=checks,scope='Entire original Attack with both SkillComponents; defender reactions plus attacker countdown/activation; compared with composed portable attack pipeline',
    limitations=['Stat/roll/candidate helpers supplied; reflection and counter callbacks recorded rather than running nested HP/queue effects; no world event loop'])
(EVIDENCE/'combined-attack-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');print(json.dumps(report))
