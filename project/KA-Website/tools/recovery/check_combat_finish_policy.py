"""Finish policy semantics: a declared INPUT (player Ending confirmation), not a quiescence claim.

Corrected 2026-09-20; supersedes the earlier `END_SETTLE_TICKS = 8` settlement claim, which was an
empirical heuristic driving `yieldTrusted` with no proof that future prize producers were exhausted
and which ignored projectiles / Damaging / Leaving callbacks.
  * Native Ending does not clear retained commands and `update_fighters` keeps running while
    `battle_state>=2`, so post-verdict Leaving/re-entry callbacks can still award prizes before a
    Finish. `on-verdict` therefore truncates native-eligible chests and stays diagnostic/unranked.
  * The Ending gate is counter-gated (`combat_ending.update_ending`): ONE pulse at frame>79 requests
    Finish/EXP, so a single press finishes when the counter is already past 79. Two presses are needed
    only when the first lands at frame<=79 (it writes 80 with no transition). There is no settlement
    window and no "window opens at tick 80 and no earlier" rule - an early press itself raises the
    counter, so the next press can finish before that tick.
  * The declared input policy is at-horizon (legacy default: run the full horizon, then request ONE
    Ending confirmation) or after-ending (wait for the counter to pass 79 / wait-until-frame-80, then
    request ONE confirmation). Neither waits for command queues to empty nor claims the game does.
  * The fixture is a self-contained deterministic encounter that actually reaches a WIN verdict
    (derived from the legal validated fixture), not the stale UREF build; the name `UREF` is no
    longer referenced. Every dispatched count stays a labelled DIAGNOSTIC: `autoFinishProducerProven`
    is False and `yieldTrusted` is False for every policy (fail closed) until the native automatic
    KEY_SELECT producer is recovered. A separate deterministic witness whose Leaving callbacks keep
    queueing prizes AFTER the verdict shows `on-verdict` dispatches strictly fewer than `at-horizon`.
"""
import json
import sys
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import combat_sandbox
from combat_ending import ENDING_CONFIRM_FRAME, after_ending_confirmation, update_ending
from combat_evaluation import _finish_blockers, evaluate
from combat_initial_state import EVIDENCE
from combat_prizes import special_prize_candidates
from combat_receipt import ReceiptLedger
from combat_replay_export import export_replay
from combat_runtime_data import load_data
from combat_sandbox import finish_lifecycle, queued_prizes, run_scenario
from combat_shared_controllers import SharedControllers
from check_combat_finish_dispatch import drive
from check_combat_legal_skills import ally, legal_scenario, winning_scenario
from check_combat_sandbox import human


def winning_fixture(policy=None, ticks=None):
    """A self-contained deterministic encounter that actually reaches a win verdict.

    Derived from the legal validated fixture (`check_combat_legal_skills.winning_scenario`): the legal
    Knight is made able to annihilate encounter 19. This replaces the stale UREF scenario build; it is
    a deterministic encounter for the finish-policy semantics, not the player build or an optimal setup.
    """
    data = winning_scenario()
    if policy is not None:
        data['finishPolicy'] = policy
    if ticks is not None:
        data['tickLimit'] = ticks
    return data


def loss_fixture(policy='at-horizon', ticks=1000):
    """The same legal encounter with one HP-1 unit: deterministic team-0 annihilation (verdict 2)."""
    data = legal_scenario()
    data['ownUnits'] = [ally('finish-policy loss probe', [], {10: 1, 11: 1})]
    data['inputs'] = []
    data['finishPolicy'] = policy
    data['tickLimit'] = ticks
    return data


def post_verdict_prize_run(policy):
    """Deterministic witness whose Leaving callbacks keep awarding prizes AFTER the verdict.

    Uses the same SharedControllers loop `run_scenario` uses. `on-verdict` breaks at the verdict tick
    boundary, so prizes the same encounter keeps producing afterwards are truncated; `at-horizon` runs
    the full horizon and keeps every queued prize. Returns `(queued, dispatched)`.
    """
    specs = [dict(human(f'finish-policy prize probe {team}', [], {10: 100, 11: 100, 14: 10, 15: 10}),
                  team=team, grid=team, cell=[team, team], levels=[], human=team == 0, boss=team == 1)
             for team in (0, 1)]
    engine = SharedControllers(specs, 7, 8, prize_candidates=special_prize_candidates(19),
                               receipts=ReceiptLedger())
    boss = engine.teams[1][0]
    engine.param(boss['id'], 10)['rawValue'] = 0
    engine.change(boss, 8)  # one prize queued before the verdict

    def inject(world, phase):
        if world.battle_state == 3 and phase == 'before_fighters' and len(world.prizes) < 4:
            world.change(boss, 8)  # post-verdict Leaving callbacks keep awarding prizes

    engine.run(120, inject, finish_policy=policy)
    queued = len(queued_prizes(engine))
    _, dispatched = finish_lifecycle(engine, engine.verdict, policy)
    return queued, len(dispatched)


