//! Native prototype for the combat simulator's hot path.
//!
//! # Boundary
//!
//! The measured result of the first prototype is the reason for the shape of this crate: a
//! fine-grained boundary is a pessimisation under PyPy (one ctypes crossing ~0.63 us, ~5.85 us with
//! five buffer arguments, against 0.14 us for the whole JIT'd Python scan). So nothing here exposes
//! distance, fighter, component or tick helpers. The ABI is:
//!
//! ```text
//! create -> import (units + rows + config + seeds) -> execute a substantial phase -> export -> destroy
//! ```
//!
//! Two slices live here.
//!
//! 1. The original spatial/targeting scan ([`ka_nearest_eligible`], [`battle::ka_spatial_phase`]),
//!    which the profile puts at 297,489 `distance` calls and 892,467 generator executions per
//!    1,000 ticks. It mirrors:
//!
//!    ```text
//!    SharedControllers.distance(a, b)          ->  abs(l[0]-r[0]) + abs(l[1]-r[1])
//!    combat_navigation.same_grid(unit, team)   ->  any(other is not unit
//!                                                       and other.board[5] not in (2, 7, 8)
//!                                                       and other.board[7] == unit.board[7]
//!                                                       for other in team)
//!    combat_navigation.front_opponent(...)     ->  min(eligible, key=distance)
//!    ```
//!
//! 2. The ported fighter phase: `update_fighters -> SharedControllers.update -> decide -> choose`,
//!    with the native board/long-board model and the keyed math RNG. See [`ai`].
//!
//! Semantics that must not drift, and how each is pinned here:
//!
//!  * `min(..., key=distance)` returns the FIRST minimum in iteration order, because Python's `min`
//!    keeps the current element only when the next is strictly smaller. Every scan compares with
//!    `<` and never `<=`.
//!  * `distance` is pure integer arithmetic on cell coordinates; no float and no wrapping.
//!  * `i32`/`f32`/`trunc_div`/`random_below` reproduce the recovered wrapping and truncation
//!    instead of relying on Rust's saturating casts.
//!  * The RNG is the recovered subtractive backend, owned by the battle, so draw order is a
//!    property of the battle rather than of the caller's call pattern.

#![allow(clippy::missing_safety_doc)]

pub mod ai;
pub mod battle;
pub mod battle_control;
pub mod combat;
pub mod params;
pub mod phases;
pub mod rng;
pub mod state;
pub mod world;
#[cfg(feature = "profile")]
pub mod profile;

use combat::{AttackResult, EffectSpec};
use state::{KaBattle, KaEvent, KaSkill, KaUnit, KA_MAX_ROWS, KA_MAX_UNITS};
use world::{KaComponentView, KA_SUBSET_COUNT};
use rng::KaRandom;

/// `abs(l[0]-r[0]) + abs(l[1]-r[1])`, the recovered cell distance.
#[no_mangle]
pub extern "C" fn ka_manhattan(ax: i32, ay: i32, bx: i32, by: i32) -> i32 {
    (ax - bx).abs() + (ay - by).abs()
}

/// The signed 32-bit wrap the engine's `i32()` performs, exposed so the parity harness can pin it.
#[no_mangle]
pub extern "C" fn ka_i32_wrap(value: i64) -> i32 {
    value as i32
}

/// Truncation toward zero of an `f32`, mirroring `native_float_to_int`.
#[no_mangle]
pub extern "C" fn ka_f32_to_int(value: f32) -> i32 {
    ai::native_float_to_int(value)
}

/// `combat_resolution.attack_interval`, exposed so the one host-libm dependency (`pow`) can be
/// compared against the Python side directly instead of only through the differential.
#[no_mangle]
pub extern "C" fn ka_attack_interval(agility: i32) -> i32 {
    ai::attack_interval(agility)
}

/// One forward pass over the roster: count the eligible opponents and return the nearest of them.
///
/// Inputs are flat, caller-owned arrays in ascending entity order - the same order the Python
/// iterates - so the tie-break falls out of the loop order without any comparison of ids.
#[no_mangle]
pub unsafe extern "C" fn ka_nearest_eligible(
    cells: *const i32,
    present: *const u8,
    hp: *const i32,
    state: *const i32,
    team: *const u8,
    n: usize,
    query_index: usize,
    query_team: u8,
    out_index: *mut i32,
) -> usize {
    if cells.is_null()
        || present.is_null()
        || hp.is_null()
        || state.is_null()
        || team.is_null()
        || out_index.is_null()
        || n == 0
        || query_index >= n
    {
        if !out_index.is_null() {
            *out_index = -1;
        }
        return 0;
    }

    let cell_slice = std::slice::from_raw_parts(cells, n * 2);
    let present_slice = std::slice::from_raw_parts(present, n);
    let hp_slice = std::slice::from_raw_parts(hp, n);
    let state_slice = std::slice::from_raw_parts(state, n);
    let team_slice = std::slice::from_raw_parts(team, n);

    let query_x = *cell_slice.get_unchecked(query_index * 2);
    let query_y = *cell_slice.get_unchecked(query_index * 2 + 1);

    let mut count: usize = 0;
    let mut best_index: i32 = -1;
    let mut best_distance: i32 = i32::MAX;

    for index in 0..n {
        if index == query_index {
            continue;
        }
        if *team_slice.get_unchecked(index) == query_team {
            continue;
        }
        if *present_slice.get_unchecked(index) == 0 {
            continue;
        }
        if *hp_slice.get_unchecked(index) <= 0 {
            continue;
        }
        let unit_state = *state_slice.get_unchecked(index);
        if unit_state == 7 || unit_state == 8 {
            continue;
        }
        count += 1;
        let dx = query_x - *cell_slice.get_unchecked(index * 2);
        let dy = query_y - *cell_slice.get_unchecked(index * 2 + 1);
        let distance = dx.abs() + dy.abs();
        // Strictly smaller: Python's min keeps the first minimum encountered.
        if distance < best_distance {
            best_distance = distance;
            best_index = index as i32;
        }
    }

    *out_index = best_index;
    count
}

// ------------------------------------------------------------------------------------------
// Native-owned battle state
// ------------------------------------------------------------------------------------------

/// Create a native battle state. One crossing; the state then lives in Rust.
#[no_mangle]
pub extern "C" fn ka_battle_create() -> *mut KaBattle {
    Box::into_raw(KaBattle::boxed_blank())
}

/// `ka_battle_clone(template) -> new_handle`.
///
/// `KaBattle` is a flat, self-contained struct: every array is inline, there are no heap pointers,
/// no `Drop` and no interior references, so a bitwise copy is a complete, independent deep copy.
/// The clone shares nothing with the template, which is what makes per-seed cloning safe.
#[no_mangle]
pub unsafe extern "C" fn ka_battle_clone(template: *const KaBattle) -> *mut KaBattle {
    if template.is_null() {
        return std::ptr::null_mut();
    }
    // The copy is made straight into the destination box: `std::ptr::read` would put a multi-megabyte
    // temporary on the stack first.
    let mut boxed = Box::<KaBattle>::new_uninit();
    std::ptr::copy_nonoverlapping(template as *const u8, boxed.as_mut_ptr() as *mut u8,
                                  std::mem::size_of::<KaBattle>());
    Box::into_raw(boxed.assume_init())
}

