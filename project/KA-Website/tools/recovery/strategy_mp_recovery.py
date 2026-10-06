"""MP-limited builds: notice the condition, then test the herb-supported correction.

The engine can already use a Holy Herb, and the observer already samples MP and latches the live
`<=3%` policy. What did not exist is the *optimiser* half: nothing watched MP on an ordinary no-item
candidate, and nothing turned "this build's healer ran out of MP" into a changed build that could be
measured. That half is this module.

The chain it implements, in order:

    ordinary run (no item policy) carries MP telemetry for the role-resolved DPS and healer
      -> a run crosses the requested `<=3% of its own maximum` threshold *before the battle ends*
      -> the candidate is an eligible parent (any stream, any rank, not only a leader)
      -> the configured item allowance and budget are checked
      -> an herb-enabled child is created with its own canonical identity, or reused if this exact
         request already exists
      -> a bounded, declared bank of comparable fresh seeds is scheduled for the child
      -> the persisted results are compared to the parent and reported, with the herb's own telemetry

Three things this module is careful about:

  * **observation and permission are separate.** `combat_progress` samples the units named by the
    scenario's `mpWatchUnits` (the observation-only list this module writes into the *evaluation copy*
    of a candidate) or by `holyHerbTriggerUnits` (the list that may spend a charge). A no-item
    candidate therefore reports its healer's MP minimums without being given any stock.
  * **nothing is created without an explicit allowance.** With no configured stock and use cap the
    module records the eligible parents and reports that they await an item allowance; it does not
    invent a quantity and it does not create work.
  * **a low MP reading is not a result.** The child's own reward, verdicts, herb uses and MP are
    reported next to the parent's; recovering MP is not treated as an improvement.

Nothing here changes the parent's scenario, its recorded runs or its identity. The herb-enabled child
differs from its parent only in the item policy, so `strategy_optimizer.identity` gives it a distinct
candidate id and its statistics are kept separately.
"""
from __future__ import annotations

import hashlib
import json
import time

import strategy_students as students

#: The persisted allowance. `enabled` is the user's permission to create these experiments at all;
#: `stock`/`maxUses` are the game quantities they declare (the game never states one); `bank` is the
#: comparable-run bank each child is measured over; `maxChildren` bounds how many distinct children
#: this stream may hold. Defaults create nothing.
SETTING_KEY = 'mpRecoverySetting'
DEFAULT_SETTING = dict(enabled=False, stock=0, maxUses=0, bank=64, maxChildren=8)
#: The durable request ledger: one row per parent+policy request, so a repeated observation reuses the
#: child instead of creating another copy of it.
LEDGER_KEY = 'mpRecoveryLedger'
LEDGER_VERSION = 1
#: The requesting owner. A general MP retry belongs to this operation, not to Breakthrough and not to
#: whichever student happened to create the parent.
SOURCE = 'mp-recovery'
LABEL_PREFIX = 'MP recovery · '
#: The requested threshold, in percent of the unit's own maximum MP, read from the observer that
#: implements the live policy so the two can never drift apart.
THRESHOLD_PERCENT = 3
#: How many candidates one pass may examine, and how many requests one payload names. The time budget
#: is the real bound; this is the belt-and-braces cap that keeps a fast machine from doing unbounded
#: work in one pass when every candidate is cheap.
SCAN_PER_PASS = 240
SCAN_PER_ENCOUNTER = SCAN_PER_PASS  # kept as the old name so an import of it still resolves
PUBLISHED_PARENTS = 4
#: The wall-clock budget one slow pass may spend scanning, in seconds. This runs on the coordinator
#: thread, so an unbounded scan starves dispatch: measured on the live library before this bound
#: existed, resolving roles for 800 residents per pass cost ~5 s of a 5 s cadence and the pool fell to
#: 2.3 runs/s with 50 idle workers. A pass now stops at the budget and resumes from the next encounter.
SCAN_SECONDS = 0.12

#: Role resolution reads the derived placement, which is a full `prepare_setup` (~7 ms on a real
#: scenario). A candidate's scenario is immutable, so its watch list is too: cache it by candidate id.
_WATCH_CACHE = {}
WATCH_CACHE_LIMIT = 8192


