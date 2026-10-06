//! Native entity/component storage and the recovered subset membership.
//!
//! Fighters stay in the flat roster (`KaBattle.units`); every entity the ported phases *create*
//! (effects, trails, projectiles) lives in the arena here. Both kinds share one identity space,
//! exactly as `CombatEntities` does, so `world.allocate()` and component writes can be compared.
//!
//! Transcribed from `combat_entities`, `combat_collections.EntitySlotSet`/`ComponentSubset`,
//! `combat_lifecycle.create_entity`/`destroy_entity` and the eleven subsets
//! `SharedControllers.__init__` installs. Two rules are load-bearing and kept literally:
//!
//! * `add_component` silently ignores an occupied slot;
//! * every add/remove notifies **all** subsets, which recompute `matches` and add or remove the
//!   identity, preserving `EntitySlotSet` slot order (freed slots are reused LIFO).

use crate::rng::i32_of;

/// Component slot ids, from `combat_entities.COMPONENT_IDS`.
pub const SLOT_POSITION: usize = 0;
pub const SLOT_SPEED: usize = 1;
pub const SLOT_SEB: usize = 2;
pub const SLOT_DEPTH: usize = 4;
pub const SLOT_CELL: usize = 5;
pub const SLOT_IMAGE: usize = 7;
pub const SLOT_ANIMATION: usize = 12;
pub const SLOT_DIRECTION: usize = 14;
pub const SLOT_MODIFY_ANIMATION: usize = 19;
pub const SLOT_AI: usize = 28;
pub const SLOT_GARBAGE: usize = 32;
pub const SLOT_PARAMETER: usize = 33;
pub const SLOT_EFFECT: usize = 38;
pub const SLOT_PROJECTILE: usize = 39;
pub const SLOT_ATTACK: usize = 46;
pub const SLOT_SKILL: usize = 51;

/// The component array size `CombatEntities.allocate` installs.
pub const KA_SLOT_COUNT: usize = 52;
/// Created-entity capacity.
/// Created-entity arena capacity. A full-length 7000-tick battle creates ~0.4-0.8 objects per tick, so
/// the heaviest captured plain battles reach ~3200 of 4096 -- but a consumable timeline keeps a team
/// alive to the horizon often enough that ~11% of seeds passed 4096, and every one of those fell back
/// to Python. Exhausting the arena is a hard native refusal (the canonical engine has no bound), so the
/// bound is set well above the observed worst case.
#[cfg(not(feature = "large-arena"))]
pub const KA_MAX_OBJECTS: usize = 32768;
#[cfg(feature = "large-arena")]
pub const KA_MAX_OBJECTS: usize = 262144;
/// `EntitySlotSet` capacity per subset (fighters + created entities).
/// Slot capacity of one subset. Must cover `KA_MAX_UNITS + KA_MAX_OBJECTS`, because a subset can hold
/// every live entity at once and `KaSlotSet::add` silently drops a member once the table is full.
pub const KA_SUBSET_CAP: usize = KA_MAX_OBJECTS + crate::state::KA_MAX_UNITS;

/// The eleven subsets in `CombatEntities(100, [...])` order.
pub const KA_SUBSET_COUNT: usize = 11;
pub const SUBSET_PROJECTILES: usize = 0;
pub const SUBSET_MOVING: usize = 1;
pub const SUBSET_CELLS: usize = 2;
pub const SUBSET_MODIFIERS: usize = 3;
pub const SUBSET_CELL_MEMBERS: usize = 4;
pub const SUBSET_EFFECTS: usize = 5;
pub const SUBSET_GARBAGE: usize = 6;
pub const SUBSET_SKILL_MEMBERS: usize = 7;
pub const SUBSET_FIGHTER_ANIMATIONS: usize = 8;
pub const SUBSET_ROTATING: usize = 9;
pub const SUBSET_HEIGHT_MEMBERS: usize = 10;

