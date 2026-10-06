"""Original enemy roster/skill/cost inputs relevant to chest-farming simulation."""
import json
from collections import Counter
from combat_initial_state import EVIDENCE
from combat_encounters import special_enemy_baseline
from combat_resolution import skill_invocation_rate,skill_mp_cost


def special_enemy_roles(encounter_id,defeat_count):
    baseline=special_enemy_baseline(encounter_id,defeat_count,lambda n:0)
    skills={s['id']:s for s in json.loads((EVIDENCE/'weapon-skill-profiles.json').read_text(encoding='utf-8'))['skills']}
    boss=next(f for f in baseline['fighters'] if f['leaderIdentity'])
    rows=[]
    for fighter in baseline['fighters']:
        assert len(fighter['skills']['dataIds'])==1
        skill=skills[fighter['skills']['dataIds'][0]];level=fighter['skills']['invocationLevels'][0]
        cost=skill_mp_cost(skill['minMp'],skill['maxMp'],1,monster=True)
        healing=skill['category']==1 and skill['type']==2
        distance=sum(abs(a-b) for a,b in zip(fighter['cell'],boss['cell']))
        rows.append(dict(rosterIndex=fighter['incomingIndex'],monsterId=fighter['monsterId'],name=fighter['name'],
            boss=fighter['leaderIdentity'],cell=fighter['cell'],skillId=skill['id'],skillType=skill['type'],
            skillCategory=skill['category'],skillHitCount=skill['count'],shootingRange=skill['shootingRange'],
            invocationLevel=level,conditionalInvocationPercent=100 if skill['flags']&8192 else skill_invocation_rate(skill['type'],level),
            mpCost=cost,initialMP=fighter['parameters']['11']['rawValue'],
            initialMPOnlyCastBudget=fighter['parameters']['11']['rawValue']//cost if cost>0 else None,
            recovery=healing,healPercentOfTargetMaxHP=skill['value'] if healing else None,
            initiallyWithinBossHealRange=healing and not fighter['leaderIdentity'] and distance<=skill['shootingRange']))
    return dict(encounterId=encounter_id,title=baseline['title'],defeatCount=defeat_count,level=baseline['level'],
        healerCount=sum(r['recovery'] for r in rows),skillCounts=dict(Counter(r['skillId'] for r in rows)),fighters=rows)


if __name__=='__main__':
    encounters=json.loads((EVIDENCE/'encounters.json').read_text(encoding='utf-8'))['encounters']
    rows=[special_enemy_roles(e['id'],0) for e in encounters]
    assert len(rows)==20 and sum(len(e['fighters']) for e in rows)==270
    by_id={e['encounterId']:e for e in rows}
    assert [by_id[i]['healerCount'] for i in (3,7,11,15,19)]==[16,16,16,19,0]
    report=dict(encounters=rows,limits=[
        'Original constructor baselines at explicit defeatCount0; no player save or full fight replay.',
        'Invocation percent is conditional on candidate/reaction consideration and eligibility, not a per-tick or unconditional action probability.',
        'MP-only cast budget assumes no replenishment and no other MP spending; it is not predicted number of successful heals.',
        'Initial healing range does not mean the boss will be selected: all begin full HP, nearest eligible target and movement matter.',
        'Follower rate100 draws are supplied as0; live RNG state is not captured.'])
    (EVIDENCE/'enemy-combat-roles.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(encounters=len(rows),fighters=sum(len(e['fighters']) for e in rows),extremeHealerCounts={i:by_id[i]['healerCount'] for i in (3,7,11,15,19)})))
