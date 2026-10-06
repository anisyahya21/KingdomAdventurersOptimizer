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

from combat_lifecycle import create_entity
manager, klass, entity = 0x10004000, 0x10005000, 0x10006000
w64(manager, klass); w64(klass+0x178, 0x30000100)
events=[]
def signed64(value): return value if value < 1<<63 else value-(1<<64)
def counter(): return signed64(struct.unpack('<Q',uc.mem_read(manager+0x18,8))[0])
def external(machine,address,size,user):
    if 0x1474148 <= address < 0x14741b8:return
    if address==0x30000100:
        events.append(('create',reg('W1'),signed64(reg('X2')),counter()));value=entity
    elif address==0x147eab8:
        events.append(('add',reg('X1'),bool(reg('W2')),counter()));value=0
    else:raise AssertionError(hex(address))
    machine.reg_write(UC_ARM64_REG_X0,value);machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
checks=0
for initial in (0,1,12345,(1<<63)-1,-(1<<63),-1):
 for draft in (False,True):
  for typ in (0,1,7):
   w64(manager+0x18,initial & ((1<<64)-1));events.clear()
   run(0x1474148,x0=manager,w1=typ,w2=draft)
   expected=[];state={'created_entities':initial}
   def create(t,n):expected.append(('create',t,n,state['created_entities']));return entity
   def add(e,d):expected.append(('add',e,d,state['created_entities']))
   assert create_entity(state,typ,draft,create,add)==entity
   assert events==expected,(events,expected)
   assert counter()==state['created_entities']
   checks+=1
result=dict(nativeEntityCreationChecks=checks,scope='Original ID reservation and virtual-factory/AddEntity callback ordering including signed64 wrap and drafts',limitations=['Factory and AddEntity supplied; no complete entity registration in this fixture'])
(EVIDENCE/'entity-creation-checks.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
print(json.dumps(result))
