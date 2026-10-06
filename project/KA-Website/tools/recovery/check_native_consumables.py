"""Consumable/input subsystem parity: canonical Python vs the Rust kernel.

`combat_consumables` is the source of truth. The canonical order inside one tick is

    tick head / verdict gate
    -> input_callback('before_fighters')
    -> fighters
    -> input_callback('after_fighters')   (the sandbox's `observed_inputs` runs the inputs and the
                                           reward observer under that one callback)
    -> status -> projectiles -> movement -> ... -> trailing phases

Both sides start from the identical real captured tick-0 engine of a scenario carrying a real
consumable timeline and then execute the same ticks:

    Python: `consumable_loop`, the canonical `SharedControllers.run` shell driven by
            `combat_consumables` exactly as `combat_sandbox.run_scenario` drives it.
    Rust:   `ka_run_battle`, from a `ka_abi` snapshot that carries the schedule, the item table and
            the starting stock.

`consumable_loop` is only allowed to be the reference because it is proven against the real
`run_scenario` report first: the harness compares its Holy Herb / battle-item use records,
remaining stocks, verdict and tick count against the sandbox's own report, and fails if they differ.

Sections:
    1. targeted dispatch parity (per-case 1..N tick)
    2. the canonically unreachable single-resident item types
    3. whole-battle consumable parity through the production adapter boundary
    4. template / clone isolation with consumables
    5. created-entity arena headroom for a full-horizon consumable battle
    6. the real desktop Run worker path picking the native backend

    pypy3.exe check_native_consumables.py [section ...]
"""
import copy
import ctypes
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import strategy_optimizer_native as native_backend  # noqa: E402
import ka_abi  # noqa: E402
import ka_events  # noqa: E402
from check_native_battle_full import compare, encounter_from_engine  # noqa: E402
from check_native_real_state import Checkpoints, global_phases, projectile_phase  # noqa: E402
from check_native_real_state import status_phase, trailing_phases  # noqa: E402
from combat_animation_selection import HUMAN_BASES  # noqa: E402
from combat_consumables import use_battle_holy_herb, use_battle_item  # noqa: E402
from combat_ending import (advance_battle_frame, after_ending_confirmation, enter_ending,  # noqa: E402
                           is_annihilated)
from combat_resolution import add_raw_parameter  # noqa: E402
from combat_reward_entitlement import RewardEntitlementWatch  # noqa: E402
from combat_sandbox import run_scenario  # noqa: E402
from combat_shared_controllers import ROWS  # noqa: E402
from combat_shared_resolution import execute_shared_skill_commands  # noqa: E402
from combat_tick import update_fighters  # noqa: E402
from strategy_optimizer_adapter import default_scenario  # noqa: E402


