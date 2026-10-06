"""Bounded candidate-level bagged regression for experiment priority, never reward evidence.

No numerical dependency is required. Fit is an explicit preparation operation. Predict only
traverses bounded trees and cannot simulate, access a library, or update a recommendation.
"""
from __future__ import annotations
import hashlib
import json
import math
import random
import statistics

VERSION = 'encounter-adviser-1'
MAX_CANDIDATES = 256
MIN_CANDIDATES = 12
TREE_COUNT = 9
MAX_DEPTH = 4
MIN_LEAF = 3
FORBIDDEN_FEATURES = ('reward', 'earned', 'certificate', 'queue', 'reentry', 're-entry', 'verdict', 'telemetry', 'holdout')

def _digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def _number(v):
    return isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v)

def _tree(rows, rng, depth=0):
    mean=statistics.fmean(row[1] for row in rows)
    node={'mean':mean,'candidates':len(rows)}
    if depth>=MAX_DEPTH or len(rows)<2*MIN_LEAF: return node
    width=len(rows[0][0]); axes=list(range(width)); rng.shuffle(axes)
    best=None
    for axis in axes[:max(1,math.ceil(math.sqrt(width)))]:
        values=sorted(set(row[0][axis] for row in rows))
        if len(values)<2: continue
        positions=sorted(set(min(len(values)-2,int((len(values)-1)*q/8)) for q in range(1,8)))
        for pos in positions:
            threshold=(values[pos]+values[pos+1])/2
            left=[r for r in rows if r[0][axis]<=threshold]; right=[r for r in rows if r[0][axis]>threshold]
            if min(len(left),len(right))<MIN_LEAF: continue
            loss=0.0
            for group in (left,right):
                center=statistics.fmean(r[1] for r in group)
                loss+=sum((r[1]-center)**2 for r in group)
            if best is None or loss<best[0]: best=(loss,axis,threshold,left,right)
    if best is None: return node
    _,axis,threshold,left,right=best
    node.update(axis=axis,threshold=threshold,left=_tree(left,rng,depth+1),right=_tree(right,rng,depth+1))
    return node

def _predict(tree, features):
    while 'axis' in tree:
        tree=tree['left'] if features[tree['axis']]<=tree['threshold'] else tree['right']
    return tree['mean']

def _ensemble(rows, rng):
    # Candidates are the sampling unit. Sampling 10,000 battles on one candidate does not
    # make it 10,000 training rows; uncertainty affects a bounded bootstrap weight only.
    weights=[r[2] for r in rows]
    return [_tree(rng.choices(rows,weights=weights,k=len(rows)),rng) for _ in range(TREE_COUNT)]

