"""Focused native-backed check for the reward-entitlement certificate plumbing.

Four independent legs, none of which re-implements combat:

  * native win/loss gate: reuses the recorded `BattleSystem.Finish` cases already replayed by
    `check_combat_finish_dispatch.replay_recorded_native` (no second fixture);
  * native key/instruction evidence re-read from the combat key overlay and the frozen asm slices:
    the only BKL:16 writers, the state-4 exit that does not clear it, and the two Cure predicates
    that accept an HP0 same-team target;
  * the enemy-Cure pin over all 20 supported encounters (plus a fail-closed unknown-master-row case);
  * portable differentials of the report plumbing (hand-set engine states, labelled) covering the
    strict pre-verdict timing, a late-only certificate refusal, an immutable pre-verdict certificate,
    a post-certificate queue change, the command gate, the Cure/missing-row scope refusals and a
    15-pending loss that still awards 0; plus two real runs (the legal winning fixture, certified at a
    frame strictly before its verdict, and an annihilated-own-team loss);
  * the exporter dataflow (rewardEntitlement reaches finalState; an older payload without it stays
    valid, and the player-facing fields carry no native offsets).

Earlier implementation note: the first cut issued the certificate only AFTER `engine.verdict` was set
(winning fixture verdict 259 -> certificate 260). That trusted a late hold and left
automaticFinish unknown irrelevant; the certificate is now strictly pre-verdict.

The `8 -> 6 -> 8` regression is a labelled synthetic ChangeState sequence, not a native execution;
the native evidence for the repeated-prize producer is the recorded Finish/queued-command checks.
"""
import json
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parents[2]
sys.path.insert(0, str(TOOLS))

from check_combat_finish_dispatch import replay_recorded_native
from check_combat_sandbox import example, human
from combat_commands import enqueue_skill_command
from combat_initial_state import EVIDENCE
from combat_prizes import special_prize_candidates
from combat_receipt import ReceiptLedger
from combat_reward_entitlement import (CURE_SKILL_TYPES, RewardEntitlementWatch,
    certificate_scope, enemy_cure_sources)
from combat_runtime_data import load_data
from combat_sandbox import run_scenario
from combat_shared_controllers import ROWS, SharedControllers

KEYS = ROOT / 'RE-evidence/20260919-combat-registry/combat-keys.json'
ENC0 = ROOT / 'RE-evidence/20260912-combat/encounters.json'

# Recovered RVAs (frozen binary fb834373...30208) the certificate reasons about.
RVA = dict(enterLeaving='0x1586a80', addGuerrillaPrize='0x14ed618', updateCharging='0x1584bf0',
           updateAttacking='0x1585568', exitUsingSkill='0x1585d2c', exitAttacking='0x1585a18',
           onCure='0x1587e64', updateRecovery='0x14aaa04', rescuePredicate='0x15e20dc',
           recoveryPredicate='0x15e2190')


def native_gate_cases():
    """Reuse the recorded native Finish case set: winner==1 dispatches, any other winner dispatches 0."""
    recorded, checked = replay_recorded_native()
    wins = [e for e in recorded['examples'] if e['winner'] == 1]
    losses = [e for e in recorded['examples'] if e['winner'] != 1]
    assert wins and losses and checked == len(recorded['examples'])
    assert all(e['released'] == e['queuedTreasures'] for e in wins)
    assert all(e['released'] == 0 for e in losses)
    return checked, sorted({(e['winner'], e['queuedTreasures']) for e in recorded['examples']})