# ------------------------------------------------------------------ canonical input callback
def make_inputs(scenario, engine, counters):
    """`combat_sandbox.run_scenario`'s `inputs()` closure, reproduced statement for statement.

    `counters` carries the mutable nonlocal state the sandbox keeps in its closure: `stock`,
    `itemCounts`, `uses`, `itemUses`, plus `log`, one row per dispatch in dispatch order in the
    native `use_log` shape.
    """
    items = scenario.get('items', {})
    item_slot = counters['itemSlot']

    def add(identity, parameter, amount, source):
        before = engine.value(identity, parameter)
        param = engine.param(identity, parameter)
        param['rawValue'] = add_raw_parameter(param['rawValue'], amount,
                                              engine.maximum(identity, parameter))[0]
        after = engine.value(identity, parameter)
        if after != before:
            engine.emit('resource_change', target=identity, parameter=parameter, before=before,
                        after=after, max=engine.maximum(identity, parameter), sourceItem=source)

    def emit_item(record):
        engine.emit('battle_item',
                    **{k: v for k, v in record.items() if k not in ('targets', 'tick', 'phase')},
                    targets=record['targets'])

    def inputs(world, phase):
        for event in scenario['inputs']:
            if event['tick'] != world.tick or event['phase'] != phase:
                continue
            if event['type'] == 'holy_herb':
                def spend():
                    counters['stock'] -= 1
                blocked = counters['stock'] <= 0
                used = use_battle_holy_herb(
                    [u['id'] for u in world.teams[0]], lambda i: world.units[i]['board'][5],
                    lambda: counters['stock'] > 0, world.rate, world.maximum,
                    lambda i: world.specs[i]['human'],
                    lambda i, p, amount: add(i, p, amount, 'holy_herb'), spend)
                counters['uses'].append(dict(tick=world.tick, phase=phase, item='holy_herb',
                                             used=used, remaining=counters['stock']))
                counters['log'].append(dict(kind=ka_abi.INPUT_HOLY_HERB, item=-1,
                                            tick=world.tick, used=int(bool(used)),
                                            percent=-1 if blocked else 100,
                                            remaining=counters['stock']))
                record = dict(parameter=11, scope='all', used=used,
                              percent=None if blocked else 100, item='holy_herb',
                              remaining=counters['stock'],
                              targets=[u['id'] for u in world.teams[0]
                                       if world.units[u['id']]['board'][5] not in (7, 8)])
                if blocked:
                    record['blocked'] = 'no stock'
                emit_item(record)
                continue
            name = event['item']
            row = items[name]

            def spend(name=name):
                counters['itemCounts'][name] -= 1

            record = use_battle_item(
                row, [u['id'] for u in world.teams[0]],
                lambda name=name: counters['itemCounts'][name] > 0,
                lambda row=row: engine.next_math('item_bonus_value',
                                                 bound=row['bonusMaxValue'] - row['bonusMinValue'] + 1),
                world.rate, world.maximum, lambda i: world.specs[i]['human'],
                lambda i, p, amount: add(i, p, amount, name), spend,
                state=lambda i: world.units[i]['board'][5])
            record.update(tick=world.tick, phase=phase, item=name,
                          remaining=counters['itemCounts'][name])
            counters['itemUses'].append(record)
            percent = record['percent']
            counters['log'].append(dict(kind=ka_abi.INPUT_ITEM, item=item_slot[name],
                                        tick=world.tick, used=int(bool(record['used'])),
                                        percent=-1 if percent is None else percent,
                                        remaining=counters['itemCounts'][name]))
            emit_item(record)

    return inputs


def new_counters(scenario, item_slot):
    return dict(stock=int(scenario['holyHerbStock']),
                itemCounts=dict(scenario.get('itemStock', {})), uses=[], itemUses=[], log=[],
                itemSlot=item_slot)


def consumable_tick(engine, inputs, watch):
    """One canonical tick: the `SharedControllers.run` body plus the two input callbacks.

    The reward observer sits inside the sandbox's `after_fighters` callback, right after the inputs,
    which is exactly where `battle_control::tick` runs it natively.
    """
    engine.tick += 1
    engine.battle_frame = advance_battle_frame(engine.battle_frame)
    if engine.battle_state == 2 and any(is_annihilated(t) for t in engine.teams):
        engine.battle_frame = 0
        engine.verdict = enter_ending(engine.teams, engine.change)
        engine.battle_state = 3
        engine.emit('verdict', result=engine.verdict)
    inputs(engine, 'before_fighters')
    update_fighters(engine.teams, engine.battle_state, engine.update,
                    lambda unit: execute_shared_skill_commands(
                        engine.world, unit['id'], lambda command: ROWS[command['skill']],
                        engine.animate, engine.use))
    inputs(engine, 'after_fighters')
    watch.observe(engine)
    status_phase(engine)
    projectile_phase(engine)
    global_phases(engine)
    trailing_phases(engine)


def battle_shell(engine, policy=0):
    """`check_native_battle_full.battle_loop`'s return dict for an already-stepped engine."""
    if engine.battle_state == 3 and policy == 0:
        counter, transition = after_ending_confirmation(engine.battle_frame, engine.verdict)
        engine.ending_counter = counter
        engine.ending_confirmed = transition is not None
        if transition is not None:
            engine.ending_gate_tick = engine.tick
    return dict(
        ticks=engine.tick + 1, verdict=engine.verdict, battleState=engine.battle_state,
        battleFrame=engine.battle_frame, endingGateTick=engine.ending_gate_tick,
        endingConfirmed=engine.ending_confirmed, endingCounter=engine.ending_counter,
        prizeCallbacks=len(engine.prizes),
        unresolvedCommands=sum(1 for u in engine.units.values() if u['commands']),
        pendingProjectiles=len(engine.projectiles.members.indices),
        activeDamageOrLeaving=sum(1 for u in engine.units.values() if u['board'][5] in (6, 8)),
        mathDraws=engine.math_draws, libDraws=engine.lib_draws,
        verdictTick=next((event['tick'] for event in engine.trace if event['kind'] == 'verdict'),
                         None))


