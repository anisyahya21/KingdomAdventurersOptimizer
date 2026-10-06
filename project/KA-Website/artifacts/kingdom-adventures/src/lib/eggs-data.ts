import { googleSheetUrl } from "@/lib/api";


export type EggStat = "Attack" | "Defense" | "Balanced" | "Special";
export type EggBand = "None" | "Low" | "Medium" | "High" | "Over";

export interface EggMonsterOutcome {
  eggColor: string;
  monsterName: string;
  requiredStat: EggStat;
  thresholds: Record<Exclude<EggBand, "None">, number | null>;
  startingSkill: string;
  secondSkillItem: string;
}

export interface EggFeedItem {
  name: string;
  copperCoins: number | null;
  stats: Record<EggStat, number>;
  exp: number;
  hatchTimeSeconds: number | null;
  remarks: string;
}

export interface EggReferenceData {
  monsters: EggMonsterOutcome[];
  feedItems: EggFeedItem[];
  eggColors: string[];
}

type GoogleVizResponse = {
  table?: {
    rows?: Array<{
      c?: Array<{ v?: string | number | null; f?: string | null } | null>;
    }>;
  };
};

function parseGoogleViz(text: string): GoogleVizResponse {
  const json = text.replace(/^[^(]+\(/, "").replace(/\);?\s*$/, "");
  return JSON.parse(json) as GoogleVizResponse;
}

function cellValue(
  cells: Array<{ v?: string | number | null; f?: string | null } | null> | undefined,
  index: number
): string | number | null {
  if (!cells || index < 0 || index >= cells.length) return null;
  const cell = cells[index];
  if (!cell) return null;
  return cell.v ?? cell.f ?? null;
}

function asText(value: string | number | null): string {
  return String(value ?? "").trim();
}

function parseNumber(value: string | number | null): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  const raw = asText(value);
  if (!raw || raw === "-" || raw === "?" || raw.toLowerCase() === "jiu") return null;
  const match = raw.match(/-?\d+(?:\.\d+)?/);
  return match ? Number(match[0]) : null;
}

function parseEggStat(value: string | number | null): EggStat | null {
  const raw = asText(value).toLowerCase();
  if (raw === "attack" || raw === "atk") return "Attack";
  if (raw === "defense" || raw === "defence" || raw === "def") return "Defense";
  if (raw === "balanced" || raw === "balance") return "Balanced";
  if (raw === "special") return "Special";
  return null;
}

function parseHatchTimeSeconds(remarks: string): number | null {
  const match = remarks.match(/(-?\d+(?:\.\d+)?)\s*seconds?/i);
  return match ? Number(match[1]) : null;
}

function toMonsterOutcome(
  cells: Array<{ v?: string | number | null; f?: string | null } | null>
): EggMonsterOutcome | null {
  const monsterName = asText(cellValue(cells, 1));
  if (!monsterName) return null;

  const requiredStat = parseEggStat(cellValue(cells, 2));
  if (!requiredStat) return null;

  return {
    eggColor: asText(cellValue(cells, 0)),
    monsterName,
    requiredStat,
    thresholds: {
      Low: parseNumber(cellValue(cells, 3)),
      Medium: parseNumber(cellValue(cells, 4)),
      High: parseNumber(cellValue(cells, 5)),
      Over: parseNumber(cellValue(cells, 6)),
    },
    startingSkill: asText(cellValue(cells, 7)),
    secondSkillItem: asText(cellValue(cells, 8)),
  };
}

// Known name corrections from the Google Sheet source to canonical item names.
// Keys are lowercased for case-insensitive matching.
const FEED_ITEM_NAME_CORRECTIONS: Record<string, string> = {
  "hearty mushroom": "Healthy Mushroom",
  "carbonated juice": "Fizzy Juice",
  "kingdom flag": "National Flag",
  "rice omelette": "Rice Omelet",
  // Sage's Tome — handle apostrophe/spacing variants from Google Sheets
  "sage\u2019s tome": "Sage's Tome",
  "sage's tome": "Sage's Tome",
  "sage \u2019s tome": "Sage's Tome",
  "sage 's tome": "Sage's Tome",
  // Grape Juice — bad Japanese romanization from sheet
  "budjusjus": "Grape Juice",
  "budjujus": "Grape Juice",
};

