"""Deterministic checks for `strategy_search` (ka-strategy-search-1).

Fast structural/legality checks always run. The real-simulation checks (default frozen UREF search,
a tie-diverse fixture search with 3 real candidates on 2 seeds, a deterministic repeat and a
cancellation) run by default and write their artifacts to
`RE-evidence/20260920-local-strategy-ui/search/`. Run the real part under the existing offline
resource guard (never the external runtime):

  python RE-evidence/20260920-runtime-oracle-wairo-sentinel/resource_guard.py \
      --limit-mb 1024 --seconds 600 -- python KA-Website/tools/recovery/strategy_search_check.py

`--light` skips the real simulations; `--artifacts DIR` moves the artifact directory.
"""
import argparse
import importlib.util
import json
import sys
import time
from copy import deepcopy
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORKSPACE = HERE.parents[2]
sys.path.insert(0, str(HERE))

import strategy_search as search
from combat_scenario import ScenarioError, load_scenario

FROZEN = WORKSPACE / 'RE-evidence/20260920-user-wairo-strategy/scenarios/UREF.scenario.json'
DEFAULT_ARTIFACTS = WORKSPACE / 'RE-evidence/20260920-local-strategy-ui/search'
FIXTURE_CANDIDATE_COUNT = 3
FIXTURE_SEEDS = [{'mathSeed': 7, 'libSeed': 8}, {'mathSeed': 9, 'libSeed': 10}]
FIXTURE_TICK_LIMIT = 7000
# Determinism does not need a resolved battle, so the repeat check uses a short real horizon.
DETERMINISM_TICK_LIMIT = 300
FIXTURE_THIRD_FODDER_HP = 149
PARTIAL_TICK_LIMIT = 100
# The frozen UREF human skill axis yields this many distinct order-relevant activation orderings;
# the hardening below must not change it, and an oversized array must fail before enumeration.
FROZEN_SKILL_ORDER_ARRANGEMENTS = 119
OVERSIZED_HUMAN_SKILL_SLOTS = 9  # 9! = 362880 > search.MAX_UNIT_SKILL_PERMUTATIONS (8! = 40320)
TRANSPORT_MODULE = (WORKSPACE / 'KA-Website/artifacts/kingdom-adventures/api/'
                    '_strategy_search_jobs.py')

RESULTS = []


def record(name, ok, detail=None):
    RESULTS.append(dict(check=name, ok=bool(ok), detail=detail))
    print(json.dumps(dict(check=name, ok=bool(ok), detail=detail)))
    assert ok, f'{name}: {detail}'


def rejects(payload, fragment, name):
    try:
        search.run_search(payload)
    except search.StrategySearchError as error:
        record(name, fragment in str(error), str(error))
        return
    raise AssertionError(f'{name}: request was accepted but should have been rejected')


def fixture_base():
    """The frozen UREF scenario with one fodder made content-distinct but equally defensive.

    This keeps a native `(priority, effectiveDefense)` tie group whose members are no longer
    interchangeable, which is the only situation in which an ownUnits permutation can change the
    native placement. Nothing else is touched.
    """
    base = json.loads(FROZEN.read_text(encoding='utf-8'))
    for unit in base['ownUnits']:
        if unit['name'] == 'Scholar Fodder 3':
            unit['parameters']['10']['rawValue'] = FIXTURE_THIRD_FODDER_HP
    return base


def metrics_fingerprint(row):
    return {key: value for key, value in row.items() if key != 'elapsedSeconds'}


