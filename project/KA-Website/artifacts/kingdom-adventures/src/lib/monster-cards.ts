/**
 * One row per Monster.csv entry, shaped for the combined spawns / loot / XP card page.
 *
 * Sources and ownership:
 *  - `KA GameData - Monster.csv` (terrain, levels, type, drop fields, exp stats)
 *  - `src/game-data/native-treasure.json` via `treasure-lookup` (treasure box and contents)
 *  - `src/game-data/monster-sprites.json` (original map body pose, keyed by monster id)
 *  - `cave-lookup` (the cave that spawns a terrain, from the original MapChip/Terrain rows)
 *
 * Recovered spawn rule (see RE-evidence/20260917-map-monster-spawn/FINDINGS.md):
 * `EnemyBaseSystem.ChooseMonsterData 0x015666cc` keeps a MonsterData row when its `terrain`
 * matches the terrain of the map chip on the cell (or its `group` matches that terrain's
 * monsterGroupId) and its `areaLevelMin..areaLevelMax` contains the level of the area it sits
 * in. Caves are part of the same generator list, and the enemy-base path only ever requests
 * type 0, so farmable animals (type 1) never come from a cave and have no battle XP.
 */
import { getMonsterSpawnDataById, MONSTER_CSV_ROWS } from "@/game-data/monster-spawn-data";
import { ALL_AREA_LEVELS } from "@/lib/monster-xp";
import { TREASURE_BY_ID, treasureDisplayName } from "@/lib/treasure-lookup";
import { caveForTerrain, type CaveAppearance } from "@/lib/cave-lookup";
import { getItemIcon } from "@/lib/equipment-icons";
import { localSharedData } from "@/lib/local-shared-data";

export const TYPE_MONSTER = 0;
export const TYPE_ANIMAL = 1;

/** Terrain chip classes used for the biome chip colour, matching the world map palette. */
const TERRAIN_CLASSES: Record<number, string> = {
  1: "soil",
  2: "grass",
  3: "sand",
  4: "rock",
  5: "volcano",
  6: "snow",
  7: "swamp",
  15: "soil",
};

export const EXP_STAT_COLUMNS = [
  "HP",
  "MP",
  "Vigor",
  "Atk",
  "Def",
  "Spd",
  "Luck",
  "Owned??",
  "Int",
  "Dex",
  "Gather",
  "Move",
  "Heart",
] as const;

export type ExpStat = (typeof EXP_STAT_COLUMNS)[number];

const STAT_LABELS: Record<ExpStat, string> = {
  HP: "HP",
  MP: "MP",
  Vigor: "Vigor",
  Atk: "Atk",
  Def: "Def",
  Spd: "Spd",
  Luck: "Luck",
  "Owned??": "Owned",
  Int: "Int",
  Dex: "Dex",
  Gather: "Gather",
  Move: "Move",
  Heart: "Heart",
};

const STAT_ICON_KEYS: Partial<Record<ExpStat, string>> = {
  HP: "HP",
  MP: "MP",
  Vigor: "Vigor",
  Atk: "Attack",
  Def: "Defence",
  Spd: "Speed",
  Luck: "Luck",
  Int: "Intelligence",
  Dex: "Dexterity",
  Gather: "Gather",
  Move: "Move",
  Heart: "Heart",
};

const STAT_ICONS = (localSharedData as { statIcons?: Record<string, string> }).statIcons ?? {};

export type MonsterCardReward = {
  name: string;
  icon?: string;
  rate: number;
  min: number;
  max: number;
};

export type MonsterCardBox = {
  id: number;
  name: string;
  icon: string;
  rate: number;
  /** Monster.csv dropDataType 1 (material, farmable animals) versus 2 (treasure box). */
  material: boolean;
  rewards: MonsterCardReward[];
};

export type MonsterCard = {
  id: number;
  name: string;
  type: number;
  terrainCode: number;
  terrainName: string;
  terrainClass: string;
  minLevel: number;
  maxLevel: number;
  silverPrice: number;
  sprite?: string;
  cave?: CaveAppearance;
  box?: MonsterCardBox;
  stats: Record<ExpStat, number>;
  averageMultiplier: number;
  searchText: string;
};

const rows = MONSTER_CSV_ROWS;
const header = rows[2] ?? [];
const index = (name: string) => header.indexOf(name);
const column = {
  id: index("id"),
  silverPrice: index("silverPrice"),
  dropDataType: index("dropDataType"),
  dropDataId: index("dropDataId"),
  dropRate: index("dropRate"),
  stats: EXP_STAT_COLUMNS.map((stat) => index(stat)),
};

