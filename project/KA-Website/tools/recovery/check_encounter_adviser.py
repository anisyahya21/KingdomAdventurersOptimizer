"""Candidate-level leakage, bounded fitting and optimistic-allocation checks."""
import copy
import strategy_encounter_adviser as adviser

def main():
    rows=[{'candidateId':str(i),'compatibility':'encounter:1:policy:A','window':'development',
           'features':{'damage':i%8,'interval':i//8,'contact':(i*7)%13},
           'meanEarned':float(20-abs(i%8-4)*2+i//8),'resolved':16,'total':16,'standardError':2}
          for i in range(40)]
    model=adviser.fit(rows,compatibility='encounter:1:policy:A')
    assert model['status']=='fitted' and model['candidateCount']==40
    assert model['heldOut']['testCandidates']==8 and not model['heldOut']['confirmationDataUsed']
    # Re-reading unchanged evidence and adding incompatible/confirmation rows must not refit.
    excluded=dict(rows[0],candidateId='holdout',window='confirmation',meanEarned=1e12)
    foreign=dict(rows[0],candidateId='foreign',compatibility='other',meanEarned=1e12)
    assert adviser.fit(rows*3+[excluded,foreign],compatibility='encounter:1:policy:A',previous=model) is model
    assert adviser.fit(rows[:5],compatibility='encounter:1:policy:A')['status']=='coverage'
    unknown=dict(rows[0],candidateId='censored',resolved=15,total=16,meanEarned=1e12)
    assert adviser.fit(rows+[unknown],compatibility='encounter:1:policy:A',previous=model) is model
    leaked=copy.deepcopy(rows); leaked[0]['features']['futureEarned']=999
    try: adviser.fit(leaked,compatibility='encounter:1:policy:A')
    except ValueError: pass
    else: raise AssertionError('future outcome feature admitted')
    predictions=adviser.rank(model,[{'id':str(i),'features':r['features'],'novelty':float(i==0)} for i,r in enumerate(rows)],coverage_slots=1)
    assert predictions[0]['id']=='0' and len(predictions)==len(rows)
    assert all(not p['advice']['observed'] for p in predictions)
    print('PASS adviser: compatible candidate-level fitting, held-out check, no confirmation leakage, no duplicate evidence, reserved coverage')
if __name__=='__main__': main()
