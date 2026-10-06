//! Effective fighter parameters and raw parameter mutation.
//!
//! Transcribed from `combat_parameters.fighter_parameter` / `equipment_contribution` and
//! `combat_resolution.{equipment_parameter, affinity_contribution, subtract_raw_parameter,
//! add_raw_parameter}`. The recovered shape matters:
//!
//! * `bounded = rawMax != 2147483647`, and the equipment loop runs only when
//!   `maximum == bounded`. For a bounded parameter that means equipment raises the *maximum* and
//!   leaves the current value alone - so HP/MP current values carry no equipment term, but their
//!   caps do. That is transcribed, not "fixed".
//! * `equipment_level` returns `pvpLevel` for a human on the non-ally team, and only then does
//!   `equipment_contribution` prefer a positive selected level over the row level.
//! * `Parameter.Sub`/`Add` clamp only when the stored maximum is finite.

use crate::rng::i32_of;

pub const KA_MAX_PARAMS: usize = 16;
pub const KA_MAX_EQUIPMENT: usize = 8;
pub const KA_MAX_PARAM_PAIRS: usize = 16;

/// Sentinel the engine stores for "no finite maximum".
pub const KA_UNBOUNDED: i32 = i32::MAX;
/// `combat_parameters.HUMAN_TRAINING_PARAMETERS`.
pub const KA_TRAINING_PARAMS: [i32; 12] =
    [10, 11, 12, 13, 14, 15, 16, 18, 19, 20, 21, 22];

/// One `Parameter` record: clones of the serialized fields the math reads.
#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct KaParam {
    pub id: i32,
    pub raw_value: i32,
    pub extra_value: i32,
    pub raw_max: i32,
    pub extra_max: i32,
    pub training_level: i32,
}

/// One `equipmentRows` entry with the `parameters` list flattened.
#[repr(C)]
#[derive(Clone, Copy)]
pub struct KaEquipRow {
    pub level: i32,
    pub pvp_level: i32,
    pub affinity: i32,
    /// Length of the row's `parameters` list; indices beyond it contribute nothing.
    pub pair_count: i32,
    /// `(base, growth)` per parameter slot; `present == 0` stands for the Python `None` entry.
    pub pairs: [[i32; 2]; KA_MAX_PARAM_PAIRS],
    pub present: [u8; KA_MAX_PARAM_PAIRS],
}

impl Default for KaEquipRow {
    fn default() -> Self {
        KaEquipRow {
            level: 0,
            pvp_level: 0,
            affinity: 0,
            pair_count: 0,
            pairs: [[0; 2]; KA_MAX_PARAM_PAIRS],
            present: [0; KA_MAX_PARAM_PAIRS],
        }
    }
}

/// `combat_resolution.equipment_parameter`.
#[inline]
fn equipment_parameter(base: i32, growth: i32, level: i32) -> i32 {
    i32_of(base as i64 + i32_of(growth as i64 * i32_of(level as i64 - 1) as i64) as i64)
}

/// `combat_resolution.affinity_contribution`.
#[inline]
fn affinity_contribution(value: i32, affinity: i32, alive_human: bool) -> i32 {
    if !alive_human || affinity != 0 {
        return value;
    }
    if value > 0 {
        (value / 2).max(1)
    } else {
        0
    }
}

/// `combat_parameters.equipment_level` with `owner_levels is None`, the controller's case.
#[inline]
fn equipment_level(row: &KaEquipRow, human: bool, ally: bool) -> i32 {
    if human && !ally {
        row.pvp_level
    } else {
        row.level
    }
}

/// `combat_parameters.equipment_contribution`.
fn equipment_contribution(
    row: &KaEquipRow,
    parameter_id: i32,
    selected_level: i32,
    human: bool,
) -> i32 {
    let index = i32_of(parameter_id as i64 - 10);
    if index < 0 || index >= row.pair_count {
        return 0;
    }
    let level = if selected_level > 0 { selected_level } else { row.level };
    let slot = index as usize;
    let value = if row.present[slot] != 0 {
        equipment_parameter(row.pairs[slot][0], row.pairs[slot][1], level)
    } else {
        0
    };
    affinity_contribution(value, row.affinity, human)
}

