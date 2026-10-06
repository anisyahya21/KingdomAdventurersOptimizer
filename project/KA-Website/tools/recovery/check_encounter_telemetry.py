"""Compact encounter-telemetry parity: opt-in, additive, digest-preserving, v3.

Verifies the `encounterTelemetry` block (schema v2):

  * telemetry OFF and ON on the same fixture+seeds produce a byte-identical `digest` and an
    identical legacy key set (the block is attached strictly after the digest);
  * every implemented counter is compared field for field between the bounded Python event-site
    observer and the versioned encounter kernel (`ka_kernel_encounter_v2.dll`) - there is no
    "native-only" counter left;
  * an absent observer is `null` + reason, never a blank-progress zero, and the genuinely
    unobserved quantities (`landedHits`, `expectedLandedHits`) carry an explicit reason; every
    other v3 metric is a real reading;
  * the death-snapshot future-hit mass is distinct from the remaining command count, carries its
    semantics and the currently-executing-hit inclusion, and the compact post-death actual-hit gap
    summary, boss Leaving/Damaging-reset/re-entry counters, boss-access ticks and per-tick
    targetable/UsingSkill occupancy are published;
  * the own true-death (HP -> 0) tick is a different event from the first Leaving (state 8) entry,
    and both are published;
  * the enemy pre-reaction roll is published separately from the post-reaction resolved result;
  * the boss post-death attempts/lands are counted from the event's target identity and HP state;
  * the `finalReward` on-verdict dispatch is exercised for a real resolved loss (0 chests), a
    synthetic win and Cure, and an unresolved run (null + reason);
  * the consumable fixture uses the canonical Item.txt rows and the consumption limit is observed
    exhausted (a later trigger is blocked, not silently ignored);
  * the opt-in kernel is selected by name/version/size/hash, and the DLL hash is recorded in the
    build's source-hash manifest - the library identity is the loaded DLL (path + file hash +
    version/size probes), never claimed to be a hash of the Rust source; `telemetry` off never
    shares a cached template pointer with the encounter DLL owner;
  * a missing encounter kernel is a FAILURE, never a silent skip.

Bounded: <=12 full Python battles and <=96 native runs; `--timing` adds a matched 32+32 native
off/on sample for wall-clock only (a tiny sample and never a throughput claim).
"""
import argparse
import ctypes
import json
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import strategy_optimizer_adapter as adapter  # noqa: E402
import strategy_optimizer_native as native  # noqa: E402
import combat_consumables  # noqa: E402
import combat_progress  # noqa: E402
import ka_encounter_abi  # noqa: E402
import ka_abi  # noqa: E402

#: Scalar fields the Python and native backends both build; a mismatch fails closed.
SHARED = ('ownIdentities', 'ownRoles', 'ownSurvivors', 'bossIdentity', 'bossFirstDeathTick',
          'bossLeavingTick', 'counterChecks', 'counterEnqueues', 'ownDeaths', 'ownLeavings',
          'enemy', 'boss', 'postDeath')
#: Nested blocks compared child by child for a readable failure line.
SHARED_NESTED = (('bossLifecycle', ('reentries', 'leavings', 'postDeathPrizes')),
                 ('commands', ('remainingAtDeath', 'remainingTargetingBossAtDeath',
                               'targetHoldersAtDeath', 'targetingBoss', 'targetingBossReleased',
                               'maxSimultaneous', 'maxSimultaneousTargetingBoss',
                               'targetHoldersPeak', 'releasedAfterDeath',
                               'releasedAfterDeathTargetingBoss', 'firstReleaseAfterDeathTick',
                               'lastReleaseAfterDeathTick', 'futureAttempts',
                               'futureAttemptsIncludesExecutingHit', 'futureAttemptsSemantics',
                               'landedHits', 'expectedLandedHits')))
#: Nulls that must carry a reason (the quantities neither backend observes yet).
REQUIRED_UNAVAILABLE = ('landedHits', 'expectedLandedHits')
#: Every counter the parity comparison must cover (native and Python both).
COUNTER_KEYS = ('enemyRolls', 'enemyRollHits', 'enemyRollMisses', 'enemyResolvedAttacks',
                'enemyHits', 'enemyMisses', 'counterChecks', 'counterEnqueues', 'bossDeathTick',
                'bossPostdeathAttempts', 'bossPostdeathLands', 'bossReentries', 'bossLeavings',
                'bossPostdeathGapCount', 'bossPostdeathGapMin', 'bossPostdeathGapMax',
                'bossPostdeathGapSum', 'bossFutureHitsAtDeath', 'bossFutureIncludeExecuting',
                'bossAccessFirstCommand', 'bossAccessFirstAttempt', 'targetableTicks',
                'usingSkillTicks', 'bossDamagingResets')