def _watch_units_cached(scenario, key):
    """`watch_units` memoised by an immutable key (a candidate id), bounded."""
    if key is not None:
        found = _WATCH_CACHE.get(key)
        if found is not None:
            return found
    result = watch_units(scenario)
    if key is not None:
        if len(_WATCH_CACHE) >= WATCH_CACHE_LIMIT:
            _WATCH_CACHE.clear()
        _WATCH_CACHE[key] = result
    return result


_TELEMETRY_CACHE = {}
#: The telemetry set is "candidates that have reported MP"; a new one appears within a pass or two, so
#: a minute of cache is not a staleness risk and saves a `LIKE` scan over the whole `meta` table on
#: every coordinator pass - measured in the five-minute trace as most of this pass's 142 ms.
TELEMETRY_CACHE_SECONDS = 60.0


def _telemetry_candidates(store):
    """Ids whose stored aggregates carry an MP block, read from `meta` in one query.

    Only a candidate that has already been measured *with* a watch list can be MP-eligible, and the
    aggregate says so in its own `mpRuns` counter. Filtering on it turns a pass from "decode every
    resident's scenario" into "look at the handful of builds that actually report MP", which is what
    keeps this off the critical path. Cached briefly so a burst of passes pays for it once.
    """
    cached = _TELEMETRY_CACHE.get('value')
    now = time.time()
    if cached is not None and now - cached['at'] < TELEMETRY_CACHE_SECONDS:
        return cached['ids']
    ids = set()
    for (row_key,) in store.db.execute(
            'SELECT key FROM meta WHERE key LIKE \'aggregate:%\' AND value LIKE \'%"mpRuns"%\' '
            'LIMIT 20000'):
        parts = str(row_key).split(':')
        if len(parts) == 3:
            ids.add(parts[1])
    _TELEMETRY_CACHE['value'] = dict(at=now, ids=ids)
    return ids


def setting(store):
    """The configured allowance, with every field validated, defaulting to "create nothing"."""
    out = dict(DEFAULT_SETTING)
    raw = dict(store.get(SETTING_KEY) or {})
    out['enabled'] = bool(raw.get('enabled', out['enabled']))
    for field in ('stock', 'maxUses', 'bank', 'maxChildren'):
        try:
            value = int(raw.get(field, out[field]))
        except (TypeError, ValueError):
            value = DEFAULT_SETTING[field]
        out[field] = max(0, value)
    out['bank'] = max(1, out['bank'])
    out['thresholdPercent'] = THRESHOLD_PERCENT
    out['source'] = SOURCE
    # A permission with nothing to spend is not a permission: it reports as awaiting a quantity rather
    # than as enabled, so the surface can never look like it is working while creating no trials.
    out['effective'] = bool(out['enabled'] and out['stock'] > 0 and out['maxUses'] > 0)
    return out


def configure(store, values):
    """Validate and persist an allowance. A refused value is reported, never clamped silently."""
    current = setting(store)
    updated = dict(enabled=current['enabled'], stock=current['stock'], maxUses=current['maxUses'],
                   bank=current['bank'], maxChildren=current['maxChildren'])
    for field, raw in (values or {}).items():
        if field not in updated:
            return dict(ok=False, error=f'unknown MP-recovery setting {field!r}')
        if field == 'enabled':
            if not isinstance(raw, bool):
                return dict(ok=False, error='enabled must be true or false')
            updated['enabled'] = raw
            continue
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return dict(ok=False, error=f'{field} must be a whole number')
        if value < 0:
            return dict(ok=False, error=f'{field} must not be negative')
        if field == 'bank' and value < 1:
            return dict(ok=False, error='bank must be at least one run')
        updated[field] = value
    if updated['maxUses'] > updated['stock']:
        return dict(ok=False,
                    error='the use cap cannot exceed the declared stock: the policy can never spend '
                          'more charges than it holds')
    if updated['enabled'] and updated['stock'] > 0 and updated['maxUses'] < 1:
        return dict(ok=False,
                    error='an enabled allowance needs a use cap of at least one, or the automatic '
                          'policy can never fire')
    with store.db:
        store.set(SETTING_KEY, updated)
    return dict(ok=True, setting=setting(store))


def _placed(scenario):
    """`[(placement index, unit)]` in the derived placement order."""
    by_index = students._by_index(scenario)
    return [(index, by_index[index]) for index in sorted(by_index)]


