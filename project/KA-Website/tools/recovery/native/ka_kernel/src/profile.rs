//! Optional phase timings for the bulk battle path. Build in a separate target directory.
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Duration;

pub const PHASES: usize = 12;
static NANOS: [AtomicU64; PHASES] = [const { AtomicU64::new(0) }; PHASES];

#[inline]
pub fn record(phase: usize, elapsed: Duration) {
    NANOS[phase].fetch_add(elapsed.as_nanos().min(u64::MAX as u128) as u64, Ordering::Relaxed);
}

#[no_mangle]
pub unsafe extern "C" fn ka_profile_read(out: *mut u64, len: usize) -> i32 {
    if out.is_null() || len < PHASES { return -1; }
    for (i, value) in NANOS.iter().enumerate() {
        *out.add(i) = value.load(Ordering::Relaxed);
    }
    0
}

#[no_mangle]
pub extern "C" fn ka_profile_reset() {
    for value in NANOS.iter() { value.store(0, Ordering::Relaxed); }
}