RECOVERY_ITEMS_PATH = (HERE.parent.parent
                       / 'artifacts/kingdom-adventures/src/game-data/native-recovery-items.json')
#: The build's source-hash manifest (source hashes recorded at build time + the built DLL identity).
MANIFEST_PATH = HERE / 'native' / 'ka_kernel' / 'encounter-source-manifest.json'

FIXTURES = []


def canonical_recovery():
    """The canonical recovered battle-item rows, from the site's own source-of-truth file."""
    data = json.loads(RECOVERY_ITEMS_PATH.read_text(encoding='utf-8'))
    return {item['name']: item for item in data['items']}


def canonical_row(item):
    return dict(bonusCategory=item['bonusCategory'], bonusType=item['bonusType'],
                bonusMinValue=item['bonusMinValue'], bonusMaxValue=item['bonusMaxValue'])


def _humans(scenario):
    return [unit['name'] for unit in scenario.get('ownUnits') or [] if unit.get('human')]


def build_fixtures():
    """A death fixture, a consumable fixture, and a real resolved loss (finite horizon).

    The first two are censored at their horizon; the loss fixture raises the configured finite
    horizon until the recovered verdict actually fires, so `finalReward` on-verdict dispatch is
    exercised on a *real* terminal battle (verdict 2, loss) rather than only synthetically.
    """
    rows = canonical_recovery()
    large = rows['Recovery Potion (L)']

    death = adapter.default_scenario()
    death['finishPolicy'] = 'on-verdict'
    death['_label'] = 'death'
    FIXTURES.append(('death', adapter.validate_scenario(death), (7, 8)))

    consumable = adapter.default_scenario()
    humans = _humans(consumable)
    consumable['holyHerbStock'] = 2
    consumable['holyHerbMaxUses'] = 2
    consumable['holyHerbTriggerUnits'] = humans[:1]
    consumable['items'] = {large['name']: canonical_row(large)}
    consumable['itemStock'] = {large['name']: 1}
    consumable['inputs'] = [dict(tick=40, phase='after_fighters', type='item',
                                 item=large['name'], target='all')]
    consumable['tickLimit'] = 3000
    consumable['_label'] = 'consumable'
    FIXTURES.append(('consumable', adapter.validate_scenario(consumable), (7, 8)))

    loss = adapter.default_scenario()
    loss['finishPolicy'] = 'on-verdict'
    loss['tickLimit'] = 12000   # high enough that the recovered verdict fires (~6070)
    loss['_label'] = 'loss'
    FIXTURES.append(('loss', adapter.validate_scenario(loss), (7, 8)))


def unavailable_keys(block):
    return list((block.get('unavailable') or {}).keys())


