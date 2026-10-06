import monsterCsv from "../../../../data/Sheet csv/KA GameData - Monster.csv?raw";
import { NATIVE_AREA_LEVELS, parseCsv } from "./monster-truth";

export const XP_STAT_COLUMNS = ["HP", "MP", "Vigor", "Atk", "Def", "Spd", "Luck", "Owned??", "Int", "Dex", "Gather", "Move", "Heart"] as const;
export const XP_UP_BONUSES = { 1: 1.25, 2: 1.5, 3: 1.75 } as const;
type XpStat = (typeof XP_STAT_COLUMNS)[number];

export type XpTerrain = "Ground/dirt" | "Grass" | "Sand" | "Rock" | "Snow" | "Swamp" | "Volcano";

export type XpMonster = {
  id: number;
  name: string;
  terrain: XpTerrain;
  minLevel: number;
  maxLevel: number;
  stats: Record<XpStat, number>;
  averageMultiplier: number;
};

export type XpResult = {
  terrain: XpTerrain;
  level: number;
  monsters: XpMonster[];
  averageMultiplier: number;
  averageXp: number;
  minXp: number;
  maxXp: number;
  bonusMultiplier: number;
  statXp: Record<XpStat, number>;
};

const rows = parseCsv(monsterCsv);
const header = rows[2] ?? [];
const dataRows = rows.slice(3);
const index = (name: string) => header.indexOf(name);
const terrainIndex = index("terrain");
const minIndex = index("areaLevelMin");
const maxIndex = index("areaLevelMax");
const nameIndex = index("name");
const idIndex = index("id");
const typeIndex = index("type");
const statIndexes = XP_STAT_COLUMNS.map(index);

const terrainByCode: Record<number, XpTerrain> = {
  1: "Ground/dirt",
  2: "Grass",
  3: "Sand",
  4: "Rock",
  5: "Volcano",
  6: "Snow",
  7: "Swamp",
};

function parseMonster(row: string[]): XpMonster | null {
  const name = row[nameIndex]?.trim();
  const terrain = terrainByCode[Number(row[terrainIndex])];
  // Monster type 1 entries are farmable animals/pets, not combat monsters.
  if (!name || !terrain || Number(row[typeIndex]) === 1) return null;

  const stats = Object.fromEntries(XP_STAT_COLUMNS.map((stat, position) => {
    const value = Number(row[statIndexes[position]]);
    return [stat, Number.isFinite(value) && value > 0 ? value : 0];
  })) as Record<XpStat, number>;
  const nonZeroStats = Object.values(stats).filter((value) => value > 0);
  if (nonZeroStats.length === 0) return null;

  return {
    id: Number(row[idIndex]),
    name,
    terrain,
    minLevel: Number(row[minIndex]),
    maxLevel: Number(row[maxIndex]),
    stats,
    averageMultiplier: nonZeroStats.reduce((sum, value) => sum + value, 0) / nonZeroStats.length,
  };
}

export const COMBAT_MONSTERS = dataRows.map(parseMonster).filter((monster): monster is XpMonster => Boolean(monster));

export const XP_TERRAINS: XpTerrain[] = ["Ground/dirt", "Grass", "Sand", "Rock", "Snow", "Swamp", "Volcano"];

export function getEligibleMonsters(terrain: XpTerrain, level: number) {
  return COMBAT_MONSTERS.filter((monster) => monster.terrain === terrain && monster.minLevel <= level && monster.maxLevel >= level);
}

export function getXpResult(terrain: XpTerrain, level: number, enabledXpUps: number[] = []): XpResult {
  const monsters = getEligibleMonsters(terrain, level);
  const bonusMultiplier = enabledXpUps.reduce((product, skill) => product * XP_UP_BONUSES[skill as 1 | 2 | 3], 1);
  const averageMultiplier = monsters.length === 0 ? 0 : monsters.reduce((sum, monster) => sum + monster.averageMultiplier, 0) / monsters.length;
  const xpFor = (monster: XpMonster) => 30 * monster.averageMultiplier * level / 100 * bonusMultiplier;
  const values = monsters.map(xpFor);
  const statXp = Object.fromEntries(XP_STAT_COLUMNS.map((stat) => [
    stat,
    monsters.length === 0 ? 0 : monsters.reduce((sum, monster) => {
      const nonZeroCount = Object.values(monster.stats).filter((value) => value > 0).length;
      return sum + 30 * monster.stats[stat] * level / 100 / nonZeroCount * bonusMultiplier;
    }, 0) / monsters.length,
  ])) as Record<XpStat, number>;

  return {
    terrain,
    level,
    monsters,
    averageMultiplier,
    averageXp: values.length === 0 ? 0 : values.reduce((sum, value) => sum + value, 0) / values.length,
    minXp: values.length === 0 ? 0 : Math.min(...values),
    maxXp: values.length === 0 ? 0 : Math.max(...values),
    bonusMultiplier,
    statXp,
  };
}

export const ALL_AREA_LEVELS = Array.from(new Set(Object.values(NATIVE_AREA_LEVELS).flat())).sort((a, b) => a - b);

export function nativeTerrainAtLevel(level: number) {
  return XP_TERRAINS.filter((terrain) => terrain === "Ground/dirt" || (NATIVE_AREA_LEVELS[terrain] ?? []).includes(level));
}
