"""Joined gap filling, front targeting and complete-roster movement smoke runs."""
from pathlib import Path
exec(Path(__file__).with_name('check_combat_shared_controllers.py').read_text(encoding='utf-8').split('cases=[]')[0])
from combat_navigation import empty_cell,front_opponent
from combat_spatial import cell_key

cells=[(x,y) for x in range(5) for y in range(-10,31)]
assert not empty_cell(None,lambda i:False,lambda i:0)
assert empty_cell([],lambda i:False,lambda i:0)
# Departed foreground ally no longer blocks advancement of the healer behind.
engine=SharedControllers(fixture(1000,False,False),1,2,movement_cells=cells)
farmer=engine.names['farmer'];healer=engine.names['healer']
engine.param(farmer,10)['rawValue']=0;engine.units[farmer]['board'][5]=8
result=engine.run(80)
assert any(e['kind']=='state' and e['target']==healer and e['new']==2 for e in result['trace'])
assert engine.units[healer]['board'][7]==0 and list(engine.units[healer]['cell'])==[0,4]
# A front opponent in a different column remains a valid normal target.
specs=fixture(1000,False,False)
specs[2]['cell']=[4,3];specs[2]['grid']=4
cross=SharedControllers(specs,1,2)
assert cross.normal_target(cross.units[cross.names['farmer']])==cross.names['knight']

cases=[]
for encounter in range(20):
    original=special_enemy_baseline(encounter,0,lambda n:0)
    offset=max(3,len(original['fighters'])//5+1)
    own=fixture(1000,True,False)[:2]
    farmer,healer=own
    farmer['cell'][1]=offset+1;healer['cell'][1]=offset+2
    roster=[farmer]
    for column in range(1,5):
        fodder=deepcopy(farmer);fodder.update(name=f'fodder{column}',grid=column,cell=[column,offset+1],skills=[],levels=[])
        fodder['parameters'][10].update(rawValue=1,rawMax=1)
        fodder['parameters'][14]['rawValue']=0
        roster.append(fodder)
    roster.append(healer)
    for source in original['fighters']:
        roster.append(dict(name=f"enemy{source['incomingIndex']}",human=False,team=1,grid=source['grid'],cell=source['cell'],
            boss=source['leaderIdentity'],monsterType=0,skills=source['skills']['dataIds'],levels=source['skills']['invocationLevels'],
            parameters={int(k):v for k,v in deepcopy(source['parameters']).items()}))
    lab=SharedControllers(roster,7,8,row_offset=offset,movement_cells=cells)
    for _ in range(original['followerSelectionDraws']):lab.next_lib()
    run=lab.run(1000)
    for i,u in lab.units.items():
        matches=[key for key,bucket in lab.occupancy.buckets.items() if i in bucket]
        assert matches==[cell_key(*u['cell'],lab.map_width)]
        assert lab.occupancy.buckets[matches[0]].count(i)==1
    cases.append(dict(encounterId=encounter,enemies=len(original['fighters']),
        moves=sum(e['kind']=='state' and e['new']==2 for e in run['trace']),
        cellChanges=sum(e['kind']=='cell_change' for e in run['trace']),verdict=run['verdict']))
assert sum(c['moves'] for c in cases)>0
report=dict(completeRosterCases=len(cases),ticks=len(cases)*1000,
    movementEntries=sum(c['moves'] for c in cases),gapFillProbe=True,crossColumnTargetProbe=True,cases=cases,
    limits=['Explicit test cell-collection domain x0..4/y-10..30; native battle-map initialization not inferred from this domain.',
            'Synthetic farmer/healer/four one-HP fodders; original enemy rosters at defeatCount0.',
            'Departure/knockdown flight included; effect/RNG/garbage lifecycle and receipt/teardown incomplete; not validated full-fight predictions.'])
(EVIDENCE/'navigation-shared-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
print(json.dumps({k:v for k,v in report.items() if k not in ('cases','limits')}))