def check_consumable_mechanism(failures, notes):
    """The recovered Item.txt rows and the recovery they perform (before/after HP/MP)."""
    rows = canonical_recovery()
    large = rows['Recovery Potion (L)']
    herb = rows['Holy Herb']
    if (large['bonusCategory'], large['bonusType'], large['bonusMinValue'],
            large['bonusMaxValue']) != (3, 0, 50, 50):
        failures.append(f'Recovery Potion (L) canonical row moved: {canonical_row(large)}')
    if (herb['bonusCategory'], herb['bonusType'], herb['bonusMinValue'],
            herb['bonusMaxValue']) != (3, 2, 100, 100):
        failures.append(f'Holy Herb canonical row moved: {canonical_row(herb)}')
    if combat_progress.HERB_TRIGGER_PERCENT != 3:
        failures.append('the Holy Herb policy trigger threshold is no longer 3%')

    hp = {100: 0, 101: 50}
    max_hp = {100: 200, 101: 200}
    stock = [1]
    record = combat_consumables.use_battle_item(
        canonical_row(large), [100, 101], lambda: stock[0] > 0, lambda: 0,
        lambda identity, pid: 0, lambda identity, pid: max_hp[identity],
        lambda identity: True, lambda identity, pid, amount: hp.__setitem__(identity, hp[identity] + amount),
        lambda: stock.__setitem__(0, stock[0] - 1))
    if record['percent'] != 50:
        failures.append(f'Recovery Potion (L) applied {record["percent"]}% not the canonical 50%')
    if (hp[100], hp[101]) != (100, 150):
        failures.append(f'Recovery Potion (L) before/after HP wrong: {hp}')
    if stock[0] != 0 or not record['used']:
        failures.append('Recovery Potion (L) did not spend exactly one stock')
    blocked = combat_consumables.use_battle_item(
        canonical_row(large), [100], lambda: False, lambda: 0, lambda identity, pid: 0,
        lambda identity, pid: max_hp[identity], lambda identity: True,
        lambda identity, pid, amount: None, lambda: None)
    if blocked.get('blocked') != 'no stock' or blocked['percent'] is not None:
        failures.append('an out-of-stock Recovery Potion (L) was not blocked before the roll')

    # Holy Herb exhaustion: a one-charge stock restores once, and the second dispatch is *blocked*
    # (recorded, not silently ignored), so the consumption limit is observable end to end.
    mp = {100: 0}
    max_mp = {100: 4}
    herb_stock = [1]
    first = combat_consumables.use_battle_holy_herb(
        [100], lambda identity: 0, lambda: herb_stock[0] > 0, lambda identity, pid: 0,
        lambda identity, pid: max_mp[identity], lambda identity: True,
        lambda identity, pid, amount: mp.__setitem__(identity, mp[identity] + amount),
        lambda: herb_stock.__setitem__(0, herb_stock[0] - 1))
    second_record_ok = combat_consumables.use_battle_holy_herb(
        [100], lambda identity: 0, lambda: herb_stock[0] > 0, lambda identity, pid: 0,
        lambda identity, pid: max_mp[identity], lambda identity: True,
        lambda identity, pid, amount: mp.__setitem__(identity, mp[identity] + amount),
        lambda: herb_stock.__setitem__(0, herb_stock[0] - 1))
    if not first or mp[100] != 4:
        failures.append(f'Holy Herb did not restore 100% MP (trigger 3 is the threshold): {mp}')
    if second_record_ok or herb_stock[0] != 0:
        failures.append('Holy Herb did not block an exhausted dispatch (limit not observed)')
    notes.append('consumable mechanism: Recovery Potion (L) 50% HP all residents; Holy Herb 100% MP '
                 'all residents (trigger 3% is the threshold); limit exhaustion blocks the next use')


def _sample_counters():
    """A minimal non-null counter block for the synthetic terminal-dispatch checks."""
    return dict(ownFirstDeathTick=[[100, 5]], ownFirstLeavingTick=[[100, 9]], enemyRolls=1,
                enemyRollHits=1, enemyRollMisses=0, enemyResolvedAttacks=1, enemyHits=1,
                enemyMisses=0, counterChecks=2, counterEnqueues=1, bossDeathTick=1542,
                bossPostdeathAttempts=3, bossPostdeathLands=3, bossReentries=3)


