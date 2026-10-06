"""Native special map construction and tile lookup with allocation supplied."""
from pathlib import Path
exec(Path(__file__).with_name('check_combat_damage_wrapper.py').read_text(encoding='utf-8').split('from itertools import product')[0])
for flag in (0x316aafb,0x316b337):uc.mem_write(flag,b'\x01')
for got in (0x2f5b960,0x2f5f3f0,0x2f5f3f8,0x2f5fb68):w64(got,0x10001000)
w64(0x10001000,0x10002000);w32(0x100020e0,1)
world,manager,entity,map_data,rows,flags= [0x10004000+i*0x1000 for i in range(6)]
w64(world+0x30,manager)
calls=[]
def external(machine,address,size,user):
    if 0x1475c5c<=address<0x1475da8 or 0x15afd9c<=address<0x15afe98:return
    if address==0x18cf5cc:
        assert (reg('W0'),reg('W1'),reg('X2'))==(16,8,0)
        w32(rows+0x18,16)
        for y in range(16):
            row=0x10010000+y*0x100;w64(rows+0x20+y*8,row);w32(row+0x18,8)
            uc.mem_write(row+0x20,bytes(64))
        result=rows;calls.append('null tile array')
    elif address==0x18cef8c:
        assert (reg('W0'),reg('W1'))==(128,0);result=flags
    elif address==0x1474148:
        assert (reg('X0'),reg('W1'),reg('W2'))==(manager,0,0);result=entity
    elif address==0x1470264:
        assert reg('X0')==entity
        assert tuple(reg('W'+str(i)) for i in range(1,8))==(8,16,48,24,24,24,16)
        sp=reg('SP');assert r32(sp)==16
        import struct
        assert struct.unpack('<QQ',uc.mem_read(sp+8,16))==(rows,0)
        w32(map_data+0x10,8);w32(map_data+0x14,16);w64(map_data+0x30,rows)
        result=entity;calls.append('map component')
    else:raise AssertionError(hex(address))
    machine.reg_write(UC_ARM64_REG_X0,result);machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
uc.mem_write(0x20008000,bytes(32));w32(0x20008000,16)
run(0x1475c5c,x0=world,w1=8,w2=16,w3=48,w4=24,w5=24,w6=24,w7=16)
assert calls==['null tile array','map component']
count=0
for x in range(-1,9):
    for y in range(-1,17):
        run(0x15afd9c,x0=map_data,w1=x,w2=y,w3=0)
        assert reg('X0')==0;count+=1
report=dict(nativeEmptyMapLookups=count,nativeCreateMap=True,
    scope='Original CreateMap and GetMapChip(false); allocation, CreateDoubleArray and AddMap supplied with inspected native null initialization/store semantics',
    limits=['Does not execute every subsequent world system or establish vehicle/Human height flags.'])
(EVIDENCE/'empty-map-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
print(json.dumps(report))
