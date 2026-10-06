"""Replay adapters for pinned native engines.

Go traces preserve opaque KaEvent ABI words, Rust traces use the selected DLL's full-tick capture,
and C++ traces use its native ``ka-battle-replay-1`` capture. Semantic event labels come only from
the canonical ``ka_events`` mapping; state and object snapshots remain copied from native output.
No path runs a Python battle.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
import uuid
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[3]
_INPUT_MANIFEST = _ROOT / 'coordination/native-preparation/shared/comparison-inputs/bank2-v3/input-set-manifest.json'
_MAX_REPLAY_BYTES = 32 * 1024 * 1024
_STATE_NAMES = {1: 'waiting', 2: 'moving', 3: 'charging', 4: 'attacking',
                5: 'using_skill', 6: 'damaging', 7: 'knocking_down', 8: 'leaving'}


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                      allow_nan=False).encode('utf-8')


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _file_sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _native_number(primary, fallback=None):
    if isinstance(primary, (int, float)) and not isinstance(primary, bool):
        return primary
    if isinstance(fallback, (int, float)) and not isinstance(fallback, bool):
        return fallback
    return None


def _read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def _selected_manifest(assets):
    """Use the manifest snapshot bound when the controller selected its engine assets."""
    manifest = assets.get('manifestData')
    if not isinstance(manifest, dict):
        raise ValueError('Selected native engine has no captured manifest snapshot.')
    return manifest


def _go_template(encounter_id):
    manifest = _read_json(_INPUT_MANIFEST)
    row = manifest.get('outputs', {}).get('goPerEncounter', {}).get(str(encounter_id))
    if not isinstance(row, dict) or not isinstance(row.get('path'), str):
        raise ValueError(f'No pinned Go replay tables exist for encounter {encounter_id}.')
    path = Path(row['path'])
    if not path.is_absolute():
        path = (_ROOT / path).resolve()
    if _file_sha(path) != row.get('sha256'):
        raise ValueError('Pinned Go replay table template changed; refusing replay.')
    template = _read_json(path)
    if template.get('schema') != 'ka-go-workload-1' or not isinstance(template.get('tables'), dict):
        raise ValueError('Pinned Go replay table template has an unsupported schema.')
    return template


def _replay_parameter_projection(raw_unit, visual_unit):
    """Project original parameter intent alongside only native-observed effective values/caps."""
    raw_parameters = raw_unit.get('parameters') or {}
    native_parameters = visual_unit.get('effectiveParameters') or visual_unit.get('parameters') or {}
    result = {}
    keys = list(dict.fromkeys([str(key) for key in raw_parameters] +
                              [str(key) for key in native_parameters]))
    for key in keys:
        value = raw_parameters.get(key, raw_parameters.get(int(key) if key.isdigit() else key))
        raw = None
        if isinstance(value, dict) and all(isinstance(value.get(k), (int, float)) for k in
                ('rawValue', 'rawMax', 'extraValue', 'extraMax', 'trainingLevel')):
            raw = dict(rawValue=value['rawValue'], rawMax=value['rawMax'],
                       extraValue=value['extraValue'], extraMax=value['extraMax'],
                       trainingLevel=value['trainingLevel'])
        native = native_parameters.get(str(key), native_parameters.get(key))
        effective_value = (native.get('effectiveValue', native.get('value'))
                           if isinstance(native, dict) else None)
        effective_maximum = (native.get('effectiveMaximum', native.get('maximum'))
                             if isinstance(native, dict) else None)
        source = 'native-visual-snapshot' if isinstance(native, dict) else None
        if str(key) == '10':
            hp = visual_unit.get('hp') or {}
            current = visual_unit.get('hpCurrent', hp.get('raw'))
            maximum = visual_unit.get('hpMax', hp.get('max'))
            if isinstance(current, (int, float)):
                effective_value = current
                source = 'native-initial-state'
            if isinstance(maximum, (int, float)):
                effective_maximum = maximum
                source = 'native-initial-state'
        elif str(key) == '11':
            mp = visual_unit.get('mp') or {}
            current = visual_unit.get('mpCurrent', mp.get('raw'))
            maximum = visual_unit.get('mpMax', mp.get('max'))
            if isinstance(current, (int, float)):
                effective_value = current
                source = 'native-initial-state'
            if isinstance(maximum, (int, float)):
                effective_maximum = maximum
                source = 'native-initial-state'
        result[str(key)] = dict(raw=raw, effectiveValue=effective_value,
                                effectiveMaximum=effective_maximum,
                                effectiveSource=source)
    return result


def _summary_payload(scenario, visual_units, trace, report, catalog):
    raw_units = scenario.get('ownUnits') or []
    units_by_id = {}
    units = []
    for i, visual in enumerate(visual_units):
        side = visual.get('side')
        if side not in ('ally', 'enemy'):
            raise ValueError('Native replay visual unit has an unknown side.')
        roster_index = visual.get('rosterIndex')
        if isinstance(roster_index, bool) or not isinstance(roster_index, int):
            roster_index = sum(1 for row in units if row['side'] == side)
        unit_id = f'{side}:{roster_index}'
        identity = visual.get('identity')
        if isinstance(identity, bool) or not isinstance(identity, int) or identity in units_by_id:
            raise ValueError('Native replay unit identities are missing or duplicated.')
        cell = visual.get('cell')
        if not isinstance(cell, list) or len(cell) != 2:
            raise ValueError('Native replay initial cell is unavailable.')
        if side == 'ally':
            if 'rawIntentOwnIndex' in visual:
                raw_index = visual.get('rawIntentOwnIndex')
            elif visual.get('sourceKind') == 'household-pet-appended':
                raw_index = None
            else:  # Go legacy payloads and pre-R14 Rust traces are roster ordered.
                raw_index = roster_index
            raw = raw_units[raw_index] if isinstance(raw_index, int) and not isinstance(raw_index, bool) \
                and 0 <= raw_index < len(raw_units) else {}
        else:
            raw = {}
        skill_ids = list(raw.get('skills', visual.get('skills') or []))
        levels = list(raw.get('invocationLevels', visual.get('levels') or []))
        if len(skill_ids) != len(levels):
            raise ValueError(f'Native replay skill/level lists disagree for {visual.get("name")}')
        params = _replay_parameter_projection(raw, visual)
        weapon_id = raw.get('weaponId') if side == 'ally' else 0
        if isinstance(weapon_id, bool) or not isinstance(weapon_id, int):
            weapon_id = 0
        record = dict(unitId=unit_id, side=side, rosterIndex=roster_index,
            entityId=identity, name=str(visual.get('name') or unit_id),
            kind='human' if visual.get('human') else 'monster', human=bool(visual.get('human')),
            monsterId=visual.get('monsterId'), weaponId=weapon_id,
            skillIds=skill_ids, invocationLevels=levels, grid=visual.get('grid', 0),
            column=cell[0], row=cell[1], cell=list(cell),
            startHp=_native_number(visual.get('hpCurrent'), (visual.get('hp') or {}).get('raw')),
            startMp=_native_number(visual.get('mpCurrent'), (visual.get('mp') or {}).get('raw')),
            equipment=list(raw.get('equipment') or []) if side == 'ally' else [], parameters=params)
        for key in ('visitor', 'leaderIdentity'):
            if raw.get(key) is not None:
                record[key] = raw[key]
        for key in ('sourceDomain', 'sourceIndex', 'sourceKind', 'rawIntentOwnIndex'):
            if key in visual:
                record[key] = visual[key]
        units.append(record)
        units_by_id[identity] = record


    if not units or any(not isinstance(unit['startHp'], (int, float)) or
                        not isinstance(unit['startMp'], (int, float)) for unit in units):
        raise ValueError('Native replay does not provide start HP/MP for every fighter.')

    events = []
    for raw in trace.get('events') or []:
        identity = raw.get('unit')
        event = dict(seq=len(events), tick=raw.get('tick'), phase='native',
            kind='native:' + str(raw.get('name') or raw.get('kind')),
            nativeEventName=raw.get('name'), nativeKind=raw.get('kind'),
            nativeUnitId=units_by_id[identity]['unitId'] if identity in units_by_id else None,
            nativeArgs=list(raw.get('args') or []))
        if isinstance(event['tick'], bool) or not isinstance(event['tick'], int):
            raise ValueError('Native replay event tick is invalid.')
        events.append(event)

    frames = trace.get('frames') or []
    final = trace.get('finalBattleState') or (frames[-1] if frames else None)
    if not isinstance(final, dict):
        raise ValueError('Native replay has no final full-state record.')
    final_source_units = trace.get('finalUnits') or final.get('units') or []
    final_by_id = {unit.get('identity'): unit for unit in final_source_units}
    if len(final_by_id) != len(units):
        raise ValueError('Native replay final state does not exactly cover the starting roster.')
    final_units = []
    for unit in units:
        state = final_by_id.get(unit['entityId'])
        if not isinstance(state, dict) or not isinstance(state.get('present'), bool):
            raise ValueError(f'Native final state omits {unit["unitId"]}.')
        cell = state.get('cell')
        if not isinstance(cell, list) or len(cell) != 2:
            raise ValueError(f'Native final state has no cell for {unit["unitId"]}.')
        raw_state = state.get('state')
        final_units.append(dict(unitId=unit['unitId'], entityId=unit['entityId'], side=unit['side'],
            rosterIndex=unit['rosterIndex'], hp=state.get('hp'), mp=state.get('mp'), state=raw_state,
            stateName=_STATE_NAMES.get(raw_state),
            commands=state.get('commandCount', state.get('command_count')), cell=list(cell)))

    unit_ids = {identity: unit['unitId'] for identity, unit in units_by_id.items()}
    projected_events = _native_event_projection(trace, unit_ids)
    projected_events = _attach_native_deltas(projected_events, trace, unit_ids)
    own = sum(1 for unit in units if unit['side'] == 'ally')
    enemies = sum(1 for unit in units if unit['side'] == 'enemy')
    encounter_id = scenario['encounterId']
    encounter_entry = next((x for x in catalog.get('encounterTables', {}).get('encounters', [])
                            if x.get('id') == encounter_id), {})
    skill_profiles = {str(x.get('id')): x for x in catalog.get('profiles', {}).get('skills', [])}
    equipment_profiles = {str(x.get('id')): x for x in catalog.get('profiles', {}).get('equipment', [])}
    skill_ids = sorted({x for unit in units for x in unit['skillIds']})
    weapon_ids = sorted({unit['weaponId'] for unit in units if unit['weaponId']})
    replay_catalog = dict(stateNames={str(k): v for k, v in _STATE_NAMES.items()},
        skills={str(i): dict(id=i, motion=skill_profiles[str(i)].get('motion', 0),
            type=skill_profiles[str(i)].get('type', 0), value=skill_profiles[str(i)].get('value', 0),
            minMp=skill_profiles[str(i)].get('minMp', 0), maxMp=skill_profiles[str(i)].get('maxMp', 0),
            requiredEquipType=skill_profiles[str(i)].get('requiredEquipType', -1), source='pinned-native-catalog')
            for i in skill_ids if str(i) in skill_profiles},
        equipment={str(i): dict(id=i, name=equipment_profiles[str(i)].get('name', str(i)),
            type=equipment_profiles[str(i)].get('type', 0), motion=equipment_profiles[str(i)].get('motion', 0),
            shootingRange=equipment_profiles[str(i)].get('shootingRange', 1),
            projectileFlag=equipment_profiles[str(i)].get('projectileFlag', False), source='pinned-native-catalog')
            for i in weapon_ids if str(i) in equipment_profiles})
    return dict(schema='ka-battle-replay-1',
        source=dict(exporter='strategy_native_replay', runner='pinned Go native DLL replay',
                    scenarioSchema='ka-go-raw-scenario-1',
                    note='Frames and raw ABI event words are copied from the selected native trace; event arguments are intentionally not interpreted.'),
        setupSummary=dict(encounterId=encounter_id, defeatCount=scenario.get('defeatCount', 0),
            mathSeed=scenario['mathSeed'], libSeed=scenario['libSeed'], tickLimit=scenario.get('tickLimit'),
            holyHerbStock=scenario.get('holyHerbStock', 0), inputs=list(scenario.get('inputs') or []),
            ownUnitCount=own, enemyUnitCount=enemies,
            prePlacement=sorted((scenario.get('prePlacement') or {}).keys())
                if isinstance(scenario.get('prePlacement') or {}, dict) else [],
            finishPolicy=scenario.get('finishPolicy', 'at-horizon')),
        encounter=dict(encounterId=encounter_id, title=encounter_entry.get('title'),
        level=None, defeatCount=scenario.get('defeatCount', 0),
            followerSelectionDraws=None, formationOrder=None, ownFormationOrder=None,
            enemyMonsterIds=[u['monsterId'] for u in units if u['side'] == 'enemy' and u['monsterId'] is not None]),
        units=units, ticks=trace.get('simulatedTicks', trace.get('ticksCaptured', final.get('tick', 0))), events=projected_events,
        finalState=dict(verdict=final.get('verdict'), battleState=final.get('battleState'),
            battleFrame=final.get('battleFrame'), ticks=final.get('tick'),
            stopReason=trace.get('stoppedAt', 'native-stop'), censored=None,
            prizeCallbacks=report.get('prize_callbacks', 0), mathDraws=report.get('math_draws', 0),
            libDraws=report.get('lib_draws', 0), rngFinalState=None, retainedRewards=None,
            initializationMode='native-prepared-snapshot', units=final_units, cellsDerivedFrom='native full-state frames'),
        catalog=replay_catalog, nativeTraceSummary=dict(schema=trace.get('schema'),
            complete=trace.get('complete'), eventCount=len(trace.get('events') or []),
            stateDeltaCount=len(trace.get('unitStateDeltas') or []),
            omittedTicks=trace.get('omittedTicks', 0), omittedFrames=trace.get('omittedFrames', 0),
            omittedEvents=trace.get('omittedEvents', 0), source=trace.get('source'),
            parity=trace.get('parity')))


def _rust_visual_units(scenario, trace, catalog):
    """Project Rust's captured roster identity/state only; combat remains in the DLL."""
    own = scenario.get('ownUnits') or []
    source_map = trace.get('sourceMapping') or []
    if isinstance(source_map, dict):
        source_rows = [dict(value, identity=value.get('identity', key)) for key, value in source_map.items()
                       if isinstance(value, dict)]
    elif isinstance(source_map, list):
        source_rows = source_map
    else:
        source_rows = []
    source_by_identity = {}
    for mapping in source_rows:
        if not isinstance(mapping, dict):
            continue
        identity = mapping.get('identity', mapping.get('preparedIdentity',
                   mapping.get('preparedFighterIdentity')))
        if isinstance(identity, int) and not isinstance(identity, bool):
            source_by_identity[identity] = mapping
    result = []
    for row in trace.get('initialUnits') or []:
        index = row.get('rosterIndex')
        side = row.get('side')
        if side not in ('ally', 'enemy') or isinstance(index, bool) or not isinstance(index, int):
            raise ValueError('Rust nativeTrace has invalid initial unit roster identity.')
        mapping = source_by_identity.get(row.get('identity'), {})
        if not mapping:
            mapping = {key: row[key] for key in
                       ('rawIntentOwnIndex', 'sourceDomain', 'sourceIndex', 'sourceKind') if key in row}
        raw_index = mapping.get('rawIntentOwnIndex')
        if raw_index is None and 'rawIntentOwnIndex' not in mapping and not mapping.get('sourceKind'):
            raw_index = index if side == 'ally' and index < len(own) else None
        source = own[raw_index] if side == 'ally' and isinstance(raw_index, int) and \
            not isinstance(raw_index, bool) and 0 <= raw_index < len(own) else {}
        fighter_id = row.get('fighterId')
        monster_id = source.get('monsterId') if side == 'ally' else None
        if monster_id is None and mapping.get('monsterId') is not None:
            monster_id = mapping.get('monsterId')
        monster_row = (catalog.get('monsterTable') or {}).get(str(fighter_id)) if side == 'enemy' else None
        if isinstance(monster_row, list) and monster_row and str(monster_row[0]) == str(fighter_id):
            monster_id = fighter_id
        monster_name = monster_row[1] if isinstance(monster_row, list) and len(monster_row) > 1 else None
        name = source.get('name') or mapping.get('name') if side == 'ally' else (monster_name or f'enemy:{index}:{fighter_id}')
        native_parameters = row.get('effectiveParameters') or row.get('parameters') or {}
        hp_param = native_parameters.get('10', native_parameters.get(10, {})) if isinstance(native_parameters, dict) else {}
        mp_param = native_parameters.get('11', native_parameters.get(11, {})) if isinstance(native_parameters, dict) else {}
        result.append(dict(identity=row.get('identity'), side=side, rosterIndex=index, name=name,
            human=bool(row.get('human')), monsterId=monster_id,
            grid=row.get('grid'), cell=row.get('cell'),
            hp={'raw': _native_number(row.get('hp'), hp_param.get('value')),
                'max': _native_number(row.get('hpMax'), hp_param.get('maximum'))},
            mp={'raw': _native_number(row.get('mp'), mp_param.get('value')),
                'max': _native_number(row.get('mpMax'), mp_param.get('maximum'))},
            parameters=native_parameters,
            skills=list(row.get('skillIds') or []),
            levels=list(source.get('invocationLevels') or [None] * len(row.get('skillIds') or [])),
            state=row.get('state'), weaponType=row.get('weaponType'),
            commandCount=row.get('commandCount'), **{key: mapping[key] for key in
                ('sourceDomain', 'sourceIndex', 'sourceKind', 'rawIntentOwnIndex') if key in mapping}))
    return result