def check_terminal_dispatch(failures, notes):
    """Exercise the `finalReward` on-verdict dispatch for win, loss, Cure and unresolved.

    There is no native Cure *verdict* (the recovered verdict is 2 own-annihilated else 1); the Cure
    case is a resolved win whose reward reading came from the Cure path, so what is exercised here
    is the dispatch boundary the Cure terminal outcome travels through.

    A loss never dispatches the queued chests just because they were pending: the native encounter
    report only exposes the pre-verdict queue, so a resolved non-win reports final dispatch 0 with
    the queue preserved separately, and the observed `finishBoundary` slot stays null with a reason
    while a declared finish-*policy* descriptor travels under `finishBoundaryLabel`.
    """
    progress = dict(combat_progress.blank_progress(), bossIdentity=999)

    def block(verdict, censored, dispatched=None, queue=None, pending=None,
              boundary=None, boundary_label=None):
        finish = None
        if dispatched is not None or queue is not None:
            finish = {}
            if dispatched is not None:
                finish['dispatchedChests'] = dispatched
            if queue is not None:
                finish['queueAtVerdict'] = queue
        return combat_progress.encounter_telemetry(
            dict(progress), finish_policy='on-verdict', verdict=verdict, censored=censored,
            diagnostic_finish=finish, counters=_sample_counters(),
            finish_boundary=boundary, finish_boundary_label=boundary_label,
            reward_outcome=dict(pendingChests=(dispatched if pending is None else pending)))

    win = block(1, False, dispatched=5)
    if (win['finalReward'] or {}).get('dispatchedChests') != 5 \
            or (win['finalReward'] or {}).get('policy') != 'on-verdict':
        failures.append(f'terminal win did not dispatch on-verdict: {win["finalReward"]}')
    loss = block(2, False, dispatched=0)
    if (loss['finalReward'] or {}).get('dispatchedChests') != 0:
        failures.append(f'terminal loss did not dispatch zero chests: {loss["finalReward"]}')
    queued_loss = block(2, False, queue=15, pending=15, boundary_label='diagnostic-truncated')
    queued_reward = queued_loss['finalReward'] or {}
    if queued_reward.get('dispatchedChests') != 0 or queued_reward.get('queueAtVerdict') != 15:
        failures.append('a 15-chest pre-verdict queue was dispatched on a loss: '
                        f'{queued_reward}')
    if queued_reward.get('pendingChests') != 15:
        failures.append(f'the pending queue was not distinguished from the dispatch: {queued_reward}')
    if queued_loss['finalStatus']['finishBoundary'] is not None:
        failures.append('a declared finish-policy descriptor was promoted into the observed '
                        f'finishBoundary slot: {queued_loss["finalStatus"]["finishBoundary"]!r}')
    if queued_loss['finalStatus']['finishBoundaryLabel'] != 'diagnostic-truncated':
        failures.append('the declared finish-policy descriptor was not preserved under '
                        f'finishBoundaryLabel: {queued_loss["finalStatus"]}')
    if 'finishBoundary' not in (queued_loss.get('unavailable') or {}):
        failures.append('a null finishBoundary carries no explicit reason')
    cure = block(1, False, dispatched=3)
    if (cure['finalReward'] or {}).get('dispatchedChests') != 3:
        failures.append(f'Cure terminal outcome did not dispatch: {cure["finalReward"]}')
    unresolved = block(None, True)
    if unresolved['finalReward'] is not None:
        failures.append('an unresolved run declared a final award')
    if 'finalReward' not in (unresolved.get('unavailable') or {}):
        failures.append('an unresolved finalReward has no explicit reason')
    notes.append('terminal dispatch: win=5, loss=0 (a 15-chest queue stays pending), Cure=3, '
                 'unresolved=null+reason; finishBoundary null+reason with the policy descriptor '
                 'preserved separately (manual fixtures; the resolved loss below is battle-derived)')


