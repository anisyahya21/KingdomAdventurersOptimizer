//! Narrow native projection for the canonical enemy-only encounter revision.
//! The Python adapter validates scenarios, attests input data, names rows, and hashes canonical JSON.

#[rustfmt::skip]
#[path = "../../ka_kernel/src/rng.rs"]
mod canonical_rng;

use canonical_rng::KaRandom;
use std::collections::HashMap;
use std::slice;

#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct KaEncounterInput {
    pub id: i32,
    pub level_field: i32,
    pub boss_id: i32,
    pub follower_start: u32,
    pub follower_count: u32,
}

#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct KaFollowerInput {
    pub monster_id: i32,
    pub check_rate: i32,
}

#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct KaMonsterInput {
    pub id: i32,
    pub skill_id: i32,
    /// One `[a,b,c,d]` curve for each canonical parameter id 10,11,13,14,15,16,19.
    pub curves: [[i32; 4]; 7],
}

#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct KaEnemyRow {
    pub monster_id: i32,
    pub boss: u8,
    pub reserved: [u8; 3],
    pub skill_id: i32,
    pub parameters: [i32; 7],
}

pub struct Context {
    encounters: Vec<KaEncounterInput>,
    followers: Vec<KaFollowerInput>,
    monsters: Vec<KaMonsterInput>,
    encounter_index: HashMap<i32, usize>,
    monster_index: HashMap<i32, usize>,
    max_rows: u32,
}

fn i32_wrap(value: i64) -> i32 {
    value as i32
}

fn trunc_div(value: i32, divisor: i32) -> i32 {
    (value as i64 / divisor as i64) as i32
}

fn random_below(raw: i32, limit: i32) -> i32 {
    if limit == 0 {
        return 0;
    }
    let magnitude = if raw < 0 {
        i32_wrap(-(raw as i64))
    } else {
        raw
    };
    magnitude - trunc_div(magnitude, limit) * limit
}

fn monster_parameter(curve: &[i32; 4], level: i32) -> i32 {
    let (low, high, step, span) = if level < 1 {
        return curve[0];
    } else if level > 10_000 {
        return curve[3];
    } else if level <= 100 {
        (curve[0], curve[1], level - 1, 99)
    } else if level <= 1_000 {
        (curve[1], curve[2], level - 100, 900)
    } else {
        (curve[2], curve[3], level - 1_000, 9_000)
    };
    let delta = i32_wrap(high as i64 - low as i64);
    let scaled = i32_wrap(delta as i64 * step as i64);
    i32_wrap(low as i64 + trunc_div(scaled, span) as i64)
}

#[no_mangle]
pub extern "C" fn ka_revision_abi_version() -> u32 {
    1
}

/// Copy attested flat arrays into a read-only native context. Null/invalid inputs return null.
#[no_mangle]
pub unsafe extern "C" fn ka_encounter_ctx_open(
    encounters_ptr: *const KaEncounterInput,
    encounter_count: u32,
    followers_ptr: *const KaFollowerInput,
    follower_count: u32,
    monsters_ptr: *const KaMonsterInput,
    monster_count: u32,
) -> *mut Context {
    if encounters_ptr.is_null()
        || monsters_ptr.is_null()
        || (follower_count > 0 && followers_ptr.is_null())
        || encounter_count == 0
        || monster_count == 0
        || encounter_count > 10_000
        || follower_count > 1_000_000
        || monster_count > 1_000_000
    {
        return std::ptr::null_mut();
    }
    let encounters = slice::from_raw_parts(encounters_ptr, encounter_count as usize).to_vec();
    let followers = if follower_count == 0 {
        Vec::new()
    } else {
        slice::from_raw_parts(followers_ptr, follower_count as usize).to_vec()
    };
    let monsters = slice::from_raw_parts(monsters_ptr, monster_count as usize).to_vec();
    let mut encounter_index = HashMap::with_capacity(encounters.len());
    let mut monster_index = HashMap::with_capacity(monsters.len());
    let mut max_rows = 0u32;
    for (index, encounter) in encounters.iter().enumerate() {
        let end = match encounter
            .follower_start
            .checked_add(encounter.follower_count)
        {
            Some(value) if value <= follower_count => value,
            _ => return std::ptr::null_mut(),
        };
        if end < encounter.follower_start || encounter_index.insert(encounter.id, index).is_some() {
            return std::ptr::null_mut();
        }
        max_rows = max_rows.max(encounter.follower_count.saturating_add(1));
    }
    for (index, monster) in monsters.iter().enumerate() {
        if monster_index.insert(monster.id, index).is_some() {
            return std::ptr::null_mut();
        }
    }
    for encounter in &encounters {
        if !monster_index.contains_key(&encounter.boss_id) {
            return std::ptr::null_mut();
        }
        let start = encounter.follower_start as usize;
        let end = start + encounter.follower_count as usize;
        if followers[start..end].iter().any(|f| {
            !(0..=100).contains(&f.check_rate) || !monster_index.contains_key(&f.monster_id)
        }) {
            return std::ptr::null_mut();
        }
    }
    Box::into_raw(Box::new(Context {
        encounters,
        followers,
        monsters,
        encounter_index,
        monster_index,
        max_rows,
    }))
}

