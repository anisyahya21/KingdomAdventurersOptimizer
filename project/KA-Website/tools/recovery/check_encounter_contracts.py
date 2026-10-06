"""Independent acceptance probes for encounter-aware contracts; no battles/live access."""
from pathlib import Path
import hashlib,json,sqlite3,tempfile,sys
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
import strategy_outcomes as outcomes
import strategy_search_mode as mode
import strategy_experiment_store as ledger
import strategy_build_domain as domain
import strategy_mechanics as mechanics

def check(condition, message):
    if not condition: raise AssertionError(message)

def run():
    for bad in (-1,0,3,99,True):
        r=outcomes.outcome({'verdict':bad,'rewardOutcome':{'pendingChests':15}},policy={'finishPolicy':'on-verdict'})
        check(r['finalEarned'] is None,'unknown verdict must never create zero reward')
    r=outcomes.outcome({'verdict':1,'error':'worker failure','rewardOutcome':{'pendingChests':15}},policy={'finishPolicy':'on-verdict'})
    check(r['finalEarned'] is None,'worker error cannot become an earning observation')
    for cfg in ({'version':1,'mode':'community-first','allocations':{'community':0,'discovery':1},'purposes':{'improvement':16}},
                {'version':1,'mode':'all-strategy','allocations':{'community':0.5,'average':0.5},'purposes':{'improvement':16}}):
        try: mode.validate_config(cfg)
        except ValueError: pass
        else: raise AssertionError('disabled or retired stream admitted')
    import strategy_search
    scenario=json.loads(strategy_search.DEFAULT_BASE_SCENARIO_PATH.read_text(encoding='utf-8'))
    prepared=mechanics.prepared_setup(scenario)
    value=prepared['ownUnits'][0]['effectiveParameters'][19] if 19 in prepared['ownUnits'][0]['effectiveParameters'] else prepared['ownUnits'][0]['effectiveParameters']['19']
    # Constraint rejection does not depend on parameter representation: the extreme fixed value
    # differs from this actual fixture and remains a finite signed integer.
    constrained=domain.validate_constraints(scenario,{'context':'player','fixed':{'ownUnits.0.parameters.19':2147483647}})
    check(not constrained['valid'],'fixed effective DEX mismatch must reject candidate')
    body=dict(scenario); body.pop('mathSeed',None); body.pop('libSeed',None)
    expected=hashlib.sha256(json.dumps(body,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
    check(domain.identity(scenario)==expected,'existing canonical candidate identity changed')
    db=sqlite3.connect(':memory:'); ledger.initialize(db)
    intent={'scope':'community','purpose':'improvement','planned_budget':1,'stopping':'one sample','ownerShare':1,
            'policy':{'finishPolicy':'on-verdict'},'mechanicsRevision':'test','encounterRevision':'test'}
    experiment=ledger.create_experiment(db,intent)
    check(ledger.reserve(db,experiment,'candidate',[123,456]),'one seed pair must fit one battle budget')
    check(not ledger.reserve(db,experiment,'candidate',[123,457]),'second distinct pair must exceed one battle budget')
    cfg=mode.default_config('community-first')
    sid=ledger.configure_session(db,cfg)
    ledger.deactivate_session(db,sid)
    check(ledger.configure_session(db,cfg)==sid,'reactivation must preserve the original budget session')
    old_config=mode.default_config('all-strategy')
    old_config['allocations']={name:float(name=='rebel') for name in mode.share_streams()}
    old_sid=ledger.configure_session(db,old_config)
    old_experiment=ledger.create_experiment(db,dict(intent,scope='old-rebel',owner='rebel',
                                                   sessionId=old_sid))
    check(ledger.reserve(db,old_experiment,'old-rebel-build',[222,333]),
          'fixture must have interrupted paid work in an earlier allocation policy')
    check(not ledger.recover(db,session_id=sid)['outstanding'],
          'recovery must not steal outstanding jobs from another budget session')
    ledger.activate_exclusive_session(db,sid)
    recovery=ledger.recover(db)
    check(any(row['chargedExperimentId']==old_experiment for row in recovery['blocked']),
          'Community activation must block old Rebel reservations without deleting them')
    check(not ledger.reserve(db,old_experiment,'old-rebel-build',[223,334]),
          'previous policy cannot reserve a new run after exclusive activation')
    engine_a=ledger.create_experiment(db,dict(intent,scope='engine-a',engineRevision='a'))
    engine_b=ledger.create_experiment(db,dict(intent,scope='engine-b',engineRevision='b'))
    check(ledger.reserve(db,engine_a,'same-build',[888,999])
          and ledger.reserve(db,engine_b,'same-build',[888,999]),
          'distinct engine revisions must retain independent charges')
    check(db.execute('SELECT COUNT(DISTINCT sample_key) FROM ea_sample_link WHERE '
                     'experiment_id IN (?,?)',(engine_a,engine_b)).fetchone()[0]==2,
          'same candidate/policy/pair must not coalesce across engine revisions')
    mixed=outcomes.summarize([outcomes.outcome({'verdict':2}),outcomes.outcome({'verdict':None})])
    check(mixed['meanEarned'] is None and mixed['partialMean']==0,
          'canonical recommendation mean must not expose a favourable resolved subset')
    db.close()
    # A zero-damage/miss tail must be bounded during derivation, not after it.
    tail=mechanics.ttk_profile({0:0.999,1:0.001},100,max_hits=8)
    check(tail['truncated'] and not tail['summary'] and len(tail['hits'])<=8,
          'TTK convolution exceeded its budget or published a censored conditional mean')
    import strategy_joint_proposals as proposals
    source=dict(scenario,encounterId=18)
    easy=dict(scenario,encounterId=0)
    hard=dict(scenario,encounterId=19)
    a=proposals.transfer_targets(source,easy); b=proposals.transfer_targets(source,hard)
    check(a and b and a!=b,'cross-encounter adaptation ignored destination roster')
    check(all('reward' not in k.lower() and 'earned' not in k.lower()
              for goals in (a,b) for row in goals.values() for k in row),
          'cross-encounter transfer leaked reward observations')
    print('PASS independent reward/config/constraint/identity/seed-pair contract probes')

if __name__=='__main__': run()
