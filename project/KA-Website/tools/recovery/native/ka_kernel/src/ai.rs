//! The ported fighter phase: `update_fighters -> SharedControllers.update -> decide -> choose`.
//!
//! Every function here is a transcription of recovered Python, named after its source, so the
//! differential harness can point at both sides of each rule:
//!
//! | Native                          | Python source                                          |
//! |---------------------------------|--------------------------------------------------------|
//! | `trunc_div`, `i32_of`           | `combat_initial_state.trunc_div/i32`                   |
//! | `linear_easing`, `skill_mp_cost`| `combat_resolution.linear_easing/skill_mp_cost`         |
//! | `random_below`                  | `combat_resolution.random_below`                       |
//! | `decide_skill_invocation`       | `combat_resolution.decide_skill_invocation`             |
//! | `attack_interval`               | `combat_resolution.attack_interval`                     |
//! | `is_most_front`, `decide_next_state` | `combat_states.is_most_front/decide_next_state`    |
//! | `line_cells`, `battle_band`     | `combat_geometry.line_cells/battle_band`                |
//! | `fighter_path`, `battle_move_base` | `combat_geometry.fighter_path/battle_move_base`      |
//! | `same_grid`, `queue_position`   | `combat_navigation.same_grid/queue_position`            |
//! | `nearest_skill_target`          | `combat_targeting.nearest_skill_target`                 |
//! | `active_skill_infos`, `attack_skill_candidates` | `combat_skill_selection.*`            |
//! | `can_use_skill`                 | `combat_skills.can_use_skill`                           |
//! | `candidates`, `choose`, `decide`, `change`, `update` | `combat_shared_controllers.SharedControllers.*` |
//! | `update_fighters`               | `combat_tick.update_fighters`                           |
//! | `execute_skill_queue`           | `combat_commands.execute_skill_queue`                   |
//!
//! Two boundaries are explicit rather than silently approximated:
//!
//! * `use_skill` inside the command queue is reported as an event, not applied. Applying it mutates
//!   HP/world state through the attack, projectile, cure and buff routes, which are later slices.
//!   The queue bookkeeping (tick, duration, use index, animation rate/frame, removal) is exact.
//! * States 4 (attacking), 6 (damaging), 7 (knocking down) and 8 (leaving) enter world-dependent
//!   handlers; they return [`KA_ERR_UNSUPPORTED_STATE`] instead of guessing.

// The ported loops index fixed-capacity arrays by slot on purpose: the slot order *is* the recovered
// iteration order, and several of them step two parallel arrays with one cursor.
#![allow(clippy::needless_range_loop)]
// Several recovered routines take many independent flags, mirroring the Python keyword arguments
// one for one; splitting them into a struct would hide that correspondence.
#![allow(clippy::too_many_arguments)]

use crate::rng::i32_of;
use crate::state::*;

/// `update`/`change` reached a state whose handler needs the world (projectiles, prizes, damage).
pub const KA_ERR_UNSUPPORTED_STATE: i32 = -1;
/// A transition required an unimplemented enter/exit handler.
pub const KA_ERR_UNSUPPORTED_TRANSITION: i32 = -2;
/// A skill id was used that the battle's row table does not carry.
pub const KA_ERR_UNKNOWN_SKILL_ROW: i32 = -3;
/// A fixed-capacity buffer (board, queue, path, recursion depth) would have overflowed.
pub const KA_ERR_CAPACITY: i32 = -4;
/// `decide_skill_invocation` got an invocation level outside the recovered rate table.
pub const KA_ERR_INVOCATION_LEVEL: i32 = -5;
/// A queued command was not the recovered opcode 29.
pub const KA_ERR_UNSUPPORTED_OPCODE: i32 = -6;

/// `AISystem` command execution event, recorded instead of applied.
pub const KA_EVENT_USE: i32 = 5;

/// `line_cells` returns at most `shooting_range` cells; `battle_band` at most `5 * depth`.
pub const KA_MAX_CELLS: usize = 48;

/// A materialized cell list (`line_cells` / `battle_band`).
#[repr(C)]
#[derive(Clone, Copy)]
pub struct KaCells {
    pub len: u32,
    pub cells: [KaPoint; KA_MAX_CELLS],
}

impl Default for KaCells {
    fn default() -> Self {
        KaCells { len: 0, cells: [KaPoint::default(); KA_MAX_CELLS] }
    }
}

/// One candidate the controller would consider, in yield order.
#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct KaCandidate {
    /// `skill['id']`.
    pub skill: i32,
    /// The invocation level paired with the *filtered* ordinal, as the Python does.
    pub level: i32,
    /// Target entity index, or `-1` for the Python `None`.
    pub target: i32,
}

// --------------------------------------------------------------------------------------------
// Recovered integer/float primitives
// --------------------------------------------------------------------------------------------

/// Python floor division for integers (`a // b`), matching `combat_initial_state.trunc_div`'s use
/// of a possibly negative divisor.
#[inline]
fn floor_div(a: i64, b: i64) -> i64 {
    let quotient = a / b;
    let remainder = a % b;
    if remainder != 0 && (remainder < 0) != (b < 0) {
        quotient - 1
    } else {
        quotient
    }
}

/// `combat_initial_state.trunc_div(value, divisor)`.
#[inline]
pub fn trunc_div(value: i32, divisor: i32) -> i32 {
    let magnitude = floor_div(value.unsigned_abs() as i64, divisor as i64);
    let signed = if value < 0 { -magnitude } else { magnitude };
    signed as i32
}

/// `combat_resolution.native_float_to_int`: f32 -> truncation, with NaN -> 0 and infinity -> INT_MIN.
pub fn native_float_to_int(value: f32) -> i32 {
    if value.is_nan() {
        return 0;
    }
    if value.is_infinite() {
        return i32::MIN;
    }
    let truncated = value.trunc();
    if truncated <= i32::MIN as f32 {
        i32::MIN
    } else if truncated >= i32::MAX as f32 {
        i32::MAX
    } else {
        truncated as i32
    }
}

