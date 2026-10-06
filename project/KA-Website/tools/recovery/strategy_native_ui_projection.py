"""Project persisted native optimizer evidence into the original React host shapes.

Only fields supported by normalized native rows or their raw native reports are projected. Missing
Finish/retention metrics and loss-opportunity metrics remain unknown; maxima never fill mean fields.
The owning controller/library/workflow modules call these helpers at their API boundaries.
"""
from __future__ import annotations

import math
from collections import Counter


def _finite(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(float(value)))


def _report_maps(run):
    result = run.get('result') if isinstance(run.get('result'), dict) else {}
    outer = run.get('report') if isinstance(run.get('report'), dict) else {}
    nested = outer.get('report') if isinstance(outer.get('report'), dict) else {}
    core = result.get('report') if isinstance(result.get('report'), dict) else nested
    fields = core.get('fields') if isinstance(core.get('fields'), dict) else {}
    return [run, result, outer, core, fields]


def _pick(maps, *keys):
    for mapping in maps:
        if not isinstance(mapping, dict):
            continue
        for key in keys:
            if key in mapping and mapping[key] is not None:
                return mapping[key]
    return None


def project_strategy_row(row):
    """Return the React EncounterStrategyRow aliases without crossing Earned/Potential lanes."""
    if not isinstance(row, dict):
        return row
    result = dict(row)
    candidate_id = result.get('candidateId', result.get('id'))
    if candidate_id is not None:
        result.setdefault('candidateId', str(candidate_id))
        result.setdefault('id', str(candidate_id))
    result.setdefault('intent', result.get('scenario'))
    result.setdefault('scenario', result.get('intent'))
    result.setdefault('label', result.get('displayName') or result.get('candidateId'))
    result.setdefault('source', 'native')
    summary = result.get('nativeSummary') if isinstance(result.get('nativeSummary'), dict) else {}
    # The native summary counts all eligible production attempts; the broader sidecar summary
    # also retains diagnostics and dedup conflicts and must not be presented as validation trials.
    for target, source in (('attempts', 'n'), ('wins', 'wins'), ('losses', 'losses'),
                           ('noVerdict', 'censored'), ('winRate', 'winRate'),
                           ('comparable', 'comparable')):
        if source in summary:
            result[target] = summary[source]
    # The legacy overview calls the same measured earned maximum bestChestsEarned/chestMax.
    # Potential is deliberately sourced only from its own loss-only field.
    if result.get('bestEarned') is None and _finite(summary.get('chestMax')):
        result['bestEarned'] = summary['chestMax']
    if result.get('bestPotential') is None and _finite(result.get('bestPotentialChests')):
        result['bestPotential'] = result['bestPotentialChests']
    if result.get('bestChestsEarned') is None and _finite(result.get('bestEarned')):
        result['bestChestsEarned'] = result['bestEarned']
    result.setdefault('attempts', 0)
    result.setdefault('earnedSamples', 0)
    result.setdefault('potentialSamples', 0)
    result.setdefault('eaEarnedSamples', 0)
    result.setdefault('comparable', False)
    return result


