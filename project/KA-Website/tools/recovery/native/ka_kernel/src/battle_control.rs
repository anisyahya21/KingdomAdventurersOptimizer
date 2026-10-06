//! Battle control: the tick head, the verdict, the Ending gate, the finish policies and the
//! read-only reward-entitlement observer.
//!
//! Mirrors `combat_ending` (`advance_battle_frame`, `is_annihilated`, `enter_ending`,
//! `update_ending`/`after_ending_confirmation`), the head of `SharedControllers.run`, and the
//! observable state of `combat_reward_entitlement.RewardEntitlementWatch`.
//!
//! The report's free-text fields (`limits`, the entitlement prose) are not reproduced: they are
//! presentation, and the optimiser reads the structured fields compared here.

use crate::ai::{trunc_div, KA_ERR_UNSUPPORTED_STATE};
use crate::combat::KA_ERR_UNSUPPORTED_ROUTE;
use crate::phases;

// The release kernel compiles these calls away. The profiling DLL is built separately so the
// running optimiser never loads timing instrumentation by accident.
macro_rules! timed_phase {
    ($index:expr, $call:expr) => {{
        #[cfg(feature = "profile")]
        let started = std::time::Instant::now();
        let result = $call;
        #[cfg(feature = "profile")]
        crate::profile::record($index, started.elapsed());
        result
    }};
}
use crate::rng::i32_of;
use crate::state::*;
use crate::world::*;

/// `combat_ending.advance_battle_frame`.
#[inline]
pub fn advance_battle_frame(frame: i32) -> i32 {
    let value = i32_of(frame as i64 + 1);
    i32_of(value as i64 - trunc_div(value, 2147483647) as i64 * 2147483647)
}

/// `combat_ending.is_annihilated`: every member of the roster is in Leaving (state 8).
pub fn is_annihilated(battle: &KaBattle, team: i32) -> bool {
    let mut roster = [0i32; KA_MAX_UNITS];
    let len = battle.roster(team, &mut roster);
    for slot in 0..len {
        let index = roster[slot] as usize;
        if battle.units[index].state() != 8 {
            return false;
        }
    }
    true
}

/// `combat_ending.update_ending` with a declared confirmation (`key_pulse=True`).
pub fn update_ending(frame: i32, winner: i32) -> (i32, bool) {
    if frame <= 79 {
        (80, false)
    } else {
        let _ = winner;
        (frame, true)
    }
}

/// `combat_ending.enter_ending`: reset every non-7/8 fighter to state 0, then read the verdict off
/// the own team.
pub fn enter_ending(battle: &mut KaBattle) -> Result<i32, i32> {
    for team in 0..2i32 {
        let mut roster = [0i32; KA_MAX_UNITS];
        let len = battle.roster(team, &mut roster);
        for slot in 0..len {
            let index = roster[slot] as usize;
            let state = battle.units[index].state();
            if state != 7 && state != 8 {
                crate::ai::change(battle, index, 0)?;
            }
        }
    }
    Ok(if is_annihilated(battle, 0) { 2 } else { 1 })
}

/// The tick head from `SharedControllers.run`, before the fighters phase.
pub fn tick_head(battle: &mut KaBattle) -> Result<(), i32> {
    battle.tick = battle.tick.wrapping_add(1);
    battle.battle_frame = advance_battle_frame(battle.battle_frame);
    if battle.battle_state == 2 && (is_annihilated(battle, 0) || is_annihilated(battle, 1)) {
        battle.battle_frame = 0;
        battle.verdict = enter_ending(battle)?;
        battle.verdict_tick = battle.tick as i32;
        battle.prizes_at_verdict = battle.prize_count as i32;
        battle.battle_state = 3;
        // The sandbox's `verdict` event is emitted with the result.
        battle.push_event(KA_EVENT_VERDICT, battle.verdict, battle.tick as i32, 0, 0, 0, 0);
    }
    Ok(())
}

/// `RewardEntitlementWatch.observe` - read-only, run after the fighters phase each tick.
pub fn observe(battle: &mut KaBattle) {
    battle.observations += 1;
    let pending = battle.prize_count as i32;
    battle.pending_final = pending;
    // The single rival leader.
    let mut boss = -1i32;
    for index in 0..battle.count as usize {
        if battle.units[index].boss != 0 {
            boss = battle.units[index].identity;
            break;
        }
    }
    if boss >= 0 {
        let index = match battle.fighter_slot(boss) {
            Some(index) => index,
            None => return,
        };
        battle.boss_hp = battle.units[index].hp();
        battle.boss_state = battle.units[index].state();
        let mut stored = 0i32;
        let mut queued = 0i32;
        for other in 0..battle.count as usize {
            if battle.units[other].long_board.get(16) == Some(boss as i64) {
                stored += 1;
            }
            if battle.units[other].command_count > 0 {
                queued += 1;
            }
        }
        battle.stored_target_holders = stored;
        battle.queued_command_holders = queued;
        battle.clauses = [
            u8::from(battle.boss_hp == 0),
            u8::from(battle.boss_state == 8),
            u8::from(stored == 0),
            u8::from(queued == 0),
        ];
    } else {
        return;
    }
    if battle.verdict != 0 {
        battle.verdict_observations += 1;
        if battle.pending_at_verdict < 0 {
            battle.pending_at_verdict = pending;
        }
    }
    if battle.certificate_held == 0 {
        if battle.scope_allowed != 0 && battle.clauses.iter().all(|value| *value != 0) {
            if battle.verdict == 0 {
                battle.certificate_held = 1;
                battle.certificate_frame = battle.tick as i32;
                battle.certificate_pending = pending;
            } else if battle.late_hold_frame < 0 {
                battle.late_hold_frame = battle.tick as i32;
            }
        }
    } else if pending != battle.certificate_pending {
        battle.post_certificate_delta = pending - battle.certificate_pending;
    }
}

