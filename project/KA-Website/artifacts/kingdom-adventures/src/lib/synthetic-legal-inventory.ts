import { EQUIPMENT_CATALOG as BATTLE_EQUIPMENT_CATALOG, equipmentSlotForEntry } from "@/lib/battle-setup";
import { EQUIPMENT_CATALOG as SITE_EQUIPMENT_CATALOG } from "@/lib/generated-equipment-data";
import type { PlayerProfile } from "@/lib/synthetic-legal-model";

const siteByName = new Map<string, (typeof SITE_EQUIPMENT_CATALOG)[number]>(SITE_EQUIPMENT_CATALOG.map((entry) => [entry.name, entry]));

/**
 * Shared display projection of the canonical site gear catalog and recovered battle catalog.
 * The site catalog excludes tools and placeholder rows; the battle catalog provides slot and
 * native item order. This keeps non-combat tools (including Fishing Rod) out of build inventory.
 */
export const SYNTHETIC_LEGAL_EQUIPMENT_CATALOG = BATTLE_EQUIPMENT_CATALOG.flatMap((battleEntry, sourceOrder) => {
  const siteEntry = siteByName.get(battleEntry.name);
  if (!siteEntry) return [];
  return [{
    id: siteEntry.id,
    name: siteEntry.name,
    rank: siteEntry.rank,
    rankLabel: siteEntry.rankLabel,
    slot: equipmentSlotForEntry(battleEntry),
    weaponType: battleEntry.type === 10 ? "Tool" : undefined,
    sourceOrder,
    battleId: battleEntry.id,
  }];
});

export type SyntheticLegalEquipment = (typeof SYNTHETIC_LEGAL_EQUIPMENT_CATALOG)[number];

/** Fill newly introduced catalog entries as unknown without changing existing declarations. */
export function ensureEquipmentCatalog(profile: PlayerProfile): PlayerProfile {
  const present = new Map(profile.equipment.map((entry) => [entry.equipmentName, entry]));
  const equipment = SYNTHETIC_LEGAL_EQUIPMENT_CATALOG.map((entry) => present.get(entry.name) ?? ({
    id: `equipment:${entry.name}`,
    equipmentName: entry.name,
    ownership: "unknown" as const,
    quantity: 0,
    currentLevel: 1,
  }));
  const catalogNames = new Set<string>(SYNTHETIC_LEGAL_EQUIPMENT_CATALOG.map((entry) => entry.name));
  equipment.push(...profile.equipment.filter((entry) => !catalogNames.has(entry.equipmentName)));
  if (equipment.length === profile.equipment.length && equipment.every((entry, index) => entry === profile.equipment[index])) return profile;
  return { ...profile, equipment };
}
