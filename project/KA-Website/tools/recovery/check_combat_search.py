"""Deterministic and negative tests for `combat_search.search` (ka-battle-search-1).

The synthetic runner makes the enumerated variant visible in the outcome (`holyHerbStock` becomes
the queued chest count), so the test proves the search really measures every candidate x variant
combination and selects the best one - it is not asserting a hard-coded answer.

The strategy checks below cover bounded generation: the search wiring (per-candidate enumeration,
the unchanged candidate first, the variant cap, the run budget) is exercised with a stub generator,
the generators themselves are exercised against the real skill profiles and the recorded fixture,
and the "which input orders actually matter" rule is confirmed with real runs of the authoritative
runner (`combat_sandbox.run_scenario`).
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from combat_evaluation import EvaluationError
from combat_initial_state import EVIDENCE
from combat_search import (
    GENERATION_CLAIM, formation_arrangements, generate_variants, native_placement_keys,
    native_skill_rows, search, skill_arrangements,
)

WORKSPACE = Path(__file__).resolve().parents[3]
RECORDED = (WORKSPACE / 'RE-evidence/20260919-configurable-battle-setup/run-battle-16.5'
            / 'scenarios/R.scenario.json')
RECORDED_TWINS = (WORKSPACE / 'RE-evidence/20260919-configurable-battle-setup/run-battle-16.5'
                  / 'scenarios/RD.scenario.json')

CLAIM_FRAGMENT = 'not an exhaustive search and no global optimum is claimed'
# Every rankable yield needs a proven native-automatic Finish. Until that producer is recovered the
# model fails closed: each combination is measured but none is ranked or selected. These checks
# therefore assert the unranked outcome plus this exact reason instead of a definitive ranking.
FINISH_UNRANKED_FRAGMENT = ('no automatic native producer of the STATE_ENDING 3 exit 0x14ef538 '
                            'is proven')


def assert_unranked_finish_fail_closed(result, expected_ids):
    """No ranking, no winner replay, and every measured combination unranked for the finish reason."""
    assert result['ranking'] == [], result['ranking']
    assert result['selected'] is None and result['replay'] is None
    assert sorted(entry['id'] for entry in result['unranked']) == sorted(expected_ids)
    for entry in result['unranked']:
        assert any(FINISH_UNRANKED_FRAGMENT in reason for reason in entry['reasons']), entry


def fake_runner(scenario, include_trace):
    """One deterministic win whose queued chest count is the scenario's holyHerbStock."""
    queued = scenario.get('holyHerbStock', 0)
    trace = [dict(kind='state', new=8, target=1)] + [dict(kind='prize', target=1, treasureId=i)
                                                     for i in range(queued)]
    return dict(level=scenario['encounterId'] + scenario['defeatCount'] // 5,
                defeatCount=scenario['defeatCount'], manifest=SYNTHETIC_SUPPORT,
                result=dict(verdict=1, censored=False, ticks=5, stopReason='synthetic'),
                trace=trace,
                finish=dict(queuedChestAwards=dict(count=queued),
                            dispatchedChestAwards=dict(count=queued),
                            inventoryCollection=dict(state='not modelled'), exp='not modelled'))


SYNTHETIC_SUPPORT = dict(support=dict(
    initialization='profile',
    sourceState=dict(source='declared isolated-scene0 startProfile (simulation condition, not captured state)'),
    conditionalSimulation=dict(supported=True, unmetConditions=[], conditions=[], scope='synthetic')))


def base_request():
    return dict(schema='ka-battle-search-1', tickLimit=100,
                levels=[dict(encounterId=2, defeatCount=0)], seeds=[[1, 1]],
                candidates=[dict(id='C1', scenario=dict(holyHerbStock=1, ownUnits=[])),
                            dict(id='C2', scenario=dict(holyHerbStock=1, ownUnits=[]))],
                variants=[dict(id='A', label='stock 3', patch=dict(holyHerbStock=3)),
                          dict(id='B', label='stock 4', patch=dict(holyHerbStock=4))])