def python_consumables(consumables_def, counters, item_slot):
    """The expected consumable snapshot block: live stock plus the dispatch log."""
    items = [dict(row) for row in consumables_def['items']]
    for name, slot in item_slot.items():
        items[slot]['stock'] = counters['itemCounts'].get(name, 0)
    return dict(holy_herb_stock=counters['stock'], items=items,
                inputs=consumables_def['inputs'], uses=list(counters['log']))


# ------------------------------------------------------------------------------- targeted cases
def item(bonus_type, low, high):
    return dict(bonusCategory=3, bonusType=bonus_type, bonusMinValue=low, bonusMaxValue=high)


def herb_input(tick, phase='before_fighters'):
    return dict(tick=tick, phase=phase, type='holy_herb')


def item_input(tick, name, phase='before_fighters'):
    return dict(tick=tick, phase=phase, type='item', item=name, target='all')


#: Each case is one real consumable timeline over a frozen encounter. `stop` is the last tick run.
TARGETED = [
    dict(name='herb-full-roster-fails', encounterId=19, mathSeed=7, libSeed=8, stop=2,
         items={}, itemStock={}, holyHerbStock=1, inputs=[herb_input(1)]),
    dict(name='herb-after-damage-succeeds', encounterId=19, mathSeed=7, libSeed=8, stop=60,
         items={}, itemStock={}, holyHerbStock=1, inputs=[herb_input(55)]),
    dict(name='herb-no-stock', encounterId=19, mathSeed=7, libSeed=8, stop=6,
         items={}, itemStock={}, holyHerbStock=0, inputs=[herb_input(5)]),
    dict(name='herb-two-uses-one-stock', encounterId=19, mathSeed=7, libSeed=8, stop=70,
         items={}, itemStock={}, holyHerbStock=1, inputs=[herb_input(40), herb_input(60)]),
    dict(name='herb-after-fighters', encounterId=19, mathSeed=7, libSeed=8, stop=35,
         items={}, itemStock={}, holyHerbStock=2,
         inputs=[herb_input(30, 'after_fighters'), herb_input(32, 'after_fighters')]),
    dict(name='item-mp-fixed-no-draw', encounterId=19, mathSeed=7, libSeed=8, stop=45,
         items={'salve': item(2, 50, 50)}, itemStock={'salve': 1}, holyHerbStock=0,
         inputs=[item_input(40, 'salve')]),
    dict(name='item-mp-ranged-draw', encounterId=19, mathSeed=7, libSeed=8, stop=45,
         items={'salve': item(2, 100, 200)}, itemStock={'salve': 2},
         holyHerbStock=0, inputs=[item_input(40, 'salve')]),
    dict(name='item-hp-ranged-full-fails-after-draw', encounterId=19, mathSeed=7, libSeed=8,
         stop=2, items={'tonic': item(0, 10, 40)}, itemStock={'tonic': 3}, holyHerbStock=0,
         inputs=[item_input(1, 'tonic')]),
    dict(name='item-parameter-12', encounterId=19, mathSeed=7, libSeed=8, stop=45,
         items={'charm': item(4, 1, 99)}, itemStock={'charm': 1}, holyHerbStock=0,
         inputs=[item_input(40, 'charm')]),
    dict(name='item-no-stock', encounterId=19, mathSeed=7, libSeed=8, stop=12,
         items={'salve': item(2, 100, 200)}, itemStock={'salve': 0}, holyHerbStock=0,
         inputs=[item_input(10, 'salve')]),
    dict(name='item-stock-exhausted', encounterId=19, mathSeed=7, libSeed=8, stop=45,
         items={'salve': item(2, 100, 200)}, itemStock={'salve': 1}, holyHerbStock=0,
         inputs=[item_input(40, 'salve'), item_input(41, 'salve')]),
    dict(name='herb-and-item-same-tick', encounterId=19, mathSeed=7, libSeed=8, stop=25,
         items={'salve': item(2, 100, 200)}, itemStock={'salve': 1}, holyHerbStock=1,
         inputs=[herb_input(20), item_input(20, 'salve')]),
    dict(name='item-then-herb-same-tick', encounterId=19, mathSeed=7, libSeed=8, stop=25,
         items={'salve': item(2, 100, 200)}, itemStock={'salve': 1}, holyHerbStock=1,
         inputs=[item_input(20, 'salve'), herb_input(20)]),
    dict(name='two-item-definitions-one-tick', encounterId=19, mathSeed=7, libSeed=8, stop=45,
         items={'salve': item(2, 100, 200), 'tonic': item(0, 50, 150)},
         itemStock={'salve': 1, 'tonic': 1}, holyHerbStock=0,
         inputs=[item_input(40, 'salve'), item_input(40, 'tonic')]),
    dict(name='both-phases-same-tick', encounterId=19, mathSeed=7, libSeed=8, stop=25,
         items={'salve': item(2, 100, 200)}, itemStock={'salve': 1}, holyHerbStock=1,
         inputs=[herb_input(20, 'before_fighters'), item_input(20, 'salve', 'after_fighters')]),
    dict(name='herb-near-verdict', encounterId=19, mathSeed=7, libSeed=8, stop=400,
         items={}, itemStock={}, holyHerbStock=3,
         inputs=[herb_input(120), herb_input(200), herb_input(300)]),
]


