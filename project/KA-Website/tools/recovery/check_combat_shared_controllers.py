"""Autonomous two-sided direct/heal laboratory using selected original enemies."""
import json
from copy import deepcopy
from itertools import product
from combat_shared_controllers import SharedControllers
from combat_scenario import ScenarioError
from combat_farmer_slice import RECIPE
from combat_encounters import special_enemy_baseline
from combat_parameters import HUMAN_TRAINING_PARAMETERS
from combat_initial_state import EVIDENCE


def fixture(mp,heal_enabled,injured):
    def human(name,grid,skills,values):
        params={p:dict(rawValue=values.get(p,1),rawMax=values.get(p,1) if p in (10,11) else 2147483647,
                       extraValue=0,extraMax=0,trainingLevel=123) for p in HUMAN_TRAINING_PARAMETERS}
        return dict(name=name,human=True,team=0,grid=grid,cell=[grid%5,4+grid//5],
                    skills=list(skills),levels=[1]*len(skills),parameters=params)
    farmer=human('farmer',0,RECIPE,{10:5000,11:mp,13:300,14:3000,15:220,16:110,19:0})
    healer=human('healer',5,[37] if heal_enabled else [],{10:5000,11:mp,13:1,14:3000,15:600,16:1,19:1})
    baseline=special_enemy_baseline(3,0,lambda n:0)
    boss=next(f for f in baseline['fighters'] if f['leaderIdentity'])
    bot=next(f for f in baseline['fighters'] if f['monsterId']==119)
    enemies=[]
    for source,name,grid in ((boss,'knight',0),(bot,'kairobot',5)):
        enemies.append(dict(name=name,human=False,team=1,grid=grid,cell=[0,3-grid//5],boss=name=='knight',
            skills=source['skills']['dataIds'],levels=source['skills']['invocationLevels'],
            parameters={int(k):v for k,v in deepcopy(source['parameters']).items()}))
    if injured:
        farmer['parameters'][10]['rawValue']//=2
        enemies[0]['parameters'][10]['rawValue']//=2
    return [farmer,healer]+enemies


def front_targets(engine,i):
    # Supplied fixed one-column melee geometry, no movement/advance inference.
    return [j for j in engine.units if not engine.same_team(i,j)
            and engine.units[j]['cell'][0]==engine.units[i]['cell'][0]
            and engine.distance(i,j)==1]


cases=[];total_heals=0;enemy_attacks=0;counter_commands=0
for seed,mp,healing,injured in product((1,7,31),(0,1000),(False,True),(False,True)):
    specs=fixture(mp,healing,injured)
    engine=SharedControllers(specs,seed,seed+1,front_targets)
    result=engine.run(2000)
    names=result['names'];events=result['trace'];enqueues={}
    for e in events:
        if e['kind']=='enqueue':
            enqueues[e['commandId']]=e
            counter_commands+=e['source']=='counter'
        elif e['kind']=='release':
            original=enqueues[e['commandId']]
            assert (e['caster'],e['target'],e['skill'])==(original['caster'],original['target'],original['skill'])
        elif e['kind']=='mp':
            assert e['before']>=e['amount'] and e['after']==e['before']-e['amount']
        elif e['kind']=='heal':
            assert e['caster']!=e['target'] and engine.same_team(e['caster'],e['target'])
            assert 0<e['before']<engine.maximum(e['target'],10)
            assert e['after']==min(engine.maximum(e['target'],10),e['before']+e['amount'])
            total_heals+=1
        elif e['kind']=='attack':
            assert not engine.same_team(e['attacker'],e['target'])
            enemy_attacks+=engine.specs[e['attacker']]['team']==1
        elif e['kind']=='prize':
            cause=events[e['causeAttack']]
            assert cause['kind']=='attack' and cause['hit'] and cause['target']==e['target'] and e['hp']==0
    for i,u in result['units'].items():
        initial=next(s for s in specs if s['name']==engine.specs[i]['name'])['parameters'][11]['rawValue']
        paid=sum(e['amount'] for e in events if e['kind']=='mp' and e['caster']==i)
        assert initial-paid==u['mp'] and u['mp']>=0
    cases.append(dict(seed=seed,mp=mp,healing=healing,initialInjury=injured,
        heals=sum(e['kind']=='heal' for e in events),enemyAttacks=sum(e['kind']=='attack' and engine.specs[e['attacker']]['team']==1 for e in events),
        counters=sum(e['kind']=='enqueue' and e['source']=='counter' for e in events),
        final=result['units'],prizeCallbacks=result['prizeCallbacks']))
assert total_heals>0 and enemy_attacks>0 and counter_commands>0
a=SharedControllers(fixture(1000,True,True),7,8,front_targets).run(2000)
b=SharedControllers(fixture(1000,True,True),7,8,front_targets).run(2000)
assert a==b
# A lethal generated enemy attack drives human knockdown and clears its queue.
fragile=fixture(0,False,False)
fragile[0]['parameters'][10].update(rawValue=1,rawMax=1)
fragile[0]['parameters'][14]['rawValue']=0
dead=SharedControllers(fragile,1,2,front_targets).run(2000)
farmer=dead['names']['farmer']
assert any(e['kind']=='state' and e['target']==farmer and e['new']==7 for e in dead['trace'])
assert dead['units'][farmer]['hp']==0 and dead['units'][farmer]['state']==8
assert dead['units'][farmer]['commands']==0
# Reject unsupported skills rather than substituting damage. Skill 0 (Normal Attack, type0) is a
# supported attack route; a category-2 noncombat row (44 Thief, type3) is the rejection case.
# The rejection surfaces as ScenarioError (transport 422) with NotImplementedError still accepted
# as the older deep-guard form.
unsupported=fixture(1000,True,False);unsupported[2]['skills']=[44]
try:SharedControllers(unsupported,1,2,front_targets)
except (NotImplementedError,ScenarioError):pass
else:raise AssertionError('Unsupported skill silently accepted')
# Recovery selection order: the nearest same-team ally with HP rate<100 is taken before the
# type-specific CanUseSkill healing rule. A nearer dead ally therefore consumes the selection
# and the farther wounded ally is left unhealed in that decision (native 0x15de880/0x15e2418;
# no fallback). A dead nearer Scholar must mask a wounded farther Ninja.
masking=fixture(1000,True,False)
masking[0]['parameters'][10]['rawValue']=0
ninja_spec=dict(masking[0],name='ninja',cell=[1,4],parameters=deepcopy(masking[0]['parameters']))
ninja_spec['parameters'][10]['rawValue']=2500
masking.append(ninja_spec)
engine=SharedControllers(masking,7,8,front_targets)
healer,scholar,ninja=(engine.names[name] for name in ('healer','farmer','ninja'))
assert engine.distance(healer,scholar)==1 and engine.distance(healer,ninja)==2
assert engine.rate(scholar,10)==0 and engine.rate(ninja,10)==50
assert [c for c in engine.candidates(healer) if c[0]['category']==1]==[]
engine.units[scholar]['parameters'][10]['rawValue']=engine.maximum(scholar,10)
chosen=[c for c in engine.candidates(healer) if c[0]['category']==1]
assert [target for s,level,target in chosen]==[ninja]
report=dict(cases=len(cases),ticks=2000*len(cases),generatedLethalCase=True,heals=total_heals,enemyAttacks=enemy_attacks,
    counterCommands=counter_commands,recoveryMaskingOrder=True,results=cases,example=a,limits=a['limits']+[
        'Only Knight boss135 and one Kairobot119 from original Extreme constructor3 at defeatCount0; all other followers omitted.',
        'Synthetic human stats; optional initial injury is a test input. No setup success or optimized yield claimed.',
        'Checks assert integration invariants; not a joined original-native world replay.'])
(EVIDENCE/'shared-controller-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
print(json.dumps({k:v for k,v in report.items() if k not in ('results','example','limits')}))
