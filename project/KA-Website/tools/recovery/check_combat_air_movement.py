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

from combat_spatial import can_move_in_air
from combat_entities import CombatEntities
from itertools import product
for flag in (0x316ab78,0x316b748):uc.mem_write(flag,b'\x01')
for got in (0x2f5f410,0x2f5fb88,0x2f5b890,0x2f5dd38):w64(got,0x10001000)
w64(0x10001000,0x10002000);w32(0x100020e0,1)
entity,human,ai,board_obj,vehicle_table,stream=0x10004000,0x10005000,0x10006000,0x10007000,0x10008000,0x10009000
w64(ai+0x58,board_obj);w32(vehicle_table+0x18,4)
vehicle_rows=[list(map(int,line.split('\t'))) for line in (EVIDENCE.parent/'20260911-treasure/xls-original/English.lproj/Vehicle.txt').read_text(encoding='utf-8-sig').splitlines() if line]
vehicles=[];tokens=[];board={};events=[]
def external(machine,address,size,user):
 if any(a<=address<b for a,b in ((0x148cca4,0x148cdac),(0x148b7fc,0x148b840),(0x1635b24,0x1635c28))):return
 result=0
 if address==0x146ee7c:result=has_human
 elif address==0x146edf4:result=human
 elif address==0x14cab78:assert reg('W1')==4;result=bool(human_flags&4)
 elif address==0x1468bc8:result=ai
 elif address==0x1a6a1cc:assert reg('W1')==55;result=board.get(55,i32(reg('W2')))
 elif address==0x1627354:events.append('vehicle_table');result=vehicle_table
 elif address==0x161c200:result=bool(r32(reg('X0')+0x1c)&reg('W1'))
 elif address==0x24dad50:result=0
 elif address==0x12d23b8:result=stream
 elif address==0x1456634:pass
 elif address==0x14567cc:result=tokens.pop(0)
 else:raise AssertionError(hex(address))
 machine.reg_write(UC_ARM64_REG_X0,result&0xffffffffffffffff);machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
for row in vehicle_rows:
 assert len(row)==9;rid=row[0];obj=0x10010000+rid*0x100;w64(vehicle_table+0x20+rid*8,obj);tokens=list(row)
 run(0x1635b24,x0=obj,x1=stream)
 assert not tokens
 assert [r32(obj+off) for off in (0x18,0x20,0x24,0x28,0x2c,0x30,0x34,0x38,0x1c)]==row
 vehicles.append(dict(id=rid,type=row[1],speed_rate=row[7],flags=row[8]))
checks=0
for has_human,human_flags,vehicle_id in product((False,True),(0,4,32,36,64,68,2147483647,-2147483648),(None,-1,0,1,2,3)):
 board={} if vehicle_id is None else {55:vehicle_id};events=[]
 actual=run(0x148cca4,x0=entity)
 store=CombatEntities(entity,[]);store.allocate();store.add_component(entity,28,{'board':dict(board)})
 if has_human:store.add_component(entity,18,{'flag':human_flags})
 expected=can_move_in_air(store,entity,vehicles)
 assert actual==expected
 assert events==(['vehicle_table'] if has_human and human_flags&4 and vehicle_id not in (None,-1) else [])
 checks+=1
report=dict(nativeAirMovementCases=checks,nativeOriginalVehicleRows=len(vehicles),vehicles=vehicles,
 scope='Original CanMoveInAir joined to IsOnVehicle; original Vehicle.Load checked against all four original rows',
 findings=['Requires Human flag4, AI board55 not -1, and vehicle flag1','Only vehicle ID2/type3 dragon has air flag in original table; this does not classify monster pets by species'],
 limitations=['Entity/blackboard access, Human/BaseData flag checks and table-token stream supplied; no original source-unit capture'])
(EVIDENCE/'air-movement-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');print(json.dumps(report))