/// `combat_resolution.linear_easing(low, high, steps, progress)`.
pub fn linear_easing(low: i32, high: i32, steps: i32, progress: i32) -> i32 {
    if progress < 0 {
        return low;
    }
    if progress >= steps {
        return high;
    }
    let fraction = progress as f32 / steps as f32;
    let span = i32_of(high as i64 - low as i64) as f32;
    let value = (fraction * span + low as f32).trunc() as i64;
    let value = value.clamp(i32::MIN as i64, i32::MAX as i64) as i32;
    let lower = low.min(high);
    let upper = low.max(high);
    value.clamp(lower, upper)
}

/// `combat_resolution.skill_mp_cost`.
pub fn skill_mp_cost(minimum: i32, maximum: i32, average_level: i32, monster: bool) -> i32 {
    if monster || (minimum == 0 && maximum == 0) {
        return minimum;
    }
    linear_easing(minimum, maximum, 998, i32_of(average_level as i64 - 1))
}

/// `combat_initial_state.effective_parameter_rate`.
pub fn effective_parameter_rate(value: i32, maximum: i32) -> i32 {
    if maximum == 0 {
        return 0;
    }
    let product = i32_of(value as i64 * 100);
    let magnitude = floor_div(product.unsigned_abs() as i64, maximum.unsigned_abs() as i64);
    let signed = if (product < 0) != (maximum < 0) { -magnitude } else { magnitude };
    let rate = signed.clamp(0, 100) as i32;
    if rate == 0 {
        i32::from(value > 0)
    } else {
        rate
    }
}

/// `combat_resolution.random_below(raw_next_int, limit)`.
#[inline]
pub fn random_below(raw_next_int: i32, limit: i32) -> i32 {
    if limit == 0 {
        return 0;
    }
    let magnitude = if raw_next_int < 0 { i32_of(-(raw_next_int as i64)) } else { raw_next_int };
    i32_of(magnitude as i64 - trunc_div(magnitude, limit) as i64 * limit as i64)
}

/// `combat_resolution.skill_invocation_rate`; `None` where the Python raises.
pub fn skill_invocation_rate(kind: i32, invocation_level: i32) -> Option<i32> {
    let rates: [i32; 3] = if kind == 15 {
        [100, 60, 30]
    } else if kind == 2 {
        [80, 50, 30]
    } else {
        [36, 18, 9]
    };
    if invocation_level < 0 || invocation_level as usize >= rates.len() {
        None
    } else {
        Some(rates[invocation_level as usize])
    }
}

/// `combat_resolution.attack_interval`. `pow` stays the documented host-libm dependency the
/// Python comment describes; the harness measures whether the two libm builds agree bit for bit.
pub fn attack_interval(agility: i32) -> i32 {
    let agility = i32_of(agility as i64).clamp(0, 99999);
    if agility == 0 {
        return i32::MIN;
    }
    let value = (1.0f64 / ((agility as f64 / 25.0).powf(0.362) / 1.5)) * 20.0;
    value.trunc() as i32
}

// --------------------------------------------------------------------------------------------
// Recovered geometry
// --------------------------------------------------------------------------------------------

/// `combat_geometry.line_cells`.
pub fn line_cells(x: i32, y: i32, dx: i32, dy: i32, shooting_range: i32) -> KaCells {
    let mut cells = KaCells::default();
    let mut index = 1i32;
    while index <= shooting_range && (cells.len as usize) < KA_MAX_CELLS {
        let slot = cells.len as usize;
        cells.cells[slot] = KaPoint { x: x + dx * index, y: y + dy * index };
        cells.len += 1;
        index += 1;
    }
    cells
}

/// `combat_geometry.battle_band`.
pub fn battle_band(y: i32, direction: i32, depth: i32) -> KaCells {
    let mut cells = KaCells::default();
    let start_y = if direction == 0 { y - depth } else { y + 1 };
    let mut row = start_y;
    while row < start_y + depth {
        for x in 0..5 {
            if (cells.len as usize) >= KA_MAX_CELLS {
                return cells;
            }
            let slot = cells.len as usize;
            cells.cells[slot] = KaPoint { x, y: row };
            cells.len += 1;
        }
        row += 1;
    }
    cells
}

/// `combat_geometry.fighter_path` - two truncated points, float32 arithmetic throughout.
///
/// `sx - sx` and `tz - tz` look like identities but are part of the recovered float pipeline: for a
/// non-finite input they produce NaN, and the `det == 0` branch below depends on which of them is
/// NaN, so they are kept rather than simplified away.
#[allow(clippy::eq_op)]
pub fn fighter_path(start: (f32, f32), target: (f32, f32)) -> [KaPoint; 2] {
    let (sx, sz) = start;
    let (tx, tz) = target;
    let a0 = sx - sx;
    let a1 = tx - (tx + 1.0);
    let b0 = (sz + 1.0) - sz;
    let b1 = tz - tz;
    let det = a0 * b1 - b0 * a1;
    let c0 = sx * (-b0) - a0 * sz;
    let c1 = tx * (-b1) - a1 * tz;
    let divide = |numerator: f32| -> f32 {
        if det == 0.0 {
            if numerator == 0.0 || numerator.is_nan() {
                f32::NAN
            } else {
                let sign = numerator.signum() * det.signum();
                f32::INFINITY.copysign(sign)
            }
        } else {
            numerator / det
        }
    };
    let cross_x = divide(a1 * c0 - a0 * c1);
    let cross_z = divide(b0 * c1 - c0 * b1);
    [
        KaPoint { x: native_float_to_int(cross_x), y: native_float_to_int(cross_z) },
        KaPoint { x: native_float_to_int(tx), y: native_float_to_int(tz) },
    ]
}

/// `combat_geometry.battle_move_base`; returns `(arrived, position, velocity)`.
pub fn battle_move_base(
    position: (f32, f32),
    target: (f32, f32),
    speed: f32,
    previous_velocity: (f32, f32),
) -> (bool, (f32, f32), (f32, f32)) {
    let (px, pz) = position;
    let (tx, tz) = target;
    let dx = tx - px;
    let dz = tz - pz;
    let distance2 = dx * dx + dz * dz;
    if distance2 <= speed * speed {
        return (true, (tx, tz), previous_velocity);
    }
    let mut speed = speed;
    let distance = distance2.sqrt();
    if distance < speed {
        speed = distance;
    }
    (false, (px, pz), (dx * speed / distance, dz * speed / distance))
}

