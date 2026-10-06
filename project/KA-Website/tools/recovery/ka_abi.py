"""One authoritative Python mirror of the Rust combat ABI, plus the snapshot codec.

Every harness loads the kernel through this module, and this module refuses to run when the Rust
struct sizes disagree with the ctypes mirrors, so a layout change cannot be silently mis-parsed.

The snapshot is one deterministic, JSON-serializable dict covering every field the kernel reads or
writes. It deliberately distinguishes a *missing* key from a key present with value 0: blackboards
and parameter tables travel as dicts, never as dense arrays with sentinels.

    {
      "config":  {tick, first_identity, next_identity, next_command, map_width, row_offset,
                  movement_enabled, battle_state, row_offset_source},
      "units":   [ {identity, present, team, human, monster, destroyed, id, components{...},
                    board{}, long_board{}, parameters{}, equipment[], skills[], levels[],
                    invoking[], commands[], path[], weapon{...}, boss, monster_type} ],
      "objects": [ {id, destroyed, components{...}} ],
      "subsets": [ {len, free_len, version, slots[], free[]} ],
      "buckets": [ {key, ids[]} ],
      "rng":     {"math": {values[56], index, partner, draws},
                  "lib":  {values[56], index, partner, draws}},
      "rows":    {id: row}, "effect_resources": {seb: max_frame}, "prizes": [ids]
    }

`components` is keyed by the recovered component slot id (`0` Position, `1` Speed, `2` Seb,
`4` Depth, `5` Cell, `7` Image, `12` Animation, `14` Direction, `19` ModifyAnimation,
`28` AI, `32` Garbage, `33` Parameter, `38` Effect, `39` Projectile, `46` Attack, `51` Skill).
"""
import ctypes
import json
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
#: The versioned production kernel. A distinct filename lets a running optimiser keep its
#: loaded DLL while the next launch picks up the verified build. `KA_KERNEL_DLL` remains an
#: override for parity and profiling checks.
_release_dir = HERE / 'native' / 'ka_kernel' / 'target' / 'release'
#: v9 adds the MP telemetry + live Holy Herb policy block. A *new* filename is what lets a running
#: optimiser keep the v8 kernel it already loaded while the next launch picks v9 up; the two
#: structs differ, so the versions must never be mixed.
_versioned_dll = _release_dir / 'ka_kernel_v9.dll'
DLL = Path(os.environ.get('KA_KERNEL_DLL') or
           (_versioned_dll if _versioned_dll.exists() else _release_dir / 'ka_kernel.dll'))

KA_MAX_UNITS = 32
KA_MAX_OBJECTS = 32768
KA_SLOT_COUNT = 52
KA_SUBSET_COUNT = 11
KA_MAX_BUCKETS = 2048
KA_MAX_ROWS = 64
KA_MAX_EFFECT_RESOURCES = 48
KA_MAX_PRIZES = 16
KA_MAX_INPUTS = 32
KA_MAX_ITEMS = 8
KA_MAX_USE_LOG = 64
#: `state::KA_MAX_MP_WATCH` / `KA_MAX_HERB_USES` / `KA_HERB_TRIGGER_PERCENT` / `KA_MP_PARAMETER`.
KA_MAX_MP_WATCH = 2
KA_MAX_HERB_USES = 16
HERB_TRIGGER_PERCENT = 3
MP_PARAMETER = 11

#: `input_callback` phases in canonical order.
PHASE_BEFORE_FIGHTERS = 0
PHASE_AFTER_FIGHTERS = 1
PHASE_CODES = {'before_fighters': PHASE_BEFORE_FIGHTERS, 'after_fighters': PHASE_AFTER_FIGHTERS}
#: Native input kinds. `-1` is "never dispatched".
INPUT_HOLY_HERB = 0
INPUT_ITEM = 1
INPUT_LABELS = {INPUT_HOLY_HERB: 'holy_herb', INPUT_ITEM: 'item'}
#: `combat_consumables.RECOVERY_PARAMETERS` / `RECOVERY_ALL_RESIDENTS`, indexed by `bonusType`.
RECOVERY_PARAMETERS = (10, 10, 11, 11, 12, 12)
RECOVERY_ALL_RESIDENTS = (True, False, True, False, True, False)


class KaVec3(ctypes.Structure):
    _fields_ = [('x', ctypes.c_float), ('y', ctypes.c_float), ('z', ctypes.c_float)]


class KaModifier(ctypes.Structure):
    _fields_ = [('type_', ctypes.c_int32), ('offset_x', ctypes.c_float),
                ('offset_y', ctypes.c_float), ('offset_z', ctypes.c_float),
                ('scale_x', ctypes.c_float), ('scale_y', ctypes.c_float),
                ('angle', ctypes.c_int32), ('anchor', ctypes.c_int32),
                ('frame', ctypes.c_int32), ('duration', ctypes.c_int32),
                ('destroy_on_finish', ctypes.c_uint8), ('looping', ctypes.c_uint8),
                ('alpha', ctypes.c_int32)]


class KaEffect(ctypes.Structure):
    _fields_ = [('type_', ctypes.c_int32), ('value1', ctypes.c_int32),
                ('value2', ctypes.c_int32), ('depth', ctypes.c_uint8),
                ('frame', ctypes.c_int32), ('max_frame', ctypes.c_int32),
                ('parent', ctypes.c_int32), ('scale', ctypes.c_int32)]


class KaProjectile(ctypes.Structure):
    _fields_ = [('start', KaVec3), ('end', KaVec3), ('speed', ctypes.c_int32),
                ('height', ctypes.c_int32), ('frame', ctypes.c_int32),
                ('length', ctypes.c_int32), ('owner', ctypes.c_int32)]


class KaEntity(ctypes.Structure):
    _fields_ = [('id', ctypes.c_int32), ('destroyed', ctypes.c_uint8),
                ('has', ctypes.c_uint8 * KA_SLOT_COUNT),
                ('position', KaVec3), ('offset', KaVec3), ('parent', ctypes.c_int32),
                ('speed', KaVec3),
                ('seb', ctypes.c_int32 * 4), ('depth', ctypes.c_int32 * 2),
                ('cell', ctypes.c_int32 * 2), ('image', ctypes.c_int32 * 6),
                ('animation', ctypes.c_int32 * 2), ('direction', ctypes.c_int32),
                ('modifier', KaModifier), ('effect', KaEffect),
                ('projectile', KaProjectile), ('attack', ctypes.c_int32),
                ('garbage', ctypes.c_int32)]


class KaParam(ctypes.Structure):
    _fields_ = [('id', ctypes.c_int32), ('raw_value', ctypes.c_int32),
                ('extra_value', ctypes.c_int32), ('raw_max', ctypes.c_int32),
                ('extra_max', ctypes.c_int32), ('training_level', ctypes.c_int32)]


class KaEquipRow(ctypes.Structure):
    _fields_ = [('level', ctypes.c_int32), ('pvp_level', ctypes.c_int32),
                ('affinity', ctypes.c_int32), ('pair_count', ctypes.c_int32),
                ('pairs', (ctypes.c_int32 * 2) * 16), ('present', ctypes.c_uint8 * 16)]


class KaParams(ctypes.Structure):
    _fields_ = [('count', ctypes.c_uint32), ('rows', KaParam * 16),
                ('equipment_count', ctypes.c_uint32), ('equipment', KaEquipRow * 8)]


class KaEntry(ctypes.Structure):
    _fields_ = [('key', ctypes.c_int32), ('value', ctypes.c_int64)]


class KaBoard(ctypes.Structure):
    _fields_ = [('len', ctypes.c_uint32), ('entries', KaEntry * 48),
                ('positions', ctypes.c_uint8 * 128)]


class KaSkill(ctypes.Structure):
    _fields_ = [('id', ctypes.c_int32), ('category', ctypes.c_int32), ('kind', ctypes.c_int32),
                ('flags', ctypes.c_int32), ('min_mp', ctypes.c_int32), ('max_mp', ctypes.c_int32),
                ('required_equip_type', ctypes.c_int32), ('shooting_range', ctypes.c_int32),
                ('range', ctypes.c_int32), ('count', ctypes.c_int32), ('motion', ctypes.c_int32),
                ('value', ctypes.c_int32), ('seb', ctypes.c_int32), ('img', ctypes.c_int32),
                ('impact_img', ctypes.c_int32), ('impact_seb', ctypes.c_int32)]


class KaCommand(ctypes.Structure):
    _fields_ = [('opcode', ctypes.c_int32), ('target', ctypes.c_int32), ('skill', ctypes.c_int32),
                ('tick', ctypes.c_int32), ('duration', ctypes.c_int32), ('use_index', ctypes.c_int32)]


class KaInvoke(ctypes.Structure):
    _fields_ = [('skill', ctypes.c_int32), ('remaining', ctypes.c_int32)]


class KaPoint(ctypes.Structure):
    _fields_ = [('x', ctypes.c_int32), ('y', ctypes.c_int32)]


