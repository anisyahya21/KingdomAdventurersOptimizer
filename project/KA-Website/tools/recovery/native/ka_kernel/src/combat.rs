//! The rule-bearing action path: `use_skill` and the attacking/damaging/knockdown/leaving states.
//!
//! Transcribed from `combat_skill_use.use_fighter_skill`, `combat_shared_controllers`
//! (`attack_result`, `reaction`, `reflect`, `after_attack`, `deliver`, `attack_effect`, `heal`,
//! `deliver_cures`, `hit_cell`, `fire_skill_projectiles`, `buff_cell`, `pay`, `sound`, `balloon`,
//! `body_flight`, `leave`, `knock`), `combat_resolution` (`resolve_attack`, `base_damage`,
//! `damage_from_parameters`, `critical_rate`, `hit_rate`, `modified_rate`, `random_range`,
//! `consume_invoking_attack`, `defender_attack_phase`, `attacker_invocation_phase`,
//! `apply_fighter_attack_results`, `apply_fighter_cure_results`, `cure_result`,
//! `reflected_attack_result`), `combat_shared_resolution` (`SharedAttackMath`,
//! `apply_shared_attack_results`, `dispatch_shared_attack`), `combat_events.dispatch_attack_event`,
//! `combat_states` (states 4/6/7/8 and their enter/exit handlers), `combat_projectiles`
//! (`launch`, `fire_shared_projectile`, `skill_projectile_destinations`) and `combat_effects`
//! (every effect spec plus `create_combat_effect`).
//!
//! Deliberately *not* here, and returned as an explicit error instead of guessed: the global tick
//! phases that consume what this module creates (projectile flight/impact, effect and modifier
//! ticking, garbage expiry, movement/facing/cell/height/animation passes).

// The ported loops index fixed-capacity arrays by slot on purpose: the slot order *is* the
// recovered iteration order, and several step two parallel arrays with one cursor.
#![allow(clippy::needless_range_loop)]

use crate::ai::{native_float_to_int, trunc_div, KA_ERR_CAPACITY, KA_ERR_UNKNOWN_SKILL_ROW};
use crate::state::KaPoint;
use crate::rng::i32_of;
use crate::state::*;
use crate::world::*;

/// The Python reached a route that raises (`use_fighter_skill`'s type-18 command branch has no
/// `reflect` callback, and uncatalogued effect routes are explicit `NotImplementedError`s).
pub const KA_ERR_UNSUPPORTED_ROUTE: i32 = -10;
/// A non-loop effect with `max_frame == 0` needed a resource frame count the Python only recovers
/// for resource 28.
pub const KA_ERR_EFFECT_RESOURCE: i32 = -11;
/// A status row referenced from `board[62]` is missing from the row table.
pub const KA_ERR_UNKNOWN_STATUS_ROW: i32 = -12;

/// One `create_combat_effect` spec, in the Python's field order.
#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct EffectSpec {
    pub type_: i32,
    pub value1: i32,
    pub value2: i32,
    pub res: i32,
    pub seb: i32,
    pub x: f32,
    pub y: f32,
    pub z: f32,
    pub scale: i32,
    pub image: i32,
    pub depth: u8,
    pub max_frame: i32,
    pub frame: i32,
    pub looping: u8,
    pub parent: i32,
    pub animate: u8,
}

/// One attack result (`resolve_attack`'s dict).
#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct AttackResult {
    pub attacker: i32,
    pub target: i32,
    /// Skill id, or `-1` for the Python `None`.
    pub skill: i32,
    pub hit: u8,
    pub critical: u8,
    pub damage: i32,
}

// ---------------------------------------------------------------------------------------------
// Resolution primitives
// ---------------------------------------------------------------------------------------------

/// `combat_resolution.critical_rate`.
pub fn critical_rate(luck: i32) -> i32 {
    if luck < 0 {
        return 10;
    }
    if luck >= 100000 {
        return 75;
    }
    let (low, high, steps, progress) = if luck < 100 {
        (10, 20, 99, luck)
    } else if luck < 1000 {
        (21, 35, 899, luck - 100)
    } else {
        (36, 75, 98999, luck - 1000)
    };
    if progress >= steps {
        return high;
    }
    let fraction = progress as f32 / steps as f32;
    let scaled = (fraction * (high - low) as f32 + low as f32).trunc() as i32;
    low.max(high.min(scaled))
}

/// `combat_resolution.hit_rate`.
pub fn hit_rate(dexterity: i32, agility: i32, luck: i32) -> i32 {
    let value = i32_of(
        i32_of(i32_of(dexterity as i64 - trunc_div(agility, 5) as i64) as i64
            - trunc_div(luck, 5) as i64) as i64
            + 150,
    );
    value.clamp(5, 97)
}

/// `combat_resolution.modified_rate`.
pub fn modified_rate(rate: i32, skill_value: i32, evasion: bool) -> i32 {
    let multiplier = i32_of(if evasion { 100 - skill_value } else { 100 + skill_value } as i64);
    trunc_div(i32_of(rate as i64 * multiplier as i64), 100)
}

/// `combat_resolution.random_range`.
pub fn random_range(raw_next_int: i32, low: i32, high: i32) -> i32 {
    let span = i32_of(high as i64 - low as i64 + 1);
    let raw = raw_next_int;
    i32_of(raw as i64 - trunc_div(raw, span) as i64 * span as i64 + low as i64)
}

/// `combat_resolution.base_damage`; `next_int` is the labelled `damage_variation` draw.
pub fn base_damage(attack: i32, defense: i32, battle: &mut KaBattle) -> i32 {
    let attack_roll = random_range(battle.draw_math(), 80, 120);
    let scaled_attack = trunc_div(i32_of(attack as i64 * attack_roll as i64), 100).max(1);
    let defense_roll = random_range(battle.draw_math(), 70, 90);
    let damage = i32_of(scaled_attack as i64 - trunc_div(i32_of(defense as i64 * defense_roll as i64), 100) as i64);
    if damage <= 5 {
        random_range(battle.draw_math(), 1, 10)
    } else {
        damage
    }
}

/// `combat_resolution.damage_from_parameters`.
pub fn damage_from_parameters(
    battle: &mut KaBattle,
    critical: bool,
    magical: bool,
    attacker: usize,
    target: usize,
) -> i32 {
    let monster = battle.units[attacker].monster != 0;
    let default = if magical && monster {
        trunc_div(battle.units[attacker].param_value(19, 0), 2)
    } else {
        0
    };
    let attack = battle.units[attacker].param_value(if magical { 18 } else { 13 }, default);
    let mut defense = battle.units[target].param_value(14, 0);
    let status = battle.units[target].board.get(62).map(|id| id as i32);
    if let Some(id) = status {
        let row = battle.row(id).copied().ok_or(KA_ERR_UNKNOWN_STATUS_ROW).unwrap_or_default();
        if row.kind == 66 {
            defense = trunc_div(
                i32_of((i32_of(100 - row.value as i64)) as i64 * defense as i64),
                100,
            );
        }
    }
    if critical {
        defense = trunc_div(defense, 8);
    }
    base_damage(attack, defense, battle)
}

/// `combat_resolution.consume_invoking_attack`.
fn consume_invoking_attack(invoking: &mut [KaInvoke; KA_MAX_INVOKE], count: &mut u32) {
    let mut write = 0usize;
    for read in 0..*count as usize {
        let remaining = i32_of(invoking[read].remaining as i64 - 1);
        if remaining > 0 {
            invoking[write] = KaInvoke { skill: invoking[read].skill, remaining };
            write += 1;
        }
    }
    *count = write as u32;
}

/// `SharedAttackMath.first_invoking`.
fn first_invoking(battle: &KaBattle, index: usize, kind: i32) -> Option<KaSkill> {
    let unit = &battle.units[index];
    for slot in 0..unit.invoking_count as usize {
        let row = battle.row(unit.invoking[slot].skill).copied()?;
        if row.kind == kind {
            return Some(row);
        }
    }
    None
}