/// The parameter lookup table of one fighter.
#[repr(C)]
#[derive(Clone, Copy)]
pub struct KaParams {
    pub count: u32,
    pub rows: [KaParam; KA_MAX_PARAMS],
    pub equipment_count: u32,
    pub equipment: [KaEquipRow; KA_MAX_EQUIPMENT],
}

impl Default for KaParams {
    fn default() -> Self {
        KaParams {
            count: 0,
            rows: [KaParam::default(); KA_MAX_PARAMS],
            equipment_count: 0,
            equipment: [KaEquipRow::default(); KA_MAX_EQUIPMENT],
        }
    }
}

impl KaParams {
    #[inline]
    pub fn find(&self, id: i32) -> Option<&KaParam> {
        self.rows[..self.count as usize].iter().find(|row| row.id == id)
    }

    #[inline]
    pub fn find_mut(&mut self, id: i32) -> Option<&mut KaParam> {
        self.rows[..self.count as usize].iter_mut().find(|row| row.id == id)
    }

    /// `SharedControllers.value(i, p, default)` -> `effective(i, p, maximum=False, default)`.
    pub fn value(&self, id: i32, human: bool, ally: bool, default: i32) -> i32 {
        self.effective(id, human, ally, false, default)
    }

    /// `SharedControllers.maximum(i, p)`.
    pub fn maximum(&self, id: i32, human: bool, ally: bool) -> i32 {
        self.effective(id, human, ally, true, 0)
    }

    /// `combat_parameters.fighter_parameter` with `exists=True` and no owner levels.
    pub fn effective(&self, id: i32, human: bool, ally: bool, maximum: bool, default: i32) -> i32 {
        let parameter = match self.find(id) {
            Some(row) => *row,
            None => return default,
        };
        let bounded = parameter.raw_max != KA_UNBOUNDED;
        if maximum && !bounded {
            return KA_UNBOUNDED;
        }
        let mut value = if maximum {
            i32_of(parameter.raw_max as i64 + parameter.extra_max as i64)
        } else {
            i32_of(parameter.raw_value as i64 + parameter.extra_value as i64)
        };
        if maximum == bounded {
            for slot in 0..self.equipment_count as usize {
                let row = &self.equipment[slot];
                let level = equipment_level(row, human, ally);
                value = i32_of(value as i64 + equipment_contribution(row, id, level, human) as i64);
            }
        }
        if maximum || id == 25 {
            return value;
        }
        let upper = self.effective(id, human, ally, true, default);
        value.max(0).min(upper)
    }

    /// `combat_parameters.average_training_level` over the human parameter group.
    pub fn average_training_level(&self, human: bool) -> i32 {
        if !human {
            return 1;
        }
        let mut total: i32 = 0;
        for id in KA_TRAINING_PARAMS {
            let level = self.find(id).map(|row| row.training_level).unwrap_or(0);
            total = i32_of(total as i64 + level as i64);
        }
        let average = crate::ai::trunc_div(total, KA_TRAINING_PARAMS.len() as i32);
        average.max(1)
    }

    /// `combat_resolution.subtract_raw_parameter` (`Parameter.Sub`).
    pub fn subtract_raw(&mut self, id: i32, amount: i32) -> Option<(i32, i32)> {
        let row = self.find_mut(id)?;
        let difference = i32_of(row.raw_value as i64 - amount as i64);
        let value = if row.raw_max == KA_UNBOUNDED { difference } else { difference.max(0) };
        row.raw_value = value;
        Some((value, i32_of(value as i64 - difference as i64)))
    }

    /// `combat_resolution.add_raw_parameter` (`Parameter.Add`).
    pub fn add_raw(&mut self, id: i32, amount: i32, effective_maximum: i32) -> Option<(i32, i32)> {
        let row = self.find_mut(id)?;
        let total = i32_of(row.raw_value as i64 + amount as i64);
        let value = if effective_maximum > 0 { total.max(0).min(effective_maximum) } else { total };
        row.raw_value = value;
        Some((value, i32_of(total as i64 - value as i64)))
    }
}
