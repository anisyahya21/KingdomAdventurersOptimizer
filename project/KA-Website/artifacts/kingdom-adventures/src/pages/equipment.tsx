import { useState, useMemo, useCallback, useRef, Fragment, useEffect } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearch } from "wouter";

import {
  ArrowUpDown, ArrowUp, ArrowDown, RefreshCw,
  Loader2, AlertTriangle, Info, X, ImageIcon, Pencil,
  ChevronDown, ChevronRight, Download, History, CheckSquare, GripVertical,
  Plus, Copy, Settings2, Clock, CheckCircle2, Star, Eye, EyeOff,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ThemedNumberInput } from "@/components/ui/themed-number-input";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

import { SearchableSelect } from "@/components/searchable-select";
import { PageHeader } from "@/components/ka/page-header";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { fetchSharedWithFallback, localSharedData } from "@/lib/local-shared-data";
import { apiUrl, googleSheetUrl } from "@/lib/api";
import { getEquipmentIcon, getEquipmentIconKeys } from "@/lib/equipment-icons";
import { compareEquipmentOriginalOrder } from "@/lib/equipment-order";
import { parseCsv } from "@/lib/monster-truth";
import { matchesLooseSearch } from "@/lib/search-normalize";
import { readBrowserCache, writeBrowserCache } from "@/lib/browser-cache";
import equipCsv from "../../../../data/sheet-research/raw-copies/KA GameData - Equip.csv?raw";
const RANKED_EQUIPMENT_NAME = /^[FSABCDE]\s*\/\s*/i;
const VARIANT_NAME_BY_ID: Record<number, string> = {
  192: "B/ Legendary Shield (B)",
  198: "B/ Legendary Shield (R)",
  235: "E/ Hat (B)",
  237: "E/ Hat (R)",
};

function getVariantDisplayName(name: string, sourceId: number | null): string {
  if (sourceId === null) return name;
  return VARIANT_NAME_BY_ID[sourceId] ?? name;
}

// â”€â”€â”€ NumInput: local-string-state to prevent typing glitch â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
function NumInput({
  value, onChange, min = 0, max = 99, className = "", inputClassName = "",
}: {
  value: number; onChange: (v: number) => void;
  min?: number; max?: number; className?: string; inputClassName?: string;
}) {
  const [local, setLocal] = useState(String(value));
  const prevRef = useRef(value);
  useEffect(() => {
    if (value !== prevRef.current) { setLocal(String(value)); prevRef.current = value; }
  }, [value]);
  const updateWhileTyping = (next: string) => {
    setLocal(next);
    if (next.trim() === "") return;
    const parsed = parseInt(next, 10);
    if (isNaN(parsed)) return;
    const clamped = Math.min(max, Math.max(min, parsed));
    prevRef.current = clamped;
    if (clamped !== value) onChange(clamped);
  };
  const commit = () => {
    const parsed = parseInt(local, 10);
    const clamped = isNaN(parsed) ? min : Math.min(max, Math.max(min, parsed));
    setLocal(String(clamped));
    prevRef.current = clamped;
    if (clamped !== value) onChange(clamped);
  };
  return (
    <ThemedNumberInput
      value={local}
      min={min}
      max={max}
      className={className}
      inputClassName={inputClassName}
      onRawChange={updateWhileTyping}
      onRawBlur={commit}
      onEnter={commit}
    />
  );
}

// â”€â”€â”€ Constants â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

const STAT_FULL: Record<string, string> = {
  hp: "HP", mp: "MP",
  vig: "Vigor", vigor: "Vigor",
  atk: "Attack", attack: "Attack",
  def: "Defence", defence: "Defence", defense: "Defence",
  spd: "Speed", speed: "Speed",
  lck: "Luck", luck: "Luck",
  int: "Intelligence", intelligence: "Intelligence",
  dex: "Dexterity", dexterity: "Dexterity",
  gth: "Gather", gather: "Gather",
  mov: "Move", move: "Move",
  hrt: "Heart", heart: "Heart",
};

const STAT_ORDER = ["HP","MP","Vigor","Attack","Defence","Speed","Luck","Intelligence","Dexterity","Gather","Move","Heart"];
const STAT_SHORT: Record<string, string> = {
  HP: "HP",
  MP: "MP",
  Vigor: "Vig",
  Attack: "Atk",
  Defence: "Def",
  Speed: "Spd",
  Luck: "Lck",
  Intelligence: "Int",
  Dexterity: "Dex",
  Gather: "Gth",
  Move: "Mov",
  Heart: "Hrt",
};
const MOBILE_SORT_OPTIONS = [
  { value: "mobile-sort", label: "Sort by" },
  { value: "name", label: "Name" },
  { value: "element", label: "Element" },
  { value: "slot", label: "Slot" },
  { value: "studioLevel", label: "Studio Lv" },
  { value: "intReq", label: "INT Req" },
  ...STAT_ORDER.map((stat) => ({ value: stat, label: STAT_SHORT[stat] ?? stat })),
];
const STAT_ALIAS_FOR: Record<string, string> = { Movement: "Move" };
const CHAR_SLOTS = ["Head", "Weapon", "Shield", "Armor", "Accessory"] as const;
type CharSlot = typeof CHAR_SLOTS[number];
const SLOT_OPTIONS: Array<CharSlot | "-"> = ["-", "Head", "Weapon", "Shield", "Armor", "Accessory"];

// â”€â”€â”€ Slot SVG icons â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

function SlotIcon({ slot, className = "w-6 h-6" }: { slot: string; className?: string }) {
  const p = { viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.6, strokeLinecap: "round" as const, strokeLinejoin: "round" as const, className };
  switch (slot) {
    case "Head": return (
      <svg {...p}>
        <path d="M12 3C7.582 3 4 6.686 4 11v1h16v-1c0-4.314-3.582-8-8-8z" />
        <rect x="2" y="12" width="20" height="3" rx="1" />
        <line x1="9" y1="15" x2="9" y2="18" />
        <line x1="15" y1="15" x2="15" y2="18" />
        <line x1="9" y1="18" x2="15" y2="18" />
      </svg>
    );
    case "Weapon": return (
      <svg {...p}>
        <line x1="5" y1="19" x2="19" y2="5" />
        <path d="M15 5h4v4" />
        <line x1="7" y1="14" x2="10" y2="17" />
        <line x1="4" y1="17" x2="7" y2="20" />
        <line x1="4" y1="20" x2="7" y2="17" />
      </svg>
    );
    case "Shield": return (
      <svg {...p}>
        <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
        <path d="M9 12l2 2 4-4" />
      </svg>
    );
    case "Armor": return (
      <svg {...p}>
        {/* Breastplate body */}
        <path d="M5 9L12 6L19 9V16C19 19 16 21 12 22C8 21 5 19 5 16V9Z" />
        {/* Left pauldron */}
        <path d="M5 9C5 9 2 9 2 12L5 12" />
        {/* Right pauldron */}
        <path d="M19 9C19 9 22 9 22 12L19 12" />
        {/* Center ridge */}
        <line x1="12" y1="6" x2="12" y2="21" />
        {/* Pectoral curve */}
        <path d="M7 12Q12 14 17 12" />
      </svg>
    );
    case "Accessory": return (
      <svg {...p}>
        <circle cx="12" cy="12" r="7" />
        <circle cx="12" cy="12" r="3" />
        <path d="M12 2v3M12 19v3M2 12h3M19 12h3" />
      </svg>
    );
    default: return (
      <svg {...p}><circle cx="12" cy="12" r="8" strokeDasharray="4 2" /></svg>
    );
  }
}

function fullStat(raw: string): string { return STAT_FULL[raw.toLowerCase().trim()] ?? raw; }
function statFromStartCol(col: string): string | null {
  const normalized = col.toLowerCase().trim().replace(/\s+/g, " ");
  const match = normalized.match(/^(.+?)\s+start$/);
  if (!match) return null;
  const stat = fullStat(match[1]);
  return STAT_ORDER.includes(stat) ? stat : null;
}

function isStatCol(col: string): boolean { return !!STAT_FULL[col.toLowerCase().trim()]; }

function isIncCol(col: string): string | null {
  const lower = col.toLowerCase().replace(/[_\s/\-+]/g, "");
  for (const key of Object.keys(STAT_FULL)) {
    if (lower.startsWith(key) && (lower.includes("inc") || lower.includes("lvl") || lower.includes("level") || lower.includes("per") || lower.endsWith("lv"))) {
      return fullStat(key);
    }
  }
  return null;
}

// â”€â”€â”€ Google Sheets â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

const SHEET_ID = "1e5t0CMBgw2MOv1NRE-vNk3229p7dYg6yJAQ8YbhYnWk";
const GID = "123527243";
const EQUIPMENT_CACHE_KEY = "equipment-sheet-v2";
const EQUIPMENT_RANKS = ["S", "A", "B", "C", "D", "E", "F"] as const;
type CraftFilter = "All" | "Craftable" | "Not Craftable";

function slotFromEquipmentType(rawType: unknown): string {
  const type = Number(rawType);
  if (type === 11) return "Shield";
  if (type === 12) return "Armor";
  if (type === 13) return "Head";
  if (type === 14) return "Accessory";
  if (Number.isFinite(type) && type > 0) return "Weapon";
  return typeof rawType === "string" ? rawType.trim() : "";
}

const ELEMENT_ICON_BY_ATTR: Record<number, { label: string; src: string }> = {
  0: { label: "Water", src: "/website_icons/attributes/attribute_0_water.png" },
  1: { label: "Soil", src: "/website_icons/attributes/attribute_1_ground.png" },
  2: { label: "Grass", src: "/website_icons/attributes/attribute_2_grass.png" },
  3: { label: "Desert", src: "/website_icons/attributes/attribute_3_sand.png" },
  4: { label: "Rock", src: "/website_icons/attributes/attribute_4_rock.png" },
  5: { label: "Fire", src: "/website_icons/attributes/attribute_5_volcano.png" },
  6: { label: "Snow", src: "/website_icons/attributes/attribute_6_snow.png" },
  7: { label: "Swamp", src: "/website_icons/attributes/attribute_7_swamp.png" },
};
const ELEMENT_BONUS_OPTIONS = [0, 1, 2, 3, 4, 5, 6, 7] as const;

function parseElementAttribute(raw: unknown, rawType: unknown): number | null {
  const parsed = Number(raw);
  if (!Number.isFinite(parsed) || parsed < 0 || parsed > 7) return null;

  const type = Number(rawType);
  if (Number.isFinite(type)) {
    return type >= 1 && type <= 9 ? parsed : null;
  }

  // Some sheet exports provide textual slot/category values instead of numeric equip type.
  // In that format, element attributes are only valid for weapon entries.
  const resolvedSlot = slotFromEquipmentType(rawType);
  if (resolvedSlot !== "Weapon") return null;

  return parsed;
}

export interface EquipmentItem {
  uid: string;
  name: string;
  sourceName: string;
  sourceId: number | null;
  sheetSlot: string;
  baseStats: Record<string, number>;
  incStats: Record<string, number>;
  elementAttribute: number | null;
  crafterStudioLevel: number;
  crafterIntelligence: number;
}

function dedupeEquipmentItems(items: EquipmentItem[]): EquipmentItem[] {
  const seenSignatures = new Set<string>();
  return items.filter((item) => {
    const statsSignature = STAT_ORDER.map((stat) => `${item.baseStats[stat] ?? 0}:${item.incStats[stat] ?? 0}`).join("|");
    const sourceKey = item.sourceName || item.name;
    const signature = item.sourceId !== null
      ? `id:${item.sourceId}`
      : `name:${sourceKey}|slot:${item.sheetSlot}|studio:${item.crafterStudioLevel}|int:${item.crafterIntelligence}|stats:${statsSignature}`;
    if (seenSignatures.has(signature)) return false;
    seenSignatures.add(signature);
    return true;
  });
}

function isPlayerFacingEquipmentName(name: string): boolean {
  if (!RANKED_EQUIPMENT_NAME.test(name)) return false;
  const lower = name.trim().toLowerCase();
  return lower !== "bare-handed" && lower !== "no equipment";
}

