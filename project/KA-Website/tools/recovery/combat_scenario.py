"""Explicit inputs for the research encounter runner, never inferred save data."""
import json
import math
from copy import deepcopy
from combat_runtime_data import load_data
from combat_household_pets import (collect_household_pets,validate_household_declaration,
                                   HouseholdPetsError)
from combat_parameters import HUMAN_TRAINING_PARAMETERS
from combat_initial_state import START_PROFILE_KINDS


class ScenarioError(ValueError):pass


def load_scenario(data):
    data=deepcopy(data)
    if data.get('schema')!='ka-special-combat-research-1':raise ScenarioError('Unsupported scenario schema')
    for key in ('encounterId','defeatCount','mathSeed','libSeed','tickLimit','ownUnits','inputs'):
        if key not in data:raise ScenarioError(f'Missing {key}')
    for key in ('encounterId','defeatCount','mathSeed','libSeed','tickLimit'):
        if type(data[key]) is not int:raise ScenarioError(f'{key} must be an integer')
    if not 0<=data['encounterId']<20 or data['defeatCount']<0 or data['tickLimit']<1:raise ScenarioError('Invalid encounter, defeat count or tick limit')
    if not data['ownUnits']:raise ScenarioError('Explicit own roster required')
    if 'prePlacement' in data:
        if not isinstance(data['prePlacement'],dict):raise ScenarioError('prePlacement must map fighter names to source state')
        for pre in data['prePlacement'].values():
            if not isinstance(pre,dict) or set(pre)!={'cell','position','offset','board','longBoard'}:
                raise ScenarioError('prePlacement requires cell, position, offset, board and longBoard')
            for key,length in (('cell',2),('position',3),('offset',3)):
                if not isinstance(pre[key],list) or len(pre[key])!=length or any(type(v) not in (int,float) or not math.isfinite(v) for v in pre[key]):
                    raise ScenarioError('Invalid pre-placement vector')
            if any(type(v) is not int for v in pre['cell']):raise ScenarioError('Pre-placement cells must be integers')
            for key in ('board','longBoard'):
                if not isinstance(pre[key],dict):raise ScenarioError('Pre-placement boards must be mappings')
                try:pre[key]={int(k):v for k,v in pre[key].items()}
                except (ValueError,TypeError):raise ScenarioError('Pre-placement board keys must be integer IDs')
                if any(type(v) is not int for v in pre[key].values()):raise ScenarioError('Pre-placement board values must be integers')
            if not {4,5,6,7,8}<=pre['board'].keys():raise ScenarioError('Pre-placement board must supply keys4/5/6/7/8')
            # InitFighters recomputes position from the placed cell and discards whatever the source
            # board held, so any finite supplied triplet describes a legal pre-placement state.
            # Accepting it (rather than demanding i32(cell)*24) avoids rejecting legal game state that
            # native simply normalizes; initialize_fighter_teams overwrites it with the native value.
    # Optional house lookup output: every selected human house owner must supply the COMPLETE ordered
    # household list ([] for an explicit "no pets"). combat_household_pets owns the pure native rule;
    # there is no save importer, so an absent entry fails closed instead of being read as "no pets".
    # Append after selected members, in selected-owner order; never sort, cap, filter or dedup.
    selected=list(data['ownUnits'])
    try:
        declared=validate_household_declaration(selected,data.get('housePets',{}),
                                                data.get('householdOwners',()))
        data['ownUnits'].extend(collect_household_pets(selected,data.get('housePets',{}),
                                                         data.get('householdOwners',())))
    except HouseholdPetsError as error:
        raise ScenarioError(str(error))
    if declared:data['householdOwners']=declared # survive re-normalization of zero-pet owners
    data.pop('housePets',None) # Normalized scenarios must not append pets twice.
    profiles=load_data('weapon-skill-profiles.json')
    skills={s['id']:s for s in profiles['skills']}
    equipment={s['id']:s for s in profiles['equipment']}
    for pre in data.get('prePlacement',{}).values():
        present={k for k in (62,63,64) if k in pre['board']}
        if present:
            board=pre['board']
            if present!={62,63,64}:raise ScenarioError('Starting status requires all three native fields62/63/64')
            if board[62] not in skills or skills[board[62]]['type'] not in (66,67):
                raise ScenarioError('Only supplied defense-down/sleep status is integrated')
            if any(not -(2**31)<=board[k]<2**31 for k in present):
                raise ScenarioError('Status fields exceed signed32 storage')
    if 'startProfile' in data:
        if 'prePlacement' in data:
            raise ScenarioError('Supply either captured prePlacement or a declared startProfile, not both')
        profile=data['startProfile']
        if not isinstance(profile,dict):raise ScenarioError('startProfile must be a mapping')
        unknown=set(profile)-{'kind','enemySpawnCell','bossCell','startingStatus','note'}
        if unknown:raise ScenarioError('Unknown startProfile field(s): '+', '.join(sorted(unknown)))
        if profile.get('kind') not in START_PROFILE_KINDS:
            raise ScenarioError('Unknown startProfile kind')
        for key in ('enemySpawnCell','bossCell'):
            if key in profile and profile[key] is not None:
                value=profile[key]
                if not isinstance(value,list) or len(value)!=2 or any(type(v) is not int for v in value):
                    raise ScenarioError(f'startProfile {key} must be an [x,y] integer pair')
        status=profile.get('startingStatus',{})
        if not isinstance(status,dict):raise ScenarioError('startingStatus must map fighter names to [skillId,turns]')
        for name,entry in status.items():
            if not isinstance(entry,(list,tuple)) or len(entry)!=2 or any(type(v) is not int for v in entry):
                raise ScenarioError('startingStatus entries must be [skillId,turns] integer pairs')
            skill_id,turns=entry
            if skill_id not in skills or skills[skill_id]['type'] not in (66,67):
                raise ScenarioError('Only supplied defense-down/sleep status is integrated')
            if not 1<=turns<2**31:raise ScenarioError('Starting status turns must be a positive signed32 value')
    # Explicit finish policy, all three yield-accounting conditions are declared, never inferred:
    #   * `at-horizon`   - legacy default and the supported/recommended yield default: every native
    #                      update runs to the supplied tick horizon and Finish is dispatched there.
    #   * `after-ending` - practical native-gated policy: every native update keeps running past the
    #                      verdict until the recovered Ending confirmation window opens (frame>79,
    #                      ENDING_CONFIRM_FRAME ticks after the verdict) and the retained command
    #                      queues settle, then the valid native confirmation runs. Horizon reached
    #                      with commands still queued stays unsettled -> a lower bound, not yield.
    #   * `on-verdict`   - DIAGNOSTIC ONLY: stops at the verdict tick boundary. It is not native
    #                      button timing and it truncates native-eligible post-verdict prizes, so its
    #                      chest count is reported as truncated and excluded from ranking.
    if 'finishPolicy' in data and data['finishPolicy'] not in ('at-horizon','after-ending','on-verdict'):
        raise ScenarioError('finishPolicy must be at-horizon, after-ending or on-verdict')
    names=set()
    for u in data['ownUnits']:
        for key in ('name','human','monsterId','parameters','skills','invocationLevels','weaponId','equipment','visitor','leaderIdentity'):
            if key not in u:raise ScenarioError(f'Unit lacks {key}')
        if u['name'] in names:raise ScenarioError('Unit names must be unique')
        names.add(u['name'])
        if u.get('friend',False):raise ScenarioError('Friends are outside Wairo/Kairo scope')
        if type(u.get('humanFlags',0)) is not int or not -(2**31)<=u.get('humanFlags',0)<2**31:
            raise ScenarioError('humanFlags must fit signed32 storage')
        if u.get('onVehicle',False) or u.get('humanFlags',0)&4:
            raise ScenarioError('Vehicle source components and movement are not integrated')
        if not u['human'] and u.get('humanFlags',0):raise ScenarioError('Monster cannot have Human flags')
        if bool(u['human'])==(u['monsterId'] is not None):raise ScenarioError('Unit must be exactly Human or Monster')
        u['parameters']={int(k):v for k,v in u['parameters'].items()}
        required=HUMAN_TRAINING_PARAMETERS if u['human'] else (10,11,13,14,15,16,19)
        if any(p not in u['parameters'] for p in required):raise ScenarioError('Missing raw/training parameters; displayed stats alone are insufficient')
        for p in u['parameters'].values():
            for field in ('rawValue','rawMax','extraValue','extraMax','trainingLevel'):
                if field not in p or type(p[field]) is not int:raise ScenarioError(f'Missing integer parameter {field}')
                if not -(2**31)<=p[field]<2**31:raise ScenarioError('Parameter exceeds signed32 storage')
        if len(u['skills'])!=len(u['invocationLevels']):raise ScenarioError('Every slot needs an invocation setting')
        for sid,level in zip(u['skills'],u['invocationLevels']):
            if sid not in skills or level not in (0,1,2):raise ScenarioError('Unknown skill or invocation setting')
        # SkillComponent.invokingSkills is copied verbatim by Entity.Clone (the clone is a
        # ByteArrayOutputStream/InputStream serialize/deserialize round trip) and InitFighters
        # never reaches SkillComponent, so in-flight invocations are an explicit source input,
        # never a value the runner may reset or invent.
        if 'invokingSkills' in u:
            if not isinstance(u['invokingSkills'],list):raise ScenarioError('invokingSkills must be a list of [skillId,count] pairs')
            for entry in u['invokingSkills']:
                if not isinstance(entry,(list,tuple)) or len(entry)!=2 or any(type(v) is not int for v in entry):
                    raise ScenarioError('invokingSkills entries must be [skillId,count] integer pairs')
                if entry[0] not in skills:raise ScenarioError('Unknown invoking skill id')
                if not 1<=entry[1]<2**31:raise ScenarioError('Invoking count must be a positive signed32 value')
        # Raw parameters cannot be relabelled effective equipment-adjusted stats.
        for slot in u['equipment']:
            if set(slot)!={'id','level','affinity'}:raise ScenarioError('Equipment needs explicit id, level and recovered/captured affinity')
            if type(slot['id']) is not int or slot['id'] not in equipment:raise ScenarioError('Unknown equipment')
            if type(slot['level']) is not int or slot['level']<1:raise ScenarioError('Equipment level must be positive integer')
            if type(slot['affinity']) not in (int,float) or not math.isfinite(slot['affinity']):raise ScenarioError('Equipment affinity must be finite numeric')
        if type(u['weaponId']) is not int or u['weaponId'] not in equipment:raise ScenarioError('Unknown weapon')
        if equipment[u['weaponId']]['category']!=0:raise ScenarioError('Weapon ID is not a weapon')
        if u['weaponId']!=0 and u['weaponId'] not in [s['id'] for s in u['equipment']]:raise ScenarioError('Weapon must be included in equipment contributions')
        if u.get('parameterLinks',[]):raise ScenarioError('Linked parameter maxima are not yet integrated in this runner')
    # Optional explicit item definitions and their finite counts. An item row is classified by the
    # native ExecuteItem 0x167b834 tables (bonusCategory/bonusType); anything else fails closed.
    items=data.get('items',{})
    if not isinstance(items,dict):raise ScenarioError('items must map an explicit name to an item row')
    for name,row in items.items():
        if not isinstance(name,str) or not isinstance(row,dict):raise ScenarioError('Item rows need a name and a mapping')
        for field in ('bonusCategory','bonusType','bonusMinValue','bonusMaxValue'):
            if type(row.get(field)) is not int:raise ScenarioError(f'Item row needs integer {field}')
        if row['bonusCategory']!=3 or row['bonusType'] not in range(6):
            raise ScenarioError('Unsupported item effect; only bonusCategory3 bonusType0..5 recovery items are recovered')
    item_stock=data.get('itemStock',{})
    if not isinstance(item_stock,dict) or any(not isinstance(name,str) or type(count) is not int or count<0 for name,count in item_stock.items()):
        raise ScenarioError('itemStock must map item names to nonnegative integer counts')
    for e in data['inputs']:
        if type(e.get('tick')) is not int or e['tick']<0:raise ScenarioError('Input tick must be nonnegative')
        if e.get('type') not in ('holy_herb','item','finish'):raise ScenarioError('Unsupported explicit input')
        if e.get('phase') not in ('before_fighters','after_fighters'):raise ScenarioError('Input phase must be explicit')
        if e.get('type')=='item':
            if not isinstance(e.get('item'),str) or e['item'] not in items:raise ScenarioError('Item input needs a declared item name')
            if e.get('item') not in item_stock:raise ScenarioError('Item input needs an explicit finite stock entry')
            # Native battle item use (OnTouchEvent 0x14f29b8) dispatches with a null single target and
            # the own-team array filtered by 0x14f34ac, so only the all-residents scope is reachable.
            if e.get('target')!='all':raise ScenarioError('Battle item input target must be the explicit all-residents scope')
    if type(data.get('holyHerbStock')) is not int or data['holyHerbStock']<0:raise ScenarioError('Explicit Holy Herb stock required')
    # The automatic `<=3%` Holy Herb policy and its MP telemetry are experiment configuration, not
    # recovered game data: the game never states a herb quantity, so both values must be declared
    # explicitly and are validated against each other rather than guessed or capped silently.
    #   * `holyHerbStock`     0 -> the herb is unavailable; the policy cannot spend a charge.
    #   * `holyHerbMaxUses`   0 -> the automatic policy is off (MP telemetry still runs if configured).
    #   * `holyHerbTriggerUnits` names the units that may authorise a charge: the DPS and the healer,
    #     because the item tops up the whole living team while the trigger decides who may spend stock
    #     (a fodder dipping must not consume a charge). At most two, each a declared own human.
    max_uses=data.get('holyHerbMaxUses',0)
    if type(max_uses) is not int or max_uses<0:
        raise ScenarioError('holyHerbMaxUses must be a nonnegative integer')
    if max_uses>data['holyHerbStock']:
        raise ScenarioError('holyHerbMaxUses cannot exceed the declared holyHerbStock')
    if max_uses>0 and data['holyHerbStock']<=0:
        raise ScenarioError('holyHerbMaxUses>0 requires a positive holyHerbStock')
    trigger=data.get('holyHerbTriggerUnits')
    if trigger is not None:
        if not isinstance(trigger,list) or any(not isinstance(name,str) for name in trigger):
            raise ScenarioError('holyHerbTriggerUnits must be a list of own-unit names')
        if len(trigger)>2:
            raise ScenarioError('holyHerbTriggerUnits takes at most two units (the DPS and the healer)')
        if len(set(trigger))!=len(trigger):
            raise ScenarioError('holyHerbTriggerUnits must not repeat a unit')
        owners={unit['name'] for unit in data['ownUnits']}
        for name in trigger:
            if name not in owners:
                raise ScenarioError(f'holyHerbTriggerUnits names an own unit that is not declared: {name}')
        by_name={unit['name']:unit for unit in data['ownUnits']}
        for name in trigger:
            if not by_name[name]['human']:
                raise ScenarioError(f'holyHerbTriggerUnits must name human residents: {name}')
    if max_uses>0 and not trigger:
        raise ScenarioError('holyHerbMaxUses>0 needs an explicit holyHerbTriggerUnits declaration')
    # `mpWatchUnits` is the **observation-only** watch list: the units whose MP the observer samples so
    # a low-MP condition can be *noticed*. It authorises nothing - it cannot spend a charge, and a
    # scenario may declare it with `holyHerbStock = 0` and `holyHerbMaxUses = 0` - which is exactly
    # what an ordinary no-item candidate needs in order to report that its healer ran out of MP. It is
    # a separate field from `holyHerbTriggerUnits` so that "watch this unit" and "this unit may spend a
    # charge" can never be confused for one another, and it obeys the same shape rules.
    watch=data.get('mpWatchUnits')
    if watch is not None:
        if not isinstance(watch,list) or any(not isinstance(name,str) for name in watch):
            raise ScenarioError('mpWatchUnits must be a list of own-unit names')
        if len(watch)>2:
            raise ScenarioError('mpWatchUnits takes at most two units (the DPS and the healer)')
        if len(set(watch))!=len(watch):
            raise ScenarioError('mpWatchUnits must not repeat a unit')
        by_name={unit['name']:unit for unit in data['ownUnits']}
        for name in watch:
            if name not in by_name:
                raise ScenarioError(f'mpWatchUnits names an own unit that is not declared: {name}')
            if not by_name[name]['human']:
                raise ScenarioError(f'mpWatchUnits must name human residents: {name}')
    return data

