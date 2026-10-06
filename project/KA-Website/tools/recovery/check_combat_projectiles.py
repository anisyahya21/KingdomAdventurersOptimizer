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

from combat_projectiles import launch,projectile_update,integrate_position,parabola
for flag in [0x316b3fd,0x316a9ce,0x316e376,0x316ab62]:uc.mem_write(flag,b'\x01')
for i,got in enumerate([0x2f5e538,0x2f5b0f0,0x2f5f980,0x2f5faf8,0x2f5fae8,0x2f5d7d0,0x2f5fd40,0x2f5fae0,0x2f5fe30,0x2f5fe20,0x2f5fe18,0x2f5b570]):
    ptr,cls=0x10001000+i*8,0x10002000+i*0x100
    w64(got,ptr);w64(ptr,cls);w32(cls+0xe0,1);w64(cls+0xb8,0x10003000)
system,entity,projectile,position,velocity,queue,items= [0x10004000+i*0x100 for i in range(7)]
w64(system+0x10,0x10006000);w64(system+0x20,0x10006100);w64(0x10006130,0x10006200)
w64(system+0x28,queue);w64(queue+0x10,items);w32(items+0x18,10)
native_ranges=[(0x15cece0,0x15cf550),(0x14607f8,0x14608d4),(0x14608e4,0x14608f4),(0x23dd6a4,0x23dd700),(0x1444988,0x14449d8),(0x14832d0,0x1483504)]
entity_index,list_index,events=0,0,[]
def external(machine,address,size,user):
    global entity_index,list_index,created
    if any(a<=address<b for a,b in native_ranges):return
    if address==0x1cd2110:
        uc.mem_write(reg('X8'),bytes(24));entity_index=0;result=0
    elif address==0x1bcf0a8:
        result=int(entity_index==0);entity_index+=1
        if result:w64(reg('X0')+0x10,entity)
    elif address in [0x1bcf0a4,0x1bcf584,0x2671a34]:result=0
    elif address==0x1471978:result=projectile
    elif address==0x1471498:result=position
    elif address==0x1472858:result=velocity
    elif address in [0x14709ac,0x146bf3c]:result=0 # rotation/trail disabled; trajectory branches unchanged
    elif address==0x1dec758:
        uc.mem_write(reg('X8'),bytes(24));list_index=0;result=0
    elif address==0x1bcf588:
        result=int(list_index<r32(queue+0x18));list_index+=1
        if result:w64(reg('X0')+0x10,entity)
    elif address==0x191e2ec:
        events.append(('event',reg('W1')));result=0
    elif address==0x1471b84:events.append(('remove',));result=entity
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
checks=0;traces=[]
launch_checks=0
for start in [(0.,15.,0.),(48.,0.,96.)]:
    for end in [(0.,0.,0.),(1.,15.,1.),(0.,0.,24.),(96.,0.,144.),(-96.,30.,-48.),start]:
        for speed in [-100,0,1,10,100,2147483647]:
            expected=launch(start,end,speed)
            floats={'s'+str(i):struct.unpack('<I',struct.pack('<f',v))[0] for i,v in enumerate(start+end)}
            result=i32(run(0x14832d0,x0=entity,w1=speed,x2=0x10008000,w3=1,x4=0,**floats))
            assert created==dict(vectors=start+end,speed=expected['speed'],height=expected['height'],length=expected['length'],frame=0,owner=0x10008000),(created,expected)
            assert result==expected['length']
            launch_checks+=1
for start in [(0.,15.,0.),(48.,0.,96.)]:
    for end in [(0.,0.,0.),(1.,15.,1.),(0.,0.,24.),(96.,0.,144.),(-96.,30.,-48.),start]:
        for speed in [1,10,100]:
            state=launch(start,end,speed);write_vec(projectile+0x10,state['start']);write_vec(projectile+0x1c,state['end'])
            w32(projectile+0x28,state['speed']);w32(projectile+0x2c,state['height']);w32(projectile+0x30,0);w32(projectile+0x34,state['length'])
            for tick in range(1000):
                write_vec(position+0x10,state['position']);write_vec(velocity+0x10,state['velocity']);events=[]
                try:run(0x15cece0,x0=system)
                except Exception:
                    print("native failure",hex(reg("PC")),state);raise
                expected=projectile_update(state)
                assert read_vec(position+0x10)==expected['position'],(state,read_vec(position+0x10),expected)
                assert read_vec(velocity+0x10)==expected['velocity']
                assert r32(projectile+0x30)==expected['frame']
                assert events==([('event',41),('remove',)] if expected['impacted'] else [])
                checks+=1
                if expected['impacted']:
                    traces.append(dict(start=start,end=end,speed=speed,length=state['length'],impactUpdate=tick+1));break
                state=integrate_position(expected)
            else:raise AssertionError('No impact within bounded test')
parabola_checks=0
for height in [20,21,50,100]:
    for length in [0,1,2,3,7,10,31]:
        for frame in range(0,2*length+4):
            assert i32(run(0x1444988,w0=height,w1=length,w2=frame))==parabola(height,length,frame)
            parabola_checks+=1
report=dict(launchChecks=launch_checks,trajectoryUpdates=checks,trajectories=len(traces),parabolaChecks=parabola_checks,traces=traces,limits=['Original launcher arithmetic executes with clamp/component creation stubs.','Original ProjectileSystem arithmetic/impact decision executes with one-entity enumeration and component getter stubs.','Screen-space rotation/trail disabled; impact event recorded without damage subscribers.','MoveSystem position addition is composed from the recovered float32 rule, not native system enumeration.'])
(EVIDENCE/'projectile-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');print(json.dumps({k:v for k,v in report.items() if k!='traces'}))