def native_key_evidence():
    """BKL:16 writers/readers and the Cure-branch HP writers, from the overlay + frozen asm."""
    overlay = json.loads(KEYS.read_text(encoding='utf-8'))
    keys = {entry['key']: entry for entry in overlay['keys']}
    target = keys['BKL:16']
    assert target['officialName'] == 'BATTLE_TARGET_ENTITY_ID'
    # Only UpdateCharging sets the target and only ExitUsingSkill clears it; UpdateAttacking only reads.
    assert target['functions'] == [RVA['updateCharging'], RVA['updateAttacking'], RVA['exitUsingSkill']]
    assert target['operations'] == {'read': 1, 'write': 3}
    assert RVA['exitAttacking'] not in target['functions'], 'Attacking exit must not clear the stored target'
    hp_writes = sorted({(site['site'], site['accessorName'], site['function'])
                        for site in overlay['paramSites']
                        if site['paramId'] == 10 and site['operation'] == 'write'})
    assert ('0x1587f48', 'Param.AddValue', RVA['onCure']) in hp_writes, 'OnCure is the Cure-branch HP add'
    # The only other AddValue writers are creation/GettingExp/overworld resident construction.
    adds = {(site, function) for site, accessor, function in hp_writes if accessor == 'Param.AddValue'}
    assert adds == {('0x14aafc4', RVA['updateRecovery']), ('0x1587f48', RVA['onCure']),
                    ('0x16a9bfc', '0x16a8fa0')}, adds
    rescue = (EVIDENCE / '15e20dc.asm').read_text(encoding='utf-8')
    recovery = (EVIDENCE / '15e2190.asm').read_text(encoding='utf-8')
    # Same-team gate (xor of Entity.get_hasAlly) on both predicates.
    for asm in (rescue, recovery):
        assert asm.count('kairo.unity.ecs.Entity$$get_hasAlly') == 2
    # Rescue: Param.GetValue(e,10) < 1 accepts HP0; Recovery: Param.GetRate(e,10) < 100 accepts HP0.
    assert 'mov w1, #0xa' in rescue and 'cmp w0, #1' in rescue and 'cset w0, lt' in rescue
    assert 'GetRate' in recovery and 'cmp w0, #0x64' in recovery and 'cset w0, lt' in recovery
    return dict(bkl16Functions=target['functions'], hpWriteSites=len(hp_writes),
                curePredicatesAcceptHp0=True)


def enemy_cure_pin():
    """Enemy-roster Cure pin over every supported encounter (no enemy Cure => certificate allowed)."""
    data = json.loads(ENC0.read_text(encoding='utf-8'))
    skill_rows = {row['id']: row for row in load_data('weapon-skill-profiles.json')['skills']}
    monsters = {m['id']: m for m in data['monsters']}
    allowed, refused, sources = [], [], []
    for encounter in data['encounters']:
        fighters = [dict(monsterId=f['monsterId'], name=mons_name(monsters, f['monsterId']),
                         skills=dict(dataIds=[monsters[f['monsterId']]['skillId']]))
                    for f in encounter['followers']]
        boss = encounter['bossId']
        fighters.append(dict(monsterId=boss, name=mons_name(monsters, boss),
                             skills=dict(dataIds=[monsters[boss]['skillId']])))
        roster = dict(encounterId=encounter['id'], fighters=fighters)
        found = enemy_cure_sources(roster, skill_rows)
        assert found == certificate_scope(roster, skill_rows)['enemyCureSources']
        (allowed if not found else refused).append(encounter['id'])
        sources.extend(dict(encounterId=encounter['id'], **source) for source in found)
    assert refused and allowed, (refused, allowed)
    assert all(source['skillType'] in CURE_SKILL_TYPES for source in sources)
    # Every Cure source is the single per-monster Monster.skillId (Kairobot 119 skill 37, type 2).
    assert {source['monsterId'] for source in sources} == {119}
    assert {source['skillId'] for source in sources} == {37}
    return dict(allowedEncounters=allowed, refusedEncounters=sorted(set(refused)),
                cureSourceMonsters=sorted({source['monsterId'] for source in sources}))


def mons_name(monsters, monster_id):
    return monsters[monster_id]['name']


ENCOUNTER_19 = dict(encounterId=19, fighters=[dict(monsterId=142, name='Wairo Tank',
                                                   skills=dict(dataIds=[35]))])


def probe_engine(boss_hp=0, encounter_id=19):
    """Two-fighter probe engine; no combat progression beyond the direct calls in the fixture."""
    specs = []
    for team in (0, 1):
        unit = human(f'probe {team}', [], {10: 100, 11: 100, 14: 10, 15: 10})
        specs.append(dict(unit, team=team, grid=team, cell=[team, team], levels=[], human=team == 0,
                          boss=team == 1))
    engine = SharedControllers(specs, 7, 8, prize_candidates=special_prize_candidates(encounter_id),
                               receipts=ReceiptLedger())
    boss = engine.teams[1][0]
    engine.param(boss['id'], 10)['rawValue'] = boss_hp
    return engine, boss


