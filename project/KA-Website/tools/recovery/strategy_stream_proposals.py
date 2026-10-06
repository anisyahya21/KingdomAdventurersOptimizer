"""All-strategy proposal service: one owner-aware entry point over the shared joint mechanics.

The Community stream keeps its original strict generator (`strategy_joint_proposals.pool`) and is
never re-implemented here. Every other registered stream is served by its **actual** generator,
resolved from the real call path, which supplies one legal seed build for that strategy; the same
shared mechanics then refine that seed with deliberate crit/speed/incoming-DEX/HTK moves and the
bounded inverse-ATK compensation (the `admit` hook added to `pool`/`_build` preserves each stream's
own admissibility instead of relabelling a Community row).

  * community  - strict Community pool, unchanged.
  * discovery  - `strategy_synthetic_roots.synthetic_root` (fresh synthetic team / region opener).
  * rebel      - `strategy_students.rebel_child` (actual tier + broken-rule ledger recorded).
  * stumble    - `strategy_optimizer_adapter.propose` (the blind legal search-space mutation).
  * average    - retired (Student 9): refused in EVERY mode.
  * mechanism  - `strategy_breakthrough.plan_arms` (controlled counterfactual arm + question).
  * freewill   - declared but inactive: never proposed.

The service is PURE preparation: no battle is run, no database is opened, nothing is reserved or
written. It never falls back to another owner - a refusal returns `[]` plus an explicit reason in
the optional `diagnostics` mapping. Disabled-owner filtering stays the Coordinator's job, but a
`allocation` argument offers the positive-allocation check and a zero is refused here.
"""
from __future__ import annotations

import hashlib
import random

import search_contract as contract
import strategy_build_domain as domain
import strategy_joint_proposals as joint
import strategy_mechanics as mechanics
import strategy_students as students

VERSION = 'strategy-stream-proposals-1'

# Owner names are READ from the actual registration, never invented here.
REGISTERED_OWNERS = tuple(students.SHARE_STREAMS)
DECLARED_OWNERS = tuple(students.STUDENT_NAMES)
INACTIVE_OWNERS = (students.STUDENT_FREEWILL,)
RETIRED_OWNERS = (students.STUDENT_AVERAGE,)
# `stumble` currently has no dedicated dispatch branch; its real generator is the blind mutation.
DEFERRED_OWNERS = (students.STUDENT_STUMBLING,)

MAXIMUM_CAP = joint.MAXIMUM_CAP
# At most this many legal seed builds are tried per call; discovery/stumble/rebel supply one, the
# mechanism stream supplies its bounded control arms (the reference arm is never proposed).
SEED_BUDGET = 4
# Bounded deterministic draws for one rebel tier: a tier may strip the DPS role, and a seed without
# a DPS has no joint axis, so a few draws are tried before the tier is honestly reported empty.
REBEL_SEED_ATTEMPTS = 4

_PURPOSE_ALIASES = {'comparison': 'improvement', 'exploration': 'improvement'}


def supported_owners():
    """The owner names this service actually proposes for (registered, active, not retired)."""
    return tuple(name for name in REGISTERED_OWNERS
                 if name not in INACTIVE_OWNERS and name not in RETIRED_OWNERS)


def stream_generator(owner):
    """Resolve the ACTUAL registered generator callable for one non-community owner.

    Resolution follows the real call path (the optimiser's own fresh-root, rebel, blind-search and
    breakthrough modules); nothing here re-implements a generator or invents a module name. Returns
    None for owners that have no seed generator (community is served by the strict pool itself).
    """
    if owner == students.STREAM_DISCOVERY:
        import strategy_synthetic_roots
        return strategy_synthetic_roots.synthetic_root
    if owner == students.STUDENT_REBEL:
        return students.rebel_child
    if owner == students.STUDENT_STUMBLING:
        import strategy_optimizer_adapter
        return strategy_optimizer_adapter.propose
    if owner == students.STUDENT_MECHANISM:
        import strategy_breakthrough
        return strategy_breakthrough.plan_arms
    return None


def generator_name(owner):
    function = stream_generator(owner)
    if function is None:
        return None
    return '%s.%s' % (getattr(function, '__module__', '?'), getattr(function, '__name__', '?'))


def _digest(value):
    return hashlib.sha256(domain.canonical(value).encode('utf-8')).hexdigest()


def _prepare(scenario):
    return mechanics.normalize_scenario(scenario)