def check_contract():
    request = search.default_request()
    record('default_request_schema', request['schema'] == search.REQUEST_SCHEMA, request['schema'])
    record('default_request_objective', request['objective'] == 'generated-boxes',
           request['objective'])
    record('default_request_search_axis',
           request['searchAxis'] == search.DEFAULT_SEARCH_AXIS
           and search.DEFAULT_SEARCH_AXIS in search.SEARCH_AXIS_CHOICES, request['searchAxis'])
    expected = {'schema', 'candidateCount', 'searchSeed', 'seeds', 'tickLimit', 'objective',
                'searchAxis', 'baseScenario'}
    record('default_request_keys', expected <= set(request), sorted(request))
    record('default_request_json', isinstance(json.dumps(request), str), 'serializable')
    record('default_request_limits', (request['candidateCount'] <= search.MAX_CANDIDATE_COUNT
                                      and request['tickLimit'] <= search.MAX_TICK_LIMIT
                                      and len(request['seeds']) <= search.MAX_SEEDS),
           dict(candidateCount=request['candidateCount'], tickLimit=request['tickLimit'],
                seeds=request['seeds']))
    record('default_request_planned_runs',
           (1 + request['candidateCount']) * len(request['seeds']) <= search.MAX_PLANNED_RUNS,
           (1 + request['candidateCount']) * len(request['seeds']))
    record('exposed_constants', all(hasattr(search, name) for name in (
        'REQUEST_SCHEMA', 'RESULT_SCHEMA', 'OBJECTIVES', 'MAX_CANDIDATE_COUNT', 'MAX_SEEDS',
        'MAX_TICK_LIMIT', 'MAX_PLANNED_RUNS', 'CORE_MODEL_SHA256', 'PRE_SETTLEMENT_BOX_METRIC',
        'SEARCH_AXIS_CHOICES', 'DEFAULT_SEARCH_AXIS', 'AXIS_DESCRIPTIONS',
        'MAX_UNIT_SKILL_PERMUTATIONS')),
        'limits/constants exported')
    normalized, base = search._normalize_request(request)
    frozen = json.loads(FROZEN.read_text(encoding='utf-8'))
    record('frozen_base_identity', normalized['baseScenarioSource'].endswith('UREF.scenario.json')
           and normalized['baseScenarioSha256'] == search._sha256_text(search._canonical_json(frozen))
           and normalized['baseScenarioFileSha256'] == search._sha256_file(FROZEN)
           and request['baseScenarioSha256'] == search._sha256_file(FROZEN),
           normalized['baseScenarioSha256'])
    record('core_model_hash', search.simulator_hashes()['coreModelMatches']
           and search.simulator_hashes()['coreModelSha256'] == search.CORE_MODEL_SHA256,
           search.CORE_MODEL_SHA256[:12])
    return request, normalized, base


def check_rejections(request):
    def bad(**changes):
        payload = dict(request)
        payload.update(changes)
        return payload

    rejects(bad(schema='nope'), 'schema must be', 'reject_schema')
    rejects(bad(objective='free-form'), 'objective must be one of', 'reject_objective')
    rejects(bad(searchAxis='free-form'), 'searchAxis must be one of', 'reject_search_axis')
    rejects(bad(candidateCount=0), 'candidateCount must be between', 'reject_candidate_zero')
    rejects(bad(candidateCount=65), 'candidateCount must be between', 'reject_candidate_high')
    rejects(bad(candidateCount=4.0), 'candidateCount must be an integer', 'reject_candidate_float')
    rejects(bad(candidateCount=True), 'candidateCount must be an integer', 'reject_candidate_bool')
    rejects(bad(tickLimit=0), 'tickLimit must be between', 'reject_tick_zero')
    rejects(bad(tickLimit=20001), 'tickLimit must be between', 'reject_tick_high')
    rejects(bad(tickLimit='7000'), 'tickLimit must be an integer', 'reject_tick_string')
    rejects(bad(searchSeed=-1), 'searchSeed must be between', 'reject_search_seed')
    rejects(bad(seeds=[]), 'seeds must be a non-empty list', 'reject_seeds_empty')
    rejects(bad(seeds=[{'mathSeed': 1, 'libSeed': 2}] * 9), 'seeds exceeds', 'reject_seeds_cap')
    rejects(bad(seeds=[{'mathSeed': 'x', 'libSeed': 2}]), 'seeds[].mathSeed must be an integer',
            'reject_seed_type')
    rejects(bad(candidateCount=64, seeds=[{'mathSeed': 7, 'libSeed': 8}]), 'planned runs',
            'reject_planned_runs_cap')
    rejects(bad(baseScenario='UREF'), 'baseScenario must be a JSON object or null',
            'reject_base_not_object')
    rejects(bad(baseScenario={'schema': 'ka-special-combat-research-1'}), 'baseScenario is not a '
            'legal scenario', 'reject_base_illegal')
    rejects(bad(baseScenario={'schema': 'ka-special-combat-research-1', 'note': 'x' * 300000}),
            'baseScenario exceeds', 'reject_base_too_large')
    record('rejections', True, f'{len(RESULTS)} rejection checks')

    try:
        search._run_metrics(dict(result={}, setup={'ownUnits': []}, metrics={}, trace=[]),
                            'generated-boxes', 0.0)
    except search.StrategySearchError as error:
        record('reject_box_metric_unavailable', 'generated-boxes is unavailable' in str(error),
               str(error))
    else:
        raise AssertionError('a report without the box metric must reject generated-boxes')


