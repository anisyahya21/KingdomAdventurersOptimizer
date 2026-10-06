import { useEffect, useMemo, useRef, useState } from "react";
import { CalendarDays, Package, Sparkles, Swords, Wand2 } from "lucide-react";
import { Link } from "wouter";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { ThemedNumberInput } from "@/components/ui/themed-number-input";
import { PageHeader } from "@/components/ka/page-header";
import { KA_CATEGORY_BADGE_CLASS } from "@/design-system/category-styles";
import { CategoryBadge } from "@/components/ka/category-badge";
import { getEntityHref } from "@/components/ka/entity-link";
import { FACILITY_GACHA_EVENTS } from "@/game-data/facility-gacha-events";
import { EQUIPMENT_CATALOG } from "@/lib/generated-equipment-data";
import { localSharedData } from "@/lib/local-shared-data";
import { getEquipmentIcon } from "@/lib/equipment-icons";
import { eventClockDateToLocalDate, getOffsetAdjustedNow, useEventHourOffset } from "@/lib/event-time";
import { useEquipmentIcons } from "@/hooks/use-equipment-icons";

export type EventKind = "jobs" | "facilities" | "weapons" | "items";

export type GachaEvent = {
  id: string;
  kind: EventKind;
  title: string;
  poolLabel: string;
  startMonth: number;
  startDay: number;
  endMonth: number;
  endDay: number;
  notes?: string;
};

function namedWeaponPoolTitle(names: string[]): string {
  if (names.length <= 2) return names.join(" + ");
  return `${names[0]} +${names.length - 1} more`;
}

const STAFF_POOL = ["S/ Mystic Staff"];
const CLUB_POOL = ["S/ Golden Club", "S/ Divine Club"];
const SPEAR_POOL = ["S/ Fire Spear", "S/ Black Spear"];
const HAMMER_POOL = ["S/ Fire Hammer", "S/ Golden Hammer"];
const SWORD_POOL = ["S/ Fire Sword", "S/ Blizzard Sword", "S/ Legendary Sword", "S/ Conqueror's Sword", "S/ Yggdrasil Sword"];
const GUN_POOL = ["S/ Golden Gun"];
const BOW_POOL = ["S/ Champion's Bow"];
const AXE_POOL = ["S/ Legendary Axe"];

type ResolvedEvent = GachaEvent & {
  startAt: Date;
  endAt: Date;
  isActive: boolean;
  isUpcoming: boolean;
  durationDays: number;
};

const MAX_EVENT_COUNTDOWN_MS = 330 * 86400000;

type EquipmentCatalogItem = {
  name: string;
  type: number;
  rankLabel?: string;
  buyPrice?: number;
  requiredKairo?: string;
};

type StatOverride = Record<string, { base?: number; inc?: number }>;

type WeaponPreviewItem = {
  name: string;
  slot: string;
  rankLabel: string;
  buyPrice?: number;
  requiredKairo?: string;
  weaponType?: string;
  shopName: string;
  equipIcon?: string;
  statIcons: Record<string, string>;
  stats: Array<{
    name: string;
    base: number;
    inc: number;
  }>;
};

const GUIDE_PREVIEW_DIALOG_CLASS =
  "h-[calc(100dvh-1rem)] w-[calc(100vw-1rem)] max-w-none overflow-y-auto p-4 pt-12 sm:h-auto sm:max-h-[90vh] sm:w-full sm:max-w-[95vw] sm:p-6 xl:max-w-6xl";

const GUIDE_EQUIPMENT_STATS = [
  "HP",
  "MP",
  "Vigor",
  "Attack",
  "Defence",
  "Speed",
  "Luck",
  "Intelligence",
  "Dexterity",
  "Gather",
  "Move",
  "Heart",
];