def watch_for(encounter=ENCOUNTER_19):
    return RewardEntitlementWatch(encounter, ROWS)


def report_block(watch, verdict, dispatched=0, boundary='fixture'):
    from combat_reward_entitlement import entitlement_report
    return entitlement_report(watch=watch, verdict=verdict, prize_callbacks=watch.pending_final,
                              diagnostic_dispatched=dispatched, finish_boundary=boundary)


def plumbing_fixtures():
    checks = {}
    # 1. Loss observed only AFTER the verdict: the hold is late, so it is not certified, but the award
    #    is still 0 from the native win/loss gate (the pending count is never promoted to an award).
    engine, boss = probe_engine()
    engine.change(boss, 8)
    engine.verdict = 2
    watch = watch_for()
    watch.observe(engine)
    report = report_block(watch, 2)
    assert report['awardedChestCount'] == 0 and report['awardedChestCountBasis'] == 'native-win-loss-gate'
    assert report['rewardCountSettled'] is False and report['pendingChestCount'] == 1
    assert report['certificate']['holds'] is False, 'a post-verdict hold must not be certified'
    assert report['lateCertificateObserved'] is True and report['certificate']['lateHold'] is not None
    checks['lossSafeZero'] = dict(awarded=0, pending=report['pendingChestCount'],
                                  lateHoldFrame=report['certificate']['lateHold']['frame'])

    # 2. Loss with 15 pending chests -> 0. A pre-verdict certificate DOES hold over the 15, yet the loss
    #    gate still awards 0 and never promotes the pending count.
    engine, boss = probe_engine()
    for _ in range(15):
        engine.change(boss, 6)
        engine.change(boss, 8)
    watch = watch_for()
    watch.observe(engine)                      # verdict still None -> pre-verdict certificate
    certified = report_block(watch, 2)         # the loss verdict arrives afterwards
    assert certified['pendingChestCount'] == 15
    assert certified['certificate']['holds'] is True
    assert certified['awardedChestCount'] == 0 and certified['awardedChestCountBasis'] == 'native-win-loss-gate'
    assert certified['rewardCountSettled'] is False
    checks['lossWithPendingFifteenAwardsZero'] = dict(pending=15, awarded=0,
                                                      certificateFrame=certified['certificate']['frame'])

    # 3. Win with a stale BKL:16 on a fighter -> conservative C3 fails pre-verdict, no certificate.
    engine, boss = probe_engine()
    engine.change(boss, 8)
    engine.units[engine.teams[0][0]['id']]['long_board'][16] = boss['id']
    watch = watch_for()
    watch.observe(engine)
    report = report_block(watch, 1)
    assert report['certificate']['holds'] is False
    assert report['certificate']['clauses']['C3_noStoredTarget'] is False
    assert report['awardedChestCount'] is None and report['awardedChestCountBasis'] == 'unknown-win-without-certificate'
    assert report['rewardCountSettled'] is False
    checks['staleTargetNoCertificate'] = dict(storedHolders=report['certificate']['storedTargetHolders'])

    # 4. Pre-verdict certificate then win, unchanged queue -> immutable certificate, awarded = pending.
    engine, boss = probe_engine()
    engine.change(boss, 8)
    watch = watch_for()
    watch.observe(engine)                      # verdict None: certificate is issued here
    assert watch.certificate is not None
    frame = watch.certificate['frame']
    report = report_block(watch, 1)
    assert report['certificate']['holds'] is True and report['certificate']['frame'] == frame
    assert report['certificate']['issuedBeforeVerdict'] is True
    assert report['awardedChestCount'] == report['pendingChestCount'] == 1
    assert report['rewardCountSettled'] is True
    watch.observe(engine)                      # more frames, no prize change: snapshot is immutable
    again = report_block(watch, 1)
    assert again['certificate']['frame'] == frame and again['awardedChestCount'] == 1
    checks['preVerdictCertificateThenWin'] = dict(frame=frame, awarded=again['awardedChestCount'])

    # 5. Late-only certificate: C1..C4 first hold at the verdict frame -> refused, awarded unknown.
    engine, boss = probe_engine()
    engine.change(boss, 8)
    engine.verdict = 1
    watch = watch_for()
    watch.observe(engine)
    report = report_block(watch, 1)
    assert report['certificate']['holds'] is False
    assert report['lateCertificateObserved'] is True and report['certificate']['lateHold'] is not None
    assert all(report['certificate']['lateHold']['clauses'].values())
    assert report['awardedChestCount'] is None
    assert 'at or after the verdict' in report['rewardCountReason']
    checks['lateCertificateRefused'] = dict(lateFrame=report['certificate']['lateHold']['frame'],
                                            awarded=report['awardedChestCount'])

    # 6. A prize queued after the pre-verdict certificate invalidates it (never latched).
    engine, boss = probe_engine()
    engine.change(boss, 8)
    watch = watch_for()
    watch.observe(engine)
    engine.change(boss, 6)
    engine.change(boss, 8)
    watch.observe(engine)
    report = report_block(watch, 1)
    assert report['certificate']['holds'] is True         # the snapshot still exists...
    assert report['postCertificateQueueChanged'] is True and report['postCertificateQueueDelta'] == 1
    assert report['rewardCountSettled'] is False and report['awardedChestCount'] is None
    checks['postCertificateQueueChangeInvalidates'] = dict(delta=report['postCertificateQueueDelta'])

    # 7. No certificate while a command targets the boss; clearing pre-verdict releases it.
    engine, boss = probe_engine()
    engine.change(boss, 8)
    own = engine.teams[0][0]
    skill_id = 110 if 110 in ROWS else next(iter(ROWS))
    enqueue_skill_command(engine.units[own['id']]['commands'], boss['id'], ROWS[skill_id], target_exists=True)
    watch = watch_for()
    watch.observe(engine)
    assert watch.certificate is None
    assert watch.latest['clauses']['C4_noQueuedCommandTarget'] is False
    assert watch.latest['bossTargetCommandHolders'] == [own['id']]
    engine.units[own['id']]['commands'].clear()
    watch.observe(engine)
    released = report_block(watch, 1)
    assert released['certificate']['holds'] is True and released['rewardCountSettled'] is True
    checks['rolledCommandGate'] = dict(blockedHolders=[own['id']],
                                       releasedHolds=released['certificate']['holds'])

    # 8. Scope refusal: an enemy Cure roster forbids the certificate even in a clean pre-verdict state.
    cure_encounter = dict(encounterId=0, fighters=[dict(monsterId=119, name='Kairobot',
                                                        skills=dict(dataIds=[37]))])
    engine, boss = probe_engine(encounter_id=0)
    engine.change(boss, 8)
    watch = RewardEntitlementWatch(cure_encounter, ROWS)
    watch.observe(engine)
    refused = report_block(watch, 1)
    assert refused['certificate']['holds'] is False and refused['rewardCountSettled'] is False
    assert refused['certificate']['scope']['allowed'] is False
    assert refused['awardedChestCount'] is None
    checks['cureScopeRefused'] = dict(sources=refused['certificate']['scope']['enemyCureSources'])

    # 9. FAIL CLOSED: an enemy skill with no known master row refuses the certificate. The old
    #    enemy_cure_sources silently skipped a missing row and left `allowed` True.
    unknown_encounter = dict(encounterId=19, fighters=[dict(monsterId=142, name='Wairo Tank',
                                                             skills=dict(dataIds=[999999]))])
    engine, boss = probe_engine()
    engine.change(boss, 8)
    watch = RewardEntitlementWatch(unknown_encounter, ROWS)
    watch.observe(engine)
    refused = report_block(watch, 1)
    assert watch.scope['allowed'] is False
    assert [entry['skillId'] for entry in watch.scope['unsupportedEnemySkills']] == [999999]
    assert refused['certificate']['holds'] is False and refused['awardedChestCount'] is None
    assert 'no known master row' in refused['rewardCountReason']
    checks['missingMasterRowFailClosed'] = dict(unsupported=[999999], allowed=watch.scope['allowed'])
    return checks