def watch_units(scenario):
    """`(names, detail)` - the role-resolved units whose MP is worth observing.

    The DPS and the healer, resolved from the *derived placement* and the unit's own carried skills
    (`strategy_students.role`), never from roster order and never from display position. Fodder are
    deliberately excluded: a fodder dipping must not be what proposes an experiment, which is the same
    rule the herb policy already uses for spending a charge.

    Returns at most `combat_progress.MP_WATCH_LIMIT` names, DPS first then healer - the order the herb
    policy declares them in. `detail` states what could not be resolved rather than guessing: a build
    with no healer has no healer to watch, and that is reported.
    """
    dps, healer = None, None
    for _index, unit in _placed(scenario):
        role = students.role(unit)
        if role == students.ROLE_DPS and dps is None:
            dps = unit.get('name')
        elif role == students.ROLE_HEALER and healer is None:
            healer = unit.get('name')
    names = [name for name in (dps, healer) if name]
    missing = []
    if dps is None:
        missing.append('no unit carries an attacking skill (no DPS)')
    if healer is None:
        missing.append('no unit carries a healing skill (no healer)')
    return names, ('; '.join(missing) if missing else None)


def observation_patch(scenario, key=None):
    """The observation-only fields to add to the *evaluation copy* of one candidate.

    Never written to the stored candidate: identity, replay and provenance are all unaffected by an
    observation that cannot change what the battle does. Empty when no role can be resolved.

    A scenario that already declares `holyHerbTriggerUnits` is left alone. Those units are both the
    policy's trigger and what the observer samples, and replacing them with a *different* watched pair
    would leave the policy able to fire only for units it is not watching - which is exactly the kind
    of silent misconfiguration this module exists to remove.

    `key` is the candidate id when the caller has it: role resolution is a full `prepare_setup` (~7 ms
    on a real scenario) and a candidate's scenario never changes, so it is memoised. This runs on the
    dispatch path, where paying that per candidate per pass is exactly the kind of cost that starves
    the worker pool.
    """
    if scenario.get('holyHerbTriggerUnits'):
        return {}
    names, _detail = _watch_units_cached(scenario, key)
    return {'mpWatchUnits': names} if names else {}


def _evidence_rows(store, candidate, limit=8):
    """The retained run rows of one candidate, newest first, decoded defensively.

    Materialised with `fetchall` on purpose: `crossing_run` returns from inside this list, and a
    cursor left open by an early `return` holds a read lock that makes `Store.close()`'s
    `wal_checkpoint(TRUNCATE)` fail with "database table is locked".
    """
    rows = []
    for phase, ordinal, blob in store.db.execute(
            'SELECT phase,ordinal,result FROM run WHERE candidate=? ORDER BY ordinal DESC LIMIT ?',
            (candidate, int(limit))).fetchall():
        try:
            payload = json.loads(blob)
        except (TypeError, ValueError):
            continue
        rows.append(dict(phase=phase, ordinal=int(ordinal), result=payload))
    return rows


#: A candidate's crossing verdict is stable for as long as its newest runs are: re-running the query and
#: re-parsing up to eight run payloads on every pass cost 79 ms of this pass's 125 ms in the five-minute
#: trace (`docs/benchmarks/optimizer-20260926-trace`). Cached per candidate for a minute, so a new run
#: is still seen promptly while a deep history is read once.
_CROSSING_CACHE = {}
CROSSING_CACHE_SECONDS = 60.0