def _native_event_projection(trace, unit_ids):
    """Use ka_events' documented raw-KaEvent mapping; retain raw ABI words on every row."""
    import ka_events

    rows = []
    for event in trace.get('events') or []:
        code = event.get('kindCode', event.get('kind'))
        kind = event.get('kindName', event.get('name'))
        unit = event.get('unit')
        args = event.get('args')
        if args is None:
            args = [event.get(key, 0) for key in ('a', 'b', 'c', 'd', 'e')]
        if isinstance(code, bool) or not isinstance(code, int) or len(args) != 5:
            raise ValueError('Native trace has an invalid KaEvent tuple.')
        tick = event.get('tick')
        if isinstance(tick, bool) or not isinstance(tick, int):
            raise ValueError('Native trace has an invalid event tick.')
        raw = (code, unit, *args)
        try:
            normalized = ka_events.normalize_native([raw])
        except AssertionError:
            normalized = []
        name = str(kind or f'kind-{code}')
        if normalized:
            event_code, actor, target, payload = normalized[0]
            actor_id = unit_ids.get(actor)
            target_id = unit_ids.get(target)
            event_row = dict(tick=tick, kind=name, nativeKind=event_code,
                nativeName=name, nativeTuple=list(raw), nativeArgs=list(args))
            def id_field(field, value):
                if value is not None:
                    event_row[field] = value
            if event_code == 1:
                event_row.update(kind='state', old=payload[0], new=payload[1], hp=payload[2])
                event_row['stateName'] = _STATE_NAMES.get(payload[1])
                id_field('targetUnitId', actor_id)
            elif event_code == 2:
                event_row.update(kind='enqueue', skillId=payload[0])
                id_field('casterUnitId', actor_id); id_field('targetUnitId', target_id)
            elif event_code == 3:
                event_row.update(kind='invocation', skillId=payload[0], invocationLevel=payload[1], passed=bool(payload[2]))
                id_field('casterUnitId', actor_id)
            elif event_code == 4:
                event_row.update(kind='animation_request', behavior=payload[0], clip=payload[1])
                id_field('targetUnitId', actor_id)
            elif event_code == 6:
                event_row.update(kind='attack', skillId=None if payload[0] < 0 else payload[0],
                    hit=bool(payload[1]), critical=bool(payload[2]), damage=payload[3])
                id_field('attackerUnitId', actor_id); id_field('targetUnitId', target_id)
            elif event_code == 7:
                event_row.update(kind='mp', skillId=payload[0], amount=payload[1]); id_field('casterUnitId', actor_id)
            elif event_code == 8:
                event_row.update(kind='effect_birth', effectId=actor, type=payload[0], lifetime=payload[1])
            elif event_code == 11:
                event_row.update(kind='heal', skillId=payload[0], amount=payload[1], before=payload[2], after=payload[3])
                id_field('casterUnitId', actor_id); id_field('targetUnitId', target_id)
            elif event_code == 12:
                event_row.update(kind='status_apply', skillId=payload[0], applied=bool(payload[1]), after={'63': payload[2]})
                id_field('casterUnitId', actor_id); id_field('targetUnitId', target_id)
            elif event_code == 13:
                event_row.update(kind='prize', treasureId=payload[0]); id_field('targetUnitId', actor_id)
            elif event_code == 14:
                event_row.update(kind='invoking', beforeCount=payload[0], afterCount=payload[1], appendedSkillId=payload[2])
                id_field('casterUnitId', actor_id)
            elif event_code == 15:
                event_row.update(kind='area_cell', skillId=payload[0], cell=payload[1:3]); id_field('casterUnitId', actor_id)
            elif event_code == 16:
                event_row.update(kind='state_sound', soundId=actor, passed=bool(payload[0]))
            elif event_code == 18:
                event_row.update(kind='body_flight', speed=payload[0], alreadyInFlight=bool(payload[1]))
                id_field('targetUnitId', actor_id)
            elif event_code == 19:
                event_row.update(kind='release', skillId=payload[0], index=payload[1], used=bool(payload[2]))
                id_field('casterUnitId', actor_id); id_field('targetUnitId', target_id)
            elif event_code == 20:
                event_row.update(kind='projectile_launch', projectileId=actor, skillId=payload[0])
                id_field('ownerUnitId', target_id)
            elif event_code == 21:
                event_row.update(kind='revive', skillId=payload[0], state=payload[1], hp=payload[2])
                id_field('targetUnitId', actor_id)
            elif event_code == 22:
                event_row.update(kind='status_text', textCode=payload[0], skillId=payload[1])
                id_field('targetUnitId', actor_id)
            elif event_code == 23:
                event_row.update(kind='attack_batch', attackCount=actor)
            elif event_code == 24:
                event_row.update(kind='cell_change', oldKey=payload[0], cell=payload[1:3])
                id_field('targetUnitId', actor_id)
            elif event_code == 25:
                event_row.update(kind='status_tick', before={'63': payload[0], '64': payload[1]},
                    after={'63': payload[2], '64': payload[3]}, statusPresent=bool(payload[4]))
                id_field('targetUnitId', actor_id)
            elif event_code == 26:
                event_row.update(kind='body_impact'); id_field('targetUnitId', actor_id)
            elif event_code == 27:
                event_row.update(kind='projectile_impact', projectileId=actor, cell=payload)
                id_field('ownerUnitId', target_id)
            elif event_code == 28:
                event_row.update(kind='projectile_cleanup', projectileId=actor, route=payload[0], duration=payload[1])
            elif event_code == 30:
                event_row.update(kind='resource_change', parameter=payload[0], before=payload[1],
                    after=payload[2], maximum=payload[3], sourceItemId=payload[4])
                id_field('targetUnitId', actor_id)
            elif event_code == 31:
                event_row.update(kind='battle_item', itemId=actor, used=bool(payload[0]),
                    percent=payload[1], remaining=payload[2], blocked=bool(payload[3]), targetCount=payload[4])
        else:
            event_row = dict(tick=tick, kind='native:' + name, nativeKind=code,
                             nativeName=name, nativeTuple=list(raw), nativeArgs=list(args))
        rows.append(event_row)
    return rows


