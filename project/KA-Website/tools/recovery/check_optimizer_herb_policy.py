"""The live `<=3%` Holy Herb policy and the MP telemetry it rides on: reference versus native.

Two things are proved here, separately.

**MP telemetry.** For the configured DPS and healer the observers publish the minimum MP reached, that
minimum as a percentage of the unit's own maximum, whether the unit ever crossed `<=3%`, the first
crossing tick and phase, and whether MP ever reached exactly 0. The reference observer
(`combat_progress.ProgressWatch.observe`) and the kernel's own after-fighters observer must agree on
every one of those, field by field, on fixed seeds - alongside the compact digest, so the whole fight
is the same fight.

**The policy.** With the policy enabled, a watched unit crossing `<=3%` of its own maximum MP latches a
Holy Herb use; the *existing* legal item-action seam consumes it through the same recovered
`use_battle_holy_herb` a prescribed `{tick, phase}` event uses; the stock is decremented, the use is
counted, the cap is respected, and the trigger re-arms only after the herb has restored the unit above
the threshold. The automatic run is proved to produce the *same battle* as the equivalent prescribed
events at the same ticks and phases.

Stock and cap are experiment configuration: the game never states a herb quantity. This file therefore
declares its own values and labels them. `MP_FIXTURE` is a lowered MP pool for the watched DPS, chosen
so the drain is reachable inside the horizon - a scenario parameter, not a claim about the game.

    python check_optimizer_herb_policy.py
"""
import ctypes
import json
import sys
from copy import deepcopy
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import ka_abi  # noqa: E402
import search_contract as contract  # noqa: E402
import strategy_optimizer_adapter as adapter  # noqa: E402
import strategy_optimizer_native as native  # noqa: E402
from check_native_real_state import Checkpoints  # noqa: E402
from combat_sandbox import run_scenario  # noqa: E402

#: TEST FIXTURE (scenario/experiment configuration, never a recovered or production game value).
MP_FIXTURE = dict(draining=200, ample=120)
#: TEST FIXTURE stock/cap pairs the policy cases declare explicitly.
STOCK_FIXTURE = dict(small=1, three=3)
HORIZON = 20000
FAILURES = []
NOTES = []


def expect(condition, message):
    if not condition:
        FAILURES.append(message)
    return bool(condition)


def base_scenario():
    return adapter.default_scenario()


def variant(mp=None, stock=0, max_uses=0, triggers=(), horizon=HORIZON, offset=0, label=''):
    """One scenario with the declared experiment parameters applied and validated."""
    scenario = json.loads(json.dumps(base_scenario()))
    scenario['tickLimit'] = horizon
    scenario['holyHerbStock'] = stock
    scenario['holyHerbMaxUses'] = max_uses
    scenario['holyHerbTriggerUnits'] = list(triggers)
    humans = [unit['name'] for unit in scenario['ownUnits'] if unit['human']]
    scenario['mathSeed'] = 7 + offset*7919
    scenario['libSeed'] = 8 + offset*104729
    if mp is not None:
        unit = next(unit for unit in scenario['ownUnits'] if unit['name'] == humans[0])
        contract.set_effective_parameter(scenario, unit, 11, mp)
    scenario['_label'] = label
    return adapter.validate_scenario(scenario)


def compare(scenario, label):
    """Reference vs native on one scenario; returns `(reference, native)` payloads."""
    python = adapter._python_simulate_compact(
        scenario, (scenario['mathSeed'], scenario['libSeed']))
    native_payload, reason = native._native_compact(
        scenario, (scenario['mathSeed'], scenario['libSeed']))
    if native_payload is None:
        FAILURES.append(f'{label}: native refused the case ({reason})')
        return python, None
    keys = ('verdict', 'censored', 'ticks', 'prizeCallbacks', 'survivors', 'resourceUses',
            'healthFraction', 'behavior', 'digest', 'progressMetrics', 'mpMetrics', 'herbMetrics')
    for key in keys:
        if python.get(key) != native_payload.get(key):
            FAILURES.append(f'{label}: {key} python={python.get(key)!r} '
                            f'native={native_payload.get(key)!r}')
    return python, native_payload