/// `combat_states.cell_to_grid`.
pub fn cell_to_grid(team: i32, row_offset: i32, cell: (i32, i32)) -> i32 {
    let row = i32_of(cell.1 as i64 - row_offset as i64 - if team == 0 { 1 } else { 0 });
    let row = trunc_div(row, if team == 0 { 1 } else { -1 });
    i32_of(row as i64 * 5 + cell.0 as i64)
}

// --------------------------------------------------------------------------------------------
// Recovered predicates and navigation
// --------------------------------------------------------------------------------------------

/// `combat_states.is_most_front`.
#[inline]
pub fn is_most_front(grid: i32) -> bool {
    (i32_of(grid as i64 + 4) as u32) < 9
}

/// `combat_states.is_normal_attack_target`.
#[inline]
pub fn is_normal_attack_target(exists: bool, hp: i32, state: i32) -> bool {
    exists && hp > 0 && matches!(state, 1 | 3 | 4 | 6)
}

/// `combat_navigation.same_grid`: another unit of the same roster on the same grid and not
/// KnockingDown(7)/Leaving(8)/Moving(2).
pub fn same_grid(battle: &KaBattle, query: usize, roster: &[i32], roster_len: usize) -> bool {
    let grid = battle.units[query].grid();
    for slot in 0..roster_len {
        let index = roster[slot] as usize;
        if index == query {
            continue;
        }
        let state = battle.units[index].state();
        if state != 2 && state != 7 && state != 8 && battle.units[index].grid() == grid {
            return true;
        }
    }
    false
}

/// `combat_navigation.queue_position`. Returns the nullable destination pair.
pub fn queue_position(
    battle: &KaBattle,
    query: usize,
    roster: &[i32],
    roster_len: usize,
    row_offset: i32,
) -> (i32, i32) {
    let mut counts = [0i32; 5];
    for column in 0..5i32 {
        let mut total = 0i32;
        for slot in 0..roster_len {
            let index = roster[slot] as usize;
            if battle.units[index].hp_value() <= 0 {
                continue;
            }
            let grid = battle.units[index].grid();
            if i32_of(grid as i64 - trunc_div(grid, 5) as i64 * 5) == column {
                total += 1;
            }
        }
        counts[column as usize] = total;
    }
    let mut column = 0usize;
    for candidate in 1..5usize {
        if counts[candidate] < counts[column] {
            column = candidate;
        }
    }
    let row = counts[column] + 1;
    let team = battle.units[query].board_team();
    let y = if team == 0 { row_offset + 1 + row } else { row_offset - row };
    (column as i32 * 24, y * 24)
}

/// `combat_states.decide_next_state`. `destination` is `None` for the Python `None`.
pub fn decide_next_state(
    state: i32,
    team: i32,
    cell: (i32, i32),
    most_front: bool,
    long_range: bool,
    skill_candidate: bool,
    same_grid_flag: bool,
    advanceable: bool,
    queue: Option<(i32, i32)>,
) -> (i32, Option<(i32, i32)>) {
    let steps = (24i32, 24i32);
    if state != 2 && state != 7 && state != 8 {
        if !(0..5).contains(&cell.0) {
            if let Some((qx, qy)) = queue {
                return (2, Some((i32_of(qx as i64), i32_of(qy as i64))));
            }
        }
        if same_grid_flag || advanceable {
            let backward = if team == 0 { 1 } else { -1 };
            let direction = if same_grid_flag { backward } else { -backward };
            return (
                2,
                Some((i32_of(cell.0 as i64 * steps.0 as i64), i32_of(i32_of(cell.1 as i64 + direction as i64) as i64 * steps.1 as i64))),
            );
        }
    }
    let next = if most_front || long_range || skill_candidate { 3 } else { 1 };
    (next, None)
}

/// `combat_targeting.nearest_skill_target`: strict minimum, first tie preserved.
///
/// `order` holds *identities*, exactly as `self.target_order` does, and the predicate receives an
/// identity; the roster slot is resolved only for the cell read.
pub fn nearest_skill_target<F>(
    battle: &KaBattle,
    caster: usize,
    order: &[i32],
    order_len: usize,
    shooting_range: i32,
    mut predicate: F,
) -> Option<i32>
where
    F: FnMut(i32) -> bool,
{
    let mut chosen: Option<i32> = None;
    let mut best = i32::MAX;
    for slot in 0..order_len {
        let target = order[slot];
        if target == battle.units[caster].identity {
            continue;
        }
        let index = match battle.fighter_slot(target) {
            Some(index) => index,
            None => continue,
        };
        let distance = manhattan(battle, caster, index);
        if distance <= shooting_range && predicate(target) && distance < best {
            chosen = Some(target);
            best = distance;
        }
    }
    chosen
}

/// `SharedControllers.distance` over `components[5]`.
#[inline]
pub fn manhattan(battle: &KaBattle, left: usize, right: usize) -> i32 {
    let a = &battle.units[left];
    let b = &battle.units[right];
    (a.cell_x() - b.cell_x()).abs() + (a.cell_y() - b.cell_y()).abs()
}

/// `SharedControllers.skill_cells`.
pub fn skill_cells(battle: &KaBattle, index: usize, row: &KaSkill) -> KaCells {
    let unit = &battle.units[index];
    let x = unit.cell_x();
    let y = unit.cell_y();
    let direction = unit.direction();
    if row.kind == 26 {
        let offsets = [(0i32, -1i32), (1, 0), (0, 1), (-1, 0)];
        let (dx, dy) = offsets[(direction.rem_euclid(4)) as usize];
        return line_cells(x, y, dx, dy, row.shooting_range);
    }
    if row.kind == 27 {
        return battle_band(y, direction, row.range);
    }
    KaCells::default()
}

