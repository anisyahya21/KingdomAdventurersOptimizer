//! Native kernel adapter for the standalone optimizer.
//!
//! Imports genuine fresh snapshots, applies canonical initialization using the shared
//! native Rust engine, and executes the selected DLL. Unsupported captured worlds fail closed.

use ka_shared_engine::{ai, state::KaBattle};
use serde::Serialize;
use serde_json::{json, Map, Value};
use std::collections::HashMap;
use std::ffi::{c_char, c_void};
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::time::Instant;

type Handle = *mut c_void;
type CloneFn = unsafe extern "C" fn(*const c_void) -> Handle;
type SkipFn = unsafe extern "C" fn(Handle, u32);
type RunFn = unsafe extern "C" fn(Handle, u32, i32, *mut BattleReport) -> i32;
type FullTickFn = unsafe extern "C" fn(Handle) -> i32;
type ReportFn = unsafe extern "C" fn(*const c_void, i32, *mut BattleReport) -> i32;
type StatsFn = unsafe extern "C" fn(*const c_void, *mut u64) -> i32;
type ChecksumFn = unsafe extern "C" fn(*const c_void) -> u64;
type FreeFn = unsafe extern "C" fn(Handle);
type SizeFn = unsafe extern "C" fn() -> u32;
type EncounterReportFn = unsafe extern "C" fn(*const c_void, i32, *mut EncounterReport) -> i32;
type CreateFn = unsafe extern "C" fn() -> Handle;
type ImportFn = unsafe extern "C" fn(Handle, *const KaUnit, u32, u64) -> u32;
type ConfigFn = unsafe extern "C" fn(Handle, i32, i32, u8);
type IdentityFn = unsafe extern "C" fn(Handle, i32);
type CountersFn = unsafe extern "C" fn(Handle, u32, i32, i32);
type AddRowFn = unsafe extern "C" fn(Handle, *const Skill) -> u32;
type AddEffectFn = unsafe extern "C" fn(Handle, i32, i32) -> u32;
type SetPrizesFn = unsafe extern "C" fn(Handle, *const i32, u32);
type SetBasesFn = unsafe extern "C" fn(Handle, *const i32, u32) -> u32;
type SetAnimationFn = unsafe extern "C" fn(Handle, i32, i32, i32, i32) -> u32;
type SetSubsetFn = unsafe extern "C" fn(Handle, u32, u32, u32, i32, *const i32, *const u32);
type SetBucketFn = unsafe extern "C" fn(Handle, u32, i32, u32, *const i32) -> u32;
type SetRngFn = unsafe extern "C" fn(Handle, u32, *const i32, i32, i32, u32);
type SetControlFn = unsafe extern "C" fn(Handle, i32, i32, i32, i32, i32, i32, i32, i32, i32);
type SetScopeFn = unsafe extern "C" fn(Handle, u8);
type AddInputFn = unsafe extern "C" fn(Handle, i32, i32, i32, i32) -> u32;
type AddItemFn = unsafe extern "C" fn(Handle, i32, i32, u8, i32, i32, i32) -> u32;
type SetHerbFn = unsafe extern "C" fn(Handle, i32);
type SetMpWatchFn = unsafe extern "C" fn(Handle, *const i32, u32, i32) -> u32;

// The C layout below follows canonical ka_kernel state.rs / world.rs / params.rs. These are
// input-only mirrors, not a second simulation implementation.
const SLOT_COUNT: usize = 52;
const BOARD_CAP: usize = 48;
const MAX_PARAMS: usize = 16;
const MAX_EQUIPMENT: usize = 8;
const MAX_SKILLS: usize = 12;
const MAX_LEVELS: usize = 12;
const MAX_COMMANDS: usize = 1024;
const MAX_INVOKE: usize = 64;
const MAX_PATH: usize = 8;

#[repr(C)]
#[derive(Clone, Copy, Default)]
struct Vec3 {
    x: f32,
    y: f32,
    z: f32,
}
#[repr(C)]
#[derive(Clone, Copy, Default)]
struct Modifier {
    type_: i32,
    offset_x: f32,
    offset_y: f32,
    offset_z: f32,
    scale_x: f32,
    scale_y: f32,
    angle: i32,
    anchor: i32,
    frame: i32,
    duration: i32,
    destroy_on_finish: u8,
    looping: u8,
    alpha: i32,
}
#[repr(C)]
#[derive(Clone, Copy, Default)]
struct Effect {
    type_: i32,
    value1: i32,
    value2: i32,
    depth: u8,
    frame: i32,
    max_frame: i32,
    parent: i32,
    scale: i32,
}
#[repr(C)]
#[derive(Clone, Copy, Default)]
struct Projectile {
    start: Vec3,
    end: Vec3,
    speed: i32,
    height: i32,
    frame: i32,
    length: i32,
    owner: i32,
}
#[repr(C)]
#[derive(Clone, Copy)]
struct Entity {
    id: i32,
    destroyed: u8,
    has: [u8; SLOT_COUNT],
    position: Vec3,
    offset: Vec3,
    parent: i32,
    speed: Vec3,
    seb: [i32; 4],
    depth: [i32; 2],
    cell: [i32; 2],
    image: [i32; 6],
    animation: [i32; 2],
    direction: i32,
    modifier: Modifier,
    effect: Effect,
    projectile: Projectile,
    attack: i32,
    garbage: i32,
}
impl Default for Entity {
    fn default() -> Self {
        Self {
            id: 0,
            destroyed: 0,
            has: [0; SLOT_COUNT],
            position: Vec3::default(),
            offset: Vec3::default(),
            parent: -1,
            speed: Vec3::default(),
            seb: [0; 4],
            depth: [0; 2],
            cell: [0; 2],
            image: [-1; 6],
            animation: [1, -1],
            direction: 0,
            modifier: Modifier::default(),
            effect: Effect::default(),
            projectile: Projectile::default(),
            attack: 0,
            garbage: 0,
        }
    }
}
#[repr(C)]
#[derive(Clone, Copy, Default)]
struct Param {
    id: i32,
    raw_value: i32,
    extra_value: i32,
    raw_max: i32,
    extra_max: i32,
    training_level: i32,
}
#[repr(C)]
#[derive(Clone, Copy)]
struct Equip {
    level: i32,
    pvp_level: i32,
    affinity: i32,
    pair_count: i32,
    pairs: [[i32; 2]; 16],
    present: [u8; 16],
}
impl Default for Equip {
    fn default() -> Self {
        Self {
            level: 0,
            pvp_level: 0,
            affinity: 0,
            pair_count: 0,
            pairs: [[0; 2]; 16],
            present: [0; 16],
        }
    }
}
#[repr(C)]
#[derive(Clone, Copy)]
struct Params {
    count: u32,
    rows: [Param; MAX_PARAMS],
    equipment_count: u32,
    equipment: [Equip; MAX_EQUIPMENT],
}
impl Default for Params {
    fn default() -> Self {
        Self {
            count: 0,
            rows: [Param::default(); MAX_PARAMS],
            equipment_count: 0,
            equipment: [Equip::default(); MAX_EQUIPMENT],
        }
    }
}
#[repr(C)]
#[derive(Clone, Copy, Default)]
struct Entry {
    key: i32,
    value: i64,
}
#[repr(C)]
#[derive(Clone, Copy)]
struct Board {
    len: u32,
    entries: [Entry; BOARD_CAP],
    positions: [u8; 128],
}
impl Default for Board {
    fn default() -> Self {
        Self {
            len: 0,
            entries: [Entry::default(); BOARD_CAP],
            positions: [0; 128],
        }
    }
}
#[repr(C)]
#[derive(Clone, Copy, Default)]
struct Skill {
    id: i32,
    category: i32,
    kind: i32,
    flags: i32,
    min_mp: i32,
    max_mp: i32,
    required_equip_type: i32,
    shooting_range: i32,
    range: i32,
    count: i32,
    motion: i32,
    value: i32,
    seb: i32,
    img: i32,
    impact_img: i32,
    impact_seb: i32,
}
#[repr(C)]
#[derive(Clone, Copy, Default)]
struct Command {
    opcode: i32,
    target: i32,
    skill: i32,
    tick: i32,
    duration: i32,
    use_index: i32,
}
#[repr(C)]
#[derive(Clone, Copy, Default)]
struct Invoke {
    skill: i32,
    remaining: i32,
}
#[repr(C)]
#[derive(Clone, Copy, Default)]
struct Point {
    x: i32,
    y: i32,
}
#[repr(C)]
#[derive(Clone, Copy)]
struct KaUnit {
    present: u8,
    team: u8,
    human: u8,
    monster: u8,
    flags: i32,
    id: i32,
    identity: i32,
    body: Entity,
    params: Params,
    weapon_type: i32,
    weapon_shooting_range: i32,
    weapon_motion: i32,
    weapon_projectile_flag: i32,
    boss: u8,
    monster_type: i32,
    special_human: u8,
    monster_size: i32,
    human_flag: i32,
    board: Board,
    long_board: Board,
    skill_ids: [i32; MAX_SKILLS],
    skill_count: u32,
    levels: [i32; MAX_LEVELS],
    level_count: u32,
    invoking: [Invoke; MAX_INVOKE],
    invoking_count: u32,
    commands: [Command; MAX_COMMANDS],
    command_count: u32,
    path: [Point; MAX_PATH],
    path_count: u32,
}
impl Default for KaUnit {
    fn default() -> Self {
        Self {
            present: 0,
            team: 0,
            human: 0,
            monster: 0,
            flags: 0,
            id: 0,
            identity: 0,
            body: Entity::default(),
            params: Params::default(),
            weapon_type: 0,
            weapon_shooting_range: 1,
            weapon_motion: 3,
            weapon_projectile_flag: 0,
            boss: 0,
            monster_type: 0,
            special_human: 0,
            monster_size: 0,
            human_flag: 0,
            board: Board::default(),
            long_board: Board::default(),
            skill_ids: [0; MAX_SKILLS],
            skill_count: 0,
            levels: [0; MAX_LEVELS],
            level_count: 0,
            invoking: [Invoke::default(); MAX_INVOKE],
            invoking_count: 0,
            commands: [Command::default(); MAX_COMMANDS],
            command_count: 0,
            path: [Point::default(); MAX_PATH],
            path_count: 0,
        }
    }
}

#[link(name = "kernel32")]
unsafe extern "system" {
    fn LoadLibraryW(name: *const u16) -> Handle;
    fn GetProcAddress(module: Handle, name: *const c_char) -> *mut c_void;
    fn FreeLibrary(module: Handle) -> i32;
}

#[derive(Clone, Copy, Default, Serialize, PartialEq)]
#[repr(C)]
struct BattleReport {
    status: i32,
    ticks: i32,
    verdict: i32,
    verdict_tick: i32,
    battle_state: i32,
    battle_frame: i32,
    finish_policy: i32,
    ending_gate_tick: i32,
    ending_confirmed: i32,
    ending_counter: i32,
    prize_callbacks: i32,
    pre_verdict_prize_callbacks: i32,
    unresolved_commands: i32,
    pending_projectiles: i32,
    active_damage_or_leaving: i32,
    math_draws: i32,
    lib_draws: i32,
    certificate_held: i32,
    certificate_frame: i32,
    certificate_pending: i32,
    late_hold_frame: i32,
    pending_at_verdict: i32,
    pending_final: i32,
    post_certificate_delta: i32,
    observations: i32,
    verdict_observations: i32,
    clauses: [i32; 4],
    boss_hp: i32,
    boss_state: i32,
    stored_target_holders: i32,
    queued_command_holders: i32,
    scope_allowed: i32,
    heals: i32,
    attack_attempts: i32,
    survivors: i32,
    own_hp: i64,
    own_hp_max: i64,
    own_present: i32,
    resource_uses: i32,
    progress_boss: i32,
    progress_boss_death_tick: i32,
    progress_boss_leaving_tick: i32,
    progress_stored_at_death: i32,
    progress_stored_targeting_boss_at_death: i32,
    progress_stored_target_holders_at_death: i32,
    progress_commands_targeting_boss: i32,
    progress_commands_targeting_boss_released: i32,
    progress_max_stored: i32,
    progress_max_targeting_boss: i32,
    progress_target_holders_peak: i32,
    progress_post_death_reentries: i32,
    progress_post_death_leavings: i32,
    progress_post_death_prizes: i32,
    progress_released_after_death: i32,
    progress_released_after_death_targeting_boss: i32,
    progress_first_release_after_death: i32,
    progress_last_release_after_death: i32,
    mp_watch_count: i32,
    mp_identity: [i32; 2],
    mp_min: [i32; 2],
    mp_min_percent: [i32; 2],
    mp_low: [i32; 2],
    mp_first_low_tick: [i32; 2],
    mp_first_low_phase: [i32; 2],
    mp_zero: [i32; 2],
    herb_stock_start: i32,
    herb_stock_remaining: i32,
    herb_max_uses: i32,
    herb_use_count: i32,
    herb_log_count: i32,
    herb_use_tick: [i32; 16],
    herb_use_phase: [i32; 16],
    herb_use_source: [i32; 16],
    herb_use_ok: [i32; 16],
}

#[derive(Clone, Copy, Default, Serialize, PartialEq)]
#[repr(C)]
struct EncounterReport {
    version: i32,
    own_count: i32,
    own_identity: [i32; 32],
    own_first_death_tick: [i32; 32],
    own_first_leaving_tick: [i32; 32],
    own_survived: [i32; 32],
    enemy_resolved_attacks: i32,
    enemy_hits: i32,
    enemy_misses: i32,
    enemy_rolls: i32,
    enemy_roll_hits: i32,
    enemy_roll_misses: i32,
    counter_checks: i32,
    counter_enqueues: i32,
    boss_postdeath_attempts: i32,
    boss_postdeath_lands: i32,
    boss_death_tick: i32,
    boss_reentries: i32,
    boss_leavings: i32,
    boss_postdeath_gap_count: i32,
    boss_postdeath_gap_min: i32,
    boss_postdeath_gap_max: i32,
    boss_postdeath_gap_sum: i32,
    future_hits_at_death: i32,
    future_hits_include_executing: i32,
    boss_access_first_command: i32,
    boss_access_first_attempt: i32,
    targetable_ticks: i32,
    using_skill_ticks: i32,
    boss_damaging_resets: i32,
    item_uses_ok: i32,
    finish_dispatched_chests: i32,
}

