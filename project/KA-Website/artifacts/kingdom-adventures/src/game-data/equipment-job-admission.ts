/**
 * Native job-equipment admission: `JobData.GetAffinity 0x16208d0` + `EquipData.CanEquip 0x1620e14`.
 *
 * Recovered rule (canonical index + frozen slices; narrow, no raw-RE pass):
 *  * `GetAffinity(job, entity, equip)` indexes the job group's affinity array by the equipment
 *    TYPE (`EquipData +0x4c`, not `+0x48` category) and returns 1 when the job group is missing
 *    (`RE-evidence/20260912-combat/16208d0.asm`).
 *  * While the indexed affinity is -1 or 0 (`add w8,w21,#1; cmp w8,#1; b.hi` skips every value
 *    >= 1), the method searches the entity's POSSESSED skills for one whose `type` is 48
 *    (`TYPE_EQUIP_MASTER`, the literal `0x30` stored into the search record) and whose `value`
 *    (`SkillData +0x30`) equals the equipment type (`EquipData +0x4c`); the predicate is
 *    `data.JobData.<>c__DisplayClass193_0$$<GetAffinity>b__0 0x162cc34`. A match makes the method
 *    return 1 (`tst w0,#1; csinc w0,w21,wzr,eq`), otherwise the indexed affinity is returned.
 *  * `EquipData.CanEquip 0x1620e14` calls `GetAffinity` and rejects exactly the value -1
 *    (`cmn w0,#1; b.eq` -> false). Affinity 0 does NOT reject the item there; it only halves a
 *    positive contribution in the parameter wrapper `0x16207b8` (`special-combat.md:225-227`).
 *
 * Consequence: a matching EQUIP_MASTER skill lifts BOTH a rejected (-1) and a weak (0) verdict,
 * because the override happens inside `GetAffinity`, before `CanEquip` ever sees the value. The
 * site's declared job-access table (`job-equipment.ts`, `can` / `weak` / `cannot`) is the declared
 * input that stands in for those three values; the override itself is native.
 *
 * This module owns only that rule. It does not own the access table (`job-equipment.ts`), the skill
 * catalog (`battle-setup.ts`) or the saved-loadout conversion (`battle-legality.ts`).
 */
import { SKILL_BY_ID } from "@/lib/battle-setup";
import type { WeaponValue } from "./job-equipment";

/** `SkillData.TYPE_EQUIP_MASTER` (`dump.cs`); the only skill type `GetAffinity` searches for. */
export const SKILL_TYPE_EQUIP_MASTER = 48;

/** `EquipData.type` of the shield slot (`equipmentSlotForEntry`). */
export const EQUIP_TYPE_SHIELD = 11;

/** `GetAffinity` values. -1 is the only value `CanEquip` rejects. */
export const EQUIP_AFFINITY_NORMAL = 1;
export const EQUIP_AFFINITY_WEAK = 0;
export const EQUIP_AFFINITY_REJECTED = -1;

/** The three declared job-access verdicts mapped onto the three native affinity values. */
export function declaredAffinityForAccess(access: WeaponValue | null | undefined): number {
  if (access === "cannot") return EQUIP_AFFINITY_REJECTED;
  if (access === "weak") return EQUIP_AFFINITY_WEAK;
  return EQUIP_AFFINITY_NORMAL;
}

/**
 * First possessed skill the native predicate accepts: `SkillData.type == 48` and
 * `SkillData.value == EquipData.type`. Returns `null` when the equipment type is unknown or no
 * possessed row matches, matching the predicate's `cbz` early exits.
 */
export function equipMasterSkillForEquipmentType(
  possessedSkillIds: readonly number[],
  equipmentType: number | null,
): number | null {
  if (equipmentType === null) return null;
  for (const skillId of possessedSkillIds) {
    const row = SKILL_BY_ID.get(skillId);
    if (row && row.type === SKILL_TYPE_EQUIP_MASTER && row.value === equipmentType) return skillId;
  }
  return null;
}

export type EquipmentAffinityVerdict = {
  /** The affinity `GetAffinity` returns for this (job, equipment, possessed skills) triple. */
  affinity: number;
  /** The declared value before the type-48 override. */
  declaredAffinity: number;
  /** `CanEquip` result: false only when the final affinity is -1. */
  allowed: boolean;
  /** True when a possessed EQUIP_MASTER row lifted a -1 or 0 verdict to 1. */
  overridden: boolean;
  /** The matching possessed skill id, or null when the override did not apply. */
  equipMasterSkillId: number | null;
};

export function resolveEquipmentAffinity({
  access,
  equipmentType,
  possessedSkillIds,
}: {
  access: WeaponValue | null | undefined;
  equipmentType: number | null;
  possessedSkillIds: readonly number[];
}): EquipmentAffinityVerdict {
  const declaredAffinity = declaredAffinityForAccess(access);
  const searchable =
    declaredAffinity === EQUIP_AFFINITY_REJECTED || declaredAffinity === EQUIP_AFFINITY_WEAK;
  const equipMasterSkillId = searchable
    ? equipMasterSkillForEquipmentType(possessedSkillIds, equipmentType)
    : null;
  const affinity = equipMasterSkillId === null ? declaredAffinity : EQUIP_AFFINITY_NORMAL;
  return {
    affinity,
    declaredAffinity,
    allowed: affinity !== EQUIP_AFFINITY_REJECTED,
    overridden: equipMasterSkillId !== null,
    equipMasterSkillId,
  };
}