class KaUnit(ctypes.Structure):
    _fields_ = [
        ('present', ctypes.c_uint8), ('team', ctypes.c_uint8), ('human', ctypes.c_uint8),
        ('monster', ctypes.c_uint8), ('flags', ctypes.c_int32), ('id', ctypes.c_int32),
        ('identity', ctypes.c_int32),
        ('body', KaEntity),
        ('params', KaParams),
        ('weapon_type', ctypes.c_int32), ('weapon_shooting_range', ctypes.c_int32),
        ('weapon_motion', ctypes.c_int32), ('weapon_projectile_flag', ctypes.c_int32),
        ('boss', ctypes.c_uint8), ('monster_type', ctypes.c_int32),
        ('special_human', ctypes.c_uint8), ('monster_size', ctypes.c_int32),
        ('human_flag', ctypes.c_int32),
        ('board', KaBoard), ('long_board', KaBoard),
        ('skill_ids', ctypes.c_int32 * 12), ('skill_count', ctypes.c_uint32),
        ('levels', ctypes.c_int32 * 12), ('level_count', ctypes.c_uint32),
        ('invoking', KaInvoke * 64), ('invoking_count', ctypes.c_uint32),
        ('commands', KaCommand * 1024), ('command_count', ctypes.c_uint32),
        ('path', KaPoint * 8), ('path_count', ctypes.c_uint32),
    ]


class KaComponentView(ctypes.Structure):
    _fields_ = [('slot', ctypes.c_int32), ('ints', ctypes.c_int32 * 8),
                ('floats', ctypes.c_float * 8)]


class KaEvent(ctypes.Structure):
    _fields_ = [('kind', ctypes.c_int32), ('unit', ctypes.c_int32), ('a', ctypes.c_int32),
                ('b', ctypes.c_int32), ('c', ctypes.c_int32), ('d', ctypes.c_int32),
                ('e', ctypes.c_int32)]


class EffectSpec(ctypes.Structure):
    _fields_ = [('type_', ctypes.c_int32), ('value1', ctypes.c_int32),
                ('value2', ctypes.c_int32), ('res', ctypes.c_int32), ('seb', ctypes.c_int32),
                ('x', ctypes.c_float), ('y', ctypes.c_float), ('z', ctypes.c_float),
                ('scale', ctypes.c_int32), ('image', ctypes.c_int32),
                ('depth', ctypes.c_uint8), ('max_frame', ctypes.c_int32),
                ('frame', ctypes.c_int32), ('looping', ctypes.c_uint8),
                ('parent', ctypes.c_int32), ('animate', ctypes.c_uint8)]


class KaBattleReport(ctypes.Structure):
    _fields_ = [('status', ctypes.c_int32), ('ticks', ctypes.c_int32), ('verdict', ctypes.c_int32),
                ('verdict_tick', ctypes.c_int32), ('battle_state', ctypes.c_int32),
                ('battle_frame', ctypes.c_int32), ('finish_policy', ctypes.c_int32),
                ('ending_gate_tick', ctypes.c_int32), ('ending_confirmed', ctypes.c_int32),
                ('ending_counter', ctypes.c_int32), ('prize_callbacks', ctypes.c_int32),
                ('pre_verdict_prize_callbacks', ctypes.c_int32),
                ('unresolved_commands', ctypes.c_int32), ('pending_projectiles', ctypes.c_int32),
                ('active_damage_or_leaving', ctypes.c_int32), ('math_draws', ctypes.c_int32),
                ('lib_draws', ctypes.c_int32), ('certificate_held', ctypes.c_int32),
                ('certificate_frame', ctypes.c_int32), ('certificate_pending', ctypes.c_int32),
                ('late_hold_frame', ctypes.c_int32), ('pending_at_verdict', ctypes.c_int32),
                ('pending_final', ctypes.c_int32), ('post_certificate_delta', ctypes.c_int32),
                ('observations', ctypes.c_int32), ('verdict_observations', ctypes.c_int32),
                ('clauses', ctypes.c_int32 * 4), ('boss_hp', ctypes.c_int32),
                ('boss_state', ctypes.c_int32), ('stored_target_holders', ctypes.c_int32),
                ('queued_command_holders', ctypes.c_int32), ('scope_allowed', ctypes.c_int32),
                ('heals', ctypes.c_int32), ('attack_attempts', ctypes.c_int32),
                ('survivors', ctypes.c_int32), ('own_hp', ctypes.c_int64),
                ('own_hp_max', ctypes.c_int64), ('own_present', ctypes.c_int32),
                ('resource_uses', ctypes.c_int32),
                # Stored-attack progress (`combat_progress.ProgressWatch.report()`), in the
                # observer's own published field order.
                ('progress_boss', ctypes.c_int32),
                ('progress_boss_death_tick', ctypes.c_int32),
                ('progress_boss_leaving_tick', ctypes.c_int32),
                ('progress_stored_at_death', ctypes.c_int32),
                ('progress_stored_targeting_boss_at_death', ctypes.c_int32),
                ('progress_stored_target_holders_at_death', ctypes.c_int32),
                ('progress_commands_targeting_boss', ctypes.c_int32),
                ('progress_commands_targeting_boss_released', ctypes.c_int32),
                ('progress_max_stored', ctypes.c_int32),
                ('progress_max_targeting_boss', ctypes.c_int32),
                ('progress_target_holders_peak', ctypes.c_int32),
                ('progress_post_death_reentries', ctypes.c_int32),
                ('progress_post_death_leavings', ctypes.c_int32),
                ('progress_post_death_prizes', ctypes.c_int32),
                ('progress_released_after_death', ctypes.c_int32),
                ('progress_released_after_death_targeting_boss', ctypes.c_int32),
                ('progress_first_release_after_death', ctypes.c_int32),
                ('progress_last_release_after_death', ctypes.c_int32),
                # MP telemetry (`combat_progress.mp_metrics`), one block per declared trigger unit.
                # Read-only, attached to the compact result after its digest.
                ('mp_watch_count', ctypes.c_int32),
                ('mp_identity', ctypes.c_int32 * KA_MAX_MP_WATCH),
                ('mp_min', ctypes.c_int32 * KA_MAX_MP_WATCH),
                ('mp_min_percent', ctypes.c_int32 * KA_MAX_MP_WATCH),
                ('mp_low', ctypes.c_int32 * KA_MAX_MP_WATCH),
                ('mp_first_low_tick', ctypes.c_int32 * KA_MAX_MP_WATCH),
                ('mp_first_low_phase', ctypes.c_int32 * KA_MAX_MP_WATCH),
                ('mp_zero', ctypes.c_int32 * KA_MAX_MP_WATCH),
                # Explicit Holy Herb telemetry (`combat_progress.herb_metrics`): the declared stock
                # and cap, the successful-use count, and one row per dispatch.
                ('herb_stock_start', ctypes.c_int32),
                ('herb_stock_remaining', ctypes.c_int32),
                ('herb_max_uses', ctypes.c_int32),
                ('herb_use_count', ctypes.c_int32),
                ('herb_log_count', ctypes.c_int32),
                ('herb_use_tick', ctypes.c_int32 * KA_MAX_HERB_USES),
                ('herb_use_phase', ctypes.c_int32 * KA_MAX_HERB_USES),
                ('herb_use_source', ctypes.c_int32 * KA_MAX_HERB_USES),
                ('herb_use_ok', ctypes.c_int32 * KA_MAX_HERB_USES)]


REPORT_FIELDS = tuple(name for name, _ in KaBattleReport._fields_)


def report_dict(report):
    return {name: getattr(report, name) for name in REPORT_FIELDS}