def scenario_for(base, case):
    return dict(base, encounterId=case['encounterId'], tickLimit=case['stop'] + 1,
                mathSeed=case['mathSeed'], libSeed=case['libSeed'], inputs=case['inputs'],
                items=case['items'], itemStock=case['itemStock'],
                holyHerbStock=case['holyHerbStock'])


def targeted_section(library, base, report):
    for case in TARGETED:
        scenario = scenario_for(base, case)
        stop = case['stop']
        checkpoints = Checkpoints([0])
        run_scenario(dict(scenario), checkpoints=checkpoints, stop_tick=0)
        stored = checkpoints.stored[0]
        consumables_def, item_slot, reason = ka_abi.consumables_from_scenario(scenario)
        if reason is not None:
            report['failures'].append(f"{case['name']}: unexpectedly ineligible: {reason}")
            continue
        canonical = run_scenario(dict(scenario), include_trace=False)
        expected_consumables = python_consumables(consumables_def, new_counters(scenario,
                                                                               item_slot),
                                                  item_slot)
        pre = ka_abi.engine_snapshot(stored['engine'], ROWS, int(stored['engine'].battle_state),
                                     HUMAN_BASES, expected_consumables)
        battle = ka_abi.load_snapshot(library, pre)
        try:
            reloaded = ka_abi.native_snapshot(library, battle, pre)
            import_diffs = ka_abi.diff_snapshots(pre, reloaded)
            if import_diffs:
                report['failures'].append(
                    f"{case['name']}: consumable import is lossy: {import_diffs[:4]}")
                continue
            # Canonical Python reference, from the identical tick-0 engine, stepped one tick at a
            # time so a divergence is localised to its tick rather than only seen at the end.
            engine = copy.deepcopy(stored['engine'])
            counters = new_counters(scenario, item_slot)
            watch = RewardEntitlementWatch(encounter_from_engine(engine), ROWS)
            inputs = make_inputs(scenario, engine, counters)
            library.ka_battle_set_scope_allowed(battle, int(watch.scope['allowed']))
            battle_state = int(stored['engine'].battle_state)
            steps = 0
            for step in range(stop):
                trace_start = len(engine.trace)
                consumable_tick(engine, inputs, watch)
                status = library.ka_native_full_tick(battle)
                if status != 0:
                    report['failures'].append(
                        f"{case['name']}: native refused at tick {step + 1} with {status}")
                    break
                steps = step + 1
                expected = ka_abi.engine_snapshot(
                    engine, ROWS, battle_state, HUMAN_BASES,
                    python_consumables(consumables_def, counters, item_slot))
                got = ka_abi.native_snapshot(library, battle, pre)
                diffs = ka_abi.diff_snapshots(expected, got)
                want_events = ka_events.normalize_python(engine.trace[trace_start:], item_slot)
                have_events = ka_events.normalize_native(ka_abi.native_events(library, battle))
                if want_events != have_events:
                    diffs.append(f'events: python={want_events[:10]!r} '
                                 f'native={have_events[:10]!r}')
                if diffs:
                    report['failures'].append(
                        f"{case['name']}: tick {step + 1}: " + '; '.join(diffs[:6]))
                    break
                report['events'] += len(want_events)
                report['kinds'].update(kind for kind, _, _, _ in want_events)
            if steps != stop:
                continue
            # the replica must reproduce the real sandbox, or it is not a reference
            for key, have in (('holyHerbUses', counters['uses']),
                              ('itemUses', counters['itemUses']),
                              ('holyHerbRemaining', counters['stock']),
                              ('itemRemaining', counters['itemCounts'])):
                if canonical.get(key) != have:
                    report['failures'].append(
                        f"{case['name']}: replica disagrees with combat_sandbox on {key}: "
                        f'sandbox={canonical.get(key)!r} replica={have!r}')
            # the whole-battle report block, from the same `SharedControllers.run` shell
            report_out = ka_abi.KaBattleReport()
            status = library.ka_battle_report(battle, 0, ctypes.byref(report_out))
            if status != 0:
                report['failures'].append(f"{case['name']}: report refused with {status}")
                continue
            got = ka_abi.report_dict(report_out)
            diffs = compare(case['name'], battle_shell(engine), got, watch, got)
            counters_used = sum(1 for row in counters['log'] if row['used'])
            if report_out.resource_uses != counters_used:
                diffs.append(
                    f"{case['name']}: resourceUses python={counters_used} "
                    f'native={report_out.resource_uses}')
            if diffs:
                report['failures'].append(f"{case['name']}: " + '; '.join(diffs[:6]))
                continue
            report['uses'] += len(counters['log'])
            report['cases'] += 1
            report['case_rows'].append(
                dict(name=case['name'], ticks=stop, uses=len(counters['log']),
                     successful=counters_used, herb=counters['stock'],
                     events=len(engine.trace)))
        finally:
            library.ka_battle_free(battle)