/// `SharedAttackMath.critical`.
fn math_critical(battle: &mut KaBattle, attacker: usize) -> bool {
    let mut rate = critical_rate(battle.units[attacker].param_value(16, 0));
    if let Some(skill) = first_invoking(battle, attacker, 23) {
        rate = modified_rate(rate, skill.value, false);
    }
    let draw = battle.next_math(100);
    draw < rate
}

/// `SharedAttackMath.hit`.
fn math_hit(battle: &mut KaBattle, attacker: usize, target: usize) -> bool {
    let mut rate = hit_rate(
        battle.units[attacker].param_value(19, 0),
        battle.units[target].param_value(15, 0),
        battle.units[target].param_value(16, 0),
    );
    if let Some(skill) = first_invoking(battle, target, 22) {
        rate = modified_rate(rate, skill.value, true);
    }
    let draw = battle.next_math(100);
    draw < rate
}

// ---------------------------------------------------------------------------------------------
// Effects
// ---------------------------------------------------------------------------------------------

/// `combat_effects.create_combat_effect`: the exact component insertion order, including the
/// optional cell/depth pair and the trailing image/animation.
pub fn create_effect(battle: &mut KaBattle, spec: &EffectSpec) -> Result<i32, i32> {
    let mut duration = spec.max_frame;
    if duration == 0 && spec.looping == 0 {
        // `SharedControllers.create_effect.resource` only resolves resource 28.
        if spec.res != 28 {
            return Err(KA_ERR_EFFECT_RESOURCE);
        }
        duration = i32_of(battle.effect_resource(spec.seb) as i64 - 1);
    }
    let entity = battle.allocate();
    {
        let target = battle.entity_mut(entity).ok_or(KA_ERR_CAPACITY)?;
        target.effect = KaEffect {
            type_: spec.type_,
            value1: spec.value1,
            value2: spec.value2,
            depth: spec.depth,
            frame: spec.frame,
            max_frame: duration,
            parent: spec.parent,
            scale: spec.scale,
        };
    }
    battle.add_component(entity, SLOT_EFFECT);
    {
        let target = battle.entity_mut(entity).ok_or(KA_ERR_CAPACITY)?;
        target.position = KaVec3 { x: spec.x, y: spec.y, z: spec.z };
        target.offset = KaVec3::default();
        target.parent = -1;
    }
    battle.add_component(entity, SLOT_POSITION);
    battle.add_component(entity, SLOT_SPEED);
    {
        let target = battle.entity_mut(entity).ok_or(KA_ERR_CAPACITY)?;
        target.seb = [spec.res, spec.seb, spec.frame, -1];
    }
    battle.add_component(entity, SLOT_SEB);
    if spec.depth != 0 {
        {
            let target = battle.entity_mut(entity).ok_or(KA_ERR_CAPACITY)?;
            target.cell = [
                trunc_div(native_float_to_int(spec.x), 24),
                trunc_div(native_float_to_int(spec.z), 24),
            ];
        }
        battle.add_component(entity, SLOT_CELL);
        {
            let target = battle.entity_mut(entity).ok_or(KA_ERR_CAPACITY)?;
            target.depth = [0, 40];
        }
        battle.add_component(entity, SLOT_DEPTH);
    }
    if spec.looping == 0 {
        {
            let target = battle.entity_mut(entity).ok_or(KA_ERR_CAPACITY)?;
            target.garbage = duration;
        }
        battle.add_component(entity, SLOT_GARBAGE);
    }
    if spec.image != -1 {
        {
            let target = battle.entity_mut(entity).ok_or(KA_ERR_CAPACITY)?;
            target.image = [spec.image, spec.res, -1, -1, -1, -1];
        }
        battle.add_component(entity, SLOT_IMAGE);
    }
    if spec.animate != 0 {
        {
            let target = battle.entity_mut(entity).ok_or(KA_ERR_CAPACITY)?;
            target.animation = [1, -1];
        }
        battle.add_component(entity, SLOT_ANIMATION);
    }
    // `effect_birth` carries (effect, type, lifetime); the lifetime is the resolved duration.
    battle.push_event(KA_EVENT_CREATE, entity, spec.type_, duration, spec.res, spec.seb, 0);
    Ok(entity)
}

/// `combat_effects.departure_effect_spec`.
pub fn departure_effect_spec(position: KaVec3) -> EffectSpec {
    EffectSpec {
        type_: 0,
        value1: 0,
        value2: 0,
        res: 28,
        seb: 0,
        x: position.x,
        y: position.y + 10.0,
        z: position.z,
        scale: 100,
        image: 6,
        depth: 1,
        max_frame: 0,
        frame: 0,
        looping: 0,
        parent: -1,
        animate: 1,
    }
}

/// `combat_effects.cell_skill_effect_spec`.
pub fn cell_skill_effect_spec(skill: &KaSkill, cell: (i32, i32)) -> EffectSpec {
    EffectSpec {
        type_: 0,
        value1: 0,
        value2: 0,
        res: 28,
        seb: skill.seb,
        x: i32_of(cell.0 as i64 * 24) as f32,
        y: 30.0,
        z: i32_of(cell.1 as i64 * 24) as f32,
        scale: 100,
        image: skill.img,
        depth: 1,
        max_frame: 0,
        frame: 0,
        looping: 0,
        parent: -1,
        animate: 1,
    }
}

/// `combat_effects.skill_balloon_spec`.
pub fn skill_balloon_spec(skill_id: i32, position: KaVec3, ally: bool) -> EffectSpec {
    EffectSpec {
        type_: 15,
        value1: skill_id,
        value2: i32::from(!ally),
        res: 6,
        seb: 160,
        x: position.x,
        y: position.y + 50.0,
        z: position.z,
        scale: 100,
        image: -1,
        depth: 0,
        max_frame: 20,
        frame: 0,
        looping: 0,
        parent: -1,
        animate: 1,
    }
}

/// `combat_effects.healing_effect_spec`.
pub fn healing_effect_spec(amount: i32, position: KaVec3) -> EffectSpec {
    EffectSpec {
        type_: 1,
        value1: amount,
        value2: 0,
        res: 4,
        seb: 279,
        x: position.x,
        y: position.y + 20.0,
        z: position.z,
        scale: 100,
        image: -1,
        depth: 0,
        max_frame: 30,
        frame: 0,
        looping: 0,
        parent: -1,
        animate: 1,
    }
}

/// `combat_effects.attack_effect_specs`; returns the specs in native order.
pub fn attack_effect_specs(
    result: &AttackResult,
    position: KaVec3,
    attacker_ally: bool,
    out: &mut [EffectSpec; 3],
) -> usize {
    let spec = |type_: i32, value: i32, res: i32, seb: i32, y: f32, scale: i32, image: i32,
                depth: u8, duration: i32| EffectSpec {
        type_,
        value1: value,
        value2: 0,
        res,
        seb,
        x: position.x,
        y: position.y + y,
        z: position.z,
        scale,
        image,
        depth,
        max_frame: duration,
        frame: 0,
        looping: 0,
        parent: -1,
        animate: 1,
    };
    if result.hit == 0 {
        out[0] = spec(12, 0, 6, 126, 15.0, 100, -1, 0, 30);
        return 1;
    }
    let critical = result.critical != 0;
    let scale = if critical { 150 } else { 100 };
    let mut len = 0usize;
    out[len] = spec(0, 0, 28, 0, 10.0, scale, if critical { 1 } else { 2 }, 1, 0);
    len += 1;
    if critical {
        out[len] = spec(12, 9, 6, 126, 15.0, 100, -1, 0, 30);
        len += 1;
    }
    out[len] = spec(
        1,
        result.damage,
        4,
        if critical { 237 } else { 78 },
        20.0,
        scale,
        -1,
        0,
        30,
    );
    // The non-critical ally/enemy resource split changes `seb` only for the damage number.
    if !critical && !attacker_ally {
        out[len].seb = 112;
    }
    if critical && !attacker_ally {
        out[len].seb = 112;
    }
    len + 1
}

