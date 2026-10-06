import { ResearchPreviewGate } from "@/components/ka/research-preview-gate";
import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Slider } from "@/components/ui/slider";
import { Check, ChevronDown, Eraser, Shield, Skull } from "lucide-react";
import RuntimeWorldRenderTestPage, { type RuntimeCellHighlightOverlay, type RuntimeExternalOverlay } from "./runtime-world-render-test";
import { NATIVE_MAP, mapTerrainCodeToType, parseTerrainMapCsv, type TerrainType } from "@/lib/monster-truth";
import { renderCharacterPreview } from "@/lib/character-renderer";
import fullTerrainCsv from "../data/full-terrain-map.csv?raw";
import chaosStoneSpriteUrl from "../../../../website_icons/facilities_confirmed/facility_191_chaos_stone.png";
import signboardSpriteUrl from "../../../../website_icons/facilities_confirmed/facility_068_info_board.png";

type Tool = "none" | "stone" | "board" | "monster" | "unit" | "reclaim" | "erase";
type Piece = "stone" | "board";
type DemoSeamMode = "auto" | "first" | "last";
type ReclaimMode = "claim" | "unclaim";
type AnchorScore = {
  reclaimCount: number;
  unreachable: number;
  spread: number;
  maxDistance: number;
  totalDistance: number;
  centerBias: number;
};
type ResultCycleState = {
  options: Point[][];
  index: number;
  focus: Point;
  basePaintedSeaTiles: Set<string>;
  baseReclaimedTiles: Set<string>;
};

type Point = { x: number; y: number };

const FULL_TERRAIN_MAP = parseTerrainMapCsv(fullTerrainCsv);
const ROWS = FULL_TERRAIN_MAP.length;
const COLS = FULL_TERRAIN_MAP[0]?.length ?? 0;
const TILE_PX_MAX = 10;
const ZOOM_MIN = 0.5;
const ZOOM_MAX = 14;
const ZOOM_STEP = 0.25;
const WORLD_MAP_WATER_COLOR = "#6d96db";
const CHAOS_STONE_SPRITE = chaosStoneSpriteUrl;
const SIGNBOARD_SPRITE = signboardSpriteUrl;
const LEGENDARY_CAVE_FACILITY_ID = 196;
const TOOL_BUTTON_BASE_CLASS = "flex h-10 w-10 items-center justify-center rounded-full border transition-colors";
const TOOL_BUTTON_ACTIVE_CLASS = "border-cyan-300/70 bg-cyan-400/20 text-cyan-100";
const TOOL_BUTTON_IDLE_CLASS = "border-border/60 bg-background/85 text-muted-foreground hover:border-cyan-300/60 hover:bg-cyan-400/10 hover:text-foreground";

const TERRAIN_COLORS: Record<TerrainType, string> = {
  grass: "#2f7d32",
  sand: "#c6ad62",
  volcano: "#b94f45",
  swamp: "#2f7d73",
  rock: "#8a8f98",
  snow: "#a9c8dc",
  ground: "#a87b1d",
};

function terrainAt(x: number, y: number): TerrainType | undefined {
  return mapTerrainCodeToType(FULL_TERRAIN_MAP[y]?.[x]);
}

function getNativeIndex(index: number, cellCount: number, nativeCount: number) {
  return Math.min(nativeCount - 1, Math.floor((index * nativeCount) / cellCount));
}

function levelAt(x: number, y: number): number | undefined {
  if (!inside(x, y)) return undefined;
  const nativeRows = NATIVE_MAP.length;
  const nativeCols = NATIVE_MAP[0]?.length ?? 0;
  const ny = getNativeIndex(y, ROWS, nativeRows);
  const nx = getNativeIndex(x, COLS, nativeCols);
  const raw = NATIVE_MAP[ny]?.[nx]?.level;
  if (!Number.isFinite(raw)) return undefined;
  return raw;
}

function keyOf(x: number, y: number) {
  return `${x},${y}`;
}

function parseKey(key: string): Point {
  const [x, y] = key.split(",").map(Number);
  return { x, y };
}

function inside(x: number, y: number) {
  return x >= 0 && y >= 0 && x < COLS && y < ROWS;
}

function stoneFootprint(anchor: Point): Point[] {
  const { x, y } = anchor;
  return [
    { x, y },
    { x: x + 1, y },
    { x, y: y + 1 },
    { x: x + 1, y: y + 1 },
  ];
}

function eastFrontSpawn(anchor: Point): Point {
  // Spawn origin is the south-east tile of the 2x2 stone footprint.
  return { x: anchor.x + 1, y: anchor.y + 1 };
}

function stoneDirectionalBounds(anchor: Point) {
  return {
    minX: anchor.x - 2,
    maxX: anchor.x + 5,
    minY: anchor.y - 3,
    maxY: anchor.y + 4,
  };
}

function withinStoneDirectionalRange(anchor: Point, p: Point) {
  const b = stoneDirectionalBounds(anchor);
  return p.x >= b.minX && p.x <= b.maxX && p.y >= b.minY && p.y <= b.maxY;
}

function movementEnvelope(anchor: Point): Set<string> {
  const b = stoneDirectionalBounds(anchor);
  const out = new Set<string>();
  for (let y = b.minY; y <= b.maxY; y += 1) {
    for (let x = b.minX; x <= b.maxX; x += 1) {
      if (inside(x, y)) out.add(keyOf(x, y));
    }
  }
  return out;
}

function stepDistance(a: Point, b: Point) {
  return Math.abs(a.x - b.x) + Math.abs(a.y - b.y);
}

function cardinalStepsInRange(anchor: Point, from: Point, to: Point): number | null {
  if (!inside(from.x, from.y) || !inside(to.x, to.y)) return null;
  if (!withinStoneDirectionalRange(anchor, from) || !withinStoneDirectionalRange(anchor, to)) return null;
  // Profile distance: cardinal movement only, without obstacle-routing bias.
  return stepDistance(from, to);
}

function getProfileTargets(stoneCount: number) {
  if (stoneCount <= 1) return { maxDistance: 4, maxSpread: 1 };
  if (stoneCount === 2) return { maxDistance: 5, maxSpread: 1 };
  if (stoneCount === 3) return { maxDistance: 6, maxSpread: 1 };
  if (stoneCount === 4) return { maxDistance: 6, maxSpread: 1 };
  if (stoneCount === 5) return { maxDistance: 7, maxSpread: 2 };
  if (stoneCount === 6) return { maxDistance: 7, maxSpread: 2 };
  return { maxDistance: 8, maxSpread: 3 };
}

function isSeaAtKey(key: string, baseSea: Set<string>, paintedSea: Set<string>, reclaimed: Set<string>) {
  if (reclaimed.has(key)) return false;
  return baseSea.has(key) || paintedSea.has(key);
}

function compareAnchorScores(a: AnchorScore, b: AnchorScore) {
  if (a.unreachable !== b.unreachable) return a.unreachable < b.unreachable ? -1 : 1;
  if (a.reclaimCount !== b.reclaimCount) return a.reclaimCount < b.reclaimCount ? -1 : 1;
  if (a.spread !== b.spread) return a.spread < b.spread ? -1 : 1;
  if (a.maxDistance !== b.maxDistance) return a.maxDistance < b.maxDistance ? -1 : 1;
  if (a.totalDistance !== b.totalDistance) return a.totalDistance < b.totalDistance ? -1 : 1;
  if (a.centerBias !== b.centerBias) return a.centerBias < b.centerBias ? -1 : 1;
  return 0;
}

function clampZoom(value: number) {
  return Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, +value.toFixed(2)));
}

