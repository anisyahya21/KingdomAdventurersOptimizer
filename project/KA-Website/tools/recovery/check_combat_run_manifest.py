"""Support rejection, pre-refill provenance and draw-stream replay checks."""
from copy import deepcopy
from check_combat_sandbox import example
from combat_sandbox import run_scenario
from combat_scenario import ScenarioError,load_scenario
from combat_run_manifest import canonical_hash,support_contract
from combat_resolution import SystemRandomState

data=example();data['tickLimit']=250
report=run_scenario(data,True)
assert report['manifest']['normalizedScenarioSha256']==canonical_hash(load_scenario(data))
assert report['result']['censored'] and report['result']['retainedRewards'] is None
assert not report['manifest']['support']['exactReplaySupported']
for mode in ('exact','recommendation','bogus'):
    try:support_contract(dict(load_scenario(data),mode=mode))
    except ScenarioError:pass
    else:raise AssertionError(mode)
changed=deepcopy(data);changed['ownUnits'][0]['parameters'][10]['rawValue']=1
other=run_scenario(changed,True)
assert report['manifest']['normalizedScenarioSha256']!=other['manifest']['normalizedScenarioSha256']
assert report['result']==other['result'] # Same refill outcome, distinct source provenance.
assert report['manifest']['contentSha256']==other['manifest']['contentSha256']
for stream,seed in (('math',data['mathSeed']),('lib',data['libSeed'])):
    rng=SystemRandomState(seed)
    draws=[e for e in report['trace'] if e['kind']=='rng' and e['stream']==stream]
    assert len(draws)==report['result'][stream+'Draws']
    for index,event in enumerate(draws,1):
        assert event['draw']==index and event['raw']==rng.next_int()
        assert event['purpose']!='unclassified'
    assert report['result']['rngFinalState'][stream]==dict(index=rng.index,partner=rng.partner,values=rng.values)
assert any(e['kind']=='effect_birth' and e['type']==15 for e in report['trace'])
invalid=deepcopy(data)
invalid['ownUnits'][0]['equipment']=[dict(id=0,level=1,affinity=float('nan'))]
try:load_scenario(invalid)
except ScenarioError:pass
else:raise AssertionError('Nonfinite affinity accepted')
invalid=deepcopy(data)
invalid['prePlacement']={'probe':dict(cell=[0,0],position=[0.,0.,0.],offset=[0.,0.,0.],
    board={4:0,5:1,6:0,7:0,8:0,62:1},longBoard={})}
try:load_scenario(invalid)
except ScenarioError:pass
else:raise AssertionError('Unintegrated starting status accepted')
print('Support rejection, provenance, refill equivalence, both RNG streams and balloon integration passed')
