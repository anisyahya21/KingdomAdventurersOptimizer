"""Original special-prize routine: repeated calls append repeated chest awards."""
from pathlib import Path
exec(Path(__file__).with_name('check_combat_damage_wrapper.py').read_text(encoding='utf-8').split('from itertools import product')[0])
from itertools import product

uc.mem_write(0x316ae49,b'\x01')
for got in (0x2f5b890,0x2f5fbd8,0x2f5fbe8,0x2f5fc08,0x2f5fc30,0x2f61dd8,0x2f61de8,0x2f61df0):w64(got,0x10001000)
w64(0x10001000,0x10002000);w32(0x100020e0,1)
system,conquest,table,boss,treasure,prizes,items= [0x10010000+i*0x1000 for i in range(7)]
w64(system+0x48,conquest);w64(system+0x58,prizes);w64(prizes+0x10,items);w32(items+0x18,128)
w32(table+0x18,1);w64(table+0x20,boss);w32(boss+0x40,1203);w32(treasure+0x18,710)
draws=[];constructors=[]
def external(machine,address,size,user):
    if 0x14ed618<=address<0x14ed83c:return
    result=0
    if address==0x12d23b8:result=0x10020000
    elif address in (0x14f35c8,0x1cb5fc8):pass
    elif address==0x1626d14:result=table
    elif address in (0x16268c8,0x18abfac,0x18a70c4):result=0x10021000
    elif address==0x18c6404:draws.append('select');result=treasure if has_treasure else 0
    elif address==0x14f2f5c:
        args=tuple(reg('W'+str(i)) for i in range(1,5));constructors.append(args)
        machine.mem_write(reg('X0'),struct.pack('<4I',*args))
    else:raise AssertionError(hex(address))
    machine.reg_write(UC_ARM64_REG_X0,result);machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
cases=0;successful_appends=0
for guerrilla,valid_id,has_treasure,repetitions in product((False,True),(False,True),(False,True),(1,2,8,64)):
    uc.mem_write(system+0x65,bytes([guerrilla]));w32(conquest+0x50,0 if valid_id else -1)
    w32(prizes+0x18,0);w32(prizes+0x1c,0);draws=[];constructors=[]
    for _ in range(repetitions):run(0x14ed618,x0=system)
    count=repetitions if guerrilla and valid_id and has_treasure else 0
    assert r32(prizes+0x18)==count and r32(prizes+0x1c)==count
    assert len(draws)==(repetitions if guerrilla and valid_id else 0)
    assert constructors==[(0,710,7,0)]*count
    for i in range(count):assert struct.unpack('<4I',uc.mem_read(items+0x20+i*16,16))==(0,710,7,0)
    successful_appends+=count;cases+=1
report=dict(nativeRepeatPrizeCases=cases,successfulAppends=successful_appends,
    finding='Repeated eligible AddGuerrillaPrize calls append another prize each time; no once-per-boss guard in this routine.',
    limits=['Original gate/list-append instructions executed; data filtering, RandomOrDefault and Prize constructor supplied.',
            'Does not establish complete reward display/settlement, nor that a real stored-attack setup repeatedly reaches this routine.'])
(EVIDENCE/'repeat-prize-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
print(json.dumps(report))
