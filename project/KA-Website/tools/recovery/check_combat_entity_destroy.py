"""Native entity destruction callbacks, component removal and final flag order."""
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

from combat_lifecycle import destroy_entity
entity,world,manager,entity_class,manager_class,components,removed_handler,args=[0x10004000+i*0x1000 for i in range(8)]
w64(entity,entity_class);w64(entity+0x30,world);w64(world+0x30,manager)
w64(manager,manager_class);w64(manager+0x38,world);w64(manager+0x20,0x1000d000);w64(entity+0x40,components)
w64(manager_class+0x1a8,0x14d4e98);w64(entity_class+0x198,0x147dfd8)
w64(entity_class+0x1a8,0x147e030);w64(entity_class+0x1c8,0x30000100)
w64(removed_handler+0x18,0x30000104);w64(removed_handler+0x40,manager)
w32(components+0x18,6);w64(entity+0x28,123)
for flag in (0x316ab33,0x316ad82,0x316ad88):uc.mem_write(flag,b'\x01')
for got in (0x2f5f0c8,0x2f61510,0x2f61518,0x2f613a0):w64(got,0x10001000)
w64(0x10001000,0x10002000);w32(0x100020e0,1)
events=[];indexed=True
def snapshot():
    return (uc.mem_read(entity+0x38,1)[0],tuple(struct.unpack('<6Q',uc.mem_read(components+0x20,48))),
            tuple(struct.unpack('<3Q',uc.mem_read(entity+0x10,24))),indexed)
def external(machine,address,size,user):
    global indexed
    if any(a<=address<b for a,b in ((0x1473e30,0x1473ef8),(0x14d4e98,0x14d4f28),
            (0x14d4f28,0x14d4f94),(0x147dfd8,0x147e0a8))):return
    value=0
    if address==0x12d23b8:value=args
    elif address in (0x2691aa0,0x2638460):pass
    elif address==0x191e2ec:events.append(('destroyed_event',reg('W1'),snapshot()))
    elif address==0x30000100:value=bool(struct.unpack('<Q',uc.mem_read(components+0x20+reg('W1')*8,8))[0])
    elif address==0x30000104:events.append(('removed',reg('W2'),reg('X3'),snapshot()))
    elif address==0x1b2fe84:events.append(('index_remove',reg('X1'),snapshot()));indexed=False;value=1
    else:raise AssertionError(hex(address))
    machine.reg_write(UC_ARM64_REG_X0,int(value));machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
checks=0
for mask in range(64):
    for flags in range(8):
        for has_listener in (False,True):
            indexed=True;events=[];values=[0x123000+i if mask&(1<<i) else 0 for i in range(6)]
            listeners=[0x11,removed_handler if has_listener else 0,0x33]
            uc.mem_write(components+0x20,struct.pack('<6Q',*values));uc.mem_write(entity+0x10,struct.pack('<3Q',*listeners));uc.mem_write(entity+0x38,bytes([flags]))
            try:run(0x1473e30,x0=entity)
            except Exception:
                print(hex(reg('PC')));raise
            first_events=list(events);run(0x1473e30,x0=entity);assert events==first_events
            model=dict(flags=flags,components=[v or None for v in values],listeners=[v or None for v in listeners],indexed=True)
            expected=[]
            def snap(unit):return (unit['flags'],tuple(v or 0 for v in unit['components']),tuple(v or 0 for v in unit['listeners']),unit['indexed'])
            def on_destroyed(unit):expected.append(('destroyed_event',57,snap(unit)))
            def removed(unit,index,component):expected.append(('removed',index,component,snap(unit)))
            def remove_index(unit):expected.append(('index_remove',123,snap(unit)));unit['indexed']=False
            destroy_entity(model,on_destroyed,removed,remove_index)
            assert events==expected,(mask,flags,has_listener,events,expected)
            assert snapshot()==snap(model)
            checks+=1
report=dict(nativeEntityDestroyCases=checks,
    scope='Original Entity.Destroy, EntityManager.DestroyEntity, MyEntityManager.OnEntityDestroyed, BaseEntity.RemoveAll/Remove',
    limitations=['Contains, event delivery, dictionary removal and allocation supplied; no actual subset/cell callbacks'])
(EVIDENCE/'entity-destroy-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');print(json.dumps(report))

