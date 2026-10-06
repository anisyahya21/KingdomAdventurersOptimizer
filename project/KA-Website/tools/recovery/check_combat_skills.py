"""Original ARM64 skill primitives with explicit synthetic component/callee stubs."""
import json
import random
import struct
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE
from unicorn.arm64_const import *
from elftools.elf.elffile import ELFFile
from combat_initial_state import EVIDENCE, i32
from combat_resolution import skill_mp_cost, buff_accuracy, buff_turns

uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
for base, size in [(0x1000000,0x2300000),(0x10000000,0x10000),(0x20000000,0x10000),(0x30000000,0x1000),(0x750000,0x10000)]:
    uc.mem_map(base,size)
binary = EVIDENCE.parent / 'G2.1/39257e72291d/inputs/libil2cpp.so'
raw = binary.read_bytes()
for entry in json.loads((EVIDENCE/'audit.json').read_text())['methods']:
    a,b,o = [int(entry[k],16) for k in ['rva','end','offset']]
    uc.mem_write(a,raw[o:o+b-a])
with binary.open('rb') as f:
    elf=ELFFile(f)
    for a in [0x753504,0x753590]:
        seg=next(s for s in elf.iter_segments() if s['p_type']=='PT_LOAD' and s['p_vaddr']<=a<s['p_vaddr']+s['p_filesz'])
        f.seek(seg['p_offset']+a-seg['p_vaddr']);uc.mem_write(a,f.read(4))
def w32(a,n):uc.mem_write(a,struct.pack('<I',n&0xffffffff))
def w64(a,n):uc.mem_write(a,struct.pack('<Q',n))
def run(a,**regs):
    uc.reg_write(UC_ARM64_REG_SP,0x20008000);uc.reg_write(UC_ARM64_REG_LR,0x30000000)
    for k,v in regs.items():uc.reg_write(globals()['UC_ARM64_REG_'+k.upper()],v&0xffffffffffffffff)
    uc.emu_start(a,0x30000000,count=4000)
    assert uc.reg_read(UC_ARM64_REG_PC)==0x30000000,hex(uc.reg_read(UC_ARM64_REG_PC))
    return i32(uc.reg_read(UC_ARM64_REG_W0))
for flag in [0x316b71b,0x316ba01,0x316ba02,0x316c35c]:uc.mem_write(flag,b'\x01')
w64(0x2f5d0d0,0x10001000);w64(0x10001000,0x10001100);w32(0x100011e0,1)
skill,caster,target=0x10002000,0x10003000,0x10004000
state={};calls=[]
allowed=[(0x16323a4,0x1632458),(0x23fe33c,0x23fe41c),(0x168e66c,0x168e75c),(0x168e760,0x168e7fc),(0x18b2f00,0x18b2f8c)]
def external(m,a,size,user):
    if any(lo<=a<hi for lo,hi in allowed):return
    result=0
    if a==0x1470c84:result=state['monster']
    elif a==0x16610f4:result=state['average']
    elif a==0x23dd630:m.reg_write(UC_ARM64_REG_S0,0)
    elif a==0x168d178:result=state['intelligence']
    elif a==0x166070c:
        who=m.reg_read(UC_ARM64_REG_X0);pid=m.reg_read(UC_ARM64_REG_W1)
        result=state['params'][who,pid];calls.append((who,pid))
    elif a==0x18c05a0:
        assert m.reg_read(UC_ARM64_REG_W1)==26
        result=state['delegate']
    elif a==0x30000080:
        calls.append(('listener',m.reg_read(UC_ARM64_REG_X0),m.reg_read(UC_ARM64_REG_X1)))
    else:raise AssertionError(hex(a))
    m.reg_write(UC_ARM64_REG_W0,result&0xffffffff);m.reg_write(UC_ARM64_REG_PC,m.reg_read(UC_ARM64_REG_LR))
hook=uc.hook_add(UC_HOOK_CODE,external)
counts=dict(mpCost=0,buffAccuracy=0,buffTurns=0,synchronousDispatch=0,entityExists=0)
rng=random.Random(20260912)
for monster in [False,True]:
    for low,high in [(0,0),(0,100),(1,1),(3,99),(100,3)]:
        w32(skill+0x44,low);w32(skill+0x48,high)
        for avg in [0,1,2,500,998,999,1000,5000]+[rng.randrange(1,1500) for _ in range(50)]:
            state.update(monster=monster,average=avg)
            assert run(0x16323a4,x0=skill,x1=caster)==skill_mp_cost(low,high,avg,monster=monster)
            counts['mpCost']+=1
