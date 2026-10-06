"""Deterministic multi-seed evaluation of supplied legal team candidates.

`ka-battle-eval-1` request -> one compact summary per candidate/level plus a bounded ranking, and
the replay of the selected winner only. Nothing here re-implements combat: every run is the
existing authoritative pipeline (`combat_scenario.load_scenario` ->
`combat_setup.prepare_setup` -> `combat_sandbox.run_scenario`).

Honesty contract, enforced by the code rather than by prose:

  * the enemy level of a run is the runner's own level, produced by the recovered
    `levelField + defeatCount // 5` formula in `combat_encounters.special_enemy_baseline`; the
    evaluation never invents or interpolates a level. Each selected level is supplied as its own
    `(encounterId, defeatCount)` input, so there is no fixed finite list pretending to be all
    levels;
  * every candidate is measured on the *same* disclosed seed pairs at the same levels and tick
    limit, and the exact seed list is echoed back with every summary;
  * mean/variance/standard error and the normal interval are labelled sampling-only: they describe
    exactly the disclosed seed list, not a population;
  * a censored fight (tick horizon reached before `IsAnnihilated`) is reported as censored and is
    excluded from yield statistics. It is never turned into a zero-chest failure;
  * retained/collected inventory is not modelled and is never counted;
  * only candidates measured on the same levels/seeds/tickLimit are ranked together; a candidate
    whose runs are incomplete or rejected is listed but not ranked;
  * the input supplies the candidates. Nothing here invents stats, skills or boosts.
"""
import statistics
from copy import deepcopy

from combat_scenario import ScenarioError
from combat_sandbox import run_scenario

EVAL_SCHEMA = 'ka-battle-eval-1'
SEARCH_SCHEMA = 'ka-battle-search-1'
HARD_MAX_RUNS = 256
#: Resource guard on a declared horizon. Moved with the measured evidence: the real Search Space v3
#: library censored 29.9% of "Vs. Aloha Kairobot (Hard)" attempts at 10,000 ticks and every sampled
#: one of them resolved by 20,000 (longest verdict tick 19,780), so 30,000 is the new ceiling.
HARD_MAX_TICKS = 30000
HARD_MAX_SEEDS = 64
HARD_MAX_CANDIDATES = 32
HARD_MAX_VARIANTS = 32
HARD_MAX_OWN_UNITS = 64
DEFAULT_TICK_LIMIT = 2000
SWEPT_KEYS = ('schema', 'encounterId', 'defeatCount', 'mathSeed', 'libSeed', 'tickLimit')
# Declared, non-swept source conditions that decide whether two candidates are comparable.
# A different value here is a different simulation condition and must never be pooled silently.
SOURCE_CONDITION_KEYS = ('startProfile', 'finishPolicy', 'prePlacement')