def fit(observations, *, compatibility, previous=None):
    """Fit JSON-ready trees on pre-run numeric features and compatible DEVELOPMENT summaries.

    Each input: candidateId, compatibility (exact scope/revision/policy key), features,
    meanEarned, resolved, total, standardError (optional), window='development'.
    A fresh candidate-level held-out split is reported; uncertainty is explicitly heuristic.
    """
    selected={}
    for obs in observations:
        if obs.get('window')!='development' or obs.get('compatibility')!=compatibility: continue
        cid=obs.get('candidateId'); features=obs.get('features')
        if not cid or not isinstance(features,dict) or not features: continue
        if any(any(word in key.lower() for word in FORBIDDEN_FEATURES) for key in features):
            raise ValueError('Prediction features must be pre-run mechanical quantities, without outcome telemetry')
        if not all(_number(v) for v in features.values()) or not _number(obs.get('meanEarned')): continue
        count=obs.get('resolved',0)
        if not isinstance(count,int) or isinstance(count,bool) or count<1 or count!=obs.get('total'): continue
        old=selected.get(cid)
        if old is None or count>old['resolved']: selected[cid]=obs
        elif count==old['resolved'] and _digest(old)!=_digest(obs):
            raise ValueError('Conflicting summaries for the same candidate and evidence count')
    # Deterministic bounded sample of candidates, independent of reward/ranking.
    chosen=sorted(selected.values(),key=lambda o:_digest(o['candidateId']))[:MAX_CANDIDATES]
    signature=_digest({'version':VERSION,'compatibility':compatibility,'observations':chosen})
    if previous and previous.get('signature')==signature: return previous
    base={'version':VERSION,'signature':signature,'compatibility':compatibility,'candidateCount':len(chosen),
          'uncertaintyLabel':'ensemble disagreement heuristic; not a calibrated confidence interval',
          'predictionIsEvidence':False,'fitOutsideDispatch':True}
    if len(chosen)<MIN_CANDIDATES:
        return dict(base,status='coverage',reason=f'Need {MIN_CANDIDATES} distinct compatible resolved candidates',trees=[])
    names=sorted(set.intersection(*(set(o['features']) for o in chosen)))
    if not names: return dict(base,status='coverage',reason='No common mechanical features',trees=[])
    rows=[]
    for obs in chosen:
        se=obs.get('standardError')
        weight=1.0 if not _number(se) else 1.0+3.0/(1.0+max(0.0,se)**2)
        rows.append(([obs['features'][name] for name in names],obs['meanEarned'],weight,obs['candidateId']))
    rng=random.Random(int(signature[:16],16)); indices=list(range(len(rows))); rng.shuffle(indices)
    held=set(indices[:max(2,len(rows)//5)])
    training=[r for i,r in enumerate(rows) if i not in held]; testing=[r for i,r in enumerate(rows) if i in held]
    trial=_ensemble(training,rng)
    errors=[statistics.fmean(_predict(t,r[0]) for t in trial)-r[1] for r in testing]
    check={'unit':'candidate','trainCandidates':len(training),'testCandidates':len(testing),
           'meanAbsoluteError':statistics.fmean(abs(e) for e in errors),
           'rootMeanSquaredError':math.sqrt(statistics.fmean(e*e for e in errors)),
           'confirmationDataUsed':False}
    return dict(base,status='fitted',featureNames=names,trees=_ensemble(rows,rng),heldOut=check,
                trainingCandidateIds=[r[3] for r in rows])

def predict(model, features):
    if model.get('status')!='fitted':
        return {'expectedEarned':None,'disagreement':None,'status':'coverage','observed':False}
    try: values=[features[n] for n in model['featureNames']]
    except KeyError: return {'expectedEarned':None,'disagreement':None,'status':'missing-features','observed':False}
    if not all(_number(v) for v in values): raise ValueError('Non-finite prediction feature')
    estimates=[_predict(t,values) for t in model['trees']]
    return {'expectedEarned':statistics.fmean(estimates),'disagreement':statistics.pstdev(estimates),
            'ensemble':estimates,'status':'predicted','observed':False,
            'uncertaintyLabel':model['uncertaintyLabel']}

def rank(model, proposals, *, round_index=0, coverage_slots=1):
    """Return a deterministic optimistic ranking with reserved diverse coverage slots.

    Candidate proposal dicts carry id, features, novelty. No proposal is rejected by prediction.
    The scheduler retains final ownership of admissibility and finite purpose budgets.
    """
    scored=[]
    for proposal in proposals:
        estimate=predict(model,proposal['features'])
        expected=estimate['expectedEarned']; uncertainty=estimate['disagreement']
        score=(expected+uncertainty) if expected is not None else 0.0
        scored.append(dict(proposal,advice=estimate,experimentPriority=score))
    coverage=sorted(scored,key=lambda p:(-float(p.get('novelty',0)),_digest([round_index,p['id']])))[:max(0,coverage_slots)]
    ids={p['id'] for p in coverage}
    remainder=sorted((p for p in scored if p['id'] not in ids),key=lambda p:(-p['experimentPriority'],p['id']))
    return coverage+remainder
