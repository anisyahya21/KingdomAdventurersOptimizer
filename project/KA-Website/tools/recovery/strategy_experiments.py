"""Durable, explicitly requested experiments using the normal validation stream.

Scheduling only: no alternate simulator, scoring rule, or fabricated statistical verdict.
The coordinator remains the sole writer and Store.record owns every completed battle.
"""
from copy import deepcopy
import time

KEY = 'focusedExperiment'
HISTORY = 'focusedExperimentHistory'


def run_count(value):
    if isinstance(value, bool):
        raise ValueError('Run count must be a positive whole number.')
    try:
        count = int(value)
    except (ValueError, TypeError, OverflowError):
        raise ValueError('Run count must be a positive whole number.') from None
    if str(count) != str(value).strip() or not 1 <= count <= 1_000_000_000:
        raise ValueError('Choose 1 to 1,000,000,000 runs; this is a requested budget, not a confidence threshold.')
    return count


def skill_variants(scenario):
    """Single-slot removals plus whole-team removals of each carried skill.

    The latter detects redundancy across units which single-slot ablation misses. Both are
    conditional on this frozen team; neither establishes a universal skill ranking.
    """
    import search_contract
    names = search_contract.whitelist()
    groups = {}
    for index, unit in enumerate(scenario.get('ownUnits') or []):
        if not unit.get('human'):
            continue
        for slot, skill in enumerate(unit.get('skills') or []):
            groups.setdefault(skill, []).append((index, slot))
            child = deepcopy(scenario)
            for field in ('skills', 'invocationLevels'):
                child['ownUnits'][index][field].pop(slot)
            yield f"{unit['name']}: remove {names.get(skill, f'skill {skill}')}", child
    for skill, slots in groups.items():
        if len(slots) < 2:
            continue
        child = deepcopy(scenario)
        for index, slot in sorted(slots, reverse=True):
            for field in ('skills', 'invocationLevels'):
                child['ownUnits'][index][field].pop(slot)
        yield f"Team: remove all copies of {names.get(skill, f'skill {skill}')}", child


def create(store, candidate_id, count, mode='evaluate'):
    from strategy_optimizer import identity
    from strategy_optimizer_adapter import stats, validate_scenario
    import strategy_learner as learner
    import search_contract
    import strategy_probe

    count = run_count(count)
    if mode not in ('evaluate', 'skills'):
        raise ValueError('Unknown focused experiment mode.')
    previous = store.get(KEY) or {}
    if previous.get('status') == 'active':
        raise ValueError('Finish or end the current focused experiment before starting another.')
    scenario = store.scenario(candidate_id)
    if scenario is None:
        raise ValueError('Restore this build to the library before testing it.')
    if not store.compatible:
        raise ValueError('The simulator changed; these results cannot share this library.')
    if mode == 'skills' and count < 128:
        raise ValueError('Skill comparisons require at least 128 new seeds per build; use more for precise estimates.')
    variants = []
    if mode == 'skills':
        for label, raw in skill_variants(scenario):
            child = validate_scenario(raw)
            # Removal from an imported build must never smuggle an illegal new build into search.
            search_contract.check_scenario(child)
            variants.append((label, child))
        if not variants:
            raise ValueError('This build has no human skills to remove.')
    with store.db:
        # Protect the selected parent during admissions; the transaction restores the previous
        # document on any refusal. The final document replaces this construction marker.
        store.set(KEY, dict(status='preparing', candidateId=candidate_id, arms=[]))
        store.db.execute("UPDATE candidate SET source='saved' WHERE id=? AND source='mutation'",
                         (candidate_id,))
        arms = {candidate_id: 'Original build'}
        for label, child in variants:
            cid = store.add(child, label, strategy_probe.PROBE_SOURCE, stats(child))
            arms.setdefault(cid, label)
            store.db.execute("UPDATE candidate SET source='saved' WHERE id=? AND source='mutation'", (cid,))
            store.set(KEY, dict(status='preparing', candidateId=candidate_id,
                                arms=[dict(candidateId=key) for key in arms]))
            if cid != candidate_id and identity(child) == cid:
                existing = store.db.execute('SELECT parent FROM lineage WHERE candidate=?', (cid,)).fetchone()
                if existing is None or existing[0] is None:
                    learner.record_child(store.db, cid, candidate_id, candidate_id, 1,
                                         'remove-skill', None, label, 'skill-experiment',
                                         int(scenario['encounterId']), store.get('proposals') or 0,
                                         search_contract.SEARCH_SPACE_VERSION, time.time_ns())
        # Shared new ordinals provide a fresh paired block for every arm. Old arms may need
        # catch-up work first; those real battles also count, and the UI reports that work.
        starts = {cid: store.next_ordinal(cid, 'validation') for cid in arms}
        paired_start = max(starts.values())
        end = paired_start + count
        job = dict(id=str(time.time_ns()), status='active', mode=mode,
                   candidateId=candidate_id, encounterId=int(scenario['encounterId']),
                   requestedPerBuild=count, pairedStart=paired_start, pairedEnd=end,
                   arms=[dict(candidateId=cid, label=label, start=starts[cid], target=end)
                         for cid, label in arms.items()], createdAt=time.time())
        if previous:
            history = list(store.get(HISTORY) or [])
            history.append(previous)
            store.set(HISTORY, history)
        store.set(KEY, job)
    return job