# --------------------------------------------------------------- canonically unreachable types
def unreachable_section(library, base, report):
    """An odd `bonusType` dispatches the single-resident scope, which no battle path supplies."""
    for bonus_type in (1, 3, 5):
        scenario = dict(base, encounterId=19, tickLimit=20, mathSeed=7, libSeed=8,
                        inputs=[item_input(5, 'odd')],
                        items={'odd': item(bonus_type, 10, 20)}, itemStock={'odd': 1},
                        holyHerbStock=0)
        supported, reason = native_backend.eligibility(scenario)
        if supported:
            report['failures'].append(f'bonusType {bonus_type}: judged native-eligible')
        elif 'all-resident' not in (reason or ''):
            report['failures'].append(f'bonusType {bonus_type}: unhelpful reason {reason!r}')
        try:
            run_scenario(dict(scenario), stop_tick=10)
            report['failures'].append(
                f'bonusType {bonus_type}: the canonical run did not reject the dispatch')
        except ValueError as error:
            if 'explicit resident' not in str(error):
                report['failures'].append(f'bonusType {bonus_type}: unexpected error {error}')
        # the kernel itself refuses the same dispatch rather than inventing a target
        checkpoints = Checkpoints([0])
        run_scenario(dict(base, encounterId=19, tickLimit=20, mathSeed=7, libSeed=8),
                     checkpoints=checkpoints, stop_tick=0)
        engine = checkpoints.stored[0]['engine']
        forced = dict(holy_herb_stock=0, uses=[],
                      items=[dict(parameter=ka_abi.RECOVERY_PARAMETERS[bonus_type],
                                  all_residents=0, bonus_min=10, bonus_max=20, stock=1)],
                      inputs=[dict(tick=1, phase=0, kind=ka_abi.INPUT_ITEM, item=0)])
        pre = ka_abi.engine_snapshot(engine, ROWS, int(engine.battle_state), HUMAN_BASES, forced)
        battle = ka_abi.load_snapshot(library, pre)
        try:
            out = ka_abi.KaBattleReport()
            status = library.ka_run_battle(battle, 3, 0, ctypes.byref(out))
            if status == 0:
                report['failures'].append(
                    f'bonusType {bonus_type}: the kernel ran a single-resident dispatch')
            else:
                report['refusals'] += 1
        finally:
            library.ka_battle_free(battle)
        report['cases'] += 1


