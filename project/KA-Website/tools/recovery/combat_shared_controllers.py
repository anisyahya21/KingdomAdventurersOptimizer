"""Shared autonomous combat and navigation controllers for research encounters.

Movement uses native map width and empty missing buckets; complete world lifecycle is not integrated.
Unsupported skills fail explicitly. This is a composition engine, not a claimed
faithful full Wairo/Kairo simulator. Front-target overrides are for controlled tests.
"""
from copy import deepcopy
from combat_farmer_slice import ROWS
from combat_entities import CombatEntities,ComponentMap
from combat_effects import tick_modifiers,create_combat_effect,update_effect_phase,attack_effect_specs,skill_balloon_spec,healing_effect_spec,cell_skill_effect_spec,departure_effect_spec
from combat_lifecycle import update_garbage
import math
from combat_initial_state import effective_parameter_rate,initialize_fighter_teams
from combat_parameters import average_training_level, fighter_parameter, equipment_contribution
from combat_parameters import equip_master_lifted_types, equipment_affinity
from combat_commands import enqueue_skill_command
from combat_skill_selection import active_skill_infos, attack_skill_candidates
from combat_skill_use import use_fighter_skill
from combat_skills import buff_entities_on_cell, can_receive_status, can_use_skill, play_skill_sound, update_status
from combat_targeting import nearest_skill_target, opponent_in_skill_cells, damage_cell, can_damage_shared
from combat_geometry import line_cells, battle_band
from combat_shared_resolution import SharedAttackMath, dispatch_shared_attack, execute_shared_skill_commands, target_exists
from combat_resolution import (SystemRandomState, random_below, resolve_attack, defender_attack_phase,
    decide_skill_invocation, skill_mp_cost, attack_interval, cure_result, add_raw_parameter, reflected_attack_result,
    buff_accuracy, buff_turns, apply_fighter_cure_results)
from combat_states import (change_fighter_state, exit_using_skill, enter_using_skill, enter_damaging,
    exit_damaging, update_damaging, tick_charging, tick_using_skill, update_attacking,
    is_normal_attack_target, is_most_front, enter_knocking_down, update_knocking_down,
    enter_leaving_special, update_leaving)
from combat_tick import update_fighters
from combat_ending import is_annihilated, enter_ending,advance_battle_frame,after_ending_confirmation
from combat_collections import ComponentSubset
from combat_projectiles import fire_shared_projectile, tick_projectiles, skill_projectile_destinations,create_projectile_trail,isometric_trail_screen
from combat_spatial import update_positions, update_cells, battle_cell,update_facing,update_heights
from combat_resolution import f32, native_float_to_int, attacker_invocation_phase
from combat_navigation import same_grid, empty_cell, queue_position, front_opponent
from combat_states import decide_next_state, enter_moving, exit_moving, update_moving, move_fighter
from combat_spatial import CellOccupancy, cell_key
from combat_prizes import select_prize
import json
from combat_runtime_data import load_data
from combat_animation_selection import change_combat_animation
from combat_animation import update_animations
EQUIPMENT={r['id']:r for r in load_data('weapon-skill-profiles.json')['equipment']}


class _EquipmentContribution:
    """Reusable `equipment_contribution` callback for one `human` flag.

    `SharedControllers.effective` allocated a fresh lambda on every parameter read, but the
    callback only closes over `spec['human']`; one instance per flag is the same callable
    behaviour without the per-read allocation. `fighter_parameter` invokes it positionally as
    `(row, parameter_id, selected_level)` and the affinity argument is unchanged.
    """
    __slots__=('human',)
    def __init__(self,human):self.human=human
    def __call__(self,row,parameter_id,level):
        return equipment_contribution(row,parameter_id,level,affinity=row['affinity'],human=self.human)


_CONTRIBUTIONS={}
def contribution_callback(human):
    callback=_CONTRIBUTIONS.get(human)
    if callback is None:
        callback=_CONTRIBUTIONS[human]=_EquipmentContribution(human)
    return callback

# Skill types whose recovered effect route is integrated in this runner. Each is backed by a
# native differential, so accepting the type is not a guess.
SUPPORTED_SKILL_TYPES=frozenset((0,1,2,11,15,18,19,20,21,22,23,24,26,27,60))
SUPPORTED_GENERATED_STATUS_TYPES=frozenset((66,67))
# Permanently-active equipment-affinity passives: native SkillData.TYPE_EQUIP_MASTER (48).
# A possessed row whose `value` equals an equipment type lifts that type's declared job
# affinity -1/0 to 1 (JobData.GetAffinity 0x16208d0, predicate 0x162cc34). The rows carry no
# FLAG_FOR_BATTLE, so they are never selected as an active skill; the row stays in the
# possessed list and its only effect is applied where equipment contributions are assembled.
SUPPORTED_PASSIVE_SKILL_TYPES=frozenset((48,))
UNSUPPORTED_SKILL_TYPES=frozenset(range(3,66))-SUPPORTED_SKILL_TYPES-SUPPORTED_PASSIVE_SKILL_TYPES


def skill_unsupported_reason(skill):
    """None when this row's effect route is integrated, else the explicit rejection reason.

    The flags&0x40000 generated-status route is checked first because it is selected by flag,
    not by type; everything outside the two integrated routes is a named unsupported row
    (category-2 noncombat/vehicle passives, unrecovered status types) and never silently ignored.
    """
    if skill['flags']&0x40000:
        if skill['type'] not in SUPPORTED_GENERATED_STATUS_TYPES or skill['category']!=0:
            return f"type {skill['type']} category {skill['category']} has no recovered generated-status route"
        return None
    if skill['type'] in SUPPORTED_SKILL_TYPES:return None
    if skill['type'] in SUPPORTED_PASSIVE_SKILL_TYPES:
        # EQUIP_MASTER is always-on: a row that could be invoked would need an active route
        # this runner does not have, so it fails closed instead of being silently accepted.
        if skill['category']!=2:
            return f"type {skill['type']} category {skill['category']} has no recovered active route"
        if skill['flags']&8:
            return f"type {skill['type']} category {skill['category']} carries the battle flag but has no recovered active route"
        return None
    return f"type {skill['type']} is not in the integrated set (category-2 noncombat/vehicle/passive rows are explicitly unsupported)"


