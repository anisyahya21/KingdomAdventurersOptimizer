"""Bounded candidate search: enumeration, never a claim of a global optimum.

`ka-battle-search-1` is the search envelope on top of `ka-battle-eval-1`. It enumerates the
*supplied* candidates against the *supplied* variant patches, measures every combination on the
same disclosed levels and seed pairs, ranks the combinations that completed on the same scope, and
returns the replay of the single selected winner. It adds no game data:

  * a candidate is a legal team scenario supplied by the caller;
  * a variant is an explicit patch over caller-allowed scenario keys
    (`ownUnits`, `inputs`, `holyHerbStock`, `prePlacement`, `housePets`, `mode`). The swept
    comparison keys (`schema`, `encounterId`, `defeatCount`, `mathSeed`, `libSeed`, `tickLimit`)
    cannot be patched, so a variant can never pin a level or a seed;
  * nothing here synthesises a stat boost: every variant value is caller-supplied and is validated
    by the same `load_scenario` the runner uses;
  * the search space is the bounded cross product of those inputs. It is *not* an exhaustive or
    globally optimal search over gear/team space, and every result says so.

`strategy` adds bounded *generation* on top: instead of enumerating only caller-supplied patches,
the search derives legal variants from the candidate's own validated units. Only two axes exist, and
neither can change what the team owns:

  * `formations` - permutations of the SAME `ownUnits` (identical stats, jobs, levels, equipment and
    skills). The native placement (`combat_initial_state.formation`) sorts members by
    `(priority, -effectiveDefense, incomingIndex)` and only then writes `grid`/`cell`, so a
    permutation is a distinct input only when it flips the relative order of two units that share
    the same native priority AND the same effective defense. Permutations that cannot change the
    placement are never emitted, and units that are byte-identical apart from their name are one
    content group, so swapping twins is not sold as search diversity. Owner-bound pets are never
    moved away from their owner.
  * `skillPriorities` - permutations of one unit's `skills` list that keep every `skillId` paired
    with its own `invocationLevel`. Native order only matters inside one candidate pass, so a
    permutation is emitted only when it flips two skills that can reach the same pass (`recovery`
    before `attack`, `defensive` reactions, `after_attack` invoking skills).

Generation is capped before anything runs: the caller-visible variants per candidate and the
planned runs are both checked, and a truncated generation is disclosed with the count it was
truncated from. Nothing here adds a skill, a piece of equipment, a stat level, a party member or a
level that the validated candidate did not already carry.
"""
import json
from copy import deepcopy
from itertools import permutations, product

from combat_sandbox import run_scenario
from combat_evaluation import (
    EVAL_SCHEMA, SEARCH_SCHEMA, EvaluationError, HARD_MAX_RUNS, HARD_MAX_TICKS, HARD_MAX_SEEDS,
    HARD_MAX_CANDIDATES, HARD_MAX_VARIANTS, DEFAULT_TICK_LIMIT, CAVEATS,
    normalize_limits, normalize_levels, normalize_seeds, normalize_candidates, strategy_catalog,
    evaluate, _positive_int, _require,
)

SEARCH_CLAIM = ('bounded enumeration over the supplied candidates and explicitly supplied variant '
                'patches; this is not an exhaustive search and no global optimum is claimed')
GENERATION_CLAIM = ('generated variants are bounded re-orderings of the candidate\'s own validated '
                    'inputs (ownUnits order within one native placement tie, and per-unit skill '
                    'order within one native pass); the same units, stats, jobs, levels, equipment, '
                    'skills, pets and party size are preserved and no variant adds a skill, an '
                    'equipment row, a stat, a level or a party member')
PATCH_KEYS = ('ownUnits', 'inputs', 'holyHerbStock', 'prePlacement', 'housePets', 'mode')
FORBIDDEN_PATCH_KEYS = ('schema', 'encounterId', 'defeatCount', 'mathSeed', 'libSeed', 'tickLimit')
STRATEGY_KEYS = ('formations', 'skillPriorities', 'maxVariantsPerCandidate')
BASE_VARIANT_ID = 'base'
MAX_ARRANGEMENT_FACTORIAL = 5040