def humans_of(scenario):
    return [unit['name'] for unit in scenario['ownUnits'] if unit['human']]


# ---------------------------------------------------------------------------------------------
# 1. MP telemetry parity and coverage
# ---------------------------------------------------------------------------------------------

def check_mp_telemetry():
    dps, healer = humans_of(base_scenario())[:2]
    coverage = dict(crossing=0, zero=0, no_crossing=0)
    # (a) the real DPS pool: the DPS dips to its 3% floor and the policy fires
    for offset in (0, 1, 2):
        scenario = variant(stock=STOCK_FIXTURE['three'], max_uses=STOCK_FIXTURE['three'],
                           triggers=[dps, healer], horizon=3000, offset=offset,
                           label=f'fixture enc19 3000t seed{offset} (DPS+healer)')
        python, _native = compare(scenario, scenario['_label'])
        mp = python['mpMetrics']
        expect(len(mp) == 2 and [entry['name'] for entry in mp] == [dps, healer],
               f"{scenario['_label']}: the MP block must name the declared trigger units in order, "
               f'got {mp!r}')
        expect(all(entry['identity'] >= 0 for entry in mp),
               f"{scenario['_label']}: every trigger unit must resolve to a roster identity")
        expect(all(entry['firstLowMpPhase'] in (None, 'after_fighters') for entry in mp),
               f"{scenario['_label']}: the crossing phase must be the seam the observer samples at")
        expect(python['herbMetrics']['useCount'] >= 1,
               f"{scenario['_label']}: the policy must fire when the DPS crosses 3%")
        coverage['crossing'] += 1 if any(entry['reachedLowMp'] for entry in mp) else 0
    # (b) a lowered MP pool: the watched DPS reaches exactly 0 and the field says so
    for offset in (0, 1, 2):
        label = f'fixture enc19 drained-MP DPS seed{offset} (telemetry only, stock 0)'
        telemetry_only = variant(mp=MP_FIXTURE['draining'], stock=0, max_uses=0, triggers=[dps],
                                 offset=offset, label=label)
        python, _native = compare(telemetry_only, label)
        entry = python['mpMetrics'][0]
        coverage['zero'] += 1 if entry['reachedZero'] else 0
        expect(entry['reachedZero'] and entry['minimumMp'] == 0 and entry['minimumMpPercent'] == 0,
               f'{label}: a drained pool must report min 0 / 0% / reachedZero, got {entry!r}')
        expect(entry['firstLowMpTick'] >= 0 and entry['reachedLowMp'],
               f'{label}: reaching 0 is also a `<=3%` crossing, got {entry!r}')
        expect(python['herbMetrics']['useCount'] == 0,
               f'{label}: stock 0 must dispatch nothing')
    # (c) a pool that never crosses: no crossing, no use, and the sentinel stays unset
    for offset in (0, 1, 2):
        label = f'fixture enc19 ample-MP DPS seed{offset} (never crosses)'
        scenario = variant(mp=MP_FIXTURE['ample'], stock=STOCK_FIXTURE['three'],
                           max_uses=STOCK_FIXTURE['three'], triggers=[dps], offset=offset,
                           label=label)
        python, _native = compare(scenario, label)
        entry = python['mpMetrics'][0]
        coverage['no_crossing'] += 1 if not entry['reachedLowMp'] else 0
        expect(entry['minimumMpPercent'] > 3 and entry['firstLowMpTick'] == -1
               and entry['firstLowMpPhase'] is None and not entry['reachedZero'],
               f'{label}: an uncrossed pool must keep the -1/None sentinels, got {entry!r}')
        expect(python['herbMetrics']['useCount'] == 0
               and python['herbMetrics']['remainingStock'] == STOCK_FIXTURE['three'],
               f'{label}: a threshold that is never crossed must not spend stock')
    expect(all(coverage[key] for key in coverage), f'MP coverage incomplete: {coverage}')
    NOTES.append('mp telemetry: '
                 + ', '.join(f'{key}={value}' for key, value in sorted(coverage.items())))