for _ in range(1200):
    dex,luck=[rng.randrange(-100,2000) for _ in range(2)]
    typ=rng.choice([66,67]);value=rng.randrange(0,101)
    w32(skill+0x2c,typ);w32(skill+0x30,value)
    state['params']={(caster,19):dex,(target,16):luck};calls.clear()
    assert run(0x168e66c,x0=caster,x1=target,x2=skill)==buff_accuracy(dex,luck,typ,value)
    assert calls==[(caster,19),(target,16)];counts['buffAccuracy']+=1
for monster in [False,True]:
    for intelligence in [-1,0,1,333,334,666,667,999,1000,1001,5000]:
        state.update(monster=monster,intelligence=intelligence,params={(caster,18):intelligence})
        assert run(0x168e760,x0=caster)==buff_turns(intelligence)
        counts['buffTurns']+=1
# Real Send<object> tail-invokes delegate before returning; dictionary and listener stubbed.
w64(0x2f6f240,0x10005000);w64(0x10005000,0x10005100)
w64(0x10005218,0x30000080);w64(0x10005240,0x10005300)
for delegate in [0,0x10005200]:
    state['delegate']=delegate;calls.clear()
    run(0x18b2f00,x0=caster,w1=26,x2=target)
    assert calls==([('listener',0x10005300,target)] if delegate else [])
    counts['synchronousDispatch']+=1
uc.hook_del(hook)
for flags in range(256):
    uc.mem_write(caster+0x38,bytes([flags]))
    assert run(0x1477df0,x0=caster)==int(not flags&2)
    counts['entityExists']+=1
assert run(0x1477df0,x0=0)==0;counts['entityExists']+=1
# Whole SkillSystem.Update with one synthetic entity; native counter/expiry logic.
uc.mem_write(0x316b477,b'\x01')
for got in [0x2f5faf8,0x2f5fae8,0x2f5fe38,0x2f60048,0x2f5fb60,0x2f5fcf0,0x2f5fae0]:
    w64(got,0x10005000)
system,world,ai,board=0x10006000,0x10006100,0x10006200,0x10006300
w64(system+0x20,world);w64(world+0x30,0x10006400);w64(ai+0x58,board)
board_values={};enumerated=False
counts['buffExpiration']=0

def status_external(m,a,size,user):
    global enumerated
    if 0x15ddc04<=a<0x15ddf58 or 0x168d414<=a<0x168d55c:return
    value=0
    if a==0x1cd2110:
        enumerated=False;m.mem_write(m.reg_read(UC_ARM64_REG_X8),bytes(24))
    elif a==0x1bcf0a8:
        value=int(not enumerated)
        if value:w64(m.reg_read(UC_ARM64_REG_X0)+0x10,caster)
        enumerated=True
    elif a==0x1bcf0a4:pass
    elif a==0x146b510:value=1
    elif a==0x1468bc8:value=ai
    elif a in [0x1a6a204,0x1a6a108,0x1a6a12c,0x1a6a2b8]:
        key=m.reg_read(UC_ARM64_REG_W1)
        if a==0x1a6a204:value=int(key in board_values)
        elif a==0x1a6a108:value=board_values[key]
        elif a==0x1a6a12c:board_values[key]=i32(m.reg_read(UC_ARM64_REG_W2))
        else:board_values.pop(key)
    else:raise AssertionError(hex(a))
    m.reg_write(UC_ARM64_REG_X0,value&0xffffffffffffffff)
    m.reg_write(UC_ARM64_REG_PC,m.reg_read(UC_ARM64_REG_LR))