def load(dll_path=None):
    """Load the kernel and bind every entry point the harnesses use."""
    path = Path(dll_path) if dll_path else DLL
    if not path.is_file():
        raise SystemExit(f'build it first: cargo build --release in {path.parent.parent}')
    library = ctypes.CDLL(str(path))
    pointer = ctypes.c_void_p
    library.ka_battle_create.restype = pointer
    library.ka_battle_free.argtypes = [pointer]
    library.ka_battle_clone.argtypes = [pointer]
    library.ka_battle_clone.restype = pointer
    library.ka_battle_checksum.argtypes = [pointer]
    library.ka_battle_checksum.restype = ctypes.c_uint64
    library.ka_battle_import.argtypes = [pointer, ctypes.POINTER(KaUnit), ctypes.c_uint32,
                                         ctypes.c_uint64]
    library.ka_battle_import.restype = ctypes.c_uint32
    library.ka_battle_seed_rng.argtypes = [pointer, ctypes.c_int32, ctypes.c_int32]
    library.ka_subset_count.argtypes = [pointer, ctypes.c_uint32]
    library.ka_subset_count.restype = ctypes.c_uint32
    library.ka_subset_member.argtypes = [pointer, ctypes.c_uint32, ctypes.c_uint32]
    library.ka_subset_member.restype = ctypes.c_int32
    library.ka_battle_export.argtypes = [pointer, ctypes.POINTER(KaUnit)]
    library.ka_battle_export.restype = ctypes.c_uint32
    library.ka_battle_config.argtypes = [pointer, ctypes.c_int32, ctypes.c_int32, ctypes.c_uint8]
    library.ka_battle_identity.argtypes = [pointer, ctypes.c_int32]
    library.ka_battle_set_counters.argtypes = [pointer, ctypes.c_uint32, ctypes.c_int32,
                                               ctypes.c_int32]
    library.ka_battle_counters.argtypes = [pointer, ctypes.POINTER(ctypes.c_uint32),
                                           ctypes.POINTER(ctypes.c_int32),
                                           ctypes.POINTER(ctypes.c_int32)]
    library.ka_battle_add_row.argtypes = [pointer, ctypes.POINTER(KaSkill)]
    library.ka_battle_add_row.restype = ctypes.c_uint32
    library.ka_battle_add_effect_resource.argtypes = [pointer, ctypes.c_int32, ctypes.c_int32]
    library.ka_battle_add_effect_resource.restype = ctypes.c_uint32
    library.ka_battle_set_prizes.argtypes = [pointer, ctypes.POINTER(ctypes.c_int32),
                                             ctypes.c_uint32]
    library.ka_battle_set_human_bases.argtypes = [pointer, ctypes.POINTER(ctypes.c_int32),
                                                  ctypes.c_uint32]
    library.ka_battle_set_human_bases.restype = ctypes.c_uint32
    library.ka_battle_set_object.argtypes = [pointer, ctypes.c_uint32, ctypes.POINTER(KaEntity)]
    library.ka_battle_set_object.restype = ctypes.c_uint32
    library.ka_object_entity.argtypes = [pointer, ctypes.c_uint32, ctypes.POINTER(KaEntity)]
    library.ka_object_entity.restype = ctypes.c_int32
    library.ka_object_count.argtypes = [pointer]
    library.ka_object_count.restype = ctypes.c_uint32
    library.ka_battle_set_subset.argtypes = [pointer, ctypes.c_uint32, ctypes.c_uint32,
                                             ctypes.c_uint32, ctypes.c_int32,
                                             ctypes.POINTER(ctypes.c_int32),
                                             ctypes.POINTER(ctypes.c_uint32)]
    library.ka_subset_state.argtypes = [pointer, ctypes.c_uint32,
                                        ctypes.POINTER(ctypes.c_uint32),
                                        ctypes.POINTER(ctypes.c_uint32),
                                        ctypes.POINTER(ctypes.c_int32),
                                        ctypes.POINTER(ctypes.c_int32),
                                        ctypes.POINTER(ctypes.c_uint32)]
    library.ka_battle_set_bucket.argtypes = [pointer, ctypes.c_uint32, ctypes.c_int32,
                                             ctypes.c_uint32, ctypes.POINTER(ctypes.c_int32)]
    library.ka_battle_set_bucket.restype = ctypes.c_uint32
    library.ka_bucket_count.argtypes = [pointer]
    library.ka_bucket_count.restype = ctypes.c_uint32
    library.ka_bucket_state.argtypes = [pointer, ctypes.c_uint32,
                                        ctypes.POINTER(ctypes.c_int32),
                                        ctypes.POINTER(ctypes.c_uint32),
                                        ctypes.POINTER(ctypes.c_int32)]
    library.ka_bucket_state.restype = ctypes.c_int32
    library.ka_battle_set_rng.argtypes = [pointer, ctypes.c_uint32,
                                          ctypes.POINTER(ctypes.c_int32), ctypes.c_int32,
                                          ctypes.c_int32, ctypes.c_uint32]
    library.ka_rng_state.argtypes = [pointer, ctypes.c_uint32,
                                     ctypes.POINTER(ctypes.c_int32),
                                     ctypes.POINTER(ctypes.c_int32),
                                     ctypes.POINTER(ctypes.c_int32),
                                     ctypes.POINTER(ctypes.c_uint32)]
    library.ka_entity_components.argtypes = [pointer, ctypes.c_int32,
                                             ctypes.POINTER(KaComponentView), ctypes.c_uint32]
    library.ka_entity_components.restype = ctypes.c_uint32
    library.ka_update_fighters.argtypes = [pointer, ctypes.c_int32]
    library.ka_update_fighters.restype = ctypes.c_int32
    library.ka_run_native_steps.argtypes = [pointer, ctypes.c_int32, ctypes.c_uint32,
                                            ctypes.POINTER(ctypes.c_uint32),
                                            ctypes.POINTER(ctypes.c_int32)]
    library.ka_run_native_steps.restype = ctypes.c_int32
    library.ka_battle_event_count.argtypes = [pointer]
    library.ka_battle_event_count.restype = ctypes.c_uint32
    library.ka_battle_export_events.argtypes = [pointer, ctypes.POINTER(KaEvent)]
    library.ka_battle_export_events.restype = ctypes.c_uint32
    library.ka_battle_math_draws.argtypes = [pointer]
    library.ka_battle_math_draws.restype = ctypes.c_uint32
    library.ka_battle_lib_draws.argtypes = [pointer]
    library.ka_battle_lib_draws.restype = ctypes.c_uint32
    library.ka_unit_raw.argtypes = [pointer, ctypes.c_int32, ctypes.c_int32]
    library.ka_unit_raw.restype = ctypes.c_int32
    library.ka_battle_allocate.argtypes = [pointer]
    library.ka_battle_allocate.restype = ctypes.c_int32
    library.ka_battle_destroy.argtypes = [pointer, ctypes.c_int32]
    library.ka_battle_set_animation_resource.argtypes = [pointer, ctypes.c_int32, ctypes.c_int32,
                                                         ctypes.c_int32, ctypes.c_int32]
    library.ka_battle_set_scope_allowed.argtypes = [pointer, ctypes.c_uint8]
    library.ka_battle_set_control.argtypes = [pointer] + [ctypes.c_int32] * 9
    library.ka_battle_skip_lib_draws.argtypes = [pointer, ctypes.c_uint32]
    library.ka_run_battle.argtypes = [pointer, ctypes.c_uint32, ctypes.c_int32,
                                      ctypes.POINTER(KaBattleReport)]
    library.ka_run_battle.restype = ctypes.c_int32
    library.ka_battle_add_input.argtypes = [pointer] + [ctypes.c_int32] * 4
    library.ka_battle_add_input.restype = ctypes.c_uint32
    library.ka_battle_add_item.argtypes = [pointer, ctypes.c_int32, ctypes.c_int32, ctypes.c_uint8,
                                           ctypes.c_int32, ctypes.c_int32, ctypes.c_int32]
    library.ka_battle_add_item.restype = ctypes.c_uint32
    library.ka_battle_set_herb_stock.argtypes = [pointer, ctypes.c_int32]
    library.ka_battle_herb_stock.argtypes = [pointer]
    library.ka_battle_herb_stock.restype = ctypes.c_int32
    library.ka_battle_set_mp_watch.argtypes = [pointer, ctypes.POINTER(ctypes.c_int32),
                                               ctypes.c_uint32, ctypes.c_int32]
    library.ka_battle_set_mp_watch.restype = ctypes.c_uint32
    library.ka_battle_mp_config.argtypes = [pointer, ctypes.POINTER(ctypes.c_int32)]
    library.ka_battle_mp_config.restype = ctypes.c_int32
    library.ka_battle_mp.argtypes = [pointer, ctypes.c_int32]
    library.ka_battle_mp.restype = ctypes.c_int32
    library.ka_battle_input_count.argtypes = [pointer]
    library.ka_battle_input_count.restype = ctypes.c_uint32
    library.ka_battle_input.argtypes = [pointer, ctypes.c_uint32, ctypes.POINTER(ctypes.c_int32)]
    library.ka_battle_input.restype = ctypes.c_int32
    library.ka_battle_item_count.argtypes = [pointer]
    library.ka_battle_item_count.restype = ctypes.c_uint32
    library.ka_battle_item.argtypes = [pointer, ctypes.c_uint32, ctypes.POINTER(ctypes.c_int32)]
    library.ka_battle_item.restype = ctypes.c_int32
    library.ka_use_log_count.argtypes = [pointer]
    library.ka_use_log_count.restype = ctypes.c_uint32
    library.ka_use_log.argtypes = [pointer, ctypes.c_uint32, ctypes.POINTER(ctypes.c_int32)]
    library.ka_use_log.restype = ctypes.c_int32
    library.ka_battle_stats.argtypes = [pointer, ctypes.POINTER(ctypes.c_uint64)]
    library.ka_lib_log_count.argtypes = [pointer]
    library.ka_lib_log_count.restype = ctypes.c_uint32
    library.ka_lib_log.argtypes = [pointer, ctypes.c_uint32, ctypes.POINTER(ctypes.c_int32)]
    library.ka_lib_log.restype = ctypes.c_int32
    library.ka_battle_stats.restype = ctypes.c_int32
    library.ka_battle_report.argtypes = [pointer, ctypes.c_int32,
                                         ctypes.POINTER(KaBattleReport)]
    library.ka_battle_report.restype = ctypes.c_int32
    library.ka_battle_set_animation_resource.restype = ctypes.c_uint32
    library.ka_animation_resource_count.argtypes = [pointer]
    library.ka_animation_resource_count.restype = ctypes.c_uint32
    library.ka_animation_resource.argtypes = [pointer, ctypes.c_uint32,
                                              ctypes.POINTER(ctypes.c_int32),
                                              ctypes.POINTER(ctypes.c_int32),
                                              ctypes.POINTER(ctypes.c_int32),
                                              ctypes.POINTER(ctypes.c_int32)]
    library.ka_animation_resource.restype = ctypes.c_int32
    library.ka_projectile_source_count.argtypes = [pointer]
    library.ka_projectile_source_count.restype = ctypes.c_uint32
    library.ka_projectile_source.argtypes = [pointer, ctypes.c_uint32,
                                             ctypes.POINTER(ctypes.c_int32),
                                             ctypes.POINTER(ctypes.c_int32),
                                             ctypes.POINTER(ctypes.c_int32)]
    library.ka_projectile_source.restype = ctypes.c_int32
    library.ka_battle_set_projectile_source.argtypes = [pointer, ctypes.c_int32, ctypes.c_int32,
                                                       ctypes.c_int32]
    library.ka_native_tick.argtypes = [pointer, ctypes.c_int32]
    library.ka_native_tick.restype = ctypes.c_int32
    library.ka_native_full_tick.argtypes = [pointer]
    library.ka_native_full_tick.restype = ctypes.c_int32
    library.ka_run_phase.argtypes = [pointer, ctypes.c_uint32]
    library.ka_run_phase.restype = ctypes.c_int32
    library.ka_battle_rebuild_subsets.argtypes = [pointer]
    library.ka_battle_seed_occupancy.argtypes = [pointer]
    library.ka_create_effect.argtypes = [pointer, ctypes.POINTER(EffectSpec)]
    library.ka_create_effect.restype = ctypes.c_int32
    library.ka_fire_projectile.argtypes = [pointer, ctypes.c_int32] + [ctypes.c_float] * 6 + \
        [ctypes.c_int32, ctypes.c_int32, ctypes.c_uint8, ctypes.c_int32]
    library.ka_fire_projectile.restype = ctypes.c_int32
    library.ka_attack.argtypes = [pointer, ctypes.c_uint32, ctypes.c_int32, ctypes.c_int32,
                                  ctypes.c_void_p]
    library.ka_attack.restype = ctypes.c_int32
    library.ka_use_skill.argtypes = [pointer, ctypes.c_uint32, ctypes.POINTER(KaCommand),
                                     ctypes.c_int32, ctypes.POINTER(ctypes.c_int32)]
    library.ka_use_skill.restype = ctypes.c_int32
    library.ka_change_state.argtypes = [pointer, ctypes.c_uint32, ctypes.c_int32]
    library.ka_change_state.restype = ctypes.c_int32
    library.ka_critical_rate.argtypes = [ctypes.c_int32]
    library.ka_critical_rate.restype = ctypes.c_int32
    library.ka_hit_rate.argtypes = [ctypes.c_int32] * 3
    library.ka_hit_rate.restype = ctypes.c_int32
    library.ka_modified_rate.argtypes = [ctypes.c_int32, ctypes.c_int32, ctypes.c_uint8]
    library.ka_modified_rate.restype = ctypes.c_int32
    library.ka_random_range.argtypes = [ctypes.c_int32] * 3
    library.ka_random_range.restype = ctypes.c_int32
    library.ka_parabola.argtypes = [ctypes.c_int32] * 3
    library.ka_parabola.restype = ctypes.c_int32
    library.ka_base_damage.argtypes = [pointer, ctypes.c_int32, ctypes.c_int32]
    library.ka_base_damage.restype = ctypes.c_int32
    library.ka_damage_from_parameters.argtypes = [pointer, ctypes.c_uint32, ctypes.c_uint32,
                                                  ctypes.c_uint8, ctypes.c_uint8]
    library.ka_damage_from_parameters.restype = ctypes.c_int32
    library.ka_manhattan.argtypes = [ctypes.c_int32] * 4
    library.ka_manhattan.restype = ctypes.c_int32
    library.ka_i32_wrap.argtypes = [ctypes.c_int64]
    library.ka_i32_wrap.restype = ctypes.c_int32
    library.ka_f32_to_int.argtypes = [ctypes.c_float]
    library.ka_f32_to_int.restype = ctypes.c_int32
    library.ka_nearest_eligible.argtypes = [
        ctypes.POINTER(ctypes.c_int32), ctypes.POINTER(ctypes.c_uint8),
        ctypes.POINTER(ctypes.c_int32), ctypes.POINTER(ctypes.c_int32),
        ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t, ctypes.c_size_t,
        ctypes.c_uint8, ctypes.POINTER(ctypes.c_int32)]
    library.ka_nearest_eligible.restype = ctypes.c_size_t
    library.ka_spatial_phase.argtypes = [pointer, ctypes.POINTER(ctypes.c_int32),
                                         ctypes.POINTER(ctypes.c_int32),
                                         ctypes.POINTER(ctypes.c_uint8)]
    library.ka_spatial_phase.restype = ctypes.c_uint32
    library.ka_spatial_phase_many.argtypes = [pointer, ctypes.c_uint32,
                                              ctypes.POINTER(ctypes.c_int32),
                                              ctypes.POINTER(ctypes.c_int32),
                                              ctypes.POINTER(ctypes.c_uint8)]
    library.ka_spatial_phase_many.restype = ctypes.c_uint32
    library.ka_battle_math_rng.argtypes = [pointer]
    library.ka_battle_math_rng.restype = pointer
    library.ka_rng_index.argtypes = [pointer]
    library.ka_rng_index.restype = ctypes.c_int32
    library.ka_rng_partner.argtypes = [pointer]
    library.ka_rng_partner.restype = ctypes.c_int32
    library.ka_rng_value.argtypes = [pointer, ctypes.c_uint32]
    library.ka_rng_value.restype = ctypes.c_int32
    for name, expected in (('ka_sizeof_unit', ctypes.sizeof(KaUnit)),
                           ('ka_sizeof_entity', ctypes.sizeof(KaEntity)),
                           ('ka_sizeof_params', ctypes.sizeof(KaParams)),
                           ('ka_sizeof_board', ctypes.sizeof(KaBoard)),
                           ('ka_sizeof_skill', ctypes.sizeof(KaSkill)),
                           ('ka_sizeof_command', ctypes.sizeof(KaCommand)),
                           ('ka_sizeof_effect_spec', ctypes.sizeof(EffectSpec)),
                           ('ka_sizeof_component_view', ctypes.sizeof(KaComponentView))):
        probe = getattr(library, name)
        probe.restype = ctypes.c_uint32
        actual = probe()
        if actual != expected:
            raise SystemExit(f'ABI mismatch: {name} reports {actual}, Python mirror is {expected}')
    library.ka_sizeof_battle.restype = ctypes.c_uint32
    BATTLE_BYTES = library.ka_sizeof_battle()
    library.battle_bytes = BATTLE_BYTES
    # The report struct travels across the ABI by value, so a layout change in either language must be
    # refused here rather than mis-parsed field by field.
    library.ka_sizeof_report.restype = ctypes.c_uint32
    report_bytes = library.ka_sizeof_report()
    if report_bytes != ctypes.sizeof(KaBattleReport):
        raise SystemExit(f'ABI mismatch: ka_sizeof_report reports {report_bytes}, Python mirror is '
                         f'{ctypes.sizeof(KaBattleReport)}')
    library.report_bytes = report_bytes
    capacity_probe = getattr(library, 'ka_object_capacity', None)
    if capacity_probe is not None:
        capacity_probe.restype = ctypes.c_uint32
    library.object_capacity = capacity_probe() if capacity_probe else KA_MAX_OBJECTS
    return library