def progress(store, job=None):
    job = job or store.get(KEY)
    if not job:
        return None
    result = dict(job)
    result['arms'] = [dict(arm, completed=max(0, min(arm['target'],
                         store.next_ordinal(arm['candidateId'], 'validation')) - arm['start']))
                      for arm in job['arms']]
    result['completed'] = sum(arm['completed'] for arm in result['arms'])
    result['total'] = sum(arm['target'] - arm['start'] for arm in result['arms'])
    return result


def finish(store, status='complete'):
    job = store.get(KEY)
    if job:
        job = dict(job, status=status, endedAt=time.time())
        with store.db:
            store.set(KEY, job)
    return job


def report(db, job):
    """Read the shared fresh block only, excluding catch-up and earlier selected observations."""
    import strategy_evidence
    import strategy_finetune
    import strategy_probe
    if not job:
        return None
    def rows(cid):
        columns = ','.join(strategy_evidence.COLUMNS)
        return [strategy_evidence.decode(*row) for row in db.execute(
            f'SELECT {columns} FROM evidence WHERE candidate=? AND phase=? '
            'AND ordinal>=? AND ordinal<? ORDER BY ordinal',
            (cid, 'validation', job['pairedStart'], job['pairedEnd']))]
    original = rows(job['candidateId'])
    reference = strategy_probe.chest_series(original)
    return dict(job, comparisons=[dict(arm, **strategy_finetune.summarise_rows(
        original if arm['candidateId'] == job['candidateId'] else rows(arm['candidateId']),
        None if arm['candidateId'] == job['candidateId'] else reference)) for arm in job['arms']])


def batch_progress(db, job):
    """How much of one saved job's own requested block is on disk, from evidence alone.

    A saved batch cannot borrow the lifetime ordinal cursor: a reopened library has already advanced
    next_ordinal past the batch, which would read every historical job as finished. Counting the
    compact evidence rows inside each arm's own [start, target) window keeps catch-up arms and the
    shared fresh block together, and a pruned or unresolved run stays absent rather than being
    replaced by a fabricated reward. One aggregate query answers the whole job.
    """
    if not job:
        return dict(completed=0, total=0)
    arms = [arm for arm in (job.get('arms') or []) if isinstance(arm, dict)
            and arm.get('candidateId') is not None and arm.get('start') is not None
            and arm.get('target') is not None]
    total = sum(max(0, int(arm['target']) - int(arm['start'])) for arm in arms)
    if not arms:
        return dict(completed=0, total=total)
    clauses, args = [], []
    for arm in arms:
        clauses.append('(candidate=? AND ordinal>=? AND ordinal<?)')
        args.extend((arm['candidateId'], int(arm['start']), int(arm['target'])))
    completed = db.execute(
        "SELECT COUNT(*) FROM evidence WHERE phase='validation' AND (" + ' OR '.join(clauses) + ')',
        args).fetchone()[0]
    return dict(completed=int(completed), total=int(total))


def learn_stat_points(store, program, measurements):
    """Feed paired, resolved effects into the existing family/unit-specific local learner.

    Repeated inspections replace a point's previous vote; more runs cannot count as multiple
    independent discoveries. An inconclusive estimate withdraws an earlier directional vote.
    """
    import strategy_learner as learner
    import strategy_finetune
    base = int(program['baseline']['effective'])
    scenario = store.scenario(program['parent']['candidateId'])
    if base <= 0 or scenario is None:
        return False
    previous = dict(store.get('controlledStatEvidence') or {})
    local = dict(store.get('localEvidence') or {})
    changed = False
    for value, point in measurements.items():
        if not strategy_finetune.measurement_ready(point):
            continue
        delta = point['paired']
        outcome = ('improved' if delta['lower'] > 0 else 'worsened' if delta['upper'] < 0
                   else 'flat' if delta['lower'] == delta['upper'] == 0 else None)
        token = f"{program['id']}:{value}"
        key = learner.local_key(program['encounterId'], learner.strategy_region(scenario),
                                program['unit'], program['axis'],
                                # A 900% jump is not evidence about a 20% local step.
                                int(round(100 * (int(value) / base - 1.0))))
        new = dict(key=key, outcome=outcome, n=delta['n'], mean=delta['mean'],
                   lower=delta['lower'], upper=delta['upper'], value=int(value),
                   parent=program['parent']['candidateId'])
        old = previous.get(token)
        if old == new:
            continue
        if old and old.get('outcome'):
            entry = dict(local.get(old['key']) or {})
            entry[old['outcome']] = max(0, int(entry.get(old['outcome']) or 0) - 1)
            entry['n'] = max(0, int(entry.get('n') or 0) - 1)
            local[old['key']] = entry
        if outcome:
            entry = dict(local.get(key) or dict(improved=0, flat=0, worsened=0, n=0))
            entry[outcome] = int(entry.get(outcome) or 0) + 1
            entry['n'] = int(entry.get('n') or 0) + 1
            if outcome == 'improved':
                entry['anchor'] = int(value)
            local[key] = entry
        previous[token] = new
        changed = True
    if changed:
        with store.db:
            store.set('controlledStatEvidence', previous)
            store.set('localEvidence', local)
    return changed
