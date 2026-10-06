import { zoomAt, wheelZoomFactor } from '@/lib/map-camera';
import nativeMapFacilities from "@/game-data/native-map-facilities.json";
import nativeMapGround from "@/game-data/native-map-ground.json";
import { recoverWaterTiles } from "@/lib/native-water";
import { drawWaterSurface } from "@/lib/water-surface";
import { NativeOceanBackground } from "@/components/ka/native-ocean-background";
import { getOptDrawCommands, getSebOptDrawCommands, parseOptSprite, type OptSprite } from "@/lib/opt-sprite";
import { useEffect, useMemo, useRef, useState } from "react";
import { Skull } from "lucide-react";
import { parseNativeMapBinary, type NativeMapBinary, type NativeMapCell } from "@/runtime/world-builder/native-map";
import type { ParsedMapCell } from "@/runtime/world-builder/types";
import {
  NATIVE_MAP,
  mapTerrainCodeToType,
  parseTerrainMapCsv,
  type TerrainType,
} from "@/lib/monster-truth";
import fullTerrainCsv from "../data/full-terrain-map.csv?raw";

type CameraState = {
  offsetX: number;
  offsetY: number;
  zoom: number;
};

type MapChipRow = {
  id: number;
  type: number;
  category: number;
  name: string;
  res: number;
  img: number;
  seb: number;
  frame: number;
  relatedDataType: number;
  relatedDataId: number;
  field17: number;
  field18: number;
  field19: number;
  layer: number;
  field21: number;
  sizeWidth: number;
  sizeHeight: number;
  field24: number;
  field25: number;
  field26: number;
  field27: number;
};

type TerrainRow = {
  id: number;
  name: string;
  type: number;
  category: number;
  dataId: number;
  res: number;
  img: number;
  seb: number;
  frame: number;
  natureId: number;
  natureGroupId: number;
};

type SebRecord = {
  frameIndex: number;
  tick: number;
  sourceId: number;
  srcX: number;
  srcY: number;
  width: number;
  height: number;
  offsetX: number;
  offsetY: number;
};

type SebBlock = {
  blockIndex: number;
  period: number;
  records: SebRecord[];
};

type SebFile = {
  blockCount: number;
  headerValue: number;
  blocks: SebBlock[];
};

type OptSlot = {
  u: number;
  v: number;
  destX: number;
  destY: number;
  srcX: number;
  srcY: number;
  width: number;
  height: number;
  empty: boolean;
};

type OptMetadata = {
  cellWidth: number;
  cellHeight: number;
  cols: number;
  rows: number;
  slots: OptSlot[];
};

type AtlasRegion = {
  name: string;
  x: number;
  y: number;
  width: number;
  height: number;
};

type SpriteSelection = {
  assetFolder?: string;
  method: "seb-block0" | "opt-slot" | "full-image" | "placeholder-skip" | "missing-image";
  sourceFilename: string;
  srcX: number;
  srcY: number;
  srcW: number;
  srcH: number;
  drawOffsetX: number;
  drawOffsetY: number;
  frameInfo: string;
};

type TileDrawCommand = {
  cellX: number;
  cellY: number;
  rawMapValue: number;
  sourceField: "f1" | "f2";
  f1TerrainTypeValue: number | null;
  f1TerrainTypeName: string | null;
  selectedTerrainVisualFamily: string | null;
  selectedBaseReason: string;
  layer: number;
  mapChipId: number;
  mapChipName: string;
  mapChipCategory: number;
  mapChipImgId: number;
  mapChipSebId: number;
  mapChipFrame: number;
  resolvedImgFilename: string;
  imageCacheKeyUsed: string;
  imageObjectInstanceId: number | null;
  imageObjectResolvedSrc: string;
  imageObjectRequestPath: string;
  loadedPngPath: string;
  drawGroup: "base-terrain" | "nature-object" | "player-made-surface";
  natureCategory?: NatureVisualCategory;
  terrainName: string | null;
  selection: SpriteSelection;
};

type NatureVisualCategory = "terrain-nature" | "resource-treasure" | "human-npc" | "special-unknown";

type NatureCategoryVisibility = Record<NatureVisualCategory, boolean>;

type NatureRowSelection = {
  row: TerrainRow;
  reason: string;
  method: "visual-type-pool" | "nature-fields" | "direct-row-id" | "direct-img";
  field: "type-pool" | "f0/f3/f5" | "f0/f3" | "f0" | "f1" | "f2" | "f3" | "f4" | "f5";
  score: number;
  category?: NatureVisualCategory;
};

type RenderInterpretationMode = "f1-terrain" | "mapchip-f2" | "terrain-f2";
type F1RowSelectionMode = "coord-hash" | "map-fields";
type TerrainAlignmentMode = "sprite-native" | "diamond-fit";
type OverlayMode = "both" | "texture-only" | "diamond-only";

type LoadedImageAsset = {
  key: string;
  requestPath: string;
  resolvedSrc: string;
  instanceId: number;
  image: HTMLImageElement;
  opt?: OptSprite;
};

type PortGatePiece = {
  chipId: number;
  facilityId: number;
  buildingImageId: number;
  assetKey: "gate_00" | "gate_01" | "gate_02" | "gate_03";
  dx: number;
  dy: number;
};

type PortBridgePiece = {
  assetKey: "hashi00" | "bridge_side" | "bridge_wall_00";
  dx: number;
  dy: number;
  kind: "chip" | "wall";
  srcY?: number;
  srcH?: number;
};

type PortAssembly = {
  id: string;
  facilityId: 7 | 10;
  name: string;
  unlockLevel: number;
  zoneX: number;
  zoneY: number;
  cellX: number;
  cellY: number;
};

type PortGateLayout = Record<PortGatePiece["assetKey"], { dx: number; dy: number }>;

type PortGateDragState = {
  assetKey: PortGatePiece["assetKey"];
  startPointerWorld: { x: number; y: number };
  startOffset: { dx: number; dy: number };
  moved: boolean;
};

export type RuntimeExternalOverlay = {
  id: string;
  cellX: number;
  cellY: number;
  widthTiles?: number;
  spriteUrl?: string;
  spriteScale?: number;
  anchorOffsetTilesY?: number;
  anchorMode?: "south" | "center";
  markerText?: string;
  markerKind?: "monster" | "unit";
  invalid?: boolean;
  opacity?: number;
};

export type RuntimeCellHighlightOverlay = {
  x: number;
  y: number;
  fillColor?: string;
  strokeColor?: string;
  lineWidth?: number;
};

type RuntimeWorldRenderTestPageProps = {
  /** Optional editor layer; the public reference map has no editor state. */
  drawEditorLayer?: (context: CanvasRenderingContext2D, camera: CameraState, width: number, height: number) => void;
  onTerrainReady?: (land: Set<string>) => void;
  editorCellPicking?: boolean;
  editorCellElevation?: (cell:{x:number;y:number})=>number;
  onEditorLine?: (start:{x:number;y:number},end:{x:number;y:number},phase:'preview'|'commit'|'cancel')=>void;
  /** Optional gate: a line drag only starts when this accepts the pressed cell. */
  editorLineAllowed?: (start:{x:number;y:number})=>boolean;
  onEditorObjectClick?: (point:{x:number;y:number},camera:CameraState) => boolean;
  /** Stretch the renderer to its parent box instead of the fixed page height. */
  fillHeight?: boolean;
  publicMode?: boolean;
  initialZoom?: number;
  controlledZoom?: number;
  zoomRequest?: {factor:number;token:number}|null;
  showCellGrid?: boolean;
  visibleOnePieceFacilityIds?: number[];
  landOverrideCells?: Array<{ x: number; y: number }>;
  reclaimedTintCells?: Array<{ x: number; y: number }>;
  focusCell?: { x: number; y: number; token: number } | null;
  interactiveInPublicMode?: boolean;
  onCellClick?: (cell: { x: number; y: number }) => void;
  onCellHover?: (cell: { x: number; y: number } | null) => void;
  externalOverlays?: RuntimeExternalOverlay[];
  cellHighlightOverlays?: RuntimeCellHighlightOverlay[];
  showLevelOverlay?: boolean;
  hideNatureToggleButtons?: boolean;
  dimUncoveredConquestAreas?: boolean;
  conquestCoverageAreas?: Array<{
    area: string;
    level: number;
  }>;
  spawnOverlayAreas?: Array<{
    area: string;
    level: number;
    color?: string;
    patternIndex?: number;
  }>;
  initialNatureVisibility?: {
    terrain?: boolean;
    resources?: boolean;
    humans?: boolean;
    special?: boolean;
  };
};

type DiagnosticListItem = {
  key: string;
  count: number;
};

type RenderDiagnostics = {
  uniqueRawMapValues: number;
  uniqueMapChipIds: number;
  uniqueMapChipImgValues: number;
  uniqueResolvedPngFilenames: number;
  uniqueDrawnPngFilenames: number;
  uniqueSourceRects: number;
  topMapChipNames: DiagnosticListItem[];
  topImgIds: DiagnosticListItem[];
  topResolvedPngFilenames: DiagnosticListItem[];
  topDrawnPngFilenames: DiagnosticListItem[];
  stageDiagnosis: string;
};

type RenderPipeline = {
  oceanBackground: HTMLImageElement;
  viewportLighting: HTMLImageElement;
  waterAssets: Map<string, { image: HTMLImageElement; opt: OptSprite }>;
  parsedMap: NativeMapBinary;
  f1TerrainCommands: TileDrawCommand[];
  mapChipCommands: TileDrawCommand[];
  terrainCommands: TileDrawCommand[];
  imageCache: Map<string, LoadedImageAsset>;
  facilityBuildingCache: Map<number, LoadedImageAsset>;
  portAssetCache: Map<string, LoadedImageAsset>;
  f1TerrainDiagnostics: RenderDiagnostics;
  mapChipDiagnostics: RenderDiagnostics;
  terrainDiagnostics: RenderDiagnostics;
  fallbackSummary: {
    sebBlockZeroFallbackCount: number;
    optSlotFallbackCount: number;
    fullImageFallbackCount: number;
    skippedPlaceholderCount: number;
    missingImageCount: number;
    excludedControlRows: number;
    excludedStructures: number;
  };
  unresolvedBehaviors: string[];
  lookups: {
    mapChipById: Map<number, MapChipRow>;
    terrainById: Map<number, TerrainRow>;
    terrainByType: Map<number, TerrainRow[]>;
    terrainByCategory: Map<number, TerrainRow[]>;
    mapChipByLayer: Map<number, MapChipRow[]>;
    mapChipByCategory: Map<number, MapChipRow[]>;
    imageById: Map<number, string>;
    natureImageById: Map<number, string>;
  };
  usedMetadata: {
    mapChipRows: number;
    terrainRows: number;
    imgEntries: number;
    sebEntries: number;
    optFiles: number;
    atlasRegions: number;
  };
};

type ClickedCellPreview = {
  mode: "exact-crop" | "fallback-full-png";
  imageWidth: number;
  imageHeight: number;
  previewWidth: number;
  previewHeight: number;
  bgPosX: number;
  bgPosY: number;
  bgSizeW: number;
  bgSizeH: number;
  path: string;
};

const TILE_WIDTH = 48;
const TILE_HEIGHT = 24;
const FULL_TERRAIN_MAP = parseTerrainMapCsv(fullTerrainCsv);
const TERRAIN_TYPE_TO_AREA: Record<TerrainType, string> = {
  grass: "Grass",
  sand: "Sand",
  volcano: "Volcano",
  swamp: "Swamp",
  rock: "Rock",
  snow: "Snow",
  ground: "Ground",
};
const BASE_URL = typeof window !== "undefined"
  ? new URL(import.meta.env.BASE_URL ?? "/", window.location.origin).href
  : "/";

if (typeof window !== "undefined") {
  console.log("[runtime-world-render-test] window.location.href", window.location.href);
  console.log("[runtime-world-render-test] import.meta.env.BASE_URL", import.meta.env.BASE_URL);
  console.log("[runtime-world-render-test] resolved BASE_URL", BASE_URL);
}