# ---------------------------------------------------------------------------------------------
# Snapshot codec
# ---------------------------------------------------------------------------------------------

SLOT_POSITION, SLOT_SPEED, SLOT_SEB, SLOT_DEPTH, SLOT_CELL = 0, 1, 2, 4, 5
SLOT_IMAGE, SLOT_ANIMATION, SLOT_DIRECTION, SLOT_MODIFY_ANIMATION = 7, 12, 14, 19
SLOT_AI, SLOT_GARBAGE, SLOT_PARAMETER, SLOT_EFFECT, SLOT_PROJECTILE = 28, 32, 33, 38, 39
SLOT_ATTACK, SLOT_SKILL = 46, 51


def component_presence(components):
    return [slot for slot in range(len(components)) if components[slot] is not None]


def modifiers_of(value):
    """Canonical full `ModifyAnimation` record.

    The native component is a fixed struct, so absent Python keys (the projectile-fade dict omits the
    offset/scale/anchor fields) are filled with the values the native constructor writes. Only
    `type`, `duration`, `frame`, `loop`, `destroy_on_finish`, `alpha` and `offset_x` are ever read,
    and only by the modifier phase, which is not ported yet.
    """
    return dict(type=value['type'], offset_x=value.get('offset_x', 0.0),
                offset_y=value.get('offset_y', 0.0), offset_z=value.get('offset_z', 0.0),
                scale_x=value.get('scale_x', 1.0), scale_y=value.get('scale_y', 1.0),
                angle=value.get('angle', 0), anchor=value.get('anchor', 0),
                frame=value.get('frame', 0), duration=value.get('duration', 0),
                destroy_on_finish=bool(value.get('destroy_on_finish', False)),
                loop=bool(value.get('loop', False)), alpha=value.get('alpha', 255))


def effect_of(value):
    return dict(type=value[0], value1=value[1], value2=value[2], depth=bool(value[3]),
                frame=value[4], max_frame=value[5], parent=value[6], scale=value[7])


