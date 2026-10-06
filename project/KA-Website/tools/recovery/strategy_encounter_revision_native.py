"""Attested native enemy-revision projection with canonical Python fallback.

This module validates scenarios through
the existing Python normalizer, then uses a separate Rust library only to reproduce the enemy rows
needed by ``strategy_mechanics.encounter_revision``. Python remains the canonical JSON/SHA producer.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import sys
import threading
from pathlib import Path

import combat_runtime_data
import strategy_mechanics as mechanics

_I32_MIN = -(1 << 31)
_I32_MAX = (1 << 31) - 1
_DATA_NAMES = ('encounters.json', 'weapon-skill-profiles.json')
_PARAMETER_IDS = (10, 11, 13, 14, 15, 16, 19)
_CONTEXT_LOCK = threading.RLock()
_LIBRARY_LOCK = threading.Lock()
_CONTEXT = None
_LIBRARY = None


class _EncounterInput(ctypes.Structure):
    _fields_ = [('id', ctypes.c_int32), ('level_field', ctypes.c_int32),
                ('boss_id', ctypes.c_int32), ('follower_start', ctypes.c_uint32),
                ('follower_count', ctypes.c_uint32)]


class _FollowerInput(ctypes.Structure):
    _fields_ = [('monster_id', ctypes.c_int32), ('check_rate', ctypes.c_int32)]


class _MonsterInput(ctypes.Structure):
    _fields_ = [('id', ctypes.c_int32), ('skill_id', ctypes.c_int32),
                ('curves', (ctypes.c_int32 * 4) * 7)]


class _EnemyRow(ctypes.Structure):
    _fields_ = [('monster_id', ctypes.c_int32), ('boss', ctypes.c_uint8),
                ('reserved', ctypes.c_uint8 * 3), ('skill_id', ctypes.c_int32),
                ('parameters', ctypes.c_int32 * 7)]


class NativeUnavailable(RuntimeError):
    """The optimization cannot safely answer; caller must use the canonical Python compiler."""


def _library_path() -> Path:
    requested = os.environ.get('KA_REVISION_LIBRARY')
    if requested:
        return Path(requested).resolve()
    native = Path(__file__).resolve().parent / 'native' / 'ka_revision' / 'target' / 'release'
    if sys.platform == 'win32':
        return native / 'ka_revision.dll'
    if sys.platform == 'darwin':
        return native / 'libka_revision.dylib'
    return native / 'libka_revision.so'


def _load_library():
    global _LIBRARY
    with _LIBRARY_LOCK:
        if _LIBRARY is not None:
            return _LIBRARY
    path = _library_path()
    if not path.is_file():
        raise NativeUnavailable(f'native revision library is not built: {path}')
    lib = ctypes.CDLL(str(path))
    lib._ka_revision_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    lib.ka_revision_abi_version.argtypes = []
    lib.ka_revision_abi_version.restype = ctypes.c_uint32
    if lib.ka_revision_abi_version() != 1:
        raise NativeUnavailable('native revision library ABI version mismatch')
    lib.ka_encounter_ctx_open.argtypes = [
        ctypes.POINTER(_EncounterInput), ctypes.c_uint32,
        ctypes.POINTER(_FollowerInput), ctypes.c_uint32,
        ctypes.POINTER(_MonsterInput), ctypes.c_uint32,
    ]
    lib.ka_encounter_ctx_open.restype = ctypes.c_void_p
    lib.ka_encounter_ctx_close.argtypes = [ctypes.c_void_p]
    lib.ka_encounter_ctx_close.restype = None
    lib.ka_encounter_ctx_max_rows.argtypes = [ctypes.c_void_p]
    lib.ka_encounter_ctx_max_rows.restype = ctypes.c_uint32
    lib.ka_encounter_roster.argtypes = [
        ctypes.c_void_p, ctypes.c_int32, ctypes.c_int32, ctypes.c_int32,
        ctypes.POINTER(_EnemyRow), ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_int32),
        ctypes.POINTER(ctypes.c_uint32),
    ]
    lib.ka_encounter_roster.restype = ctypes.c_int32
    with _LIBRARY_LOCK:
        if _LIBRARY is None:
            _LIBRARY = lib
        return _LIBRARY


def _i32(value: int) -> int:
    return ((value + (1 << 31)) & 0xFFFFFFFF) - (1 << 31)


def _checked_i32(value, field: str) -> int:
    if type(value) is not int or not _I32_MIN <= value <= _I32_MAX:
        raise NativeUnavailable(f'{field} is outside the native i32 table domain')
    return value


def _read_current_data():
    """Read and fingerprint every source file used by scenario validation or native rows."""
    raw_by_name = {}
    digest = hashlib.sha256(b'ka-encounter-revision-data-v1\0')
    for name in _DATA_NAMES:
        raw = combat_runtime_data.data_path(name).read_bytes()
        raw_by_name[name] = raw
        digest.update(name.encode('utf-8') + b'\0')
        digest.update(len(raw).to_bytes(8, 'little'))
        digest.update(raw)
    return digest.digest(), raw_by_name


def _context_from_data(encounter_data: dict, attestation: bytes, library=None):
    encounters = encounter_data['encounters']
    monsters = encounter_data['monsters']
    if (not isinstance(encounters, list) or not encounters
            or not isinstance(monsters, list) or not monsters):
        raise NativeUnavailable('encounter data tables are empty or malformed')

    follower_rows = []
    encounter_rows = []
    encounter_ids = set()
    for encounter in encounters:
        eid = _checked_i32(encounter.get('id'), 'encounter id')
        if eid in encounter_ids:
            raise NativeUnavailable('duplicate encounter id')
        encounter_ids.add(eid)
        level_field = _checked_i32(encounter.get('levelField'), 'levelField')
        boss_id = _checked_i32(encounter.get('bossId'), 'bossId')
        followers = encounter.get('followers')
        if not isinstance(followers, list):
            raise NativeUnavailable('followers must be a list')
        start = len(follower_rows)
        for follower in followers:
            rate = _checked_i32(follower.get('checkRate'), 'follower checkRate')
            if not 0 <= rate <= 100:
                raise NativeUnavailable('follower checkRate is outside 0..100')
            follower_rows.append((
                _checked_i32(follower.get('monsterId'), 'follower monsterId'), rate))
        encounter_rows.append((eid, level_field, boss_id, start, len(followers)))

    monster_rows = []
    names = {}
    monster_ids = set()
    for monster in monsters:
        mid = _checked_i32(monster.get('id'), 'monster id')
        if mid in monster_ids:
            raise NativeUnavailable('duplicate monster id')
        monster_ids.add(mid)
        skill_id = _checked_i32(monster.get('skillId'), 'monster skillId')
        raw_curves = monster.get('parametersRaw')
        if not isinstance(raw_curves, list) or len(raw_curves) != 7:
            raise NativeUnavailable('monster parameter curves do not match canonical ids')
        curves = []
        for curve in raw_curves:
            if not isinstance(curve, list) or len(curve) != 4:
                raise NativeUnavailable('monster curve must have exactly four endpoints')
            curves.append([_checked_i32(value, 'monster curve endpoint') for value in curve])
        name = monster.get('name')
        if not isinstance(name, str):
            raise NativeUnavailable('monster name is not text')
        names[mid] = name
        monster_rows.append((mid, skill_id, curves))
    if any(row[2] not in monster_ids for row in encounter_rows):
        raise NativeUnavailable('boss monster is missing from source data')
    if any(mid not in monster_ids for mid, _rate in follower_rows):
        raise NativeUnavailable('follower monster is missing from source data')

    encounter_array = (_EncounterInput * len(encounter_rows))(*[
        _EncounterInput(eid, level, boss, start, count)
        for eid, level, boss, start, count in encounter_rows])
    follower_array = (_FollowerInput * len(follower_rows))(*[
        _FollowerInput(mid, rate) for mid, rate in follower_rows])
    monster_array = (_MonsterInput * len(monster_rows))(*[
        _MonsterInput(mid, skill, (ctypes.c_int32 * 4 * 7)(*[
            (ctypes.c_int32 * 4)(*curve) for curve in curves]))
        for mid, skill, curves in monster_rows])
    library = library or _load_library()
    attestation = bytes(attestation) + bytes.fromhex(library._ka_revision_sha256)
    context_ptr = library.ka_encounter_ctx_open(
        encounter_array, len(encounter_rows), follower_array, len(follower_rows),
        monster_array, len(monster_rows))
    if not context_ptr:
        raise NativeUnavailable('Rust refused the flattened encounter context')
    return _NativeContext(library, context_ptr, bytes(attestation), names,
                          encounter_array, follower_array, monster_array)


class _NativeContext:
    """Strongly owns the DLL and native context; C copies all source arrays at creation."""

    def __init__(self, library, pointer, attestation, names, *source_arrays):
        self.library = library
        self.pointer = ctypes.c_void_p(pointer)
        self.attestation = attestation
        self.names = dict(names)
        self._source_arrays = source_arrays
        self.max_rows = int(library.ka_encounter_ctx_max_rows(self.pointer))

    def close(self):
        pointer = getattr(self, 'pointer', None)
        if pointer:
            self.pointer = None
            self.library.ka_encounter_ctx_close(pointer)

    def __del__(self):
        self.close()

    def roster(self, encounter_id: int, defeat_count: int, lib_seed: int):
        rows = (_EnemyRow * max(1, self.max_rows))()
        written, level, draws = ctypes.c_uint32(), ctypes.c_int32(), ctypes.c_uint32()
        status = self.library.ka_encounter_roster(
            self.pointer, encounter_id, _i32(defeat_count), _i32(lib_seed), rows,
            self.max_rows, ctypes.byref(written), ctypes.byref(level), ctypes.byref(draws))
        if status != 0 or written.value > self.max_rows:
            raise NativeUnavailable(f'native roster call failed with status {status}')
        result = []
        for row in rows[:written.value]:
            name = self.names.get(int(row.monster_id))
            if name is None:
                raise NativeUnavailable('native roster returned an unknown monster id')
            result.append([name, bool(row.boss), [int(value) for value in row.parameters],
                           [int(row.skill_id)]])
        return int(level.value), int(draws.value), result


def _current_context():
    source_attestation, raw = _read_current_data()
    global _CONTEXT
    with _CONTEXT_LOCK:
        library = _load_library()
        attestation = source_attestation + bytes.fromhex(library._ka_revision_sha256)
        if _CONTEXT is not None:
            if _CONTEXT.attestation == attestation:
                return _CONTEXT
            # A source change invalidates the resident snapshot. Fail over to the canonical Python
            # compiler for this call; a later call may build a new context from the fresh bytes.
            previous = _CONTEXT
            _CONTEXT = None
            del previous
            raise NativeUnavailable('attested source data changed; use canonical compiler')
        try:
            encounter_data = json.loads(raw['encounters.json'].decode('utf-8'))
            # `_context_from_data` binds data and code provenance exactly once for this context.
            context = _context_from_data(encounter_data, source_attestation, library)
        except NativeUnavailable:
            raise
        except Exception as exc:  # a changed/malformed file remains the canonical compiler's case
            raise NativeUnavailable(f'could not attest native source context: {exc}') from exc
        previous = _CONTEXT
        _CONTEXT = context
        # In-flight calls retain a strong Python reference, so destruction waits until each C call
        # returns even though ctypes releases the GIL around the native function.
        del previous
        return context


def native_revision(scenario):
    """Return the exact canonical revision or None if native use is not safely available.

    Scenario validation deliberately runs first and propagates the same Python exception as the
    canonical path. An unavailable or stale native context is a signal to use the full compiler.
    """
    normalized = mechanics.normalize_scenario(scenario)
    encounter_id = normalized.get('encounterId')
    defeat_count = normalized.get('defeatCount')
    lib_seed = normalized.get('libSeed')
    if (type(encounter_id) is not int or type(defeat_count) is not int
            or type(lib_seed) is not int or not 0 <= encounter_id < 20):
        return None
    # The revision does not consume own-unit values, but the canonical compile first prepares
    # them and may fail on values outside the proven input domain. Keep unusual affinities on the
    # full Python path so this shortcut cannot turn a canonical preparation failure into admission.
    for unit in normalized.get('ownUnits') or ():
        for item in unit.get('equipment') or ():
            if item.get('affinity') not in (-1, 0, 1):
                return None
    try:
        context = _current_context()
        level, draws, roster = context.roster(encounter_id, defeat_count, lib_seed)
    except (NativeUnavailable, OSError, ValueError, TypeError):
        return None
    payload = dict(id=encounter_id, defeatCount=defeat_count, level=level, terrain=-1,
                   roster=roster, mechanicsRevision=mechanics.MECHANICS_REVISION)
    digest = hashlib.sha256(mechanics.canonical(payload).encode('utf-8')).hexdigest()
    return dict(digest=digest, digestRevision='strategy-encounter-digest-1',
                encounterId=encounter_id, defeatCount=defeat_count, level=level,
                rosterDigest=digest)


def clear_context():
    """Test/maintenance hook; normal runtime swaps only after a fresh source attestation."""
    global _CONTEXT
    with _CONTEXT_LOCK:
        old = _CONTEXT
        _CONTEXT = None
    del old
