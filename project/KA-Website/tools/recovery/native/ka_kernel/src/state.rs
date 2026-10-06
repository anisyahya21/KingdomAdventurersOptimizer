//! Native-owned battle state: the roster, the created-entity arena and the shared components.
//!
//! The Python object graph is not reproduced; the semantics its phases read are. Each mapping:
//!
//! | Python                                                     | Native here                          |
//! |------------------------------------------------------------|--------------------------------------|
//! | `unit['board']` - the AI blackboard *dict*                  | `KaBoard` keyed, sorted              |
//! | `unit['long_board']`                                        | `KaUnit.long_board`                  |
//! | `4 in board` / `board.get(17,-1)` / `board.pop(62)`         | `KaBoard::contains/get/remove`       |
//! | `unit['cell']` = `components[5]`                            | `KaUnit.body.cell`                   |
//! | `unit['position']` / `offset` / `velocity`                  | `KaUnit.body.position/offset/speed`  |
//! | `unit['direction']` = `components[14][0]`                   | `KaUnit.body.direction`              |
//! | `unit['animation_rate']` = `components[12][0]`              | `KaUnit.body.animation[0]`           |
//! | `command_animation()['frame']` = `components[2][2]`         | `KaUnit.body.seb[2]`                 |
//! | `unit['flags'] & 2` (destroyed)                             | `KaUnit.body.destroyed`              |
//! | `Skill.dataIds` / `invocationLevels` / `maxSlotNum`         | `skill_ids[..skill_count]` / `levels` |
//! | `ROWS[skill_id]` - the global skill row table               | `KaBattle.rows` indexed by id        |
//! | `Parameter` component and `equipmentRows`                   | `KaUnit.params` (`params.rs`)         |
//! | created effects / trails / projectiles                      | `KaBattle.objects` (`world.rs`)       |
//!
//! Two deliberate boundaries, both flagged rather than guessed:
//!
//! * `average_training_level` and the effective-parameter pass are computed natively from the same
//!   raw parameters and equipment rows the Python uses, so HP/MP mutation stays exact.
//! * Global tick phases (projectile flight, effect/modifier ticking, garbage expiry, movement,
//!   facing, cells, heights, animation) are later slices; this module only owns what the fighter
//!   and action state machine reads and writes.

use crate::params::KaParams;
use crate::rng::KaRandom;
use crate::world::{KaEntity, KaSubset, KA_MAX_OBJECTS, KA_SUBSET_COUNT};
use crate::world::{KaBucket, KA_MAX_BUCKETS};

/// Maximum own units plus encounter fighters the scenario schema allows.
pub const KA_MAX_UNITS: usize = 32;

/// Keyed blackboard capacity. The native board carries at most a dozen live keys
/// (4,5,6,7,8,12,13,14,17 and the single 62/63/64 status slot).
pub const KA_BOARD_CAP: usize = 48;

/// `SharedControllers` effect resource frame table capacity.
pub const KA_MAX_EFFECT_RESOURCES: usize = 48;
/// Prize candidate capacity (`select_prize`).
pub const KA_MAX_PRIZES: usize = 16;
/// `humanAnimationSebBases` capacity (the recovered table has 43 entries).
pub const KA_MAX_HUMAN_BASES: usize = 48;

/// Equipped active skill capacity (`Skill.maxSlotNum`).
pub const KA_MAX_SKILLS: usize = 12;
/// `Skill.invocationLevels` capacity; indexed by the *filtered* ordinal, see `ai::active_skill_infos`.
pub const KA_MAX_LEVELS: usize = 12;
/// `combat_farmer_slice.ROWS` capacity for the rows the ported phases reach.
pub const KA_MAX_ROWS: usize = 64;
/// Implementation storage bound, not a recovered game limit. Long fights can exceed 256.
/// Overflow still refuses native execution so the caller can use the canonical fallback.
pub const KA_MAX_COMMANDS: usize = 1024;
/// `invokingSkills` capacity.
pub const KA_MAX_INVOKE: usize = 64;
/// `GetPath` returns exactly two points; the list is a queue the mover pops.
pub const KA_MAX_PATH: usize = 8;
/// Human training parameter group size.
pub const KA_TRAINING_COUNT: usize = 12;

/// Compact event codes, mirroring the trace kinds the phase emits.
pub const KA_EVENT_STATE: i32 = 1;
pub const KA_EVENT_ENQUEUE: i32 = 2;
pub const KA_EVENT_INVOCATION: i32 = 3;
pub const KA_EVENT_ANIMATION: i32 = 4;
pub const KA_EVENT_USE: i32 = 5;
pub const KA_EVENT_ATTACK: i32 = 6;
pub const KA_EVENT_MP_PAY: i32 = 7;
pub const KA_EVENT_CREATE: i32 = 8;
pub const KA_EVENT_DESTROY: i32 = 9;
pub const KA_EVENT_HP: i32 = 10;
pub const KA_EVENT_HEAL: i32 = 11;
pub const KA_EVENT_STATUS: i32 = 12;
pub const KA_EVENT_PRIZE: i32 = 13;
pub const KA_EVENT_INVOKING: i32 = 14;
pub const KA_EVENT_AREA_CELL: i32 = 15;
/// `SharedControllers.state_sound` - one canonical kind; the RNG purpose is only a label.
pub const KA_EVENT_STATE_SOUND: i32 = 16;
pub const KA_EVENT_BODY_FLIGHT: i32 = 18;
pub const KA_EVENT_RELEASE: i32 = 19;
pub const KA_EVENT_PROJECTILE_LAUNCH: i32 = 20;
pub const KA_EVENT_REVIVE: i32 = 21;
pub const KA_EVENT_STATUS_TEXT: i32 = 22;
pub const KA_EVENT_ATTACK_BATCH: i32 = 23;
/// `combat_spatial.update_cells`'s `cell_changed` callback: `emit('cell_change', ...)`.
pub const KA_EVENT_CELL_CHANGE: i32 = 24;
/// The verdict transition emitted by `SharedControllers.run`.
pub const KA_EVENT_VERDICT: i32 = 29;
/// `resource_change` from a consumable parameter add.
pub const KA_EVENT_RESOURCE_CHANGE: i32 = 30;
/// `battle_item` from `combat_sandbox`'s `emit_item`: one authoritative dispatch record per
/// consumable, emitted *after* the dispatch's `resource_change` events.
pub const KA_EVENT_BATTLE_ITEM: i32 = 31;
/// `combat_skills.update_status`'s `status_tick` observation.
pub const KA_EVENT_STATUS_TICK: i32 = 25;
/// `impact_projectile`'s `body_impact` (a projectile with no Attack component).
pub const KA_EVENT_BODY_IMPACT: i32 = 26;
pub const KA_EVENT_PROJECTILE_IMPACT: i32 = 27;
pub const KA_EVENT_PROJECTILE_CLEANUP: i32 = 28;
/// `SharedControllers.projectile_sources` retains every launch for the battle's lifetime.
/// Each launch creates an entity, so the created-entity arena bounds this table too.
pub const KA_MAX_PROJECTILE_SOURCES: usize = crate::world::KA_MAX_OBJECTS;
/// Diagnostic lib-draw log capacity (well above the ~1.1k draws of a 7000-tick battle).
pub const KA_LIB_LOG_CAP: usize = 4096;
/// Lib-stream purposes, mirroring the canonical `next_lib` labels.
pub const KA_LIB_SKILL_SOUND: i32 = 0;
pub const KA_LIB_DEFENDER_SOUND: i32 = 1;
pub const KA_LIB_NORMAL_ATTACK_UPDATE: i32 = 2;
pub const KA_LIB_LEAVING_SOUND: i32 = 3;
pub const KA_LIB_KNOCKDOWN_SOUND: i32 = 4;
pub const KA_LIB_OTHER: i32 = 9;