function resolveAssetUrl(relativePath: string) {
  const resolved = new URL(relativePath.replace(/^\//, ""), BASE_URL).href;
  if (typeof window !== "undefined") {
    console.log("[runtime-world-render-test] resolveAssetUrl", relativePath, "->", resolved);
  }
  return resolved;
}

const MAP_PATH = resolveAssetUrl("world-assets/map/map_160_160.bin");
const MAP_CHIP_PATH = resolveAssetUrl("world-assets/xls/English.lproj/MapChip.txt");
const TERRAIN_PATH = resolveAssetUrl("world-assets/xls/English.lproj/Terrain.txt");
const CHIP_IMG_INF_PATH = resolveAssetUrl("world-assets/chip/img.inf");
const CHIP_SEB_INF_PATH = resolveAssetUrl("world-assets/chip/seb.inf");
const BUILDING_IMG_INF_PATH = resolveAssetUrl("world-assets/building/img.inf");
const NATURE_IMG_INF_PATH = resolveAssetUrl("world-assets/nature/img.inf");
const NATURE_SEB_INF_PATH = resolveAssetUrl("world-assets/nature/seb.inf");
const IMAGE_ATLAS_PATH = resolveAssetUrl("world-assets/image_atlas/ImageAtlas0.txt");
const PORT_ASSET_PATHS: Record<string, string> = {
  gate_00: resolveAssetUrl("world-assets/building/gate_00.png"),
  gate_01: resolveAssetUrl("world-assets/building/gate_01.png"),
  gate_02: resolveAssetUrl("world-assets/building/gate_02.png"),
  gate_03: resolveAssetUrl("world-assets/building/gate_03.png"),
  wasteland: resolveAssetUrl("world-assets/chip/wasteland.png"),
  hashi00: resolveAssetUrl("world-assets/chip/hashi00.png"),
  bridge_side: resolveAssetUrl("world-assets/chip/bridge_side.png"),
  bridge_wall_00: resolveAssetUrl("world-assets/wall/bridge_wall_00.png"),
};

const ENABLE_MAP_NATURE_OVERLAYS = true;
const CONTROL_ROW_EXCLUDES = new Set([27, 28, 29, 30, 31, 32, 277]);
const BASE_TERRAIN_FAMILY_PREFIXES = ["jimen", "iwa", "suna", "swamp", "tuchi", "wasteland", "volcano_soil", "kazan", "snow"];
const F1_TERRAIN_TYPE_NAMES: Record<number, string> = {
  1: "Ground",
  2: "Grass",
  3: "Sand",
  4: "Rock",
  5: "Volcano",
  6: "Snow",
  7: "Swamp",
  8: "Snow soil",
  9: "Desert soil",
  10: "Volcano soil",
  11: "Rocky soil",
  12: "Swamp soil",
  13: "Grassland soil",
  14: "End",
};
const NATURE_CATEGORY_LAYER_ORDER: Record<NatureVisualCategory, number> = {
  "terrain-nature": 1,
  "resource-treasure": 2,
  "human-npc": 3,
  "special-unknown": 4,
};
const NATURE_CATEGORY_LABELS: Record<NatureVisualCategory, string> = {
  "terrain-nature": "terrain nature",
  "resource-treasure": "resources",
  "human-npc": "humans",
  "special-unknown": "special",
};

type FacilityOverlay = {
  id: number;
  name: string;
  unlockLevel: number;
  buildingImageId: number;
  zoneX: number;
  zoneY: number;
  cellX: number;
  cellY: number;
  baseHeightPixels: number;
  spriteOffsetX: number;
  spriteOffsetY: number;
  spriteWidth: number;
  spriteHeight: number;
  originX: number;
  originY: number;
  chipId: number;
};
type DebugFacilityPlacementOverlay = {
  facilityId: number;
  mapChipId: number;
  mapChipImgId: number;
  name: string;
  tileX: number;
  tileY: number;
  sizeWidth?: number;
  sizeHeight?: number;
};
const OVERLAY_STYLES = {
  manualCorrected: {
    footprintStroke: "rgba(34, 211, 238, 0.95)",
    footprintFill: "rgba(34, 211, 238, 0.16)",
    anchorStroke: "rgba(34, 211, 238, 0.95)",
    labelBackground: "rgba(34, 211, 238, 0.85)",
  },
  aiCorrected: {
    footprintStroke: "rgba(234, 179, 8, 0.95)",
    footprintFill: "rgba(234, 179, 8, 0.16)",
    anchorStroke: "rgba(234, 179, 8, 0.95)",
    labelBackground: "rgba(234, 179, 8, 0.85)",
  },
  rawData: {
    footprintStroke: "rgba(236, 72, 153, 0.95)",
    footprintFill: "rgba(236, 72, 153, 0.16)",
    anchorStroke: "rgba(236, 72, 153, 0.95)",
    labelBackground: "rgba(236, 72, 153, 0.85)",
  },
  legendaryRaw: {
    footprintStroke: "rgba(34, 197, 94, 0.95)",
    footprintFill: "rgba(34, 197, 94, 0.16)",
    anchorStroke: "rgba(34, 197, 94, 0.95)",
    labelBackground: "rgba(34, 197, 94, 0.85)",
  },
} as const;

type OverlayStyle = (typeof OVERLAY_STYLES)[keyof typeof OVERLAY_STYLES];

function drawOverlayLabelBackground(
  context: CanvasRenderingContext2D,
  x: number,
  y: number,
  lines: string[],
  backgroundColor: string,
  zoom: number,
  xAlign: "center" | "right" = "center",
) {
  context.save();
  context.font = `bold ${Math.max(10, 12 * zoom)}px sans-serif`;
  context.textAlign = xAlign === "center" ? "center" : "left";
  context.textBaseline = "top";
  const padding = Math.max(4, 4 * zoom);
  const lineHeight = Math.max(14, 14 * zoom);
  const maxWidth = lines.reduce((width, line) => Math.max(width, context.measureText(line).width), 0);
  const boxWidth = maxWidth + padding * 2;
  const boxHeight = lines.length * lineHeight + padding * 2;
  const boxX = xAlign === "center" ? x - boxWidth / 2 : x - boxWidth;
  const boxY = y - boxHeight;

  context.fillStyle = backgroundColor;
  context.fillRect(boxX, boxY, boxWidth, boxHeight);
  context.strokeStyle = "rgba(15, 23, 42, 0.9)";
  context.lineWidth = Math.max(1, 1 * zoom);
  context.strokeRect(boxX, boxY, boxWidth, boxHeight);

  const textX = xAlign === "center" ? x : boxX + padding;
  context.fillStyle = "#ffffff";
  lines.forEach((line, index) => {
    context.fillText(line, textX, boxY + padding + index * lineHeight);
  });
  context.restore();
}

function drawOverlayLegend(context: CanvasRenderingContext2D, zoom: number, canvasWidth: number) {
  const margin = 10;
  const lineHeight = Math.max(16, 14 * zoom);
  const iconSize = Math.max(12, 12 * zoom);
  const entries = [
    { type: "fill" as const, text: "filled cells = occupancy" },
    { type: "rect" as const, text: "rectangle = sprite bounds" },
    { type: "dot" as const, text: "dot = sprite draw origin" },
    { type: "diamond" as const, text: "diamond = raw logical coordinate" },
  ];

  context.save();
  context.font = `bold ${Math.max(12, 14 * zoom)}px sans-serif`;
  context.textBaseline = "top";
  let maxTextWidth = 0;
  entries.forEach((entry) => {
    maxTextWidth = Math.max(maxTextWidth, context.measureText(entry.text).width);
  });
  const width = iconSize + 6 + maxTextWidth + margin * 2;
  const height = lineHeight * (entries.length + 1) + margin * 2;
  const x = canvasWidth - width - margin;
  const y = margin;

  context.fillStyle = "rgba(15, 23, 42, 0.85)";
  context.fillRect(x, y, width, height);
  context.strokeStyle = "rgba(255, 255, 255, 0.2)";
  context.lineWidth = 1;
  context.strokeRect(x, y, width, height);

  context.fillStyle = "#ffffff";
  context.fillText("Legend:", x + margin, y + margin);

  entries.forEach((entry, index) => {
    const entryY = y + margin + lineHeight * (index + 1);
    const iconX = x + margin + iconSize / 2;
    const iconCenterY = entryY + lineHeight / 2;

    context.strokeStyle = "#ffffff";
    context.fillStyle = "#ffffff";
    context.lineWidth = Math.max(1, 1.5 * zoom);

    switch (entry.type) {
      case "fill":
        context.fillRect(x + margin, entryY + (lineHeight - iconSize) / 2, iconSize, iconSize);
        break;
      case "rect":
        context.strokeRect(x + margin, entryY + (lineHeight - iconSize) / 2, iconSize, iconSize);
        break;
      case "dot":
        context.beginPath();
        context.arc(iconX, iconCenterY, Math.max(3, 4 * zoom), 0, Math.PI * 2);
        context.fill();
        break;
      case "diamond":
        context.beginPath();
        context.moveTo(iconX, iconCenterY - iconSize / 2);
        context.lineTo(iconX + iconSize / 2, iconCenterY);
        context.lineTo(iconX, iconCenterY + iconSize / 2);
        context.lineTo(iconX - iconSize / 2, iconCenterY);
        context.closePath();
        context.stroke();
        break;
    }

    context.fillStyle = "#ffffff";
    context.fillText(entry.text, x + margin + iconSize + 6, entryY);
  });
  context.restore();
}

function getNativeIndex(index: number, cellCount: number, nativeCount: number) {
  return Math.min(nativeCount - 1, Math.floor((index * nativeCount) / cellCount));
}

function hexToRgba(hex: string, alpha: number) {
  const normalized = hex.replace("#", "");
  if (normalized.length !== 6) {
    return `rgba(245,158,11,${alpha})`;
  }
  const r = parseInt(normalized.slice(0, 2), 16);
  const g = parseInt(normalized.slice(2, 4), 16);
  const b = parseInt(normalized.slice(4, 6), 16);
  return `rgba(${r},${g},${b},${alpha})`;
}

function drawSpawnStripeOverlay(
  context: CanvasRenderingContext2D,
  centerX: number,
  centerY: number,
  halfW: number,
  halfH: number,
  color: string,
  patternIndex: number,
  zoom: number,
) {
  const stripeColor = hexToRgba(color, 0.78);
  const fillColor = hexToRgba(color, 0.18);
  const diagonalA = patternIndex % 2 === 0;
  const spacing = Math.max(3, Math.round(6 * zoom));

  context.save();
  context.beginPath();
  context.moveTo(centerX, centerY - halfH);
  context.lineTo(centerX + halfW, centerY);
  context.lineTo(centerX, centerY + halfH);
  context.lineTo(centerX - halfW, centerY);
  context.closePath();
  context.clip();

  context.fillStyle = fillColor;
  context.fillRect(centerX - halfW, centerY - halfH, halfW * 2, halfH * 2);

  context.strokeStyle = stripeColor;
  context.lineWidth = Math.max(1, 1.4 * zoom);
  const min = -halfW - halfH;
  const max = halfW + halfH;
  for (let offset = min; offset <= max; offset += spacing) {
    context.beginPath();
    if (diagonalA) {
      context.moveTo(centerX - halfW + offset, centerY + halfH);
      context.lineTo(centerX + halfW + offset, centerY - halfH);
    } else {
      context.moveTo(centerX - halfW + offset, centerY - halfH);
      context.lineTo(centerX + halfW + offset, centerY + halfH);
    }
    context.stroke();
  }

  context.restore();
}

// Debug-only isolated facility placement test: exactly one simple building at one fixed valid tile.
const DEBUG_FACILITY_PLACEMENT_OVERLAYS: DebugFacilityPlacementOverlay[] = [
  {
    facilityId: 172,
    mapChipId: 232,
    mapChipImgId: 69,
    name: "CORRECTED Ranking Board: 102,134",
    tileX: 102,
    tileY: 134,
  },
];
const DEBUG_RAW_RANKING_BOARD_OVERLAYS: DebugFacilityPlacementOverlay[] = [
  {
    // Source: raw MapChip / Facility_lookup / NATIVE_MAP zone placement evidence.
    // Raw facility coordinate = (104, 136) for Ranking Board.
    // This overlay uses the raw tile directly with NO -2 / -2 correction.
    facilityId: 172,
    mapChipId: 232,
    mapChipImgId: 69,
    name: "RAW DATA Ranking Board: 104,136",
    sizeWidth: 2,
    sizeHeight: 2,
    tileX: 104,
    tileY: 136,
  },
];
const DEBUG_RAW_LEGENDARY_CAVE_OVERLAYS: DebugFacilityPlacementOverlay[] = [
  {
    // Source: raw MapChip / Facility_lookup / NATIVE_MAP zone placement evidence.
    // Raw facility coordinate = (88, 24) for Legendary Cave.
    // This overlay uses the raw tile directly with NO -2 / -2 correction.
    facilityId: 196,
    mapChipId: 258,
    mapChipImgId: 92,
    name: "RAW DATA Legendary Cave: 88,24",
    sizeWidth: 2,
    sizeHeight: 2,
    tileX: 88,
    tileY: 24,
  },
];

const NATURE_CATEGORY_CHANCE_BY_TERRAIN_TYPE: Record<number, Partial<Record<NatureVisualCategory, number>>> = {
  1: { "terrain-nature": 0.14, "resource-treasure": 0.02, "human-npc": 0.02, "special-unknown": 0.02 },
  2: { "terrain-nature": 0.3, "resource-treasure": 0.04, "human-npc": 0.02, "special-unknown": 0.04 },
  3: { "terrain-nature": 0.18, "resource-treasure": 0.08, "human-npc": 0.015, "special-unknown": 0.03 },
  4: { "terrain-nature": 0.28, "resource-treasure": 0.14, "human-npc": 0.015, "special-unknown": 0.04 },
  5: { "terrain-nature": 0.24, "resource-treasure": 0.12, "human-npc": 0.01, "special-unknown": 0.03 },
  6: { "terrain-nature": 0.2, "resource-treasure": 0.04, "human-npc": 0.01, "special-unknown": 0.02 },
  7: { "terrain-nature": 0.24, "resource-treasure": 0.04, "human-npc": 0.01, "special-unknown": 0.02 },
};
// Native area-unlock targets; cellX/Y remain exclusive ends for legacy debug overlays.
const ONE_PIECE_FACILITY_OVERLAYS: FacilityOverlay[] = nativeMapFacilities;
const PORT_ASSEMBLIES: PortAssembly[] = [
  { id: "port-level-7", facilityId: 7, name: "Port", unlockLevel: 7, zoneX: 9, zoneY: 6, cellX: 152, cellY: 100 },
  { id: "port-level-44", facilityId: 10, name: "Port", unlockLevel: 44, zoneX: 9, zoneY: 2, cellX: 152, cellY: 36 },
];

function getPortCompositeBaseCell(port: PortAssembly): { x: number; y: number } {
  return {
    x: port.cellX,
    y: port.cellY,
  };
}

// Native PlaceChip combination offsets from the 4x4 port origin, direction1.
const PORT_GATE_PIECES: PortGatePiece[] = [
  { chipId: 67, facilityId: 7, buildingImageId: 2, assetKey: "gate_00", dx: 0, dy: 0 },
  { chipId: 68, facilityId: 8, buildingImageId: 3, assetKey: "gate_01", dx: 0, dy: 2 },
  { chipId: 69, facilityId: 9, buildingImageId: 20, assetKey: "gate_02", dx: 2, dy: 2 },
  { chipId: 70, facilityId: 10, buildingImageId: 21, assetKey: "gate_03", dx: 2, dy: 0 },
];
const DEFAULT_PORT_GATE_LAYOUT: PortGateLayout = {
  gate_00: { dx: 0, dy: 0 }, gate_01: { dx: 0, dy: 2 },
  gate_02: { dx: 2, dy: 2 }, gate_03: { dx: 2, dy: 0 },
};
const PORT_BRIDGE_PIECES: PortBridgePiece[] = Array.from({ length: 8 }, (_, i) => ({
  assetKey: "hashi00", dx: 4 + i % 4, dy: 1 + Math.floor(i / 4), kind: "chip",
}));
const F1_TERRAIN_FAMILY_BY_TYPE: Record<number, string> = {
  1: "tuchi",
  2: "jimen",
  3: "suna",
  4: "iwa",
  5: "kazan",
  6: "snow",
  7: "swamp",
  8: "snow",
  9: "suna",
  10: "volcano_soil",
  11: "iwa",
  12: "swamp",
  13: "jimen",
};

const OLD_PORT_MAPCHIP_IDS = new Set<number>([67, 68, 69, 70]);
const PREFERRED_DIRT_MAPCHIP_IDS = new Set<number>([5, 6, 7, 24]);

function isDirtLikeCommand(command: TileDrawCommand): boolean {
  if ([5, 6, 7].includes(command.mapChipId)) return true;
  const haystack = `${command.terrainName ?? ""} ${command.mapChipName} ${command.resolvedImgFilename} ${command.selection.sourceFilename}`.toLowerCase();
  const hasDirtToken = /\bdirt\b|\btuchi\b|\bjimen\b|mud|soil/.test(haystack);
  const hasNonDirtToken = /\bgrass\b|\bkusa\b|\bwater\b|\bmizu\b|sea|river|ocean|snow|rock|stone/.test(haystack);
  return hasDirtToken && !hasNonDirtToken;
}

function isWaterLikeCommand(command: TileDrawCommand): boolean {
  const haystack = `${command.terrainName ?? ""} ${command.mapChipName} ${command.resolvedImgFilename} ${command.selection.sourceFilename}`;
  return isWaterToken(haystack);
}

function readStoredPortGateLayout(): PortGateLayout {
  if (typeof window === "undefined") {
    return DEFAULT_PORT_GATE_LAYOUT;
  }

  try {
    const raw = window.localStorage.getItem("ka-runtime-port-gate-layout-native-v1");
    if (!raw) {
      return DEFAULT_PORT_GATE_LAYOUT;
    }
    const parsed = JSON.parse(raw) as Partial<PortGateLayout>;
    return {
      gate_00: normalizePortGateOffset(parsed.gate_00, DEFAULT_PORT_GATE_LAYOUT.gate_00),
      gate_01: normalizePortGateOffset(parsed.gate_01, DEFAULT_PORT_GATE_LAYOUT.gate_01),
      gate_02: normalizePortGateOffset(parsed.gate_02, DEFAULT_PORT_GATE_LAYOUT.gate_02),
      gate_03: normalizePortGateOffset(parsed.gate_03, DEFAULT_PORT_GATE_LAYOUT.gate_03),
    };
  } catch {
    return DEFAULT_PORT_GATE_LAYOUT;
  }
}

function normalizePortGateOffset(
  value: { dx?: number; dy?: number } | undefined,
  fallback: { dx: number; dy: number },
): { dx: number; dy: number } {
  const dx = typeof value?.dx === "number" && Number.isFinite(value.dx) ? value.dx : fallback.dx;
  const dy = typeof value?.dy === "number" && Number.isFinite(value.dy) ? value.dy : fallback.dy;
  return { dx, dy };
}

function UnverifiedRuntimeWorldRenderTestPage({
  drawEditorLayer,
  onTerrainReady,
  editorCellPicking = false,
  editorCellElevation,
  onEditorLine,
  editorLineAllowed,
  onEditorObjectClick,
  fillHeight = false,
  publicMode = false,
  initialZoom = 0.65,
  controlledZoom,
  zoomRequest,
  showCellGrid = false,
  visibleOnePieceFacilityIds,
  landOverrideCells = [],
  reclaimedTintCells = [],
  focusCell = null,
  interactiveInPublicMode = false,
  onCellClick,
  onCellHover,
  externalOverlays = [],
  cellHighlightOverlays = [],
  showLevelOverlay = false,
  hideNatureToggleButtons = false,
  dimUncoveredConquestAreas = false,
  conquestCoverageAreas = [],
    spawnOverlayAreas = [],
  initialNatureVisibility,
}: RuntimeWorldRenderTestPageProps = {}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const suppressNextCanvasClickRef = useRef(false);
  const editorLineRef=useRef<{id:number;start:{x:number;y:number};end:{x:number;y:number}}|null>(null);
  const pointerStartRef = useRef<{x:number;y:number}|null>(null);
  const activeTouchPointsRef = useRef<Map<number, { x: number; y: number }>>(new Map());
  const touchMovedRef = useRef(false);
  const pinchStateRef = useRef<{
    startDistance: number;
    startZoom: number;
    anchorWorldX: number;
    anchorWorldY: number;
  } | null>(null);
  const [camera, setCamera] = useState<CameraState>({ offsetX: 0, offsetY: 0, zoom: editorCellPicking ? initialZoom : 1 });
  const [resizeVersion,setResizeVersion]=useState(0);
  useEffect(()=>{
    if(!editorCellPicking||!containerRef.current)return;
    let previous:{width:number;height:number}|null=null;
    const observer=new ResizeObserver(entries=>{
      const {width,height}=entries[0].contentRect;
      if(previous?.width===width&&previous.height===height)return;
      if(previous){const dx=(width-previous.width)/2,dy=(height-previous.height)/2;setCamera(c=>({...c,offsetX:c.offsetX+dx,offsetY:c.offsetY+dy}));}
      previous={width,height};setResizeVersion(v=>v+1);
    });
    observer.observe(containerRef.current);return()=>observer.disconnect();
  },[editorCellPicking]);
  const [dragging, setDragging] = useState(false);
  const [hoveredCell, setHoveredCell] = useState<{ x: number; y: number } | null>(null);
  const [selectedCell, setSelectedCell] = useState<{ x: number; y: number } | null>(null);
  const [twoByTwoMode, setTwoByTwoMode] = useState(false);
  const [renderMode, setRenderMode] = useState<RenderInterpretationMode>("f1-terrain");
  const [f1RowSelectionMode, setF1RowSelectionMode] = useState<F1RowSelectionMode>("map-fields");
  const [terrainAlignmentMode, setTerrainAlignmentMode] = useState<TerrainAlignmentMode>("diamond-fit");
  const [overlayMode, setOverlayMode] = useState<OverlayMode>("texture-only");
  const [showNatureDebugOverlay, setShowNatureDebugOverlay] = useState(false);
  const [showTerrainNature, setShowTerrainNature] = useState(initialNatureVisibility?.terrain ?? true);
  const [showResourceNature, setShowResourceNature] = useState(initialNatureVisibility?.resources ?? true);
  const [showHumanNature, setShowHumanNature] = useState(initialNatureVisibility?.humans ?? true);
  const [showSpecialNature, setShowSpecialNature] = useState(initialNatureVisibility?.special ?? false);
  const [showOnePieceFacilities, setShowOnePieceFacilities] = useState(true);
  const [showNoOffsetFacilityDuplicates, setShowNoOffsetFacilityDuplicates] = useState(false);
  const [showCorrectedRankingBoardPlacementTest, setShowCorrectedRankingBoardPlacementTest] = useState(false);
  const [showRawRankingBoardPlacementTest, setShowRawRankingBoardPlacementTest] = useState(false);
  const [showLegendaryCaveRawPlacementTest, setShowLegendaryCaveRawPlacementTest] = useState(false);
  const [showPortAssemblies, setShowPortAssemblies] = useState(true);
  const [showPortBridgePieces, setShowPortBridgePieces] = useState(true);
  const [portGateLayout, setPortGateLayout] = useState<PortGateLayout>(() => (publicMode ? DEFAULT_PORT_GATE_LAYOUT : readStoredPortGateLayout()));
  const [portGateDrag, setPortGateDrag] = useState<PortGateDragState | null>(null);
  const [forceVisibleNatureMode, setForceVisibleNatureMode] = useState(false);
  const [forceVisibleLargeNatureMode, setForceVisibleLargeNatureMode] = useState(false);
  const [showNatureCandidateBounds, setShowNatureCandidateBounds] = useState(false);
  const [showTileCenters, setShowTileCenters] = useState(false);
  const [showTextureBounds, setShowTextureBounds] = useState(false);
  const [logicalFootprintScale, setLogicalFootprintScale] = useState(1);
  const [defaultTerrainMode, setDefaultTerrainMode] = useState(false);
  const [pipeline, setPipeline] = useState<RenderPipeline | null>(null);
  const [error, setError] = useState<string | null>(null);
  const allowMapInteraction = !publicMode || interactiveInPublicMode || !!onCellClick || !!onCellHover;
  const visibleOnePieceFacilitySet = useMemo(
    () => (visibleOnePieceFacilityIds ? new Set(visibleOnePieceFacilityIds) : null),
    [visibleOnePieceFacilityIds],
  );
  const visibleOnePieceFacilities = useMemo(
    () => (visibleOnePieceFacilitySet
      ? ONE_PIECE_FACILITY_OVERLAYS.filter((facility) => visibleOnePieceFacilitySet.has(facility.id))
      : ONE_PIECE_FACILITY_OVERLAYS),
    [visibleOnePieceFacilitySet],
  );
  const reclaimedTintKeys = useMemo(() => {
    const keys = new Set<string>();
    for (const cell of reclaimedTintCells) {
      keys.add(`${cell.x},${cell.y}`);
    }
    return keys;
  }, [reclaimedTintCells]);

  useEffect(() => {
    if (typeof controlledZoom !== "number" || !Number.isFinite(controlledZoom)) {
      return;
    }
    const nextZoom = Math.max(0.08, Math.min(3.5, controlledZoom));
    setCamera((previous) =>
      Math.abs(previous.zoom - nextZoom) < 0.0001
        ? previous
        : {
            ...previous,
            zoom: nextZoom,
          },
    );
  }, [controlledZoom]);

  useEffect(() => {
    if (!focusCell) {
      return;
    }
    const container = containerRef.current;
    if (!container) {
      return;
    }
    const width = container.clientWidth;
    const height = container.clientHeight;
    if (width <= 0 || height <= 0) {
      return;
    }

    setCamera(previous=>({
      ...previous,
      offsetX:width/2-(focusCell.x-focusCell.y)*TILE_WIDTH*previous.zoom/2,
      offsetY:height/2-(focusCell.x+focusCell.y)*TILE_HEIGHT*previous.zoom/2,
    }));
  }, [focusCell]);

  useEffect(()=>{
    if(!zoomRequest||!containerRef.current)return;
    const {clientWidth,clientHeight}=containerRef.current;
    setCamera(previous=>zoomAt(previous,previous.zoom*zoomRequest.factor,clientWidth/2,clientHeight/2));
  },[zoomRequest]);

  useEffect(()=>{
    const canvas=canvasRef.current;
    if(!editorCellPicking||!canvas)return;
    const wheel=(event:WheelEvent)=>{
      event.preventDefault();event.stopPropagation();
      const rect=canvas.getBoundingClientRect();
      setCamera(previous=>zoomAt(previous,previous.zoom*wheelZoomFactor(event.deltaY,event.deltaMode),event.clientX-rect.left,event.clientY-rect.top));
    };
    canvas.addEventListener('wheel',wheel,{passive:false});
    return()=>canvas.removeEventListener('wheel',wheel);
  },[editorCellPicking]);

  const natureCategoryVisibility = useMemo<NatureCategoryVisibility>(() => ({
    "terrain-nature": showTerrainNature,
    "resource-treasure": showResourceNature,
    "human-npc": showHumanNature,
    "special-unknown": showSpecialNature,
  }), [showHumanNature, showResourceNature, showSpecialNature, showTerrainNature]);

  useEffect(() => {
    if (publicMode) {
      return;
    }
    window.localStorage.setItem("ka-runtime-port-gate-layout-native-v1", JSON.stringify(portGateLayout));
  }, [portGateLayout, publicMode]);

  function updatePortGateOffset(assetKey: PortGatePiece["assetKey"], axis: "dx" | "dy", value: number) {
    if (!Number.isFinite(value)) {
      return;
    }
    setPortGateLayout((previous) => ({
      ...previous,
      [assetKey]: {
        ...previous[assetKey],
        [axis]: value,
      },
    }));
  }

  function resetPortGateLayout() {
    setPortGateLayout(DEFAULT_PORT_GATE_LAYOUT);
  }

  const natureOverlayCommandCount = useMemo(() => {
    if (!pipeline) {
      return 0;
    }
    return filterNatureCommandsByVisibility(pipeline.f1TerrainCommands, natureCategoryVisibility).filter((command) => command.drawGroup === "nature-object").length;
  }, [natureCategoryVisibility, pipeline]);

  const natureOverlayCellKeys = useMemo(() => {
    if (!pipeline) {
      return new Set<string>();
    }
    return new Set(
      pipeline.f1TerrainCommands
        .filter((command) => command.drawGroup === "nature-object")
        .filter((command) => isNatureCommandVisible(command, natureCategoryVisibility))
        .map((command) => `${command.cellX},${command.cellY}`),
    );
  }, [natureCategoryVisibility, pipeline]);

  const spawnOverlayByAreaLevel = useMemo(() => {
    const map = new Map<string, Array<{ color: string; patternIndex: number }>>();
    for (const overlay of spawnOverlayAreas) {
      const area = overlay.area?.trim().toLowerCase();
      if (!area || !Number.isFinite(overlay.level)) {
        continue;
      }
      const key = `${area}|${overlay.level}`;
      const entries = map.get(key) ?? [];
      entries.push({
        color: overlay.color ?? "#f59e0b",
        patternIndex: overlay.patternIndex ?? 0,
      });
      map.set(key, entries);
    }
    return map;
  }, [spawnOverlayAreas]);

  const spawnOverlayCells = useMemo(() => {
    if (!pipeline || spawnOverlayByAreaLevel.size === 0) {
      return [] as Array<{ x: number; y: number; overlays: Array<{ color: string; patternIndex: number }> }>;
    }
    const mapWidth = pipeline.parsedMap.width;
    const mapHeight = pipeline.parsedMap.height;
    const terrainRows = FULL_TERRAIN_MAP.length;
    const terrainCols = FULL_TERRAIN_MAP[0]?.length ?? 0;
    const nativeRows = NATIVE_MAP.length;
    const nativeCols = NATIVE_MAP[0]?.length ?? 0;
    if (mapWidth <= 0 || mapHeight <= 0 || terrainRows <= 0 || terrainCols <= 0 || nativeRows <= 0 || nativeCols <= 0) {
      return [];
    }

    const cells: Array<{ x: number; y: number; overlays: Array<{ color: string; patternIndex: number }> }> = [];

    for (let y = 0; y < mapHeight; y += 1) {
      const terrainY = getNativeIndex(y, mapHeight, terrainRows);
      const nativeY = getNativeIndex(y, mapHeight, nativeRows);
      for (let x = 0; x < mapWidth; x += 1) {
        const terrainX = getNativeIndex(x, mapWidth, terrainCols);
        const nativeX = getNativeIndex(x, mapWidth, nativeCols);
        const terrainType = mapTerrainCodeToType(FULL_TERRAIN_MAP[terrainY]?.[terrainX]);
        if (!terrainType) {
          continue;
        }
        const level = NATIVE_MAP[nativeY]?.[nativeX]?.level;
        if (!Number.isFinite(level)) {
          continue;
        }
        const area = TERRAIN_TYPE_TO_AREA[terrainType].toLowerCase();
        const overlays = spawnOverlayByAreaLevel.get(`${area}|${level}`);
        if (!overlays || overlays.length === 0) {
          continue;
        }
        cells.push({ x, y, overlays });
      }
    }

    return cells;
  }, [pipeline, spawnOverlayByAreaLevel]);

  const conquestCoverageSet = useMemo(() => {
    const set = new Set<string>();
    for (const area of conquestCoverageAreas) {
      const areaName = area.area?.trim().toLowerCase();
      if (!areaName || !Number.isFinite(area.level)) {
        continue;
      }
      set.add(`${areaName}|${area.level}`);
    }
    return set;
  }, [conquestCoverageAreas]);

  const conquestCoveredCellKeys = useMemo(() => {
    if (!pipeline || conquestCoverageSet.size === 0) {
      return new Set<string>();
    }
    const mapWidth = pipeline.parsedMap.width;
    const mapHeight = pipeline.parsedMap.height;
    const terrainRows = FULL_TERRAIN_MAP.length;
    const terrainCols = FULL_TERRAIN_MAP[0]?.length ?? 0;
    const nativeRows = NATIVE_MAP.length;
    const nativeCols = NATIVE_MAP[0]?.length ?? 0;
    if (mapWidth <= 0 || mapHeight <= 0 || terrainRows <= 0 || terrainCols <= 0 || nativeRows <= 0 || nativeCols <= 0) {
      return new Set<string>();
    }

    const keys = new Set<string>();

    for (let y = 0; y < mapHeight; y += 1) {
      const terrainY = getNativeIndex(y, mapHeight, terrainRows);
      const nativeY = getNativeIndex(y, mapHeight, nativeRows);
      for (let x = 0; x < mapWidth; x += 1) {
        const terrainX = getNativeIndex(x, mapWidth, terrainCols);
        const nativeX = getNativeIndex(x, mapWidth, nativeCols);
        const terrainType = mapTerrainCodeToType(FULL_TERRAIN_MAP[terrainY]?.[terrainX]);
        if (!terrainType) {
          continue;
        }
        const level = NATIVE_MAP[nativeY]?.[nativeX]?.level;
        if (!Number.isFinite(level)) {
          continue;
        }
        const area = TERRAIN_TYPE_TO_AREA[terrainType].toLowerCase();
        if (conquestCoverageSet.has(`${area}|${level}`)) {
          keys.add(`${x},${y}`);
        }
      }
    }
    return keys;
  }, [conquestCoverageSet, pipeline]);

  const levelOverlayData = useMemo(() => {
    const empty = {
      edges: [] as Array<{ x: number; y: number; top: boolean; right: boolean; bottom: boolean; left: boolean }>,
      labels: [] as Array<{ x: number; y: number; level: number }>,
    };
    if (!pipeline || !showLevelOverlay) {
      return empty;
    }

    const mapWidth = pipeline.parsedMap.width;
    const mapHeight = pipeline.parsedMap.height;
    const nativeRows = NATIVE_MAP.length;
    const nativeCols = NATIVE_MAP[0]?.length ?? 0;
    if (mapWidth <= 0 || mapHeight <= 0 || nativeRows <= 0 || nativeCols <= 0) {
      return empty;
    }

    const nativeXByX = Array.from({ length: mapWidth }, (_, x) => getNativeIndex(x, mapWidth, nativeCols));
    const nativeYByY = Array.from({ length: mapHeight }, (_, y) => getNativeIndex(y, mapHeight, nativeRows));

    const edges: Array<{ x: number; y: number; top: boolean; right: boolean; bottom: boolean; left: boolean }> = [];
    for (let y = 0; y < mapHeight; y += 1) {
      const ny = nativeYByY[y];
      for (let x = 0; x < mapWidth; x += 1) {
        const nx = nativeXByX[x];
        const level = NATIVE_MAP[ny]?.[nx]?.level;
        if (!Number.isFinite(level)) {
          continue;
        }

        const top = y === 0 || nativeYByY[y - 1] !== ny;
        const bottom = y === mapHeight - 1 || nativeYByY[y + 1] !== ny;
        const left = x === 0 || nativeXByX[x - 1] !== nx;
        const right = x === mapWidth - 1 || nativeXByX[x + 1] !== nx;
        if (top || right || bottom || left) {
          edges.push({ x, y, top, right, bottom, left });
        }
      }
    }

    const labels: Array<{ x: number; y: number; level: number }> = [];
    for (let ny = 0; ny < nativeRows; ny += 1) {
      for (let nx = 0; nx < nativeCols; nx += 1) {
        const level = NATIVE_MAP[ny]?.[nx]?.level;
        if (!Number.isFinite(level)) {
          continue;
        }

        const minX = Math.floor((nx * mapWidth) / nativeCols);
        const maxX = Math.floor(((nx + 1) * mapWidth) / nativeCols) - 1;
        const minY = Math.floor((ny * mapHeight) / nativeRows);
        const maxY = Math.floor(((ny + 1) * mapHeight) / nativeRows) - 1;
        if (maxX < minX || maxY < minY) {
          continue;
        }

        labels.push({
          x: Math.floor((minX + maxX) / 2),
          y: Math.floor((minY + maxY) / 2),
          level,
        });
      }
    }

    return { edges, labels };
  }, [pipeline, showLevelOverlay]);

  const natureDebugSummary = useMemo(() => {
    if (!pipeline) {
      return {
        commandCount: 0,
        cellCount: 0,
        rowIdCount: 0,
        spriteCount: 0,
        largeSourceRectCount: 0,
        fullImageSourceCount: 0,
        topRows: [] as DiagnosticListItem[],
        topSprites: [] as DiagnosticListItem[],
        topReasons: [] as DiagnosticListItem[],
        availableCategories: [] as DiagnosticListItem[],
        largeAvailableCategories: [] as DiagnosticListItem[],
      };
    }

    const commands = filterNatureCommandsByVisibility(pipeline.f1TerrainCommands, natureCategoryVisibility).filter((command) => command.drawGroup === "nature-object");
    const byRowId = new Map<string, number>();
    const bySprite = new Map<string, number>();
    const byReason = new Map<string, number>();
    const availableByCategory = new Map<string, number>();
    const largeAvailableByCategory = new Map<string, number>();
    let largeSourceRectCount = 0;
    let fullImageSourceCount = 0;

    for (const [category, rows] of pipeline.lookups.terrainByCategory) {
      availableByCategory.set(String(category), rows.length);
      for (const row of rows) {
        const filename = row.res === 20 ? pipeline.lookups.natureImageById.get(row.img) : pipeline.lookups.imageById.get(row.img);
        const image = filename ? pipeline.imageCache.get(filename)?.image : null;
        if (image && (image.width >= TILE_WIDTH || image.height >= TILE_HEIGHT * 2)) {
          largeAvailableByCategory.set(String(category), (largeAvailableByCategory.get(String(category)) ?? 0) + 1);
        }
      }
    }

    for (const command of commands) {
      byRowId.set(`${command.mapChipId}:${command.mapChipName}`, (byRowId.get(`${command.mapChipId}:${command.mapChipName}`) ?? 0) + 1);
      bySprite.set(command.selection.sourceFilename || command.resolvedImgFilename || "missing", (bySprite.get(command.selection.sourceFilename || command.resolvedImgFilename || "missing") ?? 0) + 1);
      byReason.set(command.selectedBaseReason, (byReason.get(command.selectedBaseReason) ?? 0) + 1);
      if (command.selection.srcW >= TILE_WIDTH || command.selection.srcH >= TILE_HEIGHT * 2) {
        largeSourceRectCount += 1;
      }
      const image = pipeline.imageCache.get(command.selection.sourceFilename)?.image;
      if (image && (image.width >= TILE_WIDTH || image.height >= TILE_HEIGHT * 2)) {
        fullImageSourceCount += 1;
      }
    }

    const topEntries = (map: Map<string, number>, limit: number) =>
      [...map.entries()]
        .sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0]))
        .slice(0, limit)
        .map(([key, count]) => ({ key, count }));

    return {
      commandCount: commands.length,
      cellCount: new Set(commands.map((command) => `${command.cellX},${command.cellY}`)).size,
      rowIdCount: byRowId.size,
      spriteCount: bySprite.size,
      largeSourceRectCount,
      fullImageSourceCount,
      topRows: topEntries(byRowId, 12),
      topSprites: topEntries(bySprite, 12),
      topReasons: topEntries(byReason, 8),
      availableCategories: topEntries(availableByCategory, 8),
      largeAvailableCategories: topEntries(largeAvailableByCategory, 8),
    };
  }, [natureCategoryVisibility, pipeline]);

  const activeCommands = useMemo(() => {
    if (!pipeline) {
      return [] as TileDrawCommand[];
    }
    if (renderMode === "f1-terrain") {
      return filterNatureCommandsByVisibility(pipeline.f1TerrainCommands, natureCategoryVisibility);
    }
    return renderMode === "terrain-f2" ? pipeline.terrainCommands : pipeline.mapChipCommands;
  }, [natureCategoryVisibility, pipeline, renderMode]);

  const diagnostics = useMemo(() => {
    if (!pipeline) {
      return {
        uniqueRawMapValues: 0,
        uniqueMapChipIds: 0,
        uniqueMapChipImgValues: 0,
        uniqueResolvedPngFilenames: 0,
        uniqueDrawnPngFilenames: 0,
        uniqueSourceRects: 0,
        topMapChipNames: [] as DiagnosticListItem[],
        topImgIds: [] as DiagnosticListItem[],
        topResolvedPngFilenames: [] as DiagnosticListItem[],
        topDrawnPngFilenames: [] as DiagnosticListItem[],
        stageDiagnosis: "",
      };
    }
    if (renderMode === "f1-terrain") {
      return pipeline.f1TerrainDiagnostics;
    }
    return renderMode === "terrain-f2" ? pipeline.terrainDiagnostics : pipeline.mapChipDiagnostics;
  }, [pipeline, renderMode]);

  useEffect(() => {
    let disposed = false;

    async function loadPipeline() {
      try {
        const loaded = await buildEngineRenderPipeline(f1RowSelectionMode);
        if (disposed) {
          return;
        }
        setPipeline(loaded);
        setError(null);
      } catch (loadError) {
        if (disposed) {
          return;
        }
        const message = loadError instanceof Error ? loadError.message : String(loadError);
        setError(message);
      }
    }

    loadPipeline();
    return () => {
      disposed = true;
    };
  }, [f1RowSelectionMode]);

  useEffect(() => {
    const container = containerRef.current;
    const canvas = canvasRef.current;
    if (!container || !canvas || !pipeline) {
      return;
    }

    const width = container.clientWidth;
    const height = container.clientHeight;
    if (width <= 0 || height <= 0) {
      return;
    }

    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }

    if (camera.zoom === 1 && camera.offsetX === 0 && camera.offsetY === 0) {
      const reset = computeInitialCamera(pipeline.parsedMap.width, pipeline.parsedMap.height, width, height, initialZoom);
      setCamera(reset);
    }
  }, [camera.offsetX, camera.offsetY, camera.zoom, initialZoom, pipeline]);

  useEffect(() => {
    const canvas = canvasRef.current;
    const container = containerRef.current;
    if (!canvas || !container) {
      return;
    }

    const width = container.clientWidth;
    const height = container.clientHeight;
    if (width <= 0 || height <= 0) {
      return;
    }

    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }

    const context = canvas.getContext("2d");
    if (!context) {
      return;
    }

    context.clearRect(0, 0, width, height);
    if (!pipeline) {
      context.fillStyle = "#1d4f6c";
      context.fillRect(0, 0, width, height);
      if (!publicMode) {
        drawGridBackground(context, width, height);
      }
      return;
    }

    // Transparent world canvas composites native water over the animated ocean.
    // Keeping the ocean separate avoids recomputing world geometry every tick.
    context.imageSmoothingEnabled = false;

    const filteredCommands = defaultTerrainMode
      ? activeCommands.filter((command) => command.drawGroup === "base-terrain" || command.drawGroup === "nature-object")
      : activeCommands;

    const withoutOldPortMapchips = filteredCommands.filter((command) => !OLD_PORT_MAPCHIP_IDS.has(command.mapChipId));

    const forcedPortDirtCommands = (() => {
      const forcedDirtCells = new Set<string>();
      for (const cell of landOverrideCells) {
        forcedDirtCells.add(`${cell.x},${cell.y}`);
      }

      if (showPortAssemblies) {
        for (const port of nativeMapGround.ports) {
          for (const [x, y] of port.ground) forcedDirtCells.add(`${x},${y}`);
        }
      }
      if (showOnePieceFacilities) {
        for (const facility of visibleOnePieceFacilities) {
          for (let y = facility.originY; y < facility.originY + 2; y++)
            for (let x = facility.originX; x < facility.originX + 2; x++)
              forcedDirtCells.add(`${x},${y}`);
        }
      }

      if (forcedDirtCells.size === 0) {
        return withoutOldPortMapchips;
      }

      const dirtTemplates = withoutOldPortMapchips
        .filter((command) => command.selection.assetFolder !== "nature")
        .filter((command) => command.drawGroup !== "nature-object")
        .filter(isDirtLikeCommand)
        .sort((left, right) => left.mapChipId - right.mapChipId);

      const nonWaterByCell = new Map<string, TileDrawCommand>();
      for (const template of dirtTemplates) {
        const key = `${template.cellX},${template.cellY}`;
        if (!nonWaterByCell.has(key)) {
          nonWaterByCell.set(key, template);
        }
      }

      const findTemplateForCell = (x: number, y: number): TileDrawCommand | null => {
        const direct = nonWaterByCell.get(`${x},${y}`);
        if (direct) {
          return direct;
        }
        for (let radius = 1; radius <= 6; radius += 1) {
          for (let dy = -radius; dy <= radius; dy += 1) {
            for (let dx = -radius; dx <= radius; dx += 1) {
              if (Math.abs(dx) + Math.abs(dy) > radius) {
                continue;
              }
              const nearby = nonWaterByCell.get(`${x + dx},${y + dy}`);
              if (nearby) {
                return nearby;
              }
            }
          }
        }
        return dirtTemplates[0] ?? null;
      };

      return withoutOldPortMapchips.filter(command =>
        command.drawGroup !== "nature-object" || !forcedDirtCells.has(`${command.cellX},${command.cellY}`)
      ).map((command) => {
        if (command.selection.assetFolder === "nature" || command.drawGroup === "nature-object") {
          return command;
        }
        const key = `${command.cellX},${command.cellY}`;
        if (!forcedDirtCells.has(key)) {
          return command;
        }
        const template = findTemplateForCell(command.cellX, command.cellY);
        if (!template) {
          return command;
        }
        return {
          ...template,
          cellX: command.cellX,
          cellY: command.cellY,
          layer: command.layer,
          drawGroup: command.drawGroup,
        };
      });
    })();

    const sortedCommands = [...forcedPortDirtCommands].sort((left, right) => {
      const depthA = left.cellX + left.cellY;
      const depthB = right.cellX + right.cellY;
      if (depthA !== depthB) {
        return depthA - depthB;
      }
      if ((defaultTerrainMode || renderMode === "f1-terrain") && left.drawGroup !== right.drawGroup) {
        const drawGroupOrder = left.drawGroup === "base-terrain" ? 0 : 1;
        const otherDrawGroupOrder = right.drawGroup === "base-terrain" ? 0 : 1;
        if (drawGroupOrder !== otherDrawGroupOrder) {
          return drawGroupOrder - otherDrawGroupOrder;
        }
      }
      if (left.layer !== right.layer) {
        return left.layer - right.layer;
      }
      if (left.cellY !== right.cellY) {
        return left.cellY - right.cellY;
      }
      return left.cellX - right.cellX;
    });

    const drawCommands = forceVisibleNatureMode
      ? [
          ...sortedCommands.filter((command) => command.drawGroup !== "nature-object"),
          ...sortedCommands.filter((command) => command.drawGroup === "nature-object"),
        ]
      : sortedCommands;

    const renderedCellKeys = new Set<string>();
    const waterCellKeys = new Set<string>();

    const zoom = camera.zoom;
    const logicalTileWidth = TILE_WIDTH * logicalFootprintScale;
    const logicalTileHeight = TILE_HEIGHT * logicalFootprintScale;
    const shouldDrawTextures = overlayMode !== "diamond-only";
    const shouldDrawDiamonds = showCellGrid;

    // Water tiles and shore pieces are world geometry, unlike the viewport ocean.
    const waterTerrainCells = new Map<string, { x: number; y: number; water: boolean }>();
    for (const c of forcedPortDirtCommands) {
      if (c.drawGroup === "nature-object") continue;
      waterTerrainCells.set(`${c.cellX},${c.cellY}`, { x: c.cellX, y: c.cellY, water: isWaterLikeCommand(c) });
    }
    if (shouldDrawTextures) drawWaterSurface(context, pipeline.waterAssets,
      recoverWaterTiles([...waterTerrainCells.values()]), camera, width, height);

    const drawCellGridOverlay = () => {
      if (!shouldDrawDiamonds) {
        return;
      }
      const seen = new Set<string>();
      context.strokeStyle = "rgba(0, 0, 0, 0.92)";
      context.lineWidth = Math.max(0.9, zoom * 0.18);
      for (const command of forcedPortDirtCommands) {
        const key = `${command.cellX},${command.cellY}`;
        if (seen.has(key)) {
          continue;
        }
        seen.add(key);
        const isoCenter = worldToIso(command.cellX, command.cellY, zoom, camera.offsetX, camera.offsetY);
        drawIsoDiamond(context, isoCenter.x, isoCenter.y, (logicalTileWidth * zoom) / 2, (logicalTileHeight * zoom) / 2);
        if (showTileCenters) {
          context.fillStyle = "rgba(56, 189, 248, 0.9)";
          context.fillRect(Math.round(isoCenter.x) - 1, Math.round(isoCenter.y) - 1, 3, 3);
        }
      }
    };

    for (const command of drawCommands) {
      const cellKey = `${command.cellX},${command.cellY}`;
      renderedCellKeys.add(cellKey);
      if (isWaterLikeCommand(command)) {
        waterCellKeys.add(cellKey);
      }

      const iso = worldToIso(command.cellX, command.cellY, zoom, camera.offsetX, camera.offsetY);
      const sprite = command.selection;
      const imageAsset = pipeline.imageCache.get(sprite.sourceFilename);
      if (!imageAsset) {
        continue;
      }

      const image = imageAsset.image;
      if (!image) {
        continue;
      }

      const natureImageIsLarge =
        command.drawGroup === "nature-object" &&
        (sprite.srcW >= TILE_WIDTH || sprite.srcH >= TILE_HEIGHT * 2 || image.width >= TILE_WIDTH || image.height >= TILE_HEIGHT * 2);
      const forceVisibleNature =
        command.drawGroup === "nature-object" &&
        (forceVisibleNatureMode || (forceVisibleLargeNatureMode && natureImageIsLarge));
      const sourceRectX = forceVisibleNature ? 0 : sprite.srcX;
      const sourceRectY = forceVisibleNature ? 0 : sprite.srcY;
      const sourceRectW = forceVisibleNature ? image.width : sprite.srcW;
      const sourceRectH = forceVisibleNature ? image.height : sprite.srcH;

      // Guard against malformed source rectangles that can throw DOMException in drawImage.
      if (
        !Number.isFinite(sourceRectX) ||
        !Number.isFinite(sourceRectY) ||
        !Number.isFinite(sourceRectW) ||
        !Number.isFinite(sourceRectH) ||
        sourceRectW <= 0 ||
        sourceRectH <= 0 ||
        sourceRectX < 0 ||
        sourceRectY < 0 ||
        sourceRectX >= image.width ||
        sourceRectY >= image.height
      ) {
        continue;
      }

      const clippedSrcW = Math.min(sourceRectW, image.width - sourceRectX);
      const clippedSrcH = Math.min(sourceRectH, image.height - sourceRectY);
      if (clippedSrcW <= 0 || clippedSrcH <= 0) {
        continue;
      }

      const placement = forceVisibleNature
        ? computeForcedNaturePlacement({
            command,
            isoX: iso.x,
            isoY: iso.y,
            zoom,
            sourceW: clippedSrcW,
            sourceH: clippedSrcH,
          })
        : computeTexturePlacement({
            renderMode,
            terrainAlignmentMode,
            command,
            isoX: iso.x,
            isoY: iso.y,
            zoom,
            clippedSrcW,
            clippedSrcH,
          });

      const drawX = placement.drawX;
      const drawY = placement.drawY;
      const drawW = placement.drawW;
      const drawH = placement.drawH;

      if (drawX > width || drawY > height || drawX + drawW < 0 || drawY + drawH < 0) {
        continue;
      }

      try {
        if (placement.clipToDiamond) {
          context.save();
          context.beginPath();
          context.moveTo(iso.x, iso.y - (logicalTileHeight * zoom) / 2);
          context.lineTo(iso.x + (logicalTileWidth * zoom) / 2, iso.y);
          context.lineTo(iso.x, iso.y + (logicalTileHeight * zoom) / 2);
          context.lineTo(iso.x - (logicalTileWidth * zoom) / 2, iso.y);
          context.closePath();
          context.clip();
        }

        if (shouldDrawTextures) {
          const previousAlpha = context.globalAlpha;
          if (forceVisibleNature) {
            context.globalAlpha = 1;
          }

          if (isWaterLikeCommand(command)) {
            // Already drawn using recovered water OPT/SEB and shore adjacency.
          } else {
            context.drawImage(
              image,
              sourceRectX,
              sourceRectY,
              clippedSrcW,
              clippedSrcH,
              drawX,
              drawY,
              drawW,
              drawH,
            );
          }

          if (showTextureBounds || forceVisibleNature || (showNatureCandidateBounds && command.drawGroup === "nature-object")) {
            context.strokeStyle = forceVisibleNature || command.drawGroup === "nature-object" ? "rgba(255, 31, 92, 0.95)" : "rgba(248, 113, 113, 0.72)";
            context.lineWidth = 1;
            context.strokeRect(drawX + 0.5, drawY + 0.5, drawW - 1, drawH - 1);
            if (showNatureCandidateBounds && command.drawGroup === "nature-object") {
              context.fillStyle = "rgba(255,255,255,0.95)";
              context.font = "10px monospace";
              context.fillText(`${command.mapChipId}:${command.mapChipFrame}`, drawX + 2, Math.max(10, drawY - 3));
            }
          }

          context.globalAlpha = previousAlpha;
        }

        if (placement.clipToDiamond) {
          context.restore();
        }
      } catch {
        if (placement.clipToDiamond) {
          context.restore();
        }
        continue;
      }
    }

    if (reclaimedTintKeys.size > 0) {
      const halfW = (logicalTileWidth * zoom) / 2 + 0.9;
      const halfH = (logicalTileHeight * zoom) / 2 + 0.9;
      context.fillStyle = "rgba(34, 197, 94, 0.22)";
      context.strokeStyle = "rgba(21, 128, 61, 0.48)";
      context.lineWidth = Math.max(0.8, zoom * 0.18);
      for (const key of reclaimedTintKeys) {
        const [xText, yText] = key.split(",");
        const x = Number(xText);
        const y = Number(yText);
        if (!Number.isFinite(x) || !Number.isFinite(y)) {
          continue;
        }
        const isoCenter = worldToIso(x, y, zoom, camera.offsetX, camera.offsetY);
        drawIsoDiamond(context, isoCenter.x, isoCenter.y, halfW, halfH);
        context.fill();
        context.stroke();
      }
    }

    drawCellGridOverlay();

    if (showOnePieceFacilities) {
      drawOnePieceFacilityOverlays(context, pipeline.facilityBuildingCache, camera, width, height, !publicMode, !publicMode, visibleOnePieceFacilities);
    }
    if (showNoOffsetFacilityDuplicates) {
      drawOnePieceFacilityNoOffsetDuplicates(context, pipeline.facilityBuildingCache, camera, width, height, !publicMode, visibleOnePieceFacilities);
    }
    if (showCorrectedRankingBoardPlacementTest) {
      // Debug-only isolated facility placement overlay.
      drawFacilityPlacementTestOverlays(context, pipeline.facilityBuildingCache, camera, width, height);
    }
    if (showRawRankingBoardPlacementTest) {
      drawRawRankingBoardPlacementTestOverlays(context, pipeline.facilityBuildingCache, camera, width, height, OVERLAY_STYLES.rawData);
    }
    if (showLegendaryCaveRawPlacementTest) {
      drawRawLegendaryCavePlacementTestOverlays(context, pipeline.facilityBuildingCache, camera, width, height, OVERLAY_STYLES.legendaryRaw);
    }
    if (!publicMode) {
      drawOverlayLegend(context, camera.zoom, width);
    }
    if (showPortAssemblies) {
      drawPortAssemblies(context, pipeline.portAssetCache, camera, width, height, portGateLayout, showPortBridgePieces, true);
    }

    if (dimUncoveredConquestAreas) {
      // Render uncovered cells as near-solid grayscale so they read as truly inactive.
      const halfW = (logicalTileWidth * zoom) / 2 + 1.05;
      const halfH = (logicalTileHeight * zoom) / 2 + 1.05;
      context.fillStyle = "rgb(38, 38, 38)";
      for (const key of renderedCellKeys) {
        if (waterCellKeys.has(key) || conquestCoveredCellKeys.has(key)) {
          continue;
        }
        const [xText, yText] = key.split(",");
        const x = Number(xText);
        const y = Number(yText);
        if (!Number.isFinite(x) || !Number.isFinite(y)) {
          continue;
        }
        const isoCenter = worldToIso(x, y, zoom, camera.offsetX, camera.offsetY);
        context.beginPath();
        context.moveTo(isoCenter.x, isoCenter.y - halfH);
        context.lineTo(isoCenter.x + halfW, isoCenter.y);
        context.lineTo(isoCenter.x, isoCenter.y + halfH);
        context.lineTo(isoCenter.x - halfW, isoCenter.y);
        context.closePath();
        context.fill();
      }
    }

    if (spawnOverlayCells.length > 0) {
      const halfW = (logicalTileWidth * zoom) / 2;
      const halfH = (logicalTileHeight * zoom) / 2;
      for (const cell of spawnOverlayCells) {
        const isoCenter = worldToIso(cell.x, cell.y, zoom, camera.offsetX, camera.offsetY);
        const style = cell.overlays[(cell.x + cell.y) % cell.overlays.length] ?? cell.overlays[0];
        drawSpawnStripeOverlay(context, isoCenter.x, isoCenter.y, halfW, halfH, style.color, style.patternIndex, zoom);
      }
    }

    drawEditorLayer?.(context, camera, width, height);

    if (cellHighlightOverlays.length > 0) {
      const halfW = (logicalTileWidth * zoom) / 2;
      const halfH = (logicalTileHeight * zoom) / 2;
      for (const overlay of cellHighlightOverlays) {
        if (!Number.isFinite(overlay.x) || !Number.isFinite(overlay.y)) {
          continue;
        }
        const isoCenter = worldToIso(overlay.x, overlay.y, zoom, camera.offsetX, camera.offsetY);
        context.beginPath();
        context.moveTo(isoCenter.x, isoCenter.y - halfH);
        context.lineTo(isoCenter.x + halfW, isoCenter.y);
        context.lineTo(isoCenter.x, isoCenter.y + halfH);
        context.lineTo(isoCenter.x - halfW, isoCenter.y);
        context.closePath();
        if (overlay.fillColor) {
          context.fillStyle = overlay.fillColor;
          context.fill();
        }
        if (overlay.strokeColor) {
          context.strokeStyle = overlay.strokeColor;
          context.lineWidth = Math.max(0.8, overlay.lineWidth ?? 1.05);
          context.stroke();
        }
      }
    }

    if (showLevelOverlay && levelOverlayData.edges.length > 0) {
      const halfW = (logicalTileWidth * zoom) / 2;
      const halfH = (logicalTileHeight * zoom) / 2;

      const drawBorders = (strokeStyle: string, lineWidth: number) => {
        context.strokeStyle = strokeStyle;
        context.lineWidth = lineWidth;
        for (const edge of levelOverlayData.edges) {
          const iso = worldToIso(edge.x, edge.y, zoom, camera.offsetX, camera.offsetY);
          if (edge.top) {
            context.beginPath();
            context.moveTo(iso.x - halfW, iso.y);
            context.lineTo(iso.x, iso.y - halfH);
            context.lineTo(iso.x + halfW, iso.y);
            context.stroke();
          }
          if (edge.right) {
            context.beginPath();
            context.moveTo(iso.x, iso.y - halfH);
            context.lineTo(iso.x + halfW, iso.y);
            context.lineTo(iso.x, iso.y + halfH);
            context.stroke();
          }
          if (edge.bottom) {
            context.beginPath();
            context.moveTo(iso.x - halfW, iso.y);
            context.lineTo(iso.x, iso.y + halfH);
            context.lineTo(iso.x + halfW, iso.y);
            context.stroke();
          }
          if (edge.left) {
            context.beginPath();
            context.moveTo(iso.x, iso.y - halfH);
            context.lineTo(iso.x - halfW, iso.y);
            context.lineTo(iso.x, iso.y + halfH);
            context.stroke();
          }
        }
      };

      drawBorders("rgba(136, 19, 55, 0.96)", 2.9);
      drawBorders("rgba(251, 113, 133, 0.95)", 1.2);

      const fontSize = Math.max(12, Math.min(21, 12 + zoom * 2.4));
      context.font = `600 ${fontSize}px Inter, system-ui, sans-serif`;
      context.textAlign = "center";
      context.textBaseline = "middle";
      for (const label of levelOverlayData.labels) {
        const iso = worldToIso(label.x, label.y, zoom, camera.offsetX, camera.offsetY);
        const text = String(label.level);
        const textWidth = context.measureText(text).width;
        const boxW = Math.ceil(textWidth + 18);
        const boxH = Math.ceil(fontSize + 10);
        const left = Math.round(iso.x - boxW / 2);
        const top = Math.round(iso.y - boxH / 2);

        context.fillStyle = "rgba(76, 5, 25, 0.86)";
        context.fillRect(left, top, boxW, boxH);
        context.strokeStyle = "rgba(251, 113, 133, 0.98)";
        context.lineWidth = 1.35;
        context.strokeRect(left + 0.5, top + 0.5, boxW - 1, boxH - 1);
        context.fillStyle = "rgba(255, 241, 242, 0.99)";
        context.fillText(text, Math.round(iso.x), Math.round(iso.y));
      }
    }

    if (!publicMode && hoveredCell) {
      const isoCenter = worldToIso(hoveredCell.x, hoveredCell.y, zoom, camera.offsetX, camera.offsetY);
      context.strokeStyle = "rgba(255, 255, 255, 0.8)";
      context.lineWidth = 1;
      drawIsoDiamond(context, isoCenter.x, isoCenter.y, (logicalTileWidth * zoom) / 2, (logicalTileHeight * zoom) / 2);
    }

    if (!publicMode && selectedCell) {
      const isoCenter = worldToIso(selectedCell.x, selectedCell.y, zoom, camera.offsetX, camera.offsetY);
      context.strokeStyle = "rgba(250, 204, 21, 0.95)";
      context.lineWidth = 2;
      drawIsoDiamond(context, isoCenter.x, isoCenter.y, (logicalTileWidth * zoom) / 2, (logicalTileHeight * zoom) / 2);
    }

    if (showNatureDebugOverlay && natureOverlayCellKeys.size > 0) {
      context.fillStyle = "rgba(255, 31, 92, 0.95)";
      for (const key of natureOverlayCellKeys) {
        const [xText, yText] = key.split(",");
        const cellX = Number(xText);
        const cellY = Number(yText);
        if (!Number.isFinite(cellX) || !Number.isFinite(cellY)) {
          continue;
        }
        const isoCenter = worldToIso(cellX, cellY, zoom, camera.offsetX, camera.offsetY);
        context.beginPath();
        context.arc(Math.round(isoCenter.x), Math.round(isoCenter.y - 4 * zoom), Math.max(2, 2.5 * zoom), 0, Math.PI * 2);
        context.fill();
      }
    }
  }, [
    resizeVersion,
    drawEditorLayer,
    landOverrideCells,
    reclaimedTintKeys,
    visibleOnePieceFacilities,
    activeCommands,
    camera,
    defaultTerrainMode,
    forceVisibleNatureMode,
    forceVisibleLargeNatureMode,
    hoveredCell,
    natureOverlayCellKeys,
    overlayMode,
    pipeline,
    logicalFootprintScale,
    renderMode,
    selectedCell,
    showOnePieceFacilities,
    showNoOffsetFacilityDuplicates,
    showPortAssemblies,
    showPortBridgePieces,
    portGateLayout,
    showCellGrid,
    cellHighlightOverlays,
    conquestCoveredCellKeys,
    dimUncoveredConquestAreas,
    spawnOverlayCells,
    showLevelOverlay,
    levelOverlayData,
    showNatureDebugOverlay,
    showNatureCandidateBounds,
    showTextureBounds,
    showTileCenters,
    terrainAlignmentMode,
  ]);

  useEffect(() => {
    if (!onTerrainReady || !pipeline || !activeCommands.length) return;
    const land = new Set(activeCommands.filter(c => c.drawGroup === 'base-terrain' && !isWaterLikeCommand(c)).map(c => `${c.cellX},${c.cellY}`));
    for (const [x,y] of nativeMapGround.facilityGround) land.add(`${x},${y}`);
    for (const port of nativeMapGround.ports) for (const [x,y] of port.ground) land.add(`${x},${y}`);
    onTerrainReady(land);
  }, [activeCommands, pipeline, onTerrainReady]);

  const hoveredCellInfo = useMemo(() => {
    if (!hoveredCell || !pipeline) {
      return null;
    }
    const parsedCell = getParsedCellAt(pipeline.parsedMap, hoveredCell.x, hoveredCell.y);
    const top = activeCommands.find((entry) => entry.cellX === hoveredCell.x && entry.cellY === hoveredCell.y);
    return {
      parsedCell,
      top,
    };
  }, [activeCommands, hoveredCell, pipeline]);

  const selectedCellInfo = useMemo(() => {
    if (!selectedCell || !pipeline) {
      return null;
    }
    const parsedCell = getParsedCellAt(pipeline.parsedMap, selectedCell.x, selectedCell.y);
    const top = activeCommands.find((entry) => entry.cellX === selectedCell.x && entry.cellY === selectedCell.y);
    return {
      parsedCell,
      top,
    };
  }, [activeCommands, pipeline, selectedCell]);

  const selectedCellCommandCount = useMemo(() => {
    if (!selectedCell || !pipeline) {
      return 0;
    }
    return activeCommands.filter((entry) => entry.cellX === selectedCell.x && entry.cellY === selectedCell.y).length;
  }, [activeCommands, pipeline, selectedCell]);

  const selectedCellCommands = useMemo(() => {
    if (!selectedCell || !pipeline) {
      return [] as TileDrawCommand[];
    }
    return activeCommands.filter((entry) => entry.cellX === selectedCell.x && entry.cellY === selectedCell.y);
  }, [activeCommands, pipeline, selectedCell]);

  const selectedCellBaseCommandCount = useMemo(() => {
    return selectedCellCommands.filter((entry) => entry.drawGroup === "base-terrain").length;
  }, [selectedCellCommands]);

  const selectedCellNatureCommandCount = useMemo(() => {
    return selectedCellCommands.filter((entry) => entry.drawGroup === "nature-object").length;
  }, [selectedCellCommands]);

  const selectedCellNatureCommand = useMemo(() => {
    return selectedCellCommands.find((entry) => entry.drawGroup === "nature-object") ?? null;
  }, [selectedCellCommands]);

  const selectedCellNatureCommandDetails = useMemo(() => {
    if (!selectedCell || !pipeline) {
      return [];
    }

    return selectedCellCommands
      .filter((entry) => entry.drawGroup === "nature-object")
      .map((command) => {
        const image = pipeline.imageCache.get(command.selection.sourceFilename)?.image;
        const iso = worldToIso(command.cellX, command.cellY, camera.zoom, camera.offsetX, camera.offsetY);
        const sourceRectX = command.selection.srcX;
        const sourceRectY = command.selection.srcY;
        const sourceRectW = image ? Math.min(command.selection.srcW, image.width - sourceRectX) : command.selection.srcW;
        const sourceRectH = image ? Math.min(command.selection.srcH, image.height - sourceRectY) : command.selection.srcH;
        const placement = sourceRectW > 0 && sourceRectH > 0
          ? computeTexturePlacement({
              renderMode,
              terrainAlignmentMode,
              command,
              isoX: iso.x,
              isoY: iso.y,
              zoom: camera.zoom,
              clippedSrcW: sourceRectW,
              clippedSrcH: sourceRectH,
            })
          : null;
        const row = pipeline.lookups.terrainById.get(command.mapChipId);
        return {
          command,
          row,
          imageWidth: image?.width ?? null,
          imageHeight: image?.height ?? null,
          sourceRectX,
          sourceRectY,
          sourceRectW,
          sourceRectH,
          drawX: placement?.drawX ?? null,
          drawY: placement?.drawY ?? null,
          drawW: placement?.drawW ?? null,
          drawH: placement?.drawH ?? null,
        };
      });
  }, [camera.offsetX, camera.offsetY, camera.zoom, pipeline, renderMode, selectedCell, selectedCellCommands, terrainAlignmentMode]);

  const clickedCellCandidateInterpretations = useMemo(() => {
    if (!selectedCellInfo?.parsedCell || !pipeline) {
      return null;
    }

    const f2 = selectedCellInfo.parsedCell.fields.f2;
    const mapChipById = pipeline.lookups.mapChipById.get(f2);
    const terrainById = pipeline.lookups.terrainById.get(f2);
    const terrainByCategory = pipeline.lookups.terrainByCategory.get(f2) ?? [];
    const chipsByLayer = pipeline.lookups.mapChipByLayer.get(f2) ?? [];
    const replacementByCategory = (pipeline.lookups.mapChipByCategory.get(f2) ?? []).filter((row) =>
      isPlayerMadeSurface(row.name, row.res === 20 ? pipeline.lookups.natureImageById.get(row.img) ?? "" : pipeline.lookups.imageById.get(row.img) ?? ""),
    );

    return {
      mapChipById,
      terrainById,
      terrainByCategory,
      chipsByLayer,
      replacementByCategory,
    };
  }, [pipeline, selectedCellInfo]);

  const clickedCellTerrainSheetDebug = useMemo(() => {
    if (!selectedCellInfo?.parsedCell || !pipeline) {
      return null;
    }

    const parsedCell = selectedCellInfo.parsedCell;
    const terrainType = parsedCell.fields.f1;
    const allRows = pipeline.lookups.terrainByType.get(terrainType) ?? [];
    const validRows = allRows.filter((row) => !isPlayerMadeSurface(row.name, row.res === 20 ? pipeline.lookups.natureImageById.get(row.img) ?? "" : pipeline.lookups.imageById.get(row.img) ?? ""));
    const baseRows = validRows.filter((row) => row.category === 0);
    const natureRows = validRows.filter((row) => row.res === 20);
    const specialRows = validRows.filter((row) => row.category === 2);
    const baseCommand = selectedCellCommands.find((command) => command.drawGroup === "base-terrain") ?? null;
    const natureCommand = selectedCellCommands.find((command) => command.drawGroup === "nature-object") ?? null;
    const selectedNatureByFields = selectNatureRowByDirectFields(natureRows, parsedCell.fields);
    const selectedNatureRowsByFields = selectNatureRowsByDiscoveredFields(natureRows, parsedCell.fields);
    const directFieldMatrix = buildDirectFieldMatchMatrix(validRows, parsedCell.fields);
    const directNatureFieldMatrix = buildDirectFieldMatchMatrix(natureRows, parsedCell.fields);
    const candidateMatches = baseRows.map((row) => ({
      row,
      match: compareTerrainRowAgainstMapFields(row, parsedCell.fields),
    }));
    const candidateNatureMatches = natureRows.map((row) => ({
      row,
      match: compareTerrainRowAgainstMapFields(row, parsedCell.fields),
    }));

    return {
      terrainType,
      allRowsCount: allRows.length,
      matchingRowsCount: validRows.length,
      baseRowsCount: baseRows.length,
      baseRows,
      candidateMatches,
      selectedBaseRowId: baseCommand?.mapChipId ?? null,
      selectedBaseImg: baseCommand?.mapChipImgId ?? null,
      selectedBaseFilename: baseCommand?.resolvedImgFilename ?? "",
      selectedReason: baseCommand?.selectedBaseReason ?? "",
      selectedCategory: baseCommand?.mapChipCategory ?? null,
      natureRowsCount: natureRows.length,
      natureRows,
      candidateNatureMatches,
      directFieldMatrix,
      directNatureFieldMatrix,
      naturePlacementField: selectedNatureByFields.field,
      natureSelectionMethod: selectedNatureByFields.method,
      selectedNatureRowsByFields,
      selectedNatureRowId: natureCommand?.mapChipId ?? null,
      selectedNatureImg: natureCommand?.mapChipImgId ?? null,
      selectedNatureFilename: natureCommand?.resolvedImgFilename ?? "",
      selectedNatureId: natureCommand ? natureRows.find((row) => row.id === natureCommand.mapChipId)?.natureId ?? null : null,
      selectedNatureGroupId: natureCommand ? natureRows.find((row) => row.id === natureCommand.mapChipId)?.natureGroupId ?? null : null,
      selectedNatureReason: selectedNatureByFields.reason,
      natureWasDrawn: Boolean(natureCommand),
      specialRowsCount: specialRows.length,
    };
  }, [pipeline, selectedCellCommands, selectedCellInfo]);

  const selectedCellPreview = useMemo<ClickedCellPreview | null>(() => {
    if (!selectedCellInfo?.top || !pipeline) {
      return null;
    }

    const top = selectedCellInfo.top;
    const imageAsset = pipeline.imageCache.get(top.selection.sourceFilename);
    if (!imageAsset || !top.loadedPngPath) {
      return null;
    }

    const image = imageAsset.image;

    const hasExactRect = top.selection.srcW > 0 && top.selection.srcH > 0;

    if (!hasExactRect) {
      const previewScale = Math.max(1, Math.floor(120 / Math.max(1, image.width, image.height)));
      return {
        mode: "fallback-full-png",
        imageWidth: image.width,
        imageHeight: image.height,
        previewWidth: Math.max(1, image.width * previewScale),
        previewHeight: Math.max(1, image.height * previewScale),
        bgPosX: 0,
        bgPosY: 0,
        bgSizeW: image.width * previewScale,
        bgSizeH: image.height * previewScale,
        path: top.loadedPngPath,
      };
    }

    const previewScale = Math.max(1, Math.floor(120 / Math.max(1, top.selection.srcW, top.selection.srcH)));
    return {
      mode: "exact-crop",
      imageWidth: image.width,
      imageHeight: image.height,
      previewWidth: Math.max(1, top.selection.srcW * previewScale),
      previewHeight: Math.max(1, top.selection.srcH * previewScale),
      bgPosX: -top.selection.srcX * previewScale,
      bgPosY: -top.selection.srcY * previewScale,
      bgSizeW: image.width * previewScale,
      bgSizeH: image.height * previewScale,
      path: top.loadedPngPath,
    };
  }, [pipeline, selectedCellInfo]);

  const clickedCellGeometry = useMemo(() => {
    if (!selectedCellInfo?.top || !pipeline) {
      return null;
    }

    const top = selectedCellInfo.top;
    const imageAsset = pipeline.imageCache.get(top.selection.sourceFilename);
    const image = imageAsset?.image;
    if (!image) {
      return null;
    }

    const iso = worldToIso(top.cellX, top.cellY, camera.zoom, camera.offsetX, camera.offsetY);
    const clippedSrcW = Math.min(top.selection.srcW, image.width - top.selection.srcX);
    const clippedSrcH = Math.min(top.selection.srcH, image.height - top.selection.srcY);
    if (clippedSrcW <= 0 || clippedSrcH <= 0) {
      return null;
    }

    const placement = computeTexturePlacement({
      renderMode,
      terrainAlignmentMode,
      command: top,
      isoX: iso.x,
      isoY: iso.y,
      zoom: camera.zoom,
      clippedSrcW,
      clippedSrcH,
    });

    return {
      iso,
      placement,
      logicalBounds: {
        left: iso.x - (TILE_WIDTH * logicalFootprintScale * camera.zoom) / 2,
        top: iso.y - (TILE_HEIGHT * logicalFootprintScale * camera.zoom) / 2,
        width: TILE_WIDTH * logicalFootprintScale * camera.zoom,
        height: TILE_HEIGHT * logicalFootprintScale * camera.zoom,
      },
    };
  }, [camera.offsetX, camera.offsetY, camera.zoom, logicalFootprintScale, pipeline, renderMode, selectedCellInfo, terrainAlignmentMode]);

  const selectedCellNatureGeometry = useMemo(() => {
    if (!selectedCellNatureCommand || !pipeline) {
      return null;
    }

    const natureCommand = selectedCellNatureCommand;
    const imageAsset = pipeline.imageCache.get(natureCommand.selection.sourceFilename);
    const image = imageAsset?.image;
    if (!image) {
      return null;
    }

    const iso = worldToIso(natureCommand.cellX, natureCommand.cellY, camera.zoom, camera.offsetX, camera.offsetY);
    const forceVisibleNature = forceVisibleNatureMode;
    const sourceRectX = forceVisibleNature ? 0 : natureCommand.selection.srcX;
    const sourceRectY = forceVisibleNature ? 0 : natureCommand.selection.srcY;
    const sourceRectW = forceVisibleNature ? image.width : natureCommand.selection.srcW;
    const sourceRectH = forceVisibleNature ? image.height : natureCommand.selection.srcH;
    const clippedSrcW = Math.min(sourceRectW, image.width - sourceRectX);
    const clippedSrcH = Math.min(sourceRectH, image.height - sourceRectY);
    if (clippedSrcW <= 0 || clippedSrcH <= 0) {
      return null;
    }

    const placement = forceVisibleNature
      ? computeForcedNaturePlacement({
          command: natureCommand,
          isoX: iso.x,
          isoY: iso.y,
          zoom: camera.zoom,
          sourceW: clippedSrcW,
          sourceH: clippedSrcH,
        })
      : computeTexturePlacement({
          renderMode,
          terrainAlignmentMode,
          command: natureCommand,
          isoX: iso.x,
          isoY: iso.y,
          zoom: camera.zoom,
          clippedSrcW,
          clippedSrcH,
        });

    return {
      sourceRectX,
      sourceRectY,
      sourceRectW: clippedSrcW,
      sourceRectH: clippedSrcH,
      drawX: placement.drawX,
      drawY: placement.drawY,
      drawW: placement.drawW,
      drawH: placement.drawH,
      opacity: 1,
      layerOrder: `drawGroup=${natureCommand.drawGroup}, layer=${natureCommand.layer}, forcedTop=${forceVisibleNature ? "yes" : "no"}`,
    };
  }, [
    camera.offsetX,
    camera.offsetY,
    camera.zoom,
    forceVisibleNatureMode,
    pipeline,
    renderMode,
    selectedCellNatureCommand,
    terrainAlignmentMode,
  ]);

  function hitTestPortGatePiece(clientX: number, clientY: number): PortGatePiece["assetKey"] | null {
    if (publicMode || !showPortAssemblies || !canvasRef.current) {
      return null;
    }

    return hitTestPortGatePieceAtClient(clientX, clientY, canvasRef.current, camera, portGateLayout, pipeline?.portAssetCache);
  }

  function pickEditorCell(clientX:number,clientY:number) {
    const canvas=canvasRef.current;if(!canvas)return null;
    const ground=pointerToWorldFromClient(clientX,clientY,canvas,camera,editorCellPicking);
    const elevation=ground?editorCellElevation?.(ground)??0:0;
    return elevation?pointerToWorldFromClient(clientX,clientY+elevation*camera.zoom,canvas,camera,editorCellPicking):ground;
  }

  function onPointerDown(event: React.PointerEvent<HTMLCanvasElement>) {
    pointerStartRef.current = {x:event.clientX,y:event.clientY};
    suppressNextCanvasClickRef.current = false;
    if(onEditorLine&&event.pointerType==='mouse'&&event.button===0&&!event.shiftKey) {
      const cell=pointerToWorldFromClient(event.clientX,event.clientY,event.currentTarget,camera,true);
      if(cell&&(!editorLineAllowed||editorLineAllowed(cell))){editorLineRef.current={id:event.pointerId,start:cell,end:cell};event.currentTarget.setPointerCapture(event.pointerId);onEditorLine(cell,cell,'preview');return;}
    }
    if (event.pointerType === "touch") {
      touchMovedRef.current = false;
      const rect = event.currentTarget.getBoundingClientRect();
      const localX = event.clientX - rect.left;
      const localY = event.clientY - rect.top;
      activeTouchPointsRef.current.set(event.pointerId, { x: localX, y: localY });
      event.currentTarget.setPointerCapture(event.pointerId);

      if (activeTouchPointsRef.current.size >= 2) {
        const points = [...activeTouchPointsRef.current.values()];
        const first = points[0];
        const second = points[1];
        const centerX = (first.x + second.x) / 2;
        const centerY = (first.y + second.y) / 2;
        const startDistance = Math.hypot(second.x - first.x, second.y - first.y);

        const anchorWorld = pointerToWorldFloatFromClient(
          centerX + rect.left,
          centerY + rect.top,
          event.currentTarget,
          camera,
        );

        if (anchorWorld && startDistance > 0) {
          pinchStateRef.current = {
            startDistance,
            startZoom: camera.zoom,
            anchorWorldX: anchorWorld.x,
            anchorWorldY: anchorWorld.y,
          };
        }
      }

      setDragging(false);
      return;
    }

    const selectedPortPiece = hitTestPortGatePiece(event.clientX, event.clientY);
    if (selectedPortPiece) {
      const pointerWorld = pointerToWorldFloatFromClient(event.clientX, event.clientY, event.currentTarget, camera);
      if (pointerWorld) {
        event.currentTarget.setPointerCapture(event.pointerId);
        setPortGateDrag({
          assetKey: selectedPortPiece,
          startPointerWorld: pointerWorld,
          startOffset: portGateLayout[selectedPortPiece] ?? DEFAULT_PORT_GATE_LAYOUT[selectedPortPiece],
          moved: false,
        });
        setDragging(false);
        return;
      }
    }

    event.currentTarget.setPointerCapture(event.pointerId);
    setDragging(true);
  }

  function onPointerUp(event: React.PointerEvent<HTMLCanvasElement>) {
    const line=editorLineRef.current;
    if(line&&line.id===event.pointerId) {
      editorLineRef.current=null;pointerStartRef.current=null;suppressNextCanvasClickRef.current=true;
      if(event.currentTarget.hasPointerCapture(event.pointerId))event.currentTarget.releasePointerCapture(event.pointerId);
      const end=pointerToWorldFromClient(event.clientX,event.clientY,event.currentTarget,camera,true)??line.end;
      onEditorLine?.(line.start,end,event.type==='pointercancel'?'cancel':'commit');return;
    }
    if(pointerStartRef.current && Math.hypot(event.clientX-pointerStartRef.current.x,event.clientY-pointerStartRef.current.y)>6) suppressNextCanvasClickRef.current=true;
    pointerStartRef.current=null;
    if (event.pointerType === "touch") {
      activeTouchPointsRef.current.delete(event.pointerId);
      if (activeTouchPointsRef.current.size < 2) {
        pinchStateRef.current = null;
      }
      if (touchMovedRef.current) {
        suppressNextCanvasClickRef.current = true;
      }
      touchMovedRef.current = false;
    }

    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    if (portGateDrag?.moved) {
      suppressNextCanvasClickRef.current = true;
    }
    setPortGateDrag(null);
    setDragging(false);
  }

  function onPointerLeave() {
    if(editorLineRef.current)return;
    activeTouchPointsRef.current.clear();
    pinchStateRef.current = null;
    touchMovedRef.current = false;

    if (portGateDrag) {
      setHoveredCell(null);
      return;
    }
    setDragging(false);
    setHoveredCell(null);
    onCellHover?.(null);
  }

  function onPointerMove(event: React.PointerEvent<HTMLCanvasElement>) {
    const line=editorLineRef.current;
    if(line&&line.id===event.pointerId) {
      const cell=pointerToWorldFromClient(event.clientX,event.clientY,event.currentTarget,camera,true);
      if(cell){line.end=cell;onEditorLine?.(line.start,cell,'preview');}return;
    }
    if(pointerStartRef.current && Math.hypot(event.clientX-pointerStartRef.current.x,event.clientY-pointerStartRef.current.y)>6) suppressNextCanvasClickRef.current=true;
    if (event.pointerType === "touch" && activeTouchPointsRef.current.has(event.pointerId)) {
      const rect = event.currentTarget.getBoundingClientRect();
      const localX = event.clientX - rect.left;
      const localY = event.clientY - rect.top;
      const previousTouch = activeTouchPointsRef.current.get(event.pointerId) ?? null;
      activeTouchPointsRef.current.set(event.pointerId, { x: localX, y: localY });

      if (activeTouchPointsRef.current.size >= 2 && pinchStateRef.current) {
        const points = [...activeTouchPointsRef.current.values()];
        const first = points[0];
        const second = points[1];
        const centerX = (first.x + second.x) / 2;
        const centerY = (first.y + second.y) / 2;
        const currentDistance = Math.hypot(second.x - first.x, second.y - first.y);

        if (currentDistance > 0) {
          const pinch = pinchStateRef.current;
          const nextZoom = Math.max(0.08, Math.min(3.5, pinch.startZoom * (currentDistance / pinch.startDistance)));
          const anchorIso = worldToIso(pinch.anchorWorldX, pinch.anchorWorldY, nextZoom, 0, 0);

          setCamera((previous) => ({
            ...previous,
            zoom: nextZoom,
            offsetX: centerX - anchorIso.x,
            offsetY: centerY - anchorIso.y,
          }));
        }

        setDragging(false);
        return;
      }

      if (activeTouchPointsRef.current.size === 1 && previousTouch) {
        const dx = localX - previousTouch.x;
        const dy = localY - previousTouch.y;
        if (dx !== 0 || dy !== 0) {
          touchMovedRef.current = true;
          setCamera((previous) => ({
            ...previous,
            offsetX: previous.offsetX + dx,
            offsetY: previous.offsetY + dy,
          }));
        }
        setHoveredCell(null);
        onCellHover?.(null);
        return;
      }
    }

    if (portGateDrag) {
      const pointerWorld = pointerToWorldFloatFromClient(event.clientX, event.clientY, event.currentTarget, camera);
      if (!pointerWorld) {
        return;
      }

      const nextOffset = {
        dx: roundToQuarter(portGateDrag.startOffset.dx + pointerWorld.x - portGateDrag.startPointerWorld.x),
        dy: roundToQuarter(portGateDrag.startOffset.dy + pointerWorld.y - portGateDrag.startPointerWorld.y),
      };
      setPortGateLayout((previous) => ({
        ...previous,
        [portGateDrag.assetKey]: nextOffset,
      }));
      if (!portGateDrag.moved) {
        setPortGateDrag((previous) => (previous ? { ...previous, moved: true } : previous));
      }
      return;
    }

    if (dragging) {
      setCamera((previous) => ({
        ...previous,
        offsetX: previous.offsetX + event.movementX,
        offsetY: previous.offsetY + event.movementY,
      }));
      return;
    }

    if (!pipeline || !canvasRef.current) {
      return;
    }

    if (!allowMapInteraction) {
      setHoveredCell(null);
      return;
    }

    const nextHover = pickEditorCell(event.clientX,event.clientY);
    setHoveredCell(nextHover);
    onCellHover?.(nextHover);
  }

  function onCanvasClick(event: React.MouseEvent<HTMLCanvasElement>) {
    if (!allowMapInteraction) {
      return;
    }

    if (suppressNextCanvasClickRef.current) {
      suppressNextCanvasClickRef.current = false;
      return;
    }

    if (!pipeline || !canvasRef.current) {
      return;
    }
    const rect=canvasRef.current.getBoundingClientRect();
    if(onEditorObjectClick?.({x:event.clientX-rect.left,y:event.clientY-rect.top},camera))return;
    const clicked = pickEditorCell(event.clientX,event.clientY);
    setSelectedCell(clicked);
    if(clicked) onCellClick?.(clicked);
  }

  function onWheel(event: React.WheelEvent<HTMLCanvasElement>) {
    if (editorCellPicking || !event.shiftKey) {
      return;
    }

    event.preventDefault();
    event.stopPropagation();
    const zoomFactor = event.deltaY < 0 ? 1.12 : 0.9;
    setCamera((previous) => ({
      ...previous,
      zoom: Math.max(0.08, Math.min(3.5, previous.zoom * zoomFactor)),
    }));
  }

  function resetCamera() {
    if (!pipeline || !containerRef.current) {
      return;
    }
    const width = containerRef.current.clientWidth;
    const height = containerRef.current.clientHeight;
    setCamera(computeResetCamera(pipeline.parsedMap.width, pipeline.parsedMap.height, width, height, initialZoom));
  }

  return (
    <div className={fillHeight ? "flex h-full min-h-0 w-full flex-col" : publicMode ? "mx-auto max-w-[2400px]" : "mx-auto max-w-[1500px] p-4"}>
      {!publicMode && (
        <>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h1 className="text-lg font-semibold">Engine-Driven Isometric Render Test</h1>
            <div className="flex items-center gap-2 text-xs">
              <a href="/runtime-world-grid-test" className="rounded border border-border bg-card px-2 py-1 hover:bg-muted">
                Open top-view grid test
              </a>
              <a href="/runtime-world-render-test" className="rounded border border-border bg-card px-2 py-1 hover:bg-muted">
                Open isometric render test
              </a>
            </div>
          </div>
          <p className="text-sm text-muted-foreground">
            Chain: MapChip.img -&gt; img.inf -&gt; PNG and MapChip.seb -&gt; seb.inf -&gt; SEB frame metadata when available.
            Use Shift+wheel to zoom the map; regular wheel scrolls the page.
          </p>
        </>
      )}

      <div className={editorCellPicking ? 'hidden' : 'mt-3 flex flex-wrap items-center gap-2 text-xs'}>
        <button type="button" onClick={resetCamera} className="rounded border border-border bg-card px-3 py-1 hover:bg-muted">
          reset camera
        </button>
        {!publicMode && (
          <>
            <span className="rounded border border-border bg-card px-2 py-1">zoom {camera.zoom.toFixed(2)}x</span>
            <span className="rounded border border-border bg-card px-2 py-1">
              camera ({Math.round(camera.offsetX)}, {Math.round(camera.offsetY)})
            </span>
            <span className="rounded border border-border bg-card px-2 py-1">
              render mode: {formatRenderModeLabel(renderMode)}
            </span>
            <button
              type="button"
              onClick={() => setRenderMode((previous) => nextRenderMode(previous))}
              className="rounded border border-border bg-card px-3 py-1 hover:bg-muted"
            >
              cycle render mode
            </button>
            <span className="rounded border border-border bg-card px-2 py-1">
              f1 row selection: {f1RowSelectionMode}
            </span>
            <button
              type="button"
              onClick={() => setF1RowSelectionMode((previous) => (previous === "map-fields" ? "coord-hash" : "map-fields"))}
              className="rounded border border-border bg-card px-3 py-1 hover:bg-muted"
            >
              toggle f1 row mode
            </button>
            <span className="rounded border border-border bg-card px-2 py-1">
              terrain alignment: {terrainAlignmentMode === "diamond-fit" ? "diamond-fit" : "sprite-native"}
            </span>
            <button
              type="button"
              onClick={() => setTerrainAlignmentMode((previous) => (previous === "diamond-fit" ? "sprite-native" : "diamond-fit"))}
              className="rounded border border-border bg-card px-3 py-1 hover:bg-muted"
            >
              toggle alignment mode
            </button>
            <span className="rounded border border-border bg-card px-2 py-1">overlay: {overlayMode}</span>
            <span className="rounded border border-border bg-card px-2 py-1">
              logical footprint: {(TILE_WIDTH * logicalFootprintScale).toFixed(1)}x{(TILE_HEIGHT * logicalFootprintScale).toFixed(1)}
            </span>
            <button
              type="button"
              onClick={() => setOverlayMode((previous) => nextOverlayMode(previous))}
              className="rounded border border-border bg-card px-3 py-1 hover:bg-muted"
            >
              cycle overlay
            </button>
            <button
              type="button"
              onClick={() => setShowTileCenters((previous) => !previous)}
              className="rounded border border-border bg-card px-3 py-1 hover:bg-muted"
            >
              centers: {showTileCenters ? "on" : "off"}
            </button>
            <button
              type="button"
              onClick={() => setShowTextureBounds((previous) => !previous)}
              className="rounded border border-border bg-card px-3 py-1 hover:bg-muted"
            >
              texture bounds: {showTextureBounds ? "on" : "off"}
            </button>
            <button
              type="button"
              onClick={() => setDefaultTerrainMode((previous) => !previous)}
              className="rounded border border-border bg-card px-3 py-1 hover:bg-muted"
            >
              default terrain mode: {defaultTerrainMode ? "on" : "off"}
            </button>
            <button
              type="button"
              onClick={() => setShowNatureDebugOverlay((previous) => !previous)}
              className="rounded border border-border bg-card px-3 py-1 hover:bg-muted"
            >
              nature debug overlay: {showNatureDebugOverlay ? "on" : "off"}
            </button>
          </>
        )}
        {!hideNatureToggleButtons && (
          <>
            <button
              type="button"
              onClick={() => setShowTerrainNature((previous) => !previous)}
              className="rounded border border-border bg-card px-3 py-1 hover:bg-muted"
            >
              terrain nature: {showTerrainNature ? "on" : "off"}
            </button>
            <button
              type="button"
              onClick={() => setShowResourceNature((previous) => !previous)}
              className="rounded border border-border bg-card px-3 py-1 hover:bg-muted"
            >
              resources: {showResourceNature ? "on" : "off"}
            </button>
            <button
              type="button"
              onClick={() => setShowHumanNature((previous) => !previous)}
              className="rounded border border-border bg-card px-3 py-1 hover:bg-muted"
            >
              humans: {showHumanNature ? "on" : "off"}
            </button>
            <button
              type="button"
              onClick={() => setShowSpecialNature((previous) => !previous)}
              className="rounded border border-border bg-card px-3 py-1 hover:bg-muted"
            >
              special: {showSpecialNature ? "on" : "off"}
            </button>
          </>
        )}
        {!publicMode && (
          <>
            <button
              type="button"
              onClick={() => setShowCorrectedRankingBoardPlacementTest((previous) => !previous)}
              className="rounded border border-border bg-card px-3 py-1 hover:bg-muted"
            >
              Show CORRECTED Ranking Board: {showCorrectedRankingBoardPlacementTest ? "on" : "off"}
            </button>
            <button
              type="button"
              onClick={() => setShowOnePieceFacilities((previous) => !previous)}
              className="rounded border border-border bg-card px-3 py-1 hover:bg-muted"
            >
              native facility placements: {showOnePieceFacilities ? "on" : "off"}
            </button>
            <button
              type="button"
              onClick={() => setShowNoOffsetFacilityDuplicates((previous) => !previous)}
              className="rounded border border-border bg-card px-3 py-1 hover:bg-muted"
            >
              experimental offset duplicates: {showNoOffsetFacilityDuplicates ? "on" : "off"}
            </button>
            <button
              type="button"
              onClick={() => setShowRawRankingBoardPlacementTest((previous) => !previous)}
              className="rounded border border-border bg-card px-3 py-1 hover:bg-muted"
            >
              Show RAW DATA Ranking Board: {showRawRankingBoardPlacementTest ? "on" : "off"}
            </button>
            <button
              type="button"
              onClick={() => setShowLegendaryCaveRawPlacementTest((previous) => !previous)}
              className="rounded border border-border bg-card px-3 py-1 hover:bg-muted"
            >
              Show Legendary Cave Raw Placement Test: {showLegendaryCaveRawPlacementTest ? "on" : "off"}
            </button>
          </>
        )}
        {!publicMode && (
          <>
            <button
              type="button"
              onClick={() => setShowPortAssemblies((previous) => !previous)}
              className="rounded border border-border bg-card px-3 py-1 hover:bg-muted"
            >
              ports: {showPortAssemblies ? "on" : "off"}
            </button>
            <button
              type="button"
              onClick={() => setShowPortBridgePieces((previous) => !previous)}
              className="rounded border border-border bg-card px-3 py-1 hover:bg-muted"
            >
              port bridge: {showPortBridgePieces ? "on" : "off"}
            </button>
            <button
              type="button"
              onClick={() => setForceVisibleNatureMode((previous) => !previous)}
              className="rounded border border-border bg-card px-3 py-1 hover:bg-muted"
            >
              force visible nature: {forceVisibleNatureMode ? "on" : "off"}
            </button>
            <button
              type="button"
              onClick={() => setForceVisibleLargeNatureMode((previous) => !previous)}
              className="rounded border border-border bg-card px-3 py-1 hover:bg-muted"
            >
              force large nature: {forceVisibleLargeNatureMode ? "on" : "off"}
            </button>
            <button
              type="button"
              onClick={() => setShowNatureCandidateBounds((previous) => !previous)}
              className="rounded border border-border bg-card px-3 py-1 hover:bg-muted"
            >
              nature boxes: {showNatureCandidateBounds ? "on" : "off"}
            </button>
            <label className="flex items-center gap-2 rounded border border-border bg-card px-3 py-1">
              <span>footprint scale</span>
              <input
                type="range"
                min={0.75}
                max={1.05}
                step={0.01}
                value={logicalFootprintScale}
                onChange={(event) => setLogicalFootprintScale(Number(event.target.value))}
              />
              <span>{logicalFootprintScale.toFixed(2)}x</span>
            </label>
          </>
        )}
      </div>

      {publicMode && !editorCellPicking && (
        <div className="mt-2 text-sm font-medium text-amber-500">
          Controls: Shift + mouse wheel to zoom. Pinch to zoom on mobile.
        </div>
      )}

      {error && <div className="mt-3 rounded border border-red-500/40 bg-red-950/40 p-3 text-xs text-red-100">{error}</div>}

      <div ref={containerRef} className={`relative overflow-hidden border border-border ${fillHeight?'min-h-0 flex-1 rounded-none border-0':`mt-3 rounded ${editorCellPicking?'h-[calc(100dvh-285px)] min-h-[360px]':'h-[74vh]'}`}`} style={{ backgroundColor: "#1d4f6c" }}>
        <NativeOceanBackground image={pipeline?.oceanBackground} />
        <canvas
          ref={canvasRef}
          className={`relative h-full w-full touch-none ${dragging || portGateDrag ? "cursor-grabbing" : "cursor-grab"}`}
          onPointerDown={onPointerDown}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
          onPointerLeave={onPointerLeave}
          onPointerMove={onPointerMove}
          onWheel={onWheel}
          onClick={onCanvasClick}
          aria-label="Engine driven map renderer canvas"
        />
        {/* GameForm.DrawOverlayTopAlpha 0x16be250: resGame2 SEB24/image27,
            original 120x165 image scaled to the viewport after the world pass.
            This fixed overlay is independent of the time-of-day lighting. */}
        {pipeline && <img src={pipeline.viewportLighting.src} alt="" aria-hidden="true"
          className="pointer-events-none absolute inset-0 h-full w-full select-none" />}
        {externalOverlays.length > 0 && (
          <div className="pointer-events-none absolute inset-0 overflow-hidden">
            {externalOverlays.map((overlay) => {
              const widthTiles = Math.max(1, overlay.widthTiles ?? 1);
              const baseIso = worldToIso(overlay.cellX, overlay.cellY, camera.zoom, camera.offsetX, camera.offsetY);
              const anchorMode = overlay.anchorMode ?? "south";
              const southY = baseIso.y + TILE_HEIGHT * camera.zoom * (0.5 + (widthTiles - 1) + (overlay.anchorOffsetTilesY ?? 0));
              const centerY = baseIso.y + TILE_HEIGHT * camera.zoom * (overlay.anchorOffsetTilesY ?? 0);
              const centerX = baseIso.x;
              const spriteScale = Math.max(0.2, overlay.spriteScale ?? 1);
              const drawW = TILE_WIDTH * camera.zoom * (widthTiles + (overlay.spriteUrl ? 0.9 : 0.2)) * spriteScale;
              const drawH = TILE_HEIGHT * camera.zoom * (widthTiles + (overlay.spriteUrl ? 2.4 : 1.1)) * spriteScale;
              const markerClass = overlay.markerKind === "monster"
                ? "border-red-900/45 bg-red-500/85"
                : "border-emerald-900/45 bg-emerald-500/85";
              return (
                <div
                  key={overlay.id}
                  className="absolute"
                  style={{
                    left: centerX,
                    top: anchorMode === "center" ? centerY : southY,
                    width: drawW,
                    height: drawH,
                    transform: anchorMode === "center" ? "translate(-50%, -50%)" : "translate(-50%, -100%)",
                    opacity: Math.max(0.2, Math.min(1, overlay.opacity ?? 1)),
                  }}
                >
                  {overlay.spriteUrl ? (
                    <img
                      src={overlay.spriteUrl}
                      alt=""
                      className="h-full w-full object-contain [image-rendering:pixelated]"
                      style={overlay.invalid ? { filter: "grayscale(1) brightness(0.55) sepia(0.8) hue-rotate(-35deg)" } : undefined}
                      draggable={false}
                    />
                  ) : (
                    <span className={`absolute left-1/2 top-1/2 flex h-[65%] w-[65%] -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full border text-sm font-bold text-white ${markerClass}`}>
                      {overlay.markerKind === "monster"
                        ? <Skull className="h-[72%] w-[72%]" strokeWidth={2.2} />
                        : (overlay.markerText ?? "")}
                    </span>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {!publicMode && pipeline && (
        <div className="mt-3 grid gap-3 text-xs lg:grid-cols-2">
          <div className="rounded border border-border bg-card p-3">
            <div className="font-medium">Metadata Pipeline</div>
            <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1">
              <div>map size: {pipeline.parsedMap.width} x {pipeline.parsedMap.height}</div>
              <div>f1 terrain render commands: {pipeline.f1TerrainCommands.length}</div>
              <div>mapchip render commands: {pipeline.mapChipCommands.length}</div>
              <div>terrain render commands: {pipeline.terrainCommands.length}</div>
              <div>active render commands: {activeCommands.length}</div>
              <div>nature overlay commands: {natureOverlayCommandCount}</div>
              <div>MapChip rows: {pipeline.usedMetadata.mapChipRows}</div>
              <div>Terrain rows: {pipeline.usedMetadata.terrainRows}</div>
              <div>img.inf entries: {pipeline.usedMetadata.imgEntries}</div>
              <div>seb.inf entries: {pipeline.usedMetadata.sebEntries}</div>
              <div>loaded opt files: {pipeline.usedMetadata.optFiles}</div>
              <div>atlas regions: {pipeline.usedMetadata.atlasRegions}</div>
            </div>
          </div>

          <div className="rounded border border-sky-500/30 bg-card p-3">
            <div className="flex items-center justify-between gap-3">
              <div>
                <div className="font-medium">Port 4x4 Manual Layout</div>
                <div className="mt-1 text-muted-foreground">
                  Port footprint is [cellX-4..cellX-1] x [cellY-4..cellY-1]. Each piece below is a 2x2 anchor inside that 4x4.
                </div>
              </div>
              <button
                type="button"
                onClick={resetPortGateLayout}
                className="rounded border border-border bg-background px-2 py-1 hover:bg-muted"
              >
                reset
              </button>
            </div>
            <div className="mt-3 grid gap-2">
              {PORT_GATE_PIECES.map((piece) => {
                const offset = portGateLayout[piece.assetKey];
                return (
                  <div key={piece.assetKey} className="grid grid-cols-[1fr_auto_auto] items-center gap-2 rounded border border-border/60 bg-background px-2 py-1">
                    <div>
                      <div className="font-medium">{piece.assetKey}</div>
                      <div className="text-muted-foreground">chip {piece.chipId}, facility {piece.facilityId}, 2x2</div>
                    </div>
                    <label className="flex items-center gap-1">
                      dx
                      <input
                        type="number"
                        step="0.25"
                        value={offset.dx}
                        onChange={(event) => updatePortGateOffset(piece.assetKey, "dx", Number(event.target.value))}
                        className="w-16 rounded border border-border bg-card px-2 py-1"
                      />
                    </label>
                    <label className="flex items-center gap-1">
                      dy
                      <input
                        type="number"
                        step="0.25"
                        value={offset.dy}
                        onChange={(event) => updatePortGateOffset(piece.assetKey, "dy", Number(event.target.value))}
                        className="w-16 rounded border border-border bg-card px-2 py-1"
                      />
                    </label>
                  </div>
                );
              })}
            </div>
            <pre className="mt-3 overflow-auto rounded border border-border bg-background p-2 text-[11px] text-muted-foreground">
              {JSON.stringify(portGateLayout, null, 2)}
            </pre>
          </div>

          {diagnostics ? false && (
            <div className="rounded border border-border bg-card p-3 lg:col-span-2">
              <div className="font-medium">Render Diagnostics ({formatRenderModeLabel(renderMode)})</div>
              <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1">
                <div>unique raw map values: {diagnostics.uniqueRawMapValues}</div>
                <div>unique MapChip ids: {diagnostics.uniqueMapChipIds}</div>
                <div>unique MapChip.img ids: {diagnostics.uniqueMapChipImgValues}</div>
                <div>unique resolved PNG filenames: {diagnostics.uniqueResolvedPngFilenames}</div>
                <div>unique drawn PNG filenames: {diagnostics.uniqueDrawnPngFilenames}</div>
                <div>unique source rectangles drawn: {diagnostics.uniqueSourceRects}</div>
              </div>
              <div className="mt-2 rounded border border-border bg-background px-2 py-1">
                stage diagnosis: {diagnostics.stageDiagnosis}
              </div>
              <div className="mt-2 grid gap-2 lg:grid-cols-3">
                <div className="rounded border border-border bg-background p-2">
                  <div className="font-medium">Top 20 MapChip names</div>
                  <div className="mt-1 max-h-40 overflow-auto space-y-1">
                    {diagnostics.topMapChipNames.map((item) => (
                      <div key={`chip-name-${item.key}`} className="flex justify-between gap-2">
                        <span className="truncate">{item.key}</span>
                        <span className="text-muted-foreground">{item.count}</span>
                      </div>
                    ))}
                  </div>
                </div>
                <div className="rounded border border-border bg-background p-2">
                  <div className="font-medium">Top 20 img ids</div>
                  <div className="mt-1 max-h-40 overflow-auto space-y-1">
                    {diagnostics.topImgIds.map((item) => (
                      <div key={`img-id-${item.key}`} className="flex justify-between gap-2">
                        <span>{item.key}</span>
                        <span className="text-muted-foreground">{item.count}</span>
                      </div>
                    ))}
                  </div>
                </div>
                <div className="rounded border border-border bg-background p-2">
                  <div className="font-medium">Top 20 resolved PNG filenames</div>
                  <div className="mt-1 max-h-40 overflow-auto space-y-1">
                    {diagnostics.topResolvedPngFilenames.map((item) => (
                      <div key={`png-resolved-name-${item.key}`} className="flex justify-between gap-2">
                        <span className="truncate">{item.key}</span>
                        <span className="text-muted-foreground">{item.count}</span>
                      </div>
                    ))}
                  </div>
                </div>
                <div className="rounded border border-border bg-background p-2 lg:col-span-3">
                  <div className="font-medium">Top 20 drawn PNG filenames</div>
                  <div className="mt-1 max-h-40 overflow-auto space-y-1">
                    {diagnostics.topDrawnPngFilenames.map((item) => (
                      <div key={`png-name-${item.key}`} className="flex justify-between gap-2">
                        <span className="truncate">{item.key}</span>
                        <span className="text-muted-foreground">{item.count}</span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          ) : null}


          {natureDebugSummary ? false && (
            <div className="rounded border border-rose-500/30 bg-rose-950/20 p-3 lg:col-span-2">
              <div className="font-medium">Nature Selection Debug</div>
              <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1">
                <div>nature draw commands: {natureDebugSummary.commandCount}</div>
                <div>nature tiles: {natureDebugSummary.cellCount}</div>
                <div>unique Terrain rows: {natureDebugSummary.rowIdCount}</div>
                <div>unique sprite files: {natureDebugSummary.spriteCount}</div>
                <div>large source rect commands: {natureDebugSummary.largeSourceRectCount}</div>
                <div>large full-image commands: {natureDebugSummary.fullImageSourceCount}</div>
              </div>
              <div className="mt-3 grid gap-3 lg:grid-cols-3">
                <div>
                  <div className="font-medium">Top rows</div>
                  <div className="mt-1 max-h-36 overflow-auto space-y-1">
                    {natureDebugSummary.topRows.map((entry) => (
                      <div key={`nature-row-${entry.key}`} className="flex justify-between gap-2">
                        <span className="truncate">{entry.key}</span>
                        <span className="text-muted-foreground">{entry.count}</span>
                      </div>
                    ))}
                  </div>
                </div>
                <div>
                  <div className="font-medium">Top sprites</div>
                  <div className="mt-1 max-h-36 overflow-auto space-y-1">
                    {natureDebugSummary.topSprites.map((entry) => (
                      <div key={`nature-sprite-${entry.key}`} className="flex justify-between gap-2">
                        <span className="truncate">{entry.key}</span>
                        <span className="text-muted-foreground">{entry.count}</span>
                      </div>
                    ))}
                  </div>
                </div>
                <div>
                  <div className="font-medium">Top selection reasons</div>
                  <div className="mt-1 max-h-36 overflow-auto space-y-1">
                    {natureDebugSummary.topReasons.map((entry) => (
                      <div key={`nature-reason-${entry.key}`} className="flex justify-between gap-2">
                        <span className="truncate">{entry.key}</span>
                        <span className="text-muted-foreground">{entry.count}</span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
              <div className="mt-3 grid gap-3 lg:grid-cols-2">
                <div>
                  <div className="font-medium">Available Terrain categories</div>
                  <div className="mt-1 max-h-32 overflow-auto space-y-1">
                    {natureDebugSummary.availableCategories.map((entry) => (
                      <div key={`terrain-cat-${entry.key}`} className="flex justify-between gap-2">
                        <span>category {entry.key}</span>
                        <span className="text-muted-foreground">{entry.count}</span>
                      </div>
                    ))}
                  </div>
                </div>
                <div>
                  <div className="font-medium">Large-image rows by category</div>
                  <div className="mt-1 max-h-32 overflow-auto space-y-1">
                    {natureDebugSummary.largeAvailableCategories.map((entry) => (
                      <div key={`terrain-large-cat-${entry.key}`} className="flex justify-between gap-2">
                        <span>category {entry.key}</span>
                        <span className="text-muted-foreground">{entry.count}</span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          ) : null}


          <div className="rounded border border-border bg-card p-3">
            <div className="flex items-center justify-between">
              <div className="font-medium">Cell Inspector</div>
              <label className="flex cursor-pointer items-center gap-2 text-xs">
                <input
                  type="checkbox"
                  checked={twoByTwoMode}
                  onChange={(e) => setTwoByTwoMode(e.target.checked)}
                  className="h-3 w-3"
                />
                2×2 mode
              </label>
            </div>
            {selectedCell && pipeline ? (
              <div className="mt-2 space-y-0.5 font-mono text-xs">
                {(twoByTwoMode
                  ? [
                      { label: "NW", x: selectedCell.x,     y: selectedCell.y     },
                      { label: "NE", x: selectedCell.x + 1, y: selectedCell.y     },
                      { label: "SW", x: selectedCell.x,     y: selectedCell.y + 1 },
                      { label: "SE", x: selectedCell.x + 1, y: selectedCell.y + 1 },
                    ]
                  : [{ label: "",   x: selectedCell.x,     y: selectedCell.y     }]
                ).map(({ label, x, y }) => {
                  const c = getParsedCellAt(pipeline.parsedMap, x, y);
                  return (
                    <div key={`${x}-${y}`}>
                      {label !== "" && <span className="mr-1 text-muted-foreground">{label}</span>}
                      ({x}, {y}) f2={c?.fields.f2 ?? "?"} f1={c?.fields.f1 ?? "?"}
                      {" · height level "}{c?.heightLevel ?? "?"}{" · base height "}{c?.baseHeightPixels ?? "?"} px
                    </div>
                  );
                })}
              </div>
            ) : (
              <div className="mt-2 text-xs text-muted-foreground">Click a cell on the canvas. Enable 2×2 to read all 4 cells at once.</div>
            )}
          </div>

          <div className="rounded border border-border bg-card p-3 lg:col-span-2">
            <div className="font-medium">Static Facilities — Cell Footprints</div>
            <div className="mt-2 overflow-x-auto">
              <table className="w-full text-left font-mono text-xs">
                <thead>
                  <tr className="border-b border-border text-muted-foreground">
                    <th className="py-0.5 pr-4 font-sans font-medium">Name</th>
                    <th className="py-0.5 pr-3">NW</th>
                    <th className="py-0.5 pr-3">NE</th>
                    <th className="py-0.5 pr-3">SW</th>
                    <th className="py-0.5 pr-3">SE</th>
                    <th className="py-0.5">img</th>
                  </tr>
                </thead>
                <tbody>
                  {ONE_PIECE_FACILITY_OVERLAYS.map((f) => (
                    <tr key={f.id} className="border-b border-border/40">
                      <td className="py-0.5 pr-4 font-sans">{f.name}</td>
                      <td className="py-0.5 pr-3">({f.cellX - 2},{f.cellY - 2})</td>
                      <td className="py-0.5 pr-3">({f.cellX - 1},{f.cellY - 2})</td>
                      <td className="py-0.5 pr-3">({f.cellX - 2},{f.cellY - 1})</td>
                      <td className="py-0.5 pr-3">({f.cellX - 1},{f.cellY - 1})</td>
                      <td className="py-0.5">{pipeline.facilityBuildingCache.has(f.id) ? "✓" : "·"} {f.buildingImageId}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

async function buildEngineRenderPipeline(f1RowSelectionMode: F1RowSelectionMode): Promise<RenderPipeline> {
  const [mapBinary, mapChipText, terrainText, imgInfText, sebInfText, buildingImgInfText, natureImgInfText, natureSebInfText, atlasText] = await Promise.all([
    fetchArrayBuffer(MAP_PATH),
    fetchText(MAP_CHIP_PATH),
    fetchText(TERRAIN_PATH),
    fetchText(CHIP_IMG_INF_PATH),
    fetchText(CHIP_SEB_INF_PATH),
    fetchText(BUILDING_IMG_INF_PATH),
    fetchText(NATURE_IMG_INF_PATH),
    fetchText(NATURE_SEB_INF_PATH),
    fetchText(IMAGE_ATLAS_PATH),
  ]);

  const parsedMap = parseNativeMapBinary(mapBinary);
  const mapChipRows = parseMapChipRows(mapChipText);
  const terrainRows = parseTerrainRows(terrainText);
  const mapChipById = new Map<number, MapChipRow>(mapChipRows.map((row) => [row.id, row]));
  const terrainById = new Map<number, TerrainRow>(terrainRows.map((row) => [row.id, row]));
  const terrainByType = groupRowsByKey(terrainRows, (row) => row.type);
  const terrainByCategory = groupRowsByKey(terrainRows, (row) => row.category);
  const mapChipByLayer = groupRowsByKey(mapChipRows, (row) => row.layer);
  const mapChipByCategory = groupRowsByKey(mapChipRows, (row) => row.category);
  const imageById = parseInfTable(imgInfText);
  const sebById = parseInfTable(sebInfText);
  const buildingImageById = parseInfTable(buildingImgInfText);
  const natureImageById = parseInfTable(natureImgInfText);
  const natureSebById = parseInfTable(natureSebInfText);
  const atlasByName = parseImageAtlas(atlasText);

  const usedF2Values = new Set<number>();
  const usedF1Values = new Set<number>();
  const f1CommandByCell = new Map<string, TileDrawCommand[]>();

  for (const cell of parsedMap.cells) {
    usedF2Values.add(cell.fields.f2);
    usedF1Values.add(cell.fields.f1);
  }

  const neededFilenames = new Set<string>();
  const neededSebFiles = new Set<string>();
  const neededNatureFilenames = new Set<string>();
  const neededNatureSebFiles = new Set<string>();

  for (const f2Value of usedF2Values) {
    const mapChipRow = mapChipById.get(f2Value);
    if (mapChipRow) {
      const mapChipFilename = imageById.get(mapChipRow.img);
      if (mapChipFilename) {
        neededFilenames.add(mapChipFilename);
      }
      const mapChipSebName = sebById.get(mapChipRow.seb);
      if (mapChipSebName) {
        neededSebFiles.add(mapChipSebName);
      }
    }

    const terrainRow = terrainById.get(f2Value);
    if (terrainRow) {
      const terrainFilename = imageById.get(terrainRow.img);
      if (terrainFilename) {
        neededFilenames.add(terrainFilename);
      }
      const terrainSebName = sebById.get(terrainRow.seb);
      if (terrainSebName) {
        neededSebFiles.add(terrainSebName);
      }
    }
  }

  for (const f1Value of usedF1Values) {
    const terrainRowsForType = terrainByType.get(f1Value) ?? [];
    for (const terrainRow of terrainRowsForType) {
      const isNatureRow = terrainRow.res === 20;
      const terrainFilename = isNatureRow ? natureImageById.get(terrainRow.img) : imageById.get(terrainRow.img);
      if (terrainFilename) {
        if (isNatureRow) {
          neededNatureFilenames.add(terrainFilename);
        } else {
          neededFilenames.add(terrainFilename);
        }
      }
      const terrainSebName = isNatureRow ? natureSebById.get(terrainRow.seb) : sebById.get(terrainRow.seb);
      if (terrainSebName) {
        if (isNatureRow) {
          neededNatureSebFiles.add(terrainSebName);
        } else {
          neededSebFiles.add(terrainSebName);
        }
      }
    }
  }

  const optByStem = new Map<string, OptMetadata>();
  await Promise.all(
    [...neededFilenames].map(async (filename) => {
      const stem = filename.replace(/\.[^.]+$/, "");
      const optPath = resolveAssetUrl(`world-assets/chip/${stem}.opt`);
      const maybe = await fetchArrayBufferOptional(optPath);
      if (!maybe) {
        return;
      }
      const parsedOpt = parseOptSequential(maybe);
      optByStem.set(stem, parsedOpt);
    }),
  );

  const sebFiles = new Map<string, SebFile>();
  await Promise.all(
    [...neededSebFiles].map(async (name) => {
      const path = resolveAssetUrl(`world-assets/chip/${name}`);
      const maybe = await fetchArrayBufferOptional(path);
      if (!maybe) {
        return;
      }
      sebFiles.set(name, parseSeb(maybe));
    }),
  );

  const natureOptByStem = new Map<string, OptMetadata>();
  await Promise.all(
    [...neededNatureFilenames].map(async (filename) => {
      const stem = filename.replace(/\.[^.]+$/, "");
      const optPath = resolveAssetUrl(`world-assets/nature/${stem}.opt`);
      const maybe = await fetchArrayBufferOptional(optPath);
      if (!maybe) {
        return;
      }
      const parsedOpt = parseOptSequential(maybe);
      natureOptByStem.set(stem, parsedOpt);
    }),
  );

  const natureSebFiles = new Map<string, SebFile>();
  await Promise.all(
    [...neededNatureSebFiles].map(async (name) => {
      const path = resolveAssetUrl(`world-assets/nature/${name}`);
      const maybe = await fetchArrayBufferOptional(path);
      if (!maybe) {
        return;
      }
      natureSebFiles.set(name, parseSeb(maybe));
    }),
  );

  for (const seb of sebFiles.values()) {
    for (const block of seb.blocks) {
      for (const record of block.records) {
        const sourceFilename = imageById.get(record.sourceId);
        if (sourceFilename) {
          neededFilenames.add(sourceFilename);
        }
      }
    }
  }

  const imageCache = new Map<string, LoadedImageAsset>();
  let nextImageInstanceId = 1;
  await Promise.all(
    [...neededFilenames].map(async (filename) => {
      const requestPath = resolveAssetUrl(`world-assets/chip/${filename}`);
      const image = await loadImage(requestPath);
      imageCache.set(filename, {
        key: filename,
        requestPath,
        resolvedSrc: image.currentSrc || image.src || requestPath,
        instanceId: nextImageInstanceId,
        image,
      });
      nextImageInstanceId += 1;
    }),
  );
  await Promise.all(
    [...neededNatureFilenames].map(async (filename) => {
      const requestPath = resolveAssetUrl(`world-assets/nature/${filename}`);
      const image = await loadImage(requestPath);
      imageCache.set(filename, {
        key: filename,
        requestPath,
        resolvedSrc: image.currentSrc || image.src || requestPath,
        instanceId: nextImageInstanceId,
        image,
      });
      nextImageInstanceId += 1;
    }),
  );

  const mapChipCommands: TileDrawCommand[] = [];
  const terrainCommands: TileDrawCommand[] = [];
  let sebBlockZeroFallbackCount = 0;
  let optSlotFallbackCount = 0;
  let fullImageFallbackCount = 0;
  let skippedPlaceholderCount = 0;
  let missingImageCount = 0;
  let excludedControlRows = 0;
  let excludedStructures = 0;

  for (const cell of parsedMap.cells) {
    const terrainType = cell.fields.f1;
    const terrainTypeName = getF1TerrainTypeName(terrainType);
    const terrainFamily = getF1TerrainFamily(terrainType);
    const typeRows = (terrainByType.get(terrainType) ?? []).filter((row) => {
      const filename = row.res === 20 ? natureImageById.get(row.img) ?? "" : imageById.get(row.img) ?? "";
      return !isPlayerMadeSurface(row.name, filename);
    });
    const baseRows = typeRows.filter((row) => row.category === 0);
    const selectedByMode = pickTerrainRowForCell(baseRows, terrainFamily, imageById, cell.x, cell.y, cell.fields, f1RowSelectionMode);
    const selectedTerrainRow = selectedByMode.row;
    const selectedBaseReason = selectedByMode.reason;
    const selectedNatureRows = selectOneNatureRowForTile(typeRows, cell.x, cell.y, cell.fields, natureImageById);
    const fallbackFamilyFilename = findFallbackBaseFilename(baseRows, imageById, terrainFamily);
    const waterTerrain = isWaterTerrainFamily(terrainFamily) || (selectedTerrainRow ? isWaterToken(selectedTerrainRow.name) : false);
    const commandKey = `${cell.x},${cell.y}`;
    const cellCommands = f1CommandByCell.get(commandKey) ?? [];

    let selection: SpriteSelection | null = null;
    let resolvedImgFilename = "";
    let mapChipImgId = -1;
    let mapChipSebId = -1;
    let mapChipFrame = 0;
    let mapChipId = -1;
    let mapChipName = `f1 terrain type ${terrainTypeName}`;
    let mapChipCategory = terrainType;

    if (selectedTerrainRow) {
      selection = resolveSpriteSelection(
        selectedTerrainRow,
        imageById,
        sebById,
        sebFiles,
        optByStem,
        imageCache,
        atlasByName,
        false,
        false,
      );
      resolvedImgFilename = imageById.get(selectedTerrainRow.img) ?? "";
      mapChipImgId = selectedTerrainRow.img;
      mapChipSebId = selectedTerrainRow.seb;
      mapChipFrame = selectedTerrainRow.frame;
      mapChipId = selectedTerrainRow.id;
      mapChipName = selectedTerrainRow.name;
      mapChipCategory = selectedTerrainRow.category;
    }

    if ((!selection || selection.method === "missing-image" || selection.method === "placeholder-skip") && fallbackFamilyFilename) {
      const fallbackImage = imageCache.get(fallbackFamilyFilename)?.image;
      if (fallbackImage) {
        selection = {
          method: "full-image",
          sourceFilename: fallbackFamilyFilename,
          srcX: 0,
          srcY: 0,
          srcW: fallbackImage.width,
          srcH: fallbackImage.height,
          drawOffsetX: -fallbackImage.width / 2,
          drawOffsetY: -fallbackImage.height + 1,
          frameInfo: "f1 category=0 base fallback full image",
        };
        if (!resolvedImgFilename) {
          resolvedImgFilename = fallbackFamilyFilename;
        }
      }
    }

    if (!selection || selection.method === "missing-image" || selection.method === "placeholder-skip") {
      continue;
    }

    const imageAsset = selection.sourceFilename ? imageCache.get(selection.sourceFilename) : null;
    const command: TileDrawCommand = {
      cellX: cell.x,
      cellY: cell.y,
      rawMapValue: terrainType,
      sourceField: "f1",
      f1TerrainTypeValue: terrainType,
      f1TerrainTypeName: terrainTypeName,
      selectedTerrainVisualFamily: terrainFamily,
      selectedBaseReason,
      layer: 0,
      mapChipId,
      mapChipName,
      mapChipCategory,
      mapChipImgId,
      mapChipSebId,
      mapChipFrame,
      resolvedImgFilename,
      imageCacheKeyUsed: selection.sourceFilename,
      imageObjectInstanceId: imageAsset?.instanceId ?? null,
      imageObjectResolvedSrc: imageAsset?.resolvedSrc ?? "",
      imageObjectRequestPath: imageAsset?.requestPath ?? "",
      loadedPngPath: imageAsset?.requestPath ?? (selection.sourceFilename ? resolveAssetUrl(`world-assets/chip/${selection.sourceFilename}`) : ""),
      drawGroup: "base-terrain",
      terrainName: terrainTypeName,
      selection,
    };

    cellCommands.push(command);

    if (ENABLE_MAP_NATURE_OVERLAYS && !waterTerrain) {
      for (const selectedNature of selectedNatureRows) {
        const natureSelection = resolveSpriteSelection(
          selectedNature.row,
          natureImageById,
          natureSebById,
          natureSebFiles,
          natureOptByStem,
          imageCache,
          atlasByName,
          false,
          false,
        );

        if (natureSelection.method === "missing-image" || natureSelection.method === "placeholder-skip") {
          continue;
        }

        const natureImageAsset = natureSelection.sourceFilename ? imageCache.get(natureSelection.sourceFilename) : null;
        const natureResolvedFilename = natureImageById.get(selectedNature.row.img) ?? "";
        const natureTerrainName = selectedNature.row.name || `nature overlay type ${terrainTypeName}`;
        const natureCommand: TileDrawCommand = {
          cellX: cell.x,
          cellY: cell.y,
          rawMapValue: terrainType,
          sourceField: "f1",
          f1TerrainTypeValue: terrainType,
          f1TerrainTypeName: terrainTypeName,
          selectedTerrainVisualFamily: terrainFamily,
          selectedBaseReason: selectedNature.reason,
          layer: NATURE_CATEGORY_LAYER_ORDER[selectedNature.category ?? "terrain-nature"] + selectedNature.score / 1000,
          mapChipId: selectedNature.row.id,
          mapChipName: natureTerrainName,
          mapChipCategory: selectedNature.row.category,
          mapChipImgId: selectedNature.row.img,
          mapChipSebId: selectedNature.row.seb,
          mapChipFrame: selectedNature.row.frame,
          resolvedImgFilename: natureResolvedFilename,
          imageCacheKeyUsed: natureSelection.sourceFilename,
          imageObjectInstanceId: natureImageAsset?.instanceId ?? null,
          imageObjectResolvedSrc: natureImageAsset?.resolvedSrc ?? "",
          imageObjectRequestPath: natureImageAsset?.requestPath ?? "",
          loadedPngPath: natureImageAsset?.requestPath ?? (natureSelection.sourceFilename ? resolveAssetUrl(`world-assets/nature/${natureSelection.sourceFilename}`) : ""),
          drawGroup: "nature-object",
          natureCategory: selectedNature.category,
          terrainName: natureTerrainName,
          selection: natureSelection,
        };
        cellCommands.push(natureCommand);
      }
    }

    f1CommandByCell.set(commandKey, cellCommands);
  }

  const f1TerrainCommands = [...f1CommandByCell.values()].flat();

  for (const cell of parsedMap.cells) {
    const chip = mapChipById.get(cell.fields.f2);
    if (!chip) {
      continue;
    }

    if (CONTROL_ROW_EXCLUDES.has(chip.id) || chip.res === 4) {
      excludedControlRows += 1;
      continue;
    }

    if (chip.res === 23 || chip.id === 58 || chip.id === 59 || chip.id === 60 || chip.id === 61 || /town hall/i.test(chip.name)) {
      excludedStructures += 1;
      continue;
    }

    const selection = resolveSpriteSelection(chip, imageById, sebById, sebFiles, optByStem, imageCache, atlasByName, true);
    if (selection.method === "placeholder-skip") {
      skippedPlaceholderCount += 1;
      continue;
    }
    if (selection.method === "missing-image") {
      missingImageCount += 1;
      continue;
    }
    if (selection.method === "seb-block0") {
      sebBlockZeroFallbackCount += 1;
    } else if (selection.method === "opt-slot") {
      optSlotFallbackCount += 1;
    } else if (selection.method === "full-image") {
      fullImageFallbackCount += 1;
    }

    const terrainName = chip.relatedDataType === 2 ? terrainById.get(chip.relatedDataId)?.name ?? null : null;
    const imageAsset = selection.sourceFilename ? imageCache.get(selection.sourceFilename) : null;
    const resolvedImgFilename = imageById.get(chip.img) ?? "";
    mapChipCommands.push({
      cellX: cell.x,
      cellY: cell.y,
      rawMapValue: cell.fields.f2,
      sourceField: "f2",
      f1TerrainTypeValue: null,
      f1TerrainTypeName: null,
      selectedTerrainVisualFamily: null,
      selectedBaseReason: "not-f1-mode",
      layer: chip.layer,
      mapChipId: chip.id,
      mapChipName: chip.name,
      mapChipCategory: chip.category,
      mapChipImgId: chip.img,
      mapChipSebId: chip.seb,
      mapChipFrame: chip.frame,
      resolvedImgFilename,
      imageCacheKeyUsed: selection.sourceFilename,
      imageObjectInstanceId: imageAsset?.instanceId ?? null,
      imageObjectResolvedSrc: imageAsset?.resolvedSrc ?? "",
      imageObjectRequestPath: imageAsset?.requestPath ?? "",
      loadedPngPath: imageAsset?.requestPath ?? (selection.sourceFilename ? resolveAssetUrl(`world-assets/chip/${selection.sourceFilename}`) : ""),
      drawGroup: classifyDrawGroup(chip.name, resolvedImgFilename),
      terrainName,
      selection,
    });
  }

  for (const cell of parsedMap.cells) {
    const terrain = terrainById.get(cell.fields.f2);
    if (!terrain) {
      continue;
    }

    const selection = resolveSpriteSelection(terrain, imageById, sebById, sebFiles, optByStem, imageCache, atlasByName, false);
    if (selection.method === "placeholder-skip" || selection.method === "missing-image") {
      continue;
    }

    const imageAsset = selection.sourceFilename ? imageCache.get(selection.sourceFilename) : null;
    const resolvedImgFilename = imageById.get(terrain.img) ?? "";
    terrainCommands.push({
      cellX: cell.x,
      cellY: cell.y,
      rawMapValue: cell.fields.f2,
      sourceField: "f2",
      f1TerrainTypeValue: null,
      f1TerrainTypeName: null,
      selectedTerrainVisualFamily: null,
      selectedBaseReason: "not-f1-mode",
      layer: terrain.category,
      mapChipId: terrain.id,
      mapChipName: terrain.name,
      mapChipCategory: terrain.category,
      mapChipImgId: terrain.img,
      mapChipSebId: terrain.seb,
      mapChipFrame: terrain.frame,
      resolvedImgFilename,
      imageCacheKeyUsed: selection.sourceFilename,
      imageObjectInstanceId: imageAsset?.instanceId ?? null,
      imageObjectResolvedSrc: imageAsset?.resolvedSrc ?? "",
      imageObjectRequestPath: imageAsset?.requestPath ?? "",
      loadedPngPath: imageAsset?.requestPath ?? (selection.sourceFilename ? resolveAssetUrl(`world-assets/chip/${selection.sourceFilename}`) : ""),
      drawGroup: classifyDrawGroup(terrain.name, resolvedImgFilename),
      terrainName: terrain.name,
      selection,
    });
  }

  const facilityBuildingCache = new Map<number, LoadedImageAsset>();
  await Promise.all(
    ONE_PIECE_FACILITY_OVERLAYS.map(async (facility) => {
      const filename = buildingImageById.get(facility.buildingImageId);
      if (!filename) {
        return;
      }

      try {
        const requestPath = resolveAssetUrl(`world-assets/building/${filename}`);
        const image = await loadImage(requestPath);
        const optBytes = await fetchArrayBufferOptional(requestPath.replace(/\.png$/, ".opt"));
        const opt = optBytes ? parseOptSprite(optBytes) : undefined;
        facilityBuildingCache.set(facility.id, {
          opt,
          key: `facility-${facility.id}`,
          requestPath,
          resolvedSrc: image.currentSrc || image.src || requestPath,
          instanceId: nextImageInstanceId,
          image,
        });
        nextImageInstanceId += 1;
      } catch {
        // Keep facility placement visible through fallback markers if a building sprite is missing.
      }
    }),
  );

  await Promise.all(
    DEBUG_FACILITY_PLACEMENT_OVERLAYS.map(async (overlay) => {
      if (facilityBuildingCache.has(overlay.facilityId)) return;
      // Debug-only asset load for fixed facility placement test.
      const filename = buildingImageById.get(overlay.mapChipImgId);
      if (!filename) {
        return;
      }

      try {
        const requestPath = resolveAssetUrl(`world-assets/building/${filename}`);
        const image = await loadImage(requestPath);
        facilityBuildingCache.set(overlay.facilityId, {
          key: `debug-facility-placement-${overlay.facilityId}`,
          requestPath,
          resolvedSrc: image.currentSrc || image.src || requestPath,
          instanceId: nextImageInstanceId,
          image,
        });
        nextImageInstanceId += 1;
      } catch {
        // Keep facility placement test visible through fallback drawing if image is missing.
      }
    }),
  );

  await Promise.all(
    DEBUG_RAW_RANKING_BOARD_OVERLAYS.map(async (overlay) => {
      if (facilityBuildingCache.has(overlay.facilityId)) return;
      const filename = buildingImageById.get(overlay.mapChipImgId);
      if (!filename) {
        return;
      }

      try {
        const requestPath = resolveAssetUrl(`world-assets/building/${filename}`);
        const image = await loadImage(requestPath);
        facilityBuildingCache.set(overlay.facilityId, {
          key: `debug-raw-ranking-board-${overlay.facilityId}`,
          requestPath,
          resolvedSrc: image.currentSrc || image.src || requestPath,
          instanceId: nextImageInstanceId,
          image,
        });
        nextImageInstanceId += 1;
      } catch {
        // Keep raw data placement test visible through fallback drawing if image is missing.
      }
    }),
  );

  await Promise.all(
    DEBUG_RAW_LEGENDARY_CAVE_OVERLAYS.map(async (overlay) => {
      if (facilityBuildingCache.has(overlay.facilityId)) return;
      const filename = buildingImageById.get(overlay.mapChipImgId);
      if (!filename) {
        return;
      }

      try {
        const requestPath = resolveAssetUrl(`world-assets/building/${filename}`);
        const image = await loadImage(requestPath);
        facilityBuildingCache.set(overlay.facilityId, {
          key: `debug-raw-legendary-cave-${overlay.facilityId}`,
          requestPath,
          resolvedSrc: image.currentSrc || image.src || requestPath,
          instanceId: nextImageInstanceId,
          image,
        });
        nextImageInstanceId += 1;
      } catch {
        // Keep raw data placement test visible through fallback drawing if image is missing.
      }
    }),
  );

  const portAssetCache = new Map<string, LoadedImageAsset>();
  await Promise.all(
    Object.entries(PORT_ASSET_PATHS).map(async ([assetKey, requestPath]) => {
      try {
        const image = await loadImage(requestPath);
        let opt: OptSprite | undefined;
        if (assetKey.startsWith("gate_") || assetKey === "hashi00" || assetKey === "bridge_wall_00") {
          const bytes = await fetchArrayBufferOptional(requestPath.replace(/\.png$/, ".opt"));
          if (!bytes) throw new Error(`Missing gate OPT: ${assetKey}`);
          opt = parseOptSprite(bytes);
          if (opt.cells.some((cell) => cell.some((component) => component.imageRef !== -1)))
            throw new Error(`Gate references an unloaded image: ${assetKey}`);
        }
        portAssetCache.set(assetKey, {
          key: assetKey,
          requestPath,
          resolvedSrc: image.currentSrc || image.src || requestPath,
          instanceId: nextImageInstanceId,
          image,
          opt,
        });
        nextImageInstanceId += 1;
      } catch {
        // Port assembly remains optional if a bridge or gate asset is missing.
      }
    }),
  );

  const f1TerrainDiagnostics = buildRenderDiagnostics(f1TerrainCommands);
  const mapChipDiagnostics = buildRenderDiagnostics(mapChipCommands);
  const terrainDiagnostics = buildRenderDiagnostics(terrainCommands);

  const unresolvedBehaviors = [
    "SEB block selection semantics are still unresolved globally; deterministic first pass uses block 0 for seb-backed chips.",
    "When MapChip.frame does not map to a filled OPT slot, renderer falls back to first filled slot in row-major order.",
    "Atlas regions are used as metadata validation; 1x1 atlas placeholders are skipped to avoid drawing invalid assets.",
  ];

  const waterAssets = new Map<string, { image: HTMLImageElement; opt: OptSprite }>();
  await Promise.all(["mizu00", "mizu01", "mizu02", "mizu03", "mizu_edge"].map(async name => {
    const path = resolveAssetUrl(`world-assets/chip/${name}`);
    const [image, bytes] = await Promise.all([loadImage(`${path}.png`), fetchArrayBufferOptional(`${path}.opt`)]);
    if (!bytes) throw new Error(`Missing original water OPT: ${name}`);
    waterAssets.set(name, { image, opt: parseOptSprite(bytes) });
  }));
  return {
    parsedMap,
    waterAssets,
    oceanBackground: await loadImage(resolveAssetUrl("world-assets/game_2/back.png")),
    viewportLighting: await loadImage(resolveAssetUrl("world-assets/game_2/effect_00.png")),
    f1TerrainCommands,
    mapChipCommands,
    terrainCommands,
    imageCache,
    facilityBuildingCache,
    portAssetCache,
    f1TerrainDiagnostics,
    mapChipDiagnostics,
    terrainDiagnostics,
    fallbackSummary: {
      sebBlockZeroFallbackCount,
      optSlotFallbackCount,
      fullImageFallbackCount,
      skippedPlaceholderCount,
      missingImageCount,
      excludedControlRows,
      excludedStructures,
    },
    unresolvedBehaviors,
    lookups: {
      mapChipById,
      terrainById,
      terrainByType,
      terrainByCategory,
      mapChipByLayer,
      mapChipByCategory,
      imageById,
      natureImageById,
    },
    usedMetadata: {
      mapChipRows: mapChipRows.length,
      terrainRows: terrainRows.length,
      imgEntries: imageById.size,
      sebEntries: sebById.size,
      optFiles: optByStem.size,
      atlasRegions: atlasByName.size,
    },
  };
}

function resolveSpriteSelection(
  chip: MapChipRow | TerrainRow,
  imageById: Map<number, string>,
  sebById: Map<number, string>,
  sebFiles: Map<string, SebFile>,
  optByStem: Map<string, OptMetadata>,
  imageCache: Map<string, LoadedImageAsset>,
  atlasByName: Map<string, AtlasRegion>,
  useSebSourceImage: boolean,
  allowSebSelection = true,
): SpriteSelection {
  const chipFilename = imageById.get(chip.img);
  if (!chipFilename) {
    return {
      method: "missing-image",
      sourceFilename: "",
      srcX: 0,
      srcY: 0,
      srcW: 1,
      srcH: 1,
      drawOffsetX: 0,
      drawOffsetY: 0,
      frameInfo: "missing img.inf mapping",
    };
  }

  const atlasRegion = atlasByName.get(chipFilename);
  if (atlasRegion && atlasRegion.width <= 1 && atlasRegion.height <= 1) {
    return {
      method: "placeholder-skip",
      sourceFilename: chipFilename,
      srcX: 0,
      srcY: 0,
      srcW: 1,
      srcH: 1,
      drawOffsetX: 0,
      drawOffsetY: 0,
      frameInfo: "atlas placeholder 1x1",
    };
  }

  const sebName = sebById.get(chip.seb);
  if (allowSebSelection && sebName) {
    const seb = sebFiles.get(sebName);
    if (seb && seb.blocks.length > 0) {
      const block = seb.blocks[0];
      const selectedRecord = block.records.find((record) => record.frameIndex === chip.frame) ?? block.records[0];
      if (selectedRecord) {
        const sebFilename = useSebSourceImage
          ? imageById.get(selectedRecord.sourceId) ?? chipFilename
          : chipFilename;
        if (!imageCache.has(sebFilename)) {
          return {
            method: "missing-image",
            sourceFilename: sebFilename,
            srcX: 0,
            srcY: 0,
            srcW: 1,
            srcH: 1,
            drawOffsetX: 0,
            drawOffsetY: 0,
            frameInfo: `SEB ${sebName} source image missing`,
          };
        }

        return {
          method: "seb-block0",
          sourceFilename: sebFilename,
          srcX: selectedRecord.srcX,
          srcY: selectedRecord.srcY,
          srcW: Math.max(1, selectedRecord.width),
          srcH: Math.max(1, selectedRecord.height),
          drawOffsetX: selectedRecord.offsetX,
          drawOffsetY: selectedRecord.offsetY,
          frameInfo: `SEB ${sebName} block=0 frame=${chip.frame} period=${block.period}`,
        };
      }
    }
  }

  const stem = chipFilename.replace(/\.[^.]+$/, "");
  const opt = optByStem.get(stem);
  if (opt) {
    const slotByFrame = opt.slots[chip.frame];
    const selectedSlot = slotByFrame && !slotByFrame.empty ? slotByFrame : opt.slots.find((slot) => !slot.empty);

    if (selectedSlot && !selectedSlot.empty) {
      return {
        method: "opt-slot",
        sourceFilename: chipFilename,
        srcX: selectedSlot.srcX,
        srcY: selectedSlot.srcY,
        srcW: Math.max(1, selectedSlot.width),
        srcH: Math.max(1, selectedSlot.height),
        drawOffsetX: -opt.cellWidth / 2 + selectedSlot.destX,
        drawOffsetY: -opt.cellHeight + selectedSlot.destY + 1,
        frameInfo: `OPT ${stem}.opt slot=${chip.frame} fallback=${slotByFrame && !slotByFrame.empty ? "no" : "yes"}`,
      };
    }
  }

  const fullImage = imageCache.get(chipFilename)?.image;
  if (!fullImage) {
    return {
      method: "missing-image",
      sourceFilename: chipFilename,
      srcX: 0,
      srcY: 0,
      srcW: 1,
      srcH: 1,
      drawOffsetX: 0,
      drawOffsetY: 0,
      frameInfo: "png missing from cache",
    };
  }

  return {
    method: "full-image",
    sourceFilename: chipFilename,
    srcX: 0,
    srcY: 0,
    srcW: fullImage.width,
    srcH: fullImage.height,
    drawOffsetX: -fullImage.width / 2,
    drawOffsetY: -fullImage.height + 1,
    frameInfo: "full image fallback",
  };
}

function parseMapChipRows(text: string): MapChipRow[] {
  const rows: MapChipRow[] = [];
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.replace(/^\ufeff/, "").trim();
    if (!line) {
      continue;
    }
    const parts = line.split("\t");
    const id = asInt(parts[0], -1);
    if (id < 0) {
      continue;
    }

    rows.push({
      id,
      type: asInt(parts[1], 0),
      category: asInt(parts[2], 0),
      name: parts[8] ?? "",
      res: asInt(parts[9], 0),
      img: asInt(parts[10], -1),
      seb: asInt(parts[11], -1),
      frame: asInt(parts[12], 0),
      relatedDataType: asInt(parts[15], 0),
      relatedDataId: asInt(parts[16], -1),
      field17: asInt(parts[17], 0),
      field18: asInt(parts[18], 0),
      field19: asInt(parts[19], 0),
      layer: asInt(parts[20], 0),
      field21: asInt(parts[21], 0),
      sizeWidth: asInt(parts[22], 1),
      sizeHeight: asInt(parts[23], 1),
      field24: asInt(parts[24], 0),
      field25: asInt(parts[25], 0),
      field26: asInt(parts[26], 0),
      field27: asInt(parts[27], 0),
    });
  }
  return rows;
}

function parseTerrainRows(text: string): TerrainRow[] {
  const rows: TerrainRow[] = [];
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.replace(/^\ufeff/, "").trim();
    if (!line) {
      continue;
    }
    const parts = line.split("\t");
    const id = asInt(parts[0], -1);
    if (id < 0) {
      continue;
    }
    rows.push({
      id,
      type: asInt(parts[1], 0),
      category: asInt(parts[2], 0),
      dataId: asInt(parts[3], -1),
      res: asInt(parts[5], 0),
      img: asInt(parts[6], -1),
      seb: asInt(parts[7], -1),
      frame: asInt(parts[8], 0),
      natureId: asInt(parts[9], -1),
      natureGroupId: asInt(parts[10], -1),
      name: parts[4] ?? "",
    });
  }
  return rows;
}

function parseInfTable(text: string): Map<number, string> {
  const result = new Map<number, string>();
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.replace(/^\ufeff/, "").trim();
    if (!line) {
      continue;
    }
    const parts = line.split("\t");
    const id = asInt(parts[0], -1);
    if (id < 0 || !parts[1]) {
      continue;
    }
    const token = parts[1].split(",")[0]?.trim();
    if (!token) {
      continue;
    }
    result.set(id, token);
  }
  return result;
}

function parseImageAtlas(text: string): Map<string, AtlasRegion> {
  const regions = new Map<string, AtlasRegion>();
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.replace(/^\ufeff/, "").trim();
    if (!line) {
      continue;
    }
    const parts = line.split(",").map((entry) => entry.trim());
    if (parts.length < 5) {
      continue;
    }
    const name = parts[0];
    if (!name) {
      continue;
    }
    regions.set(name, {
      name,
      x: asInt(parts[1], 0),
      y: asInt(parts[2], 0),
      width: asInt(parts[3], 0),
      height: asInt(parts[4], 0),
    });
  }
  return regions;
}

function parseOptSequential(buffer: ArrayBuffer): OptMetadata {
  const bytes = new Uint8Array(buffer);
  if (bytes.length < 4) {
    return {
      cellWidth: 48,
      cellHeight: 36,
      cols: 0,
      rows: 0,
      slots: [],
    };
  }

  const view = new DataView(buffer);
  const cellWidth = bytes[0];
  const cellHeight = bytes[1];
  const cols = bytes[2];
  const rows = bytes[3];
  let offset = 4;
  const slots: OptSlot[] = [];

  for (let v = 0; v < rows; v += 1) {
    for (let u = 0; u < cols; u += 1) {
      if (offset >= bytes.length) {
        slots.push({
          u,
          v,
          destX: 0,
          destY: 0,
          srcX: 0,
          srcY: 0,
          width: 0,
          height: 0,
          empty: true,
        });
        continue;
      }

      const flag = bytes[offset];
      if (flag === 0) {
        slots.push({
          u,
          v,
          destX: 0,
          destY: 0,
          srcX: 0,
          srcY: 0,
          width: 0,
          height: 0,
          empty: true,
        });
        offset += 1;
        continue;
      }

      if (flag === 1 && offset + 15 <= bytes.length) {
        const destX = view.getUint16(offset + 4, true);
        const destY = view.getUint16(offset + 6, true);
        const srcX = view.getUint16(offset + 8, true);
        const srcY = view.getUint16(offset + 10, true);
        const width = view.getUint16(offset + 12, true);
        const height = bytes[offset + 14];
        slots.push({
          u,
          v,
          destX,
          destY,
          srcX,
          srcY,
          width,
          height,
          empty: false,
        });
        offset += 15;
        continue;
      }

      slots.push({
        u,
        v,
        destX: 0,
        destY: 0,
        srcX: 0,
        srcY: 0,
        width: 0,
        height: 0,
        empty: true,
      });
      offset += 1;
    }
  }

  return {
    cellWidth,
    cellHeight,
    cols,
    rows,
    slots,
  };
}

function parseSeb(buffer: ArrayBuffer): SebFile {
  const view = new DataView(buffer);
  const totalBytes = buffer.byteLength;
  if (totalBytes < 4) {
    return { blockCount: 0, headerValue: 0, blocks: [] };
  }

  const blockCount = view.getUint16(0, false);
  const headerValue = view.getUint16(2, false);
  let offset = 4;
  const blocks: SebBlock[] = [];

  for (let blockIndex = 0; blockIndex < blockCount; blockIndex += 1) {
    if (offset + 4 > totalBytes) {
      break;
    }

    const frameCount = view.getUint16(offset, false);
    const period = view.getUint16(offset + 2, false);
    offset += 4;

    const records: SebRecord[] = [];
    for (let frameIndex = 0; frameIndex < frameCount; frameIndex += 1) {
      if (offset + 20 > totalBytes) {
        break;
      }

      const tick = view.getInt16(offset, false);
      const sourceId = view.getInt16(offset + 2, false);
      const srcX = view.getInt16(offset + 4, false);
      const srcY = view.getInt16(offset + 6, false);
      const width = view.getInt16(offset + 8, false);
      const height = view.getInt16(offset + 10, false);
      const offsetX = view.getInt16(offset + 12, false);
      const offsetY = view.getInt16(offset + 14, false);
      offset += 20;

      records.push({
        frameIndex,
        tick,
        sourceId,
        srcX,
        srcY,
        width,
        height,
        offsetX,
        offsetY,
      });
    }

    blocks.push({
      blockIndex,
      period,
      records,
    });
  }

  return {
    blockCount,
    headerValue,
    blocks,
  };
}

function worldToIso(x: number, y: number, zoom: number, offsetX: number, offsetY: number): { x: number; y: number } {
  const halfW = (TILE_WIDTH * zoom) / 2;
  const halfH = (TILE_HEIGHT * zoom) / 2;
  return {
    x: offsetX + (x - y) * halfW,
    y: offsetY + (x + y) * halfH,
  };
}

function pointerToWorld(
  event: React.PointerEvent<HTMLCanvasElement>,
  canvas: HTMLCanvasElement,
  camera: CameraState,
): { x: number; y: number } | null {
  return pointerToWorldFromClient(event.clientX, event.clientY, canvas, camera);
}

function pointerToWorldFromClient(
  clientX: number,
  clientY: number,
  canvas: HTMLCanvasElement,
  camera: CameraState,
  centered = false,
): { x: number; y: number } | null {
  const rect = canvas.getBoundingClientRect();
  const localX = clientX - rect.left;
  const localY = clientY - rect.top;

  const halfW = (TILE_WIDTH * camera.zoom) / 2;
  const halfH = (TILE_HEIGHT * camera.zoom) / 2;
  if (halfW <= 0 || halfH <= 0) {
    return null;
  }

  const isoX = localX - camera.offsetX;
  const isoY = localY - camera.offsetY;
  const worldX = (isoX / halfW + isoY / halfH) / 2;
  const worldY = (isoY / halfH - isoX / halfW) / 2;
  const x = centered ? Math.round(worldX) : Math.floor(worldX);
  const y = centered ? Math.round(worldY) : Math.floor(worldY);
  if (x < 0 || y < 0) {
    return null;
  }

  return { x, y };
}

function pointerToWorldFloatFromClient(
  clientX: number,
  clientY: number,
  canvas: HTMLCanvasElement,
  camera: CameraState,
): { x: number; y: number } | null {
  const rect = canvas.getBoundingClientRect();
  const localX = clientX - rect.left;
  const localY = clientY - rect.top;

  const halfW = (TILE_WIDTH * camera.zoom) / 2;
  const halfH = (TILE_HEIGHT * camera.zoom) / 2;
  if (halfW <= 0 || halfH <= 0) {
    return null;
  }

  const isoX = localX - camera.offsetX;
  const isoY = localY - camera.offsetY;
  return {
    x: (isoX / halfW + isoY / halfH) / 2,
    y: (isoY / halfH - isoX / halfW) / 2,
  };
}

function roundToQuarter(value: number): number {
  return Math.round(value * 4) / 4;
}

function hitTestPortGatePieceAtClient(
  clientX: number,
  clientY: number,
  canvas: HTMLCanvasElement,
  camera: CameraState,
  portGateLayout: PortGateLayout,
  portAssetCache?: Map<string, LoadedImageAsset>,
): PortGatePiece["assetKey"] | null {
  const rect = canvas.getBoundingClientRect();
  const localX = clientX - rect.left;
  const localY = clientY - rect.top;
  const zoom = camera.zoom;

  for (let portIndex = PORT_ASSEMBLIES.length - 1; portIndex >= 0; portIndex -= 1) {
    const port = PORT_ASSEMBLIES[portIndex];
    const base = getPortCompositeBaseCell(port);
    const baseX = base.x;
    const baseY = base.y;

    for (let pieceIndex = PORT_GATE_PIECES.length - 1; pieceIndex >= 0; pieceIndex -= 1) {
      const piece = PORT_GATE_PIECES[pieceIndex];
      const offset = portGateLayout[piece.assetKey] ?? { dx: piece.dx, dy: piece.dy };
      const iso = worldToIso(baseX + offset.dx, baseY + offset.dy, zoom, camera.offsetX, camera.offsetY);
      const diamondCenterY = iso.y + (TILE_HEIGHT * zoom) / 2;
      const asset = portAssetCache?.get(piece.assetKey);

      if (asset?.opt) {
        const opt = asset.opt;
        const cellDrawW = opt.cellWidth * zoom;
        const cellDrawH = opt.cellHeight * zoom;
        const cellDrawX = iso.x - cellDrawW / 2;
        const cellDrawY = iso.y + (24 - 116 - (port.cellY === 36 && piece.chipId === 67 ? 2 : 3)) * zoom;
        const commands = getOptDrawCommands(opt, 0, 0, { x: cellDrawX, y: cellDrawY, width: cellDrawW, height: cellDrawH });
        if (commands.some(({ destination: d }) => localX >= d.x && localX <= d.x + d.width && localY >= d.y && localY <= d.y + d.height)) {
          return piece.assetKey;
        }
      }

      const inFootprint =
        Math.abs((localX - iso.x) / (TILE_WIDTH * zoom)) +
          Math.abs((localY - diamondCenterY) / (TILE_HEIGHT * zoom)) <=
        1.05;
      if (inFootprint) {
        return piece.assetKey;
      }
    }
  }

  return null;
}

function drawIsoDiamond(context: CanvasRenderingContext2D, centerX: number, centerY: number, halfW: number, halfH: number) {
  context.beginPath();
  context.moveTo(centerX, centerY - halfH);
  context.lineTo(centerX + halfW, centerY);
  context.lineTo(centerX, centerY + halfH);
  context.lineTo(centerX - halfW, centerY);
  context.closePath();
  context.stroke();
}

function drawOnePieceFacilityOverlays(
  context: CanvasRenderingContext2D,
  facilityBuildingCache: Map<number, LoadedImageAsset>,
  camera: CameraState,
  canvasWidth: number,
  canvasHeight: number,
  showLabels: boolean,
  showFootprint: boolean,
  facilities: FacilityOverlay[],
) {
  const zoom = camera.zoom;
  const footprintHalfW = TILE_WIDTH * zoom;
  const footprintHalfH = TILE_HEIGHT * zoom;
  // halfH_tile: half the height of one isometric tile.
  // worldToIso(cellX, cellY) returns the CENTER of the NW tile, but the 2x2 bounding
  // diamond is centered one tile-halfH south of that point.  Shifting by halfH_tile
  // corrects both the yellow footprint box and the building sprite bottom contact.
  const halfH_tile = (TILE_HEIGHT * zoom) / 2;

  context.save();
  for (const facility of facilities) {
    // cellX/cellY stores the exclusive-end of the 2x2 footprint (NW = cellX-2, cellY-2).
    const iso = worldToIso(facility.originX, facility.originY, zoom, camera.offsetX, camera.offsetY);
    // The correct 2x2 diamond center is one tile-halfH below the NW tile center.
    const diamondCenterY = iso.y + halfH_tile;
    if (iso.x < -96 || iso.x > canvasWidth + 96 || diamondCenterY < -128 || diamondCenterY > canvasHeight + 96) {
      continue;
    }

    const facilityStyle = facility.id === 172 ? OVERLAY_STYLES.aiCorrected : null;
    if (showFootprint) {
      if (facilityStyle) {
        context.fillStyle = facilityStyle.footprintFill;
        context.strokeStyle = facilityStyle.footprintStroke;
      } else {
        context.fillStyle = "rgba(250, 204, 21, 0.16)";
        context.strokeStyle = "rgba(250, 204, 21, 0.9)";
      }
      context.lineWidth = Math.max(1, 1.5 * zoom);
      context.beginPath();
      context.moveTo(iso.x,                  diamondCenterY - footprintHalfH); // N vertex
      context.lineTo(iso.x + footprintHalfW, diamondCenterY);                  // E vertex
      context.lineTo(iso.x,                  diamondCenterY + footprintHalfH); // S vertex
      context.lineTo(iso.x - footprintHalfW, diamondCenterY);                  // W vertex
      context.closePath();
      context.fill();
      context.stroke();
    }

    if (facilityStyle && showFootprint) {
      context.strokeStyle = facilityStyle.anchorStroke;
      context.lineWidth = Math.max(2, 2 * zoom);
      const anchorRadius = Math.max(4, 5 * zoom);
      context.beginPath();
      context.arc(iso.x, iso.y, anchorRadius, 0, Math.PI * 2);
      context.stroke();
      context.beginPath();
      context.moveTo(iso.x - anchorRadius, iso.y);
      context.lineTo(iso.x + anchorRadius, iso.y);
      context.moveTo(iso.x, iso.y - anchorRadius);
      context.lineTo(iso.x, iso.y + anchorRadius);
      context.stroke();
    }

    const building = facilityBuildingCache.get(facility.id)?.image;

    const opt = facilityBuildingCache.get(facility.id)?.opt;
    if (building && opt) {
      const anchor = worldToIso(facility.originX + 1, facility.originY + 1, zoom, camera.offsetX, camera.offsetY);
      for (const { source: source, destination: d } of getOptDrawCommands(opt, 0, 0, {
        x: anchor.x + facility.spriteOffsetX * zoom, y: anchor.y + (facility.spriteOffsetY - facility.baseHeightPixels) * zoom,
        width: opt.cellWidth * zoom, height: opt.cellHeight * zoom,
      })) {
        context.drawImage(building, source.x, source.y, source.width, source.height, d.x, d.y, d.width, d.height);
      }
    } else {
      const markerSize = Math.max(22, Math.min(44, TILE_WIDTH * zoom));
      context.fillStyle = "#facc15";
      context.beginPath();
      context.arc(iso.x, diamondCenterY - footprintHalfH, markerSize * 0.42, 0, Math.PI * 2);
      context.fill();
      context.fillStyle = "#0f172a";
      context.font = `bold ${Math.max(10, markerSize * 0.32)}px sans-serif`;
      context.textAlign = "center";
      context.textBaseline = "middle";
      context.fillText(String(facility.id), iso.x, diamondCenterY - footprintHalfH);
    }

    if (facilityStyle && showLabels) {
      const labelLines = [
        `${facility.name}`,
        `facility ${facility.id} mapChip ${facility.buildingImageId}`,
        `tile (${facility.cellX - 2}, ${facility.cellY - 2})`,
      ];
      drawOverlayLabelBackground(
        context,
        canvasWidth - 10,
        90,
        labelLines,
        facilityStyle.labelBackground,
        zoom,
        "right",
      );
    } else if (zoom >= 0.7) {
      context.font = `bold ${Math.max(10, 11 * zoom)}px sans-serif`;
      context.textAlign = "center";
      context.textBaseline = "top";
      context.lineWidth = 3;
      context.strokeStyle = "rgba(15,23,42,0.95)";
      context.fillStyle = "#fde68a";
      context.strokeText(facility.name, iso.x, diamondCenterY + footprintHalfH + 2);
      context.fillText(facility.name, iso.x, diamondCenterY + footprintHalfH + 2);
    }
  }
  context.restore();
}

function drawOnePieceFacilityNoOffsetDuplicates(
  context: CanvasRenderingContext2D,
  facilityBuildingCache: Map<number, LoadedImageAsset>,
  camera: CameraState,
  canvasWidth: number,
  canvasHeight: number,
  showLabels: boolean,
  facilities: FacilityOverlay[],
) {
  const zoom = camera.zoom;
  const footprintHalfW = TILE_WIDTH * zoom;
  const footprintHalfH = TILE_HEIGHT * zoom;
  const halfH_tile = (TILE_HEIGHT * zoom) / 2;

  context.save();
  for (const facility of facilities) {
    // Today's RE finding: do not apply a blanket (-2,-2) anchor correction.
    const iso = worldToIso(facility.cellX, facility.cellY, zoom, camera.offsetX, camera.offsetY);
    const diamondCenterY = iso.y + halfH_tile;
    if (iso.x < -96 || iso.x > canvasWidth + 96 || diamondCenterY < -128 || diamondCenterY > canvasHeight + 96) {
      continue;
    }

    context.fillStyle = "rgba(59, 130, 246, 0.14)";
    context.strokeStyle = "rgba(59, 130, 246, 0.92)";
    context.lineWidth = Math.max(1, 1.5 * zoom);
    context.beginPath();
    context.moveTo(iso.x,                  diamondCenterY - footprintHalfH);
    context.lineTo(iso.x + footprintHalfW, diamondCenterY);
    context.lineTo(iso.x,                  diamondCenterY + footprintHalfH);
    context.lineTo(iso.x - footprintHalfW, diamondCenterY);
    context.closePath();
    context.fill();
    context.stroke();

    const building = facilityBuildingCache.get(facility.id)?.image;
    if (building) {
      const drawW = building.width * zoom;
      const drawH = building.height * zoom;
      const drawX = iso.x - drawW / 2;
      const drawY = diamondCenterY + footprintHalfH - drawH;
      const previousAlpha = context.globalAlpha;
      context.globalAlpha = 0.82;
      context.drawImage(building, drawX, drawY, drawW, drawH);
      context.globalAlpha = previousAlpha;
    }

    if (showLabels && zoom >= 0.7) {
      context.font = `bold ${Math.max(10, 11 * zoom)}px sans-serif`;
      context.textAlign = "center";
      context.textBaseline = "top";
      context.lineWidth = 3;
      context.strokeStyle = "rgba(15,23,42,0.95)";
      context.fillStyle = "#93c5fd";
      context.strokeText(`${facility.name} (dup no -2,-2)`, iso.x, diamondCenterY + footprintHalfH + 2);
      context.fillText(`${facility.name} (dup no -2,-2)`, iso.x, diamondCenterY + footprintHalfH + 2);
    }
  }
  context.restore();
}

function drawFacilityPlacementTestOverlays(
  context: CanvasRenderingContext2D,
  facilityBuildingCache: Map<number, LoadedImageAsset>,
  camera: CameraState,
  canvasWidth: number,
  canvasHeight: number,
) {
  const zoom = camera.zoom;
  const halfW = (TILE_WIDTH * zoom) / 2;
  const halfH = (TILE_HEIGHT * zoom) / 2;
  const tileHalfH = (TILE_HEIGHT * zoom) / 2;

  context.save();
  for (const overlay of DEBUG_FACILITY_PLACEMENT_OVERLAYS) {
    const iso = worldToIso(overlay.tileX, overlay.tileY, zoom, camera.offsetX, camera.offsetY);
    const diamondCenterY = iso.y + tileHalfH;
    const footprintHalfW = TILE_WIDTH * zoom;
    const footprintHalfH = TILE_HEIGHT * zoom;
    const isVisible = iso.x >= -128 && iso.x <= canvasWidth + 128 && diamondCenterY >= -128 && diamondCenterY <= canvasHeight + 128;
    if (!isVisible) {
      continue;
    }

    const style = OVERLAY_STYLES.manualCorrected;
    context.strokeStyle = style.footprintStroke;
    context.fillStyle = style.footprintFill;
    context.lineWidth = Math.max(1, 1.5 * zoom);
    drawIsoDiamond(context, iso.x, diamondCenterY, footprintHalfW, footprintHalfH);
    context.fill();
    context.stroke();

    const building = facilityBuildingCache.get(overlay.facilityId)?.image;
    if (building) {
      const drawW = building.width * zoom;
      const drawH = building.height * zoom;
      const drawX = iso.x - drawW / 2;
      const drawY = diamondCenterY + footprintHalfH - drawH;
      context.shadowColor = "rgba(0,0,0,0.35)";
      context.shadowBlur = 4;
      context.drawImage(building, drawX, drawY, drawW, drawH);
      context.shadowBlur = 0;
    } else {
      context.fillStyle = "rgba(59, 130, 246, 0.75)";
      context.beginPath();
      context.arc(iso.x, iso.y, Math.max(6, 8 * zoom), 0, Math.PI * 2);
      context.fill();
    }

    // Anchor marker at the placement tile origin.
    context.strokeStyle = style.anchorStroke;
    context.lineWidth = Math.max(2, 2 * zoom);
    const anchorRadius = Math.max(4, 5 * zoom);
    context.beginPath();
    context.arc(iso.x, iso.y, anchorRadius, 0, Math.PI * 2);
    context.stroke();
    context.beginPath();
    context.moveTo(iso.x - anchorRadius, iso.y);
    context.lineTo(iso.x + anchorRadius, iso.y);
    context.moveTo(iso.x, iso.y - anchorRadius);
    context.lineTo(iso.x, iso.y + anchorRadius);
    context.stroke();

    drawOverlayLabelBackground(
      context,
      canvasWidth - 10,
      90,
      [
        overlay.name,
        `facility ${overlay.facilityId} mapChip ${overlay.mapChipId}`,
        `tile (${overlay.tileX}, ${overlay.tileY})`,
      ],
      style.labelBackground,
      zoom,
      "right",
    );
  }
  context.restore();
}

function drawRawFacilityOccupancySpriteOverlay(
  context: CanvasRenderingContext2D,
  facilityBuildingCache: Map<number, LoadedImageAsset>,
  camera: CameraState,
  canvasWidth: number,
  canvasHeight: number,
  overlay: DebugFacilityPlacementOverlay,
  style: OverlayStyle,
  labelYOffset: number,
) {
  const zoom = camera.zoom;
  const tileHalfH = (TILE_HEIGHT * zoom) / 2;
  const footprintSizeW = overlay.sizeWidth ?? 2;
  const footprintSizeH = overlay.sizeHeight ?? 2;
  const iso = worldToIso(overlay.tileX, overlay.tileY, zoom, camera.offsetX, camera.offsetY);
  const footprintCenterY = iso.y + tileHalfH * (footprintSizeH - 1);
  const footprintHalfW = (TILE_WIDTH * footprintSizeW * zoom) / 2;
  const footprintHalfH = (TILE_HEIGHT * footprintSizeH * zoom) / 2;
  const visible = iso.x >= -160 && iso.x <= canvasWidth + 160 && footprintCenterY >= -160 && footprintCenterY <= canvasHeight + 160;
  if (!visible) {
    return;
  }

  // Occupancy layer: show the footprint cells that are reserved by the facility.
  context.save();
  context.strokeStyle = style.footprintStroke;
  context.fillStyle = style.footprintFill;
  context.lineWidth = Math.max(1, 1.5 * zoom);
  drawIsoDiamond(context, iso.x, footprintCenterY, footprintHalfW, footprintHalfH);
  context.fill();
  context.stroke();
  context.restore();

  // Raw logical coordinate marker.
  const rawMarkerHalfW = Math.max(6, (TILE_WIDTH * zoom) / 4);
  const rawMarkerHalfH = Math.max(4, (TILE_HEIGHT * zoom) / 4);
  context.save();
  context.strokeStyle = style.anchorStroke;
  context.lineWidth = Math.max(2, 2 * zoom);
  drawIsoDiamond(context, iso.x, iso.y, rawMarkerHalfW, rawMarkerHalfH);
  context.stroke();
  context.restore();

  // Sprite draw origin and bounds layer: do not draw the sprite texture itself.
  const spriteOriginX = iso.x;
  const spriteOriginY = footprintCenterY + footprintHalfH;
  const building = facilityBuildingCache.get(overlay.facilityId)?.image;
  const drawW = building ? building.width * zoom : TILE_WIDTH * footprintSizeW * zoom;
  const drawH = building ? building.height * zoom : TILE_HEIGHT * footprintSizeH * zoom;
  const drawX = spriteOriginX - drawW / 2;
  const drawY = spriteOriginY - drawH;

  context.save();
  context.strokeStyle = style.anchorStroke;
  context.lineWidth = Math.max(2, 2 * zoom);
  context.strokeRect(drawX + 0.5, drawY + 0.5, drawW - 1, drawH - 1);
  context.beginPath();
  context.arc(spriteOriginX, spriteOriginY, Math.max(4, 5 * zoom), 0, Math.PI * 2);
  context.fillStyle = style.anchorStroke;
  context.fill();
  context.restore();

  const occupiedCells: string[] = [];
  for (let dx = 0; dx < footprintSizeW; dx += 1) {
    for (let dy = 0; dy < footprintSizeH; dy += 1) {
      occupiedCells.push(`(${overlay.tileX + dx}, ${overlay.tileY + dy})`);
    }
  }

  drawOverlayLabelBackground(
    context,
    canvasWidth - 10,
    labelYOffset,
    [
      `${overlay.name}`,
      "OCCUPIED TILES",
      `raw tile: (${overlay.tileX}, ${overlay.tileY})`,
      `occupied: ${occupiedCells.join(", ")}`,
      `footprint: ${footprintSizeW}x${footprintSizeH}`,
    ],
    style.labelBackground,
    zoom,
    "right",
  );

  drawOverlayLabelBackground(
    context,
    canvasWidth - 10,
    labelYOffset + 120,
    [
      "SPRITE DRAW ORIGIN",
      `origin px: (${Math.round(spriteOriginX)}, ${Math.round(spriteOriginY)})`,
      `draw rect: (${Math.round(drawX)}, ${Math.round(drawY)})`,
      `image size: ${Math.round(drawW)}x${Math.round(drawH)}`,
    ],
    style.labelBackground,
    zoom,
    "right",
  );
}

function drawRawRankingBoardPlacementTestOverlays(
  context: CanvasRenderingContext2D,
  facilityBuildingCache: Map<number, LoadedImageAsset>,
  camera: CameraState,
  canvasWidth: number,
  canvasHeight: number,
  style: OverlayStyle,
) {
  for (const overlay of DEBUG_RAW_RANKING_BOARD_OVERLAYS) {
    drawRawFacilityOccupancySpriteOverlay(
      context,
      facilityBuildingCache,
      camera,
      canvasWidth,
      canvasHeight,
      overlay,
      style,
      90,
    );
  }
}

function drawRawLegendaryCavePlacementTestOverlays(
  context: CanvasRenderingContext2D,
  facilityBuildingCache: Map<number, LoadedImageAsset>,
  camera: CameraState,
  canvasWidth: number,
  canvasHeight: number,
  style: OverlayStyle,
) {
  for (const overlay of DEBUG_RAW_LEGENDARY_CAVE_OVERLAYS) {
    drawRawFacilityOccupancySpriteOverlay(
      context,
      facilityBuildingCache,
      camera,
      canvasWidth,
      canvasHeight,
      overlay,
      style,
      260,
    );
  }
}

function drawPortAssemblies(
  context: CanvasRenderingContext2D,
  portAssetCache: Map<string, LoadedImageAsset>,
  camera: CameraState,
  canvasWidth: number,
  canvasHeight: number,
  portGateLayout: PortGateLayout,
  showBridgePieces: boolean,
  showLabels: boolean,
) {
  const zoom = camera.zoom;
  const halfTileH = (TILE_HEIGHT * zoom) / 2;
  const pieceHalfW = TILE_WIDTH * zoom;
  const pieceHalfH = TILE_HEIGHT * zoom;
  const portHalfW = TILE_WIDTH * zoom * 2;
  const portHalfH = TILE_HEIGHT * zoom * 2;

  context.save();
  for (const port of PORT_ASSEMBLIES) {
    const base = getPortCompositeBaseCell(port);
    const baseX = base.x;
    const baseY = base.y;
    const baseIso = worldToIso(baseX, baseY, zoom, camera.offsetX, camera.offsetY);
    if (baseIso.x < -320 || baseIso.x > canvasWidth + 320 || baseIso.y < -260 || baseIso.y > canvasHeight + 180) {
      continue;
    }

    const totalCenterIso = worldToIso(baseX, baseY, zoom, camera.offsetX, camera.offsetY);
    const totalCenterY = totalCenterIso.y + halfTileH * 3;

    if (showBridgePieces) {
      const depth = (x: number, y: number, height: number, extra: number) =>
        100 * (y * 160 + x) + 24 * (x + y) + height + extra;
      const draws = PORT_BRIDGE_PIECES.map(piece => ({
        x: baseX + piece.dx, y: baseY + piece.dy, key: "hashi00", height: 0,
        sprite: nativeMapGround.bridgeFloorSprite,
        depth: depth(baseX + piece.dx, baseY + piece.dy, 0, 10),
      }));
      const nativePort = nativeMapGround.ports.find(p => p.x === baseX && p.y === baseY);
      for (const [x, y, direction] of nativePort?.railings ?? []) {
        draws.push({ x, y, key: "bridge_wall_00", height: 8,
          sprite: nativeMapGround.bridgeWallSprites[direction],
          depth: depth(x, y, 8, [-10, 124, 16024, -10][direction] + direction) });
      }
      for (const draw of draws.sort((a, b) => a.depth - b.depth)) {
        const loaded = portAssetCache.get(draw.key);
        if (!loaded?.opt) continue;
        const iso = worldToIso(draw.x, draw.y, zoom, camera.offsetX, camera.offsetY);
        for (const { source: s, destination: d } of getSebOptDrawCommands(
          loaded.opt, draw.sprite, { x: iso.x / zoom, y: iso.y / zoom - draw.height }, zoom)) {
          context.drawImage(loaded.image, s.x, s.y, s.width, s.height, d.x, d.y, d.width, d.height);
        }
      }
    }

    const gateDraws = PORT_GATE_PIECES.map((piece) => {
      const asset = portAssetCache.get(piece.assetKey)?.image ?? null;
      const offset = portGateLayout[piece.assetKey] ?? { dx: piece.dx, dy: piece.dy };
      const worldX = baseX + offset.dx;
      const worldY = baseY + offset.dy;
      const iso = worldToIso(worldX, worldY, zoom, camera.offsetX, camera.offsetY);
      const diamondCenterY = iso.y + halfTileH;
      return {
        piece,
        asset,
        offset,
        iso,
        diamondCenterY,
        groundY: diamondCenterY + pieceHalfH,
        depth: worldX + worldY,
      };
    }).sort((a, b) => a.groundY - b.groundY || a.depth - b.depth);

    for (const draw of gateDraws) {
      const { piece, asset, iso, diamondCenterY } = draw;
      if (!asset) {
        continue;
      }

      const opt = portAssetCache.get(piece.assetKey)?.opt;
      if (!opt) continue;
      const cellDrawW = opt.cellWidth * zoom;
      const cellDrawH = opt.cellHeight * zoom;
      const cellDrawX = iso.x - cellDrawW / 2;
      const cellDrawY = iso.y + (24 - 116 - (port.cellY === 36 && piece.chipId === 67 ? 2 : 3)) * zoom;
      context.shadowColor = "rgba(0,0,0,0.35)";
      context.shadowBlur = 4;
      for (const { source: s, destination: d } of getOptDrawCommands(opt, 0, 0,
        { x: cellDrawX, y: cellDrawY, width: cellDrawW, height: cellDrawH })) {
        context.drawImage(asset, s.x, s.y, s.width, s.height, d.x, d.y, d.width, d.height);
      }
      context.shadowBlur = 0;

    }

    if (showLabels && zoom >= 0.7) {
      context.font = `bold ${Math.max(10, 11 * zoom)}px sans-serif`;
      context.textAlign = "center";
      context.textBaseline = "top";
      context.lineWidth = 3;
      context.strokeStyle = "rgba(15,23,42,0.95)";
      context.fillStyle = "#fde68a";
      const labelY = totalCenterY + Math.max(2, pieceHalfH * 0.2);
      context.strokeText(`Port lv${port.unlockLevel}`, totalCenterIso.x, labelY);
      context.fillText(`Port lv${port.unlockLevel}`, totalCenterIso.x, labelY);
    }
  }
  context.restore();
}

function computeResetCamera(mapWidth: number, mapHeight: number, canvasWidth: number, canvasHeight: number, zoom: number): CameraState {
  const centerIsoY = ((mapWidth / 2 + mapHeight / 2) * TILE_HEIGHT * zoom) / 2;
  return {
    offsetX: canvasWidth / 2,
    offsetY: canvasHeight * 0.62 - centerIsoY,
    zoom,
  };
}

function computeInitialCamera(mapWidth: number, mapHeight: number, canvasWidth: number, canvasHeight: number, defaultZoom: number): CameraState {
  if (typeof window !== "undefined") {
    const focus = new URLSearchParams(window.location.search).get("focus");
    const facility = ONE_PIECE_FACILITY_OVERLAYS.find(f =>
      f.name.toLowerCase().replace(/[^a-z0-9]/g, "") === focus);
    if (facility) {
      return computeCameraForWorldCell(facility.originX + 1, facility.originY + 1,
        canvasWidth, canvasHeight, Math.max(defaultZoom, 1.5));
    }
    const port = focus === "port44" ? PORT_ASSEMBLIES[1] : focus === "port7" || focus === "ports" ? PORT_ASSEMBLIES[0] : null;
    if (port) {
      const zoom = Math.max(defaultZoom, 0.8);
      const targetX = port.cellX + 1;
      const targetY = port.cellY - 1;
      return computeCameraForWorldCell(targetX, targetY, canvasWidth, canvasHeight, zoom);
    }
  }

  return computeResetCamera(mapWidth, mapHeight, canvasWidth, canvasHeight, defaultZoom);
}

function computeCameraForWorldCell(cellX: number, cellY: number, canvasWidth: number, canvasHeight: number, zoom: number): CameraState {
  const isoX = ((cellX - cellY) * TILE_WIDTH * zoom) / 2;
  const isoY = ((cellX + cellY) * TILE_HEIGHT * zoom) / 2;
  return {
    offsetX: canvasWidth / 2 - isoX,
    offsetY: canvasHeight * 0.52 - isoY,
    zoom,
  };
}

function drawGridBackground(context: CanvasRenderingContext2D, width: number, height: number) {
  context.strokeStyle = "rgba(255,255,255,0.05)";
  for (let x = 0; x < width; x += 32) {
    context.beginPath();
    context.moveTo(x, 0);
    context.lineTo(x, height);
    context.stroke();
  }
  for (let y = 0; y < height; y += 32) {
    context.beginPath();
    context.moveTo(0, y);
    context.lineTo(width, y);
    context.stroke();
  }
}

function getParsedCellAt(parsed: NativeMapBinary, x: number, y: number): NativeMapCell | null {
  if (x < 0 || y < 0 || x >= parsed.width || y >= parsed.height) {
    return null;
  }
  const index = y * parsed.width + x;
  return parsed.cells[index] ?? null;
}

async function fetchText(path: string): Promise<string> {
  const response = await fetch(path);
  console.log("[runtime-world-render-test] fetchText", path, response.status, response.headers.get("Content-Type"));
  if (!response.ok) {
    throw new Error(`Failed to load ${path}: HTTP ${response.status}`);
  }
  return response.text();
}

async function fetchArrayBuffer(path: string): Promise<ArrayBuffer> {
  const response = await fetch(path);
  console.log("[runtime-world-render-test] fetchArrayBuffer", path, response.status, response.headers.get("Content-Type"));
  if (!response.ok) {
    throw new Error(`Failed to load ${path}: HTTP ${response.status}`);
  }
  return response.arrayBuffer();
}

async function fetchArrayBufferOptional(path: string): Promise<ArrayBuffer | null> {
  const response = await fetch(path);
  console.log("[runtime-world-render-test] fetchArrayBufferOptional", path, response.status, response.headers.get("Content-Type"));
  if (!response.ok) {
    return null;
  }
  return response.arrayBuffer();
}

function loadImage(path: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error(`Failed to load image ${path}`));
    image.src = path;
  });
}

function asInt(value: string | undefined, fallback: number): number {
  if (value === undefined) {
    return fallback;
  }
  const trimmed = value.trim();
  if (!trimmed) {
    return fallback;
  }
  const numeric = Number.parseInt(trimmed, 10);
  if (Number.isFinite(numeric)) {
    return numeric;
  }
  const floatValue = Number.parseFloat(trimmed);
  if (Number.isFinite(floatValue)) {
    return Math.trunc(floatValue);
  }
  return fallback;
}

function buildRenderDiagnostics(commands: TileDrawCommand[]): RenderDiagnostics {
  const rawValues = new Set<number>();
  const chipIds = new Set<number>();
  const imgIds = new Set<number>();
  const resolvedPngNames = new Set<string>();
  const drawnPngNames = new Set<string>();
  const sourceRects = new Set<string>();

  const mapChipNameCounts = new Map<string, number>();
  const imgIdCounts = new Map<string, number>();
  const resolvedPngNameCounts = new Map<string, number>();
  const drawnPngNameCounts = new Map<string, number>();

  for (const command of commands) {
    rawValues.add(command.rawMapValue);
    chipIds.add(command.mapChipId);
    imgIds.add(command.mapChipImgId);

    if (command.resolvedImgFilename) {
      resolvedPngNames.add(command.resolvedImgFilename);
      resolvedPngNameCounts.set(command.resolvedImgFilename, (resolvedPngNameCounts.get(command.resolvedImgFilename) ?? 0) + 1);
    }
    if (command.selection.sourceFilename) {
      drawnPngNames.add(command.selection.sourceFilename);
      drawnPngNameCounts.set(command.selection.sourceFilename, (drawnPngNameCounts.get(command.selection.sourceFilename) ?? 0) + 1);
    }

    sourceRects.add(`${command.selection.srcX},${command.selection.srcY},${command.selection.srcW},${command.selection.srcH}`);

    mapChipNameCounts.set(command.mapChipName, (mapChipNameCounts.get(command.mapChipName) ?? 0) + 1);
    imgIdCounts.set(String(command.mapChipImgId), (imgIdCounts.get(String(command.mapChipImgId)) ?? 0) + 1);
  }

  const uniqueMapChipImgValues = imgIds.size;
  const uniqueResolvedPngFilenames = resolvedPngNames.size;
  const uniqueDrawnPngFilenames = drawnPngNames.size;
  const uniqueSourceRects = sourceRects.size;

  let stageDiagnosis = "No commands rendered for diagnostics.";
  if (uniqueMapChipImgValues <= 1) {
    stageDiagnosis = "B) likely wrong Map cell -> MapChip lookup (MapChip.img is effectively single-valued).";
  } else if (uniqueMapChipImgValues > 1 && uniqueResolvedPngFilenames <= 1) {
    stageDiagnosis = "C/D) likely img.inf resolution or PNG cache key issue (many img ids collapse to one filename).";
  } else if (uniqueResolvedPngFilenames > 5 && uniqueDrawnPngFilenames <= 2) {
    stageDiagnosis = "E) Map->MapChip->img chain varies, but draw stage collapses to few PNGs; investigate SEB/OPT source image and source-rect selection.";
  } else if (uniqueSourceRects <= 1) {
    stageDiagnosis = "E) source rectangles are effectively identical (SEB/OPT frame resolution stage).";
  } else if (chipIds.size <= 1 && rawValues.size > 1) {
    stageDiagnosis = "A/B) raw map diversity exists but MapChip lookup collapses to one chip id.";
  } else {
    stageDiagnosis = "Map->MapChip->img->PNG variation is present; remaining mismatch likely in later SEB block selection behavior.";
  }

  return {
    uniqueRawMapValues: rawValues.size,
    uniqueMapChipIds: chipIds.size,
    uniqueMapChipImgValues,
    uniqueResolvedPngFilenames,
    uniqueDrawnPngFilenames,
    uniqueSourceRects,
    topMapChipNames: topEntries(mapChipNameCounts),
    topImgIds: topEntries(imgIdCounts),
    topResolvedPngFilenames: topEntries(resolvedPngNameCounts),
    topDrawnPngFilenames: topEntries(drawnPngNameCounts),
    stageDiagnosis,
  };
}

function topEntries(counts: Map<string, number>, limit = 20): DiagnosticListItem[] {
  return [...counts.entries()]
    .sort((left, right) => {
      if (right[1] !== left[1]) {
        return right[1] - left[1];
      }
      return left[0].localeCompare(right[0]);
    })
    .slice(0, limit)
    .map(([key, count]) => ({ key, count }));
}

function groupRowsByKey<T>(rows: T[], keySelector: (row: T) => number): Map<number, T[]> {
  const grouped = new Map<number, T[]>();
  for (const row of rows) {
    const key = keySelector(row);
    const existing = grouped.get(key);
    if (existing) {
      existing.push(row);
    } else {
      grouped.set(key, [row]);
    }
  }
  return grouped;
}

function classifyDrawGroup(mapChipName: string, resolvedImgFilename: string): "base-terrain" | "nature-object" | "player-made-surface" {
  if (isBaseTerrainFamily(mapChipName, resolvedImgFilename)) {
    return "base-terrain";
  }
  if (isPlayerMadeSurface(mapChipName, resolvedImgFilename)) {
    return "player-made-surface";
  }
  return "nature-object";
}

function isBaseTerrainFamily(mapChipName: string, resolvedImgFilename: string): boolean {
  const tokens = [mapChipName, resolvedImgFilename].map((value) => value.toLowerCase());
  return tokens.some((token) => BASE_TERRAIN_FAMILY_PREFIXES.some((prefix) => token.includes(prefix)));
}

function isPlayerMadeSurface(mapChipName: string, resolvedImgFilename: string): boolean {
  const token = `${mapChipName} ${resolvedImgFilename}`.toLowerCase();
  return /azemichi|road|bridge|bridge_side|construction|site|floor|path|pave|dungeon_floor|genkan|douro|hodou|oudanhodou|rouka|hashi/.test(token);
}

function classifyNatureVisualCategory(row: TerrainRow, natureImageById: Map<number, string>): NatureVisualCategory {
  const filename = natureImageById.get(row.img) ?? "";
  const token = `${row.name} ${filename}`.toLowerCase();
  if (/human_|miner|person|npc|people/.test(token)) {
    return "human-npc";
  }
  if (/special_(01|03|04|07|10)\.png/.test(token)) {
    return "resource-treasure";
  }
  if (/special_(00|02|08|09|11)\.png/.test(token)) {
    return "terrain-nature";
  }
  if (/gem|crystal|ore|chest|treasure|material|resource/.test(token)) {
    return "resource-treasure";
  }
  if (/special_/.test(token)) {
    return "special-unknown";
  }
  return "terrain-nature";
}

function isNatureCommandVisible(command: TileDrawCommand, visibility: NatureCategoryVisibility): boolean {
  if (command.drawGroup !== "nature-object") {
    return true;
  }
  return visibility[command.natureCategory ?? "terrain-nature"];
}

function filterNatureCommandsByVisibility(commands: TileDrawCommand[], visibility: NatureCategoryVisibility): TileDrawCommand[] {
  return commands.filter((command) => isNatureCommandVisible(command, visibility));
}

function isWaterTerrainFamily(family: string | null): boolean {
  if (!family) {
    return false;
  }
  return /water|mizu|river|sea|lake|ocean|pool/.test(family.toLowerCase());
}

function isWaterToken(value: string): boolean {
  return /water|mizu|river|sea|lake|ocean|pool/.test(value.toLowerCase());
}

function getF1TerrainTypeName(f1Value: number): string {
  return F1_TERRAIN_TYPE_NAMES[f1Value] ?? `Unknown (${f1Value})`;
}

function getF1TerrainFamily(f1Value: number): string | null {
  return F1_TERRAIN_FAMILY_BY_TYPE[f1Value] ?? null;
}

function findImageFilenameForFamily(imageById: Map<number, string>, family: string): string | null {
  const loweredFamily = family.toLowerCase();
  const filenames = [...imageById.values()]
    .filter((filename) => filename.toLowerCase().includes(loweredFamily))
    .sort((left, right) => left.localeCompare(right));
  return filenames[0] ?? null;
}

function findFallbackBaseFilename(rows: TerrainRow[], imageById: Map<number, string>, family: string | null): string | null {
  if (rows.length === 0) {
    return null;
  }

  const sortedRows = [...rows].sort((left, right) => {
    if (left.id !== right.id) {
      return left.id - right.id;
    }
    return left.img - right.img;
  });

  if (!family) {
    const first = sortedRows[0];
    return first ? imageById.get(first.img) ?? null : null;
  }

  const loweredFamily = family.toLowerCase();
  const familyRow = sortedRows.find((row) => {
    const filename = imageById.get(row.img) ?? "";
    return filename.toLowerCase().includes(loweredFamily) || row.name.toLowerCase().includes(loweredFamily);
  });
  const targetRow = familyRow ?? sortedRows[0];
  return targetRow ? imageById.get(targetRow.img) ?? null : null;
}

function pickTerrainRowForCell(
  rows: TerrainRow[],
  family: string | null,
  imageById: Map<number, string>,
  cellX: number,
  cellY: number,
  fields: ParsedMapCell["fields"],
  mode: F1RowSelectionMode,
): { row: TerrainRow | null; reason: string } {
  if (rows.length === 0) {
    return { row: null, reason: "no category=0 candidate rows" };
  }

  const familyRows = !family
    ? rows
    : rows.filter((row) => {
        const filename = imageById.get(row.img) ?? "";
        const loweredFamily = family.toLowerCase();
        return filename.toLowerCase().includes(loweredFamily) || row.name.toLowerCase().includes(loweredFamily);
      });

  const candidates = familyRows.length > 0 ? familyRows : rows;
  const sortedCandidates = [...candidates].sort((left, right) => {
    if (left.id !== right.id) {
      return left.id - right.id;
    }
    return left.img - right.img;
  });

  if (mode === "map-fields") {
    const scored = sortedCandidates.map((row) => scoreTerrainRowByMapFields(row, fields));
    scored.sort((left, right) => {
      if (right.score !== left.score) {
        return right.score - left.score;
      }
      return left.row.id - right.row.id;
    });
    const best = scored[0];
    if (best) {
      return {
        row: best.row,
        reason: best.reasons.length > 0
          ? `map-fields score=${best.score} (${best.reasons.join(", ")})`
          : `map-fields score=${best.score} (no direct field match, id tie-break)`,
      };
    }
  }

  if (sortedCandidates.length === 1) {
    return { row: sortedCandidates[0] ?? null, reason: "single candidate row" };
  }

  const hash = (((cellX * 73856093) ^ (cellY * 19349663)) >>> 0);
  const selectedIndex = hash % sortedCandidates.length;
  return { row: sortedCandidates[selectedIndex] ?? null, reason: `coord-hash index=${selectedIndex} of ${sortedCandidates.length}` };
}

function scoreTerrainRowByMapFields(row: TerrainRow, fields: ParsedMapCell["fields"]): { row: TerrainRow; score: number; reasons: string[] } {
  let score = 0;
  const reasons: string[] = [];

  if (row.id === fields.f2) {
    score += 100;
    reasons.push("id=f2");
  }
  if (row.natureId >= 0 && row.natureId === fields.f0) {
    score += 70;
    reasons.push("natureId=f0");
  }
  if (row.natureGroupId >= 0 && row.natureGroupId === fields.f3) {
    score += 60;
    reasons.push("natureGroupId=f3");
  }
  if (row.frame === fields.f5) {
    score += 35;
    reasons.push("frame=f5");
  }
  if (row.type === fields.f5) {
    score += 20;
    reasons.push("type=f5");
  }
  if (row.img === fields.f5) {
    score += 15;
    reasons.push("img=f5");
  }
  if (row.dataId >= 0 && row.dataId === fields.f5) {
    score += 20;
    reasons.push("dataId=f5");
  }

  const mapValues = new Set([fields.f0, fields.f1, fields.f2, fields.f3, fields.f4, fields.f5]);
  if (mapValues.has(row.id)) {
    score += 6;
    reasons.push("id in f0..f5");
  }
  if (mapValues.has(row.img)) {
    score += 4;
    reasons.push("img in f0..f5");
  }
  if (mapValues.has(row.frame)) {
    score += 3;
    reasons.push("frame in f0..f5");
  }
  if (row.natureId >= 0 && mapValues.has(row.natureId)) {
    score += 5;
    reasons.push("natureId in f0..f5");
  }
  if (row.natureGroupId >= 0 && mapValues.has(row.natureGroupId)) {
    score += 5;
    reasons.push("natureGroupId in f0..f5");
  }
  if (row.dataId >= 0 && mapValues.has(row.dataId)) {
    score += 4;
    reasons.push("dataId in f0..f5");
  }

  return { row, score, reasons };
}

function selectNatureRowByDirectFields(
  rows: TerrainRow[],
  fields: ParsedMapCell["fields"],
): { row: TerrainRow | null; reason: string; method: "direct-row-id" | "direct-img" | "none"; field: "f0" | "f1" | "f2" | "f3" | "f4" | "f5" | null } {
  const selected = selectNatureRowsByDiscoveredFields(rows, fields)[0];
  if (selected) {
    const selectedField = selected.field.includes("/") ? "f0" : selected.field;
    return {
      row: selected.row,
      reason: selected.reason,
      method: selected.method === "direct-img" ? "direct-img" : "direct-row-id",
      field: selectedField as "f0" | "f1" | "f2" | "f3" | "f4" | "f5",
    };
  }

  return { row: null, reason: "none (no discovered nature field, direct row id, or direct img match)", method: "none", field: null };
}

function selectNatureRowsByDiscoveredFields(
  rows: TerrainRow[],
  fields: ParsedMapCell["fields"],
): NatureRowSelection[] {
  if (rows.length === 0) {
    return [];
  }

  const orderedRows = [...rows].sort((left, right) => left.id - right.id);
  const scored = orderedRows
    .map((row) => scoreNatureRowByDiscoveredFields(row, fields))
    .filter((entry) => entry.score > 0)
    .sort((left, right) => {
      if (right.score !== left.score) return right.score - left.score;
      return left.row.id - right.row.id;
    });

  const exact = scored.filter((entry) => entry.score >= 240);
  if (exact.length > 0) {
    return exact.map((entry) => ({
      row: entry.row,
      reason: `nature fields score=${entry.score} (${entry.reasons.join(", ")})`,
      method: "nature-fields",
      field: "f0/f3/f5",
      score: entry.score,
    }));
  }

  const groupMatches = scored.filter((entry) => entry.score >= 180);
  if (groupMatches.length > 0) {
    return groupMatches.map((entry) => ({
      row: entry.row,
      reason: `nature id/group score=${entry.score} (${entry.reasons.join(", ")})`,
      method: "nature-fields",
      field: "f0/f3",
      score: entry.score,
    }));
  }

  const strongSingles = scored.filter((entry) => entry.score >= 100);
  if (strongSingles.length > 0) {
    return strongSingles.map((entry) => ({
      row: entry.row,
      reason: `single nature field score=${entry.score} (${entry.reasons.join(", ")})`,
      method: "nature-fields",
      field: entry.reasons[0]?.startsWith("natureGroupId") ? "f3" : entry.reasons[0]?.startsWith("frame") ? "f5" : "f0",
      score: entry.score,
    }));
  }

  const fieldEntries: Array<{ key: "f0" | "f1" | "f2" | "f3" | "f4" | "f5"; value: number }> = [
    { key: "f0", value: fields.f0 },
    { key: "f1", value: fields.f1 },
    { key: "f2", value: fields.f2 },
    { key: "f3", value: fields.f3 },
    { key: "f4", value: fields.f4 },
    { key: "f5", value: fields.f5 },
  ];

  for (const field of fieldEntries) {
    const matchedRow = orderedRows.find((row) => row.id === field.value);
    if (matchedRow) {
      return [{
        row: matchedRow,
        reason: `direct row id match (${field.key}=${field.value} -> Terrain.id=${matchedRow.id})`,
        method: "direct-row-id",
        field: field.key,
        score: 40,
      }];
    }
  }

  for (const field of fieldEntries) {
    const matchedRow = orderedRows.find((row) => row.img === field.value);
    if (matchedRow) {
      return [{
        row: matchedRow,
        reason: `direct img match (${field.key}=${field.value} -> Terrain.img=${matchedRow.img})`,
        method: "direct-img",
        field: field.key,
        score: 30,
      }];
    }
  }

  return [];
}

function selectOneNatureRowForTile(
  rowsForTerrainType: TerrainRow[],
  cellX: number,
  cellY: number,
  fields: ParsedMapCell["fields"],
  natureImageById: Map<number, string>,
): NatureRowSelection[] {
  const natureRows = rowsForTerrainType.filter((row) => row.res === 20 && row.type === fields.f1);
  if (natureRows.length === 0) return [];

  const categoryPools = new Map<NatureVisualCategory, TerrainRow[]>();
  for (const row of natureRows) {
    const category = classifyNatureVisualCategory(row, natureImageById);
    const existing = categoryPools.get(category);
    if (existing) existing.push(row);
    else categoryPools.set(category, [row]);
  }

  const chances = NATURE_CATEGORY_CHANCE_BY_TERRAIN_TYPE[fields.f1] ?? { "terrain-nature": 0.12, "resource-treasure": 0.03, "human-npc": 0.01, "special-unknown": 0.01 };
  const orderedCategories = Object.keys(NATURE_CATEGORY_LAYER_ORDER) as NatureVisualCategory[];
  const roll = stableUnitHash(cellX, cellY, fields.f1, 101);
  let threshold = 0;
  let selectedCategory: NatureVisualCategory | null = null;
  for (const category of orderedCategories) {
    if (!categoryPools.has(category)) continue;
    threshold += chances[category] ?? 0;
    if (roll < threshold) {
      selectedCategory = category;
      break;
    }
  }
  if (!selectedCategory) return [];

  const candidates = [...(categoryPools.get(selectedCategory) ?? [])].sort((left, right) => left.id - right.id || left.img - right.img);
  if (candidates.length === 0) return [];

  const index = Math.floor(stableUnitHash(cellX, cellY, fields.f1, 211) * candidates.length) % candidates.length;
  const row = candidates[index];
  if (!row) return [];

  return [{
    row,
    reason: `one visual per tile: f1=${fields.f1} ${NATURE_CATEGORY_LABELS[selectedCategory]} chance=${Math.round((chances[selectedCategory] ?? 0) * 100)}%, row=${row.id}, pool=${candidates.length}`,
    method: "visual-type-pool",
    field: "type-pool",
    score: 1000 - index,
    category: selectedCategory,
  }];
}

function stableUnitHash(cellX: number, cellY: number, terrainType: number, salt: number): number {
  let hash = (cellX * 374761393 + cellY * 668265263 + terrainType * 2246822519 + salt * 3266489917) >>> 0;
  hash = Math.imul(hash ^ (hash >>> 15), 2246822507) >>> 0;
  hash = Math.imul(hash ^ (hash >>> 13), 3266489909) >>> 0;
  return ((hash ^ (hash >>> 16)) >>> 0) / 4294967296;
}

function scoreNatureRowByDiscoveredFields(row: TerrainRow, fields: ParsedMapCell["fields"]): { row: TerrainRow; score: number; reasons: string[] } {
  let score = 0;
  const reasons: string[] = [];

  if (row.natureId >= 0 && row.natureId === fields.f0) {
    score += 100;
    reasons.push("natureId=f0");
  }
  if (row.natureGroupId >= 0 && row.natureGroupId === fields.f3) {
    score += 80;
    reasons.push("natureGroupId=f3");
  }
  if (row.frame === fields.f5) {
    score += 60;
    reasons.push("frame=f5");
  }
  if (row.id === fields.f2) {
    score += 45;
    reasons.push("id=f2");
  }
  if (row.dataId >= 0 && row.dataId === fields.f5) {
    score += 25;
    reasons.push("dataId=f5");
  }

  return { row, score, reasons };
}

function buildDirectFieldMatchMatrix(rows: TerrainRow[], fields: ParsedMapCell["fields"]): Array<{
  field: "f0" | "f1" | "f2" | "f3" | "f4" | "f5";
  value: number;
  rowIdMatches: number[];
  typeMatches: number[];
  categoryMatches: number[];
  imgMatches: number[];
  natureGroupIdMatches: number[];
  dataIdMatches: number[];
  frameMatches: number[];
}> {
  const fieldEntries: Array<{ key: "f0" | "f1" | "f2" | "f3" | "f4" | "f5"; value: number }> = [
    { key: "f0", value: fields.f0 },
    { key: "f1", value: fields.f1 },
    { key: "f2", value: fields.f2 },
    { key: "f3", value: fields.f3 },
    { key: "f4", value: fields.f4 },
    { key: "f5", value: fields.f5 },
  ];

  return fieldEntries.map((field) => ({
    field: field.key,
    value: field.value,
    rowIdMatches: rows.filter((row) => row.id === field.value).map((row) => row.id),
    typeMatches: rows.filter((row) => row.type === field.value).map((row) => row.id),
    categoryMatches: rows.filter((row) => row.category === field.value).map((row) => row.id),
    imgMatches: rows.filter((row) => row.img === field.value).map((row) => row.id),
    natureGroupIdMatches: rows.filter((row) => row.natureGroupId === field.value).map((row) => row.id),
    dataIdMatches: rows.filter((row) => row.dataId === field.value).map((row) => row.id),
    frameMatches: rows.filter((row) => row.frame === field.value).map((row) => row.id),
  }));
}

function compareTerrainRowAgainstMapFields(row: TerrainRow, fields: ParsedMapCell["fields"]): {
  directMatches: string[];
  likelyMatches: string[];
} {
  const directMatches: string[] = [];
  const likelyMatches: string[] = [];

  if (row.id === fields.f0) directMatches.push("id=f0");
  if (row.id === fields.f1) directMatches.push("id=f1");
  if (row.id === fields.f2) directMatches.push("id=f2");
  if (row.id === fields.f3) directMatches.push("id=f3");
  if (row.id === fields.f4) directMatches.push("id=f4");
  if (row.id === fields.f5) directMatches.push("id=f5");

  if (row.type === fields.f0) directMatches.push("type=f0");
  if (row.type === fields.f1) directMatches.push("type=f1");
  if (row.type === fields.f2) directMatches.push("type=f2");
  if (row.type === fields.f3) directMatches.push("type=f3");
  if (row.type === fields.f4) directMatches.push("type=f4");
  if (row.type === fields.f5) directMatches.push("type=f5");

  if (row.category === fields.f0) directMatches.push("category=f0");
  if (row.category === fields.f1) directMatches.push("category=f1");
  if (row.category === fields.f2) directMatches.push("category=f2");
  if (row.category === fields.f3) directMatches.push("category=f3");
  if (row.category === fields.f4) directMatches.push("category=f4");
  if (row.category === fields.f5) directMatches.push("category=f5");

  if (row.natureId >= 0 && row.natureId === fields.f0) directMatches.push("natureId=f0");
  if (row.natureId >= 0 && row.natureId === fields.f1) directMatches.push("natureId=f1");
  if (row.natureId >= 0 && row.natureId === fields.f2) directMatches.push("natureId=f2");
  if (row.natureId >= 0 && row.natureId === fields.f3) directMatches.push("natureId=f3");
  if (row.natureId >= 0 && row.natureId === fields.f4) directMatches.push("natureId=f4");
  if (row.natureId >= 0 && row.natureId === fields.f5) directMatches.push("natureId=f5");

  if (row.natureGroupId >= 0 && row.natureGroupId === fields.f0) directMatches.push("natureGroupId=f0");
  if (row.natureGroupId >= 0 && row.natureGroupId === fields.f1) directMatches.push("natureGroupId=f1");
  if (row.natureGroupId >= 0 && row.natureGroupId === fields.f2) directMatches.push("natureGroupId=f2");
  if (row.natureGroupId >= 0 && row.natureGroupId === fields.f3) directMatches.push("natureGroupId=f3");
  if (row.natureGroupId >= 0 && row.natureGroupId === fields.f4) directMatches.push("natureGroupId=f4");
  if (row.natureGroupId >= 0 && row.natureGroupId === fields.f5) directMatches.push("natureGroupId=f5");

  if (row.img === fields.f0) directMatches.push("img=f0");
  if (row.img === fields.f1) directMatches.push("img=f1");
  if (row.img === fields.f2) directMatches.push("img=f2");
  if (row.img === fields.f3) directMatches.push("img=f3");
  if (row.img === fields.f4) directMatches.push("img=f4");
  if (row.img === fields.f5) directMatches.push("img=f5");

  if (row.frame === fields.f0) directMatches.push("frame=f0");
  if (row.frame === fields.f1) directMatches.push("frame=f1");
  if (row.frame === fields.f2) directMatches.push("frame=f2");
  if (row.frame === fields.f3) directMatches.push("frame=f3");
  if (row.frame === fields.f4) directMatches.push("frame=f4");
  if (row.frame === fields.f5) directMatches.push("frame=f5");

  if (row.dataId >= 0 && row.dataId === fields.f0) directMatches.push("dataId=f0");
  if (row.dataId >= 0 && row.dataId === fields.f1) directMatches.push("dataId=f1");
  if (row.dataId >= 0 && row.dataId === fields.f2) directMatches.push("dataId=f2");
  if (row.dataId >= 0 && row.dataId === fields.f3) directMatches.push("dataId=f3");
  if (row.dataId >= 0 && row.dataId === fields.f4) directMatches.push("dataId=f4");
  if (row.dataId >= 0 && row.dataId === fields.f5) directMatches.push("dataId=f5");

  if (row.natureId >= 0 && row.natureId === fields.f0) likelyMatches.push("likely f0->natureId");
  if (row.id === fields.f2) likelyMatches.push("likely f2->Terrain.id");
  if (row.natureGroupId >= 0 && row.natureGroupId === fields.f3) likelyMatches.push("likely f3->natureGroupId");
  if (row.type === fields.f5) likelyMatches.push("likely f5->type");
  if (row.frame === fields.f5) likelyMatches.push("likely f5->frame");
  if (row.dataId >= 0 && row.dataId === fields.f5) likelyMatches.push("likely f5->dataId");
  if (row.natureId >= 0 && row.natureId === fields.f0 && row.natureGroupId >= 0 && row.natureGroupId === fields.f3) {
    likelyMatches.push("combo f0/f3 -> natureId/natureGroupId");
  }

  return { directMatches, likelyMatches };
}

function formatRenderModeLabel(mode: RenderInterpretationMode): string {
  if (mode === "f1-terrain") {
    return "f1 Terrain Mode";
  }
  if (mode === "terrain-f2") {
    return "f2 as Terrain.id";
  }
  return "f2 as MapChip.id";
}

function nextRenderMode(mode: RenderInterpretationMode): RenderInterpretationMode {
  if (mode === "f1-terrain") {
    return "mapchip-f2";
  }
  if (mode === "mapchip-f2") {
    return "terrain-f2";
  }
  return "f1-terrain";
}

function nextOverlayMode(mode: OverlayMode): OverlayMode {
  if (mode === "both") {
    return "texture-only";
  }
  if (mode === "texture-only") {
    return "diamond-only";
  }
  return "both";
}

function computeTexturePlacement(input: {
  renderMode: RenderInterpretationMode;
  terrainAlignmentMode: TerrainAlignmentMode;
  command: TileDrawCommand;
  isoX: number;
  isoY: number;
  zoom: number;
  clippedSrcW: number;
  clippedSrcH: number;
}): {
  drawX: number;
  drawY: number;
  drawW: number;
  drawH: number;
  clipToDiamond: boolean;
  anchorMode: string;
} {
  const useDiamondFit =
    input.renderMode === "f1-terrain" &&
    input.command.sourceField === "f1" &&
    input.command.drawGroup === "base-terrain" &&
    input.terrainAlignmentMode === "diamond-fit";

  if (useDiamondFit) {
    // Slight overdraw avoids hairline cracks between adjacent tiles when zoomed/scaled.
    const drawW = Math.max(1, Math.ceil(TILE_WIDTH * input.zoom) + 2);
    const drawH = Math.max(1, Math.ceil(TILE_HEIGHT * input.zoom) + 2);
    return {
      drawX: Math.floor(input.isoX - drawW / 2),
      drawY: Math.floor(input.isoY - drawH / 2),
      drawW,
      drawH,
      clipToDiamond: false,
      anchorMode: "diamond-fit-center-bleed",
    };
  }

  return {
    drawX: Math.round(input.isoX + input.command.selection.drawOffsetX * input.zoom),
    drawY: Math.round(input.isoY + input.command.selection.drawOffsetY * input.zoom),
    drawW: Math.max(1, Math.round(input.clippedSrcW * input.zoom)),
    drawH: Math.max(1, Math.round(input.clippedSrcH * input.zoom)),
    clipToDiamond: false,
    anchorMode: "sprite-native-offset",
  };
}

function computeForcedNaturePlacement(input: {
  command: TileDrawCommand;
  isoX: number;
  isoY: number;
  zoom: number;
  sourceW: number;
  sourceH: number;
}): {
  drawX: number;
  drawY: number;
  drawW: number;
  drawH: number;
  clipToDiamond: boolean;
  anchorMode: string;
} {
  const drawW = Math.max(1, Math.round(input.sourceW * input.zoom));
  const drawH = Math.max(1, Math.round(input.sourceH * input.zoom));
  const nativeAnchorX = Math.round(input.isoX + input.command.selection.drawOffsetX * input.zoom);
  const nativeAnchorY = Math.round(input.isoY + input.command.selection.drawOffsetY * input.zoom);

  // Preserve the original native anchor while forcing full-image source to avoid vertical lift.
  const drawX = Math.round(nativeAnchorX - input.command.selection.srcX * input.zoom);
  const drawY = Math.round(nativeAnchorY - input.command.selection.srcY * input.zoom);

  return {
    drawX,
    drawY,
    drawW,
    drawH,
    clipToDiamond: false,
    anchorMode: "force-visible-nature-full-image",
  };
}

export default function RuntimeWorldRenderTestPage(props: RuntimeWorldRenderTestPageProps = {}) {
  return <UnverifiedRuntimeWorldRenderTestPage {...props} />;
}
