"""Original area-range opponent state and cell predicates, independent of HP."""
from pathlib import Path
exec(Path(__file__).with_name('check_combat_damage_wrapper.py').read_text(encoding='utf-8').split('from itertools import product')[0])
from itertools import product
from combat_targeting import opponent_in_skill_cells
uc.mem_write(0x316b200,b'\x01')
for got in (0x2f64d00,0x2f60048,0x2f601c8,0x2f64d10,0x2f64d08):w64(got,0x10001000)
w64(0x10001000,0x10002000)
closure,entity,ai,board,cell=tuple(0x10010000+i*0x1000 for i in range(5))
w64(closure+0x10,entity);w64(ai+0x58,board)
state=0;in_range=False
def external(machine,address,size,user):
    if 0x1589ca4<=address<0x1589dfc or 0x1589e08<=address<0x1589e70:return
    if address==0x12d23b8:result=0x10020000
    elif address in (0x2691aa0,0x1cb4ba4):result=0
    elif address==0x1468bc8:result=ai
    elif address==0x1a6a108:result=state
    elif address==0x187c518:result=int(in_range)
    elif address==0x146caa4:result=cell
    else:raise AssertionError(hex(address))
    machine.reg_write(UC_ARM64_REG_X0,result);machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
count=0
for state,in_range in product(range(9),(False,True)):
    run(0x1589ca4,x0=closure,x1=entity)
    expected=opponent_in_skill_cells([1],[(0,0)] if in_range else [(1,1)],lambda i:state,lambda i:(0,0))
    assert bool(reg('W0'))==expected;count+=1
cells=0
for x,y,a,b in product((-1,0,4),(0,3,100),(-1,0,4),(0,3,100)):
    w32(cell+0x10,x);w32(cell+0x14,y)
    run(0x1589e08,x0=closure,x1=(a&0xffffffff)|((b&0xffffffff)<<32))
    assert bool(reg('W0'))==((x,y)==(a,b));cells+=1
report=dict(nativeStateCases=count,nativeCellCases=cells,
    finding='Area candidate gate excludes states7/8 and compares stored cell equality; no HP getter in this predicate.',
    limits=['Native predicate instructions; allocation, component/blackboard access and Any<Location> supplied.',
            'Full outer roster enumeration and actual area damage filtering are separate.'])
(EVIDENCE/'area-gate-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
print(json.dumps(report))
