"""Actual desktop Optimizer loop: pause, drain, resume and close a partially dispatched cohort."""
import json, tempfile, time
from pathlib import Path
import strategy_optimizer as so
import strategy_encounter_search as search
import check_cohort_dispatch as fixture

pool=fixture.GatedPool([])
search.EVALUATOR_FACTORY=fixture.base.mock_factory(pool)
opt=so.Optimizer(Path(tempfile.mkdtemp(prefix='ka-queued-resume-'))/'scratch.sqlite')
def wait(test):
    end=time.monotonic()+15
    while time.monotonic()<end:
        value=opt.status()
        if value.get('error'): raise AssertionError(value['error'])
        if test(value): return value
        time.sleep(.05)
    evaluator=opt._community_evaluator
    raise AssertionError({'state':value.get('state'),'error':value.get('error'),
                          'poolPending':len(pool.pending),
                          'evaluatorPending':len(evaluator.pending) if evaluator else None,
                          'evaluatorWorkers':evaluator.workers if evaluator else None})
try:
    wait(lambda v: opt._campaign is not None)
    # The concurrent fleet uses the persisted multi-select focus, not the retired campaign's
    # in-memory encounter_ids list.
    opt.command('focus_encounter', {'encounterIds': [19]}, wait=True)
    # The requested total includes two planners; 24 total therefore provides 22 battle slots.
    opt.command('community_start',{'workers':24,'duty':1.,'newCampaign':True},wait=True)
    evaluator=opt._community_evaluator
    assert evaluator is not None and evaluator.workers==22 and len(evaluator._slot_ready)==22
    assert all(coord.evaluator is evaluator for coord in opt._campaign_coordinators.values())
    wait(lambda v: len(evaluator.pending)==22)
    sid=opt._encounter.session_id
    opt.command('pause',wait=True)
    pool.release(len(pool.pending))
    wait(lambda v: v['state']=='Paused')
    assert not evaluator.pending, 'pause must harvest every accepted battle before returning'
    assert opt._encounter._has_work() and not opt._encounter.busy
    remaining=sum(m['planned'] for m in opt._encounter._cohort())
    assert remaining>0, 'fixture must retain undispatched authorised jobs'
    opt.command('community_start',{'workers':24,'duty':1.},wait=True)
    assert opt._community_evaluator is evaluator, 'same worker configuration should keep the shared evaluator'
    wait(lambda v: len(evaluator.pending)==22)
    assert opt._encounter.session_id==sid
    opt.command('pause',wait=True); pool.release(len(pool.pending))
    wait(lambda v: v['state']=='Paused')
    assert not evaluator.pending, 'resumed work must also drain completely on pause'
    print('PASS: paused queued cohort resumes the SAME budget session; submitted work drains; '
          'queued work survives; no false busy refusal')
finally:
    opt.command('close')
    pool.release(len(pool.pending))
    opt.thread.join(timeout=15)
    assert not opt.thread.is_alive(), 'close failed with saved queued work'
    print('PASS: desktop shutdown completes with a checkpointed partial cohort')
