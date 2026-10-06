"""Canonical, reusable reward/evidence outcome for one strategy measurement.

This module is the single reading layer between a run result and every consumer that wants to
know what a run *earned*. It does not simulate, does not mutate a library, and invents no fact:
it reads the authoritative reward block the runner already produced and labels exactly how far
that reading is entitled to go.

Authority (read-only, `combat_reward_entitlement.entitlement_report`):

  * A native LOSS dispatches nothing (safe zero from the recovered Finish gate
    `special battle flag and winner==1`), independent of any pending count or certificate.
  * A native WIN earns the certified pending count ONLY when a reward-entitlement certificate
    C1..C4 held strictly before the verdict and no prize was queued afterwards
    (`awardedChestCountBasis == 'reward-entitlement-certificate'`).
  * An unresolved battle, a censored run, or a win without a compatible certificate leaves the
    earned count UNKNOWN - never a fabricated zero, never a pending count silently promoted.
  * An early certificate alone does NOT establish a final award, and a certificate is neither
    necessary nor sufficient by itself for the terminal-policy measure below.

Terminal-code rule: only `verdict == 2` is the recovered terminal loss and therefore a proven safe
zero. Any other non-winning integer (0, 3, negative, ...) is NOT promoted to zero; it stays
`unresolved` with no earned number. An explicit runner error or a censored/truncated run is its own
status (`error`/`censored`) that always blocks a full recommendation, even when a terminal verdict is
also present. A numeric `awardedChests` is only accepted under an authoritative basis
(`reward-entitlement-certificate` or the dispatch-gate basis); an arbitrary basis string is refused.
Reported counts must be whole numbers, so a fractional pending/awarded reading is refused, never
rounded.

The one additive branch this module adds beyond the certificate is the *declared simulator
policy* measure. When the caller explicitly declares `finishPolicy == 'on-verdict'` (the input
policy the runner models with the recovered `combat_finish.special_finish` dispatch) and the run
reached a terminal win with a finite pending count, that pending count is the DISPATCHED quantity
under that declared policy. It is labelled `terminal-policy-dispatch` and is kept distinct from a
certified entitlement and from any inventory receipt. Without the explicit policy declaration a
pending count is never promoted: missing counts stay None.

`summarize` keeps every denominator. It reports the arithmetic mean over the *available* readings
(the partial mean) separately from a full resolved claim, refuses to recommend on any subset that
hides an unresolved or errored run, and reports quantiles, zero/loss/resource frequency, sample
counts and simple jackpot/skew diagnostics.
"""
from __future__ import annotations

import json
import math

#: Status of one outcome. `win-unproven`/`unresolved`/`error` carry no earned number.
LOSS = 'loss'
CERTIFIED = 'certified'
TERMINAL_POLICY = 'terminal-policy-dispatch'
WIN_UNPROVEN = 'win-unproven'
UNRESOLVED = 'unresolved'
ERROR = 'error'
#: A run the runner cut short (horizon/ceiling) before a verdict: not an error, but no earned number.
CENSORED = 'censored'

STATUSES = (LOSS, CERTIFIED, TERMINAL_POLICY, WIN_UNPROVEN, UNRESOLVED, ERROR, CENSORED)
#: Statuses whose `finalEarned` is a resolved number and therefore part of a full claim.
RESOLVED_STATUSES = (LOSS, CERTIFIED, TERMINAL_POLICY)
#: Statuses that make a summary ineligible for recommendation (no favourable subset).
UNRESOLVED_STATUSES = (WIN_UNPROVEN, UNRESOLVED, ERROR, CENSORED)

CERTIFICATE_BASIS = 'reward-entitlement-certificate'
LOSS_BASIS = 'native-win-loss-gate'
#: The recovered native dispatch gate's own basis label: `special_finish` dispatches only on `winner==1`.
TERMINAL_DISPATCH_BASIS = 'native-win-dispatch-gate'
#: Basis labels the recovered runner can authoritatively emit, plus the dispatch-gate label.
AUTHORITATIVE_BASES = (CERTIFICATE_BASIS, LOSS_BASIS, TERMINAL_DISPATCH_BASIS,
                       'unknown-win-without-certificate', 'unknown-unresolved-battle')
#: Only the recovered terminal winner/loss codes are terminal: winner==1 wins, winner==2 loses.
WIN_VERDICT = 1
LOSS_VERDICT = 2

_ERROR_KEYS = ('error', 'errorMessage', 'error_message', 'traceback', 'exception')


def _count(value):
    """A whole-number chest count, or None.

    Booleans, NaN/inf, negatives and fractional counts are not counts: a fractional pending/awarded
    reading is malformed, so it is refused rather than rounded into a fabricated integer.
    """
    if value is None or isinstance(value, bool):
        return None
    if not isinstance(value, (int, float)):
        return None
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        if not value.is_integer():
            return None
        value = int(value)
    if value < 0:
        return None
    return value


