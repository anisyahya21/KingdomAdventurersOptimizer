"""Interactive consumable branch for one displayed fight tick (`ka-battle-interaction-1`).

The transport (`api/battle-run.py`) hands this module a *source* scenario and exactly one player command.
The module inserts that command into the scenario's own explicit input schedule and re-runs the unchanged
authoritative pipeline:

    combat_scenario.load_scenario -> combat_replay_export.export_replay
                                  (= combat_setup.prepare_setup -> combat_sandbox.run_scenario)

so the returned `ka-battle-replay-1` payload is the runner's own output for the branched scenario. No combat
behaviour is added here, no event is fabricated, no effect is painted by a client, and nothing persistent is
written (no emulator, no native state, no save file).

What this module owns:

  * command resolution against the recovered item tables: `holy_herb` (Item.txt 31, bonusType 2, MP, all
    residents) or a scenario item row in the battle-reachable all-residents scope (recovered bonusTypes 0/2/4,
    `ExecuteItem 0x167b834` inner table 0x772584 -> `RecoveryResidentsParameter 0x167aea0` at 0x167c2bc).
    Recovered single-resident rows (bonusTypes 1/3/5 -> `RecoveryResidentParameter 0x167acac` at 0x167c36c)
    need an explicit `resident`; native battle item use (OnTouchEvent 0x14f29b8) dispatches with a null single
    target and the own-team array filtered by 0x14f34ac, so they are not reachable in battle and fail closed.
  * the deterministic insertion point: after every existing input of an earlier tick and after existing
    inputs of the same tick/phase, so the pre-command schedule order is preserved exactly;
  * bounded execution: one command per request and one simulation at a time (non-blocking lock -> 429),
    on top of the transport's body/tick/unit limits;
  * the acceptance report read back from the runner's own records (`battle_item` / `resource_change` events,
    `itemUses`, stock counters) and the prefix evidence a client can verify against the replay it displayed.

Nothing in the response is computed by this module: the healing/RNG behaviour is the runner's.
"""
import collections
import hashlib
import itertools
import json
import threading
import time
from copy import deepcopy

from combat_consumables import RECOVERY_ALL_RESIDENTS, RECOVERY_PARAMETERS
from combat_replay_export import export_replay
from combat_sandbox import RunAborted, run_scenario
from combat_scenario import load_scenario

INTERACTION_SCHEMA = 'ka-battle-interaction-1'
POLL_SCHEMA = 'ka-battle-interaction-poll-1'
SCENARIO_SCHEMA = 'ka-special-combat-research-1'
HOLY_HERB = 'holy_herb'
COMMAND_KIND = 'use-item'
PHASES = ('before_fighters', 'after_fighters')
PHASE_ORDER = {name: index for index, name in enumerate(PHASES)}
RECOVERY_CATEGORY = 3
SINGLE_SCOPE_REASON = ('single-resident scope is not reachable in battle: native item use dispatches '
                       'ExecuteItem 0x167b834 with a null single target, so only the all-residents '
                       'recovery branch (0x167aea0) is reachable')
UNSUPPORTED_ROW_REASON = ('the declared row is outside the recovered recovery-item classification '
                          '(bonusCategory 3, bonusType 0..5)')
RESERVED_NAME_REASON = ("the item name 'holy_herb' is reserved for the built-in herb stock")
PREFIX_DIGEST_ALGORITHM = ('sha256 over json.dumps(prefixEvents, sort_keys=True, separators=(",", ":"), '
                           'ensure_ascii=False) with prefixEvents = branch replay events whose tick is '
                           'strictly below commandTick')
PREFIX_VERIFY = ('events are ordered by tick, so branch_replay.events[0..prefix.eventCount) must be '
                 'identical to the displayed replay\'s first prefix.eventCount events')
LIMITS = [
    'One command per request; the branch is still the full scenario re-run through export_replay, replayed from a '
    'bounded engine checkpoint of the same scenario when one is available (indicators.session says whether it was used) '
    '- never a live/interactive session and never a truncated fight.',
    'command.phase is the declared ordering inside the runner frame (before_fighters/after_fighters), not a '
    'recovered native touch-event order within a frame.',
    'Only the runner\'s own battle_item/resource_change events describe the effect; the client must play the '
    'returned replay instead of animating an effect itself.',
    'Per-unit attribution is bound to one dispatch: resource_change records are read between the previous '
    'same-item dispatch at that tick and this command\'s own battle_item record.',
    'Single-resident recovery rows fail closed: no battle path supplies the resident they need.',
]

# One simulation at a time in this interpreter. A second concurrent request is refused instead of queueing.
_RUN_LOCK = threading.Lock()


