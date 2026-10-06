//! The first ported global tick phases: movement, facing, cells and heights.
//!
//! Canonical order inside `SharedControllers.run` is:
//!
//! ```text
//! fighters -> skills (status) -> projectiles -> move -> rotate -> cells -> height
//!          -> modifiers -> animation -> effects -> garbage
//! ```
//!
//! Only the middle four are ported here, in that order. Each mirrors its source exactly:
//!
//! | native                | Python                                             |
//! |-----------------------|----------------------------------------------------|
//! | `update_positions`    | `combat_spatial.update_positions`                  |
//! | `update_facing`       | `combat_spatial.update_facing` (render_mode 0)     |
//! | `update_cells`        | `combat_spatial.update_cells` + `CellOccupancy.changed` |
//! | `update_heights`      | `combat_spatial.update_heights` (null-tile map)    |
//!
//! Boundaries the controller itself fixes, transcribed rather than generalised: `can_move_in_air`
//! is `lambda i: False`, `has_product`/`has_treasure` are `lambda i: False`, `map_chip` is
//! `lambda x,y: None`, and `human_images` returns the entity's own image arrays, so the facing
//! swap is a no-op in this composition.

use crate::ai::{native_float_to_int, trunc_div, KA_ERR_CAPACITY, KA_ERR_UNKNOWN_SKILL_ROW,
                KA_ERR_UNSUPPORTED_STATE};
use crate::rng::i32_of;
use crate::state::*;
use crate::world::*;

/// `combat_projectiles.magnitude`.
#[inline]
fn magnitude3(vector: KaVec3) -> f32 {
    ((vector.x * vector.x) + (vector.y * vector.y) + (vector.z * vector.z)).sqrt()
}

/// `combat_projectiles.isometric_trail_screen` (ToScreenPos mode0, camera=false).
#[inline]
fn isometric_trail_screen(position: KaVec3) -> (i32, i32) {
    (
        native_float_to_int(position.x - position.z),
        native_float_to_int((position.x + position.z) * 0.5 - position.y),
    )
}

/// `i32(x)` then `trunc_div(..., 24)`: `combat_spatial.battle_cell` at the 24x24 battle cell size.
#[inline]
fn battle_cell(value: f32) -> i32 {
    trunc_div(native_float_to_int(value), 24)
}

/// `combat_spatial.update_positions`: `position += speed`, then the parent-offset override.
pub fn update_positions(battle: &mut KaBattle) {
    let count = battle.collect_members(SUBSET_MOVING);
    for slot in 0..count {
        let id = battle.scratch_members[slot];
        let (px, py, pz, sx, sy, sz, parent, ox, oy, oz) = match battle.entity(id) {
            Some(entity) => (
                entity.position.x,
                entity.position.y,
                entity.position.z,
                entity.speed.x,
                entity.speed.y,
                entity.speed.z,
                entity.parent,
                entity.offset.x,
                entity.offset.y,
                entity.offset.z,
            ),
            None => continue,
        };
        let mut x = px + sx;
        let mut y = py + sy;
        let mut z = pz + sz;
        if parent >= 0 {
            if let Some(p) = battle.entity(parent) {
                x = p.position.x + ox;
                y = p.position.y + oy;
                z = p.position.z + oz;
            }
        }
        if let Some(entity) = battle.entity_mut(id) {
            entity.position.x = x;
            entity.position.y = y;
            entity.position.z = z;
        }
    }
}

/// `combat_spatial.update_facing` with the controller's `render_mode=0` and no air movement.
pub fn update_facing(battle: &mut KaBattle) {
    let count = battle.collect_members(SUBSET_ROTATING);
    for slot in 0..count {
        let id = battle.scratch_members[slot];
        let (x, z, direction) = match battle.entity(id) {
            Some(entity) => (entity.speed.x, entity.speed.z, entity.direction),
            None => continue,
        };
        let mut facing = direction;
        if x != 0.0 || z != 0.0 {
            // The `can_move_in_air` branch is `lambda i: False` in this composition.
            if z < 0.0 && z.abs() > x.abs() {
                facing = 0;
            } else if x > 0.0 && x.abs() > z.abs() {
                facing = 1;
            } else if z > 0.0 && z.abs() > x.abs() {
                facing = 2;
            } else if x < 0.0 && x.abs() > z.abs() {
                facing = 3;
            }
        }
        if let Some(entity) = battle.entity_mut(id) {
            entity.direction = facing;
            // `c[2][1] = i32(trunc_div(c[2][1], 4) * 4 + direction[0])` - the Seb id's low bits.
            entity.seb[1] = i32_of(trunc_div(entity.seb[1], 4) as i64 * 4 + facing as i64);
        }
    }
}