#[no_mangle]
pub unsafe extern "C" fn ka_encounter_ctx_close(context: *mut Context) {
    if !context.is_null() {
        drop(Box::from_raw(context));
    }
}

#[no_mangle]
pub unsafe extern "C" fn ka_encounter_ctx_max_rows(context: *const Context) -> u32 {
    context.as_ref().map_or(0, |ctx| ctx.max_rows)
}

/// Emit selected follower rows in constructor/incoming order, then the boss; no formation sorting.
/// Returns 0 on success, -1 for bad pointers/context/id, and -2 for insufficient output capacity.
#[no_mangle]
pub unsafe extern "C" fn ka_encounter_roster(
    context: *const Context,
    encounter_id: i32,
    defeat_count: i32,
    lib_seed: i32,
    output: *mut KaEnemyRow,
    output_capacity: u32,
    written: *mut u32,
    level_out: *mut i32,
    draws_out: *mut u32,
) -> i32 {
    if context.is_null()
        || output.is_null()
        || written.is_null()
        || level_out.is_null()
        || draws_out.is_null()
    {
        return -1;
    }
    let ctx = &*context;
    let encounter = match ctx.encounter_index.get(&encounter_id) {
        Some(index) => ctx.encounters[*index],
        None => return -1,
    };
    let required = encounter.follower_count.saturating_add(1);
    *written = required;
    if output_capacity < required {
        return -2;
    }
    let level = i32_wrap(encounter.level_field as i64 + trunc_div(defeat_count, 5) as i64);
    let start = encounter.follower_start as usize;
    let end = start + encounter.follower_count as usize;
    let followers = &ctx.followers[start..end];
    let mut rng = KaRandom::seeded(lib_seed);
    let mut index = 0usize;
    for follower in followers {
        let roll = random_below(rng.next_int(), 100);
        if roll < follower.check_rate {
            let monster_index = ctx.monster_index[&follower.monster_id];
            let monster = &ctx.monsters[monster_index];
            let mut params = [0i32; 7];
            for param in 0..7 {
                params[param] = monster_parameter(&monster.curves[param], level);
            }
            output.add(index).write(KaEnemyRow {
                monster_id: monster.id,
                boss: 0,
                reserved: [0; 3],
                skill_id: monster.skill_id,
                parameters: params,
            });
            index += 1;
        }
    }
    let boss_index = ctx.monster_index[&encounter.boss_id];
    let boss = &ctx.monsters[boss_index];
    let mut params = [0i32; 7];
    for param in 0..7 {
        params[param] = monster_parameter(&boss.curves[param], level);
    }
    output.add(index).write(KaEnemyRow {
        monster_id: boss.id,
        boss: 1,
        reserved: [0; 3],
        skill_id: boss.skill_id,
        parameters: params,
    });
    *written = index as u32 + 1;
    *level_out = level;
    *draws_out = encounter.follower_count;
    0
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn i32_wrapping_and_truncation_match_contract() {
        assert_eq!(i32_wrap(i32::MAX as i64 + 1), i32::MIN);
        assert_eq!(trunc_div(-7, 5), -1);
        assert_eq!(trunc_div(7, 5), 1);
    }
}
