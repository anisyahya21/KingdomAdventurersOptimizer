"""Interactive consumable branch: acceptance, stock and prefix preservation (`ka-battle-interaction-1`).

The contract under test is `combat_interaction.run_interaction` plus its transport wiring:

  * one command is inserted into the scenario's own input schedule at the clicked tick, after every
    earlier input and after same tick/phase inputs, and the whole scenario is re-run through the
    unchanged pipeline (`load_scenario` -> `export_replay`);
  * every event before the command tick is the original run's own event (prefix preserved), while the
    run from that tick on is the runner's new output (the fight state changed);
  * the acceptance/stock report is read back from the runner's own `battle_item` / `resource_change`
    records - a used command spends stock, a no-effect command does not;
  * unsupported rows fail closed (single-resident recovery rows, undeclared items, reserved names),
    impossible timing and impossible stock are refused, and a second concurrent simulation is refused
    instead of queueing;
  * the deployed transport advertises and accepts the envelope, and the two copies of the helper module
    (recovery workspace / packaged runtime) are identical.

Usage: python check_combat_interaction.py
"""
import importlib.util
import json
import pathlib
import sys
import time

from combat_consumables import RECOVERY_ALL_RESIDENTS
from combat_interaction import (CHECKPOINT_LIMIT, HOLY_HERB, INTERACTION_SCHEMA, InteractionError,
                                JOB_STRIDE, LIMITS, POLL_SCHEMA, SESSION_LIMIT, SIGNATURE_LIMIT,
                                WINDOW_TICKS, _RUN_LOCK, acquire_run_lock, clear_sessions,
                                consumables, insert_command, release_run_lock,
                                job_state, last_run, poll_branch, prime_session, run_interaction,
                                session_state)
from combat_parameters import HUMAN_TRAINING_PARAMETERS
from combat_replay_export import export_replay

TOOLS = pathlib.Path(__file__).resolve().parent
WORKSPACE = TOOLS.parents[2]
API = WORKSPACE / 'KA-Website/artifacts/kingdom-adventures/api'
DEPLOYED = API / '_battle_runtime'
ENDING_FIXTURE = WORKSPACE / 'RE-evidence/20260920-battle-regression-pass/win.scenario.json'
SKILLS = [26, 110, 25, 24, 23, 22]
HERB_TICK = 54
PRESCHEDULED_TICK = 20  # after the first ally MP spend (tick 14), so the herb there is really used
EMPTY_TICK = 5          # before any MP spend, so every resident is still full
LARGE_POTION = 'Recovery Potion (L)'
SMALL_POTION = 'Recovery Potion (S)'
RANDOM_POTION = 'Random Potion'


def human(name, mp=1000):
    stats = {10: 5000, 11: mp, 13: 300, 14: 3000, 15: 220, 16: 110, 19: 0}
    return dict(name=name, human=True, monsterId=None, weaponId=0, equipment=[], visitor=False,
                leaderIdentity=False, skills=SKILLS, invocationLevels=[1] * len(SKILLS),
                parameters={p: dict(rawValue=stats.get(p, 1),
                                    rawMax=stats.get(p, 1) if p in (10, 11) else 2147483647,
                                    extraValue=0, extraMax=0, trainingLevel=123)
                            for p in HUMAN_TRAINING_PARAMETERS})


def items():
    return {LARGE_POTION: dict(bonusCategory=3, bonusType=0, bonusMinValue=50, bonusMaxValue=50),
            SMALL_POTION: dict(bonusCategory=3, bonusType=1, bonusMinValue=50, bonusMaxValue=50),
            RANDOM_POTION: dict(bonusCategory=3, bonusType=0, bonusMinValue=10, bonusMaxValue=90)}


def scenario(inputs, stock=1, item_stock=None, tick_limit=90):
    return dict(schema='ka-special-combat-research-1', encounterId=19, defeatCount=0, mathSeed=7,
                libSeed=8, tickLimit=tick_limit, ownUnits=[human(f'f{i}') for i in range(3)],
                holyHerbStock=stock, items=items(),
                itemStock=item_stock if item_stock is not None else {LARGE_POTION: 2, SMALL_POTION: 1,
                                                                     RANDOM_POTION: 1},
                inputs=list(inputs), note='Synthetic interaction probe; not a player build.')


def herb(tick, phase='before_fighters'):
    return dict(tick=tick, type='holy_herb', phase=phase)


def body(source, item=HOLY_HERB, tick=HERB_TICK, phase='before_fighters', **extra):
    request = dict(schema=INTERACTION_SCHEMA, scenario=source,
                   command=dict(kind='use-item', tick=tick, phase=phase, item=item))
    request.update(extra)
    return request


def events_before(replay, tick):
    return [event for event in replay['events'] if event['tick'] < tick]


def refuses(request, code, status):
    try:
        run_interaction(request)
    except InteractionError as error:
        assert error.code == code, (error.code, error)
        assert error.status == status, (error.status, error)
        return error
    raise AssertionError(f'{code} was accepted')


