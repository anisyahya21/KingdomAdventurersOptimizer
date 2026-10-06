import monsterCsv from "../../../../data/Sheet csv/KA GameData - Monster.csv?raw";
import fullTerrainCsv from "../data/full-terrain-map.csv?raw";
import { parseCsv } from "@/lib/csv";

export { parseCsv };

export type MonsterSpawn = { area: string; level: number };

export type MinedMonsterSummary = {
  id: number;
  name: string;
  terrainCode: number;
  terrainName: string;
  areaLevelMin: number;
  areaLevelMax: number;
  nativeMapSpawns: MonsterSpawn[];
  spawnNote?: string;
};

export type CommunitySighting = {
  area: string;
  level: number;
};

export type TerrainType = "grass" | "sand" | "volcano" | "swamp" | "rock" | "snow" | "ground";

const TERRAIN_CODE_TO_TERRAIN_TYPE: Record<number, TerrainType> = {
  1: "ground",
  15: "ground",
  2: "grass",
  13: "grass",
  3: "sand",
  9: "sand",
  4: "rock",
  11: "rock",
  5: "volcano",
  10: "volcano",
  6: "snow",
  8: "snow",
  7: "swamp",
  12: "swamp",
};

export function mapTerrainCodeToType(code: number): TerrainType | undefined {
  return TERRAIN_CODE_TO_TERRAIN_TYPE[code];
}

export type NativeCell = {
  terrain: TerrainType;
  level: number;
};

export const NATIVE_MAP: NativeCell[][] = [
  [{ terrain: "volcano", level: 9999 }, { terrain: "volcano", level: 4800 }, { terrain: "snow", level: 6000 }, { terrain: "snow", level: 2400 }, { terrain: "swamp", level: 5100 }, { terrain: "swamp", level: 1100 }, { terrain: "swamp", level: 888 }, { terrain: "swamp", level: 1020 }, { terrain: "swamp", level: 1600 }, { terrain: "swamp", level: 3200 }],
  [{ terrain: "volcano", level: 525 }, { terrain: "volcano", level: 250 }, { terrain: "snow", level: 208 }, { terrain: "snow", level: 320 }, { terrain: "swamp", level: 225 }, { terrain: "swamp", level: 120 }, { terrain: "swamp", level: 112 }, { terrain: "swamp", level: 69 }, { terrain: "swamp", level: 85 }, { terrain: "swamp", level: 162 }],
  [{ terrain: "snow", level: 624 }, { terrain: "snow", level: 135 }, { terrain: "snow", level: 75 }, { terrain: "snow", level: 55 }, { terrain: "rock", level: 56 }, { terrain: "rock", level: 82 }, { terrain: "rock", level: 51 }, { terrain: "rock", level: 29 }, { terrain: "rock", level: 52 }, { terrain: "rock", level: 44 }],
  [{ terrain: "snow", level: 360 }, { terrain: "snow", level: 90 }, { terrain: "snow", level: 46 }, { terrain: "snow", level: 70 }, { terrain: "rock", level: 72 }, { terrain: "rock", level: 61 }, { terrain: "rock", level: 34 }, { terrain: "rock", level: 27 }, { terrain: "rock", level: 32 }, { terrain: "rock", level: 40 }],
  [{ terrain: "snow", level: 592 }, { terrain: "snow", level: 142 }, { terrain: "snow", level: 43 }, { terrain: "snow", level: 65 }, { terrain: "sand", level: 60 }, { terrain: "sand", level: 45 }, { terrain: "rock", level: 22 }, { terrain: "rock", level: 25 }, { terrain: "rock", level: 19 }, { terrain: "rock", level: 28 }],
  [{ terrain: "sand", level: 311 }, { terrain: "sand", level: 88 }, { terrain: "sand", level: 41 }, { terrain: "sand", level: 58 }, { terrain: "sand", level: 54 }, { terrain: "sand", level: 20 }, { terrain: "rock", level: 16 }, { terrain: "rock", level: 14 }, { terrain: "rock", level: 12 }, { terrain: "rock", level: 10 }],
  [{ terrain: "sand", level: 121 }, { terrain: "sand", level: 92 }, { terrain: "sand", level: 37 }, { terrain: "sand", level: 35 }, { terrain: "sand", level: 30 }, { terrain: "sand", level: 8 }, { terrain: "sand", level: 10 }, { terrain: "sand", level: 2 }, { terrain: "grass", level: 3 }, { terrain: "grass", level: 7 }],
  [{ terrain: "sand", level: 265 }, { terrain: "sand", level: 76 }, { terrain: "sand", level: 31 }, { terrain: "sand", level: 26 }, { terrain: "sand", level: 23 }, { terrain: "sand", level: 13 }, { terrain: "sand", level: 1 }, { terrain: "sand", level: 1 }, { terrain: "grass", level: 2 }, { terrain: "grass", level: 2 }],
  [{ terrain: "grass", level: 454 }, { terrain: "grass", level: 62 }, { terrain: "grass", level: 33 }, { terrain: "grass", level: 24 }, { terrain: "grass", level: 18 }, { terrain: "grass", level: 11 }, { terrain: "grass", level: 2 }, { terrain: "grass", level: 2 }, { terrain: "ground", level: 1 }, { terrain: "ground", level: 1 }],
  [{ terrain: "grass", level: 777 }, { terrain: "grass", level: 74 }, { terrain: "grass", level: 50 }, { terrain: "grass", level: 48 }, { terrain: "grass", level: 21 }, { terrain: "grass", level: 15 }, { terrain: "grass", level: 5 }, { terrain: "grass", level: 2 }, { terrain: "ground", level: 1 }, { terrain: "ground", level: 1 }]
];