hook=uc.hook_add(UC_HOOK_CODE,status_external)
for duration in [2,3,4,5]:
    board_values={62:100,63:duration,64:0}
    for tick in range(1,duration*20+2):
        run(0x15ddc04,x0=system,w1=1)
        expected={62:100,63:duration-tick//20,64:tick} if tick<duration*20 else {}
        assert board_values==expected,(duration,tick,board_values,expected)
        counts['buffExpiration']+=1
# Receiver hit-status decrement occurs before reactive defence transforms.
counts['statusHitDecrement']=0
for hit in [0,1]:
    for duration in [1,2,5]:
        for tick in [0,19,20]:
            board_values={62:100,63:duration,64:tick}
            uc.reg_write(UC_ARM64_REG_SP,0x20008000)
            uc.reg_write(UC_ARM64_REG_X20,caster);uc.reg_write(UC_ARM64_REG_W28,hit)
            uc.emu_start(0x168d414,0x168d55c,count=1000)
            assert uc.reg_read(UC_ARM64_REG_PC)==0x168d55c
            expected={} if hit and duration==1 else {62:100,63:duration-hit,64:tick}
            assert board_values==expected
            counts['statusHitDecrement']+=1
uc.hook_del(hook)
# Isolate successful defender-reaction arms, before enumerator disposal/audio.
w64(0x2f5b570,0x10007000);w64(0x10007000,0x10007100);w32(0x100071e0,1)
mp=0x10007200
counts['defenderReaction']=0
reaction_calls=[]
def reaction_external(m,a,size,user):
    if 0x168d7c8<=a<0x168d95c:return
    value=0
    if a in [0x16657b0,0x13eb3c8]:value=max(i32(m.reg_read(UC_ARM64_REG_W0)),i32(m.reg_read(UC_ARM64_REG_W1)))
    elif a==0x1471200:value=mp
    elif a==0x14c89d0:value=mp
    elif a==0x16323a4:value=7
    elif a==0x15e0620:reaction_calls.append('balloon')
    elif a==0x1468bc8:value=ai
    elif a==0x14c5738:
        reaction_calls.append(('queue',m.reg_read(UC_ARM64_REG_X1),m.reg_read(UC_ARM64_REG_X2),m.reg_read(UC_ARM64_REG_W3)))
    elif a==0x15e0c2c:
        reaction_calls.append(('reflect',m.reg_read(UC_ARM64_REG_X0),m.reg_read(UC_ARM64_REG_X1),m.reg_read(UC_ARM64_REG_W3),m.reg_read(UC_ARM64_REG_W4)))
    else:raise AssertionError(hex(a))
    m.reg_write(UC_ARM64_REG_X0,value&0xffffffffffffffff);m.reg_write(UC_ARM64_REG_PC,m.reg_read(UC_ARM64_REG_LR))
hook=uc.hook_add(UC_HOOK_CODE,reaction_external)
for typ,start in [(18,0x168d7c8),(20,0x168d8b4),(21,0x168d800),(24,0x168d8dc)]:
    for damage in [0,1,2,3,100,99999]:
        reaction_calls.clear();w32(mp+0x14,100);w32(mp+0x18,100);w32(skill+0x30,50)
        for reg,value in [('SP',0x20008000),('X20',caster),('X21',target),('X25',skill),('W29',damage),('W28',1),('W27',1)]:uc.reg_write(globals()['UC_ARM64_REG_'+reg],value)
        uc.emu_start(start,0x168d95c,count=1000)
        assert uc.reg_read(UC_ARM64_REG_PC)==0x168d95c
        actual_damage=uc.reg_read(UC_ARM64_REG_W29)
        if typ==18:assert reaction_calls==[('reflect',caster,target,0,damage)] and actual_damage==damage
        elif typ==20:assert reaction_calls==[('queue',target,skill,0)] and actual_damage==damage
        elif typ==21:assert reaction_calls==['balloon'] and actual_damage==max(1,damage//2)
        else:assert reaction_calls==['balloon'] and actual_damage==0 and uc.reg_read(UC_ARM64_REG_W28)==0 and uc.reg_read(UC_ARM64_REG_W27)==0
        assert struct.unpack('<i',uc.mem_read(mp+0x14,4))[0]==(93 if typ in [21,24] else 100)
        counts['defenderReaction']+=1
uc.hook_del(hook)
# Parameter.Add receives effective maximum in guerrilla party HP/MP refill.
counts['initialRefill']=0
def add_external(m,a,size,user):
    if 0x1682824<=a<0x1682868:return
    assert a==0x13eb3a0,hex(a)
    value=max(i32(m.reg_read(UC_ARM64_REG_W1)),min(i32(m.reg_read(UC_ARM64_REG_W2)),i32(m.reg_read(UC_ARM64_REG_W0))))
    m.reg_write(UC_ARM64_REG_W0,value&0xffffffff);m.reg_write(UC_ARM64_REG_PC,m.reg_read(UC_ARM64_REG_LR))
hook=uc.hook_add(UC_HOOK_CODE,add_external)
for maximum in [0,1,100,10000]:
    for current in [0,1,50,10000]:
        w32(mp+0x14,current)
        run(0x1682824,x0=mp,w1=maximum,w2=maximum)
        actual=struct.unpack('<i',uc.mem_read(mp+0x14,4))[0]
        assert actual==(min(current+maximum,maximum) if maximum>0 else current)
        counts['initialRefill']+=1
uc.hook_del(hook)
result=dict(status='pass',cases=counts,limits='Original isolated methods; component access, average-level getter, sine at zero amplitude, dictionary, delegate and one-entity status enumeration stubbed. No game replay or subscriber-order validation.')
(EVIDENCE/'skill-checks.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result))
