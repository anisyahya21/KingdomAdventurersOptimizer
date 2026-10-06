import { forwardRef, useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowRight, Calculator, CheckCircle2, Route, Sigma, TableProperties } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { ButtonProps } from "@/components/ui/button";
import { EquipmentSearchSelect, type EquipmentSearchOption } from "@/components/ka/equipment-search-select";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ThemedNumberInput } from "@/components/ui/themed-number-input";
import { parseCsv } from "@/lib/monster-truth";
import { apiUrl } from "@/lib/api";
import { getEquipmentIcon } from "@/lib/equipment-icons";
import { fetchSharedWithFallback } from "@/lib/local-shared-data";
import { EQUIPMENT_CATALOG, EQUIPMENT_EXCHANGE_ROWS } from "@/lib/generated-equipment-data";
import { matchesLooseSearch } from "@/lib/search-normalize";
import equipCsv from "../../../../data/sheet-research/raw-copies/KA GameData - Equip.csv?raw";
import expCsv from "../../../../data/sheet-research/raw-copies/KA GameData - Exp.csv?raw";

const MAX_LEVEL = 99;
const PLAYER_EQUIPMENT_NAME = /^[FSABCDEG]\s*\//i;
const LEVEL_CAPS = [5, 10, 20, 30, 40, 50, 60, 70, 80, 90, 99] as const;
const BASIC_EXP_STATS = ["HP", "MP", "Vigor", "Attack", "Defence", "Speed", "Luck"];
const ACCESSORY_EXP_STATS = ["Intelligence", "Dexterity", "Gather", "Move", "Heart"];

type EquipStat = { base: number; inc: number };
type Equipment = {
  id: number;
  name: string;
  category: number;
  rank: number;
  rankLabel: string;
  type: number;
  expRate: number;
  mixBonusExp: number;
  buyPrice: number | null;
  stats: Record<string, EquipStat>;
};

type SharedData = {
  equipIcons?: Record<string, string>;
};

type CalcSnapshot = {
  recipientId: string;
  recipientCurrentLevel: number;
  recipientTargetLevel: number;
  sourceId: string;
  sourceLevel: number;
};

type RouteCandidate = {
  label: string;
  expPerSource: number;
  quantity: number;
  totalExp: number;
  wasteExp: number;
  copperCost: number | null;
  steps: string[];
  formulaSteps?: GrandFormulaStep[];
};

type StageBreakdown = {
  stage: string;
  expNeeded: number;
  quantityNeeded: number;
  usefulExp: number;
  wastedExp: number;
  copperCost: number | null;
};

type StagePlan = {
  stages: StageBreakdown[];
  totalQuantity: number;
  totalCopperCost: number | null;
  totalUsefulExp: number;
  totalWastedExp: number;
};

type GrandFormulaStep = {
  stage: string;
  startLevel: number;
  endLevel: number;
  sourceName: string;
  sourceLevel: number;
  expPerSource: number;
  quantity: number;
  fedExp: number;
  neededExp: number;
  wastedExp: number;
  copperEach: number | null;
  copperCost: number | null;
};

type TwoItemResult = {
  found: boolean;
  label: string;
  optionLabel?: string;
  totalCopperCost: number;
  totalWastedExp: number;
  itemASacrifices: number;
  itemBSacrifices: number;
  steps: TwoItemStep[];
};

type TwoItemRoutes = {
  cheapest: TwoItemResult;
  fewerSacrifices: TwoItemResult | null;
  balanced: TwoItemResult | null;
  all: TwoItemResult[];
};

type TwoItemOptionSlot = {
  title: string;
  missingText: string;
  result: TwoItemResult | null;
};

type TwoItemStep = {
  action: TwoItemAction;
  sourceName: string;
  sourceLevel: number;
  recipientName: string;
  recipientStartLevel: number;
  recipientEndLevel: number;
  quantity: number;
  usefulExp: number;
  wastedExp: number;
  copperCost: number;
};

type GrandRow = { checked: boolean; level: number };

const exchangeCostByInputId = new Map<number, number>();
for (const row of EQUIPMENT_EXCHANGE_ROWS as readonly { inputId: number; buyPrice: number; tradable: boolean }[]) {
  if (!row.tradable) continue;
  const current = exchangeCostByInputId.get(row.inputId);
  if (current == null || row.buyPrice < current) exchangeCostByInputId.set(row.inputId, row.buyPrice);
}

function toNumber(value: string | undefined) {
  const parsed = Number(value ?? "");
  return Number.isFinite(parsed) ? parsed : 0;
}

function clampLevel(value: number) {
  return Math.min(MAX_LEVEL, Math.max(1, Math.floor(value || 1)));
}

function formatNumber(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value)) return "N/A";
  return Math.max(0, Math.floor(value)).toLocaleString();
}

function rankLabelFromName(name: string) {
  return name.trim().match(/^([A-Z])\s*\//i)?.[1]?.toUpperCase() ?? "?";
}

function parseEquipment(): Equipment[] {
  const rows = parseCsv(equipCsv);
  const headerIndex = rows.findIndex((row) => row.includes("name") && row.includes("expRate"));
  const header = rows[headerIndex] ?? [];
  const categoryIndex = header.indexOf("category");
  const rankIndex = header.indexOf("rank");
  const typeIndex = header.indexOf("type");
  const expRateIndex = header.indexOf("expRate");
  const mixBonusExpIndex = header.indexOf("mixBonusExp");

  const statColumns: Array<{ name: string; start: number; increment: number }> = [];
  for (let index = 0; index < header.length - 1; index += 1) {
    const startHeader = (header[index] ?? "").trim();
    const nextHeader = (header[index + 1] ?? "").trim();
    const name = startHeader.endsWith(" start")
      ? startHeader.replace(/\s+start$/, "").trim()
      : "";
    if (!name || nextHeader !== "increment") continue;
    const normalizedName =
      name === "Atk" ? "Attack" :
      name === "Def" ? "Defence" :
      name === "Spd" ? "Speed" :
      name === "Lck" ? "Luck" :
      name === "Int" ? "Intelligence" :
      name === "Dex" ? "Dexterity" :
      name === "Gth" ? "Gather" :
      name === "Mov" ? "Move" :
      name === "Hrt" ? "Heart" :
      name;
    if (normalizedName === "Owned (??)") continue;
    statColumns.push({ name: normalizedName, start: index, increment: index + 1 });
  }

  const rawById = new Map(
    rows
      .slice(headerIndex + 1)
      .map((row) => {
        const id = toNumber(row[0]);
        return [id, row] as const;
      })
      .filter(([id]) => id > 0),
  );

  return (EQUIPMENT_CATALOG as readonly {
    id: number;
    name: string;
    rank: number;
    rankLabel: string;
    type: number;
    buyPrice: number;
  }[])
    .map((catalogItem) => {
      const row = rawById.get(catalogItem.id) ?? [];
      const name = catalogItem.name;
      const stats = statColumns.reduce<Record<string, EquipStat>>((acc, stat) => {
        acc[stat.name] = { base: toNumber(row[stat.start]), inc: toNumber(row[stat.increment]) };
        return acc;
      }, {});
      const exchangeCost = exchangeCostByInputId.get(catalogItem.id);
      return {
        id: catalogItem.id,
        name,
        category: toNumber(row[categoryIndex]),
        rank: catalogItem.rank || toNumber(row[rankIndex]),
        rankLabel: catalogItem.rankLabel || rankLabelFromName(name),
        type: catalogItem.type || toNumber(row[typeIndex]),
        expRate: toNumber(row[expRateIndex]),
        mixBonusExp: toNumber(row[mixBonusExpIndex]),
        buyPrice: exchangeCost ?? catalogItem?.buyPrice ?? null,
        stats,
      };
    })
    .filter((item) => item.id > 0 && PLAYER_EQUIPMENT_NAME.test(item.name))
    .sort((a, b) => a.rank - b.rank || a.type - b.type || a.name.localeCompare(b.name));
}

function parseExpByLevel() {
  const rows = parseCsv(expCsv);
  const byLevel = new Map<number, number>();
  for (const row of rows.slice(1)) {
    const level = toNumber(row[1] || row[0]);
    if (level > 0) byLevel.set(level, toNumber(row[2]));
  }
  return byLevel;
}

const EQUIPMENT = parseEquipment();
const EXP_BY_LEVEL = parseExpByLevel();
const EQUIPMENT_BY_ID = new Map(EQUIPMENT.map((item) => [String(item.id), item]));
const EQUIPMENT_SEARCH_OPTIONS: EquipmentSearchOption[] = EQUIPMENT.map((item) => ({
  value: String(item.id),
  label: item.name,
  detail: `Rank ${item.rankLabel}`,
  iconName: item.name,
}));
const OPTIMIZER_STORAGE_KEY = "equipment-leveling-optimizer:state";
const GRAND_STORAGE_KEY = "equipment-leveling-optimizer:grand";
const GRAND_RANK_OPTIONS = ["F", "E", "D", "C", "B", "A", "S"];
const DEFAULT_GRAND_ROWS = Object.fromEntries(
  EQUIPMENT.map((item) => [String(item.id), { checked: true, level: 1 }]),
) as Record<string, GrandRow>;

function readGrandStorage() {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(GRAND_STORAGE_KEY);
    return raw ? JSON.parse(raw) as {
      recipientId?: string;
      currentLevel?: number;
      targetLevel?: number;
      rows?: Record<string, Partial<GrandRow>>;
    } : null;
  } catch {
    return null;
  }
}