# ---------------------------------------------------------------------------------------------
# 2. What the herb actually did: tick, phase, MP around the use, stock, restoration
# ---------------------------------------------------------------------------------------------

def reference_checkpoint_mp(scenario, tick, identity):
    """The reference engine's own MP for `identity` at the end of `tick`, from a bounded run."""
    checkpoints = Checkpoints([tick])
    run_scenario(scenario, checkpoints=checkpoints, stop_tick=tick)
    engine = checkpoints.stored[tick]['engine']
    return engine.value(identity, 11), engine.maximum(identity, 11)


def native_checkpoint_mp(scenario, steps, identity):
    """The kernel's own MP after `steps` native ticks (ending at tick `steps - 1`)."""
    entry = native._build_template(scenario, native.scenario_key(scenario))
    library = native.library()
    try:
        clone = library.ka_battle_clone(entry['handle'])
        try:
            library.ka_battle_seed_rng(clone, scenario['mathSeed'], scenario['libSeed'])
            report = ka_abi.KaBattleReport()
            policy = 2 if scenario.get('finishPolicy') == 'on-verdict' else 0
            status = library.ka_run_battle(clone, steps, policy, ctypes.byref(report))
            return None if status != 0 else library.ka_battle_mp(clone, identity)
        finally:
            library.ka_battle_free(clone)
    finally:
        library.ka_battle_free(entry['handle'])


def check_use_effect():
    base = base_scenario()
    dps = humans_of(base)[0]
    label = f'fixture enc19 drained-MP DPS {dps} (policy on)'
    scenario = variant(mp=MP_FIXTURE['draining'], stock=STOCK_FIXTURE['three'],
                       max_uses=STOCK_FIXTURE['three'], triggers=[dps], offset=0, label=label)
    python, native_payload = compare(scenario, label)
    herb = python['herbMetrics']
    uses = herb['uses']
    expect(herb['useCount'] == len(uses) and herb['useCount'] >= 2,
           f'expected several automatic uses, got {herb!r}')
    expect(herb['remainingStock'] == herb['startingStock'] - herb['useCount'],
           f'stock must decrement once per successful use, got {herb!r}')
    expect(all(row['used'] and row['source'] == 0 for row in uses),
           f'the declared DPS is trigger slot 0 and every use must be successful: {uses!r}')
    expect(all(row['phase'] == 'before_fighters' for row in uses),
           f'a latched crossing is consumed by the next input seam: {uses!r}')
    expect(uses and uses[0]['tick'] - 1 == python['mpMetrics'][0]['firstLowMpTick'],
           f'the first use must follow the first crossing by exactly one tick: {uses[:1]} vs '
           f"{python['mpMetrics'][0]!r}")
    # The use must run the recovered action: the reference emits the herb's own `resource_change`,
    # carrying the pre-dispatch and post-dispatch values the engine itself computed.
    full = run_scenario(scenario, include_trace=True)
    identity = python['mpMetrics'][0]['identity']
    # The herb tops up *every* living resident, so the trace carries one resource_change per affected
    # resident per dispatch; the trigger unit is the one whose pool actually reached 0.
    events = [event for event in full['trace']
              if event.get('kind') == 'resource_change' and event.get('sourceItem') == 'holy_herb'
              and event.get('target') == identity]
    expect(len(events) == herb['useCount'],
           f'one resource_change per dispatched herb for the trigger unit, got {len(events)} for '
           f'{herb["useCount"]} uses')
    if events:
        event = events[0]
        expect(event['target'] == identity and event['parameter'] == 11,
               f'the herb restores parameter 11 on the trigger unit, got {event!r}')
        expect(event['before'] < event['after'],
               f"the herb must raise MP, got before={event['before']} after={event['after']}")
        expect(event['after'] == event['max'],
               f"the herb restores 100% of the unit's own maximum, got {event!r}")
    # MP immediately before and after the first use, read on *both* engines at the same ticks.
    first = uses[0]['tick'] if uses else -1
    before_reference, maximum = reference_checkpoint_mp(scenario, first - 1, identity)
    after_reference, _ = reference_checkpoint_mp(scenario, first, identity)
    before_native = native_checkpoint_mp(scenario, first, identity)
    after_native = native_checkpoint_mp(scenario, first + 1, identity)
    expect(before_native == before_reference,
           f'MP before the use differs: python={before_reference} native={before_native}')
    expect(after_native == after_reference,
           f'MP after the use differs: python={after_reference} native={after_native}')
    expect(after_reference >= before_reference,
           f'the use must not lower MP: before={before_reference} after={after_reference}')
    expect(after_reference > maximum*3//100,
           f'the use must leave the unit above the 3% trigger: {after_reference} of {maximum}')
    NOTES.append(f'first automatic use: tick {first} before_fighters, MP {before_reference} -> '
                 f'{after_reference} of {maximum} (above the threshold)')
    expect(native_payload is not None and native_payload['herbMetrics'] == herb,
           'the kernel must report the same herb block')