#[derive(Clone, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
struct NativeTraceUnitState {
    slot: usize,
    identity: i32,
    fighter_id: i32,
    team: i32,
    side: &'static str,
    roster_index: usize,
    source_domain: Option<&'static str>,
    source_index: Option<usize>,
    source_kind: Option<&'static str>,
    raw_intent_own_index: Option<usize>,
    #[serde(skip_serializing_if = "Option::is_none")]
    effective_parameters: Option<Map<String, Value>>,
    present: bool,
    human: bool,
    monster: bool,
    boss: bool,
    weapon_type: i32,
    weapon_motion: i32,
    skill_ids: Vec<i32>,
    hp: i32,
    hp_max: i32,
    mp: i32,
    mp_max: i32,
    state: i32,
    frame: i32,
    grid: i32,
    cell: [i32; 2],
    command_count: usize,
    destroyed: bool,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct NativeTraceEvent {
    seq: u64,
    tick: i32,
    kind_code: i32,
    kind_name: Option<&'static str>,
    unit: i32,
    a: i32,
    b: i32,
    c: i32,
    d: i32,
    e: i32,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct NativeTraceStateDelta {
    tick: i32,
    units: Vec<NativeTraceUnitDelta>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct NativeTraceUnitDelta {
    slot: usize,
    identity: i32,
    #[serde(skip_serializing_if = "Option::is_none")]
    hp: Option<i32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    hp_max: Option<i32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    mp: Option<i32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    mp_max: Option<i32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    state: Option<i32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    frame: Option<i32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    grid: Option<i32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    cell: Option<[i32; 2]>,
    #[serde(skip_serializing_if = "Option::is_none")]
    command_count: Option<usize>,
    #[serde(skip_serializing_if = "Option::is_none")]
    present: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    destroyed: Option<bool>,
}

const MAX_NATIVE_TRACE_TICKS: u32 = 25_000;
const MAX_NATIVE_TRACE_EVENTS: usize = 250_000;
const MAX_NATIVE_TRACE_STATE_ROWS: usize = 250_000;
// Reserve two MiB for the surrounding native report fields when it embeds the trace.
const MAX_NATIVE_TRACE_SERIALIZED_BYTES: usize = 32 * 1024 * 1024 - 2 * 1024 * 1024;

#[derive(Clone, Copy, Default)]
struct NativeTraceSource {
    domain: Option<&'static str>,
    index: Option<usize>,
    kind: Option<&'static str>,
    raw_intent_own_index: Option<usize>,
}

#[derive(Clone, Copy)]
struct Kernel {
    clone: CloneFn,
    skip: SkipFn,
    run: RunFn,
    full_tick: Option<FullTickFn>,
    report: ReportFn,
    stats: StatsFn,
    checksum: ChecksumFn,
    free: FreeFn,
    encounter_report: Option<EncounterReportFn>,
    battle_size: u32,
    report_size: u32,
    unit_size: u32,
    skill_size: u32,
    entity_size: u32,
    encounter_size: Option<u32>,
    encounter_version: Option<u32>,
    create: CreateFn,
    import: ImportFn,
    config: ConfigFn,
    identity: IdentityFn,
    counters: CountersFn,
    add_row: AddRowFn,
    add_effect: AddEffectFn,
    set_prizes: SetPrizesFn,
    set_bases: SetBasesFn,
    set_animation: SetAnimationFn,
    set_subset: SetSubsetFn,
    set_bucket: SetBucketFn,
    set_rng: SetRngFn,
    set_control: SetControlFn,
    set_scope: SetScopeFn,
    add_input: AddInputFn,
    add_item: AddItemFn,
    set_herb: SetHerbFn,
    set_mp_watch: SetMpWatchFn,
}

pub struct NativeEngine {
    module: Handle,
    kernel: Kernel,
    dll_sha256: String,
    template: Option<(String, Handle)>,
}
struct NativeGuard {
    ptr: Handle,
    free: FreeFn,
}
impl Drop for NativeGuard {
    fn drop(&mut self) {
        if !self.ptr.is_null() {
            unsafe { (self.free)(self.ptr) }
        }
    }
}

unsafe fn symbol<T: Copy>(module: Handle, name: &'static [u8]) -> Result<T, String> {
    let p = GetProcAddress(module, name.as_ptr() as *const c_char);
    if p.is_null() {
        return Err(format!(
            "missing native export {}",
            String::from_utf8_lossy(&name[..name.len() - 1])
        ));
    }
    Ok(std::mem::transmute_copy(&p))
}
unsafe fn optional<T: Copy>(module: Handle, name: &'static [u8]) -> Option<T> {
    let p = GetProcAddress(module, name.as_ptr() as *const c_char);
    if p.is_null() {
        None
    } else {
        Some(std::mem::transmute_copy(&p))
    }
}

fn obj<'a>(v: &'a Value, key: &str) -> Result<&'a Value, String> {
    v.get(key).ok_or_else(|| format!("snapshot missing {key}"))
}
fn int(v: &Value, name: &str) -> Result<i32, String> {
    let n = v
        .as_i64()
        .ok_or_else(|| format!("{name} must be a signed integer"))?;
    i32::try_from(n).map_err(|_| format!("{name} outside i32"))
}
fn uint(v: &Value, name: &str) -> Result<u32, String> {
    let n = v
        .as_u64()
        .ok_or_else(|| format!("{name} must be a nonnegative integer"))?;
    u32::try_from(n).map_err(|_| format!("{name} outside u32"))
}
fn boolean(v: &Value, name: &str) -> Result<u8, String> {
    v.as_bool()
        .map(u8::from)
        .or_else(|| v.as_i64().filter(|n| *n == 0 || *n == 1).map(|n| n as u8))
        .ok_or_else(|| format!("{name} must be boolean/0/1"))
}
fn arr<'a>(v: &'a Value, name: &str) -> Result<&'a Vec<Value>, String> {
    v.as_array()
        .ok_or_else(|| format!("{name} must be an array"))
}
fn optional_i32(v: &Value, field: &str, fallback: i32) -> Result<i32, String> {
    match v.get(field) {
        None => Ok(fallback),
        Some(x) if x.is_null() => Ok(fallback),
        Some(x) => int(x, field),
    }
}
fn f32_at(a: &[Value], i: usize, n: &str) -> Result<f32, String> {
    let x = a.get(i).ok_or_else(|| format!("{n} missing index {i}"))?;
    let y = x
        .as_f64()
        .ok_or_else(|| format!("{n}[{i}] must be numeric"))?;
    if !y.is_finite() || y.abs() > f32::MAX as f64 {
        return Err(format!("{n}[{i}] outside finite f32"));
    }
    Ok(y as f32)
}
fn array_i32<const N: usize>(v: &Value, n: &str) -> Result<[i32; N], String> {
    let a = arr(v, n)?;
    if a.len() != N {
        return Err(format!("{n} length {} != {N}", a.len()));
    }
    let mut out = [0; N];
    for (i, x) in a.iter().enumerate() {
        out[i] = int(x, n)?
    }
    Ok(out)
}

fn fill_entity(record: &Value, entity: &mut Entity) -> Result<(), String> {
    entity.id = optional_i32(record, "id", 0)?;
    entity.destroyed = boolean(obj(record, "destroyed")?, "destroyed")?;
    entity.parent = -1;
    for slot in arr(obj(record, "present_slots")?, "present_slots")? {
        let i = int(slot, "present_slots item")?;
        if !(0..SLOT_COUNT as i32).contains(&i) {
            return Err(format!("component slot {i} out of range"));
        }
        entity.has[i as usize] = 1;
    }
    let c = obj(record, "components")?;
    if let Some(p) = c.get("position").filter(|x| !x.is_null()) {
        let a = arr(p, "components.position")?;
        if a.len() != 7 {
            return Err("position must have 7 entries".into());
        }
        entity.position = Vec3 {
            x: f32_at(a, 0, "position")?,
            y: f32_at(a, 1, "position")?,
            z: f32_at(a, 2, "position")?,
        };
        entity.offset = Vec3 {
            x: f32_at(a, 3, "position")?,
            y: f32_at(a, 4, "position")?,
            z: f32_at(a, 5, "position")?,
        };
        entity.parent = if a[6].is_null() {
            -1
        } else {
            int(&a[6], "position.parent")?
        };
    }
    if let Some(x) = c.get("speed").filter(|x| !x.is_null()) {
        let a = arr(x, "components.speed")?;
        entity.speed = Vec3 {
            x: f32_at(a, 0, "speed")?,
            y: f32_at(a, 1, "speed")?,
            z: f32_at(a, 2, "speed")?,
        };
    }
    macro_rules! ints {
        ($key:literal,$field:ident,$len:expr) => {
            if let Some(x) = c.get($key).filter(|x| !x.is_null()) {
                entity.$field = array_i32::<$len>(x, concat!("components.", $key))?;
            }
        };
    }
    ints!("seb", seb, 4);
    ints!("depth", depth, 2);
    ints!("cell", cell, 2);
    ints!("image", image, 6);
    ints!("animation", animation, 2);
    if let Some(x) = c.get("direction").filter(|x| !x.is_null()) {
        entity.direction = int(x, "direction")?;
    }
    if let Some(x) = c.get("garbage").filter(|x| !x.is_null()) {
        entity.garbage = int(x, "garbage")?;
    }
    if let Some(x) = c.get("attack").filter(|x| !x.is_null()) {
        entity.attack = int(x, "attack")?;
    }
    if let Some(x) = c.get("modifier").filter(|x| !x.is_null()) {
        let m = x;
        entity.modifier.type_ = int(obj(m, "type")?, "modifier.type")?;
        entity.modifier.offset_x = num(m, "offset_x")?;
        entity.modifier.offset_y = num(m, "offset_y")?;
        entity.modifier.offset_z = num(m, "offset_z")?;
        entity.modifier.scale_x = num(m, "scale_x")?;
        entity.modifier.scale_y = num(m, "scale_y")?;
        entity.modifier.angle = int(obj(m, "angle")?, "modifier.angle")?;
        entity.modifier.anchor = int(obj(m, "anchor")?, "modifier.anchor")?;
        entity.modifier.frame = int(obj(m, "frame")?, "modifier.frame")?;
        entity.modifier.duration = int(obj(m, "duration")?, "modifier.duration")?;
        entity.modifier.destroy_on_finish =
            boolean(obj(m, "destroy_on_finish")?, "modifier.destroy_on_finish")?;
        entity.modifier.looping = boolean(obj(m, "loop")?, "modifier.loop")?;
        entity.modifier.alpha = int(obj(m, "alpha")?, "modifier.alpha")?;
    }
    if let Some(x) = c.get("effect").filter(|x| !x.is_null()) {
        let e = x;
        entity.effect.type_ = int(obj(e, "type")?, "effect.type")?;
        entity.effect.value1 = int(obj(e, "value1")?, "effect.value1")?;
        entity.effect.value2 = int(obj(e, "value2")?, "effect.value2")?;
        entity.effect.depth = boolean(obj(e, "depth")?, "effect.depth")?;
        entity.effect.frame = int(obj(e, "frame")?, "effect.frame")?;
        entity.effect.max_frame = int(obj(e, "max_frame")?, "effect.max_frame")?;
        entity.effect.parent = optional_i32(e, "parent", -1)?;
        entity.effect.scale = int(obj(e, "scale")?, "effect.scale")?;
    }
    if let Some(x) = c.get("projectile").filter(|x| !x.is_null()) {
        let p = x;
        for (k, out) in [
            ("start", &mut entity.projectile.start),
            ("end", &mut entity.projectile.end),
        ] {
            let a = arr(obj(p, k)?, k)?;
            *out = Vec3 {
                x: f32_at(a, 0, k)?,
                y: f32_at(a, 1, k)?,
                z: f32_at(a, 2, k)?,
            };
        }
        entity.projectile.speed = int(obj(p, "speed")?, "projectile.speed")?;
        entity.projectile.height = int(obj(p, "height")?, "projectile.height")?;
        entity.projectile.frame = int(obj(p, "frame")?, "projectile.frame")?;
        entity.projectile.length = int(obj(p, "length")?, "projectile.length")?;
        entity.projectile.owner = int(obj(p, "owner")?, "projectile.owner")?;
    }
    Ok(())
}
fn num(v: &Value, k: &str) -> Result<f32, String> {
    let x = obj(v, k)?
        .as_f64()
        .ok_or_else(|| format!("{k} must be numeric"))?;
    if !x.is_finite() || x.abs() > f32::MAX as f64 {
        return Err(format!("{k} outside finite f32"));
    }
    Ok(x as f32)
}
fn board(v: &Value, name: &str) -> Result<Board, String> {
    let mut b = Board::default();
    let m = v
        .as_object()
        .ok_or_else(|| format!("{name} must be an object"))?;
    if m.len() > BOARD_CAP {
        return Err(format!("{name} exceeds {BOARD_CAP} keys"));
    }
    let mut pairs = Vec::with_capacity(m.len());
    for (k, x) in m {
        let key = k
            .parse::<i32>()
            .map_err(|_| format!("{name} key {k:?} is not i32"))?;
        let val = x
            .as_i64()
            .ok_or_else(|| format!("{name}[{k}] must be i64"))?;
        pairs.push((key, val));
    }
    pairs.sort_by_key(|x| x.0);
    b.len = pairs.len() as u32;
    for (i, (key, value)) in pairs.into_iter().enumerate() {
        b.entries[i] = Entry { key, value };
        if (0..128).contains(&key) {
            b.positions[key as usize] = (i + 1) as u8;
        }
    }
    Ok(b)
}
fn unit(v: &Value) -> Result<KaUnit, String> {
    let mut u = KaUnit::default();
    u.present = boolean(obj(v, "present")?, "present")?;
    u.team = uint(obj(v, "team")?, "team")?
        .try_into()
        .map_err(|_| "team outside u8")?;
    u.human = boolean(obj(v, "human")?, "human")?;
    u.monster = boolean(obj(v, "monster")?, "monster")?;
    u.flags = int(obj(v, "flags")?, "flags")?;
    u.id = int(obj(v, "id")?, "id")?;
    u.identity = int(obj(v, "identity")?, "identity")?;
    u.body.id = u.identity;
    fill_entity(v, &mut u.body)?;
    let weapon = obj(v, "weapon")?;
    u.weapon_type = int(obj(weapon, "type")?, "weapon.type")?;
    u.weapon_shooting_range = int(obj(weapon, "shootingRange")?, "weapon.shootingRange")?;
    u.weapon_motion = int(obj(weapon, "motion")?, "weapon.motion")?;
    u.weapon_projectile_flag = int(obj(weapon, "projectileFlag")?, "weapon.projectileFlag")?;
    u.boss = v
        .get("boss")
        .map(|x| boolean(x, "boss"))
        .transpose()?
        .unwrap_or(0);
    u.monster_type = optional_i32(v, "monsterType", 0)?;
    u.special_human = v
        .get("specialHuman")
        .map(|x| boolean(x, "specialHuman"))
        .transpose()?
        .unwrap_or(0);
    u.monster_size = optional_i32(v, "monsterSize", 0)?;
    u.human_flag = optional_i32(v, "human_flag", 0)?;
    u.board = board(obj(v, "board")?, "board")?;
    u.long_board = board(obj(v, "long_board")?, "long_board")?;
    let params = obj(v, "parameters")?
        .as_object()
        .ok_or("parameters must be an object")?;
    if params.len() > MAX_PARAMS {
        return Err("unit parameter count exceeds ABI capacity".into());
    }
    let mut ps = Vec::new();
    for (k, p) in params {
        let id = k.parse::<i32>().map_err(|_| "parameter key invalid")?;
        ps.push(Param {
            id,
            raw_value: int(obj(p, "rawValue")?, "rawValue")?,
            extra_value: int(obj(p, "extraValue")?, "extraValue")?,
            raw_max: int(obj(p, "rawMax")?, "rawMax")?,
            extra_max: int(obj(p, "extraMax")?, "extraMax")?,
            training_level: int(obj(p, "trainingLevel")?, "trainingLevel")?,
        });
    }
    ps.sort_by_key(|p| p.id);
    u.params.count = ps.len() as u32;
    for (i, p) in ps.into_iter().enumerate() {
        u.params.rows[i] = p;
    }
    let equipment = arr(obj(v, "equipment")?, "equipment")?;
    if equipment.len() > MAX_EQUIPMENT {
        return Err("equipment rows exceed ABI capacity".into());
    }
    u.params.equipment_count = equipment.len() as u32;
    for (i, e) in equipment.iter().enumerate() {
        let t = &mut u.params.equipment[i];
        t.level = int(obj(e, "level")?, "equipment.level")?;
        t.pvp_level = optional_i32(e, "pvpLevel", 0)?;
        t.affinity = optional_i32(e, "affinity", 1)?;
        let pairs = arr(obj(e, "parameters")?, "equipment.parameters")?;
        if pairs.len() > 16 {
            return Err("equipment parameter pairs exceed ABI capacity".into());
        }
        t.pair_count = pairs.len() as i32;
        for (j, p) in pairs.iter().enumerate() {
            if p.is_null() {
                continue;
            }
            let pair = arr(p, "equipment pair")?;
            if pair.len() != 2 {
                return Err("equipment pair must have two entries".into());
            }
            t.present[j] = 1;
            t.pairs[j] = [
                int(&pair[0], "equipment base")?,
                int(&pair[1], "equipment growth")?,
            ];
        }
    }
    for (field, out, max) in [("skills", 0, MAX_SKILLS), ("levels", 1, MAX_LEVELS)] {
        let a = arr(obj(v, field)?, field)?;
        if a.len() > max {
            return Err(format!("{field} exceed ABI capacity"));
        }
        if out == 0 {
            u.skill_count = a.len() as u32;
            for (i, x) in a.iter().enumerate() {
                u.skill_ids[i] = int(x, "skill id")?
            }
        } else {
            u.level_count = a.len() as u32;
            for (i, x) in a.iter().enumerate() {
                u.levels[i] = int(x, "skill level")?
            }
        }
    }
    let invoking = arr(obj(v, "invoking")?, "invoking")?;
    if invoking.len() > MAX_INVOKE {
        return Err("invoking entries exceed ABI capacity".into());
    }
    u.invoking_count = invoking.len() as u32;
    for (i, x) in invoking.iter().enumerate() {
        let a = arr(x, "invoking row")?;
        if a.len() != 2 {
            return Err("invoking entry must be pair".into());
        }
        u.invoking[i] = Invoke {
            skill: int(&a[0], "invoking skill")?,
            remaining: int(&a[1], "invoking remaining")?,
        };
    }
    let commands = arr(obj(v, "commands")?, "commands")?;
    if commands.len() > MAX_COMMANDS {
        return Err("commands exceed ABI capacity".into());
    }
    u.command_count = commands.len() as u32;
    for (i, c) in commands.iter().enumerate() {
        u.commands[i] = Command {
            opcode: int(obj(c, "opcode")?, "command.opcode")?,
            target: int(obj(c, "target")?, "command.target")?,
            skill: int(obj(c, "skill")?, "command.skill")?,
            tick: int(obj(c, "tick")?, "command.tick")?,
            duration: int(obj(c, "duration")?, "command.duration")?,
            use_index: int(obj(c, "useIndex")?, "command.useIndex")?,
        };
    }
    let path = arr(obj(v, "path")?, "path")?;
    if path.len() > MAX_PATH {
        return Err("path entries exceed ABI capacity".into());
    }
    u.path_count = path.len() as u32;
    for (i, p) in path.iter().enumerate() {
        let a = arr(p, "path point")?;
        if a.len() != 2 {
            return Err("path point must be pair".into());
        }
        u.path[i] = Point {
            x: int(&a[0], "path.x")?,
            y: int(&a[1], "path.y")?,
        };
    }
    Ok(u)
}