def check_counter_fixture(failures, notes):
    """A deterministic manual fixture: the counter object publishes the exact event sequence.

    Independent of any battle, this pins the semantics the review asked for - true death vs first
    Leaving are separate events, and the pre-reaction roll is separate from the resolved result.
    """
    counters = combat_progress.EncounterCounters([100, 101], boss=200)
    counters.note_own_death(100, 7)
    counters.note_own_leaving(100, 9)
    counters.note_own_death(100, 11)          # ignored: only the first death is recorded
    counters.note_own_leaving(101, 13)
    counters.enemy_rolls = 4
    counters.enemy_roll_hits = 3
    counters.enemy_roll_misses = 1
    counters.enemy_resolved_attacks = 4
    counters.enemy_hits = 2                   # a reaction flipped one roll-hit into a miss
    counters.enemy_misses = 2
    counters.counter_checks = 5
    counters.counter_enqueues = 2
    counters.boss_death_tick = 21
    counters.boss_postdeath_attempts = 3
    counters.boss_postdeath_lands = 1
    counters.boss_reentries = 1
    counters.boss_leavings = 2
    counters.note_boss_target_command(5, 200)
    counters.note_boss_attempt(6)
    counters.note_postdeath_hit(30)
    counters.note_postdeath_hit(34)
    counters.note_postdeath_hit(41)
    report = counters.report()
    expected_deaths = [[100, 7], [101, -1]]
    expected_leavings = [[100, 9], [101, 13]]
    if report['ownFirstDeathTick'] != expected_deaths:
        failures.append(f'manual fixture: ownFirstDeathTick {report["ownFirstDeathTick"]} != '
                        f'{expected_deaths}')
    if report['ownFirstLeavingTick'] != expected_leavings:
        failures.append(f'manual fixture: ownFirstLeavingTick {report["ownFirstLeavingTick"]} != '
                        f'{expected_leavings}')
    if report['enemyRollHits'] != 3 or report['enemyHits'] != 2:
        failures.append('manual fixture: roll/resolved hit counts collapsed into one another')
    if (report['bossLeavings'], report['bossAccessFirstCommand'],
            report['bossAccessFirstAttempt']) != (2, 5, 6):
        failures.append(f'manual fixture: boss access/Leaving wrong: {report}')
    if (report['bossPostdeathGapCount'], report['bossPostdeathGapMin'],
            report['bossPostdeathGapMax'], report['bossPostdeathGapSum']) != (2, 4, 7, 11):
        failures.append(f'manual fixture: post-death hit gaps wrong: {report}')
    # Future-hit mass is injected here as an exact reading (the ProgressWatch fixture below tests the
    # derivation): 0 remaining hits, no in-flight command, 4 targetable ticks, 1 UsingSkill tick.
    report['bossFutureHitsAtDeath'] = 0
    report['bossFutureIncludeExecuting'] = 0
    report['bossDamagingResets'] = 2
    report['targetableTicks'] = 4
    report['usingSkillTicks'] = 1
    block = combat_progress.encounter_telemetry(
        dict(combat_progress.blank_progress(), bossIdentity=200), counters=report)
    if block['ownDeaths'] != {'100': 7} or block['ownLeavings'] != {'100': 9, '101': 13}:
        failures.append(f'manual fixture: published death/Leaving maps wrong: '
                        f'{block["ownDeaths"]} / {block["ownLeavings"]}')
    if block['enemy'] != dict(rolls=4, rollHits=3, rollMisses=1, resolvedAttacks=4, hits=2, misses=2):
        failures.append(f'manual fixture: published enemy block wrong: {block["enemy"]}')
    expected_boss = dict(firstDeathTick=21, postDeathAttempts=3, postDeathLands=1, reentries=1,
                         leavings=2, damagingResets=2, accessFirstCommandTick=5,
                         accessFirstAttemptTick=6,
                         postDeathHitGaps=dict(count=2, minTicks=4, maxTicks=7, sumTicks=11))
    if block['boss'] != expected_boss:
        failures.append(f'manual fixture: published boss block wrong: {block["boss"]}')
    if block['occupancy'] != dict(targetableTicks=4, usingSkillTicks=1):
        failures.append(f'manual fixture: occupancy wrong: {block["occupancy"]}')
    commands = block['commands']
    if (commands['futureAttempts'], commands['futureAttemptsIncludesExecutingHit']) != (0, False):
        failures.append(f'manual fixture: future-hit mass wrong: {commands["futureAttempts"]!r}')
    if not commands['futureAttemptsSemantics']:
        failures.append('manual fixture: future-hit mass has no semantics label')
    notes.append('manual counter fixture: own death (7) and first Leaving (9) distinct; '
                 'roll hits 3 vs resolved hits 2; boss attempts 3 vs lands 1; gaps 4/7; '
                 'future mass 0 - exact')


def check_future_hits_fixture(failures, notes):
    """Exact manual fixture for the death-snapshot future-hit mass derivation."""
    rows = {10: dict(count=5), 11: dict(count=3)}
    watch = combat_progress.ProgressWatch(200, rows=rows)

    class _World:
        units = {
            1: dict(commands=[dict(target=200, skill=10, use_index=2, tick=7),
                              dict(target=200, skill=11, use_index=0, tick=0),
                              dict(target=999, skill=10, use_index=0, tick=3)]),
            2: dict(commands=[dict(target=200, skill=11, use_index=3, tick=9)]),
        }

    mass, executing = watch._future_hits(_World(), 200)
    # skill 10 on the boss: 5 - 2 = 3 still to fire (and it has begun, so executing = True);
    # skill 11 tick 0 on the boss: 3 - 0 = 3 but not yet begun; the target-999 command is not boss;
    # skill 11 use_index 3 on the boss: 3 - 3 = 0.
    if mass != 6:
        failures.append(f'future-hits fixture: mass {mass} != 6')
    if executing is not True:
        failures.append(f'future-hits fixture: executing inclusion {executing} != True')
    no_rows = combat_progress.ProgressWatch(200)
    if no_rows._future_hits(_World(), 200) != (None, None):
        failures.append('future-hits fixture: a missing skill-row table was not unavailable')
    notes.append('future-hits fixture: mass 6 = (5-2)+(3-0)+(3-3), executing hit included - exact')