def projectile_of(value):
    return dict(start=list(value['start']), end=list(value['end']), speed=value['speed'],
                height=value['height'], frame=value['frame'], length=value['length'],
                owner=value['owner'])


def image_of(value):
    return [value['tex_ids'][0], value['res_ids'][0], value['tex_u'][0], value['tex_v'][0],
            value['tex_w'][0], value['tex_h'][0]]


def consumable_shape(consumables=None):
    """The canonical consumable snapshot block, shared by both snapshot directions.

    `combat_sandbox.run_scenario` keeps the consumable counters in closure state, not on the engine,
    so a caller with no consumables passes `None`; the empty block is then indistinguishable from a
    native battle that never dispatched one.

    `mp_watch` / `holy_herb_max_uses` are the declared experiment configuration (the trigger units
    that may authorise an automatic Holy Herb and the declared cap), not recovered game data.
    """
    consumables = consumables or {}
    return dict(
        holy_herb_stock=int(consumables.get('holy_herb_stock', 0)),
        holy_herb_max_uses=int(consumables.get('holy_herb_max_uses', 0)),
        mp_watch=[int(value) for value in consumables.get('mp_watch') or ()],
        items=[dict(parameter=int(row['parameter']),
                    all_residents=int(bool(row['all_residents'])),
                    bonus_min=int(row['bonus_min']), bonus_max=int(row['bonus_max']),
                    stock=int(row['stock'])) for row in consumables.get('items') or []],
        inputs=[dict(tick=int(row['tick']), phase=int(row['phase']), kind=int(row['kind']),
                     item=int(row['item'])) for row in consumables.get('inputs') or []],
        uses=[dict(kind=int(row['kind']), item=int(row['item']), tick=int(row['tick']),
                   used=int(bool(row['used'])), percent=int(row['percent']),
                   remaining=int(row['remaining'])) for row in consumables.get('uses') or []])


def consumables_from_scenario(scenario):
    """Translate a *loaded* scenario's consumable declarations into the native shape.

    Returns `(state, item_slot, reason)`. `item_slot` maps a declared item name to its native
    battle-item table index. `reason` is `None` when every declared input is reachable on the native
    battle path; otherwise it is a short reason naming the first declaration that is not, so the
    backend can fall back with a reason code instead of guessing.
    """
    items = scenario.get('items') or {}
    stock = scenario.get('itemStock') or {}
    item_slot = {}
    rows = []
    for name, row in items.items():
        bonus_type = int(row['bonusType'])
        item_slot[name] = len(rows)
        rows.append(dict(parameter=RECOVERY_PARAMETERS[bonus_type],
                         all_residents=RECOVERY_ALL_RESIDENTS[bonus_type],
                         bonus_min=int(row['bonusMinValue']),
                         bonus_max=int(row['bonusMaxValue']),
                         stock=int(stock.get(name, 0))))
    inputs = []
    reason = None
    for event in scenario.get('inputs') or []:
        kind = event.get('type')
        if kind == 'holy_herb':
            inputs.append(dict(tick=int(event['tick']), phase=PHASE_CODES[event['phase']],
                               kind=INPUT_HOLY_HERB, item=-1))
            continue
        if kind != 'item':
            reason = reason or f'input type {kind!r} has no native consumer'
            continue
        name = event.get('item')
        if name not in item_slot:
            reason = reason or f'input item {name!r} is not a declared item'
            continue
        bonus_type = int(items[name]['bonusType'])
        if not RECOVERY_ALL_RESIDENTS[bonus_type]:
            reason = reason or (f'item {name!r} bonusType {bonus_type} is not a reachable '
                                'all-resident recovery type')
            continue
        inputs.append(dict(tick=int(event['tick']), phase=PHASE_CODES[event['phase']],
                           kind=INPUT_ITEM, item=item_slot[name]))
    if len(rows) > KA_MAX_ITEMS:
        reason = reason or f'{len(rows)} declared items exceed the native item table'
    if len(inputs) > KA_MAX_INPUTS:
        reason = reason or f'{len(inputs)} input events exceed the native input schedule'
    return (dict(holy_herb_stock=int(scenario.get('holyHerbStock') or 0),
                 holy_herb_max_uses=int(scenario.get('holyHerbMaxUses') or 0),
                 # Filled in by the caller, which is the only place that can resolve a declared
                 # trigger-unit *name* to the roster identity both engines address it by.
                 mp_watch=[], items=rows, inputs=inputs, uses=[]), item_slot, reason)


def native_consumable_state(library, battle):
    """Read the native consumable block back into the `consumable_shape` form."""
    config = (ctypes.c_int32 * 4)()
    library.ka_battle_mp_config(battle, config)
    inputs = []
    buffer = (ctypes.c_int32 * 5)()
    for index in range(library.ka_battle_input_count(battle)):
        library.ka_battle_input(battle, index, buffer)
        inputs.append(dict(tick=buffer[0], phase=buffer[1], kind=buffer[2], item=buffer[3]))
    items = []
    buffer = (ctypes.c_int32 * 6)()
    for index in range(library.ka_battle_item_count(battle)):
        library.ka_battle_item(battle, index, buffer)
        items.append(dict(parameter=buffer[1], all_residents=buffer[2], bonus_min=buffer[3],
                          bonus_max=buffer[4], stock=buffer[5]))
    uses = []
    for index in range(library.ka_use_log_count(battle)):
        library.ka_use_log(battle, index, buffer)
        uses.append(dict(kind=buffer[0], item=buffer[1], tick=buffer[2], used=buffer[3],
                         percent=buffer[4], remaining=buffer[5]))
    return dict(holy_herb_stock=library.ka_battle_herb_stock(battle),
                holy_herb_max_uses=config[1],
                mp_watch=[config[2], config[3]][:max(0, min(config[0], KA_MAX_MP_WATCH))],
                items=items, inputs=inputs, uses=uses)


def needed_rows(units, created, rows_table):
    """Only the rows the loaded state can reach: owned skills, queued/status skills, and the two
    weapon-projectile rows `update_attacking` resolves (`ROWS[1]`, `ROWS[2]`)."""
    needed = {1, 2}
    for unit in units:
        needed.update(unit['skills'])
        needed.update(command['skill'] for command in unit['commands'])
        if 62 in unit['board']:
            needed.add(unit['board'][62])
    for entity in created:
        components = entity['components']
        if components['attack'] is not None:
            needed.add(components['attack'])
        if components['effect'] is not None and components['effect']['type'] == 15:
            needed.add(components['effect']['value1'])
    return {int(key): dict(rows_table[key]) for key in sorted(needed) if key in rows_table}