function readOptimizerStorage() {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(OPTIMIZER_STORAGE_KEY);
    return raw ? JSON.parse(raw) as {
      recipientId?: string;
      recipientCurrentLevel?: number;
      recipientTargetLevel?: number;
      sourceId?: string;
      sourceLevel?: number;
      itemAId?: string;
      itemALevel?: number;
      itemATargetLevel?: number;
      itemBId?: string;
      itemBLevel?: number;
      itemBTargetLevel?: number;
      grand?: {
        recipientId?: string;
        currentLevel?: number;
        targetLevel?: number;
        rows?: Record<string, Partial<GrandRow>>;
      };
    } : null;
  } catch {
    return null;
  }
}

function buildGrandRowsFromStorage(storedRows: Record<string, Partial<GrandRow>> | undefined) {
  return Object.fromEntries(
    EQUIPMENT.map((item) => {
      const id = String(item.id);
      const stored = storedRows?.[id];
      return [
        id,
        {
          checked: stored?.checked ?? true,
          level: clampLevel(stored?.level ?? 1),
        },
      ];
    }),
  ) as Record<string, GrandRow>;
}

function statAtLevel(stat: EquipStat, level: number) {
  return Math.round(stat.base + (clampLevel(level) - 1) * stat.inc);
}

function totalParamAtLevel(item: Equipment | undefined, level: number) {
  if (!item) return 0;
  const statNames = item.category === 2 ? ACCESSORY_EXP_STATS : BASIC_EXP_STATS;
  return statNames.reduce((sum, statName) => {
    const stat = item.stats[statName];
    return sum + Math.max(0, stat ? statAtLevel(stat, level) : 0);
  }, 0);
}

function expFromSource(recipient: Equipment | undefined, source: Equipment | undefined, sourceLevel: number) {
  if (!recipient || !source) return { totalParam: 0, weightedExp: 0, finalExp: 0 };
  const totalParam = totalParamAtLevel(source, sourceLevel);
  const weightedExp = Math.floor((totalParam * recipient.expRate * 60) / 10000);
  const finalExp = Math.floor((weightedExp + source.mixBonusExp) * 1.5);
  return { totalParam, weightedExp, finalExp };
}

function expNeededForSheetRange(startLevel: number, targetLevel: number) {
  const start = clampLevel(startLevel);
  const target = clampLevel(targetLevel);
  if (target <= start) return 0;
  let total = 0;
  for (let level = start; level < target; level += 1) {
    total += EXP_BY_LEVEL.get(level) ?? 0;
  }
  return total;
}

function nextStageEnd(level: number, targetLevel: number) {
  const target = clampLevel(targetLevel);
  return LEVEL_CAPS.find((cap) => cap > level && cap <= target) ?? target;
}

function stageExpNeeded(startLevel: number, endLevel: number) {
  return expNeededForSheetRange(startLevel, endLevel);
}

function nextLevelExpNeeded(currentLevel: number) {
  return EXP_BY_LEVEL.get(clampLevel(currentLevel)) ?? 0;
}

function currentCapStart(level: number) {
  const clamped = clampLevel(level);
  if (LEVEL_CAPS.includes(clamped as (typeof LEVEL_CAPS)[number])) return clamped;
  const previousCap = [...LEVEL_CAPS].reverse().find((cap) => cap < clamped);
  return previousCap ?? 1;
}

function capProgressForLevel(level: number) {
  const clamped = clampLevel(level);
  const start = currentCapStart(clamped);
  if (clamped <= start) return 0;
  return stageExpNeeded(start, clamped);
}

function capTotalFromLevel(level: number, targetLevel: number) {
  const stageEnd = nextStageEnd(level, targetLevel);
  return stageExpNeeded(currentCapStart(level), stageEnd);
}

function levelFromCapProgress(level: number, progress: number, targetLevel: number) {
  let nextLevel = currentCapStart(level);
  const stageStart = currentCapStart(level);
  const stageEnd = nextStageEnd(level, targetLevel);
  for (let candidate = stageStart + 1; candidate <= stageEnd; candidate += 1) {
    if (progress < stageExpNeeded(stageStart, candidate)) break;
    nextLevel = candidate;
  }
  return nextLevel;
}

function buildStagePlan(
  currentLevel: number,
  targetLevel: number,
  expPerSacrifice: number,
  sourceCopperCost: number | null | undefined,
): StagePlan {
  let level = clampLevel(currentLevel);
  const target = clampLevel(targetLevel);
  const stages: StageBreakdown[] = [];
  let totalQuantity = 0;
  let totalCopperCost: number | null = sourceCopperCost == null ? null : 0;
  let totalUsefulExp = 0;
  let totalWastedExp = 0;

  while (level < target && expPerSacrifice > 0) {
    const stageEnd = nextStageEnd(level, target);
    const needed = stageExpNeeded(level, stageEnd);
    const quantityNeeded = Math.ceil(needed / expPerSacrifice);
    const totalFedExp = quantityNeeded * expPerSacrifice;
    const wastedExp = Math.max(0, totalFedExp - needed);
    const copperCost = sourceCopperCost == null ? null : quantityNeeded * sourceCopperCost;

    stages.push({
      stage: `Lv${level} to Lv${stageEnd}`,
      expNeeded: needed,
      quantityNeeded,
      usefulExp: needed,
      wastedExp,
      copperCost,
    });

    totalQuantity += quantityNeeded;
    if (totalCopperCost != null && copperCost != null) totalCopperCost += copperCost;
    totalUsefulExp += needed;
    totalWastedExp += wastedExp;
    level = stageEnd;
  }

  return { stages, totalQuantity, totalCopperCost, totalUsefulExp, totalWastedExp };
}

function applyExpToCappedItem(level: number, progress: number, targetLevel: number, gainedExp: number) {
  if (level >= targetLevel) return { level, progress, usefulExp: 0, wastedExp: gainedExp };
  const stageEnd = nextStageEnd(level, targetLevel);
  const stageTotal = stageExpNeeded(level, stageEnd);
  const needed = Math.max(0, stageTotal - progress);
  const usefulExp = Math.min(gainedExp, needed);
  const wastedExp = gainedExp - usefulExp;
  const nextProgress = progress + usefulExp;
  if (nextProgress >= stageTotal) {
    return { level: stageEnd, progress: 0, usefulExp, wastedExp };
  }
  return { level, progress: nextProgress, usefulExp, wastedExp };
}

type TwoItemState = {
  aLevel: number;
  aProgress: number;
  bLevel: number;
  bProgress: number;
  totalCopperCost: number;
  totalWastedExp: number;
  itemASacrifices: number;
  itemBSacrifices: number;
  steps: TwoItemStep[];
};

type TwoItemAction = "A_TO_B" | "B_TO_A";

function applyBatchToRecipient({
  state,
  itemA,
  itemB,
  action,
  aTarget,
  bTarget,
}: {
  state: TwoItemState;
  itemA: Equipment;
  itemB: Equipment;
  action: TwoItemAction;
  aTarget: number;
  bTarget: number;
}) {
  const source = action === "A_TO_B" ? itemA : itemB;
  const recipient = action === "A_TO_B" ? itemB : itemA;
  const sourceLevel = action === "A_TO_B" ? state.aLevel : state.bLevel;
  const recipientLevel = action === "A_TO_B" ? state.bLevel : state.aLevel;
  const recipientProgress = action === "A_TO_B" ? state.bProgress : state.aProgress;
  const recipientTarget = action === "A_TO_B" ? bTarget : aTarget;
  const copperEach = source.buyPrice ?? 0;
  const expPerSource = expFromSource(recipient, source, sourceLevel).finalExp;

  if (recipientLevel >= recipientTarget || expPerSource <= 0) return null;

  const stageEnd = nextStageEnd(recipientLevel, recipientTarget);
  const stageStart = currentCapStart(recipientLevel);
  const stageTotal = stageExpNeeded(stageStart, stageEnd);
  const nextUsefulLevel = Math.min(stageEnd, recipientLevel + 1);
  const nextLevelProgress = stageExpNeeded(stageStart, nextUsefulLevel);
  const neededToNextUsefulLevel = Math.max(0, nextLevelProgress - recipientProgress);
  const neededToStageEnd = Math.max(0, stageTotal - recipientProgress);
  const quantity = Math.max(1, Math.ceil(neededToNextUsefulLevel / expPerSource));
  const totalFedExp = quantity * expPerSource;
  const usefulExp = Math.min(totalFedExp, neededToStageEnd);
  const wastedExp = totalFedExp - usefulExp;
  const nextProgress = recipientProgress + usefulExp;
  const reachedStageEnd = nextProgress >= stageTotal;
  const nextRecipientLevel = reachedStageEnd ? stageEnd : levelFromCapProgress(recipientLevel, nextProgress, recipientTarget);
  const nextRecipientProgress = reachedStageEnd ? 0 : nextProgress;

  const nextState: TwoItemState = {
    ...state,
    totalCopperCost: state.totalCopperCost + quantity * copperEach,
    totalWastedExp: state.totalWastedExp + wastedExp,
    itemASacrifices: state.itemASacrifices + (action === "A_TO_B" ? quantity : 0),
    itemBSacrifices: state.itemBSacrifices + (action === "B_TO_A" ? quantity : 0),
    steps: [
      ...state.steps,
      {
        action,
        sourceName: source.name,
        sourceLevel,
        recipientName: recipient.name,
        recipientStartLevel: recipientLevel,
        recipientEndLevel: nextRecipientLevel,
        quantity,
        usefulExp,
        wastedExp,
        copperCost: quantity * copperEach,
      },
    ],
  };

  if (action === "A_TO_B") {
    nextState.bLevel = nextRecipientLevel;
    nextState.bProgress = nextRecipientProgress;
  } else {
    nextState.aLevel = nextRecipientLevel;
    nextState.aProgress = nextRecipientProgress;
  }

  return nextState;
}

