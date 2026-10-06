"""Ending / rewards / event-export regression contract for the generated battle replay.

Static + model only; no native execution. Pins, on the authoritative runner's own output, the four
things the viewer depends on:

 1. ENDING CUT - the export separates the engine's Verdict/Ending from the declared-Finish tail.
    `endingTick` is the Ending entry, `endingCutTick` is the last simulated tick (the losing side's
    knock-down/departure animation is kept), and the diagnostic Finish emissions at the horizon are
    outside the cut. `finalState.ticks` stays the engine horizon.
 2. KO + REVIVE WINDOW - the verdict is not "last HP 0". A knocked-down member (state 7, HP 0) still
    inside the revive window, or a revived member (state 2), keeps the team open; the verdict fires
    the tick after the LAST member reaches Leaving (state 8).
 3. REWARD ENTITLEMENT / DISPATCH BOUNDARY - a loss dispatches zero chests regardless of the queue;
    a win's awarded count is the pre-verdict certificate; a chest_dispatch is a diagnostic ENTITY
    boundary at the horizon, never collected inventory or a validated horizon reward.
 4. EVENT EXPORT - the viewer's inputs exist: `attack` (hpBefore/hpAfter/damage), `mp` (before/after/
    amount for the skill spend) and `release` (used + skillId for the real release bubble).

See `RE-evidence/20260920-battle-regression-pass/REGRESSION-CONTRACT.md` for the visuals worker's side
and `RESULT-combat.md` for the automatic-Finish probe decision.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from check_combat_finish_policy import loss_fixture
from check_combat_legal_skills import revive_scenario, winning_scenario
from combat_ending import enter_ending, is_annihilated
from combat_replay_export import export_replay
from combat_resolution import apply_fighter_cure_results, cure_result
from combat_run_manifest import NATIVE_SHA256

OUT = (Path(__file__).resolve().parents[3] / 'RE-evidence' / '20260920-battle-regression-pass'
       / 'battle-regression-checks.json')
FINISH_PHASE = 'finish'
CUT_FIELDS = ('endingTick', 'endingCutTick', 'horizonTick', 'postEndingTicks', 'silentTailTicks',
              'finishPhaseEvents', 'finishPhaseFirstTick', 'endingCutReason')


def team(*states):
    return [dict(board={5: state}) for state in states]


def check_revive_window():
    """KO -> revive vs departure: only a whole team in Leaving state 8 ends the fight."""
    assert is_annihilated(team(8, 8)) is True
    assert is_annihilated(team(8, 7)) is False, 'knocked-down HP0 member still blocks annihilation'
    assert is_annihilated(team(8, 2)) is False, 'a revived member blocks annihilation'
    assert is_annihilated(team(8, 0)) is False
    # Ending resets only units outside 7/8, so a revivable member keeps its window after the call.
    teams = [team(8, 7), team(8)]
    resets = []

    def change(unit, state):
        resets.append(state)
        unit['board'][5] = state

    assert enter_ending(teams, change) == 1, 'team 0 is not annihilated while a member is in state 7'
    assert teams[0][1]['board'][5] == 7 and resets == [], 'the revivable member is not reset'
    # A type-15 Cure payload revives a knocked-down member to state 2 (the window is used).
    hp = [0]
    states = []
    payload = cure_result(caster=1, target=2, skill=dict(type=15, value=100), effective_hp_maximum=200)
    apply_fighter_cure_results([payload], lambda target: True,
                               lambda target, amount: hp.__setitem__(0, hp[0] + amount),
                               lambda target: (-24.75, 96.5), lambda target, point: None,
                               lambda target, state: states.append(state))
    assert hp == [200] and states == [2], (hp, states)
    # Without a reachable queue position the revive does NOT run and the member stays knocked down.
    states = []
    apply_fighter_cure_results([payload], lambda target: True, lambda target, amount: None,
                               lambda target: None, lambda target, point: None,
                               lambda target, state: states.append(state))
    assert states == [], 'a revive needs a reachable queue position'


def check_run(label, scenario, verdict):
    replay = export_replay(scenario, include_events=True)
    state, events = replay['finalState'], replay['events']
    for field in CUT_FIELDS:
        assert field in state, (label, 'missing finalState.%s' % field)
    assert state['verdict'] == verdict and state['censored'] is False, (label, state['verdict'])
    ending_tick, cut, horizon = state['endingTick'], state['endingCutTick'], state['horizonTick']
    assert ending_tick == state['verdictTick'] and ending_tick is not None, label
    assert state['ticks'] - 1 == horizon, (label, state['ticks'], horizon)
    assert ending_tick <= cut <= horizon, (label, ending_tick, cut, horizon)
    assert state['postEndingTicks'] == cut - ending_tick, label
    assert state['silentTailTicks'] == horizon - cut, label
    # The declared-Finish tail (diagnostic chest dispatch + its RNG) is not battle time.
    finish_ticks = [event['tick'] for event in events if event['phase'] == FINISH_PHASE]
    assert state['finishPhaseEvents'] == len(finish_ticks), label
    assert all(finish_tick > cut for finish_tick in finish_ticks), (label, finish_ticks, cut)
    assert not [event for event in events if event['phase'] != FINISH_PHASE and event['tick'] > cut], label
    # The verdict is driven by the losing team's Leaving conversion, not its last HP 0.
    losing = {unit['unitId'] for unit in replay['units'] if unit['side'] == ('enemy' if verdict == 1 else 'ally')}
    first_leaving, first_zero = {}, {}
    for event in events:
        target = event.get('targetUnitId')
        if target not in losing:
            continue
        if event['kind'] == 'state' and event.get('new') == 8:
            first_leaving.setdefault(target, event['tick'])
        if event['kind'] == 'attack' and event.get('hpAfter') == 0:
            first_zero.setdefault(target, event['tick'])
    assert first_leaving and set(first_leaving) == losing, (label, len(first_leaving), len(losing))
    last_leaving = max(first_leaving.values())
    assert ending_tick == last_leaving + 1, (label, ending_tick, last_leaving)
    assert any(first_zero.get(unit) is not None and first_zero[unit] < first_leaving[unit]
               for unit in losing), (label, 'no KO -> departure window observed')
    # Reward entitlement vs the diagnostic dispatch.
    entitlement = state['rewardEntitlement']
    if verdict == 1:
        assert entitlement['awardedChestCount'] == entitlement['pendingChestCount'] > 0, label
        assert entitlement['awardedChestCountBasis'] == 'reward-entitlement-certificate', label
        certificate = entitlement['certificate']
        assert certificate['issuedBeforeVerdict'] is True and certificate['frame'] < ending_tick, label
        assert entitlement['postCertificateQueueChanged'] is False, label
    else:
        assert entitlement['awardedChestCount'] == 0, label
        assert entitlement['awardedChestCountBasis'] == 'native-win-loss-gate', label
    dispatches = [event['tick'] for event in events if event['kind'] == 'chest_dispatch']
    assert all(tick > cut for tick in dispatches), (label, dispatches, cut)
    # Event contract the visuals consume.
    attacks = [event for event in events if event['kind'] == 'attack']
    releases = [event for event in events if event['kind'] == 'release']
    assert attacks and all({'hit', 'hpBefore', 'hpAfter', 'damage'} <= set(event) for event in attacks), label
    assert releases and all(event['skillId'] is not None and 'used' in event for event in releases), label
    assert any(event['used'] for event in releases), label
    spends = [event for event in events if event['kind'] == 'mp']
    if verdict == 1:
        assert spends, label
    assert all(event['after'] <= event['before'] and event['amount'] >= 0 for event in spends), label
    return dict(label=label, verdict=verdict, ticks=state['ticks'], endingTick=ending_tick,
                endingCutTick=cut, horizonTick=horizon, postEndingTicks=state['postEndingTicks'],
                silentTailTicks=state['silentTailTicks'], finishPhaseEvents=state['finishPhaseEvents'],
                prizeCallbacks=state['prizeCallbacks'],
                pendingChests=entitlement['pendingChestCount'],
                awardedChests=entitlement['awardedChestCount'],
                awardedBasis=entitlement['awardedChestCountBasis'],
                dispatchedChests=None if replay['finish'] is None else replay['finish']['dispatchedChestAwards']['count'],
                attacks=len(attacks), releases=len(releases), mpSpends=len(spends))


def main():
    check_revive_window()
    win = check_run('win', winning_scenario(), 1)
    loss = check_run('loss', loss_fixture(ticks=1000), 2)
    # Compact defeat with a real knock-down/departure window (the legal Knight plus a Revive-100%
    # ally): the fight does not end at the first HP 0, and the kept post-Ending window is non-empty.
    defeat = check_run('revive-roster-defeat', revive_scenario(), 2)
    # The win's own silent tail is what the viewer used to play: the cut must remove it.
    assert win['silentTailTicks'] > 0 and win['endingCutTick'] > win['endingTick']
    assert win['dispatchedChests'] == win['pendingChests'], win
    assert loss['dispatchedChests'] == 0 and loss['prizeCallbacks'] == 0, loss
    assert defeat['endingCutTick'] > defeat['endingTick'] and defeat['silentTailTicks'] > 0, defeat
    assert defeat['awardedChests'] == 0 and defeat['dispatchedChests'] == 0, defeat
    report = dict(schema='ka-battle-regression-check-1', nativeSha256=NATIVE_SHA256,
                  reviveWindow='only a whole team in Leaving state 8 ends the fight; state 7 (revive '
                               'window) and state 2 (revived) keep it open',
                  runs=[win, loss, defeat])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(dict(win=dict(endingTick=win['endingTick'], endingCutTick=win['endingCutTick'],
                                   horizonTick=win['horizonTick'], silentTailTicks=win['silentTailTicks'],
                                   awarded=win['awardedChests']),
                          loss=dict(endingTick=loss['endingTick'], awarded=loss['awardedChests']),
                          defeat=dict(endingTick=defeat['endingTick'], endingCutTick=defeat['endingCutTick'],
                                      silentTailTicks=defeat['silentTailTicks'], awarded=defeat['awardedChests']),
                          output=str(OUT))))


if __name__ == '__main__':
    main()
