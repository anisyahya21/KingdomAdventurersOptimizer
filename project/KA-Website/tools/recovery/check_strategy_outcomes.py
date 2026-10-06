"""Focused checks for `strategy_outcomes`: reward edge cases and honest summarization.

Every case is a hand-built reward reading (no simulator), so the checks pin the reading rules:
certified win, terminal-Cure/on-verdict dispatch, safe loss zero, unresolved/censored/error, invalid
booleans/NaN/negatives, the nested entitlement path, and the summarize denominators.

    python check_strategy_outcomes.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import strategy_outcomes as so


def _win_basis(basis, awarded, pending):
    return dict(verdict=1, rewardOutcome=dict(awardedChests=awarded, awardedBasis=basis,
                                              pendingChests=pending), seeds=[1, 2], resourceUses=0)


def reward_edge_cases():
    checks = {}
    # 1. Certified pre-verdict win -> the certified award.
    row = so.outcome(_win_basis('reward-entitlement-certificate', 2, 2))
    assert (row['status'], row['finalEarned'], row['basis']) == ('certified', 2, 'reward-entitlement-certificate')
    checks['certifiedWin'] = dict(finalEarned=row['finalEarned'])

    # 2. Terminal Cure / win without certificate, declared on-verdict -> policy dispatch, NOT entitlement.
    cure = _win_basis('unknown-win-without-certificate', None, 3)
    row = so.outcome(cure, policy='on-verdict')
    assert row['status'] == 'terminal-policy-dispatch' and row['finalEarned'] == 3
    assert row['basis'] == 'terminal-policy-dispatch' and row['pending'] == 3
    checks['terminalCureOnVerdict'] = dict(finalEarned=row['finalEarned'], pending=row['pending'])

    # 2b. Same win WITHOUT a declared policy: pending is never promoted.
    bare = so.outcome(_win_basis('unknown-win-without-certificate', None, 3))
    assert bare['status'] == 'win-unproven' and bare['finalEarned'] is None and bare['pending'] == 3
    checks['winUnprovenNoPolicy'] = dict(finalEarned=bare['finalEarned'], pending=bare['pending'])

    # 2c. Declared policy but missing pending -> still unknown.
    missing = so.outcome(dict(verdict=1, rewardOutcome=dict(pendingChests=None)), policy='on-verdict')
    assert missing['status'] == 'win-unproven' and missing['finalEarned'] is None
    checks['onVerdictMissingPending'] = dict(finalEarned=missing['finalEarned'])

    # 3. Loss is a safe zero, independent of a large pending count.
    loss = so.outcome(dict(verdict=2, rewardOutcome=dict(awardedChests=0, pendingChests=15)))
    assert loss['status'] == 'loss' and loss['finalEarned'] == 0 and loss['pending'] == 15
    checks['lossSafeZero'] = dict(finalEarned=loss['finalEarned'], pending=loss['pending'])

    # 3b. A loss claiming a positive award is incompatible and refused.
    bad = so.outcome(dict(verdict=2, rewardOutcome=dict(awardedChests=5, pendingChests=1)))
    assert bad['status'] == 'error' and bad['error'] and bad['finalEarned'] is None
    checks['lossIncompatibleRefused'] = True

    # 4. Unresolved and censored runs never claim a number. Censored is a DISTINCT status from error.
    unresolved = so.outcome(dict(verdict=None, rewardOutcome=dict(pendingChests=1)))
    assert unresolved['status'] == 'unresolved' and unresolved['finalEarned'] is None
    censored = so.outcome(dict(verdict=None, censored=True, rewardOutcome=dict(pendingChests=1)))
    assert censored['status'] == 'censored' and censored['error'] is False and censored['resolved'] is False
    assert censored['finalEarned'] is None
    checks['unresolvedAndCensored'] = dict(unresolved=unresolved['status'], censored=censored['status'])

    # 4b. Only the recovered terminal loss 2 is a safe zero. Other integers/negatives stay unresolved.
    for unknown_verdict in (0, 3, -1, 7):
        other = so.outcome(dict(verdict=unknown_verdict, rewardOutcome=dict(awardedChests=0,
                                                                             pendingChests=4)))
        assert other['status'] == 'unresolved' and other['resolved'] is False
        assert other['finalEarned'] is None, unknown_verdict
    checks['onlyVerdict2IsLoss'] = True

    # 4c. An explicit runner error is never ignored just because the result also has a terminal verdict.
    errored = so.outcome(dict(verdict=1, error='engine blew up', finishPolicy='on-verdict',
                              rewardOutcome=dict(awardedChests=3,
                                                 awardedBasis='reward-entitlement-certificate',
                                                 pendingChests=3)))
    assert errored['status'] == 'error' and errored['error'] is True and errored['finalEarned'] is None
    nested_error = so.outcome(dict(verdict=1, result=dict(verdict=1, error='nested failure'),
                                   rewardOutcome=dict(pendingChests=2)), policy='on-verdict')
    assert nested_error['status'] == 'error' and nested_error['error'] is True
    checks['explicitErrorWins'] = True

    # 4d. A numeric award needs an authoritative basis; an arbitrary basis string is refused.
    arbitrary = so.outcome(dict(verdict=1, rewardOutcome=dict(awardedChests=4,
                                                              awardedBasis='trust-me-bro',
                                                              pendingChests=4)))
    assert arbitrary['status'] == 'error' and arbitrary['error'] is True
    dispatch = so.outcome(dict(verdict=1, rewardOutcome=dict(awardedChests=4,
                                                             awardedBasis='native-win-dispatch-gate',
                                                             pendingChests=4)))
    assert dispatch['status'] == 'terminal-policy-dispatch' and dispatch['finalEarned'] == 4
    checks['authoritativeBasisOnly'] = True

    # 5. Booleans, NaN, negatives and FRACTIONS are not counts.
    invalid = so.outcome(dict(verdict=1, rewardOutcome=dict(awardedChests=True, pendingChests=-1)),
                         policy='on-verdict')
    assert invalid['pending'] is None and invalid['awardedReported'] is None
    assert invalid['finalEarned'] is None and invalid['status'] == 'win-unproven'
    nan = so.outcome(dict(verdict=1, rewardOutcome=dict(awardedChests=float('nan'), pendingChests=2.0)),
                     policy='on-verdict')
    assert nan['finalEarned'] == 2 and nan['status'] == 'terminal-policy-dispatch'
    fractional = so.outcome(dict(verdict=1, rewardOutcome=dict(awardedChests=None, pendingChests=2.5)),
                            policy='on-verdict')
    assert fractional['pending'] is None and fractional['finalEarned'] is None
    assert fractional['status'] == 'win-unproven'
    frac_award = so.outcome(dict(verdict=1, rewardOutcome=dict(awardedChests=1.5,
                                                               awardedBasis='reward-entitlement-certificate',
                                                               pendingChests=1)))
    assert frac_award['awardedReported'] is None and frac_award['status'] == 'win-unproven'
    checks['invalidCounts'] = True

    # 5b. Simulation timing and the consumable policy/uses travel with the outcome as available.
    carried = so.outcome(dict(verdict=1, ticks=1234, timing={'wallSeconds': 0.5}, resourceUses=2,
                              consumablePolicy={'holyHerb': 'on'}, finishPolicy='on-verdict',
                              rewardOutcome=dict(pendingChests=1)))
    assert carried['ticks'] == 1234 and carried['timing'] == {'wallSeconds': 0.5}
    assert carried['consumablePolicy'] == {'holyHerb': 'on'}
    assert carried['costs']['resourceUses'] == 2
    checks['timingAndConsumables'] = True

    # 6. The nested raw-entitlement path reads the same way as the adapter path.
    nested = dict(verdict=1, result=dict(verdict=1, rewardEntitlement=dict(
        awardedChestCount=4, awardedChestCountBasis='reward-entitlement-certificate', pendingChestCount=4)))
    row = so.outcome(nested)
    assert row['status'] == 'certified' and row['finalEarned'] == 4
    checks['nestedEntitlement'] = row['finalEarned']

    # 7. The canonical row carries the FULL declared policy (not only finishPolicy) for boundary checks.
    policy = {'finishPolicy': 'on-verdict', 'simulator': {'skillPolicy': 'greedy'}}
    carried = so.outcome(_win_basis('unknown-win-without-certificate', None, 2), policy=policy)
    assert carried['policy'] == policy and carried['finishPolicy'] == 'on-verdict'
    assert carried['status'] == 'terminal-policy-dispatch'
    checks['fullPolicyCarried'] = True
    return checks


def summarization():
    checks = {}
    rows = [
        so.outcome(_win_basis('reward-entitlement-certificate', 2, 2)),
        so.outcome(_win_basis('reward-entitlement-certificate', 0, 0)),
        so.outcome(dict(verdict=2, rewardOutcome=dict(awardedChests=0, pendingChests=1))),
        so.outcome(dict(verdict=None, rewardOutcome=dict(pendingChests=1))),
    ]
    report = so.summarize(rows, thresholds=(1, 2))
    assert report['total'] == 4 and report['numericCount'] == 3 and report['unresolvedCount'] == 1
    assert report['denominators'] == dict(attempted=4, resolved=3, lost=1, unresolved=1, errors=0,
                                          censored=0)
    # Partial mean over the 3 resolved; no full claim while an outcome is unresolved.
    assert abs(report['partialMean'] - (2 + 0 + 0) / 3.0) < 1e-12
    assert report['fullClaim']['complete'] is False and report['fullClaim']['meanEarned'] is None
    assert report['eligibleForRecommendation'] is False
    assert report['zeroCount'] == 2 and report['lossCount'] == 1
    checks['partialVsFull'] = dict(partial=report['partialMean'], full=report['fullClaim']['meanEarned'])
    checks['thresholds'] = report['thresholds']

    # All-resolved cohort is eligible and equals the partial mean.
    resolved = rows[:3]
    good = so.summarize(resolved)
    assert good['fullClaim']['complete'] is True and good['eligibleForRecommendation'] is True
    assert abs(good['fullClaim']['meanEarned'] - good['partialMean']) < 1e-12
    checks['resolvedEligible'] = good['fullClaim']['meanEarned']

    # Empty cohort is never recommended.
    empty = so.summarize([])
    assert empty['eligibleForRecommendation'] is False and empty['partialMean'] is None
    checks['emptyNotEligible'] = True

    # No fake favourable cohort: a hungry win hidden behind a censored/errored run must not recommend.
    hidden = [
        so.outcome(_win_basis('reward-entitlement-certificate', 500, 500)),
        so.outcome(dict(verdict=1, censored=True, rewardOutcome=dict(pendingChests=1))),
    ]
    hungry = so.summarize(hidden)
    assert hungry['eligibleForRecommendation'] is False
    assert hungry['fullClaim']['meanEarned'] is None and hungry['censoredCount'] == 1
    errored = so.summarize([_win_basis('reward-entitlement-certificate', 500, 500),
                            dict(status='error', error=True, finalEarned=None, costs={})])
    assert errored['eligibleForRecommendation'] is False
    checks['noFakeFavourableCohort'] = True

    # Jackpot diagnostic: one sample dominating the total.
    jackpot = so.summarize([so.outcome(_win_basis('reward-entitlement-certificate', 100, 100)),
                            so.outcome(_win_basis('reward-entitlement-certificate', 0, 0))])
    assert jackpot['diagnostics']['jackpotSensitive'] is True
    checks['jackpotSensitive'] = jackpot['diagnostics']['top1ShareOfTotal']
    return checks


def main():
    report = dict(module='strategy_outcomes',
                  edgeCases=reward_edge_cases(),
                  summarization=summarization(),
                  limits=['Reading rules only; no simulator was run.',
                          'A certificate is neither necessary nor sufficient pre-verdict for the '
                          'terminal-policy measure; that measure needs the explicit on-verdict policy.'])
    print(json.dumps(report))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