const SUBSET_REQUIRED: [&[usize]; KA_SUBSET_COUNT] = [
    &[SLOT_PROJECTILE, SLOT_POSITION, SLOT_SPEED],
    &[SLOT_POSITION, SLOT_SPEED],
    &[SLOT_POSITION, SLOT_CELL],
    &[SLOT_MODIFY_ANIMATION],
    &[SLOT_CELL],
    &[SLOT_EFFECT],
    &[SLOT_GARBAGE],
    &[SLOT_SKILL],
    &[SLOT_SEB, SLOT_ANIMATION, SLOT_AI],
    &[SLOT_DIRECTION, SLOT_SPEED, SLOT_SEB, SLOT_IMAGE],
    &[SLOT_POSITION, SLOT_CELL, SLOT_SPEED],
];
const SUBSET_EXCLUDED: [&[usize]; KA_SUBSET_COUNT] = [
    &[],
    &[],
    &[],
    &[],
    &[],
    &[SLOT_DEPTH],
    &[],
    &[],
    &[],
    &[],
    &[11, SLOT_PROJECTILE, SLOT_EFFECT],
];

/// `combat_collections.EntitySlotSet`: identity-keyed slots with LIFO free reuse.
#[repr(C)]
#[derive(Clone, Copy)]
pub struct KaSlotSet {
    /// `len(self.slots)` - the slot array length, including freed holes.
    pub len: u32,
    pub free_len: u32,
    pub version: i32,
    /// Derived, internal: occupied slot count (`len - free_len`), kept for O(1) `count`.
    pub live: u32,
    /// Derived, internal: `1` when the slot is in the free list, so `is_free` is O(1). The canonical
    /// `free` list is still the authority for reuse order.
    pub free_mark: [u8; KA_SUBSET_CAP],
    /// Derived, internal: identity - `base` -> slot, so the duplicate check is O(1).
    pub slot_of: [i32; KA_SUBSET_CAP],
    /// Identity base for `slot_of` (the battle's `first_identity`).
    pub base: i32,
    pub slots: [i32; KA_SUBSET_CAP],
    /// `free.pop()` order, i.e. the free list is walked from the end.
    pub free: [u32; KA_SUBSET_CAP],
}

impl Default for KaSlotSet {
    fn default() -> Self {
        KaSlotSet {
            len: 0,
            free_len: 0,
            version: 0,
            live: 0,
            free_mark: [0; KA_SUBSET_CAP],
            slot_of: [-1; KA_SUBSET_CAP],
            base: i32::MIN,
            slots: [0; KA_SUBSET_CAP],
            free: [0; KA_SUBSET_CAP],
        }
    }
}

impl KaSlotSet {
    /// `value in self.indices` -> the slot holding it.
    fn find(&self, value: i32) -> Option<usize> {
        // Derived direct index first; identity ranges beyond the table fall back to the scan.
        let offset = value as i64 - self.base as i64;
        if offset >= 0 && (offset as usize) < KA_SUBSET_CAP {
            let slot = self.slot_of[offset as usize];
            if slot >= 0 {
                let slot = slot as usize;
                if slot < self.len as usize && self.free_mark[slot] == 0
                    && self.slots[slot] == value
                {
                    return Some(slot);
                }
            }
            return None;
        }
        (0..self.len as usize).find(|&slot| self.free_mark[slot] == 0 && self.slots[slot] == value)
    }

    /// Derived index maintenance for `slot_of`.
    #[inline]
    fn mark(&mut self, value: i32, slot: usize) {
        let offset = value as i64 - self.base as i64;
        if offset >= 0 && (offset as usize) < KA_SUBSET_CAP {
            self.slot_of[offset as usize] = slot as i32;
        }
    }

    #[inline]
    fn unmark(&mut self, value: i32) {
        let offset = value as i64 - self.base as i64;
        if offset >= 0 && (offset as usize) < KA_SUBSET_CAP {
            self.slot_of[offset as usize] = -1;
        }
    }

    /// `AddIfNotPresent`. Returns whether the identity was inserted.
    pub fn add(&mut self, value: i32) -> bool {
        if self.find(value).is_some() {
            return false;
        }
        let index = if self.free_len > 0 {
            // `free.pop()` - the most recently freed slot.
            let index = self.free[(self.free_len - 1) as usize] as usize;
            self.free_len -= 1;
            index
        } else {
            let len = self.len as usize;
            if len >= KA_SUBSET_CAP {
                return false;
            }
            self.len += 1;
            len
        };
        self.slots[index] = value;
        self.free_mark[index] = 0;
        self.mark(value, index);
        self.live += 1;
        self.version = i32_of(self.version as i64 + 1);
        true
    }