# ---------------------------------------------------------------------------------------------
# 3. The automatic policy runs the same battle as the equivalent prescribed events
# ---------------------------------------------------------------------------------------------

def prescribed_equivalent(scenario, uses):
    """The same scenario with the automatic uses written out as prescribed `{tick, phase}` events.

    The watch declaration stays: with `holyHerbMaxUses = 0` it is telemetry only, so the prescribed run
    must report the *same* MP readings as the automatic one. That is what makes "the policy produced
    this battle" a measurement rather than a claim.
    """
    out = deepcopy(scenario)
    out['holyHerbMaxUses'] = 0
    order = {'before_fighters': 0, 'after_fighters': 1}
    out['inputs'] = sorted(list(out.get('inputs') or [])
                           + [dict(tick=row['tick'], phase=row['phase'], type='holy_herb')
                              for row in uses],
                           key=lambda event: (event['tick'], order[event['phase']]))
    out['_label'] = str(scenario.get('_label')) + ' (prescribed)'
    return adapter.validate_scenario(out)


def check_prescribed_equivalence():
    dps = humans_of(base_scenario())[0]
    for offset in (0, 1, 2):
        label = f'fixture enc19 drained-MP DPS seed{offset} (policy)'
        scenario = variant(mp=MP_FIXTURE['draining'], stock=STOCK_FIXTURE['three'],
                           max_uses=STOCK_FIXTURE['three'], triggers=[dps], offset=offset, label=label)
        python, _native = compare(scenario, label)
        uses = python['herbMetrics']['uses']
        if not expect(uses, f'{label}: the policy must fire'):
            continue
        scheduled = prescribed_equivalent(scenario, uses)
        other, _native_other = compare(scheduled, scheduled['_label'])
        expect(other['digest'] == python['digest'],
               f'{label}: the automatic policy must produce the same battle as the prescribed '
               f'events (digest {other["digest"][:12]} vs {python["digest"][:12]})')
        expect(other['ticks'] == python['ticks'] and other['verdict'] == python['verdict'],
               f'{label}: verdict/ticks must match the prescribed run')
        expect(other['mpMetrics'] == python['mpMetrics'],
               f'{label}: the MP telemetry must match the prescribed run')
        expect([row['tick'] for row in other['herbMetrics']['uses']]
               == [row['tick'] for row in uses],
               f'{label}: the prescribed run must dispatch at the same ticks')
        expect(all(row['source'] is None for row in other['herbMetrics']['uses']),
               f'{label}: a prescribed use is not authorised by a trigger slot')
    NOTES.append('the live policy and the same events prescribed by hand produce one battle')


# ---------------------------------------------------------------------------------------------
# 4. The cap, the re-arming, and the paths that must not spend
# ---------------------------------------------------------------------------------------------