# ------------------------------------------------------------------- whole-battle consumable
FULL_BATTLES = [
    dict(encounterId=19, tickLimit=7000, mathSeed=7, libSeed=8,
         items={'salve': item(2, 100, 200), 'tonic': item(0, 50, 150)},
         itemStock={'salve': 2, 'tonic': 2}, holyHerbStock=3,
         inputs=[herb_input(120), item_input(400, 'salve'), herb_input(1200),
                 item_input(2400, 'tonic', 'after_fighters'), herb_input(4000),
                 item_input(5000, 'salve')]),
    dict(encounterId=19, tickLimit=7000, mathSeed=26, libSeed=27,
         items={'salve': item(2, 100, 200)}, itemStock={'salve': 4}, holyHerbStock=2,
         inputs=[herb_input(60, 'after_fighters'), item_input(900, 'salve'),
                 item_input(900, 'salve'), herb_input(3000), item_input(5200, 'salve')]),
    dict(encounterId=0, tickLimit=2000, mathSeed=12, libSeed=13,
         items={'tonic': item(0, 10, 90)}, itemStock={'tonic': 3}, holyHerbStock=2,
         inputs=[item_input(50, 'tonic'), herb_input(300), item_input(800, 'tonic')]),
]


def full_battle_section(library, base, report):
    import strategy_optimizer_adapter as adapter
    for case in FULL_BATTLES:
        scenario = dict(base, **{k: v for k, v in case.items() if k != 'inputs'})
        scenario = dict(scenario, inputs=case['inputs'])
        seeds = (case['mathSeed'], case['libSeed'])
        canonical = adapter.simulate(scenario, seeds, backend='python')
        native = adapter.simulate(scenario, seeds, backend='native')
        label = f"enc{case['encounterId']}@{case['tickLimit']} seed{seeds[0]}/{seeds[1]}"
        if native.get('resultBackend') != 'native':
            report['failures'].append(f'{label}: did not run natively')
            continue
        for key in sorted(set(canonical) | set(native)):
            if key == 'resultBackend':
                continue
            if canonical.get(key) != native.get(key):
                report['failures'].append(
                    f'{label}: {key}: python={canonical.get(key)!r} native={native.get(key)!r}')
        report['cases'] += 1
        report['full_rows'].append(dict(label=label, ticks=canonical['ticks'],
                                        verdict=canonical['verdict'],
                                        resourceUses=canonical['resourceUses'],
                                        digest_match=canonical['digest'] == native['digest'],
                                        reward_match=(canonical['rewardOutcome']
                                                      == native['rewardOutcome'])))


# ------------------------------------------------------------------------- clone isolation
def clone_section(library, base, report):
    case = FULL_BATTLES[1]
    scenario = dict(base, **{k: v for k, v in case.items() if k != 'inputs'})
    scenario = dict(scenario, inputs=case['inputs'])
    checkpoints = Checkpoints([0])
    run_scenario(dict(scenario), checkpoints=checkpoints, stop_tick=0)
    engine = checkpoints.stored[0]['engine']
    consumables, _slot, reason = ka_abi.consumables_from_scenario(scenario)
    if reason is not None:
        report['failures'].append(f'clone: ineligible scenario: {reason}')
        return
    pre = ka_abi.engine_snapshot(engine, ROWS, int(engine.battle_state), HUMAN_BASES, consumables)
    template = ka_abi.load_snapshot(library, pre)
    try:
        checksum = library.ka_battle_checksum(template)
        starts = []
        finals = []
        # Seed pairs that stay inside the kernel's fixed entity arena, so the isolation check is about
        # consumable state rather than about the arena bound the `capacity` section covers.
        for seeds in ((7, 8), (26, 27), (19, 20)):
            clone = library.ka_battle_clone(template)
            if not clone:
                report['failures'].append('clone: ka_battle_clone returned null')
                return
            try:
                starts.append(ka_abi.native_consumable_state(library, clone))
                library.ka_battle_seed_rng(clone, seeds[0], seeds[1])
                out = ka_abi.KaBattleReport()
                status = library.ka_run_battle(clone, case['tickLimit'], 0, ctypes.byref(out))
                if status != 0:
                    report['failures'].append(f'clone seed{seeds}: refused with {status}')
                    return
                finals.append(ka_abi.native_consumable_state(library, clone))
            finally:
                library.ka_battle_free(clone)
        expected = ka_abi.consumable_shape(consumables)
        if any(start != expected for start in starts):
            report['failures'].append(
                f'clone: a fresh clone did not start from the template state: {starts[0]}')
        if any(final == expected for final in finals):
            report['failures'].append('clone: a clone did not consume anything')
        repeat = library.ka_battle_clone(template)
        try:
            library.ka_battle_seed_rng(repeat, 7, 8)
            out = ka_abi.KaBattleReport()
            library.ka_run_battle(repeat, case['tickLimit'], 0, ctypes.byref(out))
            same = ka_abi.native_consumable_state(library, repeat)
            if same != finals[0]:
                report['failures'].append('clone: replays of the same seed differ')
        finally:
            library.ka_battle_free(repeat)
        if library.ka_battle_checksum(template) != checksum:
            report['failures'].append('clone: the cached template checksum changed')
        if ka_abi.native_consumable_state(library, template) != expected:
            report['failures'].append('clone: the cached template state changed')
        report['cases'] += 1
        report['clone_rows'].append(dict(checksum=checksum, clones=len(finals),
                                         final_uses=len(finals[0]['uses']),
                                         final_stock=finals[0]['holy_herb_stock']))
    finally:
        library.ka_battle_free(template)