/// `SharedControllers.opponent_in_range` over `opponent_in_skill_cells`.
pub fn opponent_in_range(battle: &KaBattle, index: usize, row: &KaSkill) -> bool {
    let cells = skill_cells(battle, index, row);
    let team = battle.units[index].team;
    for target in 0..battle.count as usize {
        let other = &battle.units[target];
        if other.team == team {
            continue;
        }
        if matches!(other.state(), 7 | 8) {
            continue;
        }
        for slot in 0..cells.len as usize {
            if cells.cells[slot].x == other.cell_x() && cells.cells[slot].y == other.cell_y() {
                return true;
            }
        }
    }
    false
}

/// The recovered `rate(i, 10)` - effective HP fraction from the supplied effective reads.
#[inline]
pub fn hp_rate(battle: &KaBattle, index: usize) -> i32 {
    let unit = &battle.units[index];
    effective_parameter_rate(unit.hp_value(), unit.param_maximum(10))
}

/// `combat_skill_selection.active_skill_infos`: filter on `flags & 8`, `not flags & 32` and
/// affordability, then pair each surviving row with `invocationLevels[filtered ordinal]`.
///
/// The ordinal is deliberately the *filtered* index, not the original equipped slot, and the
/// Python comment records that it is intentionally not repaired.
pub fn active_skill_infos(
    battle: &KaBattle,
    index: usize,
    out: &mut [(i32, i32); KA_MAX_SKILLS],
) -> Result<usize, i32> {
    let unit = &battle.units[index];
    let mut len = 0usize;
    let mut ordinal = 0usize;
    for slot in 0..unit.skill_count as usize {
        let id = unit.skill_ids[slot];
        let row = battle.row(id).copied().ok_or(KA_ERR_UNKNOWN_SKILL_ROW)?;
        let affordable = unit.mp_value() >= skill_cost(battle, index, &row);
        if row.flags & 8 != 0 && row.flags & 32 == 0 && affordable {
            if ordinal >= unit.level_count as usize {
                return Err(KA_ERR_INVOCATION_LEVEL);
            }
            if len >= out.len() {
                return Err(KA_ERR_CAPACITY);
            }
            out[len] = (id, unit.levels[ordinal]);
            len += 1;
            ordinal += 1;
        }
    }
    Ok(len)
}

/// `SharedControllers.cost(i, s)`; monsters pay the flat minimum.
#[inline]
pub fn skill_cost(battle: &KaBattle, index: usize, row: &KaSkill) -> i32 {
    let unit = &battle.units[index];
    skill_mp_cost(
        row.min_mp,
        row.max_mp,
        unit.params.average_training_level(unit.human != 0),
        unit.human == 0,
    )
}

/// `combat_skills.can_use_skill`.
#[allow(clippy::too_many_arguments)]
pub fn can_use_skill(
    battle: &KaBattle,
    index: usize,
    row: &KaSkill,
    target: Option<i32>,
    check_mp: bool,
    cost: i32,
) -> bool {
    let unit = &battle.units[index];
    if row.category == 2 {
        return false;
    }
    if check_mp && unit.mp_value() < cost {
        return false;
    }
    let target_index = target.and_then(|t| battle.fighter_slot(t));
    let target_exists = target.map(|t| battle.target_exists(t)).unwrap_or(false);
    if row.flags & 16 != 0 && !target_exists {
        return false;
    }
    if row.required_equip_type != -1 && unit.weapon_type != row.required_equip_type {
        return false;
    }
    if row.category == 1 {
        let target_rate = target_index.map(|t| hp_rate(battle, t)).unwrap_or(0);
        let target_hp = target_index.map(|t| battle.units[t].hp_value()).unwrap_or(0);
        if row.kind == 2 && !(1..=99).contains(&target_rate) {
            return false;
        }
        if row.kind == 15 && target_hp > 0 {
            return false;
        }
    }
    let invoking = battle.units[index]
        .invoking[..unit.invoking_count as usize]
        .iter()
        .any(|entry| entry.skill == row.id);
    if invoking {
        return false;
    }
    if row.kind == 26 || row.kind == 27 || row.flags & 0x60000 != 0 {
        return is_most_front(unit.grid()) && opponent_in_range(battle, index, row);
    }
    true
}

/// `SharedControllers.eligible`.
pub fn eligible(battle: &KaBattle, index: usize, row: &KaSkill, target: Option<i32>, first: bool) -> bool {
    let cost = skill_cost(battle, index, row);
    can_use_skill(battle, index, row, target, first, cost)
}

/// `SharedControllers.candidates`, in yield order: supported recovery rows first, then the
/// attack-category rows through `attack_skill_candidates`.
pub fn candidates(
    battle: &KaBattle,
    index: usize,
    out: &mut [KaCandidate; KA_MAX_SKILLS],
) -> Result<usize, i32> {
    let mut infos = [(0i32, 0i32); KA_MAX_SKILLS];
    let info_len = active_skill_infos(battle, index, &mut infos)?;
    let mut len = 0usize;
    let order = target_order(battle);
    let order_len = battle.count as usize;

    // Recovery before attack, preserving the filtered input order.
    for slot in 0..info_len {
        let (id, level) = infos[slot];
        let row = battle.row(id).copied().ok_or(KA_ERR_UNKNOWN_SKILL_ROW)?;
        if row.category != 1 {
            continue;
        }
        let target = nearest_skill_target(battle, index, &order, order_len, row.shooting_range, |t| {
            match battle.fighter_slot(t) {
                Some(slot) => {
                    battle.units[slot].team == battle.units[index].team && hp_rate(battle, slot) < 100
                }
                None => false,
            }
        });
        if eligible(battle, index, &row, target, true) {
            if len >= out.len() {
                return Err(KA_ERR_CAPACITY);
            }
            out[len] = KaCandidate { skill: id, level, target: target.unwrap_or(-1) };
            len += 1;
        }
    }

    // Attack-category branch: category 0, not flags & 64.
    for slot in 0..info_len {
        let (id, level) = infos[slot];
        let row = battle.row(id).copied().ok_or(KA_ERR_UNKNOWN_SKILL_ROW)?;
        if row.category != 0 || row.flags & 64 != 0 {
            continue;
        }
        let target = if row.flags & 16 != 0 {
            nearest_skill_target(battle, index, &order, order_len, row.shooting_range, |t| {
                match battle.fighter_slot(t) {
                    Some(slot) => {
                        battle.units[slot].team != battle.units[index].team && hp_rate(battle, slot) > 0
                    }
                    None => false,
                }
            })
        } else {
            None
        };
        if eligible(battle, index, &row, target, true) {
            if len >= out.len() {
                return Err(KA_ERR_CAPACITY);
            }
            out[len] = KaCandidate { skill: id, level, target: target.unwrap_or(-1) };
            len += 1;
        }
    }
    Ok(len)
}