/// `combat_progress.boss_identity(specs)`: the single rival leader, in identity order. Called once,
/// before the first fighters phase, so an enqueue in that phase is already attributed.
#[inline]
fn progress_resolve_boss(battle: &mut KaBattle) {
    if battle.progress_boss >= 0 {
        return;
    }
    if battle.progress_boss == -2 {
        return; // already looked for and found none; nothing to attribute
    }
    let mut found = -2i32;
    for index in 0..battle.count as usize {
        if battle.units[index].boss != 0 {
            found = battle.units[index].identity;
            break;
        }
    }
    battle.progress_boss = found;
}

/// `ProgressWatch._queued`: every queued command across all fighters, and those pointing at the boss.
#[inline]
fn queued_counts(battle: &KaBattle, boss: i32) -> (i32, i32) {
    let mut total = 0i32;
    let mut targeting = 0i32;
    for index in 0..battle.count as usize {
        let count = battle.units[index].command_count as usize;
        total += count as i32;
        targeting += battle.units[index].commands[..count]
            .iter()
            .filter(|command| command.target == boss)
            .count() as i32;
    }
    (total, targeting)
}

/// `ProgressWatch._stored_holders`: fighters whose stored normal-attack target (BKL:16) is the boss.
#[inline]
fn stored_holder_count(battle: &KaBattle, boss: i32) -> i32 {
    let mut holders = 0i32;
    for index in 0..battle.count as usize {
        if battle.units[index].long_board.get(16) == Some(boss as i64) {
            holders += 1;
        }
    }
    holders
}

/// `ProgressWatch.enqueue`: one stored command entered a fighter's queue. The target is the raw
/// argument `SharedControllers.enqueue` received, exactly as the Python observer sees it.
#[inline]
pub fn progress_enqueue(battle: &mut KaBattle, target: i32) {
    if battle.progress_boss >= 0 && target == battle.progress_boss {
        battle.progress_commands_targeting_boss += 1;
        if battle.progress_boss_access_first_command < 0 {
            battle.progress_boss_access_first_command = battle.tick as i32;
        }
    }
}

/// `ProgressWatch._future_hits`: the boss-targeting command hit attempts still to fire, plus whether
/// the next (currently executing) hit of an in-flight command is part of that mass. `use_index`
/// advances when a hit fires, so a hit already resolved - including the hit executing at the sample -
/// is excluded; a command that has begun (`tick > 0`) with hits left contributes its next one.
#[inline]
fn queued_future_hits(battle: &KaBattle, boss: i32) -> (i32, i32) {
    let mut hits = 0i32;
    let mut executing = 0i32;
    for index in 0..battle.count as usize {
        let count = battle.units[index].command_count as usize;
        for command in &battle.units[index].commands[..count] {
            if command.target != boss {
                continue;
            }
            let row_count = match battle.row(command.skill) {
                Some(row) => row.count,
                None => continue,
            };
            let remaining = (row_count - command.use_index).max(0);
            hits += remaining;
            if remaining > 0 && command.tick > 0 {
                executing = 1;
            }
        }
    }
    (hits, executing)
}

/// `ProgressWatch.release`: a stored command's head completed and was removed. Never called on a
/// queue clear (knockdown, teardown) - those drop commands without releasing them.
///
/// The boss's HP is probed here, at the instant of the release, because the per-tick sample only runs
/// after the whole fighters phase: a command released later in the SAME phase as the killing blow
/// would otherwise look like it preceded the death. The probe makes the ordering explicit, and the
/// in-phase latch keeps the sample from overwriting the death tick with its own later reading.
#[inline]
pub fn progress_release(battle: &mut KaBattle, command: &KaCommand) {
    let boss = battle.progress_boss;
    if boss >= 0 && command.target == boss {
        battle.progress_commands_targeting_boss_released += 1;
    }
    let boss_dead_now = boss >= 0
        && battle.fighter_slot(boss).map(|index| battle.units[index].hp() == 0).unwrap_or(false);
    if boss_dead_now && battle.progress_boss_death_tick < 0 {
        battle.progress_boss_death_tick = battle.tick as i32;
        battle.progress_death_latched_in_phase = 1;
    }
    let tick = battle.tick as i32;
    let same_tick_death = boss_dead_now && tick == battle.progress_boss_death_tick;
    if battle.progress_boss_death_tick >= 0
        && (tick > battle.progress_boss_death_tick || same_tick_death)
    {
        battle.progress_released_after_death += 1;
        if command.target == boss {
            battle.progress_released_after_death_targeting_boss += 1;
        }
        if battle.progress_first_release_after_death < 0 {
            battle.progress_first_release_after_death = tick;
        }
        battle.progress_last_release_after_death = tick;
    }
}