/// Consumable/input subsystem (`combat_consumables` + the sandbox input timeline).
pub const KA_MAX_INPUTS: usize = 32;
pub const KA_MAX_ITEMS: usize = 8;
pub const KA_MAX_USE_LOG: usize = 64;
/// Input phases, matching the canonical `before_fighters` / `after_fighters` callbacks.
pub const KA_PHASE_BEFORE_FIGHTERS: i32 = 0;
pub const KA_PHASE_AFTER_FIGHTERS: i32 = 1;
/// Input kinds.
pub const KA_INPUT_HOLY_HERB: i32 = 0;
pub const KA_INPUT_ITEM: i32 = 1;
/// `use_battle_item`'s recovery table: `bonusType -> parameter`.
pub const KA_RECOVERY_PARAMETERS: [i32; 6] = [10, 10, 11, 11, 12, 12];
/// `RECOVERY_ALL_RESIDENTS`: odd bonus types take a single explicit resident, which the battle
/// touch path never supplies, so only these three are reachable in battle.
pub const KA_RECOVERY_ALL_RESIDENTS: [bool; 6] = [true, false, true, false, true, false];
/// MP telemetry / live Holy Herb policy: the configured trigger units (the DPS and the healer).
/// This is experiment configuration supplied by `combat_scenario`, never recovered game data.
pub const KA_MAX_MP_WATCH: usize = 2;
/// Explicit Holy Herb dispatch log capacity; the first `KA_MAX_HERB_USES` dispatches are kept.
pub const KA_MAX_HERB_USES: usize = 16;
/// `combat_progress.HERB_TRIGGER_PERCENT`: the live policy's own-maximum MP threshold.
pub const KA_HERB_TRIGGER_PERCENT: i32 = 3;
/// Parameter id the Holy Herb restores (`combat_consumables.RECOVERY_PARAMETERS[2]`).
pub const KA_MP_PARAMETER: i32 = 11;

/// One scheduled input event.
#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct KaInputEvent {
    pub tick: i32,
    pub phase: i32,
    pub kind: i32,
    /// Item table index; `-1` for a Holy Herb.
    pub item: i32,
}

/// One battle-item definition plus its live stock.
#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct KaItem {
    pub id: i32,
    pub parameter: i32,
    pub all_residents: u8,
    pub bonus_min: i32,
    pub bonus_max: i32,
    pub stock: i32,
}
/// `animation_resources` row capacity (chara res 11 + monster res 22 rows).
pub const KA_MAX_ANIMATION_RESOURCES: usize = 512;
pub const KA_MAX_EVENTS: usize = 8192;

/// One `effect-resource-checks.json` row: `seb id -> maxFrame`.
#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct KaEffectResource {
    pub id: i32,
    pub max_frame: i32,
}

/// One `self.projectile_sources[identity] = (caster, skill_row, command, hit_index)` entry; only the
/// caster identity and skill id are read by the ported impact path.
#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct KaProjectileSource {
    pub identity: i32,
    pub caster: i32,
    pub skill: i32,
}

/// One `animation-resources.json` row: `resources[res][seb] = {frame, max_frame}`.
#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct KaAnimationResource {
    pub res: i32,
    pub seb: i32,
    pub max_frame: i32,
    pub frame: i32,
}

/// One keyed blackboard entry.
#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct KaEntry {
    pub key: i32,
    pub value: i64,
}

/// A keyed blackboard with the Python `dict` operations the combat phases use.
///
/// Entries stay sorted by key so iteration order (and therefore any order-sensitive
/// comparison) is deterministic and independent of insertion history.
#[repr(C)]
#[derive(Clone, Copy)]
pub struct KaBoard {
    pub len: u32,
    pub entries: [KaEntry; KA_BOARD_CAP],
    /// Derived index for common keys, encoded as position + 1 (zero means uncached).
    /// Sorted entries remain authoritative, including for keys outside this cache.
    pub positions: [u8; 128],
}

impl Default for KaBoard {
    fn default() -> Self {
        KaBoard { len: 0, entries: [KaEntry::default(); KA_BOARD_CAP], positions: [0; 128] }
    }
}

impl KaBoard {
    #[inline]
    fn position(&self, key: i32) -> Result<usize, usize> {
        if (0..128).contains(&key) {
            let encoded = self.positions[key as usize];
            if encoded > 0 {
                let index = (encoded - 1) as usize;
                if index < self.len as usize && self.entries[index].key == key {
                    return Ok(index);
                }
            }
        }
        let len = self.len as usize;
        let mut low = 0usize;
        let mut high = len;
        while low < high {
            let middle = (low + high) / 2;
            if self.entries[middle].key < key {
                low = middle + 1;
            } else {
                high = middle;
            }
        }
        if low < len && self.entries[low].key == key {
            Ok(low)
        } else {
            Err(low)
        }
    }

    /// `board[k]`, or `None` where the Python would raise `KeyError`.
    #[inline]
    pub fn get(&self, key: i32) -> Option<i64> {
        match self.position(key) {
            Ok(index) => Some(self.entries[index].value),
            Err(_) => None,
        }
    }

    /// `k in board`.
    #[inline]
    pub fn contains(&self, key: i32) -> bool {
        self.position(key).is_ok()
    }

    /// `board[k] = value`, including the implicit insert.
    pub fn set(&mut self, key: i32, value: i64) {
        match self.position(key) {
            Ok(index) => self.entries[index].value = value,
            Err(index) => {
                let len = self.len as usize;
                if len >= KA_BOARD_CAP {
                    return;
                }
                let mut cursor = len;
                while cursor > index {
                    self.entries[cursor] = self.entries[cursor - 1];
                    cursor -= 1;
                }
                self.entries[index] = KaEntry { key, value };
                self.len = (len + 1) as u32;
                self.reindex();
            }
        }
    }

