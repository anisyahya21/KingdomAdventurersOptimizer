"""Conditional operating-region questions and paired, explicitly diagnostic uncertainty.

A tested point is a tested point. This service never fills gaps, assumes monotonicity, creates
a safe multidimensional box, or turns fixed-build degradation into a compensation impossibility.
"""
from __future__ import annotations
from copy import deepcopy
import hashlib,json,math,random,statistics

VERSION='conditional-operating-regions-1'
KINDS=('fixed-build','compensated','support')

def canonical(value): return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)
def digest(value): return hashlib.sha256(canonical(value).encode()).hexdigest()

def question(*,kind,reference_id,encounter_revision,mechanics_revision,policy,constraints,
             field,fixed_fields,adjustable_fields,tolerance=0.9,reliability=None,reference_revision=1):
    if kind not in KINDS: raise ValueError('Unknown boundary question kind')
    if not reference_id or not encounter_revision or not mechanics_revision: raise ValueError('Frozen reference and revisions required')
    if isinstance(tolerance,bool) or not isinstance(tolerance,(int,float)) or not 0<tolerance<=1: raise ValueError('Tolerance must be in (0,1]')
    if set(fixed_fields)&set(adjustable_fields): raise ValueError('Fixed and adjustable fields overlap')
    if kind=='fixed-build' and any(f!=field for f in adjustable_fields): raise ValueError('Fixed-build sensitivity may adjust only its questioned field')
    if reliability is not None:
        if not isinstance(reliability,dict) or not isinstance(reliability.get('threshold'),(int,float)) or not math.isfinite(reliability['threshold']): raise ValueError('Reliability requires a finite outcome threshold')
        if not 0<=reliability.get('minimumProbability',-1)<=1: raise ValueError('Reliability probability outside [0,1]')
    value=dict(version=VERSION,kind=kind,referenceId=reference_id,referenceRevision=reference_revision,
               encounterRevision=encounter_revision,mechanicsRevision=mechanics_revision,policy=deepcopy(policy),
               constraints=deepcopy(constraints),field=field,fixedFields=deepcopy(fixed_fields),
               adjustableFields=list(adjustable_fields),tolerance=float(tolerance),reliability=deepcopy(reliability))
    value['id']=digest(value)
    return value

def _index(rows):
    indexed={}; unresolved=0
    for row in rows:
        seeds=row.get('seeds'); amount=row.get('finalEarned')
        if not isinstance(seeds,(list,tuple)) or len(seeds)!=2: unresolved+=1; continue
        key=tuple(seeds)
        if key in indexed: raise ValueError('Duplicate seed pair in boundary evidence')
        if row.get('resolved') is not True or not isinstance(amount,(int,float)) or isinstance(amount,bool) or not math.isfinite(amount):
            unresolved+=1
        indexed[key]=row
    return indexed,unresolved

def _quantile(xs,p):
    xs=sorted(xs); pos=(len(xs)-1)*p; lo=int(pos); hi=min(len(xs)-1,lo+1)
    return xs[lo]+(xs[hi]-xs[lo])*(pos-lo)

def _wilson(successes,n,z=1.959963984540054):
    if not n:return [0.0,1.0]
    p=successes/n; den=1+z*z/n; center=(p+z*z/(2*n))/den
    half=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/den
    return [max(0,center-half),min(1,center+half)]