def engine_snapshot(engine, rows_table, battle_state, human_bases=None, consumables=None):
    """Capture every field the kernel reads or writes, straight out of the live engine."""
    objects = engine.world.objects
    fighters = list(engine.units)
    units = []
    for identity in fighters:
        components = objects[identity]['components']
        spec = engine.specs[identity]
        weapon = spec['weapon']
        ai = components[SLOT_AI]
        record = dict(
            identity=identity, present=1, id=identity,
            team=spec['team'], human=bool(spec['human']), monster=not spec['human'],
            flags=objects[identity]['flags'], destroyed=bool(objects[identity]['flags'] & 2),
            components=dict(
                position=None if components[0] is None else
                [float(components[0][0]), float(components[0][1]), float(components[0][2]),
                 float(components[0][3]), float(components[0][4]), float(components[0][5]),
                 components[0][6]],
                speed=None if components[1] is None else [float(v) for v in components[1][0:3]],
                seb=None if components[2] is None else [int(v) for v in components[2][0:4]],
                depth=None if components[4] is None else [int(v) for v in components[4][0:2]],
                cell=None if components[5] is None else [int(components[5][0]),
                                                         int(components[5][1])],
                image=None if components[7] is None else image_of(components[7]),
                animation=None if components[12] is None else [int(components[12][0]),
                                                               int(components[12][1])],
                direction=None if components[14] is None else int(components[14][0]),
                modifier=None if components[19] is None else modifiers_of(components[19]),
                garbage=None if components[32] is None else int(components[32][0]),
                effect=None if components[38] is None else effect_of(components[38]),
                projectile=None if components[39] is None else projectile_of(components[39]),
                attack=None if components[46] is None else int(components[46][0]),
            ),
            board={int(k): int(v) for k, v in sorted(ai['board'].items())},
            long_board={int(k): int(v) for k, v in sorted(ai['long_board'].items())},
            parameters={int(k): dict(rawValue=int(v['rawValue']), extraValue=int(v['extraValue']),
                                     rawMax=int(v['rawMax']), extraMax=int(v['extraMax']),
                                     trainingLevel=int(v['trainingLevel']))
                        for k, v in sorted(components[33]['parameters'].items())},
            equipment=[dict(level=int(row['level']), pvpLevel=int(row.get('pvpLevel', 0)),
                            affinity=int(row.get('affinity', 1)),
                            parameters=list(row.get('parameters', [])))
                       for row in spec.get('equipmentRows', [])],
            skills=[int(v) for v in components[51]['dataIds']],
            levels=[int(v) for v in components[51]['invocationLevels']],
            invoking=[[int(a), int(b)] for a, b in components[51]['invokingSkills']],
            commands=[dict(opcode=int(c['opcode']), target=int(c['target']), skill=int(c['skill']),
                           tick=int(c['tick']), duration=int(c['duration']),
                           useIndex=int(c['use_index'])) for c in ai['commands']],
            path=[[int(p[0]), int(p[1])] for p in ai['path']],
            weapon=dict(type=int(weapon['type']), shootingRange=int(weapon['shootingRange']),
                        motion=int(weapon['motion']), projectileFlag=int(weapon['projectileFlag'])),
            boss=bool(spec.get('boss', False)), monsterType=int(spec.get('monsterType') or 0),
            specialHuman=bool(spec.get('specialHumanAnimation', False)),
            monsterSize=int(spec.get('monsterSize') or 0),
            human_flag=int(components[18]['flag']) if components[18] is not None else 0,
        )
        record['present_slots'] = component_presence(components)
        units.append(record)

    created = []
    for identity, entity in objects.items():
        if identity in engine.units:
            continue
        components = entity['components']
        created.append(dict(id=identity, destroyed=bool(entity['flags'] & 2),
                            present_slots=component_presence(components),
                            components=dict(
                                position=None if components[0] is None else
                                [float(components[0][0]), float(components[0][1]),
                                 float(components[0][2]), float(components[0][3]),
                                 float(components[0][4]), float(components[0][5]),
                                 components[0][6]],
                                speed=None if components[1] is None else
                                [float(v) for v in components[1][0:3]],
                                seb=None if components[2] is None else
                                [int(v) for v in components[2][0:4]],
                                depth=None if components[4] is None else
                                [int(v) for v in components[4][0:2]],
                                cell=None if components[5] is None else
                                [int(components[5][0]), int(components[5][1])],
                                image=None if components[7] is None else image_of(components[7]),
                                animation=None if components[12] is None else
                                [int(components[12][0]), int(components[12][1])],
                                direction=None if components[14] is None else
                                int(components[14][0]),
                                modifier=None if components[19] is None else
                                modifiers_of(components[19]),
                                garbage=None if components[32] is None else
                                int(components[32][0]),
                                effect=None if components[38] is None else
                                effect_of(components[38]),
                                projectile=None if components[39] is None else
                                projectile_of(components[39]),
                                attack=None if components[46] is None else
                                int(components[46][0]))))
    created.sort(key=lambda row: row['id'])

    subsets = []
    for subset in engine.world.subsets:
        slots = [None if value is None else int(value) for value in subset.members.slots]
        free = [int(value) for value in subset.members.free]
        subsets.append(dict(slots=slots, free=free, version=int(subset.members.version)))
    buckets = [[int(key), [int(v) for v in ids]] for key, ids in engine.occupancy.buckets.items()]
    snapshot = dict(
        config=dict(tick=int(engine.tick), first_identity=int(min(fighters)),
                    next_identity=int(engine.world.manager['created_entities']),
                    next_command=int(engine.next_command), map_width=int(engine.map_width),
                    row_offset=int(engine.row_offset),
                    movement_enabled=bool(engine.movement_enabled),
                    battle_state=int(engine.battle_state),
                    battle_frame=int(engine.battle_frame),
                    verdict=int(engine.verdict or 0),
                    verdict_tick=next((event['tick'] for event in engine.trace
                                       if event['kind'] == 'verdict'), -1),
                    ending_counter=-1 if engine.ending_counter is None else int(engine.ending_counter),
                    ending_gate_tick=-1 if getattr(engine, 'ending_gate_tick', None) is None
                    else int(engine.ending_gate_tick),
                    prize_count=len(engine.prizes)),
        units=units, objects=created, subsets=subsets, buckets=buckets,
        rng=dict(math=dict(values=[int(v) for v in engine.math_rng.values],
                           index=int(engine.math_rng.index), partner=int(engine.math_rng.partner),
                           draws=int(engine.math_draws)),
                 lib=dict(values=[int(v) for v in engine.lib_rng.values],
                          index=int(engine.lib_rng.index), partner=int(engine.lib_rng.partner),
                          draws=int(engine.lib_draws))),
        rows=needed_rows(units, created, rows_table),
        effect_resources={int(k): int(v) for k, v in engine.effect_resources.items()},
        prizes=None if engine.prize_candidates is None else
        [int(v) for v in engine.prize_candidates],
        projectile_sources=[[int(identity), int(source[0]), int(source[1]['id'])]
                            for identity, source in engine.projectile_sources.items()],
        human_bases=[int(v) for v in (human_bases or [])],
        # `resources[res]` is a list indexed by seb id; each entry is `{frame, max_frame}`.
        animation_resources=[[manager_index, seb_index, int(row['max_frame']), int(row['frame'])]
                             for manager_index, manager in enumerate(engine.animation_resources)
                             if manager is not None
                             for seb_index, row in enumerate(manager) if row is not None],
        consumables=consumable_shape(consumables),
    )
    return snapshot


def _fit_board(board, mapping):
    board.len = len(mapping)
    board.positions[:] = [0] * 128
    for position, key in enumerate(sorted(mapping)):
        board.entries[position].key = key
        board.entries[position].value = mapping[key]
        if 0 <= key < 128:
            board.positions[key] = position + 1


def _fill_entity(entity, record):
    """Common component payload copy, shared by fighters and created entities."""
    entity.destroyed = 1 if record['destroyed'] else 0
    entity.parent = -1
    for slot in record['present_slots']:
        entity.has[slot] = 1
    components = record['components']
    position = components['position']
    if position is not None:
        entity.position.x, entity.position.y, entity.position.z = position[0], position[1], position[2]
        entity.offset.x, entity.offset.y, entity.offset.z = position[3], position[4], position[5]
        entity.parent = -1 if position[6] is None else int(position[6])
    if components['speed'] is not None:
        entity.speed.x, entity.speed.y, entity.speed.z = components['speed'][0:3]
    if components['seb'] is not None:
        for index, value in enumerate(components['seb'][0:4]):
            entity.seb[index] = value
    if components['depth'] is not None:
        entity.depth[0], entity.depth[1] = components['depth'][0:2]
    if components['cell'] is not None:
        entity.cell[0], entity.cell[1] = components['cell'][0:2]
    for index in range(6):
        entity.image[index] = -1
    entity.animation[0], entity.animation[1] = 1, -1
    if components['image'] is not None:
        for index in range(6):
            entity.image[index] = components['image'][index]
    if components['animation'] is not None:
        entity.animation[0], entity.animation[1] = components['animation'][0:2]
    if components['direction'] is not None:
        entity.direction = components['direction']
    if components['modifier'] is not None:
        m = components['modifier']
        entity.modifier.type_ = m['type']
        entity.modifier.offset_x, entity.modifier.offset_y, entity.modifier.offset_z = \
            m['offset_x'], m['offset_y'], m['offset_z']
        entity.modifier.scale_x, entity.modifier.scale_y = m['scale_x'], m['scale_y']
        entity.modifier.angle, entity.modifier.anchor = m['angle'], m['anchor']
        entity.modifier.frame, entity.modifier.duration = m['frame'], m['duration']
        entity.modifier.destroy_on_finish = int(m['destroy_on_finish'])
        entity.modifier.looping = int(m['loop'])
        entity.modifier.alpha = m['alpha']
    if components['garbage'] is not None:
        entity.garbage = components['garbage']
    if components['effect'] is not None:
        e = components['effect']
        entity.effect.type_, entity.effect.value1, entity.effect.value2 = \
            e['type'], e['value1'], e['value2']
        entity.effect.depth = int(e['depth'])
        entity.effect.frame, entity.effect.max_frame = e['frame'], e['max_frame']
        entity.effect.parent = -1 if e['parent'] is None else e['parent']
        entity.effect.scale = e['scale']
    if components['projectile'] is not None:
        p = components['projectile']
        entity.projectile.start.x, entity.projectile.start.y, entity.projectile.start.z = \
            p['start'][0:3]
        entity.projectile.end.x, entity.projectile.end.y, entity.projectile.end.z = p['end'][0:3]
        entity.projectile.speed, entity.projectile.height = p['speed'], p['height']
        entity.projectile.frame, entity.projectile.length = p['frame'], p['length']
        entity.projectile.owner = p['owner']
    if components['attack'] is not None:
        entity.attack = components['attack']