# ---------------------------------------------------------------------------------------------
# Bounded reusable session (checkpoints) - the latency path for repeated consumable clicks.
#
# A click used to re-simulate the WHOLE fight from tick 0 (`export_replay` of the branch), which
# costs the full run (~12 s on the representative 6-own/21-ally Wairo fight). The engine is
# deterministic and the branch differs from its source only AT the command tick, so an engine state
# captured at the END of an earlier tick, continued with the branch schedule, reproduces the branch
# byte for byte: verified by `check_combat_interaction.check_session_resume`.
#
# The state is captured through the runtime's own seam (`combat_sandbox.run_scenario(checkpoints=)`),
# never by re-implementing a tick, and nothing here changes combat: no effect is invented, no event
# is fabricated, and the resumed payload is asserted equal to the un-resumed one.
#
# Bounds (bounded one at a time, cleaned up): one simulation at a time (`_RUN_LOCK`), at most
# `SESSION_LIMIT` scenarios, at most `SIGNATURE_LIMIT` input schedules each, at most
# `CHECKPOINT_LIMIT` checkpoints per schedule, thinned evenly when a merge would exceed the cap.
#
# A record is stored with the signature of the run's own input schedule at or below its tick, and a
# lookup only accepts a record whose signature equals the target scenario's schedule at that same
# tick (and whose tick is strictly BELOW the command tick - a checkpoint taken at the command tick
# would already contain that tick's dispatch). That is what makes the SAME checkpoint usable by a
# scenario whose later schedule differs only at ticks the checkpoint has not reached yet, e.g. the
# second click of a branch, while a schedule that really differs before the tick can never match.
#
# The record carries the engine state plus the trace up to that tick (`combat_sandbox`
# `take_checkpoint` keeps the trace as a shared snapshot instead of deep-copying every event).
# ---------------------------------------------------------------------------------------------
SESSION_LIMIT = 2
SIGNATURE_LIMIT = 2
CHECKPOINT_LIMIT = 40
SESSION_NOTE = ('checkpoint session: the branch is replayed from the latest engine checkpoint at a '
                'tick strictly below command.tick whose input schedule matches, so the returned '
                'replay is the runner\'s own output for the whole fight, not a truncated one')

# One branch job at a time; a new click supersedes the running one.
JOB_LIMIT = 1
JOB_STRIDE = 400
JOB_WAIT_SECONDS = 5.0
WINDOW_TICKS = 120
WINDOW_TICKS_MAX = 600
WINDOW_NOTE = ('window: the response contains the true engine branch through command.tick plus a short '
               'playback window; the rest of the branch is computed asynchronously and fetched from '
               'ka-battle-interaction-poll-1. A window is not a final battle.')

_SESSIONS = collections.OrderedDict()  # base digest -> OrderedDict(signature -> records by tick)
_LAST_RUN = {}  # diagnostics only; never part of a response payload
_LOCK_OWNER = {'owner': None}  # 'click' | 'job' | None: who holds _RUN_LOCK right now
_JOB_YIELD = threading.Event()  # set by a waiting click: the job drops its stride at the next tick
_JOB_IDS = itertools.count(1)
JOB_BACKOFF = 0.05
JOB_JOIN_SECONDS = 20.0
_JOBS = collections.OrderedDict()  # jobId -> job record (bounded by JOB_LIMIT)


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def scenario_digest(normalized):
    """Stable identity of one normalized scenario (no paths, no timestamps)."""
    return hashlib.sha256(_canonical(normalized).encode('utf-8')).hexdigest()


def scenario_base_digest(normalized):
    """The session key: everything the scenario declares except its input schedule."""
    base = {key: value for key, value in normalized.items() if key != 'inputs'}
    return hashlib.sha256(_canonical(base).encode('utf-8')).hexdigest()


def schedule_signature(inputs, upto_tick):
    """Signature of an input schedule at or below one tick: what a checkpoint at that tick depends on."""
    return hashlib.sha256(_canonical([event for event in inputs if event['tick'] <= upto_tick]).encode('utf-8')).hexdigest()


