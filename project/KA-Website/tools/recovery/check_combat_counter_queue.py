"""Native skill-command construction plus native queue-tail append."""
from pathlib import Path
exec(Path(__file__).with_name('check_combat_damage_wrapper.py').read_text(encoding='utf-8').split('from itertools import product')[0])
from itertools import product
from combat_commands import enqueue_skill_command

for flag in (0x316acac,0x316ac90,0x316ac91):uc.mem_write(flag,b'\x01')
for got in (0x2f5b578,0x2f5d8a0,0x2f5dd10,0x2f60eb8):w64(got,0x10001000)
w64(0x10001000,0x10002000);w32(0x100020e0,1)
ai,target,skill,queue,items,split=0x10004000,0x10005000,0x10006000,0x10007000,0x10008000,0x10009000
w64(ai+0x70,queue);w64(queue+0x10,items);w32(items+0x18,512);w32(split+0x18,2)
allocated=0
def external(machine,address,size,user):
    global allocated
    if 0x14c5738<=address<0x14c58b4 or 0x14c4284<=address<0x14c4380:return
    result=0
    if address==0x147e7e0:result=not exists
    elif address==0x238ee40:
        value=reg('X0');w32(split+0x20,value);w32(split+0x24,value>>32);result=split
    elif address==0x14415cc:
        assert reg('W0')==7 and reg('W1')==1
        result=0x10010000+allocated*0x100;allocated+=1;w32(result+0x18,7)
    elif address==0x1dec9a0:
        assert reg('W1')==0
        n=r32(queue+0x18)
        if n:machine.mem_write(items+0x28,bytes(machine.mem_read(items+0x20,n*8)))
        w64(items+0x20,reg('X2'));w32(queue+0x18,n+1);w32(queue+0x1c,r32(queue+0x1c)+1)
    else:raise AssertionError(hex(address))
    machine.reg_write(UC_ARM64_REG_X0,result);machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
checks=0
for count,exists,first,target_id,repetitions in product((1,2,7),(False,True),(False,True),(123,0x100000001),(1,8,256)):
    w32(skill+0x18,26);w32(skill+0x34,count);w64(target+0x28,target_id)
    allocated=0;w32(queue+0x18,0);w32(queue+0x1c,0);model=[]
    for _ in range(repetitions):
        run(0x14c5738,x0=ai,x1=target,x2=skill,w3=first)
        enqueue_skill_command(model,target_id,dict(id=26,count=count),target_exists=exists,first=first)
    assert r32(queue+0x18)==len(model)==repetitions
    for i,cmd in enumerate(model):
        ptr=struct.unpack('<Q',uc.mem_read(items+0x20+i*8,8))[0]
        actual=struct.unpack('<7i',uc.mem_read(ptr+0x20,28))
        expected=(29,i32(cmd['target']),i32(cmd['target']>>32),cmd['skill'],0,cmd['duration'],0)
        assert actual==expected,(actual,expected)
    checks+=1
report=dict(nativeCounterQueueConstructionCases=checks,
    finding='Repeated skills create separate queued commands with stored target IDs; Counter has count1/duration19. No deduplication/cap found through256 repeated inserts.',
    limits=['Original AddCommandSkill, AddCommandFirst/Last executed; pool, ID split and List.Insert supplied; preallocated tail capacity avoids resize.',
            'Counts2/7 on skill26 are constructor boundary fixtures, not original Counter data; runtime Counter invocation/MP and mixed-queue release remain separate.'])
(EVIDENCE/'counter-queue-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
print(json.dumps(report))