def unit_to_native(record):
    unit = KaUnit()
    unit.present = 1 if record['present'] else 0
    unit.team = record['team']
    unit.human = int(record['human'])
    unit.monster = int(record['monster'])
    unit.flags = record['flags']
    unit.id = record['id']
    unit.identity = record['identity']
    unit.body.id = record['identity']
    _fill_entity(unit.body, record)
    unit.weapon_type = record['weapon']['type']
    unit.weapon_shooting_range = record['weapon']['shootingRange']
    unit.weapon_motion = record['weapon']['motion']
    unit.weapon_projectile_flag = record['weapon']['projectileFlag']
    unit.boss = int(record['boss'])
    unit.monster_type = record['monsterType']
    unit.special_human = int(record['specialHuman'])
    unit.monster_size = record['monsterSize']
    unit.human_flag = record['human_flag']
    _fit_board(unit.board, record['board'])
    _fit_board(unit.long_board, record['long_board'])
    parameters = record['parameters']
    unit.params.count = len(parameters)
    for position, key in enumerate(sorted(parameters)):
        row = parameters[key]
        target = unit.params.rows[position]
        target.id = key
        target.raw_value = row['rawValue']
        target.extra_value = row['extraValue']
        target.raw_max = row['rawMax']
        target.extra_max = row['extraMax']
        target.training_level = row['trainingLevel']
    unit.params.equipment_count = len(record['equipment'])
    for position, row in enumerate(record['equipment']):
        target = unit.params.equipment[position]
        target.level = row['level']
        target.pvp_level = row['pvpLevel']
        target.affinity = row['affinity']
        pairs = row['parameters']
        target.pair_count = len(pairs)
        for index, pair in enumerate(pairs):
            if pair is None:
                target.present[index] = 0
                continue
            target.present[index] = 1
            target.pairs[index][0], target.pairs[index][1] = int(pair[0]), int(pair[1])
    for index, value in enumerate(record['skills']):
        unit.skill_ids[index] = value
    unit.skill_count = len(record['skills'])
    for index, value in enumerate(record['levels']):
        unit.levels[index] = value
    unit.level_count = len(record['levels'])
    for index, (skill, remaining) in enumerate(record['invoking']):
        unit.invoking[index].skill = skill
        unit.invoking[index].remaining = remaining
    unit.invoking_count = len(record['invoking'])
    for index, command in enumerate(record['commands']):
        target = unit.commands[index]
        target.opcode = command['opcode']
        target.target = command['target']
        target.skill = command['skill']
        target.tick = command['tick']
        target.duration = command['duration']
        target.use_index = command['useIndex']
    unit.command_count = len(record['commands'])
    for index, point in enumerate(record['path']):
        unit.path[index].x, unit.path[index].y = point[0], point[1]
    unit.path_count = len(record['path'])
    return unit


def object_to_native(record):
    entity = KaEntity()
    entity.id = record['id']
    _fill_entity(entity, record)
    return entity


def load_snapshot(library, snapshot):
    """Install a snapshot into a fresh native battle; returns the battle pointer."""
    records = snapshot['units']
    units = (KaUnit * max(1, len(records)))()
    for index, record in enumerate(records):
        units[index] = unit_to_native(record)
    battle = library.ka_battle_create()
    library.ka_battle_import(battle, units, len(records), 0)
    config = snapshot['config']
    library.ka_battle_identity(battle, config['first_identity'])
    library.ka_battle_config(battle, config['map_width'], config['row_offset'],
                             int(config['movement_enabled']))
    library.ka_battle_set_counters(battle, config['tick'], config['next_identity'],
                                   config['next_command'])
    for key in sorted(snapshot['rows']):
        row = snapshot['rows'][key]
        skill = KaSkill(row['id'], row['category'], row['type'], row['flags'], row['minMp'],
                        row['maxMp'], row['requiredEquipType'], row['shootingRange'], row['range'],
                        row['count'], row['motion'], row['value'], row['seb'], row['img'],
                        row['impactImg'], row['impactSeb'])
        if library.ka_battle_add_row(battle, ctypes.byref(skill)) == 0:
            raise SystemExit('row table overflowed')
    for key in sorted(snapshot['effect_resources']):
        library.ka_battle_add_effect_resource(battle, key, snapshot['effect_resources'][key])
    if snapshot['prizes'] is not None:
        values = (ctypes.c_int32 * max(1, len(snapshot['prizes'])))(*snapshot['prizes'])
        library.ka_battle_set_prizes(battle, values, len(snapshot['prizes']))
    if snapshot['human_bases']:
        bases = (ctypes.c_int32 * len(snapshot['human_bases']))(*snapshot['human_bases'])
        library.ka_battle_set_human_bases(battle, bases, len(snapshot['human_bases']))
    for identity, caster, skill in snapshot['projectile_sources']:
        library.ka_battle_set_projectile_source(battle, identity, caster, skill)
    for res, seb, max_frame, frame in snapshot['animation_resources']:
        library.ka_battle_set_animation_resource(battle, res, seb, max_frame, frame)
    # Consumables travel with the candidate template: the schedule, the item table with its starting
    # stock, and the Holy Herb stock. A fresh battle has an empty dispatch log, which is exactly what
    # a clone must start from.
    consumables = snapshot.get('consumables')
    if consumables:
        if any(row['used'] for row in consumables['uses']):
            raise SystemExit('a snapshot with recorded consumable uses cannot be re-imported')
        library.ka_battle_set_herb_stock(battle, consumables['holy_herb_stock'])
        watch = [int(value) for value in consumables.get('mp_watch') or ()]
        watch_buffer = (ctypes.c_int32 * max(1, len(watch)))(*(watch or [0]))
        library.ka_battle_set_mp_watch(battle, watch_buffer, len(watch),
                                       int(consumables.get('holy_herb_max_uses', 0)))
        for row in consumables['items']:
            library.ka_battle_add_item(battle, 0, row['parameter'], row['all_residents'],
                                       row['bonus_min'], row['bonus_max'], row['stock'])
        for row in consumables['inputs']:
            library.ka_battle_add_input(battle, row['tick'], row['phase'], row['kind'], row['item'])
    config = snapshot['config']
    library.ka_battle_set_control(
        battle, config['battle_state'], config['battle_frame'], config['verdict'],
        config['verdict_tick'] if 'verdict_tick' in config else -1,
        config['ending_counter'], config['ending_gate_tick'], 0, -1, config['prize_count'])
    for slot, record in enumerate(snapshot['objects']):
        entity = object_to_native(record)
        library.ka_battle_set_object(battle, slot, ctypes.byref(entity))
    for index, subset in enumerate(snapshot['subsets']):
        slots = [0 if value is None else value for value in subset['slots']]
        slot_buffer = (ctypes.c_int32 * max(1, len(slots)))(*slots)
        free_buffer = (ctypes.c_uint32 * max(1, len(subset['free'])))(*subset['free'])
        library.ka_battle_set_subset(battle, index, len(slots), len(subset['free']),
                                     subset['version'], slot_buffer, free_buffer)
    for slot, (key, ids) in enumerate(snapshot['buckets']):
        buffer = (ctypes.c_int32 * max(1, len(ids)))(*ids)
        library.ka_battle_set_bucket(battle, slot, key, len(ids), buffer)
    for stream, name in ((0, 'math'), (1, 'lib')):
        state = snapshot['rng'][name]
        values = (ctypes.c_int32 * 56)(*state['values'])
        library.ka_battle_set_rng(battle, stream, values, state['index'], state['partner'],
                                  state['draws'])
    return battle


def native_events(library, battle):
    """Copy the last phase call's native event log out as 7-tuples."""
    total = library.ka_battle_event_count(battle)
    buffer = (KaEvent * max(1, total))()
    if total:
        library.ka_battle_export_events(battle, buffer)
    return [(buffer[i].kind, buffer[i].unit, buffer[i].a, buffer[i].b, buffer[i].c,
             buffer[i].d, buffer[i].e) for i in range(total)]


def _read_board(board):
    return {board.entries[index].key: board.entries[index].value for index in range(board.len)}


def _read_entity(entity):
    components = dict(position=None, speed=None, seb=None, depth=None, cell=None, image=None,
                      animation=None, direction=None, modifier=None, garbage=None, effect=None,
                      projectile=None, attack=None)
    if entity.has[SLOT_POSITION]:
        components['position'] = [entity.position.x, entity.position.y, entity.position.z,
                                  entity.offset.x, entity.offset.y, entity.offset.z,
                                  None if entity.parent < 0 else entity.parent]
    if entity.has[SLOT_SPEED]:
        components['speed'] = [entity.speed.x, entity.speed.y, entity.speed.z]
    if entity.has[SLOT_SEB]:
        components['seb'] = [entity.seb[i] for i in range(4)]
    if entity.has[SLOT_DEPTH]:
        components['depth'] = [entity.depth[0], entity.depth[1]]
    if entity.has[SLOT_CELL]:
        components['cell'] = [entity.cell[0], entity.cell[1]]
    if entity.has[SLOT_IMAGE]:
        components['image'] = [entity.image[i] for i in range(6)]
    if entity.has[SLOT_ANIMATION]:
        components['animation'] = [entity.animation[0], entity.animation[1]]
    if entity.has[SLOT_DIRECTION]:
        components['direction'] = entity.direction
    if entity.has[SLOT_MODIFY_ANIMATION]:
        m = entity.modifier
        components['modifier'] = dict(type=m.type_, offset_x=m.offset_x, offset_y=m.offset_y,
                                      offset_z=m.offset_z, scale_x=m.scale_x, scale_y=m.scale_y,
                                      angle=m.angle, anchor=m.anchor, frame=m.frame,
                                      duration=m.duration,
                                      destroy_on_finish=bool(m.destroy_on_finish),
                                      loop=bool(m.looping), alpha=m.alpha)
    if entity.has[SLOT_GARBAGE]:
        components['garbage'] = entity.garbage
    if entity.has[SLOT_EFFECT]:
        e = entity.effect
        components['effect'] = dict(type=e.type_, value1=e.value1, value2=e.value2,
                                    depth=bool(e.depth), frame=e.frame, max_frame=e.max_frame,
                                    parent=None if e.parent < 0 else e.parent, scale=e.scale)
    if entity.has[SLOT_PROJECTILE]:
        p = entity.projectile
        components['projectile'] = dict(start=[p.start.x, p.start.y, p.start.z],
                                        end=[p.end.x, p.end.y, p.end.z], speed=p.speed,
                                        height=p.height, frame=p.frame, length=p.length,
                                        owner=p.owner)
    if entity.has[SLOT_ATTACK]:
        components['attack'] = entity.attack
    return components