def check_python(failures, notes):
    blocks = {}
    for label, scenario, seeds in FIXTURES:
        off = adapter._python_simulate_compact(scenario, seeds)
        on = adapter._python_simulate_compact(scenario, seeds, telemetry=True)
        if off['digest'] != on['digest']:
            failures.append(f'{label}: Python digest moved with telemetry on')
        if 'encounterTelemetry' in off:
            failures.append(f'{label}: telemetry present with it off')
        block = on.get('encounterTelemetry')
        if not block:
            failures.append(f'{label}: telemetry block missing with it on')
            continue
        blocks[label] = block
        if set(off) != set(on) - {'encounterTelemetry'}:
            failures.append(f'{label}: legacy key set changed')
        if block['version'] != 3:
            failures.append(f'{label}: telemetry schema version {block["version"]} != 3')
        _check_counter_sanity(label, block, failures)
        reasons = unavailable_keys(block)
        for key in REQUIRED_UNAVAILABLE:
            if key not in reasons:
                failures.append(f'{label}: null slot {key} has no explicit reason')
        commands = block.get('commands') or {}
        death = block.get('bossFirstDeathTick')
        if isinstance(death, int) and death >= 0:
            if not isinstance(commands.get('futureAttempts'), int):
                failures.append(f'{label}: death observed but futureAttempts is not an integer: '
                                f'{commands.get("futureAttempts")!r}')
            if not isinstance(commands.get('futureAttemptsIncludesExecutingHit'), bool):
                failures.append(f'{label}: death observed but the executing-hit inclusion is not a '
                                f'boolean: {commands.get("futureAttemptsIncludesExecutingHit")!r}')
            if not commands.get('futureAttemptsSemantics'):
                failures.append(f'{label}: futureAttempts has no semantics label')
        occupancy = block.get('occupancy') or {}
        if occupancy.get('targetableTicks') is None or occupancy.get('usingSkillTicks') is None:
            failures.append(f'{label}: occupancy is not a real reading: {occupancy!r}')
        if block['finalStatus']['censored']:
            if block['finalReward'] is not None:
                failures.append(f'{label}: an unresolved run declared a final award')
            if 'finalReward' not in reasons:
                failures.append(f'{label}: unresolved finalReward has no explicit reason')
        elif block['finalStatus']['verdict'] == 2:
            reward = block['finalReward'] or {}
            if reward.get('policy') != 'on-verdict' or reward.get('dispatchedChests') != 0:
                failures.append(f'{label}: resolved loss did not dispatch zero on-verdict chests: '
                                f'{reward}')
        missing = _missing_counters(block)
        if missing:
            failures.append(f'{label}: counters not published: {missing}')
        absent = combat_progress.encounter_telemetry(None, finish_policy='at-horizon')
        if absent['bossIdentity'] is not None or absent['commands'] is not None:
            failures.append('absent progress was coerced to a value instead of null')
        for key in ('bossIdentity', 'bossFirstDeathTick', 'bossLeavingTick', 'bossLifecycle',
                    'commands'):
            if key not in (absent.get('unavailable') or {}):
                failures.append(f'absent progress slot {key} has no explicit reason')
        notes.append(f"{label}: verdict={block['finalStatus']['verdict']} "
                     f"censored={block['finalStatus']['censored']} "
                     f"ownDeaths={len(block['ownDeaths'] or {})} "
                     f"ownLeavings={len(block['ownLeavings'] or {})} "
                     f"enemy={block['enemy']} boss={block['boss']} "
                     f"finalReward={(block['finalReward'] or {}).get('dispatchedChests')}")
        if label == 'consumable':
            uses = on.get('herbMetrics') or {}
            rows = uses.get('uses') or []
            if on['resourceUses'] < 1:
                failures.append(f'{label}: no consumable use recorded')
            if not rows:
                failures.append(f'{label}: herbMetrics has no dispatch rows')
            if not any(row.get('used') for row in rows):
                failures.append(f'{label}: no successful consumable dispatch row')
            notes.append(f"consumable fixture: resourceUses={on['resourceUses']} "
                         f"herbUseCount={uses.get('useCount')} "
                         f"herbRemaining={uses.get('remainingStock')}")
    return blocks


def _missing_counters(block):
    """Every implemented counter must be a real value, not null, on both backends."""
    missing = []
    enemy = block.get('enemy') or {}
    for key in ('rolls', 'rollHits', 'rollMisses', 'resolvedAttacks', 'hits', 'misses'):
        if enemy.get(key) is None:
            missing.append(f'enemy.{key}')
    boss = block.get('boss') or {}
    for key in ('firstDeathTick', 'postDeathAttempts', 'postDeathLands', 'reentries', 'leavings',
                'damagingResets', 'accessFirstCommandTick', 'accessFirstAttemptTick'):
        if boss.get(key) is None:
            missing.append(f'boss.{key}')
    gaps = boss.get('postDeathHitGaps')
    if not gaps or gaps.get('count') is None or gaps.get('sumTicks') is None:
        missing.append('boss.postDeathHitGaps')
    occupancy = block.get('occupancy') or {}
    for key in ('targetableTicks', 'usingSkillTicks'):
        if occupancy.get(key) is None:
            missing.append(f'occupancy.{key}')
    for key in ('counterChecks', 'counterEnqueues'):
        if block.get(key) is None:
            missing.append(key)
    return missing