function scoreTwoItemState(state: TwoItemState) {
  return state.totalCopperCost * 1_000_000 + state.totalWastedExp;
}

function scoreTwoItemResult(result: TwoItemResult) {
  return result.totalCopperCost * 1_000_000 + result.totalWastedExp;
}

function totalSacrifices(result: TwoItemResult) {
  return result.itemASacrifices + result.itemBSacrifices;
}

function displayedStepCount(result: TwoItemResult) {
  return compactTwoItemSteps(result.steps).length;
}

function compactTwoItemSteps(steps: TwoItemStep[]) {
  return steps.reduce<TwoItemStep[]>((acc, step) => {
    const previous = acc[acc.length - 1];
    const previousStoppedAtCap = LEVEL_CAPS.includes(previous?.recipientEndLevel as (typeof LEVEL_CAPS)[number]);
    if (
      previous &&
      !previousStoppedAtCap &&
      previous.action === step.action &&
      previous.sourceName === step.sourceName &&
      previous.sourceLevel === step.sourceLevel &&
      previous.recipientName === step.recipientName &&
      previous.recipientEndLevel === step.recipientStartLevel
    ) {
      previous.quantity += step.quantity;
      previous.usefulExp += step.usefulExp;
      previous.wastedExp += step.wastedExp;
      previous.copperCost += step.copperCost;
      previous.recipientEndLevel = step.recipientEndLevel;
      return acc;
    }
    acc.push({ ...step });
    return acc;
  }, []);
}