    fn reindex(&mut self) {
        self.positions = [0; 128];
        for (index, entry) in self.entries[..self.len as usize].iter().enumerate() {
            if (0..128).contains(&entry.key) {
                self.positions[entry.key as usize] = (index + 1) as u8;
            }
        }
    }

    /// `board.pop(k, None)`.
    pub fn remove(&mut self, key: i32) -> Option<i64> {
        match self.position(key) {
            Ok(index) => {
                let value = self.entries[index].value;
                let len = self.len as usize;
                let mut cursor = index;
                while cursor + 1 < len {
                    self.entries[cursor] = self.entries[cursor + 1];
                    cursor += 1;
                }
                self.len -= 1;
                self.reindex();
                Some(value)
            }
            Err(_) => None,
        }
    }

    /// `board.get(key, default)`.
    #[inline]
    pub fn get_or(&self, key: i32, default: i64) -> i64 {
        self.get(key).unwrap_or(default)
    }
}

/// One equipped skill row, the fields the decision and effect paths read.
#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct KaSkill {
    pub id: i32,
    /// `skill['category']` - 0 attack, 1 recovery, 2 passive.
    pub category: i32,
    /// `skill['type']` (`kind` avoids the Rust keyword).
    pub kind: i32,
    pub flags: i32,
    pub min_mp: i32,
    pub max_mp: i32,
    pub required_equip_type: i32,
    pub shooting_range: i32,
    pub range: i32,
    pub count: i32,
    pub motion: i32,
    pub value: i32,
    /// `skill['seb']`, `skill['img']` and `skill['impactImg']` - effect and projectile data.
    pub seb: i32,
    pub img: i32,
    pub impact_img: i32,
    pub impact_seb: i32,
}

/// One persistent skill command (opcode 29).
#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct KaCommand {
    pub opcode: i32,
    pub target: i32,
    pub skill: i32,
    pub tick: i32,
    pub duration: i32,
    pub use_index: i32,
}

/// One `(skill_id, remaining)` entry of `invokingSkills`.
#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct KaInvoke {
    pub skill: i32,
    pub remaining: i32,
}

/// One integral path point.
#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct KaPoint {
    pub x: i32,
    pub y: i32,
}

/// One fighter: the recovered `FighterView` fields plus the owning entity.
#[repr(C)]
#[derive(Clone, Copy)]
pub struct KaUnit {
    /// `fighter is not None` - roster membership.
    pub present: u8,
    pub team: u8,
    pub human: u8,
    pub monster: u8,
    /// `fighter['flags']`; recognized as the entity flags, where bit 1 is `destroyed`.
    pub flags: i32,
    pub id: i32,
    /// The identity this fighter occupies in the shared world id space.
    pub identity: i32,
    /// Shared component payloads (`Position`, `Speed`, `Seb`, `Cell`, `Image`, `Animation`,
    /// `Direction`, `ModifyAnimation`, `Attack`, `Garbage`) plus `has[]`.
    pub body: KaEntity,
    /// `Parameter` (slot 33) plus `equipmentRows`.
    pub params: KaParams,
    /// `specs[i]['weapon']` fields the decision and action paths read.
    pub weapon_type: i32,
    pub weapon_shooting_range: i32,
    pub weapon_motion: i32,
    /// `weapon['projectileFlag']`.
    pub weapon_projectile_flag: i32,
    /// `specs[i].get('boss', False)`.
    pub boss: u8,
    /// `specs[i].get('monsterType', 0)`.
    pub monster_type: i32,
    /// `specs[i].get('specialHumanAnimation', False)`.
    pub special_human: u8,
    /// `specs[i].get('monsterSize', 0)`.
    pub monster_size: i32,
    /// `Human` component (`slot 18`) `flag`, read by `update_heights`'s `human_flag32`.
    pub human_flag: i32,
    /// `AI` (slot 28).
    pub board: KaBoard,
    pub long_board: KaBoard,
    /// `Skill` (slot 51).
    pub skill_ids: [i32; KA_MAX_SKILLS],
    pub skill_count: u32,
    pub levels: [i32; KA_MAX_LEVELS],
    pub level_count: u32,
    pub invoking: [KaInvoke; KA_MAX_INVOKE],
    pub invoking_count: u32,
    pub commands: [KaCommand; KA_MAX_COMMANDS],
    pub command_count: u32,
    pub path: [KaPoint; KA_MAX_PATH],
    pub path_count: u32,
}

impl Default for KaUnit {
    fn default() -> Self {
        KaUnit {
            present: 0,
            team: 0,
            human: 0,
            monster: 0,
            flags: 0,
            id: 0,
            identity: 0,
            body: KaEntity::default(),
            params: KaParams::default(),
            weapon_type: 0,
            weapon_shooting_range: 1,
            weapon_motion: 3,
            weapon_projectile_flag: 0,
            boss: 0,
            monster_type: 0,
            special_human: 0,
            monster_size: 0,
            human_flag: 0,
            board: KaBoard::default(),
            long_board: KaBoard::default(),
            skill_ids: [0; KA_MAX_SKILLS],
            skill_count: 0,
            levels: [0; KA_MAX_LEVELS],
            level_count: 0,
            invoking: [KaInvoke::default(); KA_MAX_INVOKE],
            invoking_count: 0,
            commands: [KaCommand::default(); KA_MAX_COMMANDS],
            command_count: 0,
            path: [KaPoint::default(); KA_MAX_PATH],
            path_count: 0,
        }
    }
}

impl KaUnit {
    /// `specs[i]['team'] == 0`.
    #[inline]
    pub fn ally(&self) -> bool {
        self.team == 0
    }

    /// `value(i, p)`.
    #[inline]
    pub fn param_value(&self, id: i32, default: i32) -> i32 {
        self.params.value(id, self.human != 0, self.ally(), default)
    }

    /// `maximum(i, p)`.
    #[inline]
    pub fn param_maximum(&self, id: i32) -> i32 {
        self.params.maximum(id, self.human != 0, self.ally())
    }

    /// `self.param(i, p)['rawValue']`.
    #[inline]
    pub fn raw(&self, id: i32) -> i32 {
        self.params.find(id).map(|row| row.raw_value).unwrap_or(0)
    }

    /// `value(i, 10)`.
    #[inline]
    pub fn hp(&self) -> i32 {
        self.param_value(10, 0)
    }

    /// `value(i, 11)`.
    #[inline]
    pub fn mp(&self) -> i32 {
        self.param_value(11, 0)
    }

    /// `value(i, 15)` - the `attack_interval` input.
    #[inline]
    pub fn agility(&self) -> i32 {
        self.param_value(15, 0)
    }

    /// `unit['cell']`.
    #[inline]
    pub fn cell(&self) -> (i32, i32) {
        (self.body.cell[0], self.body.cell[1])
    }