    /// `Remove`.
    pub fn remove(&mut self, value: i32) -> bool {
        let slot = match self.find(value) {
            Some(slot) => slot,
            None => return false,
        };
        self.unmark(value);
        self.free_mark[slot] = 1;
        if (self.free_len as usize) < KA_SUBSET_CAP {
            self.free[self.free_len as usize] = slot as u32;
            self.free_len += 1;
        }
        if self.live > 0 {
            self.live -= 1;
        }
        if self.live == 0 {
            // `if not self.indices: self.slots.clear(); self.free.clear()`
            self.len = 0;
            self.free_len = 0;
        }
        self.version = i32_of(self.version as i64 + 1);
        true
    }

    /// Walk the slots once and copy the live members out, in native slot order. This is the
    /// batch form of `__iter__`: calling `member(i)` in a loop rescans from slot 0 each time.
    pub fn collect(&self, out: &mut [i32]) -> usize {
        let mut len = 0usize;
        for slot in 0..self.len as usize {
            if self.free_mark[slot] == 0 {
                if len >= out.len() {
                    break;
                }
                out[len] = self.slots[slot];
                len += 1;
            }
        }
        len
    }

    /// Keep the derived indices consistent after a raw array load from a snapshot.
    pub fn rebuild_derived(&mut self) {
        self.free_mark = [0; KA_SUBSET_CAP];
        self.slot_of = [-1; KA_SUBSET_CAP];
        for position in 0..self.free_len as usize {
            let slot = self.free[position] as usize;
            if slot < KA_SUBSET_CAP {
                self.free_mark[slot] = 1;
            }
        }
        let mut live = 0u32;
        for slot in 0..self.len as usize {
            if self.free_mark[slot] == 0 {
                live += 1;
                self.mark(self.slots[slot], slot);
            }
        }
        self.live = live;
    }

    /// `__iter__`: slot order, skipping freed slots.
    pub fn member(&self, index: usize) -> Option<i32> {
        let mut seen = 0usize;
        for slot in 0..self.len as usize {
            if self.free_mark[slot] == 0 {
                if seen == index {
                    return Some(self.slots[slot]);
                }
                seen += 1;
            }
        }
        None
    }

    pub fn count(&self) -> usize {
        self.live as usize
    }
}

/// `combat_collections.ComponentSubset`.
#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct KaSubset {
    pub index: i32,
    pub members: KaSlotSet,
}

// ---------------------------------------------------------------------------------------------
// Created entities
// ---------------------------------------------------------------------------------------------

#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct KaVec3 {
    pub x: f32,
    pub y: f32,
    pub z: f32,
}

/// One created entity. Component payloads are the fields the ported phases write.
#[repr(C)]
#[derive(Clone, Copy)]
pub struct KaEntity {
    pub id: i32,
    pub destroyed: u8,
    pub has: [u8; KA_SLOT_COUNT],
    pub position: KaVec3,
    pub offset: KaVec3,
    pub parent: i32,
    pub speed: KaVec3,
    pub seb: [i32; 4],
    pub depth: [i32; 2],
    pub cell: [i32; 2],
    pub image: [i32; 6],
    pub animation: [i32; 2],
    pub direction: i32,
    /// `ModifyAnimation` (slot 19).
    pub modifier: KaModifier,
    /// `Effect` (slot 38).
    pub effect: KaEffect,
    /// `Projectile` (slot 39).
    pub projectile: KaProjectile,
    /// `Attack` (slot 46).
    pub attack: i32,
    /// `Garbage` (slot 32).
    pub garbage: i32,
}

impl Default for KaEntity {
    fn default() -> Self {
        KaEntity {
            id: 0,
            destroyed: 0,
            has: [0; KA_SLOT_COUNT],
            position: KaVec3::default(),
            offset: KaVec3::default(),
            parent: -1,
            speed: KaVec3::default(),
            seb: [0; 4],
            depth: [0; 2],
            cell: [0; 2],
            image: [-1; 6],
            animation: [1, -1],
            direction: 0,
            modifier: KaModifier::default(),
            effect: KaEffect::default(),
            projectile: KaProjectile::default(),
            attack: 0,
            garbage: 0,
        }
    }
}

#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct KaModifier {
    pub type_: i32,
    pub offset_x: f32,
    pub offset_y: f32,
    pub offset_z: f32,
    pub scale_x: f32,
    pub scale_y: f32,
    pub angle: i32,
    pub anchor: i32,
    pub frame: i32,
    pub duration: i32,
    pub destroy_on_finish: u8,
    pub looping: u8,
    pub alpha: i32,
}

