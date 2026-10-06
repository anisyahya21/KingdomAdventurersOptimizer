import monsterCsv from "../../../../data/Sheet csv/KA GameData - Monster.csv?raw";
import { parseCsv } from "@/lib/csv";
import { getMonsterSpriteById } from "@/lib/monster-sprites";

export type MonsterSpawnData = {
  id: number;
  name: string;
  type: number;
  terrainCode: number;
  terrainName: string;
  minLevel: number;
  maxLevel: number;
  sprite?: string;
};

const TERRAIN_NAMES: Record<number, string> = {
  0: "Water",
  1: "Ground",
  2: "Grass",
  3: "Sand",
  4: "Rock",
  5: "Volcano",
  6: "Snow",
  7: "Swamp",
  15: "Ground",
  [-1]: "Special",
};

export const MONSTER_CSV_ROWS = parseCsv(monsterCsv);
const header = MONSTER_CSV_ROWS[2] ?? [];
const index = (name: string) => header.indexOf(name);
const column = {
  id: index("id"),
  name: index("name"),
  type: index("type"),
  terrain: index("terrain"),
  minLevel: index("areaLevelMin"),
  maxLevel: index("areaLevelMax"),
};

export const MONSTER_SPAWN_DATA: MonsterSpawnData[] = MONSTER_CSV_ROWS.slice(3)
  .flatMap((row) => {
    const id = Number(row[column.id]);
    const name = row[column.name]?.trim();
    const type = Number(row[column.type]);
    const terrainCode = Number(row[column.terrain]);
    const terrainName = TERRAIN_NAMES[terrainCode];
    const minLevel = Number(row[column.minLevel]);
    const maxLevel = Number(row[column.maxLevel]);
    if (!Number.isInteger(id) || !name || !terrainName || !Number.isFinite(minLevel) || !Number.isFinite(maxLevel)) return [];

    return [{ id, name, type, terrainCode, terrainName, minLevel, maxLevel, sprite: getMonsterSpriteById(id) }];
  });

const MONSTER_BY_ID = new Map(MONSTER_SPAWN_DATA.map((monster) => [monster.id, monster] as const));
const MONSTER_BY_NAME = new Map(MONSTER_SPAWN_DATA.map((monster) => [monster.name.toLowerCase(), monster] as const));

export function getMonsterSpawnDataById(id: number) {
  return MONSTER_BY_ID.get(id);
}

export function getMonsterSpawnDataByName(name: string | undefined) {
  return name ? MONSTER_BY_NAME.get(name.trim().toLowerCase()) : undefined;
}