def strategy_checks():
    """Search wiring for generated variants: per-candidate enumeration, base first, cap, mapping."""
    def stub_generator(scenario, options, placement_keys=None, skill_rows=None):
        cap = options['maxVariantsPerCandidate']
        variants = [dict(id=f'generated-{index}', kind='formation', label='generated formation order',
                         description='reordered ownUnits', patchKeys=['ownUnits'],
                         changes=dict(ownUnitsOrder=[unit['name'] for unit in reversed(scenario['ownUnits'])]),
                         units=list(reversed(scenario['ownUnits'])))
                    for index in range(1, 4)]
        truncated = cap is not None and len(variants) > cap
        if truncated:
            variants = variants[:cap]
        return dict(available=3, generated=len(variants), truncated=truncated, cap=cap,
                    formation=dict(enabled=True, classes=[], arrangements=3, reason=None),
                    skillPriorities=dict(enabled=True, units=[], arrangements=0, reason=None),
                    variants=variants)

    def runner(scenario, include_trace):
        queued = 2 + (3 if scenario['ownUnits'][0]['name'] == 'B' else 0)
        return dict(level=scenario['encounterId'], defeatCount=scenario['defeatCount'],
                    manifest=SYNTHETIC_SUPPORT,
                    result=dict(verdict=1, censored=False, ticks=5, stopReason='synthetic'), trace=[],
                    finish=dict(queuedChestAwards=dict(count=queued),
                                dispatchedChestAwards=dict(count=queued),
                                inventoryCollection=dict(state='not modelled'), exp='not modelled'))

    exporter = lambda scenario, include_events=True: {
        'schema': 'ka-battle-replay-1', 'ownUnits': [unit['name'] for unit in scenario['ownUnits']]}
    units = [dict(name='A', skills=[], invocationLevels=[]), dict(name='B', skills=[], invocationLevels=[])]
    request = dict(schema='ka-battle-search-1', tickLimit=100, levels=[dict(encounterId=2, defeatCount=0)],
                   seeds=[[1, 1]], candidates=[dict(id='C1', label='team one', scenario=dict(ownUnits=units))],
                   strategy=dict(formations=True, skillPriorities=True, maxVariantsPerCandidate=2))
    out = search(request, runner=runner, replay_exporter=exporter, placement_key_provider=lambda s: {},
                 skill_row_provider=lambda: {}, generator=stub_generator)
    assert out['enumeration']['combinations'] == 3, out['enumeration']
    assert out['enumeration']['variantIds'] == ['base', 'generated-1', 'generated-2']
    assert out['enumeration']['plannedRuns'] == 3 and out['budget']['plannedRuns'] == 3
    assert out['enumeration']['variantPatchKeys'] == ['ownUnits']
    generated = out['enumeration']['generated']
    assert generated['availableVariants'] == 3 and generated['generatedVariants'] == 2
    candidate_report = generated['perCandidate']['C1']
    assert candidate_report['truncated'] is True and candidate_report['cap'] == 2
    assert [entry['id'] for entry in candidate_report['variants']] == ['base', 'generated-1', 'generated-2']
    assert candidate_report['variants'][0]['changes'] == {}
    assert candidate_report['variants'][1]['changes']['ownUnitsOrder'] == ['B', 'A']
    assert 'units' not in candidate_report['variants'][1], 'the full unit payload is never echoed'
    # fail closed: every generated combination is measured, base first, but none is ranked and no
    # winner replay is returned until the native automatic Finish producer is proven.
    assert_unranked_finish_fail_closed(out, ['C1::base', 'C1::generated-1', 'C1::generated-2'])
    assert GENERATION_CLAIM in out['claim'] and out['caveats'][-1] == out['claim']
    assert out['support']['claim'] == out['claim']
    assert out['enumeration']['strategy']['maxVariantsPerCandidate'] == 2
    assert json.dumps(search(request, runner=runner, replay_exporter=exporter,
                             placement_key_provider=lambda s: {}, skill_row_provider=lambda: {},
                             generator=stub_generator)) == json.dumps(out)
    plain = search({**request, 'strategy': dict(formations=True)}, runner=runner,
                   replay_exporter=exporter, placement_key_provider=lambda s: {},
                   skill_row_provider=lambda: {}, generator=stub_generator)
    plain_report = plain['enumeration']['generated']
    assert plain_report['perCandidate']['C1']['cap'] == 32, 'the caller variant cap is the default'
    assert plain_report['availableVariants'] == plain_report['generatedVariants'] == 3
    assert plain_report['perCandidate']['C1']['truncated'] is False

    def expect(patch, fragment):
        bad = json.loads(json.dumps(request))
        bad.update(patch)
        try:
            search(bad, runner=runner, replay_exporter=exporter, placement_key_provider=lambda s: {},
                   skill_row_provider=lambda: {}, generator=stub_generator)
        except EvaluationError as error:
            assert fragment in str(error), (fragment, str(error))
            return
        raise AssertionError(f'expected EvaluationError containing {fragment!r}')
    expect(dict(variants=[dict(id='A', patch=dict(holyHerbStock=3))]),
           'cannot be combined with explicit variants')
    expect(dict(strategy=dict(invented=True)), 'strategy has unsupported keys')
    expect(dict(strategy=dict(formations='yes')), 'strategy.formations must be a boolean')
    expect(dict(strategy=dict(maxVariantsPerCandidate=0)), 'must be a positive integer')
    expect(dict(limits=dict(maxRuns=2), strategy=dict()), 'run budget exceeded')
    expect(dict(limits=dict(maxCandidates=2), strategy=dict()), 'search space has 4 combinations')
    return out