#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct KaEffect {
    pub type_: i32,
    pub value1: i32,
    pub value2: i32,
    pub depth: u8,
    pub frame: i32,
    pub max_frame: i32,
    pub parent: i32,
    pub scale: i32,
}

#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct KaProjectile {
    pub start: KaVec3,
    pub end: KaVec3,
    pub speed: i32,
    pub height: i32,
    pub frame: i32,
    pub length: i32,
    pub owner: i32,
}

/// One exported component record, the comparison surface for created entities.
#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct KaComponentView {
    pub slot: i32,
    pub ints: [i32; 8],
    pub floats: [f32; 8],
}

/// `combat_spatial.CellOccupancy` bucket capacity.
pub const KA_MAX_BUCKETS: usize = 2048;
pub const KA_MAX_BUCKET_MEMBERS: usize = 64;

/// One retained cell bucket, in native insertion order.
#[repr(C)]
#[derive(Clone, Copy)]
pub struct KaBucket {
    pub key: i32,
    pub count: u32,
    pub ids: [i32; KA_MAX_BUCKET_MEMBERS],
}

impl Default for KaBucket {
    fn default() -> Self {
        KaBucket { key: 0, count: 0, ids: [0; KA_MAX_BUCKET_MEMBERS] }
    }
}

use crate::state::KaBattle;

impl KaBattle {
    /// Identity of the first created entity; fighters occupy the preceding range.
    #[inline]
    pub fn created_base(&self) -> i32 {
        self.first_identity + self.count as i32
    }

    /// Roster index for a fighter identity.
    #[inline]
    pub fn fighter_slot(&self, id: i32) -> Option<usize> {
        let offset = id - self.first_identity;
        if offset >= 0 && (offset as u32) < self.count {
            Some(offset as usize)
        } else {
            None
        }
    }

    /// Arena index for a created-entity identity.
    #[inline]
    pub fn object_slot(&self, id: i32) -> Option<usize> {
        let offset = id - self.created_base();
        if offset >= 0 && (offset as u32) < self.object_count {
            Some(offset as usize)
        } else {
            None
        }
    }

    #[inline]
    pub fn entity(&self, id: i32) -> Option<&KaEntity> {
        if let Some(slot) = self.fighter_slot(id) {
            Some(&self.units[slot].body)
        } else {
            self.object_slot(id).map(|slot| &self.objects[slot])
        }
    }

    #[inline]
    pub fn entity_mut(&mut self, id: i32) -> Option<&mut KaEntity> {
        if let Some(slot) = self.fighter_slot(id) {
            Some(&mut self.units[slot].body)
        } else {
            self.object_slot(id).map(|slot| &mut self.objects[slot])
        }
    }

    /// `is_destroyed(identity)` - `objects[id]['flags'] & 2`.
    #[inline]
    pub fn is_destroyed(&self, id: i32) -> bool {
        self.entity(id).map(|entity| entity.destroyed != 0).unwrap_or(true)
    }

    /// `target_exists(entities, identity)`.
    #[inline]
    pub fn target_exists(&self, id: i32) -> bool {
        self.entity(id).is_some() && !self.is_destroyed(id)
    }

    /// Component presence for any entity.
    #[inline]
    pub fn has(&self, id: i32, slot: usize) -> bool {
        self.entity(id).map(|entity| entity.has[slot] != 0).unwrap_or(false)
    }

    /// `ComponentSubset.matches`.
    fn subset_matches(&self, subset: usize, id: i32) -> bool {
        let entity = match self.entity(id) {
            Some(entity) => entity,
            None => return false,
        };
        SUBSET_REQUIRED[subset].iter().all(|slot| entity.has[*slot] != 0)
            && !SUBSET_EXCLUDED[subset].iter().any(|slot| entity.has[*slot] != 0)
    }

    /// `CombatEntities._component_event`: every subset recomputes membership.
    ///
    /// `cell_members` carries the occupancy subscribers (`on_added`/`on_removed`), and those fire
    /// only when the member was actually inserted or removed, exactly as `EntitySlotSet.add`/
    /// `remove` report.
    pub fn notify_subsets(&mut self, id: i32) {
        self.stat_notify_calls += 1;
        for subset in 0..KA_SUBSET_COUNT {
            self.stat_subset_checks += 1;
            let member = self.subset_matches(subset, id);
            if member {
                let inserted = self.subsets[subset].members.add(id);
                if inserted && subset == SUBSET_CELL_MEMBERS {
                    let cell = self.entity(id).map(|entity| entity.cell).unwrap_or([0, 0]);
                    self.occupancy_add(id, cell);
                }
            } else {
                let removed = self.subsets[subset].members.remove(id);
                if removed && subset == SUBSET_CELL_MEMBERS {
                    // `CellOccupancy.removed` reads the component that was just detached, which is
                    // still the entity's cell payload here.
                    let cell = self.entity(id).map(|entity| entity.cell).unwrap_or([0, 0]);
                    self.occupancy_remove(id, cell);
                }
            }
        }
    }

