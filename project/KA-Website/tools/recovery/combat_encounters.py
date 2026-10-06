"""Compose recovered special-enemy constructors and formation inputs.

Output is the traced constructor baseline, not a captured complete world state.
"""
import json
from combat_initial_state import EVIDENCE, PARAM_IDS, i32, trunc_div, monster_parameter, formation
from combat_runtime_data import load_data


def special_enemy_baseline(encounter_id, defeat_count, lib_rand_below):
    data=load_data('encounters.json')
    encounters={e['id']:e for e in data['encounters']}
    monsters={m['id']:m for m in data['monsters']}
    encounter=encounters[encounter_id]
    level=i32(encounter['levelField']+trunc_div(i32(defeat_count),5))
    members=[]
    def create(monster_id,boss=False):
        monster=monsters[monster_id]
        values={pid:monster_parameter(curve,level) for pid,curve in zip(PARAM_IDS,monster['parametersRaw'])}
        members.append(dict(monsterId=monster_id,name=monster['name'],monster=True,
                            leaderIdentity=boss,rank=1 if boss else level,level=level,maxLevel=5000,
                            effectiveDefense=values[14],
                            parameters={str(pid):dict(rawValue=v,rawMax=v if pid in (10,11) else 2147483647,
                                                     extraValue=0,extraMax=0,trainingLevel=1)
                                        for pid,v in values.items()},
                            skills=dict(maxSlotNum=1,dataIds=[monster['skillId']],invocationLevels=[1],invokingSkills=[])))
    draws=0
    for follower in encounter['followers']:
        draws+=1
        if lib_rand_below(100)<follower['checkRate']:
            create(follower['monsterId'])
    create(encounter['bossId'],True)
    placed = formation(members, 1, len(members))
    return dict(encounterId=encounter_id,title=encounter['title'],defeatCount=defeat_count,
                level=level,terrain=-1,followerSelectionDraws=draws,
                formationOrder=[fighter['incomingIndex'] for fighter in placed],
                fighters=sorted(placed,key=lambda fighter: fighter['incomingIndex']),
                evidenceScope='Constructor baseline + recovered stable formation; source-world mutations and full native initialization are not replayed.')


if __name__=='__main__':
    # All original follower rates are100. Zero is a valid supplied draw; every
    # draw is still recorded, and this report does not assert a live RNG state.
    data=json.loads((EVIDENCE/'encounters.json').read_text(encoding='utf-8'))
    assert all(f['checkRate']==100 for e in data['encounters'] for f in e['followers'])
    rows=[special_enemy_baseline(e['id'],0,lambda n:0) for e in data['encounters']]
    report=dict(status='Composed research baseline; not a complete simulation or original-game snapshot',
                encounters=rows,limits=['defeatCount=0 is an explicit example input, not the user save value.',
                'Each accepted follower and boss has its separately ordered entity instance.',
                'World/entity IDs, complete AI/component state and live RNG states remain required for exact replay.'])
    (EVIDENCE/'enemy-start-baselines.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(encounters=len(rows),fighters=sum(len(r['fighters']) for r in rows),selectionDraws=sum(r['followerSelectionDraws'] for r in rows))))