fn fjson(x: f32) -> Value {
    if x.is_finite() {
        json!(x)
    } else {
        Value::Null
    }
}
fn entity_json(e: &ka_shared_engine::world::KaEntity) -> Value {
    use ka_shared_engine::world::*;
    let mut c = serde_json::Map::new();
    for k in [
        "position",
        "speed",
        "seb",
        "depth",
        "cell",
        "image",
        "animation",
        "direction",
        "modifier",
        "garbage",
        "effect",
        "projectile",
        "attack",
    ] {
        c.insert(k.into(), Value::Null);
    }
    if e.has[SLOT_POSITION] != 0 {
        c.insert(
            "position".into(),
            json!([
                fjson(e.position.x),
                fjson(e.position.y),
                fjson(e.position.z),
                fjson(e.offset.x),
                fjson(e.offset.y),
                fjson(e.offset.z),
                if e.parent < 0 {
                    Value::Null
                } else {
                    json!(e.parent)
                }
            ]),
        );
    }
    if e.has[SLOT_SPEED] != 0 {
        c.insert(
            "speed".into(),
            json!([fjson(e.speed.x), fjson(e.speed.y), fjson(e.speed.z)]),
        );
    }
    if e.has[SLOT_SEB] != 0 {
        c.insert("seb".into(), json!(e.seb));
    }
    if e.has[SLOT_DEPTH] != 0 {
        c.insert("depth".into(), json!(e.depth));
    }
    if e.has[SLOT_CELL] != 0 {
        c.insert("cell".into(), json!(e.cell));
    }
    if e.has[SLOT_IMAGE] != 0 {
        c.insert("image".into(), json!(e.image));
    }
    if e.has[SLOT_ANIMATION] != 0 {
        c.insert("animation".into(), json!(e.animation));
    }
    if e.has[SLOT_DIRECTION] != 0 {
        c.insert("direction".into(), json!(e.direction));
    }
    if e.has[SLOT_MODIFY_ANIMATION] != 0 {
        let m = &e.modifier;
        c.insert("modifier".into(),json!({"type":m.type_,"offset_x":m.offset_x,"offset_y":m.offset_y,"offset_z":m.offset_z,"scale_x":m.scale_x,"scale_y":m.scale_y,"angle":m.angle,"anchor":m.anchor,"frame":m.frame,"duration":m.duration,"destroy_on_finish":m.destroy_on_finish!=0,"loop":m.looping!=0,"alpha":m.alpha}));
    }
    if e.has[SLOT_GARBAGE] != 0 {
        c.insert("garbage".into(), json!(e.garbage));
    }
    if e.has[SLOT_EFFECT] != 0 {
        let x = &e.effect;
        c.insert("effect".into(),json!({"type":x.type_,"value1":x.value1,"value2":x.value2,"depth":x.depth!=0,"frame":x.frame,"max_frame":x.max_frame,"parent":if x.parent<0{Value::Null}else{json!(x.parent)},"scale":x.scale}));
    }
    if e.has[SLOT_PROJECTILE] != 0 {
        let x = &e.projectile;
        c.insert("projectile".into(),json!({"start":[fjson(x.start.x),fjson(x.start.y),fjson(x.start.z)],"end":[fjson(x.end.x),fjson(x.end.y),fjson(x.end.z)],"speed":x.speed,"height":x.height,"frame":x.frame,"length":x.length,"owner":x.owner}));
    }
    if e.has[SLOT_ATTACK] != 0 {
        c.insert("attack".into(), json!(e.attack));
    }
    Value::Object(c)
}
fn board_json(b: &ka_shared_engine::state::KaBoard) -> Value {
    let mut out = serde_json::Map::new();
    for e in &b.entries[..b.len as usize] {
        out.insert(e.key.to_string(), json!(e.value));
    }
    Value::Object(out)
}
fn entity_finite(e: &ka_shared_engine::world::KaEntity) -> bool {
    [
        e.position.x,
        e.position.y,
        e.position.z,
        e.offset.x,
        e.offset.y,
        e.offset.z,
        e.speed.x,
        e.speed.y,
        e.speed.z,
        e.modifier.offset_x,
        e.modifier.offset_y,
        e.modifier.offset_z,
        e.modifier.scale_x,
        e.modifier.scale_y,
        e.projectile.start.x,
        e.projectile.start.y,
        e.projectile.start.z,
        e.projectile.end.x,
        e.projectile.end.y,
        e.projectile.end.z,
    ]
    .iter()
    .all(|x| x.is_finite())
}

/// Resolve native fighter identities only through the preparation wrapper's explicit map.
/// Names and post-formation roster positions are never used as source identity guesses.
fn native_trace_sources(
    prepared: &Value,
) -> (HashMap<i32, NativeTraceSource>, bool, Option<usize>) {
    let raw_own_count = prepared
        .get("rawIntentOwnCount")
        .and_then(Value::as_u64)
        .and_then(|n| usize::try_from(n).ok());
    let mut complete = raw_own_count.is_some();
    let mut sources = HashMap::new();
    let Some(fighters) = prepared.get("preparedFighters").and_then(Value::as_array) else {
        return (sources, false, raw_own_count);
    };
    for fighter in fighters {
        let Some(identity) = fighter
            .get("identity")
            .and_then(Value::as_i64)
            .and_then(|n| i32::try_from(n).ok())
        else {
            complete = false;
            continue;
        };
        let team = fighter.get("team").and_then(Value::as_i64);
        let source_index = fighter
            .get("sourceIndex")
            .and_then(Value::as_u64)
            .and_then(|n| usize::try_from(n).ok());
        let source = match (team, source_index) {
            (Some(0), Some(index)) => match raw_own_count {
                Some(raw_count) if index < raw_count => NativeTraceSource {
                    domain: Some("prepared-expanded-own"),
                    index: Some(index),
                    kind: Some("raw-intent-own-entry"),
                    raw_intent_own_index: Some(index),
                },
                Some(_) => NativeTraceSource {
                    domain: Some("prepared-expanded-own"),
                    index: Some(index),
                    kind: Some("household-pet-appended"),
                    raw_intent_own_index: None,
                },
                None => {
                    complete = false;
                    NativeTraceSource {
                        domain: Some("prepared-expanded-own"),
                        index: Some(index),
                        kind: None,
                        raw_intent_own_index: None,
                    }
                }
            },
            (Some(1), Some(index)) => NativeTraceSource {
                domain: Some("enemy-incoming"),
                index: Some(index),
                kind: Some("enemy-incoming"),
                raw_intent_own_index: None,
            },
            _ => {
                complete = false;
                NativeTraceSource::default()
            }
        };
        if source.domain.is_none() || source.index.is_none() {
            complete = false;
        }
        if sources.insert(identity, source).is_some() {
            complete = false;
        }
    }
    (sources, complete, raw_own_count)
}

fn native_effective_parameters(
    unit: &ka_shared_engine::state::KaUnit,
) -> Map<String, Value> {
    let mut effective = Map::new();
    for parameter in &unit.params.rows[..unit.params.count as usize] {
        effective.insert(
            parameter.id.to_string(),
            json!({
                "value":unit.param_value(parameter.id, 0),
                "maximum":unit.param_maximum(parameter.id)
            }),
        );
    }
    effective
}

fn trace_source_fields(value: &mut Value, source: NativeTraceSource) {
    if let Some(object) = value.as_object_mut() {
        object.insert("sourceDomain".into(), json!(source.domain));
        object.insert("sourceIndex".into(), json!(source.index));
        object.insert("sourceKind".into(), json!(source.kind));
        object.insert(
            "rawIntentOwnIndex".into(),
            json!(source.raw_intent_own_index),
        );
    }
}

fn unit_json(
    u: &ka_shared_engine::state::KaUnit,
    sources: &HashMap<i32, NativeTraceSource>,
) -> Value {
    let mut params = serde_json::Map::new();
    for p in &u.params.rows[..u.params.count as usize] {
        params.insert(p.id.to_string(),json!({"rawValue":p.raw_value,"extraValue":p.extra_value,"rawMax":p.raw_max,"extraMax":p.extra_max,"trainingLevel":p.training_level}));
    }
    let equipment: Vec<Value> = u.params.equipment[..u.params.equipment_count as usize]
        .iter()
        .map(|r| {
            let pairs: Vec<Value> = (0..r.pair_count.max(0) as usize)
                .map(|i| {
                    if r.present[i] == 0 {
                        Value::Null
                    } else {
                        json!([r.pairs[i][0], r.pairs[i][1]])
                    }
                })
                .collect();
            json!({"level":r.level,"pvpLevel":r.pvp_level,"affinity":r.affinity,"parameters":pairs})
        })
        .collect();
    let skills: Vec<i32> = u.skill_ids[..u.skill_count as usize].to_vec();
    let levels: Vec<i32> = u.levels[..u.level_count as usize].to_vec();
    let invoking: Vec<_> = u.invoking[..u.invoking_count as usize]
        .iter()
        .map(|x| json!([x.skill, x.remaining]))
        .collect();
    let commands:Vec<_>=u.commands[..u.command_count as usize].iter().map(|x|json!({"opcode":x.opcode,"target":x.target,"skill":x.skill,"tick":x.tick,"duration":x.duration,"useIndex":x.use_index})).collect();
    let path: Vec<_> = u.path[..u.path_count as usize]
        .iter()
        .map(|x| json!([x.x, x.y]))
        .collect();
    let mut value = json!({"identity":u.identity,"present":u.present,"id":u.id,"team":u.team,"human":u.human!=0,"monster":u.monster!=0,"flags":u.flags,"destroyed":u.body.destroyed!=0,"present_slots":u.body.has.iter().enumerate().filter_map(|(i,v)|if *v!=0{Some(i)}else{None}).collect::<Vec<_>>(),"components":entity_json(&u.body),"board":board_json(&u.board),"long_board":board_json(&u.long_board),"parameters":params,"effectiveParameters":native_effective_parameters(u),"equipment":equipment,"skills":skills,"levels":levels,"invoking":invoking,"commands":commands,"path":path,"weapon":{"type":u.weapon_type,"shootingRange":u.weapon_shooting_range,"motion":u.weapon_motion,"projectileFlag":u.weapon_projectile_flag},"boss":u.boss!=0,"monsterType":u.monster_type,"specialHuman":u.special_human!=0,"monsterSize":u.monster_size,"human_flag":u.human_flag});
    trace_source_fields(
        &mut value,
        sources.get(&u.identity).copied().unwrap_or_default(),
    );
    value
}
fn initialized_snapshot_json(
    b: &ka_shared_engine::state::KaBattle,
    prepared: &Value,
) -> Result<Value, String> {
    let mut snap = prepared
        .get("snapshot")
        .ok_or("prepared value lacks canonical snapshot")?
        .clone();
    if b.units[..b.count as usize]
        .iter()
        .any(|u| !entity_finite(&u.body))
        || b.objects[..b.object_count as usize]
            .iter()
            .any(|e| !entity_finite(e))
    {
        return Err("native initialized state contains a non-finite component value".into());
    }
    let (sources, source_metadata_complete, raw_own_count) = native_trace_sources(prepared);
    let units = b.units[..b.count as usize]
        .iter()
        .map(|unit| unit_json(unit, &sources))
        .collect::<Vec<_>>();
    let source_mapping_complete = source_metadata_complete
        && sources.len() == b.count as usize
        && b.units[..b.count as usize]
            .iter()
            .all(|unit| sources.get(&unit.identity).is_some_and(|source| source.index.is_some()));
    snap["units"] = json!(units);
    snap["sourceMapping"] = json!({
        "schema":"ka-native-trace-source-map-1",
        "complete":source_mapping_complete,
        "identitySource":"preparedFighters.identity",
        "rawIntentOwnCount":raw_own_count,
        "team0Domain":"prepared-expanded-own",
        "team1Domain":"enemy-incoming",
        "rawIntentOwnIndexRule":"sourceIndex is copied only for team-0 preparedFighters whose sourceIndex is below rawIntentOwnCount"
    });
    snap["objects"]=json!(b.objects[..b.object_count as usize].iter().map(|e|json!({"id":e.id,"destroyed":e.destroyed!=0,"present_slots":e.has.iter().enumerate().filter_map(|(i,v)|if *v!=0{Some(i)}else{None}).collect::<Vec<_>>(),"components":entity_json(e)})).collect::<Vec<_>>());
    let mut subsets = Vec::with_capacity(b.subsets.len());
    for s in &b.subsets {
        let set = &s.members;
        let free: Vec<u32> = set.free[..set.free_len as usize].to_vec();
        let holes: std::collections::HashSet<usize> = free.iter().map(|x| *x as usize).collect();
        let slots: Vec<Value> = set.slots[..set.len as usize]
            .iter()
            .enumerate()
            .map(|(i, x)| {
                if holes.contains(&i) {
                    Value::Null
                } else {
                    json!(x)
                }
            })
            .collect();
        subsets.push(json!({"slots":slots,"free":free,"version":set.version}));
    }
    snap["subsets"] = json!(subsets);
    snap["buckets"] = json!(b.buckets[..b.bucket_count as usize]
        .iter()
        .map(|x| json!([x.key, &x.ids[..x.count as usize]]))
        .collect::<Vec<_>>());
    snap["rng"] = json!({"math":{"values":b.math.values.to_vec(),"index":b.math.index,"partner":b.math.partner,"draws":b.math_draws},"lib":{"values":b.lib.values.to_vec(),"index":b.lib.index,"partner":b.lib.partner,"draws":b.lib_draws}});
    let c = &mut snap["config"];
    c["tick"] = json!(if b.tick == u32::MAX {
        -1i64
    } else {
        b.tick as i64
    });
    c["first_identity"] = json!(b.first_identity);
    c["next_identity"] = json!(b.next_identity);
    c["next_command"] = json!(b.next_command);
    c["map_width"] = json!(b.map_width);
    c["row_offset"] = json!(b.row_offset);
    c["movement_enabled"] = json!(b.movement_enabled != 0);
    c["battle_state"] = json!(b.battle_state);
    c["battle_frame"] = json!(b.battle_frame);
    c["verdict"] = json!(b.verdict);
    c["verdict_tick"] = json!(b.verdict_tick);
    c["ending_counter"] = json!(b.ending_counter);
    c["ending_gate_tick"] = json!(b.ending_gate_tick);
    c["prize_count"] = json!(b.prize_count);
    let mut sources: Vec<_> = b.projectile_sources[..b.projectile_source_count as usize]
        .iter()
        .map(|x| json!([x.identity, x.caster, x.skill]))
        .collect();
    sources.sort_by_key(|x| x[0].as_i64().unwrap_or_default());
    snap["projectile_sources"] = json!(sources);
    let mut animations: Vec<_> = b.animation_resources[..b.animation_resource_count as usize]
        .iter()
        .map(|x| json!([x.res, x.seb, x.max_frame, x.frame]))
        .collect();
    animations.sort_by_key(|x| {
        (
            x[0].as_i64().unwrap_or_default(),
            x[1].as_i64().unwrap_or_default(),
        )
    });
    snap["animation_resources"] = json!(animations);
    Ok(snap)
}