class _Collector:
    """Bounded checkpoint collector handed to the runtime for one run of one scenario.

    `command_tick` is the tick the run inserts its one command at (None when the run has none).
    Checkpoints strictly below it hold the untouched source state (`keep_after_command` keeps the
    later ones too, which belong to the branch schedule that produced them - the signature they were
    stored with is what makes them reusable, never a bare tick match).
    """

    def __init__(self, scenario, command_tick=None, keep_after_command=False, extra_ticks=()):
        horizon = scenario['tickLimit']
        self.inputs = list(scenario['inputs'])
        self.digest = scenario_base_digest(scenario)
        stride = max(1, horizon // CHECKPOINT_LIMIT)
        wanted = {stride * k for k in range(1, CHECKPOINT_LIMIT)}
        wanted.update(tick for tick in extra_ticks if tick is not None)
        if command_tick is not None and not keep_after_command:
            wanted = {tick for tick in wanted if tick < command_tick}
        self.ticks = {tick for tick in wanted if 0 <= tick < horizon}
        self.records = []

    def wanted(self, tick):
        return tick in self.ticks

    def store(self, record):
        record['signature'] = schedule_signature(self.inputs, record['tick'])
        self.records.append(record)

    def publish(self):
        merge_session(self.digest, self.records)


def merge_session(digest, records):
    """Merge into the bounded store, evicting the least recently used schedule/scenario first."""
    if not records:
        # A run that published nothing (a resumed run whose remaining ticks are past its own
        # command tick) must never wipe what is already cached.
        if digest in _SESSIONS:
            _SESSIONS.move_to_end(digest)
        return
    groups = _SESSIONS.setdefault(digest, collections.OrderedDict())
    for signature in {record['signature'] for record in records}:
        kept = {record['tick']: record for record in groups.get(signature, ())}
        kept.update({record['tick']: record for record in records if record['signature'] == signature})
        merged = [kept[tick] for tick in sorted(kept)]
        if len(merged) > CHECKPOINT_LIMIT:
            step = len(merged) / CHECKPOINT_LIMIT
            merged = [merged[int(index * step)] for index in range(CHECKPOINT_LIMIT)]
        groups[signature] = merged
        groups.move_to_end(signature)
    while len(groups) > SIGNATURE_LIMIT:
        groups.popitem(last=False)
    _SESSIONS.move_to_end(digest)
    while len(_SESSIONS) > SESSION_LIMIT:
        _SESSIONS.popitem(last=False)


def resume_checkpoint(normalized, command_tick):
    """The latest published checkpoint of this scenario strictly below `command_tick`, else None.

    A record is only accepted when the target scenario's own input schedule at or below the record's
    tick has the same signature the record was stored with, so a checkpoint is never reused by a run
    whose schedule differs before that tick. The newest matching schedule is searched first.
    """
    digest = scenario_base_digest(normalized)
    groups = _SESSIONS.get(digest)
    if not groups:
        return None
    _SESSIONS.move_to_end(digest)
    inputs = normalized['inputs']
    for signature, records in reversed(list(groups.items())):
        for record in sorted(records, key=lambda item: item['tick'], reverse=True):
            if record['tick'] >= command_tick:
                continue
            if record['signature'] == schedule_signature(inputs, record['tick']):
                groups.move_to_end(signature)
                return record
    return None


def session_state():
    """Diagnostics (tests only): the bounded store's size, never a scenario payload."""
    return dict(sessions=len(_SESSIONS), sessionLimit=SESSION_LIMIT, checkpointLimit=CHECKPOINT_LIMIT,
                signatures=SIGNATURE_LIMIT,
                checkpoints={digest[:12]: sorted({record['tick'] for records in groups.values()
                                                  for record in records})
                             for digest, groups in _SESSIONS.items()})


def clear_sessions():
    cancel_jobs()
    _SESSIONS.clear()
    _LAST_RUN.clear()


def last_run():
    """Diagnostics (tests only): how the most recent interaction was replayed."""
    return dict(_LAST_RUN)


def prime_session(scenario):
    """Run one scenario envelope while capturing bounded checkpoints (the displayed-fight path).

    Returns the same `ka-battle-replay-1` payload `export_replay` returns - the caller's contract is
    unchanged - or None when another simulation already holds the lock (priming is best-effort).
    """
    if not acquire_run_lock('click', blocking=False):
        return None
    try:
        normalized = load_scenario(scenario)
        collector = _Collector(normalized)
        replay = export_replay(normalized, include_events=True, checkpoints=collector)
        collector.publish()
        return replay
    finally:
        release_run_lock()


def acquire_run_lock(owner, blocking=True, timeout=None):
    """Take the one-simulation lock. Only the branch job ever holds it for a long time.

    A click that finds the lock held by a job sets `_JOB_YIELD`, so the job drops its current stride
    at the next tick and releases; a click that finds another click gets the lock refused, which the
    caller reports as 429 `interaction-busy` (two simulations are never run at once).
    """
    if _RUN_LOCK.acquire(blocking=False):
        _LOCK_OWNER['owner'] = owner
        return True
    if owner == 'click' and _LOCK_OWNER['owner'] == 'job':
        _JOB_YIELD.set()
        try:
            acquired = _RUN_LOCK.acquire(blocking=blocking,
                                         timeout=JOB_WAIT_SECONDS if timeout is None else timeout)
        finally:
            # The job only needs the flag while a click is actually waiting for it.
            _JOB_YIELD.clear()
        if acquired:
            _LOCK_OWNER['owner'] = owner
            return True
    return False


def release_run_lock():
    _LOCK_OWNER['owner'] = None
    _RUN_LOCK.release()


class InteractionError(ValueError):
    """A refused interaction command. `acceptance` is attached when the run already produced one."""

    def __init__(self, code, message, status=422, acceptance=None):
        super().__init__(message)
        self.code = code
        self.status = status
        self.acceptance = acceptance


def _is_int(value):
    return type(value) is int


def _input_item_name(event):
    """The item a schedule entry dispatches, in the naming the trace uses."""
    return HOLY_HERB if event.get('type') == 'holy_herb' else event.get('item')


def consumables(scenario):
    """Support view of one normalized scenario's declared consumables (icons enable/disable)."""
    rows = [dict(name=HOLY_HERB, type='holy_herb', parameter=RECOVERY_PARAMETERS[2], scope='all',
                 supported=True, disabledReason=None,
                 declaredStock=scenario.get('holyHerbStock', 0),
                 # The declared automatic-policy cap travels beside the declared stock: it is
                 # scenario/experiment configuration, never a recovered game quantity.
                 declaredMaxUses=scenario.get('holyHerbMaxUses', 0),
                 triggerUnits=list(scenario.get('holyHerbTriggerUnits') or ()))]
    for name, row in sorted(scenario.get('items', {}).items()):
        bonus_type = row.get('bonusType')
        parameter = RECOVERY_PARAMETERS.get(bonus_type) if _is_int(bonus_type) else None
        all_residents = bonus_type in RECOVERY_ALL_RESIDENTS
        scope = 'all' if all_residents else ('single' if parameter is not None else None)
        supported = bool(all_residents and row.get('bonusCategory') == RECOVERY_CATEGORY)
        rows.append(dict(name=name, type='recovery_item', parameter=parameter, scope=scope,
                         supported=supported,
                         disabledReason=None if supported else (
                             SINGLE_SCOPE_REASON if scope == 'single' else UNSUPPORTED_ROW_REASON),
                         declaredStock=scenario.get('itemStock', {}).get(name, 0)))
    return rows


def resolve_command(command, scenario):
    """Validate one `use-item` command against the raw scenario and return (scheduleEntry, description)."""
    if not isinstance(command, dict):
        raise InteractionError('invalid-request', 'command must be an object', 400)
    if command.get('kind') != COMMAND_KIND:
        raise InteractionError('invalid-request', f"command.kind must be {COMMAND_KIND}", 400)
    tick = command.get('tick')
    if not _is_int(tick) or tick < 0:
        raise InteractionError('invalid-request', 'command.tick must be a nonnegative integer', 400)
    phase = command.get('phase', PHASES[0])
    if phase not in PHASES:
        raise InteractionError('invalid-request', f"command.phase must be one of {list(PHASES)}", 400)
    name = command.get('item')
    if not isinstance(name, str) or not name:
        raise InteractionError('invalid-request', 'command.item must name a declared consumable', 400)

    declared_items = scenario.get('items') or {}
    declared_stock = scenario.get('itemStock') or {}
    if name == HOLY_HERB:
        if name in declared_items:
            raise InteractionError('interaction-unsupported-item', RESERVED_NAME_REASON)
        stock, parameter, scope, item_type = scenario.get('holyHerbStock'), 11, 'all', 'holy_herb'
        entry = dict(tick=tick, type='holy_herb', phase=phase)
    else:
        if name not in declared_items:
            raise InteractionError('interaction-unsupported-item',
                                  f"'{name}' is not declared in scenario.items; only recovered "
                                  f"all-residents recovery rows and the built-in {HOLY_HERB} are accepted")
        row = declared_items[name] or {}
        bonus_type = row.get('bonusType')
        if row.get('bonusCategory') != RECOVERY_CATEGORY or bonus_type not in RECOVERY_PARAMETERS:
            raise InteractionError('interaction-unsupported-item', UNSUPPORTED_ROW_REASON)
        if bonus_type not in RECOVERY_ALL_RESIDENTS:
            raise InteractionError('interaction-unsupported-item', SINGLE_SCOPE_REASON)
        if name not in declared_stock:
            raise InteractionError('interaction-unsupported-item',
                                  f"'{name}' needs an explicit scenario.itemStock entry")
        stock = declared_stock[name]
        parameter, scope, item_type = RECOVERY_PARAMETERS[bonus_type], 'all', 'recovery_item'
        # The authoritative loader requires the explicit all-residents scope for a battle item input.
        entry = dict(tick=tick, type='item', item=name, phase=phase, target='all')

    if not _is_int(stock) or stock < 0:
        raise InteractionError('interaction-unsupported-item',
                              f"'{name}' needs a nonnegative integer stock declaration")
    if name == HOLY_HERB:
        # `holyHerbMaxUses` is the declared cap on the *automatic* `<=3%` policy, not on this
        # prescribed dispatch, so it never blocks a legal command here - but it must still be a
        # consistent declaration, and it can never exceed the stock the policy would spend from.
        max_uses = scenario.get('holyHerbMaxUses', 0)
        if not _is_int(max_uses) or max_uses < 0:
            raise InteractionError('interaction-unsupported-item',
                                   "holyHerbMaxUses needs a nonnegative integer declaration")
        if max_uses > stock:
            raise InteractionError('interaction-unsupported-item',
                                   'holyHerbMaxUses cannot exceed the declared holyHerbStock')
    if stock <= 0:
        raise InteractionError('interaction-no-stock',
                              f"'{name}' declares no stock; a dispatch at tick {tick} is impossible", 409)
    resolved = dict(type=item_type, parameter=parameter, scope=scope, supported=True, declaredStock=stock,
                    declaredMaxUses=(scenario.get('holyHerbMaxUses', 0) if name == HOLY_HERB else 0),
                    triggerUnits=(list(scenario.get('holyHerbTriggerUnits') or ())
                                  if name == HOLY_HERB else []))
    return entry, dict(tick=tick, phase=phase, item=name, resolved=resolved)


def schedule_index(inputs, entry):
    """Position the branch command takes: after every earlier input and after same tick/phase inputs."""
    key = (entry['tick'], PHASE_ORDER[entry['phase']])
    for position, event in enumerate(inputs):
        if (event['tick'], PHASE_ORDER[event['phase']]) > key:
            return position
    return len(inputs)


def insert_command(inputs, entry):
    """Insert in place and return the schedule index; existing entries keep their relative order."""
    index = schedule_index(inputs, entry)
    inputs.insert(index, entry)
    return index


def dispatch_rank(inputs, index, name):
    """How many dispatches of `name` at the same tick precede the command in native dispatch order.

    `combat_sandbox.run_scenario` iterates its input list once per phase, and the frame runs
    before_fighters before after_fighters, so a same-tick dispatch order is (phase, schedule index).
    """
    command = inputs[index]
    key = (PHASE_ORDER[command['phase']], index)
    ahead = 0
    for position, event in enumerate(inputs):
        if position == index or event['tick'] != command['tick']:
            continue
        if _input_item_name(event) != name:
            continue
        if (PHASE_ORDER[event['phase']], position) < key:
            ahead += 1
    return ahead


def prefix_evidence(events, command_tick, displayed_tick=None):
    """Events strictly before the command tick: they are unaffected by the inserted command."""
    prefix = [event for event in events if event['tick'] < command_tick]
    digest = hashlib.sha256(json.dumps(prefix, sort_keys=True, separators=(',', ':'),
                                       ensure_ascii=False).encode('utf-8')).hexdigest()
    return dict(commandTick=command_tick, eventCount=len(prefix),
                lastTick=prefix[-1]['tick'] if prefix else None, digest=f'sha256:{digest}',
                digestAlgorithm=PREFIX_DIGEST_ALGORITHM, verify=PREFIX_VERIFY,
                displayedTick=displayed_tick)


def dispatch_event(replay, name, tick, rank):
    """The runner's own `battle_item` record for this command, in dispatch order at that tick."""
    matches = [event for event in replay['events']
               if event['kind'] == 'battle_item' and event.get('item') == name and event['tick'] == tick]
    return matches[rank] if rank < len(matches) else None


def acceptance_report(replay, event, name, declared_stock, tick):
    """Read the acceptance back from the runner's own record and resource changes; nothing is recomputed.

    The runner emits a dispatch's resource_change records immediately before that dispatch's own
    battle_item record, so a repeated same-item click at the same tick is attributed to its own
    dispatch by bounding the scan between the previous same-item record and this one.
    """
    used = bool(event.get('used'))
    blocked = event.get('blocked')
    target_unit_ids = [unit_id for unit_id in (event.get('targetUnitIds') or []) if unit_id]
    remaining = event.get('remaining')
    earlier = [entry['seq'] for entry in replay['events']
               if entry['kind'] == 'battle_item' and entry.get('item') == name
               and entry['tick'] == tick and entry['seq'] < event['seq']]
    previous_seq = max(earlier) if earlier else None
    changed = [dict(unitId=entry.get('targetUnitId'), parameter=entry.get('parameter'),
                    before=entry.get('before'), after=entry.get('after'), max=entry.get('max'), seq=entry['seq'])
               for entry in replay['events']
               if entry['kind'] == 'resource_change' and entry['tick'] == tick
               and entry.get('sourceItem') == name and entry.get('targetUnitId') in target_unit_ids
               and entry['seq'] < event['seq']
               and (previous_seq is None or entry['seq'] > previous_seq)]
    if blocked:
        reason = f"the runner refused the dispatch: {blocked}"
    elif used:
        reason = 'the runner restored at least one resident and spent one unit of stock'
    else:
        reason = ('the dispatch changed nothing (every eligible resident already had the parameter at or '
                  'above its maximum), so no stock was spent')
    return dict(accepted=used, used=used, blocked=blocked, reason=reason,
                parameter=event.get('parameter'), scope=event.get('scope'), percent=event.get('percent'),
                targetUnitIds=target_unit_ids, changed=changed,
                stock=dict(item=name, declared=declared_stock,
                           after=remaining, before=(remaining + 1 if used and _is_int(remaining) else remaining),
                           spent=used))


def _tick_indicators(replay, command_tick, phase, displayed_tick):
    final = replay['finalState']
    return dict(commandTick=command_tick, phase=phase, displayedTick=displayed_tick,
                source='replay.events[].tick (events are ordered by seq; tick -1 is initialization)',
                replayTicks=replay['ticks'], lastEventTick=max((event['tick'] for event in replay['events']),
                                                              default=None),
                verdictTick=final.get('verdictTick'), finishTick=final.get('finishTick'),
                censored=final.get('censored'), stopReason=final.get('stopReason'))


def _stock_indicator(replay, name, acceptance, source='replay.setupSummary'):
    return dict(item=name, declared=acceptance['stock']['declared'], before=acceptance['stock']['before'],
                after=acceptance['stock']['after'], spent=acceptance['stock']['spent'],
                source=('seeded by replay.setupSummary.holyHerbStock / setupSummary.itemStock, then read from '
                        'the last replay.events[] record with kind "battle_item" and item == name at or before '
                        'the displayed tick (its "remaining" is post-attempt); replay.holyHerbRemaining / '
                        'itemRemaining are end-of-run totals, not the mid-fight value'),
                endOfRun=(replay.get('holyHerbRemaining') if name == HOLY_HERB
                          else (replay.get('itemRemaining') or {}).get(name))) 


def register_job(branch, checkpoint, stop):
    """Start the one bounded branch job for a served window, superseding whatever ran before.

    The job continues the SAME branch from the window-edge checkpoint in strides, so it never repeats
    the ticks the window already simulated. A newer click sets `cancel`; the worker drops its stride
    at the next tick and the newest checkpoint stays the resume point, so no work is lost.
    """
    for old in _JOBS.values():
        old['cancel'].set()
        if old['state'] == 'running':
            old['state'] = 'superseded'
    job = dict(id='branch-%s-%d' % (scenario_base_digest(branch)[:10], next(_JOB_IDS)),
               scenario=deepcopy(branch), horizon=branch['tickLimit'] - 1, checkpoint=checkpoint,
               stopTick=checkpoint['tick'], state='running', error=None, replay=None,
               cancel=threading.Event(), thread=None)
    _JOBS[job['id']] = job
    while len(_JOBS) > JOB_LIMIT:
        _JOBS.popitem(last=False)
    thread = threading.Thread(target=_job_worker, args=(job,), name='ka-branch-job', daemon=True)
    job['thread'] = thread
    thread.start()
    return job


def _job_worker(job):
    """Advance one branch in bounded strides and publish its newest replay for the poll to read."""
    try:
        while not job['cancel'].is_set():
            checkpoint = job['checkpoint']
            if checkpoint['tick'] >= job['horizon']:
                job['replay'] = export_replay(job['scenario'], include_events=True, resume=checkpoint)
                job['state'] = 'ready'
                return
            stop = min(job['horizon'], checkpoint['tick'] + JOB_STRIDE)
            collector = _Collector(job['scenario'], keep_after_command=True, extra_ticks=(stop,))
            if not acquire_run_lock('job'):
                time.sleep(JOB_BACKOFF)
                continue
            try:
                run_scenario(deepcopy(job['scenario']), include_trace=True, resume=checkpoint,
                             stop_tick=stop, checkpoints=collector,
                             abort=lambda world: job['cancel'].is_set() or _JOB_YIELD.is_set())
                if collector.records:
                    # Every record is a complete tick end, so the newest one is always a valid resume
                    # point even when the stride was cut short.
                    job['checkpoint'] = max(collector.records, key=lambda record: record['tick'])
                    collector.publish()
                    job['stopTick'] = job['checkpoint']['tick']
                if not job['cancel'].is_set():
                    done = job['checkpoint']['tick'] >= job['horizon']
                    job['replay'] = export_replay(job['scenario'], include_events=True,
                                                  resume=job['checkpoint'],
                                                  stop_tick=None if done else job['checkpoint']['tick'])
                    if done:
                        job['state'] = 'ready'
                        return
            except RunAborted:
                pass
            finally:
                release_run_lock()
    except Exception as error:  # unexpected: report it, never invent a branch
        job['state'] = 'failed'
        job['error'] = '%s: %s' % (type(error).__name__, error)


def cancel_jobs():
    """Cancel every branch job and wait for its worker to leave the run lock."""
    for job in list(_JOBS.values()):
        job['cancel'].set()
    for job in list(_JOBS.values()):
        thread = job.get('thread')
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=JOB_JOIN_SECONDS)
    _JOBS.clear()


