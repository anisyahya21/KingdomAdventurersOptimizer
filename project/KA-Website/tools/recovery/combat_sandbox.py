"""Run an explicit offline research scenario; results are not yet certified predictions.

Usage: python combat_sandbox.py scenario.json --output report.json [--trace]
"""
import argparse
import json
from pathlib import Path
from copy import deepcopy
from combat_scenario import load_scenario,ScenarioError
from combat_setup import prepare_setup
from combat_initial_state import expand_start_profile
from combat_shared_controllers import ROWS,SharedControllers
from combat_resolution import add_raw_parameter
from combat_consumables import use_battle_holy_herb,use_battle_item
from combat_runtime_data import table
from combat_prizes import special_prize_candidates
from combat_receipt import ReceiptLedger
import combat_progress
from combat_finish import special_finish,finish_report
from combat_settlement import treasure_flight
from combat_reward_entitlement import RewardEntitlementWatch,entitlement_report
from combat_run_manifest import support_contract,run_manifest


# Native automatic-finish finding (2026-09-20). `BattleSystem.UpdateStateEnding` 0x14ef4e4 is the
# only exit from STATE_ENDING 3, and its gate is `Canvas.CheckKeyPulse(Canvas.KEY_SELECT 0x100000)`
# 0x14ef538. `Keypad.CheckKeyPulse` 0x23438b4 tests and clears one bit of `keyPulse_`, which
# `Keypad.DecideKeyState` 0x2343818 recomputes every frame as
# `(keyEventState_ | keyStateWork_) & ~keyState_` - a one-frame input edge. An exhaustive
# `bl`-site scan of libil2cpp.so for every producer of that edge (`Canvas.KeyDown` 0x22F5038,
# `Canvas.KeyUp` 0x22F50C0, `Canvas.KeyClick` 0x22F5124, `Canvas.KeyClickImmediate` 0x22F519C, plus
# the `Keys` overloads 0x22F50B8/0x22F50BC/0x22F5120/0x22F5198) finds only touch/keyboard/gamepad
# handlers and one held-joystick auto-repeat in `IApplication.Update` 0x2322028 (`KeyClick(1)` =
# KEY_0, not SELECT). No battle-driven, coroutine or guerrilla/special producer of KEY_SELECT exists
# in the direct call graph, so the observed click-free Wairo completion is not yet reproduced by a
# proven native producer. Every declared finish policy below is therefore DIAGNOSTIC-ONLY and the
# normal yield fails closed until that producer is identified.
# See check_combat_auto_finish_producer.py.
AUTO_FINISH_PRODUCER_PROVEN = False
AUTO_FINISH_UNPROVEN_REASON = ('native STATE_ENDING 3 exit is the one-frame KEY_SELECT input edge '
    '0x14ef538; no automatic producer of that edge exists in the direct call graph '
    '(check_combat_auto_finish_producer.py), so an automatic Ending->Finish is unproven and no '
    'dispatched-chest count is a trusted yield')


class RunAborted(Exception):
    """Raised by an `abort` callback: a bounded caller (the branch job) wants the run to stop now.

    The engine state in the caller's last checkpoint is untouched, so the caller resumes from it.
    A run that raises this has no report and must never be reported as a finished fight.
    """


def queued_prizes(engine):
    """Prize callback records in queue order (`engine.prizes` holds the emitted trace ids)."""
    return [dict(engine.trace[prize_id]) for prize_id in engine.prizes]


