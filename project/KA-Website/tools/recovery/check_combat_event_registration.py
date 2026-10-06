"""Native system initialization and battle/fighter event registration order."""
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

from elftools.elf.elffile import ELFFile
world,manager,list_,array,battle,fighter,klass= [0x10004000+i*0x1000 for i in range(7)]
w64(world+0x38,manager);w64(manager+0x30,list_);w64(list_+0x10,array)
w64(battle,klass);w64(fighter,klass)
for flag in (0x316ab11,0x316ac6c,0x316ae43,0x316b1bf):uc.mem_write(flag,b'\x01')
with (EVIDENCE.parent/'G2.1/39257e72291d/inputs/libil2cpp.so').open('rb') as f:
    elf=ELFFile(f);relocations={r['r_offset']:r['r_addend'] for r in elf.get_section_by_name('.rela.dyn').iter_relocations()}
script=json.loads((EVIDENCE.parent/'G2.1/39257e72291d/dump/script.json').read_text(encoding='utf-8'))
method_meta={m['Address']:m for m in script['ScriptMetadataMethod']}
got_addresses=(0x2f5f538,0x2f60dc0,0x2f60dc8,0x2f60dd0,0x2f60d10,0x2f60dd8,
    0x2f5fa18,0x2f61d40,0x2f5fa80,0x2f5fa10,0x2f61d48,0x2f5fa88,0x2f61d58,0x2f61d50,0x2f61d60,
    0x2f5fa30,0x2f64b20,0x2f5faa0,0x2f64b28,0x2f64b30)
resolved={}
for i,got in enumerate(got_addresses):
    ptr,metadata=0x10011000+i*8,0x10013000+i*0x200
    w64(got,ptr);w64(ptr,metadata);w64(metadata+0x20,0x10020000)
    target=method_meta.get(relocations.get(got))
    if target:
        w64(metadata+8,target['MethodAddress']);uc.mem_write(metadata+0x52,b'\x01')
        resolved[hex(got)]=dict(name=target['Name'],method=hex(target['MethodAddress']))
w64(0x100200c0,0x10021000);uc.mem_write(0x10020135,b'\x01')
w64(0x10021000+0x140,0x10020000)
allocation=0x10040000;events=[]
def external(machine,address,size,user):
    global allocation
    if any(a<=address<b for a,b in ((0x147a35c,0x147a3f4),(0x14c194c,0x14c1a94),
            (0x14ecb1c,0x14eccac),(0x15828f8,0x1582a88),(0x1c95ce8,0x1c95de8),
            (0x1dec758,0x1dec778),(0x1bcf55c,0x1bcf584),(0x1bcf588,0x1bcf614),(0x1bcf614,0x1bcf674))):return
    value=0
    if address==0x14c0dd8:value=2
    elif address==0x1332890:
        value=0x10022000;w64(value,0x14ecb1c if reg('X0')==battle else 0x15828f8)
    elif address==0x12d23b8:value=allocation;allocation+=0x100
    elif address==0x12d222c:value=0
    elif address==0x191d834:
        handler=reg('X2')
        method=struct.unpack('<Q',uc.mem_read(handler+0x18,8))[0]
        owner=struct.unpack('<Q',uc.mem_read(handler+0x40,8))[0]
        events.append((reg('W1'),method,owner))
    elif address==0x1bcf584:pass
    else:raise AssertionError(hex(address))
    machine.reg_write(UC_ARM64_REG_X0,value);machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
checks=0
for order in ((battle,fighter),(fighter,battle)):
    events=[];allocation=0x10040000;w32(list_+0x18,2);w32(array+0x18,2)
    for i,system in enumerate(order):w64(array+0x20+i*8,system)
    try:run(0x147a35c,x0=world)
    except Exception:
        print(hex(reg('PC')));raise
    expected=[]
    for system in order:
        expected.extend([(26,0x14f2834,system),(27,int(resolved['0x2f61d48']['method'],16),system),(7,int(resolved['0x2f61d50']['method'],16),system)] if system==battle else [(41,0x1587d50,system),(26,0x1587d60,system),(27,0x1587e64,system)])
    assert events==expected,(events,expected)
    checks+=1
report=dict(nativeSystemEventRegistrationChecks=checks,resolvedHandlerMetadata=resolved,
    scope='Original World.Init, SystemManager.Init/List enumeration, Battle/Fighter Init and EventHandler constructor',
    limitations=['Supplied init-system list/interface mapping and allocation; World.AddEventHandler recorded; native special-world constructor order established separately'])
(EVIDENCE/'event-registration-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');print(json.dumps(dict(nativeSystemEventRegistrationChecks=checks)))