/// `ProgressWatch.observe`: one per-tick sample of the stored-attack chain, at the same seam as the
/// entitlement observer (after the fighters phase, before any trailing phase).
pub fn progress_observe(battle: &mut KaBattle) {
    let boss = battle.progress_boss;
    if boss < 0 {
        return;
    }
    let index = match battle.fighter_slot(boss) {
        Some(index) => index,
        None => return,
    };
    let tick = battle.tick as i32;
    let hp = battle.units[index].hp();
    let state = battle.units[index].state();
    if hp == 0 && battle.progress_boss_death_tick < 0 {
        battle.progress_boss_death_tick = tick;
        let (total, targeting) = queued_counts(battle, boss);
        battle.progress_stored_at_death = total;
        battle.progress_stored_targeting_boss_at_death = targeting;
        battle.progress_stored_target_holders_at_death = stored_holder_count(battle, boss);
    }
    if hp == 0 && battle.progress_future_hits_captured == 0 {
        let (mass, executing) = queued_future_hits(battle, boss);
        battle.progress_boss_future_hits_at_death = mass;
        battle.progress_boss_future_includes_executing = executing;
        battle.progress_future_hits_captured = 1;
    }
    if hp == 0 && battle.progress_prizes_at_death < 0 {
        // Latched whoever found the death first (this sample, or a release earlier in the phase), so
        // `post_death_prizes` keeps counting from the death even when a release latched the tick.
        battle.progress_prizes_at_death = battle.prize_count as i32;
    }
    if state == 8 && battle.progress_boss_leaving_tick < 0 {
        battle.progress_boss_leaving_tick = tick;
    }
    if battle.progress_boss_death_tick >= 0 && tick > battle.progress_boss_death_tick {
        // A re-entry is a *change* into the state, exactly as the Python observer counts it: the
        // recovered handlers keep 6 and 8 for at least seven frames, so no transition can hide
        // between two consecutive samples.
        if state == 6 && battle.progress_previous_boss_state != 6 {
            battle.progress_post_death_reentries += 1;
        }
        if state == 8 && battle.progress_previous_boss_state != 8 {
            battle.progress_post_death_leavings += 1;
        }
        if battle.progress_previous_boss_state == 6 && state != 6 {
            battle.progress_boss_damaging_resets += 1;
        }
    }
    battle.progress_previous_boss_state = state;
    // Occupancy is a per-tick sample, never an event.
    if hp > 0 && matches!(state, 1 | 3 | 4 | 6) {
        battle.progress_targetable_ticks += 1;
    }
    if state == 5 {
        battle.progress_using_skill_ticks += 1;
    }
    let (total, targeting) = queued_counts(battle, boss);
    let holders = stored_holder_count(battle, boss);
    battle.progress_max_stored = battle.progress_max_stored.max(total);
    battle.progress_max_targeting_boss = battle.progress_max_targeting_boss.max(targeting);
    battle.progress_target_holders_peak = battle.progress_target_holders_peak.max(holders);
    if battle.progress_prizes_at_death >= 0 {
        battle.progress_post_death_prizes = battle.prize_count as i32 - battle.progress_prizes_at_death;
    }
}

/// `combat_progress.ProgressWatch.observe`'s MP half: one sample per declared trigger unit, plus the
/// live `<=3%` policy's latch.
///
/// It rides the same after-fighters observer the stored-attack chain uses, so there is no second
/// per-tick loop and no callback into Python. With no declared trigger units it returns immediately.
/// Everything it writes is policy/telemetry state that no phase reads back, so a battle with no
/// trigger units - which is every stored scenario - is bit-for-bit unchanged.
pub fn mp_observe(battle: &mut KaBattle) {
    let slots = (battle.mp_watch_count.max(0) as usize).min(KA_MAX_MP_WATCH);
    if slots == 0 {
        return;
    }
    let tick = battle.tick as i32;
    let may_spend = battle.holy_herb_max_uses > 0
        && battle.holy_herb_stock > 0
        && (battle.herb_uses_ok as i32) < battle.holy_herb_max_uses;
    let mut latch = battle.holy_herb_pending;
    for slot in 0..slots {
        let index = match battle.fighter_slot(battle.mp_watch[slot]) {
            Some(index) => index,
            None => continue,
        };
        let (value, percent, alive) = {
            let unit = &battle.units[index];
            let value = unit.mp();
            let percent = crate::ai::effective_parameter_rate(value,
                                                              unit.param_maximum(KA_MP_PARAMETER));
            let state = unit.state();
            (value, percent, state != 7 && state != 8)
        };
        if battle.mp_min[slot] < 0 || value < battle.mp_min[slot] {
            battle.mp_min[slot] = value;
        }
        if battle.mp_min_percent[slot] < 0 || percent < battle.mp_min_percent[slot] {
            battle.mp_min_percent[slot] = percent;
        }
        if value == 0 {
            battle.mp_zero[slot] = 1;
        }
        if percent <= KA_HERB_TRIGGER_PERCENT {
            if battle.mp_low[slot] == 0 {
                battle.mp_low[slot] = 1;
                battle.mp_first_low_tick[slot] = tick;
                // The sample itself happens at the after-fighters seam, which is the phase the
                // reference observer is called with; recorded verbatim rather than guessed.
                battle.mp_first_low_phase[slot] = KA_PHASE_AFTER_FIGHTERS;
            }
            // Latch once per arming: a unit that merely stays below the threshold cannot fire again
            // until the herb has restored it above the threshold and re-armed it.
            if may_spend && alive && battle.holy_herb_armed[slot] != 0 {
                battle.holy_herb_armed[slot] = 0;
                if latch == 0 {
                    latch = slot as i32 + 1;
                }
            }
        } else {
            battle.holy_herb_armed[slot] = 1;
        }
    }
    battle.holy_herb_pending = latch;
}