def _domain_admit(child, parent):
    """Shared domain check: the refined child must still satisfy the search contract."""
    try:
        contract.check_scenario(child)
    except contract.ContractError as error:
        return False, [dict(rule='domain', reason=str(error))]
    return True, []


def _rebel_admit(root_parent, tier, seed_breaks):
    """Preserve the rebel stream's own rule: the tier and its actual broken-rule set never move."""
    def admit(child, _seed):
        broken = set(students.rule_breaks(child, root_parent))
        if broken != set(seed_breaks):
            return False, [dict(rule='rebel-tier', reason='refinement moved the broken rule set')]
        if not tier.takes(broken):
            return False, [dict(rule='rebel-tier', reason='breaks fall outside %s' % tier.name)]
        return _domain_admit(child, _seed)
    return admit


def _joint_purpose(purpose):
    if purpose in joint.PURPOSES:
        return purpose
    return _PURPOSE_ALIASES.get(purpose, 'improvement')


def _seed_candidates(owner, parent, *, round_index, context, rng):
    """One bounded, legal seed build from the owner's actual generator.

    Returns `(seeds, reason)`; a refusal names the generator that produced nothing. There is never
    a fallback to another owner's generator.
    """
    normalized = _prepare(parent)
    generator = stream_generator(owner)
    if generator is None:
        return [], 'owner %r has no registered seed generator' % owner
    if owner == students.STREAM_DISCOVERY:
        base = (context or {}).get('baseScenario') or parent
        enemy_count = (context or {}).get('enemyCount')
        if enemy_count is None:
            try:
                enemy_count = contract.enemy_count(normalized.get('encounterId'))
            except Exception as error:
                return [], 'cannot resolve the discovery enemy count: %r' % (error,)
        index = int(round_index)
        try:
            scenario, roles = generator(base, index, enemy_count)
        except Exception as error:
            return [], 'synthetic_root refused this parent: %r' % (error,)
        return [dict(scenario=scenario, seedId=domain.identity(scenario),
                     generator=generator_name(owner),
                     extra=dict(seedIndex=index, seedRoles=list(roles),
                                enemyCount=enemy_count))], None
    if owner == students.STUDENT_STUMBLING:
        seed = int(round_index) * 100003 + 1
        try:
            scenario = generator(parent, seed)
        except Exception as error:
            return [], 'blind mutation refused this parent: %r' % (error,)
        return [dict(scenario=scenario, seedId=domain.identity(scenario),
                     generator=generator_name(owner), extra=dict(seed=seed))], None
    if owner == students.STUDENT_REBEL:
        tiers = students.tiers()
        tier = tiers[int(round_index) % len(tiers)]
        # A fresh random draw would make the pool non-reproducible, so each rebel seed is drawn from
        # a round/attempt/tier keyed generator exactly as `students.replenish_rebels` keys its draw.
        sizes = sum(ord(char) for char in tier.name)
        seeds = []
        for attempt in range(REBEL_SEED_ATTEMPTS):
            if attempt == 0 and rng is not None:
                draw = rng
            else:
                draw = random.Random(int(round_index) * 7919 + attempt * 104729 + sizes)
            try:
                scenario, breaks = generator(parent, tier, draw)
            except Exception as error:
                return [], 'rebel_child refused this parent: %r' % (error,)
            if scenario is None:
                continue
            seeds.append(dict(scenario=scenario, seedId=domain.identity(scenario),
                              generator=generator_name(owner),
                              extra=dict(tier=tier.name, tierRules=list(tier.rules),
                                         tierWindow=list(tier.span), brokenRules=list(breaks)),
                              tier=tier, breaks=tuple(breaks)))
        if not seeds:
            return [], '%s refused from this parent' % tier.name
        return seeds, None
    if owner == students.STUDENT_MECHANISM:
        import strategy_breakthrough as breakthrough
        try:
            import strategy_probe
            unit = strategy_probe.default_probe_unit(normalized, breakthrough.PRIMARY_AXES)
        except Exception as error:
            return [], 'no probe unit for the mechanism stream: %r' % (error,)
        try:
            arms, error = generator(normalized, unit)
        except Exception as error:
            return [], 'plan_arms refused this parent: %r' % (error,)
        if not arms:
            return [], 'plan_arms produced no arms: %s' % (error,)
        reference = arms[0]
        seeds = []
        for arm in arms[1:]:
            question = ('does the %s change (%s) move the measured outcome off the unchanged '
                        'parent?' % (arm.get('role'), arm.get('axis')))
            seeds.append(dict(
                scenario=arm['scenario'], seedId=domain.identity(arm['scenario']),
                generator=generator_name(owner),
                extra=dict(arm=arm.get('name'), armRole=arm.get('role'), axis=arm.get('axis'),
                           value=arm.get('value'), diagnosticQuestion=question,
                           counterfactualArm=reference.get('name'),
                           counterfactualId=domain.identity(normalized))))
        if not seeds:
            return [], 'plan_arms produced only the unchanged parent arm'
        return seeds, None
    return [], 'owner %r has no seed path' % owner