def check_axis_and_legality(base, fixture):
    base_axes = search.build_axes(base, 'both')
    formation = base_axes['formation']
    skills = base_axes['skill-order']
    record('frozen_formation_axis_degenerate',
           formation['space'] == 1 and formation['arrangements'] == []
           and formation['reason'] is not None, formation['reason'])
    record('frozen_skill_axis_not_degenerate',
           skills['space'] > 1 and len(skills['arrangements']) == skills['space']
           and skills['reason'] is None, dict(space=skills['space'], units=skills['units']))
    frozen_candidates, frozen_rejected = search._select_candidates(base, base_axes, 4, 1)
    record('frozen_skill_axis_candidates_generated',
           len(frozen_candidates) == 4 and not frozen_rejected
           and all(entry['axis'] == 'skill-order' for entry in frozen_candidates),
           [dict(id=entry['candidateId'], axis=entry['axis']) for entry in frozen_candidates])

    axes = search.build_axes(fixture, 'both')
    record('fixture_formation_axis_not_degenerate',
           axes['formation']['space'] > 1 and axes['formation']['arrangements']
           and axes['formation']['reason'] is None,
           dict(space=axes['formation']['space'],
                arrangements=len(axes['formation']['arrangements'])))
    baseline_placement = search._placement_signature(fixture)
    baseline_roster = search._roster_signature(fixture['ownUnits'])
    for axis_name in ('formation', 'skill-order', 'both'):
        seen = set()
        candidates, rejected = search._select_candidates(
            fixture, search.build_axes(fixture, axis_name), FIXTURE_CANDIDATE_COUNT, 1)
        record(f'fixture_{axis_name}_candidates_generated',
               len(candidates) == FIXTURE_CANDIDATE_COUNT and not rejected,
               dict(generated=len(candidates), rejected=rejected))
        for candidate in candidates:
            scenario = candidate['scenario']
            load_scenario(scenario)
            tag = f'{axis_name}_{candidate["candidateId"]}'
            record(f'{tag}_roster_and_skill_multiset_preserved',
                   search._roster_signature(scenario['ownUnits']) == baseline_roster,
                   candidate['axis'])
            record(f'{tag}_scenario_elsewhere_identical',
                   search._canonical_json({k: v for k, v in scenario.items() if k != 'ownUnits'})
                   == search._canonical_json({k: v for k, v in fixture.items() if k != 'ownUnits'}),
                   'only ownUnits differs')
            record(f'{tag}_resources_preserved',
                   (scenario['holyHerbStock'] == fixture['holyHerbStock']
                    and scenario['inputs'] == fixture['inputs']
                    and scenario['encounterId'] == fixture['encounterId']
                    and scenario['startProfile'] == fixture['startProfile']
                    and scenario['finishPolicy'] == fixture['finishPolicy']),
                   'resources/encounter unchanged')
            placement = search._placement_signature(scenario)
            record(f'{tag}_placement_rule',
                   (placement == baseline_placement) if candidate['axis'] == 'skill-order'
                   else (placement != baseline_placement),
                   'skill order keeps the native placement; formation changes it')
            record(f'{tag}_placement_matches_stored',
                   [[name, grid, list(cell)] for name, grid, cell in placement]
                   == candidate['placement'], 'recomputed via prepare_setup')
            signature = search._canonical_json(scenario['ownUnits'])
            record(f'{tag}_deduplicated', signature not in seen, 'unique ownUnits identity')
            seen.add(signature)

    values = lambda entries: [entry['scenarioSha256'] for entry in entries]
    repeat, _ = search._select_candidates(fixture, search.build_axes(fixture, 'both'),
                                          FIXTURE_CANDIDATE_COUNT, 1)
    record('candidate_selection_deterministic',
           values(repeat) == values(search._select_candidates(
               fixture, search.build_axes(fixture, 'both'), FIXTURE_CANDIDATE_COUNT, 1)[0]),
           'same searchSeed -> same candidates')
    return frozen_candidates


