import { useMemo, useState } from "react";
import { googleSheetUrl } from "@/lib/api";
import { useQuery } from "@tanstack/react-query";
import {
  MessageCircle,
  Sparkles,
  Send,
  Minus,
  ChevronDown,
  ChevronUp,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { fetchSharedWithFallback } from "@/lib/local-shared-data";
import { apiUrl } from "@/lib/api";
import { EQUIPMENT_CATALOG, EQUIPMENT_EXCHANGE_ROWS } from "@/lib/generated-equipment-data";
import { KAIRO_ROOM_LOOT_GROUPS, WAIRO_DUNGEON_LOOT_GROUP } from "@/lib/special-boss-loot";
import { PLAYTHROUGH_GUIDE_SECTION_OVERLAYS } from "@/lib/playthrough-guide";
import { FACILITIES } from "@/game-data/facilities";
import { getJobProfile, getPossibleMarriageChildren, type SharedJobProfileData } from "@/game-data/job-profile";

type RawEquipmentItem = {
  uid: string;
  name: string;
  sheetSlot: string;
  baseStats: Record<string, number>;
  incStats: Record<string, number>;
  crafterStudioLevel: number;
  crafterIntelligence: number;
};

type StatOverride = { base?: number; inc?: number };

type SharedJob = {
  generation: 1 | 2;
  type?: "combat" | "non-combat";
  category?: string;
  ranks: Record<string, { stats: Record<string, { base: number; inc: number }> }>;
};

type SharedPair = {
  id: string;
  jobA: string;
  jobB: string;
  children: string[];
  affinity?: string;
};

type SkillRow = {
  name: string;
  buyPrice?: number | null;
  sellPrice?: number | null;
  studioLevel?: number | null;
  craftingIntelligence?: number | null;
};

type SharedData = {
  overrides?: Record<string, Record<string, StatOverride>>;
  slotAssignments?: Record<string, string>;
  weaponTypes?: Record<string, string>;
  jobs?: Record<string, SharedJob>;
  pairs?: SharedPair[];
  skills?: Record<string, SkillRow>;
};

type EquipmentRecord = {
  name: string;
  slot: string;
  baseStats: Record<string, number>;
  incStats: Record<string, number>;
  crafterStudioLevel: number;
  crafterIntelligence: number;
};

type CatalogEquipmentRecord = {
  id: number;
  name: string;
  type: number;
  rank: number;
  rankLabel: string;
  unlockEquip: number;
  buyPrice: number;
  requiredKairo: string;
};

type ExchangeRowRecord = {
  inputId: number;
  inputName: string;
  outputName: string;
  startPrice: number;
  priceStep: number;
  buyPrice: number;
};

type AskFacilityRecord = {
  id: number;
  name: string;
  tab: string;
  size: string;
  mapUnlock?: number;
};

type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  text: string;
  options?: string[];
};

type AnswerResult = {
  text: string;
  options?: string[];
};

type KnowledgeEntry = {
  title: string;
  body: string;
  options?: string[];
};



const SHEET_ID = "1e5t0CMBgw2MOv1NRE-vNk3229p7dYg6yJAQ8YbhYnWk";
const EQUIPMENT_GID = "123527243";
const RANKED_EQUIPMENT_NAME = /^[FSABCDE]\s*\/\s*/i;
const ITEM_RANK_ORDER = ["S", "A", "B", "C", "D", "E", "F"] as const;
const JOB_RANK_ORDER = ["S", "A", "B", "C", "D"] as const;
const VALID_SLOTS = ["Head", "Weapon", "Shield", "Armor", "Accessory"] as const;
type ValidSlot = (typeof VALID_SLOTS)[number];

const WEAPON_CATEGORY_WORDS = [
  "staff",
  "sword",
  "spear",
  "bow",
  "gun",
  "axe",
  "hammer",
  "wand",
  "book",
  "club",
  "katana",
  "knife",
] as const;

type WeaponCategory = (typeof WEAPON_CATEGORY_WORDS)[number];

const STAT_FULL: Record<string, string> = {
  hp: "HP",
  mp: "MP",
  vig: "Vigor",
  vigor: "Vigor",
  atk: "Attack",
  att: "Attack",
  attack: "Attack",
  def: "Defence",
  defence: "Defence",
  defense: "Defence",
  spd: "Speed",
  speed: "Speed",
  mov: "Move",
  move: "Move",
  movement: "Move",
  lck: "Luck",
  luck: "Luck",
  int: "Intelligence",
  intel: "Intelligence",
  intelligence: "Intelligence",
  dex: "Dexterity",
  dexterity: "Dexterity",
  gth: "Gather",
  gather: "Gather",
  hrt: "Heart",
  heart: "Heart",
};

const STAT_LABEL: Record<string, string> = {
  HP: "HP",
  MP: "MP",
  Vigor: "VIG",
  Attack: "ATK",
  Defence: "DEF",
  Speed: "SPD",
  Move: "MOV",
  Luck: "LCK",
  Intelligence: "INT",
  Dexterity: "DEX",
  Gather: "GTH",
  Heart: "HRT",
};

const DISPLAY_STAT_KEYS = [
  "HP",
  "MP",
  "Vigor",
  "Attack",
  "Defence",
  "Speed",
  "Move",
  "Luck",
  "Intelligence",
  "Dexterity",
  "Gather",
  "Heart",
] as const;

const SLOT_KEYWORDS: Array<{ slot: ValidSlot; words: string[] }> = [
  {
    slot: "Accessory",
    words: [
      "accessory",
      "accessories",
      "ring",
      "rings",
      "necklace",
      "necklaces",
      "pendant",
      "pendants",
      "amulet",
      "amulets",
      "charm",
      "charms",
      "bracelet",
      "bracelets",
      "earring",
      "earrings",
      "ornament",
      "ornaments",
      "bag",
      "bags",
      "boots",
      "sandals",
    ],
  },
  {
    slot: "Weapon",
    words: [
      "weapon",
      "weapons",
      "staff",
      "staffs",
      "sword",
      "swords",
      "spear",
      "spears",
      "bow",
      "bows",
      "gun",
      "guns",
      "axe",
      "axes",
      "hammer",
      "hammers",
      "wand",
      "wands",
      "book",
      "books",
      "club",
      "clubs",
      "katana",
      "katanas",
      "knife",
      "knives",
    ],
  },
  { slot: "Armor", words: ["armor", "armour", "robe", "robes", "cape", "capes", "mail", "coat", "coats", "clothes", "clothing", "body"] },
  { slot: "Shield", words: ["shield", "shields", "buckler", "bucklers"] },
  { slot: "Head", words: ["head", "headgear", "helmet", "helm", "hat", "hats", "cap", "caps", "hood", "hoods", "crown", "crowns", "ribbon", "ribbons", "band", "bands", "tiara"] },
];

const FURNITURE_ROWS = [
  { name: "Candle", shopLevel: 1, requiredInt: 7 },
  { name: "Kitchen Shelves", shopLevel: 1, requiredInt: 8 },
  { name: "Desk", shopLevel: 1, requiredInt: 8 },
  { name: "Red Carpet", shopLevel: 2, requiredInt: 16 },
  { name: "Decorative Plant", shopLevel: 2, requiredInt: 17 },
  { name: "Dining Table", shopLevel: 2, requiredInt: 18 },
  { name: "Study Desk", shopLevel: 3, requiredInt: 27 },
  { name: "Rainwater Barrel", shopLevel: 3, requiredInt: 28 },
  { name: "Chest of Drawers", shopLevel: 4, requiredInt: 40 },
  { name: "Flower Vase", shopLevel: 4, requiredInt: 43 },
  { name: "Shelf", shopLevel: 5, requiredInt: 57 },
  { name: "Bookshelf", shopLevel: 5, requiredInt: 60 },
  { name: "Training Room", shopLevel: 6, requiredInt: 78 },
  { name: "Rejuvenating Bath", shopLevel: 6, requiredInt: 81 },
  { name: "Flowers", shopLevel: 7, requiredInt: 101 },
  { name: "Tomato", shopLevel: 8, requiredInt: 126 },
  { name: "Dresser", shopLevel: 9, requiredInt: 152 },
  { name: "Couch", shopLevel: 9, requiredInt: 158 },
  { name: "Bathtub", shopLevel: 10, requiredInt: 188 },
  { name: "Stove", shopLevel: 10, requiredInt: 194 },
  { name: "Pansy", shopLevel: 11, requiredInt: 228 },
  { name: "Shooting Range", shopLevel: 12, requiredInt: 266 },
  { name: "Fluffy Carpet", shopLevel: 12, requiredInt: 273 },
  { name: "Cooking Counter", shopLevel: 13, requiredInt: 315 },
  { name: "Decorative Armor", shopLevel: 14, requiredInt: 360 },
  { name: "Vanity Mirror", shopLevel: 15, requiredInt: 409 },
  { name: "Window", shopLevel: 16, requiredInt: 461 },
  { name: "Magic Training Ground", shopLevel: 17, requiredInt: 516 },
  { name: "Glittering Stone", shopLevel: 18, requiredInt: 575 },
  { name: "Black Mat", shopLevel: 19, requiredInt: 637 },
  { name: "Fireplace", shopLevel: 20, requiredInt: 703 },
  { name: "Tree Nursery", shopLevel: 21, requiredInt: 772 },
  { name: "Ancestor Statue", shopLevel: 22, requiredInt: 845 },
  { name: "Animal Figurine", shopLevel: 23, requiredInt: 921 },
  { name: "Tool Workshop", shopLevel: 24, requiredInt: 1001 },
  { name: "Ore Workbench", shopLevel: 24, requiredInt: 1001 },
  { name: "Double Bed", shopLevel: 25, requiredInt: 500 },
];

