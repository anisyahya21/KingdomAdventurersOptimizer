import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Check, ChevronDown, ChevronLeft, ChevronRight, Diamond, Loader2, MapPin, Trophy } from "lucide-react";
import { Link } from "wouter";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { CharacterPreviewCanvas } from "@/components/character-preview-canvas";
import RuntimeWorldRenderTestPage from "@/pages/runtime-world-render-test";
import { fetchSharedWithFallback, localSharedData } from "@/lib/local-shared-data";
import { buildLocalAutomaticWeeklyConquestTimeline, resolveAutomaticWeeklyConquestTimelineForNow } from "@/lib/weekly-conquest";
import { apiUrl } from "@/lib/api";
import { getEquipmentIcon, getItemIcon } from "@/lib/equipment-icons";
import { getMonsterSprite } from "@/lib/monster-sprites";
import { cn } from "@/lib/utils";
import {
  MINED_MONSTER_SUMMARY_MAP,
  NATIVE_MAP,
  mapTerrainCodeToType,
  mergeUniqueSpawns,
  parseTerrainMapCsv,
  readCommunitySightings,
  type CommunitySighting,
  type TerrainType,
} from "@/lib/monster-truth";
import fullTerrainCsv from "../data/full-terrain-map.csv?raw";

type MonsterSpawn = { area: string; level: number };
type Monster = { icon?: string; spawns: MonsterSpawn[] };
type WeeklyReward = { jobName: string; jobRank: string; diamonds: number; equipment: string };
type WeeklyConquest = { monsters: string[]; reward: WeeklyReward; monsterCounts?: Record<string, number>; updatedBy?: string; updatedAt?: number } | null;
type WeeklyMonsterEntry = { name: string; count?: number; monster?: Monster; sprite?: ReturnType<typeof getMonsterSprite>; spawns: MonsterSpawn[] };
type WeeklySharedData = { monsters: Record<string, Monster>; weeklyConquest: WeeklyConquest; equipIcons?: Record<string, string> };
type WeeklyMonsterStyle = { color: string; patternIndex: number };

const CONQUEST_TIMELINE_RADIUS = 12;
const CONQUEST_CALENDAR_PAST_WEEKS = 4;
const CONQUEST_CALENDAR_FUTURE_WEEKS = 12;

const FULL_TERRAIN_MAP = parseTerrainMapCsv(fullTerrainCsv);

const TERRAIN_COLORS: Record<TerrainType, string> = {
  grass: "#38761d",
  sand: "#e9ddb2",
  volcano: "#ea7b70",
  swamp: "#3e948b",
  rock: "#d9d9d9",
  snow: "#f3f3f3",
  ground: "#c89a00",
};

function hexToRgb(hex: string) {
  const normalized = hex.replace("#", "");
  return {
    r: parseInt(normalized.slice(0, 2), 16),
    g: parseInt(normalized.slice(2, 4), 16),
    b: parseInt(normalized.slice(4, 6), 16),
  };
}

function rgbToHex(r: number, g: number, b: number) {
  return `#${[r, g, b].map((channel) => Math.max(0, Math.min(255, Math.round(channel))).toString(16).padStart(2, "0")).join("")}`;
}

function getRelativeLuminance(hex: string) {
  const { r, g, b } = hexToRgb(hex);
  const channels = [r, g, b].map((channel) => {
    const value = channel / 255;
    return value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
  });
  return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
}

function getReadableTextColor(background: string) {
  return getRelativeLuminance(background) > 0.46 ? "#0f172a" : "#ffffff";
}

function getPatternOverlayColor(background: string, alpha = 0.42) {
  const rgb = getRelativeLuminance(background) > 0.46 ? "15,23,42" : "255,255,255";
  return `rgba(${rgb},${alpha})`;
}

function desaturateHex(hex: string, mix = 0.92) {
  const { r, g, b } = hexToRgb(hex);
  const gray = Math.round(r * 0.299 + g * 0.587 + b * 0.114);
  const mixed = [r, g, b].map((channel) => Math.round(channel * (1 - mix) + gray * mix));
  return `rgb(${mixed[0]},${mixed[1]},${mixed[2]})`;
}

function mixHex(hex: string, target: string, amount: number) {
  const sourceRgb = hexToRgb(hex);
  const targetRgb = hexToRgb(target);
  return rgbToHex(
    sourceRgb.r + (targetRgb.r - sourceRgb.r) * amount,
    sourceRgb.g + (targetRgb.g - sourceRgb.g) * amount,
    sourceRgb.b + (targetRgb.b - sourceRgb.b) * amount,
  );
}

function shadeBiomeColor(hex: string, shadeIndex: number) {
  const variants = [
    { target: "#000000", amount: 0.18 },
    { target: "#1d4ed8", amount: 0.34 },
    { target: "#ffffff", amount: 0.42 },
    { target: "#1d4ed8", amount: 0.28 },
    { target: "#f97316", amount: 0.3 },
    { target: "#7c3aed", amount: 0.3 },
  ];
  const variant = variants[Math.abs(shadeIndex) % variants.length] ?? variants[0];
  return mixHex(hex, variant.target, variant.amount);
}

const FALLBACK_MONSTER_COLORS = ["#a855f7", "#38bdf8", "#f97316", "#22c55e", "#f43f5e"];

const TERRAIN_TYPE_TO_AREA: Record<TerrainType, string> = {
  grass: "Grass",
  sand: "Sand",
  volcano: "Volcano",
  swamp: "Swamp",
  rock: "Rock",
  snow: "Snow",
  ground: "Ground",
};

const AREA_TO_TERRAIN_TYPE = Object.fromEntries(
  Object.entries(TERRAIN_TYPE_TO_AREA).map(([terrain, area]) => [area.toLowerCase(), terrain]),
) as Record<string, TerrainType>;

function getNativeIndex(index: number, cellCount: number, nativeCount: number) {
  return Math.min(nativeCount - 1, Math.floor((index * nativeCount) / cellCount));
}

function areaKey(spawn: MonsterSpawn) {
  return `${spawn.area.trim().toLowerCase()}|${spawn.level}`;
}

function getEntryTerrain(entry: WeeklyMonsterEntry) {
  const spawn = entry.spawns.find((candidate) => candidate.area && candidate.area.toLowerCase() !== "dispatch");
  return spawn ? AREA_TO_TERRAIN_TYPE[spawn.area.trim().toLowerCase()] : undefined;
}

function getWeeklyMonsterStyles(entries: WeeklyMonsterEntry[]) {
  const biomeCounts = new Map<TerrainType, number>();
  return new Map(entries.map((entry, index) => {
    const terrain = getEntryTerrain(entry);
    if (!terrain) {
      return [entry.name, {
        color: FALLBACK_MONSTER_COLORS[index % FALLBACK_MONSTER_COLORS.length],
        patternIndex: index,
      }];
    }
    const shadeIndex = biomeCounts.get(terrain) ?? 0;
    biomeCounts.set(terrain, shadeIndex + 1);
    return [entry.name, {
      color: shadeBiomeColor(TERRAIN_COLORS[terrain], shadeIndex),
      patternIndex: shadeIndex,
    }];
  }));
}