/// `combat_spatial.update_cells` plus `CellOccupancy.changed`, including the `cell_change` event.
pub fn update_cells(battle: &mut KaBattle) {
    let count = battle.collect_members(SUBSET_CELLS);
    let mut pending_len = 0usize;
    for slot in 0..count {
        let id = battle.scratch_members[slot];
        let (x, z, cx, cz) = match battle.entity(id) {
            Some(entity) => (entity.position.x, entity.position.z, entity.cell[0], entity.cell[1]),
            None => continue,
        };
        let new_x = battle_cell(x);
        let new_z = battle_cell(z);
        if cx != new_x || cz != new_z {
            let old_key = battle.cell_key([cx, cz]);
            if let Some(entity) = battle.entity_mut(id) {
                entity.cell[0] = new_x;
                entity.cell[1] = new_z;
            }
            if pending_len < battle.scratch_pending_pair.len() {
                battle.scratch_pending_pair[pending_len] = (id, old_key);
                pending_len += 1;
            }
        }
    }
    for slot in 0..pending_len {
        let (id, old_key) = battle.scratch_pending_pair[slot];
        battle.occupancy_changed(id, old_key);
        let cell = battle.entity(id).map(|entity| entity.cell).unwrap_or([0, 0]);
        battle.push_event(KA_EVENT_CELL_CHANGE, id, old_key, cell[0], cell[1], 0, 0);
    }
}

/// `combat_spatial.update_heights`. The controller passes `map_chip = lambda x,y: None`, so every
/// member that is not excluded by the human-flag rule takes the null-tile branch and gets `y = 0`.
pub fn update_heights(battle: &mut KaBattle) {
    let count = battle.collect_members(SUBSET_HEIGHT_MEMBERS);
    for slot in 0..count {
        let id = battle.scratch_members[slot];
        let skip = match battle.entity(id) {
            Some(entity) => {
                let human = battle
                    .fighter_slot(id)
                    .map(|index| battle.units[index].human != 0)
                    .unwrap_or(false);
                let flag32 = battle
                    .fighter_slot(id)
                    .map(|index| battle.units[index].human_flag & 32 != 0)
                    .unwrap_or(false);
                entity.has[SLOT_CELL] == 0 || (human && flag32)
            }
            None => continue,
        };
        if skip {
            continue;
        }
        // `null_or_destroyed(tile)` is always True for a null map chip.
        if let Some(entity) = battle.entity_mut(id) {
            entity.position.y = 0.0;
        }
    }
}

/// The ported global phases, in canonical order.
pub fn global_phases(battle: &mut KaBattle) {
    update_positions(battle);
    update_facing(battle);
    update_cells(battle);
    update_heights(battle);
}

/// The ported post-fighter prefix, in canonical order: status, projectiles, then the movement block.
pub fn ported_prefix(battle: &mut KaBattle) -> Result<(), i32> {
    update_status_phase(battle);
    tick_projectiles(battle)?;
    global_phases(battle);
    Ok(())
}

// ---------------------------------------------------------------------------------------------
// Status (`combat_skills.update_status`, run as the `skills` phase right after the fighters)
// ---------------------------------------------------------------------------------------------