def native_snapshot(library, battle, snapshot):
    """Read a stepped native battle back into the same snapshot shape as `engine_snapshot`."""
    tick = ctypes.c_uint32()
    next_identity = ctypes.c_int32()
    next_command = ctypes.c_int32()
    library.ka_battle_counters(battle, ctypes.byref(tick), ctypes.byref(next_identity),
                               ctypes.byref(next_command))
    units_buffer = (KaUnit * KA_MAX_UNITS)()
    count = library.ka_battle_export(battle, units_buffer)
    units = []
    for index in range(count):
        unit = units_buffer[index]
        present_slots = [slot for slot in range(KA_SLOT_COUNT) if unit.body.has[slot]]
        parameters = {}
        for position in range(unit.params.count):
            row = unit.params.rows[position]
            parameters[row.id] = dict(rawValue=row.raw_value, extraValue=row.extra_value,
                                      rawMax=row.raw_max, extraMax=row.extra_max,
                                      trainingLevel=row.training_level)
        equipment = []
        for position in range(unit.params.equipment_count):
            row = unit.params.equipment[position]
            pairs = []
            for index in range(row.pair_count):
                pairs.append(None if row.present[index] == 0
                             else [row.pairs[index][0], row.pairs[index][1]])
            equipment.append(dict(level=row.level, pvpLevel=row.pvp_level,
                                  affinity=row.affinity, parameters=pairs))
        units.append(dict(
            identity=unit.identity, present=unit.present, id=unit.id, team=unit.team,
            human=bool(unit.human), monster=bool(unit.monster), flags=unit.flags,
            destroyed=bool(unit.body.destroyed), present_slots=present_slots,
            components=_read_entity(unit.body),
            board=_read_board(unit.board), long_board=_read_board(unit.long_board),
            parameters=parameters, equipment=equipment,
            skills=[unit.skill_ids[i] for i in range(unit.skill_count)],
            levels=[unit.levels[i] for i in range(unit.level_count)],
            invoking=[[unit.invoking[i].skill, unit.invoking[i].remaining]
                      for i in range(unit.invoking_count)],
            commands=[dict(opcode=unit.commands[i].opcode, target=unit.commands[i].target,
                           skill=unit.commands[i].skill, tick=unit.commands[i].tick,
                           duration=unit.commands[i].duration, useIndex=unit.commands[i].use_index)
                      for i in range(unit.command_count)],
            path=[[unit.path[i].x, unit.path[i].y] for i in range(unit.path_count)],
            weapon=dict(type=unit.weapon_type, shootingRange=unit.weapon_shooting_range,
                        motion=unit.weapon_motion, projectileFlag=unit.weapon_projectile_flag),
            boss=bool(unit.boss), monsterType=unit.monster_type,
            specialHuman=bool(unit.special_human), monsterSize=unit.monster_size,
            human_flag=unit.human_flag))
    objects = []
    for slot in range(library.ka_object_count(battle)):
        entity = KaEntity()
        library.ka_object_entity(battle, slot, ctypes.byref(entity))
        objects.append(dict(id=entity.id, destroyed=bool(entity.destroyed),
                            present_slots=[s for s in range(KA_SLOT_COUNT) if entity.has[s]],
                            components=_read_entity(entity)))
    subsets = []
    for index in range(KA_SUBSET_COUNT):
        length = ctypes.c_uint32()
        free_len = ctypes.c_uint32()
        version = ctypes.c_int32()
        slots = (ctypes.c_int32 * (KA_MAX_UNITS + library.object_capacity))()
        free = (ctypes.c_uint32 * (KA_MAX_UNITS + library.object_capacity))()
        library.ka_subset_state(battle, index, ctypes.byref(length), ctypes.byref(free_len),
                                ctypes.byref(version), slots, free)
        raw = [slots[i] for i in range(length.value)]
        free_list = [free[i] for i in range(free_len.value)]
        # A freed slot holds a stale value natively; the Python `EntitySlotSet` stores `None` there,
        # so encode holes from the free list to keep the round trip lossless.
        holes = set(free_list)
        subsets.append(dict(slots=[None if index in holes else raw[index]
                                   for index in range(len(raw))],
                            free=free_list, version=version.value))
    buckets = []
    for slot in range(library.ka_bucket_count(battle)):
        key = ctypes.c_int32()
        count = ctypes.c_uint32()
        ids = (ctypes.c_int32 * 64)()
        library.ka_bucket_state(battle, slot, ctypes.byref(key), ctypes.byref(count), ids)
        buckets.append([key.value, [ids[i] for i in range(count.value)]])
    rng = {}
    for stream, name in ((0, 'math'), (1, 'lib')):
        values = (ctypes.c_int32 * 56)()
        index = ctypes.c_int32()
        partner = ctypes.c_int32()
        draws = ctypes.c_uint32()
        library.ka_rng_state(battle, stream, values, ctypes.byref(index), ctypes.byref(partner),
                             ctypes.byref(draws))
        rng[name] = dict(values=[values[i] for i in range(56)], index=index.value,
                         partner=partner.value, draws=draws.value)
    sources = []
    for slot in range(library.ka_projectile_source_count(battle)):
        identity = ctypes.c_int32()
        caster = ctypes.c_int32()
        skill = ctypes.c_int32()
        library.ka_projectile_source(battle, slot, ctypes.byref(identity), ctypes.byref(caster),
                                     ctypes.byref(skill))
        sources.append([identity.value, caster.value, skill.value])
    animation = []
    for slot in range(library.ka_animation_resource_count(battle)):
        res = ctypes.c_int32()
        seb = ctypes.c_int32()
        max_frame = ctypes.c_int32()
        frame = ctypes.c_int32()
        library.ka_animation_resource(battle, slot, ctypes.byref(res), ctypes.byref(seb),
                                      ctypes.byref(max_frame), ctypes.byref(frame))
        animation.append([res.value, seb.value, max_frame.value, frame.value])
    _report = KaBattleReport()
    library.ka_battle_report(battle, 0, ctypes.byref(_report))
    _final = dict(battle_state=_report.battle_state, battle_frame=_report.battle_frame,
                  verdict=_report.verdict, verdict_tick=_report.verdict_tick,
                  ending_counter=_report.ending_counter, ending_gate_tick=_report.ending_gate_tick,
                  prize_count=_report.prize_callbacks)
    return dict(config=dict(tick=tick.value, first_identity=snapshot['config']['first_identity'],
                            next_identity=next_identity.value, next_command=next_command.value,
                            map_width=snapshot['config']['map_width'],
                            row_offset=snapshot['config']['row_offset'],
                            movement_enabled=snapshot['config']['movement_enabled'],
                            battle_state=_final['battle_state'],
                            battle_frame=_final['battle_frame'],
                            verdict=_final['verdict'],
                            verdict_tick=_final['verdict_tick'],
                            ending_counter=_final['ending_counter'],
                            ending_gate_tick=_final['ending_gate_tick'],
                            prize_count=_final['prize_count']),
                units=units, objects=objects, subsets=subsets, buckets=buckets, rng=rng,
                rows=snapshot['rows'], effect_resources=snapshot['effect_resources'],
                prizes=snapshot['prizes'], human_bases=snapshot['human_bases'],
                projectile_sources=sorted(sources, key=lambda row: row[0]),
                animation_resources=sorted(animation, key=lambda row: (row[0], row[1])),
                consumables=native_consumable_state(library, battle))


def _round(value):
    if isinstance(value, float):
        return round(value, 4)
    return value


def diff_snapshots(expected, actual, path='', out=None):
    """Structural diff in the snapshot's own shape; returns a list of human-readable differences."""
    if out is None:
        out = []
    if len(out) > 40:
        return out
    if isinstance(expected, dict) and isinstance(actual, dict):
        for key in sorted(set(expected) | set(actual), key=str):
            if key not in expected:
                out.append(f'{path}.{key}: unexpected in native = {actual[key]!r}')
            elif key not in actual:
                out.append(f'{path}.{key}: missing in native (python = {expected[key]!r})')
            else:
                diff_snapshots(expected[key], actual[key], f'{path}.{key}', out)
    elif isinstance(expected, (list, tuple)) and isinstance(actual, (list, tuple)):
        if len(expected) != len(actual):
            out.append(f'{path}: length python={len(expected)} native={len(actual)}')
        else:
            for index, (left, right) in enumerate(zip(expected, actual)):
                diff_snapshots(left, right, f'{path}[{index}]', out)
    else:
        if _round(expected) != _round(actual):
            out.append(f'{path}: python={expected!r} native={actual!r}')
    return out
