"""Frozen, fixed-sample confirmation decisions on paired canonical terminal earnings.

The percentile bootstrap is an empirical uncertainty diagnostic, not a distribution-free
coverage guarantee for rare unseen jackpots. Report that limit with every decision. A
leave-largest-positive-pair-out check prevents a single observed windfall carrying a claim.
No partial holdout is scored; selection and sample count must be frozen before dispatch.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
import random
import statistics

VERSION = 'paired-confirmation-1'
MINIMUM_PAIRS = 64
RESAMPLES = 8192


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def freeze(*, nominee, reference, pairs, policy, encounter_revision, mechanics_revision,
           engine_revision, alpha=0.05, maximum_nominations=1, nomination_index=1):
    if not nominee or not reference or nominee == reference:
        raise ValueError('Distinct exact nominee/reference identities are required.')
    if not all((encounter_revision, mechanics_revision, engine_revision)):
        raise ValueError('All compatibility revisions must be frozen.')
    if not isinstance(policy, dict) or not policy:
        raise ValueError('The exact measurement/resource policy must be frozen.')
    if isinstance(alpha, bool) or not isinstance(alpha, (int, float)) or not 0 < alpha < 1:
        raise ValueError('Alpha must be in (0,1).')
    if (type(maximum_nominations) is not int or type(nomination_index) is not int
            or not 1 <= nomination_index <= maximum_nominations):
        raise ValueError('Invalid predeclared nomination count/index.')
    keys = [tuple(pair) for pair in pairs]
    if not keys or len(set(keys)) != len(keys):
        raise ValueError('A nonempty, distinct ordered-pair plan is required.')
    for key in keys:
        if len(key) != 2 or any(type(x) is not int or not 0 <= x <= 0x7fffffff for x in key):
            raise ValueError('Each battle takes two ordered 31-bit RNG seeds.')
    plan = dict(version=VERSION, nominee=nominee, reference=reference,
                pairs=[list(pair) for pair in keys], policy=policy,
                encounterRevision=encounter_revision, mechanicsRevision=mechanics_revision,
                engineRevision=engine_revision, alpha=float(alpha),
                maximumNominations=maximum_nominations, nominationIndex=nomination_index,
                effectiveAlpha=float(alpha) / maximum_nominations,
                minimumPairs=MINIMUM_PAIRS, resamples=RESAMPLES,
                metric='arithmetic mean terminal finalEarned',
                method='paired percentile bootstrap with single-windfall sensitivity check',
                coverageLimit='Empirical diagnostic; no guaranteed heavy-tail or unseen-tail coverage.',
                stopping='One fixed sample; no outcome-dependent extension or partial inspection')
    plan['id'] = _digest(plan)
    return json.loads(json.dumps(plan))


def _quantile(values, fraction):
    point = (len(values) - 1) * fraction
    low = int(point)
    high = min(low + 1, len(values) - 1)
    return values[low] + (values[high] - values[low]) * (point - low)


def _summary(values):
    return dict(count=len(values), mean=statistics.fmean(values),
                median=statistics.median(values), zeroFrequency=values.count(0) / len(values),
                maximum=max(values),
                standardError=statistics.stdev(values) / math.sqrt(len(values))
                if len(values) > 1 else None)


def decide(plan, rows):
    """Rows carry candidateId, seedPair and canonical outcome; never drop a bad row."""
    frozen = dict(plan)
    identity = frozen.pop('id', None)
    if identity != _digest(frozen) or plan.get('version') != VERSION:
        raise ValueError('Confirmation plan changed after nomination.')
    if plan.get('minimumPairs') != MINIMUM_PAIRS or plan.get('resamples') != RESAMPLES:
        raise ValueError('Unsupported confirmation decision rule.')
    expected = {(candidate, tuple(pair)) for candidate in (plan['nominee'], plan['reference'])
                for pair in plan['pairs']}
    indexed = {}
    invalid = Counter()
    for row in rows:
        pair = row.get('seedPair')
        if (not isinstance(pair, (list, tuple)) or len(pair) != 2
                or any(type(x) is not int or not 0 <= x <= 0x7fffffff for x in pair)):
            invalid['missing-pair'] += 1
            continue
        key = (row.get('candidateId'), tuple(pair))
        if key not in expected:
            invalid['unplanned-row'] += 1
            continue
        if key in indexed:
            invalid['duplicate-row'] += 1
            continue
        outcome = row.get('outcome') or {}
        indexed[key] = outcome
        amount = outcome.get('finalEarned')
        if (outcome.get('resolved') is not True or outcome.get('error')
                or isinstance(amount, bool) or not isinstance(amount, (int, float))
                or not math.isfinite(amount) or amount < 0):
            invalid['unresolved-or-invalid'] += 1
        for field in ('policy', 'encounterRevision', 'mechanicsRevision', 'engineRevision'):
            if row.get(field) != plan[field]:
                invalid['incompatible-' + field] += 1
    result = dict(status='inconclusive', confirmed=False, planId=identity,
                  plannedPairs=len(plan['pairs']), plannedRuns=len(expected), receivedRuns=len(rows),
                  validPlanRows=len(indexed), minimumPairs=plan['minimumPairs'],
                  method=plan['method'], coverageLimit=plan['coverageLimit'],
                  alpha=plan['effectiveAlpha'], nominationIndex=plan['nominationIndex'],
                  maximumNominations=plan['maximumNominations'])
    if invalid or set(indexed) != expected:
        # No partial means, percentiles or nominee ordering leak from an unfinished holdout.
        return dict(result, reason='Incomplete, unresolved, duplicate or incompatible holdout.',
                    invalid=dict(invalid), missingRuns=len(expected - set(indexed)))
    if len(plan['pairs']) < plan['minimumPairs']:
        return dict(result, reason='Complete sample is smaller than the predeclared decision minimum.')
    pairs = [tuple(pair) for pair in plan['pairs']]
    a = [indexed[(plan['reference'], pair)]['finalEarned'] for pair in pairs]
    b = [indexed[(plan['nominee'], pair)]['finalEarned'] for pair in pairs]
    differences = [y - x for x, y in zip(a, b)]
    mean = statistics.fmean(differences)
    result.update(reference=_summary(a), nominee=_summary(b), pairedDifference=mean,
                  lossFrequencyReference=sum(indexed[(plan['reference'], p)].get('verdict') == 2
                                             for p in pairs) / len(pairs),
                  lossFrequencyNominee=sum(indexed[(plan['nominee'], p)].get('verdict') == 2
                                           for p in pairs) / len(pairs))
    rng = random.Random(int(identity[:16], 16))
    draws = sorted(statistics.fmean(rng.choices(differences, k=len(differences)))
                   for _ in range(plan['resamples']))
    alpha = plan['effectiveAlpha']
    interval = [_quantile(draws, alpha / 2), _quantile(draws, 1 - alpha / 2)]
    without_largest = (sum(differences) - max(differences)) / (len(differences) - 1)
    result.update(diagnosticInterval=interval, resamples=plan['resamples'],
                  meanWithoutLargestPositivePair=without_largest,
                  singleWindfallSensitive=(mean > 0 and without_largest <= 0))
    result['relativeGain'] = (statistics.fmean(b) / statistics.fmean(a) - 1
                              if statistics.fmean(a) > 0 else None)
    if interval[0] > 0 and without_largest > 0:
        return dict(result, status='supported-improvement', confirmed=True,
                    reason='Fresh fixed-sample paired evidence supports a higher mean under the frozen diagnostic rule.')
    if interval[1] < 0:
        return dict(result, status='supported-regression',
                    reason='Fresh paired evidence supports a lower mean under the frozen diagnostic rule.')
    return dict(result, reason='The predeclared uncertainty or windfall-sensitivity rule is inconclusive.')
