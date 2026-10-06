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

from combat_entities import CombatEntities
from itertools import product
entity,klass,array,added,changed=0x10001000,0x10002000,0x10003000,0x10004000,0x10005000
w64(entity,klass);w64(entity+0x40,array);w64(array,klass);w32(array+0x18,52)
w64(klass+0x1c8,0x30000100);w64(klass+0x188,0x147df30)
w64(added+0x18,0x30000200);w64(changed+0x18,0x30000300)
def r64(a):return struct.unpack('<Q',uc.mem_read(a,8))[0]
events=[]
def external(machine,address,size,user):
 if 0x147df30<=address<0x147dfc4 or 0x147e0a8<=address<0x147e174:return
 result=0
 if address==0x30000100:result=r64(array+0x20+reg('W1')*8)!=0
 elif address==0x12d22a8:result=reg('X0')
 elif address==0x30000200:events.append(('add',reg('W2'),reg('X3'),r64(array+0x20+reg('W2')*8)))
 elif address==0x30000300:events.append(('change',reg('W2'),reg('X3'),reg('X4'),r64(array+0x20+reg('W2')*8)))
 else:raise AssertionError(hex(address))
 machine.reg_write(UC_ARM64_REG_X0,result);machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
checks=0
for method,ctype,old,new,listeners in product((0x147df30,0x147e0a8),(0,5,19,39,51),(None,0x10006000),(None,0x10006000,0x10007000),(False,True)):
 uc.mem_write(array+0x20,bytes(52*8));w64(array+0x20+ctype*8,old or 0)
 w64(entity+0x10,added if listeners else 0);w64(entity+0x20,changed if listeners else 0);events=[]
 run(method,x0=entity,w1=ctype,x2=new or 0)
 store=CombatEntities(0,[]);i=store.allocate();u=store.objects[i];u['components'][ctype]=old;expected=[]
 pointer=lambda p:0 if p is None else p
 u['listeners'][0]=(lambda u,t,c:expected.append(('add',t,pointer(c),pointer(u['components'][t])))) if listeners else None
 u['listeners'][2]=(lambda u,t,o,n:expected.append(('change',t,pointer(o),pointer(n),pointer(u['components'][t])))) if listeners else None
 (store.add_component if method==0x147df30 else store.change_component)(i,ctype,new)
 assert events==expected and r64(array+0x20+ctype*8)==pointer(u['components'][ctype]),(hex(method),ctype,old,new,events,expected)
 checks+=1
report=dict(nativeComponentWriteCases=checks,scope='Original BaseEntity.Add/Change including duplicate, identical-reference and null writes, with callback-visible stored component',limitations=['Contains, array assignability and event delegates supplied; EntityManager forwarding separately traced'])
(EVIDENCE/'component-write-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');print(json.dumps(report))
