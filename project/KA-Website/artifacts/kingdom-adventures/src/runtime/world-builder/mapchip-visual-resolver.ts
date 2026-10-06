import type { ParsedMapBinary } from "./types";

export interface MapChipCsvRow {
  id: number;
  name: string;
  img: number;
  seb: number;
  layer: number;
  rotation: number;
  sizeWidth: number;
  sizeHeight: number;
}

export interface MapChipAssetCandidate {
  assetId: string;
  sourcePng: string;
  spriteName: string;
  rect?: { x: number; y: number; w: number; h: number };
  pivot?: unknown;
  layer?: string;
  rawRes?: unknown;
}

export interface MapChipVisualResolution {
  mapChipId: number;
  mapChipName: string;
  drawLayer: number;
  sizeWidth: number;
  sizeHeight: number;
  rotation: number;
  img: number;
  seb: number;
  imageFamily?: string;
  imageFilename?: string;
  spriteSourcePng?: string;
  spriteAssetId?: string;
  frameRect?: { x: number; y: number; w: number; h: number };
  anchor?: unknown;
  sourceMetadata: {
    imgInfRead: boolean;
    sebInfRead: boolean;
    sebFilePath?: string;
    sebFileRead: boolean;
    optFilePath?: string;
    optFileRead: boolean;
    optInfoFilePath?: string;
    optInfoFileRead: boolean;
  };
  resolved: boolean;
  unresolvedReason?: string;
}

export interface MapChipVisualSampleTile {
  x: number;
  y: number;
  f2: number;
  mapChipName: string;
  resolved: boolean;
  spriteSourcePng?: string;
  debugFallbackColor?: string;
  drawLayer: number;
  sizeWidth: number;
  sizeHeight: number;
  rotation: number;
}

export interface MapChipVisualResolverReport {
  observedF2Count: number;
  resolvedMapChipVisualCount: number;
  unresolvedMapChipIds: number[];
  resolutions: MapChipVisualResolution[];
  sampleRegion: {
    x: number;
    y: number;
    width: number;
    height: number;
    tileCount: number;
    resolvedTileCount: number;
    unresolvedTileCount: number;
    tiles: MapChipVisualSampleTile[];
  };
}

export interface MapChipVisualResolverBuildInput {
  mapChipCsvText: string;
  observedF2Ids: number[];
  mapAssetsByMapchipId: Record<string, MapChipAssetCandidate[]>;
  spriteRootRelativePath: string;
  existingRelativeFilePaths: Set<string>;
  parsedMap: ParsedMapBinary;
  sampleRegion: { x: number; y: number; width: number; height: number };
  infPaths: {
    imgInfRelativePath: string;
    sebInfRelativePath: string;
  };
  lookupRoots?: {
    sebRootRelativePath?: string;
    optRootRelativePath?: string;
  };
}

function parseCsvLine(line: string): string[] {
  const result: string[] = [];
  let current = "";
  let inQuotes = false;

  for (let i = 0; i < line.length; i++) {
    const ch = line[i];

    if (ch === '"') {
      const next = i + 1 < line.length ? line[i + 1] : "";
      if (inQuotes && next === '"') {
        current += '"';
        i += 1;
      } else {
        inQuotes = !inQuotes;
      }
      continue;
    }

    if (ch === "," && !inQuotes) {
      result.push(current);
      current = "";
      continue;
    }

    current += ch;
  }

  result.push(current);
  return result;
}

function parseMapChipCsv(csvText: string): Map<number, MapChipCsvRow> {
  const lines = csvText
    .split(/\r?\n/g)
    .map((line) => line.trim())
    .filter((line) => line.length > 0);

  if (lines.length < 2) {
    throw new Error("MapChip CSV appears empty or malformed");
  }

  const headers = parseCsvLine(lines[0]);
  const byName = new Map<string, number>();
  for (let i = 0; i < headers.length; i++) {
    byName.set(headers[i], i);
  }

  function getNumber(cells: string[], header: string): number {
    const index = byName.get(header);
    if (index === undefined || index >= cells.length) {
      return 0;
    }
    const value = Number(cells[index]);
    return Number.isFinite(value) ? value : 0;
  }

  function getText(cells: string[], header: string): string {
    const index = byName.get(header);
    if (index === undefined || index >= cells.length) {
      return "";
    }
    return cells[index];
  }

  const map = new Map<number, MapChipCsvRow>();
  for (let i = 1; i < lines.length; i++) {
    const cells = parseCsvLine(lines[i]);
    const id = getNumber(cells, "id");
    map.set(id, {
      id,
      name: getText(cells, "name"),
      img: getNumber(cells, "img"),
      seb: getNumber(cells, "seb"),
      layer: getNumber(cells, "layer"),
      rotation: getNumber(cells, "rotation"),
      sizeWidth: getNumber(cells, "sizeWidth"),
      sizeHeight: getNumber(cells, "sizeHeight"),
    });
  }

  return map;
}

