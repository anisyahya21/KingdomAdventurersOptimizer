"""Native isometric trail projection, with camera=false as used by ProjectileSystem."""
from pathlib import Path
exec(Path(__file__).with_name('check_combat_attack_effects.py').read_text().split('from itertools import product')[0])
from combat_projectiles import isometric_trail_screen
from itertools import product
renderer,klass,statics,app=0x10004000,0x10005000,0x10006000,0x10007000
uc.mem_write(0x316b43b,b'\x01')
w64(0x2f5f980,0x10008000);w64(0x10008000,klass);w32(klass+0xe0,1)
w64(klass+0xb8,statics);w64(statics,app);w32(renderer+0x38,0)
def external(machine,address,size,user):
    if 0x15cdeec<=address<0x15ce0d4 or 0x1671ce8<=address<0x1671d00:return
    if address==0x145fdf4:
        w32(reg('X0'),reg('W1'));w32(reg('X0')+4,reg('W2'))
    else:raise AssertionError(hex(address))
    machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
checks=0
for xyz in product((-2400.5,-24.75,-.5,0.,24.25,2400.5),repeat=3):
    regs={f's{i}':struct.unpack('<I',struct.pack('<f',v))[0] for i,v in enumerate(xyz)}
    run(0x15cdeec,x0=renderer,w1=0,**regs)
    packed=reg('X0');actual=(i32(packed&0xffffffff),i32(packed>>32))
    assert actual==isometric_trail_screen(xyz),(xyz,actual)
    checks+=1
report=dict(nativeProjectionCases=checks,scope='Original RenderSystem mode0 and both isometric helpers; camera disabled; Pos constructor supplied')
(EVIDENCE/'trail-projection-checks.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report))