def normalize_variants(raw, limit):
    """Variant patches are explicit caller values over caller-allowed scenario keys."""
    if raw is None:
        return []
    _require(isinstance(raw, list), 'variants must be a list')
    _require(len(raw) <= limit, f'variants exceeds the {limit} variant cap')
    variants, seen = [], set()
    for entry in raw:
        _require(isinstance(entry, dict), 'each variant must be an object')
        variant_id = entry.get('id')
        _require(isinstance(variant_id, str) and variant_id, 'each variant needs a string id')
        _require(variant_id not in seen, f'duplicate variant id {variant_id}')
        seen.add(variant_id)
        patch = entry.get('patch')
        _require(isinstance(patch, dict) and patch, f'variant {variant_id} needs a non-empty patch object')
        forbidden = sorted(set(patch) & set(FORBIDDEN_PATCH_KEYS))
        _require(not forbidden,
                 f'variant {variant_id} may not patch the swept comparison keys {forbidden}')
        unknown = sorted(set(patch) - set(PATCH_KEYS))
        _require(not unknown, f'variant {variant_id} patches unsupported scenario keys {unknown}')
        variants.append(dict(id=variant_id, label=entry.get('label'),
                             patch=deepcopy(patch), patchKeys=sorted(patch)))
    return variants


def normalize_strategy(raw):
    """Optional bounded generation of legal variants from the candidates' own validated units."""
    if raw is None:
        return None
    _require(isinstance(raw, dict), 'strategy must be an object')
    unknown = sorted(set(raw) - set(STRATEGY_KEYS))
    _require(not unknown, f'strategy has unsupported keys {unknown}')
    for key in ('formations', 'skillPriorities'):
        if key in raw:
            _require(isinstance(raw[key], bool), f'strategy.{key} must be a boolean')
    cap = raw.get('maxVariantsPerCandidate')
    if cap is not None:
        _require(type(cap) is int and cap > 0,
                 'strategy.maxVariantsPerCandidate must be a positive integer')
    return dict(formations=raw.get('formations', True), skillPriorities=raw.get('skillPriorities', True),
                maxVariantsPerCandidate=cap)


def unit_content_signature(unit):
    """Everything about a unit except its name; equal signatures are interchangeable in a fight."""
    return json.dumps({key: value for key, value in unit.items() if key != 'name'},
                      sort_keys=True, separators=(',', ':'))


def is_owner_bound(unit):
    """Owner-bound pets are appended by their owner's order and are never permuted here."""
    return bool(unit.get('petOwnerName'))


def native_skill_passes(row):
    """The native passes one skill can reach; slot order only matters inside one pass."""
    passes = set()
    if row['flags'] & 8 and not row['flags'] & 32:
        passes.add('recovery' if row['category'] == 1 else 'attack')
    if row['flags'] & 32:
        passes.add('defensive')
    if row['flags'] & 64:
        passes.add('after_attack')
    return frozenset(passes)


def _distinct_permutations(indices, signatures):
    """Content-distinct orderings of one tie group, lexicographic; the input order comes first."""
    ordered, seen = [], set()
    for perm in permutations(indices):
        key = tuple(signatures[index] for index in perm)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(perm)
    return ordered


def formation_arrangements(units, placement_keys):
    """Distinct ownUnits orderings the native formation sort can actually distinguish.

    `placement_keys` maps a unit name to `(priority, effectiveDefense)` from the native pre-sort.
    Only units sharing one `(priority, effectiveDefense)` key can change the placement by their
    order, so only those are permuted; every other unit keeps its input position. Units with the
    same content signature (everything but the name) form one group, because swapping them cannot
    change the fight. Returns the unchanged candidate first, then the distinct re-orderings.
    """
    groups = {}
    for index, unit in enumerate(units):
        if is_owner_bound(unit):
            continue
        key = placement_keys.get(unit['name'])
        if key is None:
            continue
        groups.setdefault(key, []).append(index)
    axes = []
    for key, indices in groups.items():
        if len(indices) < 2:
            continue
        signatures = {index: unit_content_signature(units[index]) for index in indices}
        orderings = _distinct_permutations(indices, signatures)
        if len(orderings) < 2:
            continue
        axes.append((key, indices, orderings))
    classes = [dict(priority=key[0], effectiveDefense=key[1],
                    members=[units[index]['name'] for index in indices],
                    unitsInTie=len(indices), distinctOrderings=len(orderings))
               for key, indices, orderings in axes]
    arrangements = []
    for combo in (product(*[orderings for _, _, orderings in axes]) if axes else [()]):
        assignment = {}
        for (_, indices, _), ordering in zip(axes, combo):
            for position, source in zip(indices, ordering):
                assignment[position] = source
        arrangements.append(dict(assignment=assignment,
                                 identity=all(position == source for position, source in assignment.items()),
                                 classes=classes))
    arrangements.sort(key=lambda entry: not entry['identity'])
    return arrangements


