import { ResearchPreviewGate } from "@/components/ka/research-preview-gate";
import { useEffect, useMemo, useRef, useState } from "react";
import { parseMapBinarySectionA } from "@/runtime/world-builder/map-loader";
import { getF2SemanticGroup } from "@/runtime/world-builder/fixtures/f2-semantic-layer";
import type { ParsedMapBinary, ParsedMapCell } from "@/runtime/world-builder/types";

type AssetFolder = "chip" | "building" | "nature" | "wall";

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
  rotation: number;
  field21: number;
  sizeWidth: number;
  sizeHeight: number;
  unitWidth: number;
  unitHeight: number;
  moveSpeedRate: number;
  flag: number;
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
  assetFolder: AssetFolder;
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
  footprintWidth: number;
  footprintHeight: number;
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

type RenderInterpretationMode = "f1-terrain" | "mapchip-f2" | "terrain-f2" | "clean-source-mapchip";
type F1RowSelectionMode = "coord-hash" | "map-fields";
type TerrainAlignmentMode = "sprite-native" | "diamond-fit";
type OverlayMode = "both" | "texture-only" | "diamond-only";

type LoadedImageAsset = {
  key: string;
  requestPath: string;
  resolvedSrc: string;
  instanceId: number;
  image: HTMLImageElement;
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
  assetKey: "hashi00";
  dx: number;
  dy: number;
};

