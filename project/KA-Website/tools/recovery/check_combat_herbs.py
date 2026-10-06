"""Native Holy Herb dispatch and recovery primitives, with explicit boundaries."""
from pathlib import Path
exec(Path(__file__).with_name('check_combat_damage_wrapper.py').read_text(encoding='utf-8').split('from itertools import product')[0])
from itertools import product
from elftools.elf.elffile import ELFFile
from combat_initial_state import trunc_div

for flag in (0x316b927,0x316b920,0x316b921,0x316b922,0x316b928,0x316b6bd,0x316ae68):uc.mem_write(flag,b'\x01')
for i,got in enumerate((0x2f5b890,0x2f5b570,0x2f5d0d0,0x2f60048,0x2f5d358,0x2f5d360,0x2f5c138,0x2f5c140)):
    ptr,cls=0x10001000+i*8,0x10002000+i*0x100
    w64(got,ptr);w64(ptr,cls);w32(cls+0xe0,1)
uc.mem_map(0x772000,0x1000)
with (EVIDENCE.parent/'G2.1/39257e72291d/inputs/libil2cpp.so').open('rb') as f:
    elf=ELFFile(f);seg=next(s for s in elf.iter_segments() if s['p_type']=='PT_LOAD' and s['p_vaddr']<=0x772000<s['p_vaddr']+s['p_filesz'])
    f.seek(seg['p_offset']+0x772000-seg['p_vaddr']);uc.mem_write(0x772000,f.read(0x1000))
table,item,targets,entity,ai,bb,result_,app,world=[0x10004000+i*0x200 for i in range(9)]
w32(table+0x18,32);w64(table+0x20+31*8,item)
w32(item+0x18,31);w32(item+0x28,4);w32(item+0x70,3);w32(item+0x74,2)
w32(item+0x78,100);w32(item+0x7c,100);w64(ai+0x58,bb)
native_ranges=((0x167b834,0x167ccbC),(0x167acac,0x167aea0),(0x162b344,0x162b3bc),(0x14f34ac,0x14f3550))
mode='single';group_index=-1
enumerator,empty_cls=0x1000e000,0x1000f000
w64(targets,empty_cls);w64(enumerator,empty_cls)
entries={0x167af50:0x10018000,0x167afcc:0x10018010,0x167b028:0x10018020,0x167b0a4:0x10018030}
for i,entry in enumerate(entries.values()):w64(entry,0x30000100+i*4)
group_profiles={0x10010000:dict(rate=0,human=True,parameters=True),
                0x10011000:dict(rate=100,human=True,parameters=True),
                0x10012000:dict(rate=0,human=False,parameters=True),
                0x10013000:dict(rate=0,human=True,parameters=False)}
def external(machine,address,size,user):
    global group_index
    if mode=='group' and 0x167aea0<=address<0x167b0e0:return
    if any(a<=address<b for a,b in native_ranges):return
    result=0
    if address==0x161e914:result=table
    elif address==0x162b3bc:result=1 # Holy Herb global-effect classification supplied.
    elif address==0x1477df0:result=0 # ExecuteItem single-target argument is null.
    elif address==0x1673cc8:events.append('check_stock');result=stock
    elif address==0x167aea0:
        assert reg('X1')==targets and reg('W2')==11 and reg('W3')==100
        events.append('recover_team');result=team_success
    elif address==0x1673ba4:
        assert reg('X2')==item and reg('W3')==1;events.append('spend_one')
    elif address==0x1635ee8:events.append(('result',reg('W1')))
    elif address==0x165e91c:result=group_profiles[reg('X0')]['rate'] if mode=='group' else rate
    elif address==0x16609ec:
        assert reg('W1')==11 and reg('W2')==0;result=maximum
    elif address==0x146ee7c:result=group_profiles[reg('X0')]['human'] if mode=='group' else human
    elif address==0x1471288:result=group_profiles[reg('X0')]['parameters'] if mode=='group' else has_parameters
    elif address==0x1660de4:
        assert (reg('X0') in group_profiles if mode=='group' else reg('X0')==entity) and reg('W1')==11
        events.append(('add_mp',i32(reg('W2'))))
    elif address==0x1468bc8:result=ai
    elif address==0x1a6a108:
        assert reg('W1')==5;result=state
    elif address==0x1332890:result=entries[reg('LR')]
    elif address==0x30000100:result=enumerator
    elif address==0x30000104:group_index+=1;result=group_index<len(group)
    elif address==0x30000108:result=group[group_index]
    elif address==0x3000010c:pass
    else:raise AssertionError(hex(address))
    machine.reg_write(UC_ARM64_REG_X0,int(result)&0xffffffffffffffff);machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
counts=dict(itemDispatch=0,singleRecovery=0,battleTargetFilter=0,joinedGroupRecovery=0)
for stock,team_success in product((False,True),repeat=2):
    events=[];run(0x167b834,x0=app,x1=world,w2=31,w3=1,x4=0,x5=targets,x8=result_)
    expected=['check_stock']
    if stock:expected+=['recover_team']
    if stock and team_success:expected+=['spend_one']
    expected+=[('result',int(stock and team_success))]
    assert events==expected,(events,expected);counts['itemDispatch']+=1
for human,has_parameters,rate,maximum,percent in product((False,True),(False,True),(-1,0,50,99,100,101),(1,5568,99999),(0,15,100)):
    events=[];outcome=bool(run(0x167acac,x0=app,x1=entity,w2=11,w3=percent))
    assert outcome==(rate<100)
    expected=[('add_mp',trunc_div(i32(maximum*percent),100))] if rate<100 and human and has_parameters else []
    assert events==expected,(human,has_parameters,rate,events,expected);counts['singleRecovery']+=1
for state in range(-1,11):
    assert bool(run(0x14f34ac,x1=entity))==(state not in (7,8));counts['battleTargetFilter']+=1
mode='group';maximum=100
for mask in range(16):
    group=[identity for i,identity in enumerate(group_profiles) if mask&(1<<i)];group_index=-1;events=[]
    outcome=bool(run(0x167aea0,x0=app,x1=targets,w2=11,w3=100))
    assert outcome==any(group_profiles[i]['rate']<100 for i in group)
    assert len(events)==sum(group_profiles[i]['rate']<100 and group_profiles[i]['human'] and group_profiles[i]['parameters'] for i in group)
    counts['joinedGroupRecovery']+=1
report=dict(nativeHerbChecks=counts,
    findings=['Holy Herb dispatch requests MP11 recovery at100 percent for the supplied team and spends one item only on aggregate recovery success.',
              'Battle target filter excludes states7/8, with no HP test in that predicate.',
              'Recovery success is rate<100; actual AddValue additionally requires Human and Parameter components.',
              'A deficient nonhuman can report recovery success without receiving MP; all-full eligible targets report failure.',
              'Fixed Holy Herb100/100 bonus invokes no RNG in GetRandomBonusValue.'],
    limits=['Original ExecuteItem Holy Herb branch with stock/global classification/team recovery supplied; native group enumeration, individual recovery and eligibility joined separately.',
            'Param rate/max/AddValue are supplied; no equipment/stat aggregation or final raw-value clamping in this fixture.',
            'State filter checked independently; input-event ordering and full team enumeration not joined here.',
            'No claim that the user-reported generic MP herbs name uniquely identifies Holy Herb31.'])
(EVIDENCE/'herb-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
print(json.dumps(report))
