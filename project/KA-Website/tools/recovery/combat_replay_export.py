"""Generic, deterministic replay export for one ka-special-combat-research-1 scenario.

PASS 16 COMMAND 16.4. The run itself is the existing authoritative pipeline, called unchanged:

    combat_scenario.load_scenario -> combat_setup.prepare_setup -> combat_sandbox.run_scenario

This module adds no combat behaviour. It only re-labels the runner's own output into one
UI-independent payload (`ka-battle-replay-1`) that a renderer can consume:

  * stable unit identity (`side:rosterIndex`) next to the simulator's own entity ids;
  * the runner's actual event stream, normalized field by field (nothing invented);
  * the runner's actual final state, plus cells folded from the cell-change events;
  * the runner's own `report.finish` lifecycle verbatim, plus a `postFinish` disclosure: the event
    trace and `rngFinalState` include the Finish chest dispatch, while EXP,
    result-confirmation and world collection stay outside the runner;
  * the recovered state-id names (BATTLE_STATE key 5), the recovered weapon/skill motion values
    for exactly the ids in play, and an explicit list of what the simulator does not expose.

The payload deliberately contains no timestamps, no paths and no rendered/DOM state, so two runs of
the same scenario are byte-identical.

Usage:
    python combat_replay_export.py scenario.json --out replay.json [--pretty] [--no-events]
"""
import argparse
import json
from copy import deepcopy
from pathlib import Path

from combat_runtime_data import load_data
from combat_scenario import load_scenario
from combat_setup import prepare_setup
from combat_sandbox import run_scenario

SCHEMA = 'ka-battle-replay-1'
EXPORTER = 'combat_replay_export.py'
RUNNER = 'combat_sandbox.run_scenario'

# `finish_lifecycle` sets the engine phase to this before it emits the declared-Finish events
# (the diagnostic chest dispatch and its RNG draws). Those are NOT simulated battle ticks.
FINISH_PHASE = 'finish'

ENDING_CUT_NOTE = ('endingCutTick is the last tick the engine simulated; the playback must clamp its '
                   'clock to it. endingTick is the Ending entry (a whole team in Leaving state 8, not '
                   '"last HP 0", so the knock-down/revive window and the departure animation still '
                   'play). The declared-Finish chest dispatch sits at the horizon and is excluded: the '
                   'native automatic Ending->Finish producer is unproven, so no horizon reward is '
                   'validated.')


def ending_cut(result, events, horizon_tick=None):
    """Separate the engine's Verdict/Ending from the declared-Finish tail, from the runner's record only.

    Nothing is invented: the boundary is folded from the runner's own event phases.
      * `endingTick` - the frame Ending was entered (the verdict tick). That is where a whole team is
        in Leaving (state 8). A knocked-down unit (state 7, HP 0) inside the revive window, or a
        revived unit (state 2), keeps the fight open, so this is deliberately not "last HP 0".
      * `endingCutTick` - the last tick with an event outside the declared `finish` phase: the losing
        side's knock-down/departure animation is kept, the diagnostic Finish dispatch is not.
      * `horizonTick` - the declared limit's last simulated tick (end of the silent tail).
    `finalState.ticks` stays the engine horizon; `endingCutTick` is the playback window end.

    `horizon_tick` overrides the horizon for a windowed run: a window stops early, so the declared
    horizon (not the window edge) remains the reference the cut and the silent tail are measured
    against. Nothing else changes.
    """
    horizon_tick = (result['ticks'] - 1) if horizon_tick is None else horizon_tick
    verdict_tick = result.get('verdictTick')
    finish_phase = [event['tick'] for event in events if event.get('phase') == FINISH_PHASE]
    simulated = [event['tick'] for event in events if event.get('phase') != FINISH_PHASE]
    if simulated:
        cut_tick = max(simulated)
    else:
        # No trace requested: fall back to the Ending entry when resolved, else the horizon.
        cut_tick = verdict_tick if verdict_tick is not None else horizon_tick
    return dict(
        endingTick=verdict_tick,
        endingCutTick=cut_tick,
        horizonTick=horizon_tick,
        postEndingTicks=(cut_tick - verdict_tick) if verdict_tick is not None else None,
        silentTailTicks=max(0, horizon_tick - cut_tick),
        finishPhaseEvents=len(finish_phase),
        finishPhaseFirstTick=min(finish_phase) if finish_phase else None,
        endingCutReason=ENDING_CUT_NOTE,
    )