def refuses_poll(request, code, status):
    try:
        poll_branch(request)
    except InteractionError as error:
        assert (error.code, error.status) == (code, status), (error.code, error.status, error)
        return error
    raise AssertionError(f'{code} was accepted')


def check_schedule_insertion():
    """Insertion keeps every existing entry's order and lands after same tick/phase entries."""
    inputs = [herb(10), herb(PRESCHEDULED_TICK), dict(tick=HERB_TICK, type='item', item=LARGE_POTION,
                                                      phase='after_fighters', target='all'),
              herb(HERB_TICK)]
    branch = list(inputs)
    index = insert_command(branch, herb(HERB_TICK, 'after_fighters'))
    assert index == 4, index
    assert branch[:4] == inputs, branch
    assert branch[4] == herb(HERB_TICK, 'after_fighters'), branch
    earlier = list(inputs)
    assert insert_command(earlier, herb(5)) == 0, earlier
    assert earlier[0] == herb(5) and earlier[1:] == inputs, earlier
    middle = list(inputs)
    assert insert_command(middle, herb(15)) == 1, middle
    assert middle[1] == herb(15) and middle[:1] == inputs[:1] and middle[2:] == inputs[1:], middle


def check_acceptance_and_prefix():
    """A used command changes the fight state from its tick on and leaves the prefix untouched."""
    source = scenario([herb(PRESCHEDULED_TICK)], stock=2)
    original = export_replay(source)
    response = run_interaction(body(source))
    replay = response['replay']

    assert response['schema'] == INTERACTION_SCHEMA and response['status'] == 'accepted', response['status']
    assert sorted(response) == ['acceptance', 'command', 'consumables', 'indicators', 'limits', 'prefix',
                                'replay', 'scenario', 'schema', 'status'], sorted(response)
    command = response['command']
    assert command['item'] == HOLY_HERB and command['tick'] == HERB_TICK, command
    assert command['resolved'] == dict(type='holy_herb', parameter=11, scope='all', supported=True,
                                       declaredStock=2, declaredMaxUses=0,
                                       triggerUnits=[]), command['resolved']
    assert command['scheduleIndex'] == 1, command['scheduleIndex']
    assert command['scheduledInputs'] == [herb(PRESCHEDULED_TICK), herb(HERB_TICK)], command['scheduledInputs']
    assert replay['setupSummary']['inputs'] == command['scheduledInputs'], replay['setupSummary']['inputs']
    assert response['scenario']['inputs'] == command['scheduledInputs'], response['scenario']['inputs']

    # prefix: the command tick and everything before it is the original run's own event stream
    prefix = response['prefix']
    assert prefix['commandTick'] == HERB_TICK and prefix['lastTick'] == HERB_TICK - 1, prefix
    assert prefix['eventCount'] == len(events_before(original, HERB_TICK)), prefix
    assert replay['events'][:prefix['eventCount']] == original['events'][:prefix['eventCount']]
    assert events_before(replay, HERB_TICK) == events_before(original, HERB_TICK)
    assert replay['events'][prefix['eventCount']]['tick'] == HERB_TICK, replay['events'][prefix['eventCount']]
    assert prefix['digest'].startswith('sha256:') and len(prefix['digest']) == 71, prefix
    assert prefix['displayedTick'] is None, prefix

    # the fight state from the command tick on is the runner's own new output
    suffix_changed = [event for event in replay['events'] if event['tick'] >= HERB_TICK] != \
                     [event for event in original['events'] if event['tick'] >= HERB_TICK]
    assert suffix_changed, 'the branch replay is identical after the command tick'
    final = {unit['unitId']: (unit['hp'], unit['mp']) for unit in replay['finalState']['units']}
    before = {unit['unitId']: (unit['hp'], unit['mp']) for unit in original['finalState']['units']}
    assert final != before, 'the final state did not change'
    assert [unit_id for unit_id in final if final[unit_id][1] != before[unit_id][1]], (final, before)

    # acceptance/stock read back from the runner's own records
    acceptance = response['acceptance']
    assert acceptance['accepted'] is True and acceptance['used'] is True and acceptance['blocked'] is None
    assert acceptance['parameter'] == 11 and acceptance['scope'] == 'all' and acceptance['percent'] == 100
    assert acceptance['stock'] == dict(item=HOLY_HERB, declared=2, before=1, after=0, spent=True), acceptance
    assert acceptance['reason'].startswith('the runner restored'), acceptance['reason']
    records = [event for event in replay['events']
               if event['kind'] == 'battle_item' and event['item'] == HOLY_HERB]
    assert [record['tick'] for record in records] == [PRESCHEDULED_TICK, HERB_TICK], records
    assert [record['remaining'] for record in records] == [1, 0], records
    assert acceptance['targetUnitIds'] == records[1]['targetUnitIds'], acceptance
    assert all(unit_id.startswith('ally:') for unit_id in acceptance['targetUnitIds']), acceptance
    assert acceptance['changed'], acceptance
    assert all(entry['parameter'] == 11 and entry['after'] >= entry['before'] for entry in acceptance['changed'])
    assert {entry['unitId'] for entry in acceptance['changed']} <= set(acceptance['targetUnitIds'])
    for entry in acceptance['changed']:
        source_event = next(row for row in replay['events'] if row['seq'] == entry['seq'])
        assert source_event['kind'] == 'resource_change' and source_event['sourceItem'] == HOLY_HERB
        assert (source_event['before'], source_event['after'], source_event['max']) == \
               (entry['before'], entry['after'], entry['max'])
        assert source_event['tick'] == HERB_TICK, source_event
    assert replay['holyHerbRemaining'] == 0

    # indicators name the sources the UI must read
    tick_indicator = response['indicators']['tick']
    assert tick_indicator['commandTick'] == HERB_TICK and tick_indicator['phase'] == 'before_fighters'
    assert tick_indicator['replayTicks'] == replay['ticks']
    assert tick_indicator['lastEventTick'] == max(event['tick'] for event in replay['events'])
    assert 'replay.events[].tick' in tick_indicator['source'], tick_indicator['source']
    stock_indicator = response['indicators']['stock']
    assert stock_indicator['item'] == HOLY_HERB and stock_indicator['declared'] == 2
    assert (stock_indicator['before'], stock_indicator['after'], stock_indicator['spent']) == (1, 0, True)
    assert 'battle_item' in stock_indicator['source'], stock_indicator['source']
    assert stock_indicator['endOfRun'] == 0

    # deterministic: the same request produces the same branch, byte for byte
    again = run_interaction(body(source))
    assert json.dumps(again, sort_keys=True) == json.dumps(response, sort_keys=True)

    # the displayed-tick guard: branching from a longer fight is refused
    refuses(body(source, expectedPrefixEventCount=prefix['eventCount'] + 1), 'interaction-prefix-mismatch', 409)
    ok = run_interaction(body(source, expectedPrefixEventCount=prefix['eventCount'],
                              displayedTick=HERB_TICK))
    assert ok['prefix']['displayedTick'] == HERB_TICK, ok['prefix']
    return response