def _parameter_ids(prepared):
    ids = set()
    for unit in prepared['ownUnits']:
        ids.update(unit.get('effectiveParameters', {}).keys())
    return ids


def _changed_fields(before_scenario, after_scenario):
    before = mechanics.prepared_setup(_prepare(before_scenario))
    after = mechanics.prepared_setup(_prepare(after_scenario))
    fields = []
    for index in range(min(len(before['ownUnits']), len(after['ownUnits']))):
        for pid in sorted(_parameter_ids(before) | _parameter_ids(after)):
            if joint._effective(before, index, pid) != joint._effective(after, index, pid):
                fields.append('ownUnits.%d.parameters.%d' % (index, pid))
    if len(before['ownUnits']) != len(after['ownUnits']):
        fields.append('ownUnits.length')
    return sorted(fields)


def _mechanical_summary(scenario):
    normalized = _prepare(scenario)
    profile = mechanics.profile(normalized)
    roles = joint._role_indices(normalized)
    dps = roles.get(students.ROLE_DPS)
    if dps is None:
        return {}
    unit = profile['ownUnits'][dps]
    stats = unit['effectiveStats']
    incoming = [entry['hitRate'] for entry in unit['incomingAccuracy']['perEnemy']
                if entry.get('hitRate') is not None]
    outgoing = [entry['perHitSummary']['mean'] for entry in unit['outgoing']
                if entry['perHitSummary'].get('mean') is not None]
    return dict(
        atk=float(stats['attack']), lck=float(stats['luck']), spd=float(stats['speed']),
        dex=float(stats['dexterity']),
        critRate=float(mechanics.critical_rate(stats['luck'])),
        interval=int(unit['speed']['interval']),
        incomingHitMean=(sum(incoming) / len(incoming)) if incoming else 0.0,
        outgoingDamageMean=(sum(outgoing) / len(outgoing)) if outgoing else 0.0,
    )


def _counterfactual(root_parent, child, extra=None):
    before = _mechanical_summary(root_parent)
    after = _mechanical_summary(child)
    deltas = {key: round(after.get(key, 0.0) - before.get(key, 0.0), 6) for key in before}
    result = dict(ownerParent=before, candidate=after, deltas=deltas,
                  note='pre-run mechanics only; earned reward is never predicted here')
    if extra:
        result.update(extra)
    return result


def _strategy_basis(owner, seed, root_id, constraints, purpose):
    basis = dict(owner=owner, stream=owner, generator=seed.get('generator'), purpose=purpose,
                 constraintContext=(constraints.get('context') if constraints else None),
                 ownerParentId=root_id, seedId=seed.get('seedId'), seedBudget=SEED_BUDGET)
    basis.update(seed.get('extra') or {})
    return basis


def _augment(owner, row, seed, root_parent, constraints, purpose, root_id=None):
    row = dict(row)
    root_id = root_id or domain.identity(root_parent)
    row['owner'] = owner
    row['parentId'] = root_id
    row['ownerParentId'] = root_id
    row['seedId'] = seed.get('seedId')
    row['lineage'] = dict(owner=owner, ownerParentId=root_id, seedId=seed.get('seedId'),
                          parentId=root_id, childIdentity=row['childIdentity'],
                          generator=seed.get('generator'))
    row['strategyBasis'] = _strategy_basis(owner, seed, root_id, constraints, purpose)
    row['changedFields'] = _changed_fields(root_parent, row['scenario'])
    row['counterfactual'] = _counterfactual(root_parent, row['scenario'],
                                            extra=(seed.get('extra') or {}))
    return row