function uniqueTwoItemResults(results: TwoItemResult[]) {
  const seen = new Set<string>();
  return results.filter((result) => {
    const key = [
      result.found,
      result.totalCopperCost,
      result.totalWastedExp,
      result.itemASacrifices,
      result.itemBSacrifices,
      result.steps
        .map((step) => `${step.action}-${step.sourceLevel}-${step.recipientStartLevel}-${step.recipientEndLevel}-${step.quantity}`)
        .join("|"),
    ].join(":");
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function bestResultByStepCount(results: TwoItemResult[]) {
  const bySteps = new Map<number, TwoItemResult>();
  for (const result of results) {
    const steps = displayedStepCount(result);
    const current = bySteps.get(steps);
    if (
      !current ||
      result.totalCopperCost < current.totalCopperCost ||
      (result.totalCopperCost === current.totalCopperCost && scoreTwoItemResult(result) < scoreTwoItemResult(current))
    ) {
      bySteps.set(steps, result);
    }
  }
  return [...bySteps.values()];
}

function simulateTwoItemPlan({
  label,
  itemA,
  itemB,
  itemAStartLevel,
  itemATargetLevel,
  itemBStartLevel,
  itemBTargetLevel,
  chooser,
}: {
  label: string;
  itemA: Equipment;
  itemB: Equipment;
  itemAStartLevel: number;
  itemATargetLevel: number;
  itemBStartLevel: number;
  itemBTargetLevel: number;
  chooser: (state: TwoItemState) => TwoItemAction | null;
}): TwoItemResult {
  const aTarget = clampLevel(itemATargetLevel);
  const bTarget = clampLevel(itemBTargetLevel);
  let state: TwoItemState = {
    aLevel: clampLevel(itemAStartLevel),
    aProgress: capProgressForLevel(itemAStartLevel),
    bLevel: clampLevel(itemBStartLevel),
    bProgress: capProgressForLevel(itemBStartLevel),
    totalCopperCost: 0,
    totalWastedExp: 0,
    itemASacrifices: 0,
    itemBSacrifices: 0,
    steps: [],
  };

  for (let guard = 0; guard < 220; guard += 1) {
    if (state.aLevel >= aTarget && state.bLevel >= bTarget) {
      return { found: true, label, ...state };
    }
    const action = chooser(state);
    if (!action) break;
    const nextState = applyBatchToRecipient({ state, itemA, itemB, action, aTarget, bTarget });
    if (!nextState || scoreTwoItemState(nextState) === scoreTwoItemState(state)) break;
    state = nextState;
  }

  return { found: false, label, ...state };
}

function findTwoItemRoutes({
  itemA,
  itemB,
  itemAStartLevel,
  itemATargetLevel,
  itemBStartLevel,
  itemBTargetLevel,
}: {
  itemA: Equipment | undefined;
  itemB: Equipment | undefined;
  itemAStartLevel: number;
  itemATargetLevel: number;
  itemBStartLevel: number;
  itemBTargetLevel: number;
}): TwoItemRoutes | null {
  if (!itemA || !itemB) return null;
  const aTarget = clampLevel(itemATargetLevel);
  const bTarget = clampLevel(itemBTargetLevel);

  const plans = [
    simulateTwoItemPlan({
      label: `Level ${itemA.name} first`,
      itemA,
      itemB,
      itemAStartLevel,
      itemATargetLevel,
      itemBStartLevel,
      itemBTargetLevel,
      chooser: (state) => state.aLevel < aTarget ? "B_TO_A" : state.bLevel < bTarget ? "A_TO_B" : null,
    }),
    simulateTwoItemPlan({
      label: `Level ${itemB.name} first`,
      itemA,
      itemB,
      itemAStartLevel,
      itemATargetLevel,
      itemBStartLevel,
      itemBTargetLevel,
      chooser: (state) => state.bLevel < bTarget ? "A_TO_B" : state.aLevel < aTarget ? "B_TO_A" : null,
    }),
    simulateTwoItemPlan({
      label: "Dynamic best EXP per copper",
      itemA,
      itemB,
      itemAStartLevel,
      itemATargetLevel,
      itemBStartLevel,
      itemBTargetLevel,
      chooser: (state) => {
        const options: Array<{ action: TwoItemAction; score: number }> = [];
        if (state.bLevel < bTarget) {
          const exp = expFromSource(itemB, itemA, state.aLevel).finalExp;
          const cost = Math.max(1, itemA.buyPrice ?? 0);
          options.push({ action: "A_TO_B", score: exp / cost });
        }
        if (state.aLevel < aTarget) {
          const exp = expFromSource(itemA, itemB, state.bLevel).finalExp;
          const cost = Math.max(1, itemB.buyPrice ?? 0);
          options.push({ action: "B_TO_A", score: exp / cost });
        }
        return options.sort((left, right) => right.score - left.score)[0]?.action ?? null;
      },
    }),
    simulateTwoItemPlan({
      label: "Fewer sacrifices first",
      itemA,
      itemB,
      itemAStartLevel,
      itemATargetLevel,
      itemBStartLevel,
      itemBTargetLevel,
      chooser: (state) => {
        const options: Array<{ action: TwoItemAction; exp: number; cost: number }> = [];
        if (state.bLevel < bTarget) {
          options.push({
            action: "A_TO_B",
            exp: expFromSource(itemB, itemA, state.aLevel).finalExp,
            cost: itemA.buyPrice ?? 0,
          });
        }
        if (state.aLevel < aTarget) {
          options.push({
            action: "B_TO_A",
            exp: expFromSource(itemA, itemB, state.bLevel).finalExp,
            cost: itemB.buyPrice ?? 0,
          });
        }
        return options.sort((left, right) => right.exp - left.exp || left.cost - right.cost)[0]?.action ?? null;
      },
    }),
    simulateTwoItemPlan({
      label: "Balanced waste and copies",
      itemA,
      itemB,
      itemAStartLevel,
      itemATargetLevel,
      itemBStartLevel,
      itemBTargetLevel,
      chooser: (state) => {
        const options: Array<{ action: TwoItemAction; score: number }> = [];
        if (state.bLevel < bTarget) {
          const preview = applyBatchToRecipient({ state, itemA, itemB, action: "A_TO_B", aTarget, bTarget });
          if (preview) {
            const addedCost = preview.totalCopperCost - state.totalCopperCost;
            const addedWaste = preview.totalWastedExp - state.totalWastedExp;
            const addedCopies = preview.itemASacrifices - state.itemASacrifices;
            options.push({ action: "A_TO_B", score: addedCost * 5 + addedWaste / 50 + addedCopies * 2 });
          }
        }
        if (state.aLevel < aTarget) {
          const preview = applyBatchToRecipient({ state, itemA, itemB, action: "B_TO_A", aTarget, bTarget });
          if (preview) {
            const addedCost = preview.totalCopperCost - state.totalCopperCost;
            const addedWaste = preview.totalWastedExp - state.totalWastedExp;
            const addedCopies = preview.itemBSacrifices - state.itemBSacrifices;
            options.push({ action: "B_TO_A", score: addedCost * 5 + addedWaste / 50 + addedCopies * 2 });
          }
        }
        return options.sort((left, right) => left.score - right.score)[0]?.action ?? null;
      },
    }),
  ];

  const complete = plans.filter((plan) => plan.found);
  const candidates = complete.length > 0 ? complete : plans;
  const unique = bestResultByStepCount(uniqueTwoItemResults(candidates));
  const cheapest = [...unique].sort((left, right) => scoreTwoItemResult(left) - scoreTwoItemResult(right))[0];
  if (!cheapest) return null;
  const alternatives = unique.filter((result) => result !== cheapest && result.found);
  const cheapestSteps = displayedStepCount(cheapest);
  const minimumAlternativeSteps = Math.min(
    ...alternatives
      .filter((result) => displayedStepCount(result) < cheapestSteps && result.totalCopperCost >= cheapest.totalCopperCost)
      .map(displayedStepCount),
  );
  const hasFewerStepRoute = Number.isFinite(minimumAlternativeSteps);
  const middleStepTarget = hasFewerStepRoute ? Math.round((cheapestSteps + minimumAlternativeSteps) / 2) : cheapestSteps;
  const lessSteps =
    alternatives
      .filter(
        (result) =>
          displayedStepCount(result) < cheapestSteps &&
          displayedStepCount(result) > minimumAlternativeSteps &&
          result.totalCopperCost >= cheapest.totalCopperCost,
      )
      .sort(
        (left, right) =>
          Math.abs(displayedStepCount(left) - middleStepTarget) - Math.abs(displayedStepCount(right) - middleStepTarget) ||
          left.totalCopperCost - right.totalCopperCost ||
          totalSacrifices(left) - totalSacrifices(right),
      )[0] ?? null;
  const evenLessSteps =
    alternatives
      .filter(
        (result) =>
          displayedStepCount(result) === minimumAlternativeSteps &&
          result.totalCopperCost >= cheapest.totalCopperCost,
      )
      .sort(
        (left, right) =>
          left.totalCopperCost - right.totalCopperCost ||
          totalSacrifices(left) - totalSacrifices(right),
      )[0] ?? null;
  return {
    cheapest: { ...cheapest, optionLabel: "Option 1: Cheapest" },
    fewerSacrifices: lessSteps ? { ...lessSteps, optionLabel: "Option 2: Less Steps, Higher Cost" } : null,
    balanced: evenLessSteps ? { ...evenLessSteps, optionLabel: "Option 3: Even Less Steps, Higher Cost" } : null,
    all: unique,
  };
}

function NumberInput({
  id,
  value,
  onChange,
  min = 1,
  max = MAX_LEVEL,
  className = "",
}: {
  id?: string;
  value: number;
  onChange: (value: number) => void;
  min?: number;
  max?: number;
  className?: string;
}) {
  return (
    <ThemedNumberInput id={id} value={value} min={min} max={max} onValueChange={onChange} className={className} />
  );
}

function MetricCard({ label, value }: { label: string; value: string }) {
  return (
    <Card>
      <CardContent className="p-3">
        <div className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</div>
        <div className="mt-1 text-lg font-semibold tabular-nums">{value}</div>
      </CardContent>
    </Card>
  );
}

function LevelStepper({
  value,
  onChange,
}: {
  value: number;
  onChange: (value: number) => void;
}) {
  return <NumberInput value={value} onChange={onChange} className="ml-auto w-28" />;
}

function EquipmentSelect({
  label,
  value,
  onChange,
  id,
  equipIcons,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  id: string;
  equipIcons?: Record<string, string>;
}) {
  return <EquipmentSearchSelect
    id={id}
    label={label}
    value={value}
    options={[{ value: "", label: "Choose equipment", iconName: "" }, ...EQUIPMENT_SEARCH_OPTIONS]}
    onChange={onChange}
    equipIcons={equipIcons}
    placeholder="Choose equipment"
  />;
}

function CandidateSummary({ candidate, emptyText }: { candidate: RouteCandidate | null; emptyText: string }) {
  if (!candidate) {
    return <div className="rounded-md border border-border bg-background/50 p-3 text-xs text-muted-foreground">{emptyText}</div>;
  }

  return (
    <div className="rounded-md border border-border bg-background/50 p-3 space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="text-sm font-semibold">{candidate.label}</div>
        <div className="flex items-center gap-1.5">
          <Badge variant="outline">Best Found</Badge>
          <GrandFormulaDialog candidate={candidate} />
        </div>
      </div>
      <div className="grid gap-2 sm:grid-cols-3">
        <MetricCard label="Copper Cost" value={formatNumber(candidate.copperCost)} />
        <MetricCard label="Waste EXP" value={formatNumber(candidate.wasteExp)} />
        <MetricCard label="Total EXP" value={formatNumber(candidate.totalExp)} />
      </div>
      <div className="space-y-1 text-xs text-muted-foreground">
        {candidate.steps.map((step) => (
          <div key={step}>{step}</div>
        ))}
      </div>
    </div>
  );
}

function TwoItemStepCard({
  step,
  index,
  itemAName,
  itemBName,
}: {
  step: TwoItemStep;
  index: number;
  itemAName: string;
  itemBName: string;
}) {
  const itemAIsSource = step.action === "A_TO_B";
  const itemAStartLevel = itemAIsSource ? step.sourceLevel : step.recipientStartLevel;
  const itemAEndLevel = itemAIsSource ? step.sourceLevel : step.recipientEndLevel;
  const itemBStartLevel = itemAIsSource ? step.recipientStartLevel : step.sourceLevel;
  const itemBEndLevel = itemAIsSource ? step.recipientEndLevel : step.sourceLevel;
  const leftLevelText = itemAStartLevel === itemAEndLevel ? `Lv${itemAStartLevel}` : `Lv${itemAStartLevel} to Lv${itemAEndLevel}`;
  const rightLevelText = itemBStartLevel === itemBEndLevel ? `Lv${itemBStartLevel}` : `Lv${itemBStartLevel} to Lv${itemBEndLevel}`;

  return (
    <div className="rounded-md border border-border bg-muted/20 p-2">
      <div className="mb-2 flex items-center gap-2 text-[11px] text-muted-foreground">
        <span className="font-medium text-foreground">Step {index + 1}</span>
        <span>{formatNumber(step.copperCost)} copper</span>
      </div>
      <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-2">
        <div className="min-w-0 rounded-md border border-border bg-background/70 p-2">
          <div className="flex items-center justify-between gap-2">
            <div className="truncate text-xs font-semibold text-foreground">{itemAName}</div>
            {itemAIsSource ? <Badge variant="outline" className="shrink-0 text-[10px]">x{formatNumber(step.quantity)}</Badge> : null}
          </div>
          <div className="text-[11px] text-muted-foreground">{leftLevelText}</div>
        </div>
        <ArrowRight className={`h-4 w-4 ${itemAIsSource ? "text-emerald-400" : "rotate-180 text-amber-400"}`} />
        <div className="min-w-0 rounded-md border border-border bg-background/70 p-2">
          <div className="flex items-center justify-between gap-2">
            <div className="truncate text-xs font-semibold text-foreground">{itemBName}</div>
            {!itemAIsSource ? <Badge variant="outline" className="shrink-0 text-[10px]">x{formatNumber(step.quantity)}</Badge> : null}
          </div>
          <div className="text-[11px] text-muted-foreground">{rightLevelText}</div>
        </div>
      </div>
    </div>
  );
}

function FormulaLine({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-border bg-background/70 p-2">
      <div className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className="mt-1 text-xs font-medium text-foreground">{value}</div>
    </div>
  );
}

const FormulaIconButton = forwardRef<HTMLButtonElement, ButtonProps & { label: string }>(
  ({ label, ...props }, ref) => (
    <Button
      ref={ref}
      type="button"
      variant="ghost"
      size="icon"
      className="h-7 w-7 shrink-0"
      aria-label={label}
      title={label}
      {...props}
    >
      <Sigma className="h-4 w-4" />
    </Button>
  ),
);
FormulaIconButton.displayName = "FormulaIconButton";

function ExpCalculatorFormulaDialog({
  recipient,
  source,
  sourceLevel,
  totalParam,
  weightedExp,
  finalExp,
  stagePlan,
}: {
  recipient: Equipment | undefined;
  source: Equipment | undefined;
  sourceLevel: number;
  totalParam: number;
  weightedExp: number;
  finalExp: number;
  stagePlan: StagePlan;
}) {
  return (
    <Dialog>
      <DialogTrigger asChild>
        <FormulaIconButton label="Show formula for EXP Calculator" />
      </DialogTrigger>
      <DialogContent className="flex max-h-[85vh] max-w-3xl flex-col gap-0 overflow-hidden p-0">
        <DialogHeader className="shrink-0 border-b border-border px-5 py-4 pr-12">
          <DialogTitle className="text-base">EXP Calculator Math</DialogTitle>
          <DialogDescription className="text-xs">
            {source?.name ?? "Source"} Lv{sourceLevel} into {recipient?.name ?? "recipient"}.
          </DialogDescription>
        </DialogHeader>

        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-5 py-4">
          <div className="grid gap-2 sm:grid-cols-2">
            <FormulaLine
              label="Weighted EXP"
              value={`round down(${formatNumber(totalParam)} x ${formatNumber(recipient?.expRate)} x 60 / 10000) = ${formatNumber(weightedExp)}`}
            />
            <FormulaLine
              label="Final EXP each"
              value={`round down((${formatNumber(weightedExp)} + ${formatNumber(source?.mixBonusExp)}) x 1.5) = ${formatNumber(finalExp)}`}
            />
            <FormulaLine
              label="Copies per stage"
              value="round up(stage EXP needed / final EXP each)"
            />
            <FormulaLine
              label="Stage waste"
              value="copies x final EXP each - stage EXP needed"
            />
          </div>

          <div className="rounded-md border border-border bg-muted/20 p-3">
            <div className="mb-2 text-xs font-semibold text-foreground">Totals for this calculation</div>
            <div className="mb-2 text-[11px] text-muted-foreground">
              Round up means any leftover EXP requirement still needs one whole extra item.
            </div>
            <div className="grid gap-2 sm:grid-cols-3">
              <FormulaLine label="Quantity" value={formatNumber(stagePlan.totalQuantity)} />
              <FormulaLine label="Copper" value={formatNumber(stagePlan.totalCopperCost)} />
              <FormulaLine label="Wasted EXP" value={formatNumber(stagePlan.totalWastedExp)} />
            </div>
          </div>

          <div className="space-y-2">
            <div className="text-xs font-semibold text-foreground">Cap stage math</div>
            {stagePlan.stages.length === 0 ? (
              <div className="rounded-md border border-border bg-background/60 p-3 text-xs text-muted-foreground">
                No stage math yet. Choose a target level above the current level and calculate.
              </div>
            ) : (
              stagePlan.stages.map((stage) => {
                const fedExp = stage.quantityNeeded * finalExp;
                return (
                  <div key={`exp-formula-${stage.stage}`} className="rounded-md border border-border bg-background/60 p-3">
                    <div className="mb-2 text-xs font-semibold text-foreground">{stage.stage}</div>
                    <div className="grid gap-2 sm:grid-cols-2">
                      <FormulaLine label="Needed EXP" value={formatNumber(stage.expNeeded)} />
                      <FormulaLine
                        label="Quantity"
                        value={`round up(${formatNumber(stage.expNeeded)} / ${formatNumber(finalExp)}) = ${formatNumber(stage.quantityNeeded)}`}
                      />
                      <FormulaLine
                        label="Fed EXP"
                        value={`${formatNumber(stage.quantityNeeded)} x ${formatNumber(finalExp)} = ${formatNumber(fedExp)}`}
                      />
                      <FormulaLine
                        label="Waste"
                        value={`${formatNumber(fedExp)} - ${formatNumber(stage.expNeeded)} = ${formatNumber(stage.wastedExp)}`}
                      />
                      <FormulaLine label="Useful EXP" value={formatNumber(stage.usefulExp)} />
                      <FormulaLine label="Copper" value={formatNumber(stage.copperCost)} />
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>

        <div className="shrink-0 border-t border-border px-5 py-3">
          <DialogClose asChild>
            <Button type="button" variant="outline" size="sm">
              Close
            </Button>
          </DialogClose>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function GrandFormulaDialog({ candidate }: { candidate: RouteCandidate }) {
  if (!candidate.formulaSteps?.length) return null;

  return (
    <Dialog>
      <DialogTrigger asChild>
        <FormulaIconButton label="Show formula for Grand Optimizer result" />
      </DialogTrigger>
      <DialogContent className="flex max-h-[85vh] max-w-3xl flex-col gap-0 overflow-hidden p-0">
        <DialogHeader className="shrink-0 border-b border-border px-5 py-4 pr-12">
          <DialogTitle className="text-base">Grand Optimizer Math</DialogTitle>
          <DialogDescription className="text-xs">
            {candidate.label}. This uses the cheapest selected source for each cap stage.
          </DialogDescription>
        </DialogHeader>

        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-5 py-4">
          <div className="grid gap-2 sm:grid-cols-2">
            <FormulaLine label="Source choice" value="lowest copper, then lowest waste, then highest EXP per item" />
            <FormulaLine label="Quantity" value="round up(cap stage EXP needed / selected source EXP each)" />
            <FormulaLine label="Copper" value="quantity x selected source copper cost" />
            <FormulaLine label="Wasted EXP" value="quantity x EXP each - cap stage EXP needed" />
          </div>

          <div className="rounded-md border border-border bg-muted/20 p-3">
            <div className="mb-2 text-xs font-semibold text-foreground">Totals for this route</div>
            <div className="mb-2 text-[11px] text-muted-foreground">
              Round up means any leftover EXP requirement still needs one whole extra item.
            </div>
            <div className="grid gap-2 sm:grid-cols-3">
              <FormulaLine label="Fed EXP" value={formatNumber(candidate.totalExp)} />
              <FormulaLine label="Copper" value={formatNumber(candidate.copperCost)} />
              <FormulaLine label="Wasted EXP" value={formatNumber(candidate.wasteExp)} />
            </div>
          </div>

          <div className="space-y-2">
            <div className="text-xs font-semibold text-foreground">Cap stage math</div>
            {candidate.formulaSteps.map((step, index) => (
              <div key={`grand-formula-${step.stage}-${index}`} className="rounded-md border border-border bg-background/60 p-3">
                <div className="mb-2 flex flex-wrap items-center justify-between gap-2 text-xs">
                  <div className="font-semibold text-foreground">{step.stage}</div>
                  <Badge variant="outline">
                    {step.sourceName} Lv{step.sourceLevel}
                  </Badge>
                </div>
                <div className="grid gap-2 sm:grid-cols-2">
                  <FormulaLine label="Needed EXP" value={formatNumber(step.neededExp)} />
                  <FormulaLine label="EXP each" value={formatNumber(step.expPerSource)} />
                  <FormulaLine
                    label="Quantity"
                    value={`round up(${formatNumber(step.neededExp)} / ${formatNumber(step.expPerSource)}) = ${formatNumber(step.quantity)}`}
                  />
                  <FormulaLine
                    label="Fed EXP"
                    value={`${formatNumber(step.quantity)} x ${formatNumber(step.expPerSource)} = ${formatNumber(step.fedExp)}`}
                  />
                  <FormulaLine label="Wasted EXP" value={formatNumber(step.wastedExp)} />
                  <FormulaLine
                    label="Copper"
                    value={
                      step.copperCost == null
                        ? "N/A"
                        : `${formatNumber(step.quantity)} x ${formatNumber(step.copperEach)} = ${formatNumber(step.copperCost)}`
                    }
                  />
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="shrink-0 border-t border-border px-5 py-3">
          <DialogClose asChild>
            <Button type="button" variant="outline" size="sm">
              Close
            </Button>
          </DialogClose>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function TwoItemFormulaDialog({
  slot,
  itemA,
  itemB,
  itemATargetLevel,
  itemBTargetLevel,
}: {
  slot: TwoItemOptionSlot;
  itemA: Equipment | undefined;
  itemB: Equipment | undefined;
  itemATargetLevel: number;
  itemBTargetLevel: number;
}) {
  const result = slot.result;
  if (!result?.found) return null;

  const compactSteps = compactTwoItemSteps(result.steps);
  const itemAName = itemA?.name ?? "Item A";
  const itemBName = itemB?.name ?? "Item B";

  return (
    <Dialog>
      <DialogTrigger asChild>
        <FormulaIconButton label={`Show formula for ${slot.title}`} />
      </DialogTrigger>
      <DialogContent className="flex max-h-[85vh] max-w-3xl flex-col gap-0 overflow-hidden p-0">
        <DialogHeader className="shrink-0 border-b border-border px-5 py-4 pr-12">
          <DialogTitle className="text-base">{slot.title} Math</DialogTitle>
          <DialogDescription className="text-xs">
            {result.label}. This window stays fixed while the math details scroll.
          </DialogDescription>
        </DialogHeader>

        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-5 py-4">
          <div className="grid gap-2 sm:grid-cols-2">
            <FormulaLine
              label="EXP per sacrificed item"
              value="round down((round down(source stat total x recipient expRate x 60 / 10000) + source mixBonusExp) x 1.5)"
            />
            <FormulaLine
              label="Copies per batch"
              value="at least 1, then round up(EXP needed for next useful level / EXP per sacrificed item)"
            />
            <FormulaLine
              label="Copper"
              value="sum(batch quantity x sacrificed item's copper cost)"
            />
            <FormulaLine
              label="Wasted EXP"
              value="sum(max(0, fed EXP - useful EXP before the current cap))"
            />
          </div>

          <div className="rounded-md border border-border bg-muted/20 p-3">
            <div className="mb-2 text-xs font-semibold text-foreground">Totals for this option</div>
            <div className="mb-2 text-[11px] text-muted-foreground">
              Round up means any leftover EXP requirement still needs one whole extra item.
            </div>
            <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
              <FormulaLine label="Copper" value={formatNumber(result.totalCopperCost)} />
              <FormulaLine label="Wasted EXP" value={formatNumber(result.totalWastedExp)} />
              <FormulaLine label={`${itemAName} used`} value={formatNumber(result.itemASacrifices)} />
              <FormulaLine label={`${itemBName} used`} value={formatNumber(result.itemBSacrifices)} />
            </div>
          </div>

          <div className="space-y-2">
            <div className="text-xs font-semibold text-foreground">Step math</div>
            {compactSteps.map((step, index) => {
              const source = step.action === "A_TO_B" ? itemA : itemB;
              const recipient = step.action === "A_TO_B" ? itemB : itemA;
              const recipientTargetLevel = step.action === "A_TO_B" ? itemBTargetLevel : itemATargetLevel;
              const expPerSource = expFromSource(recipient, source, step.sourceLevel).finalExp;
              const fedExp = step.quantity * expPerSource;
              const copperEach = source?.buyPrice ?? 0;
              const stageStart = currentCapStart(step.recipientStartLevel);
              const stageEnd = nextStageEnd(step.recipientStartLevel, recipientTargetLevel);
              const stageTotal = stageExpNeeded(stageStart, stageEnd);
              const startingProgress = capProgressForLevel(step.recipientStartLevel);
              const nextUsefulLevel = Math.min(stageEnd, step.recipientStartLevel + 1);
              const nextLevelProgress = stageExpNeeded(stageStart, nextUsefulLevel);
              const neededToNextUsefulLevel = Math.max(0, nextLevelProgress - startingProgress);
              const neededToStageEnd = Math.max(0, stageTotal - startingProgress);
              return (
                <div
                  key={`${slot.title}-formula-${step.action}-${step.sourceLevel}-${step.recipientStartLevel}-${index}`}
                  className="rounded-md border border-border bg-background/60 p-3"
                >
                  <div className="mb-2 flex flex-wrap items-center justify-between gap-2 text-xs">
                    <div className="font-semibold text-foreground">Step {index + 1}</div>
                    <Badge variant="outline">
                      {step.sourceName} Lv{step.sourceLevel} into {step.recipientName}
                    </Badge>
                  </div>
                  <div className="grid gap-2 sm:grid-cols-2">
                    <FormulaLine
                      label="Displayed progress"
                      value={`Recipient moved from Lv${step.recipientStartLevel} to Lv${step.recipientEndLevel}`}
                    />
                    <FormulaLine
                      label="Current level cap"
                      value={`Lv${stageEnd} cap, ${formatNumber(stageTotal)} EXP from Lv${stageStart} to Lv${stageEnd}`}
                    />
                    <FormulaLine
                      label="Needed from step start"
                      value={`${formatNumber(neededToNextUsefulLevel)} to next level, ${formatNumber(neededToStageEnd)} to Lv${stageEnd} cap`}
                    />
                    <FormulaLine
                      label="EXP each"
                      value={`${formatNumber(expPerSource)} from ${step.sourceName} Lv${step.sourceLevel}`}
                    />
                    <FormulaLine
                      label="Fed EXP"
                      value={`${formatNumber(step.quantity)} x ${formatNumber(expPerSource)} = ${formatNumber(fedExp)}`}
                    />
                    <FormulaLine
                      label="Useful / wasted"
                      value={`${formatNumber(step.usefulExp)} useful, ${formatNumber(step.wastedExp)} wasted`}
                    />
                    <FormulaLine
                      label="Copper"
                      value={`${formatNumber(step.quantity)} x ${formatNumber(copperEach)} = ${formatNumber(step.copperCost)}`}
                    />
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        <div className="shrink-0 border-t border-border px-5 py-3">
          <DialogClose asChild>
            <Button type="button" variant="outline" size="sm">
              Close
            </Button>
          </DialogClose>
        </div>
      </DialogContent>
    </Dialog>
  );
}

export default function EquipmentLevelingOptimizerPage() {
  const { data: sharedData } = useQuery({
    queryKey: ["ka-shared"],
    queryFn: () => fetchSharedWithFallback<SharedData>(apiUrl("/shared")),
    staleTime: 15000,
  });
  const equipIcons = sharedData?.equipIcons ?? {};
  const firstItem = EQUIPMENT[0]?.id ? String(EQUIPMENT[0].id) : "";
  const secondItem = EQUIPMENT[1]?.id ? String(EQUIPMENT[1].id) : "";
  const optimizerStored = useMemo(() => readOptimizerStorage(), []);
  const grandStored = useMemo(() => optimizerStored?.grand ?? readGrandStorage(), [optimizerStored]);
  const [recipientId, setRecipientId] = useState(optimizerStored?.recipientId ?? firstItem);
  const [recipientCurrentLevel, setRecipientCurrentLevel] = useState(clampLevel(optimizerStored?.recipientCurrentLevel ?? 1));
  const [recipientTargetLevel, setRecipientTargetLevel] = useState(clampLevel(optimizerStored?.recipientTargetLevel ?? MAX_LEVEL));
  const [sourceId, setSourceId] = useState(optimizerStored?.sourceId ?? secondItem);
  const [sourceLevel, setSourceLevel] = useState(clampLevel(optimizerStored?.sourceLevel ?? 1));
  const [calcSnapshot, setCalcSnapshot] = useState<CalcSnapshot | null>(null);

  const [itemAId, setItemAId] = useState(optimizerStored?.itemAId ?? secondItem);
  const [itemBId, setItemBId] = useState(optimizerStored?.itemBId ?? (EQUIPMENT[2]?.id ? String(EQUIPMENT[2].id) : secondItem));
  const [itemALevel, setItemALevel] = useState(clampLevel(optimizerStored?.itemALevel ?? 1));
  const [itemATargetLevel, setItemATargetLevel] = useState(clampLevel(optimizerStored?.itemATargetLevel ?? MAX_LEVEL));
  const [itemBLevel, setItemBLevel] = useState(clampLevel(optimizerStored?.itemBLevel ?? 1));
  const [itemBTargetLevel, setItemBTargetLevel] = useState(clampLevel(optimizerStored?.itemBTargetLevel ?? MAX_LEVEL));
  const [twoCalculated, setTwoCalculated] = useState(false);

  const [grandRecipientId, setGrandRecipientId] = useState(grandStored?.recipientId ?? firstItem);
  const [grandCurrentLevel, setGrandCurrentLevel] = useState(clampLevel(grandStored?.currentLevel ?? 1));
  const [grandTargetLevel, setGrandTargetLevel] = useState(clampLevel(grandStored?.targetLevel ?? MAX_LEVEL));
  const [grandQuery, setGrandQuery] = useState("");
  const [grandRows, setGrandRows] = useState<Record<string, GrandRow>>(() => buildGrandRowsFromStorage(grandStored?.rows));
  const [grandCalculated, setGrandCalculated] = useState(false);

  useEffect(() => {
    if (typeof window === "undefined") return;
    window.localStorage.setItem(
      OPTIMIZER_STORAGE_KEY,
      JSON.stringify({
        recipientId,
        recipientCurrentLevel,
        recipientTargetLevel,
        sourceId,
        sourceLevel,
        itemAId,
        itemALevel,
        itemATargetLevel,
        itemBId,
        itemBLevel,
        itemBTargetLevel,
        grand: {
          recipientId: grandRecipientId,
          currentLevel: grandCurrentLevel,
          targetLevel: grandTargetLevel,
          rows: grandRows,
        },
      }),
    );
    window.localStorage.setItem(
      GRAND_STORAGE_KEY,
      JSON.stringify({
        recipientId: grandRecipientId,
        currentLevel: grandCurrentLevel,
        targetLevel: grandTargetLevel,
        rows: grandRows,
      }),
    );
  }, [
    grandCurrentLevel,
    grandRecipientId,
    grandRows,
    grandTargetLevel,
    itemAId,
    itemALevel,
    itemATargetLevel,
    itemBId,
    itemBLevel,
    itemBTargetLevel,
    recipientCurrentLevel,
    recipientId,
    recipientTargetLevel,
    sourceId,
    sourceLevel,
  ]);

  const calcData = useMemo(() => {
    const snap = calcSnapshot ?? { recipientId, recipientCurrentLevel, recipientTargetLevel, sourceId, sourceLevel };
    const recipient = EQUIPMENT_BY_ID.get(snap.recipientId);
    const source = EQUIPMENT_BY_ID.get(snap.sourceId);
    const sourceExp = expFromSource(recipient, source, snap.sourceLevel);
    const stagePlan = buildStagePlan(
      snap.recipientCurrentLevel,
      snap.recipientTargetLevel,
      sourceExp.finalExp,
      source?.buyPrice,
    );
    return {
      recipient,
      source,
      sourceLevel: snap.sourceLevel,
      ...sourceExp,
      stagePlan,
    };
  }, [calcSnapshot, recipientCurrentLevel, recipientId, recipientTargetLevel, sourceId, sourceLevel]);

  const twoItemResult = useMemo(() => {
    if (!twoCalculated) return null;
    const itemA = EQUIPMENT_BY_ID.get(itemAId);
    const itemB = EQUIPMENT_BY_ID.get(itemBId);
    return findTwoItemRoutes({
      itemA,
      itemB,
      itemAStartLevel: itemALevel,
      itemATargetLevel,
      itemBStartLevel: itemBLevel,
      itemBTargetLevel,
    });
  }, [itemAId, itemALevel, itemATargetLevel, itemBId, itemBLevel, itemBTargetLevel, twoCalculated]);

  const twoItemDisplayOptions = useMemo(() => {
    if (!twoItemResult) return [];
    return [
      {
        title: "Option 1: Cheapest",
        result: twoItemResult.cheapest,
        missingText: "No cheapest route could be calculated.",
      },
      {
        title: "Option 2: Less Steps, Higher Cost",
        result: twoItemResult.fewerSacrifices,
        missingText: "No calculated route had an intermediate step count between Option 1 and Option 3.",
      },
      {
        title: "Option 3: Even Less Steps, Higher Cost",
        result: twoItemResult.balanced,
        missingText: "No calculated route had fewer displayed steps than Option 1.",
      },
    ] satisfies TwoItemOptionSlot[];
  }, [twoItemResult]);

  const selectedGrandRows = useMemo(() => {
    return Object.entries(grandRows)
      .filter(([, row]) => row.checked)
      .map(([id, row]) => ({ item: EQUIPMENT_BY_ID.get(id), row }))
      .filter((entry): entry is { item: Equipment; row: GrandRow } => Boolean(entry.item));
  }, [grandRows]);

  const grandResult = useMemo(() => {
    if (!grandCalculated) return null;
    const recipient = EQUIPMENT_BY_ID.get(grandRecipientId);
    if (!recipient || grandCurrentLevel >= grandTargetLevel || selectedGrandRows.length === 0) return null;

    let level = clampLevel(grandCurrentLevel);
    let totalExp = 0;
    let copperCost: number | null = 0;
    let wasteExp = 0;
    const steps: string[] = [];
    const formulaSteps: GrandFormulaStep[] = [];

    while (level < grandTargetLevel) {
      const stageEnd = nextStageEnd(level, grandTargetLevel);
      const needed = stageExpNeeded(level, stageEnd);
      const bestSource = selectedGrandRows
        .map(({ item, row }) => {
          const expPerSource = expFromSource(recipient, item, row.level).finalExp;
          const unitCost = item.buyPrice;
          const quantity = expPerSource > 0 ? Math.ceil(needed / expPerSource) : 0;
          const sourceCopper = unitCost == null ? null : quantity * unitCost;
          return {
            item,
            row,
            expPerSource,
            quantity,
            sourceCopper,
            sourceWaste: Math.max(0, quantity * expPerSource - needed),
          };
        })
        .filter((source) => source.expPerSource > 0 && source.quantity > 0)
        .sort((a, b) => {
          if (a.sourceCopper == null && b.sourceCopper != null) return 1;
          if (a.sourceCopper != null && b.sourceCopper == null) return -1;
          return (a.sourceCopper ?? 0) - (b.sourceCopper ?? 0) || a.sourceWaste - b.sourceWaste || b.expPerSource - a.expPerSource;
        })[0];

      if (!bestSource) break;

      const fedExp = bestSource.quantity * bestSource.expPerSource;
      totalExp += fedExp;
      wasteExp += bestSource.sourceWaste;
      if (bestSource.sourceCopper == null || copperCost == null) copperCost = null;
      else copperCost += bestSource.sourceCopper;
      steps.push(
        `Lv${level} to Lv${stageEnd}: use ${bestSource.quantity} x ${bestSource.item.name} Lv${bestSource.row.level}.`,
      );
      formulaSteps.push({
        stage: `Lv${level} to Lv${stageEnd}`,
        startLevel: level,
        endLevel: stageEnd,
        sourceName: bestSource.item.name,
        sourceLevel: bestSource.row.level,
        expPerSource: bestSource.expPerSource,
        quantity: bestSource.quantity,
        fedExp,
        neededExp: needed,
        wastedExp: bestSource.sourceWaste,
        copperEach: bestSource.item.buyPrice,
        copperCost: bestSource.sourceCopper,
      });
      level = stageEnd;
    }

    return {
      label: level < grandTargetLevel ? "Partial Selected-Item Route" : "Best Found Selected-Item Route",
      expPerSource: 0,
      quantity: selectedGrandRows.length,
      totalExp,
      wasteExp,
      copperCost,
      steps,
      formulaSteps,
    };
  }, [grandCalculated, grandCurrentLevel, grandRecipientId, grandTargetLevel, selectedGrandRows]);

  const filteredGrandEquipment = useMemo(() => {
    const query = grandQuery.trim();
    if (!query) return EQUIPMENT.slice(0, 80);
    return EQUIPMENT.filter((item) => matchesLooseSearch(item.name, query)).slice(0, 120);
  }, [grandQuery]);

  const selectedCount = Object.values(grandRows).filter((row) => row.checked).length;
  const availableSources = selectedCount;

  const updateGrandRow = (id: string, patch: Partial<GrandRow>) => {
    setGrandRows((prev) => {
      const current = prev[id] ?? DEFAULT_GRAND_ROWS[id] ?? { checked: true, level: 1 };
      return { ...prev, [id]: { ...current, ...patch } };
    });
  };

  const setAllGrandRowsChecked = (checked: boolean) => {
    setGrandRows((prev) =>
      Object.fromEntries(
        EQUIPMENT.map((item) => {
          const id = String(item.id);
          return [id, { ...(prev[id] ?? DEFAULT_GRAND_ROWS[id]), checked }];
        }),
      ) as Record<string, GrandRow>,
    );
  };

  const toggleGrandRank = (rankLabel: string) => {
    setGrandRows((prev) =>
      Object.fromEntries(
        EQUIPMENT.map((item) => {
          const id = String(item.id);
          const current = prev[id] ?? DEFAULT_GRAND_ROWS[id];
          return [id, { ...current, checked: item.rankLabel === rankLabel ? !current.checked : current.checked }];
        }),
      ) as Record<string, GrandRow>,
    );
  };

  return (
    <div className="max-w-6xl mx-auto px-4 py-6 space-y-6">
      <div className="space-y-1">
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="text-xl font-bold tracking-tight">Equipment Leveling Optimizer</h1>
        </div>
        <p className="text-sm text-muted-foreground max-w-3xl">
          Find efficient Kingdom Adventurers equipment leveling routes with low EXP waste, low copper cost,
          sacrifice item planning, cap-stage math, and Grand Prize exchange inputs.
        </p>
      </div>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="flex items-center gap-2 text-base">
            <Calculator className="h-4 w-4" />
            EXP Calculator
          </CardTitle>
          <CardDescription className="text-xs">
            Uses Equip.csv stats, expRate, mixBonusExp, and Exp.csv level requirements.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-3 md:grid-cols-3">
            <EquipmentSelect id="recipient" label="EXP Recipient" value={recipientId} onChange={setRecipientId} equipIcons={equipIcons} />
            <div className="space-y-1.5">
              <Label htmlFor="recipient-current" className="text-xs">Recipient Current Level</Label>
              <NumberInput id="recipient-current" value={recipientCurrentLevel} onChange={setRecipientCurrentLevel} />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="recipient-target" className="text-xs">Recipient Target Level</Label>
              <NumberInput id="recipient-target" value={recipientTargetLevel} onChange={setRecipientTargetLevel} />
            </div>
            <EquipmentSelect id="source" label="EXP Source" value={sourceId} onChange={setSourceId} equipIcons={equipIcons} />
            <div className="space-y-1.5">
              <Label htmlFor="source-level" className="text-xs">Source Level</Label>
              <NumberInput id="source-level" value={sourceLevel} onChange={setSourceLevel} />
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <Button
              size="sm"
              onClick={() =>
                setCalcSnapshot({ recipientId, recipientCurrentLevel, recipientTargetLevel, sourceId, sourceLevel })
              }
            >
              Calculate
            </Button>
            <div className="text-xs text-muted-foreground">
              {calcData.source && calcData.recipient ? "Open the formula button to see the EXP math." : "Choose a recipient and source item."}
            </div>
            <ExpCalculatorFormulaDialog
              recipient={calcData.recipient}
              source={calcData.source}
              sourceLevel={calcData.sourceLevel}
              totalParam={calcData.totalParam}
              weightedExp={calcData.weightedExp}
              finalExp={calcData.finalExp}
              stagePlan={calcData.stagePlan}
            />
          </div>

          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
            <MetricCard label="EXP Per Source" value={formatNumber(calcData.finalExp)} />
            <MetricCard label="Source Stat Total" value={formatNumber(calcData.totalParam)} />
            <MetricCard label="Source mixBonusExp" value={formatNumber(calcData.source?.mixBonusExp)} />
            <MetricCard label="Recipient expRate" value={formatNumber(calcData.recipient?.expRate)} />
            <MetricCard label="Source Copper Cost" value={formatNumber(calcData.source?.buyPrice)} />
          </div>

          <div className="grid gap-3 sm:grid-cols-3">
            <MetricCard label="Total Quantity Needed" value={formatNumber(calcData.stagePlan.totalQuantity)} />
            <MetricCard label="Total Copper Cost" value={formatNumber(calcData.stagePlan.totalCopperCost)} />
            <MetricCard label="Total Wasted EXP" value={formatNumber(calcData.stagePlan.totalWastedExp)} />
          </div>

          <div className="overflow-x-auto rounded-md border border-border">
            <table className="w-full min-w-[680px] border-collapse text-xs">
              <thead className="bg-muted/40 text-muted-foreground">
                <tr>
                  <th className="px-3 py-2 text-left font-medium">Stage</th>
                  <th className="px-3 py-2 text-right font-medium">EXP needed</th>
                  <th className="px-3 py-2 text-right font-medium">Quantity needed</th>
                  <th className="px-3 py-2 text-right font-medium">Useful EXP</th>
                  <th className="px-3 py-2 text-right font-medium">Wasted EXP</th>
                  <th className="px-3 py-2 text-right font-medium">Copper Cost</th>
                </tr>
              </thead>
              <tbody>
                {calcData.stagePlan.stages.length === 0 ? (
                  <tr className="border-t border-border">
                    <td className="px-3 py-3 text-muted-foreground" colSpan={6}>
                      Choose a target level above the current level and calculate.
                    </td>
                  </tr>
                ) : (
                  calcData.stagePlan.stages.map((stage) => (
                    <tr key={stage.stage} className="border-t border-border">
                      <td className="px-3 py-2 font-medium">{stage.stage}</td>
                      <td className="px-3 py-2 text-right tabular-nums">{formatNumber(stage.expNeeded)}</td>
                      <td className="px-3 py-2 text-right tabular-nums">{formatNumber(stage.quantityNeeded)}</td>
                      <td className="px-3 py-2 text-right tabular-nums">{formatNumber(stage.usefulExp)}</td>
                      <td className="px-3 py-2 text-right tabular-nums">{formatNumber(stage.wastedExp)}</td>
                      <td className="px-3 py-2 text-right tabular-nums">{formatNumber(stage.copperCost)}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="flex items-center gap-2 text-base">
            <Route className="h-4 w-4" />
            Two-Item Optimizer
          </CardTitle>
          <CardDescription className="text-xs">
            ONLY Item A and Item B are used in calculations. Levels are global per equipment type.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-4 md:grid-cols-2">
            <div className="rounded-md border border-border bg-background/50 p-3 space-y-3">
              <EquipmentSelect
                id="item-a"
                label="Item A"
                value={itemAId}
                onChange={(value) => {
                  setItemAId(value);
                  setTwoCalculated(false);
                }}
                equipIcons={equipIcons}
              />
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="space-y-1.5">
                  <Label className="text-xs">Current Level</Label>
                  <NumberInput value={itemALevel} onChange={setItemALevel} />
                </div>
                <div className="space-y-1.5">
                  <Label className="text-xs">Target Level</Label>
                  <NumberInput value={itemATargetLevel} onChange={setItemATargetLevel} />
                </div>
              </div>
              <div className="text-xs text-muted-foreground">Copper cost: {formatNumber(EQUIPMENT_BY_ID.get(itemAId)?.buyPrice)}</div>
            </div>
            <div className="rounded-md border border-border bg-background/50 p-3 space-y-3">
              <EquipmentSelect
                id="item-b"
                label="Item B"
                value={itemBId}
                onChange={(value) => {
                  setItemBId(value);
                  setTwoCalculated(false);
                }}
                equipIcons={equipIcons}
              />
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="space-y-1.5">
                  <Label className="text-xs">Current Level</Label>
                  <NumberInput value={itemBLevel} onChange={setItemBLevel} />
                </div>
                <div className="space-y-1.5">
                  <Label className="text-xs">Target Level</Label>
                  <NumberInput value={itemBTargetLevel} onChange={setItemBTargetLevel} />
                </div>
              </div>
              <div className="text-xs text-muted-foreground">Copper cost: {formatNumber(EQUIPMENT_BY_ID.get(itemBId)?.buyPrice)}</div>
            </div>
          </div>
          <Button size="sm" onClick={() => setTwoCalculated(true)}>Calculate Two-Item Route</Button>
          {!twoItemResult ? (
            <div className="rounded-md border border-border bg-background/50 p-3 text-xs text-muted-foreground">
              Choose two equipment types and calculate. The search tries A into B and B into A in copper-cost order.
            </div>
          ) : twoItemDisplayOptions.some((option) => option.result?.found) ? (
            <div className="grid gap-3 lg:grid-cols-3">
              {twoItemDisplayOptions.map((slot) => (
                slot.result?.found ? (
                  <div key={slot.title} className="rounded-md border border-border bg-background/50 p-3 space-y-3">
                    <div className="space-y-1">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <div className="text-sm font-semibold">{slot.title}</div>
                        <div className="flex items-center gap-1.5">
                          <Badge variant="outline">{slot.result.label}</Badge>
                          <TwoItemFormulaDialog
                            slot={slot}
                            itemA={EQUIPMENT_BY_ID.get(itemAId)}
                            itemB={EQUIPMENT_BY_ID.get(itemBId)}
                            itemATargetLevel={itemATargetLevel}
                            itemBTargetLevel={itemBTargetLevel}
                          />
                        </div>
                      </div>
                      <div className="text-[11px] text-muted-foreground">
                        {formatNumber(displayedStepCount(slot.result))} steps Â· {formatNumber(totalSacrifices(slot.result))} total sacrifices
                      </div>
                    </div>
                    <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-1 xl:grid-cols-2">
                      <MetricCard label="Copper" value={formatNumber(slot.result.totalCopperCost)} />
                      <MetricCard label="Wasted EXP" value={formatNumber(slot.result.totalWastedExp)} />
                      <MetricCard label="Item A Used" value={formatNumber(slot.result.itemASacrifices)} />
                      <MetricCard label="Item B Used" value={formatNumber(slot.result.itemBSacrifices)} />
                    </div>
                    <div className="max-h-80 space-y-2 overflow-y-auto pr-1">
                      {compactTwoItemSteps(slot.result.steps).map((step, index) => (
                        <TwoItemStepCard
                          key={`${slot.title}-${step.action}-${step.sourceName}-${step.sourceLevel}-${step.recipientName}-${step.recipientStartLevel}-${index}`}
                          step={step}
                          index={index}
                          itemAName={EQUIPMENT_BY_ID.get(itemAId)?.name ?? "Item A"}
                          itemBName={EQUIPMENT_BY_ID.get(itemBId)?.name ?? "Item B"}
                        />
                      ))}
                    </div>
                  </div>
                ) : (
                <div key={slot.title} className="rounded-md border border-dashed border-border bg-background/30 p-3 space-y-3">
                  <div className="space-y-1">
                    <div className="text-sm font-semibold">{slot.title}</div>
                    <Badge variant="outline">No route found</Badge>
                  </div>
                  <div className="rounded-md border border-border bg-background/50 p-3 text-xs text-muted-foreground">
                    {slot.missingText}
                  </div>
                </div>
                )
              ))}
            </div>
          ) : (
            <div className="rounded-md border border-border bg-background/50 p-3 text-xs text-muted-foreground">
              No complete two-item route could be calculated with the selected items and targets.
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="flex items-center gap-2 text-base">
            <TableProperties className="h-4 w-4" />
            Grand Optimizer
          </CardTitle>
          <CardDescription className="text-xs">
            ONLY selected equipment is used to calculate the best route.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-3 md:grid-cols-3">
            <EquipmentSelect id="grand-recipient" label="Final Recipient Item" value={grandRecipientId} onChange={setGrandRecipientId} />
            <div className="space-y-1.5">
              <Label className="text-xs">Current Level</Label>
              <NumberInput value={grandCurrentLevel} onChange={setGrandCurrentLevel} />
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs">Target Level</Label>
              <NumberInput value={grandTargetLevel} onChange={setGrandTargetLevel} />
            </div>
          </div>

          <div className="grid gap-3 sm:grid-cols-3">
            <MetricCard label="Selected Items Count" value={formatNumber(selectedCount)} />
            <MetricCard label="Available Source Types" value={formatNumber(availableSources)} />
            <Card>
              <CardContent className="flex items-center gap-2 p-3">
                <CheckCircle2 className="h-4 w-4 text-green-500" />
                <div>
                  <div className="text-[11px] uppercase tracking-wide text-muted-foreground">Ready for Optimization</div>
                  <div className="mt-1 text-sm font-semibold">{selectedCount > 0 ? "Ready" : "Select sources"}</div>
                </div>
              </CardContent>
            </Card>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <Button size="sm" variant="outline" onClick={() => setAllGrandRowsChecked(true)}>Select All</Button>
            <Button size="sm" variant="outline" onClick={() => setAllGrandRowsChecked(false)}>Deselect All</Button>
            {GRAND_RANK_OPTIONS.map((rank) => (
              <Button key={rank} size="sm" variant="outline" onClick={() => toggleGrandRank(rank)}>
                Rank {rank}
              </Button>
            ))}
          </div>

          <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <Input
              value={grandQuery}
              onChange={(event) => setGrandQuery(event.target.value)}
              placeholder="Filter equipment table"
              className="h-9 sm:max-w-xs"
            />
            <Button size="sm" onClick={() => setGrandCalculated(true)}>Calculate Selected Route</Button>
          </div>

          <CandidateSummary candidate={grandResult} emptyText="Select source item types, then calculate. The current solver uses a conservative cap-stage pass over selected equipment only." />

          <div className="overflow-x-auto rounded-md border border-border">
            <table className="w-full min-w-[640px] border-collapse text-xs">
              <thead className="bg-muted/40 text-muted-foreground">
                <tr>
                  <th className="px-3 py-2 text-left font-medium">Use</th>
                  <th className="px-3 py-2 text-left font-medium">Item Name</th>
                  <th className="px-3 py-2 text-left font-medium">Rank</th>
                  <th className="px-3 py-2 text-right font-medium">Current Level</th>
                  <th className="px-3 py-2 text-right font-medium">Copper Each</th>
                </tr>
              </thead>
              <tbody>
                {filteredGrandEquipment.map((item) => {
                  const row = grandRows[String(item.id)] ?? DEFAULT_GRAND_ROWS[String(item.id)] ?? { checked: true, level: 1 };
                  return (
                    <tr key={item.id} className="border-t border-border">
                      <td className="px-3 py-2">
                        <input
                          type="checkbox"
                          className="h-4 w-4 accent-primary"
                          checked={row.checked}
                          onChange={(event) => updateGrandRow(String(item.id), { checked: event.target.checked })}
                        />
                      </td>
                      <td className="px-3 py-2 font-medium">
                        <div className="flex items-center gap-2">
                          {getEquipmentIcon(equipIcons, item.name) ? (
                            <img src={getEquipmentIcon(equipIcons, item.name)} alt="" className="h-5 w-5 rounded object-contain" />
                          ) : (
                            <span className="h-5 w-5" />
                          )}
                          <span>{item.name}</span>
                        </div>
                      </td>
                      <td className="px-3 py-2 text-muted-foreground">{item.rankLabel}</td>
                      <td className="px-3 py-2">
                        <LevelStepper
                          value={row.level}
                          onChange={(level) => updateGrandRow(String(item.id), { level })}
                        />
                      </td>
                      <td className="px-3 py-2 text-right tabular-nums text-muted-foreground">{formatNumber(item.buyPrice)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

        </CardContent>
      </Card>
    </div>
  );
}