/// `combat_skills.update_status`: one per-skill-member tick. BB64 accumulates, every 20th tick
/// decrements BB63 and clears the whole 62/63/64 slot at zero. No RNG.
pub fn update_status_phase(battle: &mut KaBattle) {
    let count = battle.collect_members(SUBSET_SKILL_MEMBERS);
    for slot in 0..count {
        let id = battle.scratch_members[slot];
        // `if components[28] is None: continue`
        if !battle.has(id, SLOT_AI) {
            continue;
        }
        let index = match battle.fighter_slot(id) {
            Some(index) => index,
            None => continue,
        };
        let before = (
            battle.units[index].board.get(62),
            battle.units[index].board.get(63),
            battle.units[index].board.get(64),
        );
        if before.0.is_some() && before.1.is_some() && before.2.is_some() {
            let counter = i32_of(before.2.unwrap() + 1);
            battle.units[index].board.set(64, counter as i64);
            if counter.rem_euclid(20) == 0 {
                let remaining = i32_of(before.1.unwrap() - 1);
                battle.units[index].board.set(63, remaining as i64);
                if remaining <= 0 {
                    for key in [62, 63, 64] {
                        battle.units[index].board.remove(key);
                    }
                }
            }
        }
        let after = (
            battle.units[index].board.get(62),
            battle.units[index].board.get(63),
            battle.units[index].board.get(64),
        );
        if before != after {
            battle.push_event(KA_EVENT_STATUS_TICK, id, before.1.unwrap_or(-1) as i32,
                              before.2.unwrap_or(-1) as i32, after.1.unwrap_or(-1) as i32,
                              after.2.unwrap_or(-1) as i32,
                              i32::from(after.0.is_some()));
        }
    }
}

// ---------------------------------------------------------------------------------------------
// Projectiles (`combat_projectiles.tick_projectiles` + `SharedControllers.impact_projectile`)
// ---------------------------------------------------------------------------------------------

/// `SharedControllers.trail` -> `create_projectile_trail`: a type-17 effect at the projectile's
/// position, plus the controller's `effect_birth` event (type 17, lifetime 10).
fn create_trail(battle: &mut KaBattle, identity: i32, previous: KaVec3) -> Result<i32, i32> {
    let (screen_x, screen_y) = isometric_trail_screen(previous);
    let position = match battle.entity(identity) {
        Some(entity) => entity.position,
        None => return Err(KA_ERR_CAPACITY),
    };
    let spec = crate::combat::EffectSpec {
        type_: 17,
        value1: screen_x,
        value2: screen_y,
        res: 0,
        seb: 0,
        x: position.x,
        y: position.y,
        z: position.z,
        scale: 100,
        image: -1,
        depth: 0,
        max_frame: 10,
        frame: 0,
        looping: 0,
        parent: -1,
        animate: 0,
    };
    crate::combat::create_effect(battle, &spec)
}

/// `SharedControllers.impact_projectile`.
fn impact_projectile(battle: &mut KaBattle, identity: i32) -> Result<(), i32> {
    let (has_attack, position, cell, texture, seb) = match battle.entity(identity) {
        Some(entity) => (
            entity.has[SLOT_ATTACK] != 0,
            entity.position,
            entity.cell,
            entity.image[0],
            entity.seb[1],
        ),
        None => return Ok(()),
    };
    if !has_attack {
        // `body_flight` projectiles carry no Attack component: only `body_impact` is emitted.
        battle.push_event(KA_EVENT_BODY_IMPACT, identity, 0, 0, 0, 0, 0);
        return Ok(());
    }
    let source = battle.projectile_source(identity);
    let computed_cell = (battle_cell(position.x), battle_cell(position.z));
    let (caster_identity, skill_id) = source.unwrap_or((-1, -1));
    battle.push_event(KA_EVENT_PROJECTILE_IMPACT, identity, caster_identity, computed_cell.0,
                      computed_cell.1, cell[0], cell[1]);
    if let Some(caster) = battle.fighter_slot(caster_identity) {
        if battle.target_exists(caster_identity) && skill_id >= 0 {
            let row = battle.row(skill_id).copied().ok_or(KA_ERR_UNKNOWN_SKILL_ROW)?;
            crate::combat::hit_cell(battle, caster, &row, computed_cell)?;
        }
    }
    if skill_id >= 0 {
        let row = battle.row(skill_id).copied().ok_or(KA_ERR_UNKNOWN_SKILL_ROW)?;
        if row.impact_img != -1 {
            let spec = crate::combat::EffectSpec {
                type_: 0,
                value1: 0,
                value2: 0,
                res: 28,
                seb: row.impact_seb,
                x: position.x,
                y: position.y + 15.0,
                z: position.z,
                scale: 100,
                image: row.impact_img,
                depth: 1,
                max_frame: 0,
                frame: 0,
                looping: 0,
                parent: -1,
                animate: 1,
            };
            crate::combat::create_effect(battle, &spec)?;
        }
    }
    if texture == 27 && seb == 27 {
        battle.add_component(identity, SLOT_MODIFY_ANIMATION);
        if let Some(entity) = battle.entity_mut(identity) {
            entity.modifier = KaModifier {
                type_: 5,
                offset_x: 0.0,
                offset_y: 0.0,
                offset_z: 0.0,
                scale_x: 1.0,
                scale_y: 1.0,
                angle: 180,
                anchor: 0,
                frame: 0,
                duration: 20,
                destroy_on_finish: 1,
                looping: 0,
                alpha: 255,
            };
        }
        battle.push_event(KA_EVENT_PROJECTILE_CLEANUP, identity, 0, 20, 0, 0, 0);
    } else {
        if let Some(entity) = battle.entity_mut(identity) {
            entity.garbage = 1;
        }
        battle.add_component(identity, SLOT_GARBAGE);
        battle.push_event(KA_EVENT_PROJECTILE_CLEANUP, identity, 1, 1, 0, 0, 0);
    }
    Ok(())
}

