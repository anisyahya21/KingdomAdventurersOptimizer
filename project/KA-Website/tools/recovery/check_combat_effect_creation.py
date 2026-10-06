"""Native effect creation component order and resource-derived lifetime."""
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

from combat_effects import create_combat_effect
from itertools import product
world,entity,resource,resources,seb=0x10004000,0x10005000,0x10006000,0x10007000,0x10008000
w64(world+0x30,0x10009000);w64(resource+0x18,resources);w32(resources+0x18,2);w64(resources+0x28,seb)
uc.mem_write(0x316ab0a,b'\x01');w64(0x2f5b570,0x10001000);w64(0x10001000,0x10002000);w32(0x100020e0,1)
events=[]
def floats():return tuple(struct.unpack('<f',struct.pack('<I',reg('S'+str(i))))[0] for i in range(6))
def external(machine,address,size,user):
    if 0x1478ea0<=address<0x1479108:return
    value=entity
    if address==0x1665090:value=world
    elif address==0x1663ca0:events.append(('resource',reg('W1'),1));value=resource
    elif address==0x1474148:events.append(('allocate',))
    elif address==0x146d6dc:events.append(('Effect',(i32(reg('W1')),i32(reg('W2')),i32(reg('W3')),bool(reg('W4')),i32(reg('W5')),i32(reg('W6')),reg('X7') or None,r32(reg('SP')))))
    elif address==0x1471530:events.append(('Position',floats()+(reg('X1') or None,)))
    elif address==0x14728f0:events.append(('Speed',floats()[:3]))
    elif address==0x1472260:events.append(('Seb',tuple(i32(reg('W'+str(i))) for i in range(1,5))))
    elif address==0x146cb3c:events.append(('Cell',(i32(reg('W1')),i32(reg('W2')))))
    elif address==0x146d370:events.append(('Depth',(i32(reg('W1')),i32(reg('W2')))))
    elif address==0x146e6bc:events.append(('Garbage',(i32(reg('W1')),)))
    elif address==0x146f154:events.append(('Image',tuple(i32(reg('W'+str(i))) for i in range(1,7))))
    elif address==0x146ba70:events.append(('Animation',(i32(reg('W1')),i32(reg('W2')))))
    else:raise AssertionError(hex(address))
    machine.reg_write(UC_ARM64_REG_X0,value);machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
checks=0
for depth,loop,animate,image,max_frame,resource_duration,x in product((False,True),(False,True),(False,True),(-1,3),(0,30),(0,1,20),(-24.75,24.75)):
    events=[];w32(seb+0x1c,resource_duration)
    spec=dict(type=12,value1=7,value2=8,depth=depth,loop=loop,frame=2,max_frame=max_frame,
              parent=0x1234,scale=150,res=28,seb=1,image=image,animate=animate,position=(x,15.,x))
    for offset,value in ((0,2),(8,0x1234),(16,image),(24,int(animate)),(32,8),(40,150)):w64(0x20008000+offset,value&0xffffffffffffffff)
    registers={f's{i}':struct.unpack('<I',struct.pack('<f',v))[0] for i,v in enumerate(spec['position'])}
    run(0x1478ea0,x0=world,w1=12,w2=7,w3=28,w4=1,w5=int(depth),w6=int(loop),w7=max_frame,**registers)
    expected=[]
    def resource_frames(res,seb):expected.append(('resource',res,seb));return resource_duration
    def allocate():expected.append(('allocate',));return entity
    result=create_combat_effect(spec,resource_frames,allocate,lambda e,component,args:expected.append((component,args)))
    assert reg('X0')==result
    assert events==expected,(spec,resource_duration,events,expected)
    checks+=1
report=dict(nativeEffectCreationCases=checks,
    scope='Full original World.CreateEffect with ordered component callback comparison; optional cell/depth, image, animation and resource-derived lifetime',
    limitations=['Resource frame counts, entity allocation and component-add implementations supplied; no complete subset/renderer state'])
(EVIDENCE/'effect-creation-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');print(json.dumps(report))