/// One complete native tick: head, every canonical phase, then the observer.
pub fn tick(battle: &mut KaBattle) -> Result<(), i32> {
    battle.event_count = 0;
    // `combat_progress.ProgressWatch` is constructed with the boss identity read from the roster
    // before the first tick, so a command enqueued during the very first fighters phase is still
    // attributed to the right target. Resolve it here, in identity order, exactly as
    // `combat_progress.boss_identity(specs)` does over the sorted spec mapping.
    progress_resolve_boss(battle);
    timed_phase!(0, tick_head(battle))?;
    // `input_callback(self, 'before_fighters')` runs after the tick head and before the fighters.
    timed_phase!(1, process_inputs(battle, KA_PHASE_BEFORE_FIGHTERS))?;
    timed_phase!(2, crate::ai::update_fighters(battle, battle.battle_state))?;
    // The sandbox's `observed_inputs` runs `inputs(world, phase)` and then the reward observer, both
    // under the single `after_fighters` callback, so the consumables run first.
    timed_phase!(1, process_inputs(battle, KA_PHASE_AFTER_FIGHTERS))?;
    timed_phase!(3, observe(battle));
    // The stored-attack observer rides the same seam, immediately after the entitlement observer.
    timed_phase!(3, progress_observe(battle));
    // MP telemetry and the live policy's latch ride the same after-fighters seam: the crossing is
    // detected at the end of this tick and consumed by the *next* tick's input seam, which is exactly
    // the reference sandbox's ordering.
    timed_phase!(3, mp_observe(battle));
    timed_phase!(4, phases::ported_prefix(battle))?;
    timed_phase!(5, phases::trailing_phases(battle))?;
    // `preVerdictPrizeCallbacks` counts prize events with `tick <= verdictTick`, so a prize awarded
    // during the verdict tick itself still counts; the snapshot is refreshed at the end of that tick.
    if battle.verdict != 0 && battle.tick as i32 == battle.verdict_tick {
        battle.prizes_at_verdict = battle.prize_count as i32;
    }
    Ok(())
}

/// `combat_consumables.recover_item_parameter`: every target is visited in order, with no early
/// exit; success is the OR of the member tests; a non-human can report success without gaining
/// anything, and a fully-healed group reports failure.
/// `source` is the dispatch label the canonical `resource_change` carries: `-1` for a Holy Herb,
/// otherwise the battle-item table index. It rides in the event's `d` word.
fn recover_parameter(battle: &mut KaBattle, targets: &[i32], parameter: i32, percent: i32,
                     source: i32) -> bool {
    let mut success = false;
    for identity in targets {
        let index = match battle.fighter_slot(*identity) {
            Some(index) => index,
            None => continue,
        };
        if param_rate(battle, index, parameter) >= 100 {
            continue;
        }
        success = true;
        let maximum = battle.units[index].param_maximum(parameter);
        let amount = trunc_div(i32_of(maximum as i64 * percent as i64), 100);
        // `human_with_parameters` is the sandbox's `lambda i: specs[i]['human']`, so a non-human
        // contributes to success without gaining anything (the recovered quirk).
        if battle.units[index].human == 0 || battle.units[index].params.find(parameter).is_none() {
            continue;
        }
        let before = battle.units[index].param_value(parameter, 0);
        battle.units[index].params.add_raw(parameter, amount, maximum);
        let after = battle.units[index].param_value(parameter, 0);
        if after != before {
            battle.push_event(KA_EVENT_RESOURCE_CHANGE, *identity, parameter, after, before,
                              source, maximum);
        }
    }
    success
}

/// `Param.GetRate` for an arbitrary parameter id: `effective_parameter_rate(value, maximum)`.
fn param_rate(battle: &KaBattle, index: usize, parameter: i32) -> i32 {
    let unit = &battle.units[index];
    crate::ai::effective_parameter_rate(unit.param_value(parameter, 0),
                                       unit.param_maximum(parameter))
}

/// The own-team roster filtered by `state not in (7, 8)` - the caller's array in the sandbox.
fn own_residents(battle: &KaBattle) -> ([i32; KA_MAX_UNITS], usize) {
    let mut roster = [0i32; KA_MAX_UNITS];
    let len = battle.roster(0, &mut roster);
    let mut out = [0i32; KA_MAX_UNITS];
    let mut count = 0usize;
    for slot in 0..len {
        let index = roster[slot] as usize;
        let state = battle.units[index].state();
        if state != 7 && state != 8 {
            out[count] = battle.units[index].identity;
            count += 1;
        }
    }
    (out, count)
}

/// `combat_consumables.use_battle_holy_herb`: own roster filtered by state, stock gate first, then
/// the parameter-11 recovery at a fixed 100 percent (so no RNG is consumed). Stock is spent only on
/// canonical success.
/// `source` is the authorising trigger slot for the live policy, `-1` for a prescribed input; it is
/// recorded in the explicit Holy Herb log so a run can say which unit spent the charge.
fn use_holy_herb(battle: &mut KaBattle, phase: i32, source: i32) {
    let (targets, count) = own_residents(battle);
    if battle.holy_herb_stock <= 0 {
        battle.log_use(KA_INPUT_HOLY_HERB, -1, false, -1, battle.holy_herb_stock);
        battle.log_herb_use(phase, source, false);
        battle.push_event(KA_EVENT_BATTLE_ITEM, -1, 0, -1, battle.holy_herb_stock, 1,
                          count as i32);
        return;
    }
    let success = recover_parameter(battle, &targets[..count], 11, 100, -1);
    if success {
        battle.holy_herb_stock -= 1;
        battle.herb_uses_ok += 1;
    }
    battle.log_use(KA_INPUT_HOLY_HERB, -1, success, 100, battle.holy_herb_stock);
    battle.log_herb_use(phase, source, success);
    battle.push_event(KA_EVENT_BATTLE_ITEM, -1, i32::from(success), 100, battle.holy_herb_stock,
                      0, count as i32);
}

/// `combat_consumables.bonus_value`: `min` when `min >= max` (no draw), else one math draw.
fn bonus_value(battle: &mut KaBattle, item: &KaItem) -> i32 {
    if item.bonus_min >= item.bonus_max {
        return item.bonus_min;
    }
    let raw = battle.draw_math();
    let span = i32_of(item.bonus_max as i64 - item.bonus_min as i64 + 1);
    i32_of(raw as i64 - trunc_div(raw, span) as i64 * span as i64 + item.bonus_min as i64)
}

