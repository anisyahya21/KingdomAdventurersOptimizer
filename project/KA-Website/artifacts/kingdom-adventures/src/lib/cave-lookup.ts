/**
 * Normal cave appearances and the terrain each one belongs to.
 *
 * Data owner: `src/game-data/cave-appearances.json`, derived from the original MapChip rows
 * 71-78 (category 34) and Terrain rows 95-102. `MapChipData.get_terrainData 0x0162d3fc`
 * returns `Data.terrainData[relatedDataId]` when `relatedDataType == 2`, and those terrain rows
 * carry the biome in their `type` field. `EnemyBaseSystem.ChooseMonsterData 0x015666cc` feeds
 * that terrain into the monster filter, so a cave spawns the monsters whose `terrain` matches
 * the cave terrain and whose `areaLevelMin..areaLevelMax` contains the area level.
 *
 * Evidence: RE-evidence/20260917-map-monster-spawn/FINDINGS.md
 */
import data from "@/game-data/cave-appearances.json";

export type CaveAppearance = {
  terrainType: number;
  terrainRow: number;
  chipId: number;
  name: string;
  icon: string;
};

export const CAVE_APPEARANCES: CaveAppearance[] = data.caves;

const BY_TERRAIN = new Map(CAVE_APPEARANCES.map((cave) => [cave.terrainType, cave]));

/** Cave that spawns monsters of this terrain type, or undefined when the terrain has no cave. */
export function caveForTerrain(terrainType: number | undefined): CaveAppearance | undefined {
  return terrainType === undefined ? undefined : BY_TERRAIN.get(terrainType);
}