def headroom_section(library, base, report):
    """The created-entity arena must be big enough for a full-horizon consumable battle.

    A consumable timeline keeps a team alive to the tick horizon, so it creates more entities than a
    battle that is annihilated early. This scenario needed more than the original 4096-entity arena and
    used to fall back to Python on every 7000-tick seed; it must now run natively with exact parity, and
    the measured occupancy must leave real headroom.
    """
    import strategy_optimizer_adapter as adapter
    case = FULL_BATTLES[1]
    scenario = dict(base, **{k: v for k, v in case.items() if k != 'inputs'})
    scenario = dict(scenario, inputs=case['inputs'])
    seeds = (909, 707)
    native_backend.reset_counters()
    native_compact = adapter.simulate(scenario, seeds, backend='native')
    canonical = adapter.simulate(scenario, seeds, backend='python')
    label = f'headroom enc{case["encounterId"]}@{case["tickLimit"]} seed{seeds[0]}/{seeds[1]}'
    counters = native_backend.counters()
    if native_compact.get('resultBackend') != 'native' or counters.get('nativeFallbacks'):
        report['failures'].append(
            f'{label}: no longer runs natively: backend={native_compact.get("resultBackend")} '
            f'counters={counters}')
        return
    for key in sorted(set(canonical) | set(native_compact)):
        if key == 'resultBackend':
            continue
        if canonical.get(key) != native_compact.get(key):
            report['failures'].append(
                f'{label}: {key}: python={canonical.get(key)!r} '
                f'native={native_compact.get(key)!r}')
    # Arena occupancy for this battle, from one more run of the cached template.
    entry = native_backend._template(scenario, native_backend.scenario_key(scenario))
    clone = library.ka_battle_clone(entry['handle'])
    try:
        library.ka_battle_seed_rng(clone, seeds[0], seeds[1])
        if entry['follower_draws']:
            library.ka_battle_skip_lib_draws(clone, entry['follower_draws'])
        out = ka_abi.KaBattleReport()
        status = library.ka_run_battle(clone, entry['tick_limit'], 0, ctypes.byref(out))
        objects = library.ka_object_count(clone) if status == 0 else -1
    finally:
        library.ka_battle_free(clone)
    if objects < 0 or objects >= ka_abi.KA_MAX_OBJECTS:
        report['failures'].append(f'{label}: arena occupancy {objects} has no headroom')
    report['cases'] += 1
    report['headroom_rows'].append(dict(label=label, ticks=canonical['ticks'],
                                        verdict=canonical['verdict'], objects=objects,
                                        cap=ka_abi.KA_MAX_OBJECTS,
                                        digest_match=(canonical['digest']
                                                      == native_compact['digest'])))