function getEquipmentRank(name: string): string {
  const match = name.trim().match(/^([FSABCDE])\s*\//i);
  return match ? match[1].toUpperCase() : "";
}

function parseLocalEquipmentSnapshot(): EquipmentItem[] {
  const rows = parseCsv(equipCsv);
  if (rows.length < 4) return [];

  const statLabels = rows[1] ?? [];
  const header = rows[2] ?? [];
  const nameColIdx = header.indexOf("name");
  const typeColIdx = header.indexOf("type");
  const attributeColIdx = header.indexOf("attribute");
  const craftLvlIdx = header.indexOf("craftTermStudioLevel");
  const craftIntIdx = header.indexOf("craftTermIntelligence");

  const statColumns: Array<{ stat: string; start: number; increment: number }> = [];
  for (let index = 0; index < header.length - 1; index += 1) {
    if (header[index] !== "start" || header[index + 1] !== "increment") continue;
    const rawLabel = (statLabels[index] ?? "").trim();
    if (!rawLabel) continue;

    const normalizedStat =
      rawLabel === "Atk" ? "Attack" :
      rawLabel === "Def" ? "Defence" :
      rawLabel === "Spd" ? "Speed" :
      rawLabel === "Lck" ? "Luck" :
      rawLabel === "Int" ? "Intelligence" :
      rawLabel === "Dex" ? "Dexterity" :
      rawLabel === "Gth" ? "Gather" :
      rawLabel === "Mov" ? "Move" :
      rawLabel === "Hrt" ? "Heart" :
      rawLabel;

    if (!STAT_ORDER.includes(normalizedStat)) continue;
    statColumns.push({ stat: normalizedStat, start: index, increment: index + 1 });
  }

  const items: EquipmentItem[] = [];
  for (const [rowIndex, row] of rows.slice(3).entries()) {
    const name = String(row[nameColIdx] ?? "").trim();
    if (!name || /^\d+$/.test(name) || !isPlayerFacingEquipmentName(name)) continue;

    const rawId = Number(row[0]);
    const sourceId = Number.isFinite(rawId) ? rawId : null;
    const displayName = getVariantDisplayName(name, sourceId);
    const rawType = typeColIdx >= 0 ? row[typeColIdx] : null;
    const parsedType = rawType == null || rawType === "" ? null : Number(rawType);
    const sheetSlot = slotFromEquipmentType(Number.isFinite(parsedType as number) ? parsedType : rawType);
    const elementAttribute = parseElementAttribute(
      attributeColIdx >= 0 ? row[attributeColIdx] : null,
      Number.isFinite(parsedType as number) ? parsedType : rawType,
    );

    const baseStats: Record<string, number> = Object.fromEntries(STAT_ORDER.map((stat) => [stat, 0]));
    const incStats: Record<string, number> = Object.fromEntries(STAT_ORDER.map((stat) => [stat, 0]));

    for (const col of statColumns) {
      baseStats[col.stat] = Number(row[col.start]) || 0;
      incStats[col.stat] = Number(row[col.increment]) || 0;
    }

    items.push({
      uid: sourceId === null ? `local-${rowIndex}` : String(sourceId),
      name: displayName,
      sourceName: name,
      sourceId,
      sheetSlot,
      baseStats,
      incStats,
      elementAttribute,
      crafterStudioLevel: Number(row[craftLvlIdx]) || 0,
      crafterIntelligence: Number(row[craftIntIdx]) || 0,
    });
  }

  return dedupeEquipmentItems(items);
}

async function fetchSheet(): Promise<EquipmentItem[]> {
  const url = googleSheetUrl("equipment");
  const text = await fetch(url)
    .then((res) => {
      if (!res.ok) throw new Error(`Equipment sheet returned ${res.status}`);
      return res.text();
    })
    .catch((err) => {
      const cached = readBrowserCache<EquipmentItem[]>(EQUIPMENT_CACHE_KEY, 7 * 24 * 60 * 60 * 1000);
      if (cached) return `__KA_CACHED_EQUIPMENT__${JSON.stringify(cached)}`;
      const localSnapshot = parseLocalEquipmentSnapshot();
      if (localSnapshot.length > 0) return `__KA_CACHED_EQUIPMENT__${JSON.stringify(localSnapshot)}`;
      throw err;
    });
  if (text.startsWith("__KA_CACHED_EQUIPMENT__")) {
    const cachedItems = JSON.parse(text.replace("__KA_CACHED_EQUIPMENT__", "")) as EquipmentItem[];
    const dedupedCachedItems = dedupeEquipmentItems(cachedItems);
    writeBrowserCache(EQUIPMENT_CACHE_KEY, dedupedCachedItems);
    return dedupedCachedItems;
  }
  const json = text.replace(/^[^(]+\(/, "").replace(/\);?\s*$/, "");
  const data = JSON.parse(json);
  const rawCols: Array<{ id: string; label: string; type: string }> = data.table.cols;
  const cols = rawCols.map((c) => (c.label || c.id).trim());
  const colTypes = rawCols.map((c) => c.type);

  const nameColIdx = (() => {
    const ex = cols.findIndex((c) => /^(name|item.?name|equipment.?name|equip(ment)?)$/i.test(c));
    if (ex >= 0) return ex;
    return colTypes.findIndex((t) => t === "string");
  })();

  const slotColIdx = (() => {
    const equipmentType = cols.findIndex((c, i) => i !== nameColIdx && /^(type|equip.?type)$/i.test(c));
    if (equipmentType >= 0) return equipmentType;
    return cols.findIndex((c, i) => i !== nameColIdx && /^(slot|category|kind)$/i.test(c));
  })();
  const attributeColIdx = cols.findIndex((c) => /^(attribute|element|biome)$/i.test(c.trim()));
  const craftLvlIdx = cols.findIndex((c) => /crafterstudio|studio.?level|crafter.?studio/i.test(c));
  const craftIntIdx = cols.findIndex((c) => /craftermintelligence|crafter.?intel|craft.*int/i.test(c));

  const rawItems: Omit<EquipmentItem, "uid">[] = [];
  const statColumnRoles = cols.map((col, index): { stat: string; field: "base" | "inc" } | null => {
    const startStat = statFromStartCol(col);
    if (startStat) return { stat: startStat, field: "base" };
    const previousStartStat = index > 0 ? statFromStartCol(cols[index - 1]) : null;
    if (/^increment$/i.test(col.trim()) && previousStartStat) return { stat: previousStartStat, field: "inc" };
    if (isStatCol(col)) return { stat: fullStat(col), field: "base" };
    const incName = isIncCol(col);
    return incName ? { stat: incName, field: "inc" } : null;
  });

  for (const row of data.table.rows) {
    if (!row?.c) continue;
    const cells = row.c as Array<{ v: string | number | null } | null>;
    const get = (i: number) => (i >= 0 && i < cells.length ? cells[i]?.v ?? null : null);
    const name = String(get(nameColIdx) ?? "").trim();
    if (!name || /^\d+$/.test(name)) continue;
    if (!isPlayerFacingEquipmentName(name)) continue;
    const rawSourceId = get(0);
    const parsedSourceId = rawSourceId === null || rawSourceId === "" ? NaN : Number(rawSourceId);
    const sourceId = Number.isFinite(parsedSourceId) ? parsedSourceId : null;
    const displayName = getVariantDisplayName(name, sourceId);
    const rawSlot = slotColIdx >= 0 ? get(slotColIdx) : null;
    const sheetSlot = slotFromEquipmentType(rawSlot);
    const elementAttribute = parseElementAttribute(attributeColIdx >= 0 ? get(attributeColIdx) : null, rawSlot);
    const baseStats: Record<string, number> = {};
    const incStats: Record<string, number> = {};
    for (let i = 0; i < cols.length; i++) {
      if (i === nameColIdx || i === slotColIdx || i === craftLvlIdx || i === craftIntIdx) continue;
      const role = statColumnRoles[i];
      if (!role) continue;
      if (role.field === "base") baseStats[role.stat] = Number(get(i)) || 0;
      else incStats[role.stat] = Number(get(i)) || 0;
    }
    for (const s of STAT_ORDER) {
      if (baseStats[s] === undefined) baseStats[s] = 0;
      if (incStats[s] === undefined) incStats[s] = 0;
    }
    rawItems.push({ name: displayName, sourceName: name, sourceId, sheetSlot, baseStats, incStats, elementAttribute, crafterStudioLevel: Number(get(craftLvlIdx)) || 0, crafterIntelligence: Number(get(craftIntIdx)) || 0 });
  }

  const dedupedRawItems: Omit<EquipmentItem, "uid">[] = [];
  const seenSignatures = new Set<string>();
  for (const item of rawItems) {
    const statsSignature = STAT_ORDER.map((stat) => `${item.baseStats[stat] ?? 0}:${item.incStats[stat] ?? 0}`).join("|");
    const signature = item.sourceId !== null
      ? `id:${item.sourceId}`
      : `name:${item.name}|slot:${item.sheetSlot}|studio:${item.crafterStudioLevel}|int:${item.crafterIntelligence}|stats:${statsSignature}`;
    if (seenSignatures.has(signature)) continue;
    seenSignatures.add(signature);
    dedupedRawItems.push(item);
  }

  const duplicateCounts = new Map<string, number>();
  for (const item of dedupedRawItems) {
    duplicateCounts.set(item.name, (duplicateCounts.get(item.name) ?? 0) + 1);
  }

  const seenDuplicateIndex = new Map<string, number>();

  const items = dedupedRawItems.map((item, index) => {
    const duplicateTotal = duplicateCounts.get(item.name) ?? 0;
    if (duplicateTotal <= 1) {
      return { uid: item.sourceId === null ? String(index) : String(item.sourceId), ...item };
    }

    const seen = (seenDuplicateIndex.get(item.name) ?? 0) + 1;
    seenDuplicateIndex.set(item.name, seen);
    const preferredSuffix = item.sourceId === null ? undefined : VARIANT_NAME_BY_ID[item.sourceId]?.match(/\([^)]*\)$/)?.[0];
    const suffix = preferredSuffix ?? `(${seen})`;
    return { uid: item.sourceId === null ? String(index) : String(item.sourceId), ...item, name: `${item.name} ${suffix}` };
  });
  const dedupedItems = dedupeEquipmentItems(items);
  writeBrowserCache(EQUIPMENT_CACHE_KEY, dedupedItems);
  return dedupedItems;
}

function statAtLevel(base: number, inc: number, level: number): number {
  return Math.round(base + (level - 1) * inc);
}

// â”€â”€â”€ Shared API state â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

type StatOverrides = Record<string, { base?: number; inc?: number }>;

interface HistoryEntry {
  id: string;
  timestamp: number;
  userName: string;
  changeType: "stat" | "slot" | "equip-icon" | "stat-icon" | "weapon-type" | "weapon-category";
  itemName: string;
  description: string;
}

interface SharedState {
  overrides: Record<string, StatOverrides>;
  slotAssignments: Record<string, string>;
  equipIcons: Record<string, string>;
  statIcons: Record<string, string>;
  weaponTypes: Record<string, string>;
  weaponCategories: string[];
  history: HistoryEntry[];
}

const EMPTY_SHARED: SharedState = {
  overrides: {}, slotAssignments: {}, equipIcons: {}, statIcons: {},
  weaponTypes: {}, weaponCategories: [], history: [],
};

async function fetchShared(): Promise<SharedState> {
  try {
    const data = await fetchSharedWithFallback<SharedState>(apiUrl("/shared"));
    return { ...EMPTY_SHARED, ...data };
  } catch { return EMPTY_SHARED; }
}

type HistoryPayload = Omit<HistoryEntry, "id" | "timestamp">;

async function putShared(endpoint: string, data: unknown, history?: HistoryPayload) {
  await fetch(apiUrl(`/shared/${endpoint}`), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ data, history }),
  });
}

function useShared() {
  const qc = useQueryClient();
  const { data: shared = EMPTY_SHARED } = useQuery<SharedState>({
    queryKey: ["ka-shared"],
    queryFn: fetchShared,
    initialData: () => ({ ...EMPTY_SHARED, ...(JSON.parse(JSON.stringify(localSharedData)) as Partial<SharedState>) }),
    staleTime: 15000,
    refetchOnWindowFocus: true,
  });

  const invalidate = useCallback(() => qc.invalidateQueries({ queryKey: ["ka-shared"] }), [qc]);

  const saveOverrides = useCallback(async (data: Record<string, StatOverrides>, history?: HistoryPayload) => {
    await putShared("overrides", data, history); invalidate();
  }, [invalidate]);

  const saveSlots = useCallback(async (data: Record<string, string>, history?: HistoryPayload) => {
    await putShared("slots", data, history); invalidate();
  }, [invalidate]);

  const saveEquipIcons = useCallback(async (data: Record<string, string>, history?: HistoryPayload) => {
    await putShared("icons/equip", data, history); invalidate();
  }, [invalidate]);

  const saveStatIcons = useCallback(async (data: Record<string, string>, history?: HistoryPayload) => {
    await putShared("icons/stat", data, history); invalidate();
  }, [invalidate]);

  const saveWeaponTypes = useCallback(async (data: Record<string, string>, history?: HistoryPayload) => {
    await putShared("weapon-types", data, history); invalidate();
  }, [invalidate]);

  const saveWeaponCategories = useCallback(async (data: string[], history?: HistoryPayload) => {
    await fetch(apiUrl("/shared/weapon-categories"), {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ data, history }),
    });
    invalidate();
  }, [invalidate]);

  const renameUser = useCallback(async (oldName: string, newName: string) => {
    if (!oldName || !newName || oldName === newName) return;
    await fetch(apiUrl("/shared/rename-user"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ oldName, newName }),
    });
    invalidate();
  }, [invalidate]);

  return { shared, saveOverrides, saveSlots, saveEquipIcons, saveStatIcons, saveWeaponTypes, saveWeaponCategories, renameUser };
}

// Returns all stat names to check for a given display stat (including aliases that collapse into it)
function statNamesToCheck(stat: string): string[] {
  const extras = Object.entries(STAT_ALIAS_FOR).filter(([, v]) => v === stat).map(([k]) => k);
  return [stat, ...extras];
}

function getEffectiveStat(item: EquipmentItem, stat: string, field: "base" | "inc", overrides: Record<string, StatOverrides>): number {
  // Sum the canonical stat plus any aliases (e.g. "Speed" + "Move")
  return statNamesToCheck(stat).reduce((sum, name) => {
    const ov = overrides[item.name]?.[name]?.[field];
    if (ov !== undefined) return sum + ov;
    return sum + (field === "base" ? (item.baseStats[name] ?? 0) : (item.incStats[name] ?? 0));
  }, 0);
}

function isStatUnset(item: EquipmentItem, stat: string, overrides: Record<string, StatOverrides>): boolean {
  // Consider set if ANY of the alias names have data
  return statNamesToCheck(stat).every(
    (name) => overrides[item.name]?.[name]?.base === undefined && (item.baseStats[name] ?? 0) === 0
  );
}

function allItemStatsFilled(item: EquipmentItem, overrides: Record<string, StatOverrides>): boolean {
  return STAT_ORDER.every((stat) => !isStatUnset(item, stat, overrides));
}

// â”€â”€â”€ Username â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

function useUserName() {
  const [userName, setUserNameState] = useState(() => localStorage.getItem("ka_username") ?? "");
  const setUserName = (name: string) => {
    setUserNameState(name);
    localStorage.setItem("ka_username", name);
  };
  return { userName, setUserName };
}

// â”€â”€â”€ Name prompt dialog â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

function NamePromptDialog({ open, currentName, onSave, onCancel }: {
  open: boolean; currentName?: string;
  onSave: (name: string) => void; onCancel?: () => void;
}) {
  const isRename = !!currentName;
  const [draft, setDraft] = useState(currentName ?? "");
  useEffect(() => { setDraft(currentName ?? ""); }, [currentName, open]);
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const n = draft.trim();
    if (n) onSave(n);
  };
  return (
    <Dialog open={open}>
      <DialogContent className="max-w-sm" onPointerDownOutside={(e) => { if (isRename && onCancel) onCancel(); else e.preventDefault(); }}>
        <DialogHeader>
          <DialogTitle>{isRename ? "Change your display name" : "Who's making this change?"}</DialogTitle>
        </DialogHeader>
        <p className="text-sm text-muted-foreground">
          {isRename
            ? `All your past changes recorded as "${currentName}" will be updated to the new name automatically.`
            : "Your name will be shown in the change history so everyone knows who updated what."}
        </p>
        <form onSubmit={submit} className="flex gap-2 mt-2">
          <Input autoFocus placeholder="Your name..." value={draft} onChange={(e) => setDraft(e.target.value)} className="flex-1" />
          <Button type="submit" disabled={!draft.trim()}>{isRename ? "Rename" : "Continue"}</Button>
        </form>
      </DialogContent>
    </Dialog>
  );
}

// â”€â”€â”€ Icon upload â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

function fileToDataUrl(file: File): Promise<string> {
  return new Promise((res, rej) => { const r = new FileReader(); r.onload = () => res(r.result as string); r.onerror = rej; r.readAsDataURL(file); });
}