def check_cap_rearm_and_gates():
    base = base_scenario()
    dps = humans_of(base)[0]
    capped = variant(mp=MP_FIXTURE['draining'], stock=STOCK_FIXTURE['three'],
                     max_uses=STOCK_FIXTURE['small'], triggers=[dps], offset=0,
                     label='fixture enc19 drained-MP DPS (cap 1)')
    python, _native = compare(capped, capped['_label'])
    expect(python['herbMetrics']['maxUses'] == STOCK_FIXTURE['small']
           and python['herbMetrics']['useCount'] == 1,
           f'the cap must stop the policy after one use, got {python["herbMetrics"]!r}')
    expect(python['herbMetrics']['remainingStock'] == STOCK_FIXTURE['three'] - 1,
           f'a capped run must still spend exactly its uses: {python["herbMetrics"]!r}')
    expect(python['mpMetrics'][0]['reachedZero'],
           'the fight must still drain past the threshold after the cap is reached')
    uncapped = variant(mp=MP_FIXTURE['draining'], stock=STOCK_FIXTURE['three'],
                       max_uses=STOCK_FIXTURE['three'], triggers=[dps], offset=0,
                       label='fixture enc19 drained-MP DPS (cap 3, re-arming)')
    python, _native = compare(uncapped, uncapped['_label'])
    ticks = [row['tick'] for row in python['herbMetrics']['uses']]
    expect(len(ticks) == STOCK_FIXTURE['three'] and ticks == sorted(ticks),
           f'the policy must fire once per re-armed crossing: {ticks!r}')
    expect(len(set(ticks)) == len(ticks),
           f'two uses must never share a tick: {ticks!r}')
    NOTES.append(f're-arm: automatic uses at ticks {ticks} under a cap of {STOCK_FIXTURE["three"]}')
    disabled = variant(mp=MP_FIXTURE['draining'], stock=STOCK_FIXTURE['three'], max_uses=0,
                       triggers=[dps], offset=0,
                       label='fixture enc19 drained-MP DPS (policy disabled)')
    python, _native = compare(disabled, disabled['_label'])
    expect(python['herbMetrics']['useCount'] == 0
           and python['herbMetrics']['remainingStock'] == STOCK_FIXTURE['three'],
           f'holyHerbMaxUses = 0 must disable the automatic policy, got {python["herbMetrics"]!r}')
    expect(python['mpMetrics'][0]['reachedZero'],
           'the MP telemetry must still run while the policy is disabled')
    for bad, why in (
            (dict(holyHerbStock=1, holyHerbMaxUses=2, holyHerbTriggerUnits=[dps]),
             'a cap above the declared stock'),
            (dict(holyHerbStock=2, holyHerbMaxUses=2, holyHerbTriggerUnits=[dps, dps]),
             'a repeated trigger unit'),
            (dict(holyHerbStock=2, holyHerbMaxUses=2, holyHerbTriggerUnits=['not a unit']),
             'an undeclared trigger unit'),
            (dict(holyHerbStock=2, holyHerbMaxUses=2), 'a policy with no trigger units')):
        scenario = json.loads(json.dumps(base_scenario()))
        scenario.update(bad)
        try:
            adapter.validate_scenario(scenario)
            FAILURES.append(f'{why} must be refused by the scenario validator')
        except Exception:  # noqa: BLE001 - the refusal is the assertion
            pass
    NOTES.append('stock/cap/trigger declarations are validated, never guessed')


# ---------------------------------------------------------------------------------------------
# 5. Declaring a watch without a policy must not move the battle
# ---------------------------------------------------------------------------------------------

def check_declaration_is_neutral():
    humans = humans_of(base_scenario())
    plain = variant(stock=0, max_uses=0, triggers=[], offset=0, label='no declaration')
    observed = variant(stock=0, max_uses=0, triggers=humans[:2], offset=0,
                       label='watch declared, policy off')
    python_plain, _ = compare(plain, plain['_label'])
    python_observed, _ = compare(observed, observed['_label'])
    expect(python_plain['digest'] == python_observed['digest'],
           'declaring trigger units with holyHerbMaxUses = 0 must not change the battle')
    expect(python_plain['mpMetrics'] == [],
           f'a scenario with no declared trigger units must report an empty MP block, '
           f'got {python_plain["mpMetrics"]!r}')
    expect(python_plain['herbMetrics'] == dict(startingStock=0, remainingStock=0, maxUses=0,
                                               useCount=0, uses=[]),
           f'a scenario with no declared stock must report a zeroed herb block, '
           f'got {python_plain["herbMetrics"]!r}')
    expect([entry['name'] for entry in python_observed['mpMetrics']] == humans[:2],
           'the declared watch must be reported while the policy stays off')
    NOTES.append('watch-only declarations are telemetry: the battle digest is unchanged')