unsafe fn zero_board_entry_padding(board: *mut ka_shared_engine::state::KaBoard) {
    use ka_shared_engine::state::KaEntry;
    let entries = std::ptr::addr_of_mut!((*board).entries) as *mut u8;
    let start = std::mem::offset_of!(KaEntry, key) + std::mem::size_of::<i32>();
    let end = std::mem::offset_of!(KaEntry, value);
    if end > start {
        let stride = std::mem::size_of::<KaEntry>();
        for index in 0..(*board).entries.len() {
            std::ptr::write_bytes(entries.add(index * stride + start), 0, end - start);
        }
    }
}

fn append_suffix(prefix: &Path, suffix: &str) -> PathBuf {
    let mut value = prefix.as_os_str().to_os_string();
    value.push(suffix);
    PathBuf::from(value)
}
unsafe fn dump_native_bytes(ptr: Handle, size: usize, path: &Path) -> Result<Value, String> {
    if ptr.is_null() {
        return Err("cannot dump null native battle".into());
    }
    let bytes = std::slice::from_raw_parts(ptr as *const u8, size);
    let mut file = fs::File::create(path)
        .map_err(|e| format!("create diagnostic dump {}: {e}", path.display()))?;
    file.write_all(bytes)
        .map_err(|e| format!("write diagnostic dump {}: {e}", path.display()))?;
    file.flush()
        .map_err(|e| format!("flush diagnostic dump {}: {e}", path.display()))?;
    file.sync_all()
        .map_err(|e| format!("sync diagnostic dump {}: {e}", path.display()))?;
    Ok(json!({"path":path.display().to_string(),"sizeBytes":size,"sha256":digest(bytes)}))
}

/// Static layout metadata for comparing native raw dumps with an independent ABI consumer.
pub fn abi_layout() -> Value {
    macro_rules! layout{($ty:ty,{$($field:ident),*$(,)?})=>{json!({"size":std::mem::size_of::<$ty>(),"align":std::mem::align_of::<$ty>(),"offsets":{$(stringify!($field):std::mem::offset_of!($ty,$field),)*}})}}
    use ka_shared_engine::{state as ks, world as kw};
    json!({"architecture":std::env::consts::ARCH,"pointerBytes":std::mem::size_of::<usize>(),
        "native":{"KaBattle":layout!(ks::KaBattle,{units,count,tick,subsets,buckets,bucket_count,math,lib,math_draws,lib_draws,events,event_count,stat_notify_calls,stat_subset_checks,stat_bucket_lookups,stat_bucket_steps}),
            "KaUnit":layout!(ks::KaUnit,{present,team,human,monster,flags,id,identity,body,params,boss,monster_type,special_human,monster_size,board,long_board,skill_ids,commands,path}),
            "KaEntity":layout!(kw::KaEntity,{id,destroyed,has,position,offset,parent,speed,image,animation,modifier,effect,projectile}),
            "KaBoard":layout!(ks::KaBoard,{len,entries,positions}),"KaEntry":layout!(ks::KaEntry,{key,value}),
            "KaModifier":layout!(kw::KaModifier,{type_,offset_x,offset_y,offset_z,scale_x,scale_y,angle,anchor,frame,duration,destroy_on_finish,looping,alpha}),
            "KaEffect":layout!(kw::KaEffect,{type_,value1,value2,depth,frame,max_frame,parent,scale}),
            "KaProjectile":layout!(kw::KaProjectile,{start,end,speed,height,frame,length,owner}),
            "KaParams":layout!(ka_shared_engine::params::KaParams,{count,rows,equipment_count,equipment}),
            "KaEquipRow":layout!(ka_shared_engine::params::KaEquipRow,{level,pvp_level,affinity,pair_count,pairs,present}),
            "KaParam":layout!(ka_shared_engine::params::KaParam,{id,raw_value,extra_value,raw_max,extra_max,training_level})},
        "inputMirrors":{"KaUnit":layout!(KaUnit,{present,team,human,monster,flags,id,identity,body,params,boss,monster_type,special_human,monster_size,board,long_board,skill_ids,commands,path}),
            "KaEntity":layout!(Entity,{id,destroyed,has,position,offset,parent,speed,image,animation,modifier,effect,projectile}),
            "KaBoard":layout!(Board,{len,entries,positions}),"KaEntry":layout!(Entry,{key,value}),
            "Modifier":layout!(Modifier,{type_,offset_x,offset_y,offset_z,scale_x,scale_y,angle,anchor,frame,duration,destroy_on_finish,looping,alpha}),
            "Params":layout!(Params,{count,rows,equipment_count,equipment}),"Equip":layout!(Equip,{level,pvp_level,affinity,pair_count,pairs,present})}})
}

impl NativeEngine {
    pub fn load(dll: &Path) -> Result<Self, String> {
        let bytes = fs::read(dll).map_err(|e| format!("read kernel {}: {e}", dll.display()))?;
        let dll_sha256 = digest(&bytes);
        #[cfg(windows)]
        let (module, kernel) = unsafe {
            use std::os::windows::ffi::OsStrExt;
            let wide: Vec<u16> = dll.as_os_str().encode_wide().chain(Some(0)).collect();
            let module = LoadLibraryW(wide.as_ptr());
            if module.is_null() {
                return Err(format!("LoadLibraryW failed for {}", dll.display()));
            }
            let result = (|| {
                let size: SizeFn = symbol(module, b"ka_sizeof_battle\0")?;
                let report_size: SizeFn = symbol(module, b"ka_sizeof_report\0")?;
                let unit_size: SizeFn = symbol(module, b"ka_sizeof_unit\0")?;
                let skill_size: SizeFn = symbol(module, b"ka_sizeof_skill\0")?;
                let entity_size: SizeFn = symbol(module, b"ka_sizeof_entity\0")?;
                let encounter_size_fn: Option<SizeFn> =
                    optional(module, b"ka_sizeof_encounter_report\0");
                let encounter_version_fn: Option<SizeFn> =
                    optional(module, b"ka_encounter_version\0");
                let encounter_size = encounter_size_fn.map(|f| f());
                let encounter_version = encounter_version_fn.map(|f| f());
                let kernel = Kernel {
                    clone: symbol(module, b"ka_battle_clone\0")?,
                    skip: symbol(module, b"ka_battle_skip_lib_draws\0")?,
                    run: symbol(module, b"ka_run_battle\0")?,
                    full_tick: optional(module, b"ka_native_full_tick\0"),
                    report: symbol(module, b"ka_battle_report\0")?,
                    stats: symbol(module, b"ka_battle_stats\0")?,
                    checksum: symbol(module, b"ka_battle_checksum\0")?,
                    free: symbol(module, b"ka_battle_free\0")?,
                    encounter_report: optional(module, b"ka_encounter_report\0"),
                    battle_size: size(),
                    report_size: report_size(),
                    unit_size: unit_size(),
                    skill_size: skill_size(),
                    entity_size: entity_size(),
                    encounter_size,
                    encounter_version,
                    create: symbol(module, b"ka_battle_create\0")?,
                    import: symbol(module, b"ka_battle_import\0")?,
                    config: symbol(module, b"ka_battle_config\0")?,
                    identity: symbol(module, b"ka_battle_identity\0")?,
                    counters: symbol(module, b"ka_battle_set_counters\0")?,
                    add_row: symbol(module, b"ka_battle_add_row\0")?,
                    add_effect: symbol(module, b"ka_battle_add_effect_resource\0")?,
                    set_prizes: symbol(module, b"ka_battle_set_prizes\0")?,
                    set_bases: symbol(module, b"ka_battle_set_human_bases\0")?,
                    set_animation: symbol(module, b"ka_battle_set_animation_resource\0")?,
                    set_subset: symbol(module, b"ka_battle_set_subset\0")?,
                    set_bucket: symbol(module, b"ka_battle_set_bucket\0")?,
                    set_rng: symbol(module, b"ka_battle_set_rng\0")?,
                    set_control: symbol(module, b"ka_battle_set_control\0")?,
                    set_scope: symbol(module, b"ka_battle_set_scope_allowed\0")?,
                    add_input: symbol(module, b"ka_battle_add_input\0")?,
                    add_item: symbol(module, b"ka_battle_add_item\0")?,
                    set_herb: symbol(module, b"ka_battle_set_herb_stock\0")?,
                    set_mp_watch: symbol(module, b"ka_battle_set_mp_watch\0")?,
                };
                if kernel.report_size as usize != std::mem::size_of::<BattleReport>() {
                    return Err(format!(
                        "native report ABI size {} != Rust {}",
                        kernel.report_size,
                        std::mem::size_of::<BattleReport>()
                    ));
                }
                if kernel.unit_size as usize != std::mem::size_of::<KaUnit>()
                    || kernel.skill_size as usize != std::mem::size_of::<Skill>()
                    || kernel.entity_size as usize != std::mem::size_of::<Entity>()
                {
                    return Err(format!("snapshot input ABI mismatch: native unit/skill/entity sizes {}/{}/{} vs Rust {}/{}/{}",kernel.unit_size,kernel.skill_size,kernel.entity_size,std::mem::size_of::<KaUnit>(),std::mem::size_of::<Skill>(),std::mem::size_of::<Entity>()));
                }
                if let Some(n) = kernel.encounter_size {
                    if n as usize != std::mem::size_of::<EncounterReport>() {
                        return Err(format!(
                            "native encounter report size {n} != Rust {}",
                            std::mem::size_of::<EncounterReport>()
                        ));
                    }
                    if kernel.encounter_version != Some(3) || kernel.encounter_report.is_none() {
                        return Err(
                            "unsupported/incomplete encounter report ABI; expected v3".into()
                        );
                    }
                }
                if kernel.battle_size == 0 || kernel.battle_size > 64 * 1024 * 1024 {
                    return Err("native battle ABI size outside safety bound".into());
                }
                if kernel.battle_size as usize != std::mem::size_of::<KaBattle>() {
                    return Err(format!(
                        "linked shared-kernel battle layout mismatch: DLL={} rlib={}",
                        kernel.battle_size,
                        std::mem::size_of::<KaBattle>()
                    ));
                }
                Ok(kernel)
            })();
            match result {
                Ok(k) => (module, k),
                Err(e) => {
                    FreeLibrary(module);
                    return Err(e);
                }
            }
        };
        #[cfg(not(windows))]
        return Err("native kernel loading is currently supported on Windows only".into());
        Ok(Self {
            module,
            kernel,
            dll_sha256,
            template: None,
        })
    }

