import { getJobProfile } from "@/game-data/job-profile";
import { canSetSkill } from "@/game-data/skill-job-admission";
import { resolveEquipmentAffinity } from "@/game-data/equipment-job-admission";
import { EQUIPMENT_BY_ID } from "@/lib/battle-setup";
import { resolveCombatEquipmentId, SKILL_ID_BY_NAME, type SavedLoadout, type SharedLoadoutData } from "@/lib/battle-legality";
import { gearSlotForName } from "@/lib/battle-team-draft";

/** Apply the same native skill admission check used when the team is converted. */
export function canPickHumanSkill(jobName: string, name: string, selected: readonly string[]): boolean {
  const skillId = SKILL_ID_BY_NAME.get(name);
  if (!jobName || skillId === undefined) return false;
  const existingSkillIds = selected
    .map((entry) => SKILL_ID_BY_NAME.get(entry))
    .filter((id): id is number => id !== undefined);
  return canSetSkill({ jobName, skillId, existingSkillIds }).status === "admitted";
}

/** A matching equipped resistance skill may lift a weapon or shield's rejected affinity. */
export function canPickHumanEquipment(
  character: Pick<SavedLoadout, "jobName" | "skills">,
  data: SharedLoadoutData,
  name: string,
): boolean {
  if (!character.jobName) return false;
  const slot = gearSlotForName(name, data.slotAssignments);
  if (slot !== "weapon" && slot !== "shield") return true;
  const profile = getJobProfile(data, character.jobName);
  if (!profile) return false;
  const id = resolveCombatEquipmentId(name);
  const entry = id === null ? undefined : EQUIPMENT_BY_ID.get(id);
  if (!entry) return false;
  const weaponType = slot === "shield" ? "Shield" : data.weaponTypes?.[name] ?? null;
  const access = slot === "shield"
    ? profile.equipmentAccess.shield
    : weaponType && weaponType !== "Tool"
      ? profile.equipmentAccess.weapons[weaponType]
      : null;
  const possessedSkillIds = (character.skills ?? [])
    .map((skill) => SKILL_ID_BY_NAME.get(skill))
    .filter((skillId): skillId is number => skillId !== undefined);
  return resolveEquipmentAffinity({ access, equipmentType: entry.type, possessedSkillIds }).allowed;
}

/** Clear only weapon/shield entries that cease to be equippable after a skill or job change. */
export function dropUnsupportedHumanEquipment<T extends SavedLoadout>(character: T, data: SharedLoadoutData): T {
  const equipment = (character.equipment ?? []).filter((item) => canPickHumanEquipment(character, data, item.name));
  return equipment.length === (character.equipment ?? []).length ? character : { ...character, equipment };
}