def check_generic_item_and_capabilities():
    """A declared all-residents row heals HP; the single-resident row is reported as unsupported."""
    source = scenario([])
    response = run_interaction(body(source, item=LARGE_POTION, tick=HERB_TICK))
    assert response['status'] == 'accepted', response['status']
    acceptance = response['acceptance']
    assert acceptance['parameter'] == 10 and acceptance['scope'] == 'all' and acceptance['percent'] == 50
    assert acceptance['stock'] == dict(item=LARGE_POTION, declared=2, before=2, after=1, spent=True), acceptance
    assert len(acceptance['changed']) == 3, acceptance['changed']
    assert all(entry['parameter'] == 10 for entry in acceptance['changed']), acceptance['changed']
    assert response['replay']['itemRemaining'] == {LARGE_POTION: 1, SMALL_POTION: 1, RANDOM_POTION: 1}
    assert 'holy_herb' not in response['replay']['itemRemaining']

    # an RNG-drawing row (min < max) is still a deterministic branch with an unchanged prefix
    original = export_replay(source)
    random_response = run_interaction(body(source, item=RANDOM_POTION, tick=HERB_TICK))
    assert random_response['acceptance']['percent'] is not None
    assert 10 <= random_response['acceptance']['percent'] <= 90, random_response['acceptance']['percent']
    prefix = random_response['prefix']['eventCount']
    assert random_response['replay']['events'][:prefix] == original['events'][:prefix]
    assert random_response['replay']['events'][prefix:] != original['events'][prefix:]
    assert random_response['acceptance']['changed'], random_response['acceptance']

    rows = {row['name']: row for row in consumables(source)}
    assert rows[HOLY_HERB]['supported'] is True and rows[HOLY_HERB]['scope'] == 'all'
    assert rows[LARGE_POTION]['supported'] is True and rows[LARGE_POTION]['parameter'] == 10
    assert rows[SMALL_POTION]['supported'] is False and rows[SMALL_POTION]['scope'] == 'single'
    assert 'null single target' in rows[SMALL_POTION]['disabledReason'], rows[SMALL_POTION]
    assert rows[SMALL_POTION]['declaredStock'] == 1 and rows[SMALL_POTION]['parameter'] == 10
    assert {row['parameter'] for row in consumables(source) if row['supported']} == {10, 11}
    assert RECOVERY_ALL_RESIDENTS == (0, 2, 4)