const TERRAIN_TYPE_TO_AREA_NAME: Record<TerrainType, string> = {
  grass: "Grass",
  sand: "Sand",
  volcano: "Volcano",
  swamp: "Swamp",
  rock: "Rock",
  snow: "Snow",
  ground: "Ground",
};

function getNativeIndex(index: number, cellCount: number, nativeCount: number) {
  return Math.min(nativeCount - 1, Math.floor((index * nativeCount) / cellCount));
}

function buildNativeAreaLevelsFromTerrainMap(): Record<string, number[]> {
  const fullTerrainMap = parseTerrainMapCsv(fullTerrainCsv);
  const nativeRows = NATIVE_MAP.length;
  const nativeCols = NATIVE_MAP[0]?.length ?? 0;
  const levelSets = new Map<string, Set<number>>();

  for (let y = 0; y < fullTerrainMap.length; y += 1) {
    const row = fullTerrainMap[y];
    for (let x = 0; x < row.length; x += 1) {
      const terrain = mapTerrainCodeToType(row[x]);
      if (!terrain) continue;
      const nativeY = getNativeIndex(y, fullTerrainMap.length, nativeRows);
      const nativeX = getNativeIndex(x, row.length, nativeCols);
      const native = NATIVE_MAP[nativeY]?.[nativeX];
      if (!native) continue;
      const areaName = TERRAIN_TYPE_TO_AREA_NAME[terrain];
      if (!levelSets.has(areaName)) levelSets.set(areaName, new Set<number>());
      levelSets.get(areaName)!.add(native.level);
    }
  }

  return Object.fromEntries(
    [...levelSets.entries()].map(([areaName, levels]) => [
      areaName,
      [...levels].sort((left, right) => left - right),
    ]),
  );
}

export const NATIVE_AREA_LEVELS: Record<string, number[]> = {
  Grass: [2, 3, 5, 7, 11, 15, 18, 21, 24, 33, 48, 50, 62, 74, 454, 777],
  Ground: [1],
  Sand: [1, 2, 8, 10, 13, 20, 23, 26, 30, 31, 35, 37, 41, 45, 54, 58, 60, 76, 88, 92, 121, 265, 311],
  Rock: [10, 12, 14, 16, 19, 22, 25, 27, 28, 29, 32, 34, 40, 44, 51, 52, 56, 61, 72, 82],
  Snow: [43, 46, 55, 65, 70, 75, 90, 135, 142, 208, 320, 360, 592, 624, 2400, 6000],
  Swamp: [69, 85, 112, 120, 162, 225, 888, 1020, 1100, 1600, 3200, 5100],
  Volcano: [250, 525, 4800, 9999],
  ...buildNativeAreaLevelsFromTerrainMap(),
};

export const TERRAIN_CODE_TO_NAME: Record<number, string> = {
  0: "Water",
  1: "Ground",
  2: "Grass",
  3: "Sand",
  4: "Rock",
  5: "Volcano",
  6: "Snow",
  7: "Swamp",
  [-1]: "Special",
};