# ---------------------------------------------------------------------------------------------
# 6. The prescribed-item mechanism is intact, and the herb is distinguishable from it
# ---------------------------------------------------------------------------------------------

def check_prescribed_items_intact():
    dps = humans_of(base_scenario())[0]
    scenario = json.loads(json.dumps(base_scenario()))
    scenario['holyHerbStock'] = STOCK_FIXTURE['three']
    scenario['holyHerbMaxUses'] = STOCK_FIXTURE['three']
    scenario['holyHerbTriggerUnits'] = [dps]
    scenario['items'] = dict(LargePotion=dict(bonusCategory=3, bonusType=0, bonusMinValue=30,
                                              bonusMaxValue=30))
    scenario['itemStock'] = dict(LargePotion=1)
    scenario['tickLimit'] = 3000
    scenario['inputs'] = [dict(tick=20, phase='before_fighters', type='holy_herb'),
                          dict(tick=40, phase='after_fighters', type='item', item='LargePotion',
                               target='all')]
    scenario['_label'] = 'fixture enc19 prescribed herb + prescribed item, policy on'
    scenario = adapter.validate_scenario(scenario)
    python, _native = compare(scenario, scenario['_label'])
    rows = python['herbMetrics']['uses']
    prescribed = [row for row in rows if row['source'] is None]
    automatic = [row for row in rows if row['source'] is not None]
    expect([row['tick'] for row in prescribed] == [20],
           f'the prescribed herb must still dispatch at its own tick, got {rows!r}')
    expect(automatic, 'the automatic policy must add its own uses beside the prescribed one')
    expect(python['herbMetrics']['useCount'] == len(rows),
           f'every dispatched herb must be counted: {python["herbMetrics"]!r}')
    expect(python['resourceUses'] == len(rows) + 1,
           f'resourceUses must count the herb uses and the battle item together, '
           f'got {python["resourceUses"]} for {len(rows)} herbs and 1 item')
    NOTES.append('prescribed herb and prescribed battle item still dispatch; the herb log separates '
                 "the policy's own uses from them")


# ---------------------------------------------------------------------------------------------
# 7. Persistence: the MP minima must aggregate in the opposite direction to every other maximum
# ---------------------------------------------------------------------------------------------

