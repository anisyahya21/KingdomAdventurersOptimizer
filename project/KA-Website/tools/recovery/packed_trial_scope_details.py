"""Read-only source identity/context/association counts; no payload transformations."""
import json,pathlib
import packed_library_trial as trial
source=pathlib.Path('A:/KingdomAdventurersOptimizer/Community-Knowledge-20260928.sqlite')
db=trial.ro(source);db.execute('BEGIN');out={'source':str(source),'battlePopulations':{}}
for table in trial.PAYLOAD_TABLES:
    counts=db.execute('SELECT count(*),sum(result IS NOT NULL),sum(outcome IS NOT NULL),count(DISTINCT candidate_id) FROM '+trial.q(table)).fetchone()
    out['battlePopulations'][table]=dict(zip(('retainedRows','completedResults','acceptedOutcomes','distinctCandidateIds'),counts))
out['distinctDevelopmentContexts']=db.execute('SELECT count(*) FROM (SELECT policy,mechanics_revision,encounter_revision,measurement_window FROM ea_sample GROUP BY policy,mechanics_revision,encounter_revision,measurement_window)').fetchone()[0]
out['developmentContextIdentityColumns']=['policy','mechanics_revision','encounter_revision','measurement_window']
out['allBattleCandidateIds']=db.execute('SELECT count(*) FROM (SELECT candidate_id FROM ea_sample UNION SELECT candidate_id FROM ea_holdout)').fetchone()[0]
out['reuseAssociations']={str(k):v for k,v in db.execute('SELECT reused,count(*) FROM ea_sample_link GROUP BY reused')}
out['liveCandidateRows']=db.execute('SELECT count(*) FROM candidate').fetchone()[0]
out['historicalCandidateAssociations']=db.execute('SELECT count(*) FROM history_candidate').fetchone()[0]
out['historicalScenarioAssociations']=db.execute('SELECT count(*) FROM history_scenario').fetchone()[0]
out['experimentRequestContexts']=db.execute('SELECT count(DISTINCT request_key) FROM ea_experiment').fetchone()[0]
out['sessionRequestContexts']=db.execute('SELECT count(DISTINCT request_key) FROM ea_session').fetchone()[0]
trial.atomic(source.with_name('Community-Knowledge-20260928.packed-trial.sqlite.support')/'source-scope-details.json',out)
print(json.dumps(out));db.close()