def finish_lifecycle(engine,verdict,policy='at-horizon'):
    """Run the recovered Finish step once a valid native Ending confirmation was requested.

    Returns `(stopReason, dispatched)`. Dispatch is the chest *entity*: `FireTreasure` draws the
    flight Math values in queue order and `CreateTreasure` spawns each chest. Nothing here is an
    inventory receipt or EXP, and the reward list is not clamped or deduplicated.

    `policy` is a DIAGNOSTIC simulation cut, never a native-automatic claim. The native Ending exit
    0x14ef538 is the one-frame KEY_SELECT input edge, and no automatic producer of that edge is
    proven in the direct call graph (AUTO_FINISH_PRODUCER_PROVEN is False), so every policy here is
    diagnostic-only and a dispatched count is never a trusted yield. `at-horizon` and `after-ending`
    only dispatch when the controller's declared Ending confirmation actually finished the fight
    (native-valid); a press at counter<=79 leaves the Ending open, so nothing is dispatched and the
    run reports awaiting-confirmation. `on-verdict` truncates post-verdict prizes.
    """
    boss=next((u for u in engine.teams[1] if engine.specs[u['id']].get('boss')),None)
    position=tuple(boss['position'][:3]) if boss is not None else (0.,0.,0.)
    dispatched=[]
    engine.phase='finish'
    verdict_tick=next((event['tick'] for event in engine.trace if event['kind']=='verdict'),engine.tick)
    engine.verdict_tick=verdict_tick
    engine.finish_tick=engine.tick
    confirmed=bool(getattr(engine,'ending_confirmed',False))
    if policy!='on-verdict' and not confirmed:
        # The declared Ending confirmation only wrote the counter (frame<=79); no native Finish ran,
        # so no chest entity was dispatched. Report it instead of fabricating a dispatched count.
        engine.emit('finish',winner=verdict,dispatchedChests=0,
                    confirmationPolicy='declared Ending confirmation at counter<=79 wrote 80, no Finish',
                    rewardsTruncated=False)
        return ('native verdict at tick %d; declared Ending confirmation at tick %d only wrote the '
                'Ending counter (frame<=79), so no native Finish ran and no chests were dispatched '
                '(awaiting-confirmation)'%(verdict_tick,engine.tick)),dispatched
    def fire(prize):
        flight=treasure_flight(prize['treasureId'],position,engine.next_math)
        dispatched.append(dict(prize,entityCreated=True,collection='pending',**flight))
        engine.emit('chest_dispatch',treasureId=prize['treasureId'],prizeEvent=prize['id'],
                    collection='pending',flight=flight)
    special_finish(verdict,queued_prizes(engine),special=True,defeat_count=lambda:None,
        fire_treasure=fire,pop_battle_form=lambda:None,destroy_source_boss=lambda:None)
    policies={
        'at-horizon':('DIAGNOSTIC ONLY (no proven native-automatic producer): ONE declared Ending '
                      'confirmation after the full final tick',
                      'native verdict at tick %d; diagnostic declared Ending confirmation at horizon tick %d'),
        'after-ending':('DIAGNOSTIC ONLY (no proven native-automatic producer): ONE declared Ending '
                        'confirmation at the recovered counter gate (counter>79) after a full tick',
                        'native verdict at tick %d; diagnostic declared Ending confirmation at tick %d'),
        'on-verdict':('DIAGNOSTIC ONLY: cut at the verdict tick boundary; not native button timing and '
                      'post-verdict prizes are truncated',
                      'native verdict at tick %d; diagnostic Finish cut at the verdict boundary tick %d')}
    confirmation,reason=policies.get(policy,policies['at-horizon'])
    engine.emit('finish',winner=verdict,dispatchedChests=len(dispatched),confirmationPolicy=confirmation,
                nativeAutomaticProducer='unproven',autoFinishReason=AUTO_FINISH_UNPROVEN_REASON,
                rewardsTruncated=policy=='on-verdict')
    return (reason%(verdict_tick,engine.tick)),dispatched


