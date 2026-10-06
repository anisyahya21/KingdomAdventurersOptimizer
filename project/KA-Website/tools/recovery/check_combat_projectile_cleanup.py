"""Native projectile creation and flight, with explicit component/event stubs."""
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

from combat_projectiles import launch,projectile_update,integrate_position,parabola,update_projectile_phase
from combat_collections import EntitySlotSet
for flag in [0x316b3fd,0x316a9ce,0x316e376,0x316ab62]:uc.mem_write(flag,b'\x01')
for i,got in enumerate([0x2f5e538,0x2f5b0f0,0x2f5f980,0x2f5faf8,0x2f5fae8,0x2f5d7d0,0x2f5fd40,0x2f5fae0,0x2f5fe30,0x2f5fe20,0x2f5fe18,0x2f5b570]):
    ptr,cls=0x10001000+i*8,0x10002000+i*0x100
    w64(got,ptr);w64(ptr,cls);w32(cls+0xe0,1);w64(cls+0xb8,0x10003000)
w64(0x10003000,0x10009000)
system,entity,projectile,position,velocity,queue,items= [0x10004000+i*0x100 for i in range(7)]
w64(system+0x10,0x10006000);w64(system+0x20,0x10006100);w64(0x10006130,0x10006200)
w64(system+0x28,queue);w64(queue+0x10,items);w32(items+0x18,10)
native_ranges=[(0x1bcf0a8,0x1bcf148),(0x15cece0,0x15cf550),(0x14607f8,0x14608d4),(0x14608e4,0x14608f4),(0x23dd6a4,0x23dd700),(0x1444988,0x14449d8),(0x14832d0,0x1483504)]
entity_index,list_index,events=0,0,[]
def external(machine,address,size,user):
    global entity_index,list_index,created
    if any(a<=address<b for a,b in native_ranges):return
    if address==0x1cd2110:
        uc.mem_write(reg('X8'),bytes(24));w64(reg('X8'),native_set);w32(reg('X8')+0xc,0);result=0
    elif address in [0x1bcf0a4,0x1bcf584,0x2671a34]:result=0
    elif address==0x1471978:result=components[reg('X0')][0]
    elif address==0x1471498:result=components[reg('X0')][1]
    elif address==0x1472858:result=components[reg('X0')][2]
    elif address==0x14709ac:result=reg('X0') in modifiers
    elif address==0x146bf3c:result=0
    elif address==0x1470924:result=reg('X0')+0x400
    elif address in (0x1671ce8,0x1671cf0,0x23dd46c):machine.reg_write(UC_ARM64_REG_S0,0);result=0
    elif address==0x1470b54:
        e=reg('X0');modifiers.remove(e)
        events.append(('modifier_removed',e,tuple((other,read_vec(c[1]+0x10)) for other,c in components.items())));result=e
    elif address==0x1dec758:
        uc.mem_write(reg('X8'),bytes(24));list_index=0;result=0
    elif address==0x1bcf588:
        result=int(list_index<r32(queue+0x18));list_index+=1
        if result:w64(reg('X0')+0x10,struct.unpack('<Q',uc.mem_read(items+0x20+(list_index-1)*8,8))[0])
    elif address==0x191e2ec:
        events.append(('event',reg('W1'),reg('X2'),tuple((e,read_vec(c[1]+0x10)) for e,c in components.items())));result=0
    elif address==0x1471b84:events.append(('remove',reg('X0')));result=reg('X0')
    elif address==0x16657b0:result=max(i32(reg('W0')),i32(reg('W1')))
    elif address==0x16657ec:result=max(i32(reg('W1')),min(i32(reg('W2')),i32(reg('W0'))))
    elif address==0x1471a10:
        created=dict(vectors=tuple(struct.unpack('<f',struct.pack('<I',reg('S'+str(i))))[0] for i in range(6)),
                     speed=i32(reg('W1')),height=i32(reg('W2')),length=i32(reg('W3')),frame=i32(reg('W4')),owner=reg('X5'))
        result=entity
    elif address in [0x14709bc,0x146ba70,0x146bf4c]:result=entity
    else:raise AssertionError(hex(address))
    machine.reg_write(UC_ARM64_REG_X0,result & 0xffffffffffffffff);machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
def write_vec(base,values):uc.mem_write(base,struct.pack('<3f',*values))
def read_vec(base):return struct.unpack('<3f',uc.mem_read(base,12))

native_set,native_slots=0x10010000,0x10011000
w64(native_set+0x18,native_slots);w32(native_slots+0x18,16)
checks=0
for reuse in (False,True):
 for case in range(256):
    immediate_mask,modifier_mask=case%16,case//16
    members=EntitySlotSet();states={};components={};modifiers=set()
    entities=[0x10020000+i*0x1000 for i in range(4)]
    for i,e in enumerate(entities):
        members.add(e)
        if modifier_mask&(1<<i):modifiers.add(e)
    if reuse:
        members.remove(entities[0]);members.remove(entities[2])
        members.add(entities[0]);members.add(entities[2])
    for i,e in enumerate(entities):
        components[e]=(e+0x100,e+0x200,e+0x300)
        immediate=bool(immediate_mask&(1<<i))
        state=launch((float(i*24),15.,0.),(float(i*24),0.,0. if immediate else 240.),100 if immediate else 1)
        states[e]=state
        pr,pos,vel=components[e]
        write_vec(pr+0x10,state['start']);write_vec(pr+0x1c,state['end'])
        w32(pr+0x28,state['speed']);w32(pr+0x2c,state['height']);w32(pr+0x30,0);w32(pr+0x34,state['length'])
        write_vec(pos+0x10,state['position']);write_vec(vel+0x10,state['velocity'])
    w32(native_set+0x24,len(members.slots));w32(native_set+0x38,0)
    for i,e in enumerate(members.slots):w32(native_slots+0x20+i*16,1);w64(native_slots+0x28+i*16,e)
    original_modifiers=set(modifiers)
    events=[];run(0x15cece0,x0=system)
    expected_events=[]
    def impact(e,current,subset):
        expected_events.append(('event',41,e,tuple((other,current[other]['position']) for other in components)))
        expected_events.append(('remove',e))
    def complete(e,current):
        if e in original_modifiers:
            original_modifiers.remove(e)
            expected_events.append(('modifier_removed',e,tuple((other,current[other]['position']) for other in components)))
    update_projectile_phase(members,states,impact,complete)
    assert modifiers==original_modifiers
    assert events==expected_events,(reuse,immediate_mask,events,expected_events)
    for e,(pr,pos,vel) in components.items():
        assert read_vec(pos+0x10)==states[e]['position']
        assert read_vec(vel+0x10)==states[e]['velocity']
    checks+=1
report=dict(nativeProjectileCleanupPhaseChecks=checks,
            scope='Original ProjectileSystem.Update and HashSet.MoveNext; reused-slot orders, all16 impact masks and all16 modifier-presence masks; removal callbacks inspect partially updated positions, event callbacks see the complete trajectory pass',
            limitations=['Component getters, pending-list iteration and removal supplied; isometric transforms/Atan2 supplied, so rotation values are not validated; no impact damage subscribers'])
(EVIDENCE/'projectile-cleanup-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');print(json.dumps(report))