/// `combat_consumables.use_battle_item` for the reachable all-resident recovery types. Stock is
/// checked first, then the bonus draw happens, then the recovery attempt, and stock is spent only on
/// success.
fn use_item(battle: &mut KaBattle, slot: usize) -> Result<(), i32> {
    let item = match battle.items.get(slot) {
        Some(item) => *item,
        None => return Err(KA_ERR_UNSUPPORTED_STATE),
    };
    if item.all_residents == 0 {
        // The battle touch path supplies no single resident, so `use_battle_item` raises; the
        // canonical battle scope cannot reach these types.
        return Err(KA_ERR_UNSUPPORTED_ROUTE);
    }
    let (targets, count) = own_residents(battle);
    let source = slot as i32;
    if item.stock <= 0 {
        battle.log_use(KA_INPUT_ITEM, source, false, -1, item.stock);
        battle.push_event(KA_EVENT_BATTLE_ITEM, source, 0, -1, item.stock, 1, count as i32);
        return Ok(());
    }
    let percent = bonus_value(battle, &item);
    let success = recover_parameter(battle, &targets[..count], item.parameter, percent, source);
    if success {
        battle.items[slot].stock -= 1;
        battle.item_uses_ok += 1;
    }
    let remaining = battle.items[slot].stock;
    battle.log_use(KA_INPUT_ITEM, source, success, percent, remaining);
    battle.push_event(KA_EVENT_BATTLE_ITEM, source, i32::from(success), percent, remaining, 0,
                      count as i32);
    Ok(())
}

/// `input_callback(world, phase)`: every event whose tick and phase match runs, in declaration
/// order.
pub fn process_inputs(battle: &mut KaBattle, phase: i32) -> Result<(), i32> {
    // The live `<=3%` policy. The crossing is latched by the existing after-fighters observer; the
    // *existing* legal item-action seam consumes it, through the same recovered `use_holy_herb` a
    // prescribed event uses, and before that tick's declared events - exactly as the reference
    // sandbox's `inputs` does.
    let pending = battle.holy_herb_pending;
    if pending != 0 {
        battle.holy_herb_pending = 0;
        use_holy_herb(battle, phase, pending - 1);
    }
    let count = battle.input_count as usize;
    for slot in 0..count {
        let event = battle.inputs[slot];
        if event.tick != battle.tick as i32 || event.phase != phase {
            continue;
        }
        match event.kind {
            KA_INPUT_HOLY_HERB => use_holy_herb(battle, phase, -1),
            KA_INPUT_ITEM => use_item(battle, event.item.max(0) as usize)?,
            _ => return Err(KA_ERR_UNSUPPORTED_STATE),
        }
    }
    Ok(())
}

/// The result the optimiser consumes, mirroring `SharedControllers.run`'s return dict.
#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct KaBattleReport {
    pub status: i32,
    pub ticks: i32,
    pub verdict: i32,
    pub verdict_tick: i32,
    pub battle_state: i32,
    pub battle_frame: i32,
    pub finish_policy: i32,
    pub ending_gate_tick: i32,
    pub ending_confirmed: i32,
    pub ending_counter: i32,
    pub prize_callbacks: i32,
    pub pre_verdict_prize_callbacks: i32,
    pub unresolved_commands: i32,
    pub pending_projectiles: i32,
    pub active_damage_or_leaving: i32,
    pub math_draws: i32,
    pub lib_draws: i32,
    pub certificate_held: i32,
    pub certificate_frame: i32,
    pub certificate_pending: i32,
    pub late_hold_frame: i32,
    pub pending_at_verdict: i32,
    pub pending_final: i32,
    pub post_certificate_delta: i32,
    pub observations: i32,
    pub verdict_observations: i32,
    pub clauses: [i32; 4],
    pub boss_hp: i32,
    pub boss_state: i32,
    pub stored_target_holders: i32,
    pub queued_command_holders: i32,
    pub scope_allowed: i32,
    /// `metrics.heals` / `metrics.attackAttempts`.
    pub heals: i32,
    pub attack_attempts: i32,
    /// `_own_survivors`: own fighters whose state is not Leaving (8); -1 is the Python `None`.
    pub survivors: i32,
    /// `_own_health_fraction` inputs: Σ own effective HP and Σ own effective HP maximum.
    pub own_hp: i64,
    pub own_hp_max: i64,
    /// `1` when the own roster is describable (the canonical helper's "not None" case).
    pub own_present: i32,
    /// `resourceUses`: successful Holy Herb uses + successful battle-item uses.
    pub resource_uses: i32,
    // ---- Stored-attack progress (`combat_progress.ProgressWatch.report()`) ----
    // Published in the observer's own field order, so the Python/Rust comparison is a plain list
    // equality. These counters are read-only observations and never entered the compact digest.
    pub progress_boss: i32,
    pub progress_boss_death_tick: i32,
    pub progress_boss_leaving_tick: i32,
    pub progress_stored_at_death: i32,
    pub progress_stored_targeting_boss_at_death: i32,
    pub progress_stored_target_holders_at_death: i32,
    pub progress_commands_targeting_boss: i32,
    pub progress_commands_targeting_boss_released: i32,
    pub progress_max_stored: i32,
    pub progress_max_targeting_boss: i32,
    pub progress_target_holders_peak: i32,
    pub progress_post_death_reentries: i32,
    pub progress_post_death_leavings: i32,
    pub progress_post_death_prizes: i32,
    pub progress_released_after_death: i32,
    pub progress_released_after_death_targeting_boss: i32,
    pub progress_first_release_after_death: i32,
    pub progress_last_release_after_death: i32,
    // ---- MP telemetry (`combat_progress.mp_metrics`), one block per declared trigger unit ----
    // Read-only observations, published in the observer's own field order. They are attached to the
    // compact result *after* its digest, so no stored digest moves.
    pub mp_watch_count: i32,
    pub mp_identity: [i32; KA_MAX_MP_WATCH],
    pub mp_min: [i32; KA_MAX_MP_WATCH],
    pub mp_min_percent: [i32; KA_MAX_MP_WATCH],
    pub mp_low: [i32; KA_MAX_MP_WATCH],
    pub mp_first_low_tick: [i32; KA_MAX_MP_WATCH],
    pub mp_first_low_phase: [i32; KA_MAX_MP_WATCH],
    pub mp_zero: [i32; KA_MAX_MP_WATCH],
    // ---- Explicit Holy Herb telemetry (`combat_progress.herb_metrics`) ----
    pub herb_stock_start: i32,
    pub herb_stock_remaining: i32,
    pub herb_max_uses: i32,
    /// `holyHerbUseCount`: successful dispatches only (`herb_uses_ok`).
    pub herb_use_count: i32,
    /// Dispatches recorded in the explicit log (the total, even past the cap).
    pub herb_log_count: i32,
    pub herb_use_tick: [i32; KA_MAX_HERB_USES],
    pub herb_use_phase: [i32; KA_MAX_HERB_USES],
    pub herb_use_source: [i32; KA_MAX_HERB_USES],
    pub herb_use_ok: [i32; KA_MAX_HERB_USES],
}