    pub fn run(
        &mut self,
        snapshot: &Value,
        math_seed: i32,
        lib_seed: i32,
        max_ticks: u32,
        finish_policy: i32,
        diagnostic_dump_prefix: Option<&Path>,
        telemetry_enabled: bool,
        replay_trace: bool,
    ) -> Result<Value, String> {
        if math_seed < 0 || lib_seed < 0 {
            return Err("canonical seed domain is 0..=2147483647".into());
        }
        // The canonical native API accepts the full positive u32 horizon; do not impose
        // an optimizer-specific tick cap here.
        if max_ticks == 0 || !(0..=2).contains(&finish_policy) {
            return Err("tick limit or finish policy outside canonical range".into());
        }
        let finish_policy_name = finish_policy_name(finish_policy)?;
        let prepared_policy = snapshot
            .get("finishPolicy")
            .and_then(Value::as_str)
            .unwrap_or(finish_policy_name);
        if prepared_policy != finish_policy_name {
            return Err(format!(
                "prepared finishPolicy {prepared_policy:?} does not match native policy {finish_policy_name:?}"
            ));
        }
        if diagnostic_dump_prefix.is_some() && finish_policy != 2 {
            return Err("raw native diagnostic dumps require finishPolicy=2 (on-verdict)".into());
        }
        let encoding_started = if telemetry_enabled {
            Some(Instant::now())
        } else {
            None
        };
        let encoded =
            serde_json::to_vec(snapshot).map_err(|e| format!("encode prepared snapshot: {e}"))?;
        let snapshot_sha = digest(&encoded);
        let template_encoding_seconds = encoding_started
            .map(|t| t.elapsed().as_secs_f64())
            .unwrap_or(0.0);
        let mut template_build_seconds = 0.0;
        let mut template_build_count = 0u32;
        if self.template.as_ref().map(|x| x.0.as_str()) != Some(snapshot_sha.as_str()) {
            if let Some((_, old)) = self.template.take() {
                unsafe { (self.kernel.free)(old) }
            }
            let build_started = if telemetry_enabled {
                Some(Instant::now())
            } else {
                None
            };
            let fresh = self.build_template(snapshot, true)?;
            if let Some(t) = build_started {
                template_build_seconds = t.elapsed().as_secs_f64();
                template_build_count = 1;
            }
            self.template = Some((snapshot_sha.clone(), fresh));
        }
        let template = self
            .template
            .as_ref()
            .ok_or("template initialization omitted")?
            .1;
        unsafe {
            let template_checksum_started = if telemetry_enabled {
                Some(Instant::now())
            } else {
                None
            };
            let template_before = (self.kernel.checksum)(template);
            let template_checksum_seconds = template_checksum_started
                .map(|t| t.elapsed().as_secs_f64())
                .unwrap_or(0.0);
            let initialized_capture_started = if telemetry_enabled {
                Some(Instant::now())
            } else {
                None
            };
            let mut initialized_snapshot =
                initialized_snapshot_json(&*(template as *const KaBattle), snapshot)?;
            let mut initialized_prepared = snapshot.clone();
            initialized_prepared["snapshot"] = std::mem::take(&mut initialized_snapshot);
            initialized_prepared["snapshotBoundary"] =
                json!("post-initialization-post-follower-skip-pre-battle");
            initialized_prepared["followerSelectionDrawsPending"] = json!(0);
            initialized_prepared["followerSelectionDrawsApplied"] = snapshot
                .get("followerSelectionDraws")
                .cloned()
                .unwrap_or(json!(0));
            initialized_prepared["initializationProtocol"] =
                json!("canonical-team-placement-pass-then-decide-change-pass-v1");
            let initialized_capture_seconds = initialized_capture_started
                .map(|t| t.elapsed().as_secs_f64())
                .unwrap_or(0.0);
            let clone_started = if telemetry_enabled {
                Some(Instant::now())
            } else {
                None
            };
            let clone = (self.kernel.clone)(template);
            let clone_seconds = clone_started
                .map(|t| t.elapsed().as_secs_f64())
                .unwrap_or(0.0);
            if clone.is_null() {
                return Err("native battle clone failed".into());
            }
            let mut clone_guard = NativeGuard {
                ptr: clone,
                free: self.kernel.free,
            };
            let clone_before_started = if telemetry_enabled {
                Some(Instant::now())
            } else {
                None
            };
            let clone_before = (self.kernel.checksum)(clone);
            let clone_before_checksum_seconds = clone_before_started
                .map(|t| t.elapsed().as_secs_f64())
                .unwrap_or(0.0);
            let diagnostic_before = if let Some(prefix) = diagnostic_dump_prefix {
                let path = append_suffix(prefix, "-before.bin");
                Some(dump_native_bytes(
                    clone,
                    self.kernel.battle_size as usize,
                    &path,
                )?)
            } else {
                None
            };
            let mut report = BattleReport::default();
            let simulation_started = if telemetry_enabled {
                Some(Instant::now())
            } else {
                None
            };
            let status = (self.kernel.run)(clone, max_ticks, finish_policy, &mut report);
            let simulation_seconds = simulation_started
                .map(|t| t.elapsed().as_secs_f64())
                .unwrap_or(0.0);
            let diagnostic_after = if let Some(prefix) = diagnostic_dump_prefix {
                let path = append_suffix(prefix, "-after.bin");
                match dump_native_bytes(clone, self.kernel.battle_size as usize, &path) {
                    Ok(value) => Some(value),
                    Err(error) => {
                        return Err(format!(
                            "native status={status}; post-run diagnostic dump failed: {error}"
                        ));
                    }
                }
            } else {
                None
            };
            if status != 0 {
                clone_guard.ptr = std::ptr::null_mut();
                (self.kernel.free)(clone);
                return Err(format!(
                    "native battle did not complete successfully (status={status}); diagnostic={:?}/{:?}",
                    diagnostic_before,diagnostic_after
                ));
            }
            if report.status != 0 {
                clone_guard.ptr = std::ptr::null_mut();
                (self.kernel.free)(clone);
                return Err(format!(
                    "native report marks run incomplete (report.status={}); diagnostic={:?}/{:?}",
                    report.status, diagnostic_before, diagnostic_after
                ));
            }
            if report.finish_policy != finish_policy {
                return Err(format!(
                    "native report finish-policy echo mismatch: requested={finish_policy}, reported={}",
                    report.finish_policy
                ));
            }
            let reads_started = if telemetry_enabled {
                Some(Instant::now())
            } else {
                None
            };
            let mut report_after = BattleReport::default();
            let read_status = (self.kernel.report)(clone, finish_policy, &mut report_after);
            let mut stats = [0u64; 4];
            let stats_status = (self.kernel.stats)(clone, stats.as_mut_ptr());
            let encounter = self.kernel.encounter_report.map(|get| {
                let mut r = EncounterReport::default();
                let s = get(clone as *const c_void, finish_policy, &mut r);
                (s, r)
            });
            let report_reads_seconds = reads_started
                .map(|t| t.elapsed().as_secs_f64())
                .unwrap_or(0.0);
            let after_checksums_started = if telemetry_enabled {
                Some(Instant::now())
            } else {
                None
            };
            let clone_after = (self.kernel.checksum)(clone);
            let template_after = (self.kernel.checksum)(template);
            let after_checksums_seconds = after_checksums_started
                .map(|t| t.elapsed().as_secs_f64())
                .unwrap_or(0.0);
            clone_guard.ptr = std::ptr::null_mut();
            (self.kernel.free)(clone);
            if read_status != 0 || stats_status != 0 || report != report_after {
                return Err(format!(
                    "native report mismatch: run={status},read={read_status},stats={stats_status}"
                ));
            }
            if clone_before != template_before || template_after != template_before {
                return Err("native clone/template checksum isolation invariant failed".into());
            }
            if let Some((s, r)) = encounter {
                if s != 0 || r.version != 3 {
                    return Err(format!(
                        "encounter report read failed: status={s},version={}",
                        r.version
                    ));
                }
            }
            let (native_trace, trace_seconds) = if replay_trace {
                let trace_started = Instant::now();
                let mut trace = capture_native_trace(
                    &self.kernel,
                    template,
                    snapshot,
                    max_ticks,
                    finish_policy,
                    &report,
                    clone_after,
                    &self.dll_sha256,
                );
                let elapsed = trace_started.elapsed().as_secs_f64();
                trace["durationSeconds"] = json!(elapsed);
                trace = bound_native_trace(trace);
                (Some(trace), elapsed)
            } else {
                (None, 0.0)
            };
            let assembly_started = if telemetry_enabled {
                Some(Instant::now())
            } else {
                None
            };
            let report_json = serde_json::to_value(report)
                .map_err(|e| format!("serialize native report: {e}"))?;
            let (earned, basis) = earned(&report);
            let certificate_before_verdict = certificate_before_verdict(&report);
            let pending_at_verdict = if report.verdict != 0 && report.pending_at_verdict >= 0 {
                json!(report.pending_at_verdict)
            } else if report.verdict != 0 && report.pre_verdict_prize_callbacks >= 0 {
                json!(report.pre_verdict_prize_callbacks)
            } else { Value::Null };
            let awarded = if report.verdict == 2 { json!(0) }
                else if certificate_before_verdict { json!(report.certificate_pending) }
                else { Value::Null };
            let reward_outcome = json!({"pendingChests":pending_at_verdict,
                "awardedChests":awarded,"awardedBasis":if report.verdict == 2 { Some("native-win-loss-gate") }
                    else if certificate_before_verdict { Some("reward-entitlement-certificate") } else { None },
                "inventoryVerified":false,"reason":if earned.is_null() { "unresolved-or-no-data" }
                    else if certificate_before_verdict { "native-pre-verdict-entitlement" } else { "native-loss-gate-or-queued-at-victory" },
                "certificate":if certificate_before_verdict { json!({"holds":true,"frame":report.certificate_frame,
                    "issuedBeforeVerdict":true,"postCertificateDelta":report.post_certificate_delta,
                    "timingRule":"native observer after fighters before global verdict phases",
                    "certificateId":Value::Null}) } else { Value::Null }});
            let earned_measurement_eligible = report.verdict != 0 && earned.as_f64()
                .is_some_and(|value| value.is_finite() && value >= 0.0);
            let earned_measurement_basis = if earned_measurement_eligible {
                basis.as_str()
            } else { None };
            let progress_json = progress_metrics(&report);
            let mp_json = mp_metrics(&report, snapshot);
            let herb_json = herb_metrics(&report);
            let post_verdict_prizes = if report.verdict != 0
                && report.prize_callbacks >= report.pre_verdict_prize_callbacks
                && report.pre_verdict_prize_callbacks >= 0
            {
                json!(report.prize_callbacks - report.pre_verdict_prize_callbacks)
            } else {
                Value::Null
            };
            let finish_boundary = finish_boundary(&report, finish_policy);
            let policy_source = snapshot
                .get("finishPolicySource")
                .and_then(Value::as_str)
                .unwrap_or("native-call-argument");
            let finish_policy_provenance = json!({
                "declared":finish_policy_name,
                "source":policy_source,
                "nativeCode":finish_policy,
                "nativeReportedCode":report.finish_policy,
                "nativeEchoVerified":report.finish_policy == finish_policy,
                "stopSemantics":finish_policy_description(finish_policy),
                "verdictTick":if report.verdict_tick >= 0 { json!(report.verdict_tick) } else { Value::Null },
                "preVerdictPrizeCallbacks":if report.verdict != 0 && report.pre_verdict_prize_callbacks >= 0 { json!(report.pre_verdict_prize_callbacks) } else { Value::Null },
                "observedPostVerdictPrizeCallbacks":post_verdict_prizes,
                "rewardsTruncated":finish_policy == 2,
                "finishBoundary":finish_boundary,
                "autoFinishProducerProven":false,
                "yieldTrusted":false,
                "autoFinishReason":"native STATE_ENDING 3 exit is the one-frame KEY_SELECT input edge 0x14ef538; no automatic producer of that edge exists in the direct call graph, so an automatic Ending-to-Finish is unproven",
                "earnedMeasurementEligible":earned_measurement_eligible,
                "earnedMeasurementBasis":earned_measurement_basis
            });
            let enc_json = encounter
                .map(|(getter_status, raw)| json!({"getterStatus":getter_status,"raw":raw}));
            let mut out = json!({"mathSeed":math_seed,"libSeed":lib_seed,"status":status,"completed":status==0,
                "report":report_json,"earned":earned,"earnedBasis":basis,"earnedMeasurementEligible":earned_measurement_eligible,"earnedMeasurementBasis":earned_measurement_basis,
                "finishPolicy":finish_policy_name,"finishPolicyProvenance":finish_policy_provenance,"finishBoundary":finish_boundary,
                "rewardOutcome":reward_outcome,"progressMetrics":progress_json,"mpMetrics":mp_json,"herbMetrics":herb_json,
                "nativeStats":{"notifyCalls":stats[0],"subsetChecks":stats[1],"bucketLookups":stats[2],"bucketSteps":stats[3]},"nativeStatsBoundary":"battle-only; initialization counters reset","initializedPrepared":initialized_prepared,
                "initializedTemplateChecksum":template_before,"templateChecksumBefore":template_before,"templateChecksumAfter":template_after,"templateUnchanged":template_before==template_after,
                "cloneChecksumBefore":clone_before,"cloneChecksumAfter":clone_after,"snapshotSha256":snapshot_sha,
                "initializationMode":snapshot.get("initializationMode").and_then(Value::as_str).unwrap_or("unknown"),"initializationProtocol":"canonical-team-sequential-placement-and-decide-change-v1",
                "kernel":self.kernel_provenance()});
            if let Some(enc) = enc_json {
                out["encounterReport"] = enc;
            }
            if let (Some(before), Some(after)) = (diagnostic_before, diagnostic_after) {
                out["diagnosticDump"] =
                    json!({"before":before,"after":after,"abiBytes":self.kernel.battle_size});
            }
            if let Some(trace) = native_trace {
                out["nativeTrace"] = trace;
            }
            if let Some(t) = assembly_started {
                let report_assembly_seconds = t.elapsed().as_secs_f64();
                let busy_seconds = template_encoding_seconds
                    + template_build_seconds
                    + template_checksum_seconds
                    + initialized_capture_seconds
                    + clone_seconds
                    + clone_before_checksum_seconds
                    + simulation_seconds
                    + report_reads_seconds
                    + after_checksums_seconds
                    + trace_seconds
                    + report_assembly_seconds;
                out["stageTimings"] = json!({
                    "seconds":{
                        "templateEncodingAndSha256":template_encoding_seconds,
                        "templateBuildImportAndAi":template_build_seconds,
                        "initializedSnapshotCapture":initialized_capture_seconds,
                        "templateInitialChecksum":template_checksum_seconds,
                        "nativeCloneCall":clone_seconds,
                        "cloneInitialChecksum":clone_before_checksum_seconds,
                        "nativeSimulation":simulation_seconds,
                        "reportStatsEncounterGetters":report_reads_seconds,
                        "postRunChecksumReads":after_checksums_seconds,
                        "nativeTraceReplay":trace_seconds,
                        "reportSerializationAndAssembly":report_assembly_seconds
                    },
                    "counts":{
                        "templateEncodingAndSha256":1,
                        "templateBuildImportAndAi":template_build_count,
                        "initializedSnapshotCapture":1,
                        "templateInitialChecksum":1,
                        "nativeCloneCall":1,
                        "cloneInitialChecksum":1,
                        "nativeSimulation":1,
                        "reportStatsEncounterGetters":1,
                        "postRunChecksumReads":1,
                        "nativeTraceReplay":if replay_trace {1u32}else{0u32},
                        "reportSerializationAndAssembly":1
                    },
                    "scope":"per-worker disjoint operations except busyTotalSeconds inclusive; diagnostic dump IO excluded",
                    "busyTotalSeconds":busy_seconds,
                    "diagnosticDumpIoExcluded":true,
                    "stageTimingsSerializationExcluded":true
                });
            }
            Ok(out)
        }
    }

    /// Export the canonical state after fighter initialization, before any battle tick. The
    /// prepared wrapper is preserved and its snapshot is replaced with the live native state.
    pub fn initialized_snapshot(&mut self, prepared: &Value) -> Result<Value, String> {
        let state = self.build_template(prepared, false)?;
        let mut guard = NativeGuard {
            ptr: state,
            free: self.kernel.free,
        };
        let mut output =
            unsafe { initialized_snapshot_json(&*(state as *const KaBattle), prepared)? };
        let mut result = prepared.clone();
        result["snapshot"] = std::mem::take(&mut output);
        result["snapshotBoundary"] = json!("post-initialization-pre-simulation");
        result["followerSelectionDrawsPending"] = prepared
            .get("followerSelectionDraws")
            .cloned()
            .unwrap_or(json!(0));
        result["initializationProtocol"] =
            json!("canonical-team-placement-pass-then-decide-change-pass-v1");
        guard.ptr = std::ptr::null_mut();
        unsafe { (self.kernel.free)(state) };
        Ok(result)
    }