def check_skill_order_hardening(base):
    """Skill-order hardening regressions; no simulator involved.

    An oversized skill array must fail fast before `combat_search.skill_arrangements` runs, and a
    non-human own unit must never reach that enumerating helper (native monster additional-slot
    legality and the fixed first slot are not recovered). The frozen UREF human axis is pinned to its
    current distinct-arrangement count so the hardening cannot change it.
    """
    frozen = search.skill_axis(base)
    record('frozen_skill_axis_human_only_unchanged',
           frozen['space'] == FROZEN_SKILL_ORDER_ARRANGEMENTS
           and len(frozen['arrangements']) == FROZEN_SKILL_ORDER_ARRANGEMENTS
           and frozen['skippedNonhumanUnits'] == []
           and frozen['nonhumanSkipReason'] is None,
           dict(space=frozen['space'], arrangements=len(frozen['arrangements']),
                units=frozen['units']))

    def oversized_unit(name, human):
        return dict(name=name, human=human, monsterId=None if human else 101,
                    skills=list(range(OVERSIZED_HUMAN_SKILL_SLOTS)),
                    invocationLevels=[1] * OVERSIZED_HUMAN_SKILL_SLOTS)

    calls = []
    original = search.combat_search.skill_arrangements

    def spy(unit, rows):
        calls.append(unit['name'])
        return []

    search.combat_search.skill_arrangements = spy
    try:
        try:
            search.skill_axis(dict(ownUnits=[oversized_unit('Oversized Human', True)]))
        except search.StrategySearchError as error:
            message = str(error)
        else:
            message = None
        record('skill_axis_oversized_fails_before_enumeration',
               message is not None and str(search.MAX_UNIT_SKILL_PERMUTATIONS) in message
               and 'per-unit' in message and calls == [],
               dict(error=message, helperCalls=list(calls),
                    cap=search.MAX_UNIT_SKILL_PERMUTATIONS))

        calls.clear()
        monster = search.skill_axis(dict(ownUnits=[oversized_unit('Goblin', False)]))
        record('skill_axis_skips_nonhuman_only',
               calls == [] and monster['arrangements'] == []
               and monster['skippedNonhumanUnits'] == ['Goblin']
               and (monster['nonhumanSkipReason'] or '').startswith('monster own units are skipped'),
               dict(helperCalls=list(calls), skipped=monster['skippedNonhumanUnits']))

        calls.clear()
        mixed = search.skill_axis(
            dict(ownUnits=list(base['ownUnits']) + [oversized_unit('Goblin', False)]))
        expected_humans = [unit['name'] for unit in base['ownUnits']
                           if bool(unit.get('human'))
                           and not search.combat_search.is_owner_bound(unit)]
        record('skill_axis_never_permutes_monster_with_human_present',
               calls == expected_humans and 'Goblin' not in calls
               and mixed['skippedNonhumanUnits'] == ['Goblin'],
               dict(helperCalls=list(calls), skipped=mixed['skippedNonhumanUnits']))
    finally:
        search.combat_search.skill_arrangements = original

    limits = search._limitations(
        dict(objective='battle-outcome', candidateCount=1,
             seeds=[{'mathSeed': 7, 'libSeed': 8}]),
        {'skill-order': dict(reason=None, skippedNonhumanUnits=['Goblin'])}, [], None,
        dict(coreModelMatches=True))
    record('skill_order_nonhuman_limitation_disclosed',
           any('skipped 1 non-human own unit(s) (Goblin)' in entry for entry in limits), limits)


def _synthetic_row(math_seed, lib_seed, ticks, *, censored=False, verdict=2, prize=0, error=None):
    """One synthetic run row for the partial-seed scoring regression (no simulator involved)."""
    resolved = not censored and error is None
    return dict(mathSeed=math_seed, libSeed=lib_seed, tickLimit=PARTIAL_TICK_LIMIT, error=error,
                censored=None if error else censored, verdict=verdict if resolved else None,
                ticks=ticks, ownSurvivors=0, prizeCallbacks=prize,
                **{search.PRE_SETTLEMENT_BOX_METRIC: prize if resolved else None},
                queuedChestAwards=0, yieldTrusted=False,
                verdictTick=ticks if resolved else None)