// ---------------------------------------------------------------------------------------------
// Projectiles
// ---------------------------------------------------------------------------------------------

/// `combat_projectiles.launch` reduced to the values `fire_shared_projectile` writes.
fn launch(start: KaVec3, end: KaVec3) -> (i32, f32) {
    let dx = end.x - start.x;
    let dy = end.y - start.y;
    let dz = end.z - start.z;
    let distance = ((dx * dx + dy * dy) + dz * dz).sqrt();
    let height = (20.0f32).max((100.0f32).min((distance * distance / 100.0).trunc()));
    (height as i32, distance)
}

/// `combat_projectiles.fire_shared_projectile`.
pub fn fire_shared_projectile(
    battle: &mut KaBattle,
    identity: i32,
    start: KaVec3,
    end: KaVec3,
    speed: i32,
    owner: i32,
    animate: bool,
    attack_skill: i32,
) -> i32 {
    let speed = speed.max(1);
    let (height, distance) = launch(start, end);
    let length = (distance / speed as f32).trunc() as i32;
    battle.remove_component(identity, SLOT_MODIFY_ANIMATION);
    // `CombatEntities.add_component` silently ignores an occupied slot, so a second `body_flight` on
    // an entity that still carries `Projectile` keeps the existing record - the payload must not be
    // rewritten before that check.
    if !battle.has(identity, SLOT_PROJECTILE) {
        if let Some(entity) = battle.entity_mut(identity) {
            entity.projectile = KaProjectile { start, end, speed, height, frame: 0, length, owner };
        }
        battle.add_component(identity, SLOT_PROJECTILE);
    }
    {
        if let Some(entity) = battle.entity_mut(identity) {
            entity.modifier = KaModifier {
                type_: 7,
                offset_x: 0.0,
                offset_y: 0.0,
                offset_z: 0.0,
                scale_x: 1.0,
                scale_y: 1.0,
                angle: 0,
                anchor: 0,
                frame: 0,
                duration: 0,
                destroy_on_finish: 0,
                looping: 1,
                alpha: 255,
            };
        }
    }
    battle.add_component(identity, SLOT_MODIFY_ANIMATION);
    if animate {
        if let Some(entity) = battle.entity_mut(identity) {
            entity.animation = [1, -1];
        }
        battle.add_component(identity, SLOT_ANIMATION);
    }
    if attack_skill >= 0 {
        if let Some(entity) = battle.entity_mut(identity) {
            entity.attack = attack_skill;
        }
        battle.add_component(identity, SLOT_ATTACK);
    }
    length
}

/// `combat_geometry.target_area`.
fn target_area(size: i32, out: &mut [(i32, i32); 64]) -> usize {
    let mut len = 0usize;
    if size <= 0 {
        return 0;
    }
    if len < out.len() {
        out[len] = (0, 0);
        len += 1;
    }
    for radius in 1..size {
        let mut ox = 0i32;
        let mut oy = -radius;
        for (dx, dy) in [(1i32, 1i32), (-1, 1), (-1, -1), (1, -1)] {
            for _ in 0..radius {
                if len < out.len() {
                    out[len] = (ox, oy);
                    len += 1;
                }
                ox += dx;
                oy += dy;
            }
        }
    }
    len
}

/// `combat_projectiles.skill_projectile_destinations`.
fn skill_projectile_destinations(
    target: KaVec3,
    area_range: i32,
    out: &mut [KaVec3; 64],
) -> usize {
    let mut offsets = [(0i32, 0i32); 64];
    let len = target_area(area_range, &mut offsets);
    for index in 0..len {
        let (dx, dy) = offsets[index];
        out[index] = KaVec3 {
            x: target.x + i32_of(dx as i64 * 24) as f32,
            y: target.y,
            z: target.z + i32_of(dy as i64 * 24) as f32,
        };
    }
    len
}

/// `SharedControllers.fire_skill_projectiles`.
pub fn fire_skill_projectiles(
    battle: &mut KaBattle,
    caster: usize,
    target: usize,
    skill: &KaSkill,
) -> Result<(), i32> {
    let position = battle.units[caster].body.position;
    let start = KaVec3 { x: position.x, y: position.y + 15.0, z: position.z };
    let mut ends = [KaVec3::default(); 64];
    let count = skill_projectile_destinations(battle.units[target].body.position, skill.range, &mut ends);
    let caster_id = battle.units[caster].identity;
    let target_id = battle.units[target].identity;
    for index in 0..count {
        let end = ends[index];
        let identity = battle.allocate();
        {
            let entity = battle.entity_mut(identity).ok_or(KA_ERR_CAPACITY)?;
            entity.position = start;
            entity.offset = KaVec3::default();
            entity.parent = -1;
        }
        battle.add_component(identity, SLOT_POSITION);
        {
            let entity = battle.entity_mut(identity).ok_or(KA_ERR_CAPACITY)?;
            let cell = crate::ai::battle_cell_pair(
                native_float_to_int(start.x),
                native_float_to_int(start.z),
            );
            entity.cell = [cell.0, cell.1];
        }
        battle.add_component(identity, SLOT_CELL);
        battle.add_component(identity, SLOT_SPEED);
        {
            let entity = battle.entity_mut(identity).ok_or(KA_ERR_CAPACITY)?;
            entity.seb = [28, skill.seb, 0, -1];
        }
        battle.add_component(identity, SLOT_SEB);
        {
            let entity = battle.entity_mut(identity).ok_or(KA_ERR_CAPACITY)?;
            entity.image = [skill.img, -1, -1, -1, -1, -1];
        }
        battle.add_component(identity, SLOT_IMAGE);
        {
            let entity = battle.entity_mut(identity).ok_or(KA_ERR_CAPACITY)?;
            entity.depth = [0, 0];
        }
        battle.add_component(identity, SLOT_DEPTH);
        {
            let entity = battle.entity_mut(identity).ok_or(KA_ERR_CAPACITY)?;
            entity.attack = skill.id;
        }
        battle.add_component(identity, SLOT_ATTACK);
        fire_shared_projectile(battle, identity, start, end, 10, caster_id, true, skill.id);
        // `self.projectile_sources[identity] = (i, s, c, index)` - the impact path reads the caster
        // and the skill row back out of here.
        battle.set_projectile_source(identity, caster_id, skill.id);
        // `fire_skill_projectiles` ends each launched projectile with its own `self.sound(s)`, so the
        // Lib stream takes one draw per projectile - not one per skill use.
        skill_sound(battle, skill);
        // `projectile_launch` carries (projectile, owner, storedTarget, skill).
        battle.push_event(KA_EVENT_PROJECTILE_LAUNCH, identity, caster_id, target_id, skill.id, 0, 0);
    }
    Ok(())
}

/// `SharedControllers.body_flight` - a projectile launched on the fighter's own entity.
pub fn body_flight(battle: &mut KaBattle, index: usize, speed: i32, start: KaVec3, end: KaVec3) {
    let identity = battle.units[index].identity;
    let occupied = battle.has(identity, SLOT_PROJECTILE);
    fire_shared_projectile(battle, identity, start, end, speed, identity, false, -1);
    battle.push_event(KA_EVENT_BODY_FLIGHT, identity, speed, i32::from(occupied), 0, 0, 0);
}

/// Keep `KaPoint` referenced so the module's imports stay meaningful for later slices.
pub type _PathPoint = KaPoint;

// ---------------------------------------------------------------------------------------------
// Attack orchestration
// ---------------------------------------------------------------------------------------------