    #[inline]
    pub fn cell_x(&self) -> i32 {
        self.body.cell[0]
    }

    #[inline]
    pub fn cell_y(&self) -> i32 {
        self.body.cell[1]
    }

    /// `value(i, 10)`.
    #[inline]
    pub fn hp_value(&self) -> i32 {
        self.hp()
    }

    /// `value(i, 11)`.
    #[inline]
    pub fn mp_value(&self) -> i32 {
        self.mp()
    }

    #[inline]
    pub fn pos_x(&self) -> f32 {
        self.body.position.x
    }

    #[inline]
    pub fn pos_z(&self) -> f32 {
        self.body.position.z
    }

    #[inline]
    pub fn set_pos_x(&mut self, value: f32) {
        self.body.position.x = value;
    }

    #[inline]
    pub fn set_pos_z(&mut self, value: f32) {
        self.body.position.z = value;
    }

    #[inline]
    pub fn vel_x(&self) -> f32 {
        self.body.speed.x
    }

    #[inline]
    pub fn vel_z(&self) -> f32 {
        self.body.speed.z
    }

    #[inline]
    pub fn set_vel_x(&mut self, value: f32) {
        self.body.speed.x = value;
    }

    #[inline]
    pub fn set_vel_z(&mut self, value: f32) {
        self.body.speed.z = value;
    }

    #[inline]
    pub fn offset_z(&self) -> f32 {
        self.body.offset.z
    }

    #[inline]
    pub fn set_offset_z(&mut self, value: f32) {
        self.body.offset.z = value;
    }

    #[inline]
    pub fn direction(&self) -> i32 {
        self.body.direction
    }

    #[inline]
    pub fn set_direction(&mut self, value: i32) {
        self.body.direction = value;
    }

    /// `unit['animation_rate']` = `components[12][0]`.
    #[inline]
    pub fn animation_rate(&self) -> i32 {
        self.body.animation[0]
    }

    #[inline]
    pub fn set_animation_rate(&mut self, value: i32) {
        self.body.animation[0] = value;
    }

    /// `command_animation()['frame']` = `components[2][2]`.
    #[inline]
    pub fn animation_frame(&self) -> i32 {
        self.body.seb[2]
    }

    #[inline]
    pub fn set_animation_frame(&mut self, value: i32) {
        self.body.seb[2] = value;
    }

    /// `target_exists(world, identity)`.
    #[inline]
    pub fn exists(&self) -> bool {
        self.present != 0 && self.body.destroyed == 0
    }

    /// `fighter['flags'] & 2`.
    #[inline]
    pub fn destroyed(&self) -> bool {
        self.body.destroyed != 0
    }

    /// `ComponentSubset.matches` for a fighter.
    #[inline]
    pub fn has_component(&self, slot: usize) -> bool {
        self.body.has[slot] != 0
    }

    /// `unit['board'][5]` - the fighter state.
    #[inline]
    pub fn state(&self) -> i32 {
        self.board.get_or(5, 0) as i32
    }

    /// `unit['board'][4]` - the per-state frame counter.
    #[inline]
    pub fn frame(&self) -> i32 {
        self.board.get_or(4, 0) as i32
    }

    /// `unit['board'][7]` - the AI grid index.
    #[inline]
    pub fn grid(&self) -> i32 {
        self.board.get_or(7, 0) as i32
    }

    /// `unit['board'][6]` - the team id carried on the blackboard.
    #[inline]
    pub fn board_team(&self) -> i32 {
        self.board.get_or(6, 0) as i32
    }

    /// `unit['board'][8]` - the attack gauge.
    #[inline]
    pub fn gauge(&self) -> i32 {
        self.board.get_or(8, 0) as i32
    }
}

/// One recorded event, so call ordering can be compared as well as final state.
#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct KaEvent {
    pub kind: i32,
    pub unit: i32,
    pub a: i32,
    pub b: i32,
    pub c: i32,
    pub d: i32,
    pub e: i32,
}

