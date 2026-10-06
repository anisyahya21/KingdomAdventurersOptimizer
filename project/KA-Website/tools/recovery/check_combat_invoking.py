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
from combat_resolution import consume_invoking_attack
for i,got in enumerate([0x2f69a60,0x2f69a68,0x2f69a50]):w64(got,0x10001000+i*8)
list_=0x10004000
entries=[]
def external(machine,address,size,user):
    if 0x168dad0<=address<0x168db5c:return
    index=reg('W1');result=0
    if address==0x1dd0638:
        sid,count=entries[index];result=(sid&0xffffffff)|((count&0xffffffff)<<32)
    elif address==0x1dd068c:
        value=reg('X2');entries[index]=(i32(value),i32(value>>32))
    elif address==0x1dd2188:entries.pop(index);w32(list_+0x18,len(entries))
    else:raise AssertionError(hex(address))
    machine.reg_write(UC_ARM64_REG_X0,result);machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
consumption_hook=uc.hook_add(UC_HOOK_CODE,external)
checks=0
for counts in product([-2147483648,-1,0,1,2,10,2147483647],repeat=3):
    original=list(enumerate(counts));entries=list(original);w32(list_+0x18,len(entries))
    uc.reg_write(UC_ARM64_REG_SP,0x20008000);uc.reg_write(UC_ARM64_REG_X26,list_)
    uc.emu_start(0x168dad0,0x168db5c,count=10000)
    assert reg('PC')==0x168db5c
    assert entries==consume_invoking_attack(original),(original,entries)
    checks+=1
uc.hook_del(consumption_hook)
# Attack-tail append producer. BattleHelper.Attack 0x168d1e8 appends its own entry through
# List<InvokingSkill>$$AddWithResize 0x1dd0928 at 0x168dea4, packing (dataId, 10) at 0x168de5c..
# 0x168de60; InvokingSkill..ctor 0x1686204 has no recorded caller in the index because it is
# inlined into that sequence. Execute the fast-path store 0x168de40..0x168deac and compare the
# stored pair with the model append in combat_resolution.attacker_invocation_phase.
from combat_farmer_slice import ROWS
from combat_resolution import attacker_invocation_phase
appends=0
for sid in (22,23,24,25,29,30,108,40,41,109,110):
    for count in (0,1,7):
        list_,array=0x10006000,0x10007000
        w32(list_+0x10,array);w32(list_+0x18,count);w32(list_+0x1c,0);w32(array+0x18,count+4)
        uc.mem_write(array+0x20+count*8,b'\xee'*8)
        uc.reg_write(UC_ARM64_REG_SP,0x20008000)
        uc.reg_write(UC_ARM64_REG_X0,list_);uc.reg_write(UC_ARM64_REG_X9,sid)
        uc.reg_write(UC_ARM64_REG_X10,0x10009000);uc.reg_write(UC_ARM64_REG_X11,0)
        uc.emu_start(0x168de40,0x168deac,count=200)
        assert reg('PC')==0x168deac,hex(reg('PC'))
        packed=struct.unpack('<Q',uc.mem_read(array+0x20+count*8,8))[0]
        expected=[];attacker_invocation_phase(expected,[ROWS[sid]],lambda s:True,lambda s:True,
            lambda s:None,lambda s:None)
        assert expected==[(sid,10)],expected
        assert (packed&0xffffffff,packed>>32)==expected[0],(sid,count,hex(packed))
        assert r32(list_+0x18)==count+1 and r32(list_+0x1c)==1 and r32(array+0x18)==count+4
        appends+=1
report=dict(invokingConsumptionCases=checks,invokingAppendCases=appends,
    appendProducer='BattleHelper.Attack 0x168d1e8 -> List<InvokingSkill>.AddWithResize 0x1dd0928 at 0x168dea4 (InvokingSkill..ctor 0x1686204 inlined)',
    limits=['Executes original Attack tail loop and the Attack-tail append store, with list get/set/remove/add stubs.',
            'Does not execute subsequent invocation selection, MP spending or balloon effects; the slow-path AddWithResize branch is not entered.'])
(EVIDENCE/'invoking-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');print(json.dumps(report))