/// A 64-bit checksum of the whole native battle, so a test can prove a clone did not mutate its
/// template (and that two clones are bit-identical before they diverge).
#[no_mangle]
pub unsafe extern "C" fn ka_battle_checksum(battle: *const KaBattle) -> u64 {
    if battle.is_null() {
        return 0;
    }
    let bytes = std::slice::from_raw_parts(
        battle as *const u8,
        std::mem::size_of::<KaBattle>(),
    );
    let mut hash: u64 = 0xcbf2_9ce4_8422_2325;
    for byte in bytes {
        hash ^= *byte as u64;
        hash = hash.wrapping_mul(0x0000_0100_0000_01b3);
    }
    hash
}

/// Destroy a native battle state created by [`ka_battle_create`].
#[no_mangle]
pub unsafe extern "C" fn ka_battle_free(battle: *mut KaBattle) {
    if !battle.is_null() {
        drop(Box::from_raw(battle));
    }
}

/// Import up to `KA_MAX_UNITS` units and the legacy RNG word. Units keep the caller's order,
/// which is the engine's ascending entity order and therefore the tie-break order.
#[no_mangle]
pub unsafe extern "C" fn ka_battle_import(
    battle: *mut KaBattle,
    units: *const KaUnit,
    count: u32,
    rng: u64,
) -> u32 {
    if battle.is_null() || units.is_null() {
        return 0;
    }
    let state = &mut *battle;
    let take = count.min(KA_MAX_UNITS as u32) as usize;
    let source = std::slice::from_raw_parts(units, take);
    state.units[..take].copy_from_slice(source);
    state.count = take as u32;
    state.rng = rng;
    state.tick = 0;
    state.math_draws = 0;
    state.lib_draws = 0;
    state.event_count = 0;
    take as u32
}

/// Seed the two recovered RNG streams. The math stream is the one `next_math('skill_invocation')`
/// draws from; the lib stream is recorded alongside it so later phases cannot silently reorder.
#[no_mangle]
pub unsafe extern "C" fn ka_battle_seed_rng(battle: *mut KaBattle, math_seed: i32, lib_seed: i32) {
    if battle.is_null() {
        return;
    }
    let state = &mut *battle;
    state.math = KaRandom::seeded(math_seed);
    state.lib = KaRandom::seeded(lib_seed);
    state.math_draws = 0;
    state.lib_draws = 0;
}

/// Set the scenario constants the fighter phase reads: map width, formation row offset and whether
/// movement is enabled.
#[no_mangle]
pub unsafe extern "C" fn ka_battle_config(
    battle: *mut KaBattle,
    map_width: i32,
    row_offset: i32,
    movement_enabled: u8,
) {
    if battle.is_null() {
        return;
    }
    let state = &mut *battle;
    state.map_width = map_width;
    state.row_offset = row_offset;
    state.movement_enabled = movement_enabled;
    state.event_count = 0;
}

/// Append one row to the battle's recovered skill row table (`combat_farmer_slice.ROWS`).
/// Returns the new row count, or `0` when the table is full.
#[no_mangle]
pub unsafe extern "C" fn ka_battle_add_row(battle: *mut KaBattle, row: *const KaSkill) -> u32 {
    if battle.is_null() || row.is_null() {
        return 0;
    }
    let state = &mut *battle;
    let slot = state.row_count as usize;
    if slot >= KA_MAX_ROWS {
        return 0;
    }
    if slot > 0 && state.rows[slot - 1].id >= (*row).id {
        state.rows_sorted = 0;
    }
    state.rows[slot] = *row;
    state.row_count += 1;
    state.row_count
}

/// Export the native state's mutable fields, so a phase can be compared with Python field by field.
#[no_mangle]
pub unsafe extern "C" fn ka_battle_export(battle: *const KaBattle, units: *mut KaUnit) -> u32 {
    if battle.is_null() || units.is_null() {
        return 0;
    }
    let state = &*battle;
    let count = state.count as usize;
    std::ptr::copy_nonoverlapping(state.units.as_ptr(), units, count);
    count as u32
}

/// The RNG word, so draw bookkeeping can be compared without exporting the whole state.
#[no_mangle]
pub unsafe extern "C" fn ka_battle_rng(battle: *const KaBattle) -> u64 {
    if battle.is_null() {
        return 0;
    }
    (*battle).rng
}

/// `self.math_draws` - how many math-stream draws the phase consumed.
#[no_mangle]
pub unsafe extern "C" fn ka_battle_math_draws(battle: *const KaBattle) -> u32 {
    if battle.is_null() {
        return 0;
    }
    (*battle).math_draws
}

/// `self.lib_draws`.
#[no_mangle]
pub unsafe extern "C" fn ka_battle_lib_draws(battle: *const KaBattle) -> u32 {
    if battle.is_null() {
        return 0;
    }
    (*battle).lib_draws
}

/// The math RNG, so the harness can compare the whole subtractive state after the phase.
#[no_mangle]
pub unsafe extern "C" fn ka_battle_math_rng(battle: *const KaBattle) -> *const KaRandom {
    if battle.is_null() {
        return std::ptr::null();
    }
    &(*battle).math
}

/// The lib RNG.
#[no_mangle]
pub unsafe extern "C" fn ka_battle_lib_rng(battle: *const KaBattle) -> *const KaRandom {
    if battle.is_null() {
        return std::ptr::null();
    }
    &(*battle).lib
}

/// Number of recorded events.
#[no_mangle]
pub unsafe extern "C" fn ka_battle_event_count(battle: *const KaBattle) -> u32 {
    if battle.is_null() {
        return 0;
    }
    (*battle).event_count
}

/// Copy the recorded event log out, so call *order* is compared as well as final state.
#[no_mangle]
pub unsafe extern "C" fn ka_battle_export_events(
    battle: *const KaBattle,
    events: *mut KaEvent,
) -> u32 {
    if battle.is_null() || events.is_null() {
        return 0;
    }
    let state = &*battle;
    let count = state.event_count as usize;
    std::ptr::copy_nonoverlapping(state.events.as_ptr(), events, count);
    count as u32
}

/// The ported `update_fighters` phase: one full fighter step over every roster, in team-then-roster
/// order. `battle_state` is the shared `battle_state` the Python gate reads.
///
/// Returns `0` on success or a negative [`ai::KA_ERR_*`] code. A non-zero return means the phase
/// needed a state handler that is not ported yet, so the caller must not treat the state as a
/// faithful simulation step.
#[no_mangle]
pub unsafe extern "C" fn ka_update_fighters(battle: *mut KaBattle, battle_state: i32) -> i32 {
    if battle.is_null() {
        return ai::KA_ERR_CAPACITY;
    }
    let state = &mut *battle;
    state.event_count = 0;
    match ai::update_fighters(state, battle_state) {
        Ok(()) => {
            // `SharedControllers.run` increments the tick before the fighters phase, so one coherent
            // step advances it exactly once.
            state.tick = state.tick.wrapping_add(1);
            0
        }
        Err(code) => code,
    }
}

/// Configure the shared identity space: `CombatEntities(100, ...)` puts the first unit at
/// identity 100, and created entities continue from there.
#[no_mangle]
pub unsafe extern "C" fn ka_battle_identity(battle: *mut KaBattle, first_identity: i32) {
    if battle.is_null() {
        return;
    }
    let state = &mut *battle;
    state.first_identity = first_identity;
    state.next_identity = first_identity + state.count as i32;
    state.next_command = 0;
    // The subsets' derived identity index needs the same base.
    for slot in 0..KA_SUBSET_COUNT {
        state.subsets[slot].members.base = first_identity;
        state.subsets[slot].members.rebuild_derived();
    }
}

