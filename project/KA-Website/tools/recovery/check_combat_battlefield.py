"""Native battle-map arguments and CellCulling missing-bucket behavior."""
from pathlib import Path
exec(Path(__file__).with_name('check_combat_damage_wrapper.py').read_text(encoding='utf-8').split('from itertools import product')[0])
from itertools import product
from combat_spatial import cell_key
for flag in (0x316aec9,0x316aeca,0x316aec8):uc.mem_write(flag,b'\x01')
for got in (0x2f60588,0x2f623d0,0x2f623d8):w64(got,0x10001000)
w64(0x10001000,0x10002000);w32(0x100020e0,1);w64(0x100020b8,0x10003000)
system,world,map_data,fallback,bucket=0x10004000,0x10005000,0x10006000,0x10007000,0x10008000
w64(system+0x10,world);w64(system+0x28,0x10009000);w64(0x10003000,fallback);w32(map_data+0x10,8)
present=False;map_args=[]
def external(machine,address,size,user):
    if 0x1500e54<=address<0x1500f10 or 0x1500a04<=address<0x1500a8c or 0x16aa350<=address<0x16aa388:return
    if address==0x1b1175c:result=int(present)
    elif address==0x1b114c8:result=bucket
    elif address==0x1475b3c:result=map_data
    elif address==0x1475c5c:
        map_args.append(tuple(reg('W'+str(i)) for i in range(1,8))+(r32(reg('SP')),))
        machine.reg_write(UC_ARM64_REG_PC,0x30000000);return
    else:raise AssertionError(hex(address))
    machine.reg_write(UC_ARM64_REG_X0,result);machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
for present in (False,True):
    run(0x1500e54,x0=system,w1=123)
    assert reg('X0')==(bucket if present else fallback)
cases=0
for x,y in product((-100,-1,0,4,7,8,100),(-100,-1,0,15,16,100)):
    run(0x1500a04,x0=system,w1=x,w2=y)
    assert i32(reg('W0'))==cell_key(x,y,8);cases+=1
run(0x16aa350,x0=world,x19=0x1000a000)
assert map_args==[(8,16,48,24,24,24,16,16)]
report=dict(nativeCellKeyCases=cases,nativeBucketCases=2,nativeMapArgumentBlock=True,
    findings=['BattleForm.Init supplies map width8/height16, size48x24, steps24x24, area16x16.',
              'GetCellEntities returns the static fallback when no dictionary key exists; its cctor builds an empty read-only list.',
              'Cell keys use x+2*width*y without clipping coordinates to map bounds.'],
    limits=['Map argument block executed, not the complete form initializer.',
            'Dictionary/component getters supplied; static fallback identity supplied; read-only empty-list construction statically inspected.'])
(EVIDENCE/'battlefield-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
print(json.dumps(report))