    /// `cell_key(x, y, map_width)`.
    #[inline]
    pub fn cell_key(&self, cell: [i32; 2]) -> i32 {
        let y_term = crate::rng::i32_of(cell[1] as i64 * self.map_width as i64);
        crate::rng::i32_of(cell[0] as i64 + crate::rng::i32_of(y_term as i64 * 2) as i64)
    }

    fn bucket_slot(&self, key: i32) -> Option<usize> {
        (0..self.bucket_count as usize).find(|&slot| self.buckets[slot].key == key)
    }

    /// `bucket_slot` with the profiling counters the harness reads back.
    fn bucket_slot_counted(&mut self, key: i32) -> Option<usize> {
        self.stat_bucket_lookups += 1;
        let count = self.bucket_count as usize;
        for slot in 0..count {
            self.stat_bucket_steps += 1;
            if self.buckets[slot].key == key {
                return Some(slot);
            }
        }
        None
    }

    /// `CellOccupancy.added`.
    pub fn occupancy_add(&mut self, id: i32, cell: [i32; 2]) {
        let key = self.cell_key(cell);
        let slot = match self.bucket_slot_counted(key) {
            Some(slot) => slot,
            None => {
                if self.bucket_count as usize >= KA_MAX_BUCKETS {
                    return;
                }
                let slot = self.bucket_count as usize;
                self.buckets[slot] = KaBucket { key, count: 0, ids: [0; KA_MAX_BUCKET_MEMBERS] };
                self.bucket_count += 1;
                slot
            }
        };
        let bucket = &mut self.buckets[slot];
        if (bucket.count as usize) < KA_MAX_BUCKET_MEMBERS {
            bucket.ids[bucket.count as usize] = id;
            bucket.count += 1;
        }
    }

    /// `CellOccupancy._remove` - removes only the first match.
    pub fn occupancy_remove(&mut self, id: i32, cell: [i32; 2]) {
        let key = self.cell_key(cell);
        if let Some(slot) = self.bucket_slot_counted(key) {
            let bucket = &mut self.buckets[slot];
            if let Some(position) = (0..bucket.count as usize).find(|&i| bucket.ids[i] == id) {
                for index in position + 1..bucket.count as usize {
                    bucket.ids[index - 1] = bucket.ids[index];
                }
                bucket.count -= 1;
            }
        }
    }

    /// `CombatEntities.allocate` -> `create_entity`, then the factory's blank entity.
    pub fn allocate(&mut self) -> i32 {
        let id = self.next_identity;
        if self.object_count as usize >= KA_MAX_OBJECTS {
            return id;
        }
        self.next_identity = crate::rng::i32_of(self.next_identity as i64 + 1);
        let slot = self.object_count as usize;
        let mut entity = KaEntity::default();
        entity.id = id;
        self.objects[slot] = entity;
        self.object_count += 1;
        id
    }

    /// `CombatEntities.add_component`: an occupied slot is silently ignored.
    pub fn add_component(&mut self, id: i32, slot: usize) -> bool {
        {
            let entity = match self.entity_mut(id) {
                Some(entity) => entity,
                None => return false,
            };
            if entity.has[slot] != 0 {
                return false;
            }
            entity.has[slot] = 1;
        }
        self.notify_subsets(id);
        true
    }

    /// `CombatEntities.remove_component`.
    pub fn remove_component(&mut self, id: i32, slot: usize) -> bool {
        {
            let entity = match self.entity_mut(id) {
                Some(entity) => entity,
                None => return false,
            };
            if entity.has[slot] == 0 {
                return false;
            }
            entity.has[slot] = 0;
        }
        self.notify_subsets(id);
        true
    }

