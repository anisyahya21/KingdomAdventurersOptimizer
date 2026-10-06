//! The recovered `System.Random` subtractive backend, as the math/lib streams the
//! combat phases draw from.
//!
//! The Python reference is `combat_resolution.SystemRandomState`:
//!
//! ```text
//! seed = i32(seed)
//! magnitude = 2147483647 if seed == -2147483648 else abs(seed)
//! previous = i32(161803398 - magnitude)
//! values[55] = previous; current = 1; index = 0
//! repeat 54: index = (index + 21) % 55; values[index] = current
//!            difference = i32(previous - current)
//!            if difference < 0: difference = i32(difference + 2147483647)
//!            previous, current = current, difference
//! repeat 4: for index in 1..55:
//!     value = i32(values[index] - values[1 + (index + 30) % 55])
//!     values[index] = i32(value + 2147483647) if value < 0 else value
//! index, partner = 0, 21
//! ```
//!
//! Every step is signed 32-bit with the same wrapping the Python `i32()` performs, so
//! the draw sequence is reproduced exactly rather than statistically.

/// Number of state words in the subtractive generator.
pub const KA_RNG_VALUES: usize = 56;

/// One recovered `SystemRandomState`, owned by the native battle.
#[repr(C)]
#[derive(Clone, Copy)]
pub struct KaRandom {
    /// `values[0]` is unused by the generator exactly as in the Python; the table is 1-based.
    pub values: [i32; KA_RNG_VALUES],
    pub index: i32,
    pub partner: i32,
}

impl Default for KaRandom {
    fn default() -> Self {
        KaRandom { values: [0; KA_RNG_VALUES], index: 0, partner: 21 }
    }
}

/// `i32(value)` - the two's-complement wrap the Python helper performs.
#[inline(always)]
pub fn i32_of(value: i64) -> i32 {
    value as i32
}

impl KaRandom {
    /// `SystemRandomState.__init__(seed)`.
    pub fn seeded(seed: i32) -> Self {
        let magnitude: i32 = if seed == i32::MIN { i32::MAX } else { seed.abs() };
        let mut previous = i32_of(161803398i64 - magnitude as i64);
        let mut state = KaRandom::default();
        state.values[55] = previous;
        let mut current: i32 = 1;
        let mut index: usize = 0;
        for _ in 0..54 {
            index = (index + 21) % 55;
            state.values[index] = current;
            let mut difference = i32_of(previous as i64 - current as i64);
            if difference < 0 {
                difference = i32_of(difference as i64 + 2147483647);
            }
            previous = current;
            current = difference;
        }
        for _ in 0..4 {
            for index in 1..=55usize {
                let value = i32_of(state.values[index] as i64 - state.values[1 + (index + 30) % 55] as i64);
                state.values[index] = if value < 0 { i32_of(value as i64 + 2147483647) } else { value };
            }
        }
        state.index = 0;
        state.partner = 21;
        state
    }

    /// `SystemRandomState.next_int()`.
    pub fn next_int(&mut self) -> i32 {
        self.index = if self.index < 55 { self.index + 1 } else { 1 };
        self.partner = if self.partner < 55 { self.partner + 1 } else { 1 };
        let mut value = i32_of(self.values[self.index as usize] as i64 - self.values[self.partner as usize] as i64);
        if value == 2147483647 {
            value -= 1;
        } else if value < 0 {
            value = i32_of(value as i64 + 2147483647);
        }
        self.values[self.index as usize] = value;
        value
    }
}

/// The exact `index`/`partner` cursor, so a differential harness can pin draw position
/// (the trailing 56-word table itself is compared through [`KaRandom::values`]).
#[no_mangle]
pub unsafe extern "C" fn ka_rng_index(pointer: *const KaRandom) -> i32 {
    if pointer.is_null() {
        return 0;
    }
    (*pointer).index
}

#[no_mangle]
pub unsafe extern "C" fn ka_rng_partner(pointer: *const KaRandom) -> i32 {
    if pointer.is_null() {
        return 0;
    }
    (*pointer).partner
}

/// Expose one state word, so the harness can compare the whole table without a struct
/// dependency on the array layout.
#[no_mangle]
pub unsafe extern "C" fn ka_rng_value(pointer: *const KaRandom, index: u32) -> i32 {
    if pointer.is_null() || index as usize >= KA_RNG_VALUES {
        return 0;
    }
    (*pointer).values[index as usize]
}