function IconUpload({ iconKey, icons, onSave, size = 28 }: {
  iconKey: string; icons: Record<string, string>;
  onSave: (icons: Record<string, string>) => void; size?: number;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [editMode, setEditMode] = useState(false);
  const { userName } = useUserName();
  const existing = iconKey.startsWith("equip:")
    ? getEquipmentIcon(icons, iconKey.slice("equip:".length))
    : icons[iconKey];

  // Only allow removal if the icon was uploaded by this user (by convention, store uploader in localStorage)
  const canRemove = (() => {
    // Store uploaded icons with a key: "ka_uploaded_icon:{iconKey}" = userName
    if (!userName) return false;
    try {
      const uploader = localStorage.getItem(`ka_uploaded_icon:${iconKey}`);
      return uploader === userName;
    } catch { return false; }
  })();

  const handleFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]; if (!file) return;
    onSave({ ...icons, [iconKey]: await fileToDataUrl(file) }); e.target.value = "";
    // Mark uploader in localStorage
    if (userName) localStorage.setItem(`ka_uploaded_icon:${iconKey}`, userName);
    setEditMode(false);
  };
  const handleRemove = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!canRemove) return;
    const next = { ...icons };
    if (iconKey.startsWith("equip:")) {
      for (const key of getEquipmentIconKeys(iconKey.slice("equip:".length))) delete next[key];
    } else {
      delete next[iconKey];
    }
    onSave(next);
    // Remove uploader mark
    localStorage.removeItem(`ka_uploaded_icon:${iconKey}`);
    setEditMode(false);
  };
  return (
    <div className="group relative inline-flex items-center justify-center shrink-0" style={{ width: size, height: size }}>
      {existing ? (
        <>
          <img src={existing} alt="" className="rounded object-contain w-full h-full" />
          {editMode ? (
            <>
              {canRemove && (
                <button onClick={handleRemove} className="absolute -top-1 -right-1 w-3.5 h-3.5 bg-destructive text-destructive-foreground rounded-full flex items-center justify-center z-10" title="Remove icon"><X className="w-2 h-2" /></button>
              )}
              <button onClick={(e) => { e.stopPropagation(); inputRef.current?.click(); }} className="absolute inset-0 rounded bg-black/30 flex items-center justify-center" title="Replace icon">
                <ImageIcon className="w-3 h-3 text-white" />
              </button>
              <button onClick={() => setEditMode(false)} className="absolute -bottom-1 -right-1 w-3 h-3 bg-muted border border-border rounded-full flex items-center justify-center z-10 text-muted-foreground hover:text-foreground" title="Done editing"><X className="w-2 h-2" /></button>
            </>
          ) : (
            <button onClick={(e) => { e.stopPropagation(); setEditMode(true); }} className="absolute -top-1 -right-1 w-3.5 h-3.5 bg-background border border-border rounded-full flex items-center justify-center opacity-0 group-hover:opacity-100 hover:!opacity-100 transition-opacity z-10" title="Edit icon">
              <Pencil className="w-2 h-2 text-muted-foreground" />
            </button>
          )}
        </>
      ) : (
        <button onClick={() => inputRef.current?.click()} className="w-full h-full flex items-center justify-center rounded border border-dashed border-border hover:border-primary/50 hover:bg-muted/30 transition-colors text-muted-foreground" title="Upload icon">
          <ImageIcon className="w-3 h-3" />
        </button>
      )}
      <input ref={inputRef} type="file" accept="image/*" className="hidden" onChange={handleFile} />
    </div>
  );
}

// â”€â”€â”€ Relative time â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

function relTime(ts: number): string {
  const diff = Date.now() - ts;
  if (diff < 60_000) return "just now";
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
  return new Date(ts).toLocaleDateString();
}

const CHANGE_COLORS: Record<string, string> = {
  stat: "bg-blue-500",
  slot: "bg-violet-500",
  "equip-icon": "bg-amber-500",
  "stat-icon": "bg-emerald-500",
  "weapon-type": "bg-rose-500",
  "weapon-category": "bg-orange-500",
};

// â”€â”€â”€ Character builder â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

interface SlotEntry { itemName: string; level: number; }
type LoadoutSlot = Record<CharSlot, SlotEntry>;
interface LoadoutStateItem { id: string; label: string; slots: LoadoutSlot; }
type LoadoutState = LoadoutStateItem[];

function createLoadout(label: string, baseSlots?: Partial<LoadoutSlot>): LoadoutStateItem {
  const slots: LoadoutSlot = {
    Head: { itemName: "", level: 1 },
    Weapon: { itemName: "", level: 1 },
    Shield: { itemName: "", level: 1 },
    Armor: { itemName: "", level: 1 },
    Accessory: { itemName: "", level: 1 },
  };
  if (baseSlots) {
    for (const slot of CHAR_SLOTS) {
      if (baseSlots[slot]) {
        slots[slot] = { ...slots[slot], ...baseSlots[slot] };
      }
    }
  }
  return {
    id: `loadout-${label.toLowerCase().replace(/[^a-z0-9]+/g, "-")}-${Math.random().toString(36).slice(2, 8)}`,
    label,
    slots,
  };
}

function normalizeSavedLoadout(value: unknown): LoadoutState {
  if (Array.isArray(value)) {
    return value.map((loadout, index) => {
      if (loadout && typeof loadout === "object") {
        const maybe = loadout as Partial<LoadoutStateItem>;
        const slots: LoadoutSlot = {
          Head: { itemName: "", level: 1 },
          Weapon: { itemName: "", level: 1 },
          Shield: { itemName: "", level: 1 },
          Armor: { itemName: "", level: 1 },
          Accessory: { itemName: "", level: 1 },
        };
        if (maybe.slots && typeof maybe.slots === "object") {
          for (const slot of CHAR_SLOTS) {
            const entry = (maybe.slots as any)[slot];
            if (entry && typeof entry === "object") {
              slots[slot] = {
                itemName: typeof entry.itemName === "string" ? entry.itemName : "",
                level: Number.isFinite(entry.level as number) ? Number(entry.level) : 1,
              };
            }
          }
        }
        return {
          id: typeof maybe.id === "string" && maybe.id ? maybe.id : `loadout-${index + 1}-${Math.random().toString(36).slice(2, 8)}`,
          label: typeof maybe.label === "string" && maybe.label ? maybe.label : `Loadout ${index + 1}`,
          slots,
        };
      }
      return createLoadout(`Loadout ${index + 1}`);
    });
  }
  if (value && typeof value === "object") {
    const record = value as Record<string, any>;
    const slots: LoadoutSlot = {
      Head: { itemName: "", level: 1 },
      Weapon: { itemName: "", level: 1 },
      Shield: { itemName: "", level: 1 },
      Armor: { itemName: "", level: 1 },
      Accessory: { itemName: "", level: 1 },
    };
    for (const slot of CHAR_SLOTS) {
      const entry = record[slot];
      if (entry && typeof entry === "object") {
        slots[slot] = {
          itemName: typeof entry.itemName === "string" ? entry.itemName : "",
          level: Number.isFinite(entry.level as number) ? Number(entry.level) : 1,
        };
      }
    }
    return [createLoadout("Loadout 1", slots)];
  }
  return [createLoadout("Loadout 1")];
}

const DEFAULT_LOADOUT: LoadoutState = [createLoadout("Loadout 1")];

// â”€â”€â”€ Info dialog â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