def _attach_native_deltas(events, trace, unit_ids):
    deltas = []
    if isinstance(trace.get('unitStateDeltas'), list):
        source = trace['unitStateDeltas']
        for tick_row in source:
            tick = tick_row.get('tick')
            for unit in tick_row.get('units') or []:
                deltas.append((tick, unit))
    elif isinstance(trace.get('frames'), list):
        previous = {}
        for frame in trace['frames']:
            tick = frame.get('tick')
            for unit in frame.get('units') or []:
                ident = unit.get('identity')
                old = previous.get(ident, {})
                fields = {key: unit.get(source) for key, source in
                          (('hp', 'hp'), ('mp', 'mp'), ('state', 'state'), ('cell', 'cell'), ('present', 'present'))
                          if unit.get(source) != old.get(source)}
                if fields:
                    deltas.append((tick, dict(identity=ident, **fields)))
                previous[ident] = unit
    for tick, delta in deltas:
        identity = delta.get('identity')
        if identity not in unit_ids:
            raise ValueError('Native state delta names an identity absent from initialUnits.')
        events.append(dict(tick=tick, kind='native_state_delta', unitId=unit_ids[identity],
                           nativeStateDelta={k: v for k, v in delta.items() if k != 'identity'}))
    events.sort(key=lambda event: (event.get('tick', -1), event.get('kind') == 'native_state_delta'))
    for seq, event in enumerate(events):
        event['seq'] = seq
    return events