# Finish belongs to the same authoritative trace and RNG snapshot as combat.
FINISH_NOTE = ('report.finish is copied verbatim from the runner. Finish RNG draws and chest_dispatch '
               'events are included in the event timeline and final RNG state. Confirmation is '
               'modelled as dispatch at the supplied tick horizon once a verdict exists; actual '
               'EXP, player confirmation timing and world collection are outside this model.')


def post_finish_block(finish, math_draws, finish_draws=0):
    """Describe the runner's joined Finish trace; no RNG reconstruction in the exporter."""
    return dict(
        finishApplied=finish is not None,
        inventoryCollection=(finish or {}).get('inventoryCollection', {}).get('state', 'not reached'),
        exp=(finish or {}).get('exp', 'not modelled'),
        confirmation='model policy: dispatch at supplied tick horizon after resolved verdict',
        worldCollection='not modelled: ground spawns and world collection are not simulated',
        traceSnapshot='event trace and rngFinalState include the Finish chest dispatch',
        chestDispatchMathDraws=finish_draws,
        mathDrawsInTrace=math_draws,
        mathDrawsAfterFinish=math_draws,
        note=FINISH_NOTE)

# BATTLE_STATE names, recovered from the native key table (special-combat.md, BKI:5).
STATE_NAMES = {1: 'waiting', 2: 'moving', 3: 'charging', 4: 'attacking',
               5: 'using_skill', 6: 'damaging', 7: 'knocking_down', 8: 'leaving'}

# Trace fields that carry a simulation entity id, in the runner's own spelling. Array fields map to
# the same unit ids in the same order, so a multi-target record keeps its per-target identity.
UNIT_FIELDS = ('target', 'caster', 'attacker', 'owner')
UNIT_ARRAY_FIELDS = (('targets', 'targetUnitIds'),)


def load_profiles():
    profiles = load_data('weapon-skill-profiles.json')
    return {row['id']: row for row in profiles['equipment']}, {row['id']: row for row in profiles['skills']}


def unit_id(side, index):
    return f'{side}:{index}'


def build_units(scenario, setup, entity_names):
    """One record per fighter: stable replay id, simulator entity id, roster facts, initial state."""
    units = []
    for index, (source, prepared) in enumerate(zip(scenario['ownUnits'], setup['ownUnits'], strict=True)):
        entity_id = entity_names.get(source['name'])
        if entity_id is None:
            raise KeyError(f"runner reported no entity id for ally '{source['name']}'")
        record = dict(
            unitId=unit_id('ally', index), side='ally', rosterIndex=index,
            entityId=entity_id, name=source['name'],
            kind='human' if source['human'] else 'monster', human=source['human'],
            monsterId=source['monsterId'], weaponId=source['weaponId'],
            skillIds=list(source['skills']), invocationLevels=list(source['invocationLevels']),
            grid=prepared['grid'], column=prepared['column'], row=prepared['row'], cell=list(prepared['cell']),
            equipment=[dict(row) for row in source['equipment']],
            # combat_sandbox.run_scenario refills every own fighter's HP/MP to the effective
            # maximum before the run ("Native special launcher refills all own fighters, including
            # house pets"), so the run-start values are the runner's own prepared maximum.
            startHp=prepared['effectiveParameters'][10]['maximum'],
            startMp=prepared['effectiveParameters'][11]['maximum'],
            parameters=dict(
                {str(pid): dict(raw=raw,
                                effectiveValue=prepared['effectiveParameters'][pid]['value'],
                                effectiveMaximum=prepared['effectiveParameters'][pid]['maximum'])
                 for pid, raw in source['parameters'].items()}
            ),
        )
        for key in ('visitor', 'leaderIdentity', 'humanFlags', 'isHouseOwner', 'petOwnerName', 'ownerPlayer'):
            if source.get(key) is not None:
                record[key] = source[key]
        units.append(record)

    for index, fighter in enumerate(setup['encounter']['fighters']):
        name = f"enemy:{fighter['incomingIndex']}:{fighter['monsterId']}"
        entity_id = entity_names.get(name)
        if entity_id is None:
            raise KeyError(f'runner reported no entity id for {name}')
        units.append(dict(
            unitId=unit_id('enemy', index), side='enemy', rosterIndex=index,
            entityId=entity_id, name=name,
            kind='monster', human=False, monsterId=fighter['monsterId'], weaponId=0,
            skillIds=list(fighter['skills']['dataIds']),
            invocationLevels=list(fighter['skills']['invocationLevels']),
            leaderIdentity=bool(fighter['leaderIdentity']), boss=bool(fighter['leaderIdentity']),
            level=fighter['level'], maxLevel=fighter['maxLevel'], rank=fighter['rank'],
            grid=fighter['grid'], column=fighter['column'], row=fighter['row'], cell=list(fighter['cell']),
            # Enemies are constructed from the encounter baseline and are not refilled.
            startHp=fighter['parameters']['10']['rawValue'],
            startMp=fighter['parameters']['11']['rawValue'],
            parameters=dict(
                {str(pid): dict(raw=raw,
                                effectiveValue=raw['rawValue'],
                                effectiveMaximum=raw['rawMax'])
                 for pid, raw in fighter['parameters'].items()}
            ),
        ))
    return units


