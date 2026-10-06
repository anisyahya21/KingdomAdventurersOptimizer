import strategy_operating_regions as regions

def main():
    policy={'finishPolicy':'on-verdict','holyHerbStock':0}
    study=regions.question(kind='fixed-build',reference_id='reference',encounter_revision='e1',mechanics_revision='m1',
                           policy=policy,constraints={'context':'synthetic'},field='DEX',fixed_fields={'ATK':300},adjustable_fields=['DEX'])
    def rows(cid,values):
        return [dict(candidateId=cid,seeds=[i,77],finalEarned=v,resolved=True,encounterRevision='e1',mechanicsRevision='m1',policy=policy) for i,v in enumerate(values)]
    reference=rows('reference',[10]*32)
    left=regions.assess(study,reference,rows('left',[10]*32),candidate_id='left',value=1)
    middle=regions.assess(study,reference,rows('middle',[3]*32),candidate_id='middle',value=2)
    right=regions.assess(study,reference,rows('right',[11]*32),candidate_id='right',value=3)
    assert [p['classification'] for p in [left,middle,right]]==['supported-acceptable','supported-degraded','supported-acceptable']
    report=regions.publish(study,[left,middle,right],gaps=[[1,2],[2,3]])
    assert report['unresolvedGaps'] and not report['safeCartesianProductClaimed'] and not report['continuousCoverageClaimed']
    assert not middle['noCompensationClaim']
    missing=rows('missing',[10]*32); missing[0]['resolved']=False; missing[0]['finalEarned']=None
    assert regions.assess(study,reference,missing,candidate_id='missing',value=4)['classification']=='unresolved'
    jackpots=rows('tail',[0]*31+[320])
    assert regions.assess(study,reference,jackpots,candidate_id='tail',value=5)['classification']=='unresolved'
    assert regions.assess(study,reference[:1],rows('tiny',[10]),candidate_id='tiny',value=6)['classification']=='unresolved'
    wrong_policy=rows('wrong',[10]*32); wrong_policy[0]['policy']={'finishPolicy':'on-verdict','holyHerbStock':1}
    assert regions.assess(study,reference,wrong_policy,candidate_id='wrong',value=7)['classification']=='unresolved'
    print('PASS boundaries: disconnected tested points, gaps, unresolved pairs, jackpot uncertainty, policy isolation, no universal compensation inference')
if __name__=='__main__': main()