def _cpp_replay_events(raw_events, unit_ids):
    """Project C++'s seven-word native tuples through the canonical event decoder."""
    rows = []
    for sequence, raw in enumerate(raw_events):
        if not isinstance(raw, dict):
            raise ValueError('C++ native replay contains a malformed event row.')
        expected_tuple = [raw.get('nativeKind'), raw.get('unit'),
                          *(raw.get(key) for key in ('a', 'b', 'c', 'd', 'e'))]
        if raw.get('rawTuple') != expected_tuple or raw.get('seq') != sequence:
            raise ValueError('C++ native event tuple or sequence differs from its raw captured fields.')
        event = dict(raw)
        event['kindCode'] = raw.get('nativeKind')
        event['kindName'] = raw.get('kind')
        projected = _native_event_projection(dict(events=[event]), unit_ids)
        if len(projected) != 1:
            raise ValueError('C++ native replay event could not be projected exactly once.')
        row = projected[0]
        # Keep engine chronology/phase evidence alongside the normalized event. C++ only
        # marks tick_head where its native emitter is uniquely known; leave other phases null.
        row['seq'] = raw.get('seq')
        row['phase'] = raw.get('phase')
        for key in ('captureStage', 'nativePhase', 'nativePhaseIndex', 'nativePhaseReason'):
            if key in raw:
                row[key] = raw[key]
        rows.append(row)
    return rows