/// Native-resident battle state, owned by Rust for the life of the battle.
#[repr(C)]
pub struct KaBattle {
    pub units: [KaUnit; KA_MAX_UNITS],
    pub count: u32,
    pub tick: u32,
    pub map_width: i32,
    pub row_offset: i32,
    pub movement_enabled: u8,
    /// Identity of unit 0; fighters occupy `first_identity .. first_identity + count`.
    pub first_identity: i32,
    /// `manager['created_entities']` - the next identity `allocate()` will hand out.
    pub next_identity: i32,
    /// Next `traceId` handed to an enqueued command (`self.next_command`).
    pub next_command: i32,
    /// Created entities (effects, trails, projectiles) in allocation order.
    pub objects: [KaEntity; KA_MAX_OBJECTS],
    pub object_count: u32,
    pub subsets: [KaSubset; KA_SUBSET_COUNT],
    /// `CellOccupancy.buckets` - retained buckets in first-insertion order.
    pub buckets: [KaBucket; KA_MAX_BUCKETS],
    pub bucket_count: u32,
    /// `self.projectile_sources`: a launched projectile keeps its source until it impacts.
    pub projectile_sources: [KaProjectileSource; KA_MAX_PROJECTILE_SOURCES],
    pub projectile_source_count: u32,
    pub animation_resources: [KaAnimationResource; KA_MAX_ANIMATION_RESOURCES],
    pub animation_resource_count: u32,
    /// The canonical resource table arrives in strictly increasing (res, seb) order.
    /// Arbitrary imported tables retain the original first-match scan.
    pub animation_resources_sorted: u8,
    /// Bulk runs can defer independent global clip counters until the last tick.
    pub defer_animation_resource_frames: u8,
    pub deferred_animation_ticks: u32,
    /// `self.effect_resources` - seb id to `maxFrame`.
    pub effect_resources: [KaEffectResource; KA_MAX_EFFECT_RESOURCES],
    pub effect_resource_count: u32,
    /// `self.prize_candidates`; `prize_present == 0` is the Python `None`.
    pub prize_candidates: [i32; KA_MAX_PRIZES],
    /// `len(self.prize_candidates)`, the `select_prize` draw bound.
    pub prize_candidate_count: u32,
    /// `len(self.prizes)` - the number of prize callbacks so far.
    pub prize_count: u32,
    pub prize_present: u8,
    // ---- Battle control (`combat_ending` + `SharedControllers.run`) ----
    pub battle_state: i32,
    pub battle_frame: i32,
    /// `self.verdict`: 0 is the Python `None`.
    pub verdict: i32,
    pub verdict_tick: i32,
    /// `self.ending_counter`; -1 is the Python `None`.
    pub ending_counter: i32,
    pub ending_gate_tick: i32,
    pub ending_confirmed: u8,
    /// `len(self.prizes)` at the moment the verdict was set.
    pub prizes_at_verdict: i32,
    /// `metrics.heals` - cumulative `heal` events over the whole run.
    pub heal_events: u32,
    /// `metrics.attackAttempts` - cumulative `attack` events over the whole run.
    pub attack_events: u32,
    // ---- `RewardEntitlementWatch` (read-only certificate observer) ----
    pub scope_allowed: u8,
    pub observations: u32,
    pub verdict_observations: u32,
    pub certificate_held: u8,
    pub certificate_frame: i32,
    pub certificate_pending: i32,
    pub late_hold_frame: i32,
    pub pending_at_verdict: i32,
    pub post_certificate_delta: i32,
    pub pending_final: i32,
    pub clauses: [u8; 4],
    pub boss_hp: i32,
    pub boss_state: i32,
    pub stored_target_holders: i32,
    pub queued_command_holders: i32,
    // ---- Stored-attack progress (`combat_progress.ProgressWatch`) ----
    // The causal chain from a stored command to a chest, sampled at the same after-fighters seam the
    // entitlement observer uses and notified from the two genuine command mutation sites. Read-only:
    // none of these fields is read back by any phase, so they cannot move a digest or a decision.
    /// The rival leader's identity, resolved once from the roster (`-1` before the first tick).
    pub progress_boss: i32,
    /// First tick whose sample saw the boss at HP 0 (`-1` = never).
    pub progress_boss_death_tick: i32,
    /// First tick whose sample saw the boss in Leaving (8) (`-1` = never).
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
    /// `len(prizes)` at the death sample, so a later sample can state the post-death delta exactly.
    pub progress_prizes_at_death: i32,
    /// The death was first seen during a fighters phase (by the release probe) rather than at the
    /// sample that follows it; set for diagnostics, never read back by any phase.
    pub progress_death_latched_in_phase: u8,
    /// The boss state seen by the previous sample; a re-entry is a *change* into 6/8.
    pub progress_previous_boss_state: i32,
    /// v3: pending boss-targeting command hit attempts still to fire, latched at the first
    /// after-fighters sample that sees the boss dead; `-1` = not latched.
    pub progress_boss_future_hits_at_death: i32,
    /// `1` once `progress_boss_future_hits_at_death` is a real reading (a real `0` is valid).
    pub progress_future_hits_captured: u8,
    /// `1` when the currently executing (not-yet-fired) hit of an in-flight boss command is part
    /// of the mass, else `0`; `-1` before it is latched.
    pub progress_boss_future_includes_executing: i32,
    /// Per-tick occupancy samples; deliberately ticks, not events.
    pub progress_targetable_ticks: u32,
    pub progress_using_skill_ticks: u32,
    /// Post-death transitions out of Damaging (6), sampled at the after-fighters seam.
    pub progress_boss_damaging_resets: u32,
    /// First tick a stored command was aimed at the boss; `-1` = none.
    pub progress_boss_access_first_command: i32,
    /// First tick an attack attempt (hit or miss) was aimed at the boss; `-1` = none.
    pub progress_boss_access_first_attempt: i32,
    // ---- Profiling counters (monotonic; never affect semantics) ----
    /// `notify_subsets` invocations (one per component add/remove).
    pub stat_notify_calls: u64,
    /// Subset membership recomputations (`notify_calls * 11`).
    pub stat_subset_checks: u64,
    /// `bucket_slot` lookups.
    pub stat_bucket_lookups: u64,
    /// Total bucket-array entries probed across those lookups.
    pub stat_bucket_steps: u64,
    /// Diagnostic log of lib-stream draws: `(purpose, tick, draw#, raw value)`.
    pub lib_log: [KaEvent; KA_LIB_LOG_CAP],
    pub lib_log_count: u32,
    // ---- Consumable / input subsystem ----
    pub inputs: [KaInputEvent; KA_MAX_INPUTS],
    pub input_count: u32,
    pub items: [KaItem; KA_MAX_ITEMS],
    pub item_count: u32,
    pub holy_herb_stock: i32,
    /// `holyHerbUses` rows with `used` truthy.
    pub herb_uses_ok: u32,
    /// `itemUses` rows with `used` truthy.
    pub item_uses_ok: u32,
    // ---- Encounter telemetry (versioned, additive; read-only, never read back by a phase) ----
    // Published only through the separate `ka_encounter_report` getter, so the default
    // `KaBattleReport` layout and every legacy run are unchanged.
    /// Own-team (team 0) first HP-positive -> zero tick per roster slot (the true death), recorded
    /// at the HP mutation in `deliver`; `-1` = never died during the run. This is NOT the Leaving
    /// entry: a fighter can leave (state 8) with HP above zero, and a dead fighter can leave again,
    /// so the two are deliberately distinct arrays.
    pub encounter_own_death_tick: [i32; KA_MAX_UNITS],
    /// Own-team (team 0) first Leaving (state 8) entry tick per roster slot; `-1` = never left.
    pub encounter_own_first_leaving_tick: [i32; KA_MAX_UNITS],
    /// Enemy (team != 0) attacks resolved against a target that existed at roll time; the
    /// *post-reaction* result (a kind-24 reaction can turn a hit into a miss), counted after
    /// `reaction` returns.
    pub encounter_enemy_resolved: u32,
    /// Of the resolved attacks, the ones whose post-reaction result hit / missed.
    pub encounter_enemy_hits: u32,
    pub encounter_enemy_misses: u32,
    /// The *pre-reaction* hit roll for the same enemy attacks: the roll before `defender_attack_phase`
    /// can flip it. Kept separate so a reader never confuses the roll with the resolved result.
    pub encounter_enemy_rolls: u32,
    pub encounter_enemy_roll_hits: u32,
    pub encounter_enemy_roll_misses: u32,
    /// Boss (rival-leader) attacks attempted while its HP was already zero, i.e. against a dead
    /// target. `attempted` counts every such resolved attack; `landed` the ones whose post-reaction
    /// result hit. Ground truth is the event's target identity and the HP/death state at the site.
    pub encounter_boss_postdeath_attempts: u32,
    pub encounter_boss_postdeath_lands: u32,
    /// First HP-positive -> zero tick for the rival leader, recorded at the HP mutation.
    pub encounter_boss_death_tick: i32,
    /// Boss Damaging (state 6) re-entries after its first death, at the HP-mutation event site.
    pub encounter_boss_reentries: u32,
    /// v3: event-site Leaving (state 8) entries for the rival leader (never the death).
    pub encounter_boss_leavings: u32,
    /// v3: compact gaps between consecutive post-death landed boss hits (no per-hit trace).
    pub encounter_boss_postdeath_last_hit: i32,
    pub encounter_boss_postdeath_gap_count: i32,
    pub encounter_boss_postdeath_gap_min: i32,
    pub encounter_boss_postdeath_gap_max: i32,
    pub encounter_boss_postdeath_gap_sum: i32,
    /// Reaction-skill candidates evaluated in `defender_attack_phase` (counter checks).
    pub encounter_counter_checks: u32,
    /// Kind-20 stored-command reactions enqueued from that phase (counter enqueues).
    pub encounter_counter_enqueues: u32,
    /// Every dispatched consumable, in order: `(kind, item index, tick, used, percent, remaining)`.
    pub use_log: [KaEvent; KA_MAX_USE_LOG],
    pub use_log_count: u32,
    // ---- MP telemetry and the live Holy Herb policy (`combat_progress.ProgressWatch`) ----
    //
    // The MP sample rides the *existing* after-fighters observer, so there is no second per-tick
    // loop and no callback into Python. With no declared trigger units (`mp_watch_count == 0`) the
    // observer returns immediately and a production battle pays nothing for the policy.
    /// Declared trigger units by identity, in declaration order (the DPS and the healer).
    pub mp_watch: [i32; KA_MAX_MP_WATCH],
    pub mp_watch_count: i32,
    /// `holyHerbMaxUses`: `0` disables the automatic policy.
    pub holy_herb_max_uses: i32,
    /// The declared `holyHerbStock`, so a report can state the starting stock.
    pub holy_herb_start_stock: i32,
    /// A trigger crossed the threshold and a use is latched for the next input seam; the value is
    /// `authorising trigger slot + 1`, or `0` for "nothing latched".
    pub holy_herb_pending: i32,
    /// Per trigger slot: `1` while that unit may authorise a new use. Cleared when it does, so a unit
    /// that simply stays below the threshold cannot fire repeatedly; set again once it recovers above.
    pub holy_herb_armed: [u8; KA_MAX_MP_WATCH],
    /// Minimum MP reached, as the effective value (`world.value(unit, 11)`); `-1` = never sampled.
    pub mp_min: [i32; KA_MAX_MP_WATCH],
    /// Minimum MP as a percentage of the unit's own maximum (`world.rate(unit, 11)`); `-1` = unset.
    pub mp_min_percent: [i32; KA_MAX_MP_WATCH],
    /// The unit was ever seen at or below `KA_HERB_TRIGGER_PERCENT`.
    pub mp_low: [u8; KA_MAX_MP_WATCH],
    pub mp_first_low_tick: [i32; KA_MAX_MP_WATCH],
    pub mp_first_low_phase: [i32; KA_MAX_MP_WATCH],
    /// The unit was ever seen at exactly 0 MP.
    pub mp_zero: [u8; KA_MAX_MP_WATCH],
    /// Explicit Holy Herb dispatch log, in order, capped at `KA_MAX_HERB_USES`:
    /// `(tick, phase, authorising trigger slot, used)`. `source` is `-1` for a prescribed input.
    pub herb_use_tick: [i32; KA_MAX_HERB_USES],
    pub herb_use_phase: [i32; KA_MAX_HERB_USES],
    pub herb_use_source: [i32; KA_MAX_HERB_USES],
    pub herb_use_ok: [u8; KA_MAX_HERB_USES],
    /// Number of dispatches recorded in that log (the total, even past the cap).
    pub herb_use_log: i32,
    /// Legacy import word retained so the existing spatial harness keeps working.
    pub rng: u64,
    pub math: KaRandom,
    pub lib: KaRandom,
    pub math_draws: u32,
    pub lib_draws: u32,
    /// The global skill row table (`combat_farmer_slice.ROWS`) the decisions resolve against.
    pub rows: [KaSkill; KA_MAX_ROWS],
    pub row_count: u32,
    pub rows_sorted: u8,
    /// `skill-combat-constants.json` `humanAnimationSebBases` - the recovered `combat_clip` table.
    pub human_bases: [i32; KA_MAX_HUMAN_BASES],
    pub human_base_count: u32,
    pub events: [KaEvent; KA_MAX_EVENTS],
    pub event_count: u32,
    // ---- Per-phase scratch. Sized for the worst case (every fighter and every created entity in one
    // subset) and allocated with the battle: as function locals these arrays were re-zeroed on every
    // phase call, which cost ~19% of a 7000-tick battle at the old arena size and tripled with a
    // larger one. `collect_*` writes only the entries it reports, and the loops read only those.
    pub scratch_members: [i32; KA_MAX_OBJECTS + KA_MAX_UNITS],
    pub scratch_pending_pair: [(i32, i32); KA_MAX_OBJECTS + KA_MAX_UNITS],
    pub scratch_pending_int: [i32; KA_MAX_OBJECTS + KA_MAX_UNITS],
}

