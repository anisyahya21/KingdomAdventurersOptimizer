"""Execute original RandomOrDefault with supplied IList and Rand callees."""
from pathlib import Path
exec(Path(__file__).with_name('check_combat_damage_wrapper.py').read_text(encoding='utf-8').split('from itertools import product')[0])
from combat_prizes import select_prize,special_prize_candidates
from combat_resolution import random_below
obj,klass,method,rgctx,interface,slot=0x10004000,0x10005000,0x10006000,0x10007000,0x10008000,0x10009000
w64(obj,klass);w64(method+0x38,rgctx);w64(rgctx,interface);w64(rgctx+8,interface)
uc.mem_write(interface+0x135,b'\x01')
w64(0x2f5c538,0x10001000);w64(0x10001000,klass);w32(klass+0xe0,1)
calls=[];resolutions=0
def external(machine,address,size,user):
    global resolutions
    if 0x18c6404<=address<0x18c65e4:return
    if address==0x1332890:
        resolutions+=1
        w64(slot,0x30000100 if resolutions<=2 else 0x30000200);w64(slot+8,0)
        result=slot
    elif address==0x30000100:result=len(candidates)
    elif address==0x145fee4:
        calls.append(reg('W0'));result=random_below(sample,reg('W0'))
    elif address==0x30000200:result=candidates[reg('W1')]
    else:raise AssertionError(hex(address))
    machine.reg_write(UC_ARM64_REG_X0,result);machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
cases=0
for count in (0,1,2,3,64):
    for sample in (0,1,123456789,1073741823,2147483646):
        candidates=list(range(100,100+count));calls=[];resolutions=0
        run(0x18c6404,x0=obj,x1=0,x2=method)
        draws=[]
        def draw():draws.append(True);return sample
        expected=select_prize(candidates,draw)
        assert reg('X0')==(expected or 0)
        assert calls==([count] if count else []) and len(draws)==len(calls)
        cases+=1
groups=[special_prize_candidates(i) for i in range(20)]
assert all(groups)
report=dict(nativeSelectionCases=cases,encounterGroups=len(groups),candidateIds=groups,
    findings=['One Math.Rand(count) call per nonempty selection, even count1; empty list returns default without a draw.'],
    limits=['IList dispatch and Math.Rand supplied; native selection control flow executed. RNG backend checked separately.'])
(EVIDENCE/'prize-selection-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
print(json.dumps(report))