def project_candidate_view(row):
    """Shape one native status candidate for the React Candidate/validation summary types."""
    shaped = project_strategy_row(row)
    if not isinstance(shaped, dict):
        return shaped
    summary = shaped.get('nativeSummary') if isinstance(shaped.get('nativeSummary'), dict) else {}
    attempts = shaped.get('attempts', 0)
    validation = dict(
        n=summary.get('n', attempts),
        wins=summary.get('wins', shaped.get('wins', 0)),
        losses=summary.get('losses', shaped.get('losses', 0)),
        censored=summary.get('censored', shaped.get('noVerdict', 0)),
        winRate=summary.get('winRate', shaped.get('winRate')),
        winInterval=summary.get('winInterval', [0, 1]),
        failureRate=summary.get('failureRate'),
        unresolvedRate=summary.get('unresolvedRate'),
        retainedMean=summary.get('retainedMean'),
        retainedSampleCount=summary.get('retainedSampleCount'),
        chestMean=summary.get('chestMean', shaped.get('meanEarned')),
        chestSampleCount=summary.get('chestSampleCount', shaped.get('earnedSamples', 0)),
        chestMin=summary.get('chestMin'),
        chestMax=summary.get('chestMax', shaped.get('bestEarned')),
        chestSD=summary.get('chestSD'),
        chestSE=summary.get('chestSE'),
        callbackMean=summary.get('callbackMean'), callbackSD=summary.get('callbackSD'),
        callbackMin=summary.get('callbackMin'), callbackP10=summary.get('callbackP10'),
        callbackMax=summary.get('callbackMax'), callbackHistogram=summary.get('callbackHistogram'),
        meanResources=summary.get('meanResources'), meanSurvivors=summary.get('meanSurvivors'),
        meanTicks=summary.get('meanTicks'), comparable=bool(summary.get('comparable', shaped.get('comparable', False))))
    # The native controller has no separately classified discovery/selection banks. Keep those
    # UI scopes explicitly empty instead of relabelling the same aggregate validation evidence.
    empty = dict(n=0, wins=0, losses=0, censored=0, winRate=None, winInterval=[0, 1],
        failureRate=None, unresolvedRate=None, retainedMean=None, retainedSampleCount=0,
        chestMean=None, chestSampleCount=0, chestMin=None, chestMax=None, chestSD=None, chestSE=None,
        callbackMean=None, callbackSD=None, callbackMin=None, callbackP10=None, callbackMax=None,
        callbackHistogram=None, meanResources=None, meanSurvivors=None, meanTicks=None, comparable=False)
    # Keep original native row meanings distinct from the UI aliases.
    shaped.update(id=shaped.get('candidateId'), scenario=shaped.get('intent'), stats={},
        discovery=empty, selection=empty, validation=validation,
        validationRuns=validation['n'], family=None, examples={}, native=True,
        bestChestsEarned=shaped.get('bestEarned'), bestPotentialChests=shaped.get('bestPotential'))
    return shaped


def project_run(run, normalized=None):
    """Add Example fields React reads, using normalized DB outcome columns when supplied."""
    if not isinstance(run, dict):
        return run
    result = dict(run)
    normalized = normalized if isinstance(normalized, dict) else {}
    maps = [normalized, *_report_maps(result)]
    verdict = _pick(maps, 'verdict')
    resolved = _pick(maps, 'resolved')
    battle_valid = _pick(maps, 'battleValid', 'battle_valid', 'completed')
    if isinstance(resolved, bool):
        result['resolved'] = resolved
    if isinstance(battle_valid, bool):
        result['battleValid'] = battle_valid
    for field, aliases in (
        ('earnedEligible', ('earnedEligible', 'earned_eligible')),
        ('diagnostic', ('diagnostic',)), ('dedupConflict', ('dedupConflict', 'dedup_conflict')),
        ('executionMode', ('executionMode', 'execution_mode')), ('purpose', ('purpose',)),
        ('earnedBasis', ('earnedBasis', 'earned_basis')),
        ('sourceEarnedBasis', ('sourceEarnedBasis', 'source_earned_basis')),
        ('higherPotential', ('higherPotential', 'higher_potential'))):
        value = _pick([normalized], *aliases)
        if value is not None:
            result[field] = value
    if isinstance(verdict, int) and not isinstance(verdict, bool):
        result['verdict'] = verdict
    elif resolved is True:
        result['verdict'] = None
    if isinstance(normalized.get('resolved'), bool):
        if normalized['resolved']:
            result['censored'] = False
        elif normalized.get('battle_valid', normalized.get('battleValid')) is True:
            result['censored'] = True
    elif 'censored' not in result or result.get('censored') is None:
        if verdict == 0:
            result['censored'] = True
        elif resolved is True or verdict in (1, 2):
            result['censored'] = False
        elif battle_valid is True and resolved is False:
            result['censored'] = True
    earned = _pick([normalized, result], 'earned')
    eligible = _pick([normalized, result], 'earnedEligible', 'earned_eligible')
    if result.get('chests') is None and eligible is True and _finite(earned):
        result['chests'] = earned
    if result.get('chestBasis') is None:
        result['chestBasis'] = _pick([normalized, result], 'earnedBasis', 'earned_basis', 'sourceEarnedBasis')
    if result.get('rewardOutcome') is None:
        result['rewardOutcome'] = _pick(_report_maps(result), 'rewardOutcome', 'rewardEntitlement')
    fields = (
        ('prizeCallbacks', ('prizeCallbacks', 'prize_callbacks')),
        ('survivors', ('survivors',)),
        ('resourceUses', ('resourceUses', 'resource_uses')),
        ('ticks', ('ticks', 'simulatedTicks')),
    )
    for target, aliases in fields:
        if result.get(target) is None:
            value = _pick(maps, *aliases)
            if _finite(value):
                result[target] = value
    # Native replay does not prove Finish/inventory retention; never infer it from chest callbacks.
    result.setdefault('retained', None)
    return result