impl KaBattle {
    /// The blank battle, boxed, without ever materialising the struct as a stack temporary.
    ///
    /// `KaBattle` is several megabytes, so building it as a value and boxing that value puts the whole
    /// state on the thread stack and overflows it once the arenas grow. The box is allocated
    /// uninitialised and every field is written through its own address; the megabyte-scale arrays are
    /// written element by element because their whole-array defaults would themselves be large
    /// temporaries. This is the single definition of the blank state.
    pub fn boxed_blank() -> Box<Self> {
        use std::ptr::{addr_of_mut, write};
        let mut boxed = Box::<Self>::new_uninit();
        unsafe {
            let raw = boxed.as_mut_ptr();
            // Zero first so every padding byte is deterministic as well as every field.
            std::ptr::write_bytes(raw as *mut u8, 0, std::mem::size_of::<Self>());
            // Declared here so `raw` resolves in the macro's own hygiene context.
        macro_rules! set {
                ($field:ident) => { write(addr_of_mut!((*raw).$field), Default::default()) };
                ($field:ident[$index:expr]) => {
                    write(addr_of_mut!((*raw).$field[$index]), Default::default())
                };
                ($field:ident = $value:expr) => { write(addr_of_mut!((*raw).$field), $value) };
            }
            for index in 0..KA_MAX_UNITS {
                set!(units[index]);
            }
            set!(count = 0);
            set!(tick = 0);
            set!(map_width = 8);
            set!(row_offset = 3);
            set!(movement_enabled = 1);
            set!(first_identity = 0);
            set!(next_identity = 0);
            set!(next_command = 0);
            for index in 0..KA_MAX_OBJECTS {
                set!(objects[index]);
            }
            set!(object_count = 0);
            for index in 0..KA_SUBSET_COUNT {
                set!(subsets[index]);
            }
            for index in 0..KA_MAX_BUCKETS {
                set!(buckets[index]);
            }
            set!(bucket_count = 0);
            set!(projectile_sources = [KaProjectileSource::default(); KA_MAX_PROJECTILE_SOURCES]);
            set!(projectile_source_count = 0);
            set!(animation_resources = [KaAnimationResource::default(); KA_MAX_ANIMATION_RESOURCES]);
            set!(animation_resource_count = 0);
            set!(animation_resources_sorted = 1);
            set!(defer_animation_resource_frames = 0);
            set!(deferred_animation_ticks = 0);
            set!(effect_resources = [KaEffectResource::default(); KA_MAX_EFFECT_RESOURCES]);
            set!(effect_resource_count = 0);
            set!(prize_candidates = [0; KA_MAX_PRIZES]);
            set!(prize_candidate_count = 0);
            set!(prize_count = 0);
            set!(prize_present = 0);
            set!(battle_state = 2);
            set!(battle_frame = 0);
            set!(verdict = 0);
            set!(verdict_tick = -1);
            set!(ending_counter = -1);
            set!(ending_gate_tick = -1);
            set!(ending_confirmed = 0);
            set!(prizes_at_verdict = -1);
            set!(heal_events = 0);
            set!(attack_events = 0);
            set!(scope_allowed = 0);
            set!(observations = 0);
            set!(verdict_observations = 0);
            set!(certificate_held = 0);
            set!(certificate_frame = -1);
            set!(certificate_pending = -1);
            set!(late_hold_frame = -1);
            set!(pending_at_verdict = -1);
            set!(post_certificate_delta = 0);
            set!(pending_final = 0);
            set!(clauses = [0; 4]);
            set!(boss_hp = 0);
            set!(boss_state = 0);
            set!(stored_target_holders = 0);
            set!(queued_command_holders = 0);
            set!(progress_boss = -1);
            set!(progress_boss_death_tick = -1);
            set!(progress_boss_leaving_tick = -1);
            set!(progress_stored_at_death = 0);
            set!(progress_stored_targeting_boss_at_death = 0);
            set!(progress_stored_target_holders_at_death = 0);
            set!(progress_commands_targeting_boss = 0);
            set!(progress_commands_targeting_boss_released = 0);
            set!(progress_max_stored = 0);
            set!(progress_max_targeting_boss = 0);
            set!(progress_target_holders_peak = 0);
            set!(progress_post_death_reentries = 0);
            set!(progress_post_death_leavings = 0);
            set!(progress_post_death_prizes = 0);
            set!(progress_released_after_death = 0);
            set!(progress_released_after_death_targeting_boss = 0);
            set!(progress_first_release_after_death = -1);
            set!(progress_last_release_after_death = -1);
            set!(progress_prizes_at_death = -1);
            set!(progress_death_latched_in_phase = 0);
            set!(progress_previous_boss_state = -1);
            set!(progress_boss_future_hits_at_death = -1);
            set!(progress_future_hits_captured = 0);
            set!(progress_boss_future_includes_executing = -1);
            set!(progress_targetable_ticks = 0);
            set!(progress_using_skill_ticks = 0);
            set!(progress_boss_damaging_resets = 0);
            set!(progress_boss_access_first_command = -1);
            set!(progress_boss_access_first_attempt = -1);
            set!(stat_notify_calls = 0);
            set!(stat_subset_checks = 0);
            set!(stat_bucket_lookups = 0);
            set!(stat_bucket_steps = 0);
            for index in 0..KA_LIB_LOG_CAP {
                set!(lib_log[index]);
            }
            set!(lib_log_count = 0);
            set!(inputs = [KaInputEvent::default(); KA_MAX_INPUTS]);
            set!(input_count = 0);
            set!(items = [KaItem::default(); KA_MAX_ITEMS]);
            set!(item_count = 0);
            set!(holy_herb_stock = 0);
            set!(herb_uses_ok = 0);
            set!(item_uses_ok = 0);
            set!(encounter_own_death_tick = [-1; KA_MAX_UNITS]);
            set!(encounter_own_first_leaving_tick = [-1; KA_MAX_UNITS]);
            set!(encounter_enemy_resolved = 0);
            set!(encounter_enemy_hits = 0);
            set!(encounter_enemy_misses = 0);
            set!(encounter_enemy_rolls = 0);
            set!(encounter_enemy_roll_hits = 0);
            set!(encounter_enemy_roll_misses = 0);
            set!(encounter_boss_postdeath_attempts = 0);
            set!(encounter_boss_postdeath_lands = 0);
            set!(encounter_boss_death_tick = -1);
            set!(encounter_boss_reentries = 0);
            set!(encounter_boss_leavings = 0);
            set!(encounter_boss_postdeath_last_hit = -1);
            set!(encounter_boss_postdeath_gap_count = 0);
            set!(encounter_boss_postdeath_gap_min = -1);
            set!(encounter_boss_postdeath_gap_max = -1);
            set!(encounter_boss_postdeath_gap_sum = 0);
            set!(encounter_counter_checks = 0);
            set!(encounter_counter_enqueues = 0);
            set!(use_log = [KaEvent::default(); KA_MAX_USE_LOG]);
            set!(use_log_count = 0);
            set!(mp_watch = [-1; KA_MAX_MP_WATCH]);
            set!(mp_watch_count = 0);
            set!(holy_herb_max_uses = 0);
            set!(holy_herb_start_stock = 0);
            set!(holy_herb_pending = 0);
            set!(holy_herb_armed = [1; KA_MAX_MP_WATCH]);
            set!(mp_min = [-1; KA_MAX_MP_WATCH]);
            set!(mp_min_percent = [-1; KA_MAX_MP_WATCH]);
            set!(mp_low = [0; KA_MAX_MP_WATCH]);
            set!(mp_first_low_tick = [-1; KA_MAX_MP_WATCH]);
            set!(mp_first_low_phase = [-1; KA_MAX_MP_WATCH]);
            set!(mp_zero = [0; KA_MAX_MP_WATCH]);
            set!(herb_use_tick = [-1; KA_MAX_HERB_USES]);
            set!(herb_use_phase = [-1; KA_MAX_HERB_USES]);
            set!(herb_use_source = [-1; KA_MAX_HERB_USES]);
            set!(herb_use_ok = [0; KA_MAX_HERB_USES]);
            set!(herb_use_log = 0);
            set!(rng = 0);
            set!(math = KaRandom::default());
            set!(lib = KaRandom::default());
            set!(math_draws = 0);
            set!(lib_draws = 0);
            set!(rows = [KaSkill::default(); KA_MAX_ROWS]);
            set!(row_count = 0);
            set!(rows_sorted = 1);
            set!(human_bases = [0; KA_MAX_HUMAN_BASES]);
            set!(human_base_count = 0);
            for index in 0..KA_MAX_EVENTS {
                set!(events[index]);
            }
            set!(event_count = 0);
            set!(scratch_members = [0; KA_MAX_OBJECTS + KA_MAX_UNITS]);
            set!(scratch_pending_pair = [(0, 0); KA_MAX_OBJECTS + KA_MAX_UNITS]);
            set!(scratch_pending_int = [0; KA_MAX_OBJECTS + KA_MAX_UNITS]);
            boxed.assume_init()
        }
    }