/// `ka_run_battle`: run the whole battle natively and fill the report.
/// The additive, versioned encounter-telemetry report. It is published only through the separate
/// `ka_encounter_report` getter, in a distinct `ka_kernel_encounter_v1.dll`, so the default
/// `KaBattleReport` layout (and every legacy run that loads `ka_kernel_v9.dll`) is unchanged.
/// Every field is a plain counter read from state no phase reads back.
#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct KaEncounterReport {
    /// Schema version of this struct (`KA_ENCOUNTER_VERSION`).
    pub version: i32,
    /// Number of own-team (team 0) roster slots, in roster order.
    pub own_count: i32,
    pub own_identity: [i32; KA_MAX_UNITS],
    /// Own-team first HP-positive -> zero tick per slot (the true death); `-1` = never died.
    pub own_first_death_tick: [i32; KA_MAX_UNITS],
    /// Own-team first Leaving (state 8) entry tick per slot; `-1` = never left. Distinct from the
    /// death: the HP transition and the Leaving entry are separate events.
    pub own_first_leaving_tick: [i32; KA_MAX_UNITS],
    /// `1` when the slot never died (`own_first_death_tick < 0`).
    pub own_survived: [i32; KA_MAX_UNITS],
    /// Enemy (team != 0) resolved attacks, post-reaction, against a target that existed at roll time.
    pub enemy_resolved_attacks: i32,
    /// Of the resolved attacks, the post-reaction hits / misses.
    pub enemy_hits: i32,
    pub enemy_misses: i32,
    /// The pre-reaction hit roll for the same enemy attacks (before any kind-24 reaction flips it).
    pub enemy_rolls: i32,
    pub enemy_roll_hits: i32,
    pub enemy_roll_misses: i32,
    pub counter_checks: i32,
    pub counter_enqueues: i32,
    /// Boss attacks attempted while its HP was already zero (including misses), and of those the
    /// ones whose post-reaction result hit (the Damaging re-entries).
    pub boss_postdeath_attempts: i32,
    pub boss_postdeath_lands: i32,
    /// First HP-positive -> zero tick for the rival leader; `-1` = never died.
    pub boss_death_tick: i32,
    pub boss_reentries: i32,
    /// v3: event-site Leaving (state 8) entries for the rival leader, and the compact gaps between
    /// consecutive post-death landed boss hits. `gap_min`/`gap_max` are `-1` when `gap_count == 0`.
    pub boss_leavings: i32,
    pub boss_postdeath_gap_count: i32,
    pub boss_postdeath_gap_min: i32,
    pub boss_postdeath_gap_max: i32,
    pub boss_postdeath_gap_sum: i32,
    /// v3: boss-targeting command hit attempts still to fire at the first-death sample, and whether
    /// the currently executing (not-yet-fired) hit is part of that mass. `-1` = not observed.
    pub future_hits_at_death: i32,
    pub future_hits_include_executing: i32,
    /// v3: first boss-access ticks (`-1` = none) and the per-tick targetable/UsingSkill occupancy.
    pub boss_access_first_command: i32,
    pub boss_access_first_attempt: i32,
    pub targetable_ticks: i32,
    pub using_skill_ticks: i32,
    pub boss_damaging_resets: i32,
    pub item_uses_ok: i32,
    /// Diagnostic on-verdict Finish dispatch (prizes queued before the verdict); `-1` when the
    /// run was not cut on the verdict, so it is never confused with a real zero.
    pub finish_dispatched_chests: i32,
}

/// `KA_ENCOUNTER_VERSION`: bump when the struct shape changes.
pub const KA_ENCOUNTER_VERSION: i32 = 3;