/// Append one `effect-resource-checks.json` row (`seb id -> maxFrame`).
#[no_mangle]
pub unsafe extern "C" fn ka_battle_add_effect_resource(
    battle: *mut KaBattle,
    id: i32,
    max_frame: i32,
) -> u32 {
    if battle.is_null() {
        return 0;
    }
    let state = &mut *battle;
    let slot = state.effect_resource_count as usize;
    if slot >= state.effect_resources.len() {
        return 0;
    }
    state.effect_resources[slot] = state::KaEffectResource { id, max_frame };
    state.effect_resource_count += 1;
    state.effect_resource_count
}

/// Install `skill-combat-constants.json`'s `humanAnimationSebBases`, the recovered `combat_clip`
/// table `ChangeAnimation` reads.
#[no_mangle]
pub unsafe extern "C" fn ka_battle_set_human_bases(
    battle: *mut KaBattle,
    bases: *const i32,
    count: u32,
) -> u32 {
    if battle.is_null() {
        return 0;
    }
    let state = &mut *battle;
    let take = (count as usize).min(state.human_bases.len());
    for slot in 0..take {
        state.human_bases[slot] = if bases.is_null() { 0 } else { *bases.add(slot) };
    }
    state.human_base_count = take as u32;
    state.human_base_count
}

/// `prize_candidates`; `count == 0` still means "present but empty" as far as the Python
/// `prize_candidates is not None` test goes, so presence is set separately.
#[no_mangle]
pub unsafe extern "C" fn ka_battle_set_prizes(
    battle: *mut KaBattle,
    candidates: *const i32,
    count: u32,
) {
    if battle.is_null() {
        return;
    }
    let state = &mut *battle;
    state.prize_present = 1;
    let take = (count as usize).min(state.prize_candidates.len());
    for slot in 0..take {
        state.prize_candidates[slot] = if candidates.is_null() {
            0
        } else {
            *candidates.add(slot)
        };
    }
    state.prize_candidate_count = take as u32;
}

/// Rebuild every subset's membership from the component presence, in ascending identity order.
///
/// This reproduces the bootstrap membership `CombatEntities` build up while the units are created
/// (one entity at a time, components added in scenario order); a mid-battle state whose slot order
/// came from later reuse must be loaded instead of rebuilt.
#[no_mangle]
pub unsafe extern "C" fn ka_battle_rebuild_subsets(battle: *mut KaBattle) {
    if battle.is_null() {
        return;
    }
    let state = &mut *battle;
    let mut ids = [0i32; world::KA_MAX_OBJECTS + state::KA_MAX_UNITS];
    let mut len = 0usize;
    for slot in 0..state.count as usize {
        ids[len] = state.units[slot].identity;
        len += 1;
    }
    for slot in 0..state.object_count as usize {
        ids[len] = state.objects[slot].id;
        len += 1;
    }
    ids[..len].sort_unstable();
    for index in 0..len {
        state.notify_subsets(ids[index]);
    }
}

/// Seed the occupancy buckets from the fighters' current cells, mirroring the `Cell` component-add
/// notifications the bootstrap performs.
#[no_mangle]
pub unsafe extern "C" fn ka_battle_seed_occupancy(battle: *mut KaBattle) {
    if battle.is_null() {
        return;
    }
    let state = &mut *battle;
    for slot in 0..state.count as usize {
        if state.units[slot].has_component(world::SLOT_AI) {
            let id = state.units[slot].identity;
            let cell = state.units[slot].cell();
            state.occupancy_add(id, [cell.0, cell.1]);
        }
    }
}

/// Execute up to `steps` fully covered ticks with no Python re-entry.
///
/// Returns `0` on success, or the first unsupported-mechanic code. The count of completed steps and
/// the last status are written to `out_completed`/`out_status` when those pointers are non-null, so
/// a partially completed run reports exactly where it stopped.
#[no_mangle]
pub unsafe extern "C" fn ka_run_native_steps(
    battle: *mut KaBattle,
    battle_state: i32,
    steps: u32,
    out_completed: *mut u32,
    out_status: *mut i32,
) -> i32 {
    if battle.is_null() {
        return ai::KA_ERR_CAPACITY;
    }
    let state = &mut *battle;
    let mut completed = 0u32;
    let mut status = 0i32;
    // The event log accumulates across the whole internal run, so the harness can compare the
    // interval's event sequence; ka_update_fighters still resets per call for single-step use.
    state.event_count = 0;
    for _ in 0..steps {
        match ai::update_fighters(state, battle_state) {
            Ok(()) => {
                completed += 1;
                state.tick = state.tick.wrapping_add(1);
                // The ported post-fighter prefix runs in canonical order.
                if let Err(code) = phases::ported_prefix(state) {
                    status = code;
                    break;
                }
                if let Err(code) = phases::trailing_phases(state) {
                    status = code;
                    break;
                }
            }
            Err(code) => {
                status = code;
                break;
            }
        }
    }
    if !out_completed.is_null() {
        *out_completed = completed;
    }
    if !out_status.is_null() {
        *out_status = status;
    }
    status
}

/// Number of created entities (effects, trails, projectiles).
#[no_mangle]
pub unsafe extern "C" fn ka_battle_allocate(battle: *mut KaBattle) -> i32 {
    if battle.is_null() {
        return -1;
    }
    (*battle).allocate()
}

/// `CombatEntities.destroy` on one identity, so the harness can exercise removal paths.
#[no_mangle]
pub unsafe extern "C" fn ka_battle_destroy(battle: *mut KaBattle, id: i32) {
    if battle.is_null() {
        return;
    }
    (*battle).destroy(id);
}

/// One complete ported tick: the fighters phase, then the four ported global phases, with the event
/// log covering the whole tick. This is the persistent-state entry point the harness steps.
#[no_mangle]
pub unsafe extern "C" fn ka_native_tick(battle: *mut KaBattle, battle_state: i32) -> i32 {
    if battle.is_null() {
        return ai::KA_ERR_CAPACITY;
    }
    let state = &mut *battle;
    state.event_count = 0;
    if let Err(code) = ai::update_fighters(state, battle_state) {
        return code;
    }
    state.tick = state.tick.wrapping_add(1);
    if let Err(code) = phases::ported_prefix(state) {
        return code;
    }
    if let Err(code) = phases::trailing_phases(state) {
        return code;
    }
    0
}

/// One complete canonical tick: the tick head, both input callbacks, the fighters, the reward
/// observer and every ported phase. `ka_run_battle` loops this; exposing it makes each tick
/// observable on its own, which is what per-tick event and state parity needs.
#[no_mangle]
pub unsafe extern "C" fn ka_native_full_tick(battle: *mut KaBattle) -> i32 {
    if battle.is_null() {
        return ai::KA_ERR_CAPACITY;
    }
    match battle_control::tick(&mut *battle) {
        Ok(()) => 0,
        Err(code) => code,
    }
}

/// Run one ported global phase in isolation, so each can be differential-tested on its own.
/// `phase`: 0 movement, 1 facing, 2 cells, 3 heights, 4 all four in canonical order.
#[no_mangle]
pub unsafe extern "C" fn ka_run_phase(battle: *mut KaBattle, phase: u32) -> i32 {
    if battle.is_null() {
        return ai::KA_ERR_CAPACITY;
    }
    let state = &mut *battle;
    state.event_count = 0;
    match phase {
        0 => phases::update_positions(state),
        1 => phases::update_facing(state),
        2 => phases::update_cells(state),
        3 => phases::update_heights(state),
        4 => phases::global_phases(state),
        5 => phases::update_status_phase(state),
        // Same phase as 5; the harness pre-loads the 20-tick boundary state for expiry coverage.
        9 => phases::update_status_phase(state),
        10 => {
            if let Err(code) = phases::tick_modifiers(state) {
                return code;
            }
        }
        11 => {
            if let Err(code) = phases::update_animations(state) {
                return code;
            }
        }
        12 => {
            if let Err(code) = phases::update_effect_phase(state) {
                return code;
            }
        }
        13 => phases::update_garbage(state),
        14 => {
            if let Err(code) = phases::trailing_phases(state) {
                return code;
            }
        }
        6 => {
            if let Err(code) = phases::tick_projectiles(state) {
                return code;
            }
        }
        7 => {
            phases::update_status_phase(state);
            if let Err(code) = phases::tick_projectiles(state) {
                return code;
            }
        }
        8 => {
            if let Err(code) = phases::ported_prefix(state) {
                return code;
            }
        }
        _ => return ai::KA_ERR_UNSUPPORTED_STATE,
    }
    0
}