    fn build_template(
        &self,
        prepared: &Value,
        skip_follower_draws: bool,
    ) -> Result<Handle, String> {
        let snapshot = prepared
            .get("snapshot")
            .ok_or("prepared value lacks canonical `snapshot`")?;
        let config = obj(snapshot, "config")?;
        let unit_values = arr(obj(snapshot, "units")?, "units")?;
        if unit_values.is_empty() || unit_values.len() > 32 {
            return Err("native fresh roster must contain 1..=32 units".into());
        }
        let mut units = Vec::with_capacity(unit_values.len());
        for v in unit_values {
            units.push(unit(v)?);
        }
        if !arr(obj(snapshot, "objects")?, "objects")?.is_empty() {
            return Err("fresh-state engine currently excludes pre-existing objects".into());
        }
        if !arr(obj(snapshot, "projectile_sources")?, "projectile_sources")?.is_empty() {
            return Err(
                "fresh-state engine currently excludes pre-existing projectile sources".into(),
            );
        }
        let state = unsafe { (self.kernel.create)() };
        if state.is_null() {
            return Err("native battle allocation failed".into());
        }
        let mut guard = NativeGuard {
            ptr: state,
            free: self.kernel.free,
        };
        let init: Result<(), String> = (|| unsafe {
            if (self.kernel.import)(state, units.as_ptr(), units.len() as u32, 0)
                != units.len() as u32
            {
                return Err("native unit import refused roster".into());
            }
            let first = int(obj(config, "first_identity")?, "first_identity")?;
            (self.kernel.identity)(state, first);
            (self.kernel.config)(
                state,
                int(obj(config, "map_width")?, "map_width")?,
                int(obj(config, "row_offset")?, "row_offset")?,
                boolean(obj(config, "movement_enabled")?, "movement_enabled")?,
            );
            // Canonical fresh engines use -1 before the first tick. The ABI stores
            // its bit pattern in u32, just as ctypes.c_uint32 does in ka_abi.py.
            let initial_tick = int(obj(config, "tick")?, "tick")?;
            if initial_tick < -1 {
                return Err("fresh snapshot tick must be -1 or nonnegative".into());
            }
            (self.kernel.counters)(
                state,
                initial_tick as u32,
                int(obj(config, "next_identity")?, "next_identity")?,
                int(obj(config, "next_command")?, "next_command")?,
            );
            let rows = obj(snapshot, "rows")?
                .as_object()
                .ok_or("rows must be an object")?;
            if rows.len() > 64 {
                return Err("skill rows exceed canonical capacity".into());
            }
            let mut sorted: Vec<_> = rows.iter().collect();
            sorted.sort_by_key(|(k, _)| k.parse::<i32>().unwrap_or(i32::MAX));
            for (key, r) in sorted {
                let id = key
                    .parse::<i32>()
                    .map_err(|_| format!("invalid skill row id {key}"))?;
                if id != int(obj(r, "id")?, "row.id")? {
                    return Err("row key/id mismatch".into());
                }
                let row = Skill {
                    id,
                    category: int(obj(r, "category")?, "row.category")?,
                    kind: int(obj(r, "type")?, "row.type")?,
                    flags: int(obj(r, "flags")?, "row.flags")?,
                    min_mp: int(obj(r, "minMp")?, "row.minMp")?,
                    max_mp: int(obj(r, "maxMp")?, "row.maxMp")?,
                    required_equip_type: int(
                        obj(r, "requiredEquipType")?,
                        "row.requiredEquipType",
                    )?,
                    shooting_range: int(obj(r, "shootingRange")?, "row.shootingRange")?,
                    range: int(obj(r, "range")?, "row.range")?,
                    count: int(obj(r, "count")?, "row.count")?,
                    motion: int(obj(r, "motion")?, "row.motion")?,
                    value: int(obj(r, "value")?, "row.value")?,
                    seb: int(obj(r, "seb")?, "row.seb")?,
                    img: int(obj(r, "img")?, "row.img")?,
                    impact_img: int(obj(r, "impactImg")?, "row.impactImg")?,
                    impact_seb: int(obj(r, "impactSeb")?, "row.impactSeb")?,
                };
                if (self.kernel.add_row)(state, &row) == 0 {
                    return Err("native skill row table overflow".into());
                }
            }
            let effects = obj(snapshot, "effect_resources")?
                .as_object()
                .ok_or("effect_resources must be an object")?;
            if effects.len() > 48 {
                return Err("effect resource count exceeds capacity".into());
            }
            let mut sorted_effects = effects
                .iter()
                .map(|(k, v)| {
                    k.parse::<i32>()
                        .map(|id| (id, v))
                        .map_err(|_| "effect resource id invalid")
                })
                .collect::<Result<Vec<_>, _>>()?;
            sorted_effects.sort_by_key(|(id, _)| *id);
            for (id, v) in sorted_effects {
                if (self.kernel.add_effect)(state, id, int(v, "effect max frame")?) == 0 {
                    return Err("native effect resources overflow".into());
                }
            }
            if let Some(p) = snapshot.get("prizes").filter(|x| !x.is_null()) {
                let a = arr(p, "prizes")?;
                if a.len() > 16 {
                    return Err("prize candidates exceed capacity".into());
                }
                let mut v = Vec::with_capacity(a.len());
                for x in a {
                    v.push(int(x, "prize candidate")?)
                }
                (self.kernel.set_prizes)(state, v.as_ptr(), v.len() as u32);
            }
            if let Some(b) = snapshot.get("human_bases") {
                let a = arr(b, "human_bases")?;
                if a.len() > 48 {
                    return Err("human animation bases exceed capacity".into());
                }
                let mut v = Vec::with_capacity(a.len());
                for x in a {
                    v.push(int(x, "human animation base")?)
                }
                if (self.kernel.set_bases)(state, v.as_ptr(), v.len() as u32) != v.len() as u32 {
                    return Err("native human base install failed".into());
                }
            }
            let animations = arr(obj(snapshot, "animation_resources")?, "animation_resources")?;
            if animations.len() > 512 {
                return Err("animation resource rows exceed capacity".into());
            }
            for x in animations {
                let a = arr(x, "animation resource")?;
                if a.len() != 4 {
                    return Err("animation resource must be [res,seb,maxFrame,frame]".into());
                }
                if (self.kernel.set_animation)(
                    state,
                    int(&a[0], "animation res")?,
                    int(&a[1], "animation seb")?,
                    int(&a[2], "animation maxFrame")?,
                    int(&a[3], "animation frame")?,
                ) == 0
                {
                    return Err("animation resource install failed".into());
                }
            }
            let subsets = arr(obj(snapshot, "subsets")?, "subsets")?;
            if subsets.len() != 11 {
                return Err("snapshot must contain all 11 canonical subsets".into());
            }
            for (i, s) in subsets.iter().enumerate() {
                let slots = arr(obj(s, "slots")?, "subset.slots")?;
                let free = arr(obj(s, "free")?, "subset.free")?;
                if slots.len() > 32768 || free.len() > slots.len() {
                    return Err("subset slots/free outside fresh-state capacity".into());
                }
                let mut sv = Vec::with_capacity(slots.len());
                for x in slots {
                    sv.push(if x.is_null() {
                        0
                    } else {
                        int(x, "subset slot")?
                    })
                }
                let mut fv = Vec::with_capacity(free.len());
                for x in free {
                    fv.push(uint(x, "subset free slot")?)
                }
                (self.kernel.set_subset)(
                    state,
                    i as u32,
                    slots.len() as u32,
                    free.len() as u32,
                    int(obj(s, "version")?, "subset.version")?,
                    sv.as_ptr(),
                    fv.as_ptr(),
                );
            }
            let buckets = arr(obj(snapshot, "buckets")?, "buckets")?;
            if buckets.len() > 2048 {
                return Err("occupancy buckets exceed capacity".into());
            }
            for (i, b) in buckets.iter().enumerate() {
                let pair = arr(b, "bucket")?;
                if pair.len() != 2 {
                    return Err("bucket must be [key,ids]".into());
                }
                let ids = arr(&pair[1], "bucket ids")?;
                if ids.len() > 64 {
                    return Err("bucket members exceed capacity".into());
                }
                let mut vals = Vec::with_capacity(ids.len());
                for x in ids {
                    vals.push(int(x, "bucket identity")?)
                }
                if (self.kernel.set_bucket)(
                    state,
                    i as u32,
                    int(&pair[0], "bucket key")?,
                    vals.len() as u32,
                    vals.as_ptr(),
                ) != i as u32 + 1
                {
                    return Err("native bucket install failed".into());
                }
            }
            let rng = obj(snapshot, "rng")?;
            for (stream, name) in [(0, "math"), (1, "lib")] {
                let r = obj(rng, name)?;
                let vals = array_i32::<56>(obj(r, "values")?, "rng.values")?;
                (self.kernel.set_rng)(
                    state,
                    stream,
                    vals.as_ptr(),
                    int(obj(r, "index")?, "rng.index")?,
                    int(obj(r, "partner")?, "rng.partner")?,
                    uint(obj(r, "draws")?, "rng.draws")?,
                );
            }
            (self.kernel.set_control)(
                state,
                int(obj(config, "battle_state")?, "battle_state")?,
                optional_i32(config, "battle_frame", 0)?,
                optional_i32(config, "verdict", 0)?,
                optional_i32(config, "verdict_tick", -1)?,
                optional_i32(config, "ending_counter", -1)?,
                optional_i32(config, "ending_gate_tick", -1)?,
                0,
                -1,
                optional_i32(config, "prize_count", 0)?,
            );
            let allowed = prepared
                .get("scopeAllowed")
                .or_else(|| snapshot.get("scope_allowed"))
                .map(|x| boolean(x, "scopeAllowed"))
                .transpose()?
                .unwrap_or(0);
            (self.kernel.set_scope)(state, allowed);
            self.install_consumables(state, snapshot)?;
            Ok(())
        })();
        init?;
        let mode = prepared
            .get("initializationMode")
            .and_then(Value::as_str)
            .unwrap_or("unknown");
        let profile_mode = mode == "profile" || mode == "isolated-scene0-sequential";
        let preplaced_mode = mode == "preplaced" || mode == "final-cell-approximation";
        if !profile_mode && !preplaced_mode {
            return Err(format!("unsupported initializationMode {mode}"));
        }
        let placement_groups = arr(obj(prepared, "placementGroups")?, "placementGroups")?;
        let order_groups = arr(
            obj(prepared, "initializationOrders")?,
            "initializationOrders",
        )?;
        if placement_groups.len() != 2 || order_groups.len() != 2 {
            return Err(
                "initialization requires separate own/enemy placement and callback groups".into(),
            );
        }
        for team_group in 0..2 {
            let ids = arr(&order_groups[team_group], "initialization order group")?;
            if profile_mode {
                let steps = arr(&placement_groups[team_group], "placement group")?;
                if steps.len() != ids.len() {
                    return Err("profile placement and initialization order lengths differ".into());
                }
                unsafe {
                    let battle = &mut *(state as *mut KaBattle);
                    for (step, ordered_id) in steps.iter().zip(ids) {
                        let id = int(obj(step, "identity")?, "placement.identity")?;
                        let slot = battle.units[..battle.count as usize]
                            .iter()
                            .position(|u| u.identity == id)
                            .ok_or_else(|| format!("placement refers to unknown identity {id}"))?;
                        if id != int(ordered_id, "initialization identity")? {
                            return Err("profile placement order differs from initialization callback order".into());
                        }
                        let source_cell =
                            array_i32::<2>(obj(step, "sourceCell")?, "placement.sourceCell")?;
                        let source_position =
                            arr(obj(step, "sourcePosition")?, "placement.sourcePosition")?;
                        if source_position.len() != 3 && source_position.len() != 7 {
                            return Err(
                                "placement sourcePosition must have xyz or canonical seven entries"
                                    .into(),
                            );
                        }
                        let source_offset =
                            arr(obj(step, "sourceOffset")?, "placement.sourceOffset")?;
                        if source_offset.len() != 3 {
                            return Err("placement sourceOffset must have three entries".into());
                        }
                        let cell = array_i32::<2>(obj(step, "cell")?, "placement.cell")?;
                        let team = int(obj(step, "team")?, "placement.team")?;
                        let grid = int(obj(step, "grid")?, "placement.grid")?;
                        let current = &battle.units[slot].body.position;
                        let offset = &battle.units[slot].body.offset;
                        if team != team_group as i32
                            || battle.units[slot].body.cell != source_cell
                            || current.x.to_bits()
                                != f32_at(source_position, 0, "sourcePosition")?.to_bits()
                            || current.y.to_bits()
                                != f32_at(source_position, 1, "sourcePosition")?.to_bits()
                            || current.z.to_bits()
                                != f32_at(source_position, 2, "sourcePosition")?.to_bits()
                            || offset.x.to_bits()
                                != f32_at(source_offset, 0, "sourceOffset")?.to_bits()
                            || offset.y.to_bits()
                                != f32_at(source_offset, 1, "sourceOffset")?.to_bits()
                            || offset.z.to_bits()
                                != f32_at(source_offset, 2, "sourceOffset")?.to_bits()
                        {
                            return Err(format!("placement source mismatch for identity {id}"));
                        }
                        let old_key = battle.cell_key(source_cell);
                        let u = &mut battle.units[slot];
                        u.body.cell = cell;
                        u.body.parent = -1;
                        u.body.position.x = cell[0].wrapping_mul(24) as f32;
                        u.body.position.z = cell[1].wrapping_mul(24) as f32;
                        u.body.offset = ka_shared_engine::world::KaVec3 {
                            x: f32_at(source_offset, 0, "sourceOffset")?,
                            y: f32_at(source_offset, 1, "sourceOffset")?,
                            z: f32_at(source_offset, 2, "sourceOffset")?,
                        };
                        u.body.speed = ka_shared_engine::world::KaVec3::default();
                        u.body.direction = if team == 0 { 0 } else { 2 };
                        u.board.set(4, 0);
                        u.board.set(5, 1);
                        u.board.set(6, team as i64);
                        u.board.set(7, grid as i64);
                        u.board.set(8, 0);
                        battle.occupancy_changed(id, old_key);
                    }
                }
            } else if !arr(&placement_groups[team_group], "placement group")?.is_empty() {
                return Err("preplaced mode cannot carry placement mutations".into());
            }
            // InitFighters places the whole team before its second callback loop.
            {
                unsafe {
                    let battle = &mut *(state as *mut KaBattle);
                    for item in ids {
                        let id = int(item, "initialization identity")?;
                        let slot = battle.units[..battle.count as usize]
                            .iter()
                            .position(|u| u.identity == id)
                            .ok_or_else(|| {
                                format!("initialization order refers to unknown identity {id}")
                            })?;
                        let chosen = ai::decide(battle, slot).map_err(|code| {
                            format!("native initial decide failed for identity {id}: {code}")
                        })?;
                        ai::change(battle,slot,chosen).map_err(|code|format!("native initial change failed for identity {id}, state {chosen}: {code}"))?;
                    }
                }
            }
        }
        // Python-prepared snapshots enter with blank profiling counters. Linked Rust
        // initialization above performs real subset/occupancy work, but those operations
        // are preparation, not battle-run statistics.
        unsafe {
            let battle = &mut *(state as *mut KaBattle);
            // Match the canonical ctypes snapshot hydration byte-for-byte: C POD arrays arrive
            // zero-filled, including KaEntry alignment gaps and unused bucket identity tails.
            for unit in battle.units.iter_mut() {
                zero_board_entry_padding(&mut unit.board);
                zero_board_entry_padding(&mut unit.long_board);
            }
            for bucket in &mut battle.buckets[..battle.bucket_count as usize] {
                let used = bucket.count.min(bucket.ids.len() as u32) as usize;
                bucket.ids[used..].fill(0);
            }
            battle.stat_notify_calls = 0;
            battle.stat_subset_checks = 0;
            battle.stat_bucket_lookups = 0;
            battle.stat_bucket_steps = 0;
            battle
                .events
                .fill(ka_shared_engine::state::KaEvent::default());
            battle.event_count = 0;
        }
        if skip_follower_draws {
            let draws = uint(
                obj(prepared, "followerSelectionDraws")?,
                "followerSelectionDraws",
            )?;
            if draws > 1_000_000 {
                return Err("follower selection draw count exceeds cap".into());
            }
            unsafe { (self.kernel.skip)(state, draws) };
        }
        guard.ptr = std::ptr::null_mut();
        Ok(state)
    }

