"""Deterministic and negative tests for `combat_evaluation.evaluate` (ka-battle-eval-1).

Two layers:

  * a synthetic runner exercises the statistics contract exactly (histogram, censoring, sampling
    labels, ranking, budget validation) with known inputs;
  * the real authoritative runner runs one recorded research scenario across two
    `(encounterId, defeatCount)` levels so the enemy level, determinism, the censored/resolved
    split, and the exported `finish`/`postFinish` block are checked end to end.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from combat_evaluation import EvaluationError, evaluate, strategy_catalog
from combat_initial_state import EVIDENCE
from combat_runtime_data import WORKSPACE_ROOT, load_data

RECORDED = (WORKSPACE_ROOT / 'RE-evidence/20260919-configurable-battle-setup/run-battle-16.5'
                              '/scenarios/R.scenario.json')


SEEDS = [[1, 1], [3, 3], [5, 5], [7, 7]]
# marker -> (mathSeed, libSeed) -> (verdict, dispatched chest entities); a loss dispatches zero
SYNTHETIC_PLAN = {
    'win-first': {(1, 1): (1, 1), (3, 3): (1, 1), (5, 5): (2, 0), (7, 7): (2, 0)},
    'yield-first': {(1, 1): (1, 5), (3, 3): (2, 0), (5, 5): (2, 0), (7, 7): (2, 0)},
    'has-censored': {(1, 1): (1, 2), (3, 3): (2, 0), (5, 5): (2, 0), (7, 7): None},
    'has-error': {(1, 1): (1, 2), (3, 3): (2, 0), (5, 5): 'raise', (7, 7): (2, 0)},
    'approximate': {(1, 1): (1, 3), (3, 3): (2, 0), (5, 5): (2, 0), (7, 7): (2, 0)},
}
# Runners must disclose manifest.support.conditionalSimulation. A synthetic runner supplies the
# contract explicitly; `approximate` supplies supported=False to prove it cannot be ranked.
SUPPORTED = {'win-first', 'yield-first', 'has-censored', 'has-error'}
# No declared finish policy is a proven native-automatic producer, so the dispatched-chest metric
# is measured but nothing is a rankable yield. Every candidate is therefore unranked with this
# exact reason (check_combat_auto_finish_producer.py); a definitive ranking cannot be asserted
# until the real producer is recovered.
FINISH_UNRANKED_FRAGMENT = ('no automatic native producer of the STATE_ENDING 3 exit 0x14ef538 '
                            'is proven')


def fake_runner(scenario, include_trace):
    """Synthetic reports with known outcomes keyed by the candidate marker."""
    marker = scenario.get('marker')
    defeat, math_seed, lib_seed = scenario['defeatCount'], scenario['mathSeed'], scenario['libSeed']
    level = 10 + defeat // 5  # mirrors special_enemy_baseline's levelField + defeatCount//5
    outcome = SYNTHETIC_PLAN[marker][(math_seed, lib_seed)]
    if outcome == 'raise':
        raise ValueError('synthetic runner error')
    if outcome is None:
        return dict(level=level, defeatCount=defeat, trace=[],
                    manifest=dict(support=dict(initialization='profile',
                        sourceState=dict(source='declared isolated-scene0 startProfile (simulation condition, not captured state)'),
                        conditionalSimulation=dict(supported=True, unmetConditions=[], conditions=[], scope='synthetic'))),
                    result=dict(verdict=None, censored=True, ticks=7, stopReason='tick-horizon'),
                    finish=None)
    verdict, queued = outcome
    trace = ([dict(kind='state', new=8, target=1)]
             + [dict(kind='prize', target=1, treasureId=i) for i in range(queued)])
    finish = dict(queuedChestAwards=dict(count=queued),
                  dispatchedChestAwards=dict(count=queued),
                  inventoryCollection=dict(state='not modelled'), exp='not modelled')
    ok = marker in SUPPORTED
    manifest = dict(support=dict(
        initialization='profile' if ok else 'approximate',
        sourceState=dict(source='declared isolated-scene0 startProfile (simulation condition, not captured state)'
                                if ok else 'final-cell approximation (not native)'),
        conditionalSimulation=dict(supported=ok, unmetConditions=[] if ok else ['source_state'],
                                   conditions=[], scope='synthetic')))
    return dict(level=level, defeatCount=defeat, manifest=manifest,
                result=dict(verdict=verdict, censored=False, ticks=7, stopReason='synthetic'),
                trace=trace, finish=finish)


def synthetic_checks():
    request = dict(schema='ka-battle-eval-1', tickLimit=400,
                   levels=[dict(encounterId=3, defeatCount=0), dict(encounterId=3, defeatCount=6)],
                   seeds=[list(seed) for seed in SEEDS],
                   candidates=[dict(id=marker, scenario=dict(marker=marker))
                               for marker in ('win-first', 'yield-first', 'has-censored', 'has-error',
                                              'approximate')])
    out = evaluate(request, runner=fake_runner, replay_exporter=lambda s, include_events=True: {'schema': 'X'})
    assert out['budget']['plannedRuns'] == 40
    by_id = {c['id']: c for c in out['candidates']}

    # the objective is expected DISPATCHED chests per attempt, not win probability first
    win_first, yield_first = by_id['win-first']['pooled'], by_id['yield-first']['pooled']
    assert win_first['winProbability'] == 0.5 and yield_first['winProbability'] == 0.25
    assert win_first['dispatchedChests']['statistics']['mean'] == 0.5
    assert yield_first['dispatchedChests']['statistics']['mean'] == 1.25
    assert 'dispatched chest entities per attempted fight' in out['rankings']['metric']
    # FAIL CLOSED: the metric is still measured (yield-first > win-first above), but no declared
    # finish policy is a proven native-automatic producer, so nothing receives a rank.
    assert out['ranking'] == [] and out['rankings']['pooled']['ranked'] == [], out['ranking']
    assert all(any(FINISH_UNRANKED_FRAGMENT in reason for reason in entry['reasons'])
               for entry in out['unranked'])

    # per-level rankings are first-class; the pooled ranking is the secondary view
    assert len(out['rankings']['perLevel']) == 2
    for level_ranking in out['rankings']['perLevel']:
        assert level_ranking['ranked'] == []
        assert [e['id'] for e in level_ranking['unranked']] == [
            'win-first', 'yield-first', 'has-censored', 'has-error', 'approximate']
        assert (level_ranking['level']['encounterId'], level_ranking['level']['defeatCount']) == (3, 0) \
            or (level_ranking['level']['encounterId'], level_ranking['level']['defeatCount']) == (3, 6)
        assert level_ranking['level']['level'] is None  # read off a ranked entry; none is ranked
        assert all(any(FINISH_UNRANKED_FRAGMENT in reason for reason in entry['reasons'])
                   for entry in level_ranking['unranked'])

    # an incomplete/censored or errored candidate is unranked with an explicit reason, never
    # scored on the easy subset of its runs
    unranked = {u['id']: u for u in out['unranked']}
    assert set(unranked) == {'win-first', 'yield-first', 'has-censored', 'has-error', 'approximate'}
    assert out['rankings']['pooled']['unranked'] == out['unranked']
    assert 'censored' in unranked['has-censored']['reasons'][0]
    assert any('error' in reason for reason in unranked['has-error']['reasons'])
    assert 'error' in unranked['has-error']['errors'][0]
    assert by_id['has-censored']['status'] == 'measured' and by_id['has-error']['status'] == 'partial'
    # an unsupported/approximate candidate is unranked with the exact unmet reason and is never
    # the selected winner even though its measured yield is the highest
    assert any('conditional simulation is not supported' in reason and 'source_state' in reason
               for reason in unranked['approximate']['reasons']), unranked['approximate']
    assert by_id['approximate']['pooled']['dispatchedChests']['statistics']['mean'] > 0
    # even the highest measured diagnostic yield is not a rankable yield, so the pooled ranking
    # stays empty rather than publishing an approximate favourite.
    assert out['rankings']['pooled']['ranked'] == []

    # observed losses dispatch exactly zero, as the native result, and the zero is counted
    assert win_first['dispatchedChests']['histogram'] == [dict(count=0, frequency=4), dict(count=1, frequency=4)]
    assert win_first['retainedInventory']['counted'] is False
    # a loss is zero dispatch but chest collection stays unknown, never a retained number
    assert win_first['dispatchedChests']['basis'].endswith('inventory receipt')

    # sampling bounds: no inferential interval; n=1 is suppressed, n>=2 reports a descriptive range
    stats = win_first['dispatchedChests']['statistics']
    assert stats['samples'] == 8 and stats['interval95'] is None
    assert stats['descriptiveRange'] == [0.0, 1.0]
    assert 'user-chosen' in stats['interval95SuppressedReason']
    assert 'descriptive sample variation' in stats['label']
    one_seed = dict(request, seeds=[[1, 1]])
    single = evaluate(one_seed, runner=fake_runner, replay_exporter=lambda s, include_events=True: {'schema': 'X'})
    single_stats = {c['id']: c for c in single['candidates']}['win-first']['levels'][0][
        'dispatchedChests']['statistics']
    assert single_stats['samples'] == 1 and single_stats['interval95'] is None
    assert single_stats['descriptiveRange'] is None
    assert 'single sample' in single_stats['interval95SuppressedReason']

    for candidate in out['candidates']:
        for level in candidate['levels']:
            assert level['seeds'] == [list(seed) for seed in SEEDS], 'the exact seed list must be echoed'
            assert level['retainedInventory']['counted'] is False
            assert 'never converted into a zero-chest failure' in level['censoring']['note']
            assert level['runs'] + len(level['errors']) == 4
    assert by_id['win-first']['levels'][0]['dispatchedChests']['statistics']['mean'] == 0.5
    assert by_id['win-first']['levels'][0]['eventEvidence']['leavingEntries'] == 4
    assert by_id['win-first']['levels'][0]['eventEvidence']['prizeEventRecords'] == 2

    # no winner: the envelope refuses to select or replay an unrankable candidate
    assert out['selected'] is None and out['replay'] is None
    assert evaluate(request, runner=fake_runner, replay_exporter=lambda s, include_events=True: {'schema': 'X'}) == out
    # level comes from defeatCount//5, so defeat 0 and defeat 6 are different levels of the same encounter
    assert [lv['level']['level'] for lv in by_id['win-first']['levels']] == [10, 11]
    assert strategy_catalog()[0]['status'].startswith('mechanism explained')
    return out


def rejection_checks():
    """Negative validation: every bounded input is checked before a run."""
    base = dict(schema='ka-battle-eval-1', levels=[dict(encounterId=0, defeatCount=0)], seeds=[[1, 2], [3, 4]],
                candidates=[dict(id='C', scenario={})])
    def expect(patch, fragment):
        request = json.loads(json.dumps(base))
        request.update(patch)
        try:
            evaluate(request, runner=fake_runner, replay_exporter=lambda s, include_events=True: None)
        except EvaluationError as error:
            assert fragment in str(error), (fragment, str(error))
            return
        raise AssertionError(f'expected EvaluationError containing {fragment!r}')
    expect(dict(schema='nope'), 'schema must be ka-battle-eval-1')
    expect(dict(limits=dict(maxRuns=1)), 'run budget exceeded')
    expect(dict(seeds=[]), 'seeds must be a non-empty list')
    expect(dict(seeds=[[1, 2], [1, 2]]), 'seeds must be distinct')
    expect(dict(seeds=[[1]]), 'each seed must be')
    expect(dict(levels=[dict(encounterId=20, defeatCount=0)]), 'encounterId must be in 0..19')
    expect(dict(levels=[dict(encounterId=0, defeatCount=-1)]), 'defeatCount must be nonnegative')
    expect(dict(tickLimit=10001), 'tickLimit must be <= limits.maxTicks')
    expect(dict(tickLimit=0), 'tickLimit must be a positive integer')
    expect(dict(candidates=[]), 'candidates must be a non-empty list')
    expect(dict(candidates=[dict(id='oversized', scenario=dict(ownUnits=[{}] * 65))]),
           'ownUnits exceeds the 64 unit cap')
    expect(dict(candidates=[dict(id='C', scenario={}), dict(id='C', scenario={})]), 'duplicate candidate id')
    # a real run rejects an illegal scenario per candidate instead of aborting the whole request
    bad = dict(schema='ka-battle-eval-1', levels=[dict(encounterId=0, defeatCount=0)], seeds=[[1, 2]],
               candidates=[dict(id='bad', scenario={})])
    out = evaluate(bad)
    assert out['ranking'] == [] and out['replay'] is None
    assert out['unranked'] and out['unranked'][0]['id'] == 'bad'
    assert 'ScenarioError' in out['unranked'][0]['errors'][0]
    return out


def real_runner_checks():
    """One recorded legal candidate across two selected levels through the authoritative runner."""
    scenario = json.loads(RECORDED.read_text(encoding='utf-8'))
    candidate_scenario = {
        **{key: value for key, value in scenario.items()
           if key not in ('schema', 'encounterId', 'defeatCount', 'mathSeed', 'libSeed', 'tickLimit')},
        'startProfile': dict(kind='isolated-scene0', enemySpawnCell=[0, 0],
                             bossCell=None, startingStatus={}),
    }
    request = dict(schema='ka-battle-eval-1', tickLimit=3000, limits=dict(maxRuns=8),
                   levels=[dict(encounterId=19, defeatCount=0), dict(encounterId=19, defeatCount=5)],
                   seeds=[[7, 8]],
                   candidates=[dict(id='recorded-R', label='recorded research fixture',
                                    scenario=candidate_scenario)])
    out = evaluate(request)
    levels = out['candidates'][0]['levels']
    level_field = {e['id']: e['levelField'] for e in load_data('encounters.json')['encounters']}[19]
    assert [lv['level']['level'] for lv in levels] == [level_field, level_field + 1], 'actual level formula'
    assert all(lv['seeds'] == [[7, 8]] for lv in levels)
    assert sum(lv['runs'] for lv in levels) == 2
    assert all(lv['winProbability'] + lv['lossProbability'] + lv['censoredProbability'] == 1 for lv in levels)
    # FAIL CLOSED: the declared at-horizon finish policy is not a proven native-automatic
    # producer, so the envelope ranks nothing and returns no winner replay. The recorded run is
    # unranked for exactly that reason; the real export pipeline is exercised directly on the
    # same validated scenario below.
    assert out['ranking'] == [] and out['selected'] is None and out['replay'] is None
    assert [entry['id'] for entry in out['unranked']] == ['recorded-R']
    assert any(FINISH_UNRANKED_FRAGMENT in reason for reason in out['unranked'][0]['reasons']), \
        out['unranked'][0]['reasons']
    # supported combat semantics stay separate from the finish policy: every source/skill
    # condition is met and the only unmet condition is the global unproven automatic Finish.
    conditional = out['candidates'][0]['conditionalSimulation']
    assert conditional['supported'] is False
    assert conditional['unmetConditions'] == ['yield_eligible_finish_policy'], conditional['unmetConditions']
    assert conditional['sourcePolicies'], 'the real run must disclose a source policy'
    from combat_evaluation import scenario_for
    from combat_replay_export import export_replay
    replay_scenario, _ = scenario_for(dict(id='recorded-R', scenario=candidate_scenario),
                                      request['levels'][0], request['seeds'][0], request['tickLimit'])
    replay = export_replay(replay_scenario, include_events=True)
    assert replay['schema'] == 'ka-battle-replay-1'
    assert 'finish' in replay and 'postFinish' in replay and 'events' in replay
    assert replay['postFinish']['mathDrawsAfterFinish'] == (
        replay['postFinish']['mathDrawsInTrace']
        + 3 * replay['finish']['dispatchedChestAwards']['count'])
    assert replay['finish'] is not None and replay['postFinish']['finishApplied'] is True
    assert replay['postFinish']['exp'] == 'not modelled'
    assert isinstance(replay['postFinish']['confirmation'], str) and replay['postFinish']['confirmation']
    assert json.dumps(evaluate(request)) == json.dumps(out), 'evaluation must be deterministic'
    return dict(evaluation=out, replay=replay)


def main():
    synthetic = synthetic_checks()
    rejected = rejection_checks()
    real = real_runner_checks()
    report = dict(
        schema='ka-combat-evaluation-checks-1',
        syntheticRuns=synthetic['budget']['plannedRuns'],
        syntheticRanking=synthetic['ranking'],
        syntheticUnranked=[entry['id'] for entry in synthetic['unranked']],
        finishUnrankedReason=FINISH_UNRANKED_FRAGMENT,
        rejectedCandidate=rejected['unranked'][0],
        realLevels=[level['level'] for level in real['evaluation']['candidates'][0]['levels']],
        realVerdicts=[level['resolvedRuns'] for level in real['evaluation']['candidates'][0]['levels']],
        realReplaySchema=real['replay']['schema'],
        realFinishApplied=real['replay']['postFinish']['finishApplied'],
        probed=[
            'per-level win/loss/censored frequencies sum to the completed runs',
            'sampling-only labels on every probability and chest statistic',
            'the censored run never enters the queued/dispatched chest statistics',
            'retained inventory is not modelled and not counted',
            'the exact disclosed seed list is echoed with every level summary',
            'the enemy level equals the recovered levelField + defeatCount//5',
            'every candidate is unranked with the exact unproven-automatic-Finish reason',
            'the winner replay carries report.finish verbatim plus the postFinish disclosure',
            'a candidate whose source policy is unsupported is unranked with its unmet condition',
            'identical requests produce byte-identical result envelopes',
        ],
        limits=['Synthetic reports exercise the statistics contract; the recorded-scenario run exercises the real pipeline.',
                'A rejected candidate is reported as unranked instead of aborting the request.',
                'No candidate is ranked while the native automatic Finish producer is unproven, so the recorded run is exercised through the direct export instead of a winner replay.'])
    (EVIDENCE / 'evaluation-checks.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: report[key] for key in ('syntheticRuns', 'syntheticRanking', 'realLevels')}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
