"""Original complete special Finish flow, with dispatch and UI callbacks supplied."""
from pathlib import Path
exec(Path(__file__).with_name('check_combat_damage_wrapper.py').read_text(encoding='utf-8').split('from itertools import product')[0])
from itertools import product

uc.mem_write(0x316ae52,b'\x01')
for i,got in enumerate((0x2f5f980,0x2f61dc0,0x2f5b890,0x2f61df8,0x2f61e08,0x2f5c140,0x2f61e10,0x2f5c138,0x2f61d70)):
    ptr,cls,statics=0x10001000+i*8,0x10002000+i*0x100,0x10003000+i*0x100
    w64(got,ptr);w64(ptr,cls);w32(cls+0xe0,1);w64(cls+0xb8,statics)
    if got==0x2f61dc0:
        w64(statics+0x48,0x10006000);w64(statics+0x60,0x10006100)
    if got==0x2f5f980:w64(statics+0x10,0x10006200)
battle,conquest,table,special,prizes,enumerable,enumerator,empty_cls=[0x10008000+i*0x100 for i in range(8)]
w64(battle+0x48,conquest);w64(battle+0x58,prizes);uc.mem_write(battle+0x65,b'\x01')
w32(conquest+0x50,19);w32(table+0x18,20);w64(table+0x20+19*8,special)
w64(enumerable,empty_cls);w64(enumerator,empty_cls)
entries={0x14edb2c:0x10010000,0x14edb9c:0x10010010,0x14edbf8:0x10010020,0x14edc74:0x10010030}
for i,entry in enumerate(entries.values()):w64(entry,0x30000100+i*4)

def external(machine,address,size,user):
    global index
    if 0x14ed864<=address<0x14edca0 or 0x14f3160<=address<0x14f316c or 0x14ee4f4<=address<0x14ee6b0:return
    result=0
    if address==0x1626d14:result=table
    elif address==0x1632ab0:
        assert reg('X0')==special and reg('W1')==1;events.append('defeat_count')
    elif address==0x18ac658:
        assert reg('X0')==prizes
        result=enumerable;index=-1
    elif address==0x1332890:result=entries[reg('LR')]
    elif address==0x30000100:result=enumerator
    elif address==0x30000104:index+=1;result=index<len(filtered)
    elif address==0x30000108:result=filtered[index]<<32  # Prize(type0,dataId,...)
    elif address==0x3000010c:events.append('dispose')
    elif address==0x14f0118:
        assert reg('X1')==conquest;released.append(reg('W2'))
    elif address==0x16b99d8:result=0x10006300
    elif address==0x18947c8:result=0x10006400
    elif address==0x237d768:
        assert reg('X1')==0x10006400;events.append('pop_battle_form')
    elif address==0x1688700:
        assert reg('X0')==conquest;result=0x10006500
    elif address==0x1473e30:
        assert reg('X0')==0x10006500;events.append('destroy_source_boss')
    else:raise AssertionError(hex(address))
    machine.reg_write(UC_ARM64_REG_X0,int(result)&0xffffffffffffffff)
    machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
filter_cases=0
for typ in (-2147483648,-1,0,1,2,7,2147483647):
    assert bool(run(0x14f3160,w1=typ))==(typ==0)
    filter_cases+=1
examples=[]
for winner,count in product((0,1,2,3),(0,1,10,60,70,256)):
    w32(battle+0x50,winner);filtered=[710+i%2 for i in range(count)];events=[];released=[]
    uc.reg_write(UC_ARM64_REG_SP,0x20008000);uc.reg_write(UC_ARM64_REG_LR,0x30000000)
    uc.reg_write(UC_ARM64_REG_X0,battle)
    uc.emu_start(0x14ed864,0x30000000,count=500000)
    assert reg('PC')==0x30000000
    assert released==(filtered if winner==1 else [])
    assert events==(['defeat_count','dispose'] if winner==1 else [])+['pop_battle_form','destroy_source_boss']
    examples.append(dict(winner=winner,queuedTreasures=count,released=len(released)))
report=dict(nativeSpecialFinishCases=len(examples),nativeTreasureFilterCases=filter_cases,examples=examples,
    findings=['Special Finish dispatches every supplied type0 treasure entry in order only when winner field equals1.',
              'No deduplication or dispatch-count clamp through256 entries in this original loop.',
              'SpecialBossData.AddDefeatCount(1) occurs once per successful Finish call, independently of repeated monster death callbacks.',
              'After dispatch: request battle form Pop, then destroy the source boss; Pop is a deferred finish request, not immediate world destruction.'],
    limits=['Original complete special Finish control flow; LINQ/enumerator, FireTreasure, form Pop and source boss Destroy supplied.',
            'Type0 filter confirmed separately by native predicate14f3160; fixture supplies already-filtered entries.',
            'Dispatch is not inventory receipt: CreateTreasure, collection, storage capacity and save persistence remain unvalidated.',
            'Form disposal internals and source-boss destruction callbacks remain outside this check.',
            'Failed Finish dispatches no treasures here; no claim about subsequent retry/recovery behavior or global maximum.'])
(EVIDENCE/'special-settlement-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
print(json.dumps({k:v for k,v in report.items() if k!='examples'}))