def skill_arrangements(unit, skill_rows):
    """Order-relevant permutations of one unit's skills; each keeps skillId <-> invocationLevel."""
    skills, levels = unit['skills'], unit['invocationLevels']
    if len(skills) < 2:
        return []
    passes = [native_skill_passes(skill_rows[skill]) if skill in skill_rows else frozenset()
              for skill in skills]
    pairs = [(a, b) for a in range(len(skills)) for b in range(a + 1, len(skills))
             if passes[a] & passes[b]]
    if not pairs:
        return []
    baseline = tuple((skills[index], levels[index]) for index in range(len(skills)))
    seen_signatures, seen_orders, out = set(), set(), []
    for perm in permutations(range(len(skills))):
        slot = {index: position for position, index in enumerate(perm)}
        signature = tuple(slot[a] < slot[b] for a, b in pairs)
        if all(signature) or signature in seen_signatures:
            continue
        order = tuple((skills[index], levels[index]) for index in perm)
        if order == baseline or order in seen_orders:
            continue
        seen_signatures.add(signature)
        seen_orders.add(order)
        out.append(dict(order=list(perm), skills=[skills[index] for index in perm],
                        invocationLevels=[levels[index] for index in perm]))
    return out


def _units_with_order(units, assignment):
    ordered = list(units)
    for position, source in assignment.items():
        ordered[position] = units[source]
    return ordered