/// `self.projectile_sources` size, so a snapshot can carry the live launch records.
#[no_mangle]
pub unsafe extern "C" fn ka_projectile_source_count(battle: *const KaBattle) -> u32 {
    if battle.is_null() {
        return 0;
    }
    (*battle).projectile_source_count
}

/// Read one `projectile_sources` entry: `(identity, caster, skill)`.
#[no_mangle]
pub unsafe extern "C" fn ka_projectile_source(
    battle: *const KaBattle,
    slot: u32,
    out_identity: *mut i32,
    out_caster: *mut i32,
    out_skill: *mut i32,
) -> i32 {
    if battle.is_null() || slot >= (*battle).projectile_source_count {
        return -1;
    }
    let row = &(*battle).projectile_sources[slot as usize];
    if !out_identity.is_null() {
        *out_identity = row.identity;
    }
    if !out_caster.is_null() {
        *out_caster = row.caster;
    }
    if !out_skill.is_null() {
        *out_skill = row.skill;
    }
    0
}

/// Install one `projectile_sources` entry.
#[no_mangle]
pub unsafe extern "C" fn ka_battle_set_projectile_source(
    battle: *mut KaBattle,
    identity: i32,
    caster: i32,
    skill: i32,
) {
    if battle.is_null() {
        return;
    }
    (*battle).set_projectile_source(identity, caster, skill);
}

/// Install one `animation-resources.json` row: `resources[res][seb] = {frame, max_frame}`.
#[no_mangle]
pub unsafe extern "C" fn ka_battle_set_animation_resource(
    battle: *mut KaBattle,
    res: i32,
    seb: i32,
    max_frame: i32,
    frame: i32,
) -> u32 {
    if battle.is_null() {
        return 0;
    }
    let state = &mut *battle;
    let slot = state.animation_resource_count as usize;
    if slot >= state.animation_resources.len() {
        return state.animation_resource_count;
    }
    if slot > 0 {
        let prior = state.animation_resources[slot - 1];
        if (prior.res, prior.seb) >= (res, seb) {
            state.animation_resources_sorted = 0;
        }
    }
    state.animation_resources[slot] =
        state::KaAnimationResource { res, seb, max_frame, frame };
    state.animation_resource_count += 1;
    state.animation_resource_count
}

/// Set `RewardEntitlementWatch.scope['allowed']` (derived from the encounter roster and the row
/// table, so it is scenario data, not run state).
#[no_mangle]
pub unsafe extern "C" fn ka_battle_set_scope_allowed(battle: *mut KaBattle, allowed: u8) {
    if battle.is_null() {
        return;
    }
    (*battle).scope_allowed = allowed;
}

/// Append one scheduled input event `(tick, phase, kind, item index)`.
#[no_mangle]
pub unsafe extern "C" fn ka_battle_add_input(
    battle: *mut KaBattle,
    tick: i32,
    phase: i32,
    kind: i32,
    item: i32,
) -> u32 {
    if battle.is_null() {
        return 0;
    }
    let state = &mut *battle;
    let slot = state.input_count as usize;
    if slot >= state.inputs.len() {
        return state.input_count;
    }
    state.inputs[slot] = state::KaInputEvent { tick, phase, kind, item };
    state.input_count += 1;
    state.input_count
}

/// Append one battle-item definition `(id, parameter, all_residents, bonus_min, bonus_max, stock)`.
#[no_mangle]
pub unsafe extern "C" fn ka_battle_add_item(
    battle: *mut KaBattle,
    id: i32,
    parameter: i32,
    all_residents: u8,
    bonus_min: i32,
    bonus_max: i32,
    stock: i32,
) -> u32 {
    if battle.is_null() {
        return 0;
    }
    let state = &mut *battle;
    let slot = state.item_count as usize;
    if slot >= state.items.len() {
        return state.item_count;
    }
    state.items[slot] = state::KaItem {
        id,
        parameter,
        all_residents,
        bonus_min,
        bonus_max,
        stock,
    };
    state.item_count += 1;
    state.item_count
}

/// Set the Holy Herb stock.
#[no_mangle]
pub unsafe extern "C" fn ka_battle_set_herb_stock(battle: *mut KaBattle, stock: i32) {
    if battle.is_null() {
        return;
    }
    (*battle).holy_herb_stock = stock;
    // The declared stock is what the report states as the starting stock; the live counter above is
    // the one the dispatches decrement.
    (*battle).holy_herb_start_stock = stock;
}

/// Configure the MP telemetry / live Holy Herb policy: `identities` is the declared trigger list
/// (the DPS and the healer), `count` its length, and `max_uses` the declared `holyHerbMaxUses`
/// (`0` disables the automatic policy). Experiment configuration, never recovered game data.
#[no_mangle]
pub unsafe extern "C" fn ka_battle_set_mp_watch(
    battle: *mut KaBattle,
    identities: *const i32,
    count: u32,
    max_uses: i32,
) -> u32 {
    if battle.is_null() {
        return 0;
    }
    let state = &mut *battle;
    let limit = count.min(state::KA_MAX_MP_WATCH as u32);
    for slot in 0..limit as usize {
        state.mp_watch[slot] = if identities.is_null() { -1 } else { *identities.add(slot) };
    }
    for slot in limit as usize..state::KA_MAX_MP_WATCH {
        state.mp_watch[slot] = -1;
    }
    state.mp_watch_count = limit as i32;
    state.holy_herb_max_uses = max_uses;
    state.holy_herb_armed = [1; state::KA_MAX_MP_WATCH];
    state.mp_min = [-1; state::KA_MAX_MP_WATCH];
    state.mp_min_percent = [-1; state::KA_MAX_MP_WATCH];
    state.mp_low = [0; state::KA_MAX_MP_WATCH];
    state.mp_first_low_tick = [-1; state::KA_MAX_MP_WATCH];
    state.mp_first_low_phase = [-1; state::KA_MAX_MP_WATCH];
    state.mp_zero = [0; state::KA_MAX_MP_WATCH];
    state.holy_herb_pending = 0;
    limit
}

/// Read the MP telemetry / policy configuration back: `[count, max_uses, identity0, identity1]`.
#[no_mangle]
pub unsafe extern "C" fn ka_battle_mp_config(battle: *const KaBattle, out: *mut i32) -> i32 {
    if battle.is_null() || out.is_null() {
        return -1;
    }
    let state = &*battle;
    *out.add(0) = state.mp_watch_count;
    *out.add(1) = state.holy_herb_max_uses;
    *out.add(2) = state.mp_watch[0];
    *out.add(3) = state.mp_watch[1];
    0
}

/// One unit's live MP (`world.value(identity, 11)`, the same effective value the observer samples),
/// or `-1` when the identity is not a fighter in this battle. Used by the interaction/consumable
/// checks to read the value immediately before and after a dispatch on both engines.
#[no_mangle]
pub unsafe extern "C" fn ka_battle_mp(battle: *const KaBattle, identity: i32) -> i32 {
    if battle.is_null() {
        return -1;
    }
    let state = &*battle;
    match state.fighter_slot(identity) {
        Some(index) => state.units[index].mp(),
        None => -1,
    }
}