def check_persistence_aggregation():
    import strategy_optimizer as optimizer
    dps = humans_of(base_scenario())[0]
    scenario = variant(mp=MP_FIXTURE['draining'], stock=STOCK_FIXTURE['three'],
                       max_uses=STOCK_FIXTURE['three'], triggers=[dps], offset=0,
                       label='fixture enc19 drained-MP DPS (aggregation)')
    readings = []
    for offset in (0, 1, 2):
        run = dict(scenario)
        run['mathSeed'] = 7 + offset*7919
        run['libSeed'] = 8 + offset*104729
        readings.append(adapter._python_simulate_compact(run, (run['mathSeed'], run['libSeed'])))
    total = None
    for reading in readings:
        total = optimizer.accumulate(total, reading)
    unit = total['mp'][dps]
    minimums = [reading['mpMetrics'][0]['minimumMp'] for reading in readings]
    percents = [reading['mpMetrics'][0]['minimumMpPercent'] for reading in readings]
    expect(unit['runs'] == len(readings),
           f'every run with a reading must be counted, got {unit!r}')
    expect(unit['minimumMp'] == min(minimums) and unit['minimumMpPercent'] == min(percents),
           f'the aggregate must keep the MINIMUM reading, not the maximum: {unit!r} from {minimums}')
    expect(unit['lowRuns'] == sum(1 for reading in readings
                                  if reading['mpMetrics'][0]['reachedLowMp']),
           f'the crossing count must count runs, got {unit!r}')
    expect(unit['zeroRuns'] == sum(1 for reading in readings
                                   if reading['mpMetrics'][0]['reachedZero']),
           f'the drained count must count runs, got {unit!r}')
    uses = [reading['herbMetrics']['useCount'] for reading in readings]
    expect(total['herb']['useTotal'] == sum(uses) and total['herb']['useMax'] == max(uses),
           f'the herb counts must add and keep the per-run maximum, got {total["herb"]!r}')
    # An unset reading (`-1` for the MP fields, `-1`/`None` for the tick) must never win a minimum, and
    # a run that carries no telemetry at all must not erase what earlier runs measured.
    unset = deepcopy(readings[0])
    unset['mpMetrics'] = [dict(unset['mpMetrics'][0], minimumMp=-1, minimumMpPercent=-1,
                               reachedLowMp=False, firstLowMpTick=-1, firstLowMpPhase=None,
                               reachedZero=False)]
    unset['herbMetrics'] = dict(unset['herbMetrics'], useCount=0, uses=[])
    total = optimizer.accumulate(total, unset)
    silent = deepcopy(readings[0])
    silent['mpMetrics'] = []
    silent.pop('herbMetrics')
    total = optimizer.accumulate(total, silent)
    expect(total['mp'][dps]['minimumMp'] == min(minimums),
           f'an unset sentinel must not win the minimum: {total["mp"][dps]!r}')
    expect(total['mp'][dps]['minimumMpPercent'] == min(percents),
           f'an unset sentinel must not win the percentage minimum: {total["mp"][dps]!r}')
    expect(total['mp'][dps]['runs'] == len(readings) + 1
           and total['mpRuns'] == len(readings) + 1,
           f'the per-unit run count must count measured runs, and `mpRuns` the runs that carried a '
           f'block: {total["mp"][dps]!r} / mpRuns={total.get("mpRuns")!r}')
    merged = optimizer.merge_aggregates(total, total)
    expect(merged['mp'][dps]['minimumMp'] == min(minimums),
           f'merging discovery and validation must not dilute a minimum: {merged["mp"][dps]!r}')
    expect(merged['mpRuns'] == 2*total['mpRuns'],
           f'`mpRuns` must add across the two phases: {merged.get("mpRuns")!r}')
    expect(merged['mp'][dps]['runs'] == 2*total['mp'][dps]['runs']
           and merged['herb']['useTotal'] == 2*total['herb']['useTotal'],
           f'merged counts must add: {merged["mp"][dps]!r} / {merged["herb"]!r}')
    record = optimizer.lane_record(dict(id='candidate', label='candidate', source='mutation'),
                                   merged, encounter=19, defeat=0)
    expect(record['mp'][dps]['minimumMp'] == min(minimums),
           f'the lane record must expose the persisted minimum: {record["mp"]!r}')
    expect(record['herb']['useTotal'] == merged['herb']['useTotal'],
           f'the lane record must expose the persisted herb counts: {record["herb"]!r}')
    NOTES.append(f'persistence: minimum {min(minimums)} MP / {min(percents)}% survives accumulate, '
                 f'merge_aggregates and lane_record; {sum(uses)} automatic uses aggregated')


def main():
    library = native.library()
    print(f'kernel: {ka_abi.DLL}')
    print(f'report bytes: {library.report_bytes} '
          f'(ctypes mirror {ctypes.sizeof(ka_abi.KaBattleReport)})')
    for check in (check_mp_telemetry, check_use_effect, check_prescribed_equivalence,
                  check_cap_rearm_and_gates, check_declaration_is_neutral,
                  check_prescribed_items_intact, check_persistence_aggregation):
        check()
    for note in NOTES:
        print(f'  {note}')
    print(f'  failures: {len(FAILURES)}')
    for detail in FAILURES[:12]:
        print(f'    {detail}')
    return 1 if FAILURES else 0


if __name__ == '__main__':
    sys.exit(main())
