import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { googleSheetUrl } from "@/lib/api";
import { useQuery } from "@tanstack/react-query";
import { Link, useLocation, useRoute } from "wouter";
import {
  CheckCircle2,
  ChevronDown,
  Hammer,
  Package,
  Leaf,
  Search,
  Shield,
  Sofa,
  Store,
  UtensilsCrossed,
  WandSparkles,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { CategoryBadge } from "@/components/ka/category-badge";
import { CostPills } from "@/components/ka/cost-pills";
import { DataCard } from "@/components/ka/data-card";
import { PageHeader } from "@/components/ka/page-header";
import { StatTable, StatTableHeaderCell } from "@/components/ka/stat-table";
import { EntityLink } from "@/components/ka/entity-link";
import { localSharedData } from "@/lib/local-shared-data";
import { getEquipmentIcon, getItemIcon, getFurnitureIcon, getFacilityIcon, getFacilityIconByName } from "@/lib/equipment-icons";
import { getSkillIcon } from "@/lib/skill-icons";
import { parseCsv } from "@/lib/monster-truth";
import {
  JOB_PARAMETER_ORDER,
  type JobParameterKey,
  type JobNeedExpProfile,
  parseJobNeedExpProfiles as parseJobNeedExpProfilesShared,
} from "@/game-data/job-needexp";
import { SHOP_RECORDS, type ShopRecord, type ShopSlug, type ShopBuilding, type ShopFacility } from "@/lib/shop-utils";
import { PLOT_SIZES, PLOT_TILES } from "@/game-data/buildings";
import { FACILITIES } from "@/game-data/facilities";
import { COLLECTION_ITEMS } from "@/game-data/collections";
import { TERRAIN_NAMES } from "@/game-data/terrain-labels";
import { ANIMAL_MONSTER_CARDS } from "@/lib/monster-cards";
import { FacilityCard } from "./houses";
import facilityLookupCsv from "../../../../data/Sheet csv/KA GameData - Facility_lookup.csv?raw";
import expCsv from "../../../../data/sheet-research/raw-copies/KA GameData - Exp.csv?raw";
import itemCsv from "../../../../data/sheet-research/raw-copies/KA GameData - Item.csv?raw";
import jobCsv from "../../../../data/Sheet csv/KA GameData - Job.csv?raw";

type EquipmentSlot = "Head" | "Weapon" | "Shield" | "Armor" | "Accessory" | "-";
type EquipmentRow = {
  sourceId: number | null;
  name: string;
  rank: string;
  slot: EquipmentSlot;
  weaponType: string;
  craftable: boolean;
  studioLevel: number;
  craftingIntelligence: number;
  attack: number;
  defence: number;
  speed: number;
  intelligence: number;
  luck: number;
  hp: number;
  mp: number;
};

type SkillRow = {
  name: string;
  studioLevel: number;
  craftingIntelligence: number;
  buyPrice: number;
  sellPrice: number;
  description?: string;
  weaponResistance?: string;
};

type ItemRow = {
  name: string;
  category: number;
  type: number;
  buyPrice: number;
  craftGroup: number;
  studioLevel: number;
  craftingIntelligence: number;
  craftTimeSeconds: number;
  bonusCategory: number;
  bonusType: number;
  bonusMinValue: number;
  bonusMaxValue: number;
  eggBonusType: number;
  eggBonusValue: number;
  eggBonusExp: number;
  eggBonusTime: number;
  shopFlag: number;
};

type FurnitureRow = {
  name: string;
  studioLevel: number;
  craftingIntelligence: number;
};

type ItemFacilityRow = {
  item: ItemRow;
  sources: string[];
  facilities: string[];
};
type ItemReferenceSortKey = "name" | "exp";
type AllyJobRank = "F" | "E" | "D" | "C" | "B" | "A" | "S";
type RankedJobNeedExpProfile = JobNeedExpProfile & {
  baseName: string;
  rank: AllyJobRank;
};

const CRAFTABLE_SKILL_NAMES = new Set([
  "2-Hit Attack",
  "3-Hit Attack",
  "4-Hit Attack",
  "5-Hit Attack",
  "Agriculturist",
  "Aid Specialist",
  "All-Out Sprint",
  "Area Attack Ⅰ",
  "Area Attack Ⅱ",
  "Arrow Rain",
  "Auto Recovery HP",
  "Auto Recovery MP",
  "Auto Recovery Vigor",
  "Axe Resistance",
  "Backup",
  "Battle Maniac",
  "Book Resistance",
  "Bow Resistance",
  "Chat Ⅰ",
  "Chat Ⅱ",
  "Chat Ⅲ",
  "Club Resistance",
  "Construction Chief",
  "Counter",
  "Craftsmanship",
  "Craftsmanship Ⅱ",
  "Craftsmanship Ⅲ",
  "Craftsmanship Ⅳ",
  "Craftsmanship Ⅴ",
  "Critical UP",
  "Culinarian",
  "Daring Charge",
  "De-Fogger",
  "Deployment Discount Ⅰ",
  "Deployment Discount Ⅱ",
  "Deployment Range Ⅰ",
  "Deployment Range Ⅱ",
  "Direct Attack Ⅰ",
  "Dodge UP",
  "Domestic Production",
  "Experience UP Ⅰ",
  "Experience UP Ⅱ",
  "Experience UP Ⅲ",
  "Facility Rec. UP HP",
  "Facility Rec. UP MP",
  "Facility Rec. UP Vigor",
  "Fire Magic Ⅰ",
  "Fire Magic Ⅱ",
  "Fire Magic Ⅲ",
  "Fire Magic Ⅳ",
  "Gun Resistance",
  "Half Reflect",
  "Hammer Resistance",
  "Heal L",
  "Heal M",
  "Heal Maddy",
  "Ice Magic Ⅰ",
  "Ice Magic Ⅱ",
  "Ice Magic Ⅲ",
  "Ice Magic Ⅳ",
  "Insta-Move",
  "Instant Construction",
  "Instant Treasure Analysis",
  "Instant Weeding",
  "Instant Workshop",
  "Instinct",
  "Leading the Charge",
  "Lightning Magic Ⅰ",
  "Lightning Magic Ⅱ",
  "Lightning Magic Ⅲ",
  "Lightning Magic Ⅳ",
  "Miner",
  "Move Speed UP",
  "Parry",
  "Perfect Dodge",
  "Ranch Know-How",
  "Research",
  "Research Ⅱ",
  "Research Ⅲ",
  "Research Ⅳ",
  "Research Ⅴ",
  "Revive 50%",
  "Round Trip",
  "Shield Resistance",
  "Skilled Craftsman Ⅰ",
  "Skilled Craftsman Ⅱ",
  "Spear Resistance",
  "Staff Resistance",
  "Stealth",
  "Strategic Retreat",
  "Stubborn",
  "Sword Resistance",
  "Thief",
  "Transport Corps",
  "Treasure Analysis",
]);

// JOB_PARAMETER_ORDER and JobParameterKey are now imported from @/game-data/job-needexp
const ALLY_STAT_MAX = 999;
const ALLY_JOB_RANKS: AllyJobRank[] = ["F", "E", "D", "C", "B", "A", "S"];
const DEFAULT_ALLY_JOB_RANK: AllyJobRank = "D";
const CANONICAL_JOB_NAME_SET = new Set(
  Object.keys(((localSharedData as { jobs?: Record<string, unknown> }).jobs ?? {})).map((name) => name.trim().toLowerCase())
);
const JOB_PARAMETER_DISPLAY_LABELS: Record<JobParameterKey, string> = {
  HP: "HP",
  MP: "MP",
  Vigor: "VIG",
  ATK: "ATK",
  DEF: "DEF",
  SPEED: "SPD",
  LUCK: "LUK",
  "Owned?": "OWN",
  INT: "INT",
  DEX: "DEX",
  CONS: "GAT",
  MOVE: "MOV",
  Heart: "HRT",
};
const JOB_PARAMETER_ICON_KEYS: Partial<Record<JobParameterKey, string>> = {
  HP: "HP",
  MP: "MP",
  Vigor: "Vigor",
  ATK: "Attack",
  DEF: "Defence",
  SPEED: "Speed",
  LUCK: "Luck",
  INT: "Intelligence",
  DEX: "Dexterity",
  CONS: "Gather",
  MOVE: "Move",
  Heart: "Heart",
};
const FEED_FILTER_STAT_KEYS = JOB_PARAMETER_ORDER.filter((key) => key !== "Owned?");

// JobNeedExpProfile is now imported from @/game-data/job-needexp

const ITEM_SOURCE_ORDER = ["Item Shop", "Restaurant", "Orchard", "Facility only", "Other sources"] as const;
const NO_FACILITY_SOURCE_FILTER = "__none__";

type SharedDataShape = {
  slotAssignments?: Record<string, string>;
  weaponTypes?: Record<string, string>;
  skills?: Record<string, SkillRow>;
  statIcons?: Record<string, string>;
};

const EQUIP_SHEET_URL = googleSheetUrl("equipment");
const ITEM_SHEET_URL = googleSheetUrl("shops-items");
const CRAFTABLE_ITEM_FLAG = 128;
const COOKED_ITEM_FLAG = 1024;
const AWAKENING_LEVEL_BONUS = 30;
const MAX_AWAKENING = 99;
const ORCHARD_FRUIT_TREE_CRAFT_GROUP = 70;
const KNOWN_ITEM_SHOP_FLAGS: Array<{ mask: number; label: string }> = [
  { mask: CRAFTABLE_ITEM_FLAG, label: "CRAFTABLE_ITEM_FLAG (128)" },
  { mask: COOKED_ITEM_FLAG, label: "COOKED_ITEM_FLAG (1024)" },
];
const ALLY_PLANNER_STORAGE_KEY = "ka.shops.ally-feed-planner.v1";
const FALLBACK_ITEM_CRAFT_FACILITIES = ["Item Workbench"];
const FALLBACK_COOKED_CRAFT_FACILITIES = ["Cooking Station"];
const FACILITY_SOURCE_ICON_ALIAS: Record<string, string> = {
  "Simple Stove": "Stove",
};
const VALID_SLOTS: EquipmentSlot[] = ["Head", "Weapon", "Shield", "Armor", "Accessory", "-"];
const EQUIPMENT_VARIANT_NAME_BY_ID: Record<number, string> = {
  192: "B/ Legendary Shield (B)",
  198: "B/ Legendary Shield (R)",
  235: "E/ Hat (B)",
  237: "E/ Hat (R)",
};
const RANKS = ["All", "B", "C", "D", "E", "F"] as const;
const EXCLUDED_SKILLS = new Set(["normal attack", "gun attack", "critical hit"]);
const FURNITURE_ROWS: FurnitureRow[] = [
  { name: "Candle", studioLevel: 1, craftingIntelligence: 7 },
  { name: "Kitchen Shelves", studioLevel: 1, craftingIntelligence: 8 },
  { name: "Desk", studioLevel: 1, craftingIntelligence: 8 },
  { name: "Red Carpet", studioLevel: 2, craftingIntelligence: 13 },
  { name: "Decorative Plant", studioLevel: 2, craftingIntelligence: 17 },
  { name: "Dining Table", studioLevel: 2, craftingIntelligence: 17 },
  { name: "Study Desk", studioLevel: 2, craftingIntelligence: 21 },
  { name: "Rainwater Barrel", studioLevel: 2, craftingIntelligence: 25 },
  { name: "Chest of Drawers", studioLevel: 2, craftingIntelligence: 29 },
  { name: "Flower Vase", studioLevel: 3, craftingIntelligence: 23 },
  { name: "Shelf", studioLevel: 3, craftingIntelligence: 32 },
  { name: "Bookshelf", studioLevel: 4, craftingIntelligence: 53 },
  { name: "Training Room", studioLevel: 4, craftingIntelligence: 85 },
  { name: "Rejuvenating Bath", studioLevel: 4, craftingIntelligence: 85 },
  { name: "Flowers", studioLevel: 5, craftingIntelligence: 55 },
  { name: "Tomato", studioLevel: 5, craftingIntelligence: 80 },
  { name: "Dresser", studioLevel: 5, craftingIntelligence: 80 },
  { name: "Couch", studioLevel: 5, craftingIntelligence: 155 },
  { name: "Bathtub", studioLevel: 5, craftingIntelligence: 155 },
  { name: "Stove", studioLevel: 5, craftingIntelligence: 155 },
  { name: "Pansy", studioLevel: 6, craftingIntelligence: 77 },
  { name: "Shooting Range", studioLevel: 7, craftingIntelligence: 293 },
  { name: "Fluffy Carpet", studioLevel: 7, craftingIntelligence: 152 },
  { name: "Cooking Counter", studioLevel: 7, craftingIntelligence: 201 },
  { name: "Decorative Armor", studioLevel: 8, craftingIntelligence: 261 },
  { name: "Vanity Mirror", studioLevel: 8, craftingIntelligence: 261 },
  { name: "Window", studioLevel: 9, craftingIntelligence: 329 },
  { name: "Magic Training Ground", studioLevel: 9, craftingIntelligence: 500 },
  { name: "Glittering Stone", studioLevel: 10, craftingIntelligence: 405 },
  { name: "Black Mat", studioLevel: 11, craftingIntelligence: 489 },
  { name: "Fireplace", studioLevel: 11, craftingIntelligence: 489 },
  { name: "Tree Nursery", studioLevel: 12, craftingIntelligence: 293 },
  { name: "Ancestor Statue", studioLevel: 12, craftingIntelligence: 500 },
  { name: "Animal Figurine", studioLevel: 13, craftingIntelligence: 500 },
  { name: "Tool Workshop", studioLevel: 16, craftingIntelligence: 500 },
  { name: "Ore Workbench", studioLevel: 23, craftingIntelligence: 500 },
  { name: "Double Bed", studioLevel: 25, craftingIntelligence: 500 },
];

const SHOP_ICONS: Record<ShopSlug, ReactNode> = {
  "items-reference": <Package className="w-5 h-5 text-indigo-500" />,
  "weapon-shop": <Hammer className="w-5 h-5 text-amber-500" />,
  "armor-shop": <Shield className="w-5 h-5 text-sky-500" />,
  "accessory-shop": <Store className="w-5 h-5 text-violet-500" />,
  "item-shop": <Package className="w-5 h-5 text-emerald-500" />,
  "furniture-shop": <Sofa className="w-5 h-5 text-orange-500" />,
  restaurant: <UtensilsCrossed className="w-5 h-5 text-rose-500" />,
  "skill-shop": <WandSparkles className="w-5 h-5 text-cyan-500" />,
  insectarium: <Store className="w-5 h-5 text-amber-500" />,
  aquarium: <Store className="w-5 h-5 text-sky-500" />,
  museum: <Store className="w-5 h-5 text-violet-500" />,
  zoo: <Store className="w-5 h-5 text-lime-500" />,
  orchard: <Leaf className="w-5 h-5 text-lime-600" />,
};

function isFurnitureIconPath(icon: string | undefined): boolean {
  return typeof icon === "string" && icon.includes("/website_icons/furniture/");
}

function normalizeIconName(value: string): string {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

const FURNITURE_LARGE_ICON_OUTLIERS = new Set([
  "double bed",
  "tree nursery",
  "candle",
  "desk",
  "dining table",
]);

function furnitureRowIconClass(name: string): string {
  return FURNITURE_LARGE_ICON_OUTLIERS.has(normalizeIconName(name)) ? "h-14 w-14" : "h-10 w-10";
}

function resolveShopCardIcon(shop: ShopRecord): string | undefined {
  if (shop.workbench?.id != null) {
    const workbenchIcon = getFacilityIcon(shop.workbench.id);
    if (workbenchIcon) return workbenchIcon;
  }
  return getFurnitureIcon(shop.title) ?? getItemIcon(shop.title);
}

function resolveItemReferenceFacilityIcon(facilityName: string, facilityId: number | undefined): string | undefined {
  const aliasedName = FACILITY_SOURCE_ICON_ALIAS[facilityName] ?? facilityName;
  // Item reference tags are mostly furniture-facing names, so prefer furniture icon first.
  return (
    getFurnitureIcon(aliasedName)
    ?? getFacilityIconByName(facilityName)
    ?? (facilityId != null ? getFacilityIcon(facilityId) : undefined)
  );
}

const ITEMS_REFERENCE_SHOPS = SHOP_RECORDS.filter((shop) => shop.slug === "items-reference");
const PRIMARY_SHOPS = SHOP_RECORDS.filter((shop) => shop.category === "shop" && shop.slug !== "items-reference");
const COPPER_SHOPS = PRIMARY_SHOPS.filter((shop) => shop.currency !== "silver");
const SILVER_SHOPS = PRIMARY_SHOPS.filter((shop) => shop.currency === "silver");
const ZOO_ANIMALS = ANIMAL_MONSTER_CARDS.filter((animal) => animal.silverPrice > 0);
const SECONDARY_FACILITIES = SHOP_RECORDS.filter((shop) => shop.category === "facility");

function getRank(name: string): string {
  const match = name.trim().match(/^([FSABCDE])\s*\//i);
  return match ? match[1].toUpperCase() : "";
}

function isPlayerFacingEquipmentName(name: string): boolean {
  return /^[FSABCDE]\s*\/\s*/i.test(name);
}

function getNumeric(cells: Array<{ v: string | number | null } | null>, index: number): number {
  const value = index >= 0 && index < cells.length ? cells[index]?.v : null;
  return Number(value) || 0;
}

function getText(cells: Array<{ v: string | number | null } | null>, index: number): string {
  const value = index >= 0 && index < cells.length ? cells[index]?.v : null;
  return String(value ?? "").trim();
}

function findColumnIndex(cols: string[], patterns: RegExp[]): number {
  return cols.findIndex((col) => patterns.some((pattern) => pattern.test(col)));
}

function fallbackIndex(index: number, fallback: number): number {
  return index >= 0 ? index : fallback;
}

function parseFacilityCraftGroupMap(rawCsv: string): Map<number, string[]> {
  const rows = parseCsv(rawCsv);
  if (rows.length === 0) return new Map();
  const headerRowIndex = rows.findIndex((row) => {
    const normalized = row.map((cell) => cell.trim());
    return normalized.includes("name") && normalized.includes("craftGroup");
  });
  if (headerRowIndex < 0) return new Map();

  const header = rows[headerRowIndex].map((cell) => cell.trim());
  const craftGroupIndex = header.findIndex((cell) => /^craftGroup$/i.test(cell));
  const nameIndex = header.findIndex((cell) => /^name$/i.test(cell));
  if (craftGroupIndex < 0 || nameIndex < 0) return new Map();

  const map = new Map<number, Set<string>>();
  for (const row of rows.slice(headerRowIndex + 1)) {
    const name = String(row[nameIndex] ?? "").trim();
    const craftGroup = Number(row[craftGroupIndex] ?? "");
    if (!name || name === "#N/A" || !Number.isFinite(craftGroup) || craftGroup < 0) continue;
    const bucket = map.get(craftGroup) ?? new Set<string>();
    bucket.add(name);
    map.set(craftGroup, bucket);
  }

  const output = new Map<number, string[]>();
  for (const [group, names] of map.entries()) {
    output.set(group, Array.from(names).sort((a, b) => a.localeCompare(b)));
  }
  return output;
}

function parseExpByLevel(rawCsv: string): Map<number, number> {
  const rows = parseCsv(rawCsv);
  if (rows.length === 0) return new Map();
  const headerRowIndex = rows.findIndex((row) => {
    const normalized = row.map((cell) => cell.trim());
    return normalized.includes("id") && normalized.includes("exp");
  });
  if (headerRowIndex < 0) return new Map();

  const header = rows[headerRowIndex].map((cell) => cell.trim());
  const levelIndex = header.findIndex((cell) => /^id$/i.test(cell));
  const expIndex = header.findIndex((cell) => /^exp$/i.test(cell));
  if (levelIndex < 0 || expIndex < 0) return new Map();

  const map = new Map<number, number>();
  for (const row of rows.slice(headerRowIndex + 1)) {
    const level = Number(row[levelIndex] ?? "");
    const exp = Number(row[expIndex] ?? "");
    // Row N is EXP to go from Lv N -> Lv N+1, so only rows below cap are usable.
    if (!Number.isFinite(level) || level < 1 || level >= ALLY_STAT_MAX || !Number.isFinite(exp) || exp < 0) continue;
    map.set(level, exp);
  }
  return map;
}

function parseJobNeedExpProfiles(rawCsv: string): JobNeedExpProfile[] {
  return parseJobNeedExpProfilesShared(rawCsv, CANONICAL_JOB_NAME_SET);
}

function parseRankedJobNeedExpProfiles(rawCsv: string): RankedJobNeedExpProfile[] {
  const rows = parseCsv(rawCsv);
  if (rows.length === 0) return [];

  const headerRowIndex = rows.findIndex((row) => {
    const normalized = row.map((cell) => cell.trim());
    return normalized.includes("id") && normalized.includes("name") && normalized.includes("maxLevel");
  });
  if (headerRowIndex < 0) return [];

  const header = rows[headerRowIndex].map((cell) => cell.trim());
  const nameIndex = header.findIndex((cell) => /^name$/i.test(cell));
  const statStartIndex = header.findIndex((cell) => /^maxLevel$/i.test(cell));
  if (nameIndex < 0 || statStartIndex < 0) return [];

  const gradePrefix = /^(S|A|B|C|D|E|F)\s+(Grade|Rank)\s+(.+)$/i;
  const profiles = new Map<string, RankedJobNeedExpProfile>();

  for (const row of rows.slice(headerRowIndex + 1)) {
    const rawName = String(row[nameIndex] ?? "").trim();
    const match = rawName.match(gradePrefix);
    if (!match) continue;

    const rank = match[1].toUpperCase() as AllyJobRank;
    const baseName = match[3].trim();
    if (!baseName || !ALLY_JOB_RANKS.includes(rank)) continue;
    if (CANONICAL_JOB_NAME_SET.size > 0 && !CANONICAL_JOB_NAME_SET.has(baseName.toLowerCase())) continue;

    const profileKey = `${baseName}::${rank}`;
    if (profiles.has(profileKey)) continue;

    const needExpByParameter = {} as Record<JobParameterKey, number>;
    const maxLevelByParameter = {} as Record<JobParameterKey, number>;

    JOB_PARAMETER_ORDER.forEach((parameter, index) => {
      const maxLevelIndex = statStartIndex + (index * 5);
      const needExpIndex = statStartIndex + (index * 5) + 1;
      const maxLevelValue = Number(row[maxLevelIndex] ?? "");
      const needExpValue = Number(row[needExpIndex] ?? "");
      maxLevelByParameter[parameter] = Number.isFinite(maxLevelValue) && maxLevelValue > 0 ? maxLevelValue : 1;
      needExpByParameter[parameter] = Number.isFinite(needExpValue) && needExpValue > 0 ? needExpValue : 100;
    });

    profiles.set(profileKey, {
      name: baseName,
      baseName,
      rank,
      needExpByParameter,
      maxLevelByParameter,
    });
  }

  const rankOrder: Record<AllyJobRank, number> = { F: 0, E: 1, D: 2, C: 3, B: 4, A: 5, S: 6 };
  return Array.from(profiles.values()).sort((left, right) => {
    const byName = left.baseName.localeCompare(right.baseName);
    if (byName !== 0) return byName;
    return rankOrder[left.rank] - rankOrder[right.rank];
  });
}

function isLvLimitBonus(item: ItemRow): boolean {
  return item.bonusCategory === 2 && item.bonusType > 0;
}

const BONUS_TYPE_PARAMETER_LABELS: Record<number, string[]> = {
  10: ["HP"],
  11: ["MP"],
  12: ["VIG"],
  13: ["ATK"],
  14: ["DEF"],
  15: ["SPD"],
  16: ["LUK"],
  18: ["INT"],
  19: ["DEX"],
  20: ["GAT"],
  21: ["MOV"],
  22: ["HRT"],
  31: ["HP", "MP", "VIG"],
  32: ["ATK", "DEF", "SPD", "LUK"],
  33: ["INT", "DEX", "GAT", "MOV", "HRT"],
  34: ["HP", "MP", "VIG", "ATK", "DEF", "SPD", "LUK", "INT", "DEX", "GAT", "MOV", "HRT"],
};

const BONUS_TYPE_PARAMETER_KEYS: Record<number, JobParameterKey[]> = {
  10: ["HP"],
  11: ["MP"],
  12: ["Vigor"],
  13: ["ATK"],
  14: ["DEF"],
  15: ["SPEED"],
  16: ["LUCK"],
  18: ["INT"],
  19: ["DEX"],
  20: ["CONS"],
  21: ["MOVE"],
  22: ["Heart"],
  31: ["HP", "MP", "Vigor"],
  32: ["ATK", "DEF", "SPEED", "LUCK"],
  33: ["INT", "DEX", "CONS", "MOVE", "Heart"],
  34: ["HP", "MP", "Vigor", "ATK", "DEF", "SPEED", "LUCK", "INT", "DEX", "CONS", "MOVE", "Heart"],
};

function formatAllyBonusType(item: ItemRow): string {
  if (item.bonusType <= 0) return "-";
  if (isLvLimitBonus(item)) {
    return `BONUS_TYPE_LV_LIMIT (${item.bonusType})`;
  }
  return `BONUS_TYPE_${item.bonusType}`;
}

function formatAllyBonusRange(minValue: number, maxValue: number): string {
  const min = Number.isFinite(minValue) && minValue > 0 ? minValue : 0;
  const max = Number.isFinite(maxValue) && maxValue > 0 ? maxValue : 0;
  if (min <= 0 && max <= 0) return "-";
  if (min > 0 && max > 0) {
    if (min === max) return `+${min}`;
    return `+${min} to +${max}`;
  }
  const only = max > 0 ? max : min;
  return `+${only}`;
}

function renderAllyBonusEffect(item: ItemRow, statIcons: Record<string, string>, multiplier = 1): ReactNode {
  const labels = BONUS_TYPE_PARAMETER_LABELS[item.bonusType] ?? [];
  const base = Number.isFinite(item.bonusMinValue) && item.bonusMinValue > 0 ? item.bonusMinValue : 0;
  if (base <= 0) return "-";

  const value = base * multiplier;
  if (labels.length === 0) {
    return "Bonus";
  }

  const iconKeyByLabel: Record<string, string> = {
    HP: "HP",
    MP: "MP",
    ENG: "Vigor",
    VIG: "Vigor",
    ATK: "Attack",
    DEF: "Defence",
    SPD: "Speed",
    LUK: "Luck",
    INT: "Intelligence",
    DEX: "Dexterity",
    GAT: "Gather",
    MOV: "Move",
    HRT: "Heart",
  };

  if (labels.length === 1) {
    const label = labels[0];
    const iconSrc = statIcons[iconKeyByLabel[label] ?? ""];
    return (
      <span className="inline-flex items-center gap-1">
        {iconSrc ? <img src={iconSrc} alt={label} className="h-3.5 w-3.5 object-contain" /> : null}
        <span>{label}</span>
      </span>
    );
  }

  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      {labels.map((label) => {
        const iconSrc = statIcons[iconKeyByLabel[label] ?? ""];
        return (
          <span key={label} className="inline-flex items-center gap-1">
            {iconSrc ? <img src={iconSrc} alt={label} className="h-3.5 w-3.5 object-contain" /> : null}
            <span>{label}</span>
          </span>
        );
      })}
    </span>
  );
}

function getAllyExpGained(item: ItemRow, multiplier = 1): number {
  const base = Number.isFinite(item.bonusMinValue) && item.bonusMinValue > 0 ? item.bonusMinValue : 0;
  return base * multiplier;
}

function hasFeedExp(item: ItemRow): boolean {
  return item.bonusMinValue > 0 && (BONUS_TYPE_PARAMETER_KEYS[item.bonusType]?.length ?? 0) > 0;
}

function getTotalExpNeeded(expByLevel: Map<number, number>, currentLevel: number, targetLevel: number, needExpPercent = 100): number {
  if (targetLevel <= currentLevel) return 0;
  let total = 0;
  for (let level = currentLevel; level < targetLevel; level += 1) {
    const baseExp = expByLevel.get(level) ?? 0;
    total += Math.floor((baseExp * needExpPercent) / 100);
  }
  return total;
}

async function fetchEquipmentRows(): Promise<EquipmentRow[]> {
  const response = await fetch(EQUIP_SHEET_URL);
  const text = await response.text();
  const json = text.replace(/^[^(]+\(/, "").replace(/\);?\s*$/, "");
  const data = JSON.parse(json);
  const cols: string[] = data.table.cols.map((col: { label?: string; id?: string }) => (col.label || col.id || "").trim());
  const rows = data.table.rows as Array<{ c: Array<{ v: string | number | null } | null> }>;

  const nameIndex = cols.findIndex((col) => /^(name|item.?name|equipment.?name)$/i.test(col));
  const slotIndex = cols.findIndex((col) => /^(slot|type|equip.?type|category|kind)$/i.test(col));
  const studioIndex = cols.findIndex((col) => /crafterstudio|studio.?level|crafter.?studio/i.test(col));
  const intIndex = cols.findIndex((col) => /craftermintelligence|crafter.?intel|craft.*int/i.test(col));

  const shared = localSharedData as SharedDataShape;
  const slotAssignments = shared.slotAssignments ?? {};
  const weaponTypes = shared.weaponTypes ?? {};

  return rows
    .map((row) => {
      const cells = row.c ?? [];
      const name = String(cells[nameIndex]?.v ?? "").trim();
      if (!name || !isPlayerFacingEquipmentName(name)) return null;
      const rawSourceId = Number(cells[0]?.v ?? Number.NaN);
      const sourceId = Number.isFinite(rawSourceId) ? rawSourceId : null;
      const displayName = sourceId === null ? name : (EQUIPMENT_VARIANT_NAME_BY_ID[sourceId] ?? name);
      const sheetSlot = String(cells[slotIndex]?.v ?? "").trim();
      const assignedSlot = slotAssignments[displayName] ?? slotAssignments[name];
      const slot = VALID_SLOTS.includes(assignedSlot as EquipmentSlot)
        ? assignedSlot as EquipmentSlot
        : VALID_SLOTS.includes(sheetSlot as EquipmentSlot)
          ? sheetSlot as EquipmentSlot
          : "-";

      const craftable = getNumeric(cells, studioIndex) > 0;
      if (!craftable) return null;

      const equipmentRow: EquipmentRow = {
        sourceId,
        name: displayName,
        rank: getRank(displayName),
        slot,
        weaponType: weaponTypes[displayName] ?? weaponTypes[name] ?? "",
        craftable: true,
        studioLevel: getNumeric(cells, studioIndex),
        craftingIntelligence: getNumeric(cells, intIndex),
        hp: getNumeric(cells, cols.findIndex((col) => /^hp$/i.test(col))),
        mp: getNumeric(cells, cols.findIndex((col) => /^mp$/i.test(col))),
        attack: getNumeric(cells, cols.findIndex((col) => /^atk$|^attack$/i.test(col))),
        defence: getNumeric(cells, cols.findIndex((col) => /^def$|^defence$|^defense$/i.test(col))),
        speed: getNumeric(cells, cols.findIndex((col) => /^spd$|^speed$/i.test(col))),
        intelligence: getNumeric(cells, cols.findIndex((col) => /^int$|^intelligence$/i.test(col))),
        luck: getNumeric(cells, cols.findIndex((col) => /^lck$|^luck$/i.test(col))),
      };
      return equipmentRow;
    })
    .filter((row): row is EquipmentRow => !!row);
}

async function fetchItemRows(): Promise<ItemRow[]> {
  const response = await fetch(ITEM_SHEET_URL);
  const text = await response.text();
  const json = text.replace(/^[^(]+\(/, "").replace(/\);?\s*$/, "");
  const data = JSON.parse(json);
  const cols: string[] = data.table.cols.map((col: { label?: string; id?: string }) => (col.label || col.id || "").trim());
  const rows = data.table.rows as Array<{ c: Array<{ v: string | number | null } | null> }>;

  const nameIndex = fallbackIndex(findColumnIndex(cols, [/^name$/i]), 1);
  const categoryIndex = fallbackIndex(findColumnIndex(cols, [/^category$/i]), 2);
  const typeIndex = fallbackIndex(findColumnIndex(cols, [/^type$/i]), 3);
  const buyPriceIndex = fallbackIndex(findColumnIndex(cols, [/prices\/buyprice/i, /buyprice/i, /prices\/buy/i]), 14);
  const craftGroupIndex = fallbackIndex(findColumnIndex(cols, [/^craftgroup$/i]), 6);
  const studioIndex = fallbackIndex(findColumnIndex(cols, [/prices\/craftTermStudioLevel/i, /crafttermstudiolevel/i]), 28);
  const intIndex = fallbackIndex(findColumnIndex(cols, [/prices\/craftTermIntelligence/i, /crafttermintelligence/i]), 29);
  const timeIndex = fallbackIndex(findColumnIndex(cols, [/prices\/craftTimeSeconds/i, /crafttimeseconds/i]), 30);
  const bonusCategoryIndex = fallbackIndex(findColumnIndex(cols, [/prices\/bonusCategory/i, /bonuscategory/i]), 18);
  const bonusTypeIndex = fallbackIndex(findColumnIndex(cols, [/prices\/bonusType/i, /bonustype/i]), 19);
  const bonusMinValueIndex = fallbackIndex(findColumnIndex(cols, [/prices\/bonusMinValue/i, /bonusminvalue/i]), 20);
  const bonusMaxValueIndex = fallbackIndex(findColumnIndex(cols, [/prices\/bonusMaxValue/i, /bonusmaxvalue/i]), 21);
  const eggTypeIndex = fallbackIndex(findColumnIndex(cols, [/prices\/eggBonusType/i, /eggbonustype/i]), 22);
  const eggValueIndex = fallbackIndex(findColumnIndex(cols, [/prices\/eggBonusValue/i, /eggbonusvalue/i]), 23);
  const eggExpIndex = fallbackIndex(findColumnIndex(cols, [/prices\/eggBonusExp/i, /eggbonusexp/i]), 24);
  const eggTimeIndex = fallbackIndex(findColumnIndex(cols, [/prices\/eggBonusTime/i, /eggbonustime/i]), 25);
  const shopFlagIndices = cols
    .map((col, index) => (/^flag$/i.test(col) ? index : -1))
    .filter((index) => index >= 0);
  const fallbackShopFlagIndex = fallbackIndex(findColumnIndex(cols, [/^flag$/i]), 32);

  const sheetRows = rows
    .map((row) => {
      const cells = row.c ?? [];
      const name = getText(cells, nameIndex);
      const studioLevel = getNumeric(cells, studioIndex);
      const craftingIntelligence = getNumeric(cells, intIndex);
      if (!name) return null;

      const mergedShopFlag = (shopFlagIndices.length > 0 ? shopFlagIndices : [fallbackShopFlagIndex])
        .reduce((acc, index) => {
          const value = Number(cells[index]?.v ?? 0);
          return acc | (Number.isFinite(value) ? value : 0);
        }, 0);

      return {
        name,
        category: getNumeric(cells, categoryIndex),
        type: getNumeric(cells, typeIndex),
        buyPrice: getNumeric(cells, buyPriceIndex),
        craftGroup: getNumeric(cells, craftGroupIndex),
        studioLevel,
        craftingIntelligence,
        craftTimeSeconds: getNumeric(cells, timeIndex),
        bonusCategory: getNumeric(cells, bonusCategoryIndex),
        bonusType: getNumeric(cells, bonusTypeIndex),
        bonusMinValue: getNumeric(cells, bonusMinValueIndex),
        bonusMaxValue: getNumeric(cells, bonusMaxValueIndex),
        eggBonusType: getNumeric(cells, eggTypeIndex),
        eggBonusValue: getNumeric(cells, eggValueIndex),
        eggBonusExp: getNumeric(cells, eggExpIndex),
        eggBonusTime: getNumeric(cells, eggTimeIndex),
        shopFlag: mergedShopFlag,
      };
    })
    .filter((row): row is ItemRow => !!row);

  const fallbackRows = parseItemRowsFromCsv(itemCsv);
  if (fallbackRows.length === 0) {
    return sheetRows.sort((a, b) => {
      const studioDiff = a.studioLevel - b.studioLevel;
      return studioDiff !== 0 ? studioDiff : a.name.localeCompare(b.name);
    });
  }

  const mergedByName = new Map<string, ItemRow>();
  for (const row of fallbackRows) mergedByName.set(row.name, row);
  for (const row of sheetRows) mergedByName.set(row.name, { ...mergedByName.get(row.name), ...row });

  return Array.from(mergedByName.values()).sort((a, b) => {
    const studioDiff = a.studioLevel - b.studioLevel;
    return studioDiff !== 0 ? studioDiff : a.name.localeCompare(b.name);
  });
}

function parseItemRowsFromCsv(rawCsv: string): ItemRow[] {
  const rows = parseCsv(rawCsv);
  if (rows.length === 0) return [];

  const headerRow = rows.find((row) => row.some((cell) => /^id$/i.test(String(cell).trim())) && row.some((cell) => /^name$/i.test(String(cell).trim())));
  if (!headerRow) return [];

  const header = headerRow.map((cell) => String(cell).trim());
  const dataStart = rows.indexOf(headerRow) + 1;
  const indexOf = (label: string, fallback = -1) => {
    const index = header.findIndex((cell) => cell.toLowerCase() === label.toLowerCase());
    return index >= 0 ? index : fallback;
  };

  const nameIndex = indexOf("name", 1);
  const categoryIndex = indexOf("category", 2);
  const typeIndex = indexOf("type", 3);
  const buyPriceIndex = indexOf("buyPrice", 14);
  const craftGroupIndex = indexOf("craftGroup", 6);
  const bonusCategoryIndex = indexOf("bonusCategory", 18);
  const bonusTypeIndex = indexOf("bonusType", 19);
  const bonusMinValueIndex = indexOf("bonusMinValue", 20);
  const bonusMaxValueIndex = indexOf("bonusMaxValue", 21);
  const eggTypeIndex = indexOf("eggBonusType", 22);
  const eggValueIndex = indexOf("eggBonusValue", 23);
  const eggExpIndex = indexOf("eggBonusExp", 24);
  const eggTimeIndex = indexOf("eggBonusTime", 25);
  const studioIndex = indexOf("craftTermStudioLevel", 28);
  const intIndex = indexOf("craftTermIntelligence", 29);
  const timeIndex = indexOf("craftTimeSeconds", 30);
  const flagIndexes = header.map((cell, index) => (/^flag$/i.test(cell) ? index : -1)).filter((index) => index >= 0);

  const output: ItemRow[] = [];
  for (const row of rows.slice(dataStart)) {
    const name = String(row[nameIndex] ?? "").trim();
    if (!name) continue;

    const mergedShopFlag = (flagIndexes.length > 0 ? flagIndexes : [32])
      .reduce((acc, index) => {
        const value = Number(row[index] ?? 0);
        return acc | (Number.isFinite(value) ? value : 0);
      }, 0);

    output.push({
      name,
      category: Number(row[categoryIndex] ?? 0) || 0,
      type: Number(row[typeIndex] ?? 0) || 0,
      buyPrice: Number(row[buyPriceIndex] ?? 0) || 0,
      craftGroup: Number(row[craftGroupIndex] ?? 0) || 0,
      studioLevel: Number(row[studioIndex] ?? 0) || 0,
      craftingIntelligence: Number(row[intIndex] ?? 0) || 0,
      craftTimeSeconds: Number(row[timeIndex] ?? 0) || 0,
      bonusCategory: Number(row[bonusCategoryIndex] ?? 0) || 0,
      bonusType: Number(row[bonusTypeIndex] ?? 0) || 0,
      bonusMinValue: Number(row[bonusMinValueIndex] ?? 0) || 0,
      bonusMaxValue: Number(row[bonusMaxValueIndex] ?? 0) || 0,
      eggBonusType: Number(row[eggTypeIndex] ?? 0) || 0,
      eggBonusValue: Number(row[eggValueIndex] ?? 0) || 0,
      eggBonusExp: Number(row[eggExpIndex] ?? 0) || 0,
      eggBonusTime: Number(row[eggTimeIndex] ?? 0) || 0,
      shopFlag: mergedShopFlag,
    });
  }

  return output;
}

function ShopHeader({ selectedShop }: {
  selectedShop?: ShopRecord | null;
}) {
  return (
    <PageHeader
      className="mb-6"
      icon={selectedShop ? SHOP_ICONS[selectedShop.slug] : <Store className="w-5 h-5 text-indigo-500" />}
      title={selectedShop ? selectedShop.title : "Shops and items"}
    >
      <p>
        {selectedShop
          ? selectedShop.description
          : "Browse shops, item requirements, and ally feed planning data in one place."}
      </p>
    </PageHeader>
  );
}

function ShopTabs({ currentSlug }: { currentSlug?: ShopSlug }) {
  return (
    <div className="space-y-2 mb-6">
      <div className="flex flex-wrap gap-2">
        {[...COPPER_SHOPS, ...SILVER_SHOPS].map((shop) => (
          <Link key={shop.slug} href={`/shops/${shop.slug}`}>
            <button
              className={`inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-xs font-medium transition-colors ${
                currentSlug === shop.slug
                  ? "border-primary bg-primary/10 text-primary"
                  : "border-border bg-background text-muted-foreground hover:text-foreground hover:border-primary/40"
              }`}
            >
              {SHOP_ICONS[shop.slug]}
              {shop.shortTitle}
            </button>
          </Link>
        ))}
      </div>
      {SECONDARY_FACILITIES.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 border-t border-border/60 pt-2">
          <span className="text-[11px] font-medium text-muted-foreground mr-1">Other Facilities</span>
          {SECONDARY_FACILITIES.map((shop) => (
            <Link key={shop.slug} href={`/shops/${shop.slug}`}>
              <button
                className={`inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-xs font-medium transition-colors ${
                  currentSlug === shop.slug
                    ? "border-primary bg-primary/10 text-primary"
                    : "border-border bg-background text-muted-foreground hover:text-foreground hover:border-primary/40"
                }`}
              >
                {SHOP_ICONS[shop.slug]}
                {shop.shortTitle}
              </button>
            </Link>
          ))}
        </div>
      )}
      {ITEMS_REFERENCE_SHOPS.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 border-t border-border/60 pt-2">
          <span className="text-[11px] font-medium text-muted-foreground mr-1">References</span>
          {ITEMS_REFERENCE_SHOPS.map((shop) => (
            <Link key={shop.slug} href={`/shops/${shop.slug}`}>
              <button
                className={`inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-xs font-medium transition-colors ${
                  currentSlug === shop.slug
                    ? "border-primary bg-primary/10 text-primary"
                    : "border-border bg-background text-muted-foreground hover:text-foreground hover:border-primary/40"
                }`}
              >
                {SHOP_ICONS[shop.slug]}
                {shop.shortTitle}
              </button>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}

function BuildingSlotRow({ label, values, highlight }: { label: string; values: [number,number,number,number]; highlight?: boolean }) {
  const allZero = values.every(v => v === 0);
  return (
    <tr className={allZero ? "opacity-30" : ""}>
      <td className={`pr-2 py-0.5 text-[11px] whitespace-nowrap ${highlight ? "font-semibold text-foreground" : "text-muted-foreground"}`}>
        {label}
      </td>
      {values.map((v, i) => (
        <td key={i} className={`text-center px-2 py-0.5 text-xs tabular-nums ${highlight && v > 0 ? "font-semibold text-foreground" : "text-muted-foreground"}`}>
          {v === 0 ? "×" : v}
        </td>
      ))}
    </tr>
  );
}

function ShopBuildingPanel({ shop }: { shop: ShopRecord }) {
  const b = shop.building;
  if (!b) return null;
  const panelIcon = shop.workbench?.id != null
    ? (getFacilityIcon(shop.workbench.id) ?? getFurnitureIcon(shop.title) ?? getItemIcon(shop.title))
    : (getFurnitureIcon(shop.title) ?? getItemIcon(shop.title));

  return (
    <DataCard
      title={
        <span className="inline-flex items-center gap-2">
          {panelIcon ? (
            <img
              src={panelIcon}
              alt=""
              className={`${isFurnitureIconPath(panelIcon) ? "h-8 w-8" : "h-6 w-6"} shrink-0 object-contain`}
              style={{ imageRendering: "pixelated" }}
            />
          ) : null}
          <span>{shop.title}</span>
        </span>
      }
      action={<CategoryBadge category="shop">Shop</CategoryBadge>}
      contentClassName="space-y-3"
    >
      <CostPills costs={b} />
      <StatTable>
        <thead>
          <tr>
            <th className="pr-2 text-left text-[10px] font-medium text-muted-foreground/60 pb-1"></th>
            {PLOT_SIZES.map(s => (
              <StatTableHeaderCell key={s} label={s} sublabel={PLOT_TILES[s]} />
            ))}
          </tr>
        </thead>
        <tbody>
          <BuildingSlotRow label="Extra beds"   values={b.beds} highlight />
          {b.store.some(v => v > 0) && (
            <BuildingSlotRow label="Shelves"    values={b.store} highlight />
          )}
        </tbody>
      </StatTable>
    </DataCard>
  );
}

// Workbench item costs: all shop workbenches use item group 9
// qty = max(1, floor(N/3)), where N = level (1-indexed)
const WORKBENCH_ITEMS: [string, number][] = [
  ["Large Nail", 3],
  ["Iron Ore",   2],
  ["Pretty Cloth", 1],
  ["Copper Coin", 3],
];

function formatUpgTime(seconds: number): string {
  if (seconds <= 0) return "0s";
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} min`;
  const totalMinutes = Math.round(seconds / 60);
  const h = Math.floor(totalMinutes / 60);
  const m = totalMinutes % 60;
  return m === 0 ? `${h}h` : `${h}h ${m}m`;
}

// FacilityCard is still rendered from Houses until it moves to a shared KA component.



function ShopOwnerLink({ owner }: { owner: string }) {
  const ownerSuffix = owner === "Farmer" ? "C+" : null;

  return (
    <span className="inline-flex items-center gap-1.5">
      <EntityLink
        type="job"
        name={owner}
        onClick={(e) => e.stopPropagation()}
        className="font-medium text-purple-600 dark:text-purple-400 hover:text-purple-500 dark:hover:text-purple-300 transition-colors"
      >
        {owner}
      </EntityLink>
      {ownerSuffix && <span className="font-medium text-foreground">{ownerSuffix}</span>}
    </span>
  );
}

function ShopIndexCard({ shop, onOpen }: { shop: ShopRecord; onOpen: () => void }) {
  const plot = shop.building
    ? `${import.meta.env.BASE_URL}world-assets/plots/house-${shop.building.id}-M.png`
    : undefined;
  return (
    <Card onClick={onOpen} className="shadow-sm hover:shadow-md hover:border-primary/30 transition-all group h-full cursor-pointer">
      <CardHeader className="pb-2">
        {plot ? (
          <img src={plot} alt={`${shop.title}, M sized land plot`} className="h-28 w-40 max-w-full object-contain object-left" style={{ imageRendering: "pixelated" }} loading="lazy" />
        ) : SHOP_ICONS[shop.slug]}
        <CardTitle className="text-base mt-2">{shop.title}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <CardDescription className="text-xs leading-relaxed">{shop.description}</CardDescription>
        <ShopOwnerLink owner={shop.owner} />
      </CardContent>
    </Card>
  );
}

function CoinShopHeading({ coin, title }: { coin: "Copper" | "Silver"; title: string }) {
  return (
    <h2 className="mb-3 flex items-center gap-2 text-lg font-semibold">
      <img src={getItemIcon(`${coin} Coin`)} alt="" className="h-7 w-7 object-contain" style={{ imageRendering: "pixelated" }} />
      {title}
    </h2>
  );
}

type SilverShopItem = {
  id: number;
  name: string;
  icon?: string;
  silverPrice: number;
  studioLevel: number | null;
  intelligence: number | null;
  terrainName?: string;
  areaLevel: number | null;
  minimumAreaLevel?: boolean;
};

function SilverShopPanel({ shop }: { shop: ShopRecord }) {
  const [query, setQuery] = useState("");
  const isZoo = shop.slug === "zoo";
  const sourceItems: SilverShopItem[] = isZoo
    ? ZOO_ANIMALS.map((animal) => ({
        id: animal.id,
        name: animal.name,
        icon: `${import.meta.env.BASE_URL}zoo-icons/${animal.id}.png`,
        silverPrice: animal.silverPrice,
        studioLevel: null,
        intelligence: null,
        terrainName: animal.terrainName,
        areaLevel: animal.minLevel,
        minimumAreaLevel: true,
      }))
    : COLLECTION_ITEMS.filter((item) => item.category === shop.collectionCategory).map((item) => ({
        id: item.id,
        name: item.name,
        icon: item.icon,
        silverPrice: item.silverPrice,
        studioLevel: item.studioLevel,
        intelligence: item.intelligence,
        terrainName: item.terrain === null ? undefined : TERRAIN_NAMES[item.terrain] ?? `Terrain ${item.terrain}`,
        areaLevel: item.areaLevel,
      }));
  const items = sourceItems.filter((item) => matchesQuery(item.name, query));
  return (
    <Card className="shadow-sm">
      <CardHeader className="pb-3">
        <CardTitle className="text-base">{isZoo ? "Animals" : "Collectables"}</CardTitle>
        <CardDescription>
          {isZoo ? <>Silver coin prices and habitat requirements. These animals also appear in <Link href="/monsters" className="text-primary underline underline-offset-2">Monsters</Link>.</> : "Silver coin prices and collection requirements."}
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="relative mb-4 max-w-sm">
          <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
          <Input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={isZoo ? "Search animals..." : "Search collectables..."} className="pl-9 h-9" />
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {items.map((item) => (
            <div key={item.id} className="flex items-start gap-3 rounded-lg border border-border p-3">
              {item.icon && <img src={item.icon} alt="" className={`${isZoo ? "h-16 w-16" : "h-12 w-12"} shrink-0 object-contain`} style={{ imageRendering: "pixelated" }} loading="lazy" />}
              <div className="min-w-0 space-y-1">
                <div className="font-medium">{item.name}</div>
                <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
                  <span className="inline-flex items-center gap-1">
                    <img src={getItemIcon("Silver Coin")} alt="" className="h-4 w-4 object-contain" style={{ imageRendering: "pixelated" }} />
                    {item.silverPrice} silver coins
                  </span>
                  {item.studioLevel !== null && <span>Studio level {item.studioLevel}</span>}
                  {item.intelligence !== null && <span>Intelligence {item.intelligence}</span>}
                  {item.terrainName && item.areaLevel !== null && <span>{item.terrainName} · Area level {item.areaLevel}{item.minimumAreaLevel ? "+" : ""}</span>}
                </div>
              </div>
            </div>
          ))}
        </div>
        {items.length === 0 && <p className="text-sm text-muted-foreground">No {isZoo ? "animals" : "collectables"} match your search.</p>}
      </CardContent>
    </Card>
  );
}

function dedupeEquipmentRows(rows: EquipmentRow[]): EquipmentRow[] {
  const byKey = new Map<string, EquipmentRow>();
  for (const row of rows) {
    const key = row.sourceId === null
      ? `${row.name}::${row.slot}::${row.weaponType}`
      : `id:${row.sourceId}`;
    if (!byKey.has(key)) byKey.set(key, row);
  }
  return Array.from(byKey.values());
}

function normalizeQuery(text: string): string[] {
  return text
    .toLowerCase()
    .split(/\s+/)
    .map((part) => part.trim())
    .filter(Boolean);
}

function matchesQuery(name: string, query: string): boolean {
  const parts = normalizeQuery(query);
  if (parts.length === 0) return true;
  const normalizedName = name.toLowerCase();
  return parts.every((part) => normalizedName.includes(part));
}

function getKnownItemFlagNames(shopFlag: number): string[] {
  return KNOWN_ITEM_SHOP_FLAGS.filter((flag) => (shopFlag & flag.mask) !== 0).map((flag) => flag.label);
}

function getUnknownItemFlagBits(shopFlag: number): number[] {
  const knownMask = KNOWN_ITEM_SHOP_FLAGS.reduce((mask, flag) => mask | flag.mask, 0);
  const unknownMask = (shopFlag & ~knownMask) >>> 0;
  const bits: number[] = [];
  for (let bit = 0; bit < 31; bit += 1) {
    const value = 1 << bit;
    if ((unknownMask & value) !== 0) bits.push(value);
  }
  return bits;
}

function EquipmentTable({ rows, showWeaponType = false }: { rows: EquipmentRow[]; showWeaponType?: boolean }) {
  type SortCol = "name" | "rank" | "studio" | "int";
  const [sortCol, setSortCol] = useState<SortCol>("studio");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");
  function toggleSort(col: SortCol) {
    if (sortCol === col) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else { setSortCol(col); setSortDir("asc"); }
  }
  const sorted = useMemo(() => {
    const rankOrder: Record<string, number> = { S: 1, A: 2, B: 3, C: 4, D: 5, E: 6, F: 7 };
    const dir = sortDir === "asc" ? 1 : -1;
    return [...rows].sort((a, b) => {
      switch (sortCol) {
        case "rank": return ((rankOrder[a.rank] ?? 99) - (rankOrder[b.rank] ?? 99)) * dir;
        case "studio": return (((a.studioLevel ?? 0) - (b.studioLevel ?? 0)) * dir) || a.name.localeCompare(b.name);
        case "int": return (((a.craftingIntelligence ?? 0) - (b.craftingIntelligence ?? 0)) * dir) || a.name.localeCompare(b.name);
        default: return a.name.localeCompare(b.name) * dir;
      }
    });
  }, [rows, sortCol, sortDir]);
  const arrow = (col: SortCol) => sortCol === col ? (sortDir === "asc" ? " ▲" : " ▼") : " ⇅";
  const thC = "px-3 py-2 font-medium cursor-pointer select-none hover:bg-muted/60 transition-colors";
  return (
    <div className="overflow-x-auto rounded-lg border border-border">
      <table className="w-full text-sm">
        <thead className="bg-muted/40 text-muted-foreground">
          <tr>
            <th className={`${thC} text-left`} onClick={() => toggleSort("name")}>Name{arrow("name")}</th>
            <th className={`${thC} text-center`} onClick={() => toggleSort("rank")}>Rank{arrow("rank")}</th>
            <th className="px-3 py-2 text-center font-medium">Slot</th>
            {showWeaponType && <th className="px-3 py-2 text-center font-medium">Type</th>}
            <th className={`${thC} text-center`} onClick={() => toggleSort("studio")}>Studio{arrow("studio")}</th>
            <th className={`${thC} text-center`} onClick={() => toggleSort("int")}>INT{arrow("int")}</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((row) => (
            <tr key={row.name} className="border-t border-border/70">
              <td className="px-3 py-2 font-medium text-foreground">
                <div className="flex items-center gap-2">
                  {getEquipmentIcon(null, row.name) && (
                    <img src={getEquipmentIcon(null, row.name)!} alt="" className="h-10 w-10 shrink-0 object-contain" style={{ imageRendering: "pixelated" }} />
                  )}
                  {row.name}
                </div>
              </td>
              <td className="px-3 py-2 text-center">{row.rank || "-"}</td>
              <td className="px-3 py-2 text-center">{row.slot}</td>
              {showWeaponType && <td className="px-3 py-2 text-center">{row.weaponType || "-"}</td>}
              <td className="px-3 py-2 text-center">{row.studioLevel}</td>
              <td className="px-3 py-2 text-center">{row.craftingIntelligence || "-"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SkillsTable({ rows }: { rows: SkillRow[] }) {
  type SortCol = "name" | "studio" | "int" | "buy" | "sell";
  const [sortCol, setSortCol] = useState<SortCol>("studio");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");
  function toggleSort(col: SortCol) {
    if (sortCol === col) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else { setSortCol(col); setSortDir("asc"); }
  }
  const sorted = useMemo(() => {
    const dir = sortDir === "asc" ? 1 : -1;
    return [...rows].sort((a, b) => {
      switch (sortCol) {
        case "studio": return (((a.studioLevel ?? 0) - (b.studioLevel ?? 0)) * dir) || a.name.localeCompare(b.name);
        case "int": return (((a.craftingIntelligence ?? 0) - (b.craftingIntelligence ?? 0)) * dir) || a.name.localeCompare(b.name);
        case "buy": return (((a.buyPrice ?? 0) - (b.buyPrice ?? 0)) * dir) || a.name.localeCompare(b.name);
        case "sell": return (((a.sellPrice ?? 0) - (b.sellPrice ?? 0)) * dir) || a.name.localeCompare(b.name);
        default: return a.name.localeCompare(b.name) * dir;
      }
    });
  }, [rows, sortCol, sortDir]);
  const arrow = (col: SortCol) => sortCol === col ? (sortDir === "asc" ? " ▲" : " ▼") : " ⇅";
  const thC = "px-3 py-2 font-medium cursor-pointer select-none hover:bg-muted/60 transition-colors";
  return (
    <div className="overflow-x-auto rounded-lg border border-border">
      <table className="w-full text-sm">
        <thead className="bg-muted/40 text-muted-foreground">
          <tr>
            <th className={`${thC} text-left`} onClick={() => toggleSort("name")}>Skill{arrow("name")}</th>
            <th className={`${thC} text-center`} onClick={() => toggleSort("studio")}>Studio{arrow("studio")}</th>
            <th className={`${thC} text-center`} onClick={() => toggleSort("int")}>INT{arrow("int")}</th>
            <th className={`${thC} text-center`} onClick={() => toggleSort("buy")}>Buy{arrow("buy")}</th>
            <th className={`${thC} text-center`} onClick={() => toggleSort("sell")}>Sell{arrow("sell")}</th>
            <th className="px-3 py-2 text-left font-medium">Notes</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((row) => {
            const skillIcon = getSkillIcon(row.name);
            return (
            <tr key={row.name} className="border-t border-border/70">
              <td className="px-3 py-2 font-medium text-foreground">
                <div className="flex items-center gap-2">
                  {skillIcon ? (
                    <img
                      src={skillIcon}
                      alt=""
                      className="h-10 w-10 shrink-0 object-contain"
                      style={{ imageRendering: "pixelated" }}
                    />
                  ) : null}
                  <span>{row.name}</span>
                </div>
              </td>
              <td className="px-3 py-2 text-center">{row.studioLevel}</td>
              <td className="px-3 py-2 text-center">{row.craftingIntelligence || "-"}</td>
              <td className="px-3 py-2 text-center">{row.buyPrice || "-"}</td>
              <td className="px-3 py-2 text-center">{row.sellPrice || "-"}</td>
              <td className="px-3 py-2 text-muted-foreground">{row.description || row.weaponResistance || "-"}</td>
            </tr>
          );
          })}
        </tbody>
      </table>
    </div>
  );
}

function ItemTable({ rows, showStudio = true }: { rows: ItemRow[]; showStudio?: boolean }) {
  type SortCol = "name" | "studio" | "int" | "craftTime";
  const [sortCol, setSortCol] = useState<SortCol>(showStudio ? "studio" : "name");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");
  function toggleSort(col: SortCol) {
    if (sortCol === col) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else { setSortCol(col); setSortDir("asc"); }
  }
  const sorted = useMemo(() => {
    const dir = sortDir === "asc" ? 1 : -1;
    return [...rows].sort((a, b) => {
      switch (sortCol) {
        case "studio": return (((a.studioLevel ?? 0) - (b.studioLevel ?? 0)) * dir) || a.name.localeCompare(b.name);
        case "int": return (((a.craftingIntelligence ?? 0) - (b.craftingIntelligence ?? 0)) * dir) || a.name.localeCompare(b.name);
        case "craftTime": return (((a.craftTimeSeconds ?? 0) - (b.craftTimeSeconds ?? 0)) * dir) || a.name.localeCompare(b.name);
        default: return a.name.localeCompare(b.name) * dir;
      }
    });
  }, [rows, sortCol, sortDir]);
  const arrow = (col: SortCol) => sortCol === col ? (sortDir === "asc" ? " ▲" : " ▼") : " ⇅";
  const thC = "px-3 py-2 font-medium cursor-pointer select-none hover:bg-muted/60 transition-colors";
  return (
    <div className="overflow-x-auto rounded-lg border border-border">
      <table className="w-full text-sm">
        <thead className="bg-muted/40 text-muted-foreground">
          <tr>
            <th className={`${thC} text-left`} onClick={() => toggleSort("name")}>Item{arrow("name")}</th>
            {showStudio && <th className={`${thC} text-center`} onClick={() => toggleSort("studio")}>Studio{arrow("studio")}</th>}
            <th className={`${thC} text-center`} onClick={() => toggleSort("int")}>INT{arrow("int")}</th>
            <th className={`${thC} text-center`} onClick={() => toggleSort("craftTime")}>Craft Time{arrow("craftTime")}</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((row) => (
            <tr key={row.name} className="border-t border-border/70">
              <td className="px-3 py-2 font-medium text-foreground">
                <div className="flex items-center gap-1.5">
                  {getItemIcon(row.name) && (
                    <img src={getItemIcon(row.name)!} alt="" className="h-6 w-6 shrink-0 object-contain" style={{ imageRendering: "pixelated" }} />
                  )}
                  {row.name}
                </div>
              </td>
              {showStudio && <td className="px-3 py-2 text-center">{row.studioLevel}</td>}
              <td className="px-3 py-2 text-center">{row.craftingIntelligence || "-"}</td>
              <td className="px-3 py-2 text-center">{row.craftTimeSeconds ? `${row.craftTimeSeconds}s` : "-"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function FurnitureTable({ rows }: { rows: FurnitureRow[] }) {
  type SortCol = "name" | "studio" | "int";
  const [sortCol, setSortCol] = useState<SortCol>("studio");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");
  function toggleSort(col: SortCol) {
    if (sortCol === col) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else { setSortCol(col); setSortDir("asc"); }
  }
  const sorted = useMemo(() => {
    const dir = sortDir === "asc" ? 1 : -1;
    return [...rows].sort((a, b) => {
      switch (sortCol) {
        case "studio": return (((a.studioLevel ?? 0) - (b.studioLevel ?? 0)) * dir) || a.name.localeCompare(b.name);
        case "int": return (((a.craftingIntelligence ?? 0) - (b.craftingIntelligence ?? 0)) * dir) || a.name.localeCompare(b.name);
        default: return a.name.localeCompare(b.name) * dir;
      }
    });
  }, [rows, sortCol, sortDir]);
  const arrow = (col: SortCol) => sortCol === col ? (sortDir === "asc" ? " ▲" : " ▼") : " ⇅";
  const thC = "px-3 py-2 font-medium cursor-pointer select-none hover:bg-muted/60 transition-colors";
  return (
    <div className="overflow-x-auto rounded-lg border border-border">
      <table className="w-full text-sm">
        <thead className="bg-muted/40 text-muted-foreground">
          <tr>
            <th className={`${thC} text-left`} onClick={() => toggleSort("name")}>Name{arrow("name")}</th>
            <th className={`${thC} text-center`} onClick={() => toggleSort("studio")}>Studio{arrow("studio")}</th>
            <th className={`${thC} text-center`} onClick={() => toggleSort("int")}>INT{arrow("int")}</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((row) => (
            <tr key={row.name} className="border-t border-border/70">
              <td className="px-3 py-2 font-medium text-foreground">
                <div className="flex items-center gap-1.5">
                  {getFurnitureIcon(row.name) && (
                    <img src={getFurnitureIcon(row.name)!} alt="" className={`${furnitureRowIconClass(row.name)} shrink-0 object-contain`} style={{ imageRendering: "pixelated" }} />
                  )}
                  {row.name}
                </div>
              </td>
              <td className="px-3 py-2 text-center">{row.studioLevel}</td>
              <td className="px-3 py-2 text-center">{row.craftingIntelligence}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function ShopsPage() {
  const [, navigate] = useLocation();
  const [, params] = useRoute("/shops/:slug");
  const [search, setSearch] = useState("");
  const [rankFilter, setRankFilter] = useState<(typeof RANKS)[number]>("All");
  const [armorSlotFilter, setArmorSlotFilter] = useState<"All" | "Head" | "Armor" | "Shield">("All");
  const [skillSearch, setSkillSearch] = useState("");
  const [itemSearch, setItemSearch] = useState("");
  const [itemSourceFilter, setItemSourceFilter] = useState<Set<string>>(new Set());
  const [facilitySourceFilter, setFacilitySourceFilter] = useState<Set<string>>(new Set());
  const [furnitureSearch, setFurnitureSearch] = useState("");
  const [plannerAwakeningInput, setPlannerAwakeningInput] = useState("0");
  const [selectedFeedItemName, setSelectedFeedItemName] = useState("");
  const [selectedNeedExpJobName, setSelectedNeedExpJobName] = useState("");
  const [selectedNeedExpRank, setSelectedNeedExpRank] = useState<AllyJobRank>(DEFAULT_ALLY_JOB_RANK);
  const [feedItemQuery, setFeedItemQuery] = useState("");
  const [jobQuery, setJobQuery] = useState("");
  const [feedItemDropdownOpen, setFeedItemDropdownOpen] = useState(false);
  const [jobDropdownOpen, setJobDropdownOpen] = useState(false);
  const [plannerCurrentStatInputs, setPlannerCurrentStatInputs] = useState<Partial<Record<JobParameterKey, string>>>({});
  const [studioFilter, setStudioFilter] = useState<Set<number>>(new Set());
  const [intFilter, setIntFilter] = useState<Set<number>>(new Set());
  const [showItemReferenceDebug, setShowItemReferenceDebug] = useState(false);
  const [showHiddenNoExpItems, setShowHiddenNoExpItems] = useState(false);
  const [feedXpFilterEnabled, setFeedXpFilterEnabled] = useState(false);
  const [feedXpStatFilter, setFeedXpStatFilter] = useState<Set<JobParameterKey>>(new Set());
  const [feedXpStatExcludeFilter, setFeedXpStatExcludeFilter] = useState<Set<JobParameterKey>>(new Set());
  const [itemReferenceSortKey, setItemReferenceSortKey] = useState<ItemReferenceSortKey>("name");
  const [itemReferenceSortDir, setItemReferenceSortDir] = useState<"asc" | "desc">("asc");
  const [openFilterMenu, setOpenFilterMenu] = useState<string | null>(null);
  const [openReferenceFilterMenu, setOpenReferenceFilterMenu] = useState<"shop-source" | "facility-source" | null>(null);
  const [plannerStateHydrated, setPlannerStateHydrated] = useState(false);
  const filterMenuRef = useRef<HTMLDivElement>(null);
  const referenceFilterMenuRef = useRef<HTMLDivElement>(null);
  const plannerSectionRef = useRef<HTMLDivElement>(null);
  const feedItemDropdownRef = useRef<HTMLDivElement>(null);
  const jobDropdownRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (filterMenuRef.current && !filterMenuRef.current.contains(e.target as Node)) setOpenFilterMenu(null);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (referenceFilterMenuRef.current && !referenceFilterMenuRef.current.contains(e.target as Node)) setOpenReferenceFilterMenu(null);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      const target = e.target as Node;
      if (feedItemDropdownRef.current && !feedItemDropdownRef.current.contains(target)) setFeedItemDropdownOpen(false);
      if (jobDropdownRef.current && !jobDropdownRef.current.contains(target)) setJobDropdownOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);
  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      const raw = window.localStorage.getItem(ALLY_PLANNER_STORAGE_KEY);
      if (!raw) {
        setPlannerStateHydrated(true);
        return;
      }
      const parsed = JSON.parse(raw) as {
        selectedFeedItemName?: string;
        selectedNeedExpJobName?: string;
        selectedNeedExpRank?: string;
        plannerAwakeningInput?: string;
        plannerCurrentStatInputs?: Partial<Record<JobParameterKey, string>>;
      };
      if (typeof parsed.selectedFeedItemName === "string") setSelectedFeedItemName(parsed.selectedFeedItemName);
      if (typeof parsed.selectedNeedExpJobName === "string") setSelectedNeedExpJobName(parsed.selectedNeedExpJobName);
      if (typeof parsed.selectedNeedExpRank === "string") {
        const normalizedRank = parsed.selectedNeedExpRank.toUpperCase() as AllyJobRank;
        if (ALLY_JOB_RANKS.includes(normalizedRank)) {
          setSelectedNeedExpRank(normalizedRank);
        }
      }
      if (typeof parsed.plannerAwakeningInput === "string") setPlannerAwakeningInput(parsed.plannerAwakeningInput);
      if (parsed.plannerCurrentStatInputs && typeof parsed.plannerCurrentStatInputs === "object") {
        setPlannerCurrentStatInputs(parsed.plannerCurrentStatInputs);
      }
    } catch {
      // Ignore malformed localStorage payloads and continue with defaults.
    } finally {
      setPlannerStateHydrated(true);
    }
  }, []);

  const selectedShop = useMemo(
    () => SHOP_RECORDS.find((shop) => shop.slug === params?.slug) ?? null,
    [params]
  );
  const statIcons = useMemo(() => {
    const shared = localSharedData as SharedDataShape;
    return shared.statIcons ?? {};
  }, []);
  const currentUrlSearch = typeof window !== "undefined" ? window.location.search : "";

  useEffect(() => {
    const currentSearch = new URLSearchParams(window.location.search);
    const q = currentSearch.get("search") ?? "";
    if (selectedShop?.slug === "item-shop" || selectedShop?.slug === "restaurant" || selectedShop?.slug === "orchard") setItemSearch(q);
    else if (selectedShop?.slug === "furniture-shop") setFurnitureSearch(q);
    else setSearch(q);
    setItemSourceFilter(new Set());
    setFacilitySourceFilter(new Set());
    setStudioFilter(new Set());
    setIntFilter(new Set());
  }, [currentUrlSearch, selectedShop?.slug]);

  const { data: equipmentRows = [], isLoading: equipmentLoading } = useQuery({
    queryKey: ["shop-equipment-rows"],
    queryFn: fetchEquipmentRows,
    staleTime: 60_000,
  });
  const { data: itemRows = [], isLoading: itemLoading } = useQuery({
    queryKey: ["shop-item-rows"],
    queryFn: fetchItemRows,
    staleTime: 60_000,
  });

  const skillRows = useMemo(() => {
    const shared = localSharedData as SharedDataShape;
    return Object.values(shared.skills ?? {})
      .filter((skill) => (skill.studioLevel ?? 0) > 0)
      .filter((skill) => CRAFTABLE_SKILL_NAMES.has(skill.name))
      .filter((skill) => !EXCLUDED_SKILLS.has(skill.name.trim().toLowerCase()))
      .sort((a, b) => {
        const studioDiff = (a.studioLevel ?? 0) - (b.studioLevel ?? 0);
        return studioDiff !== 0 ? studioDiff : a.name.localeCompare(b.name);
      });
  }, []);

  const dedupedEquipmentRows = useMemo(() => dedupeEquipmentRows(equipmentRows), [equipmentRows]);

  const filteredEquipment = useMemo(() => {
    return dedupedEquipmentRows.filter((row) => {
      const matchesSearch = matchesQuery(row.name, search);
      const matchesRank = rankFilter === "All" || row.rank === rankFilter;
      return matchesSearch && matchesRank;
    });
  }, [dedupedEquipmentRows, rankFilter, search]);

  const weaponRows = useMemo(
    () => filteredEquipment.filter((row) => row.slot === "Weapon" && (studioFilter.size === 0 || studioFilter.has(row.studioLevel)) && (intFilter.size === 0 || intFilter.has(row.craftingIntelligence))),
    [filteredEquipment, studioFilter, intFilter]
  );
  const armorRows = useMemo(
    () => filteredEquipment.filter((row) => ["Head", "Armor", "Shield"].includes(row.slot) && (armorSlotFilter === "All" || row.slot === armorSlotFilter) && (studioFilter.size === 0 || studioFilter.has(row.studioLevel)) && (intFilter.size === 0 || intFilter.has(row.craftingIntelligence))),
    [armorSlotFilter, filteredEquipment, studioFilter, intFilter]
  );
  const accessoryRows = useMemo(
    () => filteredEquipment.filter((row) => row.slot === "Accessory" && (studioFilter.size === 0 || studioFilter.has(row.studioLevel)) && (intFilter.size === 0 || intFilter.has(row.craftingIntelligence))),
    [filteredEquipment, studioFilter, intFilter]
  );
  const filteredSkillRows = useMemo(
    () => skillRows.filter((row) => matchesQuery(row.name, skillSearch) && (studioFilter.size === 0 || studioFilter.has(row.studioLevel)) && (intFilter.size === 0 || intFilter.has(row.craftingIntelligence))),
    [skillRows, skillSearch, studioFilter, intFilter]
  );
  const itemShopRows = useMemo(
    () => itemRows.filter((row) => {
      const isCooked = (row.shopFlag & COOKED_ITEM_FLAG) !== 0;
      const isCraftable = (row.shopFlag & CRAFTABLE_ITEM_FLAG) !== 0;
      return isCraftable && !isCooked;
    }),
    [itemRows]
  );
  const restaurantRows = useMemo(
    () => itemRows.filter((row) => (row.shopFlag & COOKED_ITEM_FLAG) !== 0),
    [itemRows]
  );
  const orchardRows = useMemo(
    () => itemRows.filter((row) => row.craftGroup === ORCHARD_FRUIT_TREE_CRAFT_GROUP),
    [itemRows]
  );
  const facilityByCraftGroup = useMemo(() => parseFacilityCraftGroupMap(facilityLookupCsv), []);
  const expByLevel = useMemo(() => parseExpByLevel(expCsv), []);
  const jobNeedExpProfiles = useMemo(() => parseJobNeedExpProfiles(jobCsv), []);
  const rankedJobNeedExpProfiles = useMemo(() => parseRankedJobNeedExpProfiles(jobCsv), []);
  const maxExpLevel = useMemo(() => {
    const levels = Array.from(expByLevel.keys());
    return levels.length > 0 ? Math.max(...levels) : 1;
  }, [expByLevel]);
  const allReferenceItemRows = useMemo(() => {
    const byName = new Map<string, ItemRow>();
    for (const row of itemRows) {
      if (!byName.has(row.name)) byName.set(row.name, row);
    }
    return Array.from(byName.values()).sort((a, b) => a.name.localeCompare(b.name));
  }, [itemRows]);
  const itemSourcesByName = useMemo(() => {
    const byName = new Map<string, Set<string>>();
    for (const row of itemRows) {
      const bucket = byName.get(row.name) ?? new Set<string>();
      const isCooked = (row.shopFlag & COOKED_ITEM_FLAG) !== 0;
      const isCraftable = (row.shopFlag & CRAFTABLE_ITEM_FLAG) !== 0;
      const isOrchard = row.craftGroup === ORCHARD_FRUIT_TREE_CRAFT_GROUP;
      const hasMappedFacility = row.craftGroup >= 0;

      if (isCraftable && !isCooked) {
        bucket.add("Item Shop");
      }
      if (isCooked) {
        bucket.add("Restaurant");
      }
      if (isOrchard) {
        bucket.add("Orchard");
      }
      if (!isCooked && !isOrchard && !bucket.has("Item Shop") && hasMappedFacility) {
        bucket.add("Facility only");
      }
      if (bucket.size === 0) {
        bucket.add("Other sources");
      }
      byName.set(row.name, bucket);
    }

    const output = new Map<string, string[]>();
    for (const [name, sources] of byName.entries()) {
      const knownSourceOrder = new Map<string, number>(
        ITEM_SOURCE_ORDER.map((source, index) => [source, index])
      );
      output.set(
        name,
        Array.from(sources).sort((a, b) => {
          const aKnown = knownSourceOrder.has(a);
          const bKnown = knownSourceOrder.has(b);
          if (aKnown && bKnown) return (knownSourceOrder.get(a) ?? 0) - (knownSourceOrder.get(b) ?? 0);
          if (aKnown) return -1;
          if (bKnown) return 1;
          return a.localeCompare(b);
        })
      );
    }
    return output;
  }, [itemRows]);
  const itemFacilityRows = useMemo<ItemFacilityRow[]>(() => {
    return allReferenceItemRows.map((item) => {
      const mapped = facilityByCraftGroup.get(item.craftGroup) ?? [];
      const sources = itemSourcesByName.get(item.name) ?? [];
      const needsCraftFallback = item.craftGroup < 0 && sources.includes("Item Shop");
      const fallback = needsCraftFallback ? FALLBACK_ITEM_CRAFT_FACILITIES : [];
      const facilities = mapped.length > 0 ? mapped : fallback;
      return { item, sources, facilities };
    });
  }, [allReferenceItemRows, facilityByCraftGroup, itemSourcesByName]);
  const itemFacilityByName = useMemo(() => {
    const map = new Map<string, ItemFacilityRow>();
    for (const row of itemFacilityRows) map.set(row.item.name, row);
    return map;
  }, [itemFacilityRows]);
  const facilityIdByName = useMemo(() => {
    const map = new Map<string, number>();
    for (const facility of FACILITIES) {
      map.set(facility.name.toLowerCase(), facility.id);
    }
    return map;
  }, []);
  const itemSourceFilterOptions = useMemo(() => {
    const set = new Set<string>();
    for (const row of itemFacilityRows) {
      const sources = row.sources.length > 0 ? row.sources : ["Other sources"];
      for (const source of sources) set.add(source);
    }
    const knownOrder = new Map<string, number>(ITEM_SOURCE_ORDER.map((source, index) => [source, index]));
    return Array.from(set).sort((a, b) => {
      const aKnown = knownOrder.has(a);
      const bKnown = knownOrder.has(b);
      if (aKnown && bKnown) return (knownOrder.get(a) ?? 0) - (knownOrder.get(b) ?? 0);
      if (aKnown) return -1;
      if (bKnown) return 1;
      return a.localeCompare(b);
    });
  }, [itemFacilityRows]);
  const facilitySourceFilterOptions = useMemo(() => {
    const set = new Set<string>();
    for (const row of itemFacilityRows) {
      for (const facility of row.facilities) set.add(facility);
    }
    return Array.from(set).sort((a, b) => a.localeCompare(b));
  }, [itemFacilityRows]);
  const toggleItemSourceFilter = (source: string) => {
    setItemSourceFilter((prev) => {
      const next = new Set(prev);
      if (next.has(source)) {
        next.delete(source);
      } else {
        next.add(source);
      }
      return next;
    });
  };
  const toggleFacilitySourceFilter = (source: string) => {
    setFacilitySourceFilter((prev) => {
      const next = new Set(prev);
      if (next.has(source)) {
        next.delete(source);
      } else {
        next.add(source);
      }
      return next;
    });
  };
  const toggleFeedXpStatFilter = (stat: JobParameterKey) => {
    setFeedXpFilterEnabled(true);
    setFeedXpStatFilter((prev) => {
      const next = new Set(prev);
      if (next.has(stat)) {
        next.delete(stat);
      } else {
        next.add(stat);
      }
      return next;
    });
  };
  const toggleFeedXpStatExcludeFilter = (stat: JobParameterKey) => {
    setFeedXpFilterEnabled(true);
    setFeedXpStatExcludeFilter((prev) => {
      const next = new Set(prev);
      if (next.has(stat)) {
        next.delete(stat);
      } else {
        next.add(stat);
      }
      return next;
    });
  };
  const setItemReferenceSort = (key: ItemReferenceSortKey) => {
    setItemReferenceSortKey((currentKey) => {
      if (currentKey === key) {
        setItemReferenceSortDir((currentDir) => (currentDir === "asc" ? "desc" : "asc"));
        return currentKey;
      }
      setItemReferenceSortDir(key === "exp" ? "desc" : "asc");
      return key;
    });
  };
  const filteredItemFacilityRows = useMemo(() => {
    const activeStats = Array.from(feedXpStatFilter);
    const excludedStats = Array.from(feedXpStatExcludeFilter);
    const feedXpFilteringActive = feedXpFilterEnabled || activeStats.length > 0;
    const rows = itemFacilityRows.filter((row) => {
      if (!matchesQuery(row.item.name, itemSearch)) return false;
      if (!showHiddenNoExpItems && !hasFeedExp(row.item)) return false;
      if (feedXpFilteringActive && !hasFeedExp(row.item)) return false;
      const itemStats = BONUS_TYPE_PARAMETER_KEYS[row.item.bonusType] ?? [];
      if (activeStats.length > 0) {
        if (!activeStats.some((stat) => itemStats.includes(stat))) return false;
      }
      if (excludedStats.length > 0 && excludedStats.some((stat) => itemStats.includes(stat))) return false;
      if (itemSourceFilter.size > 0 && !row.sources.some((source) => itemSourceFilter.has(source))) return false;
      if (facilitySourceFilter.size === 0) return true;
      if (facilitySourceFilter.has(NO_FACILITY_SOURCE_FILTER) && row.facilities.length === 0) return true;
      return row.facilities.some((facility) => facilitySourceFilter.has(facility));
    });
    return rows.sort((a, b) => {
      const direction = itemReferenceSortDir === "asc" ? 1 : -1;
      if (itemReferenceSortKey === "exp") {
        const diff = getAllyExpGained(a.item) - getAllyExpGained(b.item);
        if (diff !== 0) return diff * direction;
      }
      return a.item.name.localeCompare(b.item.name) * direction;
    });
  }, [facilitySourceFilter, feedXpFilterEnabled, feedXpStatExcludeFilter, feedXpStatFilter, itemFacilityRows, itemReferenceSortDir, itemReferenceSortKey, itemSearch, itemSourceFilter, showHiddenNoExpItems]);
  const hiddenNoExpItemCount = useMemo(
    () => itemFacilityRows.filter((row) => !hasFeedExp(row.item)).length,
    [itemFacilityRows]
  );
  const feedCandidateItems = useMemo(
    () => allReferenceItemRows
      .filter((row) => row.bonusMinValue > 0 && (BONUS_TYPE_PARAMETER_KEYS[row.bonusType]?.length ?? 0) > 0)
      .sort((a, b) => (b.bonusMinValue - a.bonusMinValue) || a.name.localeCompare(b.name)),
    [allReferenceItemRows]
  );
  const selectedShopSourceLabel = itemSourceFilter.size === 0
    ? "All shop sources"
    : itemSourceFilter.size === 1
      ? (Array.from(itemSourceFilter)[0] ?? "All shop sources")
      : `${itemSourceFilter.size} selected`;
  const selectedFacilitySourceLabel = facilitySourceFilter.size === 0
    ? "All facility sources"
    : facilitySourceFilter.size === 1
      ? (facilitySourceFilter.has(NO_FACILITY_SOURCE_FILTER)
        ? "No facility"
        : (Array.from(facilitySourceFilter)[0] ?? "All facility sources"))
      : `${facilitySourceFilter.size} selected`;
  const selectedFeedXpStatLabel = feedXpStatFilter.size === 0
    ? "All stats"
    : feedXpStatFilter.size === 1
      ? (JOB_PARAMETER_DISPLAY_LABELS[Array.from(feedXpStatFilter)[0] ?? "HP"] ?? "All stats")
      : `${feedXpStatFilter.size} stats`;
  useEffect(() => {
    if (selectedFeedItemName && feedCandidateItems.some((item) => item.name === selectedFeedItemName)) return;
    const topGradeMeat = feedCandidateItems.find((item) => item.name === "Top-Grade Meat");
    setSelectedFeedItemName(topGradeMeat?.name ?? feedCandidateItems[0]?.name ?? "");
  }, [feedCandidateItems, selectedFeedItemName]);
  const selectedFeedItem = useMemo(
    () => feedCandidateItems.find((item) => item.name === selectedFeedItemName) ?? null,
    [feedCandidateItems, selectedFeedItemName]
  );
  useEffect(() => {
    if (!feedItemDropdownOpen) setFeedItemQuery(selectedFeedItemName);
  }, [feedItemDropdownOpen, selectedFeedItemName]);
  useEffect(() => {
    if (selectedNeedExpJobName && jobNeedExpProfiles.some((job) => job.name === selectedNeedExpJobName)) return;
    setSelectedNeedExpJobName(jobNeedExpProfiles[0]?.name ?? "");
  }, [jobNeedExpProfiles, selectedNeedExpJobName]);
  const selectedNeedExpJob = useMemo(
    () => jobNeedExpProfiles.find((job) => job.name === selectedNeedExpJobName) ?? null,
    [jobNeedExpProfiles, selectedNeedExpJobName]
  );
  const selectedNeedExpRankedJob = useMemo(() => {
    return rankedJobNeedExpProfiles.find((job) => job.baseName === selectedNeedExpJobName && job.rank === selectedNeedExpRank) ?? null;
  }, [rankedJobNeedExpProfiles, selectedNeedExpJobName, selectedNeedExpRank]);
  useEffect(() => {
    if (!jobDropdownOpen) setJobQuery(selectedNeedExpJobName);
  }, [jobDropdownOpen, selectedNeedExpJobName]);
  const filteredFeedItemOptions = useMemo(
    () => feedCandidateItems.filter((item) => matchesQuery(item.name, feedItemQuery)),
    [feedCandidateItems, feedItemQuery]
  );
  const filteredJobOptions = useMemo(
    () => jobNeedExpProfiles.filter((job) => matchesQuery(job.name, jobQuery)),
    [jobNeedExpProfiles, jobQuery]
  );
  const plannerAwakening = useMemo(() => {
    const parsed = Number.parseInt(plannerAwakeningInput, 10);
    if (!Number.isFinite(parsed)) return 0;
    return Math.min(Math.max(parsed, 0), MAX_AWAKENING);
  }, [plannerAwakeningInput]);
  const selectedFeedStatKeys = useMemo<JobParameterKey[]>(() => {
    if (!selectedFeedItem) return [];
    return BONUS_TYPE_PARAMETER_KEYS[selectedFeedItem.bonusType] ?? [];
  }, [selectedFeedItem]);
  const selectedFeedStatExpRows = useMemo(() => {
    return selectedFeedStatKeys.map((key) => {
      const baseMaxLevel = selectedNeedExpRankedJob?.maxLevelByParameter[key] ?? selectedNeedExpJob?.maxLevelByParameter[key] ?? 1;
      const targetStatLevel = Math.min(ALLY_STAT_MAX, maxExpLevel + 1, baseMaxLevel + (AWAKENING_LEVEL_BONUS * plannerAwakening));
      const rawInput = plannerCurrentStatInputs[key] ?? "1";
      const parsed = Number.parseInt(rawInput, 10);
      const currentStatLevel = Number.isFinite(parsed)
        ? Math.min(Math.max(parsed, 1), targetStatLevel)
        : 1;
      const needExpPercent = selectedNeedExpRankedJob?.needExpByParameter[key] ?? selectedNeedExpJob?.needExpByParameter[key] ?? 100;
      const expNeeded = getTotalExpNeeded(expByLevel, currentStatLevel, targetStatLevel, needExpPercent);
      return {
        key,
        targetStatLevel,
        currentStatLevel,
        needExpPercent,
        expNeeded,
      };
    });
  }, [expByLevel, maxExpLevel, plannerAwakening, plannerCurrentStatInputs, selectedFeedStatKeys, selectedNeedExpJob, selectedNeedExpRankedJob]);
  const plannerTotalExpNeeded = useMemo(() => {
    if (selectedFeedStatExpRows.length === 0) return 0;
    return Math.max(...selectedFeedStatExpRows.map((row) => row.expNeeded));
  }, [selectedFeedStatExpRows]);
  const selectedNeedExpRange = useMemo(() => {
    if (selectedFeedStatExpRows.length === 0) return null;
    const values = selectedFeedStatExpRows.map((row) => row.needExpPercent);
    return {
      min: Math.min(...values),
      max: Math.max(...values),
    };
  }, [selectedFeedStatExpRows]);
  const selectedFeedExpPerItem = useMemo(() => {
    if (!selectedFeedItem) return 0;
    return Math.max(0, selectedFeedItem.bonusMinValue);
  }, [selectedFeedItem]);
  const selectedFeedCopperPerItem = useMemo(() => {
    if (!selectedFeedItem) return 0;
    return Math.max(0, selectedFeedItem.buyPrice);
  }, [selectedFeedItem]);
  const feedItemsNeeded = useMemo(() => {
    if (!selectedFeedItem || selectedFeedExpPerItem <= 0) return null;
    if (plannerTotalExpNeeded <= 0) return 0;
    return Math.ceil(plannerTotalExpNeeded / selectedFeedExpPerItem);
  }, [plannerTotalExpNeeded, selectedFeedExpPerItem, selectedFeedItem]);
  const selectedFeedTotalCopperNeeded = useMemo(() => {
    if (feedItemsNeeded === null) return null;
    return selectedFeedCopperPerItem * feedItemsNeeded;
  }, [feedItemsNeeded, selectedFeedCopperPerItem]);
  useEffect(() => {
    if (selectedFeedStatKeys.length === 0) {
      setPlannerCurrentStatInputs({});
      return;
    }
    setPlannerCurrentStatInputs((prev) => {
      const next: Partial<Record<JobParameterKey, string>> = {};
      selectedFeedStatKeys.forEach((key) => {
        next[key] = prev[key] ?? "1";
      });
      return next;
    });
  }, [selectedFeedStatKeys]);
  useEffect(() => {
    if (typeof window === "undefined" || !plannerStateHydrated) return;
    const payload = {
      selectedFeedItemName,
      selectedNeedExpJobName,
      selectedNeedExpRank,
      plannerAwakeningInput,
      plannerCurrentStatInputs,
    };
    window.localStorage.setItem(ALLY_PLANNER_STORAGE_KEY, JSON.stringify(payload));
  }, [plannerAwakeningInput, plannerCurrentStatInputs, plannerStateHydrated, selectedFeedItemName, selectedNeedExpJobName, selectedNeedExpRank]);
  const selectedFeedStatProjection = useMemo(() => {
    if (!selectedFeedItem || selectedFeedStatKeys.length === 0) return [];

    return selectedFeedStatKeys.map((key) => {
      const expRow = selectedFeedStatExpRows.find((row) => row.key === key);
      const statCap = expRow?.targetStatLevel ?? 1;
      const currentStat = expRow?.currentStatLevel ?? 1;
      const expNeeded = expRow?.expNeeded ?? 0;
      const itemsToMax = selectedFeedExpPerItem > 0 ? Math.ceil(expNeeded / selectedFeedExpPerItem) : null;

      return {
        key,
        label: JOB_PARAMETER_DISPLAY_LABELS[key],
        statCap,
        currentStat,
        itemsToCapEarliest: itemsToMax,
        itemsToCapLatest: itemsToMax,
      };
    });
  }, [selectedFeedExpPerItem, selectedFeedItem, selectedFeedStatExpRows, selectedFeedStatKeys]);
  const selectedFeedEarliestCapItems = useMemo(() => {
    const values = selectedFeedStatProjection
      .map((row) => row.itemsToCapEarliest)
      .filter((value): value is number => value !== null);
    if (values.length === 0) return null;
    return Math.min(...values);
  }, [selectedFeedStatProjection]);
  const filteredItemRows = useMemo(
    () => itemShopRows.filter((row) => matchesQuery(row.name, itemSearch) && (studioFilter.size === 0 || studioFilter.has(row.studioLevel)) && (intFilter.size === 0 || intFilter.has(row.craftingIntelligence))),
    [itemShopRows, itemSearch, studioFilter, intFilter]
  );
  const filteredRestaurantRows = useMemo(
    () => restaurantRows.filter((row) => matchesQuery(row.name, itemSearch) && (studioFilter.size === 0 || studioFilter.has(row.studioLevel)) && (intFilter.size === 0 || intFilter.has(row.craftingIntelligence))),
    [restaurantRows, itemSearch, studioFilter, intFilter]
  );
  const filteredOrchardRows = useMemo(
    () => orchardRows.filter((row) => matchesQuery(row.name, itemSearch) && (intFilter.size === 0 || intFilter.has(row.craftingIntelligence))),
    [orchardRows, itemSearch, intFilter]
  );
  const filteredFurnitureRows = useMemo(
    () => FURNITURE_ROWS.filter((row) => matchesQuery(row.name, furnitureSearch) && (studioFilter.size === 0 || studioFilter.has(row.studioLevel)) && (intFilter.size === 0 || intFilter.has(row.craftingIntelligence))),
    [furnitureSearch, studioFilter, intFilter]
  );
  const shopStudioLevels = useMemo((): number[] => {
    if (!selectedShop) return [];
    const levels = (() => {
      switch (selectedShop.slug) {
        case "weapon-shop": return dedupedEquipmentRows.filter((r) => r.slot === "Weapon" && r.craftable).map((r) => r.studioLevel);
        case "armor-shop": return dedupedEquipmentRows.filter((r) => ["Head", "Armor", "Shield"].includes(r.slot) && r.craftable).map((r) => r.studioLevel);
        case "accessory-shop": return dedupedEquipmentRows.filter((r) => r.slot === "Accessory" && r.craftable).map((r) => r.studioLevel);
        case "skill-shop": return skillRows.map((r) => r.studioLevel);
        case "item-shop": return itemShopRows.map((r) => r.studioLevel);
        case "restaurant": return restaurantRows.map((r) => r.studioLevel);
        case "orchard": return [];
        case "furniture-shop": return FURNITURE_ROWS.map((r) => r.studioLevel);
        default: return [];
      }
    })();
    return [...new Set(levels.filter((v) => v > 0))].sort((a, b) => a - b);
  }, [selectedShop, dedupedEquipmentRows, skillRows, itemShopRows, restaurantRows, orchardRows]);
  const availableIntValues = useMemo(() => {
    if (!selectedShop) return [];
    type IntRow = { studioLevel: number; craftingIntelligence: number };
    const allRows: IntRow[] = (() => {
      switch (selectedShop.slug) {
        case "weapon-shop": return dedupedEquipmentRows.filter((r) => r.slot === "Weapon");
        case "armor-shop": return dedupedEquipmentRows.filter((r) => ["Head", "Armor", "Shield"].includes(r.slot));
        case "accessory-shop": return dedupedEquipmentRows.filter((r) => r.slot === "Accessory");
        case "skill-shop": return skillRows;
        case "item-shop": return itemShopRows;
        case "restaurant": return restaurantRows;
        case "orchard": return orchardRows;
        case "furniture-shop": return FURNITURE_ROWS;
        default: return [];
      }
    })();
    const base = studioFilter.size > 0 ? allRows.filter((r) => studioFilter.has(r.studioLevel)) : allRows;
    return [...new Set(base.map((r) => r.craftingIntelligence).filter((v) => v > 0))].sort((a, b) => a - b);
  }, [studioFilter, selectedShop, dedupedEquipmentRows, skillRows, itemShopRows, restaurantRows, orchardRows]);
  const toggleStudio = (level: number) => {
    setStudioFilter((prev) => {
      const next = new Set(prev);
      next.has(level) ? next.delete(level) : next.add(level);
      return next;
    });
    setIntFilter(new Set());
  };
  const toggleInt = (v: number) => setIntFilter((prev) => {
    const next = new Set(prev);
    next.has(v) ? next.delete(v) : next.add(v);
    return next;
  });
  const FilterDropdowns = ({ showRank = true, showArmorSlot = false }: { showRank?: boolean; showArmorSlot?: boolean }) => (
    <div ref={filterMenuRef} className="flex flex-wrap items-center gap-2">
      {showArmorSlot && (
        <div className="relative">
          <button onClick={() => setOpenFilterMenu((v) => v === "armorSlot" ? null : "armorSlot")}
            className={`h-8 px-3 text-xs rounded-md border font-medium flex items-center gap-1.5 transition-colors ${armorSlotFilter !== "All" ? "bg-primary text-primary-foreground border-primary" : "border-input bg-background text-muted-foreground hover:text-foreground"}`}>
            {armorSlotFilter === "All" ? "Slot" : armorSlotFilter}<ChevronDown className={`w-3 h-3 transition-transform ${openFilterMenu === "armorSlot" ? "rotate-180" : ""}`} />
          </button>
          {openFilterMenu === "armorSlot" && (
            <div className="absolute z-50 top-full mt-1 left-0 min-w-[120px] rounded-md border border-border bg-popover shadow-md text-xs overflow-hidden">
              {(["All", "Head", "Armor", "Shield"] as const).map((opt) => (
                <button key={opt} onMouseDown={(e) => { e.preventDefault(); e.stopPropagation(); setArmorSlotFilter(opt); }}
                  className="w-full flex items-center gap-2 px-3 py-2 hover:bg-muted transition-colors text-foreground">
                  <span className={`w-3.5 h-3.5 shrink-0 ${armorSlotFilter === opt ? "text-primary" : "opacity-0"}`}><CheckCircle2 className="w-3.5 h-3.5" /></span>
                  {opt === "All" ? "All slots" : opt}
                </button>
              ))}
            </div>
          )}
        </div>
      )}
      {showRank && (
        <div className="relative">
          <button onClick={() => setOpenFilterMenu((v) => v === "rank" ? null : "rank")}
            className={`h-8 px-3 text-xs rounded-md border font-medium flex items-center gap-1.5 transition-colors ${rankFilter !== "All" ? "bg-primary text-primary-foreground border-primary" : "border-input bg-background text-muted-foreground hover:text-foreground"}`}>
            {rankFilter === "All" ? "Rank" : rankFilter}<ChevronDown className={`w-3 h-3 transition-transform ${openFilterMenu === "rank" ? "rotate-180" : ""}`} />
          </button>
          {openFilterMenu === "rank" && (
            <div className="absolute z-50 top-full mt-1 left-0 min-w-[120px] rounded-md border border-border bg-popover shadow-md text-xs overflow-hidden">
              {RANKS.map((rank) => (
                <button key={rank} onMouseDown={(e) => { e.preventDefault(); e.stopPropagation(); setRankFilter(rank); }}
                  className="w-full flex items-center gap-2 px-3 py-2 hover:bg-muted transition-colors text-foreground">
                  <span className={`w-3.5 h-3.5 shrink-0 ${rankFilter === rank ? "text-primary" : "opacity-0"}`}><CheckCircle2 className="w-3.5 h-3.5" /></span>
                  {rank === "All" ? "All ranks" : `${rank} rank`}
                </button>
              ))}
            </div>
          )}
        </div>
      )}
      {shopStudioLevels.length > 0 && (
        <div className="relative">
          <button onClick={() => setOpenFilterMenu((v) => v === "studio" ? null : "studio")}
            className={`h-8 px-3 text-xs rounded-md border font-medium flex items-center gap-1.5 transition-colors ${studioFilter.size > 0 ? "bg-primary text-primary-foreground border-primary" : "border-input bg-background text-muted-foreground hover:text-foreground"}`}>
            Studio{studioFilter.size > 0 ? ` (${studioFilter.size})` : ""}<ChevronDown className={`w-3 h-3 transition-transform ${openFilterMenu === "studio" ? "rotate-180" : ""}`} />
          </button>
          {openFilterMenu === "studio" && (
            <div className="absolute z-50 top-full mt-1 left-0 min-w-[110px] rounded-md border border-border bg-popover shadow-md text-xs overflow-hidden">
              {shopStudioLevels.map((level) => (
                <button key={level} onMouseDown={(e) => { e.preventDefault(); e.stopPropagation(); toggleStudio(level); }}
                  className="w-full flex items-center gap-2 px-3 py-2 hover:bg-muted transition-colors text-foreground">
                  <span className={`w-3.5 h-3.5 shrink-0 ${studioFilter.has(level) ? "text-primary" : "opacity-0"}`}><CheckCircle2 className="w-3.5 h-3.5" /></span>
                  Lv {level}
                </button>
              ))}
            </div>
          )}
        </div>
      )}
      {availableIntValues.length > 0 && (
        <div className="relative">
          <button onClick={() => setOpenFilterMenu((v) => v === "int" ? null : "int")}
            className={`h-8 px-3 text-xs rounded-md border font-medium flex items-center gap-1.5 transition-colors ${intFilter.size > 0 ? "bg-primary text-primary-foreground border-primary" : "border-input bg-background text-muted-foreground hover:text-foreground"}`}>
            INT{intFilter.size > 0 ? ` (${intFilter.size})` : ""}<ChevronDown className={`w-3 h-3 transition-transform ${openFilterMenu === "int" ? "rotate-180" : ""}`} />
          </button>
          {openFilterMenu === "int" && (
            <div className="absolute z-50 top-full mt-1 left-0 min-w-[110px] rounded-md border border-border bg-popover shadow-md text-xs overflow-hidden">
              {availableIntValues.map((v) => (
                <button key={v} onMouseDown={(e) => { e.preventDefault(); e.stopPropagation(); toggleInt(v); }}
                  className="w-full flex items-center gap-2 px-3 py-2 hover:bg-muted transition-colors text-foreground">
                  <span className={`w-3.5 h-3.5 shrink-0 ${intFilter.has(v) ? "text-primary" : "opacity-0"}`}><CheckCircle2 className="w-3.5 h-3.5" /></span>
                  INT {v}
                </button>
              ))}
            </div>
          )}
        </div>
      )}
      {(rankFilter !== "All" || armorSlotFilter !== "All" || studioFilter.size > 0 || intFilter.size > 0) && (
        <button onClick={() => { setRankFilter("All"); setArmorSlotFilter("All"); setStudioFilter(new Set()); setIntFilter(new Set()); }}
          className="h-8 px-2 text-xs text-muted-foreground hover:text-foreground transition-colors">
          ✕ clear
        </button>
      )}
    </div>
  );
  const selectedShopResults = useMemo(() => {
    switch (selectedShop?.slug) {
      case "items-reference":
        return itemFacilityRows.filter((row) => matchesQuery(row.item.name, itemSearch)).length;
      case "weapon-shop":
        return weaponRows.length;
      case "armor-shop":
        return armorRows.length;
      case "accessory-shop":
        return accessoryRows.length;
      case "skill-shop":
        return filteredSkillRows.length;
      case "item-shop":
        return filteredItemRows.length;
      case "restaurant":
        return filteredRestaurantRows.length;
      case "orchard":
        return filteredOrchardRows.length;
      case "furniture-shop":
        return filteredFurnitureRows.length;
      case "zoo":
        return ZOO_ANIMALS.length;
      default:
        return selectedShop?.collectionCategory !== undefined
          ? COLLECTION_ITEMS.filter((item) => item.category === selectedShop.collectionCategory).length
          : null;
    }
  }, [
    accessoryRows.length,
    armorRows.length,
    itemFacilityRows,
    itemSearch,
    filteredFurnitureRows.length,
    filteredItemRows.length,
    filteredOrchardRows.length,
    filteredRestaurantRows.length,
    filteredSkillRows.length,
    selectedShop?.slug,
    weaponRows.length,
  ]);

  if (!selectedShop) {
    return (
      <div className="min-h-screen bg-background transition-colors">
        <div className="max-w-6xl mx-auto px-4 py-8">
          <ShopHeader />

          <section>
            <CoinShopHeading coin="Copper" title="Copper coin shops" />
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
              {COPPER_SHOPS.map((shop) => <ShopIndexCard key={shop.slug} shop={shop} onOpen={() => navigate(`/shops/${shop.slug}`)} />)}
            </div>
          </section>
          <section className="mt-8">
            <CoinShopHeading coin="Silver" title="Silver coin shops" />
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
              {SILVER_SHOPS.map((shop) => <ShopIndexCard key={shop.slug} shop={shop} onOpen={() => navigate(`/shops/${shop.slug}`)} />)}
            </div>
          </section>
          {SECONDARY_FACILITIES.length > 0 && (
            <div className="mt-6">
              <h2 className="text-sm font-semibold text-muted-foreground mb-3">Other Facilities</h2>
              <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
                {SECONDARY_FACILITIES.map((shop) => <ShopIndexCard key={shop.slug} shop={shop} onOpen={() => navigate(`/shops/${shop.slug}`)} />)}
              </div>
            </div>
          )}

          {ITEMS_REFERENCE_SHOPS.length > 0 && (
            <div className="mt-6">
              <h2 className="text-sm font-semibold text-muted-foreground mb-3">Items Reference</h2>
              <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
                {ITEMS_REFERENCE_SHOPS.map((shop) => (
                  (() => {
                    const cardIcon = resolveShopCardIcon(shop);
                    return (
                  <Card
                    key={shop.slug}
                    onClick={() => navigate(`/shops/${shop.slug}`)}
                    className="shadow-sm hover:shadow-md hover:border-primary/30 transition-all group h-full cursor-pointer"
                  >
                    <CardHeader className="pb-2">
                      <div className="p-2 rounded-lg bg-muted group-hover:bg-primary/10 transition-colors w-fit">
                        {cardIcon ? (
                          <img
                            src={cardIcon}
                            alt=""
                            className={`${isFurnitureIconPath(cardIcon) ? "h-8 w-8" : "h-6 w-6"} object-contain`}
                            style={{ imageRendering: "pixelated" }}
                          />
                        ) : (
                          SHOP_ICONS[shop.slug]
                        )}
                      </div>
                      <CardTitle className="text-base mt-2">{shop.title}</CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-3 text-sm">
                      <CardDescription className="text-xs leading-relaxed">{shop.description}</CardDescription>
                      <div className="flex items-center justify-between text-xs text-muted-foreground">
                        <ShopOwnerLink owner={shop.owner} />

                      </div>
                    </CardContent>
                  </Card>
                    );
                  })()
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-background transition-colors">
      <div className="max-w-6xl mx-auto px-4 py-8">
        <ShopHeader selectedShop={selectedShop} />
        <ShopTabs currentSlug={selectedShop.slug} />

        {selectedShop.slug === "items-reference" && (
          <div className="mb-4 flex justify-start">
            <Button
              type="button"
              variant="outline"
              className="border-primary/60 bg-primary/10 text-primary hover:bg-primary/15"
              onClick={() => plannerSectionRef.current?.scrollIntoView({ behavior: "smooth", block: "start" })}
            >
              Go to Ally Feed Planner
            </Button>
          </div>
        )}

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-4">
          <ShopBuildingPanel shop={selectedShop} />
          {selectedShop.workbench && (
            (() => {
              const facility = FACILITIES.find(f => f.id === selectedShop.workbench?.id);
              return facility ? <FacilityCard f={facility} /> : null;
            })()
          )}
        </div>

        {selectedShop.slug !== "items-reference" && (
          <Card className="shadow-sm mb-4">
            <CardHeader className="pb-2">
              <CardTitle className="text-base">Owner</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-wrap items-center gap-3 text-sm">
              <span className="text-muted-foreground">Owner:</span>
              <ShopOwnerLink owner={selectedShop.owner} />

              {selectedShopResults !== null && (
                <>
                  <span className="text-muted-foreground">Results:</span>
                  <span className="font-medium text-foreground">{selectedShopResults}</span>
                </>
              )}
            </CardContent>
          </Card>
        )}

        {selectedShop.currency === "silver" && (
          <SilverShopPanel shop={selectedShop} />
        )}

        {selectedShop.slug === "weapon-shop" && (
          <div className="space-y-4">
            <Card className="shadow-sm">
              <CardHeader className="pb-3">
            <CardTitle className="text-base">Weapon Shop Database</CardTitle>
            <CardDescription className="text-xs">
                  Browse Kingdom Adventurers weapon shop data, weapon ranks, weapon types, crafting levels,
                intelligence requirements, prices, and combat stats.
            </CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="flex flex-wrap items-center gap-3">
                  <div className="relative">
                    <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
                    <Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search weapons..." className="pl-9 h-9" />
                  </div>
                  <FilterDropdowns showRank />
                </div>
              </CardContent>
            </Card>

            <Card className="shadow-sm">
              <CardContent className="pt-6">
                {equipmentLoading ? <p className="text-sm text-muted-foreground">Loading weapon data...</p> : <EquipmentTable rows={weaponRows} showWeaponType />}
              </CardContent>
            </Card>
          </div>
        )}

        {selectedShop.slug === "armor-shop" && (
          <div className="space-y-4">
            <Card className="shadow-sm">
              <CardHeader className="pb-3">
            <CardTitle className="text-base">Armor Shop Database</CardTitle>
            <CardDescription className="text-xs">
                  Browse Kingdom Adventurers armor shop data for headgear, armor, and shields with ranks,
                  crafting levels, intelligence requirements, prices, defence, speed, luck, HP, and MP.
            </CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="flex flex-wrap items-center gap-3">
                  <div className="relative">
                    <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
                    <Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search armor..." className="pl-9 h-9" />
                  </div>
                  <FilterDropdowns showArmorSlot showRank />
                </div>
              </CardContent>
            </Card>

            <Card className="shadow-sm">
              <CardContent className="pt-6">
                {equipmentLoading ? <p className="text-sm text-muted-foreground">Loading armor data...</p> : <EquipmentTable rows={armorRows} />}
              </CardContent>
            </Card>
          </div>
        )}

        {selectedShop.slug === "accessory-shop" && (
          <div className="space-y-4">
            <Card className="shadow-sm">
              <CardHeader className="pb-3">
                <CardTitle className="text-base">Accessory Shop Database</CardTitle>
                <CardDescription className="text-xs">
                  Accessory-only browsing with rank and crafting requirements.
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="flex flex-wrap items-center gap-3">
                  <div className="relative">
                    <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
                    <Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search accessories..." className="pl-9 h-9" />
                  </div>
                  <FilterDropdowns showRank />
                </div>
              </CardContent>
            </Card>

            <Card className="shadow-sm">
              <CardContent className="pt-6">
                {equipmentLoading ? <p className="text-sm text-muted-foreground">Loading accessory data...</p> : <EquipmentTable rows={accessoryRows} />}
              </CardContent>
            </Card>
          </div>
        )}

        {selectedShop.slug === "skill-shop" && (
          <div className="space-y-4">
            <Card className="shadow-sm">
              <CardHeader className="pb-3">
                <CardTitle className="text-base">Skill Shop Database</CardTitle>
                <CardDescription className="text-xs">
                  Shop-craftable skills with crafting requirements and prices.
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="flex flex-wrap items-center gap-3">
                  <div className="relative">
                    <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
                    <Input value={skillSearch} onChange={(e) => setSkillSearch(e.target.value)} placeholder="Search skills..." className="pl-9 h-9" />
                  </div>
                  <FilterDropdowns showRank={false} />
                  <span className="text-xs text-muted-foreground">{filteredSkillRows.length} skills</span>
                </div>
              </CardContent>
            </Card>

            <Card className="shadow-sm">
              <CardContent className="pt-6">
                <SkillsTable rows={filteredSkillRows} />
              </CardContent>
            </Card>
          </div>
        )}

        {selectedShop.slug === "items-reference" && (
          <div className="space-y-4">
            <Card className="shadow-sm">
              <CardHeader className="pb-3">
                <CardTitle className="text-base">Item Reference</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="flex flex-wrap items-center gap-3">
                  <div className="relative">
                    <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
                    <Input value={itemSearch} onChange={(e) => setItemSearch(e.target.value)} placeholder="Search items..." className="pl-9 h-9" />
                  </div>
                </div>

                <div ref={referenceFilterMenuRef} className="flex flex-wrap items-center gap-2">
                  <div className="relative">
                    <button
                      type="button"
                      onClick={() => setOpenReferenceFilterMenu((prev) => prev === "shop-source" ? null : "shop-source")}
                      className={`h-9 rounded-md border px-3 text-sm inline-flex items-center gap-2 font-medium transition-colors ${itemSourceFilter.size > 0 ? "bg-primary text-primary-foreground border-primary" : "border-input bg-background text-muted-foreground hover:text-foreground"}`}
                    >
                      Shop: {selectedShopSourceLabel}
                      <ChevronDown className={`w-4 h-4 transition-transform ${openReferenceFilterMenu === "shop-source" ? "rotate-180" : ""}`} />
                    </button>
                    {openReferenceFilterMenu === "shop-source" && (
                      <div className="absolute z-50 top-full mt-1 left-0 min-w-[180px] rounded-md border border-border bg-popover shadow-md text-xs overflow-hidden max-h-64 overflow-y-auto">
                        <button
                          type="button"
                          onMouseDown={(e) => {
                            e.preventDefault();
                            e.stopPropagation();
                            setItemSourceFilter(new Set());
                          }}
                          className="w-full flex items-center gap-2 px-3 py-2 hover:bg-muted transition-colors text-foreground"
                        >
                          <span className={`w-3.5 h-3.5 shrink-0 ${itemSourceFilter.size === 0 ? "text-primary" : "opacity-0"}`}><CheckCircle2 className="w-3.5 h-3.5" /></span>
                          All shop sources
                        </button>
                        {itemSourceFilterOptions.map((source) => (
                          <button
                            key={source}
                            type="button"
                            onMouseDown={(e) => {
                              e.preventDefault();
                              e.stopPropagation();
                              toggleItemSourceFilter(source);
                            }}
                            className="w-full flex items-center gap-2 px-3 py-2 hover:bg-muted transition-colors text-foreground"
                          >
                            <span className={`w-3.5 h-3.5 shrink-0 ${itemSourceFilter.has(source) ? "text-primary" : "opacity-0"}`}><CheckCircle2 className="w-3.5 h-3.5" /></span>
                            {source}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>

                  <div className="relative">
                    <button
                      type="button"
                      onClick={() => setOpenReferenceFilterMenu((prev) => prev === "facility-source" ? null : "facility-source")}
                      className={`h-9 rounded-md border px-3 text-sm inline-flex items-center gap-2 font-medium transition-colors ${facilitySourceFilter.size > 0 ? "bg-primary text-primary-foreground border-primary" : "border-input bg-background text-muted-foreground hover:text-foreground"}`}
                    >
                      Facility: {selectedFacilitySourceLabel}
                      <ChevronDown className={`w-4 h-4 transition-transform ${openReferenceFilterMenu === "facility-source" ? "rotate-180" : ""}`} />
                    </button>
                    {openReferenceFilterMenu === "facility-source" && (
                      <div className="absolute z-50 top-full mt-1 left-0 min-w-[220px] rounded-md border border-border bg-popover shadow-md text-xs overflow-hidden max-h-64 overflow-y-auto">
                        <button
                          type="button"
                          onMouseDown={(e) => {
                            e.preventDefault();
                            e.stopPropagation();
                            setFacilitySourceFilter(new Set());
                          }}
                          className="w-full flex items-center gap-2 px-3 py-2 hover:bg-muted transition-colors text-foreground"
                        >
                          <span className={`w-3.5 h-3.5 shrink-0 ${facilitySourceFilter.size === 0 ? "text-primary" : "opacity-0"}`}><CheckCircle2 className="w-3.5 h-3.5" /></span>
                          All facility sources
                        </button>
                        <button
                          type="button"
                          onMouseDown={(e) => {
                            e.preventDefault();
                            e.stopPropagation();
                            toggleFacilitySourceFilter(NO_FACILITY_SOURCE_FILTER);
                          }}
                          className="w-full flex items-center gap-2 px-3 py-2 hover:bg-muted transition-colors text-foreground"
                        >
                          <span className={`w-3.5 h-3.5 shrink-0 ${facilitySourceFilter.has(NO_FACILITY_SOURCE_FILTER) ? "text-primary" : "opacity-0"}`}><CheckCircle2 className="w-3.5 h-3.5" /></span>
                          No facility
                        </button>
                        {facilitySourceFilterOptions.map((facility) => (
                          <button
                            key={facility}
                            type="button"
                            onMouseDown={(e) => {
                              e.preventDefault();
                              e.stopPropagation();
                              toggleFacilitySourceFilter(facility);
                            }}
                            className="w-full flex items-center gap-2 px-3 py-2 hover:bg-muted transition-colors text-foreground"
                          >
                            <span className={`w-3.5 h-3.5 shrink-0 ${facilitySourceFilter.has(facility) ? "text-primary" : "opacity-0"}`}><CheckCircle2 className="w-3.5 h-3.5" /></span>
                            {facility}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>

                  {(itemSourceFilter.size > 0 || facilitySourceFilter.size > 0) && (
                    <button
                      type="button"
                      onClick={() => {
                        setItemSourceFilter(new Set());
                        setFacilitySourceFilter(new Set());
                      }}
                      className="h-8 px-2 text-xs text-muted-foreground hover:text-foreground transition-colors"
                    >
                      ✕ clear
                    </button>
                  )}
                </div>

                <div className="flex items-center gap-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <button
                      type="button"
                      onClick={() => setShowItemReferenceDebug((prev) => !prev)}
                      className={`h-8 px-3 text-xs rounded-md border font-medium transition-colors ${showItemReferenceDebug ? "bg-primary text-primary-foreground border-primary" : "border-input bg-background text-muted-foreground hover:text-foreground"}`}
                    >
                      {showItemReferenceDebug ? "Debug: on" : "Debug: off"}
                    </button>
                    <span className="text-xs text-muted-foreground">Developer only: shows raw attributes and source classification inputs.</span>
                  </div>
                  <button
                    type="button"
                    onClick={() => setShowHiddenNoExpItems((prev) => !prev)}
                    className="ml-auto h-7 px-2 text-[11px] rounded-md border border-input text-muted-foreground hover:text-foreground hover:border-primary/40 transition-colors"
                  >
                    {showHiddenNoExpItems
                      ? "Hide hidden"
                      : `Show hidden (${hiddenNoExpItemCount.toLocaleString()})`}
                  </button>
                </div>

                <div className="rounded-lg border border-border bg-muted/20 p-3 space-y-3">
                  <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                    <div>
                      <div className="text-sm font-semibold text-foreground">Feeding XP filters</div>
                      <div className="text-xs text-muted-foreground">Filter item reference rows by ally feeding XP stat and sort by XP gained.</div>
                    </div>
                    <div className="flex flex-wrap items-center gap-2">
                      <button
                        type="button"
                        onClick={() => setFeedXpFilterEnabled((prev) => !prev)}
                        className={`h-9 rounded-md border px-3 text-sm font-medium transition-colors ${feedXpFilterEnabled ? "bg-primary text-primary-foreground border-primary" : "border-input bg-background text-muted-foreground hover:text-foreground"}`}
                      >
                        {feedXpFilterEnabled ? "Feeding XP: on" : "Feeding XP: off"}
                      </button>
                      <button
                        type="button"
                        onClick={() => setItemReferenceSort("exp")}
                        className={`h-9 rounded-md border px-3 text-sm font-medium transition-colors ${itemReferenceSortKey === "exp" ? "bg-primary/10 border-primary/50 text-primary" : "border-input bg-background text-muted-foreground hover:text-foreground"}`}
                      >
                        Sort XP {itemReferenceSortKey === "exp" ? (itemReferenceSortDir === "asc" ? "up" : "down") : ""}
                      </button>
                    </div>
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-xs text-muted-foreground">{selectedFeedXpStatLabel}</span>
                    <button
                      type="button"
                      onClick={() => setFeedXpStatFilter(new Set())}
                      className={`h-8 rounded-md border px-2 text-xs transition-colors ${feedXpStatFilter.size === 0 ? "bg-primary/10 border-primary/40 text-primary" : "border-input text-muted-foreground hover:text-foreground"}`}
                    >
                      All stats
                    </button>
                    {FEED_FILTER_STAT_KEYS.map((stat) => {
                      const active = feedXpStatFilter.has(stat);
                      return (
                        <button
                          key={stat}
                          type="button"
                          onClick={() => toggleFeedXpStatFilter(stat)}
                          className={`h-8 rounded-md border px-2 text-xs transition-colors inline-flex items-center gap-1.5 ${active ? "bg-primary text-primary-foreground border-primary" : "border-input bg-background text-muted-foreground hover:text-foreground"}`}
                        >
                          {statIcons[JOB_PARAMETER_ICON_KEYS[stat] ?? ""] ? (
                            <img
                              src={statIcons[JOB_PARAMETER_ICON_KEYS[stat] ?? ""]}
                              alt={JOB_PARAMETER_DISPLAY_LABELS[stat]}
                              className="h-3.5 w-3.5 object-contain"
                            />
                          ) : null}
                          {JOB_PARAMETER_DISPLAY_LABELS[stat]}
                        </button>
                      );
                    })}
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-xs text-muted-foreground">Exclude:</span>
                    {FEED_FILTER_STAT_KEYS.map((stat) => {
                      const active = feedXpStatExcludeFilter.has(stat);
                      return (
                        <button
                          key={`exclude-${stat}`}
                          type="button"
                          onClick={() => toggleFeedXpStatExcludeFilter(stat)}
                          className={`h-8 rounded-md border px-2 text-xs transition-colors inline-flex items-center gap-1.5 ${active ? "bg-primary text-primary-foreground border-primary" : "border-input bg-background text-muted-foreground hover:text-foreground"}`}
                        >
                          {statIcons[JOB_PARAMETER_ICON_KEYS[stat] ?? ""] ? (
                            <img
                              src={statIcons[JOB_PARAMETER_ICON_KEYS[stat] ?? ""]}
                              alt={JOB_PARAMETER_DISPLAY_LABELS[stat]}
                              className="h-3.5 w-3.5 object-contain"
                            />
                          ) : null}
                          {JOB_PARAMETER_DISPLAY_LABELS[stat]}
                        </button>
                      );
                    })}
                  </div>
                </div>

                <div className="overflow-x-auto rounded-lg border border-border">
                  <table className="w-full text-sm">
                    <thead className="bg-muted/40 text-muted-foreground">
                      <tr>
                        <th className="px-3 py-2 text-left font-medium">
                          <button type="button" onClick={() => setItemReferenceSort("name")} className="inline-flex items-center gap-1 hover:text-foreground">
                            Item {itemReferenceSortKey === "name" ? (itemReferenceSortDir === "asc" ? "up" : "down") : ""}
                          </button>
                        </th>
                        <th className="px-3 py-2 text-left font-medium">Shop source(s)</th>
                        <th className="px-3 py-2 text-left font-medium">Facility source(s)</th>
                        <th className="px-3 py-2 text-center font-medium">Copper coin</th>
                        <th className="px-3 py-2 text-center font-medium">Studio</th>
                        <th className="px-3 py-2 text-center font-medium">INT</th>
                        <th className="px-3 py-2 text-center font-medium">
                          <button type="button" onClick={() => setItemReferenceSort("exp")} className="inline-flex items-center justify-center gap-1 hover:text-foreground">
                            Exp gained {itemReferenceSortKey === "exp" ? (itemReferenceSortDir === "asc" ? "up" : "down") : ""}
                          </button>
                        </th>
                        <th className="px-3 py-2 text-left font-medium">Ally bonus effect</th>
                      </tr>
                    </thead>
                    <tbody>
                      {filteredItemFacilityRows.map((row) => {
                        const isCraftable = (row.item.shopFlag & CRAFTABLE_ITEM_FLAG) !== 0;
                        const isCooked = (row.item.shopFlag & COOKED_ITEM_FLAG) !== 0;
                        const knownFlags = getKnownItemFlagNames(row.item.shopFlag);
                        const unknownFlags = getUnknownItemFlagBits(row.item.shopFlag);
                        const classifiedAsItemShop = isCraftable && !isCooked;
                        const classification = classifiedAsItemShop ? "Item Shop" : "Facility only";
                        const binaryFlag = row.item.shopFlag.toString(2).padStart(12, "0");
                        return (
                        <tr key={row.item.name} className="border-t border-border/70 align-top">
                          <td className="px-3 py-2 font-medium text-foreground">
                            <div className="flex items-center gap-1.5">
                              {getItemIcon(row.item.name) && (
                                <img src={getItemIcon(row.item.name)!} alt="" className="h-6 w-6 shrink-0 object-contain" style={{ imageRendering: "pixelated" }} />
                              )}
                              <div>{row.item.name}</div>
                            </div>
                            {showItemReferenceDebug && (
                              <div className="mt-1.5 space-y-1 text-[11px] font-normal leading-4 text-muted-foreground">
                                <div>shopFlag: {row.item.shopFlag} (0b{binaryFlag})</div>
                                <div>known flags: {knownFlags.length > 0 ? knownFlags.join(" | ") : "none"}</div>
                                <div>unknown flag bits: {unknownFlags.length > 0 ? unknownFlags.join(", ") : "none"}</div>
                                <div>isCraftable(128): {String(isCraftable)} | hasCooked(1024): {String(isCooked)}</div>
                                <div>craftGroup: {row.item.craftGroup} | category: {row.item.category} | type: {row.item.type}</div>
                                <div>classified as: {classification}</div>
                              </div>
                            )}
                          </td>
                          <td className="px-3 py-2 text-muted-foreground">{row.sources.length > 0 ? row.sources.join(" / ") : "-"}</td>
                          <td className="px-3 py-2 text-muted-foreground">
                            {row.facilities.length > 0 ? (
                              <div className="flex flex-wrap items-center gap-2">
                                {row.facilities.map((facilityName) => {
                                  const facilityId = facilityIdByName.get(facilityName.toLowerCase());
                                  const facilityIcon = resolveItemReferenceFacilityIcon(facilityName, facilityId);
                                  return (
                                    <span key={`${row.item.name}-${facilityName}`} className="inline-flex items-center gap-1.5 rounded border border-border/70 bg-muted/25 px-1.5 py-0.5">
                                      {facilityIcon ? (
                                        <img
                                          src={facilityIcon}
                                          alt=""
                                          className="h-4 w-4 shrink-0 object-contain"
                                          style={{ imageRendering: "pixelated" }}
                                        />
                                      ) : null}
                                      <span>{facilityName}</span>
                                    </span>
                                  );
                                })}
                              </div>
                            ) : "-"}
                            {showItemReferenceDebug && (
                              <div className="mt-1 text-[11px] leading-4 text-muted-foreground/90">
                                fallbackUsed: {String(row.facilities.length > 0 && row.item.craftGroup < 0 && row.sources.includes("Item Shop"))}
                              </div>
                            )}
                          </td>
                          <td className="px-3 py-2 text-center">{row.item.buyPrice > 0 ? row.item.buyPrice.toLocaleString() : "-"}</td>
                          <td className="px-3 py-2 text-center">{row.item.studioLevel}</td>
                          <td className="px-3 py-2 text-center">{row.item.craftingIntelligence || "-"}</td>
                          <td className="px-3 py-2 text-center">{getAllyExpGained(row.item) > 0 ? `+${getAllyExpGained(row.item).toLocaleString()}` : "-"}</td>
                          <td className="px-3 py-2 text-muted-foreground">{renderAllyBonusEffect(row.item, statIcons)}</td>
                        </tr>
                      );})}
                    </tbody>
                  </table>
                </div>

                <div ref={plannerSectionRef} className="space-y-2 pt-1">
                  <h3 className="text-sm font-semibold">Ally Feed Planner</h3>
                  <p className="text-xs text-muted-foreground">Pick a job, rank, and awakening first. Max stat levels are auto-derived per stat from that profile.</p>
                </div>

                <div className="flex flex-wrap items-end gap-3">
                  <div className="w-full md:w-[300px]">
                    <label className="text-xs text-muted-foreground">Feed item</label>
                    <div className="relative mt-1" ref={feedItemDropdownRef}>
                      <Input
                        value={feedItemDropdownOpen ? feedItemQuery : selectedFeedItemName}
                        onChange={(e) => {
                          setFeedItemQuery(e.target.value);
                          setFeedItemDropdownOpen(true);
                        }}
                        onFocus={() => {
                          setFeedItemQuery(selectedFeedItemName);
                          setFeedItemDropdownOpen(true);
                        }}
                        onKeyDown={(e) => {
                          if (e.key === "Escape") {
                            setFeedItemDropdownOpen(false);
                            return;
                          }
                          if (e.key === "Enter" && filteredFeedItemOptions[0]) {
                            e.preventDefault();
                            setSelectedFeedItemName(filteredFeedItemOptions[0].name);
                            setFeedItemQuery(filteredFeedItemOptions[0].name);
                            setFeedItemDropdownOpen(false);
                          }
                        }}
                        placeholder="Type to filter feed items"
                        className={`h-9 pr-9 ${feedItemDropdownOpen ? "rounded-b-none border-primary ring-1 ring-primary" : ""}`}
                        role="combobox"
                        aria-expanded={feedItemDropdownOpen}
                        aria-controls="feed-item-results"
                      />
                      <button
                        type="button"
                        onMouseDown={(e) => {
                          e.preventDefault();
                          setFeedItemDropdownOpen((open) => !open);
                        }}
                        className="absolute right-1 top-1 flex h-7 w-7 items-center justify-center rounded text-primary hover:bg-primary/10"
                        aria-label={feedItemDropdownOpen ? "Close feed items" : "Open feed items"}
                      >
                        <ChevronDown className={`h-4 w-4 transition-transform ${feedItemDropdownOpen ? "rotate-180" : ""}`} />
                      </button>
                      {feedItemDropdownOpen && (
                        <div
                          id="feed-item-results"
                          className="absolute left-0 right-0 top-full z-50 max-h-64 overflow-y-auto rounded-b-md border border-t-0 border-primary bg-popover shadow-lg"
                        >
                          {filteredFeedItemOptions.length === 0 ? (
                            <div className="px-3 py-3 text-center text-xs text-muted-foreground">No matching feed items</div>
                          ) : (
                            filteredFeedItemOptions.map((item) => (
                              <button
                                key={item.name}
                                type="button"
                                onMouseDown={(e) => {
                                  e.preventDefault();
                                  setSelectedFeedItemName(item.name);
                                  setFeedItemQuery(item.name);
                                  setFeedItemDropdownOpen(false);
                                }}
                                className={`flex w-full items-center gap-2 px-3 py-2 text-left text-sm transition-colors hover:bg-muted ${
                                  item.name === selectedFeedItemName ? "bg-primary/10 font-medium text-foreground" : ""
                                }`}
                              >
                                {getItemIcon(item.name) && (
                                  <img src={getItemIcon(item.name)!} alt="" className="h-5 w-5 shrink-0 object-contain" style={{ imageRendering: "pixelated" }} />
                                )}
                                <span className="truncate">{item.name}</span>
                              </button>
                            ))
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                  <div className="w-full md:w-[300px]">
                    <label className="text-xs text-muted-foreground">Job</label>
                    <div className="relative mt-1" ref={jobDropdownRef}>
                      <Input
                        value={jobDropdownOpen ? jobQuery : selectedNeedExpJobName}
                        onChange={(e) => {
                          setJobQuery(e.target.value);
                          setJobDropdownOpen(true);
                        }}
                        onFocus={() => {
                          setJobQuery(selectedNeedExpJobName);
                          setJobDropdownOpen(true);
                        }}
                        onKeyDown={(e) => {
                          if (e.key === "Escape") {
                            setJobDropdownOpen(false);
                            return;
                          }
                          if (e.key === "Enter" && filteredJobOptions[0]) {
                            e.preventDefault();
                            setSelectedNeedExpJobName(filteredJobOptions[0].name);
                            setJobQuery(filteredJobOptions[0].name);
                            setJobDropdownOpen(false);
                          }
                        }}
                        placeholder="Type to filter jobs"
                        className={`h-9 pr-9 ${jobDropdownOpen ? "rounded-b-none border-primary ring-1 ring-primary" : ""}`}
                        role="combobox"
                        aria-expanded={jobDropdownOpen}
                        aria-controls="job-results"
                      />
                      <button
                        type="button"
                        onMouseDown={(e) => {
                          e.preventDefault();
                          setJobDropdownOpen((open) => !open);
                        }}
                        className="absolute right-1 top-1 flex h-7 w-7 items-center justify-center rounded text-primary hover:bg-primary/10"
                        aria-label={jobDropdownOpen ? "Close jobs" : "Open jobs"}
                      >
                        <ChevronDown className={`h-4 w-4 transition-transform ${jobDropdownOpen ? "rotate-180" : ""}`} />
                      </button>
                      {jobDropdownOpen && (
                        <div
                          id="job-results"
                          className="absolute left-0 right-0 top-full z-50 max-h-64 overflow-y-auto rounded-b-md border border-t-0 border-primary bg-popover shadow-lg"
                        >
                          {filteredJobOptions.length === 0 ? (
                            <div className="px-3 py-3 text-center text-xs text-muted-foreground">No matching jobs</div>
                          ) : (
                            filteredJobOptions.map((job) => (
                              <button
                                key={job.name}
                                type="button"
                                onMouseDown={(e) => {
                                  e.preventDefault();
                                  setSelectedNeedExpJobName(job.name);
                                  setJobQuery(job.name);
                                  setJobDropdownOpen(false);
                                }}
                                className={`flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-sm transition-colors hover:bg-muted ${
                                  job.name === selectedNeedExpJobName ? "bg-primary/10 font-medium text-foreground" : ""
                                }`}
                              >
                                <span className="truncate">{job.name}</span>
                              </button>
                            ))
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                  <div className="w-[110px]">
                    <label className="text-xs text-muted-foreground">Rank</label>
                    <select
                      value={selectedNeedExpRank}
                      onChange={(e) => setSelectedNeedExpRank(e.target.value as AllyJobRank)}
                      className="h-9 mt-1 w-full rounded-md border border-input bg-background px-2 text-sm focus:outline-none focus:ring-1 focus:ring-ring"
                    >
                      {ALLY_JOB_RANKS.map((rank) => (
                        <option key={rank} value={rank}>{rank}</option>
                      ))}
                    </select>
                  </div>
                  <div className="w-[120px]">
                    <label className="text-xs text-muted-foreground">Awakening</label>
                    <Input
                      type="number"
                      min={0}
                      max={MAX_AWAKENING}
                      value={plannerAwakeningInput}
                      onChange={(e) => setPlannerAwakeningInput(e.target.value)}
                      className="h-9 mt-1"
                    />
                  </div>
                </div>

                {selectedFeedStatKeys.length > 0 && (
                  <div className="rounded-md border border-border bg-muted/20 p-3 space-y-2">
                    <div className="text-xs text-muted-foreground">Current ally stat levels for affected stats (Current / Max from job + awakening)</div>
                    <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-2">
                      {selectedFeedStatKeys.map((key) => {
                        const expRow = selectedFeedStatExpRows.find((row) => row.key === key);
                        const statCap = expRow?.targetStatLevel ?? 1;
                        return (
                        <div key={key} className="rounded-md border border-border bg-background/60 px-2 py-1.5 flex items-center justify-between gap-2">
                          <div className="inline-flex items-center gap-1.5 min-w-0">
                            {statIcons[JOB_PARAMETER_ICON_KEYS[key] ?? ""] ? (
                              <img
                                src={statIcons[JOB_PARAMETER_ICON_KEYS[key] ?? ""]}
                                alt={JOB_PARAMETER_DISPLAY_LABELS[key]}
                                className="h-4 w-4 object-contain"
                              />
                            ) : null}
                            <span className="text-xs font-medium text-foreground">{JOB_PARAMETER_DISPLAY_LABELS[key]}</span>
                          </div>
                          <div className="inline-flex items-center gap-1">
                            <span className="text-[11px] text-muted-foreground">Lv</span>
                            <Input
                              type="number"
                              min={1}
                              max={statCap}
                              value={plannerCurrentStatInputs[key] ?? "1"}
                              onChange={(e) => {
                                const value = e.target.value;
                                setPlannerCurrentStatInputs((prev) => ({ ...prev, [key]: value }));
                              }}
                              className="h-7 w-20 text-xs px-2 text-center"
                            />
                            <span className="text-[11px] text-muted-foreground">/ {statCap}</span>
                          </div>
                        </div>
                      );})}
                    </div>
                  </div>
                )}

                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-2 text-sm">
                  <div className="rounded-md border border-border p-3">
                    <div className="text-xs text-muted-foreground">Awakening (applied)</div>
                    <div className="font-semibold">+{plannerAwakening}</div>
                  </div>
                  <div className="rounded-md border border-border p-3">
                    <div className="text-xs text-muted-foreground">Total EXP needed (max affected stat)</div>
                    <div className="font-semibold">{plannerTotalExpNeeded.toLocaleString()}</div>
                  </div>
                  <div className="rounded-md border border-border p-3">
                    <div className="text-xs text-muted-foreground">Selected item EXP each (per affected stat)</div>
                    <div className="font-semibold">{selectedFeedExpPerItem > 0 ? selectedFeedExpPerItem.toLocaleString() : "-"}</div>
                  </div>
                  <div className="rounded-md border border-border p-3">
                    <div className="text-xs text-muted-foreground">Copper coin per item</div>
                    <div className="font-semibold">{selectedFeedCopperPerItem > 0 ? selectedFeedCopperPerItem.toLocaleString() : "-"}</div>
                  </div>
                  <div className="rounded-md border border-border p-3">
                    <div className="text-xs text-muted-foreground">needExp multiplier range</div>
                    <div className="font-semibold">
                      {selectedNeedExpRange
                        ? selectedNeedExpRange.min === selectedNeedExpRange.max
                          ? `${selectedNeedExpRange.min.toFixed(1)}%`
                          : `${selectedNeedExpRange.min.toFixed(1)}% to ${selectedNeedExpRange.max.toFixed(1)}%`
                        : "-"}
                    </div>
                  </div>
                  <div className="rounded-md border border-border p-3">
                    <div className="text-xs text-muted-foreground">Items needed</div>
                    <div className="font-semibold">{feedItemsNeeded === null ? "-" : feedItemsNeeded.toLocaleString()}</div>
                  </div>
                  <div className="rounded-md border border-border p-3">
                    <div className="text-xs text-muted-foreground">Total copper coin needed</div>
                    <div className="font-semibold">{selectedFeedTotalCopperNeeded === null ? "-" : selectedFeedTotalCopperNeeded.toLocaleString()}</div>
                  </div>
                  <div className="rounded-md border border-border p-3">
                    <div className="text-xs text-muted-foreground">Earliest max hit (items)</div>
                    <div className="font-semibold">{selectedFeedEarliestCapItems === null ? "-" : selectedFeedEarliestCapItems.toLocaleString()}</div>
                  </div>
                </div>

                {selectedFeedStatProjection.length > 0 && (
                  <div className="space-y-2">
                    <div className="rounded-md border border-border bg-muted/20 px-3 py-2 text-xs text-muted-foreground">
                      Job:{" "}
                      {selectedNeedExpJob?.name ? (
                        <>
                          <EntityLink type="job" name={selectedNeedExpJob.name} className="font-semibold text-primary hover:no-underline">
                            {selectedNeedExpJob.name}
                          </EntityLink>
                          <span className="ml-1">(Rank {selectedNeedExpRank})</span>
                        </>
                      ) : (
                        <span className="font-semibold text-primary">-</span>
                      )}
                    </div>
                    <div className="overflow-x-auto rounded-lg border border-border">
                      <table className="w-full text-sm">
                      <thead className="bg-muted/40 text-muted-foreground">
                        <tr>
                          <th className="px-3 py-2 text-left font-medium">Stat</th>
                          <th className="px-3 py-2 text-center font-medium">Current</th>
                          <th className="px-3 py-2 text-center font-medium">Target</th>
                          <th className="px-3 py-2 text-center font-medium">needExp</th>
                          <th className="px-3 py-2 text-center font-medium">EXP to target</th>
                          <th className="px-3 py-2 text-center font-medium">Items to max</th>
                        </tr>
                      </thead>
                      <tbody>
                        {selectedFeedStatProjection.map((row) => {
                          const expRow = selectedFeedStatExpRows.find((expValue) => expValue.key === row.key);
                          return (
                          <tr key={row.key} className="border-t border-border/70">
                            <td className="px-3 py-2 font-medium text-foreground">
                              <span className="inline-flex items-center gap-1.5">
                                {statIcons[JOB_PARAMETER_ICON_KEYS[row.key] ?? ""] ? (
                                  <img
                                    src={statIcons[JOB_PARAMETER_ICON_KEYS[row.key] ?? ""]}
                                    alt={row.label}
                                    className="h-4 w-4 object-contain"
                                  />
                                ) : null}
                                <span>{row.label}</span>
                              </span>
                            </td>
                            <td className="px-3 py-2 text-center">Lv {row.currentStat}</td>
                            <td className="px-3 py-2 text-center">Lv {row.statCap}</td>
                            <td className="px-3 py-2 text-center">{expRow ? `${expRow.needExpPercent.toFixed(1)}%` : "-"}</td>
                            <td className="px-3 py-2 text-center">{expRow ? expRow.expNeeded.toLocaleString() : "-"}</td>
                            <td className="px-3 py-2 text-center">
                              {row.itemsToCapEarliest === null
                                ? "-"
                                : row.itemsToCapLatest === null || row.itemsToCapLatest === row.itemsToCapEarliest
                                  ? row.itemsToCapEarliest.toLocaleString()
                                  : `${row.itemsToCapEarliest.toLocaleString()} to ${row.itemsToCapLatest.toLocaleString()}`}
                            </td>
                          </tr>
                        );})}
                      </tbody>
                    </table>
                    </div>
                  </div>
                )}
              </CardContent>
            </Card>
          </div>
        )}

        {selectedShop.slug === "item-shop" && (
          <div className="space-y-4">
            <Card className="shadow-sm">
              <CardHeader className="pb-3">
                <CardTitle className="text-base">Item Shop Database</CardTitle>
                <CardDescription className="text-xs">
                  Craftable item-shop entries pulled from the item sheet by studio and intelligence requirements.
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="flex flex-wrap items-center gap-3">
                  <div className="relative">
                    <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
                    <Input value={itemSearch} onChange={(e) => setItemSearch(e.target.value)} placeholder="Search items..." className="pl-9 h-9" />
                  </div>
                  <FilterDropdowns showRank={false} />
                  <span className="text-xs text-muted-foreground">{filteredItemRows.length} items</span>
                </div>
              </CardContent>
            </Card>

            <Card className="shadow-sm">
              <CardContent className="pt-6">
                {itemLoading ? <p className="text-sm text-muted-foreground">Loading item data...</p> : <ItemTable rows={filteredItemRows} />}
              </CardContent>
            </Card>
          </div>
        )}

        {selectedShop.slug === "furniture-shop" && (
          <div className="space-y-4">
            <Card className="shadow-sm">
              <CardHeader className="pb-3">
                <CardTitle className="text-base">Furniture Shop Database</CardTitle>
                <CardDescription className="text-xs">
                  Community-confirmed furniture catalog with studio and intelligence requirements. Prices and fuller facility effects can be added once we decode them from the source cleanly.
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="flex flex-wrap items-center gap-3">
                  <div className="relative">
                    <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
                    <Input value={furnitureSearch} onChange={(e) => setFurnitureSearch(e.target.value)} placeholder="Search furniture..." className="pl-9 h-9" />
                  </div>
                  <FilterDropdowns showRank={false} />
                  <span className="text-xs text-muted-foreground">{filteredFurnitureRows.length} furniture items</span>
                </div>
              </CardContent>
            </Card>

            <Card className="shadow-sm">
              <CardContent className="pt-6">
                <FurnitureTable rows={filteredFurnitureRows} />
              </CardContent>
            </Card>
          </div>
        )}

        {selectedShop.slug === "restaurant" && (
          <div className="space-y-4">
            <Card className="shadow-sm">
              <CardHeader className="pb-3">
                <CardTitle className="text-base">Restaurant Database</CardTitle>
                <CardDescription className="text-xs">
                  Cooked goods from the item sheet with studio and intelligence requirements.
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="flex flex-wrap items-center gap-3">
                  <div className="relative">
                    <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
                    <Input value={itemSearch} onChange={(e) => setItemSearch(e.target.value)} placeholder="Search restaurant items..." className="pl-9 h-9" />
                  </div>
                  <FilterDropdowns showRank={false} />
                  <span className="text-xs text-muted-foreground">{filteredRestaurantRows.length} items</span>
                </div>
              </CardContent>
            </Card>

            <Card className="shadow-sm">
              <CardContent className="pt-6">
                {itemLoading ? <p className="text-sm text-muted-foreground">Loading restaurant data...</p> : <ItemTable rows={filteredRestaurantRows} />}
              </CardContent>
            </Card>
          </div>
        )}

        {selectedShop.slug === "orchard" && (
          <div className="space-y-4">
            <Card className="shadow-sm">
              <CardHeader className="pb-3">
                <CardTitle className="text-base">Orchard Database</CardTitle>
                <CardDescription className="text-xs">
                  Fruits and vegetables from harvest-tagged item data.
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="flex flex-wrap items-center gap-3">
                  <div className="relative">
                    <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
                    <Input value={itemSearch} onChange={(e) => setItemSearch(e.target.value)} placeholder="Search orchard items..." className="pl-9 h-9" />
                  </div>
                  <FilterDropdowns showRank={false} />
                  <span className="text-xs text-muted-foreground">{filteredOrchardRows.length} items</span>
                </div>
              </CardContent>
            </Card>

            <Card className="shadow-sm">
              <CardContent className="pt-6">
                {itemLoading ? <p className="text-sm text-muted-foreground">Loading orchard data...</p> : <ItemTable rows={filteredOrchardRows} showStudio={false} />}
              </CardContent>
            </Card>
          </div>
        )}
      </div>
    </div>
  );
}