/// `combat_projectiles.tick_projectiles`, with the controller's `rotate = lambda *a: None`.
pub fn tick_projectiles(battle: &mut KaBattle) -> Result<(), i32> {
    let mut pending_len = 0usize;
    // The subset is snapshotted first: the trail callback allocates entities (which notify every
    // subset) while Python iterates a captured generator.
    let member_len = battle.collect_members(SUBSET_PROJECTILES);
    for slot in 0..member_len {
        let identity = battle.scratch_members[slot];
        let (start, end, speed, height, old, length, position) = match battle.entity(identity) {
            Some(entity) => (
                entity.projectile.start,
                entity.projectile.end,
                entity.projectile.speed,
                entity.projectile.height,
                entity.projectile.frame,
                entity.projectile.length,
                entity.position,
            ),
            None => continue,
        };
        let delta = KaVec3 { x: end.x - start.x, y: end.y - start.y, z: end.z - start.z };
        let distance = magnitude3(delta);
        let normal = if distance != 0.0 {
            KaVec3 { x: delta.x / distance, y: delta.y / distance, z: delta.z / distance }
        } else {
            KaVec3 { x: 0.0, y: 0.0, z: 0.0 }
        };
        let y = start.y + crate::combat::parabola(height, length, old) as f32;
        let next_y = start.y + crate::combat::parabola(height, length, i32_of(old as i64 + 1)) as f32;
        let dy = next_y - y;
        {
            if let Some(entity) = battle.entity_mut(identity) {
                entity.position.y = y;
                entity.speed.x = normal.x * speed as f32;
                entity.speed.z = normal.z * speed as f32;
            }
        }
        // `rotate(identity, dy)` is the controller's no-op.
        let mut impact = length < 1;
        if !impact {
            let frame = old;
            if let Some(entity) = battle.entity_mut(identity) {
                entity.projectile.frame = i32_of(frame as i64 + 1);
            }
            let current_y = battle.entity(identity).map(|e| e.position.y).unwrap_or(y);
            impact = dy <= 0.0 && frame >= 1 && current_y <= end.y;
        }
        if impact {
            if let Some(entity) = battle.entity_mut(identity) {
                entity.position = end;
                entity.speed = KaVec3 { x: 0.0, y: 0.0, z: 0.0 };
            }
            battle.remove_component(identity, SLOT_MODIFY_ANIMATION);
            if pending_len < battle.scratch_pending_int.len() {
                battle.scratch_pending_int[pending_len] = identity;
                pending_len += 1;
            }
        } else if old >= 1 && battle.has(identity, SLOT_ATTACK) {
            let previous = KaVec3 {
                x: position.x
                    - battle.entity(identity).map(|e| e.speed.x).unwrap_or(0.0),
                y: start.y
                    + crate::combat::parabola(height, length, i32_of(old as i64 - 1)) as f32,
                z: position.z
                    - battle.entity(identity).map(|e| e.speed.z).unwrap_or(0.0),
            };
            create_trail(battle, identity, previous)?;
        }
    }
    for slot in 0..pending_len {
        let identity = battle.scratch_pending_int[slot];
        impact_projectile(battle, identity)?;
        battle.remove_component(identity, SLOT_PROJECTILE);
    }
    Ok(())
}