/// Number of recorded consumable dispatches.
#[no_mangle]
pub unsafe extern "C" fn ka_use_log_count(battle: *const KaBattle) -> u32 {
    if battle.is_null() {
        return 0;
    }
    (*battle).use_log_count
}

/// Read one consumable record `(kind, item index, tick, used, percent, remaining)`.
#[no_mangle]
pub unsafe extern "C" fn ka_use_log(battle: *const KaBattle, slot: u32, out: *mut i32) -> i32 {
    if battle.is_null() || out.is_null() || slot >= (*battle).use_log_count {
        return -1;
    }
    let row = (*battle).use_log[slot as usize];
    *out.add(0) = row.kind;
    *out.add(1) = row.unit;
    *out.add(2) = row.a;
    *out.add(3) = row.b;
    *out.add(4) = row.c;
    *out.add(5) = row.d;
    0
}

/// Number of scheduled consumable input events.
#[no_mangle]
pub unsafe extern "C" fn ka_battle_input_count(battle: *const KaBattle) -> u32 {
    if battle.is_null() {
        return 0;
    }
    (*battle).input_count
}

/// Read one scheduled input `(tick, phase, kind, item)`; `out[4]` receives the live input count.
#[no_mangle]
pub unsafe extern "C" fn ka_battle_input(battle: *const KaBattle, slot: u32, out: *mut i32) -> i32 {
    if battle.is_null() || out.is_null() || slot >= (*battle).input_count {
        return -1;
    }
    let row = (*battle).inputs[slot as usize];
    *out.add(0) = row.tick;
    *out.add(1) = row.phase;
    *out.add(2) = row.kind;
    *out.add(3) = row.item;
    *out.add(4) = (*battle).input_count as i32;
    0
}

/// Number of battle-item definitions.
#[no_mangle]
pub unsafe extern "C" fn ka_battle_item_count(battle: *const KaBattle) -> u32 {
    if battle.is_null() {
        return 0;
    }
    (*battle).item_count
}

/// Read one item `(id, parameter, all_residents, bonus_min, bonus_max, stock)`.
#[no_mangle]
pub unsafe extern "C" fn ka_battle_item(battle: *const KaBattle, slot: u32, out: *mut i32) -> i32 {
    if battle.is_null() || out.is_null() || slot >= (*battle).item_count {
        return -1;
    }
    let row = (*battle).items[slot as usize];
    *out.add(0) = row.id;
    *out.add(1) = row.parameter;
    *out.add(2) = row.all_residents as i32;
    *out.add(3) = row.bonus_min;
    *out.add(4) = row.bonus_max;
    *out.add(5) = row.stock;
    0
}

/// Remaining Holy Herb stock.
#[no_mangle]
pub unsafe extern "C" fn ka_battle_herb_stock(battle: *const KaBattle) -> i32 {
    if battle.is_null() {
        return 0;
    }
    (*battle).holy_herb_stock
}

/// Consume `count` Lib draws with no reduction - the constructor's deferred follower-selection
/// draws, which a reseeded clone must replay to reach the same pre-battle RNG position a fresh
/// `SharedControllers` would have.
#[no_mangle]
pub unsafe extern "C" fn ka_battle_skip_lib_draws(battle: *mut KaBattle, count: u32) {
    if battle.is_null() {
        return;
    }
    let state = &mut *battle;
    for _ in 0..count {
        state.draw_lib_label(state::KA_LIB_OTHER);
    }
}

/// Restore the battle-control counters, so a mid-battle state can be resumed exactly.
#[no_mangle]
pub unsafe extern "C" fn ka_battle_set_control(
    battle: *mut KaBattle,
    battle_state: i32,
    battle_frame: i32,
    verdict: i32,
    verdict_tick: i32,
    ending_counter: i32,
    ending_gate_tick: i32,
    ending_confirmed: i32,
    prizes_at_verdict: i32,
    prize_count: i32,
) {
    if battle.is_null() {
        return;
    }
    let state = &mut *battle;
    state.battle_state = battle_state;
    state.battle_frame = battle_frame;
    state.verdict = verdict;
    state.verdict_tick = verdict_tick;
    state.ending_counter = ending_counter;
    state.ending_gate_tick = ending_gate_tick;
    state.ending_confirmed = u8::from(ending_confirmed != 0);
    state.prizes_at_verdict = prizes_at_verdict;
    state.prize_count = prize_count.max(0) as u32;
}

/// `ka_run_battle`: one import, the whole battle in Rust, one report export.
///
/// `policy`: 0 `at-horizon`, 1 `after-ending`, 2 `on-verdict`.
#[no_mangle]
pub unsafe extern "C" fn ka_run_battle(
    battle: *mut KaBattle,
    steps: u32,
    policy: i32,
    out: *mut battle_control::KaBattleReport,
) -> i32 {
    if battle.is_null() {
        return ai::KA_ERR_CAPACITY;
    }
    let state = &mut *battle;
    let status = battle_control::run_battle(state, steps, policy);
    if !out.is_null() {
        *out = battle_control::report(state, policy, status);
    }
    status
}

/// Read the current report without running (for checkpoint comparisons).
/// Read the profiling counters: `notify_calls, subset_checks, bucket_lookups, bucket_steps`.
#[no_mangle]
pub unsafe extern "C" fn ka_battle_stats(battle: *const KaBattle, out: *mut u64) -> i32 {
    if battle.is_null() || out.is_null() {
        return ai::KA_ERR_CAPACITY;
    }
    let state = &*battle;
    *out.add(0) = state.stat_notify_calls;
    *out.add(1) = state.stat_subset_checks;
    *out.add(2) = state.stat_bucket_lookups;
    *out.add(3) = state.stat_bucket_steps;
    0
}

/// Number of recorded lib draws (diagnostic only; the same count is in `lib_draws`).
#[no_mangle]
pub unsafe extern "C" fn ka_lib_log_count(battle: *const KaBattle) -> u32 {
    if battle.is_null() {
        return 0;
    }
    (*battle).lib_log_count
}

/// Read one lib-draw record `(purpose, tick, draw_number, raw, bound)`.
#[no_mangle]
pub unsafe extern "C" fn ka_lib_log(
    battle: *const KaBattle,
    slot: u32,
    out: *mut i32,
) -> i32 {
    if battle.is_null() || out.is_null() || slot >= (*battle).lib_log_count {
        return -1;
    }
    let row = (*battle).lib_log[slot as usize];
    *out.add(0) = row.kind;
    *out.add(1) = row.unit;
    *out.add(2) = row.a;
    *out.add(3) = row.b;
    *out.add(4) = row.c;
    0
}

#[no_mangle]
pub unsafe extern "C" fn ka_battle_report(
    battle: *const KaBattle,
    policy: i32,
    out: *mut battle_control::KaBattleReport,
) -> i32 {
    if battle.is_null() || out.is_null() {
        return ai::KA_ERR_CAPACITY;
    }
    *out = battle_control::report(&*battle, policy, 0);
    0
}