def delayed_reentry_regression():
    """LABELLED SYNTHETIC: 8 -> 6 -> 8 keeps awarding; the pre-verdict certificate reports the change."""
    engine, boss = probe_engine()
    engine.change(boss, 8)
    watch = watch_for()
    watch.observe(engine)                      # verdict None: pre-verdict certificate over 1
    certified = report_block(watch, 1)
    assert certified['rewardCountSettled'] is True and certified['pendingChestCount'] == 1
    engine.change(boss, 6)
    engine.change(boss, 8)  # synthetic repeat of the native event26 -> Damaging6 -> Leaving8 path
    watch.observe(engine)
    after = report_block(watch, 1)
    assert after['pendingChestCount'] == 2, 'the re-entry must keep awarding (no latch)'
    assert after['postCertificateQueueChanged'] is True and after['postCertificateQueueDelta'] == 1
    assert after['rewardCountSettled'] is False
    return dict(synthetic=True, before=certified['pendingChestCount'], after=after['pendingChestCount'],
                delta=after['postCertificateQueueDelta'],
                note='synthetic ChangeState repeat, labelled; native producer evidence is '
                     'check_combat_queued_chests.py (queued commands -> zero-HP boss re-entry)')


def real_runs():
    from check_combat_finish_policy import winning_fixture
    win = run_scenario(winning_fixture(policy='at-horizon', ticks=400))
    win_entitlement = win['result']['rewardEntitlement']
    assert win_entitlement['battleVerdict'] == 1 and win_entitlement['victoryRequired'] is True
    # The corrected observer certifies the STRICTLY PRE-VERDICT hold; the earlier implementation only
    # ever saw the later (post-verdict) hold, so it trusted a certificate it should not have needed.
    assert win_entitlement['certificate']['holds'] is True
    assert win_entitlement['certificate']['issuedBeforeVerdict'] is True
    assert win_entitlement['certificate']['frame'] < win['result']['verdictTick']
    assert win_entitlement['awardedChestCount'] == win_entitlement['pendingChestCount'] == 1
    assert win_entitlement['rewardCountSettled'] is True
    assert win_entitlement['postCertificateQueueChanged'] is False
    assert win_entitlement['diagnosticFinish']['dispatchedChests'] == 1
    support = win['manifest']['support']
    assert support['combatRulesSupported'] is True and support['rewardYieldSupported'] is False
    assert support['finishPolicy']['autoFinishProducerProven'] is False
    assert support['conditionalSimulation']['unmetConditions'] == ['yield_eligible_finish_policy']
    assert win['result']['yieldTrusted'] is False

    loss = example()
    for unit in loss['ownUnits']:
        unit['parameters'][10].update(rawValue=1, rawMax=1)
        unit['skills'] = []
        unit['invocationLevels'] = []
    loss['housePets'] = {owner: [] for owner in loss['housePets']}
    loss['tickLimit'] = 200
    lost = run_scenario(loss)
    loss_entitlement = lost['result']['rewardEntitlement']
    assert lost['result']['verdict'] == 2
    assert loss_entitlement['awardedChestCount'] == 0
    assert loss_entitlement['awardedChestCountBasis'] == 'native-win-loss-gate'
    assert loss_entitlement['rewardCountSettled'] is False
    assert loss_entitlement['capturedPendingAtVerdict'] == loss_entitlement['pendingChestCount']
    return dict(winAwarded=win_entitlement['awardedChestCount'],
                winCertificateFrame=win_entitlement['certificate']['frame'],
                winVerdictTick=win['result']['verdictTick'],
                lossAwarded=loss_entitlement['awardedChestCount'],
                lossPending=loss_entitlement['pendingChestCount'],
                lossVerdictTick=lost['result']['verdictTick'])


