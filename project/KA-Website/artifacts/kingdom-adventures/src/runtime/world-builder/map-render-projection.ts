import type { ParsedMapBinary, TerrainKind } from "./types";

export interface MapRenderTile {
  x: number;
  y: number;
  terrainKind: TerrainKind;
  chipId: number;
  color: string;
}

export interface MapRenderProjectionStats {
  width: number;
  height: number;
  totalCells: number;
  terrainCounts: Record<TerrainKind, number>;
  remainingBytes: number;
  unknownTerrainFlags: number;
}

export interface MapRenderProjection {
  tiles: MapRenderTile[];
  stats: MapRenderProjectionStats;
}

export function buildMapRenderProjection(parsed: ParsedMapBinary): MapRenderProjection {
  const tiles: MapRenderTile[] = [];
  const terrainCounts: Record<TerrainKind, number> = { soil: 0, water: 0 };
  let unknownTerrainFlags = 0;

  for (const cell of parsed.cells) {
    const terrain = resolveTerrainKind(cell.fields.f0);
    if (cell.fields.f0 !== 0 && cell.fields.f0 !== 1) {
      unknownTerrainFlags += 1;
    }

    terrainCounts[terrain] += 1;

    const chipId = cell.fields.f2;
    const color = resolvePlaceholderColor(terrain, chipId);

    tiles.push({
      x: cell.x,
      y: cell.y,
      terrainKind: terrain,
      chipId,
      color,
    });
  }

  return {
    tiles,
    stats: {
      width: parsed.width,
      height: parsed.height,
      totalCells: parsed.cells.length,
      terrainCounts,
      remainingBytes: parsed.remainingBytes,
      unknownTerrainFlags,
    },
  };
}

function resolveTerrainKind(flag: number): TerrainKind {
  return flag === 1 ? "water" : "soil";
}

function resolvePlaceholderColor(terrain: TerrainKind, chipId: number): string {
  if (chipId > 0) {
    if (chipId === 58 || chipId === 59 || chipId === 60 || chipId === 61) {
      return "#f97316";
    }
    return "#c084fc";
  }

  return terrain === "water" ? "#0ea5e9" : "#1f2937";
}