function drawMonsterTilePattern(
  ctx: CanvasRenderingContext2D,
  px: number,
  py: number,
  cellSize: number,
  style: WeeklyMonsterStyle,
) {
  const pattern = style.patternIndex % 6;
  if (pattern === 0 || cellSize < 2) return;
  const size = Math.ceil(cellSize);
  const stroke = Math.max(1, Math.floor(cellSize / 5));
  ctx.save();
  ctx.strokeStyle = getPatternOverlayColor(style.color, 0.52);
  ctx.fillStyle = getPatternOverlayColor(style.color, 0.5);
  ctx.lineWidth = stroke;
  ctx.globalAlpha = 1;

  if (pattern === 1) {
    ctx.beginPath();
    ctx.moveTo(px, py + size);
    ctx.lineTo(px + size, py);
    ctx.stroke();
  } else if (pattern === 2) {
    ctx.beginPath();
    ctx.moveTo(px, py);
    ctx.lineTo(px + size, py + size);
    ctx.stroke();
  } else if (pattern === 3) {
    ctx.fillRect(px, py + Math.floor(size / 2), size, stroke);
  } else if (pattern === 4) {
    ctx.fillRect(px + Math.floor(size / 2), py, stroke, size);
  } else {
    const dotSize = Math.max(1, Math.floor(cellSize / 2));
    ctx.fillRect(px + Math.floor((size - dotSize) / 2), py + Math.floor((size - dotSize) / 2), dotSize, dotSize);
  }
  ctx.restore();
}

function getSelectorPatternBackground(style: WeeklyMonsterStyle) {
  const overlay = getPatternOverlayColor(style.color, 0.34);
  const pattern = style.patternIndex % 6;
  if (pattern === 1) return `repeating-linear-gradient(135deg, transparent 0 6px, ${overlay} 6px 9px)`;
  if (pattern === 2) return `repeating-linear-gradient(45deg, transparent 0 6px, ${overlay} 6px 9px)`;
  if (pattern === 3) return `repeating-linear-gradient(0deg, transparent 0 7px, ${overlay} 7px 10px)`;
  if (pattern === 4) return `repeating-linear-gradient(90deg, transparent 0 7px, ${overlay} 7px 10px)`;
  if (pattern === 5) return `radial-gradient(circle at center, ${overlay} 0 3px, transparent 3px)`;
  return undefined;
}