def _error(result):
    """The runner's explicit error signal, or None. Never ignored just because a verdict exists."""
    for key in _ERROR_KEYS:
        value = result.get(key)
        if value:
            return value if isinstance(value, str) else repr(value)
    nested = result.get('result')
    if isinstance(nested, dict):
        for key in _ERROR_KEYS:
            value = nested.get(key)
            if value:
                return value if isinstance(value, str) else repr(value)
    return None


def _simulation_timing(result):
    """`(ticks, timing)` read from the compact payload or a raw report; each may be None."""
    nested = result.get('result') if isinstance(result.get('result'), dict) else {}
    ticks = result.get('ticks')
    if ticks is None:
        ticks = nested.get('ticks')
    if isinstance(ticks, bool) or not isinstance(ticks, (int, float)):
        ticks = None
    timing = result.get('timing')
    if timing is None:
        timing = nested.get('timing')
    if not isinstance(timing, dict):
        timing = None
    return ticks, timing


def _consumable_policy(policy, result):
    """The declared consumable policy, verbatim, from the caller or the run result; else None."""
    candidates = []
    if isinstance(policy, dict):
        candidates.extend((policy.get('consumablePolicy'), policy.get('consumables')))
        simulator = policy.get('simulator')
        if isinstance(simulator, dict):
            candidates.extend((simulator.get('consumablePolicy'), simulator.get('consumables')))
    candidates.extend((result.get('consumablePolicy'), result.get('consumables')))
    for candidate in candidates:
        if candidate:
            return candidate
    return None


def _verdict(result):
    verdict = result.get('verdict')
    if verdict is None and isinstance(result.get('result'), dict):
        verdict = result['result'].get('verdict')
    return verdict


def _censored(result):
    if bool(result.get('censored')):
        return True
    nested = result.get('result')
    return bool(isinstance(nested, dict) and nested.get('censored'))


def _seeds(result):
    seeds = result.get('seeds')
    if seeds is None and isinstance(result.get('result'), dict):
        seeds = result['result'].get('seeds')
    if isinstance(seeds, (list, tuple)):
        return [seed for seed in seeds]
    return []


def _resource_uses(result):
    uses = result.get('resourceUses')
    if uses is None and isinstance(result.get('behavior'), dict):
        uses = None
    return _count(uses)


def _reward_block(result):
    """The authoritative reward reading, from the adapter's `rewardOutcome` or a raw entitlement."""
    reward = result.get('rewardOutcome')
    if isinstance(reward, dict):
        return reward
    nested = result.get('result')
    entitlement = nested.get('rewardEntitlement') if isinstance(nested, dict) else None
    if isinstance(entitlement, dict):
        return dict(pendingChests=entitlement.get('pendingChestCount'),
                    awardedChests=entitlement.get('awardedChestCount'),
                    awardedBasis=entitlement.get('awardedChestCountBasis'),
                    reason=entitlement.get('rewardCountReason'))
    return {}


def _finish_policy(policy, result):
    """The explicitly declared finish policy, from the caller or the result itself."""
    value = None
    if isinstance(policy, str):
        value = policy
    elif isinstance(policy, dict):
        value = policy.get('finishPolicy') or policy.get('finish_policy')
        simulator = policy.get('simulator')
        if value is None and isinstance(simulator, dict):
            value = simulator.get('finishPolicy')
    if value is None:
        value = result.get('finishPolicy')
    return value