def check_partial_seed_scoring(base):
    """Regression for the partial-seed/censored scoring bug.

    A candidate that censors (or errors) on one requested seed used to be scored only on the
    remaining seeds, so its shorter tick total could beat a baseline that finished every seed. It
    must now be non-comparable: reported with its honest partial score, but never `best` or an
    improvement, and the positive control must still let a fully completed faster candidate win.
    """
    full = [(1, 2), (3, 4)]
    baseline = [_synthetic_row(1, 2, 90), _synthetic_row(3, 4, 80)]
    baseline_summary = search._summary('baseline', base, base, baseline, baseline,
                                       'generated-boxes', PARTIAL_TICK_LIMIT, 'baseline', full,
                                       'baseline')
    record('partial_baseline_comparable',
           baseline_summary['comparable'] is True and baseline_summary['score'] == [0, 0, -170],
           baseline_summary['score'])

    censored = [_synthetic_row(1, 2, 5), _synthetic_row(3, 4, PARTIAL_TICK_LIMIT, censored=True)]
    censored_summary = search._summary('cand01', base, base, censored, baseline,
                                       'generated-boxes', PARTIAL_TICK_LIMIT, 'generated', full,
                                       'skill-order')
    decision = search._decide([baseline_summary, censored_summary], baseline_summary, False)
    record('partial_censored_not_comparable',
           censored_summary['comparable'] is False
           and censored_summary['metrics']['pairedCompletedSeedPairs'] == 1
           and censored_summary['metrics']['runsCensored'] == 1,
           dict(comparable=censored_summary['comparable'],
                paired=censored_summary['metrics']['pairedCompletedSeedPairs']))
    record('partial_censored_tickbreak_cannot_win',
           censored_summary['score'] == [0, 0, -5]
           and censored_summary['pairedBaselineScore'] == [0, 0, -90]
           and censored_summary['beatsBaseline'] is False
           and decision['improved'] is False
           and decision['best']['candidateId'] == 'baseline',
           dict(candidateScore=censored_summary['score'],
                pairedBaselineScore=censored_summary['pairedBaselineScore'],
                beats=censored_summary['beatsBaseline'], best=decision['best']['candidateId']))

    errored = [_synthetic_row(1, 2, 5), _synthetic_row(3, 4, 0, error='RuntimeError: boom')]
    error_summary = search._summary('cand01', base, base, errored, baseline, 'generated-boxes',
                                    PARTIAL_TICK_LIMIT, 'generated', full, 'skill-order')
    error_decision = search._decide([baseline_summary, error_summary], baseline_summary, False)
    record('partial_errored_seed_not_improvement',
           error_summary['comparable'] is False and error_summary['beatsBaseline'] is False
           and error_decision['improved'] is False,
           dict(comparable=error_summary['comparable'], errored=error_summary['metrics']['runsErrored']))

    complete = [_synthetic_row(1, 2, 5), _synthetic_row(3, 4, 5)]
    complete_summary = search._summary('cand01', base, base, complete, baseline, 'generated-boxes',
                                       PARTIAL_TICK_LIMIT, 'generated', full, 'skill-order')
    complete_decision = search._decide([baseline_summary, complete_summary], baseline_summary, False)
    record('partial_complete_positive_control',
           complete_summary['comparable'] is True and complete_summary['beatsBaseline'] is True
           and complete_decision['improved'] is True
           and complete_decision['best']['candidateId'] == 'cand01',
           dict(score=complete_summary['score'], best=complete_decision['best']['candidateId']))

    short_baseline = [_synthetic_row(1, 2, 90)]
    short_summary = search._summary('baseline', base, base, short_baseline, short_baseline,
                                    'generated-boxes', PARTIAL_TICK_LIMIT, 'baseline', full,
                                    'baseline')
    short_candidate = search._summary('cand01', base, base, complete, short_baseline,
                                      'generated-boxes', PARTIAL_TICK_LIMIT, 'generated', full,
                                      'skill-order')
    short_decision = search._decide([short_summary, short_candidate], short_summary, False)
    record('partial_incomplete_baseline_blocks_improvement',
           short_summary['comparable'] is False and short_candidate['comparable'] is False
           and short_decision['improved'] is False
           and short_decision['best']['candidateId'] == 'baseline',
           dict(baselineComparable=short_summary['comparable']))