function WeeklySpawnMiniMap({
  entries,
  disabledMonsters,
  coveredAreaKeys,
  onToggleMonster,
  onToggleArea,
}: {
  entries: WeeklyMonsterEntry[];
  disabledMonsters: string[];
  coveredAreaKeys: string[];
  onToggleMonster: (monsterName: string) => void;
  onToggleArea: (spawn: MonsterSpawn) => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const [hovered, setHovered] = useState<{ spawn: MonsterSpawn; monsters: string[] } | null>(null);

  const disabledSet = useMemo(() => new Set(disabledMonsters), [disabledMonsters]);
  const coveredSet = useMemo(() => new Set(coveredAreaKeys), [coveredAreaKeys]);
  const monsterStyles = useMemo(() => getWeeklyMonsterStyles(entries), [entries]);
  const spawnMap = useMemo(() => {
    const map = new Map<string, string[]>();
    for (const entry of entries) {
      for (const spawn of entry.spawns) {
        const key = areaKey(spawn);
        map.set(key, [...(map.get(key) ?? []), entry.name]);
      }
    }
    return map;
  }, [entries]);

  const getSpawnAtPoint = useCallback((clientX: number, clientY: number) => {
    const canvas = canvasRef.current;
    if (!canvas) return null;
    const rect = canvas.getBoundingClientRect();
    const rows = FULL_TERRAIN_MAP.length;
    const cols = FULL_TERRAIN_MAP[0]?.length ?? 0;
    const cellSize = Math.max(1, Math.min(rect.width / cols, rect.height / rows));
    const mapWidth = cellSize * cols;
    const mapHeight = cellSize * rows;
    const offsetX = (rect.width - mapWidth) / 2;
    const offsetY = (rect.height - mapHeight) / 2;
    const localX = clientX - rect.left - offsetX;
    const localY = clientY - rect.top - offsetY;
    if (localX < 0 || localY < 0 || localX > mapWidth || localY > mapHeight) return null;
    const x = Math.max(0, Math.min(cols - 1, Math.floor(localX / cellSize)));
    const y = Math.max(0, Math.min(rows - 1, Math.floor(localY / cellSize)));
    const terrain = mapTerrainCodeToType(FULL_TERRAIN_MAP[y]?.[x]);
    if (!terrain) return null;
    const nativeY = getNativeIndex(y, rows, NATIVE_MAP.length);
    const nativeX = getNativeIndex(x, cols, NATIVE_MAP[0]?.length ?? 0);
    const level = NATIVE_MAP[nativeY]?.[nativeX]?.level;
    if (!Number.isFinite(level)) return null;
    const spawn = { area: TERRAIN_TYPE_TO_AREA[terrain], level };
    const monsters = spawnMap.get(areaKey(spawn)) ?? [];
    return monsters.length ? { spawn, monsters } : null;
  }, [spawnMap]);

  useEffect(() => {
    const canvas = canvasRef.current;
    const wrap = wrapRef.current;
    if (!canvas || !wrap) return;

    const draw = () => {
      const rows = FULL_TERRAIN_MAP.length;
      const cols = FULL_TERRAIN_MAP[0]?.length ?? 0;
      const rect = wrap.getBoundingClientRect();
      const size = Math.max(320, Math.min(760, Math.floor(rect.width)));
      const width = size;
      const height = size;
      const dpr = window.devicePixelRatio || 1;
      canvas.width = Math.floor(width * dpr);
      canvas.height = Math.floor(height * dpr);
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.imageSmoothingEnabled = false;
      const drawWidth = canvas.width;
      const drawHeight = canvas.height;
      ctx.clearRect(0, 0, drawWidth, drawHeight);
      const cellSize = Math.max(1, Math.min(drawWidth / cols, drawHeight / rows));
      const mapWidth = cellSize * cols;
      const mapHeight = cellSize * rows;
      const offsetX = (drawWidth - mapWidth) / 2;
      const offsetY = (drawHeight - mapHeight) / 2;

      for (let y = 0; y < rows; y += 1) {
        for (let x = 0; x < cols; x += 1) {
          const terrain = mapTerrainCodeToType(FULL_TERRAIN_MAP[y]?.[x]);
          const nativeY = getNativeIndex(y, rows, NATIVE_MAP.length);
          const nativeX = getNativeIndex(x, cols, NATIVE_MAP[0]?.length ?? 0);
          const level = NATIVE_MAP[nativeY]?.[nativeX]?.level;
          const spawn = terrain && Number.isFinite(level) ? { area: TERRAIN_TYPE_TO_AREA[terrain], level } : null;
          const monstersHere = spawn ? (spawnMap.get(areaKey(spawn)) ?? []) : [];
          const activeMonsters = monstersHere.filter((name) => !disabledSet.has(name));
          const hasWeeklySpawn = monstersHere.length > 0;
          const hasActiveSpawn = activeMonsters.length > 0;
          const isCovered = spawn ? coveredSet.has(areaKey(spawn)) : false;
          const terrainColor = terrain ? TERRAIN_COLORS[terrain] : "#172033";
          ctx.fillStyle = hasActiveSpawn ? terrainColor : desaturateHex(terrainColor);
          ctx.globalAlpha = hasActiveSpawn ? 0.95 : hasWeeklySpawn ? 0.62 : 0.42;
          const px = Math.round(offsetX + x * cellSize);
          const py = Math.round(offsetY + y * cellSize);
          const tileWidth = Math.max(1, Math.round(offsetX + (x + 1) * cellSize) - px);
          const tileHeight = Math.max(1, Math.round(offsetY + (y + 1) * cellSize) - py);
          const patternSize = Math.max(tileWidth, tileHeight);
          ctx.fillRect(px, py, tileWidth, tileHeight);
          ctx.globalAlpha = 1;
          if (hasActiveSpawn) {
            const activeStyles = activeMonsters
              .map((name, index) => monsterStyles.get(name) ?? { color: FALLBACK_MONSTER_COLORS[index % FALLBACK_MONSTER_COLORS.length], patternIndex: index });
            if (activeStyles.length === 1) {
              const style = activeStyles[0];
              ctx.fillStyle = style.color;
              ctx.globalAlpha = 0.68;
              ctx.fillRect(px, py, tileWidth, tileHeight);
              ctx.globalAlpha = 1;
              drawMonsterTilePattern(ctx, px, py, patternSize, style);
            } else {
              const checkerIndex = (x + y) % activeStyles.length;
              const style = activeStyles[checkerIndex] ?? activeStyles[0];
              ctx.fillStyle = style.color;
              ctx.globalAlpha = 0.72;
              ctx.fillRect(px, py, tileWidth, tileHeight);
              ctx.globalAlpha = 1;
              drawMonsterTilePattern(ctx, px, py, patternSize, style);
            }
          }
          if (isCovered && hasWeeklySpawn) {
            ctx.fillStyle = "rgba(16,185,129,0.72)";
            ctx.fillRect(px, py, Math.max(1, tileWidth * 0.45), Math.max(1, tileHeight * 0.45));
          }
        }
      }
    };

    draw();
    const observer = new ResizeObserver(draw);
    observer.observe(wrap);
    return () => observer.disconnect();
  }, [coveredSet, disabledSet, entries, monsterStyles, spawnMap]);

  return (
    <div className="mx-auto w-full max-w-[800px] rounded-lg border border-border bg-muted/10 p-2">
      <p className="mb-1.5 text-[11px] font-semibold text-muted-foreground">Weekly Spawn Map</p>
      <div className="mb-2 flex flex-wrap items-start gap-2">
        {entries.map((entry, index) => {
          const disabled = disabledSet.has(entry.name);
          const monsterStyle = monsterStyles.get(entry.name) ?? { color: FALLBACK_MONSTER_COLORS[index % FALLBACK_MONSTER_COLORS.length], patternIndex: index };
          const textColor = getReadableTextColor(monsterStyle.color);
          return (
            <button
              key={entry.name}
              type="button"
              onClick={() => onToggleMonster(entry.name)}
              className={cn(
                "flex w-[86px] flex-col items-center gap-1 rounded-md border px-1.5 py-1 text-[10px] font-semibold leading-tight transition-colors",
                disabled ? "border-border bg-muted/30 text-muted-foreground/50" : "border-transparent",
              )}
              style={disabled ? undefined : { backgroundColor: monsterStyle.color, backgroundImage: getSelectorPatternBackground(monsterStyle), color: textColor }}
            >
              <span className="flex h-11 w-11 items-center justify-center">
                {entry.monster?.icon ? (
                  <img src={entry.monster.icon} alt="" className="h-full w-full object-contain" loading="lazy" style={{ imageRendering: "pixelated" }} />
                ) : (
                  <span className="flex h-full w-full items-center justify-center">
                    <Trophy className="w-3.5 h-3.5 opacity-50" />
                  </span>
                )}
              </span>
              <span className="line-clamp-2 min-h-[1.5rem] break-words">{entry.name}</span>
            </button>
          );
        })}
      </div>
      <div ref={wrapRef} className="relative aspect-square w-full overflow-hidden rounded-md border border-border/60 bg-background/70">
        <canvas
          ref={canvasRef}
          className="mx-auto block aspect-square w-full cursor-pointer"
          onMouseMove={(event) => setHovered(getSpawnAtPoint(event.clientX, event.clientY))}
          onMouseLeave={() => setHovered(null)}
          onClick={(event) => {
            const hit = getSpawnAtPoint(event.clientX, event.clientY);
            if (hit) onToggleArea(hit.spawn);
          }}
        />
      </div>
      <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-[10px] text-muted-foreground">
        <div className="flex flex-wrap gap-2">
          <span>Bright: selected monster spawns</span>
          <span>Dim: inactive or hidden</span>
          <span>Green: covered</span>
        </div>
        <div className="min-h-4 text-right">
          {hovered ? (
            <span>
              {hovered.spawn.area} Lv{hovered.spawn.level}: {hovered.monsters.join(", ")}
            </span>
          ) : (
            <span>Hover or tap a bright area</span>
          )}
        </div>
      </div>
    </div>
  );
}

function useSharedData() {
  return useQuery({
    queryKey: ["ka-shared"],
    queryFn: () => fetchSharedWithFallback<WeeklySharedData>(apiUrl("/shared")),
    initialData: () => localSharedData as WeeklySharedData,
    staleTime: 15000,
    refetchOnWindowFocus: true,
  });
}

export default function WeeklyConquestPage() {
  const { data, isLoading } = useSharedData();
  const monsters = data?.monsters ?? {};
  const equipIcons = data?.equipIcons ?? {};
  const diamondsIcon = getItemIcon("Diamonds");
  const fallbackWeeklyConquest: WeeklyConquest = data?.weeklyConquest ?? null;
  const [showConquestCalendar, setShowConquestCalendar] = useState(false);
  const [timeNow, setTimeNow] = useState(() => Date.now());
  const [conquestOffset, setConquestOffset] = useState(0);
  const [manualConquestId, setManualConquestId] = useState<string>("0");
  const [manualConquestError, setManualConquestError] = useState<string | null>(null);
  const [deploymentQuery, setDeploymentQuery] = useState("");
  const [deploymentOpen, setDeploymentOpen] = useState(false);
  const [disabledMapMonsters, setDisabledMapMonsters] = useState<string[]>([]);
  const [expandedSpawnGroups, setExpandedSpawnGroups] = useState<string[]>([]);
  const [showLevelsOverlay, setShowLevelsOverlay] = useState(false);
  const [communitySightings] = useState<Record<string, CommunitySighting[]>>(() => readCommunitySightings());
  const [coveredConquestAreas, setCoveredConquestAreas] = useState<string[]>(() => {
    try {
      const saved = localStorage.getItem("ka_conquest_covered_areas");
      const parsed = saved ? JSON.parse(saved) : [];
      return Array.isArray(parsed) ? parsed : [];
    } catch {
      return [];
    }
  });
  const deploymentBoxRef = useRef<HTMLDivElement | null>(null);

  const { data: conquestTimeline } = useQuery({
    queryKey: ["weekly-conquest-automatic"],
    queryFn: () => buildLocalAutomaticWeeklyConquestTimeline(new Date(), CONQUEST_TIMELINE_RADIUS),
    initialData: () => buildLocalAutomaticWeeklyConquestTimeline(undefined, CONQUEST_TIMELINE_RADIUS),
    initialDataUpdatedAt: Date.now(),
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  });
  const currentTimeline = useMemo(
    () => resolveAutomaticWeeklyConquestTimelineForNow(conquestTimeline, new Date(timeNow), CONQUEST_TIMELINE_RADIUS),
    [conquestTimeline, timeNow],
  );

  const browsedConquest = currentTimeline.entries.find(
    (entry) => entry.id === (currentTimeline.currentId + conquestOffset),
  ) ?? null;

  const weeklyConquest: WeeklyConquest = browsedConquest
    ? { monsters: browsedConquest.monsters, reward: browsedConquest.reward, monsterCounts: browsedConquest.monsterCounts }
    : fallbackWeeklyConquest;
  const hasJobReward = Boolean(weeklyConquest?.reward?.jobName && weeklyConquest?.reward?.jobRank);
  const equipmentRewardIcon = weeklyConquest?.reward?.equipment
    ? getEquipmentIcon(equipIcons, weeklyConquest.reward.equipment)
    : undefined;

  const conquestMeta = browsedConquest
    ? {
        title: browsedConquest.name,
        subtitle: "Automatic from Campaign + Campaign_lookup",
        range: `${new Date(browsedConquest.startedAt).toLocaleString()} - ${new Date(browsedConquest.endsAt).toLocaleString()}`,
        isCurrent: conquestOffset === 0,
      }
    : fallbackWeeklyConquest?.updatedBy
      ? {
          title: "Manual fallback",
          subtitle: `Last updated by ${fallbackWeeklyConquest.updatedBy}`,
          range: fallbackWeeklyConquest.updatedAt ? new Date(fallbackWeeklyConquest.updatedAt).toLocaleString() : "",
          isCurrent: true,
        }
      : null;

  const canGoPrevious = Boolean(currentTimeline.entries.find((entry) => entry.id === currentTimeline.currentId + conquestOffset - 1));
  const canGoNext = Boolean(currentTimeline.entries.find((entry) => entry.id === currentTimeline.currentId + conquestOffset + 1));
  const currentTimelineId = currentTimeline.currentId;
  const conquestCalendarEntries = useMemo(() => {
    const currentIndex = currentTimeline.entries.findIndex((entry) => entry.id === currentTimelineId);
    if (currentIndex < 0) return currentTimeline.entries;
    return currentTimeline.entries.slice(
      Math.max(0, currentIndex - CONQUEST_CALENDAR_PAST_WEEKS),
      currentIndex + CONQUEST_CALENDAR_FUTURE_WEEKS + 1,
    );
  }, [currentTimeline, currentTimelineId]);
  const conquestEventEntries = conquestCalendarEntries;
  const selectedConquestId = currentTimelineId + conquestOffset;
  const isOngoingEvent = browsedConquest ? timeNow >= browsedConquest.startedAt && timeNow < browsedConquest.endsAt : false;

  useEffect(() => {
    setManualConquestId(String(selectedConquestId + 1));
  }, [selectedConquestId]);

  const selectConquestById = useCallback((value: number) => {
    if (!currentTimeline.entries.length) return;
    const targetId = value - 1;
    const entry = currentTimeline.entries.find((entryItem) => entryItem.id === targetId);
    if (!entry) {
      setManualConquestError("Event not available in the current timeline window.");
      return;
    }
    setManualConquestError(null);
    setConquestOffset(entry.id - currentTimelineId);
  }, [currentTimeline.entries, currentTimelineId]);
  const weeklyMonsterEntries = useMemo<WeeklyMonsterEntry[]>(() => {
    return (weeklyConquest?.monsters ?? []).map((monsterName) => {
      const monster = monsters[monsterName];
      const sprite = getMonsterSprite(monsterName);
      const minedSummary = MINED_MONSTER_SUMMARY_MAP[monsterName];
      const communitySpawns = communitySightings[monsterName] ?? [];
      const count = weeklyConquest?.monsterCounts?.[monsterName];
      return {
        name: monsterName,
        count,
        monster: {
          ...(monster ?? { spawns: [] }),
          icon: sprite?.src,
        },
        sprite,
        // Canonical spawn source: mined native map + optional community sightings.
        // Ignore legacy shared monster.spawns to prevent stale/incorrect conquest levels.
        spawns: mergeUniqueSpawns(minedSummary?.nativeMapSpawns, communitySpawns),
      };
    });
  }, [communitySightings, monsters, weeklyConquest]);

  const toggleMapMonster = useCallback((monsterName: string) => {
    setDisabledMapMonsters((current) => (
      current.includes(monsterName)
        ? current.filter((name) => name !== monsterName)
        : [...current, monsterName]
    ));
  }, []);

  const weeklyMonsterStyles = useMemo(() => getWeeklyMonsterStyles(weeklyMonsterEntries), [weeklyMonsterEntries]);

  const weeklyCoverageAreas = useMemo(() => {
    const coverage: Array<{ area: string; level: number }> = [];
    for (const entry of weeklyMonsterEntries) {
      if (disabledMapMonsters.includes(entry.name)) {
        continue;
      }
      for (const spawn of entry.spawns) {
        if (!spawn.area || spawn.area.toLowerCase() === "dispatch") {
          continue;
        }
        coverage.push({
          area: spawn.area,
          level: spawn.level,
        });
      }
    }
    return coverage;
  }, [disabledMapMonsters, weeklyMonsterEntries]);

  const conquestCountdown = useMemo(() => {
    if (!browsedConquest || !isOngoingEvent) return "";
    const diff = browsedConquest.endsAt - timeNow;
    if (diff <= 0) return "Event ending soon";
    const totalSeconds = Math.floor(diff / 1000);
    const hours = Math.floor(totalSeconds / 3600);
    const minutes = Math.floor((totalSeconds % 3600) / 60);
    const seconds = totalSeconds % 60;
    return `${hours}h ${String(minutes).padStart(2, "0")}m ${String(seconds).padStart(2, "0")}s remaining`;
  }, [browsedConquest, isOngoingEvent, timeNow]);

  useEffect(() => {
    const interval = window.setInterval(() => setTimeNow(Date.now()), 1000);
    return () => window.clearInterval(interval);
  }, []);

  useEffect(() => {
    setConquestOffset(0);
  }, [currentTimeline.currentId]);

  useEffect(() => {
    localStorage.setItem("ka_conquest_covered_areas", JSON.stringify(coveredConquestAreas));
  }, [coveredConquestAreas]);

  const conquestAreaKey = useCallback((spawn: MonsterSpawn) => `${spawn.area.trim().toLowerCase()}|${spawn.level}`, []);
  const toggleConquestArea = useCallback((spawn: MonsterSpawn) => {
    const key = conquestAreaKey(spawn);
    setCoveredConquestAreas((current) => current.includes(key) ? current.filter((value) => value !== key) : [...current, key]);
  }, [conquestAreaKey]);
  const isConquestAreaCovered = useCallback((spawn: MonsterSpawn) => coveredConquestAreas.includes(conquestAreaKey(spawn)), [coveredConquestAreas, conquestAreaKey]);

  const availableConquestDeployments = useMemo(() => {
    const seen = new Set<string>();
    const results: Array<{ label: string; spawn: MonsterSpawn }> = [];
    for (const entry of weeklyMonsterEntries) {
      for (const spawn of entry.spawns) {
        if (!spawn.area || spawn.area === "Dispatch") continue;
        const key = `${spawn.area.trim().toLowerCase()}|${spawn.level}`;
        if (seen.has(key)) continue;
        seen.add(key);
        results.push({ label: `${spawn.area} Lv${spawn.level}`, spawn });
      }
    }
    return results.sort((a, b) => {
      const areaCmp = a.spawn.area.localeCompare(b.spawn.area);
      if (areaCmp !== 0) return areaCmp;
      return a.spawn.level - b.spawn.level;
    });
  }, [weeklyMonsterEntries]);

  const filteredConquestDeployments = useMemo(() => {
    const q = deploymentQuery.trim().toLowerCase();
    if (!q) return availableConquestDeployments;
    return availableConquestDeployments.filter(({ label, spawn }) => {
      const normalizedLabel = label.toLowerCase();
      const area = spawn.area.toLowerCase();
      const level = String(spawn.level);
      const compact = `${area} lv${level}`;
      return normalizedLabel.includes(q) || area.includes(q) || level.includes(q) || compact.includes(q.replace(/\s+/g, " ").trim());
    });
  }, [deploymentQuery, availableConquestDeployments]);

  const addDeploymentFromSearch = useCallback((spawn: MonsterSpawn) => {
    if (!isConquestAreaCovered(spawn)) toggleConquestArea(spawn);
    setDeploymentQuery("");
    setDeploymentOpen(false);
  }, [isConquestAreaCovered, toggleConquestArea]);

  useEffect(() => {
    const handleOutsideClick = (event: MouseEvent) => {
      if (!deploymentBoxRef.current) return;
      if (!deploymentBoxRef.current.contains(event.target as Node)) setDeploymentOpen(false);
    };
    document.addEventListener("mousedown", handleOutsideClick);
    return () => document.removeEventListener("mousedown", handleOutsideClick);
  }, []);

  return (
    <div className="min-h-screen bg-background transition-colors">
      <div className="max-w-5xl mx-auto px-3 py-5">
        <div className="flex items-center gap-3 mb-3">
          <h1 className="text-base font-bold text-foreground flex items-center gap-2">
            <Trophy className="w-4 h-4 text-amber-500" />Weekly Conquest
          </h1>
        </div>

        {isLoading ? (
          <div className="flex items-center justify-center py-16"><Loader2 className="w-6 h-6 animate-spin text-muted-foreground" /></div>
        ) : (
          <Card className="shadow-sm mb-4 border-violet-200 dark:border-violet-900/50">
            <CardHeader className="p-3 pb-2">
              <CardTitle className="text-sm flex items-center gap-2">
                <Trophy className="w-3.5 h-3.5 text-amber-500" />Weekly Conquest
              </CardTitle>
              {conquestMeta ? (
                <div className="space-y-0.5">
                  <p className="text-[11px] text-muted-foreground">{conquestMeta.subtitle}</p>
                  {conquestMeta.range ? <p className="text-[11px] text-muted-foreground/70">{conquestMeta.range}</p> : null}
                  {conquestCountdown ? (
                    <div className="inline-flex items-center rounded-full bg-amber-500/10 px-2 py-0.5 text-[11px] font-semibold text-amber-400 dark:text-amber-300">
                      {conquestCountdown}
                    </div>
                  ) : null}
                </div>
              ) : null}
            </CardHeader>
            <CardContent className="p-3 pt-0">
              <div className="space-y-2">
                {conquestMeta?.title ? (
                  <div className="rounded-md border border-border bg-muted/20 px-2.5 py-1.5">
                    <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                      <div>
                        <p className="text-[10px] text-muted-foreground">{conquestMeta.isCurrent ? "Current Event" : conquestOffset < 0 ? "Past Event" : "Upcoming Event"}</p>
                        <p className="text-xs font-semibold">{conquestMeta.title}</p>
                      </div>
                      {conquestTimeline?.entries?.length ? (
                        <div className="flex flex-wrap items-center gap-1">
                          <Button size="icon" variant="outline" className="h-7 w-7" disabled={!canGoPrevious} onClick={() => setConquestOffset((value) => value - 1)}>
                            <ChevronLeft className="w-3.5 h-3.5" />
                          </Button>
                          <Button size="sm" variant="ghost" className="h-7 px-2 text-[11px]" disabled={conquestOffset === 0} onClick={() => setConquestOffset(0)}>
                            Back to This Week
                          </Button>
                          <Button size="icon" variant="outline" className="h-7 w-7" disabled={!canGoNext} onClick={() => setConquestOffset((value) => value + 1)}>
                            <ChevronRight className="w-3.5 h-3.5" />
                          </Button>
                        </div>
                      ) : null}
                    </div>
                  </div>
                ) : null}

                {conquestEventEntries.length > 0 ? (
                  <div className="-mx-3 overflow-x-auto px-3">
                    <div className="inline-flex gap-1.5 py-1.5 min-w-[max-content]">
                      {conquestEventEntries.map((entry) => {
                        const offset = entry.id - currentTimelineId;
                        const isSelected = entry.id === selectedConquestId;
                        const stateClasses = isSelected ? "bg-primary text-primary-foreground border-primary" : offset < 0 ? "bg-muted text-muted-foreground border-border" : "bg-muted/80 text-muted-foreground border-border";
                        return (
                          <button key={entry.id} type="button" onClick={() => setConquestOffset(offset)} className={`shrink-0 rounded-lg border px-2.5 py-1.5 text-left text-[10px] transition-colors ${stateClasses}`}>
                            <div className="font-semibold leading-tight">{entry.name}</div>
                            <div className="text-[9px] text-muted-foreground/70">{offset === 0 ? "Current" : offset < 0 ? `${-offset} past` : `${offset} upcoming`}</div>
                          </button>
                        );
                      })}
                    </div>
                  </div>
                ) : null}

                <div className="rounded-md border border-border bg-muted/10 px-2.5 py-2">
                  <div className="flex items-center justify-between gap-3">
                    <p className="text-xs font-semibold text-muted-foreground">Event Reward</p>
                    {isOngoingEvent ? <span className="rounded-full border border-border px-2 py-0.5 text-[10px] text-muted-foreground">Ongoing event</span> : null}
                  </div>
                  <div className="mt-2.5 grid grid-cols-3 gap-2">
                    <div className="rounded-md border border-border/60 bg-background/45 px-2 py-2 text-center">
                      <p className="text-[10px] font-semibold text-muted-foreground">{weeklyConquest?.reward?.equipment ? weeklyConquest.reward.equipment : "Equipment - not set"}</p>
                      <div className="mt-1.5 flex h-12 items-center justify-center">
                        {equipmentRewardIcon ? (
                          <img src={equipmentRewardIcon} alt="" className="h-11 w-11 object-contain" style={{ imageRendering: "pixelated" }} />
                        ) : (
                          <Trophy className="h-7 w-7 text-muted-foreground/50" />
                        )}
                      </div>
                    </div>
                    <div className="rounded-md border border-border/60 bg-background/45 px-2 py-2 text-center">
                      <p className="text-[10px] font-semibold text-muted-foreground">{hasJobReward ? `${weeklyConquest!.reward.jobRank} - ${weeklyConquest!.reward.jobName}` : "Job reward - not set"}</p>
                      <div className="mt-1.5 flex h-12 items-center justify-center">
                        {hasJobReward ? (
                          <CharacterPreviewCanvas
                            jobName={weeklyConquest!.reward.jobName}
                            rank={weeklyConquest!.reward.jobRank}
                            variant={1}
                            equipState="right"
                            scale={2}
                            poseFrame={0}
                            label="Weekly conquest male job reward"
                            className="h-11 w-auto"
                          />
                        ) : (
                          <Trophy className="h-7 w-7 text-muted-foreground/50" />
                        )}
                      </div>
                    </div>
                    <div className="rounded-md border border-border/60 bg-background/45 px-2 py-2 text-center">
                      <p className="text-[10px] font-semibold text-muted-foreground">{weeklyConquest?.reward && weeklyConquest.reward.diamonds > 0 ? `${weeklyConquest.reward.diamonds.toLocaleString()} Diamonds` : "Diamonds - not set"}</p>
                      <div className="mt-1.5 flex h-12 items-center justify-center">
                        {diamondsIcon ? (
                          <img src={diamondsIcon} alt="" className="h-10 w-10 object-contain" style={{ imageRendering: "pixelated" }} />
                        ) : (
                          <Diamond className="h-7 w-7 text-muted-foreground/50" />
                        )}
                      </div>
                    </div>
                  </div>
                </div>

                <div className="grid gap-2 lg:grid-cols-[1fr_auto]">
                  <div className="rounded-md border border-border bg-muted/10 p-2">
                    <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                      <div>
                        <p className="text-xs font-semibold text-muted-foreground">Event Calendar</p>
                        <p className="text-[11px] text-muted-foreground">Past events, this week, and the next 12 weeks with rewards.</p>
                      </div>
                      <Button size="sm" variant="ghost" className="h-7 px-2 text-[11px]" onClick={() => setShowConquestCalendar((value) => !value)}>
                        {showConquestCalendar ? <ChevronDown className="w-3.5 h-3.5 mr-1" /> : <ChevronRight className="w-3.5 h-3.5 mr-1" />}
                        {showConquestCalendar ? "Collapse" : "Expand"}
                      </Button>
                    </div>
                    {showConquestCalendar ? (
                      <div className="mt-3 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
                        {conquestCalendarEntries.map((entry) => {
                          const isCurrent = entry.id === currentTimelineId;
                          const isPast = entry.id < currentTimelineId;
                          const entryLabel = isCurrent ? "Current" : isPast ? "Past" : "Upcoming";
                          const equipmentIcon = entry.reward?.equipment
                            ? getEquipmentIcon(equipIcons, entry.reward.equipment)
                            : undefined;
                          const hasCalendarJobReward = Boolean(entry.reward?.jobName && entry.reward?.jobRank);
                          return (
                            <div key={entry.id} className={`rounded-xl border p-2 ${isCurrent ? "border-primary bg-primary/10" : "border-border bg-muted/50"}`}>
                              <div className="flex items-center justify-between gap-2">
                                <div className="min-w-0">
                                  <p className="text-[11px] font-semibold leading-snug truncate">{entry.name}</p>
                                  <p className="text-[9px] text-muted-foreground">{new Date(entry.startedAt).toLocaleDateString()} - {new Date(entry.endsAt).toLocaleDateString()}</p>
                                </div>
                                <span className={`rounded-full px-2 py-0.5 text-[9px] font-medium ${isCurrent ? "bg-primary text-primary-foreground" : isPast ? "bg-muted text-muted-foreground" : "bg-muted/70 text-muted-foreground"}`}>
                                  {entryLabel}
                                </span>
                              </div>
                              <div className="mt-1.5 flex min-w-0 flex-wrap items-center gap-x-2.5 gap-y-1">
                                {entry.reward?.equipment ? (
                                  <span className="inline-flex min-w-0 max-w-full items-center gap-1" title={entry.reward.equipment}>
                                    {equipmentIcon ? (
                                      <img src={equipmentIcon} alt="" className="h-4 w-4 shrink-0 object-contain" style={{ imageRendering: "pixelated" }} />
                                    ) : <Trophy className="h-3.5 w-3.5 shrink-0 text-muted-foreground/60" />}
                                    <span className="max-w-[150px] truncate text-[9px] text-muted-foreground">{entry.reward.equipment}</span>
                                  </span>
                                ) : null}
                                {hasCalendarJobReward ? (
                                  <span className="inline-flex min-w-0 max-w-full items-center gap-1" title={`${entry.reward.jobRank} - ${entry.reward.jobName}`}>
                                    <CharacterPreviewCanvas
                                      jobName={entry.reward.jobName}
                                      rank={entry.reward.jobRank}
                                      variant={1}
                                      equipState="right"
                                      scale={1}
                                      poseFrame={0}
                                      label={`${entry.reward.jobRank} ${entry.reward.jobName} conquest reward`}
                                      className="h-4 w-4 shrink-0"
                                    />
                                    <span className="max-w-[120px] truncate text-[9px] text-muted-foreground">{entry.reward.jobRank} - {entry.reward.jobName}</span>
                                  </span>
                                ) : null}
                                {entry.reward?.diamonds > 0 ? (
                                  <span className="inline-flex items-center gap-1 text-[9px] text-muted-foreground" title={`${entry.reward.diamonds.toLocaleString()} Diamonds`}>
                                    {diamondsIcon ? (
                                      <img src={diamondsIcon} alt="" className="h-4 w-4 shrink-0 object-contain" style={{ imageRendering: "pixelated" }} />
                                    ) : <Diamond className="h-3.5 w-3.5 shrink-0" />}
                                    <span>{entry.reward.diamonds.toLocaleString()} Diamonds</span>
                                  </span>
                                ) : null}
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    ) : null}
                  </div>
                  <div className="rounded-md border border-border bg-muted/10 p-3">
                    <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">Go to Conquest Event</p>
                    <div className="mt-2 flex items-center gap-2">
                      <span className="text-sm font-semibold">#</span>
                      <Input
                        type="number"
                        min={1}
                        max={99}
                        value={manualConquestId}
                        onChange={(event) => {
                          const next = event.target.value.replace(/[^0-9]/g, "");
                          setManualConquestError(null);
                          setManualConquestId(next);
                        }}
                        onKeyDown={(event) => {
                          if (event.key === "Enter") {
                            const nextId = Number(manualConquestId);
                            if (Number.isInteger(nextId) && nextId >= 1 && nextId <= 99) {
                              selectConquestById(nextId);
                            } else {
                              setManualConquestError("Enter a number between 1 and 99.");
                            }
                          }
                        }}
                        placeholder="1-99"
                        className="h-9 w-[96px] text-sm"
                      />
                      <Button
                        size="sm"
                        variant="outline"
                        className="h-9 px-3 text-[11px]"
                        onClick={() => {
                          const nextId = Number(manualConquestId);
                          if (Number.isInteger(nextId) && nextId >= 1 && nextId <= 99) {
                            selectConquestById(nextId);
                          } else {
                            setManualConquestError("Enter a number between 1 and 99.");
                          }
                        }}
                      >
                        Go
                      </Button>
                    </div>
                    {manualConquestError ? <p className="mt-2 text-[10px] text-destructive">{manualConquestError}</p> : null}
                  </div>
                </div>

                <div className="grid grid-cols-2 lg:grid-cols-3 items-start gap-2">
                  {Array.from({ length: Math.max(5, weeklyMonsterEntries.length) + 1 }).map((_, i) => {
                    if (i === Math.max(5, weeklyMonsterEntries.length)) {
                      return (
                        <div key="add-deployments" ref={deploymentBoxRef} className="col-span-2 lg:col-span-1 rounded-md border border-dashed border-border bg-muted/10 px-2 py-1.5">
                          <p className="mb-1 text-[11px] font-semibold text-muted-foreground">Add Deployments</p>
                          <div className="relative">
                            <Input
                              value={deploymentQuery}
                              onChange={(e) => {
                                setDeploymentQuery(e.target.value);
                                setDeploymentOpen(true);
                              }}
                              onFocus={() => setDeploymentOpen(true)}
                              onKeyDown={(e) => {
                                if (e.key === "Enter") {
                                  e.preventDefault();
                                  if (filteredConquestDeployments.length > 0) addDeploymentFromSearch(filteredConquestDeployments[0].spawn);
                                }
                                if (e.key === "Escape") setDeploymentOpen(false);
                              }}
                              placeholder="Type area or level..."
                              className="h-7 text-[11px]"
                            />
                            {deploymentOpen ? (
                              <div className="absolute z-20 top-full left-0 right-0 mt-1 rounded-md border border-border bg-popover shadow-lg max-h-48 overflow-y-auto">
                                {filteredConquestDeployments.length > 0 ? (
                                  filteredConquestDeployments.map(({ label, spawn }) => {
                                    const covered = isConquestAreaCovered(spawn);
                                    return (
                                      <button
                                        key={`${spawn.area}-${spawn.level}`}
                                        type="button"
                                        onMouseDown={(e) => {
                                          e.preventDefault();
                                          addDeploymentFromSearch(spawn);
                                        }}
                                        className={`w-full text-left px-2.5 py-1.5 text-[11px] border-b border-border last:border-b-0 hover:bg-muted/50 transition-colors ${covered ? "text-emerald-400" : "text-foreground"}`}
                                      >
                                        <span className="inline-flex items-center gap-1.5">
                                          {covered ? <Check className="w-3 h-3" /> : <MapPin className="w-3 h-3 text-muted-foreground" />}
                                          {label}
                                        </span>
                                      </button>
                                    );
                                  })
                                ) : (
                                  <div className="px-2.5 py-2 text-xs text-muted-foreground">No matching deployments this week.</div>
                                )}
                              </div>
                            ) : null}
                          </div>
                          <p className="text-[9px] text-muted-foreground mt-1.5">Only deployments from this week&apos;s conquest are available.</p>
                          <p className="text-[10px] text-muted-foreground/70 mt-1">Covered: {coveredConquestAreas.length}</p>
                        </div>
                      );
                    }

                    const entry = weeklyMonsterEntries[i];
                    const mName = entry?.name;
                    const minedSummary = mName ? MINED_MONSTER_SUMMARY_MAP[mName] : undefined;
                    const displaySpawns = entry?.spawns ?? [];
                    const spawnGroups = new Map<string, { area: string; minLevel: number; spawns: MonsterSpawn[] }>();
                    for (const spawn of displaySpawns) {
                      const key = spawn.area.trim().toLowerCase();
                      if (!key) continue;
                      const group = spawnGroups.get(key) ?? { area: spawn.area.trim(), minLevel: spawn.level, spawns: [] };
                      group.minLevel = Math.min(group.minLevel, spawn.level);
                      group.spawns.push(spawn);
                      spawnGroups.set(key, group);
                    }
                    if (minedSummary) {
                      const key = minedSummary.terrainName.trim().toLowerCase();
                      const group = spawnGroups.get(key) ?? { area: minedSummary.terrainName, minLevel: minedSummary.areaLevelMin, spawns: [] };
                      group.area = minedSummary.terrainName;
                      group.minLevel = minedSummary.areaLevelMin;
                      spawnGroups.set(key, group);
                    }
                    const spawnSummaries = Array.from(spawnGroups.entries());
                    return mName ? (
                      <div key={i} className="min-w-0 rounded-md border border-border bg-muted/20 px-2 py-2 text-[11px]">
                        <div className="flex items-center gap-2">
                          <div className="flex h-11 w-11 shrink-0 items-center justify-center">
                            {entry.sprite?.src ? (
                              <img src={entry.sprite.src} alt={mName} className="h-full w-full object-contain" loading="lazy" style={{ imageRendering: "pixelated" }} />
                            ) : <Trophy className="w-4 h-4 text-muted-foreground/40" />}
                          </div>
                          <div className="min-w-0 flex-1">
                            <Link
                              href={`/monsters?monster=${encodeURIComponent(mName)}`}
                              className="line-clamp-2 text-[11px] font-semibold leading-tight text-foreground hover:text-primary underline-offset-2 hover:underline"
                            >
                              {mName}
                            </Link>
                            <p className="text-[9px] text-muted-foreground">Kills: {entry.count != null ? entry.count.toLocaleString() : "-"}</p>
                          </div>
                        </div>
                        {spawnSummaries.length > 0 ? (
                          <div className="mt-1.5 flex flex-wrap gap-1">
                            {spawnSummaries.map(([key, group]) => {
                              const expandedKey = `${mName}|${key}`;
                              const isExpanded = expandedSpawnGroups.includes(expandedKey);
                              return (
                                <div key={key} className="min-w-0 max-w-full">
                                  <button
                                    type="button"
                                    aria-expanded={isExpanded}
                                    onClick={() => setExpandedSpawnGroups((current) => (
                                      current.includes(expandedKey)
                                        ? current.filter((value) => value !== expandedKey)
                                        : [...current, expandedKey]
                                    ))}
                                    className="inline-flex max-w-full items-center gap-1 rounded bg-muted px-1.5 py-1 text-[10px] leading-tight text-muted-foreground transition-colors hover:text-foreground"
                                    title={`${group.area}, minimum level ${group.minLevel}. Tap to ${isExpanded ? "hide" : "show"} recorded spawn levels.`}
                                  >
                                    <span className="truncate font-medium">{group.area} Lv{group.minLevel}+</span>
                                    <ChevronDown className={`h-3 w-3 shrink-0 transition-transform ${isExpanded ? "rotate-180" : ""}`} />
                                  </button>
                                  {isExpanded && group.spawns.length > 0 ? (
                                    <div className="mt-1 grid grid-cols-2 gap-1">
                                      {group.spawns.map((spawn, spawnIndex) => (
                                        <button
                                          key={`${spawn.area}-${spawn.level}-${spawnIndex}`}
                                          type="button"
                                          onClick={() => toggleConquestArea(spawn)}
                                          className={`inline-flex min-w-0 items-center gap-1 rounded px-1 py-1 text-[10px] leading-tight transition-colors ${isConquestAreaCovered(spawn) ? "bg-emerald-500/15 text-emerald-300" : "bg-background/60 text-muted-foreground"}`}
                                          title={`${spawn.area} Lv${spawn.level}: ${isConquestAreaCovered(spawn) ? "marked as covered" : "tap to mark as covered"}`}
                                        >
                                          {isConquestAreaCovered(spawn) ? <Check className="h-3 w-3 shrink-0" /> : <MapPin className="h-3 w-3 shrink-0" />}
                                          <span className="truncate">Lv{spawn.level}</span>
                                        </button>
                                      ))}
                                    </div>
                                  ) : null}
                                </div>
                              );
                            })}
                          </div>
                        ) : (
                          <div className="mt-1.5 rounded bg-background/30 px-1.5 py-1">
                            <p className="text-[9px] text-muted-foreground/70">No recorded spawn levels.</p>
                          </div>
                        )}
                      </div>
                    ) : (
                      <div key={i} className="flex h-11 items-center gap-2 rounded-md border border-dashed border-border bg-muted/10 px-2 py-1.5">
                        <div className="w-7 h-7 rounded-md border border-dashed border-border/50 flex items-center justify-center shrink-0">
                          <Trophy className="w-3.5 h-3.5 text-muted-foreground/20" />
                        </div>
                        <p className="text-xs text-muted-foreground/40">Slot {i + 1} - not set</p>
                      </div>
                    );
                  })}
                </div>

                <Card className="overflow-hidden">
                  <CardHeader className="pb-2">
                    <CardTitle className="text-sm">Weekly Conquest World Map (V2)</CardTitle>
                  </CardHeader>
                  <CardContent className="p-2">
                    <div className="mb-2 rounded-lg border border-border bg-muted/10 p-2">
                      <p className="mb-1.5 text-[11px] font-semibold text-muted-foreground">Weekly Spawn Map</p>
                      <div className="flex flex-wrap items-start gap-2">
                        {weeklyMonsterEntries.map((entry, index) => {
                          const selected = !disabledMapMonsters.includes(entry.name);
                          const monsterStyle = weeklyMonsterStyles.get(entry.name) ?? {
                            color: FALLBACK_MONSTER_COLORS[index % FALLBACK_MONSTER_COLORS.length],
                            patternIndex: index,
                          };
                          const textColor = getReadableTextColor(monsterStyle.color);
                          return (
                            <button
                              key={entry.name}
                              type="button"
                              onClick={() => toggleMapMonster(entry.name)}
                              aria-pressed={selected}
                              className={cn(
                                "flex w-[86px] flex-col items-center gap-1 rounded-md border px-1.5 py-1 text-[10px] font-semibold leading-tight transition-colors",
                                selected
                                  ? "border-primary/70 ring-1 ring-primary/40"
                                  : "border-border bg-muted/20 text-muted-foreground/80",
                              )}
                              style={selected ? { backgroundColor: monsterStyle.color, backgroundImage: getSelectorPatternBackground(monsterStyle), color: textColor } : undefined}
                            >
                              <span className="flex h-11 w-11 items-center justify-center">
                                {entry.monster?.icon ? (
                                  <img src={entry.monster.icon} alt="" className="h-full w-full object-contain" loading="lazy" style={{ imageRendering: "pixelated" }} />
                                ) : (
                                  <span className="flex h-full w-full items-center justify-center">
                                    <Trophy className="w-3.5 h-3.5 opacity-50" />
                                  </span>
                                )}
                              </span>
                              <span className="line-clamp-2 min-h-[1.5rem] break-words">
                                {entry.name}{entry.count ? ` (${entry.count})` : ""}
                              </span>
                            </button>
                          );
                        })}
                        <button
                          type="button"
                          onClick={() => setShowLevelsOverlay((previous) => !previous)}
                          aria-pressed={showLevelsOverlay}
                          className={cn(
                            "flex w-[86px] flex-col items-center justify-center gap-1 rounded-md border px-1.5 py-1 text-[10px] font-semibold leading-tight transition-colors min-h-[78px]",
                            showLevelsOverlay
                              ? "border-primary/70 bg-primary/25 text-primary ring-1 ring-primary/40"
                              : "border-border bg-muted/20 text-muted-foreground/80",
                          )}
                        >
                          <span className="inline-flex rounded-sm border border-current px-1 py-0.5 text-[10px]">123</span>
                          <span className="line-clamp-2 min-h-[1.5rem] break-words">Show levels</span>
                        </button>
                      </div>
                    </div>

                    <RuntimeWorldRenderTestPage
                      publicMode
                      initialZoom={0.28}
                      showLevelOverlay={showLevelsOverlay}
                      hideNatureToggleButtons
                      dimUncoveredConquestAreas
                      conquestCoverageAreas={weeklyCoverageAreas}
                      initialNatureVisibility={{
                        terrain: false,
                        resources: false,
                        humans: false,
                        special: false,
                      }}
                    />
                  </CardContent>
                </Card>
              </div>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}