def generation_checks():
    """The real generators: what they emit, what they refuse to emit, and the cap."""
    rows = native_skill_rows()
    attacks = [row for row in rows.values() if row['category'] == 0 and row['flags'] & 8
               and not row['flags'] & 32 and not row['flags'] & 64]
    assert len(attacks) >= 2, 'the recovered profiles must contain attack skills'
    same_pass = dict(name='Synthetic Sam', skills=[attacks[0]['id'], attacks[1]['id']],
                     invocationLevels=[1, 0])
    orderings = skill_arrangements(same_pass, rows)
    assert len(orderings) == 1 and orderings[0]['skills'] == same_pass['skills'][::-1]
    assert orderings[0]['invocationLevels'] == same_pass['invocationLevels'][::-1], (
        'each skillId must stay paired with its own invocationLevel')
    assert skill_arrangements(dict(name='Mixed', skills=[37, 109], invocationLevels=[1, 1]), rows) == [], (
        'recovery and attack are separate native passes, so their slot order is not an input')
    tie = [dict(name='Alpha', marker=1), dict(name='Beta', marker=2)]
    swapped = formation_arrangements(tie, {'Alpha': (6, 0), 'Beta': (6, 0)})
    assert [entry['identity'] for entry in swapped] == [True, False]
    assert swapped[1]['assignment'] == {0: 1, 1: 0}
    assert [entry['identity'] for entry in
            formation_arrangements(tie, {'Alpha': (6, 0), 'Beta': (6, 1)})] == [True]
    twins = [dict(name='Twin A', marker=1), dict(name='Twin B', marker=1)]
    assert len(formation_arrangements(twins, {'Twin A': (6, 0), 'Twin B': (6, 0)})) == 1
    fixture = json.loads(RECORDED.read_text(encoding='utf-8'))
    report = generate_variants(fixture, dict(formations=True, skillPriorities=True,
                                             maxVariantsPerCandidate=8),
                               placement_keys=native_placement_keys(fixture), skill_rows=rows)
    assert report['available'] == 0 and report['truncated'] is False
    assert report['formation']['arrangements'] == 0 and report['formation']['classes'] == []
    assert report['skillPriorities']['arrangements'] == 0 and report['skillPriorities']['units'] == []
    twin_fixture = json.loads(RECORDED_TWINS.read_text(encoding='utf-8'))
    twin_report = generate_variants(twin_fixture, dict(formations=True, skillPriorities=True,
                                                       maxVariantsPerCandidate=8),
                                    placement_keys=native_placement_keys(twin_fixture), skill_rows=rows)
    assert twin_report['available'] == 0, 'swapping content-identical twins is not a distinct input'
    three = [dict(name=name, marker=index) for index, name in enumerate('ABC')]
    capped = generate_variants(dict(ownUnits=three), dict(formations=True, skillPriorities=False,
                                                          maxVariantsPerCandidate=2),
                               placement_keys={name: (6, 0) for name in 'ABC'}, skill_rows=rows)
    assert capped['available'] == 5 and capped['generated'] == 2 and capped['truncated'] is True
    for entry in capped['variants']:
        assert sorted(unit['name'] for unit in entry['units']) == ['A', 'B', 'C']
        assert sorted(unit['marker'] for unit in entry['units']) == [0, 1, 2]
    return report