    #[inline]
    pub fn push_event(&mut self, kind: i32, unit: i32, a: i32, b: i32, c: i32, d: i32, e: i32) {
        let index = self.event_count as usize;
        if index < KA_MAX_EVENTS {
            self.events[index] = KaEvent { kind, unit, a, b, c, d, e };
            self.event_count += 1;
        }
    }

    /// Snapshot one subset's live members into the battle's scratch buffer and return their count.
    #[inline]
    pub(crate) fn collect_members(&mut self, subset: usize) -> usize {
        let KaBattle { subsets, scratch_members, .. } = self;
        subsets[subset].members.collect(scratch_members)
    }

    /// `ROWS[skill_id]`. `None` where the Python would raise `KeyError`; the caller
    /// reports that as an unsupported row rather than guessing one.
    #[inline]
    pub fn row(&self, id: i32) -> Option<&KaSkill> {
        let rows = &self.rows[..self.row_count as usize];
        if self.rows_sorted != 0 {
            rows.binary_search_by_key(&id, |row| row.id).ok().map(|slot| &rows[slot])
        } else {
            rows.iter().find(|row| row.id == id)
        }
    }

    /// The roster of one team in ascending entity order (`self.teams[team]`).
    #[inline]
    pub fn roster(&self, team: i32, out: &mut [i32; KA_MAX_UNITS]) -> usize {
        let mut len = 0;
        for index in 0..self.count as usize {
            if self.units[index].present != 0 && self.units[index].board_team() == team {
                out[len] = index as i32;
                len += 1;
            }
        }
        len
    }