def generate_variants(scenario, options, placement_keys=None, skill_rows=None):
    """Bounded legal variants derived from one validated candidate scenario.

    The returned `variants` never include the unchanged candidate; the caller prepends it. Every
    entry keeps the candidate's own units and values and only reorders them.
    """
    units = scenario.get('ownUnits') or []
    report = dict(available=0, generated=0, truncated=False, cap=options.get('maxVariantsPerCandidate'),
                  formation=dict(enabled=bool(options.get('formations')), classes=[], arrangements=0,
                                 reason=None),
                  skillPriorities=dict(enabled=bool(options.get('skillPriorities')), units=[],
                                       arrangements=0, reason=None),
                  variants=[])
    formation_axis = [dict(assignment={}, identity=True, classes=[])]
    if report['formation']['enabled']:
        if placement_keys is None:
            report['formation']['reason'] = ('native placement keys are unavailable, so no formation '
                                             'permutation can be shown to change the placement')
        else:
            formation_axis = formation_arrangements(units, placement_keys)
            report['formation']['classes'] = formation_axis[0]['classes'] if formation_axis else []
            report['formation']['arrangements'] = len(formation_axis) - 1
    skill_axis = [dict(identity=True, changes={})]
    if report['skillPriorities']['enabled']:
        if skill_rows is None:
            report['skillPriorities']['reason'] = ('skill rows are unavailable, so no skill order can '
                                                   'be shown to reach a shared native pass')
        else:
            entries = []
            for unit in units:
                if is_owner_bound(unit):
                    continue
                orderings = skill_arrangements(unit, skill_rows)
                if not orderings:
                    continue
                entries.append((unit['name'], orderings))
                report['skillPriorities']['units'].append(
                    dict(name=unit['name'], skills=list(unit['skills']),
                         invocationLevels=list(unit['invocationLevels']),
                         orderRelevantArrangements=len(orderings)))
            if entries:
                for combo in product(*[orderings for _, orderings in entries]):
                    skill_axis.append(dict(identity=False, changes={
                        name: dict(skills=arrangement['skills'],
                                   invocationLevels=arrangement['invocationLevels'])
                        for (name, _), arrangement in zip(entries, combo)}))
            report['skillPriorities']['arrangements'] = len(skill_axis) - 1
    generated, seen = [], set()
    for formation in formation_axis:
        for skill in skill_axis:
            if formation['identity'] and skill['identity']:
                continue
            ordered = _units_with_order(units, formation['assignment'])
            if skill['changes']:
                ordered = [dict(unit,
                                skills=skill['changes'][unit['name']]['skills'],
                                invocationLevels=skill['changes'][unit['name']]['invocationLevels'])
                           if unit['name'] in skill['changes'] else unit for unit in ordered]
            patch_units = [deepcopy(unit) for unit in ordered]
            signature = json.dumps(patch_units, sort_keys=True, separators=(',', ':'))
            if signature in seen:
                continue
            seen.add(signature)
            generated.append(dict(units=patch_units, formation=formation, skill=skill))
    report['available'] = len(generated)
    cap = options.get('maxVariantsPerCandidate')
    if cap is not None and len(generated) > cap:
        generated, report['truncated'] = generated[:cap], True
    report['generated'] = len(generated)
    for index, entry in enumerate(generated, 1):
        kind = ('formation' if entry['skill']['identity'] else
                'skillPriority' if entry['formation']['identity'] else 'formation+skillPriority')
        changes = {}
        if not entry['formation']['identity']:
            changes['ownUnitsOrder'] = [unit['name'] for unit in entry['units']]
        if entry['skill']['changes']:
            changes['skillOrder'] = [dict(unit=name, **payload)
                                     for name, payload in entry['skill']['changes'].items()]
        report['variants'].append(dict(
            id=f'generated-{index}', kind=kind,
            label=' + '.join(part for part in (
                'formation order' if not entry['formation']['identity'] else None,
                ('skill priority ' + ', '.join(entry['skill']['changes'])) if entry['skill']['changes'] else None,
            ) if part),
            description=('ownUnits order -> [' + ', '.join(unit['name'] for unit in entry['units']) + ']'
                         if not entry['formation']['identity'] else 'unchanged candidate') +
                        '; ' + ('; '.join(
                            f"{name} skills -> {payload['skills']} (invocation {payload['invocationLevels']})"
                            for name, payload in entry['skill']['changes'].items())
                            if entry['skill']['changes'] else 'no skill reordered'),
            changes=changes, patchKeys=['ownUnits'], units=entry['units']))
    return report


def native_placement_keys(scenario):
    """(priority, effectiveDefense) per unit name from the native formation pre-sort."""
    from combat_setup import prepare_setup
    prepared = prepare_setup(deepcopy(scenario))
    return {row['name']: (row['priority'], row['effectiveDefense']) for row in prepared['ownUnits']}


def native_skill_rows():
    from combat_runtime_data import load_data
    return {row['id']: row for row in load_data('weapon-skill-profiles.json')['skills']}


def generate_for_candidates(candidates, strategy, levels, seeds, tick_limit, limits,
                            placement_key_provider=None, skill_row_provider=None, generator=None):
    """One bounded generation report per candidate; never runs anything and never adds values."""
    generate = generator or generate_variants
    placements = placement_key_provider or native_placement_keys
    skill_rows_provider = skill_row_provider or native_skill_rows
    cap = strategy['maxVariantsPerCandidate'] or limits['maxVariants']
    options = dict(formations=strategy['formations'], skillPriorities=strategy['skillPriorities'],
                   maxVariantsPerCandidate=cap)
    reports = {}
    for candidate in candidates:
        scenario = deepcopy(candidate['scenario'])
        scenario.update(schema='ka-special-combat-research-1', encounterId=levels[0]['encounterId'],
                        defeatCount=levels[0]['defeatCount'], mathSeed=seeds[0][0], libSeed=seeds[0][1],
                        tickLimit=tick_limit)
        keys, rows, reason = None, None, None
        try:
            keys = placements(scenario)
        except Exception as error:
            reason = f'{type(error).__name__}: {error}'
        try:
            rows = skill_rows_provider()
        except Exception as error:
            reason = reason or f'{type(error).__name__}: {error}'
        report = generate(scenario, options, placement_keys=keys, skill_rows=rows)
        if keys is None and report['formation']['reason'] is None:
            report['formation']['reason'] = f'native placement keys unavailable ({reason})'
        if rows is None and report['skillPriorities']['reason'] is None:
            report['skillPriorities']['reason'] = f'skill rows unavailable ({reason})'
        report['candidateId'] = candidate['id']
        report['variants'] = [dict(id=BASE_VARIANT_ID, kind='base', label='unchanged candidate',
                                   description='the candidate exactly as supplied; no input reordered',
                                   changes={}, patchKeys=[], units=None)] + report['variants']
        reports[candidate['id']] = report
    return reports


