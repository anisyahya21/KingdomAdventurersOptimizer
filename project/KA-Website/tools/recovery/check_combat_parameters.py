"""Native command timing and interruption primitives, with explicit callee stubs."""
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

from itertools import product
from combat_parameters import fighter_parameter
for flag in [0x316b880,0x316b881]:uc.mem_write(flag,b'\x01')
for i,got in enumerate([0x2f5b570,0x2f5b890,0x2f5d378,0x2f5d0d0]):
    ptr,cls=0x10001000+i*8,0x10002000+i*0x100
    w64(got,ptr);w64(ptr,cls);w32(cls+0xe0,1)
entity,param,component,equip,ids,table,owner,levels=[0x10004000+i*0x100 for i in range(8)]
w64(equip+0x10,ids);w32(ids+0x18,2);w32(table+0x18,2);w64(owner+0x28,levels);w32(levels+0x18,2)
rows=[dict(level=3,pvpLevel=7,base=11),dict(level=5,pvpLevel=9,base=-13)]
for i,row in enumerate(rows):
    record=0x10005000+i*0x100;secure=0x10005200+i*0x100
    w32(ids+0x20+i*4,i);w64(table+0x20+i*8,record);w32(record+0x20,row['level']);w32(record+0x38,row['pvpLevel']);w64(levels+0x20+i*8,secure);w32(secure,20+i)
calls=[]
def external(machine,address,size,user):
    if any(a<=address<b for a,b in [(0x166070c,0x16609e8),(0x16609ec,0x1660cc4),(0x16825cc,0x168260c),(0x16657ec,0x1665800)]):return
    result=0
    if address==0x147e7e0:result=not exists
    elif address==0x1471288:result=1
    elif address==0x1471200:result=component
    elif address==0x14ce638:result=present
    elif address==0x14c89d0:result=param
    elif address==0x146de58:result=has_equipment
    elif address==0x146ddd0:result=equip
    elif address==0x161e0b8:result=table
    elif address==0x1470f10:result=has_owner
    elif address==0x1470e88:result=owner
    elif address==0x23a6c20:result=r32(reg('X0'))
    elif address==0x146ee7c:result=human
    elif address==0x146b824:result=ally
    elif address==0x146e0d4:result=0
    elif address==0x16207b8:
        index=(reg('X1')-0x10005000)//0x100;level=i32(reg('W3'));calls.append((index,level));result=rows[index]['base']+level*10
    else:raise AssertionError(hex(address))
    machine.reg_write(UC_ARM64_REG_X0,int(result)&0xffffffffffffffff);machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
checks=0
for flags in product([False,True],repeat=7):
 exists,present,has_equipment,has_owner,human,ally,maximum=flags
 for rawmax in [0,100,2147483647]:
  for raw,extra,extramax in [(70,10,20),(-100,0,-20),(2147483647,200,2147483647)]:
   for pid in [10,14,25]:
    data=dict(rawValue=raw,rawMax=rawmax,extraValue=extra,extraMax=extramax)
    for offset,key in [(0x14,'rawValue'),(0x18,'rawMax'),(0x1c,'extraValue'),(0x20,'extraMax')]:w32(param+offset,data[key])
    calls=[];actual=i32(run(0x16609ec if maximum else 0x166070c,x0=entity,w1=pid,w2=-23))
    expected_calls=[]
    def contribution(row,pid,level):expected_calls.append((rows.index(row),level));return row['base']+level*10
    expected=fighter_parameter(pid,data if present else None,rows if has_equipment else [],contribution,owner_levels=[20,21] if has_owner else None,human=human,ally=ally,exists=exists,maximum=maximum,default=-23)
    assert actual==expected,(flags,data,pid,actual,expected)
    assert calls==expected_calls,(calls,expected_calls)
    checks+=1
report=dict(effectiveParameterCases=checks,limits=['Original Param.GetValue/GetMaxValue plus Parameter getters and native Clamp execute together.','Equipment contribution arithmetic/affinity is stubbed; per-slot level selection and accumulation are executed natively.','Facility-only surrounding HP effects excluded by fighter-domain input.'])
(EVIDENCE/'effective-parameter-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');print(json.dumps(report))
