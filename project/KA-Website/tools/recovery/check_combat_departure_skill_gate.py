"""Special boss Leaving grid mutation composed with stored skill eligibility."""
import json
from itertools import product
from combat_initial_state import EVIDENCE
from combat_entities import CombatEntities
from combat_states import enter_leaving_special,is_most_front
from combat_skills import can_use_skill
from combat_skill_use import use_fighter_skill
from combat_encounters import special_enemy_baseline

rows={r['id']:r for r in json.loads((EVIDENCE/'weapon-skill-profiles.json').read_text(encoding='utf-8'))['skills']}
examples=[]
for encounter_id,front_grid in product(range(20),range(5)):
    boss=next(f for f in special_enemy_baseline(encounter_id,0,lambda n:0)['fighters'] if f['leaderIdentity'])
    skill=rows[boss['skills']['dataIds'][0]]
    world=CombatEntities(100,[]);identity=world.allocate()
    world.add_component(identity,'AI',dict(board={4:0,5:8,6:1,7:front_grid},long_board={},commands=[],path=[]))
    world.add_component(identity,'Position',[float(front_grid*24),0.,72.,0.,0.,0.,None])
    unit=world.fighter(identity);calls=[]
    def use():
        return use_fighter_skill(skill,0,dict(can_use=lambda check:can_use_skill(skill,check_mp=check,
            mp=100000,cost=skill['minMp'],target_exists=True,weapon_type=0,target_hp=1000,target_hp_rate=100,
            invoking=False,battle_world=True,most_front=is_most_front(unit['board'][7]),opponent_in_range=True),
            pay_mp=lambda:calls.append('pay'),balloon=lambda:None,fire_projectiles=lambda:calls.append('projectile'),
            cells=lambda kind:[(0,4)],damage_cell=lambda c:calls.append('cell'),cell_effect=lambda c:None,
            attack=lambda:calls.append('direct'),send=lambda *a:None,sound=lambda:None))
    before=use();calls.clear()
    enter_leaving_special(unit,3,lambda u:True,lambda u:True,lambda *a:None,lambda u:None,lambda:None,lambda s:None)
    after=use();requires_front=skill['type'] in (26,27) or bool(skill['flags']&0x60000)
    assert unit['board'][7]==front_grid+100
    assert before and after==(not requires_front),(encounter_id,skill['id'],front_grid,before,after)
    assert bool(calls)==after
    examples.append(dict(encounterId=encounter_id,bossId=boss['monsterId'],skillId=skill['id'],skillType=skill['type'],
        gridBefore=front_grid,gridAfter=unit['board'][7],allowedBeforeLeaving=before,allowedAfterLeaving=after,effectsAfterLeaving=calls))
report=dict(composedBossDepartureSkillCases=len(examples),examples=examples,
    findings=['Leaving adds100 to boss grid; queued commands survive, but their skill eligibility is still checked on use.',
              'Wairo Tank and Kairo Kommander skill35 loses front-row eligibility after this transition.',
              'Ordinary multi-hit and projectile boss skills do not acquire this front-row gate merely from Leaving.'],
    limits=['Portable composition of previously native-checked Leaving/IsMostFront/CanUseSkill/UseSkill pieces.',
            'Prescribed front position at departure, sufficient MP and living target; not initial enemy formation or complete fight.',
            'Area opponent-range check supplied true; projectile travel and outgoing attack/counter/reward feedback not simulated.',
            'Attacks already emitted before Leaving are a separate impact path and are not cancelled by this eligibility check.'])
(EVIDENCE/'departure-skill-gate-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
print(json.dumps({k:v for k,v in report.items() if k!='examples'}))