def enumerate_generated_combinations(candidates, generated):
    """candidate x (the unchanged candidate plus its own generated legal variants)."""
    combinations = []
    for candidate in candidates:
        for entry in generated[candidate['id']]['variants']:
            scenario = deepcopy(candidate['scenario'])
            if entry['patchKeys']:
                scenario['ownUnits'] = deepcopy(entry['units'])
            combinations.append(dict(
                id=f"{candidate['id']}::{entry['id']}", candidateId=candidate['id'],
                variantId=entry['id'],
                label=' / '.join(part for part in (candidate.get('label'), entry['label']) if part) or None,
                scenario=scenario))
    return combinations


def generated_summary(candidates, generated):
    """The compact, caller-visible generation block (never the full unit payload)."""
    per_candidate = {}
    available = count = 0
    for candidate in candidates:
        report = generated[candidate['id']]
        available += report['available']
        count += report['generated']
        per_candidate[candidate['id']] = dict(
            available=report['available'], generated=report['generated'],
            truncated=report['truncated'], cap=report['cap'],
            formation=deepcopy(report['formation']), skillPriorities=deepcopy(report['skillPriorities']),
            variants=[{key: value for key, value in entry.items() if key != 'units'}
                      for entry in report['variants']])
    return dict(availableVariants=available, generatedVariants=count, perCandidate=per_candidate,
                rule=('per candidate: the unchanged candidate plus permutations of its own validated '
                      'ownUnits order inside one native placement tie, and per-unit skill order inside '
                      'one native pass; nothing else is generated'))


def enumerate_combinations(candidates, variants):
    """The bounded cross product: candidate x variant (the candidate alone when no variant)."""
    combinations = []
    if not variants:
        for candidate in candidates:
            combinations.append(dict(id=candidate['id'], label=candidate.get('label'),
                                     candidateId=candidate['id'], variantId=None,
                                     scenario=deepcopy(candidate['scenario'])))
        return combinations
    for candidate in candidates:
        for variant in variants:
            scenario = deepcopy(candidate['scenario'])
            scenario.update(deepcopy(variant['patch']))
            combinations.append(dict(
                id=f"{candidate['id']}::{variant['id']}",
                label='/'.join(part for part in (candidate.get('label'), variant.get('label')) if part) or None,
                candidateId=candidate['id'], variantId=variant['id'], scenario=scenario))
    return combinations


