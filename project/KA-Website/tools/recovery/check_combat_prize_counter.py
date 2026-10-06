"""Native reward-bar overflow count, for interpreting farming screenshots."""
from pathlib import Path
exec(Path(__file__).with_name('check_combat_damage_wrapper.py').read_text(encoding='utf-8').split('from itertools import product')[0])
for got in (0x2f5b570,0x2f5b8a0,0x2f61f70):w64(got,0x10001000)
w64(0x10001000,0x10002000);w32(0x100020e0,1);w64(0x100020b8,0x10003000)
prizes=0x10004000;labels=[]
def external(machine,address,size,user):
    if 0x14f1d88<=address<0x14f1e78:return
    result=0
    if address==0x22bee98:labels.append(i32(reg('W1')));result=0x10005000
    elif address not in (0x231173c,0x22ffdf8,0x2301a70,0x2311790):raise AssertionError(hex(address))
    machine.reg_write(UC_ARM64_REG_X0,result);machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
for count in range(101):
    labels=[];w32(prizes+0x18,count);w64(0x20008038,0x30000000)
    run(0x14f1d88,x8=prizes,x19=0x10006000)
    assert labels==([count-10] if count>=11 else []),(count,labels)
report=dict(nativePrizeOverflowCases=101,
    finding='Overflow label receives prize-list count minus10, and is absent for counts0..10. Screenshot +60 with10 visible boxes corresponds to70 queued prize entries.',
    limits=['Original DrawPrizeBar numeric tail executed; font/color/localization/drawing supplied. Ten-icon cap separately traced in method prefix.',
            'Prize entry count is not proof of final chest settlement or inventory delivery.'])
(EVIDENCE/'prize-counter-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
print(json.dumps(report))