/// Fill the additive encounter report from the current battle state. Read-only.
pub fn encounter_report(battle: &KaBattle, policy: i32) -> KaEncounterReport {
    let mut out = KaEncounterReport {
        version: KA_ENCOUNTER_VERSION,
        finish_dispatched_chests: -1,
        ..Default::default()
    };
    let mut roster = [0i32; KA_MAX_UNITS];
    let len = battle.roster(0, &mut roster);
    out.own_count = len as i32;
    for slot in 0..len as usize {
        let unit = &battle.units[roster[slot] as usize];
        out.own_identity[slot] = unit.identity;
        let death = battle.encounter_own_death_tick[slot];
        out.own_first_death_tick[slot] = death;
        out.own_first_leaving_tick[slot] = battle.encounter_own_first_leaving_tick[slot];
        out.own_survived[slot] = i32::from(death < 0);
    }
    out.enemy_resolved_attacks = battle.encounter_enemy_resolved as i32;
    out.enemy_hits = battle.encounter_enemy_hits as i32;
    out.enemy_misses = battle.encounter_enemy_misses as i32;
    out.enemy_rolls = battle.encounter_enemy_rolls as i32;
    out.enemy_roll_hits = battle.encounter_enemy_roll_hits as i32;
    out.enemy_roll_misses = battle.encounter_enemy_roll_misses as i32;
    out.counter_checks = battle.encounter_counter_checks as i32;
    out.counter_enqueues = battle.encounter_counter_enqueues as i32;
    out.boss_postdeath_attempts = battle.encounter_boss_postdeath_attempts as i32;
    out.boss_postdeath_lands = battle.encounter_boss_postdeath_lands as i32;
    out.boss_death_tick = battle.encounter_boss_death_tick;
    out.boss_reentries = battle.encounter_boss_reentries as i32;
    out.boss_leavings = battle.encounter_boss_leavings as i32;
    out.boss_postdeath_gap_count = battle.encounter_boss_postdeath_gap_count;
    out.boss_postdeath_gap_min = battle.encounter_boss_postdeath_gap_min;
    out.boss_postdeath_gap_max = battle.encounter_boss_postdeath_gap_max;
    out.boss_postdeath_gap_sum = battle.encounter_boss_postdeath_gap_sum;
    out.future_hits_at_death = if battle.progress_future_hits_captured != 0 {
        battle.progress_boss_future_hits_at_death
    } else {
        -1
    };
    out.future_hits_include_executing = if battle.progress_future_hits_captured != 0 {
        battle.progress_boss_future_includes_executing
    } else {
        -1
    };
    out.boss_access_first_command = battle.progress_boss_access_first_command;
    out.boss_access_first_attempt = battle.progress_boss_access_first_attempt;
    out.targetable_ticks = battle.progress_targetable_ticks as i32;
    out.using_skill_ticks = battle.progress_using_skill_ticks as i32;
    out.boss_damaging_resets = battle.progress_boss_damaging_resets as i32;
    out.item_uses_ok = battle.item_uses_ok as i32;
    if policy == 2 && battle.prizes_at_verdict >= 0 {
        out.finish_dispatched_chests = battle.prizes_at_verdict;
    }
    out
}

/// `ka_run_battle`: run the whole battle natively and fill the report.
///
/// `policy`: 0 `at-horizon`, 1 `after-ending`, 2 `on-verdict`.
pub fn run_battle(battle: &mut KaBattle, steps: u32, policy: i32) -> i32 {
    // `ProgressWatch` knows the rival leader before the first tick; resolve it here as well so a
    // zero-step run reports the same `bossIdentity` the canonical observer was constructed with.
    progress_resolve_boss(battle);
    battle.defer_animation_resource_frames =
        u8::from(phases::can_defer_animation_resource_frames(battle));
    battle.deferred_animation_ticks = 0;
    let mut status = 0i32;
    for _ in 0..steps {
        if let Err(code) = tick(battle) {
            status = code;
            break;
        }
        if battle.battle_state == 3 {
            if policy == 2 {
                battle.ending_confirmed = 0;
                break;
            }
            if policy == 1 && battle.battle_frame > 79 {
                let (counter, transition) = update_ending(battle.battle_frame, battle.verdict);
                battle.ending_counter = counter;
                if transition {
                    battle.ending_gate_tick = battle.tick as i32;
                    battle.ending_confirmed = 1;
                    break;
                }
            }
        }
    }
    if battle.battle_state == 3 && policy == 0 {
        let (counter, transition) = update_ending(battle.battle_frame, battle.verdict);
        battle.ending_counter = counter;
        battle.ending_confirmed = u8::from(transition);
        if transition {
            battle.ending_gate_tick = battle.tick as i32;
        }
    }
    phases::finish_deferred_animation_resource_frames(battle);
    status
}