def build_skill_slots(units):
    """First-position slot lookup per unit: the ordered setup skill list is the slot order."""
    slots = {}
    for unit in units:
        for index, skill_id in enumerate(unit['skillIds']):
            slots.setdefault((unit['unitId'], skill_id), index)
    return slots


def normalize_events(trace, id_to_unit, slots):
    """Re-label the runner's own events. No event is added, removed or merged."""
    unit_by_id = {unit['unitId']: unit for unit in id_to_unit.values()}
    events = []
    for raw in trace:
        event = dict(raw)
        event['seq'] = event.pop('id')
        for field in UNIT_FIELDS:
            if field not in event:
                continue
            value = event.pop(field)
            actor = id_to_unit.get(value)
            event[f'{field}UnitId'] = actor['unitId'] if actor else None
        for field, mapped in UNIT_ARRAY_FIELDS:
            if field not in event:
                continue
            value = event.pop(field)
            event[mapped] = [id_to_unit[identity]['unitId'] if identity in id_to_unit else None
                             for identity in value]
        if 'projectile' in event:
            event['projectileId'] = event.pop('projectile')
        if 'attacks' in event:
            event['attackSeqs'] = event.pop('attacks')
        if event['kind'] == 'state':
            event['stateName'] = STATE_NAMES.get(event['new'])
        if 'skill' in event:
            # Uniform spelling: null means the ordinary weapon attack, a number is the skill record.
            event['skillId'] = event.pop('skill')
        if isinstance(event.get('skillId'), int):
            actor = next((unit_by_id.get(event[field])
                          for field in ('casterUnitId', 'attackerUnitId') if event.get(field)), None)
            if actor is not None:
                slot = slots.get((actor['unitId'], event['skillId']))
                if slot is not None:
                    event['skillSlot'] = slot
        events.append(event)
    return events


def fold_cells(units, events):
    """Cells are not part of the runner's final state: start from the prepared formation cell for
    units the runner never moved, then apply the cell-change events in trace order."""
    cells = {unit['unitId']: list(unit['cell']) for unit in units}
    for event in events:
        if event['kind'] == 'cell_change' and event['targetUnitId']:
            cells[event['targetUnitId']] = list(event['cell'])
    return cells


def build_catalog(units, events, equipment, skills):
    """Recovered motion/identity values for exactly the ids this run used."""
    skill_ids, weapon_ids = set(), set()
    for unit in units:
        skill_ids.update(unit['skillIds'])
        if unit['weaponId']:
            weapon_ids.add(unit['weaponId'])
    for event in events:
        if event.get('skillId') is not None:
            skill_ids.add(event['skillId'])
    return dict(
        stateNames={str(key): value for key, value in STATE_NAMES.items()},
        skills={str(sid): dict(id=sid, motion=skills[sid]['motion'], type=skills[sid]['type'],
                               value=skills[sid]['value'], minMp=skills[sid]['minMp'],
                               maxMp=skills[sid]['maxMp'], requiredEquipType=skills[sid]['requiredEquipType'],
                               source='RE-evidence/20260912-combat/weapon-skill-profiles.json')
                for sid in sorted(skill_ids)},
        equipment={str(eid): dict(id=eid, name=equipment[eid]['name'], type=equipment[eid]['type'],
                                  motion=equipment[eid]['motion'],
                                  shootingRange=equipment[eid]['shootingRange'],
                                  projectileFlag=equipment[eid]['projectileFlag'],
                                  source='RE-evidence/20260912-combat/weapon-skill-profiles.json')
                   for eid in sorted(weapon_ids)},
    )