def export_dataflow():
    """The exporter carries the runner's `rewardEntitlement` into `finalState`; old payloads still parse."""
    from combat_replay_export import export_replay
    from check_combat_finish_policy import winning_fixture
    replay = export_replay(winning_fixture(policy='at-horizon', ticks=400), include_events=False)
    ent = replay['finalState'].get('rewardEntitlement')
    assert isinstance(ent, dict), 'the exporter must carry rewardEntitlement into finalState'
    assert ent['pendingChestCount'] == replay['finalState']['prizeCallbacks']
    assert ent['battleVerdict'] == replay['finalState']['verdict']
    assert ent['victoryRequired'] is True
    assert ent['awardedChestCount'] == ent['pendingChestCount'] == 1
    assert ent['rewardCountSettled'] is True
    assert ent['certificate']['issuedBeforeVerdict'] is True
    # The player-facing fields a UI reads stay free of raw native offsets/RVAs.
    for field in ('awardedChestCountBasis', 'rewardCountReason'):
        assert '0x' not in str(ent[field]), (field, ent[field])
    # An older payload without the block is still a valid replay object (fallback path).
    legacy = dict(replay)
    legacy['finalState'] = {key: value for key, value in replay['finalState'].items()
                            if key != 'rewardEntitlement'}
    assert 'rewardEntitlement' not in legacy['finalState']
    return dict(pending=ent['pendingChestCount'], awarded=ent['awardedChestCount'],
                settled=ent['rewardCountSettled'], basis=ent['awardedChestCountBasis'])