def run_scenario(data,include_trace=False,checkpoints=None,resume=None,stop_tick=None,abort=None):
    """Run one scenario to the declared horizon, or to `stop_tick` when a bounded caller asks for it.

    `stop_tick` (optional) stops the simulation after that tick and marks the report `windowed`: a
    window is not a final battle (no Finish input, no dispatched chest, no unlimited trust), it just
    stops earlier. `abort` (optional) is called every tick; when it returns true the run raises
    `RunAborted` so a background caller can release the interpreter at once. Both default to None,
    so every existing caller is unchanged.
    """
    scenario=load_scenario(data)
    support=support_contract(scenario)
    manifest=run_manifest(scenario,support)
    if any(e['type']=='finish' for e in scenario['inputs']):
        raise ScenarioError('Finish/EXP/teardown is not integrated; supply a tick horizon, not a guessed finish event')
    setup=prepare_setup(scenario)
    # Native special launcher refills all own fighters, including house pets.
    for source,prepared in zip(scenario['ownUnits'],setup['ownUnits'],strict=True):
        for p in (10,11):
            maximum=prepared['effectiveParameters'][p]['maximum']
            source['parameters'][p]['rawValue']=add_raw_parameter(source['parameters'][p]['rawValue'],maximum,maximum)[0]
    setup=prepare_setup(scenario)
    monsters=table('Monster')
    roster=[]
    for source,prepared in zip(scenario['ownUnits'],setup['ownUnits'],strict=True):
        if source['monsterId'] is not None and source['monsterId'] not in monsters:raise ScenarioError('Unknown pet monster ID')
        roster.append(dict(name=source['name'],human=source['human'],team=0,grid=prepared['grid'],cell=prepared['cell'],
            parameters=source['parameters'],skills=source['skills'],levels=source['invocationLevels'],
            equipment=source['equipment'],weaponId=source['weaponId'],
            humanFlags=source.get('humanFlags',0),
            invokingSkills=source.get('invokingSkills',[]),petOwnerName=source.get('petOwnerName'),
            monsterType=int(monsters[source['monsterId']][4]) if source['monsterId'] is not None else None,
            monsterSize=int(monsters[source['monsterId']][5]) if source['monsterId'] is not None else 0))
    enemy=setup['encounter']
    for source in enemy['fighters']:
        roster.append(dict(name=f"enemy:{source['incomingIndex']}:{source['monsterId']}",human=False,team=1,
            grid=source['grid'],cell=source['cell'],boss=source['leaderIdentity'],monsterType=int(monsters[source['monsterId']][4]),monsterSize=int(monsters[source['monsterId']][5]),
            parameters={int(k):v for k,v in source['parameters'].items()},
            skills=source['skills']['dataIds'],levels=source['skills']['invocationLevels']))
    # Resolve pre-placement without mutating the loaded scenario, so re-normalizing an
    # already-expanded scenario (startProfile + derived prePlacement) never trips the
    # startProfile/prePlacement mutual exclusion in load_scenario.
    if 'prePlacement' in scenario:
        pre=scenario['prePlacement']
    elif 'startProfile' in scenario:
        # Declared simulation conditions -> the same sequential InitFighters path; no hand-written
        # blackboards. support_contract().sourceState records what is modelled vs placeholder.
        pre=expand_start_profile(scenario['startProfile'],roster)
    else:
        pre=None
    if pre is not None:
        if set(pre)!={s['name'] for s in roster}:raise ScenarioError('prePlacement must name every own and enemy fighter exactly once')
        for spec in roster:spec['prePlacement']=pre[spec['name']]
    receipts=ReceiptLedger()
    engine=SharedControllers(roster,scenario['mathSeed'],scenario['libSeed'],row_offset=max(3,len(enemy['fighters'])//5+1),
        movement=True,initialization_orders=[setup['ownFormationOrder'],enemy['formationOrder']],
        prize_candidates=special_prize_candidates(scenario['encounterId']),receipts=receipts)
    for _ in range(enemy['followerSelectionDraws']):engine.next_lib('follower_selection_deferred')
    stock=scenario['holyHerbStock'];uses=[]
    items=scenario.get('items',{});item_counts=dict(scenario.get('itemStock',{}));item_uses=[]
    # The units whose MP is sampled, as the identities both engines address a fighter by. Scenario
    # configuration, never recovered game data; `load_scenario` has already refused an unknown or
    # repeated name. `mpWatchUnits` is the observation-only list an ordinary no-item candidate may
    # declare (it authorises nothing and needs no stock); `holyHerbTriggerUnits` remains the list that
    # may spend a charge, and is sampled too because a policy that triggers on MP has to observe it.
    watch_names=list(scenario.get('mpWatchUnits') or scenario.get('holyHerbTriggerUnits') or ())
    watch=[engine.names[name] for name in watch_names]
    def inputs(world,phase):
        nonlocal stock
        def add(i,p,amount,source):
            # The engine's own effective value and maximum, read immediately before and after the
            # same Parameter.Add; the delta is never recomputed here (check_combat_item_timeline).
            before=world.value(i,p)
            param=world.param(i,p)
            param['rawValue']=add_raw_parameter(param['rawValue'],amount,world.maximum(i,p))[0]
            after=world.value(i,p)
            if after!=before:
                engine.emit('resource_change',target=i,parameter=p,before=before,after=after,
                            max=world.maximum(i,p),sourceItem=source)
        def emit_item(record):
            # One authoritative attempt/use record per dispatched consumable (Holy Herb included).
            engine.emit('battle_item',**{k:v for k,v in record.items() if k not in ('targets','tick','phase')},
                        targets=record['targets'])
        def dispatch_holy_herb(source):
            """The one recovered Holy Herb dispatch, shared by the live policy and a prescribed event."""
            nonlocal stock
            def spend():
                nonlocal stock
                stock-=1
            blocked=stock<=0
            used=use_battle_holy_herb([u['id'] for u in world.teams[0]],lambda i:world.units[i]['board'][5],
                lambda:stock>0,world.rate,world.maximum,lambda i:world.specs[i]['human'],
                lambda i,p,amount:add(i,p,amount,'holy_herb'),spend)
            uses.append(dict(tick=world.tick,phase=phase,item='holy_herb',used=used,remaining=stock))
            progress_watch.note_herb_use(world.tick,combat_progress.PHASE_CODES[phase],source,used)
            record=dict(parameter=11,scope='all',used=used,percent=None if blocked else 100,
                        item='holy_herb',remaining=stock,
                        targets=[u['id'] for u in world.teams[0] if world.units[u['id']]['board'][5] not in (7,8)])
            if blocked:record['blocked']='no stock'
            emit_item(record)
        # The live `<=3%` policy: the crossing was latched by the existing after-fighters observer, and
        # this - the existing legal item-action seam - consumes it, before that tick's declared events.
        pending=progress_watch.take_pending_herb()
        if pending is not None:
            dispatch_holy_herb(pending)
        for event in scenario['inputs']:
            if event['tick']!=world.tick or event['phase']!=phase:continue
            if event['type']=='holy_herb':
                dispatch_holy_herb(None)
                continue
            name=event['item'];row=items[name]
            def spend(name=name):
                item_counts[name]-=1
            record=use_battle_item(row,[u['id'] for u in world.teams[0]],
                lambda name=name:item_counts[name]>0,
                lambda row=row:engine.next_math('item_bonus_value',bound=row['bonusMaxValue']-row['bonusMinValue']+1),
                world.rate,world.maximum,lambda i:world.specs[i]['human'],
                lambda i,p,amount:add(i,p,amount,name),spend,
                state=lambda i:world.units[i]['board'][5])
            record.update(tick=world.tick,phase=phase,item=name,remaining=item_counts[name])
            item_uses.append(record)
            emit_item(record)
    finish_policy=scenario.get('finishPolicy','at-horizon')
    # Report-only reward-entitlement observer: read the engine each frame after the fighters phase and
    # record the first frame the C1..C4 certificate holds STRICTLY BEFORE the verdict (a later hold is
    # kept only as a late hold, never certified), plus any later prize change. It never mutates the
    # engine and never stops the battle; see combat_reward_entitlement.
    entitlement_watch=RewardEntitlementWatch(setup['encounter'],ROWS)
    # Part A/B stored-attack progress observer: the same after-fighters seam, so it samples exactly
    # the state the native kernel's own observer samples. Read-only; it never mutates the engine.
    # The declared trigger units drive the MP telemetry and the live `<=3%` policy; with none declared
    # the observer's MP half returns immediately and the policy can never latch.
    progress_watch=combat_progress.ProgressWatch(combat_progress.boss_identity(engine.specs),
        watch=watch,watch_names=watch_names,holy_herb_max_uses=scenario.get('holyHerbMaxUses',0),
        holy_herb_start_stock=scenario['holyHerbStock'],rows=ROWS)
    engine.progress=progress_watch
    # Part D event-site counters: the Python mirror of the native encounter counters, incremented at
    # the genuine mutation sites (HP mutation, attack resolution, reaction candidate loop, Leaving
    # entry). Read-only; it never mutates the engine, draws a number or enters the digest.
    encounter_counters=combat_progress.EncounterCounters(
        [unit['id'] for unit in engine.teams[0]],combat_progress.boss_identity(engine.specs))
    engine.encounter=encounter_counters
    def observed_inputs(world,phase):
        inputs(world,phase)
        if phase=='after_fighters':
            entitlement_watch.observe(world)
            progress_watch.observe(world,stock=stock)
    if resume is not None:
        # Bounded-session resume (`combat_interaction`): continue from a checkpoint captured at the
        # END of an earlier tick of the same normalized scenario. The engine state, the consumable
        # counters and the entitlement observer travel with the checkpoint, so the resumed run
        # reproduces the byte-identical tick sequence from that checkpoint on. A fresh engine starts
        # at tick -1, so the un-resumed path is unchanged.
        engine=deepcopy(resume['engine'])
        # The trace travels as the checkpoint's own append-only snapshot: the event dicts are shared
        # (their own tick is complete before the checkpoint is taken, so nothing rewrites them) and
        # only the list is copied, which keeps resuming cheap enough for a dense checkpoint grid.
        engine.trace=list(resume['trace']) if resume.get('trace') is not None else engine.trace
        stock=resume['stock'];item_counts=dict(resume['itemCounts'])
        uses=deepcopy(resume['uses']);item_uses=deepcopy(resume['itemUses'])
        entitlement_watch=deepcopy(resume['entitlementWatch'])
        progress_watch=deepcopy(resume['progressWatch'])
        engine.progress=progress_watch
        # The restored engine's own counter sink travels with `deepcopy(resume['engine'])`; the report
        # must read the RESTORED counters, not a fresh zeroed object.
        encounter_counters=engine.encounter
        # The queue/receipt ledger lives on the engine (`self.receipts`), so the report must read the
        # RESTORED ledger, not the fresh one built above for the discarded engine.
        receipts=engine.receipts
    def take_checkpoint(world):
        if checkpoints is None or not checkpoints.wanted(world.tick):return
        trace=world.trace
        world.trace=[]
        try:
            frozen=deepcopy(world)
        finally:
            world.trace=trace
        checkpoints.store(dict(tick=world.tick,engine=frozen,trace=list(trace),stock=stock,
            itemCounts=dict(item_counts),uses=deepcopy(uses),itemUses=deepcopy(item_uses),
            entitlementWatch=deepcopy(entitlement_watch),progressWatch=deepcopy(progress_watch)))
    def tick_seam(world):
        # Bounded callers (the branch job) must be able to stop a stride and hand the interpreter
        # back; the checkpoint already taken for earlier ticks is the resume point, so nothing is lost.
        if abort is not None and abort(world):raise RunAborted('run aborted by the caller at tick %d'%world.tick)
        take_checkpoint(world)
    horizon=scenario['tickLimit']-1
    stop=horizon if stop_tick is None else min(horizon,stop_tick)
    windowed=stop<horizon
    # `run(n)` processes n consecutive native ticks starting at self.tick+1, and a full run ends on
    # tick `tickLimit-1`. Resuming from a checkpoint at tick t therefore needs tickLimit-1-t ticks;
    # a fresh engine starts at -1, which gives the unchanged tickLimit.
    result=engine.run(stop-engine.tick,observed_inputs,finish_policy=finish_policy,
        tick_hook=tick_seam if (checkpoints is not None or abort is not None) else None)
    events=result.pop('trace')
    # Native completion condition: BattleSystem.IsAnnihilated (a whole team in Leaving state 8).
    # Reaching it runs EnterEnding, which is where this run's winner field comes from; when it is
    # not reached the tick horizon is a censored stop, never a guessed finish.
    verdict=result['verdict']
    resolved=verdict is not None
    draws_before_finish=engine.math_draws
    if windowed:
        # A window is never a finished battle: the declared-Finish input was never reached and the
        # branch continues past the window edge, so no Finish step runs and no chest entity is
        # dispatched here. `finish` stays None and the branch job runs the rest of the fight.
        stopReason,dispatched=None,[]
    else:
        stopReason,dispatched=finish_lifecycle(engine,verdict,finish_policy) if resolved else (None,[])
    # `events` aliases engine.trace: Finish draws/events belong to that same timeline.
    result['finishMathDraws']=engine.math_draws-draws_before_finish
    result['mathDraws']=engine.math_draws
    result['libDraws']=engine.lib_draws
    result['rngFinalState']={name:dict(index=rng.index,partner=rng.partner,values=list(rng.values))
                             for name,rng in (('math',engine.math_rng),('lib',engine.lib_rng))}
    result['censored']=True if windowed else not resolved
    result['windowed']=windowed
    result['windowStopTick']=stop if windowed else None
    result['windowHorizonTick']=horizon if windowed else None
    result['windowRemainingTicks']=(horizon-stop) if windowed else None
    result['verdictTick']=getattr(engine,'verdict_tick',None) if resolved else None
    if result['verdictTick'] is None and resolved:
        # Fold the verdict tick from the runner's own `verdict` event (a window never runs the Finish
        # step that would set `engine.verdict_tick`, so the event is the only record).
        result['verdictTick']=next((event['tick'] for event in events if event['kind']=='verdict'),None)
    result['finishTick']=getattr(engine,'finish_tick',None) if resolved else None
    result['finishPolicy']=finish_policy
    # Additive pre-verdict prize-callback metric (bulk transport without the trace): the exact value
    # `strategy_search` folds from `kind == "prize"` trace events at or before the verdict tick, but
    # computed here from the engine's own timeline so a bulk caller that discards the trace does not
    # have to transport it. Pre-settlement only (never a dispatched/owned chest count) and None while
    # the battle is unresolved, because a censored run has no verdict boundary to cut at.
    result['preVerdictPrizeCallbacks']=(None if not resolved else
        sum(1 for event in events if event['kind']=='prize'
            and (result['verdictTick'] is None or event['tick']<=result['verdictTick'])))
    if windowed:
        result['stopReason']=('window stop at tick %d of the declared horizon %d: only this window was '
            'simulated, so this payload is not the final battle'%(stop,horizon))
    else:
        result['stopReason']=stopReason if resolved else 'tick horizon; battle unresolved (IsAnnihilated not reached)'
    # Finish-input honesty, no arbitrary settlement window. The declared player confirmation is the
    # input: a run is only trusted when that press actually finished the fight (native-valid). This is
    # NOT a claim that every future prize producer was exhausted - queued commands or in-flight
    # projectiles could have produced more chests, but the valid player Finish is an actual game
    # action that truncates them. Pending activity is exposed diagnostically and only the observed
    # dispatched count under this legal declared input is reported.
    last_prize=max((event['tick'] for event in events if event['kind']=='prize'),default=None)
    result['lastPrizeTick']=last_prize
    # A window requests no Ending confirmation at all, so both stay unreported instead of borrowing
    # the value a full run would have produced.
    result['endingConfirmed']=None if windowed else getattr(engine,'ending_confirmed',None)
    result['endingCounter']=None if windowed else getattr(engine,'ending_counter',None)
    result['pendingActivity']=dict(unresolvedCommands=result.get('unresolvedCommands'),
                                   pendingProjectiles=result.get('pendingProjectiles'),
                                   activeDamageOrLeaving=result.get('activeDamageOrLeaving'))
    confirmed=bool(getattr(engine,'ending_confirmed',False))
    result['rewardsTruncated']=finish_policy=='on-verdict'
    if windowed:result['finishBoundary']='window-not-final'
    elif not resolved:result['finishBoundary']='censored-unresolved-battle'
    elif result['rewardsTruncated']:result['finishBoundary']='diagnostic-truncated'
    elif confirmed:result['finishBoundary']='diagnostic-declared-finish; native-auto-producer-unproven'
    else:result['finishBoundary']='awaiting-confirmation'
    # Additive reward-entitlement report: pending count, native verdict, victory requirement, and the
    # certificate-only awarded count. Separate from the diagnostic Finish cut and from yieldTrusted.
    result['rewardEntitlement']=entitlement_report(watch=entitlement_watch,verdict=verdict,
        prize_callbacks=result['prizeCallbacks'],diagnostic_dispatched=len(dispatched),
        finish_boundary=result['finishBoundary'])
    # Part B stored-attack progress: the causal chain from stored commands to chests, measured at the
    # same after-fighters seam as the entitlement observer. Purely additive report data; it changes no
    # engine behaviour and is never folded into the compact result digest.
    result['progressMetrics']=progress_watch.report()
    # Part D event-site counters (own true-death / first-Leaving, enemy roll / resolved, counter
    # checks / enqueues, boss post-death) plus the v3 per-tick readings (future-hit mass at death,
    # occupancy, Damaging resets). Attached after the digest like every other telemetry block.
    counters=encounter_counters.report();counters.update(progress_watch.counter_report())
    result['encounterCounters']=counters
    # MP telemetry for the declared trigger units, and the explicit Holy Herb evidence. Both are
    # attached after the compact digest for the same reason `progressMetrics` is, so every digest a
    # saved library already holds stays valid; a scenario with no declared trigger units reports an
    # empty MP block and a zeroed herb block rather than a guessed reading.
    result['mpMetrics']=progress_watch.mp_report()
    result['herbMetrics']=progress_watch.herb_report(stock)
    # FAIL CLOSED: no declared policy is a proven native-automatic producer, so no dispatched count is
    # a trusted yield. The observed count stays reported as a labelled diagnostic only.
    result['autoFinishProducerProven']=AUTO_FINISH_PRODUCER_PROVEN
    result['autoFinishReason']=AUTO_FINISH_UNPROVEN_REASON
    result['yieldTrusted']=bool(AUTO_FINISH_PRODUCER_PROVEN and resolved and confirmed
                                and not result['rewardsTruncated'])
    result['retainedRewards']=None
    # The ledger keeps queued awards distinct from dispatched/retained ones. Finish dispatches the
    # chest *entities*; a receipt still needs a collected chest and supplied master-data rows, so the
    # queue is censored only when the battle itself stayed unresolved.
    if windowed or not resolved:receipts.censor(result['stopReason'])
    report=dict(status='research-only; not certified for gear recommendations',encounterId=scenario['encounterId'],
        defeatCount=scenario['defeatCount'],level=enemy['level'],title=enemy['title'],terrain=-1,
        seeds=dict(math=scenario['mathSeed'],lib=scenario['libSeed']),manifest=manifest,setup=setup,result=result,
        holyHerbUses=uses,holyHerbRemaining=stock,itemUses=item_uses,itemRemaining=item_counts,receipts=receipts.report(),
        finish=None if not resolved else finish_report(verdict,queued_prizes(engine),dispatched),
        metrics=dict(attackAttempts=sum(e['kind']=='attack' for e in events),
            heals=sum(e['kind']=='heal' for e in events),commands=sum(e['kind']=='enqueue' for e in events),
            failedReleases=sum(e['kind']=='release' and not e['used'] for e in events),
            prizeCallbacks=result['prizeCallbacks']),
        unresolved=['Pre-placement source state is the declared startProfile (enemy Cell and board19/20 from the recovered CreateMonster producer, optional declared starting status); a captured boss world cell, own-team clone boards and exact initial system/event state are not modelled (see manifest.support.sourceState).',
                    'Finish policies are diagnostic cuts: the recovered Android 2.6.2 input gate does not explain the observed automatic iOS return to town. on-verdict can truncate pending activity and is not asserted to be the game policy. Battle verdict, pending chests and reward entitlement are reported separately: the native loss gate yields zero; a winning entitlement requires an immutable queue certified before the verdict. Diagnostic chest dispatch is not an inventory receipt. Global yield ranking remains disabled; see rewardEntitlement and check_combat_auto_finish_producer.py.',
                    'Complete effect/resource/garbage behavior and associated entity allocation/RNG.',
                    'Model-estimated fight result only: automatic Ending transition, EXP and teardown remain incomplete; diagnostic Finish dispatches chest entities without collecting them.',
                    'Receipt dispatch needs a collected chest with supplied ItemData/MaterialData rows; world collection of ground spawns is not modelled.',
                    'Linked parameter graphs and unsupported skills are rejected; job/equipment compatibility is an explicit input responsibility.',
                    'House-pet entries must be the complete ordered lookup result supplied by the user; save extraction is not implemented.'])
    if include_trace:report['trace']=events
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('scenario',type=Path)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--trace',action='store_true')
    args=parser.parse_args()
    report=run_scenario(json.loads(args.scenario.read_text(encoding='utf-8')),args.trace)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(status=report['status'],metrics=report['metrics'],output=str(args.output.resolve()))))
