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

from combat_resolution import decide_skill_invocation
for flag in [0x316b47d,0x316b47f,0x316a9c3]:uc.mem_write(flag,b'\x01')
for i,got in enumerate([0x2f64be0,0x2f5d2b8,0x2f5c538]):
    ptr,cls=0x10001000+i*8,0x10002000+i*0x100
    w64(got,ptr);w64(ptr,cls);w32(cls+0xe0,1);w64(cls+0xb8,0x10003000)
for i,(offset,rates) in enumerate([(0x158,[36,18,9]),(0x168,[80,50,30]),(0x170,[100,60,30])]):
    array=0x10003400+i*0x100;w64(0x10003000+offset,array);w32(array+0x18,3)
    for j,n in enumerate(rates):w32(array+0x20+4*j,n)
skill=0x10004000;draws=[]
def external(machine,address,size,user):
    if any(a<=address<b for a,b in [(0x15dff08,0x15e0024),(0x15e03d8,0x15e0494),(0x1460024,0x1460084)]):return
    if address==0x1686304:result=skill
    elif address==0x161c200:result=bool(row['flags']&reg('W1'))
    elif address==0x145fee4:draws.append(reg('W0'));result=roll
    else:raise AssertionError(hex(address))
    machine.reg_write(UC_ARM64_REG_X0,int(result));machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
rows=json.loads((EVIDENCE/'weapon-skill-profiles.json').read_text(encoding='utf-8'))['skills']
checks=0
for row in rows:
 for level in range(3):
  for roll in [0,8,9,17,18,29,30,35,36,49,50,59,60,79,80,99]:
   w32(skill+0x2c,row['type']);draws=[]
   actual=bool(run(0x15e03d8,x0=row['id']|(level<<32)))
   expected_draws=[]
   def rand(n):expected_draws.append(n);return roll
   expected=decide_skill_invocation(row['type'],level,row['flags'],rand)
   assert actual==expected and draws==expected_draws,(row['id'],level,roll,actual,expected,draws)
   checks+=1
report=dict(invocationCases=checks,skills=len(rows),limits=['Original DecideToUseSkill, GetInvocationRate and math.Random.Hits execute together.','Skill lookup/flag test and math.Random.Rand are stubbed; candidate eligibility is checked separately.','Three recovered rate arrays are supplied in synthetic static memory.'])
(EVIDENCE/'invocation-rate-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');print(json.dumps(report))