def crossing_run(store, candidate, watched, limit=8):
    """One retained run in which a watched unit crossed the threshold **before the battle ended**.

    This is what separates "the healer ran dry while the fight was still being lost" from "the pool was
    sampled low after the outcome was already settled". `ticks` is the run's own end, so a crossing at
    or after it is context, not a reason to spend runs. A run whose MP telemetry is absent contributes
    nothing here: it is not evidence of a crossing and it is not evidence against one.
    """
    watched = [name for name in watched if name]
    # Keyed by the library as well as the candidate: two different libraries can hold the same candidate
    # id (identical scenarios hash the same), and a verdict from one is not evidence about the other.
    cache_key = (str(getattr(store, 'path', '')), candidate)
    cached = _CROSSING_CACHE.get(cache_key)
    now = time.time()
    if cached is not None and now - cached[0] < CROSSING_CACHE_SECONDS:
        found = cached[1]
        # A cached hit is only reused for the same watch list; a different list is a different question.
        return found if cached[2] == tuple(watched) else None
    for row in _evidence_rows(store, candidate, limit=limit):
        result = row['result']
        metrics = result.get('mpMetrics')
        if not isinstance(metrics, list):
            continue
        end = result.get('ticks')
        for entry in metrics:
            if not isinstance(entry, dict) or entry.get('name') not in watched:
                continue
            if not entry.get('reachedLowMp'):
                continue
            tick = entry.get('firstLowMpTick')
            if not isinstance(tick, int) or isinstance(tick, bool) or tick < 0:
                continue
            if isinstance(end, int) and not isinstance(end, bool) and tick >= end:
                # The pool was only seen this low after the battle had effectively finished.
                continue
            found = dict(unit=entry['name'], tick=tick, phase=entry.get('firstLowMpPhase'),
                         runEnd=end, verdict=result.get('verdict'),
                         minimumMp=entry.get('minimumMp'),
                         minimumMpPercent=entry.get('minimumMpPercent'),
                         evidencePhase=row['phase'], ordinal=row['ordinal'])
            _CROSSING_CACHE[cache_key] = (now, found, tuple(watched))
            return found
    _CROSSING_CACHE[cache_key] = (now, None, tuple(watched))
    return None


def readings(scenario, aggregate, names=None):
    """`({unit: reading}, detail)` - the merged MP history for the units this candidate would watch.

    `names` lets a caller that has already resolved the watch list (and paid the `prepare_setup` behind
    it) reuse that answer instead of resolving it a second time for the same candidate.
    """
    if names is None:
        names, detail = watch_units(scenario)
    else:
        detail = None
    merged = (aggregate or {}).get('mp') or {}
    out = {}
    for name in names:
        entry = merged.get(name)
        if isinstance(entry, dict):
            out[name] = dict(entry)
    return out, detail


def herb_policy(scenario):
    """`(stock, maxUses, watched)` as the scenario declares them - what is configured, not what ran."""
    return (int(scenario.get('holyHerbStock') or 0), int(scenario.get('holyHerbMaxUses') or 0),
            [str(name) for name in scenario.get('holyHerbTriggerUnits') or ()])


def requested_policy(scenario, allowance, watched):
    """The item policy a correction for this parent would carry, or None with a reason."""
    _stock, max_uses, _declared = herb_policy(scenario)
    if max_uses > 0:
        return None, 'the parent already declares an automatic herb policy'
    if not watched:
        return None, 'no role-resolved unit could be watched, so no unit could authorise a charge'
    return dict(holyHerbStock=int(allowance['stock']), holyHerbMaxUses=int(allowance['maxUses']),
                holyHerbTriggerUnits=list(watched)), None


def _request_key(parent, policy):
    payload = json.dumps([parent, policy['holyHerbStock'], policy['holyHerbMaxUses'],
                          list(policy['holyHerbTriggerUnits'])], sort_keys=True)
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()[:32]


def ledger(store):
    state = dict(store.get(LEDGER_KEY) or {})
    state.setdefault('version', LEDGER_VERSION)
    state.setdefault('requests', {})
    state.setdefault('proposals', 0)
    return state


def _save(store, state):
    state['version'] = LEDGER_VERSION
    with store.db:
        store.set(LEDGER_KEY, state)


def _observed(store, candidate, bank):
    """How many of the child's declared bank ordinals have already been measured."""
    row = store.db.execute(
        'SELECT COUNT(DISTINCT ordinal) FROM evidence WHERE candidate=? AND ordinal>=0 AND ordinal<?',
        (candidate, int(bank))).fetchone()
    return int(row[0] or 0)


def banks(store):
    """`{child id: bank}` for every request still owing comparable runs.

    Returned in the shape the scheduler already merges declared experiment banks in, so a correction is
    measured through the existing mechanism rather than a second one. A finished request stops being
    offered: the bank is a total, not a renewal.
    """
    state = ledger(store)
    out = {}
    for request in state['requests'].values():
        child = request.get('child')
        if not child or request.get('status') != 'pending':
            continue
        bank = int(request.get('bank') or 0)
        if bank and _observed(store, child, bank) < bank:
            out[child] = max(out.get(child, 0), bank)
    return out


