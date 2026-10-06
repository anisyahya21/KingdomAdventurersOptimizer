"""The opt-in, versioned encounter-telemetry ABI (`ka_kernel_encounter_v3.dll`).

This is deliberately separate from `ka_abi.py`. The default `KaBattleReport` layout, the versioned
`ka_kernel_v9.dll` selection and every legacy run are untouched: the encounter counters live in the
same Rust source but are published only through the additional `ka_encounter_report` getter, and
only in a *separately named* DLL. A `telemetry=True` run selects this DLL and requires the getter;
a missing DLL or getter is an explicit error, never a silent slow Python fallback.

Version 3 is a *new filename* (`ka_kernel_encounter_v3.dll`, `..._v3_large.dll`) because the report
struct gained fields again: the death-snapshot future-hit mass (distinct from the remaining command
count) and its currently-executing-hit inclusion, the compact post-death actual-hit gap summary,
event-site boss Leaving entries and per-tick Damaging resets, boss-access first-command/first-attempt
ticks and per-tick targetable/UsingSkill occupancy. A process still holding v1/v2 can never be served
the v3 ABI: the loader checks `ka_sizeof_encounter_report`, the `ka_encounter_version` integer and
the DLL's own SHA-256 (the build identity a cached template is bound to). The v1/v2 files are left on
disk unchanged.

`encounter_dll_path()` resolves `KA_ENCOUNTER_DLL` first, then the versioned filename, then the
`_large` variant. `load()` reuses `ka_abi.load` (the encounter DLL is a superset of the kernel) and
refuses a mirror whose struct size, version integer or file hash disagrees.
"""
import ctypes
import hashlib
import os
from pathlib import Path

import ka_abi

#: How many `KaEncounterReport` own-slot rows exist; matches `state::KA_MAX_UNITS`.
KA_MAX_UNITS = ka_abi.KA_MAX_UNITS
#: Schema version the Python mirror understands; matches `battle_control::KA_ENCOUNTER_VERSION`.
ENCOUNTER_VERSION = 3
#: The Rust source file whose HMAC-style build identity this ABI is tied to; its SHA-256 is recorded
#: next to each cached template so a template is never cloned by a different build.
SOURCE_NAME = 'battle_control.rs'

_release_dir = ka_abi.HERE / 'native' / 'ka_kernel' / 'target' / 'release'


class EncounterKernelUnavailable(RuntimeError):
    """The versioned encounter kernel (or its getter) is not available."""


class KaEncounterReport(ctypes.Structure):
    """Mirror of `battle_control::KaEncounterReport` (field order is the ABI)."""
    _fields_ = [
        ('version', ctypes.c_int32),
        ('own_count', ctypes.c_int32),
        ('own_identity', ctypes.c_int32 * KA_MAX_UNITS),
        ('own_first_death_tick', ctypes.c_int32 * KA_MAX_UNITS),
        ('own_first_leaving_tick', ctypes.c_int32 * KA_MAX_UNITS),
        ('own_survived', ctypes.c_int32 * KA_MAX_UNITS),
        ('enemy_resolved_attacks', ctypes.c_int32),
        ('enemy_hits', ctypes.c_int32),
        ('enemy_misses', ctypes.c_int32),
        ('enemy_rolls', ctypes.c_int32),
        ('enemy_roll_hits', ctypes.c_int32),
        ('enemy_roll_misses', ctypes.c_int32),
        ('counter_checks', ctypes.c_int32),
        ('counter_enqueues', ctypes.c_int32),
        ('boss_postdeath_attempts', ctypes.c_int32),
        ('boss_postdeath_lands', ctypes.c_int32),
        ('boss_death_tick', ctypes.c_int32),
        ('boss_reentries', ctypes.c_int32),
        ('boss_leavings', ctypes.c_int32),
        ('boss_postdeath_gap_count', ctypes.c_int32),
        ('boss_postdeath_gap_min', ctypes.c_int32),
        ('boss_postdeath_gap_max', ctypes.c_int32),
        ('boss_postdeath_gap_sum', ctypes.c_int32),
        ('future_hits_at_death', ctypes.c_int32),
        ('future_hits_include_executing', ctypes.c_int32),
        ('boss_access_first_command', ctypes.c_int32),
        ('boss_access_first_attempt', ctypes.c_int32),
        ('targetable_ticks', ctypes.c_int32),
        ('using_skill_ticks', ctypes.c_int32),
        ('boss_damaging_resets', ctypes.c_int32),
        ('item_uses_ok', ctypes.c_int32),
        ('finish_dispatched_chests', ctypes.c_int32),
    ]


def encounter_dll_path(large=False):
    override = os.environ.get('KA_ENCOUNTER_DLL')
    if override:
        return Path(override)
    name = 'ka_kernel_encounter_v3_large.dll' if large else 'ka_kernel_encounter_v3.dll'
    return _release_dir / name