def check_repeated_same_tick_click():
    """A repeated same-item click at the same tick is attributed to its own dispatch only."""
    # the prescheduled dispatch heals first; the clicked branch command is then the no-effect one
    source = scenario([herb(HERB_TICK)], stock=3)
    response = run_interaction(body(source, tick=HERB_TICK))
    assert response['status'] == 'no-effect', response['status']
    acceptance = response['acceptance']
    assert acceptance['used'] is False and acceptance['blocked'] is None, acceptance
    assert acceptance['changed'] == [], acceptance['changed']
    assert acceptance['stock'] == dict(item=HOLY_HERB, declared=3, before=2, after=2, spent=False), acceptance
    records = [event for event in response['replay']['events']
               if event['kind'] == 'battle_item' and event['item'] == HOLY_HERB
               and event['tick'] == HERB_TICK]
    assert [record['used'] for record in records] == [True, False], records
    changes = [event for event in response['replay']['events']
               if event['kind'] == 'resource_change' and event['tick'] == HERB_TICK
               and event['sourceItem'] == HOLY_HERB]
    assert len(changes) == 2 and all(event['seq'] < records[0]['seq'] for event in changes), changes

    # mirrored: the clicked command dispatches first (before_fighters) and owns the changes
    source = scenario([herb(HERB_TICK, phase='after_fighters')], stock=3)
    response = run_interaction(body(source, tick=HERB_TICK))
    acceptance = response['acceptance']
    assert acceptance['used'] is True and len(acceptance['changed']) == 2, acceptance
    records = [event for event in response['replay']['events']
               if event['kind'] == 'battle_item' and event['item'] == HOLY_HERB
               and event['tick'] == HERB_TICK]
    assert [record['used'] for record in records] == [True, False], records
    assert all(entry['seq'] < records[0]['seq'] for entry in acceptance['changed']), acceptance['changed']


def check_no_effect_and_refusals():
    """Full targets spend nothing, and every impossible request is refused, not simulated."""
    source = scenario([])
    no_effect = run_interaction(body(source, tick=EMPTY_TICK))
    assert no_effect['status'] == 'no-effect', no_effect['status']
    assert no_effect['acceptance']['used'] is False and no_effect['acceptance']['blocked'] is None
    assert no_effect['acceptance']['changed'] == []
    assert no_effect['acceptance']['stock'] == dict(item=HOLY_HERB, declared=1, before=1, after=1, spent=False)
    assert 'no stock was spent' in no_effect['acceptance']['reason'], no_effect['acceptance']['reason']
    record = next(event for event in no_effect['replay']['events'] if event['kind'] == 'battle_item')
    assert record['used'] is False and record['remaining'] == 1 and record['tick'] == EMPTY_TICK
    # a no-effect dispatch is reported, not simulated: no other event kind changed, the RNG stream is
    # untouched (the herb is a fixed 100 with no draw) and the final unit state is the source run's
    source_run = export_replay(source)
    def shape(events):
        return [(event['tick'], event['kind']) for event in events if event['kind'] != 'battle_item']
    assert shape(no_effect['replay']['events']) == shape(source_run['events'])
    assert no_effect['replay']['finalState']['units'] == source_run['finalState']['units']
    assert no_effect['replay']['finalState']['rngFinalState'] == source_run['finalState']['rngFinalState']
    assert [event for event in source_run['events'] if event['tick'] == EMPTY_TICK] == \
           [event for event in no_effect['replay']['events'] if event['tick'] == EMPTY_TICK
            and event['kind'] != 'battle_item']

    refuses(body(source, item=SMALL_POTION, tick=HERB_TICK), 'interaction-unsupported-item', 422)
    refuses(body(source, item='Not Declared', tick=HERB_TICK), 'interaction-unsupported-item', 422)
    refuses(body(dict(source, items={HOLY_HERB: dict(bonusCategory=3, bonusType=2, bonusMinValue=100,
                                                     bonusMaxValue=100)}, itemStock={HOLY_HERB: 1}),
                 item=HOLY_HERB, tick=HERB_TICK), 'interaction-unsupported-item', 422)
    refuses(body(scenario([], stock=0)), 'interaction-no-stock', 409)
    refuses(body(dict(source, itemStock={LARGE_POTION: 0}), item=LARGE_POTION, tick=HERB_TICK),
            'interaction-no-stock', 409)
    refuses(body(source, tick=1000), 'interaction-timing', 422)
    refuses(body(source, tick=HERB_TICK, phase='whenever'), 'invalid-request', 400)
    refuses(body(source, tick=-1), 'invalid-request', 400)
    refuses(body(source, tick=1.5), 'invalid-request', 400)
    refuses(dict(schema=INTERACTION_SCHEMA, command=dict(kind='use-item', tick=1, item=HOLY_HERB)),
            'invalid-request', 400)
    refuses(dict(schema=INTERACTION_SCHEMA, scenario=source,
                 command=dict(kind='dispel', tick=1, item=HOLY_HERB)), 'invalid-request', 400)

    # an already-spent stock is reported with the runner's own acceptance, not silently re-run
    spent = scenario([herb(PRESCHEDULED_TICK)], stock=1)
    blocked = refuses(body(spent, tick=HERB_TICK), 'interaction-no-stock', 409)
    assert blocked.acceptance['blocked'] == 'no stock' and blocked.acceptance['used'] is False
    assert blocked.acceptance['stock'] == dict(item=HOLY_HERB, declared=1, before=0, after=0, spent=False)
    assert blocked.acceptance['changed'] == []
    assert 'refused the dispatch' in blocked.acceptance['reason'], blocked.acceptance['reason']

    # single-flight: while one simulation holds the lock another request is refused, never queued
    assert _RUN_LOCK.acquire(blocking=False)
    try:
        refuses(body(source, tick=HERB_TICK), 'interaction-busy', 429)
    finally:
        _RUN_LOCK.release()
    assert run_interaction(body(source, tick=HERB_TICK))['status'] == 'accepted'


