"""Compare direct skill/normal attacks and cell-based impacts on a zero-HP boss.

Portable composition of independently native-checked dispatch/filter/event
routines. Target acquisition and projectile travel are deliberately supplied.
"""
import json
from itertools import product
from combat_initial_state import EVIDENCE
from combat_entities import CombatEntities
from combat_shared_resolution import dispatch_shared_attack, target_exists
from combat_targeting import can_damage_shared, damage_cell
from combat_skill_use import use_fighter_skill
from combat_skills import can_use_skill
from combat_resolution import resolve_attack
from combat_projectiles import process_projectile_impact
from combat_spatial import battle_cell

rows={r['id']:r for r in json.loads((EVIDENCE/'weapon-skill-profiles.json').read_text(encoding='utf-8'))['skills']}
selected=[r for r in rows.values() if r['flags']&8 and r['category']==0 and r['type'] in (1,11,19,20,26,27)]
routes=[('skill',r['id']) for r in selected]+[('normal_direct',0),('normal_bow',1),('normal_gun',2)]
examples=[]
for (route,skill_id),hp,state,weapon,still_in_cell in product(routes,(0,10),(3,6,8),(0,7,8),(False,True)):
    world=CombatEntities(100,[]);caster,boss,projectile=world.allocate(),world.allocate(),world.allocate()
    world.add_component(caster,49,{});world.add_component(caster,18,{})
    world.add_component(boss,20,{})
    world.add_component(boss,33,dict(parameters={10:dict(rawValue=hp,rawMax=10,extraValue=0)},extras=[]))
    world.add_component(boss,28,dict(board={4:19,5:state}))
    # Stored direct target can be far away; cell attacks use the impact cell.
    world.add_component(boss,0,[0.,0.,72. if still_in_cell else -2400.,0.,0.,0.,None])
    world.add_component(boss,5,[0,3 if still_in_cell else -100])
    record=rows[skill_id];events=[]
    def exists(identity):return target_exists(world,identity)
    def change(unit,next_state):unit['board'].update({4:0,5:next_state});events.append('damage_state')
    def send(event,results):
        assert event==26;events.append('event26')
        dispatch_shared_attack(world,results,lambda r:None,change)
    def attack(target):
        events.append('attack')
        return resolve_attack(caster,target,record,exists,lambda a:False,lambda a,b:True,lambda *args:1,lambda a:False)
    def eligible(target):
        return can_damage_shared(world,caster,target,record,lambda i:True,lambda i:False,lambda i:dict(type=0))
    def hit_cell(cell):
        members=[boss] if tuple(world.objects[boss]['components'][5])==tuple(cell) else []
        damage_cell(members,eligible,attack,send)
    def impact():
        events.append('projectile_impact')
        world.add_component(projectile,0,[0.,0.,72.,0.,0.,0.,None])
        world.add_component(projectile,5,[0,2])  # Deliberately stale; actual position must select row3.
        def damage_at_position(identity,owner,skill):
            p=world.objects[identity]['components'][0];hit_cell(battle_cell(int(p[0]),int(p[2]),24,24))
        process_projectile_impact(projectile,dict(destroyed=world.is_destroyed,has_cell=lambda i:True,
            owner=lambda i:caster,alive=exists,fighter=lambda i:True,has_attack=lambda i:True,
            damage_at_position=damage_at_position,skill=lambda i:record,terrain_at_cell=lambda i:None,
            effect=lambda *args:None,texture=lambda i:-1,seb=lambda i:0,
            garbage=lambda i,t:world.add_component(i,32,[t])))
    allowed=True
    if route=='normal_direct':send(26,[attack(boss)])
    elif route in ('normal_bow','normal_gun'):impact()
    else:
        def can_use(check):
            return can_use_skill(record,check_mp=check,mp=100000,cost=0,target_exists=True,
                weapon_type=weapon,target_hp=hp,target_hp_rate=hp*10,invoking=False,
                battle_world=True,most_front=True,opponent_in_range=True)
        allowed=use_fighter_skill(record,1,dict(can_use=can_use,pay_mp=lambda:None,balloon=lambda:None,
            attack=lambda:attack(boss),send=send,sound=lambda:None,fire_projectiles=impact,
            cells=lambda kind:[(0,3)],damage_cell=hit_cell,cell_effect=lambda cell:None))
    direct=route=='normal_direct' or (route=='skill' and record['type'] in (19,20))
    expected=allowed and (direct or (hp>0 and still_in_cell))
    assert ('damage_state' in events)==expected,(route,skill_id,hp,state,weapon,still_in_cell,events)
    assert world.fighter(boss)['parameters'][10]['rawValue']==(max(0,hp-1) if expected else hp)
    assert world.fighter(boss)['board'][4]==(0 if expected else 19)
    examples.append(dict(route=route,skillId=skill_id,hp=hp,state=state,weaponType=weapon,inImpactCell=still_in_cell,
                         skillAllowed=allowed,damageStateEntered=expected))
report=dict(postmortemRouteCases=len(examples),originalSkillRows=len(selected),examples=examples,
    findings=['Stored Counter and multi-hit UseSkill remain direct attacks with melee, gun or bow weapon types.',
              'Normal bow/gun impacts and projectile/line/band skills filter positive HP at the affected cell.',
              'Direct release can reset zero-HP boss Damaging even after it leaves the original cell; cell routes cannot when component HP is zero.'],
    limits=['Portable composition, not full native world execution; original dispatch/filter/event routines separately native-checked.',
            'Skill MP, invocation, front/range eligibility, accuracy and base damage supplied; empty extras list.',
            'Projectile arrives at prescribed position; no travel, other occupants, healing or reward settlement.',
            'No conclusion that every bow/gun farming build fails, nor that a weapon is globally optimal.'])
(EVIDENCE/'postmortem-route-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
print(json.dumps({k:v for k,v in report.items() if k!='examples'}))
