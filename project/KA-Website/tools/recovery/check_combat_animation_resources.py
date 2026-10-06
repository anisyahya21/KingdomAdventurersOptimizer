"""Original SEB loader header for every recovered chara/monster binding."""
from pathlib import Path
exec(Path(__file__).with_name('check_combat_effect_resources.py').read_text().split('bindings =')[0])
report=json.loads((OUT/'animation-resources.json').read_text())
checks=0
for res,rows in report['resources'].items():
    directory=OUT/(res+'-animation-original')
    for row in rows:
        data=(directory/row['file']).read_bytes();cursor=0
        assert hashlib.sha256(data).hexdigest()==row['sha256']
        uc.reg_write(UC_ARM64_REG_SP,0x20008000)
        uc.reg_write(UC_ARM64_REG_X0,0x10004000);uc.reg_write(UC_ARM64_REG_X1,0x10005000)
        uc.emu_start(start,end,count=10000)
        assert uc.reg_read(UC_ARM64_REG_PC)==0x234faf0
        assert cursor==(4 if row['format']==0 else 5)
        assert struct.unpack('<i',uc.mem_read(0x1000401c,4))[0]==row['maxFrame']
        checks+=1
result=dict(nativeAnimationHeaderChecks=checks,scope='Original loader through maxFrame assignment; stream and allocation supplied')
(OUT/'animation-resource-checks.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result))