def _canonical(value):
    """Order-insensitive canonical form so dict key order never changes a comparability key."""
    if isinstance(value, dict):
        return tuple(sorted((str(key), _canonical(item)) for key, item in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_canonical(item) for item in value)
    return value


def _source_conditions(scenario):
    """Canonical signature of a candidate's declared source conditions (never the swept keys)."""
    return tuple((key, _canonical(scenario.get(key))) for key in SOURCE_CONDITION_KEYS
                 if key in scenario)

STATISTICS_LABEL = ('descriptive sample variation over exactly the disclosed seed list; the seeds '
                    'are user-chosen, not a random sample, so no population confidence interval or '
                    'certified rate is implied')
INTERVAL_SUPPRESSED_ONE = ('a single sample cannot justify a statistical interval or certainty; '
                           'only the observed value is reported')
INTERVAL_SUPPRESSED_NONRANDOM = ('the seeds are user-chosen rather than a random sample, so no '
                                 'population confidence interval is reported; the range is '
                                 'descriptive only')
PROBABILITY_LABEL = ('sampling-only frequency over the disclosed seed list; win+loss+censored equal '
                     'the completed runs')
RANKING_METRIC = ('expected dispatched chest entities per attempted fight (mean over every '
                  'attempted run in the comparability group)')
RANK_METRIC_NOTE = ('dispatched chest entities are the Finish-time entity creation; an observed '
                    'loss dispatches exactly zero as the native result, and chest collection '
                    '(inventory receipt) remains unmodelled')
RETAINED_NOTE = ('retained/collected inventory is not modelled: the runner dispatches chest entities '
                 'and stops, so no collected reward is ever counted here')
CENSOR_NOTE = ('a censored fight (tick horizon before IsAnnihilated) is excluded from yield '
               'statistics; it is never converted into a zero-chest failure')

STRATEGY_CATALOG = [
    dict(
        id='multi-hit-leaving-callback-farm',
        title='Repeated zero-HP boss Leaving callback farming',
        status='mechanism explained; realising strategy inputs incomplete',
        sources=[
            'KA-Website/docs/reverse-engineering/special-combat.md',
            'KA-Website/docs/reverse-engineering/combat-community-observations.md',
        ],
        mechanism=('A retained direct hit can move a knocked-down, non-destroyed boss back into '
                   'Damaging; when it reaches Leaving(8) again, EnterLeaving runs AddGuerrillaPrize '
                   'and queues another prize/chest callback. Positive HP restoration is not '
                   'required.'),
        evidenceRule=('counted from the runner\'s own event records: `state` entries with new==8 and the '
                      '`prize` records they emit; never from an assumed callback count'),
        confirmableNow=['whether a supplied run reaches state 8 more than once',
                        'how many `prize` event records that run actually emits'],
        incompleteInputs=[
            'the exact player team, equipment levels/affinity and invocation settings that realise repeated re-entry for a given encounter',
            'boss HP/damage arithmetic for that team (must come from a supplied loadout, not be synthesised)',
            'whether the real game performs additional world collection after dispatch',
        ],
        notes=['the observation source is player-reported; the native re-entry mechanism is separately supported by the code slices listed in special-combat.md',
               'no candidate fixture is shipped for the farming loadout because its exact inputs are still unknown'],
    ),
]


class EvaluationError(ValueError):
    pass


def strategy_catalog():
    """Documented/user strategies located by targeted repo retrieval, with honest status."""
    return deepcopy(STRATEGY_CATALOG)


def leaving_callback_evidence(trace):
    """Explain repeated Leaving callback farming from the runner's own event records."""
    state_entries = [e for e in trace if e.get('kind') == 'state' and e.get('new') == 8]
    prize_records = [e for e in trace if e.get('kind') == 'prize']
    return dict(source='runner event records',
                leavingEntries=len(state_entries),
                prizeEventRecords=len(prize_records),
                prizeTreasureIds=[e.get('treasureId') for e in prize_records],
                note='counts come from the recorded event stream, not from an assumed recipe')


def _require(condition, message):
    if not condition:
        raise EvaluationError(message)


def _positive_int(value, name, default=None):
    if value is None:
        if default is None:
            raise EvaluationError(f'{name} is required')
        return default
    _require(type(value) is int and value > 0, f'{name} must be a positive integer')
    return value


def normalize_limits(raw):
    """Validate caller limits and clamp them to the hard transport caps."""
    raw = raw or {}
    _require(isinstance(raw, dict), 'limits must be an object')
    for key in raw:
        _require(key in ('maxRuns', 'maxTicks', 'maxSeeds', 'maxCandidates', 'maxVariants'),
                 f'unknown limit {key}')
    limits = dict(
        maxRuns=min(_positive_int(raw.get('maxRuns'), 'limits.maxRuns', HARD_MAX_RUNS), HARD_MAX_RUNS),
        maxTicks=min(_positive_int(raw.get('maxTicks'), 'limits.maxTicks', HARD_MAX_TICKS), HARD_MAX_TICKS),
        maxSeeds=min(_positive_int(raw.get('maxSeeds'), 'limits.maxSeeds', HARD_MAX_SEEDS), HARD_MAX_SEEDS),
        maxCandidates=min(_positive_int(raw.get('maxCandidates'), 'limits.maxCandidates', HARD_MAX_CANDIDATES),
                          HARD_MAX_CANDIDATES),
        maxVariants=min(_positive_int(raw.get('maxVariants'), 'limits.maxVariants', HARD_MAX_VARIANTS),
                        HARD_MAX_VARIANTS),
    )
    return limits


def normalize_seeds(raw, limit):
    _require(isinstance(raw, list) and raw, 'seeds must be a non-empty list of [mathSeed, libSeed] pairs')
    _require(len(raw) <= limit, f'seeds exceeds the {limit} seed cap')
    seeds = []
    for entry in raw:
        if isinstance(entry, dict):
            entry = [entry.get('mathSeed'), entry.get('libSeed')]
        _require(isinstance(entry, list) and len(entry) == 2, 'each seed must be a [mathSeed, libSeed] pair')
        math_seed, lib_seed = entry
        _require(type(math_seed) is int and type(lib_seed) is int, 'seed values must be integers')
        _require(0 <= math_seed < 2 ** 31 and 0 <= lib_seed < 2 ** 31,
                 'seed values must fit the runner\'s unsigned 31-bit injection')
        seeds.append([math_seed, lib_seed])
    _require(len({tuple(s) for s in seeds}) == len(seeds), 'seeds must be distinct')
    return seeds


def normalize_levels(raw):
    _require(isinstance(raw, list) and raw, 'levels must be a non-empty list of encounter selections')
    levels = []
    for entry in raw:
        _require(isinstance(entry, dict), 'each level must be an object')
        encounter_id, defeat_count = entry.get('encounterId'), entry.get('defeatCount')
        _require(type(encounter_id) is int and type(defeat_count) is int,
                 'level encounterId/defeatCount must be integers')
        _require(0 <= encounter_id < 20, 'encounterId must be in 0..19 (the recovered encounter set)')
        _require(defeat_count >= 0, 'defeatCount must be nonnegative')
        key = (encounter_id, defeat_count)
        _require(key not in {(l['encounterId'], l['defeatCount']) for l in levels},
                 'duplicate (encounterId, defeatCount) selection')
        levels.append(dict(encounterId=encounter_id, defeatCount=defeat_count,
                           label=entry.get('label')))
    return levels


def normalize_candidates(raw, limit):
    _require(isinstance(raw, list) and raw, 'candidates must be a non-empty list')
    _require(len(raw) <= limit, f'candidates exceeds the {limit} candidate cap')
    candidates, seen = [], set()
    for entry in raw:
        _require(isinstance(entry, dict), 'each candidate must be an object')
        candidate_id = entry.get('id')
        _require(isinstance(candidate_id, str) and candidate_id, 'each candidate needs a string id')
        _require(candidate_id not in seen, f'duplicate candidate id {candidate_id}')
        seen.add(candidate_id)
        _require(isinstance(entry.get('scenario'), dict), f'candidate {candidate_id} needs a scenario object')
        own_units = entry['scenario'].get('ownUnits')
        _require(not isinstance(own_units, list) or len(own_units) <= HARD_MAX_OWN_UNITS,
                 f'candidate {candidate_id} ownUnits exceeds the {HARD_MAX_OWN_UNITS} unit cap')
        # The loader appends housePets[owner] after the selected members, so the expanded roster -
        # not just ownUnits - is what the unit cap must bound (same schema semantics, no new limit).
        house_pets = entry['scenario'].get('housePets')
        if isinstance(house_pets, dict):
            expanded = (len(own_units) if isinstance(own_units, list) else 0) + sum(
                len(pets) for pets in house_pets.values() if isinstance(pets, list))
            _require(expanded <= HARD_MAX_OWN_UNITS,
                     f'candidate {candidate_id} expanded roster (ownUnits + housePets) exceeds the '
                     f'{HARD_MAX_OWN_UNITS} unit cap')
        candidates.append(dict(id=candidate_id, label=entry.get('label'), scenario=entry['scenario']))
    return candidates


def scenario_for(candidate, level, seed, tick_limit):
    """The candidate's own scenario with the swept level/seed/tickLimit keys injected.

    A candidate may carry those keys; they are replaced, and the replacement is disclosed in the
    result, so a caller can never smuggle a fixed level or seed into a comparison.
    """
    scenario = deepcopy(candidate['scenario'])
    replaced = sorted(key for key in SWEPT_KEYS if key in scenario)
    scenario.update(schema='ka-special-combat-research-1', encounterId=level['encounterId'],
                    defeatCount=level['defeatCount'], mathSeed=seed[0], libSeed=seed[1],
                    tickLimit=tick_limit)
    return scenario, replaced


def _run_once(scenario, runner):
    """One run: the runner's own report, plus the event-record evidence for Leaving callbacks."""
    report = runner(scenario, True)
    trace = report.pop('trace', None) or []
    result, finish = report['result'], report.get('finish')
    verdict = result['verdict']
    censored = bool(result['censored'])
    return dict(
        verdict=verdict, censored=censored, ticks=result['ticks'], stopReason=result['stopReason'],
        level=report['level'], defeatCount=report['defeatCount'],
        finishPolicy=result.get('finishPolicy','at-horizon'), finishBoundary=result.get('finishBoundary'),
        yieldTrusted=bool(result.get('yieldTrusted')), rewardsTruncated=bool(result.get('rewardsTruncated')),
        queuedChests=None if finish is None else finish['queuedChestAwards']['count'],
        dispatchedChests=None if finish is None else finish['dispatchedChestAwards']['count'],
        inventoryCollection=None if finish is None else finish['inventoryCollection']['state'],
        exp=None if finish is None else finish['exp'],
        evidence=leaving_callback_evidence(trace), error=None,
        conditionalSimulation=dict(
            disclosed=bool((report.get('manifest') or {}).get('support', {}).get('conditionalSimulation')),
            supported=bool((report.get('manifest') or {}).get('support', {}).get('conditionalSimulation', {}).get('supported')),
            unmetConditions=list((report.get('manifest') or {}).get('support', {}).get('conditionalSimulation', {}).get('unmetConditions') or []),
            initialization=((report.get('manifest') or {}).get('support') or {}).get('initialization'),
            sourcePolicy=(((report.get('manifest') or {}).get('support') or {}).get('sourceState') or {}).get('source'),
        ),
    )


def _histogram(values):
    counts = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return [dict(count=value, frequency=counts[value]) for value in sorted(counts)]


def _round(value):
    return None if value is None else round(value, 6)


def _conditional_block(records):
    """Actual per-run `manifest.support.conditionalSimulation`, aggregated honestly.

    A run whose runner did not disclose the block counts as undisclosed -> unsupported, so a
    synthetic runner can never bypass the source-policy gate.
    """
    usable = [record for record in records if record.get('error') is None]
    blocks = [record.get('conditionalSimulation') or {} for record in usable]
    disclosed = bool(usable) and all(block.get('disclosed') for block in blocks)
    supported = bool(usable) and all(block.get('supported') for block in blocks)
    unmet = sorted({name for block in blocks for name in block.get('unmetConditions', [])})
    policies = sorted({block.get('sourcePolicy') for block in blocks if block.get('sourcePolicy')})
    return dict(disclosed=disclosed, supported=supported, unmetConditions=unmet,
                sourcePolicies=policies,
                scope='conditional model estimate on injected seeds and explicit source inputs; not a live replay')


def _stats(values):
    """Descriptive statistics for exactly the disclosed seed list.

    There is deliberately no inferential confidence interval: the seeds are user-chosen rather
    than a random sample, and one sample cannot justify an interval or a claim of certainty. What
    is reported is the observed central value plus, for n >= 2, a descriptive range/variance.
    """
    samples = len(values)
    if samples == 0:
        return dict(samples=0, mean=None, variance=None, standardDeviation=None,
                    standardError=None, descriptiveRange=None, interval95=None,
                    interval95SuppressedReason='no resolved runs to describe',
                    label=STATISTICS_LABEL)
    mean = statistics.fmean(values)
    if samples == 1:
        variance = standard_deviation = standard_error = interval = None
        suppressed = INTERVAL_SUPPRESSED_ONE
    else:
        variance = statistics.variance(values)
        standard_deviation = statistics.stdev(values)
        standard_error = standard_deviation / samples ** 0.5
        interval = [min(values), max(values)]
        suppressed = INTERVAL_SUPPRESSED_NONRANDOM
    return dict(samples=samples, mean=_round(mean), variance=_round(variance),
                standardDeviation=_round(standard_deviation), standardError=_round(standard_error),
                descriptiveRange=None if interval is None else [_round(interval[0]), _round(interval[1])],
                interval95=None, interval95SuppressedReason=suppressed,
                label=STATISTICS_LABEL)


def summarize_level(candidate, level, seeds, records):
    runs = [record for record in records if record['error'] is None]
    errors = [record['error'] for record in records if record['error'] is not None]
    resolved = [record for record in runs if not record['censored']]
    wins = sum(record['verdict'] == 1 for record in resolved)
    losses = sum(record['verdict'] == 2 for record in resolved)
    censored = len(runs) - len(resolved)
    queued = [record['queuedChests'] for record in resolved]
    dispatched = [record['dispatchedChests'] for record in resolved]
    total = len(runs)
    return dict(
        candidateId=candidate['id'], level=dict(encounterId=level['encounterId'],
                                                defeatCount=level['defeatCount'],
                                                level=runs[0]['level'] if runs else None,
                                                label=level.get('label')),
        seeds=[list(seed) for seed in seeds], runs=total, resolvedRuns=len(resolved),
        wins=wins, losses=losses, censored=censored,
        winProbability=_round(wins / total) if total else None,
        lossProbability=_round(losses / total) if total else None,
        censoredProbability=_round(censored / total) if total else None,
        probabilities=dict(label=PROBABILITY_LABEL, basis='completed runs at this level and seed list'),
        queuedChestCallbacks=dict(histogram=_histogram(queued), statistics=_stats(queued),
                                  basis='resolved runs only; a censored run contributes nothing'),
        dispatchedChests=dict(histogram=_histogram(dispatched), statistics=_stats(dispatched),
                              basis='resolved runs only; a chest entity is not an inventory receipt'),
        retainedInventory=dict(state='not modelled', counted=False, note=RETAINED_NOTE),
        conditionalSimulation=_conditional_block(records),
        finishPolicy=_finish_policy_block(candidate, records),
        censoring=dict(censored=censored, note=CENSOR_NOTE,
                       stopReason=runs[0]['stopReason'] if runs and censored else None),
        eventEvidence=dict(
            leavingEntries=sum(record['evidence']['leavingEntries'] for record in runs),
            prizeEventRecords=sum(record['evidence']['prizeEventRecords'] for record in runs),
            prizeTreasureIds=[tid for record in runs for tid in record['evidence']['prizeTreasureIds']],
            source='runner event records'),
        errors=errors,
    )


def summarize_candidate(candidate, level_summaries, replaced_keys):
    all_records = [record for summary in level_summaries for record in summary['_records']]
    resolved = [record for record in all_records if record['error'] is None and not record['censored']]
    queued = [record['queuedChests'] for record in resolved]
    dispatched = [record['dispatchedChests'] for record in resolved]
    fetched = sum(summary['runs'] for summary in level_summaries)
    wins = sum(summary['wins'] for summary in level_summaries)
    losses = sum(summary['losses'] for summary in level_summaries)
    censored = sum(summary['censored'] for summary in level_summaries)
    errors = [error for summary in level_summaries for error in summary['errors']]
    return dict(
        id=candidate['id'], label=candidate.get('label'),
        sweptKeysReplaced=replaced_keys,
        pooled=dict(runs=fetched, resolvedRuns=len(resolved), wins=wins, losses=losses, censored=censored,
                    winProbability=_round(wins / fetched) if fetched else None,
                    lossProbability=_round(losses / fetched) if fetched else None,
                    censoredProbability=_round(censored / fetched) if fetched else None,
                    queuedChestCallbacks=dict(histogram=_histogram(queued), statistics=_stats(queued),
                                              basis='resolved runs only; a censored run contributes nothing'),
                    dispatchedChests=dict(histogram=_histogram(dispatched), statistics=_stats(dispatched),
                                          basis='resolved runs only; a chest entity is not an inventory receipt'),
                    retainedInventory=dict(state='not modelled', counted=False, note=RETAINED_NOTE),
                    label=PROBABILITY_LABEL),
        levels=[{key: value for key, value in summary.items() if key != '_records'}
                for summary in level_summaries],
        conditionalSimulation=_conditional_block(all_records),
        finishPolicy=_finish_policy_block(candidate, all_records),
        errors=errors,
        status='rejected' if errors and not fetched else ('partial' if errors else 'measured'),
    )


def _finish_policy_block(candidate, records):
    """Declared finish policy and whether its chest count is a trusted yield.

    `on-verdict` cuts at the verdict tick boundary and truncates native post-verdict Leaving/re-entry
    prizes, so its count is a diagnostic lower bound and the candidate is never ranked by it
    (rewards truncated). A resolved run whose declared Ending confirmation did not finish the fight
    (counter<=79, so no native Finish ran) is reported awaiting-confirmation and is likewise not a
    trusted expected yield. `yieldTrusted` is the observed dispatched count under this legal declared
    input - not a claim that every future prize producer was exhausted.
    """
    policy=candidate['scenario'].get('finishPolicy','at-horizon')
    usable=[record for record in records if record.get('error') is None]
    # Only records that actually declare a finish boundary can be unconfirmed; a runner that does not
    # model the Finish boundary (e.g. a synthetic unit-test runner) is not failed for its absence.
    declared=[record for record in usable if record.get('finishBoundary') is not None]
    trusted=(not declared) or all(record.get('yieldTrusted') for record in declared)
    # FAIL CLOSED: no declared finish policy is a proven native-automatic producer, so a chest count
    # under it can never be a rankable yield. `trustedYield` still reports whether the declared cut
    # actually finished the fight, for diagnostics only.
    return dict(policy=policy, yieldEligible=False, trustedYield=trusted,
                autoFinishProducerProven=False,
                note='every finish policy is DIAGNOSTIC ONLY and yield-ineligible: the native Ending '
                     'exit 0x14ef538 is the one-frame KEY_SELECT input edge and no automatic producer '
                     'of it is proven (check_combat_auto_finish_producer.py). at-horizon/after-ending '
                     'request ONE declared Ending confirmation, on-verdict cuts at the verdict '
                     'boundary; dispatched counts are observed diagnostics, never trusted yield.')


def _finish_blockers(finish):
    """Ranking blockers for a declared finish policy (diagnostic cut or unconfirmed finish)."""
    finish = finish or {}
    if finish.get('yieldEligible') is False:
        reason = ('the run cuts at the verdict tick boundary and truncates native post-verdict prize '
                  'activity (rewards truncated)') if finish.get('policy') == 'on-verdict' else (
                  'no automatic native producer of the STATE_ENDING 3 exit 0x14ef538 is proven, so the '
                  'dispatched count is a diagnostic under a declared cut')
        return [
            'diagnostic finish policy %s: %s, so the dispatched chest count is not a rankable yield '
            '(check_combat_auto_finish_producer.py)' % (finish.get('policy'), reason)]
    if finish and not finish.get('trustedYield'):
        return [
            'awaiting-confirmation finish boundary: the declared Ending confirmation did not finish '
            'the fight (counter<=79 wrote 80 with no native Finish), so no chest entity was dispatched '
            'and the count is not a rankable yield (use after-ending or a longer at-horizon)']
    return []


CAVEATS = [
    'research-mode model estimate: the runner rejects exact/recommendation modes and never certifies a gear recommendation',
    'the ranking objective is the expected dispatched chest entities per attempted fight; win probability is reported but does not order the ranking',
    'only candidates with complete, error-free, uncensored runs on the identical levels/seed list/tick limit receive a definitive rank; every other candidate is unranked with its reasons',
    'a censored run is never dropped to make a candidate look better and never counted as zero yield',
    'the win/loss/censored frequencies and every chest statistic describe only the disclosed seed list',
    'the seeds are user-chosen, not a random sample: no confidence interval for a population is reported, and a single sample is reported without an interval',
    'retained/collected inventory is not modelled, so no summary reports a collected reward',
    'chest dispatch is the Finish entity creation; opening the chest (receipt/inventory) is outside this report',
    'EXP, the result-confirmation callbacks and world collection of ground spawns are not modelled',
    'a candidate whose runs do not disclose a supported conditional simulation (approx placeholder source state, unmet condition, or no disclosure) is unranked with the exact unmet reason; approximate runs are never ranked',
    'linked parameter graphs and unsupported skills are rejected by the runner; job/equipment legality is the caller\'s explicit responsibility',
]


def _run_records(candidate, levels, seeds, tick_limit, runner):
    replaced, level_summaries = set(), []
    for level in levels:
        records = []
        for seed in seeds:
            scenario, keys = scenario_for(candidate, level, seed, tick_limit)
            replaced.update(keys)
            try:
                record = _run_once(scenario, runner)
            except (ScenarioError, KeyError, TypeError, ValueError) as error:
                record = dict(error=f'{type(error).__name__}: {error}', censored=None, verdict=None)
            records.append(record)
        level_summaries.append(summarize_level(candidate, level, seeds, records) | {'_records': records})
    return level_summaries, sorted(replaced)


def _rank_blockers(errors, censored, runs, expected_runs, support=None):
    """Why a candidate cannot take a definitive rank inside one comparability group."""
    blockers = []
    if support is not None and not support.get('supported'):
        if not support.get('disclosed'):
            blockers.append(
                'conditional-simulation support was not disclosed by the runner: an undisclosed '
                'source policy is not rankable')
        else:
            unmet = support.get('unmetConditions') or []
            blockers.append('conditional simulation is not supported for this source policy'
                            + (f" (unmet: {', '.join(unmet)})" if unmet else
                               ' (the run is approximate and the chest yield is not comparable)'))
    if errors:
        blockers.append(f'{len(errors)} run error(s): a candidate with errors is not comparable')
    if censored:
        blockers.append(
            f'{censored} censored run(s): the dispatched-chest count of a censored fight is unknown, '
            'so dropping it would bias the mean and counting it as zero would be false; unranked')
    if runs < expected_runs:
        blockers.append(f'incomplete seed coverage: {runs}/{expected_runs} runs completed')
    return blockers


def _dispatched_mean(summary):
    return summary['pooled']['dispatchedChests']['statistics']['mean']


def _partition(summaries, keys):
    """Group summaries by comparability key, preserving first-seen order.

    `keys` maps candidate id -> comparability key. Without keys every summary is one group, which
    is only correct when the caller has already proven identical source conditions.
    """
    if keys is None:
        return [list(summaries)]
    order, groups = [], {}
    for summary in summaries:
        key = keys.get(summary['id'])
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(summary)
    return [groups[key] for key in order]


def _rank_group(summaries, expected_runs):
    """Definitive pooled ranking inside one comparability group."""
    ranked, unranked = [], []
    for summary in summaries:
        blockers = _rank_blockers(summary['errors'], summary['pooled']['censored'],
                                  summary['pooled']['runs'], expected_runs,
                                  summary.get('conditionalSimulation'))
        blockers.extend(_finish_blockers(summary.get('finishPolicy')))
        if blockers:
            unranked.append(dict(id=summary['id'], status=summary['status'], reasons=blockers,
                                 errors=summary['errors']))
        else:
            ranked.append(summary)
    ranked.sort(key=lambda s: (-(_dispatched_mean(s) or 0.0), s['id']))
    entries = [dict(id=summary['id'], rank=position,
                    expectedDispatchedChestsPerAttempt=_dispatched_mean(summary),
                    winProbability=summary['pooled']['winProbability'])
               for position, summary in enumerate(ranked, 1)]
    return ranked, entries, unranked


def _rank_summaries(summaries, expected_runs, keys=None):
    """Pooled ranking, assigned inside each comparability group.

    Candidates with different declared source conditions (a different startProfile / finishPolicy /
    captured prePlacement) land in different groups and never share a rank scale, so a different
    profile value can never be silently treated as equivalent.
    """
    ranked, entries, unranked = [], [], []
    for group in _partition(summaries, keys):
        group_ranked, group_entries, group_unranked = _rank_group(group, expected_runs)
        ranked.extend(group_ranked)
        entries.extend(group_entries)
        unranked.extend(group_unranked)
    return ranked, entries, unranked


def _per_level_rankings(summaries, levels, seeds, keys=None):
    """One definitive ranking per selected level and comparability group."""
    per_level = []
    for level in levels:
        expected = len(seeds)
        entries, unranked = [], []
        level_value = None
        for group in _partition(summaries, keys):
            ranked = []
            for summary in group:
                level_summary = next(
                    (lv for lv in summary['levels']
                     if lv['level']['encounterId'] == level['encounterId']
                     and lv['level']['defeatCount'] == level['defeatCount']), None)
                if level_summary is None:
                    unranked.append(dict(id=summary['id'], status=summary['status'],
                                         reasons=['this level was not measured for the candidate'],
                                         errors=[]))
                    continue
                blockers = _rank_blockers(level_summary['errors'], level_summary['censored'],
                                          level_summary['runs'], expected,
                                          level_summary.get('conditionalSimulation'))
                blockers.extend(_finish_blockers(level_summary.get('finishPolicy')))
                if blockers:
                    unranked.append(dict(id=summary['id'], status=summary['status'], reasons=blockers,
                                         errors=level_summary['errors']))
                else:
                    ranked.append(level_summary)
            ranked.sort(key=lambda lv: (-(lv['dispatchedChests']['statistics']['mean'] or 0.0),
                                        lv['candidateId']))
            if ranked and level_value is None:
                level_value = ranked[0]['level']['level']
            group_entries = [dict(id=lv['candidateId'], rank=position,
                                  expectedDispatchedChestsPerAttempt=lv['dispatchedChests']['statistics']['mean'],
                                  winProbability=lv['winProbability'])
                             for position, lv in enumerate(ranked, 1)]
            for entry in group_entries:
                next(lv for lv in ranked if lv['candidateId'] == entry['id'])['rank'] = entry['rank']
            entries.extend(group_entries)
        per_level.append(dict(
            level=dict(encounterId=level['encounterId'], defeatCount=level['defeatCount'],
                       level=level_value, label=level.get('label')),
            ranked=entries, unranked=unranked))
    return per_level

def _select_winner(ranked, candidates, levels, seeds, tick_limit, replay_exporter):
    if not ranked:
        return None, None
    winner = ranked[0]
    usable = [lv for lv in winner['levels'] if not lv['errors'] and lv['censored'] == 0]
    best = max(usable, key=lambda lv: (lv['dispatchedChests']['statistics']['mean'] or 0.0,
                                       -lv['level']['defeatCount']))
    level = next(l for l in levels
                 if l['encounterId'] == best['level']['encounterId']
                 and l['defeatCount'] == best['level']['defeatCount'])
    candidate = next(c for c in candidates if c['id'] == winner['id'])
    scenario, _ = scenario_for(candidate, level, seeds[0], tick_limit)
    if replay_exporter is None:  # lazy so the aggregate path needs no export
        from combat_replay_export import export_replay as replay_exporter
    replay = replay_exporter(scenario, include_events=True)
    selected = dict(candidateId=winner['id'], level=best['level'], seed=list(seeds[0]), rank=1,
                    rankMetric=RANKING_METRIC,
                    replaySelection=('pooled winner candidate at its highest mean-dispatched-chest '
                                     'level, measured with the first disclosed seed'))
    return selected, replay


def evaluate(request, runner=run_scenario, replay_exporter=None):
    """Run one `ka-battle-eval-1` request and return the compact result envelope."""
    _require(isinstance(request, dict), 'the evaluation request must be a JSON object')
    _require(request.get('schema') == EVAL_SCHEMA, f'schema must be {EVAL_SCHEMA}')
    limits = normalize_limits(request.get('limits'))
    levels = normalize_levels(request.get('levels'))
    seeds = normalize_seeds(request.get('seeds'), limits['maxSeeds'])
    candidates = normalize_candidates(request.get('candidates'), limits['maxCandidates'])
    tick_limit = _positive_int(request.get('tickLimit'), 'tickLimit', DEFAULT_TICK_LIMIT)
    _require(tick_limit <= limits['maxTicks'], f'tickLimit must be <= limits.maxTicks ({limits["maxTicks"]})')

    planned = len(candidates) * len(levels) * len(seeds)
    _require(planned <= limits['maxRuns'],
             f'run budget exceeded: {len(candidates)} candidates x {len(levels)} levels x '
             f'{len(seeds)} seeds = {planned} runs > limits.maxRuns ({limits["maxRuns"]})')

    summaries = []
    for candidate in candidates:
        level_summaries, replaced = _run_records(candidate, levels, seeds, tick_limit, runner)
        summaries.append(summarize_candidate(candidate, level_summaries, replaced))
    expected_runs = len(levels) * len(seeds)
    # Comparability keys are per candidate: identical swept levels/seeds/tick limit AND identical
    # declared source conditions. A different startProfile/finishPolicy/prePlacement is a different
    # group, so different profile values are never silently treated as equivalent.
    comparability_keys = {
        candidate['id']: (_comparability_key(levels, seeds, tick_limit),
                          _source_conditions(candidate['scenario']))
        for candidate in candidates}
    ranked, pooled_entries, unranked_entries = _rank_summaries(summaries, expected_runs,
                                                               comparability_keys)
    per_level = _per_level_rankings(summaries, levels, seeds, comparability_keys)
    ranks = {entry['id']: entry['rank'] for entry in pooled_entries}
    for summary in summaries:
        summary['rank'] = ranks.get(summary['id'])
        summary['rankMetric'] = RANKING_METRIC
    groups = {}
    for summary in ranked:
        groups.setdefault(comparability_keys[summary['id']], []).append(summary['id'])
    selected, replay = _select_winner(ranked, candidates, levels, seeds, tick_limit, replay_exporter)
    return dict(
        schema=EVAL_SCHEMA,
        status='research-only; not certified for gear recommendations',
        request=dict(levels=levels, seeds=[list(seed) for seed in seeds], tickLimit=tick_limit, limits=limits),
        budget=dict(plannedRuns=planned, candidates=len(candidates), levels=len(levels),
                    seeds=len(seeds), maxRuns=limits['maxRuns'], maxTicks=limits['maxTicks'],
                    maxSeeds=limits['maxSeeds']),
        comparability=dict(
            scope=dict(levels=[[l['encounterId'], l['defeatCount']] for l in levels],
                       seeds=[list(seed) for seed in seeds], tickLimit=tick_limit),
            groups={str(index): members for index, members in enumerate(groups.values())},
            rule='only candidates with identical levels, seed list and tick limit AND a compatible '
                 'supported conditional source policy with fully resolved matching seed coverage are ranked together'),
        candidates=summaries,
        rankings=dict(metric=RANKING_METRIC, metricNote=RANK_METRIC_NOTE, perLevel=per_level,
                      pooled=dict(ranked=pooled_entries, unranked=unranked_entries)),
        ranking=[entry['id'] for entry in pooled_entries],
        unranked=unranked_entries,
        selected=selected,
        replay=replay,
        strategies=strategy_catalog(),
        caveats=list(CAVEATS),
        support=dict(mode='research', exactReplaySupported=False, recommendationsSupported=False,
                     claim='bounded evaluation of supplied candidates; not a global optimum'),
        caps=dict(hardMaxRuns=HARD_MAX_RUNS, hardMaxTicks=HARD_MAX_TICKS, hardMaxSeeds=HARD_MAX_SEEDS,
                  hardMaxCandidates=HARD_MAX_CANDIDATES, hardMaxVariants=HARD_MAX_VARIANTS),
    )


def _comparability_key(levels, seeds, tick_limit, source_conditions=()):
    return (tuple((level['encounterId'], level['defeatCount']) for level in levels),
            tuple(tuple(seed) for seed in seeds), tick_limit, tuple(source_conditions))