/// `self.target_order = sorted(self.units, key=lambda i: not self.specs[i]['human'])`:
/// the Human subset in ascending entity order, then the Monster subset, stable.
///
/// The entries are *identities* (`self.units` is keyed by identity), not roster slots.
pub fn target_order(battle: &KaBattle) -> [i32; KA_MAX_UNITS] {
    let mut order = [-1i32; KA_MAX_UNITS];
    let mut len = 0usize;
    for index in 0..battle.count as usize {
        if battle.units[index].human != 0 {
            order[len] = battle.units[index].identity;
            len += 1;
        }
    }
    for index in 0..battle.count as usize {
        if battle.units[index].human == 0 {
            order[len] = battle.units[index].identity;
            len += 1;
        }
    }
    order
}

/// `SharedControllers.invoke`: `decide_skill_invocation`'s single lazily-consumed math draw.
pub fn invoke(battle: &mut KaBattle, index: usize, row: &KaSkill, level: i32) -> Result<bool, i32> {
    if row.flags & 8192 != 0 {
        let actor = battle.units[index].identity;
        battle.push_event(KA_EVENT_INVOCATION, actor, row.id, level, 1, 0, 0);
        return Ok(true);
    }
    let rate = skill_invocation_rate(row.kind, level).ok_or(KA_ERR_INVOCATION_LEVEL)?;
    let rolled = battle.next_math(100);
    let passed = rolled < rate;
    let actor = battle.units[index].identity;
    battle.push_event(KA_EVENT_INVOCATION, actor, row.id, level, i32::from(passed), 0, 0);
    Ok(passed)
}

/// `SharedControllers.choose`: the first candidate whose invocation roll passes.
pub fn choose(battle: &mut KaBattle, index: usize) -> Result<Option<(i32, i32)>, i32> {
    let mut list = [KaCandidate::default(); KA_MAX_SKILLS];
    let len = candidates(battle, index, &mut list)?;
    for slot in 0..len {
        let candidate = list[slot];
        let row = battle.row(candidate.skill).copied().ok_or(KA_ERR_UNKNOWN_SKILL_ROW)?;
        if invoke(battle, index, &row, candidate.level)? {
            return Ok(Some((candidate.skill, candidate.target)));
        }
    }
    Ok(None)
}

/// `SharedControllers.decide`, split so the writes land after every read.
fn decide_state(battle: &KaBattle, index: usize) -> Result<(i32, Option<(i32, i32)>), i32> {
    let unit = &battle.units[index];
    let state = unit.state();
    let team = unit.board_team();
    let cell = (unit.cell_x(), unit.cell_y());
    let most_front = is_most_front(unit.grid());
    let long_range = unit.weapon_shooting_range > 1;

    if battle.movement_enabled == 0 {
        let mut list = [KaCandidate::default(); KA_MAX_SKILLS];
        let has_candidate = candidates(battle, index, &mut list)? > 0;
        let next = if most_front || long_range || has_candidate { 3 } else { 1 };
        return Ok((next, None));
    }

    let mut roster = [0i32; KA_MAX_UNITS];
    let roster_len = battle.roster(team, &mut roster);

    // `empty_cell` over the forward bucket. `occupancy.buckets.get(key, ())` is never `None`, so a
    // cell with no bucket is empty; an occupant blocks only when it carries an AI component
    // (`has_ai`) and sits in a state other than KnockingDown(7)/Leaving(8).
    let forward_y = cell.1 + if team == 0 { -1 } else { 1 };
    let forward_key = battle.cell_key([cell.0, forward_y]);
    let mut blocked = false;
    if let Some(slot) = (0..battle.bucket_count as usize)
        .find(|&slot| battle.buckets[slot].key == forward_key)
    {
        let bucket = &battle.buckets[slot];
        for position in 0..bucket.count as usize {
            let occupant = bucket.ids[position];
            if !battle.has(occupant, crate::world::SLOT_AI) {
                continue;
            }
            if let Some(fighter) = battle.fighter_slot(occupant) {
                let occupant_state = battle.units[fighter].state();
                if occupant_state != 7 && occupant_state != 8 {
                    blocked = true;
                    break;
                }
            }
        }
    }
    let advanceable = !most_front && !blocked;

    let same_grid_flag = same_grid(battle, index, &roster, roster_len);
    let queue = if state != 2 && state != 7 && state != 8 && !(0..5).contains(&cell.0) {
        Some(queue_position(battle, index, &roster, roster_len, battle.row_offset))
    } else {
        None
    };

    let (mut next, mut destination) = decide_next_state(
        state, team, cell, most_front, long_range, false, same_grid_flag, advanceable, queue,
    );
    if next == 1 {
        let mut list = [KaCandidate::default(); KA_MAX_SKILLS];
        let has_candidate = candidates(battle, index, &mut list)? > 0;
        let (rerun, redestination) = decide_next_state(
            state, team, cell, most_front, long_range, has_candidate, same_grid_flag, advanceable,
            queue,
        );
        next = rerun;
        destination = redestination;
    }
    Ok((next, destination))
}

/// `SharedControllers.decide`.
pub fn decide(battle: &mut KaBattle, index: usize) -> Result<i32, i32> {
    let (state, destination) = decide_state(battle, index)?;
    if let Some((x, y)) = destination {
        battle.units[index].board.set(13, x as i64);
        battle.units[index].board.set(14, y as i64);
    }
    Ok(state)
}