def job_state():
    """Diagnostics (tests only): the bounded job store, never a scenario payload."""
    return dict(jobLimit=JOB_LIMIT, stride=JOB_STRIDE, windowTicks=WINDOW_TICKS,
                jobs=[dict(id=job['id'], state=job['state'], stopTick=job['stopTick'],
                           horizon=job['horizon'], error=job['error'],
                           events=None if job['replay'] is None else len(job['replay']['events']))
                      for job in _JOBS.values()])


def poll_branch(payload):
    """One poll of a branch job: the events at or after `fromTick` plus the branch's current state.

    The job's own replay is the only source: nothing is recomputed, trimmed or re-timed here, and a
    poll never runs a simulation. `prefixEventCount` is the job's own count of events strictly below
    `fromTick`, which is what the client compares against its displayed replay before splicing.
    """
    job_id = payload.get('jobId')
    if not isinstance(job_id, str) or not job_id:
        raise InteractionError('invalid-request', f'{POLL_SCHEMA} requires the jobId of a branch job', 400)
    job = _JOBS.get(job_id)
    if job is None:
        raise InteractionError('interaction-job-unknown',
                              'no branch job with that id is retained: it was superseded by a newer '
                              'click or the session was cleared', 404)
    from_tick = payload.get('fromTick')
    if from_tick is not None and (not _is_int(from_tick) or from_tick < 0):
        raise InteractionError('invalid-request', 'fromTick must be a nonnegative integer', 400)
    replay = job['replay']
    if replay is None:
        raise InteractionError('interaction-job-pending',
                              'the branch job has not finished its first stride yet; retry', 409)
    final = replay['finalState']
    events = replay['events']
    delta = [event for event in events if from_tick is None or event['tick'] >= from_tick]
    ready = job['state'] == 'ready'
    return dict(schema=POLL_SCHEMA, jobId=job_id, state=job['state'], fromTick=from_tick,
                prefixEventCount=len(events) - len(delta),
                window=dict(stopTick=final.get('windowStopTick'),
                            horizonTick=final.get('windowHorizonTick'),
                            remainingTicks=final.get('windowRemainingTicks'), complete=ready),
                replay=dict(replay, events=delta),
                limits=list(LIMITS) + [WINDOW_NOTE])