class SharedControllers:
    def __init__(self, specs, math_seed, lib_seed, front_targets=None, row_offset=3, movement_cells=None, movement=False,
                 initialization_orders=None, prize_candidates=None, receipts=None):
        self.projectiles=ComponentSubset((39,0,1));self.moving=ComponentSubset((0,1));self.cells=ComponentSubset((0,5))
        self.modifiers=ComponentSubset((19,));self.garbage=ComponentSubset((32,));self.cell_members=ComponentSubset((5,))
        self.effects=ComponentSubset((38,),(4,))
        self.skill_members=ComponentSubset((51,))
        self.fighter_animations=ComponentSubset((2,12,28))
        self.rotating=ComponentSubset((14,1,2,7))
        self.height_members=ComponentSubset((0,5,1),(11,39,38))
        self.world=CombatEntities(100,[self.projectiles,self.moving,self.cells,self.modifiers,self.cell_members,self.effects,self.garbage,self.skill_members,self.fighter_animations,self.rotating,self.height_members])
        self.animation_resources=[None]*23
        clips=load_data('animation-resources.json')['resources']
        for res,name in ((11,'chara'),(22,'monster')):
            rows=clips[name];resources=[None]*(max(r['id'] for r in rows)+1)
            for r in rows:resources[r['id']]=dict(frame=0,max_frame=r['maxFrame'])
            self.animation_resources[res]=resources
        self.map_width=8
        self.occupancy=CellOccupancy(self.world,self.map_width)
        self.cell_members.on_added=self.occupancy.added;self.cell_members.on_removed=self.occupancy.removed
        self.lifetimes=ComponentMap(self.world,32,scalar=0)
        self.effect_values=ComponentMap(self.world,38,fields=('type','value1','value2','depth','frame','max_frame','parent','scale'))
        self.effect_positions=ComponentMap(self.world,0,window=(0,3))
        self.effect_resources={r['id']:r['maxFrame'] for r in load_data('effect-resource-checks.json')['resources']}
        self.movement_enabled=movement or movement_cells is not None
        if movement_cells is not None:
            for x,y in movement_cells:self.occupancy.buckets.setdefault(cell_key(x,y,self.map_width),[])
        self.projectile_sources={}
        self.units={};self.specs={};self.names={};self.teams=[[],[]]
        self.front_targets=front_targets;self.row_offset=row_offset
        self.trace=[];self.tick=-1;self.phase='initial';self.next_command=0
        self.latest_hit={};self.prizes=[];self.battle_state=2;self.battle_frame=0;self.verdict=None
        # Optional read-only stored-attack observer (`combat_progress.ProgressWatch`). It is notified
        # at the two genuine queue mutation sites and sampled once per tick; when it is None every
        # path below is byte-identical to the recovered engine.
        self.progress=None
        # Optional read-only event-site counter sink (`combat_progress.EncounterCounters`). When it is
        # None every path below is byte-identical to the recovered engine; nothing here is read back
        # by a decision or a random draw.
        self.encounter=None
        self.prize_candidates=prize_candidates
        self.receipts=receipts
        self.math_rng=SystemRandomState(math_seed);self.lib_rng=SystemRandomState(lib_seed)
        self.math_draws=0;self.lib_draws=0
        sequential=any('prePlacement' in s for s in specs)
        if sequential and not all('prePlacement' in s for s in specs):raise ValueError('Pre-placement state must cover every fighter')
        self.initialization_mode='supplied pre-placement state, sequential teams' if sequential else 'final-cell starting approximation'
        for source in specs:
            spec=deepcopy(source)
            if spec['name'] in self.names:raise ValueError('Duplicate fighter name')
            if spec['team'] not in (0,1):raise ValueError('Invalid team')
            if len(spec['skills'])!=len(spec['levels']):raise ValueError('Missing invocation settings')
            if any(level not in (0,1,2) for level in spec['levels']):raise ValueError('Invalid invocation setting')
            if spec.get('parameterLinks'):raise NotImplementedError('Linked parameters remain unsupported')
            if spec.get('onVehicle',False) or spec.get('humanFlags',0)&4:
                raise NotImplementedError('Vehicle source components and movement remain unsupported')
            spec['equipmentRows']=[dict(EQUIPMENT[s['id']],level=s['level'],affinity=s['affinity']) for s in spec.get('equipment',[])]
            lifted=equip_master_lifted_types(ROWS[sid] for sid in spec['skills'])
            spec['equipmentRows']=[dict(EQUIPMENT[s['id']],level=s['level'],
                affinity=equipment_affinity(EQUIPMENT[s['id']]['type'],s['affinity'],lifted))
                for s in spec.get('equipment',[])]
            spec['weapon']=EQUIPMENT[spec.get('weaponId',0)]
            for p in spec['parameters'].values():
                p.setdefault('extraValue',0);p.setdefault('extraMax',0)
            for sid in spec['skills']:
                skill=ROWS[sid]
                reason=skill_unsupported_reason(skill)
                if reason is not None:
                    # ScenarioError (a ValueError) is the actionable validation failure: the
                    # transport answers 422 scenario-rejected with the row named, not a generic 500.
                    from combat_scenario import ScenarioError
                    raise ScenarioError(f"skill {sid} ({skill['nameText']}, type {skill['type']} "
                        f"category {skill['category']}) is not integrated: {reason}")
            identity=self.world.allocate();self.names[spec['name']]=identity;self.specs[identity]=spec
            cell=spec['cell'];grid=spec['grid']
            components=dict(AI=dict(board={4:0,5:1,6:spec['team'],7:grid,8:0},long_board={},commands=[],path=[]),
                Position=[cell[0]*24.,0.,cell[1]*24.,0.,0.,0.,None],Speed=[0.,0.,0.],Cell=list(cell),
                Direction=[0 if spec['team']==0 else 2],Animation=[1,-1],Seb=[11 if spec['human'] else 22,0,0,-1],
                Image=(-1,-1,-1,-1,-1,-1),
                Parameter=dict(parameters=spec['parameters'],extras=[]),
                Skill=dict(dataIds=spec['skills'],invocationLevels=spec['levels'],invokingSkills=spec.get('invokingSkills',[]),maxSlotNum=len(spec['skills'])))
            if sequential:
                pre=spec['prePlacement']
                components['Cell']=list(pre['cell'])
                components['Position']=list(pre['position'])+list(pre['offset'])+[None]
                components['AI']['board']={int(k):v for k,v in pre['board'].items()}
                components['AI']['long_board']={int(k):v for k,v in pre['longBoard'].items()}
            for name,value in components.items():self.world.add_component(identity,name,value)
            self.world.add_component(identity,18 if spec['human'] else 20,dict(flag=spec.get('humanFlags',0)) if spec['human'] else {})
            if spec['team']==0:self.world.add_component(identity,49,{})
            unit=self.world.fighter(identity);self.units[identity]=unit;self.teams[spec['team']].append(unit)
            unit['human']=spec['human'];unit['invisible']=False
        # Preserve native Human/Monster subset grouping for nearest target ties.
        self.target_order=sorted(self.units,key=lambda i:not self.specs[i]['human'])
        self.math=SharedAttackMath(self.world,ROWS,self.value,lambda i:not self.specs[i]['human'],self.next_math,
                                  labelled_next=self.next_math)
        orders=initialization_orders if initialization_orders is not None else [list(range(len(t))) for t in self.teams]
        if sequential:
            placements=[[dict(incomingIndex=index,grid=self.specs[roster[index]['id']]['grid'],cell=self.specs[roster[index]['id']]['cell'])
                for index in order] for roster,order in zip(self.teams,orders,strict=True)]
            def moved(u,old):
                self.occupancy.changed(u['id'],cell_key(*old,self.map_width))
                self.emit('initial_placement',target=u['id'],oldCell=old,cell=list(u['cell']))
            initialize_fighter_teams(self.teams,placements,self.decide,self.change,moved,
                lambda u:None,lambda u:u['commands'].clear(),self.update_human_images)
        else:
            for roster,order in zip(self.teams,orders,strict=True):
                for index in order:
                    u=roster[index];self.change(u,self.decide(u))

    def emit(self,kind,**fields):
        event=dict(id=len(self.trace),tick=self.tick,phase=self.phase,kind=kind,**fields)
        self.trace.append(event);return event['id']

    def update_human_images(self,unit):
        """Recovered InitFighters Human branch, executed for every human placement.

        Native path FighterSystem.InitFighters 0x1582b10: Entity.get_hasHuman 0x146ee7c gates
        AISystem.ChangeWeaponImage 0x148b6cc (arg EquipComponent.get_weapon 0x14c7db0) and
        AISystem.ChangeShieldImage 0x148b764 (arg EquipComponent.get_shield 0x14c7e3c); each writes
        one EquipData image id into the HumanComponent array (+0x4c weapon, +0x50 shield) and tail
        branches to AnimationSet.SetHumanImgs 0x165ea88, which fills HumanComponent +0x30/+0x40 from
        the static ANIMATION_SET and the +0x20 lookup. None of those bodies draws a random number,
        allocates managed memory (only IL2CPP class/method-init guards) or changes subset/occupancy
        membership, so the omitted presentation cannot alter combat outcome. Recorded for receipt.
        """
        spec=self.specs[unit['id']]
        weapon=spec.get('weapon',{})
        shield=next((row for row in spec.get('equipmentRows',[])
                     if EQUIPMENT.get(row['id'],{}).get('category')==2),None)
        unit['images']=dict(weapon=weapon.get('id'),shield=shield['id'] if shield else None)
        self.emit('human_images',target=unit['id'],weaponImage=weapon.get('id'),
                  shieldImage=unit['images']['shield'],rngDraws=0,
                  source='InitFighters0x1582b10 ChangeWeaponImage0x148b6cc ChangeShieldImage0x148b764 SetHumanImgs0x165ea88')

    def next_math(self,purpose='unclassified',bound=None):
        self.math_draws+=1
        value=self.math_rng.next_int()
        self.emit('rng',stream='math',draw=self.math_draws,purpose=purpose,raw=value,bound=bound,
                  reduced=random_below(value,bound) if bound is not None else None)
        return value

    def next_lib(self,purpose='unclassified',bound=None):
        self.lib_draws+=1
        value=self.lib_rng.next_int()
        self.emit('rng',stream='lib',draw=self.lib_draws,purpose=purpose,raw=value,bound=bound,
                  reduced=random_below(value,bound) if bound is not None else None)
        return value

    def param(self,i,p):return self.units[i]['parameters'][p]
    def value(self,i,p,default=0):
        return self.effective(i,p,False,default)
    def effective(self,i,p,maximum,default=0):
        spec=self.specs[i]
        return fighter_parameter(p,self.units[i]['parameters'].get(p),spec['equipmentRows'],
            contribution_callback(spec['human']),
            human=spec['human'],ally=spec['team']==0,maximum=maximum,default=default)
    def maximum(self,i,p):return self.effective(i,p,True)
    def rate(self,i,p):return effective_parameter_rate(self.value(i,p),self.maximum(i,p))
    def cost(self,i,s):
        return skill_mp_cost(s['minMp'],s['maxMp'],average_training_level(self.units[i]['parameters'],self.specs[i]['human']),
                             monster=not self.specs[i]['human'])
    def distance(self,a,b):
        # FighterView['cell'] exposes these exact two fields. Avoid allocating two
        # windows and iterators for every target probe; no cached coordinates.
        left=self.units[a].entity['components'][5];right=self.units[b].entity['components'][5]
        return abs(left[0]-right[0])+abs(left[1]-right[1])
    def same_team(self,a,b):return self.specs[a]['team']==self.specs[b]['team']
    def skill_cells(self,i,s):
        x,y=self.units[i]['cell'];direction=self.units[i]['direction']
        if s['type']==26:
            dx,dy=((0,-1),(1,0),(0,1),(-1,0))[direction%4]
            return line_cells(x,y,dx,dy,s['shootingRange'])
        if s['type']==27:return battle_band(y,direction,s['range'])
        return []
    def opponent_in_range(self,i,s):
        # `self.units`/`self.specs` are written only in `__init__`, so the per-team roster is fixed
        # for the run. Rebuilding the comprehension on every eligibility probe re-derives the same
        # list, so it is built once per team and reused; `is not self.same_team(i,t)` is the exact
        # `specs[t]['team'] != specs[i]['team']` predicate.
        opponents=getattr(self,'_opponents_by_team',None)
        if opponents is None:
            opponents={team:[t for t in self.units if self.specs[t]['team']!=team] for team in (0,1)}
            self._opponents_by_team=opponents
        return opponent_in_skill_cells(opponents[self.specs[i]['team']],self.skill_cells(i,s),
            lambda t:self.units[t]['board'][5],lambda t:self.units[t]['cell'])
    def eligible(self,i,s,t,first=True):
        return can_use_skill(s,check_mp=first,mp=self.value(i,11),cost=self.cost(i,s),
            target_exists=target_exists(self.world,t),weapon_type=self.specs[i]['weapon']['type'],
            target_hp=self.value(t,10) if t in self.units else 0,
            target_hp_rate=self.rate(t,10) if t in self.units else 0,
            invoking=any(sid==s['id'] for sid,count in self.units[i]['invoking']),
            battle_world=True,most_front=is_most_front(self.units[i]['board'][7]),opponent_in_range=self.opponent_in_range(i,s))

    def candidates(self,i):
        infos=active_skill_infos([ROWS[s] for s in self.specs[i]['skills']],self.specs[i]['levels'],
                                 self.value(i,11),lambda s:self.cost(i,s))
        # Supported recovery before attack, preserving input skill order.
        for s,level in infos:
            if s['category']!=1:continue
            # Native recovery selection (0x15de880 reduction, 0x15e2418 predicate): nearest
            # same-team ally below full HP rate wins, including a dead ally. The type-specific
            # healing/resurrection HP rule stays in CanUseSkill; a nearer ineligible ally
            # therefore consumes the selection and leaves no fallback target.
            target=nearest_skill_target(i,self.target_order,s['shootingRange'],self.distance,
                lambda t:self.same_team(i,t) and self.rate(t,10)<100)
            if self.eligible(i,s,target):yield s,level,target
        def attack_target(s):
            return nearest_skill_target(i,self.target_order,s['shootingRange'],self.distance,
                lambda t:not self.same_team(i,t) and self.rate(t,10)>0)
        yield from attack_skill_candidates(infos,attack_target,lambda s,t:self.eligible(i,s,t))

    def invoke(self,i,s,level):
        passed=decide_skill_invocation(s['type'],level,s['flags'],lambda n:random_below(self.next_math('skill_invocation',n),n))
        self.emit('invocation',caster=i,skill=s['id'],level=level,passed=passed)
        return passed
    def choose(self,u):
        for s,level,target in self.candidates(u['id']):
            if self.invoke(u['id'],s,level):return s['id'],target
        return None
    def decide(self,u):
        if self.movement_enabled:
            team=self.teams[u['board'][6]];x,y=u['cell']
            forward=(x,y+(-1 if u['board'][6]==0 else 1))
            occupants=self.occupancy.buckets.get(cell_key(*forward,self.map_width),())
            advance=not is_most_front(u['board'][7]) and empty_cell(occupants,
                lambda i:self.world.objects[i]['components'][28] is not None,
                lambda i:self.world.fighter(i)['board'][5])
            decision=(u['board'][5],u['board'][6],u['cell'],
                is_most_front(u['board'][7]),self.specs[u['id']]['weapon']['shootingRange']>1)
            navigation=dict(same_grid=same_grid(u,team),advanceable=advance,
                queue_position=(queue_position(u,team,lambda other:self.value(other['id'],10),self.row_offset)
                    if u['board'][5] not in (2,7,8) and not 0 <= x < 5 else None))
            # Candidate existence is a pure query (invocation/RNG is in choose/invoke).
            # Only ask when it can change the canonical decision, then use that same helper.
            state,destination=decide_next_state(*decision,False,**navigation)
            if state==1:
                state,destination=decide_next_state(*decision,
                    next(self.candidates(u['id']),None) is not None,**navigation)
            if destination is not None:u['board'][13],u['board'][14]=destination
            return state
        return 3 if is_most_front(u['board'][7]) or self.specs[u['id']]['weapon']['shootingRange']>1 or next(self.candidates(u['id']),None) is not None else 1
    def animate(self,u,m):
        spec=self.specs[u['id']]
        clip=change_combat_animation(u,m,human=spec['human'],hp_rate=self.rate(u['id'],10),
            monster_size=spec.get('monsterSize',0),special_human=spec.get('specialHumanAnimation',False),
            on_vehicle=spec.get('onVehicle',False))
        self.emit('animation_request',target=u['id'],behavior=m,clip=clip,source='state_or_command',
                  implemented='clip/frame/nextBehavior only; Human image composition omitted')
    def body_flight(self,u,speed,start,end):
        occupied=self.world.objects[u['id']]['components'][39] is not None
        fire_shared_projectile(self.world,u['id'],start,end,speed,u['id'])
        self.emit('body_flight',target=u['id'],speed=speed,start=start,end=end,alreadyInFlight=occupied)
    def leave(self,u):
        i=u['id']
        if self.encounter is not None and self.specs[i]['team']==0:
            self.encounter.note_own_leaving(i,self.tick)
        if self.encounter is not None and self.specs[i]['team']!=0 and i==self.encounter.boss:
            # v3: an event-site Leaving entry for the rival leader; never the death.
            self.encounter.note_boss_leaving(self.tick)
        def award():
            treasure=select_prize(self.prize_candidates,lambda:self.next_math('boss_prize',len(self.prize_candidates))) if self.prize_candidates is not None else None
            if self.prize_candidates is not None and treasure is None:return
            self.prizes.append(self.emit('prize',target=i,causeAttack=self.latest_hit.get(i),hp=self.value(i,10),treasureId=treasure))
            # Queued stage only: the receipt needs a collected chest and master data to dispatch.
            if self.receipts is not None:
                self.receipts.queue(treasure,tick=self.tick,target=i)
        enter_leaving_special(u,self.row_offset,lambda u:not self.specs[u['id']]['human'],
            lambda u:self.specs[u['id']].get('boss',False),self.body_flight,self.departure_effect,award,
            lambda s:self.state_sound('leaving_sound',s))
    def knock(self,u):
        enter_knocking_down(u,self.teams[u['board'][6]],self.row_offset,lambda u:u['commands'].clear(),
                             self.body_flight,self.animate,self.departure_effect,lambda s:self.state_sound('knockdown_sound',s))
    def departure_effect(self,u):
        return self.create_effect(departure_effect_spec(u['position']))
    def state_sound(self,purpose,sound):
        passed=random_below(self.next_lib(purpose,100),100)<100
        self.emit('state_sound',sound=sound,passed=passed)
    def change(self,u,state):
        self.emit('state',target=u['id'],old=u['board'][5],new=state,hp=self.value(u['id'],10))
        empty=lambda u:None
        change_fighter_state(u,state,
            {0:empty,1:empty,2:lambda u:exit_moving(u,lambda:self.row_offset),3:empty,4:lambda u:self.animate(u,3),5:lambda u:exit_using_skill(u,self.animate),
             6:lambda u:exit_damaging(u,self.row_offset),7:empty,8:empty},
            {0:empty,1:lambda u:self.animate(u,3),2:lambda u:enter_moving(u,self.animate),3:lambda u:self.animate(u,3),4:lambda u:self.animate(u,self.specs[u['id']]['weapon']['motion']),
             5:lambda u:enter_using_skill(u,lambda s:ROWS[s],self.animate,self.decide,self.change),
             6:lambda u:enter_damaging(u,self.animate),7:self.knock,8:self.leave})
    def enqueue(self,i,s,t,source):
        c=enqueue_skill_command(self.units[i]['commands'],t,ROWS[s],target_exists=target_exists(self.world,t))
        c['traceId']=self.next_command;self.next_command+=1
        if self.progress is not None:self.progress.enqueue(i,t)
        if self.encounter is not None:self.encounter.note_boss_target_command(self.tick,t)
        self.emit('enqueue',caster=i,target=t,skill=s,source=source,commandId=c['traceId'])
    def progress_boss_hp(self):
        """The rival leader's current HP, for the stored-attack observer's ordering probe.

        A pure read, taken at the instant a stored command is released: it is what tells the observer
        whether the boss was already dead when that release happened, even when both fall in the same
        fighters phase. `None` when this battle has no boss.
        """
        boss=self.progress.fields['bossIdentity']
        return self.value(boss,10) if boss>=0 and boss in self.units else None
    def pay(self,i,s):
        before=self.value(i,11);amount=self.cost(i,s)
        self.param(i,11)['rawValue']=max(0,self.param(i,11)['rawValue']-amount)
        self.emit('mp',caster=i,skill=s['id'],before=before,after=self.value(i,11),amount=amount)
    def sound(self,s):
        play_skill_sound(s,100,lambda n:random_below(self.next_lib('skill_sound',100),100)<n,lambda s:None)
    def reaction(self,i,attacker,hit,critical,damage):
        defensive=[(ROWS[s],level) for s,level in zip(self.specs[i]['skills'],self.specs[i]['levels']) if ROWS[s]['flags']&32]
        levels={s['id']:level for s,level in defensive}
        def eligible(s):
            # One reaction-skill candidate examined - the native counter-check site.
            if self.encounter is not None:self.encounter.counter_checks+=1
            return self.eligible(i,s,attacker)
        def counter(s):
            # A kind-20 stored command accepted from the reaction phase - the native counter-enqueue
            # site (`defender_attack_phase` calls this only for the accepted type-20 candidate).
            if self.encounter is not None:self.encounter.counter_enqueues+=1
            self.enqueue(i,s['id'],attacker,'counter')
        return defender_attack_phase(hit,critical,damage,self.units[i]['board'],[s for s,l in defensive],
            eligible,lambda s:self.invoke(i,s,levels[s['id']]),
            lambda s,d:self.reflect(i,attacker,s,d),counter,
            lambda s:self.pay(i,s),lambda s:self.balloon(i,s),lambda:random_below(self.next_lib('defender_sound',100),100)<100)
    def reflect(self,i,t,s,damage):
        def result():
            r=reflected_attack_result(i,t,s,damage)
            r['traceEvent']=self.emit('attack',route='reflection',attacker=i,target=t,skill=s['id'],
                commandId=None,hitIndex=None,hit=True,critical=False,damage=r['damage'],hpBefore=self.value(t,10))
            return r
        use_fighter_skill(s,0,dict(can_use=lambda first:self.eligible(i,s,t,first),pay_mp=lambda:self.pay(i,s),
            balloon=lambda:None,reflect=result,send=lambda event,results:self.deliver(results),sound=lambda:self.sound(s)))
    def attack_result(self,i,t,s,c=None,index=None):
        exists=target_exists(self.world,t)
        self.math.last_critical=False;self.math.last_hit=False
        result=resolve_attack(i,t,s,lambda t:target_exists(self.world,t),self.math.critical,self.math.hit,
            self.math.damage,lambda i:False,defender_phase=lambda h,k,d:self.reaction(t,i,h,k,d),
            attacker_phase=lambda:self.after_attack(i))
        if self.encounter is not None and self.specs[i]['team']!=0 and exists:
            # Pre-reaction roll, then the post-reaction result - the same two sites the kernel counts.
            self.encounter.enemy_rolls+=1
            if self.math.last_critical or self.math.last_hit:self.encounter.enemy_roll_hits+=1
            else:self.encounter.enemy_roll_misses+=1
            self.encounter.enemy_resolved_attacks+=1
            if result['hit']:self.encounter.enemy_hits+=1
            else:self.encounter.enemy_misses+=1
        event=self.emit('attack',attacker=i,target=t,skill=s['id'] if s else None,
            commandId=c['traceId'] if c else None,hitIndex=index,hit=result['hit'],critical=result['critical'],
            damage=result['damage'],hpBefore=self.value(t,10))
        result['traceEvent']=event
        return result
    def after_attack(self,i):
        pairs=[(ROWS[s],level) for s,level in zip(self.specs[i]['skills'],self.specs[i]['levels']) if ROWS[s]['flags']&64]
        levels={s['id']:level for s,level in pairs}
        invoking=self.units[i]['invoking'];before=list(invoking)
        attacker_invocation_phase(invoking,[s for s,level in pairs],lambda s:self.eligible(i,s,None),
            lambda s:self.invoke(i,s,levels[s['id']]),lambda s:self.pay(i,s),lambda s:self.balloon(i,s))
        if list(invoking)!=before:self.emit('invoking',caster=i,before=before,after=list(invoking))
    def deliver(self,results):
        self.emit('attack_batch',attacks=[r['traceEvent'] for r in results])
        # Battle subscriber processes every effect before Fighter applies HP.
        for r in results:self.attack_effect(r)
        counters=self.encounter
        for r in results:
            t,event=r['target'],r['traceEvent']
            was_hp=self.value(t,10) if (counters is not None and t in self.units
                                        and target_exists(self.world,t)) else None
            if counters is not None and was_hp is not None and t==counters.boss:
                # v3: the first attack attempt aimed at the rival leader, hit or miss.
                counters.note_boss_attempt(self.tick)
            if counters is not None and was_hp is not None and t==counters.boss and was_hp==0 and counters.boss_death_tick>=0:
                counters.boss_postdeath_attempts+=1
            if r['hit']:self.latest_hit[t]=event
            dispatch_shared_attack(self.world,[r],lambda r:None,self.change)
            if was_hp is not None:
                now_hp=self.value(t,10) if target_exists(self.world,t) else 0
                if self.specs[t]['team']==0 and was_hp>0 and now_hp==0:
                    counters.note_own_death(t,self.tick)
                if t==counters.boss and was_hp>0 and now_hp==0:
                    counters.boss_death_tick=self.tick
                if r['hit'] and t==counters.boss and was_hp==0 and counters.boss_death_tick>=0:
                    counters.boss_postdeath_lands+=1
                    counters.boss_reentries+=1
                    counters.note_postdeath_hit(self.tick)
            self.trace[event]['hpAfter']=self.value(t,10)
    def create_effect(self,spec):
        def resource(res,seb):
            if res!=28:raise NotImplementedError('Unrecovered effect resource duration')
            return self.effect_resources[seb]
        identity=create_combat_effect(spec,resource,self.world.allocate,self.world.add_component)
        self.emit('effect_birth',effect=identity,type=spec['type'],lifetime=self.world.objects[identity]['components'][32][0])
        return identity
    def balloon(self,i,s):
        return self.create_effect(skill_balloon_spec(s['id'],self.units[i]['position'],self.specs[i]['team']==0))
    def trail(self,i,previous):
        identity=create_projectile_trail(self.world,i,previous,isometric_trail_screen)
        self.emit('effect_birth',effect=identity,type=17,lifetime=10,projectile=i,
                  previous=list(previous),screen=list(isometric_trail_screen(previous)))
    def attack_effect(self,result):
        t=result['target']
        if not target_exists(self.world,t):return
        for spec in attack_effect_specs(result,self.units[t]['position'],self.specs[result['attacker']]['team']==0):
            self.create_effect(spec)
        if result['hit'] and not self.specs[t]['human']:
            self.world.remove_component(t,19)
            self.world.add_component(t,19,dict(type=4,frame=0,duration=6,loop=False,destroy_on_finish=False,
                offset_x=0.,offset_y=0.,offset_z=0.,scale_x=1.,scale_y=1.,angle=0,alpha=255))
    def attack(self,i,t,s,c=None,index=None):
        result=self.attack_result(i,t,s,c,index);self.deliver([result]);return result
    def hit_cell(self,i,s,cell,c,index):
        occupants=[t for t in self.occupancy.buckets.get(cell_key(*cell,self.map_width),[]) if t in self.units]
        def eligible(t):
            return can_damage_shared(self.world,i,t,s,lambda t:self.specs[t].get('boss',False),
                lambda t:False,lambda t:dict(type=self.specs[t].get('monsterType',0)))
        self.emit('area_cell',caster=i,skill=s['id'],cell=cell,occupants=occupants)
        damage_cell(occupants,eligible,lambda t:self.attack_result(i,t,s,c,index),
                    lambda event,results:self.deliver(results))
    def fire_skill_projectiles(self,i,t,s,c,index):
        position=self.units[i]['position']
        start=(position[0],f32(position[1]+15.),position[2])
        for end in skill_projectile_destinations(tuple(self.units[t]['position']),s['range']):
            identity=self.world.allocate()
            self.world.add_component(identity,'Position',list(start)+[0.,0.,0.,None])
            self.world.add_component(identity,'Cell',list(battle_cell(native_float_to_int(start[0]),native_float_to_int(start[2]),24,24)))
            self.world.add_component(identity,'Speed',[0.,0.,0.])
            self.world.add_component(identity,'Seb',[28,s['seb'],0,-1])
            self.world.add_component(identity,'Image',(s['img'],-1,-1,-1,-1,-1))
            self.world.add_component(identity,'Depth',[0,0])
            self.world.add_component(identity,'Attack',[s['id']])
            fire_shared_projectile(self.world,identity,start,end,10,i,animate=True,attack_skill=s['id'])
            self.projectile_sources[identity]=(i,s,c.copy(),index)
            self.emit('projectile_launch',projectile=identity,owner=i,storedTarget=t,
                commandId=c['traceId'],hitIndex=index,skill=s['id'],start=start,end=end)
            self.sound(s)
    def impact_projectile(self,identity):
        components=self.world.objects[identity]['components']
        if components[46] is None:
            self.emit('body_impact',target=identity,position=list(components[0][:3]))
            return
        i,s,c,index=self.projectile_sources[identity]
        pos=components[0]
        cell=battle_cell(native_float_to_int(pos[0]),native_float_to_int(pos[2]),24,24)
        self.emit('projectile_impact',projectile=identity,owner=i,cell=cell,storedCell=list(components[5]))
        if target_exists(self.world,i):self.hit_cell(i,s,cell,c,index)
        if s['impactImg']!=-1:
            self.create_effect(dict(type=0,value1=0,value2=0,res=28,seb=s['impactSeb'],
                position=(pos[0],f32(pos[1]+15.),pos[2]),scale=100,image=s['impactImg'],depth=True,
                max_frame=0,frame=0,loop=False,parent=None,animate=True))
        if components[7]['tex_ids'][0]==27 and components[2][1]==27:
            self.world.add_component(identity,19,dict(type=5,angle=180,frame=0,duration=20,
                destroy_on_finish=True,loop=False,alpha=255))
            self.emit('projectile_cleanup',projectile=identity,route='fade',duration=20)
        else:
            self.world.add_component(identity,32,[1])
            self.emit('projectile_cleanup',projectile=identity,route='garbage',duration=1)
    def heal(self,i,t,s):
        return cure_result(i,t,s,self.maximum(t,10))
    def add_cure_hp(self,identity,amount):
        parameter=self.param(identity,10)
        parameter['rawValue']=add_raw_parameter(parameter['rawValue'],amount,self.maximum(identity,10))[0]
    def cure_queue_position(self,identity):
        unit=self.units[identity]
        return queue_position(unit,self.teams[unit['board'][6]],lambda other:self.value(other['id'],10),self.row_offset)
    def set_cure_destination(self,identity,point):
        self.units[identity]['board'][13],self.units[identity]['board'][14]=point
    def deliver_cures(self,results):
        """Battle.OnCure effect, then FighterSystem.OnCure HP add and CureResult type15 revive.

        Native order (check_combat_cure_event): effect, then raw Parameter.Add, then for a
        resurrection payload the nullable queue position -> BB13/BB14 and ChangeState(2).
        """
        for result in results:
            t=result['target']
            if target_exists(self.world,t):self.create_effect(healing_effect_spec(result['amount'],self.units[t]['position']))
        before={r['target']:self.value(r['target'],10) for r in results if target_exists(self.world,r['target'])}
        apply_fighter_cure_results(results,lambda i:target_exists(self.world,i),self.add_cure_hp,
            self.cure_queue_position,self.set_cure_destination,lambda i,state:self.change(self.units[i],state))
        for result in results:
            t=result['target']
            if t not in before:continue
            self.emit('heal',caster=result['caster'],target=t,skill=result['skill']['id'],
                      amount=result['amount'],before=before[t],after=self.value(t,10))
            if result['skill']['type']==15:
                self.emit('revive',target=t,skill=result['skill']['id'],state=self.units[t]['board'][5],hp=self.value(t,10))
    def can_receive(self,caster,target,skill):
        spec=self.specs[target]
        return can_receive_status(has_parameter=True,has_hp=10 in self.units[target]['parameters'],
            map_chip=False,stored_hp=self.value(target,10),has_status=62 in self.units[target]['board'],
            monster=not spec['human'],boss=bool(spec.get('boss',False)),main_world=False,
            monster_type=int(spec.get('monsterType',0)),hostile_flag=bool(skill['flags']&0x20000),
            caster_ally=self.specs[caster]['team']==0,target_ally=spec['team']==0)
    def buff_cell(self,caster,skill,cell):
        """BuffEntitiesOnCell: CanBuff-filter the cell occupants, then write status in array order."""
        # Each candidate is `{id, board}`; `board` is the live AI blackboard dict of that fighter.
        occupants=[dict(id=t,board=self.units[t]['board'])
                   for t in self.occupancy.buckets.get(cell_key(*cell,self.map_width),[]) if t in self.units]
        snapshots={}
        def board_state(unit):
            return {k:unit['board'][k] for k in (62,63,64) if k in unit['board']}
        def accuracy(unit):
            return buff_accuracy(self.value(caster,19),self.value(unit['id'],16),skill['type'],skill['value'])
        def hits(unit,rate):
            return random_below(self.next_math('buff_hit',100),100)<rate
        def turns(unit):
            return buff_turns(self.value(caster,18))
        def show_text(unit,kind):
            self.emit('status_text',target=unit['id'],text=kind,skill=skill['id'])
        for unit in occupants:
            snapshots[unit['id']]=board_state(unit)
        applied=buff_entities_on_cell(skill,occupants,lambda u:self.can_receive(caster,u['id'],skill),
            accuracy,hits,turns,show_text)
        for unit in occupants:
            after=board_state(unit)
            self.emit('status_apply',caster=caster,target=unit['id'],skill=skill['id'],
                      applied=after.get(62)==skill['id'],before=snapshots[unit['id']],after=after)
        return applied
    def unsupported_category(self,caster,skill):
        raise NotImplementedError(f"Skill {skill['id']} category {skill['category']} has no recovered effect route")
    def use(self,u,c,index):
        i,t,s=u['id'],c['target'],ROWS[c['skill']]
        used=use_fighter_skill(s,index,dict(can_use=lambda first:self.eligible(i,s,t,first),
            pay_mp=lambda:self.pay(i,s),balloon=lambda:self.balloon(i,s),attack=lambda:self.attack_result(i,t,s,c,index),
            cure=lambda:self.heal(i,t,s),send=lambda event,results:self.deliver(results) if event==26 else self.deliver_cures(results) if event==27 else None,sound=lambda:self.sound(s),
            cells=lambda kind:self.skill_cells(i,s),damage_cell=lambda cell:self.hit_cell(i,s,cell,c,index),cell_effect=lambda cell:self.create_effect(cell_skill_effect_spec(s,cell)),
            buff_cell=lambda cell:self.buff_cell(i,s,cell),unsupported_category=lambda:self.unsupported_category(i,s),
            fire_projectiles=lambda:self.fire_skill_projectiles(i,t,s,c,index)))
        self.emit('release',caster=i,target=t,skill=s['id'],commandId=c['traceId'],index=index,used=used)
    def normal_target(self,u):
        if self.front_targets is None:
            return front_opponent([v['id'] for v in self.teams[1-u['board'][6]]],
                lambda t:target_exists(self.world,t),lambda t:self.value(t,10),lambda t:self.units[t]['board'][5],
                lambda t:self.units[t]['board'][7],lambda t:self.distance(u['id'],t))
        return next((t for t in self.front_targets(self,u['id']) if
            is_normal_attack_target(target_exists(self.world,t),self.value(t,10),self.units[t]['board'][5])),None)
    def ranged_target(self,u):
        i=u['id']
        return nearest_skill_target(i,[v['id'] for v in self.teams[1-u['board'][6]]],
            self.specs[i]['weapon']['shootingRange'],self.distance,
            lambda t:is_normal_attack_target(target_exists(self.world,t),self.value(t,10),self.units[t]['board'][5]))
    def update(self,u,state):
        i=u['id']
        if state==6:
            update_damaging(u,lambda u:self.value(i,10),lambda u:self.specs[i]['human'],
                lambda u:not self.specs[i]['human'],lambda u:u['board'][6]==1,lambda:False,
                self.decide,lambda u:None,self.change)
        elif state==7:update_knocking_down(u,self.animate,self.change)
        elif state==8:update_leaving(u)
        elif state==2:update_moving(u,move_fighter,lambda:self.row_offset,self.decide,self.change)
        elif state==1:
            decision=self.decide(u)
            if decision!=1:self.change(u,decision)
        elif state==5:tick_using_skill(u,self.decide,self.change)
        elif state==3:
            sleeping=62 in u['board'] and ROWS[u['board'][62]]['type']==67
            tick_charging(u,sleeping,dict(interval=lambda u:attack_interval(self.value(i,15)),choose_skill=self.choose,
                add_command=lambda u,s,t:self.enqueue(i,s,t,'charge'),change_state=self.change,
                long_range=lambda u:self.specs[i]['weapon']['shootingRange']>1,nearest_target=self.ranged_target,
                most_front=lambda u:is_most_front(u['board'][7]),front_target=self.normal_target,decide=self.decide))
        elif state==4:
            weapon=self.specs[i]['weapon']
            gauge,state=update_attacking(u['board'][4],u['board'][8],lambda:u['long_board'].get(16),weapon['projectileFlag'],weapon['type'],
                lambda t:self.attack(i,t,None),lambda t,sid:self.fire_skill_projectiles(i,t,ROWS[sid],dict(traceId=None),0),
                lambda:self.next_lib('normal_attack_update'),lambda:self.decide(u))
            u['board'][8]=gauge
            if state is not None:self.change(u,state)
    def run(self,ticks,input_callback=None,finish_policy='at-horizon',stop_on_verdict=None,tick_hook=None):
        # `finish_policy` is the declared FINISH INPUT policy - a player confirmation, not a claim
        # that the battle reaches quiescence. `at-horizon` (legacy supported default) runs every
        # native update to the supplied horizon and then, after that full last tick, requests ONE
        # native Ending confirmation (`combat_ending.update_ending`). A press while the Ending counter
        # is <=79 only writes the counter to 80 and requests no transition, so the run reports
        # awaiting-confirmation with no Finish and no dispatched chests; a press with frame>79 is a
        # valid native Finish. `after-ending` waits for the recovered counter to pass 79 (declared
        # wait-until-frame-80) and then requests ONE confirmation after a full tick; it does not wait
        # for command queues to empty and does not claim the game settles or exhausts them.
        # `on-verdict` is a DIAGNOSTIC cut at the verdict boundary only; never a native button claim.
        self.ending_gate_tick=None; self.ending_confirmed=None; self.ending_counter=None
        if stop_on_verdict is not None:finish_policy='on-verdict' if stop_on_verdict else finish_policy
        for _ in range(ticks):
            self.tick+=1;self.phase='battle'
            self.battle_frame=advance_battle_frame(self.battle_frame)
            if self.battle_state==2 and any(is_annihilated(t) for t in self.teams):
                self.battle_frame=0
                self.verdict=enter_ending(self.teams,self.change);self.battle_state=3
                self.emit('verdict',result=self.verdict)
            self.phase='fighters'
            if input_callback is not None:input_callback(self,'before_fighters')
            # Native post-fighter branch gates on IsGoodMatchAttribute; special terrain=-1 makes it false.
            update_fighters(self.teams,self.battle_state,self.update,
                lambda u:execute_shared_skill_commands(self.world,u['id'],lambda c:ROWS[c['skill']],self.animate,self.use,
                    None if self.progress is None else
                    (lambda command,identity=u['id']:self.progress.release(
                        identity,command,self.tick,self.progress_boss_hp()))))
            if input_callback is not None:input_callback(self,'after_fighters')
            self.phase='skills'
            for identity in self.skill_members.members:
                components=self.world.objects[identity]['components']
                if components[28] is None:continue
                board=components[28]['board']
                before={k:board[k] for k in (62,63,64) if k in board}
                update_status(board)
                after={k:board[k] for k in (62,63,64) if k in board}
                if before!=after:self.emit('status_tick',target=identity,before=before,after=after)
            self.phase='projectiles'
            tick_projectiles(self.projectiles.members,self.world,lambda *a:None,self.trail,self.impact_projectile)
            self.phase='move'
            update_positions(self.moving.members,self.world)
            self.phase='rotate'
            def image_arrays(i,right):
                image=self.world.objects[i]['components'][7]
                return image['res_ids'],image['tex_ids'] # Appearance arrays remain outside the combat clip model.
            update_facing(self.rotating.members,self.world,0,lambda i:False,
                lambda i:self.world.objects[i]['components'][18] is not None,image_arrays,math.atan2,math.fmod)
            self.phase='cells'
            def cell_changed(i,old):
                self.occupancy.changed(i,old)
                self.emit('cell_change',target=i,oldKey=old,cell=list(self.world.objects[i]['components'][5]))
            update_cells(self.cells.members,self.world,self.map_width,24,24,cell_changed)
            self.phase='height'
            # The special map starts with null tiles; supported phases create no terrain entities.
            update_heights(self.height_members.members,self.world,dict(
                has_product=lambda i:False,has_treasure=lambda i:False,
                has_human=lambda i:self.world.objects[i]['components'][18] is not None,
                human_flag32=lambda i:bool(self.world.objects[i]['components'][18]['flag']&32),
                can_move_in_air=lambda i:False,map_chip=lambda x,y:None,
                null_or_destroyed=lambda tile:tile is None))
            self.phase='modifiers'
            tick_modifiers(self.modifiers.members,self.world,lambda a:math.sin(math.radians(a)),lambda:None)
            self.phase='animation'
            update_animations(self.fighter_animations.members,self.world,self.animation_resources,
                dict(enabled=True,auto_animation=False,common_frames_update=True),lambda i,b:self.animate(self.units[i],b))
            self.phase='effects'
            update_effect_phase(self.effects.members,self.effect_values,self.effect_positions,self.world.is_destroyed,self.world.destroy)
            self.phase='garbage'
            update_garbage(self.garbage.members,self.lifetimes,self.world.destroy)
            # Additive checkpoint seam for the bounded interaction session
            # (combat_interaction): called at the END of a fully processed tick, so a state captured
            # here resumes the identical tick sequence. It never mutates the engine and defaults to
            # None for every existing caller, so no combat behaviour changes.
            if tick_hook is not None:tick_hook(self)
            if self.battle_state==3:
                if finish_policy=='on-verdict':
                    self.ending_confirmed=False
                    break
                # after-ending: declared wait-until-frame-80, then ONE confirmation after a full tick.
                # Command queues are neither awaited nor claimed settled; the valid player Finish is
                # an actual game action that may truncate them.
                if finish_policy=='after-ending' and self.battle_frame>79:
                    counter,transition=after_ending_confirmation(self.battle_frame,self.verdict)
                    self.ending_counter=counter
                    if transition is not None:
                        self.ending_gate_tick=self.tick; self.ending_confirmed=True
                        break
        if self.battle_state==3 and finish_policy=='at-horizon':
            # Declared input phase: ONE native Ending confirmation after the full last tick.
            counter,transition=after_ending_confirmation(self.battle_frame,self.verdict)
            self.ending_counter=counter
            self.ending_confirmed=transition is not None
            if transition is not None:self.ending_gate_tick=self.tick
        return dict(ticks=self.tick+1,names=self.names,initializationMode=self.initialization_mode,verdict=self.verdict,prizeCallbacks=len(self.prizes),
            battleState=self.battle_state,battleFrame=self.battle_frame,
            finishPolicy=finish_policy,endingGateTick=self.ending_gate_tick,
            endingConfirmed=self.ending_confirmed,endingCounter=self.ending_counter,
            unresolvedCommands=sum(1 for u in self.units.values() if u['commands']),
            pendingProjectiles=len(self.projectiles.members.indices),
            activeDamageOrLeaving=sum(1 for u in self.units.values() if u['board'][5] in (6,8)),
            mathDraws=self.math_draws,libDraws=self.lib_draws,trace=self.trace,
            rngFinalState={name:dict(index=rng.index,partner=rng.partner,values=list(rng.values))
                           for name,rng in (('math',self.math_rng),('lib',self.lib_rng))},
            units={i:dict(hp=self.value(i,10),mp=self.value(i,11),state=u['board'][5],commands=len(u['commands'])) for i,u in self.units.items()},
            limits=['Shared combat/navigation composition with original8x16 map dimensions and empty missing-cell fallback; not a validated complete encounter.',
                    'No complete effect allocation/RNG, source-world bookkeeping or teardown; projectile fade/garbage cleanup is integrated.',
                    'Linked parameters remain unsupported; equipment IDs/levels/affinity and raw clone inputs must be supplied.'])