/// `SharedControllers.animate` with the recovered behaviour id (`ChangeAnimation` is
/// `combat_animation_selection.change_combat_animation`: it selects the SEB clip and writes it.
pub fn animate(battle: &mut KaBattle, index: usize, behavior: i32) {
    let clip = combat_clip(battle, index, behavior);
    let actor = battle.units[index].identity;
    battle.push_event(KA_EVENT_ANIMATION, actor, behavior, clip.unwrap_or(-1), 0, 0, 0);
    let unit = &mut battle.units[index];
    if let Some(clip) = clip {
        unit.body.seb[1] = clip;
        unit.body.seb[2] = 0;
        unit.body.animation[1] = -1;
    }
}

/// `combat_animation_selection.combat_clip`. `None` where the Python raises
/// (`NotImplementedError` for an unintegrated behaviour, a vehicle source or a missing human base).
pub fn combat_clip(battle: &KaBattle, index: usize, behavior: i32) -> Option<i32> {
    const COMBAT_BEHAVIORS: [i32; 19] = [0, 1, 2, 3, 4, 5, 7, 10, 11, 12, 15, 16, 28, 30, 31, 32,
                                         9, 37, 4];
    if !COMBAT_BEHAVIORS.contains(&behavior) {
        return None;
    }
    let unit = &battle.units[index];
    let direction = unit.body.direction;
    let base = if unit.human != 0 {
        if behavior < 0 || behavior as usize >= battle.human_base_count as usize {
            return None;
        }
        let mut base = battle.human_bases[behavior as usize];
        let hp_rate = hp_rate(battle, index);
        if hp_rate <= 20 && (behavior == 2 || behavior == 3) {
            base = if behavior == 2 { 104 } else { 192 };
        } else if (behavior == 1 || behavior == 2) && unit.special_human != 0 {
            base = 196;
        }
        base
    } else {
        (if behavior == 4 { 16 } else { 0 }) + (if unit.monster_size == 3 { 4 } else { 0 })
    };
    Some(i32_of(base as i64 + direction as i64))
}

/// `combat_spatial.battle_cell(x, z, 24, 24)`.
#[inline]
pub fn battle_cell_pair(x: i32, z: i32) -> (i32, i32) {
    (trunc_div(x, 24), trunc_div(z, 24))
}

/// `SharedControllers.change` -> `combat_states.change_fighter_state`, including the synchronous
/// exit handler, the `board[4] = 0` reset and the enter handler.
pub fn change(battle: &mut KaBattle, index: usize, state: i32) -> Result<(), i32> {
    change_depth(battle, index, state, 0)
}

fn change_depth(battle: &mut KaBattle, index: usize, state: i32, depth: u32) -> Result<(), i32> {
    if depth > 8 {
        return Err(KA_ERR_CAPACITY);
    }
    let old = battle.units[index].state();
    let hp = battle.units[index].hp_value();
    let actor = battle.units[index].identity;
    battle.push_event(KA_EVENT_STATE, actor, old, state, hp, 0, 0);

    // Exit handlers.
    match old {
        2 => {
            let team = battle.units[index].board_team();
            let cell = (battle.units[index].cell_x(), battle.units[index].cell_y());
            let grid = cell_to_grid(team, battle.row_offset, cell);
            let unit = &mut battle.units[index];
            unit.set_vel_x(0.0);
            unit.set_vel_z(0.0);
            unit.set_animation_rate(1);
            unit.path_count = 0;
            unit.board.set(7, grid as i64);
        }
        4 => animate(battle, index, 3),
        5 => {
            exit_using_skill(battle, index);
            animate(battle, index, 3);
        }
        6 => {
            let team = battle.units[index].board_team();
            let grid = battle.units[index].grid();
            let row = trunc_div(grid, 5);
            let column = i32_of(grid as i64 - row as i64 * 5);
            let y = i32_of(battle.row_offset as i64
                + if team == 0 { 1 } else { 0 }
                + if team == 0 { row as i64 } else { -(row as i64) });
            let unit = &mut battle.units[index];
            unit.set_pos_x(i32_of(column as i64 * 24) as f32);
            unit.set_pos_z(i32_of(y as i64 * 24) as f32);
            unit.set_offset_z(0.0);
        }
        _ => {}
    }

    {
        let unit = &mut battle.units[index];
        unit.board.set(5, state as i64);
        unit.board.set(4, 0);
    }

    // Enter handlers.
    match state {
        1 | 3 => animate(battle, index, 3),
        2 => {
            let unit = &battle.units[index];
            let start = (unit.pos_x(), unit.pos_z());
            let target = (
                unit.board.get_or(13, 0) as i32 as f32,
                unit.board.get_or(14, 0) as i32 as f32,
            );
            let points = fighter_path(start, target);
            let unit = &mut battle.units[index];
            unit.path[0] = points[0];
            unit.path[1] = points[1];
            unit.path_count = 2;
            unit.set_animation_rate(2);
            animate(battle, index, 2);
        }
        4 => {
            let motion = battle.units[index].weapon_motion;
            animate(battle, index, motion);
        }
        5 => {
            let skill_id = battle.units[index].board.get_or(17, -1) as i32;
            if skill_id != -1 {
                let row = battle.row(skill_id).copied().ok_or(KA_ERR_UNKNOWN_SKILL_ROW)?;
                animate(battle, index, row.motion);
            } else {
                let next = decide_state(battle, index)?.0;
                change_depth(battle, index, next, depth + 1)?;
            }
        }
        6 => {
            let height = battle.units[index].pos_z();
            let unit = &mut battle.units[index];
            unit.board.set(12, native_float_to_int(height) as i64);
            animate(battle, index, 30);
        }
        7 => crate::combat::knock(battle, index)?,
        8 => crate::combat::leave(battle, index)?,
        _ => {}
    }
    Ok(())
}

/// `combat_states.exit_using_skill`.
fn exit_using_skill(battle: &mut KaBattle, index: usize) {
    let unit = &mut battle.units[index];
    unit.board.set(8, 0);
    unit.board.set(17, -1);
    unit.long_board.set(16, -1);
}

