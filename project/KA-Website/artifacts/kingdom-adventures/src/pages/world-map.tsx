import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { mapTerrainCodeToType, parseTerrainMapCsv } from "@/lib/monster-truth";
import fullTerrainCsv from "../data/full-terrain-map.csv?raw";

const FULL_TERRAIN_MAP: number[][] = parseTerrainMapCsv(fullTerrainCsv);

type TerrainType =
  | "grass"
  | "sand"
  | "volcano"
  | "swamp"
  | "rock"
  | "snow"
  | "ground";

type SurveyCategory = "storehouse" | "chaos_stone" | "cash_register" | "dragon_taming";
type SurveyDef = {
  id: number; name: string; terrainId: number; minLevel: number;
  minCost: number; maxCost: number; minHearts: number; maxHearts: number;
  minRate: number; maxRate: number; minTimeSec: number; maxTimeSec: number;
  category: SurveyCategory;
};

// CSV terrain numeric ID ? map TerrainType (-1 = any land)
const SURVEY_TERRAIN_MAP: Partial<Record<number, TerrainType>> = {
  1: "grass", 2: "grass", 3: "rock", 4: "sand", 5: "snow", 6: "swamp", 7: "volcano", 15: "ground",
};
const SURVEY_CAT_COLORS: Record<SurveyCategory, string> = {
  storehouse: "#3b82f6", chaos_stone: "#a855f7", cash_register: "#f59e0b", dragon_taming: "#ef4444",
};
const SURVEY_CAT_LABELS: Record<SurveyCategory, string> = {
  storehouse: "S", chaos_stone: "C", cash_register: "$", dragon_taming: "D",
};
const SURVEY_CAT_NAMES: Record<SurveyCategory, string> = {
  storehouse: "Storehouse Blueprint", chaos_stone: "Chaos Stone", cash_register: "Cash Register", dragon_taming: "Dragon Taming",
};

const SURVEY_DEFS: SurveyDef[] = [
  // -- Storehouses tier 1 (minLevel 45) ---------------------------------------
  { id:0,  name:"Storehouse Grass Bp",    terrainId:2,  minLevel:45, minCost:0, maxCost:12, minHearts:72,  maxHearts:822,  minRate:15, maxRate:75, minTimeSec:2700, maxTimeSec:60,    category:"storehouse" },
  { id:1,  name:"Storehouse Wood Bp",     terrainId:4,  minLevel:45, minCost:0, maxCost:12, minHearts:72,  maxHearts:822,  minRate:15, maxRate:75, minTimeSec:2700, maxTimeSec:60,    category:"storehouse" },
  { id:2,  name:"Storehouse Food Bp",     terrainId:15, minLevel:45, minCost:0, maxCost:12, minHearts:90,  maxHearts:840,  minRate:15, maxRate:75, minTimeSec:2700, maxTimeSec:60,    category:"storehouse" },
  { id:3,  name:"Storehouse Iron Bp",     terrainId:3,  minLevel:45, minCost:0, maxCost:12, minHearts:78,  maxHearts:828,  minRate:15, maxRate:75, minTimeSec:2700, maxTimeSec:60,    category:"storehouse" },
  { id:4,  name:"Storehouse Magic Bp",    terrainId:7,  minLevel:45, minCost:0, maxCost:12, minHearts:81,  maxHearts:831,  minRate:15, maxRate:75, minTimeSec:2700, maxTimeSec:60,    category:"storehouse" },
  { id:5,  name:"Storehouse Stamina Bp",  terrainId:6,  minLevel:45, minCost:0, maxCost:12, minHearts:79,  maxHearts:829,  minRate:15, maxRate:75, minTimeSec:2700, maxTimeSec:60,    category:"storehouse" },
  { id:6,  name:"Storehouse Treasure Bp", terrainId:5,  minLevel:45, minCost:0, maxCost:12, minHearts:74,  maxHearts:824,  minRate:15, maxRate:75, minTimeSec:2700, maxTimeSec:60,    category:"storehouse" },
  { id:7,  name:"Storehouse Items Bp",    terrainId:1,  minLevel:45, minCost:0, maxCost:12, minHearts:74,  maxHearts:824,  minRate:15, maxRate:75, minTimeSec:2700, maxTimeSec:60,    category:"storehouse" },
  { id:8,  name:"Storehouse Eggs Bp",     terrainId:2,  minLevel:45, minCost:0, maxCost:12, minHearts:74,  maxHearts:824,  minRate:15, maxRate:75, minTimeSec:2700, maxTimeSec:60,    category:"storehouse" },
  // -- Storehouses tier 2 (minLevel 60) ---------------------------------------
  { id:11, name:"Storehouse Grass Bp T2",    terrainId:6,  minLevel:60, minCost:2, maxCost:48, minHearts:257, maxHearts:1007, minRate:15, maxRate:75, minTimeSec:2700, maxTimeSec:10800, category:"storehouse" },
  { id:12, name:"Storehouse Wood Bp T2",     terrainId:2,  minLevel:60, minCost:2, maxCost:48, minHearts:282, maxHearts:1032, minRate:15, maxRate:75, minTimeSec:2700, maxTimeSec:10800, category:"storehouse" },
  { id:13, name:"Storehouse Food Bp T2",     terrainId:4,  minLevel:60, minCost:2, maxCost:48, minHearts:260, maxHearts:1010, minRate:15, maxRate:75, minTimeSec:2700, maxTimeSec:10800, category:"storehouse" },
  { id:14, name:"Storehouse Iron Bp T2",     terrainId:15, minLevel:60, minCost:2, maxCost:48, minHearts:270, maxHearts:1020, minRate:15, maxRate:75, minTimeSec:2700, maxTimeSec:10800, category:"storehouse" },
  { id:15, name:"Storehouse Magic Bp T2",    terrainId:3,  minLevel:60, minCost:2, maxCost:48, minHearts:262, maxHearts:1012, minRate:15, maxRate:75, minTimeSec:2700, maxTimeSec:10800, category:"storehouse" },
  { id:16, name:"Storehouse Stamina Bp T2",  terrainId:7,  minLevel:60, minCost:2, maxCost:48, minHearts:247, maxHearts:997,  minRate:15, maxRate:75, minTimeSec:2700, maxTimeSec:10800, category:"storehouse" },
  { id:17, name:"Storehouse Treasure Bp T2", terrainId:1,  minLevel:60, minCost:2, maxCost:48, minHearts:287, maxHearts:1037, minRate:15, maxRate:75, minTimeSec:2700, maxTimeSec:10800, category:"storehouse" },
  { id:18, name:"Storehouse Items Bp T2",    terrainId:3,  minLevel:60, minCost:2, maxCost:48, minHearts:272, maxHearts:1022, minRate:15, maxRate:75, minTimeSec:2700, maxTimeSec:10800, category:"storehouse" },
  { id:19, name:"Storehouse Eggs Bp T2",     terrainId:1,  minLevel:60, minCost:2, maxCost:48, minHearts:247, maxHearts:997,  minRate:15, maxRate:75, minTimeSec:2700, maxTimeSec:10800, category:"storehouse" },
  // -- Storehouses tier 3 (minLevel 90) ---------------------------------------
  { id:22, name:"Storehouse Grass Bp T3",    terrainId:3,  minLevel:90, minCost:3, maxCost:80, minHearts:580, maxHearts:1330, minRate:15, maxRate:75, minTimeSec:2700, maxTimeSec:10800, category:"storehouse" },
  { id:23, name:"Storehouse Wood Bp T3",     terrainId:5,  minLevel:90, minCost:3, maxCost:80, minHearts:530, maxHearts:1280, minRate:15, maxRate:75, minTimeSec:2700, maxTimeSec:10800, category:"storehouse" },
  { id:24, name:"Storehouse Food Bp T3",     terrainId:2,  minLevel:90, minCost:3, maxCost:80, minHearts:480, maxHearts:1230, minRate:15, maxRate:75, minTimeSec:2700, maxTimeSec:10800, category:"storehouse" },
  { id:25, name:"Storehouse Iron Bp T3",     terrainId:4,  minLevel:90, minCost:3, maxCost:80, minHearts:590, maxHearts:1340, minRate:15, maxRate:75, minTimeSec:2700, maxTimeSec:10800, category:"storehouse" },
  { id:26, name:"Storehouse Magic Bp T3",    terrainId:15, minLevel:90, minCost:3, maxCost:80, minHearts:580, maxHearts:1330, minRate:15, maxRate:75, minTimeSec:2700, maxTimeSec:10800, category:"storehouse" },
  { id:27, name:"Storehouse Stamina Bp T3",  terrainId:3,  minLevel:90, minCost:3, maxCost:80, minHearts:480, maxHearts:1230, minRate:15, maxRate:75, minTimeSec:2700, maxTimeSec:10800, category:"storehouse" },
  { id:28, name:"Storehouse Treasure Bp T3", terrainId:7,  minLevel:90, minCost:3, maxCost:80, minHearts:555, maxHearts:1305, minRate:15, maxRate:75, minTimeSec:2700, maxTimeSec:10800, category:"storehouse" },
  { id:29, name:"Storehouse Items Bp T3",    terrainId:6,  minLevel:90, minCost:3, maxCost:80, minHearts:480, maxHearts:1230, minRate:15, maxRate:75, minTimeSec:2700, maxTimeSec:10800, category:"storehouse" },
  { id:30, name:"Storehouse Eggs Bp T3",     terrainId:5,  minLevel:90, minCost:3, maxCost:80, minHearts:550, maxHearts:1300, minRate:15, maxRate:75, minTimeSec:2700, maxTimeSec:10800, category:"storehouse" },
  // -- Chaos Stone -------------------------------------------------------------
  { id:10, name:"Chaos Stone T1", terrainId:-1, minLevel:45, minCost:0, maxCost:8,  minHearts:87,  maxHearts:837,  minRate:10, maxRate:75, minTimeSec:3600, maxTimeSec:60,    category:"chaos_stone" },
  { id:21, name:"Chaos Stone T2", terrainId:-1, minLevel:60, minCost:2, maxCost:24, minHearts:257, maxHearts:1007, minRate:10, maxRate:75, minTimeSec:3600, maxTimeSec:14400, category:"chaos_stone" },
  { id:32, name:"Chaos Stone T3", terrainId:-1, minLevel:90, minCost:3, maxCost:32, minHearts:515, maxHearts:1265, minRate:10, maxRate:75, minTimeSec:3600, maxTimeSec:14400, category:"chaos_stone" },
  // -- Cash Register -----------------------------------------------------------
  { id:77, name:"Cash Register (Med)",  terrainId:-1, minLevel:30,  minCost:1, maxCost:24, minHearts:240, maxHearts:990,  minRate:10, maxRate:85, minTimeSec:3600, maxTimeSec:14400, category:"cash_register" },
  { id:76, name:"Cash Register (Easy)", terrainId:-1, minLevel:120, minCost:0, maxCost:8,  minHearts:75,  maxHearts:825,  minRate:10, maxRate:80, minTimeSec:3600, maxTimeSec:14400, category:"cash_register" },
  { id:78, name:"Cash Register (Hard)", terrainId:-1, minLevel:200, minCost:2, maxCost:48, minHearts:450, maxHearts:1200, minRate:10, maxRate:90, minTimeSec:3600, maxTimeSec:14400, category:"cash_register" },
  // -- Dragon Taming -----------------------------------------------------------
  { id:80, name:"Dragon Taming (Swamp)", terrainId:6, minLevel:70,  minCost:5, maxCost:24, minHearts:425, maxHearts:1175, minRate:1, maxRate:70, minTimeSec:3600, maxTimeSec:14400, category:"dragon_taming" },
  { id:79, name:"Dragon Taming (Rock)",  terrainId:3, minLevel:80,  minCost:4, maxCost:20, minHearts:350, maxHearts:1100, minRate:1, maxRate:70, minTimeSec:3600, maxTimeSec:14400, category:"dragon_taming" },
  { id:81, name:"Dragon Taming (Snow)",  terrainId:5, minLevel:500, minCost:6, maxCost:56, minHearts:500, maxHearts:1250, minRate:1, maxRate:70, minTimeSec:3600, maxTimeSec:14400, category:"dragon_taming" },
];

type ToolType = "none" | "pen" | "draw_area" | "reclaim" | "deploy" | "move_deploy" | "road" | "chaos_setup" | "townhall";
type EraserTarget = "pen" | "draw_area" | "road" | "chaos_setup" | "deploy" | "all";
type BrushSize = 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10;
type DeploymentSize = 2 | 3 | 4 | 5 | 6;
type PaintMode = "mark" | "erase";
type ReclaimMode = "reclaim" | "restore";
type LayerKey = "levels" | "poi" | "deployments" | "reclaimed" | "grid" | "roads" | "water" | "facilities" | "chaos_setup";
type MapViewMode = "visual" | "logic";
type ChaosSetupPiece = "info_board" | "chaos_stone";
type TownHallPlacement = { x: number; y: number; level: number };

type NativeCell = {
  terrain: TerrainType;
  level: number;
};

type Tile = {
  x: number;
  y: number;
  terrain: TerrainType;
  level: number;
  nativeX: number;
  nativeY: number;
  buildable: boolean;
  fullTerrainId: number | null;
};

const DEBUG_TILES: Array<{ id: string; x: number; y: number; label: string }> = [
  { id: "t1", x: 69,  y: 70,  label: "Grass zone border" },
  { id: "t2", x: 72,  y: 13,  label: "Top swamp border" },
  { id: "t3", x: 89,  y: 70,  label: "Sand/rock border" },
  { id: "t4", x: 14,  y: 12,  label: "Lava/grass border" },
  { id: "t5", x: 135, y: 106, label: "Ground/grass border" },
  { id: "t6", x: 70,  y: 54,  label: "Rock/grass border" },
  { id: "t7", x: 79,  y: 39,  label: "Annotated ground test" },
  { id: "t8", x: 107, y: 52,  label: "Edge sand/rock" },
];

type HistoryState = {
  outlined: string[];
  reclaimed: string[];
  deployed: Array<{ k: string; c: number }>;
  roads: string[];
  penned: Array<{ k: string; c: string }>;
  chaosSetup: Array<{ k: string; piece: ChaosSetupPiece }>;
  townhallPlacement: TownHallPlacement | null;
};

type ScreenshotArea = "full-map" | "viewport";

type ScreenshotOptions = {
  area: ScreenshotArea;
  includeLevels: boolean;
  includeGrid: boolean;
  includeRoads: boolean;
  includePen: boolean;
  includeDrawArea: boolean;
  includeDeployments: boolean;
  includeReclaimed: boolean;
  includeWeeklyConquest: boolean;
  includeChaosSetup: boolean;
  includeFacilities: boolean;
  includeSurveys: boolean;
  includeTownHall: boolean;
};

const TERRAIN_COLORS: Record<TerrainType, string> = {
  grass: "#38761d",
  sand: "#e9ddb2",
  volcano: "#ea7b70",
  swamp: "#3e948b",
  rock: "#d9d9d9",
  snow: "#f3f3f3",
  ground: "#c89a00",
};

const RAW_TERRAIN_COLORS: Record<number, string> = {
  0: "#7fb4e8",
  1: TERRAIN_COLORS.ground,
  2: TERRAIN_COLORS.grass,
  3: TERRAIN_COLORS.sand,
  4: TERRAIN_COLORS.rock,
  5: TERRAIN_COLORS.volcano,
  6: TERRAIN_COLORS.snow,
  7: TERRAIN_COLORS.swamp,
  8: TERRAIN_COLORS.snow,
  9: TERRAIN_COLORS.sand,
  10: TERRAIN_COLORS.volcano,
  11: TERRAIN_COLORS.rock,
  12: TERRAIN_COLORS.swamp,
  13: TERRAIN_COLORS.grass,
  15: "#c79d3d",
};

const ROAD_COLOR = "rgba(100, 116, 139, 0.72)";
const ROAD_BORDER = "rgba(148, 163, 184, 0.95)";
function hexToRgba(hex: string, alpha: number) {
  const normalized = hex.startsWith("#") ? hex.slice(1) : hex;
  const r = parseInt(normalized.slice(0, 2), 16);
  const g = parseInt(normalized.slice(2, 4), 16);
  const b = parseInt(normalized.slice(4, 6), 16);
  return `rgba(${r},${g},${b},${alpha})`;
}
function drawRoadTexture(ctx: CanvasRenderingContext2D, px: number, py: number, size: number) {
  ctx.fillStyle = ROAD_COLOR;
  ctx.fillRect(px, py, size, size);
  ctx.strokeStyle = ROAD_BORDER;
  ctx.lineWidth = 1;
  ctx.strokeRect(px + 0.5, py + 0.5, size - 1, size - 1);

  const dashAlpha = size < 8 ? 0.2 : 0.16;
  ctx.strokeStyle = `rgba(255,255,255,${dashAlpha})`;
  ctx.lineWidth = 1;
  const dashLen = Math.max(2, Math.floor(size / 4));
  ctx.setLineDash([dashLen, dashLen]);
  ctx.beginPath();
  const lineCount = Math.max(1, Math.floor(size / 3));
  for (let i = 1; i <= lineCount; i += 1) {
    const y = py + (i * size) / (lineCount + 1);
    ctx.moveTo(px + size * 0.15, y);
    ctx.lineTo(px + size * 0.85, y);
  }
  ctx.stroke();
  ctx.setLineDash([]);
}

function getDeploymentClusterCenter(keys: Set<string>) {
  let sumX = 0;
  let sumY = 0;
  let count = 0;
  keys.forEach((key) => {
    const [x, y] = key.split(",").map(Number);
    sumX += x;
    sumY += y;
    count += 1;
  });
  return {
    x: count === 0 ? 0 : Math.round(sumX / count),
    y: count === 0 ? 0 : Math.round(sumY / count),
  };
}

function translateDeploymentKeys(
  sourceKeys: Set<string>,
  sourceCenter: { x: number; y: number },
  targetCenter: { x: number; y: number },
  cols: number,
  rows: number
) {
  const dx = targetCenter.x - sourceCenter.x;
  const dy = targetCenter.y - sourceCenter.y;
  const translated = new Set<string>();
  sourceKeys.forEach((key) => {
    const [x, y] = key.split(",").map(Number);
    const nx = x + dx;
    const ny = y + dy;
    if (nx >= 0 && ny >= 0 && nx < cols && ny < rows) {
      translated.add(keyOf(nx, ny));
    }
  });
  return translated;
}

const DEPLOY_BASE_COLOR = "#22d3ee";
const DEPLOY_FILL = hexToRgba(DEPLOY_BASE_COLOR, 0.24);
const DEPLOY_BORDER = hexToRgba(DEPLOY_BASE_COLOR, 0.98);
const TOWNHALL_BASE_SIZE = 32;
const TOWNHALL_SIZE = 4;
const MAP_CANVAS_MARGIN = 8;
const TOWNHALL_OUTLINE = "rgba(37,99,235,0.9)";
const TOWNHALL_FOOTPRINT_FILL = "rgba(56,189,248,0.18)";
const TOWNHALL_FOOTPRINT_BORDER = "rgba(14,165,233,0.95)";
const RECLAIMED_FILL = "rgba(56, 189, 248, 0.18)";
const RECLAIMED_BORDER = "rgba(14, 165, 233, 0.98)";
const RECLAIMED_STRIPE = "rgba(2, 132, 199, 0.6)";
const OUTLINE_BORDER = "#2563eb";
const PREVIEW_ORANGE = "rgba(251,146,60,0.95)";
const DEFAULT_TILE_SIZE = 5;
const BASE_TILE_SIZE = DEFAULT_TILE_SIZE;
const MIN_TILE_SIZE = 0.5;
const MOBILE_MIN_TILE_SIZE = 0.25;
const MIN_TILE_SIZE_FULLSCREEN_FILL = false; // allow deeper zoom-out in fullscreen mode
const MAX_TILE_SIZE = 24;
const PAN_SAMPLE_MS = 16.6667;
const PAN_VELOCITY_SMOOTHING = 0.22;
const MAX_PAN_VELOCITY = 36;
const CAMERA_POSITION_LERP = 0.2;
const CAMERA_ZOOM_LERP = 0.18;
const CAMERA_PAN_FRICTION = 0.82;
const STORAGE_PREFIX = "ka-world-map-v6";
const SCREENSHOT_OPTIONS_STORAGE_KEY = `${STORAGE_PREFIX}:screenshot:options`;
type MapCamera = {
  x: number;
  y: number;
  zoom: number;
  targetX: number;
  targetY: number;
  targetZoom: number;
  vx: number;
  vy: number;
};
const LAYER_LABELS: Record<LayerKey, string> = {
  levels: "Levels",
  chaos_setup: "Chaos Setup",
  facilities: "Facilities",
  poi: "POI",
  deployments: "Deployments",
  reclaimed: "Reclaimed",
  grid: "Grid",
  roads: "Roads",
  water: "Water",
};
const VISUAL_MAP_LAYERS: Record<LayerKey, boolean> = {
  levels: false,
  poi: false,
  deployments: false,
  reclaimed: false,
  grid: false,
  roads: true,
  water: true,
  facilities: true,
  chaos_setup: true,
};
const LOGIC_MAP_LAYERS: Record<LayerKey, boolean> = {
  levels: true,
  poi: true,
  deployments: true,
  reclaimed: true,
  grid: true,
  roads: true,
  water: true,
  facilities: true,
  chaos_setup: true,
};

// Map facilities: unlocked by clearing a zone with the matching level
const MAP_FACILITY_UNLOCKS: { name: string; level: number }[] = [
  { name: "Ranking Board",         level: 2 },
  { name: "Trophy Room",           level: 3 },
  { name: "Briefing Room",         level: 5 },
  { name: "Port",                  level: 7 },
  { name: "Cabin",                 level: 8 },
  { name: "Friend Post Office",    level: 10 },
  { name: "Material Shop",         level: 10 },
  { name: "Master Smithy",         level: 11 },
  { name: "Monster Farm",          level: 14 },
  { name: "Underground Arena",     level: 20 },
  { name: "Treasure Room",         level: 21 },
  { name: "Weekly Conquest Bonus", level: 22 },
  { name: "Movers",                level: 23 },
  { name: "Friends Agency",        level: 30 },
  { name: "Equipment Exchange",    level: 34 },
  { name: "Job Center",            level: 35 },
  { name: "Trading Post",          level: 40 },
  { name: "Instructor's Room",     level: 41 },
  { name: "Port",                  level: 44 },
  { name: "Monster Fusion Lab",    level: 45 },
  { name: "Kairo Room",            level: 58 },
  { name: "Legendary Cave",        level: 120 },
  { name: "Date Spot",             level: 135 },
];