function UnverifiedChaosSetupLabPage() {
  const mapViewportRef = useRef<HTMLDivElement | null>(null);
  const mapGridRef = useRef<HTMLDivElement | null>(null);
  const previousMapZoomRef = useRef<number>(1);
  const [tool, setTool] = useState<Tool>("none");
  const [reclaimMode, setReclaimMode] = useState<ReclaimMode>("claim");
  const [showCoverageCheck, setShowCoverageCheck] = useState<boolean>(false);
  const [demoStoneCount, setDemoStoneCount] = useState<number>(1);
  const [demoAxis, setDemoAxis] = useState<"x" | "y">("x");
  const [demoSeamMode, setDemoSeamMode] = useState<DemoSeamMode>("auto");
  const [showDebugLabels, setShowDebugLabels] = useState<boolean>(false);
  const [showLevelOverlay, setShowLevelOverlay] = useState<boolean>(false);
  const [mapZoom, setMapZoom] = useState<number>(1);
  const [pickSetupAreaFromMap, setPickSetupAreaFromMap] = useState<boolean>(false);
  const [selectedSetupArea, setSelectedSetupArea] = useState<Point | null>(null);
  const [hoverTile, setHoverTile] = useState<Point | null>(null);
  const [locationInput, setLocationInput] = useState<string>("");
  const locationInputRef = useRef<HTMLInputElement | null>(null);
  const levelPickerRef = useRef<HTMLDivElement | null>(null);
  const [showLevelDropdown, setShowLevelDropdown] = useState<boolean>(false);
  const [paintedSeaTiles, setPaintedSeaTiles] = useState<Set<string>>(() => new Set());
  const [reclaimedTiles, setReclaimedTiles] = useState<Set<string>>(() => new Set());
  const [pieces, setPieces] = useState<Map<string, Piece>>(() => new Map());
  const [openMonsterTile, setOpenMonsterTile] = useState<Point | null>(null);
  const [openUnitTile, setOpenUnitTile] = useState<Point | null>(null);
  const [unitMarkerSpriteUrl, setUnitMarkerSpriteUrl] = useState<string | null>(null);
  const [generatorError, setGeneratorError] = useState<string | null>(null);
  const [copyStatus, setCopyStatus] = useState<string | null>(null);
  const [resultCycle, setResultCycle] = useState<ResultCycleState | null>(null);
  const [nextResultAnimating, setNextResultAnimating] = useState<boolean>(false);
  const [runtimeFocusCell, setRuntimeFocusCell] = useState<{ x: number; y: number; token: number } | null>(null);
  const nextResultAnimationTimerRef = useRef<number | null>(null);

  useEffect(() => {
    if (!showLevelDropdown) return;
    const onPointerDown = (event: MouseEvent) => {
      if (!levelPickerRef.current) return;
      const target = event.target as Node | null;
      if (target && levelPickerRef.current.contains(target)) return;
      setShowLevelDropdown(false);
    };
    window.addEventListener("mousedown", onPointerDown);
    return () => window.removeEventListener("mousedown", onPointerDown);
  }, [showLevelDropdown]);

  useEffect(() => {
    return () => {
      if (nextResultAnimationTimerRef.current != null) {
        window.clearTimeout(nextResultAnimationTimerRef.current);
      }
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    const previewCanvas = document.createElement("canvas");
    renderCharacterPreview(previewCanvas, {
      jobName: "Archer",
      rank: "A",
      variant: 1,
      equipState: "right",
      scale: 2,
      poseFrame: 1,
    })
      .then((ok) => {
        if (cancelled || !ok) return;
        setUnitMarkerSpriteUrl(previewCanvas.toDataURL("image/png"));
      })
      .catch(() => {
        if (!cancelled) setUnitMarkerSpriteUrl(null);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const viewport = mapViewportRef.current;
    const previousZoom = previousMapZoomRef.current;

    if (!viewport || previousZoom === mapZoom) {
      previousMapZoomRef.current = mapZoom;
      return;
    }

    const centerXInBaseScale = (viewport.scrollLeft + viewport.clientWidth / 2) / Math.max(previousZoom, 0.01);
    const centerYInBaseScale = (viewport.scrollTop + viewport.clientHeight / 2) / Math.max(previousZoom, 0.01);

    requestAnimationFrame(() => {
      const nextScrollLeft = centerXInBaseScale * mapZoom - viewport.clientWidth / 2;
      const nextScrollTop = centerYInBaseScale * mapZoom - viewport.clientHeight / 2;
      const maxScrollLeft = Math.max(0, viewport.scrollWidth - viewport.clientWidth);
      const maxScrollTop = Math.max(0, viewport.scrollHeight - viewport.clientHeight);

      viewport.scrollLeft = Math.max(0, Math.min(maxScrollLeft, nextScrollLeft));
      viewport.scrollTop = Math.max(0, Math.min(maxScrollTop, nextScrollTop));
    });

    previousMapZoomRef.current = mapZoom;
  }, [mapZoom]);

  const baseSeaTiles = useMemo(() => {
    const out = new Set<string>();
    for (let y = 0; y < ROWS; y += 1) {
      for (let x = 0; x < COLS; x += 1) {
        // Real map mask: non-terrain cells are outside playable shape.
        if (!terrainAt(x, y)) {
          out.add(keyOf(x, y));
        }
      }
    }
    return out;
  }, []);

  const seaTiles = useMemo(() => {
    const out = new Set<string>();
    baseSeaTiles.forEach((k) => {
      if (!reclaimedTiles.has(k)) out.add(k);
    });
    paintedSeaTiles.forEach((k) => {
      if (!reclaimedTiles.has(k)) out.add(k);
    });
    return out;
  }, [baseSeaTiles, reclaimedTiles, paintedSeaTiles]);

  const stoneAnchors = useMemo(() => {
    const anchors: Point[] = [];
    pieces.forEach((piece, k) => {
      if (piece === "stone") anchors.push(parseKey(k));
    });
    anchors.sort((a, b) => (a.y - b.y) || (a.x - b.x));
    return anchors;
  }, [pieces]);

  const stoneIdByAnchorKey = useMemo(() => {
    const out = new Map<string, number>();
    stoneAnchors.forEach((anchor, idx) => {
      out.set(keyOf(anchor.x, anchor.y), idx + 1);
    });
    return out;
  }, [stoneAnchors]);

  const spawnIdByKey = useMemo(() => {
    const out = new Map<string, number>();
    stoneAnchors.forEach((anchor, idx) => {
      const spawn = eastFrontSpawn(anchor);
      if (inside(spawn.x, spawn.y)) out.set(keyOf(spawn.x, spawn.y), idx + 1);
    });
    return out;
  }, [stoneAnchors]);

  const stoneReachChecks = useMemo(() => {
    return stoneAnchors.map((anchor) => {
      const id = stoneIdByAnchorKey.get(keyOf(anchor.x, anchor.y)) ?? 0;
      const spawn = eastFrontSpawn(anchor);
      const distanceToM = openMonsterTile
        ? cardinalStepsInRange(anchor, spawn, openMonsterTile)
        : null;
      const canReachM = distanceToM != null;
      return { id, anchor, spawn, canReachM, distanceToM };
    });
  }, [stoneAnchors, stoneIdByAnchorKey, openMonsterTile]);

  const stoneLevelSummary = useMemo(() => {
    const levels = stoneAnchors
      .map((a) => levelAt(a.x, a.y))
      .filter((lv): lv is number => Number.isFinite(lv));
    if (levels.length === 0) return { min: null, max: null, avg: null };
    const min = Math.min(...levels);
    const max = Math.max(...levels);
    const avg = Math.round(levels.reduce((sum, lv) => sum + lv, 0) / levels.length);
    return { min, max, avg };
  }, [stoneAnchors]);

  const levelOptions = useMemo(() => {
    const out = new Set<number>();
    for (let y = 0; y < ROWS; y += 1) {
      for (let x = 0; x < COLS; x += 1) {
        const lv = levelAt(x, y);
        if (lv == null) continue;
        out.add(lv);
      }
    }
    return Array.from(out).sort((a, b) => a - b);
  }, []);

  const levelTilesByLevel = useMemo(() => {
    const byLevel = new Map<number, Point[]>();
    for (let y = 0; y < ROWS; y += 1) {
      for (let x = 0; x < COLS; x += 1) {
        const lv = levelAt(x, y);
        if (lv == null) continue;
        const list = byLevel.get(lv) ?? [];
        list.push({ x, y });
        byLevel.set(lv, list);
      }
    }
    return byLevel;
  }, []);

  const nativeLevelLabelByKey = useMemo(() => {
    const labels = new Map<string, number>();
    const nativeRows = NATIVE_MAP.length;
    const nativeCols = NATIVE_MAP[0]?.length ?? 0;

    for (let ny = 0; ny < nativeRows; ny += 1) {
      for (let nx = 0; nx < nativeCols; nx += 1) {
        const native = NATIVE_MAP[ny]?.[nx];
        if (!native) continue;

        const minX = Math.floor((nx * COLS) / nativeCols);
        const maxX = Math.floor(((nx + 1) * COLS) / nativeCols) - 1;
        const minY = Math.floor((ny * ROWS) / nativeRows);
        const maxY = Math.floor(((ny + 1) * ROWS) / nativeRows) - 1;
        if (maxX < minX || maxY < minY) continue;

        const centerX = Math.floor((minX + maxX) / 2);
        const centerY = Math.floor((minY + maxY) / 2);
        labels.set(keyOf(centerX, centerY), native.level);
      }
    }

    return labels;
  }, []);

  useEffect(() => {
    if (!selectedSetupArea) {
      setLocationInput("");
      return;
    }
    const lv = levelAt(selectedSetupArea.x, selectedSetupArea.y);
    setLocationInput(lv != null ? String(lv) : "");
  }, [selectedSetupArea]);

  const distanceProfile = useMemo(() => {
    const stoneCount = stoneAnchors.length;
    const target = getProfileTargets(stoneCount);
    const distances = stoneReachChecks
      .map((s) => s.distanceToM)
      .filter((d): d is number => typeof d === "number");
    const maxDistance = distances.length > 0 ? Math.max(...distances) : null;
    const minDistance = distances.length > 0 ? Math.min(...distances) : null;
    const spread = maxDistance != null && minDistance != null ? maxDistance - minDistance : null;
    const allReach = stoneReachChecks.every((s) => s.canReachM);
    const distancePass = maxDistance != null ? maxDistance <= target.maxDistance : false;
    const spreadPass = spread != null ? spread <= target.maxSpread : false;
    return {
      target,
      maxDistance,
      minDistance,
      spread,
      allReach,
      distancePass,
      spreadPass,
      overallPass: stoneCount > 0 && allReach && distancePass && spreadPass,
    };
  }, [stoneAnchors.length, stoneReachChecks]);

  const boardIdByKey = useMemo(() => {
    const boardKeys: string[] = [];
    pieces.forEach((piece, k) => {
      if (piece === "board") boardKeys.push(k);
    });
    boardKeys.sort((a, b) => {
      const pa = parseKey(a);
      const pb = parseKey(b);
      return (pa.y - pb.y) || (pa.x - pb.x);
    });
    const out = new Map<string, number>();
    boardKeys.forEach((k, idx) => out.set(k, idx + 1));
    return out;
  }, [pieces]);

  const occupiedByStones = useMemo(() => {
    const occupied = new Set<string>();
    stoneAnchors.forEach((anchor) => {
      stoneFootprint(anchor).forEach((p) => {
        if (inside(p.x, p.y)) occupied.add(keyOf(p.x, p.y));
      });
    });
    return occupied;
  }, [stoneAnchors]);

  const blockedFacilityTiles = useMemo(() => {
    const blocked = new Set<string>();
    const addRect = (x0: number, y0: number, width: number, height: number) => {
      for (let y = y0; y < y0 + height; y += 1) {
        for (let x = x0; x < x0 + width; x += 1) {
          if (inside(x, y)) blocked.add(keyOf(x, y));
        }
      }
    };

    // One-piece facilities use a 2x2 footprint rooted at (cellX - 2, cellY - 2).
    addRect(88 - 2, 24 - 2, 2, 2); // Legendary Cave

    // Port footprints mirror runtime-world-render-test port foundation and bridge coverage.
    const ports = [
      { cellX: 152, cellY: 104 },
      { cellX: 152, cellY: 40 },
    ];
    ports.forEach((port) => {
      const baseX = port.cellX - 2;
      const baseY = port.cellY - 2;
      addRect(baseX, baseY - 2, 6, 4); // foundation + rear strip
      addRect(baseX + 6, baseY - 1, 4, 2); // bridge deck
    });

    return blocked;
  }, []);

  const placementPreview = useMemo(() => {
    const cells = new Set<string>();
    if (!hoverTile || (tool !== "stone" && tool !== "board" && tool !== "monster" && tool !== "unit")) {
      return { kind: null as "stone" | "board" | "monster" | "unit" | null, valid: false, cells };
    }

    if (tool === "monster" || tool === "unit") {
      const k = keyOf(hoverTile.x, hoverTile.y);
      cells.add(k);
      const valid = !seaTiles.has(k) && !occupiedByStones.has(k) && !blockedFacilityTiles.has(k);
      return { kind: tool, valid, cells };
    }

    if (tool === "board") {
      const k = keyOf(hoverTile.x, hoverTile.y);
      cells.add(k);
      const valid = !seaTiles.has(k) && !occupiedByStones.has(k) && !blockedFacilityTiles.has(k);
      return { kind: "board" as const, valid, cells };
    }

    const valid = isStoneAnchorValid(hoverTile, seaTiles, pieces, blockedFacilityTiles);
    stoneFootprint(hoverTile).forEach((p) => {
      if (inside(p.x, p.y)) cells.add(keyOf(p.x, p.y));
    });
    return { kind: "stone" as const, valid, cells };
  }, [blockedFacilityTiles, hoverTile, tool, seaTiles, occupiedByStones, pieces]);

  const runtimeMapZoom = useMemo(() => {
    return Math.max(0.14, Math.min(3.2, mapZoom * 0.2));
  }, [mapZoom]);

  const runtimeLandOverrideCells = useMemo(() => {
    return Array.from(reclaimedTiles).map(parseKey);
  }, [reclaimedTiles]);

  const runtimeReclaimedTintCells = useMemo(() => {
    return Array.from(reclaimedTiles).map(parseKey);
  }, [reclaimedTiles]);

  const runtimeOverlays = useMemo<RuntimeExternalOverlay[]>(() => {
    const out: RuntimeExternalOverlay[] = [];

    stoneAnchors.forEach((anchor, idx) => {
      out.push({
        id: `stone-${idx + 1}`,
        cellX: anchor.x + 0.5,
        cellY: anchor.y + 0.5,
        widthTiles: 2,
        spriteUrl: CHAOS_STONE_SPRITE,
        spriteScale: 1.02,
        anchorOffsetTilesY: 0.28,
      });
    });

    boardIdByKey.forEach((_id, key) => {
      const p = parseKey(key);
      out.push({
        id: `board-${key}`,
        cellX: p.x + 0.5,
        cellY: p.y + 0.5,
        widthTiles: 1,
        spriteUrl: SIGNBOARD_SPRITE,
        spriteScale: 0.437,
        anchorOffsetTilesY: 0.24,
      });
    });

    if (openMonsterTile) {
      out.push({
        id: "open-monster",
        cellX: openMonsterTile.x + 0.5,
        cellY: openMonsterTile.y + 0.5,
        markerKind: "monster",
        anchorMode: "center",
      });
    }

    if (openUnitTile) {
      out.push({
        id: "open-unit",
        cellX: openUnitTile.x + 0.5,
        cellY: openUnitTile.y + 0.5,
        ...(unitMarkerSpriteUrl
          ? {
              widthTiles: 1,
              spriteUrl: unitMarkerSpriteUrl,
              spriteScale: 0.403,
              anchorOffsetTilesY: 0.22,
            }
          : {
              markerText: "U",
              markerKind: "unit" as const,
            }),
      });
    }

    if (hoverTile && placementPreview.kind === "stone") {
      out.push({
        id: "preview-stone",
        cellX: hoverTile.x + 0.5,
        cellY: hoverTile.y + 0.5,
        widthTiles: 2,
        spriteUrl: CHAOS_STONE_SPRITE,
        spriteScale: 1.02,
        anchorOffsetTilesY: 0.28,
        invalid: !placementPreview.valid,
        opacity: 0.62,
      });
    }

    if (hoverTile && placementPreview.kind === "board") {
      out.push({
        id: "preview-board",
        cellX: hoverTile.x + 0.5,
        cellY: hoverTile.y + 0.5,
        widthTiles: 1,
        spriteUrl: SIGNBOARD_SPRITE,
        spriteScale: 0.437,
        anchorOffsetTilesY: 0.24,
        invalid: !placementPreview.valid,
        opacity: 0.62,
      });
    }

    if (hoverTile && placementPreview.kind === "monster") {
      out.push({
        id: "preview-monster",
        cellX: hoverTile.x + 0.5,
        cellY: hoverTile.y + 0.5,
        markerKind: "monster",
        anchorMode: "center",
        invalid: !placementPreview.valid,
        opacity: 0.62,
      });
    }

    if (hoverTile && placementPreview.kind === "unit") {
      out.push({
        id: "preview-unit",
        cellX: hoverTile.x + 0.5,
        cellY: hoverTile.y + 0.5,
        ...(unitMarkerSpriteUrl
          ? {
              widthTiles: 1,
              spriteUrl: unitMarkerSpriteUrl,
              spriteScale: 0.403,
              anchorOffsetTilesY: 0.22,
            }
          : {
              markerText: "U",
              markerKind: "unit" as const,
            }),
        invalid: !placementPreview.valid,
        opacity: 0.62,
      });
    }

    return out;
  }, [boardIdByKey, hoverTile, openMonsterTile, openUnitTile, placementPreview.kind, placementPreview.valid, stoneAnchors, unitMarkerSpriteUrl]);

  const combinedEnvelope = useMemo(() => {
    const all = new Set<string>();
    stoneAnchors.forEach((anchor) => {
      movementEnvelope(anchor).forEach((k) => all.add(k));
    });
    return all;
  }, [stoneAnchors]);

  const extraOpenInEnvelope = useMemo(() => {
    const allowed = new Set<string>();
    if (openMonsterTile) allowed.add(keyOf(openMonsterTile.x, openMonsterTile.y));
    if (openUnitTile) allowed.add(keyOf(openUnitTile.x, openUnitTile.y));

    let count = 0;
    combinedEnvelope.forEach((k) => {
      if (allowed.has(k)) return;
      if (seaTiles.has(k)) return;
      if (occupiedByStones.has(k)) return;
      if (blockedFacilityTiles.has(k)) return;
      if (pieces.get(k) === "board") return;
      count += 1;
    });
    return count;
  }, [blockedFacilityTiles, combinedEnvelope, seaTiles, occupiedByStones, pieces, openMonsterTile, openUnitTile]);

  const leakTiles = useMemo(() => {
    const leaks = new Set<string>();
    const allowed = new Set<string>();
    if (openMonsterTile) allowed.add(keyOf(openMonsterTile.x, openMonsterTile.y));
    if (openUnitTile) allowed.add(keyOf(openUnitTile.x, openUnitTile.y));

    combinedEnvelope.forEach((k) => {
      if (allowed.has(k)) return;
      if (seaTiles.has(k)) return;
      if (occupiedByStones.has(k)) return;
      if (blockedFacilityTiles.has(k)) return;
      if (pieces.get(k) === "board") return;
      leaks.add(k);
    });
    return leaks;
  }, [blockedFacilityTiles, combinedEnvelope, seaTiles, occupiedByStones, pieces, openMonsterTile, openUnitTile]);

  const stoneCoverageChecks = useMemo(() => {
    const allowed = new Set<string>();
    if (openMonsterTile) allowed.add(keyOf(openMonsterTile.x, openMonsterTile.y));
    if (openUnitTile) allowed.add(keyOf(openUnitTile.x, openUnitTile.y));

    return stoneAnchors.map((anchor) => {
      const id = stoneIdByAnchorKey.get(keyOf(anchor.x, anchor.y)) ?? 0;
      const envelope = movementEnvelope(anchor);
      let leakCount = 0;
      envelope.forEach((k) => {
        if (allowed.has(k)) return;
        if (seaTiles.has(k)) return;
        if (occupiedByStones.has(k)) return;
        if (blockedFacilityTiles.has(k)) return;
        if (pieces.get(k) === "board") return;
        leakCount += 1;
      });
      return { id, anchor, leakCount, covered: leakCount === 0 };
    });
  }, [blockedFacilityTiles, stoneAnchors, stoneIdByAnchorKey, seaTiles, occupiedByStones, pieces, openMonsterTile, openUnitTile]);

  const runtimeCoverageOverlays = useMemo<RuntimeCellHighlightOverlay[]>(() => {
    if (!showCoverageCheck) return [];

    const overlays: RuntimeCellHighlightOverlay[] = [];

    combinedEnvelope.forEach((k) => {
      if (leakTiles.has(k)) return;
      if (seaTiles.has(k)) return;

      const p = parseKey(k);
      const isCovered = occupiedByStones.has(k) || pieces.get(k) === "board";
      const isReservedOpen =
        (openMonsterTile && openMonsterTile.x === p.x && openMonsterTile.y === p.y) ||
        (openUnitTile && openUnitTile.x === p.x && openUnitTile.y === p.y);
      if (!isCovered && !isReservedOpen) {
        overlays.push({
          x: p.x,
          y: p.y,
          fillColor: "rgba(252, 211, 77, 0.42)",
          strokeColor: "rgba(245, 158, 11, 0.72)",
          lineWidth: 1,
        });
      }
    });

    leakTiles.forEach((k) => {
      const p = parseKey(k);
      overlays.push({
        x: p.x,
        y: p.y,
        fillColor: "rgba(251, 113, 133, 0.74)",
        strokeColor: "rgba(239, 68, 68, 0.96)",
        lineWidth: 1.2,
      });
    });

    return overlays;
  }, [showCoverageCheck, combinedEnvelope, leakTiles, seaTiles, occupiedByStones, pieces, openMonsterTile, openUnitTile]);

  function clearAll() {
    setPieces(new Map());
    setPaintedSeaTiles(new Set());
    setReclaimedTiles(new Set());
    setOpenMonsterTile(null);
    setOpenUnitTile(null);
    setGeneratorError(null);
    setCopyStatus(null);
    setShowCoverageCheck(false);
    setLocationInput("");
    setResultCycle(null);
    setNextResultAnimating(false);
    setHoverTile(null);
  }

  function focusSetupView() {
    const focusPoints: Point[] = [];
    occupiedByStones.forEach((k) => focusPoints.push(parseKey(k)));
    pieces.forEach((piece, k) => {
      if (piece === "board") focusPoints.push(parseKey(k));
    });
    if (openMonsterTile) focusPoints.push(openMonsterTile);
    if (openUnitTile) focusPoints.push(openUnitTile);

    if (focusPoints.length === 0) {
      setCopyStatus("No setup tiles to focus yet.");
      return;
    }

    const xs = focusPoints.map((p) => p.x);
    const ys = focusPoints.map((p) => p.y);
    const minX = Math.min(...xs);
    const maxX = Math.max(...xs);
    const minY = Math.min(...ys);
    const maxY = Math.max(...ys);

    const centerX = (minX + maxX + 1) / 2;
    const centerY = (minY + maxY + 1) / 2;
    const boundsSpan = Math.max(maxX - minX + 1, maxY - minY + 1);
    const fitBaseZoom = boundsSpan <= 4 ? 9 : boundsSpan <= 8 ? 7.5 : boundsSpan <= 14 ? 6.5 : 5.5;
    const targetZoom = +Math.min(ZOOM_MAX, Math.max(fitBaseZoom, mapZoom + 1.2)).toFixed(2);
    setMapZoom(targetZoom);
    setRuntimeFocusCell({ x: centerX, y: centerY, token: Date.now() });

    setCopyStatus(`Focused on setup at ${Math.round(targetZoom * 100)}% zoom.`);
  }

  function trySelectLocationFromInput(input: string) {
    const level = Number.parseInt(input.trim(), 10);
    if (!Number.isFinite(level)) {
      setCopyStatus("Invalid level. Enter a level value (for example 135).");
      return;
    }
    const candidates = levelTilesByLevel.get(level) ?? [];
    if (candidates.length === 0) {
      setCopyStatus(`No map tiles found for Lv ${level}.`);
      return;
    }

    const focus = selectedSetupArea ?? { x: Math.floor(COLS / 2), y: Math.floor(ROWS / 2) };
    let best = candidates[0];
    let bestDist = Math.abs(best.x - focus.x) + Math.abs(best.y - focus.y);
    for (let i = 1; i < candidates.length; i += 1) {
      const c = candidates[i];
      const dist = Math.abs(c.x - focus.x) + Math.abs(c.y - focus.y);
      if (dist < bestDist) {
        best = c;
        bestDist = dist;
      }
    }

    setSelectedSetupArea(best);
    setPickSetupAreaFromMap(false);
    setLocationInput(String(level));
    setCopyStatus(`Picked level ${level}: using tile (${best.x},${best.y}).`);
  }

  function openLocationLevelDropdown() {
    const input = locationInputRef.current;
    if (input) input.focus();
    setShowLevelDropdown((prev) => !prev);
  }

  async function copyDiagnostics() {
    const points: Point[] = [];
    pieces.forEach((piece, k) => {
      if (piece !== "board") return;
      points.push(parseKey(k));
    });
    occupiedByStones.forEach((k) => points.push(parseKey(k)));
    if (openMonsterTile) points.push(openMonsterTile);
    if (openUnitTile) points.push(openUnitTile);

    const renderAsciiMap = () => {
      if (points.length === 0) return "(no placed tiles)";

      const xs = points.map((p) => p.x);
      const ys = points.map((p) => p.y);
      const minX = Math.max(0, Math.min(...xs));
      const maxX = Math.min(COLS - 1, Math.max(...xs));
      const minY = Math.max(0, Math.min(...ys));
      const maxY = Math.min(ROWS - 1, Math.max(...ys));

      const rows: string[] = [];
      for (let y = minY; y <= maxY; y += 1) {
        let row = "";
        for (let x = minX; x <= maxX; x += 1) {
          const k = keyOf(x, y);
          if (openMonsterTile && openMonsterTile.x === x && openMonsterTile.y === y) {
            row += "M";
          } else if (openUnitTile && openUnitTile.x === x && openUnitTile.y === y) {
            row += "U";
          } else if (spawnIdByKey.has(k)) {
            row += "S";
          } else if (blockedFacilityTiles.has(k)) {
            row += "F";
          } else if (occupiedByStones.has(k)) {
            row += "X";
          } else if (pieces.get(k) === "board") {
            row += "B";
          } else if (seaTiles.has(k)) {
            row += "~";
          } else {
            row += ".";
          }
        }
        rows.push(row);
      }

      return [
        `bbox: x=${minX}..${maxX} y=${minY}..${maxY}`,
        "legend: B=board S=spawn X=stone M=monster U=unit F=facility ~=sea .=open",
        ...rows,
      ].join("\n");
    };

    const lines: string[] = [];
    lines.push(`stones=${stoneAnchors.length} boards=${boardIdByKey.size} reclaimed=${reclaimedTiles.size}`);
    lines.push(`extraOpenInEnvelope=${extraOpenInEnvelope} mStandable=${monsterTileStandable} uStandable=${unitTileStandable} unitAdj=${unitAdjMonster}`);
    lines.push(`M=${openMonsterTile ? `(${openMonsterTile.x},${openMonsterTile.y})` : "-"} U=${openUnitTile ? `(${openUnitTile.x},${openUnitTile.y})` : "-"}`);
    stoneReachChecks.forEach(({ id, anchor, spawn, canReachM, distanceToM }) => {
      lines.push(`S${id}: anchor=(${anchor.x},${anchor.y}) spawn=(${spawn.x},${spawn.y}) reach=${canReachM} steps=${distanceToM ?? "-"}`);
    });
    lines.push(`profile: n=${stoneAnchors.length} targetMax=${distanceProfile.target.maxDistance} targetSpread=${distanceProfile.target.maxSpread}`);
    lines.push(`profile: max=${distanceProfile.maxDistance ?? "-"} min=${distanceProfile.minDistance ?? "-"} spread=${distanceProfile.spread ?? "-"}`);
    lines.push(`profile: allReach=${distanceProfile.allReach} maxPass=${distanceProfile.distancePass} spreadPass=${distanceProfile.spreadPass} overall=${distanceProfile.overallPass}`);
    lines.push("ascii-layout:");
    lines.push(renderAsciiMap());

    try {
      await navigator.clipboard.writeText(lines.join("\n"));
      setCopyStatus("Diagnostics copied. Paste them here.");
    } catch {
      setCopyStatus("Copy failed. You can manually copy the diagnostics panel text.");
    }
  }

  function isStoneAnchorValid(anchor: Point, mapSea: Set<string>, mapPieces: Map<string, Piece>, blockedTiles: Set<string>) {
    if (!inside(anchor.x, anchor.y) || !inside(anchor.x + 1, anchor.y + 1)) return false;
    const fp = stoneFootprint(anchor);
    const footprintKeys = new Set(fp.map((p) => keyOf(p.x, p.y)));

    // Prevent any 2x2 footprint intersection with already placed stones.
    for (const [k, piece] of mapPieces.entries()) {
      if (piece !== "stone") continue;
      const existingAnchor = parseKey(k);
      const existingFootprint = stoneFootprint(existingAnchor);
      if (existingFootprint.some((p) => footprintKeys.has(keyOf(p.x, p.y)))) {
        return false;
      }
    }

    for (const p of fp) {
      const k = keyOf(p.x, p.y);
      if (mapSea.has(k)) return false;
      if (blockedTiles.has(k)) return false;
      if (mapPieces.get(k) === "board") return false;
    }
    return true;
  }

  function removeStoneCovering(cellKey: string, next: Map<string, Piece>) {
    const target = parseKey(cellKey);
    const stoneKeys: string[] = [];
    next.forEach((piece, k) => {
      if (piece !== "stone") return;
      const fp = stoneFootprint(parseKey(k));
      if (fp.some((p) => p.x === target.x && p.y === target.y)) {
        stoneKeys.push(k);
      }
    });
    stoneKeys.forEach((k) => next.delete(k));
  }

  function onCellClick(x: number, y: number) {
    const k = keyOf(x, y);

    if (pickSetupAreaFromMap) {
      const lv = levelAt(x, y);
      if (lv != null) {
        setSelectedSetupArea({ x, y });
        setPickSetupAreaFromMap(false);
        setCopyStatus(`Picked location: (${x},${y}) Lv ${lv}.`);
      } else {
        setCopyStatus("Selected tile has no valid level data. Pick another tile.");
      }
      return;
    }

    if (tool === "reclaim") {
      const currentlyReclaimed = reclaimedTiles.has(k);
      const currentlySea = seaTiles.has(k);
      if (reclaimMode === "unclaim" && currentlyReclaimed) {
        // Unclaim: turn tile back into sea and clear pieces on that tile.
        setReclaimedTiles((prev) => {
          const next = new Set(prev);
          next.delete(k);
          return next;
        });
        setPaintedSeaTiles((prev) => {
          const next = new Set(prev);
          if (baseSeaTiles.has(k)) {
            next.delete(k);
          } else {
            next.add(k);
          }
          return next;
        });
        setPieces((prev) => {
          const next = new Map(prev);
          next.delete(k);
          removeStoneCovering(k, next);
          return next;
        });
      } else if (reclaimMode === "claim" && currentlySea) {
        // Claim: convert sea to usable land.
        setPaintedSeaTiles((prev) => {
          const next = new Set(prev);
          next.delete(k);
          return next;
        });
        setReclaimedTiles((prev) => {
          const next = new Set(prev);
          next.add(k);
          return next;
        });
      } else {
        setCopyStatus(
          reclaimMode === "claim"
            ? "Claim works only on sea tiles."
            : "Unclaim works only on reclaimed tiles."
        );
      }
      return;
    }

    if (tool === "erase") {
      if (openMonsterTile && openMonsterTile.x === x && openMonsterTile.y === y) {
        setOpenMonsterTile(null);
      }
      if (openUnitTile && openUnitTile.x === x && openUnitTile.y === y) {
        setOpenUnitTile(null);
      }
      setPieces((prev) => {
        const next = new Map(prev);
        next.delete(k);
        removeStoneCovering(k, next);
        return next;
      });
      return;
    }

    if (tool === "board") {
      if (seaTiles.has(k) || occupiedByStones.has(k) || blockedFacilityTiles.has(k)) return;
      setPieces((prev) => {
        const next = new Map(prev);
        next.set(k, "board");
        return next;
      });
      return;
    }

    if (tool === "stone") {
      const anchor = { x, y };
      setPieces((prev) => {
        const next = new Map(prev);
        if (isStoneAnchorValid(anchor, seaTiles, next, blockedFacilityTiles)) {
          next.set(k, "stone");
        }
        return next;
      });
      return;
    }

    if (tool === "monster" || tool === "unit") {
      if (seaTiles.has(k) || occupiedByStones.has(k) || blockedFacilityTiles.has(k)) {
        setCopyStatus("M/U markers can only be placed on open land tiles.");
        return;
      }

      setPieces((prev) => {
        const next = new Map(prev);
        next.delete(k);
        return next;
      });

      if (tool === "monster") {
        setOpenMonsterTile({ x, y });
        if (openUnitTile && openUnitTile.x === x && openUnitTile.y === y) {
          setOpenUnitTile(null);
        }
      } else {
        setOpenUnitTile({ x, y });
        if (openMonsterTile && openMonsterTile.x === x && openMonsterTile.y === y) {
          setOpenMonsterTile(null);
        }
      }
    }
  }

  function getCollapsedSeamIndex(segmentCount: number, mode: DemoSeamMode): number | null {
    const seamCount = segmentCount - 1;
    if (seamCount < 2) return null;
    if (mode === "first") return 0;
    if (mode === "last") return seamCount - 1;
    return Math.floor((seamCount - 1) / 2);
  }

  function buildGridAnchors(
    stoneCount: number,
    cols: number,
    stepX: number,
    stepY: number,
    compactAxis: "x" | "y",
    seamMode: DemoSeamMode,
    center: Point
  ) {
    const n = Math.max(1, Math.min(7, stoneCount));
    const cx = center.x;
    const cy = center.y;

    const rows = Math.ceil(n / cols);

    const collapsedRowSeam = compactAxis === "x" && stepY > 2 ? getCollapsedSeamIndex(rows, seamMode) : null;
    const collapsedColSeam = compactAxis === "y" && stepX > 2 ? getCollapsedSeamIndex(cols, seamMode) : null;

    const xGapReduction = collapsedColSeam != null ? 1 : 0;
    const yGapReduction = collapsedRowSeam != null ? 1 : 0;

    const footprintW = (cols - 1) * stepX + 2 - xGapReduction;
    const footprintH = (rows - 1) * stepY + 2 - yGapReduction;
    const startX = Math.floor(cx - footprintW / 2);
    const startY = Math.floor(cy - footprintH / 2);

    const rowYs: number[] = [];
    let curY = startY;
    for (let r = 0; r < rows; r += 1) {
      rowYs.push(curY);
      if (r < rows - 1) {
        const seamReduction = collapsedRowSeam === r ? 1 : 0;
        curY += stepY - seamReduction;
      }
    }

    const colXs: number[] = [];
    let curX = startX;
    for (let c = 0; c < cols; c += 1) {
      colXs.push(curX);
      if (c < cols - 1) {
        const seamReduction = collapsedColSeam === c ? 1 : 0;
        curX += stepX - seamReduction;
      }
    }

    const anchors: Point[] = [];
    for (let r = 0; r < rows; r += 1) {
      for (let c = 0; c < cols; c += 1) {
        if (anchors.length >= n) break;
        anchors.push({ x: colXs[c], y: rowYs[r] });
      }
    }
    return anchors;
  }

  function buildDemoAnchors(stoneCount: number, compactAxis: "x" | "y", seamMode: DemoSeamMode, center: Point) {
    const n = Math.max(1, Math.min(7, stoneCount));
    const maxCols = n;
    const candidates: Point[][] = [];
    const stepVariants: Array<{ stepX: number; stepY: number }> = [
      { stepX: 2, stepY: 3 },
      { stepX: 3, stepY: 2 },
      { stepX: 2, stepY: 2 },
      { stepX: 3, stepY: 3 },
    ];

      for (let cols = 1; cols <= maxCols; cols += 1) {
        for (const v of stepVariants) {
          candidates.push(buildGridAnchors(n, cols, v.stepX, v.stepY, compactAxis, seamMode, center));
        }
      }

    const unique = new Map<string, Point[]>();
    candidates.forEach((anchors) => {
      const key = anchors
        .map((a) => `${a.x},${a.y}`)
        .sort()
        .join("|");
      if (!unique.has(key)) unique.set(key, anchors);
    });

    return Array.from(unique.values());
  }

  function translateAnchors(anchors: Point[], dx: number, dy: number): Point[] {
    return anchors.map((a) => ({ x: a.x + dx, y: a.y + dy }));
  }

  function scoreAnchorLayout(
    anchors: Point[],
    baseSea: Set<string>,
    paintedSea: Set<string>,
    reclaimed: Set<string>,
    requiredSpawnLevel: number | null,
    focusPoint: Point | null
  ): AnchorScore | null {
    const n = anchors.length;
    if (n === 0) return null;

    const anchorPieces = new Map<string, Piece>();
    const reclaimForAnchors = new Set<string>();

    for (const a of anchors) {
      if (!inside(a.x, a.y) || !inside(a.x + 1, a.y + 1)) return null;
      const fp = stoneFootprint(a);
      for (const p of fp) {
        const k = keyOf(p.x, p.y);
        if (anchorPieces.get(k) === "board") return null;
        if (blockedFacilityTiles.has(k)) return null;
        if (isSeaAtKey(k, baseSea, paintedSea, reclaimed)) reclaimForAnchors.add(k);
      }
      anchorPieces.set(keyOf(a.x, a.y), "stone");
    }

    if (requiredSpawnLevel != null) {
      for (const a of anchors) {
        const spawn = eastFrontSpawn(a);
        const spawnLevel = levelAt(spawn.x, spawn.y);
        if (spawnLevel == null || spawnLevel !== requiredSpawnLevel) return null;
      }
    }

    const stoneCells = new Set<string>();
    anchors.forEach((a) => {
      stoneFootprint(a).forEach((p) => {
        if (inside(p.x, p.y)) stoneCells.add(keyOf(p.x, p.y));
      });
    });

    const stoneSources = anchors.map((anchor) => ({ anchor, spawn: eastFrontSpawn(anchor) }));

    let best: AnchorScore | null = null;

    for (let y = 0; y < ROWS; y += 1) {
      for (let x = 0; x < COLS; x += 1) {
        const k = keyOf(x, y);
        if (stoneCells.has(k)) continue;
        if (blockedFacilityTiles.has(k)) continue;

        const p = { x, y };
        const distances = stoneSources.map(({ anchor, spawn }) => cardinalStepsInRange(anchor, spawn, p));
        const numeric = distances.filter((d): d is number => d != null);
        if (numeric.length === 0) continue;

        const unreachable = n - numeric.length;
        const maxDistance = Math.max(...numeric);
        const minDistance = Math.min(...numeric);
        const spread = maxDistance - minDistance;
        const totalDistance = numeric.reduce((sum, d) => sum + d, 0);
        const fx = focusPoint?.x ?? Math.floor(COLS / 2);
        const fy = focusPoint?.y ?? Math.floor(ROWS / 2);
        const centerBias = Math.abs(x - fx) + Math.abs(y - fy);

        const mReclaim = isSeaAtKey(k, baseSea, paintedSea, reclaimed) && !reclaimForAnchors.has(k) ? 1 : 0;
        const candidate = { unreachable, spread, maxDistance, totalDistance, centerBias };
        const candidateWithReclaim = { reclaimCount: reclaimForAnchors.size + mReclaim, ...candidate };
        if (!best) {
          best = candidateWithReclaim;
          continue;
        }

        if (candidateWithReclaim.unreachable !== best.unreachable) {
          if (candidateWithReclaim.unreachable < best.unreachable) best = candidateWithReclaim;
          continue;
        }
        if (candidateWithReclaim.reclaimCount !== best.reclaimCount) {
          if (candidateWithReclaim.reclaimCount < best.reclaimCount) best = candidateWithReclaim;
          continue;
        }
        if (candidateWithReclaim.spread !== best.spread) {
          if (candidateWithReclaim.spread < best.spread) best = candidateWithReclaim;
          continue;
        }
        if (candidateWithReclaim.maxDistance !== best.maxDistance) {
          if (candidateWithReclaim.maxDistance < best.maxDistance) best = candidateWithReclaim;
          continue;
        }
        if (candidateWithReclaim.totalDistance !== best.totalDistance) {
          if (candidateWithReclaim.totalDistance < best.totalDistance) best = candidateWithReclaim;
          continue;
        }
        if (candidateWithReclaim.centerBias < best.centerBias) best = candidateWithReclaim;
      }
    }

    return best;
  }

  function applyAnchorsAsSetup(
    anchors: Point[],
    focus: Point,
    sourcePaintedSeaTiles: Set<string>,
    sourceReclaimedTiles: Set<string>,
    resultIndex: number,
    resultTotal: number
  ) {
    const nextPieces = new Map<string, Piece>();
    const nextPaintedSea = new Set<string>(sourcePaintedSeaTiles);
    const nextReclaimed = new Set<string>(sourceReclaimedTiles);

    anchors.forEach((a) => {
      stoneFootprint(a).forEach((p) => {
        const k = keyOf(p.x, p.y);
        if (isSeaAtKey(k, baseSeaTiles, nextPaintedSea, nextReclaimed)) nextReclaimed.add(k);
      });
    });

    anchors.forEach((a) => {
      const tempSea = new Set<string>();
      baseSeaTiles.forEach((k) => tempSea.add(k));
      nextPaintedSea.forEach((k) => tempSea.add(k));
      nextReclaimed.forEach((k) => tempSea.delete(k));
      if (isStoneAnchorValid(a, tempSea, nextPieces, blockedFacilityTiles)) {
        nextPieces.set(keyOf(a.x, a.y), "stone");
      }
    });

    const stoneSources = anchors.map((anchor) => ({ anchor, spawn: eastFrontSpawn(anchor) }));

    const isSeaTile = (k: string) => {
      if (nextReclaimed.has(k)) return false;
      return baseSeaTiles.has(k) || nextPaintedSea.has(k);
    };

    const isStandable = (p: Point, stoneCells: Set<string>) => {
      if (!inside(p.x, p.y)) return false;
      const k = keyOf(p.x, p.y);
      if (blockedFacilityTiles.has(k)) return false;
      return !stoneCells.has(k);
    };

    const stoneCells = new Set<string>();
    anchors.forEach((a) => {
      stoneFootprint(a).forEach((p) => {
        if (inside(p.x, p.y)) stoneCells.add(keyOf(p.x, p.y));
      });
    });

    // Pick M/U by minimizing travel imbalance first, then max travel.
    const target = getProfileTargets(anchors.length);
    let best: {
      monster: Point;
      unit: Point;
      unitDirectionPriority: number;
      unreachableCount: number;
      unitUnreachableCount: number;
      pairUnreachableCount: number;
      reclaimCount: number;
      pairViolationCount: number;
      pairSpread: number;
      pairMaxDistance: number;
      pairVariance: number;
      pairTotalDistance: number;
      spread: number;
      maxDistance: number;
      totalDistance: number;
      violationCount: number;
      sortedDesc: number[];
      variance: number;
      unitSpread: number;
      unitMaxDistance: number;
      unitTotalDistance: number;
      unitViolationCount: number;
      unitSortedDesc: number[];
      unitVariance: number;
      centerBias: number;
    } | null = null;

    function getDistanceProfile(p: Point, requireAllReach: boolean) {
      const distances = stoneSources.map(({ anchor, spawn }) => cardinalStepsInRange(anchor, spawn, p));
      const numericDistances = distances.filter((d): d is number => d != null);
      const unreachableCount = distances.length - numericDistances.length;
      if (requireAllReach && unreachableCount > 0) return null;
      if (numericDistances.length === 0) return null;
      const maxDistance = Math.max(...numericDistances);
      const minDistance = Math.min(...numericDistances);
      const spread = maxDistance - minDistance;
      const totalDistance = numericDistances.reduce((sum, d) => sum + d, 0);
      const meanDistance = totalDistance / numericDistances.length;
      const variance = numericDistances.reduce((sum, d) => {
        const diff = d - meanDistance;
        return sum + diff * diff;
      }, 0);
      const sortedDesc = [...numericDistances].sort((a, b) => b - a);
      const violationCount =
        unreachableCount +
        Number(maxDistance > target.maxDistance) +
        Number(spread > target.maxSpread);
      return {
        spread,
        maxDistance,
        totalDistance,
        violationCount,
        unreachableCount,
        sortedDesc,
        variance,
      };
    }

    function getUnitDirectionPriority(monster: Point, unit: Point) {
      if (unit.x === monster.x && unit.y === monster.y - 1) return 0;
      if (unit.x === monster.x - 1 && unit.y === monster.y) return 1;
      if (unit.x === monster.x + 1 && unit.y === monster.y) return 2;
      if (unit.x === monster.x && unit.y === monster.y + 1) return 3;
      return 9;
    }

    function isCandidateBetter(candidate: {
      unitDirectionPriority: number;
      unreachableCount: number;
      unitUnreachableCount: number;
      pairUnreachableCount: number;
      pairViolationCount: number;
      pairSpread: number;
      pairMaxDistance: number;
      pairVariance: number;
      pairTotalDistance: number;
      spread: number;
      maxDistance: number;
      totalDistance: number;
      violationCount: number;
      sortedDesc: number[];
      variance: number;
      unitSpread: number;
      unitMaxDistance: number;
      unitTotalDistance: number;
      unitViolationCount: number;
      unitSortedDesc: number[];
      unitVariance: number;
      centerBias: number;
    }, current: {
      unitDirectionPriority: number;
      unreachableCount: number;
      unitUnreachableCount: number;
      pairUnreachableCount: number;
      pairViolationCount: number;
      pairSpread: number;
      pairMaxDistance: number;
      pairVariance: number;
      pairTotalDistance: number;
      spread: number;
      maxDistance: number;
      totalDistance: number;
      violationCount: number;
      sortedDesc: number[];
      variance: number;
      unitSpread: number;
      unitMaxDistance: number;
      unitTotalDistance: number;
      unitViolationCount: number;
      unitSortedDesc: number[];
      unitVariance: number;
      centerBias: number;
    } | null) {
      if (!current) return true;
      if (candidate.unreachableCount !== current.unreachableCount) return candidate.unreachableCount < current.unreachableCount;
      if (candidate.violationCount !== current.violationCount) return candidate.violationCount < current.violationCount;
      if (candidate.spread !== current.spread) return candidate.spread < current.spread;
      if (candidate.maxDistance !== current.maxDistance) return candidate.maxDistance < current.maxDistance;
      if (candidate.variance !== current.variance) return candidate.variance < current.variance;
      const len = Math.min(candidate.sortedDesc.length, current.sortedDesc.length);
      for (let i = 0; i < len; i += 1) {
        if (candidate.sortedDesc[i] !== current.sortedDesc[i]) {
          return candidate.sortedDesc[i] < current.sortedDesc[i];
        }
      }
      if (candidate.totalDistance !== current.totalDistance) return candidate.totalDistance < current.totalDistance;
      if (candidate.unitUnreachableCount !== current.unitUnreachableCount) return candidate.unitUnreachableCount < current.unitUnreachableCount;
      if (candidate.pairUnreachableCount !== current.pairUnreachableCount) return candidate.pairUnreachableCount < current.pairUnreachableCount;
      if (candidate.unitDirectionPriority !== current.unitDirectionPriority) {
        return candidate.unitDirectionPriority < current.unitDirectionPriority;
      }
      if (candidate.unitTotalDistance !== current.unitTotalDistance) return candidate.unitTotalDistance < current.unitTotalDistance;
      if (candidate.unitMaxDistance !== current.unitMaxDistance) return candidate.unitMaxDistance < current.unitMaxDistance;
      if (candidate.unitSpread !== current.unitSpread) return candidate.unitSpread < current.unitSpread;
      return candidate.centerBias < current.centerBias;
    }

    const neighborDirs: Point[] = [
      { x: 1, y: 0 },
      { x: -1, y: 0 },
      { x: 0, y: 1 },
      { x: 0, y: -1 },
    ];

    for (let y = 0; y < ROWS; y += 1) {
      for (let x = 0; x < COLS; x += 1) {
        const monsterCandidate = { x, y };
        if (!isStandable(monsterCandidate, stoneCells)) continue;
        const monsterProfile = getDistanceProfile(monsterCandidate, true);
        if (!monsterProfile) continue;

        for (const dir of neighborDirs) {
          const unitCandidate = { x: x + dir.x, y: y + dir.y };
          if (!isStandable(unitCandidate, stoneCells)) continue;
          const unitProfile = getDistanceProfile(unitCandidate, false);
          if (!unitProfile) continue;

          const centerBias = Math.abs(x - focus.x) + Math.abs(y - focus.y);

          const candidate = {
            monster: monsterCandidate,
            unit: unitCandidate,
            unitDirectionPriority: getUnitDirectionPriority(monsterCandidate, unitCandidate),
            unreachableCount: monsterProfile.unreachableCount,
            unitUnreachableCount: unitProfile.unreachableCount,
            pairUnreachableCount: monsterProfile.unreachableCount + unitProfile.unreachableCount,
            reclaimCount:
              nextReclaimed.size +
              Number(isSeaTile(keyOf(monsterCandidate.x, monsterCandidate.y))) +
              Number(isSeaTile(keyOf(unitCandidate.x, unitCandidate.y))),
            pairViolationCount: monsterProfile.violationCount + unitProfile.violationCount,
            pairSpread: Math.max(monsterProfile.spread, unitProfile.spread),
            pairMaxDistance: Math.max(monsterProfile.maxDistance, unitProfile.maxDistance),
            pairVariance: Math.max(monsterProfile.variance, unitProfile.variance),
            pairTotalDistance: monsterProfile.totalDistance + unitProfile.totalDistance,
            spread: monsterProfile.spread,
            maxDistance: monsterProfile.maxDistance,
            totalDistance: monsterProfile.totalDistance,
            violationCount: monsterProfile.violationCount,
            sortedDesc: monsterProfile.sortedDesc,
            variance: monsterProfile.variance,
            unitSpread: unitProfile.spread,
            unitMaxDistance: unitProfile.maxDistance,
            unitTotalDistance: unitProfile.totalDistance,
            unitViolationCount: unitProfile.violationCount,
            unitSortedDesc: unitProfile.sortedDesc,
            unitVariance: unitProfile.variance,
            centerBias,
          };

          if (!best || candidate.reclaimCount < best.reclaimCount || (candidate.reclaimCount === best.reclaimCount && isCandidateBetter(candidate, best))) {
            best = {
              ...candidate,
            };
          }
        }
      }
    }

    if (!best) {
      setGeneratorError("Demo rejected: no valid M/U pair where all spawn tiles can reach M by range.");
      return false;
    }

    const monster = best.monster;
    const unit = best.unit;
    if (isSeaTile(keyOf(monster.x, monster.y))) nextReclaimed.add(keyOf(monster.x, monster.y));
    if (isSeaTile(keyOf(unit.x, unit.y))) nextReclaimed.add(keyOf(unit.x, unit.y));

    const union = new Set<string>();
    anchors.forEach((a) => {
      movementEnvelope(a).forEach((k) => union.add(k));
    });

    const open = new Set([keyOf(monster.x, monster.y), keyOf(unit.x, unit.y)]);

    const mKey = keyOf(monster.x, monster.y);
    const uKey = keyOf(unit.x, unit.y);
    const mStandable = !stoneCells.has(mKey) && !isSeaTile(mKey);
    const uStandable = !stoneCells.has(uKey) && !isSeaTile(uKey);
    const adjacent = Math.abs(monster.x - unit.x) + Math.abs(monster.y - unit.y) === 1;
    if (!mStandable || !uStandable || !adjacent) {
      const reasons: string[] = [];
      if (!mStandable) reasons.push("M is not standable");
      if (!uStandable) reasons.push("U is not standable");
      if (!adjacent) reasons.push("M and U are not adjacent");
      setGeneratorError(`Demo rejected: ${reasons.join("; ")}.`);
      return false;
    }

    union.forEach((k) => {
      if (open.has(k)) return;
      if (stoneCells.has(k)) return;
      if (baseSeaTiles.has(k) && !nextReclaimed.has(k)) return;
      if (nextPaintedSea.has(k) && !nextReclaimed.has(k)) return;
      nextPieces.set(k, "board");
    });

    setPieces(nextPieces);
    setPaintedSeaTiles(nextPaintedSea);
    setReclaimedTiles(nextReclaimed);
    setOpenMonsterTile(monster);
    setOpenUnitTile(unit);
    setGeneratorError(null);
    const resultText = resultTotal > 1 ? ` Result ${resultIndex + 1}/${resultTotal}.` : "";
    setCopyStatus(`Setup applied (${anchors.length} stones, reclaimed ${nextReclaimed.size} tiles).${resultText}`);
    return true;
  }

  function cycleToNextResult() {
    setNextResultAnimating(true);
    if (nextResultAnimationTimerRef.current != null) {
      window.clearTimeout(nextResultAnimationTimerRef.current);
    }
    nextResultAnimationTimerRef.current = window.setTimeout(() => {
      setNextResultAnimating(false);
      nextResultAnimationTimerRef.current = null;
    }, 140);

    if (!resultCycle || resultCycle.options.length < 2) {
      setCopyStatus("Only one best result is available for current settings.");
      return;
    }
    const nextIndex = (resultCycle.index + 1) % resultCycle.options.length;
    const ok = applyAnchorsAsSetup(
      resultCycle.options[nextIndex],
      resultCycle.focus,
      resultCycle.basePaintedSeaTiles,
      resultCycle.baseReclaimedTiles,
      nextIndex,
      resultCycle.options.length
    );
    if (!ok) return;
    setResultCycle((prev) => {
      if (!prev) return prev;
      return { ...prev, index: nextIndex };
    });
  }

  function applyStoneDemo(stoneCount: number, compactAxis: "x" | "y" = "x", seamMode: DemoSeamMode = "auto") {
    const focus = selectedSetupArea ?? { x: Math.floor(COLS / 2), y: Math.floor(ROWS / 2) };
    const requiredSpawnLevel = selectedSetupArea ? levelAt(selectedSetupArea.x, selectedSetupArea.y) ?? null : null;
    if (selectedSetupArea && requiredSpawnLevel == null) {
      setGeneratorError("Selected setup area has no valid level data. Pick another area tile.");
      return false;
    }
    const anchorCandidates = buildDemoAnchors(stoneCount, compactAxis, seamMode, focus);

    let bestAnchorScore: AnchorScore | null = null;
    const bestAnchorOptions = new Map<string, Point[]>();

    const evaluateAnchorCandidates = (candidateList: Point[][]) => {
      candidateList.forEach((candidateAnchors) => {
        const score = scoreAnchorLayout(
          candidateAnchors,
          baseSeaTiles,
          paintedSeaTiles,
          reclaimedTiles,
          requiredSpawnLevel,
          selectedSetupArea
        );
        if (!score) return;
        const candidateKey = candidateAnchors.map((a) => `${a.x},${a.y}`).sort().join("|");
        if (!bestAnchorScore) {
          bestAnchorScore = score;
          bestAnchorOptions.clear();
          bestAnchorOptions.set(candidateKey, candidateAnchors);
          return;
        }
        const cmp = compareAnchorScores(score, bestAnchorScore);
        if (cmp > 0) {
          return;
        }
        if (cmp < 0) {
          bestAnchorScore = score;
          bestAnchorOptions.clear();
          bestAnchorOptions.set(candidateKey, candidateAnchors);
          return;
        }
        if (!bestAnchorOptions.has(candidateKey)) bestAnchorOptions.set(candidateKey, candidateAnchors);
      });
    };

    evaluateAnchorCandidates(anchorCandidates);

    if (!bestAnchorScore) {
      const offsetRange = [-4, -2, 0, 2, 4];
      const expanded = new Map<string, Point[]>();
      anchorCandidates.forEach((candidate) => {
        const baseKey = candidate.map((a) => `${a.x},${a.y}`).sort().join("|");
        expanded.set(baseKey, candidate);
        offsetRange.forEach((dx) => {
          offsetRange.forEach((dy) => {
            if (dx === 0 && dy === 0) return;
            const shifted = translateAnchors(candidate, dx, dy);
            const shiftedKey = shifted.map((a) => `${a.x},${a.y}`).sort().join("|");
            if (!expanded.has(shiftedKey)) expanded.set(shiftedKey, shifted);
          });
        });
      });
      evaluateAnchorCandidates(Array.from(expanded.values()));
    }

    if (!bestAnchorScore) {
      setGeneratorError(
        requiredSpawnLevel != null
          ? `No valid ${stoneCount}-stone layout found where all S spawn tiles are Lv ${requiredSpawnLevel} in the selected area.`
          : `No valid ${stoneCount}-stone layout found for the selected area.`
      );
      setResultCycle(null);
      return false;
    }
    const equalBestAnchors = Array.from(bestAnchorOptions.values());
    const cycleState: ResultCycleState = {
      options: equalBestAnchors.map((candidate) => candidate.map((a) => ({ x: a.x, y: a.y }))),
      index: 0,
      focus: { ...focus },
      basePaintedSeaTiles: new Set<string>(paintedSeaTiles),
      baseReclaimedTiles: new Set<string>(reclaimedTiles),
    };

    const ok = applyAnchorsAsSetup(cycleState.options[0] ?? [], focus, cycleState.basePaintedSeaTiles, cycleState.baseReclaimedTiles, 0, cycleState.options.length);
    if (!ok) {
      setResultCycle(null);
      return false;
    }
    setResultCycle(cycleState);
    return true;
  }

  function tileStyle(x: number, y: number) {
    const k = keyOf(x, y);
    const terrain = terrainAt(x, y);
    const reclaimed = reclaimedTiles.has(k);
    if (seaTiles.has(k)) return "";
    if (!terrain && !reclaimed) return "bg-slate-950";
    if (showDebugLabels && spawnIdByKey.has(k)) return "bg-fuchsia-300";
    if (showCoverageCheck && leakTiles.has(k)) {
      return "bg-rose-400/80";
    }
    if (showCoverageCheck && combinedEnvelope.has(k)) {
      const isCovered = occupiedByStones.has(k) || pieces.get(k) === "board";
      const isReservedOpen =
        (openMonsterTile && openMonsterTile.x === x && openMonsterTile.y === y) ||
        (openUnitTile && openUnitTile.x === x && openUnitTile.y === y);
      if (!isCovered && !isReservedOpen && !seaTiles.has(k)) return "bg-amber-300/60";
    }
    if (openMonsterTile && openMonsterTile.x === x && openMonsterTile.y === y) return "bg-red-400";
    if (openUnitTile && openUnitTile.x === x && openUnitTile.y === y) return "bg-emerald-400";
    return "";
  }

  function tileInlineStyle(x: number, y: number): CSSProperties | undefined {
    const k = keyOf(x, y);
    const terrain = terrainAt(x, y);
    const reclaimed = reclaimedTiles.has(k);
    const selectedArea = selectedSetupArea && selectedSetupArea.x === x && selectedSetupArea.y === y;
    const leak = showCoverageCheck && leakTiles.has(k);
    const inPlacementPreview = placementPreview.cells.has(k);
    const nativeRows = NATIVE_MAP.length;
    const nativeCols = NATIVE_MAP[0]?.length ?? 0;
    const ny = getNativeIndex(y, ROWS, nativeRows);
    const nx = getNativeIndex(x, COLS, nativeCols);
    const nSX = Math.floor((nx * COLS) / nativeCols);
    const nEX = Math.floor(((nx + 1) * COLS) / nativeCols) - 1;
    const nSY = Math.floor((ny * ROWS) / nativeRows);
    const nEY = Math.floor(((ny + 1) * ROWS) / nativeRows) - 1;
    const overlayBorders = showLevelOverlay
      ? {
          right: x === nEX,
          left: x === nSX && nx === 0,
          bottom: y === nEY,
          top: y === nSY && ny === 0,
        }
      : null;

    const overlayShadow = [
      selectedArea ? "inset 0 0 0 1px #fde047" : null,
      leak ? "inset 0 0 0 2px rgba(239,68,68,0.95)" : null,
      inPlacementPreview
        ? placementPreview.valid
          ? "inset 0 0 0 2px rgba(148,163,184,0.55)"
          : "inset 0 0 0 2px rgba(239,68,68,0.8)"
        : null,
    ].filter(Boolean).join(", ") || undefined;

    if (seaTiles.has(k)) {
      return {
        backgroundColor: WORLD_MAP_WATER_COLOR,
        borderRight: overlayBorders?.right ? "1px solid rgba(0,0,0,0.62)" : undefined,
        borderLeft: overlayBorders?.left ? "1px solid rgba(0,0,0,0.62)" : undefined,
        borderBottom: overlayBorders?.bottom ? "1px solid rgba(0,0,0,0.62)" : undefined,
        borderTop: overlayBorders?.top ? "1px solid rgba(0,0,0,0.62)" : undefined,
        boxShadow: overlayShadow,
      };
    }

    if (!terrain && !reclaimed) return undefined;
    if (showDebugLabels && spawnIdByKey.has(k)) return undefined;
    if (openMonsterTile && openMonsterTile.x === x && openMonsterTile.y === y) return undefined;
    if (openUnitTile && openUnitTile.x === x && openUnitTile.y === y) return undefined;

    if (reclaimed) {
      const reclaimedBase = terrain ? TERRAIN_COLORS[terrain] : "#6f8f3a";
      const reclaimedPattern =
        "repeating-linear-gradient(135deg, rgba(255,255,255,0.16) 0 2px, rgba(0,0,0,0.10) 2px 4px)";
      return {
        backgroundColor: reclaimedBase,
        backgroundImage: reclaimedPattern,
        borderRight: overlayBorders?.right ? "1px solid rgba(0,0,0,0.62)" : undefined,
        borderLeft: overlayBorders?.left ? "1px solid rgba(0,0,0,0.62)" : undefined,
        borderBottom: overlayBorders?.bottom ? "1px solid rgba(0,0,0,0.62)" : undefined,
        borderTop: overlayBorders?.top ? "1px solid rgba(0,0,0,0.62)" : undefined,
        boxShadow: overlayShadow,
      };
    }

    if (!terrain) return undefined;
    return {
      backgroundColor: TERRAIN_COLORS[terrain],
      borderRight: overlayBorders?.right ? "1px solid rgba(0,0,0,0.62)" : undefined,
      borderLeft: overlayBorders?.left ? "1px solid rgba(0,0,0,0.62)" : undefined,
      borderBottom: overlayBorders?.bottom ? "1px solid rgba(0,0,0,0.62)" : undefined,
      borderTop: overlayBorders?.top ? "1px solid rgba(0,0,0,0.62)" : undefined,
      boxShadow: overlayShadow,
    };
  }

  function tileLabel(x: number, y: number) {
    const k = keyOf(x, y);
    if (openMonsterTile && openMonsterTile.x === x && openMonsterTile.y === y) return "M";
    if (openUnitTile && openUnitTile.x === x && openUnitTile.y === y) return "U";

    const spawnId = spawnIdByKey.get(k);
    if (spawnId != null) return `S${spawnId}`;

    const stoneId = stoneIdByAnchorKey.get(k);
    if (stoneId != null) return `${stoneId}`;

    const boardId = boardIdByKey.get(k);
    if (boardId != null) return `B${boardId}`;

    return "";
  }

  function tileHoverText(x: number, y: number) {
    const k = keyOf(x, y);
    const lv = levelAt(x, y);
    const terrain = terrainAt(x, y);
    const sea = seaTiles.has(k);
    const reclaimed = reclaimedTiles.has(k);
    const state = reclaimed ? "Reclaimed land" : sea ? "Sea" : "Land";
    const terrainLabel = terrain ?? (reclaimed ? "reclaimed" : "water");
    return `Lv ${lv ?? "-"} | ${terrainLabel} | ${state}`;
  }

  function tileDecor(x: number, y: number) {
    const k = keyOf(x, y);

    if (openMonsterTile && openMonsterTile.x === x && openMonsterTile.y === y) {
      return (
        <span className="pointer-events-none absolute inset-0 z-[4] flex items-center justify-center">
          <span className="flex h-[88%] w-[88%] items-center justify-center rounded-full border border-red-900/40 bg-red-500/90 text-slate-100">
            <Skull className="h-[72%] w-[72%]" strokeWidth={2.2} />
          </span>
        </span>
      );
    }

    if (openUnitTile && openUnitTile.x === x && openUnitTile.y === y) {
      return (
        <span className="pointer-events-none absolute inset-0 z-[4] flex items-center justify-center">
          <span className="flex h-[88%] w-[88%] items-center justify-center rounded-full border border-emerald-900/40 bg-emerald-500/90 text-slate-100">
            <Shield className="h-[72%] w-[72%]" strokeWidth={2.2} />
          </span>
        </span>
      );
    }

    if (placementPreview.kind === "monster" && hoverTile && x === hoverTile.x && y === hoverTile.y) {
      const outerClass = placementPreview.valid
        ? "border-red-900/40 bg-red-500/80"
        : "border-red-900/55 bg-red-900/45";
      return (
        <span className="pointer-events-none absolute inset-0 z-[4] flex items-center justify-center opacity-60">
          <span className={`flex h-[88%] w-[88%] items-center justify-center rounded-full border ${outerClass} text-slate-100`}>
            <Skull className="h-[72%] w-[72%]" strokeWidth={2.2} />
          </span>
        </span>
      );
    }

    if (placementPreview.kind === "unit" && hoverTile && x === hoverTile.x && y === hoverTile.y) {
      const outerClass = placementPreview.valid
        ? "border-emerald-900/40 bg-emerald-500/80"
        : "border-red-900/55 bg-red-900/45";
      return (
        <span className="pointer-events-none absolute inset-0 z-[4] flex items-center justify-center opacity-60">
          <span className={`flex h-[88%] w-[88%] items-center justify-center rounded-full border ${outerClass} text-slate-100`}>
            <Shield className="h-[72%] w-[72%]" strokeWidth={2.2} />
          </span>
        </span>
      );
    }

    if (pieces.get(k) === "board") {
      return (
        <span className="pointer-events-none absolute inset-0 z-[4]">
          <img
            src={SIGNBOARD_SPRITE}
            alt=""
            className="h-full w-full object-contain [image-rendering:pixelated]"
            draggable={false}
          />
        </span>
      );
    }

    if (stoneIdByAnchorKey.has(k)) {
      return (
        <span
          className="pointer-events-none absolute left-0 top-0 z-[3]"
          style={{ width: "calc(var(--tile-size) * 2)", height: "calc(var(--tile-size) * 2)" }}
        >
          <img
            src={CHAOS_STONE_SPRITE}
            alt=""
            className="h-full w-full object-contain [image-rendering:pixelated]"
            draggable={false}
          />
        </span>
      );
    }

    if (placementPreview.kind === "board" && placementPreview.cells.has(k)) {
      return (
        <span className="pointer-events-none absolute inset-0 z-[4] opacity-65">
          <img
            src={SIGNBOARD_SPRITE}
            alt=""
            className="h-full w-full object-contain [image-rendering:pixelated]"
            style={placementPreview.valid ? undefined : { filter: "grayscale(1) brightness(0.55) sepia(0.8) hue-rotate(-35deg)" }}
            draggable={false}
          />
        </span>
      );
    }

    if (placementPreview.kind === "stone" && hoverTile && x === hoverTile.x && y === hoverTile.y) {
      return (
        <span
          className="pointer-events-none absolute left-0 top-0 z-[3] opacity-55"
          style={{ width: "calc(var(--tile-size) * 2)", height: "calc(var(--tile-size) * 2)" }}
        >
          <img
            src={CHAOS_STONE_SPRITE}
            alt=""
            className="h-full w-full object-contain [image-rendering:pixelated]"
            style={placementPreview.valid ? undefined : { filter: "grayscale(1) brightness(0.55) sepia(0.8) hue-rotate(-35deg)" }}
            draggable={false}
          />
        </span>
      );
    }

    return null;
  }

  const unitAdjMonster =
    openMonsterTile && openUnitTile
      ? Math.abs(openMonsterTile.x - openUnitTile.x) + Math.abs(openMonsterTile.y - openUnitTile.y) === 1
      : false;

  const monsterTileStandable = openMonsterTile
    ? !seaTiles.has(keyOf(openMonsterTile.x, openMonsterTile.y)) && !occupiedByStones.has(keyOf(openMonsterTile.x, openMonsterTile.y)) && !blockedFacilityTiles.has(keyOf(openMonsterTile.x, openMonsterTile.y))
    : false;

  const unitTileStandable = openUnitTile
    ? !seaTiles.has(keyOf(openUnitTile.x, openUnitTile.y)) && !occupiedByStones.has(keyOf(openUnitTile.x, openUnitTile.y)) && !blockedFacilityTiles.has(keyOf(openUnitTile.x, openUnitTile.y))
    : false;
  const zoomPercent = Math.round(mapZoom * 100);
  const handleZoomChange = (values: number[]) => {
    const next = values[0];
    if (typeof next !== "number" || Number.isNaN(next)) return;
    setMapZoom(clampZoom(next));
  };

  return (
    <div className="mx-auto w-full max-w-[1400px] space-y-4 p-4">
      <Card>
        <CardHeader>
          <CardTitle>Chaos Setup Lab</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm text-muted-foreground">
          <p>
            Lightweight sandbox for chaos stones + sign boards on the real map mask and terrain colors. Stone/board now use mapped facility sprites from the confirmed icon set.
          </p>
          <p className="font-medium text-amber-600 dark:text-amber-400">
            Experimental tool: generated setups may not always match live game behavior. Please verify generated setups with the community on Discord before relying on them.
          </p>
          <p className="rounded-md border border-red-500/70 bg-red-500/10 px-3 py-2 font-semibold text-red-700 dark:text-red-300">
            Warning: when removing a Chaos Stone, pay the 50 diamonds or it will be lost permanently. Losing one permanently is bad enough that many players would rather restart than accept it.
          </p>
          <p>
            Spawn/movement model used in this lab: spawn from the stone's south-east tile (inside 2x2), range is measured from stone borders (West +2 from west edge, East +4 from east edge, North +3 from north edge, South +3 from south edge), with N/E/S/W cardinal movement only.
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardContent className="space-y-2 p-3">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs font-medium text-muted-foreground">Reclaim Land:</span>
            <Button
              size="sm"
              variant={tool === "reclaim" && reclaimMode === "claim" ? "default" : "outline"}
              onClick={() => {
                setTool((prev) => (prev === "reclaim" && reclaimMode === "claim") ? "none" : "reclaim");
                setReclaimMode("claim");
              }}
            >
              Claim
            </Button>
            <Button
              size="sm"
              variant={tool === "reclaim" && reclaimMode === "unclaim" ? "default" : "outline"}
              onClick={() => {
                setTool((prev) => (prev === "reclaim" && reclaimMode === "unclaim") ? "none" : "reclaim");
                setReclaimMode("unclaim");
              }}
            >
              Unclaim
            </Button>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs font-medium text-muted-foreground">Map View:</span>
            <Button size="sm" variant={showDebugLabels ? "default" : "outline"} onClick={() => setShowDebugLabels((prev) => !prev)}>
              {showDebugLabels ? "Hide Labels" : "Show Labels"}
            </Button>
            <Button size="sm" variant={showLevelOverlay ? "default" : "outline"} onClick={() => setShowLevelOverlay((prev) => !prev)}>
              {showLevelOverlay ? "Hide Levels" : "Level Overlay"}
            </Button>
          </div>
          <div className="text-xs text-muted-foreground">
            Level overlay draws native area borders and shows each area's Lv label in the center.
          </div>

          <div className="space-y-2 rounded-md border border-border/60 p-2">
            <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Manual Tool</div>
            <div className="text-xs text-muted-foreground">
              Use the floating bubble toolbar on the right side of the map for Stone, Board, Monster, Unit, Erase, and Coverage.
            </div>
            <div className="text-xs text-muted-foreground">
              Active tool: <span className="font-semibold text-foreground">{tool === "none" ? "none" : tool}</span>
            </div>
            <div className="text-xs text-muted-foreground">
              Coverage check: red tiles are leak points. Use boards to cover all non-sea envelope tiles.
            </div>
            {showCoverageCheck && stoneCoverageChecks.length > 0 && (
              <div className="grid grid-cols-1 gap-1 text-xs text-muted-foreground md:grid-cols-2 lg:grid-cols-4">
                {stoneCoverageChecks.map((check) => (
                  <div key={`coverage-${check.id}`} className={check.covered ? "text-emerald-600 dark:text-emerald-400" : "text-red-600 dark:text-red-400"}>
                    S{check.id}: {check.covered ? "covered" : `${check.leakCount} leak tile${check.leakCount > 1 ? "s" : ""}`}
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="space-y-2 rounded-md border border-border/60 p-2">
            <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Auto Planner</div>
            <div className="flex flex-wrap items-center gap-2">
              <Button size="sm" variant={demoAxis === "x" ? "default" : "outline"} onClick={() => setDemoAxis("x")}>Axis X</Button>
              <Button size="sm" variant={demoAxis === "y" ? "default" : "outline"} onClick={() => setDemoAxis("y")}>Axis Y</Button>
              <select
                value={demoSeamMode}
                onChange={(event) => setDemoSeamMode(event.target.value as DemoSeamMode)}
                className="h-8 rounded-md border border-input bg-background px-2 text-sm"
                aria-label="Demo seam mode"
              >
                <option value="auto">Seam: Auto</option>
                <option value="first">Seam: First</option>
                <option value="last">Seam: Last</option>
              </select>
              <select
                value={demoStoneCount}
                onChange={(event) => {
                  const next = Number.parseInt(event.target.value, 10);
                  setDemoStoneCount(Number.isFinite(next) ? next : 1);
                }}
                className="h-8 rounded-md border border-input bg-background px-2 text-sm"
                aria-label="Demo stone count"
              >
                {[1, 2, 3, 4, 5, 6, 7].map((n) => (
                  <option key={`demo-count-${n}`} value={n}>{n} stone{n > 1 ? "s" : ""}</option>
                ))}
              </select>
              <span className="text-xs font-medium text-muted-foreground">Pick Location:</span>
              <div ref={levelPickerRef} className="relative">
                <div className="flex h-8 w-[200px] items-center rounded-md border border-input bg-background">
                  <input
                    ref={locationInputRef}
                    value={locationInput}
                    onChange={(event) => {
                      setLocationInput(event.target.value);
                      setShowLevelDropdown(true);
                    }}
                    onFocus={() => setShowLevelDropdown(true)}
                    onBlur={() => {
                      if (locationInput.trim()) trySelectLocationFromInput(locationInput);
                    }}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") {
                        event.preventDefault();
                        setShowLevelDropdown(false);
                        trySelectLocationFromInput(locationInput);
                      }
                      if (event.key === "Escape") {
                        setShowLevelDropdown(false);
                      }
                    }}
                    placeholder="Level (e.g. 135)"
                    className="h-full w-full bg-transparent px-2 text-sm outline-none"
                    aria-label="Pick location level"
                  />
                  <button
                    type="button"
                    aria-label="Open level suggestions"
                    onClick={openLocationLevelDropdown}
                    className="flex h-full w-8 items-center justify-center border-l border-input text-muted-foreground hover:text-foreground"
                  >
                    <ChevronDown className="h-4 w-4" />
                  </button>
                </div>
                {showLevelDropdown && (
                  <div className="absolute left-0 top-9 z-30 max-h-52 w-[200px] overflow-auto rounded-md border border-border bg-popover p-1 shadow-md">
                    {levelOptions
                      .filter((lv) => locationInput.trim() === "" || String(lv).includes(locationInput.trim()))
                      .slice(0, 80)
                      .map((lv) => (
                        <button
                          key={`level-option-${lv}`}
                          type="button"
                          className="block w-full rounded px-2 py-1 text-left text-sm text-popover-foreground hover:bg-accent hover:text-accent-foreground"
                          onMouseDown={(event) => event.preventDefault()}
                          onClick={() => {
                            setLocationInput(String(lv));
                            setShowLevelDropdown(false);
                            trySelectLocationFromInput(String(lv));
                          }}
                        >
                          Lv {lv}
                        </button>
                      ))}
                  </div>
                )}
              </div>
              <Button
                size="sm"
                variant={pickSetupAreaFromMap ? "default" : "outline"}
                onClick={() => setPickSetupAreaFromMap((prev) => !prev)}
              >
                {pickSetupAreaFromMap ? "Picking From Map..." : "Pick From Map"}
              </Button>
              <Button
                size="sm"
                variant="default"
                className="min-w-[110px]"
                title={selectedSetupArea ? "Generate best setup for selected location" : "Pick a location first, then apply setup"}
                onClick={() => {
                  if (!selectedSetupArea) {
                    setCopyStatus("Pick a location first, then click Apply Setup.");
                    return;
                  }
                  setCopyStatus("Applying setup...");
                  const ok = applyStoneDemo(demoStoneCount, demoAxis, demoSeamMode);
                  if (!ok) {
                    setCopyStatus("Setup could not be applied. Check the message above.");
                  }
                }}
              >
                Apply Setup
              </Button>
              <Button
                size="sm"
                variant="outline"
                className={`min-w-[102px] transition-transform duration-150 ${nextResultAnimating ? "scale-95" : "scale-100"}`}
                disabled={!resultCycle || resultCycle.options.length < 2}
                onClick={cycleToNextResult}
              >
                Next Result
              </Button>
              <Button size="sm" variant="outline" onClick={() => setSelectedSetupArea(null)}>Clear Area</Button>
            </div>
            {resultCycle && resultCycle.options.length > 1 && (
              <div className="text-xs text-muted-foreground">
                Showing result {resultCycle.index + 1} of {resultCycle.options.length}. Click Next Result to rotate.
              </div>
            )}
            <div className="text-xs text-muted-foreground">
              Pick Location uses one control: type a level or use the arrow dropdown, then Pick From Map is the alternate method.
            </div>
            <div className="text-xs text-muted-foreground">
              After changing Axis, Seam, or stone count, click Apply Setup to regenerate.
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <Button size="sm" variant="outline" onClick={focusSetupView}>Focus Setup</Button>
            <div className="flex min-w-[220px] items-center gap-2 rounded-md border border-border/70 px-2 py-1">
              <span className="text-xs font-medium text-muted-foreground">Zoom</span>
              <Slider
                min={ZOOM_MIN}
                max={ZOOM_MAX}
                step={ZOOM_STEP}
                value={[mapZoom]}
                onValueChange={handleZoomChange}
                aria-label="Map zoom"
                className="w-[160px]"
              />
              <Button size="sm" variant="outline" onClick={() => setMapZoom(1)}>{zoomPercent}%</Button>
            </div>
            <Button size="sm" variant="outline" onClick={copyDiagnostics}>Copy Diagnostics</Button>
            <Button size="sm" variant="outline" onClick={clearAll}>Clear</Button>
          </div>

          {generatorError && <div className="w-full text-xs text-red-600 dark:text-red-400">{generatorError}</div>}
          {copyStatus && <div className="w-full text-xs text-muted-foreground">{copyStatus}</div>}
          {!selectedSetupArea && (
            <div className="w-full text-xs text-amber-600 dark:text-amber-400">
              Pick From Map to enable Apply Setup.
            </div>
          )}
        </CardContent>
      </Card>

      <Card className="relative overflow-hidden">
        <CardContent className="p-2">
          <RuntimeWorldRenderTestPage
            publicMode
            controlledZoom={runtimeMapZoom}
            showCellGrid
            visibleOnePieceFacilityIds={[LEGENDARY_CAVE_FACILITY_ID]}
            landOverrideCells={runtimeLandOverrideCells}
            reclaimedTintCells={runtimeReclaimedTintCells}
            focusCell={runtimeFocusCell}
            interactiveInPublicMode
            hideNatureToggleButtons
            initialNatureVisibility={{
              terrain: false,
              resources: false,
              humans: false,
              special: false,
            }}
            externalOverlays={runtimeOverlays}
            cellHighlightOverlays={runtimeCoverageOverlays}
            onCellHover={(cell) => setHoverTile(cell)}
            onCellClick={(cell) => onCellClick(cell.x, cell.y)}
          />
        </CardContent>
        <div className="pointer-events-none absolute right-3 top-1/2 z-20 -translate-y-1/2">
          <div className="pointer-events-auto rounded-full border border-cyan-300/35 bg-slate-950/75 p-2 backdrop-blur-sm">
            <div className="flex flex-col items-center gap-2">
              <button
                type="button"
                aria-label="Place stone"
                title="Stone"
                onClick={() => setTool((prev) => prev === "stone" ? "none" : "stone")}
                className={`${TOOL_BUTTON_BASE_CLASS} ${tool === "stone" ? TOOL_BUTTON_ACTIVE_CLASS : TOOL_BUTTON_IDLE_CLASS}`}
              >
                <img src={CHAOS_STONE_SPRITE} alt="" className="h-6 w-6 object-contain [image-rendering:pixelated]" draggable={false} />
              </button>

              <button
                type="button"
                aria-label="Place board"
                title="Board"
                onClick={() => setTool((prev) => prev === "board" ? "none" : "board")}
                className={`${TOOL_BUTTON_BASE_CLASS} ${tool === "board" ? TOOL_BUTTON_ACTIVE_CLASS : TOOL_BUTTON_IDLE_CLASS}`}
              >
                <img src={SIGNBOARD_SPRITE} alt="" className="h-6 w-6 object-contain [image-rendering:pixelated]" draggable={false} />
              </button>

              <button
                type="button"
                aria-label="Place monster"
                title="Monster"
                onClick={() => setTool((prev) => prev === "monster" ? "none" : "monster")}
                className={`${TOOL_BUTTON_BASE_CLASS} ${tool === "monster" ? TOOL_BUTTON_ACTIVE_CLASS : TOOL_BUTTON_IDLE_CLASS}`}
              >
                <Skull className="h-5 w-5" strokeWidth={2.1} />
              </button>

              <button
                type="button"
                aria-label="Place unit"
                title="Unit (A Rank Archer)"
                onClick={() => setTool((prev) => prev === "unit" ? "none" : "unit")}
                className={`${TOOL_BUTTON_BASE_CLASS} ${tool === "unit" ? TOOL_BUTTON_ACTIVE_CLASS : TOOL_BUTTON_IDLE_CLASS}`}
              >
                {unitMarkerSpriteUrl ? (
                  <img src={unitMarkerSpriteUrl} alt="" className="h-7 w-7 object-contain [image-rendering:pixelated]" draggable={false} />
                ) : (
                  <Shield className="h-5 w-5" strokeWidth={2.1} />
                )}
              </button>

              <button
                type="button"
                aria-label="Erase piece or marker"
                title="Erase"
                onClick={() => setTool((prev) => prev === "erase" ? "none" : "erase")}
                className={`${TOOL_BUTTON_BASE_CLASS} ${tool === "erase" ? TOOL_BUTTON_ACTIVE_CLASS : TOOL_BUTTON_IDLE_CLASS}`}
              >
                <Eraser className="h-5 w-5" strokeWidth={2.1} />
              </button>

              <button
                type="button"
                aria-label="Toggle coverage check"
                title="Coverage Check"
                onClick={() => setShowCoverageCheck((prev) => !prev)}
                className={`${TOOL_BUTTON_BASE_CLASS} ${showCoverageCheck ? TOOL_BUTTON_ACTIVE_CLASS : TOOL_BUTTON_IDLE_CLASS}`}
              >
                <Check className="h-5 w-5" strokeWidth={2.1} />
              </button>
            </div>
          </div>
        </div>
      </Card>

      <Card className="relative hidden">
        <CardContent ref={mapViewportRef} className="max-h-[72vh] overflow-auto p-2">
          <div
            ref={mapGridRef}
            className="mx-auto grid w-max overflow-visible rounded-md"
            style={{
              gridTemplateColumns: `repeat(${COLS}, var(--tile-size))`,
              ["--tile-size" as string]: `calc(clamp(2px, min(calc((100vw - 48px) / ${COLS}), calc((100vh - 360px) / ${ROWS})), ${TILE_PX_MAX}px) * ${mapZoom})`,
            }}
          >
            {Array.from({ length: ROWS }).map((_, y) =>
              Array.from({ length: COLS }).map((__, x) => {
                const k = keyOf(x, y);
                const levelLabel = showLevelOverlay ? nativeLevelLabelByKey.get(k) : undefined;
                const shouldShowLevelLabel =
                  levelLabel != null &&
                  !showDebugLabels;
                return (
                  <button
                    key={k}
                    data-map-tile={k}
                    type="button"
                    onMouseEnter={() => setHoverTile({ x, y })}
                    onMouseLeave={() => setHoverTile(null)}
                    onClick={() => onCellClick(x, y)}
                    title={tileHoverText(x, y)}
                    style={{ width: "var(--tile-size)", height: "var(--tile-size)", ...tileInlineStyle(x, y) }}
                    className={`relative overflow-visible text-[8px] leading-none ${tileStyle(x, y)}`}
                  >
                    {tileDecor(x, y)}
                    {shouldShowLevelLabel && (
                      <span className="pointer-events-none absolute inset-0 z-[5] flex items-center justify-center font-semibold text-slate-50 [text-shadow:0_1px_2px_rgba(0,0,0,0.96)]">
                        <span
                          className="leading-none"
                          style={{ fontSize: `${Math.max(8, Math.min(16, Math.round(mapZoom * 2)))}px` }}
                        >
                          Lv {levelLabel}
                        </span>
                      </span>
                    )}
                    {showDebugLabels && (
                      <span className="pointer-events-none relative z-10 text-[8px] leading-none text-slate-50 [text-shadow:0_1px_1px_rgba(0,0,0,0.85)]">
                        {tileLabel(x, y)}
                      </span>
                    )}
                  </button>
                );
              })
            )}
          </div>
        </CardContent>
        <div className="pointer-events-none absolute right-2 top-1/2 z-20 -translate-y-1/2">
          <div className="pointer-events-auto flex items-center">
            <Slider
              min={ZOOM_MIN}
              max={ZOOM_MAX}
              step={ZOOM_STEP}
              value={[mapZoom]}
              onValueChange={handleZoomChange}
              orientation="vertical"
              aria-label="Map zoom slider on map"
              className="h-72 w-6 flex-col"
              trackClassName="mx-auto h-full w-[3px] rounded-full bg-primary/20"
              thumbClassName="h-5 w-5 border-primary/70 bg-background shadow"
            />
          </div>
        </div>
      </Card>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">Diagnostics</CardTitle>
        </CardHeader>
        <CardContent className="max-h-[34vh] space-y-3 overflow-auto p-3 text-xs text-muted-foreground">
          <div className="grid grid-cols-1 gap-1 md:grid-cols-4">
            <div>Stones: <span className="font-semibold text-foreground">{stoneAnchors.length}</span></div>
            <div>Boards: <span className="font-semibold text-foreground">{boardIdByKey.size}</span></div>
            <div>
              Stone levels: <span className="font-semibold text-foreground">
                {stoneLevelSummary.min == null ? "-" : `${stoneLevelSummary.min}-${stoneLevelSummary.max} (avg ${stoneLevelSummary.avg})`}
              </span>
            </div>
            <div>
              Setup area: <span className="font-semibold text-foreground">
                {selectedSetupArea ? `(${selectedSetupArea.x},${selectedSetupArea.y}) Lv ${levelAt(selectedSetupArea.x, selectedSetupArea.y) ?? "-"}` : "auto-center"}
              </span>
            </div>
            <div>Open tiles in envelope (should be 0 extra): <span className="font-semibold text-foreground">{extraOpenInEnvelope}</span></div>
            <div>Unit adjacent to sink: <span className="font-semibold text-foreground">{unitAdjMonster ? "Yes" : "No"}</span></div>
            <div>Reclaimed tiles: <span className="font-semibold text-foreground">{reclaimedTiles.size}</span></div>
            <div>M tile standable: <span className="font-semibold text-foreground">{monsterTileStandable ? "Yes" : "No"}</span></div>
            <div>U tile standable: <span className="font-semibold text-foreground">{unitTileStandable ? "Yes" : "No"}</span></div>
          </div>
          {stoneAnchors.length > 0 && (
            <div className="space-y-1 pt-1">
              <div className="font-medium text-foreground">Stone Anchors And Spawn Tiles</div>
              <div className="grid grid-cols-1 gap-1 md:grid-cols-2 lg:grid-cols-4">
                {stoneReachChecks.map(({ id, anchor, spawn, canReachM, distanceToM }) => {
                  return (
                    <div key={keyOf(anchor.x, anchor.y)}>
                      Stone {id}: anchor ({anchor.x},{anchor.y}) spawn S{id} ({spawn.x},{spawn.y}) can reach M by range: {canReachM ? "Yes" : "No"} steps to M: {distanceToM ?? "-"}
                    </div>
                  );
                })}
              </div>
            </div>
          )}
          <div className="space-y-1 pt-1">
            <div className="font-medium text-foreground">Stone-Count Profile Check (Trial)</div>
            <div>
              N={stoneAnchors.length} targets: max distance at most {distanceProfile.target.maxDistance}, spread at most {distanceProfile.target.maxSpread}
            </div>
            <div>
              Measured: max distance {distanceProfile.maxDistance ?? "-"}, min distance {distanceProfile.minDistance ?? "-"}, spread {distanceProfile.spread ?? "-"}
            </div>
            <div>
              Checks: reachability {distanceProfile.allReach ? "PASS" : "FAIL"}, max distance {distanceProfile.distancePass ? "PASS" : "FAIL"}, spread {distanceProfile.spreadPass ? "PASS" : "FAIL"}
            </div>
            <div className={distanceProfile.overallPass ? "text-emerald-700 dark:text-emerald-400" : "text-red-600 dark:text-red-400"}>
              Overall profile status: {distanceProfile.overallPass ? "PASS" : "FAIL"}
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

export default function ChaosSetupLabPage() {
  return <ResearchPreviewGate><UnverifiedChaosSetupLabPage /></ResearchPreviewGate>;
}