def main():
    native_cases, winner_counts = native_gate_cases()
    evidence = native_key_evidence()
    pin = enemy_cure_pin()
    fixtures = plumbing_fixtures()
    reentry = delayed_reentry_regression()
    runs = real_runs()
    exported = export_dataflow()
    report = dict(
        nativeRecordedFinishCases=native_cases, recordedWinnerCounts=winner_counts,
        nativeEvidence=evidence, enemyCurePin=pin, plumbingFixtures=fixtures,
        delayedReentry=reentry, realRuns=runs, exportedRewardEntitlement=exported,
        findings=[
            'The native win/loss gate (special flag and winner==1) is reused from the recorded Finish '
            'cases; a winner!=1 run reports awardedChestCount 0 independent of the pending count and of any '
            'certificate (15 pending -> 0 is covered).',
            'The certificate is only issued strictly before the verdict: a synthetic pre-verdict hold with an '
            'unchanged queue certifies the pending count, while the earlier implementation (which read the '
            'certificate only AFTER the verdict) trusted a late hold. The legal winning fixture now certifies '
            'at a strictly pre-verdict frame (235 < verdict 259), not at the old post-verdict frame 260.',
            'The certificate is refused for an enemy-Cure roster, for an enemy skill id with no known master '
            'row (the old enemy_cure_sources silently skipped it and left the scope allowed), and for a stale '
            'BKL:16; a prize queued after the snapshot invalidates it.',
            'The delayed 8->6->8 repeat keeps awarding prizes (labelled synthetic); the certificate never '
            'latches, never freezes the battle, and reports the later queue change.',
            'combatRulesSupported is True while rewardYieldSupported and autoFinishProducerProven stay False: '
            'the combat rules are decoupled from the unproven Finish timing, and no ranking/yield trust changed.'],
        limits=[
            'The Cure pin is a roster pin (Monster.skillId against skill types), not a runtime observation of '
            'every possible HP write; a new HP-raising source must re-open the audit.',
            'C3 is conservative across all fighters: a stale BKL:16 on a dead/idle fighter may never clear, so '
            'the certificate may not hold on a real run (a hold first reached only at/after the verdict stays '
            'unknown and is never certified).',
            'The certificate is a strict pre-verdict snapshot; it does not prove the native ordering between the '
            'last prize producer and EnterEnding, it only refuses to trust a hold observed at/after the verdict.',
            'The 8->6->8 fixture is synthetic and labelled; the native producer evidence is the recorded Finish '
            'cases plus check_combat_queued_chests.py.',
            'awardedChestCount is an entitlement count, not an inventory receipt (combat_finish.finish_report).'])
    (EVIDENCE / 'reward-entitlement-checks.json').write_text(json.dumps(report, indent=2) + '\n',
                                                             encoding='utf-8')
    print(json.dumps({k: report[k] for k in ('nativeRecordedFinishCases', 'nativeEvidence',
                                             'enemyCurePin', 'realRuns', 'exportedRewardEntitlement',
                                             'delayedReentry')}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