/// `SharedControllers.enqueue` -> `combat_commands.enqueue_skill_command`.
pub fn enqueue_command(
    battle: &mut KaBattle,
    index: usize,
    row: &KaSkill,
    target: i32,
) -> Result<(), i32> {
    if battle.units[index].command_count as usize >= KA_MAX_COMMANDS {
        return Err(KA_ERR_CAPACITY);
    }
    let duration = if row.count == 1 {
        19
    } else {
        i32_of(19 + i32_of(5 * row.count as i64) as i64)
    };
    let target_exists = target >= 0 && battle.target_exists(target);
    let command = KaCommand {
        opcode: 29,
        target: if target_exists { target } else { -1 },
        skill: row.id,
        tick: 0,
        duration,
        use_index: 0,
    };
    let trace_id = battle.next_command;
    battle.next_command = i32_of(battle.next_command as i64 + 1);
    let unit = &mut battle.units[index];
    let slot = unit.command_count as usize;
    unit.commands[slot] = command;
    unit.command_count += 1;
    let actor = battle.units[index].identity;
    battle.push_event(KA_EVENT_ENQUEUE, actor, row.id, target, trace_id, 0, 0);
    // `SharedControllers.enqueue`: the stored-attack observer is notified with the raw target the
    // caller asked for, at the same place the canonical `enqueue` event is emitted.
    crate::battle_control::progress_enqueue(battle, target);
    Ok(())
}

/// `combat_states.tick_charging` - the state that calls `choose`.
fn tick_charging(battle: &mut KaBattle, index: usize, sleeping: bool) -> Result<(), i32> {
    let most_front = is_most_front(battle.units[index].grid());
    let long_range = battle.units[index].weapon_shooting_range > 1;
    if !sleeping {
        let interval = attack_interval(battle.units[index].agility());
        let gauge = i32_of(battle.units[index].gauge() as i64 + 1);
        battle.units[index].board.set(8, gauge as i64);
        if gauge <= interval {
            return Ok(());
        }
        if let Some((skill_id, target)) = choose(battle, index)? {
            let row = battle.row(skill_id).copied().ok_or(KA_ERR_UNKNOWN_SKILL_ROW)?;
            enqueue_command(battle, index, &row, target)?;
            battle.units[index].board.set(17, skill_id as i64);
            change(battle, index, 5)?;
            return Ok(());
        }
        let mut target: Option<i32> = None;
        if long_range {
            target = ranged_target(battle, index);
        }
        if target.is_none() && most_front {
            target = normal_target(battle, index);
        }
        battle.units[index].long_board.set(16, target.unwrap_or(-1) as i64);
        if let Some(target) = target {
            let _ = target;
            change(battle, index, 4)?;
            return Ok(());
        }
    }
    let decision = decide(battle, index)?;
    if decision != 3 {
        change(battle, index, decision)?;
    }
    Ok(())
}

/// `SharedControllers.ranged_target`.
fn ranged_target(battle: &KaBattle, index: usize) -> Option<i32> {
    let mut roster = [0i32; KA_MAX_UNITS];
    let roster_len = battle.roster(1 - battle.units[index].board_team(), &mut roster);
    for slot in 0..roster_len {
        let unit = roster[slot] as usize;
        roster[slot] = battle.units[unit].identity;
    }
    let shooting_range = battle.units[index].weapon_shooting_range;
    nearest_skill_target(battle, index, &roster, roster_len, shooting_range, |t| {
        match battle.fighter_slot(t) {
            Some(slot) => is_normal_attack_target(battle.units[slot].exists(),
                                                  battle.units[slot].hp_value(),
                                                  battle.units[slot].state()),
            None => false,
        }
    })
}

/// `SharedControllers.normal_target` -> `combat_navigation.front_opponent`.
fn normal_target(battle: &KaBattle, index: usize) -> Option<i32> {
    let mut roster = [0i32; KA_MAX_UNITS];
    let roster_len = battle.roster(1 - battle.units[index].board_team(), &mut roster);
    for slot in 0..roster_len {
        let unit = roster[slot] as usize;
        roster[slot] = battle.units[unit].identity;
    }
    let mut chosen: Option<i32> = None;
    let mut best = i32::MAX;
    for slot in 0..roster_len {
        let target = roster[slot];
        let unit_index = match battle.fighter_slot(target) {
            Some(value) => value,
            None => continue,
        };
        let unit = &battle.units[unit_index];
        if !is_normal_attack_target(unit.exists(), unit.hp_value(), unit.state()) {
            continue;
        }
        if !is_most_front(unit.grid()) {
            continue;
        }
        let distance = manhattan(battle, index, unit_index);
        if distance < best {
            chosen = Some(target);
            best = distance;
        }
    }
    chosen
}

/// `combat_states.tick_using_skill`.
fn tick_using_skill(battle: &mut KaBattle, index: usize) -> Result<(), i32> {
    let has_command = battle.units[index].commands[..battle.units[index].command_count as usize]
        .iter()
        .any(|command| command.opcode == 29);
    if has_command {
        return Ok(());
    }
    battle.units[index].board.set(8, 0);
    let decision = decide(battle, index)?;
    change(battle, index, decision)
}

/// `combat_states.update_moving` (path integration is pure position math, no world access).
fn update_moving(battle: &mut KaBattle, index: usize) -> Result<(), i32> {
    if battle.units[index].path_count > 0 {
        let point = battle.units[index].path[0];
        let unit = &battle.units[index];
        let (arrived, position, velocity) = battle_move_base(
            (unit.pos_x(), unit.pos_z()),
            (point.x as f32, point.y as f32),
            4.46f32,
            (unit.vel_x(), unit.vel_z()),
        );
        {
            let unit = &mut battle.units[index];
            unit.set_pos_x(position.0);
            unit.set_pos_z(position.1);
            if arrived {
                unit.set_vel_x(0.0);
                unit.set_vel_z(0.0);
                // `move_fighter`: an arrival restores the ordinary animation rate before the
                // grid update and the path pop.
                unit.set_animation_rate(1);
                let team = unit.board_team();
                let cell = (unit.cell_x(), unit.cell_y());
                let row_offset = battle.row_offset;
                let grid = cell_to_grid(team, row_offset, cell);
                unit.board.set(7, grid as i64);
                let count = unit.path_count as usize;
                for slot in 1..count {
                    unit.path[slot - 1] = unit.path[slot];
                }
                unit.path_count -= 1;
            } else {
                unit.set_vel_x(velocity.0);
                unit.set_vel_z(velocity.1);
            }
        }
    }
    if battle.units[index].path_count == 0 {
        let team = battle.units[index].board_team();
        battle.units[index].set_direction(if team == 0 { 0 } else { 2 });
        let decision = decide(battle, index)?;
        change(battle, index, decision)?;
    }
    Ok(())
}