def outcome(result, *, candidate_id=None, encounter_revision=None, mechanics_revision=None,
            experiment_id=None, measurement_window=None, policy=None):
    """The canonical outcome for one real run result (adapter compact dict or raw report).

    Returns a JSON-ready dict. `finalEarned` is a number only when the reading is entitled to one;
    otherwise it is None and `status`/`reason` say why. `pending` is always kept separately. The
    identity/revision fields are carried through so the outcome stays comparable across runs.
    """
    result = result if isinstance(result, dict) else {}
    reward = _reward_block(result)
    pending = _count(reward.get('pendingChests'))
    awarded = _count(reward.get('awardedChests'))
    basis = reward.get('awardedBasis') if isinstance(reward.get('awardedBasis'), str) else None
    finish = _finish_policy(policy, result)
    verdict = _verdict(result)
    error = _error(result)
    ticks, timing = _simulation_timing(result)

    row = dict(
        candidateId=candidate_id,
        encounterRevision=encounter_revision,
        mechanicsRevision=mechanics_revision,
        experimentId=experiment_id,
        measurementWindow=measurement_window,
        finishPolicy=finish,
        policy=policy,
        verdict=(verdict if isinstance(verdict, int) and not isinstance(verdict, bool) else None),
        censored=_censored(result),
        pending=pending,
        awardedReported=awarded,
        finalEarned=None,
        basis=basis,
        status=None,
        reason=None,
        resolved=False,
        error=False,
        seeds=_seeds(result),
        ticks=ticks,
        timing=timing,
        consumablePolicy=_consumable_policy(policy, result),
        costs=dict(resourceUses=_resource_uses(result)),
    )

    if row['censored']:
        row['status'] = CENSORED
        row['reason'] = ('run censored: the runner cut the run short before a verdict, so no earned '
                         'count is claimed (this is a truncated run, not a runner error)')
        return row

    if error is not None:
        row['status'], row['error'] = ERROR, True
        row['reason'] = ('runner reported an error (%s): no earned count is claimed even though the '
                         'result also carries a terminal reading' % error)
        return row

    if verdict is None:
        row['status'] = UNRESOLVED
        row['reason'] = ('battle unresolved (no verdict): pending %r is reported, the earned count is '
                         'unknown' % (pending,))
        return row

    if not isinstance(verdict, int) or isinstance(verdict, bool):
        row['status'], row['error'] = ERROR, True
        row['reason'] = 'non-integer verdict %r is not a valid terminal outcome' % (verdict,)
        return row

    if verdict == LOSS_VERDICT:
        if awarded not in (None, 0):
            row['status'], row['error'] = ERROR, True
            row['reason'] = ('authoritative loss gate awards 0 but the result reports %r: an '
                             'incompatible numeric award is refused' % (awarded,))
            return row
        row.update(status=LOSS, finalEarned=0, resolved=True,
                   basis=basis or LOSS_BASIS,
                   reason=('safe 0 from the native win/loss dispatch gate; pending %r is not an award '
                           'and no certificate can promote it' % (pending,)))
        return row

    if verdict != WIN_VERDICT:
        # Any other integer (0, 3, negative, ...) is NOT the recovered terminal loss 2: only winner==2
        # is a proven safe zero, so an unknown code must not be mapped to a numeric zero.
        row['status'] = UNRESOLVED
        row['reason'] = ('verdict %r is neither the recovered win (1) nor the recovered terminal loss '
                         '(2): only 2 is a proven safe zero, so no earned count is claimed (pending %r '
                         'is reported, unresolved)' % (verdict, pending))
        return row

    # verdict == 1: a terminal win.
    if awarded is not None:
        if basis == CERTIFICATE_BASIS:
            row.update(status=CERTIFIED, finalEarned=awarded, resolved=True, basis=CERTIFICATE_BASIS,
                       reason=('certified pre-verdict entitlement (basis %s): awarded equals the pending '
                               'count at the certificate' % CERTIFICATE_BASIS))
            return row
        if basis == TERMINAL_DISPATCH_BASIS:
            row.update(status=TERMINAL_POLICY, finalEarned=awarded, resolved=True,
                       basis=TERMINAL_DISPATCH_BASIS,
                       reason=('awarded numeric under the authoritative terminal dispatch basis %s: the '
                               'dispatched count is accepted, distinct from an entitlement and from an '
                               'inventory receipt' % TERMINAL_DISPATCH_BASIS))
            return row
        row['status'], row['error'] = ERROR, True
        row['reason'] = ('a numeric award %r came with basis %r, which is not an authoritative dispatch '
                         'basis (expected %s or %s): an arbitrary basis string is refused and never '
                         'promoted' % (awarded, basis, CERTIFICATE_BASIS, TERMINAL_DISPATCH_BASIS))
        return row

    if finish == 'on-verdict' and pending is not None:
        row.update(status=TERMINAL_POLICY, finalEarned=pending, resolved=True,
                   basis=TERMINAL_POLICY,
                   reason=('declared finishPolicy=on-verdict: finite pending count %r is the DISPATCHED '
                           'quantity under this declared simulator policy (recovered special_finish); it '
                           'is not a certified entitlement and not an inventory receipt' % (pending,)))
        return row

    row['status'] = WIN_UNPROVEN
    row['reason'] = ('win without a compatible pre-verdict certificate or an explicit on-verdict policy: '
                     'the earned count stays unknown (pending %r is not promoted)' % (pending,))
    return row


def is_resolved(row):
    return bool(row) and row.get('status') in RESOLVED_STATUSES


def _mean(values):
    return sum(values) / float(len(values)) if values else None


def _quantile(sorted_values, fraction):
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = (len(sorted_values) - 1) * fraction
    low = int(math.floor(position))
    high = int(math.ceil(position))
    if low == high:
        return sorted_values[low]
    weight = position - low
    return sorted_values[low] * (1.0 - weight) + sorted_values[high] * weight