def real_ending_source():
    """The matched native Ending fixture with a declared consumable (a scenario input, not a game fact)."""
    data = json.loads(ENDING_FIXTURE.read_text(encoding='utf-8'))
    data['holyHerbStock'] = 2
    return data


def check_real_ending_cut_and_branch_prefix():
    """The real Ending fixture: the cut is the playback end, and the item branch keeps the prefix.

    Pins the deployed bug directly: `finalState.ticks` is a declared horizon (4000) while the fight
    ends at the Ending entry, so a player that ignores `endingCutTick` plays a silent tail the engine
    never simulated - and, before the package was rebuilt, played it all the way to tick 6999-style
    horizons because the cut was missing from the payload.
    """
    source = real_ending_source()
    clear_sessions()
    base = export_replay(source)
    final = base['finalState']
    simulated = [event['tick'] for event in base['events'] if event.get('phase') != 'finish']
    leaving = [event['tick'] for event in base['events']
               if event['kind'] == 'state' and event['new'] == 8]
    kos = [event['tick'] for event in base['events']
           if event['kind'] == 'attack' and event['hpAfter'] == 0]
    assert final['endingCutTick'] == max(simulated) < final['ticks'], final
    assert final['horizonTick'] == final['ticks'] - 1
    assert final['endingCutTick'] > final['endingTick'] >= max(leaving), final
    assert final['postEndingTicks'] == final['endingCutTick'] - final['endingTick'] > 0
    assert final['silentTailTicks'] == final['horizonTick'] - final['endingCutTick'] > 0
    assert final['finishPhaseEvents'] > 0 and final['finishPhaseFirstTick'] > final['endingCutTick']
    # Never "last HP 0": the verdict follows the losing team's Leaving entries, which follow the KOs.
    assert final['endingTick'] > max(kos), (final, max(kos))
    assert final['endingCutTick'] > max(kos)

    # The item branch is the runner's own re-run: same cut fields, byte-identical prefix.
    command_tick = 60  # after the fixture's first ally MP spend (tick 16), so the herb is used
    response = run_interaction(body(source, tick=command_tick))
    assert response['status'] == 'accepted', response['acceptance']
    replay = response['replay']
    prefix = response['prefix']['eventCount']
    assert prefix == len(events_before(base, command_tick))
    assert replay['events'][:prefix] == base['events'][:prefix]
    assert replay['events'][prefix]['tick'] == command_tick
    assert replay['events'][prefix:] != base['events'][prefix:]
    branch_final = replay['finalState']
    assert branch_final['endingCutTick'] == max(
        event['tick'] for event in replay['events'] if event.get('phase') != 'finish')
    assert branch_final['horizonTick'] == replay['ticks'] - 1
    assert branch_final['endingTick'] is not None and branch_final['silentTailTicks'] >= 0

    # A later click on the same fixture replays from a bounded checkpoint (past the first stride) and
    # must be the identical payload - receipts/prize ledger included, not just the event stream.
    later = 400
    clear_sessions()
    cold_later = run_interaction(body(source, tick=later))
    assert last_run()['resumed'] is False
    prime_session(source)
    warm_later = run_interaction(body(source, tick=later))
    assert last_run()['resumed'] is True, last_run()
    assert json.dumps(warm_later, sort_keys=True) == json.dumps(cold_later, sort_keys=True)
    return response