// ---------------------------------------------------------------------------------------------
// Modifiers (`combat_effects.tick_modifiers`)
// ---------------------------------------------------------------------------------------------

/// `combat_effects.update_hit_modifier` (type 4). Returns whether it completes.
fn update_hit_modifier(modifier: &mut KaModifier) -> bool {
    if !(modifier.looping != 0) && modifier.duration == 0 {
        return true;
    }
    let frame = modifier.frame;
    if frame < 0 {
        modifier.frame = i32_of(frame as i64 + 1);
        return false;
    }
    let offset = trunc_div(i32_of(modifier.duration as i64 - frame as i64), 2) as f32;
    modifier.offset_x = if frame & 1 != 0 { offset } else { -offset };
    modifier.frame = i32_of(frame as i64 + 1);
    if frame >= modifier.duration {
        if modifier.looping != 0 {
            modifier.frame = 0;
        } else {
            return true;
        }
    }
    false
}

/// `combat_effects.update_projectile_modifier` (type 7).
fn update_projectile_modifier(modifier: &mut KaModifier) -> bool {
    if !(modifier.looping != 0) && modifier.duration == 0 {
        return true;
    }
    let old = modifier.frame;
    modifier.frame = i32_of(old as i64 + 1);
    if old >= 0 && old >= modifier.duration {
        if modifier.looping != 0 {
            modifier.frame = 0;
        } else {
            return true;
        }
    }
    false
}

/// `combat_effects.graph_easing_int`.
fn graph_easing_int(start: i32, end: i32, duration: i32, frame: i32, curve: i32) -> i32 {
    if frame < 0 {
        return start;
    }
    if frame >= duration {
        return end;
    }
    let t = frame as f32 / duration as f32;
    let pi = 3.141_592_7_f32;
    let angle = (t * pi) * 180.0 / pi;
    // `sin_degrees` is `lambda a: math.sin(math.radians(a))` - a host-libm call.
    let eased = t + ((curve as f32 / 314.159_27_f32) * (angle as f64).to_radians().sin() as f32);
    let value = crate::ai::native_float_to_int(
        eased * i32_of(end as i64 - start as i64) as f32 + start as f32,
    );
    start.max(end).min(start.min(end).max(value))
}

/// `combat_effects.update_projectile_fade` (type 5).
fn update_projectile_fade(modifier: &mut KaModifier) -> bool {
    if !(modifier.looping != 0) && modifier.duration == 0 {
        return true;
    }
    let old = modifier.frame;
    if old >= 0 {
        modifier.alpha = graph_easing_int(255, 0, modifier.duration, old, -100);
    }
    modifier.frame = i32_of(old as i64 + 1);
    if old >= 0 && old >= modifier.duration {
        if modifier.looping != 0 {
            modifier.frame = 0;
        } else {
            return true;
        }
    }
    false
}

/// `combat_effects.tick_modifiers`: complete every update before any cleanup, and re-read the
/// component before acting on it.
pub fn tick_modifiers(battle: &mut KaBattle) -> Result<(), i32> {
    let count = battle.collect_members(SUBSET_MODIFIERS);
    let mut pending_len = 0usize;
    for slot in 0..count {
        let id = battle.scratch_members[slot];
        let complete = match battle.entity_mut(id) {
            Some(entity) => match entity.modifier.type_ {
                4 => update_hit_modifier(&mut entity.modifier),
                5 => update_projectile_fade(&mut entity.modifier),
                7 => update_projectile_modifier(&mut entity.modifier),
                _ => return Err(KA_ERR_UNSUPPORTED_STATE),
            },
            None => continue,
        };
        if complete && pending_len < battle.scratch_pending_int.len() {
            battle.scratch_pending_int[pending_len] = id;
            pending_len += 1;
        }
    }
    for slot in 0..pending_len {
        let id = battle.scratch_pending_int[slot];
        let (destroy, type_, type8) = match battle.entity(id) {
            Some(entity) => (entity.modifier.destroy_on_finish != 0, entity.modifier.type_, entity.modifier.type_ == 8),
            None => continue,
        };
        let _ = type_;
        if destroy {
            battle.destroy(id);
        } else {
            if type8 {
                // Component 30 is the static-render marker; never produced in this scope, kept so
                // the branch order matches the canonical source.
                battle.add_component(id, 30);
            }
            battle.remove_component(id, SLOT_MODIFY_ANIMATION);
        }
    }
    Ok(())
}