/// Fill the report from the current battle state.
pub fn report(battle: &KaBattle, policy: i32, status: i32) -> KaBattleReport {
    let mut unresolved = 0i32;
    let mut damage_or_leaving = 0i32;
    for index in 0..battle.count as usize {
        if battle.units[index].command_count > 0 {
            unresolved += 1;
        }
        let state = battle.units[index].state();
        if state == 6 || state == 8 {
            damage_or_leaving += 1;
        }
    }
    KaBattleReport {
        status,
        ticks: battle.tick as i32 + 1,
        verdict: battle.verdict,
        verdict_tick: battle.verdict_tick,
        battle_state: battle.battle_state,
        battle_frame: battle.battle_frame,
        finish_policy: policy,
        ending_gate_tick: battle.ending_gate_tick,
        ending_confirmed: i32::from(battle.ending_confirmed != 0),
        ending_counter: battle.ending_counter,
        prize_callbacks: battle.prize_count as i32,
        pre_verdict_prize_callbacks: battle.prizes_at_verdict,
        unresolved_commands: unresolved,
        pending_projectiles: battle.subsets[SUBSET_PROJECTILES].members.count() as i32,
        active_damage_or_leaving: damage_or_leaving,
        math_draws: battle.math_draws as i32,
        lib_draws: battle.lib_draws as i32,
        certificate_held: i32::from(battle.certificate_held != 0),
        certificate_frame: battle.certificate_frame,
        certificate_pending: battle.certificate_pending,
        late_hold_frame: battle.late_hold_frame,
        pending_at_verdict: battle.pending_at_verdict,
        pending_final: battle.pending_final,
        post_certificate_delta: battle.post_certificate_delta,
        observations: battle.observations as i32,
        verdict_observations: battle.verdict_observations as i32,
        clauses: [
            i32::from(battle.clauses[0] != 0),
            i32::from(battle.clauses[1] != 0),
            i32::from(battle.clauses[2] != 0),
            i32::from(battle.clauses[3] != 0),
        ],
        boss_hp: battle.boss_hp,
        boss_state: battle.boss_state,
        stored_target_holders: battle.stored_target_holders,
        queued_command_holders: battle.queued_command_holders,
        scope_allowed: i32::from(battle.scope_allowed != 0),
        heals: battle.heal_events as i32,
        attack_attempts: battle.attack_events as i32,
        survivors: own_survivors(battle),
        own_hp: own_hp_total(battle, false),
        own_hp_max: own_hp_total(battle, true),
        own_present: if battle.count > 0 { 1 } else { 0 },
        resource_uses: (battle.herb_uses_ok + battle.item_uses_ok) as i32,
        progress_boss: battle.progress_boss,
        progress_boss_death_tick: battle.progress_boss_death_tick,
        progress_boss_leaving_tick: battle.progress_boss_leaving_tick,
        progress_stored_at_death: battle.progress_stored_at_death,
        progress_stored_targeting_boss_at_death: battle.progress_stored_targeting_boss_at_death,
        progress_stored_target_holders_at_death: battle.progress_stored_target_holders_at_death,
        progress_commands_targeting_boss: battle.progress_commands_targeting_boss,
        progress_commands_targeting_boss_released: battle.progress_commands_targeting_boss_released,
        progress_max_stored: battle.progress_max_stored,
        progress_max_targeting_boss: battle.progress_max_targeting_boss,
        progress_target_holders_peak: battle.progress_target_holders_peak,
        progress_post_death_reentries: battle.progress_post_death_reentries,
        progress_post_death_leavings: battle.progress_post_death_leavings,
        progress_post_death_prizes: battle.progress_post_death_prizes,
        progress_released_after_death: battle.progress_released_after_death,
        progress_released_after_death_targeting_boss:
            battle.progress_released_after_death_targeting_boss,
        progress_first_release_after_death: battle.progress_first_release_after_death,
        progress_last_release_after_death: battle.progress_last_release_after_death,
        mp_watch_count: battle.mp_watch_count.max(0),
        mp_identity: battle.mp_watch,
        mp_min: battle.mp_min,
        mp_min_percent: battle.mp_min_percent,
        mp_low: [i32::from(battle.mp_low[0] != 0), i32::from(battle.mp_low[1] != 0)],
        mp_first_low_tick: battle.mp_first_low_tick,
        mp_first_low_phase: battle.mp_first_low_phase,
        mp_zero: [i32::from(battle.mp_zero[0] != 0), i32::from(battle.mp_zero[1] != 0)],
        herb_stock_start: battle.holy_herb_start_stock,
        herb_stock_remaining: battle.holy_herb_stock,
        herb_max_uses: battle.holy_herb_max_uses,
        herb_use_count: battle.herb_uses_ok as i32,
        herb_log_count: battle.herb_use_log,
        herb_use_tick: battle.herb_use_tick,
        herb_use_phase: battle.herb_use_phase,
        herb_use_source: battle.herb_use_source,
        herb_use_ok: [
            i32::from(battle.herb_use_ok[0] != 0), i32::from(battle.herb_use_ok[1] != 0),
            i32::from(battle.herb_use_ok[2] != 0), i32::from(battle.herb_use_ok[3] != 0),
            i32::from(battle.herb_use_ok[4] != 0), i32::from(battle.herb_use_ok[5] != 0),
            i32::from(battle.herb_use_ok[6] != 0), i32::from(battle.herb_use_ok[7] != 0),
            i32::from(battle.herb_use_ok[8] != 0), i32::from(battle.herb_use_ok[9] != 0),
            i32::from(battle.herb_use_ok[10] != 0), i32::from(battle.herb_use_ok[11] != 0),
            i32::from(battle.herb_use_ok[12] != 0), i32::from(battle.herb_use_ok[13] != 0),
            i32::from(battle.herb_use_ok[14] != 0), i32::from(battle.herb_use_ok[15] != 0),
        ],
    }
}

/// `_own_survivors`: own fighters (team 0) whose state is not Leaving (8). `-1` when the roster is
/// absent, mirroring the canonical `None`.
pub fn own_survivors(battle: &KaBattle) -> i32 {
    let mut roster = [0i32; KA_MAX_UNITS];
    let len = battle.roster(0, &mut roster);
    if len == 0 {
        return -1;
    }
    let mut survivors = 0i32;
    for slot in 0..len {
        let index = roster[slot] as usize;
        if battle.units[index].state() != 8 {
            survivors += 1;
        }
    }
    survivors
}

/// `_own_health_fraction`'s two sums: own effective HP, and own effective HP maximum.
pub fn own_hp_total(battle: &KaBattle, maximum: bool) -> i64 {
    let mut roster = [0i32; KA_MAX_UNITS];
    let len = battle.roster(0, &mut roster);
    let mut total = 0i64;
    for slot in 0..len {
        let unit = &battle.units[roster[slot] as usize];
        total += if maximum { unit.param_maximum(10) as i64 } else { unit.hp() as i64 };
    }
    total
}
