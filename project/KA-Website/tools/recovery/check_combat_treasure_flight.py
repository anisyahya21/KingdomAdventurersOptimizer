"""Original FireTreasure argument/RNG ordering versus the portable dispatch."""
from pathlib import Path
exec(Path(__file__).with_name('check_combat_damage_wrapper.py').read_text(encoding='utf-8').split('from itertools import product')[0])
from itertools import product
from combat_settlement import treasure_flight
from combat_resolution import random_range,f32
uc.mem_write(0x316ae5e,b'\x01')
for got in (0x2f5c538,0x2f5e538):w64(got,0x10001000)
w64(0x10001000,0x10002000);w32(0x100020e0,1)
worlds,world,conquest,source,position,chest=[0x10004000+i*0x100 for i in range(6)]
w64(worlds+0x10,world);w64(conquest+0x40,123)
events=[];raws=[]
def floats(n):return tuple(struct.unpack('<f',struct.pack('<I',reg('S'+str(i))))[0] for i in range(n))
def external(machine,address,size,user):
    if 0x14f0118<=address<0x14f031c:return
    result=0
    if address==0x14c2b14:result=worlds
    elif address==0x147b328:
        assert reg('X0')==world and reg('X1')==123;result=source
    elif address==0x1471498:result=position
    elif address==0x145ff98:
        lower,upper=reg('W0'),reg('W1');events.append(('rng',lower,upper))
        result=random_range(raws.pop(0),lower,upper)
    elif address==0x14607ec:uc.mem_write(reg('X0'),struct.pack('<fff',*floats(3)))
    elif address==0x1479898:
        assert reg('X0')==world;events.append(('create',reg('W1'),floats(3)));result=chest
    elif address==0x1471a10:
        assert reg('X0')==chest
        events.append(('projectile',tuple(reg('W'+str(i)) for i in range(1,5)),floats(6)))
        assert reg('X5')==0;result=chest
    elif address==0x1479108:
        assert reg('X0')==world
        events.append(('smoke',reg('W1'),reg('W2'),reg('W3'),floats(3)))
    else:raise AssertionError(hex(address))
    machine.reg_write(UC_ARM64_REG_X0,result);machine.reg_write(UC_ARM64_REG_PC,reg('LR'))
uc.hook_add(UC_HOOK_CODE,external)
count=0
for pos,values,tid in product(((0.,0.,0.),(-25.5,3.,96.),(100000.,-12.25,-72.)),
                            ((0,0,0),(19,18,17),(20,21,22),(2147483646,4,6)),(710,711,1294)):
    uc.mem_write(position+0x10,struct.pack('<fff',*pos));events=[];raws=list(values)
    returned=run(0x14f0118,x0=0x10006000,x1=conquest,w2=tid)
    supplied=iter(values);expected=treasure_flight(tid,pos,lambda purpose,bound:next(supplied))
    assert returned==expected['duration']
    assert events==[('rng',20,40),('rng',10,30),('rng',10,30),('create',tid,expected['start']),
        ('projectile',(1,expected['height'],expected['duration'],0),expected['start']+expected['end']),
        ('smoke',6,0,100,expected['smokePosition'])],(events,expected)
    assert not raws;count+=1
report=dict(nativeTreasureFlightCases=count,
    scope='Original FireTreasure through return; Math range helper, entity/component allocation and source position supplied',
    findings=['Three ordered Math draws per dispatch: duration[20,40), X/Z offsets[10,30).',
              'Original treasure ID reaches CreateTreasure; source-world position is conquest object position.',
              'Type1 projectile height=2*duration and smoke image6 follow creation.'],
    limits=['World collection/storage and complete Finish teardown remain unimplemented.'])
(EVIDENCE/'treasure-flight-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
print(json.dumps(report))