def _cpp_materialize_objects(replay):
    """Fold native sparse object diffs into exact per-tick snapshots."""
    initial = replay.get('initialObjects')
    frames = replay.get('frames')
    if not isinstance(initial, list) or not isinstance(frames, list):
        raise ValueError('C++ replay omits native initial objects or sparse frames.')
    objects = {}
    for item in initial:
        if (not isinstance(item, dict) or isinstance(item.get('id'), bool) or
                not isinstance(item.get('id'), int) or item['id'] in objects):
            raise ValueError('C++ replay has an invalid initial native object row.')
        objects[item['id']] = json.loads(json.dumps(item))
    snapshots = []
    for frame in frames:
        if not isinstance(frame, dict) or not isinstance(frame.get('tick'), int):
            raise ValueError('C++ replay has an invalid native frame row.')
        diffs = frame.get('objects', [])
        if not isinstance(diffs, list):
            raise ValueError('C++ replay object deltas are malformed.')
        for delta in diffs:
            if (not isinstance(delta, dict) or isinstance(delta.get('id'), bool) or
                    not isinstance(delta.get('id'), int)):
                raise ValueError('C++ replay has a malformed native object delta.')
            identity = delta['id']
            old = objects.get(identity)
            if old is None:
                # C++ capture emits newly allocated arena entities as full rows.
                if not all(key in delta for key in ('destroyed', 'present_slots', 'components')):
                    raise ValueError('C++ replay has a partial delta for an unknown native object.')
                objects[identity] = json.loads(json.dumps(delta))
                continue
            for key in ('destroyed', 'present_slots'):
                if key in delta:
                    old[key] = json.loads(json.dumps(delta[key]))
            changes = delta.get('components', {})
            if not isinstance(changes, dict) or not isinstance(old.get('components'), dict):
                raise ValueError('C++ replay native object component delta is malformed.')
            old['components'].update(json.loads(json.dumps(changes)))
        snapshots.append(dict(tick=frame['tick'], objects=[
            json.loads(json.dumps(objects[key])) for key in sorted(objects)]))
    return snapshots