def check_session_resume():
    """A click replayed from a bounded checkpoint is byte-identical to the un-resumed branch."""
    source = scenario([herb(PRESCHEDULED_TICK)], stock=3, tick_limit=900)
    ticks = (500, 300)
    # The pre-optimisation code path: the same request with no session available at all.
    clear_sessions()
    expected = {tick: run_unresumed(body(source, tick=tick)) for tick in ticks}

    clear_sessions()
    cold = run_interaction(body(source, tick=500))
    assert last_run()['resumed'] is False, last_run()
    assert json.dumps(cold, sort_keys=True) == json.dumps(expected[500], sort_keys=True)
    state = session_state()
    assert state['sessions'] == 1 and len(state['checkpoints']) == 1, state
    published = list(state['checkpoints'].values())[0]
    assert published == sorted(published) and len(published) <= CHECKPOINT_LIMIT, published
    assert published and max(published) < 900, published

    for tick in ticks:
        warm = run_interaction(body(source, tick=tick))
        used = last_run()
        assert used['resumed'] is True and used['checkpointTick'] < tick, used
        assert json.dumps(warm, sort_keys=True) == json.dumps(expected[tick], sort_keys=True), tick

    # bounded and cleaned up: only SESSION_LIMIT scenarios stay; clear_sessions drops everything
    foreign = [scenario([herb(PRESCHEDULED_TICK)], stock=2, tick_limit=300 + index * 10)
               for index in range(SESSION_LIMIT + 1)]
    for index, other in enumerate(foreign):
        run_interaction(body(other, tick=HERB_TICK))
        assert session_state()['sessions'] <= SESSION_LIMIT, session_state()
    assert len(session_state()['checkpoints']) == SESSION_LIMIT, session_state()
    clear_sessions()
    assert session_state() == dict(sessions=0, sessionLimit=SESSION_LIMIT,
                                   checkpointLimit=CHECKPOINT_LIMIT, signatures=SIGNATURE_LIMIT,
                                   checkpoints={}), session_state()
    return published


def run_unresumed(request):
    """The same request with the session disabled (the pre-optimisation code path)."""
    clear_sessions()
    try:
        return run_interaction(request)
    finally:
        clear_sessions()


def check_window_and_branch_job():
    """`windowTicks` answers with the true branch prefix plus one bounded job; the job's branch is
    byte-identical to the synchronous full branch, and a window is never presented as a final fight."""
    source = scenario([herb(PRESCHEDULED_TICK)], stock=3, tick_limit=900)
    tick, window = 500, 40
    clear_sessions()
    full = run_interaction(body(source, tick=tick))
    clear_sessions()
    short = run_interaction(body(source, tick=tick, windowTicks=window))
    final = short['replay']['finalState']
    assert last_run()['windowTicks'] == window and last_run()['windowStopTick'] == tick + window, last_run()
    assert final['windowed'] is True and final['windowStopTick'] == tick + window, final
    assert final['windowHorizonTick'] == 899 and final['windowRemainingTicks'] == 899 - (tick + window)
    assert short['replay']['ticks'] == full['replay']['ticks'] == 900, short['replay']['ticks']
    assert final['horizonTick'] == 899, final
    assert short['prefix'] == full['prefix'] and short['acceptance'] == full['acceptance']
    # The window really is the runner's own branch, truncated: byte-identical through the edge.
    edge = len(short['replay']['events'])
    assert short['replay']['events'] == full['replay']['events'][:edge], 'window is not the branch prefix'
    assert final['finishBoundary'] == 'window-not-final' and final['censored'] is True, final
    assert 'window stop at tick %d' % (tick + window) in final['stopReason'], final['stopReason']
    assert short['replay']['finish'] is None and short['replay']['postFinish']['finishApplied'] is False
    assert short['window']['complete'] is False and short['window']['remainingTicks'] == 899 - (tick + window)
    job = short['window']['jobId']
    assert job and len(job_state()['jobs']) == 1, job_state()

    # Poll the job until it is complete; every poll must splice as an exact prefix of the branch.
    from_tick = tick + window + 1
    polls = 0
    while True:
        polls += 1
        assert polls < 400, 'the branch job did not finish'
        try:
            reply = poll_branch(dict(schema=POLL_SCHEMA, jobId=job, fromTick=from_tick))
        except InteractionError as error:
            # A poll between the click and the job's first completed stride is an explicit retry,
            # never an empty answer.
            assert (error.code, error.status) == ('interaction-job-pending', 409), (error.code, error.status)
            time.sleep(0.02)
            continue
        assert reply['schema'] == POLL_SCHEMA and reply['jobId'] == job
        assert reply['prefixEventCount'] == len(events_before(short['replay'], from_tick))
        delta = reply['replay']['events']
        assert all(event['tick'] >= from_tick for event in delta), 'a poll returned events before fromTick'
        merged = dict(reply['replay'], events=events_before(short['replay'], from_tick) + delta)
        if reply['state'] == 'ready':
            break
        assert reply['replay']['finalState']['windowStopTick'] >= final['windowStopTick']
        time.sleep(0.05)
    assert reply['window']['complete'] is True and reply['replay']['finalState']['windowed'] is False
    assert json.dumps(merged, sort_keys=True) == json.dumps(full['replay'], sort_keys=True), \
        'the asynchronous branch is not identical to the synchronous one'
    assert polls > 1, 'the branch job finished before it could be observed running'

    # A window that already reaches the horizon is the ordinary full response with no job.
    near = run_interaction(body(source, tick=890, windowTicks=WINDOW_TICKS))
    assert 'window' not in near and near['replay']['finalState']['windowed'] is False, near.keys()
    clear_sessions()
    return dict(jobId=job, polls=polls, windowEvents=edge, branchEvents=len(full['replay']['events']))


