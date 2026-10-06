"""Original CanDamage with shared component-backed HP eligibility."""
from pathlib import Path
exec(Path(__file__).with_name('check_combat_damage_wrapper.py').read_text(encoding='utf-8').split('from itertools import product')[0])
from itertools import product
from combat_entities import CombatEntities
from combat_targeting import can_damage_shared
from combat_shared_resolution import shared_parameter_value

attacker,target,parameter,monster,worlds,data=0x10004000,0x10005000,0x10006000,0x10007000,0x10008000,0x10009000
w64(target+0x30,0x1000a000)
def external(machine,address,size,user):
    if 0x168e8cc<=address<0x168ea2c:return
    result=0
    if address==0x1471288:result=has_parameter
    elif address==0x1471200:result=parameter
    elif address==0x14ce638:result=has_hp
    elif address==0x146fd60:result=map_chip
    elif address==0x14ce670:result=i32(raw_hp+extra_hp)
    elif address==0x1470c84:result=has_monster
    elif address==0x1470bfc:result=monster
    elif address==0x14cdc10:result=boss
    elif address==0x14c2b14:result=worlds
    elif address==0x146b824:result=attacker_ally if reg('X0')==attacker else target_ally
    elif address==0x14cdb94:result=data
    else:raise AssertionError(hex(address))
    machine.reg_write(UC_ARM64_REG_X0,int(result)&0xffffffffffffffff);machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
checks=0
for flags,hps,monster_type in product(product((False,True),repeat=8),((0,0),(0,5),(5,-10),(100,0)),(0,1)):
    has_parameter,has_hp,map_chip,has_monster,boss,main_world,attacker_ally,target_ally=flags
    raw_hp,extra_hp=hps;w32(data+0x30,monster_type);w64(worlds+0x10,0x1000a000 if main_world else 0x1000b000)
    # Non-null skill is the projectile/AoE combat path in scope.
    actual=run(0x168e8cc,x0=attacker,x1=target,x2=0x1000c000)
    shared=CombatEntities(100,[]);a,b=shared.allocate(),shared.allocate()
    if has_parameter:shared.add_component(b,33,dict(parameters={10:dict(rawValue=raw_hp,extraValue=extra_hp)} if has_hp else {},extras=[]))
    for identity,component,present in ((b,11,map_chip),(b,20,has_monster),(a,49,attacker_ally),(b,49,target_ally)):
        if present:shared.add_component(identity,component,{})
    expected=can_damage_shared(shared,a,b,{},lambda i:boss,lambda i:main_world,lambda i:dict(type=monster_type))
    assert actual==expected,(flags,hps,monster_type,actual,expected)
    checks+=1
report=dict(nativeSharedDamageFilterCases=checks,
    scope='Original CanDamage compared with component-backed eligibility, including extra HP and special-world boss admission.',
    limitations=['Component access, stored-value getter, monster data/boss and world singleton supplied; linked HP getter separately native-checked.',
                'Non-null skill path only; no original encounter runtime capture.'])
(EVIDENCE/'shared-damage-filter-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
print(json.dumps(report))