    /// `CombatEntities.destroy` -> `destroy_entity`, including the synchronous removal events.
    pub fn destroy(&mut self, id: i32) {
        if self.is_destroyed(id) {
            return;
        }
        for slot in 0..KA_SLOT_COUNT {
            if self.has(id, slot) {
                self.remove_component(id, slot);
            }
        }
        if let Some(entity) = self.entity_mut(id) {
            entity.destroyed = 1;
        }
        self.push_event(crate::state::KA_EVENT_DESTROY, id, 0, 0, 0, 0, 0);
    }

    /// `CellOccupancy.changed`.
    pub fn occupancy_changed(&mut self, id: i32, old_key: i32) {
        if let Some(slot) = self.bucket_slot_counted(old_key) {
            let bucket = &mut self.buckets[slot];
            if let Some(position) = (0..bucket.count as usize).find(|&i| bucket.ids[i] == id) {
                for index in position + 1..bucket.count as usize {
                    bucket.ids[index - 1] = bucket.ids[index];
                }
                bucket.count -= 1;
            }
        }
        let cell = match self.entity(id) {
            Some(entity) => entity.cell,
            None => return,
        };
        self.occupancy_add(id, cell);
    }

    /// Serialize one entity's present components into the comparison surface.
    pub fn export_components(&self, id: i32, out: &mut [KaComponentView], capacity: usize) -> usize {
        let entity = match self.entity(id) {
            Some(entity) => entity,
            None => return 0,
        };
        let mut len = 0usize;
        for slot in 0..KA_SLOT_COUNT {
            if entity.has[slot] == 0 || len >= capacity {
                continue;
            }
            let mut view = KaComponentView { slot: slot as i32, ..Default::default() };
            match slot {
                SLOT_POSITION => {
                    view.floats[0] = entity.position.x;
                    view.floats[1] = entity.position.y;
                    view.floats[2] = entity.position.z;
                    view.floats[3] = entity.offset.x;
                    view.floats[4] = entity.offset.y;
                    view.floats[5] = entity.offset.z;
                    view.ints[0] = entity.parent;
                }
                SLOT_SPEED => {
                    view.floats[0] = entity.speed.x;
                    view.floats[1] = entity.speed.y;
                    view.floats[2] = entity.speed.z;
                }
                SLOT_SEB => view.ints[..4].copy_from_slice(&entity.seb),
                SLOT_DEPTH => view.ints[..2].copy_from_slice(&entity.depth),
                SLOT_CELL => view.ints[..2].copy_from_slice(&entity.cell),
                SLOT_IMAGE => view.ints[..6].copy_from_slice(&entity.image),
                SLOT_ANIMATION => view.ints[..2].copy_from_slice(&entity.animation),
                SLOT_DIRECTION => view.ints[0] = entity.direction,
                SLOT_MODIFY_ANIMATION => {
                    let m = &entity.modifier;
                    view.ints[0] = m.type_;
                    view.ints[1] = m.angle;
                    view.ints[2] = m.anchor;
                    view.ints[3] = m.frame;
                    view.ints[4] = m.duration;
                    view.ints[5] = m.destroy_on_finish as i32;
                    view.ints[6] = m.looping as i32;
                    view.ints[7] = m.alpha;
                    view.floats[0] = m.offset_x;
                    view.floats[1] = m.offset_y;
                    view.floats[2] = m.offset_z;
                    view.floats[3] = m.scale_x;
                    view.floats[4] = m.scale_y;
                }
                SLOT_GARBAGE => view.ints[0] = entity.garbage,
                SLOT_EFFECT => {
                    let e = &entity.effect;
                    view.ints[0] = e.type_;
                    view.ints[1] = e.value1;
                    view.ints[2] = e.value2;
                    view.ints[3] = e.depth as i32;
                    view.ints[4] = e.frame;
                    view.ints[5] = e.max_frame;
                    view.ints[6] = e.parent;
                    view.ints[7] = e.scale;
                }
                SLOT_PROJECTILE => {
                    let p = &entity.projectile;
                    view.ints[0] = p.speed;
                    view.ints[1] = p.height;
                    view.ints[2] = p.frame;
                    view.ints[3] = p.length;
                    view.ints[4] = p.owner;
                    view.floats[0] = p.start.x;
                    view.floats[1] = p.start.y;
                    view.floats[2] = p.start.z;
                    view.floats[3] = p.end.x;
                    view.floats[4] = p.end.y;
                    view.floats[5] = p.end.z;
                }
                SLOT_ATTACK => view.ints[0] = entity.attack,
                _ => {}
            }
            out[len] = view;
            len += 1;
        }
        len
    }
}