/// `SharedControllers.pay`: the MP subtraction is a plain clamp, not `Parameter.Sub`.
pub fn pay(battle: &mut KaBattle, index: usize, skill: &KaSkill) -> i32 {
    let before = battle.units[index].mp();
    let amount = crate::ai::skill_cost(battle, index, skill);
    if let Some(row) = battle.units[index].params.find_mut(11) {
        row.raw_value = (row.raw_value - amount).max(0);
    }
    let actor = battle.units[index].identity;
    battle.push_event(KA_EVENT_MP_PAY, actor, skill.id, before, amount, 0, 0);
    amount
}

/// `combat_skills.play_skill_sound`: the Lib draw happens even when playback is suppressed.
pub fn skill_sound(battle: &mut KaBattle, _skill: &KaSkill) {
    battle.draw_lib_label(KA_LIB_SKILL_SOUND);
}

/// `SharedControllers.sound`.
pub fn sound(battle: &mut KaBattle, skill: &KaSkill) {
    skill_sound(battle, skill);
}

/// `SharedControllers.balloon`.
pub fn balloon(battle: &mut KaBattle, index: usize, skill: &KaSkill) -> Result<i32, i32> {
    let position = battle.units[index].body.position;
    let ally = battle.units[index].ally();
    let spec = skill_balloon_spec(skill.id, position, ally);
    create_effect(battle, &spec)
}

/// `SharedControllers.state_sound`: one Lib draw, then the `<100` comparison. The canonical trace
/// kind is a single `state_sound`; the leaving/knockdown distinction is only the RNG label.
pub fn state_sound(battle: &mut KaBattle, purpose: i32, sound_id: i32) -> bool {
    let draw = battle.next_lib_label(purpose, 100);
    let passed = draw < 100;
    battle.push_event(KA_EVENT_STATE_SOUND, sound_id, i32::from(passed), 0, 0, 0, 0);
    passed
}

/// `SharedControllers.attack_effect`.
fn attack_effect(battle: &mut KaBattle, result: &AttackResult) -> Result<(), i32> {
    let target = result.target;
    if !battle.target_exists(target) {
        return Ok(());
    }
    let target_index = match battle.fighter_slot(target) {
        Some(index) => index,
        None => return Ok(()),
    };
    let position = battle.units[target_index].body.position;
    let attacker_ally = battle
        .fighter_slot(result.attacker)
        .map(|index| battle.units[index].ally())
        .unwrap_or(false);
    let mut specs = [EffectSpec::default(); 3];
    let count = attack_effect_specs(result, position, attacker_ally, &mut specs);
    for slot in 0..count {
        create_effect(battle, &specs[slot])?;
    }
    if result.hit != 0 && battle.units[target_index].human == 0 {
        battle.remove_component(target, SLOT_MODIFY_ANIMATION);
        if let Some(entity) = battle.entity_mut(target) {
            entity.modifier = KaModifier {
                type_: 4,
                offset_x: 0.0,
                offset_y: 0.0,
                offset_z: 0.0,
                scale_x: 1.0,
                scale_y: 1.0,
                angle: 0,
                anchor: 0,
                frame: 0,
                duration: 6,
                destroy_on_finish: 0,
                looping: 0,
                alpha: 255,
            };
        }
        battle.add_component(target, SLOT_MODIFY_ANIMATION);
    }
    Ok(())
}

/// `SharedControllers.deliver`: Battle's whole-array effect pass, then Fighter's HP pass.
fn deliver(battle: &mut KaBattle, results: &[AttackResult]) -> Result<(), i32> {
    // `deliver` opens with `attack_batch`, carrying one id per delivered result.
    battle.push_event(KA_EVENT_ATTACK_BATCH, results.len() as i32, 0, 0, 0, 0, 0);
    for result in results {
        attack_effect(battle, result)?;
    }
    // Encounter telemetry: the true own-team death is the HP-positive -> zero transition at this HP
    // mutation, never the Leaving entry. The rival leader is identified from the engine's own unit
    // flags so the post-death attempt/land counters use the event's target identity. Read-only.
    let mut own_roster = [0i32; KA_MAX_UNITS];
    let own_len = battle.roster(0, &mut own_roster);
    let boss = encounter_boss_identity(battle);
    for result in results {
        if !battle.target_exists(result.target) {
            continue;
        }
        let index = match battle.fighter_slot(result.target) {
            Some(index) => index,
            None => continue,
        };
        let team = battle.units[index].board_team();
        let was_hp = battle.units[index].hp();
        let tick = battle.tick as i32;
        // An attempt on an already-dead boss counts whether or not the post-reaction result hit.
        if result.target == boss && was_hp == 0 && battle.encounter_boss_death_tick >= 0 {
            battle.encounter_boss_postdeath_attempts += 1;
        }
        if result.target == boss && battle.progress_boss_access_first_attempt < 0 {
            battle.progress_boss_access_first_attempt = tick;
        }
        if result.hit == 0 {
            continue;
        }
        let target = result.target;
        let damage = result.damage;
        battle.units[index].params.subtract_raw(10, damage);
        let hp = battle.units[index].hp();
        if team == 0 && was_hp > 0 && hp == 0 {
            for slot in 0..own_len as usize {
                if own_roster[slot] as usize == index && battle.encounter_own_death_tick[slot] < 0 {
                    battle.encounter_own_death_tick[slot] = tick;
                }
            }
        }
        if target == boss && was_hp > 0 && hp == 0 {
            battle.encounter_boss_death_tick = tick;
        }
        if target == boss && was_hp == 0 && battle.encounter_boss_death_tick >= 0 {
            battle.encounter_boss_postdeath_lands += 1;
            battle.encounter_boss_reentries += 1;
            if battle.encounter_boss_postdeath_last_hit >= 0 {
                let gap = tick - battle.encounter_boss_postdeath_last_hit;
                battle.encounter_boss_postdeath_gap_count += 1;
                battle.encounter_boss_postdeath_gap_sum += gap;
                if battle.encounter_boss_postdeath_gap_min < 0
                    || gap < battle.encounter_boss_postdeath_gap_min
                {
                    battle.encounter_boss_postdeath_gap_min = gap;
                }
                if gap > battle.encounter_boss_postdeath_gap_max {
                    battle.encounter_boss_postdeath_gap_max = gap;
                }
            }
            battle.encounter_boss_postdeath_last_hit = tick;
        }
        crate::ai::change(battle, index, 6)?;
        battle.push_event(KA_EVENT_HP, target, damage, hp, 1, 0, 0);
    }
    Ok(())
}

/// The rival leader's stable identity, from the engine's own unit flags; `-1` when none exists.
/// Used only by the read-only encounter counters, never by a combat decision.
fn encounter_boss_identity(battle: &KaBattle) -> i32 {
    for index in 0..battle.count as usize {
        if battle.units[index].present != 0 && battle.units[index].boss != 0 {
            return battle.units[index].identity;
        }
    }
    -1
}