def real_runner_evidence():
    """Which ownUnits orders and skill orders a real run of the authoritative runner distinguishes."""
    from combat_sandbox import run_scenario

    def run(data):
        return run_scenario(json.loads(json.dumps(data)), True)

    def fight(report):
        return dict(verdict=report['result']['verdict'], ticks=report['result']['ticks'],
                    metrics=report['metrics'], mathDraws=report['result']['mathDraws'],
                    dispatched=(report['finish'] or {}).get('dispatchedChestAwards', {}).get('count'),
                    placement=sorted((row['name'], tuple(row['cell']))
                                     for row in report['setup']['ownUnits']),
                    trace=report['trace'])

    fixture = json.loads(RECORDED.read_text(encoding='utf-8'))
    fixture.update(mathSeed=7, libSeed=8, tickLimit=1500)
    base = fight(run(fixture))
    cross_pass = json.loads(json.dumps(fixture))
    cross_pass['ownUnits'][0]['skills'] = list(reversed(cross_pass['ownUnits'][0]['skills']))
    cross_pass['ownUnits'][0]['invocationLevels'] = list(reversed(cross_pass['ownUnits'][0]['invocationLevels']))
    cross = fight(run(cross_pass))
    relabelled = json.loads(json.dumps(fixture))
    relabelled['ownUnits'] = list(reversed(relabelled['ownUnits']))
    other = fight(run(relabelled))
    assert cross['trace'] == base['trace'], (
        'swapping skills from different native passes must not change the run')
    assert base['placement'] == other['placement'], (
        'the defense-sorted placement must not move when the input order is reversed')
    assert (base['verdict'], base['ticks'], base['metrics'], base['mathDraws'], base['dispatched']) == (
        other['verdict'], other['ticks'], other['metrics'], other['mathDraws'], other['dispatched']), (
        'a placement-preserving ownUnits swap may only relabel entity ids')
    twins = json.loads(RECORDED_TWINS.read_text(encoding='utf-8'))
    twins.update(mathSeed=7, libSeed=8, tickLimit=1500)
    twin_base = fight(run(twins))
    twin_swap = json.loads(json.dumps(twins))
    twin_swap['ownUnits'] = twin_swap['ownUnits'][:2] + list(reversed(twin_swap['ownUnits'][2:]))
    twin_other = fight(run(twin_swap))
    assert (twin_base['verdict'], twin_base['ticks'], twin_base['metrics'], twin_base['mathDraws']) == (
        twin_other['verdict'], twin_other['ticks'], twin_other['metrics'], twin_other['mathDraws']), (
        'two content-identical units swap to the same measurement')
    return dict(crossPassSkillSwapTraceIdentical=True, placementPreservingOrderSwapSameMetrics=True,
                twinSwapSameMetrics=True, ticks=base['ticks'], tickLimit=1500)