def proposal_pool(owner, parent, *, constraints=None, evidence=None, round_index=0, maximum=12,
                  purpose='improvement', question=None, diagnostics=None, allocation=None,
                  config=None, context=None, rng=None):
    """Standard joint proposal rows for one owner, or `[]` with an explicit `diagnostics` reason.

    `parent` is the scenario the caller holds. The Community owner delegates to the unchanged strict
    pool. Other owners ask their actual generator for one legal seed and refine it with the shared
    mechanics under that stream's own admissibility. `allocation`, when supplied, is only checked
    for positivity here; filtering disabled owners remains the Coordinator's responsibility.
    """
    diagnostic = diagnostics if isinstance(diagnostics, dict) else {}
    owner = str(owner)

    def refuse(reason, code='no-proposal'):
        diagnostic.clear()
        diagnostic.update(owner=owner, proposed=0, reason=reason, code=code)
        return []

    if owner in INACTIVE_OWNERS:
        return refuse('%r is inactive and is never allocated' % owner, 'inactive-owner')
    if owner in RETIRED_OWNERS:
        return refuse('%r is retired in every mode' % owner, 'retired-owner')
    if owner not in REGISTERED_OWNERS:
        return refuse('unknown owner %r; expected one of %s' % (owner, REGISTERED_OWNERS),
                      'unknown-owner')
    maximum = max(0, min(int(maximum), MAXIMUM_CAP))
    if not maximum:
        return refuse('maximum budget is zero', 'zero-budget')
    if allocation is not None:
        try:
            positive = float(allocation) > 0
        except (TypeError, ValueError):
            positive = False
        if not positive:
            return refuse('%r has no positive allocation; disabled-owner filtering stays with '
                          'the Coordinator' % owner, 'zero-allocation')
    if config is not None and config.get('mode') == 'community-first' \
            and owner != students.STUDENT_COMMUNITY:
        return refuse('mode community-first is Community-only; %r is not eligible' % owner,
                      'community-only-mode')
    if constraints is not None and not domain.constraint_schema(constraints)['valid']:
        raise ValueError('Invalid build constraint document')
    try:
        normalized = _prepare(parent)
    except Exception as error:
        return refuse('parent is not a legal scenario: %r' % (error,), 'invalid-parent')
    root_id = domain.identity(normalized)

    if owner == students.STUDENT_COMMUNITY:
        rows = joint.pool(normalized, constraints=constraints, evidence=evidence,
                          round_index=round_index, maximum=maximum,
                          purpose=_joint_purpose(purpose), question=question)
        if not rows:
            return refuse('the strict Community pool produced no candidate for this parent',
                          'community-empty')
        seed = dict(generator='strategy_joint_proposals.pool', seedId=None)
        result = [_augment(owner, row, seed, normalized, constraints, purpose, root_id=root_id)
                  for row in rows[:maximum]]
        diagnostic.clear()
        diagnostic.update(owner=owner, proposed=len(result), reason=None, code='ok',
                          generator=seed['generator'])
        return result

    seeds, reason = _seed_candidates(owner, normalized, round_index=round_index,
                                     context=context, rng=rng)
    if not seeds:
        return refuse(reason or 'the registered generator produced no seed', 'seed-empty')
    if owner == students.STUDENT_MECHANISM:
        # Rotate the controlled arms across rounds; the reference arm (A) was dropped at seed time.
        offset = int(round_index) % len(seeds)
        ordered = (seeds[offset:] + seeds[:offset])[:SEED_BUDGET]
    else:
        ordered = seeds[:SEED_BUDGET]
    rows, seen = [], {root_id}
    for seed in ordered:
        if owner == students.STUDENT_REBEL:
            admit = _rebel_admit(normalized, seed['tier'], seed['breaks'])
        else:
            admit = _domain_admit
        refined = joint.pool(seed['scenario'], constraints=constraints, evidence=evidence,
                             round_index=round_index, maximum=maximum,
                             purpose=_joint_purpose(purpose), question=question, admit=admit)
        for row in refined:
            if row['childIdentity'] in seen:
                continue
            seen.add(row['childIdentity'])
            rows.append(_augment(owner, row, seed, normalized, constraints, purpose,
                                 root_id=root_id))
        if len(rows) >= maximum:
            break
    if not rows:
        return refuse('the %s seed produced no admitted joint refinement' % owner,
                      'refinement-empty')
    rows.sort(key=lambda row: (-row['novelty'], row['id']))
    diagnostic.clear()
    diagnostic.update(owner=owner, proposed=len(rows[:maximum]), reason=None, code='ok',
                      generator=seeds[0]['generator'], seeds=len(seeds))
    return rows[:maximum]


__all__ = ['VERSION', 'REGISTERED_OWNERS', 'DECLARED_OWNERS', 'INACTIVE_OWNERS', 'RETIRED_OWNERS',
           'MAXIMUM_CAP', 'supported_owners', 'stream_generator', 'generator_name', 'proposal_pool']
