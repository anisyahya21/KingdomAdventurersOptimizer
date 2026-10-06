"""Execute original OnEnd roster traversal against portable ending ordering."""
from pathlib import Path
exec(Path(__file__).with_name('check_combat_damage_wrapper.py').read_text(encoding='utf-8').split('from itertools import product')[0])
from itertools import product
from combat_ending import enter_ending

uc.mem_write(0x316b1d8,b'\x01')
w64(0x2f60048,0x10001000);w64(0x10001000,0x10002000)
system,teams=0x10010000,0x10011000
w64(system+0x38,teams);w32(teams+0x18,2)
identities=[0x10020000+i*0x1000 for i in range(4)]
for team in range(2):
    roster=0x10012000+team*0x1000
    w64(teams+0x30+team*24,roster);w32(roster+0x18,2)
    for slot in range(2):w64(roster+0x20+slot*8,identities[team*2+slot])
for identity in identities:
    w64(identity+0x158,identity+0x200)

calls=[]
states={}
def external(machine,address,size,user):
    if 0x1587aa4<=address<0x1587bd8:return
    if address==0x1468bc8:
        value=reg('X0')+0x100
    elif address==0x1a6a108:
        assert reg('W1')==5
        value=states[reg('X0')-0x200]
    elif address==0x1583b48:
        identity,state=reg('X2'),reg('W1')
        calls.append((identity,state));states[identity]=state;value=0
    else:raise AssertionError(hex(address))
    machine.reg_write(UC_ARM64_REG_X0,value)
    machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
cases=0
for values in product((0,3,5,6,7,8),repeat=4):
    states=dict(zip(identities,values));calls=[]
    run(0x1587aa4,x0=system)
    units=[dict(identity=i,board={5:s},commands=[dict(opcode=29)]) for i,s in zip(identities,values)]
    expected=[]
    def change(u,s):
        expected.append((u['identity'],s));u['board'][5]=s
    result=enter_ending([units[:2],units[2:]],change)
    assert calls==expected
    assert result==(2 if values[:2]==(8,8) else 1)
    assert all(u['commands']==[dict(opcode=29)] for u in units)
    assert states=={u['identity']:u['board'][5] for u in units}
    cases+=1
report=dict(nativeOnEndCases=cases,
    finding='Original ordered roster traversal agrees with portable OnEnd state selection, including calling ChangeState(0) on state0 and preserving states7/8.',
    limits=['GetAI, Blackboard access and ChangeState callbacks supplied; state-entry side effects checked separately.',
            'Verdict assertion follows inspected EnterEnding branch; native EnterEnding and teardown not executed here.'])
(EVIDENCE/'ending-order-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
print(json.dumps(report))