def _candidate_aggregate(store, candidate):
    """One candidate's discovery+validation counters, merged by the optimiser's own rule.

    Both phases come from one query: this runs per examined candidate inside a time-bounded pass, so
    two round trips per candidate was half the cost of looking at one.
    """
    import strategy_optimizer
    merged = None
    rows = store.db.execute('SELECT key,value FROM meta WHERE key IN (?,?)',
                            (f'aggregate:{candidate}:discovery',
                             f'aggregate:{candidate}:validation')).fetchall()
    for _key, value in rows:
        try:
            total = json.loads(value)
        except (TypeError, ValueError):
            continue
        if total:
            merged = strategy_optimizer.merge_aggregates(merged, total)
    return merged or {}


def advance(store, now=None, limit=SCAN_PER_PASS, encounters=None, budget_seconds=SCAN_SECONDS):
    """One slow pass: find eligible parents, apply the allowance, create or reuse corrections.

    Returns a compact summary of what it decided. It never runs a battle: a child is measured by the
    scheduler through the declared bank `banks()` returns. The pass stops after the first encounter it
    changes anything in, so a single pass stays cheap and the next pass continues the rotation.

    Two bounds keep it off the coordinator's critical path, because it runs on the same thread that
    feeds the workers: it examines only candidates whose own aggregates report MP telemetry, and it
    stops at `budget_seconds` whatever it has found, resuming from the next encounter in the rotation.
    Before those bounds existed a pass walked every resident of every encounter and cost ~5 s of a 5 s
    cadence, which starved dispatch to 2.3 runs/s with a full worker pool idle.
    """
    allowance = setting(store)
    state = ledger(store)
    started = time.time() if now is None else float(now)
    deadline = time.monotonic() + max(0.0, float(budget_seconds))
    summary = dict(enabled=allowance['enabled'], effective=allowance['effective'], created=[],
                   reused=[], awaiting=[], unresolved=[], examined=0, decision=None,
                   skippedWithoutTelemetry=0, timedOut=False, thresholdPercent=THRESHOLD_PERCENT)
    # A build that has never reported MP cannot be eligible, so the scan is over the candidates whose
    # own aggregates carry an MP block - not over the population. `candidate_encounters` is the store's
    # own cached index, so this costs no scenario decode and no per-encounter query.
    telemetry = _telemetry_candidates(store)
    if encounters is None:
        # The ordinary case: no scope was asked for, and `candidate_encounters()` would rebuild the
        # whole index - which every prune invalidates, and prunes happen continuously on a saturated
        # library. The candidate's own encounter is only needed for the event detail.
        encounters_of = None
        candidates = sorted(telemetry)
    else:
        encounters_of = store.candidate_encounters()
        allowed = {int(value) for value in encounters}
        candidates = sorted(cid for cid in telemetry if encounters_of.get(cid) in allowed)
    summary['candidatesWithTelemetry'] = len(candidates)
    # Rotate, so a pass that times out never starves the candidates after it.
    if candidates:
        offset = int(state.get('scanCursor') or 0) % len(candidates)
        candidates = candidates[offset:] + candidates[:offset]
        state['scanCursor'] = (offset + 1) % len(candidates)
    changed = False
    for candidate in candidates[:max(0, int(limit))]:
        if time.monotonic() > deadline:
            summary['timedOut'] = True
            break
        encounter = encounters_of.get(candidate) if encounters_of is not None else None
        scenario = store.scenario(candidate)
        if scenario is None:
            continue
        summary['examined'] += 1
        watched, detail = _watch_units_cached(scenario, candidate)
        if not watched:
            summary['unresolved'].append(dict(candidate=candidate,
                                              encounter=(None if encounter is None else int(encounter)),
                                              reason=detail))
            continue
        aggregate = _candidate_aggregate(store, candidate)
        history, _ = readings(scenario, aggregate, names=watched)
        low = {name: entry for name, entry in history.items()
               if int(entry.get('lowRuns') or 0) > 0 or int(entry.get('zeroRuns') or 0) > 0}
        if not low:
            continue
        crossing = crossing_run(store, candidate, list(low))
        eligible = dict(candidate=candidate,
                        encounter=(None if encounter is None else int(encounter)), watched=list(low),
                        readings=low, crossing=crossing)
        policy, refusal = requested_policy(scenario, allowance, list(low))
        if policy is None:
            eligible['reason'] = refusal
            summary['awaiting'].append(eligible)
            continue
        key = _request_key(candidate, policy)
        if key in state['requests']:
            existing = state['requests'][key]
            existing['lastSeen'] = started
            summary['reused'].append(dict(request=key, child=existing.get('child'),
                                          status=existing.get('status')))
            changed = True
            continue
        if crossing is None:
            eligible['reason'] = ('the threshold was crossed only at or after the run ended, so '
                                  'there is nothing left to correct')
            summary['awaiting'].append(eligible)
            continue
        if not allowance['enabled']:
            eligible['reason'] = 'MP-recovery experiments are switched off'
            summary['awaiting'].append(eligible)
            continue
        if not allowance['effective']:
            eligible['reason'] = ('low MP detected; the herb-supported retry awaits an item '
                                  'allowance (no stock and use cap are configured)')
            summary['awaiting'].append(eligible)
            continue
        if len(state['requests']) >= int(allowance['maxChildren']):
            eligible['reason'] = (f'the configured limit of {allowance["maxChildren"]} '
                                  'MP-recovery children is already used')
            summary['awaiting'].append(eligible)
            continue
        child, existed = _create_child(store, state, candidate, scenario, policy, key, started)
        if child is None:
            eligible['reason'] = 'the child was refused by the library'
            summary['awaiting'].append(eligible)
            continue
        state['requests'][key] = dict(
            key=key, parent=candidate, child=child,
            encounter=(None if encounter is None else int(encounter)),
            watch=list(policy['holyHerbTriggerUnits']), stock=policy['holyHerbStock'],
            maxUses=policy['holyHerbMaxUses'], bank=int(allowance['bank']),
            crossing=crossing, created=started, lastSeen=started, existed=bool(existed),
            status='pending', decision=None)
        state['proposals'] = int(state.get('proposals') or 0) + 1
        changed = True
        (summary['reused'] if existed else summary['created']).append(
            dict(request=key, parent=candidate, child=child,
                 watch=list(policy['holyHerbTriggerUnits'])))
        if changed or summary['timedOut']:
            break
    # Always persisted: the rotation cursor and the short-lived telemetry cache live in the ledger, and
    # a pass that found nothing still has to hand the next pass a different starting point.
    _save(store, state)
    summary['decision'] = _decision(summary, allowance)
    return summary