    unsafe fn install_consumables(&self, state: Handle, snapshot: &Value) -> Result<(), String> {
        let Some(c) = snapshot.get("consumables").filter(|x| !x.is_null()) else {
            return Ok(());
        };
        let used = arr(obj(c, "uses")?, "consumables.uses")?;
        if used
            .iter()
            .any(|x| x.get("used").and_then(Value::as_bool) == Some(true))
        {
            return Err("fresh consumables snapshot cannot include prior used records".into());
        }
        (self.kernel.set_herb)(state, int(obj(c, "holy_herb_stock")?, "holy_herb_stock")?);
        let watch = arr(
            c.get("mp_watch").unwrap_or(&Value::Null),
            "consumables.mp_watch",
        )?;
        if watch.len() > 2 {
            return Err("MP watch exceeds capacity".into());
        }
        let mut ids = Vec::new();
        for x in watch {
            ids.push(int(x, "MP watch identity")?)
        }
        if (self.kernel.set_mp_watch)(
            state,
            ids.as_ptr(),
            ids.len() as u32,
            optional_i32(c, "holy_herb_max_uses", 0)?,
        ) != ids.len() as u32
        {
            return Err("MP watch install failed".into());
        }
        let items = arr(obj(c, "items")?, "consumables.items")?;
        if items.len() > 8 {
            return Err("item rows exceed capacity".into());
        }
        for item in items {
            if (self.kernel.add_item)(
                state,
                0,
                int(obj(item, "parameter")?, "item.parameter")?,
                boolean(obj(item, "all_residents")?, "item.all_residents")?,
                int(obj(item, "bonus_min")?, "item.bonus_min")?,
                int(obj(item, "bonus_max")?, "item.bonus_max")?,
                int(obj(item, "stock")?, "item.stock")?,
            ) == 0
            {
                return Err("native item table overflow".into());
            }
        }
        let inputs = arr(obj(c, "inputs")?, "consumables.inputs")?;
        if inputs.len() > 32 {
            return Err("input events exceed capacity".into());
        }
        for event in inputs {
            if (self.kernel.add_input)(
                state,
                int(obj(event, "tick")?, "input.tick")?,
                int(obj(event, "phase")?, "input.phase")?,
                int(obj(event, "kind")?, "input.kind")?,
                int(obj(event, "item")?, "input.item")?,
            ) == 0
            {
                return Err("native input timeline overflow".into());
            }
        }
        Ok(())
    }

    pub fn kernel_provenance(&self) -> Value {
        json!({"kernelSha256":self.dll_sha256,"battleAbiBytes":self.kernel.battle_size,
               "reportAbiBytes":self.kernel.report_size,"unitAbiBytes":self.kernel.unit_size,"skillAbiBytes":self.kernel.skill_size,"entityAbiBytes":self.kernel.entity_size,
               "encounterReportAbiBytes":self.kernel.encounter_size,"encounterAbiVersion":self.kernel.encounter_version,
               "nativeFullTickExportAvailable":self.kernel.full_tick.is_some(),
               "initialization":"linked-ka_shared_engine-ai-decide-change;native-ka_run_battle",
               "sharedKernelSourceSha256":digest(include_bytes!("../../../../../KA-Website/tools/recovery/native/ka_kernel/src/lib.rs")),
               "earnedSource":"optimizer-chest_count-certificate-or-queued-at-victory"})
    }
}

fn native_trace_event_name(kind: i32) -> Option<&'static str> {
    use ka_shared_engine::state as event;
    match kind {
        event::KA_EVENT_STATE => Some("state"),
        event::KA_EVENT_ENQUEUE => Some("enqueue"),
        event::KA_EVENT_INVOCATION => Some("invocation"),
        event::KA_EVENT_ANIMATION => Some("animation"),
        event::KA_EVENT_USE => Some("use"),
        event::KA_EVENT_ATTACK => Some("attack"),
        event::KA_EVENT_MP_PAY => Some("mp_pay"),
        event::KA_EVENT_CREATE => Some("create"),
        event::KA_EVENT_DESTROY => Some("destroy"),
        event::KA_EVENT_HP => Some("hp"),
        event::KA_EVENT_HEAL => Some("heal"),
        event::KA_EVENT_STATUS => Some("status"),
        event::KA_EVENT_PRIZE => Some("prize"),
        event::KA_EVENT_INVOKING => Some("invoking"),
        event::KA_EVENT_AREA_CELL => Some("area_cell"),
        event::KA_EVENT_STATE_SOUND => Some("state_sound"),
        event::KA_EVENT_BODY_FLIGHT => Some("body_flight"),
        event::KA_EVENT_RELEASE => Some("release"),
        event::KA_EVENT_PROJECTILE_LAUNCH => Some("projectile_launch"),
        event::KA_EVENT_REVIVE => Some("revive"),
        event::KA_EVENT_STATUS_TEXT => Some("status_text"),
        event::KA_EVENT_ATTACK_BATCH => Some("attack_batch"),
        event::KA_EVENT_CELL_CHANGE => Some("cell_change"),
        event::KA_EVENT_STATUS_TICK => Some("status_tick"),
        event::KA_EVENT_BODY_IMPACT => Some("body_impact"),
        event::KA_EVENT_PROJECTILE_IMPACT => Some("projectile_impact"),
        event::KA_EVENT_PROJECTILE_CLEANUP => Some("projectile_cleanup"),
        event::KA_EVENT_VERDICT => Some("verdict"),
        event::KA_EVENT_RESOURCE_CHANGE => Some("resource_change"),
        event::KA_EVENT_BATTLE_ITEM => Some("battle_item"),
        _ => None,
    }
}

fn native_trace_unit_states(
    battle: &KaBattle,
    sources: &HashMap<i32, NativeTraceSource>,
    include_effective_parameters: bool,
) -> Vec<NativeTraceUnitState> {
    let mut roster_indices = [0usize; 2];
    battle.units[..(battle.count as usize).min(battle.units.len())]
        .iter()
        .enumerate()
        .map(|(slot, unit)| {
            let team_index = usize::from(unit.team != 0);
            let roster_index = roster_indices[team_index];
            roster_indices[team_index] += 1;
            let source = sources.get(&unit.identity).copied().unwrap_or_default();
            NativeTraceUnitState {
                slot,
                identity: unit.identity,
                fighter_id: unit.id,
                team: unit.team as i32,
                side: if unit.team == 0 { "ally" } else { "enemy" },
                roster_index,
                source_domain: source.domain,
                source_index: source.index,
                source_kind: source.kind,
                raw_intent_own_index: source.raw_intent_own_index,
                effective_parameters: include_effective_parameters
                    .then(|| native_effective_parameters(unit)),
                present: unit.present != 0,
                human: unit.human != 0,
                monster: unit.monster != 0,
                boss: unit.boss != 0,
                weapon_type: unit.weapon_type,
                weapon_motion: unit.weapon_motion,
                skill_ids: unit.skill_ids[..(unit.skill_count as usize).min(unit.skill_ids.len())].to_vec(),
                hp: unit.hp(),
                hp_max: unit.param_maximum(10),
                mp: unit.mp(),
                mp_max: unit.param_maximum(11),
                state: unit.state(),
                frame: unit.frame(),
                grid: unit.grid(),
                cell: [unit.body.cell[0], unit.body.cell[1]],
                command_count: (unit.command_count as usize).min(unit.commands.len()),
                destroyed: unit.destroyed(),
            }
        })
        .collect()
}

fn incomplete_native_trace(reason: &str, policy: i32, horizon: u32, kernel_sha256: &str) -> Value {
    json!({
        "schema":"ka-native-tick-trace-1",
        "targetSchema":"ka-battle-replay-1",
        "complete":false,
        "truncated":false,
        "finishPolicy":finish_policy_name(policy).unwrap_or("unknown"),
        "tickLimit":horizon,
        "source":{"kernelSha256":kernel_sha256,"stepExport":"ka_native_full_tick",
            "eventEncoding":"native KaEvent {kind,unit,a,b,c,d,e}; kind names are hints only",
            "normalizationRequired":true},
        "events":[],
        "unitStateDeltas":[],
        "reason":reason
    })
}

/// Replay the selected DLL's exported canonical full tick on an independent native clone. The
/// wrapper below mirrors `battle_control::run_battle`, including its deferred animation frames and
/// finish policy, then requires both the selected DLL report and full-state checksum to match the
/// ordinary one-shot run before any trace can be marked complete.
unsafe fn capture_native_trace(
    kernel: &Kernel,
    template: Handle,
    prepared: &Value,
    horizon: u32,
    finish_policy: i32,
    expected_report: &BattleReport,
    expected_checksum: u64,
    kernel_sha256: &str,
) -> Value {
    let Some(full_tick) = kernel.full_tick else {
        return incomplete_native_trace(
            "selected native kernel does not export ka_native_full_tick",
            finish_policy,
            horizon,
            kernel_sha256,
        );
    };
    let replay = (kernel.clone)(template);
    if replay.is_null() {
        return incomplete_native_trace(
            "selected native kernel could not clone the initialized battle",
            finish_policy,
            horizon,
            kernel_sha256,
        );
    }
    let _guard = NativeGuard {
        ptr: replay,
        free: kernel.free,
    };
    let battle = &mut *(replay as *mut KaBattle);
    battle.defer_animation_resource_frames = u8::from(
        ka_shared_engine::phases::can_defer_animation_resource_frames(battle),
    );
    battle.deferred_animation_ticks = 0;

    let (sources, source_metadata_complete, raw_own_count) = native_trace_sources(prepared);
    let initial_units = native_trace_unit_states(battle, &sources, true);
    let mut previous_units = initial_units.clone();
    let mut events = Vec::<NativeTraceEvent>::new();
    let mut unit_state_deltas = Vec::<NativeTraceStateDelta>::new();
    let mut next_seq = 0u64;
    let mut captured_ticks = 0u32;
    let mut captured_state_rows = 0usize;
    let mut event_capacity_reached = false;
    let mut truncated = false;
    let mut run_status = 0i32;

    for _ in 0..horizon {
        run_status = full_tick(replay);
        if run_status != 0 {
            break;
        }
        captured_ticks = captured_ticks.saturating_add(1);
        let tick = battle.tick as i32;
        let within_tick_bound = captured_ticks <= MAX_NATIVE_TRACE_TICKS;
        if !within_tick_bound {
            truncated = true;
        }
        if within_tick_bound {
            let event_count = (battle.event_count as usize).min(battle.events.len());
            if battle.event_count as usize >= ka_shared_engine::state::KA_MAX_EVENTS {
                event_capacity_reached = true;
                truncated = true;
            }
            for raw in &battle.events[..event_count] {
                if events.len() >= MAX_NATIVE_TRACE_EVENTS {
                    truncated = true;
                    break;
                }
                events.push(NativeTraceEvent {
                    seq: next_seq,
                    tick,
                    kind_code: raw.kind,
                    kind_name: native_trace_event_name(raw.kind),
                    unit: raw.unit,
                    a: raw.a,
                    b: raw.b,
                    c: raw.c,
                    d: raw.d,
                    e: raw.e,
                });
                next_seq = next_seq.saturating_add(1);
            }
            if battle.event_count as usize > event_count {
                truncated = true;
            }
            // Tick snapshots omit the full stat map; initial/final states carry it once.
            let current_units = native_trace_unit_states(battle, &sources, false);
            let changed: Vec<_> = current_units
                .iter()
                .zip(previous_units.iter())
                .filter_map(|(current, previous)| {
                    let delta = NativeTraceUnitDelta {
                        slot: current.slot,
                        identity: current.identity,
                        hp: (current.hp != previous.hp).then_some(current.hp),
                        hp_max: (current.hp_max != previous.hp_max).then_some(current.hp_max),
                        mp: (current.mp != previous.mp).then_some(current.mp),
                        mp_max: (current.mp_max != previous.mp_max).then_some(current.mp_max),
                        state: (current.state != previous.state).then_some(current.state),
                        frame: (current.frame != previous.frame).then_some(current.frame),
                        grid: (current.grid != previous.grid).then_some(current.grid),
                        cell: (current.cell != previous.cell).then_some(current.cell),
                        command_count: (current.command_count != previous.command_count)
                            .then_some(current.command_count),
                        present: (current.present != previous.present).then_some(current.present),
                        destroyed: (current.destroyed != previous.destroyed)
                            .then_some(current.destroyed),
                    };
                    (delta.hp.is_some()
                        || delta.hp_max.is_some()
                        || delta.mp.is_some()
                        || delta.mp_max.is_some()
                        || delta.state.is_some()
                        || delta.frame.is_some()
                        || delta.grid.is_some()
                        || delta.cell.is_some()
                        || delta.command_count.is_some()
                        || delta.present.is_some()
                        || delta.destroyed.is_some())
                        .then_some(delta)
                })
                .collect();
            if !changed.is_empty() {
                if captured_state_rows.saturating_add(changed.len()) <= MAX_NATIVE_TRACE_STATE_ROWS {
                    captured_state_rows += changed.len();
                    unit_state_deltas.push(NativeTraceStateDelta {
                        tick,
                        units: changed,
                    });
                } else {
                    truncated = true;
                }
            }
            previous_units = current_units;
        }

        if battle.battle_state == 3 {
            if finish_policy == 2 {
                battle.ending_confirmed = 0;
                break;
            }
            if finish_policy == 1 && battle.battle_frame > 79 {
                let (counter, transition) =
                    ka_shared_engine::battle_control::update_ending(battle.battle_frame, battle.verdict);
                battle.ending_counter = counter;
                if transition {
                    battle.ending_gate_tick = battle.tick as i32;
                    battle.ending_confirmed = 1;
                    break;
                }
            }
        }
    }
    if battle.battle_state == 3 && finish_policy == 0 {
        let (counter, transition) =
            ka_shared_engine::battle_control::update_ending(battle.battle_frame, battle.verdict);
        battle.ending_counter = counter;
        battle.ending_confirmed = u8::from(transition);
        if transition {
            battle.ending_gate_tick = battle.tick as i32;
        }
    }
    ka_shared_engine::phases::finish_deferred_animation_resource_frames(battle);

    let final_units = native_trace_unit_states(battle, &sources, true);
    let source_mapping_complete = source_metadata_complete
        && sources.len() == battle.count as usize
        && battle.units[..battle.count as usize].iter().all(|unit| {
            sources
                .get(&unit.identity)
                .is_some_and(|source| source.domain.is_some() && source.index.is_some())
        });
    let trace_checksum = (kernel.checksum)(replay);
    let mut trace_report = BattleReport::default();
    let report_status = (kernel.report)(replay as *const c_void, finish_policy, &mut trace_report);
    let report_matches = report_status == 0 && trace_report == *expected_report;
    let checksum_matches = trace_checksum == expected_checksum;
    let complete = run_status == 0
        && report_matches
        && checksum_matches
        && !truncated
        && !event_capacity_reached
        && source_mapping_complete;
    json!({
        "schema":"ka-native-tick-trace-1",
        "targetSchema":"ka-battle-replay-1",
        "complete":complete,
        "truncated":truncated,
        "finishPolicy":finish_policy_name(finish_policy).unwrap_or("unknown"),
        "tickLimit":horizon,
        "ticksCaptured":captured_ticks,
        "source":{"kernelSha256":kernel_sha256,"stepExport":"ka_native_full_tick",
            "tickControl":"ka_shared_engine battle_control::run_battle semantics around selected-DLL full ticks",
            "eventEncoding":"native KaEvent {kind,unit,a,b,c,d,e}; kind names are hints only",
            "normalizationRequired":true},
        "sourceMapping":{"schema":"ka-native-trace-source-map-1",
            "complete":source_mapping_complete,"identitySource":"preparedFighters.identity",
            "rawIntentOwnCount":raw_own_count,"team0Domain":"prepared-expanded-own",
            "team1Domain":"enemy-incoming",
            "rawIntentOwnIndexRule":"sourceIndex is copied only for team-0 preparedFighters whose sourceIndex is below rawIntentOwnCount"},
        "initialUnits":initial_units,
        "events":events,
        "unitStateDeltas":unit_state_deltas,
        "finalUnits":final_units,
        "finalBattleState":{"tick":battle.tick as i32,"battleState":battle.battle_state,
            "battleFrame":battle.battle_frame,"verdict":battle.verdict,"verdictTick":battle.verdict_tick,
            "endingConfirmed":battle.ending_confirmed != 0,"endingCounter":battle.ending_counter,
            "endingGateTick":battle.ending_gate_tick,"mathDraws":battle.math_draws,"libDraws":battle.lib_draws},
        "parity":{"nativeStatus":run_status,"reportStatus":report_status,"reportMatches":report_matches,
            "checksumMatches":checksum_matches,"expectedChecksum":expected_checksum,"traceChecksum":trace_checksum,
            "expectedReport":serde_json::to_value(expected_report).unwrap_or(Value::Null),
            "traceReport":serde_json::to_value(trace_report).unwrap_or(Value::Null)},
        "eventCapacityReached":event_capacity_reached,
        "eventCount":events.len(),
        "unitStateRows":captured_state_rows,
        "reason":if source_mapping_complete {Value::Null} else {json!("preparedFighters identity/sourceIndex mapping is missing or inconsistent")},
        "normalizationNote":"Native KaEvent payloads retain raw a-e fields; the host adapter must map them into ka-battle-replay-1 fields using canonical event semantics."
    })
}