def assess(study,reference_rows,candidate_rows,*,candidate_id,value,minimum_pairs=16,resamples=512,alpha=0.05):
    """Diagnostic paired bootstrap of candidate - tolerance*reference on complete starting pairs.

    This is development evidence, not a fixed-sample holdout confirmation or guaranteed coverage
    for heavy tails. Whole seed pairs are resampled; event-by-event RNG alignment is not assumed.
    """
    if minimum_pairs<2 or resamples<128 or not 0<alpha<1: raise ValueError('Invalid uncertainty plan')
    a,unresolved_a=_index(reference_rows); b,unresolved_b=_index(candidate_rows)
    result=dict(questionId=study['id'],kind=study['kind'],candidateId=candidate_id,value=deepcopy(value),
                referenceId=study['referenceId'],classification='unresolved',tested=True,interpolation=False,
                totalReference=len(reference_rows),totalCandidate=len(candidate_rows),
                unresolvedReference=unresolved_a,unresolvedCandidate=unresolved_b,
                pairedCount=len(set(a)&set(b)),tolerance=study['tolerance'],
                uncertaintyMethod='paired percentile bootstrap; diagnostic, not calibrated for heavy tails',
                confirmationStatus='development',noCompensationClaim=False)
    if unresolved_a or unresolved_b or set(a)!=set(b):
        return dict(result,reason='Missing, unresolved or unmatched seed pairs block this comparison')
    if not a or len(a)<minimum_pairs:
        return dict(result,reason='Insufficient complete paired evidence for this declared diagnostic plan')
    keys=sorted(a)
    for key in keys:
        for row in (a[key],b[key]):
            if row.get('policy') != study['policy']:
                return dict(result,reason='Evidence resource/Finish policy does not match the frozen question')
            if row.get('encounterRevision')!=study['encounterRevision'] or row.get('mechanicsRevision')!=study['mechanicsRevision']:
                return dict(result,reason='Evidence revisions do not match the frozen question')
        if a[key].get('candidateId')!=study['referenceId'] or b[key].get('candidateId')!=candidate_id:
            return dict(result,reason='Candidate/reference identity mismatch')
    av=[a[k]['finalEarned'] for k in keys]; bv=[b[k]['finalEarned'] for k in keys]
    reference_mean=statistics.fmean(av); mean=statistics.fmean(bv)
    result.update(referenceMeanEarned=reference_mean,meanEarned=mean,meanDifference=mean-reference_mean,
                  retainedFraction=mean/reference_mean if reference_mean>0 else None)
    if reference_mean<=0: return dict(result,reason='Reference has no positive mean; a retention ratio is not informative')
    delta=[y-study['tolerance']*x for x,y in zip(av,bv)]
    rng=random.Random(int(digest([study['id'],candidate_id,keys])[:16],16))
    draws=[statistics.fmean(rng.choices(delta,k=len(delta))) for _ in range(resamples)]
    interval=[_quantile(draws,alpha/2),_quantile(draws,1-alpha/2)]
    result.update(toleranceAdjustedDifference=statistics.fmean(delta),diagnosticInterval=interval,alpha=alpha,resamples=resamples)
    reliability=study.get('reliability'); reliable=None; unreliable=False
    if reliability:
        successes=sum(y>=reliability['threshold'] for y in bv); bounds=_wilson(successes,len(bv))
        result['reliability']={'threshold':reliability['threshold'],'successes':successes,'count':len(bv),
                               'probability':successes/len(bv),'wilson95':bounds,
                               'required':reliability['minimumProbability']}
        reliable=bounds[0]>=reliability['minimumProbability']; unreliable=bounds[1]<reliability['minimumProbability']
    if interval[0]>0 and (reliability is None or reliable):
        result.update(classification='supported-acceptable',reason='Diagnostic evidence supports the declared retention and reliability tolerance at this tested point')
    elif interval[1]<0 or unreliable:
        result.update(classification='supported-degraded',reason='Diagnostic evidence supports a practically harmful loss under this frozen question')
    else:
        result['reason']='Evidence does not resolve the declared tolerance; failure to detect a difference is not equivalence'
    if study['kind']=='compensated' and result['classification']!='supported-acceptable':
        result['scopeLimit']='No acceptable build found for this tested compensation under the stated constraints and budget; other compensations remain open'
    return result

def publish(study,points,*,gaps=(),domain_endpoints=None):
    if any(p.get('questionId')!=study['id'] for p in points): raise ValueError('Cannot mix boundary references/questions')
    return {'question':deepcopy(study),'points':deepcopy(list(points)),'unresolvedGaps':deepcopy(list(gaps)),
            'domainLimitedEndpoints':deepcopy(domain_endpoints),'interpolatedRegions':[],
            'safeCartesianProductClaimed':False,'continuousCoverageClaimed':False,
            'referenceRevision':study['referenceRevision']}
