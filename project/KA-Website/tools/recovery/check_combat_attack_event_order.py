"""Native multicast Battle/Fighter attack subscriber batch order."""
import json
import struct
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE
from unicorn.arm64_const import *
from combat_initial_state import EVIDENCE, i32

binary = (EVIDENCE.parent / 'G2.1/39257e72291d/inputs/libil2cpp.so').read_bytes()
audit = json.loads((EVIDENCE / 'audit.json').read_text())
uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
for base, size in [(0x1000000, 0x2300000), (0x10000000, 0x100000),
                   (0x20000000, 0x10000), (0x30000000, 0x1000)]: uc.mem_map(base, size)
for entry in audit['methods']:
    start, end, offset = [int(entry[k], 16) for k in ['rva', 'end', 'offset']]
    uc.mem_write(start, binary[offset:offset+end-start])

def w32(a, n): uc.mem_write(a, struct.pack('<I', n & 0xffffffff))
def w64(a, n): uc.mem_write(a, struct.pack('<Q', n))
def r32(a): return struct.unpack('<i', uc.mem_read(a, 4))[0]
def reg(n): return uc.reg_read(globals()['UC_ARM64_REG_' + n])
def run(start, **regs):
    uc.reg_write(UC_ARM64_REG_SP, 0x20008000)
    uc.reg_write(UC_ARM64_REG_LR, 0x30000000)
    for name, value in regs.items():
        uc.reg_write(globals()['UC_ARM64_REG_' + name.upper()], value & 0xffffffffffffffff)
    uc.emu_start(start, 0x30000000, count=10000)
    assert reg('PC') == 0x30000000, hex(reg('PC'))
    return reg('W0')

from combat_events import dispatch_attack_event
from combat_resolution import apply_fighter_attack_results,subtract_raw_parameter
from combat_entities import CombatEntities
from combat_shared_resolution import dispatch_shared_attack
from itertools import product
for entry in json.loads((EVIDENCE/'event-runtime-slices.json').read_text(encoding='utf-8')):
    start,end,offset=[int(entry[k],16) for k in ('rva','end','offset')]
    uc.mem_write(start,binary[offset:offset+end-start])
manager,combined,listeners,results=0x10004000,0x10005000,0x10006000,0x10007000
w64(combined+0x18,0x11725bc);w64(combined+0x40,combined);w64(combined+0x78,listeners)
w32(listeners+0x18,2)
for i,method in enumerate((0x14f2834,0x1587d60)):
    handler=0x10008000+i*0x100;w64(listeners+0x20+i*8,handler);w64(handler+0x18,method);w64(handler+0x40,manager)
for flag in (0x316c35c,0x316b1da):uc.mem_write(flag,b'\x01')
for got in (0x2f6f240,0x2f5fd88):w64(got,0x10001000)
w64(0x10001000,0x10002000)
targets=[0x10010000+i*0x1000 for i in range(3)]
events=[]
def hp_snapshot():return tuple(r32(t+0x14) for t in targets)
def external(machine,address,size,user):
    if any(a<=address<b for a,b in ((0x18b2f00,0x18b2f8c),(0x11725bc,0x1172604),
            (0x14f2834,0x14f28a4),(0x1587d60,0x1587e64),(0x16828c8,0x1682910),(0x22141a8,0x22141b8))):return
    value=0
    if address==0x18c05a0:value=combined
    elif address==0x168f480:
        receiver=struct.unpack('<Q',uc.mem_read(reg('X0')+8,8))[0]
        events.append(('effect',receiver,hp_snapshot()))
    elif address==0x147e7e0:value=bool(destroyed_mask&(1<<targets.index(reg('X0'))))
    elif address in (0x1471200,0x14c89d0):value=reg('X0')
    elif address==0x13eb3c8:value=max(i32(reg('W0')),i32(reg('W1')))
    elif address==0x1583b48:events.append(('state',reg('X2'),reg('W1'),hp_snapshot()))
    else:raise AssertionError(hex(address))
    machine.reg_write(UC_ARM64_REG_X0,int(value));machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
checks=0
for hit_mask,destroyed_mask,order in product(range(8),range(8),((0,1,2),(2,0,1),(0,1,0))):
    events=[];model=[];w32(results+0x18,3)
    shared=CombatEntities(100,[]);identities={}
    for index,target in enumerate(targets):
        identity=shared.allocate();identities[target]=identity
        shared.add_component(identity,'Parameter',dict(parameters={10:dict(rawValue=100,rawMax=100)}))
        shared.objects[identity]['flags']=2 if destroyed_mask&(1<<index) else 0
    native_ids={identity:target for target,identity in identities.items()}
    for target in targets:w32(target+0x14,100);w32(target+0x18,100)
    for i,index in enumerate(order):
        target=targets[index];hit=bool(hit_mask&(1<<i));damage=(0,99,101)[i]
        uc.mem_write(results+0x20+i*32,struct.pack('<QQ??2xiQ',0x1234,target,hit,False,damage,0))
        model.append(dict(target=identities[target],hit=hit,damage=damage))
    run(0x18b2f00,x0=manager,w1=26,x2=results)
    expected=[]
    def snap():return tuple(shared.fighter(identities[t])['parameters'][10]['rawValue'] for t in targets)
    def effect(result):expected.append(('effect',native_ids[result['target']],snap()))
    def change(fighter,state):expected.append(('state',native_ids[fighter['id']],state,snap()))
    dispatch_shared_attack(shared,model,effect,change)
    assert events==expected,(hit_mask,destroyed_mask,order,events,expected)
    assert hp_snapshot()==snap()
    checks+=1
report=dict(nativeAttackSubscriberBatchChecks=checks, sharedComponentStorage=True,
    scope='Original EventManager.Send, multicast invoker, Battle.OnAttack then Fighter.OnAttack and raw HP subtraction',
    limitations=['Effects and state transitions recorded; binding uses independently recovered special-battle registration order; not complete effect/component lifecycle'])
(EVIDENCE/'attack-event-order-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');print(json.dumps(report))

