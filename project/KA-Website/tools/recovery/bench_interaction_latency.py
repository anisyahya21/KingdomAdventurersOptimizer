"""Bounded representative latency measurement for the fast consumable interaction path.

Fixture: `uref.scenario.json` (6 own / 21 enemy, tickLimit 6070) plus a declared holy-herb stock, the
same representative request the battle-ship latency table uses. Nothing here is a test suite: it
measures, prints, and writes `RE-evidence/20260921-battle-ship/measured-latency-fast.json`.

Usage: python bench_interaction_latency.py [--ticks 1000,3000,5000] [--window 120]
"""
import argparse
import json
import pathlib
import sys
import time
from copy import deepcopy

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from combat_interaction import (INTERACTION_SCHEMA, POLL_SCHEMA, WINDOW_TICKS, clear_sessions,
                                job_state, last_run, poll_branch, prime_session, run_interaction)
from combat_replay_export import export_replay

WORKSPACE = HERE.parents[2]
UREF = WORKSPACE / 'RE-evidence/20260920-battle-regression-pass/uref.scenario.json'
OUT = WORKSPACE / 'RE-evidence/20260921-battle-ship/measured-latency-fast.json'


def source_scenario():
    scenario = json.loads(UREF.read_text(encoding='utf-8'))
    scenario['holyHerbStock'] = 2
    return scenario


def request_for(scenario, tick, item='holy_herb', window=None):
    body = dict(schema=INTERACTION_SCHEMA, scenario=scenario,
                command=dict(kind='use-item', tick=tick, phase='before_fighters', item=item),
                displayedTick=tick)
    if window is not None:
        body['windowTicks'] = window
    return body


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ticks', default='1000,3000,5000')
    parser.add_argument('--window', type=int, default=WINDOW_TICKS)
    parser.add_argument('--jobwait', type=float, default=120.0)
    args = parser.parse_args(argv)
    ticks = [int(part) for part in args.ticks.split(',')]
    source = source_scenario()
    report = dict(schema='ka-battle-ship-latency-fast-1', measuredAt=time.strftime('%Y-%m-%d'),
                  host='Windows, Python %s, in-process packaged runtime, no server' % sys.version.split()[0],
                  fixture=dict(scenario=str(UREF.relative_to(WORKSPACE)), ownUnits=len(source['ownUnits']),
                               tickLimit=source['tickLimit'], holyHerbStock=source['holyHerbStock'],
                               windowTicks=args.window), clicks=[])

    clear_sessions()
    started = time.perf_counter()
    replay = export_replay(deepcopy(source), include_events=True)
    cold = time.perf_counter() - started
    clear_sessions()
    started = time.perf_counter()
    primed_replay = prime_session(deepcopy(source))
    primed = time.perf_counter() - started
    assert primed_replay == replay, 'priming changed the displayed payload'
    print('display: cold %.2fs, primed %.2fs, horizon %d, verdict %s' % (
        cold, primed, replay['ticks'], replay['finalState']['verdict']))
    report['display'] = dict(coldSeconds=round(cold, 3), primedSeconds=round(primed, 3),
                             payloadIdentical=primed_replay == replay)

    for tick in ticks:
        clear_sessions()
        prime_session(deepcopy(source))
        started = time.perf_counter()
        full = run_interaction(request_for(source, tick))
        full_seconds = time.perf_counter() - started

        clear_sessions()
        prime_session(deepcopy(source))
        started = time.perf_counter()
        window = run_interaction(request_for(source, tick, window=args.window))
        window_seconds = time.perf_counter() - started

        final = window['replay']['finalState']
        job_id = window['window']['jobId']
        edge = len(window['replay']['events'])
        assert window['replay']['events'] == full['replay']['events'][:edge], 'window is not the branch prefix'
        assert window['prefix'] == full['prefix'] and window['acceptance'] == full['acceptance']

        started = time.perf_counter()
        while True:
            try:
                reply = poll_branch(dict(schema=POLL_SCHEMA, jobId=job_id,
                                         fromTick=final['windowStopTick'] + 1))
            except Exception as error:
                if getattr(error, 'code', None) != 'interaction-job-pending':
                    raise
                time.sleep(0.05)
                continue
            if reply['state'] == 'ready':
                job_seconds = time.perf_counter() - started
                break
            assert time.perf_counter() - started < args.jobwait, 'branch job did not finish'
            time.sleep(0.05)
        merged = dict(reply['replay'],
                      events=[e for e in window['replay']['events']
                              if e['tick'] <= final['windowStopTick']] + reply['replay']['events'])
        identical = json.dumps(merged, sort_keys=True) == json.dumps(full['replay'], sort_keys=True)
        entry = dict(tick=tick, resumedFrom=last_run()['checkpointTick'],
                     fullSeconds=round(full_seconds, 3), windowSeconds=round(window_seconds, 3),
                     speedup=round(full_seconds / max(window_seconds, 1e-6), 1),
                     windowStopTick=final['windowStopTick'],
                     windowRemainingTicks=final['windowRemainingTicks'],
                     windowCoverSeconds=round(final['windowRemainingTicks'] / 20.0, 2),
                     jobSeconds=round(job_seconds, 3), jobIdenticalToSyncBranch=identical,
                     stockAfter=window['acceptance']['stock']['after'])
        report['clicks'].append(entry)
        print('click@%5d: full %6.2fs -> window %5.2fs (x%4.1f), edge %d, job ready in %5.2fs, '
              'async==sync %s, stock after %s' % (tick, full_seconds, window_seconds, entry['speedup'],
                                                  final['windowStopTick'], job_seconds, identical,
                                                  entry['stockAfter']))
    clear_sessions()
    report['jobsAfterClear'] = len(job_state()['jobs'])
    report['note'] = ('windowSeconds is the honest click latency: the response carries the true engine branch '
                      'through command.tick plus a short window; jobSeconds is the asynchronous remainder, '
                      'covered by the window while the client keeps playing.')
    OUT.write_text(json.dumps(report, indent=1) + '\n', encoding='utf-8')
    print(json.dumps(dict(out=str(OUT.relative_to(WORKSPACE)), clicks=len(report['clicks']))))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
