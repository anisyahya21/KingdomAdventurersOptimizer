"""Fleet status must stay report-equivalent without invoking full Coordinator.report copies.

Run: python tools/recovery/check_optimizer_fleet_reporting.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import strategy_encounter_search as search
import strategy_optimizer as optimizer


def make_coordinator(encounter_id, *, blocked=False):
    coord = search.Coordinator(path=':memory:', workers=2)
    member = dict(candidateId='candidate-%d' % encounter_id, reserved=2, completed=1,
                  blocked=blocked, jobs=[dict(id='job-%d' % encounter_id)],
                  observedCandidate=dict(private='not-public'),
                  nested=dict(counter=encounter_id))
    coord.enabled = True
    coord.encounter = encounter_id
    coord.session_id = 'session-%d' % encounter_id
    coord.state = dict(roundIndex=3, cohort=[member], current=member,
                       completedExperiments=[dict(id='done')], retiredFrozenPlans=[dict(id='old')],
                       paired=[dict(id='paired')], confirmation=dict(pending=True),
                       publication=None, queue=[dict(id='queued')], idle=False,
                       unspendable=['budget'], unspendableTasks=[dict(task='blocked')],
                       timings=dict(planningSeconds=.25), portfolio=[], boundaries=[], history=[])
    return coord


def main():
    coords = {encounter_id: make_coordinator(encounter_id, blocked=(encounter_id < 17))
              for encounter_id in range(20)}
    reports = {encounter_id: coord.report() for encounter_id, coord in coords.items()}
    expected = reports[0]
    for coord in coords.values():
        coord.report = lambda: (_ for _ in ()).throw(
            AssertionError('fleet status must not call full Coordinator.report()'))

    view = optimizer.Optimizer.__new__(optimizer.Optimizer)
    view._campaign_coordinators = coords
    view._community_fleet_state = dict(
        status='active', campaignId='report-fixture', enabledEncounterIds=list(coords),
        encounters={str(encounter_id): dict(generation=encounter_id + 1)
                    for encounter_id in coords})
    status = view._campaign_fleet_status()

    # Fleet publication may reuse the reports it already built, but campaign rows must remain
    # detached from the sibling `perEncounter` snapshots and runtime coordinator state.
    view._campaign_coordinator_status = lambda _coord: (_ for _ in ()).throw(
        AssertionError('report reuse must not rebuild coordinator status'))
    reused = view._campaign_fleet_status(reports=reports)

    first = status['encounters']['0']
    reused_first = reused['encounters']['0']
    # Available memory is sampled independently for each report and may change while the
    # desktop optimizer is running. Compare the stable report fields exactly.
    stable_expected = json.loads(json.dumps(expected['timings']))
    stable_actual = json.loads(json.dumps(first['timings']))
    for timings in (stable_expected, stable_actual):
        (timings.get('proposalWorkers') or {}).pop('freeBytes', None)
    equivalent = (first['sessionId'] == expected['sessionId']
                  and first['progress'] == expected['progress']
                  and stable_actual == stable_expected)
    reuse_equivalent = (reused_first['sessionId'] == first['sessionId']
                        and reused_first['progress'] == first['progress']
                        and reused_first['timings'] == expected['timings'])
    first['progress']['cohort'][0]['nested']['counter'] = -1
    first['progress']['current']['nested']['counter'] = -2
    reused_first['progress']['cohort'][0]['nested']['counter'] = -3
    reused_first['progress']['current']['nested']['counter'] = -4
    detached = (coords[0].state['cohort'][0]['nested']['counter'] == 0
                and expected['progress']['cohort'][0]['nested']['counter'] == 0
                and expected['progress']['current']['nested']['counter'] == 0)

    checks = dict(
        all_20_encounters_reported=len(status['encounters']) == 20,
        legacy_progress_and_timing_shape_preserved=equivalent,
        reused_report_matches_legacy_campaign_view=reuse_equivalent,
        reused_campaign_rows_do_not_alias_per_encounter_reports=detached,
        status_snapshot_detached_from_runtime=detached,
        blocked_and_runnable_counts_preserved=(status['activeEncounters'] == 20
                                                and status['runnableEncounters'] == 3),
        no_full_reports_invoked=True)
    failures = [name for name, passed in checks.items() if not passed]
    print(json.dumps(dict(passed=not failures, checks=checks, failures=failures), sort_keys=True))
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