/// `SharedControllers.update`: the recovered state dispatch.
pub fn update(battle: &mut KaBattle, index: usize, state: i32) -> Result<(), i32> {
    match state {
        1 => {
            let decision = decide(battle, index)?;
            if decision != 1 {
                change(battle, index, decision)?;
            }
            Ok(())
        }
        2 => update_moving(battle, index),
        3 => {
            // `sleeping`: board[62] holds a skill row whose recovered type is 67.
            let sleeping = match battle.units[index].board.get(62) {
                Some(id) => {
                    let row = battle.row(id as i32).copied().ok_or(KA_ERR_UNKNOWN_SKILL_ROW)?;
                    row.kind == 67
                }
                None => false,
            };
            tick_charging(battle, index, sleeping)
        }
        5 => tick_using_skill(battle, index),
        4 => {
            let (gauge, next) = crate::combat::update_attacking(battle, index)?;
            battle.units[index].board.set(8, gauge as i64);
            if let Some(next) = next {
                change(battle, index, next)?;
            }
            Ok(())
        }
        6 => crate::combat::update_damaging(battle, index),
        7 => crate::combat::update_knocking_down(battle, index),
        8 => {
            crate::combat::update_leaving(battle, index);
            Ok(())
        }
        // `SharedControllers.update` is an if/elif chain with no final `else`, so any state outside
        // 1..8 - including the state 0 the recovered pre-placement boards carry - updates nothing.
        // That is the canonical behaviour, not an unported mechanic.
        _ => Ok(()),
    }
}

/// `combat_commands.update_skill_command`. Returns `true` when the head command completed.
fn update_skill_command(
    battle: &mut KaBattle,
    index: usize,
    command: &mut KaCommand,
    row: &KaSkill,
) -> Result<bool, i32> {
    let count = row.count;
    if command.tick == 0 {
        animate(battle, index, row.motion);
        if count >= 2 {
            battle.units[index].set_animation_rate(4);
        }
    }
    if count == 1 {
        if command.tick == 11 {
            let current = *command;
            crate::combat::use_skill(battle, index, &current, command.use_index)?;
            command.use_index = i32_of(command.use_index as i64 + 1);
        }
    } else {
        if command.use_index < count {
            let remainder = i32_of(command.tick as i64 - trunc_div(command.tick, 5) as i64 * 5);
            if remainder == 1 {
                let current = *command;
                crate::combat::use_skill(battle, index, &current, command.use_index)?;
                command.use_index = i32_of(command.use_index as i64 + 1);
            } else if remainder == 0 {
                animate(battle, index, row.motion);
            }
        }
        if command.tick == i32_of(command.duration as i64 - 19) {
            battle.units[index].set_animation_rate(1);
            animate(battle, index, 3);
        }
    }
    command.tick = i32_of(command.tick as i64 + 1);
    if command.use_index >= count && command.tick > command.duration {
        battle.units[index].set_animation_frame(0);
        battle.units[index].set_animation_rate(1);
        return Ok(true);
    }
    Ok(false)
}

/// `combat_commands.execute_skill_queue`, restricted to the recovered command 29.
fn execute_commands(battle: &mut KaBattle, index: usize) -> Result<(), i32> {
    loop {
        if battle.units[index].command_count == 0 {
            return Ok(());
        }
        let mut command = battle.units[index].commands[0];
        if command.opcode != 29 {
            return Err(KA_ERR_UNSUPPORTED_OPCODE);
        }
        let row = battle.row(command.skill).copied().ok_or(KA_ERR_UNKNOWN_SKILL_ROW)?;
        let completed = update_skill_command(battle, index, &mut command, &row)?;
        battle.units[index].commands[0] = command;
        if !completed {
            return Ok(());
        }
        let count = battle.units[index].command_count as usize;
        for slot in 1..count {
            battle.units[index].commands[slot - 1] = battle.units[index].commands[slot];
        }
        battle.units[index].command_count -= 1;
        // `execute_skill_queue.remove_first_command`: a completed head is a *release*, and the
        // observer sees the command that was removed - never a queue clear.
        crate::battle_control::progress_release(battle, &command);
    }
}

/// `combat_tick.update_fighters`: team-then-roster order, `flags & 2` skip, `board[4]` increment,
/// prior-state gating of `after_fighter`, then the state dispatch and the command drain.
///
/// `after_fighter` is the caller's optional post-update hook; the recovered default is a no-op and
/// the only in-tree caller passes that default, so no hook is modelled.
pub fn update_fighters(battle: &mut KaBattle, battle_state: i32) -> Result<(), i32> {
    if (battle_state as u32) < 2 {
        return Ok(());
    }
    for team in 0..2i32 {
        let mut roster = [0i32; KA_MAX_UNITS];
        let roster_len = battle.roster(team, &mut roster);
        for slot in 0..roster_len {
            let index = roster[slot] as usize;
            if battle.units[index].present == 0 || battle.units[index].destroyed() {
                continue;
            }
            let frame = battle.units[index].frame();
            battle.units[index].board.set(4, i32_of(frame as i64 + 1) as i64);
            let prior = battle.units[index].state();
            #[cfg(feature = "profile")]
            let started = std::time::Instant::now();
            update(battle, index, prior)?;
            #[cfg(feature = "profile")]
            crate::profile::record(10, started.elapsed());
            #[cfg(feature = "profile")]
            let started = std::time::Instant::now();
            execute_commands(battle, index)?;
            #[cfg(feature = "profile")]
            crate::profile::record(11, started.elapsed());
            // `if prior_state not in (7, 8): after_fighter(...)` - the recovered hook is a no-op,
            // but the gate is part of the order and is preserved here for later slices.
        }
    }
    Ok(())
}
