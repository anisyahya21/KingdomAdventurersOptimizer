"""End-to-end scenario, gear, pet order, refill and zero-stock input checks."""
import json
from copy import deepcopy
from combat_sandbox import run_scenario
from combat_scenario import load_scenario,ScenarioError
from combat_initial_state import EVIDENCE,monster_parameter,PARAM_IDS
from combat_parameters import HUMAN_TRAINING_PARAMETERS
from combat_shared_controllers import EQUIPMENT,SharedControllers
from combat_prizes import special_prize_candidates
from recover_special_combat import table,array


def human(name,skills,stats):
    return dict(name=name,human=True,monsterId=None,weaponId=0,equipment=[],visitor=False,leaderIdentity=False,
        skills=skills,invocationLevels=[1]*len(skills),
        parameters={p:dict(rawValue=stats.get(p,1),rawMax=stats.get(p,1) if p in (10,11) else 2147483647,
                           extraValue=0,extraMax=0,trainingLevel=123) for p in HUMAN_TRAINING_PARAMETERS})


def example():
    farmer=human('synthetic farmer',[26,110,25,24,23,22],{10:5000,11:1000,13:300,14:3000,15:220,16:110,19:0})
    farmer['isHouseOwner']=True
    own=[farmer]+[human(f'synthetic fodder{i}',[],{10:1,11:1,13:1,14:1,15:1,16:1,19:1}) for i in range(4)]
    own.append(human('synthetic healer',[37,107],{10:5000,11:1000,13:1,14:3000,15:600,16:1,19:1}))
    curves,_=array(table('Monster')[0],7,2)
    values={p:monster_parameter(curve,80) for p,curve in zip(PARAM_IDS,curves)}
    pet=dict(name='synthetic household pet',human=False,monsterId=0,weaponId=0,equipment=[],visitor=False,leaderIdentity=False,
        skills=[],invocationLevels=[],parameters={p:dict(rawValue=v,rawMax=v if p in (10,11) else 2147483647,
            extraValue=0,extraMax=0,trainingLevel=1) for p,v in values.items()})
    return dict(schema='ka-special-combat-research-1',encounterId=19,defeatCount=0,mathSeed=7,libSeed=8,
        tickLimit=200,ownUnits=own,housePets={farmer['name']:[pet]},holyHerbStock=0,
        inputs=[dict(tick=10,type='holy_herb',phase='before_fighters')],
        note='Synthetic test values, not the player build, an optimal setup or recommended gear.')


if __name__=='__main__':
    data=example()
    normalized=load_scenario(data)
    assert normalized==load_scenario(normalized)
    assert [u['name'] for u in normalized['ownUnits']]==[u['name'] for u in data['ownUnits']]+['synthetic household pet']
    base=run_scenario(data,True)
    assert base['holyHerbRemaining']==0 and not base['holyHerbUses'][0]['used']
    assert len(base['setup']['ownUnits'])==7 and len(base['result']['units'])==28
    assert base==run_scenario(data,True)
    sourced=deepcopy(data)
    sourced['prePlacement']={name:dict(cell=[100,100],position=[2400.,0.,2400.],offset=[0.,0.,0.],
        board={4:0,5:0,6:0,7:0,8:0},longBoard={}) for name in base['result']['names']}
    initialized=run_scenario(sourced,True)
    assert initialized['result']['initializationMode']=='supplied pre-placement state, sequential teams'
    assert sum(e['kind']=='initial_placement' for e in initialized['trace'])==28
    del sourced['prePlacement'][next(iter(sourced['prePlacement']))]
    try:run_scenario(sourced)
    except ScenarioError:pass
    else:raise AssertionError('Incomplete pre-placement mapping accepted')
    # Raw HP changes are refilled; gear HP contribution changes the effective maximum.
    armor=next(r for r in EQUIPMENT.values() if r['category']!=0 and r['parameters'][0][0]>0)
    geared=deepcopy(data);geared['ownUnits'][0]['equipment']=[dict(id=armor['id'],level=2,affinity=1)]
    geared['ownUnits'][0]['parameters'][10]['rawValue']=1
    g=run_scenario(geared)
    hp=g['setup']['ownUnits'][0]['effectiveParameters'][10]
    assert hp['value']==hp['maximum'] and hp['maximum']>5000
    # Ranged normal attacks must launch projectiles, even without attack skills.
    ranged=deepcopy(data);ranged['ownUnits'][0]['skills']=[];ranged['ownUnits'][0]['invocationLevels']=[]
    bow=next(r for r in EQUIPMENT.values() if r['type']==8 and r['category']==0 and r['projectileFlag'])
    ranged['ownUnits'][0].update(weaponId=bow['id'],equipment=[dict(id=bow['id'],level=1,affinity=1)])
    ranged['tickLimit']=500
    r=run_scenario(ranged,True);farmer=r['result']['names']['synthetic farmer']
    assert any(e['kind']=='projectile_launch' and e['owner']==farmer and e['commandId'] is None for e in r['trace'])
    # Owner eligibility is explicit, not guessed from a pet being nearby.
    invalid=deepcopy(data);invalid['ownUnits'][0]['isHouseOwner']=False
    try:load_scenario(invalid)
    except ScenarioError:pass
    else:raise AssertionError('Non-owner pets accepted')
    # Repeated departure awards must consume the same Math stream as attacks.
    specs=[]
    for team in (0,1):
        unit=human(f'prize probe {team}',[],{10:100,11:100,14:10,15:10})
        specs.append(dict(unit,team=team,grid=team,cell=[team,team],levels=[],human=team==0,boss=team==1))
    engine=SharedControllers(specs,7,8,prize_candidates=special_prize_candidates(19))
    boss=engine.teams[1][0];engine.param(boss['id'],10)['rawValue']=0
    before=engine.math_draws
    before_lib=engine.lib_draws
    for _ in range(64):engine.leave(boss)
    prizes=[e for e in engine.trace if e['kind']=='prize']
    assert len(prizes)==64 and engine.math_draws-before==64
    assert engine.lib_draws-before_lib==64
    assert sum(e['kind']=='effect_birth' for e in engine.trace)==64
    for prize in prizes:
        assert engine.trace[prize['id']+1]['purpose']=='leaving_sound'
    assert all(e['treasureId'] in special_prize_candidates(19) for e in prizes)
    path=EVIDENCE/'sandbox-synthetic-scenario.json'
    path.write_text(json.dumps(data,indent=2)+'\n',encoding='utf-8')
    (EVIDENCE/'sandbox-synthetic-report.json').write_text(json.dumps(base,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(scenario=str(path),ownFighters=7,enemies=21,zeroStock=True,gearRefill=True,normalBowProjectile=True,
        repeatedPrizeDraws=64,limits='Synthetic inputs; no full-game equivalence or optimization claim.')))