def _quantile(sorted_values, probability):
    if not sorted_values:
        return None
    index = (len(sorted_values) - 1) * probability
    lower = int(index)
    upper = min(len(sorted_values) - 1, lower + 1)
    return sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * (index - lower)


def project_outcome_distribution(runs, verification_only=False):
    """Build the React histogram only from durable, eligible production Earned observations."""
    source = [row for row in runs if isinstance(row, dict)]
    valid = [row for row in source if row.get('executionMode') == 'production'
             and row.get('purpose') == 'production' and row.get('battleValid') is True
             and row.get('diagnostic') is not True and row.get('dedupConflict') is not True]
    if verification_only:
        return dict(available=False, reason='Verification-only rows are not production validation evidence.',
                    knownSamples=0, evidenceRows=len(valid), lifetimeValidationTotal=len(valid),
                    unresolved=0, unknown=0, coverage='none', missingEvidence=0)
    values = [float(row['earned']) for row in valid if row.get('earnedEligible') is True
              and row.get('resolved') is True and _finite(row.get('earned'))]
    unresolved = sum(row.get('resolved') is not True for row in valid)
    unknown = sum(row.get('resolved') is True and not (
        row.get('earnedEligible') is True and _finite(row.get('earned'))) for row in valid)
    sorted_values = sorted(values)
    counts = Counter(sorted_values)
    bins = [dict(chests=value, count=counts[value]) for value in sorted(counts)]
    n = len(sorted_values)
    complete = n == len(valid) and unresolved == 0 and unknown == 0
    result = dict(available=bool(n), bins=bins, knownSamples=n, evidenceRows=len(valid),
        lifetimeValidationTotal=len(valid), unresolved=unresolved, unknown=unknown,
        coverage='complete' if complete else ('partial' if n or valid else 'none'),
        missingEvidence=0, min=(sorted_values[0] if n else None), max=(sorted_values[-1] if n else None),
        mean=(sum(sorted_values)/n if n else None), p10=_quantile(sorted_values, .1),
        median=_quantile(sorted_values, .5), p90=_quantile(sorted_values, .9),
        mode=(min(counts, key=lambda value: (-counts[value], value)) if n else None))
    if not n:
        result['reason'] = 'No eligible production Earned observations are recorded for this build.'
    return result


def project_detail(detail):
    """Project native detail/runs into the original React investigation response shape."""
    if not isinstance(detail, dict):
        return detail
    result = dict(detail)
    stored = [project_run(row) for row in (result.get('storedRuns') or [])]
    trials = [project_run(row) for row in (result.get('trialRuns') or [])]
    result['storedRuns'], result['trialRuns'] = stored, trials
    if result.get('outcomeDistribution') is None:
        result['outcomeDistribution'] = project_outcome_distribution(
            stored, verification_only=result.get('verificationOnly') is True)
    result.setdefault('measured', bool((result.get('storedSummary') or {}).get('earnedSamples', 0)))
    return result
