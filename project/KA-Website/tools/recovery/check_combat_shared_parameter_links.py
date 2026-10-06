"""Original ParameterComponent.GetValue and linked nonrecursive ParamSet reads."""
from pathlib import Path
exec(Path(__file__).with_name('check_combat_damage_wrapper.py').read_text(encoding='utf-8').split('from itertools import product')[0])
from itertools import product
from combat_entities import CombatEntities
from combat_shared_resolution import shared_parameter_value

uc.mem_write(0x316ad33,b'\x01')
for got in (0x2f5f638,0x2f5f640):w64(got,0x10001000)
native_ids=[0x10010000+i*0x1000 for i in range(3)]
for entity in native_ids:
    w64(entity+0x100+0x10,entity+0x200);w64(entity+0x100+0x18,entity+0x300)
    w32(entity+0x300+0x18,0)
calls=[]
def external(machine,address,size,user):
    if 0x14ce670<=address<0x14ce790 or 0x16825cc<=address<0x16825dc:return
    result=0
    if address==0x1683024:result=reg('X0')+0x200
    elif address==0x1deb9a8:
        result=links[reg('W1')];calls.append(('link',result))
    elif address==0x147e7e0:result=reg('X0')==0 or bool(mask&(1<<native_ids.index(reg('X0'))))
    elif address==0x1471200:result=reg('X0')+0x100
    elif address==0x1682f7c:result=present[native_ids.index(reg('X0')-0x200)]
    else:raise AssertionError(hex(address))
    machine.reg_write(UC_ARM64_REG_X0,int(result)&0xffffffffffffffff);machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
checks=0
for mask,has_linked_hp,raw,extra,order in product(range(8),(False,True),(-5,0,100,2147483647),(-7,0,20),
        ((),(1,2),(2,1,2),(0,1),(None,1))):
    present=[True,has_linked_hp,True]
    links=[0 if i is None else native_ids[i] for i in order]
    w32(native_ids[0]+0x300+0x18,len(links))
    shared=CombatEntities(100,[]);identities=[shared.allocate() for _ in range(3)]
    for index,(native,identity) in enumerate(zip(native_ids,identities)):
        value=i32(raw+index)
        w32(native+0x400+0x14,value);w32(native+0x400+0x1c,extra)
        shared.add_component(identity,'Parameter',dict(parameters={10:dict(rawValue=value,extraValue=extra)} if present[index] else {},
             extras=[None if i is None else identities[i] for i in order] if index==0 else [identities[0]]))
        shared.objects[identity]['flags']=2 if mask&(1<<index) else 0
    calls=[];actual=i32(run(0x14ce670,x0=native_ids[0]+0x100,w1=10))
    expected=shared_parameter_value(shared,identities[0],10)
    assert actual==expected,(mask,has_linked_hp,raw,extra,order,actual,expected)
    assert calls==[('link',linked) for linked in links]
    checks+=1
report=dict(nativeSharedParameterLinkCases=checks,
    scope='Original ParameterComponent.GetValue and Parameter.value, including duplicate/self links, null/destroyed filtering, missing linked parameter and signed overflow.',
    limitations=['ParamSet/list/component access supplied; caller already has own parameter. No evidence that a captured special-fight unit has nonempty extras.'])
(EVIDENCE/'shared-parameter-links-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
print(json.dumps(report))