def file_sha256(path):
    """The DLL file's SHA-256; the concrete build identity a cached template is bound to."""
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def load(dll_path=None):
    """Load the encounter kernel and bind `ka_encounter_report`; raise if it is not available.

    Three independent guards, because a same-size struct is not proof of the same ABI: the native
    `ka_sizeof_encounter_report` must equal the Python mirror, the `ka_encounter_version` integer
    must equal `ENCOUNTER_VERSION`, and the DLL's own SHA-256 is exposed so callers can pin it.
    """
    path = Path(dll_path) if dll_path else encounter_dll_path()
    if not path.is_file():
        raise EncounterKernelUnavailable(f'encounter kernel is not built at {path}')
    try:
        library = ka_abi.load(path)
    except SystemExit as error:  # ka_abi fails closed on a report-layout mismatch
        raise EncounterKernelUnavailable(str(error)) from error
    getter = getattr(library, 'ka_encounter_report', None)
    sizeof = getattr(library, 'ka_sizeof_encounter_report', None)
    version = getattr(library, 'ka_encounter_version', None)
    if getter is None or sizeof is None or version is None:
        raise EncounterKernelUnavailable(
            f'{path.name} does not export the encounter getter, sizeof and version probes')
    getter.argtypes = [ctypes.c_void_p, ctypes.c_int32, ctypes.POINTER(KaEncounterReport)]
    getter.restype = ctypes.c_int32
    sizeof.argtypes = []
    sizeof.restype = ctypes.c_uint32
    version.argtypes = []
    version.restype = ctypes.c_uint32
    if sizeof() != ctypes.sizeof(KaEncounterReport):
        raise EncounterKernelUnavailable(
            f'encounter ABI mismatch: native {sizeof()} != python '
            f'{ctypes.sizeof(KaEncounterReport)}')
    if int(version()) != ENCOUNTER_VERSION:
        raise EncounterKernelUnavailable(
            f'encounter version mismatch: native {int(version())} != python {ENCOUNTER_VERSION}')
    library.encounter_abi_version = ENCOUNTER_VERSION
    library.encounter_sha256 = file_sha256(path)
    return library


def read(library, battle, policy):
    """Call the getter for one finished battle; return a plain, JSON-serializable dict."""
    report = KaEncounterReport()
    status = library.ka_encounter_report(battle, int(policy), ctypes.byref(report))
    if status != 0:
        raise EncounterKernelUnavailable(f'ka_encounter_report returned {status}')
    if int(report.version) != ENCOUNTER_VERSION:
        raise EncounterKernelUnavailable(
            f'encounter report version {int(report.version)} != {ENCOUNTER_VERSION}')
    count = max(0, min(int(report.own_count), KA_MAX_UNITS))
    own = [dict(identity=int(report.own_identity[slot]),
                firstDeathTick=int(report.own_first_death_tick[slot]),
                firstLeavingTick=int(report.own_first_leaving_tick[slot]),
                survived=bool(report.own_survived[slot])) for slot in range(count)]
    return dict(
        version=int(report.version),
        own=own,
        enemyResolvedAttacks=int(report.enemy_resolved_attacks),
        enemyHits=int(report.enemy_hits),
        enemyMisses=int(report.enemy_misses),
        enemyRolls=int(report.enemy_rolls),
        enemyRollHits=int(report.enemy_roll_hits),
        enemyRollMisses=int(report.enemy_roll_misses),
        counterChecks=int(report.counter_checks),
        counterEnqueues=int(report.counter_enqueues),
        bossPostdeathAttempts=int(report.boss_postdeath_attempts),
        bossPostdeathLands=int(report.boss_postdeath_lands),
        bossDeathTick=int(report.boss_death_tick),
        bossReentries=int(report.boss_reentries),
        bossLeavings=int(report.boss_leavings),
        bossPostdeathGapCount=int(report.boss_postdeath_gap_count),
        bossPostdeathGapMin=int(report.boss_postdeath_gap_min),
        bossPostdeathGapMax=int(report.boss_postdeath_gap_max),
        bossPostdeathGapSum=int(report.boss_postdeath_gap_sum),
        futureHitsAtDeath=int(report.future_hits_at_death),
        futureHitsIncludeExecuting=int(report.future_hits_include_executing),
        bossAccessFirstCommand=int(report.boss_access_first_command),
        bossAccessFirstAttempt=int(report.boss_access_first_attempt),
        targetableTicks=int(report.targetable_ticks),
        usingSkillTicks=int(report.using_skill_ticks),
        bossDamagingResets=int(report.boss_damaging_resets),
        itemUsesOk=int(report.item_uses_ok),
        finishDispatchedChests=int(report.finish_dispatched_chests))


if __name__ == '__main__':  # tiny self-check: size + version + getter presence, no battle run
    lib = load()
    print(f"encounter kernel ok: {encounter_dll_path().name} "
          f"version={lib.encounter_abi_version} sizeof={ctypes.sizeof(KaEncounterReport)} "
          f"sha256={lib.encounter_sha256[:12]}")