def main():
    request = base_request()
    exporter = lambda scenario, include_events=True: {'schema': 'ka-battle-replay-1',
                                                      'holyHerbStock': scenario['holyHerbStock']}
    out = search(request, runner=fake_runner, replay_exporter=exporter)
    enumeration = out['enumeration']
    assert enumeration['candidates'] == 2 and enumeration['variants'] == 2 and enumeration['combinations'] == 4
    assert enumeration['plannedRuns'] == 4 and out['budget']['plannedRuns'] == 4
    assert enumeration['variantIds'] == ['A', 'B']
    assert enumeration['variantPatchKeys'] == ['holyHerbStock']
    # The full cross product is enumerated and measured, but no combination is a rankable yield:
    # the target is measured for all four and the fail-closed finish reason is disclosed on each.
    assert_unranked_finish_fail_closed(out, ['C1::A', 'C1::B', 'C2::A', 'C2::B'])
    assert out['request']['seeds'] == [[1, 1]], 'the exact seed list must be disclosed'
    assert CLAIM_FRAGMENT in out['claim'] and out['support']['claim'] == out['claim']
    assert out['caveats'][-1] == out['claim']
    assert 'descriptive sample variation' in out['candidates'][0]['pooled']['queuedChestCallbacks']['statistics']['label']
    assert 'dispatched chest entities per attempted fight' in out['rankings']['metric']
    assert out['rankings']['perLevel'][0]['ranked'] == []
    level_key = out['rankings']['perLevel'][0]['level']
    assert (level_key['encounterId'], level_key['defeatCount']) == (2, 0)
    # the derived enemy level is read off a ranked entry, and nothing is ranked while the finish
    # policy fails closed, so this field stays null.
    assert level_key['level'] is None
    assert json.dumps(search(request, runner=fake_runner, replay_exporter=exporter)) == json.dumps(out)

    # no variants -> enumerate the supplied candidates themselves
    plain = search({**request, 'variants': []}, runner=fake_runner, replay_exporter=exporter)
    assert plain['enumeration']['combinations'] == 2 and plain['enumeration']['variantIds'] == []
    assert_unranked_finish_fail_closed(plain, ['C1', 'C2'])

    # negative validation
    def expect(patch, fragment):
        bad = json.loads(json.dumps(request))
        bad.update(patch)
        try:
            search(bad, runner=fake_runner, replay_exporter=exporter)
        except EvaluationError as error:
            assert fragment in str(error), (fragment, str(error))
            return
        raise AssertionError(f'expected EvaluationError containing {fragment!r}')
    expect(dict(schema='ka-battle-eval-1'), 'schema must be ka-battle-search-1')
    expect(dict(limits=dict(maxRuns=3)), 'run budget exceeded')
    expect(dict(limits=dict(maxCandidates=3)), 'search space has 4 combinations')
    expect(dict(variants=[dict(id='X', patch=dict(encounterId=5))]),
           'may not patch the swept comparison keys')
    expect(dict(variants=[dict(id='X', patch=dict(mathSeed=9))]),
           'may not patch the swept comparison keys')
    expect(dict(variants=[dict(id='X', patch=dict(inventedStat=99))]),
           'patches unsupported scenario keys')
    expect(dict(variants=[dict(id='X', patch={})]), 'needs a non-empty patch object')
    expect(dict(variants=[dict(id='A', patch=dict(holyHerbStock=3)),
                          dict(id='A', patch=dict(holyHerbStock=4))]), 'duplicate variant id')

    strategy = strategy_checks()
    generation = generation_checks()
    real = real_runner_evidence()
    report = dict(
        schema='ka-combat-search-checks-1',
        combinations=enumeration['combinations'],
        plannedRuns=enumeration['plannedRuns'],
        ranking=out['ranking'],
        winner=out['selected'],
        unranked=[entry['id'] for entry in out['unranked']],
        unrankedReason=FINISH_UNRANKED_FRAGMENT,
        variantsNoVariantRun=plain['ranking'],
        strategyCombinations=strategy['enumeration']['combinations'],
        strategyWinner=strategy['selected'],
        strategyUnranked=[entry['id'] for entry in strategy['unranked']],
        strategyGenerated=strategy['enumeration']['generated']['perCandidate']['C1'],
        generatedForRecordedFixture=dict(available=generation['available'], truncated=generation['truncated'],
                                         formation=generation['formation'], skillPriorities=generation['skillPriorities']),
        realRunnerEvidence=real,
        probed=['the candidate x variant cross product is fully enumerated and measured',
                'every measured combination is unranked with the exact finish-policy reason',
                'no winner is selected and no replay is returned while the finish policy fails closed',
                'variant patches cannot touch the swept level/seed/tickLimit keys',
                'unknown patch keys and empty patches are rejected',
                'the search budget and candidate/variant caps are enforced before any run',
                'the bounded-enumeration claim is present in both claim and caveats',
                'a strategy request measures the unchanged candidate plus the generated variants only',
                'generated variants are capped per candidate before any run and the truncation is disclosed',
                'every generated combination maps back to its own base candidate and variant',
                'form has no generated variant for the recorded fixture: the native placement tie is',
                '  broken by effective defense and the two skills sit in different native passes',
                'content-identical twin units are never emitted as search diversity',
                'a real run keeps the same placement, metrics and draws when the input order cannot',
                '  change the native formation sort, and an identical trace when skills from different',
                '  passes are swapped; only a same-pass order and a same-key-tie order are real inputs'],
        limits=['The search is bounded enumeration over the supplied candidates and variant patches; it is not an',
                'exhaustive search over gear/team space and it never synthesises game values.',
                'Generated variants only reorder the candidate\'s ownUnits and per-unit skills; no stat,',
                'equipment, skill, level or party member is ever added.'])
    (EVIDENCE / 'search-checks.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: report[key] for key in ('combinations', 'plannedRuns', 'ranking', 'winner',
                                                   'strategyCombinations', 'strategyWinner')}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