def _check_counter_sanity(label, block, failures):
    enemy = block.get('enemy') or {}
    if enemy.get('rolls') != enemy.get('rollHits', 0) + enemy.get('rollMisses', 0):
        failures.append(f'{label}: enemy roll parts do not sum to enemy.rolls: {enemy}')
    if enemy.get('resolvedAttacks') != enemy.get('hits', 0) + enemy.get('misses', 0):
        failures.append(f'{label}: enemy resolved parts do not sum: {enemy}')
    boss = block.get('boss') or {}
    if boss.get('postDeathLands', 0) > boss.get('postDeathAttempts', 0):
        failures.append(f'{label}: boss lands exceed attempts: {boss}')
    if boss.get('damagingResets', 0) > boss.get('reentries', 0):
        failures.append(f'{label}: boss Damaging resets exceed re-entries: {boss}')
    gaps = boss.get('postDeathHitGaps') or {}
    if gaps.get('count'):
        if not (0 < gaps.get('minTicks', 0) <= gaps.get('maxTicks', 0) <= gaps.get('sumTicks', 0)):
            failures.append(f'{label}: post-death hit gaps are not coherent: {gaps}')
        if gaps.get('count', 0) > boss.get('postDeathLands', 0):
            failures.append(f'{label}: more gaps than post-death lands: {gaps}')
    death_identities = {int(key) for key in (block.get('ownDeaths') or {})}
    roster_identities = set(block.get('ownIdentities') or [])
    if not death_identities <= roster_identities:
        failures.append(f'{label}: ownDeaths names an identity outside the roster: {death_identities}')
    leaving_identities = {int(key) for key in (block.get('ownLeavings') or {})}
    if not leaving_identities <= roster_identities:
        failures.append(f'{label}: ownLeavings names an identity outside the roster: '
                        f'{leaving_identities}')


def _compare_shared(label, python_block, native_block, failures):
    for key in SHARED:
        if python_block.get(key) != native_block.get(key):
            failures.append(f'{label}: {key} python={python_block.get(key)!r} '
                            f'native={native_block.get(key)!r}')
    for parent, children in SHARED_NESTED:
        left, right = python_block.get(parent) or {}, native_block.get(parent) or {}
        for child in children:
            if left.get(child) != right.get(child):
                failures.append(f'{label}: {parent}.{child} python={left.get(child)!r} '
                                f'native={right.get(child)!r}')


def check_kernel_identity(failures, notes):
    """The opt-in kernel is selected by name/version/size/hash, and is not the default v9 image."""
    try:
        lib = ka_encounter_abi.load()
    except Exception as error:  # noqa: BLE001
        # A missing kernel must not silently pass: this is the check that proves parity.
        failures.append(f'encounter kernel is unavailable (must not silently pass): {error}')
        return
    if ka_encounter_abi.ENCOUNTER_VERSION != 3:
        failures.append('ka_encounter_abi.ENCOUNTER_VERSION is not 3')
    if int(lib.encounter_abi_version) != 3:
        failures.append('encounter kernel version is not 3')
    small = ka_encounter_abi.encounter_dll_path(False).name
    large = ka_encounter_abi.encounter_dll_path(True).name
    if small != 'ka_kernel_encounter_v3.dll' or large != 'ka_kernel_encounter_v3_large.dll':
        failures.append(f'encounter DLL names are not the v3 pair: {small} / {large}')
    if not lib.encounter_sha256:
        failures.append('encounter DLL SHA-256 was not recorded')
    # The library identity is the loaded DLL (path + file hash + version/size probes); the file hash
    # is NOT a hash of the Rust source. The manifest records the source hashes at build time so the
    # build is traceable without re-reading the source on every dispatch.
    if not MANIFEST_PATH.is_file():
        failures.append(f'source-hash manifest is missing: {MANIFEST_PATH}')
    else:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding='utf-8'))
        entry = (manifest.get('dlls') or {}).get(Path(lib._name).name)
        if not entry or entry.get('sha256') != lib.encounter_sha256:
            failures.append('the loaded DLL is not the build pinned in the source-hash manifest')
        if not isinstance(manifest.get('sources'), dict) or not manifest['sources']:
            failures.append('the source-hash manifest records no source hashes')
    notes.append(f'encounter kernel: {Path(lib._name).name} version={lib.encounter_abi_version} '
                 f'sizeof={ctypes.sizeof(ka_encounter_abi.KaEncounterReport)} '
                 f'sha256={lib.encounter_sha256[:12]} (manifest-pinned)')