def check_window_latency():
    """A windowed click must not wait for the whole future: measured, and materially faster."""
    source = scenario([herb(PRESCHEDULED_TICK)], stock=3, tick_limit=900)
    tick, window = 500, 40
    clear_sessions()
    prime_session(source)
    started = time.perf_counter()
    full = run_interaction(body(source, tick=tick))
    full_seconds = time.perf_counter() - started
    clear_sessions()
    prime_session(source)
    started = time.perf_counter()
    short = run_interaction(body(source, tick=tick, windowTicks=window))
    window_seconds = time.perf_counter() - started
    assert short['replay']['finalState']['windowStopTick'] == tick + window
    assert window_seconds * 2 < full_seconds, (window_seconds, full_seconds)
    clear_sessions()
    return dict(fullSeconds=round(full_seconds, 3), windowSeconds=round(window_seconds, 3),
                speedup=round(full_seconds / max(window_seconds, 1e-6), 1),
                windowTicks=window, resumedFrom=short['prefix']['commandTick'])


def check_repeat_click_and_job_lifecycle():
    """A second click while a branch job runs supersedes it (no lost activation, still one job)."""
    source = scenario([herb(PRESCHEDULED_TICK)], stock=4, tick_limit=900)
    clear_sessions()
    first = run_interaction(body(source, tick=200, windowTicks=30))
    first_job = first['window']['jobId']
    assert first_job and len(job_state()['jobs']) == 1, job_state()
    # Repeat click, same item, later tick, while the first branch future is still pending.
    second = run_interaction(body(first['scenario'], tick=400, windowTicks=30))
    second_job = second['window']['jobId']
    assert second_job != first_job, (first_job, second_job)
    state = job_state()
    assert len(state['jobs']) == 1 and state['jobs'][0]['id'] == second_job, state
    # The superseded job is gone, and the new one still answers with the branch of the second click.
    unknown = refuses_poll(dict(schema=POLL_SCHEMA, jobId=first_job, fromTick=0), 'interaction-job-unknown', 404)
    assert 'superseded' in str(unknown)
    assert second['scenario']['inputs'] == [herb(PRESCHEDULED_TICK), herb(200), herb(400)]
    # The second click resumed from a checkpoint published by a DIFFERENT schedule (the first branch),
    # which is only allowed because the schedules match up to the checkpoint tick.
    assert last_run()['resumed'] is True and last_run()['checkpointTick'] < 400, last_run()

    # A running branch job must never block a click: the job drops its stride and the click runs.
    started = time.perf_counter()
    third = run_interaction(body(second['scenario'], tick=600, windowTicks=30))
    blocked_seconds = time.perf_counter() - started
    assert third['replay']['finalState']['windowStopTick'] == 630, third['replay']['finalState']
    assert blocked_seconds < 3.0, blocked_seconds
    assert job_state()['jobs'][0]['id'] == third['window']['jobId'], job_state()

    # Busy is still reported with clear feedback, never a silent drop: another CLICK holds the lock.
    assert acquire_run_lock('click')
    try:
        refuses(body(source, tick=HERB_TICK, windowTicks=30), 'interaction-busy', 429)
    finally:
        release_run_lock()
    # Errors: a bad fromTick is a 400, and a cleared session drops the job and its worker.
    live_job = third['window']['jobId']
    refuses_poll(dict(schema=POLL_SCHEMA, jobId=live_job, fromTick=-1), 'invalid-request', 400)
    refuses_poll(dict(schema=POLL_SCHEMA), 'invalid-request', 400)
    clear_sessions()
    assert job_state()['jobs'] == [], job_state()
    refuses_poll(dict(schema=POLL_SCHEMA, jobId=live_job, fromTick=0), 'interaction-job-unknown', 404)
    return dict(superseded=[first_job, second_job], active=live_job,
                blockedSeconds=round(blocked_seconds, 3))