WINDOW_NOTE = ('finalState.windowed marks a LIVE WINDOW: the run was stopped at windowStopTick and the '
               'branch continues for windowRemainingTicks more ticks. A window is not a final battle - it '
               'carries no verdict claim, no declared-Finish dispatch and no ending cut for the whole '
               'fight - and it must never be shown as one. ticks/horizonTick keep the declared horizon.')


def export_replay(scenario, include_events=True, checkpoints=None, resume=None, stop_tick=None, abort=None):
    """Run one scenario through the authoritative pipeline and return the replay payload.

    `stop_tick`/`abort` are passed straight to `run_scenario`; with `stop_tick` the payload is marked
    as a window (see WINDOW_NOTE) and keeps the declared horizon in `ticks`/`horizonTick`.
    """
    data = deepcopy(scenario)
    normalized = load_scenario(data)
    setup = prepare_setup(deepcopy(data))
    report = run_scenario(deepcopy(data), include_trace=include_events, checkpoints=checkpoints,
                          resume=resume, stop_tick=stop_tick, abort=abort)
    result = report['result']
    windowed = bool(result.get('windowed'))

    entity_names = result['names']
    units = build_units(normalized, setup, entity_names)
    id_to_unit = {unit['entityId']: unit for unit in units}
    events = normalize_events(report.get('trace') or [], id_to_unit, build_skill_slots(units))
    equipment, skills = load_profiles()
    cells = fold_cells(units, events)

    final_units = []
    for unit in units:
        state = result['units'][unit['entityId']]
        final_units.append(dict(
            unitId=unit['unitId'], entityId=unit['entityId'], side=unit['side'],
            rosterIndex=unit['rosterIndex'], hp=state['hp'], mp=state['mp'],
            state=state['state'], stateName=STATE_NAMES.get(state['state']), commands=state['commands'],
            cell=cells[unit['unitId']],
        ))

    return dict(
        schema=SCHEMA,
        source=dict(exporter=EXPORTER, runner=RUNNER, scenarioSchema=normalized['schema'],
                    note='One deterministic run of the authoritative research pipeline; no combat behaviour is reimplemented here.'),
        setupSummary=dict(
            encounterId=normalized['encounterId'], defeatCount=normalized['defeatCount'],
            mathSeed=normalized['mathSeed'], libSeed=normalized['libSeed'], tickLimit=normalized['tickLimit'],
            holyHerbStock=normalized['holyHerbStock'], inputs=list(normalized['inputs']),
            items=deepcopy(normalized.get('items', {})), itemStock=dict(normalized.get('itemStock', {})),
            ownUnitCount=len(normalized['ownUnits']),
            enemyUnitCount=len(setup['encounter']['fighters']),
            prePlacement=sorted(normalized.get('prePlacement', {})),
            startProfile=deepcopy(normalized.get('startProfile')),
            finishPolicy=normalized.get('finishPolicy', 'at-horizon'),
            finishPolicyEligible=normalized.get('finishPolicy', 'at-horizon')!='on-verdict',
        ),
        encounter=dict(
            encounterId=setup['encounter']['encounterId'], title=setup['encounter']['title'],
            level=setup['encounter']['level'], defeatCount=setup['encounter']['defeatCount'],
            followerSelectionDraws=setup['encounter']['followerSelectionDraws'],
            formationOrder=list(setup['encounter']['formationOrder']),
            ownFormationOrder=list(setup['ownFormationOrder']),
            enemyMonsterIds=[fighter['monsterId'] for fighter in setup['encounter']['fighters']],
        ),
        units=units,
        # A window keeps the DECLARED horizon here (the renderer must not read the window edge as the
        # fight's length); windowStopTick/windowRemainingTicks say how far the window really ran.
        ticks=normalized['tickLimit'] if windowed else result['ticks'],
        events=events,
        finalState=dict(
            verdict=result['verdict'], battleState=result['battleState'],
            battleFrame=result['battleFrame'],
            ticks=result['ticks'], verdictTick=result.get('verdictTick'), finishTick=result.get('finishTick'),
            finishPolicy=result.get('finishPolicy'), stopReason=result['stopReason'], censored=result['censored'],
            finishBoundary=result.get('finishBoundary'), yieldTrusted=result.get('yieldTrusted'),
            rewardsTruncated=result.get('rewardsTruncated'), endingConfirmed=result.get('endingConfirmed'),
            endingCounter=result.get('endingCounter'), pendingActivity=result.get('pendingActivity'),
            endingGateTick=result.get('endingGateTick'),
            # Additive ending cut: separate the engine's Verdict/Ending from the declared-Finish tail
            # (see `ending_cut`). `ticks` stays the engine horizon; the renderer clamps playback to
            # `endingCutTick`, so the post-verdict silent tail and the diagnostic horizon chest
            # dispatch are not played as battle time.
            **ending_cut(result, events, horizon_tick=(
                normalized['tickLimit'] - 1 if windowed else None)),
            # Additive live-window marker (WINDOW_NOTE). Absent on every full payload.
            windowed=windowed,
            windowStopTick=result.get('windowStopTick') if windowed else None,
            windowHorizonTick=result.get('windowHorizonTick') if windowed else None,
            windowRemainingTicks=result.get('windowRemainingTicks') if windowed else None,
            windowNote=WINDOW_NOTE if windowed else None,
            lastPrizeTick=result.get('lastPrizeTick'), unresolvedCommands=result.get('unresolvedCommands'),
            prizeCallbacks=result['prizeCallbacks'], mathDraws=result['mathDraws'], libDraws=result['libDraws'],
            rngFinalState=result['rngFinalState'], retainedRewards=result['retainedRewards'],
            initializationMode=result['initializationMode'], units=final_units,
            # Additive report-only block from the runner (combat_reward_entitlement): pending chests vs an
            # awarded count that requires a certificate issued strictly before the verdict. Absent in older
            # payloads; the renderer must fall back to `prizeCallbacks` then. It is an entitlement count, not
            # an inventory receipt, and no combat logic is re-run here.
            rewardEntitlement=result.get('rewardEntitlement'),
            cellsDerivedFrom='prepared formation cell per unit, then every cell_change event in trace order '
                             '(the runner exposes no final cell per unit)',
        ),
        finish=report['finish'],
        postFinish=post_finish_block(report['finish'], result['mathDraws'], result['finishMathDraws']),
        catalog=build_catalog(units, events, equipment, skills),
        metrics=report['metrics'],
        holyHerbRemaining=report['holyHerbRemaining'],
        itemRemaining=report['itemRemaining'], itemUses=report['itemUses'],
        receipts=report['receipts'],
        runnerLimits=list(result['limits']) + list(report['unresolved']),
        notes=[
            'unitId is the stable replay identity (side:rosterIndex); entityId is the simulator id the event stream uses',
            'trace seq is the runner event order; tick -1 is the initialization phase',
            'weaponId 0 is the recovered Bare-Handed record for a human and "no weapon" for a monster',
            'fighter state ids use the recovered BATTLE_STATE table in catalog.stateNames; finalState.battleState is the separate BattleSystem state and is deliberately not named here',
            'state id 0 appears only in the phase "battle" when the run ends and units are torn down; it is outside the recovered 1..8 table, so stateName stays null and the raw id is kept',
            'finish is report.finish verbatim (fightOutcome, queuedChestAwards, dispatchedChestAwards, inventoryCollection, exp); postFinish discloses that the event trace and rngFinalState include the Finish chest dispatch',
        ],
        missing=[
            'explicit KO event: the runner exposes the Damaging(6) -> KnockingDown(7) -> Leaving(8) state chain and attack hpAfter instead of a dedicated KO event',
            'receipt/inventory collection is unmodelled; chest_dispatch events describe dispatched chest entities, not retained inventory',
            'per-tick snapshots: the trace records state-changing events, not one snapshot per tick; HP/MP/state/cell are folded from the events',
            'damage numbers for projectile impacts: impact events carry the projectile and cell, the damage lands through the attack events',
            'EXP, the result-confirmation callbacks and world collection of ground spawns are not modelled (postFinish says so explicitly)',
        ],
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('scenario', type=Path)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--pretty', action='store_true')
    parser.add_argument('--no-events', action='store_true')
    args = parser.parse_args(argv)

    scenario = json.loads(args.scenario.read_text(encoding='utf-8'))
    replay = export_replay(scenario, include_events=not args.no_events)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(replay, indent=2 if args.pretty else None, sort_keys=False, ensure_ascii=False) + '\n',
        encoding='utf-8')
    print(json.dumps(dict(
        schema=replay['schema'], encounterId=replay['setupSummary']['encounterId'],
        units=len(replay['units']), events=len(replay['events']), ticks=replay['ticks'],
        verdict=replay['finalState']['verdict'], output=str(args.out.resolve()))))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