def policy_report(data):
    report = run_scenario(data)
    result, finish = report['result'], report['finish']
    return dict(verdict=result['verdict'], censored=result['censored'],
                verdictTick=result['verdictTick'], finishTick=result['finishTick'],
                finishPolicy=result['finishPolicy'], stopReason=result['stopReason'],
                finishBoundary=result['finishBoundary'], yieldTrusted=result['yieldTrusted'],
                rewardsTruncated=result['rewardsTruncated'], endingGateTick=result['endingGateTick'],
                endingConfirmed=result['endingConfirmed'], endingCounter=result['endingCounter'],
                pendingActivity=result['pendingActivity'], lastPrizeTick=result['lastPrizeTick'],
                queued=None if finish is None else finish['queuedChestAwards']['count'],
                dispatched=None if finish is None else finish['dispatchedChestAwards']['count'],
                prizeCallbacks=result['prizeCallbacks'])


def main():
    # 1) recovered Ending gate, and no arbitrary settlement threshold anywhere
    assert not hasattr(combat_sandbox, 'END_SETTLE_TICKS')
    assert update_ending(79, True, False, 1) == (80, None)     # early press only writes 80
    assert update_ending(80, True, False, 1) == (80, 'finish')  # ONE press at frame>79 finishes
    assert after_ending_confirmation(79, 1) == (80, None)
    assert after_ending_confirmation(80, 1) == (80, 'finish')
    counter, transition = after_ending_confirmation(79, 1)      # two presses only from frame<=79
    assert (counter, transition) == (80, None)
    assert after_ending_confirmation(counter, 1) == (80, 'finish')

    # 2) deterministic legal-derived encounter: the diagnostic cut truncates, the declared input keeps
    #    every queued prize, and no count is ever a trusted yield.
    horizon = policy_report(winning_fixture('at-horizon', 3000))
    early = policy_report(winning_fixture('on-verdict', 3000))
    gated = policy_report(winning_fixture('after-ending', 3000))
    far = policy_report(winning_fixture('at-horizon', 5000))
    verdict_tick = horizon['verdictTick']
    assert verdict_tick is not None
    # The horizon runs one tick below its tickLimit, so counter 79 lands at tickLimit verdict_tick+80
    # and counter 80 (the single-press Finish) at verdict_tick+81.
    frame79 = policy_report(winning_fixture('at-horizon', verdict_tick + ENDING_CONFIRM_FRAME))
    frame80 = policy_report(winning_fixture('at-horizon', verdict_tick + ENDING_CONFIRM_FRAME + 1))
    assert early['verdict'] == gated['verdict'] == horizon['verdict'] == 1
    assert early['verdictTick'] == verdict_tick and early['finishTick'] == early['verdictTick']
    assert early['queued'] == early['dispatched'] == early['prizeCallbacks']
    assert early['finishBoundary'] == 'diagnostic-truncated' and early['rewardsTruncated'] is True
    assert early['yieldTrusted'] is False
    assert horizon['queued'] == horizon['dispatched'] == horizon['prizeCallbacks']
    assert horizon['rewardsTruncated'] is False and horizon['yieldTrusted'] is False
    assert gated['queued'] == gated['dispatched'] == horizon['queued']
    # after-ending confirms at the recovered gate, not instantly and not by waiting for quiescence
    assert gated['endingGateTick'] == gated['finishTick'] == early['verdictTick'] + ENDING_CONFIRM_FRAME
    assert gated['endingConfirmed'] is True and gated['endingCounter'] == ENDING_CONFIRM_FRAME
    # FAIL CLOSED: the boundary records that a declared cut finished the fight, but no automatic
    # native producer of the Ending KEY_SELECT edge is proven, so the count is never a trusted yield.
    assert gated['finishBoundary'] == 'diagnostic-declared-finish; native-auto-producer-unproven'
    assert horizon['finishBoundary'] == 'diagnostic-declared-finish; native-auto-producer-unproven'
    assert gated['endingConfirmed'] is True and gated['yieldTrusted'] is False
    # horizons 3000 and 5000 agree on this one case (empirical stability, not a general saturation proof)
    assert horizon['queued'] == far['queued']
    assert horizon['lastPrizeTick'] == far['lastPrizeTick'] and horizon['lastPrizeTick'] < verdict_tick
    # frame 79 single press -> counter 80, no Finish (0 dispatched); frame 80 single press -> Finish
    assert frame80['endingCounter'] == ENDING_CONFIRM_FRAME and frame80['endingConfirmed'] is True
    assert frame80['finishBoundary'] == 'diagnostic-declared-finish; native-auto-producer-unproven'
    assert frame80['dispatched'] == frame80['queued'] == horizon['queued']
    assert frame80['yieldTrusted'] is False
    assert frame79['endingCounter'] == ENDING_CONFIRM_FRAME and frame79['endingConfirmed'] is False
    assert frame79['finishBoundary'] == 'awaiting-confirmation' and frame79['dispatched'] == 0
    assert frame79['yieldTrusted'] is False and frame79['queued'] == horizon['queued']

    # 3) legacy default when finishPolicy is unset
    legacy = policy_report({k: v for k, v in winning_fixture().items() if k != 'finishPolicy'})
    assert legacy['finishPolicy'] == 'at-horizon' and legacy['queued'] == legacy['dispatched']
    assert legacy['yieldTrusted'] is False

    # 4) strict truncation witness: this deterministic encounter keeps queueing prizes after the
    #    verdict, so the on-verdict cut is strictly below the full-horizon run (unequal, not labelled).
    trunc_early_q, trunc_early_d = post_verdict_prize_run('on-verdict')
    trunc_horizon_q, trunc_horizon_d = post_verdict_prize_run('at-horizon')
    assert trunc_early_q == trunc_early_d and trunc_horizon_q == trunc_horizon_d
    assert trunc_early_q < trunc_horizon_q and trunc_early_d < trunc_horizon_d

    # 5) a losing verdict dispatches nothing and never fabricates a receipt (no prize producer)
    loss = policy_report(loss_fixture())
    assert loss['verdict'] == 2 and loss['dispatched'] == 0 and loss['queued'] == 0
    assert loss['finishBoundary'] == 'diagnostic-declared-finish; native-auto-producer-unproven' and loss['yieldTrusted'] is False

    # 6) unresolved: censored at the horizon for every policy, Finish never guessed
    for policy in ('on-verdict', 'after-ending', 'at-horizon'):
        unresolved = policy_report(winning_fixture(policy, 100))
        assert unresolved['censored'] is True and unresolved['dispatched'] is None
        assert unresolved['finishTick'] is None and unresolved['finishBoundary'] == 'censored-unresolved-battle'
        assert unresolved['yieldTrusted'] is False

    # 7) a short at-horizon is awaiting-confirmation: no dispatched count is fabricated and it is not a
    #    censored lower bound derived from an arbitrary settlement window
    short = policy_report(winning_fixture('at-horizon', verdict_tick + 10))
    assert short['censored'] is False and short['finishBoundary'] == 'awaiting-confirmation'
    assert short['queued'] == horizon['queued'] and short['dispatched'] == 0 and short['yieldTrusted'] is False

    # 8) a legally confirmed finish reports pending activity instead of asserting settlement: a
    #    declared confirmation past the gate finishes even with a retained command queued
    engine, _ = drive(prize_count=4)
    own = engine.teams[0][0]
    engine.units[own['id']]['commands'].append(dict(skill=0, index=0, tick=-1, kind='declared'))
    engine.battle_frame = 100
    confirmed_run = engine.run(0, finish_policy='at-horizon')
    assert confirmed_run['endingConfirmed'] is True and confirmed_run['unresolvedCommands'] == 1
    _, pending_dispatched = finish_lifecycle(engine, engine.verdict)
    assert len(pending_dispatched) == 4

    # 9) FAIL CLOSED: with no proven automatic producer, BOTH declared policies are unranked - the
    #    at-horizon count is a diagnostic cut, and on-verdict additionally truncates post-verdict prizes.
    request = dict(schema='ka-battle-eval-1', levels=[dict(encounterId=19, defeatCount=0)], seeds=[[7, 8]],
                   tickLimit=700, limits=dict(maxRuns=8, maxTicks=10000, maxSeeds=8, maxCandidates=8),
                   candidates=[dict(id='at-horizon', scenario=winning_fixture('at-horizon')),
                               dict(id='on-verdict', scenario=winning_fixture('on-verdict'))])
    out = evaluate(request)
    ranked = [entry['id'] for entry in out['rankings']['pooled']['ranked']]
    unranked = {entry['id']: entry['reasons'] for entry in out['rankings']['pooled']['unranked']}
    assert ranked == [], ranked
    assert set(unranked) == {'at-horizon', 'on-verdict'}, unranked
    assert any('rewards truncated' in reason or 'truncates native post-verdict' in reason
               for reason in unranked['on-verdict']), unranked['on-verdict']
    assert any('no automatic native producer' in reason for reason in unranked['at-horizon']), unranked['at-horizon']
    unproven_reasons = _finish_blockers(dict(policy='at-horizon', yieldEligible=False, trustedYield=False))
    assert unproven_reasons and 'no automatic native producer' in unproven_reasons[0], unproven_reasons
    awaiting_reasons = _finish_blockers(dict(policy='at-horizon', yieldEligible=True, trustedYield=False))
    assert awaiting_reasons and 'awaiting-confirmation' in awaiting_reasons[0], awaiting_reasons
    assert _finish_blockers(dict(policy='at-horizon', yieldEligible=True, trustedYield=True)) == []  # only a proven producer may clear this
    assert _finish_blockers(dict(policy='after-ending', yieldEligible=False, trustedYield=True)) != []  # fail closed

    # 10) declared-profile round trip, and the export carries finish-policy eligibility and boundary
    skills = {row['id']: row for row in load_data('weapon-skill-profiles.json')['skills']}
    status_skill = next(row['id'] for row in skills.values() if row['type'] in (66, 67))
    profiled = deepcopy(legal_scenario())
    profiled['finishPolicy'] = 'at-horizon'
    profiled['startProfile'] = dict(kind='isolated-scene0', enemySpawnCell=[1, 1], bossCell=[7, 9],
                                    startingStatus={profiled['ownUnits'][0]['name']: [status_skill, 2]})
    run = run_scenario(deepcopy(profiled))
    support = run['manifest']['support']
    # FAIL CLOSED: the declared profile itself is accepted and modelled, but the run cannot be a
    # supported conditional simulation while the automatic Finish producer is unproven.
    assert support['conditionalSimulation']['unmetConditions'] == ['yield_eligible_finish_policy']
    conditions = {c['name']: c['met'] for c in support['conditionalSimulation']['conditions']}
    assert conditions['scene0_special_battle'] is True and conditions['source_state'] is True
    assert conditions['yield_eligible_finish_policy'] is False
    assert support['sourceState']['profile'] == profiled['startProfile']
    assert support['finishPolicy']['yieldEligible'] is False
    assert support['finishPolicy']['autoFinishProducerProven'] is False
    replay = export_replay(profiled)
    summary = replay['setupSummary']
    assert summary['startProfile'] == profiled['startProfile'], summary['startProfile']
    assert summary['finishPolicy'] == 'at-horizon' and summary['finishPolicyEligible'] is True
    assert summary['prePlacement'] == []
    assert 'pendingActivity' in replay['finalState'] and 'endingConfirmed' in replay['finalState']
    diagnostic = deepcopy(profiled); diagnostic['finishPolicy'] = 'on-verdict'
    diag_replay = export_replay(diagnostic)
    assert diag_replay['setupSummary']['finishPolicyEligible'] is False
    assert diag_replay['finalState']['rewardsTruncated'] is True
    assert diag_replay['finalState']['yieldTrusted'] is False

    report = dict(schema='ka-combat-finish-policy-checks-2', gate=dict(frame=ENDING_CONFIRM_FRAME),
                  onVerdict=early, afterEnding=gated, horizon=horizon, horizon5000=far,
                  frame80=frame80, frame79=frame79, legacyDefault=legacy,
                  truncation=dict(onVerdict=dict(queued=trunc_early_q, dispatched=trunc_early_d),
                                  atHorizon=dict(queued=trunc_horizon_q, dispatched=trunc_horizon_d)),
                  loss=loss, shortHorizon=short, legallyConfirmedWithPending=dict(
                      endingConfirmed=confirmed_run['endingConfirmed'],
                      unresolvedCommands=confirmed_run['unresolvedCommands'],
                      dispatched=len(pending_dispatched)),
                  ranking=dict(ranked=ranked, unrankedReasons=unranked))
    (EVIDENCE / 'finish-policy-checks.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: report[k] for k in ('horizon', 'onVerdict', 'afterEnding', 'frame80', 'frame79')}))


if __name__ == '__main__':
    main()