/// `SharedControllers.reaction` -> `combat_resolution.defender_attack_phase`.
fn reaction(
    battle: &mut KaBattle,
    defender: usize,
    attacker: i32,
    hit: bool,
    critical: bool,
    damage: i32,
) -> Result<(bool, bool, i32), i32> {
    let defender_id = battle.units[defender].identity;
    if hit {
        let mut board = battle.units[defender].board;
        if board.contains(62) && board.contains(63) {
            let remaining = i32_of(board.get_or(63, 0) - 1);
            board.set(63, remaining as i64);
            if remaining <= 0 {
                for key in [62, 63, 64] {
                    board.remove(key);
                }
            }
        }
        battle.units[defender].board = board;
    }
    // `zip(specs[i]['skills'], specs[i]['levels'])` - the *unfiltered* slot pairing.
    let count = (battle.units[defender].skill_count as usize)
        .min(battle.units[defender].level_count as usize);
    let mut pairs = [(0i32, 0i32); KA_MAX_SKILLS];
    let mut pair_count = 0usize;
    for slot in 0..count {
        let id = battle.units[defender].skill_ids[slot];
        let row = battle.row(id).copied().ok_or(KA_ERR_UNKNOWN_SKILL_ROW)?;
        if row.flags & 32 != 0 {
            pairs[pair_count] = (id, battle.units[defender].levels[slot]);
            pair_count += 1;
        }
    }
    let mut state = (hit, critical, damage);
    for slot in 0..pair_count {
        // Encounter telemetry: one reaction-skill candidate examined (a counter check).
        battle.encounter_counter_checks += 1;
        let (id, level) = pairs[slot];
        let row = battle.row(id).copied().ok_or(KA_ERR_UNKNOWN_SKILL_ROW)?;
        let eligible = crate::ai::eligible(battle, defender, &row, Some(attacker), true);
        if !eligible || !crate::ai::invoke(battle, defender, &row, level)? {
            continue;
        }
        match row.kind {
            18 => {
                reflect(battle, defender_id, attacker, &row, state.2)?;
            }
            20 => {
                // Encounter telemetry: a kind-20 stored command was enqueued (a counter enqueue).
                battle.encounter_counter_enqueues += 1;
                crate::ai::enqueue_command(battle, defender, &row, attacker)?;
            }
            21 | 24 => {
                if row.kind == 21 {
                    state.2 = trunc_div(i32_of(row.value as i64 * state.2 as i64), 100).max(1);
                }
                pay(battle, defender, &row);
                balloon(battle, defender, &row)?;
                if row.kind == 24 {
                    state = (false, false, 0);
                }
            }
            _ => {}
        }
        // `sound_roll()` runs after the branch, for every accepted candidate.
        battle.draw_lib_label(KA_LIB_DEFENDER_SOUND);
        break;
    }
    Ok(state)
}

/// `SharedControllers.reflect` -> `combat_resolution.reflected_attack_result`.
fn reflect(
    battle: &mut KaBattle,
    attacker: i32,
    target: i32,
    skill: &KaSkill,
    incoming_damage: i32,
) -> Result<AttackResult, i32> {
    let damage = trunc_div(i32_of(skill.value as i64 * incoming_damage as i64), 100).max(1);
    let result = AttackResult { attacker, target, skill: skill.id, hit: 1, critical: 0, damage };
    battle.attack_events += 1;
        battle.push_event(KA_EVENT_ATTACK, attacker, target, skill.id, 1, 0, damage);
    if let Some(index) = battle.fighter_slot(attacker) {
        crate::ai::eligible(battle, index, skill, Some(target), true);
        pay(battle, index, skill);
    }
    deliver(battle, &[result])?;
    // `SharedControllers.reflect` runs `use_fighter_skill(s, 0, {..., sound: lambda: self.sound(s)})`.
    // For a type-18 skill that reaches the type-18 branch, which ends with `effects['sound']()` - one
    // `skill_sound` Lib draw after the send. Omitting it left a reflected attack one draw short.
    skill_sound(battle, skill);
    Ok(result)
}

/// `SharedControllers.after_attack` -> `combat_resolution.attacker_invocation_phase`.
fn after_attack(battle: &mut KaBattle, attacker: usize) -> Result<(), i32> {
    let count = (battle.units[attacker].skill_count as usize)
        .min(battle.units[attacker].level_count as usize);
    let mut pairs = [(0i32, 0i32); KA_MAX_SKILLS];
    let mut pair_count = 0usize;
    for slot in 0..count {
        let id = battle.units[attacker].skill_ids[slot];
        let row = battle.row(id).copied().ok_or(KA_ERR_UNKNOWN_SKILL_ROW)?;
        if row.flags & 64 != 0 {
            pairs[pair_count] = (id, battle.units[attacker].levels[slot]);
            pair_count += 1;
        }
    }
    let before = battle.units[attacker].invoking;
    let before_count = battle.units[attacker].invoking_count;
    let mut invoking = battle.units[attacker].invoking;
    let mut invoking_count = before_count;
    let mut appended: i32 = -1;
    consume_invoking_attack(&mut invoking, &mut invoking_count);
    // Python mutates the unit's invoking list before checking candidate eligibility.
    // A charge that reaches zero on this attack must no longer block that same skill.
    battle.units[attacker].invoking = invoking;
    battle.units[attacker].invoking_count = invoking_count;
    for slot in 0..pair_count {
        let (id, level) = pairs[slot];
        let row = battle.row(id).copied().ok_or(KA_ERR_UNKNOWN_SKILL_ROW)?;
        let eligible = crate::ai::eligible(battle, attacker, &row, None, true);
        if !eligible || !crate::ai::invoke(battle, attacker, &row, level)? {
            continue;
        }
        pay(battle, attacker, &row);
        balloon(battle, attacker, &row)?;
        let already = (0..invoking_count as usize).any(|i| invoking[i].skill == row.id);
        if !already && (invoking_count as usize) < KA_MAX_INVOKE {
            invoking[invoking_count as usize] = KaInvoke { skill: row.id, remaining: 10 };
            invoking_count += 1;
            appended = row.id;
        }
        break;
    }
    battle.units[attacker].invoking = invoking;
    battle.units[attacker].invoking_count = invoking_count;
    if invoking_count != before_count
        || (0..before_count as usize)
            .any(|i| before[i].skill != invoking[i].skill || before[i].remaining != invoking[i].remaining)
    {
        battle.push_event(
            KA_EVENT_INVOKING,
            battle.units[attacker].identity,
            before_count as i32,
            invoking_count as i32,
            appended,
            0,
            0,
        );
    }
    Ok(())
}

/// `SharedControllers.attack_result` -> `combat_resolution.resolve_attack`.
pub fn attack_result(
    battle: &mut KaBattle,
    attacker: usize,
    target: i32,
    skill: Option<i32>,
) -> Result<AttackResult, i32> {
    let attacker_id = battle.units[attacker].identity;
    if !battle.target_exists(target) {
        return Ok(AttackResult {
            attacker: attacker_id,
            target,
            skill: skill.unwrap_or(-1),
            hit: 0,
            critical: 0,
            damage: 0,
        });
    }
    let target_index = battle.fighter_slot(target);
    let magical = match skill {
        Some(id) => battle.row(id).map(|row| row.kind == 1).unwrap_or(false),
        None => false,
    };
    let critical = math_critical(battle, attacker);
    let hit = critical
        || match target_index {
            Some(index) => math_hit(battle, attacker, index),
            None => false,
        };
    // Encounter telemetry: the enemy (team != 0) *pre-reaction* hit roll, recorded before the
    // reaction can flip it (a kind-24 reaction turns a hit into a miss). Read-only; no phase reads
    // these fields. The post-reaction result is counted after `reaction` returns, below.
    if battle.units[attacker].board_team() != 0 {
        battle.encounter_enemy_rolls += 1;
        if hit {
            battle.encounter_enemy_roll_hits += 1;
        } else {
            battle.encounter_enemy_roll_misses += 1;
        }
    }
    let mut state = if hit {
        match target_index {
            Some(index) => (true, critical, damage_from_parameters(battle, critical, magical, attacker, index)),
            None => (true, critical, 0),
        }
    } else {
        (false, critical, 0)
    };
    if let Some(index) = target_index {
        state = reaction(battle, index, attacker_id, state.0, state.1, state.2)?;
    }
    after_attack(battle, attacker)?;
    // Encounter telemetry: the *resolved* enemy attack, counted AFTER `reaction` so a reaction
    // that converts the result is reflected rather than double-counted as a roll. Read-only.
    if battle.units[attacker].board_team() != 0 {
        battle.encounter_enemy_resolved += 1;
        if state.0 {
            battle.encounter_enemy_hits += 1;
        } else {
            battle.encounter_enemy_misses += 1;
        }
    }
    let result = AttackResult {
        attacker: attacker_id,
        target,
        skill: skill.unwrap_or(-1),
        hit: u8::from(state.0),
        critical: u8::from(state.1),
        damage: state.2,
    };
    battle.attack_events += 1;
    battle.push_event(KA_EVENT_ATTACK, attacker_id, target, result.skill,
                      result.hit as i32, result.critical as i32, result.damage);
    Ok(result)
}

