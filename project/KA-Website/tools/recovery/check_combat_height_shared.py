"""Joined empty-map Height timing and explicit Human source-flag boundaries."""
from pathlib import Path
exec(Path(__file__).with_name('check_combat_shared_controllers.py').read_text().split('cases=[]')[0])
checks=0
for flags in (0,32):
    specs=fixture(1000,False,False);specs[0]['humanFlags']=flags
    lab=SharedControllers(specs,1,2);lab.update=lambda *args:None
    fighter=lab.names['farmer'];c=lab.world.objects[fighter]['components']
    c[0][1]=11.;c[0][4]=999.;c[1][1]=2.
    lab.run(1)
    assert c[0][1]==(13. if flags&32 else 0.)
    assert c[0][4]==999. # Grounding changes actual Y, not sprite offset Y.
    assert lab.battle_frame==1
    for excluded in (11,39,38):
        lab.world.add_component(fighter,excluded,{})
        assert fighter not in lab.height_members.members
        lab.world.remove_component(fighter,excluded)
        assert fighter in lab.height_members.members
    checks+=1
for field,value in (('humanFlags',4),('onVehicle',True)):
    specs=fixture(1000,False,False);specs[0][field]=value
    try:SharedControllers(specs,1,2)
    except NotImplementedError:pass
    else:raise AssertionError('Unsupported vehicle silently accepted')
    checks+=1
report=dict(joinedHeightCases=checks,scope='Controlled Move-to-Height timing, flag32, subset exclusions and rejected vehicle inputs; native Height and empty map checked separately')
(EVIDENCE/'height-shared-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');print(json.dumps(report))