def _stdev(values, mean):
    if len(values) < 2 or mean is None:
        return None
    variance = sum((value - mean) ** 2 for value in values) / float(len(values))
    return math.sqrt(variance)


def _skewness(values, mean, stdev):
    if len(values) < 3 or not stdev:
        return None
    m3 = sum((value - mean) ** 3 for value in values) / float(len(values))
    return m3 / (stdev ** 3)


def summarize(outcomes, thresholds=()):
    """Aggregate a list of `outcome` rows without hiding a denominator.

    `partialMean` describes outcomes carrying a number. `meanEarned` and `fullClaim` expose
    it only when EVERY outcome resolved without error, else they are None. Any unresolved
    or errored outcome forces `eligibleForRecommendation` false, so no favourable subset is ever
    recommended. Diagnostics report skew and a one-sample jackpot share.
    """
    rows = [row for row in (outcomes or []) if isinstance(row, dict)]
    n = len(rows)
    by_status = {}
    by_basis = {}
    numeric = []
    resources = []
    losses = zeros = unresolved = errors = censored = 0
    for row in rows:
        status = row.get('status')
        by_status[status] = by_status.get(status, 0) + 1
        basis = row.get('basis') or 'none'
        by_basis[basis] = by_basis.get(basis, 0) + 1
        if status == ERROR:
            errors += 1
        if status == CENSORED:
            censored += 1
        if status in UNRESOLVED_STATUSES:
            unresolved += 1
        if status == LOSS:
            losses += 1
        value = row.get('finalEarned')
        if value is not None and not isinstance(value, bool):
            numeric.append(value)
            if value == 0:
                zeros += 1
        uses = (row.get('costs') or {}).get('resourceUses')
        if uses is not None and not isinstance(uses, bool):
            resources.append(uses)

    ordered = sorted(numeric)
    mean = _mean(ordered)
    stdev = _stdev(ordered, mean)
    complete = bool(n) and unresolved == 0 and errors == 0 and len(numeric) == n
    report = dict(
        total=n,
        numericCount=len(numeric),
        resolvedCount=len(numeric),
        byStatus=by_status,
        byBasis=by_basis,
        unresolvedCount=unresolved,
        errorCount=errors,
        censoredCount=censored,
        lossCount=losses,
        zeroCount=zeros,
        lossFrequency=(losses / float(n)) if n else None,
        zeroFrequency=(zeros / float(len(numeric))) if numeric else None,
        resourceCount=sum(1 for value in resources if value > 0),
        resourceMean=_mean(resources),
        resourceFrequency=((sum(1 for value in resources if value > 0) / float(len(resources)))
                           if resources else None),
        partialMean=mean,
        meanEarned=mean if complete else None,
        median=_quantile(ordered, 0.5),
        min=ordered[0] if ordered else None,
        max=ordered[-1] if ordered else None,
        stdev=stdev,
        quantiles={name: _quantile(ordered, fraction) for name, fraction in
                   (('p0', 0.0), ('p25', 0.25), ('p50', 0.5), ('p75', 0.75), ('p100', 1.0))},
        metricSampleCounts=dict(total=n, numeric=len(numeric), resources=len(resources)),
        eligibleForRecommendation=complete and len(numeric) > 0,
        fullClaim=dict(complete=complete, meanEarned=mean if complete else None,
                       note='a full claim needs every outcome resolved with no error; the partial '
                            'mean covers only the available readings'),
        diagnostics=dict(
            skewness=_skewness(ordered, mean, stdev),
            top1ShareOfTotal=((ordered[-1] / sum(ordered)) if ordered and sum(ordered) > 0 else None),
            maxToMedianRatio=((ordered[-1] / _quantile(ordered, 0.5))
                              if ordered and _quantile(ordered, 0.5) else None),
            jackpotSensitive=bool(len(ordered) >= 2 and sum(ordered) > 0
                                  and ordered[-1] / sum(ordered) > 0.5),
            sampleCount=len(ordered)),
    )
    report['thresholds'] = {str(threshold):
                            dict(count=sum(1 for value in ordered if value >= threshold),
                                 frequency=(sum(1 for value in ordered if value >= threshold) /
                                            float(len(ordered)) if ordered else None))
                            for threshold in (thresholds or ())}
    report['denominators'] = dict(attempted=n, resolved=len(numeric), lost=losses,
                                  unresolved=unresolved, errors=errors, censored=censored)
    return report


def compact_json(row):
    """Stable JSON for persisting one outcome (used by the experiment store)."""
    return json.dumps(row, sort_keys=True, separators=(',', ':'))