const NATIVE_MAP: NativeCell[][] = [
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

// Precompute which facility names belong to each zone (nx,ny).
// When multiple zones share the same mapUnlock level, facilities are distributed
// one-to-one across those zones (sorted left-to-right by nx, then ny).
const ZONE_FACILITY_NAMES: Map<string, string[]> = (() => {
  const zonesByLevel = new Map<number, { nx: number; ny: number }[]>();
  for (let ny = 0; ny < 10; ny++) {
    for (let nx = 0; nx < 10; nx++) {
      const lv = NATIVE_MAP[ny]?.[nx]?.level;
      if (lv == null) continue;
      if (!zonesByLevel.has(lv)) zonesByLevel.set(lv, []);
      zonesByLevel.get(lv)!.push({ nx, ny });
    }
  }
  const result = new Map<string, string[]>();
  // Group facilities by level, keeping insertion order
  const facsByLevel = new Map<number, string[]>();
  for (const f of MAP_FACILITY_UNLOCKS) {
    if (!facsByLevel.has(f.level)) facsByLevel.set(f.level, []);
    facsByLevel.get(f.level)!.push(f.name);
  }
  for (const [lv, names] of facsByLevel) {
    const zones = (zonesByLevel.get(lv) ?? []).slice().sort((a, b) => a.nx - b.nx || a.ny - b.ny);
    names.forEach((name, i) => {
      const zone = zones[i % zones.length];
      if (!zone) return;
      const key = `${zone.nx},${zone.ny}`;
      if (!result.has(key)) result.set(key, []);
      result.get(key)!.push(name);
    });
  }
  return result;
})();

const FULL_MASK = [
  "0000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000",
  "0000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000110000000000000000",
  "0000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000001110000000000000",
  "0000000000000000000000000000000000000000000000000000000111111000000000000000000000000000000000000000000000000000000000000000000000000000000100011110000000000000",
  "0000000000000000000000000000000000000000000000000000001111111100000000000000000000000000000000000000000000000000000000000000000000000000001111111001000000000000",
  "0000000000000000000000000000000000000000000000000000011111111100000000000000111000000000000000000000000000000000000000000000000000000000001111111101000000000000",
  "0000000000000000100000000000000000000000000000000000001111111110000000000011111000000000000000000000000000000000000000000000000000000000000111111111100000000000",
  "0000000000000000110100000000011000111100000000000000000111111111000001101111111011010000000000000000000000000000000000000000000000000000001111111111100000000000",
  "0000000000000101111100000001111111111000000000000000001111111111100111111111111111111100000000000000000000000000000000000000000000000001111111111111111000000000",
  "0000000000001111111000001111111111111111000000000000001111111111111111100011111111111110000000000000000000000000000000000000000000111101111111111111111000000000",
  "0000000000001111111111101111111111111111000000001110111111111111111111111111111111111111000000000000000000000000000000000000000000011111111111111111111100000000",
  "0000000010000111111111111111111111111111100000111110011111111111111111111111111111111111100000000000000000000000000000000000000001011111111111111111111110000000",
  "0000000010001111111111111111111111111111100000111111111111111111111111111111111111111111111000000000000000000000000000000000000011111111111111111111111110000000",
  "0000000110000111111111111111111111111111000011111111111111111111111111111111111111111111111100000000000000000000000000000000000011111111111111111111111100000000",
  "0000001100001111111111111111111111111011111111111111111111111111111111111111111111111111111100000000000000000000000000000000000001111111111111111111111100000000",
  "0000000000000111111111111111111111111011111111111111111111111111111111111111111111111111111100000000000000000110000000000000000011111111111111111111111110000000",
  "0000000000100111111111111111111111111111111111111111111111111111111111111111111111111111111110000000000000100110110000000000000011111111111111111111111110000000",
  "0000000000111111111111111111111111111111111111111111111111111111111111111111111111111111111110000000000001111111111000000000000011111111111111111111111111000000",
  "0000000000111111111111111111111111111111111111111111111111111111111111111111111111111111111111010000000111111111100000000000000011111111111111111111111111100000",
  "0000000000111111111111111111111111111111111111111111111111111111111111111111111111111111111111110001111111111111111100000000000011111111111111111111111111100000",
  "0000000000111111111111111111111111111111111111111111111111111111111111111111111111111111111111110100111111111111111100011111010111111111111111111111111111100000",
  "0000000000001111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111101111111111111111111111111111111111111110000",
  "0000000000001111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111101111111111111111111111111111111111111110000",
  "0000000000000111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111110000",
  "0000000000000011111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111100000",
  "0000000000000111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111100000",
  "0000000000001111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111100000000",
  "0000000000011111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111100000000",
  "0000000000011111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111100000000",
  "0000000000111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111000000",
  "0000000001011111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111110000",
  "0000000001011111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111110000",
  "0000000000011111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111110000",
  "0000000000111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111100000",
  "0000000001111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111100000",
  "0000000000111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111100000",
  "0000000001111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111110000",
  "0000000001111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111110000",
  "0000000001111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111100000",
  "0000011011111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111110000",
  "0000111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111000",
  "0011111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111000",
  "0011111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111110000",
  "0011111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111101100",
  "0001111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111000000",
  "0001111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111110000000",
  "0000011111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111110000000",
  "0000001111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111100000000",
  "0000001011111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111000000",
  "0000000011111110100111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111100000",
  "0000000011111110011111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111100000",
  "0000000001011111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111100000",
  "0000000000001011111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111110000",
  "0000000000000011111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111100000",
  "0000000000000001111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111110000",
  "0000000000000000111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111110000",
  "0000000000000001111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111110000",
  "0000000000000001111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111110000",
  "0000000000000001111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111000",
  "0000000000000011111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111110000",
  "0000000000000111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111100000",
  "0000000000001111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111100000",
  "0000000000000111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111110000",
  "0000000000000111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111100000",
  "0000000000000111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111110000000",
  "0000000000000111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111110000000",
  "0000000000000111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111000000000",
  "0000000000000111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111000000000",
  "0000000000011111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111100000000",
  "0000000000011111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111000000000",
  "0000000000001111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111000000000",
  "0000000000011111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111100000000",
  "0000000000111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111100000000",
  "0000000000111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111000000000",
  "0000000000111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111000000000",
  "0000000011111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111100000000000",
  "0000000011111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111100000000000",
  "0000000001111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111110000000000",
  "0000000001111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111000000000",
  "0000000001111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111000000000",
  "0000000000111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111100000000",
  "0000000000111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111110000000",
  "0000000000011111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111110000000",
  "0000000001111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111100000000",
  "0000000011111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111000000000",
  "0000000011111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111100000000",
  "0000000111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111110000000",
  "0000000011111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111110000000",
  "0000000001111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111110000000",
  "0000010001111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111100000000",
  "0000011001111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111110000000",
  "0000111011111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111100000000",
  "0000000111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111100000000",
  "0000001111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111110000000",
  "0011101111111111111111111111111111111111111111111110111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111100000000",
  "0001111111111111111111111111111111111111111111110000111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111000000000",
  "0011111111111111111111111111111111111111111111000000111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111100000000000",
  "0111111111111111111111111111111111111111111110000000011111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111110000000000",
  "0011111111111111111111111111111111111111111100000000001111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111000000000",
  "0011111111111111111111111111111111111111111100000000000111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111110000000000",
  "0111111111111111111111111111111111111111110000000000000010101111111111111111111111111111111111111111111111111111111111111111111111111111111111111111110000000000",
  "0111111111111111111111111111111111111111110000000000000010001111111111111111111111111111111111111111111111111111111111111111111111111111111111111111110000000000",
  "0011111111111111111111111111111111111111100000000000000000001111111111111111111111111111111111111111111111111111111111111111111111111111111111111111110000000000",
  "0001111111111111111111111111111111111111100000000000000000001111111111111111111111111111111111111111111111111111111111111111111111111111111111111111110000000000",
  "0011111111111111111111111111111111111111110000000000000000111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111100000000000",
  "0011111111111111111111111111111111111111111111100000000000101111111111111111111111111111111111111111111111111111111111111111111111111111111110111111111000000000",
  "0001111111111111111111111111111111111111111111100000000000001111111111111111111111111111111111111111111111111111111111111111111111111111111110011111111000000000",
  "0001111111111111111111111111111111111111111111100000000000000011111111111111111111111111111111111111111111111111111111111111111111111111111110111111111100000000",
  "0001111111111111111111111111111111111111111111100000000000000111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111100000000",
  "0001111111111111111111111111111111111111111111100000000000000111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111000000000",
  "0000010111111111111111111111111111111111111111111000000000011111111111111111111111111111111111111111111111111111111111111111111111111111111111111111110000000000",
  "0000000111111111111111111111111111111111111111111111000001001111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111000000000",
  "0000000111111111111111111111111111111111111111111110000111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111110000000000",
  "0000000111111111111111111111111111111111111111111100001111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111110000000000",
  "0000000011111111111111111111111111111111111111111111101111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111110000000000",
  "0000000001111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111000000000",
  "0000001011111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111100000000000",
  "0000011111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111000000000000",
  "0000011111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111100000000000",
  "0000000011111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111110000000000",
  "0000000000011111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111110000000000",
  "0000000000011111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111110000000000",
  "0000000011111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111000000",
  "0000000111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111000000",
  "0000000001111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111000000",
  "0000001101111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111000000",
  "0000001111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111000000",
  "0000000111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111000000",
  "0000000011111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111000000",
  "0000000011111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111000000",
  "0000000001111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111000000",
  "0000000000011111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111000000",
  "0000000000111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111000000",
  "0000000000011111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111000000",
  "0000000000001111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111000000",
  "0000000000001111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111000000",
  "0000000000011111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111000000",
  "0000000000111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111000000",
  "0000000000011111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111000000",
  "0000000011111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111000000",
  "0000000000111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111100111111111111111111111111111111111111111111111111111000000",
  "0000000000111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111000111111111111111111111111111111111111111111111111111000000",
  "0000000000011111111111111111111111111111111111111111111111111111111111111111111111111111111111111100000011111111111111111111111111111111111111111111111111000000",
  "0000000000000111111111111111111111111111111111111111111111111111111111111111111111111111111111111000000011111111111111111111111111111111111111111111111111000000",
  "0000000000001111111111111111111111111111111111111111111111111111111111111111111111111111111111110000000001111111111111111111111111111111111111111111111111000000",
  "0000000000000111111111111111111111111111111111111111111111111111111111111111111111111111111111100100000000011111011111111111111111111111111111111111111111000000",
  "0000000000000111111111111111111111111111111111111111111111111111111111111111111111111111111111111100000000011110011111111111111111111111111111111111111111000000",
  "0000000000000011111111111111111111111111111111111111111111111111111111111111111111111111111111111000000000000111111111111111111111111111111111111111111111000000",
  "0000000000000011111111111111111111111111111111111111111111111111111111111111111111111111111111110000000000000101111111111111111111111111111111111111111111000000",
  "0000000000000011111111111111111111111111111111111111111111111111111111111111111111111111111110000000000000000000111111111111111111111111111111111111111111000000",
  "0000000000000001111111111111111111111111111111111111111111111111111111111111111111111111111110000110000000000000001111111011111111111111111111111111111111000000",
  "0000000000000001111111111111111111111111111111111110111111101110111111111111111111111111111110000000000000000000000100000111111111111111111111111111111111000000",
  "0000000000000000000010111111111111111111100111111000111100100000011111111111001111111111111100000000000000000000000000000111111111111111111111111111111111000000",
  "0000000000000000000000111111111111111111111111111100100000110000011111111110011111111111111110000000000000000000000000000111111111111111111111111111111111000000",
  "0000000000000000000000011111100001011111111111111101000000000000001111111100001110111111111110000000000000000000000000000011111111111111111000000000000000000000",
  "0000000000000000000000000011000000001001000001110001000000000000000110001000000010001111111000000000000000000000000000000000001001110011000000000000000000000000",
  "0000000000000000000000000000000000000001100000000000000000000000000000000000000000000111110000000000000000000000000000000000000000100000000000000000000000000000",
  "0000000000000000000000000000000000000000000000000000000000000000000000000000000000000001100000000000000000000000000000000000000000000000000000000000000000000000",
  "0000000000000000000000000000000000000000000000000000000000000000000000000000000000000001000000000000000000000000000000000000000000000000000000000000000000000000",
  "0000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000",
];



function isTouchDevice() {
  if (typeof window === "undefined") return false;
  return (
    window.matchMedia("(hover: none)").matches ||
    "ontouchstart" in window ||
    (typeof navigator !== "undefined" && navigator.maxTouchPoints > 0)
  );
}

function getNativeIndex(position: number, size: number, buckets: number) {
  return Math.min(buckets - 1, Math.floor((position * buckets) / size));
}

function keyOf(x: number, y: number) {
  return `${x},${y}`;
}

function arrayToSet(values: unknown): Set<string> {
  if (!Array.isArray(values)) return new Set();
  return new Set(values.filter((v): v is string => typeof v === "string"));
}

function setToSortedArray(values: Set<string>) {
  return Array.from(values).sort();
}

function encodeState(
  outlinedTiles: Set<string>,
  reclaimedTiles: Set<string>,
  deployedTiles: Map<string, number>,
  roadTiles: Set<string>,
  penTiles: Map<string, string>,
  chaosSetupTiles: Map<string, ChaosSetupPiece>
) {
  return `KAWM3:${btoa(JSON.stringify({
    o: setToSortedArray(outlinedTiles),
    r: setToSortedArray(reclaimedTiles),
    d: Array.from(deployedTiles.entries()).map(([k, c]) => ({ k, c })),
    roads: setToSortedArray(roadTiles),
    p: Array.from(penTiles.entries()).map(([k, c]) => ({ k, c })),
    cs: Array.from(chaosSetupTiles.entries()).map(([k, piece]) => ({ k, piece })),
  }))}`;
}

function decodeState(text: string) {
  const raw = text.trim();
  let parsed: any;
  if (raw.startsWith("KAWM3:")) parsed = JSON.parse(atob(raw.slice(6)));
  else if (raw.startsWith("KAWM2:")) parsed = JSON.parse(atob(raw.slice(6)));
  else if (raw.startsWith("KAWM1:")) parsed = JSON.parse(atob(raw.slice(6)));
  else parsed = JSON.parse(raw);

  let deployed: Array<{ k: string; c: number }> = [];
  if (Array.isArray(parsed?.d)) {
    if (parsed.d.every((entry: any) => typeof entry === "string")) {
      deployed = (parsed.d as string[]).map((k) => ({ k, c: 1 }));
    } else {
      deployed = (parsed.d as any[])
        .filter((e) => e && typeof e.k === "string" && typeof e.c === "number")
        .map((e) => ({ k: e.k, c: e.c }));
    }
  }

  return {
    outlined: arrayToSet(parsed?.o),
    reclaimed: arrayToSet(parsed?.r),
    deployed: new Map(deployed.map((e) => [e.k, e.c])),
    roads: arrayToSet(parsed?.roads),
    penned: Array.isArray(parsed?.p)
      ? (parsed.p as any[]).filter((e) => e && typeof e.k === "string" && typeof e.c === "string")
      : [],
    chaosSetup: Array.isArray(parsed?.cs)
      ? (parsed.cs as any[]).filter((e) => e && typeof e.k === "string" && (e.piece === "info_board" || e.piece === "chaos_stone"))
      : [],
  };
}

function buildTiles() {
  const rows = FULL_MASK.length;
  const cols = FULL_MASK[0]?.length ?? 0;
  const nativeRows = NATIVE_MAP.length;
  const nativeCols = NATIVE_MAP[0]?.length ?? 0;

  const grid = new Map<string, Tile>();
  const terrainCounts: Record<TerrainType, number> = {
    grass: 0,
    sand: 0,
    volcano: 0,
    swamp: 0,
    rock: 0,
    snow: 0,
    ground: 0,
  };

  for (let y = 0; y < rows; y += 1) {
    const row = FULL_MASK[y];
    for (let x = 0; x < cols; x += 1) {
      const nativeX = getNativeIndex(x, cols, nativeCols);
      const nativeY = getNativeIndex(y, rows, nativeRows);
      const native = NATIVE_MAP[nativeY][nativeX];
      const buildable = row[x] === "1";
      const fullTerrainCode = FULL_TERRAIN_MAP[y]?.[x];
      const tileTerrain = Number.isFinite(fullTerrainCode)
        ? mapTerrainCodeToType(fullTerrainCode) ?? native.terrain
        : native.terrain;
      const tile: Tile = {
        x,
        y,
        terrain: tileTerrain,
        level: native.level,
        nativeX,
        nativeY,
        buildable,
        fullTerrainId: Number.isFinite(fullTerrainCode) ? fullTerrainCode : null,
      };
      if (buildable) terrainCounts[tile.terrain] += 1;
      grid.set(keyOf(x, y), tile);
    }
  }

  return { grid, rows, cols, terrainCounts };
}

function getCenteredSquareCoordinates(centerX: number, centerY: number, size: number, cols: number, rows: number) {
  const coords: Array<[number, number]> = [];
  const left = Math.floor((size - 1) / 2);
  const right = size - 1 - left;
  const top = Math.floor((size - 1) / 2);
  const bottom = size - 1 - top;

  for (let y = Math.max(0, centerY - top); y <= Math.min(rows - 1, centerY + bottom); y += 1) {
    for (let x = Math.max(0, centerX - left); x <= Math.min(cols - 1, centerX + right); x += 1) {
      coords.push([x, y]);
    }
  }
  return coords;
}

function getDiamondCoordinates(centerX: number, centerY: number, radius: number, cols: number, rows: number) {
  const coords: Array<[number, number]> = [];
  for (let y = Math.max(0, centerY - radius); y <= Math.min(rows - 1, centerY + radius); y += 1) {
    for (let x = Math.max(0, centerX - radius); x <= Math.min(cols - 1, centerX + radius); x += 1) {
      const dx = Math.abs(x - centerX);
      const dy = Math.abs(y - centerY);
      if (dx + dy <= radius) coords.push([x, y]);
    }
  }
  return coords;
}

function getTownHallCoverageSize(level: number) {
  const tier = Math.max(0, Math.floor(level / 10));
  return TOWNHALL_BASE_SIZE + tier * 4;
}

function getTownHallFootprintCoords(centerX: number, centerY: number, cols: number, rows: number) {
  return getCenteredSquareCoordinates(centerX, centerY, TOWNHALL_SIZE, cols, rows);
}

function getCenteredSquareBounds(centerX: number, centerY: number, size: number, cols: number, rows: number) {
  const left = Math.floor((size - 1) / 2);
  const right = size - 1 - left;
  const top = Math.floor((size - 1) / 2);
  const bottom = size - 1 - top;
  return {
    minX: Math.max(0, centerX - left),
    maxX: Math.min(cols - 1, centerX + right),
    minY: Math.max(0, centerY - top),
    maxY: Math.min(rows - 1, centerY + bottom),
  };
}

function getTownHallCoverageBounds(centerX: number, centerY: number, level: number, cols: number, rows: number) {
  return getCenteredSquareBounds(centerX, centerY, getTownHallCoverageSize(level), cols, rows);
}

function snapshot(
  outlined: Set<string>,
  reclaimed: Set<string>,
  deployed: Map<string, number>,
  roads: Set<string>,
  penned: Map<string, string>,
  chaosSetup: Map<string, ChaosSetupPiece>,
  townhallPlacement: TownHallPlacement | null
): HistoryState {
  return {
    outlined: setToSortedArray(outlined),
    reclaimed: setToSortedArray(reclaimed),
    deployed: Array.from(deployed.entries()).map(([k, c]) => ({ k, c })),
    roads: setToSortedArray(roads),
    penned: Array.from(penned.entries()).map(([k, c]) => ({ k, c })),
    chaosSetup: Array.from(chaosSetup.entries()).map(([k, piece]) => ({ k, piece })),
    townhallPlacement,
  };
}

function restoreSnapshot(state: HistoryState) {
  return {
    outlined: new Set(state.outlined),
    reclaimed: new Set(state.reclaimed),
    deployed: new Map((state.deployed ?? []).map((e) => [e.k, e.c])),
    roads: new Set(state.roads),
    penned: new Map((state.penned ?? []).map((e) => [e.k, e.c])),
    chaosSetup: new Map((state.chaosSetup ?? []).map((e) => [e.k, e.piece])),
    townhallPlacement: state.townhallPlacement ?? null,
  };
}

function toggleTool(current: ToolType, next: ToolType) {
  return current === next ? "none" : next;
}

function getChaosSetupFootprint(x: number, y: number, piece: ChaosSetupPiece, cols: number, rows: number): string[] {
  if (piece === "info_board") {
    if (x < 0 || y < 0 || x >= cols || y >= rows) return [];
    return [keyOf(x, y)];
  }
  if (x < 0 || y < 0 || x + 1 >= cols || y + 1 >= rows) return [];
  return [
    keyOf(x, y),
    keyOf(x + 1, y),
    keyOf(x, y + 1),
    keyOf(x + 1, y + 1),
  ];
}

function getChaosSetupFootprintCoords(x: number, y: number, piece: ChaosSetupPiece, cols: number, rows: number): Array<[number, number]> {
  return getChaosSetupFootprint(x, y, piece, cols, rows).map((key) => {
    const [px, py] = key.split(",").map(Number);
    return [px, py];
  });
}

const BRUSH_COLORS = [
  { label: "Blue (POI)", value: "#2563eb" },
  { label: "Purple (Road)", value: "#d946ef" },
  { label: "Cyan (Deploy)", value: "#22d3ee" },
  { label: "Red", value: "#ef4444" },
  { label: "Orange", value: "#fb923c" },
  { label: "Green", value: "#22c55e" },
  { label: "Yellow", value: "#facc15" },
  { label: "White", value: "#ffffff" },
  { label: "Pink", value: "#f472b6" },
];

const BubbleIconPen = () => (
  <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M17 3a2.828 2.828 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z"/>
  </svg>
);
const BubbleIconDrawArea = () => (
  <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <rect x="3" y="3" width="18" height="18" rx="2"/>
    <path d="M9 3v18M15 3v18M3 9h18M3 15h18" strokeOpacity="0.5" strokeWidth="1"/>
  </svg>
);
const BubbleIconEraser = () => (
  <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M20 20H7L3 16l11-11 6 6-3 3"/>
    <path d="M6.0 20l3-3"/>
  </svg>
);
const BubbleIconRoad = () => (
  <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M4 21 8 3"/>
    <path d="M20 21 16 3"/>
    <path d="M12 8v2"/>
    <path d="M12 14v2"/>
  </svg>
);
const BubbleIconTownHall = () => (
  <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M4 10l8-6 8 6v10H4V10z" />
    <path d="M8 22V12h8v10" />
    <path d="M9 15h6" />
    <path d="M12 18v-4" />
  </svg>
);
const BubbleIconCursor = () => (
  <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M4 4l6 18 3-7 7-3L4 4z"/>
  </svg>
);

export default function WorldMapPage() {
  const [touchMode, setTouchMode] = useState(false);
  const [touchEditMode, setTouchEditMode] = useState(false);
  const [mobileToolbarOpen, setMobileToolbarOpen] = useState(false);
  const [fullscreenToolbarOpen, setFullscreenToolbarOpen] = useState(false);
  const [activeTool, setActiveTool] = useState<ToolType>("none");
  const [brushSize, setBrushSize] = useState<BrushSize>(1);
  const [penBrushSize, setPenBrushSize] = useState<BrushSize>(1);
  const [eraserSize, setEraserSize] = useState<BrushSize>(1);
  const [paintMode, setPaintMode] = useState<PaintMode>("mark");
  const [reclaimMode, setReclaimMode] = useState<ReclaimMode>("reclaim");
  const [deploymentSize, setDeploymentSize] = useState<DeploymentSize>(2);
  const [tileSize, setTileSize] = useState(DEFAULT_TILE_SIZE);
  const [selectedTile, setSelectedTile] = useState<Tile | null>(null);
  const [hoveredTile, setHoveredTile] = useState<Tile | null>(null);
  const [outlinedTiles, setOutlinedTiles] = useState<Set<string>>(() => new Set());
  const [penTiles, setPenTiles] = useState<Map<string, string>>(() => new Map());
  const [chaosSetupTiles, setChaosSetupTiles] = useState<Map<string, ChaosSetupPiece>>(() => new Map());
  const [reclaimedTiles, setReclaimedTiles] = useState<Set<string>>(() => new Set());
  const [deployedTiles, setDeployedTiles] = useState<Map<string, number>>(() => new Map());
  const [roadTiles, setRoadTiles] = useState<Set<string>>(() => new Set());
  const [isPainting, setIsPainting] = useState(false);
  const [mapViewMode, setMapViewMode] = useState<MapViewMode>("visual");

  function incrementDeploymentCount(next: Map<string, number>, key: string) {
    next.set(key, (next.get(key) ?? 0) + 1);
  }

  function decrementDeploymentCount(next: Map<string, number>, key: string) {
    const current = next.get(key) ?? 0;
    if (current <= 1) next.delete(key);
    else next.set(key, current - 1);
  }

  function getDeploymentCount(key: string) {
    return deployedTilesRef.current.get(key) ?? 0;
  }

  function hasDeployment(key: string) {
    return getDeploymentCount(key) > 0;
  }

  const [notice, setNotice] = useState<string | null>(null);
  const [cleanMode, setCleanMode] = useState(false);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [isFullscreenFallback, setIsFullscreenFallback] = useState(false);
  const [showMinimap, setShowMinimap] = useState(true);
  const [minimapCollapsed, setMinimapCollapsed] = useState(false);
  const [minimapBubblePos, setMinimapBubblePos] = useState({ x: 12, y: 12 });
  const [mapCamera, setMapCamera] = useState({ left: 0, top: 0, zoom: 1 });
  const [mapScroll, setMapScroll] = useState({ left: 0, top: 0, width: 0, height: 0, viewportWidth: 0, viewportHeight: 0 });
  const [penColor, setPenColor] = useState(BRUSH_COLORS[0].value);
  const [drawAreaColor, setDrawAreaColor] = useState(BRUSH_COLORS[0].value);
  const [deployColor, setDeployColor] = useState(BRUSH_COLORS[2].value);
  const [townhallLevel, setTownhallLevel] = useState(1);
  const [townhallPlacement, setTownhallPlacement] = useState<TownHallPlacement | null>(null);
  const [recentColors, setRecentColors] = useState<string[]>([]);
  const [chaosSetupPiece, setChaosSetupPiece] = useState<ChaosSetupPiece>("info_board");
  const [layers, setLayers] = useState<Record<LayerKey, boolean>>(() => ({ ...VISUAL_MAP_LAYERS }));
  const [historyPast, setHistoryPast] = useState<HistoryState[]>([]);
  const [historyFuture, setHistoryFuture] = useState<HistoryState[]>([]);
  const [isPanning, setIsPanning] = useState(false);
  const [roadSnapPreview, setRoadSnapPreview] = useState<Set<string>>(new Set());
  const [drawBubbleOpen, setDrawBubbleOpen] = useState(false);
  const [drawBubblePos, setDrawBubblePos] = useState({ x: 16, y: 220 });
  const [lineAssist, setLineAssist] = useState(false);
  const [eraserTarget, setEraserTarget] = useState<EraserTarget>("pen");
  const [showSurveys, setShowSurveys] = useState(false);
  const [visibleSurveyCats, setVisibleSurveyCats] = useState<Set<SurveyCategory>>(
    new Set(["storehouse", "chaos_stone", "cash_register", "dragon_taming"])
  );
  const [screenshotDialogOpen, setScreenshotDialogOpen] = useState(false);
  const [screenshotStatus, setScreenshotStatus] = useState<null | "working">(null);
  const [screenshotPreviewUrl, setScreenshotPreviewUrl] = useState<string | null>(null);
  const [screenshotPreviewFileName, setScreenshotPreviewFileName] = useState<string | null>(null);
  const [screenshotOptions, setScreenshotOptions] = useState<ScreenshotOptions>({
    area: "full-map",
    includeLevels: false,
    includeGrid: false,
    includeRoads: true,
    includePen: true,
    includeDrawArea: true,
    includeDeployments: true,
    includeReclaimed: true,
    includeWeeklyConquest: false,
    includeChaosSetup: true,
    includeFacilities: false,
    includeSurveys: false,
    includeTownHall: true,
  });
  const [surveyTooltip, setSurveyTooltip] = useState<{ x: number; y: number; items: SurveyDef[] } | null>(null);
  const [layersDropdownOpen, setLayersDropdownOpen] = useState(false);
  const [surveysDropdownOpen, setSurveysDropdownOpen] = useState(false);
  const [floatingLayersOpen, setFloatingLayersOpen] = useState(!isTouchDevice());
  const [floatingToolsOpen, setFloatingToolsOpen] = useState(false);
  const [moveDeployPreviewKeys, setMoveDeployPreviewKeys] = useState<Set<string>>(() => new Set());

  const outlinedTilesRef = useRef(outlinedTiles);
  const penTilesRef = useRef(penTiles);
  const chaosSetupTilesRef = useRef(chaosSetupTiles);
  const reclaimedTilesRef = useRef(reclaimedTiles);
  const deployedTilesRef = useRef(deployedTiles);
  const roadTilesRef = useRef(roadTiles);

  const viewportRef = useRef<HTMLDivElement>(null);
  const mapGridRef = useRef<HTMLDivElement>(null);
  const moveDeploySourceRef = useRef<{ sourceKeys: Set<string>; sourceCenter: { x: number; y: number } } | null>(null);
  const moveDeployPreviewRef = useRef<Set<string>>(new Set());
  const panStartRef = useRef<{ x: number; y: number; left: number; top: number; source: "pointer" | "mouse"; pointerId?: number } | null>(null);
  const cameraRef = useRef<MapCamera>({ x: 0, y: 0, zoom: 1, targetX: 0, targetY: 0, targetZoom: 1, vx: 0, vy: 0 });
  const cameraAnimationRef = useRef<number | null>(null);
  const cameraLastFrameTimeRef = useRef<number | null>(null);
  const renderRafRef = useRef<number | null>(null);
  const uiSyncRafRef = useRef<number | null>(null);
  const baseTerrainCacheRef = useRef<{ canvas: HTMLCanvasElement; tileSize: number; water: boolean } | null>(null);
  const panVelocityRef = useRef({ lastX: 0, lastY: 0, lastTime: 0, vx: 0, vy: 0 });
  const lastPaintedKeysRef = useRef<Set<string>>(new Set());
  const lastTileRef = useRef<Tile | null>(null);
  const hoveredTileRef = useRef<Tile | null>(null);
  const previewKeysRef = useRef<Set<string>>(new Set());
  const townhallPreviewBoundsRef = useRef<ReturnType<typeof getTownHallCoverageBounds> | null>(null);
  const lineStartTileRef = useRef<Tile | null>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const minimapCanvasRef = useRef<HTMLCanvasElement>(null);
  const activePaintPointersRef = useRef<Set<number>>(new Set());
  const roadSnapStartRef = useRef<{ x: number; y: number } | null>(null);
  const roadSnapPreviewRef = useRef<Set<string>>(new Set());
  const activeToolRef = useRef(activeTool);
  const paintModeRef = useRef(paintMode);
  const perfDebugRef = useRef(false);
  const drawBubbleDragRef = useRef<{ startX: number; startY: number; startPosX: number; startPosY: number } | null>(null);
  const minimapBubbleDragRef = useRef<{ startX: number; startY: number; startPosX: number; startPosY: number } | null>(null);
  const spaceDownRef = useRef(false);
  const shiftDownRef = useRef(false);
  const pendingPointerRef = useRef<{ x: number; y: number; pointerId: number } | null>(null);
  const rafIdRef = useRef<number | null>(null);

  const fullscreenContainerRef = useRef<HTMLDivElement>(null);

  function markPerf(label: string) {
    if (!perfDebugRef.current || typeof window === "undefined") return;
    const startedAt = performance.now();
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        console.info(`[world-map perf] ${label}: ${Math.round(performance.now() - startedAt)}ms to next paint`);
      });
    });
  }

  function isValidScreenshotOptions(value: unknown): value is ScreenshotOptions {
    if (!value || typeof value !== "object") return false;
    const candidate = value as Record<string, unknown>;
    return (candidate.area === "full-map" || candidate.area === "viewport")
      && typeof candidate.includeLevels === "boolean"
      && typeof candidate.includeGrid === "boolean"
      && typeof candidate.includeRoads === "boolean"
      && typeof candidate.includePen === "boolean"
      && typeof candidate.includeDrawArea === "boolean"
      && typeof candidate.includeDeployments === "boolean"
      && typeof candidate.includeReclaimed === "boolean"
      && typeof candidate.includeWeeklyConquest === "boolean"
      && typeof candidate.includeChaosSetup === "boolean"
      && typeof candidate.includeFacilities === "boolean"
      && typeof candidate.includeSurveys === "boolean"
      && typeof candidate.includeTownHall === "boolean";
  }

  function getSavedScreenshotOptions(): ScreenshotOptions | null {
    if (typeof window === "undefined") return null;
    const raw = window.localStorage.getItem(SCREENSHOT_OPTIONS_STORAGE_KEY);
    if (!raw) return null;
    try {
      const parsed = JSON.parse(raw) as unknown;
      return isValidScreenshotOptions(parsed) ? parsed : null;
    } catch {
      return null;
    }
  }

  function clearScreenshotPreview() {
    if (screenshotPreviewUrl) {
      URL.revokeObjectURL(screenshotPreviewUrl);
    }
    setScreenshotPreviewUrl(null);
    setScreenshotPreviewFileName(null);
  }

  function getScreenshotFileName(options: ScreenshotOptions) {
    return cleanMode
      ? `world-map-${options.area}-clean.png`
      : `world-map-${options.area}.png`;
  }

  function getDefaultScreenshotOptions(): ScreenshotOptions {
    return {
      area: "full-map",
      includeLevels: layers.levels,
      includeGrid: layers.grid,
      includeRoads: layers.roads,
      includePen: layers.poi,
      includeDrawArea: layers.poi,
      includeDeployments: layers.deployments,
      includeReclaimed: layers.reclaimed,
      includeWeeklyConquest: false,
      includeChaosSetup: layers.chaos_setup,
      includeFacilities: layers.facilities,
      includeSurveys: showSurveys,
      includeTownHall: !!townhallPlacement,
    };
  }

  function applyScreenshotPreset(preset: "shareable" | "planner" | "current") {
    if (preset === "current") {
      setScreenshotOptions(getDefaultScreenshotOptions());
      return;
    }
    if (preset === "shareable") {
      setScreenshotOptions({
        area: "full-map",
        includeLevels: false,
        includeGrid: false,
        includeRoads: false,
        includePen: false,
        includeDrawArea: false,
        includeDeployments: false,
        includeReclaimed: false,
        includeWeeklyConquest: false,
        includeChaosSetup: false,
        includeFacilities: false,
        includeSurveys: false,
        includeTownHall: false,
      });
      return;
    }
    setScreenshotOptions({
      area: "full-map",
      includeLevels: layers.levels,
      includeGrid: layers.grid,
      includeRoads: true,
      includePen: true,
      includeDrawArea: true,
      includeDeployments: true,
      includeReclaimed: true,
      includeWeeklyConquest: false,
      includeChaosSetup: layers.chaos_setup,
      includeFacilities: layers.facilities,
      includeSurveys: showSurveys,
      includeTownHall: !!townhallPlacement,
    });
  }

  function openScreenshotDialog() {
    clearScreenshotPreview();
    setScreenshotOptions(getSavedScreenshotOptions() ?? getDefaultScreenshotOptions());
    setScreenshotDialogOpen(true);
  }

  useEffect(() => {
    if (typeof window === "undefined") return;
    window.localStorage.setItem(SCREENSHOT_OPTIONS_STORAGE_KEY, JSON.stringify(screenshotOptions));
  }, [screenshotOptions]);

  useEffect(() => {
    return () => {
      if (screenshotPreviewUrl) URL.revokeObjectURL(screenshotPreviewUrl);
    };
  }, [screenshotPreviewUrl]);

  useEffect(() => {
    if (!screenshotDialogOpen) return;
    if (!screenshotPreviewUrl) return;
    clearScreenshotPreview();
  }, [screenshotOptions, screenshotDialogOpen]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const params = new URLSearchParams(window.location.search);
    const enabled = params.has("perf") || window.localStorage.getItem("ka-world-map-perf") === "1";
    perfDebugRef.current = enabled;
    if (!enabled) return;
    console.info("[world-map perf] enabled. Use ?perf=1 or localStorage ka-world-map-perf=1.");
    if (typeof PerformanceObserver === "undefined") return;
    let observer: PerformanceObserver | null = null;
    try {
      observer = new PerformanceObserver((list) => {
        list.getEntries().forEach((entry) => {
          console.warn(`[world-map perf] long task: ${Math.round(entry.duration)}ms`, entry);
        });
      });
      observer.observe({ entryTypes: ["longtask"] });
    } catch {
      observer = null;
    }
    return () => observer?.disconnect();
  }, []);

  useEffect(() => { outlinedTilesRef.current = outlinedTiles; }, [outlinedTiles]);
  useEffect(() => { penTilesRef.current = penTiles; }, [penTiles]);
  useEffect(() => { chaosSetupTilesRef.current = chaosSetupTiles; }, [chaosSetupTiles]);
  useEffect(() => { reclaimedTilesRef.current = reclaimedTiles; }, [reclaimedTiles]);
  useEffect(() => { deployedTilesRef.current = deployedTiles; }, [deployedTiles]);
  useEffect(() => { roadTilesRef.current = roadTiles; }, [roadTiles]);
  useEffect(() => { activeToolRef.current = activeTool; }, [activeTool]);
  useEffect(() => { paintModeRef.current = paintMode; }, [paintMode]);

  function setActiveToolImmediate(next: ToolType | ((prev: ToolType) => ToolType)) {
    const value = typeof next === "function" ? next(activeToolRef.current) : next;
    activeToolRef.current = value;
    markPerf(`tool ${value}`);
    setActiveTool(value);
    scheduleRender();
  }

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Shift") shiftDownRef.current = true;
      if (e.code === "Space" && !e.repeat && (e.target as HTMLElement).tagName !== "INPUT" && (e.target as HTMLElement).tagName !== "TEXTAREA") {
        e.preventDefault();
        spaceDownRef.current = true;
        if (viewportRef.current) viewportRef.current.style.cursor = "grab";
      }
    }
    function onKeyUp(e: KeyboardEvent) {
      if (e.key === "Shift") shiftDownRef.current = false;
      if (e.code === "Space") {
        spaceDownRef.current = false;
        if (viewportRef.current) viewportRef.current.style.cursor = "";
      }
    }
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("keyup", onKeyUp);
    return () => { window.removeEventListener("keydown", onKeyDown); window.removeEventListener("keyup", onKeyUp); };
  }, []);

  useEffect(() => {
    function onShortcutKey(e: KeyboardEvent) {
      const tag = (e.target as HTMLElement).tagName;
      if (tag === "INPUT" || tag === "TEXTAREA") return;
      if (e.code === "Space") return; // handled by space+drag effect
      // Ctrl/Cmd shortcuts
      if ((e.ctrlKey || e.metaKey) && e.code === "KeyZ" && !e.shiftKey) {
        e.preventDefault(); undoRedoRef.current.undo(); return;
      }
      if ((e.ctrlKey || e.metaKey) && (e.code === "KeyY" || (e.code === "KeyZ" && e.shiftKey))) {
        e.preventDefault(); undoRedoRef.current.redo(); return;
      }
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      if (e.code === "KeyP") { setActiveToolImmediate("pen"); setPaintMode("mark"); }
      if (e.code === "KeyD") { setActiveToolImmediate("draw_area"); setPaintMode("mark"); setBrushSize((prev) => Math.max(2, prev) as BrushSize); }
      if (e.code === "KeyR") { setActiveToolImmediate("road"); setPaintMode("mark"); }
      if (e.code === "KeyE") { setPaintMode((prev) => prev === "erase" ? "mark" : "erase"); }
      if (e.code === "Escape") { setActiveToolImmediate("none"); }
      if (e.code === "BracketLeft") {
        if (paintModeRef.current === "erase") setEraserSize((prev) => Math.max(1, prev - 1) as BrushSize);
        else setBrushSize((prev) => Math.max(1, prev - 1) as BrushSize);
      }
      if (e.code === "BracketRight") {
        if (paintModeRef.current === "erase") setEraserSize((prev) => Math.min(10, prev + 1) as BrushSize);
        else setBrushSize((prev) => Math.min(10, prev + 1) as BrushSize);
      }
    }
    window.addEventListener("keydown", onShortcutKey);
    return () => window.removeEventListener("keydown", onShortcutKey);
  }, []);

  useEffect(() => {
    function onClickOutside() {
      setLayersDropdownOpen(false);
      setSurveysDropdownOpen(false);
    }
    document.addEventListener("click", onClickOutside);
    return () => document.removeEventListener("click", onClickOutside);
  }, []);

  useEffect(() => {
    function onFsChange() {
      setIsFullscreen(!!document.fullscreenElement || isFullscreenFallback);
    }
    document.addEventListener("fullscreenchange", onFsChange);
    return () => document.removeEventListener("fullscreenchange", onFsChange);
  }, [isFullscreenFallback]);

  useEffect(() => {
    setTouchMode(isTouchDevice());
  }, []);

  useEffect(() => {
    if (!touchMode) {
      setMobileToolbarOpen(false);
      setTouchEditMode(false);
    }
  }, [touchMode]);

  useEffect(() => {
    if (!isFullscreen) setFullscreenToolbarOpen(false);
  }, [isFullscreen]);

  useEffect(() => {
    if (!isFullscreen || !touchMode || typeof window === "undefined") return;
    const orientation = (window.screen as any)?.orientation ?? (window.screen as any)?.mozOrientation ?? (window.screen as any)?.msOrientation;
    if (!orientation?.lock) {
      setNotice("Landscape fullscreen not supported by this browser.");
      return;
    }

    orientation.lock("landscape").catch(() => {
      setNotice("Landscape fullscreen lock failed on this device/browser.");
    });

    return () => {
      orientation.unlock?.();
    };
  }, [isFullscreen, touchMode]);

  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(null), 1800);
    return () => window.clearTimeout(timer);
  }, [notice]);

  useEffect(() => {
    function stopPainting(event?: MouseEvent | PointerEvent) {
      if (event instanceof PointerEvent) {
        if (!activePaintPointersRef.current.has(event.pointerId)) return;
        activePaintPointersRef.current.delete(event.pointerId);
        if (activePaintPointersRef.current.size > 0) return;
      }
      // Commit road snap on release
      if (activeToolRef.current === "road" && paintModeRef.current === "mark") {
        const keys = roadSnapPreviewRef.current;
        if (keys.size > 0) {
          setRoadTiles((prev) => {
            const next = new Set(prev);
            keys.forEach((k) => next.add(k));
            roadTilesRef.current = next;
            scheduleRender();
            return next;
          });
        }
      }
      if (activeToolRef.current === "move_deploy" && moveDeploySourceRef.current) {
        const source = moveDeploySourceRef.current;
        const keys = moveDeployPreviewRef.current;
        setDeployedTiles((prev) => {
          const next = new Map(prev);
          source.sourceKeys.forEach((k) => decrementDeploymentCount(next, k));
          if (keys.size > 0) {
            keys.forEach((k) => incrementDeploymentCount(next, k));
          } else {
            source.sourceKeys.forEach((k) => incrementDeploymentCount(next, k));
          }
          deployedTilesRef.current = next;
          scheduleRender();
          return next;
        });
      }
      roadSnapStartRef.current = null;
      roadSnapPreviewRef.current = new Set();
      setRoadSnapPreview(new Set());
      setMoveDeployPreviewKeys(new Set());
      moveDeployPreviewRef.current = new Set();
      moveDeploySourceRef.current = null;
      setIsPainting(false);
      lastPaintedKeysRef.current = new Set();
      lastTileRef.current = null;
      lineStartTileRef.current = null;
      activePaintPointersRef.current = new Set();
    }
    function stopPanning(event?: MouseEvent | PointerEvent) {
      if (event instanceof PointerEvent && panStartRef.current?.source === "pointer" && panStartRef.current.pointerId !== event.pointerId) {
        return;
      }
      if (panStartRef.current) {
        const velocity = panVelocityRef.current;
        const camera = cameraRef.current;
        const dragDistance = Math.hypot(
          velocity.lastX - panStartRef.current.x,
          velocity.lastY - panStartRef.current.y,
        );
        const shouldCarryMomentum = dragDistance >= 6 && (Math.abs(velocity.vx) > 0.12 || Math.abs(velocity.vy) > 0.12);
        camera.vx = shouldCarryMomentum ? velocity.vx : 0;
        camera.vy = shouldCarryMomentum ? velocity.vy : 0;
        camera.targetX = camera.x;
        camera.targetY = camera.y;
        camera.targetZoom = camera.zoom;
        ensureCameraAnimation();
      }
      setIsPanning(false);
      panStartRef.current = null;
    }
    window.addEventListener("mouseup", stopPainting);
    window.addEventListener("pointerup", stopPainting);
    window.addEventListener("pointercancel", stopPainting);
    window.addEventListener("mouseup", stopPanning);
    window.addEventListener("pointerup", stopPanning);
    return () => {
      window.removeEventListener("mouseup", stopPainting);
      window.removeEventListener("pointerup", stopPainting);
      window.removeEventListener("pointercancel", stopPainting);
      window.removeEventListener("mouseup", stopPanning);
      window.removeEventListener("pointerup", stopPanning);
    };
  }, []);

  const { grid, rows, cols, terrainCounts: baseTerrainCounts } = useMemo(() => buildTiles(), []);

  const selectDebugTile = useCallback((x: number, y: number) => {
    const tile = grid.get(keyOf(x, y));
    if (tile) {
      setSelectedTile(tile);
      if (viewportRef.current) {
        const tileSizePx = tileSize;
        setCameraPosition(
          Math.max(0, x * tileSizePx - viewportRef.current.clientWidth / 2),
          Math.max(0, y * tileSizePx - viewportRef.current.clientHeight / 2)
        );
      }
    }
  }, [grid, tileSize]);

  const isLand = (tile: Tile) => tile.buildable || reclaimedTilesRef.current.has(keyOf(tile.x, tile.y));

  const visibleInfoTile = touchMode ? selectedTile : hoveredTile ?? selectedTile;

  async function copyVisibleTileInfo() {
    if (!visibleInfoTile) return;
    const text = `x=${visibleInfoTile.x}, y=${visibleInfoTile.y}, nativeX=${visibleInfoTile.nativeX}, nativeY=${visibleInfoTile.nativeY}, terrain=${visibleInfoTile.terrain}, level=${visibleInfoTile.level}, rawTileId=${visibleInfoTile.fullTerrainId ?? "n/a"}`;
    try {
      await navigator.clipboard.writeText(text);
      flashNotice("Tile info copied to clipboard");
    } catch {
      flashNotice("Copy failed");
    }
  }

  const effectiveTerrainCounts = useMemo(() => {
    const counts = { ...baseTerrainCounts };
    reclaimedTiles.forEach((key) => {
      const tile = grid.get(key);
      if (!tile || tile.buildable) return;
      counts[tile.terrain] += 1;
    });
    return counts;
  }, [baseTerrainCounts, reclaimedTiles, grid]);

  const totalLandTiles = useMemo(
    () => Array.from(grid.values()).filter((tile) => tile.buildable).length + reclaimedTiles.size,
    [grid, reclaimedTiles]
  );

  const deployedLandTiles = useMemo(() => {
    let covered = 0;
    deployedTiles.forEach((count, key) => {
      if (count <= 0) return;
      const tile = grid.get(key);
      if (!tile) return;
      if (tile.buildable || reclaimedTiles.has(key)) covered += 1;
    });
    return covered;
  }, [deployedTiles, grid, reclaimedTiles]);

  const coveredPercent = totalLandTiles > 0 ? (deployedLandTiles / totalLandTiles) * 100 : 0;

  const deploymentCoverageByBiomeLevel = useMemo(() => {
    const counts = new Map<string, number>();
    deployedTiles.forEach((deploymentCount, key) => {
      if (deploymentCount <= 0) return;
      const tile = grid.get(key);
      if (!tile) return;
      if (!isLand(tile)) return;
      const label = `${tile.terrain} • Lv.${tile.level}`;
      counts.set(label, (counts.get(label) ?? 0) + 1);
    });
    return Array.from(counts.entries())
      .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
      .map(([label, count]) => ({ label, count }));
  }, [deployedTiles, reclaimedTiles]);

  const reclaimCost = reclaimedTiles.size * 10;

  const currentTheme = {
    appBg: "linear-gradient(180deg, #0f172a 0%, #111827 100%)",
    panelBg: "rgba(15, 23, 42, 0.82)",
    panelBorder: "rgba(255,255,255,0.08)",
    water: "#6d96db",
    waterBorder: "rgba(255,255,255,0.10)",
  };
  const brushOptions: BrushSize[] = [1,2,3,4,5,6,7,8,9,10];
  const drawAreaBrushOptions: BrushSize[] = [2,3,4,5,6,7,8,9,10];
  const deploymentOptions: DeploymentSize[] = [2,3,4,5,6];
  const layerOrder: LayerKey[] = ["levels","chaos_setup","facilities","poi","reclaimed","grid","roads","deployments"];
  const minimapSize = useMemo(() => {
    const maxWidth = touchMode ? 132 : 168;
    const maxHeight = touchMode ? 112 : 144;
    const scale = Math.min(maxWidth / cols, maxHeight / rows, 1);
    return {
      width: Math.max(96, Math.round(cols * scale)),
      height: Math.max(78, Math.round(rows * scale)),
      scaleX: Math.max(96, Math.round(cols * scale)) / cols,
      scaleY: Math.max(78, Math.round(rows * scale)) / rows,
    };
  }, [cols, rows, touchMode]);

  useEffect(() => {
    const width = minimapCollapsed ? 40 : minimapSize.width + 16;
    const height = minimapCollapsed ? 40 : minimapSize.height + 44;
    requestAnimationFrame(() => {
      setMinimapBubblePos((pos) => clampFloatingMapPos(pos.x, pos.y, width, height));
    });
  }, [isFullscreen, touchMode, minimapCollapsed, minimapSize.width, minimapSize.height]);

  const surveyZoneData = useMemo(() => {
    const defsByZone = new Map<string, SurveyDef[]>();
    const catsByZone = new Map<string, SurveyCategory[]>();

    for (let ny = 0; ny < 10; ny += 1) {
      for (let nx = 0; nx < 10; nx += 1) {
        const native = NATIVE_MAP[ny]?.[nx];
        if (!native) continue;
        const matching = SURVEY_DEFS.filter((s) => {
          if (s.terrainId !== -1 && SURVEY_TERRAIN_MAP[s.terrainId] !== native.terrain) return false;
          return native.level >= s.minLevel;
        });
        if (!matching.length) continue;

        const key = `${nx},${ny}`;
        defsByZone.set(key, matching);
        catsByZone.set(key, [...new Set(matching.map((s) => s.category))] as SurveyCategory[]);
      }
    }

    return { defsByZone, catsByZone };
  }, []);

  const townhallPlacementKeys = useMemo(() => {
    if (!townhallPlacement) return new Set<string>();
    return new Set(getTownHallFootprintCoords(townhallPlacement.x, townhallPlacement.y, cols, rows).map(([x, y]) => keyOf(x, y)));
  }, [townhallPlacement, cols, rows]);

  const townhallCoverageBounds = useMemo(() => {
    if (!townhallPlacement) return null;
    return getTownHallCoverageBounds(townhallPlacement.x, townhallPlacement.y, townhallPlacement.level, cols, rows);
  }, [townhallPlacement, cols, rows]);

  const townhallPreviewBounds = useMemo(() => {
    if (activeTool !== "townhall" || !hoveredTile) return null;
    return getTownHallCoverageBounds(hoveredTile.x, hoveredTile.y, townhallLevel, cols, rows);
  }, [activeTool, hoveredTile, townhallLevel, cols, rows]);

  const previewKeys = useMemo(() => {
    // Road snap preview is handled separately via roadSnapPreview state
    if (activeTool === "road" && paintMode === "mark") {
      return roadSnapPreview;
    }
    if (activeTool === "move_deploy") {
      return moveDeployPreviewKeys;
    }
    if (!hoveredTile || activeTool === "none") return new Set<string>();
    const coords =
      activeTool === "deploy"
        ? getDiamondCoordinates(hoveredTile.x, hoveredTile.y, deploymentSize, cols, rows)
        : activeTool === "chaos_setup"
        ? getChaosSetupFootprintCoords(hoveredTile.x, hoveredTile.y, chaosSetupPiece, cols, rows)
        : activeTool === "townhall"
        ? getCenteredSquareCoordinates(hoveredTile.x, hoveredTile.y, getTownHallCoverageSize(townhallLevel), cols, rows)
        : activeTool === "pen"
        ? getCenteredSquareCoordinates(hoveredTile.x, hoveredTile.y, paintMode === "erase" ? eraserSize : 1, cols, rows)
        : getCenteredSquareCoordinates(hoveredTile.x, hoveredTile.y, brushSize, cols, rows);

    const next = new Set<string>();
    coords.forEach(([x, y]) => {
      const key = keyOf(x, y);
      const tile = grid.get(key);
      if (!tile) return;
      if (activeTool === "reclaim") {
        next.add(key);
        return;
      }
      if (activeTool === "chaos_setup") {
        if (isLand(tile)) next.add(key);
        return;
      }
      if (activeTool === "pen") { next.add(key); return; } // pen works on all tiles
      if (isLand(tile)) next.add(key);
    });
    return next;
  }, [hoveredTile, activeTool, paintMode, deploymentSize, brushSize, eraserSize, cols, rows, grid, reclaimedTiles, roadSnapPreview, chaosSetupPiece, townhallLevel, moveDeployPreviewKeys]);

  const previewStats = useMemo(() => {
    if (!hoveredTile || activeTool !== "deploy") return null;
    let total = 0;
    let newCoverage = 0;
    previewKeys.forEach((key) => {
      const tile = grid.get(key);
      if (!tile || !isLand(tile)) return;
      total += 1;
      if (getDeploymentCount(key) === 0) newCoverage += 1;
    });
    return { total, newCoverage };
  }, [hoveredTile, activeTool, previewKeys, deployedTiles, reclaimedTiles]);

  function buildPreviewKeysForTile(tile: Tile | null) {
    const tool = activeToolRef.current;
    const mode = paintModeRef.current;
    if (tool === "road" && mode === "mark") return roadSnapPreview;
    if (tool === "move_deploy") return moveDeployPreviewKeys;
    if (!tile || tool === "none") return new Set<string>();
    const coords =
      tool === "deploy"
        ? getDiamondCoordinates(tile.x, tile.y, deploymentSize, cols, rows)
        : tool === "chaos_setup"
        ? getChaosSetupFootprintCoords(tile.x, tile.y, chaosSetupPiece, cols, rows)
        : tool === "townhall"
        ? getCenteredSquareCoordinates(tile.x, tile.y, getTownHallCoverageSize(townhallLevel), cols, rows)
        : tool === "pen"
        ? getCenteredSquareCoordinates(tile.x, tile.y, mode === "erase" ? eraserSize : 1, cols, rows)
        : getCenteredSquareCoordinates(tile.x, tile.y, brushSize, cols, rows);

    const next = new Set<string>();
    coords.forEach(([x, y]) => {
      const key = keyOf(x, y);
      const candidate = grid.get(key);
      if (!candidate) return;
      if (tool === "reclaim") {
        next.add(key);
        return;
      }
      if (tool === "chaos_setup") {
        if (isLand(candidate)) next.add(key);
        return;
      }
      if (tool === "pen") {
        next.add(key);
        return;
      }
      if (isLand(candidate)) next.add(key);
    });
    return next;
  }

  function updateCanvasHover(tile: Tile | null) {
    const previous = hoveredTileRef.current;
    if (
      (previous === null && tile === null) ||
      (previous !== null && tile !== null && previous.x === tile.x && previous.y === tile.y)
    ) {
      return;
    }
    hoveredTileRef.current = tile;
    previewKeysRef.current = buildPreviewKeysForTile(tile);
    townhallPreviewBoundsRef.current =
      activeToolRef.current === "townhall" && tile
        ? getTownHallCoverageBounds(tile.x, tile.y, townhallLevel, cols, rows)
        : null;

    if (activeToolRef.current === "none") {
      setHoveredTile(tile);
    } else {
      scheduleRender();
    }
  }

  function flashNotice(message: string) {
    setNotice(message);
  }

  function clampNumber(value: number, min: number, max: number) {
    return Math.min(Math.max(value, min), max);
  }

  function tileSizeFromZoom(zoom = cameraRef.current.zoom) {
    return BASE_TILE_SIZE * zoom;
  }

  function getMinZoom() {
    return getMinTileSize() / BASE_TILE_SIZE;
  }

  function getMaxZoom() {
    return MAX_TILE_SIZE / BASE_TILE_SIZE;
  }

  function getViewportWorldOffset(nextTileSize = tileSizeFromZoom()) {
    const viewport = viewportRef.current;
    const viewportWidth = viewport?.clientWidth ?? 0;
    const viewportHeight = viewport?.clientHeight ?? 0;
    const worldWidth = cols * nextTileSize + MAP_CANVAS_MARGIN * 2;
    const worldHeight = rows * nextTileSize + MAP_CANVAS_MARGIN * 2;
    return {
      x: Math.max(0, (viewportWidth - worldWidth) / 2),
      y: Math.max(0, (viewportHeight - worldHeight) / 2),
    };
  }

  function syncMapScroll() {
    const viewport = viewportRef.current;
    if (!viewport) return;
    const camera = cameraRef.current;
    const renderedTileSize = tileSizeFromZoom(camera.zoom);
    const worldWidth = cols * renderedTileSize + MAP_CANVAS_MARGIN * 2;
    const worldHeight = rows * renderedTileSize + MAP_CANVAS_MARGIN * 2;
    tileSizeRef.current = renderedTileSize;
    setMapScroll({
      left: camera.x,
      top: camera.y,
      width: worldWidth,
      height: worldHeight,
      viewportWidth: viewport.clientWidth,
      viewportHeight: viewport.clientHeight,
    });
    setMapCamera({ left: camera.x, top: camera.y, zoom: camera.zoom });
    setTileSize(renderedTileSize);
  }

  function scheduleUiSync() {
    if (uiSyncRafRef.current !== null) return;
    uiSyncRafRef.current = requestAnimationFrame(() => {
      uiSyncRafRef.current = null;
      syncMapScroll();
    });
  }

  function scheduleRender() {
    if (renderRafRef.current !== null) return;
    renderRafRef.current = requestAnimationFrame(() => {
      renderRafRef.current = null;
      drawMap();
    });
  }

  function getCameraBounds(nextTileSize = tileSizeFromZoom()) {
    const viewport = viewportRef.current;
    const viewportWidth = viewport?.clientWidth ?? 0;
    const viewportHeight = viewport?.clientHeight ?? 0;
    return {
      maxLeft: Math.max(0, cols * nextTileSize + MAP_CANVAS_MARGIN * 2 - viewportWidth),
      maxTop: Math.max(0, rows * nextTileSize + MAP_CANVAS_MARGIN * 2 - viewportHeight),
    };
  }

  function clampCamera(camera = cameraRef.current) {
    const nextTileSize = tileSizeFromZoom(camera.zoom);
    const bounds = getCameraBounds(nextTileSize);
    camera.x = clampNumber(camera.x, 0, bounds.maxLeft);
    camera.y = clampNumber(camera.y, 0, bounds.maxTop);
    camera.targetX = clampNumber(camera.targetX, 0, bounds.maxLeft);
    camera.targetY = clampNumber(camera.targetY, 0, bounds.maxTop);
    return camera;
  }

  function setCameraPosition(left: number, top: number, nextTileSize = tileSizeFromZoom(), sync = false) {
    const camera = cameraRef.current;
    camera.zoom = clampNumber(nextTileSize / BASE_TILE_SIZE, getMinZoom(), getMaxZoom());
    camera.targetZoom = camera.zoom;
    camera.x = left;
    camera.y = top;
    camera.targetX = left;
    camera.targetY = top;
    camera.vx = 0;
    camera.vy = 0;
    clampCamera(camera);
    tileSizeRef.current = tileSizeFromZoom(camera.zoom);
    scheduleRender();
    scheduleUiSync();
    if (sync) syncMapScroll();
  }

  function stopCameraAnimation() {
    if (cameraAnimationRef.current !== null) {
      cancelAnimationFrame(cameraAnimationRef.current);
      cameraAnimationRef.current = null;
    }
    cameraLastFrameTimeRef.current = null;
    cameraRef.current.vx = 0;
    cameraRef.current.vy = 0;
  }

  function animateCamera(frameTime: number) {
    const camera = cameraRef.current;
    const lastFrameTime = cameraLastFrameTimeRef.current;
    const frameScale = lastFrameTime == null
      ? 1
      : clampNumber((frameTime - lastFrameTime) / PAN_SAMPLE_MS, 0.75, 2);
    cameraLastFrameTimeRef.current = frameTime;
    const zoomDelta = camera.targetZoom - camera.zoom;
    const xDelta = camera.targetX - camera.x;
    const yDelta = camera.targetY - camera.y;
    const zoomLerp = 1 - Math.pow(1 - CAMERA_ZOOM_LERP, frameScale);
    const positionLerp = 1 - Math.pow(1 - CAMERA_POSITION_LERP, frameScale);
    const panFriction = Math.pow(CAMERA_PAN_FRICTION, frameScale);
    camera.zoom += zoomDelta * zoomLerp;
    camera.x += xDelta * positionLerp + camera.vx * frameScale;
    camera.y += yDelta * positionLerp + camera.vy * frameScale;
    camera.vx *= panFriction;
    camera.vy *= panFriction;
    clampCamera(camera);
    tileSizeRef.current = tileSizeFromZoom(camera.zoom);
    scheduleRender();
    scheduleUiSync();

    const stillMoving =
      Math.abs(zoomDelta) > 0.001 ||
      Math.abs(xDelta) > 0.5 ||
      Math.abs(yDelta) > 0.5 ||
      Math.abs(camera.vx) > 0.08 ||
      Math.abs(camera.vy) > 0.08;

    if (stillMoving) {
      cameraAnimationRef.current = requestAnimationFrame(animateCamera);
    } else {
      camera.zoom = camera.targetZoom;
      camera.x = camera.targetX;
      camera.y = camera.targetY;
      camera.vx = 0;
      camera.vy = 0;
      clampCamera(camera);
      cameraLastFrameTimeRef.current = null;
      cameraAnimationRef.current = null;
      scheduleRender();
      syncMapScroll();
    }
  }

  function ensureCameraAnimation() {
    if (cameraAnimationRef.current === null) {
      cameraAnimationRef.current = requestAnimationFrame(animateCamera);
    }
  }

  function clampFloatingMapPos(x: number, y: number, width = 44, height = 44) {
    const bounds = viewportRef.current?.parentElement;
    if (!bounds) return { x, y };
    const pad = 8;
    return {
      x: clampNumber(x, pad, Math.max(pad, bounds.clientWidth - width - pad)),
      y: clampNumber(y, pad, Math.max(pad, bounds.clientHeight - height - pad)),
    };
  }

  function pushHistory() {
    setHistoryPast((prev) => [...prev.slice(-29), snapshot(outlinedTiles, reclaimedTiles, deployedTiles, roadTiles, penTiles, chaosSetupTiles, townhallPlacement)]);
    setHistoryFuture([]);
  }

  function restoreSets(state: HistoryState) {
    const restored = restoreSnapshot(state);
    outlinedTilesRef.current = restored.outlined;
    reclaimedTilesRef.current = restored.reclaimed;
    deployedTilesRef.current = restored.deployed;
    roadTilesRef.current = restored.roads;
    penTilesRef.current = restored.penned;
    chaosSetupTilesRef.current = restored.chaosSetup;
    setOutlinedTiles(restored.outlined);
    setReclaimedTiles(restored.reclaimed);
    setDeployedTiles(restored.deployed);
    setRoadTiles(restored.roads);
    setPenTiles(restored.penned);
    setChaosSetupTiles(restored.chaosSetup);
    setTownhallPlacement(restored.townhallPlacement);
    scheduleRender();
  }

  function undo() {
    if (!historyPast.length) return;
    const previous = historyPast[historyPast.length - 1];
    setHistoryPast((prev) => prev.slice(0, -1));
    setHistoryFuture((prev) => [snapshot(outlinedTiles, reclaimedTiles, deployedTiles, roadTiles, penTiles, chaosSetupTiles, townhallPlacement), ...prev].slice(0, 30));
    restoreSets(previous);
    flashNotice("Undid last change");
  }

  function redo() {
    if (!historyFuture.length) return;
    const next = historyFuture[0];
    setHistoryFuture((prev) => prev.slice(1));
    setHistoryPast((prev) => [...prev.slice(-29), snapshot(outlinedTiles, reclaimedTiles, deployedTiles, roadTiles, penTiles, chaosSetupTiles, townhallPlacement)]);
    restoreSets(next);
    flashNotice("Redid last change");
  }

  function resetMap() {
    setActiveToolImmediate("none");
    setPaintMode("mark");
    setBrushSize(1);
    setPenBrushSize(1);
    setEraserSize(1);
    setDeploymentSize(2);
    setTownhallLevel(1);
    setTownhallPlacement(null);
    const emptyOutlined = new Set<string>();
    const emptyPen = new Map<string, string>();
    const emptyChaosSetup = new Map<string, ChaosSetupPiece>();
    const emptyReclaimed = new Set<string>();
    const emptyDeployed = new Map<string, number>();
    const emptyRoads = new Set<string>();
    outlinedTilesRef.current = emptyOutlined;
    penTilesRef.current = emptyPen;
    chaosSetupTilesRef.current = emptyChaosSetup;
    reclaimedTilesRef.current = emptyReclaimed;
    deployedTilesRef.current = emptyDeployed;
    roadTilesRef.current = emptyRoads;
    setOutlinedTiles(emptyOutlined);
    setPenTiles(emptyPen);
    setChaosSetupTiles(emptyChaosSetup);
    setReclaimedTiles(emptyReclaimed);
    setDeployedTiles(emptyDeployed);
    setRoadTiles(emptyRoads);
    setHistoryPast([]);
    setHistoryFuture([]);
    setCleanMode(false);
    scheduleRender();
    flashNotice("Map reset to clean state");
  }

  // Keep a stable ref to undo/redo so keyboard handler doesn't need re-registration
  const undoRedoRef = useRef({ undo, redo });
  undoRedoRef.current = { undo, redo };

  function applyPatchToSet(
    target: "outline" | "pen" | "chaos_setup" | "reclaim" | "deploy" | "road",
    coords: Array<[number, number]>,
    mode: "mark" | "erase" | "reclaim" | "restore"
  ) {
    if (target === "chaos_setup") {
      const anchor = coords[0];
      if (!anchor) return;
      const anchorKey = keyOf(anchor[0], anchor[1]);
      const historyKey = `chaos:${anchorKey}`;
      if (lastPaintedKeysRef.current.has(historyKey)) return;

      setChaosSetupTiles((prev) => {
        const next = new Map(prev);

        if (mode === "mark") {
          const footprint = getChaosSetupFootprint(anchor[0], anchor[1], chaosSetupPiece, cols, rows);
          if (!footprint.length) return prev;
          const canPlace = footprint.every((footKey) => {
            const tile = grid.get(footKey);
            return !!tile && isLand(tile);
          });
          if (!canPlace) return prev;
          next.set(anchorKey, chaosSetupPiece);
          chaosSetupTilesRef.current = next;
          scheduleRender();
          return next;
        }

        let removed = false;
        prev.forEach((piece, placedKey) => {
          if (removed) return;
          const [px, py] = placedKey.split(",").map(Number);
          const footprint = getChaosSetupFootprint(px, py, piece, cols, rows);
          if (footprint.includes(anchorKey)) {
            next.delete(placedKey);
            removed = true;
          }
        });
        chaosSetupTilesRef.current = next;
        scheduleRender();
        return next;
      });

      lastPaintedKeysRef.current.add(historyKey);
      return;
    }

    const keys = coords.map(([x, y]) => keyOf(x, y));
    const freshKeys = keys.filter((key) => !lastPaintedKeysRef.current.has(key));
    const keysToApply = target === "deploy" ? keys : freshKeys;
    if (!keysToApply.length) return;

    if (target === "outline") {
      setOutlinedTiles((prev) => {
        const next = new Set(prev);
        keysToApply.forEach((key) => {
          const tile = grid.get(key);
          if (!tile || !isLand(tile)) return;
          if (mode === "mark") next.add(key);
          else next.delete(key);
        });
        outlinedTilesRef.current = next;
        scheduleRender();
        return next;
      });
    }

    if (target === "pen") {
      setPenTiles((prev) => {
        const next = new Map(prev);
        keysToApply.forEach((key) => {
          const tile = grid.get(key);
          if (!tile) return;
          if (mode === "mark") next.set(key, penColor);
          else next.delete(key);
        });
        penTilesRef.current = next;
        scheduleRender();
        return next;
      });
    }

    if (target === "reclaim") {
      setReclaimedTiles((prev) => {
        const next = new Set(prev);
        keysToApply.forEach((key) => {
          const tile = grid.get(key);
          if (!tile || tile.buildable) return;
          if (mode === "reclaim") next.add(key);
          else next.delete(key);
        });
        reclaimedTilesRef.current = next;
        scheduleRender();
        return next;
      });
    }

    if (target === "deploy") {
      setDeployedTiles((prev) => {
        const next = new Map(prev);
        keysToApply.forEach((key) => {
          const tile = grid.get(key);
          if (!tile || !isLand(tile)) return;
          if (mode === "mark") incrementDeploymentCount(next, key);
          else decrementDeploymentCount(next, key);
        });
        deployedTilesRef.current = next;
        scheduleRender();
        return next;
      });
    }

    if (target === "road") {
      setRoadTiles((prev) => {
        const next = new Set(prev);
        keysToApply.forEach((key) => {
          const tile = grid.get(key);
          if (!tile || !isLand(tile)) return;
          if (mode === "mark") next.add(key);
          else next.delete(key);
        });
        roadTilesRef.current = next;
        scheduleRender();
        return next;
      });
    }

    if (target !== "deploy") {
      freshKeys.forEach((key) => lastPaintedKeysRef.current.add(key));
    }
  }


  function collectConnectedDeployment(startKey: string) {
    const visited = new Set<string>();
    const stack = [startKey];
    while (stack.length) {
      const key = stack.pop()!;
      if (visited.has(key) || getDeploymentCount(key) === 0) continue;
      visited.add(key);
      const [xStr, yStr] = key.split(",");
      const x = Number(xStr);
      const y = Number(yStr);
      [keyOf(x + 1, y), keyOf(x - 1, y), keyOf(x, y + 1), keyOf(x, y - 1)].forEach((nextKey) => {
        if (getDeploymentCount(nextKey) > 0 && !visited.has(nextKey)) stack.push(nextKey);
      });
    }
    return visited;
  }

  function handleToolAction(tile: Tile) {
    const tool = activeToolRef.current;
    const mode = paintModeRef.current;
    markPerf(`action ${tool}/${mode}`);
    setSelectedTile(tile);
    if (tool === "none") return;

    if (mode === "erase") {
      const eraseCoords =
        eraserTarget === "deploy"
          ? getDiamondCoordinates(tile.x, tile.y, deploymentSize, cols, rows)
          : eraserTarget === "chaos_setup"
          ? getChaosSetupFootprintCoords(tile.x, tile.y, chaosSetupPiece, cols, rows)
          : getCenteredSquareCoordinates(tile.x, tile.y, eraserSize, cols, rows);

      if (eraserTarget === "all") {
        applyPatchToSet("pen", eraseCoords, "erase");
        applyPatchToSet("outline", eraseCoords, "erase");
        applyPatchToSet("road", eraseCoords, "erase");
        applyPatchToSet("chaos_setup", eraseCoords, "erase");
        const cluster = collectConnectedDeployment(keyOf(tile.x, tile.y));
        if (cluster.size) {
          setDeployedTiles((prev) => {
            const next = new Map(prev);
            cluster.forEach((key) => decrementDeploymentCount(next, key));
            deployedTilesRef.current = next;
            scheduleRender();
            return next;
          });
        } else {
          applyPatchToSet("deploy", eraseCoords, "erase");
        }
        return;
      }

      if (eraserTarget === "pen") applyPatchToSet("pen", eraseCoords, "erase");
      if (eraserTarget === "draw_area") applyPatchToSet("outline", eraseCoords, "erase");
      if (eraserTarget === "road") applyPatchToSet("road", eraseCoords, "erase");
      if (eraserTarget === "chaos_setup") applyPatchToSet("chaos_setup", eraseCoords, "erase");
      if (eraserTarget === "deploy") {
        const cluster = collectConnectedDeployment(keyOf(tile.x, tile.y));
        if (cluster.size) {
          setDeployedTiles((prev) => {
            const next = new Map(prev);
            cluster.forEach((key) => decrementDeploymentCount(next, key));
            deployedTilesRef.current = next;
            scheduleRender();
            return next;
          });
        } else {
          applyPatchToSet("deploy", eraseCoords, "erase");
        }
      }
      return;
    }

    if (tool === "townhall") {
      setTownhallPlacement({ x: tile.x, y: tile.y, level: townhallLevel });
      return;
    }

    const coords =
      tool === "deploy"
        ? getDiamondCoordinates(tile.x, tile.y, deploymentSize, cols, rows)
        : tool === "chaos_setup"
        ? getChaosSetupFootprintCoords(tile.x, tile.y, chaosSetupPiece, cols, rows)
        : tool === "pen"
        ? getCenteredSquareCoordinates(tile.x, tile.y, penBrushSize, cols, rows)
        : getCenteredSquareCoordinates(tile.x, tile.y, brushSize, cols, rows);

    if (tool === "pen") applyPatchToSet("pen", coords, "mark");
    if (tool === "chaos_setup") applyPatchToSet("chaos_setup", coords, mode);
    if (tool === "draw_area") applyPatchToSet("outline", coords, mode);
    if (tool === "reclaim") applyPatchToSet("reclaim", coords, reclaimMode);
    if (tool === "deploy") {
      applyPatchToSet("deploy", coords, "mark");
    }
    if (tool === "road") applyPatchToSet("road", coords, mode);
  }

  function tilesOnLine(x0: number, y0: number, x1: number, y1: number): Array<[number, number]> {
    const pts: Array<[number, number]> = [];
    let dx = Math.abs(x1 - x0), sx = x0 < x1 ? 1 : -1;
    let dy = -Math.abs(y1 - y0), sy = y0 < y1 ? 1 : -1;
    let err = dx + dy, cx = x0, cy = y0;
    for (;;) {
      pts.push([cx, cy]);
      if (cx === x1 && cy === y1) break;
      const e2 = 2 * err;
      if (e2 >= dy) { err += dy; cx += sx; }
      if (e2 <= dx) { err += dx; cy += sy; }
    }
    return pts;
  }

  function addRecentColor(color: string) {
    setRecentColors((prev) => [color, ...prev.filter((c) => c !== color)].slice(0, 5));
  }

  function beginPaint(tile: Tile) {
    const tool = activeToolRef.current;
    const mode = paintModeRef.current;
    // Road+mark is handled at viewport level (for snap), skip here
    if (tool === "road" && mode === "mark") {
      setSelectedTile(tile);
      return;
    }
    if (tool === "townhall") {
      pushHistory();
      handleToolAction(tile);
      return;
    }
    if (tool === "move_deploy") {
      const cluster = collectConnectedDeployment(keyOf(tile.x, tile.y));
      if (cluster.size === 0) return;
      pushHistory();
      const sourceCenter = getDeploymentClusterCenter(cluster);
      moveDeploySourceRef.current = { sourceKeys: cluster, sourceCenter };
      const previewKeys = translateDeploymentKeys(cluster, sourceCenter, sourceCenter, cols, rows);
      moveDeployPreviewRef.current = previewKeys;
      setMoveDeployPreviewKeys(previewKeys);
      setDeployedTiles((prev) => {
        const next = new Map(prev);
        cluster.forEach((key) => decrementDeploymentCount(next, key));
        deployedTilesRef.current = next;
        scheduleRender();
        return next;
      });
      setSelectedTile(tile);
      setIsPainting(true);
      return;
    }
    if (tool !== "none") {
      if (tool === "deploy") {
        pushHistory();
        handleToolAction(tile);
        return;
      }
      if (tool === "pen" && mode === "mark") addRecentColor(penColor);
      if (tool === "draw_area" && mode === "mark") addRecentColor(drawAreaColor);
      pushHistory();
      lastPaintedKeysRef.current = new Set();
      lastTileRef.current = tile;
      lineStartTileRef.current = tile;
      setIsPainting(true);
      if (!(tool === "pen" && mode === "mark" && lineAssist)) {
        handleToolAction(tile);
      }
      return;
    }
    setSelectedTile(tile);
  }

  function continuePaint(tile: Tile) {
    const tool = activeToolRef.current;
    const mode = paintModeRef.current;
    if (!isPainting || tool === "none") return;
    if (tool === "move_deploy") {
      const moveState = moveDeploySourceRef.current;
      if (!moveState) return;
      const previewKeys = translateDeploymentKeys(moveState.sourceKeys, moveState.sourceCenter, { x: tile.x, y: tile.y }, cols, rows);
      moveDeployPreviewRef.current = previewKeys;
      setMoveDeployPreviewKeys(previewKeys);
      setSelectedTile(tile);
      return;
    }
    if (tool === "pen" && mode === "mark" && lineAssist) {
      const start = lineStartTileRef.current ?? tile;
      let targetX = tile.x;
      let targetY = tile.y;
      if (Math.abs(tile.x - start.x) >= Math.abs(tile.y - start.y)) targetY = start.y;
      else targetX = start.x;
      const pts = tilesOnLine(start.x, start.y, targetX, targetY);
      for (const [x, y] of pts) {
        const t = grid.get(keyOf(x, y));
        if (t) handleToolAction(t);
      }
      lastTileRef.current = tile;
      return;
    }
    const last = lastTileRef.current;
    if (last && (last.x !== tile.x || last.y !== tile.y)) {
      let targetX = tile.x;
      let targetY = tile.y;
      if (tool === "pen" && (lineAssist || shiftDownRef.current)) {
        if (Math.abs(tile.x - last.x) >= Math.abs(tile.y - last.y)) targetY = last.y;
        else targetX = last.x;
      }
      const pts = tilesOnLine(last.x, last.y, targetX, targetY);
      for (const [x, y] of pts.slice(1)) {
        const t = grid.get(keyOf(x, y));
        if (t) handleToolAction(t);
      }
    } else {
      handleToolAction(tile);
    }
    lastTileRef.current = tile;
  }

  function tileAtPointer(clientX: number, clientY: number): Tile | null {
    const viewport = viewportRef.current;
    if (!viewport) return null;
    const rect = viewport.getBoundingClientRect();
    const viewportOffset = getViewportWorldOffset(tileSizeRef.current);
    const offsetX = clientX - rect.left - viewportOffset.x + cameraRef.current.x - MAP_CANVAS_MARGIN;
    const offsetY = clientY - rect.top - viewportOffset.y + cameraRef.current.y - MAP_CANVAS_MARGIN;
    const tx = Math.floor(offsetX / tileSizeRef.current);
    const ty = Math.floor(offsetY / tileSizeRef.current);
    if (tx < 0 || ty < 0 || tx >= cols || ty >= rows) return null;
    return grid.get(keyOf(tx, ty)) ?? null;
  }

  function shouldUseTouchNavigation(pointerType: string) {
    return touchMode && pointerType === "touch" && !touchEditMode;
  }

  function hasConflictingPointerGesture(pointerId: number) {
    const panStart = panStartRef.current;
    if (panStart?.source === "pointer" && panStart.pointerId !== pointerId) {
      return true;
    }
    return activePaintPointersRef.current.size > 0 && !activePaintPointersRef.current.has(pointerId);
  }

  function hasActivePointerGesture(pointerId?: number) {
    const panStart = panStartRef.current;
    if (panStart?.source === "pointer" && (pointerId == null || panStart.pointerId === pointerId)) {
      return true;
    }
    return pointerId == null
      ? activePaintPointersRef.current.size > 0
      : activePaintPointersRef.current.has(pointerId);
  }

  function updateRoadSnap(tile: Tile) {
    const start = roadSnapStartRef.current;
    if (!start) return;
    const dx = tile.x - start.x;
    const dy = tile.y - start.y;
    const keys = new Set<string>();
    if (Math.abs(dx) >= Math.abs(dy)) {
      const minX = Math.min(start.x, tile.x);
      const maxX = Math.max(start.x, tile.x);
      for (let x = minX; x <= maxX; x++) keys.add(keyOf(x, start.y));
    } else {
      const minY = Math.min(start.y, tile.y);
      const maxY = Math.max(start.y, tile.y);
      for (let y = minY; y <= maxY; y++) keys.add(keyOf(start.x, y));
    }
    roadSnapPreviewRef.current = keys;
    setRoadSnapPreview(new Set(keys));
  }

  function onViewportPointerDown(e: React.PointerEvent<HTMLDivElement>) {
    const tool = activeToolRef.current;
    const mode = paintModeRef.current;
    if (e.button === 2) return;
    if (spaceDownRef.current) return; // space+drag handled by startPan
    if (hasConflictingPointerGesture(e.pointerId)) {
      e.preventDefault();
      return;
    }
    updateCanvasHover(null);
    setSurveyTooltip(null);
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    if (tool === "none" || shouldUseTouchNavigation(e.pointerType)) {
      if (e.pointerType === "pen") {
        e.preventDefault();
        const tile = tileAtPointer(e.clientX, e.clientY);
        if (tile) setSelectedTile(tile);
        return;
      }
      // left-click drag pans; click without drag selects tile
      e.preventDefault();
      const viewport = viewportRef.current;
      if (!viewport) return;
      setIsPanning(true);
      stopCameraAnimation();
      panStartRef.current = { x: e.clientX, y: e.clientY, left: cameraRef.current.x, top: cameraRef.current.y, source: "pointer", pointerId: e.pointerId };
      panVelocityRef.current = { lastX: e.clientX, lastY: e.clientY, lastTime: performance.now(), vx: 0, vy: 0 };
      return;
    }
    const tile = tileAtPointer(e.clientX, e.clientY);
    if (!tile) return;
    e.preventDefault();
    if (tool === "road" && mode === "mark") {
      pushHistory();
      activePaintPointersRef.current = new Set([e.pointerId]);
      roadSnapStartRef.current = { x: tile.x, y: tile.y };
      const startKey = keyOf(tile.x, tile.y);
      roadSnapPreviewRef.current = new Set([startKey]);
      setRoadSnapPreview(new Set([startKey]));
      setIsPainting(true);
    } else {
      activePaintPointersRef.current = new Set([e.pointerId]);
      beginPaint(tile);
    }
  }

  function onViewportPointerMove(e: React.PointerEvent<HTMLDivElement>) {
    // Pan is synchronous — no RAF throttle
    if (panStartRef.current?.source === "pointer" && panStartRef.current.pointerId === e.pointerId && viewportRef.current) {
      const dx = e.clientX - panStartRef.current.x;
      const dy = e.clientY - panStartRef.current.y;
      const now = performance.now();
      const sample = panVelocityRef.current;
      const dt = Math.max(8, now - sample.lastTime);
      const instantaneousVx = clampNumber(((sample.lastX - e.clientX) / dt) * PAN_SAMPLE_MS, -MAX_PAN_VELOCITY, MAX_PAN_VELOCITY);
      const instantaneousVy = clampNumber(((sample.lastY - e.clientY) / dt) * PAN_SAMPLE_MS, -MAX_PAN_VELOCITY, MAX_PAN_VELOCITY);
      sample.vx += (instantaneousVx - sample.vx) * PAN_VELOCITY_SMOOTHING;
      sample.vy += (instantaneousVy - sample.vy) * PAN_VELOCITY_SMOOTHING;
      sample.lastX = e.clientX;
      sample.lastY = e.clientY;
      sample.lastTime = now;
      setCameraPosition(panStartRef.current.left - dx, panStartRef.current.top - dy);
      return;
    }
    // Survey pin hover detection
    if (!hasActivePointerGesture(e.pointerId) && showSurveys && viewportRef.current) {
      const vp = viewportRef.current;
      const rect = vp.getBoundingClientRect();
      const viewportOffset = getViewportWorldOffset(tileSizeRef.current);
      const canvasX = e.clientX - rect.left - viewportOffset.x + cameraRef.current.x - MAP_CANVAS_MARGIN;
      const canvasY = e.clientY - rect.top - viewportOffset.y + cameraRef.current.y - MAP_CANVAS_MARGIN;
      const MARGIN = 8;
      const PIN_R = Math.max(5, Math.min(14, tileSize * 1.6));
      let found: SurveyDef[] | null = null;
      let foundScreenX = 0, foundScreenY = 0;
      const tileX = Math.floor(canvasX / tileSizeRef.current);
      const tileY = Math.floor(canvasY / tileSizeRef.current);
      const centerNx = clampNumber(Math.floor((tileX * 10) / cols), 0, 9);
      const centerNy = clampNumber(Math.floor((tileY * 10) / rows), 0, 9);
      const nxStart = Math.max(0, centerNx - 2);
      const nxEnd = Math.min(9, centerNx + 2);
      const nyStart = Math.max(0, centerNy - 2);
      const nyEnd = Math.min(9, centerNy + 2);

      outer: for (let ny = nyStart; ny <= nyEnd; ny += 1) {
        for (let nx = nxStart; nx <= nxEnd; nx += 1) {
          const zoneKey = `${nx},${ny}`;
          const zoneDefs = surveyZoneData.defsByZone.get(zoneKey);
          const zoneCats = surveyZoneData.catsByZone.get(zoneKey);
          if (!zoneDefs || !zoneCats) continue;
          const cats = zoneCats.filter((cat) => visibleSurveyCats.has(cat));
          if (!cats.length) continue;
          const matching = zoneDefs.filter((s) => visibleSurveyCats.has(s.category));
          if (!matching.length) continue;
          const nCX = Math.floor((Math.floor((nx * cols) / 10) + Math.floor(((nx + 1) * cols) / 10) - 1) / 2);
          const nCY = Math.floor((Math.floor((ny * rows) / 10) + Math.floor(((ny + 1) * rows) / 10) - 1) / 2);
          const baseX = nCX * tileSize + MARGIN + tileSize / 2;
          const baseY = nCY * tileSize + MARGIN + tileSize / 2;
          const spacing = PIN_R * 2.2;
          const totalW = (cats.length - 1) * spacing;
          for (let i = 0; i < cats.length; i++) {
            const pinX = baseX - totalW / 2 + i * spacing;
            const pinY = baseY - PIN_R * 3.5;
            const dist = Math.hypot(canvasX - pinX, canvasY - pinY);
            if (dist <= PIN_R + 4) {
              found = matching;
              foundScreenX = e.clientX;
              foundScreenY = e.clientY;
              break outer;
            }
          }
        }
      }
      if (found) {
        setSurveyTooltip({ x: foundScreenX, y: foundScreenY, items: found });
      } else {
        setSurveyTooltip(null);
      }
    }
    // Drawing uses RAF throttle
    pendingPointerRef.current = { x: e.clientX, y: e.clientY, pointerId: e.pointerId };
    if (rafIdRef.current !== null) return;
    rafIdRef.current = requestAnimationFrame(() => {
      rafIdRef.current = null;
      const pos = pendingPointerRef.current;
      if (!pos) return;
      pendingPointerRef.current = null;
      if (isPainting && activePaintPointersRef.current.size > 0 && !activePaintPointersRef.current.has(pos.pointerId)) return;
      if (hasActivePointerGesture(pos.pointerId)) return;
      const tile = tileAtPointer(pos.x, pos.y);
      if (!tile) return;
      updateCanvasHover(tile);
      if (!isPainting) return;
      if (activeToolRef.current === "road" && paintModeRef.current === "mark") {
        updateRoadSnap(tile);
      } else {
        continuePaint(tile);
      }
    });
  }

  function getTileBackground(tile: Tile) {
    if (!isLand(tile)) return currentTheme.water;
    if (tile.fullTerrainId != null && RAW_TERRAIN_COLORS[tile.fullTerrainId]) {
      return RAW_TERRAIN_COLORS[tile.fullTerrainId];
    }
    return TERRAIN_COLORS[tile.terrain];
  }

  function getTileTextColor(tile: Tile) {
    if (!isLand(tile)) return "transparent";
    if (tile.terrain === "swamp" || tile.terrain === "volcano" || tile.terrain === "ground") return "white";
    return "#111827";
  }

  function getBoxShadow(tile: Tile) {
    const key = keyOf(tile.x, tile.y);
    const outlined = outlinedTiles.has(key) && layers.poi;
    const penColor_tile = penTiles.get(key);
    const penned = !!penColor_tile && layers.poi;
    const deployed = getDeploymentCount(key) > 0 && layers.deployments;
    const reclaimed = reclaimedTiles.has(key) && layers.reclaimed;
    const road = roadTiles.has(key) && layers.roads;
    const preview = previewKeys.has(key);
    const parts: string[] = [];

    if (road) {
      parts.push(`inset 0 0 0 9999px ${ROAD_COLOR}`);
      parts.push(`inset 0 0 0 1px ${ROAD_BORDER}`);
    }

    if (reclaimed) {
      parts.push(`inset 0 0 0 9999px ${RECLAIMED_FILL}`);
      parts.push(`inset 0 0 0 1px ${RECLAIMED_BORDER}`);
    }

    if (deployed) {
      parts.push(`inset 0 0 0 9999px ${DEPLOY_FILL}`);
      parts.push(`inset 0 0 0 1px ${DEPLOY_BORDER}`);
    }


    if (outlined) {
      if (!outlinedTiles.has(keyOf(tile.x, tile.y - 1))) parts.push(`inset 0 2px 0 0 ${drawAreaColor}`);
      if (!outlinedTiles.has(keyOf(tile.x + 1, tile.y))) parts.push(`inset -2px 0 0 0 ${drawAreaColor}`);
      if (!outlinedTiles.has(keyOf(tile.x, tile.y + 1))) parts.push(`inset 0 -2px 0 0 ${drawAreaColor}`);
      if (!outlinedTiles.has(keyOf(tile.x - 1, tile.y))) parts.push(`inset 2px 0 0 0 ${drawAreaColor}`);
    }

    if (penned) {
      parts.push(`inset 0 0 0 9999px ${penColor_tile}`);
    }

    if (preview) {
      if (activeTool === "draw_area") {
        parts.push("inset 0 0 0 9999px rgba(251,146,60,0.10)");
        if (!previewKeys.has(keyOf(tile.x, tile.y - 1))) parts.push(`inset 0 2px 0 0 ${PREVIEW_ORANGE}`);
        if (!previewKeys.has(keyOf(tile.x + 1, tile.y))) parts.push(`inset -2px 0 0 0 ${PREVIEW_ORANGE}`);
        if (!previewKeys.has(keyOf(tile.x, tile.y + 1))) parts.push(`inset 0 -2px 0 0 ${PREVIEW_ORANGE}`);
        if (!previewKeys.has(keyOf(tile.x - 1, tile.y))) parts.push(`inset 2px 0 0 0 ${PREVIEW_ORANGE}`);
      } else if (activeTool === "pen") {
        parts.push(`inset 0 0 0 9999px ${penColor}88`);
      } else if (activeTool === "deploy") {
        parts.push("inset 0 0 0 9999px rgba(34,211,238,0.12)");
        parts.push(`inset 0 0 0 1px ${DEPLOY_BORDER}`);
      } else if (activeTool === "move_deploy") {
        parts.push("inset 0 0 0 9999px rgba(34,211,238,0.16)");
        parts.push(`inset 0 0 0 1px ${DEPLOY_BORDER}`);
      } else if (activeTool === "reclaim") {
        parts.push("inset 0 0 0 9999px rgba(255,255,255,0.12)");
        parts.push("inset 0 0 0 1px rgba(255,255,255,0.92)");
      } else if (activeTool === "road") {
        parts.push("inset 0 0 0 9999px rgba(100,116,139,0.35)");
        parts.push(`inset 0 0 0 1px ${ROAD_BORDER}`);
      } else if (activeTool === "chaos_setup") {
        parts.push("inset 0 0 0 9999px rgba(250,204,21,0.16)");
        parts.push("inset 0 0 0 1px rgba(250,204,21,0.85)");
      }
    }

    if (selectedTile && selectedTile.x === tile.x && selectedTile.y === tile.y) {
      parts.push("inset 0 0 0 1px #111827");
    }

    return parts.length ? parts.join(",") : undefined;
  }

  function getCursor() {
    if (isPanning) return "grabbing";
    if (spaceDownRef.current) return "grab";
    if (activeTool === "none") return "grab";
    if (activeTool === "deploy") return "copy";
    if (activeTool === "townhall") return paintMode === "mark" ? "copy" : "alias";
    if (activeTool === "chaos_setup") return paintMode === "mark" ? "copy" : "alias";
    if (activeTool === "reclaim") return reclaimMode === "reclaim" ? "cell" : "not-allowed";
    if (activeTool === "draw_area") return paintMode === "mark" ? "crosshair" : "alias";
    if (activeTool === "pen") return paintMode === "mark" ? "crosshair" : "alias";
    if (activeTool === "road") return paintMode === "mark" ? "crosshair" : "alias";
    return "default";
  }

  function panBy(dx: number, dy: number) {
    setCameraPosition(cameraRef.current.x + dx, cameraRef.current.y + dy);
  }

  function getAutoFitTileSize() {
    const vp = viewportRef.current;
    if (!vp) return DEFAULT_TILE_SIZE;
    const margin = MAP_CANVAS_MARGIN * 2; // canvas margin on both sides
    const availableWidth = Math.max(1, vp.clientWidth - margin);
    const availableHeight = Math.max(1, vp.clientHeight - margin);
    const fit = Math.min(availableWidth / cols, availableHeight / rows);
    return Math.max(getMinTileSize(), Math.min(MAX_TILE_SIZE, fit));
  }

  useLayoutEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport) return;

    const updateSize = () => {
      const autoSize = getAutoFitTileSize();
      if (autoSize < tileSizeRef.current) {
        setCameraPosition(cameraRef.current.x, cameraRef.current.y, autoSize);
      } else {
        scheduleRender();
        scheduleUiSync();
      }
    };

    updateSize();
    const observer = new ResizeObserver(updateSize);
    observer.observe(viewport);
    return () => observer.disconnect();
  }, [cols, rows, touchMode, isFullscreen]);

  const tileSizeRef = useRef(tileSize);
  useEffect(() => { tileSizeRef.current = tileSize; }, [tileSize]);

  function getMinTileSize() {
    return isFullscreen || touchMode ? MOBILE_MIN_TILE_SIZE : MIN_TILE_SIZE;
  }

  useEffect(() => {
    const minTileSize = getMinTileSize();
    if (tileSizeRef.current < minTileSize) {
      setCameraPosition(cameraRef.current.x, cameraRef.current.y, minTileSize);
    }
    scheduleRender();
    requestAnimationFrame(syncMapScroll);
  }, [isFullscreen, touchMode]);

  useLayoutEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport) return;
    const wheelViewport = viewport;
    function onWheel(e: WheelEvent) {
      e.preventDefault();
      zoomAt(e.clientX, e.clientY, e.deltaY < 0 ? 1.14 : 1 / 1.14);
    }
    wheelViewport.addEventListener("wheel", onWheel, { passive: false });
    return () => wheelViewport.removeEventListener("wheel", onWheel);
  }, [touchMode, isFullscreen]);

  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport) return;
    const touchViewport = viewport;
    let lastDist = 0;
    let lastMidX = 0;
    let lastMidY = 0;
    function onTouchStart(e: TouchEvent) {
      if (e.touches.length !== 2) return;
      stopCameraAnimation();
      panStartRef.current = null;
      setIsPanning(false);
      const t0 = e.touches[0], t1 = e.touches[1];
      lastDist = Math.hypot(t1.clientX - t0.clientX, t1.clientY - t0.clientY);
      lastMidX = (t0.clientX + t1.clientX) / 2;
      lastMidY = (t0.clientY + t1.clientY) / 2;
    }
    function onTouchMove(e: TouchEvent) {
      if (e.touches.length !== 2) return;
      e.preventDefault();
      panStartRef.current = null;
      const t0 = e.touches[0], t1 = e.touches[1];
      const dist = Math.hypot(t1.clientX - t0.clientX, t1.clientY - t0.clientY);
      if (!lastDist) { lastDist = dist; return; }
      const factor = dist / lastDist;
      lastDist = dist;
      const previousMidX = lastMidX;
      const previousMidY = lastMidY;
      const midX = (t0.clientX + t1.clientX) / 2;
      const midY = (t0.clientY + t1.clientY) / 2;
      const currentSize = tileSizeRef.current;
      const rect = touchViewport.getBoundingClientRect();
      const viewportOffset = getViewportWorldOffset(currentSize);
      const offsetX = previousMidX - rect.left - viewportOffset.x + cameraRef.current.x;
      const offsetY = previousMidY - rect.top - viewportOffset.y + cameraRef.current.y;
      const nextTileSize = Math.max(getMinTileSize(), Math.min(MAX_TILE_SIZE, currentSize * factor));
      const scale = nextTileSize / currentSize;
      setCameraPosition(offsetX * scale - (midX - rect.left - viewportOffset.x), offsetY * scale - (midY - rect.top - viewportOffset.y), nextTileSize);
      lastMidX = midX;
      lastMidY = midY;
    }
    touchViewport.addEventListener("touchstart", onTouchStart, { passive: false });
    touchViewport.addEventListener("touchmove", onTouchMove, { passive: false });
    return () => {
      touchViewport.removeEventListener("touchstart", onTouchStart);
      touchViewport.removeEventListener("touchmove", onTouchMove);
    };
  }, [touchMode, isFullscreen]);

  function startPan(e: React.MouseEvent<HTMLDivElement>) {
    // right-click or space+drag pan via mouse events
    if (e.button !== 2 && !spaceDownRef.current) return;
    e.preventDefault();
    const viewport = viewportRef.current;
    if (!viewport) return;
    setIsPanning(true);
    stopCameraAnimation();
      panStartRef.current = {
      x: e.clientX,
      y: e.clientY,
      left: cameraRef.current.x,
      top: cameraRef.current.y,
      source: "mouse",
        pointerId: undefined,
    };
    panVelocityRef.current = { lastX: e.clientX, lastY: e.clientY, lastTime: performance.now(), vx: 0, vy: 0 };
  }

  function movePan(e: React.MouseEvent<HTMLDivElement>) {
    if (!panStartRef.current || panStartRef.current.source !== "mouse" || !viewportRef.current) return;
    const dx = e.clientX - panStartRef.current.x;
    const dy = e.clientY - panStartRef.current.y;
    const now = performance.now();
    const sample = panVelocityRef.current;
    const dt = Math.max(8, now - sample.lastTime);
    const instantaneousVx = clampNumber(((sample.lastX - e.clientX) / dt) * PAN_SAMPLE_MS, -MAX_PAN_VELOCITY, MAX_PAN_VELOCITY);
    const instantaneousVy = clampNumber(((sample.lastY - e.clientY) / dt) * PAN_SAMPLE_MS, -MAX_PAN_VELOCITY, MAX_PAN_VELOCITY);
    sample.vx += (instantaneousVx - sample.vx) * PAN_VELOCITY_SMOOTHING;
    sample.vy += (instantaneousVy - sample.vy) * PAN_VELOCITY_SMOOTHING;
    sample.lastX = e.clientX;
    sample.lastY = e.clientY;
    sample.lastTime = now;
    setCameraPosition(panStartRef.current.left - dx, panStartRef.current.top - dy);
  }

  function centerOn(x: number, y: number) {
    const viewport = viewportRef.current;
    if (!viewport) return;
    setCameraPosition(
      Math.max(0, x * tileSize - viewport.clientWidth / 2),
      Math.max(0, y * tileSize - viewport.clientHeight / 2)
    );
  }

  function zoomAt(clientX: number, clientY: number, factor: number) {
    const viewport = viewportRef.current;
    if (!viewport) return;
    const camera = cameraRef.current;
    const currentSize = tileSizeFromZoom(camera.targetZoom);
    const rect = viewport.getBoundingClientRect();
    const viewportOffset = getViewportWorldOffset(currentSize);
    const offsetX = clientX - rect.left - viewportOffset.x + camera.targetX;
    const offsetY = clientY - rect.top - viewportOffset.y + camera.targetY;
    const nextTileSize = Math.max(getMinTileSize(), Math.min(MAX_TILE_SIZE, currentSize * factor));
    if (nextTileSize === currentSize) return;
    const scale = nextTileSize / currentSize;
    camera.targetZoom = clampNumber(nextTileSize / BASE_TILE_SIZE, getMinZoom(), getMaxZoom());
    camera.targetX = offsetX * scale - (clientX - rect.left - viewportOffset.x);
    camera.targetY = offsetY * scale - (clientY - rect.top - viewportOffset.y);
    camera.vx = 0;
    camera.vy = 0;
    clampCamera(camera);
    ensureCameraAnimation();
  }

  function zoomAtCenter(factor: number) {
    const viewport = viewportRef.current;
    if (!viewport) return;
    const rect = viewport.getBoundingClientRect();
    zoomAt(rect.left + rect.width / 2, rect.top + rect.height / 2, factor);
  }

  function fitMapToViewport() {
    const viewport = viewportRef.current;
    if (!viewport) return;
    const nextTileSize = getAutoFitTileSize();
    const camera = cameraRef.current;
    camera.targetZoom = nextTileSize / BASE_TILE_SIZE;
    camera.targetX = 0;
    camera.targetY = 0;
    camera.vx = 0;
    camera.vy = 0;
    ensureCameraAnimation();
  }

  function selectMapTool(tool: ToolType) {
    setActiveToolImmediate((prev) => toggleTool(prev, tool));
    setFloatingToolsOpen(false);
    if (tool === "draw_area" && brushSize < 2) setBrushSize(2);
  }

  function applyMapViewMode(mode: MapViewMode) {
    setMapViewMode(mode);
    setLayers(mode === "visual" ? { ...VISUAL_MAP_LAYERS } : { ...LOGIC_MAP_LAYERS });
    setShowSurveys(mode === "logic");
    scheduleRender();
    flashNotice(mode === "visual" ? "Visual map mode" : "Logic overlay mode");
  }

  async function exportToClipboard() {
    try {
      const text = encodeState(outlinedTiles, reclaimedTiles, deployedTiles, roadTiles, penTiles, chaosSetupTiles);
      await navigator.clipboard.writeText(text);
      flashNotice("Map copied to clipboard");
    } catch {
      flashNotice("Clipboard copy failed");
    }
  }

  async function importFromClipboard() {
    try {
      const text = await navigator.clipboard.readText();
      if (!text.trim()) {
        flashNotice("No data in clipboard");
        return;
      }
      pushHistory();
      const decoded = decodeState(text);
      const decodedPen = new Map((decoded.penned ?? []).map((e) => [e.k, e.c]));
      const decodedChaosSetup = new Map((decoded.chaosSetup ?? []).map((e) => [e.k, e.piece]));
      outlinedTilesRef.current = decoded.outlined;
      reclaimedTilesRef.current = decoded.reclaimed;
      deployedTilesRef.current = decoded.deployed;
      roadTilesRef.current = decoded.roads;
      penTilesRef.current = decodedPen;
      chaosSetupTilesRef.current = decodedChaosSetup;
      setOutlinedTiles(decoded.outlined);
      setReclaimedTiles(decoded.reclaimed);
      setDeployedTiles(decoded.deployed);
      setRoadTiles(decoded.roads);
      setPenTiles(decodedPen);
      setChaosSetupTiles(decodedChaosSetup);
      scheduleRender();
      flashNotice("Map imported from clipboard");
    } catch {
      flashNotice("No data in clipboard");
    }
  }

  function saveSlot(slot: number) {
    const text = encodeState(outlinedTiles, reclaimedTiles, deployedTiles, roadTiles, penTiles, chaosSetupTiles);
    localStorage.setItem(`${STORAGE_PREFIX}:slot:${slot}`, text);
    flashNotice(`Saved slot ${slot}`);
  }

  function loadSlot(slot: number) {
    const text = localStorage.getItem(`${STORAGE_PREFIX}:slot:${slot}`);
    if (!text) {
      flashNotice(`Slot ${slot} is empty`);
      return;
    }
    try {
      pushHistory();
      const decoded = decodeState(text);
      const decodedPen = new Map((decoded.penned ?? []).map((e) => [e.k, e.c]));
      const decodedChaosSetup = new Map((decoded.chaosSetup ?? []).map((e) => [e.k, e.piece]));
      outlinedTilesRef.current = decoded.outlined;
      reclaimedTilesRef.current = decoded.reclaimed;
      deployedTilesRef.current = decoded.deployed;
      roadTilesRef.current = decoded.roads;
      penTilesRef.current = decodedPen;
      chaosSetupTilesRef.current = decodedChaosSetup;
      setOutlinedTiles(decoded.outlined);
      setReclaimedTiles(decoded.reclaimed);
      setDeployedTiles(decoded.deployed);
      setRoadTiles(decoded.roads);
      setPenTiles(decodedPen);
      setChaosSetupTiles(decodedChaosSetup);
      scheduleRender();
      flashNotice(`Loaded slot ${slot}`);
    } catch {
      flashNotice(`Slot ${slot} is invalid`);
    }
  }

  function clearSlot(slot: number) {
    localStorage.removeItem(`${STORAGE_PREFIX}:slot:${slot}`);
    flashNotice(`Cleared slot ${slot}`);
  }

  async function lockLandscapeOrientation() {
    if (typeof window === "undefined" || !touchMode) return;
    const orientation = (window.screen as any)?.orientation ?? (window.screen as any)?.mozOrientation ?? (window.screen as any)?.msOrientation;
    if (!orientation?.lock) {
      flashNotice("Landscape fullscreen not supported by this browser.");
      return;
    }

    try {
      await orientation.lock("landscape");
    } catch {
      flashNotice("Landscape fullscreen lock failed on this browser.");
    }
  }

  function toggleFullscreen() {
    markPerf(isFullscreen || document.fullscreenElement || isFullscreenFallback ? "exit fullscreen" : "enter fullscreen");
    if (!document.fullscreenElement && !isFullscreenFallback) {
      const requestFullscreen = fullscreenContainerRef.current?.requestFullscreen?.bind(fullscreenContainerRef.current);
      if (requestFullscreen) {
        requestFullscreen()
          .then(() => {
            setIsFullscreenFallback(false);
            setIsFullscreen(true);
            lockLandscapeOrientation();
          })
          .catch(() => {
            setIsFullscreenFallback(true);
            setIsFullscreen(true);
          });
      } else {
        setIsFullscreenFallback(true);
        setIsFullscreen(true);
      }
    } else if (document.fullscreenElement) {
      document.exitFullscreen()
        .catch(() => {
          setIsFullscreenFallback(false);
          setIsFullscreen(false);
        })
        .finally(() => {
          const orientation = (window.screen as any)?.orientation ?? (window.screen as any)?.mozOrientation ?? (window.screen as any)?.msOrientation;
          orientation?.unlock?.();
        });
    } else {
      setIsFullscreenFallback(false);
      setIsFullscreen(false);
    }
  }

  function renderScreenshotCanvas(options: ScreenshotOptions) {
    const renderScale = options.area === "viewport"
      ? Math.max(6, Math.min(tileSizeRef.current, 18))
      : Math.max(4, Math.min(tileSizeRef.current, 10));
    const canvas = document.createElement("canvas");
    canvas.width = cols * renderScale;
    canvas.height = rows * renderScale;
    const ctx = canvas.getContext("2d");
    if (!ctx) return null;

    ctx.fillStyle = currentTheme.water;
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    for (let y = 0; y < rows; y += 1) {
      for (let x = 0; x < cols; x += 1) {
        const tile = grid.get(keyOf(x, y));
        if (!tile) continue;
        const key = keyOf(x, y);
        const land = isLand(tile);
        const px = x * renderScale;
        const py = y * renderScale;

        ctx.fillStyle = getTileBackground(tile);
        ctx.fillRect(px, py, renderScale, renderScale);

        if (land && options.includeLevels) {
          const normalizedLevel = Math.min(tile.level, 6000) / 6000;
          const levelShade = 0.10 + normalizedLevel * 0.52;
          ctx.fillStyle = `rgba(37,99,235,${Math.max(0.10, levelShade * 0.42).toFixed(2)})`;
          ctx.fillRect(px, py, renderScale, renderScale);
          ctx.fillStyle = `rgba(17,24,39,${Math.max(0.16, levelShade * 0.86).toFixed(2)})`;
          ctx.fillRect(px, py, renderScale, renderScale);
        }

        if (options.includeGrid) {
          ctx.strokeStyle = land ? "rgba(0,0,0,0.10)" : currentTheme.waterBorder;
          ctx.lineWidth = 1;
          ctx.strokeRect(px + 0.5, py + 0.5, renderScale - 1, renderScale - 1);
        }

        if (options.includeRoads && roadTiles.has(key)) {
          drawRoadTexture(ctx, px, py, renderScale);
        }

        if (options.includeReclaimed && reclaimedTiles.has(key)) {
          ctx.fillStyle = RECLAIMED_FILL;
          ctx.fillRect(px, py, renderScale, renderScale);
          ctx.strokeStyle = RECLAIMED_BORDER;
          ctx.lineWidth = 1;
          ctx.strokeRect(px + 0.5, py + 0.5, renderScale - 1, renderScale - 1);
          ctx.strokeStyle = RECLAIMED_STRIPE;
          ctx.beginPath();
          ctx.moveTo(px, py + renderScale * 0.7);
          ctx.lineTo(px + renderScale * 0.7, py);
          ctx.moveTo(px + renderScale * 0.3, py + renderScale);
          ctx.lineTo(px + renderScale, py + renderScale * 0.3);
          ctx.stroke();
        }

        if (options.includeDeployments && getDeploymentCount(key) > 0) {
          ctx.fillStyle = DEPLOY_FILL;
          ctx.fillRect(px, py, renderScale, renderScale);
          ctx.strokeStyle = DEPLOY_BORDER;
          ctx.lineWidth = 1;
          ctx.strokeRect(px + 0.5, py + 0.5, renderScale - 1, renderScale - 1);
        }

        if (options.includeDrawArea && outlinedTiles.has(key)) {
          ctx.strokeStyle = drawAreaColor;
          ctx.lineWidth = Math.max(1, Math.floor(renderScale / 4));
          if (!outlinedTiles.has(keyOf(x, y - 1))) { ctx.beginPath(); ctx.moveTo(px, py); ctx.lineTo(px + renderScale, py); ctx.stroke(); }
          if (!outlinedTiles.has(keyOf(x + 1, y))) { ctx.beginPath(); ctx.moveTo(px + renderScale, py); ctx.lineTo(px + renderScale, py + renderScale); ctx.stroke(); }
          if (!outlinedTiles.has(keyOf(x, y + 1))) { ctx.beginPath(); ctx.moveTo(px, py + renderScale); ctx.lineTo(px + renderScale, py + renderScale); ctx.stroke(); }
          if (!outlinedTiles.has(keyOf(x - 1, y))) { ctx.beginPath(); ctx.moveTo(px, py); ctx.lineTo(px, py + renderScale); ctx.stroke(); }
        }

        if (options.includePen && penTiles.has(key)) {
          ctx.fillStyle = penTiles.get(key)!;
          ctx.fillRect(px, py, renderScale, renderScale);
        }
      }
    }

    if (options.includeFacilities && renderScale >= 5.5) {
      ctx.save();
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      for (let ny = 0; ny < 10; ny += 1) {
        for (let nx = 0; nx < 10; nx += 1) {
          const names = ZONE_FACILITY_NAMES.get(`${nx},${ny}`);
          if (!names?.length) continue;
          const nSX = Math.floor((nx * cols) / 10);
          const nEX = Math.floor(((nx + 1) * cols) / 10) - 1;
          const nSY = Math.floor((ny * rows) / 10);
          const nEY = Math.floor(((ny + 1) * rows) / 10) - 1;
          const zoneW = (nEX - nSX + 1) * renderScale;
          const zoneH = (nEY - nSY + 1) * renderScale;
          const cxPx = nSX * renderScale + zoneW / 2;
          const cyPx = nSY * renderScale + zoneH / 2;
          const maxW = zoneW * 0.88;
          let fSize = Math.max(8, Math.min(44, Math.floor(zoneW * 0.13)));
          for (const name of names) {
            ctx.font = `bold ${fSize}px Arial, sans-serif`;
            while (ctx.measureText(name).width > maxW && fSize > 7) {
              fSize -= 1;
              ctx.font = `bold ${fSize}px Arial, sans-serif`;
            }
          }
          ctx.font = `bold ${fSize}px Arial, sans-serif`;
          const lineH = fSize * 1.35;
          const totalH = names.length * lineH;
          let yOff = cyPx - totalH / 2 + lineH / 2;
          for (const name of names) {
            ctx.shadowColor = "rgba(0,0,0,0.95)";
            ctx.shadowBlur = Math.max(3, fSize * 0.45);
            ctx.fillStyle = "#fde68a";
            ctx.fillText(name, cxPx, yOff);
            ctx.shadowBlur = 0;
            yOff += lineH;
          }
        }
      }
      ctx.restore();
    }


    if (options.includeSurveys) {
      const margin = 8;
      const pinRadius = Math.max(5, Math.min(14, renderScale * 1.6));
      ctx.save();
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      for (let ny = 0; ny < 10; ny += 1) {
        for (let nx = 0; nx < 10; nx += 1) {
          const zoneKey = `${nx},${ny}`;
          const zoneCats = surveyZoneData.catsByZone.get(zoneKey);
          if (!zoneCats) continue;
          const cats = zoneCats.filter((cat) => visibleSurveyCats.has(cat));
          if (!cats.length) continue;
          const nSX = Math.floor((nx * cols) / 10);
          const nEX = Math.floor(((nx + 1) * cols) / 10) - 1;
          const nSY = Math.floor((ny * rows) / 10);
          const nEY = Math.floor(((ny + 1) * rows) / 10) - 1;
          const nCX = Math.floor((nSX + nEX) / 2);
          const nCY = Math.floor((nSY + nEY) / 2);
          const baseX = nCX * renderScale + margin + renderScale / 2;
          const baseY = nCY * renderScale + margin + renderScale / 2;
          const spacing = pinRadius * 2.2;
          const totalW = (cats.length - 1) * spacing;
          cats.forEach((cat, index) => {
            const pinX = baseX - totalW / 2 + index * spacing;
            const pinY = baseY - pinRadius * 3.5;
            const color = SURVEY_CAT_COLORS[cat];
            ctx.shadowColor = "rgba(0,0,0,0.6)";
            ctx.shadowBlur = 4;
            ctx.beginPath();
            ctx.arc(pinX, pinY, pinRadius, 0, Math.PI * 2);
            ctx.fillStyle = color;
            ctx.fill();
            ctx.lineWidth = 1.5;
            ctx.strokeStyle = "rgba(255,255,255,0.9)";
            ctx.stroke();
            ctx.beginPath();
            ctx.moveTo(pinX, pinY + pinRadius);
            ctx.lineTo(pinX, pinY + pinRadius + pinRadius * 0.8);
            ctx.strokeStyle = color;
            ctx.lineWidth = Math.max(1.5, pinRadius * 0.35);
            ctx.stroke();
            ctx.shadowBlur = 0;
            ctx.font = `bold ${Math.max(7, Math.floor(pinRadius * 0.9))}px sans-serif`;
            ctx.fillStyle = "#fff";
            ctx.fillText(SURVEY_CAT_LABELS[cat], pinX, pinY);
          });
        }
      }
      ctx.restore();
    }

    if (options.includeChaosSetup) {
      chaosSetupTiles.forEach((piece, placedKey) => {
        const [x, y] = placedKey.split(",").map(Number);
        const px = x * renderScale;
        const py = y * renderScale;
        if (piece === "info_board") {
          ctx.fillStyle = "#8b5a2b";
          ctx.fillRect(px + renderScale * 0.44, py + renderScale * 0.45, Math.max(1, renderScale * 0.12), renderScale * 0.45);
          ctx.fillStyle = "#d4a95f";
          ctx.fillRect(px + renderScale * 0.18, py + renderScale * 0.12, renderScale * 0.64, renderScale * 0.36);
          ctx.strokeStyle = "#8b5a2b";
          ctx.lineWidth = Math.max(1, renderScale * 0.05);
          ctx.strokeRect(px + renderScale * 0.18, py + renderScale * 0.12, renderScale * 0.64, renderScale * 0.36);
        } else {
          ctx.fillStyle = "#6b7280";
          ctx.beginPath();
          ctx.moveTo(px + renderScale, py + renderScale);
          ctx.lineTo(px + renderScale * 1.75, py + renderScale * 0.95);
          ctx.lineTo(px + renderScale * 1.55, py + renderScale * 0.2);
          ctx.lineTo(px + renderScale, py + renderScale * 0.05);
          ctx.lineTo(px + renderScale * 0.4, py + renderScale * 0.4);
          ctx.lineTo(px + renderScale * 0.1, py + renderScale);
          ctx.closePath();
          ctx.fill();
        }
      });
    }

    if (options.includeTownHall && townhallPlacement) {
      townhallPlacementKeys.forEach((key) => {
        const [x, y] = key.split(",").map(Number);
        const px = x * renderScale;
        const py = y * renderScale;
        ctx.fillStyle = TOWNHALL_FOOTPRINT_FILL;
        ctx.fillRect(px, py, renderScale, renderScale);
        ctx.strokeStyle = TOWNHALL_FOOTPRINT_BORDER;
        ctx.lineWidth = 1;
        ctx.strokeRect(px + 0.5, py + 0.5, renderScale - 1, renderScale - 1);
      });
      if (townhallCoverageBounds) {
        const { minX, minY, maxX, maxY } = townhallCoverageBounds;
        ctx.strokeStyle = TOWNHALL_FOOTPRINT_BORDER;
        ctx.lineWidth = Math.max(1, renderScale * 0.75);
        ctx.setLineDash([Math.max(4, renderScale * 0.5), Math.max(4, renderScale * 0.5)]);
        ctx.strokeRect(
          Math.round(minX * renderScale) + 0.5,
          Math.round(minY * renderScale) + 0.5,
          Math.round((maxX - minX + 1) * renderScale) - 1,
          Math.round((maxY - minY + 1) * renderScale) - 1,
        );
        ctx.setLineDash([]);
      }
    }

    if (options.includeLevels && renderScale >= 5.5) {
      const zonePx = Math.floor((cols / 10) * renderScale);
      const fontSize = Math.max(14, Math.min(72, Math.floor(zonePx * 0.18)));
      ctx.save();
      ctx.font = `bold ${fontSize}px Arial, sans-serif`;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.shadowColor = "rgba(0,0,0,0.98)";
      ctx.shadowBlur = Math.max(6, fontSize * 0.45);
      ctx.fillStyle = "#ffffff";
      for (let ny = 0; ny < 10; ny += 1) {
        for (let nx = 0; nx < 10; nx += 1) {
          const native = NATIVE_MAP[ny]?.[nx];
          if (!native) continue;
          const nSX = Math.floor((nx * cols) / 10);
          const nEX = Math.floor(((nx + 1) * cols) / 10) - 1;
          const nSY = Math.floor((ny * rows) / 10);
          const nEY = Math.floor(((ny + 1) * rows) / 10) - 1;
          const cxPx = nSX * renderScale + (nEX - nSX + 1) * renderScale / 2;
          const cyPx = nSY * renderScale + (nEY - nSY + 1) * renderScale / 2;
          ctx.fillText(String(native.level), cxPx, cyPx);
        }
      }
      ctx.restore();
    }

    if (options.area !== "viewport") return canvas;
    const viewport = viewportRef.current;
    if (!viewport) return canvas;
    const currentTileSize = tileSizeRef.current;
    const viewportOffset = getViewportWorldOffset(currentTileSize);
    const left = Math.max(0, cameraRef.current.x - MAP_CANVAS_MARGIN - viewportOffset.x);
    const top = Math.max(0, cameraRef.current.y - MAP_CANVAS_MARGIN - viewportOffset.y);
    const right = Math.min(cols * currentTileSize, left + viewport.clientWidth);
    const bottom = Math.min(rows * currentTileSize, top + viewport.clientHeight);
    const sourceX = Math.max(0, Math.floor((left / currentTileSize) * renderScale));
    const sourceY = Math.max(0, Math.floor((top / currentTileSize) * renderScale));
    const sourceW = Math.max(1, Math.min(canvas.width - sourceX, Math.ceil(((right - left) / currentTileSize) * renderScale)));
    const sourceH = Math.max(1, Math.min(canvas.height - sourceY, Math.ceil(((bottom - top) / currentTileSize) * renderScale)));
    const croppedCanvas = document.createElement("canvas");
    croppedCanvas.width = sourceW;
    croppedCanvas.height = sourceH;
    const croppedCtx = croppedCanvas.getContext("2d");
    if (!croppedCtx) return canvas;
    croppedCtx.drawImage(canvas, sourceX, sourceY, sourceW, sourceH, 0, 0, sourceW, sourceH);
    return croppedCanvas;
  }

  async function generateScreenshotPreview() {
    try {
      setScreenshotStatus("working");
      const canvas = renderScreenshotCanvas(screenshotOptions);
      if (!canvas) throw new Error("Screenshot canvas could not be created.");
      const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/png"));
      if (!blob) throw new Error("Screenshot export failed.");
      clearScreenshotPreview();
      const fileName = getScreenshotFileName(screenshotOptions);
      const url = URL.createObjectURL(blob);
      setScreenshotPreviewUrl(url);
      setScreenshotPreviewFileName(fileName);
    } catch (error) {
      console.error("Screenshot preview failed", error);
      setNotice("Screenshot preview failed.");
    } finally {
      setScreenshotStatus(null);
    }
  }

  function downloadScreenshot() {
    if (!screenshotPreviewUrl) {
      setNotice("Generate preview first.");
      return;
    }
    const link = document.createElement("a");
    link.href = screenshotPreviewUrl;
    link.download = screenshotPreviewFileName ?? getScreenshotFileName(screenshotOptions);
    link.click();
    setNotice("Screenshot saved.");
    setScreenshotDialogOpen(false);
    clearScreenshotPreview();
  }


  // â”€â”€ Shared toolbar sections (used in both normal panel and fullscreen sidebar) â”€â”€
  // -- Canvas rendering --
  function getBaseTerrainCache(renderedTileSize: number) {
    const roundedTileSize = Math.max(0.25, Math.round(renderedTileSize * 4) / 4);
    const waterEnabled = layers.water;
    const existing = baseTerrainCacheRef.current;
    if (existing && existing.tileSize === roundedTileSize && existing.water === waterEnabled) {
      return existing.canvas;
    }

    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.ceil(cols * roundedTileSize));
    canvas.height = Math.max(1, Math.ceil(rows * roundedTileSize));
    const ctx = canvas.getContext("2d");
    if (!ctx) return canvas;
    ctx.fillStyle = waterEnabled ? currentTheme.water : "#0b1020";
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    for (let cy = 0; cy < rows; cy += 1) {
      for (let cx = 0; cx < cols; cx += 1) {
        const tile = grid.get(keyOf(cx, cy));
        if (!tile) continue;
        const px = Math.round(cx * roundedTileSize);
        const py = Math.round(cy * roundedTileSize);
        const pw = Math.max(1, Math.round((cx + 1) * roundedTileSize) - px);
        const ph = Math.max(1, Math.round((cy + 1) * roundedTileSize) - py);
        ctx.fillStyle = tile.buildable
          ? tile.fullTerrainId != null && RAW_TERRAIN_COLORS[tile.fullTerrainId]
            ? RAW_TERRAIN_COLORS[tile.fullTerrainId]
            : TERRAIN_COLORS[tile.terrain]
          : currentTheme.water;
        ctx.fillRect(px, py, pw, ph);
      }
    }

    baseTerrainCacheRef.current = { canvas, tileSize: roundedTileSize, water: waterEnabled };
    return canvas;
  }

  function drawMap() {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const viewport = viewportRef.current;
    const w = Math.max(1, Math.round(viewport?.clientWidth ?? cols * tileSize));
    const h = Math.max(1, Math.round(viewport?.clientHeight ?? rows * tileSize));
    const dpr = Math.max(1, Math.min(window.devicePixelRatio || 1, 2));
    const pixelWidth = Math.round(w * dpr);
    const pixelHeight = Math.round(h * dpr);
    if (canvas.width !== pixelWidth || canvas.height !== pixelHeight) {
      canvas.width = pixelWidth;
      canvas.height = pixelHeight;
    }
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = layers.water ? currentTheme.water : "#0b1020";
    ctx.fillRect(0, 0, w, h);
    const camera = cameraRef.current;
    const renderedTileSize = tileSizeFromZoom(camera.zoom);
    const renderOutlinedTiles = outlinedTilesRef.current;
    const renderPenTiles = penTilesRef.current;
    const renderChaosSetupTiles = chaosSetupTilesRef.current;
    const renderReclaimedTiles = reclaimedTilesRef.current;
    const renderRoadTiles = roadTilesRef.current;
    const startX = Math.max(0, Math.floor((camera.x - MAP_CANVAS_MARGIN) / renderedTileSize) - 2);
    const startY = Math.max(0, Math.floor((camera.y - MAP_CANVAS_MARGIN) / renderedTileSize) - 2);
    const endX = Math.min(cols - 1, Math.ceil((camera.x + w - MAP_CANVAS_MARGIN) / renderedTileSize) + 2);
    const endY = Math.min(rows - 1, Math.ceil((camera.y + h - MAP_CANVAS_MARGIN) / renderedTileSize) + 2);
    const visibleNativeStartX = Math.max(0, Math.floor((startX * 10) / cols) - 1);
    const visibleNativeEndX = Math.min(9, Math.floor((endX * 10) / cols) + 1);
    const visibleNativeStartY = Math.max(0, Math.floor((startY * 10) / rows) - 1);
    const visibleNativeEndY = Math.min(9, Math.floor((endY * 10) / rows) + 1);
    const viewportOffset = getViewportWorldOffset(renderedTileSize);
    ctx.save();
    ctx.translate(viewportOffset.x + MAP_CANVAS_MARGIN - camera.x, viewportOffset.y + MAP_CANVAS_MARGIN - camera.y);

    const roundedTileSize = Math.max(0.25, Math.round(renderedTileSize * 4) / 4);
    const baseCache = getBaseTerrainCache(renderedTileSize);
    const cacheScale = renderedTileSize / roundedTileSize;
    const srcX = Math.max(0, Math.floor(startX * roundedTileSize));
    const srcY = Math.max(0, Math.floor(startY * roundedTileSize));
    const srcW = Math.min(baseCache.width - srcX, Math.ceil((endX - startX + 1) * roundedTileSize));
    const srcH = Math.min(baseCache.height - srcY, Math.ceil((endY - startY + 1) * roundedTileSize));
    const dstX = srcX * cacheScale;
    const dstY = srcY * cacheScale;
    const dstW = srcW * cacheScale;
    const dstH = srcH * cacheScale;
    ctx.drawImage(baseCache, srcX, srcY, srcW, srcH, dstX, dstY, dstW, dstH);
    const renderActiveTool = activeToolRef.current;
    const renderPreviewKeys = previewKeysRef.current.size > 0 ? previewKeysRef.current : previewKeys;
    const renderTownhallPreviewBounds = townhallPreviewBoundsRef.current ?? townhallPreviewBounds;

    for (let cy = startY; cy <= endY; cy++) {
      for (let cx = startX; cx <= endX; cx++) {
        const tile = grid.get(keyOf(cx, cy));
        if (!tile) continue;
        const key = keyOf(cx, cy);
        const px = Math.round(cx * renderedTileSize);
        const py = Math.round(cy * renderedTileSize);
        const pw = Math.max(1, Math.round((cx + 1) * renderedTileSize) - px);
        const ph = Math.max(1, Math.round((cy + 1) * renderedTileSize) - py);
        const land = isLand(tile);

        if (land && layers.levels) {
          const normalizedLevel = Math.min(tile.level, 6000) / 6000;
          const levelShade = 0.10 + normalizedLevel * 0.52;
          ctx.fillStyle = `rgba(37,99,235,${Math.max(0.10, levelShade * 0.42).toFixed(2)})`;
          ctx.fillRect(px, py, pw, ph);
          ctx.fillStyle = `rgba(17,24,39,${Math.max(0.16, levelShade * 0.86).toFixed(2)})`;
          ctx.fillRect(px, py, pw, ph);
          const nSX = Math.floor((tile.nativeX * cols) / 10);
          const nEX = Math.floor(((tile.nativeX + 1) * cols) / 10) - 1;
          const nSY = Math.floor((tile.nativeY * rows) / 10);
          const nEY = Math.floor(((tile.nativeY + 1) * rows) / 10) - 1;
          const isRightEdge = cx === nEX;
          const isBottomEdge = cy === nEY;
          const isLeftEdge = cx === nSX && tile.nativeX === 0;
          const isTopEdge = cy === nSY && tile.nativeY === 0;
          ctx.fillStyle = "rgba(0,0,0,0.45)";
          if (isRightEdge) ctx.fillRect(px + pw - 2, py, 2, ph);
          if (isLeftEdge) ctx.fillRect(px, py, 2, ph);
          if (isBottomEdge) ctx.fillRect(px, py + ph - 2, pw, 2);
          if (isTopEdge) ctx.fillRect(px, py, pw, 2);
        }

        if (layers.grid) {
          const gridOpacity = clampNumber((renderedTileSize - 4) / 10, 0.08, 0.38);
          ctx.strokeStyle = land ? `rgba(0,0,0,${gridOpacity * 0.4})` : `rgba(255,255,255,${gridOpacity * 0.35})`;
          ctx.lineWidth = 1;
          ctx.strokeRect(px + 0.5, py + 0.5, pw - 1, ph - 1);
        }

        if (layers.roads && renderRoadTiles.has(key)) {
          drawRoadTexture(ctx, px, py, pw);
        }

        if (layers.reclaimed && renderReclaimedTiles.has(key)) {
          ctx.fillStyle = RECLAIMED_FILL;
          ctx.fillRect(px, py, pw, ph);
          ctx.strokeStyle = RECLAIMED_BORDER;
          ctx.lineWidth = 1;
          ctx.strokeRect(px + 0.5, py + 0.5, pw - 1, ph - 1);
          ctx.strokeStyle = RECLAIMED_STRIPE;
          ctx.beginPath();
          ctx.moveTo(px, py + ph * 0.72);
          ctx.lineTo(px + pw * 0.72, py);
          ctx.moveTo(px + pw * 0.28, py + ph);
          ctx.lineTo(px + pw, py + ph * 0.28);
          ctx.stroke();
        }

        if (layers.deployments && getDeploymentCount(key) > 0) {
          ctx.fillStyle = DEPLOY_FILL;
          ctx.fillRect(px, py, pw, ph);
          ctx.strokeStyle = DEPLOY_BORDER;
          ctx.lineWidth = 1;
          ctx.strokeRect(px + 0.5, py + 0.5, pw - 1, ph - 1);
        }

        if (layers.poi && renderOutlinedTiles.has(key)) {
          ctx.strokeStyle = drawAreaColor;
          ctx.lineWidth = 2;
          ctx.beginPath();
          if (!renderOutlinedTiles.has(keyOf(cx, cy - 1))) { ctx.moveTo(px, py + 1); ctx.lineTo(px + pw, py + 1); }
          if (!renderOutlinedTiles.has(keyOf(cx + 1, cy))) { ctx.moveTo(px + pw - 1, py); ctx.lineTo(px + pw - 1, py + ph); }
          if (!renderOutlinedTiles.has(keyOf(cx, cy + 1))) { ctx.moveTo(px, py + ph - 1); ctx.lineTo(px + pw, py + ph - 1); }
          if (!renderOutlinedTiles.has(keyOf(cx - 1, cy))) { ctx.moveTo(px + 1, py); ctx.lineTo(px + 1, py + ph); }
          ctx.stroke();
        }

        if (layers.poi) {
          const penColorTile = renderPenTiles.get(key);
          if (penColorTile) { ctx.fillStyle = penColorTile; ctx.fillRect(px, py, pw, ph); }
        }

        if (renderPreviewKeys.has(key) && renderActiveTool === "chaos_setup") {
          ctx.fillStyle = "rgba(250,204,21,0.16)";
          ctx.fillRect(px, py, pw, ph);
          ctx.strokeStyle = "rgba(250,204,21,0.85)";
          ctx.lineWidth = 1;
          ctx.strokeRect(px + 0.5, py + 0.5, pw - 1, ph - 1);
        }

        if (renderPreviewKeys.has(key)) {
          if (renderActiveTool === "draw_area") {
            ctx.fillStyle = "rgba(251,146,60,0.10)";
            ctx.fillRect(px, py, pw, ph);
            ctx.strokeStyle = PREVIEW_ORANGE;
            ctx.lineWidth = 2;
            ctx.beginPath();
            if (!renderPreviewKeys.has(keyOf(cx, cy - 1))) { ctx.moveTo(px, py + 1); ctx.lineTo(px + pw, py + 1); }
            if (!renderPreviewKeys.has(keyOf(cx + 1, cy))) { ctx.moveTo(px + pw - 1, py); ctx.lineTo(px + pw - 1, py + ph); }
            if (!renderPreviewKeys.has(keyOf(cx, cy + 1))) { ctx.moveTo(px, py + ph - 1); ctx.lineTo(px + pw, py + ph - 1); }
            if (!renderPreviewKeys.has(keyOf(cx - 1, cy))) { ctx.moveTo(px + 1, py); ctx.lineTo(px + 1, py + ph); }
            ctx.stroke();
          } else if (renderActiveTool === "pen") {
            ctx.fillStyle = `${penColor}88`;
            ctx.fillRect(px, py, pw, ph);
          } else if (renderActiveTool === "deploy") {
            ctx.fillStyle = "rgba(34,211,238,0.12)";
            ctx.fillRect(px, py, pw, ph);
            ctx.strokeStyle = DEPLOY_BORDER;
            ctx.lineWidth = 1;
            ctx.strokeRect(px + 0.5, py + 0.5, pw - 1, ph - 1);
          } else if (renderActiveTool === "move_deploy") {
            ctx.fillStyle = "rgba(34,211,238,0.18)";
            ctx.fillRect(px, py, pw, ph);
            ctx.strokeStyle = DEPLOY_BORDER;
            ctx.lineWidth = 1;
            ctx.strokeRect(px + 0.5, py + 0.5, pw - 1, ph - 1);
          } else if (renderActiveTool === "reclaim") {
            ctx.fillStyle = "rgba(255,255,255,0.12)";
            ctx.fillRect(px, py, pw, ph);
            ctx.strokeStyle = "rgba(255,255,255,0.92)";
            ctx.lineWidth = 1;
            ctx.strokeRect(px + 0.5, py + 0.5, pw - 1, ph - 1);
          } else if (renderActiveTool === "road") {
            ctx.fillStyle = "rgba(100,116,139,0.35)";
            ctx.fillRect(px, py, pw, ph);
            ctx.strokeStyle = ROAD_BORDER;
            ctx.lineWidth = 1;
            ctx.strokeRect(px + 0.5, py + 0.5, pw - 1, ph - 1);
          }
        }

        if (townhallPlacement && townhallPlacementKeys.has(key)) {
          ctx.fillStyle = TOWNHALL_FOOTPRINT_FILL;
          ctx.fillRect(px, py, pw, ph);
          ctx.strokeStyle = TOWNHALL_FOOTPRINT_BORDER;
          ctx.lineWidth = 1;
          ctx.strokeRect(px + 0.5, py + 0.5, pw - 1, ph - 1);
        }

        if (selectedTile && selectedTile.x === cx && selectedTile.y === cy) {
          ctx.strokeStyle = "#111827";
          ctx.lineWidth = 1;
          ctx.strokeRect(px + 0.5, py + 0.5, pw - 1, ph - 1);
        }
      }
    }

    if (townhallPlacement && townhallCoverageBounds) {
      const { minX, minY, maxX, maxY } = townhallCoverageBounds;
      ctx.lineWidth = Math.max(1, renderedTileSize * 0.75);
      ctx.setLineDash([Math.max(4, renderedTileSize * 0.5), Math.max(4, renderedTileSize * 0.5)]);
      ctx.strokeRect(
        Math.round(minX * renderedTileSize) + 0.5,
        Math.round(minY * renderedTileSize) + 0.5,
        Math.round((maxX - minX + 1) * renderedTileSize) - 1,
        Math.round((maxY - minY + 1) * renderedTileSize) - 1
      );
      ctx.setLineDash([]);
    }

    if (renderActiveTool === "townhall" && renderTownhallPreviewBounds) {
      const { minX, minY, maxX, maxY } = renderTownhallPreviewBounds;
      ctx.strokeStyle = "rgba(56,189,248,0.95)";
      ctx.lineWidth = Math.max(1, renderedTileSize * 0.75);
      ctx.setLineDash([Math.max(4, renderedTileSize * 0.5), Math.max(4, renderedTileSize * 0.5)]);
      ctx.strokeRect(
        Math.round(minX * renderedTileSize) + 0.5,
        Math.round(minY * renderedTileSize) + 0.5,
        Math.round((maxX - minX + 1) * renderedTileSize) - 1,
        Math.round((maxY - minY + 1) * renderedTileSize) - 1
      );
      ctx.setLineDash([]);
    }

    if (layers.chaos_setup) {
      renderChaosSetupTiles.forEach((piece, placedKey) => {
        const [x, y] = placedKey.split(",").map(Number);
        const maxPieceX = piece === "chaos_stone" ? x + 1 : x;
        const maxPieceY = piece === "chaos_stone" ? y + 1 : y;
        if (maxPieceX < startX || x > endX || maxPieceY < startY || y > endY) return;
        const px = x * renderedTileSize;
        const py = y * renderedTileSize;
        if (piece === "info_board") {
          ctx.fillStyle = "#8b5a2b";
          ctx.fillRect(px + renderedTileSize * 0.44, py + renderedTileSize * 0.45, Math.max(1, renderedTileSize * 0.12), renderedTileSize * 0.45);
          ctx.fillStyle = "#d4a95f";
          ctx.fillRect(px + renderedTileSize * 0.18, py + renderedTileSize * 0.12, renderedTileSize * 0.64, renderedTileSize * 0.36);
          ctx.strokeStyle = "#8b5a2b";
          ctx.lineWidth = Math.max(1, renderedTileSize * 0.05);
          ctx.strokeRect(px + renderedTileSize * 0.18, py + renderedTileSize * 0.12, renderedTileSize * 0.64, renderedTileSize * 0.36);
        } else {
          ctx.fillStyle = "#9ca3af";
          ctx.beginPath();
          ctx.moveTo(px + renderedTileSize, py + renderedTileSize * 1.95);
          ctx.lineTo(px + renderedTileSize * 1.8, py + renderedTileSize * 1.85);
          ctx.lineTo(px + renderedTileSize * 1.65, py + renderedTileSize * 0.9);
          ctx.lineTo(px + renderedTileSize * 1.3, py + renderedTileSize * 0.2);
          ctx.lineTo(px + renderedTileSize * 0.75, py + renderedTileSize * 0.45);
          ctx.lineTo(px + renderedTileSize * 0.3, py + renderedTileSize * 1.1);
          ctx.lineTo(px + renderedTileSize * 0.15, py + renderedTileSize * 1.95);
          ctx.closePath();
          ctx.fill();
          ctx.strokeStyle = "#4b5563";
          ctx.lineWidth = Math.max(1, renderedTileSize * 0.06);
          ctx.stroke();
        }
      });
    }

    // -- Map facilities ----------------------------------------------------------
    if (layers.facilities && renderedTileSize >= 5.5) {
      ctx.save();
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      for (let ny = visibleNativeStartY; ny <= visibleNativeEndY; ny += 1) {
        for (let nx = visibleNativeStartX; nx <= visibleNativeEndX; nx += 1) {
          const names = ZONE_FACILITY_NAMES.get(`${nx},${ny}`);
          if (!names?.length) continue;
          // Zone pixel bounds
          const nSX = Math.floor((nx * cols) / 10);
          const nEX = Math.floor(((nx + 1) * cols) / 10) - 1;
          const nSY = Math.floor((ny * rows) / 10);
          const nEY = Math.floor(((ny + 1) * rows) / 10) - 1;
          const zoneW = (nEX - nSX + 1) * renderedTileSize;
          const zoneH = (nEY - nSY + 1) * renderedTileSize;
          const cxPx = nSX * renderedTileSize + zoneW / 2;
          const cyPx = nSY * renderedTileSize + zoneH / 2;
          // Fit font: start at ~13% of zone width, shrink until text fits
          const maxW = zoneW * 0.88;
          let fSize = Math.max(8, Math.min(44, Math.floor(zoneW * 0.13)));
          for (const name of names) {
            ctx.font = `bold ${fSize}px Arial, sans-serif`;
            while (ctx.measureText(name).width > maxW && fSize > 7) {
              fSize--;
              ctx.font = `bold ${fSize}px Arial, sans-serif`;
            }
          }
          ctx.font = `bold ${fSize}px Arial, sans-serif`;
          const lineH = fSize * 1.35;
          const totalH = names.length * lineH;
          let yOff = cyPx - totalH / 2 + lineH / 2;
          for (const name of names) {
            ctx.shadowColor = "rgba(0,0,0,0.95)";
            ctx.shadowBlur = Math.max(3, fSize * 0.45);
            ctx.fillStyle = "#fde68a";
            ctx.fillText(name, cxPx, yOff);
            ctx.shadowBlur = 0;
            yOff += lineH;
          }
        }
      }
      ctx.restore();
    }

    // -- Survey pins ------------------------------------------------------------
    if (showSurveys) {
      const MARGIN = 8;
      const PIN_R = Math.max(5, Math.min(14, renderedTileSize * 1.6));
      ctx.save();
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      for (let ny = visibleNativeStartY; ny <= visibleNativeEndY; ny += 1) {
        for (let nx = visibleNativeStartX; nx <= visibleNativeEndX; nx += 1) {
          const native = NATIVE_MAP[ny]?.[nx];
          if (!native) continue;
          const zoneTerrain: TerrainType = native.terrain;
          const zoneLevel: number = native.level;
          // Collect matching surveys (filtered by visible cats)
          const zoneKey = `${nx},${ny}`;
          const zoneDefs = surveyZoneData.defsByZone.get(zoneKey);
          const zoneCats = surveyZoneData.catsByZone.get(zoneKey);
          if (!zoneDefs || !zoneCats) continue;
          const cats = zoneCats.filter((cat) => visibleSurveyCats.has(cat));
          if (!cats.length) continue;
          // Zone center in canvas coords
          const nSX = Math.floor((nx * cols) / 10);
          const nEX = Math.floor(((nx + 1) * cols) / 10) - 1;
          const nSY = Math.floor((ny * rows) / 10);
          const nEY = Math.floor(((ny + 1) * rows) / 10) - 1;
          const nCX = Math.floor((nSX + nEX) / 2);
          const nCY = Math.floor((nSY + nEY) / 2);
          const baseX = nCX * renderedTileSize + MARGIN + renderedTileSize / 2;
          const baseY = nCY * renderedTileSize + MARGIN + renderedTileSize / 2;
          // Spread dots horizontally
          const spacing = PIN_R * 2.2;
          const totalW = (cats.length - 1) * spacing;
          cats.forEach((cat, i) => {
            const cx2 = baseX - totalW / 2 + i * spacing;
            const cy2 = baseY - PIN_R * 3.5;
            const color = SURVEY_CAT_COLORS[cat];
            // Shadow
            ctx.shadowColor = "rgba(0,0,0,0.6)";
            ctx.shadowBlur = 4;
            // Circle
            ctx.beginPath();
            ctx.arc(cx2, cy2, PIN_R, 0, Math.PI * 2);
            ctx.fillStyle = color;
            ctx.fill();
            ctx.lineWidth = 1.5;
            ctx.strokeStyle = "rgba(255,255,255,0.9)";
            ctx.stroke();
            // Stem
            ctx.beginPath();
            ctx.moveTo(cx2, cy2 + PIN_R);
            ctx.lineTo(cx2, cy2 + PIN_R + PIN_R * 0.8);
            ctx.strokeStyle = color;
            ctx.lineWidth = Math.max(1.5, PIN_R * 0.35);
            ctx.stroke();
            ctx.shadowBlur = 0;
            // Letter
            const fsize = Math.max(7, Math.floor(PIN_R * 0.9));
            ctx.font = `bold ${fsize}px sans-serif`;
            ctx.shadowBlur = 0;
            ctx.fillStyle = "#fff";
            ctx.fillText(SURVEY_CAT_LABELS[cat], cx2, cy2);
          });
        }
      }
      ctx.restore();
    }

    // -- Level numbers (drawn last so they always appear on top) ----------------
    if (layers.levels && renderedTileSize >= 5.5) {
      const zonePx = Math.floor((cols / 10) * renderedTileSize);
      const fontSize = Math.max(14, Math.min(72, Math.floor(zonePx * 0.18)));
      ctx.save();
      ctx.font = `bold ${fontSize}px Arial, sans-serif`;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.shadowColor = "rgba(0,0,0,0.98)";
      ctx.shadowBlur = Math.max(6, fontSize * 0.45);
      ctx.fillStyle = "#ffffff";
      for (let ny = visibleNativeStartY; ny <= visibleNativeEndY; ny += 1) {
        for (let nx = visibleNativeStartX; nx <= visibleNativeEndX; nx += 1) {
          const native = NATIVE_MAP[ny]?.[nx];
          if (!native) continue;
          const nSX = Math.floor((nx * cols) / 10);
          const nEX = Math.floor(((nx + 1) * cols) / 10) - 1;
          const nSY = Math.floor((ny * rows) / 10);
          const nEY = Math.floor(((ny + 1) * rows) / 10) - 1;
          const cxPx = nSX * renderedTileSize + (nEX - nSX + 1) * renderedTileSize / 2;
          const cyPx = nSY * renderedTileSize + (nEY - nSY + 1) * renderedTileSize / 2;
          ctx.fillText(String(native.level), cxPx, cyPx);
        }
      }
      ctx.shadowBlur = 0;
      ctx.restore();
    }
    ctx.restore();
  }

  function drawMinimap() {
    if (!showMinimap) return;
    const canvas = minimapCanvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const renderPenTiles = penTilesRef.current;
    const renderChaosSetupTiles = chaosSetupTilesRef.current;
    const renderReclaimedTiles = reclaimedTilesRef.current;
    const renderRoadTiles = roadTilesRef.current;
    ctx.clearRect(0, 0, cols, rows);
    grid.forEach((tile) => {
      const cx = tile.x;
      const cy = tile.y;
      const key = keyOf(cx, cy);
      const land = isLand(tile);
      let bg = currentTheme.water;
      if (land) bg = TERRAIN_COLORS[tile.terrain];
      if (layers.roads && renderRoadTiles.has(key)) bg = "rgba(100,116,139,0.95)";
      if (layers.poi && renderPenTiles.has(key)) bg = renderPenTiles.get(key)!;
      if (layers.reclaimed && renderReclaimedTiles.has(key)) bg = "#8fd3ff";
      if (layers.deployments && getDeploymentCount(key) > 0) bg = "rgba(34,211,238,0.9)";
      ctx.fillStyle = bg;
      ctx.fillRect(cx, cy, 1, 1);
    });
    if (layers.chaos_setup) {
      renderChaosSetupTiles.forEach((piece, placedKey) => {
        const [x, y] = placedKey.split(",").map(Number);
        ctx.fillStyle = piece === "info_board" ? "#d4a95f" : "#6b7280";
        ctx.fillRect(x, y, piece === "info_board" ? 1 : 2, piece === "info_board" ? 1 : 2);
      });
    }
  }

  // Render invalidation for non-camera state changes.
  // Camera movement/zoom uses scheduleRender directly, so we avoid duplicate redraws.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => {
    scheduleRender();
  }, [
    outlinedTiles, penTiles, chaosSetupTiles, reclaimedTiles, deployedTiles, roadTiles,
    layers, hoveredTile, selectedTile, activeTool, paintMode, drawAreaColor, penColor, deployColor, previewKeys,
    townhallPlacement, townhallLevel, townhallCoverageBounds, townhallPlacementKeys, townhallPreviewBounds,
  ]);

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!showMinimap) return;
    drawMinimap();
  }, [
    showMinimap,
    reclaimedTiles, deployedTiles, roadTiles, penTiles, chaosSetupTiles, outlinedTiles, layers,
  ]);

  const toolbarContent = (
    <div className="space-y-4">
      {/* Tools */}
      <div className="space-y-2">
        <div className="text-[10px] uppercase tracking-widest opacity-50 px-1">Tools</div>
        <div className="flex flex-col gap-1">
          <Button size="sm" variant={activeTool === "pen" ? "default" : "ghost"} className="w-full justify-start" onClick={() => setActiveToolImmediate((prev) => toggleTool(prev, "pen"))}>Pen</Button>
          <Button size="sm" variant={activeTool === "draw_area" ? "default" : "ghost"} className="w-full justify-start" onClick={() => { setActiveToolImmediate((prev) => toggleTool(prev, "draw_area")); if (brushSize < 2) setBrushSize(2); }}>Draw area</Button>
          <Button size="sm" variant={activeTool === "chaos_setup" ? "default" : "ghost"} className="w-full justify-start" onClick={() => setActiveToolImmediate((prev) => toggleTool(prev, "chaos_setup"))}>Chaos setup</Button>
          <Button size="sm" variant={activeTool === "reclaim" ? "default" : "ghost"} className="w-full justify-start" onClick={() => setActiveToolImmediate((prev) => toggleTool(prev, "reclaim"))}>Reclaim land</Button>
          <Button size="sm" variant={activeTool === "deploy" ? "default" : "ghost"} className="w-full justify-start" onClick={() => setActiveToolImmediate((prev) => toggleTool(prev, "deploy"))}>Deployment mode</Button>
          <Button size="sm" variant={activeTool === "townhall" ? "default" : "ghost"} className="w-full justify-start" onClick={() => setActiveToolImmediate((prev) => toggleTool(prev, "townhall"))}>Town Hall</Button>
          <Button size="sm" variant={activeTool === "road" ? "default" : "ghost"} className="w-full justify-start" onClick={() => setActiveToolImmediate((prev) => toggleTool(prev, "road"))}>Roads</Button>
        </div>
      </div>

      {/* Undo/Redo */}
      <div className="flex gap-1">
        <Button size="sm" variant="outline" className="flex-1" onClick={undo}>Undo</Button>
        <Button size="sm" variant="outline" className="flex-1" onClick={redo}>Redo</Button>
      </div>

      {/* Tool sub-options */}
      {activeTool === "pen" && (
        <div className="space-y-2">
          <div className="text-[10px] uppercase tracking-widest opacity-50 px-1">Pen color</div>
          <div className="flex flex-wrap gap-1">
            {BRUSH_COLORS.map((c) => (
              <button key={c.value} title={c.label} onClick={() => setPenColor(c.value)} className="w-6 h-6 rounded-full border-2 transition-transform hover:scale-110" style={{ background: c.value, borderColor: penColor === c.value ? "#fff" : "transparent" }} />
            ))}
          </div>
          {recentColors.length > 0 && (
            <>
              <div className="text-[10px] uppercase tracking-widest opacity-50 px-1">Recent</div>
              <div className="flex flex-wrap gap-1">
                {recentColors.map((c, i) => (
                  <button key={i} title={c} onClick={() => setPenColor(c)} className="w-6 h-6 rounded-full border-2 transition-transform hover:scale-110" style={{ background: c, borderColor: penColor === c ? "#fff" : "rgba(255,255,255,0.3)" }} />
                ))}
              </div>
            </>
          )}
          <div className="flex gap-1">
            <Button size="sm" variant={paintMode === "mark" ? "default" : "outline"} className="flex-1" onClick={() => setPaintMode("mark")}>Draw</Button>
            <Button size="sm" variant={paintMode === "erase" ? "default" : "outline"} className="flex-1" onClick={() => setPaintMode("erase")}>Erase</Button>
          </div>
          {paintMode === "erase" && (
            <>
              <div className="text-[10px] uppercase tracking-widest opacity-50 px-1">Eraser size</div>
              <div className="flex flex-wrap gap-1">
                {brushOptions.map((size) => (
                  <Button key={size} size="sm" variant={eraserSize === size ? "default" : "outline"} onClick={() => setEraserSize(size)} className="px-2 py-1 text-xs">{size}</Button>
                ))}
              </div>
            </>
          )}
          <Button size="sm" variant="outline" className="w-full" onClick={() => { pushHistory(); const next = new Map<string, string>(); penTilesRef.current = next; setPenTiles(next); scheduleRender(); }}>Clear pen</Button>
        </div>
      )}

      {activeTool === "draw_area" && (
        <div className="space-y-2">
          <div className="text-[10px] uppercase tracking-widest opacity-50 px-1">Area color</div>
          <div className="flex flex-wrap gap-1">
            {BRUSH_COLORS.map((c) => (
              <button key={c.value} title={c.label} onClick={() => setDrawAreaColor(c.value)} className="w-6 h-6 rounded-full border-2 transition-transform hover:scale-110" style={{ background: c.value, borderColor: drawAreaColor === c.value ? "#fff" : "transparent" }} />
            ))}
          </div>
          {recentColors.length > 0 && (
            <>
              <div className="text-[10px] uppercase tracking-widest opacity-50 px-1">Recent</div>
              <div className="flex flex-wrap gap-1">
                {recentColors.map((c, i) => (
                  <button key={i} title={c} onClick={() => setDrawAreaColor(c)} className="w-6 h-6 rounded-full border-2 transition-transform hover:scale-110" style={{ background: c, borderColor: drawAreaColor === c ? "#fff" : "rgba(255,255,255,0.3)" }} />
                ))}
              </div>
            </>
          )}
          <div className="text-[10px] uppercase tracking-widest opacity-50 px-1">Brush size</div>
          <div className="flex flex-wrap gap-1">
            {drawAreaBrushOptions.map((size) => (
              <Button key={size} size="sm" variant={brushSize === size ? "default" : "outline"} onClick={() => setBrushSize(size)} className="px-2 py-1 text-xs">{size}</Button>
            ))}
          </div>
          <div className="flex gap-1">
            <Button size="sm" variant={paintMode === "mark" ? "default" : "outline"} className="flex-1" onClick={() => setPaintMode("mark")}>Mark</Button>
            <Button size="sm" variant={paintMode === "erase" ? "default" : "outline"} className="flex-1" onClick={() => setPaintMode("erase")}>Erase</Button>
          </div>
          <Button size="sm" variant="outline" className="w-full" onClick={() => { pushHistory(); const next = new Set<string>(); outlinedTilesRef.current = next; setOutlinedTiles(next); scheduleRender(); }}>Clear draw area</Button>
        </div>
      )}

      {activeTool === "chaos_setup" && (
        <div className="space-y-2">
          <div className="text-[10px] uppercase tracking-widest opacity-50 px-1">Piece</div>
          <div className="flex gap-1">
            <Button size="sm" variant={chaosSetupPiece === "info_board" ? "default" : "outline"} className="flex-1" onClick={() => setChaosSetupPiece("info_board")}>Info Board</Button>
            <Button size="sm" variant={chaosSetupPiece === "chaos_stone" ? "default" : "outline"} className="flex-1" onClick={() => setChaosSetupPiece("chaos_stone")}>Chaos Stone</Button>
          </div>
          <div className="flex gap-1">
            <Button size="sm" variant={paintMode === "mark" ? "default" : "outline"} className="flex-1" onClick={() => setPaintMode("mark")}>Place</Button>
            <Button size="sm" variant={paintMode === "erase" ? "default" : "outline"} className="flex-1" onClick={() => setPaintMode("erase")}>Remove</Button>
          </div>
          <div className="text-xs opacity-60">
            Drag to place multiple pieces. Chaos Stone places as a 2×2 footprint.
          </div>
          <Button size="sm" variant="outline" className="w-full" onClick={() => { pushHistory(); const next = new Map<string, ChaosSetupPiece>(); chaosSetupTilesRef.current = next; setChaosSetupTiles(next); scheduleRender(); }}>Clear chaos setup</Button>
        </div>
      )}

      {activeTool === "reclaim" && (
        <div className="space-y-2">
          <div className="text-[10px] uppercase tracking-widest opacity-50 px-1">Brush size</div>
          <div className="flex flex-wrap gap-1">
            {brushOptions.map((size) => (
              <Button key={size} size="sm" variant={brushSize === size ? "default" : "outline"} onClick={() => setBrushSize(size)} className="px-2 py-1 text-xs">{size}</Button>
            ))}
          </div>
          <div className="flex gap-1">
            <Button size="sm" variant={reclaimMode === "reclaim" ? "default" : "outline"} className="flex-1" onClick={() => setReclaimMode("reclaim")}>Reclaim</Button>
            <Button size="sm" variant={reclaimMode === "restore" ? "default" : "outline"} className="flex-1" onClick={() => setReclaimMode("restore")}>Restore</Button>
          </div>
          <div className="text-xs opacity-60">Cost: {reclaimCost}</div>
        </div>
      )}

      {activeTool === "deploy" && (
        <div className="space-y-2">
          <div className="flex gap-1">
            <Button size="sm" variant={paintMode === "mark" ? "default" : "outline"} className="flex-1" onClick={() => setPaintMode("mark")}>Add</Button>
            <Button size="sm" variant={paintMode === "erase" ? "default" : "outline"} className="flex-1" onClick={() => setPaintMode("erase")}>Remove</Button>
          </div>
          <div className="text-[10px] uppercase tracking-widest opacity-50 px-1">Deployment size</div>
          <div className="flex flex-wrap gap-1">
            {deploymentOptions.map((size) => (
              <Button key={size} size="sm" variant={deploymentSize === size ? "default" : "outline"} onClick={() => setDeploymentSize(size)} className="px-2 py-1 text-xs">{size}</Button>
            ))}
          </div>
          <Button size="sm" variant="outline" className="w-full" onClick={() => { pushHistory(); const next = new Map<string, number>(); deployedTilesRef.current = next; setDeployedTiles(next); scheduleRender(); }}>Clear deployments</Button>
          {previewStats && <div className="text-xs opacity-60">{previewStats.total} tiles, {previewStats.newCoverage} new</div>}
        </div>
      )}

      {activeTool === "townhall" && (
        <div className="space-y-2">
          <div className="flex gap-1">
            <Button size="sm" variant={paintMode === "mark" ? "default" : "outline"} className="flex-1" onClick={() => setPaintMode("mark")}>Place</Button>
            <Button size="sm" variant={paintMode === "erase" ? "default" : "outline"} className="flex-1" onClick={() => setPaintMode("erase")}>Remove</Button>
          </div>
          <div className="text-[10px] uppercase tracking-widest opacity-50 px-1">Town Hall level</div>
          <div className="flex flex-wrap gap-1">
            {[1,10,20,30,40,50,60,70,80,90,100].map((level) => (
              <Button key={`townhall-level-${level}`} size="sm" variant={townhallLevel === level ? "default" : "outline"} onClick={() => setTownhallLevel(level)} className="px-2 py-1 text-xs">
                {level}
              </Button>
            ))}
          </div>
          <div className="text-xs opacity-60">Buildable area: {getTownHallCoverageSize(townhallLevel)}×{getTownHallCoverageSize(townhallLevel)}</div>
          <div className="text-xs opacity-60">Town Hall size: {TOWNHALL_SIZE}×{TOWNHALL_SIZE}</div>
          <Button size="sm" variant="outline" className="w-full" onClick={() => { pushHistory(); setTownhallPlacement(null); }}>Clear Town Hall</Button>
          {townhallPlacement && (
            <div className="text-xs opacity-60">Placed at {townhallPlacement.x},{townhallPlacement.y} (level {townhallPlacement.level})</div>
          )}
        </div>
      )}

      {activeTool === "road" && (
        <div className="space-y-2">
          <div className="text-[10px] uppercase tracking-widest opacity-50 px-1">Road mode</div>
          <div className="flex gap-1">
            <Button size="sm" variant={paintMode === "mark" ? "default" : "outline"} className="flex-1" onClick={() => setPaintMode("mark")}>Draw</Button>
            <Button size="sm" variant={paintMode === "erase" ? "default" : "outline"} className="flex-1" onClick={() => setPaintMode("erase")}>Erase</Button>
          </div>
          <Button size="sm" variant="outline" className="w-full" onClick={() => { pushHistory(); const next = new Set<string>(); roadTilesRef.current = next; setRoadTiles(next); scheduleRender(); }}>Clear roads</Button>
        </div>
      )}

      {/* Layers */}
      <div className="space-y-2">
        <div className="text-[10px] uppercase tracking-widest opacity-50 px-1">Layers</div>
        <div className="flex flex-col gap-1">
          {layerOrder.map((layer) => (
            <button
              key={layer}
              onClick={() => setLayers((prev) => ({ ...prev, [layer]: !prev[layer] }))}
              className="flex items-center gap-2 rounded-md px-2 py-1.5 text-xs transition-colors hover:bg-white/5"
              style={{ color: layers[layer] ? "#fff" : "rgba(255,255,255,0.35)" }}
            >
              <span className="w-3 h-3 rounded-sm border flex-shrink-0" style={{ background: layers[layer] ? "#3b82f6" : "transparent", borderColor: layers[layer] ? "#3b82f6" : "rgba(255,255,255,0.2)" }} />
              <span>{LAYER_LABELS[layer]}</span>
            </button>
          ))}
        </div>
      </div>

      {/* Surveys */}
      <div className="space-y-2">
        <div className="text-[10px] uppercase tracking-widest opacity-50 px-1">Surveys</div>
        <button
          onClick={() => setShowSurveys((v) => !v)}
          className="flex items-center gap-2 rounded-md px-2 py-1.5 text-xs w-full transition-colors hover:bg-white/5"
          style={{ color: showSurveys ? "#fff" : "rgba(255,255,255,0.35)" }}
        >
          <span className="w-3 h-3 rounded-sm border flex-shrink-0" style={{ background: showSurveys ? "#3b82f6" : "transparent", borderColor: showSurveys ? "#3b82f6" : "rgba(255,255,255,0.2)" }} />
          Show survey pins
        </button>
        {showSurveys && (
          <div className="flex flex-col gap-1 pl-1">
            {(["storehouse", "chaos_stone", "cash_register", "dragon_taming"] as SurveyCategory[]).map((cat) => {
              const active = visibleSurveyCats.has(cat);
              return (
                <button
                  key={cat}
                  onClick={() => setVisibleSurveyCats((prev) => {
                    const next = new Set(prev);
                    if (next.has(cat)) next.delete(cat); else next.add(cat);
                    return next;
                  })}
                  className="flex items-center gap-2 rounded-md px-2 py-1 text-xs transition-colors hover:bg-white/5"
                  style={{ color: active ? "#fff" : "rgba(255,255,255,0.35)" }}
                >
                  <span className="w-4 h-4 rounded-full flex items-center justify-center text-[9px] font-bold text-white flex-shrink-0" style={{ background: active ? SURVEY_CAT_COLORS[cat] : "rgba(255,255,255,0.1)" }}>
                    {SURVEY_CAT_LABELS[cat]}
                  </span>
                  {SURVEY_CAT_NAMES[cat]}
                </button>
              );
            })}
          </div>
        )}
      </div>

      {/* Zoom */}
      <div className="space-y-2">
        <div className="text-[10px] uppercase tracking-widest opacity-50 px-1">Zoom</div>
        <div className="flex gap-1 items-center">
          <Button size="sm" variant="outline" className="flex-1" onClick={() => zoomAtCenter(1 / 1.18)}>-</Button>
          <span className="text-xs opacity-60 w-14 text-center">{tileSize.toFixed(1)}px</span>
          <Button size="sm" variant="outline" className="flex-1" onClick={() => zoomAtCenter(1.18)}>+</Button>
        </div>
      </div>

      {/* Save slots */}
      <div className="space-y-2">
        <div className="text-[10px] uppercase tracking-widest opacity-50 px-1">Save slots</div>
        <div className="flex flex-col gap-1">
          {[1,2,3].map((slot) => (
            <div key={slot} className="flex gap-1">
              <Button size="sm" variant="outline" className="flex-1" onClick={() => saveSlot(slot)}>Save {slot}</Button>
              <Button size="sm" variant="outline" className="flex-1" onClick={() => loadSlot(slot)}>Load {slot}</Button>
              <Button size="sm" variant="outline" className="px-2 text-destructive hover:text-destructive" onClick={() => clearSlot(slot)}>×</Button>
            </div>
          ))}
        </div>
      </div>

      {/* Export / Import / Screenshot */}
      <div className="space-y-2">
        <div className="text-[10px] uppercase tracking-widest opacity-50 px-1">File</div>
        <div className="flex flex-col gap-1">
          <Button size="sm" variant="outline" className="w-full justify-start" onClick={exportToClipboard}>Export</Button>
          <Button size="sm" variant="outline" className="w-full justify-start" onClick={importFromClipboard}>Import</Button>
          <Button size="sm" variant="outline" className="w-full justify-start" onClick={resetMap}>Reset</Button>
          <Button size="sm" variant="outline" className="w-full justify-start" onClick={openScreenshotDialog}>Screenshot</Button>
        </div>
      </div>

    </div>
  );

  // â”€â”€ Map viewport â”€â”€
  const minimapPanelWidth = minimapSize.width + 16;
  const minimapPanelHeight = minimapSize.height + 44;
  const minimapViewRect = (() => {
    const contentWidth = Math.max(1, mapScroll.width - MAP_CANVAS_MARGIN * 2);
    const contentHeight = Math.max(1, mapScroll.height - MAP_CANVAS_MARGIN * 2);
    const left = clampNumber((mapScroll.left - MAP_CANVAS_MARGIN) / contentWidth * minimapSize.width, 0, minimapSize.width);
    const top = clampNumber((mapScroll.top - MAP_CANVAS_MARGIN) / contentHeight * minimapSize.height, 0, minimapSize.height);
    const width = clampNumber(mapScroll.viewportWidth / contentWidth * minimapSize.width, 10, minimapSize.width - left);
    const height = clampNumber(mapScroll.viewportHeight / contentHeight * minimapSize.height, 10, minimapSize.height - top);
    return { left, top, width, height };
  })();

  const floatingButtonStyle = {
    background: "rgba(15,23,42,0.88)",
    borderColor: "rgba(255,255,255,0.16)",
    color: "#fff",
  };

  const mapViewport = (
    <div
      className="relative overflow-hidden bg-muted/20"
      style={{
        height: isFullscreen ? "100%" : cleanMode ? "clamp(520px, 84vh, 1200px)" : "clamp(500px, 80vh, 1100px)",
        width: "100%",
        borderRadius: isFullscreen ? 0 : undefined,
      }}
    >
      <div
        ref={viewportRef}
        className="absolute inset-0 overflow-hidden"
        style={{
          cursor: getCursor(),
          touchAction: "none",
        }}
        onMouseDown={startPan}
        onMouseMove={movePan}
        onPointerDown={onViewportPointerDown}
        onPointerMove={onViewportPointerMove}
        onPointerUp={(e) => {
          if ((activeTool === "none" || shouldUseTouchNavigation(e.pointerType)) && panStartRef.current && (panStartRef.current.source !== "pointer" || panStartRef.current.pointerId === e.pointerId)) {
            const dx = Math.abs(e.clientX - panStartRef.current.x);
            const dy = Math.abs(e.clientY - panStartRef.current.y);
            if (dx < 5 && dy < 5) {
              const tile = tileAtPointer(e.clientX, e.clientY);
              if (tile) setSelectedTile(tile);
            }
          }
        }}
        onPointerLeave={() => updateCanvasHover(null)}
        onDoubleClick={(e) => { const t = tileAtPointer(e.clientX, e.clientY); if (t) centerOn(t.x, t.y); }}
        onContextMenu={(e) => e.preventDefault()}
        onClick={() => setDrawBubbleOpen(false)}
      >
        <canvas
          ref={canvasRef}
          style={{ display: "block", width: "100%", height: "100%" }}
        />
      </div>

      {!cleanMode && (
        <>
          <div
            className="absolute left-3 top-3 z-40 flex max-w-[calc(100%-96px)] items-center gap-1 rounded-lg border p-1 shadow-2xl backdrop-blur"
            style={floatingButtonStyle}
            onPointerDown={(e) => e.stopPropagation()}
            onClick={(e) => e.stopPropagation()}
          >
            <button
              className="h-9 rounded-md px-3 text-xs font-semibold transition-colors hover:bg-white/10"
              style={{ background: activeTool === "none" ? "rgba(59,130,246,0.9)" : "transparent" }}
              onClick={() => {
                setActiveToolImmediate("none");
                setPaintMode("mark");
                setFloatingToolsOpen(false);
              }}
              title="Browse and pan"
            >
              Browse
            </button>
            <button
              className="h-9 rounded-md px-3 text-xs font-semibold transition-colors hover:bg-white/10"
              style={{ background: activeTool !== "none" ? "rgba(59,130,246,0.9)" : "transparent" }}
              onClick={() => setFloatingToolsOpen((open) => !open)}
              title="Open editing tools"
            >
              Tools
            </button>
            <button
              className="h-9 rounded-md px-3 text-xs font-semibold transition-colors hover:bg-white/10"
              onClick={() => setFloatingLayersOpen((open) => !open)}
              title="Open layers"
            >
              Layers
            </button>
          </div>

          {floatingToolsOpen && (
            <div
              className="absolute left-3 top-[58px] z-40 grid max-w-[calc(100%-24px)] grid-cols-4 gap-1 rounded-lg border p-2 shadow-2xl backdrop-blur sm:grid-cols-7"
              style={{ ...floatingButtonStyle, background: "rgba(15,23,42,0.94)" }}
              onPointerDown={(e) => e.stopPropagation()}
              onClick={(e) => e.stopPropagation()}
            >
              {[
                { key: "pen" as ToolType, label: "Pen" },
                { key: "draw_area" as ToolType, label: "Area" },
                { key: "road" as ToolType, label: "Road" },
                { key: "deploy" as ToolType, label: "Deploy" },
                { key: "reclaim" as ToolType, label: "Reclaim" },
                { key: "chaos_setup" as ToolType, label: "Build" },
                { key: "townhall" as ToolType, label: "Town" },
              ].map((tool) => (
                <button
                  key={tool.key}
                  className="h-9 rounded-md border px-2 text-xs font-semibold transition-colors hover:bg-white/10"
                  style={{
                    background: activeTool === tool.key ? "rgba(59,130,246,0.9)" : "rgba(255,255,255,0.04)",
                    borderColor: activeTool === tool.key ? "#60a5fa" : "rgba(255,255,255,0.12)",
                  }}
                  onClick={() => selectMapTool(tool.key)}
                >
                  {tool.label}
                </button>
              ))}
            </div>
          )}

          <div
            className="absolute right-3 top-3 z-40 flex flex-col overflow-hidden rounded-lg border shadow-2xl backdrop-blur"
            style={floatingButtonStyle}
            onPointerDown={(e) => e.stopPropagation()}
            onClick={(e) => e.stopPropagation()}
          >
            <button className="h-9 w-10 text-lg font-semibold transition-colors hover:bg-white/10" onClick={() => zoomAtCenter(1.18)} title="Zoom in">+</button>
            <button className="h-9 w-10 border-y border-white/10 text-lg font-semibold transition-colors hover:bg-white/10" onClick={() => zoomAtCenter(1 / 1.18)} title="Zoom out">-</button>
            <button className="h-9 w-10 text-[10px] font-bold uppercase transition-colors hover:bg-white/10" onClick={fitMapToViewport} title="Fit map">Fit</button>
          </div>

          {floatingLayersOpen && (
            <div
              className="absolute right-3 top-[126px] z-40 w-[min(260px,calc(100%-24px))] rounded-lg border p-2 shadow-2xl backdrop-blur"
              style={{ ...floatingButtonStyle, background: "rgba(15,23,42,0.94)" }}
              onPointerDown={(e) => e.stopPropagation()}
              onClick={(e) => e.stopPropagation()}
            >
              <div className="mb-2 flex items-center justify-between gap-2">
                <div className="text-[10px] font-semibold uppercase tracking-wide opacity-70">Map layers</div>
                <button className="h-7 rounded-md px-2 text-xs hover:bg-white/10" onClick={() => setFloatingLayersOpen(false)} title="Close layers">Close</button>
              </div>
              <div className="mb-2 grid grid-cols-2 gap-1 rounded-md border border-white/10 p-1">
                {(["visual", "logic"] as MapViewMode[]).map((mode) => (
                  <button
                    key={mode}
                    className="h-8 rounded-md px-2 text-xs font-semibold transition-colors hover:bg-white/10"
                    style={{
                      background: mapViewMode === mode ? "rgba(59,130,246,0.9)" : "transparent",
                      color: mapViewMode === mode ? "#fff" : "rgba(255,255,255,0.72)",
                    }}
                    onClick={() => applyMapViewMode(mode)}
                  >
                    {mode === "visual" ? "Visual" : "Logic"}
                  </button>
                ))}
              </div>
              <div className="grid grid-cols-2 gap-1">
                {layerOrder.map((layer) => (
                  <button
                    key={`floating-layer-${layer}`}
                    className="flex h-8 items-center gap-2 rounded-md px-2 text-left text-xs transition-colors hover:bg-white/10"
                    style={{ color: layers[layer] ? "#fff" : "rgba(255,255,255,0.45)" }}
                    onClick={() => setLayers((prev) => ({ ...prev, [layer]: !prev[layer] }))}
                  >
                    <span className="h-3 w-3 flex-shrink-0 rounded-sm border" style={{ background: layers[layer] ? "#3b82f6" : "transparent", borderColor: layers[layer] ? "#60a5fa" : "rgba(255,255,255,0.25)" }} />
                    <span className="truncate">{LAYER_LABELS[layer]}</span>
                  </button>
                ))}
              </div>
              <div className="mt-2 flex gap-1 border-t border-white/10 pt-2">
                <button className="h-8 flex-1 rounded-md px-2 text-xs hover:bg-white/10" onClick={() => setShowMinimap((prev) => !prev)}>
                  {showMinimap ? "Hide minimap" : "Show minimap"}
                </button>
                <button className="h-8 flex-1 rounded-md px-2 text-xs hover:bg-white/10" onClick={() => setShowSurveys((prev) => !prev)}>
                  {showSurveys ? "Hide pins" : "Show pins"}
                </button>
              </div>
            </div>
          )}
        </>
      )}
      {/* Draw Tool Bubble */}
      {!cleanMode && (() => {
        const isRoad = activeTool === "road" && paintMode === "mark";
        const isPen = activeTool === "pen" && paintMode === "mark";
        const isDrawArea = activeTool === "draw_area" && paintMode === "mark";
        const isChaosSetup = activeTool === "chaos_setup" && paintMode === "mark";
        const isTownhall = activeTool === "townhall" && paintMode === "mark";
        const isErase = paintMode === "erase" && activeTool !== "none";
        const isNone = activeTool === "none";

        const eraseTargetLabel = eraserTarget === "all"
          ? "All"
          : eraserTarget === "pen"
          ? "Pen"
          : eraserTarget === "draw_area"
          ? "Area"
          : eraserTarget === "road"
          ? "Road"
          : eraserTarget === "deploy"
          ? "Deploy"
          : eraserTarget === "chaos_setup"
          ? "Chaos Setup"
          : eraserTarget;
        const toolName = isNone ? "No tool" : isErase ? `Erase ${eraseTargetLabel}` : isRoad ? "Road" : isPen ? "Pen" : isDrawArea ? "Draw Area" : isChaosSetup ? `CS (${chaosSetupPiece === "info_board" ? "Info Board" : "Chaos Stone"})` : isTownhall ? "Town Hall" : activeTool;

        const bubbleIcon = isNone ? <BubbleIconCursor /> : isRoad ? <BubbleIconRoad /> : isErase ? <BubbleIconEraser /> : isDrawArea ? <BubbleIconDrawArea /> : isTownhall ? <BubbleIconTownHall /> : <BubbleIconPen />;

        return (
          <div
            className="absolute z-50 flex items-center gap-2"
            style={{ left: drawBubblePos.x, top: drawBubblePos.y }}
          >
            <div className="relative flex-shrink-0 group">
              <button
                title={toolName}
                className="w-11 h-11 rounded-full border-2 flex items-center justify-center shadow-2xl backdrop-blur select-none transition-transform active:scale-95"
                style={{ background: !isNone ? "rgba(37,99,235,0.82)" : "rgba(15,23,42,0.88)", borderColor: !isNone ? "#60a5fa" : "rgba(255,255,255,0.22)", color: "#fff", cursor: "grab" }}
                onPointerDown={(e) => {
                  if (e.button === 2) return;
                  e.stopPropagation();
                  (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
                  drawBubbleDragRef.current = { startX: e.clientX, startY: e.clientY, startPosX: drawBubblePos.x, startPosY: drawBubblePos.y };
                }}
                onPointerMove={(e) => {
                  const drag = drawBubbleDragRef.current;
                  if (!drag) return;
                  e.stopPropagation();
                  const dx = e.clientX - drag.startX;
                  const dy = e.clientY - drag.startY;
                  if (Math.abs(dx) > 4 || Math.abs(dy) > 4) {
                    setDrawBubblePos({ x: drag.startPosX + dx, y: drag.startPosY + dy });
                  }
                }}
                onPointerUp={(e) => {
                  const drag = drawBubbleDragRef.current;
                  const wasDrag = drag && (Math.abs(e.clientX - drag.startX) > 4 || Math.abs(e.clientY - drag.startY) > 4);
                  drawBubbleDragRef.current = null;
                  if (!wasDrag) setDrawBubbleOpen((prev) => !prev);
                }}
                onClick={(e) => e.stopPropagation()}
              >
                {bubbleIcon}
              </button>
              {!drawBubbleOpen && (
                <div className="absolute -top-7 left-1/2 -translate-x-1/2 rounded-full px-2 py-0.5 text-xs font-semibold shadow-lg pointer-events-none whitespace-nowrap opacity-0 group-hover:opacity-100 transition-opacity" style={{ background: "rgba(15,23,42,0.92)", color: "#fff", border: "1px solid rgba(255,255,255,0.15)" }}>
                  {toolName}
                </div>
              )}
              {isRoad && isPainting && roadSnapPreview.size > 0 && (
                <div className="absolute -top-6 left-1/2 -translate-x-1/2 rounded-full px-2 py-0.5 text-xs font-bold shadow-lg pointer-events-none whitespace-nowrap" style={{ background: "rgba(15,23,42,0.92)", color: "#94a3b8", border: "1px solid #94a3b8" }}>
                  {roadSnapPreview.size}
                </div>
              )}
              {isErase && (
                <div className="absolute -bottom-5 left-1/2 -translate-x-1/2 rounded-full px-2 py-0.5 text-[10px] font-bold shadow-lg pointer-events-none whitespace-nowrap" style={{ background: "rgba(239,68,68,0.85)", color: "#fff", border: "1px solid #ef4444" }}>
                  {eraseTargetLabel} {eraserSize}
                </div>
              )}
            </div>
            {drawBubbleOpen && (
              <div className="flex flex-row gap-2 items-center" onClick={(e) => e.stopPropagation()}>
                <button
                  title="Pan (no tool)"
                  onClick={() => { setActiveToolImmediate("none"); setPaintMode("mark"); setDrawBubbleOpen(false); }}
                  className="w-11 h-11 rounded-full border-2 flex items-center justify-center shadow-xl backdrop-blur transition-all"
                  style={{ background: isNone ? "rgba(37,99,235,0.85)" : "rgba(15,23,42,0.88)", borderColor: isNone ? "#2563eb" : "rgba(255,255,255,0.18)", color: isNone ? "#fff" : "rgba(255,255,255,0.75)" }}
                ><BubbleIconCursor /></button>
                <button
                  title="Pen"
                  onClick={() => { setActiveToolImmediate("pen"); setPaintMode("mark"); setDrawBubbleOpen(false); }}
                  className="w-11 h-11 rounded-full border-2 flex items-center justify-center shadow-xl backdrop-blur transition-all"
                  style={{ background: isPen ? "rgba(37,99,235,0.85)" : "rgba(15,23,42,0.88)", borderColor: isPen ? "#2563eb" : "rgba(255,255,255,0.18)", color: isPen ? "#fff" : "rgba(255,255,255,0.75)" }}
                ><BubbleIconPen /></button>
                <button
                  title="Draw area"
                  onClick={() => { setActiveToolImmediate("draw_area"); setPaintMode("mark"); if (brushSize < 2) setBrushSize(2); setDrawBubbleOpen(false); }}
                  className="w-11 h-11 rounded-full border-2 flex items-center justify-center shadow-xl backdrop-blur transition-all"
                  style={{ background: isDrawArea ? "rgba(37,99,235,0.85)" : "rgba(15,23,42,0.88)", borderColor: isDrawArea ? "#2563eb" : "rgba(255,255,255,0.18)", color: isDrawArea ? "#fff" : "rgba(255,255,255,0.75)" }}
                ><BubbleIconDrawArea /></button>
                <button
                  title="CS"
                  onClick={() => { setActiveToolImmediate("chaos_setup"); setPaintMode("mark"); setDrawBubbleOpen(false); }}
                  className="w-11 h-11 rounded-full border-2 flex items-center justify-center shadow-xl backdrop-blur transition-all"
                  style={{ background: isChaosSetup ? "rgba(217,119,6,0.88)" : "rgba(15,23,42,0.88)", borderColor: isChaosSetup ? "#f59e0b" : "rgba(255,255,255,0.18)", color: isChaosSetup ? "#fff" : "rgba(255,255,255,0.75)" }}
                >
                  <span className="text-[10px] font-bold">BLD</span>
                </button>
                <button
                  title="Eraser"
                  onClick={() => { if (activeTool !== "none") setPaintMode("erase"); setDrawBubbleOpen(false); }}
                  className="w-11 h-11 rounded-full border-2 flex items-center justify-center shadow-xl backdrop-blur transition-all"
                  style={{ background: isErase ? "rgba(239,68,68,0.75)" : "rgba(15,23,42,0.88)", borderColor: isErase ? "#ef4444" : "rgba(255,255,255,0.18)", color: isErase ? "#fff" : "rgba(255,255,255,0.75)" }}
                ><BubbleIconEraser /></button>
                <button
                  title="Town Hall"
                  onClick={() => { setActiveToolImmediate("townhall"); setPaintMode("mark"); setDrawBubbleOpen(false); }}
                  className="w-11 h-11 rounded-full border-2 flex items-center justify-center shadow-xl backdrop-blur transition-all"
                  style={{ background: activeTool === "townhall" ? "rgba(37,99,235,0.85)" : "rgba(15,23,42,0.88)", borderColor: activeTool === "townhall" ? "#60a5fa" : "rgba(255,255,255,0.18)", color: activeTool === "townhall" ? "#fff" : "rgba(255,255,255,0.75)" }}
                ><span className="text-[10px] font-bold">TH</span></button>
                <button
                  title="Road"
                  onClick={() => { setActiveToolImmediate("road"); setPaintMode("mark"); setDrawBubbleOpen(false); }}
                  className="w-11 h-11 rounded-full border-2 flex items-center justify-center shadow-xl backdrop-blur transition-all"
                  style={{ background: isRoad ? "rgba(100,116,139,0.85)" : "rgba(15,23,42,0.88)", borderColor: isRoad ? "#94a3b8" : "rgba(255,255,255,0.18)", color: isRoad ? "#fff" : "rgba(255,255,255,0.75)" }}
                ><BubbleIconRoad /></button>
              </div>
            )}
          </div>
        );
      })()}

      {!cleanMode && activeTool !== "none" && (
        <div
          className="absolute top-3 left-1/2 -translate-x-1/2 z-50 max-w-[calc(100%-24px)]"
          onClick={(e) => e.stopPropagation()}
        >
          <div className="flex items-center gap-1.5 rounded-xl border px-2.5 py-1.5 shadow-2xl backdrop-blur overflow-x-auto whitespace-nowrap"
            style={{ background: "rgba(15,23,42,0.92)", borderColor: "rgba(255,255,255,0.15)", color: "#fff", maxWidth: touchMode ? "min(92vw, 560px)" : "min(70vw, 860px)" }}>
            {activeTool === "pen" && paintMode === "mark" && (
              <>
                <span className="text-[10px] font-semibold opacity-60 uppercase tracking-wider">Pen</span>
                {([1,2,3,4,5,6] as BrushSize[]).map((size) => (
                  <button
                    key={`pen-size-${size}`}
                    onClick={() => setPenBrushSize(size)}
                    className="w-6 h-6 rounded-full border text-[10px] font-bold shrink-0"
                    style={{ background: penBrushSize === size ? "rgba(37,99,235,0.88)" : "rgba(15,23,42,0.72)", borderColor: penBrushSize === size ? "#60a5fa" : "rgba(255,255,255,0.18)" }}
                  >{size}</button>
                ))}
                <button
                  onClick={() => setLineAssist((prev) => !prev)}
                  className="h-6 px-2 rounded-full border text-[10px] font-bold shrink-0"
                  style={{ background: lineAssist ? "rgba(37,99,235,0.88)" : "rgba(15,23,42,0.72)", borderColor: lineAssist ? "#60a5fa" : "rgba(255,255,255,0.18)" }}
                >
                  Line
                </button>
                {BRUSH_COLORS.slice(0, 6).map((c) => (
                  <button
                    key={`pen-color-${c.value}`}
                    onClick={() => setPenColor(c.value)}
                    className="w-5 h-5 rounded-full border-2 shrink-0"
                    style={{ background: c.value, borderColor: penColor === c.value ? "#fff" : "rgba(255,255,255,0.2)" }}
                    title={c.label}
                  />
                ))}
              </>
            )}

            {paintMode === "erase" && (
              <>
                <span className="text-[10px] font-semibold opacity-60 uppercase tracking-wider">Eraser</span>
                {(["pen","draw_area","road","chaos_setup","deploy","all"] as const).map((layer) => {
                  const label = layer === "pen" ? "Pen" : layer === "draw_area" ? "Area" : layer === "road" ? "Road" : layer === "chaos_setup" ? "BLD" : layer === "deploy" ? "Deploy" : "All";
                  const active = eraserTarget === layer;
                  return (
                    <button
                      key={`erase-layer-${layer}`}
                      onClick={() => setEraserTarget(layer)}
                      className="h-6 px-2 rounded-full border text-[10px] font-bold shrink-0"
                      style={{ background: active ? "rgba(239,68,68,0.85)" : "rgba(15,23,42,0.72)", borderColor: active ? "#ef4444" : "rgba(255,255,255,0.18)" }}
                    >{label}</button>
                  );
                })}
                {([1,2,3,4,5,6] as BrushSize[]).map((size) => (
                  <button
                    key={`eraser-size-${size}`}
                    onClick={() => setEraserSize(size)}
                    className="w-6 h-6 rounded-full border text-[10px] font-bold shrink-0"
                    style={{ background: eraserSize === size ? "rgba(239,68,68,0.85)" : "rgba(15,23,42,0.72)", borderColor: eraserSize === size ? "#ef4444" : "rgba(255,255,255,0.18)" }}
                  >{size}</button>
                ))}
              </>
            )}

            {activeTool === "draw_area" && (
              <>
                <span className="text-[10px] font-semibold opacity-60 uppercase tracking-wider">Area</span>
                {([2,3,4,5,6] as BrushSize[]).map((size) => (
                  <button
                    key={`area-size-${size}`}
                    onClick={() => setBrushSize(size)}
                    className="w-6 h-6 rounded-full border text-[10px] font-bold shrink-0"
                    style={{ background: brushSize === size ? "rgba(37,99,235,0.88)" : "rgba(15,23,42,0.72)", borderColor: brushSize === size ? "#60a5fa" : "rgba(255,255,255,0.18)" }}
                  >{size}</button>
                ))}
                {BRUSH_COLORS.slice(0, 6).map((c) => (
                  <button
                    key={`area-color-${c.value}`}
                    onClick={() => setDrawAreaColor(c.value)}
                    className="w-5 h-5 rounded-full border-2 shrink-0"
                    style={{ background: c.value, borderColor: drawAreaColor === c.value ? "#fff" : "rgba(255,255,255,0.2)" }}
                    title={c.label}
                  />
                ))}
              </>
            )}

            {activeTool === "deploy" && paintMode === "mark" && (
              <>
                <span className="text-[10px] font-semibold opacity-60 uppercase tracking-wider">Deploy</span>
                {deploymentOptions.map((size) => (
                  <button
                    key={`deploy-size-${size}`}
                    onClick={() => setDeploymentSize(size)}
                    className="w-6 h-6 rounded-full border text-[10px] font-bold shrink-0"
                    style={{ background: deploymentSize === size ? "rgba(37,99,235,0.88)" : "rgba(15,23,42,0.72)", borderColor: deploymentSize === size ? "#60a5fa" : "rgba(255,255,255,0.18)" }}
                  >{size}</button>
                ))}
                {BRUSH_COLORS.slice(0, 6).map((c) => (
                  <button
                    key={`deploy-color-${c.value}`}
                    onClick={() => setDeployColor(c.value)}
                    className="w-5 h-5 rounded-full border-2 shrink-0"
                    style={{ background: c.value, borderColor: deployColor === c.value ? "#fff" : "rgba(255,255,255,0.2)" }}
                    title={c.label}
                  />
                ))}
              </>
            )}

            {activeTool === "chaos_setup" && (
              <>
                <span className="text-[10px] font-semibold opacity-60 uppercase tracking-wider">Buildings</span>
                <button
                  onClick={() => setChaosSetupPiece("info_board")}
                  className="h-6 px-2 rounded-full border text-[10px] font-bold shrink-0"
                  style={{ background: chaosSetupPiece === "info_board" ? "rgba(217,119,6,0.88)" : "rgba(15,23,42,0.72)", borderColor: chaosSetupPiece === "info_board" ? "#f59e0b" : "rgba(255,255,255,0.18)" }}
                >
                  Board
                </button>
                <button
                  onClick={() => setChaosSetupPiece("chaos_stone")}
                  className="h-6 px-2 rounded-full border text-[10px] font-bold shrink-0"
                  style={{ background: chaosSetupPiece === "chaos_stone" ? "rgba(217,119,6,0.88)" : "rgba(15,23,42,0.72)", borderColor: chaosSetupPiece === "chaos_stone" ? "#f59e0b" : "rgba(255,255,255,0.18)" }}
                >
                  Stone
                </button>
              </>
            )}

            {activeTool === "road" && (
              <>
                <span className="text-[10px] font-semibold opacity-60 uppercase tracking-wider">Road</span>
                <div className="text-[11px] opacity-75">Straight drag</div>
              </>
            )}
          </div>
        </div>
      )}

      {/* Survey tooltip */}
      {surveyTooltip && showSurveys && (() => {
        const byCategory = new Map<SurveyCategory, SurveyDef[]>();
        surveyTooltip.items.forEach((s) => {
          if (!byCategory.has(s.category)) byCategory.set(s.category, []);
          byCategory.get(s.category)!.push(s);
        });
        // Compute position relative to the outer wrapper
        const outerEl = viewportRef.current?.parentElement;
        const outerRect = outerEl?.getBoundingClientRect();
        const tx = outerRect ? surveyTooltip.x - outerRect.left + 14 : surveyTooltip.x;
        const ty = outerRect ? surveyTooltip.y - outerRect.top - 8 : surveyTooltip.y;
        return (
          <div
            className="absolute z-[60] pointer-events-none rounded-xl border shadow-2xl text-xs"
            style={{ left: tx, top: ty, transform: "translateY(-100%)", background: "rgba(10,14,26,0.96)", borderColor: "rgba(255,255,255,0.12)", color: "#e2e8f0", minWidth: 200, maxWidth: 280, padding: "10px 12px" }}
          >
            {[...byCategory.entries()].map(([cat, items]) => (
              <div key={cat} className="mb-2 last:mb-0">
                <div className="font-bold mb-1 flex items-center gap-1.5" style={{ color: SURVEY_CAT_COLORS[cat] }}>
                  <span className="w-4 h-4 rounded-full flex items-center justify-center text-[9px] font-bold text-white flex-shrink-0" style={{ background: SURVEY_CAT_COLORS[cat] }}>{SURVEY_CAT_LABELS[cat]}</span>
                  {SURVEY_CAT_NAMES[cat]}
                </div>
                {items.map((s) => (
                  <div key={s.id} className="ml-5 mb-0.5 opacity-85">
                    <span className="font-medium">{s.name}</span>
                    <span className="opacity-60 ml-1">(Lv {s.minLevel}+)</span>
                    <div className="opacity-60 text-[10px]">
                      {s.minCost === s.maxCost ? (s.minCost === 0 ? "Free" : `${s.minCost}??`) : `${s.minCost}–${s.maxCost}??`}
                      {" · "}
                      {s.minRate}–{s.maxRate}% success
                      {" · "}
                      {s.minTimeSec < 3600 ? `${s.minTimeSec/60}m` : `${s.minTimeSec/3600}h`}–{s.maxTimeSec < 3600 ? `${s.maxTimeSec/60}m` : `${s.maxTimeSec/3600}h`}
                    </div>
                  </div>
                ))}
              </div>
            ))}
          </div>
        );
      })()}

      {showMinimap && !cleanMode && (
        minimapCollapsed ? (
          /* Collapsed: draggable floating icon button */
          <button
            className="absolute z-30 w-10 h-10 rounded-full border-2 flex items-center justify-center shadow-xl backdrop-blur hover:scale-105"
            style={{ left: minimapBubblePos.x, top: minimapBubblePos.y, background: currentTheme.panelBg, borderColor: currentTheme.panelBorder, cursor: "grab", touchAction: "none" }}
            title="Show minimap"
            onPointerDown={(e) => {
              e.stopPropagation();
              (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
              minimapBubbleDragRef.current = { startX: e.clientX, startY: e.clientY, startPosX: minimapBubblePos.x, startPosY: minimapBubblePos.y };
            }}
            onPointerMove={(e) => {
              const drag = minimapBubbleDragRef.current;
              if (!drag) return;
              e.stopPropagation();
              setMinimapBubblePos(clampFloatingMapPos(drag.startPosX + (e.clientX - drag.startX), drag.startPosY + (e.clientY - drag.startY), 40, 40));
            }}
            onPointerUp={(e) => {
              const drag = minimapBubbleDragRef.current;
              const wasDrag = drag && (Math.abs(e.clientX - drag.startX) > 4 || Math.abs(e.clientY - drag.startY) > 4);
              minimapBubbleDragRef.current = null;
              if (!wasDrag) setMinimapCollapsed(false);
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M3 7l6 3 6-3 6 3v10l-6-3-6 3-6-3V7z"/>
              <path d="M9 10v10M15 7v10"/>
            </svg>
          </button>
        ) : (
          /* Expanded: full minimap panel */
          <div className="absolute z-30 rounded-lg border p-2 backdrop-blur shadow-2xl" style={{ left: minimapBubblePos.x, top: minimapBubblePos.y, width: minimapPanelWidth, background: "rgba(15,23,42,0.88)", borderColor: "rgba(255,255,255,0.16)", touchAction: "none" }}>
            <div
              className="flex items-center justify-between gap-2 mb-2 cursor-grab"
              onPointerDown={(e) => {
                e.stopPropagation();
                (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
                minimapBubbleDragRef.current = { startX: e.clientX, startY: e.clientY, startPosX: minimapBubblePos.x, startPosY: minimapBubblePos.y };
              }}
              onPointerMove={(e) => {
                const drag = minimapBubbleDragRef.current;
                if (!drag) return;
                e.stopPropagation();
                setMinimapBubblePos(clampFloatingMapPos(drag.startPosX + (e.clientX - drag.startX), drag.startPosY + (e.clientY - drag.startY), minimapPanelWidth, minimapPanelHeight));
              }}
              onPointerUp={(e) => {
                minimapBubbleDragRef.current = null;
                e.stopPropagation();
              }}
            >
              <div className="text-[10px] uppercase tracking-wide opacity-70">Minimap</div>
              <button
                className="w-7 h-7 rounded-md flex items-center justify-center text-base font-bold hover:bg-white/10 opacity-80 hover:opacity-100 transition-all"
                onPointerDown={(e) => e.stopPropagation()}
                onClick={(e) => {
                  e.stopPropagation();
                  setMinimapBubblePos((pos) => clampFloatingMapPos(pos.x, pos.y, 40, 40));
                  setMinimapCollapsed(true);
                }}
                title="Collapse minimap"
                aria-label="Collapse minimap"
              >
                &minus;
              </button>
            </div>
            <div className="relative overflow-hidden rounded-md border border-white/10 bg-slate-950/60" style={{ width: minimapSize.width, height: minimapSize.height }}>
              <canvas
                ref={minimapCanvasRef}
                width={cols}
                height={rows}
                style={{ display: "block", width: minimapSize.width, height: minimapSize.height, cursor: "crosshair" }}
                onClick={(e) => {
                  const rect = e.currentTarget.getBoundingClientRect();
                  centerOn(Math.floor((e.clientX - rect.left) / rect.width * cols), Math.floor((e.clientY - rect.top) / rect.height * rows));
                }}
              />
              <div
                className="absolute pointer-events-none rounded-[2px] border-2 border-white/90 shadow-[0_0_0_1px_rgba(15,23,42,0.8)]"
                style={minimapViewRect}
              />
            </div>
          </div>
        )
      )}

      {activeTool === "road" && paintMode === "mark" && isPainting && roadSnapPreview.size > 0 && (
        <div className="absolute bottom-3 left-0 right-0 z-50 flex justify-center pointer-events-none">
          <div className="rounded-full px-4 py-1.5 text-sm font-bold shadow-2xl backdrop-blur" style={{ background: "rgba(15,23,42,0.94)", color: "#94a3b8", border: "1.5px solid #64748b" }}>
            ðŸ›£ï¸ {roadSnapPreview.size} tile{roadSnapPreview.size !== 1 ? "s" : ""}
          </div>
        </div>
      )}
    </div>
  );

  const screenshotDialog = (
    <Dialog open={screenshotDialogOpen} onOpenChange={(open) => { if (screenshotStatus !== "working") { if (!open) clearScreenshotPreview(); setScreenshotDialogOpen(open); } }}>
      <DialogContent
        className="w-[min(96vw,720px)] max-w-[min(96vw,720px)] max-h-[90vh] overflow-y-auto p-4 sm:p-6"
      >
        <DialogHeader>
          <DialogTitle>Map screenshot</DialogTitle>
        </DialogHeader>
        <div className="space-y-4 text-sm">
          <div className="space-y-2">
            <div className="font-medium">Quick presets</div>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
              <Button size="sm" variant="outline" onClick={() => applyScreenshotPreset("shareable")}>Shareable</Button>
              <Button size="sm" variant="outline" onClick={() => applyScreenshotPreset("planner")}>Planner</Button>
              <Button size="sm" variant="outline" onClick={() => applyScreenshotPreset("current")}>Current state</Button>
            </div>
          </div>
          <div className="space-y-2">
            <div className="font-medium">Capture area</div>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              <Button size="sm" variant={screenshotOptions.area === "full-map" ? "default" : "outline"} onClick={() => setScreenshotOptions((prev) => ({ ...prev, area: "full-map" }))}>Full map</Button>
              <Button size="sm" variant={screenshotOptions.area === "viewport" ? "default" : "outline"} onClick={() => setScreenshotOptions((prev) => ({ ...prev, area: "viewport" }))}>Current viewport</Button>
            </div>
          </div>
          <div className="space-y-2">
            <div className="font-medium">Include in screenshot</div>
            <div className="grid gap-2 sm:grid-cols-2">
              {[
                ["includeLevels", "Levels overlay"],
                ["includeGrid", "Grid"],
                ["includePen", "Pen marks"],
                ["includeDrawArea", "Draw area outlines"],
                ["includeRoads", "Roads"],
                ["includeDeployments", "Deployments"],
                ["includeReclaimed", "Reclaimed tiles"],
                ["includeChaosSetup", "Chaos setup"],
                ["includeFacilities", "Facilities"],
                ["includeSurveys", "Survey pins"],
                ["includeTownHall", "Town hall coverage"],
              ].map(([key, label]) => (
                <label key={key} className="flex items-center gap-2 rounded-md border px-3 py-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={Boolean(screenshotOptions[key as keyof ScreenshotOptions])}
                    onChange={(e) => setScreenshotOptions((prev) => ({ ...prev, [key]: e.target.checked }))}
                  />
                  <span>{label}</span>
                </label>
              ))}
            </div>
          </div>
          <div className="space-y-2">
            <div className="font-medium">Preview</div>
            <div className="rounded-md border bg-black/30 p-2 min-h-[150px] sm:min-h-[180px] flex items-center justify-center">
              {screenshotPreviewUrl ? (
                <img
                  src={screenshotPreviewUrl}
                  alt="Screenshot preview"
                  className="max-h-[220px] sm:max-h-[260px] w-auto rounded border border-white/10"
                  style={{ imageRendering: "pixelated" }}
                />
              ) : (
                <div className="text-xs opacity-70">Generate preview to review before download.</div>
              )}
            </div>
          </div>
          <div className="flex flex-col-reverse gap-2 pt-2 sm:flex-row sm:items-center sm:justify-end">
            <Button size="sm" className="w-full sm:w-auto" variant="outline" onClick={() => { clearScreenshotPreview(); setScreenshotDialogOpen(false); }} disabled={screenshotStatus === "working"}>Cancel</Button>
            <Button size="sm" className="w-full sm:w-auto" variant="outline" onClick={generateScreenshotPreview} disabled={screenshotStatus === "working"}>
              {screenshotStatus === "working" ? "Preparing..." : "Generate preview"}
            </Button>
            <Button size="sm" className="w-full sm:w-auto" onClick={downloadScreenshot} disabled={screenshotStatus === "working" || !screenshotPreviewUrl}>
              Download PNG
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );

  return (
    <>
      {notice && (
        <div className="fixed inset-0 z-[9999] flex items-center justify-center pointer-events-none">
          <div className="rounded-xl border px-5 py-3 text-sm shadow-2xl bg-background/95 backdrop-blur text-foreground">
            {notice}
          </div>
        </div>
      )}

      {/* â”€â”€ FULLSCREEN CONTAINER â”€â”€ */}
      <div
        ref={fullscreenContainerRef}
        className={isFullscreen ? "fixed inset-0 z-[9990] flex overflow-hidden" : ""}
        style={isFullscreen ? { background: currentTheme.appBg } : {}}
      >
        {/* Left toolbar — only in fullscreen */}
        {isFullscreen && (
          <div
            className="h-full overflow-y-auto flex-shrink-0 border-r transition-all duration-200"
            style={{
              width: touchMode ? (fullscreenToolbarOpen ? 156 : 56) : 240,
              background: "rgba(10,15,30,0.98)",
              borderColor: currentTheme.panelBorder,
            }}
          >
            <div className="border-b sticky top-0 z-10" style={{ borderColor: currentTheme.panelBorder, background: "rgba(10,15,30,0.98)" }}>
              {touchMode ? (
                <button
                  onClick={() => setFullscreenToolbarOpen((prev) => !prev)}
                  className="w-full h-11 px-3 flex items-center justify-between text-sm font-semibold hover:bg-white/10 transition-colors"
                  title={fullscreenToolbarOpen ? "Collapse toolbar" : "Expand toolbar"}
                >
                  <span>{fullscreenToolbarOpen ? "Map" : "Map"}</span>
                  <span className="text-lg leading-none">{fullscreenToolbarOpen ? "<" : ">"}</span>
                </button>
              ) : (
                <div className="p-2 sm:p-3 space-y-1">
                  <div className="flex items-center justify-between">
                    <span className="font-semibold text-sm">World Map</span>
                    <button
                      onClick={toggleFullscreen}
                      className="w-7 h-7 rounded-md flex items-center justify-center hover:bg-white/10 transition-colors"
                      title="Exit fullscreen"
                    >
                      <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2">
                        <path d="M8 3v3a2 2 0 0 1-2 2H3"/><path d="M21 8h-3a2 2 0 0 1-2-2V3"/>
                        <path d="M3 16h3a2 2 0 0 1 2 2v3"/><path d="M16 21v-3a2 2 0 0 1 2-2h3"/>
                      </svg>
                    </button>
                  </div>
                  <div className="text-[10px] opacity-40">{cols} × {rows} tiles</div>
                </div>
              )}
            </div>
            <div className={touchMode && !fullscreenToolbarOpen ? "p-1" : "p-2 sm:p-3"}>
              {touchMode && !fullscreenToolbarOpen ? (
                <div className="grid gap-1">
                  <button
                    onClick={() => setLayers((prev) => ({ ...prev, levels: !prev.levels }))}
                    className="w-full h-10 rounded-xl border border-white/10 bg-white/5 text-white flex items-center justify-center text-sm hover:bg-white/10 transition-colors"
                    title="Toggle levels layer"
                  >
                    Lvls
                  </button>
                  <button
                    onClick={() => setActiveToolImmediate((prev) => toggleTool(prev, "reclaim"))}
                    className={`w-full h-10 rounded-xl border ${activeTool === "reclaim" ? "border-cyan-300 bg-cyan-500/20" : "border-white/10 bg-white/5"} text-white flex items-center justify-center text-sm hover:bg-white/10 transition-colors`}
                    title="Reclaim land"
                  >
                    Reclaim
                  </button>
                  <button
                    onClick={undo}
                    className="w-full h-10 rounded-xl border border-white/10 bg-white/5 text-white flex items-center justify-center text-sm hover:bg-white/10 transition-colors"
                    title="Undo"
                  >
                    ?
                  </button>
                  <button
                    onClick={redo}
                    className="w-full h-10 rounded-xl border border-white/10 bg-white/5 text-white flex items-center justify-center text-sm hover:bg-white/10 transition-colors"
                    title="Redo"
                  >
                    ?
                  </button>
                  <button
                    onClick={() => setActiveToolImmediate((prev) => toggleTool(prev, "deploy"))}
                    className={`w-full h-10 rounded-xl border ${activeTool === "deploy" ? "border-cyan-300 bg-cyan-500/20" : "border-white/10 bg-white/5"} flex items-center justify-center px-1 hover:bg-white/10 transition-colors`}
                    title="Deploy (Manhattan circle)"
                  >
                    <div className="grid grid-cols-3 gap-[2px]" style={{ width: 26, height: 26 }}>
                      <div className="bg-cyan-400 rounded-sm"></div>
                      <div className="bg-cyan-400 rounded-sm"></div>
                      <div className="bg-cyan-400 rounded-sm"></div>
                      <div className="bg-cyan-400 rounded-sm"></div>
                      <div className="bg-cyan-500 rounded-sm"></div>
                      <div className="bg-cyan-400 rounded-sm"></div>
                      <div className="bg-cyan-400 rounded-sm"></div>
                      <div className="bg-cyan-400 rounded-sm"></div>
                      <div className="bg-transparent"></div>
                    </div>
                  </button>
                </div>
              ) : (
                <>
                  {touchMode && (
                    <div className="mb-2 flex justify-end">
                      <button
                        onClick={toggleFullscreen}
                        className="h-8 px-2 rounded-md text-xs flex items-center justify-center hover:bg-white/10 transition-colors"
                        title="Exit fullscreen"
                      >
                        Exit
                      </button>
                    </div>
                  )}
                  {toolbarContent}
                </>
              )}
            </div>
          </div>
        )}

        {/* Main content area */}
        <div className={isFullscreen ? "flex-1 h-full overflow-hidden" : ""}>
          {/* Normal page layout */}
          {!isFullscreen && (
            <main className="w-full px-3 sm:px-4 py-6 space-y-6 select-none" style={{ background: currentTheme.appBg }}>
              <div className="flex items-center gap-3 flex-wrap">
                <h1 className="text-2xl font-bold">World map (Beta)</h1>
                <span className="rounded-full border px-3 py-1 text-xs font-medium">Beta</span>
                <span className="rounded-full border px-3 py-1 text-xs font-medium">Experimental</span>
              </div>

              <p className="text-xs text-muted-foreground flex items-center gap-1 flex-wrap">
                Map data pulled from{" "}
                <a href="https://docs.google.com/spreadsheets/d/1pNx7SjpgjuKFI9Hgr21y3ammRlZjKNTTdvfLYQL7l7A/edit?gid=1473922384#gid=1473922384" target="_blank" rel="noopener noreferrer" className="underline underline-offset-2 hover:text-foreground transition-colors">Map full</a>
                {" "}by minhnim (Kingdom Adventurers EN Sheet) Â· terrain from{" "}
                <a href="https://docs.google.com/spreadsheets/d/1e5t0CMBgw2MOv1NRE-vNk3229p7dYg6yJAQ8YbhYnWk/edit?gid=1631803140#gid=1631803140" target="_blank" rel="noopener noreferrer" className="underline underline-offset-2 hover:text-foreground transition-colors">KA GameData</a>
              </p>

              {!cleanMode && !touchMode && (
                <div className="flex flex-wrap items-center gap-2 rounded-xl border px-3 py-2" style={{ background: currentTheme.panelBg, borderColor: currentTheme.panelBorder }}>
                  {/* Tools */}
                  <Button size="sm" className="h-8 w-8 p-0" title="Pan" aria-label="Pan" variant={activeTool === "none" ? "default" : "ghost"} onClick={() => setActiveToolImmediate("none")}><BubbleIconCursor /></Button>
                  <Button size="sm" className="h-8 w-8 p-0" title="Pen" aria-label="Pen" variant={activeTool === "pen" ? "default" : "ghost"} onClick={() => setActiveToolImmediate((prev) => toggleTool(prev, "pen"))}><BubbleIconPen /></Button>
                  <Button size="sm" variant={activeTool === "draw_area" ? "default" : "ghost"} onClick={() => { setActiveToolImmediate((prev) => toggleTool(prev, "draw_area")); if (brushSize < 2) setBrushSize(2); }}>Area</Button>
                  <Button size="sm" variant={activeTool === "reclaim" ? "default" : "ghost"} onClick={() => setActiveToolImmediate((prev) => toggleTool(prev, "reclaim"))}>Reclaim</Button>
                  <Button size="sm" variant={activeTool === "deploy" ? "default" : "ghost"} onClick={() => setActiveToolImmediate((prev) => toggleTool(prev, "deploy"))}>Deploy</Button>
                  <Button size="sm" className="h-8 w-8 p-0" title="Eraser" aria-label="Eraser" variant={paintMode === "erase" ? "default" : "ghost"} onClick={() => { if (activeTool === "none") setActiveToolImmediate("pen"); setPaintMode((prev) => prev === "erase" ? "mark" : "erase"); }}><BubbleIconEraser /></Button>
                  <Button size="sm" className="h-8 w-8 p-0" title="Roads" aria-label="Roads" variant={activeTool === "road" ? "default" : "ghost"} onClick={() => setActiveToolImmediate((prev) => toggleTool(prev, "road"))}><BubbleIconRoad /></Button>
                  <span className="w-px h-5 bg-border mx-1" />
                  {/* Undo / Redo */}
                  <Button size="sm" title="Undo" aria-label="Undo" variant="ghost" onClick={undo}>?</Button>
                  <Button size="sm" title="Redo" aria-label="Redo" variant="ghost" onClick={redo}>?</Button>
                  <span className="w-px h-5 bg-border mx-1" />
                  {/* Actions */}
                  <Button size="sm" className="h-8 w-8 p-0" title="Export" aria-label="Export" variant="ghost" onClick={exportToClipboard}>
                    <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 16V4"/><path d="M8 8l4-4 4 4"/><path d="M4 20h16"/></svg>
                  </Button>
                  <Button size="sm" className="h-8 w-8 p-0" title="Import" aria-label="Import" variant="ghost" onClick={importFromClipboard}>
                    <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 4v12"/><path d="M8 12l4 4 4-4"/><path d="M4 20h16"/></svg>
                  </Button>
                  <Button size="sm" className="h-8 w-8 p-0" title="Screenshot" aria-label="Screenshot" variant="ghost" onClick={openScreenshotDialog}>
                    <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2"><rect x="3" y="6" width="18" height="14" rx="2"/><path d="M8 6l1.5-2h5L16 6"/><circle cx="12" cy="13" r="3"/></svg>
                  </Button>
                  <Button size="sm" className="h-8 w-8 p-0" title="Minimap" aria-label="Minimap" variant={showMinimap ? "default" : "ghost"} onClick={() => setShowMinimap((prev) => !prev)}>
                    <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2"><path d="M3 6l6-2 6 2 6-2v14l-6 2-6-2-6 2z"/><path d="M9 4v14"/><path d="M15 6v14"/></svg>
                  </Button>
                  <span className="w-px h-5 bg-border mx-1" />
                  {/* Layers dropdown */}
                  <div className="relative" onClick={(e) => e.stopPropagation()}>
                    <Button
                      size="sm"
                      variant={layersDropdownOpen ? "default" : "ghost"}
                      onClick={() => { setLayersDropdownOpen((v) => !v); setSurveysDropdownOpen(false); }}
                    >
                      Layers ?
                    </Button>
                    {layersDropdownOpen && (
                      <div
                        className="absolute top-full left-0 mt-1 z-50 rounded-xl border shadow-2xl py-1 min-w-[150px]"
                        style={{ background: currentTheme.panelBg, borderColor: currentTheme.panelBorder }}
                      >
                        {layerOrder.map((layer) => (
                          <button
                            key={layer}
                            onClick={() => setLayers((prev) => ({ ...prev, [layer]: !prev[layer] }))}
                            className="flex items-center gap-2 w-full px-3 py-1.5 text-xs hover:bg-white/5 transition-colors"
                            style={{ color: layers[layer] ? "#fff" : "rgba(255,255,255,0.4)" }}
                          >
                            <span className="w-3 h-3 rounded-sm border flex-shrink-0" style={{ background: layers[layer] ? "#3b82f6" : "transparent", borderColor: layers[layer] ? "#3b82f6" : "rgba(255,255,255,0.2)" }} />
                            <span>{LAYER_LABELS[layer]}</span>
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                  {/* Surveys dropdown */}
                  <div className="relative" onClick={(e) => e.stopPropagation()}>
                    <Button
                      size="sm"
                      variant={surveysDropdownOpen || showSurveys ? "default" : "ghost"}
                      onClick={() => { setSurveysDropdownOpen((v) => !v); setLayersDropdownOpen(false); }}
                    >
                      Surveys ?
                    </Button>
                    {surveysDropdownOpen && (
                      <div
                        className="absolute top-full left-0 mt-1 z-50 rounded-xl border shadow-2xl py-1 min-w-[190px]"
                        style={{ background: currentTheme.panelBg, borderColor: currentTheme.panelBorder }}
                      >
                        {/* Master toggle */}
                        <button
                          onClick={() => setShowSurveys((v) => !v)}
                          className="flex items-center gap-2 w-full px-3 py-1.5 text-xs hover:bg-white/5 transition-colors border-b mb-1"
                          style={{ color: showSurveys ? "#fff" : "rgba(255,255,255,0.4)", borderColor: "rgba(255,255,255,0.08)" }}
                        >
                          <span className="w-3 h-3 rounded-sm border flex-shrink-0" style={{ background: showSurveys ? "#3b82f6" : "transparent", borderColor: showSurveys ? "#3b82f6" : "rgba(255,255,255,0.2)" }} />
                          Show survey pins
                        </button>
                        {(["storehouse", "chaos_stone", "cash_register", "dragon_taming"] as SurveyCategory[]).map((cat) => {
                          const active = visibleSurveyCats.has(cat);
                          return (
                            <button
                              key={cat}
                              onClick={() => setVisibleSurveyCats((prev) => {
                                const next = new Set(prev);
                                if (next.has(cat)) next.delete(cat); else next.add(cat);
                                return next;
                              })}
                              className="flex items-center gap-2 w-full px-3 py-1.5 text-xs hover:bg-white/5 transition-colors"
                              style={{ color: active ? "#fff" : "rgba(255,255,255,0.4)" }}
                            >
                              <span className="w-4 h-4 rounded-full flex items-center justify-center text-[9px] font-bold text-white flex-shrink-0" style={{ background: active ? SURVEY_CAT_COLORS[cat] : "rgba(255,255,255,0.1)" }}>
                                {SURVEY_CAT_LABELS[cat]}
                              </span>
                              {SURVEY_CAT_NAMES[cat]}
                            </button>
                          );
                        })}
                      </div>
                    )}
                  </div>
                </div>
              )}

              {!cleanMode && !touchMode && activeTool === "townhall" && (
                <div className="mb-3 rounded-xl border px-3 py-3 space-y-3" style={{ background: currentTheme.panelBg, borderColor: currentTheme.panelBorder }}>
                  <div className="flex gap-1">
                    <Button size="sm" variant={paintMode === "mark" ? "default" : "outline"} className="flex-1" onClick={() => setPaintMode("mark")}>Place</Button>
                    <Button size="sm" variant={paintMode === "erase" ? "default" : "outline"} className="flex-1" onClick={() => setPaintMode("erase")}>Remove</Button>
                  </div>
                  <div className="text-[10px] uppercase tracking-widest opacity-50 px-1">Town Hall level</div>
                  <div className="flex flex-wrap gap-1">
                    {[1,10,20,30,40,50,60,70,80,90,100].map((level) => (
                      <Button key={`townhall-level-${level}`} size="sm" variant={townhallLevel === level ? "default" : "outline"} onClick={() => setTownhallLevel(level)} className="px-2 py-1 text-xs">
                        {level}
                      </Button>
                    ))}
                  </div>
                  <div className="text-xs opacity-60">Buildable area: {getTownHallCoverageSize(townhallLevel)}×{getTownHallCoverageSize(townhallLevel)}</div>
                  <div className="text-xs opacity-60">Town Hall size: {TOWNHALL_SIZE}×{TOWNHALL_SIZE}</div>
                </div>
              )}

              {!cleanMode && touchMode && (
                <div className="mb-3 flex items-center justify-between gap-2">
                  <div className="text-xs opacity-70">
                    {activeTool === "none"
                      ? "Pan"
                      : activeTool === "chaos_setup"
                      ? `CS: ${chaosSetupPiece === "info_board" ? "Board" : "Stone"}`
                      : activeTool === "draw_area"
                      ? "Draw Area"
                      : activeTool === "reclaim"
                      ? reclaimMode === "reclaim" ? "Reclaim" : "Restore"
                      : activeTool === "deploy"
                      ? "Deploy"
                      : activeTool === "road"
                      ? paintMode === "erase" ? "Erase Roads" : "Roads"
                      : paintMode === "erase"
                      ? `Erase ${activeTool}`
                      : activeTool}
                  </div>
                  <div className="flex items-center gap-2">
                    <Button
                      size="sm"
                      variant={touchEditMode ? "default" : "ghost"}
                      className="h-8 px-2 text-[11px]"
                      onClick={() => setTouchEditMode((prev) => !prev)}
                    >
                      {touchEditMode ? "Touch Edit" : "Touch Pan"}
                    </Button>
                    <Button size="sm" className="h-8 w-8 p-0" title="Minimap" aria-label="Minimap" variant="ghost" onClick={() => setShowMinimap((prev) => !prev)}>
                      <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2"><path d="M3 6l6-2 6 2 6-2v14l-6 2-6-2-6 2z"/><path d="M9 4v14"/><path d="M15 6v14"/></svg>
                    </Button>
                    <Button size="sm" variant="outline" onClick={() => setMobileToolbarOpen((prev) => !prev)}>
                      {mobileToolbarOpen ? "<" : ">"}
                    </Button>
                  </div>
                </div>
              )}

              {!cleanMode && touchMode && mobileToolbarOpen && (
                <div className="mb-3 rounded-xl border p-3 space-y-3" style={{ background: currentTheme.panelBg, borderColor: currentTheme.panelBorder }}>
                  <div className="grid grid-cols-2 gap-2">
                    <Button size="sm" variant={touchEditMode ? "default" : "outline"} onClick={() => setTouchEditMode(true)}>
                      Finger Edit
                    </Button>
                    <Button size="sm" variant={!touchEditMode ? "default" : "outline"} onClick={() => setTouchEditMode(false)}>
                      Finger Pan
                    </Button>
                  </div>

                  <div className="grid grid-cols-3 gap-2">
                    <Button size="sm" className="h-8 w-8 p-0" title="Pan" aria-label="Pan" variant={activeTool === "none" ? "default" : "ghost"} onClick={() => setActiveToolImmediate("none")}><BubbleIconCursor /></Button>
                    <Button size="sm" className="h-8 w-8 p-0" title="Pen" aria-label="Pen" variant={activeTool === "pen" ? "default" : "ghost"} onClick={() => setActiveToolImmediate((prev) => toggleTool(prev, "pen"))}><BubbleIconPen /></Button>
                    <Button size="sm" variant={activeTool === "draw_area" ? "default" : "ghost"} onClick={() => { setActiveToolImmediate((prev) => toggleTool(prev, "draw_area")); if (brushSize < 2) setBrushSize(2); }}>Area</Button>
                    <Button size="sm" variant={activeTool === "chaos_setup" ? "default" : "ghost"} onClick={() => setActiveToolImmediate((prev) => toggleTool(prev, "chaos_setup"))}>CS</Button>
                    <Button size="sm" variant={activeTool === "reclaim" ? "default" : "ghost"} onClick={() => setActiveToolImmediate((prev) => toggleTool(prev, "reclaim"))}>Reclaim</Button>
                    <Button size="sm" variant={activeTool === "deploy" ? "default" : "ghost"} onClick={() => setActiveToolImmediate((prev) => toggleTool(prev, "deploy"))}>Deploy</Button>
                    <Button size="sm" className="h-8 w-8 p-0" title="Eraser" aria-label="Eraser" variant={paintMode === "erase" ? "default" : "ghost"} onClick={() => { if (activeTool === "none") setActiveToolImmediate("pen"); setPaintMode((prev) => prev === "erase" ? "mark" : "erase"); }}><BubbleIconEraser /></Button>
                    <Button size="sm" className="h-8 w-8 p-0" title="Roads" aria-label="Roads" variant={activeTool === "road" ? "default" : "ghost"} onClick={() => setActiveToolImmediate((prev) => toggleTool(prev, "road"))}><BubbleIconRoad /></Button>
                    <Button size="sm" title="Undo" aria-label="Undo" variant="ghost" onClick={undo}>?</Button>
                    <Button size="sm" title="Redo" aria-label="Redo" variant="ghost" onClick={redo}>?</Button>
                  </div>

                  {activeTool === "chaos_setup" && (
                    <div className="grid grid-cols-2 gap-2">
                      <Button size="sm" variant={chaosSetupPiece === "info_board" ? "default" : "outline"} onClick={() => setChaosSetupPiece("info_board")}>Board</Button>
                      <Button size="sm" variant={chaosSetupPiece === "chaos_stone" ? "default" : "outline"} onClick={() => setChaosSetupPiece("chaos_stone")}>Stone</Button>
                    </div>
                  )}

                  <div className="grid grid-cols-2 gap-2">
                    <Button size="sm" variant={layersDropdownOpen ? "default" : "ghost"} onClick={() => { setLayersDropdownOpen((v) => !v); setSurveysDropdownOpen(false); }}>
                      Layers
                    </Button>
                    <Button size="sm" variant={surveysDropdownOpen || showSurveys ? "default" : "ghost"} onClick={() => { setSurveysDropdownOpen((v) => !v); setLayersDropdownOpen(false); }}>
                      Surveys
                    </Button>
                    <Button size="sm" className="h-8 w-8 p-0" title="Export" aria-label="Export" variant="ghost" onClick={exportToClipboard}>
                      <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 16V4"/><path d="M8 8l4-4 4 4"/><path d="M4 20h16"/></svg>
                    </Button>
                    <Button size="sm" className="h-8 w-8 p-0" title="Import" aria-label="Import" variant="ghost" onClick={importFromClipboard}>
                      <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 4v12"/><path d="M8 12l4 4 4-4"/><path d="M4 20h16"/></svg>
                    </Button>
                    <Button size="sm" variant="ghost" onClick={resetMap}>Reset</Button>
                    <Button size="sm" className="h-8 w-8 p-0" title="Screenshot" aria-label="Screenshot" variant="ghost" onClick={openScreenshotDialog}>
                      <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2"><rect x="3" y="6" width="18" height="14" rx="2"/><path d="M8 6l1.5-2h5L16 6"/><circle cx="12" cy="13" r="3"/></svg>
                    </Button>
                    <Button size="sm" variant="ghost" onClick={toggleFullscreen}>Fullscreen</Button>
                  </div>

                  {layersDropdownOpen && (
                    <div className="grid grid-cols-2 gap-2 rounded-lg border p-2" style={{ borderColor: currentTheme.panelBorder }}>
                      {layerOrder.map((layer) => (
                        <button
                          key={layer}
                          onClick={() => setLayers((prev) => ({ ...prev, [layer]: !prev[layer] }))}
                          className="flex items-center gap-2 rounded-md px-2 py-1.5 text-xs transition-colors hover:bg-white/5"
                          style={{ color: layers[layer] ? "#fff" : "rgba(255,255,255,0.4)" }}
                        >
                          <span className="w-3 h-3 rounded-sm border flex-shrink-0" style={{ background: layers[layer] ? "#3b82f6" : "transparent", borderColor: layers[layer] ? "#3b82f6" : "rgba(255,255,255,0.2)" }} />
                          <span>{LAYER_LABELS[layer]}</span>
                        </button>
                      ))}
                    </div>
                  )}

                  {surveysDropdownOpen && (
                    <div className="space-y-2 rounded-lg border p-2" style={{ borderColor: currentTheme.panelBorder }}>
                      <button
                        onClick={() => setShowSurveys((v) => !v)}
                        className="flex items-center gap-2 w-full px-2 py-1.5 text-xs rounded-md hover:bg-white/5 transition-colors"
                        style={{ color: showSurveys ? "#fff" : "rgba(255,255,255,0.4)" }}
                      >
                        <span className="w-3 h-3 rounded-sm border flex-shrink-0" style={{ background: showSurveys ? "#3b82f6" : "transparent", borderColor: showSurveys ? "#3b82f6" : "rgba(255,255,255,0.2)" }} />
                        Show survey pins
                      </button>
                      <div className="grid grid-cols-2 gap-2">
                        {(["storehouse", "chaos_stone", "cash_register", "dragon_taming"] as SurveyCategory[]).map((cat) => {
                          const active = visibleSurveyCats.has(cat);
                          return (
                            <button
                              key={cat}
                              onClick={() => setVisibleSurveyCats((prev) => {
                                const next = new Set(prev);
                                if (next.has(cat)) next.delete(cat); else next.add(cat);
                                return next;
                              })}
                              className="flex items-center gap-2 rounded-md px-2 py-1.5 text-xs transition-colors hover:bg-white/5"
                              style={{ color: active ? "#fff" : "rgba(255,255,255,0.4)" }}
                            >
                              <span className="w-4 h-4 rounded-full flex items-center justify-center text-[9px] font-bold text-white flex-shrink-0" style={{ background: active ? SURVEY_CAT_COLORS[cat] : "rgba(255,255,255,0.1)" }}>
                                {SURVEY_CAT_LABELS[cat]}
                              </span>
                              {SURVEY_CAT_LABELS[cat]}
                            </button>
                          );
                        })}
                      </div>
                    </div>
                  )}
                </div>
              )}

              <div className={cleanMode || isFullscreen || activeTool !== "none" ? "" : "grid gap-6 lg:grid-cols-[minmax(0,1fr)_320px]"}>
                <Card style={{ background: currentTheme.panelBg, borderColor: currentTheme.panelBorder }} className={cleanMode ? "col-span-full" : ""}>
                  <CardContent className="p-4">
                    <div className="mb-3 flex items-center justify-between gap-3 flex-wrap">
                      <div>
                        <div className="font-semibold">Tile map planner</div>
                        <div className="text-sm opacity-70">Wheel zooms &middot; right-drag pans &middot; click+drag paints</div>
                      </div>
                      <div className="flex items-center gap-2">
                        {cleanMode && (
                          <button onClick={() => setCleanMode(false)} className="flex items-center gap-1 text-xs px-2 py-1 rounded-md border border-border bg-background hover:bg-muted transition-colors">
                            <span className="text-base leading-none">&times;</span> Exit screenshot mode
                          </button>
                        )}
                        <div className="text-xs opacity-70">{cols} &times; {rows}</div>
                        <button
                          onClick={toggleFullscreen}
                          className="flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs hover:bg-white/5 transition-colors"
                          style={{ borderColor: currentTheme.panelBorder }}
                          title="Enter fullscreen"
                        >
                          <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="2">
                            <path d="M3 8V5a2 2 0 0 1 2-2h3"/><path d="M16 3h3a2 2 0 0 1 2 2v3"/>
                            <path d="M21 16v3a2 2 0 0 1-2 2h-3"/><path d="M8 21H5a2 2 0 0 1-2-2v-3"/>
                          </svg>
                          Fullscreen
                        </button>
                      </div>
                    </div>
                    {mapViewport}
                  </CardContent>
                </Card>

                {!cleanMode && !isFullscreen && activeTool === "none" && (
                  <div className="space-y-6">
                    <Card style={{ background: currentTheme.panelBg, borderColor: currentTheme.panelBorder }}>
                      <CardContent className="p-4 space-y-3">
                        <div className="font-semibold">Tile info</div>
                        {visibleInfoTile ? (
                          <div className="space-y-2 text-sm">
                            {isLand(visibleInfoTile) ? (
                              <>
                                <div className="flex items-center justify-between gap-3">
                                  <div>
                                    <div>Coords: x={visibleInfoTile.x}, y={visibleInfoTile.y}</div>
                                    <div>Native zone: x={visibleInfoTile.nativeX}, y={visibleInfoTile.nativeY}</div>
                                  </div>
                                  <Button size="sm" variant="outline" onClick={copyVisibleTileInfo}>Copy</Button>
                                </div>
                                <div>Biome: <span className="capitalize">{visibleInfoTile.terrain}</span></div>
                                <div>Level: {visibleInfoTile.level}</div>
                                <div>Raw tile ID: {visibleInfoTile.fullTerrainId ?? "n/a"}</div>
                              </>
                            ) : (
                              <>
                                <div>Biome: Water</div>
                                <div>Level: —</div>
                              </>
                            )}
                          </div>
                        ) : (
                          <div className="text-sm opacity-70">Hover or tap a tile.</div>
                        )}
                      </CardContent>
                    </Card>

                    <Card style={{ background: currentTheme.panelBg, borderColor: currentTheme.panelBorder }}>
                      <CardContent className="p-4 space-y-3">
                        <div className="font-semibold">Deployment coverage</div>
                        <div className="pt-2 space-y-2">
                          <div className="text-xs uppercase tracking-wide opacity-70">By biome + level</div>
                          {deploymentCoverageByBiomeLevel.length > 0 ? (
                            <div className="space-y-1 max-h-48 overflow-auto pr-1">
                              {deploymentCoverageByBiomeLevel.map((entry) => (
                                <div key={entry.label} className="flex items-center justify-between gap-2 text-sm rounded-md border px-2 py-1">
                                  <span className="truncate capitalize">{entry.label}</span>
                                  <span className="text-xs opacity-70">{entry.count}</span>
                                </div>
                              ))}
                            </div>
                          ) : (
                            <div className="text-sm opacity-70">No deployments placed.</div>
                          )}
                        </div>
                      </CardContent>
                    </Card>

                    <Card style={{ background: currentTheme.panelBg, borderColor: currentTheme.panelBorder }}>
                      <CardContent className="p-4 space-y-3">
                        <div className="font-semibold">Legend</div>
                        <div className="grid grid-cols-2 gap-2 text-sm">
                          <div className="flex items-center justify-between gap-2 rounded-lg border px-3 py-2">
                            <div className="flex items-center gap-2">
                              <div className="h-4 w-4 rounded-sm border" style={{ backgroundColor: currentTheme.water }} />
                              <span>water</span>
                            </div>
                          </div>
                          {(["grass", "sand", "swamp", "rock", "snow", "volcano", "ground"] as TerrainType[]).map((terrain) => (
                            <div key={terrain} className="flex items-center justify-between gap-2 rounded-lg border px-3 py-2">
                              <div className="flex items-center gap-2">
                                <div className="h-4 w-4 rounded-sm border" style={{ backgroundColor: TERRAIN_COLORS[terrain] }} />
                                <span className="capitalize">{terrain}</span>
                              </div>
                              <span className="text-xs opacity-70">{effectiveTerrainCounts[terrain]}</span>
                            </div>
                          ))}
                        </div>
                      </CardContent>
                    </Card>
                  </div>
                )}
              </div>
            </main>
          )}

          {/* Fullscreen map — takes entire right area */}
          {isFullscreen && (
            <div className="w-full h-full select-none">
              {mapViewport}
            </div>
          )}
        </div>

        {screenshotDialog}
      </div>
    </>
  );
}