def search(request, runner=run_scenario, replay_exporter=None, placement_key_provider=None,
           skill_row_provider=None, generator=None):
    """Run one `ka-battle-search-1` request and return the search envelope.

    With `strategy` the combinations are built from variants generated out of each candidate's own
    validated units instead of from caller-supplied patches. Generation happens before any run, and
    the caller-visible variant cap and the run budget are both enforced first.
    """
    _require(isinstance(request, dict), 'the search request must be a JSON object')
    _require(request.get('schema') == SEARCH_SCHEMA, f'schema must be {SEARCH_SCHEMA}')
    limits = normalize_limits(request.get('limits'))
    levels = normalize_levels(request.get('levels'))
    seeds = normalize_seeds(request.get('seeds'), limits['maxSeeds'])
    candidates = normalize_candidates(request.get('candidates'), limits['maxCandidates'])
    variants = normalize_variants(request.get('variants'), limits['maxVariants'])
    strategy = normalize_strategy(request.get('strategy'))
    _require(not (strategy is not None and variants),
             'a strategy request cannot be combined with explicit variants; send one or the other')
    tick_limit = _positive_int(request.get('tickLimit'), 'tickLimit', DEFAULT_TICK_LIMIT)
    _require(tick_limit <= limits['maxTicks'], f'tickLimit must be <= limits.maxTicks ({limits["maxTicks"]})')

    generated = None
    if strategy is not None:
        generated = generate_for_candidates(candidates, strategy, levels, seeds, tick_limit, limits,
                                            placement_key_provider=placement_key_provider,
                                            skill_row_provider=skill_row_provider, generator=generator)
        combinations = enumerate_generated_combinations(candidates, generated)
        variant_ids = sorted({combination['variantId'] for combination in combinations})
        patch_keys = ['ownUnits'] if any(combination['variantId'] != BASE_VARIANT_ID
                                         for combination in combinations) else []
        enumeration_rule = ('per candidate: the unchanged candidate plus generated permutations of '
                            'its own validated inputs; a variant patches only ownUnits order')
        claim = SEARCH_CLAIM + '; ' + GENERATION_CLAIM
    else:
        combinations = enumerate_combinations(candidates, variants)
        variant_ids = [variant['id'] for variant in variants]
        patch_keys = sorted({key for variant in variants for key in variant['patchKeys']})
        enumeration_rule = ('candidate x variant cross product; variants patch only caller-allowed '
                            'scenario keys')
        claim = SEARCH_CLAIM
    _require(len(combinations) <= limits['maxCandidates'],
             f'search space has {len(combinations)} combinations > limits.maxCandidates '
             f'({limits["maxCandidates"]})')
    planned = len(combinations) * len(levels) * len(seeds)
    _require(planned <= limits['maxRuns'],
             f'run budget exceeded: {len(combinations)} combinations x {len(levels)} levels x '
             f'{len(seeds)} seeds = {planned} runs > limits.maxRuns ({limits["maxRuns"]})')

    evaluated = evaluate(dict(schema=EVAL_SCHEMA, levels=levels, seeds=seeds, tickLimit=tick_limit,
                              limits=request.get('limits'), candidates=combinations),
                         runner=runner, replay_exporter=replay_exporter)
    selected = evaluated['selected']
    if selected is not None:
        combination = next(c for c in combinations if c['id'] == selected['candidateId'])
        selected = dict(selected, candidateId=combination['candidateId'], variantId=combination['variantId'],
                        combinationId=combination['id'])
    enumeration = dict(
        candidates=len(candidates), variants=len(variants), combinations=len(combinations),
        levels=len(levels), seeds=len(seeds), plannedRuns=planned,
        variantIds=variant_ids, variantPatchKeys=patch_keys, rule=enumeration_rule)
    if strategy is not None:
        enumeration['strategy'] = dict(strategy, maxVariantsPerCandidate=(
            strategy['maxVariantsPerCandidate'] or limits['maxVariants']))
        enumeration['generated'] = generated_summary(candidates, generated)
    return dict(
        schema=SEARCH_SCHEMA,
        status='research-only; not certified for gear recommendations',
        claim=claim,
        request=dict(levels=levels, seeds=[list(seed) for seed in seeds], tickLimit=tick_limit,
                     limits=limits),
        enumeration=enumeration,
        budget=evaluated['budget'],
        comparability=evaluated['comparability'],
        candidates=evaluated['candidates'],
        rankings=evaluated['rankings'],
        ranking=evaluated['ranking'],
        unranked=evaluated['unranked'],
        selected=selected,
        replay=evaluated['replay'],
        strategies=strategy_catalog(),
        caveats=list(CAVEATS) + [claim],
        support=dict(mode='research', exactReplaySupported=False, recommendationsSupported=False,
                     claim=claim),
        caps=dict(hardMaxRuns=HARD_MAX_RUNS, hardMaxTicks=HARD_MAX_TICKS, hardMaxSeeds=HARD_MAX_SEEDS,
                  hardMaxCandidates=HARD_MAX_CANDIDATES, hardMaxVariants=HARD_MAX_VARIANTS),
    )