function pickPrimaryCandidate(candidates: MapChipAssetCandidate[] | undefined): MapChipAssetCandidate | undefined {
  if (!candidates || candidates.length === 0) {
    return undefined;
  }

  return candidates[0];
}

function deriveSpritePngPath(
  assetId: string,
  spriteRootRelativePath: string,
  existingRelativeFilePaths: Set<string>,
): string | undefined {
  const relative = `${spriteRootRelativePath}/${assetId}.png`.replace(/\\/g, "/");
  return existingRelativeFilePaths.has(relative) ? relative : undefined;
}

function fallbackColorForId(id: number): string {
  const hash = ((id * 2654435761) >>> 0) & 0xffffff;
  return `#${hash.toString(16).padStart(6, "0")}`;
}

function collectSampleRegionTiles(
  parsedMap: ParsedMapBinary,
  region: { x: number; y: number; width: number; height: number },
  byId: Map<number, MapChipVisualResolution>,
): MapChipVisualSampleTile[] {
  const tiles: MapChipVisualSampleTile[] = [];
  const startX = Math.max(0, region.x);
  const startY = Math.max(0, region.y);
  const endX = Math.min(parsedMap.width, region.x + region.width);
  const endY = Math.min(parsedMap.height, region.y + region.height);

  for (let y = startY; y < endY; y++) {
    for (let x = startX; x < endX; x++) {
      const index = y * parsedMap.width + x;
      const cell = parsedMap.cells[index];
      const resolution = byId.get(cell.fields.f2);

      if (!resolution || !resolution.resolved) {
        tiles.push({
          x,
          y,
          f2: cell.fields.f2,
          mapChipName: resolution?.mapChipName ?? `Unknown (${cell.fields.f2})`,
          resolved: false,
          debugFallbackColor: fallbackColorForId(cell.fields.f2),
          drawLayer: resolution?.drawLayer ?? 0,
          sizeWidth: resolution?.sizeWidth ?? 1,
          sizeHeight: resolution?.sizeHeight ?? 1,
          rotation: resolution?.rotation ?? 0,
        });
        continue;
      }

      tiles.push({
        x,
        y,
        f2: resolution.mapChipId,
        mapChipName: resolution.mapChipName,
        resolved: true,
        spriteSourcePng: resolution.spriteSourcePng,
        drawLayer: resolution.drawLayer,
        sizeWidth: resolution.sizeWidth,
        sizeHeight: resolution.sizeHeight,
        rotation: resolution.rotation,
      });
    }
  }

  return tiles;
}