def _create_child(store, state, parent, scenario, policy, key, started):
    """One herb-enabled child of `parent`, through the same `add_child` path every other child uses."""
    import strategy_optimizer_adapter as adapter
    child_scenario = dict(scenario)
    child_scenario.update(policy)
    child_scenario['inputs'] = []
    note = str(scenario.get('note') or '')
    child_scenario['note'] = f'{note} | MP recovery: herb policy declared'.strip(' |')
    watched = ', '.join(policy['holyHerbTriggerUnits'])
    change = (f'Holy Herb stock {int(scenario.get("holyHerbStock") or 0)}->{policy["holyHerbStock"]}'
              f', max uses {int(scenario.get("holyHerbMaxUses") or 0)}->{policy["holyHerbMaxUses"]}'
              f' (watch {watched})')
    label = f'{LABEL_PREFIX}{change}'[:160]
    proposal = int(state.get('proposals') or 0) + 1
    try:
        child, existed = store.add_child(child_scenario, label,
                                         lambda value=child_scenario: adapter.stats(value),
                                         parent, 'holy-herb', 'mp', change, SOURCE, proposal)
    except Exception as exc:  # noqa: BLE001 - a refused child is reported, never fatal
        state.setdefault('refusals', {})[key] = repr(exc)
        return None, False
    # `add_child` can hand back an id the library then does not hold: the population cap prunes a
    # no-evidence build the moment a sibling is proposed, and pruning deletes the candidate row while
    # deliberately keeping its lineage. A request for a build that is not in the library is not a
    # request, so it is refused explicitly here instead of being reported as created and then sitting
    # at zero measured runs forever.
    if not store.db.execute('SELECT 1 FROM candidate WHERE id=?', (child,)).fetchone():
        state.setdefault('refusals', {})[key] = (f'the library pruned {child[:12]} before it could be '
                                                 'measured (the population cap is full)')
        return None, False
    return child, existed