// ---------------------------------------------------------------------------------------------
// Animation (`combat_animation.update_animations`)
// ---------------------------------------------------------------------------------------------

/// `combat_animation.signed_remainder`.
fn signed_remainder(value: i32, divisor: i32) -> i32 {
    let quotient = if divisor == 0 {
        0
    } else {
        (value.unsigned_abs() / divisor.unsigned_abs()) as i32
            * if (value < 0) != (divisor < 0) { -1 } else { 1 }
    };
    i32_of(value as i64 - i32_of(quotient as i64) as i64 * divisor as i64)
}

/// In a bulk run, canonical global resource frames are independent modulo counters. This is
/// enabled only when every row begins in [0, max_frame), where repeated signed remainders equal
/// one final modulo. Single-step execution always retains the literal update loop.
pub fn can_defer_animation_resource_frames(battle: &KaBattle) -> bool {
    battle.animation_resource_count > 0 && battle.animation_resources
        [..battle.animation_resource_count as usize]
        .iter().all(|row| row.max_frame > 0 && row.frame >= 0 && row.frame < row.max_frame)
}

pub fn finish_deferred_animation_resource_frames(battle: &mut KaBattle) {
    let ticks = battle.deferred_animation_ticks as u64;
    if ticks > 0 {
        for row in &mut battle.animation_resources[..battle.animation_resource_count as usize] {
            row.frame = ((row.frame as u64 + ticks) % row.max_frame as u64) as i32;
        }
    }
    battle.deferred_animation_ticks = 0;
    battle.defer_animation_resource_frames = 0;
}

/// `combat_animation.update_animations` with the controller's `state` (enabled, not auto-animating,
/// common frames on) and `change_animation = lambda i, b: self.animate(self.units[i], b)`.
pub fn update_animations(battle: &mut KaBattle) -> Result<(), i32> {
    // Global clip-frame loop: every resource row's own frame counter advances and wraps. The counter
    // is written here and read nowhere in the recovered engine (only `max_frame` is read), so it is
    // advanced for fidelity but carries no rule consequence.
    if battle.defer_animation_resource_frames != 0 {
        battle.deferred_animation_ticks += 1;
    } else {
        for slot in 0..battle.animation_resource_count as usize {
            let (frame, max_frame) = {
                let row = battle.animation_resources[slot];
                (row.frame, row.max_frame)
            };
            battle.animation_resources[slot].frame =
                signed_remainder(i32_of(frame as i64 + 1), max_frame);
        }
    }
    let count = battle.collect_members(SUBSET_FIGHTER_ANIMATIONS);
    for slot in 0..count {
        let id = battle.scratch_members[slot];
        let (res, seb_id, rate, backup) = match battle.entity(id) {
            Some(entity) => (entity.seb[0], entity.seb[1], entity.animation[0], entity.animation[1]),
            None => continue,
        };
        if res < 0 || seb_id < 0 || !battle.has_animation_manager(res) {
            continue;
        }
        let max_frame = match battle.animation_resource(res, seb_id) {
            Some((max_frame, _)) => max_frame,
            None => return Err(KA_ERR_UNSUPPORTED_STATE),
        };
        let index = match battle.fighter_slot(id) {
            Some(index) => index,
            None => continue,
        };
        let frame = i32_of(battle.units[index].body.seb[2] as i64 + rate as i64);
        battle.units[index].body.seb[2] = frame;
        if frame >= max_frame {
            battle.units[index].body.seb[2] = signed_remainder(frame, max_frame);
            if backup != -1 {
                crate::ai::animate(battle, index, backup);
                // Reacquire: the synchronous callback may replace the component.
                battle.units[index].body.animation[1] = -1;
            }
        }
    }
    Ok(())
}

// ---------------------------------------------------------------------------------------------
// Effects (`combat_effects.update_effect_phase` + `update_combat_effect`)
// ---------------------------------------------------------------------------------------------