    #[inline]
    pub fn effect_resource(&self, id: i32) -> i32 {
        self.effect_resources[..self.effect_resource_count as usize]
            .iter()
            .find(|row| row.id == id)
            .map(|row| row.max_frame)
            .unwrap_or(0)
    }

    /// `self.projectile_sources.get(identity)`.
    #[inline]
    pub fn projectile_source(&self, identity: i32) -> Option<(i32, i32)> {
        self.projectile_sources[..self.projectile_source_count as usize]
            .iter()
            .find(|row| row.identity == identity)
            .map(|row| (row.caster, row.skill))
    }

    /// `resources[res][seb]` - the recovered animation resource row (its `max_frame` is what the
    /// animation phase reads; its `frame` is the global clip-frame counter).
    #[inline]
    pub fn animation_resource(&self, res: i32, seb: i32) -> Option<(i32, i32)> {
        let rows = &self.animation_resources[..self.animation_resource_count as usize];
        let row = if self.animation_resources_sorted != 0 {
            rows.binary_search_by_key(&(res, seb), |row| (row.res, row.seb))
                .ok().map(|index| &rows[index])
        } else {
            rows.iter().find(|row| row.res == res && row.seb == seb)
        };
        row.map(|row| (row.max_frame, row.frame))
    }

    /// `resources[res] is not None` - the manager exists for this resource id.
    #[inline]
    pub fn has_animation_manager(&self, res: i32) -> bool {
        let rows = &self.animation_resources[..self.animation_resource_count as usize];
        if self.animation_resources_sorted != 0 {
            rows.binary_search_by_key(&res, |row| row.res).is_ok()
        } else {
            rows.iter().any(|row| row.res == res)
        }
    }

    /// `self.projectile_sources[identity] = ...`.
    pub fn set_projectile_source(&mut self, identity: i32, caster: i32, skill: i32) {
        let slot = self.projectile_source_count as usize;
        if slot < self.projectile_sources.len() {
            self.projectile_sources[slot] = KaProjectileSource { identity, caster, skill };
            self.projectile_source_count += 1;
        }
    }

    /// `self.next_math(purpose, bound)`: one draw from the math stream, counted and reduced.
    #[inline]
    pub fn next_math(&mut self, bound: i32) -> i32 {
        self.math_draws += 1;
        let raw = self.math.next_int();
        crate::ai::random_below(raw, bound)
    }

    /// One un-reduced math draw: `self.draw('damage_variation')` passes `bound=None`, so the raw
    /// value is handed to `random_range` unchanged.
    #[inline]
    pub fn draw_math(&mut self) -> i32 {
        self.math_draws += 1;
        self.math.next_int()
    }

    /// One un-reduced lib draw.
    #[inline]
    pub fn draw_lib(&mut self) -> i32 {
        self.lib_draws += 1;
        self.lib.next_int()
    }

    /// `self.next_lib(purpose, bound)`.
    #[inline]
    pub fn next_lib(&mut self, bound: i32) -> i32 {
        self.lib_draws += 1;
        let raw = self.lib.next_int();
        crate::ai::random_below(raw, bound)
    }

    /// `self.next_lib(purpose, bound)` with the canonical purpose label recorded for diagnostics.
    /// Behaviour is identical to [`KaBattle::next_lib`]; only the log entry is added.
    #[inline]
    pub fn next_lib_label(&mut self, purpose: i32, bound: i32) -> i32 {
        self.lib_draws += 1;
        let raw = self.lib.next_int();
        let slot = self.lib_log_count as usize;
        if slot < KA_LIB_LOG_CAP {
            self.lib_log[slot] = KaEvent {
                kind: purpose,
                unit: self.tick as i32,
                a: self.lib_draws as i32,
                b: raw,
                c: bound,
                d: 0,
                e: 0,
            };
            self.lib_log_count += 1;
        }
        crate::ai::random_below(raw, bound)
    }

    /// `self.draw_lib()` (the lib stream with no bound).
    #[inline]
    pub fn draw_lib_label(&mut self, purpose: i32) {
        self.next_lib_label(purpose, 0);
    }

    /// Record one dispatched consumable for parity diagnostics.
    pub fn log_use(&mut self, kind: i32, item: i32, used: bool, percent: i32, remaining: i32) {
        let slot = self.use_log_count as usize;
        if slot < KA_MAX_USE_LOG {
            self.use_log[slot] = KaEvent {
                kind,
                unit: item,
                a: self.tick as i32,
                b: i32::from(used),
                c: percent,
                d: remaining,
                e: 0,
            };
            self.use_log_count += 1;
        }
    }

    /// Record one explicit Holy Herb dispatch (`combat_sandbox`'s `holyHerbUses` row plus the
    /// authorising trigger slot). `resource_uses` cannot say which consumable spent a charge, and
    /// the shared use log carries no phase, so the policy's own evidence lives here.
    pub fn log_herb_use(&mut self, phase: i32, source: i32, used: bool) {
        let slot = self.herb_use_log as usize;
        if slot < KA_MAX_HERB_USES {
            self.herb_use_tick[slot] = self.tick as i32;
            self.herb_use_phase[slot] = phase;
            self.herb_use_source[slot] = source;
            self.herb_use_ok[slot] = u8::from(used);
        }
        self.herb_use_log += 1;
    }
}