def check_transport_wiring():
    """The deployed handler advertises and accepts the envelope; both module copies are identical."""
    # Deployed-runtime parity for EVERY module: the missing rebuild of combat_replay_export.py is
    # exactly what shipped a payload without `endingCutTick` to the live endpoint.
    for packaged in sorted(DEPLOYED.glob('*.py')):
        canonical = TOOLS / packaged.name
        assert canonical.is_file(), f'{packaged} has no recovery source'
        assert packaged.read_bytes() == canonical.read_bytes(), f'{packaged} is out of date'
    manifest = json.loads((DEPLOYED / 'combat_runtime_data/runtime-manifest.json').read_text(encoding='utf-8'))
    import hashlib
    files = {p.relative_to(DEPLOYED).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(DEPLOYED.rglob('*'))
             if p.is_file() and '__pycache__' not in p.parts
             and p.name != 'runtime-manifest.json'}
    assert manifest['packageFiles'] == files, 'runtime manifest does not match the deployed files'
    spec = importlib.util.spec_from_file_location('battle_run_interaction_function', API / 'battle-run.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    runtime = module._runtime()
    assert runtime['interaction'] is not None and runtime['interaction_error'] is InteractionError
    assert runtime['interaction_schema'] == INTERACTION_SCHEMA
    assert runtime['interaction_poll'] is not None
    assert runtime['interaction_poll_schema'] == POLL_SCHEMA

    class StubHandler(module.handler):
        def __init__(self):
            self.status, self.payload = None, None

        def _respond(self, status, payload):
            self.status, self.payload = status, payload

    stub = StubHandler()
    stub.do_GET()
    assert stub.status == 200 and stub.payload['status'] == 'ok'
    assert stub.payload['interactionSchema'] == INTERACTION_SCHEMA, stub.payload
    assert stub.payload['interactionPollSchema'] == POLL_SCHEMA, stub.payload
    assert stub.payload['envelopes'] == ['ka-special-combat-research-1', 'ka-battle-eval-1',
                                         'ka-battle-search-1'], stub.payload['envelopes']

    source = scenario([herb(PRESCHEDULED_TICK)], stock=2)
    status, payload = module.handle_request(json.dumps(body(source)).encode('utf-8'))
    assert (status, payload['schema']) == (200, INTERACTION_SCHEMA), (status, payload)
    assert payload['status'] == 'accepted' and payload['acceptance']['stock']['after'] == 0
    assert payload['replay']['schema'] == 'ka-battle-replay-1'
    assert payload['scenario']['inputs'] == [herb(PRESCHEDULED_TICK), herb(HERB_TICK)]
    assert payload['limits'] == LIMITS, payload['limits']
    status, payload = module.handle_request(json.dumps(body(source, item=SMALL_POTION)).encode('utf-8'))
    assert (status, payload['code']) == (422, 'interaction-unsupported-item'), (status, payload)
    foreign = dict(schema=INTERACTION_SCHEMA, scenario=dict(source, schema='ka-other-1'),
                   command=dict(kind='use-item', tick=HERB_TICK, item=HOLY_HERB))
    status, payload = module.handle_request(json.dumps(foreign).encode('utf-8'))
    assert (status, payload['code']) == (422, 'scenario-rejected'), (status, payload)
    assert module.INTERACTION_SCHEMA == INTERACTION_SCHEMA
    assert set(module.ACCEPTED_SCHEMAS) >= {INTERACTION_SCHEMA, POLL_SCHEMA}, module.ACCEPTED_SCHEMAS
    assert module.INTERACTION_POLL_SCHEMA == POLL_SCHEMA
    # The poll envelope is accepted by the same transport and refuses an unknown job with its own code.
    status, payload = module.handle_request(json.dumps(dict(schema=POLL_SCHEMA, jobId='nope')).encode('utf-8'))
    assert (status, payload['code']) == (404, 'interaction-job-unknown'), (status, payload)
    status, payload = module.handle_request(json.dumps(dict(schema=POLL_SCHEMA)).encode('utf-8'))
    assert (status, payload['code']) == (400, 'invalid-request'), (status, payload)


if __name__ == '__main__':
    check_schedule_insertion()
    response = check_acceptance_and_prefix()
    check_generic_item_and_capabilities()
    check_no_effect_and_refusals()
    check_repeated_same_tick_click()
    check_ending_cut = check_real_ending_cut_and_branch_prefix()
    check_session_resume()
    window_report = check_window_and_branch_job()
    latency_report = check_window_latency()
    click_report = check_repeat_click_and_job_lifecycle()
    check_transport_wiring()
    report = dict(schema='ka-battle-interaction-checks-1',
                  checks=['schedule insertion order and branch point',
                          'used command: acceptance, stock, prefix and changed state',
                          'declared all-residents row and RNG-drawing row',
                          'single-resident and undeclared rows fail closed',
                          'no-effect dispatch spends no stock and reproduces the source run',
                          'repeated same-item click at one tick attributes only its own dispatch',
                          'impossible timing/stock/phase requests are refused',
                          'single-flight refusal while a run holds the lock',
                          'real Ending fixture: cut fields and item branch prefix',
                          'checkpoint session resume is byte-identical and bounded',
                          'window response is the true branch prefix, never a final battle',
                          'branch job poll splices an exact prefix and equals the synchronous branch',
                          'windowed click is materially faster than the full branch',
                          'repeat click supersedes the branch job; busy/error/teardown are explicit',
                          'transport envelope wiring and deployed module parity'],
                  commandTick=HERB_TICK, prefixEvents=response['prefix']['eventCount'],
                  changedUnits=len(response['acceptance']['changed']), issues=[],
                  window=window_report, latency=latency_report, repeatClick=click_report)
    evidence = WORKSPACE / 'RE-evidence/20260920-visual-battle-builder/interaction-checks.json'
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report))
