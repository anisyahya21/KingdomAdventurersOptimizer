/**
 * Device-wide universal valuable settings (the Loadout Builder's "Universal Settings" card) and
 * their carryover into the battle pages.
 *
 * The Loadout Builder stores "how many of each Water of ... you have used" once per device under
 * `ka_resident_stat_items` and mirrors the counts onto every saved loadout (`residentStatItems`).
 * The battle pages read the same device key. The universal setting is authoritative even when
 * a character carries an older mirrored value or the universal counts are all zero.
 *
 * Counts are replaced, never summed, so one water is worth its `amount` exactly once.
 */
import { RESIDENT_STAT_ITEMS, type ResidentStatItemCounts } from "@/game-data/resident-stat-items";

/** The device-wide storage key the Loadout Builder writes and the battle pages read. */
export const RESIDENT_STAT_ITEMS_KEY = "ka_resident_stat_items";

/**
 * Defensive read of the stored device counts: known water keys with a positive whole count only.
 * An unknown key or a malformed value is dropped rather than carried as a bonus.
 */
export function deviceResidentValuables(raw: unknown): ResidentStatItemCounts {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return {};
  const record = raw as Record<string, unknown>;
  const counts: ResidentStatItemCounts = {};
  for (const item of RESIDENT_STAT_ITEMS) {
    const value = record[item.key];
    if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) continue;
    counts[item.key] = Math.floor(value);
  }
  return counts;
}

export function withResidentValuables<T extends { residentStatItems?: ResidentStatItemCounts }>(
  loadout: T,
  device: ResidentStatItemCounts,
): T {
  return { ...loadout, residentStatItems: { ...device } };
}

/** Apply the universal setting to every character, including an explicit zero count. */
export function loadoutsWithResidentValuables<T extends { residentStatItems?: ResidentStatItemCounts }>(
  loadouts: T[],
  device: ResidentStatItemCounts,
): T[] {
  return loadouts.map((loadout) => withResidentValuables(loadout, device));
}