/// `SharedControllers.attack`: resolve, then deliver.
pub fn attack(
    battle: &mut KaBattle,
    attacker: usize,
    target: i32,
    skill: Option<i32>,
) -> Result<AttackResult, i32> {
    let result = attack_result(battle, attacker, target, skill)?;
    deliver(battle, &[result])?;
    Ok(result)
}

/// `combat_targeting.can_damage_shared`.
pub fn can_damage_shared(battle: &KaBattle, attacker: i32, target: i32, skill: Option<i32>) -> bool {
    let target_entity = match battle.entity(target) {
        Some(entity) => entity,
        None => return false,
    };
    if target_entity.has[SLOT_PARAMETER] == 0 {
        return false;
    }
    let target_index = match battle.fighter_slot(target) {
        Some(index) => index,
        None => return false,
    };
    if skill.is_some() && target_entity.has[11] != 0 {
        return false;
    }
    let unit = &battle.units[target_index];
    let stored = crate::rng::i32_of(
        unit.raw(10) as i64
            + unit.params.find(10).map(|row| row.extra_value).unwrap_or(0) as i64,
    );
    if stored <= 0 {
        return false;
    }
    // `is_main_world` is False in the special battle world, so boss immunity does not apply here.
    let attacker_has_49 = battle
        .entity(attacker)
        .map(|entity| entity.has[49] != 0)
        .unwrap_or(false);
    if attacker_has_49 == (target_entity.has[49] != 0) {
        return false;
    }
    if target_entity.has[20] != 0 && unit.monster_type == 1 {
        return false;
    }
    true
}

/// `combat_targeting.damage_cell`.
fn damage_cell(
    battle: &mut KaBattle,
    occupants: &[i32],
    attacker: usize,
    skill: Option<i32>,
) -> Result<(), i32> {
    let attacker_id = battle.units[attacker].identity;
    let mut targets = [0i32; KA_MAX_BUCKET_MEMBERS];
    let mut count = 0usize;
    for id in occupants {
        if can_damage_shared(battle, attacker_id, *id, skill) {
            targets[count] = *id;
            count += 1;
        }
    }
    if count == 0 {
        return Ok(());
    }
    let mut results = [AttackResult::default(); KA_MAX_BUCKET_MEMBERS];
    for slot in 0..count {
        results[slot] = attack_result(battle, attacker, targets[slot], skill)?;
    }
    deliver(battle, &results[..count])
}

/// `SharedControllers.hit_cell`: the occupants are filtered to the registered fighters.
pub fn hit_cell(
    battle: &mut KaBattle,
    caster: usize,
    skill: &KaSkill,
    cell: (i32, i32),
) -> Result<(), i32> {
    let caster_id = battle.units[caster].identity;
    let key = battle.cell_key([cell.0, cell.1]);
    let mut occupants = [0i32; KA_MAX_BUCKET_MEMBERS];
    let mut count = 0usize;
    if let Some(slot) =
        (0..battle.bucket_count as usize).find(|&slot| battle.buckets[slot].key == key)
    {
        let bucket = battle.buckets[slot];
        for position in 0..bucket.count as usize {
            let id = bucket.ids[position];
            if battle.fighter_slot(id).is_some() {
                occupants[count] = id;
                count += 1;
            }
        }
    }
    battle.push_event(KA_EVENT_AREA_CELL, caster_id, skill.id, cell.0, cell.1, 0, 0);
    damage_cell(battle, &occupants[..count], caster, Some(skill.id))
}

// ---------------------------------------------------------------------------------------------
// Cure / heal
// ---------------------------------------------------------------------------------------------

/// One `cure_result` payload.
#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct CureResult {
    pub caster: i32,
    pub target: i32,
    pub skill: i32,
    pub amount: i32,
}

/// `SharedControllers.heal` -> `combat_resolution.cure_result`.
pub fn heal(battle: &KaBattle, caster: usize, target: i32, skill: &KaSkill) -> CureResult {
    let maximum = battle.units[caster].param_maximum(10);
    let _ = maximum;
    let target_max = match battle.fighter_slot(target) {
        Some(index) => battle.units[index].param_maximum(10),
        None => 0,
    };
    CureResult {
        caster: battle.units[caster].identity,
        target,
        skill: skill.id,
        amount: trunc_div(i32_of(skill.value as i64 * target_max as i64), 100),
    }
}

/// `SharedControllers.deliver_cures`.
pub fn deliver_cures(battle: &mut KaBattle, results: &[CureResult]) -> Result<(), i32> {
    for result in results {
        if !battle.target_exists(result.target) {
            continue;
        }
        if let Some(index) = battle.fighter_slot(result.target) {
            let position = battle.units[index].body.position;
            let spec = healing_effect_spec(result.amount, position);
            create_effect(battle, &spec)?;
        }
    }
    let mut before = [0i32; KA_MAX_BUCKET_MEMBERS];
    let mut seen = [0u8; KA_MAX_BUCKET_MEMBERS];
    let mut before_count = 0usize;
    for result in results {
        if !battle.target_exists(result.target) {
            continue;
        }
        if let Some(index) = battle.fighter_slot(result.target) {
            before[before_count] = battle.units[index].hp();
            seen[before_count] = 1;
            before_count += 1;
        }
    }
    let _ = (before_count, seen);
    for result in results {
        if !battle.target_exists(result.target) {
            continue;
        }
        let index = match battle.fighter_slot(result.target) {
            Some(index) => index,
            None => continue,
        };
        let maximum = battle.units[index].param_maximum(10);
        let before = battle.units[index].hp();
        battle.units[index].params.add_raw(10, result.amount, maximum);
        let row = battle.row(result.skill).copied().ok_or(KA_ERR_UNKNOWN_SKILL_ROW)?;
        if row.kind == 15 {
            let position = cure_queue_position(battle, index);
            if let Some((x, y)) = position {
                battle.units[index].board.set(13, x as i64);
                battle.units[index].board.set(14, y as i64);
                crate::ai::change(battle, index, 2)?;
            }
        }
        let hp = battle.units[index].hp();
        battle.heal_events += 1;
        battle.push_event(KA_EVENT_HEAL, result.caster, result.target, result.skill,
                          result.amount, before, hp);
        if row.kind == 15 {
            battle.push_event(KA_EVENT_REVIVE, result.target, result.skill,
                              battle.units[index].state(), hp, 0, 0);
        }
    }
    Ok(())
}

/// `SharedControllers.cure_queue_position`.
fn cure_queue_position(battle: &KaBattle, index: usize) -> Option<(i32, i32)> {
    let team = battle.units[index].board_team();
    let mut roster = [0i32; KA_MAX_UNITS];
    let roster_len = battle.roster(team, &mut roster);
    Some(crate::ai::queue_position(battle, index, &roster, roster_len, battle.row_offset))
}

// ---------------------------------------------------------------------------------------------
// Buffs
// ---------------------------------------------------------------------------------------------

/// `combat_resolution.buff_accuracy`.
fn buff_accuracy(dexterity: i32, target_luck: i32, skill_type: i32, skill_value: i32) -> i32 {
    let mut rate = crate::ai::linear_easing(25, 55, 1000, dexterity);
    if skill_type == 67 {
        rate = i32_of(rate as i64 + skill_value as i64);
    }
    i32_of(rate as i64 - crate::ai::linear_easing(5, 20, 1000, target_luck) as i64)
}