const SUGGESTIONS = [
  "How much does F/ Wooden Staff cost?",
  "What does C/ Magic Staff exchange into?",
  "Where is Equipment Exchange on the map?",
  "How can I get a Royal?",
];

function makeId() {
  return Math.random().toString(36).slice(2, 10);
}

function normalize(text: string): string {
  return text.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
}

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function getItemRank(name: string): number {
  const m = name.trim().match(/^([FSABCDE])\s*\//i);
  if (!m) return ITEM_RANK_ORDER.length;
  return ITEM_RANK_ORDER.indexOf(m[1].toUpperCase() as (typeof ITEM_RANK_ORDER)[number]);
}

function getJobRank(rankName: string): number {
  const idx = JOB_RANK_ORDER.indexOf(rankName.toUpperCase() as (typeof JOB_RANK_ORDER)[number]);
  return idx >= 0 ? idx : JOB_RANK_ORDER.length;
}

function statAtLevel(base: number, inc: number, level: number): number {
  return Math.round(base + (level - 1) * inc);
}

function isPlayerFacingEquipmentName(name: string): boolean {
  if (!RANKED_EQUIPMENT_NAME.test(name)) return false;
  const lower = name.trim().toLowerCase();
  return lower !== "bare-handed" && lower !== "no equipment";
}

function cleanupItemName(name: string): string {
  return name.replace(/^[FSABCDE]\s*\/\s*/i, "").trim();
}

function fullStat(raw: string): string {
  return STAT_FULL[raw.toLowerCase().trim()] ?? raw;
}

function isStatCol(col: string): boolean {
  return !!STAT_FULL[col.toLowerCase().trim()];
}

function isIncCol(col: string): string | null {
  const lower = col.toLowerCase().replace(/[_\s/\-+]/g, "");
  for (const key of Object.keys(STAT_FULL)) {
    if (
      lower.startsWith(key) &&
      (lower.includes("inc") ||
        lower.includes("lvl") ||
        lower.includes("level") ||
        lower.includes("per") ||
        lower.endsWith("lv"))
    ) {
      return fullStat(key);
    }
  }
  return null;
}

function parseLevel(text: string, maxLevel = 999): number {
  if (/\bmax level\b/i.test(text)) return maxLevel;

  const match = text.match(/(?:level|lvl|lv)\s*(\d{1,4})/i);
  if (!match) return Math.min(maxLevel, maxLevel >= 999 ? 999 : 99);

  const parsed = Number(match[1]);
  if (!Number.isFinite(parsed)) return Math.min(maxLevel, maxLevel >= 999 ? 999 : 99);

  return Math.max(1, Math.min(maxLevel, parsed));
}

function parseStat(text: string): string | null {
  const lower = text.toLowerCase();

  if (/\bfastest\b/.test(lower)) return "Speed";
  if (/\bstrongest\b/.test(lower)) return "Attack";
  if (/\bsmartest\b/.test(lower)) return "Intelligence";
  if (/\bluckiest\b/.test(lower)) return "Luck";
  if (/\btankiest\b|\btoughest\b/.test(lower)) return "Defence";

  if (/\bint(?:elligence)?\b/.test(lower)) return "Intelligence";
  if (/\bdef(?:ence|ense)?\b/.test(lower)) return "Defence";
  if (/\batk\b|\batt\b|\battack\b/.test(lower)) return "Attack";
  if (/\bspd\b|\bspeed\b/.test(lower)) return "Speed";
  if (/\bmove(?:ment)?\b|\bmov\b/.test(lower)) return "Move";
  if (/\blck\b|\bluck\b/.test(lower)) return "Luck";
  if (/\bvig(?:or)?\b/.test(lower)) return "Vigor";
  if (/\bdex(?:terity)?\b/.test(lower)) return "Dexterity";
  if (/\bgth\b|\bgather\b/.test(lower)) return "Gather";
  if (/\bhrt\b|\bheart\b/.test(lower)) return "Heart";
  if (/\bhp\b/.test(lower)) return "HP";
  if (/\bmp\b/.test(lower)) return "MP";
  return null;
}

function parseSlot(text: string): ValidSlot | null {
  const lower = text.toLowerCase();
  for (const entry of SLOT_KEYWORDS) {
    if (entry.words.some((word) => lower.includes(word))) return entry.slot;
  }
  return null;
}

function parseWeaponCategory(text: string): WeaponCategory | null {
  const lower = normalize(text);
  for (const cat of WEAPON_CATEGORY_WORDS) {
    if (new RegExp(`\\b${escapeRegExp(cat)}s?\\b`).test(lower)) return cat;
  }
  return null;
}

function normalizeWeaponType(raw: string): string {
  return normalize(raw).replace(/\bweapons?\b/g, "").trim();
}

function getWeaponCategoryForItem(item: EquipmentRecord, shared: SharedData): WeaponCategory | null {
  const fromShared = shared.weaponTypes?.[item.name];
  const sharedNorm = normalizeWeaponType(fromShared ?? "");
  for (const cat of WEAPON_CATEGORY_WORDS) {
    if (sharedNorm.includes(cat)) return cat;
  }

  const clean = normalize(cleanupItemName(item.name));
  for (const cat of WEAPON_CATEGORY_WORDS) {
    if (clean.includes(cat)) return cat;
  }

  return null;
}

function statNamesToCheck(stat: string): string[] {
  if (stat === "Move") return ["Move", "Movement"];
  return [stat];
}

function containsMaxLevel(text: string): boolean {
  return /\bmax level\b/i.test(text);
}

function isShortRankingFollowup(question: string): boolean {
  const lower = question.toLowerCase().trim();
  return (
    /^(and\s+)?(the\s+)?(second|2nd|third|3rd)\b/.test(lower) ||
    /^(and\s+)?(the\s+)?(second|2nd|third|3rd)\s+highest\b/.test(lower) ||
    /^(and\s+)?what about\b/.test(lower)
  );
}

function parseExclusionTerms(question: string): string[] {
  const lower = normalize(question);
  const results: string[] = [];

  const patterns = [
    /\bexcluding\s+([a-z0-9 ]{1,40})$/,
    /\bexclude\s+([a-z0-9 ]{1,40})$/,
    /\bwithout\s+([a-z0-9 ]{1,40})$/,
  ];

  for (const pattern of patterns) {
    const match = lower.match(pattern);
    if (!match?.[1]) continue;

    const phrase = match[1]
      .split(/\b(at|level|max|job|class|unit|with|for|and|or)\b/)[0]
      .trim();

    if (phrase) results.push(phrase);
  }

  return Array.from(new Set(results));
}

function result(text: string, options?: string[]): AnswerResult {
  return { text, options };
}

async function fetchEquipmentSheet(): Promise<RawEquipmentItem[]> {
  const url = googleSheetUrl("equipment");
  const res = await fetch(url);
  const text = await res.text();
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

  const slotColIdx = cols.findIndex((c, i) => i !== nameColIdx && /^(slot|type|equip.?type|category|kind)$/i.test(c));
  const craftLvlIdx = cols.findIndex((c) => /crafterstudio|studio.?level|crafter.?studio/i.test(c));
  const craftIntIdx = cols.findIndex((c) => /craftermintelligence|crafter.?intel|craft.*int/i.test(c));

  const items: RawEquipmentItem[] = [];
  let uid = 0;

  for (const row of data.table.rows ?? []) {
    if (!row?.c) continue;

    const cells = row.c as Array<{ v: string | number | null } | null>;
    const get = (i: number) => (i >= 0 && i < cells.length ? cells[i]?.v ?? null : null);

    const name = String(get(nameColIdx) ?? "").trim();
    if (!name || /^\d+$/.test(name) || !isPlayerFacingEquipmentName(name)) continue;

    const sheetSlot = slotColIdx >= 0 ? String(get(slotColIdx) ?? "").trim() : "";
    const baseStats: Record<string, number> = {};
    const incStats: Record<string, number> = {};

    for (let i = 0; i < cols.length; i++) {
      if (i === nameColIdx || i === slotColIdx || i === craftLvlIdx || i === craftIntIdx) continue;

      const col = cols[i];
      if (!col) continue;

      const incName = isIncCol(col);
      if (incName) {
        incStats[incName] = Number(get(i)) || 0;
      } else if (isStatCol(col)) {
        baseStats[fullStat(col)] = Number(get(i)) || 0;
      }
    }

    items.push({
      uid: String(uid++),
      name,
      sheetSlot,
      baseStats,
      incStats,
      crafterStudioLevel: Number(get(craftLvlIdx)) || 0,
      crafterIntelligence: Number(get(craftIntIdx)) || 0,
    });
  }

  return items;
}

async function fetchSharedData(): Promise<SharedData> {
  return fetchSharedWithFallback<SharedData>(apiUrl("/shared"));
}

function buildEquipmentRecords(sheetItems: RawEquipmentItem[], shared: SharedData): EquipmentRecord[] {
  const sheetMap = new Map(sheetItems.map((item) => [item.name, item]));
  const allNames = new Set<string>([
    ...sheetItems.map((i) => i.name),
    ...Object.keys(shared.overrides ?? {}),
    ...Object.keys(shared.slotAssignments ?? {}),
  ]);

  const records: EquipmentRecord[] = [];

  for (const name of allNames) {
    const sheet = sheetMap.get(name);
    const overrides = shared.overrides?.[name] ?? {};
    const baseStats: Record<string, number> = {};
    const incStats: Record<string, number> = {};

    const statSet = new Set<string>([
      ...Object.keys(sheet?.baseStats ?? {}),
      ...Object.keys(sheet?.incStats ?? {}),
      ...Object.keys(overrides),
    ]);

    for (const stat of statSet) {
      const names = statNamesToCheck(stat);
      let base = 0;
      let inc = 0;

      for (const statName of names) {
        const ov = overrides[statName];
        if (ov?.base !== undefined) base += ov.base;
        else base += sheet?.baseStats?.[statName] ?? 0;

        if (ov?.inc !== undefined) inc += ov.inc;
        else inc += sheet?.incStats?.[statName] ?? 0;
      }

      baseStats[stat] = base;
      incStats[stat] = inc;
    }

    const slot = shared.slotAssignments?.[name] || sheet?.sheetSlot || "";

    records.push({
      name,
      slot,
      baseStats,
      incStats,
      crafterStudioLevel: sheet?.crafterStudioLevel ?? 0,
      crafterIntelligence: sheet?.crafterIntelligence ?? 0,
    });
  }

  return records.sort((a, b) => {
    const byRank = getItemRank(a.name) - getItemRank(b.name);
    if (byRank !== 0) return byRank;
    return a.name.localeCompare(b.name);
  });
}

function isExactEquipmentQuestion(question: string): boolean {
  const lower = question.toLowerCase();
  return (
    /\bwhat is\b/.test(lower) ||
    /\bhow much\b/.test(lower) ||
    /\bshow\b/.test(lower) ||
    /\bwhere can i unlock\b/.test(lower) ||
    /\bwhere do i unlock\b/.test(lower) ||
    /\bwhere can i craft\b/.test(lower) ||
    /\bwhere do i craft\b/.test(lower) ||
    /\bwhere can i get\b/.test(lower) ||
    /\bwhere do i get\b/.test(lower) ||
    /\bhow do i get\b/.test(lower) ||
    /\bhow do i unlock\b/.test(lower) ||
    /\bwhat level unlocks\b/.test(lower) ||
    RANKED_EQUIPMENT_NAME.test(question)
  );
}

function findExactEquipment(question: string, items: EquipmentRecord[]): EquipmentRecord | null {
  if (!isExactEquipmentQuestion(question)) return null;

  const q = normalize(question);

  const exactFull = items.find((item) => {
    const pattern = new RegExp(`\\b${escapeRegExp(normalize(item.name))}\\b`);
    return pattern.test(q);
  });
  if (exactFull) return exactFull;

  const phraseMatches = items.filter((item) => {
    const cleaned = normalize(cleanupItemName(item.name));
    if (cleaned.length < 4) return false;
    const pattern = new RegExp(`\\b${escapeRegExp(cleaned)}\\b`);
    return pattern.test(q);
  });

  if (phraseMatches.length === 1) return phraseMatches[0];
  if (phraseMatches.length > 1) {
    return [...phraseMatches].sort((a, b) => getItemRank(a.name) - getItemRank(b.name))[0];
  }

  return null;
}

function mapSlotToShop(slot: string): string {
  if (slot === "Weapon") return "Weapon Shop";
  if (slot === "Accessory") return "Accessory Shop";
  if (slot === "Head" || slot === "Armor" || slot === "Shield") return "Armor Shop";
  return "Shop";
}

function questionLooksLikeJobQuestion(question: string): boolean {
  const lower = question.toLowerCase();
  return (
    lower.startsWith("who ") ||
    /\bwhat job\b/.test(lower) ||
    /\bwhich job\b/.test(lower) ||
    /\bjob\b/.test(lower) ||
    /\bclass\b/.test(lower) ||
    /\bunit\b/.test(lower) ||
    /\bcombat unit\b/.test(lower) ||
    /\bcombat class\b/.test(lower) ||
    /\bnon combat unit\b/.test(lower) ||
    /\bnon-combat unit\b/.test(lower) ||
    /\bnon combat class\b/.test(lower) ||
    /\bnon-combat class\b/.test(lower) ||
    /\bmage\b/.test(lower) ||
    /\bdoctor\b/.test(lower) ||
    /\bchampion\b/.test(lower)
  );
}

function questionLooksLikeEquipmentRanking(question: string): boolean {
  return /\b(best|highest|strongest|fastest|smartest|luckiest|tankiest|toughest|top)\b/i.test(question);
}

function questionLooksLikeLoadout(question: string): boolean {
  const lower = question.toLowerCase();
  return (
    /\bloadout\b/.test(lower) ||
    /\bload out\b/.test(lower) ||
    /\bloud out\b/.test(lower) ||
    /\bfull loadout\b/.test(lower) ||
    /\bfull load out\b/.test(lower) ||
    /\bfull gear\b/.test(lower) ||
    /\bgear set\b/.test(lower) ||
    /\bone per slot\b/.test(lower) ||
    /\btotal\b/.test(lower)
  );
}

function questionLooksLikeSinglePiece(question: string): boolean {
  const lower = question.toLowerCase();
  return /\bsingle piece\b/.test(lower) || /\bsingle item\b/.test(lower) || /\bone item\b/.test(lower);
}

function wantsCraftableOnly(question: string): boolean {
  return /\bcraftable\b/.test(question.toLowerCase());
}

function wantsGrowth(question: string): boolean {
  return /\bgrowth\b/.test(question.toLowerCase()) || /\bper level\b/.test(question.toLowerCase()) || /\bincrement\b/.test(question.toLowerCase());
}

function parseOrdinal(question: string): number {
  const lower = question.toLowerCase().trim();
  if (/\bthird\b|\b3rd\b/.test(lower)) return 3;
  if (/\bsecond\b|\b2nd\b/.test(lower)) return 2;
  return 1;
}

function inferFollowupJobQuestion(question: string, messages: ChatMessage[]): string {
  const lower = question.toLowerCase().trim();

  const looksLikeFollowup =
    isShortRankingFollowup(question) ||
    (/^(and\s+)?(the\s+)?(second|2nd|third|3rd)\b/.test(lower)) ||
    (/^(and\s+)?(same|what about)\b/.test(lower) && !parseStat(question));

  if (!looksLikeFollowup) return question;

  const priorUser = [...messages]
    .reverse()
    .find((m) => {
      if (m.role !== "user") return false;
      const text = m.text.toLowerCase();
      return (
        questionLooksLikeJobQuestion(m.text) ||
        /\b(highest|best|top|second|third|2nd|3rd|strongest|fastest|smartest|luckiest|tankiest|toughest)\b/.test(text)
      );
    });

  if (!priorUser) return question;

  const currentStat = parseStat(question);
  const priorStat = parseStat(priorUser.text);
  const stat = currentStat ?? priorStat;
  if (!stat) return question;

  const ordinal = parseOrdinal(question);
  const ordinalWord = ordinal === 3 ? "third" : ordinal === 2 ? "second" : "highest";

  const currentLower = question.toLowerCase();
  const priorLower = priorUser.text.toLowerCase();

  const combatOnly =
    /\bcombat\b/.test(currentLower)
      ? !/\bnon combat\b|\bnon-combat\b/.test(currentLower)
      : /\bcombat\b/.test(priorLower) && !/\bnon combat\b|\bnon-combat\b/.test(priorLower);

  const nonCombatOnly =
    /\bnon combat\b|\bnon-combat\b/.test(currentLower) ||
    /\bnon combat\b|\bnon-combat\b/.test(priorLower);

  const explicitLevelMatch = question.match(/(?:level|lvl|lv)\s*(\d{1,4})/i);
  const priorLevelMatch = priorUser.text.match(/(?:level|lvl|lv)\s*(\d{1,4})/i);

  let rebuilt = `${ordinalWord} highest ${STAT_LABEL[stat] ?? stat} job`;

  if (combatOnly) {
    rebuilt = `${ordinalWord} highest combat ${STAT_LABEL[stat] ?? stat} job`;
  } else if (nonCombatOnly) {
    rebuilt = `${ordinalWord} highest non-combat ${STAT_LABEL[stat] ?? stat} job`;
  }

  if (containsMaxLevel(question) || containsMaxLevel(priorUser.text)) {
    rebuilt += ` at max level`;
  } else if (explicitLevelMatch) {
    rebuilt += ` at level ${explicitLevelMatch[1]}`;
  } else if (priorLevelMatch) {
    rebuilt += ` at level ${priorLevelMatch[1]}`;
  }

  const exclusions = parseExclusionTerms(question);
  if (exclusions.length > 0) {
    rebuilt += ` excluding ${exclusions.join(" and ")}`;
  }

  return rebuilt;
}

function answerTargetedClarification(question: string): AnswerResult | null {
  const lower = normalize(question);
  const stat = parseStat(question);

  if (!stat) return null;

  // stop loops after the user already picked one branch
  if (
    lower.includes("from equipment loadout") ||
    lower.includes("from equipment only") ||
    lower.includes("from maxed job") ||
    lower.includes("job + equipment")
  ) {
    return null;
  }

  // only clarify real max-possible questions
  const asksMax =
    lower.includes("max possible") ||
    lower.includes("maximum possible") ||
    lower.includes("highest possible");

  if (!asksMax) return null;

  const label = STAT_LABEL[stat] ?? stat;

  return result(`That can mean two things for ${label}. Pick one:`, [
    `highest possible ${label} from equipment only`,
    `highest possible ${label} from maxed job + equipment`,
  ]);
}

function skillDisplayName(raw: string): string {
  const lower = raw.toLowerCase().trim();
  if (/^\d+\s*hit attack$/.test(lower)) {
    const n = lower.match(/^(\d+)/)?.[1] ?? "";
    return `${n}-Hit Attack`;
  }
  return raw;
}

function findExactSkill(question: string, shared: SharedData): SkillRow | null {
  const skills = Object.values(shared.skills ?? {});
  if (skills.length === 0) return null;

  const normalizedQuestion = normalize(question);

  const exact = skills.find((skill) => {
    const clean = normalize(skillDisplayName(skill.name));
    return new RegExp(`\\b${escapeRegExp(clean)}\\b`).test(normalizedQuestion);
  });

  return exact ?? null;
}

function answerSkillLookup(question: string, shared: SharedData): AnswerResult | null {
  const lower = question.toLowerCase();
  const skill = findExactSkill(question, shared);
  if (!skill) return null;

  const asksPrice = /\bhow much\b|\bcost\b|\bprice\b|\bbuy\b|\bsell\b|\bworth\b/.test(lower);
  const asksLevel = /\blevel\b|\bstudio\b|\bunlock\b/.test(lower);
  const asksInt = /\bint\b|\bcrafting\b/.test(lower);

  if (asksPrice && !/\bsell\b/.test(lower) && !/\bbuy\b/.test(lower)) {
    return result(`${skill.name} costs $${skill.buyPrice ?? "—"}. Sell price: $${skill.sellPrice ?? "—"}. Studio level: ${skill.studioLevel ?? "—"}. Crafting INT: ${skill.craftingIntelligence ?? "—"}.`);
  }
  if (/\bsell\b/.test(lower)) {
    return result(`${skill.name} sells for $${skill.sellPrice ?? "—"}. Buy price: $${skill.buyPrice ?? "—"}.`);
  }
  if (/\bbuy\b/.test(lower) || /\bcost\b/.test(lower) || /\bprice\b/.test(lower)) {
    return result(`${skill.name} costs $${skill.buyPrice ?? "—"}. Sell price: $${skill.sellPrice ?? "—"}.`);
  }
  if (asksLevel || asksInt) {
    return result(`${skill.name} unlocks at studio level ${skill.studioLevel ?? "—"} and needs ${skill.craftingIntelligence ?? "—"} Crafting INT. Buy price: $${skill.buyPrice ?? "—"}.`);
  }

  return result(`${skill.name}: buy $${skill.buyPrice ?? "—"}, sell $${skill.sellPrice ?? "—"}, studio level ${skill.studioLevel ?? "—"}, Crafting INT ${skill.craftingIntelligence ?? "—"}.`);
}

function answerFurnitureUnlock(question: string): AnswerResult | null {
  const lower = question.toLowerCase();
  const asksUnlock = /\b(unlock|where can i|get|craft|make|how do i get|how do i unlock)\b/.test(lower);
  if (!asksUnlock) return null;

  const row = FURNITURE_ROWS.find((entry) => lower.includes(entry.name.toLowerCase()));
  if (!row) return null;

  return result(`${row.name} unlocks at Furniture Shop level ${row.shopLevel} and needs ${row.requiredInt} INT.`);
}

function findExactCatalogEquipment(question: string, items: readonly CatalogEquipmentRecord[]): CatalogEquipmentRecord | null {
  const q = normalize(question);

  const exactFull = items.find((item) => {
    const pattern = new RegExp(`\\b${escapeRegExp(normalize(item.name))}\\b`);
    return pattern.test(q);
  });
  if (exactFull) return exactFull;

  const phraseMatches = items.filter((item) => {
    const cleaned = normalize(cleanupItemName(item.name));
    if (cleaned.length < 4) return false;
    const pattern = new RegExp(`\\b${escapeRegExp(cleaned)}\\b`);
    return pattern.test(q);
  });

  if (phraseMatches.length === 1) return phraseMatches[0];
  if (phraseMatches.length > 1) {
    return [...phraseMatches].sort((a, b) => getItemRank(a.name) - getItemRank(b.name))[0];
  }

  return null;
}

function answerEquipmentPrice(question: string, catalog: readonly CatalogEquipmentRecord[]): AnswerResult | null {
  const lower = question.toLowerCase();
  if (!/\b(price|cost|buy price|buy cost|copper|coin|coins|how much)\b/.test(lower)) return null;

  const item = findExactCatalogEquipment(question, catalog);
  if (!item) return null;

  return result(`${item.name} costs ${item.buyPrice} copper coins.`);
}

function answerEquipmentExchangeInfo(question: string, exchangeRows: readonly ExchangeRowRecord[]): AnswerResult | null {
  const lower = question.toLowerCase();
  const row = exchangeRows.find((entry) => {
    const full = new RegExp(`\\b${escapeRegExp(normalize(entry.inputName))}\\b`);
    const clean = new RegExp(`\\b${escapeRegExp(normalize(cleanupItemName(entry.inputName)))}\\b`);
    const q = normalize(question);
    return full.test(q) || clean.test(q);
  });
  if (!row) return null;

  if (/\b(exchange|trade|kairo)\b/.test(lower)) {
    return result(
      `${row.inputName} exchanges into ${row.outputName}. ` +
      `Its exchange starts at ${row.startPrice} and goes up by ${row.priceStep} each time for that exact item. ` +
      `Its buy price is ${row.buyPrice} copper coins.`
    );
  }

  return null;
}

function answerFacilityLookup(question: string, facilities: readonly AskFacilityRecord[]): AnswerResult | null {
  const lower = question.toLowerCase();
  if (!/\b(where|location|map|unlock|facility)\b/.test(lower)) return null;

  const q = normalize(question);
  const facility = facilities.find((entry) => {
    const full = new RegExp(`\\b${escapeRegExp(normalize(entry.name))}\\b`);
    return full.test(q);
  });
  if (!facility) return null;

  const unlock = facility.mapUnlock ? ` It appears on the map at unlock level ${facility.mapUnlock}.` : "";
  return result(`${facility.name} is a ${facility.size} map facility in Houses & Facilities > Map.${unlock}`);
}

function answerEquipmentExactStat(question: string, items: EquipmentRecord[]): AnswerResult | null {
  const item = findExactEquipment(question, items);
  const stat = parseStat(question);
  if (!item || !stat) return null;

  const level = Math.min(parseLevel(question, 99), 99);
  const base = item.baseStats[stat] ?? 0;
  const inc = item.incStats[stat] ?? 0;
  const value = statAtLevel(base, inc, level);

  return result(`${item.name} has ${value} ${STAT_LABEL[stat] ?? stat} at level ${level}. Base ${STAT_LABEL[stat] ?? stat}: ${base}. Growth: +${inc} per level.`);
}

function answerEquipmentUnlock(question: string, items: EquipmentRecord[]): AnswerResult | null {
  const lower = question.toLowerCase();
  if (!/\b(unlock|where can i|get|craft|make|how do i get|how do i unlock)\b/.test(lower)) return null;

  const item = findExactEquipment(question, items);
  if (!item) return null;

  if (item.crafterStudioLevel <= 0) {
    return result(`${item.name} is marked as not craftable on the current site data.`);
  }

  const shop = mapSlotToShop(item.slot);
  return result(`${item.name} unlocks at ${shop} level ${item.crafterStudioLevel} and needs ${item.crafterIntelligence} INT.`);
}

function scoreEquipment(item: EquipmentRecord, stat: string, level: number, useGrowthOnly: boolean): { value: number; base: number; inc: number } {
  const base = item.baseStats[stat] ?? 0;
  const inc = item.incStats[stat] ?? 0;
  const value = useGrowthOnly ? inc : statAtLevel(base, inc, level);
  return { value, base, inc };
}

function filterCraftable(items: EquipmentRecord[], craftableOnly: boolean): EquipmentRecord[] {
  if (!craftableOnly) return items;
  return items.filter((item) => item.crafterStudioLevel > 0);
}

function answerBestEquipment(question: string, items: EquipmentRecord[], shared: SharedData): AnswerResult | null {
  if (!questionLooksLikeEquipmentRanking(question)) return null;
  if (questionLooksLikeJobQuestion(question)) return null;
  if (questionLooksLikeLoadout(question)) return null;

  const stat = parseStat(question);
  if (!stat) return null;

  const lower = question.toLowerCase();
  const slot = parseSlot(question);
  const craftableOnly = wantsCraftableOnly(question);
  const level = Math.min(parseLevel(question, 99), 99);
  const useGrowthOnly = wantsGrowth(question);
  const asksGenericItem = /\b(item|equipment|piece)\b/.test(lower);
  const weaponCategory = parseWeaponCategory(question);

  let pool = filterCraftable(items, craftableOnly);

  if (slot) {
    pool = pool.filter((item) => item.slot === slot);
  }

  if (weaponCategory) {
    pool = pool.filter((item) => item.slot === "Weapon" && getWeaponCategoryForItem(item, shared) === weaponCategory);
  }

  if (questionLooksLikeSinglePiece(question) && !slot && !weaponCategory) {
    // all items
  } else if (!slot && !weaponCategory && asksGenericItem) {
    // generic item query
  } else if (!slot && !weaponCategory) {
    return null;
  }

  const ranked = pool
    .map((item) => {
      const scored = scoreEquipment(item, stat, level, useGrowthOnly);
      return { item, ...scored };
    })
    .filter((entry) => entry.value > 0)
    .sort((a, b) => {
      if (b.value !== a.value) return b.value - a.value;
      if (b.inc !== a.inc) return b.inc - a.inc;
      if (b.base !== a.base) return b.base - a.base;
      return getItemRank(a.item.name) - getItemRank(b.item.name);
    });

  const best = ranked[0];
  if (!best) {
    const qualifier = weaponCategory
      ? `${weaponCategory} `
      : slot
        ? `${slot.toLowerCase()} `
        : "";
    const craftText = craftableOnly ? "craftable " : "";
    return result(`I could not find any ${craftText}${qualifier}item with non-zero ${STAT_LABEL[stat] ?? stat} in the current site data.`);
  }

  const subject = weaponCategory
    ? `${weaponCategory} weapons`
    : slot
      ? `${slot.toLowerCase()} items`
      : "single equipment pieces";

  const craftText = craftableOnly ? " craftable" : "";

  if (useGrowthOnly) {
    return result(`${best.item.name} has the highest ${STAT_LABEL[stat] ?? stat} growth among${craftText} ${subject}. It gains +${best.inc} per level, with base ${best.base}.`);
  }

  return result(`${best.item.name} has the highest ${STAT_LABEL[stat] ?? stat} among${craftText} ${subject} at level ${level}. It reaches ${best.value}, with base ${best.base} and +${best.inc} per level.`);
}

function answerBestLoadout(question: string, items: EquipmentRecord[]): AnswerResult | null {
  const lower = question.toLowerCase();

  const impliedLoadout =
    /\b(best|highest|top|strongest|fastest|smartest|luckiest|tankiest|toughest)\b/.test(lower) &&
    /\b(full gear|gear set|loadout|load out|loud out)\b/.test(lower);

  if (!questionLooksLikeLoadout(question) && !impliedLoadout) return null;

  const stat = parseStat(question);
  if (!stat) return null;

  const craftableOnly = wantsCraftableOnly(question);
  const level = Math.min(parseLevel(question, 99), 99);
  const useGrowthOnly = wantsGrowth(question);

  const pool = filterCraftable(items, craftableOnly);

  const winners: Array<{ slot: ValidSlot; item: EquipmentRecord; value: number; base: number; inc: number }> = [];

  for (const slot of VALID_SLOTS) {
    const best = pool
      .filter((item) => item.slot === slot)
      .map((item) => ({ item, ...scoreEquipment(item, stat, level, useGrowthOnly) }))
      .filter((entry) => entry.value > 0)
      .sort((a, b) => {
        if (b.value !== a.value) return b.value - a.value;
        if (b.inc !== a.inc) return b.inc - a.inc;
        if (b.base !== a.base) return b.base - a.base;
        return getItemRank(a.item.name) - getItemRank(b.item.name);
      })[0];

    if (best) winners.push({ slot, ...best });
  }

  if (winners.length === 0) {
    const craftText = craftableOnly ? " craftable" : "";
    return result(`I could not build a${craftText} loadout with non-zero ${STAT_LABEL[stat] ?? stat}.`);
  }

  const total = winners.reduce((sum, row) => sum + row.value, 0);
  const label = STAT_LABEL[stat] ?? stat;
  const lines = winners.map((row) => `• ${row.slot}: ${row.item.name} (${useGrowthOnly ? `+${row.inc}` : row.value} ${label})`);
  const craftText = craftableOnly ? " craftable" : "";

  const lead = useGrowthOnly
    ? `Best total ${label} growth${craftText} full loadout:`
    : `Best total ${label}${craftText} full loadout at level ${level}:`;

  return result(`${lead}\n${lines.join("\n")}\nTotal ${label}: ${total}`);
}

function answerCombinedMax(question: string, items: EquipmentRecord[], shared: SharedData): AnswerResult | null {
  const lower = normalize(question);
  const stat = parseStat(question);
  if (!stat) return null;

  if (!/\b(max|maximum|highest)\s+(possible\s+)?/.test(lower)) return null;
  if (!/\b(job\b.*equipment|equipment\b.*job|maxed job|maxed job \+ equipment|maxed job and equipment|maxed|loadout)\b/.test(lower)) return null;

  const level = 99;
  const jobs = Object.entries(shared.jobs ?? {});
  if (jobs.length === 0) return result("I could not read any jobs from the site data.");

  const bestJob = jobs
    .flatMap(([name, job]) =>
      Object.entries(job.ranks ?? {}).map(([rankName, rank]) => {
        const entry = rank.stats?.[stat];
        if (!entry) return null;
        return {
          name,
          rankName,
          value: statAtLevel(entry.base, entry.inc, 999),
        };
      })
    )
    .filter((row): row is NonNullable<typeof row> => Boolean(row))
    .sort((a, b) => b.value - a.value || getJobRank(a.rankName) - getJobRank(b.rankName))[0];

  if (!bestJob) return result(`I could not find any job with ${STAT_LABEL[stat] ?? stat} data.`);

  const winners: Array<{ slot: ValidSlot; item: EquipmentRecord; value: number }> = [];

  for (const slot of VALID_SLOTS) {
    const best = items
      .filter((item) => item.slot === slot)
      .map((item) => ({
        item,
        value: statAtLevel(item.baseStats[stat] ?? 0, item.incStats[stat] ?? 0, level),
      }))
      .filter((row) => row.value > 0)
      .sort((a, b) => b.value - a.value || getItemRank(a.item.name) - getItemRank(b.item.name))[0];

    if (best) winners.push({ slot, item: best.item, value: best.value });
  }

  const equipTotal = winners.reduce((sum, row) => sum + row.value, 0);
  const total = bestJob.value + equipTotal;
  const label = STAT_LABEL[stat] ?? stat;
  const lines = winners.map((row) => `• ${row.slot}: ${row.item.name} (${row.value} ${label})`);

  return result(
    `Highest possible ${label} from maxed job + equipment loadout:\n` +
      `Best job: ${bestJob.name} (${bestJob.rankName}) = ${bestJob.value} ${label} at level 999\n` +
      `${lines.join("\n")}\n` +
      `Equipment total: ${equipTotal}\n` +
      `Grand total: ${total}`
  );
}

function answerComparison(question: string, items: EquipmentRecord[]): AnswerResult | null {
  const lower = question.toLowerCase();
  if (!/\bbetter\b|\bstronger\b|\bcompare\b|\bvs\b|\bversus\b|\bor\b/.test(lower)) return null;

  const normalized = normalize(question);

  const findBySegment = (segment: string): EquipmentRecord | null => {
    const cleanSegment = normalize(segment);
    if (!cleanSegment) return null;

    const exactFull = items.find(
      (item) => new RegExp(`\\b${escapeRegExp(normalize(item.name))}\\b`).test(cleanSegment)
    );
    if (exactFull) return exactFull;

    const exactClean = items.find((item) => {
      const cleanName = normalize(cleanupItemName(item.name));
      return cleanName.length >= 4 && new RegExp(`\\b${escapeRegExp(cleanName)}\\b`).test(cleanSegment);
    });
    if (exactClean) return exactClean;

    const partials = items.filter((item) => {
      const cleanName = normalize(cleanupItemName(item.name));
      if (cleanName.length < 4) return false;
      const words = cleanName.split(" ").filter(Boolean);
      return words.length > 0 && words.every((w) => cleanSegment.includes(w) || w.includes(cleanSegment));
    });

    return partials.length
      ? partials.sort((a, b) => getItemRank(a.name) - getItemRank(b.name))[0]
      : null;
  };

  const cleanedQuestion = question
    .replace(/^what\s+is\s+better\s+/i, "")
    .replace(/^which\s+is\s+better\s+/i, "")
    .replace(/^compare\s+/i, "");

  let left: EquipmentRecord | null = null;
  let right: EquipmentRecord | null = null;

  const splitMatch = cleanedQuestion.split(/\bvs\b|\bversus\b|\bor\b/i).map((s) => s.trim()).filter(Boolean);
  if (splitMatch.length >= 2) {
    left = findBySegment(splitMatch[0]);
    right = findBySegment(splitMatch[1]);
  }

  if (!left || !right) {
    const found = items.filter((item) => {
      const full = new RegExp(`\\b${escapeRegExp(normalize(item.name))}\\b`);
      const cleanName = normalize(cleanupItemName(item.name));
      if (cleanName.length < 4) return full.test(normalized);
      const clean = new RegExp(`\\b${escapeRegExp(cleanName)}\\b`);
      return full.test(normalized) || clean.test(normalized);
    });

    const unique = Array.from(new Map(found.map((item) => [item.name, item])).values());
    left = left ?? unique[0] ?? null;
    right = right ?? unique[1] ?? null;
  }

  if (!left || !right) return null;

  const stat = parseStat(question);
  const level = Math.min(parseLevel(question, 99), 99);

  if (stat) {
    const av = statAtLevel(left.baseStats[stat] ?? 0, left.incStats[stat] ?? 0, level);
    const bv = statAtLevel(right.baseStats[stat] ?? 0, right.incStats[stat] ?? 0, level);

    if (av === bv) {
      return result(`${left.name} and ${right.name} are tied for ${STAT_LABEL[stat] ?? stat} at level ${level}, at ${av}.`);
    }

    const winner = av > bv ? left : right;
    const loser = av > bv ? right : left;
    const winVal = Math.max(av, bv);
    const loseVal = Math.min(av, bv);

    return result(`${winner.name} is better for ${STAT_LABEL[stat] ?? stat} at level ${level}. It has ${winVal}, while ${loser.name} has ${loseVal}.`);
  }

  const totalA = DISPLAY_STAT_KEYS.reduce(
    (sum, key) => sum + statAtLevel(left.baseStats[key] ?? 0, left.incStats[key] ?? 0, level),
    0
  );
  const totalB = DISPLAY_STAT_KEYS.reduce(
    (sum, key) => sum + statAtLevel(right.baseStats[key] ?? 0, right.incStats[key] ?? 0, level),
    0
  );

  if (totalA === totalB) {
    return result(`${left.name} and ${right.name} are tied on total displayed stats at level ${level}, at ${totalA}.`);
  }

  const winner = totalA > totalB ? left : right;
  const loser = totalA > totalB ? right : left;
  const winVal = Math.max(totalA, totalB);
  const loseVal = Math.min(totalA, totalB);

  return result(`${winner.name} has the higher total displayed stats at level ${level}. It totals ${winVal}, while ${loser.name} totals ${loseVal}.`);
}

function answerBestJob(question: string, shared: SharedData, messages: ChatMessage[] = []): AnswerResult | null {
  const resolvedQuestion = inferFollowupJobQuestion(question, messages);
  const lower = resolvedQuestion.toLowerCase();
  const stat = parseStat(resolvedQuestion);
  if (!stat) return null;

  const looksLikeRankedJobQuestion =
    questionLooksLikeJobQuestion(resolvedQuestion) ||
    (/\b(best|highest|top|second|third|2nd|3rd|strongest|fastest|smartest|luckiest|tankiest|toughest)\b/.test(lower) &&
      /\b(unit|class|profession|job|who)\b/.test(lower));

  if (!looksLikeRankedJobQuestion) return null;

  const exclusions = parseExclusionTerms(resolvedQuestion);

  const rankIndex = parseOrdinal(resolvedQuestion) - 1;
  const level = parseLevel(resolvedQuestion, 999);
  const combatOnly = /\bcombat\b/.test(lower) && !/\bnon combat\b|\bnon-combat\b/.test(lower);
  const nonCombatOnly = /\bnon combat\b|\bnon-combat\b/.test(lower);

  const jobs = Object.entries(shared.jobs ?? {});
  if (jobs.length === 0) return result("I could not read any jobs from the site data.");

  const ranked = jobs
    .filter(([name, job]) => {
      if (combatOnly && job.type !== "combat") return false;
      if (nonCombatOnly && job.type !== "non-combat") return false;

      const cleanName = normalize(name);
      if (exclusions.some((term) => cleanName.includes(normalize(term)))) return false;

      return true;
    })
    .flatMap(([name, job]) =>
      Object.entries(job.ranks ?? {}).map(([rankName, rank]) => {
        const entry = rank.stats?.[stat];
        if (!entry) return null;

        const value = statAtLevel(entry.base, entry.inc, level);
        return { name, rankName, type: job.type ?? "unknown", value, base: entry.base, inc: entry.inc };
      })
    )
    .filter((entry): entry is NonNullable<typeof entry> => Boolean(entry))
    .sort((a, b) => {
      if (b.value !== a.value) return b.value - a.value;
      if (b.inc !== a.inc) return b.inc - a.inc;
      if (b.base !== a.base) return b.base - a.base;
      return getJobRank(a.rankName) - getJobRank(b.rankName);
    });

  const deduped = Array.from(new Map(ranked.map((row) => [row.name, row])).values());
  const picked = deduped[rankIndex];

  if (!picked) {
    const ord = rankIndex === 1 ? "second" : rankIndex === 2 ? "third" : `${rankIndex + 1}th`;
    return result(`I could not find a ${ord} matching job with ${STAT_LABEL[stat] ?? stat} data.`);
  }

  const typeText = combatOnly ? " combat" : nonCombatOnly ? " non-combat" : "";
  const ordLabel =
    rankIndex === 0 ? "highest" : rankIndex === 1 ? "second-highest" : rankIndex === 2 ? "third-highest" : `${rankIndex + 1}th-highest`;

  const exclusionText = exclusions.length > 0 ? `, excluding ${exclusions.join(" and ")}` : "";

  return result(`${picked.name} has the ${ordLabel}${typeText} ${STAT_LABEL[stat] ?? stat} at level ${level} on the current site data${exclusionText}, using rank ${picked.rankName}. It reaches ${picked.value}, with base ${picked.base} and +${picked.inc} per level.`);
}

function answerJobRelations(question: string, shared: SharedData): AnswerResult | null {
  const lower = question.toLowerCase();
  if (!/\b(job|shop|building|house|survey|marriage|child|children|open|unlock|use)\b/.test(lower)) return null;

  const jobName = Object.keys(shared.jobs ?? {}).find((name) => {
    const clean = normalize(name);
    return clean.length >= 4 && new RegExp(`\\b${escapeRegExp(clean)}\\b`).test(normalize(question));
  });
  if (!jobName) return null;

  const profile = getJobProfile(shared as SharedJobProfileData, jobName);
  if (!profile) return null;

  const lines = [`${profile.name} job facts from the shared data:`];
  if (profile.buildings.length > 0) {
    lines.push(`Shops/buildings: ${profile.buildings.map((building) => `${building.buildingName}${building.rankNote ? ` (${building.rankNote})` : ""}`).join(", ")}`);
  }
  if (profile.surveyCapable) {
    lines.push("Survey: can build Survey Corps HQ.");
  }
  if (profile.marriage.children.length > 0) {
    lines.push(`Marriage children: ${profile.marriage.children.join(", ")}.`);
  }
  if (profile.equipmentAccess.shield) {
    lines.push(`Shield access: ${profile.equipmentAccess.shield}.`);
  }

  return lines.length > 1 ? result(lines.join("\n")) : null;
}

function answerShop(question: string): AnswerResult | null {
  const lower = question.toLowerCase();

  if (/\bcape\b|\bcapes\b/.test(lower)) return result("Capes are in the Armor Shop.");
  if (/\b(accessory|ring|pendant|amulet|necklace)\b/.test(lower) && /\b(shop|where|buy|craft|make)\b/.test(lower)) {
    return result("Accessories are in the Accessory Shop.");
  }
  if (/\b(weapon|sword|staff|spear|bow|gun|axe|hammer|book|club)\b/.test(lower) && /\b(shop|where|buy|craft|make)\b/.test(lower)) {
    return result("Weapons are in the Weapon Shop.");
  }
  if (/\b(armor|armour|shield|headgear|hat|helmet)\b/.test(lower) && /\b(shop|where|buy|craft|make)\b/.test(lower)) {
    return result("Armor, shields, and headgear are in the Armor Shop.");
  }

  return null;
}

function answerPairs(question: string, shared: SharedData): AnswerResult | null {
  const lower = question.toLowerCase();
  const hasMarriageIntent =
    /\bparent\b|\bmarry\b|\bbreed\b|\bchild\b|\bpair\b|\bcompatibility\b|\bhow can i get a royal\b|\bhow do i get a royal\b/.test(lower);

  const asksRoyal =
    /\broyal\b/.test(lower) &&
    !questionLooksLikeJobQuestion(question) &&
    !questionLooksLikeLoadout(question) &&
    !questionLooksLikeEquipmentRanking(question) &&
    !parseStat(question);

  const asksMonarchOnly =
    /^\s*monarch\s*$/i.test(question) ||
    /^\s*royal\s*$/i.test(question);

  if (!hasMarriageIntent && !asksRoyal && !asksMonarchOnly) return null;

  const pairs = shared.pairs ?? [];
  if (pairs.length === 0) return result("I could not read any pair data from the site.");

  const normalizedQuestion = normalize(question);
  const royalPairs = pairs.filter((pair) =>
    getPossibleMarriageChildren({ pairs }, pair.jobA, pair.jobB, { includeParentInheritance: false })
      .some((child) => normalize(child) === "royal")
  );

  const requestedAffinity = (() => {
    if (/\b(highest|best)\s+compatibility\b/i.test(question)) return "A";
    const match = lower.match(/\b([abcde])\s+compatibility\b|\bcompatibility\s+([abcde])\b|\baffinity\s+([abcde])\b/);
    if (!match) return null;
    return (match[1] || match[2] || match[3] || "").toUpperCase() || null;
  })();

  const knownJobs = Array.from(
    new Set(
      pairs
        .flatMap((pair) => [pair.jobA, pair.jobB, ...getPossibleMarriageChildren({ pairs }, pair.jobA, pair.jobB)])
        .map((name) => name.trim())
        .filter(Boolean)
    )
  );

  const requestedJobs = knownJobs.filter((job) => {
    const clean = normalize(job);
    if (clean.length < 4) return false;
    return new RegExp(`\\b${escapeRegExp(clean)}\\b`).test(normalizedQuestion);
  });

  let filtered = asksRoyal || asksMonarchOnly ? royalPairs : pairs;

  if (requestedAffinity) {
    filtered = filtered.filter((pair) => (pair.affinity || "").toUpperCase() === requestedAffinity);
  }

  if (requestedJobs.length > 0) {
    filtered = filtered.filter((pair) => {
      const hay = [pair.jobA, pair.jobB, ...getPossibleMarriageChildren({ pairs }, pair.jobA, pair.jobB)].map(normalize);
      return requestedJobs.some((job) => hay.includes(normalize(job)));
    });
  }

  if (asksRoyal || asksMonarchOnly || /\bmonarch\b/.test(lower)) {
    const grouped = new Map<string, string[]>();

    for (const pair of filtered) {
      const affinity = (pair.affinity || "?").toUpperCase();
      const left = pair.jobA;
      const right = pair.jobB;
      const display =
        normalize(left) === "monarch"
          ? `Monarch + ${right}`
          : normalize(right) === "monarch"
            ? `Monarch + ${left}`
            : `${left} + ${right}`;

      if (!grouped.has(affinity)) grouped.set(affinity, []);
      grouped.get(affinity)!.push(display);
    }

    const affinityOrder = ["A", "B", "C", "D", "E", "?"];
    const lines = affinityOrder
      .filter((aff) => grouped.has(aff))
      .map((aff) => {
        const values = Array.from(new Set(grouped.get(aff)!)).sort((a, b) => a.localeCompare(b));
        const label =
          aff === "?"
            ? "Unknown compatibility"
            : aff === "A" && /\b(highest|best)\s+compatibility\b/i.test(question)
              ? "A Compatibility (highest)"
              : `${aff} Compatibility`;
        return `${label}: ${values.join("; ")}`;
      });

    if (lines.length === 0) {
      return result("I could not find any matching Royal child pairs in the current site data.");
    }

    return result(`Royal child pairs:\n${lines.join("\n")}`);
  }

  const shown = filtered.slice(0, 12).map((pair) => {
    const children = getPossibleMarriageChildren({ pairs }, pair.jobA, pair.jobB, { includeParentInheritance: false }).join(", ");
    const affinity = pair.affinity ? ` (${pair.affinity})` : "";
    return `${pair.jobA} + ${pair.jobB}${affinity} -> ${children}`;
  });

  if (shown.length === 0) {
    return result("I could not find any matching parent-child pairs in the current site data.");
  }

  return result(`Matching parent-child pairs:\n${shown.join("\n")}`);
}

function answerWeeklyConquest(question: string): AnswerResult | null {
  const lower = question.toLowerCase();
  if (!/\bweekly conquest\b|\bthis week\b/.test(lower)) return null;
  return result("Weekly Conquest is tracked on the Weekly Conquest page and the World Map layer. I do not answer the live current rotation inside Ask Database yet.");
}

function tokenizeKnowledge(text: string): string[] {
  return normalize(text)
    .split(/\s+/)
    .map((part) => part.trim())
    .filter((part) => part.length >= 2);
}

function scoreKnowledgeEntry(question: string, entry: KnowledgeEntry): number {
  const tokens = Array.from(new Set(tokenizeKnowledge(question)));
  if (tokens.length === 0) return 0;

  const haystack = normalize(`${entry.title} ${entry.body}`);
  let score = 0;

  for (const token of tokens) {
    if (haystack.includes(token)) {
      score += token.length >= 6 ? 3 : 2;
      if (new RegExp(`\\b${escapeRegExp(token)}\\b`).test(haystack)) {
        score += 2;
      }
      if (normalize(entry.title).includes(token)) {
        score += 3;
      }
    }
  }

  return score;
}

function answerKnowledgeIndex(question: string, knowledge: readonly KnowledgeEntry[]): AnswerResult | null {
  const trimmed = question.trim();
  if (!trimmed) return null;

  const scored = knowledge
    .map((entry) => ({ entry, score: scoreKnowledgeEntry(trimmed, entry) }))
    .filter((entry) => entry.score > 0)
    .sort((a, b) => b.score - a.score);

  if (scored.length === 0 || scored[0].score < 4) return null;

  const top = scored.slice(0, 3);
  const primary = top[0].entry;
  const followups = top
    .slice(1)
    .map(({ entry }) => entry.title)
    .filter((title) => title !== primary.title);

  const extra = followups.length > 0 ? `\n\nRelated site info: ${followups.join("; ")}` : "";
  return result(`${primary.title}\n${primary.body}${extra}`, primary.options);
}

function answerOwnedInventory(question: string): AnswerResult | null {
  const lower = question.toLowerCase();
  if (!/\bi own\b|\bowned\b|\bmy gear\b|\bmy equipment\b/.test(lower)) return null;
  return result("I do not know your owned gear yet. I can answer database-wide, craftable, and full-loadout questions, but personal inventory is not wired in yet.");
}

function answerQuestion(
  question: string,
  items: EquipmentRecord[],
  catalog: readonly CatalogEquipmentRecord[],
  exchangeRows: readonly ExchangeRowRecord[],
  facilities: readonly AskFacilityRecord[],
  knowledge: readonly KnowledgeEntry[],
  shared: SharedData,
  messages: ChatMessage[] = []
): AnswerResult {
  return (
    answerTargetedClarification(question) ??
    answerOwnedInventory(question) ??
    answerEquipmentPrice(question, catalog) ??
    answerEquipmentExchangeInfo(question, exchangeRows) ??
    answerFacilityLookup(question, facilities) ??
    answerSkillLookup(question, shared) ??
    answerFurnitureUnlock(question) ??
    answerEquipmentUnlock(question, items) ??
    answerEquipmentExactStat(question, items) ??
    answerCombinedMax(question, items, shared) ??
    answerComparison(question, items) ??
    answerJobRelations(question, shared) ??
    answerBestJob(question, shared, messages) ??
    answerBestLoadout(question, items) ??
    answerBestEquipment(question, items, shared) ??
    answerPairs(question, shared) ??
    answerWeeklyConquest(question) ??
    answerShop(question) ??
    answerKnowledgeIndex(question, knowledge) ??
    result("I do not know that one yet.")
  );
}

type AskDatabaseWidgetProps = {
  initialOpen?: boolean;
};

export default function AskDatabaseWidget({ initialOpen = false }: AskDatabaseWidgetProps) {
  const [open, setOpen] = useState(initialOpen);
  const [guideOpen, setGuideOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      id: makeId(),
      role: "assistant",
      text: "Ask me about equipment prices, exchange routes, jobs, skills, furniture unlocks, map facilities, shops, loadouts, or Royal child pairs.",
    },
  ]);

  const { data: sheetItems = [], isLoading: sheetLoading } = useQuery({
    queryKey: ["askdb-equipment-sheet"],
    queryFn: fetchEquipmentSheet,
    enabled: open,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  });

  const { data: shared = {}, isLoading: sharedLoading } = useQuery({
    queryKey: ["askdb-shared"],
    queryFn: fetchSharedData,
    enabled: open,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  });

  const equipmentRecords = useMemo(() => buildEquipmentRecords(sheetItems, shared), [sheetItems, shared]);
  const catalogRecords = useMemo(() => EQUIPMENT_CATALOG as readonly CatalogEquipmentRecord[], []);
  const exchangeRecords = useMemo(() => EQUIPMENT_EXCHANGE_ROWS as readonly ExchangeRowRecord[], []);
  const facilityRecords = useMemo(() => (FACILITIES as readonly AskFacilityRecord[]).filter((entry) => entry.tab === "map"), []);
  const knowledgeEntries = useMemo<readonly KnowledgeEntry[]>(() => {
    const facilityKnowledge = facilityRecords.map((facility) => ({
      title: facility.name,
      body: `${facility.name} is a map facility on the Houses & Facilities page. It is listed in the ${facility.tab} facilities tab${facility.mapUnlock ? ` and unlocks at map level ${facility.mapUnlock}` : ""}. Size: ${facility.size}.`,
      options: [`Where is ${facility.name}?`, `Open ${facility.name} page`],
    }));

    const exchangeKnowledge = exchangeRecords.slice(0, 80).map((row) => ({
      title: `${row.inputName} exchange`,
      body: `${row.inputName} exchanges into ${row.outputName}. Its mined exchange starts at ${row.startPrice} and increases by ${row.priceStep} each time you trade that exact source item. Its equipment buy price is ${row.buyPrice} copper coins.`,
      options: [`What does ${row.inputName} exchange into?`, `How much does ${row.inputName} cost?`],
    }));

    const kairoKnowledge = KAIRO_ROOM_LOOT_GROUPS.map((group) => ({
      title: `Kairo Room loot: ${group.title}`,
      body: `${group.title} appears in Kairo Room. Difficulties: ${group.encounters.map((encounter) => `${encounter.difficulty} Lv ${encounter.level}/${encounter.bossLevel}`).join(", ")}. Example drops include ${group.encounters[0]?.tables.flat().slice(0, 4).map((line) => line.item).join(", ") || "special loot"}.`,
      options: [`Kairo Room ${group.title} loot`, "Open Kairo Room"],
    }));

    const wairoKnowledge: KnowledgeEntry = {
      title: "Wairo Dungeon loot",
      body: `Wairo Raid Dungeon has mined loot tables for Easy, Normal, Hard, and Extreme. Example drops include ${WAIRO_DUNGEON_LOOT_GROUP.encounters[0]?.tables.flat().slice(0, 5).map((line) => line.item).join(", ") || "A/ Kairo Gun and Myriad Arrows"}.`,
      options: ["What drops in Wairo Dungeon?", "Open Wairo Dungeon"],
    };

    const guideKnowledge = Object.entries(PLAYTHROUGH_GUIDE_SECTION_OVERLAYS).map(([section, overlay]) => ({
      title: `Guide section: ${section}`,
      body: `${section} is covered in the playthrough guide. ${overlay.description}`,
      options: [overlay.cta, "Open Playthrough Guide"],
    }));

    const staticKnowledge: KnowledgeEntry[] = [
      {
        title: "Weekly Conquest",
        body: "Weekly Conquest is tracked on the Weekly Conquest page and on the World Map weekly layer. Ask Database does not answer the live current rotation directly yet.",
        options: ["Open Weekly Conquest", "Open World Map"],
      },
      {
        title: "Equipment Exchange",
        body: "Equipment Exchange lets you trade eligible equipment, up to B rank, into A/ Kairo equipment. The trade price starts from a mined base price and rises by a mined step for that exact source item.",
        options: ["Open Equipment Exchange", "Where is Equipment Exchange?"],
      },
      {
        title: "Master Smithy",
        body: "Master Smithy uses Kairo equipment to break level caps on equipment. The site has an Equipment Exchange calculator to estimate the cheapest route to get the needed Kairo pieces.",
        options: ["Open Equipment Exchange", "Where is Master Smithy?"],
      },
    ];

    return [
      ...staticKnowledge,
      ...facilityKnowledge,
      ...exchangeKnowledge,
      ...kairoKnowledge,
      wairoKnowledge,
      ...guideKnowledge,
    ];
  }, [exchangeRecords, facilityRecords]);

  const canSend = query.trim().length > 0;

  const submitQuestion = (raw?: string) => {
    const text = (raw ?? query).trim();
    if (!text) return;

    const userMsg: ChatMessage = { id: makeId(), role: "user", text };

    const answer =
      sheetLoading || sharedLoading
        ? result("Loading site data...")
        : answerQuestion(text, equipmentRecords, catalogRecords, exchangeRecords, facilityRecords, knowledgeEntries, shared, messages);

    const assistantMsg: ChatMessage = {
      id: makeId(),
      role: "assistant",
      text: answer.text,
      options: answer.options,
    };

    setMessages((prev) => [...prev, userMsg, assistantMsg]);
    setQuery("");
    setOpen(true);
  };

  return (
    <>
      {!open && (
        <button
          onClick={() => setOpen(true)}
          className="fixed bottom-5 right-5 z-50 flex h-14 w-14 items-center justify-center rounded-full bg-primary text-primary-foreground shadow-lg transition-transform hover:scale-105"
          aria-label="Open Ask the Database"
        >
          <MessageCircle className="h-6 w-6" />
        </button>
      )}

      {open && (
        <div className="fixed bottom-5 right-5 z-50 w-[min(26rem,calc(100vw-1.5rem))] max-w-[calc(100vw-1.5rem)]">
          <Card className="overflow-hidden border-primary/20 shadow-2xl">
            <CardHeader className="border-b border-border bg-background pb-3">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <CardTitle className="flex items-center gap-2 text-xl">
                    <Sparkles className="h-5 w-5 text-primary" />
                    Ask the Database
                  </CardTitle>
                  <p className="mt-1 text-sm text-muted-foreground">
                    Floating helper, not AI.
                  </p>
                </div>

                <Button
                  variant="ghost"
                  size="icon"
                  className="h-8 w-8 shrink-0"
                  onClick={() => setOpen(false)}
                  aria-label="Minimize Ask the Database"
                >
                  <Minus className="h-4 w-4" />
                </Button>
              </div>
            </CardHeader>

            <CardContent className="bg-background p-0">
              <div className="border-b border-border px-4 py-3">
                <button
                  type="button"
                  onClick={() => setGuideOpen((v) => !v)}
                  className="flex w-full items-center justify-between text-left text-sm text-muted-foreground transition-colors hover:text-foreground"
                >
                  <span>Guide and examples</span>
                  {guideOpen ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
                </button>

                {guideOpen && (
                  <div className="mt-3 space-y-2 text-xs text-muted-foreground">
                    <div>Try simple questions first.</div>
                    <div>Examples: “How do I unlock Double Bed?”, “What job has the highest attack?”, “How can I get a Royal?”, “Max possible attack?”</div>
                  </div>
                )}
              </div>

              <div className="max-h-[28rem] space-y-3 overflow-y-auto px-4 py-4">
                {messages.map((message) => (
                  <div key={message.id} className="space-y-2">
                    <div
                      className={`max-w-[90%] whitespace-pre-line rounded-2xl px-4 py-3 text-sm leading-relaxed ${
                        message.role === "user"
                          ? "ml-auto bg-primary text-primary-foreground"
                          : "bg-muted text-foreground"
                      }`}
                    >
                      {message.text}
                    </div>

                    {message.role === "assistant" && message.options && message.options.length > 0 && (
                      <div className="flex flex-wrap gap-2">
                        {message.options.map((option) => (
                          <button
                            key={`${message.id}-${option}`}
                            onClick={() => submitQuestion(option)}
                            className="rounded-full border border-border px-3 py-1.5 text-left text-xs transition-colors hover:bg-muted"
                          >
                            {option}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
              </div>

              <div className="space-y-3 border-t border-border px-4 py-3">
                <div className="flex flex-wrap gap-2">
                  {SUGGESTIONS.map((suggestion) => (
                    <button
                      key={suggestion}
                      onClick={() => submitQuestion(suggestion)}
                      className="rounded-full border border-border px-3 py-1.5 text-left text-xs transition-colors hover:bg-muted"
                    >
                      {suggestion}
                    </button>
                  ))}
                </div>

                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    submitQuestion();
                  }}
                  className="flex items-center gap-2"
                >
                  <Input
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    placeholder="Ask about equipment, jobs, shops, or marriage..."
                    className="h-12"
                  />
                  <Button type="submit" size="icon" className="h-12 w-12 shrink-0" disabled={!canSend}>
                    <Send className="h-4 w-4" />
                  </Button>
                </form>
              </div>
            </CardContent>
          </Card>
        </div>
      )}
    </>
  );
}
