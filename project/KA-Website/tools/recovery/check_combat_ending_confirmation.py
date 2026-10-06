"""Original ending key gate, frame arithmetic and empty EXP entry prefix."""
from pathlib import Path
exec(Path(__file__).with_name('check_combat_damage_wrapper.py').read_text(encoding='utf-8').split('from itertools import product')[0])
from itertools import product
from combat_ending import advance_battle_frame,update_ending,experience_prizes
for flag in (0x316ae4f,0x316ae45,0x316ae50):uc.mem_write(flag,b'\x01')
for got in (0x2f5f980,0x2f61dd0,0x2f61dc0,0x2f61df8,0x2f61ea0,0x2f61eb8,0x2f61e90):w64(got,0x10001000)
w64(0x10001000,0x10002000);w32(0x100020e0,1);w64(0x100020b8,0x10003000)
w64(0x10003000,0x10004100);w64(0x10003008,0x10004200);w64(0x10003020,0x10004300)
battle=0x10005000;events=[];pulse=False
ranges=[(0x14ef4e4,0x14ef5a4),(0x14ed47c,0x14ed510),(0x14f3070,0x14f307c),(0x14ef5a8,0x14ef880),(0x23438b4,0x23438e8)]
def external(machine,address,size,user):
    if any(a<=address<b for a,b in ranges):return
    result=0
    if address==0x22f44c8:
        assert reg('W1')==0x100000;result=pulse
    elif address==0x14ed3d4:
        assert reg('W1')==4;events.append('getting_exp')
    elif address==0x14ed864:events.append('finish')
    elif address==0x1b114c8:
        result=0x10006000;w64(result+0x18,0x30000100)
    elif address==0x30000100:events.append('update')
    elif address==0x12d23b8:result=0x10007000
    elif address==0x14f35f4:pass
    elif address==0x1665f1c:assert reg('W1')==4;events.append('bgm')
    elif address==0x18ac658:result=0x10008000;events.append('filter')
    elif address==0x18a72e8:result=0x10009000
    elif address==0x18c4bac:result=True
    else:raise AssertionError(hex(address))
    machine.reg_write(UC_ARM64_REG_X0,result);machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
checks=0
for frame,pulse,conquest,winner in product((-2147483648,-1,0,1,78,79,80,81,2147483647),(False,True),(False,True),(0,1,2,3)):
    events=[];w32(battle+0x3c,frame);w32(battle+0x50,winner);w64(battle+0x48,0x1000a000 if conquest else 0)
    run(0x14ef4e4,x0=battle)
    expected,action=update_ending(frame,pulse,conquest,winner)
    assert (r32(battle+0x3c),events)==(expected,[] if action is None else [action]);checks+=1
for frame in (-2147483648,-2147483647,-2,-1,0,79,80,2147483645,2147483646,2147483647):
    events=[];w32(battle+0x3c,frame);w64(battle+0x28,0x1000b000)
    run(0x14ed47c,x0=battle)
    assert r32(battle+0x3c)==advance_battle_frame(frame),(frame,r32(battle+0x3c),advance_battle_frame(frame))
    assert events==['update']
for typ in (-1,0,1,2,7,2147483647):
    assert bool(run(0x14f3070,w1=typ))==bool(experience_prizes([dict(type=typ)]))
events=[];w64(battle+0x58,0x1000c000)
try:run(0x14ef5a8,x0=battle)
except Exception as exc:raise AssertionError(f'EXP prefix PC={reg("PC"):x}, events={events}') from exc
assert events==['bgm','filter','finish'],events
keypad=0x1000d000
for bits in (0,0x100000,0x100001,0x200000,0x7fffffff):
    w32(keypad+0x38,bits)
    assert bool(run(0x23438b4,x0=keypad,w1=0x100000))==bool(bits&0x100000)
    assert r32(keypad+0x38)==bits&~0x100000
    assert not run(0x23438b4,x0=keypad,w1=0x100000)
report=dict(nativeEndingKeyCases=checks,nativeFrameCases=10,nativeExpFilterCases=6,nativeEmptyExpEntry=True,
    nativeKeypadConsumptionCases=5,
    findings=['A pulse at frame<=79 writes80 without finishing; a later pulse above79 enters EXP on conquest victory, otherwise Finish.',
              'No pulse leaves Ending active; Update increments its frame before the gate.',
              'Empty type1 EXP selection calls Finish immediately after BGM4. Special departure skips ordinary EXP creation.',
              'Keypad pulse mask0x100000 is consumed on read; repeated queries cannot reuse that same stored pulse.'],
    limits=['Canvas pulse, state/Finish callbacks and LINQ supplied; complete Finish/source-world receipt not executed.'])
(EVIDENCE/'ending-confirmation-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
print(json.dumps(report))