/// `combat_resolution.buff_turns`.
fn buff_turns(intelligence: i32) -> i32 {
    crate::ai::linear_easing(2, 5, 1000, intelligence)
}

/// `combat_skills.can_receive_status` with the controller's fixed arguments.
fn can_receive(battle: &KaBattle, caster: usize, target: usize, skill: &KaSkill) -> bool {
    let unit = &battle.units[target];
    let has_parameter = battle.has(unit.identity, SLOT_PARAMETER);
    let has_hp = unit.params.find(10).is_some();
    let stored_hp = unit.hp();
    let has_status = unit.board.contains(62);
    let monster = unit.human == 0;
    if !has_parameter || !has_hp || stored_hp <= 0 || has_status {
        return false;
    }
    // `main_world` is False in the special battle world, so boss immunity does not apply.
    if monster && unit.monster_type == 1 {
        return false;
    }
    let hostile = skill.flags & 0x20000 != 0;
    let caster_ally = battle.units[caster].ally();
    let target_ally = unit.ally();
    !hostile || caster_ally != target_ally
}

/// `combat_skills.apply_status`: one `buff_hit` draw, the status-slot write, and the `status_text`
/// event (`0` miss, `1` defense_down, `2` sleep). The per-occupant `status_apply` event is emitted
/// by `buff_cell`, exactly as `BuffEntitiesOnCell`'s caller does it.
fn apply_status(battle: &mut KaBattle, target: usize, skill: &KaSkill, rate: i32) -> bool {
    // `hits(accuracy())`: one buff_hit math draw, regardless of the outcome.
    let draw = battle.next_math(100);
    let target_id = battle.units[target].identity;
    if draw < rate {
        let turns = buff_turns(battle.units[target].param_value(18, 0));
        let board = &mut battle.units[target].board;
        board.set(62, skill.id as i64);
        board.set(63, turns as i64);
        board.set(64, 0);
        if skill.kind == 66 || skill.kind == 67 {
            let text = if skill.kind == 66 { 1 } else { 2 };
            battle.push_event(KA_EVENT_STATUS_TEXT, target_id, text, skill.id, 0, 0, 0);
        }
        true
    } else {
        battle.push_event(KA_EVENT_STATUS_TEXT, target_id, 0, skill.id, 0, 0, 0);
        false
    }
}

/// `SharedControllers.buff_cell`.
pub fn buff_cell(
    battle: &mut KaBattle,
    caster: usize,
    skill: &KaSkill,
    cell: (i32, i32),
) -> Result<(), i32> {
    let key = battle.cell_key([cell.0, cell.1]);
    let mut occupants = [0i32; KA_MAX_BUCKET_MEMBERS];
    let mut count = 0usize;
    if let Some(slot) =
        (0..battle.bucket_count as usize).find(|&slot| battle.buckets[slot].key == key)
    {
        let bucket = battle.buckets[slot];
        for position in 0..bucket.count as usize {
            let id = bucket.ids[position];
            if let Some(index) = battle.fighter_slot(id) {
                occupants[count] = index as i32;
                count += 1;
            }
        }
    }
    let caster_id = battle.units[caster].identity;
    for slot in 0..count {
        let target = occupants[slot] as usize;
        if !can_receive(battle, caster, target, skill) {
            continue;
        }
        let rate = buff_accuracy(
            battle.units[caster].param_value(19, 0),
            battle.units[target].param_value(16, 0),
            skill.kind,
            skill.value,
        );
        apply_status(battle, target, skill, rate);
    }
    // `buff_cell` emits `status_apply` for EVERY cell occupant, not only the accepted ones.
    for slot in 0..count {
        let target = occupants[slot] as usize;
        let applied = battle.units[target].board.get(62) == Some(skill.id as i64);
        let turns = battle.units[target].board.get_or(63, 0);
        battle.push_event(KA_EVENT_STATUS, caster_id, battle.units[target].identity, skill.id,
                          i32::from(applied), turns as i32, 0);
    }
    Ok(())
}

// ---------------------------------------------------------------------------------------------
// use_skill
// ---------------------------------------------------------------------------------------------

/// `combat_skill_use.use_fighter_skill`, with the controller's callbacks bound.
pub fn use_skill(
    battle: &mut KaBattle,
    caster: usize,
    command: &KaCommand,
    index: i32,
) -> Result<bool, i32> {
    let skill_id = command.skill;
    let target = command.target;
    let row = battle.row(skill_id).copied().ok_or(KA_ERR_UNKNOWN_SKILL_ROW)?;
    let target_option = if target >= 0 { Some(target) } else { None };
    let first = index == 0;
    if !crate::ai::eligible(battle, caster, &row, target_option, first) {
        // `SharedControllers.use` emits `release` with `used=False`; the event is not conditional on
        // the use succeeding.
        battle.push_event(KA_EVENT_RELEASE, battle.units[caster].identity, target, skill_id,
                          index, 0, row.id);
        return Ok(false);
    }
    if index == 0 {
        pay(battle, caster, &row);
        balloon(battle, caster, &row)?;
    }
    if row.kind == 1 || row.kind == 11 {
        let target_index = match target_option {
            Some(value) => match battle.fighter_slot(value) {
                Some(slot) => slot,
                None => return Err(KA_ERR_UNSUPPORTED_ROUTE),
            },
            _ => return Err(KA_ERR_UNSUPPORTED_ROUTE),
        };
        fire_skill_projectiles(battle, caster, target_index, &row)?;
    } else if row.category == 1 {
        let result = heal(battle, caster, target, &row);
        deliver_cures(battle, &[result])?;
        sound(battle, &row);
    } else if row.category != 0 {
        return Err(KA_ERR_UNSUPPORTED_ROUTE);
    } else if row.kind == 26 || row.kind == 27 {
        let cells = crate::ai::skill_cells(battle, caster, &row);
        for slot in 0..cells.len as usize {
            let cell = (cells.cells[slot].x, cells.cells[slot].y);
            hit_cell(battle, caster, &row, cell)?;
            let spec = cell_skill_effect_spec(&row, cell);
            create_effect(battle, &spec)?;
            sound(battle, &row);
        }
    } else if row.kind == 18 {
        // `use_fighter_skill` calls `effects['reflect']()`, which the controller's `use` dict does
        // not provide: the canonical Python raises here, so the kernel reports the same dead route.
        return Err(KA_ERR_UNSUPPORTED_ROUTE);
    } else if row.flags & 0x40000 != 0 {
        // `effects['cells']` is `SharedControllers.skill_cells`, which only produces cells for
        // types 26/27 - a generated-status row (type 66/67) yields none.
        let cells = crate::ai::skill_cells(battle, caster, &row);
        for slot in 0..cells.len as usize {
            let cell = (cells.cells[slot].x, cells.cells[slot].y);
            buff_cell(battle, caster, &row, cell)?;
            let spec = cell_skill_effect_spec(&row, cell);
            create_effect(battle, &spec)?;
            sound(battle, &row);
        }
    } else {
        let result = attack_result(battle, caster, target, Some(skill_id))?;
        deliver(battle, &[result])?;
        sound(battle, &row);
    }
    // `SharedControllers.use` closes with `release`, carrying the command index and the used flag.
    battle.push_event(KA_EVENT_RELEASE, battle.units[caster].identity, target, skill_id,
                      index, 1, row.id);
    Ok(true)
}

// ---------------------------------------------------------------------------------------------
// States 4, 6, 7, 8
// ---------------------------------------------------------------------------------------------

/// `combat_projectiles.parabola`.
pub fn parabola(height: i32, length: i32, frame: i32) -> i32 {
    if length == 0 {
        return 0;
    }
    let t = frame as f32 / length as f32;
    let four_t = t * 4.0;
    let value = (four_t - t * four_t) * height as f32;
    (value.trunc() as i64).clamp(i32::MIN as i64, i32::MAX as i64) as i32
}

