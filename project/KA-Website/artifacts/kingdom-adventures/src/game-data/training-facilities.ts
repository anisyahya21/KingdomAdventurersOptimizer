import facilityLookupCsv from "../../../../data/sheet-research/raw-copies/KA GameData - Facility_lookup.csv?raw";
import { FACILITIES, type FacilityTab } from "@/game-data/facilities";
import { parseCsv } from "@/lib/csv";

// Source: KA GameData - Facility_lookup.csv. It supplies the per-level
// parameter values and native facility flags used by the training view.
export const TRAINING_STATS = [
  { name: "HP", paramId: 10 },
  { name: "MP", paramId: 11 },
  { name: "Vigor", paramId: 12 },
  { name: "Attack", paramId: 13 },
  { name: "Defence", paramId: 14 },
  { name: "Speed", paramId: 15 },
  { name: "Luck", paramId: 16 },
  { name: "Intelligence", paramId: 18 },
  { name: "Dexterity", paramId: 19 },
  { name: "Gather", paramId: 20 },
  { name: "Move", paramId: 21 },
  { name: "Heart", paramId: 22 },
] as const;

export type TrainingStatName = (typeof TRAINING_STATS)[number]["name"];
export type TrainingXp = { initial: number; increment: number };
export type TrainingFacilityRecord = {
  id: number;
  name: string;
  stats: TrainingStatName[];
  baseXp: Partial<Record<TrainingStatName, TrainingXp>>;
  flags: number;
  canUpgrade: boolean;
};
export type XpEmitter = {
  facilityId: number;
  facilityName: string;
  facilityTab?: FacilityTab;
  slot: number;
  type: number;
  min: number;
  max: number;
};

const CSV_STAT_HEADERS: Record<TrainingStatName, string> = {
  HP: "HP",
  MP: "MP",
  Vigor: "Vigor",
  Attack: "Atk",
  Defence: "Def",
  Speed: "Spd",
  Luck: "Luck",
  Intelligence: "Int",
  Dexterity: "Dex",
  Gather: "Gather",
  Move: "Move",
  Heart: "Heart",
};

function integer(value: string | undefined, fallback = 0): number {
  if (value === undefined || value.trim() === "") return fallback;
  const parsed = Number(value.trim());
  return Number.isSafeInteger(parsed) ? parsed : fallback;
}

function headerIndex(header: string[], name: string): number {
  const normalized = name.trim().toLowerCase();
  // Facility_lookup repeats labels: retain the final match, which is the
  // initial-value column in the bonusParameters block, rather than the
  // unrelated HP/MP/Vigor summary fields earlier in the row.
  let found = -1;
  for (let index = 0; index < header.length; index += 1) {
    if (header[index]?.trim().toLowerCase() === normalized) found = index;
  }
  return found;
}

function firstHeaderIndex(header: string[], name: string): number {
  const normalized = name.trim().toLowerCase();
  return header.findIndex((column) => column.trim().toLowerCase() === normalized);
}

const rows = parseCsv(facilityLookupCsv);
const header = rows[0] ?? [];
const columns = new Map<string, number>();
for (const name of ["id", "name", "flag", ...Array.from({ length: 3 }, (_, slot) => [
  `aroundEffectCategory${slot + 1}`,
  `aroundEffectType${slot + 1}`,
  `aroundEffectMinValue${slot + 1}`,
  `aroundEffectMaxValue${slot + 1}`,
]).flat()]) {
  columns.set(name, name === "flag" ? firstHeaderIndex(header, name) : headerIndex(header, name));
}

const sourceFacilities = new Map(FACILITIES.map((facility) => [facility.id, facility]));
const dataRows = rows.slice(1).filter((row) => row.some((value) => value.trim() !== ""));

export const FACILITY_TRAINING_DATA: TrainingFacilityRecord[] = dataRows
  .map((row) => {
    const id = integer(row[columns.get("id") ?? -1], -1);
    const name = row[columns.get("name") ?? -1]?.trim() ?? "";
    if (id < 0 || !name) return null;

    const baseXp: Partial<Record<TrainingStatName, TrainingXp>> = {};
    for (const { name: stat } of TRAINING_STATS) {
      const initialColumn = headerIndex(header, CSV_STAT_HEADERS[stat]);
      if (initialColumn < 0 || initialColumn + 1 >= row.length) continue;
      baseXp[stat] = {
        initial: integer(row[initialColumn]),
        increment: integer(row[initialColumn + 1]),
      };
    }
    const stats = TRAINING_STATS
      .filter(({ name: stat }) => (baseXp[stat]?.initial ?? 0) > 0 || (baseXp[stat]?.increment ?? 0) > 0)
      .map(({ name: stat }) => stat);
    const flags = integer(row[columns.get("flag") ?? -1]);
    return { id, name, stats, baseXp, flags, canUpgrade: (flags & 32) === 0 };
  })
  .filter((facility): facility is TrainingFacilityRecord => facility !== null)
  .sort((left, right) => left.id - right.id);

// FacilityComponent.get_isAvailable (0x14c8a14) requires FLAG_USE = 4.
// Both furniture/outdoor use-state XP consumers are reached through this gate.
// Several decorative/recovery rows have unused bonus pairs: they are not
// training facilities. This gate does not remove their defined XP aura effects.
export const TRAINING_FACILITIES = FACILITY_TRAINING_DATA.filter((facility) => facility.stats.length > 0 && (facility.flags & 4) !== 0);

export const XP_EMITTERS: XpEmitter[] = dataRows.flatMap((row) => {
  const facilityId = integer(row[columns.get("id") ?? -1], -1);
  const csvName = row[columns.get("name") ?? -1]?.trim() ?? "";
  if (facilityId < 0 || !csvName) return [];
  const shared = sourceFacilities.get(facilityId);
  return Array.from({ length: 3 }, (_, index): XpEmitter | null => {
    const slot = index + 1;
    const read = (field: string) => integer(row[columns.get(`aroundEffect${field}${slot}`) ?? -1]);
    if (read("Category") !== 0) return null;
    return {
      facilityId,
      facilityName: shared?.name ?? csvName,
      ...(shared ? { facilityTab: shared.tab } : {}),
      slot,
      type: read("Type"),
      min: read("MinValue"),
      max: read("MaxValue"),
    };
  }).filter((emitter): emitter is XpEmitter => emitter !== null);
});