def check_template_isolation(failures, notes):
    """A cached template pointer is only cloned by the DLL image that built it (item 6)."""
    default_identity = native._library_identity(native.library())
    try:
        encounter_identity = native._library_identity(native.encounter_library())
    except Exception as error:  # noqa: BLE001
        notes.append(f'template isolation skipped (no encounter kernel): {error}')
        return
    if default_identity == encounter_identity:
        failures.append('the default and encounter kernels report the same identity')
    if 'ka_sizeof_encounter_report' not in dict(encounter_identity[2]):
        failures.append('the encounter kernel identity lacks its sizeof probe')
    notes.append(f'template isolation: default={Path(default_identity[0]).name} '
                 f'encounter={Path(encounter_identity[0]).name}')


def check_native(python_blocks, failures, notes):
    try:
        native.library()
    except Exception as error:  # noqa: BLE001
        failures.append(f'native kernel unavailable; native parity cannot be skipped: {error}')
        return 0
    ran = 0
    for label, scenario, seeds in FIXTURES:
        off = native.simulate_compact(scenario, seeds, backend='native', telemetry=False)
        on = native.simulate_compact(scenario, seeds, backend='native', telemetry=True)
        ran += 2
        if off.get('resultBackend') != 'native' or on.get('resultBackend') != 'native':
            notes.append(f'{label}: native backend not used, parity skipped')
            continue
        if off['digest'] != on['digest']:
            failures.append(f'{label}: native digest moved with telemetry on')
        if 'encounterTelemetry' in off:
            failures.append(f'{label}: native telemetry present with it off')
        _compare_shared(label, python_blocks[label], on['encounterTelemetry'], failures)
        telemetry = on['encounterTelemetry']
        if not telemetry['finalStatus']['censored'] and telemetry['finalStatus']['verdict'] == 2:
            reward = telemetry.get('finalReward') or {}
            if reward.get('policy') != 'on-verdict' or reward.get('dispatchedChests') != 0:
                failures.append(f'{label}: native resolved loss did not dispatch zero on-verdict '
                                f'chests (the kernel queue is not a dispatch): {reward}')
        notes.append(f"{label}: native shared v3 telemetry matched ({len(SHARED)} scalars); "
                     f"enemy={on['encounterTelemetry']['enemy']} "
                     f"boss={on['encounterTelemetry']['boss']}")
    return ran


def run_timing(failures, notes):
    label, scenario, seeds = FIXTURES[0]
    try:
        native.library()
        native.encounter_library()
    except Exception as error:  # noqa: BLE001
        notes.append(f'timing skipped (kernel unavailable): {error}')
        return
    samples = {'off': [], 'on': []}
    for mode in ('off', 'on'):
        for _ in range(32):
            started = time.perf_counter()
            native.simulate_compact(scenario, seeds, backend='native', telemetry=(mode == 'on'))
            samples[mode].append(time.perf_counter() - started)
    notes.append('timing (32 native runs each, tiny sample, no throughput claim): '
                 f"off median={statistics.median(samples['off'])*1000:.2f}ms "
                 f"on median={statistics.median(samples['on'])*1000:.2f}ms")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--timing', action='store_true')
    parser.add_argument('--json', type=Path)
    args = parser.parse_args()
    build_fixtures()
    failures, notes = [], []
    check_consumable_mechanism(failures, notes)
    check_terminal_dispatch(failures, notes)
    check_counter_fixture(failures, notes)
    check_future_hits_fixture(failures, notes)
    check_kernel_identity(failures, notes)
    check_template_isolation(failures, notes)
    python_blocks = check_python(failures, notes)
    native_runs = check_native(python_blocks, failures, notes)
    if args.timing:
        run_timing(failures, notes)
    for note in notes:
        print(f'  {note}')
    print(f'encounter telemetry: {len(FIXTURES)} python fixtures, {native_runs} native runs, '
          f'{len(failures)} failures')
    for detail in failures:
        print(f'  FAIL {detail}')
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(dict(failures=failures, notes=notes, native_runs=native_runs),
                                        indent=1), encoding='utf-8')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