function InfoDialog() {
  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button variant="outline" size="icon" className="h-8 w-8"><Info className="w-4 h-4" /></Button>
      </DialogTrigger>
      <DialogContent className="max-w-lg">
        <DialogHeader><DialogTitle>How to use Equipment Stats</DialogTitle></DialogHeader>
        <div className="space-y-3 text-sm text-muted-foreground leading-relaxed">
          <div><h3 className="font-semibold text-foreground mb-1">Comparing specific items</h3><p>Check the checkbox on any rows you want to compare, then click <strong>Compare</strong> to show only those items side by side. Click <strong>Show all</strong> to go back.</p></div>
          <div><h3 className="font-semibold text-foreground mb-1">Viewing base and growth</h3><p>Click an item name to expand its reference view. Each stat shows <code className="bg-muted px-1 rounded text-xs">Base</code>, <code className="bg-muted px-1 rounded text-xs">+/Lv</code>, and the calculated value at the current level.</p></div>
          <div><h3 className="font-semibold text-foreground mb-1">Fixed values</h3><p>Equipment slots, weapon types, and stat growth are treated as game data here, so they are view-only. The level inputs are still interactive so you can compare items at any level.</p></div>
          <div><h3 className="font-semibold text-foreground mb-1">What's personal</h3><p>Your Equipment Builder loadout and dark/light preference are saved only on this device.</p></div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

// â”€â”€â”€ Item stat history dialog â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

function ItemStatHistoryDialog({ itemName, history, onClose }: {
  itemName: string | null; history: HistoryEntry[]; onClose: () => void;
}) {
  const entries = useMemo(
    () => history.filter((e) => e.itemName === itemName && e.changeType === "stat"),
    [itemName, history]
  );
  return (
    <Dialog open={!!itemName} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Clock className="w-4 h-4 text-muted-foreground" />
            Stat edit history - {itemName}
          </DialogTitle>
        </DialogHeader>
        {entries.length === 0 ? (
          <p className="text-sm text-muted-foreground text-center py-6">No stat edits recorded for this item yet.</p>
        ) : (
          <div className="space-y-2 max-h-80 overflow-y-auto pr-1">
            {entries.map((e) => (
              <div key={e.id} className="flex items-start gap-2.5 text-sm">
                <div className="mt-0.5 w-6 h-6 rounded-full bg-blue-500 flex items-center justify-center text-white text-[10px] font-bold shrink-0">
                  {e.userName.charAt(0).toUpperCase()}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-baseline gap-1.5 flex-wrap">
                    <span className="font-medium text-foreground">{e.userName}</span>
                    <span className="text-[10px] text-muted-foreground" title={new Date(e.timestamp).toLocaleString()}>{relTime(e.timestamp)}</span>
                  </div>
                  <p className="text-xs text-muted-foreground mt-0.5">{e.description}</p>
                </div>
              </div>
            ))}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

// â”€â”€â”€ Weapon categories manager dialog â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

function WeaponCategoriesDialog({ open, categories, onClose, onSave }: {
  open: boolean; categories: string[]; onClose: () => void;
  onSave: (cats: string[]) => void;
}) {
  const [draft, setDraft] = useState<string[]>([]);
  const [newCat, setNewCat] = useState("");
  useEffect(() => { setDraft([...categories]); }, [categories, open]);

  const addCat = (e: React.FormEvent) => {
    e.preventDefault();
    const t = newCat.trim();
    if (t && !draft.includes(t)) { setDraft((d) => [...d, t].sort()); setNewCat(""); }
  };
  const removeCat = (cat: string) => setDraft((d) => d.filter((c) => c !== cat));

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Settings2 className="w-4 h-4 text-muted-foreground" />
            Weapon Type Categories
          </DialogTitle>
        </DialogHeader>
        <p className="text-xs text-muted-foreground">These categories appear in the weapon type dropdown when editing a weapon. Changes are shared with everyone.</p>
        <div className="space-y-1.5 max-h-48 overflow-y-auto mt-1">
          {draft.length === 0 && <p className="text-xs text-muted-foreground text-center py-3">No categories yet - add one below.</p>}
          {draft.map((cat) => (
            <div key={cat} className="flex items-center justify-between text-sm rounded px-2 py-1 bg-muted/40">
              <span>{cat}</span>
              <button onClick={() => removeCat(cat)} className="text-muted-foreground hover:text-destructive"><X className="w-3.5 h-3.5" /></button>
            </div>
          ))}
        </div>
        <form onSubmit={addCat} className="flex gap-2 mt-1">
          <Input autoFocus placeholder="New category..." value={newCat} onChange={(e) => setNewCat(e.target.value)} className="flex-1 h-8 text-sm" />
          <Button type="submit" size="sm" className="h-8" disabled={!newCat.trim() || draft.includes(newCat.trim())}>
            <Plus className="w-3.5 h-3.5" />Add
          </Button>
        </form>
        <div className="flex justify-end gap-2 mt-2">
          <Button variant="outline" size="sm" onClick={onClose}>Cancel</Button>
          <Button size="sm" onClick={() => { onSave(draft); onClose(); }}>Save</Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

// â”€â”€â”€ Dark mode â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

function initDark() {
  const saved = localStorage.getItem("theme");
  const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  const isDark = saved ? saved === "dark" : prefersDark;
  document.documentElement.classList.toggle("dark", isDark);
  return isDark;
}

// â”€â”€â”€ Export â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

function exportData(shared: SharedState) {
  const payload = { slotAssignments: shared.slotAssignments, overrides: shared.overrides };
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = "ka-equipment-data.json"; a.click();
  URL.revokeObjectURL(url);
}

// â”€â”€â”€ Main component â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

export default function EquipmentPage() {
  const locationSearch = useSearch();
  const { data: items = [], isLoading, isError, refetch, dataUpdatedAt } = useQuery({
    queryKey: ["equipment"],
    queryFn: fetchSheet,
    initialData: () => {
      const cached = readBrowserCache<EquipmentItem[]>(EQUIPMENT_CACHE_KEY, 7 * 24 * 60 * 60 * 1000);
      if (!cached) {
        const localSnapshot = parseLocalEquipmentSnapshot();
        if (localSnapshot.length > 0) return localSnapshot;
        return undefined;
      }
      const dedupedCached = dedupeEquipmentItems(cached);
      if (dedupedCached.length !== cached.length) writeBrowserCache(EQUIPMENT_CACHE_KEY, dedupedCached);
      return dedupedCached;
    },
    initialDataUpdatedAt: 0,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  });
  const { shared, saveOverrides, saveSlots, saveEquipIcons, saveStatIcons, saveWeaponTypes, saveWeaponCategories, renameUser } = useShared();
  const { overrides, slotAssignments, equipIcons, statIcons } = shared;
  const weaponTypes = shared.weaponTypes ?? {};
  const weaponCategories = shared.weaponCategories ?? [];
  const history = shared.history ?? [];

  // Dialogs
  const [itemHistoryName, setItemHistoryName] = useState<string | null>(null);
  const [showCategoriesDialog, setShowCategoriesDialog] = useState(false);

  const { userName, setUserName } = useUserName();
  const [namePromptOpen, setNamePromptOpen] = useState(false);
  const [renameMode, setRenameMode] = useState(false);
  const pendingAction = useRef<(() => Promise<void>) | null>(null);

  // Before any change, ensure we have a username
  const withName = useCallback((action: () => Promise<void>) => {
    if (userName) { action(); return; }
    pendingAction.current = action;
    setRenameMode(false);
    setNamePromptOpen(true);
  }, [userName]);

  const openRenameDialog = () => { setRenameMode(true); setNamePromptOpen(true); };

  const onNameSaved = async (name: string) => {
    if (renameMode && userName && userName !== name) {
      await renameUser(userName, name);
    }
    setUserName(name);
    setNamePromptOpen(false);
    setRenameMode(false);
    if (pendingAction.current) { pendingAction.current(); pendingAction.current = null; }
  };

  // Per-item levels (local, comparison table only)
  const [itemLevels, setItemLevels] = useState<Record<string, number>>({});
  const [bulkCompareLevel, setBulkCompareLevel] = useState(99);
  const getItemLevel = (uid: string) => itemLevels[uid] ?? 1;
  const setItemLevel = (uid: string, v: number) => setItemLevels((prev) => ({ ...prev, [uid]: Math.min(99, Math.max(1, v)) }));

  const [expandedItem, setExpandedItem] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [slotFilters, setSlotFilters] = useState<Set<string>>(new Set());
  const [rankFilters, setRankFilters] = useState<Set<string>>(new Set());
  const [excludeStatFilters, setExcludeStatFilters] = useState<Set<string>>(new Set());
  const [hideExcludedStatColumns, setHideExcludedStatColumns] = useState(false);
  const [studioFilters, setStudioFilters] = useState<Set<number>>(new Set());
  const [intFilters, setIntFilters] = useState<Set<number>>(new Set());
  const [craftFilter, setCraftFilter] = useState<CraftFilter>("All");
  const [sortCol, setSortCol] = useState<string | null>(null);
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
  const [elementSortFirst, setElementSortFirst] = useState<number | null>(null);
  const [elementSortMenuOpen, setElementSortMenuOpen] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const [showFilters, setShowFilters] = useState(false);
  const [openFilterMenu, setOpenFilterMenu] = useState<string | null>(null);
  const filterMenuRef = useRef<HTMLDivElement>(null);
  const mobileElementSortMenuRef = useRef<HTMLDivElement>(null);
  const desktopElementSortMenuRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const params = new URLSearchParams(locationSearch);
    const nextSearch = params.get("search");
    if (nextSearch !== null) setSearch(nextSearch);
  }, [locationSearch]);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (filterMenuRef.current && !filterMenuRef.current.contains(e.target as Node)) setOpenFilterMenu(null);
      const insideMobileMenu = mobileElementSortMenuRef.current?.contains(e.target as Node) ?? false;
      const insideDesktopMenu = desktopElementSortMenuRef.current?.contains(e.target as Node) ?? false;
      if (!insideMobileMenu && !insideDesktopMenu) setElementSortMenuOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  useEffect(() => {
    if (sortCol !== "element") setElementSortMenuOpen(false);
  }, [sortCol]);

  // Comparison checkboxes
  const [selectedUids, setSelectedUids] = useState<Set<string>>(new Set());
  const [compareMode, setCompareMode] = useState(false);
  const [favs, setFavs] = useState<Set<string>>(() => {
    try {
      const raw = localStorage.getItem("ka_fav_equipment");
      return raw ? new Set(JSON.parse(raw)) : new Set();
    } catch {
      return new Set();
    }
  });
  const [favsOnly, setFavsOnly] = useState(false);

  useEffect(() => {
    try {
      localStorage.setItem("ka_fav_equipment", JSON.stringify([...favs]));
    } catch {
      // ignore write errors
    }
  }, [favs]);

  const toggleSelect = (uid: string) =>
    setSelectedUids((prev) => { const next = new Set(prev); next.has(uid) ? next.delete(uid) : next.add(uid); return next; });
  const leaveCompareMode = () => setCompareMode(false);
  const clearSelection = () => { setSelectedUids(new Set()); setCompareMode(false); };
  const toggleFav = (uid: string) => setFavs((prev) => { const next = new Set(prev); next.has(uid) ? next.delete(uid) : next.add(uid); return next; });

  // Character builder - personal
  const [loadout, setLoadout] = useState<LoadoutState>(() => {
    try {
      const raw = JSON.parse(localStorage.getItem("ka_loadout") ?? "null");
      return normalizeSavedLoadout(raw);
    } catch {
      return DEFAULT_LOADOUT;
    }
  });
  const setLoadoutEntry = (loadoutId: string, slotType: CharSlot, entry: Partial<SlotEntry>) =>
    setLoadout((prev) => {
      const next = prev.map((loadoutItem) => {
        if (loadoutItem.id !== loadoutId) return loadoutItem;
        return {
          ...loadoutItem,
          slots: {
            ...loadoutItem.slots,
            [slotType]: { ...loadoutItem.slots[slotType], ...entry },
          },
        };
      });
      localStorage.setItem("ka_loadout", JSON.stringify(next));
      return next;
    });

  const addLoadout = (afterId?: string) => {
    setLoadout((prev) => {
      const next = [...prev];
      const label = `Loadout ${prev.length + 1}`;
      const newLoadout = createLoadout(label);
      if (!afterId) next.push(newLoadout);
      else {
        const index = next.findIndex((item) => item.id === afterId);
        next.splice(index < 0 ? next.length : index + 1, 0, newLoadout);
      }
      localStorage.setItem("ka_loadout", JSON.stringify(next));
      return next;
    });
  };

  const duplicateLoadout = (loadoutId: string) => {
    setLoadout((prev) => {
      const index = prev.findIndex((item) => item.id === loadoutId);
      if (index === -1) return prev;
      const source = prev[index];
      const cloned = createLoadout(`${source.label} copy`, source.slots);
      const next = [...prev];
      next.splice(index + 1, 0, cloned);
      localStorage.setItem("ka_loadout", JSON.stringify(next));
      return next;
    });
  };

  const removeLoadout = (loadoutId: string) => {
    setLoadout((prev) => {
      if (prev.length <= 1) return prev;
      const next = prev.filter((item) => item.id !== loadoutId);
      localStorage.setItem("ka_loadout", JSON.stringify(next));
      return next;
    });
  };

  const [editingLoadoutId, setEditingLoadoutId] = useState<string | null>(null);
  const [editingLoadoutLabel, setEditingLoadoutLabel] = useState("");

  const renameLoadout = (loadoutId: string, label: string) => {
    setLoadout((prev) => {
      const next = prev.map((item) => item.id === loadoutId ? { ...item, label } : item);
      localStorage.setItem("ka_loadout", JSON.stringify(next));
      return next;
    });
  };

  const startRenameLoadout = (loadoutId: string) => {
    const target = loadout.find((item) => item.id === loadoutId);
    setEditingLoadoutId(loadoutId);
    setEditingLoadoutLabel(target ? target.label : "");
  };

  const saveRenameLoadout = () => {
    if (!editingLoadoutId) return;
    renameLoadout(editingLoadoutId, editingLoadoutLabel.trim() || `Loadout ${loadout.findIndex((item) => item.id === editingLoadoutId) + 1}`);
    setEditingLoadoutId(null);
    setEditingLoadoutLabel("");
  };

  const cancelRenameLoadout = () => {
    setEditingLoadoutId(null);
    setEditingLoadoutLabel("");
  };

  // Drag-and-drop state for character builder
  const [dragOverSlot, setDragOverSlot] = useState<string | null>(null);
  const draggingItemRef = useRef<{ name: string; slot: string } | null>(null);

  // Slot helpers
  const getItemSlot = useCallback((name: string) => {
    const assigned = slotAssignments[name];
    if (assigned && assigned !== " ") return assigned;
    const item = items.find((entry) => entry.name === name);
    return CHAR_SLOTS.includes(item?.sheetSlot as CharSlot) ? item!.sheetSlot : " ";
  }, [slotAssignments, items]);

  const setItemSlot = useCallback((name: string, slot: string) => {
    withName(async () => {
      const next = { ...slotAssignments, [name]: slot };
      await saveSlots(next, { userName, changeType: "slot", itemName: name, description: `Assigned "${name}" to slot: ${slot}` });
    });
  }, [slotAssignments, saveSlots, withName, userName]);

  const setWeaponType = useCallback((name: string, category: string) => {
    withName(async () => {
      const next = { ...weaponTypes, [name]: category };
      await saveWeaponTypes(next, { userName, changeType: "weapon-type", itemName: name, description: `Set weapon type of "${name}" to: ${category || "- unset"}` });
    });
  }, [weaponTypes, saveWeaponTypes, withName, userName]);

  const saveCategories = useCallback((cats: string[]) => {
    withName(async () => {
      await saveWeaponCategories(cats, { userName, changeType: "weapon-category", itemName: "-", description: `Updated weapon category list (${cats.length} categories)` });
    });
  }, [saveWeaponCategories, withName, userName]);

  const setOverride = useCallback((itemName: string, stat: string, field: "base" | "inc", value: number) => {
    withName(async () => {
      const next = { ...overrides, [itemName]: { ...(overrides[itemName] ?? {}), [stat]: { ...(overrides[itemName]?.[stat] ?? {}), [field]: value } } };
      await saveOverrides(next, { userName, changeType: "stat", itemName, description: `Updated ${itemName} - ${stat} ${field === "base" ? "base" : "+/Lv"}: ${value}` });
    });
  }, [overrides, saveOverrides, withName, userName]);

  const handleEquipIcons = useCallback((icons: Record<string, string>, itemName?: string) => {
    withName(async () => {
      await saveEquipIcons(icons, itemName ? { userName, changeType: "equip-icon", itemName, description: `Updated icon for ${itemName}` } : undefined);
    });
  }, [saveEquipIcons, withName, userName]);

  const handleStatIcons = useCallback((icons: Record<string, string>, stat?: string) => {
    withName(async () => {
      await saveStatIcons(icons, stat ? { userName, changeType: "stat-icon", itemName: stat, description: `Updated stat icon for ${stat}` } : undefined);
    });
  }, [saveStatIcons, withName, userName]);

  const [sortByInc, setSortByInc] = useState(false);
  const [rowsToShow, setRowsToShow] = useState<"25" | "50" | "100" | "all">("25");
  const [activeElementBonuses, setActiveElementBonuses] = useState<Set<number>>(new Set());

  const allStudioLevels = useMemo(
    () => [...new Set(items.filter((i) => i.crafterStudioLevel > 0).map((i) => i.crafterStudioLevel))].sort((a, b) => a - b),
    [items]
  );
  const availableIntValues = useMemo(() => {
    const base = studioFilters.size > 0 ? items.filter((i) => studioFilters.has(i.crafterStudioLevel)) : items;
    return [...new Set(base.map((i) => i.crafterIntelligence).filter((v) => v > 0))].sort((a, b) => a - b);
  }, [items, studioFilters]);

  const anyFilterActive = slotFilters.size > 0 || rankFilters.size > 0 || excludeStatFilters.size > 0 || studioFilters.size > 0 || intFilters.size > 0 || craftFilter !== "All" || search.length > 0 || favsOnly;
  const clearAllFilters = useCallback(() => {
    setSlotFilters(new Set());
    setRankFilters(new Set());
    setExcludeStatFilters(new Set());
    setStudioFilters(new Set());
    setIntFilters(new Set());
    setCraftFilter("All");
    setFavsOnly(false);
    setSearch("");
  }, []);

  const handleSort = (col: string) => {
    if (sortCol === col) setSortDir((d) => d === "asc" ? "desc" : "asc");
    else { setSortCol(col); setSortDir("desc"); }
  };

  const toggleElementBonus = useCallback((attr: number) => {
    setActiveElementBonuses((prev) => {
      const next = new Set(prev);
      if (next.has(attr)) next.delete(attr);
      else next.add(attr);
      return next;
    });
  }, []);

  const getItemElementMultiplier = useCallback((item: EquipmentItem) => {
    if (item.elementAttribute === null) return 1;
    return activeElementBonuses.has(item.elementAttribute) ? 1.5 : 1;
  }, [activeElementBonuses]);

  const applyElementBonus = useCallback((value: number, item: EquipmentItem) => {
    return Math.round(value * getItemElementMultiplier(item));
  }, [getItemElementMultiplier]);

  const getItemStatAtLevel = useCallback((item: EquipmentItem, stat: string, level: number) => {
    const base = getEffectiveStat(item, stat, "base", overrides);
    const inc = getEffectiveStat(item, stat, "inc", overrides);
    return applyElementBonus(statAtLevel(base, inc, level), item);
  }, [overrides, applyElementBonus]);

  const getItemIncStatVal = useCallback((item: EquipmentItem, stat: string) => {
    const inc = getEffectiveStat(item, stat, "inc", overrides);
    return applyElementBonus(inc, item);
  }, [overrides, applyElementBonus]);

  const getItemStatVal = useCallback((item: EquipmentItem, stat: string) => {
    return getItemStatAtLevel(item, stat, getItemLevel(item.uid));
  }, [getItemStatAtLevel, itemLevels]);

  const filtered = useMemo(() => {
    let list = compareMode ? items.filter((i) => selectedUids.has(i.uid)) : items;
    if (search) list = list.filter((i) => matchesLooseSearch(i.name, search));
    if (slotFilters.size > 0) {
      list = list.filter((i) => {
        const a = getItemSlot(i.name);
        const s = a !== " " ? a : i.sheetSlot;
        return slotFilters.has(s);
      });
    }
    if (rankFilters.size > 0) {
      list = list.filter((i) => rankFilters.has(getEquipmentRank(i.name)));
    }
    if (excludeStatFilters.size > 0) {
      list = list.filter((i) => {
        for (const stat of excludeStatFilters) {
          const base = getEffectiveStat(i, stat, "base", overrides);
          const inc = getEffectiveStat(i, stat, "inc", overrides);
          if (base !== 0 || inc !== 0) return false;
        }
        return true;
      });
    }
    if (studioFilters.size > 0) {
      list = list.filter((i) => studioFilters.has(i.crafterStudioLevel));
    }
    if (intFilters.size > 0) {
      list = list.filter((i) => intFilters.has(i.crafterIntelligence));
    }
    if (craftFilter !== "All") {
      list = list.filter((i) => craftFilter === "Craftable" ? i.crafterStudioLevel > 0 : i.crafterStudioLevel === 0);
    }
    if (favsOnly) {
      list = list.filter((i) => favs.has(i.uid));
    }
    if (sortCol) {
      list = [...list].sort((a, b) => {
        let av: number | string, bv: number | string;
        if (sortCol === "name") { av = a.name; bv = b.name; }
        else if (sortCol === "element") {
          const aAttr = a.elementAttribute;
          const bAttr = b.elementAttribute;

          if (elementSortFirst !== null) {
            const aIsPreferred = aAttr === elementSortFirst;
            const bIsPreferred = bAttr === elementSortFirst;
            if (aIsPreferred !== bIsPreferred) return aIsPreferred ? -1 : 1;
          }

          if (aAttr === null && bAttr !== null) return 1;
          if (bAttr === null && aAttr !== null) return -1;

          av = aAttr === null ? 99 : aAttr;
          bv = bAttr === null ? 99 : bAttr;
        }
        else if (sortCol === "studioLevel") { av = a.crafterStudioLevel; bv = b.crafterStudioLevel; }
        else if (sortCol === "intReq") { av = a.crafterIntelligence; bv = b.crafterIntelligence; }
        else if (sortCol === "slot") { av = getItemSlot(a.name); bv = getItemSlot(b.name); }
        else {
          av = sortByInc ? getItemIncStatVal(a, sortCol) : getItemStatVal(a, sortCol);
          bv = sortByInc ? getItemIncStatVal(b, sortCol) : getItemStatVal(b, sortCol);
        }
        if (typeof av === "string" && typeof bv === "string") return sortDir === "asc" ? av.localeCompare(bv) : bv.localeCompare(av);
        return sortDir === "asc" ? Number(av) - Number(bv) : Number(bv) - Number(av);
      });
    } else {
      list = [...list].sort(compareEquipmentOriginalOrder);
    }
    return list;
  }, [items, compareMode, selectedUids, search, slotFilters, rankFilters, excludeStatFilters, studioFilters, intFilters, craftFilter, favsOnly, favs, sortCol, sortDir, sortByInc, overrides, getItemStatVal, getItemIncStatVal, getItemSlot, elementSortFirst]);

  const visibleItems = useMemo(() => {
    if (rowsToShow === "all") return filtered;
    return filtered.slice(0, Number(rowsToShow));
  }, [filtered, rowsToShow]);

  const applyLevelToShown = useCallback((level: number) => {
    const clamped = Math.min(99, Math.max(1, level));
    setItemLevels((prev) => {
      const next = { ...prev };
      for (const item of visibleItems) next[item.uid] = clamped;
      return next;
    });
  }, [visibleItems]);

  const applyLevelToAll = useCallback((level: number) => {
    const clamped = Math.min(99, Math.max(1, level));
    setItemLevels((prev) => {
      const next = { ...prev };
      for (const item of items) next[item.uid] = clamped;
      return next;
    });
  }, [items]);

  const loadoutTotals = useMemo(() => {
    return loadout.map((loadoutItem) => {
      const totals: Record<string, number> = {};
      for (const s of STAT_ORDER) totals[s] = 0;
      for (const slot of CHAR_SLOTS) {
        const entry = loadoutItem.slots[slot];
        if (!entry.itemName) continue;
        const item = items.find((i) => i.name === entry.itemName);
        if (!item) continue;
        for (const s of STAT_ORDER) {
          totals[s] += getItemStatAtLevel(item, s, entry.level);
        }
      }
      return { id: loadoutItem.id, label: loadoutItem.label, totals };
    });
  }, [loadout, items, getItemStatAtLevel]);

  const SortIcon = ({ col }: { col: string }) => {
    if (sortCol !== col) return <ArrowUpDown className="w-3 h-3 text-muted-foreground/40 shrink-0" />;
    return sortDir === "asc" ? <ArrowUp className="w-3 h-3 text-primary shrink-0" /> : <ArrowDown className="w-3 h-3 text-primary shrink-0" />;
  };

  const visibleStats = useMemo(() => {
    if (!hideExcludedStatColumns || excludeStatFilters.size === 0) return STAT_ORDER;
    return STAT_ORDER.filter((stat) => !excludeStatFilters.has(stat));
  }, [hideExcludedStatColumns, excludeStatFilters]);

  const colCount = 10 + visibleStats.length; // drag, checkbox, favorite, icon, name, element, level, slot, craftable, ...stats

  useEffect(() => {
    if (!sortCol) return;
    if (!hideExcludedStatColumns) return;
    if (excludeStatFilters.size === 0) return;
    if (STAT_ORDER.includes(sortCol) && !visibleStats.includes(sortCol)) {
      setSortCol(null);
    }
  }, [sortCol, hideExcludedStatColumns, excludeStatFilters, visibleStats]);

  // Most recent "stat" (base/inc) history entry per item - for the Last Edit column
  const lastStatEdit = useMemo<Record<string, HistoryEntry>>(() => {
    const map: Record<string, HistoryEntry> = {};
    for (const e of history) {
      if (e.changeType === "stat" && !map[e.itemName]) map[e.itemName] = e;
    }
    return map;
  }, [history]);

  // Clear loadout entry if slot mismatch
  useEffect(() => {
    if (!items.length || !Object.keys(slotAssignments).length) return;
    let changed = false;
    const next = loadout.map((loadoutItem) => {
      const newSlots: LoadoutSlot = { ...loadoutItem.slots };
      for (const slot of CHAR_SLOTS) {
        const entry = newSlots[slot];
        if (!entry.itemName) continue;
        const assigned = slotAssignments[entry.itemName];
        if (assigned && assigned !== "-" && assigned !== slot) {
          changed = true;
          newSlots[slot] = { itemName: "", level: 1 };
        }
      }
      return changed ? { ...loadoutItem, slots: newSlots } : loadoutItem;
    });
    if (changed) { setLoadout(next); localStorage.setItem("ka_loadout", JSON.stringify(next)); }
  }, [slotAssignments, items, loadout]);

  return (
    <div className="min-h-screen bg-background transition-colors">
      <NamePromptDialog
        open={namePromptOpen}
        currentName={renameMode ? userName : undefined}
        onSave={onNameSaved}
        onCancel={() => { setNamePromptOpen(false); setRenameMode(false); }}
      />
      <ItemStatHistoryDialog
        itemName={itemHistoryName}
        history={history}
        onClose={() => setItemHistoryName(null)}
      />
      <WeaponCategoriesDialog
        open={showCategoriesDialog}
        categories={weaponCategories}
        onClose={() => setShowCategoriesDialog(false)}
        onSave={saveCategories}
      />

      <div className="max-w-[1400px] mx-auto px-4 py-8">

        <PageHeader title="Equipment Stats" actions={<InfoDialog />} className="mb-6">
          <p>
            Search and compare Kingdom Adventurers equipment at any level, including weapons, shields,
            armor, headgear, accessories, ranks, stats, shop data, crafting requirements, and loadout values.
          </p>
        </PageHeader>

        <Card className="shadow-sm mb-4">
          <CardContent className="py-3">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs font-medium text-muted-foreground whitespace-nowrap">Element bonus</span>
              {ELEMENT_BONUS_OPTIONS.map((attr) => {
                const element = ELEMENT_ICON_BY_ATTR[attr];
                const active = activeElementBonuses.has(attr);
                return (
                  <Button
                    key={attr}
                    size="sm"
                    variant={active ? "default" : "outline"}
                    className="h-8 px-2 gap-1.5"
                    onClick={() => toggleElementBonus(attr)}
                    title={`${element.label} +50% planning bonus`}
                  >
                    <img src={element.src} alt={element.label} className="w-4 h-4 object-contain" />
                    <span className="text-[11px]">{element.label}</span>
                  </Button>
                );
              })}
            </div>
          </CardContent>
        </Card>

        {activeElementBonuses.size > 0 && (
          <div className="mb-4 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-100">
            Element bonus on map biome buffs damage by 50%, it does not give 50% more stats, this tool applies a 50% stat bonus only to help plan and visualize, but that is not accurate.
          </div>
        )}

        {/* Stat icons row */}
        <Card className="shadow-sm mb-4">
          <CardContent className="py-3">
            <div className="flex flex-wrap items-center gap-4">
              <span className="text-xs font-medium text-muted-foreground whitespace-nowrap">Stat Icons</span>
              {STAT_ORDER.map((stat) => (
                <div key={stat} className="flex flex-col items-center gap-0.5">
                  <IconUpload iconKey={stat} icons={statIcons} onSave={(icons) => handleStatIcons(icons, stat)} size={28} />
                  <span className="text-[9px] text-muted-foreground leading-none">{stat}</span>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>

        {/* Controls */}
        <div className="flex flex-wrap items-center gap-3 mb-2">
          <Input placeholder="Search equipment..." value={search} onChange={(e) => setSearch(e.target.value)} className="h-8 text-sm w-44" />
          <SearchableSelect
            value={craftFilter}
            onChange={(v) => setCraftFilter(v as CraftFilter)}
            options={[
              { value: "All", label: "All crafting" },
              { value: "Craftable", label: "Craftable" },
              { value: "Not Craftable", label: "Not craftable" },
            ]}
            triggerClassName="h-8 text-sm w-36"
          />
          <Button
            size="sm"
            variant={favsOnly ? "outline" : "ghost"}
            className={`h-8 gap-2 ${favsOnly ? "border-primary text-primary" : "text-muted-foreground hover:text-foreground"}`}
            onClick={() => setFavsOnly((v) => !v)}
            title="Show only favorited equipment"
          >
            <Star className={`w-3.5 h-3.5 ${favsOnly ? "fill-yellow-400 text-yellow-400" : ""}`} />
            Favorites{favsOnly && favs.size > 0 ? ` (${favs.size})` : ""}
          </Button>
          <Button
            size="sm"
            variant={!sortCol ? "outline" : "ghost"}
            className={`h-8 ${!sortCol ? "border-primary text-primary" : "text-muted-foreground hover:text-foreground"}`}
            onClick={() => setSortCol(null)}
            aria-pressed={!sortCol}
            title="Show equipment in its original selection order"
          >
            Game order
          </Button>
          <SearchableSelect
            value={rowsToShow}
            onChange={(v) => setRowsToShow(v as typeof rowsToShow)}
            options={[
              { value: "25", label: "Show 25" },
              { value: "50", label: "Show 50" },
              { value: "100", label: "Show 100" },
              { value: "all", label: "Show all" },
            ]}
            triggerClassName="h-8 text-sm w-32"
          />
          <button
            onClick={() => setSortByInc((v) => !v)}
            className={`h-8 px-3 text-xs rounded-md border transition-colors font-medium ${sortByInc ? "bg-primary text-primary-foreground border-primary" : "border-input bg-background text-muted-foreground hover:text-foreground hover:border-foreground/40"}`}
            title={sortByInc ? "Currently sorting stats by Growth +Lv - click to sort by value at level" : "Currently sorting stats by value at level - click to sort by Growth +Lv"}
          >
            Sort stats: {sortByInc ? "Growth +Lv" : "Value"}
          </button>
          <div className="md:hidden flex items-center gap-2">
            <SearchableSelect
              value={sortCol || "mobile-sort"}
              onChange={(v) => setSortCol(v === "mobile-sort" ? "" : v)}
              options={MOBILE_SORT_OPTIONS}
              triggerClassName="h-8 text-sm w-32"
            />
            <div className="relative" ref={mobileElementSortMenuRef}>
              <Button
                variant="outline"
                size="icon"
                className="h-8 w-8 shrink-0"
                onClick={() => {
                  if (sortCol === "element") {
                    setElementSortMenuOpen((v) => !v);
                    return;
                  }
                  setSortDir((d) => d === "asc" ? "desc" : "asc");
                }}
                title={sortCol === "element" ? "Pick which element appears first" : `Sort direction: ${sortDir === "asc" ? "Ascending" : "Descending"}`}
              >
                {sortDir === "asc" ? <ArrowUp className="w-3.5 h-3.5" /> : <ArrowDown className="w-3.5 h-3.5" />}
              </Button>
              {sortCol === "element" && elementSortMenuOpen && (
                <div className="absolute z-50 right-0 top-full mt-1 w-44 rounded-md border border-border bg-popover shadow-md text-xs overflow-hidden">
                  <button
                    onMouseDown={(e) => {
                      e.preventDefault();
                      e.stopPropagation();
                      setElementSortFirst(null);
                      setElementSortMenuOpen(false);
                    }}
                    className="w-full flex items-center gap-2 px-3 py-2 hover:bg-muted transition-colors text-foreground"
                  >
                    <span className={`w-3.5 h-3.5 shrink-0 ${elementSortFirst === null ? "text-primary" : "opacity-0"}`}><CheckCircle2 className="w-3.5 h-3.5" /></span>
                    Default element order
                  </button>
                  {ELEMENT_BONUS_OPTIONS.map((attr) => {
                    const element = ELEMENT_ICON_BY_ATTR[attr];
                    return (
                      <button
                        key={attr}
                        onMouseDown={(e) => {
                          e.preventDefault();
                          e.stopPropagation();
                          setSortCol("element");
                          setElementSortFirst(attr);
                          setElementSortMenuOpen(false);
                        }}
                        className="w-full flex items-center gap-2 px-3 py-2 hover:bg-muted transition-colors text-foreground"
                      >
                        <span className={`w-3.5 h-3.5 shrink-0 ${elementSortFirst === attr ? "text-primary" : "opacity-0"}`}><CheckCircle2 className="w-3.5 h-3.5" /></span>
                        <img src={element.src} alt={element.label} className="w-3.5 h-3.5 object-contain" />
                        {element.label} first
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          </div>
          {selectedUids.size > 0 && !compareMode && (
            <Button size="sm" variant="outline" className="h-8 gap-1.5 border-primary text-primary hover:bg-primary/5" onClick={() => setCompareMode(true)}>
              <CheckSquare className="w-3.5 h-3.5" />Compare {selectedUids.size} selected
            </Button>
          )}
          {compareMode && (
            <>
              <Button size="sm" variant="outline" className="h-8 gap-1.5" onClick={leaveCompareMode}>
                <Eye className="w-3.5 h-3.5" />Show all
              </Button>
              <Button size="sm" variant="ghost" className="h-8 gap-1 text-muted-foreground hover:text-foreground" onClick={clearSelection}>
                <X className="w-3 h-3" />Clear selection
              </Button>
            </>
          )}
          {anyFilterActive && (
            <Button size="sm" variant="ghost" className="h-8 gap-1 text-muted-foreground hover:text-foreground" onClick={clearAllFilters}>
              <X className="w-3 h-3" />Clear all
            </Button>
          )}

          <div className="ml-auto flex items-center gap-2">
            {dataUpdatedAt && <span className="text-xs text-muted-foreground">Updated {new Date(dataUpdatedAt).toLocaleTimeString()}</span>}
            <Button variant="outline" size="sm" onClick={() => refetch()} disabled={isLoading} className="h-8 gap-2">
              {isLoading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}Refresh
            </Button>
          </div>
        </div>
        {/* Filter dropdowns */}
        <div ref={filterMenuRef} className="flex flex-wrap items-center gap-2 mb-3">
          {/* Slot */}
          <div className="relative">
            <button
              onClick={() => setOpenFilterMenu((v) => v === "slot" ? null : "slot")}
              className={`h-8 px-3 text-xs rounded-md border font-medium flex items-center gap-1.5 transition-colors ${
                slotFilters.size > 0 ? "bg-primary text-primary-foreground border-primary" : "border-input bg-background text-muted-foreground hover:text-foreground"
              }`}
            >
              Slot{slotFilters.size > 0 ? ` (${slotFilters.size})` : ""}
              <ChevronDown className={`w-3 h-3 transition-transform ${openFilterMenu === "slot" ? "rotate-180" : ""}`} />
            </button>
            {openFilterMenu === "slot" && (
              <div className="absolute z-50 top-full mt-1 left-0 min-w-[140px] rounded-md border border-border bg-popover shadow-md text-xs overflow-hidden">
                {CHAR_SLOTS.map((slot) => (
                  <button key={slot} onMouseDown={(e) => { e.preventDefault(); e.stopPropagation(); setSlotFilters((prev) => { const next = new Set(prev); next.has(slot) ? next.delete(slot) : next.add(slot); return next; }); }}
                    className="w-full flex items-center gap-2 px-3 py-2 hover:bg-muted transition-colors text-foreground">
                    <span className={`w-3.5 h-3.5 shrink-0 ${slotFilters.has(slot) ? "text-primary" : "opacity-0"}`}><CheckCircle2 className="w-3.5 h-3.5" /></span>
                    {slot}
                  </button>
                ))}
              </div>
            )}
          </div>
          {/* Tier */}
          <div className="relative">
            <button
              onClick={() => setOpenFilterMenu((v) => v === "tier" ? null : "tier")}
              className={`h-8 px-3 text-xs rounded-md border font-medium flex items-center gap-1.5 transition-colors ${
                rankFilters.size > 0 ? "bg-primary text-primary-foreground border-primary" : "border-input bg-background text-muted-foreground hover:text-foreground"
              }`}
            >
              Tier{rankFilters.size > 0 ? ` (${rankFilters.size})` : ""}
              <ChevronDown className={`w-3 h-3 transition-transform ${openFilterMenu === "tier" ? "rotate-180" : ""}`} />
            </button>
            {openFilterMenu === "tier" && (
              <div className="absolute z-50 top-full mt-1 left-0 min-w-[120px] rounded-md border border-border bg-popover shadow-md text-xs overflow-hidden">
                {EQUIPMENT_RANKS.map((rank) => (
                  <button key={rank} onMouseDown={(e) => { e.preventDefault(); e.stopPropagation(); setRankFilters((prev) => { const next = new Set(prev); next.has(rank) ? next.delete(rank) : next.add(rank); return next; }); }}
                    className="w-full flex items-center gap-2 px-3 py-2 hover:bg-muted transition-colors text-foreground">
                    <span className={`w-3.5 h-3.5 shrink-0 ${rankFilters.has(rank) ? "text-primary" : "opacity-0"}`}><CheckCircle2 className="w-3.5 h-3.5" /></span>
                    {rank}
                  </button>
                ))}
              </div>
            )}
          </div>
          {/* Does not have stats */}
          <div className="relative">
            <button
              onClick={() => setOpenFilterMenu((v) => v === "exclude-stats" ? null : "exclude-stats")}
              className={`h-8 px-3 text-xs rounded-md border font-medium flex items-center gap-1.5 transition-colors ${
                excludeStatFilters.size > 0 ? "bg-primary text-primary-foreground border-primary" : "border-input bg-background text-muted-foreground hover:text-foreground"
              }`}
            >
              Does not have{excludeStatFilters.size > 0 ? ` (${excludeStatFilters.size})` : ""}
              <ChevronDown className={`w-3 h-3 transition-transform ${openFilterMenu === "exclude-stats" ? "rotate-180" : ""}`} />
            </button>
            {openFilterMenu === "exclude-stats" && (
              <div className="absolute z-50 top-full mt-1 left-0 min-w-[180px] rounded-md border border-border bg-popover shadow-md text-xs overflow-hidden">
                {STAT_ORDER.map((stat) => (
                  <button key={stat} onMouseDown={(e) => { e.preventDefault(); e.stopPropagation(); setExcludeStatFilters((prev) => { const next = new Set(prev); next.has(stat) ? next.delete(stat) : next.add(stat); return next; }); }}
                    className="w-full flex items-center gap-2 px-3 py-2 hover:bg-muted transition-colors text-foreground">
                    <span className={`w-3.5 h-3.5 shrink-0 ${excludeStatFilters.has(stat) ? "text-primary" : "opacity-0"}`}><CheckCircle2 className="w-3.5 h-3.5" /></span>
                    {STAT_SHORT[stat] ?? stat}
                  </button>
                ))}
              </div>
            )}
          </div>
          <button
            onClick={() => setHideExcludedStatColumns((v) => !v)}
            className={`h-8 w-8 rounded-md border transition-colors inline-flex items-center justify-center ${
              hideExcludedStatColumns ? "bg-primary text-primary-foreground border-primary" : "border-input bg-background text-muted-foreground hover:text-foreground"
            }`}
            title={hideExcludedStatColumns ? "Show all stat columns" : "Hide excluded stat columns"}
            aria-label={hideExcludedStatColumns ? "Show all stat columns" : "Hide excluded stat columns"}
          >
            {hideExcludedStatColumns ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
          </button>
          {/* Craft */}
          <div className="relative">
            <button
              onClick={() => setOpenFilterMenu((v) => v === "craft" ? null : "craft")}
              className={`h-8 px-3 text-xs rounded-md border font-medium flex items-center gap-1.5 transition-colors ${
                craftFilter !== "All" ? "bg-primary text-primary-foreground border-primary" : "border-input bg-background text-muted-foreground hover:text-foreground"
              }`}
            >
              {craftFilter === "All" ? "Craftable" : craftFilter}
              <ChevronDown className={`w-3 h-3 transition-transform ${openFilterMenu === "craft" ? "rotate-180" : ""}`} />
            </button>
            {openFilterMenu === "craft" && (
              <div className="absolute z-50 top-full mt-1 left-0 min-w-[140px] rounded-md border border-border bg-popover shadow-md text-xs overflow-hidden">
                {(["All", "Craftable", "Not Craftable"] as const).map((opt) => (
                  <button key={opt} onMouseDown={(e) => { e.preventDefault(); e.stopPropagation(); setCraftFilter(opt); }}
                    className="w-full flex items-center gap-2 px-3 py-2 hover:bg-muted transition-colors text-foreground">
                    <span className={`w-3.5 h-3.5 shrink-0 ${craftFilter === opt ? "text-primary" : "opacity-0"}`}><CheckCircle2 className="w-3.5 h-3.5" /></span>
                    {opt === "All" ? "All craftable" : opt}
                  </button>
                ))}
              </div>
            )}
          </div>
          {/* Studio */}
          {allStudioLevels.length > 0 && (
            <div className="relative">
              <button
                onClick={() => setOpenFilterMenu((v) => v === "studio" ? null : "studio")}
                className={`h-8 px-3 text-xs rounded-md border font-medium flex items-center gap-1.5 transition-colors ${
                  studioFilters.size > 0 ? "bg-primary text-primary-foreground border-primary" : "border-input bg-background text-muted-foreground hover:text-foreground"
                }`}
              >
                Studio{studioFilters.size > 0 ? ` (${studioFilters.size})` : ""}
                <ChevronDown className={`w-3 h-3 transition-transform ${openFilterMenu === "studio" ? "rotate-180" : ""}`} />
              </button>
              {openFilterMenu === "studio" && (
                <div className="absolute z-50 top-full mt-1 left-0 min-w-[120px] rounded-md border border-border bg-popover shadow-md text-xs overflow-hidden">
                  {allStudioLevels.map((s) => (
                    <button key={s} onMouseDown={(e) => { e.preventDefault(); e.stopPropagation(); setStudioFilters((prev) => { const next = new Set(prev); next.has(s) ? next.delete(s) : next.add(s); return next; }); setIntFilters(new Set()); }}
                      className="w-full flex items-center gap-2 px-3 py-2 hover:bg-muted transition-colors text-foreground">
                      <span className={`w-3.5 h-3.5 shrink-0 ${studioFilters.has(s) ? "text-primary" : "opacity-0"}`}><CheckCircle2 className="w-3.5 h-3.5" /></span>
                      Lv {s}
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}
          {/* INT (sub-filter, only when studio selected) */}
          {availableIntValues.length > 0 && (
            <div className="relative">
              <button
                onClick={() => setOpenFilterMenu((v) => v === "int" ? null : "int")}
                className={`h-8 px-3 text-xs rounded-md border font-medium flex items-center gap-1.5 transition-colors ${
                  intFilters.size > 0 ? "bg-primary text-primary-foreground border-primary" : "border-input bg-background text-muted-foreground hover:text-foreground"
                }`}
              >
                INT{intFilters.size > 0 ? ` (${intFilters.size})` : ""}
                <ChevronDown className={`w-3 h-3 transition-transform ${openFilterMenu === "int" ? "rotate-180" : ""}`} />
              </button>
              {openFilterMenu === "int" && (
                <div className="absolute z-50 top-full mt-1 left-0 min-w-[100px] rounded-md border border-border bg-popover shadow-md text-xs overflow-hidden">
                  {availableIntValues.map((v) => (
                    <button key={v} onMouseDown={(e) => { e.preventDefault(); e.stopPropagation(); setIntFilters((prev) => { const next = new Set(prev); next.has(v) ? next.delete(v) : next.add(v); return next; }); }}
                      className="w-full flex items-center gap-2 px-3 py-2 hover:bg-muted transition-colors text-foreground">
                      <span className={`w-3.5 h-3.5 shrink-0 ${intFilters.has(v) ? "text-primary" : "opacity-0"}`}><CheckCircle2 className="w-3.5 h-3.5" /></span>
                      {v}
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-2 mb-4 rounded-lg border border-border bg-muted/30 px-3 py-2">
          <span className="text-xs font-medium text-muted-foreground">Compare at level</span>
          <NumInput
            value={bulkCompareLevel}
            min={1}
            max={99}
            onChange={setBulkCompareLevel}
            className="h-8 w-20 text-sm text-center bg-background border-border/70"
            inputClassName="px-1"
          />
          <Button size="sm" variant="outline" className="h-8 text-xs" onClick={() => applyLevelToShown(bulkCompareLevel)}>
            Set shown items
          </Button>
          <Button size="sm" variant="outline" className="h-8 text-xs" onClick={() => applyLevelToAll(bulkCompareLevel)}>
            Set all items
          </Button>
          <span className="text-[11px] text-muted-foreground">Quick way to compare everything at one level without changing rows one by one.</span>
        </div>

        {isLoading && <div className="flex items-center justify-center py-16 gap-3 text-muted-foreground"><Loader2 className="w-6 h-6 animate-spin text-primary" /><span className="text-sm">Fetching data...</span></div>}
        {isError && (
          <div className="flex items-center gap-3 rounded-lg border border-destructive/30 bg-destructive/5 px-4 py-3 mb-4">
            <AlertTriangle className="w-4 h-4 text-destructive shrink-0" />
            <p className="text-sm text-destructive">Failed to load sheet data.</p>
            <Button size="sm" variant="outline" onClick={() => refetch()} className="ml-auto h-7">Retry</Button>
          </div>
        )}

        {!isLoading && !isError && (
          <>
            {compareMode && (
              <div className="mb-3 flex items-center gap-2 rounded-lg bg-primary/5 border border-primary/20 px-3 py-2 text-xs text-primary font-medium">
                <CheckSquare className="w-4 h-4" />
                Comparing {selectedUids.size} items
                <div className="ml-auto flex items-center gap-2">
                  <button onClick={leaveCompareMode} className="flex items-center gap-1 hover:underline"><Eye className="w-3 h-3" />Show all</button>
                  <button onClick={clearSelection} className="flex items-center gap-1 hover:underline"><X className="w-3 h-3" />Clear selection</button>
                </div>
              </div>
            )}

            {/* Equipment Table */}
            <Card className="shadow-sm mb-6 overflow-hidden hidden md:block">
              <div className="overflow-x-auto">
                <table className="w-full text-sm border-collapse">
                  <thead>
                    <tr className="bg-muted/60 border-b border-border">
                      <th className="px-1 py-2 w-5 shrink-0 text-muted-foreground/30" title="Drag to equip in Character Builder">
                        <GripVertical className="w-3.5 h-3.5 mx-auto" />
                      </th>
                      <th className="px-2 py-2 w-7 shrink-0">
                        <input type="checkbox" className="rounded border-border"
                          checked={selectedUids.size === filtered.length && filtered.length > 0}
                          onChange={(e) => {
                            if (e.target.checked) setSelectedUids(new Set(filtered.map((i) => i.uid)));
                            else setSelectedUids(new Set());
                          }} title="Select all visible" />
                      </th>
                      <th className="px-1.5 py-2 w-8 shrink-0" />
                      <th className="px-1.5 py-2 w-8 shrink-0 text-muted-foreground/70" title="Favorites">
                        <Star className="w-3.5 h-3.5 mx-auto" />
                      </th>
                      <th className="text-left px-1.5 py-2 font-medium text-muted-foreground whitespace-nowrap">
                        <button onClick={() => handleSort("name")} className="flex items-center gap-1 hover:text-foreground">Name <SortIcon col="name" /></button>
                      </th>
                      <th className="text-center px-1 py-2 font-medium text-muted-foreground whitespace-nowrap w-11">
                        <div className="relative inline-flex items-center gap-1" ref={desktopElementSortMenuRef}>
                          <button onClick={() => handleSort("element")} className="inline-flex items-center gap-1 hover:text-foreground">Element</button>
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              setSortCol("element");
                              setElementSortMenuOpen((v) => !v);
                            }}
                            className="inline-flex items-center hover:text-foreground"
                            title="Pick which element appears first"
                          >
                            <SortIcon col="element" />
                          </button>
                          {sortCol === "element" && elementSortMenuOpen && (
                            <div className="absolute z-50 right-0 top-full mt-1 w-44 rounded-md border border-border bg-popover shadow-md text-xs overflow-hidden">
                              <button
                                onMouseDown={(e) => {
                                  e.preventDefault();
                                  e.stopPropagation();
                                  setElementSortFirst(null);
                                  setElementSortMenuOpen(false);
                                }}
                                className="w-full flex items-center gap-2 px-3 py-2 hover:bg-muted transition-colors text-foreground"
                              >
                                <span className={`w-3.5 h-3.5 shrink-0 ${elementSortFirst === null ? "text-primary" : "opacity-0"}`}><CheckCircle2 className="w-3.5 h-3.5" /></span>
                                Default element order
                              </button>
                              {ELEMENT_BONUS_OPTIONS.map((attr) => {
                                const element = ELEMENT_ICON_BY_ATTR[attr];
                                return (
                                  <button
                                    key={attr}
                                    onMouseDown={(e) => {
                                      e.preventDefault();
                                      e.stopPropagation();
                                      setSortCol("element");
                                      setElementSortFirst(attr);
                                      setElementSortMenuOpen(false);
                                    }}
                                    className="w-full flex items-center gap-2 px-3 py-2 hover:bg-muted transition-colors text-foreground"
                                  >
                                    <span className={`w-3.5 h-3.5 shrink-0 ${elementSortFirst === attr ? "text-primary" : "opacity-0"}`}><CheckCircle2 className="w-3.5 h-3.5" /></span>
                                    <img src={element.src} alt={element.label} className="w-3.5 h-3.5 object-contain" />
                                    {element.label} first
                                  </button>
                                );
                              })}
                            </div>
                          )}
                        </div>
                      </th>
                      <th className="text-center px-1.5 py-2 font-medium text-muted-foreground whitespace-nowrap w-16">Level</th>
                      <th className="text-left px-1.5 py-2 font-medium text-muted-foreground whitespace-nowrap">
                        <button onClick={() => handleSort("slot")} className="flex items-center gap-1 hover:text-foreground">Slot <SortIcon col="slot" /></button>
                      </th>
                      <th className="text-left px-1.5 py-2 font-medium text-muted-foreground whitespace-nowrap">
                        <div className="flex items-center gap-1">
                          <button onClick={() => handleSort("studioLevel")} className="flex items-center gap-1 hover:text-foreground">Studio Lv <SortIcon col="studioLevel" /></button>
                          <span className="text-muted-foreground/40">/</span>
                          <button onClick={() => handleSort("intReq")} className="flex items-center gap-1 hover:text-foreground">INT Req <SortIcon col="intReq" /></button>
                        </div>
                      </th>
                    {visibleStats.map((stat) => (
  <th key={stat} className="text-center px-1.5 py-2 font-medium text-muted-foreground">
    <button
      onClick={() => handleSort(stat)}
      className="flex flex-col items-center gap-0.5 hover:text-foreground w-full min-w-[42px]"
      title={`Sort by ${sortByInc ? "+/Lv increment" : "value at level"} for ${stat}`}
    >
      {statIcons[stat] ? (
        <img
          src={statIcons[stat]}
          alt={stat}
          className="w-4 h-4 object-contain mx-auto"
          title={stat}
        />
      ) : (
        <div className="h-4" />
      )}

      <span className="text-[9px] leading-none font-medium whitespace-nowrap">
        {stat === "Vigor" ? "Vig" :
         stat === "Attack" ? "Atk" :
         stat === "Defence" ? "Def" :
         stat === "Speed" ? "Spd" :
         stat === "Luck" ? "Lck" :
         stat === "Intelligence" ? "Int" :
         stat === "Dexterity" ? "Dex" :
         stat === "Gather" ? "Gth" :
         stat === "Move" ? "Mov" :
         stat === "Heart" ? "Hrt" :
         stat}
      </span>

      {sortByInc ? (
        <span className="text-[8px] text-primary/70 leading-none">+/Lv</span>
      ) : (
        <span className="text-[8px] leading-none opacity-0">+/Lv</span>
      )}

      <SortIcon col={stat} />
    </button>
  </th>
))}
                    </tr>
                  </thead>
                  <tbody>
                    {filtered.length === 0 && <tr><td colSpan={colCount} className="text-center py-10 text-sm text-muted-foreground">No equipment found.</td></tr>}
                    {visibleItems.map((item) => {
                      const isExpanded = expandedItem === item.uid;
                      const level = getItemLevel(item.uid);
                      const itemSlot = getItemSlot(item.name);
                      const isSelected = selectedUids.has(item.uid);
                      return (
                        <Fragment key={item.uid}>
                          <tr
                            className={`border-b border-border transition-colors ${isExpanded ? "bg-primary/5" : isSelected ? "bg-primary/5" : "hover:bg-muted/20"}`}>
                            <td
                              className="px-1 py-1.5 text-center text-muted-foreground/40 cursor-grab active:cursor-grabbing select-none"
                              draggable
                              onDragStart={(e) => {
                                draggingItemRef.current = { name: item.name, slot: itemSlot };
                                e.dataTransfer.effectAllowed = "copy";
                                e.dataTransfer.setData("text/plain", item.name);
                                e.dataTransfer.setData("application/ka-slot", itemSlot);
                              }}
                              onDragEnd={() => { draggingItemRef.current = null; setDragOverSlot(null); }}>
                              <GripVertical className="w-3.5 h-3.5 mx-auto" />
                            </td>
                            <td className="px-2 py-1.5 text-center">
                              <input type="checkbox" checked={isSelected} onChange={() => toggleSelect(item.uid)} className="rounded border-border cursor-pointer" />
                            </td>
                            <td className="px-2 py-1.5 text-center">
                              <button type="button" onClick={() => toggleFav(item.uid)} className="text-muted-foreground hover:text-yellow-400" title={favs.has(item.uid) ? "Remove from favorites" : "Add to favorites"}>
                                <Star className={`w-4 h-4 ${favs.has(item.uid) ? "fill-yellow-400 text-yellow-400" : ""}`} />
                              </button>
                            </td>
                            <td className="px-2 py-1.5">
                              <IconUpload iconKey={`equip:${item.name}`} icons={equipIcons}
                                onSave={(icons) => handleEquipIcons(icons, item.name)} size={26} />
                            </td>
                            <td className="px-1.5 py-1.5 whitespace-nowrap">
                              <button onClick={() => setExpandedItem(isExpanded ? null : item.uid)}
                                className="flex items-center gap-1.5 font-medium text-left hover:text-primary transition-colors group" title={isExpanded ? "Click to collapse" : "Click to view base and growth"}>
                                {isExpanded
                                  ? <ChevronDown className="w-3.5 h-3.5 text-primary shrink-0" />
                                  : <ChevronRight className="w-3.5 h-3.5 text-muted-foreground group-hover:text-primary shrink-0" />}
                                <span className={isExpanded ? "text-primary underline underline-offset-2 decoration-primary/40" : ""}>{item.name}</span>
                                {allItemStatsFilled(item, overrides) && (
                                  <span title="All stats have been contributed">
                                    <CheckCircle2 className="w-3.5 h-3.5 text-green-500 dark:text-green-400 shrink-0 ml-0.5" />
                                  </span>
                                )}
                              </button>
                            </td>
                            <td className="px-1 py-1.5 text-center">
                              {item.elementAttribute !== null && ELEMENT_ICON_BY_ATTR[item.elementAttribute] ? (
                                <img
                                  src={ELEMENT_ICON_BY_ATTR[item.elementAttribute].src}
                                  alt={ELEMENT_ICON_BY_ATTR[item.elementAttribute].label}
                                  className="w-4 h-4 object-contain mx-auto"
                                  title={ELEMENT_ICON_BY_ATTR[item.elementAttribute].label}
                                />
                              ) : (
                                <span className="text-muted-foreground/40">-</span>
                              )}
                            </td>
                            <td className="px-1.5 py-1.5">
                              <NumInput value={level} min={1} max={99}
                                onChange={(v) => setItemLevel(item.uid, v)}
                                className="h-7 w-16 text-xs text-center mx-auto bg-background border-border/70"
                                inputClassName="px-1" />
                            </td>
                            <td className="px-1.5 py-1.5">
                              <span className="inline-flex items-center rounded-full border border-border bg-muted/40 px-2 py-1 text-[11px] font-medium text-foreground/80">
                                {itemSlot !== " " ? itemSlot : item.sheetSlot || " "}
                              </span>
                            </td>
                            <td className="px-1.5 py-1.5">
                              {item.crafterStudioLevel === 0
                                ? <span className="text-destructive text-xs font-medium">Not craftable</span>
                                : <span className="text-xs"><strong>{item.crafterStudioLevel}</strong>{item.crafterIntelligence > 0 && <> / INT <strong>{item.crafterIntelligence}</strong></>}</span>}
                            </td>
                          {visibleStats.map((stat) => {
  const unset = isStatUnset(item, stat, overrides);

  if (unset) {
    return (
      <td
        key={stat}
        className="px-1.5 py-1.5 text-center text-xs tabular-nums text-red-400 dark:text-red-500"
      >
        -
      </td>
    );
  }

  const displayVal = sortByInc
    ? getItemIncStatVal(item, stat)
    : getItemStatVal(item, stat);

  return (
    <td
      key={stat}
      className={`px-1.5 py-1.5 text-center text-xs tabular-nums ${
        displayVal === 0
          ? "text-muted-foreground/50"
          : "font-medium text-foreground"
      }`}
    >
      {sortByInc && displayVal > 0 ? `+${displayVal}` : displayVal}
    </td>
  );
})}
                          </tr>

                          {isExpanded && (
                            <tr>
                              <td colSpan={colCount} className="bg-primary/5 border-b border-primary/20 px-4 py-3">
                                <div className="flex flex-wrap items-center gap-2 mb-3 pb-3 border-b border-primary/10 text-xs text-muted-foreground">
                                  <span>
                                    Viewing <strong className="text-foreground">{item.name}</strong>
                                  </span>
                                  <span className="rounded-full border border-border bg-background px-2 py-1 text-[11px] font-medium text-foreground/80">
                                    Slot: {itemSlot !== " " ? itemSlot : item.sheetSlot || " "}
                                  </span>
                                  {itemSlot === "Weapon" && weaponTypes[item.name] && (
                                    <span className="rounded-full border border-border bg-background px-2 py-1 text-[11px] font-medium text-foreground/80">
                                      Weapon Type: {weaponTypes[item.name]}
                                    </span>
                                  )}
                                </div>
                                <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-x-4 gap-y-2">
                                  {STAT_ORDER.filter((stat) => {
                                    const base = applyElementBonus(getEffectiveStat(item, stat, "base", overrides), item);
                                    const inc = getItemIncStatVal(item, stat);
                                    const current = getItemStatAtLevel(item, stat, level);
                                    return base !== 0 || inc !== 0 || current !== 0;
                                  }).map((stat) => {
                                    const base = applyElementBonus(getEffectiveStat(item, stat, "base", overrides), item);
                                    const inc = getItemIncStatVal(item, stat);
                                    const current = getItemStatAtLevel(item, stat, level);
                                    return (
                                      <div key={stat} className="rounded-md border border-border bg-background/70 px-2 py-2">
                                        <span className="text-[10px] font-semibold text-muted-foreground flex items-center gap-1">
                                          {statIcons[stat] && <img src={statIcons[stat]} alt={stat} className="w-3 h-3 object-contain" />}{stat}
                                        </span>
                                        <div className="mt-2 grid grid-cols-3 gap-1 text-center">
                                          <div>
                                            <div className="text-[9px] text-muted-foreground/70">Base</div>
                                            <div className="text-sm font-medium text-foreground">{base}</div>
                                          </div>
                                          <div>
                                            <div className="text-[9px] text-muted-foreground/70">+/Lv</div>
                                            <div className="text-sm font-medium text-foreground">{inc}</div>
                                          </div>
                                          <div>
                                            <div className="text-[9px] text-muted-foreground/70">Lv {level}</div>
                                            <div className="text-sm font-semibold text-primary">{current}</div>
                                          </div>
                                        </div>
                                      </div>
                                    );
                                  })}
                                </div>
                              </td>
                            </tr>
                          )}
                        </Fragment>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <div className="px-4 py-2 border-t border-border bg-muted/30 text-xs text-muted-foreground flex items-center justify-between">
                <span>{rowsToShow === "all" ? `${filtered.length}` : `${visibleItems.length} of ${filtered.length}`} visible | {items.length} total | click item name to view base and growth</span>
                {selectedUids.size > 0 && !compareMode && (
                  <button onClick={() => setCompareMode(true)} className="text-primary font-medium hover:underline">
                    Compare {selectedUids.size} selected â†’
                  </button>
                )}
              </div>
            </Card>

            <div className="md:hidden space-y-3 mb-6">
              {filtered.length === 0 ? (
                <Card className="shadow-sm">
                  <CardContent className="py-10 text-center text-sm text-muted-foreground">
                    No equipment found.
                  </CardContent>
                </Card>
              ) : (
                visibleItems.map((item) => {
                  const isExpanded = expandedItem === item.uid;
                  const level = getItemLevel(item.uid);
                  const itemSlot = getItemSlot(item.name);
                  const isSelected = selectedUids.has(item.uid);

                  const summaryStats = STAT_ORDER
                    .map((stat) => {
                      const unset = isStatUnset(item, stat, overrides);
                      if (unset) return null;

                      const value = sortByInc
                        ? getItemIncStatVal(item, stat)
                        : getItemStatVal(item, stat);

                      if (value === 0) return null;

                      return {
                        stat,
                        label: STAT_SHORT[stat] ?? stat,
                        value: sortByInc && value > 0 ? `+${value}` : `${value}`,
                      };
                    })
                    .filter(Boolean) as Array<{ stat: string; label: string; value: string }>;

                  return (
                    <Card
                      key={item.uid}
                      className={`shadow-sm overflow-hidden ${isExpanded || isSelected ? "border-primary/30" : ""}`}
                    >
                      <CardContent className="p-3">
                        <div className="flex items-start gap-3">
                          <div className="pt-0.5 shrink-0">
                            <input
                              type="checkbox"
                              checked={isSelected}
                              onChange={() => toggleSelect(item.uid)}
                              className="rounded border-border cursor-pointer"
                              aria-label={`Select ${item.name} for comparison`}
                            />
                          </div>

                          <div className="shrink-0">
                            <IconUpload
                              iconKey={`equip:${item.name}`}
                              icons={equipIcons}
                              onSave={(icons) => handleEquipIcons(icons, item.name)}
                              size={30}
                            />
                          </div>

                          <div className="min-w-0 flex-1">
                            <button
                              onClick={() => setExpandedItem(isExpanded ? null : item.uid)}
                              className="flex items-center gap-1.5 font-medium text-left hover:text-primary transition-colors w-full"
                              title={isExpanded ? "Click to collapse" : "Click to view base and growth"}
                            >
                              {isExpanded
                                ? <ChevronDown className="w-4 h-4 text-primary shrink-0" />
                                : <ChevronRight className="w-4 h-4 text-muted-foreground shrink-0" />}
                              <span className={`min-w-0 break-words ${isExpanded ? "text-primary underline underline-offset-2 decoration-primary/40" : ""}`}>
                                {item.name}
                              </span>
                              {allItemStatsFilled(item, overrides) && (
                                <span title="All stats have been contributed">
                                  <CheckCircle2
                                    className="w-3.5 h-3.5 text-green-500 dark:text-green-400 shrink-0 ml-0.5"
                                  />
                                </span>
                              )}
                            </button>

                            <div className="mt-2 flex flex-wrap gap-2">
                              <span className="inline-flex items-center rounded-full border border-border bg-muted/40 px-2 py-1 text-[11px] font-medium text-foreground/80">
                                {itemSlot !== " " ? itemSlot : item.sheetSlot || "-"}
                              </span>
                              {itemSlot === "Weapon" && weaponTypes[item.name] && (
                                <span className="inline-flex items-center rounded-full border border-border bg-muted/40 px-2 py-1 text-[11px] font-medium text-foreground/80">
                                  {weaponTypes[item.name]}
                                </span>
                              )}
                              {item.crafterStudioLevel === 0 ? (
                                <span className="inline-flex items-center rounded-full border border-red-300/50 bg-red-500/5 px-2 py-1 text-[11px] font-medium text-red-500">
                                  Not craftable
                                </span>
                              ) : (
                                <span className="inline-flex items-center rounded-full border border-border bg-muted/40 px-2 py-1 text-[11px] font-medium text-foreground/80">
                                  Studio {item.crafterStudioLevel}{item.crafterIntelligence > 0 ? ` / INT ${item.crafterIntelligence}` : ""}
                                </span>
                              )}
                            </div>

                            {summaryStats.length > 0 && (
                              <div className="mt-3 flex flex-wrap gap-1.5">
                                {summaryStats.map(({ stat, label, value }) => (
                                  <span
                                    key={stat}
                                    className="inline-flex items-center gap-1 rounded-md border border-border bg-background/70 px-2 py-1 text-[11px] leading-none"
                                  >
                                    {statIcons[stat] && (
                                      <img src={statIcons[stat]} alt={label} className="w-3 h-3 object-contain" />
                                    )}
                                    <span className="text-muted-foreground">{label}</span>
                                    <span className="font-semibold text-foreground tabular-nums">{value}</span>
                                  </span>
                                ))}
                              </div>
                            )}
                          </div>

                          <div className="shrink-0 flex items-center pt-1">
                            <button type="button" onClick={() => toggleFav(item.uid)} className="text-muted-foreground hover:text-yellow-400" title={favs.has(item.uid) ? "Remove from favorites" : "Add to favorites"}>
                              <Star className={`w-5 h-5 ${favs.has(item.uid) ? "fill-yellow-400 text-yellow-400" : ""}`} />
                            </button>
                          </div>

                          <div className="shrink-0">
                            <NumInput
                              value={level}
                              min={1}
                              max={99}
                              onChange={(v) => setItemLevel(item.uid, v)}
                              className="h-8 w-16 text-sm text-center bg-background border-border/70"
                              inputClassName="px-1"
                            />
                          </div>
                        </div>

                        {isExpanded && (
                          <div className="mt-3 grid grid-cols-1 sm:grid-cols-2 gap-2">
                            {STAT_ORDER.filter((stat) => {
                              const base = applyElementBonus(getEffectiveStat(item, stat, "base", overrides), item);
                              const inc = getItemIncStatVal(item, stat);
                              const current = getItemStatAtLevel(item, stat, level);
                              return base !== 0 || inc !== 0 || current !== 0;
                            }).map((stat) => {
                              const base = applyElementBonus(getEffectiveStat(item, stat, "base", overrides), item);
                              const inc = getItemIncStatVal(item, stat);
                              const current = getItemStatAtLevel(item, stat, level);
                              return (
                                <div key={stat} className="rounded-md border border-border bg-background/70 px-3 py-3">
                                  <div className="flex items-center gap-1 text-xs font-semibold text-muted-foreground">
                                    {statIcons[stat] && (
                                      <img src={statIcons[stat]} alt={stat} className="w-3.5 h-3.5 object-contain" />
                                    )}
                                    <span>{stat}</span>
                                  </div>
                                  <div className="mt-2 grid grid-cols-3 gap-2 text-center">
                                    <div>
                                      <div className="text-[10px] text-muted-foreground/70">Base</div>
                                      <div className="text-base font-medium text-foreground">{base}</div>
                                    </div>
                                    <div>
                                      <div className="text-[10px] text-muted-foreground/70">+/Lv</div>
                                      <div className="text-base font-medium text-foreground">{inc}</div>
                                    </div>
                                    <div>
                                      <div className="text-[10px] text-muted-foreground/70">Lv {level}</div>
                                      <div className="text-base font-semibold text-primary">{current}</div>
                                    </div>
                                  </div>
                                </div>
                              );
                            })}
                          </div>
                        )}
                      </CardContent>
                    </Card>
                  );
                })
              )}
            </div>

            {/* Equipment Builder */}
            <Card id="equipment-builder-tool" tabIndex={-1} className="shadow-sm mb-6 scroll-mt-24">
              <CardHeader className="pb-3">
                <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-3">
                  <div>
                    <CardTitle className="text-base">Equipment Builder</CardTitle>
                    <p className="text-xs text-muted-foreground">Your personal equipment loadouts - saved only for you. Each loadout contains Head, Weapon, Shield, Armor, and Accessory.</p>
                  </div>
                  <Button size="sm" variant="outline" className="h-8" onClick={() => addLoadout()}>
                    <Plus className="w-3.5 h-3.5" />Add loadout
                  </Button>
                </div>
              </CardHeader>
              <CardContent>
                <div className="space-y-4 mb-4">
                  {loadout.map((loadoutItem) => {
                    const loadoutTotal = loadoutTotals.find((item) => item.id === loadoutItem.id);
                    return (
                      <Card key={loadoutItem.id} className="shadow-sm">
                        <CardHeader className="pb-3">
                          <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-3">
                            <div>
                            {editingLoadoutId === loadoutItem.id ? (
                              <div className="flex flex-wrap items-center gap-2">
                                <Input
                                  value={editingLoadoutLabel}
                                  onChange={(e) => setEditingLoadoutLabel(e.target.value)}
                                  className="h-8 text-sm w-full max-w-xs"
                                  placeholder="Loadout name"
                                  onKeyDown={(e) => { if (e.key === "Enter") saveRenameLoadout(); }}
                                />
                                <Button size="sm" variant="outline" className="h-8" onClick={saveRenameLoadout}>
                                  Save
                                </Button>
                                <Button size="sm" variant="ghost" className="h-8" onClick={cancelRenameLoadout}>
                                  Cancel
                                </Button>
                              </div>
                            ) : (
                              <>
                                <div className="flex items-center gap-2">
                                  <div className="text-sm font-semibold text-foreground">{loadoutItem.label}</div>
                                  <button
                                    type="button"
                                    onClick={() => startRenameLoadout(loadoutItem.id)}
                                    className="inline-flex items-center justify-center rounded border border-border bg-background text-muted-foreground hover:text-foreground hover:border-foreground/70 h-8 w-8"
                                    aria-label={`Rename ${loadoutItem.label}`}
                                  >
                                    <Pencil className="w-3.5 h-3.5" />
                                  </button>
                                </div>
                                <p className="text-xs text-muted-foreground">Full loadout with one item per slot.</p>
                              </>
                            )}
                          </div>
                          <div className="flex flex-wrap gap-2">
                            <Button size="sm" variant="outline" className="h-8" onClick={() => duplicateLoadout(loadoutItem.id)}>
                              <Copy className="w-3.5 h-3.5" />Duplicate
                            </Button>
                            {loadout.length > 1 && (
                              <Button size="sm" variant="destructive" className="h-8" onClick={() => removeLoadout(loadoutItem.id)}>
                                Remove
                              </Button>
                            )}
                          </div>
                          </div>
                        </CardHeader>
                        <CardContent>
                          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-5 gap-3 mb-4">
                            {CHAR_SLOTS.map((slotType) => {
                              const entry = loadoutItem.slots[slotType];
                              const item = items.find((i) => i.name === entry.itemName);
                              const eligibleItems = items.filter((i) => {
                                const a = getItemSlot(i.name);
                                return a === slotType || a === "-";
                              });
                              const isOver = dragOverSlot === `${loadoutItem.id}:${slotType}`;
                              const dragging = draggingItemRef.current;
                              const compatible = !dragging || dragging.slot === "-" || dragging.slot === slotType;
                              return (
                                <Card key={slotType}
                                  onDragOver={(e) => {
                                    e.preventDefault();
                                    const dragSlot = draggingItemRef.current?.slot ?? "-";
                                    e.dataTransfer.dropEffect = (dragSlot === "-" || dragSlot === slotType) ? "copy" : "none";
                                    setDragOverSlot(`${loadoutItem.id}:${slotType}`);
                                  }}
                                  onDragLeave={(e) => {
                                    if (!e.currentTarget.contains(e.relatedTarget as Node)) setDragOverSlot(null);
                                  }}
                                  onDrop={(e) => {
                                    e.preventDefault();
                                    setDragOverSlot(null);
                                    const itemName = e.dataTransfer.getData("text/plain");
                                    const dragSlot = e.dataTransfer.getData("application/ka-slot");
                                    if (!itemName) return;
                                    if (dragSlot && dragSlot !== "-" && dragSlot !== slotType) return;
                                    setLoadoutEntry(loadoutItem.id, slotType, { itemName });
                                  }}
                                  className={`shadow-none transition-all ${isOver && compatible ? "border-primary ring-2 ring-primary/30 bg-primary/5" : isOver && !compatible ? "border-destructive/40 bg-destructive/5" : "border-border"}`}>
                                  <CardHeader className="pb-1 pt-3 px-3">
                                    <div className="flex items-center justify-between gap-2">
                                      <div className="flex items-center gap-1.5">
                                        <SlotIcon slot={slotType} className="w-4 h-4 text-muted-foreground" />
                                        <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">{slotType}</span>
                                      </div>
                                    </div>
                                  </CardHeader>
                                  <CardContent className="px-3 pb-3 space-y-2">
                                    {!entry.itemName && (
                                      <div className={`flex flex-col items-center justify-center gap-1 py-3 rounded border border-dashed transition-colors ${isOver && compatible ? "border-primary text-primary" : "border-border text-muted-foreground/40"}`}>
                                        <SlotIcon slot={slotType} className="w-8 h-8" />
                                        <span className="text-[10px]">Drop here</span>
                                      </div>
                                    )}
                                    {entry.itemName && getEquipmentIcon(equipIcons, entry.itemName) && (
                                      <img src={getEquipmentIcon(equipIcons, entry.itemName)} alt={entry.itemName} className="w-10 h-10 object-contain rounded mx-auto" />
                                    )}
                                    <SearchableSelect
                                      value={entry.itemName}
                                      onChange={(v) => setLoadoutEntry(loadoutItem.id, slotType, { itemName: v })}
                                      options={eligibleItems.map((i) => ({ value: i.name, label: i.name }))}
                                      placeholder="- none -"
                                      triggerClassName="h-7 text-xs w-full"
                                    />
                                    {entry.itemName && (
                                      <div className="flex items-center gap-1.5">
                                        <span className="text-[10px] text-muted-foreground">Level</span>
                                        <NumInput value={entry.level} min={1} max={99}
                                          onChange={(v) => setLoadoutEntry(loadoutItem.id, slotType, { level: v })}
                                          className="h-7 w-16 text-xs text-center bg-background border-border/70"
                                          inputClassName="px-1" />
                                      </div>
                                    )}
                                    {item && entry.itemName && (
                                      <div className="text-[10px] text-muted-foreground space-y-0.5 mt-1">
                                        {STAT_ORDER.filter((s) => getItemStatAtLevel(item, s, entry.level) > 0).map((s) => {
                                          return (
                                            <div key={s} className="flex items-center justify-between">
                                              <span className="flex items-center gap-1">{statIcons[s] && <img src={statIcons[s]} alt={s} className="w-3 h-3 object-contain" />}{s}</span>
                                              <span className="font-medium text-foreground tabular-nums">{getItemStatAtLevel(item, s, entry.level)}</span>
                                            </div>
                                          );
                                        })}
                                      </div>
                                    )}
                                  </CardContent>
                                </Card>
                              );
                            })}
                          </div>
                          <div className="rounded-lg bg-muted/50 border border-border px-4 py-3">
                            <div className="flex items-center justify-between gap-3 mb-3">
                              <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">Loadout total</p>
                              <span className="text-xs text-muted-foreground">{loadoutItem.label}</span>
                            </div>
                            <div className="flex flex-wrap gap-x-5 gap-y-1.5">
                              {STAT_ORDER.map((stat) => (
                                <div key={stat} className="flex items-center gap-1.5 text-sm">
                                  {statIcons[stat] ? <img src={statIcons[stat]} alt={stat} className="w-4 h-4 object-contain" /> : <span className="text-xs text-muted-foreground">{stat}</span>}
                                  <span className={`font-semibold tabular-nums ${loadoutTotal?.totals[stat] === 0 ? "text-muted-foreground/40" : "text-foreground"}`}>{loadoutTotal?.totals[stat] || "-"}</span>
                                </div>
                              ))}
                            </div>
                          </div>
                        </CardContent>
                      </Card>
                    );
                  })}
                </div>
                <div className="rounded-lg bg-muted/50 border border-border px-4 py-3 mt-4">
                  <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-3">Equipment loadout Comparison</p>
                  <div className="space-y-3">
                    {loadoutTotals.map((loadoutItem) => (
                      <div key={loadoutItem.id} className="rounded-lg border border-border bg-background p-3">
                        <div className="flex items-center justify-between gap-3">
                          <div>
                            <div className="text-sm font-medium text-foreground">{loadoutItem.label}</div>
                            <div className="text-xs text-muted-foreground">{CHAR_SLOTS.map((slot) => loadout.find((lo) => lo.id === loadoutItem.id)?.slots[slot].itemName || "-").join(" | ")}</div>
                          </div>
                          <span className="text-[11px] text-muted-foreground">{Object.values(loadoutItem.totals).some((val) => val > 0) ? "Configured" : "Empty"}</span>
                        </div>
                        <div className="mt-3 flex flex-wrap gap-2">
                          {STAT_ORDER.filter((stat) => loadoutItem.totals[stat] > 0).map((stat) => (
                            <span key={stat} className="inline-flex items-center gap-1 rounded-full border border-border bg-muted/30 px-2 py-1 text-[11px]">
                              {statIcons[stat] && <img src={statIcons[stat]} alt={stat} className="w-3 h-3 object-contain" />}
                              <span className="font-medium">{stat}</span>
                              <span className="tabular-nums">{loadoutItem.totals[stat]}</span>
                            </span>
                          ))}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              </CardContent>
            </Card>

            {/* Change History */}
            {showHistory && (
              <Card className="shadow-sm mb-6">
                <CardHeader className="pb-2">
                  <div className="flex items-center justify-between">
                    <CardTitle className="text-base flex items-center gap-2"><History className="w-4 h-4" />Change History</CardTitle>
                    <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => setShowHistory(false)}><X className="w-3.5 h-3.5" /></Button>
                  </div>
                  <p className="text-xs text-muted-foreground">Every edit made to shared data - slots, stats, and icons.</p>
                </CardHeader>
                <CardContent className="pt-0">
                  {history.length === 0 ? (
                    <p className="text-sm text-muted-foreground text-center py-6">No changes yet. Make a slot or stat edit and it'll appear here.</p>
                  ) : (
                    <div className="space-y-2 max-h-80 overflow-y-auto pr-1">
                      {history.map((entry) => (
                        <div key={entry.id} className="flex items-start gap-3 text-sm">
                          <div className={`mt-0.5 w-6 h-6 rounded-full flex items-center justify-center text-white text-[10px] font-bold shrink-0 ${CHANGE_COLORS[entry.changeType] ?? "bg-muted-foreground"}`}>
                            {entry.userName.charAt(0).toUpperCase()}
                          </div>
                          <div className="flex-1 min-w-0">
                            <span className="font-medium text-foreground">{entry.userName}</span>
                            <span className="text-muted-foreground"> - {entry.description}</span>
                          </div>
                          <span className="text-xs text-muted-foreground whitespace-nowrap shrink-0">{relTime(entry.timestamp)}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </CardContent>
              </Card>
            )}
          </>
        )}

        <div className="mt-8 pt-4 border-t border-border text-xs text-muted-foreground flex items-center justify-between">
          <span>Data from Google Sheets | Kingdom Adventurers</span>
        </div>
      </div>
    </div>
  );
}