/// `combat_effects.update_combat_effect`, restricted to the reachable effect types.
fn update_combat_effect(battle: &mut KaBattle, id: i32) -> Result<(), i32> {
    let (type_, depth, frame, max_frame) = match battle.entity(id) {
        Some(entity) => (
            entity.effect.type_,
            entity.effect.depth != 0,
            entity.effect.frame,
            entity.effect.max_frame,
        ),
        None => return Ok(()),
    };
    if type_ == 0 {
        if !depth {
            let old = frame;
            let mut next = i32_of(old as i64 + 1);
            if old >= 0 {
                let duration = max_frame;
                let quotient = if duration != 0 {
                    (next.unsigned_abs() / duration.unsigned_abs()) as i32
                        * if (next < 0) != (duration < 0) { -1 } else { 1 }
                } else {
                    0
                };
                next = i32_of(next as i64 - quotient as i64 * duration as i64);
            }
            if let Some(entity) = battle.entity_mut(id) {
                entity.effect.frame = next;
            }
        }
    } else if matches!(type_, 1 | 12 | 15 | 17 | 19) {
        if let Some(entity) = battle.entity_mut(id) {
            entity.effect.frame = i32_of(frame as i64 + 1);
            if type_ == 1 || type_ == 19 {
                entity.position.y += 1.0;
            }
        }
    } else {
        return Err(KA_ERR_UNSUPPORTED_STATE);
    }
    Ok(())
}

/// `combat_effects.update_effect_phase`.
pub fn update_effect_phase(battle: &mut KaBattle) -> Result<(), i32> {
    let count = battle.collect_members(SUBSET_EFFECTS);
    let mut pending_len = 0usize;
    for slot in 0..count {
        let id = battle.scratch_members[slot];
        let parent = match battle.entity(id) {
            Some(entity) => entity.effect.parent,
            None => continue,
        };
        if parent >= 0 && battle.is_destroyed(parent)
            && pending_len < battle.scratch_pending_int.len()
        {
            battle.scratch_pending_int[pending_len] = id;
            pending_len += 1;
        }
        // The native still updates an effect queued for destruction by its parent.
        update_combat_effect(battle, id)?;
    }
    for slot in 0..pending_len {
        battle.destroy(battle.scratch_pending_int[slot]);
    }
    Ok(())
}

// ---------------------------------------------------------------------------------------------
// Garbage (`combat_lifecycle.update_garbage`)
// ---------------------------------------------------------------------------------------------

/// `combat_lifecycle.update_garbage`: decrement every lifetime, then destroy all expired entities.
pub fn update_garbage(battle: &mut KaBattle) {
    let count = battle.collect_members(SUBSET_GARBAGE);
    let mut pending_len = 0usize;
    for slot in 0..count {
        let id = battle.scratch_members[slot];
        let lifetime = match battle.entity_mut(id) {
            Some(entity) => {
                entity.garbage = i32_of(entity.garbage as i64 - 1);
                entity.garbage
            }
            None => continue,
        };
        if lifetime <= 0 && pending_len < battle.scratch_pending_int.len() {
            battle.scratch_pending_int[pending_len] = id;
            pending_len += 1;
        }
    }
    for slot in 0..pending_len {
        battle.destroy(battle.scratch_pending_int[slot]);
    }
}

/// The trailing phases, in canonical order.
pub fn trailing_phases(battle: &mut KaBattle) -> Result<(), i32> {
    #[cfg(feature = "profile")]
    let started = std::time::Instant::now();
    tick_modifiers(battle)?;
    #[cfg(feature = "profile")]
    crate::profile::record(6, started.elapsed());
    #[cfg(feature = "profile")]
    let started = std::time::Instant::now();
    update_animations(battle)?;
    #[cfg(feature = "profile")]
    crate::profile::record(7, started.elapsed());
    #[cfg(feature = "profile")]
    let started = std::time::Instant::now();
    update_effect_phase(battle)?;
    #[cfg(feature = "profile")]
    crate::profile::record(8, started.elapsed());
    #[cfg(feature = "profile")]
    let started = std::time::Instant::now();
    update_garbage(battle);
    #[cfg(feature = "profile")]
    crate::profile::record(9, started.elapsed());
    Ok(())
}