def _load_transport(path):
    spec = importlib.util.spec_from_file_location('ka_strategy_search_transport_check', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check_transport_cap():
    """The transport must reject the engine's total planned-run cap up front with 422."""
    if not TRANSPORT_MODULE.is_file():
        record('transport_module_present', False, str(TRANSPORT_MODULE))
        return
    module = _load_transport(TRANSPORT_MODULE)
    boundary = dict(schema='ka-strategy-search-1', candidateCount=7,
                    seeds=[{'mathSeed': 7, 'libSeed': 8}] * 8)
    _, problems = module.validate_request(dict(boundary))
    record('transport_accepts_cap_boundary', problems == [],
           dict(planned=(1 + 7) * 8, problems=problems))
    _, problems = module.validate_request(dict(boundary, candidateCount=64))
    record('transport_rejects_oversized_product',
           any('planned runs' in entry for entry in problems), problems)
    _, problems = module.validate_request(dict(schema='ka-strategy-search-1', candidateCount=64))
    record('transport_rejects_oversized_default_seeds',
           any('planned runs' in entry for entry in problems), problems)
    _, problems = module.validate_request(dict(boundary, searchAxis='free-form'))
    record('transport_rejects_unknown_axis',
           any('searchAxis' in entry for entry in problems), problems)

def check_real_runs(args, request, artifacts):
    if args.light:
        record('real_runs_skipped', True, '--light')
        return
    artifacts.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    full = search.run_search(request)
    (artifacts / 'search-result-uref.json').write_text(json.dumps(full, indent=2) + '\n',
                                                       encoding='utf-8')
    row = full['baseline']['evaluations'][0]
    record('uref_status_completed', full['status'] == 'completed', full['status'])
    record('uref_baseline_completed', full['baseline']['completed'] is True,
           full['baseline']['score'])
    record('uref_baseline_real_metrics',
           row['error'] is None and row['censored'] is False and row['verdict'] == 2
           and row['ownSurvivors'] == 0 and 0 < row['ticks'] <= full['request']['tickLimit']
           and row['verdictTick'] is not None
           and row['prizeCallbacks'] > 0 and row[search.PRE_SETTLEMENT_BOX_METRIC] > 0,
           {key: row[key] for key in ('verdict', 'verdictTick', 'ticks', 'ownSurvivors',
                                      'prizeCallbacks', search.PRE_SETTLEMENT_BOX_METRIC)})
    record('uref_yield_fails_closed',
           row['yieldTrusted'] is False and row['autoFinishProducerProven'] is False
           and row['dispatchedChestAwards'] is not None,
           dict(yieldTrusted=row['yieldTrusted'], dispatched=row['dispatchedChestAwards']))
    record('uref_axes_honest',
           full['searchSpace']['searchAxis'] == 'both'
           and full['searchSpace']['candidateCountGenerated'] == 4
           and any(ax['axis'] == 'formation' and ax['degenerateReason']
                   for ax in full['searchSpace']['axes'])
           and any(ax['axis'] == 'skill-order' and not ax['degenerateReason']
                   and ax['arrangements'] > 1 for ax in full['searchSpace']['axes'])
           and all(entry['axis'] == 'skill-order' for entry in full['candidates']),
           [dict(axis=ax['axis'], arrangements=ax['arrangements'],
                 degenerate=bool(ax['degenerateReason'])) for ax in full['searchSpace']['axes']])
    record('uref_comparability_rule',
           all(entry['beatsBaseline'] == (entry['comparable'] and entry['score'] is not None
                                          and entry['pairedBaselineScore'] is not None
                                          and entry['score'] > entry['pairedBaselineScore'])
               and (entry['comparable']
                    or full['best']['candidateId'] != entry['candidateId'])
               for entry in [full['baseline']] + full['candidates'])
           and (not full['improvement'] or full['best']['kind'] == 'generated'),
           dict(improvement=full['improvement'], best=full['best']['candidateId'],
                candidates=[dict(id=entry['candidateId'], comparable=entry['comparable'],
                                 beatsBaseline=entry['beatsBaseline'])
                            for entry in full['candidates']]))
    record('uref_labels_present',
           all(full['metricLabels'].get(key) for key in
               ('verdict', 'censored', 'ownSurvivors', search.PRE_SETTLEMENT_BOX_METRIC,
                'yieldTrusted')),
           'metric labels')

    fixture_request = dict(search.default_request())
    fixture_request['baseScenario'] = fixture_base()
    fixture_request['candidateCount'] = FIXTURE_CANDIDATE_COUNT
    fixture_request['seeds'] = FIXTURE_SEEDS
    fixture_request['tickLimit'] = FIXTURE_TICK_LIMIT
    # Keep this fixture on the formation axis: it exists to exercise a content-distinct tie group,
    # which the skill-order axis would otherwise be sampled instead of.
    fixture_request['searchAxis'] = 'formation'
    events = []
    fixture = search.run_search(fixture_request, on_progress=events.append)
    (artifacts / 'search-result-fixture.json').write_text(json.dumps(fixture, indent=2) + '\n',
                                                          encoding='utf-8')
    (artifacts / 'progress.jsonl').write_text(
        ''.join(json.dumps(event, sort_keys=True) + '\n' for event in events), encoding='utf-8')
    planned = (1 + FIXTURE_CANDIDATE_COUNT) * len(FIXTURE_SEEDS)
    record('fixture_runs_completed',
           fixture['status'] == 'completed' and len(fixture['candidates']) == FIXTURE_CANDIDATE_COUNT
           and fixture['searchSpace']['plannedRuns'] == planned
           and fixture['searchSpace']['completedRuns'] == planned,
           dict(planned=planned, completed=fixture['searchSpace']['completedRuns']))
    record('fixture_every_run_labelled',
           all(entry['error'] is None and entry['tickLimit'] == FIXTURE_TICK_LIMIT
               and ((entry['censored'] is False and entry['verdict'] in (1, 2))
                    or (entry['censored'] is True and entry['verdict'] is None
                        and entry[search.PRE_SETTLEMENT_BOX_METRIC] is None))
               for summary in [fixture['baseline']] + fixture['candidates']
               for entry in summary['evaluations']),
           'no errors, equal horizons, censored runs labelled and unscored')
    record('fixture_improvement_consistent',
           fixture['improvement'] == any(entry['beatsBaseline'] for entry in fixture['candidates'])
           and (not fixture['improvement'] or fixture['best']['kind'] == 'generated')
           and fixture['best']['candidateId'] in ({'baseline'}
                                         | {entry['candidateId'] for entry in fixture['candidates']}),
           dict(improvement=fixture['improvement'], best=fixture['best']['candidateId']))
    record('fixture_scoring_equal_banks',
           fixture['baseline']['completed'] is True
           and all((entry['score'] is None) == (entry['metrics']['pairedCompletedSeedPairs'] == 0)
                   and 0 < entry['metrics']['pairedCompletedSeedPairs'] <= len(FIXTURE_SEEDS)
                   and entry['metrics']['runsErrored'] == 0
                   for entry in [fixture['baseline']] + fixture['candidates']),
           [dict(candidateId=entry['candidateId'], score=entry['score'],
                 paired=entry['metrics']['pairedCompletedSeedPairs'])
            for entry in [fixture['baseline']] + fixture['candidates']])
    record('fixture_comparability_rule',
           all(entry['comparable']
               == (entry['metrics']['pairedCompletedSeedPairs'] == len(FIXTURE_SEEDS))
               and (not entry['beatsBaseline'] or entry['comparable'])
               for entry in [fixture['baseline']] + fixture['candidates']),
           [dict(candidateId=entry['candidateId'], comparable=entry['comparable'],
                 paired=entry['metrics']['pairedCompletedSeedPairs'])
            for entry in [fixture['baseline']] + fixture['candidates']])
    phases = [event['phase'] for event in events]
    counts = [event['completed'] for event in events]
    record('fixture_progress_shape',
           phases.count('run-complete') == planned and phases[-1] == 'search-complete'
           and counts == sorted(counts) and counts[-1] == planned
           and all({'completed', 'total', 'candidateId', 'bestScore'} <= set(event)
                   for event in events),
           dict(runs=phases.count('run-complete')))

    target = fixture['candidates'][0]['scenario']
    first = search._run_row(target, FIXTURE_SEEDS[0], DETERMINISM_TICK_LIMIT, fixture['objective'])
    second = search._run_row(target, FIXTURE_SEEDS[0], DETERMINISM_TICK_LIMIT, fixture['objective'])
    record('deterministic_repeat', metrics_fingerprint(first) == metrics_fingerprint(second),
           {key: first[key] for key in ('verdict', 'ticks', 'prizeCallbacks', 'ownSurvivors')})

    calls = {'count': 0}

    def cancelled():
        calls['count'] += 1
        return calls['count'] > 1

    stop_request = dict(fixture_request)
    stop_request['tickLimit'] = 600
    stopped = search.run_search(stop_request, cancelled=cancelled)
    record('cancellation_honoured',
           stopped['status'] == 'cancelled' and stopped['improvement'] is False
           and stopped['searchSpace']['completedRuns'] < stopped['searchSpace']['plannedRuns']
           and stopped['limitations'][0].startswith('the search was cancelled'),
           dict(status=stopped['status'], completed=stopped['searchSpace']['completedRuns'],
                planned=stopped['searchSpace']['plannedRuns']))
    summary = dict(
        schema='ka-strategy-search-comparison-1',
        generatedBy='strategy_search_check.py',
        elapsedSeconds=round(time.monotonic() - started, 2),
        frozenUref=dict(
            request={k: v for k, v in full['request'].items() if k != 'ignoredRequestKeys'},
            baselineScore=full['baseline']['score'], bestScore=full['best']['score'],
            bestCandidateId=full['best']['candidateId'], improvement=full['improvement'],
            candidateCountGenerated=full['searchSpace']['candidateCountGenerated'],
            degenerateReason=full['searchSpace']['degenerateReason'],
            axes=[dict(axis=ax['axis'], distinctArrangements=ax['distinctArrangements'],
                       arrangements=ax['arrangements'], degenerateReason=ax['degenerateReason'],
                       units=ax['units']) for ax in full['searchSpace']['axes']],
            candidates=[dict(candidateId=entry['candidateId'], axis=entry['axis'],
                             comparable=entry['comparable'], score=entry['score'],
                             pairedBaselineScore=entry['pairedBaselineScore'],
                             beatsBaseline=entry['beatsBaseline'], metrics=entry['metrics'])
                        for entry in full['candidates']],
            baselineMetrics=full['baseline']['metrics'],
            baselineEvaluations=[{k: v for k, v in entry.items() if k != 'elapsedSeconds'}
                                 for entry in full['baseline']['evaluations']]),
        fixture=dict(
            base='frozen UREF with Scholar Fodder 3 parameter 10 rawValue %d (equal defense, '
                 'content-distinct tie group)' % FIXTURE_THIRD_FODDER_HP,
            searchAxis='formation',
            seeds=FIXTURE_SEEDS, tickLimit=FIXTURE_TICK_LIMIT,
            baselineScore=fixture['baseline']['score'], bestScore=fixture['best']['score'],
            bestCandidateId=fixture['best']['candidateId'], improvement=fixture['improvement'],
            improvements=fixture['improvements'],
            summary=[dict(candidateId=entry['candidateId'], kind=entry['kind'], axis=entry['axis'],
                          score=entry['score'], comparable=entry['comparable'],
                          pairedBaselineScore=entry['pairedBaselineScore'],
                          beatsBaseline=entry['beatsBaseline'],
                          scenarioSha256=entry['scenarioSha256'], metrics=entry['metrics'])
                     for entry in [fixture['baseline']] + fixture['candidates']]),
        cancellation=dict(status=stopped['status'],
                          completedRuns=stopped['searchSpace']['completedRuns'],
                          plannedRuns=stopped['searchSpace']['plannedRuns']))
    (artifacts / 'comparison-summary.json').write_text(json.dumps(summary, indent=2) + '\n',
                                                       encoding='utf-8')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--light', action='store_true', help='skip the real simulator runs')
    parser.add_argument('--artifacts', type=Path, default=DEFAULT_ARTIFACTS)
    args = parser.parse_args(argv)
    started = time.monotonic()
    request, normalized, base = check_contract()
    check_rejections(request)
    check_axis_and_legality(base, fixture_base())
    check_skill_order_hardening(base)
    check_partial_seed_scoring(base)
    check_transport_cap()
    args.artifacts.mkdir(parents=True, exist_ok=True)
    (args.artifacts / 'default-request.json').write_text(
        json.dumps(search.default_request(), indent=2) + '\n', encoding='utf-8')
    check_real_runs(args, request, args.artifacts)
    payload = dict(schema='ka-strategy-search-checks-1', light=bool(args.light),
                   artifacts=str(args.artifacts), elapsedSeconds=round(time.monotonic() - started, 2),
                   passed=sum(1 for entry in RESULTS if entry['ok']), total=len(RESULTS),
                   checks=RESULTS)
    (args.artifacts / 'checks.json').write_text(json.dumps(payload, indent=2) + '\n',
                                                encoding='utf-8')
    print(json.dumps(dict(schema=payload['schema'], passed=payload['passed'],
                          total=payload['total'], elapsedSeconds=payload['elapsedSeconds'],
                          artifacts=str(args.artifacts), status='PASS')))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