/// `combat_states.update_attacking`. Returns `(gauge, optional next state)`.
pub fn update_attacking(battle: &mut KaBattle, index: usize) -> Result<(i32, Option<i32>), i32> {
    let frame = battle.units[index].frame();
    let gauge = battle.units[index].gauge();
    if frame == 11 {
        let stored = battle.units[index].long_board.get(16);
        let target = match stored {
            Some(value) => value as i32,
            None => return Ok((gauge, None)),
        };
        let projectile_weapon = battle.units[index].weapon_projectile_flag != 0;
        let weapon_type = battle.units[index].weapon_type;
        if !projectile_weapon {
            attack(battle, index, target, None)?;
        } else if weapon_type == 7 || weapon_type == 8 {
            let skill_id = if weapon_type == 8 { 1 } else { 2 };
            let row = battle.row(skill_id).copied().ok_or(KA_ERR_UNKNOWN_SKILL_ROW)?;
            let target_index = match battle.fighter_slot(target) {
                Some(value) => value,
                None => return Err(KA_ERR_UNSUPPORTED_ROUTE),
            };
            fire_skill_projectiles(battle, index, target_index, &row)?;
        }
        // `sound_roll()` = next_lib('normal_attack_update').
        battle.draw_lib_label(KA_LIB_NORMAL_ATTACK_UPDATE);
        return Ok((0, None));
    }
    if frame >= 20 {
        let state = crate::ai::decide(battle, index)?;
        return Ok((gauge, Some(state)));
    }
    Ok((gauge, None))
}

/// `combat_states.update_damaging`; `on_monster_defeated` is the controller's no-op.
pub fn update_damaging(battle: &mut KaBattle, index: usize) -> Result<(), i32> {
    let frame = battle.units[index].frame();
    let team = battle.units[index].board_team();
    let height = if team == 0 { 10 } else { -10 };
    let stored = battle.units[index].board.get_or(12, 0) as i32;
    let offset = i32_of(stored as i64 + parabola(height, 6, frame) as i64);
    battle.units[index].set_offset_z(offset as f32);
    if frame >= 7 {
        if battle.units[index].hp() > 0 {
            let state = crate::ai::decide(battle, index)?;
            crate::ai::change(battle, index, state)?;
        } else if battle.units[index].human != 0 {
            crate::ai::change(battle, index, 7)?;
        } else if battle.units[index].monster != 0 {
            // `has_enemy(u)` is `board[6] == 1`; `is_pvp()` is False. `on_monster_defeated` is the
            // controller's no-op lambda.
            if battle.units[index].board_team() == 1 {
                // No observable effect in the recovered controller.
            }
            crate::ai::change(battle, index, 8)?;
        }
    }
    Ok(())
}

/// `combat_states.update_knocking_down`.
pub fn update_knocking_down(battle: &mut KaBattle, index: usize) -> Result<(), i32> {
    let frame = battle.units[index].frame();
    if frame <= 20 {
        let value = i32_of(battle.units[index].direction() as i64 + 1);
        let direction = i32_of(value as i64 - trunc_div(value, 4) as i64 * 4);
        battle.units[index].set_direction(direction);
    } else if frame == 21 {
        crate::ai::animate(battle, index, 7);
    } else if frame >= 101 {
        crate::ai::change(battle, index, 8)?;
    }
    Ok(())
}

/// `combat_states.update_leaving`.
pub fn update_leaving(battle: &mut KaBattle, index: usize) {
    let value = i32_of(battle.units[index].direction() as i64 + 2);
    let direction = i32_of(value as i64 - trunc_div(value, 4) as i64 * 4);
    battle.units[index].set_direction(direction);
}

/// `SharedControllers.departure_effect`.
pub fn departure_effect(battle: &mut KaBattle, index: usize) -> Result<i32, i32> {
    let position = battle.units[index].body.position;
    let spec = departure_effect_spec(position);
    create_effect(battle, &spec)
}

/// `SharedControllers.knock` -> `combat_states.enter_knocking_down`.
pub fn knock(battle: &mut KaBattle, index: usize) -> Result<(), i32> {
    let team = battle.units[index].board_team();
    let mut roster = [0i32; KA_MAX_UNITS];
    let roster_len = battle.roster(team, &mut roster);
    let mut count = 0i32;
    for slot in 0..roster_len {
        let other = roster[slot] as usize;
        if other != index && battle.units[other].state() == 7 {
            count += 1;
        }
    }
    let back = if team == 0 { 1 } else { -1 };
    let start = battle.units[index].body.position;
    let end = KaVec3 {
        x: i32_of(-2 * 24) as f32,
        y: 0.0,
        z: i32_of(i32_of(back as i64 * i32_of(battle.row_offset as i64 + count as i64) as i64) as i64
            * 24) as f32,
    };
    battle.units[index].board.set(7, -1);
    battle.units[index].command_count = 0;
    body_flight(battle, index, 5, start, end);
    crate::ai::animate(battle, index, 28);
    departure_effect(battle, index)?;
    state_sound(battle, KA_LIB_KNOCKDOWN_SOUND, 18);
    Ok(())
}

/// `SharedControllers.leave` -> `combat_states.enter_leaving_special` plus the prize award.
pub fn leave(battle: &mut KaBattle, index: usize) -> Result<(), i32> {
    let team = battle.units[index].board_team();
    // Encounter telemetry: the first Leaving (state 8) entry for this own-team roster slot. This is
    // deliberately NOT the death: a fighter can leave with HP above zero, and the true HP -> 0 death
    // is recorded at the HP mutation in `deliver`. Recorded once; no phase reads it.
    if team == 0 {
        let mut roster = [0i32; KA_MAX_UNITS];
        let len = battle.roster(0, &mut roster);
        for slot in 0..len as usize {
            if roster[slot] as usize == index && battle.encounter_own_first_leaving_tick[slot] < 0 {
                battle.encounter_own_first_leaving_tick[slot] = battle.tick as i32;
            }
        }
    }
    if battle.progress_boss >= 0 && battle.units[index].identity == battle.progress_boss {
        // v3: an event-site Leaving entry for the rival leader; never the death.
        battle.encounter_boss_leavings += 1;
    }
    let grid = i32_of(battle.units[index].grid() as i64 + 100);
    let row = trunc_div(grid, 5);
    let start = battle.units[index].body.position;
    let end = KaVec3 {
        x: i32_of(i32_of(grid as i64 - row as i64 * 5) as i64 * 24) as f32,
        y: 0.0,
        z: i32_of(
            i32_of(
                battle.row_offset as i64
                    + if team == 0 { 1 } else { 0 }
                    + if team == 0 { row as i64 } else { -(row as i64) },
            ) as i64
                * 24,
        ) as f32,
    };
    battle.units[index].board.set(7, grid as i64);
    body_flight(battle, index, 10, start, end);
    departure_effect(battle, index)?;
    if team == 1 && battle.units[index].monster != 0 && battle.units[index].boss != 0 {
        // `add_special_prize` -> `SharedControllers.leave.award`.
        let identity = battle.units[index].identity;
        if battle.prize_present != 0 {
            let count = battle.prize_candidate_count as i32;
            let treasure = if count == 0 {
                None
            } else {
                let pick = battle.next_math(count);
                Some(battle.prize_candidates[pick as usize])
            };
            if let Some(treasure) = treasure {
                let hp = battle.units[index].hp();
                battle.push_event(KA_EVENT_PRIZE, identity, treasure, hp, 0, 0, 0);
                battle.prize_count += 1;
            }
        }
    }
    state_sound(battle, KA_LIB_LEAVING_SOUND, 18);
    Ok(())
}