/// The additive encounter-telemetry getter. Exported by the same crate but published only in the
/// separately named `ka_kernel_encounter_v2.dll`, so nothing about the default `KaBattleReport`
/// path or `ka_kernel_v9.dll` changes. A caller that loads the encounter DLL must also find this
/// symbol; a missing getter is an explicit error, never a silent fallback. The struct shape is
/// versioned (`KA_ENCOUNTER_VERSION`); v2 adds the true own-death vs first-Leaving split, the
/// enemy roll/resolved split, and the boss post-death counters, so it is a *new filename* that a
/// running process still holding the v1 file can never see.
#[no_mangle]
pub unsafe extern "C" fn ka_encounter_report(
    battle: *const KaBattle,
    policy: i32,
    out: *mut battle_control::KaEncounterReport,
) -> i32 {
    if battle.is_null() || out.is_null() {
        return ai::KA_ERR_CAPACITY;
    }
    *out = battle_control::encounter_report(&*battle, policy);
    0
}

/// `sizeof(KaEncounterReport)`, so the Python mirror can refuse a mismatched layout.
#[no_mangle]
pub extern "C" fn ka_sizeof_encounter_report() -> u32 {
    std::mem::size_of::<battle_control::KaEncounterReport>() as u32
}

/// `KA_ENCOUNTER_VERSION` as an integer, so a loader can refuse a DLL whose report struct happens
/// to be the same size but whose field semantics differ (a version integer is checked as well as
/// `ka_sizeof_encounter_report`). Read-only; never touches a battle.
#[no_mangle]
pub extern "C" fn ka_encounter_version() -> u32 {
    battle_control::KA_ENCOUNTER_VERSION as u32
}

/// Number of animation resource rows.
#[no_mangle]
pub unsafe extern "C" fn ka_animation_resource_count(battle: *const KaBattle) -> u32 {
    if battle.is_null() {
        return 0;
    }
    (*battle).animation_resource_count
}

/// Read one animation resource row.
#[no_mangle]
pub unsafe extern "C" fn ka_animation_resource(
    battle: *const KaBattle,
    slot: u32,
    out_res: *mut i32,
    out_seb: *mut i32,
    out_max_frame: *mut i32,
    out_frame: *mut i32,
) -> i32 {
    if battle.is_null() || slot >= (*battle).animation_resource_count {
        return -1;
    }
    let row = &(*battle).animation_resources[slot as usize];
    if !out_res.is_null() {
        *out_res = row.res;
    }
    if !out_seb.is_null() {
        *out_seb = row.seb;
    }
    if !out_max_frame.is_null() {
        *out_max_frame = row.max_frame;
    }
    if !out_frame.is_null() {
        *out_frame = row.frame;
    }
    0
}

/// Number of created entities (effects, trails, projectiles).
#[no_mangle]
pub unsafe extern "C" fn ka_object_count(battle: *const KaBattle) -> u32 {
    if battle.is_null() {
        return 0;
    }
    (*battle).object_count
}

/// Identity of created entity `slot`.
#[no_mangle]
pub unsafe extern "C" fn ka_object_id(battle: *const KaBattle, slot: u32) -> i32 {
    if battle.is_null() || slot >= (*battle).object_count {
        return -1;
    }
    (*battle).objects[slot as usize].id
}

/// `is_destroyed(identity)`.
#[no_mangle]
pub unsafe extern "C" fn ka_entity_destroyed(battle: *const KaBattle, id: i32) -> i32 {
    if battle.is_null() {
        return 1;
    }
    i32::from((*battle).is_destroyed(id))
}

/// Serialize one entity's components for comparison. Returns the number written.
#[no_mangle]
pub unsafe extern "C" fn ka_entity_components(
    battle: *const KaBattle,
    id: i32,
    out: *mut KaComponentView,
    capacity: u32,
) -> u32 {
    if battle.is_null() || out.is_null() {
        return 0;
    }
    let view = std::slice::from_raw_parts_mut(out, capacity as usize);
    (*battle).export_components(id, view, capacity as usize) as u32
}

/// Number of members in subset `index`, in native slot order.
#[no_mangle]
pub unsafe extern "C" fn ka_subset_count(battle: *const KaBattle, index: u32) -> u32 {
    if battle.is_null() || index as usize >= KA_SUBSET_COUNT {
        return 0;
    }
    (*battle).subsets[index as usize].members.count() as u32
}

/// Member `position` of subset `index`, in native slot order.
#[no_mangle]
pub unsafe extern "C" fn ka_subset_member(
    battle: *const KaBattle,
    index: u32,
    position: u32,
) -> i32 {
    if battle.is_null() || index as usize >= KA_SUBSET_COUNT {
        return -1;
    }
    (*battle)
        .subsets[index as usize]
        .members
        .member(position as usize)
        .unwrap_or(-1)
}

/// `self.param(i, p)['rawValue']` for a fighter identity.
#[no_mangle]
pub unsafe extern "C" fn ka_unit_raw(battle: *const KaBattle, id: i32, parameter: i32) -> i32 {
    if battle.is_null() {
        return 0;
    }
    match (*battle).fighter_slot(id) {
        Some(slot) => (*battle).units[slot].raw(parameter),
        None => 0,
    }
}

/// `value(i, 10)` for a fighter identity.
#[no_mangle]
pub unsafe extern "C" fn ka_unit_hp(battle: *const KaBattle, id: i32) -> i32 {
    if battle.is_null() {
        return 0;
    }
    match (*battle).fighter_slot(id) {
        Some(slot) => (*battle).units[slot].hp(),
        None => 0,
    }
}

/// `self.math_draws`.
#[no_mangle]
pub unsafe extern "C" fn ka_battle_next_identity(battle: *const KaBattle) -> i32 {
    if battle.is_null() {
        return 0;
    }
    (*battle).next_identity
}

// ------------------------------------------------------------------------------------------
// Snapshot surface: load a captured canonical state in, and read a stepped state back out.
// ------------------------------------------------------------------------------------------

/// Load the shared counters the snapshot carries: tick, the next identity and the next `traceId`.
#[no_mangle]
pub unsafe extern "C" fn ka_battle_set_counters(
    battle: *mut KaBattle,
    tick: u32,
    next_identity: i32,
    next_command: i32,
) {
    if battle.is_null() {
        return;
    }
    let state = &mut *battle;
    state.tick = tick;
    state.next_identity = next_identity;
    state.next_command = next_command;
    state.event_count = 0;
}

/// Read back the shared counters.
#[no_mangle]
pub unsafe extern "C" fn ka_battle_counters(
    battle: *const KaBattle,
    tick: *mut u32,
    next_identity: *mut i32,
    next_command: *mut i32,
) {
    if battle.is_null() {
        return;
    }
    let state = &*battle;
    if !tick.is_null() {
        *tick = state.tick;
    }
    if !next_identity.is_null() {
        *next_identity = state.next_identity;
    }
    if !next_command.is_null() {
        *next_command = state.next_command;
    }
}

/// Copy one created entity into the arena slot, so a snapshot can install its whole object table.
/// Returns the resulting object count.
#[no_mangle]
pub unsafe extern "C" fn ka_battle_set_object(
    battle: *mut KaBattle,
    slot: u32,
    entity: *const world::KaEntity,
) -> u32 {
    if battle.is_null() || entity.is_null() {
        return 0;
    }
    let state = &mut *battle;
    if slot as usize >= state.objects.len() {
        return state.object_count;
    }
    state.objects[slot as usize] = *entity;
    if slot + 1 > state.object_count {
        state.object_count = slot + 1;
    }
    state.object_count
}

/// Read one created entity back out.
#[no_mangle]
pub unsafe extern "C" fn ka_object_entity(
    battle: *const KaBattle,
    slot: u32,
    out: *mut world::KaEntity,
) -> i32 {
    if battle.is_null() || out.is_null() || slot >= (*battle).object_count {
        return -1;
    }
    *out = (*battle).objects[slot as usize];
    0
}