type PortGateOptPlacement = {
  cellW: number;
  cellH: number;
  destX: number;
  destY: number;
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

type FacilityRelationRow = {
  facilityId: number;
  combination: number;
  parentChipId: number;
  wallId: number;
};

type WallVisualRow = {
  wallId: number;
  res: number;
  img: number;
  seb: number;
};

type PortGateLayout = Record<PortGatePiece["assetKey"], { dx: number; dy: number }>;
type PortGateLayoutByPort = Record<string, PortGateLayout>;
type PortBridgeLayout = { dx: number; dy: number };
type PortBridgeLayoutByPort = Record<string, PortBridgeLayout>;

type PortGateDragState = {
  assetKey: PortGatePiece["assetKey"];
  startPointerWorld: { x: number; y: number };
  startOffset: { dx: number; dy: number };
  moved: boolean;
};

type RuntimeWorldRenderTestPageProps = {
  publicMode?: boolean;
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

type PortProofStats = {
  rawPortCellCount: number;
  portDrawCommandCount: number;
  resolvedBuildingCount: number;
  resolvedChipCount: number;
  resolvedWallCount: number;
  resolvedNatureCount: number;
  skippedOrMissingCount: number;
};

type PlacePortProofConfidence = "HIGH" | "MEDIUM" | "LOW" | "UNKNOWN";

type PlacePortProofRecord = {
  chip_id: number | null;
  chip_name: string;
  source_branch: "PP-B03" | "PP-B06" | "PP-B08" | "PP-B09";
  x: number | null;
  y: number | null;
  offset_x: number | null;
  offset_y: number | null;
  condition: string;
  confidence: PlacePortProofConfidence;
  evidence_note: string;
  placed: boolean;
};

type PortPieceSlot = {
  piece: PortGatePiece;
  slot: { dx: number; dy: number };
};

type PortInterpretationCandidate = {
  key: string;
  totalScore: number;
  slotSummary: string;
  anchorOffsetX: number;
  anchorOffsetY: number;
  portScores: Array<{ portId: PortAssembly["id"]; score: number }>;
  pieceSlots: PortPieceSlot[];
};

type PortInterpretationCrop = {
  portId: PortAssembly["id"];
  portName: string;
  unlockLevel: number;
  centerCellX: number;
  centerCellY: number;
  rootCellX: number;
  rootCellY: number;
  commands: TileDrawCommand[];
};

type PortInterpretationPreview = {
  key: string;
  label: string;
  description: string;
  totalScore: number;
  crops: PortInterpretationCrop[];
};

type RankedPortInterpretationEntry = {
  candidate: PortInterpretationCandidate;
  built: { commands: TileDrawCommand[]; commandsByPortId: Map<PortAssembly["id"], TileDrawCommand[]>; skippedCount: number };
  silhouetteScore: number;
  worstPortScore: number;
};

type RenderPipeline = {
  parsedMap: ParsedMapBinary;
  f1TerrainCommands: TileDrawCommand[];
  portOverlayCommands: TileDrawCommand[];
  nativePlacePortProofCommands: TileDrawCommand[];
  nativePlacePortProofRecords: PlacePortProofRecord[];
  nativePlacePortProofWarnings: string[];
  portInterpretationPreviews: PortInterpretationPreview[];
  cleanSourceMapChipRecords: CleanSourceMapChipRenderRecord[];
  cleanOldVsComparisonRows: OldVsCleanRenderComparisonRow[];
  mapChipCommands: TileDrawCommand[];
  terrainCommands: TileDrawCommand[];
  imageCache: Map<string, LoadedImageAsset>;
  facilityBuildingCache: Map<number, LoadedImageAsset>;
  portAssetCache: Map<string, LoadedImageAsset>;
  f1TerrainDiagnostics: RenderDiagnostics;
  mapChipDiagnostics: RenderDiagnostics;
  terrainDiagnostics: RenderDiagnostics;
  portProofStats: PortProofStats;
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
    buildingImageById: Map<number, string>;
    buildingSebById: Map<number, string>;
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

type CleanRectSource = "mapchiprect" | "mapcell" | "mapchip-size-fallback" | "unknown";

type CleanSourceMapChipRenderRecord = {
  mapChipId: number;
  cellX: number;
  cellY: number;
  type: number;
  category: number;
  img: number;
  seb: number;
  frame: number;
  layer: number;
  rotation: number;
  sizeWidth: number;
  sizeHeight: number;
  unitWidth: number;
  unitHeight: number;
  flag: number;
  relatedDataType: number;
  relatedDataId: number;
  facilityId: number | null;
  classificationLabel: string;
  rectX: number;
  rectY: number;
  rectWidth: number;
  rectHeight: number;
  rectSource: CleanRectSource;
  spriteSource: string;
  placementSource: string;
  evidenceReason: string;
  sourceSelection: SpriteSelection;
};

type OldVsCleanRenderComparisonRow = {
  mapChipId: number;
  cleanLabel: string;
  oldRendererPath: string;
  oldPositionSource: string;
  cleanPositionSource: string;
  oldSpriteSource: string;
  cleanSpriteSource: string;
  oldFootprintSource: string;
  cleanFootprintSource: string;
  oldUsedStaticCoordinates: boolean;
  cleanUsedMapChipData: boolean;
  cleanUsedMapChipRect: boolean;
  visiblePositionChanged: "YES" | "NO" | "UNKNOWN";
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
const RENDER_LAB_DEBUG_PANELS_ENABLED: boolean = false;
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

const MAP_PATH = resolveAssetUrl("world-assets/map/map_160_160.map");
const MAP_CHIP_PATH = resolveAssetUrl("world-assets/xls/English.lproj/MapChip.txt");
const TERRAIN_PATH = resolveAssetUrl("world-assets/xls/English.lproj/Terrain.txt");
const CHIP_IMG_INF_PATH = resolveAssetUrl("world-assets/chip/img.inf");
const CHIP_SEB_INF_PATH = resolveAssetUrl("world-assets/chip/seb.inf");
const BUILDING_IMG_INF_PATH = resolveAssetUrl("world-assets/building/img.inf");
const BUILDING_SEB_INF_PATH = resolveAssetUrl("world-assets/building/seb.inf");
const NATURE_IMG_INF_PATH = resolveAssetUrl("world-assets/nature/img.inf");
const NATURE_SEB_INF_PATH = resolveAssetUrl("world-assets/nature/seb.inf");
const WALL_IMG_INF_PATH = resolveAssetUrl("world-assets/wall/img.inf");
const WALL_SEB_INF_PATH = resolveAssetUrl("world-assets/wall/seb.inf");
const IMAGE_ATLAS_PATH = resolveAssetUrl("world-assets/image_atlas/ImageAtlas0.txt");
const PORT_ASSET_PATHS: Record<string, string> = {
  wasteland: resolveAssetUrl("world-assets/chip/wasteland.png"),
  gate_00: resolveAssetUrl("world-assets/building/gate_00.png"),
  gate_01: resolveAssetUrl("world-assets/building/gate_01.png"),
  gate_02: resolveAssetUrl("world-assets/building/gate_02.png"),
  gate_03: resolveAssetUrl("world-assets/building/gate_03.png"),
  hashi00: resolveAssetUrl("world-assets/chip/hashi00.png"),
  bridge_side: resolveAssetUrl("world-assets/chip/bridge_side.png"),
  bridge_wall_00: resolveAssetUrl("world-assets/wall/bridge_wall_00.png"),
};

// Evidence ledger summary:
// - .map/.bin main section provides explicit port-chip placement cells (67..70).
// - MapChip rows remain the visual source of truth for img/seb/frame/layer/footprint.
// - Facility lookup rows 7..10 are used only to preserve the known port family relationship graph.
const PORT_FACILITY_RELATIONS: FacilityRelationRow[] = [
  { facilityId: 7, combination: 2, parentChipId: 67, wallId: -1 },
  { facilityId: 8, combination: 0, parentChipId: -1, wallId: -1 },
  { facilityId: 9, combination: 0, parentChipId: -1, wallId: -1 },
  { facilityId: 10, combination: 0, parentChipId: -1, wallId: -1 },
];

// Wall lookup rows preserve which wall-sheet visuals belong to the port family.
// Their exact placement is still inferred because the runtime appears to assemble them dynamically.
const PORT_WALL_VISUAL_ROWS: WallVisualRow[] = [
  { wallId: 7, res: 21, img: 44, seb: 1 },
  { wallId: 8, res: 21, img: 45, seb: 1 },
  { wallId: 9, res: 21, img: 46, seb: 1 },
  { wallId: 10, res: 21, img: 47, seb: 1 },
];

const PORT_RELATED_FACILITY_IDS = new Set(PORT_FACILITY_RELATIONS.map((row) => row.facilityId));
const PORT_RELATED_CHIP_IDS = new Set([67, 68, 69, 70]);
const USE_NATIVE_PLACEPORT_PROOF_DEFAULT = true;
const PLACEPORT_NO_DESTRUCT_FLAG = 0x2000000;
const PLACEPORT_BASE_CHIP_IDS = [67, 68, 69, 70] as const;
const PLACEPORT_FIXED_CHIP_ID = 35;
const PLACEPORT_B44_1_CHIP_ID = 65;
const PLACEPORT_GROUNDDATA_CHIP_IDS = [5, 6, 7] as const;

const PORT_GATE_OPT_PLACEMENT: Record<PortGatePiece["assetKey"], PortGateOptPlacement> = {
  // Building .opt sidecars place each gate image inside a 96x128 render cell.
  // Drawing the raw PNG directly makes gate_03 sit far too low on the map.
  gate_00: { cellW: 96, cellH: 128, destX: 8, destY: 1 },
  gate_01: { cellW: 96, cellH: 128, destX: 0, destY: 23 },
  gate_02: { cellW: 96, cellH: 128, destX: 1, destY: 16 },
  gate_03: { cellW: 96, cellH: 128, destX: 12, destY: 61 },
};
// Visual grounding tweak: OPT offsets are close but leave gate sprites a little high over tile tops.
const PORT_GATE_GROUNDING_Y_NUDGE = 8;
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
type NativeDataV2FacilityPlacement = {
  facilityId: number;
  facilityName: string;
  unlockLevel: number;
  storedCellX: number;
  storedCellY: number;
  mapChipIds: number[];
  selectedMapChipId: number | null;
  mapChipRelatedDataType: number | null;
  sizeWidth: number;
  sizeHeight: number;
  coveredMinX: number;
  coveredMinY: number;
  coveredMaxX: number;
  coveredMaxY: number;
  imgId: number;
  sebId: number;
  frame: number;
  layer: number;
  sourceImageFilename: string | null;
  sourceSebFilename: string | null;
  parentChipId: number | null;
  relationCombination: number | null;
  relationOffsetX: number | null;
  relationOffsetY: number | null;
  relationOffsetZ: number | null;
  renderReason: string;
};
type PortPieceAuditRow = {
  label: string;
  pieceKind: "gate" | "bridge";
  facilityId: number | null;
  mapChipId: number | null;
  relatedDataId: number | null;
  relatedDataType: number | null;
  img: number | null;
  seb: number | null;
  frame: number | null;
  layer: number | null;
  sizeWidth: number | null;
  sizeHeight: number | null;
  facilityParentChipId: number | null;
  facilityChips: number[];
  facilityOffsetX: number | null;
  facilityOffsetY: number | null;
  facilityOffsetZ: number | null;
  chosenAnchorCell: string;
  nativeProofAnchorCell: string;
  finalIso: string;
  spriteDrawOrigin: string;
  sourceResolverFunction: string;
  renderReason: string;
  identityStatus: string;
};
type FacilityDataAuditEvidence = {
  facilityId: number;
  parentChipId: number | null;
  chips: number[];
  offsetX: number | null;
  offsetY: number | null;
  offsetZ: number | null;
  source: string;
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
  nativeDataV2: {
    footprintStroke: "rgba(14, 165, 233, 0.95)",
    footprintFill: "rgba(14, 165, 233, 0.18)",
    anchorStroke: "rgba(14, 165, 233, 0.95)",
    labelBackground: "rgba(14, 165, 233, 0.85)",
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
const ONE_PIECE_FACILITY_OVERLAYS: FacilityOverlay[] = [
  // buildingImageId = MapChip.img (col 10) from the MapChip row where relatedDataId = facility id.
  // DO NOT use Facility_lookup.type (col 2) — that is a facility-category code, not an image ID.
  // Source: KA GameData - MapChip.csv rows 225-264.
  // img.inf path: /world-assets/building/img.inf  (tab-separated: id<TAB>filename,flags)
  { id: 172, name: "Ranking Board",        unlockLevel: 2,   buildingImageId: 69, zoneX: 6, zoneY: 8, cellX: 104, cellY: 136 }, // MapChip 232, img=69 → building_68.png
  { id: 171, name: "Trophy Room",          unlockLevel: 3,   buildingImageId: 68, zoneX: 8, zoneY: 6, cellX: 136, cellY: 104 }, // MapChip 231, img=68 → building_67.png
  { id: 167, name: "Briefing Room",        unlockLevel: 5,   buildingImageId: 62, zoneX: 6, zoneY: 9, cellX: 104, cellY: 152 }, // MapChip 227, img=62 → building_61.png
  { id: 166, name: "Friend Post Office",   unlockLevel: 10,  buildingImageId: 61, zoneX: 6, zoneY: 6, cellX: 104, cellY: 104 }, // MapChip 226, img=61 → building_60.png
  { id: 175, name: "Material Shop",        unlockLevel: 10,  buildingImageId: 72, zoneX: 9, zoneY: 5, cellX: 152, cellY: 88  }, // MapChip 235, img=72 → building_71.png
  { id: 165, name: "Master Smithy",        unlockLevel: 11,  buildingImageId: 60, zoneX: 5, zoneY: 8, cellX: 88,  cellY: 136 }, // MapChip 225, img=60 → building_59.png
  { id: 170, name: "Monster Farm",         unlockLevel: 14,  buildingImageId: 67, zoneX: 7, zoneY: 5, cellX: 120, cellY: 88  }, // MapChip 230, img=67 → building_66.png
  { id: 181, name: "Underground Arena",    unlockLevel: 20,  buildingImageId: 63, zoneX: 5, zoneY: 5, cellX: 88,  cellY: 88  }, // MapChip 241, img=63 → building_62.png
  { id: 169, name: "Treasure Room",        unlockLevel: 21,  buildingImageId: 65, zoneX: 4, zoneY: 9, cellX: 72,  cellY: 152 }, // MapChip 229, img=65 → building_64.png
  { id: 168, name: "Weekly Conquest Bonus",unlockLevel: 22,  buildingImageId: 64, zoneX: 6, zoneY: 4, cellX: 104, cellY: 72  }, // MapChip 228, img=64 → building_63.png
  { id: 198, name: "Movers",               unlockLevel: 23,  buildingImageId: 94, zoneX: 4, zoneY: 7, cellX: 72,  cellY: 120 }, // MapChip 260, img=94 → building_81.png
  { id: 173, name: "Friends Agency",       unlockLevel: 30,  buildingImageId: 70, zoneX: 4, zoneY: 6, cellX: 72,  cellY: 104 }, // MapChip 233, img=70 → building_69.png
  { id: 200, name: "Equipment Exchange",   unlockLevel: 34,  buildingImageId: 96, zoneX: 6, zoneY: 3, cellX: 104, cellY: 56  }, // MapChip 262, img=96 → building_83.png (was correct)
  { id: 174, name: "Job Center",           unlockLevel: 35,  buildingImageId: 71, zoneX: 3, zoneY: 6, cellX: 56,  cellY: 104 }, // MapChip 234, img=71 → building_70.png
  { id: 201, name: "Trading Post",         unlockLevel: 40,  buildingImageId: 97, zoneX: 9, zoneY: 3, cellX: 152, cellY: 56  }, // MapChip 263, img=97 → building_84.png (was correct)
  { id: 177, name: "Instructor's Room",    unlockLevel: 41,  buildingImageId: 74, zoneX: 2, zoneY: 5, cellX: 40,  cellY: 88  }, // MapChip 237, img=74 → building_73.png
  { id: 178, name: "Monster Fusion Lab",   unlockLevel: 45,  buildingImageId: 75, zoneX: 5, zoneY: 4, cellX: 88,  cellY: 72  }, // MapChip 238, img=75 → building_74.png
  { id: 180, name: "Kairo Room",           unlockLevel: 58,  buildingImageId: 95, zoneX: 3, zoneY: 5, cellX: 56,  cellY: 88  }, // MapChip 261, img=95 → building_82.png
  { id: 196, name: "Legendary Cave",       unlockLevel: 120, buildingImageId: 92, zoneX: 5, zoneY: 1, cellX: 88,  cellY: 24  }, // MapChip 258, img=92 → building_79.png
  { id: 202, name: "Date Spot",            unlockLevel: 135, buildingImageId: 98, zoneX: 1, zoneY: 2, cellX: 24,  cellY: 40  }, // MapChip 264, img=98 → building_85.png
];

const PORT_ASSEMBLIES: PortAssembly[] = [
  { id: "port-level-7", facilityId: 7, name: "Port", unlockLevel: 7, zoneX: 9, zoneY: 6, cellX: 152, cellY: 104 },
  { id: "port-level-44", facilityId: 10, name: "Port", unlockLevel: 44, zoneX: 9, zoneY: 2, cellX: 152, cellY: 40 },
];

const PORT_GATE_PIECES: PortGatePiece[] = [
  // DEBUG/EXPERIMENTAL ONLY:
  // These piece offsets exist only for manual lab experimentation.
  // They are not authoritative runtime rules and must never be promoted as permanent placement logic.
  // TODO: remove manual correction tooling once native PlacePort + FacilityData coverage is complete.
  // Facility id 7 / chip 67 is the verified root piece. Treat the stored facility cell as the
  // NW anchor of gate_00's 2x2 footprint, then lay out the remaining 2x2 pieces around it.
  { chipId: 67, facilityId: 7, buildingImageId: 2, assetKey: "gate_00", dx: 2, dy: -2 },
  { chipId: 68, facilityId: 8, buildingImageId: 3, assetKey: "gate_01", dx: 2, dy: 0 },
  { chipId: 70, facilityId: 10, buildingImageId: 21, assetKey: "gate_03", dx: 4, dy: -2 },
  { chipId: 69, facilityId: 9, buildingImageId: 20, assetKey: "gate_02", dx: 4, dy: 0 },
];

const DEFAULT_PORT_GATE_LAYOUT: PortGateLayout = {
  gate_00: { dx: 2, dy: -2 },
  gate_01: { dx: 2, dy: 0 },
  gate_03: { dx: 4, dy: -2 },
  gate_02: { dx: 4, dy: 0 },
};

const DEFAULT_PORT_GATE_LAYOUT_BY_PORT: PortGateLayoutByPort = PORT_ASSEMBLIES.reduce((acc, port) => {
  acc[port.id] = {
    gate_00: { ...DEFAULT_PORT_GATE_LAYOUT.gate_00 },
    gate_01: { ...DEFAULT_PORT_GATE_LAYOUT.gate_01 },
    gate_03: { ...DEFAULT_PORT_GATE_LAYOUT.gate_03 },
    gate_02: { ...DEFAULT_PORT_GATE_LAYOUT.gate_02 },
  };
  return acc;
}, {} as PortGateLayoutByPort);

// DEBUG NOTE:
// localStorage key is only for temporary manual lab tweaking.
// It must not be treated as gameplay/runtime truth.
// MANUAL TEMPORARY FIX: port gate/bridge offsets in this lab are manual correction values only.
const PORT_LAYOUT_STORAGE_KEY = "ka-runtime-port-layout-v5";
const PORT_GATE_LAYOUT_STORAGE_KEY_LEGACY = "ka-runtime-port-gate-layout-v3";
const PORT_FACILITY_DATA_AUDIT_EVIDENCE: FacilityDataAuditEvidence[] = [
  // Source: workbook_facility_ids_7_10.json + native handoff findings for parent/root and child sequence.
  { facilityId: 7, parentChipId: 67, chips: [70, 68, 69], offsetX: 0, offsetY: 0, offsetZ: 0, source: "workbook+native" },
  { facilityId: 8, parentChipId: null, chips: [], offsetX: 0, offsetY: 0, offsetZ: 0, source: "workbook" },
  { facilityId: 9, parentChipId: null, chips: [], offsetX: 0, offsetY: 0, offsetZ: 0, source: "workbook" },
  { facilityId: 10, parentChipId: null, chips: [], offsetX: 0, offsetY: 0, offsetZ: 0, source: "workbook" },
];
const PORT_COMPOSITE_SLOTS = [
  { dx: 0, dy: 0 },
  { dx: 2, dy: 0 },
  { dx: 0, dy: 2 },
  { dx: 2, dy: 2 },
] as const;

function getPortCompositeBaseCell(port: PortAssembly): { x: number; y: number } {
  return {
    x: port.cellX - 2,
    y: port.cellY - 2,
  };
}

const PORT_BRIDGE_PIECES: PortBridgePiece[] = [
  // 4x2 connected hashi group (8 tiles).
  { assetKey: "hashi00", dx: 0, dy: 0 },
  { assetKey: "hashi00", dx: 1, dy: 0 },
  { assetKey: "hashi00", dx: 2, dy: 0 },
  { assetKey: "hashi00", dx: 3, dy: 0 },
  { assetKey: "hashi00", dx: 0, dy: 1 },
  { assetKey: "hashi00", dx: 1, dy: 1 },
  { assetKey: "hashi00", dx: 2, dy: 1 },
  { assetKey: "hashi00", dx: 3, dy: 1 },
];

const DEFAULT_PORT_BRIDGE_LAYOUT: PortBridgeLayout = {
  dx: 6,
  dy: -1,
};

const DEFAULT_PORT_BRIDGE_LAYOUT_BY_PORT: PortBridgeLayoutByPort = PORT_ASSEMBLIES.reduce((acc, port) => {
  acc[port.id] = { ...DEFAULT_PORT_BRIDGE_LAYOUT };
  return acc;
}, {} as PortBridgeLayoutByPort);
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

function normalizePortBridgeOffset(
  value: { dx?: number; dy?: number } | undefined,
  fallback: { dx: number; dy: number },
): { dx: number; dy: number } {
  const dx = typeof value?.dx === "number" && Number.isFinite(value.dx) ? value.dx : fallback.dx;
  const dy = typeof value?.dy === "number" && Number.isFinite(value.dy) ? value.dy : fallback.dy;
  return { dx, dy };
}

function readStoredPortLayouts(): { gateLayouts: PortGateLayoutByPort; bridgeLayouts: PortBridgeLayoutByPort } {
  // DEBUG/EXPERIMENTAL ONLY.
  // This helper is intentionally isolated to manual-lab adjustments, not permanent placement rules.
  if (typeof window === "undefined") {
    return {
      gateLayouts: DEFAULT_PORT_GATE_LAYOUT_BY_PORT,
      bridgeLayouts: DEFAULT_PORT_BRIDGE_LAYOUT_BY_PORT,
    };
  }

  try {
    const raw = window.localStorage.getItem(PORT_LAYOUT_STORAGE_KEY);
    const rawLegacy = window.localStorage.getItem(PORT_GATE_LAYOUT_STORAGE_KEY_LEGACY);
    const source = raw ?? rawLegacy;
    if (!source) {
      return {
        gateLayouts: DEFAULT_PORT_GATE_LAYOUT_BY_PORT,
        bridgeLayouts: DEFAULT_PORT_BRIDGE_LAYOUT_BY_PORT,
      };
    }
    const parsed = JSON.parse(source) as
      | { gateLayouts?: Record<string, Partial<PortGateLayout>>; bridgeLayouts?: Record<string, Partial<PortBridgeLayout>> }
      | Record<string, Partial<PortGateLayout>>
      | Partial<PortGateLayout>;

    // Backward compatibility with old single-layout storage shape.
    const parsedLooksLikeSingleLayout =
      typeof parsed === "object" &&
      parsed !== null &&
      ("gate_00" in parsed || "gate_01" in parsed || "gate_02" in parsed || "gate_03" in parsed);

    if (parsedLooksLikeSingleLayout) {
      const legacy = parsed as Partial<PortGateLayout>;
      const normalizedLegacy: PortGateLayout = {
        gate_00: normalizePortGateOffset(legacy.gate_00, DEFAULT_PORT_GATE_LAYOUT.gate_00),
        gate_01: normalizePortGateOffset(legacy.gate_01, DEFAULT_PORT_GATE_LAYOUT.gate_01),
        gate_02: normalizePortGateOffset(legacy.gate_02, DEFAULT_PORT_GATE_LAYOUT.gate_02),
        gate_03: normalizePortGateOffset(legacy.gate_03, DEFAULT_PORT_GATE_LAYOUT.gate_03),
      };
      const migrated: PortGateLayoutByPort = {};
      const migratedBridge: PortBridgeLayoutByPort = {};
      for (const port of PORT_ASSEMBLIES) {
        migrated[port.id] = {
          gate_00: { ...normalizedLegacy.gate_00 },
          gate_01: { ...normalizedLegacy.gate_01 },
          gate_02: { ...normalizedLegacy.gate_02 },
          gate_03: { ...normalizedLegacy.gate_03 },
        };
        migratedBridge[port.id] = { ...DEFAULT_PORT_BRIDGE_LAYOUT };
      }
      return {
        gateLayouts: migrated,
        bridgeLayouts: migratedBridge,
      };
    }

    const v4Payload = parsed as {
      gateLayouts?: Record<string, Partial<PortGateLayout>>;
      bridgeLayouts?: Record<string, Partial<PortBridgeLayout>>;
    };
    const byPort = v4Payload.gateLayouts ?? (parsed as Record<string, Partial<PortGateLayout>>);
    const bridgeByPort = v4Payload.bridgeLayouts ?? {};
    const normalized: PortGateLayoutByPort = {};
    const normalizedBridge: PortBridgeLayoutByPort = {};
    for (const port of PORT_ASSEMBLIES) {
      const portLayout = byPort[port.id] ?? {};
      const portBridgeLayout = bridgeByPort[port.id] ?? {};
      normalized[port.id] = {
        gate_00: normalizePortGateOffset(portLayout.gate_00, DEFAULT_PORT_GATE_LAYOUT.gate_00),
        gate_01: normalizePortGateOffset(portLayout.gate_01, DEFAULT_PORT_GATE_LAYOUT.gate_01),
        gate_02: normalizePortGateOffset(portLayout.gate_02, DEFAULT_PORT_GATE_LAYOUT.gate_02),
        gate_03: normalizePortGateOffset(portLayout.gate_03, DEFAULT_PORT_GATE_LAYOUT.gate_03),
      };
      normalizedBridge[port.id] = normalizePortBridgeOffset(portBridgeLayout, DEFAULT_PORT_BRIDGE_LAYOUT);
    }
    return {
      gateLayouts: normalized,
      bridgeLayouts: normalizedBridge,
    };
  } catch {
    return {
      gateLayouts: DEFAULT_PORT_GATE_LAYOUT_BY_PORT,
      bridgeLayouts: DEFAULT_PORT_BRIDGE_LAYOUT_BY_PORT,
    };
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

function UnverifiedRuntimeWorldRenderTestPage({ publicMode = false }: RuntimeWorldRenderTestPageProps = {}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const suppressNextCanvasClickRef = useRef(false);
  const [camera, setCamera] = useState<CameraState>({ offsetX: 0, offsetY: 0, zoom: 1 });
  const [dragging, setDragging] = useState(false);
  const [hoveredCell, setHoveredCell] = useState<{ x: number; y: number } | null>(null);
  const [selectedCell, setSelectedCell] = useState<{ x: number; y: number } | null>(null);
  const [twoByTwoMode, setTwoByTwoMode] = useState(false);
  const [renderMode, setRenderMode] = useState<RenderInterpretationMode>("f1-terrain");
  const [f1RowSelectionMode, setF1RowSelectionMode] = useState<F1RowSelectionMode>("map-fields");
  const [terrainAlignmentMode, setTerrainAlignmentMode] = useState<TerrainAlignmentMode>("diamond-fit");
  const [overlayMode, setOverlayMode] = useState<OverlayMode>("texture-only");
  const [showNatureDebugOverlay, setShowNatureDebugOverlay] = useState(false);
  const [showTerrainNature, setShowTerrainNature] = useState(true);
  const [showResourceNature, setShowResourceNature] = useState(true);
  const [showHumanNature, setShowHumanNature] = useState(true);
  const [showSpecialNature, setShowSpecialNature] = useState(false);
  const [showOnePieceFacilities, setShowOnePieceFacilities] = useState(true);
  const [showNativeDataV2FacilityDuplicates, setShowNativeDataV2FacilityDuplicates] = useState(false);
  const [showNativeDataV2Labels, setShowNativeDataV2Labels] = useState(true);
  const [showCorrectedRankingBoardPlacementTest, setShowCorrectedRankingBoardPlacementTest] = useState(false);
  const [showRawRankingBoardPlacementTest, setShowRawRankingBoardPlacementTest] = useState(false);
  const [showLegendaryCaveRawPlacementTest, setShowLegendaryCaveRawPlacementTest] = useState(false);
  const [showPortAssemblies, setShowPortAssemblies] = useState(true);
  const [showVerifiedMapChipPortReconstruction, setShowVerifiedMapChipPortReconstruction] = useState(true);
  const [useNativePlacePortProof, setUseNativePlacePortProof] = useState(USE_NATIVE_PLACEPORT_PROOF_DEFAULT);
  const [showPortFillWaterStageLayer, setShowPortFillWaterStageLayer] = useState(false);
  const [showPortPlaceChipKnownLayer, setShowPortPlaceChipKnownLayer] = useState(false);
  const [showPortFacilityCompositeLayer, setShowPortFacilityCompositeLayer] = useState(false);
  const [showPortPieceAnchorAuditLayer, setShowPortPieceAnchorAuditLayer] = useState(false);
  const [showPortManualSandboxOverlay, setShowPortManualSandboxOverlay] = useState(false);
  const [swapPortWaterToF1Ground, setSwapPortWaterToF1Ground] = useState(true);
  const [hideOldManualPortOverlay, setHideOldManualPortOverlay] = useState(false);
  const [showPortBridgePieces, setShowPortBridgePieces] = useState(true);
  const [showCleanSourceMapChipRenderRecords, setShowCleanSourceMapChipRenderRecords] = useState(false);
  const [showCleanSourceRecordLabels, setShowCleanSourceRecordLabels] = useState(true);
  // Manual Port layout values are temporary visual experiment controls only. They are not data/native evidence and must not be promoted into default renderer rules. Final Port placement must come from MapChip / FacilityData / Facility_lookup / SEB metadata / native findings.
  const [portGateLayouts, setPortGateLayouts] = useState<PortGateLayoutByPort>(DEFAULT_PORT_GATE_LAYOUT_BY_PORT);
  const [portBridgeLayouts, setPortBridgeLayouts] = useState<PortBridgeLayoutByPort>(DEFAULT_PORT_BRIDGE_LAYOUT_BY_PORT);
  const [quickControlPortId, setQuickControlPortId] = useState<PortAssembly["id"]>(PORT_ASSEMBLIES[0]?.id ?? "port-level-7");
  const [quickControlPieceKey, setQuickControlPieceKey] = useState<PortGatePiece["assetKey"] | "hashi_group">("gate_00");
  const [quickControlDxInput, setQuickControlDxInput] = useState("0");
  const [quickControlDyInput, setQuickControlDyInput] = useState("0");
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

  const natureCategoryVisibility = useMemo<NatureCategoryVisibility>(() => ({
    "terrain-nature": showTerrainNature,
    "resource-treasure": showResourceNature,
    "human-npc": showHumanNature,
    "special-unknown": showSpecialNature,
  }), [showHumanNature, showResourceNature, showSpecialNature, showTerrainNature]);

  const cleanSourceMapChipRecords = useMemo(
    () => pipeline?.cleanSourceMapChipRecords ?? [],
    [pipeline],
  );
  const cleanSourceComparisonRows = useMemo(
    () => pipeline?.cleanOldVsComparisonRows ?? [],
    [pipeline],
  );
  const cleanSourceMapChipRectAvailable = useMemo(
    () => cleanSourceMapChipRecords.some((record) => record.rectSource === "mapchiprect"),
    [cleanSourceMapChipRecords],
  );

  function updatePortGateOffset(portId: string, assetKey: PortGatePiece["assetKey"], axis: "dx" | "dy", value: number) {
    // Manual correction in lab only. Never ship this as canonical placement behavior.
    if (!Number.isFinite(value)) {
      return;
    }
    setPortGateLayouts((previous) => ({
      ...previous,
      [portId]: {
        ...(previous[portId] ?? DEFAULT_PORT_GATE_LAYOUT),
        [assetKey]: {
          ...(previous[portId]?.[assetKey] ?? DEFAULT_PORT_GATE_LAYOUT[assetKey]),
          [axis]: value,
        },
      },
    }));
  }

  function resetPortGateLayout(portId?: string) {
    // Reset debug-only manual offsets back to baseline test values.
    setPortGateLayouts((previous) => {
      if (portId) {
        return {
          ...previous,
          [portId]: {
            gate_00: { ...DEFAULT_PORT_GATE_LAYOUT.gate_00 },
            gate_01: { ...DEFAULT_PORT_GATE_LAYOUT.gate_01 },
            gate_02: { ...DEFAULT_PORT_GATE_LAYOUT.gate_02 },
            gate_03: { ...DEFAULT_PORT_GATE_LAYOUT.gate_03 },
          },
        };
      }
      return DEFAULT_PORT_GATE_LAYOUT_BY_PORT;
    });
    setPortBridgeLayouts((previous) => {
      if (portId) {
        return {
          ...previous,
          [portId]: { ...DEFAULT_PORT_BRIDGE_LAYOUT },
        };
      }
      return DEFAULT_PORT_BRIDGE_LAYOUT_BY_PORT;
    });
  }

  function updatePortBridgeOffset(portId: string, axis: "dx" | "dy", value: number) {
    if (!Number.isFinite(value)) {
      return;
    }
    setPortBridgeLayouts((previous) => ({
      ...previous,
      [portId]: {
        ...(previous[portId] ?? DEFAULT_PORT_BRIDGE_LAYOUT),
        [axis]: value,
      },
    }));
  }

  useEffect(() => {
    const stored = readStoredPortLayouts();
    setPortGateLayouts(stored.gateLayouts);
    setPortBridgeLayouts(stored.bridgeLayouts);
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }
    try {
      window.localStorage.setItem(PORT_LAYOUT_STORAGE_KEY, JSON.stringify({
        gateLayouts: portGateLayouts,
        bridgeLayouts: portBridgeLayouts,
      }));
    } catch {
      // Ignore storage write issues in lab mode.
    }
  }, [portBridgeLayouts, portGateLayouts]);

  const quickControlPieceOptions = useMemo(() => (
    [
      ...PORT_GATE_PIECES.map((piece) => ({
        key: piece.assetKey as PortGatePiece["assetKey"] | "hashi_group",
        label: piece.assetKey,
      })),
      { key: "hashi_group" as const, label: "hashi00 4x2 group" },
    ]
  ), []);

  const quickControlCurrentOffset = useMemo(() => {
    if (quickControlPieceKey === "hashi_group") {
      return portBridgeLayouts[quickControlPortId] ?? DEFAULT_PORT_BRIDGE_LAYOUT;
    }
    const portLayout = portGateLayouts[quickControlPortId] ?? DEFAULT_PORT_GATE_LAYOUT;
    const piece = PORT_GATE_PIECES.find((entry) => entry.assetKey === quickControlPieceKey);
    const fallback = piece ? { dx: piece.dx, dy: piece.dy } : { dx: 0, dy: 0 };
    return portLayout[quickControlPieceKey] ?? fallback;
  }, [portBridgeLayouts, portGateLayouts, quickControlPieceKey, quickControlPortId]);

  useEffect(() => {
    setQuickControlDxInput(String(quickControlCurrentOffset.dx));
    setQuickControlDyInput(String(quickControlCurrentOffset.dy));
  }, [quickControlCurrentOffset.dx, quickControlCurrentOffset.dy, quickControlPieceKey, quickControlPortId]);

  function updateQuickControlOffset(axis: "dx" | "dy", rawValue: string) {
    if (axis === "dx") {
      setQuickControlDxInput(rawValue);
    } else {
      setQuickControlDyInput(rawValue);
    }

    const next = Number(rawValue);
    if (!Number.isFinite(next)) {
      return;
    }

    if (quickControlPieceKey === "hashi_group") {
      updatePortBridgeOffset(quickControlPortId, axis, next);
      return;
    }

    updatePortGateOffset(quickControlPortId, quickControlPieceKey, axis, next);
  }

  function sanitizeQuickControlInput(axis: "dx" | "dy") {
    const raw = axis === "dx" ? quickControlDxInput : quickControlDyInput;
    const parsed = Number(raw);
    if (Number.isFinite(parsed)) {
      if (axis === "dx") {
        setQuickControlDxInput(String(parsed));
      } else {
        setQuickControlDyInput(String(parsed));
      }
      return;
    }

    if (axis === "dx") {
      setQuickControlDxInput(String(quickControlCurrentOffset.dx));
    } else {
      setQuickControlDyInput(String(quickControlCurrentOffset.dy));
    }
  }

  function stepQuickControlPiece(step: -1 | 1) {
    const currentIndex = quickControlPieceOptions.findIndex((option) => option.key === quickControlPieceKey);
    if (currentIndex < 0) {
      setQuickControlPieceKey(quickControlPieceOptions[0]?.key ?? "gate_00");
      return;
    }
    const nextIndex = (currentIndex + step + quickControlPieceOptions.length) % quickControlPieceOptions.length;
    setQuickControlPieceKey(quickControlPieceOptions[nextIndex].key);
  }

  const portGateWaterAudit = useMemo(() => {
    if (!pipeline) {
      return { cells: 0, before: 0, after: 0 };
    }

    const sourceCommands = (() => {
      if (renderMode === "f1-terrain") {
        return filterNatureCommandsByVisibility(pipeline.f1TerrainCommands, natureCategoryVisibility);
      }
      if (renderMode === "terrain-f2") {
        return pipeline.terrainCommands;
      }
      return pipeline.mapChipCommands;
    })();

    const filteredCommands = defaultTerrainMode
      ? sourceCommands.filter((command) => command.drawGroup === "base-terrain" || command.drawGroup === "nature-object")
      : sourceCommands;

    const gateCells = buildManualPortGateFootprintCellSet(portGateLayouts);
    if (!swapPortWaterToF1Ground || gateCells.size === 0) {
      const waterBeforeOnly = filteredCommands.filter((command) => {
        if (command.selection.assetFolder !== "chip") {
          return false;
        }
        if (command.drawGroup === "nature-object") {
          return false;
        }
        return gateCells.has(`${command.cellX},${command.cellY}`) && isWaterLikeCommand(command);
      }).length;
      return { cells: gateCells.size, before: waterBeforeOnly, after: waterBeforeOnly };
    }

    const f1BaseByCell = new Map<string, TileDrawCommand>();
    for (const command of pipeline.f1TerrainCommands) {
      if (command.drawGroup === "base-terrain") {
        f1BaseByCell.set(`${command.cellX},${command.cellY}`, command);
      }
    }

    const fallbackDirtTemplates = buildFallbackDirtTemplates(pipeline.mapChipCommands, pipeline.f1TerrainCommands);
    const commandsAfterPortWaterSwap = filteredCommands.map((command) => {
      if (command.drawGroup === "nature-object") {
        return command;
      }
      if (command.selection.assetFolder !== "chip") {
        return command;
      }
      const cellKey = `${command.cellX},${command.cellY}`;
      if (!gateCells.has(cellKey)) {
        return command;
      }

      const replacement = f1BaseByCell.get(cellKey);
      if (replacement && isDirtLikeCommand(replacement)) {
        return replacement;
      }

      const dirtReplacement = buildDirtReplacementForCell(command, fallbackDirtTemplates);
      return dirtReplacement ?? command;
    });

    const waterBefore = filteredCommands.filter((command) => {
      if (command.selection.assetFolder !== "chip") {
        return false;
      }
      if (command.drawGroup === "nature-object") {
        return false;
      }
      return gateCells.has(`${command.cellX},${command.cellY}`) && isWaterLikeCommand(command);
    }).length;
    const waterAfter = commandsAfterPortWaterSwap.filter((command) => {
      if (command.selection.assetFolder !== "chip") {
        return false;
      }
      if (command.drawGroup === "nature-object") {
        return false;
      }
      return gateCells.has(`${command.cellX},${command.cellY}`) && isWaterLikeCommand(command);
    }).length;

    return {
      cells: gateCells.size,
      before: waterBefore,
      after: waterAfter,
    };
  }, [defaultTerrainMode, natureCategoryVisibility, pipeline, portGateLayouts, renderMode, swapPortWaterToF1Ground]);

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
        const terrainFolder = getAssetFolderFromRes(row.res);
        const filename = getImageTableForFolder(
          terrainFolder,
          pipeline.lookups.imageById,
          pipeline.lookups.buildingImageById,
          pipeline.lookups.natureImageById,
          new Map<number, string>(),
        ).get(row.img);
        const image = filename ? pipeline.imageCache.get(buildAssetCacheKey(terrainFolder, filename))?.image : null;
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
      const image = pipeline.imageCache.get(buildAssetCacheKey(command.selection.assetFolder, command.selection.sourceFilename))?.image;
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
    if (renderMode === "terrain-f2") {
      return pipeline.terrainCommands;
    }
    if (renderMode === "clean-source-mapchip") {
      // Clean-source mode should keep canonical f2 mapchip rendering as the base.
      return pipeline.mapChipCommands;
    }
    return pipeline.mapChipCommands;
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
    if (renderMode === "clean-source-mapchip") {
      return pipeline.mapChipDiagnostics;
    }
    return renderMode === "terrain-f2" ? pipeline.terrainDiagnostics : pipeline.mapChipDiagnostics;
  }, [pipeline, renderMode]);

  const portProofComparison = useMemo(() => {
    if (!pipeline) {
      return null;
    }

    const activeProofCommands = useNativePlacePortProof ? pipeline.nativePlacePortProofCommands : pipeline.portOverlayCommands;

    const manualAssetFilenames = new Set([
      "gate_00.png",
      "gate_01.png",
      "gate_02.png",
      "gate_03.png",
      "hashi00.png",
    ]);
    const manualCells = buildManualPortOverlayCellSet(portGateLayouts, portBridgeLayouts);
    const hasResolvedCommands = activeProofCommands.length > 0;
    const sameCoordinates =
      hasResolvedCommands && activeProofCommands.every((command) => manualCells.has(`${command.cellX},${command.cellY}`));
    const sameAssetFilenames =
      hasResolvedCommands && activeProofCommands.every((command) => manualAssetFilenames.has(command.selection.sourceFilename));
    const oldLayerCoveringIt = Boolean(showVerifiedMapChipPortReconstruction && !hideOldManualPortOverlay && showPortAssemblies && false);
    const toggleDisabled = !showVerifiedMapChipPortReconstruction;
    const noResolvedCommands = !hasResolvedCommands;

    let verdict = "Verified proof overlay is drawing on top of the normal map path.";
    if (toggleDisabled) {
      verdict = "Visible proof mode is disabled.";
    } else if (noResolvedCommands) {
      verdict = "No resolved reconstructed port commands were produced from raw .map port cells.";
    } else if (sameCoordinates && sameAssetFilenames) {
      verdict = "The proof commands match the same manual overlay footprint and asset filenames, so the base imagery can look identical until you rely on the colored outlines and labels.";
    } else if (sameCoordinates) {
      verdict = "The proof commands land on the same footprint as the manual overlay, but they do not resolve to the same asset filenames.";
    } else if (sameAssetFilenames) {
      verdict = "The proof commands reuse the same asset filenames, but not the same manual footprint coordinates.";
    }

    return {
      sameCoordinates,
      sameAssetFilenames,
      oldLayerCoveringIt,
      toggleDisabled,
      noResolvedCommands,
      verdict,
    };
  }, [hideOldManualPortOverlay, pipeline, portBridgeLayouts, portGateLayouts, showPortAssemblies, showVerifiedMapChipPortReconstruction, useNativePlacePortProof]);

  const nativeDataV2FacilityPlacements = useMemo(() => {
    if (!pipeline) {
      return [] as NativeDataV2FacilityPlacement[];
    }

    const mapChipRows = [...pipeline.lookups.mapChipById.values()];
    return ONE_PIECE_FACILITY_OVERLAYS.map((facility) => {
      const matchingMapChips = mapChipRows.filter((row) => row.relatedDataId === facility.id);
      const selectedMapChip =
        matchingMapChips.find((row) => row.img === facility.buildingImageId) ??
        matchingMapChips[0] ??
        null;

      const sizeWidth = Math.max(1, selectedMapChip?.sizeWidth ?? 2);
      const sizeHeight = Math.max(1, selectedMapChip?.sizeHeight ?? 2);
      const coveredMinX = facility.cellX;
      const coveredMinY = facility.cellY;
      const coveredMaxX = coveredMinX + sizeWidth - 1;
      const coveredMaxY = coveredMinY + sizeHeight - 1;
      const parentRelation = PORT_FACILITY_RELATIONS.find((row) => row.facilityId === facility.id) ?? null;

      let renderReason = "MapChip relatedData-driven placement (no manual visual correction).";
      if (!selectedMapChip && matchingMapChips.length === 0) {
        renderReason = "No MapChip relatedData row found; fallback to known facility cell with default 2x2 footprint.";
      } else if (!selectedMapChip) {
        renderReason = "Multiple MapChip candidates found; fallback to first candidate for footprint metadata.";
      }

      return {
        facilityId: facility.id,
        facilityName: facility.name,
        unlockLevel: facility.unlockLevel,
        storedCellX: facility.cellX,
        storedCellY: facility.cellY,
        mapChipIds: matchingMapChips.map((row) => row.id),
        selectedMapChipId: selectedMapChip?.id ?? null,
        mapChipRelatedDataType: selectedMapChip?.relatedDataType ?? null,
        sizeWidth,
        sizeHeight,
        coveredMinX,
        coveredMinY,
        coveredMaxX,
        coveredMaxY,
        imgId: selectedMapChip?.img ?? facility.buildingImageId,
        sebId: selectedMapChip?.seb ?? -1,
        frame: selectedMapChip?.frame ?? 0,
        layer: selectedMapChip?.layer ?? 0,
        sourceImageFilename:
          pipeline.lookups.buildingImageById.get(selectedMapChip?.img ?? facility.buildingImageId) ?? null,
        sourceSebFilename: selectedMapChip
          ? pipeline.lookups.buildingSebById.get(selectedMapChip.seb) ?? null
          : null,
        parentChipId: parentRelation?.parentChipId ?? null,
        relationCombination: parentRelation?.combination ?? null,
        relationOffsetX: parentRelation ? 0 : null,
        relationOffsetY: parentRelation ? 0 : null,
        relationOffsetZ: parentRelation ? 0 : null,
        renderReason,
      };
    });
  }, [pipeline]);

  const nativeDataV2FacilityDiagnostics = useMemo(() => {
    return nativeDataV2FacilityPlacements.map((placement) => {
      const iso = worldToIso(placement.coveredMinX, placement.coveredMinY, camera.zoom, camera.offsetX, camera.offsetY);
      return {
        ...placement,
        finalIsoX: Math.round(iso.x),
        finalIsoY: Math.round(iso.y),
      };
    });
  }, [camera.offsetX, camera.offsetY, camera.zoom, nativeDataV2FacilityPlacements]);

  const nativePortLayerDiagnostics = useMemo(() => {
    if (!pipeline) {
      return {
        fillWater: [] as PlacePortProofRecord[],
        placeChip: [] as PlacePortProofRecord[],
        facilityComposite: [] as PlacePortProofRecord[],
      };
    }

    const placed = pipeline.nativePlacePortProofRecords.filter(
      (record) => record.placed && typeof record.x === "number" && typeof record.y === "number",
    );

    return {
      fillWater: placed.filter((record) => record.source_branch === "PP-B08"),
      placeChip: placed.filter((record) => record.source_branch === "PP-B03" || record.source_branch === "PP-B06"),
      facilityComposite: placed.filter((record) => record.source_branch === "PP-B09"),
    };
  }, [pipeline]);

  const portPieceAuditRows = useMemo(() => {
    if (!pipeline) {
      return [] as PortPieceAuditRow[];
    }

    const activePortCommands = useNativePlacePortProof ? pipeline.nativePlacePortProofCommands : pipeline.portOverlayCommands;
    const evidenceByFacilityId = new Map(PORT_FACILITY_DATA_AUDIT_EVIDENCE.map((row) => [row.facilityId, row]));
    const rows: PortPieceAuditRow[] = [];

    for (const port of PORT_ASSEMBLIES) {
      const base = getPortCompositeBaseCell(port);

      for (const piece of PORT_GATE_PIECES) {
        const mapChip = pipeline.lookups.mapChipById.get(piece.chipId) ?? null;
        const facilityEvidence = evidenceByFacilityId.get(piece.facilityId) ?? null;
        const defaultOffset = DEFAULT_PORT_GATE_LAYOUT[piece.assetKey] ?? { dx: piece.dx, dy: piece.dy };
        const anchorX = base.x + defaultOffset.dx;
        const anchorY = base.y + defaultOffset.dy;
        const iso = worldToIso(anchorX, anchorY, camera.zoom, camera.offsetX, camera.offsetY);

        const asset = pipeline.portAssetCache.get(piece.assetKey)?.image ?? null;
        const optPlacement = PORT_GATE_OPT_PLACEMENT[piece.assetKey];
        const drawW = asset ? asset.width * camera.zoom : 0;
        const drawH = asset ? asset.height * camera.zoom : 0;
        const drawX = asset
          ? iso.x - (optPlacement.cellW * camera.zoom) / 2 + optPlacement.destX * camera.zoom
          : iso.x;
        const drawY = asset
          ? iso.y + (TILE_HEIGHT * camera.zoom) - optPlacement.cellH * camera.zoom + optPlacement.destY * camera.zoom
          : iso.y;
        const spriteOriginX = Math.round(drawX + drawW / 2);
        const spriteOriginY = Math.round(drawY + drawH);

        const proofCommand = activePortCommands.find((command) => command.mapChipId === piece.chipId) ?? null;
        const nativeProofAnchorCell = proofCommand ? `(${proofCommand.cellX},${proofCommand.cellY})` : "UNRESOLVED";
        const identityStatus = mapChip
          ? mapChip.relatedDataId === piece.facilityId
            ? "mapChip.relatedDataId matches facility"
            : `relatedData mismatch expected=${piece.facilityId} actual=${mapChip.relatedDataId}`
          : "missing mapchip row";

        rows.push({
          label: `${port.id}:${piece.assetKey}`,
          pieceKind: "gate",
          facilityId: piece.facilityId,
          mapChipId: piece.chipId,
          relatedDataId: mapChip?.relatedDataId ?? null,
          relatedDataType: mapChip?.relatedDataType ?? null,
          img: mapChip?.img ?? null,
          seb: mapChip?.seb ?? null,
          frame: mapChip?.frame ?? null,
          layer: mapChip?.layer ?? null,
          sizeWidth: mapChip?.sizeWidth ?? 2,
          sizeHeight: mapChip?.sizeHeight ?? 2,
          facilityParentChipId: facilityEvidence?.parentChipId ?? null,
          facilityChips: facilityEvidence?.chips ?? [],
          facilityOffsetX: facilityEvidence?.offsetX ?? null,
          facilityOffsetY: facilityEvidence?.offsetY ?? null,
          facilityOffsetZ: facilityEvidence?.offsetZ ?? null,
          chosenAnchorCell: `(${anchorX},${anchorY}) default-port-renderer`,
          nativeProofAnchorCell,
          finalIso: `(${Math.round(iso.x)},${Math.round(iso.y)})`,
          spriteDrawOrigin: asset ? `(${spriteOriginX},${spriteOriginY})` : "missing-asset",
          sourceResolverFunction: `drawPortAssemblies(DEFAULT) + mapChipById; native anchor evidence via ${
            useNativePlacePortProof
              ? "buildPlacePortEvidenceLayout -> buildPlacePortProofCommand"
              : "buildVerifiedPortReconstructionCommands"
          }`,
          renderReason: "Default Port renderer uses fixed DEFAULT layout for visual baseline; manual panel is sandbox-only.",
          identityStatus,
        });
      }

      for (const piece of PORT_BRIDGE_PIECES) {
        const anchorX = base.x + piece.dx;
        const anchorY = base.y + piece.dy;
        const iso = worldToIso(anchorX, anchorY, camera.zoom, camera.offsetX, camera.offsetY);
        rows.push({
          label: `${port.id}:${piece.assetKey}`,
          pieceKind: "bridge",
          facilityId: null,
          mapChipId: null,
          relatedDataId: null,
          relatedDataType: null,
          img: null,
          seb: null,
          frame: null,
          layer: null,
          sizeWidth: null,
          sizeHeight: null,
          facilityParentChipId: null,
          facilityChips: [],
          facilityOffsetX: null,
          facilityOffsetY: null,
          facilityOffsetZ: null,
          chosenAnchorCell: `(${anchorX},${anchorY}) inferred-bridge`,
          nativeProofAnchorCell: "UNRESOLVED",
          finalIso: `(${Math.round(iso.x)},${Math.round(iso.y)})`,
          spriteDrawOrigin: "inferred-bridge-draw",
          sourceResolverFunction: "drawPortAssemblies (inferred bridge layer)",
          renderReason: "Bridge/body visuals are inferred fallback in lab; keep separate from native/data proof layers.",
          identityStatus: "bridge/body unresolved in current native closure",
        });
      }
    }

    return rows;
  }, [camera.offsetX, camera.offsetY, camera.zoom, pipeline, useNativePlacePortProof]);

  const portPieceIdentitySummary = useMemo(() => {
    const renderedOrderAfterRoot = PORT_GATE_PIECES
      .filter((piece) => piece.assetKey !== "gate_00")
      .map((piece) => piece.chipId);
    const facilityDataRoot = PORT_FACILITY_DATA_AUDIT_EVIDENCE.find((row) => row.facilityId === 7) ?? null;
    const facilityDataChildOrder = facilityDataRoot?.chips ?? [];
    const chipsOrderMismatch =
      facilityDataChildOrder.length > 0 &&
      (facilityDataChildOrder.length !== renderedOrderAfterRoot.length ||
        facilityDataChildOrder.some((value, index) => renderedOrderAfterRoot[index] !== value));

    return {
      renderedOrderAfterRoot,
      facilityDataChildOrder,
      chipsOrderMismatch,
    };
  }, []);

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
      const reset = computeInitialCamera(pipeline.parsedMap.width, pipeline.parsedMap.height, width, height);
      setCamera(reset);
    }
  }, [camera.offsetX, camera.offsetY, camera.zoom, pipeline]);

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
    context.fillStyle = "#0a1018";
    context.fillRect(0, 0, width, height);

    if (!pipeline) {
      drawGridBackground(context, width, height);
      return;
    }

    context.imageSmoothingEnabled = false;

    const filteredCommands = defaultTerrainMode
      ? activeCommands.filter((command) => command.drawGroup === "base-terrain" || command.drawGroup === "nature-object")
      : activeCommands;

    const commandsAfterPortWaterSwap = (() => {
      if (!swapPortWaterToF1Ground) {
        return filteredCommands;
      }

      const portCells = buildManualPortGateFootprintCellSet(portGateLayouts);
      if (portCells.size === 0) {
        return filteredCommands;
      }

      const f1BaseByCell = new Map<string, TileDrawCommand>();
      for (const command of pipeline.f1TerrainCommands) {
        if (command.drawGroup === "base-terrain") {
          f1BaseByCell.set(`${command.cellX},${command.cellY}`, command);
        }
      }

      const fallbackDirtTemplates = buildFallbackDirtTemplates(pipeline.mapChipCommands, pipeline.f1TerrainCommands);

      return filteredCommands.map((command) => {
        if (command.drawGroup === "nature-object") {
          return command;
        }
        if (command.selection.assetFolder !== "chip") {
          return command;
        }
        const cellKey = `${command.cellX},${command.cellY}`;
        if (!portCells.has(cellKey)) {
          return command;
        }

        const replacement = f1BaseByCell.get(cellKey);
        if (replacement && isDirtLikeCommand(replacement)) {
          return replacement;
        }

        const dirtReplacement = buildDirtReplacementForCell(command, fallbackDirtTemplates);
        if (dirtReplacement) {
          return dirtReplacement;
        }

        return command;
      });
    })();

    const sortedCommands = [...commandsAfterPortWaterSwap].sort((left, right) => {
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

    const zoom = camera.zoom;
    const logicalTileWidth = TILE_WIDTH * logicalFootprintScale;
    const logicalTileHeight = TILE_HEIGHT * logicalFootprintScale;
    const shouldDrawTextures = overlayMode !== "diamond-only";
    const shouldDrawDiamonds = overlayMode !== "texture-only";

    if (shouldDrawDiamonds) {
      const seen = new Set<string>();
      context.strokeStyle = "rgba(147, 197, 253, 0.26)";
      context.lineWidth = 1;
      for (const command of commandsAfterPortWaterSwap) {
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
    }

    for (const command of drawCommands) {
      const iso = worldToIso(command.cellX, command.cellY, zoom, camera.offsetX, camera.offsetY);
      const sprite = command.selection;
      const imageAsset = pipeline.imageCache.get(buildAssetCacheKey(sprite.assetFolder, sprite.sourceFilename));
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

    if (showOnePieceFacilities) {
      drawOnePieceFacilityOverlays(context, pipeline.facilityBuildingCache, camera, width, height, !publicMode);
    }
    if (showNativeDataV2FacilityDuplicates) {
      drawNativeDataV2FacilityDuplicateOverlays(
        context,
        nativeDataV2FacilityPlacements,
        pipeline.facilityBuildingCache,
        camera,
        width,
        height,
        !publicMode && showNativeDataV2Labels,
      );
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
    // Keep the map unobstructed by UI overlays; legend is shown in panel UI instead.
    const proofPortCommands = useNativePlacePortProof ? pipeline.nativePlacePortProofCommands : pipeline.portOverlayCommands;
    if (showPortAssemblies || showVerifiedMapChipPortReconstruction) {
      drawVerifiedPortReconstructionProof(
        context,
        pipeline,
        camera,
        width,
        height,
        proofPortCommands,
        showVerifiedMapChipPortReconstruction,
        showVerifiedMapChipPortReconstruction && !publicMode,
      );
    }
    if (showPortAssemblies && !hideOldManualPortOverlay) {
      drawPortAssemblies(context, pipeline.portAssetCache, camera, width, height, portGateLayouts, portBridgeLayouts, showPortBridgePieces, false);
    }
    if (showPortPieceAnchorAuditLayer) {
      drawPortPieceAnchorAuditLayer(context, pipeline, camera);
    }
    if (showPortFillWaterStageLayer || showPortPlaceChipKnownLayer || showPortFacilityCompositeLayer) {
      drawNativePlacePortEvidenceLayers(
        context,
        camera,
        nativePortLayerDiagnostics,
        showPortFillWaterStageLayer,
        showPortPlaceChipKnownLayer,
        showPortFacilityCompositeLayer,
      );
    }
    if (showCleanSourceMapChipRenderRecords) {
      drawCleanSourceMapChipRecords(
        context,
        cleanSourceMapChipRecords,
        pipeline.imageCache,
        camera,
        width,
        height,
        showCleanSourceRecordLabels && !publicMode,
      );
    }

    if (hoveredCell) {
      const isoCenter = worldToIso(hoveredCell.x, hoveredCell.y, zoom, camera.offsetX, camera.offsetY);
      context.strokeStyle = "rgba(255, 255, 255, 0.8)";
      context.lineWidth = 1;
      drawIsoDiamond(context, isoCenter.x, isoCenter.y, (logicalTileWidth * zoom) / 2, (logicalTileHeight * zoom) / 2);
    }

    if (selectedCell) {
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
    activeCommands,
    camera,
    defaultTerrainMode,
    forceVisibleNatureMode,
    forceVisibleLargeNatureMode,
    hoveredCell,
    hideOldManualPortOverlay,
    natureOverlayCellKeys,
    overlayMode,
    pipeline,
    logicalFootprintScale,
    renderMode,
    selectedCell,
    showOnePieceFacilities,
    showNativeDataV2FacilityDuplicates,
    showNativeDataV2Labels,
    nativeDataV2FacilityPlacements,
    showPortAssemblies,
    showPortBridgePieces,
    showVerifiedMapChipPortReconstruction,
    useNativePlacePortProof,
    showPortFillWaterStageLayer,
    showPortPlaceChipKnownLayer,
    showPortFacilityCompositeLayer,
    showPortPieceAnchorAuditLayer,
    showPortManualSandboxOverlay,
    swapPortWaterToF1Ground,
    showCleanSourceMapChipRenderRecords,
    showCleanSourceRecordLabels,
    cleanSourceMapChipRecords,
    nativePortLayerDiagnostics,
    portGateLayouts,
    portBridgeLayouts,
    showNatureDebugOverlay,
    showNatureCandidateBounds,
    showTextureBounds,
    showTileCenters,
    terrainAlignmentMode,
  ]);

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

  const selectedCellNativePlacePortRecords = useMemo(() => {
    if (!selectedCell || !pipeline) {
      return [] as PlacePortProofRecord[];
    }
    return pipeline.nativePlacePortProofRecords.filter((record) => record.x === selectedCell.x && record.y === selectedCell.y);
  }, [pipeline, selectedCell]);

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
        const image = pipeline.imageCache.get(buildAssetCacheKey(command.selection.assetFolder, command.selection.sourceFilename))?.image;
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
      isPlayerMadeSurface(
        row.name,
        getImageTableForFolder(
          getAssetFolderFromRes(row.res),
          pipeline.lookups.imageById,
          pipeline.lookups.buildingImageById,
          pipeline.lookups.natureImageById,
          new Map<number, string>(),
        ).get(row.img) ?? "",
      ),
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
    const validRows = allRows.filter((row) => {
      const folder = getAssetFolderFromRes(row.res);
      const filename = getImageTableForFolder(
        folder,
        pipeline.lookups.imageById,
        pipeline.lookups.buildingImageById,
        pipeline.lookups.natureImageById,
        new Map<number, string>(),
      ).get(row.img) ?? "";
      return !isPlayerMadeSurface(row.name, filename);
    });
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
    const imageAsset = pipeline.imageCache.get(buildAssetCacheKey(top.selection.assetFolder, top.selection.sourceFilename));
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
    const imageAsset = pipeline.imageCache.get(buildAssetCacheKey(top.selection.assetFolder, top.selection.sourceFilename));
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
    const imageAsset = pipeline.imageCache.get(buildAssetCacheKey(natureCommand.selection.assetFolder, natureCommand.selection.sourceFilename));
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
    return null;
  }

  function onPointerDown(event: React.PointerEvent<HTMLCanvasElement>) {
    event.currentTarget.setPointerCapture(event.pointerId);
    setDragging(true);
  }

  function onPointerUp(event: React.PointerEvent<HTMLCanvasElement>) {
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    setDragging(false);
  }

  function onPointerLeave() {
    setDragging(false);
    setHoveredCell(null);
  }

  function onPointerMove(event: React.PointerEvent<HTMLCanvasElement>) {
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

    setHoveredCell(pointerToWorld(event, canvasRef.current, camera));
  }

  function onCanvasClick(event: React.MouseEvent<HTMLCanvasElement>) {
    if (suppressNextCanvasClickRef.current) {
      suppressNextCanvasClickRef.current = false;
      return;
    }

    if (!pipeline || !canvasRef.current) {
      return;
    }
    setSelectedCell(pointerToWorldFromClient(event.clientX, event.clientY, canvasRef.current, camera));
  }

  function onWheel(event: React.WheelEvent<HTMLCanvasElement>) {
    if (!event.shiftKey) {
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
    setCamera(computeResetCamera(pipeline.parsedMap.width, pipeline.parsedMap.height, width, height, 0.65));
  }

  function togglePublicFacilities() {
    const next = !(showOnePieceFacilities && showPortAssemblies);
    setShowOnePieceFacilities(next);
    setShowPortAssemblies(next);
  }

  function focusVerifiedPort(portId: PortAssembly["id"]) {
    if (!pipeline || !containerRef.current) {
      return;
    }

    const port = PORT_ASSEMBLIES.find((entry) => entry.id === portId);
    if (!port) {
      return;
    }

    const width = containerRef.current.clientWidth;
    const height = containerRef.current.clientHeight;
    const zoom = Math.max(camera.zoom, 1.05);
    const baseX = port.cellX - 2;
    const baseY = port.cellY - 2;
    const isoX = (baseX - baseY) * (TILE_WIDTH / 2);
    const isoY = (baseX + baseY) * (TILE_HEIGHT / 2);
    setCamera({
      zoom,
      offsetX: width / 2 - isoX,
      offsetY: height / 2 - isoY,
    });
  }

  return (
    <div className={publicMode ? "mx-auto max-w-[1500px]" : "mx-auto max-w-[1500px] p-4"}>
      {!publicMode && (
        <>
          <div className="flex flex-wrap items-center justify-between gap-2">
              <h1 className="text-lg font-semibold">Engine-Driven Isometric Render Lab</h1>
            <div className="flex items-center gap-2 text-xs">
              <a href="/runtime-world-grid-test" className="rounded border border-border bg-card px-2 py-1 hover:bg-muted">
                Open top-view grid test
              </a>
              <a href="/runtime-world-render-lab" className="rounded border border-border bg-card px-2 py-1 hover:bg-muted">
                Open isometric render lab
              </a>
            </div>
          </div>
          <p className="text-sm text-muted-foreground">
            Chain: MapChip.res/img/seb/frame chooses the correct folder registry first, then resolves PNG and SEB frame metadata.
            Use Shift+wheel to zoom the map; regular wheel scrolls the page.
          </p>
        </>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
        <button type="button" onClick={resetCamera} className="rounded border border-border bg-card px-3 py-1 hover:bg-muted">
          reset camera
        </button>
        {!publicMode && (
          <>
            <label className="flex items-center gap-1 rounded border border-border bg-card px-2 py-1">
              <input
                type="checkbox"
                checked={showOnePieceFacilities}
                onChange={(event) => setShowOnePieceFacilities(event.target.checked)}
                className="h-3 w-3"
              />
              old duplicate layer (-2,-2)
            </label>
            <label className="flex items-center gap-1 rounded border border-border bg-card px-2 py-1">
              <input
                type="checkbox"
                checked={showNativeDataV2FacilityDuplicates}
                onChange={(event) => setShowNativeDataV2FacilityDuplicates(event.target.checked)}
                className="h-3 w-3"
              />
              native-data-v2 duplicates (no -2,-2)
            </label>
            <label className="flex items-center gap-1 rounded border border-border bg-card px-2 py-1">
              <input
                type="checkbox"
                checked={showNativeDataV2Labels}
                onChange={(event) => setShowNativeDataV2Labels(event.target.checked)}
                className="h-3 w-3"
              />
              native-data-v2 labels
            </label>
            <label className="flex items-center gap-1 rounded border border-border bg-card px-2 py-1">
              <input
                type="checkbox"
                checked={showPortFillWaterStageLayer}
                onChange={(event) => setShowPortFillWaterStageLayer(event.target.checked)}
                className="h-3 w-3"
              />
              port FillWater pre-stage
            </label>
            <label className="flex items-center gap-1 rounded border border-border bg-card px-2 py-1">
              <input
                type="checkbox"
                checked={showPortPlaceChipKnownLayer}
                onChange={(event) => setShowPortPlaceChipKnownLayer(event.target.checked)}
                className="h-3 w-3"
              />
              port PlaceChip-known
            </label>
            <label className="flex items-center gap-1 rounded border border-border bg-card px-2 py-1">
              <input
                type="checkbox"
                checked={showPortFacilityCompositeLayer}
                onChange={(event) => setShowPortFacilityCompositeLayer(event.target.checked)}
                className="h-3 w-3"
              />
              port FacilityData composite candidate
            </label>
            <label className="flex items-center gap-1 rounded border border-border bg-card px-2 py-1">
              <input
                type="checkbox"
                checked={showPortPieceAnchorAuditLayer}
                onChange={(event) => setShowPortPieceAnchorAuditLayer(event.target.checked)}
                className="h-3 w-3"
              />
              port anchor/sprite-origin audit layer
            </label>
            <label className="flex items-center gap-1 rounded border border-border bg-card px-2 py-1">
              <input
                type="checkbox"
                checked={showPortManualSandboxOverlay}
                onChange={(event) => setShowPortManualSandboxOverlay(event.target.checked)}
                className="h-3 w-3"
              />
              manual sandbox overlay (non-evidence)
            </label>
            <label className="flex items-center gap-1 rounded border border-border bg-card px-2 py-1">
              <input
                type="checkbox"
                checked={swapPortWaterToF1Ground}
                onChange={(event) => setSwapPortWaterToF1Ground(event.target.checked)}
                className="h-3 w-3"
              />
              force dirt under gate footprint
            </label>
            <label className="flex items-center gap-1 rounded border border-border bg-card px-2 py-1">
              <input
                type="checkbox"
                checked={showPortBridgePieces}
                onChange={(event) => setShowPortBridgePieces(event.target.checked)}
                className="h-3 w-3"
              />
              show hashi00 4x2 groups
            </label>
            <label className="flex items-center gap-1 rounded border border-border bg-card px-2 py-1">
              <input
                type="checkbox"
                checked={showCleanSourceMapChipRenderRecords}
                onChange={(event) => setShowCleanSourceMapChipRenderRecords(event.target.checked)}
                className="h-3 w-3"
              />
              show clean-source MapChip render records
            </label>
            <label className="flex items-center gap-1 rounded border border-border bg-card px-2 py-1">
              <input
                type="checkbox"
                checked={showCleanSourceRecordLabels}
                onChange={(event) => setShowCleanSourceRecordLabels(event.target.checked)}
                className="h-3 w-3"
              />
              clean-source labels
            </label>
            <span className="rounded border border-border bg-card px-2 py-1">zoom {camera.zoom.toFixed(2)}x</span>
            <span className="rounded border border-border bg-card px-2 py-1">
              camera ({Math.round(camera.offsetX)}, {Math.round(camera.offsetY)})
            </span>
          </>
        )}
      </div>

      {!publicMode && (
        <div className="mt-2 rounded border border-amber-500/40 bg-amber-950/25 px-3 py-2 text-xs text-amber-100">
          Port manual layout controls are temporary lab-only corrections for testing and must be removed later; they are not permanent placement rules.
        </div>
      )}


      {!publicMode && pipeline && (
        <div className="mt-3 rounded border border-sky-500/30 bg-card p-3 text-xs">
          <div className="flex items-center justify-between gap-3">
            <div>
              <div className="font-medium">Port 4x4 Manual Layout (per port)</div>
              <div className="mt-1 text-muted-foreground">
                Port root piece uses [cellX-2..cellX-1] x [cellY-2..cellY-1]. The full 4x4 composite expands from that root to [cellX-2..cellX+1] x [cellY-2..cellY+1].
              </div>
            </div>
            <button
              type="button"
              onClick={() => resetPortGateLayout()}
              className="rounded border border-border bg-background px-2 py-1 hover:bg-muted"
            >
              reset all
            </button>
          </div>
          <div className="mt-3 grid gap-3">
            {PORT_ASSEMBLIES.map((port) => {
              const portLayout = portGateLayouts[port.id] ?? DEFAULT_PORT_GATE_LAYOUT;
              const bridgeLayout = portBridgeLayouts[port.id] ?? DEFAULT_PORT_BRIDGE_LAYOUT;
              return (
                <div key={port.id} className="rounded border border-border/60 bg-background p-2">
                  <div className="mb-2 flex items-center justify-between gap-2">
                    <div className="font-medium">{port.name} lv{port.unlockLevel} ({port.id})</div>
                    <button
                      type="button"
                      onClick={() => resetPortGateLayout(port.id)}
                      className="rounded border border-border bg-card px-2 py-1 hover:bg-muted"
                    >
                      reset this port
                    </button>
                  </div>
                  <div className="grid gap-2">
                    {PORT_GATE_PIECES.map((piece) => {
                      const offset = portLayout[piece.assetKey] ?? { dx: piece.dx, dy: piece.dy };
                      return (
                        <div key={`${port.id}-${piece.assetKey}`} className="grid grid-cols-[1fr_auto_auto] items-center gap-2 rounded border border-border/60 bg-card px-2 py-1">
                          <div>
                            <div className="font-medium">{piece.assetKey}</div>
                            <div className="text-muted-foreground">chip {piece.chipId}, facility {piece.facilityId}, 2x2</div>
                          </div>
                          <label className="flex items-center gap-1">
                            dx
                            <input
                              type="number"
                              step="0.25"
                              defaultValue={offset.dx}
                              onChange={(event) => {
                                const next = Number(event.target.value);
                                if (Number.isFinite(next)) {
                                  updatePortGateOffset(port.id, piece.assetKey, "dx", next);
                                }
                              }}
                              className="w-16 rounded border border-border bg-card px-2 py-1"
                            />
                          </label>
                          <label className="flex items-center gap-1">
                            dy
                            <input
                              type="number"
                              step="0.25"
                              defaultValue={offset.dy}
                              onChange={(event) => {
                                const next = Number(event.target.value);
                                if (Number.isFinite(next)) {
                                  updatePortGateOffset(port.id, piece.assetKey, "dy", next);
                                }
                              }}
                              className="w-16 rounded border border-border bg-card px-2 py-1"
                            />
                          </label>
                        </div>
                      );
                    })}

                    <div className="grid grid-cols-[1fr_auto_auto] items-center gap-2 rounded border border-sky-500/50 bg-sky-950/20 px-2 py-1">
                      <div>
                        <div className="font-medium">hashi00 4x2 group (8 tiles)</div>
                        <div className="text-muted-foreground">Moves as one structure for this port only</div>
                      </div>
                      <label className="flex items-center gap-1">
                        dx
                        <input
                          type="number"
                          step="0.25"
                          defaultValue={bridgeLayout.dx}
                          onChange={(event) => {
                            const next = Number(event.target.value);
                            if (Number.isFinite(next)) {
                              updatePortBridgeOffset(port.id, "dx", next);
                            }
                          }}
                          className="w-16 rounded border border-border bg-card px-2 py-1"
                        />
                      </label>
                      <label className="flex items-center gap-1">
                        dy
                        <input
                          type="number"
                          step="0.25"
                          defaultValue={bridgeLayout.dy}
                          onChange={(event) => {
                            const next = Number(event.target.value);
                            if (Number.isFinite(next)) {
                              updatePortBridgeOffset(port.id, "dy", next);
                            }
                          }}
                          className="w-16 rounded border border-border bg-card px-2 py-1"
                        />
                      </label>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
          <pre className="mt-3 overflow-auto rounded border border-border bg-background p-2 text-[11px] text-muted-foreground">
            {JSON.stringify({ gateLayouts: portGateLayouts, bridgeLayouts: portBridgeLayouts }, null, 2)}
          </pre>
        </div>
      )}


      {error && <div className="mt-3 rounded border border-red-500/40 bg-red-950/40 p-3 text-xs text-red-100">{error}</div>}

      <div ref={containerRef} className="relative mt-3 h-[74vh] overflow-hidden rounded border border-border bg-black">
        <canvas
          ref={canvasRef}
          className={`h-full w-full touch-none ${dragging ? "cursor-grabbing" : "cursor-grab"}`}
          onPointerDown={onPointerDown}
          onPointerUp={onPointerUp}
          onPointerLeave={onPointerLeave}
          onPointerMove={onPointerMove}
          onWheel={onWheel}
          onClick={onCanvasClick}
          aria-label="Engine driven map renderer canvas"
        />
        {!publicMode && (
          <div className="absolute left-2 top-2 z-20 w-64 rounded border border-sky-400/60 bg-slate-950/85 p-2 text-xs text-slate-100 shadow-lg backdrop-blur-sm">
            <div className="mb-2 font-medium text-sky-200">Quick Piece Adjust</div>
            <label className="mb-2 block">
              <div className="mb-1 text-[11px] text-slate-300">port</div>
              <select
                value={quickControlPortId}
                onChange={(event) => setQuickControlPortId(event.target.value as PortAssembly["id"])}
                className="w-full rounded border border-slate-600 bg-slate-900 px-2 py-1 text-xs"
              >
                {PORT_ASSEMBLIES.map((port) => (
                  <option key={port.id} value={port.id}>
                    {port.name} lv{port.unlockLevel}
                  </option>
                ))}
              </select>
            </label>

            <div className="mb-2 flex items-center gap-1">
              <button
                type="button"
                onClick={() => stepQuickControlPiece(-1)}
                className="rounded border border-slate-600 bg-slate-900 px-2 py-1 hover:bg-slate-800"
                aria-label="Previous piece"
              >
                &lt;
              </button>
              <select
                value={quickControlPieceKey}
                onChange={(event) => setQuickControlPieceKey(event.target.value as PortGatePiece["assetKey"] | "hashi_group")}
                className="min-w-0 flex-1 rounded border border-slate-600 bg-slate-900 px-2 py-1 text-xs"
              >
                {quickControlPieceOptions.map((option) => (
                  <option key={option.key} value={option.key}>
                    {option.label}
                  </option>
                ))}
              </select>
              <button
                type="button"
                onClick={() => stepQuickControlPiece(1)}
                className="rounded border border-slate-600 bg-slate-900 px-2 py-1 hover:bg-slate-800"
                aria-label="Next piece"
              >
                &gt;
              </button>
            </div>

            <div className="grid grid-cols-2 gap-2">
              <label className="block">
                <div className="mb-1 text-[11px] text-slate-300">dx</div>
                <input
                  type="text"
                  inputMode="decimal"
                  value={quickControlDxInput}
                  onChange={(event) => updateQuickControlOffset("dx", event.target.value)}
                  onBlur={() => sanitizeQuickControlInput("dx")}
                  className="w-full rounded border border-slate-600 bg-slate-900 px-2 py-1 text-xs"
                />
              </label>
              <label className="block">
                <div className="mb-1 text-[11px] text-slate-300">dy</div>
                <input
                  type="text"
                  inputMode="decimal"
                  value={quickControlDyInput}
                  onChange={(event) => updateQuickControlOffset("dy", event.target.value)}
                  onBlur={() => sanitizeQuickControlInput("dy")}
                  className="w-full rounded border border-slate-600 bg-slate-900 px-2 py-1 text-xs"
                />
              </label>
            </div>
            <div className="mt-2 rounded border border-slate-700 bg-slate-900/70 px-2 py-1 text-[11px] text-slate-300">
              gate cells: {portGateWaterAudit.cells} | water chips under gate: {portGateWaterAudit.before} -&gt; {portGateWaterAudit.after}
            </div>
          </div>
        )}
      </div>

      {!publicMode && pipeline && (
        <div className="mt-3 rounded border border-teal-500/35 bg-teal-950/20 p-3 text-xs">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="font-medium">Clean-Source MapChip Render Records (lab-only)</div>
            <div className="text-muted-foreground">
              records {cleanSourceMapChipRecords.length} | MapChipRect available: {cleanSourceMapChipRectAvailable ? "yes" : "no"}
            </div>
          </div>
          <div className="mt-1 text-muted-foreground">
            Records use .map f2 -&gt; MapChip.id, canonical MapChip columns, and relatedDataType/relatedDataId for facility linkage.
          </div>
          <div className="mt-2 max-h-52 overflow-auto rounded border border-border/60 bg-background">
            <table className="w-full text-left font-mono text-[11px]">
              <thead>
                <tr className="border-b border-border text-muted-foreground">
                  <th className="py-0.5 pr-2">mapChip</th>
                  <th className="py-0.5 pr-2">cell</th>
                  <th className="py-0.5 pr-2">class</th>
                  <th className="py-0.5 pr-2">rect source</th>
                  <th className="py-0.5 pr-2">sprite source</th>
                  <th className="py-0.5 pr-2">old path</th>
                  <th className="py-0.5 pr-2">position changed</th>
                </tr>
              </thead>
              <tbody>
                {cleanSourceComparisonRows.slice(0, 120).map((row) => (
                  <tr key={`${row.mapChipId}-${row.cleanLabel}-${row.oldRendererPath}`} className="border-b border-border/30 align-top">
                    <td className="py-0.5 pr-2">{row.mapChipId}</td>
                    <td className="py-0.5 pr-2">{row.cleanLabel}</td>
                    <td className="py-0.5 pr-2">{row.cleanLabel}</td>
                    <td className="py-0.5 pr-2">{row.cleanFootprintSource}</td>
                    <td className="py-0.5 pr-2">{row.cleanSpriteSource}</td>
                    <td className="py-0.5 pr-2">{row.oldRendererPath}</td>
                    <td className="py-0.5 pr-2">{row.visiblePositionChanged}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {!publicMode && pipeline && (
        <div className="mt-3 rounded border border-cyan-500/30 bg-card p-3 text-xs">
          <div className="font-medium">runtime-lab-port-facility-placement-data-audit</div>
          <div className="mt-1 text-muted-foreground">
            Audit table is data/native focused. Manual sandbox controls are excluded from default renderer rules and shown only as inspection overlay.
          </div>
          <div className="mt-2 overflow-x-auto">
            <table className="w-full text-left font-mono text-[11px]">
              <thead>
                <tr className="border-b border-border text-muted-foreground">
                  <th className="py-0.5 pr-2">label</th>
                  <th className="py-0.5 pr-2">facility id</th>
                  <th className="py-0.5 pr-2">MapChip id</th>
                  <th className="py-0.5 pr-2">relatedDataId/type</th>
                  <th className="py-0.5 pr-2">img/seb/frame/layer</th>
                  <th className="py-0.5 pr-2">size</th>
                  <th className="py-0.5 pr-2">FacilityData parent/chips/offsets</th>
                  <th className="py-0.5 pr-2">chosen anchor cell</th>
                  <th className="py-0.5 pr-2">native proof anchor</th>
                  <th className="py-0.5 pr-2">final iso</th>
                  <th className="py-0.5 pr-2">sprite draw origin</th>
                  <th className="py-0.5 pr-2">source resolver function</th>
                  <th className="py-0.5 pr-2">render reason</th>
                  <th className="py-0.5 pr-2">identity status</th>
                </tr>
              </thead>
              <tbody>
                {portPieceAuditRows.map((row) => (
                  <tr key={`${row.label}-${row.chosenAnchorCell}`} className="border-b border-border/40 align-top">
                    <td className="py-0.5 pr-2">{row.label}</td>
                    <td className="py-0.5 pr-2">{row.facilityId ?? "n/a"}</td>
                    <td className="py-0.5 pr-2">{row.mapChipId ?? "n/a"}</td>
                    <td className="py-0.5 pr-2">{row.relatedDataId ?? "n/a"}/{row.relatedDataType ?? "n/a"}</td>
                    <td className="py-0.5 pr-2">{row.img ?? "n/a"}/{row.seb ?? "n/a"}/{row.frame ?? "n/a"}/{row.layer ?? "n/a"}</td>
                    <td className="py-0.5 pr-2">{row.sizeWidth ?? "n/a"}x{row.sizeHeight ?? "n/a"}</td>
                    <td className="py-0.5 pr-2">
                      parent={row.facilityParentChipId ?? "n/a"}, chips={row.facilityChips.length > 0 ? row.facilityChips.join(",") : "n/a"}, off=({row.facilityOffsetX ?? "n/a"},{row.facilityOffsetY ?? "n/a"},{row.facilityOffsetZ ?? "n/a"})
                    </td>
                    <td className="py-0.5 pr-2">{row.chosenAnchorCell}</td>
                    <td className="py-0.5 pr-2">{row.nativeProofAnchorCell}</td>
                    <td className="py-0.5 pr-2">{row.finalIso}</td>
                    <td className="py-0.5 pr-2">{row.spriteDrawOrigin}</td>
                    <td className="py-0.5 pr-2">{row.sourceResolverFunction}</td>
                    <td className="py-0.5 pr-2">{row.renderReason}</td>
                    <td className="py-0.5 pr-2">{row.identityStatus}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="mt-3 grid gap-2 md:grid-cols-3">
            <div className="rounded border border-border/60 bg-background p-2">
              <div className="font-medium">FacilityData chips order (facility 7)</div>
              <div className="mt-1 text-muted-foreground">
                {portPieceIdentitySummary.facilityDataChildOrder.length > 0
                  ? portPieceIdentitySummary.facilityDataChildOrder.join(" -> ")
                  : "UNRESOLVED"}
              </div>
            </div>
            <div className="rounded border border-border/60 bg-background p-2">
              <div className="font-medium">Current rendered gate order (after root)</div>
              <div className="mt-1 text-muted-foreground">{portPieceIdentitySummary.renderedOrderAfterRoot.join(" -> ")}</div>
            </div>
            <div className="rounded border border-border/60 bg-background p-2">
              <div className="font-medium">Order audit</div>
              <div className="mt-1 text-muted-foreground">
                {portPieceIdentitySummary.chipsOrderMismatch
                  ? "FacilityData child order does not match current rendered sequence."
                  : "No mismatch detected from current FacilityData evidence."}
              </div>
            </div>
          </div>
        </div>
      )}

      {RENDER_LAB_DEBUG_PANELS_ENABLED && !publicMode && pipeline && pipeline.portInterpretationPreviews.length > 0 && (
        <div className="mt-4 rounded border border-cyan-500/30 bg-card p-3 text-xs">
          <div className="font-medium">Port Interpretation Comparisons</div>
          <div className="mt-1 text-muted-foreground">
            Each card is a 25x25 crop around both verified ports. Candidates are ranked from the actual shoreline map cells and ignore stored manual offsets.
          </div>
          <div className="mt-3 grid gap-3 xl:grid-cols-2">
            {pipeline.portInterpretationPreviews.map((preview) => (
              <div key={preview.key} className="rounded border border-border bg-background p-3">
                <div className="flex items-center justify-between gap-3">
                  <div className="font-medium">{preview.label}</div>
                  <div className="text-muted-foreground">score {preview.totalScore}</div>
                </div>
                <div className="mt-1 text-muted-foreground">{preview.description}</div>
                <div className="mt-3 grid gap-3 lg:grid-cols-2">
                  {preview.crops.map((crop) => (
                    <div key={`${preview.key}-${crop.portId}`} className="rounded border border-border/60 bg-card p-2">
                      <div className="flex items-center justify-between gap-2">
                        <div className="font-medium">{crop.portName} lv{crop.unlockLevel}</div>
                        <div className="text-muted-foreground">root {crop.rootCellX},{crop.rootCellY}</div>
                      </div>
                      <div className="mt-2 overflow-hidden rounded border border-border bg-slate-950">
                        <PortInterpretationCropCanvas
                          pipeline={pipeline}
                          crop={crop}
                          natureCategoryVisibility={natureCategoryVisibility}
                        />
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {RENDER_LAB_DEBUG_PANELS_ENABLED && !publicMode && pipeline && portProofComparison && (
        <div className="fixed bottom-4 right-4 z-50 max-w-[360px] rounded border border-cyan-400/50 bg-slate-950/92 p-3 text-[11px] text-slate-100 shadow-2xl">
          <div className="font-semibold text-cyan-200">
            {useNativePlacePortProof ? "Native PlacePort Evidence Proof" : "Verified MapChip Port Proof"}
          </div>
          <div className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1">
            <div>raw .map port cells</div>
            <div>{pipeline.portProofStats.rawPortCellCount}</div>
            <div>port draw commands</div>
            <div>{useNativePlacePortProof ? pipeline.nativePlacePortProofCommands.length : pipeline.portProofStats.portDrawCommandCount}</div>
            <div>resolved building</div>
            <div>{pipeline.portProofStats.resolvedBuildingCount}</div>
            <div>resolved chip</div>
            <div>{pipeline.portProofStats.resolvedChipCount}</div>
            <div>resolved wall</div>
            <div>{pipeline.portProofStats.resolvedWallCount}</div>
            <div>resolved nature</div>
            <div>{pipeline.portProofStats.resolvedNatureCount}</div>
            <div>skipped/missing</div>
            <div>{pipeline.portProofStats.skippedOrMissingCount}</div>
          </div>
          <div className="mt-3 font-medium text-cyan-200">Why it may still look unchanged</div>
          <div className="mt-1 space-y-1 text-slate-200">
            <div>same coordinates? {portProofComparison.sameCoordinates ? "yes" : "no"}</div>
            <div>same asset filenames? {portProofComparison.sameAssetFilenames ? "yes" : "no"}</div>
            <div>old layer covering it? {portProofComparison.oldLayerCoveringIt ? "yes" : "no"}</div>
            <div>toggle disabled? {portProofComparison.toggleDisabled ? "yes" : "no"}</div>
            <div>no resolved commands? {portProofComparison.noResolvedCommands ? "yes" : "no"}</div>
          </div>
          <div className="mt-3 rounded border border-slate-700 bg-slate-900/80 p-2 text-slate-100">
            {portProofComparison.verdict}
          </div>
          {useNativePlacePortProof && (
            <>
              {pipeline.nativePlacePortProofWarnings.length > 0 && (
                <div className="mt-2 rounded border border-amber-500/40 bg-amber-950/40 p-2 text-amber-100">
                  {pipeline.nativePlacePortProofWarnings.map((warning) => (
                    <div key={warning}>- {warning}</div>
                  ))}
                </div>
              )}
              <div className="mt-2 max-h-48 overflow-auto rounded border border-slate-700 bg-slate-900/80 p-2">
                <div className="mb-1 font-medium text-cyan-100">Native PlacePort records</div>
                <table className="w-full border-collapse text-[10px]">
                  <thead>
                    <tr className="text-slate-300">
                      <th className="pr-2 text-left">chip</th>
                      <th className="pr-2 text-left">branch</th>
                      <th className="pr-2 text-left">offset</th>
                      <th className="text-left">conf</th>
                    </tr>
                  </thead>
                  <tbody>
                    {pipeline.nativePlacePortProofRecords.slice(0, 32).map((record, index) => (
                      <tr key={`${record.source_branch}-${record.chip_id ?? "na"}-${index}`} className="border-t border-slate-800">
                        <td className="pr-2">{record.chip_id ?? "?"}:{record.chip_name}</td>
                        <td className="pr-2">{record.source_branch}</td>
                        <td className="pr-2">{record.offset_x ?? "?"},{record.offset_y ?? "?"}</td>
                        <td>{record.confidence}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>
      )}

      {RENDER_LAB_DEBUG_PANELS_ENABLED && !publicMode && pipeline && (
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
                <div className="font-medium">Port 4x4 Manual Layout (TEMP DEBUG ONLY)</div>
                <div className="mt-1 text-muted-foreground">
                  Temporary experimentation panel. Any dx/dy entered here is a manual correction for testing only and must never be used as permanent runtime logic.
                </div>
              </div>
              <button
                type="button"
                onClick={() => resetPortGateLayout()}
                className="rounded border border-border bg-background px-2 py-1 hover:bg-muted"
              >
                reset
              </button>
            </div>
            <div className="mt-3 grid gap-2">
              {PORT_GATE_PIECES.map((piece) => {
                const offset = (portGateLayouts[PORT_ASSEMBLIES[0]?.id ?? "port-level-7"] ?? DEFAULT_PORT_GATE_LAYOUT)[piece.assetKey];
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
                        onChange={(event) => updatePortGateOffset(PORT_ASSEMBLIES[0]?.id ?? "port-level-7", piece.assetKey, "dx", Number(event.target.value))}
                        className="w-16 rounded border border-border bg-card px-2 py-1"
                      />
                    </label>
                    <label className="flex items-center gap-1">
                      dy
                      <input
                        type="number"
                        step="0.25"
                        value={offset.dy}
                        onChange={(event) => updatePortGateOffset(PORT_ASSEMBLIES[0]?.id ?? "port-level-7", piece.assetKey, "dy", Number(event.target.value))}
                        className="w-16 rounded border border-border bg-card px-2 py-1"
                      />
                    </label>
                  </div>
                );
              })}
            </div>
            <pre className="mt-3 overflow-auto rounded border border-border bg-background p-2 text-[11px] text-muted-foreground">
              {JSON.stringify(portGateLayouts, null, 2)}
            </pre>
          </div>

          {diagnostics ? RENDER_LAB_DEBUG_PANELS_ENABLED && (
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


          {natureDebugSummary ? RENDER_LAB_DEBUG_PANELS_ENABLED && (
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
                      ({x}, {y}) f0={c?.fields.f0 ?? "?"} f2={c?.fields.f2 ?? "?"} f1={c?.fields.f1 ?? "?"}
                    </div>
                  );
                })}
                {useNativePlacePortProof && selectedCellNativePlacePortRecords.length > 0 && (
                  <div className="mt-2 rounded border border-cyan-500/40 bg-cyan-950/30 p-2 text-[11px]">
                    <div className="font-sans font-medium text-cyan-100">Native PlacePort proof on selected tile</div>
                    {selectedCellNativePlacePortRecords.map((record, index) => (
                      <div key={`${record.source_branch}-${record.chip_id ?? "na"}-${index}`} className="mt-1 text-cyan-50">
                        chip {record.chip_id ?? "?"} {record.chip_name} | {record.source_branch} | offset ({record.offset_x ?? "?"},{record.offset_y ?? "?"}) | {record.confidence}
                      </div>
                    ))}
                  </div>
                )}
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

          {useNativePlacePortProof && (
            <div className="rounded border border-cyan-500/30 bg-card p-3 lg:col-span-2">
              <div className="font-medium">Native PlacePort Proof Records</div>
              <div className="mt-1 text-muted-foreground">
                Structured records from resolved PlacePort branches. UNKNOWN rows are intentionally non-placed where coordinates are not proven.
              </div>
              <div className="mt-2 overflow-x-auto">
                <table className="w-full text-left font-mono text-[11px]">
                  <thead>
                    <tr className="border-b border-border text-muted-foreground">
                      <th className="py-0.5 pr-2">chip_id</th>
                      <th className="py-0.5 pr-2">chip_name</th>
                      <th className="py-0.5 pr-2">source_branch</th>
                      <th className="py-0.5 pr-2">x</th>
                      <th className="py-0.5 pr-2">y</th>
                      <th className="py-0.5 pr-2">offset_x</th>
                      <th className="py-0.5 pr-2">offset_y</th>
                      <th className="py-0.5 pr-2">condition</th>
                      <th className="py-0.5 pr-2">confidence</th>
                      <th className="py-0.5 pr-2">evidence_note</th>
                    </tr>
                  </thead>
                  <tbody>
                    {pipeline.nativePlacePortProofRecords.map((record, index) => (
                      <tr key={`${record.source_branch}-${record.chip_id ?? "na"}-${index}`} className="border-b border-border/40 align-top">
                        <td className="py-0.5 pr-2">{record.chip_id ?? "UNKNOWN"}</td>
                        <td className="py-0.5 pr-2">{record.chip_name}</td>
                        <td className="py-0.5 pr-2">{record.source_branch}</td>
                        <td className="py-0.5 pr-2">{record.x ?? "UNKNOWN"}</td>
                        <td className="py-0.5 pr-2">{record.y ?? "UNKNOWN"}</td>
                        <td className="py-0.5 pr-2">{record.offset_x ?? "UNKNOWN"}</td>
                        <td className="py-0.5 pr-2">{record.offset_y ?? "UNKNOWN"}</td>
                        <td className="py-0.5 pr-2">{record.condition}</td>
                        <td className="py-0.5 pr-2">{record.confidence}</td>
                        <td className="py-0.5 pr-2">{record.evidence_note}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

const CLEAN_SPECIAL_CATEGORY_LABELS = new Map<number, string>([
  [0x4b, "JobShop"],
  [0x4d, "TownHall"],
  [0x57, "Warehouse"],
  [0x42, "Port"],
  [0x4f, "Wall"],
  [0x52, "Storehouse"],
  [0x51, "Unknown_0x51"],
]);

type BuildCleanSourceRecordsArgs = {
  parsedMap: ParsedMapBinary;
  mapChipById: Map<number, MapChipRow>;
  terrainById: Map<number, TerrainRow>;
  imageById: Map<number, string>;
  buildingImageById: Map<number, string>;
  natureImageById: Map<number, string>;
  wallImageById: Map<number, string>;
  sebById: Map<number, string>;
  buildingSebById: Map<number, string>;
  natureSebById: Map<number, string>;
  wallSebById: Map<number, string>;
  sebFiles: Map<string, SebFile>;
  buildingSebFiles: Map<string, SebFile>;
  natureSebFiles: Map<string, SebFile>;
  wallSebFiles: Map<string, SebFile>;
  optByStem: Map<string, OptMetadata>;
  imageCache: Map<string, LoadedImageAsset>;
  atlasByName: Map<string, AtlasRegion>;
};

function classifySpecialMapChipCategory(category: number): string {
  return CLEAN_SPECIAL_CATEGORY_LABELS.get(category) ?? "Other";
}

function isStructureLikeMapChip(chip: MapChipRow): boolean {
  if (chip.relatedDataType === 1 && chip.relatedDataId >= 0) {
    return true;
  }
  if ((chip.res === 23 || chip.res === 21) && CLEAN_SPECIAL_CATEGORY_LABELS.has(chip.category)) {
    return true;
  }
  if ((chip.res === 23 || chip.res === 21) && PORT_RELATED_CHIP_IDS.has(chip.id)) {
    return true;
  }
  if ((chip.res === 23 || chip.res === 21) && (chip.type === 17 || chip.type === 19 || chip.type === 22 || chip.type === 25 || chip.type === 28)) {
    return true;
  }
  return false;
}

function buildCleanSourceMapChipRenderRecords(args: BuildCleanSourceRecordsArgs): CleanSourceMapChipRenderRecord[] {
  const records: CleanSourceMapChipRenderRecord[] = [];

  for (const cell of args.parsedMap.cells) {
    const chip = args.mapChipById.get(cell.fields.f2);
    if (!chip || !isStructureLikeMapChip(chip)) {
      continue;
    }

    const chipFolder = getMapChipAssetFolder(chip);
    const selection = resolveSpriteSelection(
      chipFolder,
      chip,
      getImageTableForFolder(chipFolder, args.imageById, args.buildingImageById, args.natureImageById, args.wallImageById),
      getSebTableForFolder(chipFolder, args.sebById, args.buildingSebById, args.natureSebById, args.wallSebById),
      getSebFilesForFolder(chipFolder, args.sebFiles, args.buildingSebFiles, args.natureSebFiles, args.wallSebFiles),
      args.optByStem,
      args.imageCache,
      args.atlasByName,
      true,
    );
    const sizeWidth = Math.max(1, chip.sizeWidth || 1);
    const sizeHeight = Math.max(1, chip.sizeHeight || 1);
    const facilityId = chip.relatedDataType === 1 && chip.relatedDataId >= 0 ? chip.relatedDataId : null;
    const classificationLabel = classifySpecialMapChipCategory(chip.category);
    const spriteSource = selection.sourceFilename
      ? `${selection.assetFolder}/${selection.sourceFilename} (${selection.method})`
      : `${selection.assetFolder}/missing (${selection.method})`;

    records.push({
      mapChipId: chip.id,
      cellX: cell.x,
      cellY: cell.y,
      type: chip.type,
      category: chip.category,
      img: chip.img,
      seb: chip.seb,
      frame: chip.frame,
      layer: chip.layer,
      rotation: chip.rotation,
      sizeWidth,
      sizeHeight,
      unitWidth: chip.unitWidth,
      unitHeight: chip.unitHeight,
      flag: chip.flag,
      relatedDataType: chip.relatedDataType,
      relatedDataId: chip.relatedDataId,
      facilityId,
      classificationLabel,
      rectX: cell.x,
      rectY: cell.y,
      rectWidth: sizeWidth,
      rectHeight: sizeHeight,
      rectSource: "mapchip-size-fallback",
      spriteSource,
      placementSource: "map-cell-f2-mapchip",
      evidenceReason: "Map cell f2 resolves to MapChip.id; footprint from MapChip.sizeWidth/sizeHeight; facility link via relatedDataType/relatedDataId",
      sourceSelection: selection,
    });
  }

  return records;
}

function buildOldVsCleanRenderComparisonRows(records: CleanSourceMapChipRenderRecord[]): OldVsCleanRenderComparisonRow[] {
  return records.map((record) => {
    const staticFacilityOverlay = record.facilityId != null
      ? ONE_PIECE_FACILITY_OVERLAYS.find((entry) => entry.id === record.facilityId)
      : null;
    const isPortFamily = PORT_RELATED_CHIP_IDS.has(record.mapChipId) || (record.facilityId != null && PORT_RELATED_FACILITY_IDS.has(record.facilityId));
    const oldRendererPath = staticFacilityOverlay
      ? "one-piece-facility-static-overlay + native-data-v2 duplicate"
      : isPortFamily
        ? "port assembly + manual sandbox/native proof overlays"
        : "mapchip-f2 main command path";
    const oldPositionSource = staticFacilityOverlay
      ? "ONE_PIECE_FACILITY_OVERLAYS static cellX/cellY"
      : isPortFamily
        ? "PORT_ASSEMBLIES base + manual debug offsets"
        : "map cell f2";
    const oldSpriteSource = staticFacilityOverlay
      ? "building/img.inf static id"
      : isPortFamily
        ? "mixed static assets + mapchip selection"
        : "mapchip img/seb/frame";
    const oldFootprintSource = staticFacilityOverlay
      ? "hardcoded 2x2 overlay"
      : isPortFamily
        ? "composite 4x4/manual"
        : "mapchip size fields";
    let visiblePositionChanged: "YES" | "NO" | "UNKNOWN" = "NO";
    if (staticFacilityOverlay) {
      const oldCellX = staticFacilityOverlay.cellX - 2;
      const oldCellY = staticFacilityOverlay.cellY - 2;
      visiblePositionChanged = oldCellX === record.cellX && oldCellY === record.cellY ? "NO" : "YES";
    } else if (isPortFamily) {
      visiblePositionChanged = "UNKNOWN";
    }

    return {
      mapChipId: record.mapChipId,
      cleanLabel: `${record.cellX},${record.cellY} ${record.classificationLabel}`,
      oldRendererPath,
      oldPositionSource,
      cleanPositionSource: record.placementSource,
      oldSpriteSource,
      cleanSpriteSource: record.spriteSource,
      oldFootprintSource,
      cleanFootprintSource: record.rectSource,
      oldUsedStaticCoordinates: staticFacilityOverlay != null || isPortFamily,
      cleanUsedMapChipData: true,
      cleanUsedMapChipRect: record.rectSource === "mapchiprect",
      visiblePositionChanged,
    };
  });
}

function drawCleanSourceMapChipRecords(
  context: CanvasRenderingContext2D,
  records: CleanSourceMapChipRenderRecord[],
  imageCache: Map<string, LoadedImageAsset>,
  camera: CameraState,
  viewportWidth: number,
  viewportHeight: number,
  showLabels: boolean,
) {
  const zoom = camera.zoom;
  for (const record of records) {
    const iso = worldToIso(record.cellX, record.cellY, zoom, camera.offsetX, camera.offsetY);
    const halfW = (TILE_WIDTH * zoom) / 2;
    const halfH = (TILE_HEIGHT * zoom) / 2;

    context.strokeStyle = "rgba(45, 212, 191, 0.95)";
    context.lineWidth = 1;
    drawIsoDiamond(context, iso.x, iso.y, halfW, halfH);

    context.strokeStyle = "rgba(16, 185, 129, 0.65)";
    for (let dx = 0; dx < record.rectWidth; dx += 1) {
      for (let dy = 0; dy < record.rectHeight; dy += 1) {
        const cornerIso = worldToIso(record.rectX + dx, record.rectY + dy, zoom, camera.offsetX, camera.offsetY);
        drawIsoDiamond(context, cornerIso.x, cornerIso.y, halfW, halfH);
      }
    }

    const selection = record.sourceSelection;
    const asset = selection.sourceFilename ? imageCache.get(buildAssetCacheKey(selection.assetFolder, selection.sourceFilename)) : null;
    if (asset?.image) {
      const image = asset.image;
      const sourceRectX = Math.max(0, selection.srcX);
      const sourceRectY = Math.max(0, selection.srcY);
      const sourceRectW = Math.min(selection.srcW, image.width - sourceRectX);
      const sourceRectH = Math.min(selection.srcH, image.height - sourceRectY);

      if (sourceRectW > 0 && sourceRectH > 0) {
        const placement = computeTexturePlacement({
          renderMode: "mapchip-f2",
          terrainAlignmentMode: "sprite-native",
          command: {
            cellX: record.cellX,
            cellY: record.cellY,
            rawMapValue: record.mapChipId,
            sourceField: "f2",
            f1TerrainTypeValue: null,
            f1TerrainTypeName: null,
            selectedTerrainVisualFamily: null,
            selectedBaseReason: "clean-source-lab",
            layer: record.layer,
            mapChipId: record.mapChipId,
            mapChipName: record.classificationLabel,
            mapChipCategory: record.category,
            mapChipImgId: record.img,
            mapChipSebId: record.seb,
            mapChipFrame: record.frame,
            resolvedImgFilename: selection.sourceFilename,
            imageCacheKeyUsed: selection.sourceFilename,
            imageObjectInstanceId: asset.instanceId,
            imageObjectResolvedSrc: asset.resolvedSrc,
            imageObjectRequestPath: asset.requestPath,
            loadedPngPath: asset.requestPath,
            drawGroup: classifyDrawGroup(record.classificationLabel, selection.sourceFilename),
            terrainName: null,
            footprintWidth: record.sizeWidth,
            footprintHeight: record.sizeHeight,
            selection,
          },
          isoX: iso.x,
          isoY: iso.y,
          zoom,
          clippedSrcW: sourceRectW,
          clippedSrcH: sourceRectH,
        });

        if (
          placement.drawX <= viewportWidth &&
          placement.drawY <= viewportHeight &&
          placement.drawX + placement.drawW >= 0 &&
          placement.drawY + placement.drawH >= 0
        ) {
          const previousAlpha = context.globalAlpha;
          context.globalAlpha = 0.55;
          context.drawImage(
            image,
            sourceRectX,
            sourceRectY,
            sourceRectW,
            sourceRectH,
            placement.drawX,
            placement.drawY,
            placement.drawW,
            placement.drawH,
          );
          context.globalAlpha = previousAlpha;

          context.strokeStyle = "rgba(20, 184, 166, 0.95)";
          context.strokeRect(placement.drawX + 0.5, placement.drawY + 0.5, placement.drawW - 1, placement.drawH - 1);

          context.fillStyle = "rgba(250, 204, 21, 0.98)";
          context.beginPath();
          context.arc(Math.round(iso.x), Math.round(iso.y), Math.max(1.5, 2.25 * zoom), 0, Math.PI * 2);
          context.fill();
        }
      }
    }

    if (showLabels) {
      context.fillStyle = "rgba(236, 253, 245, 0.98)";
      context.font = "10px monospace";
      const label = `chip=${record.mapChipId} c=${record.category} rdt=${record.relatedDataType}/${record.relatedDataId} i/s/f/l=${record.img}/${record.seb}/${record.frame}/${record.layer} rect=${record.rectSource}`;
      context.fillText(label, Math.round(iso.x + 6), Math.round(iso.y - 8));
    }
  }
}

async function buildEngineRenderPipeline(f1RowSelectionMode: F1RowSelectionMode): Promise<RenderPipeline> {
  const [
    mapBinary,
    mapChipText,
    terrainText,
    imgInfText,
    sebInfText,
    buildingImgInfText,
    buildingSebInfText,
    natureImgInfText,
    natureSebInfText,
    wallImgInfText,
    wallSebInfText,
    atlasText,
  ] = await Promise.all([
    fetchArrayBuffer(MAP_PATH),
    fetchText(MAP_CHIP_PATH),
    fetchText(TERRAIN_PATH),
    fetchText(CHIP_IMG_INF_PATH),
    fetchText(CHIP_SEB_INF_PATH),
    fetchText(BUILDING_IMG_INF_PATH),
    fetchText(BUILDING_SEB_INF_PATH),
    fetchText(NATURE_IMG_INF_PATH),
    fetchText(NATURE_SEB_INF_PATH),
    fetchText(WALL_IMG_INF_PATH),
    fetchText(WALL_SEB_INF_PATH),
    fetchText(IMAGE_ATLAS_PATH),
  ]);

  const parsedMap = parseMapBinarySectionA(mapBinary);
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
  const buildingSebById = parseInfTable(buildingSebInfText);
  const natureImageById = parseInfTable(natureImgInfText);
  const natureSebById = parseInfTable(natureSebInfText);
  const wallImageById = parseInfTable(wallImgInfText);
  const wallSebById = parseInfTable(wallSebInfText);
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
  const neededBuildingFilenames = new Set<string>();
  const neededBuildingSebFiles = new Set<string>();
  const neededNatureFilenames = new Set<string>();
  const neededNatureSebFiles = new Set<string>();
  const neededWallFilenames = new Set<string>();
  const neededWallSebFiles = new Set<string>();

  for (const f2Value of usedF2Values) {
    const mapChipRow = mapChipById.get(f2Value);
    if (mapChipRow) {
      const mapChipFolder = getMapChipAssetFolder(mapChipRow);
      const mapChipFilename = getImageTableForFolder(mapChipFolder, imageById, buildingImageById, natureImageById, wallImageById).get(mapChipRow.img);
      if (mapChipFilename) {
        addFilenameToFolderSet(mapChipFolder, mapChipFilename, neededFilenames, neededBuildingFilenames, neededNatureFilenames, neededWallFilenames);
      }
      const mapChipSebName = getSebTableForFolder(mapChipFolder, sebById, buildingSebById, natureSebById, wallSebById).get(mapChipRow.seb);
      if (mapChipSebName) {
        addFilenameToFolderSet(mapChipFolder, mapChipSebName, neededSebFiles, neededBuildingSebFiles, neededNatureSebFiles, neededWallSebFiles);
      }
    }

    const terrainRow = terrainById.get(f2Value);
    if (terrainRow) {
      const terrainFolder = getAssetFolderFromRes(terrainRow.res);
      const terrainFilename = getImageTableForFolder(terrainFolder, imageById, buildingImageById, natureImageById, wallImageById).get(terrainRow.img);
      if (terrainFilename) {
        addFilenameToFolderSet(terrainFolder, terrainFilename, neededFilenames, neededBuildingFilenames, neededNatureFilenames, neededWallFilenames);
      }
      const terrainSebName = getSebTableForFolder(terrainFolder, sebById, buildingSebById, natureSebById, wallSebById).get(terrainRow.seb);
      if (terrainSebName) {
        addFilenameToFolderSet(terrainFolder, terrainSebName, neededSebFiles, neededBuildingSebFiles, neededNatureSebFiles, neededWallSebFiles);
      }
    }
  }

  for (const f1Value of usedF1Values) {
    const terrainRowsForType = terrainByType.get(f1Value) ?? [];
    for (const terrainRow of terrainRowsForType) {
      const terrainFolder = getAssetFolderFromRes(terrainRow.res);
      const terrainFilename = getImageTableForFolder(terrainFolder, imageById, buildingImageById, natureImageById, wallImageById).get(terrainRow.img);
      if (terrainFilename) {
        addFilenameToFolderSet(terrainFolder, terrainFilename, neededFilenames, neededBuildingFilenames, neededNatureFilenames, neededWallFilenames);
      }
      const terrainSebName = getSebTableForFolder(terrainFolder, sebById, buildingSebById, natureSebById, wallSebById).get(terrainRow.seb);
      if (terrainSebName) {
        addFilenameToFolderSet(terrainFolder, terrainSebName, neededSebFiles, neededBuildingSebFiles, neededNatureSebFiles, neededWallSebFiles);
      }
    }
  }

  for (const piece of PORT_GATE_PIECES) {
    const portChip = mapChipById.get(piece.chipId);
    if (!portChip) {
      continue;
    }

    const portChipFolder = getMapChipAssetFolder(portChip);
    const portChipFilename = getImageTableForFolder(portChipFolder, imageById, buildingImageById, natureImageById, wallImageById).get(portChip.img);
    if (portChipFilename) {
      addFilenameToFolderSet(portChipFolder, portChipFilename, neededFilenames, neededBuildingFilenames, neededNatureFilenames, neededWallFilenames);
    }

    const portChipSebName = getSebTableForFolder(portChipFolder, sebById, buildingSebById, natureSebById, wallSebById).get(portChip.seb);
    if (portChipSebName) {
      addFilenameToFolderSet(portChipFolder, portChipSebName, neededSebFiles, neededBuildingSebFiles, neededNatureSebFiles, neededWallSebFiles);
    }
  }

  for (const proofChipId of [PLACEPORT_FIXED_CHIP_ID, PLACEPORT_B44_1_CHIP_ID, ...PLACEPORT_GROUNDDATA_CHIP_IDS]) {
    const proofChip = mapChipById.get(proofChipId);
    if (!proofChip) {
      continue;
    }
    const proofFolder = getMapChipAssetFolder(proofChip);
    const proofFilename = getImageTableForFolder(proofFolder, imageById, buildingImageById, natureImageById, wallImageById).get(proofChip.img);
    if (proofFilename) {
      addFilenameToFolderSet(proofFolder, proofFilename, neededFilenames, neededBuildingFilenames, neededNatureFilenames, neededWallFilenames);
    }
    const proofSebName = getSebTableForFolder(proofFolder, sebById, buildingSebById, natureSebById, wallSebById).get(proofChip.seb);
    if (proofSebName) {
      addFilenameToFolderSet(proofFolder, proofSebName, neededSebFiles, neededBuildingSebFiles, neededNatureSebFiles, neededWallSebFiles);
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

  const buildingSebFiles = new Map<string, SebFile>();
  await Promise.all(
    [...neededBuildingSebFiles].map(async (name) => {
      const path = resolveAssetUrl(`world-assets/building/${name}`);
      const maybe = await fetchArrayBufferOptional(path);
      if (!maybe) {
        return;
      }
      buildingSebFiles.set(name, parseSeb(maybe));
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

  const wallSebFiles = new Map<string, SebFile>();
  await Promise.all(
    [...neededWallSebFiles].map(async (name) => {
      const path = resolveAssetUrl(`world-assets/wall/${name}`);
      const maybe = await fetchArrayBufferOptional(path);
      if (!maybe) {
        return;
      }
      wallSebFiles.set(name, parseSeb(maybe));
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

  for (const seb of buildingSebFiles.values()) {
    for (const block of seb.blocks) {
      for (const record of block.records) {
        const sourceFilename = buildingImageById.get(record.sourceId);
        if (sourceFilename) {
          neededBuildingFilenames.add(sourceFilename);
        }
      }
    }
  }

  for (const seb of natureSebFiles.values()) {
    for (const block of seb.blocks) {
      for (const record of block.records) {
        const sourceFilename = natureImageById.get(record.sourceId);
        if (sourceFilename) {
          neededNatureFilenames.add(sourceFilename);
        }
      }
    }
  }

  for (const seb of wallSebFiles.values()) {
    for (const block of seb.blocks) {
      for (const record of block.records) {
        const sourceFilename = wallImageById.get(record.sourceId);
        if (sourceFilename) {
          neededWallFilenames.add(sourceFilename);
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
      imageCache.set(buildAssetCacheKey("chip", filename), {
        key: buildAssetCacheKey("chip", filename),
        requestPath,
        resolvedSrc: image.currentSrc || image.src || requestPath,
        instanceId: nextImageInstanceId,
        image,
      });
      nextImageInstanceId += 1;
    }),
  );
  await Promise.all(
    [...neededBuildingFilenames].map(async (filename) => {
      const requestPath = resolveAssetUrl(`world-assets/building/${filename}`);
      const image = await loadImage(requestPath);
      imageCache.set(buildAssetCacheKey("building", filename), {
        key: buildAssetCacheKey("building", filename),
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
      imageCache.set(buildAssetCacheKey("nature", filename), {
        key: buildAssetCacheKey("nature", filename),
        requestPath,
        resolvedSrc: image.currentSrc || image.src || requestPath,
        instanceId: nextImageInstanceId,
        image,
      });
      nextImageInstanceId += 1;
    }),
  );
  await Promise.all(
    [...neededWallFilenames].map(async (filename) => {
      const requestPath = resolveAssetUrl(`world-assets/wall/${filename}`);
      const image = await loadImage(requestPath);
      imageCache.set(buildAssetCacheKey("wall", filename), {
        key: buildAssetCacheKey("wall", filename),
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
  const rawPortCellCount = parsedMap.cells.reduce((count, cell) => count + (PORT_RELATED_CHIP_IDS.has(cell.fields.f2) ? 1 : 0), 0);
  let skippedOrMissingPortCommands = 0;
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
      const folder = getAssetFolderFromRes(row.res);
      const filename = getImageTableForFolder(folder, imageById, buildingImageById, natureImageById, wallImageById).get(row.img) ?? "";
      return !isPlayerMadeSurface(row.name, filename);
    });
    const baseRows = typeRows.filter((row) => row.category === 0);
    const selectedByMode = pickTerrainRowForCell(
      baseRows,
      terrainFamily,
      imageById,
      buildingImageById,
      natureImageById,
      wallImageById,
      cell.x,
      cell.y,
      cell.fields,
      f1RowSelectionMode,
    );
    const selectedTerrainRow = selectedByMode.row;
    const selectedBaseReason = selectedByMode.reason;
    const selectedNatureRows = selectOneNatureRowForTile(typeRows, cell.x, cell.y, cell.fields, natureImageById);
    const fallbackFamilyFilename = findFallbackBaseFilename(
      baseRows,
      imageById,
      buildingImageById,
      natureImageById,
      wallImageById,
      terrainFamily,
    );
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
      const selectedTerrainFolder = getAssetFolderFromRes(selectedTerrainRow.res);
      selection = resolveSpriteSelection(
        selectedTerrainFolder,
        selectedTerrainRow,
        getImageTableForFolder(selectedTerrainFolder, imageById, buildingImageById, natureImageById, wallImageById),
        getSebTableForFolder(selectedTerrainFolder, sebById, buildingSebById, natureSebById, wallSebById),
        getSebFilesForFolder(selectedTerrainFolder, sebFiles, buildingSebFiles, natureSebFiles, wallSebFiles),
        selectedTerrainFolder === "nature" ? natureOptByStem : optByStem,
        imageCache,
        atlasByName,
        false,
        false,
      );
      resolvedImgFilename = getImageTableForFolder(selectedTerrainFolder, imageById, buildingImageById, natureImageById, wallImageById).get(selectedTerrainRow.img) ?? "";
      mapChipImgId = selectedTerrainRow.img;
      mapChipSebId = selectedTerrainRow.seb;
      mapChipFrame = selectedTerrainRow.frame;
      mapChipId = selectedTerrainRow.id;
      mapChipName = selectedTerrainRow.name;
      mapChipCategory = selectedTerrainRow.category;
    }

    if ((!selection || selection.method === "missing-image" || selection.method === "placeholder-skip") && fallbackFamilyFilename) {
      const fallbackImage = imageCache.get(buildAssetCacheKey("chip", fallbackFamilyFilename))?.image;
      if (fallbackImage) {
        selection = {
          assetFolder: "chip",
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

    const imageAsset = selection.sourceFilename ? imageCache.get(buildAssetCacheKey(selection.assetFolder, selection.sourceFilename)) : null;
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
      loadedPngPath: imageAsset?.requestPath ?? (selection.sourceFilename ? resolveAssetUrl(`world-assets/${selection.assetFolder}/${selection.sourceFilename}`) : ""),
      drawGroup: "base-terrain",
      terrainName: terrainTypeName,
      footprintWidth: 1,
      footprintHeight: 1,
      selection,
    };

    cellCommands.push(command);

    if (ENABLE_MAP_NATURE_OVERLAYS && !waterTerrain) {
      for (const selectedNature of selectedNatureRows) {
        const natureSelection = resolveSpriteSelection(
          "nature",
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

        const natureImageAsset = natureSelection.sourceFilename ? imageCache.get(buildAssetCacheKey(natureSelection.assetFolder, natureSelection.sourceFilename)) : null;
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
          loadedPngPath: natureImageAsset?.requestPath ?? (natureSelection.sourceFilename ? resolveAssetUrl(`world-assets/${natureSelection.assetFolder}/${natureSelection.sourceFilename}`) : ""),
          drawGroup: "nature-object",
          natureCategory: selectedNature.category,
          terrainName: natureTerrainName,
          footprintWidth: 1,
          footprintHeight: 1,
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
      if (PORT_RELATED_CHIP_IDS.has(cell.fields.f2)) {
        skippedOrMissingPortCommands += 1;
      }
      continue;
    }

    if (CONTROL_ROW_EXCLUDES.has(chip.id) || chip.res === 4) {
      excludedControlRows += 1;
      continue;
    }

    if ((chip.id === 58 || chip.id === 59 || chip.id === 60 || chip.id === 61 || /town hall/i.test(chip.name)) && !isPortFamilyChip(chip)) {
      excludedStructures += 1;
      continue;
    }

    const chipFolder = getMapChipAssetFolder(chip);
    const selection = resolveSpriteSelection(
      chipFolder,
      chip,
      getImageTableForFolder(chipFolder, imageById, buildingImageById, natureImageById, wallImageById),
      getSebTableForFolder(chipFolder, sebById, buildingSebById, natureSebById, wallSebById),
      getSebFilesForFolder(chipFolder, sebFiles, buildingSebFiles, natureSebFiles, wallSebFiles),
      optByStem,
      imageCache,
      atlasByName,
      true,
    );
    if (selection.method === "placeholder-skip") {
      if (isPortFamilyChip(chip)) {
        skippedOrMissingPortCommands += 1;
      }
      skippedPlaceholderCount += 1;
      continue;
    }
    if (selection.method === "missing-image") {
      if (isPortFamilyChip(chip)) {
        skippedOrMissingPortCommands += 1;
      }
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
    const imageAsset = selection.sourceFilename ? imageCache.get(buildAssetCacheKey(selection.assetFolder, selection.sourceFilename)) : null;
    const resolvedImgFilename = getImageTableForFolder(chipFolder, imageById, buildingImageById, natureImageById, wallImageById).get(chip.img) ?? "";
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
      loadedPngPath: imageAsset?.requestPath ?? (selection.sourceFilename ? resolveAssetUrl(`world-assets/${selection.assetFolder}/${selection.sourceFilename}`) : ""),
      drawGroup: classifyDrawGroup(chip.name, resolvedImgFilename),
      terrainName,
      footprintWidth: chip.sizeWidth,
      footprintHeight: chip.sizeHeight,
      selection,
    });
  }

  for (const cell of parsedMap.cells) {
    const chip = mapChipById.get(cell.fields.f2);
    if (!chip) {
      continue;
    }

    const semanticGroup = getF2SemanticGroup(cell.fields.f2);
    const terrainLikeFromF2 = semanticGroup === "terrain/ground/water" || semanticGroup === "road/path/traffic/buildable-related";
    const terrainLikeFromRelation = chip.relatedDataType === 2 && chip.relatedDataId >= 0;
    if (!terrainLikeFromF2 && !terrainLikeFromRelation) {
      continue;
    }

    const terrainFolder = getMapChipAssetFolder(chip);
    const selection = resolveSpriteSelection(
      terrainFolder,
      chip,
      getImageTableForFolder(terrainFolder, imageById, buildingImageById, natureImageById, wallImageById),
      getSebTableForFolder(terrainFolder, sebById, buildingSebById, natureSebById, wallSebById),
      getSebFilesForFolder(terrainFolder, sebFiles, buildingSebFiles, natureSebFiles, wallSebFiles),
      terrainFolder === "nature" ? natureOptByStem : optByStem,
      imageCache,
      atlasByName,
      true,
    );
    if (selection.method === "placeholder-skip" || selection.method === "missing-image") {
      continue;
    }

    const imageAsset = selection.sourceFilename ? imageCache.get(buildAssetCacheKey(selection.assetFolder, selection.sourceFilename)) : null;
    const resolvedImgFilename = getImageTableForFolder(terrainFolder, imageById, buildingImageById, natureImageById, wallImageById).get(chip.img) ?? "";
    const terrainName = terrainById.get(chip.relatedDataId)?.name ?? chip.name;
    terrainCommands.push({
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
      loadedPngPath: imageAsset?.requestPath ?? (selection.sourceFilename ? resolveAssetUrl(`world-assets/${selection.assetFolder}/${selection.sourceFilename}`) : ""),
      drawGroup: classifyDrawGroup(chip.name, resolvedImgFilename),
      terrainName,
      footprintWidth: 1,
      footprintHeight: 1,
      selection,
    });
  }

  const verifiedPortReconstruction = buildVerifiedPortReconstructionCommands({
    parsedMap,
    mapChipById,
    terrainById,
    imageById,
    buildingImageById,
    natureImageById,
    wallImageById,
    sebById,
    buildingSebById,
    natureSebById,
    wallSebById,
    sebFiles,
    buildingSebFiles,
    natureSebFiles,
    wallSebFiles,
    optByStem,
    imageCache,
    atlasByName,
  });
  const nativePlacePortProof = buildPlacePortEvidenceLayout({
    mapChipById,
    terrainById,
    imageById,
    buildingImageById,
    natureImageById,
    wallImageById,
    sebById,
    buildingSebById,
    natureSebById,
    wallSebById,
    sebFiles,
    buildingSebFiles,
    natureSebFiles,
    wallSebFiles,
    optByStem,
    imageCache,
    atlasByName,
  });
  nativePlacePortProof.warnings.forEach((warning) => {
    console.warn("[runtime-world-render-lab][native-placeport-proof]", warning);
  });
  const portOverlayCommands = verifiedPortReconstruction.commands;
  const portInterpretationPreviews = buildPortInterpretationPreviews({
    parsedMap,
    mapChipById,
    terrainById,
    imageById,
    buildingImageById,
    natureImageById,
    wallImageById,
    sebById,
    buildingSebById,
    natureSebById,
    wallSebById,
    sebFiles,
    buildingSebFiles,
    natureSebFiles,
    wallSebFiles,
    optByStem,
    imageCache,
    atlasByName,
  });

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
        facilityBuildingCache.set(facility.id, {
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
        portAssetCache.set(assetKey, {
          key: assetKey,
          requestPath,
          resolvedSrc: image.currentSrc || image.src || requestPath,
          instanceId: nextImageInstanceId,
          image,
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
  const portProofStats: PortProofStats = {
    rawPortCellCount,
    portDrawCommandCount: portOverlayCommands.length,
    resolvedBuildingCount: portOverlayCommands.filter((command) => command.selection.assetFolder === "building").length,
    resolvedChipCount: portOverlayCommands.filter((command) => command.selection.assetFolder === "chip").length,
    resolvedWallCount: portOverlayCommands.filter((command) => command.selection.assetFolder === "wall").length,
    resolvedNatureCount: portOverlayCommands.filter((command) => command.selection.assetFolder === "nature").length,
    skippedOrMissingCount: skippedOrMissingPortCommands + verifiedPortReconstruction.skippedCount,
  };

  const unresolvedBehaviors = [
    "SEB block selection semantics are still unresolved globally; deterministic first pass uses block 0 for seb-backed chips.",
    "When MapChip.frame does not map to a filled OPT slot, renderer falls back to first filled slot in row-major order.",
    "Atlas regions are used as metadata validation; 1x1 atlas placeholders are skipped to avoid drawing invalid assets.",
  ];
  const cleanSourceMapChipRecords = buildCleanSourceMapChipRenderRecords({
    parsedMap,
    mapChipById,
    terrainById,
    imageById,
    buildingImageById,
    natureImageById,
    wallImageById,
    sebById,
    buildingSebById,
    natureSebById,
    wallSebById,
    sebFiles,
    buildingSebFiles,
    natureSebFiles,
    wallSebFiles,
    optByStem,
    imageCache,
    atlasByName,
  });
  const cleanOldVsComparisonRows = buildOldVsCleanRenderComparisonRows(cleanSourceMapChipRecords);

  return {
    parsedMap,
    f1TerrainCommands,
    portOverlayCommands,
    nativePlacePortProofCommands: nativePlacePortProof.commands,
    nativePlacePortProofRecords: nativePlacePortProof.records,
    nativePlacePortProofWarnings: nativePlacePortProof.warnings,
    portInterpretationPreviews,
    cleanSourceMapChipRecords,
    cleanOldVsComparisonRows,
    mapChipCommands,
    terrainCommands,
    imageCache,
    facilityBuildingCache,
    portAssetCache,
    f1TerrainDiagnostics,
    mapChipDiagnostics,
    terrainDiagnostics,
    portProofStats,
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
      buildingImageById,
      buildingSebById,
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
  assetFolder: AssetFolder,
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
      assetFolder,
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
      assetFolder,
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
        if (!imageCache.has(buildAssetCacheKey(assetFolder, sebFilename))) {
          return {
            assetFolder,
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
          assetFolder,
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
        assetFolder,
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

  const fullImage = imageCache.get(buildAssetCacheKey(assetFolder, chipFilename))?.image;
  if (!fullImage) {
    return {
      assetFolder,
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
    assetFolder,
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

function buildAssetCacheKey(folder: AssetFolder, filename: string): string {
  return `${folder}:${filename}`;
}

function getImageTableForFolder(
  folder: AssetFolder,
  chipImageById: Map<number, string>,
  buildingImageById: Map<number, string>,
  natureImageById: Map<number, string>,
  wallImageById: Map<number, string>,
): Map<number, string> {
  if (folder === "building") {
    return buildingImageById;
  }
  if (folder === "nature") {
    return natureImageById;
  }
  if (folder === "wall") {
    return wallImageById;
  }
  return chipImageById;
}

function getSebTableForFolder(
  folder: AssetFolder,
  chipSebById: Map<number, string>,
  buildingSebById: Map<number, string>,
  natureSebById: Map<number, string>,
  wallSebById: Map<number, string>,
): Map<number, string> {
  if (folder === "building") {
    return buildingSebById;
  }
  if (folder === "nature") {
    return natureSebById;
  }
  if (folder === "wall") {
    return wallSebById;
  }
  return chipSebById;
}

function getSebFilesForFolder(
  folder: AssetFolder,
  chipSebFiles: Map<string, SebFile>,
  buildingSebFiles: Map<string, SebFile>,
  natureSebFiles: Map<string, SebFile>,
  wallSebFiles: Map<string, SebFile>,
): Map<string, SebFile> {
  if (folder === "building") {
    return buildingSebFiles;
  }
  if (folder === "nature") {
    return natureSebFiles;
  }
  if (folder === "wall") {
    return wallSebFiles;
  }
  return chipSebFiles;
}

function getMapChipAssetFolder(chip: MapChipRow): AssetFolder {
  return getAssetFolderFromRes(chip.res);
}

function getAssetFolderFromRes(res: number): AssetFolder {
  if (res === 23) {
    return "building";
  }
  if (res === 21) {
    return "wall";
  }
  if (res === 20) {
    return "nature";
  }
  return "chip";
}

function addFilenameToFolderSet(
  folder: AssetFolder,
  filename: string,
  chipSet: Set<string>,
  buildingSet: Set<string>,
  natureSet: Set<string>,
  wallSet: Set<string>,
): void {
  if (folder === "building") {
    buildingSet.add(filename);
    return;
  }
  if (folder === "nature") {
    natureSet.add(filename);
    return;
  }
  if (folder === "wall") {
    wallSet.add(filename);
    return;
  }
  chipSet.add(filename);
}

function isPortFamilyChip(chip: MapChipRow): boolean {
  return chip.category === 48 || PORT_RELATED_FACILITY_IDS.has(chip.relatedDataId) || PORT_RELATED_CHIP_IDS.has(chip.id);
}

function isPortRelatedChipCommand(command: TileDrawCommand): boolean {
  return command.mapChipCategory === 48 || PORT_RELATED_CHIP_IDS.has(command.mapChipId) || PORT_RELATED_FACILITY_IDS.has(command.rawMapValue);
}

function buildParsedMapCellLookup(parsedMap: ParsedMapBinary): Map<string, ParsedMapCell> {
  const lookup = new Map<string, ParsedMapCell>();
  for (const cell of parsedMap.cells) {
    lookup.set(`${cell.x},${cell.y}`, cell);
  }
  return lookup;
}

function getParsedMapCell(cellLookup: Map<string, ParsedMapCell>, x: number, y: number): ParsedMapCell | null {
  return cellLookup.get(`${x},${y}`) ?? null;
}

function isLikelyPortWaterCell(cell: ParsedMapCell | null): boolean {
  if (!cell) {
    return false;
  }
  return cell.fields.f1 === 0 || (cell.fields.f2 >= 31 && cell.fields.f2 <= 34);
}

function countWaterCells(
  cellLookup: Map<string, ParsedMapCell>,
  startX: number,
  startY: number,
  width: number,
  height: number,
): number {
  let count = 0;
  for (let y = startY; y < startY + height; y += 1) {
    for (let x = startX; x < startX + width; x += 1) {
      if (isLikelyPortWaterCell(getParsedMapCell(cellLookup, x, y))) {
        count += 1;
      }
    }
  }
  return count;
}

function permutePortSlots(slots: readonly { dx: number; dy: number }[]): Array<Array<{ dx: number; dy: number }>> {
  if (slots.length <= 1) {
    return [slots.slice() as Array<{ dx: number; dy: number }>];
  }

  const result: Array<Array<{ dx: number; dy: number }>> = [];
  for (let index = 0; index < slots.length; index += 1) {
    const head = slots[index];
    const tail = slots.slice(0, index).concat(slots.slice(index + 1));
    for (const permutation of permutePortSlots(tail)) {
      result.push([head, ...permutation]);
    }
  }
  return result;
}

function scorePortPiecePlacement(
  assetKey: PortGatePiece["assetKey"],
  slot: { dx: number; dy: number },
  baseX: number,
  baseY: number,
  cellLookup: Map<string, ParsedMapCell>,
): number {
  const cellX = baseX + slot.dx;
  const cellY = baseY + slot.dy;
  const occupiedWater = countWaterCells(cellLookup, cellX, cellY, 2, 2);
  const occupiedLand = 4 - occupiedWater;
  const eastWater = countWaterCells(cellLookup, cellX + 2, cellY, 1, 2);
  const westWater = countWaterCells(cellLookup, cellX - 1, cellY, 1, 2);
  const northWater = countWaterCells(cellLookup, cellX, cellY - 1, 2, 1);
  const southWater = countWaterCells(cellLookup, cellX, cellY + 2, 2, 1);
  const northBias = slot.dy === 0 ? 1 : 0;
  const southBias = slot.dy === 2 ? 1 : 0;
  const westBias = slot.dx === 0 ? 1 : 0;
  const eastBias = slot.dx === 2 ? 1 : 0;
  const isTallPiece = assetKey === "gate_00" || assetKey === "gate_02";
  const heightBandScore = isTallPiece
    ? northBias * 10 - southBias * 10
    : southBias * 10 - northBias * 10;
  const shoreWaterPenalty = occupiedWater * 8;

  switch (assetKey) {
    case "gate_02":
      return eastWater * 14 + occupiedWater * 6 + northBias * 5 + eastBias * 8 + heightBandScore - westWater * 6 - southBias * 2;
    case "gate_03":
      return occupiedLand * 9 + westBias * 8 + southBias * 5 - shoreWaterPenalty - eastWater * 5 + heightBandScore;
    case "gate_01":
      return occupiedLand * 9 + southBias * 6 + eastBias * 4 - shoreWaterPenalty - westWater * 2 + heightBandScore;
    case "gate_00":
    default:
      return occupiedLand * 10 + westBias * 9 + northBias * 7 - shoreWaterPenalty - eastWater * 6 + northWater + heightBandScore;
  }
}

function scorePortCompositePlacement(
  baseX: number,
  baseY: number,
  cellLookup: Map<string, ParsedMapCell>,
): number {
  const westLand = 4 - countWaterCells(cellLookup, baseX - 1, baseY, 1, 4);
  const eastWater = countWaterCells(cellLookup, baseX + 4, baseY, 1, 4);
  const interiorWaterWest = countWaterCells(cellLookup, baseX, baseY, 2, 4);
  const interiorWaterEast = countWaterCells(cellLookup, baseX + 2, baseY, 2, 4);
  const occupiedWater = countWaterCells(cellLookup, baseX, baseY, 4, 4);
  const northwestWater = countWaterCells(cellLookup, baseX, baseY, 2, 2);
  const southeastWater = countWaterCells(cellLookup, baseX + 2, baseY + 2, 2, 2);
  const occupiedLand = 16 - occupiedWater;
  const shorelineBalancePenalty = Math.abs(interiorWaterEast - 4) * 6 + Math.abs(interiorWaterWest - 1) * 8;
  return westLand * 9 + eastWater * 8 + interiorWaterEast * 4 + occupiedLand * 2 + southeastWater - shorelineBalancePenalty - interiorWaterWest * 6 - northwestWater * 5;
}

function formatPortSlot(slot: { dx: number; dy: number }): string {
  if (slot.dx === 0 && slot.dy === 0) {
    return "NW";
  }
  if (slot.dx === 2 && slot.dy === 0) {
    return "NE";
  }
  if (slot.dx === 0 && slot.dy === 2) {
    return "SW";
  }
  if (slot.dx === 2 && slot.dy === 2) {
    return "SE";
  }
  return `${slot.dx},${slot.dy}`;
}

function scorePortInterpretationForPort(
  port: PortAssembly,
  pieceSlots: PortPieceSlot[],
  cellLookup: Map<string, ParsedMapCell>,
  anchorOffsetX: number,
  anchorOffsetY: number,
): number {
  const rootPlacement = pieceSlots.find((entry) => entry.piece.assetKey === "gate_00");
  if (!rootPlacement) {
    return Number.NEGATIVE_INFINITY;
  }

  const baseX = port.cellX - 2 - rootPlacement.slot.dx + anchorOffsetX;
  const baseY = port.cellY - 2 - rootPlacement.slot.dy + anchorOffsetY;
  let score = scorePortCompositePlacement(baseX, baseY, cellLookup);

  for (const entry of pieceSlots) {
    score += scorePortPiecePlacement(entry.piece.assetKey, entry.slot, baseX, baseY, cellLookup);
  }

  return score;
}

function summarizePortPieceSlots(pieceSlots: PortPieceSlot[]): string {
  const slotByPiece = new Map(pieceSlots.map((entry) => [entry.piece.assetKey, entry.slot]));
  return [
    `tower ${formatPortSlot(slotByPiece.get("gate_00") ?? { dx: -1, dy: -1 })}`,
    `gatehouse ${formatPortSlot(slotByPiece.get("gate_01") ?? { dx: -1, dy: -1 })}`,
    `dock ${formatPortSlot(slotByPiece.get("gate_02") ?? { dx: -1, dy: -1 })}`,
    `cargo ${formatPortSlot(slotByPiece.get("gate_03") ?? { dx: -1, dy: -1 })}`,
  ].join(" | ");
}

function buildOrderedPortSlotFamilies(): PortPieceSlot[][] {
  const families = permutePortSlots(PORT_COMPOSITE_SLOTS).map((slotPermutation) =>
    PORT_GATE_PIECES.map((piece, index) => ({ piece, slot: slotPermutation[index] })),
  );

  return families.filter((family) => {
    const slotByChipId = new Map(family.map((entry) => [entry.piece.chipId, entry.slot]));
    const tower = slotByChipId.get(67);
    const cargo = slotByChipId.get(70);
    const dock = slotByChipId.get(69);
    const gatehouse = slotByChipId.get(68);
    if (!tower || !cargo || !dock || !gatehouse) {
      return false;
    }

    const allUnique = new Set(family.map((entry) => `${entry.slot.dx},${entry.slot.dy}`)).size === 4;
    if (!allUnique) {
      return false;
    }

    return tower.dx === 0 && cargo.dx === 0 && dock.dx === 2 && gatehouse.dx === 2;
  });
}

function buildPortInterpretationCandidates(
  cellLookup: Map<string, ParsedMapCell>,
  limit = 12,
): PortInterpretationCandidate[] {
  const candidates: PortInterpretationCandidate[] = [];

  for (const pieceSlots of buildOrderedPortSlotFamilies()) {
    for (const anchorOffset of PORT_GLOBAL_ANCHOR_OFFSETS) {
      const portScores = PORT_ASSEMBLIES.map((port) => ({
        portId: port.id,
        score: scorePortInterpretationForPort(port, pieceSlots, cellLookup, anchorOffset.dx, anchorOffset.dy),
      }));
      const totalScore = portScores.reduce((sum, entry) => sum + entry.score, 0);
      const slotSummary = summarizePortPieceSlots(pieceSlots);
      candidates.push({
        key: `${anchorOffset.dx},${anchorOffset.dy}|${pieceSlots.map((entry) => `${entry.piece.assetKey}:${entry.slot.dx},${entry.slot.dy}`).join("|")}`,
        totalScore,
        slotSummary,
        anchorOffsetX: anchorOffset.dx,
        anchorOffsetY: anchorOffset.dy,
        portScores,
        pieceSlots,
      });
    }
  }

  return candidates
    .sort((left, right) => {
      const leftWorst = Math.min(...left.portScores.map((entry) => entry.score));
      const rightWorst = Math.min(...right.portScores.map((entry) => entry.score));
      if (rightWorst !== leftWorst) {
        return rightWorst - leftWorst;
      }
      if (right.totalScore !== left.totalScore) {
        return right.totalScore - left.totalScore;
      }
      return left.slotSummary.localeCompare(right.slotSummary);
    })
    .slice(0, limit);
}

function resolveSharedPortPieceSlots(
  cellLookup: Map<string, ParsedMapCell>,
): PortPieceSlot[] {
  return buildPortInterpretationCandidates(cellLookup, 1)[0]?.pieceSlots ?? [];
}

function resolvePortPiecePlacements(
  port: PortAssembly,
  pieceSlots: PortPieceSlot[],
  anchorOffsetX = 0,
  anchorOffsetY = 0,
): Array<{ piece: PortGatePiece; cellX: number; cellY: number }> {
  const rootPlacement = pieceSlots.find((entry) => entry.piece.assetKey === "gate_00");
  if (!rootPlacement) {
    return [];
  }

  const baseX = port.cellX - 2 - rootPlacement.slot.dx + anchorOffsetX;
  const baseY = port.cellY - 2 - rootPlacement.slot.dy + anchorOffsetY;
  return pieceSlots.map((entry) => ({
    piece: entry.piece,
    cellX: baseX + entry.slot.dx,
    cellY: baseY + entry.slot.dy,
  }));
}

function buildPortCommandsForCandidate(args: {
  parsedMap: ParsedMapBinary;
  mapChipById: Map<number, MapChipRow>;
  terrainById: Map<number, TerrainRow>;
  imageById: Map<number, string>;
  buildingImageById: Map<number, string>;
  natureImageById: Map<number, string>;
  wallImageById: Map<number, string>;
  sebById: Map<number, string>;
  buildingSebById: Map<number, string>;
  natureSebById: Map<number, string>;
  wallSebById: Map<number, string>;
  sebFiles: Map<string, SebFile>;
  buildingSebFiles: Map<string, SebFile>;
  natureSebFiles: Map<string, SebFile>;
  wallSebFiles: Map<string, SebFile>;
  optByStem: Map<string, OptMetadata>;
  imageCache: Map<string, LoadedImageAsset>;
  atlasByName: Map<string, AtlasRegion>;
}, candidate: Pick<PortInterpretationCandidate, "pieceSlots" | "anchorOffsetX" | "anchorOffsetY">): { commands: TileDrawCommand[]; commandsByPortId: Map<PortAssembly["id"], TileDrawCommand[]>; skippedCount: number } {
  const commands: TileDrawCommand[] = [];
  const commandsByPortId = new Map<PortAssembly["id"], TileDrawCommand[]>();
  let skippedCount = 0;

  for (const port of PORT_ASSEMBLIES) {
    const resolvedPlacements = resolvePortPiecePlacements(port, candidate.pieceSlots, candidate.anchorOffsetX, candidate.anchorOffsetY);
    const portCommands: TileDrawCommand[] = [];

    for (const placement of resolvedPlacements) {
      const piece = placement.piece;
      const chip = args.mapChipById.get(piece.chipId);
      if (!chip) {
        skippedCount += 1;
        continue;
      }

      const chipFolder = getMapChipAssetFolder(chip);
      const selection = resolveSpriteSelection(
        chipFolder,
        chip,
        getImageTableForFolder(chipFolder, args.imageById, args.buildingImageById, args.natureImageById, args.wallImageById),
        getSebTableForFolder(chipFolder, args.sebById, args.buildingSebById, args.natureSebById, args.wallSebById),
        getSebFilesForFolder(chipFolder, args.sebFiles, args.buildingSebFiles, args.natureSebFiles, args.wallSebFiles),
        args.optByStem,
        args.imageCache,
        args.atlasByName,
        false,
      );

      if (selection.method === "missing-image" || selection.method === "placeholder-skip") {
        skippedCount += 1;
        continue;
      }

      const imageAsset = selection.sourceFilename ? args.imageCache.get(buildAssetCacheKey(selection.assetFolder, selection.sourceFilename)) : null;
      const resolvedImgFilename = getImageTableForFolder(chipFolder, args.imageById, args.buildingImageById, args.natureImageById, args.wallImageById).get(chip.img) ?? "";
      const command: TileDrawCommand = {
        cellX: placement.cellX,
        cellY: placement.cellY,
        rawMapValue: chip.id,
        sourceField: "f2",
        f1TerrainTypeValue: null,
        f1TerrainTypeName: null,
        selectedTerrainVisualFamily: null,
        selectedBaseReason: `verified-port-root ${port.id}`,
        layer: chip.layer + 0.5,
        mapChipId: chip.id,
        mapChipName: `${chip.name} ${port.id}`,
        mapChipCategory: chip.category,
        mapChipImgId: chip.img,
        mapChipSebId: chip.seb,
        mapChipFrame: chip.frame,
        resolvedImgFilename,
        imageCacheKeyUsed: selection.sourceFilename,
        imageObjectInstanceId: imageAsset?.instanceId ?? null,
        imageObjectResolvedSrc: imageAsset?.resolvedSrc ?? "",
        imageObjectRequestPath: imageAsset?.requestPath ?? "",
        loadedPngPath: imageAsset?.requestPath ?? (selection.sourceFilename ? resolveAssetUrl(`world-assets/${selection.assetFolder}/${selection.sourceFilename}`) : ""),
        drawGroup: classifyDrawGroup(chip.name, resolvedImgFilename),
        terrainName: args.terrainById.get(chip.relatedDataId)?.name ?? null,
        footprintWidth: 2,
        footprintHeight: 2,
        selection,
      };
      commands.push(command);
      portCommands.push(command);
    }

    commandsByPortId.set(port.id, portCommands);
  }

  return { commands, commandsByPortId, skippedCount };
}

function computePortCommandRect(
  command: TileDrawCommand,
  imageCache: Map<string, LoadedImageAsset>,
): { assetKey: string; left: number; top: number; right: number; bottom: number; centerX: number; centerY: number } | null {
  const sourceFilename = command.selection.sourceFilename;
  if (!sourceFilename) {
    return null;
  }

  const imageAsset = imageCache.get(buildAssetCacheKey(command.selection.assetFolder, sourceFilename));
  const image = imageAsset?.image;
  if (!image) {
    return null;
  }

  const assetKey = sourceFilename.replace(/\.png$/i, "");
  const optPlacement = Object.prototype.hasOwnProperty.call(PORT_GATE_OPT_PLACEMENT, assetKey)
    ? PORT_GATE_OPT_PLACEMENT[assetKey as PortGatePiece["assetKey"]]
    : null;

  if (!optPlacement) {
    return null;
  }

  const iso = worldToIso(command.cellX, command.cellY, 1, 0, 0);
  const halfTileH = TILE_HEIGHT / 2;
  const diamondCenterY = iso.y + halfTileH;
  const cellDrawX = iso.x - optPlacement.cellW / 2;
  const cellDrawY = diamondCenterY + halfTileH - optPlacement.cellH;
  const left = cellDrawX + optPlacement.destX;
  const top = cellDrawY + optPlacement.destY;
  const right = left + image.width;
  const bottom = top + image.height;
  return {
    assetKey,
    left,
    top,
    right,
    bottom,
    centerX: (left + right) / 2,
    centerY: (top + bottom) / 2,
  };
}

function computeRectOverlapArea(
  left: { left: number; top: number; right: number; bottom: number },
  right: { left: number; top: number; right: number; bottom: number },
): number {
  const overlapW = Math.min(left.right, right.right) - Math.max(left.left, right.left);
  const overlapH = Math.min(left.bottom, right.bottom) - Math.max(left.top, right.top);
  if (overlapW <= 0 || overlapH <= 0) {
    return 0;
  }
  return overlapW * overlapH;
}

function computeAxisOverlap(startA: number, endA: number, startB: number, endB: number): number {
  return Math.max(0, Math.min(endA, endB) - Math.max(startA, startB));
}

function isHorizontalRectTouching(
  first: { left: number; top: number; right: number; bottom: number },
  second: { left: number; top: number; right: number; bottom: number },
): boolean {
  const [leftRect, rightRect] = first.left <= second.left ? [first, second] : [second, first];
  const verticalOverlap = computeAxisOverlap(leftRect.top, leftRect.bottom, rightRect.top, rightRect.bottom);
  const maxGap = 18;
  const maxOverlap = 36;
  const gap = rightRect.left - leftRect.right;
  return verticalOverlap >= 30 && gap <= maxGap && gap >= -maxOverlap;
}

function isVerticalRectTouching(
  first: { left: number; top: number; right: number; bottom: number },
  second: { left: number; top: number; right: number; bottom: number },
): boolean {
  const [topRect, bottomRect] = first.top <= second.top ? [first, second] : [second, first];
  const horizontalOverlap = computeAxisOverlap(topRect.left, topRect.right, bottomRect.left, bottomRect.right);
  const maxGap = 18;
  const maxOverlap = 36;
  const gap = bottomRect.top - topRect.bottom;
  return horizontalOverlap >= 30 && gap <= maxGap && gap >= -maxOverlap;
}

function hasRequiredPortCompositeContacts(
  commands: TileDrawCommand[],
  pieceSlots: PortPieceSlot[],
  imageCache: Map<string, LoadedImageAsset>,
): boolean {
  const rectBySlot = new Map<string, { left: number; top: number; right: number; bottom: number }>();

  for (const command of commands) {
    const pieceSlot = pieceSlots.find((entry) => entry.piece.chipId === command.mapChipId);
    if (!pieceSlot) {
      continue;
    }
    const rect = computePortCommandRect(command, imageCache);
    if (!rect) {
      return false;
    }
    rectBySlot.set(`${pieceSlot.slot.dx},${pieceSlot.slot.dy}`, rect);
  }

  const northwest = rectBySlot.get("0,0");
  const northeast = rectBySlot.get("2,0");
  const southwest = rectBySlot.get("0,2");
  const southeast = rectBySlot.get("2,2");
  if (!northwest || !northeast || !southwest || !southeast) {
    return false;
  }

  return isHorizontalRectTouching(northwest, northeast)
    && isHorizontalRectTouching(southwest, southeast)
    && isVerticalRectTouching(northwest, southwest)
    && isVerticalRectTouching(northeast, southeast);
}

function scorePortCommandSilhouette(
  commands: TileDrawCommand[],
  imageCache: Map<string, LoadedImageAsset>,
): number {
  const rects = commands.map((command) => computePortCommandRect(command, imageCache)).filter(Boolean) as Array<{
    assetKey: string;
    left: number;
    top: number;
    right: number;
    bottom: number;
    centerX: number;
    centerY: number;
  }>;

  if (rects.length < 4) {
    return Number.NEGATIVE_INFINITY;
  }

  const bounds = rects.reduce(
    (accumulator, rect) => ({
      left: Math.min(accumulator.left, rect.left),
      top: Math.min(accumulator.top, rect.top),
      right: Math.max(accumulator.right, rect.right),
      bottom: Math.max(accumulator.bottom, rect.bottom),
    }),
    { left: Number.POSITIVE_INFINITY, top: Number.POSITIVE_INFINITY, right: Number.NEGATIVE_INFINITY, bottom: Number.NEGATIVE_INFINITY },
  );
  const width = bounds.right - bounds.left;
  const height = bounds.bottom - bounds.top;

  let overlapPenalty = 0;
  for (let index = 0; index < rects.length; index += 1) {
    for (let otherIndex = index + 1; otherIndex < rects.length; otherIndex += 1) {
      overlapPenalty += computeRectOverlapArea(rects[index], rects[otherIndex]);
    }
  }

  const byAssetKey = new Map(rects.map((rect) => [rect.assetKey, rect]));
  const tower = byAssetKey.get("gate_00");
  const gatehouse = byAssetKey.get("gate_01");
  const dock = byAssetKey.get("gate_02");
  const cargo = byAssetKey.get("gate_03");

  let roleScore = 0;
  if (tower && dock && cargo && gatehouse) {
    if (tower.centerX < dock.centerX && cargo.centerX < gatehouse.centerX) {
      roleScore += 40;
    }
    if (dock.centerY < gatehouse.centerY) {
      roleScore += 20;
    }
    if (cargo.centerY > tower.centerY) {
      roleScore += 20;
    }
  }

  const compactnessPenalty = Math.abs(width - 170) * 0.6 + Math.abs(height - 170) * 0.4;
  return roleScore - overlapPenalty / 300 - compactnessPenalty;
}

function buildRankedPortInterpretationEntries(args: {
  parsedMap: ParsedMapBinary;
  mapChipById: Map<number, MapChipRow>;
  terrainById: Map<number, TerrainRow>;
  imageById: Map<number, string>;
  buildingImageById: Map<number, string>;
  natureImageById: Map<number, string>;
  wallImageById: Map<number, string>;
  sebById: Map<number, string>;
  buildingSebById: Map<number, string>;
  natureSebById: Map<number, string>;
  wallSebById: Map<number, string>;
  sebFiles: Map<string, SebFile>;
  buildingSebFiles: Map<string, SebFile>;
  natureSebFiles: Map<string, SebFile>;
  wallSebFiles: Map<string, SebFile>;
  optByStem: Map<string, OptMetadata>;
  imageCache: Map<string, LoadedImageAsset>;
  atlasByName: Map<string, AtlasRegion>;
}, limit: number): RankedPortInterpretationEntry[] {
  const cellLookup = buildParsedMapCellLookup(args.parsedMap);
  const baseCandidates = buildPortInterpretationCandidates(cellLookup, 64);
  const rankedEntries = baseCandidates.map((candidate) => {
    const built = buildPortCommandsForCandidate(args, candidate);
    const allPortsTouchAsComposite = PORT_ASSEMBLIES.every((port) =>
      hasRequiredPortCompositeContacts(built.commandsByPortId.get(port.id) ?? [], candidate.pieceSlots, args.imageCache),
    );
    const silhouetteScore = PORT_ASSEMBLIES.reduce((sum, port) => sum + scorePortCommandSilhouette(built.commandsByPortId.get(port.id) ?? [], args.imageCache), 0);
    return {
      candidate,
      built,
      silhouetteScore: allPortsTouchAsComposite ? silhouetteScore : Number.NEGATIVE_INFINITY,
      worstPortScore: Math.min(...candidate.portScores.map((entry) => entry.score)),
    } satisfies RankedPortInterpretationEntry;
  });

  return rankedEntries
    .filter((entry) => Number.isFinite(entry.silhouetteScore))
    .sort((left, right) => {
      if (right.worstPortScore !== left.worstPortScore) {
        return right.worstPortScore - left.worstPortScore;
      }
      if (right.silhouetteScore !== left.silhouetteScore) {
        return right.silhouetteScore - left.silhouetteScore;
      }
      if (right.candidate.totalScore !== left.candidate.totalScore) {
        return right.candidate.totalScore - left.candidate.totalScore;
      }
      return left.candidate.slotSummary.localeCompare(right.candidate.slotSummary);
    })
    .slice(0, limit);
}

function buildPortInterpretationPreviews(args: {
  parsedMap: ParsedMapBinary;
  mapChipById: Map<number, MapChipRow>;
  terrainById: Map<number, TerrainRow>;
  imageById: Map<number, string>;
  buildingImageById: Map<number, string>;
  natureImageById: Map<number, string>;
  wallImageById: Map<number, string>;
  sebById: Map<number, string>;
  buildingSebById: Map<number, string>;
  natureSebById: Map<number, string>;
  wallSebById: Map<number, string>;
  sebFiles: Map<string, SebFile>;
  buildingSebFiles: Map<string, SebFile>;
  natureSebFiles: Map<string, SebFile>;
  wallSebFiles: Map<string, SebFile>;
  optByStem: Map<string, OptMetadata>;
  imageCache: Map<string, LoadedImageAsset>;
  atlasByName: Map<string, AtlasRegion>;
}): PortInterpretationPreview[] {
  const rankedEntries = buildRankedPortInterpretationEntries(args, 12);

  return rankedEntries.map((entry, index) => {
    const candidate = entry.candidate;
    const built = entry.built;
    return {
      key: candidate.key,
      label: `Interpretation ${String(index + 1).padStart(2, "0")}`,
      description: `push (${candidate.anchorOffsetX >= 0 ? "+" : ""}${candidate.anchorOffsetX}, ${candidate.anchorOffsetY >= 0 ? "+" : ""}${candidate.anchorOffsetY}) | ${candidate.slotSummary} | silhouette ${entry.silhouetteScore.toFixed(1)} | ${candidate.portScores.map((portScore) => `${portScore.portId.replace("port-level-", "lv")}: ${portScore.score}`).join(" | ")}`,
      totalScore: candidate.totalScore,
      crops: PORT_ASSEMBLIES.map((port) => {
        const commands = built.commandsByPortId.get(port.id) ?? [];
        const rootCommand = commands.find((command) => command.selection.sourceFilename === "gate_00.png") ?? commands[0] ?? null;
        return {
          portId: port.id,
          portName: port.name,
          unlockLevel: port.unlockLevel,
          centerCellX: port.cellX,
          centerCellY: port.cellY,
          rootCellX: rootCommand?.cellX ?? port.cellX,
          rootCellY: rootCommand?.cellY ?? port.cellY,
          commands,
        };
      }),
    };
  });
}

function buildVerifiedPortReconstructionCommands(args: {
  parsedMap: ParsedMapBinary;
  mapChipById: Map<number, MapChipRow>;
  terrainById: Map<number, TerrainRow>;
  imageById: Map<number, string>;
  buildingImageById: Map<number, string>;
  natureImageById: Map<number, string>;
  wallImageById: Map<number, string>;
  sebById: Map<number, string>;
  buildingSebById: Map<number, string>;
  natureSebById: Map<number, string>;
  wallSebById: Map<number, string>;
  sebFiles: Map<string, SebFile>;
  buildingSebFiles: Map<string, SebFile>;
  natureSebFiles: Map<string, SebFile>;
  wallSebFiles: Map<string, SebFile>;
  optByStem: Map<string, OptMetadata>;
  imageCache: Map<string, LoadedImageAsset>;
  atlasByName: Map<string, AtlasRegion>;
}): { commands: TileDrawCommand[]; skippedCount: number } {
  const bestEntry = buildRankedPortInterpretationEntries(args, 1)[0];
  const built = bestEntry
    ? bestEntry.built
    : { commands: [], commandsByPortId: new Map<PortAssembly["id"], TileDrawCommand[]>(), skippedCount: 0 };
  return { commands: built.commands, skippedCount: built.skippedCount };
}

function mapChipPassesPlacePortB44_0(row: MapChipRow): boolean {
  return row.type === 9 && row.category !== 78 && (row.flag & PLACEPORT_NO_DESTRUCT_FLAG) !== 0;
}

function buildPlacePortProofCommand(args: {
  chip: MapChipRow;
  x: number;
  y: number;
  sourceBranch: "PP-B03" | "PP-B06" | "PP-B08" | "PP-B09";
  evidenceNote: string;
  terrainById: Map<number, TerrainRow>;
  imageById: Map<number, string>;
  buildingImageById: Map<number, string>;
  natureImageById: Map<number, string>;
  wallImageById: Map<number, string>;
  sebById: Map<number, string>;
  buildingSebById: Map<number, string>;
  natureSebById: Map<number, string>;
  wallSebById: Map<number, string>;
  sebFiles: Map<string, SebFile>;
  buildingSebFiles: Map<string, SebFile>;
  natureSebFiles: Map<string, SebFile>;
  wallSebFiles: Map<string, SebFile>;
  optByStem: Map<string, OptMetadata>;
  imageCache: Map<string, LoadedImageAsset>;
  atlasByName: Map<string, AtlasRegion>;
}): TileDrawCommand | null {
  const chipFolder = getMapChipAssetFolder(args.chip);
  const selection = resolveSpriteSelection(
    chipFolder,
    args.chip,
    getImageTableForFolder(chipFolder, args.imageById, args.buildingImageById, args.natureImageById, args.wallImageById),
    getSebTableForFolder(chipFolder, args.sebById, args.buildingSebById, args.natureSebById, args.wallSebById),
    getSebFilesForFolder(chipFolder, args.sebFiles, args.buildingSebFiles, args.natureSebFiles, args.wallSebFiles),
    args.optByStem,
    args.imageCache,
    args.atlasByName,
    false,
  );

  if (selection.method === "missing-image" || selection.method === "placeholder-skip") {
    return null;
  }

  const imageAsset = selection.sourceFilename ? args.imageCache.get(buildAssetCacheKey(selection.assetFolder, selection.sourceFilename)) : null;
  const resolvedImgFilename = getImageTableForFolder(chipFolder, args.imageById, args.buildingImageById, args.natureImageById, args.wallImageById).get(args.chip.img) ?? "";
  const terrainName = args.chip.relatedDataType === 2 ? args.terrainById.get(args.chip.relatedDataId)?.name ?? null : null;

  return {
    cellX: args.x,
    cellY: args.y,
    rawMapValue: args.chip.id,
    sourceField: "f2",
    f1TerrainTypeValue: null,
    f1TerrainTypeName: null,
    selectedTerrainVisualFamily: null,
    selectedBaseReason: `native-placeport-proof ${args.sourceBranch} ${args.evidenceNote}`,
    layer: args.chip.layer + 0.75,
    mapChipId: args.chip.id,
    mapChipName: args.chip.name,
    mapChipCategory: args.chip.category,
    mapChipImgId: args.chip.img,
    mapChipSebId: args.chip.seb,
    mapChipFrame: args.chip.frame,
    resolvedImgFilename,
    imageCacheKeyUsed: selection.sourceFilename,
    imageObjectInstanceId: imageAsset?.instanceId ?? null,
    imageObjectResolvedSrc: imageAsset?.resolvedSrc ?? "",
    imageObjectRequestPath: imageAsset?.requestPath ?? "",
    loadedPngPath: imageAsset?.requestPath ?? (selection.sourceFilename ? resolveAssetUrl(`world-assets/${selection.assetFolder}/${selection.sourceFilename}`) : ""),
    drawGroup: classifyDrawGroup(args.chip.name, resolvedImgFilename),
    terrainName,
    footprintWidth: args.chip.sizeWidth,
    footprintHeight: args.chip.sizeHeight,
    selection,
  };
}

function buildPlacePortEvidenceLayout(args: {
  mapChipById: Map<number, MapChipRow>;
  terrainById: Map<number, TerrainRow>;
  imageById: Map<number, string>;
  buildingImageById: Map<number, string>;
  natureImageById: Map<number, string>;
  wallImageById: Map<number, string>;
  sebById: Map<number, string>;
  buildingSebById: Map<number, string>;
  natureSebById: Map<number, string>;
  wallSebById: Map<number, string>;
  sebFiles: Map<string, SebFile>;
  buildingSebFiles: Map<string, SebFile>;
  natureSebFiles: Map<string, SebFile>;
  wallSebFiles: Map<string, SebFile>;
  optByStem: Map<string, OptMetadata>;
  imageCache: Map<string, LoadedImageAsset>;
  atlasByName: Map<string, AtlasRegion>;
}): {
  commands: TileDrawCommand[];
  records: PlacePortProofRecord[];
  warnings: string[];
} {
  const warnings = new Set<string>();
  const commands: TileDrawCommand[] = [];
  const records: PlacePortProofRecord[] = [];

  const baseCandidates = PLACEPORT_BASE_CHIP_IDS
    .map((id) => args.mapChipById.get(id))
    .filter((row): row is MapChipRow => Boolean(row))
    .sort((left, right) => left.id - right.id);
  const fixedCandidate = args.mapChipById.get(PLACEPORT_FIXED_CHIP_ID) ?? null;
  const b44_1Candidate = args.mapChipById.get(PLACEPORT_B44_1_CHIP_ID) ?? null;
  const groundDataCandidates = PLACEPORT_GROUNDDATA_CHIP_IDS
    .map((id) => args.mapChipById.get(id))
    .filter((row): row is MapChipRow => Boolean(row))
    .sort((left, right) => left.id - right.id);
  const deterministicGroundData = groundDataCandidates[0] ?? null;

  if (baseCandidates.length === 0) {
    warnings.add("PP-B03 base selector has no resolved MapChip row in current mapChipById lookup.");
  } else {
    warnings.add("PP-B03 category==33 yields multiple candidate rows (67/68/69/70); no single row is auto-placed in proof mode.");
  }
  if (!fixedCandidate) {
    warnings.add("PP-B08 fixed selector row 35 is missing from mapChipById lookup.");
  }
  if (!deterministicGroundData) {
    warnings.add("PP-B06 groundData deterministic candidate is missing (expected IDs 5/6/7).");
  }

  for (const port of PORT_ASSEMBLIES) {
    const base = getPortCompositeBaseCell(port);

    for (const baseCandidate of baseCandidates) {
      records.push({
        chip_id: baseCandidate.id,
        chip_name: baseCandidate.name,
        source_branch: "PP-B03",
        x: base.x,
        y: base.y,
        offset_x: 0,
        offset_y: 0,
        condition: "FirstDataCategoryOf(0x21) => category==33",
        confidence: "MEDIUM",
        evidence_note: `Candidate set member for ${port.id}; unresolved ordering among 67/68/69/70 so not auto-placed`,
        placed: false,
      });
    }

    if (fixedCandidate) {
      if (!mapChipPassesPlacePortB44_0(fixedCandidate)) {
        warnings.add("Row 35 exists but does not satisfy b__44_0 predicate at runtime check.");
      }
      const fixedX = base.x + 4;
      const fixedY = base.y + 1;
      const fixedCommand = buildPlacePortProofCommand({
        chip: fixedCandidate,
        x: fixedX,
        y: fixedY,
        sourceBranch: "PP-B08",
        evidenceNote: `${port.id} fixed offset +4,+1 from PlacePort base`,
        terrainById: args.terrainById,
        imageById: args.imageById,
        buildingImageById: args.buildingImageById,
        natureImageById: args.natureImageById,
        wallImageById: args.wallImageById,
        sebById: args.sebById,
        buildingSebById: args.buildingSebById,
        natureSebById: args.natureSebById,
        wallSebById: args.wallSebById,
        sebFiles: args.sebFiles,
        buildingSebFiles: args.buildingSebFiles,
        natureSebFiles: args.natureSebFiles,
        wallSebFiles: args.wallSebFiles,
        optByStem: args.optByStem,
        imageCache: args.imageCache,
        atlasByName: args.atlasByName,
      });
      if (fixedCommand) {
        commands.push(fixedCommand);
      } else {
        warnings.add(`PP-B08 ${port.id} fixed chip ${fixedCandidate.id} could not resolve sprite selection.`);
      }
      records.push({
        chip_id: fixedCandidate.id,
        chip_name: fixedCandidate.name,
        source_branch: "PP-B08",
        x: fixedX,
        y: fixedY,
        offset_x: 4,
        offset_y: 1,
        condition: "b__44_0 => type==9 && category!=78 && Check(0x2000000)",
        confidence: "HIGH",
        evidence_note: `Resolved fixed branch row for ${port.id}`,
        placed: Boolean(fixedCommand),
      });
    }

    if (b44_1Candidate) {
      records.push({
        chip_id: b44_1Candidate.id,
        chip_name: b44_1Candidate.name,
        source_branch: "PP-B09",
        x: null,
        y: null,
        offset_x: null,
        offset_y: null,
        condition: "b__44_1 => category==51",
        confidence: "HIGH",
        evidence_note: `No direct PlaceChip sink in bounded PlacePort region for ${port.id}; kept as non-placement proof record`,
        placed: false,
      });
    }

    if (deterministicGroundData) {
      records.push({
        chip_id: deterministicGroundData.id,
        chip_name: deterministicGroundData.name,
        source_branch: "PP-B06",
        x: null,
        y: null,
        offset_x: null,
        offset_y: null,
        condition: "neighbor map chip category==7 then Random(groundData type==10/category==0)",
        confidence: "UNKNOWN",
        evidence_note: `Loop-derived neighbor offsets unresolved for ${port.id}; deterministic candidate=${deterministicGroundData.id} recorded but not placed`,
        placed: false,
      });
    }
  }

  warnings.add("PP-B06 neighbor branch offsets are loop-derived and remain UNKNOWN; no guessed coordinates were placed.");
  warnings.add("PP-B09 category==51 branch is recorded only as proof metadata because no direct in-region placement sink is proven.");

  return {
    commands,
    records,
    warnings: [...warnings],
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
      rotation: asInt(parts[21], 0),
      field21: asInt(parts[21], 0),
      sizeWidth: asInt(parts[22], 1),
      sizeHeight: asInt(parts[23], 1),
      unitWidth: asInt(parts[24], 1),
      unitHeight: asInt(parts[25], 1),
      moveSpeedRate: asInt(parts[26], 100),
      flag: asInt(parts[27], 0),
      field24: asInt(parts[24], 1),
      field25: asInt(parts[25], 1),
      field26: asInt(parts[26], 100),
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
  const x = Math.floor(worldX);
  const y = Math.floor(worldY);
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
  portGateLayouts: PortGateLayoutByPort,
  portAssetCache?: Map<string, LoadedImageAsset>,
): PortGatePiece["assetKey"] | null {
  const rect = canvas.getBoundingClientRect();
  const localX = clientX - rect.left;
  const localY = clientY - rect.top;
  const zoom = camera.zoom;

  for (let portIndex = PORT_ASSEMBLIES.length - 1; portIndex >= 0; portIndex -= 1) {
    const port = PORT_ASSEMBLIES[portIndex];
    const base = getPortCompositeBaseCell(port);
    const portLayout = portGateLayouts[port.id] ?? DEFAULT_PORT_GATE_LAYOUT;

    for (let pieceIndex = PORT_GATE_PIECES.length - 1; pieceIndex >= 0; pieceIndex -= 1) {
      const piece = PORT_GATE_PIECES[pieceIndex];
      const offset = portLayout[piece.assetKey] ?? { dx: piece.dx, dy: piece.dy };
      const iso = worldToIso(base.x + offset.dx, base.y + offset.dy, zoom, camera.offsetX, camera.offsetY);
      const diamondCenterY = iso.y + (TILE_HEIGHT * zoom) / 2;
      const asset = portAssetCache?.get(piece.assetKey);

      if (asset) {
        const opt = PORT_GATE_OPT_PLACEMENT[piece.assetKey];
        const cellDrawW = opt.cellW * zoom;
        const cellDrawH = opt.cellH * zoom;
        const cellDrawX = iso.x - cellDrawW / 2;
        const cellDrawY = diamondCenterY + TILE_HEIGHT * zoom - cellDrawH;
        const imageDrawX = cellDrawX + opt.destX * zoom;
        const imageDrawY = cellDrawY + opt.destY * zoom;
        const imageDrawW = asset.image.width * zoom;
        const imageDrawH = asset.image.height * zoom;
        if (
          localX >= imageDrawX &&
          localX <= imageDrawX + imageDrawW &&
          localY >= imageDrawY &&
          localY <= imageDrawY + imageDrawH
        ) {
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
  for (const facility of ONE_PIECE_FACILITY_OVERLAYS) {
    // cellX/cellY stores the exclusive-end of the 2x2 footprint (NW = cellX-2, cellY-2).
    const iso = worldToIso(facility.cellX - 2, facility.cellY - 2, zoom, camera.offsetX, camera.offsetY);
    // The correct 2x2 diamond center is one tile-halfH below the NW tile center.
    const diamondCenterY = iso.y + halfH_tile;
    if (iso.x < -96 || iso.x > canvasWidth + 96 || diamondCenterY < -128 || diamondCenterY > canvasHeight + 96) {
      continue;
    }

    const facilityStyle = facility.id === 172 ? OVERLAY_STYLES.aiCorrected : null;
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

    if (facilityStyle) {
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

    if (building) {
      const drawW = building.width * zoom;
      const drawH = building.height * zoom;
      const drawX = iso.x - drawW / 2;
      // Sprite bottom at south vertex of the 2x2 diamond.
      const drawY = diamondCenterY + footprintHalfH - drawH;
      context.shadowColor = "rgba(0,0,0,0.35)";
      context.shadowBlur = 4;
      context.drawImage(building, drawX, drawY, drawW, drawH);
      context.shadowBlur = 0;
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

    if (facilityStyle) {
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

function drawNativeDataV2FacilityDuplicateOverlays(
  context: CanvasRenderingContext2D,
  placements: NativeDataV2FacilityPlacement[],
  facilityBuildingCache: Map<number, LoadedImageAsset>,
  camera: CameraState,
  canvasWidth: number,
  canvasHeight: number,
  showLabels: boolean,
) {
  const zoom = camera.zoom;
  const tileHalfH = (TILE_HEIGHT * zoom) / 2;
  const style = OVERLAY_STYLES.nativeDataV2;

  context.save();
  for (const placement of placements) {
    const iso = worldToIso(placement.coveredMinX, placement.coveredMinY, zoom, camera.offsetX, camera.offsetY);
    const footprintCenterY = iso.y + tileHalfH * (placement.sizeHeight - 1);
    const footprintHalfW = (TILE_WIDTH * placement.sizeWidth * zoom) / 2;
    const footprintHalfH = (TILE_HEIGHT * placement.sizeHeight * zoom) / 2;

    const visible =
      iso.x >= -192 &&
      iso.x <= canvasWidth + 192 &&
      footprintCenterY >= -224 &&
      footprintCenterY <= canvasHeight + 224;
    if (!visible) {
      continue;
    }

    context.strokeStyle = style.footprintStroke;
    context.fillStyle = style.footprintFill;
    context.lineWidth = Math.max(1, 1.5 * zoom);
    drawIsoDiamond(context, iso.x, footprintCenterY, footprintHalfW, footprintHalfH);
    context.fill();
    context.stroke();

    const rawMarkerHalfW = Math.max(6, (TILE_WIDTH * zoom) / 4);
    const rawMarkerHalfH = Math.max(4, (TILE_HEIGHT * zoom) / 4);
    context.strokeStyle = style.anchorStroke;
    context.lineWidth = Math.max(2, 2 * zoom);
    drawIsoDiamond(context, iso.x, iso.y, rawMarkerHalfW, rawMarkerHalfH);
    context.stroke();

    const building = facilityBuildingCache.get(placement.facilityId)?.image;
    if (building) {
      const drawW = building.width * zoom;
      const drawH = building.height * zoom;
      const drawX = iso.x - drawW / 2;
      const drawY = footprintCenterY + footprintHalfH - drawH;
      const previousAlpha = context.globalAlpha;
      context.globalAlpha = 0.86;
      context.drawImage(building, drawX, drawY, drawW, drawH);
      context.globalAlpha = previousAlpha;
    }

    if (showLabels && zoom >= 0.72) {
      context.font = `bold ${Math.max(10, 11 * zoom)}px sans-serif`;
      context.textAlign = "center";
      context.textBaseline = "top";
      context.lineWidth = 3;
      context.strokeStyle = "rgba(15,23,42,0.95)";
      context.fillStyle = "#7dd3fc";
      const label = `${placement.facilityName} (native-data-v2)`;
      context.strokeText(label, iso.x, footprintCenterY + footprintHalfH + 2);
      context.fillText(label, iso.x, footprintCenterY + footprintHalfH + 2);
    }
  }
  context.restore();
}

function drawNativePlacePortEvidenceLayers(
  context: CanvasRenderingContext2D,
  camera: CameraState,
  diagnostics: {
    fillWater: PlacePortProofRecord[];
    placeChip: PlacePortProofRecord[];
    facilityComposite: PlacePortProofRecord[];
  },
  showFillWater: boolean,
  showPlaceChip: boolean,
  showFacilityComposite: boolean,
) {
  const zoom = camera.zoom;
  const markerHalfW = Math.max(8, (TILE_WIDTH * zoom) / 3);
  const markerHalfH = Math.max(5, (TILE_HEIGHT * zoom) / 3);

  const drawGroup = (records: PlacePortProofRecord[], stroke: string, fill: string) => {
    context.save();
    context.strokeStyle = stroke;
    context.fillStyle = fill;
    context.lineWidth = Math.max(1, 1.5 * zoom);
    for (const record of records) {
      if (typeof record.x !== "number" || typeof record.y !== "number") {
        continue;
      }
      const iso = worldToIso(record.x, record.y, zoom, camera.offsetX, camera.offsetY);
      drawIsoDiamond(context, iso.x, iso.y, markerHalfW, markerHalfH);
      context.fill();
      context.stroke();
    }
    context.restore();
  };

  if (showFillWater) {
    drawGroup(diagnostics.fillWater, "rgba(34, 211, 238, 0.95)", "rgba(34, 211, 238, 0.22)");
  }
  if (showPlaceChip) {
    drawGroup(diagnostics.placeChip, "rgba(132, 204, 22, 0.95)", "rgba(132, 204, 22, 0.22)");
  }
  if (showFacilityComposite) {
    drawGroup(diagnostics.facilityComposite, "rgba(217, 70, 239, 0.95)", "rgba(217, 70, 239, 0.22)");
  }
}

function drawPortManualSandboxOverlay(
  context: CanvasRenderingContext2D,
  camera: CameraState,
  portGateLayouts: PortGateLayoutByPort,
  showLabels: boolean,
) {
  const zoom = camera.zoom;
  const styleStroke = "rgba(244, 114, 182, 0.95)";
  const styleFill = "rgba(244, 114, 182, 0.18)";

  context.save();
  for (const port of PORT_ASSEMBLIES) {
    const base = getPortCompositeBaseCell(port);
    const portLayout = portGateLayouts[port.id] ?? DEFAULT_PORT_GATE_LAYOUT;
    for (const piece of PORT_GATE_PIECES) {
      const offset = portLayout[piece.assetKey] ?? { dx: piece.dx, dy: piece.dy };
      const anchorX = base.x + offset.dx;
      const anchorY = base.y + offset.dy;
      const iso = worldToIso(anchorX, anchorY, zoom, camera.offsetX, camera.offsetY);
      const centerY = iso.y + (TILE_HEIGHT * zoom) / 2;
      drawIsoDiamond(context, iso.x, centerY, TILE_WIDTH * zoom, TILE_HEIGHT * zoom);
      context.fillStyle = styleFill;
      context.strokeStyle = styleStroke;
      context.lineWidth = Math.max(1, 1.5 * zoom);
      context.fill();
      context.stroke();
      if (showLabels && zoom >= 0.7) {
        context.font = `bold ${Math.max(10, 11 * zoom)}px sans-serif`;
        context.textAlign = "center";
        context.textBaseline = "top";
        context.lineWidth = 3;
        context.strokeStyle = "rgba(15,23,42,0.95)";
        context.fillStyle = "#f9a8d4";
        const label = `sandbox ${piece.assetKey} (${offset.dx},${offset.dy})`;
        context.strokeText(label, iso.x, centerY + TILE_HEIGHT * zoom + 2);
        context.fillText(label, iso.x, centerY + TILE_HEIGHT * zoom + 2);
      }
    }
  }
  context.restore();
}

function drawPortPieceAnchorAuditLayer(
  context: CanvasRenderingContext2D,
  pipeline: RenderPipeline,
  camera: CameraState,
) {
  const zoom = camera.zoom;
  const halfTileH = (TILE_HEIGHT * zoom) / 2;

  context.save();
  for (const port of PORT_ASSEMBLIES) {
    const base = getPortCompositeBaseCell(port);
    for (const piece of PORT_GATE_PIECES) {
      const offset = DEFAULT_PORT_GATE_LAYOUT[piece.assetKey] ?? { dx: piece.dx, dy: piece.dy };
      const anchorX = base.x + offset.dx;
      const anchorY = base.y + offset.dy;
      const iso = worldToIso(anchorX, anchorY, zoom, camera.offsetX, camera.offsetY);

      // logical anchor
      context.strokeStyle = "rgba(255,255,255,0.95)";
      context.lineWidth = Math.max(1.5, 2 * zoom);
      drawIsoDiamond(context, iso.x, iso.y, Math.max(6, (TILE_WIDTH * zoom) / 4), Math.max(4, (TILE_HEIGHT * zoom) / 4));
      context.stroke();

      // logical footprint (2x2)
      const footprintCenterY = iso.y + halfTileH;
      context.strokeStyle = "rgba(56, 189, 248, 0.95)";
      context.lineWidth = Math.max(1, 1.4 * zoom);
      drawIsoDiamond(context, iso.x, footprintCenterY, TILE_WIDTH * zoom, TILE_HEIGHT * zoom);
      context.stroke();

      const asset = pipeline.portAssetCache.get(piece.assetKey)?.image;
      if (!asset) {
        continue;
      }
      const opt = PORT_GATE_OPT_PLACEMENT[piece.assetKey];
      const drawW = asset.width * zoom;
      const drawH = asset.height * zoom;
      const drawX = iso.x - (opt.cellW * zoom) / 2 + opt.destX * zoom;
      const drawY = iso.y + TILE_HEIGHT * zoom - opt.cellH * zoom + opt.destY * zoom;
      const spriteOriginX = drawX + drawW / 2;
      const spriteOriginY = drawY + drawH;

      // sprite bounds
      context.strokeStyle = "rgba(250, 204, 21, 0.95)";
      context.lineWidth = Math.max(1, 1.3 * zoom);
      context.strokeRect(drawX + 0.5, drawY + 0.5, drawW - 1, drawH - 1);

      // sprite origin
      context.beginPath();
      context.fillStyle = "rgba(250, 204, 21, 0.95)";
      context.arc(spriteOriginX, spriteOriginY, Math.max(3, 4 * zoom), 0, Math.PI * 2);
      context.fill();
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

function buildManualPortGateFootprintCellSet(portGateLayouts: PortGateLayoutByPort): Set<string> {
  const cells = new Set<string>();

  for (const port of PORT_ASSEMBLIES) {
    const base = getPortCompositeBaseCell(port);
    const portLayout = portGateLayouts[port.id] ?? DEFAULT_PORT_GATE_LAYOUT;

    for (const piece of PORT_GATE_PIECES) {
      const offset = portLayout[piece.assetKey] ?? { dx: piece.dx, dy: piece.dy };
      for (let yy = 0; yy < 2; yy++) {
        for (let xx = 0; xx < 2; xx++) {
          const cellX = Math.round(base.x + offset.dx + xx);
          const cellY = Math.round(base.y + offset.dy + yy);
          cells.add(`${cellX},${cellY}`);
        }
      }
    }
  }

  return cells;
}

function buildManualPortOverlayCellSet(
  portGateLayouts: PortGateLayoutByPort,
  portBridgeLayouts: PortBridgeLayoutByPort,
): Set<string> {
  const cells = buildManualPortGateFootprintCellSet(portGateLayouts);

  for (const port of PORT_ASSEMBLIES) {
    const base = getPortCompositeBaseCell(port);
    const bridgeOffset = portBridgeLayouts[port.id] ?? DEFAULT_PORT_BRIDGE_LAYOUT;

    for (const piece of PORT_BRIDGE_PIECES) {
      cells.add(`${Math.round(base.x + bridgeOffset.dx + piece.dx)},${Math.round(base.y + bridgeOffset.dy + piece.dy)}`);
    }
  }

  return cells;
}

function drawVerifiedPortReconstructionProof(
  context: CanvasRenderingContext2D,
  pipeline: RenderPipeline,
  camera: CameraState,
  canvasWidth: number,
  canvasHeight: number,
  commands: TileDrawCommand[],
  showProofOutline: boolean,
  showLabels: boolean,
) {
  const colorByFamily: Record<AssetFolder, { stroke: string; fill: string }> = {
    building: { stroke: "rgba(34, 211, 238, 0.98)", fill: "rgba(34, 211, 238, 0.16)" },
    chip: { stroke: "rgba(250, 204, 21, 0.98)", fill: "rgba(250, 204, 21, 0.16)" },
    wall: { stroke: "rgba(248, 113, 113, 0.98)", fill: "rgba(248, 113, 113, 0.16)" },
    nature: { stroke: "rgba(74, 222, 128, 0.98)", fill: "rgba(74, 222, 128, 0.16)" },
  };

  context.save();
  for (const command of commands) {
    const sprite = command.selection;
    const imageAsset = sprite.sourceFilename ? pipeline.imageCache.get(buildAssetCacheKey(sprite.assetFolder, sprite.sourceFilename)) : null;
    const image = imageAsset?.image;
    if (!image) {
      continue;
    }

    const iso = worldToIso(command.cellX, command.cellY, camera.zoom, camera.offsetX, camera.offsetY);
    const clippedSrcW = Math.min(sprite.srcW, image.width - sprite.srcX);
    const clippedSrcH = Math.min(sprite.srcH, image.height - sprite.srcY);
    if (clippedSrcW <= 0 || clippedSrcH <= 0) {
      continue;
    }

    const sourceKey = command.selection.sourceFilename.replace(/\.png$/i, "") as PortGatePiece["assetKey"] | string;
    const optPlacement = Object.prototype.hasOwnProperty.call(PORT_GATE_OPT_PLACEMENT, sourceKey)
      ? PORT_GATE_OPT_PLACEMENT[sourceKey as PortGatePiece["assetKey"]]
      : null;
    const placement = optPlacement
      ? (() => {
          const halfTileH = (TILE_HEIGHT * camera.zoom) / 2;
          const diamondCenterY = iso.y + halfTileH;
          const cellDrawW = optPlacement.cellW * camera.zoom;
          const cellDrawH = optPlacement.cellH * camera.zoom;
          const cellDrawX = iso.x - cellDrawW / 2;
          const cellDrawY = diamondCenterY + halfTileH - cellDrawH;
          return {
            drawX: cellDrawX + optPlacement.destX * camera.zoom,
            drawY: cellDrawY + (optPlacement.destY + PORT_GATE_GROUNDING_Y_NUDGE) * camera.zoom,
            drawW: image.width * camera.zoom,
            drawH: image.height * camera.zoom,
            clipToDiamond: false,
          };
        })()
      : computeTexturePlacement({
          renderMode: "mapchip-f2",
          terrainAlignmentMode: "diamond-fit",
          command,
          isoX: iso.x,
          isoY: iso.y,
          zoom: camera.zoom,
          clippedSrcW,
          clippedSrcH,
        });

    if (
      placement.drawX > canvasWidth ||
      placement.drawY > canvasHeight ||
      placement.drawX + placement.drawW < 0 ||
      placement.drawY + placement.drawH < 0
    ) {
      continue;
    }

    const familyStyle = colorByFamily[sprite.assetFolder];
    context.globalAlpha = 0.96;
    context.drawImage(
      image,
      sprite.srcX,
      sprite.srcY,
      clippedSrcW,
      clippedSrcH,
      placement.drawX,
      placement.drawY,
      placement.drawW,
      placement.drawH,
    );
    context.globalAlpha = 1;

    if (showProofOutline) {
      context.fillStyle = familyStyle.fill;
      context.strokeStyle = familyStyle.stroke;
      context.lineWidth = Math.max(1.5, 2 * camera.zoom);
      context.fillRect(placement.drawX, placement.drawY, placement.drawW, placement.drawH);
      context.strokeRect(placement.drawX + 0.5, placement.drawY + 0.5, placement.drawW - 1, placement.drawH - 1);
    }

    const footprintIso = worldToIso(command.cellX, command.cellY, camera.zoom, camera.offsetX, camera.offsetY);
    if (showProofOutline) {
      context.strokeStyle = familyStyle.stroke;
      context.lineWidth = Math.max(1, 1.5 * camera.zoom);
      drawIsoDiamond(context, footprintIso.x, footprintIso.y, (TILE_WIDTH * camera.zoom) / 2, (TILE_HEIGHT * camera.zoom) / 2);
    }

    if (showLabels) {
      const row = pipeline.lookups.mapChipById.get(command.mapChipId);
      drawOverlayLabelBackground(
        context,
        placement.drawX + placement.drawW / 2,
        placement.drawY - Math.max(8, 8 * camera.zoom),
        [
          `chip ${command.mapChipId} res ${row?.res ?? "?"}`,
          `img ${command.mapChipImgId} seb ${command.mapChipSebId}`,
          `${sprite.assetFolder} ${sprite.sourceFilename || "missing"}`,
        ],
        familyStyle.stroke,
        Math.max(0.7, camera.zoom),
      );
    }
  }
  context.restore();
}

function drawPortAssemblies(
  context: CanvasRenderingContext2D,
  portAssetCache: Map<string, LoadedImageAsset>,
  camera: CameraState,
  canvasWidth: number,
  canvasHeight: number,
  portGateLayouts: PortGateLayoutByPort,
  portBridgeLayouts: PortBridgeLayoutByPort,
  showBridgePieces: boolean,
  _showLabels: boolean,
) {
  const zoom = camera.zoom;
  const halfTileH = (TILE_HEIGHT * zoom) / 2;

  context.save();
  for (const port of PORT_ASSEMBLIES) {
    // Facility id 7 / chip 67 is the root 2x2 piece. The full composite extends 4x4 from that root.
    const base = getPortCompositeBaseCell(port);
    const portLayout = portGateLayouts[port.id] ?? DEFAULT_PORT_GATE_LAYOUT;
    const bridgeOffset = portBridgeLayouts[port.id] ?? DEFAULT_PORT_BRIDGE_LAYOUT;
    const baseX = base.x;
    const baseY = base.y;
    const baseIso = worldToIso(baseX, baseY, zoom, camera.offsetX, camera.offsetY);
    if (baseIso.x < -320 || baseIso.x > canvasWidth + 320 || baseIso.y < -260 || baseIso.y > canvasHeight + 180) {
      continue;
    }

    // Draw visible gate sprites for the 4-piece Port composite.
    for (const piece of PORT_GATE_PIECES) {
      const offset = portLayout[piece.assetKey] ?? { dx: piece.dx, dy: piece.dy };
      const asset = portAssetCache.get(piece.assetKey)?.image;
      if (!asset) {
        continue;
      }

      const iso = worldToIso(baseX + offset.dx, baseY + offset.dy, zoom, camera.offsetX, camera.offsetY);
      const optPlacement = PORT_GATE_OPT_PLACEMENT[piece.assetKey];
      const drawW = asset.width * zoom;
      const drawH = asset.height * zoom;
      const drawX = optPlacement
        ? iso.x - (optPlacement.cellW * zoom) / 2 + optPlacement.destX * zoom
        : iso.x - drawW / 2;
      const drawY = optPlacement
        ? iso.y + halfTileH * 2 - optPlacement.cellH * zoom + (optPlacement.destY + PORT_GATE_GROUNDING_Y_NUDGE) * zoom
        : iso.y + halfTileH - drawH;

      context.globalAlpha = 0.97;
      context.drawImage(asset, drawX, drawY, drawW, drawH);
      context.globalAlpha = 1;
    }

    if (showBridgePieces) {
      for (const piece of PORT_BRIDGE_PIECES) {
        const asset = portAssetCache.get(piece.assetKey)?.image;
        if (!asset) {
          continue;
        }
        const iso = worldToIso(baseX + bridgeOffset.dx + piece.dx, baseY + bridgeOffset.dy + piece.dy, zoom, camera.offsetX, camera.offsetY);
        const drawW = asset.width * zoom;
        const drawH = asset.height * zoom;
        const drawX = iso.x - drawW / 2;
        const drawY = iso.y + halfTileH - drawH;
        context.globalAlpha = 0.9;
        context.drawImage(asset, drawX, drawY, drawW, drawH);
        context.globalAlpha = 1;
      }
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

function computeInitialCamera(mapWidth: number, mapHeight: number, canvasWidth: number, canvasHeight: number): CameraState {
  if (typeof window !== "undefined") {
    const focus = new URLSearchParams(window.location.search).get("focus");
    const port = focus === "port44" ? PORT_ASSEMBLIES[1] : focus === "port7" || focus === "ports" ? PORT_ASSEMBLIES[0] : null;
    if (port) {
      const zoom = 0.8;
      const targetX = port.cellX + 1;
      const targetY = port.cellY - 1;
      return computeCameraForWorldCell(targetX, targetY, canvasWidth, canvasHeight, zoom);
    }
  }

  return computeResetCamera(mapWidth, mapHeight, canvasWidth, canvasHeight, 0.65);
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

function getParsedCellAt(parsed: ParsedMapBinary, x: number, y: number): ParsedMapCell | null {
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

const PORT_INTERPRETATION_CROP_RADIUS = 12;
const PORT_INTERPRETATION_CANVAS_WIDTH = 320;
const PORT_INTERPRETATION_CANVAS_HEIGHT = 228;
const PORT_INTERPRETATION_ZOOM = 0.74;
const PORT_GLOBAL_ANCHOR_OFFSETS = [
  { dx: 0, dy: 0 },
  { dx: 1, dy: 0 },
  { dx: 2, dy: 0 },
  { dx: 1, dy: -1 },
  { dx: 2, dy: -1 },
  { dx: 1, dy: 1 },
  { dx: 2, dy: 1 },
  { dx: 3, dy: 0 },
  { dx: 3, dy: -1 },
  { dx: 3, dy: 1 },
  { dx: 0, dy: -1 },
  { dx: 0, dy: 1 },
] as const;
const PORT_FACILITY_ORDERED_DEPENDENTS: PortGatePiece["chipId"][] = [70, 68, 69];

function PortInterpretationCropCanvas(props: {
  pipeline: RenderPipeline;
  crop: PortInterpretationCrop;
  natureCategoryVisibility: NatureCategoryVisibility;
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) {
      return;
    }

    const context = canvas.getContext("2d");
    if (!context) {
      return;
    }

    drawPortInterpretationCrop(context, props.pipeline, props.crop, props.natureCategoryVisibility, canvas.width, canvas.height);
  }, [props.crop, props.natureCategoryVisibility, props.pipeline]);

  return (
    <canvas
      ref={canvasRef}
      width={PORT_INTERPRETATION_CANVAS_WIDTH}
      height={PORT_INTERPRETATION_CANVAS_HEIGHT}
      className="block h-auto w-full"
      aria-label={`${props.crop.portName} interpretation crop`}
    />
  );
}

function drawPortInterpretationCrop(
  context: CanvasRenderingContext2D,
  pipeline: RenderPipeline,
  crop: PortInterpretationCrop,
  natureCategoryVisibility: NatureCategoryVisibility,
  width: number,
  height: number,
) {
  context.clearRect(0, 0, width, height);
  context.fillStyle = "#020617";
  context.fillRect(0, 0, width, height);
  drawGridBackground(context, width, height);

  const minX = crop.centerCellX - PORT_INTERPRETATION_CROP_RADIUS;
  const maxX = crop.centerCellX + PORT_INTERPRETATION_CROP_RADIUS;
  const minY = crop.centerCellY - PORT_INTERPRETATION_CROP_RADIUS;
  const maxY = crop.centerCellY + PORT_INTERPRETATION_CROP_RADIUS;
  const inCropBounds = (command: TileDrawCommand) => command.cellX >= minX && command.cellX <= maxX && command.cellY >= minY && command.cellY <= maxY;
  const baseCommands = filterNatureCommandsByVisibility(pipeline.f1TerrainCommands, natureCategoryVisibility).filter(inCropBounds);
  const combinedCommands = [...baseCommands, ...crop.commands.filter(inCropBounds)].sort((left, right) => {
    const depthA = left.cellX + left.cellY;
    const depthB = right.cellX + right.cellY;
    if (depthA !== depthB) {
      return depthA - depthB;
    }
    if (left.drawGroup !== right.drawGroup) {
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

  const camera = computeCameraForWorldCell(crop.centerCellX, crop.centerCellY, width, height, PORT_INTERPRETATION_ZOOM);
  camera.offsetY += 18;

  for (const command of combinedCommands) {
    const iso = worldToIso(command.cellX, command.cellY, camera.zoom, camera.offsetX, camera.offsetY);
    const sprite = command.selection;
    const imageAsset = pipeline.imageCache.get(buildAssetCacheKey(sprite.assetFolder, sprite.sourceFilename));
    if (!imageAsset?.image) {
      continue;
    }

    const image = imageAsset.image;
    const clippedSrcW = Math.min(sprite.srcW, image.width - sprite.srcX);
    const clippedSrcH = Math.min(sprite.srcH, image.height - sprite.srcY);
    if (clippedSrcW <= 0 || clippedSrcH <= 0) {
      continue;
    }

    const placement = computeTexturePlacement({
      renderMode: "f1-terrain",
      terrainAlignmentMode: "diamond-fit",
      command,
      isoX: iso.x,
      isoY: iso.y,
      zoom: camera.zoom,
      clippedSrcW,
      clippedSrcH,
    });

    if (placement.drawX > width || placement.drawY > height || placement.drawX + placement.drawW < 0 || placement.drawY + placement.drawH < 0) {
      continue;
    }

    context.drawImage(
      image,
      sprite.srcX,
      sprite.srcY,
      clippedSrcW,
      clippedSrcH,
      placement.drawX,
      placement.drawY,
      placement.drawW,
      placement.drawH,
    );
  }

  const rootIso = worldToIso(crop.rootCellX, crop.rootCellY, camera.zoom, camera.offsetX, camera.offsetY);
  context.strokeStyle = "rgba(34, 211, 238, 0.9)";
  context.lineWidth = 1.5;
  drawIsoDiamond(context, rootIso.x, rootIso.y, (TILE_WIDTH * camera.zoom) / 2, (TILE_HEIGHT * camera.zoom) / 2);
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

function isWaterLikeCommand(command: TileDrawCommand): boolean {
  if (command.terrainName && isWaterToken(command.terrainName)) {
    return true;
  }
  if (command.selectedTerrainVisualFamily && isWaterTerrainFamily(command.selectedTerrainVisualFamily)) {
    return true;
  }
  if (command.mapChipName && isWaterToken(command.mapChipName)) {
    return true;
  }
  if (command.resolvedImgFilename && isWaterToken(command.resolvedImgFilename)) {
    return true;
  }
  if (command.selection.sourceFilename && isWaterToken(command.selection.sourceFilename)) {
    return true;
  }
  return false;
}

function isDirtLikeCommand(command: TileDrawCommand): boolean {
  const terrainToken = (command.terrainName ?? "").toLowerCase();
  const familyToken = (command.selectedTerrainVisualFamily ?? "").toLowerCase();
  const filenameToken = (command.resolvedImgFilename ?? command.selection.sourceFilename ?? "").toLowerCase();
  return terrainToken.includes("dirt") || familyToken.includes("tuchi") || /^tuchi0\d/.test(filenameToken);
}

function isRequestedPortDirtFilename(filename: string): boolean {
  const token = filename.toLowerCase();
  return /(^|\/)(tuchi00|tuchi01|tuchi02)\.png$/.test(token) || /(tuchi00|tuchi01|tuchi02)\.png/.test(token);
}

function buildFallbackDirtTemplates(primaryCommands: TileDrawCommand[], secondaryCommands: TileDrawCommand[] = []): TileDrawCommand[] {
  const commands = [...primaryCommands, ...secondaryCommands];
  const dirt = commands
    .filter((command) => command.selection.assetFolder === "chip")
    .filter((command) => !isWaterLikeCommand(command))
    .filter((command) => isDirtLikeCommand(command));

  const preferred = dirt.filter((command) => {
    const filename = command.resolvedImgFilename || command.selection.sourceFilename || "";
    return isRequestedPortDirtFilename(filename);
  });

  const source = preferred.length > 0 ? preferred : dirt;

  const uniqueByFilename = new Map<string, TileDrawCommand>();
  for (const entry of source) {
    const key = (entry.resolvedImgFilename || entry.selection.sourceFilename || `${entry.mapChipId}`).toLowerCase();
    if (!uniqueByFilename.has(key)) {
      uniqueByFilename.set(key, entry);
    }
  }

  const templates = [...uniqueByFilename.values()];
  if (templates.length > 0) {
    return templates;
  }

  const fallbackNonWater = commands
    .filter((command) => command.selection.assetFolder === "chip")
    .filter((command) => !isWaterLikeCommand(command));
  return fallbackNonWater.slice(0, 3);
}

function buildDirtReplacementForCell(command: TileDrawCommand, templates: TileDrawCommand[]): TileDrawCommand | null {
  if (templates.length === 0) {
    return null;
  }
  const index = Math.abs((command.cellX * 31 + command.cellY * 17) % templates.length);
  const template = templates[index];
  return {
    ...template,
    cellX: command.cellX,
    cellY: command.cellY,
    layer: command.layer,
    drawGroup: command.drawGroup,
  };
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

function getTerrainRowResolvedFilename(
  row: TerrainRow,
  imageById: Map<number, string>,
  buildingImageById: Map<number, string>,
  natureImageById: Map<number, string>,
  wallImageById: Map<number, string>,
): string {
  const folder = getAssetFolderFromRes(row.res);
  return getImageTableForFolder(folder, imageById, buildingImageById, natureImageById, wallImageById).get(row.img) ?? "";
}

function findFallbackBaseFilename(
  rows: TerrainRow[],
  imageById: Map<number, string>,
  buildingImageById: Map<number, string>,
  natureImageById: Map<number, string>,
  wallImageById: Map<number, string>,
  family: string | null,
): string | null {
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
    return first ? getTerrainRowResolvedFilename(first, imageById, buildingImageById, natureImageById, wallImageById) || null : null;
  }

  const loweredFamily = family.toLowerCase();
  const familyRow = sortedRows.find((row) => {
    const filename = getTerrainRowResolvedFilename(row, imageById, buildingImageById, natureImageById, wallImageById);
    return filename.toLowerCase().includes(loweredFamily) || row.name.toLowerCase().includes(loweredFamily);
  });
  const targetRow = familyRow ?? sortedRows[0];
  return targetRow ? getTerrainRowResolvedFilename(targetRow, imageById, buildingImageById, natureImageById, wallImageById) || null : null;
}

function pickTerrainRowForCell(
  rows: TerrainRow[],
  family: string | null,
  imageById: Map<number, string>,
  buildingImageById: Map<number, string>,
  natureImageById: Map<number, string>,
  wallImageById: Map<number, string>,
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
        const filename = getTerrainRowResolvedFilename(row, imageById, buildingImageById, natureImageById, wallImageById);
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
  if (mode === "clean-source-mapchip") {
    return "Clean-Source MapChip Lab";
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
  if (mode === "terrain-f2") {
    return "clean-source-mapchip";
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
  return <ResearchPreviewGate><UnverifiedRuntimeWorldRenderTestPage {...props} /></ResearchPreviewGate>;
}