// Rows from the sheet that are clearly garbage data and should be dropped.
const FEED_ITEM_GARBAGE_NAMES = new Set(["6", "thailand"]);

function toFeedItem(
  cells: Array<{ v?: string | number | null; f?: string | null } | null>
): EggFeedItem | null {
  const rawName = asText(cellValue(cells, 10));
  if (!rawName) return null;
  if (FEED_ITEM_GARBAGE_NAMES.has(rawName.toLowerCase())) return null;
  const name = FEED_ITEM_NAME_CORRECTIONS[rawName.toLowerCase()] ?? rawName;
  const remarks = asText(cellValue(cells, 17));

  return {
    name,
    copperCoins: parseNumber(cellValue(cells, 11)),
    stats: {
      Attack: parseNumber(cellValue(cells, 12)) ?? 0,
      Defense: parseNumber(cellValue(cells, 13)) ?? 0,
      Balanced: parseNumber(cellValue(cells, 14)) ?? 0,
      Special: parseNumber(cellValue(cells, 15)) ?? 0,
    },
    exp: parseNumber(cellValue(cells, 16)) ?? 0,
    hatchTimeSeconds: parseHatchTimeSeconds(remarks),
    remarks,
  };
}

function mergeFeedItem(current: EggFeedItem, next: EggFeedItem): EggFeedItem {
  return {
    ...current,
    copperCoins: current.copperCoins ?? next.copperCoins,
    exp: current.exp || next.exp,
    hatchTimeSeconds: current.hatchTimeSeconds ?? next.hatchTimeSeconds,
    remarks: current.remarks || next.remarks,
    stats: {
      Attack: current.stats.Attack || next.stats.Attack,
      Defense: current.stats.Defense || next.stats.Defense,
      Balanced: current.stats.Balanced || next.stats.Balanced,
      Special: current.stats.Special || next.stats.Special,
    },
  };
}

export function getReachedBand(total: number, thresholds: Record<Exclude<EggBand, "None">, number | null>): EggBand {
  if (thresholds.Over !== null && total >= thresholds.Over) return "Over";
  if (thresholds.High !== null && total >= thresholds.High) return "High";
  if (thresholds.Medium !== null && total >= thresholds.Medium) return "Medium";
  if (thresholds.Low !== null && total >= thresholds.Low) return "Low";
  return "None";
}

export function getNextThreshold(
  total: number,
  thresholds: Record<Exclude<EggBand, "None">, number | null>
): { band: Exclude<EggBand, "None">; remaining: number } | null {
  const order: Array<Exclude<EggBand, "None">> = ["Low", "Medium", "High", "Over"];
  for (const band of order) {
    const threshold = thresholds[band];
    if (threshold !== null && total < threshold) {
      return { band, remaining: threshold - total };
    }
  }
  return null;
}

export async function fetchEggReferenceData(): Promise<EggReferenceData> {
  const url = googleSheetUrl("eggs");
  const res = await fetch(url);
  const text = await res.text();
  const data = parseGoogleViz(text);

  const monsterMap = new Map<string, EggMonsterOutcome>();
  const feedMap = new Map<string, EggFeedItem>();

  for (const row of data.table?.rows ?? []) {
    const cells = row.c ?? [];

    const monster = toMonsterOutcome(cells);
    if (monster && !monsterMap.has(monster.monsterName)) {
      monsterMap.set(monster.monsterName, monster);
    }

    const feedItem = toFeedItem(cells);
    if (feedItem) {
      const existing = feedMap.get(feedItem.name);
      feedMap.set(feedItem.name, existing ? mergeFeedItem(existing, feedItem) : feedItem);
    }
  }

  const monsters = Array.from(monsterMap.values()).sort((a, b) =>
    a.eggColor.localeCompare(b.eggColor) || a.monsterName.localeCompare(b.monsterName)
  );
  const feedItems = Array.from(feedMap.values()).sort((a, b) => a.name.localeCompare(b.name));
  const eggColors = Array.from(new Set(monsters.map((monster) => monster.eggColor))).filter(Boolean);

  return { monsters, feedItems, eggColors };
}