function buildCard(row: string[]): MonsterCard | null {
  const monster = getMonsterSpawnDataById(Number(row[column.id]));
  if (!monster) return null;
  const { id, name, type, terrainCode, terrainName, minLevel, maxLevel, sprite } = monster;
  const stats = Object.fromEntries(
    EXP_STAT_COLUMNS.map((stat, position) => {
      const value = Number(row[column.stats[position]]);
      return [stat, Number.isFinite(value) && value > 0 ? value : 0];
    }),
  ) as Record<ExpStat, number>;
  const nonZeroStats = Object.values(stats).filter((value) => value > 0);
  const boxId = Number(row[column.dropDataId]);
  const treasure = TREASURE_BY_ID.get(boxId);
  const box: MonsterCardBox | undefined = treasure
    ? {
        id: treasure.id,
        name: treasureDisplayName(treasure.name),
        icon: treasure.icon,
        rate: Number(row[column.dropRate]) || 0,
        material: Number(row[column.dropDataType]) === 1,
        rewards: treasure.rewards.map((reward) => ({
          name: treasureDisplayName(reward.name),
          icon: getItemIcon(reward.name),
          rate: reward.rate,
          min: reward.min,
          max: reward.max,
        })),
      }
    : undefined;
  const cave = type === TYPE_MONSTER ? caveForTerrain(terrainCode) : undefined;
  return {
    id,
    name,
    type,
    terrainCode,
    terrainName,
    terrainClass: TERRAIN_CLASSES[terrainCode] ?? "soil",
    minLevel,
    maxLevel,
    silverPrice: Number(row[column.silverPrice]) || 0,
    sprite,
    cave,
    box,
    stats,
    averageMultiplier: nonZeroStats.length ? nonZeroStats.reduce((sum, value) => sum + value, 0) / nonZeroStats.length : 0,
    searchText: [
      name,
      terrainName,
      cave?.name ?? "",
      box?.name ?? "",
      ...(box?.rewards.map((reward) => reward.name) ?? []),
    ]
      .join(" ")
      .toLowerCase(),
  };
}

const ALL_CARDS = rows.slice(3).map(buildCard).filter((card): card is MonsterCard => card !== null);

/** Combat monsters, in CSV order. */
export const COMBAT_MONSTER_CARDS = ALL_CARDS.filter((card) => card.type === TYPE_MONSTER);

/** Farmable animals (type 1). They are not fought, so the card shows a yield instead of a drop. */
export const ANIMAL_MONSTER_CARDS = ALL_CARDS.filter((card) => card.type === TYPE_ANIMAL);

export function filterMonsterCards(cards: MonsterCard[], query: string): MonsterCard[] {
  const trimmed = query.trim().toLowerCase();
  if (!trimmed) return cards;
  return cards.filter((card) => card.searchText.includes(trimmed));
}

/** Battle XP for one monster at an area level: 30 × exp multiplier × level ÷ 100. */
export function xpPerKill(card: MonsterCard, level: number): number {
  return (30 * card.averageMultiplier * level) / 100;
}

export type StatXpEntry = { stat: ExpStat; label: string; icon?: string; value: number };

/** Average XP per stat: one non-zero exp stat is granted per kill, so this is its share. */
export function statXp(card: MonsterCard, level: number): StatXpEntry[] {
  const nonZeroCount = Object.values(card.stats).filter((value) => value > 0).length;
  if (!nonZeroCount) return [];
  return EXP_STAT_COLUMNS.filter((stat) => card.stats[stat] > 0).map((stat) => ({
    stat,
    label: STAT_LABELS[stat],
    icon: STAT_ICONS[STAT_ICON_KEYS[stat] ?? ""],
    value: (30 * card.stats[stat] * level) / 100 / nonZeroCount,
  }));
}

export const MAX_AREA_LEVEL = 9999;

/**
 * Area levels the game can put this monster at, for the XP level box.
 *
 * Spawns are not biome-locked: the random spawn mechanic can place a monster in an area whose
 * biome differs from its own row, so any area level at or above its lowest spawn level is a real
 * possibility (a rock monster at level 3200, for example). Suggestions are the map's area levels.
 */
export function selectableAreaLevels(minLevel: number): number[] {
  return ALL_AREA_LEVELS.filter((level) => level >= minLevel && level <= MAX_AREA_LEVEL);
}

/** Card to show when a monster is focused, with the level list used by the XP panel. */
export function monsterByName(name: string): MonsterCard | undefined {
  const target = name.trim().toLowerCase();
  return ALL_CARDS.find((card) => card.name.toLowerCase() === target);
}