/// Enforce a hard serialized trace bound after all trace metadata is present. If the detailed
/// payload is too large, retain parity and boundary states where they fit, but never claim the
/// reduced artifact is complete.
fn bound_native_trace(trace: Value) -> Value {
    let encoded = match serde_json::to_vec(&trace) {
        Ok(bytes) => bytes,
        Err(error) => {
            return json!({
                "schema":"ka-native-tick-trace-1",
                "targetSchema":"ka-battle-replay-1",
                "complete":false,
                "truncated":true,
                "tracePayloadLimitBytes":MAX_NATIVE_TRACE_SERIALIZED_BYTES,
                "reason":format!("trace serialization failed: {error}")
            });
        }
    };
    if encoded.len() <= MAX_NATIVE_TRACE_SERIALIZED_BYTES {
        return trace;
    }

    let omitted_sha256 = digest(&encoded);
    let summary = json!({
        "schema":"ka-native-tick-trace-1",
        "targetSchema":"ka-battle-replay-1",
        "complete":false,
        "truncated":true,
        "finishPolicy":trace.get("finishPolicy").cloned().unwrap_or(Value::Null),
        "tickLimit":trace.get("tickLimit").cloned().unwrap_or(Value::Null),
        "ticksCaptured":trace.get("ticksCaptured").cloned().unwrap_or(Value::Null),
        "durationSeconds":trace.get("durationSeconds").cloned().unwrap_or(Value::Null),
        "source":trace.get("source").cloned().unwrap_or(Value::Null),
        "sourceMapping":trace.get("sourceMapping").cloned().unwrap_or(Value::Null),
        "initialUnits":trace.get("initialUnits").cloned().unwrap_or(Value::Null),
        "finalUnits":trace.get("finalUnits").cloned().unwrap_or(Value::Null),
        "finalBattleState":trace.get("finalBattleState").cloned().unwrap_or(Value::Null),
        "parity":trace.get("parity").cloned().unwrap_or(Value::Null),
        "eventCount":trace.get("eventCount").cloned().unwrap_or(Value::Null),
        "unitStateRows":trace.get("unitStateRows").cloned().unwrap_or(Value::Null),
        "eventCapacityReached":trace.get("eventCapacityReached").cloned().unwrap_or(Value::Null),
        "tracePayloadLimitBytes":MAX_NATIVE_TRACE_SERIALIZED_BYTES,
        "traceSerializedBytes":encoded.len(),
        "omittedDetailSha256":omitted_sha256,
        "droppedDetailFields":["events","unitStateDeltas"],
        "reason":"serialized native trace exceeded the size bound; per-tick detail omitted"
    });
    match serde_json::to_vec(&summary) {
        Ok(bytes) if bytes.len() <= MAX_NATIVE_TRACE_SERIALIZED_BYTES => summary,
        _ => json!({
            "schema":"ka-native-tick-trace-1",
            "targetSchema":"ka-battle-replay-1",
            "complete":false,
            "truncated":true,
            "tracePayloadLimitBytes":MAX_NATIVE_TRACE_SERIALIZED_BYTES,
            "traceSerializedBytes":encoded.len(),
            "omittedDetailSha256":omitted_sha256,
            "droppedDetailFields":["events","unitStateDeltas","initialUnits","finalUnits","finalBattleState","parity","sourceMapping"],
            "reason":"serialized native trace exceeded the size bound; compact fallback emitted"
        }),
    }
}

impl Drop for NativeEngine {
    fn drop(&mut self) {
        unsafe {
            if let Some((_, state)) = self.template.take() {
                (self.kernel.free)(state);
            }
            FreeLibrary(self.module);
        }
    }
}

/// The exact kernel report has no Earned scalar. Keep the absence explicit; prize callback counts
/// are not currency and must not be promoted into a score.
#[allow(dead_code)]
fn serialize_native_report(report: &BattleReport) -> Result<Value, String> {
    serde_json::to_value(report).map_err(|e| format!("serialize native report: {e}"))
}

fn finish_policy_name(policy: i32) -> Result<&'static str, String> {
    match policy {
        0 => Ok("at-horizon"),
        1 => Ok("after-ending"),
        2 => Ok("on-verdict"),
        _ => Err(format!("finish policy outside canonical range: {policy}")),
    }
}

fn finish_policy_description(policy: i32) -> &'static str {
    match policy {
        0 => "simulate the declared tick horizon, then request one Ending confirmation",
        1 => "after verdict, wait for the Ending counter gate and request one confirmation",
        2 => "stop at the verdict tick; post-verdict Leaving callbacks and prizes are not simulated",
        _ => "unknown finish policy",
    }
}

/// Certificate observer order is authoritative: it runs after fighters and before global phases.
/// A verdict may be entered later in those same global phases, so equal certificate/verdict ticks
/// are still a pre-verdict certificate. All other certificate conditions are checked directly from
/// the native report; queue counts and the weaker queued-at-victory reading do not qualify.
fn certificate_before_verdict(report: &BattleReport) -> bool {
    report.verdict == 1
        && report.scope_allowed != 0
        && report.certificate_held != 0
        && report.certificate_pending >= 0
        && report.post_certificate_delta == 0
        && report.certificate_frame >= 0
        && report.verdict_tick >= 0
        && report.certificate_frame <= report.verdict_tick
}

fn finish_boundary(report: &BattleReport, policy: i32) -> &'static str {
    if report.verdict == 0 {
        "censored-unresolved-battle"
    } else if policy == 2 {
        "diagnostic-truncated"
    } else if report.ending_confirmed != 0 {
        "diagnostic-declared-finish; native-auto-producer-unproven"
    } else {
        "awaiting-confirmation"
    }
}

fn progress_metrics(report: &BattleReport) -> Value {
    json!({
        "bossIdentity":report.progress_boss,
        "bossDeathTick":report.progress_boss_death_tick,
        "bossLeavingTick":report.progress_boss_leaving_tick,
        "storedCommandsAtDeath":report.progress_stored_at_death,
        "storedCommandsTargetingBossAtDeath":report.progress_stored_targeting_boss_at_death,
        "storedTargetHoldersAtDeath":report.progress_stored_target_holders_at_death,
        "commandsTargetingBoss":report.progress_commands_targeting_boss,
        "commandsTargetingBossReleased":report.progress_commands_targeting_boss_released,
        "maxSimultaneousStoredCommands":report.progress_max_stored,
        "maxSimultaneousCommandsTargetingBoss":report.progress_max_targeting_boss,
        "storedTargetHoldersPeak":report.progress_target_holders_peak,
        "postDeathBossReentries":report.progress_post_death_reentries,
        "postDeathBossLeavings":report.progress_post_death_leavings,
        "postDeathPrizes":report.progress_post_death_prizes,
        "commandsReleasedAfterDeath":report.progress_released_after_death,
        "commandsReleasedAfterDeathTargetingBoss":report.progress_released_after_death_targeting_boss,
        "firstPostDeathCommandReleaseTick":report.progress_first_release_after_death,
        "lastPostDeathCommandReleaseTick":report.progress_last_release_after_death
    })
}

fn mp_metrics(report: &BattleReport, prepared: &Value) -> Value {
    let names = prepared
        .get("mpWatchNames")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let count = report.mp_watch_count.clamp(0, 2) as usize;
    let rows: Vec<Value> = (0..count)
        .map(|slot| {
            let name = names
                .get(slot)
                .and_then(Value::as_str)
                .map(|value| json!(value))
                .unwrap_or(Value::Null);
            let first_low_phase = match report.mp_first_low_phase[slot] {
                0 => json!("before_fighters"),
                1 => json!("after_fighters"),
                _ => Value::Null,
            };
            json!({
                "name":name,
                "identity":report.mp_identity[slot],
                "minimumMp":report.mp_min[slot],
                "minimumMpPercent":report.mp_min_percent[slot],
                "reachedLowMp":report.mp_low[slot] != 0,
                "firstLowMpTick":report.mp_first_low_tick[slot],
                "firstLowMpPhase":first_low_phase,
                "reachedZero":report.mp_zero[slot] != 0
            })
        })
        .collect();
    json!(rows)
}

fn herb_metrics(report: &BattleReport) -> Value {
    let count = report.herb_log_count.clamp(0, 16) as usize;
    let uses: Vec<Value> = (0..count)
        .map(|slot| {
            let phase = match report.herb_use_phase[slot] {
                0 => json!("before_fighters"),
                1 => json!("after_fighters"),
                _ => Value::Null,
            };
            let source = if report.herb_use_source[slot] >= 0 {
                json!(report.herb_use_source[slot])
            } else {
                Value::Null
            };
            json!({
                "tick":report.herb_use_tick[slot],
                "phase":phase,
                "source":source,
                "used":report.herb_use_ok[slot] != 0
            })
        })
        .collect();
    json!({
        "startingStock":report.herb_stock_start,
        "remainingStock":report.herb_stock_remaining,
        "maxUses":report.herb_max_uses,
        "useCount":report.herb_use_count,
        "uses":uses
    })
}

fn earned(report: &BattleReport) -> (Value, Value) {
    if report.verdict == 0 {
        return (Value::Null, json!("unresolved"));
    }
    if report.verdict == 2 {
        return (json!(0), json!("native-win-loss-gate"));
    }
    if report.verdict != 1 {
        return (Value::Null, json!("unknown-verdict"));
    }
    if certificate_before_verdict(report) {
        return (
            json!(report.certificate_pending),
            json!("reward-entitlement-certificate"),
        );
    }
    if report.pending_at_verdict >= 0 {
        return (json!(report.pending_at_verdict), json!("queued-at-victory"));
    }
    if report.pre_verdict_prize_callbacks >= 0 {
        return (json!(report.pre_verdict_prize_callbacks), json!("queued-at-victory"));
    }
    (Value::Null, json!("no-data"))
}

/// SHA-256 implementation reused from the full-battle resident bridge.
pub fn digest(data: &[u8]) -> String {
    const K: [u32; 64] = [
        0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4,
        0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe,
        0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f,
        0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7,
        0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc,
        0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
        0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070, 0x19a4c116,
        0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
        0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7,
        0xc67178f2,
    ];
    let mut h = [
        0x6a09e667u32,
        0xbb67ae85,
        0x3c6ef372,
        0xa54ff53a,
        0x510e527f,
        0x9b05688c,
        0x1f83d9ab,
        0x5be0cd19,
    ];
    let bits = (data.len() as u128).wrapping_mul(8) as u64;
    let mut p = Vec::with_capacity(data.len().saturating_add(72));
    p.extend_from_slice(data);
    p.push(0x80);
    while p.len() % 64 != 56 {
        p.push(0);
    }
    p.extend_from_slice(&bits.to_be_bytes());
    for block in p.chunks_exact(64) {
        let mut w = [0u32; 64];
        for (i, c) in block.chunks_exact(4).take(16).enumerate() {
            w[i] = u32::from_be_bytes(c.try_into().unwrap());
        }
        for i in 16..64 {
            let a = w[i - 15].rotate_right(7) ^ w[i - 15].rotate_right(18) ^ (w[i - 15] >> 3);
            let b = w[i - 2].rotate_right(17) ^ w[i - 2].rotate_right(19) ^ (w[i - 2] >> 10);
            w[i] = w[i - 16]
                .wrapping_add(a)
                .wrapping_add(w[i - 7])
                .wrapping_add(b);
        }
        let [mut a, mut b, mut c, mut d, mut e, mut f, mut g, mut q] = h;
        for i in 0..64 {
            let s1 = e.rotate_right(6) ^ e.rotate_right(11) ^ e.rotate_right(25);
            let ch = (e & f) ^ (!e & g);
            let t1 = q
                .wrapping_add(s1)
                .wrapping_add(ch)
                .wrapping_add(K[i])
                .wrapping_add(w[i]);
            let s0 = a.rotate_right(2) ^ a.rotate_right(13) ^ a.rotate_right(22);
            let maj = (a & b) ^ (a & c) ^ (b & c);
            let t2 = s0.wrapping_add(maj);
            q = g;
            g = f;
            f = e;
            e = d.wrapping_add(t1);
            d = c;
            c = b;
            b = a;
            a = t1.wrapping_add(t2);
        }
        for (slot, v) in h.iter_mut().zip([a, b, c, d, e, f, g, q]) {
            *slot = slot.wrapping_add(v);
        }
    }
    h.iter()
        .flat_map(|v| v.to_be_bytes())
        .map(|b| format!("{b:02x}"))
        .collect()
}