def _decision(summary, allowance):
    if summary['created']:
        return (f"created {len(summary['created'])} herb-supported correction(s) at stock "
                f"{allowance['stock']} / max {allowance['maxUses']} over a bank of "
                f"{allowance['bank']} runs, compared with each parent on the same fresh seeds")
    if summary['reused']:
        return f"{len(summary['reused'])} existing correction(s) already cover this observation"
    if summary['awaiting']:
        return ('low MP was observed but no correction was created: '
                + (summary['awaiting'][0].get('reason') or 'the allowance is not configured'))
    if summary['unresolved']:
        return ('no role-resolved DPS or healer could be watched for the examined builds: '
                + (summary['unresolved'][0].get('reason') or 'roles unresolved'))
    return 'no low-MP evidence in the examined builds'


def _comparison(store, request):
    """The parent/child readings a request publishes: rewards, verdicts, herb use and MP."""
    parent, child = request.get('parent'), request.get('child')
    bank = int(request.get('bank') or 0)
    observed = _observed(store, child, bank) if child else 0
    out = dict(request=request.get('key'), parentId=parent, childId=child, bank=bank,
               observed=observed, reserved=max(0, bank - observed),
               watch=list(request.get('watch') or []), crossing=request.get('crossing'),
               policy=dict(holyHerbStock=request.get('stock'),
                           holyHerbMaxUses=request.get('maxUses'),
                           holyHerbTriggerUnits=list(request.get('watch') or [])))
    for role, candidate in (('parent', parent), ('child', child)):
        if not candidate:
            continue
        aggregate = _candidate_aggregate(store, candidate)
        chest_count = int(aggregate.get('chestCount') or 0)
        chest_sum = float(aggregate.get('chestSum') or 0.0)
        herb = dict(aggregate.get('herb') or {})
        out[role] = dict(candidate=candidate, attempts=int(aggregate.get('n') or 0),
                         wins=int(aggregate.get('wins') or 0),
                         losses=int(aggregate.get('losses') or 0),
                         unresolved=int(aggregate.get('censored') or 0),
                         earnedSamples=chest_count,
                         meanEarned=(chest_sum / chest_count) if chest_count else None,
                         bestEarned=aggregate.get('earnedMax'),
                         herbRuns=int(herb.get('runs') or 0),
                         herbUses=int(herb.get('useTotal') or 0),
                         herbUseRuns=int(herb.get('useRuns') or 0),
                         mp={name: dict(reading) for name, reading in
                             (aggregate.get('mp') or {}).items()})
    if child and observed >= bank and request.get('status') == 'pending':
        request['status'] = 'complete'
        request['decision'] = (f'the child was measured over its declared bank ({observed}/{bank} '
                               'ordinals); compare its own mean and herb counts with the parent, and '
                               'do not treat a recovered MP pool as an improvement on its own')
    return out


def status(store):
    """The bounded payload the surface renders: the allowance, and one row per request."""
    allowance = setting(store)
    state = ledger(store)
    requests = []
    for _key, request in sorted(state['requests'].items(),
                                key=lambda item: -(item[1].get('created') or 0))[:PUBLISHED_PARENTS]:
        comparison = _comparison(store, request)
        comparison['status'] = request.get('status')
        comparison['decision'] = request.get('decision') or comparison.get('decision')
        requests.append(comparison)
    return dict(label='MP recovery', source=SOURCE, setting=allowance, requests=requests,
                proposals=int(state.get('proposals') or 0),
                refusals=dict(state.get('refusals') or {}), thresholdPercent=THRESHOLD_PERCENT,
                # Published so the surface can show *which* revision the running host loaded. The
                # unbounded first version of the scan starved dispatch on a large library, and the
                # only way to tell from outside whether a host had the bounded one was to measure the
                # coordinator's CPU - so the budget is now part of the payload.
                scanBudgetSeconds=SCAN_SECONDS, scanPerPass=SCAN_PER_PASS,
                awaitingAllowance=bool(not allowance['effective'] and requests))