def run_path_section(library, base, report):
    """The desktop Run worker itself must select native for consumable scenarios.

    `strategy_optimizer.worker` is the exact function the Run/discovery/validation pool executes; it
    calls `strategy_optimizer_adapter.simulate` with no backend argument, so this exercises the real
    production decision rather than a test double.
    """
    import strategy_optimizer as optimiser
    import strategy_optimizer_adapter as adapter
    import strategy_optimizer_native as native_backend
    for name, extra in (('non-consumable', {}),
                        ('consumable', dict(holyHerbStock=2,
                                            items={'salve': item(2, 100, 200)},
                                            itemStock={'salve': 1},
                                            inputs=[herb_input(60), item_input(120, 'salve')]))):
        scenario = dict(base, encounterId=19, tickLimit=700, mathSeed=7, libSeed=8)
        scenario.update(extra)
        native_backend.reset_counters()
        backends = {}
        for index in range(4):
            seeds = (1000 + index, 2000 + index)
            result = optimiser.worker(scenario, seeds)
            backends[result['resultBackend']] = backends.get(result['resultBackend'], 0) + 1
            canonical = adapter.simulate(scenario, seeds, backend='python')
            for key in sorted(set(canonical) | set(result)):
                if key in ('resultBackend', 'elapsedSeconds'):
                    continue
                if canonical.get(key) != result.get(key):
                    report['failures'].append(
                        f'{name}: Run worker {key}: python={canonical.get(key)!r} '
                        f'worker={result.get(key)!r}')
        counters = native_backend.counters()
        if backends != {'native': 4}:
            report['failures'].append(f'{name}: Run worker backends {backends}')
        if counters.get('nativeFallbacks'):
            report['failures'].append(f'{name}: Run worker fell back: {counters}')
        report['cases'] += 1
        report['run_rows'].append(dict(name=name, backends=backends,
                                       cache=counters.get('templateCacheHits', 0),
                                       runs=counters.get('nativeRuns', 0)))


SECTIONS = (('targeted', targeted_section), ('unreachable', unreachable_section),
            ('full', full_battle_section), ('clone', clone_section),
            ('headroom', headroom_section), ('run', run_path_section))


def main():
    wanted = [name for name in sys.argv[1:] if not name.startswith('-')] or \
        [name for name, _ in SECTIONS]
    library = ka_abi.load()
    base = default_scenario()
    report = dict(failures=[], cases=0, events=0, uses=0, refusals=0, kinds=set(),
                  case_rows=[], full_rows=[], clone_rows=[], headroom_rows=[], run_rows=[])
    started = time.perf_counter()
    for name, function in SECTIONS:
        if name not in wanted:
            continue
        mark = time.perf_counter()
        function(library, base, report)
        print(f'  {name}: {len(report["failures"])} failures, {time.perf_counter() - mark:.2f}s')
    print(f'consumable parity ({time.perf_counter() - started:.2f}s):')
    for row in report['case_rows']:
        print(f"  OK {row['name']}: {row['ticks']} ticks, {row['uses']} dispatches "
              f"({row['successful']} used), herb stock {row['herb']}, {row['events']} events")
    for row in report['full_rows']:
        print(f"  OK full {row['label']}: ticks={row['ticks']} verdict={row['verdict']} "
              f"resourceUses={row['resourceUses']} digest={row['digest_match']} "
              f"rewardOutcome={row['reward_match']}")
    for row in report['clone_rows']:
        print(f"  OK clone: checksum={row['checksum']:#x} clones={row['clones']} "
              f"uses={row['final_uses']} herb stock={row['final_stock']}")
    for row in report['headroom_rows']:
        print(f"  OK headroom {row['label']}: native, ticks={row['ticks']} verdict={row['verdict']} "
              f"objects={row['objects']}/{row['cap']} digest={row['digest_match']}")
    for row in report['run_rows']:
        print(f"  OK desktop Run {row['name']}: backends={row['backends']} nativeRuns={row['runs']} "
              f"cacheHits={row['cache']}")
    print(f"  cases={report['cases']} events={report['events']} dispatches={report['uses']} "
          f"native refusals={report['refusals']} event kinds={sorted(report['kinds'])}")
    if report['failures']:
        print('FAILURES:')
        for row in report['failures'][:20]:
            print(f'  {row}')
        return 1
    print('  consumable events, stock, use records, resource_change, RNG, final state, '
          'compact result, digest and rewardOutcome all matched')
    return 0


if __name__ == '__main__':
    sys.exit(main())