/// Install one subset's raw `EntitySlotSet` arrays, so slot order and the free list survive a load.
#[no_mangle]
pub unsafe extern "C" fn ka_battle_set_subset(
    battle: *mut KaBattle,
    index: u32,
    len: u32,
    free_len: u32,
    version: i32,
    slots: *const i32,
    free: *const u32,
) {
    if battle.is_null() || index as usize >= KA_SUBSET_COUNT {
        return;
    }
    let state = &mut *battle;
    let set = &mut state.subsets[index as usize].members;
    let take = (len as usize).min(set.slots.len());
    for slot in 0..take {
        set.slots[slot] = if slots.is_null() { 0 } else { *slots.add(slot) };
    }
    let free_take = (free_len as usize).min(set.free.len());
    for slot in 0..free_take {
        set.free[slot] = if free.is_null() { 0 } else { *free.add(slot) };
    }
    set.len = len.min(set.slots.len() as u32);
    set.free_len = free_len.min(set.free.len() as u32);
    set.version = version;
    set.base = state.first_identity;
    set.rebuild_derived();
}

/// Read one subset's raw arrays back.
#[no_mangle]
pub unsafe extern "C" fn ka_subset_state(
    battle: *const KaBattle,
    index: u32,
    out_len: *mut u32,
    out_free_len: *mut u32,
    out_version: *mut i32,
    out_slots: *mut i32,
    out_free: *mut u32,
) {
    if battle.is_null() || index as usize >= KA_SUBSET_COUNT {
        return;
    }
    let set = &(*battle).subsets[index as usize].members;
    if !out_len.is_null() {
        *out_len = set.len;
    }
    if !out_free_len.is_null() {
        *out_free_len = set.free_len;
    }
    if !out_version.is_null() {
        *out_version = set.version;
    }
    for slot in 0..set.len as usize {
        if !out_slots.is_null() {
            *out_slots.add(slot) = set.slots[slot];
        }
    }
    for slot in 0..set.free_len as usize {
        if !out_free.is_null() {
            *out_free.add(slot) = set.free[slot];
        }
    }
}

/// Install one cell-occupancy bucket. `slot` must be `0..count` in first-insertion order.
#[no_mangle]
pub unsafe extern "C" fn ka_battle_set_bucket(
    battle: *mut KaBattle,
    slot: u32,
    key: i32,
    count: u32,
    ids: *const i32,
) -> u32 {
    if battle.is_null() || slot as usize >= world::KA_MAX_BUCKETS {
        return 0;
    }
    let state = &mut *battle;
    let bucket = &mut state.buckets[slot as usize];
    bucket.key = key;
    let take = (count as usize).min(bucket.ids.len());
    for index in 0..take {
        bucket.ids[index] = if ids.is_null() { 0 } else { *ids.add(index) };
    }
    bucket.count = take as u32;
    if slot + 1 > state.bucket_count {
        state.bucket_count = slot + 1;
    }
    state.bucket_count
}

/// Number of retained occupancy buckets.
#[no_mangle]
pub unsafe extern "C" fn ka_bucket_count(battle: *const KaBattle) -> u32 {
    if battle.is_null() {
        return 0;
    }
    (*battle).bucket_count
}

/// Read one occupancy bucket back.
#[no_mangle]
pub unsafe extern "C" fn ka_bucket_state(
    battle: *const KaBattle,
    slot: u32,
    out_key: *mut i32,
    out_count: *mut u32,
    out_ids: *mut i32,
) -> i32 {
    if battle.is_null() || slot >= (*battle).bucket_count {
        return -1;
    }
    let bucket = &(*battle).buckets[slot as usize];
    if !out_key.is_null() {
        *out_key = bucket.key;
    }
    if !out_count.is_null() {
        *out_count = bucket.count;
    }
    for index in 0..bucket.count as usize {
        if !out_ids.is_null() {
            *out_ids.add(index) = bucket.ids[index];
        }
    }
    0
}

/// Install one recovered RNG stream's complete state. `stream` 0 = math, 1 = lib.
#[no_mangle]
pub unsafe extern "C" fn ka_battle_set_rng(
    battle: *mut KaBattle,
    stream: u32,
    values: *const i32,
    index: i32,
    partner: i32,
    draws: u32,
) {
    if battle.is_null() {
        return;
    }
    let state = &mut *battle;
    let rng = if stream == 0 { &mut state.math } else { &mut state.lib };
    for slot in 0..rng.values.len() {
        rng.values[slot] = if values.is_null() { 0 } else { *values.add(slot) };
    }
    rng.index = index;
    rng.partner = partner;
    if stream == 0 {
        state.math_draws = draws;
    } else {
        state.lib_draws = draws;
    }
}

/// Read one recovered RNG stream's complete state.
#[no_mangle]
pub unsafe extern "C" fn ka_rng_state(
    battle: *const KaBattle,
    stream: u32,
    out_values: *mut i32,
    out_index: *mut i32,
    out_partner: *mut i32,
    out_draws: *mut u32,
) {
    if battle.is_null() {
        return;
    }
    let state = &*battle;
    let (rng, draws) = if stream == 0 {
        (&state.math, state.math_draws)
    } else {
        (&state.lib, state.lib_draws)
    };
    for slot in 0..rng.values.len() {
        if !out_values.is_null() {
            *out_values.add(slot) = rng.values[slot];
        }
    }
    if !out_index.is_null() {
        *out_index = rng.index;
    }
    if !out_partner.is_null() {
        *out_partner = rng.partner;
    }
    if !out_draws.is_null() {
        *out_draws = draws;
    }
}

/// The event count of the last phase call, so a step's ordered events can be compared.
#[no_mangle]
pub unsafe extern "C" fn ka_event_count(battle: *const KaBattle) -> u32 {
    if battle.is_null() {
        return 0;
    }
    (*battle).event_count
}

/// `ka_sizeof` probes, so the Python mirror can refuse to run against a mismatched struct.
#[no_mangle]
pub extern "C" fn ka_sizeof_unit() -> u32 {
    std::mem::size_of::<KaUnit>() as u32
}

#[no_mangle]
pub extern "C" fn ka_sizeof_entity() -> u32 {
    std::mem::size_of::<world::KaEntity>() as u32
}

#[no_mangle]
pub extern "C" fn ka_sizeof_params() -> u32 {
    std::mem::size_of::<params::KaParams>() as u32
}

#[no_mangle]
pub extern "C" fn ka_sizeof_board() -> u32 {
    std::mem::size_of::<state::KaBoard>() as u32
}

#[no_mangle]
pub extern "C" fn ka_sizeof_skill() -> u32 {
    std::mem::size_of::<KaSkill>() as u32
}

#[no_mangle]
pub extern "C" fn ka_sizeof_command() -> u32 {
    std::mem::size_of::<state::KaCommand>() as u32
}

#[no_mangle]
pub extern "C" fn ka_sizeof_effect_spec() -> u32 {
    std::mem::size_of::<EffectSpec>() as u32
}

#[no_mangle]
pub extern "C" fn ka_sizeof_component_view() -> u32 {
    std::mem::size_of::<KaComponentView>() as u32
}

/// The whole battle state is one flat struct; its size is reported so a capacity change is visible
/// in the ABI record rather than only in the arena bounds.
#[no_mangle]
pub extern "C" fn ka_sizeof_battle() -> u32 {
    std::mem::size_of::<KaBattle>() as u32
}

/// Storage capacity of this build, for snapshot buffer sizing with either arena tier.
#[no_mangle]
pub extern "C" fn ka_object_capacity() -> u32 {
    world::KA_MAX_OBJECTS as u32
}