const COMMUNITY_SIGHTINGS_KEY = "ka_monster_community_sightings";

export function parseTerrainMapCsv(text: string, headerRows = 0): number[][] {
  return parseCsv(text)
    .slice(headerRows)
    .map((row) => row.map((cell) => Number(cell)));
}

function buildMinedMonsters(rawCsv: string): MinedMonsterSummary[] {
  const rows = parseCsv(rawCsv);
  if (rows.length < 4) return [];

  const header = rows[2];
  const terrainIndex = header.indexOf("terrain");
  const minIndex = header.indexOf("areaLevelMin");
  const maxIndex = header.indexOf("areaLevelMax");

  return rows.slice(3)
    .filter((row) => row[0] && row[1])
    .map((row) => {
      const id = Number(row[0] ?? "");
      const name = row[1] ?? `Monster ${row[0] ?? ""}`;
      const terrainCode = Number(row[terrainIndex] ?? "");
      const areaLevelMin = Number(row[minIndex] ?? "");
      const areaLevelMax = Number(row[maxIndex] ?? "");
      const terrainName = TERRAIN_CODE_TO_NAME[terrainCode] || "Special";
      const nativeMapSpawns =
        (NATIVE_AREA_LEVELS[terrainName] ?? [])
          .filter((level) => level >= areaLevelMin && level <= areaLevelMax)
          .map((level) => ({ area: terrainName, level }));

      let spawnNote: string | undefined;
      if (terrainCode === 0) {
        spawnNote = "Water terrain in mined Monster.csv. Exact water-level mapping still needs decoding.";
      } else if (terrainCode === -1) {
        spawnNote = "Special/non-standard terrain case in mined Monster.csv.";
      }

      return {
        id: Number.isFinite(id) ? id : 0,
        name,
        terrainCode,
        terrainName,
        areaLevelMin: Number.isFinite(areaLevelMin) ? areaLevelMin : 0,
        areaLevelMax: Number.isFinite(areaLevelMax) ? areaLevelMax : 0,
        nativeMapSpawns,
        spawnNote,
      };
    })
    .sort((left, right) => left.name.localeCompare(right.name));
}

export const MINED_MONSTER_SUMMARIES = buildMinedMonsters(monsterCsv);

export const MINED_MONSTER_SUMMARY_MAP = Object.fromEntries(
  MINED_MONSTER_SUMMARIES.map((monster) => [monster.name, monster]),
) as Record<string, MinedMonsterSummary>;

export function readCommunitySightings(): Record<string, CommunitySighting[]> {
  if (typeof window === "undefined") return {};
  try {
    const raw = window.localStorage.getItem(COMMUNITY_SIGHTINGS_KEY);
    const parsed = raw ? JSON.parse(raw) : {};
    if (!parsed || typeof parsed !== "object") return {};
    return Object.fromEntries(
      Object.entries(parsed).map(([name, value]) => [
        name,
        Array.isArray(value)
          ? value
              .map((entry) => {
                if (!entry || typeof entry !== "object") return null;
                const area = String((entry as { area?: unknown }).area ?? "").trim();
                const level = Number((entry as { level?: unknown }).level ?? NaN);
                if (!area || !Number.isFinite(level)) return null;
                return { area, level };
              })
              .filter((entry): entry is CommunitySighting => Boolean(entry))
          : [],
      ]),
    );
  } catch {
    return {};
  }
}

export function writeCommunitySightings(value: Record<string, CommunitySighting[]>) {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(COMMUNITY_SIGHTINGS_KEY, JSON.stringify(value));
}

export function mergeUniqueSpawns(...groups: Array<MonsterSpawn[] | undefined>): MonsterSpawn[] {
  const seen = new Set<string>();
  const merged: MonsterSpawn[] = [];
  for (const group of groups) {
    for (const spawn of group ?? []) {
      const key = `${spawn.area.trim().toLowerCase()}|${spawn.level}`;
      if (seen.has(key)) continue;
      seen.add(key);
      merged.push(spawn);
    }
  }
  return merged.sort((left, right) => {
    const areaCmp = left.area.localeCompare(right.area);
    if (areaCmp !== 0) return areaCmp;
    return left.level - right.level;
  });
}