const GUIDE_STAT_SHORT: Record<string, string> = {
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

const JOB_EVENTS: GachaEvent[] = [
  { id: "job-01", kind: "jobs", title: "S Rank Samurai", poolLabel: "S Rank Job Event", startMonth: 1, startDay: 2, endMonth: 1, endDay: 5 },
  { id: "job-02", kind: "jobs", title: "S Rank Viking", poolLabel: "S Rank Job Event", startMonth: 1, startDay: 8, endMonth: 1, endDay: 10 },
  { id: "job-03", kind: "jobs", title: "S Rank Pirate", poolLabel: "S Rank Job Event", startMonth: 1, startDay: 15, endMonth: 1, endDay: 17 },
  { id: "job-04", kind: "jobs", title: "S Rank Champion", poolLabel: "S Rank Job Event", startMonth: 1, startDay: 22, endMonth: 1, endDay: 24 },
  { id: "job-05", kind: "jobs", title: "S Rank Wizard", poolLabel: "S Rank Job Event", startMonth: 1, startDay: 28, endMonth: 1, endDay: 30 },
  { id: "job-06", kind: "jobs", title: "S Rank Paladin", poolLabel: "S Rank Job Event", startMonth: 2, startDay: 5, endMonth: 2, endDay: 8 },
  { id: "job-07", kind: "jobs", title: "S Rank Gunner", poolLabel: "S Rank Job Event", startMonth: 2, startDay: 9, endMonth: 2, endDay: 12 },
  { id: "job-08", kind: "jobs", title: "S Rank Archer", poolLabel: "S Rank Job Event", startMonth: 2, startDay: 15, endMonth: 2, endDay: 18 },
  { id: "job-09", kind: "jobs", title: "S Rank Ninja", poolLabel: "S Rank Job Event", startMonth: 3, startDay: 1, endMonth: 3, endDay: 3 },
  { id: "job-10", kind: "jobs", title: "S Rank Samurai", poolLabel: "S Rank Job Event", startMonth: 3, startDay: 8, endMonth: 3, endDay: 10 },
  { id: "job-11", kind: "jobs", title: "S Rank Viking", poolLabel: "S Rank Job Event", startMonth: 3, startDay: 15, endMonth: 3, endDay: 17 },
  { id: "job-12", kind: "jobs", title: "S Rank Pirate", poolLabel: "S Rank Job Event", startMonth: 3, startDay: 22, endMonth: 3, endDay: 24 },
  { id: "job-13", kind: "jobs", title: "S Rank Champion", poolLabel: "S Rank Job Event", startMonth: 3, startDay: 28, endMonth: 3, endDay: 30 },
  { id: "job-14", kind: "jobs", title: "S Rank Wizard", poolLabel: "S Rank Job Event", startMonth: 4, startDay: 1, endMonth: 4, endDay: 3 },
  { id: "job-15", kind: "jobs", title: "S Rank Monk", poolLabel: "S Rank Job Event", startMonth: 4, startDay: 8, endMonth: 4, endDay: 10 },
  { id: "job-16", kind: "jobs", title: "S Rank Samurai", poolLabel: "S Rank Job Event", startMonth: 4, startDay: 15, endMonth: 4, endDay: 17 },
  { id: "job-17", kind: "jobs", title: "S Rank Guard", poolLabel: "S Rank Job Event", startMonth: 4, startDay: 22, endMonth: 4, endDay: 24 },
  { id: "job-18", kind: "jobs", title: "S Rank Knight", poolLabel: "S Rank Job Event", startMonth: 5, startDay: 1, endMonth: 5, endDay: 3 },
  { id: "job-19", kind: "jobs", title: "S Rank Mage", poolLabel: "S Rank Job Event", startMonth: 5, startDay: 8, endMonth: 5, endDay: 10 },
  { id: "job-20", kind: "jobs", title: "S Rank Paladin", poolLabel: "S Rank Job Event", startMonth: 5, startDay: 15, endMonth: 5, endDay: 17 },
  { id: "job-21", kind: "jobs", title: "S Rank Gunner", poolLabel: "S Rank Job Event", startMonth: 5, startDay: 22, endMonth: 5, endDay: 24 },
  { id: "job-22", kind: "jobs", title: "S Rank Archer", poolLabel: "S Rank Job Event", startMonth: 5, startDay: 28, endMonth: 5, endDay: 30 },
  { id: "job-23", kind: "jobs", title: "S Rank Ninja", poolLabel: "S Rank Job Event", startMonth: 6, startDay: 1, endMonth: 6, endDay: 3 },
  { id: "job-24", kind: "jobs", title: "S Rank Samurai", poolLabel: "S Rank Job Event", startMonth: 6, startDay: 8, endMonth: 6, endDay: 10 },
  { id: "job-25", kind: "jobs", title: "S Rank Viking", poolLabel: "S Rank Job Event", startMonth: 6, startDay: 15, endMonth: 6, endDay: 17 },
  { id: "job-26", kind: "jobs", title: "S Rank Pirate", poolLabel: "S Rank Job Event", startMonth: 6, startDay: 22, endMonth: 6, endDay: 24 },
  { id: "job-27", kind: "jobs", title: "S Rank Champion", poolLabel: "S Rank Job Event", startMonth: 7, startDay: 1, endMonth: 7, endDay: 3 },
  { id: "job-28", kind: "jobs", title: "S Rank Wizard", poolLabel: "S Rank Job Event", startMonth: 7, startDay: 8, endMonth: 7, endDay: 10 },
  { id: "job-29", kind: "jobs", title: "S Rank Guard", poolLabel: "S Rank Job Event", startMonth: 7, startDay: 15, endMonth: 7, endDay: 17 },
  { id: "job-30", kind: "jobs", title: "S Rank Knight", poolLabel: "S Rank Job Event", startMonth: 7, startDay: 22, endMonth: 7, endDay: 24 },
  { id: "job-31", kind: "jobs", title: "S Rank Mage", poolLabel: "S Rank Job Event", startMonth: 7, startDay: 28, endMonth: 7, endDay: 30 },
  { id: "job-32", kind: "jobs", title: "S Rank Paladin", poolLabel: "S Rank Job Event", startMonth: 8, startDay: 1, endMonth: 8, endDay: 3 },
  { id: "job-33", kind: "jobs", title: "S Rank Gunner", poolLabel: "S Rank Job Event", startMonth: 8, startDay: 8, endMonth: 8, endDay: 10 },
  { id: "job-34", kind: "jobs", title: "S Rank Archer", poolLabel: "S Rank Job Event", startMonth: 8, startDay: 15, endMonth: 8, endDay: 17 },
  { id: "job-35", kind: "jobs", title: "S Rank Ninja", poolLabel: "S Rank Job Event", startMonth: 8, startDay: 22, endMonth: 8, endDay: 24 },
  { id: "job-36", kind: "jobs", title: "S Rank Samurai", poolLabel: "S Rank Job Event", startMonth: 8, startDay: 28, endMonth: 8, endDay: 30 },
  { id: "job-37", kind: "jobs", title: "S Rank Viking", poolLabel: "S Rank Job Event", startMonth: 9, startDay: 1, endMonth: 9, endDay: 3 },
  { id: "job-38", kind: "jobs", title: "S Rank Pirate", poolLabel: "S Rank Job Event", startMonth: 9, startDay: 8, endMonth: 9, endDay: 10 },
  { id: "job-39", kind: "jobs", title: "S Rank Champion", poolLabel: "S Rank Job Event", startMonth: 9, startDay: 15, endMonth: 9, endDay: 17 },
  { id: "job-40", kind: "jobs", title: "S Rank Wizard", poolLabel: "S Rank Job Event", startMonth: 9, startDay: 22, endMonth: 9, endDay: 24 },
  { id: "job-41", kind: "jobs", title: "S Rank Guard", poolLabel: "S Rank Job Event", startMonth: 10, startDay: 1, endMonth: 10, endDay: 3 },
  { id: "job-42", kind: "jobs", title: "S Rank Knight", poolLabel: "S Rank Job Event", startMonth: 10, startDay: 8, endMonth: 10, endDay: 10 },
  { id: "job-43", kind: "jobs", title: "S Rank Mage", poolLabel: "S Rank Job Event", startMonth: 10, startDay: 13, endMonth: 10, endDay: 16 },
  { id: "job-44", kind: "jobs", title: "S Rank Paladin", poolLabel: "S Rank Job Event", startMonth: 10, startDay: 20, endMonth: 10, endDay: 23 },
  { id: "job-45", kind: "jobs", title: "S Rank Gunner", poolLabel: "S Rank Job Event", startMonth: 10, startDay: 26, endMonth: 10, endDay: 30 },
  { id: "job-46", kind: "jobs", title: "S Rank Archer", poolLabel: "S Rank Job Event", startMonth: 11, startDay: 1, endMonth: 11, endDay: 4 },
  { id: "job-47", kind: "jobs", title: "S Rank Ninja", poolLabel: "S Rank Job Event", startMonth: 11, startDay: 8, endMonth: 11, endDay: 11 },
  { id: "job-48", kind: "jobs", title: "S Rank Samurai", poolLabel: "S Rank Job Event", startMonth: 11, startDay: 16, endMonth: 11, endDay: 18 },
  { id: "job-49", kind: "jobs", title: "S Rank Viking", poolLabel: "S Rank Job Event", startMonth: 11, startDay: 23, endMonth: 11, endDay: 25 },
  { id: "job-50", kind: "jobs", title: "S Rank Pirate", poolLabel: "S Rank Job Event", startMonth: 12, startDay: 2, endMonth: 12, endDay: 4 },
  { id: "job-51", kind: "jobs", title: "S Rank Champion", poolLabel: "S Rank Job Event", startMonth: 12, startDay: 7, endMonth: 12, endDay: 9 },
  { id: "job-52", kind: "jobs", title: "S Rank Wizard", poolLabel: "S Rank Job Event", startMonth: 12, startDay: 14, endMonth: 12, endDay: 16 },
  { id: "job-53", kind: "jobs", title: "S Rank Guard", poolLabel: "S Rank Job Event", startMonth: 12, startDay: 21, endMonth: 12, endDay: 24 },
  { id: "job-54", kind: "jobs", title: "S Rank Santa Claus", poolLabel: "Holiday S Rank Job Event", startMonth: 12, startDay: 25, endMonth: 12, endDay: 25, notes: "One-day holiday S-rank job event with a stronger featured focus than the standard rotation." },
  { id: "job-55", kind: "jobs", title: "S Rank Knight", poolLabel: "S Rank Job Event", startMonth: 12, startDay: 28, endMonth: 12, endDay: 31 },
];

const FACILITY_PATTERN: GachaEvent[] = FACILITY_GACHA_EVENTS.map((event) => ({
  ...event,
  kind: "facilities",
}));

const ITEM_EVENTS: GachaEvent[] = [
  {
    id: "item-01",
    kind: "items",
    title: "Item Gacha",
    poolLabel: "Default item pool",
    startMonth: 1,
    startDay: 1,
    endMonth: 12,
    endDay: 31,
    notes: "Default item pool. A separate yearly item-event rotation is not shown here yet.",
  },
];

const WEAPON_EVENTS: GachaEvent[] = [
  { id: "kairo-01", kind: "weapons", title: "Kairo Series", poolLabel: "Recurring weapon event", startMonth: 1, startDay: 5, endMonth: 1, endDay: 10 },
  { id: "kairo-02", kind: "weapons", title: "Kairo Series", poolLabel: "Recurring weapon event", startMonth: 1, startDay: 20, endMonth: 1, endDay: 25 },
  { id: "kairo-03", kind: "weapons", title: "Kairo Series", poolLabel: "Recurring weapon event", startMonth: 2, startDay: 5, endMonth: 2, endDay: 10 },
  { id: "kairo-04", kind: "weapons", title: "Kairo Series", poolLabel: "Recurring weapon event", startMonth: 2, startDay: 20, endMonth: 2, endDay: 25 },
  { id: "kairo-05", kind: "weapons", title: "Kairo Series", poolLabel: "Recurring weapon event", startMonth: 3, startDay: 5, endMonth: 3, endDay: 10 },
  { id: "kairo-06", kind: "weapons", title: "Kairo Series", poolLabel: "Recurring weapon event", startMonth: 3, startDay: 20, endMonth: 3, endDay: 25 },
  { id: "kairo-07", kind: "weapons", title: "Kairo Series", poolLabel: "Recurring weapon event", startMonth: 4, startDay: 5, endMonth: 4, endDay: 10 },
  { id: "kairo-08", kind: "weapons", title: "Kairo Series", poolLabel: "Recurring weapon event", startMonth: 4, startDay: 20, endMonth: 4, endDay: 25 },
  { id: "kairo-09", kind: "weapons", title: "Kairo Series", poolLabel: "Recurring weapon event", startMonth: 5, startDay: 5, endMonth: 5, endDay: 10 },
  { id: "kairo-10", kind: "weapons", title: "Kairo Series", poolLabel: "Recurring weapon event", startMonth: 5, startDay: 20, endMonth: 5, endDay: 25 },
  { id: "kairo-11", kind: "weapons", title: "Kairo Series", poolLabel: "Recurring weapon event", startMonth: 6, startDay: 5, endMonth: 6, endDay: 10 },
  { id: "kairo-12", kind: "weapons", title: "Kairo Series", poolLabel: "Recurring weapon event", startMonth: 6, startDay: 20, endMonth: 6, endDay: 25 },
  { id: "kairo-13", kind: "weapons", title: "Kairo Series", poolLabel: "Recurring weapon event", startMonth: 7, startDay: 5, endMonth: 7, endDay: 10 },
  { id: "kairo-14", kind: "weapons", title: "Kairo Series", poolLabel: "Recurring weapon event", startMonth: 7, startDay: 20, endMonth: 7, endDay: 25 },
  { id: "kairo-15", kind: "weapons", title: "Kairo Series", poolLabel: "Recurring weapon event", startMonth: 8, startDay: 5, endMonth: 8, endDay: 10 },
  { id: "kairo-16", kind: "weapons", title: "Kairo Series", poolLabel: "Recurring weapon event", startMonth: 8, startDay: 20, endMonth: 8, endDay: 25 },
  { id: "kairo-17", kind: "weapons", title: "Kairo Series", poolLabel: "Recurring weapon event", startMonth: 9, startDay: 5, endMonth: 9, endDay: 10 },
  { id: "kairo-18", kind: "weapons", title: "Kairo Series", poolLabel: "Recurring weapon event", startMonth: 9, startDay: 20, endMonth: 9, endDay: 25 },
  { id: "kairo-19", kind: "weapons", title: "Kairo Series", poolLabel: "Recurring weapon event", startMonth: 10, startDay: 5, endMonth: 10, endDay: 10 },
  { id: "kairo-20", kind: "weapons", title: "Kairo Series", poolLabel: "Recurring weapon event", startMonth: 10, startDay: 20, endMonth: 10, endDay: 25 },
  { id: "kairo-21", kind: "weapons", title: "Kairo Series", poolLabel: "Recurring weapon event", startMonth: 11, startDay: 5, endMonth: 11, endDay: 10 },
  { id: "kairo-22", kind: "weapons", title: "Kairo Series", poolLabel: "Recurring weapon event", startMonth: 11, startDay: 20, endMonth: 11, endDay: 25 },
  { id: "kairo-23", kind: "weapons", title: "Kairo Series", poolLabel: "Recurring weapon event", startMonth: 12, startDay: 5, endMonth: 12, endDay: 10 },
  { id: "kairo-24", kind: "weapons", title: "Kairo Series", poolLabel: "Recurring weapon event", startMonth: 12, startDay: 20, endMonth: 12, endDay: 25 },
  { id: "sweapon-01", kind: "weapons", title: namedWeaponPoolTitle(STAFF_POOL), poolLabel: "One-day S weapon pool", startMonth: 1, startDay: 1, endMonth: 1, endDay: 1, notes: STAFF_POOL.join(" | ") },
  { id: "sweapon-02", kind: "weapons", title: namedWeaponPoolTitle(CLUB_POOL), poolLabel: "One-day S weapon pool", startMonth: 1, startDay: 14, endMonth: 1, endDay: 14, notes: CLUB_POOL.join(" | ") },
  { id: "sweapon-03", kind: "weapons", title: namedWeaponPoolTitle(SPEAR_POOL), poolLabel: "One-day S weapon pool", startMonth: 1, startDay: 28, endMonth: 1, endDay: 28, notes: SPEAR_POOL.join(" | ") },
  { id: "sweapon-04", kind: "weapons", title: namedWeaponPoolTitle(HAMMER_POOL), poolLabel: "One-day S weapon pool", startMonth: 2, startDay: 1, endMonth: 2, endDay: 1, notes: HAMMER_POOL.join(" | ") },
  { id: "sweapon-05", kind: "weapons", title: namedWeaponPoolTitle(SWORD_POOL), poolLabel: "One-day S weapon pool", startMonth: 2, startDay: 14, endMonth: 2, endDay: 14, notes: SWORD_POOL.join(" | ") },
  { id: "sweapon-06", kind: "weapons", title: namedWeaponPoolTitle(GUN_POOL), poolLabel: "One-day S weapon pool", startMonth: 2, startDay: 28, endMonth: 2, endDay: 28, notes: GUN_POOL.join(" | ") },
  { id: "sweapon-07", kind: "weapons", title: namedWeaponPoolTitle(BOW_POOL), poolLabel: "One-day S weapon pool", startMonth: 3, startDay: 1, endMonth: 3, endDay: 1, notes: BOW_POOL.join(" | ") },
  { id: "sweapon-08", kind: "weapons", title: namedWeaponPoolTitle(AXE_POOL), poolLabel: "One-day S weapon pool", startMonth: 3, startDay: 14, endMonth: 3, endDay: 14, notes: AXE_POOL.join(" | ") },
  { id: "sweapon-09", kind: "weapons", title: namedWeaponPoolTitle(STAFF_POOL), poolLabel: "One-day S weapon pool", startMonth: 3, startDay: 28, endMonth: 3, endDay: 28, notes: STAFF_POOL.join(" | ") },
  { id: "sweapon-10", kind: "weapons", title: namedWeaponPoolTitle(CLUB_POOL), poolLabel: "One-day S weapon pool", startMonth: 4, startDay: 1, endMonth: 4, endDay: 1, notes: CLUB_POOL.join(" | ") },
  { id: "sweapon-11", kind: "weapons", title: namedWeaponPoolTitle(SPEAR_POOL), poolLabel: "One-day S weapon pool", startMonth: 4, startDay: 14, endMonth: 4, endDay: 14, notes: SPEAR_POOL.join(" | ") },
  { id: "sweapon-12", kind: "weapons", title: namedWeaponPoolTitle(HAMMER_POOL), poolLabel: "One-day S weapon pool", startMonth: 4, startDay: 28, endMonth: 4, endDay: 28, notes: HAMMER_POOL.join(" | ") },
  { id: "sweapon-13", kind: "weapons", title: namedWeaponPoolTitle(SWORD_POOL), poolLabel: "One-day S weapon pool", startMonth: 5, startDay: 1, endMonth: 5, endDay: 1, notes: SWORD_POOL.join(" | ") },
  { id: "sweapon-14", kind: "weapons", title: namedWeaponPoolTitle(GUN_POOL), poolLabel: "One-day S weapon pool", startMonth: 5, startDay: 14, endMonth: 5, endDay: 14, notes: GUN_POOL.join(" | ") },
  { id: "sweapon-15", kind: "weapons", title: namedWeaponPoolTitle(BOW_POOL), poolLabel: "One-day S weapon pool", startMonth: 5, startDay: 28, endMonth: 5, endDay: 28, notes: BOW_POOL.join(" | ") },
  { id: "sweapon-16", kind: "weapons", title: namedWeaponPoolTitle(AXE_POOL), poolLabel: "One-day S weapon pool", startMonth: 6, startDay: 1, endMonth: 6, endDay: 1, notes: AXE_POOL.join(" | ") },
  { id: "sweapon-17", kind: "weapons", title: namedWeaponPoolTitle(STAFF_POOL), poolLabel: "One-day S weapon pool", startMonth: 6, startDay: 14, endMonth: 6, endDay: 14, notes: STAFF_POOL.join(" | ") },
  { id: "sweapon-18", kind: "weapons", title: namedWeaponPoolTitle(CLUB_POOL), poolLabel: "One-day S weapon pool", startMonth: 6, startDay: 28, endMonth: 6, endDay: 28, notes: CLUB_POOL.join(" | ") },
  { id: "sweapon-19", kind: "weapons", title: namedWeaponPoolTitle(SPEAR_POOL), poolLabel: "One-day S weapon pool", startMonth: 7, startDay: 1, endMonth: 7, endDay: 1, notes: SPEAR_POOL.join(" | ") },
  { id: "sweapon-20", kind: "weapons", title: namedWeaponPoolTitle(HAMMER_POOL), poolLabel: "One-day S weapon pool", startMonth: 7, startDay: 14, endMonth: 7, endDay: 14, notes: HAMMER_POOL.join(" | ") },
  { id: "sweapon-21", kind: "weapons", title: namedWeaponPoolTitle(SWORD_POOL), poolLabel: "One-day S weapon pool", startMonth: 7, startDay: 28, endMonth: 7, endDay: 28, notes: SWORD_POOL.join(" | ") },
  { id: "sweapon-22", kind: "weapons", title: namedWeaponPoolTitle(GUN_POOL), poolLabel: "One-day S weapon pool", startMonth: 8, startDay: 1, endMonth: 8, endDay: 1, notes: GUN_POOL.join(" | ") },
  { id: "sweapon-23", kind: "weapons", title: namedWeaponPoolTitle(BOW_POOL), poolLabel: "One-day S weapon pool", startMonth: 8, startDay: 14, endMonth: 8, endDay: 14, notes: BOW_POOL.join(" | ") },
  { id: "sweapon-24", kind: "weapons", title: namedWeaponPoolTitle(AXE_POOL), poolLabel: "One-day S weapon pool", startMonth: 8, startDay: 28, endMonth: 8, endDay: 28, notes: AXE_POOL.join(" | ") },
  { id: "sweapon-25", kind: "weapons", title: namedWeaponPoolTitle(STAFF_POOL), poolLabel: "One-day S weapon pool", startMonth: 9, startDay: 1, endMonth: 9, endDay: 1, notes: STAFF_POOL.join(" | ") },
  { id: "sweapon-26", kind: "weapons", title: namedWeaponPoolTitle(CLUB_POOL), poolLabel: "One-day S weapon pool", startMonth: 9, startDay: 14, endMonth: 9, endDay: 14, notes: CLUB_POOL.join(" | ") },
  { id: "sweapon-27", kind: "weapons", title: namedWeaponPoolTitle(SPEAR_POOL), poolLabel: "One-day S weapon pool", startMonth: 9, startDay: 28, endMonth: 9, endDay: 28, notes: SPEAR_POOL.join(" | ") },
  { id: "sweapon-28", kind: "weapons", title: namedWeaponPoolTitle(HAMMER_POOL), poolLabel: "One-day S weapon pool", startMonth: 10, startDay: 1, endMonth: 10, endDay: 1, notes: HAMMER_POOL.join(" | ") },
  { id: "sweapon-29", kind: "weapons", title: namedWeaponPoolTitle(SWORD_POOL), poolLabel: "One-day S weapon pool", startMonth: 10, startDay: 14, endMonth: 10, endDay: 14, notes: SWORD_POOL.join(" | ") },
  { id: "sweapon-30", kind: "weapons", title: namedWeaponPoolTitle(GUN_POOL), poolLabel: "One-day S weapon pool", startMonth: 10, startDay: 28, endMonth: 10, endDay: 28, notes: GUN_POOL.join(" | ") },
  { id: "sweapon-31", kind: "weapons", title: namedWeaponPoolTitle(BOW_POOL), poolLabel: "One-day S weapon pool", startMonth: 11, startDay: 1, endMonth: 11, endDay: 1, notes: BOW_POOL.join(" | ") },
  { id: "sweapon-32", kind: "weapons", title: namedWeaponPoolTitle(AXE_POOL), poolLabel: "One-day S weapon pool", startMonth: 11, startDay: 14, endMonth: 11, endDay: 14, notes: AXE_POOL.join(" | ") },
  { id: "sweapon-33", kind: "weapons", title: namedWeaponPoolTitle(STAFF_POOL), poolLabel: "One-day S weapon pool", startMonth: 11, startDay: 28, endMonth: 11, endDay: 28, notes: STAFF_POOL.join(" | ") },
  { id: "sweapon-34", kind: "weapons", title: namedWeaponPoolTitle(CLUB_POOL), poolLabel: "One-day S weapon pool", startMonth: 12, startDay: 1, endMonth: 12, endDay: 1, notes: CLUB_POOL.join(" | ") },
  { id: "sweapon-35", kind: "weapons", title: namedWeaponPoolTitle(HAMMER_POOL), poolLabel: "One-day S weapon pool", startMonth: 12, startDay: 14, endMonth: 12, endDay: 14, notes: HAMMER_POOL.join(" | ") },
  { id: "sweapon-36", kind: "weapons", title: namedWeaponPoolTitle(SWORD_POOL), poolLabel: "One-day S weapon pool", startMonth: 12, startDay: 28, endMonth: 12, endDay: 28, notes: SWORD_POOL.join(" | ") },
];

export const ALL_GACHA_EVENTS: GachaEvent[] = [...JOB_EVENTS, ...FACILITY_PATTERN, ...WEAPON_EVENTS, ...ITEM_EVENTS];

export function buildGachaEventWindow(event: GachaEvent, year: number, offset: number) {
  return {
    startAt: eventClockDateToLocalDate(new Date(year, event.startMonth - 1, event.startDay, 0, 0, 0, 0), offset),
    endAt: eventClockDateToLocalDate(new Date(year, event.endMonth - 1, event.endDay, 23, 59, 59, 999), offset),
  };
}

export function resolveGachaEvent(event: GachaEvent, now: Date, offset: number): ResolvedEvent {
  const year = getOffsetAdjustedNow(now, offset).getFullYear();
  const windows = [
    buildGachaEventWindow(event, year - 1, offset),
    buildGachaEventWindow(event, year, offset),
    buildGachaEventWindow(event, year + 1, offset),
  ];
  const activeWindow = windows.find((window) => window.startAt.getTime() <= now.getTime() && window.endAt.getTime() >= now.getTime());
  const upcomingWindow = windows
    .filter((window) => {
      const diff = window.startAt.getTime() - now.getTime();
      return diff > 0 && diff <= MAX_EVENT_COUNTDOWN_MS;
    })
    .sort((a, b) => a.startAt.getTime() - b.startAt.getTime())[0];
  const chosen = activeWindow ?? upcomingWindow ?? windows[1];
  const startDiff = chosen.startAt.getTime() - now.getTime();
  return {
    ...event,
    startAt: chosen.startAt,
    endAt: chosen.endAt,
    durationDays: Math.floor((chosen.endAt.getTime() - chosen.startAt.getTime()) / 86400000) + 1,
    isActive: chosen.startAt.getTime() <= now.getTime() && chosen.endAt.getTime() >= now.getTime(),
    isUpcoming: startDiff > 0 && startDiff <= MAX_EVENT_COUNTDOWN_MS,
  };
}

function formatDate(date: Date): string {
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", weekday: "short" }).format(date);
}

function formatCountdown(ms: number): string {
  if (ms <= 0) return "0m";
  const totalSeconds = Math.floor(ms / 1000);
  const days = Math.floor(totalSeconds / 86400);
  const hours = Math.floor((totalSeconds % 86400) / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

function normalizeWeaponSearchName(name: string): string {
  return name.replace(/^S\/\s*/i, "").trim();
}

function normalizeEquipmentName(value: string) {
  return value
    .toLowerCase()
    .replace(/^[fsabcde]\s*\/\s*/i, "")
    .replace(/\s+/g, " ")
    .trim();
}

function equipmentSlotFromType(rawType: number) {
  if (rawType === 11) return "Shield";
  if (rawType === 12) return "Armor";
  if (rawType === 13) return "Head";
  if (rawType === 14) return "Accessory";
  if (Number.isFinite(rawType) && rawType > 0) return "Weapon";
  return "";
}

function equipmentShopFromType(rawType: number) {
  if (rawType === 14) return "Accessory shop";
  if (rawType === 11 || rawType === 12 || rawType === 13) return "Armor shop";
  if (Number.isFinite(rawType) && rawType > 0) return "Weapon shop";
  return "Equipment shop";
}

function findEquipmentCatalogItem(search: string) {
  if (!search) return null;
  const catalog = EQUIPMENT_CATALOG as readonly EquipmentCatalogItem[];
  const exactSearch = search.toLowerCase().trim();
  const normalizedSearch = normalizeEquipmentName(search);
  return catalog.find((candidate) => candidate.name.toLowerCase() === exactSearch)
    ?? catalog.find((candidate) => normalizeEquipmentName(candidate.name) === normalizedSearch)
    ?? catalog.find((candidate) => normalizeEquipmentName(candidate.name).includes(normalizedSearch))
    ?? null;
}

function statAtLevel(base: number, inc: number, level: number) {
  return Math.round(base + (level - 1) * inc);
}

function buildWeaponPreviewItem(name: string, equipIcons?: Record<string, string>): WeaponPreviewItem | null {
  const item = findEquipmentCatalogItem(name);
  if (!item) return null;
  const shared = localSharedData as {
    overrides?: Record<string, StatOverride>;
    slotAssignments?: Record<string, string>;
    weaponTypes?: Record<string, string>;
    equipIcons?: Record<string, string>;
    statIcons?: Record<string, string>;
  };
  const overrides = shared.overrides?.[item.name] ?? {};
  const stats = GUIDE_EQUIPMENT_STATS
    .map((stat) => ({
      name: stat,
      base: overrides[stat]?.base ?? 0,
      inc: overrides[stat]?.inc ?? 0,
    }))
    .filter((stat) => stat.base !== 0 || stat.inc !== 0);

  return {
    name: item.name,
    slot: shared.slotAssignments?.[item.name] || equipmentSlotFromType(item.type),
    rankLabel: item.rankLabel ?? item.name.match(/^([FSABCDE])\s*\//i)?.[1]?.toUpperCase() ?? "",
    buyPrice: item.buyPrice,
    requiredKairo: item.requiredKairo,
    weaponType: shared.weaponTypes?.[item.name],
    shopName: equipmentShopFromType(item.type),
    equipIcon: getEquipmentIcon(equipIcons ?? shared.equipIcons, item.name),
    statIcons: shared.statIcons ?? {},
    stats,
  };
}

function weaponHrefFromEvent(event: ResolvedEvent): string {
  if (event.notes) {
    const firstWeapon = event.notes.split("|").map((part) => part.trim()).find(Boolean);
    if (firstWeapon) {
      return getEntityHref("equipment", normalizeWeaponSearchName(firstWeapon)) ?? "/equipment-stats";
    }
  }
  return getEntityHref("equipment", normalizeWeaponSearchName(event.title)) ?? "/equipment-stats";
}

function weaponNamesFromEvent(event: ResolvedEvent): string[] {
  if (!event.notes) return [];
  return event.notes.split("|").map((part) => part.trim()).filter(Boolean);
}

function eventKindClass(kind: EventKind): string {
  if (kind === "jobs") return KA_CATEGORY_BADGE_CLASS.job;
  if (kind === "facilities") return KA_CATEGORY_BADGE_CLASS.building;
  if (kind === "items") return KA_CATEGORY_BADGE_CLASS.shop;
  return KA_CATEGORY_BADGE_CLASS.equipment;
}

function eventKindLabel(kind: EventKind): string {
  if (kind === "jobs") return "Gacha Job";
  if (kind === "facilities") return "S Facility Event";
  if (kind === "items") return "Gacha Items";
  return "Gacha Weapons";
}

function eventKindIcon(kind: EventKind) {
  if (kind === "jobs") return <Sparkles className="w-4 h-4 text-rose-500" />;
  if (kind === "facilities") return <Wand2 className="w-4 h-4 text-lime-500" />;
  if (kind === "items") return <Package className="w-4 h-4 text-sky-500" />;
  return <Swords className="w-4 h-4 text-amber-500" />;
}

function WeaponPreviewDialog({
  item,
  open,
  onOpenChange,
}: {
  item: WeaponPreviewItem | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [level, setLevel] = useState(99);
  const titleRef = useRef<HTMLHeadingElement | null>(null);

  useEffect(() => {
    if (open) setLevel(99);
  }, [open, item?.name]);

  if (!item) return null;

  const setClampedLevel = (value: number) => {
    setLevel(Math.min(99, Math.max(1, Number.isFinite(value) ? Math.round(value) : 1)));
  };
  const statMap = new Map(item.stats.map((stat) => [stat.name, stat]));
  const visibleStats = GUIDE_EQUIPMENT_STATS.flatMap((statName) => {
    const stat = statMap.get(statName);
    if (!stat) return [];
    return [{
      name: statName,
      shortLabel: GUIDE_STAT_SHORT[statName] ?? statName,
      icon: item.statIcons[statName],
      base: stat.base,
      inc: stat.inc,
      value: statAtLevel(stat.base, stat.inc, level),
    }];
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className={GUIDE_PREVIEW_DIALOG_CLASS}
        onOpenAutoFocus={(event) => {
          event.preventDefault();
          titleRef.current?.focus({ preventScroll: true });
        }}
      >
        <DialogHeader>
          <DialogTitle ref={titleRef} tabIndex={-1}>{item.name}</DialogTitle>
          <DialogDescription>
            Weapon stats shown at the selected level.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3 sm:hidden">
          <div className="rounded-lg border border-border bg-card p-3">
            <div className="flex items-start gap-3">
              {item.equipIcon ? (
                <img src={item.equipIcon} alt={item.name} className="h-12 w-12 rounded-md object-contain" />
              ) : (
                <span className="h-12 w-12 rounded-md border border-dashed border-border bg-muted/30" />
              )}
              <div className="min-w-0 flex-1 space-y-2">
                <div className="text-sm font-semibold leading-tight">{item.name}</div>
                <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                  <span className="inline-flex rounded-full border border-border bg-muted/40 px-2 py-1 font-medium text-foreground">
                    {item.weaponType || item.slot || "Weapon"}
                  </span>
                  {item.rankLabel ? <Badge variant="outline">Rank {item.rankLabel}</Badge> : null}
                  {item.buyPrice ? <Badge variant="outline">{item.buyPrice} copper</Badge> : null}
                </div>
              </div>
            </div>

            <div className="mt-3 grid grid-cols-2 gap-2 text-xs">
              <div className="rounded-md border border-border bg-background/70 p-2">
                <div className="text-[10px] uppercase tracking-wide text-muted-foreground">Level</div>
                <div className="mt-2">
                  <ThemedNumberInput
                    value={level}
                    min={1}
                    max={99}
                    onValueChange={setClampedLevel}
                    className="h-9 w-full"
                    inputClassName="px-1"
                  />
                </div>
              </div>
              <div className="rounded-md border border-border bg-background/70 p-2">
                <div className="text-[10px] uppercase tracking-wide text-muted-foreground">Type</div>
                <div className="mt-2 text-sm font-medium leading-tight">{item.weaponType || item.slot || "Weapon"}</div>
              </div>
            </div>

            {item.requiredKairo ? (
              <div className="mt-2 rounded-md border border-border bg-background/70 px-3 py-2 text-xs text-muted-foreground">
                Exchange: <span className="font-medium text-foreground">{item.requiredKairo}</span>
              </div>
            ) : null}
          </div>

          {visibleStats.length ? (
            <div className="grid grid-cols-2 gap-2">
              {visibleStats.map((stat) => (
                <div key={stat.name} className="rounded-lg border border-border bg-card p-3">
                  <div className="flex items-center gap-2 text-xs font-semibold text-muted-foreground">
                    {stat.icon ? <img src={stat.icon} alt={stat.name} className="h-4 w-4 object-contain" /> : null}
                    <span>{stat.shortLabel}</span>
                  </div>
                  <div className="mt-2 text-lg font-semibold tabular-nums text-foreground">{stat.value}</div>
                  <div className="mt-2 grid grid-cols-2 gap-2 text-[11px] text-muted-foreground">
                    <div>
                      <div>Base</div>
                      <div className="font-medium tabular-nums text-foreground">{stat.base}</div>
                    </div>
                    <div>
                      <div>+/Lv</div>
                      <div className="font-medium tabular-nums text-foreground">{stat.inc}</div>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="rounded-lg border border-border bg-card p-4 text-sm text-muted-foreground">
              No contributed stat data found for this equipment yet.
            </div>
          )}
        </div>

        <div className="hidden overflow-x-auto rounded-lg border border-border bg-card sm:block">
          <div className="min-w-[1060px]">
            <div className="grid grid-cols-[240px_78px_110px_repeat(12,minmax(58px,1fr))] items-stretch border-b border-border bg-muted/25 text-xs text-muted-foreground">
              <div className="px-4 py-3 font-medium">Name</div>
              <div className="px-2 py-3 text-center font-medium">Level</div>
              <div className="px-2 py-3 font-medium">Type</div>
              {GUIDE_EQUIPMENT_STATS.map((stat) => (
                <div key={stat} className="flex flex-col items-center gap-0.5 px-1 py-2 text-center">
                  {item.statIcons[stat] ? (
                    <img src={item.statIcons[stat]} alt={stat} className="h-4 w-4 object-contain" />
                  ) : (
                    <span className="text-[10px] font-semibold text-primary">{GUIDE_STAT_SHORT[stat] ?? stat}</span>
                  )}
                  <span className="text-[10px]">{GUIDE_STAT_SHORT[stat] ?? stat}</span>
                </div>
              ))}
            </div>

            <div className="grid grid-cols-[240px_78px_110px_repeat(12,minmax(58px,1fr))] items-center border-b border-border text-sm">
              <div className="flex min-w-0 items-center gap-2 px-4 py-3 font-semibold">
                {item.equipIcon ? (
                  <img src={item.equipIcon} alt={item.name} className="h-6 w-6 rounded object-contain" />
                ) : (
                  <span className="h-6 w-6 rounded border border-dashed border-border bg-muted/30" />
                )}
                <span className="truncate">{item.name}</span>
              </div>
              <div className="px-2 py-3">
                <ThemedNumberInput
                  value={level}
                  min={1}
                  max={99}
                  onValueChange={setClampedLevel}
                  className="h-9 w-[68px]"
                  inputClassName="px-1"
                />
              </div>
              <div className="px-2 py-3">
                <span className="inline-flex rounded-full border border-border bg-muted/40 px-2 py-1 text-[11px] font-medium">
                  {item.weaponType || item.slot || "Weapon"}
                </span>
              </div>
              {GUIDE_EQUIPMENT_STATS.map((statName) => {
                const stat = statMap.get(statName);
                const value = stat ? statAtLevel(stat.base, stat.inc, level) : 0;
                return (
                  <div
                    key={statName}
                    className={`px-1 py-3 text-center text-xs tabular-nums ${
                      stat ? "font-semibold text-foreground" : "text-destructive"
                    }`}
                  >
                    {stat ? value : "-"}
                  </div>
                );
              })}
            </div>

            {item.stats.length ? (
              <div className="grid grid-cols-2 gap-2 p-3 sm:grid-cols-3 lg:grid-cols-6">
                {item.stats.map((stat) => (
                  <div key={stat.name} className="rounded-md border border-border bg-background/70 px-2 py-2">
                    <div className="flex items-center gap-1 text-[10px] font-semibold text-muted-foreground">
                      {item.statIcons[stat.name] ? <img src={item.statIcons[stat.name]} alt={stat.name} className="h-3 w-3 object-contain" /> : null}
                      {stat.name}
                    </div>
                    <div className="mt-2 grid grid-cols-3 gap-1 text-center">
                      <div>
                        <div className="text-[9px] text-muted-foreground/70">Base</div>
                        <div className="text-sm font-medium tabular-nums">{stat.base}</div>
                      </div>
                      <div>
                        <div className="text-[9px] text-muted-foreground/70">+/Lv</div>
                        <div className="text-sm font-medium tabular-nums">{stat.inc}</div>
                      </div>
                      <div>
                        <div className="text-[9px] text-muted-foreground/70">Lv {level}</div>
                        <div className="text-sm font-semibold text-primary tabular-nums">{statAtLevel(stat.base, stat.inc, level)}</div>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="p-4 text-sm text-muted-foreground">No contributed stat data found for this equipment yet.</div>
            )}
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          {item.rankLabel ? <Badge variant="outline">Rank {item.rankLabel}</Badge> : null}
          {item.buyPrice ? <Badge variant="outline">{item.buyPrice} copper</Badge> : null}
          {item.requiredKairo ? <Badge variant="outline">Exchange: {item.requiredKairo}</Badge> : null}
          <Badge variant="outline">{item.shopName}</Badge>
        </div>

        <Link href={getEntityHref("equipment", item.name) ?? `/equipment-stats?search=${encodeURIComponent(item.name)}`}>
          <Button variant="outline" className="w-full">Open Full Equipment Page</Button>
        </Link>
      </DialogContent>
    </Dialog>
  );
}

function SectionHeader({
  icon,
  title,
  description,
}: {
  icon: React.ReactNode;
  title: string;
  description: string;
}) {
  return (
    <div className="space-y-1">
      <div className="flex items-center gap-2">
        {icon}
        <h2 className="text-lg font-semibold tracking-tight">{title}</h2>
      </div>
      <p className="text-sm text-muted-foreground">{description}</p>
    </div>
  );
}

function EventRow({ event, now }: { event: ResolvedEvent; now: Date }) {
  const rowClass = event.isActive
    ? "flex flex-col gap-2 rounded-lg border border-emerald-500/60 bg-emerald-500/5 px-3 py-3 md:flex-row md:items-center md:justify-between"
    : "flex flex-col gap-2 rounded-lg border border-border px-3 py-3 md:flex-row md:items-center md:justify-between";
  return (
    <div className={rowClass}>
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="outline" className={eventKindClass(event.kind)}>
            {event.poolLabel}
          </Badge>
          {event.isActive && (
            <CategoryBadge category="success">
              Active now
            </CategoryBadge>
          )}
        </div>
        <div className="mt-1 font-medium">{event.title}</div>
        <div className="text-xs text-muted-foreground">
          {formatDate(event.startAt)} to {formatDate(event.endAt)} · {event.durationDays} day{event.durationDays === 1 ? "" : "s"}
        </div>
        {event.notes && <div className="mt-1 text-xs text-muted-foreground max-w-2xl">{event.notes}</div>}
      </div>
      <div className="text-sm font-medium md:text-right">
        {event.isActive
          ? `Ends in ${formatCountdown(event.endAt.getTime() - now.getTime())}`
          : event.isUpcoming
          ? `Starts in ${formatCountdown(event.startAt.getTime() - now.getTime())}`
          : "Inactive"}
      </div>
    </div>
  );
}

function CompactEventRow({ event, now }: { event: ResolvedEvent; now: Date }) {
  const rowClass = event.isActive
    ? "rounded-lg border border-emerald-500/60 bg-emerald-500/5 px-3 py-2"
    : "rounded-lg border border-border px-3 py-2";
  return (
    <div className={rowClass}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="font-medium truncate">{event.title}</div>
          <div className="text-xs text-muted-foreground">
            {formatDate(event.startAt)} to {formatDate(event.endAt)} · {event.durationDays} day{event.durationDays === 1 ? "" : "s"}
          </div>
        </div>
        <div className="text-xs font-medium whitespace-nowrap">
          {event.isActive
            ? `Ends in ${formatCountdown(event.endAt.getTime() - now.getTime())}`
            : event.isUpcoming
            ? `Starts in ${formatCountdown(event.startAt.getTime() - now.getTime())}`
            : "Inactive"}
        </div>
      </div>
    </div>
  );
}

function PlainEventRow({
  event,
  now,
  onOpenWeapon,
  equipIcons,
}: {
  event: ResolvedEvent;
  now: Date;
  onOpenWeapon: (name: string) => void;
  equipIcons?: Record<string, string>;
}) {
  const rowClass = event.isActive
    ? "rounded-lg border border-emerald-500/60 bg-emerald-500/5 px-3 py-3"
    : "rounded-lg border border-border px-3 py-3";
  const weaponNames = event.kind === "weapons" ? weaponNamesFromEvent(event) : [];
  const titleContent =
    event.kind === "weapons" && weaponNames.length === 2 ? (
      <span>
        <button
          type="button"
          onClick={() => onOpenWeapon(weaponNames[0])}
          className="inline-flex items-center gap-1 font-medium text-foreground/85 underline decoration-foreground/30 underline-offset-2 hover:text-foreground"
        >
          {getEquipmentIcon(equipIcons, weaponNames[0]) && (
            <img src={getEquipmentIcon(equipIcons, weaponNames[0])} alt="" className="h-4 w-4 rounded object-contain" />
          )}
          {weaponNames[0]}
        </button>
        {" + "}
        <button
          type="button"
          onClick={() => onOpenWeapon(weaponNames[1])}
          className="inline-flex items-center gap-1 font-medium text-foreground/85 underline decoration-foreground/30 underline-offset-2 hover:text-foreground"
        >
          {getEquipmentIcon(equipIcons, weaponNames[1]) && (
            <img src={getEquipmentIcon(equipIcons, weaponNames[1])} alt="" className="h-4 w-4 rounded object-contain" />
          )}
          {weaponNames[1]}
        </button>
      </span>
    ) : event.kind === "weapons" ? (
      <button
        type="button"
        onClick={() => onOpenWeapon(weaponNames[0] ?? event.title)}
        className="inline-flex items-center gap-1 font-medium text-foreground/85 underline decoration-foreground/30 underline-offset-2 hover:text-foreground"
      >
        {getEquipmentIcon(equipIcons, weaponNames[0] ?? event.title) && (
          <img src={getEquipmentIcon(equipIcons, weaponNames[0] ?? event.title)} alt="" className="h-4 w-4 rounded object-contain" />
        )}
        {event.title}
      </button>
    ) : (
      event.title
    );
  return (
    <div className={rowClass}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="font-medium truncate">{titleContent}</div>
          <div className="text-xs text-muted-foreground">
            {formatDate(event.startAt)} to {formatDate(event.endAt)} · {event.durationDays} day{event.durationDays === 1 ? "" : "s"}
          </div>
          {event.notes && event.kind !== "weapons" && <div className="mt-1 text-xs text-muted-foreground max-w-2xl">{event.notes}</div>}
          {event.kind === "weapons" && weaponNames.length > 0 && (
            <div className="mt-1 flex flex-wrap items-center gap-x-1 gap-y-1 text-xs text-muted-foreground max-w-2xl">
              {weaponNames.map((name, index) => (
                <span key={`${event.id}-${name}`}>
                  <button
                    type="button"
                    onClick={() => onOpenWeapon(name)}
                    className="inline-flex items-center gap-1 font-medium text-foreground/85 underline decoration-foreground/30 underline-offset-2 hover:text-foreground"
                  >
                    {getEquipmentIcon(equipIcons, name) && (
                      <img src={getEquipmentIcon(equipIcons, name)} alt="" className="h-4 w-4 rounded object-contain" />
                    )}
                    {name}
                  </button>
                  {index < weaponNames.length - 1 ? " | " : ""}
                </span>
              ))}
            </div>
          )}
        </div>
        <div className="text-xs font-medium whitespace-nowrap">
          {event.isActive
            ? `Ends in ${formatCountdown(event.endAt.getTime() - now.getTime())}`
            : event.isUpcoming
            ? `Starts in ${formatCountdown(event.startAt.getTime() - now.getTime())}`
            : "Inactive"}
        </div>
      </div>
    </div>
  );
}

function SummaryCard({
  kind,
  active,
  upcoming,
  now,
  onOpenWeapon,
  equipIcons,
}: {
  kind: EventKind;
  active: ResolvedEvent | null;
  upcoming: ResolvedEvent | null;
  now: Date;
  onOpenWeapon: (name: string) => void;
  equipIcons?: Record<string, string>;
}) {
  const focus = active ?? upcoming;
  const weaponNames = focus && kind === "weapons" ? weaponNamesFromEvent(focus) : [];
  return (
    <Card className={active ? "border-emerald-500/60 bg-emerald-500/5" : undefined}>
      <CardHeader className="pb-2">
        <div className="flex items-center gap-2">
          {eventKindIcon(kind)}
          <CardTitle className="text-base">{eventKindLabel(kind)}</CardTitle>
        </div>
        <CardDescription>
          {active ? "Active now" : upcoming ? "Upcoming" : "No event shown"}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-2 text-sm">
        {focus ? (
          <>
            <div className="font-medium">
              {kind === "weapons" && weaponNames.length > 0 ? (
                <span className="flex flex-wrap gap-x-1">
                  {weaponNames.map((name, index) => (
                    <span key={`${focus.id}-summary-${name}`}>
                      <button
                        type="button"
                        onClick={() => onOpenWeapon(name)}
                        className="inline-flex items-center gap-1 font-medium text-foreground/85 underline decoration-foreground/30 underline-offset-2 hover:text-foreground"
                      >
                        {getEquipmentIcon(equipIcons, name) && (
                          <img src={getEquipmentIcon(equipIcons, name)} alt="" className="h-4 w-4 rounded object-contain" />
                        )}
                        {name}
                      </button>
                      {index < weaponNames.length - 1 ? " + " : ""}
                    </span>
                  ))}
                </span>
              ) : kind === "weapons" ? (
                <button
                  type="button"
                  onClick={() => onOpenWeapon(focus.title)}
                  className="inline-flex items-center gap-1 font-medium text-foreground/85 underline decoration-foreground/30 underline-offset-2 hover:text-foreground"
                >
                  {getEquipmentIcon(equipIcons, focus.title) && (
                    <img src={getEquipmentIcon(equipIcons, focus.title)} alt="" className="h-4 w-4 rounded object-contain" />
                  )}
                  {focus.title}
                </button>
              ) : (
                focus.title
              )}
            </div>
            <div className="text-xs text-muted-foreground">{focus.poolLabel}</div>
            <div className="text-xs text-muted-foreground">
              {formatDate(focus.startAt)} to {formatDate(focus.endAt)}
            </div>
            <div className="text-xs">
              <span className="font-medium">{active ? "Active now" : "Upcoming"}</span>
            </div>
            <div className="text-xs">
              <span className="text-muted-foreground">Starts in:</span>{" "}
              <span className="font-medium">
                {active ? "Already live" : formatCountdown(focus.startAt.getTime() - now.getTime())}
              </span>
            </div>
            <div className="text-xs">
              <span className="text-muted-foreground">Time remaining:</span>{" "}
              <span className="font-medium">
                {active ? formatCountdown(focus.endAt.getTime() - now.getTime()) : "Not live yet"}
              </span>
            </div>
            <div className="text-xs">
              <span className="text-muted-foreground">Duration:</span>{" "}
              <span className="font-medium">
                {focus.durationDays} day{focus.durationDays === 1 ? "" : "s"}
              </span>
            </div>
          </>
        ) : (
          <div className="text-muted-foreground">No schedule shown yet.</div>
        )}
      </CardContent>
    </Card>
  );
}

export default function GachaEventsPage() {
  const equipIcons = useEquipmentIcons();
  const [now, setNow] = useState(() => new Date());
  const [eventOffset] = useEventHourOffset();
  const [weaponPreview, setWeaponPreview] = useState<WeaponPreviewItem | null>(null);
  const [weaponPreviewOpen, setWeaponPreviewOpen] = useState(false);

  useEffect(() => {
    const interval = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(interval);
  }, []);

  const resolved = useMemo(() => ALL_GACHA_EVENTS.map((event) => resolveGachaEvent(event, now, eventOffset)), [eventOffset, now]);
  const activeEvents = useMemo(() => resolved.filter((event) => event.isActive), [resolved]);
  const nextOverall = useMemo(
    () =>
      resolved
        .filter((event) => event.isUpcoming)
        .sort((a, b) => a.startAt.getTime() - b.startAt.getTime())[0] ?? null,
    [resolved]
  );

  const upcomingJobs = useMemo(
    () => resolved.filter((event) => event.kind === "jobs").sort((a, b) => a.startAt.getTime() - b.startAt.getTime()).slice(0, 12),
    [resolved]
  );
  const facilityEvents = useMemo(
    () => resolved.filter((event) => event.kind === "facilities").sort((a, b) => a.startAt.getTime() - b.startAt.getTime()),
    [resolved]
  );
  const upcomingSWeapons = useMemo(
    () =>
      resolved
        .filter((event) => event.kind === "weapons" && event.poolLabel === "One-day S weapon pool")
        .sort((a, b) => a.startAt.getTime() - b.startAt.getTime())
        .slice(0, 10),
    [resolved]
  );
  const upcomingKairo = useMemo(
    () =>
      resolved
        .filter((event) => event.kind === "weapons" && event.title === "Kairo Series")
        .sort((a, b) => a.startAt.getTime() - b.startAt.getTime())
        .slice(0, 4),
    [resolved]
  );
  const summaryByKind = useMemo(() => {
    const kinds: EventKind[] = ["jobs", "weapons", "facilities", "items"];
    return kinds.map((kind) => {
      const scoped = resolved.filter((event) => event.kind === kind).sort((a, b) => a.startAt.getTime() - b.startAt.getTime());
      return {
        kind,
        active: scoped.find((event) => event.isActive) ?? null,
        upcoming: scoped.find((event) => event.isUpcoming) ?? null,
      };
    });
  }, [resolved]);

  const handleOpenWeapon = (name: string) => {
    const preview = buildWeaponPreviewItem(normalizeWeaponSearchName(name), equipIcons);
    if (!preview) return;
    setWeaponPreview(preview);
    setWeaponPreviewOpen(true);
  };

  return (
    <div className="max-w-6xl mx-auto px-4 py-6 space-y-6">
      <PageHeader icon={<CalendarDays className="w-5 h-5" />} title="Gacha Events">
        <p className="text-sm text-muted-foreground max-w-3xl">
          Focused event tracker for the parts of gacha that matter most: S-rank jobs, repeating S facility events, and S-weapon days.
        </p>
        <p className="text-xs text-muted-foreground max-w-3xl">
          Times follow your local event clock with your event offset applied ({eventOffset >= 0 ? `+${eventOffset}` : eventOffset}h).
        </p>
      </PageHeader>

      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        {summaryByKind.map((summary) => (
          <SummaryCard
            key={summary.kind}
            kind={summary.kind}
            active={summary.active}
            upcoming={summary.upcoming}
            now={now}
            onOpenWeapon={handleOpenWeapon}
            equipIcons={equipIcons}
          />
        ))}
      </div>

      <section className="space-y-3">
        <SectionHeader
          icon={<Sparkles className="w-5 h-5 text-rose-500" />}
          title="S Rank Jobs"
          description="The next featured S-rank job windows from the current yearly rotation."
        />
        <div className="grid gap-2 lg:grid-cols-2">
          {upcomingJobs.map((event) => (
            <CompactEventRow key={event.id} event={event} now={now} />
          ))}
        </div>
      </section>

      <section className="space-y-3">
        <SectionHeader
          icon={<Wand2 className="w-5 h-5 text-lime-500" />}
          title="S Facility Event"
          description="Full monthly S facility schedule, including High Grade Storage as one grouped entry plus the other top-tier facility rewards."
        />
        <div className="grid gap-2 md:grid-cols-2">
          {facilityEvents.map((event) => (
          <PlainEventRow key={event.id} event={event} now={now} onOpenWeapon={handleOpenWeapon} equipIcons={equipIcons} />
          ))}
        </div>
      </section>

      <section className="space-y-3">
        <SectionHeader
          icon={<Swords className="w-5 h-5 text-amber-500" />}
          title="S Weapon Events"
          description="One-day S-weapon bonus rows plus the recurring Kairo weapon windows from the equipment gacha."
        />
        <div className="grid gap-3 lg:grid-cols-2">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-base">Next S Weapon Days</CardTitle>
              <CardDescription>Upcoming one-day featured S weapon pools</CardDescription>
            </CardHeader>
            <CardContent className="space-y-2">
              {upcomingSWeapons.map((event) => (
                <PlainEventRow key={event.id} event={event} now={now} onOpenWeapon={handleOpenWeapon} equipIcons={equipIcons} />
              ))}
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-base">Kairo Windows</CardTitle>
              <CardDescription>Recurring multi-day weapon banner windows</CardDescription>
            </CardHeader>
            <CardContent className="space-y-2">
              {upcomingKairo.map((event) => (
                <PlainEventRow key={event.id} event={event} now={now} onOpenWeapon={handleOpenWeapon} equipIcons={equipIcons} />
              ))}
            </CardContent>
          </Card>
        </div>
      </section>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Scope note</CardTitle>
          <CardDescription>What this page is and is not trying to do</CardDescription>
        </CardHeader>
        <CardContent className="space-y-2 text-sm text-muted-foreground">
          <div>This page tracks schedules, durations, and featured pools for the major events.</div>
          <div>It is not trying to be a full odds explorer yet.</div>
        </CardContent>
      </Card>

      <WeaponPreviewDialog
        item={weaponPreview}
        open={weaponPreviewOpen}
        onOpenChange={setWeaponPreviewOpen}
      />
    </div>
  );
}