export function buildMapChipVisualResolverReport(input: MapChipVisualResolverBuildInput): MapChipVisualResolverReport {
  const mapChipById = parseMapChipCsv(input.mapChipCsvText);
  const observed = [...new Set(input.observedF2Ids)].sort((a, b) => a - b);

  const imgInfRead = input.existingRelativeFilePaths.has(input.infPaths.imgInfRelativePath);
  const sebInfRead = input.existingRelativeFilePaths.has(input.infPaths.sebInfRelativePath);

  const resolutions: MapChipVisualResolution[] = observed.map((id) => {
    const csv = mapChipById.get(id);
    const candidates = input.mapAssetsByMapchipId[String(id)];
    const candidate = pickPrimaryCandidate(candidates);

    const mapChipName = csv?.name ?? `Unknown (${id})`;
    const drawLayer = csv?.layer ?? 0;
    const sizeWidth = csv?.sizeWidth ?? 1;
    const sizeHeight = csv?.sizeHeight ?? 1;
    const rotation = csv?.rotation ?? 0;
    const img = csv?.img ?? 0;
    const seb = csv?.seb ?? 0;

    const imageFamily = candidate?.sourcePng?.split("/")[0];
    const imageFilename = candidate?.sourcePng?.split("/").slice(1).join("/");
    const spriteSourcePng = candidate
      ? deriveSpritePngPath(candidate.assetId, input.spriteRootRelativePath, input.existingRelativeFilePaths)
      : undefined;

    const sebRoot = input.lookupRoots?.sebRootRelativePath ?? "";
    const optRoot = input.lookupRoots?.optRootRelativePath ?? "";
    const sebFilePath = candidate?.spriteName ? `${sebRoot}/${candidate.spriteName}.seb`.replace(/\\/g, "/") : undefined;
    const optFilePath = candidate?.spriteName ? `${optRoot}/${candidate.spriteName}.opt`.replace(/\\/g, "/") : undefined;
    const optInfoFilePath = candidate?.spriteName
      ? `${optRoot}/${candidate.spriteName}.optinfo`.replace(/\\/g, "/")
      : undefined;

    const sebFileRead = sebFilePath ? input.existingRelativeFilePaths.has(sebFilePath) : false;
    const optFileRead = optFilePath ? input.existingRelativeFilePaths.has(optFilePath) : false;
    const optInfoFileRead = optInfoFilePath ? input.existingRelativeFilePaths.has(optInfoFilePath) : false;

    const resolved = !!csv && !!candidate && !!spriteSourcePng;
    let unresolvedReason: string | undefined;
    if (!csv) {
      unresolvedReason = "Missing MapChip.csv row";
    } else if (!candidate) {
      unresolvedReason = "Missing map_assets mapping for mapchip id";
    } else if (!spriteSourcePng) {
      unresolvedReason = "Mapped asset has no extracted sprite PNG";
    }

    return {
      mapChipId: id,
      mapChipName,
      drawLayer,
      sizeWidth,
      sizeHeight,
      rotation,
      img,
      seb,
      imageFamily,
      imageFilename,
      spriteSourcePng,
      spriteAssetId: candidate?.assetId,
      frameRect: candidate?.rect,
      anchor: candidate?.pivot,
      sourceMetadata: {
        imgInfRead,
        sebInfRead,
        sebFilePath,
        sebFileRead,
        optFilePath,
        optFileRead,
        optInfoFilePath,
        optInfoFileRead,
      },
      resolved,
      unresolvedReason,
    };
  });

  const byId = new Map<number, MapChipVisualResolution>(resolutions.map((resolution) => [resolution.mapChipId, resolution]));
  const tiles = collectSampleRegionTiles(input.parsedMap, input.sampleRegion, byId);

  return {
    observedF2Count: observed.length,
    resolvedMapChipVisualCount: resolutions.filter((resolution) => resolution.resolved).length,
    unresolvedMapChipIds: resolutions.filter((resolution) => !resolution.resolved).map((resolution) => resolution.mapChipId),
    resolutions,
    sampleRegion: {
      x: input.sampleRegion.x,
      y: input.sampleRegion.y,
      width: input.sampleRegion.width,
      height: input.sampleRegion.height,
      tileCount: tiles.length,
      resolvedTileCount: tiles.filter((tile) => tile.resolved).length,
      unresolvedTileCount: tiles.filter((tile) => !tile.resolved).length,
      tiles,
    },
  };
}

export function buildMapChipVisualResolverMarkdown(report: MapChipVisualResolverReport): string {
  const lines: string[] = [];
  lines.push("# MapChip Visual Resolver Report");
  lines.push("");
  lines.push(`- Observed f2 IDs: ${report.observedF2Count}`);
  lines.push(`- Resolved visual assets: ${report.resolvedMapChipVisualCount}`);
  lines.push(`- Unresolved IDs: ${report.unresolvedMapChipIds.length === 0 ? "none" : report.unresolvedMapChipIds.join(", ")}`);
  lines.push("");
  lines.push("## Sample Region");
  lines.push(`- Origin: (${report.sampleRegion.x}, ${report.sampleRegion.y})`);
  lines.push(`- Size: ${report.sampleRegion.width}x${report.sampleRegion.height}`);
  lines.push(`- Tiles: ${report.sampleRegion.tileCount}`);
  lines.push(`- Resolved tiles: ${report.sampleRegion.resolvedTileCount}`);
  lines.push(`- Unresolved tiles: ${report.sampleRegion.unresolvedTileCount}`);
  lines.push("");

  lines.push("## Unresolved IDs");
  if (report.unresolvedMapChipIds.length === 0) {
    lines.push("- None");
  } else {
    for (const id of report.unresolvedMapChipIds) {
      const resolution = report.resolutions.find((item) => item.mapChipId === id);
      lines.push(`- ${id}: ${resolution?.unresolvedReason ?? "Unknown reason"}`);
    }
  }
  lines.push("");

  lines.push("## Resolutions (first 60)");
  const subset = report.resolutions.slice(0, 60);
  for (const entry of subset) {
    lines.push(
      `- ${entry.mapChipId} ${entry.mapChipName}: ${entry.resolved ? `resolved -> ${entry.spriteSourcePng}` : `unresolved (${entry.unresolvedReason})`}`,
    );
  }

  return `${lines.join("\n")}\n`;
}