/// The exported report struct's size, so the ctypes mirror cannot drift from this build unnoticed.
#[no_mangle]
pub extern "C" fn ka_sizeof_report() -> u32 {
    std::mem::size_of::<battle_control::KaBattleReport>() as u32
}

// ------------------------------------------------------------------------------------------
// Pure resolution primitives, exposed for targeted parity.
// ------------------------------------------------------------------------------------------

#[no_mangle]
pub extern "C" fn ka_critical_rate(luck: i32) -> i32 {
    combat::critical_rate(luck)
}

#[no_mangle]
pub extern "C" fn ka_hit_rate(dexterity: i32, agility: i32, luck: i32) -> i32 {
    combat::hit_rate(dexterity, agility, luck)
}

#[no_mangle]
pub extern "C" fn ka_modified_rate(rate: i32, skill_value: i32, evasion: u8) -> i32 {
    combat::modified_rate(rate, skill_value, evasion != 0)
}

#[no_mangle]
pub extern "C" fn ka_random_range(raw: i32, low: i32, high: i32) -> i32 {
    combat::random_range(raw, low, high)
}

#[no_mangle]
pub extern "C" fn ka_parabola(height: i32, length: i32, frame: i32) -> i32 {
    combat::parabola(height, length, frame)
}

/// `combat_resolution.base_damage`, consuming the battle's math stream in the recovered order.
#[no_mangle]
pub unsafe extern "C" fn ka_base_damage(
    battle: *mut KaBattle,
    attack: i32,
    defense: i32,
) -> i32 {
    if battle.is_null() {
        return 0;
    }
    combat::base_damage(attack, defense, &mut *battle)
}

/// `combat_resolution.damage_from_parameters` for two roster slots.
#[no_mangle]
pub unsafe extern "C" fn ka_damage_from_parameters(
    battle: *mut KaBattle,
    attacker_slot: u32,
    target_slot: u32,
    critical: u8,
    magical: u8,
) -> i32 {
    if battle.is_null() {
        return 0;
    }
    let state = &mut *battle;
    let attacker = attacker_slot as usize;
    let target = target_slot as usize;
    if attacker >= state.count as usize || target >= state.count as usize {
        return 0;
    }
    combat::damage_from_parameters(state, critical != 0, magical != 0, attacker, target)
}

/// Create one effect from an explicit spec (`create_combat_effect`).
#[no_mangle]
pub unsafe extern "C" fn ka_create_effect(
    battle: *mut KaBattle,
    spec: *const EffectSpec,
) -> i32 {
    if battle.is_null() || spec.is_null() {
        return -1;
    }
    match combat::create_effect(&mut *battle, &*spec) {
        Ok(identity) => identity,
        Err(code) => code,
    }
}

/// `fire_shared_projectile` on an existing identity; returns the `length` field it stored.
#[no_mangle]
pub unsafe extern "C" fn ka_fire_projectile(
    battle: *mut KaBattle,
    identity: i32,
    start_x: f32,
    start_y: f32,
    start_z: f32,
    end_x: f32,
    end_y: f32,
    end_z: f32,
    speed: i32,
    owner: i32,
    animate: u8,
    attack_skill: i32,
) -> i32 {
    if battle.is_null() {
        return 0;
    }
    combat::fire_shared_projectile(
        &mut *battle,
        identity,
        world::KaVec3 { x: start_x, y: start_y, z: start_z },
        world::KaVec3 { x: end_x, y: end_y, z: end_z },
        speed,
        owner,
        animate != 0,
        attack_skill,
    )
}

/// `SharedControllers.attack` on two roster slots; `skill < 0` is the Python `None`.
#[no_mangle]
pub unsafe extern "C" fn ka_attack(
    battle: *mut KaBattle,
    attacker_slot: u32,
    target_id: i32,
    skill: i32,
    out: *mut AttackResult,
) -> i32 {
    if battle.is_null() {
        return ai::KA_ERR_CAPACITY;
    }
    let state = &mut *battle;
    let attacker = attacker_slot as usize;
    if attacker >= state.count as usize {
        return ai::KA_ERR_CAPACITY;
    }
    match combat::attack(state, attacker, target_id, if skill < 0 { None } else { Some(skill) }) {
        Ok(result) => {
            if !out.is_null() {
                *out = result;
            }
            0
        }
        Err(code) => code,
    }
}

/// `SharedControllers.use` for one queued command slot.
#[no_mangle]
pub unsafe extern "C" fn ka_use_skill(
    battle: *mut KaBattle,
    caster_slot: u32,
    command: *const state::KaCommand,
    index: i32,
    out_used: *mut i32,
) -> i32 {
    if battle.is_null() || command.is_null() {
        return ai::KA_ERR_CAPACITY;
    }
    let state = &mut *battle;
    let caster = caster_slot as usize;
    if caster >= state.count as usize {
        return ai::KA_ERR_CAPACITY;
    }
    match combat::use_skill(state, caster, &*command, index) {
        Ok(used) => {
            if !out_used.is_null() {
                *out_used = i32::from(used);
            }
            0
        }
        Err(code) => code,
    }
}

/// `ai::animate` / `ai::change`, so the harness can drive single transitions.
#[no_mangle]
pub unsafe extern "C" fn ka_change_state(battle: *mut KaBattle, slot: u32, state_id: i32) -> i32 {
    if battle.is_null() {
        return ai::KA_ERR_CAPACITY;
    }
    let state = &mut *battle;
    if slot as usize >= state.count as usize {
        return ai::KA_ERR_CAPACITY;
    }
    match ai::change(state, slot as usize, state_id) {
        Ok(()) => 0,
        Err(code) => code,
    }
}

/// The original spatial/targeting slice, kept so its recorded parity evidence stays runnable.

/// The original spatial/targeting slice, kept so its recorded parity evidence stays runnable.
#[no_mangle]
pub unsafe extern "C" fn ka_spatial_phase(
    battle: *mut KaBattle,
    out_target: *mut i32,
    out_distance: *mut i32,
    out_same_grid: *mut u8,
) -> u32 {
    if battle.is_null() {
        return 0;
    }
    let state = &mut *battle;
    let count = state.count as usize;
    for query in 0..count {
        let query_team = state.units[query].team;
        let mut best_index: i32 = -1;
        let mut best_distance: i32 = i32::MAX;
        for index in 0..count {
            if index == query || !state.is_eligible(index, query_team) {
                continue;
            }
            let distance = state.distance(query, index);
            // Strictly smaller: Python's min keeps the first minimum in iteration order.
            if distance < best_distance {
                best_distance = distance;
                best_index = index as i32;
            }
        }
        if !out_target.is_null() {
            *out_target.add(query) = best_index;
        }
        if !out_distance.is_null() {
            *out_distance.add(query) = if best_index < 0 { -1 } else { best_distance };
        }
        if !out_same_grid.is_null() {
            *out_same_grid.add(query) = if state.same_grid(query) { 1 } else { 0 };
        }
    }
    state.tick = state.tick.wrapping_add(1);
    count as u32
}

/// Repeat the spatial phase `iterations` times on native-resident state without crossing the
/// boundary, for the architecture-cost measurement.
#[no_mangle]
pub unsafe extern "C" fn ka_spatial_phase_many(
    battle: *mut KaBattle,
    iterations: u32,
    out_target: *mut i32,
    out_distance: *mut i32,
    out_same_grid: *mut u8,
) -> u32 {
    if battle.is_null() {
        return 0;
    }
    let mut processed = 0;
    for _ in 0..iterations {
        processed = ka_spatial_phase(battle, out_target, out_distance, out_same_grid);
    }
    processed
}
