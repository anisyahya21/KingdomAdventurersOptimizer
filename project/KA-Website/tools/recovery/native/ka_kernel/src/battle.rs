//! The spatial/targeting slice's semantics, expressed on the native-owned battle state.
//!
//! These are the three recovered reads the original prototype measured, now reading the board model
//! in [`crate::state`] instead of the prototype's flat `state`/`grid`/`hp` mirror fields:
//!
//! ```text
//! SharedControllers.distance(a, b)          ->  abs(l[0]-r[0]) + abs(l[1]-r[1])
//! combat_navigation.same_grid(unit, team)   ->  any(other is not unit
//!                                                   and other.board[5] not in (2, 7, 8)
//!                                                   and other.board[7] == unit.board[7]
//!                                                   for other in team)
//! front-opponent eligibility                ->  exists and hp > 0 and state not in (7, 8)
//! ```
//!
//! The spatial phase's team filter is the caller-supplied `query_team` rather than the roster list,
//! exactly as `ka_nearest_eligible` takes it, so the two entry points agree.

use crate::state::KaBattle;

impl KaBattle {
    /// `SharedControllers.distance`: `abs(l[0]-r[0]) + abs(l[1]-r[1])` over cell coordinates.
    #[inline(always)]
    pub fn distance(&self, a: usize, b: usize) -> i32 {
        let left = &self.units[a];
        let right = &self.units[b];
        (left.cell_x() - right.cell_x()).abs() + (left.cell_y() - right.cell_y()).abs()
    }

    /// The eligibility filter of the front-opponent scan plus `same_team`:
    /// `exists and hp > 0 and state not in (7, 8)`.
    ///
    /// `exists` is `target_exists`, so it is the `exists` field and not the roster flag.
    #[inline(always)]
    pub fn is_eligible(&self, index: usize, query_team: u8) -> bool {
        let unit = &self.units[index];
        unit.present != 0
            && unit.exists()
            && unit.team != query_team
            && unit.hp_value() > 0
            && unit.state() != 7
            && unit.state() != 8
    }

    /// `combat_navigation.same_grid(unit, team)`: another unit on the same board grid that is not
    /// Moving(2), KnockingDown(7) or Leaving(8).
    #[inline(always)]
    pub fn same_grid(&self, query: usize) -> bool {
        let grid = self.units[query].grid();
        for index in 0..self.count as usize {
            if index == query {
                continue;
            }
            let other = &self.units[index];
            let state = other.state();
            if state != 2 && state != 7 && state != 8 && other.grid() == grid {
                return true;
            }
        }
        false
    }
}