class NativeReplayMixin:
    """Exact replay adapter; coordinator may wire this mixin into NativeOptimizer."""

    def _workflow_rust_replay(self, intent, cid, label, pair, stored, expected):
        manifest = _selected_manifest(expected)
        revision = manifest.get('revision')
        trace_capability = expected.get('capabilities', {}).get('nativeTrace')
        if (manifest.get('language') != 'rust' or not isinstance(revision, str) or not revision or
                manifest.get('executableSha256') != expected['executable']['sha256'] or
                not isinstance(trace_capability, str) or not trace_capability):
            raise ValueError('Selected Rust build has no pinned native-trace capability.')
        for asset in [expected['executable'], expected['kernel'], *expected['staticInputs'].values()]:
            if _file_sha(asset['path']) != asset['sha256']:
                raise ValueError(f'Pinned native replay asset changed: {asset["path"]}')
        if not isinstance(stored.get('report'), dict):
            raise ValueError('Persisted Rust run has no full native report for exact replay comparison.')
        stamp = time.strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex
        folder = self.path.with_suffix(self.path.suffix + '.native-replays') / ('rust-' + stamp)
        output = folder / 'native-output'
        output.mkdir(parents=True, exist_ok=False)
        candidates_path = folder / 'candidates.json'
        candidates_path.write_bytes(_canonical([intent]) + b'\n')
        input_ids_path = folder / 'candidate-source-ids.json'
        input_ids_path.write_bytes(_canonical([cid]) + b'\n')
        config = dict(schema='ka-rust-optimizer-config-v1', kernel=str(expected['kernel']['path']),
            catalog=str(expected['staticInputs']['catalog']['path']), candidates=str(candidates_path),
            output=str(output), executors=1, generations=1, candidatesPerGeneration=1,
            candidateLimit=1, encounterFilter=intent['encounterId'], searchSeed=pair[0],
            seedPairs=[pair], mechanicsProvenance=dict(engineSha256=expected['executable']['sha256'],
                kernelSha256=expected['kernel']['sha256'],
                staticInputs={k: v['sha256'] for k, v in expected['staticInputs'].items()}),
            policyProvenance=dict(schema='native-replay-policy-v1', purpose='strategy-evaluate',
                finishPolicies=[intent.get('finishPolicy', 'at-horizon')], seedPairs=[pair]),
            executionMode=self._execution_mode, candidateSourceIds=[cid], replayTrace=True,
            resultSyncBatchSize=1)
        config_path = folder / 'config.json'
        config_bytes = json.dumps(config, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8') + b'\n'
        config_path.write_bytes(config_bytes)
        (folder / 'request.json').write_bytes(_canonical(dict(schema='ka-native-rust-replay-request-1',
            configSha256=_sha(config_bytes), candidateId=cid, intent=intent, seeds=pair,
            executableSha256=expected['executable']['sha256'], kernelSha256=expected['kernel']['sha256'])) + b'\n')
        process = subprocess.run([str(expected['executable']['path']), str(config_path)], cwd=_ROOT,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=240,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0, check=False)
        (folder / 'stdout.log').write_bytes(process.stdout)
        (folder / 'stderr.log').write_bytes(process.stderr)
        if process.returncode:
            raise RuntimeError(f'Pinned Rust {revision} replay exited {process.returncode}: ' +
                process.stderr.decode('utf-8', 'replace')[-2000:])
        ledger = output / 'battles.jsonl'
        records = [json.loads(line) for line in ledger.read_text(encoding='utf-8').splitlines() if line.strip()]
        record = next((r for r in records if r.get('recordKind') == 'battle' and
                       r.get('trialIntent') == intent and r.get('mathSeed') == pair[0] and
                       r.get('libSeed') == pair[1] and r.get('inputSourceCandidateId') == cid), None)
        if not isinstance(record, dict) or not isinstance(record.get('report'), dict):
            raise ValueError(f'Rust {revision} emitted no durable battle trace for the exact candidate and seeds.')
        report = record['report']
        trace = report.get('nativeTrace') or {}
        parity = trace.get('parity') or {}
        if (trace.get('schema') != 'ka-native-tick-trace-1' or trace.get('complete') is not True or
                trace.get('truncated') is not False or parity.get('nativeStatus') != 0 or
                parity.get('reportMatches') is not True or parity.get('checksumMatches') is not True):
            raise ValueError(f'Rust {revision} did not certify complete full-tick report and checksum parity.')
        stored_report = stored['report']
        for key in ('report', 'encounterReport', 'cloneChecksumAfter'):
            if report.get(key) != stored_report.get(key):
                raise ValueError(f'Rust native replay {key} differs from the exact durable run.')
        if parity.get('expectedReport') != parity.get('traceReport'):
            raise ValueError('Rust trace report payload differs from its selected DLL report.')
        if trace.get('source', {}).get('kernelSha256') != expected['kernel']['sha256']:
            raise ValueError('Rust trace provenance does not match the selected pinned kernel.')
        catalog = _read_json(expected['staticInputs']['catalog']['path'])
        visuals = _rust_visual_units(intent, trace, catalog)
        replay = _summary_payload(intent, visuals, trace, report.get('report') or {}, catalog)
        replay['nativeProvenance'] = dict(language='rust', revision=revision,
            executableSha256=expected['executable']['sha256'], kernelSha256=expected['kernel']['sha256'],
            traceSchema=trace['schema'], reportMatches=True, checksumMatches=True)
        from strategy_optimizer_desktop import _canonical_formation, replay_visual_setup
        visual = replay_visual_setup(intent, replay)
        replay['visualSetup'] = visual['visualSetup']
        if len(_canonical(replay)) > _MAX_REPLAY_BYTES:
            raise ValueError('Projected native replay exceeds the original 32 MiB response limit.')
        sidecar = dict(schema='ka-native-replay-artifact-1', requestSha256=_file_sha(folder / 'request.json'),
            journalSha256=_file_sha(ledger), executableSha256=expected['executable']['sha256'],
            kernelSha256=expected['kernel']['sha256'], revision=revision, candidateId=cid, seeds=pair,
            replayComplete=True, reportMatches=True, checksumMatches=True, capturedAt=time.time())
        temp = folder / 'artifact.json.tmp'
        temp.write_bytes(_canonical(sidecar) + b'\n')
        os.replace(temp, folder / 'artifact.json')
        replay['nativeArtifact'] = dict(directory=str(folder), metadata=sidecar)
        return dict(ok=True, replay=replay, scenario=intent, formation=_canonical_formation(intent),
            visualSetup=visual['visualSetup'], jobIdentity=visual['jobIdentity'], warnings=visual['warnings'],
            candidateId=cid, label=label, mathSeed=pair[0], libSeed=pair[1], seeds=pair,
            nativeArtifact=str(folder))

    def _workflow_cpp_replay(self, intent, cid, label, pair, stored, expected):
        manifest = _selected_manifest(expected)
        revision = manifest.get('revision')
        if (manifest.get('language') != 'cpp' or not isinstance(revision, str) or not revision or
                manifest.get('executableSha256') != expected['executable']['sha256'] or
                manifest.get('kernelSha256') != expected['kernel']['sha256'] or
                expected.get('capabilities', {}).get('replay') is not True):
            raise ValueError('Selected C++ build has no pinned native replay capability.')
        for asset in [expected['executable'], expected['kernel'], *expected['staticInputs'].values()]:
            if _file_sha(asset['path']) != asset['sha256']:
                raise ValueError(f'Pinned C++ replay asset changed: {asset["path"]}')
        stored_result = stored.get('result')
        if not isinstance(stored_result, dict):
            raise ValueError('Persisted C++ run has no raw native result for exact replay comparison.')
        exact_fields = ('report', 'rawReportBytes', 'encounterReport', 'rawEncounterReportBytes', 'nativeChecksum')
        missing = [key for key in exact_fields if key not in stored_result]
        if missing:
            raise ValueError('Persisted C++ run lacks exact raw comparison fields: ' + ', '.join(missing))

        stamp = time.strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex
        folder = self.path.with_suffix(self.path.suffix + '.native-replays') / ('cpp-' + stamp)
        folder.mkdir(parents=True, exist_ok=False)
        raw_path = folder / 'raw-scenario.json'
        tables_path = Path(expected['staticInputs']['catalog']['path'])
        result_path = folder / 'native-result.json'
        raw_bytes = _canonical(intent) + b'\n'
        raw_path.write_bytes(raw_bytes)
        request = dict(schema='ka-native-cpp-replay-request-1', candidateId=cid,
            intent=intent, seeds=pair, revision=revision,
            executableSha256=expected['executable']['sha256'], kernelSha256=expected['kernel']['sha256'],
            tablesSha256=expected['staticInputs']['catalog']['sha256'])
        request_bytes = _canonical(request) + b'\n'
        (folder / 'request.json').write_bytes(request_bytes)
        command = [str(expected['executable']['path']), '--replay', str(raw_path), str(tables_path),
                   str(expected['kernel']['path']), str(pair[0]), str(pair[1]), str(result_path)]
        process = subprocess.run(command, cwd=_ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=240, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0, check=False)
        (folder / 'stdout.log').write_bytes(process.stdout)
        (folder / 'stderr.log').write_bytes(process.stderr)
        if process.returncode:
            raise RuntimeError(f'Pinned C++ {revision} replay exited {process.returncode}: ' +
                process.stderr.decode('utf-8', 'replace')[-2000:])
        raw_result = result_path.read_bytes()
        if len(raw_result) > _MAX_REPLAY_BYTES:
            raise ValueError('C++ native replay exceeds the original 32 MiB replay limit.')
        result = json.loads(raw_result)
        if result.get('ok') is not True or result.get('originalIntent') != intent:
            raise ValueError('C++ native replay did not preserve the exact requested candidate intent.')
        if result.get('rawScenario') != intent:
            raise ValueError('C++ native replay raw scenario differs from the exact candidate and seed pair.')
        provenance = result.get('provenance') or {}
        if (provenance.get('implementationLanguage') != 'cpp' or
                provenance.get('actualExecutableSha256') != expected['executable']['sha256'] or
                provenance.get('currentKernelSha256') != expected['kernel']['sha256'] or
                provenance.get('tablesSha256') != expected['staticInputs']['catalog']['sha256']):
            raise ValueError('C++ replay provenance does not match the selected pinned build assets.')
        for key in exact_fields:
            if result.get(key) != stored_result.get(key):
                raise ValueError(f'C++ native replay {key} differs from the exact durable run.')
        replay = result.get('replay')
        if (not isinstance(replay, dict) or replay.get('schema') != 'ka-battle-replay-1' or
                replay.get('traceComplete') is not True):
            raise ValueError('C++ native capture is incomplete or has an unsupported replay schema.')
        if (replay.get('source', {}).get('runner') != 'ka_native_full_tick' or
                replay.get('source', {}).get('exporter') != 'C++ native replay capture'):
            raise ValueError('C++ replay was not exported from the pinned native full-tick capture path.')
        setup = replay.get('setupSummary') or {}
        if (setup.get('mathSeed') != pair[0] or setup.get('libSeed') != pair[1] or
                setup.get('encounterId') != intent.get('encounterId') or
                setup.get('defeatCount', 0) != intent.get('defeatCount', 0)):
            raise ValueError('C++ native replay setup differs from the exact candidate and seed pair.')
        trace_summary = replay.get('nativeTrace') or {}
        if (trace_summary.get('eventCount') != len(replay.get('events') or []) or
                isinstance(trace_summary.get('capturedTickCount'), bool) or
                not isinstance(trace_summary.get('capturedTickCount'), int) or
                trace_summary.get('framesAreSparseDeltas') is not True):
            raise ValueError('C++ native capture does not certify its event and sparse-frame counts.')
        units = replay.get('units')
        if not isinstance(units, list) or not units:
            raise ValueError('C++ native replay has no captured fighter roster.')
        unit_ids = {}
        for unit in units:
            identity, unit_id = unit.get('entityId'), unit.get('unitId')
            if isinstance(identity, bool) or not isinstance(identity, int) or not isinstance(unit_id, str):
                raise ValueError('C++ native replay has an invalid fighter identity mapping.')
            if identity in unit_ids:
                raise ValueError('C++ native replay has duplicate fighter identities.')
            unit_ids[identity] = unit_id
        raw_events = replay.get('events')
        if not isinstance(raw_events, list):
            raise ValueError('C++ native replay event log is unavailable.')
        replay['events'] = _cpp_replay_events(raw_events, unit_ids)
        replay['nativeObjectsByTick'] = _cpp_materialize_objects(replay)
        replay['nativeProvenance'] = dict(language='cpp', revision=revision,
            executableSha256=expected['executable']['sha256'], kernelSha256=expected['kernel']['sha256'],
            traceComplete=True, eventCount=len(raw_events), capturedTickCount=trace_summary['capturedTickCount'],
            reportMatches=True, encounterReportMatches=True, rawCoreMatches=True, checksumMatches=True)
        from strategy_optimizer_desktop import _canonical_formation, replay_visual_setup
        visual = replay_visual_setup(intent, replay)
        replay['visualSetup'] = visual['visualSetup']
        if len(_canonical(replay)) > _MAX_REPLAY_BYTES:
            raise ValueError('Projected C++ native replay exceeds the original 32 MiB response limit.')
        sidecar = dict(schema='ka-native-replay-artifact-1', requestSha256=_sha(request_bytes),
            rawScenarioSha256=_sha(raw_bytes), resultSha256=_sha(raw_result),
            executableSha256=expected['executable']['sha256'], kernelSha256=expected['kernel']['sha256'],
            tablesSha256=expected['staticInputs']['catalog']['sha256'], revision=revision,
            candidateId=cid, seeds=pair, replayComplete=True, rawCoreMatches=True,
            encounterReportMatches=True, checksumMatches=True, capturedAt=time.time())
        (folder / 'native-replay.json').write_bytes(raw_result)
        temp = folder / 'artifact.json.tmp'
        temp.write_bytes(_canonical(sidecar) + b'\n')
        os.replace(temp, folder / 'artifact.json')
        replay['nativeArtifact'] = dict(directory=str(folder), metadata=sidecar)
        return dict(ok=True, replay=replay, scenario=intent, formation=_canonical_formation(intent),
            visualSetup=visual['visualSetup'], jobIdentity=visual['jobIdentity'], warnings=visual['warnings'],
            candidateId=cid, label=label, mathSeed=pair[0], libSeed=pair[1], seeds=pair,
            nativeArtifact=str(folder))

    def _workflow_simulate_strategy_visual(self, candidate_id=None, holder_key=None):
        """Persist one fresh native evaluation, then replay its exact durable seed pair."""
        run = self._workflow_run_strategy(candidate_id=candidate_id, holder_key=holder_key, count=1)
        if not isinstance(run, dict) or not run.get('ok'):
            return run if isinstance(run, dict) else {'ok': False, 'error': 'Native visual evaluation returned no result.'}
        trial_rows = run.get('trialRuns') or []
        if len(trial_rows) != 1 or not isinstance(trial_rows[0], dict):
            return {'ok': False, 'error': 'Fresh native evaluation did not return one durable trial row.',
                    'candidateId': run.get('candidateId'), 'trialRuns': trial_rows,
                    'batchSummary': run.get('batchSummary')}
        pair = trial_rows[0].get('seeds')
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            return {'ok': False, 'error': 'Fresh native trial has no exact persisted seed pair.',
                    'candidateId': run.get('candidateId'), 'trialRuns': trial_rows,
                    'batchSummary': run.get('batchSummary')}
        replay = self._workflow_replay(candidate_id=run.get('candidateId'), seeds=pair)
        if not isinstance(replay, dict) or not replay.get('ok'):
            failure = dict(run)
            failure.update(ok=False, error=(replay or {}).get('error', 'Native replay failed after the sample was durably recorded.'),
                           freshSamplePersisted=True, seeds=list(pair),
                           replayError=replay)
            return failure
        result = dict(run)
        result.update(replay)
        # Keep the durable detail/trial response returned by the native evaluator together with
        # the replay payload; replay fields describe the same exact stored seed pair.
        result.update(trialRuns=trial_rows, batchSummary=run.get('batchSummary'),
                      trialSummary=run.get('trialSummary'), freshSamplePersisted=True,
                      seeds=list(pair), mathSeed=pair[0], libSeed=pair[1])
        return result

    def _workflow_replay(self, candidate_id=None, holder_key=None, seeds=None):
        lock = getattr(self, '_job_lock', None)
        if lock is None or not lock.acquire(blocking=False):
            return {'ok': False, 'error': 'A native evaluation/search wave is active; replay will not overlap it.'}
        queue_lock = None
        folder = None
        try:
            if self.language not in ('go', 'rust', 'cpp'):
                raise ValueError('Exact UI replay is unavailable for the selected native engine.')
            status = self.status()
            if status.get('state') in ('Running', 'Saving', 'Opening library'):
                raise ValueError('Pause the native search and wait for its durable wave to finish before replay.')
            scenario, cid, label, holder = self._workflow_target(candidate_id, holder_key)
            if seeds is None:
                if holder_key is not None:
                    holder_pair = holder.get('seeds') if isinstance(holder, dict) else None
                    if holder_pair is None and isinstance(holder, dict):
                        holder_pair = [holder.get('mathSeed'), holder.get('libSeed')]
                    if holder_pair is None:
                        holder_pair = [scenario.get('mathSeed'), scenario.get('libSeed')]
                    seeds = holder_pair
                else:
                    # Select the newest persisted production/evidence row and report its exact pair.
                    detail_for_seed = self._workflow_detail(candidate_id=cid) if cid else None
                    candidates = (detail_for_seed or {}).get('storedRuns', [])
                    chosen = next((row for row in reversed(candidates)
                        if isinstance(row, dict) and row.get('strategyEvidence') is True
                        and row.get('diagnostic') is not True
                        and isinstance(row.get('seeds'), (list, tuple))
                        and len(row['seeds']) == 2), None)
                    if chosen is None:
                        raise ValueError('Replay requires an exact seed pair; this candidate has no eligible durable native run.')
                    seeds = chosen['seeds']
            if not isinstance(seeds, (list, tuple)) or len(seeds) != 2 or any(
                    isinstance(x, bool) or not isinstance(x, int) or not 0 <= x < 2**31 for x in seeds):
                raise ValueError('Replay requires the exact recorded [mathSeed, libSeed] pair.')
            pair = [int(seeds[0]), int(seeds[1])]
            detail = self._workflow_detail(candidate_id=cid) if cid else None
            if not isinstance(detail, dict) or not detail.get('ok'):
                raise ValueError('Replay target must be a persisted native candidate with a durable run row.')
            stored = next((r for r in detail.get('storedRuns', []) if r.get('seeds') == pair), None)
            if not isinstance(stored, dict):
                raise ValueError('That exact seed pair is not present in this candidate’s durable native runs.')
            intent = dict(scenario, mathSeed=pair[0], libSeed=pair[1])
            expected = self._engine_assets
            import language_test_queue as queue
            queue_lock = queue.locked()
            active = [r for r in queue.load().get('requests', []) if r.get('status') == 'active']
            if active:
                raise RuntimeError('Another serialized native check is active; replay will not overlap it.')
            if self.language == 'rust':
                return self._workflow_rust_replay(intent, cid, label, pair, stored, expected)
            if self.language == 'cpp':
                return self._workflow_cpp_replay(intent, cid, label, pair, stored, expected)
            if isinstance(stored.get('result'), dict):
                stored = stored['result']
            for field in ('report', 'encounterReport', 'checksumAfter'):
                if field not in stored:
                    raise ValueError(f'The persisted Go run lacks {field}; it cannot verify an exact replay.')
            template = _go_template(intent['encounterId'])
            if expected.get('source') != 'finishing-manifest' or not expected.get('capabilities', {}).get('visualReplay'):
                raise ValueError('Selected Go manifest does not declare native visual replay.')
            manifest = _selected_manifest(expected)
            if manifest.get('language') != 'go' or manifest.get('executableSha256') != expected['executable']['sha256']:
                raise ValueError('Selected Go build manifest is inconsistent; refusing replay.')
            for asset in [expected['executable'], expected['kernel'], *expected['staticInputs'].values()]:
                if _file_sha(asset['path']) != asset['sha256']:
                    raise ValueError(f'Pinned native replay asset changed: {asset["path"]}')
            mechanics = {'templateMechanics': template.get('mechanicsIdentity'),
                'executableSha256': expected['executable']['sha256'],
                'kernelSha256': expected['kernel']['sha256'],
                'staticInputs': {k: v['sha256'] for k, v in expected['staticInputs'].items()}}
            request = dict(schema='ka-go-replay-request-1', tables=template['tables'],
                kernelPath=str(expected['kernel']['path']), kernelSha256=expected['kernel']['sha256'],
                mechanicsIdentity=mechanics, intent=intent, seeds=pair,
                trace=dict(everyTicks=1, maxEvents=250000, maxFrames=30000))
            root = self.path.with_suffix(self.path.suffix + '.native-replays')
            folder = root / (time.strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex)
            folder.mkdir(parents=True, exist_ok=False)
            request_path = folder / 'request.json'
            request_bytes = json.dumps(request, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8') + b'\n'
            request_path.write_bytes(request_bytes)
            output = folder / 'native-output'
            command = [str(expected['executable']['path']), '--mode', 'replay', '--input', str(request_path),
                       '--output', str(output), '--executors', '1']
            process = subprocess.run(command, cwd=_ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=180, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0, check=False)
            (folder / 'stdout.log').write_bytes(process.stdout)
            (folder / 'stderr.log').write_bytes(process.stderr)
            if process.returncode:
                raise RuntimeError(f'Pinned Go replay exited {process.returncode}: ' +
                    process.stderr.decode('utf-8', 'replace')[-2000:])
            raw = (output / 'replay.json').read_bytes()
            if len(raw) > _MAX_REPLAY_BYTES:
                raise ValueError('Native trace exceeds the original 32 MiB replay limit.')
            native = json.loads(raw)
            native_status = _read_json(output / 'status.json')
            if native.get('schema') != 'ka-go-native-trace-1' or native_status.get('traceComplete') is not True:
                raise ValueError('Native replay trace is incomplete or has an unsupported schema.')
            if native.get('intent') != intent or native.get('seeds') != pair:
                raise ValueError('Native replay changed the exact target intent or seed pair.')
            build = native.get('build') or {}
            if (build.get('executableSha256') != expected['executable']['sha256'] or
                    Path(build.get('executablePath', '')).resolve() != Path(expected['executable']['path']).resolve()):
                raise ValueError('Native replay provenance does not match the selected pinned executable.')
            if (native.get('kernel') or {}).get('sha256') != expected['kernel']['sha256']:
                raise ValueError('Native replay provenance does not match the selected pinned kernel.')
            result = native.get('result') or {}
            trace = result.get('trace') or {}
            if (trace.get('complete') is not True or trace.get('everyTicks') != 1 or
                    trace.get('omittedTicks') or trace.get('omittedFrames') or trace.get('omittedEvents') or
                    trace.get('omittedEventsUnknown')):
                raise ValueError('Native trace has omitted ticks, frames or events; refusing incomplete replay.')
            for field in ('report', 'encounterReport', 'checksumAfter'):
                if result.get(field) != stored.get(field):
                    raise ValueError(f'Replay {field} differs from the exact durable native run; refusing mismatch.')
            catalog = _read_json(expected['staticInputs']['catalog']['path'])
            replay = _summary_payload(intent, native.get('visualUnits') or [], trace,
                                      trace.get('finalReport') or result.get('report') or {}, catalog)
            from strategy_optimizer_desktop import _canonical_formation, replay_visual_setup
            visual = replay_visual_setup(intent, replay)
            replay['visualSetup'] = visual['visualSetup']
            if len(_canonical(replay)) > _MAX_REPLAY_BYTES:
                raise ValueError('Projected native replay exceeds the original 32 MiB response limit.')
            sidecar = dict(schema='ka-native-replay-artifact-1', requestSha256=_sha(request_bytes),
                nativeReplaySha256=_sha(raw), executableSha256=expected['executable']['sha256'],
                kernelSha256=expected['kernel']['sha256'], candidateId=cid, seeds=pair,
                replayComplete=True, capturedAt=time.time())
            temp = folder / 'artifact.json.tmp'
            temp.write_bytes(_canonical(sidecar) + b'\n')
            os.replace(temp, folder / 'artifact.json')
            # Share only the returned endpoint artifact; raw native replay, request and logs remain durable.
            replay['nativeArtifact'] = dict(directory=str(folder), metadata=sidecar)
            return dict(ok=True, replay=replay, scenario=intent, formation=_canonical_formation(intent),
                visualSetup=visual['visualSetup'], jobIdentity=visual['jobIdentity'], warnings=visual['warnings'],
                candidateId=cid, label=label, mathSeed=pair[0], libSeed=pair[1], seeds=pair,
                nativeArtifact=str(folder))
        except subprocess.TimeoutExpired:
            return {'ok': False, 'error': 'Native replay exceeded 180 seconds; request and partial diagnostics are preserved.'}
        except Exception as exc:
            return {'ok': False, 'error': str(exc), **({'nativeArtifact': str(folder)} if folder else {})}
        finally:
            if queue_lock is not None:
                try:
                    queue_lock.unlink(missing_ok=True)
                except OSError:
                    pass
            lock.release()