def run_interaction(payload):
    """Validate, branch, run and report one interactive consumable command.

    With `windowTicks` the branch is only simulated through `command.tick + windowTicks` (a live
    window, `replay.finalState.windowed`) and the rest of the branch is handed to one bounded branch
    job the client fetches from `ka-battle-interaction-poll-1`. Without it the response is the
    unchanged full branch.
    """
    scenario = payload.get('scenario')
    if not isinstance(scenario, dict):
        raise InteractionError('invalid-request', f'{INTERACTION_SCHEMA} requires a nested scenario object', 400)
    entry, description = resolve_command(payload.get('command'), scenario)

    expected_prefix = payload.get('expectedPrefixEventCount')
    if expected_prefix is not None and (not _is_int(expected_prefix) or expected_prefix < 0):
        raise InteractionError('invalid-request', 'expectedPrefixEventCount must be a nonnegative integer', 400)
    displayed_tick = payload.get('displayedTick')
    if displayed_tick is not None and not _is_int(displayed_tick):
        raise InteractionError('invalid-request', 'displayedTick must be an integer', 400)
    window_ticks = payload.get('windowTicks')
    if window_ticks is not None and (not _is_int(window_ticks) or not 1 <= window_ticks <= WINDOW_TICKS_MAX):
        raise InteractionError('invalid-request',
                              f'windowTicks must be an integer in 1..{WINDOW_TICKS_MAX}', 400)

    if not acquire_run_lock('click'):
        raise InteractionError('interaction-busy',
                              'another interaction simulation is already running; retry after it finishes', 429)
    try:
        normalized = load_scenario(scenario)  # ScenarioError -> the transport answers 422 scenario-rejected
        if entry['tick'] > normalized['tickLimit']:
            raise InteractionError('interaction-timing',
                                  f"tick {entry['tick']} is beyond the declared tickLimit "
                                  f"{normalized['tickLimit']}, so no frame would dispatch it")
        branch = deepcopy(normalized)
        branch['inputs'] = list(normalized['inputs'])
        index = insert_command(branch['inputs'], entry)
        horizon = branch['tickLimit'] - 1
        stop = horizon if window_ticks is None else min(horizon, entry['tick'] + window_ticks)
        # The window edge is captured as a checkpoint too, so the branch job starts from exactly the
        # tick the window stopped on instead of replaying it.
        collector = _Collector(branch, command_tick=entry['tick'],
                               keep_after_command=window_ticks is not None,
                               extra_ticks=(entry['tick'], stop))
        resume = resume_checkpoint(normalized, entry['tick'])
        replay = export_replay(branch, include_events=True, checkpoints=collector, resume=resume,
                               stop_tick=stop)
        collector.publish()
        # Diagnostics only (tests/telemetry). The response payload stays byte-identical for identical
        # requests, so a cache hit is never visible in the runner's own contract.
        _LAST_RUN.clear()
        _LAST_RUN.update(dict(resumed=resume is not None,
                              checkpointTick=None if resume is None else resume['tick'],
                              checkpointsPublished=len(collector.records),
                              windowTicks=window_ticks,
                              windowStopTick=stop if stop < horizon else None,
                              note=SESSION_NOTE))
        job = (register_job(branch, collector.records[-1], stop)
               if stop < horizon and collector.records else None)
    finally:
        release_run_lock()

    prefix = prefix_evidence(replay['events'], entry['tick'], displayed_tick)
    if expected_prefix is not None and expected_prefix > prefix['eventCount']:
        raise InteractionError('interaction-prefix-mismatch',
                              f'the branch has {prefix["eventCount"]} events before tick {entry["tick"]}, '
                              f'fewer than the supplied expectedPrefixEventCount {expected_prefix}', 409)

    event = dispatch_event(replay, description['item'], entry['tick'],
                           dispatch_rank(branch['inputs'], index, description['item']))
    if event is None:
        raise InteractionError('interaction-timing',
                              f'the fight resolved before tick {entry["tick"]}, so the command was never '
                              f'dispatched (stopReason: {replay["finalState"]["stopReason"]})')

    acceptance = acceptance_report(replay, event, description['item'],
                                  description['resolved']['declaredStock'], entry['tick'])
    if acceptance['blocked']:
        raise InteractionError('interaction-no-stock',
                              f"the runner refused the dispatch at tick {entry['tick']}: "
                              f"{acceptance['blocked']} (the declared stock was already spent)", 409,
                              acceptance=acceptance)

    command = dict(description, scheduleIndex=index, scheduledInputs=list(branch['inputs']))
    result = dict(schema=INTERACTION_SCHEMA, status='accepted' if acceptance['used'] else 'no-effect',
                  command=command, acceptance=acceptance, prefix=prefix,
                  indicators=dict(tick=_tick_indicators(replay, entry['tick'], entry['phase'], displayed_tick),
                                  stock=_stock_indicator(replay, description['item'], acceptance)),
                  consumables=consumables(normalized), scenario=branch, replay=replay, limits=list(LIMITS))
    final = replay['finalState']
    if final.get('windowed'):
        # The branch continues past this window. The response says so explicitly (and the replay
        # itself carries finalState.windowed), so no client can read the window as a finished fight.
        result['window'] = dict(stopTick=final['windowStopTick'], horizonTick=final['windowHorizonTick'],
                                remainingTicks=final['windowRemainingTicks'],
                                jobId=None if job is None else job['id'], state='running' if job else None,
                                complete=False, note=WINDOW_NOTE)
        result['limits'] = list(LIMITS) + [WINDOW_NOTE]
    return result
